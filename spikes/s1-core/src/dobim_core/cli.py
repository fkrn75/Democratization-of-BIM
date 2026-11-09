# SPDX-License-Identifier: MPL-2.0
"""레지스트리에서 생성되는 CLI (15.3절).

규칙: 대화형 프롬프트 없음, stdout 은 결과 JSON 한 개뿐, stderr 는 사람용 로그, 종료 코드는 errors.py 표.
이 모듈은 dobim_core.mcp_server 를 import 하지 않는다 (import-linter 가 강제한다).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from dobim_core import dispatch
from dobim_core.errors import DobimError, exit_code_of, internal_error


class _Out:
    """프로토콜 전용 stdout. C++ 라이브러리나 print 가 fd 1 에 써도 JSON 을 오염시키지 않는다 (15.10절)."""

    def __init__(self):
        self.fd = os.dup(1)
        os.dup2(2, 1)
        sys.stdout = sys.stderr

    def emit(self, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        view = memoryview(data)
        while view:
            view = view[os.write(self.fd, view):]


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise DobimError("USAGE", message)


def _types_of(prop: dict) -> set[str]:
    ts = set()
    for sub in [prop, *prop.get("anyOf", [])]:
        t = sub.get("type")
        if t:
            ts.add(t)
    return ts


def _parse_kv(items: list[str], name: str) -> dict:
    """key=value 를 해석한다. 값이 문자열 전용 필드이면 숫자처럼 보여도 문자열로 둔다(22자리 id 등)."""
    from dobim_core import registry
    cmd = registry.load().get(name)
    props = cmd.args_model.model_json_schema().get("properties", {}) if cmd else {}
    out = {}
    for it in items:
        if "=" not in it:
            raise DobimError("USAGE", f"key=value 형식이 아니다: {it}")
        k, v = it.split("=", 1)
        ts = _types_of(props.get(k, {}))
        if ts and "string" in ts and not ts & {"integer", "number", "boolean", "array", "object"}:
            out[k] = v
            continue
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def _load_json(spec: str) -> dict:
    if spec == "-":
        text = sys.stdin.buffer.read().decode("utf-8-sig")
    elif spec.startswith("@"):
        try:
            with open(spec[1:], "rb") as fh:
                text = fh.read().decode("utf-8-sig")   # PowerShell 5.1 의 BOM 대응 (15.10절)
        except OSError as ex:
            raise DobimError("NOT_FOUND", f"인자 파일을 읽을 수 없다: {ex.filename}") from ex
    else:
        text = spec
    try:
        val = json.loads(text)
    except ValueError as ex:
        raise DobimError("USAGE", f"JSON 해석 실패: {ex}") from ex
    if not isinstance(val, dict):
        raise DobimError("USAGE", "JSON 인자는 객체여야 한다")
    return val


def build_parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="dobim", description="Democratization of BIM 헤드리스 코어 (S1 스파이크)")
    ap.add_argument("--workspace", default=None, help="파일 접근을 허용할 폴더(기본: 현재 폴더)")
    ap.add_argument("--actor", default="cli", help="로그에 남길 행위자 이름")
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=_Parser)

    p = sub.add_parser("list", help="커맨드 목록")
    p.add_argument("--query", default="")
    p.add_argument("--group", default=None)
    p.add_argument("--risk", default=None)

    p = sub.add_parser("schema", help="커맨드 스키마와 예시")
    p.add_argument("name")

    p = sub.add_parser("run", help="커맨드 실행")
    p.add_argument("name")
    p.add_argument("-p", "--project", default=None)
    p.add_argument("--json", default=None, help="JSON 인자: 문자열, @파일, - (stdin)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--expected-rev", type=int, default=None)
    p.add_argument("--confirm", default=None, help="파괴적 커맨드의 확인 토큰")

    p = sub.add_parser("script", help="JSONL 배치: 줄마다 {\"command\":..., \"args\":{...}}")
    p.add_argument("file")
    p.add_argument("-p", "--project", required=True)
    p.add_argument("--atomic", action="store_true", help="전체를 하나의 트랜잭션으로")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--expected-rev", type=int, default=None)

    p = sub.add_parser("job", help="작업 상태")
    p.add_argument("action", choices=["get"])
    p.add_argument("id")

    p = sub.add_parser("mcp", help="MCP 서버")
    p.add_argument("action", choices=["serve"])
    p.add_argument("--allow-code", action="store_true")
    return ap


def _script_lines(path: str) -> list[dict]:
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8-sig")
    except OSError as ex:
        raise DobimError("NOT_FOUND", f"스크립트를 읽을 수 없다: {ex.filename}") from ex
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            item = json.loads(line)
        except ValueError as ex:
            raise DobimError("USAGE", f"{i}번째 줄 JSON 오류: {ex}") from ex
        out.append(item)
    return out


def main(argv: list[str] | None = None) -> int:
    env = None
    try:
        ns, extras = build_parser().parse_known_args(argv)
        # key=value 인자는 옵션과 섞여 올 수 있어 argparse 위치 인자로 받지 않고 남은 토큰으로 받는다
        if extras and (ns.cmd != "run" or any("=" not in t or t.startswith("-") for t in extras)):
            raise DobimError("USAGE", f"알 수 없는 인자: {' '.join(extras)}")
    except DobimError as ex:
        env_out = dispatch.err_env(ex)
        _Out().emit(env_out)
        return exit_code_of(env_out)
    if ns.cmd == "mcp":   # MCP 는 자기 stdout 을 SDK 가 소유한다. 여기서 fd 를 돌리면 안 된다
        from dobim_core import mcp_server
        return mcp_server.serve(ns.workspace, ns.actor if ns.actor != "cli" else "mcp", ns.allow_code)
    out = _Out()
    try:
        env = dispatch.Env(ns.workspace, ns.actor)
        if ns.cmd == "list":
            env_out = dispatch.run(env, "commands.search", {"query": ns.query, "group": ns.group, "risk": ns.risk})
        elif ns.cmd == "schema":
            env_out = dispatch.run(env, "commands.describe", {"name": ns.name})
        elif ns.cmd == "job":
            env_out = dispatch.err_env(DobimError("NOT_FOUND", f"작업이 없다: {ns.id}", path="/id",
                                                  hint="S1 에는 장시간 작업 커맨드가 없다"))
        elif ns.cmd == "run":
            args = _load_json(ns.json) if ns.json else {}
            args.update(_parse_kv(extras, ns.name))
            env_out = dispatch.run(env, ns.name, args, project=ns.project, expected_rev=ns.expected_rev,
                                   dry_run=ns.dry_run, confirm=ns.confirm)
        else:  # script
            items = _script_lines(ns.file)
            if ns.atomic:
                env_out = dispatch.run_batch(env, items, project=ns.project, expected_rev=ns.expected_rev,
                                             dry_run=ns.dry_run)
            else:
                env_out = dispatch.run_script(env, items, project=ns.project, dry_run=ns.dry_run)
        out.emit(env_out)
        return exit_code_of(env_out)
    except DobimError as ex:
        env_out = dispatch.err_env(ex)
        out.emit(env_out)
        return exit_code_of(env_out)
    except Exception as ex:  # 내부 오류도 봉투로 낸다
        env_out = dispatch.err_env(internal_error(ex))
        out.emit(env_out)
        return exit_code_of(env_out)
    finally:
        if env is not None:
            env.close()


if __name__ == "__main__":
    sys.exit(main())
