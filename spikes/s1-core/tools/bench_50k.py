# SPDX-License-Identifier: MPL-2.0
"""1단계 과업 ⑤: 합성 대형 모델(기본 5만 요소)에서 읽기 전용 뷰 1장 생성 시간.

목표(사용자 확정): 요소 5만 개 모델의 읽기 전용 뷰(평면) 1장 생성이 30초 이내.
읽기 전용 = 이미 만들어 저장한 프로젝트를 새 프로세스에서 열고(차가운 그래프), 뷰 하나를 내보낸다(view.export .svg).

모델(합성, 결정적, 시드 고정, 현장 자료 없음):
  레벨 L 개(층고 3,300). 레벨마다 g x g 방 격자(칸 6,000 x 5,000):
    벽 2g(g+1)(칸마다 한 토막, 두께 150~250 중 시드로 고름) + 칸 모서리 L 조인
    문 2g(g+1)(벽마다 하나, 위치·폭은 시드로 고름)
    공간(방) g^2 + 바닥 슬래브 1(건물 전체)
  레벨 수는 목표 요소 수 / 5,000 (최소 1), g 는 목표를 넘는 가장 작은 값.
뷰: 1층 평면, 건물 가운데를 가로지르는 단면(깊이는 건물 전체), 남측 입면.

사용:
  python tools/bench_50k.py build --elements 50000 --out 모델.dobim          (모델 만들기)
  python tools/bench_50k.py measure --model 모델.dobim --view plan          (새 프로세스에서 뷰 1장 내보내기 시간)
  python tools/bench_50k.py curve --sizes 1000,5000,10000,20000,50000 --dir 폴더 --out 결과.json
measure 는 단계별 시간(범위·이웃 판정·벽 선분·기타 요소 선분·병합·채움·문자·출력)을 나눠 적는다.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
import kit  # noqa: E402

CW, CH, FH = 6000.0, 5000.0, 3300.0   # 칸 가로·세로, 층고(mm)
V_PLAN, V_SEC, V_ELEV = kit.mkid("4", 1), kit.mkid("4", 2), kit.mkid("4", 3)
VIEWS = {"plan": V_PLAN, "section": V_SEC, "elevation": V_ELEV}


def per_level(g: int) -> int:
    """레벨 하나의 요소 수: 레벨 1 + 벽 2g(g+1) + 문 2g(g+1) + 공간 g^2 + 슬래브 1."""
    return 1 + 4 * g * (g + 1) + g * g + 1


def plan_size(target: int) -> tuple[int, int]:
    levels = max(1, round(target / 5000))
    g = 1
    while levels * per_level(g) < target:
        g += 1
    return levels, g


def commands(target: int, seed: int = 20261119):
    """(levels, g, 커맨드 목록). 같은 목표·시드면 같은 목록이다."""
    rng = random.Random(seed)
    levels, g = plan_size(target)
    n = {"1": 0, "2": 0, "3": 0, "5": 0, "6": 0}

    def nid(p):
        n[p] += 1
        return kit.mkid(p, n[p])

    cmds, lv1 = [], None
    for li in range(levels):
        lv = nid("3")
        lv1 = lv1 or lv
        cmds.append(("level.create", {"id": lv, "name": f"{li + 1}F", "elevation": li * FH}))
        cmds.append(("slab.create", {"id": nid("5"), "level": lv, "thickness": 200,
                                     "boundary": [[0, 0], [g * CW, 0], [g * CW, g * CH], [0, g * CH]]}))
        H, Vw = {}, {}
        for r in range(g + 1):
            for c in range(g):
                H[r, c] = nid("1")
                cmds.append(("wall.create", {"id": H[r, c], "level": lv, "axis": [c * CW, r * CH, (c + 1) * CW, r * CH],
                                             "thickness": rng.choice([150, 200, 250]), "height": FH - 200}))
        for c in range(g + 1):
            for r in range(g):
                Vw[r, c] = nid("1")
                cmds.append(("wall.create", {"id": Vw[r, c], "level": lv, "axis": [c * CW, r * CH, c * CW, (r + 1) * CH],
                                             "thickness": rng.choice([150, 200, 250]), "height": FH - 200}))
        for r in range(g):
            for c in range(g):
                cmds.append(("join.set", {"a": H[r, c], "b": Vw[r, c]}))
        for (r, c), w in sorted(H.items()):
            cmds.append(("door.place", {"id": nid("2"), "host": w, "offset": rng.choice([1500, 2000, 3000, 4000]),
                                        "width": rng.choice([800, 900, 1000]), "height": 2100}))
        for (r, c), w in sorted(Vw.items()):
            cmds.append(("door.place", {"id": nid("2"), "host": w, "offset": rng.choice([1200, 2500, 3500]),
                                        "width": rng.choice([800, 900]), "height": 2100}))
        for r in range(g):
            for c in range(g):
                x0, y0 = c * CW, r * CH
                cmds.append(("space.create", {"id": nid("6"), "level": lv, "name": f"{li + 1}{r:02d}{c:02d}",
                                              "boundary": [[x0, y0], [x0 + CW, y0], [x0 + CW, y0 + CH], [x0, y0 + CH]]}))
    W, Hh = g * CW, g * CH
    cmds.append(("view.create", {"id": V_PLAN, "name": "1F 평면", "type": "plan", "level": lv1, "cut_height": 1200}))
    cmds.append(("view.create", {"id": V_SEC, "name": "단면 A", "type": "section", "p1": [-1000, Hh / 2 + 2500],
                                 "p2": [W + 1000, Hh / 2 + 2500], "depth": Hh + 2000}))
    cmds.append(("view.create", {"id": V_ELEV, "name": "남측 입면", "type": "elevation", "dir": "S"}))
    return levels, g, cmds


def build(target: int, out: Path, chunk: int = 500, seed: int = 20261119) -> dict:
    if out.exists():
        out.unlink()
    for ext in ("-wal", "-shm"):
        p = Path(str(out) + ext)
        if p.exists():
            p.unlink()
    levels, g, cmds = commands(target, seed)
    sess = kit.Session(out)
    t = time.perf_counter()
    for i in range(0, len(cmds), chunk):
        r = sess.batch([{"command": a, "args": b} for a, b in cmds[i:i + chunk]])
        assert r["ok"], r.get("error")
    dt = time.perf_counter() - t
    st = sess.run("model.stats", {})
    sess.close()
    counts = st["result"] if st.get("ok") else None
    return {"target": target, "levels": levels, "grid": g, "commands": len(cmds), "build_sec": round(dt, 1), "stats": counts}


# ---------------------------------------------------------------- 측정
def _phase_timer(D):
    """drawing 모듈의 질의 함수를 감싸 단계별 누적 시간을 잰다(측정용, 바깥쪽 호출만 센다)."""
    groups = {
        "nbrs": ["plan_nbrs", "vnbrs", "slab_nbr_walls", "stair_nbr_walls", "_occ_index", "_view_occluders", "_wall_occluders", "_f_occ"],
        "wall_lines": ["plan_wall_lines", "vwall_lines", "plan_fire_lines", "plan_wall_glazing", "elev_finish"],
        "other_lines": ["fam_lines", "slab_lines", "stair_lines", "railing_lines", "elevator_lines", "roof_lines",
                        "solid_lines", "foreign_lines", "view_grid_lines", "site_sign_lines", "site_section_lines"],
        "scope": ["scope_walls", "scope_fams", "scope_slabs", "scope_stairs", "scope_railings", "scope_elevators",
                  "scope_roofs", "scope_solids", "scope_foreign", "scope_spaces"],
        "merge": ["merge_lines"],
        "fills": ["view_fills"],
        "labels": ["view_labels", "view_dims", "site_dims"],
    }
    acc = {k: 0.0 for k in groups}
    stack = []   # 자기 시간(self time): 안쪽에서 감싼 함수에 쓴 시간은 바깥 단계에서 뺀다
    orig = {}
    for grp, names in groups.items():
        for nm in names:
            f = getattr(D, nm, None)
            if f is None:
                continue
            orig[nm] = f

            def wrap(*a, _f=f, _g=grp, **k):
                stack.append(0.0)
                t = time.perf_counter()
                try:
                    return _f(*a, **k)
                finally:
                    dt = time.perf_counter() - t
                    child = stack.pop()
                    acc[_g] += dt - child
                    if stack:
                        stack[-1] += dt
            setattr(D, nm, wrap)
    return acc, orig


def measure(model: Path, view: str, out_svg: Path | None = None, phases: bool = True) -> dict:
    """새 엔진으로 모델을 열고 뷰 하나를 .svg 로 내보낸다. 단계 시간은 phases 가 참일 때만(감싸기 비용이 조금 있다)."""
    from dobim_core import drawing as D
    acc = None
    if phases:
        acc, _ = _phase_timer(D)
    t0 = time.perf_counter()
    sess = kit.Session(model, create=False)
    t_open = time.perf_counter() - t0
    out_svg = out_svg or model.with_name(model.stem + f"-{view}.svg")
    if out_svg.exists():
        out_svg.unlink()
    t1 = time.perf_counter()
    lines = sess.eng.g.q(D.view_lines, VIEWS[view])
    t_lines = time.perf_counter() - t1
    t2 = time.perf_counter()
    args = {"id": VIEWS[view], "path": out_svg.name}
    r = sess.run("view.export", args)   # 되돌릴 수 없는 커맨드: 확인 토큰을 받아 다시 부른다(두 번의 시간을 모두 센다)
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = sess.run("view.export", args, confirm=r["error"]["details"]["confirm_token"])
    t_export = time.perf_counter() - t2
    assert r["ok"], r.get("error")
    sess.close()
    total = time.perf_counter() - t0
    res = {"view": view, "open_sec": round(t_open, 3), "view_lines_sec": round(t_lines, 3),
           "export_rest_sec": round(t_export, 3), "total_sec": round(total, 3), "lines": len(lines),
           "svg_bytes": out_svg.stat().st_size}
    if acc is not None:
        res["phases_sec"] = {k: round(v, 3) for k, v in acc.items()}
    return res


def _sub(args: list[str]) -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    p = subprocess.run([sys.executable, str(Path(__file__).resolve()), *args], capture_output=True, text=True,
                       encoding="utf-8", env=env)
    if p.returncode != 0:
        raise SystemExit(p.stdout + p.stderr)
    return json.loads(p.stdout.strip().splitlines()[-1])


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--elements", type=int, default=50000)
    b.add_argument("--out", type=Path, required=True)
    m = sp.add_parser("measure")
    m.add_argument("--model", type=Path, required=True)
    m.add_argument("--view", choices=list(VIEWS), default="plan")
    m.add_argument("--no-phases", action="store_true")
    c = sp.add_parser("curve")
    c.add_argument("--sizes", default="1000,5000,10000,20000,50000")
    c.add_argument("--views", default="plan,section,elevation")
    c.add_argument("--dir", type=Path, required=True)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--reuse", action="store_true", help="이미 있는 모델 파일을 다시 쓴다")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        print(json.dumps(build(a.elements, a.out), ensure_ascii=False))
    elif a.cmd == "measure":
        print(json.dumps(measure(a.model, a.view, phases=not a.no_phases), ensure_ascii=False))
    else:
        a.dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for n in [int(x) for x in a.sizes.split(",")]:
            mp = a.dir / f"synth-{n}.dobim"
            info = _sub(["build", "--elements", str(n), "--out", str(mp)]) if not (a.reuse and mp.exists()) else {"target": n, "reused": True}
            row = {"build": info}
            for v in a.views.split(","):
                row[v] = _sub(["measure", "--model", str(mp), "--view", v])
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), file=sys.stderr)
        res = {"machine": platform.processor() or platform.machine(), "python": platform.python_version(), "rows": rows}
        a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
