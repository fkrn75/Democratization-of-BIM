# SPDX-License-Identifier: MPL-2.0
"""조작 기록 재생기(GUI B0, ADR-0016 결정 3, docs/2d-editing.md 2.5절 2·3항).

GUI 세션은 실행한 커맨드를 순서대로 조작 기록(JSON, 형식 dobim.gui.record/1)으로 남긴다. 이 도구는 그 기록을 새 프로젝트에
**헤드리스 CLI(python -m dobim_core, 서브프로세스)** 로 재생하고 model.hash 가 기록의 최종 해시와 같은지 본다. 커맨드 레지스트리 하나를
쓰므로 정의상 같아야 하고, 다르면 GUI 가 커맨드 밖에서 상태를 바꾼 것이다(패리티 G 위반).

기록 형식:
  {"format": "dobim.gui.record/1",
   "project": {"guid": "<프로젝트 UUID: 자동 id 가 같게 나오도록>"},
   "steps": [
     {"action": "wall.draw", "command": "wall.create", "args": {...}},                 커맨드 하나
     {"action": "wall.draw_chain", "batch": [{"command": ..., "args": ...}, ...]},     한 트랜잭션 묶음(원자 배치)
     {"action": "canvas.zoom", "exempt": true},                                        제외 동작(커맨드 없음)
     {"action": "canvas.hover", "exempt": true, "query": {"command": "view.pick", "args": {...}}}   제외 동작이 부른 조회
   ],
   "final": {"rev": n, "state": "...", "derived": "...", "ifc": "..."}}          GUI 세션 끝의 model.hash
  파괴적 커맨드(확인 토큰이 필요한 내보내기 등)는 기록에 그대로 두면 재생기가 미리보기 토큰을 받아 확인까지 한다.

검사(실패 코드):
  R_FORMAT    기록 형식 오류
  R_STEP      재생 중 커맨드가 실패했다(기록 당시에는 성공했으므로 재생 불일치)
  R_EXEMPT    제외 동작의 조회가 query·read 가 아니거나, 조회 뒤 rev·해시 3종이 바뀌었다(제외 동작 무해성)
  R_ACTION    --actions 를 주면: 단계의 action 이 actions.json 에 없거나, 그 동작에 매핑되지 않은 커맨드를 냈거나, 제외 여부가 다르다
  R_HASH      재생 뒤 model.hash(rev·state·derived·ifc)가 기록의 final 과 다르다

사용: python tools/replay.py 기록.json [--actions gui/actions.json] [--keep 폴더]   결과 JSON(stdout), 통과 0 / 실패 1.
Recorder 는 같은 기록을 만드는 시험용 기록기다(GUI 의 커맨드 클라이언트가 할 일을 코어 경로로 흉내 낸다).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))
from dobim_core import registry  # noqa: E402

FORMAT = "dobim.gui.record/1"
HASH_KEYS = ("rev", "state", "derived", "ifc")
PROJECT = "replay.dobim"


# ================================================================ 기록기(시험·GUI 어댑터 흉내)
class Recorder:
    """dispatch 경로(CLI·MCP 와 같은)로 커맨드를 실행하며 조작 기록을 쌓는다. 세션 하나 = 프로젝트 하나."""

    def __init__(self, workspace: Path, guid: str, name: str = "gui.dobim"):
        from dobim_core import dispatch
        self._dispatch = dispatch
        self.env = dispatch.Env(workspace, "gui")
        self.project = name
        r = dispatch.run(self.env, "project.new", {"path": name, "guid": guid})
        assert r["ok"], r
        self.guid = guid
        self.steps: list[dict] = []

    def _run(self, name, args, **kw):
        d = self._dispatch
        r = d.run(self.env, name, args, project=self.project, **kw)
        if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":   # 파괴적 커맨드: GUI 는 확인 대화 뒤 토큰으로 다시 부른다
            r = d.run(self.env, name, args, project=self.project, confirm=r["error"]["details"]["confirm_token"], **kw)
        return r

    def do(self, action: str, command: str, args: dict | None = None) -> dict:
        args = dict(args or {})
        r = self._run(command, args)
        if r["ok"]:   # 실패한 커맨드는 상태를 바꾸지 않으므로 기록하지 않는다(GUI 는 오류를 보이고 끝)
            self.steps.append({"action": action, "command": command, "args": args})
        return r

    def batch(self, action: str, items: list[tuple[str, dict]]) -> dict:
        cmds = [{"command": n, "args": dict(a)} for n, a in items]
        r = self._dispatch.run_batch(self.env, cmds, project=self.project)
        if r["ok"]:
            self.steps.append({"action": action, "batch": cmds})
        return r

    def exempt(self, action: str, query: tuple[str, dict] | None = None) -> dict | None:
        step: dict = {"action": action, "exempt": True}
        r = None
        if query is not None:
            r = self._run(query[0], query[1])
            step["query"] = {"command": query[0], "args": dict(query[1])}
        self.steps.append(step)
        return r

    def hashes(self) -> dict:
        r = self._run("model.hash", {})
        return {k: r["result"][k] for k in HASH_KEYS}

    def record(self) -> dict:
        return {"format": FORMAT, "project": {"guid": self.guid}, "steps": json.loads(json.dumps(self.steps)), "final": self.hashes()}

    def close(self):
        self.env.close()


# ================================================================ CLI 재생
class Cli:
    """헤드리스 CLI 서브프로세스. 작업 폴더 하나에서 같은 프로젝트 파일을 쓴다."""

    def __init__(self, workspace: Path, python: str | None = None):
        self.ws = Path(workspace)
        self.py = python or sys.executable
        self.env = {**os.environ, "PYTHONPATH": str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONIOENCODING": "utf-8"}
        self.calls = 0

    def _call(self, argv: list[str]) -> dict:
        self.calls += 1
        p = subprocess.run([self.py, "-m", "dobim_core", "--workspace", str(self.ws), "--actor", "replay", *argv],
                           capture_output=True, cwd=self.ws, env=self.env, timeout=600)
        out = p.stdout.decode("utf-8", errors="replace").strip()
        try:
            return json.loads(out.splitlines()[-1]) if out else {"ok": False, "error": {"code": "NO_OUTPUT", "message": p.stderr.decode("utf-8", "replace")[-500:]}}
        except ValueError:
            return {"ok": False, "error": {"code": "BAD_OUTPUT", "message": out[-500:]}}

    def _jsonfile(self, obj, name) -> str:
        f = self.ws / name
        f.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return str(f)

    def run(self, name: str, args: dict, project: str | None = PROJECT) -> dict:
        argv = ["run", name, "--json", "@" + self._jsonfile(args, "_args.json")]
        if project:
            argv += ["-p", project]
        r = self._call(argv)
        if not r.get("ok") and r.get("error", {}).get("code") == "CONFIRM_REQUIRED":
            r = self._call(argv + ["--confirm", r["error"]["details"]["confirm_token"]])
        return r

    def script(self, items: list[dict], atomic: bool) -> dict:
        f = self.ws / "_steps.jsonl"
        f.write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items), encoding="utf-8")
        return self._call(["script", str(f), "-p", PROJECT] + (["--atomic"] if atomic else []))

    def hashes(self) -> dict:
        r = self.run("model.hash", {})
        return {k: r["result"][k] for k in HASH_KEYS} if r.get("ok") else {"error": r.get("error")}


def _load_actions(path):
    if path is None:
        return None
    acts = json.loads(Path(path).read_text(encoding="utf-8"))["actions"]
    out = {}
    for a in acts:
        out[a["id"]] = {"exempt": True} if a.get("exempt") else {"commands": [a["command"]] if "command" in a else list(a["commands"])}
    return out


def _check_format(rec) -> str | None:
    if not isinstance(rec, dict) or rec.get("format") != FORMAT:
        return f"format 이 {FORMAT} 이 아니다"
    if not isinstance(rec.get("project"), dict) or not isinstance(rec["project"].get("guid"), str):
        return "project.guid 가 없다"
    if not isinstance(rec.get("steps"), list):
        return "steps 목록이 없다"
    if not isinstance(rec.get("final"), dict) or any(k not in rec["final"] for k in HASH_KEYS):
        return "final 에 rev·state·derived·ifc 가 다 있어야 한다"
    for i, s in enumerate(rec["steps"]):
        if not isinstance(s, dict) or not isinstance(s.get("action"), str):
            return f"{i}번째 단계에 action 이 없다"
        kinds = [k for k in ("command", "batch", "exempt") if k in s]
        if len(kinds) != 1:
            return f"{i}번째 단계는 command·batch·exempt 중 정확히 하나를 가져야 한다"
        if "command" in s and (not isinstance(s["command"], str) or not isinstance(s.get("args", {}), dict)):
            return f"{i}번째 단계의 command·args 형식이 틀렸다"
        if "batch" in s and (not isinstance(s["batch"], list) or not s["batch"]):
            return f"{i}번째 단계의 batch 가 비었다"
        if "exempt" in s and (s["exempt"] is not True or set(s) - {"action", "exempt", "query"}):
            return f"{i}번째 제외 단계에는 query 말고 다른 것이 있으면 안 된다(제외 동작은 커맨드를 내지 않는다)"
    return None


def check_actions(rec: dict, actions: dict) -> list[dict]:
    """기록의 단계 ↔ actions.json 대조: 없는 동작, 제외 여부 불일치, 동작에 매핑되지 않은 커맨드."""
    errors = []
    for i, s in enumerate(rec["steps"]):
        a = actions.get(s["action"])
        if a is None:
            errors.append({"code": "R_ACTION", "step": i, "message": f"actions.json 에 없는 동작: {s['action']}"})
            continue
        if ("exempt" in s) != bool(a.get("exempt")):
            errors.append({"code": "R_ACTION", "step": i, "message": f"{s['action']}: 제외 여부가 actions.json 과 다르다"})
            continue
        names = [s["command"]] if "command" in s else [b.get("command") for b in s.get("batch", [])]
        for n in names:
            if n not in a.get("commands", []):
                errors.append({"code": "R_ACTION", "step": i, "message": f"{s['action']} 에 매핑되지 않은 커맨드를 냈다: {n}"})
    return errors


def replay(rec: dict, workspace: Path, actions: dict | None = None, python: str | None = None) -> dict:
    """기록 rec 을 workspace(빈 폴더)에 CLI 로 재생한다. 결과 {"pass", "errors", "final", "replayed", "cli_calls"}."""
    errors: list[dict] = []
    bad = _check_format(rec)
    if bad:
        return {"pass": False, "errors": [{"code": "R_FORMAT", "step": None, "message": bad}], "final": None, "replayed": None, "cli_calls": 0}
    reg = registry.load()
    if actions is not None:   # 재생 전에 대조한다(매핑 밖 커맨드는 패리티 G 위반)
        errors += check_actions(rec, actions)
    cli = Cli(workspace, python)
    r = cli.run("project.new", {"path": PROJECT, "guid": rec["project"]["guid"]}, project=None)
    if not r.get("ok"):
        return {"pass": False, "errors": errors + [{"code": "R_STEP", "step": None, "message": f"project.new 실패: {r.get('error')}"}],
                "final": None, "replayed": None, "cli_calls": cli.calls}
    pending: list[dict] = []   # 이어진 보통 단계는 비원자 script 한 번으로 보낸다(서브프로세스 수를 줄인다)
    pend_idx: list[int] = []

    def flush() -> bool:
        if not pending:
            return True
        res = cli.script(pending, atomic=False)
        ok = res.get("ok", False)
        if not ok:
            items = (res.get("result") or {}).get("items") or []
            fail = next((it for it in items if not it.get("ok")), None)
            at = pend_idx[fail["index"]] if fail and fail.get("index", -1) < len(pend_idx) else pend_idx[0]
            errors.append({"code": "R_STEP", "step": at, "message": f"재생 실패: {(fail or res).get('error')}"})
        pending.clear()
        pend_idx.clear()
        return ok

    for i, s in enumerate(rec["steps"]):
        if "command" in s:
            c = reg.get(s["command"])
            if c is not None and c.risk == "destructive":   # 확인 토큰 2단계는 run 으로 따로
                if not flush():
                    break
                rr = cli.run(s["command"], s.get("args", {}))
                if not rr.get("ok"):
                    errors.append({"code": "R_STEP", "step": i, "message": f"재생 실패: {rr.get('error')}"})
                    break
                continue
            pending.append({"command": s["command"], "args": s.get("args", {})})
            pend_idx.append(i)
            continue
        if not flush():
            break
        if "batch" in s:
            rr = cli.script([{"command": b.get("command"), "args": b.get("args", {})} for b in s["batch"]], atomic=True)
            if not rr.get("ok"):
                errors.append({"code": "R_STEP", "step": i, "message": f"원자 배치 재생 실패: {rr.get('error')}"})
                break
            continue
        q = s.get("query")   # 제외 단계
        if q is not None:
            c = reg.get(q.get("command")) if isinstance(q, dict) else None
            if c is None or c.kind != "query" or c.risk != "read":
                errors.append({"code": "R_EXEMPT", "step": i, "message": f"제외 동작의 조회가 query·read 커맨드가 아니다: {q}"})
                continue
            before = cli.hashes()
            rr = cli.run(q["command"], q.get("args", {}))
            after = cli.hashes()
            if before != after:
                errors.append({"code": "R_EXEMPT", "step": i, "message": f"제외 동작 뒤 rev·해시가 바뀌었다: {before} -> {after}"})
            if not rr.get("ok"):
                errors.append({"code": "R_EXEMPT", "step": i, "message": f"제외 동작의 조회가 실패했다: {rr.get('error')}"})
    else:
        flush()
    got = cli.hashes()
    if got != {k: rec["final"][k] for k in HASH_KEYS}:
        diff = [k for k in HASH_KEYS if got.get(k) != rec["final"].get(k)]
        errors.append({"code": "R_HASH", "step": None, "message": f"재생 해시가 기록과 다르다: {diff}"})
    return {"pass": not errors, "errors": errors, "final": rec["final"], "replayed": got, "cli_calls": cli.calls,
            "steps": len(rec["steps"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="조작 기록(dobim.gui.record/1)을 CLI 로 재생해 model.hash 를 비교한다")
    ap.add_argument("record")
    ap.add_argument("--actions", default=None, help="gui/actions.json(주면 단계의 동작·커맨드 매핑도 본다)")
    ap.add_argument("--keep", default=None, help="재생 작업 폴더를 여기에 남긴다(기본: 임시 폴더, 끝나면 지움)")
    ns = ap.parse_args(argv)
    try:
        rec = json.loads(Path(ns.record).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        sys.stdout.write(json.dumps({"pass": False, "errors": [{"code": "R_FORMAT", "step": None, "message": str(ex)}]}, ensure_ascii=False) + "\n")
        return 1
    actions = _load_actions(ns.actions)
    if ns.keep:
        ws = Path(ns.keep)
        ws.mkdir(parents=True, exist_ok=True)
        res = replay(rec, ws, actions)
    else:
        ws = Path(tempfile.mkdtemp(prefix="dobim-replay-"))
        try:
            res = replay(rec, ws, actions)
        finally:
            shutil.rmtree(ws, ignore_errors=True)
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
