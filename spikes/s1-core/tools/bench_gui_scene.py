# SPDX-License-Identifier: MPL-2.0
"""GUI B1 성능 측정: 합성 평면(벽 + 문 약 5,000 요소)에서 view.scene·view.pick·geom.snap 응답 시간과 응답 토큰.

목표(ADR-0016 뒤집는 조건): 응답 하나 30초 상한, 응답 하나 8,000토큰(봉투 두 벌 기준, 한 벌 3,950 추정 토큰 상한).
측정 경로:
  - 사이드카·MCP 경로(프로세스 하나, dispatch.run, 엔진 캐시): 첫 쪽(차가운 계산) / 다음 쪽 / 전체 쪽 / 타일 / 바뀐 뒤 다시 받기
  - CLI 경로(서브프로세스 하나 = 프로젝트 열기 + 계산 + 응답 한 쪽)
토큰: limits.est_tokens(보수 추정). tiktoken 이 있으면 o200k_base 실측도 적는다(대용 측정, Claude 토크나이저 아님).
사용: python tools/bench_gui_scene.py --rows 38 --cols 38 --out 결과.json   (합성 데이터만 쓴다)
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
import kit  # noqa: E402
from dobim_core import limits  # noqa: E402

LV = "3" + "0" * 20 + "1"
V = "4" + "0" * 20 + "1"
CELL = 4000.0


def build(sess, rows, cols, chunk=250):
    """rows x cols 방 격자: 가로벽 (rows+1) x cols, 세로벽 (cols+1) x rows. 안쪽 가로벽마다 문 하나, 바깥 가로벽마다 문 하나."""
    assert sess.run("level.create", {"id": LV, "name": "1F", "elevation": 0})["ok"]
    assert sess.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": LV, "cut_height": 1200})["ok"]
    walls, doors = [], []
    n = 0
    for r in range(rows + 1):
        for c in range(cols):
            n += 1
            wid = "1" + f"{n:021d}"
            walls.append(("wall.create", {"id": wid, "level": LV, "axis": [c * CELL, r * CELL, (c + 1) * CELL, r * CELL], "thickness": 200, "height": 3000}))
            doors.append(("door.place", {"id": "2" + f"{n:021d}", "host": wid, "offset": 1200, "width": 900, "height": 2100}))
    for c in range(cols + 1):
        for r in range(rows):
            n += 1
            wid = "1" + f"{n:021d}"
            walls.append(("wall.create", {"id": wid, "level": LV, "axis": [c * CELL, r * CELL, c * CELL, (r + 1) * CELL], "thickness": 200, "height": 3000}))
            if c not in (0, cols) and (r + c) % 3 == 0:
                doors.append(("door.place", {"id": "2" + f"{n:021d}", "host": wid, "offset": 2000, "width": 800, "height": 2100}))
    t = time.perf_counter()
    items = walls + doors
    for i in range(0, len(items), chunk):
        r = sess.batch([{"command": a, "args": b} for a, b in items[i:i + chunk]])
        assert r["ok"], r.get("error")
    return len(walls), len(doors), time.perf_counter() - t


def tok(env, enc):
    s = json.dumps(env, ensure_ascii=False)
    out = {"bytes": len(s.encode("utf-8")), "est_tokens": round(limits.est_tokens(env), 1)}
    if enc is not None:
        out["tiktoken_o200k"] = len(enc.encode(s))
    return out


def timed(fn):
    t = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t


def all_pages(sess, args, enc):
    cur, n, t0, worst, sizes = None, 0, time.perf_counter(), 0.0, []
    first = None
    while True:
        env, dt = timed(lambda: sess.run("view.scene", {**args, **({"cursor": cur} if cur else {})}))
        assert env["ok"], env
        if first is None:
            first = dt
        worst = max(worst, dt)
        sizes.append(tok(env, enc))
        n += 1
        cur = env["page"]["next_cursor"]
        if cur is None:
            break
    return {"pages": n, "first_s": round(first, 3), "worst_page_s": round(worst, 3), "total_s": round(time.perf_counter() - t0, 2),
            "max_est_tokens": max(s["est_tokens"] for s in sizes), "max_bytes": max(s["bytes"] for s in sizes),
            **({"max_tiktoken_o200k": max(s["tiktoken_o200k"] for s in sizes)} if enc else {}),
            "mean_est_tokens": round(statistics.mean(s["est_tokens"] for s in sizes), 1), "summary": env["result"]["summary"]}


def cli_first_page(path: Path, python: str):
    env = {**os.environ, "PYTHONPATH": str(HERE.parent / "src"), "PYTHONIOENCODING": "utf-8"}
    t = time.perf_counter()
    p = subprocess.run([python, "-m", "dobim_core", "--workspace", str(path.parent), "run", "view.scene", "-p", path.name, f"id={V}"],
                       capture_output=True, env=env, cwd=path.parent, timeout=900)
    dt = time.perf_counter() - t
    out = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])
    return {"ok": out["ok"], "seconds": round(dt, 2), "items": len(out["result"]["items"]) if out["ok"] else None}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=38)
    ap.add_argument("--cols", type=int, default=38)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-cli", action="store_true")
    ns = ap.parse_args(argv)
    try:
        import tiktoken
        enc = tiktoken.get_encoding("o200k_base")
    except Exception:   # 개발 의존이 없으면 추정 토큰만
        enc = None
    ws = Path(tempfile.mkdtemp(prefix="dobim-gui-bench-"))
    sess = kit.Session(ws / "big.dobim")
    res: dict = {"rows": ns.rows, "cols": ns.cols}
    nw, nd, tb = build(sess, ns.rows, ns.cols)
    res["model"] = {"walls": nw, "doors": nd, "elements": nw + nd + 2, "build_s": round(tb, 1)}
    print("모델", res["model"], flush=True)
    sess.eng.sync()
    env, dt = timed(lambda: sess.run("view.linework", {"id": V, "limit": 1}))
    res["view_linework_first_s"] = round(dt, 2)   # 기준: 지금 도면 선분(병합) 첫 계산
    from dobim_core import drawing as D
    D.forget_views(sess.eng.g, [V])   # 공정한 비교: 뷰 노드를 비우고 view.scene 을 차갑게 다시 잰다
    env, dt = timed(lambda: sess.run("view.scene", {"id": V}))
    res["scene_cold_first_page_s"] = round(dt, 2)
    res["scene_cold_first_page_size"] = tok(env, enc)
    print("차가운 첫 쪽", dt, flush=True)
    res["scene_all_pages_default"] = all_pages(sess, {"id": V}, enc)
    res["scene_all_pages_decimals0"] = all_pages(sess, {"id": V, "decimals": 0, "limit": 1000}, enc)
    W = ns.cols * CELL
    H = ns.rows * CELL
    res["scene_tile_1of16"] = all_pages(sess, {"id": V, "bbox": [0, 0, W / 4, H / 4]}, enc)
    print("쪽 나누기", res["scene_all_pages_default"]["pages"], flush=True)
    # 집기·스냅(따뜻한 장면)
    pts = [(W * f, H * g) for f, g in ((0.13, 0.5), (0.5, 0.5), (0.77, 0.21))]
    pk = [timed(lambda p=p: sess.run("view.pick", {"id": V, "x": p[0] + 30, "y": p[1]}))[1] for p in pts]
    sn = [timed(lambda p=p: sess.run("geom.snap", {"view": V, "x": p[0] + 30, "y": p[1] + 20, "from": [0, 0]}))[1] for p in pts]
    res["pick_s"] = [round(x, 3) for x in pk]
    res["snap_s"] = [round(x, 3) for x in sn]
    # 바뀐 뒤 다시 받기(증분): 벽 하나 옮기고 그 벽만 ids 로
    wid = "1" + f"{5:021d}"
    mv, dmove = timed(lambda: sess.run("wall.move", {"id": wid, "dx": 0, "dy": 50}))
    assert mv["ok"], mv.get("error")
    env, dt = timed(lambda: sess.run("view.scene", {"id": V, "ids": [wid]}))
    res["after_move"] = {"wall_move_s": round(dmove, 3), "scene_ids_s": round(dt, 2), "size": tok(env, enc)}
    sess.close()
    if not ns.no_cli:
        res["cli_cold_first_page"] = cli_first_page(ws / "big.dobim", sys.executable)
        print("CLI", res["cli_cold_first_page"], flush=True)
    res["targets"] = {"response_s_max": 30, "response_tokens_max": 8000, "per_copy_est_tokens_cap": limits.RESPONSE_CAP_TOKENS}
    text = json.dumps(res, ensure_ascii=False, indent=1)
    print(text)
    if ns.out:
        Path(ns.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
