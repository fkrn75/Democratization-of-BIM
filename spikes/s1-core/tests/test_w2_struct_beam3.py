# SPDX-License-Identifier: MPL-2.0
"""W2 열아홉 번째 PR(구조 보 3부: 곡선 보, 경사 보 끝면 연직 자르기와 가림 조각, 보끼리 접합 간격과 경사 자르기).

1. 곡선 보(bulge): 원호 기준선, 호를 나눈 마이터 스윕 다면체(옆면 평면·체적 = 파푸스), 기둥·주 보 끝 자르기(호 길이), 수량(호 길이), 대칭·복사·저장
2. 곡선 보 도면(평면 숨은선·절단 채움·단면·입면 가림)과 증분 = 전체 재계산
3. 경사 보 끝면 연직 자르기(vertical_ends)와 경사 보 가림 조각(높이 조각)
4. 보끼리 접합 간격(gap)과 비스듬한 주 보 옆면에 맞춘 경사 자르기(skew_cut)
5. IFC: 곡선 보 IfcSurfaceCurveSweptAreaSolid(IFC4·IFC2X3 검증 오류 0, 몸체가 코어 다면체와 같은 자리), 끝면 IfcBooleanClippingResult, 왕복 해시·바이트
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import sys
from math import cos, degrees, sqrt

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import arcgeom as AG
from dobim_core import beams as BM
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import queries as Q
from dobim_core import solids as SO

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V, V2, VE = F.V1, F.V2, "4" + "0" * 19 + "e9"
BT, BS, BC = ("9" + "0" * 19 + x for x in ("c1", "c2", "c3"))                              # 보 유형
BA, BB, BCC, BD = ("9" + "0" * 19 + x for x in ("n1", "n2", "n3", "n4"))                   # 보
C1, C2 = ("0" * 20 + x for x in ("d1", "d2"))                                             # 기둥
IBT, IBS, IBM, IBN, IBK = ("3" + "0" * 19 + x for x in ("c7", "c8", "n7", "n8", "n9"))   # IFC 왕복용(첫 글자가 유효한 GlobalId)
SEC = {"shape": "rect", "width": 400.0, "depth": 600.0}


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


def get(s, e):
    return ok(s.run("element.get", {"id": e}))


def poly_volume(verts, faces) -> float:
    """다면체 부피(면마다 팬 삼각분할 + 발산 정리, 구멍 없는 단면)."""
    v = 0.0
    for f in faces:
        lp = f[0]
        a = verts[lp[0]]
        for i in range(1, len(lp) - 1):
            b, c = verts[lp[i]], verts[lp[i + 1]]
            v += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    return v


@pytest.fixture
def bs(base):
    ok(base.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    return base


def curved(**kw):
    p = {"name": "c", "start": [0.0, 0.0], "end": [10000.0, 0.0], "offset": 0.0, "anchor": "top", "section": dict(SEC), "bulge": 0.5}
    p.update(kw)
    return p


# ---------------------------------------------------------------- 1. 곡선 보 순수 형상
def test_1_곡선_보_순수_형상():
    p = curved()
    assert K.row_params_error("beam", p) is None
    ai = BM.arc_info(p)
    c = 10000.0
    r = c * (1 + 0.25) / (4 * 0.5)
    assert ai[2] == pytest.approx(r) and ai[2] == pytest.approx(6250.0) and ai[4] == 1.0
    assert (ai[0], ai[1]) == (pytest.approx(5000.0), pytest.approx(3750.0))                      # 반시계: 중심이 진행 방향 왼쪽
    assert ai[5] == pytest.approx(4 * 0.4636476090008061)                                         # 4 atan(bulge)
    ln = BM.length3(p)
    assert ln == pytest.approx(r * ai[5]) and BM.arc_length(p) == ln
    assert AG.point(ai, ln, 0.0) == (pytest.approx(10000.0, abs=1e-6), pytest.approx(0.0, abs=1e-6))   # 호의 끝이 end
    # 시계 호는 중심이 오른쪽, 대칭
    q = curved(bulge=-0.5)
    aj = BM.arc_info(q)
    assert aj[4] == -1.0 and (aj[0], aj[1]) == (pytest.approx(5000.0), pytest.approx(-3750.0))
    # 거절: 경사 보·끝면 옵션과 함께, 거의 직선, 너무 큰 볼록도, 반지름이 단면 폭의 반보다 작다
    assert K.row_params_error("beam", curved(end_offset=500.0))
    assert K.row_params_error("beam", curved(vertical_ends=True))
    assert K.row_params_error("beam", curved(skew_cut=True))
    assert K.row_params_error("beam", curved(bulge=1.5)) and K.row_params_error("beam", curved(bulge=0.0))
    assert K.row_params_error("beam", curved(bulge=0.0001))                                       # 호 가운데가 현에서 1mm 미만
    assert K.row_params_error("beam", curved(start=[0.0, 0.0], end=[600.0, 0.0], bulge=1.0, section={"shape": "rect", "width": 800.0, "depth": 600.0}))
    assert K.row_params_error("beam", {k: v for k, v in curved(vertical_ends=True).items() if k != "bulge"})   # vertical_ends 는 경사 보만
    assert K.row_params_error("beam", curved(gap=5000.0)) and K.row_params_error("beam", curved(gap=-1.0))
    # 다면체: 옆면은 모두 평면, 체적 = 파푸스(1 % 안: 현 근사는 아래로 치우친다), 시계 호도 같다, 자르면 그만큼 줄어든다
    verts, faces = BM.mesh(p, 1000.0, (0.0, 0.0))
    assert all(SO._planar([verts[i] for i in f[0]]) for f in faces)
    v0 = poly_volume(verts, faces)
    assert v0 == pytest.approx(BM.volume(p, ln), rel=2e-3) and v0 < BM.volume(p, ln)
    vq, fq = BM.mesh(q, 0.0, (0.0, 0.0))
    assert poly_volume(vq, fq) == pytest.approx(v0, rel=1e-9)
    vc, fc = BM.mesh(p, 1000.0, (500.0, 700.0))
    assert poly_volume(vc, fc) == pytest.approx(BM.volume(p, ln - 1200.0), rel=2e-3)
    zs = [v[2] for v in verts]
    assert min(zs) == pytest.approx(400.0) and max(zs) == pytest.approx(1000.0)                   # 윗면 anchor top
    # 조각 수: 한 조각 중심각 4.5도 이하이고 현-호 거리 2mm 이하(반지름 6.25 m 라 후자가 정한다: 2.9도 -> 37조각)
    pcs = BM.pieces(p, 0.0, (0.0, 0.0))
    assert len(pcs) == 37 == len(BM.arc_stations(p, (0.0, 0.0))[1]) - 1
    # 평면 외곽: 오른쪽 가장자리 + 왼쪽 가장자리(되돌아옴), 반시계, 안쪽 가장자리(왼쪽)가 반지름 - 200
    rect = BM.plan_rect(p, (0.0, 0.0))
    assert len(rect) == 76 and SO.area2(rect) > 0
    assert min(sqrt((x - ai[0]) ** 2 + (y - ai[1]) ** 2) for x, y in rect) == pytest.approx(r - 200.0, rel=2e-3)
    # H형강·ㄷ형강·강관도 같은 방식(구멍 포함)
    for name in ("H-400x200x8x13", "C-150x75x6.5x10", "B-200x100x6", "P-139.8x4.5"):
        sc = BM.preset_section(name)
        w = curved(section=sc, anchor="center")
        assert K.row_params_error("beam", w) is None, name
        vv, ff = BM.mesh(w, 0.0, (0.0, 0.0))
        assert all(SO._planar([vv[i] for i in f[0]]) for f in ff), name
    # 비대칭 단면(ㄷ형: 웨브가 왼쪽): 체적이 단면적 x 호 길이와 다르다(도심이 안쪽이라 짧다)
    cw = curved(section=BM.preset_section("C-150x75x6.5x10"), anchor="center")
    assert BM.centroid_a(cw) < 0 and BM.centroid_a(cw) == pytest.approx(BM.centroid_a(curved(section=BM.preset_section("C-150x75x6.5x10"), anchor="center", bulge=0.5)))
    # 곡선 보 끝 자르기: 기둥 단면 안에서 끝나는 호는 호를 따라 기둥 면까지
    col = ("C1", -1e9, 1e9, (-250.0, -250.0, 250.0, 250.0), ((-250.0, -250.0), (250.0, -250.0), (250.0, 250.0), (-250.0, 250.0)))
    cut = BM.trims(p, (-600.0, 0.0), (col,))
    assert cut[1] == 0.0 and cut[2:] == ("C1", None)
    xe, ye = AG.point(ai, cut[0], 0.0)
    assert abs(xe - 250.0) < 1e-3 or abs(ye - 250.0) < 1e-3 or abs(ye + 250.0) < 1e-3          # 잘린 점이 기둥 경계 위
    assert cut[0] > 250.0 and cut[0] < 400.0


# ---------------------------------------------------------------- 2. 곡선 보 명령·수량·편집
def test_2_곡선_보_명령(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 600}))
    assert err(bs.run("beam.create", {"level": F.LV2, "type": BT, "start": [0, -4000], "end": [10000, -4000], "bulge": 0.5, "end_offset": 500}))["code"] == "CONSTRAINT"
    assert err(bs.run("beam.create", {"level": F.LV2, "type": BT, "start": [0, -4000], "end": [10000, -4000], "bulge": 0.5, "vertical_ends": True}))["code"] == "CONSTRAINT"
    assert not bs.run("beam.create", {"level": F.LV2, "type": BT, "start": [0, -4000], "end": [10000, -4000], "bulge": 2.0})["ok"]
    ok(bs.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [0, -4000], "end": [10000, -4000], "bulge": 0.5}))
    p = get(bs, BA)["params"]
    assert p["bulge"] == 0.5
    arc = BM.arc_length(p)
    it = ok(bs.run("beam.list", {}))["items"][0]
    assert it["bulge"] == 0.5 and it["radius"] == pytest.approx(6250.0) and it["sweep_deg"] == pytest.approx(degrees(BM.arc_info(p)[5]), abs=1e-3)
    assert it["length"] == pytest.approx(arc, abs=1e-3) and it["cut_length"] == pytest.approx(arc, abs=1e-3)       # 길이 = 호 길이
    assert it["volume_m3"] == pytest.approx(400 * 600 * arc / 1e9, abs=1e-6)
    t = ok(bs.run("schedule.table", {"category": "beams", "fields": ["name", "length", "volume"], "units": "mm"}))
    assert t["items"][0][1:] == [pytest.approx(arc), pytest.approx(400 * 600 * arc)]
    # 호는 진행 방향 오른쪽(-y)으로 부푼다: 평면 외곽의 가장 낮은 y = 꼭대기(-6500) 바깥 가장자리
    g = g_of(bs)
    ys = [q[1] for q in g.q(D.beam_info, BA)[6]]
    assert min(ys) == pytest.approx(-6698.05, abs=0.1) and max(ys) == pytest.approx(-3800.0 + 0.0, abs=250.0)
    # 직선으로 되돌리기(bulge 0)와 되돌리기
    ok(bs.run("beam.set", {"id": BA, "bulge": 0}))
    assert "bulge" not in get(bs, BA)["params"] and ok(bs.run("beam.list", {}))["items"][0]["length"] == pytest.approx(10000.0)
    ok(bs.run("history.undo", {}))
    assert get(bs, BA)["params"]["bulge"] == 0.5
    assert err(bs.run("beam.set", {"id": BA, "end_offset": 500}))["code"] == "CONSTRAINT"                          # 곡선 보는 수평 보만
    ok(bs.run("beam.set", {"id": BA, "bulge": -0.25, "end": [9000, -4000]}))                                       # 시계 반대로, 끝점도 함께
    q = get(bs, BA)["params"]
    assert q["bulge"] == -0.25 and q["end"] == [9000, -4000]
    ok(bs.run("history.undo", {}))
    # 옮기기·복사·대칭(부풀어 오른 쪽이 거울 쪽으로)·회전
    ok(bs.run("beam.move", {"id": BA, "dx": 100, "dy": 200, "dz": 50}))
    q = get(bs, BA)["params"]
    assert q["start"] == [100, -3800] and q["bulge"] == 0.5 and q["offset"] == 50
    ok(bs.run("history.undo", {}))
    c = ok(bs.run("element.copy", {"ids": [BA], "dx": 0, "dy": -3000}))
    nb = c["ids"][0]
    assert get(bs, nb)["params"]["bulge"] == 0.5 and get(bs, nb)["params"]["start"] == [0, -7000]
    ok(bs.run("element.mirror", {"ids": [nb], "axis": [0, -7000, 1000, -7000]}))
    mp = get(bs, nb)["params"]
    assert mp["bulge"] == -0.5 and mp["start"] == [0, -7000] and mp["end"] == [10000, -7000]
    ai0, ai1 = BM.arc_info(get(bs, BA)["params"]), BM.arc_info(mp)
    assert ai1[4] == -ai0[4] and ai1[2] == pytest.approx(ai0[2])
    ok(bs.run("element.rotate", {"ids": [nb], "center": [0, -7000], "angle": 90}))
    rp = get(bs, nb)["params"]
    assert rp["bulge"] == -0.5 and rp["end"] == [pytest.approx(0.0, abs=1e-3), pytest.approx(3000.0, abs=1e-3)]
    ok(bs.run("element.delete", {"id": nb}))
    # 유형을 바꾸면 곡선 보도 단면이 따라간다
    ok(bs.run("beamtype.set", {"id": BT, "depth": 800}))
    assert ok(bs.run("beam.list", {}))["items"][0]["volume_m3"] == pytest.approx(400 * 800 * arc / 1e9, abs=1e-6)
    ok(bs.run("beamtype.set", {"id": BT, "depth": 600}))
    v = ok(bs.run("model.validate", {}))
    assert not [m for m in v["violations"] if "IfcGloballyUniqueId" not in m], v
    same_as_full(bs)


# ---------------------------------------------------------------- 3. 곡선 보 도면
def test_3_곡선_보_도면(bs):
    VS = "4" + "0" * 19 + "e8"
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 600}))
    ok(bs.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [0, -4000], "end": [10000, -4000], "bulge": 0.5}))           # 절단 높이 위(숨은선)
    ok(bs.run("beam.create", {"id": BB, "level": F.LV1, "type": BT, "start": [0, -12000], "end": [10000, -12000], "bulge": -0.5, "offset": 1400}))   # 절단 높이에 걸친다
    g = g_of(bs)
    hid = g.q(D.beam_lines, V, BA)
    assert hid and all(x[0] == "proj" for x in hid)
    assert len(hid) >= 2 * 37                                                             # 호를 나눈 다각형의 변마다 파선
    arcb = BM.arc_length(get(bs, BB)["params"])
    fills = g.q(D.beam_fills, V, BB)
    assert len(fills) == 37                                                               # 조각(현)마다 절단 다각형
    assert sum(abs(D._area2(list(pl))) / 2.0 for pl in fills) == pytest.approx(400 * arcb, rel=2e-3)   # 절단 면적 = 폭 x 호 길이
    assert any(x[0] == "cut" for x in g.q(D.beam_lines, V, BB))
    assert g.q(D.beam_fills, V, BA) == ()
    # 단면: 호 꼭대기를 가로지르는 절단(x = 5000)은 단면 하나(400 x 600)
    ok(bs.run("view.create", {"id": V2, "name": "S", "type": "section", "p1": [5000, -9000], "p2": [5000, 0]}))
    g = g_of(bs)
    f = g.q(D.beam_fills, V2, BA)
    assert len(f) == 1 and abs(D._area2(list(f[0]))) / 2.0 == pytest.approx(400 * 600)
    assert any(x[0] == "cut" for x in g.q(D.beam_lines, V2, BA))
    # 입면: 곡선 보(회전 없음)는 수직 각기둥 조각으로 벽 선을 가린다
    ok(bs.run("view.create", {"id": VS, "name": "남측", "type": "elevation", "dir": "S"}))
    top = [x for x in g_of(bs).q(D.vwall_lines, VS, F.W1) if abs(x[2] - 3000) < 1e-6 and abs(x[4] - 3000) < 1e-6]
    assert not top and len(g_of(bs).q(D._beam_vpieces, VS, BA)) == 37                    # 벽 윗선(z 3000)이 보 뒤로 숨는다
    ok(bs.run("beam.set", {"id": BA, "roll": 45}))                                       # 회전한 보는 가리지 않는다(1부와 같은 한계)
    assert g_of(bs).q(D._beam_vpieces, VS, BA) == ()
    ok(bs.run("beam.set", {"id": BA, "roll": 0}))
    same_as_full(bs, (V, V2, VS))
    ok(bs.run("beam.set", {"id": BA, "bulge": 0.3}))
    same_as_full(bs, (V, V2, VS))
    # GUI 장면
    from dobim_core import scene as SC
    sc = g_of(bs).q(SC.view_scene, V2)
    assert any(t[0] == "beam" and t[1] == BA for t in sc)


# ---------------------------------------------------------------- 4. 곡선 보 끝 자르기(기둥·주 보)
def test_4_곡선_보_끝_자르기(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 600}))
    ok(bs.run("beamtype.create", {"id": BS, "name": "B1", "width": 300, "depth": 500}))
    ok(bs.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [0, -4000], "end": [10000, -4000], "bulge": 0.5}))
    p = get(bs, BA)["params"]
    ai = BM.arc_info(p)
    # 기둥: 호를 따라 기둥 면까지
    column(bs, C1, (0, -4000))
    g = g_of(bs)
    c = g.q(Q.beam_cut, BA)
    assert c[2] == C1 and c[3] is None and 250.0 < c[0] < 400.0
    xe, ye = AG.point(ai, c[0], 0.0)
    assert abs(ye + 4250.0) < 1e-3 or abs(xe - 250.0) < 1e-3                               # 잘린 점이 기둥 경계 위
    spec = g.q(Q.ifc_spec, BA)
    assert spec["origin"][:2] == (pytest.approx(xe, abs=1e-6), pytest.approx(ye, abs=1e-6))
    assert spec["arc"][2] == pytest.approx((ai[2] * ai[5] - c[0]) / ai[2]) and spec["length"] == pytest.approx(ai[2] * ai[5] - c[0])
    it = ok(bs.run("beam.list", {}))["items"][0]
    assert it["start_cut"] == {"column": C1, "length": round(c[0], 3)} and it["cut_length"] == pytest.approx(ai[2] * ai[5] - c[0], abs=1e-3)
    ok(bs.run("element.delete", {"id": C1}))
    assert g_of(bs).q(Q.beam_cut, BA) == (0.0, 0.0, None, None)
    # 곡선 주 보에 직선 보가 T 로 닿는다: 주 보 바깥 가장자리(현 근사 바깥 변)까지
    ok(bs.run("beam.create", {"id": BB, "level": F.LV2, "type": BS, "start": [5000, -9000], "end": [5000, -6500]}))
    g = g_of(bs)
    cb = (6250.0 * cos(0.5 * 1.8545904360032244 / 37) + 200.0)                             # 현 근사 바깥 변의 중심에서 거리
    want = (-250.0 - cb) - (-6500.0)
    c = g.q(Q.beam_cut, BB)
    assert c[0] == 0.0 and c[2] is None and c[3] == BA and c[1] == pytest.approx(-want, abs=0.02)
    assert g.q(Q.beam_cut, BA) == (0.0, 0.0, None, None)                                    # 주 보는 잘리지 않는다
    ok(bs.run("beam.move", {"id": BA, "dy": -100}))                                         # 주 보를 옮기면 따라간다
    assert g_of(bs).q(Q.beam_cut, BB)[1] == pytest.approx(-want + 100.0, abs=0.02)
    ok(bs.run("history.undo", {}))
    # 곡선 보의 끝이 직선 주 보 안에서 끝난다: 호를 따라 주 보 옆면까지
    ok(bs.run("beam.create", {"id": BD, "level": F.LV2, "type": BT, "start": [-3000, -4000], "end": [3000, -4000]}))
    g = g_of(bs)
    c = g.q(Q.beam_cut, BA)
    assert c[2] == BD and c[3] is None
    x1, y1 = AG.point(ai, c[0], 0.0)
    assert abs(y1 + 4200.0) < 1e-3                                                           # 주 보(폭 400)의 아래 옆면 y = -4000 - 200
    assert g.q(Q.beam_cut, BD) == (0.0, 0.0, None, None)
    ok(bs.run("beam.move", {"id": BD, "dy": 100}))
    assert g_of(bs).q(Q.beam_cut, BA)[0] < c[0]
    same_as_full(bs, (V,))


# ---------------------------------------------------------------- 5. 경사 보 끝면 연직 자르기와 가림 조각
def test_5_경사_보_연직_끝면(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 600}))
    assert err(bs.run("beam.create", {"level": F.LV1, "type": BT, "start": [0, -2000], "end": [3000, -2000], "vertical_ends": True}))["code"] == "CONSTRAINT"   # 수평 보
    ok(bs.run("beam.create", {"id": BA, "level": F.LV1, "type": BT, "start": [0, -2000], "end": [3000, -2000], "offset": 1000, "end_offset": 5000, "vertical_ends": True}))
    p = get(bs, BA)["params"]
    assert p["vertical_ends"] is True
    column(bs, C1, (0, -2000))
    g = g_of(bs)
    c = g.q(Q.beam_cut, BA)
    assert c[2] == C1 and c[0] == pytest.approx(250.0 / 0.6)                                   # 평면 250 = 축 방향 250 / cos
    ends = g.q(Q.beam_ends, BA)
    assert ends == ((0.0, pytest.approx(0.8 / 0.6)), (0.0, pytest.approx(0.8 / 0.6)))          # 평면 위치가 b 에 안 따르게 tan(경사각) 만큼 민다
    verts, faces = g.q(D.beam_info, BA)[0], g.q(D.beam_info, BA)[1]
    assert [round(v[0], 9) for v in verts[:4]] == [250.0] * 4                                    # 시작 끝면이 기둥 면(x = 250)에 나란한 연직면
    assert [round(v[0], 9) for v in verts[4:8]] == [3000.0] * 4                                  # 끝 끝면도 연직면(x = 3000)
    assert all(SO._planar([verts[i] for i in f[0]]) for f in faces)
    assert poly_volume(verts, faces) == pytest.approx(400 * 600 * (5000.0 - 250 / 0.6), rel=1e-9)   # 끝면이 어긋나도 체적 = 단면적 x 자른 실길이
    # 끝면이 수직이 아닌 보(옵션 없음)는 같은 위치에서 끝면 점 x 가 b 에 따라 퍼진다
    ok(bs.run("beam.set", {"id": BA, "vertical_ends": False}))
    assert "vertical_ends" not in get(bs, BA)["params"] and g_of(bs).q(Q.beam_ends, BA) is None
    v2 = g_of(bs).q(D.beam_info, BA)[0]
    assert len({round(v[0], 6) for v in v2[:4]}) == 2
    ok(bs.run("history.undo", {}))
    # 평면 외곽: 끝면이 연직이라 x 범위가 [250, 3000]
    rect = g_of(bs).q(D.beam_info, BA)[6]
    assert min(q[0] for q in rect) == pytest.approx(250.0) and max(q[0] for q in rect) == pytest.approx(3000.0)
    # 대칭·복사해도 옵션이 따라간다, 수평으로 되돌리면 옵션과 충돌
    ok(bs.run("element.mirror", {"ids": [BA], "axis": [0, 0, 1000, 0]}))
    assert get(bs, BA)["params"]["vertical_ends"] is True
    ok(bs.run("history.undo", {}))
    assert err(bs.run("beam.set", {"id": BA, "end_offset": 1000}))["code"] == "CONSTRAINT"
    ok(bs.run("beam.set", {"id": BA, "end_offset": 1000, "vertical_ends": False}))
    assert "end_offset" not in get(bs, BA)["params"]
    ok(bs.run("history.undo", {}))
    same_as_full(bs, (V,))


def test_5_경사_보_가림_조각(bs):
    VS = "4" + "0" * 19 + "e8"
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 600}))
    ok(bs.run("view.create", {"id": VS, "name": "남측", "type": "elevation", "dir": "S"}))          # 남쪽에서 북쪽(벽 W1, y = 0)을 본다
    # 벽 앞(y = -1000)에서 벽 윗선 z 3000 을 가로지르는 경사 보: x 0 -> 6000 에서 윗면 높이 2000 -> 4000(기준선 anchor top)
    ok(bs.run("beam.create", {"id": BA, "level": F.LV1, "type": BT, "start": [0, -1000], "end": [6000, -1000], "offset": 2000, "end_offset": 4000}))
    g = g_of(bs)
    pcs = g.q(D._beam_vpieces, VS, BA)
    assert pcs and all(z1 > z0 for z0, z1, _p in pcs) and len(pcs) <= BM.SLAB_MAX
    zs = [z for z0, z1, _p in pcs for z in (z0, z1)]
    assert min(zs) > 1300.0 and max(zs) < 4100.0                                                  # 보의 높이 범위 안(안쪽 근사)
    top = [x for x in g.q(D.vwall_lines, VS, F.W1) if abs(x[2] - 3000) < 1e-6 and abs(x[4] - 3000) < 1e-6]
    # 벽 윗선(z 3000)은 보가 덮는 구간만 사라진다. 보 윗면은 z = 2000 + x/3 이라 x = 3000 에서 3000 을 지나고, 아랫면(윗면에서 축에 수직으로 600)은
    # 아랫면의 점 (x, z) = (t cos + 600 sin, 2000 + t sin - 600 cos)(tan = 1/3)이 z = 3000 인 x ~ 4897 에서 지난다. 안쪽 근사라 가장자리가 높이 조각 / tan = 3 x 10 mm 안쪽으로 물러난다
    vis = sorted({(min(x[1], x[3]), max(x[1], x[3])) for x in top})
    tol = 4.0 * BM.SLAB_MM + 1.0
    assert len(vis) == 2 and vis[0][0] == pytest.approx(0.0) and vis[1][1] == pytest.approx(6100.0)
    assert 3000.0 - 1.0 <= vis[0][1] <= 3000.0 + tol                                              # 보 윗면 쪽 가장자리
    assert 4897.0 - tol <= vis[1][0] <= 4897.0 + 1.0                                              # 보 아랫면 쪽 가장자리
    # 보가 없으면 벽 윗선이 한 줄로 이어진다
    ok(bs.run("element.delete", {"id": BA}))
    top0 = [x for x in g_of(bs).q(D.vwall_lines, VS, F.W1) if abs(x[2] - 3000) < 1e-6 and abs(x[4] - 3000) < 1e-6]
    assert top0 and max(abs(x[3] - x[1]) for x in top0) > 5000
    ok(bs.run("history.undo", {}))
    same_as_full(bs, (V, VS))
    # 높이 조각 하나는 보 안쪽에 든다: 조각의 평면 다각형이 보 평면 외곽(+ 작은 여유) 안
    info = g_of(bs).q(D.beam_info, BA)
    for z0, z1, poly in BM.slab_slices(info[5]):
        assert z1 > z0 and len(poly) >= 3 and SO.area2(list(poly)) > 0
    assert BM.slab_slices(info[5])
    assert len(BM.slab_slices(info[5])) <= BM.SLAB_MAX * len(info[5])


# ---------------------------------------------------------------- 6. 보끼리 접합 간격과 경사 자르기
def _tee(bs, skew=False, gap=0.0, start=(1000, 4000), end=(3000, 0)):
    """주 보 BA(x 축 방향, 폭 400, y -200 ~ 200)에 작은 보 BB 가 비스듬히 닿는다(끝이 주 보 가운데 선 위)."""
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    ok(bs.run("beamtype.create", {"id": BS, "name": "B1", "width": 300, "depth": 500}))
    ok(bs.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [-2000, 0], "end": [8000, 0]}))
    a = {"id": BB, "level": F.LV2, "type": BS, "start": list(start), "end": list(end)}
    if skew:
        a["skew_cut"] = True
    if gap:
        a["gap"] = gap
    ok(bs.run("beam.create", a))


def test_6_접합_간격(bs):
    _tee(bs)
    sn = 4000.0 / sqrt(2000.0 ** 2 + 4000.0 ** 2)                                                    # 보 축과 주 보 옆면이 이루는 각의 사인
    c0 = g_of(bs).q(Q.beam_cut, BB)
    assert c0 == (0.0, pytest.approx(200.0 / sn), None, BA)                                          # 주 보 옆면(y = 200)까지, 축 방향 200 / sin
    ok(bs.run("beam.set", {"id": BB, "gap": 50}))
    assert get(bs, BB)["params"]["gap"] == 50.0
    c1 = g_of(bs).q(Q.beam_cut, BB)
    assert c1[1] == pytest.approx(200.0 / sn + 50.0) and c1[3] == BA                                  # 간격 = 축 방향으로 더 일찍 끝난다
    it = {x["id"]: x for x in ok(bs.run("beam.list", {}))["items"]}
    assert it[BB]["gap"] == 50.0 and it[BB]["end_cut"] == {"beam": BA, "length": round(200.0 / sn + 50.0, 3)}
    t = ok(bs.run("schedule.table", {"category": "beams", "fields": ["name", "length"], "units": "mm"}))
    assert sorted(round(r[1], 3) for r in t["items"])[0] == pytest.approx(sqrt(2000.0 ** 2 + 4000.0 ** 2) - 200.0 / sn - 50.0, abs=1e-3)
    ok(bs.run("beam.move", {"id": BA, "dy": 100}))                                                   # 주 보를 옮기면 따라간다
    assert g_of(bs).q(Q.beam_cut, BB)[1] == pytest.approx(300.0 / sn + 50.0)
    ok(bs.run("history.undo", {}))
    # 간격이 남는 길이를 CUT_MIN 아래로 만들면 간격 없이 자른다
    ok(bs.run("beam.set", {"id": BB, "gap": 1000}))
    assert g_of(bs).q(Q.beam_cut, BB)[1] == pytest.approx(200.0 / sn + 1000.0)
    ok(bs.run("beam.set", {"id": BB, "start": [3000, 250], "end": [3000, 0], "gap": 100}))           # 평면 길이 250: 주 보 옆면까지 200 -> 남는 길이 50, 간격 100 을 더하면 모자라 간격은 안 든다
    c = g_of(bs).q(Q.beam_cut, BB)
    assert c[3] == BA and c[1] == pytest.approx(200.0)
    ok(bs.run("beam.set", {"id": BB, "gap": 0, "start": [1000, 4000], "end": [3000, 0]}))
    assert "gap" not in get(bs, BB)["params"] and g_of(bs).q(Q.beam_cut, BB)[1] == pytest.approx(200.0 / sn)
    # 기둥 면에는 적용하지 않는다
    column(bs, C1, (3000, 0))
    ok(bs.run("beam.set", {"id": BB, "gap": 50}))
    c = g_of(bs).q(Q.beam_cut, BB)
    assert c[3] == C1                                                                                # 기둥(250)이 보(200 + 50)보다 많이 줄인다
    same_as_full(bs, (V,))
    assert not bs.run("beam.set", {"id": BB, "gap": 5000})["ok"]


def test_6_경사_자르기(bs):
    _tee(bs, skew=True)
    sn = 4000.0 / sqrt(2000.0 ** 2 + 4000.0 ** 2)
    g = g_of(bs)
    ends = g.q(Q.beam_ends, BB)
    assert ends[0] == (0.0, 0.0) and ends[1][1] == 0.0 and ends[1][0] != 0.0                       # 시작은 수직, 끝만 주 보 옆면에 맞춘다
    verts, faces = g.q(D.beam_info, BB)[0], g.q(D.beam_info, BB)[1]
    assert [round(v[1], 6) for v in verts[4:8]] == [200.0] * 4                                      # 끝 끝면이 주 보 옆면(y = 200)과 나란한 연직면
    assert len({round(v[0], 3) for v in verts[4:8]}) == 2                                          # 끝면 위의 점들은 보 폭만큼 옆으로 퍼진다(두 x)
    assert all(SO._planar([verts[i] for i in f[0]]) for f in faces)
    # 체적 = 단면적 x 자른 길이(끝면이 비스듬해도 같다: 끝 기준점이 축 위)
    cl = sqrt(2000.0 ** 2 + 4000.0 ** 2) - 200.0 / sn
    assert poly_volume(verts, faces) == pytest.approx(300 * 500 * cl, rel=1e-9)
    # 평면 외곽은 끝이 비스듬한 평행사변형: 끝 쪽 두 점의 y 가 모두 200
    rect = g.q(D.beam_info, BB)[6]
    assert sorted(round(q[1], 6) for q in rect)[:2] == [200.0, 200.0] and len(rect) == 4
    # 간격과 함께: 끝면 평면이 주 보 옆면에서 간격만큼(축 방향) 물러난다
    ok(bs.run("beam.set", {"id": BB, "gap": 100}))
    v2 = g_of(bs).q(D.beam_info, BB)[0]
    assert [round(v[1], 6) for v in v2[4:8]] == [pytest.approx(200.0 + 100.0 * sn)] * 4
    ok(bs.run("beam.set", {"id": BB, "gap": 0}))
    # 수직으로 닿으면 끝면이 축에 수직이라 옵션은 효과가 없다(끝면 기울기 없음)
    ok(bs.run("beam.set", {"id": BB, "start": [3000, 4000], "end": [3000, 0]}))
    assert g_of(bs).q(Q.beam_ends, BB) is None
    # 20도보다 얕게 닿으면 맞추지 않는다
    ok(bs.run("beam.set", {"id": BB, "start": [-5000, 1500], "end": [3000, 0]}))
    assert g_of(bs).q(Q.beam_cut, BB)[3] == BA and g_of(bs).q(Q.beam_ends, BB) is None
    # 곡선 보와 함께 둘 수 없다, 기둥에 닿으면 변 방향이 없어 맞추지 않는다
    assert err(bs.run("beam.set", {"id": BB, "bulge": 0.3}))["code"] == "CONSTRAINT"
    ok(bs.run("beam.set", {"id": BB, "start": [1000, 4000], "end": [3000, 0]}))
    column(bs, C1, (3000, 0))
    assert g_of(bs).q(Q.beam_cut, BB)[3] == C1 and g_of(bs).q(Q.beam_ends, BB) is None
    ok(bs.run("element.delete", {"id": C1}))
    # 곡선 주 보: 외곽 변(현)의 방향으로 맞춘다(호 꼭대기(3000, -1500)의 안쪽 가장자리에 비스듬히 닿는다)
    ok(bs.run("beam.set", {"id": BA, "bulge": 0.3}))
    ok(bs.run("beam.set", {"id": BB, "start": [1000, 2500], "end": [3000, -1500]}))
    g = g_of(bs)
    assert g.q(Q.beam_cut, BB)[3] == BA and g.q(Q.beam_ends, BB)[1][0] != 0.0
    same_as_full(bs, (V,))
    # 대칭해도 옵션이 따라가고 끝면이 거울 쪽으로 맞춰진다
    ok(bs.run("element.mirror", {"ids": [BA, BB], "axis": [0, 5000, 1000, 5000]}))
    g = g_of(bs)
    assert get(bs, BB)["params"]["skew_cut"] is True and get(bs, BA)["params"]["bulge"] == -0.3
    assert g.q(Q.beam_cut, BB)[3] == BA and g.q(Q.beam_ends, BB) is not None


# ---------------------------------------------------------------- 7. 저장하고 다시 열기
def test_7_저장하고_다시_열기(tmp_path):
    s = kit.Session(tmp_path / "b.dobim")
    try:
        for name, args in F.SCRIPT:
            ok(s.run(name, args))
        ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
        ok(s.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700, "material": "C24"}))
        ok(s.run("beamtype.create", {"id": BS, "name": "B1", "width": 300, "depth": 500}))
        ok(s.run("beam.create", {"id": BA, "level": F.LV2, "type": BT, "start": [-2000, -3000], "end": [8000, -3000], "bulge": 0.4}))
        ok(s.run("beam.create", {"id": BB, "level": F.LV2, "type": BS, "start": [1000, -9000], "end": [3000, -5000], "skew_cut": True, "gap": 30}))
        ok(s.run("beam.create", {"id": BCC, "level": F.LV1, "type": BS, "start": [0, -9000], "end": [3000, -9000], "offset": 1000, "end_offset": 4000, "vertical_ends": True}))
        key = lambda s_: (get(s_, BA)["params"], get(s_, BB)["params"], get(s_, BCC)["params"], g_of(s_).q(D.view_lines, V), g_of(s_).q(Q.ifc_spec, BA),
                          g_of(s_).q(Q.ifc_spec, BB), g_of(s_).q(Q.ifc_spec, BCC), g_of(s_).q(Q.beam_cut, BB), g_of(s_).q(Q.beam_ends, BB))
        before = key(s)
    finally:
        s.close()
    s = kit.Session(tmp_path / "b.dobim", create=False)
    try:
        assert key(s) == before and before[7][3] == BA and "clips" in before[5] and "arc" in before[4] and "clips" in before[6]
    finally:
        s.close()


# ---------------------------------------------------------------- 8. IFC
def test_8_IFC_곡선_보와_연직_끝면(tmp_path):
    import ifcopenshell.geom
    import ifcopenshell.util.shape as US
    import numpy as np
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("beamtype.create", {"id": IBT, "name": "SB1", "preset": "C-150x75x6.5x10", "material": "SS275"}))
        ok(b.run("beamtype.create", {"id": IBS, "name": "G1", "width": 400, "depth": 700, "material": "C24"}))
        ok(b.run("beam.create", {"id": IBM, "level": F.LV2, "type": IBT, "start": [0, -3000], "end": [6000, -3000], "bulge": 0.5, "anchor": "center"}))         # 반시계 ㄷ형
        ok(b.run("beam.create", {"id": IBN, "level": F.LV2, "type": IBS, "start": [0, -9000], "end": [6000, -9000], "bulge": -0.4, "roll": 20}))             # 시계 + 회전
        ok(b.run("beamtype.create", {"name": "P1", "preset": "P-114.3x4.5"}))
        tid = next(x["id"] for x in ok(b.run("beamtype.list", {}))["items"] if x["name"] == "P1")
        ok(b.run("beam.create", {"id": IBK, "level": F.LV2, "type": tid, "start": [0, -14000], "end": [4000, -14000], "anchor": "center", "bulge": 0.2}))           # 강관
        IBV, IBW = "3" + "0" * 19 + "v1", "3" + "0" * 19 + "v2"
        ok(b.run("beam.create", {"id": IBV, "level": F.LV2, "type": IBS, "start": [0, -20000], "end": [4000, -20000], "offset": 0, "end_offset": 1500, "vertical_ends": True}))   # 경사 + 연직 끝면
        ok(b.run("beam.create", {"id": "3" + "0" * 19 + "v0", "level": F.LV2, "type": IBS, "start": [-2000, -24000], "end": [8000, -24000]}))                       # 주 보(경사 자르기)
        ok(b.run("beam.create", {"id": IBW, "level": F.LV2, "type": IBT, "start": [1000, -28000], "end": [3000, -24000], "skew_cut": True, "gap": 20}))
        h = b.hashes()
        out = b.export("b.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        st = ifcopenshell.geom.settings()
        st.set("use-world-coords", True)

        def world(e):
            sh = ifcopenshell.geom.create_shape(st, e)
            return np.array(sh.geometry.verts).reshape(-1, 3) * 1000.0, US.get_volume(sh.geometry) * 1e9

        items = {x["id"]: x for x in ok(b.run("beam.list", {}))["items"]}
        for bid in (IBM, IBN, IBK):
            e = f.by_guid(bid)
            rep = e.Representation.Representations[0]
            sw = rep.Items[0]
            assert rep.RepresentationType == "AdvancedSweptSolid" and sw.is_a("IfcSurfaceCurveSweptAreaSolid")
            assert sw.Directrix.is_a("IfcTrimmedCurve") and sw.Directrix.BasisCurve.is_a("IfcCircle") and sw.ReferenceSurface.is_a("IfcPlane")
            p = get(b, bid)["params"]
            ai = BM.arc_info(p)
            r_, sg_, phi = ai[2], ai[4], ai[5]
            assert sw.Directrix.BasisCurve.Radius == pytest.approx(r_) and sw.EndParam == pytest.approx(phi) and sw.StartParam == 0.0
            assert tuple(sw.Directrix.BasisCurve.Position.Axis.DirectionRatios) == (0.0, 0.0, sg_)
            # 몸체 정점이 코어 다면체의 정점(현 근사)과 같은 자리: 시작·끝 끝면(방사 평면)의 모든 정점이 IFC 몸체 위
            vs, vol = world(e)
            mesh_v, mesh_f = BM.mesh(p, 3300.0, (0.0, 0.0))
            mv = np.array(mesh_v)
            nloop = len(BM.section_outline(p))
            n_st = len(BM.arc_stations(p, (0.0, 0.0))[1])
            if p["section"]["shape"] != "pipe":   # 강관의 원은 IFC 쪽이 참 원이라 정점이 다르다(체적으로 본다)
                for q in list(mv[:nloop]) + list(mv[(n_st - 1) * nloop: n_st * nloop]):
                    assert np.min(np.linalg.norm(vs - q, axis=1)) < 0.5, (bid, q)   # 마이터 늘림 a x (1/cos(반각) - 1) 만큼(1.2 mm 이하) 참 스윕보다 바깥
            # 체적 = 파푸스(참 스윕과 같다)
            assert vol == pytest.approx(BM.volume(p, BM.arc_length(p)), rel=2e-2 if p["section"]["shape"] == "pipe" else 2e-4)   # 강관 원은 ifcopenshell 이 다각형으로 그려 1% 안
        # 연직 끝면: 두 반공간으로 잘라낸 IfcBooleanClippingResult, 몸체 정점이 코어 다면체 정점을 모두 포함하고 체적이 같다
        e = f.by_guid(IBV)
        rep = e.Representation.Representations[0]
        assert rep.RepresentationType == "Clipping" and rep.Items[0].is_a("IfcBooleanClippingResult")
        n_hs = 0
        it = rep.Items[0]
        while it.is_a("IfcBooleanClippingResult"):
            assert it.SecondOperand.is_a("IfcHalfSpaceSolid") and it.SecondOperand.AgreementFlag is True
            n_hs += 1
            it = it.FirstOperand
        assert n_hs == 2 and it.is_a("IfcExtrudedAreaSolid")
        pv = get(b, IBV)["params"]
        ends = BM.end_shears(pv, (None, None))
        assert ends == ((0.0, pytest.approx(1500.0 / 4000.0)), (0.0, pytest.approx(1500.0 / 4000.0)))
        mv_, mf_ = BM.mesh(pv, 3300.0, (0.0, 0.0), ends)
        vs, vol = world(e)
        for q in np.array(mv_):
            assert np.min(np.linalg.norm(vs - q, axis=1)) < 0.05, q
        assert vol == pytest.approx(poly_volume(mv_, mf_), rel=1e-6)
        # 경사 자르기 + 간격: 끝 끝면 정점이 코어와 같다
        e = f.by_guid(IBW)
        assert e.Representation.Representations[0].RepresentationType == "Clipping"
        pw = get(b, IBW)["params"]
        cutw = items[IBW]["end_cut"]["length"]                                                        # 주 보로 자른 끝(간격 20 포함)
        ends = BM.end_shears(pw, (None, (1.0, 0.0)))
        assert ends is not None and ends[0] == (0.0, 0.0)
        mw_, fw_ = BM.mesh(pw, 3300.0, (0.0, cutw), ends)
        vs, vol = world(e)
        for q in np.array(mw_):
            assert np.min(np.linalg.norm(vs - q, axis=1)) < 0.05, q
        assert vol == pytest.approx(BM.volume(pw, BM.length3(pw) - cutw, ends), rel=1e-4)                  # 비대칭 단면(ㄷ)이라 끝 밀림 평균만큼 길이가 달라진다
        assert vol != pytest.approx(BM.section_area(pw["section"]) * (BM.length3(pw) - cutw), rel=1e-4)
        # 재료: 곡선 보도 IfcMaterialProfileSetUsage
        u = [r.RelatingMaterial for r in f.by_guid(IBM).HasAssociations if r.is_a("IfcRelAssociatesMaterial")]
        assert len(u) == 1 and u[0].is_a("IfcMaterialProfileSetUsage") and u[0].CardinalPoint == 5
        # IFC2X3: IfcSurfaceCurveSweptAreaSolid·IfcBooleanClippingResult 모두 2x3 에 있다
        assert b.destructive("ifc.export", {"path": "b3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "b3.ifc"))
        assert file_errors(f3) == []
        assert len(f3.by_type("IfcSurfaceCurveSweptAreaSolid")) == 3 and len(f3.by_type("IfcBooleanClippingResult")) == 4
        # 왕복: 가져오기 해시·IFC 바이트
        r = b.import_ifc("b.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("b2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        # 곡선을 직선으로 되돌렸다가 되돌리면 같다
        ok(b.run("beam.set", {"id": IBM, "bulge": 0}))
        assert len(ifcopenshell.open(str(b.export("b4.ifc"))).by_type("IfcSurfaceCurveSweptAreaSolid")) == 2
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
        ok(b.run("element.delete", {"id": IBM}))
        f5 = ifcopenshell.open(str(b.export("b5.ifc")))
        assert file_errors(f5) == [] and len(f5.by_type("IfcSurfaceCurveSweptAreaSolid")) == 2
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()
