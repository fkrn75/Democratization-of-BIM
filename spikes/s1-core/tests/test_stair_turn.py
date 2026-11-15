# SPDX-License-Identifier: MPL-2.0
"""꺾인 계단(층참 있는 L형·U형): 배치(순수 함수), 커맨드·검사, IFC 부속(IfcStairFlight·층참 IfcSlab) 투영·가져오기 왕복, 도면(평면·단면·입면), 증분 == 전체 재생성."""
import sys

import ifcopenshell
import pytest

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

RUN = [1000, 1000, 4000, 1000]   # +x 방향 3000mm: 아래 단 묶음 챌판 9 -> 디딤판 8 개, 깊이 3000/8 = 375
TURN_L = {"type": "L", "side": "left", "risers_a": 9}
TURN_U = {"type": "U", "side": "left", "risers_a": 9}


def mk_stair(sess, turn=TURN_L, **kw):
    args = {"id": F.ST1, "level": F.LV1, "run": RUN, "width": 1200, "height": 3000, "risers": 18, "turn": turn, **kw}
    r = sess.run("stair.create", args)
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


def covers(lines, cls, a, b, tol=1e-6):
    """lines 중 cls 선분 하나가 a-b 구간 전체를 덮는가(같은 직선 위에 이어 붙은 선분이면 된다)."""
    for x in lines:
        if x[0] != cls:
            continue
        p, q = (x[1], x[2]), (x[3], x[4])
        cr = (q[0] - p[0]) * (a[1] - p[1]) - (q[1] - p[1]) * (a[0] - p[0])
        cr2 = (q[0] - p[0]) * (b[1] - p[1]) - (q[1] - p[1]) * (b[0] - p[0])
        if abs(cr) > 1e-3 or abs(cr2) > 1e-3:
            continue
        lo, hi = (min(p[0], q[0]), max(p[0], q[0])), (min(p[1], q[1]), max(p[1], q[1]))
        if lo[0] - tol <= min(a[0], b[0]) and max(a[0], b[0]) <= lo[1] + tol and hi[0] - tol <= min(a[1], b[1]) and max(a[1], b[1]) <= hi[1] + tol:
            return True
    return False


def params(over=None):
    p = {"name": "ST", "run": [1000.0, 1000.0, 4000.0, 1000.0], "width": 1200.0, "height": 3000.0, "risers": 18, "base_offset": 0.0,
         "turn": {"type": "L", "side": "left", "risers_a": 9, "landing": None, "gap": 0.0}}
    p.update(over or {})
    return p


# ---------------------------------------------------------------- 배치(순수 함수)
def test_L형_배치는_층참_끝에서_왼쪽으로_90도_올라간다():
    lay = K.stair_layout(params())
    a, b = lay["flights"]
    assert a["run"] == (1000.0, 1000.0, 4000.0, 1000.0) and a["n"] == 9 and a["z0"] == 0.0
    assert b["n"] == 9 and abs(b["z0"] - 9 * 3000 / 18) < 1e-9 and abs(b["td"] - a["td"]) < 1e-9
    # 층참: 걸음선 끝(x=4000)에서 폭(1200)만큼 깊이, 폭 방향 -600..600
    assert [tuple(round(v, 6) for v in q) for q in lay["landing"]["poly"]] == [(4000.0, 400.0), (5200.0, 400.0), (5200.0, 1600.0), (4000.0, 1600.0)]
    assert abs(lay["landing"]["z"] - 1500.0) < 1e-9
    # 위 단 묶음은 층참 가장자리(y=1600)에서 +y(왼쪽) 방향, 층참 중앙 x=4600
    bx1, by1, bx2, by2 = b["run"]
    assert abs(bx1 - 4600.0) < 1e-9 and abs(by1 - 1600.0) < 1e-9 and abs(bx2 - 4600.0) < 1e-9 and by2 > by1
    assert abs((by2 - by1) - 8 * 375.0) < 1e-9


def test_U형_배치는_층참에서_되돌아_오르고_오른쪽도_대칭이다():
    lay = K.stair_layout(params({"turn": {"type": "U", "side": "left", "risers_a": 9, "landing": None, "gap": 100.0}}))
    a, b = lay["flights"]
    assert abs(b["run"][0] - 4000.0) < 1e-9 and abs(b["run"][1] - 2300.0) < 1e-9     # 층참 가까운 가장자리(x=4000), 폭 + 간격(1300)만큼 왼쪽
    assert b["run"][2] < b["run"][0] and abs(b["run"][3] - b["run"][1]) < 1e-9        # 되돌아 -x 방향
    r = K.stair_layout(params({"turn": {"type": "U", "side": "right", "risers_a": 9, "landing": 1500.0, "gap": 100.0}}))
    assert abs(r["flights"][1]["run"][1] - (1000.0 - 1300.0)) < 1e-9 and abs(r["flights"][1]["run"][0] - 4000.0) < 1e-9


def test_직선_계단은_배치가_단_묶음_하나이고_층참이_없다():
    p = params()
    del p["turn"]
    lay = K.stair_layout(p)
    assert len(lay["flights"]) == 1 and lay["landing"] is None and lay["flights"][0]["n"] == 18


# ---------------------------------------------------------------- 커맨드·검사
def test_꺾인_계단_생성은_정규화한_turn을_저장한다(base):
    mk_stair(base, turn={"type": "U", "risers_a": 8})
    p = base.run("element.get", {"id": F.ST1})["result"]["params"]
    assert p["turn"] == {"type": "U", "side": "left", "risers_a": 8, "landing": None, "gap": 100.0}
    base.run("stair.set", {"id": F.ST1, "turn": {"type": "L", "risers_a": 9}})
    assert base.run("element.get", {"id": F.ST1})["result"]["params"]["turn"]["gap"] == 0.0


def test_잘못된_꺾임은_거절한다(base):
    for bad in ({"type": "L", "risers_a": 1}, {"type": "L", "risers_a": 17}, {"type": "L", "risers_a": 9, "landing": 1000}, {"type": "X", "risers_a": 9}):
        r = base.run("stair.create", {"level": F.LV1, "run": RUN, "width": 1200, "height": 3000, "risers": 18, "turn": bad})
        assert not r["ok"], bad
    assert "stair" not in base.run("model.stats", {})["result"]["counts"]


def test_꺾임_수정_해제와_실행_취소(base):
    mk_stair(base)
    h0 = base.eng.state_hash()
    assert base.run("stair.set", {"id": F.ST1, "clear_turn": True})["ok"]
    assert "turn" not in base.run("element.get", {"id": F.ST1})["result"]["params"]
    assert not base.run("stair.set", {"id": F.ST1, "risers": 12})["ok"] is False          # 직선이라 챌판 수를 바꿀 수 있다
    assert base.run("history.undo", {})["ok"] and base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0
    r = base.run("stair.set", {"id": F.ST1, "risers": 10})                                  # risers_a(9) 가 전체 - 2 를 넘는다
    assert not r["ok"]
    assert not base.run("stair.set", {"id": F.ST1, "clear_turn": True, "turn": TURN_L})["ok"]


# ---------------------------------------------------------------- IFC
@pytest.mark.parametrize("turn,ptype", [(TURN_L, "QUARTER_TURN_STAIR"), (TURN_U, "HALF_TURN_STAIR")])
def test_IFC는_IfcStair_아래_IfcStairFlight_둘과_층참_IfcSlab을_집합한다(base, tmp_path, turn, ptype):
    mk_stair(base, turn=turn)
    out = tmp_path / "s.ifc"
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    st = f.by_guid(F.ST1)
    assert st.is_a("IfcStair") and st.PredefinedType == ptype and st.Representation is None
    kids = [k for r in st.IsDecomposedBy for k in r.RelatedObjects]
    assert sorted(k.is_a() for k in kids) == ["IfcSlab", "IfcStairFlight", "IfcStairFlight"]
    landing = [k for k in kids if k.is_a("IfcSlab")][0]
    assert landing.PredefinedType == "LANDING"
    flights = sorted((k for k in kids if k.is_a("IfcStairFlight")), key=lambda k: k.Name)
    assert [k.NumberOfRisers for k in flights] == [9, 9] and [k.NumberOfTreads for k in flights] == [8, 8]
    assert all(abs(k.RiserHeight - 3000 / 18) < 1e-9 for k in flights)
    assert [r.RelatingStructure.GlobalId for r in st.ContainedInStructure] == [F.LV1]
    assert base.run("model.validate", {})["ok"]


def test_꺾임을_풀면_부속이_사라지고_직선_몸체가_돌아온다(base, tmp_path):
    mk_stair(base)
    base.run("stair.set", {"id": F.ST1, "clear_turn": True})
    base.eng.flush_ifc()
    base.eng.ifc.write(str(tmp_path / "s.ifc"))
    f = ifcopenshell.open(str(tmp_path / "s.ifc"))
    assert not f.by_type("IfcStairFlight") and not [s for s in f.by_type("IfcSlab") if s.PredefinedType == "LANDING"]
    st = f.by_guid(F.ST1)
    assert st.PredefinedType == "STRAIGHT_RUN_STAIR" and st.Representation is not None


@pytest.mark.parametrize("turn", [TURN_L, TURN_U])
def test_꺾인_계단을_내보내고_가져오면_저작_계단으로_되살아나고_해시_3종이_같다(tmp_path, turn):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": RUN, "width": 1200, "height": 3000, "risers": 18, "turn": turn})["ok"]
        h = b.hashes()
        b.export("s.ifc")
        r = b.import_ifc("s.ifc", "imp.dobim")
        assert r["ok"], r
        assert b.origins("imp.dobim") == {"authored": 8, "foreign": 0, "detached": 0}   # 부속(단 묶음·층참)은 요소로 세지 않는다
        g = b.run("element.get", {"id": F.ST1}, project="imp.dobim")["result"]
        assert g["origin"] == "authored" and g["params"]["turn"]["type"] == turn["type"]
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("stair.set", {"id": F.ST1, "width": 1000}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_외부에서_층참을_바꾸면_분리된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": RUN, "width": 1200, "height": 3000, "risers": 18, "turn": TURN_L})["ok"]
        b.export("s.ifc")
        f = ifcopenshell.open(str(tmp_path / "s.ifc"))
        ld = [k for k in f.by_type("IfcSlab") if k.PredefinedType == "LANDING"][0]
        ld.Representation.Representations[0].Items[0].Depth = 999.0
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
    finally:
        b.close()


# ---------------------------------------------------------------- 도면
def fills_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_fills, vid)


def test_평면은_절단_높이가_층참_위일_때_층참_윤곽과_꺾이는_걸음선을_그린다(base):
    mk_stair(base)
    hi = mk(base, name="P", type="plan", level=F.LV1, cut_height=2000)               # 층참 윗면(1500) 위, 위 단 묶음은 일부 절단
    ln = lines_of(base, hi)
    # 층참 윤곽(옆선과 합쳐져 한 선분이 될 수 있어 덮는지만 본다)
    assert covers(ln, "proj", (4000.0, 400.0), (5200.0, 400.0)) and covers(ln, "proj", (5200.0, 400.0), (5200.0, 1600.0)) and covers(ln, "proj", (4000.0, 1600.0), (5200.0, 1600.0))
    assert any(x[0] == "cut" for x in ln)                                              # 위 단 묶음 절단선
    assert covers(ln, "symbol", (1500.0, 1000.0), (4600.0, 1000.0))                    # 걸음선이 층참 안(중심 x=4600)까지 이어진다
    assert covers(ln, "symbol", (4600.0, 1000.0), (4600.0, 2000.0))                    # 거기서 꺾여 위 단 묶음 걸음선(+y)으로
    low = mk(base, name="P2", type="plan", level=F.LV1, cut_height=1200)             # 층참 밑: 아래 단 묶음만, 층참 윤곽 없음
    ln2 = lines_of(base, low)
    assert not covers(ln2, "proj", (5200.0, 400.0), (5200.0, 1600.0)) and any(x[0] == "cut" for x in ln2)
    top = mk(base, name="P3", type="plan", level=F.LV1, cut_height=3500)             # 계단 위: 절단선 없음, 위 단 묶음 전체
    ln3 = lines_of(base, top)
    assert not any(x[0] == "cut" for x in ln3) and covers(ln3, "proj", (5200.0, 400.0), (5200.0, 1600.0))


def test_U형_평면은_두_단_묶음이_나란히_있다(base):
    mk_stair(base, turn=TURN_U)
    p = mk(base, name="P", type="plan", level=F.LV1, cut_height=3500)
    ln = lines_of(base, p)
    # 아래 묶음 y 400..1600, 위 묶음 y 1700..2900(간격 100): 위 묶음 옆선이 y=1700 과 2900 에 x 4000 -> 1000 방향으로 놓인다
    assert any(x[0] == "proj" and abs(x[2] - 1700.0) < 1e-6 and abs(x[4] - 1700.0) < 1e-6 for x in ln)
    assert any(x[0] == "proj" and abs(x[2] - 2900.0) < 1e-6 and abs(x[4] - 2900.0) < 1e-6 for x in ln)


def test_단면은_층참_판과_두_단_묶음을_절단한다(base):
    mk_stair(base)
    sec = mk(base, name="S", type="section", p1=[0, 1000], p2=[8000, 1000])           # 아래 단 묶음의 걸음선 위: s = x
    f = fills_of(base, sec)
    landing = [x for x in f if len(x) == 4 and abs(min(p[1] for p in x) - 1350.0) < 1e-6 and abs(max(p[1] for p in x) - 1500.0) < 1e-6]
    assert len(landing) == 1 and abs(min(p[0] for p in landing[0]) - 4000.0) < 1e-6 and abs(max(p[0] for p in landing[0]) - 5200.0) < 1e-6
    treads = [x for x in f if len(x) == 4 and max(p[1] for p in x) < 1500.0 - 1e-6 and min(p[1] for p in x) == 0.0]
    assert len(treads) == 8                                                             # 아래 단 묶음 디딤판 8
    perp = mk(base, name="S2", type="section", p1=[4600, 0], p2=[4600, 4000])         # 위 단 묶음의 걸음선 위(s = y)
    pf = fills_of(base, perp)
    up = [x for x in pf if len(x) == 4 and abs(min(p[1] for p in x) - 1350.0) < 1e-6 and max(p[1] for p in x) > 1500.0 + 1.0]
    assert len(up) == 8                                                                 # 위 단 묶음 디딤판 8 (밑은 층참 두께만큼 채움)


def test_입면에_층참_모서리가_나온다(base):
    mk_stair(base, run=[1000, -2000, 4000, -2000])
    el = mk(base, name="E", type="elevation", dir="S")
    ln = lines_of(base, el)
    assert any(x[0] == "proj" and abs(x[2] - 1500.0) < 1e-6 and abs(x[4] - 1500.0) < 1e-6 for x in ln)   # 층참 윗면 둘레


def test_꺾인_계단_증분이_전체_재생성과_같다(base):
    import s3lib
    views = {"p": mk(base, name="P", type="plan", level=F.LV1, cut_height=2000), "p2": mk(base, name="P2", type="plan", level=F.LV1, cut_height=3500),
             "s": mk(base, name="S", type="section", p1=[0, 1000], p2=[8000, 1000]), "s2": mk(base, name="S2", type="section", p1=[4600, -1000], p2=[4600, 4000]),
             "e": mk(base, name="E", type="elevation", dir="S"), "e2": mk(base, name="E2", type="elevation", dir="E")}
    before = {k: lines_of(base, v) for k, v in views.items()}

    def check():
        for k, v in views.items():
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k
            assert fills_of(base, v) == base.eng.g.q(D.view_fills, v)

    mk_stair(base)
    check()
    assert base.run("stair.set", {"id": F.ST1, "turn": TURN_U})["ok"]
    check()
    assert base.run("stair.set", {"id": F.ST1, "turn": {"type": "L", "side": "right", "risers_a": 6, "landing": 1500}})["ok"]
    check()
    assert base.run("stair.move", {"id": F.ST1, "dx": 300, "dy": 200})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("stair.delete", {"id": F.ST1})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k
    check()


def test_꺾인_계단_도면과_해시는_결정적이다(tmp_path):
    import s3lib

    def build(name):
        s = s3lib.kit.Session(tmp_path / name, guid=s3lib.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        assert s.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": RUN, "width": 1200, "height": 3000, "risers": 18, "turn": TURN_U})["ok"]
        vs = [mk(s, name="P", type="plan", level=F.LV1, cut_height=2000), mk(s, name="S", type="section", p1=[0, 1000], p2=[8000, 1000]), mk(s, name="E", type="elevation", dir="E")]
        out = ([lines_of(s, v) for v in vs], [fills_of(s, v) for v in vs], s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a[0] == b[0] and a[1] == b[1] and a[2] == b[2]
