# SPDX-License-Identifier: MPL-2.0
"""승강기 보강: 층별 승강장 문 개구부(면·폭·높이·층 목록), 피트 깊이, 기계실, 승강기가 낸 슬래브 구멍의 이동 연동(소유 추적).
검사·커맨드, 평면(문 개구부·피트·기계실 표기)·단면(문 사각형·피트·기계실 상자), 치수(dims), 구멍 연동 규칙(사용자 구멍 불간섭), IFC 왕복, 증분 == 전체 재생성."""
import sys

import pytest

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

SHAFT = [4000, 500, 5800, 2300]     # 1800 x 1800
BOUND = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
S_LV2 = "6" + "0" * 20 + "2"
S_LV1 = "6" + "0" * 20 + "3"


def mk_ev(sess, **kw):
    args = {"id": F.EL1, "level": F.LV1, "shaft": SHAFT, "rise": 6000, **kw}
    r = sess.run("elevator.create", args)
    assert r["ok"], r
    return r["result"]["id"]


def mk(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


def labels_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_labels, vid)


def dims_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return [d[5] for d in sess.eng.g.q(D.view_dims, vid)]


def mk_slab(sess, sid, level, boundary=BOUND, **kw):
    r = sess.run("slab.create", {"id": sid, "level": level, "boundary": boundary, "thickness": 200, **kw})
    assert r["ok"], r
    return sid


def openings(sess, sid):
    return sess.run("element.get", {"id": sid})["result"]["params"].get("openings", [])


DOORS = {"face": "S", "w": 900, "h": 2100, "levels": [F.LV1, F.LV2]}


# ---------------------------------------------------------------- 검사·커맨드
def test_선택_입력이_저장되고_element_get_파생값에_나온다(base):
    mk_ev(base, pit=1400, machine_height=2800, doors=DOORS)
    g = base.run("element.get", {"id": F.EL1})["result"]
    p = g["params"]
    assert p["pit"] == 1400.0 and p["machine"] == {"height": 2800.0} and p["doors"] == {"face": "S", "w": 900.0, "h": 2100.0} and p["stops"] == [F.LV1, F.LV2]
    assert g["derived"]["pit_bottom"] == -1400.0 and g["derived"]["machine_top"] == 6000 + 2800 and g["derived"]["stops"] == 2
    # 입력이 없으면 키가 없다(저장 형식·응답이 이전과 같다)
    assert base.run("elevator.delete", {"id": F.EL1})["ok"]
    mk_ev(base)
    p = base.run("element.get", {"id": F.EL1})["result"]
    assert not {"pit", "machine", "doors", "stops", "hole_slabs", "hole_clearance"} & set(p["params"])
    assert not {"pit_bottom", "machine_top", "stops"} & set(p["derived"])


def test_잘못된_선택_입력은_거절한다(base):
    for bad in ({"pit": 0}, {"pit": 9000}, {"machine_height": 0}, {"machine_height": 9000},
                {"doors": {**DOORS, "w": 1700}},                       # 샤프트 면(1800)보다 200mm 이상 좁아야 한다
                {"doors": {**DOORS, "h": 7000}},                       # 샤프트보다 높다
                {"doors": {**DOORS, "levels": ["9" * 22]}},            # 레벨이 아니다
                {"doors": {**DOORS, "face": "X"}}, {"doors": {**DOORS, "levels": []}}):
        assert not base.run("elevator.create", {"id": F.EL1, "level": F.LV1, "shaft": SHAFT, "rise": 6000, **bad})["ok"], bad
    # 문이 샤프트 높이 범위 밖 층에 있다: 4000 높이 샤프트(1F 0~4000) 에 2F(3300) + 문 2100 은 범위를 넘는다
    assert not base.run("elevator.create", {"id": F.EL1, "level": F.LV1, "shaft": SHAFT, "rise": 4000, "doors": DOORS})["ok"]


def test_set으로_바꾸고_지우고_실행_취소한다(base):
    mk_ev(base)
    h0 = base.eng.state_hash()
    assert base.run("elevator.set", {"id": F.EL1, "pit": 1000, "machine_height": 2500, "doors": DOORS})["ok"]
    p = base.run("element.get", {"id": F.EL1})["result"]["params"]
    assert p["pit"] == 1000.0 and p["machine"]["height"] == 2500.0 and p["stops"] == [F.LV1, F.LV2]
    assert base.run("elevator.set", {"id": F.EL1, "doors": {**DOORS, "face": "E", "levels": [F.LV1]}})["ok"]
    assert base.run("elevator.set", {"id": F.EL1, "clear_pit": True, "clear_machine": True, "clear_doors": True})["ok"]
    p = base.run("element.get", {"id": F.EL1})["result"]["params"]
    assert not {"pit", "machine", "doors", "stops"} & set(p)
    assert not base.run("elevator.set", {"id": F.EL1, "pit": 1000, "clear_pit": True})["ok"]
    assert not base.run("elevator.set", {"id": F.EL1, "doors": {**DOORS, "w": 1790}})["ok"]
    for _ in range(3):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


# ---------------------------------------------------------------- 평면
def test_평면은_정차층에서만_문_개구부를_그리고_피트_기계실_높이는_샤프트만_그린다(base):
    mk_ev(base, pit=1400, machine_height=2800, doors=DOORS)
    p1 = mk(base, name="P1", type="plan", level=F.LV1, cut_height=1200)
    p2 = mk(base, name="P2", type="plan", level=F.LV2, cut_height=1200)
    pit = mk(base, name="PIT", type="plan", level=F.LV1, cut_height=-800)    # 피트(카 바닥 0 아래)
    mach = mk(base, name="MR", type="plan", level=F.LV2, cut_height=3300)    # 3300 + 3300 = 6600: 샤프트 위(6000) ~ 기계실 윗면(8800)
    ln1 = lines_of(base, p1)
    # S 면(y=500) 문 폭 900: 가운데 4900 ± 450 -> 4450~5350 이 비어 있고 문설주 둘(symbol)이 있다
    face = [x for x in ln1 if x[0] == "proj" and x[2] == x[4] == 500.0 and min(x[1], x[3]) >= 4000 and max(x[1], x[3]) <= 5800]
    assert sorted((min(x[1], x[3]), max(x[1], x[3])) for x in face) == [(4000.0, 4450.0), (5350.0, 5800.0)]
    jambs = [x for x in ln1 if x[0] == "symbol" and x[1] == x[3] and x[1] in (4450.0, 5350.0)]
    assert len(jambs) == 2
    assert any(x[0] == "symbol" and x[2] == x[4] == 650.0 for x in ln1)   # 문 선(샤프트 안쪽으로 150)
    assert ("EV" in [t[2] for t in labels_of(base, p1)])
    assert "피트" in [t[2] for t in labels_of(base, pit)] and "EV" not in [t[2] for t in labels_of(base, pit)]
    assert "기계실" in [t[2] for t in labels_of(base, mach)] and "EV" not in [t[2] for t in labels_of(base, mach)]
    # 피트·기계실 높이 평면은 샤프트 윤곽만(카 X 없음)
    for v in (pit, mach):
        assert not any(x[0] == "symbol" for x in lines_of(base, v))
        assert sum(1 for x in lines_of(base, v) if x[0] == "proj") == 4
    # 문이 없는 층: 2F 는 stops 에 있어 문이 있지만 LV 에 없는 평면은 문이 없다
    assert base.run("elevator.set", {"id": F.EL1, "doors": {**DOORS, "levels": [F.LV1]}})["ok"]
    ln2 = lines_of(base, p2)
    assert not any(x[0] == "symbol" and x[1] == x[3] and x[1] in (4450.0, 5350.0) for x in ln2)


def test_평면_치수에_문_폭이_더해지고_피트_기계실_평면은_샤프트_치수만(base):
    mk_ev(base, pit=1400, machine_height=2800, doors=DOORS)
    p1 = mk(base, name="P1", type="plan", level=F.LV1, scale=50, dims=True)
    pit = mk(base, name="PIT", type="plan", level=F.LV1, cut_height=-800, scale=50, dims=True)
    d1 = dims_of(base, p1)
    assert "문 900" in d1 and "샤프트 1800" in d1 and "카 1100" in d1
    dp = dims_of(base, pit)
    assert "문 900" not in dp and "카 1100" not in dp and dp == ["샤프트 1800", "샤프트 1800"]


# ---------------------------------------------------------------- 단면·입면
def test_단면은_피트까지_내려간_샤프트와_기계실_상자와_문_사각형을_그린다(base):
    mk_ev(base, pit=1400, machine_height=2800, doors=DOORS)
    sec = mk(base, name="S", type="section", p1=[3000, 300], p2=[7000, 300], scale=50, dims=True)   # y=300 에서 +y 를 본다: 샤프트의 S 면(y=500)과 문이 정면이다
    ln = lines_of(base, sec)
    zs = {round(v, 1) for x in ln for v in (x[2], x[4])}
    assert -1400.0 in zs and 8800.0 in zs                     # 피트 바닥, 기계실 윗면(6000 + 2800)
    assert 6000.0 in zs                                        # 샤프트 위 = 기계실 바닥
    # 문 사각형: 1F(0)에서 2100, 2F(3300)에서 5400
    assert 2100.0 in zs and 5400.0 in zs and 3300.0 in zs
    d = dims_of(base, sec)
    assert "피트 1400" in d and "기계실 2800" in d
    # 옵션이 없으면(clear) 이전과 같다
    sec0 = mk(base, name="S0", type="section", p1=[4900, -1000], p2=[4900, 4000], scale=50)
    after = lines_of(base, sec0)
    assert base.run("elevator.set", {"id": F.EL1, "clear_pit": True, "clear_machine": True, "clear_doors": True})["ok"]
    assert lines_of(base, sec0) != after


def test_선택_입력을_지우면_도면이_입력_전과_같다(base):
    mk_ev(base)
    views = [mk(base, name="P", type="plan", level=F.LV1, cut_height=1200), mk(base, name="P2", type="plan", level=F.LV2, cut_height=1200),
             mk(base, name="S", type="section", p1=[4900, -1000], p2=[4900, 4000]), mk(base, name="E", type="elevation", dir="S")]
    before = [(lines_of(base, v), labels_of(base, v)) for v in views]
    assert base.run("elevator.set", {"id": F.EL1, "pit": 1400, "machine_height": 2800, "doors": DOORS})["ok"]
    assert [(lines_of(base, v), labels_of(base, v)) for v in views] != before
    assert base.run("elevator.set", {"id": F.EL1, "clear_pit": True, "clear_machine": True, "clear_doors": True})["ok"]
    assert [(lines_of(base, v), labels_of(base, v)) for v in views] == before


# ---------------------------------------------------------------- 슬래브 구멍 연동
def test_cut_slabs가_낸_구멍은_승강기가_소유하고_이동에_따라간다(base):
    mk_ev(base)
    mk_slab(base, S_LV2, F.LV2)
    r = base.run("elevator.cut_slabs", {"id": F.EL1, "clearance": 50})["result"]
    assert r["cut"] == [S_LV2]
    p = base.run("element.get", {"id": F.EL1})["result"]["params"]
    assert p["hole_slabs"] == [S_LV2] and p["hole_clearance"] == 50.0
    hole = [[3950.0, 450.0], [5850.0, 450.0], [5850.0, 2350.0], [3950.0, 2350.0]]
    assert openings(base, S_LV2) == [hole]
    mv = base.run("elevator.move", {"id": F.EL1, "dx": -1000, "dy": 300})["result"]
    assert mv["holes"] == {"moved": [S_LV2], "lost": []}
    assert openings(base, S_LV2) == [[[x - 1000, y + 300] for x, y in hole]]
    assert base.run("element.get", {"id": F.EL1})["result"]["params"]["shaft"] == [3000.0, 800.0, 4800.0, 2600.0]
    # 실행 취소는 승강기와 구멍을 함께 되돌린다
    assert base.run("history.undo", {})["ok"]
    assert openings(base, S_LV2) == [hole]
    # set 으로 샤프트를 키우면 구멍도 커진다
    assert base.run("elevator.set", {"id": F.EL1, "shaft": [4000, 500, 5700, 2500]})["ok"]
    assert openings(base, S_LV2) == [[[3950.0, 450.0], [5750.0, 450.0], [5750.0, 2550.0], [3950.0, 2550.0]]]
    # 구멍 여유를 바꿔 다시 cut_slabs 하면 소유한 구멍도 새 여유로 맞춰진다(구멍이 둘로 늘지 않는다)
    r = base.run("elevator.cut_slabs", {"id": F.EL1, "clearance": 100})["result"]
    assert len(openings(base, S_LV2)) == 1 and r["already"] == [S_LV2] or r["cut"] == []
    assert base.run("element.get", {"id": F.EL1})["result"]["params"]["hole_clearance"] == 100.0


def test_사용자가_직접_낸_구멍은_건드리지_않는다(base):
    mk_ev(base)
    user_hole = [[500, 500], [1500, 500], [1500, 1500], [500, 1500]]
    mk_slab(base, S_LV2, F.LV2, openings=[user_hole])
    shaft_like = [[4000, 500], [5800, 500], [5800, 2300], [4000, 2300]]
    mk_slab(base, S_LV1, F.LV2, openings=[shaft_like])   # 사용자가 샤프트와 같은 모양으로 낸 구멍(승강기가 낸 것이 아니다)
    r = base.run("elevator.cut_slabs", {"id": F.EL1})["result"]
    assert S_LV2 in r["cut"] and S_LV1 in r["already"]
    p = base.run("element.get", {"id": F.EL1})["result"]["params"]
    assert p["hole_slabs"] == [S_LV2]                       # 이미 있던 구멍(already)은 소유하지 않는다
    base.run("elevator.move", {"id": F.EL1, "dx": -500, "dy": 0})
    assert openings(base, S_LV1) == [shaft_like]            # 사용자 구멍은 그대로
    ops = openings(base, S_LV2)
    assert user_hole in [[list(map(float, q)) for q in o] for o in ops] and len(ops) == 2   # 사용자 구멍은 그대로, 승강기 구멍만 이동
    assert [[3500.0, 500.0], [5300.0, 500.0], [5300.0, 2300.0], [3500.0, 2300.0]] in [[list(map(float, q)) for q in o] for o in ops]


def test_구멍이_슬래브_경계_밖이_되는_이동은_거절한다_승강기도_그대로(base):
    mk_ev(base)
    mk_slab(base, S_LV2, F.LV2)
    assert base.run("elevator.cut_slabs", {"id": F.EL1})["ok"]
    h = base.eng.state_hash()
    r = base.run("elevator.move", {"id": F.EL1, "dx": 500, "dy": 0})   # x2 = 6300 > 6000
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT", r
    assert base.eng.state_hash() == h
    assert not base.run("elevator.set", {"id": F.EL1, "shaft": [4000, 500, 6200, 2300]})["ok"]
    assert base.eng.state_hash() == h


def test_사용자가_고친_구멍은_따라가지_않고_lost로_알린다(base):
    mk_ev(base)
    mk_slab(base, S_LV2, F.LV2)
    assert base.run("elevator.cut_slabs", {"id": F.EL1})["ok"]
    assert base.run("slab.set", {"id": S_LV2, "openings": [[[4000, 500], [5800, 500], [5800, 2000], [4000, 2000]]]})["ok"]   # 사용자가 구멍을 고쳤다
    mv = base.run("elevator.move", {"id": F.EL1, "dx": -1000, "dy": 0})["result"]
    assert mv["holes"] == {"moved": [], "lost": [S_LV2]}
    assert openings(base, S_LV2) == [[[4000.0, 500.0], [5800.0, 500.0], [5800.0, 2000.0], [4000.0, 2000.0]]]
    assert base.run("slab.delete", {"id": S_LV2})["ok"]
    assert base.run("elevator.move", {"id": F.EL1, "dx": 100, "dy": 0})["ok"]   # 지워진 슬래브는 건너뛴다
    assert base.run("elevator.delete", {"id": F.EL1})["ok"]


def test_승강기를_지워도_구멍은_남는다(base):
    mk_ev(base)
    mk_slab(base, S_LV2, F.LV2)
    assert base.run("elevator.cut_slabs", {"id": F.EL1})["ok"]
    assert base.run("elevator.delete", {"id": F.EL1})["ok"]
    assert len(openings(base, S_LV2)) == 1


def test_승강기_구멍_연동_증분이_전체_재생성과_같다(base):
    views = {"p2": mk(base, name="P2", type="plan", level=F.LV2, cut_height=1200), "pt": mk(base, name="PT", type="plan", level=F.LV1, cut_height=5000),
             "s": mk(base, name="S", type="section", p1=[4900, -1000], p2=[4900, 4000]), "e": mk(base, name="E", type="elevation", dir="S")}

    def check():
        for k, v in views.items():
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k
            assert labels_of(base, v) == s3lib.RefGraph(s3lib._Inputs(base.eng)).q(D.view_labels, v), k

    mk_ev(base, pit=1000, machine_height=2000, doors=DOORS)
    check()
    mk_slab(base, S_LV2, F.LV2)
    assert base.run("elevator.cut_slabs", {"id": F.EL1})["ok"]
    check()
    assert base.run("elevator.move", {"id": F.EL1, "dx": -500, "dy": 200})["ok"]
    check()
    assert base.run("elevator.set", {"id": F.EL1, "doors": {**DOORS, "face": "N"}, "machine_height": 3000})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()


# ---------------------------------------------------------------- IFC 왕복
def test_선택_입력이_있는_승강기를_내보내고_가져오면_되살아나고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("slab.create", {"id": S_LV2, "level": F.LV2, "boundary": BOUND, "thickness": 200})["ok"]
        assert b.run("elevator.create", {"id": F.EL1, "level": F.LV1, "shaft": SHAFT, "rise": 6000, "pit": 1400, "machine_height": 2800, "doors": DOORS})["ok"]
        assert b.run("elevator.cut_slabs", {"id": F.EL1, "clearance": 50})["ok"]
        h = b.hashes()
        b.export("r.ifc")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim") == {"authored": 9, "foreign": 0, "detached": 0}
        a = b.run("element.get", {"id": F.EL1})["result"]["params"]
        c = b.run("element.get", {"id": F.EL1}, project="imp.dobim")["result"]["params"]
        assert a == c and c["hole_slabs"] == [S_LV2]
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        # 가져온 뒤에도 구멍이 따라간다
        assert b.run("elevator.move", {"id": F.EL1, "dx": -500, "dy": 0}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_병합이_승강기의_참조_id를_옮긴다():
    from dobim_core.kinds import remap_params
    p = {"name": "e", "shaft": [0, 0, 1000, 1000], "stops": ["a" * 22, "b" * 22], "hole_slabs": ["c" * 22], "hole_clearance": 0.0}
    out = remap_params("elevator", p, {"a" * 22: "x" * 22, "c" * 22: "y" * 22})
    assert out["stops"] == ["x" * 22, "b" * 22] and out["hole_slabs"] == ["y" * 22]
