# SPDX-License-Identifier: MPL-2.0
"""슬래브 구멍(openings): 검사, 면적 제외, IFC(IfcOpeningElement + IfcRelVoidsElement) 투영·왕복, 도면(평면 X 표시·단면 구간 제외), 증분 == 전체 재생성."""
import sys

import ifcopenshell
import pytest

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

BOUND = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
HOLE = [[2000, 1000], [3000, 1000], [3000, 3000], [2000, 3000]]    # 계단실 1000 x 2000
HOLE2 = [[4000, 1000], [5000, 1000], [5000, 2000], [4000, 2000]]


def mk_slab(sess, openings=(HOLE,), **kw):
    args = {"id": F.S1, "level": F.LV1, "boundary": BOUND, "thickness": 200, "top_offset": 0, **kw}
    if openings:
        args["openings"] = [list(o) for o in openings]
    r = sess.run("slab.create", args)
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


def fills_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_fills, vid)


# ---------------------------------------------------------------- 커맨드·검사·면적
def test_구멍이_있는_슬래브는_구멍을_반시계로_저장하고_면적에서_뺀다(base):
    mk_slab(base, openings=[HOLE[::-1], HOLE2])                                   # 하나는 시계 방향으로 준다
    r = base.run("element.get", {"id": F.S1})["result"]
    assert r["params"]["openings"][0] == [[float(x), float(y)] for x, y in HOLE]  # 반시계로 되돌려 저장(시작점은 입력 순서를 따른다)  # noqa: E501
    assert r["derived"]["opening_area_m2"] == pytest.approx(2.0 + 1.0) and r["derived"]["area_m2"] == pytest.approx(24.0 - 3.0)
    assert r["derived"]["openings"] == 2


def test_잘못된_구멍은_거절한다(base):
    bad = {"경계_밖": [[5500, 1000], [6500, 1000], [6500, 2000], [5500, 2000]], "경계_접촉": [[0, 1000], [1000, 1000], [1000, 2000], [0, 2000]],
           "자기교차": [[2000, 1000], [3000, 3000], [3000, 1000], [2000, 3000]], "겹침": [[2500, 1500], [3500, 1500], [3500, 2500], [2500, 2500]]}
    for name, o in bad.items():
        ops = [HOLE, o] if name == "겹침" else [o]
        r = base.run("slab.create", {"level": F.LV1, "boundary": BOUND, "openings": ops})
        assert not r["ok"], name
    assert "slab" not in base.run("model.stats", {})["result"]["counts"]


def test_구멍_수정_이동_해제와_실행_취소(base):
    mk_slab(base, openings=None)
    h0 = base.eng.state_hash()
    assert base.run("slab.set", {"id": F.S1, "openings": [HOLE]})["ok"]
    assert base.run("slab.move", {"id": F.S1, "dx": 100, "dy": 50})["ok"]
    p = base.run("element.get", {"id": F.S1})["result"]["params"]
    assert p["openings"][0][0] == [2100.0, 1050.0] and p["boundary"][0] == [100.0, 50.0]
    assert not base.run("slab.set", {"id": F.S1, "openings": [HOLE2], "clear_openings": True})["ok"]
    assert not base.run("slab.set", {"id": F.S1, "boundary": [[0, 0], [1500, 0], [1500, 1500], [0, 1500]]})["ok"]   # 구멍이 경계 밖이 된다
    assert base.run("slab.set", {"id": F.S1, "clear_openings": True})["ok"]
    assert "openings" not in base.run("element.get", {"id": F.S1})["result"]["params"]
    for _ in range(3):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0                                               # 더했다 지우면 해시가 처음과 같다


# ---------------------------------------------------------------- IFC
def test_IFC_투영은_슬래브에_IfcOpeningElement를_IfcRelVoidsElement로_잇는다(base, tmp_path):
    mk_slab(base, openings=[HOLE, HOLE2])
    base.eng.flush_ifc()
    out = tmp_path / "s.ifc"
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    sl = f.by_guid(F.S1)
    ops = sorted((r.RelatedOpeningElement for r in sl.HasOpenings), key=lambda o: o.Name)
    assert [o.is_a() for o in ops] == ["IfcOpeningElement"] * 2
    solid = ops[0].Representation.Representations[0].Items[0]
    assert solid.is_a("IfcExtrudedAreaSolid") and solid.Depth == 220.0 and solid.Position.Location.Coordinates[2] == -210.0   # 슬래브보다 위아래 10mm
    assert base.run("model.validate", {})["ok"]
    # 구멍을 하나 줄이면 부속이 지워지고, 모두 지우면 개구부가 하나도 남지 않는다
    assert base.run("slab.set", {"id": F.S1, "openings": [HOLE]})["ok"]
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    assert len(ifcopenshell.open(str(out)).by_type("IfcOpeningElement")) == 1 + 0 + 0 + 2   # 문 둘의 개구부 + 구멍 하나(기준 프로젝트에는 문이 둘)
    assert base.run("slab.set", {"id": F.S1, "clear_openings": True})["ok"]
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    assert len(ifcopenshell.open(str(out)).by_type("IfcOpeningElement")) == 2


def test_슬래브를_지우면_구멍_부속도_사라진다(base, tmp_path):
    mk_slab(base)
    base.eng.flush_ifc()
    assert base.run("slab.delete", {"id": F.S1})["ok"]
    base.eng.flush_ifc()
    base.eng.ifc.write(str(tmp_path / "s.ifc"))
    assert len(ifcopenshell.open(str(tmp_path / "s.ifc")).by_type("IfcOpeningElement")) == 2
    assert base.run("history.undo", {})["ok"]
    base.eng.flush_ifc()
    base.eng.ifc.write(str(tmp_path / "s.ifc"))
    assert len(ifcopenshell.open(str(tmp_path / "s.ifc")).by_type("IfcOpeningElement")) == 3


def test_구멍_있는_슬래브를_내보내고_가져오면_저작으로_되살아나고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": BOUND, "thickness": 200, "openings": [HOLE, HOLE2]})["ok"]
        h = b.hashes()
        b.export("s.ifc")
        r = b.import_ifc("s.ifc", "imp.dobim")
        assert r["ok"], r
        assert b.origins("imp.dobim") == {"authored": 8, "foreign": 0, "detached": 0}   # 구멍(개구부)은 요소로 세지 않는다
        g = b.run("element.get", {"id": F.S1}, project="imp.dobim")["result"]
        assert g["origin"] == "authored" and len(g["params"]["openings"]) == 2
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("slab.set", {"id": F.S1, "openings": [HOLE]}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_외부에서_구멍_형상을_바꾸면_분리된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": BOUND, "thickness": 200, "openings": [HOLE]})["ok"]
        b.export("s.ifc")
        f = ifcopenshell.open(str(tmp_path / "s.ifc"))
        op = f.by_guid(F.S1).HasOpenings[0].RelatedOpeningElement
        op.Representation.Representations[0].Items[0].Depth = 999.0
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
    finally:
        b.close()


# ---------------------------------------------------------------- 도면
def test_평면은_슬래브가_절단_높이_아래이면_구멍_윤곽과_X를_그린다(base):
    mk_slab(base)
    p = mk(base, name="P", type="plan", level=F.LV1, cut_height=1200)
    ln = lines_of(base, p)
    assert ("proj", 2000.0, 1000.0, 3000.0, 1000.0) in ln and ("proj", 3000.0, 1000.0, 3000.0, 3000.0) in ln
    xs = [x for x in ln if x[0] == "symbol" and 2000.0 <= min(x[1], x[3]) and max(x[1], x[3]) <= 3000.0 and 1000.0 <= min(x[2], x[4]) and max(x[2], x[4]) <= 3000.0]
    assert len(xs) == 2 and {(x[1], x[2], x[3], x[4]) for x in xs} == {(2000.0, 1000.0, 3000.0, 3000.0), (2000.0, 3000.0, 3000.0, 1000.0)}   # 두 대각선(선분 방향은 정규화돼 있다)
    inside = mk(base, name="P2", type="plan", level=F.LV1, cut_height=-100)             # 절단 높이가 슬래브 두께 안: 구멍 윤곽도 절단선
    li = lines_of(base, inside)
    assert ("cut", 2000.0, 1000.0, 3000.0, 1000.0) in li
    above = mk(base, name="P3", type="plan", level=F.LV1, cut_height=-500)              # 슬래브 밑: 그리지 않는다
    assert not any(x[0] == "symbol" for x in lines_of(base, above))


def test_단면은_구멍_구간을_절단_사각형에서_뺀다(base):
    mk_slab(base)
    sec = mk(base, name="S", type="section", p1=[0, 2000], p2=[6000, 2000])            # y=2000: 구멍(x 2000..3000)을 지난다
    sl = [f for f in fills_of(base, sec) if len(f) == 4 and abs(min(q[1] for q in f) + 200.0) < 1e-6 and abs(max(q[1] for q in f)) < 1e-6]
    spans = sorted((min(q[0] for q in f), max(q[0] for q in f)) for f in sl)
    assert spans == [(0.0, 2000.0), (3000.0, 6000.0)]
    miss = mk(base, name="S2", type="section", p1=[0, 500], p2=[6000, 500])             # y=500: 구멍을 지나지 않는다
    sl2 = [f for f in fills_of(base, miss) if len(f) == 4 and abs(min(q[1] for q in f) + 200.0) < 1e-6]
    assert [(min(q[0] for q in f), max(q[0] for q in f)) for f in sl2] == [(0.0, 6000.0)]


def test_구멍이_없으면_도면이_바뀌지_않는다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1, cut_height=1200), "p2": mk(base, name="P2", type="plan", level=F.LV1, cut_height=-100),
             "s": mk(base, name="S", type="section", p1=[0, 2000], p2=[6000, 2000]), "e": mk(base, name="E", type="elevation", dir="S")}
    mk_slab(base, openings=None)
    before = {k: (lines_of(base, v), fills_of(base, v)) for k, v in views.items()}
    assert base.run("slab.set", {"id": F.S1, "openings": [HOLE]})["ok"]
    assert base.run("slab.set", {"id": F.S1, "clear_openings": True})["ok"]
    for k, v in views.items():
        assert (lines_of(base, v), fills_of(base, v)) == before[k], k


def test_구멍_증분이_전체_재생성과_같다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1, cut_height=1200), "p2": mk(base, name="P2", type="plan", level=F.LV1, cut_height=-100),
             "s": mk(base, name="S", type="section", p1=[0, 2000], p2=[6000, 2000]), "e": mk(base, name="E", type="elevation", dir="S")}
    before = {k: lines_of(base, v) for k, v in views.items()}

    def check():
        for k, v in views.items():
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k
            assert fills_of(base, v) == base.eng.g.q(D.view_fills, v)

    mk_slab(base, openings=None)
    check()
    assert base.run("slab.set", {"id": F.S1, "openings": [HOLE, HOLE2]})["ok"]
    check()
    assert base.run("slab.move", {"id": F.S1, "dx": 200, "dy": 100})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("slab.delete", {"id": F.S1})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k
    check()


def test_구멍_도면과_해시는_결정적이다(tmp_path):
    def build(name):
        s = s3lib.kit.Session(tmp_path / name, guid=s3lib.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        mk_slab(s, openings=[HOLE, HOLE2])
        vs = [mk(s, name="P", type="plan", level=F.LV1, cut_height=1200), mk(s, name="S", type="section", p1=[0, 2000], p2=[6000, 2000])]
        out = ([lines_of(s, v) for v in vs], [fills_of(s, v) for v in vs], s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a[0] == b[0] and a[1] == b[1] and a[2] == b[2]
