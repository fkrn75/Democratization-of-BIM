# SPDX-License-Identifier: MPL-2.0
"""W2 스물두 번째 PR: 평지붕 후속 — 파라펫(roof.parapet.set)과 지붕 배수(배수 물매·배수구, roof.drainage.set).

1. 재현: 이전 평지붕은 물매 0 수평 윗면뿐이었다. 파라펫·배수 명령이 없었고(알 수 없는 커맨드), 파라펫은 지붕 슬래브(use=roof)에만 있었다.
2. 파라펫: 변 선택(전부·번호)·마이터 모서리(겹침 없음)·지붕 윗면 위에 앉음·원호 변·거절, 지붕을 고치면 따라간다
3. 배수: 배수 물매(상부면 기울기: 윗면이 한 방향으로 기운 평면)·배수구 점. 물매 0 기본은 그대로
4. 도면(평면·단면·입면)·수량·IFC(IfcWall PARAPET·IfcWasteTerminal ROOFDRAIN)·왕복·하위 호환
합성 데이터만 쓴다."""
import math
import sys

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.shape as US
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
VP = "4" + "0" * 19 + "p1"     # 평면(절단 1200: 파라펫 900 위)
VC = "4" + "0" * 19 + "p2"     # 평면(절단 500: 파라펫 안)
VS = "4" + "0" * 19 + "s1"     # x = 3000 단면
VE = "4" + "0" * 19 + "e1"     # 남측 입면
BOX = [[0, 0], [8000, 0], [8000, 6000], [0, 6000]]
L_SHAPE = [[0, 0], [8000, 0], [8000, 3000], [3000, 3000], [3000, 6000], [0, 6000]]
Q45 = math.tan(math.radians(90 / 4))
BAND = [[10000, 0], [20000, 0], [0, 20000], [0, 10000]]   # 사분 고리(바깥 R20000·안 R10000): 원호 변 둘


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


def pcs(s, eid=RF):
    return K.roof_parapet_pieces(params(s, eid))


def flat(s, boundary=BOX, **kw):
    return ok(s.run("roof.create", dict({"id": RF, "level": F.LV2, "boundary": boundary, "form": "flat", "thickness": 200}, **kw)))


def parapet(s, **kw):
    return ok(s.run("roof.parapet.set", dict({"id": RF, "height": 900, "thickness": 200}, **kw)))


def area2(q):
    return K.polygon_area2([list(p) for p in q])


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [x["message"][:160] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def views(s):
    ok(s.run("view.create", {"id": VP, "name": "지붕", "type": "plan", "level": F.LV2, "cut_height": 1200}))
    ok(s.run("view.create", {"id": VC, "name": "지붕 절단", "type": "plan", "level": F.LV2, "cut_height": 500}))
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [3000, -1500], "p2": [3000, 7500]}))
    ok(s.run("view.create", {"id": VE, "name": "남", "type": "elevation", "dir": "S"}))


def same_as_full(s, views=(VP, VC, VS, VE)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)


def top_z(s):
    return g_of(s).q(D.roof_info, RF)["ztop"]


# ---------------------------------------------------------------- 1. 재현과 기본 동작
def test_1_파라펫_기본_변_선택_거절(base):
    flat(base)
    p0 = params(base, RF)
    assert "parapet" not in p0 and "fall" not in p0 and "drains" not in p0                        # 이전과 같은 행(새 키 없음)
    assert ok(base.run("roof.parapet", {"id": RF})) == {"id": RF, "parapet": None}
    r = parapet(base)
    assert r["changed"] and r["parapet"]["pieces"] == 4 and r["parapet"]["length"] == 28000.0
    assert params(base, RF)["parapet"] == {"height": 900.0, "thickness": 200.0, "edges": [0, 1, 2, 3]}
    ps = pcs(base)
    assert [pc[0] for pc in ps] == ["parapet:0:0", "parapet:1:1", "parapet:2:2", "parapet:3:3"] and {pc[1] for pc in ps} == {"parapet"}
    for _lb, _kd, q, zb, zt in ps:   # 지붕 윗면(0)에 앉고 높이 900
        assert zb == (0.0,) * 4 and zt == (900.0,) * 4 and area2(q) > 0
    # 모서리는 맞물린다: 네 조각 넓이 합 = 바깥 둘레와 안쪽 고리 사이(겹침 없음), 이웃 조각이 모서리 면을 공유한다
    assert sum(area2(pc[2]) for pc in ps) / 2.0 == pytest.approx(8000 * 6000 - 7600 * 5600)
    for i in range(4):
        a, b = ps[i], ps[(i + 1) % 4]
        assert a[2][2] == b[2][1] and a[2][3] == b[2][0]            # 끝 바깥 점·안쪽 점 = 다음 조각의 시작 바깥 점·안쪽 점
    # 변 선택: 번호를 주면 그 변만, 끝은 변에 수직(맞닿은 이웃이 없으면)
    r = parapet(base, edges=[0, 2], height=1100)
    assert r["parapet"]["pieces"] == 2 and r["parapet"]["height"] == 1100.0 and params(base, RF)["parapet"]["edges"] == [0, 2]
    assert [pc[2] for pc in pcs(base)][0] == ((0.0, 200.0), (0.0, 0.0), (8000.0, 0.0), (8000.0, 200.0))
    assert ok(base.run("roof.parapet", {"id": RF}))["edges"] == [
        {"edge": 0, "pieces": 1, "length": 8000.0}, {"edge": 2, "pieces": 1, "length": 8000.0}]
    assert ok(base.run("element.get", {"id": RF}))["derived"]["parapet"]["pieces"] == 2
    assert not ok(base.run("roof.parapet.set", {"id": RF, "height": 1100.0}))["changed"]         # 같은 값은 바뀌지 않는다
    # 두께·높이만 바꾸면 변은 그대로
    parapet(base, thickness=300)
    assert params(base, RF)["parapet"] == {"height": 900.0, "thickness": 300.0, "edges": [0, 2]}
    ok(base.run("roof.parapet.set", {"id": RF, "clear": True}))
    assert "parapet" not in params(base, RF) and pcs(base) == []
    # 거절
    assert err(base.run("roof.parapet.set", {"id": RF, "thickness": 200}))["code"] == "CONSTRAINT"                     # 높이 필요
    assert err(base.run("roof.parapet.set", {"id": RF, "height": 900, "edges": [4]}))["code"] == "CONSTRAINT"           # 변 번호 범위
    assert err(base.run("roof.parapet.set", {"id": RF, "height": 900, "edges": [1, 1]}))["code"] == "CONSTRAINT"
    assert err(base.run("roof.parapet.set", {"id": RF, "height": 4000}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("roof.parapet.set", {"id": RF, "clear": True, "height": 900}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("roof.parapet.set", {"id": RF, "height": 900, "thickness": 1100}))["code"] == "SCHEMA_INVALID"   # 두께 상한 1000
    ok(base.run("roof.parapet.set", {"id": RF, "height": 900, "thickness": 900}))                                         # 8000 x 6000 에서 두께 900 은 된다
    ok(base.run("roof.parapet.set", {"id": RF, "clear": True}))
    gable = F.RF1[:-1] + "9"
    ok(base.run("roof.create", {"id": gable, "level": F.LV2, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "form": "gable", "pitch": 30}))
    assert err(base.run("roof.parapet.set", {"id": gable, "height": 900}))["code"] == "CONSTRAINT"                    # 경사 지붕은 안 된다
    assert err(base.run("roof.drainage.set", {"id": gable, "fall": {"dir": 0, "pct": 2}}))["code"] == "CONSTRAINT"


def test_1_두꺼운_파라펫은_이유와_함께_거절한다(base):
    flat(base, [[0, 0], [8000, 0], [8000, 1000], [0, 1000]])
    e = err(base.run("roof.parapet.set", {"id": RF, "height": 900, "thickness": 600}))                                  # 1000 폭에 600 두께 둘이 겹친다
    assert e["code"] == "CONSTRAINT" and "겹" in e["message"] or "너무" in e["message"] or "접" in e["message"]
    assert "parapet" not in params(base, RF)
    parapet(base, thickness=400)                                                                                         # 400 + 400 < 1000: 된다
    assert len(pcs(base)) == 4
    assert err(base.run("roof.set", {"id": RF, "boundary": [[0, 0], [8000, 0], [8000, 700], [0, 700]]}))["code"] == "CONSTRAINT"   # 고치면 겹쳐서 거절


# ---------------------------------------------------------------- 2. 형상: 오목·마이터·원호
def test_2_오목_외곽_마이터는_슬래브_파라펫_고리와_같은_넓이(base):
    flat(base, L_SHAPE)
    parapet(base)
    ps = pcs(base)
    assert len(ps) == 6
    ring = K.parapet_ring([list(q) for q in L_SHAPE], 200.0)                       # 지붕 슬래브 파라펫의 안쪽 고리(같은 마이터 규칙)
    assert sum(area2(pc[2]) for pc in ps) / 2.0 == pytest.approx((abs(area2(L_SHAPE)) - abs(area2(ring))) / 2.0)
    # 조각끼리 겹치지 않는다(어느 조각의 무게중심도 다른 조각 안이 아니다)
    from dobim_core import roofparapet as RPR
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            assert not RPR._overlap(a[2], b[2])
    inner = {tuple(round(v, 6) for v in pc[2][0]) for pc in ps}                      # 안쪽 시작점들이 고리의 점이다
    assert inner == {tuple(round(v, 6) for v in q) for q in ring}


def test_2_일부_변만_고르면_끝이_변에_수직이다(base):
    flat(base, L_SHAPE)
    parapet(base, edges=[1, 2])                                                      # 변 1(x=8000 세로 3000), 변 2(y=3000 가로 5000): 볼록 꼭짓점 (8000, 3000)에서 만난다
    ps = pcs(base)
    assert len(ps) == 2 and all(area2(pc[2]) > 0 for pc in ps)
    assert ps[0][2][2] == ps[1][2][1] and ps[0][2][3] == ps[1][2][0] == (7800.0, 2800.0)   # 만나는 모서리는 마이터(같은 면을 공유)
    assert ps[0][2][0] == (7800.0, 0.0 + 200.0) or ps[0][2][0][0] == 7800.0                 # 맞닿는 이웃이 없는 시작은 변에 수직
    parapet(base, edges=[0, 3])                                                      # 이웃하지 않은 두 변: 끝이 변에 수직이다
    ps = pcs(base)
    assert [pc[2] for pc in ps] == [((0.0, 200.0), (0.0, 0.0), (8000.0, 0.0), (8000.0, 200.0)),
                                    ((2800.0, 3000.0), (3000.0, 3000.0), (3000.0, 6000.0), (2800.0, 6000.0))]


def test_2_원호_변은_그_호의_모든_현에_파라펫이_선다(base):
    flat(base, BAND, bulges=[0, Q45, 0, -Q45])
    parapet(base, thickness=300)
    p = params(base, RF)
    assert p["parapet"]["edges"] == [0, 1, 2, 3]
    ps = pcs(base)
    gm = g_of(base).q(Q.roof_gm, RF)
    assert len(ps) == len(gm["outline"]) == 4 + 7 + 7 or len(ps) == len(gm["outline"])
    assert sum(area2(pc[2]) for pc in ps) > 0
    ok(base.run("roof.parapet.set", {"id": RF, "edges": [1]}))                      # 호 변 하나: 그 호의 현마다 한 조각
    ps = pcs(base)
    assert len(ps) == sum(1 for s in gm["eave_src"] if s == 1) and {pc[0].split(":")[1] for pc in ps} == {"1"}
    q = ok(base.run("roof.parapet", {"id": RF}))
    assert q["edges"][0]["pieces"] == len(ps)
    # 호 길이(현으로 펼친)가 바깥 면 길이다
    assert q["total"]["length"] == pytest.approx(sum(math.dist(pc[2][1], pc[2][2]) for pc in ps), abs=1e-3)


# ---------------------------------------------------------------- 3. 지붕을 고치면 따라간다
def test_3_지붕을_고치면_파라펫이_따라간다(base):
    flat(base)
    parapet(base, edges=[0, 3])
    h0 = base.eng.state_hash()
    before = [pc[2] for pc in pcs(base)]
    ok(base.run("roof.move", {"id": RF, "dx": 1000, "dy": -500}))
    assert [pc[2] for pc in pcs(base)] == [tuple((x + 1000.0, y - 500.0) for x, y in q) for q in before]
    ok(base.run("roof.set", {"id": RF, "boundary": [[0, 0], [9000, 0], [9000, 6000], [0, 6000]]}))          # 외곽 변경: 같은 변 번호에 맞게 다시 계산
    assert pcs(base)[0][2][2] == (9000.0, 0.0) and len(pcs(base)) == 2
    ok(base.run("roof.set", {"id": RF, "base_offset": 500, "overhang": 300}))                                # 높이·내밀기: 바닥이 올라가고 바깥 면이 처마 윤곽을 따른다
    ps = pcs(base)
    assert ps[0][3] == (500.0,) * 4 and ps[0][4] == (1400.0,) * 4 and ps[0][2][1] == (-300.0, -300.0)
    assert err(base.run("roof.set", {"id": RF, "boundary": [[0, 0], [9000, 0], [9000, 6000], [5000, 7000], [0, 6000]]}))["code"] == "CONSTRAINT"   # 꼭짓점 수가 바뀌면 변 번호가 어긋난다
    ok(base.run("roof.set", {"id": RF, "thickness": 300}))
    assert pcs(base)[0][3] == (500.0,) * 4                                                                    # 두께는 아래로라 파라펫은 그대로
    ok(base.run("roof.parapet.set", {"id": RF, "clear": True}))
    ok(base.run("roof.set", {"id": RF, "boundary": [[0, 0], [9000, 0], [9000, 6000], [5000, 7000], [0, 6000]]}))   # 떼면 꼭짓점 수를 바꿀 수 있다
    for _ in range(6):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0
    ok(base.run("history.redo", {}))
    base.close()


def test_3_이동_회전_대칭_복사는_파라펫_변을_따라간다(base):
    flat(base, L_SHAPE)
    parapet(base, edges=[0, 1])
    a0 = [pc[2] for pc in pcs(base)]
    ok(base.run("element.rotate", {"ids": [RF], "center": [0, 0], "angle": 90}))
    rot = [pc[2] for pc in pcs(base)]
    assert [v for p in rot[0] for v in p] == pytest.approx([-200.0, 0.0, 0.0, 0.0, 0.0, 8000.0, -200.0, 7800.0], abs=1e-6)       # (x, y) -> (-y, x)
    assert sum(area2(q) for q in rot) == pytest.approx(sum(area2(q) for q in a0))
    ok(base.run("history.undo", {}))
    ok(base.run("element.mirror", {"ids": [RF], "axis": [0, 0, 0, 1000]}))                  # 대칭: 같은 변(조각 위치가 거울상)
    m = [pc[2] for pc in pcs(base)]
    assert sum(area2(q) for q in m) == pytest.approx(sum(area2(q) for q in a0))
    xs = sorted(min(p[0] for p in q) for q in m)
    assert xs == pytest.approx(sorted(-max(p[0] for p in q) for q in a0))
    ok(base.run("history.undo", {}))
    n = ok(base.run("element.copy", {"ids": [RF], "dx": 20000, "dy": 0}))
    new = n["ids"][0] if "ids" in n else n["new_ids"][0]
    assert params(base, new)["parapet"] == params(base, RF)["parapet"]
    assert [pc[2] for pc in pcs(base, new)] == [tuple((x + 20000.0, y) for x, y in q) for q in a0]


# ---------------------------------------------------------------- 4. 배수: 물매·배수구
def test_4_배수_물매는_윗면이_기운_평면_하나다(base):
    flat(base, base_offset=100)
    gm0 = g_of(base).q(Q.roof_gm, RF)
    assert gm0["planes"] == [(0.0, 0.0, 100.0)] and gm0["zlow"] == gm0["ztop"] == 100.0       # 물매 0 기본은 그대로
    r = ok(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 90, "pct": 2}}))
    assert r["changed"] and r["fall"] == {"dir": 90.0, "pct": 2.0} and r["drains"] == 0
    gm = g_of(base).q(Q.roof_gm, RF)
    a, b, c = gm["planes"][0]
    assert (a, b) == pytest.approx((0.0, -0.02)) and c == pytest.approx(100.0 + 0.02 * 6000)  # y 가 커질수록(흐름 방향) 낮아진다
    assert gm["zlow"] == pytest.approx(100.0) and gm["ztop"] == pytest.approx(100.0 + 0.02 * 6000)
    assert all(abs(q[2] - (a * q[0] + b * q[1] + c)) < 1e-9 for q in gm["faces"][0])           # 면의 모든 꼭짓점이 평면 위
    assert len(gm["faces"]) == 1 and gm["fpitch"] == [0.0] and gm["fall_pct"] == pytest.approx(2.0)
    assert params(base, RF)["pitch"] == 0.0 and params(base, RF)["form"] == "flat"            # 형식·pitch 는 그대로(물매 0 은 배수 물매와 별개)
    row = g_of(base).q(__import__("dobim_core.schedules", fromlist=["x"]).element_row, RF)
    assert row["area"] == pytest.approx(8000 * 6000 * math.sqrt(1 + 0.02 ** 2))                 # 일람표 면적은 경사면 넓이
    assert row["pitch"] == 0.0
    assert ok(base.run("element.get", {"id": RF}))["derived"]["fall_pct"] == 2.0
    # 바꾸기·거절·떼기
    ok(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 0, "pct": 5}}))
    assert g_of(base).q(Q.roof_gm, RF)["planes"][0][0] == pytest.approx(-0.05)
    assert err(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 0, "pct": 15}}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 0, "pct": 0}}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("roof.drainage.set", {"id": RF, "clear": ["fall"], "fall": {"dir": 0, "pct": 2}}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("roof.drainage.set", {"id": RF}))["code"] == "SCHEMA_INVALID"
    ok(base.run("roof.drainage.set", {"id": RF, "clear": ["fall"]}))
    assert "fall" not in params(base, RF) and g_of(base).q(Q.roof_gm, RF)["planes"] == [(0.0, 0.0, 100.0)]
    assert not ok(base.run("roof.drainage.set", {"id": RF, "clear": ["fall"]}))["changed"]


def test_4_파라펫은_기운_윗면_위에_같은_높이로_앉는다(base):
    flat(base)
    ok(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 90, "pct": 2}}))             # y=0 쪽이 높다(z 120), y=6000 쪽이 낮다(0)
    parapet(base, edges=[0, 2])
    ps = pcs(base)
    assert ps[0][3] == pytest.approx((116.0, 120.0, 120.0, 116.0)) and ps[0][4] == pytest.approx((1016.0, 1020.0, 1020.0, 1016.0))   # 안쪽 점은 두께만큼 낮다
    assert ps[1][3] == pytest.approx((4.0, 0.0, 0.0, 4.0)) and all(zt - zb == pytest.approx(900.0) for pc in ps for zb, zt in zip(pc[3], pc[4]))
    s = ok(base.run("roof.parapet", {"id": RF}))["total"]
    assert s["volume"] == pytest.approx(2 * 8000 * 200 * 900)                                  # 수직 기둥이라 부피는 평면 넓이 x 높이
    ok(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 0, "pct": 3}}))                # 물매를 바꾸면 따라간다
    assert pcs(base)[0][3] != ps[0][3]


def test_4_배수구_점은_지붕_안쪽_파라펫_밖이어야_한다(base):
    flat(base)
    r = ok(base.run("roof.drainage.set", {"id": RF, "drains": [[1000, 1000], [4000, 3000]], "drain_diameter": 110}))
    assert r["drains"] == 2 and r["drain_diameter"] == 110.0
    assert params(base, RF)["drains"] == [[1000.0, 1000.0], [4000.0, 3000.0]] and params(base, RF)["drain_diameter"] == 110.0
    assert ok(base.run("roof.drainage.set", {"id": RF, "drains": [[500, 500]]}))["drain_diameter"] == 110.0          # 지름은 그대로 남는다
    assert err(base.run("roof.drainage.set", {"id": RF, "drains": [[9000, 100]]}))["code"] == "CONSTRAINT"           # 지붕 밖
    assert err(base.run("roof.drainage.set", {"id": RF, "drains": [[100, 100], [100, 100]]}))["code"] == "CONSTRAINT"   # 같은 자리
    assert err(base.run("roof.drainage.set", {"id": RF, "drains": [[100, 100]] * 33}))["code"] == "SCHEMA_INVALID"
    ok(base.run("roof.drainage.set", {"id": RF, "drains": [[100, 100]]}))
    e = err(base.run("roof.parapet.set", {"id": RF, "height": 900}))                                                 # (100, 100) 은 파라펫(두께 200) 위
    assert e["code"] == "CONSTRAINT" and "배수구" in e["message"]
    ok(base.run("roof.drainage.set", {"id": RF, "drains": [[300, 300]]}))
    parapet(base)
    assert err(base.run("roof.drainage.set", {"id": RF, "drains": [[100, 100]]}))["code"] == "CONSTRAINT"           # 파라펫을 만든 뒤에도 같은 검사
    ok(base.run("roof.drainage.set", {"id": RF, "clear": ["drains"]}))
    assert "drains" not in params(base, RF) and "drain_diameter" not in params(base, RF)
    assert err(base.run("roof.drainage.set", {"id": RF, "drain_diameter": 100}))["code"] == "CONSTRAINT"             # 배수구 없이 지름만은 안 된다
    ok(base.run("roof.drainage.set", {"id": RF, "drains": [[3000, 3000]]}))
    # 지붕을 옮기면 배수구도 옮겨 간다
    ok(base.run("roof.move", {"id": RF, "dx": 1000, "dy": 2000}))
    assert params(base, RF)["drains"] == [[4000.0, 5000.0]]
    ok(base.run("element.mirror", {"ids": [RF], "axis": [0, 0, 0, 1000]}))
    assert params(base, RF)["drains"] == [[-4000.0, 5000.0]]
    ok(base.run("history.undo", {}))


def test_4_물매가_있는_평지붕에도_개구와_상단_붙이기가_된다(base):
    flat(base, [[-500, -500], [6500, -500], [6500, 4500], [-500, 4500]])
    ok(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 0, "pct": 2}}))               # x 가 커질수록 낮다: 가장 낮은 쪽 x=6500 이 3300
    v = ok(base.run("opening.vertical", {"host": RF, "boundary": [[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]}))
    assert v["faces"] == [0]
    r = ok(base.run("wall.attach_top", {"id": F.W2, "targets": [RF]}))
    assert r["top_min"] < r["top_max"] or r["top_min"] == pytest.approx(r["top_max"])        # 기운 아랫면에 붙는다(벽 위치에 따라 높이가 다르다)


# ---------------------------------------------------------------- 5. 도면
def lines(s, v):
    return g_of(s).q(D.view_lines, v)


def test_5_평면_파라펫은_절단_높이에_따라_절단선_또는_투영선(base):
    views(base)
    flat(base)
    flat0, top0 = lines(base, VP), top_z(base)
    ok(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 90, "pct": 2}, "drains": [[4000, 3000]], "drain_diameter": 110}))
    base0 = lines(base, VP)
    parapet(base, edges=[0, 2])
    g = g_of(base)
    seg_p, lab_p = g.q(D.roof_plan_parts, VP, RF)                                            # 절단 1200 > 파라펫 900: 투영선
    seg_c, _ = g.q(D.roof_plan_parts, VC, RF)                                                # 절단 500 < 파라펫 900: 절단선
    assert sum(1 for s in seg_p if s[0] == "cut") == 0 and sum(1 for s in seg_c if s[0] == "cut") == 8   # 조각마다 4선(공유 모서리 없음)
    pj = {tuple(s[1:]) for s in seg_p if s[0] == "proj"}
    assert (0.0, 0.0, 8000.0, 0.0) in pj or (8000.0, 0.0, 0.0, 0.0) in pj                  # 바깥 면(= 처마 윤곽)도 그려진다
    assert any(abs(s[1] - 0.0) < 1e-6 and abs(s[2] - 200.0) < 1e-6 and abs(s[3] - 8000.0) < 1e-6 for s in seg_p) or any(
        abs(s[2] - 200.0) < 1e-6 and abs(s[4] - 200.0) < 1e-6 for s in seg_p)                # 안쪽 변
    assert any("배수 물매 2%" == t[2] for t in lab_p)
    assert sum(1 for s in seg_p if s[0] == "symbol") >= 14                                  # 화살표(3)+배수구 원(12)·십자(2)
    assert lines(base, VP) != base0
    # 파라펫이 없는 지붕·배수 없는 지붕의 평면은 이전과 같다
    ok(base.run("roof.parapet.set", {"id": RF, "clear": True}))
    ok(base.run("roof.drainage.set", {"id": RF, "clear": ["fall", "drains"]}))
    assert top_z(base) == pytest.approx(top0) and lines(base, VP) == flat0                 # 떼면 처음 평면과 같다
    parapet(base)
    ps = g.q(D.roof_plan_parts, VP, RF)[0]
    n_proj = sum(1 for s in ps if s[0] == "proj")
    assert n_proj >= 8                                                                        # 네 조각의 바깥 모서리(공유 마이터는 긋지 않는다)
    same_as_full(base)


def test_5_단면과_입면의_파라펫(base):
    views(base)
    flat(base)
    parapet(base, edges=[0, 2])
    g = g_of(base)
    top = top_z(base)
    rects = g.q(D.roof_cut_rects, VS, RF)
    assert len(rects) == 3                                                                    # 지붕 띠 + 파라펫 둘(변 1·3 은 단면과 나란해 안 잘린다)
    zs = sorted((min(p[1] for p in r), max(p[1] for p in r)) for r in rects)
    assert (top - 200.0, top) in [(round(a, 6), round(b, 6)) for a, b in zs] and (top, top + 900.0) in [(round(a, 6), round(b, 6)) for a, b in zs]
    ln = lines(base, VS)
    assert any(s[0] == "cut" and abs(s[2] - (top + 900.0)) < 1e-6 for s in ln)               # 파라펫 윗선(절단 윤곽)
    el = lines(base, VE)
    assert any(s[0] == "proj" and abs(s[2] - (top + 900.0)) < 1e-6 and abs(s[4] - (top + 900.0)) < 1e-6 and abs(abs(s[3] - s[1]) - 8000.0) < 1e-6 for s in el)   # 입면 윗선
    # 가림: 앞(남) 파라펫이 뒤 지붕 윗면 모서리(배수 물매로 높은 y=6000... 아래 시험)를 가린다
    ok(base.run("roof.parapet.set", {"id": RF, "clear": True}))
    ok(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 270, "pct": 2}}))             # 남(y=0)이 낮고 북(y=6000)이 120 높다
    free = [s for s in lines(base, VE) if s[0] == "proj" and abs(s[2] - (top + 120.0)) < 1e-6 and abs(s[4] - (top + 120.0)) < 1e-6]
    assert free                                                                               # 파라펫이 없으면 뒤(북) 윗모서리가 보인다
    parapet(base, edges=[0])
    hid = [s for s in lines(base, VE) if s[0] == "proj" and abs(s[2] - (top + 120.0)) < 1e-6 and abs(s[4] - (top + 120.0)) < 1e-6]
    assert not hid                                                                            # 높이 900 파라펫이 가린다
    same_as_full(base)


def test_5_반사_천장_평면은_파라펫을_그리지_않는다(base):
    flat(base)
    ok(base.run("view.rcp", {"id": VC, "name": "RCP", "level": F.LV1}))
    before = lines(base, VC)
    parapet(base)
    assert lines(base, VC) == before                                                          # 파라펫은 지붕 위라 아래에서 보이지 않는다


# ---------------------------------------------------------------- 6. 수량
def test_6_수량_일람표와_조회(base):
    flat(base)
    from dobim_core import schedules as SC
    parapet(base, edges=[0, 2], height=1000, thickness=250)
    q = ok(base.run("roof.parapet", {"id": RF}))["total"]
    assert q == {"pieces": 2, "length": 16000.0, "height": 1000.0, "volume": 16000 * 250 * 1000.0, "area": 16000000.0}
    row = g_of(base).q(SC.element_row, RF)
    assert row["parapet_length"] == 16000.0 and row["parapet_volume"] == 16000 * 250 * 1000.0
    assert row["area"] == 8000 * 6000 and row["volume"] == 8000 * 6000 * 200                 # 지붕 판의 면적·체적은 파라펫을 더하지 않는다
    ok(base.run("roof.parapet.set", {"id": RF, "clear": True}))
    assert "parapet_length" not in g_of(base).q(SC.element_row, RF)


# ---------------------------------------------------------------- 7. IFC
def test_7_IFC_파라펫_배수구와_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("roof.create", {"id": RF, "level": F.LV2, "boundary": BOX, "form": "flat", "thickness": 200}))
        ok(b.run("roof.parapet.set", {"id": RF, "height": 900, "thickness": 200, "edges": [0, 1, 2]}))
        ok(b.run("roof.drainage.set", {"id": RF, "fall": {"dir": 90, "pct": 2}, "drains": [[4000, 3000], [2000, 5000]], "drain_diameter": 110}))
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        rname = ok(b.run("element.get", {"id": RF}))["params"]["name"]
        rf = next(e for e in f.by_type("IfcRoof") if e.Name == rname)
        assert rf.PredefinedType == "FLAT_ROOF"
        parts = [o for r in rf.IsDecomposedBy for o in r.RelatedObjects]
        walls = [o for o in parts if o.is_a("IfcWall")]
        assert len(walls) == 3 and all(w.PredefinedType == "PARAPET" for w in walls)
        assert sorted(w.GlobalId for w in walls) == sorted(part_guid(RF, f"parapet:{i}:{i}") for i in range(3))
        assert all(w.Representation.Representations[0].RepresentationType == "Brep" for w in walls)
        drains = [o for o in parts if o.is_a("IfcWasteTerminal")]
        assert len(drains) == 2 and all(d.PredefinedType == "ROOFDRAIN" for d in drains)
        slab = next(o for o in parts if o.is_a("IfcSlab"))
        assert slab.PredefinedType == "ROOF" and slab.GlobalId == part_guid(RF, "face:0")
        st = ifcopenshell.geom.settings()
        vol = sum(US.get_volume(ifcopenshell.geom.create_shape(st, w).geometry) for w in walls)
        assert vol == pytest.approx(ok(b.run("roof.parapet", {"id": RF}))["total"]["volume"] * 1e-9, rel=1e-6)   # IFC 형상 부피 = 수량(m³)
        assert file_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "t3.ifc"))
        assert file_errors(f3) == []
        assert len(f3.by_type("IfcWall")) >= 3 and sum(1 for e in f3.by_type("IfcFlowTerminal")) >= 2                   # IFC2X3: 배수구는 IfcFlowTerminal
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        p = ok(b.run("element.get", {"id": RF}, project="imp.dobim"))["params"]
        assert p["parapet"]["edges"] == [0, 1, 2] and p["fall"] == {"dir": 90.0, "pct": 2.0} and p["drains"] == [[4000.0, 3000.0], [2000.0, 5000.0]]
        ok(b.run("roof.parapet.set", {"id": RF, "height": 1200}))
        f = ifcopenshell.open(str(b.export("t4.ifc")))
        assert file_errors(f) == []
        ok(b.run("roof.parapet.set", {"id": RF, "clear": True}))
        ok(b.run("roof.drainage.set", {"id": RF, "clear": ["drains"]}))
        f = ifcopenshell.open(str(b.export("t5.ifc")))
        assert not f.by_type("IfcWall") or all(w.PredefinedType != "PARAPET" for w in f.by_type("IfcWall"))
        assert not f.by_type("IfcWasteTerminal") and file_errors(f) == []
        for _ in range(3):
            ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()


# ---------------------------------------------------------------- 8. 하위 호환
def test_8_새_기능을_쓰지_않은_평지붕과_다른_지붕은_그대로(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("roof.create", {"id": RF, "level": F.LV2, "boundary": BOX, "form": "flat", "overhang": 300, "thickness": 200}))
        p = ok(b.run("element.get", {"id": RF}))
        assert set(p["params"]) == {"name", "boundary", "form", "pitch", "overhang", "base_offset", "thickness"} and "parapet" not in p["derived"]
        f = ifcopenshell.open(str(b.export("a.ifc")))
        assert not f.by_type("IfcWasteTerminal") and not [w for w in f.by_type("IfcWall") if w.PredefinedType == "PARAPET"]
        from dobim_core import kinds as K2
        g = K2.roof_geom(p["params"])
        assert g["planes"] == [(0.0, 0.0, 0.0)] and "fall_pct" not in g
    finally:
        b.close()


# ---------------------------------------------------------------- 9. 상호작용
def test_9_형식을_바꾸거나_외곽을_줄이면_파라펫_배수는_이유와_함께_거절한다(base):
    flat(base)
    parapet(base)
    ok(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 0, "pct": 2}, "drains": [[3000, 3000]]}))
    e = err(base.run("roof.set", {"id": RF, "form": "footprint", "pitch": 20}))
    assert e["code"] == "CONSTRAINT" and "평지붕" in e["message"]                                  # 경사 형식에는 둘 수 없다
    ok(base.run("roof.parapet.set", {"id": RF, "clear": True}))
    ok(base.run("roof.drainage.set", {"id": RF, "clear": ["fall", "drains"]}))
    ok(base.run("roof.set", {"id": RF, "form": "footprint", "pitch": 20}))                          # 떼면 바뀐다
    ok(base.run("roof.set", {"id": RF, "form": "flat"}))
    ok(base.run("roof.drainage.set", {"id": RF, "drains": [[7000, 5000]]}))
    e = err(base.run("roof.set", {"id": RF, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]}))   # 줄이면 배수구가 지붕 밖
    assert e["code"] == "CONSTRAINT" and "배수구" in e["message"]


def test_9_회전하면_배수_물매_방향과_배수구가_따라간다(base):
    flat(base)
    ok(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 0, "pct": 2}, "drains": [[1000, 2000]]}))
    ok(base.run("element.rotate", {"ids": [RF], "center": [0, 0], "angle": 90}))
    p = params(base, RF)
    assert p["fall"]["dir"] == pytest.approx(90.0) and p["drains"][0] == pytest.approx([-2000.0, 1000.0], abs=1e-6)
    a, b, _c = g_of(base).q(Q.roof_gm, RF)["planes"][0]
    assert (a, b) == pytest.approx((0.0, -0.02), abs=1e-9)                                          # 흐름 방향 +y 라 y 가 클수록 낮다


def test_9_처마_부속은_기운_평지붕에서도_만들어진다(base):
    flat(base, overhang=400)
    ok(base.run("roof.drainage.set", {"id": RF, "fall": {"dir": 0, "pct": 2}}))
    r = ok(base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": "all"}, "gutter": {"edges": "all"}, "soffit": {"edges": "all"}}))
    assert [r["eaves"][k]["pieces"] for k in ("fascia", "gutter", "soffit")] == [4, 2, 2]            # 홈통·반자는 수평 처마(흐름 방향과 수직인 두 변)에만
    parapet(base, edges=[0, 1, 2, 3])
    views(base)
    same_as_full(base)
    ok(base.run("roof.parapet.set", {"id": RF, "clear": True}))
    ok(base.run("roof.drainage.set", {"id": RF, "clear": ["fall"]}))
    assert g_of(base).q(Q.roof_gm, RF)["planes"] == [(0.0, 0.0, 0.0)]
