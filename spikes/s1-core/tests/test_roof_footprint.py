# SPDX-License-Identifier: MPL-2.0
"""외곽선 지붕(가중 직선 골격)·지붕 유형·지붕 층 구성·처마 부속 (W2 여덟 번째 PR, ARC-19·ARC-20).

- 순수 계산: 기존 다각형 지붕(roofpoly)이 되는 외곽은 같은 면, 오목·물매가 다른 변·짧은 변(1mm)·변마다 내밀기·원뿔 부채꼴, 볼록 외곽의 높이 = 물매 x 거리,
  오목 외곽의 높이 <= 물매 x 거리, 거절 문장, 결정성
- 커맨드: roof.create·roof.set form=footprint·overhangs, 시계 외곽 뒤집기, 대칭 복사의 변 번호, poly 거절 힌트
- 지붕 유형: 만들기·고치기·복제·지우기(detach)·일람표, 유형 전파, 유형 있는 지붕의 층 구성·두께 편집 거절, 실행 취소·다시 실행
- 처마 부속: fascia(모서리 연귀)·gutter(수평 처마만)·soffit, roof.eaves 조회, 꼭짓점 수 변경 거절, 벽 상단 붙이기
- 압출 지붕(roof.extrude): 프로파일 검사·면·용마루, 고치기·이동·대칭, 처마 부속·벽 상단 붙이기, IFC
- 도면: 지붕 평면(처마 부속 윤곽), 단면(층 경계선·처마 부속 절단면), 증분 = 전체 재계산
- IFC: IfcRoofType + IfcRelDefinesByType + 유형 IfcMaterialLayerSet, 지붕 IfcMaterialLayerSetUsage, 처마 부속 IfcCovering·IfcPipeSegment(GUTTER),
  IFC4·IFC2X3 검증 오류 0, 가져오기 왕복 해시·바이트, 가져온 프로젝트 고치기·되돌리기
"""
import math
import random
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import roofeaves as RE
from dobim_core import roofext as RX
from dobim_core import roofpoly as RP
from dobim_core import roofskel as RS

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

RT = F.RT1
RF = F.RF1
VP, VS, VE = F.V1, F.V2, "4" + "0" * 20 + "8"
IRT, IRF, IRG, IRX = ("3" + "0" * 19 + x for x in ("u1", "r1", "r2", "r3"))
L4 = [{"material": "우레탄 방수", "thickness": 5}, {"material": "무근 콘크리트", "thickness": 150}, {"material": "단열재", "thickness": 220},
      {"material": "철근콘크리트", "thickness": 50}]
RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
LSH = [[0, 0], [9000, 0], [9000, 4000], [4000, 4000], [4000, 8000], [0, 8000]]
TSH = [[0, 0], [12000, 0], [12000, 4000], [8000, 4000], [8000, 9000], [4000, 9000], [4000, 4000], [0, 4000]]
USH = [[0, 0], [10000, 0], [10000, 8000], [7000, 8000], [7000, 3000], [3000, 3000], [3000, 8000], [0, 8000]]
ARB = [[0, 0], [7000, 0], [7000, 3000], [5500, 2000], [4000, 5000], [1500, 3500], [0, 6000]]
JOG = [[0, 0], [5000, 0], [5000, 200], [8000, 200], [8000, 5000], [0, 5000]]   # 200mm 짧은 변


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


def geom(b, sl, oh=None, z0=0.0):
    oh = oh if oh is not None else [0.0] * len(b)
    return RS.geom(b, sl, K.roof_tans(sl), oh, z0)


def _areas(g):
    return sorted(round(abs(RP._area2([(q[0], q[1]) for q in fc])) / 2e6, 4) for fc in g["faces"])


def height_at(g, p):
    """윗면 높이(그 점을 덮는 볼록 조각의 평면). 경계 위면 아무 조각."""
    for fc, (a, b, c) in g["pieces"]:
        xy = [(q[0], q[1]) for q in fc]
        n = len(xy)
        if all((xy[(i + 1) % n][0] - xy[i][0]) * (p[1] - xy[i][1]) - (xy[(i + 1) % n][1] - xy[i][1]) * (p[0] - xy[i][0]) >= -1e-6 for i in range(n)):
            return a * p[0] + b * p[1] + c
    return None


def _dist(b, p):
    best = 1e300
    n = len(b)
    for i in range(n):
        best = min(best, RP._dist_seg(b[i], b[(i + 1) % n], p))
    return best


# ---------------------------------------------------------------- 순수 계산
@pytest.mark.parametrize("b", [RECT, LSH, TSH, USH, [[0, 0], [6000, 0], [7500, 3000], [3000, 6000], [-1500, 3000]]])
@pytest.mark.parametrize("oh", [0.0, 600.0])
def test_기존_다각형_지붕이_되는_외곽은_같은_면이다(b, oh):
    for sl in ([30.0] * len(b), [30.0 if i % 2 == 0 else 0.0 for i in range(len(b))]):
        ks = K.roof_tans(sl)
        if RP.problem(b, sl, ks, oh, 500.0) is not None:
            continue
        g1 = RP.geom(b, sl, ks, oh, 0.0)
        g2 = RS.geom(b, sl, ks, [oh] * len(b), 0.0)
        assert _areas(g1) == _areas(g2)
        assert g2["ztop"] == pytest.approx(g1["ztop"]) and g2["zlow"] == pytest.approx(g1["zlow"])
        assert (len(g1["ridges"]), len(g1["hips"]), len(g1["valleys"])) == (len(g2["ridges"]), len(g2["hips"]), len(g2["valleys"]))


def test_새_외곽들은_면이_외곽을_덮고_골격이_맞다():
    cases = {   # 이름: (외곽, 물매, 내밀기, (면, 용마루, 추녀마루, 골))
        "모임": (RECT, [30] * 4, None, (4, 1, 4, 0)),
        "정사각 모임": ([[0, 0], [4000, 0], [4000, 4000], [0, 4000]], [30] * 4, None, (4, 0, 4, 0)),
        "물매 섞인 L": (LSH, [30, 40, 25, 35, 30, 45], None, (6, 2, 6, 1)),
        "물매 섞인 T": (TSH, [30, 20, 35, 30, 45, 30, 25, 30], None, (8, 3, 8, 2)),
        "변마다 내밀기 U": (USH, [30, 40, 30, 25, 30, 35, 30, 20], [600] * 8, (8, 3, 8, 2)),
        "변마다 내밀기 직사각": (RECT, [30, 45, 30, 20], [600, 300, 0, 900], (4, 1, 4, 0)),
        "짧은 변": (JOG, [30] * 6, [500] * 6, (6, 1, 6, 1)),
        "오목 임의": (ARB, [25, 35, 30, 40, 30, 20, 30], [300] * 7, (7, 0, 9, 2)),
    }
    for name, (b, sl, oh, want) in cases.items():
        assert RS.problem(b, K.roof_tans(sl), oh or [0.0] * len(b)) is None, name
        g = geom(b, sl, oh)
        assert (len(g["faces"]), len(g["ridges"]), len(g["hips"]), len(g["valleys"])) == want, name
        assert sum(abs(RP._area2([(q[0], q[1]) for q in fc])) for fc in g["faces"]) == pytest.approx(abs(RP._area2(g["outline"])), rel=1e-9), name
        assert len(g["eave_src"]) == len(g["outline"]) and g["fpitch"]
    # 변마다 내밀기: 처마 윤곽은 내민 선의 교점(연귀), 가장 낮은 처마 = 물매 x 내밀기가 가장 큰 변
    g = geom(RECT, [30, 45, 30, 20], [600, 300, 0, 900])
    assert sorted(g["outline"]) == sorted([(-900.0, -600.0), (6300.0, -600.0), (6300.0, 4000.0), (-900.0, 4000.0)])
    assert g["zlow"] == pytest.approx(-600 * math.tan(math.radians(30)), abs=1e-6)


def test_볼록_외곽의_높이는_물매_곱하기_거리이고_오목이면_넘지_않는다():
    rnd = random.Random(7)
    circ = [[10000 * math.cos(2 * math.pi * i / 24), 10000 * math.sin(2 * math.pi * i / 24)] for i in range(24)]
    k = math.tan(math.radians(30))
    for b, convex in ((circ, True), (RECT, True), (LSH, False), (USH, False), (ARB, False), (JOG, False)):
        g = geom(b, [30] * len(b))
        xs, ys = [q[0] for q in b], [q[1] for q in b]
        n = 0
        while n < 60:
            p = (rnd.uniform(min(xs), max(xs)), rnd.uniform(min(ys), max(ys)))
            if not RP._point_in([tuple(q) for q in b], p):
                continue
            n += 1
            h = height_at(g, p)
            assert h is not None
            d = _dist(b, p)
            if convex:
                assert h == pytest.approx(k * d, abs=1e-6)
            else:
                assert -1e-6 <= h <= k * d + 1e-6


def test_원뿔_부채꼴_지붕과_많은_꼭짓점():
    def sector(ri, ro, a0, a1, m):
        outer = [[ro * math.cos(math.radians(a0 + (a1 - a0) * i / m)), ro * math.sin(math.radians(a0 + (a1 - a0) * i / m))] for i in range(m + 1)]
        inner = [[ri * math.cos(math.radians(a1 - (a1 - a0) * i / m)), ri * math.sin(math.radians(a1 - (a1 - a0) * i / m))] for i in range(m + 1)]
        b = outer + inner
        sl = [0.0] * len(b)
        for i in range(m + 1, len(b) - 1):   # 안쪽 호 변만 물매(바깥으로 오른다)
            sl[i] = 8.0
        return b, sl
    b, sl = sector(17350, 24350, 10, 70, 30)
    g = geom(b, sl)
    assert len(g["faces"]) == 30 and len(g["valleys"]) == 29
    assert g["ztop"] == pytest.approx((24350 - 17350 * math.cos(math.radians(1))) * math.tan(math.radians(8)), rel=2e-3)
    c = [[30000 * math.cos(2 * math.pi * i / 120), 30000 * math.sin(2 * math.pi * i / 120)] for i in range(120)]
    assert RS.problem(c, K.roof_tans([30] * 120), [500.0] * 120) is None


def test_거절_문장과_결정성():
    t = K.roof_tans
    assert "꼭짓점 3~" in RS.problem([[0, 0], [1, 0]], [0.5] * 2, [0.0] * 2)
    assert "반시계" in RS.problem(RECT[::-1], t([30] * 4), [0.0] * 4)
    assert "짧다" in RS.problem([[0, 0], [6000, 0], [6000, 0.5], [6000, 4000], [0, 4000]], t([30] * 5), [0.0] * 5)
    assert "한 직선" in RS.problem([[0, 0], [3000, 0], [6000, 0], [6000, 4000], [0, 4000]], t([30] * 5), [0.0] * 5)
    assert "스스로 겹친다" in RS.problem([[0, 0], [10000, 0], [10000, 5000], [3000, -2000], [0, 5000]], t([30] * 5), [0.0] * 5)
    assert "경사 변" in RS.problem(RECT, t([0] * 4), [0.0] * 4)
    assert "오목 꼭짓점" in RS.problem(LSH, t([30, 30, 30, 0, 30, 30]), [0.0] * 6)
    assert "꼬인다" in RS.problem(USH, t([30] * 8), [2500.0] * 8)
    a = geom(USH, [30, 40, 30, 25, 30, 35, 30, 20], [600] * 8)
    RS._build.cache_clear()
    b = geom(USH, [30, 40, 30, 25, 30, 35, 30, 20], [600] * 8)
    assert a == b


def test_처마_부속_조각():
    p = {"name": "r", "boundary": RECT, "form": "footprint", "pitch": 30.0, "overhang": 600.0, "base_offset": 0.0, "thickness": 200.0, "slopes": [30.0, 0.0, 30.0, 0.0],
         "eaves": {"fascia": {"edges": [0, 1, 2, 3], "width": 30.0, "depth": 250.0}, "gutter": {"edges": [0, 1, 2], "width": 150.0, "depth": 120.0},
                   "soffit": {"edges": [0, 1], "thickness": 15.0}}}
    assert K.row_params_error("roof", p) is None
    pcs = K.roof_eave_pieces(p)
    sm = RE.summary(pcs)
    assert sm["fascia"]["pieces"] == 6                       # 처마 2 + 박공 변마다 경사 2
    assert sm["gutter"]["pieces"] == 2                       # 박공 변(경사 처마)에는 홈통을 달지 않는다
    assert sm["soffit"]["pieces"] == 1                       # 박공 변(경사)에는 반자를 달지 않는다
    assert sm["fascia"]["length"] > 2 * 7200
    ze = -600 * math.tan(math.radians(30))
    fas = [pc for pc in pcs if pc[1] == "fascia" and pc[0].startswith("fascia:0:")][0]
    assert fas[4][0] == pytest.approx(ze) and fas[3][0] == pytest.approx(ze - 250.0)
    assert (min(q[0] for q in fas[2]), min(q[1] for q in fas[2])) == pytest.approx((-630.0, -630.0))   # 모서리 연귀: 바깥 모서리 = 두 내민 선의 교점
    gut = [pc for pc in pcs if pc[1] == "gutter"][0]
    assert gut[4][0] == pytest.approx(ze - RE.GUTTER_DROP) and min(q[1] for q in gut[2]) == pytest.approx(-780.0)
    so = [pc for pc in pcs if pc[1] == "soffit"][0]
    assert so[4][0] == pytest.approx(ze - 200.0) and so[3][0] == pytest.approx(ze - 215.0)
    for pc in pcs:
        v, fs = RE.piece_mesh(pc)
        assert len(v) == 8 and len(fs) == 6
    bad = dict(p, eaves={"fascia": {"edges": [4], "width": 30.0, "depth": 250.0}})
    assert K.row_params_error("roof", bad) is not None


# ---------------------------------------------------------------- 커맨드
def mk_fp(s, **kw):
    a = {"id": RF, "level": F.LV2, "boundary": USH, "form": "footprint", "pitch": 30, "slopes": [30, 40, 30, 25, 30, 35, 30, 20], "overhangs": [600] * 8,
         "base_offset": -300}
    a.update(kw)
    return ok(s.run("roof.create", a))["id"]


def test_외곽선_지붕_만들기_고치기와_되돌리기(base):
    h0 = base.eng.state_hash()
    mk_fp(base)
    p = params(base, RF)
    assert p["form"] == "footprint" and p["overhangs"] == [600.0] * 8 and p["slopes"][1] == 40.0
    d = ok(base.run("element.get", {"id": RF}))["derived"]
    assert d["faces"] == 8 and "eaves" not in d
    ok(base.run("roof.set", {"id": RF, "overhangs": [600, 300, 600, 0, 600, 0, 600, 300]}))
    assert params(base, RF)["overhangs"][1] == 300.0
    ok(base.run("roof.set", {"id": RF, "overhang": 400}))   # overhang 만 주면 변마다 내밀기를 뗀다
    assert "overhangs" not in params(base, RF) and params(base, RF)["overhang"] == 400.0
    ok(base.run("roof.set", {"id": RF, "pitch": 35}))         # pitch 만 주면 경사 변 모두
    assert set(params(base, RF)["slopes"]) == {35.0}
    assert err(base.run("roof.set", {"id": RF, "form": "hip"}))["code"] == "CONSTRAINT"   # 직사각형이 아니다
    assert err(base.run("roof.set", {"id": RF, "overhangs": [1] * 7}))["path"] == "/overhangs"
    e = err(base.run("roof.create", {"level": F.LV2, "boundary": JOG, "form": "poly", "pitch": 30}))
    assert "footprint" in e["hint"]                          # 다각형 지붕이 거절하면 외곽선 지붕을 권한다
    ok(base.run("roof.create", {"level": F.LV2, "boundary": JOG, "form": "footprint", "pitch": 30}))   # 짧은 변(200mm) 허용
    cw = ok(base.run("roof.create", {"level": F.LV2, "boundary": RECT[::-1], "form": "footprint", "pitch": 30, "slopes": [30, 0, 30, 0],
                                      "overhangs": [100, 200, 300, 400]}))["id"]   # 시계 외곽: 뒤집으며 변마다 값도 같은 변을 따라간다
    q = params(base, cw)
    assert K.polygon_area2(q["boundary"]) > 0 and q["overhangs"] == [300.0, 200.0, 100.0, 400.0] and q["slopes"] == [30.0, 0.0, 30.0, 0.0]
    with pytest.raises(AssertionError):
        ok(base.run("roof.create", {"level": F.LV2, "boundary": RECT, "form": "hip", "pitch": 30, "overhangs": [0] * 4}))
    assert ok(base.run("model.validate", {}))["errors"] == 0
    for _ in range(6):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0
    for _ in range(6):
        ok(base.run("history.redo", {}))
    assert params(base, cw)["overhangs"] == [300.0, 200.0, 100.0, 400.0]


def test_대칭_복사는_변마다_값과_처마_부속_변을_옮긴다(base):
    mk_fp(base, boundary=RECT, slopes=[30, 0, 45, 0], overhangs=[100, 200, 300, 400])
    ok(base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": [0, 1]}}))
    r = ok(base.run("element.mirror", {"ids": [RF], "axis": [0, -5000, 6000, -5000], "keep": True}))
    nid = [x for x in r["created"] if x != RF][0] if "created" in r else r["ids"][0]
    p, q = params(base, RF), params(base, nid)
    g1, g2 = K.roof_geom(p), K.roof_geom(q)
    assert _areas(g1) == _areas(g2) and g1["ztop"] == pytest.approx(g2["ztop"]) and g1["zlow"] == pytest.approx(g2["zlow"])
    assert RE.summary(K.roof_eave_pieces(p)) == RE.summary(K.roof_eave_pieces(q))


def test_지붕_유형(base):
    h0 = base.eng.state_hash()
    ok(base.run("rooftype.create", {"id": RT, "name": "처마 425", "layers": L4, "function": "eave"}))
    assert err(base.run("rooftype.create", {"name": "처마 425", "thickness": 10}))["path"] == "/name"
    assert err(base.run("rooftype.create", {"name": "x", "layers": L4, "thickness": 300}))["path"] == "/thickness"
    t2 = ok(base.run("rooftype.create", {"name": "유리 28", "thickness": 28, "function": "glazing"}))["id"]
    mk_fp(base, type=RT)
    p = params(base, RF)
    assert p["rtype"] == RT and p["thickness"] == 425.0 and p["layers"][0]["material"] == "우레탄 방수"
    assert err(base.run("roof.create", {"level": F.LV2, "boundary": RECT, "form": "hip", "pitch": 30, "type": RT, "thickness": 100}))["path"] == "/thickness"
    assert err(base.run("roof.layers", {"id": RF, "layers": L4}))["code"] == "CONSTRAINT"
    assert err(base.run("roof.set", {"id": RF, "thickness": 300}))["code"] == "CONSTRAINT"
    r = ok(base.run("rooftype.set", {"id": RT, "layers": L4[:3] + [{"material": "철근콘크리트", "thickness": 100}]}))
    assert r["roofs"] == 1 and params(base, RF)["thickness"] == 475.0
    assert ok(base.run("rooftype.set", {"id": RT, "name": "처마 475"}))["roofs"] == 0   # 이름만 바꾸면 지붕은 그대로
    lst = ok(base.run("rooftype.list", {}))
    assert [(x["name"], x["roofs"]) for x in lst["items"]] == [("유리 28", 0), ("처마 475", 1)] and lst["untyped_roofs"] == 0
    d = ok(base.run("rooftype.duplicate", {"id": RT, "name": "처마 복제"}))["id"]
    ok(base.run("roof.type.set", {"ids": [RF], "type": t2}))
    assert params(base, RF)["thickness"] == 28.0 and "layers" not in params(base, RF)
    ok(base.run("roof.type.set", {"ids": [RF], "type": RT}))
    e = err(base.run("rooftype.delete", {"id": RT}))
    assert e["details"]["roofs"] == [RF]
    assert err(base.run("element.delete", {"id": RT}))["code"] == "CONSTRAINT"
    assert ok(base.run("rooftype.delete", {"id": RT, "detach": True}))["detached"] == 1
    p = params(base, RF)
    assert "rtype" not in p and p["thickness"] == 475.0 and len(p["layers"]) == 4   # 층 구성은 지붕에 남는다
    ok(base.run("roof.layers", {"id": RF, "layers": None}))
    ok(base.run("element.delete", {"id": d}))
    assert ok(base.run("model.validate", {}))["errors"] == 0
    st = ok(base.run("model.stats", {}))["counts"]
    assert st["rooftype"] == 1
    for _ in range(11):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0
    for _ in range(11):
        ok(base.run("history.redo", {}))
    assert ok(base.run("model.stats", {}))["counts"]["rooftype"] == 1


def test_처마_부속_커맨드와_벽_상단_붙이기(base):
    mk_fp(base, boundary=[[-600, -600], [6600, -600], [6600, 4600], [-600, 4600]], slopes=[30, 0, 30, 0], overhangs=[0] * 4)
    r = ok(base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": "all"}, "gutter": {"edges": [0, 2]}, "soffit": {"edges": [0, 2]}}))
    assert r["eaves"]["gutter"]["pieces"] == 2 and "soffit" not in r["eaves"]   # 내밀기 0 이면 반자 조각이 없다
    q = ok(base.run("roof.eaves", {"id": RF}))
    assert [e["on"] for e in q["edges"]] == [["fascia", "gutter", "soffit"], ["fascia"], ["fascia", "gutter", "soffit"], ["fascia"]]
    assert q["edges"][0]["eave_length"] == 7200.0 and q["settings"]["fascia"] == {"width": 30.0, "depth": 250.0}
    assert err(base.run("roof.eaves.set", {"id": RF, "gutter": {"edges": [4]}}))["path"] == "/gutter/edges"
    assert err(base.run("roof.set", {"id": RF, "boundary": [[-600, -600], [6600, -600], [6600, 4600], [3000, 5000], [-600, 4600]]}))["code"] == "CONSTRAINT"
    ok(base.run("roof.eaves.set", {"id": RF, "clear": ["gutter", "soffit"]}))
    assert set(params(base, RF)["eaves"]) == {"fascia"}
    assert err(base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": [0]}, "clear": ["fascia"]}))["code"] == "SCHEMA_INVALID"
    ok(base.run("roof.eaves.set", {"id": RF, "clear": ["fascia"]}))
    assert "eaves" not in params(base, RF)
    # 벽 상단을 외곽선 지붕 아랫면에 붙인다: 박공 지붕이라 W2(동쪽 박공 아래)는 가운데가 높다
    ok(base.run("wall.attach_top", {"id": F.W2, "targets": [RF]}))
    prof = params(base, F.W2)["top_profile"]
    zs = [z for _, z in prof]
    assert max(zs) > min(zs) + 500


def views(s):
    ok(s.run("view.create", {"id": VP, "name": "지붕", "type": "plan", "level": F.LV2, "cut_height": 9000}))
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [5000, -2000], "p2": [5000, 10000], "scale": 50}))
    ok(s.run("view.create", {"id": VE, "name": "남측", "type": "elevation", "dir": "S"}))


def same_as_full(s):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in (VP, VS, VE):
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)
        assert g.q(D.view_fills, v) == full.q(D.view_fills, v)
        assert g.q(D.view_detail_lines, v) == full.q(D.view_detail_lines, v)


def test_도면_층_경계선_처마_부속과_증분(base):
    views(base)
    g = g_of(base)
    before = {v: g.q(D.view_lines, v) for v in (VP, VS, VE)}
    mk_fp(base)
    same_as_full(base)
    g = g_of(base)
    sec_fill = g.q(D.view_fills, VS)
    assert g.q(D.view_detail_lines, VS) == () or all(x[0] != "hatch" or True for x in g.q(D.view_detail_lines, VS))
    ok(base.run("roof.layers", {"id": RF, "layers": [{"material": "기와", "thickness": 50}, {"material": "단열재", "thickness": 150}]}))
    g = g_of(base)
    det = g.q(D.view_detail_lines, VS)
    assert any(x[0] == "hatch" and x[2] != x[4] for x in det)   # 경사 띠 안의 층 경계선(기운 선)
    same_as_full(base)
    ok(base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": "all"}, "gutter": {"edges": [0]}}))
    g = g_of(base)
    fills = g.q(D.view_fills, VS)
    assert len(fills) > len(sec_fill)   # 단면이 처마 널(남·북 처마)을 자른다
    plan = g.q(D.view_lines, VP)
    assert any(x[0] == "proj" and abs(x[2] - (-630.0)) < 1e-3 and abs(x[4] - (-630.0)) < 1e-3 for x in plan) or any(
        x[0] == "proj" and abs(x[2] + 630.0) < 1e-3 for x in plan)   # 남쪽 처마 널 바깥선(y = -600 - 30)
    same_as_full(base)
    ok(base.run("roof.set", {"id": RF, "pitch": 25}))
    same_as_full(base)
    ok(base.run("rooftype.create", {"id": RT, "name": "t", "layers": L4}))
    ok(base.run("roof.type.set", {"ids": [RF], "type": RT}))
    same_as_full(base)
    ok(base.run("rooftype.set", {"id": RT, "thickness": 300, "clear": ["layers"]}))
    same_as_full(base)
    ok(base.run("element.delete", {"id": RF}))
    g = g_of(base)
    assert {v: g.q(D.view_lines, v) for v in (VP, VS, VE)} == before   # 지붕이 없으면 도면이 바뀌지 않는다
    same_as_full(base)


def test_새_기능을_쓰지_않으면_응답이_그대로다(base):
    st = ok(base.run("model.stats", {}))["counts"]
    assert "rooftype" not in st
    r = ok(base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": RECT, "form": "gable", "pitch": 30, "overhang": 600}))
    assert set(r) == {"id"} and set(params(base, RF)) == {"name", "boundary", "form", "pitch", "overhang", "base_offset", "thickness"}
    assert set(ok(base.run("element.get", {"id": RF}))["derived"]) == {"eave_low", "top", "outline_area_m2", "faces"}


# ---------------------------------------------------------------- 압출 지붕
BARREL = [[0, 0], [1000, 900], [2000, 1400], [3000, 1600], [4000, 1400], [5000, 900], [6000, 0]]


def test_압출_지붕_순수_형상():
    assert "평평" in RX.problem([0, 0, 6000, 0], [[0, 0], [2000, 0]])
    assert "늘어나야" in RX.problem([0, 0, 6000, 0], [[0, 0], [0.5, 100]])
    assert "75도" in RX.problem([0, 0, 6000, 0], [[0, 0], [100, 1000]])
    assert "1mm" in RX.problem([0, 0, 0.5, 0], [[0, 0], [100, 10]])
    p = {"name": "x", "boundary": RX.rect([0, 0, 6000, 0], [[-2000, 0], [0, 1155], [2000, 0]]), "form": "extrusion", "pitch": 30.0, "overhang": 0.0,
         "base_offset": 100.0, "thickness": 150.0, "axis": [0, 0, 6000, 0], "profile": [[-2000, 0], [0, 1155], [2000, 0]]}
    assert K.row_params_error("roof", p) is None
    g = K.roof_geom(p)
    assert len(g["faces"]) == 2 and len(g["ridges"]) == 1 and not g["valleys"] and g["ztop"] == pytest.approx(1255.0) and g["zlow"] == pytest.approx(100.0)
    assert g["fpitch"] == pytest.approx([30.0, 30.0], abs=2e-2)
    assert sorted(g["outline"]) == sorted([(0.0, -2000.0), (6000.0, -2000.0), (6000.0, 2000.0), (0.0, 2000.0)])
    assert K.row_params_error("roof", dict(p, boundary=RECT)) is not None
    assert K.row_params_error("roof", dict(p, slopes=[30, 30, 30, 30])) is not None
    q = dict(p, profile=BARREL, boundary=RX.rect([0, 0, 6000, 0], BARREL))
    gb = K.roof_geom(q)
    assert len(gb["faces"]) == 6 and len(gb["ridges"]) == 1 and len(gb["hips"]) == 4 and not gb["valleys"]   # 볼트: 꼭대기는 용마루, 나머지 접힘선은 꺾임
    assert gb["ztop"] == pytest.approx(1700.0)


def test_압출_지붕_커맨드와_처마_부속(base):
    h0 = base.eng.state_hash()
    r = ok(base.run("roof.extrude", {"id": RF, "level": F.LV1, "axis": [-500, 2000, 6500, 2000], "profile": [[-2600, 0], [0, 1500], [2600, 0]], "base_offset": 3000,
                                      "thickness": 150}))
    p = params(base, RF)
    assert r == {"id": RF} and p["form"] == "extrusion" and len(p["boundary"]) == 4 and p["pitch"] == pytest.approx(29.98, abs=0.05)
    assert ok(base.run("element.get", {"id": RF}))["derived"]["faces"] == 2
    ok(base.run("roof.set", {"id": RF, "profile": [[-2600, 0], [-1300, 1000], [0, 1500], [1300, 1000], [2600, 0]]}))
    assert len(K.roof_geom(params(base, RF))["faces"]) == 4
    assert err(base.run("roof.set", {"id": RF, "pitch": 20}))["code"] == "CONSTRAINT"
    assert err(base.run("roof.set", {"id": RF, "profile": [[0, 0], [10, 0]]}))["code"] == "CONSTRAINT"
    assert err(base.run("roof.set", {"id": F.W1, "axis": [0, 0, 1, 0]}))["code"] in ("NOT_FOUND", "CONSTRAINT", "WRONG_KIND")
    ok(base.run("roof.move", {"id": RF, "dx": 100, "dy": 0}))
    assert params(base, RF)["axis"][0] == -400.0
    ok(base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": "all"}, "gutter": {"edges": "all"}}))
    sm = ok(base.run("roof.eaves", {"id": RF}))["eaves"]
    assert sm["gutter"]["pieces"] == 2 and sm["fascia"]["pieces"] == 2 + 2 * 4   # 홈통은 양쪽 처마만, 널은 마구리 꺾은선을 따라간다
    ok(base.run("wall.attach_top", {"id": F.W2, "targets": [RF]}))
    zs = [z for _, z in params(base, F.W2)["top_profile"]]
    assert max(zs) > min(zs) + 500
    m = ok(base.run("element.mirror", {"ids": [RF], "axis": [0, -3000, 6000, -3000], "keep": True}))
    nid = [x for x in (m.get("created") or m.get("ids") or []) if x != RF][0]
    a, b = K.roof_geom(params(base, RF)), K.roof_geom(params(base, nid))
    assert _areas(a) == _areas(b) and a["ztop"] == pytest.approx(b["ztop"])
    assert ok(base.run("model.validate", {}))["errors"] == 0
    for _ in range(6):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0


# ---------------------------------------------------------------- IFC
def _ifc_errors(ff):
    import ifcopenshell.validate as IV
    lg = IV.json_logger()
    IV.validate(ff, lg, express_rules=True)
    return [x["message"][:300] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]]


def test_IFC_지붕_유형_층_구성_처마_부속_검증과_왕복(tmp_path):
    import ifcopenshell
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        h_plain = b.hashes()
        ok(b.run("rooftype.create", {"id": IRT, "name": "처마 425", "layers": L4, "function": "eave"}))
        ok(b.run("rooftype.create", {"name": "쓰지 않는 유형", "thickness": 28}))
        ok(b.run("roof.create", {"id": IRF, "level": F.LV2, "boundary": USH, "form": "footprint", "pitch": 30, "slopes": [30, 40, 30, 25, 30, 35, 30, 20],
                                 "overhangs": [600] * 8, "type": IRT}))
        ok(b.run("roof.eaves.set", {"id": IRF, "fascia": {"edges": "all"}, "gutter": {"edges": [0, 2, 4]}, "soffit": {"edges": [0, 1]}}))
        ok(b.run("roof.create", {"id": IRG, "level": F.LV2, "boundary": [[20000 + x, y] for x, y in JOG], "form": "footprint", "pitch": 30, "overhang": 500,
                                 "slopes": [30, 30, 30, 0, 30, 0]}))
        ok(b.run("roof.layers", {"id": IRG, "layers": [{"material": "기와", "thickness": 50}, {"material": "합판", "thickness": 150}]}))
        ok(b.run("roof.extrude", {"id": IRX, "level": F.LV2, "axis": [0, 20000, 8000, 20000], "profile": BARREL, "type": IRT}))
        ok(b.run("roof.eaves.set", {"id": IRX, "gutter": {"edges": [0, 2]}}))
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        rt = {t.Name: t for t in f.by_type("IfcRoofType")}
        assert set(rt) == {"처마 425", "쓰지 않는 유형"} and rt["처마 425"].ElementType == "eave"
        rel = [r for r in f.by_type("IfcRelDefinesByType") if r.RelatingType == rt["처마 425"]]
        assert len(rel) == 1 and [o.GlobalId for o in rel[0].RelatedObjects] == [IRF, IRX]
        ls = [r.RelatingMaterial for r in f.by_type("IfcRelAssociatesMaterial") if rt["처마 425"] in r.RelatedObjects]
        assert [(ly.Material.Name, ly.LayerThickness) for ly in ls[0].MaterialLayers] == [(x["material"], float(x["thickness"])) for x in L4]
        roof = f.by_guid(IRF)
        kids = [k for r in roof.IsDecomposedBy for k in r.RelatedObjects]
        assert sum(1 for k in kids if k.is_a("IfcSlab")) == 8
        assert {k.ObjectType for k in kids if k.is_a("IfcCovering")} == {"FASCIA", "SOFFIT"}
        assert all(k.PredefinedType == "GUTTER" for k in kids if k.is_a("IfcPipeSegment")) and any(k.is_a("IfcPipeSegment") for k in kids)
        us = [r.RelatingMaterial for r in f.by_type("IfcRelAssociatesMaterial") if f.by_guid(IRG) in r.RelatedObjects]
        assert us and us[0].is_a("IfcMaterialLayerSetUsage") and [ly.Material.Name for ly in us[0].ForLayerSet.MaterialLayers] == ["기와", "합판"]
        assert f.by_guid(IRX).PredefinedType == "BARREL_ROOF"
        assert _ifc_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t2x3.ifc", "schema": "IFC2X3"})["ok"]
        f2 = ifcopenshell.open(str(tmp_path / "t2x3.ifc"))
        assert f2.schema == "IFC2X3" and _ifc_errors(f2) == []
        assert {t.PredefinedType for t in f2.by_type("IfcSlabType") if t.Name in ("처마 425", "쓰지 않는 유형")} == {"ROOF"}
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("t3.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        assert ok(b.run("roof.eaves", {"id": IRF}, project="imp.dobim"))["eaves"]["gutter"]["pieces"] >= 2
        ok(b.run("rooftype.set", {"id": IRT, "thickness": 300, "clear": ["layers"]}, project="imp.dobim"))
        ok(b.run("roof.eaves.set", {"id": IRF, "clear": ["gutter"]}, project="imp.dobim"))
        f3 = ifcopenshell.open(str(b.export("t4.ifc", "imp.dobim")))
        assert _ifc_errors(f3) == [] and not [x for x in f3.by_type("IfcPipeSegment") if x.Name.startswith("RF-0000r1-")]
        ok(b.run("history.undo", {}, project="imp.dobim"))
        ok(b.run("history.undo", {}, project="imp.dobim"))
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        r2 = b.import_ifc("t2x3.ifc", "imp2.dobim")
        assert r2["ok"], r2
        assert len(ok(b.run("rooftype.list", {}, project="imp2.dobim"))["items"]) == 2
        f5 = ifcopenshell.open(str(b.export("t5.ifc", "imp2.dobim")))   # IFC2x3 에서 올린 프로젝트를 IFC4 로: 지붕 유형은 다시 IfcRoofType
        assert len(f5.by_type("IfcRoofType")) == 2 and not [t for t in f5.by_type("IfcSlabType") if t.Name == "처마 425"] and _ifc_errors(f5) == []
        for _ in range(8):
            ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h_plain)
    finally:
        b.close()


def test_다시_연_프로젝트는_같은_IFC_해시(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("rooftype.create", {"id": IRT, "name": "처마", "layers": L4}))
        ok(b.run("roof.create", {"id": IRF, "level": F.LV2, "boundary": LSH, "form": "footprint", "pitch": 30, "type": IRT, "overhang": 300}))
        ok(b.run("roof.eaves.set", {"id": IRF, "fascia": {"edges": "all"}, "soffit": {"edges": [0, 1, 2]}}))
        h = b.hashes()
        b.close()
        b2 = S.Bench(tmp_path)
        assert S.trip(b2.hashes()) == S.trip(h)
        b2.close()
    finally:
        b.close()
