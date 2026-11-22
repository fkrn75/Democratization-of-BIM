# SPDX-License-Identifier: MPL-2.0
"""W2 네 번째 PR ARC-22: 커튼월(커튼 그리드·멀리언·패널).
- 격자 규칙(고정 개수·고정 간격·최대 간격·가운데 맞춤) + 개별 격자선 더하기·빼기·옮기기, 직선·곡선 축
- 멀리언: 격자선 한 칸마다의 부속(키), 교차부 우선순위, 외곽 멀리언, 개별 지우기·유형 바꾸기, 유형 고치기
- 패널: 칸마다 유리·불투명·문·빈 칸
- 도면: 평면 절단(멀리언 사각형·패널 두 면 선·문 열림), 입면 격자, 단면 절단, model.stats·curtain.parts 집계
- IFC: IfcCurtainWall + IfcRelAggregates(IfcMember·IfcPlate·IfcDoor), 재료, IFC4·IFC2X3 검증, 왕복 해시·바이트, 되돌리기, 증분 = 전체 재계산.
합성 데이터만 쓴다."""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import curtain as CW
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import queries as Q
from dobim_core.ifc_model import part_guid

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V, VS = F.V1, F.V2
VE = "4" + "0" * 20 + "8"
CW1 = F.CW1
CWI = "3" + "0" * 19 + "c1"   # IFC 시험용: 첫 글자가 유효한 GlobalId(내보내기가 바꾸지 않는다)
AXIS = [0, -2000, 6000, -2000]


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def g_of(s):
    s.eng.sync()
    s.eng._touch_pending()
    return s.eng.g


def params(s, eid):
    return ok(s.run("element.get", {"id": eid}))["params"]


def setup(s):
    ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [3100, -5000], "p2": [3100, 1000]}))
    ok(s.run("view.create", {"id": VE, "name": "남측", "type": "elevation", "dir": "S"}))
    return s


@pytest.fixture
def bs(base):
    return setup(base)


def mk(s, **kw):
    a = {"id": CW1, "level": F.LV1, "axis": AXIS, "height": 3000, "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "count", "value": 2}}
    a.update(kw)
    return ok(s.run("curtain.create", a))


def same_as_full(s, views=(V, VS, VE)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)
        assert g.q(D.view_fills, v) == full.q(D.view_fills, v)


# ---------------------------------------------------------------- 순수 배치
def P(**kw):
    p = {"name": "c", "axis": [0.0, 0.0, 6000.0, 0.0], "height": 3000.0, "u_grid": {"rule": "spacing", "value": 1500.0}, "v_grid": {"rule": "count", "value": 2.0},
         "types": {"a": {"width": 50.0, "depth": 150.0}}, "mullion": {"interior": "a", "border": "a"}, "join": "vertical", "panel_thickness": 24.0}
    p.update(kw)
    return p


def test_격자_규칙():
    assert CW.grid_lines({"rule": "count", "value": 4.0}, [], [], 6000) == [1500, 3000, 4500]
    assert CW.grid_lines({"rule": "spacing", "value": 2500.0}, [], [], 6000) == [2500, 5000]
    assert CW.grid_lines({"rule": "spacing", "value": 2500.0, "justify": "end"}, [], [], 6000) == [1000, 3500]
    assert CW.grid_lines({"rule": "spacing", "value": 2500.0, "justify": "center"}, [], [], 6000) == [500, 3000, 5500]
    assert CW.grid_lines({"rule": "max_spacing", "value": 2500.0}, [], [], 6000) == [2000, 4000]
    assert CW.grid_lines({"rule": "count", "value": 4.0}, [750.0], [3000.0], 6000) == [750, 1500, 4500]   # 개별 더하기·빼기
    lay = CW.layout(P())
    assert len(lay["us"]) == 5 and len(lay["zs"]) == 3
    # 세로 5줄 x 2칸 = 10, 가로 3줄 x 4칸 = 12, 패널 8
    assert len(lay["mullions"]) == 22 and len(lay["panels"]) == 8
    m = {x[0]: x for x in lay["mullions"]}
    assert m["V0:0"][3][0] == pytest.approx((0, -75)) and m["V0:0"][3][1] == pytest.approx((50, -75))      # 외곽은 끝 안쪽
    assert m["V1500:0"][3][0] == pytest.approx((1475, -75)) and (m["V1500:0"][4], m["V1500:0"][5]) == (0, 1500)   # 세로 연속
    h = m["H1500:0"]                                                                                     # 가로대는 세로대 사이(외곽 50, 내부 25 자른다)
    assert h[3][0] == pytest.approx((50, -75)) and h[3][1] == pytest.approx((1475, -75)) and (h[4], h[5]) == (1475, 1525)
    lay2 = CW.layout(P(join="horizontal"))
    m2 = {x[0]: x for x in lay2["mullions"]}
    assert m2["H1500:0"][3][0] == pytest.approx((0, -75)) and (m2["V1500:0"][4], m2["V1500:0"][5]) == (50, 1475)   # 가로 연속
    assert CW.problem(P(u_grid={"rule": "spacing", "value": 40.0})) is not None                          # 멀리언보다 좁은 칸
    assert "칸 수" in CW.problem(P(axis=[0.0, 0.0, 300000.0, 0.0], u_grid={"rule": "spacing", "value": 200.0}, v_grid={"rule": "spacing", "value": 10.0}))


def test_곡선_축과_큰_입면():
    p = P(axis=[0.0, 0.0, 20000.0, 0.0], arc=[10000.0, 5000.0], u_grid={"rule": "max_spacing", "value": 1200.0})
    lay = CW.layout(p)
    pts = lay["pts"]
    c = (10000.0, (5000 ** 2 - 10000 ** 2) / (2 * 5000))                      # 원 중심
    r = ((pts[0][0] - c[0]) ** 2 + (pts[0][1] - c[1]) ** 2) ** 0.5
    assert all(abs(((x - c[0]) ** 2 + (y - c[1]) ** 2) ** 0.5 - r) < 1e-6 for x, y in pts)            # 격자선 점은 호 위
    big = P(axis=[0.0, 0.0, 141000.0, 0.0], height=12000.0, u_grid={"rule": "spacing", "value": 1500.0}, v_grid={"rule": "spacing", "value": 1500.0})
    assert CW.problem(big) is None and len(CW.layout(big)["panels"]) == 94 * 8                       # 141 m 입면 하나


# ---------------------------------------------------------------- 커맨드
def test_만들기_격자_편집과_재정의(bs):
    r = mk(bs)
    assert (r["mullions"], r["panels"], r["cells"]) == (22, 8, 8)
    g = g_of(bs)
    assert g.q(Q.model_stats)["curtwall"] == 1 and g.q(Q.model_stats)["mullion"] == 22 and g.q(Q.model_stats)["curtain_panel"] == 8
    r = ok(bs.run("curtain.grid", {"id": CW1, "dir": "u", "op": "add", "at": 750}))
    assert r["u_lines"][0] == 750 and r["panels"] == 10
    assert err(bs.run("curtain.grid", {"id": CW1, "dir": "u", "op": "add", "at": 750.5}))["code"] == "CONSTRAINT"   # 이미 있다
    r = ok(bs.run("curtain.grid", {"id": CW1, "dir": "u", "op": "remove", "at": 3000}))                            # 규칙이 만든 선 빼기
    assert 3000 not in r["u_lines"] and params(bs, CW1)["u_remove"] == [3000.0]
    r = ok(bs.run("curtain.grid", {"id": CW1, "dir": "v", "op": "move", "at": 1500, "to": 1000}))
    assert r["v_lines"] == [1000]
    ok(bs.run("curtain.grid", {"id": CW1, "dir": "u", "op": "remove", "at": 750}))                                 # 더한 선을 빼면 목록에서 사라진다
    assert "u_add" not in params(bs, CW1)
    # 멀리언 지우기·유형 바꾸기, 패널 바꾸기
    ok(bs.run("curtain.type.set", {"id": CW1, "name": "굵은", "width": 100, "depth": 250}))
    ok(bs.run("curtain.mullion.set", {"id": CW1, "keys": ["V1500:0"], "type": "delete"}))
    ok(bs.run("curtain.mullion.set", {"id": CW1, "keys": ["H1000:0", "H1000:1500"], "type": "굵은"}))
    assert err(bs.run("curtain.mullion.set", {"id": CW1, "keys": ["V2222:0"], "type": "delete"}))["code"] == "CONSTRAINT"
    ok(bs.run("curtain.panel.set", {"id": CW1, "keys": ["P0:0"], "type": "door"}))
    ok(bs.run("curtain.panel.set", {"id": CW1, "keys": ["P4500:1000"], "type": "opaque"}))
    s = ok(bs.run("curtain.parts", {"id": CW1}))
    assert s["by_type"]["굵은"]["count"] == 2 and s["by_panel"]["door"]["count"] == 1 and s["by_panel"]["opaque"]["count"] == 1
    ms = ok(bs.run("curtain.parts", {"id": CW1, "what": "mullions", "key": "H1000:0"}))["items"]
    assert ms[0]["type"] == "굵은" and ms[0]["label"] == "m:H1000:0"
    assert not ok(bs.run("curtain.parts", {"id": CW1, "what": "mullions", "limit": 3}))["items"][3:]
    ok(bs.run("curtain.mullion.set", {"id": CW1, "keys": ["H1000:0"], "type": "default"}))
    assert list(params(bs, CW1)["mullion_overrides"]) == ["H1000:1500", "V1500:0"]
    ok(bs.run("curtain.set", {"id": CW1, "v_grid": {"rule": "none"}}))                                               # 규칙만 바꾸면 개별로 더한 선(1000)은 남는다
    assert ok(bs.run("curtain.parts", {"id": CW1, "what": "grid"}))["items"] == [{"dir": "u", "at": 1500}, {"dir": "u", "at": 4500}, {"dir": "v", "at": 1000}]
    ok(bs.run("curtain.grid", {"id": CW1, "dir": "v", "op": "remove", "at": 1000}))                                 # 격자가 바뀌어 없는 칸의 재정의는 버린다
    p = params(bs, CW1)
    assert list(p["mullion_overrides"]) == ["V1500:0"] and list(p["panel_overrides"]) == ["P0:0"]
    ok(bs.run("curtain.set", {"id": CW1, "border_type": "none", "join": "horizontal"}))
    assert ok(bs.run("curtain.parts", {"id": CW1}))["mullions"] == 1                                               # 외곽 없음: 내부 세로대 4500 하나(1500 은 지웠다)
    assert err(bs.run("curtain.set", {"id": CW1, "u_grid": {"rule": "spacing", "value": 30}}))["code"] == "CONSTRAINT"
    same_as_full(bs)
    ok(bs.run("history.undo", {}))
    ok(bs.run("curtain.move", {"id": CW1, "dx": 0, "dy": -500}))
    same_as_full(bs)
    r = bs.run("element.delete", {"id": CW1})
    assert r["ok"], r
    assert "curtwall" not in g_of(bs).q(Q.model_stats)


def test_도면_평면_입면_단면(bs):
    mk(bs)
    ok(bs.run("curtain.panel.set", {"id": CW1, "keys": ["P0:0"], "type": "door"}))
    g = g_of(bs)
    lines, fills = g.q(D.curtain_plan, V, CW1)
    assert len(fills) == 5                                                             # 절단 높이 1200: 세로 멀리언 5개(외곽 2 + 내부 3)
    cut = [ln for ln in lines if ln[0] == "cut"]
    assert any(abs(ln[2] - ln[4]) < 1e-9 and abs(ln[2] - (-2012)) < 1e-6 for ln in cut)   # 유리 면 선(축 -2000 에서 두께 24 의 반)
    assert any(ln[0] == "proj" for ln in lines)                                        # 문 열림 기호·바닥 가로대 윗면
    vl = g.q(D.view_lines, VE)
    xs = sorted({round(ln[1], 3) for ln in vl if abs(ln[1] - ln[3]) < 1e-9})
    for u in (0, 50, 1475, 1525, 2975, 3025, 4475, 4525, 5950, 6000):                  # 입면: 세로 멀리언 두 변(보는 방향 N 이라 s = -x)
        assert -u in xs or u in xs
    assert any(ln[0] == "cut" for ln in g.q(D.view_lines, VS))                          # 단면 x=3100: 가로대·패널 절단
    assert g.q(D.view_fills, VS)
    from dobim_core import scene as SC                                                  # GUI 장면: 커튼월이 요소 하나로(도면과 같은 곳을 덮는다)
    sys.path.insert(0, str(ROOT / "tests"))
    from test_gui_scene import cover, same_cover, scene_lines
    for v in (V, VS, VE):
        sc = g.q(SC.view_scene, v)
        assert any(t[0] == "curtwall" and t[1] == CW1 for t in sc)
        same_cover(cover(scene_lines(sc)), cover(g.q(D.view_lines, v)))
        assert sorted(p for t in sc for p in t[3]) == sorted(g.q(D.view_fills, v))
    ok(bs.run("curtain.mullion.set", {"id": CW1, "keys": ["V1500:0", "V1500:1500"], "type": "delete"}))   # 지운 멀리언 자리: 입면에 패널 가장자리가 보인다
    g = g_of(bs)
    xs = {round(ln[1], 3) for ln in g.q(D.view_lines, VE) if abs(ln[1] - ln[3]) < 1e-9}
    assert 1500 in xs and 1475 not in xs
    same_as_full(bs)


def test_큰_커튼월_도면이_무너지지_않는다(bs):
    import time
    t = time.perf_counter()
    r = mk(bs, axis=[0, -2000, 141000, -2000], height=30000, u_grid={"rule": "spacing", "value": 1500}, v_grid={"rule": "spacing", "value": 1000})
    assert r["mullions"] > 5000
    g = g_of(bs)
    for v in (V, VE, VS):
        g.q(D.view_lines, v)
    assert time.perf_counter() - t < 120


# ---------------------------------------------------------------- IFC
def _ifc_errors(ff):
    import ifcopenshell.validate as IV
    lg = IV.json_logger()
    IV.validate(ff, lg, express_rules=True)
    return [x["message"] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def test_커튼월_IFC_와_왕복(tmp_path):
    import ifcopenshell
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("curtain.create", {"id": CWI, "level": F.LV1, "axis": AXIS, "height": 3000, "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "count", "value": 2}}))
        ok(b.run("curtain.panel.set", {"id": CWI, "keys": ["P0:0"], "type": "door"}))
        ok(b.run("curtain.panel.set", {"id": CWI, "keys": ["P1500:0"], "type": "opaque"}))
        ok(b.run("curtain.mullion.set", {"id": CWI, "keys": ["V3000:0"], "type": "delete"}))
        c2 = ok(b.run("curtain.create", {"level": F.LV1, "axis": [0, -6000, 6000, -6000], "arc_through": [3000, -7500], "height": 2500,
                                         "u_grid": {"rule": "max_spacing", "value": 1000}, "v_grid": {"rule": "none"}, "border": "other", "border_width": 80, "border_depth": 200}))["id"]
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        cw = f.by_guid(CWI)
        assert cw.is_a("IfcCurtainWall")
        kids = [k for r in cw.IsDecomposedBy for k in r.RelatedObjects]
        assert len(kids) == 21 + 8                                                     # 멀리언 22 - 지운 1, 패널 8
        assert f.by_guid(part_guid(CWI, "m:V1500:0")).is_a("IfcMember") and f.by_guid(part_guid(CWI, "m:V1500:0")).PredefinedType == "MULLION"
        assert f.by_guid(part_guid(CWI, "p:P0:0")).is_a("IfcDoor") and f.by_guid(part_guid(CWI, "p:P1500:0")).PredefinedType == "CURTAIN_PANEL"
        mats = {r.RelatingMaterial.Name for k in kids for r in k.HasAssociations if r.is_a("IfcRelAssociatesMaterial")}
        assert mats == {"알루미늄", "유리", "불투명 패널", "문"}
        assert len([k for r in f.by_guid(c2).IsDecomposedBy for k in r.RelatedObjects]) > 10
        assert _ifc_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"]
        assert _ifc_errors(ifcopenshell.open(str(tmp_path / "t3.ifc"))) == []
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: x for k, x in b.hashes("imp.dobim").items() if k != "rev"} == {k: x for k, x in h.items() if k != "rev"}
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        ok(b.run("curtain.mullion.set", {"id": CWI, "keys": ["H0:0"], "type": "delete"}, project="imp.dobim"))   # 가져온 커튼월 고치기
        f2 = ifcopenshell.open(str(b.export("t5.ifc", "imp.dobim")))
        assert len([k for r in f2.by_guid(CWI).IsDecomposedBy for k in r.RelatedObjects]) == 28 and _ifc_errors(f2) == []
        ok(b.run("curtain.set", {"id": CWI, "u_grid": {"rule": "count", "value": 3}}))   # 키가 모두 바뀌는 편집
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)                                          # 되돌리면 의미 해시(state·derived·ifc)가 같다
        ok(b.run("element.delete", {"id": CWI}))
        f4 = ifcopenshell.open(str(b.export("t4.ifc")))
        assert not f4.by_type("IfcMember") or all(not m.GlobalId.startswith(part_guid(CWI, "m:V0:0")) for m in f4.by_type("IfcMember"))
        assert len(f4.by_type("IfcCurtainWall")) == 1 and _ifc_errors(f4) == []
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()
