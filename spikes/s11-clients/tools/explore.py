# SPDX-License-Identifier: MPL-2.0
"""탐색 시험 실행기: 커맨드 이름 없는 과제 20개를 실제 클라이언트에 준다. 판정은 끝난 뒤 CLI 상태 조회로 한다.

사용: python explore.py <claude|codex-desktop|codex-npm> <out.json> [--only T01,T02] [--workers N]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import common as C
import explore_tasks as E
from layer_c import invocations


def run_one(client: str, t, model: str) -> dict:
    ws = C.WORK / f"explore-{client}" / t[0]
    shutil.rmtree(ws.parent / (ws.name + "-undo-check"), ignore_errors=True)
    t[2](ws)
    tag = f"explore-{client}-{t[0]}"
    prompt = E.prompt_of(t)
    if client == "claude":
        r = C.run_claude(prompt, ws, tag, model=model)
        p = C.parse_claude(r["raw"])
    else:
        r = C.run_codex(prompt, ws, tag, exe=C.codex_desktop_exe() if client == "codex-desktop" else C.CODEX_NPM)
        p = C.parse_codex(r["raw"])
    ok = E.judge(t, ws)
    inv = invocations(p["calls"])
    return {"task": t[0], "success": ok, "exit": r["exit"], "wall_s": r["wall_s"], "tool_calls": len(p["calls"]),
            "search_calls": sum(1 for c in p["calls"] if c["tool"] in C.SEARCH_TOOLS),
            "describe_calls": sum(1 for c in p["calls"] if c["tool"] == "dobim_command_describe"),
            "error_responses": [i["outcome"] for i in inv if i["outcome"] != "ok"],
            "commands_used": sorted({i["command"] for i in inv}), "usage": p["usage"],
            "non_mcp_items": {k: v for k, v in (p.get("item_types") or {}).items() if k not in ("agent_message", "mcp_tool_call", "reasoning")},
            "result_max_bytes": max([len(c["result_text"].encode("utf-8")) for c in p["calls"]] or [0])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("client")
    ap.add_argument("out")
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--model", default="sonnet")
    a = ap.parse_args()
    tasks = [t for t in E.TASKS if not a.only or t[0] in a.only.split(",")]
    with ThreadPoolExecutor(a.workers) as ex:
        rows = list(ex.map(lambda t: run_one(a.client, t, a.model), tasks))
    n = len(rows)
    succ = sum(r["success"] for r in rows)
    summary = {"client": a.client, "tasks": n, "success": succ, "success_rate": round(succ / n, 3),
               "mean_search_calls_all": round(sum(r["search_calls"] for r in rows) / n, 2),
               "mean_search_calls_successful": round(sum(r["search_calls"] for r in rows if r["success"]) / max(1, succ), 2),
               "mean_tool_calls": round(sum(r["tool_calls"] for r in rows) / n, 2),
               "failed": [r["task"] for r in rows if not r["success"]]}
    Path(a.out).write_text(json.dumps(C.scrub({"summary": summary, "rows": rows}), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
