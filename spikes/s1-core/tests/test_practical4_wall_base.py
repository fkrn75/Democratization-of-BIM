# SPDX-License-Identifier: MPL-2.0
"""22단계(실무 모델링 4) 24.1: 벽 바닥 오프셋(base_offset, 상용 도구의 Base Offset).
합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

W5 = "1" + "0" * 20 + "5"


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


def fills_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_fills, vid)


def view(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def params(sess, eid):
    return sess.run("element.get", {"id": eid})["result"]["params"]


def test_바닥_오프셋을_주고_떼면_행과_해시가_그대로다(base):
    h0 = base.eng.state_hash()
    assert "base_offset" not in params(base, F.W1)                     # 입력이 없으면 행에 키가 없다
    r = base.run("wall.set", {"id": F.W1, "base_offset": -300, "height": 3300})
    assert r["ok"], r
    assert params(base, F.W1)["base_offset"] == -300.0
    g = base.run("element.get", {"id": F.W1})["result"]["derived"]
    assert g["bottom_rel"] == -300.0 and g["top_rel"] == 3000.0
    rows = {x[0]: x for x in base.run("schedule.rows", {"kind": "wall"})["result"]["items"]}
    assert rows[F.W1][5] == 3300.0                                      # 높이 칸 = 바닥에서 잰 height
    assert base.run("wall.set", {"id": F.W1, "base_offset": 0, "height": 3000})["ok"]
    assert "base_offset" not in params(base, F.W1)                     # 0 이면 키를 뗀다
    assert base.eng.state_hash() == h0
    for _ in range(2):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_만들기_분할과_검사(base):
    r = base.run("wall.create", {"id": W5, "level": F.LV1, "axis": [0, 8000, 6000, 8000], "height": 3200, "base_offset": -200})
    assert r["ok"], r
    assert base.run("wall.create", {"level": F.LV1, "axis": [0, 9000, 6000, 9000], "base_offset": 0})["ok"]
    assert base.run("wall.split", {"id": W5, "at": 2000})["ok"]
    news = [x for x in base.run("schedule.rows", {"kind": "wall"})["result"]["items"] if x[1].endswith("-b")]
    assert len(news) == 1 and params(base, news[0][0])["base_offset"] == -200.0   # 분할한 끝 쪽도 같은 바닥
    # 상단 윤곽은 바닥보다 높아야 한다
    bad = base.run("wall.set", {"id": W5, "top_profile": [[0, -100], [2000, -100]]})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"
    # 모양 개구부는 레벨 기준 높이: 벽 바닥(-200) 아래로 나가면 거절, 바닥 안이면 된다
    op = {"shape": "rect", "offset": 1000, "sill": -300, "width": 600, "height": 600}
    assert not base.run("wall.set", {"id": W5, "openings": [op]})["ok"]
    assert base.run("wall.set", {"id": W5, "openings": [dict(op, sill=-150)]})["ok"]
    # 바닥을 올리면 문턱(0)이 바닥 아래가 되는 문은 거절된다
    bad = base.run("wall.set", {"id": F.W3, "base_offset": 500})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"
    assert base.run("wall.set", {"id": F.W2, "base_offset": 500})["ok"]   # 문 없는 벽은 된다
    bad = base.run("door.place", {"host": F.W2, "offset": 2000, "width": 900, "height": 2100})
    assert not bad["ok"] and bad["error"]["path"] == "/sill"
    assert base.run("door.place", {"host": F.W2, "offset": 2000, "width": 900, "height": 2100, "sill": 500})["ok"]


def test_윤곽_벽에_바닥_오프셋이면_넓이와_높이를_바닥에서_잰다(base):
    r = base.run("wall.set", {"id": F.W2, "base_offset": -100, "top_profile": [[0, 3000], [2000, 4000], [4000, 3000]]})
    assert r["ok"], r
    g = base.run("element.get", {"id": F.W2})["result"]["derived"]
    area = (3000.0 + 4000.0) / 2.0 * 4000.0 + 100.0 * 4000.0
    assert g["gross_area_m2"] == pytest.approx(area / 1e6) and g["top_rel"] == 4000.0 and g["bottom_rel"] == -100.0
    rows = {x[0]: x for x in base.run("schedule.rows", {"kind": "wall"})["result"]["items"]}
    assert rows[F.W2][5] == 4100.0


def test_IFC_몸체는_바닥에서_시작하고_왕복하면_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("wall.set", {"id": F.W1, "base_offset": -100, "height": 3100})["ok"]
        assert b.run("wall.set", {"id": F.W2, "base_offset": -100, "top_profile": [[0, 3000], [2000, 3500], [4000, 3000]]})["ok"]
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        sol = f.by_guid(F.W1).Representation.Representations[0].Items[0]
        assert tuple(sol.Position.Location.Coordinates) == (0.0, 0.0, -100.0) and sol.Depth == 3100.0
        prof = f.by_guid(F.W2).Representation.Representations[0].Items[0]
        zs = sorted({round(p.Coordinates[1], 3) for p in prof.SweptArea.OuterCurve.Points})
        assert zs == [-100.0, 3000.0, 3500.0]
        assert tuple(f.by_guid(F.W3).Representation.Representations[0].Items[0].Position.Location.Coordinates) == (0.0, 0.0, 0.0)
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim")["foreign"] == 0 and b.origins("imp.dobim")["detached"] == 0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("element.get", {"id": F.W1}, project="imp.dobim")["result"]["params"]["base_offset"] == -100.0
    finally:
        b.close()


def test_단면과_입면은_바닥_오프셋까지_그린다(base):
    assert base.run("wall.set", {"id": F.W2, "base_offset": -600, "height": 3600})["ok"]
    across = view(base, name="Y", type="section", p1=[5000, 1000], p2=[7000, 1000])
    rects = fills_of(base, across)
    assert any(min(p[1] for p in q) == pytest.approx(-600.0) and max(p[1] for p in q) == pytest.approx(3000.0) for q in rects)
    ele = view(base, name="E", type="elevation", dir="E")
    segs = lines_of(base, ele)
    assert min(min(s[2], s[4]) for s in segs if s[0] == "proj") == pytest.approx(-600.0)
    # 평면: 절단 높이(1200)는 벽 안이라 그대로 절단된다. 바닥을 절단 높이 위로 올리면 절단되지 않는다
    v = view(base, name="P", type="plan", level=F.LV1)
    def w2_cuts():   # W2 면의 세로 절단선(가운데 구간: 이웃 벽 끝선은 빼고)
        return [s for s in lines_of(base, v) if s[0] == "cut" and abs(s[1] - s[3]) < 1e-6 and 5850 < s[1] < 6150 and min(s[2], s[4]) < 2000 < max(s[2], s[4])]
    assert w2_cuts()
    assert base.run("wall.set", {"id": F.W2, "base_offset": 1500, "height": 1500})["ok"]
    assert not w2_cuts()


def test_증분_도면이_전체_계산과_같다(base):
    vs = [view(base, name="P", type="plan", level=F.LV1),
          view(base, name="E", type="elevation", dir="E"),
          view(base, name="X", type="section", p1=[5000, 1000], p2=[7000, 1000])]
    for v in vs:
        lines_of(base, v)
    assert base.run("wall.set", {"id": F.W2, "base_offset": -400, "height": 3400})["ok"]
    for v in vs:
        inc = lines_of(base, v)
        full = s3lib.RefGraph(s3lib._Inputs(base.eng)).q(D.view_lines, v)
        assert inc == full
