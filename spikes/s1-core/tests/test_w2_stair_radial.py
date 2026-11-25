# SPDX-License-Identifier: MPL-2.0
"""W2 열일곱 번째 PR: 방사(원형·나선) 계단(ARC-29)과 승강기 회전(ARC-33), 방사 계단 난간 연동.

성능 재확인 v2 에서 원형 계단·회전한 승강기를 만들 수 없어 직선 계단 조각·축 정렬 승강기로 대신했다.
1. 재현: 이전에는 걸음선(run) 없는 계단을 만들 수 없었고 승강기는 회전 대상이 아니었다 -> stair.spiral, elevator rotation
2. 방사 계단 형상: 중심·안쪽/바깥 반지름·시작 각·방향·총 회전각(또는 디딤판 각), 디딤판은 같은 각의 볼록 각기둥, 계단 검사 경고(보행선 기준)
3. 도면: 평면(절단 아래 디딤판·절단선·진행 화살표), 단면 절단, 입면, 치수, 증분 = 전체, 수량(단 수·체적)
4. 편집: stair.set(형식 바꾸기 포함)·move·element.rotate·mirror·되돌리기·저장 왕복, 난간(railing.from_stair)이 따라간다
5. IFC: IfcStair(SPIRAL_STAIR / CURVED_RUN_STAIR) + IfcStairFlight(SPIRAL), IFC4·IFC2X3 검증 오류 0, 가져오기 왕복 해시·바이트
6. 승강기 회전: 만들기·고치기·평면 기호·단면·치수·슬래브 구멍·element.rotate·mirror(문 면)·IFC 카 몸체·왕복
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import math
import sys

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import commands as C
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core.ifc_model import part_guid

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

ST = F.ST1
EL = F.EL1
VP = "4" + "0" * 19 + "p1"     # 1F 평면(절단 1200)
VS = "4" + "0" * 19 + "s1"     # 계단 중심을 지나는 단면(x = 10000, 동쪽을 본다)
VE = "4" + "0" * 19 + "e1"     # 남측 입면
C0 = [10000, 6000]
SPIRAL = {"center": C0, "radius_in": 300, "radius_out": 1500, "start": 0, "direction": "ccw", "sweep": 360, "height": 3000, "risers": 18}


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def g_of(s):
    s.eng.sync()
    s.eng._touch_pending()
    return s.eng.g


def params(s, eid):
    return ok(s.run("element.get", {"id": eid}))["params"]


def erow(s, e):
    from dobim_core import schedules as SC
    return g_of(s).q(SC.element_row, e)


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [x["message"][:160] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def views(s):
    ok(s.run("view.create", {"id": VP, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [10000, 3000], "p2": [10000, 9000]}))
    ok(s.run("view.create", {"id": VE, "name": "남", "type": "elevation", "dir": "S"}))


def same_as_full(s, vs=(VP, VS, VE)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in vs:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)


def area(poly):
    return abs(K.polygon_area2([list(q) for q in poly])) / 2.0


def spiral(s, **kw):
    return ok(s.run("stair.spiral", {"id": ST, "level": F.LV1, **dict(SPIRAL, **kw)}))


# ---------------------------------------------------------------- 1. 재현
def test_1_재현_걸음선_없는_계단과_승강기_회전(base):
    # 이전: 계단은 걸음선(run) 필수(직선·꺾인 계단만), 승강기는 회전 편집 대상이 아니었다
    assert err(base.run("stair.create", {"level": F.LV1, "center": C0, "width": 1200}))["code"] == "SCHEMA_INVALID"
    r = spiral(base)
    p = params(base, ST)
    assert r["id"] == ST and r["sweep"] == 360.0 and "run" not in p and "turn" not in p
    assert p["spiral"] == {"center": [10000.0, 6000.0], "r_in": 300.0, "start": 0.0, "sweep": 360.0} and p["width"] == 1200.0
    # 계단 검사(경고): 챌판 166.7mm 는 통과, 보행선(안쪽 끝 + 300 = 반지름 600) 위 디딤 600 x 21.18도 = 221.8mm < 260mm
    assert [w["code"] for w in r["warnings"]] == ["STAIR_TREAD"] and r["warnings"][0]["value"] == pytest.approx(600 * math.radians(360 / 17), abs=1e-3)
    # 거절: 바깥 <= 안쪽, sweep·step_angle 둘 다 또는 둘 다 없음, 디딤판 각 > 90, 총 회전각 > 720
    assert err(base.run("stair.spiral", dict(SPIRAL, level=F.LV1, radius_out=300)))["code"] == "SCHEMA_INVALID"
    assert err(base.run("stair.spiral", dict(SPIRAL, level=F.LV1, step_angle=20)))["code"] == "SCHEMA_INVALID"
    assert err(base.run("stair.spiral", {k: v for k, v in SPIRAL.items() if k != "sweep"} | {"level": F.LV1}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("stair.spiral", dict(SPIRAL, level=F.LV1, sweep=270, risers=3)))["code"] == "CONSTRAINT"
    e = err(base.run("stair.spiral", {k: v for k, v in SPIRAL.items() if k != "sweep"} | {"level": F.LV1, "step_angle": 60, "risers": 18}))
    assert e["code"] == "CONSTRAINT" and "720" in e["message"]
    # 디딤판 각으로 정의 + 시계 방향 + 시작 각 맞춤(-90 -> 270)
    r2 = ok(base.run("stair.spiral", {k: v for k, v in SPIRAL.items() if k != "sweep"} | {"level": F.LV1, "step_angle": 15, "direction": "cw", "start": -90}))
    q = params(base, r2["id"])["spiral"]
    assert q["sweep"] == -255.0 and q["start"] == 270.0
    # 직선 계단의 행은 그대로다(새 키 없음)
    ok(base.run("stair.create", {"id": ST[:-1] + "9", "level": F.LV1, "run": [0, 8000, 4000, 8000]}))
    assert set(params(base, ST[:-1] + "9")) == {"name", "run", "width", "height", "risers", "base_offset"}
    # 승강기: 회전 인자. 이전에는 element.rotate 가 승강기를 거절했다
    ok(base.run("elevator.create", {"id": EL, "level": F.LV1, "shaft": [2000, 7000, 4000, 9500], "rise": 6000, "rotation": 30}))
    assert params(base, EL)["rotation"] == 30.0
    ok(base.run("elevator.create", {"id": EL[:-1] + "9", "level": F.LV1, "shaft": [20000, 0, 22000, 2000], "rise": 6000, "rotation": 360}))
    assert "rotation" not in params(base, EL[:-1] + "9")                               # 0(360 의 배수)이면 키가 없다


# ---------------------------------------------------------------- 2. 형상·수량
def test_2_방사_계단_형상과_수량(base):
    spiral(base)
    p = params(base, ST)
    sl = K.stair_spiral_layout(p)
    tr = sl["treads"]
    assert len(tr) == 17 and tr[-1]["z"] == pytest.approx(3000 - 3000 / 18) and tr[0]["zb"] == 0.0
    assert tr[5]["zb"] == pytest.approx(5 * 3000 / 18 - K.STAIR_SPIRAL_T)
    for t in tr:   # 디딤판은 반시계 볼록 다각형, 안쪽 끝은 반지름 300, 바깥은 1500
        poly = t["poly"]
        n = len(poly)
        assert K.polygon_area2([list(q) for q in poly]) > 0
        for i in range(n):
            a, b, c = poly[i], poly[(i + 1) % n], poly[(i + 2) % n]
            assert (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) >= -1e-6
        rs = sorted(round(math.hypot(q[0] - C0[0], q[1] - C0[1]), 6) for q in poly)
        assert rs[0] == 300.0 and rs[-1] == 1500.0
    # 디딤판 넓이 합 = 고리 넓이(현 근사: 바깥 원호는 11.25도 이하 현, 안쪽은 디딤판마다 현 하나)
    ring = math.pi * (1500 ** 2 - 300 ** 2)
    assert sum(area(t["poly"]) for t in tr) == pytest.approx(ring, rel=0.01)
    row = erow(base, ST)
    assert row["risers"] == 18 and row["tread"] == pytest.approx(sl["tread"]) and row["length"] == pytest.approx(600 * 2 * math.pi)
    assert row["volume"] == pytest.approx(K.stair_volume(p)) and row["volume"] > 0
    der = ok(base.run("element.get", {"id": ST}))["derived"]
    assert der["treads"] == 17 and der["step_angle"] == pytest.approx(360 / 17) and der["walkline_radius"] == 600.0 and der["warnings"][0]["code"] == "STAIR_TREAD"
    # 직선 계단 체적 = 옆모습 톱니 넓이 x 폭
    ok(base.run("stair.create", {"id": ST[:-1] + "9", "level": F.LV1, "run": [0, 8000, 3400, 8000], "width": 1000, "height": 1800, "risers": 10}))
    td, rs = 3400 / 9, 180
    assert erow(base, ST[:-1] + "9")["volume"] == pytest.approx(sum(td * (k + 1) * rs for k in range(9)) * 1000)
    # 안쪽 반지름 0: 디딤판이 중심 한 점에서 퍼진다(삼각형 + 바깥 현)
    ok(base.run("stair.set", {"id": ST, "spiral": {"center": C0, "radius_in": 0, "radius_out": 1000, "sweep": 270}}))
    p = params(base, ST)
    assert p["width"] == 1000.0 and all(tuple(t["poly"][0]) == (10000.0, 6000.0) or (10000.0, 6000.0) in [tuple(q) for q in t["poly"]]
                                        for t in K.stair_spiral_layout(p)["treads"])


# ---------------------------------------------------------------- 3. 도면
def test_3_방사_계단_도면(base):
    views(base)
    spiral(base)
    g = g_of(base)
    segs, fills = g.q(D.plan_stair_parts, VP, ST)
    # 절단 1200: 윗면 <= 1200 인 디딤판 7개(7 x 166.7 = 1166.7)의 윤곽, 절단선 하나, 폭 가운데 원호 화살표
    assert fills == () and sum(1 for x in segs if x[0] == "cut") == 1
    sg = g.q(D.stair_spiral, ST)
    vis_pts = {(round(q[0], 3), round(q[1], 3)) for t in sg[0][:7] for q in t[0]}
    proj_pts = {(round(x[1], 3), round(x[2], 3)) for x in segs if x[0] == "proj"} | {(round(x[3], 3), round(x[4], 3)) for x in segs if x[0] == "proj"}
    assert proj_pts == vis_pts
    sym = [x for x in segs if x[0] == "symbol"]
    assert len(sym) >= 4 and all(math.hypot(x[1] - C0[0], x[2] - C0[1]) == pytest.approx(900, abs=1e-6) for x in sym[:-2])   # 화살표는 폭 가운데(반지름 900) 원호
    # 절단이 계단 위면 모든 디딤판, 절단선 없음
    ok(base.run("view.set", {"id": VP, "cut_height": 3500}))
    segs2 = g_of(base).q(D.plan_stair_parts, VP, ST)[0]
    assert not any(x[0] == "cut" for x in segs2) and sum(1 for x in segs2 if x[0] == "proj") > sum(1 for x in segs if x[0] == "proj")
    ok(base.run("view.set", {"id": VP, "cut_height": 1200}))
    g = g_of(base)
    # 단면(중심을 지나는 x = 10000): 절단면과 만나는 디딤판마다 절단 사각형, 절단선
    rects = g.q(D.stair_cut_rects, VS, ST)
    assert len(rects) >= 2 and any(x[0] == "cut" for x in g.q(D.stair_lines, VS, ST))
    assert max(r[2][1] for r in rects) <= 3000 + 1e-6
    # 입면: 디딤판 모서리가 투영선으로(바깥 원호 현 꼭짓점의 수직선은 없다)
    el = g.q(D.stair_lines, VE, ST)
    assert el and all(x[0] == "proj" for x in el)
    ve = g.q(D.stair_vedges, VE, ST)
    verts = [e for e in ve if abs(e[0][0] - e[1][0]) < 1e-9 and abs(e[0][2] - e[1][2]) > 1e-9]
    assert len(verts) == 17 * 4
    same_as_full(base)
    # 시계 방향 계단도 같은 수의 선
    ok(base.run("stair.set", {"id": ST, "spiral": {"center": C0, "radius_in": 300, "radius_out": 1500, "sweep": 360, "direction": "cw"}}))
    segs3 = g_of(base).q(D.plan_stair_parts, VP, ST)[0]
    assert sum(1 for x in segs3 if x[0] == "cut") == 1 and len(segs3) == len(segs)
    same_as_full(base)


def test_3_치수_옵션_평면(base):
    spiral(base)
    ok(base.run("view.create", {"id": VP, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200, "dims": True}))
    g = g_of(base)
    dims = g.q(D.view_dims, VP)
    w = [d for d in dims if d[1].endswith(":width:0")]
    assert len(w) == 1 and w[0][5] == "1200"
    labels = g.q(D.view_dim_labels, VP)
    assert any("18단" in x[2] for x in labels)
    # 상세 뷰 범위(around): 바깥 반지름 상자
    assert D.detail_extent(g, ST, "plan", None) == pytest.approx((8500.0, 4500.0, 11500.0, 7500.0), abs=10)   # 바깥 원호는 현 꼭짓점
    # 태그: 폭 가운데 원 위 가운데 각(180도)의 점
    ok(base.run("annot.tag.create", {"view": VP, "target": ST, "fields": ["name", "risers"]}))
    from dobim_core import annot as AN
    assert AN.tag_anchor(g_of(base), ST) == pytest.approx((10000 - 900, 6000), abs=1e-6)


# ---------------------------------------------------------------- 4. 편집·난간
def test_4_편집_형식_바꾸기_이동_회전_대칭_저장(base):
    spiral(base)
    h0 = base.eng.state_hash()
    r = ok(base.run("stair.set", {"id": ST, "risers": 20, "width": 1000}))
    p = params(base, ST)
    assert p["risers"] == 20 and p["width"] == 1000.0 and p["spiral"]["r_in"] == 300.0 and "warnings" in r
    assert err(base.run("stair.set", {"id": ST, "turn": {"type": "L", "risers_a": 8}}))["code"] == "CONSTRAINT"
    assert err(base.run("stair.set", {"id": ST, "risers": 3}))["code"] == "CONSTRAINT"           # 디딤판 각 180 > 90
    m = ok(base.run("stair.move", {"id": ST, "dx": 1000, "dy": -500}))
    assert m["center"] == [11000.0, 5500.0]
    ok(base.run("element.rotate", {"ids": [ST], "center": [0, 0], "angle": 90}))
    q = params(base, ST)["spiral"]
    assert q["center"] == [-5500.0, 11000.0] and q["start"] == 90.0 and q["sweep"] == 360.0
    ok(base.run("element.mirror", {"ids": [ST], "axis": [0, 0, 0, 1000]}))                       # 대칭: 회전 방향이 바뀐다
    q = params(base, ST)["spiral"]
    assert q["center"] == [5500.0, 11000.0] and q["start"] == 90.0 and q["sweep"] == -360.0
    ok(base.run("element.copy", {"ids": [ST], "dx": 5000, "dy": 0, "new_ids": [ST[:-1] + "8"]}))
    assert params(base, ST[:-1] + "8")["spiral"]["center"] == [10500.0, 11000.0]
    # 형식 바꾸기: 걸음선을 주면 직선, spiral 을 주면 다시 방사
    ok(base.run("stair.set", {"id": ST, "run": [0, 0, 4000, 0]}))
    p = params(base, ST)
    assert "spiral" not in p and p["run"] == [0.0, 0.0, 4000.0, 0.0]
    ok(base.run("stair.set", {"id": ST, "spiral": {"center": C0, "radius_out": 1200, "step_angle": 18}}))
    p = params(base, ST)
    assert "run" not in p and p["spiral"]["sweep"] == 18.0 * 19 and p["width"] == 1200.0
    for _ in range(7):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0
    ok(base.run("history.redo", {}))
    h1 = base.eng.state_hash()
    base.close()
    s2 = kit.Session(base.path, create=False)
    try:
        assert s2.eng.state_hash() == h1 and params(s2, ST)["risers"] == 20
        ok(s2.run("stair.delete", {"id": ST}))
        ok(s2.run("history.undo", {}))
        assert "spiral" in params(s2, ST)
    finally:
        s2.close()


def test_4_방사_계단_난간_연동(base):
    spiral(base)
    r = ok(base.run("railing.from_stair", {"stair": ST, "side": "both", "inset": 50}))
    assert len(r["created"]) == 2
    rl = {params(base, x)["src_tag"]: params(base, x) for x in r["created"]}
    assert set(rl) == {"sL", "sR"}
    inner, outer = rl["sL"], rl["sR"]   # 반시계로 오르면 왼쪽이 안쪽
    assert all(math.hypot(q[0] - C0[0], q[1] - C0[1]) == pytest.approx(350, abs=1e-6) for q in inner["path"])
    assert all(math.hypot(q[0] - C0[0], q[1] - C0[1]) == pytest.approx(1450, abs=1e-6) for q in outer["path"])
    assert len(outer["path"]) == 33 and outer["base_offset"] == pytest.approx(3000 / 18) and outer["rise"] == pytest.approx(17 * 3000 / 18)   # 현 32(11.25도)
    # 계단을 옮기면 난간이 따라간다
    m = ok(base.run("stair.move", {"id": ST, "dx": 500, "dy": 0}))
    assert sorted(m["railings"]["moved"]) == sorted(r["created"])
    assert params(base, r["created"][1])["path"][0][0] == pytest.approx(outer["path"][0][0] + 500)
    ok(base.run("element.rotate", {"ids": [ST], "center": [0, 0], "angle": 45}))
    p = params(base, ST)
    exp = {s["tag"]: s for s in C._stair_rail_specs(p, "both", 50.0, True)}
    for x in r["created"]:   # 회전한 계단에서 다시 만든 난간선과 같다(사용자가 고치지 않은 난간은 따라간다)
        q = params(base, x)
        assert [c for pt in q["path"] for c in pt] == pytest.approx([c for pt in exp[q["src_tag"]]["path"] for c in pt], abs=1e-3)
    # 안쪽 반지름 0 이면 안쪽 난간은 없다
    ok(base.run("stair.spiral", dict(SPIRAL, id=ST[:-1] + "9", level=F.LV1, radius_in=0)))
    r2 = ok(base.run("railing.from_stair", {"stair": ST[:-1] + "9"}))
    assert [params(base, x)["src_tag"] for x in r2["created"]] == ["sR"]


# ---------------------------------------------------------------- 5. IFC
def test_5_방사_계단과_회전_승강기_IFC_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("stair.spiral", {"id": ST, "level": F.LV1, **SPIRAL}))
        ok(b.run("stair.spiral", {"id": ST[:-1] + "9", "level": F.LV1, **dict(SPIRAL, center=[20000, 6000], radius_in=3000, radius_out=4200, sweep=90, risers=8,
                                                                                  height=1200, direction="cw")}))
        ok(b.run("railing.from_stair", {"stair": ST}))
        ok(b.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": [[0, 6000], [8000, 6000], [8000, 12000], [0, 12000]], "thickness": 200}))
        ok(b.run("elevator.create", {"id": EL, "level": F.LV1, "shaft": [2000, 7000, 4000, 9500], "rise": 6000, "rotation": 30,
                                     "doors": {"face": "S", "w": 900, "h": 2100, "levels": [F.LV1, F.LV2]}}))
        ok(b.run("elevator.cut_slabs", {"id": EL, "clearance": 50}))
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        sts = {e.GlobalId: e for e in f.by_type("IfcStair")}
        names = {ok(b.run("element.get", {"id": x}))["params"]["name"]: x for x in (ST, ST[:-1] + "9")}
        by = {names[e.Name]: e for e in sts.values() if e.Name in names}
        assert by[ST].PredefinedType == "SPIRAL_STAIR" and by[ST[:-1] + "9"].PredefinedType == "CURVED_RUN_STAIR"
        assert by[ST].Representation is None
        fl = [o for r in by[ST].IsDecomposedBy for o in r.RelatedObjects]
        assert len(fl) == 1 and fl[0].is_a("IfcStairFlight") and fl[0].PredefinedType == "SPIRAL" and fl[0].GlobalId == part_guid(ST, "spiral")
        assert (fl[0].NumberOfRisers, fl[0].NumberOfTreads) == (18, 17) and fl[0].RiserHeight == pytest.approx(3000 / 18)
        assert len(fl[0].Representation.Representations[0].Items) == 17
        ev = next(e for e in f.by_type("IfcTransportElement"))
        pts = ev.Representation.Representations[0].Items[0].SweptArea.OuterCurve.Points
        cab = [tuple(p.Coordinates) for p in pts][:4]
        p = ok(b.run("element.get", {"id": EL}))["params"]
        exp = K.elevator_ring(p, K.elevator_geom(p)["cab"])
        assert [pytest.approx(q) for q in exp] == [tuple(c) for c in cab]
        assert file_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "t3.ifc"))
        assert file_errors(f3) == [] and {e.ShapeType for e in f3.by_type("IfcStair")} >= {"SPIRAL_STAIR", "CURVED_RUN_STAIR"}
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        assert ok(b.run("element.get", {"id": EL}, project="imp.dobim"))["params"]["rotation"] == 30.0
        # 직선 계단으로 바꾸면 단 묶음 부속이 사라지고 계단 몸체가 생긴다(검증 오류 0), 되돌리면 같다
        ok(b.run("stair.set", {"id": ST, "run": [9000, 4000, 13000, 4000]}))
        f = ifcopenshell.open(str(b.export("t4.ifc")))
        st = next(e for e in f.by_type("IfcStair") if e.GlobalId == by[ST].GlobalId)
        assert st.Representation is not None and not [o for r in st.IsDecomposedBy for o in r.RelatedObjects] and file_errors(f) == []
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()


# ---------------------------------------------------------------- 6. 승강기 회전
def _elev(s, rot=30.0, **kw):
    ok(s.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": [[0, 5000], [8000, 5000], [8000, 12000], [0, 12000]], "thickness": 200}))
    ok(s.run("elevator.create", {"id": EL, "level": F.LV1, "shaft": [2000, 7000, 4000, 9500], "rise": 6000, "rotation": rot,
                                 "doors": {"face": "S", "w": 900, "h": 2100, "levels": [F.LV1]}, **kw}))


def test_6_승강기_회전_도면과_구멍(base):
    views(base)
    _elev(base)
    g = g_of(base)
    p = params(base, EL)
    shaft = K.elevator_ring(p, p["shaft"])
    segs = g.q(D.elevator_lines, VP, EL)
    pts = {(round(x[1], 6), round(x[2], 6)) for x in segs if x[0] == "proj"}
    for q in shaft:   # 샤프트 꼭짓점이 돌았다(축 정렬 꼭짓점은 없다)
        assert (round(q[0], 6), round(q[1], 6)) in pts
    assert (2000.0, 7000.0) not in pts
    sym = [x for x in segs if x[0] == "symbol"]
    cab = K.elevator_ring(p, K.elevator_geom(p)["cab"])
    assert (sym[-2][1], sym[-2][2]) == pytest.approx(cab[0]) and (sym[-2][3], sym[-2][4]) == pytest.approx(cab[2])   # 카의 대각 X
    assert g.q(D.elevator_lines, VS, EL) is not None
    # 슬래브 구멍: 돈 샤프트 + 여유 50
    r = ok(base.run("elevator.cut_slabs", {"id": EL, "clearance": 50}))
    assert r["cut"] == [F.S1]
    hole = params(base, F.S1)["openings"][0]
    exp = K.elevator_ring(p, (1950, 6950, 4050, 9550))
    assert sorted(map(tuple, hole)) == sorted((round(x, 6), round(y, 6)) for x, y in exp)
    # 회전 고치기: 구멍이 따라간다, 0 이면 키가 사라지고 구멍은 축 정렬
    ok(base.run("elevator.set", {"id": EL, "rotation": 45}))
    p = params(base, EL)
    assert p["rotation"] == 45.0
    hole = params(base, F.S1)["openings"][0]
    assert sorted(map(tuple, hole)) == sorted((round(x, 6), round(y, 6)) for x, y in K.elevator_ring(p, (1950, 6950, 4050, 9550)))
    ok(base.run("elevator.set", {"id": EL, "rotation": 0}))
    assert "rotation" not in params(base, EL)
    assert sorted(map(tuple, params(base, F.S1)["openings"][0])) == sorted([(1950.0, 6950.0), (4050.0, 6950.0), (4050.0, 9550.0), (1950.0, 9550.0)])
    same_as_full(base)


def test_6_승강기_회전_대칭_편집(base):
    _elev(base, rot=0.0)
    ok(base.run("elevator.cut_slabs", {"id": EL}))
    h0 = base.eng.state_hash()
    # 원점 둘레 90도: 슬래브는 돌지 않으므로 따라가는 구멍이 슬래브 경계 밖 -> 거절(아무것도 바뀌지 않는다)
    e = err(base.run("element.rotate", {"ids": [EL], "center": [0, 0], "angle": 90}))
    assert e["code"] == "CONSTRAINT" and "구멍" in e["message"] and base.eng.state_hash() == h0
    # 구멍 없는 승강기는 원점 둘레로 돈다: 가운데 (3000, 8250) -> (-8250, 3000), 크기 그대로, 회전각 90
    ok(base.run("elevator.create", {"id": EL[:-1] + "9", "level": F.LV1, "shaft": [2000, 7000, 4000, 9500], "rise": 3000}))
    ok(base.run("element.rotate", {"ids": [EL[:-1] + "9"], "center": [0, 0], "angle": 90}))
    p = params(base, EL[:-1] + "9")
    assert p["shaft"] == [-9250.0, 1750.0, -7250.0, 4250.0] and p["rotation"] == 90.0
    ring = K.elevator_ring(p, p["shaft"])
    assert {(round(x, 6) + 0.0, round(y, 6) + 0.0) for x, y in ring} == {(-9500.0, 2000.0), (-7000.0, 2000.0), (-7000.0, 4000.0), (-9500.0, 4000.0)}
    for _ in range(2):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0
    ok(base.run("element.rotate", {"ids": [EL], "center": [3000, 8250], "angle": 30}))       # 제자리 회전: 구멍이 따라 돈다
    p = params(base, EL)
    assert p["shaft"] == [2000.0, 7000.0, 4000.0, 9500.0] and p["rotation"] == 30.0
    assert sorted(map(tuple, params(base, F.S1)["openings"][0])) == sorted((round(x, 6), round(y, 6)) for x, y in K.elevator_ring(p, p["shaft"]))
    def door_world(p):
        (a, b), n = K.elevator_door_edge(p)
        rot = K.elevator_rot(p)
        return sorted((round(q[0], 6), round(q[1], 6)) for q in (K.rot_pt(rot, *a), K.rot_pt(rot, *b))), K.rot_vec(rot, *n)
    d0, n0 = door_world(p)
    ok(base.run("element.mirror", {"ids": [EL], "axis": [3000, 0, 3000, 1000]}))            # 대칭(세로축): 회전 180 - 30, 문 면 S -> N
    p = params(base, EL)
    assert p["rotation"] == 150.0 and p["doors"]["face"] == "N" and p["shaft"] == [2000.0, 7000.0, 4000.0, 9500.0]
    d1, n1 = door_world(p)   # 대칭 결과의 문 위치·바깥 법선 = 대칭 전 값을 축(x = 3000)으로 뒤집은 것
    assert [c for q in d1 for c in q] == pytest.approx([c for q in sorted((round(6000 - x, 6), y) for x, y in d0) for c in q], abs=1e-5)
    assert n1 == pytest.approx((-n0[0], n0[1]))
    assert err(base.run("element.copy", {"ids": [EL], "dx": 1000, "dy": 0}))["code"] == "CONSTRAINT"   # 복사는 안 된다(구멍 소유)
    for _ in range(2):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0
