# SPDX-License-Identifier: MPL-2.0
"""W2 열여섯 번째 PR: 평지붕(물매 0, ARC-19·ARC-21 잔여)과 처마 부속 변 번호 기본값(입력 순서).

사용자 결정: 평지붕 = 물매 0 지붕(배수 물매는 만들지 않는다). roof.create form=flat(pitch 생략 또는 0, 물매 0 으로 저장).
1. 재현: 이전에는 물매 0 지붕을 만들 수 없었다(pitch 0 스키마 거절, footprint·poly 의 slopes 가 모두 0 이면 거절) -> form=flat
2. 형상: 수평 윗면 = 레벨 + base_offset, 두께는 아래로. 오목 외곽·처마 내밀기(변마다 overhangs)·원호 변(bulges, 지붕 개구와 같은 규약)
3. 편집·형식 바꾸기·이동·대칭·되돌리기·저장 왕복, 지붕 유형(층 구성)
4. 상호작용: 지붕 개구(vertical·face·shaft also), 벽·커튼월 상단 붙이기, 처마 부속(원호 변은 그 호의 모든 현)
5. 도면: 평면(처마 윤곽·처마 선), 반사 천장 평면(밑면 윤곽), 단면 절단 띠, 입면(원호 현 사이 수직 모서리 없음), 수량(면적·체적)
6. IFC: IfcRoof(FLAT_ROOF) + 면 하나 IfcSlab(ROOF), IFC4·IFC2X3 검증 오류 0, 가져오기 왕복 해시·바이트
7. roof.eaves.set 의 변 번호 기본값 = 입력 순서(numbering=input). stored 를 명시하면 이전 동작(저장 순서 + 경고)
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import math
import sys

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import queries as Q
from dobim_core.ifc_model import part_guid

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

RF = F.RF1
VP = "4" + "0" * 19 + "p1"     # 2F 평면(절단 1200: 평지붕 윗면 3300 위)
VS = "4" + "0" * 19 + "s1"     # x = 2000 단면
VE = "4" + "0" * 19 + "e1"     # 남측 입면
VC = "4" + "0" * 19 + "c1"     # 1F 반사 천장 평면
L_SHAPE = [[0, 0], [8000, 0], [8000, 3000], [3000, 3000], [3000, 6000], [0, 6000]]   # 오목(L) 외곽
Q45 = math.tan(math.radians(90 / 4))   # 사분원 볼록도
BAND = [[10000, 0], [20000, 0], [0, 20000], [0, 10000]]   # 사분 고리(바깥 R20000·안 R10000) 캐노피: 원호 변 둘


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


def erow(s, e):
    from dobim_core import schedules as SC
    return g_of(s).q(SC.element_row, e)


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [x["message"][:160] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def same_as_full(s, views=(VP, VS, VE, VC)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)


def views(s):
    ok(s.run("view.create", {"id": VP, "name": "2F", "type": "plan", "level": F.LV2, "cut_height": 1200}))
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [2000, -1500], "p2": [2000, 7500]}))
    ok(s.run("view.create", {"id": VE, "name": "남", "type": "elevation", "dir": "S"}))
    ok(s.run("view.rcp", {"id": VC, "name": "1F 천장", "level": F.LV1}))


def poly_area(pts):
    return abs(K.polygon_area2([list(q) for q in pts])) / 2.0


# ---------------------------------------------------------------- 1. 재현: 물매 0 지붕
def test_1_물매_0_지붕은_flat_으로만_만든다(base):
    box = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
    e = err(base.run("roof.create", {"level": F.LV2, "boundary": box, "form": "gable", "pitch": 0}))
    assert e["code"] == "CONSTRAINT" and "form=flat" in e["message"]                # 이전: 스키마 거절(pitch > 0). 이제 평지붕을 안내한다
    assert err(base.run("roof.create", {"level": F.LV2, "boundary": box, "form": "hip"}))["code"] == "CONSTRAINT"   # 경사 지붕은 물매 필수
    for form in ("footprint", "poly"):   # 변 물매가 모두 0 인 외곽선·다각형 지붕은 이전처럼 거절(평지붕은 form=flat 하나로)
        assert err(base.run("roof.create", {"level": F.LV2, "boundary": box, "form": form, "pitch": 30, "slopes": [0, 0, 0, 0]}))["code"] == "CONSTRAINT"
    assert err(base.run("roof.create", {"level": F.LV2, "boundary": box, "form": "flat", "pitch": 2}))["code"] == "CONSTRAINT"   # 평지붕은 물매 0
    assert err(base.run("roof.create", {"level": F.LV2, "boundary": box, "form": "flat", "slopes": [0, 0, 0, 0]}))["code"] == "CONSTRAINT"
    assert err(base.run("roof.create", {"level": F.LV2, "boundary": box, "form": "gable", "pitch": 30, "bulges": [0, 0.2, 0, 0]}))["code"] == "CONSTRAINT"
    r = ok(base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": box, "form": "flat", "pitch": 0}))
    p = params(base, RF)
    assert r["id"] == RF and p["form"] == "flat" and p["pitch"] == 0.0 and p["thickness"] == 200.0 and "slopes" not in p and "bulges" not in p
    # 기존 지붕의 행은 그대로다(새 키 없음)
    ok(base.run("roof.create", {"id": F.RF1[:-1] + "9", "level": F.LV2, "boundary": box, "form": "gable", "pitch": 30, "overhang": 600}))
    assert set(params(base, F.RF1[:-1] + "9")) == {"name", "boundary", "form", "pitch", "overhang", "base_offset", "thickness"}


# ---------------------------------------------------------------- 2. 형상·도면·수량
def test_2_오목_외곽_평지붕의_형상_도면_수량(base):
    views(base)
    ok(base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": L_SHAPE, "form": "flat", "thickness": 250, "overhang": 300}))
    g = g_of(base)
    gm = g.q(Q.roof_gm, RF)
    assert len(gm["faces"]) == 1 and gm["planes"] == [(0.0, 0.0, 0.0)] and gm["zlow"] == gm["ztop"] == 0.0
    assert not gm["ridges"] and not gm["hips"] and not gm["valleys"] and not gm["arrows"] and gm["fpitch"] == [0.0]
    ol = gm["outline"]
    assert sorted((round(x, 6), round(y, 6)) for x, y in ol) == sorted([(-300, -300), (8300, -300), (8300, 3300), (3300, 3300), (3300, 6300), (-300, 6300)])
    area = poly_area(ol)
    assert sum(poly_area([(q[0], q[1]) for q in pc]) for pc, _ in gm["pieces"]) == pytest.approx(area)   # 볼록 조각이 윤곽을 덮는다
    row = erow(base, RF)
    assert row["form"] == "flat" and row["pitch"] == 0.0 and row["area"] == pytest.approx(area) and row["volume"] == pytest.approx(area * 250)
    # 평면(절단 1200 < 윗면 3300): 처마 윤곽과 처마 선, 물매 화살표·문자 없음
    segs, labels = g.q(D.roof_plan_parts, VP, RF)
    assert len(segs) == 12 and all(x[0] == "proj" for x in segs) and labels == ()
    # 단면 x = 2000: 절단 띠 윗면 3300, 아랫면 3050, 처마 끝 -300 ~ 6300
    cuts = g.q(D.roof_cut_rects, VS, RF)
    s0 = min(c[0][0] for c in cuts)
    s1 = max(c[1][0] for c in cuts)
    assert (s1 - s0) == pytest.approx(6600.0) and {round(c[2][1], 6) for c in cuts} == {3300.0} and {round(c[0][1], 6) for c in cuts} == {3050.0}
    assert any(x[0] == "cut" for x in g.q(D.roof_lines, VS, RF))
    # 입면(남): 윗선·아랫선이 수평, 뒤쪽(오목 꼭짓점)의 수직 모서리는 판이 가린다
    el = g.q(D.roof_lines, VE, RF)
    zs = {round(x[2], 3) for x in el} | {round(x[4], 3) for x in el}
    assert zs == {3300.0, 3050.0} and not any(x[1] == x[3] == 3300.0 for x in el)
    # 반사 천장 평면(1F, 절단 2300, 깊이 2000): 밑면 3050 이 보이는 깊이 안 -> 처마 윤곽
    rc = g.q(D.roof_plan_parts, VC, RF)[0]
    assert len(rc) == 6 and all(x[0] == "proj" for x in rc)
    assert RF in g.q(D.scope_roofs, VC)
    same_as_full(base)


def test_2_원호_외곽_평지붕과_처마_부속(base):
    views(base)
    r = ok(base.run("roof.create", {"id": RF, "level": F.LV1, "base_offset": 3000, "boundary": BAND, "form": "flat", "thickness": 150,
                                    "bulges": [0, Q45, 0, -Q45]}))
    p = params(base, RF)
    assert p["bulges"] == pytest.approx([0, Q45, 0, -Q45]) and r["id"] == RF
    g = g_of(base)
    gm = g.q(Q.roof_gm, RF)
    assert len(gm["eave_loop"]) == 4 + 7 + 7                                       # 사분원 = 한 바퀴 32 현의 8 현
    tri = 0.5 * 8 * math.sin(2 * math.pi / 32)
    want = tri * (20000 ** 2 - 10000 ** 2)                                          # 현으로 펼친 고리 넓이
    assert erow(base, RF)["area"] == pytest.approx(want, rel=1e-9)
    # 입면: 현 사이 꼭짓점에는 수직 모서리가 없다(모서리 넷만)
    vert = [(a, b) for a, b in g.q(D.roof_edges3, RF) if abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9]
    assert len(vert) == 4
    # 처마 부속: 호 변에 단 fascia 는 그 호의 모든 현(8 조각), 내밀기 없는 처마 반자는 없다
    ok(base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": [1], "width": 30, "depth": 300}}))
    pcs = K.roof_eave_pieces(params(base, RF))
    assert len(pcs) == 8 and all(pc[1] == "fascia" for pc in pcs)
    e = ok(base.run("roof.eaves", {"id": RF}))["edges"]
    arc = 8 * 2 * 20000 * math.sin(math.pi / 32)
    assert e[1]["on"] == ["fascia"] and e[1]["eave_length"] == pytest.approx(arc, abs=1e-2)
    # 원호를 떼면 직선 외곽(사다리꼴), 다시 주면 원호
    ok(base.run("roof.set", {"id": RF, "bulges": []}))
    assert "bulges" not in params(base, RF)
    ok(base.run("history.undo", {}))
    assert "bulges" in params(base, RF)
    # 반사 천장 평면(1F, 절단 2300): 밑면 2850 -> 윤곽 + 처마 부속
    rc = g_of(base).q(D.roof_plan_parts, VC, RF)[0]
    assert len(rc) == 18 + 8 * 4
    same_as_full(base)


def test_2_처마_내밀기_변마다와_거절(base):
    ok(base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": L_SHAPE, "form": "flat", "overhangs": [600, 0, 0, 0, 0, 0]}))
    gm = g_of(base).q(Q.roof_gm, RF)
    assert min(y for _x, y in gm["outline"]) == pytest.approx(-600.0) and min(x for x, _y in gm["outline"]) == pytest.approx(0.0)
    # 오목 꼭짓점 쪽 짧은 변이 처마에 먹혀 뒤집히는 내밀기는 거절
    notch = [[0, 0], [6000, 0], [6000, 3000], [3100, 3000], [3100, 2000], [2900, 2000], [2900, 3000], [0, 3000]]
    e = err(base.run("roof.create", {"level": F.LV2, "boundary": notch, "form": "flat", "overhang": 500}))
    assert e["code"] == "CONSTRAINT" and "처마" in e["message"]
    assert err(base.run("roof.create", {"level": F.LV2, "boundary": [[0, 0], [5000, 0], [0, 5000], [5000, 5000]], "form": "flat"}))["code"] == "CONSTRAINT"   # 자기 교차
    assert err(base.run("roof.create", {"level": F.LV2, "boundary": L_SHAPE, "form": "flat", "bulges": [0, 0, 1]}))["code"] == "CONSTRAINT"   # 볼록도 수


# ---------------------------------------------------------------- 3. 편집·되돌리기·저장 왕복·유형
def test_3_편집_형식_바꾸기_이동_대칭_저장(base):
    ok(base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": BAND, "form": "flat", "bulges": [0, Q45, 0, -Q45]}))
    h0 = base.eng.state_hash()
    ok(base.run("roof.set", {"id": RF, "thickness": 300, "overhang": 200, "base_offset": 500}))
    p = params(base, RF)
    assert (p["thickness"], p["overhang"], p["base_offset"], p["pitch"]) == (300.0, 200.0, 500.0, 0.0)
    assert err(base.run("roof.set", {"id": RF, "pitch": 5}))["code"] == "CONSTRAINT"              # 평지붕은 물매 0
    assert err(base.run("roof.set", {"id": RF, "form": "footprint"}))["code"] == "CONSTRAINT"     # 경사 형식으로는 물매도 준다
    ok(base.run("roof.move", {"id": RF, "dx": 1000, "dy": -500}))
    assert params(base, RF)["boundary"][0] == [11000.0, -500.0] and params(base, RF)["bulges"] == pytest.approx([0, Q45, 0, -Q45])
    a0 = erow(base, RF)["area"]
    ok(base.run("element.mirror", {"ids": [RF], "axis": [0, 0, 0, 1000]}))                    # 대칭: 원호의 부푼 쪽이 같은 변을 따라간다
    q = params(base, RF)
    assert erow(base, RF)["area"] == pytest.approx(a0, rel=1e-9) and K.polygon_area2(q["boundary"]) > 0 and sorted(q["bulges"]) == pytest.approx(sorted([0, Q45, 0, -Q45]))
    ok(base.run("element.rotate", {"ids": [RF], "center": [0, 0], "angle": 30}))
    assert erow(base, RF)["area"] == pytest.approx(a0, rel=1e-6)
    for _ in range(4):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0
    ok(base.run("history.redo", {}))
    h1 = base.eng.state_hash()
    base.close()
    s2 = kit.Session(base.path, create=False)   # 저장 왕복: 다시 열면 같은 상태
    try:
        assert s2.eng.state_hash() == h1 and params(s2, RF)["thickness"] == 300.0
        # 경사 지붕으로 바꾸고 다시 평지붕으로
        ok(s2.run("roof.set", {"id": RF, "form": "footprint", "pitch": 20}))
        p = params(s2, RF)
        assert p["form"] == "footprint" and p["slopes"] == [20.0] * 4 and "bulges" not in p
        ok(s2.run("roof.set", {"id": RF, "form": "flat"}))
        p = params(s2, RF)
        assert p["form"] == "flat" and p["pitch"] == 0.0 and "slopes" not in p
        ok(s2.run("roof.delete", {"id": RF}))
        ok(s2.run("history.undo", {}))
        assert params(s2, RF)["form"] == "flat"
    finally:
        s2.close()


def test_3_지붕_유형_층_구성(base):
    ok(base.run("rooftype.create", {"id": F.RT1, "name": "평지붕 방수", "layers": [{"material": "우레탄 방수", "thickness": 5}, {"material": "보호 몰탈", "thickness": 50},
                                                                                {"material": "단열재", "thickness": 150}, {"material": "철근콘크리트", "thickness": 200}]}))
    ok(base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": L_SHAPE, "form": "flat", "type": F.RT1}))
    p = params(base, RF)
    assert p["thickness"] == 405.0 and p["rtype"] == F.RT1 and len(p["layers"]) == 4
    ok(base.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [2000, -1500], "p2": [2000, 7500], "scale": 20}))
    g = g_of(base)
    det = g.q(D.roof_detail_lines, VS, RF)
    assert sorted({round(x[2], 3) for x in det}) == [3300 - 205.0, 3300 - 55.0, 3300 - 5.0]   # 층 경계선(수평, 윗면에서 5·55·205)
    ok(base.run("rooftype.set", {"id": F.RT1, "layers": [{"material": "우레탄 방수", "thickness": 5}, {"material": "철근콘크리트", "thickness": 195}]}))
    assert params(base, RF)["thickness"] == 200.0


# ---------------------------------------------------------------- 4. 개구·붙이기·처마 부속
def test_4_평지붕_개구와_상단_붙이기(base):
    views(base)
    box = [[-500, -500], [6500, -500], [6500, 4500], [-500, 4500]]
    ok(base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": box, "form": "flat", "thickness": 200}))
    a0 = erow(base, RF)["area"]
    v = ok(base.run("opening.vertical", {"host": RF, "boundary": [[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]}))
    assert v["faces"] == [0] and v["area_m2"] == 1.0
    f = ok(base.run("opening.face", {"host": RF, "circle": {"center": [4000, 2000], "radius": 400}}))
    assert f["face"] == 0
    g = g_of(base)
    vg = g.q(D._roof_void_geom, RF)
    assert len(vg) == 2 and vg[1][1] == vg[1][2]                                   # 수평 면의 면 개구는 아랫면이 밀리지 않는다
    circ = 0.5 * 32 * 400 ** 2 * math.sin(2 * math.pi / 32)
    assert erow(base, RF)["area"] == pytest.approx(a0 - 1e6 - circ)
    sh = ok(base.run("opening.shaft", {"level": F.LV1, "boundary": [[5000, 3000], [5600, 3000], [5600, 3600], [5000, 3600]], "height": 4000, "also": ["roof"]}))
    assert sh["roofs"] == [RF]
    assert len(g_of(base).q(Q.roof_voids, RF)) == 3
    rc = g_of(base).q(D.roof_plan_parts, VC, RF)[0]
    assert sum(1 for x in rc if x[0] == "symbol") > 0                             # 반사 천장 평면: 구멍 X
    # 벽 상단 붙이기: 아랫면(3300 - 200)
    r = ok(base.run("wall.attach_top", {"id": F.W2, "targets": [RF]}))
    assert r["top_min"] == pytest.approx(3100.0) and r["top_max"] == pytest.approx(3100.0)
    # 커튼월 상단 붙이기
    ok(base.run("curtain.create", {"id": F.CW1, "level": F.LV1, "axis": [3000, 500, 3000, 3500], "height": 5000,
                                   "u_grid": {"rule": "spacing", "value": 1000}, "v_grid": {"rule": "spacing", "value": 1500}}))
    c = ok(base.run("curtain.attach", {"id": F.CW1, "targets": [RF]}))
    assert c["changed"] and c["top_max"] == pytest.approx(3100.0)
    same_as_full(base)


# ---------------------------------------------------------------- 5. IFC
def test_5_평지붕_IFC_와_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("rooftype.create", {"id": F.RT1, "name": "평지붕", "layers": [{"material": "방수", "thickness": 10}, {"material": "콘크리트", "thickness": 190}]}))
        ok(b.run("roof.create", {"id": RF, "level": F.LV2, "boundary": BAND, "form": "flat", "type": F.RT1, "overhang": 300, "bulges": [0, Q45, 0, -Q45]}))
        ok(b.run("roof.eaves.set", {"id": RF, "fascia": {"edges": "all", "material": "금속 널"}}))
        v = ok(b.run("opening.vertical", {"host": RF, "circle": {"center": [10000, 10000], "radius": 600}}))["id"]
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        rf = f.by_guid(next(e.GlobalId for e in f.by_type("IfcRoof") if e.Name == params_name(b)))
        assert rf.PredefinedType == "FLAT_ROOF"
        parts = [o for r in rf.IsDecomposedBy for o in r.RelatedObjects]
        slabs = [o for o in parts if o.is_a("IfcSlab")]
        assert len(slabs) == 1 and slabs[0].PredefinedType == "ROOF" and slabs[0].GlobalId == part_guid(RF, "face:0")
        assert sum(1 for o in parts if o.is_a("IfcCovering")) == 4 + 7 + 7               # 모든 현에 처마 널
        voids = {r.RelatedOpeningElement.GlobalId: r.RelatingBuildingElement for r in f.by_type("IfcRelVoidsElement")}
        assert voids[part_guid(RF, f"void:{v}:0")] == slabs[0]
        assert file_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "t3.ifc"))
        assert file_errors(f3) == [] and any(e.ShapeType == "FLAT_ROOF" for e in f3.by_type("IfcRoof"))   # IFC2X3 는 ShapeType
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        assert ok(b.run("element.get", {"id": RF}, project="imp.dobim"))["params"]["bulges"] == pytest.approx([0, Q45, 0, -Q45])
        ok(b.run("roof.set", {"id": RF, "base_offset": 200}))
        f = ifcopenshell.open(str(b.export("t4.ifc")))
        assert file_errors(f) == []
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()


def params_name(b):
    return ok(b.run("element.get", {"id": RF}))["params"]["name"]


# ---------------------------------------------------------------- 7. 처마 부속 변 번호 기본값
CW_L = [[0, 0], [0, 6000], [3000, 6000], [3000, 3000], [8000, 3000], [8000, 0]]   # 시계 방향 L(입력 변 0 = 서쪽 x=0)


def test_7_처마_변_번호_기본은_입력_순서(base):
    r = base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": CW_L, "form": "flat", "overhang": 400})
    assert r["ok"] and any("numbering=stored" in w for w in r["warnings"])
    w = base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": [0]}})           # 기본(입력 순서): 경고 없이 입력 변 0(서쪽)
    assert w["ok"] and not w["warnings"]
    e = ok(base.run("roof.eaves", {"id": RF}))["edges"]
    on = [x for x in e if "fascia" in x["on"]]
    assert len(on) == 1 and on[0]["input_edge"] == 0 and {tuple(on[0]["from"]), tuple(on[0]["to"])} == {(0.0, 0.0), (0.0, 6000.0)}
    stored0 = on[0]["edge"]
    assert params(base, RF)["eaves"]["fascia"]["edges"] == [stored0] and stored0 != 0   # 저장 값은 저장 순서 번호(파일 의미는 그대로)
    w = base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": [0]}, "numbering": "stored"})   # stored 명시 = 이전 기본 동작(저장 순서 + 경고)
    assert w["ok"] and any("numbering=input" in x for x in w["warnings"])
    assert params(base, RF)["eaves"]["fascia"]["edges"] == [0]
    # 반시계 외곽 지붕은 두 번호가 같다
    ok(base.run("roof.create", {"id": RF[:-1] + "8", "level": F.LV2, "boundary": L_SHAPE, "form": "flat"}))
    a = ok(base.run("roof.eaves.set", {"id": RF[:-1] + "8", "fascia": {"edges": [2]}}))
    assert a["changed"] and params(base, RF[:-1] + "8")["eaves"]["fascia"]["edges"] == [2]
