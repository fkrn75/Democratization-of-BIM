# SPDX-License-Identifier: MPL-2.0
"""W2 ARC-04: 곡선(원호) 벽 wall.arc·wall.arc_set 과 호 길이 연관 치수.
- 결정적 삼각함수·호 계산(arcgeom), 세 점·중심 입력, 거절 사례(한 직선·두께보다 작은 반지름·아직 받지 않는 키·직선 전용 편집)
- 직선 벽과 L 조인(반원 앱스: 이음매 선이 없다), 문·창 호스팅(호 위 거리 offset), 평면 절단선·채움·단면·입면(조각 이음매 선 없음)
- 면적 평면 gross·공간 자동 생성, 이동·회전·대칭·되돌리기·증분 = 전체, IFC(복합 곡선 몸체 + IfcTrimmedCurve(IfcCircle) 축, 부피) 왕복
- 호 길이·반지름 연관 치수: 벽을 옮기고 반지름·두께를 바꾸면 값이 따라가고, 지우면 고아, IFC 왕복. 합성 데이터만 쓴다."""
import math
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import annot as AN
from dobim_core import arcgeom as AG
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import queries as Q

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

AW = "1" + "0" * 20 + "9"      # 곡선 벽(반원 앱스: (0,4000) -> (-2000,2000) -> (0,0), 중심 (0,2000) 반지름 2000)
AD = "2" + "0" * 20 + "9"      # 곡선 벽의 문
AF = "2" + "0" * 20 + "8"      # 곡선 벽의 창
V, VS, VE = F.V1, F.V2, "4" + "0" * 20 + "3"
PTS = [[0, 4000], [-2000, 2000], [0, 0]]


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def destructive(s, name, args):
    r = s.run(name, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


def g_of(s):
    s.eng.sync()
    s.eng._touch_pending()
    return s.eng.g


def apse(s, views=True):
    """기준 프로젝트 + W3 끝(0,4000)에서 W1 시작(0,0)까지 반원 앱스 곡선 벽, 두 직선 벽과 L 조인 + 평면·단면·서쪽 입면 뷰."""
    ok(s.run("wall.arc", {"id": AW, "level": F.LV1, "points": PTS, "thickness": 200, "height": 3000}))
    ok(s.run("join.set", {"a": F.W3, "b": AW}))
    ok(s.run("join.set", {"a": F.W1, "b": AW}))
    if views:
        ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
        ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [-3000, 2000], "p2": [7000, 2000]}))
        ok(s.run("view.create", {"id": VE, "name": "W", "type": "elevation", "dir": "W"}))
    return s


@pytest.fixture
def ab(base):
    return apse(base)


# ---------------------------------------------------------------- 순수 계산
def test_결정적_삼각함수와_호_계산():
    for t in (0.0, 0.3, 1.0, 2.5, -3.0, 7.0, -11.0):
        sn, cs = AG.sincos(t)
        assert abs(sn - math.sin(t)) < 1e-14 and abs(cs - math.cos(t)) < 1e-14
    for y, x in ((1, 1), (1, -1), (-1, -1), (-1, 1), (0, -1), (0, 1), (3, 0.2), (-3, 0.2), (-1e-4, -5)):
        assert abs(AG.atan2(y, x) - math.atan2(y, x)) < 1e-14
    ai = AG.info([0, 4000, 0, 0], [-2000, 2000])        # 반시계 반원
    assert ai[:3] == pytest.approx((0, 2000, 2000)) and ai[4] == 1.0 and ai[5] == pytest.approx(math.pi)
    assert AG.length(ai) == pytest.approx(2000 * math.pi)
    assert AG.point(ai, AG.length(ai) / 2, 100) == pytest.approx((-1900, 2000))   # 왼쪽(+v) = 중심 쪽
    assert AG.u_of(ai, (100, -50)) == pytest.approx(AG.length(ai) + 2000 * math.atan2(100, 2050), rel=1e-9)   # 끝 너머(늘인 곳)
    cw = AG.info([0, 0, 0, 4000], [-2000, 2000])        # 거꾸로 돌면 시계, 왼쪽 = 바깥
    assert cw[4] == -1.0 and AG.point(cw, AG.length(cw) / 2, 100) == pytest.approx((-2100, 2000))
    axis, mid = AG.from_center(0, 2000, 2000, 90, 270)
    assert axis == [0.0, 4000.0, 0.0, 0.0] and mid == [-2000.0, 2000.0]
    assert AG.canonical_mid([0, 4000, 0, 0], [-1200, 3600]) == [-2000.0, 2000.0]
    us = AG.breaks(ai, -100.0, 1000.0)
    assert us[0] == -100.0 and us[-1] == 1000.0 and all(b > a for a, b in zip(us, us[1:]))
    assert AG.problem([0, 0, 1000, 0], [500, 0.5], 200) and AG.problem([0, 4000, 0, 0], [-2000, 2000], 4100)
    assert AG.problem([0, 4000, 0, 0], [-2000, 2000], 200) is None


# ---------------------------------------------------------------- 만들기·거절
def test_세_점과_중심_입력이_같은_벽을_만들고_호_가운데를_저장한다(base):
    a = ok(base.run("wall.arc", {"level": F.LV1, "points": [[0, 4000], [-1200, 3600], [0, 0]]}))
    b = ok(base.run("wall.arc", {"level": F.LV1, "center": [0, 2000], "radius": 2000, "start_angle": 90, "end_angle": 270}))
    assert a["axis"] == b["axis"] == [0.0, 4000.0, 0.0, 0.0] and a["arc"] == b["arc"] == [-2000.0, 2000.0]
    assert a["radius"] == 2000.0 and a["length"] == round(2000 * math.pi, 3)
    p = ok(base.run("element.get", {"id": a["id"]}))
    assert p["params"]["arc"] == [-2000.0, 2000.0]
    rows = {r[0]: r for r in ok(base.run("schedule.rows", {"kind": "wall"}))["items"]}
    assert rows[a["id"]][4] == pytest.approx(2000 * math.pi)                    # 일람표 길이 = 호 길이


def test_거절_사례(base):
    assert err(base.run("wall.arc", {"level": F.LV1, "points": [[0, 0], [1000, 0], [2000, 0]]}))["code"] == "CONSTRAINT"
    assert err(base.run("wall.arc", {"level": F.LV1, "points": [[0, 0], [1000, 0.4], [2000, 0]]}))["code"] == "CONSTRAINT"   # 거의 직선
    assert err(base.run("wall.arc", {"level": F.LV1, "center": [0, 0], "radius": 90, "start_angle": 0, "end_angle": 90}))["code"] == "CONSTRAINT"   # 반지름 < 두께/2
    assert err(base.run("wall.arc", {"level": F.LV1, "center": [0, 0], "radius": 900, "start_angle": 0, "end_angle": 360}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("wall.arc", {"level": F.LV1, "points": PTS, "center": [0, 0]}))["code"] == "SCHEMA_INVALID"
    apse(base, views=False)
    # 분할·간격띄우기·T 조인 줄기·트림/연장은 W2 세 번째 PR 부터 곡선 벽도 받는다(test_arc_wall_edit.py)
    for name, args in (("wall.set", {"id": AW, "axis": [0, 0, 100, 0]}), ("wall.set", {"id": AW, "top_profile": [[0, 3000], [6000, 2000]]}),   # 윤곽 끝 = 호 길이(6283.185)가 아니다
                       ("join.tee", {"stem": AW, "host": F.W2})):   # 곡선 벽 원이 W2 축(x=6000)과 만나지 않는다
        assert err(base.run(name, args))["code"] == "CONSTRAINT", name
    ok(base.run("wall.set", {"id": AW, "thickness": 300, "height": 3200}))          # 두께·높이는 직선 벽과 같다
    assert err(base.run("wall.set", {"id": AW, "thickness": 4200}))["code"] == "CONSTRAINT"   # 안쪽 면 반지름이 음수가 된다


# ---------------------------------------------------------------- 조인·호스팅
def test_반원_앱스는_직선_벽과_L_조인하고_이음매가_없다(ab):
    g = g_of(ab)
    assert g.q(Q.wall_trim, AW)[2] == "ok" and g.q(Q.wall_length, AW) == pytest.approx(2000 * math.pi)
    assert g.q(Q.wall_extent, F.W1) == pytest.approx((0.0, 6100.0))                 # 접선이 이어지는 조인: 모서리 연장 없음
    cut = [ln for ln in g.q(D.plan_wall_lines, V, AW) if ln[0] == "cut"]
    for _, x0, y0, x1, y1 in cut:                                                    # 절단선 끝점은 바깥·안쪽 면의 원 위에 있다
        for x, y in ((x0, y0), (x1, y1)):
            assert min(abs(math.hypot(x, y - 2000) - 2100), abs(math.hypot(x, y - 2000) - 1900)) < 0.01
    vl = g.q(D.view_lines, V)
    assert not any(abs(x0) < 0.5 and abs(x1) < 0.5 and min(y0, y1) < 4100 and max(y0, y1) > 3900 for _, x0, y0, x1, y1 in vl)   # (0,4000) 이음매에 가로선이 없다
    n = AG.facet_n(g.q(Q.wall_arc, AW))
    arc_fills = [q for q in g.q(D.view_fills, V) if len(q) == 2 * (n + 1)]
    assert len(arc_fills) == 1                                                       # 곡선 벽 절단 채움은 조각을 이은 한 다각형(채움 이음매 없음)


def test_문과_창은_호_위_거리에_놓이고_평면_기호를_그린다(ab):
    ok(ab.run("door.place", {"id": AD, "host": AW, "offset": 1500, "width": 900, "height": 2100}))
    ok(ab.run("family.place", {"id": AF, "family": "window.framed", "type_values": {}, "values": {}, "host": AW, "offset": 4500}))
    assert err(ab.run("door.place", {"host": AW, "offset": 6200, "width": 900}))["code"] == "CONSTRAINT"   # 호 길이 6283 밖
    g = g_of(ab)
    x, y, z = g.q(Q.door_world, AD)
    assert math.hypot(x, y - 2000) == pytest.approx(2000) and (x, y) == pytest.approx(AG.point(g.q(Q.wall_arc, AW), 1500))
    ops = g.q(D.wall_openings, AW)
    assert [o[:2] for o in ops][0] == pytest.approx((1050, 1950))
    sym = [ln for ln in g.q(D.plan_wall_lines, V, AW) if ln[0] == "symbol"]
    assert len(sym) > 8
    r = ok(ab.run("annot.dim.create", {"view": V, "a": f"door:{AD}#jamb.start", "b": f"door:{AD}#jamb.end"}))
    st = g_of(ab).q(AN.dim_value, r["id"])
    assert st[0] == "ok" and st[1] == pytest.approx(2 * 2000 * math.sin(900 / 2000 / 2))   # 문틀 사이 현


def test_단면_입면은_조각_이음매_선이_없다(ab):
    g = g_of(ab)
    rects = g.q(D.vcut_rects, VS, AW)
    assert rects and min(p[0] for r in rects for p in r) == pytest.approx(3000 - 2100) and max(p[0] for r in rects for p in r) == pytest.approx(3000 - 1900)
    el = g.q(D.vwall_lines, VE, AW)
    vert = [ln for ln in el if abs(ln[1] - ln[3]) < 1e-6 and abs(ln[2] - ln[4]) > 1]
    xs = sorted({round(ln[1], 1) for ln in vert})
    assert len(xs) <= 4, xs                                                          # 양 끝·윤곽만(조각 경계마다 세로선이 서지 않는다)
    assert g.q(D.view_lines, VE) and g.q(D.view_lines, VS)


def test_면적_평면과_공간_자동_생성이_곡선_벽을_따른다(ab):
    ok(ab.run("view.area_plan", {"id": "9" + "0" * 20 + "a", "name": "면적", "level": F.LV1, "scheme": "gross"}))
    from dobim_core import viewplans as VP
    g = g_of(ab)
    items = g.q(VP.area_items, "9" + "0" * 20 + "a")
    rect = 6100 * 4200
    half = math.pi * 2100 ** 2 / 2
    assert items and items[0][2] == pytest.approx(rect + half, rel=2e-3)            # 외곽 = 사각 + 바깥면 반원(조각 근사)
    ok(ab.run("wall.create", {"id": "1" + "0" * 20 + "8", "level": F.LV1, "axis": [6000, 4000, 6000, 0], "thickness": 200}))
    r = ok(ab.run("space.from_walls", {"level": F.LV1, "all": True}))
    sp = ok(ab.run("element.get", {"id": r["created"][0]}))["params"]
    xs = [p[0] for p in sp["boundary"]]
    assert min(xs) == pytest.approx(-1900, abs=1.0)                                  # 안쪽 면 반원까지(기본 edge = inner)


# ---------------------------------------------------------------- 편집·되돌리기·증분
def test_이동_회전_대칭_복사(ab):
    ok(ab.run("wall.move", {"id": AW, "dx": 100, "dy": 50}))
    p = ok(ab.run("element.get", {"id": AW}))["params"]
    assert p["axis"] == [100.0, 4050.0, 100.0, 50.0] and p["arc"] == [-1900.0, 2050.0]
    ok(ab.run("wall.move", {"id": AW, "dx": -100, "dy": -50}))
    r = ok(ab.run("element.copy", {"ids": [AW], "dx": 0, "dy": 10000}))
    q = ok(ab.run("element.get", {"id": r["ids"][0]}))["params"]
    assert q["arc"] == [-2000.0, 12000.0]
    ok(ab.run("element.mirror", {"ids": [r["ids"][0]], "axis": [0, 0, 0, 1000]}))
    q = ok(ab.run("element.get", {"id": r["ids"][0]}))["params"]
    assert q["arc"] == [2000.0, 12000.0] and q["axis"] == [0.0, 10000.0, 0.0, 14000.0]   # 축이 뒤집혀도 왼쪽 면이 왼쪽
    ok(ab.run("element.rotate", {"ids": [r["ids"][0]], "center": [0, 12000], "angle": 90}))
    q = ok(ab.run("element.get", {"id": r["ids"][0]}))["params"]
    assert q["arc"] == [0.0, 14000.0]


def test_되돌리면_도면이_같고_증분과_전체가_같다(ab):
    def exp(name):
        assert destructive(ab, "view.export", {"id": V, "path": name, "overwrite": True})["ok"]
        return (ab.path.parent / name).read_bytes()
    before = exp("a.svg")
    ok(ab.run("wall.arc_set", {"id": AW, "points": [[0, 4000], [-1500, 2000], [0, 0]]}))
    assert exp("b.svg") != before
    ok(ab.run("history.undo", {}))
    assert exp("c.svg") == before
    ok(ab.run("door.place", {"id": AD, "host": AW, "offset": 1500, "width": 900, "height": 2100}))
    ok(ab.run("annot.dim.create", {"view": V, "a": f"wall:{AW}#arc.left", "measure": "arc"}))
    g = g_of(ab)
    for v in (V, VS, VE):
        g.q(D.view_lines, v)
    g.q(AN.annot_bundle)
    ok(ab.run("wall.arc_set", {"id": AW, "center": [0, 2000], "radius": 2500, "start_angle": 90, "end_angle": 270}))
    ok(ab.run("wall.set", {"id": AW, "thickness": 300}))
    g = g_of(ab)
    full = s3lib.RefGraph(s3lib._Inputs(ab.eng))
    for v in (V, VS, VE):
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)
    assert g.q(AN.annot_bundle) == full.q(AN.annot_bundle)


# ---------------------------------------------------------------- 호 길이 연관 치수
def test_호_길이_치수는_벽을_바꾸면_따라간다(ab):
    a = ok(ab.run("annot.dim.create", {"view": V, "a": f"wall:{AW}#arc.left", "measure": "arc", "offset": 600}))["id"]
    ax = ok(ab.run("annot.dim.create", {"view": V, "a": f"wall:{AW}#arc", "measure": "arc"}))["id"]
    rr = ok(ab.run("annot.dim.create", {"view": V, "a": f"wall:{AW}#arc", "measure": "radius"}))["id"]

    def val(d):
        st = g_of(ab).q(AN.dim_value, d)
        assert st[0] == "ok", st
        return st[1]
    assert val(a) == pytest.approx(1900 * math.pi) and val(ax) == pytest.approx(2000 * math.pi) and val(rr) == pytest.approx(2000)
    ok(ab.run("wall.move", {"id": AW, "dx": 300, "dy": 0}))
    assert val(ax) == pytest.approx(2000 * math.pi)
    ok(ab.run("wall.move", {"id": AW, "dx": -300, "dy": 0}))
    ok(ab.run("wall.set", {"id": AW, "thickness": 400}))
    assert val(a) == pytest.approx(1800 * math.pi)                                   # 왼쪽(안쪽) 면 반지름 = 2000 - 200
    ok(ab.run("wall.arc_set", {"id": AW, "points": [[0, 4000], [-1000, 2000], [0, 0]]}))
    ai = g_of(ab).q(Q.wall_arc, AW)
    assert val(ax) == pytest.approx(ai[2] * ai[5]) and val(rr) == pytest.approx(2500)
    draw = g_of(ab).q(AN.annot_draw, a)
    assert draw[0] == "adim" and len(draw[3]) == 2 and draw[4] == AN.fmt_num(val(a))
    assert err(ab.run("annot.dim.create", {"view": V, "a": f"wall:{F.W1}#arc", "measure": "arc"}))["code"] == "CONSTRAINT"   # 직선 벽에는 호가 없다
    assert err(ab.run("annot.dim.create", {"view": V, "a": f"wall:{AW}#face.left", "b": f"wall:{F.W1}#face.left"}))["code"] == "CONSTRAINT"
    assert err(ab.run("annot.dim.create", {"view": V, "a": f"wall:{AW}#arc", "b": f"wall:{F.W1}#trim.end"}))["code"] == "CONSTRAINT"
    ok(ab.run("element.delete", {"id": AW}))
    assert g_of(ab).q(AN.dim_value, a)[0] == "orphan"


# ---------------------------------------------------------------- IFC
def _arc_items(w):
    rep = {r.RepresentationIdentifier: r for r in w.Representation.Representations}
    body, axis = rep["Body"].Items[0], rep["Axis"].Items[0]
    return body, axis


def test_IFC_는_복합_곡선_몸체와_원호_축이고_부피가_맞으며_왕복한다(tmp_path):
    import ifcopenshell
    import ifcopenshell.geom as geom
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("wall.arc", {"id": AW, "level": F.LV1, "points": [[0, -6000], [-2000, -8000], [0, -10000]], "thickness": 200, "height": 3000}))
        ok(b.run("door.place", {"id": AD, "host": AW, "offset": 1500, "width": 900, "height": 2100}))
        ok(b.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1}))
        ok(b.run("annot.dim.create", {"id": "5" + "0" * 20 + "9", "view": V, "a": f"wall:{AW}#arc.right", "measure": "arc"}))
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        w = f.by_guid(AW)
        body, axis = _arc_items(w)
        assert body.is_a("IfcExtrudedAreaSolid") and body.SweptArea.OuterCurve.is_a("IfcCompositeCurve")
        segs = [s.ParentCurve for s in body.SweptArea.OuterCurve.Segments]
        assert [c.is_a() for c in segs] == ["IfcTrimmedCurve", "IfcPolyline", "IfcTrimmedCurve", "IfcPolyline"]
        assert axis.is_a("IfcTrimmedCurve") and axis.BasisCurve.is_a("IfcCircle") and axis.BasisCurve.Radius == pytest.approx(2000)
        assert f.by_guid(AD).ObjectPlacement.PlacementRelTo == w.ObjectPlacement
        st = geom.settings()
        sh = geom.create_shape(st, w)
        import numpy as np
        vs = np.array(sh.geometry.verts).reshape(-1, 3)
        fs = np.array(sh.geometry.faces).reshape(-1, 3)
        vol = abs(sum(np.dot(vs[a], np.cross(vs[bb], vs[c])) for a, bb, c in fs)) / 6.0 * 1e9
        ring = math.pi * (2100 ** 2 - 1900 ** 2) / 2 * 3000
        door = 900 * 200 * 2100
        assert vol == pytest.approx(ring - door, rel=0.01)                            # 반원 고리 - 문 개구부
        import ifcopenshell.validate as IV

        def errors(ff):
            lg = IV.json_logger()
            IV.validate(ff, lg, express_rules=True)
            # IfcSurfaceStyle 의 2x3 Styles 오류는 곡선 벽이 없는 기준 프로젝트(재료 색)에서도 나는 기존 변환 한계라 뺀다
            return [x["message"] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
                    and "IfcSurfaceStyle" not in x["message"]]
        assert errors(f) == []
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "t3.ifc"))
        assert f3.schema == "IFC2X3" and errors(f3) == [] and _arc_items(f3.by_guid(AW))[1].is_a("IfcTrimmedCurve")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert ok(b.run("element.get", {"id": AW}, project="imp.dobim"))["params"]["arc"] == [-2000.0, -8000.0]
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
    finally:
        b.close()


def test_수평_선분_후보_미리_거르기는_값이_같다(ab, monkeypatch):
    """가림 후보가 많을 때(BYZ_MIN 초과) 수평 선분의 후보를 높이로 미리 거르는 속도 경로가 거르지 않은 값과 같다."""
    ok(ab.run("door.place", {"id": AD, "host": AW, "offset": 1500, "width": 900, "height": 2100}))
    ok(ab.run("wall.arc", {"level": F.LV1, "points": [[-4000, 5000], [-5000, 2000], [-4000, -1000]]}))
    got = {}
    for lim in (0, 10 ** 9):
        monkeypatch.setattr(D, "BYZ_MIN", lim)
        full = s3lib.RefGraph(s3lib._Inputs(ab.eng))
        got[lim] = [full.q(D.view_lines, v) for v in (VS, VE)]
    assert got[0] == got[10 ** 9] and all(got[0])
