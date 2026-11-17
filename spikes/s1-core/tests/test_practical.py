# SPDX-License-Identifier: MPL-2.0
"""19단계(실무 모델링): 곧은 부재(solid form=member: 보·가새·관·덕트), 통심선(grid) 등.
합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import solids as SO

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

from test_solid import closed_ok, volume  # noqa: E402

BEAM = {"id": F.SO1, "level": F.LV2, "category": "beam", "form": "member", "start": [0, 0, 0], "end": [6000, 0, 0],
        "section": {"shape": "rect", "width": 400, "depth": 600}}


def _m(**kw):
    return {"name": "m", "category": "beam", "form": "member", "start": [0, 0, 0], "end": [5000, 0, 0], "anchor": "top",
            "section": {"shape": "rect", "width": 300, "depth": 500}, **kw}


@pytest.mark.parametrize("sec,area", [
    ({"shape": "rect", "width": 300, "depth": 500}, 300 * 500),
    ({"shape": "h", "width": 200, "depth": 400, "web": 8, "flange": 13}, 2 * 200 * 13 + 8 * (400 - 26)),
    ({"shape": "box", "width": 200, "depth": 200, "wall": 10}, 200 * 200 - 180 * 180),
])
def test_부재_다면체는_닫혀_있고_부피가_단면적x길이다(sec, area):
    for st, en in (([0, 0, 0], [5000, 0, 0]), ([100, 200, 0], [3100, 4200, 1500]), ([0, 0, 0], [0, 0, 5000])):
        ln = sum((b - a) ** 2 for a, b in zip(st, en)) ** 0.5
        p = _m(start=st, end=en, section=sec)
        assert SO.solid_problem(p) is None, SO.solid_problem(p)
        v, f = SO.solid_mesh(p)
        assert closed_ok(v, f)
        assert volume(v, f) == pytest.approx(area * ln, rel=1e-9)


def test_원형_관은_32각형_근사이고_속이_비었다():
    p = _m(category="pipe", anchor="center", section={"shape": "pipe", "width": 100, "depth": 100, "wall": 5})
    v, f = SO.solid_mesh(p)
    assert closed_ok(v, f)
    assert len(f[0]) == 2 and len(f[0][0]) == 32   # 끝면 = 바깥 고리 + 구멍 고리
    assert 0 < volume(v, f) < 3.1416 * 50 * 50 * 5000


def test_보_기준선은_윗면_가운데다():
    v, _ = SO.solid_mesh(_m())
    zs = [q[2] for q in v]
    ys = [q[1] for q in v]
    assert max(zs) == 0.0 and min(zs) == -500.0 and min(ys) == -150.0 and max(ys) == 150.0
    v, _ = SO.solid_mesh(_m(anchor="center"))
    assert max(q[2] for q in v) == 250.0


@pytest.mark.parametrize("bad,frag", [
    ({"end": [0, 0, 0]}, "길이"),
    ({"anchor": "side"}, "anchor"),
    ({"section": {"shape": "h", "width": 200, "depth": 400}}, "키가 다르다"),
    ({"section": {"shape": "circle", "width": 200, "depth": 300}}, "지름"),
    ({"section": {"shape": "box", "width": 200, "depth": 200, "wall": 100}}, "두께"),
    ({"section": {"shape": "h", "width": 200, "depth": 400, "web": 200, "flange": 10}}, "web"),
])
def test_잘못된_부재는_이유와_함께_거절한다(bad, frag):
    why = SO.solid_problem(_m(**bad))
    assert why and frag in why


def test_보_생성_수정_이동과_IFC_클래스(base, tmp_path):
    h0 = base.eng.state_hash()
    assert base.run("solid.create", BEAM)["ok"]
    g = base.run("element.get", {"id": F.SO1})["result"]
    assert g["ifc_class"] == "IfcBeam" and g["params"]["anchor"] == "top" and g["params"]["section"] == {"shape": "rect", "width": 400.0, "depth": 600.0}
    assert base.run("solid.set", {"id": F.SO1, "section": {"shape": "h", "width": 200, "depth": 400, "web": 8, "flange": 13}})["ok"]
    assert base.run("solid.move", {"id": F.SO1, "dx": 100, "dz": -50})["ok"]
    p = base.run("element.get", {"id": F.SO1})["result"]["params"]
    assert p["start"] == [100.0, 0.0, -50.0] and p["section"]["web"] == 8.0
    assert not base.run("solid.set", {"id": F.SO1, "polygon": [[0, 0], [1, 0], [1, 1]]})["ok"]   # 다른 형태의 필드
    assert not base.run("solid.create", {**BEAM, "id": None, "section": {"shape": "pipe", "width": 100, "depth": 100}})["ok"]
    for cat, cls in (("pipe", "IfcPipeSegment"), ("duct", "IfcDuctSegment"), ("cable_tray", "IfcCableCarrierSegment")):
        r = base.run("solid.create", {**BEAM, "id": None, "category": cat, "anchor": "center"})
        assert r["ok"], r
        assert base.run("element.get", {"id": r["result"]["id"]})["result"]["ifc_class"] == cls
    base.eng.flush_ifc()
    out = tmp_path / "m.ifc"
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    b = f.by_guid(F.SO1)
    assert b.is_a("IfcBeam") and b.Representation.Representations[0].Items[0].is_a("IfcFacetedBrep")
    assert len(f.by_type("IfcPipeSegment")) == 1 and len(f.by_type("IfcDuctSegment")) == 1
    for _ in range(6):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_부재를_내보내고_가져오면_저작으로_되살아나고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("solid.create", BEAM)["ok"]
        assert b.run("solid.create", {**BEAM, "id": "f" + "0" * 20 + "2", "category": "pipe", "anchor": "center",
                                      "section": {"shape": "pipe", "width": 100, "depth": 100, "wall": 4}})["ok"]
        h = b.hashes()
        b.export("r.ifc")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        o = b.origins("imp.dobim")
        assert o["foreign"] == 0 and o["detached"] == 0, o
        g = b.run("element.get", {"id": "f" + "0" * 20 + "2"}, project="imp.dobim")["result"]
        assert g["origin"] == "authored" and g["params"]["section"]["shape"] == "pipe"
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


def test_입면은_보를_그린다(base):
    assert base.run("solid.create", {**BEAM, "start": [0, -500, 0], "end": [6000, -500, 0]})["ok"]
    vid = base.run("view.create", {"name": "S", "type": "elevation", "dir": "S"})["result"]["id"]
    base.eng.sync()
    base.eng._touch_pending()
    segs = base.eng.g.q(D.view_lines, vid)
    assert any(s[0] == "proj" for s in segs)


# ---------------------------------------------------------------- 통심선(grid)
def test_통심선_생성_수정_이동_삭제와_기호_유일(base):
    h0 = base.eng.state_hash()
    r = base.run("grid.create", {"name": "X1", "line": [0, -2000, 0, 8000]})
    assert r["ok"], r
    gid = r["result"]["id"]
    assert not base.run("grid.create", {"name": "X1", "line": [3000, -2000, 3000, 8000]})["ok"]   # 같은 기호
    assert not base.run("grid.create", {"name": "X9", "line": [0, 0, 0, 0]})["ok"]                 # 길이 0
    assert base.run("grid.set", {"id": gid, "bubbles": "end", "name": "1"})["ok"]
    assert base.run("grid.move", {"id": gid, "dx": 100})["ok"]
    p = base.run("element.get", {"id": gid})["result"]["params"]
    assert p == {"name": "1", "line": [100.0, -2000.0, 100.0, 8000.0], "bubbles": "end"}
    assert base.run("grid.delete", {"id": gid})["ok"]
    for _ in range(4):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def _view_data(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid), sess.eng.g.q(D.view_labels, vid)


def test_통심선은_평면에_1점쇄선과_원_기호를_입면에_세로선을_그린다(base):
    vp = base.run("view.create", {"name": "P", "type": "plan", "level": F.LV1, "cut_height": 1200})["result"]["id"]
    ve = base.run("view.create", {"name": "S", "type": "elevation", "dir": "S"})["result"]["id"]
    l0, t0 = _view_data(base, vp)
    e0, et0 = _view_data(base, ve)
    assert base.run("grid.create", {"name": "X1", "line": [-1000, -3000, -1000, 8000]})["ok"]   # 남북 방향: 남측 입면에 세로선
    assert base.run("grid.create", {"name": "Y1", "line": [-3000, -1000, 9000, -1000], "bubbles": "start"})["ok"]
    l1, t1 = _view_data(base, vp)
    assert len(l1) > len(l0) and {t[2] for t in t1} - {t[2] for t in t0} == {"X1", "Y1"}
    assert sum(1 for t in t1 if t[2] == "X1") == 2 and sum(1 for t in t1 if t[2] == "Y1") == 1
    e1, et1 = _view_data(base, ve)
    new = set(e1) - set(e0)
    assert new and all(abs(s[1] - s[3]) < 1e-6 or s[0] == "symbol" for s in new)
    assert [t[2] for t in et1 if t not in et0] == ["X1"]   # 동서 방향 통심선은 남측 입면에 안 나온다


def test_통심선을_내보내고_가져오면_정의가_되살아난다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("grid.create", {"id": "g" + "0" * 20 + "1", "name": "A", "line": [0, -1000, 0, 9000]})["ok"]
        h = b.hashes()
        b.export("r.ifc")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        g = b.run("element.get", {"id": "g" + "0" * 20 + "1"}, project="imp.dobim")["result"]
        assert g["params"]["name"] == "A"
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


# ---------------------------------------------------------------- 벽 자동 조인
def test_벽_자동_조인은_L_모서리만_잇고_T와_나란한_벽은_두고_실행_취소된다(sess):
    for name, args in F.SCRIPT[:1]:
        assert sess.run(name, args)["ok"]
    lv = F.SCRIPT[0][1]["id"]
    ws = {}
    for k, ax in (("a", [0, 0, 6000, 0]), ("b", [6000, 0, 6000, 4000]), ("c", [3000, 100, 3000, 3000]), ("d", [6000, 4000, 9000, 4000])):
        r = sess.run("wall.create", {"level": lv, "axis": ax, "thickness": 200, "height": 3000})
        assert r["ok"], r
        ws[k] = r["result"]["id"]
    h0 = sess.eng.state_hash()
    r = sess.run("join.auto", {"level": lv})
    assert r["ok"], r
    pairs = {frozenset(p) for p in r["result"]["joined"]}
    assert pairs == {frozenset((ws["a"], ws["b"])), frozenset((ws["b"], ws["d"]))}   # c 는 a 의 중간에 닿는 T, d 와 a 는 나란하다
    assert sess.run("join.auto", {"level": lv})["result"]["count"] == 0   # 다시 돌려도 더 걸지 않는다
    for _ in range(2):   # 효과 없는 실행도 이력 한 칸이면 두 번 되돌린다
        if sess.eng.state_hash() == h0:
            break
        assert sess.run("history.undo", {})["ok"]
    assert sess.eng.state_hash() == h0


# ---------------------------------------------------------------- 경사 판(plate)
def _pl(**kw):
    return {"name": "p", "category": "roof", "form": "plate", "polygon": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 300,
            "base": [0, 0, 3000], "pitch": 30.0, "dir": 180.0, **kw}


def test_경사_판은_닫혀_있고_부피가_수직두께x면적이며_윗면이_물매대로_기운다():
    p = _pl(holes=[[[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]])
    assert SO.solid_problem(p) is None, SO.solid_problem(p)
    v, f = SO.solid_mesh(p)
    assert closed_ok(v, f)
    assert volume(v, f) == pytest.approx((6000 * 4000 - 1000 * 1000) * 300, rel=1e-9)
    a, b, c = SO.plate_plane(p)
    t = 0.5773502691896257   # tan 30
    assert a == pytest.approx(t) and b == pytest.approx(0, abs=1e-12) and c == pytest.approx(3000)   # dir 180(-x 로 흐름) => +x 로 올라간다
    assert max(q[2] for q in v) == pytest.approx(3000 + 6000 * t)


def test_오목_윤곽_경사_판():
    p = _pl(polygon=[[0, 0], [6000, 0], [6000, 4000], [3000, 4000], [3000, 2000], [0, 2000]], pitch=0.0)
    v, f = SO.solid_mesh(p)
    assert closed_ok(v, f) and volume(v, f) == pytest.approx((6000 * 2000 + 3000 * 2000) * 300)


@pytest.mark.parametrize("bad,frag", [
    ({"pitch": 85.0}, "pitch"),
    ({"holes": [[[-100, 100], [500, 100], [500, 500]]]}, "윤곽 안"),
    ({"holes": [[[100, 100], [900, 100], [900, 900], [100, 900]], [[500, 500], [1500, 500], [1500, 1500], [500, 1500]]]}, "겹친다"),
    ({"thickness": 0}, "thickness"),
])
def test_잘못된_경사_판은_거절한다(bad, frag):
    why = SO.solid_problem(_pl(**bad))
    assert why and frag in why


def test_경사_판_명령과_IFC와_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        r = b.run("solid.create", {"id": F.SO1, "level": F.LV2, "category": "roof", "form": "plate", "polygon": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]],
                                   "holes": [[[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]], "thickness": 300, "base": [0, 0, 0], "pitch": 30, "dir": 180})
        assert r["ok"], r
        assert b.run("solid.move", {"id": F.SO1, "dx": 100, "dz": 50})["ok"]
        p = b.run("element.get", {"id": F.SO1})["result"]["params"]
        assert p["base"] == [100.0, 0.0, 50.0] and p["holes"][0][0] == [1100.0, 1000.0]
        assert b.run("solid.set", {"id": F.SO1, "holes": []})["ok"]
        assert "holes" not in b.run("element.get", {"id": F.SO1})["result"]["params"]
        h = b.hashes()
        b.export("r.ifc")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim")["foreign"] == 0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()
