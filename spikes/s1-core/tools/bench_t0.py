# SPDX-License-Identifier: MPL-2.0
"""합격 기준 3: 합성 5,000요소에서 T0 p95 50ms 이내.

T0 의 정의(architecture.md 5절 "T0 동기: 입력, 의미 데이터, 일람표 값")를 가장 엄격하게 잡았다.
  변경 커맨드 1개의 dispatch.run 전체 시간 =
    인자 검증 + BEGIN IMMEDIATE + 따라잡기 확인 + 커맨드 적용(IFC 속성셋·재료 포함) + 증분 무효화
    + 문·벽 일람표 전체(64버킷)·합계·개수를 최신으로 재계산(T0 상태 전체) + 로그·요소 행 쓰기 + COMMIT(WAL)
IFC 형상 투영(T1 이후)과 뷰·도면은 포함하지 않는다. 투영 시간은 따로 잰다.
"""
import argparse
import json
import platform
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit  # noqa: E402
from dobim_core import queries as Q  # noqa: E402
from dobim_core.engine import Engine  # noqa: E402


def build(sess: kit.Session, levels=10, cols=15, rows=10, doors_per_level=199):
    """레벨 10 + 벽 3,000 + 문 1,990 = 5,000 요소. 벽은 격자이고 모서리마다 조인한다."""
    n = {"1": 0, "2": 0, "3": 0}

    def nid(p):
        n[p] += 1
        return kit.mkid(p, n[p])

    cmds, walls, doors = [], [], []
    for li in range(levels):
        lv = nid("3")
        cmds.append(("level.create", {"id": lv, "name": f"L{li}", "elevation": li * 3300}))
        H, V = {}, {}
        for r in range(rows):
            for c in range(cols):
                H[r, c], V[r, c] = nid("1"), nid("1")
                cmds.append(("wall.create", {"id": H[r, c], "level": lv, "axis": [c * 6000, r * 5000, (c + 1) * 6000, r * 5000]}))
                cmds.append(("wall.create", {"id": V[r, c], "level": lv, "axis": [c * 6000, r * 5000, c * 6000, (r + 1) * 5000]}))
        lw = []
        for r in range(rows):
            for c in range(cols):
                cmds.append(("join.set", {"a": H[r, c], "b": V[r, c]}))
                if c + 1 < cols:
                    cmds.append(("join.set", {"a": H[r, c], "b": V[r, c + 1]}))
                lw += [H[r, c], V[r, c]]
        walls += lw
        for k in range(doors_per_level):
            d = nid("2")
            host = lw[k]
            length = 6000 if host in H.values() else 5000
            cmds.append(("door.place", {"id": d, "host": host, "offset": length / 2, "width": 900, "height": 2100}))
            if k % 3 == 0:
                cmds.append(("pset.set", {"id": d, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI30"}))
            doors.append(d)
    t = time.perf_counter()
    for i in range(0, len(cmds), 250):
        r = sess.batch([{"command": a, "args": b} for a, b in cmds[i:i + 250]])
        assert r["ok"], r
    return walls, doors, time.perf_counter() - t


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def stats(xs):
    return {"n": len(xs), "mean_ms": round(statistics.fmean(xs), 3), "p50_ms": round(pct(xs, 50), 3),
            "p95_ms": round(pct(xs, 95), 3), "p99_ms": round(pct(xs, 99), 3), "max_ms": round(max(xs), 3)}


def measure(work: Path, n_ops: int, synchronous: str, seed: int, validate_ref: bool) -> dict:
    path = work / f"bench-{synchronous}.dobim"
    path.unlink(missing_ok=True)
    sess = kit.Session(path, synchronous=synchronous)
    eng = sess.eng
    walls, doors, t_build = build(sess)
    counts = eng.g.q(Q.model_stats)
    # 첫 T0 상태는 이미 따뜻하다(생성 배치가 매번 당겼다). 그래프 노드 수를 기록한다.
    nodes = len(eng.g.nodes)
    rng = random.Random(seed)
    times, by_op, parts = [], {}, {"sync_ms": [], "apply_ms": [], "t0_ms": [], "commit_ms": []}
    recomputes = []
    skipped = 0
    new_n = 100000
    for i in range(n_ops):
        k = rng.random()
        if k < 0.35:
            name, args = "wall.move", {"id": rng.choice(walls), "dx": rng.randrange(-5, 6) * 100, "dy": rng.randrange(-5, 6) * 100}
        elif k < 0.55:
            name, args = "door.set", {"id": rng.choice(doors), rng.choice(["width", "sill", "height"]): rng.choice([800, 900, 1000])}
            if "sill" in args:
                args["sill"] = rng.choice([0, 50, 100])
        elif k < 0.70:
            name, args = "wall.set", {"id": rng.choice(walls), "thickness": rng.choice([150, 200, 250, 300])}
        elif k < 0.80:
            name, args = "pset.set", {"id": rng.choice(doors + walls), "pset": "Pset_WallCommon",
                                      "prop": "FireRating", "value": rng.choice(["EI30", "EI60", "EI90"])}
        elif k < 0.85:
            name, args = "material.set", {"id": rng.choice(walls), "name": rng.choice(["Concrete", "Steel", "Timber"])}
        elif k < 0.90:
            a, b = rng.sample(walls, 2)
            name, args = "join.set", {"a": a, "b": b}
            if eng.st.out.get(("on_level", a)) != eng.st.out.get(("on_level", b)):
                name, args = "wall.move", {"id": a, "dx": 100}
        elif k < 0.94:
            new_n += 1
            host = rng.choice(walls)
            name, args = "door.place", {"id": kit.mkid("2", new_n), "host": host, "offset": rng.choice([1500, 2000, 2500]), "width": 800}
        elif k < 0.97:
            name, args = "history.undo", {}
        else:
            name, args = "history.redo", {}
        rc0 = eng.g.recomputes
        t = time.perf_counter()
        r = sess.run(name, args)
        dt = (time.perf_counter() - t) * 1000
        recomputes.append(eng.g.recomputes - rc0)
        if not r["ok"]:
            if r["error"]["code"] in ("NOTHING_TO_UNDO", "NOTHING_TO_REDO", "CONSTRAINT", "NOT_FOUND"):
                skipped += 1   # 취소로 사라진 문을 고르는 등 정상적으로 거부된 시도는 시간에서 뺀다
                continue
            raise SystemExit(f"벤치 중 예상 밖 실패: {name} {args} {r['error']}")
        if name == "door.place":
            doors.append(args["id"])
        times.append(dt)
        by_op.setdefault(name, []).append(dt)
        for key in parts:
            parts[key].append(eng.timing.get(key, 0.0))
    res = {"synchronous": synchronous, "elements": counts, "graph_nodes_after_build": nodes,
           "build_sec": round(t_build, 1), "rejected_attempts_excluded": skipped, "T0": stats(times),
           "recomputed_nodes_per_command": {"mean": round(statistics.fmean(recomputes), 1), "p95": pct(recomputes, 95),
                                            "max": max(recomputes), "graph_nodes": nodes},
           "T0_by_command": {k: stats(v) for k, v in sorted(by_op.items())},
           "T0_breakdown_p95_ms": {k: round(pct(v, 95), 3) for k, v in parts.items()}}
    # 최악에 가까운 팬아웃: 벽 100개를 한 트랜잭션으로 옮긴다(조인 이웃·문·일람표 버킷이 한꺼번에 무효화)
    bt = []
    for _ in range(20):
        picks = rng.sample(walls, 100)
        t = time.perf_counter()
        r = sess.batch([{"command": "wall.move", "args": {"id": w, "dx": 100}} for w in picks])
        bt.append((time.perf_counter() - t) * 1000)
        assert r["ok"], r
    res_batch = {"batch_of_100_wall_moves_ms": {"p50": round(pct(bt, 50), 1), "max": round(max(bt), 1)}}
    # 측정 경로가 맞는 값을 냈는지: 증분 파생값 = 기준 그래프(캐시 없음)
    if validate_ref:
        t = time.perf_counter()
        res["incremental_equals_reference"] = eng.derived_hash() == eng.derived_hash(reference=True)
        res["reference_check_sec"] = round(time.perf_counter() - t, 1)
    res.update(res_batch)
    # 투영(T1 이후) 비용
    t = time.perf_counter()
    n_full = eng.flush_ifc()
    res["ifc_first_flush"] = {"projected": n_full, "sec": round(time.perf_counter() - t, 2)}
    w = rng.choice(walls)
    sess.run("wall.move", {"id": w, "dx": 300})
    t = time.perf_counter()
    n_inc = eng.flush_ifc()
    res["ifc_incremental_flush_after_one_wall_move"] = {"projected": n_inc, "ms": round((time.perf_counter() - t) * 1000, 1)}
    path_open = path
    sess.close()
    t = time.perf_counter()
    e2 = Engine(path_open, "open")
    res["open_project_sec"] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    e2.g.q(Q.t0_state)
    res["first_t0_after_open_sec"] = round(time.perf_counter() - t, 2)
    e2.close()
    res["log_entries"] = eng.rev
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261107)
    ap.add_argument("--out", default=None)
    ap.add_argument("--full-sync", action="store_true", help="synchronous=FULL 도 측정")
    ns = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="dobim-bench-"))
    out = {"definition": "T0 = 변경 커맨드 1개의 dispatch.run 전체 시간(검증+쓰기잠금+적용+무효화+T0 상태 전체 재계산+로그 쓰기+COMMIT). "
                         "IFC 형상 투영은 제외(별도 측정). 프로세스 하나, 따뜻한 상태, 5,000요소.",
           "target": "p95 <= 50 ms", "platform": platform.platform(), "python": platform.python_version(),
           "cpu": platform.processor(), "seed": ns.seed, "runs": []}
    out["runs"].append(measure(work, ns.ops, "NORMAL", ns.seed, True))
    if ns.full_sync:
        out["runs"].append(measure(work, ns.ops, "FULL", ns.seed, False))
    p95 = out["runs"][0]["T0"]["p95_ms"]
    out["pass_p95_50ms"] = all(r["T0"]["p95_ms"] <= 50 for r in out["runs"])
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if ns.out:
        Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if out["pass_p95_50ms"] else 1


if __name__ == "__main__":
    sys.exit(main())
