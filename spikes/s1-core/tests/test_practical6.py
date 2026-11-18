# SPDX-License-Identifier: MPL-2.0
"""24단계(실무 모델링 6): 큰 창호·복합 창호·모서리 창호, 창 열림 방식(프로젝트·미닫이)과 IFC 창 패널,
여러 층에 걸친 벽(상단 레벨 구속), 자동 난간 id. 합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core.family import library
from dobim_core.family.validate import validate_recipe

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

W5 = "1" + "0" * 20 + "5"
RUN = [1000, 1000, 4000, 1000]


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


def view(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def params(sess, eid):
    return sess.run("element.get", {"id": eid})["result"]["params"]


def diagonal(s):
    """호를 이루는 사선 조각인가(평면 선은 (종류, x1, y1, x2, y2, ...))."""
    return abs(s[1] - s[3]) > 1e-6 and abs(s[2] - s[4]) > 1e-6


def gid(c, n):
    return c + f"{n:021d}"


# ---------------------------------------------------------------- 패밀리 라이브러리
@pytest.mark.parametrize("fid", ["window.framed", "window.storefront", "door.single", "door.sliding"])
def test_라이브러리_레시피가_전체_검증을_통과한다(fid):
    res = validate_recipe(library.load(fid), "full")
    assert res["ok"], res["errors"]


def test_치수_상한이_넓어졌다(base):
    # 예전 상한(창 폭 3000·높이 2400, 미닫이문 폭 4000, 외여닫이 폭 1500) 밖의 값
    assert base.run("wall.set", {"id": F.W3, "height": 6000})["ok"]
    r = base.run("family.place", {"family": "window.framed", "type_values": {"width": 450, "height": 1200}, "host": F.W3, "offset": 600})
    assert r["ok"], r
    r = base.run("family.place", {"family": "window.framed", "type_values": {"width": 1500, "height": 5400}, "values": {"sill": 300}, "host": F.W2, "offset": 2000})
    assert not r["ok"]                                                   # 벽(3000) 밖: 호스트 높이 검사는 그대로
    assert base.run("wall.set", {"id": F.W2, "height": 6000})["ok"]
    r = base.run("family.place", {"family": "window.framed", "type_values": {"width": 1500, "height": 5400}, "values": {"sill": 300}, "host": F.W2, "offset": 2000})
    assert r["ok"], r
    r = base.run("family.place", {"family": "door.sliding", "type_values": {"width": 1800, "height": 3000, "panels": 2}, "host": F.W3, "offset": 4500})
    assert r["ok"], r
    r = base.run("family.place", {"family": "window.framed", "type_values": {"width": 7000}, "host": F.W3, "offset": 3000})
    assert not r["ok"] and r["error"]["code"] == "FAMILY_INVALID"   # 6000 넘는 창은 복합 창호로


# ---------------------------------------------------------------- 열림 방식 + IFC 창 패널
def _type_panels(f, inst):
    t = ifcopenshell.util.element.get_type(inst)
    return t, [(p.OperationType, p.PanelPosition) for p in (t.HasPropertySets or ()) if p.is_a("IfcWindowPanelProperties")]


def test_프로젝트창_미닫이창은_IfcWindow_와_창_패널로_나가고_왕복한다(tmp_path):
    import ifcopenshell.util.element  # noqa: F401
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ids = {k: gid("b", i) for i, k in enumerate(("fixed", "project", "slide", "case"), 1)}
        assert b.run("family.place", {"id": ids["project"], "family": "window.framed", "type_values": {"width": 900, "height": 600, "operation": "project_out"},
                                      "values": {"sill": 1500}, "host": F.W1, "offset": 4000})["ok"]
        r = b.run("family.place", {"id": ids["slide"], "family": "window.framed", "type_values": {"width": 2400, "height": 2200, "operation": "sliding", "frame_d": 100},
                                   "values": {"sill": 0}, "host": F.W2, "offset": 2000})
        assert r["ok"], r
        assert b.run("family.place", {"id": ids["fixed"], "family": "window.framed", "type_values": {"mullions": 2, "width": 2400}, "host": F.W3, "offset": 1300})["ok"]
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        exp = {"project": ("SINGLE_PANEL", [("TOPHUNG", "MIDDLE")]),
               "slide": ("DOUBLE_PANEL_VERTICAL", [("SLIDINGHORIZONTAL", "LEFT"), ("SLIDINGHORIZONTAL", "RIGHT")]),
               "fixed": ("TRIPLE_PANEL_VERTICAL", [("FIXEDCASEMENT", "LEFT"), ("FIXEDCASEMENT", "MIDDLE"), ("FIXEDCASEMENT", "RIGHT")])}
        for k, (part, panels) in exp.items():
            e = f.by_guid(ids[k])
            assert e.is_a() == "IfcWindow"                               # 미닫이창도 IfcDoor 가 아니다
            t, got = _type_panels(f, e)
            assert t.is_a("IfcWindowType") and t.PartitioningType == part
            assert got == panels
        assert len(f.by_type("IfcDoor")) == 2                            # 고정 프로젝트의 문 2개뿐
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim")["foreign"] == 0 and b.origins("imp.dobim")["detached"] == 0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        out2 = b.export("t2.ifc", project="imp.dobim")                  # 다시 내보내도 패널이 한 벌이다(겹치지 않는다)
        f2 = ifcopenshell.open(str(out2))
        assert _type_panels(f2, f2.by_guid(ids["slide"]))[1] == exp["slide"][1]
        assert len(f2.by_type("IfcWindowPanelProperties")) == len(f.by_type("IfcWindowPanelProperties")) == 6
        # 타입을 지우면(인스턴스를 지운 뒤) 패널도 사라진다
        t_id = b.run("element.get", {"id": ids["project"]})["result"]["params"]["type"]
        assert b.run("element.delete", {"id": ids["project"]})["ok"]
        assert b.run("element.delete", {"id": t_id})["ok"]
        f3 = ifcopenshell.open(str(b.export("t3.ifc")))
        assert len(f3.by_type("IfcWindowPanelProperties")) == 5
    finally:
        b.close()


def test_미닫이창_평면은_두_짝_선을_그리고_여닫이_호가_없다(base):
    v = view(base, name="P", type="plan", level=F.LV1)
    before = lines_of(base, v)
    r = base.run("family.place", {"family": "window.framed", "type_values": {"width": 2000, "height": 1500, "operation": "sliding", "frame_d": 100},
                                  "host": F.W2, "offset": 2000})
    assert r["ok"], r
    new = [s for s in lines_of(base, v) if s not in before]
    assert new and not [s for s in new if diagonal(s)]                   # 여닫이 호(사선 조각)가 없다
    # W2 는 x=6000 세로 벽: 짝 선은 y 방향으로 길고 서로 다른 x 에 있다(두 짝이 깊이로 어긋난다)
    long_y = {round(s[1], 3) for s in new if abs(s[1] - s[3]) < 1e-6 and abs(s[2] - s[4]) > 800}
    assert len(long_y) >= 2


# ---------------------------------------------------------------- 복합 창호
def test_복합_창호는_칸을_나누고_문을_넣는다(base):
    assert base.run("wall.set", {"id": F.W1, "height": 3600})["ok"]
    v = view(base, name="P", type="plan", level=F.LV1)
    before = lines_of(base, v)
    r = base.run("family.place", {"id": gid("b", 9), "family": "window.storefront",
                                  "type_values": {"width": 5000, "height": 3150, "door_w": 1800, "door_leaves": 2, "transom": 2500, "bay_max": 1200},
                                  "values": {"door_pos": 1500, "sill": 150}, "host": F.W1, "offset": 3500})
    assert r["ok"], r
    p = params(base, gid("b", 9))
    assert p["width"] == 5000 and p["height"] == 3150
    props = base.run("element.get", {"id": gid("b", 9)})["result"]
    ps = props.get("psets") or {}
    if ps:
        assert ps["DoBIM_WindowParams"]["DoorWidth"] == 1800 and ps["DoBIM_WindowParams"]["Bays"] == 4
    new = [s for s in lines_of(base, v) if s not in before]
    left = [s for s in new if diagonal(s) and min(s[1], s[3]) < 2900]   # 양여닫이: 문 개구(x 2450~4250)의 두 호
    right = [s for s in new if diagonal(s) and max(s[1], s[3]) > 3400]
    assert left and right
    # 문이 창호 안 폭보다 넓으면 문 없이 고정창만(문 폭 0 으로 보고)
    r = base.run("family.place", {"family": "window.storefront", "type_values": {"width": 1500, "height": 2400, "door_w": 1800}, "host": F.W3, "offset": 1000})
    assert r["ok"], r
    # 문짝이 450 미만이면 거절
    r = base.run("family.place", {"family": "window.storefront", "type_values": {"width": 3000, "height": 2400, "door_w": 800, "door_leaves": 2}, "host": F.W3, "offset": 4000})
    assert not r["ok"]


def test_복합_창호_IFC는_사용자_정의_분할과_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        i1 = gid("b", 10)
        r = b.run("family.place", {"id": i1, "family": "window.storefront", "type_values": {"width": 3600, "height": 2700, "door_w": 900},
                                   "values": {"door_pos": 1200, "open": "out", "swing": "R"}, "host": F.W1, "offset": 3800})
        assert r["ok"], r
        h = b.hashes()
        f = ifcopenshell.open(str(b.export("t.ifc")))
        e = f.by_guid(i1)
        t, panels = _type_panels(f, e)
        assert e.is_a() == "IfcWindow" and t.PartitioningType == "USERDEFINED" and t.UserDefinedPartitioningType == "STOREFRONT_DOOR"
        assert panels == [("FIXEDCASEMENT", "MIDDLE"), ("OTHEROPERATION", "MIDDLE")]
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


# ---------------------------------------------------------------- 모서리 창호
def test_모서리_창호는_두_다리를_모서리에_붙인다(base):
    ids = [gid("b", 21), gid("b", 22)]
    r = base.run("family.place_corner", {"ids": ids, "family": "window.storefront", "walls": [F.W1, F.W2], "legs": [2500, 2000],
                                         "type_values": {"height": 2400, "bay_max": 1200}, "values_b": {}})
    assert r["ok"], r
    assert r["result"]["corner"] == [6000.0, 0.0]
    pa, pb = params(base, ids[0]), params(base, ids[1])
    assert pa["offset"] == pytest.approx(6000 - 1250) and pa["values"]["end_r"] == "corner" and pa["values"]["end_l"] == "jamb"
    assert pb["offset"] == pytest.approx(1000) and pb["values"]["end_l"] == "corner" and pb["values"]["end_r"] == "jamb"
    assert pa["width"] == 2500 and pb["width"] == 2000
    # 직각이 아니거나 맞닿지 않으면 거절
    bad = base.run("family.place_corner", {"family": "window.storefront", "walls": [F.W1, F.W3], "legs": [1000, 1000]})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"
    bad = base.run("family.place_corner", {"family": "window.framed", "walls": [F.W2, F.W3], "legs": [1000, 1000]})
    assert not bad["ok"] and bad["error"]["path"] == "/family"
    # 뒤집으면 지역 x 가 반대라 모서리 끝 이름이 바뀐다
    r = base.run("family.place_corner", {"family": "window.storefront", "walls": [F.W2, F.W3], "legs": [1500, 1500], "flip": [True, False],
                                         "type_values": {"height": 2400}})
    assert r["ok"], r
    pa = params(base, r["result"]["ids"][0])
    assert pa["values"]["end_l"] == "corner" and pa["rotation"] == 180.0
    # 실행 취소는 두 다리를 함께 없앤다
    n = len(base.run("element.query", {"kind": "finst"})["result"]["items"])
    assert base.run("history.undo", {})["ok"]
    assert len(base.run("element.query", {"kind": "finst"})["result"]["items"]) == n - 2


# ---------------------------------------------------------------- 여러 층에 걸친 벽
def test_상단_레벨_구속은_높이를_계산하고_검사한다(base):
    r = base.run("wall.create", {"id": W5, "level": F.LV1, "axis": [0, 8000, 6000, 8000], "top_level": F.LV2, "top_offset": 3300})
    assert r["ok"], r
    p = params(base, W5)
    assert p["height"] == 6600.0 and p["top_level"] == F.LV2 and p["top_offset"] == 3300.0
    bad = base.run("wall.create", {"level": F.LV1, "axis": [0, 9000, 6000, 9000], "top_level": F.LV2, "height": 3000})
    assert not bad["ok"]
    bad = base.run("wall.create", {"level": F.LV2, "axis": [0, 9000, 6000, 9000], "top_level": F.LV1})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"     # 상단이 바닥보다 낮다
    # 바닥 오프셋을 바꾸면 상단은 그대로, 높이가 바뀐다
    assert base.run("wall.set", {"id": W5, "base_offset": -300})["ok"]
    assert params(base, W5)["height"] == 6900.0
    assert base.run("wall.set", {"id": W5, "top_offset": 0})["ok"]
    p = params(base, W5)
    assert p["height"] == 3600.0 and "top_offset" not in p
    # 분할하면 끝 쪽도 같은 구속
    assert base.run("wall.split", {"id": W5, "at": 2000})["ok"]
    news = [x for x in base.run("schedule.rows", {"kind": "wall"})["result"]["items"] if x[1].endswith("-b")]
    assert params(base, news[0][0])["top_level"] == F.LV2
    # 상단이 묶인 레벨은 지울 수 없다(벽이 없는 레벨이어도)
    lv3 = gid("3", 9)
    assert base.run("level.create", {"id": lv3, "name": "R", "elevation": 9000})["ok"]
    assert base.run("wall.set", {"id": W5, "top_level": lv3})["ok"]
    assert params(base, W5)["height"] == 9300.0
    bad = base.run("element.delete", {"id": lv3})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"
    # height 를 직접 주면 구속을 뗀다
    r = base.run("wall.set", {"id": W5, "height": 3000})
    assert r["ok"] and "top_level" not in params(base, W5)
    assert base.run("element.delete", {"id": lv3})["ok"]


def test_두_층_벽은_위층_평면에도_절단된다(base):
    v2 = view(base, name="P2", type="plan", level=F.LV2)
    v1 = view(base, name="P1", type="plan", level=F.LV1)

    def cuts_at_y(vid, y):
        return [s for s in lines_of(base, vid) if s[0] == "cut" and abs(s[2] - s[4]) < 1e-6 and abs(s[2] - y) < 150 and abs(s[1] - s[3]) > 1000]

    before2 = lines_of(base, v2)
    assert base.run("wall.create", {"id": W5, "level": F.LV1, "axis": [0, 8000, 6000, 8000], "top_level": F.LV2, "top_offset": 3300})["ok"]
    assert cuts_at_y(v1, 8000) and cuts_at_y(v2, 8000)
    # 1층 벽(높이 3000)은 2층 평면(절단 3300+1200)에 나오지 않는다(투영도 더하지 않는다): 그 밖의 2층 선은 그대로
    rest = [s for s in lines_of(base, v2) if not (abs(s[2] - 8000) < 150 and abs(s[4] - 8000) < 150)]
    assert sorted(rest) == sorted(s for s in before2)
    # 증분 계산이 전체 계산과 같다
    assert base.run("wall.set", {"id": W5, "top_offset": 500})["ok"]   # 상단 3800: 2층 절단(4500) 아래
    assert not cuts_at_y(v2, 8000)
    for v in (v1, v2):
        inc = lines_of(base, v)
        full = s3lib.RefGraph(s3lib._Inputs(base.eng)).q(D.view_lines, v)
        assert inc == full


def test_상단_구속_벽은_IFC_왕복에서_구속이_남는다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("wall.create", {"id": W5, "level": F.LV1, "axis": [0, 8000, 6000, 8000], "top_level": F.LV2, "top_offset": 2000})["ok"]
        h = b.hashes()
        f = ifcopenshell.open(str(b.export("t.ifc")))
        assert f.by_guid(W5).Representation.Representations[0].Items[0].Depth == 5300.0
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        p = b.run("element.get", {"id": W5}, project="imp.dobim")["result"]["params"]
        assert p["top_level"] == F.LV2 and p["top_offset"] == 2000.0
    finally:
        b.close()


# ---------------------------------------------------------------- 자동 난간 id
def test_계단_난간은_id_목록을_받는다(base):
    assert base.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": RUN, "width": 1200, "height": 3300, "risers": 19})["ok"]
    bad = base.run("railing.from_stair", {"stair": F.ST1, "ids": [gid("c", 5)]})
    assert not bad["ok"] and bad["error"]["path"] == "/ids"
    n = bad["error"]["details"]["expected"]
    ids = [gid("c", 10 + i) for i in range(n)]
    r = base.run("railing.from_stair", {"stair": F.ST1, "ids": ids})
    assert r["ok"] and r["result"]["created"] == ids, r
    bad = base.run("railing.from_stair", {"stair": F.ST1, "ids": ids})
    assert not bad["ok"] and bad["error"]["path"] == "/ids/0"            # 이미 있는 id
    assert params(base, ids[0])["src"] == F.ST1
    # 슬래브 난간도 같은 규칙
    assert base.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200})["ok"]
    r = base.run("railing.from_slab", {"slab": F.S1, "edges": [0, 1], "ids": [gid("c", 30)]})
    assert r["ok"] and r["result"]["created"] == [gid("c", 30)], r
