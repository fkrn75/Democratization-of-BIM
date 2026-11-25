# SPDX-License-Identifier: MPL-2.0
"""W2 열여덟 번째 PR(구조 보 2부: 경사 보, 보끼리 접합, 단면 추가, 구조 재료의 IfcMaterialProfileSet).

1. 경사 보: 끝점 기준선 높이(end_offset). 단면은 축에 수직, 실길이 수량, 기둥 면 자르기는 축 방향 길이, 도면(평면 숨은선·단면 채움·입면), IFC 돌출 축 기울기
2. 보끼리 접합: 다른 보(주 보)의 평면 외곽 안에서 끝나는 보는 그 옆면까지 자른다(T·L). 서로 끝이 닿으면 춤이 큰 보(같으면 폭, 같으면 id)가 이긴다.
   주 보를 옮기면 따라간다. 높이가 안 겹치거나 cutback false 면 자르지 않는다
3. 단면 추가: ㄷ형강(c)·ㄱ형강(l)·각형 강관(box)·원형 강관(pipe)과 KS 표준 단면 preset. IFC 매개변수 프로필
4. 구조 재료: 보 유형 material -> 보 재료. IFC4 는 IfcMaterialProfileSetUsage(보)·IfcMaterialProfileSet(보 유형), IFC2X3 은 IfcMaterial 로 대체
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import sys

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import beams as BM
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import queries as Q

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V, V2, VE = F.V1, F.V2, "4" + "0" * 19 + "e9"
BT, BS, BC, BL, BP, BX = ("9" + "0" * 19 + x for x in ("b1", "b2", "b3", "b4", "b5", "b6"))   # 보 유형
BA, BB, BCC, BD = ("9" + "0" * 19 + x for x in ("m1", "m2", "m3", "m4"))                   # 보
C1, C2 = ("0" * 20 + x for x in ("c1", "c2"))                                             # 기둥
IBT, IBS, IBM, IBN, IBK = ("3" + "0" * 19 + x for x in ("b7", "b8", "m7", "m8", "m9"))   # IFC 왕복용(첫 글자가 유효한 GlobalId)


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


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [x["message"][:160] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def same_as_full(s, views=(V,)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)
        assert g.q(D.view_fills, v) == full.q(D.view_fills, v)


def column(s, cid, pos, w=500):
    ok(s.run("family.place", {"id": cid, "family": "column.section", "type_values": {"section": "rect", "width": w, "depth": w},
                              "values": {"height": 3300}, "level": F.LV1, "position": list(pos)}))


def get(s, e):
    return ok(s.run("element.get", {"id": e}))


@pytest.fixture
def bs(base):
    ok(base.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    return base


# ---------------------------------------------------------------- 1. 순수 형상
def test_1_경사_보와_새_단면_순수():
    sc = {"shape": "rect", "width": 400.0, "depth": 600.0}
    p = {"name": "b", "start": [0.0, 0.0], "end": [3000.0, 0.0], "offset": 0.0, "end_offset": 4000.0, "anchor": "top", "section": sc}
    assert K.row_params_error("beam", p) is None
    c, s, l3 = BM.slope(p)
    assert l3 == pytest.approx(5000.0) and (c, s) == (pytest.approx(0.6), pytest.approx(0.8))
    assert BM.length3(p) == pytest.approx(5000.0)
    verts, faces = BM.mesh(p, 1000.0, (0.0, 0.0))
    # 단면은 축에 수직: 시작 끝면 윗변은 z 1000, 아랫변은 1000 - 600 x cos
    assert max(v[2] for v in verts) == pytest.approx(5000.0) and min(v[2] for v in verts) == pytest.approx(1000.0 - 600 * 0.6)
    rect = BM.plan_rect(p, (0.0, 0.0))
    xs = sorted({round(q[0], 6) for q in rect})
    assert xs == [pytest.approx(0.0), pytest.approx(3000.0 + 600 * 0.8)]                 # 밑변이 진행 방향으로 600 x sin 만큼 더 나간다
    assert BM.end_zranges(p, 1000.0) == ((pytest.approx(640.0), pytest.approx(1000.0)), (pytest.approx(4640.0), pytest.approx(5000.0)))
    assert K.row_params_error("beam", dict(p, end_offset=0.0))                           # end_offset = offset 은 적지 않는다
    assert K.row_params_error("beam", dict(p, end_offset=20000.0))                       # 80도보다 가파르다
    # 새 단면
    for name, shape, n_out, n_holes, n_quads in (("C-150x75x6.5x10", "c", 8, 0, 3), ("L-75x75x9", "l", 6, 0, 2), ("B-200x100x6", "box", 4, 1, 4),
                                                ("P-139.8x4.5", "pipe", 32, 1, 32)):
        sc = BM.preset_section(name)
        assert sc["shape"] == shape and BM.section_problem(sc) is None and BM.section_label(sc) == name
        q = {"section": sc, "anchor": "center"}
        assert len(BM.section_outline(q)) == n_out and len(BM.section_holes(q)) == n_holes and len(BM.section_quads(q)) == n_quads
        quads_area = sum(abs(D._area2(list(x))) / 2.0 for x in BM.section_quads(q))
        if shape != "pipe":
            assert quads_area == pytest.approx(BM.section_area(sc))                       # 볼록 조각 넓이 합 = 단면적
        else:
            assert quads_area == pytest.approx(BM.section_area(sc), rel=0.02)              # 32각형 근사
    assert BM.section_area(BM.preset_section("C-150x75x6.5x10")) == pytest.approx(2 * 75 * 10 + 6.5 * 130)
    assert BM.section_area(BM.preset_section("L-75x75x9")) == pytest.approx(9 * (75 + 75 - 9))
    assert BM.section_area(BM.preset_section("B-200x100x6")) == pytest.approx(200 * 100 - 188 * 88)
    # ㄷ형 웨브는 왼쪽(a -): IFC U 형 프로필과 같은 쪽
    cq = BM.section_quads({"section": BM.preset_section("C-150x75x6.5x10"), "anchor": "center"})
    assert min(x[0] for x in cq[1]) == pytest.approx(-37.5) and max(x[0] for x in cq[1]) == pytest.approx(-31.0)
    assert BM.section_problem({"shape": "pipe", "width": 100.0, "depth": 120.0, "thickness": 5.0})          # 지름이 둘
    assert BM.section_problem({"shape": "l", "width": 50.0, "depth": 50.0, "thickness": 60.0})
    assert BM.section_problem({"shape": "box", "width": 50.0, "depth": 50.0})                                # 두께 없음


# ---------------------------------------------------------------- 2. 경사 보 명령·기둥 면 자르기·도면
def test_2_경사_보(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 600}))
    assert err(bs.run("beam.create", {"level": F.LV2, "type": BT, "start": [0, 0], "end": [100, 0], "end_offset": 5000}))["code"] == "CONSTRAINT"
    ok(bs.run("beam.create", {"id": BA, "level": F.LV1, "type": BT, "start": [0, -2000], "end": [3000, -2000], "offset": 1000, "end_offset": 5000}))
    p = get(bs, BA)["params"]
    assert p["end_offset"] == 5000.0
    item = ok(bs.run("beam.list", {}))["items"][0]
    assert item["length"] == pytest.approx(5000.0) and item["slope_deg"] == pytest.approx(53.13, abs=0.01) and item["end_offset"] == 5000
    t = ok(bs.run("schedule.table", {"category": "beams", "fields": ["name", "length", "volume"], "units": "mm"}))
    assert t["items"][0][1:] == [pytest.approx(5000.0), pytest.approx(400 * 600 * 5000.0)]        # 실길이
    ok(bs.run("beam.move", {"id": BA, "dz": 100}))
    p = get(bs, BA)["params"]
    assert (p["offset"], p["end_offset"]) == (1100, 5100)
    ok(bs.run("beam.set", {"id": BA, "offset": 1000, "end_offset": 5000}))
    ok(bs.run("beam.set", {"id": BA, "end_offset": 1000}))                                        # 같은 높이 = 수평 보
    assert "end_offset" not in get(bs, BA)["params"]
    ok(bs.run("history.undo", {}))
    assert get(bs, BA)["params"]["end_offset"] == 5000
    # 기둥 면 자르기: 평면 자른 길이 250 이 축 방향으로 250 / cos
    column(bs, C1, (0, -2000))
    g = g_of(bs)
    c = g.q(Q.beam_cut, BA)
    assert c[2] == C1 and c[0] == pytest.approx(250.0 / 0.6)
    spec = g.q(Q.ifc_spec, BA)
    assert spec["slope"] == (pytest.approx(0.6), pytest.approx(0.8)) and spec["origin"][0] == pytest.approx(250.0)
    assert spec["origin"][2] == pytest.approx(1000.0 + 0.8 * 250 / 0.6) and spec["length"] == pytest.approx(5000.0 - 250 / 0.6)
    # 도면: 평면(1층 절단 1200 은 보를 자른다: 채움), 단면(축을 가로지르는 절단: 채움 넓이 = 단면적 / cos), 입면(가림 조각 없음)
    assert g.q(D.beam_fills, V, BA) and any(x[0] == "cut" for x in g.q(D.beam_lines, V, BA))
    ok(bs.run("view.create", {"id": V2, "name": "S", "type": "section", "p1": [1500, -5000], "p2": [1500, 2000]}))
    ok(bs.run("view.create", {"id": VE, "name": "남", "type": "elevation", "dir": "N"}))
    g = g_of(bs)
    f = g.q(D.beam_fills, V2, BA)
    assert len(f) == 1 and abs(D._area2(list(f[0]))) / 2.0 == pytest.approx(400 * 600 / 0.6)
    assert g.q(D._beam_vpieces, VE, BA) == () and g.q(D.beam_lines, VE, BA)
    same_as_full(bs, (V, V2, VE))
    # 대칭·저장 형식 검사
    ok(bs.run("element.mirror", {"ids": [BA], "axis": [0, 0, 1000, 0]}))
    assert get(bs, BA)["params"]["end_offset"] == 5000
    v = ok(bs.run("model.validate", {}))
    assert not [m for m in v["violations"] if "IfcGloballyUniqueId" not in m], v


# ---------------------------------------------------------------- 3. 보끼리 접합
def test_3_보끼리_접합(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    ok(bs.run("beamtype.create", {"id": BS, "name": "B1", "width": 300, "depth": 500}))
    ok(bs.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [0, 0], "end": [6000, 0]}))          # 주 보
    ok(bs.run("beam.create", {"id": BB, "level": F.LV2, "type": BS, "start": [3000, 4000], "end": [3000, 0]}))    # T: 작은 보 끝이 주 보 가운데
    g = g_of(bs)
    assert g.q(Q.beam_cut, BB) == (0.0, pytest.approx(200.0), None, BA)                                            # 주 보 옆면(y 200)까지
    assert g.q(Q.beam_cut, BA) == (0.0, 0.0, None, None)
    it = {x["id"]: x for x in ok(bs.run("beam.list", {}))["items"]}
    assert it[BB]["end_cut"] == {"beam": BA, "length": 200.0} and it[BB]["cut_length"] == pytest.approx(3800.0)
    ok(bs.run("beam.move", {"id": BA, "dy": 100}))                                                                 # 주 보를 옮기면 따라간다(옆면 y 300)
    assert g_of(bs).q(Q.beam_cut, BB)[1] == pytest.approx(300.0)
    ok(bs.run("history.undo", {}))
    # L: 서로 끝이 닿는다 -> 춤이 큰 주 보가 이긴다
    ok(bs.run("beam.create", {"id": BCC, "level": F.LV2, "type": BS, "start": [6000, 0], "end": [6000, 5000]}))
    g = g_of(bs)
    assert g.q(Q.beam_cut, BCC)[:3] == (pytest.approx(200.0), 0.0, BA) and g.q(Q.beam_cut, BA) == (0.0, 0.0, None, None)
    ok(bs.run("beamtype.set", {"id": BS, "depth": 800}))                                                           # 이제 B1 이 더 깊다: 주 보가 바뀐다
    g = g_of(bs)
    assert g.q(Q.beam_cut, BA)[1:] == (pytest.approx(150.0), None, BCC) and g.q(Q.beam_cut, BCC) == (0.0, 0.0, None, None)
    ok(bs.run("history.undo", {}))
    # 높이가 안 겹치면 자르지 않는다, cutback false 도
    ok(bs.run("beam.set", {"id": BB, "offset": -1500}))
    assert g_of(bs).q(Q.beam_cut, BB) == (0.0, 0.0, None, None)
    ok(bs.run("beam.set", {"id": BB, "offset": 0, "cutback": False}))
    assert g_of(bs).q(Q.beam_cut, BB) == (0.0, 0.0, None, None)
    ok(bs.run("beam.set", {"id": BB, "cutback": True}))
    # 한 줄로 이어지는 보(끝이 맞닿는다)는 서로 자르지 않는다
    ok(bs.run("beam.create", {"id": BD, "level": F.LV2, "type": BT, "start": [6000, 0], "end": [12000, 0]}))
    g = g_of(bs)
    assert g.q(Q.beam_cut, BD)[2] is None and g.q(Q.beam_cut, BA)[3] is None
    # 기둥과 주 보가 함께 닿으면 더 많이 줄이는 쪽(기둥 면 250 > 보 면 200)
    column(bs, C1, (3000, 0))
    c = g_of(bs).q(Q.beam_cut, BB)
    assert c[1] == pytest.approx(250.0) and c[3] == C1
    # 수량·도면이 잘린 길이를 쓴다
    t = ok(bs.run("schedule.table", {"category": "beams", "fields": ["name", "length"], "units": "mm"}))
    assert sorted(round(r[1], 3) for r in t["items"]) == sorted([round(x, 3) for x in (6000.0, 3750.0, 4800.0, 6000.0)])
    ok(bs.run("view.create", {"id": V2, "name": "2F", "type": "plan", "level": F.LV2, "cut_height": 1200}))
    ok(bs.run("view.create", {"id": VE, "name": "남", "type": "elevation", "dir": "N"}))
    same_as_full(bs, (V, V2, VE))
    assert g_of(bs).q(Q.beam_cut, BB)[3] == C1
    ok(bs.run("element.delete", {"id": BA}))                                                                       # 주 보를 지우면 남은 주 보(BD)가 정한다
    c = g_of(bs).q(Q.beam_cut, BCC)
    assert c[0] == pytest.approx(200.0) and c[2] == BD
    ok(bs.run("element.delete", {"id": BD}))                                                                       # 주 보가 없으면 늘어난다
    assert g_of(bs).q(Q.beam_cut, BCC) == (0.0, 0.0, None, None)
    same_as_full(bs, (V, VE))


# ---------------------------------------------------------------- 4. 단면 추가와 구조 재료
def test_4_새_단면과_구조_재료(bs):
    ok(bs.run("beamtype.create", {"id": BC, "name": "SB1", "preset": "C-150x75x6.5x10", "material": "SS275"}))
    ok(bs.run("beamtype.create", {"id": BL, "name": "L1", "shape": "l", "width": 75, "depth": 75, "thickness": 9}))
    ok(bs.run("beamtype.create", {"id": BP, "name": "P1", "shape": "pipe", "width": 139.8, "thickness": 4.5}))
    assert get(bs, BP)["params"]["section"] == {"shape": "pipe", "width": 139.8, "depth": 139.8, "thickness": 4.5}
    assert err(bs.run("beamtype.create", {"name": "X", "shape": "box", "width": 100, "depth": 100}))["code"] == "CONSTRAINT"      # 두께 없음
    assert err(bs.run("beamtype.create", {"name": "X", "shape": "rect", "width": 100, "depth": 100, "thickness": 5}))["code"] == "CONSTRAINT"
    ok(bs.run("beamtype.create", {"id": BX, "name": "B1", "preset": "B-200x100x6"}))
    lst = ok(bs.run("beamtype.list", {}))
    assert {"C-150x75x6.5x10", "L-75x75x9", "P-139.8x4.5", "B-200x100x6"} <= set(lst["presets"])
    assert next(x for x in lst["items"] if x["id"] == BC)["material"] == "SS275"
    ok(bs.run("beam.create", {"id": BA, "level": F.LV2, "type": BC, "start": [0, -1000], "end": [6000, -1000]}))
    ok(bs.run("beam.create", {"id": BB, "level": F.LV2, "type": BX, "start": [0, -2000], "end": [6000, -2000]}))
    ok(bs.run("material.set", {"id": BB, "name": "콘크리트"}))                                                      # 단일 재료(유형 재료 없음)
    assert get(bs, BA)["params"]["material"] == "SS275" and get(bs, BA)["material"] == "SS275"
    assert err(bs.run("material.set", {"id": BA, "name": "SM355"}))["code"] == "CONSTRAINT"                       # 구조 재료는 유형이 정한다
    t = ok(bs.run("schedule.table", {"category": "beams", "fields": ["name", "type", "material", "length"]}))
    assert [r[1:3] for r in t["items"]] == [["SB1", "SS275"], ["B1", "콘크리트"]]
    to = ok(bs.run("schedule.takeoff", {"categories": ["beams"], "by_level": False}))
    assert {r[2] for r in to["items"]} == {"SS275", "콘크리트"}
    # 단일 재료가 있는 보에 재료 있는 유형을 입히면 단일 재료를 떼고(되돌리면 돌아온다), 유형 재료를 지우면 보 재료도 없어진다
    ok(bs.run("beam.type.set", {"ids": [BB], "type": BC}))
    assert get(bs, BB)["material"] == "SS275"
    ok(bs.run("history.undo", {}))
    assert get(bs, BB)["material"] == "콘크리트" and "material" not in get(bs, BB)["params"]
    ok(bs.run("beamtype.set", {"id": BC, "material": "SM355"}))
    assert get(bs, BA)["params"]["material"] == "SM355"
    ok(bs.run("beamtype.set", {"id": BC, "material": ""}))
    assert "material" not in get(bs, BA)["params"] and get(bs, BA)["material"] is None
    ok(bs.run("history.undo", {}))
    ok(bs.run("beam.type.set", {"ids": [BA], "type": None}))                                                      # 유형을 떼면 유형 재료도 떨어진다
    assert "material" not in get(bs, BA)["params"] and get(bs, BA)["params"]["section"]["shape"] == "c"
    ok(bs.run("history.undo", {}))
    # 도면: 단면 채움 조각 수(ㄷ 3, 각형 강관 4)와 넓이
    ok(bs.run("view.create", {"id": V2, "name": "S", "type": "section", "p1": [3000, -5000], "p2": [3000, 2000]}))
    g = g_of(bs)
    fa, fb = g.q(D.beam_fills, V2, BA), g.q(D.beam_fills, V2, BB)
    assert len(fa) == 3 and sum(abs(D._area2(list(x))) / 2.0 for x in fa) == pytest.approx(BM.section_area(BM.preset_section("C-150x75x6.5x10")))
    assert len(fb) == 4 and sum(abs(D._area2(list(x))) / 2.0 for x in fb) == pytest.approx(200 * 100 - 188 * 88)
    ok(bs.run("beam.create", {"id": BCC, "level": F.LV2, "type": BP, "start": [0, -3000], "end": [6000, -3000]}))
    ok(bs.run("beam.create", {"id": BD, "level": F.LV2, "type": BL, "start": [0, -4000], "end": [6000, -4000], "roll": 90}))
    g = g_of(bs)
    assert len(g.q(D.beam_fills, V2, BCC)) == 32 and len(g.q(D.beam_fills, V2, BD)) == 2
    same_as_full(bs, (V, V2))


# ---------------------------------------------------------------- 5. 저장하고 다시 열기
def test_5_저장하고_다시_열기(tmp_path):
    s = kit.Session(tmp_path / "b.dobim")
    try:
        for name, args in F.SCRIPT:
            ok(s.run(name, args))
        ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
        ok(s.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700, "material": "C24"}))
        ok(s.run("beamtype.create", {"id": BC, "name": "SB1", "preset": "C-200x80x7.5x11"}))
        ok(s.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [0, 0], "end": [8000, 0]}))
        ok(s.run("beam.create", {"id": BB, "level": F.LV2, "type": BC, "start": [4000, 3000], "end": [4000, 0], "end_offset": -300}))
        before = (get(s, BA)["params"], get(s, BB)["params"], g_of(s).q(D.view_lines, V), g_of(s).q(Q.ifc_spec, BB), g_of(s).q(Q.beam_cut, BB))
    finally:
        s.close()
    s = kit.Session(tmp_path / "b.dobim", create=False)
    try:
        after = (get(s, BA)["params"], get(s, BB)["params"], g_of(s).q(D.view_lines, V), g_of(s).q(Q.ifc_spec, BB), g_of(s).q(Q.beam_cut, BB))
        assert after == before and before[4][3] == BA
    finally:
        s.close()


# ---------------------------------------------------------------- 6. IFC
def test_6_IFC_경사_보_단면_재료(tmp_path):
    import ifcopenshell.geom
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("beamtype.create", {"id": IBT, "name": "SB1", "preset": "C-150x75x6.5x10", "material": "SS275"}))
        ok(b.run("beamtype.create", {"id": IBS, "name": "G1", "width": 400, "depth": 700, "material": "C24"}))
        ok(b.run("beam.create", {"id": IBM, "level": F.LV2, "type": IBS, "start": [0, -3000], "end": [6000, -3000]}))
        ok(b.run("beam.create", {"id": IBN, "level": F.LV2, "type": IBT, "start": [3000, -6000], "end": [3000, -3000], "end_offset": -400}))   # 경사 + 주 보에 접합
        ok(b.run("beamtype.create", {"name": "P1", "preset": "P-114.3x4.5"}))
        tid = next(x["id"] for x in ok(b.run("beamtype.list", {}))["items"] if x["name"] == "P1")
        ok(b.run("beam.create", {"id": IBK, "level": F.LV2, "type": tid, "start": [0, -8000], "end": [4000, -8000], "anchor": "center"}))
        h = b.hashes()
        out = b.export("b.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        e = f.by_guid(IBN)
        sw = e.Representation.Representations[0].Items[0]
        assert sw.SweptArea.is_a("IfcUShapeProfileDef") and sw.SweptArea.ProfileName == "C-150x75x6.5x10"
        dz = -400.0
        l3 = (3000.0 ** 2 + dz ** 2) ** 0.5
        assert tuple(sw.Position.Axis.DirectionRatios) == (pytest.approx(3000.0 / l3), 0.0, pytest.approx(dz / l3))
        cut = 200.0 / (3000.0 / l3)                                                                  # 주 보 옆면(y -3000 - 200): 평면 200 -> 축 방향
        assert sw.Depth == pytest.approx(l3 - cut, abs=1e-6)
        assert f.by_guid(IBK).Representation.Representations[0].Items[0].SweptArea.is_a("IfcCircleHollowProfileDef")
        # 재료: 보 = IfcMaterialProfileSetUsage, 유형 = IfcMaterialProfileSet
        u = [r.RelatingMaterial for r in e.HasAssociations if r.is_a("IfcRelAssociatesMaterial")]
        assert len(u) == 1 and u[0].is_a("IfcMaterialProfileSetUsage") and u[0].CardinalPoint == 8
        mp = u[0].ForProfileSet.MaterialProfiles[0]
        assert mp.Material.Name == "SS275" and mp.Profile.is_a("IfcUShapeProfileDef") and mp.Profile.Depth == 150
        t = [r.RelatingType for r in e.IsTypedBy][0]
        tm = [r.RelatingMaterial for r in t.HasAssociations if r.is_a("IfcRelAssociatesMaterial")]
        assert len(tm) == 1 and tm[0].is_a("IfcMaterialProfileSet") and tm[0].MaterialProfiles[0].Material.Name == "SS275"
        assert not [r for r in f.by_guid(IBK).HasAssociations if r.is_a("IfcRelAssociatesMaterial")]           # 재료 없는 유형
        # 몸체 부피 = 단면적 x 자른 실길이(ifcopenshell.geom)
        st = ifcopenshell.geom.settings()
        sh = ifcopenshell.geom.create_shape(st, e)
        import ifcopenshell.util.shape as US
        assert US.get_volume(sh.geometry) * 1e9 == pytest.approx(BM.section_area(BM.preset_section("C-150x75x6.5x10")) * (l3 - cut), rel=1e-4)
        # IFC2X3: 단면 재료 집합은 IfcMaterial 로
        assert b.destructive("ifc.export", {"path": "b3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "b3.ifc"))
        assert file_errors(f3) == [] and len(f3.by_type("IfcUShapeProfileDef")) == 1 and len(f3.by_type("IfcCircleHollowProfileDef")) == 1
        m3 = [r.RelatingMaterial for r in f3.by_guid(IBN).HasAssociations if r.is_a("IfcRelAssociatesMaterial")]
        assert len(m3) == 1 and m3[0].is_a("IfcMaterial") and m3[0].Name == "SS275"
        # 왕복
        r = b.import_ifc("b.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("b2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        # 유형 재료를 바꾸고 되돌리면 그대로, 지우면 사용·집합이 남지 않는다
        ok(b.run("beamtype.set", {"id": IBT, "material": "SM355"}))
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
        ok(b.run("element.delete", {"id": IBN}))                                                    # 재료 있는 보를 지우면 사용·집합도 사라진다
        f5 = ifcopenshell.open(str(b.export("b5.ifc")))
        assert len(f5.by_type("IfcMaterialProfileSetUsage")) == 1 and len(f5.by_type("IfcMaterialProfileSet")) == 3 and file_errors(f5) == []
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
        ok(b.run("beamtype.set", {"id": IBT, "material": ""}))
        ok(b.run("beamtype.set", {"id": IBS, "material": ""}))
        f4 = ifcopenshell.open(str(b.export("b4.ifc")))
        assert not f4.by_type("IfcMaterialProfileSet") and not f4.by_type("IfcMaterialProfileSetUsage") and not f4.by_type("IfcMaterialProfile")
        assert file_errors(f4) == []
    finally:
        b.close()
