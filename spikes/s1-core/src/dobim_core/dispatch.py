# SPDX-License-Identifier: MPL-2.0
"""프로토콜 계층이 공유하는 실행 경로: 검증 → 위험 등급 검사 → 실행 → 결과 봉투.

CLI 와 MCP 서버는 이 모듈만 호출한다. 두 계층 사이에 분기가 없으므로 같은 입력은 같은 봉투를 낸다
(패리티 C). 클라이언트(Claude·Codex)별 분기는 두지 않는다.
"""
from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dobim_core import registry
from dobim_core.errors import DobimError, internal_error
from dobim_core.paths import resolve_in_workspace

CONFIRM_TTL_S = 300
SCHEMA_HINT = "커맨드 스키마는 dobim schema <name> 또는 dobim_command_describe 로 확인하세요"


class Env:
    """프로토콜 계층 하나(프로세스 하나)의 실행 환경. 엔진은 프로젝트 파일마다 하나씩 캐시한다."""

    def __init__(self, workspace: str | Path | None = None, actor: str = "cli", allow_code: bool = False):
        self.workspace = Path(workspace or Path.cwd()).resolve()
        self.actor = actor
        self.allow_code = allow_code
        self._engines: dict[str, Any] = {}
        self._handles: dict[str, str] = {}

    def engine(self, project: str | None):
        from dobim_core.engine import Engine
        if not project:
            raise DobimError("NO_PROJECT", "프로젝트를 지정해야 한다", path="/project",
                             hint="CLI 는 -p, MCP 는 project 인자(경로 또는 핸들)")
        project = self._handles.get(project, project)
        p = resolve_in_workspace(self.workspace, project, must_exist=True)
        key = str(p)
        eng = self._engines.get(key)
        if eng is None:
            try:
                eng = Engine(p, self.actor)
            except (DobimError, KeyboardInterrupt):
                raise
            except Exception as ex:   # 깨진 파일·잠금 등: 봉투 없는 예외로 새지 않게 한다
                raise internal_error(ex) from ex
            self._engines[key] = eng
        eng.workspace = self.workspace
        eng.actor = self.actor
        return eng

    def adopt(self, path: str | Path, engine) -> None:
        """이미 열린 엔진을 이 환경에 등록한다(시험 도구용)."""
        self._engines[str(Path(path).resolve())] = engine

    def new_handle(self, project: str) -> str:
        h = f"p{len(self._handles) + 1}"
        self._handles[h] = project
        return h

    def close(self):
        for e in self._engines.values():
            e.close()
        self._engines.clear()


# ---------------------------------------------------------------- 봉투
def ok_env(rev, result, *, affected=None, warnings=None, page=None, dry_run=False) -> dict:
    out = {"ok": True, "rev": rev, "result": result,
           "affected": affected or {"created": [], "modified": [], "deleted": []},
           "warnings": warnings or [], "page": page, "artifacts": [], "job": None}
    if dry_run:
        out["dry_run"] = True
    return out


def err_env(ex: DobimError, rev=None) -> dict:
    out = {"ok": False, "error": ex.to_dict()}
    if rev is not None:
        out["rev"] = rev
    return out


def _rev_of(eng):
    """오류 봉투에 넣을 리비전. 엔진이 망가져 읽지 못하면 생략한다."""
    try:
        return eng.rev if eng is not None else None
    except Exception:
        return None


def _clean(x):
    return json.loads(json.dumps(x, allow_nan=False))


def _pointer(loc) -> str:
    return "/" + "/".join(str(p) for p in loc) if loc else ""


def parse_args(cmd: registry.Command, raw: dict | None):
    try:
        return cmd.args_model.model_validate(raw or {})
    except ValidationError as ex:
        errs = ex.errors()
        first = errs[0]
        raise DobimError("SCHEMA_INVALID", f"{_pointer(first['loc']) or '/'}: {first['msg']}",
                         path=_pointer(first["loc"]) or "/", hint=SCHEMA_HINT,
                         details={"errors": [{"path": _pointer(e["loc"]), "type": e["type"], "msg": e["msg"]}
                                             for e in errs[:5]]}) from None


def get_command(name: str) -> registry.Command:
    cmds = registry.load()
    c = cmds.get(name)
    if c is None:
        close = difflib.get_close_matches(name, list(cmds), n=3)
        raise DobimError("UNKNOWN_COMMAND", f"없는 커맨드: {name}", path="/command",
                         hint="commands.search 로 찾으세요", details={"similar": close})
    return c


# ---------------------------------------------------------------- 확인 토큰 (15.8절)
def _token(eng, name: str, args_json: str, rev: int, exp: int) -> str:
    msg = f"{name}|{args_json}|{rev}|{exp}".encode()
    mac = hmac.new(eng.confirm_secret.encode(), msg, hashlib.sha256).hexdigest()[:24]
    return f"{exp}.{mac}"


def _check_token(eng, name, args_json, rev, token: str) -> None:
    try:
        exp = int(token.split(".", 1)[0])
    except ValueError:
        raise DobimError("CONFIRM_INVALID", "확인 토큰 형식이 틀렸다", path="/confirm") from None
    if exp < time.time():
        raise DobimError("CONFIRM_INVALID", "확인 토큰이 만료됐다", path="/confirm", hint="미리보기를 다시 받으세요")
    if not hmac.compare_digest(token, _token(eng, name, args_json, rev, exp)):
        raise DobimError("CONFIRM_INVALID", "토큰이 인자 또는 리비전과 맞지 않는다", path="/confirm",
                         hint="미리보기를 다시 받으세요")


# ---------------------------------------------------------------- 실행
def check_risk(cmd: registry.Command, allowed: set[str] | None, env: Env) -> None:
    if cmd.risk == "code_exec" and not env.allow_code:
        raise DobimError("PATH_DENIED", "code_exec 커맨드는 서버를 --allow-code 로 시작해야 한다", path="/command")
    if allowed is not None and cmd.risk not in allowed:
        tool = {"read": "dobim_query", "write": "dobim_execute", "destructive": "dobim_execute_destructive",
                "code_exec": "dobim_execute_destructive"}[cmd.risk]
        raise DobimError("RISK_MISMATCH", f"{cmd.name} 은 위험 등급 {cmd.risk} 이다", path="/command",
                         hint=f"{tool} 로 호출하세요")


def run(env: Env, name: str, args: dict | None = None, *, project: str | None = None,
        expected_rev: int | None = None, dry_run: bool = False, confirm: str | None = None,
        allowed_risks: set[str] | None = None) -> dict:
    """커맨드 하나를 실행해 결과 봉투를 돌려준다. 예외는 오류 봉투로 바꾼다."""
    eng = None
    try:
        cmd = get_command(name)
        check_risk(cmd, allowed_risks, env)
        model = parse_args(cmd, args)
        if not cmd.needs_project:
            result = cmd.func(env, model)
            page = result.pop("page", None) if isinstance(result, dict) else None
            return ok_env(result.get("rev", 0) if isinstance(result, dict) else 0, _clean(result), page=_clean(page))
        eng = env.engine(project)
        if cmd.risk == "destructive":
            return _run_destructive(eng, cmd, model, confirm, dry_run)
        if cmd.kind == "mutation":
            r = eng.mutate([(cmd, model)], expected_rev=expected_rev, dry_run=dry_run)
            return ok_env(r["rev"], _clean(r["results"][0]), affected=r["affected"], warnings=r["warnings"],
                          dry_run=r["dry_run"])
        result, warns = eng.read(cmd, model)
        page = result.pop("page", None) if isinstance(result, dict) else None
        return ok_env(eng.rev, _clean(result), warnings=warns, page=_clean(page))
    except DobimError as ex:
        return err_env(ex, _rev_of(eng))
    except Exception as ex:   # 조회·변경 어디서든 예상 밖 예외는 CLI 와 같은 INTERNAL 봉투로 낸다
        return err_env(internal_error(ex), _rev_of(eng))


def _run_destructive(eng, cmd, model, confirm, dry_run) -> dict:
    eng.sync()
    args_json = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    if confirm is None or dry_run:
        from dobim_core.engine import Ctx
        preview = cmd.preview(Ctx(eng, eng.rev + 1, eng.actor, True), model) if cmd.preview else {}
        exp = int(time.time()) + CONFIRM_TTL_S
        raise DobimError("CONFIRM_REQUIRED", f"{cmd.name} 은 되돌릴 수 없는 커맨드라 확인이 필요하다",
                         path="/confirm", hint="details.confirm_token 을 confirm 인자로 다시 호출하세요",
                         details={"preview": _clean(preview), "confirm_token": _token(eng, cmd.name, args_json, eng.rev, exp),
                                  "expires_in_s": CONFIRM_TTL_S, "rev": eng.rev})
    _check_token(eng, cmd.name, args_json, eng.rev, confirm)
    result, warns = eng.read(cmd, model)
    return ok_env(eng.rev, _clean(result), warnings=warns)


def run_batch(env: Env, commands: list[dict], *, project: str | None, expected_rev: int | None = None,
              dry_run: bool = False, allowed_risks: set[str] | None = None) -> dict:
    """변경 커맨드 여러 개를 하나의 원자적 트랜잭션으로 실행한다 (한 번에 되돌려진다)."""
    eng = None
    try:
        if not commands:
            raise DobimError("SCHEMA_INVALID", "commands 가 비어 있다", path="/commands")
        parsed = []
        for i, item in enumerate(commands):
            try:
                cmd = get_command(item.get("command") or item.get("name") or "")
                check_risk(cmd, allowed_risks, env)
                if cmd.kind != "mutation":
                    raise DobimError("USAGE", f"배치에는 mutation 만 넣을 수 있다: {cmd.name}", path="/command")
                parsed.append((cmd, parse_args(cmd, item.get("args"))))
            except DobimError as ex:
                ex.path = f"/commands/{i}{ex.path or ''}"
                raise
        eng = env.engine(project)
        r = eng.mutate(parsed, expected_rev=expected_rev, dry_run=dry_run)
        return ok_env(r["rev"], _clean(r["results"]), affected=r["affected"], warnings=r["warnings"],
                      dry_run=r["dry_run"])
    except DobimError as ex:
        return err_env(ex, _rev_of(eng))
    except Exception as ex:
        return err_env(internal_error(ex), _rev_of(eng))
