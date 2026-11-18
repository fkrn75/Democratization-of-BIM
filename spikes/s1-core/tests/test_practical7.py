# SPDX-License-Identifier: MPL-2.0
"""25단계(실무 모델링 7): 2~4개 벽 전개 창호, 모서리 판정 확장(교점·조인·허용 오차), 가로대 여러 개, 칸별 열림 방식,
층 경계를 넘는 창(쌓인 벽 함께 자르기 also_cuts), 호스트 적합 규칙(조인 범위·이어지는 벽), 위생기구 종류(PredefinedType).
합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell
import ifcopenshell.util.element  # noqa: F401
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import solids as SO

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402


def gid(c, n):
    return c + f"{n:021d}"


def params(sess, eid, project=None):
    kw = {"project": project} if project else {}
    return sess.run("element.get", {"id": eid}, **kw)["result"]["params"]


def props(sess, eid):
    return sess.run("element.get", {"id": eid})["result"].get("psets") or {}


def walls(sess, axes, level=F.LV1, height=3000, start=10):
    ids = []
    for i, ax in enumerate(axes):
        w = gid("1", start + i)
        r = sess.run("wall.create", {"id": w, "level": level, "axis": ax, "thickness": 200, "height": height})
        assert r["ok"], r
        ids.append(w)
    return ids


def openings(sess, w):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.wall_openings, w)


SQ = [[0, 10000, 6000, 10000], [6000, 10000, 6000, 14000], [6000, 14000, 0, 14000], [0, 14000, 0, 10000]]   # 시계 반대 방향 사각형 네 벽
SF = {"height": 2400, "bay_max": 1200}


# ---------------------------------------------------------------- 1. 2~4개 벽 전개 창호
def test_세_벽_전개_창호는_가운데_벽을_두_모서리_사이로_채운다(base):
    a, b, c, _ = walls(base, SQ)
    ids = [gid("b", 1), gid("b", 2), gid("b", 3)]
    r = base.run("family.place_corner", {"ids": ids, "family": "window.storefront", "walls": [a, b, c], "legs": [2000, 4000, 1500], "type_values": SF})
    assert r["ok"], r
    assert r["result"]["corners"] == [[6000.0, 10000.0], [6000.0, 14000.0]] and r["result"]["corner"] == [6000.0, 10000.0]
    pa, pb, pc = (params(base, i) for i in ids)
    assert pa["offset"] == pytest.approx(5000) and pa["values"]["end_r"] == "corner" and pa["values"]["end_l"] == "jamb"
    assert pb["offset"] == pytest.approx(2000) and pb["width"] == pytest.approx(4000)
    assert pb["values"]["end_l"] == "corner" and pb["values"]["end_r"] == "corner"
    assert pc["offset"] == pytest.approx(750) and pc["values"]["end_l"] == "corner" and pc["values"]["end_r"] == "jamb"
    ps = props(base, ids[1])
    if ps:
        assert ps["DoBIM_CornerUnit"]["Unit"] == ",".join(ids) and ps["DoBIM_CornerUnit"]["Partner"] == f"{ids[0]},{ids[2]}"
    # 실행 취소는 세 다리를 함께 없앤다
    assert base.run("history.undo", {})["ok"]
    assert not base.run("element.query", {"kind": "finst"})["result"]["items"]


def test_네_벽_전개와_가운데_다리_길이_검사(base):
    ws = walls(base, SQ)
    bad = base.run("family.place_corner", {"family": "window.storefront", "walls": ws, "legs": [1000, 4000, 5000, 1000], "type_values": SF})
    assert not bad["ok"] and bad["error"]["path"] == "/legs/2" and bad["error"]["details"]["expected"] == pytest.approx(6000)
    r = base.run("family.place_corner", {"family": "window.storefront", "walls": ws, "legs": [1000, 4000, 6000, 1000], "type_values": SF,
                                         "values_by": [{}, {}, {"door_pos": 0}, {}]})
    assert r["ok"], r
    assert len(r["result"]["ids"]) == 4 and len(r["result"]["corners"]) == 3
    pd = params(base, r["result"]["ids"][3])
    assert pd["offset"] == pytest.approx(500) and pd["values"]["end_l"] == "corner" and pd["values"]["end_r"] == "jamb"
    # 개수가 다르거나 순서가 맞지 않으면 거절
    bad = base.run("family.place_corner", {"family": "window.storefront", "walls": ws[:3], "legs": [1000, 4000]})
    assert not bad["ok"]
    bad = base.run("family.place_corner", {"family": "window.storefront", "walls": [ws[0], ws[2]], "legs": [1000, 1000]})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"                  # 평행: 모서리 없음


# ---------------------------------------------------------------- 2. 모서리 판정: 축선 교점·허용 오차·조인
def test_축선이_닿지_않아도_교점이_허용_오차_안이면_모서리다(base):
    a, b = walls(base, [[0, 10000, 5950, 10000], [6000, 10050, 6000, 14000]])   # 끝이 교점(6000, 10000)에서 50mm 모자란다
    r = base.run("family.place_corner", {"family": "window.storefront", "walls": [a, b], "legs": [2000, 1500], "type_values": SF})
    assert r["ok"], r
    assert r["result"]["corner"] == [6000.0, 10000.0]
    pa, pb = (params(base, i) for i in r["result"]["ids"])
    assert pa["offset"] == pytest.approx(5000) and pb["offset"] == pytest.approx(700)   # 다리는 교점에서 잰다(축선 끝이 아니라)
    assert base.run("history.undo", {})["ok"]
    bad = base.run("family.place_corner", {"family": "window.storefront", "walls": [a, b], "legs": [2000, 1500], "tol": 10})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"
    assert bad["error"]["details"]["distance"] == {a: 50.0, b: 50.0} and bad["error"]["details"]["reach"] == 10.0


def test_조인된_L은_조인_규칙으로_모서리를_찾는다(base):
    a, b = walls(base, [[0, 10000, 5400, 10000], [6000, 10600, 6000, 14000]])   # 600mm 모자란다: 기본 허용(300) 밖
    bad = base.run("family.place_corner", {"family": "window.storefront", "walls": [a, b], "legs": [2000, 1500], "type_values": SF})
    assert not bad["ok"] and bad["error"]["details"]["joined"] is False
    assert base.run("join.set", {"a": a, "b": b})["ok"]
    r = base.run("family.place_corner", {"family": "window.storefront", "walls": [a, b], "legs": [2000, 1500], "type_values": SF})
    assert r["ok"], r
    assert r["result"]["corner"] == [6000.0, 10000.0]


# ---------------------------------------------------------------- 3. 가로대 여러 개
def test_가로대_목록은_transom_값으로_풀리고_검사된다(base):
    for w in (F.W2, F.W3):
        assert base.run("wall.set", {"id": w, "height": 3600})["ok"]
    r = base.run("family.place", {"family": "window.storefront", "type_values": {"width": 3000, "height": 3300}, "transoms": [2100, 2700],
                                  "host": F.W3, "offset": 3000})
    assert r["ok"], r
    tp = params(base, r["result"]["type"])
    assert tp["type_values"]["transom"] == 2100 and tp["type_values"]["transom2"] == 2700 and tp["type_values"]["transom3"] == 0
    tps = props(base, r["result"]["type"])
    if tps:
        assert tps["DoBIM_StorefrontParams"]["TransomCount"] == 2
    pv = base.run("family.preview", {"type": r["result"]["type"]})["result"]
    names = [b["id"] for b in pv["body"]] if pv["body_count"] <= 24 else []
    if names:
        assert "transom_bar" in names and "transom_bar2" in names and "glass_lite2" in names
    # 기존 단일 값(transom)은 그대로 받는다
    r = base.run("family.place", {"family": "window.storefront", "type_values": {"width": 1500, "height": 2700, "transom": 2100}, "host": F.W3, "offset": 1000})
    assert r["ok"], r
    # 너무 가까운 가로대·내림차순·개수 초과·함께 주기는 거절
    for tr, tv in (([2100, 2200], {}), ([2700, 2100], {}), ([600, 1200, 1800, 2400, 3000], {}), ([2100], {"transom": 2000})):
        bad = base.run("family.place", {"family": "window.storefront", "type_values": {"width": 1200, "height": 3300, **tv}, "transoms": tr,
                                        "host": F.W2, "offset": 2000})
        assert not bad["ok"] and bad["error"]["path"] == "/transoms", (tr, bad)
    bad = base.run("family.place", {"family": "window.framed", "transoms": [1000], "host": F.W2, "offset": 2000})
    assert not bad["ok"] and bad["error"]["details"]["max"] == 0                    # 가로대 매개변수가 없는 패밀리


def test_전개_창호에도_가로대_목록을_준다(base):
    assert base.run("wall.set", {"id": F.W1, "height": 3600})["ok"]
    assert base.run("wall.set", {"id": F.W2, "height": 3600})["ok"]
    r = base.run("family.place_corner", {"family": "window.storefront", "walls": [F.W1, F.W2], "legs": [2500, 2000],
                                         "type_values": {"height": 3300}, "transoms": [2100, 2700]})
    assert r["ok"], r
    for i in r["result"]["ids"]:
        assert params(base, params(base, i)["type"])["type_values"]["transom2"] == 2700


# ---------------------------------------------------------------- 4. 칸별 열림 방식
def _panels(f, guid):
    t = ifcopenshell.util.element.get_type(f.by_guid(guid))
    return t, [(p.OperationType, p.PanelPosition) for p in (t.HasPropertySets or ()) if p.is_a("IfcWindowPanelProperties")]


def test_칸별_열림_방식은_IFC_창_패널에_칸마다_나가고_왕복한다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        i1, i2, i3 = gid("b", 31), gid("b", 32), gid("b", 33)
        r = b.run("family.place", {"id": i1, "family": "window.framed", "type_values": {"width": 2700, "height": 1500, "mullions": 2, "bay2": "project_out"},
                                   "host": F.W3, "offset": 1700})
        assert r["ok"], r
        r = b.run("family.place", {"id": i2, "family": "window.framed", "type_values": {"width": 1800, "height": 1500, "operation": "project_out",
                                                                                        "mullions": 1, "bay1": "fixed"}, "host": F.W1, "offset": 4200})
        assert r["ok"], r
        r = b.run("family.place", {"id": i3, "family": "window.framed", "type_values": {"width": 1200, "operation": "casement_in", "bay1": "fixed"},
                                   "host": F.W2, "offset": 2000})
        assert r["ok"], r                                                            # 칸이 없으면 bay 값은 무시된다
        bad = b.run("family.place", {"family": "window.framed", "type_values": {"operation": "casement_in", "mullions": 1}, "host": F.W2, "offset": 3000})
        assert not bad["ok"] and bad["error"]["code"] == "FAMILY_INVALID"           # 여닫이 창의 멀리언은 여전히 거절
        h = b.hashes()
        f = ifcopenshell.open(str(b.export("t.ifc")))
        t, pn = _panels(f, i1)
        assert t.PartitioningType == "TRIPLE_PANEL_VERTICAL"
        assert pn == [("FIXEDCASEMENT", "LEFT"), ("TOPHUNG", "MIDDLE"), ("FIXEDCASEMENT", "RIGHT")]
        t, pn = _panels(f, i2)
        assert t.PartitioningType == "DOUBLE_PANEL_VERTICAL" and pn == [("FIXEDCASEMENT", "LEFT"), ("TOPHUNG", "RIGHT")]
        assert _panels(f, i3)[1] == [("NOTDEFINED", "MIDDLE")]
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


def test_혼합_창은_프로젝트_칸에만_창짝과_기호가_생긴다(base):
    v = base.run("view.create", {"name": "P", "type": "plan", "level": F.LV1})["result"]["id"]
    base.eng.sync()
    base.eng._touch_pending()
    before = base.eng.g.q(D.view_lines, v)
    r = base.run("family.place", {"family": "window.framed", "type_values": {"width": 2700, "height": 1500, "mullions": 2, "bay2": "project_out"},
                                  "host": F.W3, "offset": 1700})
    assert r["ok"], r
    pv = base.run("family.preview", {"type": r["result"]["type"]})["result"]
    ids = [x["id"] for x in pv["body"]]
    assert "bay_sash2" in ids and "bay_sash1" not in ids and "glass" not in ids and sum(x.startswith("mullion") for x in ids) == 2
    base.eng.sync()
    base.eng._touch_pending()
    new = [s for s in base.eng.g.q(D.view_lines, v) if s not in before]
    tilted = [s for s in new if abs(s[1] - s[3]) > 1e-6 and abs(s[2] - s[4]) > 1e-6]
    assert len(tilted) == 1                                                          # 프로젝트 칸 하나의 젖힘 선


# ---------------------------------------------------------------- 5. 층 경계를 넘는 창
def test_구속_벽의_창은_레벨을_넘고_위층_평면에_그려진다(base):
    w = gid("1", 40)
    assert base.run("wall.create", {"id": w, "level": F.LV1, "axis": [0, 8000, 6000, 8000], "top_level": F.LV2, "top_offset": 3300})["ok"]
    r = base.run("family.place", {"id": gid("b", 40), "family": "window.framed", "type_values": {"width": 1000, "height": 2400}, "values": {"sill": 2500},
                                  "host": w, "offset": 3000})
    assert r["ok"], r                                                                # 2500~4900: LV2(3300)를 넘는다
    v2 = base.run("view.create", {"name": "P2", "type": "plan", "level": F.LV2})["result"]["id"]
    base.eng.sync()
    base.eng._touch_pending()
    lines = base.eng.g.q(D.view_lines, v2)
    assert [s for s in lines if s[0] == "symbol" and abs(s[2] - 8000) < 150 and 2500 <= min(s[1], s[3]) and max(s[1], s[3]) <= 3500]


def _stacked(sess):
    lo, hi = gid("1", 50), gid("1", 51)
    assert sess.run("wall.create", {"id": lo, "level": F.LV1, "axis": [0, 8000, 6000, 8000], "thickness": 200, "height": 3000})["ok"]
    assert sess.run("wall.create", {"id": hi, "level": F.LV2, "axis": [0, 8000, 6000, 8000], "thickness": 200, "height": 3000})["ok"]
    return lo, hi


WIN = {"family": "window.framed", "type_values": {"width": 1200, "height": 1500}, "values": {"sill": 2500}}   # 2500~4000: 아래 벽 위 끝(3000)·슬래브(3000~3300)·위 벽 아래(3300) 넘는다


def test_쌓인_벽은_also_cuts_로_함께_자른다(base):
    lo, hi = _stacked(base)
    bad = base.run("family.place", {**WIN, "host": lo, "offset": 3000})
    assert not bad["ok"] and bad["error"]["path"] == "/elevation"
    d = bad["error"]["details"]
    assert d["unserved"] == ["top"] and d["overflow"]["top"] == pytest.approx(1000) and d["host"]["z"] == [0.0, 3000.0]
    i = gid("b", 50)
    r = base.run("family.place", {**WIN, "id": i, "host": lo, "offset": 3000, "also_cuts": [hi]})
    assert r["ok"], r
    assert params(base, i)["also_cuts"] == [hi]
    assert openings(base, hi) == ((2400.0, 3600.0, 3300.0, 4000.0),)                 # 위 벽: 아래 끝 3300 ~ 창 위 4000
    assert openings(base, lo) == ((2400.0, 3600.0, 2500.0, 3000.0),)
    # 증분 계산이 전체 계산과 같다(위층 평면·입면)
    v2 = base.run("view.create", {"name": "P2", "type": "plan", "level": F.LV2})["result"]["id"]
    ve = base.run("view.create", {"name": "S", "type": "elevation", "dir": "S"})
    assert ve["ok"], ve
    vids = [v2, ve["result"]["id"]]
    assert base.run("family.set", {"id": i, "offset": 2500})["ok"]
    for v in vids:
        base.eng.sync()
        base.eng._touch_pending()
        assert base.eng.g.q(D.view_lines, v) == s3lib.RefGraph(s3lib._Inputs(base.eng)).q(D.view_lines, v)
    assert openings(base, hi) == ((1900.0, 3100.0, 3300.0, 4000.0),)
    # 함께 자르는 벽은 지우거나 나눌 수 없다. 떼면 다시 높이 검사에 걸린다
    bad = base.run("element.delete", {"id": hi})
    assert not bad["ok"] and bad["error"]["details"]["instances"] == [i]
    assert not base.run("wall.split", {"id": hi, "at": 1000})["ok"]
    bad = base.run("family.set", {"id": i, "also_cuts": []})
    assert not bad["ok"] and bad["error"]["path"] == "/elevation"
    # 평행하지 않거나 겹치지 않는 벽은 거절
    bad = base.run("family.set", {"id": i, "also_cuts": [F.W2]})
    assert not bad["ok"] and bad["error"]["path"] == "/also_cuts/0"
    # 호스트를 지우면 인스턴스와 함께 사라지고 위 벽 개구부도 없어진다
    assert base.run("element.delete", {"id": lo})["ok"]
    assert openings(base, hi) == ()
    assert base.run("element.delete", {"id": hi})["ok"]


def test_쌓인_벽_개구부는_IFC_에_나가고_왕복한다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        lo, hi = _stacked(b)
        i = gid("b", 51)
        assert b.run("family.place", {**WIN, "id": i, "host": lo, "offset": 3000, "also_cuts": [hi]})["ok"]
        h = b.hashes()
        f = ifcopenshell.open(str(b.export("t.ifc")))
        voids = [r.RelatedOpeningElement for r in f.by_type("IfcRelVoidsElement") if r.RelatingBuildingElement.GlobalId == hi]
        assert len(voids) == 1 and not voids[0].HasFillings                          # 채움 관계는 호스트 개구부 하나뿐
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim")["foreign"] == 0 and b.origins("imp.dobim")["detached"] == 0
        hi_ = b.hashes("imp.dobim")
        assert {k: v for k, v in hi_.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert params(b, i, "imp.dobim")["also_cuts"] == [hi]
        f2 = ifcopenshell.open(str(b.export("t2.ifc", project="imp.dobim")))
        assert len(f2.by_type("IfcOpeningElement")) == len(f.by_type("IfcOpeningElement"))
    finally:
        b.close()


# ---------------------------------------------------------------- 6. 호스트 적합 규칙
def test_조인된_L_벽의_모서리_연장까지는_개구부를_받는다(base):
    # W1(0,0 -> 6000,0)은 W2 와 L 조인: id 가 작은 W1 이 W2 반 두께(100)만큼 6100 까지 나간다
    r = base.run("family.place", {"family": "window.framed", "type_values": {"width": 1000}, "host": F.W1, "offset": 5550})
    assert r["ok"], r                                                                # 5050~6050: 예전에는 축선(6000) 밖이라 거절
    bad = base.run("family.place", {"family": "window.framed", "type_values": {"width": 1000}, "host": F.W1, "offset": 5650})
    assert not bad["ok"] and bad["error"]["path"] == "/offset"
    d = bad["error"]["details"]
    assert d["host"]["range"] == [0.0, 6100.0] and d["overflow"] == {"end": pytest.approx(49.5)} and d["joined"] == [F.W2]
    # 조인을 떼면 조인 범위가 줄지만 이미 놓인 창은 그대로(형상은 벽 범위로 자른다)
    assert base.run("join.clear", {"a": F.W1, "b": F.W2})["ok"]
    assert params(base, r["result"]["id"])["offset"] == 5550


def test_같은_선으로_이어지는_벽은_also_cuts_로_넘어간다(base):
    a, b2 = walls(base, [[0, 9000, 3000, 9000], [3000, 9000, 6000, 9000]], start=60)
    bad = base.run("family.place", {"family": "window.framed", "type_values": {"width": 1200}, "host": a, "offset": 2800})
    assert not bad["ok"] and bad["error"]["details"]["unserved"] == ["end"]
    r = base.run("family.place", {"family": "window.framed", "type_values": {"width": 1200}, "host": a, "offset": 2800, "also_cuts": [b2]})
    assert r["ok"], r
    assert openings(base, b2) == ((0.0, 400.0, 900.0, 2100.0),)
    assert openings(base, a) == ((2200.0, 3000.0, 900.0, 2100.0),)


# ---------------------------------------------------------------- 7. 위생기구 종류
SAN = {"toilet": "TOILETPAN", "urinal": "URINAL", "bidet": "BIDET", "washbasin": "WASHHANDBASIN", "sink": "SINK",
       "bath": "BATH", "shower": "SHOWER", "cistern": "CISTERN", "sanitary_terminal": None}
BOX = {"level": F.LV1, "form": "prism", "polygon": [[0, 0], [600, 0], [600, 400], [0, 400]], "z0": 0, "z1": 740}


def test_위생기구_종류는_IFC4_형식이다():
    f = ifcopenshell.file(schema="IFC4")
    for cat, pre in SAN.items():
        c, p, o = SO.SOLID_CATEGORIES[cat]
        assert (c, p, o) == ("IfcSanitaryTerminal", pre, None)
        e = f.create_entity(c)
        if p:
            e.PredefinedType = p
    assert set(SAN) == set(SO.SANITARY_CATEGORIES)


def test_위생기구_종류는_PredefinedType_으로_나가고_바꾸면_따라간다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ids = {}
        for k, cat in enumerate(SAN, 1):
            ids[cat] = gid("f", 70 + k)
            r = b.run("solid.create", {**BOX, "id": ids[cat], "name": cat, "category": cat})
            assert r["ok"], r
        f = ifcopenshell.open(str(b.export("t.ifc")))
        for cat, pre in SAN.items():
            assert f.by_guid(ids[cat]).PredefinedType == pre
        # 같은 클래스 안에서 범주를 바꾸면 형식도 따라간다(종류 미지정으로 되돌리면 비운다)
        assert b.run("solid.set", {"id": ids["toilet"], "category": "washbasin"})["ok"]
        assert b.run("solid.set", {"id": ids["bath"], "category": "sanitary_terminal"})["ok"]
        assert b.run("solid.set", {"id": ids["sanitary_terminal"], "category": "shower"})["ok"]
        h = b.hashes()
        f = ifcopenshell.open(str(b.export("t2.ifc")))
        assert f.by_guid(ids["toilet"]).PredefinedType == "WASHHANDBASIN"
        assert f.by_guid(ids["bath"]).PredefinedType is None
        assert f.by_guid(ids["sanitary_terminal"]).PredefinedType == "SHOWER"
        assert not b.run("solid.set", {"id": ids["sink"], "category": "boiler"})["ok"]   # 다른 클래스로는 못 바꾼다(기존 규칙)
        r = b.import_ifc("t2.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()
