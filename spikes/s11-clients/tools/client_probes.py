# SPDX-License-Identifier: MPL-2.0
"""실제 클라이언트로 하는 개별 탐침 두 가지.

env:      SystemRoot 를 서버 환경에 명시하지 않았을 때 클라이언트가 서버를 정상 기동시키는가(15.10 환경 변수 항목).
bigresp:  큰 응답(element.query limit=1000, 약 600개 요소)을 클라이언트가 어떻게 처리하는가(잘림·파일 저장·출력 한도).
사용: python client_probes.py <env|bigresp> <claude|codex-desktop|codex-npm> <out.json>
"""
import json
import sys
from pathlib import Path

import common as C


def run(kind: str, client: str, out: str) -> int:
    exe = C.codex_desktop_exe() if client == "codex-desktop" else C.CODEX_NPM
    if kind == "env":
        ws = C.WORK / f"probe-env-{client}"
        C.build_fixture(ws)
        prompt = "dobim MCP 도구로 p.dobim 프로젝트 정보를 조회해서 벽 개수만 한 줄로 알려줘."
        if client == "claude":
            raise SystemExit("claude 는 env 항목을 서버 설정 파일에서 직접 주므로 대상이 아니다")
        r = C.run_codex(prompt, ws, f"probe-env-{client}", exe=exe, with_systemroot=False)
    else:
        import size_probe
        ws = C.WORK / "size-big"
        if not (ws / "big.dobim").exists():
            size_probe.build_big(ws)
        prompt = ("연결된 BIM 도구(dobim MCP 서버)만 사용하라. 작업 폴더의 big.dobim 에서 벽 요소 목록을 한 번에 최대한 많이 가져오도록 "
                  "페이지 크기를 최댓값(1000)으로 조회하고, 응답에 든 항목 개수와 응답이 잘렸는지(잘림 표시 문구나 파일로 저장했다는 안내가 있었는지)를 "
                  "한 줄로 보고하라.")
        if client == "claude":
            r = C.run_claude(prompt, ws, f"probe-big-{client}")
        else:
            r = C.run_codex(prompt, ws, f"probe-big-{client}", exe=exe)
    p = C.parse_claude(r["raw"]) if client == "claude" else C.parse_codex(r["raw"])
    tap = C.parse_tap(r["tap"])
    rows = []
    for c in p["calls"]:
        e = c["envelope"]
        rows.append({"tool": c["tool"], "command": (c.get("input") or {}).get("command"), "is_error": c["is_error"],
                     "result_bytes": len(c["result_text"].encode("utf-8")), "envelope_parsed": e is not None,
                     "ok": (e or {}).get("ok"), "items": len(((e or {}).get("result") or {}).get("items", [])) if e else None,
                     "page_total": ((e or {}).get("page") or {}).get("total") if e else None,
                     "result_head": c["result_text"][:80] if e is None else None, "error": str(c.get("error"))[:200] if c.get("error") else None})
    res = {"kind": kind, "client": client, "exit": r["exit"], "wall_s": r["wall_s"], "usage": p["usage"], "calls": rows,
           "final_text": p["final_text"][:400], "errors": p.get("errors"), "other_item_types": p.get("item_types"),
           "server_start_sec": tap.get("t_first_stdout"), "initialize": tap.get("initialize"), "negotiated": tap.get("negotiated"),
           "tap_tool_calls": [{"tool": x["tool"], "ms": x["ms"], "wire_bytes": x["bytes"]} for x in tap.get("latencies", []) if x["method"] == "tools/call"]}
    Path(out).write_text(json.dumps(C.scrub(res), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(C.scrub(res), ensure_ascii=False)[:1800])
    return 0


if __name__ == "__main__":
    sys.exit(run(*sys.argv[1:4]))
