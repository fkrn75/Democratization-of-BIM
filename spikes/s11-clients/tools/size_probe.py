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


def measure(m: RawMcp, label: str, tool: str, args: dict) -> dict:
    t = time.perf_counter()
    res = m.tool(tool, args, timeout=300)
    ms = (time.perf_counter() - t) * 1000
    text = res["content"][0]["text"]
    env = res.get("structuredContent") or json.loads(text)
    return {"label": label, "ok": env.get("ok"), "ms": round(ms, 1), "text_bytes": len(text.encode("utf-8")),
            "est_tokens_text": C.est_tokens(text), "est_tokens_text_plus_structured": C.est_tokens(text) * 2,
            "page_total": (env.get("page") or {}).get("total"), "code": None if env.get("ok") else env["error"]["code"]}


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
    rows.append(measure(m, "element.query limit=200", *q("element.query", {"limit": 200})))
    rows.append(measure(m, "element.query limit=1000(최대)", *q("element.query", {"limit": 1000})))
    rows.append(measure(m, "schedule.rows 문 기본(limit 100)", *q("schedule.rows", {"kind": "door"})))
    rows.append(measure(m, "schedule.rows 문 limit=1000(최대)", *q("schedule.rows", {"kind": "door", "limit": 1000})))
    rows.append(measure(m, "schedule.rows 벽 limit=1000(최대)", *q("schedule.rows", {"kind": "wall", "limit": 1000})))
    rows.append(measure(m, "history.list 기본(20)", *q("history.list")))
    rows.append(measure(m, "history.list limit=500(최대)", *q("history.list", {"limit": 500})))
    rows.append(measure(m, "relation.list 벽 하나", *q("relation.list", {"id": "1" + f"{1:021d}"})))
    rows.append(measure(m, "model.hash", *q("model.hash")))
    rows.append(measure(m, "model.validate", *q("model.validate")))
    rows.append(measure(m, "commands.search 전체", "dobim_commands_search", {}))
    desc = []
    for n in sorted(registry.load()):
        desc.append(measure(m, f"commands.describe {n}", "dobim_command_describe", {"name": n}))
    worst_desc = max(desc, key=lambda r: r["text_bytes"])
    rows.append(worst_desc)
    m.close()
    over = [r for r in rows + desc if r["est_tokens_text"] > 8000]
    res = {"model": {"walls": N_WALL, "doors": N_DOOR}, "token_estimate_method": "UTF-8 바이트 / 3 (추정)",
           "rows": rows, "describe_max_est_tokens": worst_desc["est_tokens_text"],
           "max_est_tokens_text": max(r["est_tokens_text"] for r in rows + desc),
           "over_8000_tokens": [r["label"] for r in over], "slowest_ms": max(r["ms"] for r in rows + desc),
           "slowest_label": max(rows + desc, key=lambda r: r["ms"])["label"]}
    Path(out).write_text(json.dumps(C.scrub(res), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("max_est_tokens_text", "over_8000_tokens", "slowest_ms", "slowest_label")}, ensure_ascii=False))
    for r in rows:
        print(r["label"], r["text_bytes"], r["est_tokens_text"], r["ms"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
