# SPDX-License-Identifier: MPL-2.0
"""W1 1-F 첫 PR: 레벨 고치기(ARC-37, level.set), 요소 복사·배열·다른 층 복사·회전·대칭(UI-03, element.copy/rotate/mirror).
- 레벨 높이를 바꾸면 상단이 묶인 벽 height 가 다시 계산되고, 맞출 수 없으면 거절한다. 이름은 유일
- 벽을 고르면 호스팅된 문·패밀리가 함께 가고, 속성·재료·조인(둘 다 고른 벽끼리)이 복사된다
- 회전 90도는 좌표가 정확하고, 대칭은 축선을 뒤집어 왼쪽 면·개구부 위치를 거울상으로 둔다
- 새 id 는 로그에 남아 다시 열어도(재생) 같은 상태, IFC 왕복 해시가 같다. 합성 데이터만 쓴다."""
import sys

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

W4 = "1" + "0" * 20 + "4"
BOX = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
# IFC GlobalId 첫 글자는 0~3: model.validate 오류가 없도록 시험 요소 id 를 0 으로 시작한다
SL, ST, SO, EV = ("0" * 19 + x for x in ("sl1", "st1", "so1", "ev1"))


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r, code="CONSTRAINT"):
    assert not r["ok"] and r["error"]["code"] == code, r
    return r["error"]


def el(s, eid):
    return ok(s.run("element.get", {"id": eid}))


def test_레벨_이름과_높이(base):
    s = base
    err(s.run("level.set", {"id": F.LV2, "name": "1F"}))   # 같은 이름
    err(s.run("level.set", {"id": F.LV2}), "SCHEMA_INVALID")
    ok(s.run("level.set", {"id": F.LV2, "name": "2층"}))
    # 1F 벽 상단을 2F 에 묶는다: 2F 높이를 바꾸면 height 가 따라간다
    ok(s.run("wall.set", {"id": F.W2, "top_level": F.LV2, "top_offset": -100}))
    assert el(s, F.W2)["params"]["height"] == 3200
    r = ok(s.run("level.set", {"id": F.LV2, "elevation": 3600}))
    assert r["walls"] == [F.W2] and el(s, F.W2)["params"]["height"] == 3500
    # 1F(바닥 레벨)를 올려도 다시 계산한다
    ok(s.run("level.set", {"id": F.LV1, "elevation": 600}))
    assert el(s, F.W2)["params"]["height"] == 2900
    # 상단이 바닥보다 낮아지면 거절(아무것도 바뀌지 않는다)
    err(s.run("level.set", {"id": F.LV2, "elevation": 500}))
    assert el(s, F.W2)["params"]["height"] == 2900
    ok(s.run("history.undo", {}))
    assert el(s, F.W2)["params"]["height"] == 3500
    assert el(s, F.LV1)["params"]["elevation"] == 0


def test_복사는_문_속성_재료_조인을_함께(base):
    s = base
    r = ok(s.run("element.copy", {"ids": [F.W1, F.W2], "dx": 0, "dy": -5000}))
    w1c, w2c, d1c = r["ids"]
    assert el(s, w1c)["params"]["axis"] == [0, -5000, 6000, -5000]
    rels = ok(s.run("relation.list", {"id": w1c}))
    txt = repr(rels)
    assert d1c in txt and w2c in txt   # 문 호스팅·조인
    d = el(s, d1c)
    assert d["psets"]["Pset_DoorCommon"]["FireRating"] == "EI30" and d["params"]["offset"] == 1500
    assert el(s, w1c)["material"] == "Concrete"
    assert el(s, w1c)["params"]["name"].endswith(w1c[-6:])   # 자동 이름은 새 id 를 따른다
    assert ok(s.run("model.validate", {}))["errors"] == 0


def test_배열과_다른_층_복사(base):
    s = base
    r = ok(s.run("element.copy", {"ids": [F.W2], "dx": 3000, "count": 3}))
    xs = [el(s, i)["params"]["axis"][0] for i in r["ids"]]
    assert xs == [9000, 12000, 15000]
    ok(s.run("wall.set", {"id": F.W3, "top_level": F.LV2}))
    r = s.run("element.copy", {"ids": [F.W3], "level": F.LV2})
    assert r["ok"] and r["warnings"], r
    w = el(s, r["result"]["ids"][0])
    assert "top_level" not in w["params"] and w["params"]["height"] == 3300
    assert F.LV2 in repr(ok(s.run("relation.list", {"id": w["id"]})))
    err(s.run("element.copy", {"ids": [F.W1], "level": F.LV2, "count": 2}))
    err(s.run("element.copy", {"ids": [F.W1]}))                  # 제자리
    err(s.run("element.copy", {"ids": [F.D1], "dx": 100}))       # 호스트 없이 문만
    err(s.run("element.copy", {"ids": [F.LV1], "dx": 100}))      # 레벨은 대상 아님
    ok(s.run("grid.create", {"id": "g" + "0" * 21, "name": "X1", "line": [0, -2000, 0, 6000]}))
    err(s.run("element.copy", {"ids": ["g" + "0" * 21], "dx": 100}))   # 통심선 기호는 유일


def test_새_id_는_로그에_남아_재생과_다시_열기가_같다(tmp_path):
    s = kit.Session(tmp_path / "t.dobim")
    try:
        for n, a in F.SCRIPT:
            ok(s.run(n, a))
        ok(s.run("stair.create", {"id": ST, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18}))
        ok(s.run("railing.from_stair", {"stair": ST}))
        rails = [i["id"] for i in ok(s.run("element.query", {"kind": "railing"}))["items"]]
        r = ok(s.run("element.copy", {"ids": [F.W1, ST, rails[0]], "dx": 1000, "dy": -6000, "count": 2}))
        rc = el(s, r["ids"][2])   # 첫 벌: 벽, 계단, 난간, 문(호스팅은 뒤) 순서
        assert rc["params"]["src"] == r["ids"][1]   # 출처는 새 계단을 가리킨다
        ok(s.run("element.mirror", {"ids": [F.W3], "axis": [0, 5000, 10, 5000], "keep": True}))
        ok(s.run("element.rotate", {"ids": [F.W2], "center": [6000, 0], "angle": 15}))
        ok(s.run("level.set", {"id": F.LV2, "elevation": 3600}))
        res = kit.full_check(s, tmp_path)
        assert res["ok"], res["diffs"]
        err(s.run("element.copy", {"ids": [F.W2], "dx": 100, "new_ids": [r["ids"][0]]}))   # 쓴 id
        err(s.run("element.copy", {"ids": [F.W2], "dx": 100, "new_ids": []}))
    finally:
        s.close()


def test_회전_90도는_정확하고_문이_따라간다(base):
    s = base
    ok(s.run("family.place", {"id": "0" * 21 + "5", "family": "column.section", "type_values": {"section": "round"}, "level": F.LV1, "position": [1000, 1000], "rotation": 10}))
    ok(s.run("element.rotate", {"ids": [F.W1, F.W2, F.W3, "0" * 21 + "5"], "center": [0, 0], "angle": 90}))
    assert el(s, F.W1)["params"]["axis"] == [0, 0, 0, 6000]
    assert el(s, F.W2)["params"]["axis"] == [0, 6000, -4000, 6000]
    assert el(s, F.D1)["params"]["offset"] == 1500   # 호스트를 따른다
    c = el(s, "0" * 21 + "5")["params"]
    assert c["position"] == [-1000, 1000] and c["rotation"] == 100
    err(s.run("element.rotate", {"ids": [F.W1], "center": [0, 0], "angle": 360}))
    err(s.run("element.rotate", {"ids": [F.D1], "center": [0, 0], "angle": 90}))
    assert ok(s.run("model.validate", {}))["errors"] == 0
    ok(s.run("history.undo", {}))
    assert el(s, F.W1)["params"]["axis"] == [0, 0, 6000, 0]


def test_회전_슬래브_물매_계단_난간(base):
    s = base
    ok(s.run("slab.create", {"id": SL, "level": F.LV2, "boundary": BOX, "thickness": 200, "use": "roof", "fall": {"dir": 0, "pct": 2}, "drains": [[500, 500]]}))
    ok(s.run("stair.create", {"id": ST, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18}))
    ok(s.run("railing.from_stair", {"stair": ST}))
    rails = [i["id"] for i in ok(s.run("element.query", {"kind": "railing"}))["items"]]
    assert rails
    before = {r: el(s, r)["params"]["path"] for r in rails}
    n0 = ok(s.run("model.validate", {}))["errors"]   # 난간의 표현 형식 경고(기존)는 회전 전후 같아야 한다
    ok(s.run("element.rotate", {"ids": [SL, ST], "center": [0, 0], "angle": 90}))
    sp = el(s, SL)["params"]
    assert sp["fall"]["dir"] == 90 and sp["drains"] == [[-500, 500]]
    assert el(s, ST)["params"]["run"] == [-1000, 1000, -1000, 5000]
    for r in rails:   # 계단이 낸 난간은 따라 돈다
        assert el(s, r)["params"]["path"] == [[-y, x] for x, y in before[r]]
    assert ok(s.run("model.validate", {}))["errors"] == n0


def test_승강기_구멍이_있는_슬래브는_회전하지_않는다(base):
    s = base
    ok(s.run("slab.create", {"id": SL, "level": F.LV2, "boundary": BOX, "thickness": 200}))
    ok(s.run("elevator.create", {"id": EV, "level": F.LV1, "shaft": [4000, 500, 5800, 2300], "rise": 6000}))
    ok(s.run("elevator.cut_slabs", {"id": EV}))
    err(s.run("element.rotate", {"ids": [SL], "center": [0, 0], "angle": 90}))
    err(s.run("element.copy", {"ids": [EV], "dx": 100}))


def test_대칭은_축선을_뒤집어_거울상(base):
    s = base
    ok(s.run("wall.layers", {"id": F.W1, "layers": [{"material": "Concrete", "thickness": 150}, {"material": "Insulation", "thickness": 50}]}))
    ok(s.run("element.mirror", {"ids": [F.W1], "axis": [0, -1000, 10, -1000]}))
    w = el(s, F.W1)["params"]
    assert w["axis"] == [6000, -2000, 0, -2000]        # 뒤집힌 축선
    assert el(s, F.D1)["params"]["offset"] == 4500      # 거울상 위치(x=1500 그대로)
    # 계단: 꺾는 쪽이 바뀐다
    ok(s.run("stair.create", {"id": ST, "level": F.LV1, "run": [1000, 1000, 4000, 1000], "width": 1200, "height": 3000, "risers": 18,
                              "turn": {"type": "L", "side": "left", "risers_a": 9}}))
    ok(s.run("element.mirror", {"ids": [ST], "axis": [0, 2000, 10, 2000]}))
    st = el(s, ST)["params"]
    assert st["run"] == [1000, 3000, 4000, 3000] and st["turn"]["side"] == "right"
    # 슬래브: 반시계가 유지된다
    ok(s.run("slab.create", {"id": SL, "level": F.LV2, "boundary": [[0, 0], [3000, 0], [0, 2000]], "thickness": 200}))
    ok(s.run("element.mirror", {"ids": [SL], "axis": [5000, 0, 5000, 10]}))
    b = el(s, SL)["params"]["boundary"]
    area2 = sum(b[i][0] * b[(i + 1) % 3][1] - b[(i + 1) % 3][0] * b[i][1] for i in range(3))
    assert area2 > 0 and sorted(map(tuple, b)) == [(7000, 0), (10000, 0), (10000, 2000)]
    assert ok(s.run("model.validate", {}))["errors"] == 0


def test_대칭_두번이면_제자리_keep_은_복사(base):
    s = base
    a0 = el(s, F.W3)["params"]["axis"]
    o0 = el(s, F.D2)["params"]["offset"]
    for _ in range(2):
        ok(s.run("element.mirror", {"ids": [F.W3], "axis": [0, 0, 1000, 700]}))
    assert all(abs(x - y) < 1e-3 for x, y in zip(el(s, F.W3)["params"]["axis"], a0))
    assert abs(el(s, F.D2)["params"]["offset"] - o0) < 1e-3
    r = ok(s.run("element.mirror", {"ids": [F.W3], "axis": [0, 5000, 10, 5000], "keep": True}))
    assert r["count"] == 2 and el(s, r["ids"][0])["params"]["axis"] == [0, 6000, 6000, 6000]
    err(s.run("element.mirror", {"ids": [F.W3], "axis": [0, 0, 0, 0]}))
    err(s.run("element.mirror", {"ids": [F.W3], "axis": [0, 0, 0, 10], "new_ids": []}))


def test_솔리드_대칭_회전(base):
    s = base
    ok(s.run("solid.create", {"id": SO, "level": F.LV1, "form": "prism", "polygon": [[0, 0], [600, 0], [600, 300]], "z0": 0, "z1": 1000}))
    ok(s.run("element.mirror", {"ids": [SO], "axis": [0, 0, 0, 10]}))
    ok(s.run("element.rotate", {"ids": [SO], "center": [0, 0], "angle": 45}))
    assert ok(s.run("model.validate", {}))["errors"] == 0


def test_IFC_왕복은_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("level.set", {"id": F.LV2, "elevation": 3500, "name": "2층"}))
        ok(b.run("element.copy", {"ids": [F.W1], "dx": 0, "dy": -3000, "count": 2}))
        ok(b.run("element.rotate", {"ids": [F.W2], "center": [6000, 0], "angle": 30}))
        ok(b.run("element.mirror", {"ids": [F.W3], "axis": [0, 5000, 10, 5000], "keep": True}))
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()
