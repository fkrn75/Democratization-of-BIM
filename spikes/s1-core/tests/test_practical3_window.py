# SPDX-License-Identifier: MPL-2.0
"""21단계(실무 모델링 3): 창틀·리빌 창 패밀리 `window.framed`(기본 라이브러리).
깊이 있는 창틀·유리·선택 창대/문선, 틀의 벽 안 위치(inset), 층 구성 벽에서의 기준면(지역 -v 면)과 180 뒤집기, 안/밖여닫이 평면 기호, IFC 왕복, 3D 메시 확인.
기존 레시피를 고치지 않았음을 레시피 해시로 고정한다. 합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import queries as Q
from dobim_core.family import library
from dobim_core.family import spec as FS
from dobim_core.family.validate import validate_recipe

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s10-headless" / "src"))
import scenarios as S  # noqa: E402

WA, WB, WC = ("0" * 20 + x for x in ("c1", "c2", "c3"))
WH = "1" + "0" * 19 + "h1"       # 시험용 호스트 벽(두께 200, 길이 8000, 기준 프로젝트 밖)
THICK = "1" + "0" * 19 + "h2"
THIN = "1" + "0" * 19 + "h3"
LY3 = [{"material": "치장벽돌", "thickness": 20}, {"material": "콘크리트", "thickness": 200}, {"material": "석고보드", "thickness": 80}]

# 기존 라이브러리 레시피의 해시(= 기존 타입 id 의 바탕). 이 값이 바뀌면 기존 프로젝트의 타입 id 가 달라진다(설계 메모 22.5)
PINNED = {
    "column.section": "57cb3b1aa8773aabea6f9a0bffd5e9fa1d6565d07229a04f6b4e05b01e238b4e",
    "door.single": "9433372f6973f8922e555a82c622433554bdec7eb3b24aaf23c8cc2264dcd2cc",
    "door.sliding": "cc3e5df6882307674fe99de62089ac906ab99e32964ed1a4355253a0a3902746",
    "wardrobe.builtin": "5deebbb7a1ec04844bb929d4a096e19611c0ca05f0578d311bfd0186d04eb2e1",
    "window.fixed": "cc975765205e2550822586d5bd97f3552e460f29a77a0c7f17b7e3ee12cfa40f",
}


def ok(r):
    assert r["ok"], r
    return r["result"]


def _wall(sess, wid, thickness, y, layers=None, length=8000.0):
    ok(sess.run("wall.create", {"id": wid, "level": F.LV1, "axis": [0, y, length, y], "thickness": thickness, "height": 3000}))
    if layers:
        ok(sess.run("wall.layers", {"id": wid, "layers": layers}))


@pytest.fixture
def bw(base):
    """기준 프로젝트 + 두께 200 호스트 벽 WH(축선 y=6000, 지역 -v = 월드 -y)."""
    _wall(base, WH, 200, 6000.0)
    return base


def _spec(sess, eid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(Q.ifc_spec, eid)


def _body(sess, eid) -> dict:
    return {b["id"].split("[")[0]: b for b in _spec(sess, eid)["body"]}


def _fam(sess, wall):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.hosted_fam, wall)


def _place(sess, eid, host, offset, type_values=None, values=None, rotation=0):
    a = {"id": eid, "family": "window.framed", "type_values": type_values or {}, "values": values or {}, "host": host, "offset": offset}
    if rotation:
        a["rotation"] = rotation
    return sess.run("family.place", a)


# ---------------------------------------------------------------- 라이브러리
def test_기본_라이브러리에_창틀_창이_더해졌고_기존_레시피는_그대로다():
    ids = library.ids()
    assert set(ids) == set(PINNED) | {"window.framed"}
    for fid, h in PINNED.items():
        assert FS.recipe_hash_of(library.load(fid)) == h, f"{fid} 레시피가 바뀌었다(기존 타입 id 가 달라진다)"
    res = validate_recipe(library.load("window.framed"), "full")
    assert res["ok"], res["errors"]


# ---------------------------------------------------------------- 형상: 틀 깊이와 벽 안 위치
def test_기본값은_벽_바깥면에서_100mm_안에_틀을_앉히고_개구부는_벽_전체_두께를_뚫는다(bw):
    ok(_place(bw, WA, WH, 4000))
    b = _body(bw, WA)
    assert set(b) == {"frame", "glass", "stool"}        # 문선은 기본 없음
    # 벽 200: 바깥면(지역 y=-100)에서 100 → 틀 y 0..70, 유리는 틀 가운데 24mm
    assert b["frame"]["range"] == [0.0, 70.0]
    assert b["glass"]["range"] == [23.0, 47.0]
    assert b["frame"]["profile"]["u"] == [-600.0, 600.0] and b["frame"]["profile"]["v"] == [900.0, 2100.0]
    assert b["frame"]["cuts"][0]["profile"]["u"] == [-540.0, 540.0]
    assert b["stool"]["range"] == [70.0, 125.0]          # 틀 안쪽 면에서 벽 안쪽 면(100) + 내민 25
    s = _spec(bw, WA)
    assert s["cut"] == {"x": [-600.0, 600.0], "z": [900.0, 2100.0]}
    assert s["wall_thickness"] == 200.0 and s["predefined"] == "WINDOW"
    assert -100.0 <= b["frame"]["range"][0] and b["frame"]["range"][1] <= 100.0   # 틀은 벽 두께 안(개구부 안)에 있다


def test_inset은_틀의_앞뒤_위치를_정하고_벽이_얇으면_틀이_벽_안에_들어가는_값으로_줄어든다(bw):
    ok(_place(bw, WA, WH, 4000))
    for inset, want in ((0, (-100.0, -30.0)), (60, (-40.0, 30.0)), (130, (30.0, 100.0)), (500, (30.0, 100.0))):   # 500 은 130 으로 줄어든다
        ok(bw.run("family.set", {"id": WA, "values": {"inset": inset}}))
        assert tuple(_body(bw, WA)["frame"]["range"]) == want, inset
    _wall(bw, THIN, 100, 7000.0)
    ok(_place(bw, WB, THIN, 2000))                       # 기본 inset 100 + 틀 70 > 벽 100: 틀이 안쪽 면에 닿게 줄어든다
    assert tuple(_body(bw, WB)["frame"]["range"]) == (-20.0, 50.0)
    ok(bw.run("family.set", {"id": WB, "values": {"inset": 0}}))
    assert tuple(_body(bw, WB)["frame"]["range"]) == (-50.0, 20.0)
    ok(_place(bw, WC, THIN, 4500, type_values={"frame_d": 200}))   # 틀 깊이가 벽보다 크면 벽 두께로 줄어든다
    assert tuple(_body(bw, WC)["frame"]["range"]) == (-50.0, 50.0)


def test_창대와_문선은_선택이고_실내_쪽에_붙는다(bw):
    ok(_place(bw, WA, WH, 4000, type_values={"stool_t": 0, "casing_w": 90, "casing_t": 15}))
    b = _body(bw, WA)
    assert set(b) == {"frame", "glass", "casing_l", "casing_r", "casing_h"}
    assert b["casing_l"]["range"] == [100.0, 115.0]       # 벽 안쪽 면(지역 +v = +100)에서 튀어나온다
    assert b["casing_l"]["profile"]["u"] == [-690.0, -600.0] and b["casing_r"]["profile"]["u"] == [600.0, 690.0]
    assert b["casing_h"]["profile"]["v"] == [2100.0, 2190.0] and b["casing_h"]["profile"]["u"] == [-690.0, 690.0]
    ok(_place(bw, WB, WH, 6000, type_values={"stool_t": 30, "stool_proj": 0}))
    assert _body(bw, WB)["stool"]["range"] == [70.0, 100.0] and _body(bw, WB)["stool"]["profile"]["v"] == [900.0, 930.0]
    ok(_place(bw, WC, WH, 1500, type_values={"stool_t": 30, "stool_proj": 0}, values={"inset": 130}))
    assert "stool" not in _body(bw, WC)                   # 틀이 안쪽 면에 닿고 내민 길이도 0 이면 창대 자리가 없다


def test_여닫이는_문짝_틀을_더하고_멀리언은_고정창만_둘_수_있다(bw):
    ok(_place(bw, WA, WH, 4000, type_values={"operation": "casement_in"}))
    b = _body(bw, WA)
    assert {"frame", "sash", "glass"} <= set(b) and b["sash"]["cuts"]
    assert b["sash"]["profile"]["u"] == [-540.0, 540.0]                      # 문짝 틀은 외틀 안쪽에 맞닿는다
    assert b["glass"]["profile"]["u"] == [-492.0, 492.0]                     # 유리는 문짝 틀(폭 frame_w*0.8 = 48) 안쪽
    r = _place(bw, WB, WH, 6000, type_values={"operation": "casement_out", "mullions": 1})
    assert not r["ok"] and r["error"]["code"] in ("FAMILY_INVALID", "CONSTRAINT"), r
    ok(_place(bw, WB, WH, 6000, type_values={"mullions": 2, "width": 1800}))
    assert len([x for x in _spec(bw, WB)["body"] if x["id"].startswith("mullion")]) == 2
    assert not _place(bw, WC, WH, 1500, type_values={"operation": "casement_in", "width": 500, "frame_w": 120})["ok"]   # 유리 개구 < 100
    assert not _place(bw, WC, WH, 1500, type_values={"operation": "sliding"})["ok"]


# ---------------------------------------------------------------- 평면 기호
def _arc(fam):
    return next(it for it in fam[6] if it[0] == "arc")


def test_평면_기호는_틀_단면과_틀_선_유리선이고_여닫이는_열림_호가_더해진다(bw):
    ok(_place(bw, WA, WH, 4000))
    sym = _fam(bw, WH)[0][6]
    cut = [it for it in sym if it[0] == "poly" and it[-1] == "cut"]
    assert len(cut) == 2 and all(p[1] in (0.0, 70.0) for c in cut for p in c[1])    # 양 끝 틀 단면(지역 y 0..70)
    assert sorted({it[1][1] for it in sym if it[0] == "line"}) == [0.0, 35.0, 70.0]  # 바깥 틀 선·유리 선·안쪽 틀 선
    assert not any(it[0] == "arc" for it in sym)
    # 안여닫이 L: 경첩 왼쪽, 실내(+v)로 열림 0..90
    ok(_place(bw, WB, WH, 1500, type_values={"operation": "casement_in", "width": 1000}, values={"hinge": "L"}))
    a = _arc(next(f for f in _fam(bw, WH) if f[4] == 1500.0))
    assert a[1] == (-440.0, 35.0) and (a[3], a[4]) == (0.0, 90.0) and a[2] == 880.0
    # 밖여닫이 R + 180 뒤집기: 지역 -v 쪽(바깥)으로 열리는 호가 반 바퀴 돌아 +v 쪽이 된다
    ok(_place(bw, WC, WH, 6500, type_values={"operation": "casement_out", "width": 1000}, values={"hinge": "R"}, rotation=180))
    fam = next(f for f in _fam(bw, WH) if f[4] == 6500.0)
    a = _arc(fam)
    assert a[1] == (-440.0, -35.0) and (a[3], a[4]) == (360.0, 450.0)    # 지역 180..270 + 180
    assert any((bx[4], bx[5]) == (-70.0, 0.0) for bx in fam[7])          # 틀 상자도 뒤집힌다(앞쪽 y 0..70 -> -70..0)


# ---------------------------------------------------------------- 층 구성 벽과 뒤집기: IFC 형상으로 확인
def _y_range(ifc_path, gid):
    """IFC 형상의 월드 y 범위(mm). 시험 벽은 +x 방향 축선이라 지역 v 와 월드 y 가 같다."""
    import ifcopenshell.geom as geom
    f = ifcopenshell.open(str(ifc_path))
    st = geom.settings()
    st.set("use-world-coords", True)
    sh = geom.create_shape(st, f.by_guid(gid))
    ys = [sh.geometry.verts[i] * 1000.0 for i in range(1, len(sh.geometry.verts), 3)]
    return min(ys), max(ys)


def test_층_구성_벽_바깥면_기준으로_앉고_180이면_반대면_기준이다_IFC_형상으로_확인(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        y0 = -3000.0
        _wall(b, THICK, 300, y0, layers=LY3, length=6000.0)
        tv = {"stool_t": 0}
        ok(_place(b, WA, THICK, 1500, tv, {"inset": 80}))
        ok(_place(b, WB, THICK, 3000, tv, {"inset": 80}, rotation=180))
        ok(_place(b, WC, THICK, 4500, {"casing_w": 80, "casing_t": 20}, {"inset": 0}))
        out = b.export("w.ifc")
        lo, hi = _y_range(out, WA)                        # 지역 -v = 월드 -y = 바깥(첫 층 쪽): 바깥면(y0-150)에서 80 안쪽
        assert (round(lo), round(hi)) == (round(y0 - 150 + 80), round(y0 - 150 + 80 + 70))
        lo, hi = _y_range(out, WB)                        # 180: 기준면이 +v 면(월드 y0+150)이 된다
        assert (round(lo), round(hi)) == (round(y0 + 150 - 80 - 70), round(y0 + 150 - 80))
        lo, hi = _y_range(out, WC)                        # 틀(바깥면 맞춤) + 실내 쪽 창대(내민 25) + 문선(내민 20)
        assert round(lo) == round(y0 - 150) and round(hi) == round(y0 + 150 + 25)
        f = ifcopenshell.open(str(out))
        w = f.by_guid(WA)
        assert w.is_a("IfcWindow") and w.OverallWidth == 1200 and w.OverallHeight == 1200 and w.PredefinedType == "WINDOW"
        op = next(o for o in f.by_type("IfcOpeningElement") if o.Name == w.Name + "-opening")
        assert op.Representation.Representations[0].Items[0].Depth == 300.0      # 개구부는 벽 두께 전체를 뚫는다(리빌이 생긴다)
    finally:
        b.close()


def test_3D_메시에서_창틀이_벽_바깥면보다_안으로_들어가_있다(tmp_path):
    """dobim_render 의 메시 추출(렌더가 쓰는 형상)로: 새 창은 바깥면에서 100 들어가 깊이 70, 기존 고정창은 벽 가운데 깊이 150."""
    pytest.importorskip("numpy")
    from dobim_render import mesh as M
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        _wall(b, THICK, 300, -3000.0, length=6000.0)
        ok(_place(b, WA, THICK, 3000, {"stool_t": 0}, {"inset": 100}))
        ok(b.run("family.place", {"id": WB, "family": "window.fixed", "host": THICK, "offset": 1000}))
        out = b.export("m.ifc")
        m = M.extract(str(out))
        gids = [g for g, _ in m.elems]
        assert WA in gids and WB in gids
        mm = m.verts * 1000.0

        def yr(gid):
            sel = m.faces[m.face_elem == gids.index(gid)]
            ys = mm[sel.reshape(-1), 1]
            return ys.min(), ys.max()
        wall_front = -3000.0 - 150.0
        lo, hi = yr(WA)
        assert round(lo - wall_front) == 100 and round(hi - lo) == 70
        lo, hi = yr(WB)
        assert round(lo - wall_front) == 75 and round(hi - lo) == 150
    finally:
        b.close()


# ---------------------------------------------------------------- IFC 부여 값과 왕복
def test_IFC_분할_유형과_왕복_해시_불변(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        _wall(b, THICK, 200, -3000.0, length=6000.0)
        ok(_place(b, WA, THICK, 1500, {"mullions": 1, "width": 1500}))
        ok(_place(b, WB, THICK, 4000, {"operation": "casement_in", "casing_w": 70}, {"hinge": "R", "inset": 40}, rotation=180))
        h = b.hashes()
        out = b.export("r.ifc")
        f = ifcopenshell.open(str(out))
        a, w = f.by_guid(WA), f.by_guid(WB)
        assert a.is_a("IfcWindow") and a.IsTypedBy[0].RelatingType.PartitioningType == "DOUBLE_PANEL_VERTICAL"
        assert w.IsTypedBy[0].RelatingType.PartitioningType == "SINGLE_PANEL"
        assert tuple(w.ObjectPlacement.RelativePlacement.RefDirection.DirectionRatios)[:2] == (-1.0, 0.0)
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim")["foreign"] == 0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


def test_호스트_벽_두께를_바꾸면_다시_평가하고_되돌리면_해시가_같다(bw):
    ok(_place(bw, WA, WH, 4000, values={"inset": 60}))
    h0 = bw.eng.state_hash()
    assert tuple(_body(bw, WA)["frame"]["range"]) == (-40.0, 30.0)
    ok(bw.run("wall.set", {"id": WH, "thickness": 300}))
    assert tuple(_body(bw, WA)["frame"]["range"]) == (-90.0, -20.0)      # 새 벽 바깥면(-150)에서 여전히 60
    ok(bw.run("wall.set", {"id": WH, "thickness": 100}))
    assert tuple(_body(bw, WA)["frame"]["range"]) == (-20.0, 50.0)       # 얇아지면 틀이 안쪽 면에 닿게 줄어든다
    ok(bw.run("wall.set", {"id": WH, "thickness": 200}))
    assert bw.eng.state_hash() == h0
