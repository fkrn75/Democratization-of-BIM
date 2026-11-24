# SPDX-License-Identifier: MPL-2.0
"""W2 열다섯 번째 PR(구조 보 1부: STR-02 구조 프레임 보, STR-03 보-기둥 접합 단순화, STR-07 단면 라이브러리).

1. 보 유형(beamtype): 사각(폭·춤)·H형강(폭·춤·웨브·플랜지), KS H형강 표준 단면 preset. 만들기·고치기(보가 따라간다)·복제·지우기(detach)·목록
2. 보(beam): 두 점·레벨·기준선 높이(offset)·z 맞춤(anchor)·단면 회전(roll). 만들기·고치기·옮기기·지우기·복사·대칭, 실행 취소·다시 실행
3. 기둥 면 자르기: 끝점이 기둥(IfcColumn 패밀리) 단면 안이고 높이가 겹치면 기둥 면까지 줄인다(기둥을 옮기면 따라간다). cutback false 면 자르지 않는다
4. 통심선 따라 일괄 배치(beam.grid): 교점 사이 칸마다, 기본은 두 교점에 기둥이 있는 칸만. 다시 실행하면 같은 자리는 건너뛴다
5. 도면: 평면(절단 높이 위 보는 숨은선 파선, 아래·걸친 보는 투영선·절단선과 채움), 단면(절단선 + 절단 채움), 입면(투영선, 가림)
6. IFC: IfcBeam(몸체 = IfcRectangleProfileDef·IfcIShapeProfileDef 돌출) + IfcBeamType + IfcRelDefinesByType. IFC4·IFC2X3 검증 오류 0, 왕복 해시·바이트
7. 일람표(beams 범주: 길이·체적)·재료 물량·model.stats
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import sys

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import beams as BM
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import queries as Q

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V, V2, VE = F.V1, F.V2, "4" + "0" * 19 + "e9"
BT, BH, BT2 = ("9" + "0" * 19 + x for x in ("b1", "b2", "b3"))       # 보 유형
BA, BB, BC = ("9" + "0" * 19 + x for x in ("m1", "m2", "m3"))       # 보
C1, C2 = ("0" * 20 + x for x in ("c1", "c2"))                      # 기둥
G1, G2, G3, G4 = ("g" + "0" * 19 + x for x in ("x1", "x2", "y1", "y2"))   # 통심선
IBT, IBM, IBH = ("3" + "0" * 19 + x for x in ("b7", "m7", "m8"))   # IFC 왕복용(첫 글자가 유효한 GlobalId)


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


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [x["message"][:160] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def same_as_full(s, views=(V,)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)
        assert g.q(D.view_fills, v) == full.q(D.view_fills, v)


def column(s, cid, pos, w=500):
    ok(s.run("family.place", {"id": cid, "family": "column.section", "type_values": {"section": "rect", "width": w, "depth": w},
                              "values": {"height": 3300}, "level": F.LV1, "position": list(pos)}))


@pytest.fixture
def bs(base):
    ok(base.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    return base


# ---------------------------------------------------------------- 1. 단면·형상(순수)
def test_1_단면과_형상():
    assert BM.section_problem({"shape": "rect", "width": 400.0, "depth": 700.0}) is None
    assert BM.section_problem({"shape": "h", "width": 200.0, "depth": 400.0, "web": 8.0, "flange": 13.0}) is None
    assert BM.section_problem({"shape": "h", "width": 200.0, "depth": 400.0, "web": 200.0, "flange": 13.0})        # 웨브 >= 폭
    assert BM.section_problem({"shape": "rect", "width": 400.0, "depth": 700.0, "web": 8.0})                        # 키가 다르다
    sc = BM.preset_section("H-400x200x8x13")
    assert sc == {"shape": "h", "width": 200.0, "depth": 400.0, "web": 8.0, "flange": 13.0} and BM.section_label(sc) == "H-400x200x8x13"
    assert BM.section_area(sc) == pytest.approx(2 * 200 * 13 + 8 * (400 - 26))
    p = {"name": "b", "start": [0.0, 0.0], "end": [6000.0, 0.0], "offset": 0.0, "anchor": "top", "section": sc}
    assert K.row_params_error("beam", p) is None
    assert BM.b_range(p) == (-400.0, 0.0) and BM.a_range(p) == (-100.0, 100.0)
    assert BM.b_range(dict(p, anchor="center")) == (-200.0, 200.0) and BM.b_range(dict(p, anchor="bottom")) == (0.0, 400.0)
    a0, a1 = BM.a_range(dict(p, roll=90.0))                                          # 90도 돌리면 춤(400)이 평면 폭이 된다(윗면 기준선 둘레)
    assert (a0, a1) == (pytest.approx(0.0, abs=1e-9), pytest.approx(400.0))
    verts, faces = BM.mesh(p, 3300.0, (0.0, 0.0))
    assert len(verts) == 24 and len(faces) == 14                                    # H 윤곽 12점 x 두 끝, 면 = 두 끝 + 옆 12
    assert min(v[2] for v in verts) == pytest.approx(2900.0) and max(v[2] for v in verts) == pytest.approx(3300.0)
    assert len(BM.pieces(p, 0.0, (0.0, 0.0))) == 3                                  # 아래 플랜지·웨브·위 플랜지
    assert K.row_params_error("beam", dict(p, end=[5.0, 0.0]))                       # 너무 짧다
    assert K.row_params_error("beam", dict(p, cutback=True))                         # cutback 은 false 만 적는다
    assert K.row_params_error("beam", dict(p, roll=0.0))                             # roll 0 은 키 없음
    # 기둥 면 자르기(순수): 시작점이 500 각 기둥 가운데, 끝점은 밖
    col = (C1, 0.0, 3300.0, (-250.0, -250.0, 250.0, 250.0), ((-250.0, -250.0), (250.0, -250.0), (250.0, 250.0), (-250.0, 250.0)))
    assert BM.trims(p, (2900.0, 3300.0), (col,)) == (250.0, 0.0, C1, None)
    assert BM.trims(p, (3400.0, 3800.0), (col,)) == (0.0, 0.0, None, None)          # 높이가 안 겹친다
    assert BM.trims(dict(p, cutback=False), (2900.0, 3300.0), (col,)) == (0.0, 0.0, None, None)


# ---------------------------------------------------------------- 2. 보 유형·보 명령
def test_2_보_유형과_보_명령(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    assert err(bs.run("beamtype.create", {"name": "G1"}))["code"] == "CONSTRAINT"                       # 같은 이름
    assert err(bs.run("beamtype.create", {"name": "x", "preset": "H-400x200x8x13", "depth": 500}))["code"] == "CONSTRAINT"
    assert err(bs.run("beamtype.create", {"name": "x", "shape": "h", "width": 200, "depth": 400}))["code"] == "CONSTRAINT"   # web·flange 없음
    r = ok(bs.run("beamtype.create", {"id": BH, "name": "SG1", "preset": "H-400x200x8x13"}))
    assert r["label"] == "H-400x200x8x13"
    ok(bs.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [0, 0], "end": [6000, 0]}))
    ok(bs.run("beam.create", {"id": BB, "level": F.LV2, "type": BH, "start": [0, 4000], "end": [6000, 4000], "offset": -100}))
    p = ok(bs.run("element.get", {"id": BA}))["params"]
    assert p["btype"] == BT and p["section"] == {"shape": "rect", "width": 400.0, "depth": 700.0} and p["anchor"] == "top"
    assert err(bs.run("beam.create", {"level": F.LV2, "type": BT, "start": [0, 0], "end": [5, 0]}))["code"] == "CONSTRAINT"
    assert err(bs.run("beam.create", {"level": F.LV2, "type": "9" + "0" * 21, "start": [0, 0], "end": [500, 0]}))["code"] == "NOT_FOUND"
    v = ok(bs.run("model.validate", {}))
    assert not [m for m in v["violations"] if "IfcGloballyUniqueId" not in m], v                       # of_type 관계·params.btype 이 맞는다
    r = ok(bs.run("beamtype.set", {"id": BT, "depth": 800}))                                            # 유형 편집 -> 보 반영
    assert r["beams"] == 1 and ok(bs.run("element.get", {"id": BA}))["params"]["section"]["depth"] == 800
    ok(bs.run("beam.set", {"id": BA, "offset": -50, "anchor": "center", "roll": 15, "name": "G1-1"}))
    p = ok(bs.run("element.get", {"id": BA}))["params"]
    assert p["offset"] == -50 and p["anchor"] == "center" and p["roll"] == 15 and p["name"] == "G1-1"
    ok(bs.run("beam.set", {"id": BA, "roll": 0, "anchor": "top", "offset": 0}))
    assert "roll" not in ok(bs.run("element.get", {"id": BA}))["params"]
    ok(bs.run("beam.move", {"id": BA, "dx": 0, "dy": 500, "dz": -100}))
    p = ok(bs.run("element.get", {"id": BA}))["params"]
    assert p["start"] == [0, 500] and p["end"] == [6000, 500] and p["offset"] == -100
    ok(bs.run("history.undo", {}))
    assert ok(bs.run("element.get", {"id": BA}))["params"]["start"] == [0, 0]
    ok(bs.run("history.redo", {}))
    assert ok(bs.run("element.get", {"id": BA}))["params"]["start"] == [0, 500]
    lst = ok(bs.run("beamtype.list", {}))
    assert [x["name"] for x in lst["items"]] == ["G1", "SG1"] and lst["items"][0]["beams"] == 1 and "H-400x200x8x13" in lst["presets"]
    bl = ok(bs.run("beam.list", {"level": F.LV2}))
    assert [x["id"] for x in bl["items"]] == sorted([BA, BB]) and bl["items"][0]["length"] == 6000
    ok(bs.run("beam.type.set", {"ids": [BA], "type": BH}))
    assert ok(bs.run("element.get", {"id": BA}))["params"]["section"]["shape"] == "h"
    d = ok(bs.run("beamtype.duplicate", {"id": BH, "name": "SG2", "new_id": BT2}))
    assert d["id"] == BT2
    assert err(bs.run("element.delete", {"id": BH}))["code"] == "CONSTRAINT"
    r = ok(bs.run("beamtype.delete", {"id": BH, "detach": True}))
    assert r["detached"] == 2
    p = ok(bs.run("element.get", {"id": BB}))["params"]
    assert "btype" not in p and p["section"]["shape"] == "h"
    ok(bs.run("history.undo", {}))
    assert ok(bs.run("element.get", {"id": BB}))["params"]["btype"] == BH
    # 복사·대칭(편집 공통 명령)
    c = ok(bs.run("element.copy", {"ids": [BB], "dx": 0, "dy": 1000}))
    assert c["count"] == 1
    nb = c["ids"][0]
    assert ok(bs.run("element.get", {"id": nb}))["params"]["start"] == [0, 5000]
    assert ok(bs.run("element.get", {"id": nb}))["relations"]["of_type"] == [BH]
    ok(bs.run("beam.set", {"id": nb, "roll": 30}))
    ok(bs.run("element.mirror", {"ids": [nb], "axis": [0, 0, 1000, 0]}))
    assert ok(bs.run("element.get", {"id": nb}))["params"]["roll"] == -30
    ok(bs.run("element.delete", {"id": nb}))
    assert err(bs.run("element.get", {"id": nb}))["code"] == "NOT_FOUND"
    st = g_of(bs).q(Q.model_stats)
    assert st["beam"] == 2 and st["beamtype"] == 3
    same_as_full(bs)


# ---------------------------------------------------------------- 3. 기둥 면 자르기
def test_3_기둥_면_자르기(bs):
    column(bs, C1, (0, 0))
    column(bs, C2, (6000, 0), 600)
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    ok(bs.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [0, 0], "end": [6000, 0]}))
    g = g_of(bs)
    assert g.q(Q.beam_cut, BA) == (250.0, 300.0, C1, C2)
    ln, cl, a, vol = g.q(Q.beam_measure, BA)
    assert ln == 6000 and cl == pytest.approx(5450) and vol == pytest.approx(400 * 700 * 5450)
    spec = g.q(Q.ifc_spec, BA)
    assert spec["origin"] == (250.0, 0.0, 0.0) and spec["length"] == pytest.approx(5450)
    item = ok(bs.run("beam.list", {}))["items"][0]
    assert item["start_cut"] == {"column": C1, "length": 250.0} and item["end_cut"] == {"column": C2, "length": 300.0}
    ok(bs.run("family.set", {"id": C2, "position": [6100, 0]}))                     # 기둥을 옮기면 자르는 길이가 따라간다
    assert g_of(bs).q(Q.beam_cut, BA)[1] == pytest.approx(200.0)
    ok(bs.run("beam.set", {"id": BA, "cutback": False}))
    assert g_of(bs).q(Q.beam_cut, BA) == (0.0, 0.0, None, None)
    ok(bs.run("beam.set", {"id": BA, "cutback": True, "offset": 1000}))             # 보가 기둥 위(3300 + 1000 - 700 = 3600 > 3300): 높이가 안 겹친다
    assert g_of(bs).q(Q.beam_cut, BA) == (0.0, 0.0, None, None)
    ok(bs.run("beam.set", {"id": BA, "offset": 0}))
    assert g_of(bs).q(Q.beam_cut, BA)[2] == C1
    t = ok(bs.run("schedule.table", {"category": "beams", "fields": ["name", "type", "length", "volume", "width", "depth"], "units": "mm"}))
    assert t["items"][0][1:] == ["G1", pytest.approx(5550.0), pytest.approx(400 * 700 * 5550.0), 400.0, 700.0]   # 끝 기둥을 6100 으로 옮겨 250 + 200 을 줄였다
    to = ok(bs.run("schedule.takeoff", {"categories": ["beams"], "by_level": False}))
    assert to["items"][0][3:] == [1, None, pytest.approx(400 * 700 * 5550.0 / 1e9)]
    same_as_full(bs)


# ---------------------------------------------------------------- 4. 통심선 따라 일괄 배치
def test_4_통심선_따라_일괄_배치(bs):
    for gid, nm, ln in ((G1, "1", [0, -1000, 0, 7000]), (G2, "2", [6000, -1000, 6000, 7000]), (G3, "A", [-1000, 0, 7000, 0]), (G4, "B", [-1000, 6000, 7000, 6000])):
        ok(bs.run("grid.create", {"id": gid, "name": nm, "line": ln}))
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    r = bs.run("beam.grid", {"level": F.LV2, "type": BT})                        # 기둥이 없으면 놓을 칸이 없다
    assert r["ok"] and r["result"]["count"] == 0 and r["warnings"]
    for cid, pos in ((C1, (0, 0)), (C2, (6000, 0))):
        column(bs, cid, pos)
    r = ok(bs.run("beam.grid", {"level": F.LV2, "type": BT}))
    assert r["count"] == 1                                                       # 두 끝에 기둥이 있는 칸: A 통심선의 1-2 칸
    p = ok(bs.run("element.get", {"id": r["ids"][0]}))["params"]
    assert sorted([p["start"], p["end"]]) == [[0, 0], [6000, 0]]
    r = ok(bs.run("beam.grid", {"level": F.LV2, "type": BT, "between": "intersections"}))
    assert r["count"] == 3                                                       # 나머지 세 칸(같은 자리는 건너뛴다)
    assert ok(bs.run("beam.grid", {"level": F.LV2, "type": BT, "between": "intersections"}))["count"] == 0
    assert g_of(bs).q(Q.model_stats)["beam"] == 4
    ok(bs.run("history.undo", {}))
    assert g_of(bs).q(Q.model_stats)["beam"] == 1


# ---------------------------------------------------------------- 5. 도면
def test_5_도면(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    ok(bs.run("beamtype.create", {"id": BH, "name": "SG1", "preset": "H-400x200x8x13"}))
    ok(bs.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [0, -1000], "end": [6000, -1000]}))       # 1층 평면 절단 높이 위
    ok(bs.run("beam.create", {"id": BB, "level": F.LV1, "type": BH, "start": [0, -2000], "end": [6000, -2000], "offset": 1400}))   # 절단 높이에 걸친다
    g = g_of(bs)
    hid = g.q(D.beam_lines, V, BA)
    assert hid and all(x[0] == "proj" for x in hid)
    lens = sorted({round(((x[3] - x[1]) ** 2 + (x[4] - x[2]) ** 2) ** 0.5, 3) for x in hid})
    assert lens[-1] == pytest.approx(D.HIDDEN_DASH * 100)                         # 파선 마디(용지 2mm x 축척 100)
    cut = g.q(D.beam_lines, V, BB)
    assert any(x[0] == "cut" for x in cut)
    fills = g.q(D.beam_fills, V, BB)
    assert len(fills) == 1                                                       # 절단 높이 1200 은 웨브(1013~1387)를 자른다
    xs = sorted({q[1] for q in fills[0]})
    assert xs == pytest.approx([-2004.0, -1996.0])
    assert g.q(D.beam_fills, V, BA) == ()
    vl = g.q(D.view_lines, V)
    assert any(x[0] == "cut" and abs(x[2] + 2004.0) < 1e-6 for x in vl)
    # 단면: 보를 가로지르는 절단 -> 절단선 + H 형 채움 3 조각
    ok(bs.run("view.create", {"id": V2, "name": "S", "type": "section", "p1": [3000, -5000], "p2": [3000, 2000]}))
    g = g_of(bs)
    f = g.q(D.beam_fills, V2, BB)
    assert len(f) == 3
    assert sum(abs(D._area2(list(pl))) / 2.0 for pl in f) == pytest.approx(BM.section_area(BM.preset_section("H-400x200x8x13")))
    assert len(g.q(D.beam_fills, V2, BA)) == 1 and any(x[0] == "cut" for x in g.q(D.beam_lines, V2, BA))
    assert g.q(D.view_fills, V2)
    # 입면: 투영선, 보의 가림 조각이 다른 요소(벽 포함)의 가림 목록에 든다
    ok(bs.run("view.create", {"id": VE, "name": "남", "type": "elevation", "dir": "N"}))
    g = g_of(bs)
    el = g.q(D.beam_lines, VE, BA)
    assert el and all(x[0] == "proj" for x in el)
    vp = g.q(D._beam_vpieces, VE, BB)
    assert len(vp) == 3                                                           # 가림 조각(H 3 조각)
    assert all(pc in g.q(D._occ_index, VE)[0] for pc in vp) and len(g.q(D._other_occ_boxes, VE)) >= 4
    # 증분 = 전체 계산
    same_as_full(bs, (V, V2, VE))
    ok(bs.run("beam.set", {"id": BB, "offset": 1300}))
    same_as_full(bs, (V, V2, VE))
    # GUI 장면도 보를 요소로 담는다
    from dobim_core import scene as SC
    sc = g_of(bs).q(SC.view_scene, V2)
    row = next(t for t in sc if t[0] == "beam" and t[1] == BB)
    assert row[2] and len(row[3]) == 3                                            # 선분과 H 형 절단 채움 3 조각


def test_5_입면_보가_벽_선을_가린다(bs):
    VS = "4" + "0" * 19 + "e8"
    ok(bs.run("view.create", {"id": VS, "name": "남측", "type": "elevation", "dir": "S"}))          # 남쪽에서 북쪽을 본다
    top = [x for x in g_of(bs).q(D.vwall_lines, VS, F.W1) if abs(x[2] - 3000) < 1e-6 and abs(x[4] - 3000) < 1e-6]
    assert top and max(abs(x[3] - x[1]) for x in top) > 5000                                         # 보가 없으면 벽 윗선(z 3000)이 보인다
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    ok(bs.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [-500, -1000], "end": [6500, -1000]}))   # 벽 앞(남쪽), z 2600~3300
    top = [x for x in g_of(bs).q(D.vwall_lines, VS, F.W1) if abs(x[2] - 3000) < 1e-6 and abs(x[4] - 3000) < 1e-6]
    assert not top                                                                                     # 보 뒤로 숨는다
    ok(bs.run("beam.set", {"id": BA, "roll": 45}))                                                    # 회전한 보는 가리지 않는다(한계)
    assert g_of(bs).q(D._beam_vpieces, VS, BA) == ()
    same_as_full(bs, (V, VS))


def test_5_평면_범위와_다른_층(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    ok(bs.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [0, -1000], "end": [6000, -1000]}))
    ok(bs.run("view.create", {"id": V2, "name": "2F", "type": "plan", "level": F.LV2, "cut_height": 1200}))
    g = g_of(bs)
    assert g.q(D.scope_beams, V) == (BA,)                                          # 1층 평면: 천장 밑 보(숨은선)
    assert g.q(D.scope_beams, V2) == ()                                            # 2층 평면: 바닥 밑 그 층 보는 바닥에 가린다
    ok(bs.run("beam.set", {"id": BA, "level": F.LV1, "offset": 1000}))             # 1층 레벨로(윗면 1000: 절단 높이 아래)
    g = g_of(bs)
    assert g.q(Q.beam_level, BA) == F.LV1 and g.q(D.scope_beams, V) == (BA,)
    assert all(x[0] == "proj" for x in g.q(D.beam_lines, V, BA)) and g.q(D.beam_fills, V, BA) == ()
    ok(bs.run("material.set", {"id": BA, "name": "콘크리트"}))
    t = ok(bs.run("schedule.table", {"category": "beams", "fields": ["name", "material", "level"]}))
    assert t["items"][0][1:] == ["콘크리트", "1F"]
    same_as_full(bs, (V, V2))


def test_5_저장하고_다시_열기(tmp_path):
    s = kit.Session(tmp_path / "b.dobim")
    try:
        for name, args in F.SCRIPT:
            ok(s.run(name, args))
        ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
        column(s, C1, (0, 0))
        ok(s.run("beamtype.create", {"id": BH, "name": "SG1", "preset": "H-588x300x12x20"}))
        ok(s.run("beam.create", {"id": BA, "level": F.LV2, "type": BH, "start": [0, 0], "end": [8000, 0], "roll": 10}))
        before = (ok(s.run("element.get", {"id": BA}))["params"], g_of(s).q(D.view_lines, V), g_of(s).q(Q.ifc_spec, BA))
    finally:
        s.close()
    s = kit.Session(tmp_path / "b.dobim", create=False)
    try:
        after = (ok(s.run("element.get", {"id": BA}))["params"], g_of(s).q(D.view_lines, V), g_of(s).q(Q.ifc_spec, BA))
        assert after == before and before[2]["origin"][0] == 250.0
    finally:
        s.close()


def test_5_보가_없으면_도면은_그대로(bs):
    g = g_of(bs)
    assert g.q(D.scope_beams, V) == () and g.q(Q.beam_ids) == ()


# ---------------------------------------------------------------- 6. IFC
def test_6_IFC_검증과_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("family.place", {"id": "1" + "0" * 19 + "c7", "family": "column.section", "type_values": {"section": "rect", "width": 500, "depth": 500},
                                  "values": {"height": 3300}, "level": F.LV1, "position": [0, -3000]}))
        ok(b.run("beamtype.create", {"id": IBT, "name": "SG1", "preset": "H-400x200x8x13"}))
        ok(b.run("beam.create", {"id": IBM, "level": F.LV2, "type": IBT, "start": [0, -3000], "end": [6000, -3000], "roll": 90}))
        ok(b.run("beamtype.create", {"name": "G1", "width": 400, "depth": 700}))
        lst = ok(b.run("beamtype.list", {}))
        tid = next(x["id"] for x in lst["items"] if x["name"] == "G1")
        ok(b.run("beam.create", {"id": IBH, "level": F.LV2, "type": tid, "start": [0, -5000], "end": [4000, -5000], "anchor": "center", "offset": -400}))
        h = b.hashes()
        out = b.export("b.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        e = f.by_guid(IBM)
        assert e.is_a("IfcBeam") and e.PredefinedType == "BEAM"
        body = e.Representation.Representations[0]
        assert body.RepresentationType == "SweptSolid"
        sw = body.Items[0]
        assert sw.is_a("IfcExtrudedAreaSolid") and sw.SweptArea.is_a("IfcIShapeProfileDef") and sw.Depth == pytest.approx(6000 - 250)
        assert (sw.SweptArea.OverallWidth, sw.SweptArea.OverallDepth, sw.SweptArea.WebThickness, sw.SweptArea.FlangeThickness) == (200, 400, 8, 13)
        r2 = f.by_guid(IBH).Representation.Representations[0].Items[0]
        assert r2.SweptArea.is_a("IfcRectangleProfileDef") and (r2.SweptArea.XDim, r2.SweptArea.YDim) == (400, 700)
        t = [r.RelatingType for r in e.IsTypedBy]
        assert t and t[0].is_a("IfcBeamType") and t[0].Name == "SG1" and t[0].PredefinedType == "BEAM"
        assert len(f.by_type("IfcBeamType")) == 2
        assert b.destructive("ifc.export", {"path": "b3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "b3.ifc"))
        assert file_errors(f3) == [] and len(f3.by_type("IfcBeamType")) == 2 and len(f3.by_type("IfcIShapeProfileDef")) == 1
        r = b.import_ifc("b.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("b2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        ok(b.run("beamtype.set", {"id": IBT, "preset": "H-300x150x6.5x9"}))
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()
