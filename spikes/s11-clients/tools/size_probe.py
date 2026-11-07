# SPDX-License-Identifier: MPL-2.0
"""크기·시간 시험: 도구 응답 크기(토큰 추정)와 응답 시간. 큰 모델에서 최악 응답까지 잰다.

토큰 추정은 UTF-8 바이트/3 이다(정밀 토크나이저가 없어 보수적으로 잡은 값이며 추정임을 결과에 밝힌다).
응답은 같은 봉투가 text 와 structuredContent 에 한 번씩 실리므로 wire 바이트는 약 2배이고, 모델이 보는 것은 클라이언트마다 다르다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import common as C
from win_checks import RawMcp
from dobim_core import fixture as F, registry

N_WALL, N_DOOR = 600, 300


def build_big(ws: Path) -> None:
    import shutil
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    e, _ = C.cli(ws, "project.new", {"path": "big.dobim", "guid": F.GUID})
    assert e["ok"]
    cmds = [{"command": "level.create", "args": {"id": F.LV1, "name": "1F", "elevation": 0}}]
    walls = []
    for i in range(N_WALL):
        wid = "1" + f"{i + 1:021d}"
        walls.append(wid)
        y = i * 300
        cmds.append({"command": "wall.create", "args": {"id": wid, "level": F.LV1, "axis": [0, y, 6000, y], "thickness": 200, "height": 3000}})
    for j in range(N_DOOR):
        cmds.append({"command": "door.place", "args": {"id": "2" + f"{j + 1:021d}", "host": walls[j], "offset": 1500, "width": 900, "height": 2100}})
    f = ws / "_big.jsonl"
    f.write_text("\n".join(json.dumps(c) for c in cmds), encoding="utf-8")
    t = time.time()
    p = subprocess.run([str(C.PY), "-m", "dobim_core", "--workspace", str(ws), "script", str(f), "-p", "big.dobim", "--atomic"],
                       capture_output=True, env={**os.environ, **C.server_env()}, stdin=subprocess.DEVNULL, timeout=900)
    e = json.loads(p.stdout.decode("utf-8"))
    assert e["ok"], str(e)[:300]
    f.unlink()
    print("대형 모델 구축", round(time.time() - t, 1), "초", flush=True)


_ENC = {}


def tok(name: str, text: str) -> int | None:
    try:
        import tiktoken
    except ImportError:
        return None
    if name not in _ENC:
        _ENC[name] = tiktoken.get_encoding(name)
    return len(_ENC[name].encode(text, disallowed_special=()))


def measure(m: RawMcp, label: str, tool: str, args: dict) -> dict:
    """한 번 호출해 실제 MCP 선 위의 text + structuredContent 합계 크기를 두 방식(바이트/3 추정, tiktoken)으로 잰다."""
    t = time.perf_counter()
    res = m.tool(tool, args, timeout=300)
    ms = (time.perf_counter() - t) * 1000
    text = res["content"][0]["text"]
    sc = res.get("structuredContent")
    sc_text = json.dumps(sc, ensure_ascii=False) if sc is not None else ""
    env = sc if sc is not None else json.loads(text)
    both = text + sc_text
    pg = env.get("page") or {}
    return {"label": label, "ok": env.get("ok"), "ms": round(ms, 1), "text_bytes": len(text.encode("utf-8")),
            "text_plus_structured_bytes": len(both.encode("utf-8")),
            "est_tokens_bytes_div3": C.est_tokens(both), "tiktoken_cl100k": tok("cl100k_base", both),
            "tiktoken_o200k": tok("o200k_base", both), "page_total": pg.get("total"), "truncated": pg.get("truncated"),
            "has_next_cursor": bool(pg.get("next_cursor")), "code": None if env.get("ok") else env["error"]["code"],
            "_cursor": pg.get("next_cursor")}


def follow(m: RawMcp, label: str, tool: str, args: dict, max_pages: int = 60) -> dict:
    """커서를 따라 끝까지 받는다: 쪽 수, 쪽별 최대 크기."""
    pages, cur = [], None
    while len(pages) < max_pages:
        a = {**args, "args": {**args["args"], **({"cursor": cur} if cur else {})}}
        r = measure(m, label, tool, a)
        pages.append(r)
        if not r["ok"]:
            break
        cur = r.pop("_cursor")
        if not cur:
            break
    return {"label": label + " (끝까지 이어받기)", "pages": len(pages), "complete": not pages[-1]["has_next_cursor"] and pages[-1]["ok"],
            "max_page_est_tokens": max(p["est_tokens_bytes_div3"] for p in pages),
            "max_page_tiktoken_cl100k": max(p["tiktoken_cl100k"] or 0 for p in pages),
            "max_page_tiktoken_o200k": max(p["tiktoken_o200k"] or 0 for p in pages), "total_ms": round(sum(p["ms"] for p in pages), 1)}


def main(out: str) -> int:
    ws = C.WORK / "size-big"
    build_big(ws)
    m = RawMcp(ws)
    m.handshake()
    q = lambda cmd, a=None: ("dobim_query", {"command": cmd, "args": a or {}, "project": "big.dobim"})
    rows = []
    rows.append(measure(m, "project.info", *q("project.info")))
    rows.append(measure(m, "model.stats", *q("model.stats")))
    rows.append(measure(m, "element.query 기본(limit 100)", *q("element.query", {})))
    rows.append(measure(m, "element.query limit=1000(최대)", *q("element.query", {"limit": 1000})))
    rows.append(measure(m, "schedule.rows 문 기본(limit 100)", *q("schedule.rows", {"kind": "door"})))
    rows.append(measure(m, "schedule.rows 문 limit=1000(최대)", *q("schedule.rows", {"kind": "door", "limit": 1000})))
    rows.append(measure(m, "schedule.rows 벽 limit=1000(최대)", *q("schedule.rows", {"kind": "wall", "limit": 1000})))
    rows.append(measure(m, "relation.list 벽 하나", *q("relation.list", {"id": "1" + f"{1:021d}"})))
    rows.append(measure(m, "history.list 기본(20)", *q("history.list")))
    rows.append(measure(m, "history.list limit=500(최대)", *q("history.list", {"limit": 500})))
    rows.append(measure(m, "model.hash", *q("model.hash")))
    rows.append(measure(m, "model.validate", *q("model.validate")))
    rows.append(measure(m, "commands.search 전체", "dobim_commands_search", {}))
    desc = []
    for n in sorted(registry.load()):
        desc.append(measure(m, f"commands.describe {n}", "dobim_command_describe", {"name": n}))
    worst_desc = max(desc, key=lambda r: r["text_plus_structured_bytes"])
    rows.append(worst_desc)
    big = [{"command": "wall.create", "args": {"id": "1" + f"{5000 + i:021d}", "level": F.LV1, "axis": [0, 500000 + i * 300, 6000, 500000 + i * 300],
                                               "thickness": 200, "height": 3000}} for i in range(200)]
    rows.append(measure(m, "dobim_execute 원자 배치 벽 200개", "dobim_execute", {"commands": big, "project": "big.dobim"}))
    pages = [follow(m, "element.query limit=1000", *q("element.query", {"limit": 1000})),
             follow(m, "schedule.rows 벽 limit=1000", *q("schedule.rows", {"kind": "wall", "limit": 1000})),
             follow(m, "history.list limit=500", *q("history.list", {"limit": 500})),
             follow(m, "history.affected 배치 200개", *q("history.affected", {"limit": 1000}))]
    m.close()
    allr = rows + desc
    for r in allr:
        r.pop("_cursor", None)
    over = [r["label"] for r in allr if max(r["tiktoken_cl100k"] or 0, r["tiktoken_o200k"] or 0) > 8000]
    over_est = [r["label"] for r in allr if r["est_tokens_bytes_div3"] > 8000]
    res = {"model": {"walls": N_WALL, "doors": N_DOOR}, "measure": "실제 MCP 선 위의 text + structuredContent 합계",
           "estimate_methods": ["UTF-8 바이트/3 (추정)", "tiktoken cl100k_base, o200k_base (대용 측정, Claude 토크나이저 아님)"],
           "rows": rows, "describe_max": {k: worst_desc[k] for k in ("text_plus_structured_bytes", "est_tokens_bytes_div3", "tiktoken_cl100k", "tiktoken_o200k")},
           "paged": pages,
           "max_est_tokens_bytes_div3": max(r["est_tokens_bytes_div3"] for r in allr),
           "max_tiktoken_cl100k": max(r["tiktoken_cl100k"] or 0 for r in allr),
           "max_tiktoken_o200k": max(r["tiktoken_o200k"] or 0 for r in allr),
           "over_8000_by_tiktoken": over, "over_8000_by_bytes_div3": over_est,
           "slowest_ms": max(r["ms"] for r in allr), "slowest_label": max(allr, key=lambda r: r["ms"])["label"]}
    Path(out).write_text(json.dumps(C.scrub(res), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("max_est_tokens_bytes_div3", "max_tiktoken_cl100k", "max_tiktoken_o200k", "over_8000_by_tiktoken", "slowest_ms", "slowest_label")}, ensure_ascii=False))
    for r in rows:
        print(r["label"], r["text_plus_structured_bytes"], r["est_tokens_bytes_div3"], r["tiktoken_cl100k"], r["tiktoken_o200k"], r["truncated"], r["ms"])
    print(json.dumps(pages, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
