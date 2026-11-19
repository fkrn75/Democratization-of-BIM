# SPDX-License-Identifier: MPL-2.0
"""과업 ⑤ 보조: 합성 모델(tools/bench_50k.py 로 만든 것)에서 벽 하나 이동 뒤 뷰 갱신 시간(T1, 증분 그래프).

모델 파일을 임시 폴더로 복사해 연다(원본은 그대로). 평면·단면·입면 3뷰를 먼저 계산해 두고, 시드로 고른 벽을 옮길 때마다
3뷰 view_lines 를 다시 당기는 시간을 잰다(커맨드 실행 시간은 빼고 갱신만).
사용: python tools/bench_50k_t1.py --model 모델.dobim --moves 10 --views plan,section,elevation
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
import bench_50k as B  # noqa: E402
import kit  # noqa: E402
from dobim_core import drawing as D  # noqa: E402


def run(model: Path, moves: int, views: list[str], seed: int = 7) -> dict:
    work = Path(tempfile.mkdtemp())
    mp = work / model.name
    shutil.copy(model, mp)
    sess = kit.Session(mp, create=False)
    g = sess.eng.g
    vids = [B.VIEWS[v] for v in views]
    t = time.perf_counter()
    for v in vids:
        g.q(D.view_lines, v)
    cold = time.perf_counter() - t
    plan_walls = list(g.q(D.scope_walls, B.V_PLAN))   # 1층 벽(평면에 보이는 벽)만 옮긴다
    rng = random.Random(seed)
    times, recs = [], []
    for _ in range(moves):
        w = rng.choice(plan_walls)
        r = sess.run("wall.move", {"id": w, "dx": rng.choice([-100.0, 100.0]), "dy": 0.0})
        assert r["ok"], r.get("error")
        rc = g.recomputes
        t = time.perf_counter()
        for v in vids:
            g.q(D.view_lines, v)
        times.append((time.perf_counter() - t) * 1000)
        recs.append(g.recomputes - rc)
    sess.close()
    shutil.rmtree(work, ignore_errors=True)
    times.sort()
    return {"model": model.name, "views": views, "cold_sec": round(cold, 2), "moves": moves,
            "refresh_ms": {"p50": round(statistics.median(times), 1), "max": round(times[-1], 1)},
            "recomputed_nodes_p50": statistics.median(recs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--moves", type=int, default=10)
    ap.add_argument("--views", default="plan,section,elevation")
    a = ap.parse_args()
    print(json.dumps(run(a.model, a.moves, a.views.split(",")), ensure_ascii=False))


if __name__ == "__main__":
    main()
