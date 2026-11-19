# SPDX-License-Identifier: MPL-2.0
"""W1 1-F 2차: 원형 배열·벽 간격띄우기·트림/연장(UI-03), 일괄 편집(UI-04).
- 원형 배열은 k*step 회전 복사본을 만들고, 원본과 겹치는 각은 거절한다
- 간격띄우기는 축선 왼쪽(+)으로 옮기고, 둘 다 띄운 벽끼리 조인을 옮긴다
- 트림/연장은 시작점이 움직여도 문·개구부가 제자리에 남는다. 모서리 맞추기는 두 벽을 교점까지 잇고 조인한다
- 일괄 편집은 ids 또는 where 로 고르고, where 는 로그에 ids 로 남아 재생이 같다. 하나라도 실패하면 아무것도 바뀌지 않는다
합성 데이터만 쓴다."""
import sys

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

W4 = "0" * 19 + "w4a"


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r, code="CONSTRAINT"):
    assert not r["ok"] and r["error"]["code"] == code, r
    return r["error"]


def el(s, eid):
    return ok(s.run("element.get", {"id": eid}))


def test_원형_배열(base):
    s = base
    r = ok(s.run("element.array_radial", {"ids": [F.W1], "center": [0, 0], "step": 90, "count": 3}))
    assert r["count"] == 6   # 벽 + 호스팅 문, 3벌
    axes = [el(s, i)["params"]["axis"] for i in r["ids"][0::2]]
    assert axes == [[0, 0, 0, 6000], [0, 0, -6000, 0], [0, 0, 0, -6000]]
    assert el(s, r["ids"][1])["params"]["offset"] == 1500
    err(s.run("element.array_radial", {"ids": [F.W1], "center": [0, 0], "step": 120, "count": 3}))   # 3번째가 원본과 겹친다
    err(s.run("element.array_radial", {"ids": [F.D1], "center": [0, 0], "step": 30, "count": 1}))    # 호스트 없이 문만
    assert ok(s.run("model.validate", {}))["errors"] == 0


def test_간격띄우기_복사와_이동(base):
    s = base
    r = ok(s.run("wall.offset", {"ids": [F.W1, F.W2], "distance": -1000}))
    w1c, d1c, w2c = r["ids"]
    assert el(s, w1c)["params"]["axis"] == [0, -1000, 6000, -1000]   # W1 진행 +x, 오른쪽 = -y
    assert el(s, w2c)["params"]["axis"] == [7000, 0, 7000, 4000]      # W2 진행 +y, 오른쪽 = +x
    assert el(s, d1c)["params"]["offset"] == 1500
    assert w2c in repr(ok(s.run("relation.list", {"id": w1c})))       # 조인도 옮겼다
    ok(s.run("wall.offset", {"ids": [F.W3], "distance": 500, "keep": False}))
    assert el(s, F.W3)["params"]["axis"] == [6000, 3500, 0, 3500]     # W3 진행 -x, 왼쪽 = -y
    err(s.run("wall.offset", {"ids": [F.W3], "distance": 0}))
    err(s.run("wall.offset", {"ids": [F.D1], "distance": 100}), "NOT_FOUND")
    err(s.run("wall.offset", {"ids": [F.W3], "distance": 100, "keep": False, "new_ids": []}))
    assert ok(s.run("model.validate", {}))["errors"] == 0


def test_트림_연장은_문을_제자리에(base):
    s = base
    ok(s.run("wall.create", {"id": W4, "level": F.LV1, "axis": [3000, 1000, 3000, 3000], "thickness": 200}))
    # 연장: W4 끝을 W3(y=4000)까지, 시작을 W1(y=0)까지
    r = ok(s.run("wall.extend_to", {"id": W4, "to": F.W3}))
    assert r["end"] == "end" and el(s, W4)["params"]["axis"] == [3000, 1000, 3000, 4000]
    ok(s.run("wall.extend_to", {"id": W4, "to": F.W1}))
    assert el(s, W4)["params"]["axis"] == [3000, 0, 3000, 4000]
    # 트림: W1 시작점을 W4(x=3000)까지 당기면 문 D1(x=1500)이 벽 밖으로 나가 거절, 끝점 쪽은 된다
    err(s.run("wall.extend_to", {"id": F.W1, "to": W4, "end": "start"}))
    assert el(s, F.W1)["params"]["axis"] == [0, 0, 6000, 0] and el(s, F.D1)["params"]["offset"] == 1500
    ok(s.run("door.set", {"id": F.D1, "offset": 4500}))
    ok(s.run("wall.extend_to", {"id": F.W1, "to": W4, "end": "start"}))
    assert el(s, F.W1)["params"]["axis"] == [3000, 0, 6000, 0]
    assert el(s, F.D1)["params"]["offset"] == 1500   # 축 시작이 3000 앞으로 왔으니 offset 이 3000 줄어 x=4500 그대로
    err(s.run("wall.extend_to", {"id": F.W1, "to": F.W3}))   # 평행
    err(s.run("wall.extend_to", {"id": F.W1, "to": F.W1}))
    assert ok(s.run("model.validate", {}))["errors"] == 0
    ok(s.run("history.undo", {}))
    assert el(s, F.W1)["params"]["axis"] == [0, 0, 6000, 0] and el(s, F.D1)["params"]["offset"] == 4500


def test_모서리_맞추기(base):
    s = base
    ok(s.run("wall.create", {"id": W4, "level": F.LV1, "axis": [-2000, 500, -2000, 3000], "thickness": 200}))
    r = ok(s.run("wall.corner", {"a": F.W1, "b": W4}))
    assert r["point"] == [-2000, 0] and r["ends"] == {F.W1: "start", W4: "start"} and r["joined"]
    assert el(s, F.W1)["params"]["axis"] == [-2000, 0, 6000, 0]
    assert el(s, F.D1)["params"]["offset"] == 3500   # 시작이 2000 뒤로 갔으니 문은 x=1500 그대로
    assert el(s, W4)["params"]["axis"] == [-2000, 0, -2000, 3000]
    assert W4 in repr(ok(s.run("relation.list", {"id": F.W1})))
    ok(s.run("wall.create", {"id": "0" * 19 + "w5a", "level": F.LV2, "axis": [0, 5000, 0, 6000]}))
    err(s.run("wall.corner", {"a": F.W1, "b": "0" * 19 + "w5a"}))   # 다른 레벨 조인
    assert ok(s.run("model.validate", {}))["errors"] == 0


def test_일괄_속성_재료_벽(base):
    s = base
    r = ok(s.run("pset.set_many", {"where": {"kind": "door"}, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI60"}))
    assert r["count"] == 2 and r["changed"] == 2
    assert el(s, F.D2)["psets"]["Pset_DoorCommon"]["FireRating"] == "EI60"
    r = ok(s.run("material.set_many", {"ids": [F.W1, F.W2], "name": "Brick"}))
    assert r["changed"] == 2 and el(s, F.W2)["material"] == "Brick"
    r = ok(s.run("wall.set_many", {"where": {"level": F.LV1}, "height": 3200}))
    assert r["count"] == 3 and all(el(s, w)["params"]["height"] == 3200 for w in (F.W1, F.W2, F.W3))
    ok(s.run("wall.set_many", {"ids": [F.W1, F.W2], "top_level": F.LV2}))
    assert el(s, F.W2)["params"]["height"] == 3300
    # 하나라도 실패하면 전체 거절: W3 은 상단 구속이 없어 top_offset 을 받을 수 없다(W1 도 바뀌지 않는다)
    e = err(s.run("wall.set_many", {"where": {"name_contains": "W-"}, "top_offset": -100}))
    assert e["path"] == "/ids/2"
    assert "top_offset" not in el(s, F.W1)["params"] and el(s, F.W1)["params"]["height"] == 3300
    err(s.run("wall.set_many", {"where": {"kind": "door"}, "height": 3000}))
    err(s.run("pset.set_many", {"ids": [F.D1], "where": {"kind": "door"}, "pset": "P", "prop": "a", "value": 1}), "SCHEMA_INVALID")
    err(s.run("pset.set_many", {"where": {"name_contains": "없는이름"}, "pset": "P", "prop": "a", "value": 1}), "NOT_FOUND")
    err(s.run("pset.set_many", {"ids": [F.W1], "pset": "DoBIM_Intent", "prop": "a", "value": 1}))
    assert ok(s.run("model.validate", {}))["errors"] == 0


def test_where_는_로그에_ids_로_남아_재생이_같다(tmp_path):
    s = kit.Session(tmp_path / "t.dobim")
    try:
        for n, a in F.SCRIPT:
            ok(s.run(n, a))
        ok(s.run("pset.set_many", {"where": {"kind": "wall"}, "pset": "Pset_WallCommon", "prop": "IsExternal", "value": True}))
        ok(s.run("wall.set_many", {"where": {"level": F.LV1}, "thickness": 250}))
        ok(s.run("material.set_many", {"where": {"kind": "wall", "name_contains": "W-"}, "name": "Brick"}))
        ok(s.run("element.array_radial", {"ids": [F.W2], "center": [6000, 0], "step": 45, "count": 2}))
        ok(s.run("wall.offset", {"ids": [F.W3], "distance": 1200}))
        ok(s.run("wall.create", {"id": W4, "level": F.LV1, "axis": [2000, 1000, 2000, 3000], "thickness": 200}))
        ok(s.run("wall.corner", {"a": W4, "b": F.W3, "join": False}))
        ok(s.run("door.set", {"id": F.D1, "offset": 4500}))
        ok(s.run("wall.extend_to", {"id": F.W1, "to": W4}))
        res = kit.full_check(s, tmp_path)
        assert res["ok"], res["diffs"]
    finally:
        s.close()


def test_where_가_풀린_인자가_로그에_남는다(base):
    s = base
    ok(s.run("material.set_many", {"where": {"kind": "door"}, "name": "Wood"}))
    a = ok(s.run("history.list", {"limit": 1}))["items"][0]["args"]
    assert "where" not in a and a["ids"] == sorted([F.D1, F.D2])


def test_IFC_왕복은_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("element.array_radial", {"ids": [F.W2], "center": [6000, 0], "step": 30, "count": 2}))
        ok(b.run("wall.offset", {"ids": [F.W1], "distance": -1500}))
        ok(b.run("wall.set_many", {"where": {"level": F.LV1}, "height": 3100}))
        ok(b.run("pset.set_many", {"where": {"kind": "wall"}, "pset": "Pset_WallCommon", "prop": "IsExternal", "value": True}))
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()
