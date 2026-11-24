# SPDX-License-Identifier: MPL-2.0
"""W2 열네 번째 PR: 지붕·천장·슬래브 개구(ARC-19·ARC-21·ARC-34 잔여).

1. 경사 지붕 수직 개구(opening.vertical host=roof): 평면 경계를 수직으로 관통, 용마루를 걸치면 겹치는 면마다 뚫는다
2. 면 개구(opening.face): 지붕 면 하나에 면에 수직인 개구(아랫면 구멍이 t·(a, b)/(1 + a² + b²) 만큼 밀린다)
3. 천장판 개구(opening.vertical host=천장판): 판의 구멍으로 그리고(평면·반사 천장 평면·단면), IFC 는 IfcCovering 을 뚫는 IfcOpeningElement
4. 원호 경계(bulges·circle), 샤프트 also(roof·ceiling), opening.set·opening.move, 되돌리기
5. 수량: 슬래브·지붕·천장판 일람표 면적·체적에서 개구를 뺀다
6. IFC4·IFC2X3 검증 오류 0, IfcRelVoidsElement 호스트, 가져오기 왕복 해시·바이트
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
from dobim_core import hostvoid as HV
from dobim_core import queries as Q
from dobim_core import viewplans as VP
from dobim_core.ifc_model import part_guid

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

RF, CL = F.RF1, F.SO1
VR = "4" + "0" * 19 + "r1"     # 2F 지붕 평면(절단 높이가 용마루 위)
VS = "4" + "0" * 19 + "s1"     # x = 3000 단면(지붕 용마루를 가로지른다)
VC = "4" + "0" * 19 + "c1"     # 1F 반사 천장 평면
BOX = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
RIDGE_SQ = [[2500, 1500], [3500, 1500], [3500, 2500], [2500, 2500]]   # 용마루(y=2000)를 걸친 1m 사각
FACE_SQ = [[1000, 500], [2000, 500], [2000, 1200], [1000, 1200]]      # 남쪽 면(y<2000) 안
T30 = math.tan(math.radians(30))


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


def same_as_full(s, views=(VR, VS, VC)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)


def setup(s):
    ok(s.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": BOX, "thickness": 200}))
    ok(s.run("roof.create", {"id": RF, "level": F.LV2, "boundary": BOX, "form": "gable", "pitch": 30, "overhang": 600}))
    ok(s.run("ceiling.create", {"id": CL, "level": F.LV1, "boundary": BOX, "height": 2400, "grid": [600, 600]}))
    ok(s.run("view.create", {"id": VR, "name": "지붕", "type": "plan", "level": F.LV2, "cut_height": 2500}))
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [3000, -1500], "p2": [3000, 5500]}))
    ok(s.run("view.rcp", {"id": VC, "name": "1F 천장", "level": F.LV1}))
    return s


@pytest.fixture
def hs(base):
    return setup(base)


def erow(s, e):
    from dobim_core import schedules as SC
    return g_of(s).q(SC.element_row, e)


def test_지붕_수직_개구는_겹치는_면마다_뚫고_도면에_나온다(hs):
    g = g_of(hs)
    cuts0 = g.q(D.roof_cut_rects, VS, RF)
    edges0 = len(g.q(D.roof_edges3, RF))
    r = ok(hs.run("opening.vertical", {"host": RF, "boundary": RIDGE_SQ}))
    o = r["id"]
    assert r["faces"] == [0, 1] and r["area_m2"] == 1.0 and "openings" not in params(hs, RF)
    g = g_of(hs)
    assert [v[0] for v in g.q(Q.roof_voids, RF)] == [o]
    cuts = g.q(D.roof_cut_rects, VS, RF)
    # 단면(s = y + 1500): 용마루를 걸친 구멍(y 1500~2500)만큼 두 면의 절단 띠가 줄어든다(구멍 벽은 수직)
    assert len(cuts0) == len(cuts) == 2 and cuts0[0][1][0] == cuts0[1][0][0] == 3500.0
    assert (cuts[0][0][0], cuts[0][1][0], cuts[0][2][0]) == (900.0, 3000.0, 3000.0) and (cuts[1][0][0], cuts[1][3][0]) == (4000.0, 4000.0)
    assert cuts[0][2][1] == pytest.approx(cuts0[0][3][1] + 2100 * T30)      # 구멍 가장자리의 윗면 높이 = 처마 끝 + 물매 x 거리
    assert len(g.q(D.roof_edges3, RF)) > edges0                               # 입면·단면: 구멍 둘레·구멍 벽 모서리
    segs = g.q(D.roof_plan_parts, VR, RF)[0]
    hole = [x for x in segs if x[0] == "proj" and {(x[1], x[2]), (x[3], x[4])} <= {tuple(q) for q in map(tuple, RIDGE_SQ)}]
    assert len(hole) == 4                                                    # 평면: 구멍 둘레
    ridge = [x for x in segs if x[0] == "proj" and x[2] == x[4] == 2000.0]
    assert ridge and not any(2500 < (x[1] + x[3]) / 2 < 3500 and abs(x[3] - x[1]) < 1000 + 1e-6 and min(x[1], x[3]) >= 2500 - 1e-6 for x in ridge)   # 용마루가 구멍에서 끊긴다
    a0 = erow(hs, RF)["area"]
    ok(hs.run("history.undo", {}))
    a1 = erow(hs, RF)["area"]
    assert a1 - a0 == pytest.approx(1e6 / math.cos(math.radians(30)), rel=1e-6)   # 수량: 경사면 넓이 1m² / cos 30
    ok(hs.run("history.redo", {}))
    assert err(hs.run("opening.vertical", {"host": RF, "boundary": [[3000, 2000], [4000, 2000], [4000, 3000], [3000, 3000]]}))["code"] == "CONSTRAINT"   # 겹친다
    assert err(hs.run("opening.vertical", {"host": RF, "boundary": [[6200, 100], [7000, 100], [7000, 900], [6200, 900]]}))["code"] == "CONSTRAINT"   # 외곽 밖
    same_as_full(hs)


def test_면_개구는_면에_수직이고_아랫면이_밀린다(hs):
    r = ok(hs.run("opening.face", {"host": RF, "boundary": FACE_SQ}))
    o, i = r["id"], r["face"]
    g = g_of(hs)
    gm = g.q(Q.roof_gm, RF)
    pl = gm["planes"][i]
    assert pl[1] == pytest.approx(T30) and abs(pl[0]) < 1e-9                  # 남쪽 면은 y 쪽으로 오른다
    v = g.q(D._roof_void_geom, RF)[0]
    t = params(hs, RF)["thickness"]
    dy = t * T30 / (1 + T30 * T30)
    assert v[0] == "face" and v[2][0] == pytest.approx((1000.0, 500.0 + dy)) and v[1][0] == (1000.0, 500.0)
    vs1 = "4" + "0" * 19 + "s2"
    ok(hs.run("view.create", {"id": vs1, "name": "S2", "type": "section", "p1": [1500, -1500], "p2": [1500, 5500]}))
    g = g_of(hs)
    band = [c for c in g.q(D.roof_cut_rects, vs1, RF)]
    tops = sorted(round(c[3][0], 3) for c in band) + sorted(round(c[2][0], 3) for c in band)
    bots = sorted(round(c[0][0], 3) for c in band) + sorted(round(c[1][0], 3) for c in band)
    assert len(band) == 3 and tops != bots                                   # 면 개구의 구멍 벽은 수직이 아니다(윗면·아랫면 끝이 다르다)
    assert err(hs.run("opening.face", {"host": RF, "boundary": RIDGE_SQ}))["code"] == "CONSTRAINT"   # 두 면에 걸친다
    assert err(hs.run("opening.face", {"host": F.S1, "boundary": FACE_SQ}))["code"] == "CONSTRAINT"  # 면 개구 호스트는 지붕
    ok(hs.run("opening.set", {"id": o, "boundary": [[1000, 400], [2000, 400], [2000, 1100], [1000, 1100]]}))
    assert params(hs, o)["boundary"][0] == [1000.0, 400.0]
    ok(hs.run("opening.move", {"ids": [o], "dx": 500, "dy": 0}))
    assert params(hs, o)["boundary"][0] == [1500.0, 400.0]
    assert err(hs.run("opening.move", {"ids": [o], "dx": 0, "dy": 1500}))["code"] == "CONSTRAINT"   # 용마루를 넘는다
    same_as_full(hs, views=(VR, VS, vs1))


def test_천장판_개구는_판의_구멍이고_반사_천장_평면에_나온다(hs):
    g = g_of(hs)
    n0 = len(g.q(D.solid_info, CL)[1])
    grid0 = [x for x in g.q(VP.ceiling_grid_lines, CL) if x[0] == "hatch"]
    a0 = erow(hs, CL)
    o = ok(hs.run("opening.vertical", {"host": CL, "boundary": [[2700, 1700], [3300, 1700], [3300, 2300], [2700, 2300]]}))["id"]
    g = g_of(hs)
    assert [v[0] for v in g.q(Q.solid_voids, CL)] == [o]
    assert len(g.q(D.solid_info, CL)[1]) > n0                               # 판 메시에 구멍
    pl = g.q(D.plan_opening_lines, VC)
    assert len(pl) == 4                                                      # 반사 천장 평면: 개구 경계
    grid = [x for x in g.q(VP.ceiling_grid_lines, CL) if x[0] == "hatch"]
    assert len(grid) > len(grid0)                                            # 격자가 구멍에서 끊긴다
    a1 = erow(hs, CL)
    assert a0["area"] - a1["area"] == pytest.approx(360000.0) and a0["volume"] - a1["volume"] == pytest.approx(360000.0 * 15)
    assert "openings" not in params(hs, CL)
    assert err(hs.run("opening.vertical", {"host": CL, "boundary": [[5800, 100], [6500, 100], [6500, 900], [5800, 900]]}))["code"] == "CONSTRAINT"
    ok(hs.run("solid.create", {"id": "f" + "0" * 19 + "b1", "level": F.LV1, "category": "buttress", "form": "prism",
                               "polygon": [[0, -900], [600, -900], [600, -100], [0, -100]], "z0": 0, "z1": 2400}))
    assert err(hs.run("opening.vertical", {"host": "f" + "0" * 19 + "b1", "boundary": [[100, -800], [300, -800], [300, -600], [100, -600]]}))["code"] == "CONSTRAINT"   # 천장판만
    same_as_full(hs)


def test_원호_경계와_슬래브_일람표(hs):
    a0 = erow(hs, F.S1)
    r = ok(hs.run("opening.vertical", {"host": F.S1, "circle": {"center": [3000, 2000], "radius": 500}}))
    p = params(hs, r["id"])
    assert len(p["boundary"]) == 4 and p["bulges"] == pytest.approx([math.tan(math.pi / 8)] * 4)
    area32 = 0.5 * 32 * 500 ** 2 * math.sin(2 * math.pi / 32)                # 한 바퀴 32 현으로 펼친다
    assert r["area_m2"] == pytest.approx(area32 / 1e6, abs=1e-6)
    a1 = erow(hs, F.S1)
    assert a0["area"] - a1["area"] == pytest.approx(area32, rel=1e-9) and a1["openings"] == a0["openings"] + 1
    assert len(g_of(hs).q(D.slab_info, F.S1)[3][0]) == 32
    # 볼록도 경계: 반원 끝 사각(D 모양), 시계 방향으로 줘도 반시계로 저장한다
    d = ok(hs.run("opening.vertical", {"host": F.S1, "boundary": [[500, 500], [500, 1500], [1000, 1500], [1000, 500]], "bulges": [0, 0, -1, 0]}))
    q = params(hs, d["id"])
    assert q["boundary"][0] == [1000.0, 500.0] or K_ccw(q["boundary"])
    assert d["area_m2"] == pytest.approx((500 * 1000 + 0.5 * 16 * 500 ** 2 * math.sin(2 * math.pi / 32)) / 1e6, abs=1e-6)
    assert err(hs.run("opening.vertical", {"host": F.S1, "boundary": [[500, 2500], [1500, 2500], [1500, 3500]], "bulges": [0, 1]}))["code"] == "CONSTRAINT"   # 볼록도 수가 꼭짓점 수와 다르다
    ok(hs.run("opening.set", {"id": d["id"], "bulges": []}))                  # 볼록도를 떼면 직선 경계
    assert "bulges" not in params(hs, d["id"])
    same_as_full(hs)


def K_ccw(b):
    from dobim_core import kinds as K
    return K.polygon_area2(b) > 0


def test_샤프트_also_는_지붕과_천장판도_관통한다(hs):
    sq = [[400, 400], [900, 400], [900, 900], [400, 900]]
    r0 = ok(hs.run("opening.shaft", {"level": F.LV1, "boundary": sq, "height": 6000}))
    g = g_of(hs)
    assert r0["slabs"] == [F.S1] and not g.q(Q.roof_voids, RF) and not g.q(Q.solid_voids, CL)   # also 없으면 이전처럼 슬래브만
    ok(hs.run("opening.set", {"id": r0["id"], "also": ["roof", "ceiling"]}))
    g = g_of(hs)
    assert [v[0] for v in g.q(Q.roof_voids, RF)] == [r0["id"]] and [v[0] for v in g.q(Q.solid_voids, CL)] == [r0["id"]]
    r = ok(hs.run("opening.set", {"id": r0["id"], "height": 2000}))       # 천장(2400)·지붕에 못 미친다
    assert r["roofs"] == [] and r["ceilings"] == []
    g = g_of(hs)
    assert not g.q(Q.roof_voids, RF) and not g.q(Q.solid_voids, CL)
    assert err(hs.run("opening.set", {"id": r0["id"], "offset": 100}))["code"] == "CONSTRAINT"   # 샤프트에 벽 개구 필드
    w = ok(hs.run("opening.wall", {"host": F.W2, "offset": 2800, "sill": 900, "width": 800, "height": 1200}))["id"]
    ok(hs.run("opening.set", {"id": w, "offset": 2600, "width": 900}))
    assert params(hs, w)["offset"] == 2600.0 and params(hs, w)["width"] == 900.0
    assert err(hs.run("opening.move", {"ids": [w], "dx": 100}))["code"] == "CONSTRAINT"
    ok(hs.run("history.undo", {}))
    assert params(hs, w)["offset"] == 2800.0
    same_as_full(hs)


def test_지붕_천장_개구의_IFC_와_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("roof.create", {"id": RF, "level": F.LV2, "boundary": BOX, "form": "gable", "pitch": 30, "overhang": 600}))
        ok(b.run("ceiling.create", {"id": CL, "level": F.LV1, "boundary": BOX, "height": 2400}))
        ok(b.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": BOX, "thickness": 200}))
        v = ok(b.run("opening.vertical", {"host": RF, "boundary": RIDGE_SQ, "name": "천창"}))["id"]
        fo = ok(b.run("opening.face", {"host": RF, "circle": {"center": [1500, 800], "radius": 300}}))["id"]
        c = ok(b.run("opening.vertical", {"host": CL, "boundary": [[2700, 1700], [3300, 1700], [3300, 2300], [2700, 2300]]}))["id"]
        sh = ok(b.run("opening.shaft", {"level": F.LV1, "boundary": [[400, 400], [900, 400], [900, 900], [400, 900]], "height": 6000, "also": ["ceiling", "roof"]}))["id"]
        ok(b.run("pset.set", {"id": v, "pset": "Pset_OpeningElementCommon", "prop": "Purpose", "value": "천창"}))
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        voids = {r.RelatedOpeningElement.GlobalId: r.RelatingBuildingElement for r in f.by_type("IfcRelVoidsElement")}
        rf = f.by_guid(next(iter(voids[part_guid(RF, f"void:{v}:0")].Decomposes)).RelatingObject.GlobalId)
        assert rf.is_a("IfcRoof")
        for lb in (f"void:{v}:0", f"void:{v}:1", f"void:{fo}:0", f"void:{sh}:0"):
            assert voids[part_guid(RF, lb)].is_a("IfcSlab") and voids[part_guid(RF, lb)].PredefinedType == "ROOF"
        assert voids[part_guid(CL, f"void:{c}")].is_a("IfcCovering") and voids[part_guid(CL, f"void:{sh}")].is_a("IfcCovering")
        assert voids[part_guid(F.S1, f"void:{sh}")].is_a("IfcSlab")
        op = f.by_guid(part_guid(RF, f"void:{v}:1"))
        assert op.Name.endswith(f"-void:{v}:1") and any(d.RelatingPropertyDefinition.Name == "Pset_OpeningElementCommon" for d in op.IsDefinedBy)
        assert file_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"]
        assert file_errors(ifcopenshell.open(str(tmp_path / "t3.ifc"))) == []
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: x for k, x in b.hashes("imp.dobim").items() if k != "rev"} == {k: x for k, x in h.items() if k != "rev"}
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        ok(b.run("opening.delete", {"ids": [v, sh]}))                            # 지우면 면 부속의 개구도 빠진다
        f = ifcopenshell.open(str(b.export("t4.ifc")))
        gone = {part_guid(RF, f"void:{v}:0"), part_guid(RF, f"void:{v}:1"), part_guid(CL, f"void:{sh}"), part_guid(RF, f"void:{sh}:0")}
        assert not any(o.GlobalId in gone for o in f.by_type("IfcOpeningElement")) and file_errors(f) == []
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
        ok(b.run("roof.set", {"id": RF, "pitch": 20}))                            # 지붕을 고쳐도 개구가 따라간다(면은 다시 찾는다)
        f = ifcopenshell.open(str(b.export("t5.ifc")))
        assert part_guid(RF, f"void:{v}:1") in {o.GlobalId for o in f.by_type("IfcOpeningElement")} and file_errors(f) == []
    finally:
        b.close()
