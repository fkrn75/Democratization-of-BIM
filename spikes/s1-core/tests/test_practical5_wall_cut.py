# SPDX-License-Identifier: MPL-2.0
"""23단계(실무 모델링 5) 25.1: 벽 잘라내기(cuts, wall.cut_by) — 보·인방이 벽에서 빼낸 부피(상용 도구의 형상 결합).
합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import queries as Q
from dobim_core import wallcut as WC

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

LB = "f" + "0" * 20 + "7"     # 인방(문 D1 위, W1 두께 그대로)
BM = "f" + "0" * 20 + "8"     # 벽보다 넓은 보(W1 윗단)
HALF = "f" + "0" * 20 + "9"   # 벽 두께 절반만 차지하는 부재
SK = "f" + "0" * 20 + "a"     # 기운 부재
W5 = "1" + "0" * 20 + "5"


def member(eid, start, end, width, depth, cat="beam"):
    return ("solid.create", {"id": eid, "level": F.LV1, "category": cat, "form": "member", "start": start, "end": end,
                             "section": {"shape": "rect", "width": width, "depth": depth}, "anchor": "top"})


def run(sess, cmd):
    r = sess.run(*cmd)
    assert r["ok"], r
    return r["result"]


def lintel(sess):
    run(sess, member(LB, [900, 0, 2400], [2100, 0, 2400], 200, 300))   # u 900..2100, v -100..100, z 2100..2400


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


def fills_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_fills, vid)


def view(sess, **kw):
    return run(sess, ("view.create", kw))["id"]


def params(sess, eid):
    return sess.run("element.get", {"id": eid})["result"]["params"]


def net_area(sess, w):
    return {x[0]: x for x in sess.run("schedule.rows", {"kind": "wall"})["result"]["items"]}[w][7]


def test_순수_함수_빼기와_합집합():
    solid = [(0.0, 10.0, -1.0, 1.0, 0.0, 10.0)]
    voids = [(2.0, 4.0, -5.0, 5.0, 3.0, 5.0), (3.0, 6.0, 0.0, 5.0, 4.0, 8.0)]
    rest = WC.subtract(solid, voids)
    assert WC.union_volume(rest) + WC.union_volume(WC.clip(voids, 0, 10, 1, 0, 10)) == pytest.approx(10 * 2 * 10)
    assert WC.union_volume(rest) == pytest.approx(sum((b[1] - b[0]) * (b[3] - b[2]) * (b[5] - b[4]) for b in rest))   # 조각끼리 겹치지 않는다
    a, b = WC.split([[1.0, 5.0, -1.0, 1.0, 0.0, 1.0]], 3.0)
    assert a == [[1.0, 3.0, -1.0, 1.0, 0.0, 1.0]] and b == [[0.0, 2.0, -1.0, 1.0, 0.0, 1.0]]
    assert WC.cuts_problem([[0, 1, 0, 1, 1, 0]]) and WC.cuts_problem([]) and WC.cuts_problem([[0, 1, 0, 1, 0, 1]]) is None


def test_인방으로_자르고_떼면_행과_해시가_그대로다(base):
    lintel(base)
    h0 = base.eng.state_hash()
    a0 = net_area(base, F.W1)
    assert "cuts" not in params(base, F.W1)
    r = run(base, ("wall.cut_by", {"id": F.W1, "targets": [LB]}))
    assert r["changed"] and r["cut"] == [LB] and r["cut_volume_m3"] == pytest.approx(1.2 * 0.2 * 0.3)
    assert params(base, F.W1)["cuts"] == [[900.0, 2100.0, -100.0, 100.0, 2100.0, 2400.0]]
    d = base.run("element.get", {"id": F.W1})["result"]["derived"]
    assert d["cut_boxes"] == 1 and d["cut_volume_m3"] == pytest.approx(0.072)
    assert net_area(base, F.W1) == pytest.approx(a0 - 1200.0 * 300.0)   # 두께를 다 뚫는 상자는 입면 넓이에서 빠진다
    assert base.run("wall.cut_by", {"id": F.W1, "targets": [LB]})["result"]["changed"] is False   # 다시 해도 같다
    run(base, ("wall.set", {"id": F.W1, "cuts": []}))
    assert "cuts" not in params(base, F.W1) and base.eng.state_hash() == h0
    for _ in range(2):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_넓은_보와_반쪽_부재_겹친_상자(base):
    run(base, member(BM, [-500, 0, 3000], [6500, 0, 3000], 400, 500))
    run(base, member(HALF, [3000, 50, 1500], [4000, 50, 1500], 100, 500))
    run(base, ("wall.cut_by", {"id": F.W1, "targets": [BM, HALF, BM]}))   # 같은 대상을 두 번 주면 한 번만
    cuts = params(base, F.W1)["cuts"]
    base.eng.sync()
    start, end = base.eng.g.q(Q.wall_extent, F.W1)
    assert cuts[0] == [start, end, -100.0, 100.0, 2500.0, 3000.0]          # 벽 범위로 잘린다(두께·길이)
    assert cuts[1] == [3000.0, 4000.0, 0.0, 100.0, 1000.0, 1500.0]         # 왼쪽 반만
    a = (end - start) * 500.0
    assert net_area(base, F.W1) == pytest.approx(net_area_without(base) - a)   # 반쪽 상자는 넓이에 들지 않는다
    d = base.run("element.get", {"id": F.W1})["result"]["derived"]
    assert d["cut_volume_m3"] == pytest.approx((a * 200.0 + 1000.0 * 100.0 * 500.0) / 1e9)


def net_area_without(sess):
    h = sess.run("element.get", {"id": F.W1})["result"]["params"]["cuts"]
    run(sess, ("wall.set", {"id": F.W1, "cuts": []}))
    out = net_area(sess, F.W1)
    run(sess, ("wall.set", {"id": F.W1, "cuts": h}))
    return out


def test_거절(base):
    lintel(base)
    bad = base.run("wall.cut_by", {"id": F.W1, "targets": [F.W2]})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT" and bad["error"]["path"] == "/targets/0"
    run(base, member(SK, [1000, -500, 2400], [2000, 500, 2400], 200, 300))   # 기운 보: 축에 나란한 상자가 아니다
    bad = base.run("wall.cut_by", {"id": F.W1, "targets": [LB, SK]})
    assert not bad["ok"] and bad["error"]["path"] == "/targets/1"
    bad = base.run("wall.cut_by", {"id": F.W3, "targets": [LB]})          # 겹치지 않는다
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"
    bad = base.run("wall.set", {"id": F.W1, "cuts": [[0, 100, 0, 100, 500, 400]]})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"


def test_분할은_상자를_나눈다(base):
    run(base, member(BM, [-500, 0, 3000], [6500, 0, 3000], 400, 500))
    run(base, ("wall.create", {"id": W5, "level": F.LV1, "axis": [0, 8000, 6000, 8000]}))
    run(base, ("wall.set", {"id": W5, "cuts": [[1000.0, 5000.0, -100.0, 100.0, 2500.0, 3000.0]]}))
    run(base, ("wall.split", {"id": W5, "at": 2000}))
    news = [x for x in base.run("schedule.rows", {"kind": "wall"})["result"]["items"] if x[1].endswith("-b")]
    assert params(base, W5)["cuts"] == [[1000.0, 2000.0, -100.0, 100.0, 2500.0, 3000.0]]
    assert params(base, news[0][0])["cuts"] == [[0.0, 3000.0, -100.0, 100.0, 2500.0, 3000.0]]


def _vol(f, gid):
    import ifcopenshell.geom as geom
    import ifcopenshell.util.shape as us
    st = geom.settings()
    return us.get_volume(geom.create_shape(st, f.by_guid(gid)).geometry)


def test_IFC_몸체는_불리언_빼기이고_왕복하면_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        b0 = b.export("a.ifc")
        v0 = _vol(ifcopenshell.open(str(b0)), F.W1)
        lb = member(LB, [900, 0, 2400], [2100, 0, 2400], 200, 300)
        hf = member(HALF, [3000, 50, 1500], [4000, 50, 1500], 100, 500)
        for c in (lb, hf):
            assert b.run(*c)["ok"]
        assert b.run("wall.cut_by", {"id": F.W1, "targets": [LB, HALF]})["ok"]
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        rep = f.by_guid(F.W1).Representation.Representations[0]
        assert rep.RepresentationType == "CSG" and rep.Items[0].is_a("IfcBooleanResult") and rep.Items[0].Operator == "DIFFERENCE"
        assert f.by_guid(F.W2).Representation.Representations[0].RepresentationType == "SweptSolid"
        v1 = _vol(f, F.W1)
        cut = 1.2 * 0.2 * 0.3 + 1.0 * 0.1 * 0.5
        assert v0 - v1 == pytest.approx(cut, rel=1e-4) or v0 - v1 == pytest.approx(cut * 1e9, rel=1e-4)
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim")["foreign"] == 0 and b.origins("imp.dobim")["detached"] == 0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("element.get", {"id": F.W1}, project="imp.dobim")["result"]["params"]["cuts"][0][4] == 2100.0
        # 떼면 몸체가 돌출로 돌아간다
        assert b.run("wall.set", {"id": F.W1, "cuts": []})["ok"]
        f2 = ifcopenshell.open(str(b.export("u.ifc")))
        rep2 = f2.by_guid(F.W1).Representation.Representations[0]
        assert rep2.RepresentationType == "SweptSolid" and rep2.Items[0].is_a("IfcExtrudedAreaSolid")
    finally:
        b.close()


def test_단면과_평면_절단은_잘라낸_자리를_뺀다(base):
    lintel(base)
    run(base, member(HALF, [3000, 50, 1500], [4000, 50, 1500], 100, 500))   # v 0..100, z 1000..1500(절단 높이 1200 을 품는다)
    sec = view(base, name="X", type="section", p1=[1500, -1000], p2=[1500, 1000])
    plan = view(base, name="P", type="plan", level=F.LV1)

    def w1_rects():   # W1 의 단면 절단 사각형(채움은 인방 솔리드 절단도 담으므로 벽 값만 본다)
        fills_of(base, sec)
        return [q for q in base.eng.g.q(D.vcut_rects, sec, F.W1) if min(p[1] for p in q) >= 2000 - 1e-6]
    before = w1_rects()
    assert any(min(p[1] for p in q) == pytest.approx(2100.0) for q in before)     # 문 위 벽(2100..3000)
    lines_of(base, plan)
    run(base, ("wall.cut_by", {"id": F.W1, "targets": [LB, HALF]}))
    after = w1_rects()
    assert not any(min(p[1] for p in q) < 2400 - 1e-6 for q in after)              # 인방 자리(2100..2400)가 비었다
    assert any(min(p[1] for p in q) == pytest.approx(2400.0) for q in after)
    lines_of(base, plan)   # 선은 부재 절단선과 겹쳐 합쳐지므로 조각으로 본다
    # 평면: 절단 높이를 품는 반쪽 상자 자리(u 3000..4000, v 0..100)는 W1 절단 조각에서 빠진다(그 구간은 y -100..0 만 남는다)
    cut = base.eng.g.q(D.plan_pieces, plan, F.W1)[0]
    mid = [q for q in cut if min(p[0] for p in q) >= 3000 - 1e-6 and max(p[0] for p in q) <= 4000 + 1e-6]
    assert len(mid) == 1 and max(p[1] for p in mid[0]) == pytest.approx(0.0) and min(p[1] for p in mid[0]) == pytest.approx(-100.0)
    assert not any(min(p[0] for p in q) < 3500 < max(p[0] for p in q) and max(p[1] for p in q) > 1.0 for q in cut)
    # 입면(투영)은 그대로다
    ele = view(base, name="S", type="elevation", dir="S")
    e1 = lines_of(base, ele)
    run(base, ("wall.set", {"id": F.W1, "cuts": []}))
    assert lines_of(base, ele) == e1


def test_증분_도면이_전체_계산과_같다(base):
    lintel(base)
    vs = [view(base, name="P", type="plan", level=F.LV1),
          view(base, name="E", type="elevation", dir="S"),
          view(base, name="X", type="section", p1=[1500, -1000], p2=[1500, 1000])]
    for v in vs:
        lines_of(base, v)
    run(base, ("wall.cut_by", {"id": F.W1, "targets": [LB]}))
    for v in vs:
        inc = lines_of(base, v)
        full = s3lib.RefGraph(s3lib._Inputs(base.eng)).q(D.view_lines, v)
        assert inc == full
