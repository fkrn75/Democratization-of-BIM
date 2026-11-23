# SPDX-License-Identifier: MPL-2.0
"""W2 열 번째 PR(ARC-22 잔여 + ARC-38): 경사 커튼 시스템·커튼월 상단 붙이기·커튼월별 재료·원호 그리드와 IfcGrid.

1. 경사 커튼 시스템(유리지붕): 기준선 + 물매 + 볼록 외곽 위의 커튼 그리드. 내부 멀리언은 외곽 멀리언 폭만큼 들인 외곽 안으로 잘리고, 외곽 변마다 멀리언,
   패널은 칸 ∩ 외곽(사다리꼴·삼각형). 평면·단면·입면·IFC(IfcCurtainWall + IfcMember·IfcPlate, 기울어진 SweptSolid) 왕복
2. 커튼월 상단 붙이기(curtain.attach): 지붕 아랫면으로 상단 윤곽을 채우고 윤곽 위 칸·멀리언은 없애고 걸친 칸은 사다리꼴 유리·잘린 멀리언,
   윗면 외곽 멀리언은 윤곽을 따른다. 윤곽이 평평하게 높이와 같으면 이전과 같다. curtain.detach 로 되살린다
3. 커튼월별 재료(curtain.material.set): 종류마다 재료 이름, IFC 재료 관계·재료 외관(matdef) 색이 따라간다. 없으면 기본 재료(이전과 같다)
4. 원호 그리드(ARC-38): grid.create 의 arc_through·center/radius/각도, grid.radial(방사선 여러 개). 평면 1점쇄선 호 + 양 끝 기호.
   IFC 는 IfcGrid 하나(IfcGridAxis: 직선 IfcPolyline, 원호 IfcTrimmedCurve(IfcCircle)) — 직선 그리드도 함께 나간다. 왕복 해시·바이트
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import math
import sys

import ifcopenshell
import ifcopenshell.validate
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
V2P = "4" + "0" * 20 + "9"
CW1 = F.CW1
RF = F.RF1
ICW, ICS, IRF = ("3" + "0" * 19 + x for x in ("w1", "w2", "r1"))   # IFC 왕복 시험용: 첫 글자가 유효한 GlobalId
RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


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


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [x["message"][:160] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def setup(s):
    ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [3100, -5000], "p2": [3100, 6000]}))
    ok(s.run("view.create", {"id": VE, "name": "남측", "type": "elevation", "dir": "S"}))
    return s


@pytest.fixture
def bs(base):
    return setup(base)


def same_as_full(s, views=(V, VS, VE)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)
        assert g.q(D.view_fills, v) == full.q(D.view_fills, v)


def P(**kw):
    p = {"name": "c", "axis": [0.0, 0.0, 6000.0, 0.0], "height": 3000.0, "u_grid": {"rule": "spacing", "value": 1500.0}, "v_grid": {"rule": "count", "value": 2.0},
         "types": {"a": {"width": 50.0, "depth": 150.0}}, "mullion": {"interior": "a", "border": "a"}, "join": "vertical", "panel_thickness": 24.0}
    p.update(kw)
    return p


def SP(**kw):
    p = {"name": "s", "form": "sloped", "axis": [0.0, 0.0, 6000.0, 0.0], "slope": 30.0, "boundary": [[0.0, 0.0], [6000.0, 0.0], [6000.0, 4000.0], [0.0, 4000.0]],
         "u_grid": {"rule": "spacing", "value": 1500.0}, "v_grid": {"rule": "spacing", "value": 2000.0}, "types": {"a": {"width": 60.0, "depth": 200.0}},
         "mullion": {"interior": "a", "border": "a"}, "join": "vertical", "panel_thickness": 28.0}
    p.update(kw)
    return p


# ---------------------------------------------------------------- 1. 경사 커튼 시스템(순수 배치)
def test_1_경사_커튼_배치():
    lay = CW.layout(SP())
    c30 = math.cos(math.radians(30))
    assert lay["height"] == pytest.approx(4000 / c30)                         # v 방향은 면 위 실길이
    assert len(lay["panels"]) == 4 * 3                                         # 4 x ceil(4618.8 / 2000) = 4 x 3
    keys = {m[0] for m in lay["mullions"]}
    assert {"E0:0", "E1:0", "E2:0", "E3:0"} <= keys and "V0:0" not in keys     # 외곽 상자 가장자리 격자선 대신 외곽 변 멀리언
    sh = lay["shapes"]["P0:0"]
    assert CW.shape_area(sh) == pytest.approx(1500 * 2000)
    assert sh[4] == pytest.approx((0, -0.5, c30))                               # 법선: 기준선 왼쪽으로 오르는 면
    top = lay["shapes"]["P0:4000"]                                             # 맨 위 칸: 외곽에서 잘린다
    assert CW.shape_area(top) == pytest.approx(1500 * (4000 / c30 - 4000))
    v = lay["shapes"]["V1500:0"]                                               # 내부 멀리언은 외곽 멀리언 폭(60) 들인 안쪽에서 시작
    assert min(b for _, b in v[0][0]) == pytest.approx(60)
    assert CW.zrange(lay)[1] == pytest.approx(4000 * math.tan(math.radians(30)) + 100 * c30)   # 꼭대기 = 수평 4000 x tan30 + 멀리언 깊이의 반
    # 비스듬한 외곽(사다리꼴)과 수평 유리지붕
    lay2 = CW.layout(SP(boundary=[[0.0, 0.0], [6000.0, 0.0], [4500.0, 3000.0], [1500.0, 3000.0]], slope=0.0))
    areas = sum(CW.panel_area(lay2, x) for x in lay2["panels"])
    assert areas == pytest.approx((6000 + 3000) / 2 * 3000)                     # 패널 = 칸 ∩ 외곽: 넓이 합 = 외곽 넓이
    assert CW.zrange(lay2) == pytest.approx((-100, 100))
    # 오목 외곽·너무 가파른 물매·작은 외곽은 거절
    assert "볼록" in CW.problem(SP(boundary=[[0.0, 0.0], [6000.0, 0.0], [6000.0, 4000.0], [3000.0, 1000.0], [0.0, 4000.0]]))
    assert CW.problem(SP(slope=85.0)) is not None
    assert CW.problem(SP(boundary=[[0.0, 0.0], [50.0, 0.0], [50.0, 50.0]])) is not None


def test_1_경사_커튼_명령과_도면(bs):
    r = ok(bs.run("curtain.create_sloped", {"id": CW1, "level": F.LV1, "axis": [0, -3000, 6000, -3000], "slope": 25, "base_offset": 2500,
                                            "boundary": [[0, -3000], [6000, -3000], [6000, 1000], [0, 1000]],
                                            "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "max_spacing", "value": 2000}}))
    assert r["form"] == "sloped" and r["shaped"] == r["mullions"] + r["panels"] and r["panels"] == 12
    s = ok(bs.run("curtain.parts", {"id": CW1}))
    assert s["by_panel"]["glass"]["area_m2"] == pytest.approx(6 * 4 / math.cos(math.radians(25)), abs=1e-3)   # 경사 실면적
    ok(bs.run("curtain.mullion.set", {"id": CW1, "keys": ["E0:0"], "type": "delete"}))                          # 외곽 변 멀리언도 키로 고친다
    ok(bs.run("curtain.panel.set", {"id": CW1, "keys": ["P0:0"], "type": "opaque"}))
    ok(bs.run("curtain.grid", {"id": CW1, "dir": "u", "op": "add", "at": 750}))
    assert err(bs.run("curtain.set", {"id": CW1, "height": 3000}))["code"] == "CONSTRAINT"
    assert err(bs.run("curtain.attach", {"id": CW1, "targets": [F.LV1]}))["code"] in ("CONSTRAINT", "NOT_FOUND")
    ok(bs.run("curtain.set", {"id": CW1, "slope": 30}))
    g = g_of(bs)
    st = g.q(Q.model_stats)
    assert st["curtwall"] == 1 and st["curtain_panel"] == 15
    sec = g.q(D.curtain_lines, VS, CW1)                                       # 단면(물매 방향과 나란히 자른다): 기울어진 절단선·멀리언 모서리
    assert any(x[0] == "cut" and abs(x[2] - x[4]) > 100 and abs(x[1] - x[3]) > 100 for x in sec)
    ele = g.q(D.curtain_lines, VE, CW1)                                       # 남측 입면: 기준선과 나란한 멀리언이 높이를 달리해 겹쳐 보인다
    assert len({round(x[2]) for x in ele if abs(x[2] - x[4]) < 1e-6}) > 5
    ok(bs.run("view.create", {"id": V2P, "name": "지붕 평면", "type": "plan", "level": F.LV2, "cut_height": 1200}))
    assert g_of(bs).q(D.curtain_lines, V2P, CW1)                              # 위 레벨 평면: 절단·보이는 깊이 안의 경사 부속
    same_as_full(bs, (V, VS, VE, V2P))
    ok(bs.run("curtain.move", {"id": CW1, "dx": 1000, "dy": 0}))
    assert params(bs, CW1)["boundary"][0] == [1000, -3000]
    same_as_full(bs)


# ---------------------------------------------------------------- 2. 상단 붙이기
def test_2_상단_윤곽_배치():
    assert CW.layout(P()) == CW.layout(P(top_profile=[[0.0, 3000.0], [6000.0, 3000.0]]))    # 평평한 윤곽 = 이전과 같다
    lay = CW.layout(P(top_profile=[[0.0, 3000.0], [6000.0, 1000.0]]))
    base = CW.layout(P())
    assert len(lay["panels"]) == len(base["panels"]) - 1                     # 오른쪽 위 칸(P4500:1500)은 윤곽 위라 없다
    tri = lay["shapes"]["P3000:1500"]
    assert CW.shape_area(tri) == pytest.approx(1500 * 500 / 2)                # 걸친 칸 = 삼각형
    trap = lay["shapes"]["P4500:0"]
    assert CW.shape_area(trap) == pytest.approx(1500 * (1500 + 1000) / 2)    # 사다리꼴
    band = lay["shapes"]["H3000:0"]                                          # 윗면 외곽은 윤곽을 따라가는 띠
    assert min(b for _, b in band[0][0]) == pytest.approx(3000 - 1475 / 3 - 50)                 # 띠 아래 끝 = 윤곽(u 1475) - 폭 50
    assert "V6000:1500" not in {m[0] for m in lay["mullions"]} and "V4500:1500" in lay["shapes"]   # 윤곽 위 멀리언은 없고 걸친 것은 잘린다
    assert CW.problem(P(top_profile=[[0.0, 3500.0], [6000.0, 1000.0]])) is not None   # 높이 위 윤곽은 붙이기가 높이를 늘인다(배치는 거절)
    # 단 있는 윤곽: 칸이 둘로 갈라진다
    lay3 = CW.layout(P(top_profile=[[0.0, 3000.0], [2000.0, 3000.0], [2000.0, 1200.0], [6000.0, 1200.0]]))
    assert CW.shape_area(lay3["shapes"]["P1500:0"]) == pytest.approx(500 * 1500 + 1000 * 1200)


def test_2_지붕에_붙이고_떼기(bs):
    ok(bs.run("roof.create", {"id": RF, "level": F.LV2, "boundary": RECT, "form": "gable", "pitch": 30, "overhang": 600}))
    ok(bs.run("curtain.create", {"id": CW1, "level": F.LV1, "axis": [3000, -500, 3000, 4500], "height": 7000,
                                 "u_grid": {"rule": "spacing", "value": 1000}, "v_grid": {"rule": "spacing", "value": 1500}}))
    before = ok(bs.run("curtain.parts", {"id": CW1}))
    r = ok(bs.run("curtain.attach", {"id": CW1, "targets": [RF]}))
    assert r["changed"] and r["trimmed"] > 0 and r["top_max"] < 7000 and r["height"] == 7000
    p = params(bs, CW1)
    assert len(p["top_profile"]) >= 3                                         # 박공: 오르고 내린다
    after = ok(bs.run("curtain.parts", {"id": CW1}))
    assert after["panels"] < before["panels"] and after["by_panel"]["glass"]["area_m2"] < before["by_panel"]["glass"]["area_m2"]
    g = g_of(bs)
    lay = g.q(Q.curtain_layout, CW1)
    roofz = g.q(Q.level_info, F.LV2)[1]
    for k, sh in lay["shapes"].items():                                       # 모든 부속이 지붕 아랫면 아래(1mm)
        top = max(b for pl in sh[0] for _, b in pl)
        assert top <= max(z for _, z in p["top_profile"]) + 1e-6, k
    assert roofz > 0
    same_as_full(bs)
    ok(bs.run("curtain.detach", {"id": CW1}))
    assert ok(bs.run("curtain.parts", {"id": CW1})) == before
    ok(bs.run("history.undo", {}))
    assert "top_profile" in params(bs, CW1)
    # extend=false: 지금 높이 아래만 자른다
    ok(bs.run("curtain.detach", {"id": CW1}))
    ok(bs.run("curtain.set", {"id": CW1, "height": 3500}))
    r = ok(bs.run("curtain.attach", {"id": CW1, "targets": [RF], "extend": False}))
    assert r["height"] == 3500 and r["top_max"] <= 3500
    ok(bs.run("curtain.set", {"id": CW1, "axis": [3000, -400, 3000, 4500]}))   # 축을 바꾸면 윤곽은 떨어진다
    assert "top_profile" not in params(bs, CW1)


# ---------------------------------------------------------------- 3. 커튼월별 재료
def test_3_커튼월별_재료(base):
    ok(base.run("curtain.create", {"id": CW1, "level": F.LV1, "axis": [0, -2000, 6000, -2000], "height": 3000,
                                   "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "count", "value": 2}}))
    r = ok(base.run("curtain.material.set", {"id": CW1, "glass": "로이 복층유리", "mullion": "흑색 알루미늄"}))
    assert r["materials"] == {"door": "문", "glass": "로이 복층유리", "mullion": "흑색 알루미늄", "opaque": "불투명 패널"}
    base.eng.flush_ifc()
    f = base.eng.ifc.f
    assert f.by_guid(part_guid(CW1, "material:glass")).RelatingMaterial.Name == "로이 복층유리"
    assert f.by_guid(part_guid(CW1, "material:mullion")).RelatingMaterial.Name == "흑색 알루미늄"
    assert not [m for m in f.by_type("IfcMaterial") if m.Name == "유리"]          # 안 쓰이게 된 기본 재료는 치운다
    ap = ok(base.run("material.appearance", {"ids": [CW1]}))["items"][0]["parts"]
    assert ap["glass"]["material"] == "로이 복층유리"
    ok(base.run("curtain.material.set", {"id": CW1, "default": ["glass", "mullion"]}))
    assert "materials" not in params(base, CW1)
    base.eng.flush_ifc()
    assert base.eng.ifc.f.by_guid(part_guid(CW1, "material:glass")).RelatingMaterial.Name == "유리"
    assert err(base.run("curtain.material.set", {"id": CW1}))["code"] == "SCHEMA_INVALID"


# ---------------------------------------------------------------- IFC 왕복(1~3)
def test_123_IFC_검증과_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("material.define", {"name": "로이 복층유리", "color": [120, 170, 190], "transparency": 0.6}))
        ok(b.run("curtain.create_sloped", {"id": ICS, "level": F.LV2, "axis": [0, 0, 6000, 0], "slope": 20, "boundary": RECT,
                                           "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "max_spacing", "value": 2000}}))
        ok(b.run("curtain.material.set", {"id": ICS, "glass": "로이 복층유리"}))
        ok(b.run("roof.create", {"id": IRF, "level": F.LV2, "boundary": RECT, "form": "gable", "pitch": 30, "overhang": 600}))
        ok(b.run("curtain.create", {"id": ICW, "level": F.LV1, "axis": [3000, -500, 3000, 4500], "height": 7000,
                                    "u_grid": {"rule": "spacing", "value": 1000}, "v_grid": {"rule": "spacing", "value": 1500}}))
        ok(b.run("curtain.attach", {"id": ICW, "targets": [IRF]}))
        h = b.hashes()
        out = b.export("c.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        cs = f.by_guid(ICS)
        kids = [k for r in cs.IsDecomposedBy for k in r.RelatedObjects]
        assert {k.is_a() for k in kids} == {"IfcMember", "IfcPlate"}
        pl = f.by_guid(part_guid(ICS, "p:P0:0"))
        solid = pl.Representation.Representations[0].Items[0]
        assert solid.is_a("IfcExtrudedAreaSolid") and abs(solid.Position.Axis.DirectionRatios[2] - math.cos(math.radians(20))) < 1e-9   # 기울어진 돌출
        assert {r.RelatingMaterial.Name for k in kids for r in k.HasAssociations if r.is_a("IfcRelAssociatesMaterial")} == {"알루미늄", "로이 복층유리"}
        trim = [k for r in f.by_guid(ICW).IsDecomposedBy for k in r.RelatedObjects
                if k.Representation.Representations[0].Items[0].Position.Axis is not None]
        assert trim                                                            # 잘린 부속(수직 면 돌출)
        assert b.destructive("ifc.export", {"path": "c3.ifc", "schema": "IFC2X3"})["ok"]
        assert file_errors(ifcopenshell.open(str(tmp_path / "c3.ifc"))) == []
        r = b.import_ifc("c.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("c2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        ok(b.run("curtain.detach", {"id": ICW}))
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()


# ---------------------------------------------------------------- 4. 원호 그리드·방사 그리드(ARC-38)와 IfcGrid
def test_4_원호_그리드_명령과_평면(bs):
    r = ok(bs.run("grid.create_arc", {"name": "A", "center": [0, 0], "radius": 12000, "start_angle": 0, "end_angle": 90}))
    assert r["radius"] == pytest.approx(12000) and r["length"] == pytest.approx(12000 * math.pi / 2)
    ga = r["id"]
    ids = ok(bs.run("grid.radial", {"center": [0, 0], "angles": [0, 45, 90], "r0": 2000, "r1": 14000, "names": ["R1", "R2", "R3"]}))["ids"]
    h = math.sqrt(0.5)
    assert len(ids) == 3 and params(bs, ids[1])["line"] == pytest.approx([2000 * h, 2000 * h, 14000 * h, 14000 * h])
    assert err(bs.run("grid.radial", {"center": [0, 0], "angles": [10], "r0": 0, "r1": 5000, "names": ["R1"]}))["code"] == "CONSTRAINT"   # 같은 기호
    assert err(bs.run("grid.create_arc", {"name": "Z", "line": [0, 0, 1000, 0], "arc_through": [500, 0.1]}))["code"] == "CONSTRAINT"      # 거의 직선
    g = g_of(bs)
    segs, labels = g.q(D.grid_marks, V, ga)
    assert len(labels) == 2 and {x[2] for x in labels} == {"A"}
    rr = [math.hypot((x[1] + x[3]) / 2, (x[2] + x[4]) / 2) for x in segs]
    assert sum(1 for q in rr if abs(q - 12000) < 3) > 20                       # 1점쇄선이 호 위에(조각 현과 호의 차 2mm 이하)
    assert g.q(D.grid_marks, VS, ga) == ((), ())                               # 단면·입면에는 원호 통심선을 그리지 않는다
    assert g.q(D.grid_marks, VE, ids[2])[0]                                    # 남측 입면 보는 방향(북)과 나란한 방사선(90도)은 세로선
    ok(bs.run("grid.move", {"id": ga, "dx": 100, "dy": 0}))
    assert params(bs, ga)["arc"][0] == pytest.approx(12000 * h + 100, abs=1e-3)
    ok(bs.run("grid.set", {"id": ga, "line": [12100, 0, 100, 12000]}))           # line 만 주면 직선이 된다
    assert "arc" not in params(bs, ga)
    ok(bs.run("grid.set", {"id": ga, "arc_through": [8585.3, 8485.3]}))
    assert "arc" in params(bs, ga)
    from dobim_core import scene as SC
    assert any(t[0] == "grid" and t[1] == ga for t in g_of(bs).q(SC.view_scene, V))
    same_as_full(bs)


def test_4_한_묶음_통심선의_U_V_나누기():
    from dobim_core.ifc_model import IfcModel
    fan = {f"g{i}": (f"X{i}", (0.0, 0.0, 1000.0 * math.cos(math.radians(a)), 1000.0 * math.sin(math.radians(a))), None) for i, a in enumerate((60, 75, 90, 105, 120))}
    u, v = IfcModel.grid_want(fan)                                             # 부채꼴 방사선(모두 세로에 가깝다): 방향각 평균의 양쪽으로
    assert [x[0] for x in u] == ["X0", "X1"] and [x[0] for x in v] == ["X2", "X3", "X4"]
    par = {f"g{i}": (f"X{i}", (1000.0 * i, 0.0, 1000.0 * i, 5000.0), None) for i in range(3)}
    assert IfcModel.grid_want(par) is None                                    # 모두 평행: IfcGrid 를 내보내지 않는다(IFC 는 U·V 축이 모두 있어야 한다)
    assert IfcModel.grid_want({"a": ("A", (0.0, 0.0, 0.0, 5000.0), None)}) is None


def test_4_IfcGrid_와_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        f0 = ifcopenshell.open(str(b.export("g0.ifc")))
        assert not f0.by_type("IfcGrid")                                       # 통심선이 없으면 IFC 는 이전과 같다
        gids = [ok(b.run("grid.create", {"name": f"X{i + 1}", "line": [x, -2000, x, 10000]}))["id"] for i, x in enumerate((0, 6000, 12000))]
        gy = ok(b.run("grid.create", {"name": "Y1", "line": [-2000, 0, 14000, 0]}))["id"]
        ok(b.run("grid.create_arc", {"name": "C1", "center": [0, 0], "radius": 8000, "start_angle": 0, "end_angle": 90}))
        h = b.hashes()
        out = b.export("g.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        gr = f.by_type("IfcGrid")
        assert len(gr) == 1
        assert [a.AxisTag for a in gr[0].UAxes] == ["X1", "X2", "X3"] and [a.AxisTag for a in gr[0].VAxes] == ["C1", "Y1"]
        c1 = gr[0].VAxes[0].AxisCurve
        assert c1.is_a("IfcTrimmedCurve") and c1.BasisCurve.is_a("IfcCircle") and c1.BasisCurve.Radius == pytest.approx(8000)
        assert gr[0].ContainedInStructure[0].RelatingStructure.is_a("IfcBuilding")
        assert b.destructive("ifc.export", {"path": "g3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "g3.ifc"))
        assert file_errors(f3) == [] and len(f3.by_type("IfcGrid")) == 1
        r = b.import_ifc("g.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("g2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        ok(b.run("grid.move", {"id": gids[0], "dx": 500}, project="imp.dobim"))
        f4 = ifcopenshell.open(str(b.export("g4.ifc", "imp.dobim")))
        assert len(f4.by_type("IfcGrid")) == 1 and file_errors(f4) == []         # 가져온 파일의 IfcGrid 를 바꿔 쓴다(둘이 되지 않는다)
        assert f4.by_type("IfcGrid")[0].UAxes[0].AxisCurve.Points[0].Coordinates[0] == pytest.approx(500)
        ok(b.run("grid.delete", {"id": gy}))                                   # Y1 을 지워도 V 축은 C1 이 남는다
        assert len(ifcopenshell.open(str(b.export("g5.ifc"))).by_type("IfcGrid")) == 1
        for x in gids:                                                         # 한 방향(원호)만 남으면 IfcGrid 를 내보내지 않는다
            ok(b.run("grid.delete", {"id": x}))
        assert not ifcopenshell.open(str(b.export("g6.ifc"))).by_type("IfcGrid")
        for _ in range(4):
            ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()
