# SPDX-License-Identifier: MPL-2.0
"""패리티 G 정적 검사기(GUI B0, ADR-0016 결정 3, docs/2d-editing.md 2.5절 1항).

GUI 의 모든 메뉴·도구·단축 동작은 gui/actions.json 에 선언된다. 이 검사기는 다음을 본다(하나라도 어기면 실패 = 빌드를 막는다).
  E_FORMAT          actions.json·gui_exempt.yaml 형식 오류(필드·중복 id·command/commands/exempt 중 정확히 하나)
  E_UNKNOWN_COMMAND command(s) 가 커맨드 레지스트리에 없다(매핑이 존재하지 않는 커맨드를 가리킨다)
  E_EXEMPT_MISSING  exempt 동작인데 gui_exempt.yaml 에 같은 id 항목이 없다(매핑 누락)
  E_NO_REASON       gui_exempt.yaml 항목에 이유(reason)가 없거나 비었다
  E_EXEMPT_WRITES   제외 항목이 저장소를 건드릴 수 있다: queries 에 조회(query·read)가 아닌 커맨드가 있거나,
                    제외 id 가 조회가 아닌 레지스트리 커맨드 이름과 같다(커맨드를 제외로 숨기기)
  E_BYPASS          GUI 소스(--src)에서 커맨드 클라이언트 파일(client_files) 밖에서 사이드카·dispatch 를 직접 부른다
경고(실패 아님): W_UNUSED_EXEMPT 어느 동작도 쓰지 않는 제외 항목.

사용: python tools/parity_g.py [--actions gui/actions.json] [--exempt gui_exempt.yaml] [--src GUI 소스 폴더]
결과는 JSON 한 벌(stdout), 통과 0 / 실패 1. 기본 경로는 spikes/s12-gui(평면 GUI 앱 자리)다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from dobim_core import registry  # noqa: E402

GUI_ROOT = HERE.parent.parent / "s12-gui"
DEFAULT_ACTIONS = GUI_ROOT / "gui" / "actions.json"
DEFAULT_EXEMPT = GUI_ROOT / "gui_exempt.yaml"
FORMAT = "dobim.gui.actions/1"
ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
SRC_EXT = (".ts", ".js", ".mjs", ".svelte", ".tsx", ".jsx", ".rs")
# 커맨드 클라이언트 밖에서 나오면 안 되는 호출(사이드카·코어를 직접 부르는 길)
BYPASS_PATTERNS = (r"\binvoke\s*\(", r"gui-serve", r"\bdispatch\.run\b", r"Command\.sidecar\b", r"\bsidecar\s*\(")


class YamlError(ValueError):
    pass


def _scalar(s: str):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s.startswith("["):
        if not s.endswith("]"):
            raise YamlError(f"닫히지 않은 목록: {s}")
        inner = s[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    return s


def load_exempt_yaml(text: str) -> list[dict]:
    """gui_exempt.yaml 을 읽는다. 코어에 YAML 의존을 들이지 않으려고 이 파일이 쓰는 부분집합만 받는다:
    최상위는 목록, 항목은 '- key: value' 로 시작하고 이어지는 '  key: value' 줄, 값은 문자열 또는 [a, b] 목록, # 주석.
    그 밖의 모양(중첩 사전·여러 줄 문자열·탭 들여쓰기)은 YamlError."""
    items: list[dict] = []
    cur = None
    for n, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            raise YamlError(f"{n}번째 줄: 탭 들여쓰기는 받지 않는다")
        line = raw.split(" #")[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        if line.startswith("- "):
            cur = {}
            items.append(cur)
            body = line[2:]
        elif line.startswith("  ") and cur is not None and not line.startswith("   "):
            body = line[2:]
        else:
            raise YamlError(f"{n}번째 줄: 지원하지 않는 모양: {raw.strip()}")
        if ":" not in body:
            raise YamlError(f"{n}번째 줄: key: value 가 아니다")
        k, v = body.split(":", 1)
        k = k.strip()
        if not re.fullmatch(r"[a-z_]+", k):
            raise YamlError(f"{n}번째 줄: 잘못된 키 {k!r}")
        if k in cur:
            raise YamlError(f"{n}번째 줄: 키 중복 {k}")
        cur[k] = _scalar(v)
    return items


def _err(out, code, ident, msg):
    out.append({"code": code, "id": ident, "message": msg})


def check(actions: dict, exempt: list[dict], reg: dict | None = None, src: Path | None = None) -> dict:
    """actions(actions.json 의 내용)·exempt(gui_exempt.yaml 의 항목)를 검사한다. 결과 {"pass", "errors", "warnings", "counts"}."""
    reg = registry.load() if reg is None else reg
    errors: list[dict] = []
    warnings: list[dict] = []
    # ---- gui_exempt.yaml
    ex_by: dict[str, dict] = {}
    for i, e in enumerate(exempt):
        eid = e.get("id")
        if not isinstance(eid, str) or not ID_RE.match(eid):
            _err(errors, "E_FORMAT", eid, f"gui_exempt.yaml {i + 1}번째 항목의 id 가 없거나 형식이 틀렸다")
            continue
        if eid in ex_by:
            _err(errors, "E_FORMAT", eid, "gui_exempt.yaml 에 id 가 두 번 있다")
            continue
        ex_by[eid] = e
        extra = set(e) - {"id", "reason", "queries"}
        if extra:
            _err(errors, "E_FORMAT", eid, f"gui_exempt.yaml 항목에 모르는 키: {sorted(extra)}")
        reason = e.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            _err(errors, "E_NO_REASON", eid, "제외 항목에는 이유(reason)가 필요하다")
        qs = e.get("queries", [])
        if not isinstance(qs, list):
            _err(errors, "E_FORMAT", eid, "queries 는 [커맨드, ...] 목록이어야 한다")
            qs = []
        for q in qs:
            c = reg.get(q)
            if c is None:
                _err(errors, "E_UNKNOWN_COMMAND", eid, f"queries 의 커맨드가 레지스트리에 없다: {q}")
            elif c.kind != "query" or c.risk != "read":
                _err(errors, "E_EXEMPT_WRITES", eid, f"제외 동작이 저장소를 건드릴 수 있는 커맨드를 부른다: {q}({c.kind}/{c.risk})")
        c = reg.get(eid)
        if c is not None and (c.kind != "query" or c.risk != "read"):
            _err(errors, "E_EXEMPT_WRITES", eid, f"제외 id 가 저장소를 바꾸는 커맨드 이름과 같다({c.kind}/{c.risk}): 커맨드를 제외로 숨길 수 없다")
    # ---- actions.json
    if actions.get("format") != FORMAT:
        _err(errors, "E_FORMAT", None, f"actions.json format 이 {FORMAT} 이 아니다")
    acts = actions.get("actions")
    if not isinstance(acts, list) or not acts:
        _err(errors, "E_FORMAT", None, "actions.json 에 actions 목록이 없다")
        acts = []
    seen, used_ex = set(), set()
    n_cmd = n_ex = 0
    for i, a in enumerate(acts):
        aid = a.get("id") if isinstance(a, dict) else None
        if not isinstance(aid, str) or not ID_RE.match(aid):
            _err(errors, "E_FORMAT", aid, f"actions.json {i + 1}번째 동작의 id 가 없거나 형식이 틀렸다")
            continue
        if aid in seen:
            _err(errors, "E_FORMAT", aid, "동작 id 가 두 번 있다")
            continue
        seen.add(aid)
        extra = set(a) - {"id", "label", "command", "commands", "exempt"}
        if extra:
            _err(errors, "E_FORMAT", aid, f"동작에 모르는 키: {sorted(extra)}")
        has = [k for k in ("command", "commands", "exempt") if k in a]
        if len(has) != 1:
            _err(errors, "E_FORMAT", aid, "동작은 command·commands·exempt 중 정확히 하나를 가져야 한다(매핑 누락)")
            continue
        if has[0] == "exempt":
            n_ex += 1
            if a["exempt"] is not True:
                _err(errors, "E_FORMAT", aid, "exempt 는 true 여야 한다(제외 이유는 gui_exempt.yaml 의 같은 id 항목에)")
            elif aid not in ex_by:
                _err(errors, "E_EXEMPT_MISSING", aid, "exempt 동작인데 gui_exempt.yaml 에 같은 id 항목이 없다")
            used_ex.add(aid)
            continue
        names = [a["command"]] if has[0] == "command" else a["commands"]
        if not isinstance(names, list) or not names or not all(isinstance(x, str) for x in names):
            _err(errors, "E_FORMAT", aid, "command 는 문자열, commands 는 비지 않은 문자열 목록이어야 한다")
            continue
        n_cmd += 1
        for nm in names:
            if nm not in reg:
                _err(errors, "E_UNKNOWN_COMMAND", aid, f"레지스트리에 없는 커맨드: {nm}")
    for eid in sorted(set(ex_by) - used_ex):
        _err(warnings, "W_UNUSED_EXEMPT", eid, "어느 동작도 쓰지 않는 제외 항목")
    # ---- 소스 정적 검색(c)
    n_files = 0
    if src is not None:
        clients = {Path(c).as_posix() for c in actions.get("client_files", [])}
        pats = [re.compile(p) for p in BYPASS_PATTERNS]
        for f in sorted(src.rglob("*")):
            if not f.is_file() or f.suffix not in SRC_EXT or "node_modules" in f.parts or "target" in f.parts:
                continue
            n_files += 1
            rel = f.relative_to(src).as_posix()
            if rel in clients:
                continue
            for ln_no, ln in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for p in pats:
                    if p.search(ln):
                        _err(errors, "E_BYPASS", rel, f"{ln_no}번째 줄: 커맨드 클라이언트 밖에서 사이드카를 직접 부른다({p.pattern})")
    return {"pass": not errors, "errors": errors, "warnings": warnings,
            "counts": {"actions": len(seen), "command_actions": n_cmd, "exempt_actions": n_ex, "exempt_entries": len(ex_by), "scanned_files": n_files}}


def check_files(actions_path: Path, exempt_path: Path, src: Path | None = None) -> dict:
    try:
        actions = json.loads(Path(actions_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        return {"pass": False, "errors": [{"code": "E_FORMAT", "id": None, "message": f"actions.json 을 읽지 못했다: {ex}"}], "warnings": [], "counts": {}}
    try:
        exempt = load_exempt_yaml(Path(exempt_path).read_text(encoding="utf-8"))
    except (OSError, YamlError) as ex:
        return {"pass": False, "errors": [{"code": "E_FORMAT", "id": None, "message": f"gui_exempt.yaml 을 읽지 못했다: {ex}"}], "warnings": [], "counts": {}}
    return check(actions, exempt, src=src)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="패리티 G 정적 검사(gui/actions.json ↔ 커맨드 레지스트리·gui_exempt.yaml)")
    ap.add_argument("--actions", default=str(DEFAULT_ACTIONS))
    ap.add_argument("--exempt", default=str(DEFAULT_EXEMPT))
    ap.add_argument("--src", default=None, help="GUI 소스 폴더(있으면 사이드카 직접 호출을 찾는다)")
    ns = ap.parse_args(argv)
    res = check_files(Path(ns.actions), Path(ns.exempt), Path(ns.src) if ns.src else None)
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
