# SPDX-License-Identifier: MPL-2.0
"""S3 측정 실행기. 사용: python run_s3.py <cold|incremental|fuzz|mutants|scale|ifcopenshell|export|all>

결과는 results/*.json, 도면 산출물은 out/ 에 쓴다. 절대 경로·사용자명은 결과에 넣지 않는다.
"""
from __future__ import annotations

import json
import platform
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import s3lib  # noqa: E402
from dobim_core import drawing as D  # noqa: E402
from dobim_core import queries as Q  # noqa: E402
from dobim_core.engine import _Inputs  # noqa: E402
from dobim_core.graph import Graph, RefGraph  # noqa: E402

RES = HERE / "results"
OUT = HERE / "out"


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def stats(xs):
    return {"n": len(xs), "min": round(min(xs), 3), "p50": round(pct(xs, 50), 3), "p95": round(pct(xs, 95), 3),
            "max": round(max(xs), 3), "mean": round(statistics.mean(xs), 3)}


def env_info():
    return {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine()}


def save(name, obj):
    RES.mkdir(exist_ok=True)
    obj = {**obj, "env": env_info(), "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (RES / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return obj


def ra01(work):
    return s3lib.build(Path(work) / "ra01.dobim")


# ================================================================ A. 처음부터 생성 (캐시 비어 있음)
def cold(reps: int = 7):
    work = Path(tempfile.mkdtemp())
    sess, ids, views = ra01(work)
    eng = sess.eng
    per_view = {}
    for key, vid in views.items():
        ts = []
        for _ in range(reps):
            g = Graph(_Inputs(eng))
            t = time.perf_counter()
            lines = g.q(D.view_lines, vid)
            ts.append((time.perf_counter() - t) * 1e3)
        summ = D.view_summary(lines)
        per_view[key] = {"ms": stats(ts), "segments": summ["segments"], "by_class": summ["by_class"], "nodes": len(g.nodes),
                         "recomputes": g.recomputes}
    # 뷰 7개를 한 그래프에서 이어서 (벽 형상 같은 공유 노드는 한 번만 계산)
    ts = []
    for _ in range(reps):
        g = Graph(_Inputs(eng))
        t = time.perf_counter()
        for vid in views.values():
            g.q(D.view_lines, vid)
        ts.append((time.perf_counter() - t) * 1e3)
    plan_ms = [per_view[k]["ms"]["p50"] for k in ("plan-1F", "plan-2F")]
    elev_ms = [per_view[f"elev-{d}"]["ms"]["p50"] for d in "NESW"]
    sess.close()
    return save("s3_cold.json", {
        "model": "RA-01 (코어 범위: 레벨 2, 벽 14, 문 5)", "per_view_cold_ms": per_view,
        "all_7_views_one_graph_ms": stats(ts), "plans_2_ms_sum_p50": round(sum(plan_ms), 3),
        "section_1_ms_p50": per_view["section-BB"]["ms"]["p50"], "elevations_4_ms_sum_p50": round(sum(elev_ms), 3),
        "note": "각 뷰마다 빈 그래프에서 계산한 시간(벽 형상 계산 포함). 선분 병합까지 포함, SVG 쓰기 제외"})


# ================================================================ B. 벽 1개 이동 -> 증분 갱신
def incremental(n_moves: int = 40, seed: int = 20261109):
    work = Path(tempfile.mkdtemp())
    sess, ids, views = ra01(work)
    eng = sess.eng
    rng = random.Random(seed)
    walls = [v for k, v in ids.items() if k.startswith("W-")]
    vlist = list(views.items())
    snap = {k: eng.g.q(D.view_lines, v) for k, v in vlist}      # 캐시 데우기
    recs = []
    full_nodes = None
    for i in range(n_moves):
        w = walls[i % len(walls)] if i < len(walls) * 2 else rng.choice(walls)
        dx = rng.choice([-600.0, -200.0, 200.0, 600.0, 1000.0, 0.0])
        dy = rng.choice([-600.0, -200.0, 200.0, 600.0, 0.0]) if dx == 0.0 else rng.choice([0.0, 0.0, 200.0, -400.0])
        before = {k: eng.g.q(D.view_lines, v) for k, v in vlist}
        rec0 = eng.g.recomputes
        t0 = time.perf_counter()
        r = sess.run("wall.move", {"id": w, "dx": dx, "dy": dy})
        t_mut = (time.perf_counter() - t0) * 1e3
        assert r["ok"], r
        rev = r["rev"]
        stale_before = [k for k, v in vlist if D.view_cache_state(eng.g, [v])[v] != "fresh"]
        aff_api = sess.run("view.affected", {"rev": rev})["result"]
        aff_names = sorted(it["name"] for it in aff_api["items"])
        t1 = time.perf_counter()
        after, per_view_ms = {}, {}
        for k, v in vlist:
            t = time.perf_counter()
            after[k] = eng.g.q(D.view_lines, v)
            per_view_ms[k] = (time.perf_counter() - t) * 1e3
        t_refresh = (time.perf_counter() - t1) * 1e3
        nodes_inc = eng.g.recomputes - rec0
        changed = [k for k, _ in vlist if after[k] != before[k]]
        # 전체 재생성과 비교
        t2 = time.perf_counter()
        ref_g = RefGraph(_Inputs(eng))
        ref = {k: ref_g.q(D.view_lines, v) for k, v in vlist}
        t_full = (time.perf_counter() - t2) * 1e3
        matches = {k: s3lib.match(after[k], ref[k]) for k, _ in vlist}
        # 전체 재생성이 계산하는 노드 수(= 빈 그래프에서의 재계산 수)
        g0 = Graph(_Inputs(eng))
        t3 = time.perf_counter()
        for _, v in vlist:
            g0.q(D.view_lines, v)
        t_cold_graph = (time.perf_counter() - t3) * 1e3
        full_nodes = g0.recomputes
        # undo
        t4 = time.perf_counter()
        ru = sess.run("history.undo", {})
        assert ru["ok"], ru
        undone = {k: eng.g.q(D.view_lines, v) for k, v in vlist}
        t_undo = (time.perf_counter() - t4) * 1e3
        undo_exact = all(undone[k] == before[k] for k, _ in vlist)
        recs.append({"wall": w, "dx": dx, "dy": dy, "mutate_ms": t_mut, "refresh_all_views_ms": t_refresh,
                     "total_ms": t_mut + t_refresh, "max_single_view_ms": max(per_view_ms.values()),
                     "nodes_recomputed": nodes_inc, "views_stale_after_move": len(stale_before),
                     "views_changed": changed, "views_affected_api": aff_names,
                     "affected_api_covers_changed": all(any(n.startswith(k.split("-")[0]) or True for n in aff_names) for k in changed) and
                     set(vid_name(views, k) for k in changed) <= set(aff_names),
                     "full_regen_all_views_ms": t_full, "fresh_incremental_graph_all_views_ms": t_cold_graph,
                     "min_match_ratio": min(m["ratio"] for m in matches.values()),
                     "min_length_ratio": min(m["length_ratio"] for m in matches.values()),
                     "exact_equal_all_views": all(after[k] == ref[k] for k, _ in vlist),
                     "undo_ms": t_undo, "undo_restores_original_exact": undo_exact})
    sess.close()
    tot = [r["total_ms"] for r in recs]
    out = {
        "model": "RA-01 (코어 범위)", "views": [k for k, _ in vlist], "moves": len(recs),
        "criterion_update_within_2s": {"limit_ms": 2000, "max_total_ms": round(max(tot), 3), "pass": max(tot) <= 2000.0},
        "total_ms_mutate_plus_refresh_all_7_views": stats(tot),
        "mutate_ms": stats([r["mutate_ms"] for r in recs]),
        "refresh_all_views_ms": stats([r["refresh_all_views_ms"] for r in recs]),
        "max_single_view_refresh_ms": stats([r["max_single_view_ms"] for r in recs]),
        "full_regen_all_views_ms": stats([r["full_regen_all_views_ms"] for r in recs]),
        "fresh_incremental_graph_all_views_ms": stats([r["fresh_incremental_graph_all_views_ms"] for r in recs]),
        "speedup_vs_full_regen_p50": round(statistics.median([r["full_regen_all_views_ms"] / max(r["refresh_all_views_ms"], 1e-9) for r in recs]), 2),
        "nodes_recomputed_per_move": stats([r["nodes_recomputed"] for r in recs]),
        "nodes_in_full_regen": full_nodes,
        "views_changed_per_move": stats([len(r["views_changed"]) for r in recs]),
        "line_match": {"tolerance_mm": s3lib.TOL, "min_count_ratio_over_all_moves_and_views": min(r["min_match_ratio"] for r in recs),
                       "min_length_ratio": min(r["min_length_ratio"] for r in recs),
                       "moves_exactly_equal_in_all_views": sum(r["exact_equal_all_views"] for r in recs),
                       "pass_ge_99pct": min(r["min_match_ratio"] for r in recs) >= 0.99},
        "undo": {"moves_restored_exactly": sum(r["undo_restores_original_exact"] for r in recs), "of": len(recs),
                 "undo_plus_refresh_ms": stats([r["undo_ms"] for r in recs])},
        "affected_view_api": {"covers_all_changed_views_every_move": all(r["affected_api_covers_changed"] for r in recs),
                              "mean_views_reported": round(statistics.mean(len(r["views_affected_api"]) for r in recs), 2),
                              "mean_views_actually_changed": round(statistics.mean(len(r["views_changed"]) for r in recs), 2)},
        "samples": recs[:6]}
    return save("s3_incremental.json", out)


def vid_name(views, key):
    return {"plan-1F": "1F 평면", "plan-2F": "2F 평면", "section-BB": "B-B 단면", "elev-N": "북측 입면", "elev-E": "동측 입면",
            "elev-S": "남측 입면", "elev-W": "서측 입면"}[key]


# ================================================================ C. 임의 편집열 (증분 = 전체 재생성)
def fuzz(seeds=(11, 12, 13, 14), n=300):
    runs = []
    for s in seeds:
        t = time.perf_counter()
        r = s3lib.fuzz_views(Path(tempfile.mkdtemp()), s, n)
        r["seconds"] = round(time.perf_counter() - t, 1)
        runs.append(r)
    return save("s3_fuzz.json", {
        "what": "S1 임의 커맨드 생성기로 편집(이동·두께·문·조인·삭제·undo/redo)하며 매 스텝 모든 뷰의 증분 선분 == 기준 그래프(캐시 없음) 선분",
        "runs": runs, "total_checks": sum(r["checks"] for r in runs), "total_diffs": sum(r["diff_count"] for r in runs),
        "total_errors": sum(r["error_count"] for r in runs), "min_match_ratio": min(r["min_match_ratio"] for r in runs)})


# ================================================================ D. 음성 대조 (무효화를 일부러 빠뜨림)
class _Untracked:
    """의존성을 기록하지 않는 그래프 대역: 읽은 입력·질의가 노드의 의존성에 안 남는다 = 무효화 누락 변이."""

    def __init__(self, g):
        self.inputs = g.inputs
        self.ref = RefGraph(g.inputs)

    def input(self, key):
        return self.inputs.load(key)

    def q(self, fn, *args):
        return self.ref.q(fn, *args)


def untrack(name):
    orig = getattr(D, name)

    def wrapped(g, *a):
        return orig(_Untracked(g), *a)
    wrapped.__name__ = name
    return mock.patch.object(D, name, wrapped)


def _mutants():
    def stale_touch(skip):
        orig = Graph.touch

        def touch(self, key):
            if key[0] in skip:
                return
            return orig(self, key)
        return mock.patch.object(Graph, "touch", touch)

    def early_cutoff_bug():
        orig = Graph._recompute

        def rec(self, node):
            old, old_changed = node.value, node.changed
            orig(self, node)
            if node.key[0] is D.vnbrs and old is not None and old != node.value:
                node.changed = old_changed   # 값이 바뀌었는데 changed 를 올리지 않는다 (조기 차단 오류)
        return mock.patch.object(Graph, "_recompute", rec)

    return [
        ("M1 벽 형상(wall_solid)이 의존성을 기록하지 않음", lambda: untrack("wall_solid")),
        ("M2 개구부(wall_openings)가 문 변경을 구독하지 않음", lambda: untrack("wall_openings")),
        ("M3 입면·단면 이웃(vnbrs)이 다른 벽의 이동을 구독하지 않음", lambda: untrack("vnbrs")),
        ("M4 평면 이웃(plan_nbrs)이 다른 벽의 이동을 구독하지 않음", lambda: untrack("plan_nbrs")),
        ("M5 평면 조각(plan_pieces)이 뷰 변경(절단 높이)을 구독하지 않음", lambda: untrack("plan_pieces")),
        ("M6 코너 보정 범위(wall_range)가 조인 변경을 구독하지 않음", lambda: untrack("wall_range")),
        ("M7 그래프가 관계 입력(조인·호스팅·레벨) 변경을 표시하지 않음", lambda: stale_touch({"rel_out", "rel_in"})),
        ("M8 그래프가 요소 행 변경을 표시하지 않음", lambda: stale_touch({"el"})),
        ("M9 그래프가 일람표 버킷(벽 생성·삭제) 변경을 표시하지 않음", lambda: stale_touch({"bucket", "count"})),
        ("M10 이웃 변화가 전파되지 않음(조기 차단 오류)", early_cutoff_bug),
        ("M11 뷰 설정(view_cfg)이 뷰 편집을 구독하지 않음", lambda: untrack("view_cfg")),
    ]


def mutants(seeds=(21, 22), n=140):
    # 통제: 변이 없이도 같은 하네스가 깨끗해야 한다
    base = [s3lib.fuzz_views(Path(tempfile.mkdtemp()), s, n) for s in seeds]
    baseline = {"diffs": sum(r["diff_count"] for r in base), "errors": sum(r["error_count"] for r in base),
                "checks": sum(r["checks"] for r in base)}
    rows = []
    for name, make in _mutants():
        detected, first = False, None
        per_seed = []
        for s in seeds:
            with make():
                r = s3lib.fuzz_views(Path(tempfile.mkdtemp()), s, n)
            hit = r["diff_count"] + r["error_count"]
            per_seed.append({"seed": s, "diffs": r["diff_count"], "errors": r["error_count"], "min_match_ratio": r["min_match_ratio"],
                             "first_diff_step": (r["diffs"][0]["step"] if r["diffs"] else None)})
            if hit and not detected:
                detected = True
                first = per_seed[-1]
        rows.append({"mutant": name, "detected": detected, "first_detection": first, "per_seed": per_seed})
    caught = sum(r["detected"] for r in rows)
    return save("s3_mutants.json", {
        "what": "일부러 캐시 무효화를 빠뜨린 변이를 넣고, 같은 임의 편집열 검사가 불일치를 잡는지 본다",
        "baseline_without_mutation": baseline, "mutants": rows, "detected": caught, "total": len(rows)})


# ================================================================ E. 규모 (합성 격자)
def grid_model(path: Path, k: int):
    sess = s3lib.kit.Session(path, guid=s3lib.GUID)
    cmds = [{"command": "level.create", "args": {"id": s3lib._i("3", 1), "name": "1F", "elevation": 0.0}}]
    n = 0
    step = 5000.0
    lv = s3lib._i("3", 1)
    ws = []
    for j in range(k + 1):
        for i in range(k):
            n += 1
            wid = s3lib._i("1", n)
            ws.append(wid)
            cmds.append({"command": "wall.create", "args": {"id": wid, "level": lv, "axis": [i * step, j * step, (i + 1) * step, j * step], "thickness": 200.0, "height": 3000.0}})
    for i in range(k + 1):
        for j in range(k):
            n += 1
            wid = s3lib._i("1", n)
            ws.append(wid)
            cmds.append({"command": "wall.create", "args": {"id": wid, "level": lv, "axis": [i * step, j * step, i * step, (j + 1) * step], "thickness": 200.0, "height": 3000.0}})
    for q, wid in enumerate(ws[::4], 1):
        cmds.append({"command": "door.place", "args": {"id": s3lib._i("2", q), "host": wid, "offset": 2500.0, "width": 900.0, "height": 2100.0}})
    for a in range(0, len(cmds), 400):
        r = sess.batch(cmds[a:a + 400])
        assert r["ok"], r
    vc = [("view.create", {"id": s3lib._i("4", 1), "name": "평면", "type": "plan", "level": lv, "cut_height": 1200.0}),
          ("view.create", {"id": s3lib._i("4", 2), "name": "단면", "type": "section", "p1": [-1000.0, step * k / 2 + 1500], "p2": [step * k + 1000, step * k / 2 + 1500]}),
          ("view.create", {"id": s3lib._i("4", 3), "name": "남측", "type": "elevation", "dir": "S"})]
    assert sess.batch([{"command": a, "args": b} for a, b in vc])["ok"]
    return sess, ws, {"plan": s3lib._i("4", 1), "section": s3lib._i("4", 2), "elev-S": s3lib._i("4", 3)}


def scale(ks=(3, 6, 10), moves=12):
    rows = []
    for k in ks:
        work = Path(tempfile.mkdtemp())
        sess, ws, views = grid_model(work / "grid.dobim", k)
        eng = sess.eng
        n_walls = len(ws)
        t = time.perf_counter()
        g0 = Graph(_Inputs(eng))
        for v in views.values():
            g0.q(D.view_lines, v)
        cold_ms = (time.perf_counter() - t) * 1e3
        seg = {kk: len(g0.q(D.view_lines, v)) for kk, v in views.items()}
        for v in views.values():
            eng.g.q(D.view_lines, v)
        rng = random.Random(7)
        inc, full = [], []
        for _ in range(moves):
            w = rng.choice(ws)
            r = sess.run("wall.move", {"id": w, "dx": rng.choice([-300.0, 300.0, 700.0]), "dy": rng.choice([0.0, 300.0])})
            assert r["ok"]
            t = time.perf_counter()
            for v in views.values():
                eng.g.q(D.view_lines, v)
            inc.append((time.perf_counter() - t) * 1e3)
            t = time.perf_counter()
            rg = RefGraph(_Inputs(eng))
            for v in views.values():
                rg.q(D.view_lines, v)
            full.append((time.perf_counter() - t) * 1e3)
        sess.close()
        rows.append({"grid_k": k, "walls": n_walls, "doors": len(ws[::4]), "views": list(views), "segments": seg,
                     "cold_3_views_ms": round(cold_ms, 1), "incremental_refresh_ms": stats(inc), "full_regen_ms": stats(full),
                     "speedup_p50": round(statistics.median(f / max(i, 1e-9) for f, i in zip(full, inc)), 2)})
    return save("s3_scale.json", {"what": "합성 격자(벽 끝이 닿는 방 격자, 조인 없음) 규모 시험: 이동 1회 뒤 평면·단면·남측입면 3뷰 갱신", "rows": rows})


# ================================================================ F. ifcopenshell.draw 대조 (평면 절단선 커버리지)
def ifc_compare():
    import re
    import ifcopenshell
    import ifcopenshell.draw as idraw
    work = Path(tempfile.mkdtemp())
    sess, ids, views = ra01(work)
    eng = sess.eng
    eng.flush_ifc()
    ifc_path = work / "ra01.ifc"
    eng.ifc.write(str(ifc_path))
    f = ifcopenshell.open(str(ifc_path))
    out = {"model": "RA-01 코어 범위의 IFC 투영(IfcOpenShell 0.9 draw, HLR)", "ifc_entities": {"IfcWall": len(f.by_type("IfcWall")), "IfcDoor": len(f.by_type("IfcDoor"))},
           "ifcopenshell_draw": {}}
    for view in ("floorplan", "section", "elevation"):
        s = idraw.draw_settings(auto_floorplan=view == "floorplan", auto_section=view == "section", auto_elevation=view == "elevation",
                                width=420, height=297, scale=1 / 100, cells=False, include_projection=True, css="")
        ts = []
        svg = b""
        for _ in range(5):
            t = time.perf_counter()
            svg = idraw.main(s, files=[f])
            ts.append((time.perf_counter() - t) * 1e3)
        out["ifcopenshell_draw"][view] = {"ms": stats(ts), "bytes": len(svg), "paths": len(re.findall(rb"<path", svg))}
    # 평면 대조: 1F 평면의 절단선 vs ifcopenshell 1F 평면 경로 (미터->용지 mm 변환은 ifc:matrix3 을 읽어 되돌린다)
    s = idraw.draw_settings(auto_floorplan=True, width=420, height=297, scale=1 / 100, cells=False, include_projection=True, css="")
    svg = idraw.main(s, files=[f]).decode("utf-8")
    theirs = _ifc_svg_segments(svg, storey_name="1F")
    mine = [ln for ln in eng.g.q(D.view_lines, views["plan-1F"]) if ln[0] == "cut"]
    out["plan_1F_cross_check"] = _coverage(mine, theirs)
    # 단면·입면: 선분 대조 대신 범위만 (ifcopenshell 의 auto 뷰 방향 규칙이 달라 같은 뷰를 보장할 수 없다)
    sess.close()
    return save("s3_ifcopenshell_compare.json", out)


def _ifc_svg_segments(svg: str, storey_name: str):
    """ifcopenshell.draw 평면 SVG 에서 해당 층 그룹의 path 선분을 모델 mm 좌표로 되돌린다."""
    import re
    g = re.search(r'<g id="storey-[^"]*" class="IfcBuildingStorey" ifc:name="%s"[^>]*ifc:matrix3="(\[\[[^"]*\]\])"' % re.escape(storey_name), svg)
    m = json.loads(g.group(1))
    sx, ox, sy, oy = m[0][0], m[0][2], m[1][1], m[1][2]
    start = g.start()
    nxt = svg.find('class="IfcBuildingStorey"', g.end())
    body = svg[start: nxt if nxt > 0 else len(svg)]
    segs = []
    for pd in re.findall(r'<path[^>]* d="([^"]+)"', body):
        toks = re.findall(r"[MLZmlz]|-?\d+\.?\d*(?:e-?\d+)?", pd)
        pts, i, cur, first = [], 0, None, None
        while i < len(toks):
            t = toks[i]
            if t in "ML":
                x, y = float(toks[i + 1]), float(toks[i + 2])
                i += 3
                p = ((x - ox) / sx * 1000.0, -(y - oy) / sy * 1000.0)   # SVG 의 y 는 아래로 자란다
                if t == "M":
                    first = p
                elif cur is not None:
                    segs.append((cur, p))
                cur = p
            elif t in "Zz":
                if cur is not None and first is not None:
                    segs.append((cur, first))
                cur = first
                i += 1
            else:
                i += 1
    return segs


def _coverage(mine, theirs, tol=2.0):
    """내 절단선 길이 중 ifcopenshell 선에서 tol(mm) 이내인 비율과, 그 반대(ifcopenshell 선 중 내 선 근처). 선분을 1 mm 간격으로 표본."""
    def sample(segs, step=1.0):
        pts = []
        for a, b in segs:
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = (dx * dx + dy * dy) ** 0.5
            n = max(1, int(L / step))
            pts += [(a[0] + dx * i / n, a[1] + dy * i / n) for i in range(n + 1)]
        return pts

    def dist_to(p, segs):
        best = 1e18
        for a, b in segs:
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
            d = ((p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2) ** 0.5
            best = min(best, d)
        return best

    ms = [((l[1], l[2]), (l[3], l[4])) for l in mine]
    ps_mine, ps_theirs = sample(ms, 20.0), sample(theirs, 20.0)
    near_m = sum(dist_to(p, theirs) <= tol for p in ps_mine) / len(ps_mine)
    near_t = sum(dist_to(p, ms) <= tol for p in ps_theirs) / len(ps_theirs)
    return {"tolerance_mm": tol, "my_cut_segments": len(ms), "ifcopenshell_segments": len(theirs),
            "my_lines_within_tol_of_ifcopenshell": round(near_m, 4), "ifcopenshell_lines_within_tol_of_mine": round(near_t, 4),
            "note": "ifcopenshell 선은 벽별 윤곽(내부 이음매·문 본체 포함)이고 내 선은 합집합 윤곽이라 두 번째 값은 이음매만큼 낮다. 내 도면은 L 모서리를 채워 코어 투영의 노치와 모서리에서 다르다"}


# ================================================================ G. 산출물 (SVG·PDF)
def export():
    OUT.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp())
    sess, ids, views = ra01(work)
    sess.eng.workspace = work
    rows = []
    for k, v in views.items():
        for ext in ("svg", "pdf"):
            r = sess.run("view.export", {"id": v, "path": str(work / f"{k}.{ext}"), "overwrite": True})
            assert r["ok"], r
            shutil.copy(work / f"{k}.{ext}", OUT / f"RA-01-core_{k}.{ext}")
            rows.append({"view": k, "file": f"out/RA-01-core_{k}.{ext}", **{kk: r["result"][kk] for kk in ("bytes", "segments", "fills", "non_ascii_dropped")}})
    svg = (OUT / "RA-01-core_plan-1F.svg").read_text(encoding="utf-8")
    sess.close()
    return save("s3_export.json", {"files": rows, "svg_contains_korean_text": any(ord(c) > 127 for c in svg),
                                   "pdf_korean": "한글은 '?' 로 대체(글꼴 미내장, non_ascii_dropped=true)", "pdf_is_vector": True})


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    jobs = {"cold": cold, "incremental": incremental, "fuzz": fuzz, "mutants": mutants, "scale": scale, "ifcopenshell": ifc_compare,
            "export": export}
    for name in (list(jobs) if what == "all" else [what]):
        t = time.perf_counter()
        r = jobs[name]()
        print(f"[{name}] {time.perf_counter() - t:.1f}s", {k: v for k, v in r.items() if k in (
            "criterion_update_within_2s", "line_match", "undo", "total_diffs", "total_errors", "detected", "total", "baseline_without_mutation")})


if __name__ == "__main__":
    main()
