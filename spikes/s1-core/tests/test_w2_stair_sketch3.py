# SPDX-License-Identifier: MPL-2.0
"""W2 스물세 번째 PR: 스케치 계단 후속 2(ARC-29) — 구간 층참(landings)과 균형 돌음(balanced), 꺾임 135도 초과 거절 사유.

이전(스무 번째 PR)은 스케치 계단의 보행선 일부를 평평한 층참으로 지정할 수 없었고(모서리 층참 corners 1 만 있었다),
모서리 쐐기는 같은 각으로만 나눴다(안쪽 모서리 점 둘레 균등, 보행선 위 깊이가 이웃 직선 디딤판과 다르다).
1. 재현: 층참 지정 없이는 보행선이 같은 깊이로 나뉜다 -> landings 로 층참 변(디딤판 하나, 평평한 윗면)
2. 층참 형상·수량: 챌판 수·높이 배분, 층참 뒤 디딤판 밑면, 구간 분배, 모서리와 함께, 거절
3. 층참 도면(평면·단면·입면, 증분 = 전체)·난간(평평한 층참 난간)·편집·IFC(IfcStairFlight 와 IfcSlab LANDING 분리, 왕복 해시·바이트)
4. 균형 돌음 형상: 같은 보행선 깊이, 모든 챌판 선이 안쪽 모서리 점을 지난다, 이웃 직선 디딤판은 사다리꼴, 넓이 합 = 같은 영역
5. 균형 돌음 거절·계단 검사 근거·도면·난간·편집·IFC
6. 꺾임 135도 초과 거절 사유
7. 무작위 입력 불변식
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import math
import random
import sys

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import commands as C
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core.ifc_model import part_guid

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

ST = F.ST1
SK = ST[:-1] + "5"
VP = "4" + "0" * 19 + "p1"     # 1F 평면
VS = "4" + "0" * 19 + "s1"     # 층참을 지나는 단면(x = 4800)
VE = "4" + "0" * 19 + "e1"     # 남측 입면
# 직선 3000 -> 층참 1500 -> 직선 3000(모두 동쪽, 폭 1200, 챌판 18 = 디딤판 17: 구간마다 8 + 층참 하나)
LAND = {"path": [[1000, 16000], [4000, 16000], [5500, 16000], [8500, 16000]], "landings": [1], "width": 1200, "height": 3000, "risers": 18}
# 직선 3000 -> 왼쪽(북) 90도 -> 직선 3000, 균형 돌음 쐐기 4개. 접하는 원호 반지름 600, 안쪽 모서리 점 (3400, 18600)
BAL = {"path": [[1000, 18000], [4000, 18000], [4000, 21000]], "corners": [4], "balanced": [1], "width": 1200, "height": 3000, "risers": 18}
RISE = 3000 / 18


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


def views(s):
    ok(s.run("view.create", {"id": VP, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [4800, 15000], "p2": [4800, 17500]}))
    ok(s.run("view.create", {"id": VE, "name": "남", "type": "elevation", "dir": "S"}))


def same_as_full(s, vs=(VP, VS, VE)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in vs:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)


def area(poly):
    return abs(K.polygon_area2([list(q) for q in poly])) / 2.0


def convex(poly) -> bool:
    n = len(poly)
    cr = [(poly[(i + 1) % n][0] - poly[i][0]) * (poly[(i + 2) % n][1] - poly[(i + 1) % n][1]) - (poly[(i + 1) % n][1] - poly[i][1]) * (poly[(i + 2) % n][0] - poly[(i + 1) % n][0])
          for i in range(n)]
    return all(c > -1e-6 for c in cr) or all(c < 1e-6 for c in cr)


def sketch(s, spec, eid=SK, **kw):
    return ok(s.run("stair.sketch", {"id": eid, "level": F.LV1, **dict(spec, **kw)}))


def lay_of(s, eid=SK):
    return K.stair_sketch_layout(params(s, eid))


def skpart(spec):
    return {k: v for k, v in spec.items() if k in ("path", "bulges", "corners", "landings", "balanced")}


def prow(sk, **kw):
    return dict({"name": "x", "width": 1200.0, "height": 3000.0, "risers": 18, "base_offset": 0.0}, sketch=sk, **kw)


def region_area(lay):
    return sum(area(q) for t in lay["treads"] for q in t["pieces"])


# ---------------------------------------------------------------- 1. 재현
def test_1_재현_층참_지정_없이는_보행선을_같게_나눈다(base):
    # 이전: 직선 보행선은 17 디딤판으로 같게 나뉜다(깊이 7500 / 17). 층참 변을 따로 둘 방법이 없었다
    plain = {k: v for k, v in LAND.items() if k != "landings"}
    sketch(base, plain)
    lay0 = lay_of(base)
    assert lay0["td"] == pytest.approx(7500 / 17) and lay0["landings"] == [] and all("landing" not in t for t in lay0["treads"])
    assert set(params(base, SK)["sketch"]) == {"path"}
    # landings 로 변 1 을 층참으로: 그 변이 디딤판 하나(깊이 1500, 윗면 평평)이고 나머지 16 디딤판은 양쪽 직선 구간에 8 + 8(깊이 375)
    L2 = SK[:-1] + "7"
    r = sketch(base, LAND, eid=L2)
    lay = lay_of(base, L2)
    assert params(base, L2)["sketch"]["landings"] == [1] and lay["td"] == pytest.approx(375.0) and r["length"] == pytest.approx(7500)
    assert [x["tread"] for x in lay["landings"]] == [8] and lay["landings"][0]["len"] == pytest.approx(1500.0) and lay["landings"][0]["seg"] == 1
    # 이전 형식의 행·계단은 그대로(층참 키 없음, 모서리 층참 corners 1 도 그대로 쐐기 하나)
    sketch(base, {"path": [[0, 0], [3000, 0], [3000, 3000]], "corners": [1], "width": 1200, "height": 3000, "risers": 18}, eid=SK[:-1] + "9")
    sk9 = params(base, SK[:-1] + "9")["sketch"]
    assert set(sk9) == {"path", "corners"} and lay_of(base, SK[:-1] + "9")["landings"] == []


# ---------------------------------------------------------------- 2. 층참 형상·수량
def test_2_층참_형상_높이_배분과_수량(base):
    sketch(base, LAND)
    p = params(base, SK)
    lay = K.stair_sketch_layout(p)
    tr = lay["treads"]
    assert len(tr) == 17 and [("landing" in t) for t in tr] == [False] * 8 + [True] + [False] * 8
    for j, t in enumerate(tr):   # 디딤판 윗면 = (j + 1) 챌판 높이: 층참도 디딤판 하나를 차지한다(높이 배분 그대로, 마지막 디딤판 위 + 챌판 = 위층 바닥)
        assert t["z"] == pytest.approx((j + 1) * RISE)
    assert tr[-1]["z"] + RISE == pytest.approx(3000)
    for a, b in zip(tr, tr[1:]):
        assert b["s0"] == pytest.approx(a["s1"])
    ld = tr[8]
    assert ld["s0"] == pytest.approx(3000) and ld["s1"] == pytest.approx(4500) and ld["z"] == pytest.approx(1500) and ld["zb"] == pytest.approx(1350)
    assert len(ld["pieces"]) == 1 and area(ld["pieces"][0]) == pytest.approx(1200 * 1500)
    # 층참 뒤 디딤판 밑면 = 층참 윗면 - 150(꺾인 계단 위 단 묶음 규약), 층참 앞은 아래 끝까지 속이 찬 덩어리
    assert [t["zb"] for t in tr[:8]] == [0.0] * 8 and [t["zb"] for t in tr[9:]] == [pytest.approx(1350)] * 8
    # 층참 앞뒤 직선 구간은 같은 깊이 375 의 사각형, 층참 변의 양끝 챌판 선(x = 4000·5500)
    assert all(t["s1"] - t["s0"] == pytest.approx(375) for t in tr if "landing" not in t)
    assert tr[8]["risers"][0] == (pytest.approx((4000.0, 16600.0)), pytest.approx((4000.0, 15400.0))) and tr[8]["risers"][1][0] == pytest.approx((5500.0, 16600.0))
    # 체적 = 조각마다 넓이 x (윗면 - 밑면)
    exp = sum(area(q) * (t["z"] - t["zb"]) for t in tr for q in t["pieces"])
    assert K.stair_volume(p) == pytest.approx(exp) and erow(base, SK)["volume"] == pytest.approx(exp)
    assert erow(base, SK)["tread"] == pytest.approx(375.0) and erow(base, SK)["length"] == pytest.approx(7500)
    der = ok(base.run("element.get", {"id": SK}))["derived"]
    assert der["landings"] == 1 and der["landing_depth"] == pytest.approx(1500) and der["treads"] == 17 and der["tread_depth"] == pytest.approx(375)
    # 같은 영역(폭 x 보행선 길이)을 덮는다: 층참 지정 없는 같은 계단과 조각 넓이 합이 같다
    assert region_area(lay) == pytest.approx(1200 * 7500)
    assert region_area(lay) == pytest.approx(region_area(K.stair_sketch_layout(prow({"path": LAND["path"]}))))


def test_2_층참_모서리와_함께_원호_뒤_층참_둘(base):
    # 직선 3000 -> 왼쪽 90도 모서리 쐐기 3 -> 위로 층참 변(3000 - 600 = 2400) -> 직선 2000. 디딤판 17 = 쐐기 3 + 층참 1 + 직선 구간 둘에 13
    spec = {"path": [[0, 0], [3000, 0], [3000, 3000], [3000, 5000]], "corners": [3, 0], "landings": [1], "width": 1200, "height": 3000, "risers": 18}
    sketch(base, spec)
    lay = lay_of(base)
    assert [s["kind"] for s in lay["segs"]] == ["line", "corner", "line", "line"] and lay["segs"][2].get("land") and not lay["segs"][3].get("land")
    assert [("winder" in t, "landing" in t) for t in lay["treads"]].count((True, False)) == 3 and [x["len"] for x in lay["landings"]] == [pytest.approx(2400.0)]
    runs = [t for t in lay["treads"] if "winder" not in t and "landing" not in t]
    assert len(runs) == 13 and len(lay["treads"]) == 17
    # 순서: 직선 구간 | 쐐기 3 | 층참 | 직선 구간. 층참 윗면은 앞 디딤판 윗면 + 챌판 하나
    kinds = ["w" if "winder" in t else "L" if "landing" in t else "r" for t in lay["treads"]]
    assert "".join(kinds).count("rwwwL") == 1 and kinds[-1] == "r"
    iL = kinds.index("L")
    assert lay["treads"][iL]["z"] - lay["treads"][iL - 1]["z"] == pytest.approx(RISE)
    assert region_area(lay) == pytest.approx(1200 * (3000 - 600 + 3000 - 600 + 2000) + 1200 ** 2)   # 직선 변 셋(접점까지 줄었다) + 연 모양
    # 원호 변 뒤 층참, 층참 둘
    spec2 = {"path": [[0, 0], [3000, 0], [4000, 1000], [4000, 4000], [4000, 6000]], "bulges": [0, 0.41421356, 0, 0], "landings": [2, 3], "width": 1200, "height": 3000, "risers": 24}
    sketch(base, spec2, eid=SK[:-1] + "9")
    l2 = lay_of(base, SK[:-1] + "9")
    assert [x["len"] for x in l2["landings"]] == [pytest.approx(3000), pytest.approx(2000)] and sum(1 for t in l2["treads"] if "landing" in t) == 2
    assert len(l2["treads"]) == 23 and l2["treads"][-1]["z"] == pytest.approx(23 * 3000 / 24)
    assert [t["zb"] for t in l2["treads"] if "landing" in t] == [pytest.approx(x["z"] - 150) for x in l2["treads"] if "landing" in x]


def test_2_층참_거절(base):
    def bad(spec=LAND, **kw):
        return err(base.run("stair.sketch", dict(spec, level=F.LV1, **kw)))
    # 원호 변은 층참이 될 수 없다
    e = bad(path=[[0, 0], [3000, 0], [4000, 1000]], bulges=[0, 0.41421356], landings=[1])
    assert e["code"] == "CONSTRAINT" and "원호" in e["message"] and "층참" in e["message"]
    # 변 번호 범위 밖·중복·내림차순·개수 초과·음수는 입력 오류
    assert bad(landings=[3])["code"] == "CONSTRAINT"
    assert bad(landings=[-1])["code"] == "SCHEMA_INVALID"
    assert bad(path=[[i * 3000, 0] for i in range(12)], landings=list(range(9)))["code"] in ("SCHEMA_INVALID", "CONSTRAINT")
    # 디딤판이 모자라다: 챌판 3 = 디딤판 2 < 층참 1 + 직선 구간 둘
    assert "챌판 수가 모자란다" in bad(risers=3)["message"]
    # 층참뿐인 계단은 디딤판이 층참 수와 같아야 한다(남는 디딤판을 둘 직선 구간이 없다)
    e = bad(path=[[0, 0], [1500, 0]], landings=[0], risers=5)
    assert e["code"] == "CONSTRAINT" and "남는 디딤판" in e["message"]
    sketch(base, {"path": [[0, 0], [1500, 0]], "landings": [0], "width": 1200, "height": 3000, "risers": 2}, eid=SK[:-1] + "9")
    assert len(lay_of(base, SK[:-1] + "9")["treads"]) == 1
    # 거절된 입력은 모델을 바꾸지 않는다
    h = base.eng.state_hash()
    bad(risers=3)
    assert base.eng.state_hash() == h


def test_2_층참_계단_검사_깊이가_폭보다_작으면_경고(base):
    sketch(base, LAND)
    assert ok(base.run("stair.check", {"id": SK}))["warnings"] == []
    short = dict(LAND, path=[[1000, 16000], [4000, 16000], [4600, 16000], [7600, 16000]])   # 층참 깊이 600 < 폭 1200
    sketch(base, short, eid=SK[:-1] + "9")
    c = ok(base.run("stair.check", {"id": SK[:-1] + "9"}))
    assert c["form"] == "sketch" and [w["code"] for w in c["warnings"]] == ["STAIR_LANDING"]
    assert c["warnings"][0]["value"] == pytest.approx(600.0) and c["warnings"][0]["limit"] == pytest.approx(1200.0)
    assert "balanced" not in c   # 균형 돌음이 없으면 응답 키는 이전과 같다


# ---------------------------------------------------------------- 3. 층참 도면·난간·편집·IFC
def test_3_층참_도면_평면_단면_입면(base):
    views(base)
    sketch(base, LAND)
    g = g_of(base)

    def pts(segs):
        return {(round(x[1], 3), round(x[2], 3)) for x in segs if x[0] == "proj"} | {(round(x[3], 3), round(x[4], 3)) for x in segs if x[0] == "proj"}
    # 절단 1200: 층참(윗면 1500)은 아직 안 보인다 -> x 는 4000(직선 구간 끝) 이하. 절단선이 있다
    s1 = g.q(D.plan_stair_parts, VP, SK)[0]
    assert max(q[0] for q in pts(s1)) <= 4000 + 1e-6 and sum(1 for x in s1 if x[0] == "cut") == 1
    # 절단 3500: 전부 보인다 -> 층참의 양끝 챌판 선(x = 4000·5500)과 옆선이 그려진다
    ok(base.run("view.set", {"id": VP, "cut_height": 3500}))
    s2 = g_of(base).q(D.plan_stair_parts, VP, SK)[0]
    assert not any(x[0] == "cut" for x in s2)
    pp = pts(s2)
    assert {(4000.0, 15400.0), (4000.0, 16600.0), (5500.0, 15400.0), (5500.0, 16600.0)} <= pp
    ok(base.run("view.set", {"id": VP, "cut_height": 1200}))
    g = g_of(base)
    # 단면(x = 4800): 층참 조각만 잘린다 -> 두께 150(1350 ~ 1500)·길이 1200 의 절단 사각형 하나
    rects = g.q(D.stair_cut_rects, VS, SK)
    assert len(rects) == 1 and (rects[0][0][1], rects[0][2][1]) == (pytest.approx(1350.0), pytest.approx(1500.0))
    assert abs(rects[0][1][0] - rects[0][0][0]) == pytest.approx(1200.0)
    # 입면: 층참 조각의 윗면·밑면 가장자리와 수직 모서리(층참 밑면 1350)
    ve = g.q(D.stair_vedges, VE, SK)
    assert any(abs(e[0][2] - 1350.0) < 1e-6 or abs(e[1][2] - 1350.0) < 1e-6 for e in ve)
    same_as_full(base)


def test_3_층참_난간_평평한_구간과_따라가기(base):
    sketch(base, LAND)
    r = ok(base.run("railing.from_stair", {"stair": SK, "side": "both", "inset": 50}))
    rl = {params(base, x)["src_tag"]: params(base, x) for x in r["created"]}
    assert [params(base, x)["src_tag"] for x in r["created"]] == ["kL0", "kR0", "kL1", "kR1", "kL2", "kR2"]
    # 구간마다: 앞 직선 구간 = 첫 챌판 위에서 8 챌판 오르고, 층참 난간은 평평하며(윗면 높이), 뒤 직선 구간은 층참 위 챌판 하나 위에서 시작해 위층 바닥에서 끝난다
    for s in "LR":
        assert rl[f"k{s}0"]["base_offset"] == pytest.approx(RISE) and rl[f"k{s}0"]["rise"] == pytest.approx(8 * RISE)
        assert rl[f"k{s}1"]["base_offset"] == pytest.approx(9 * RISE) and rl[f"k{s}1"]["rise"] == pytest.approx(0.0)
        assert rl[f"k{s}2"]["base_offset"] == pytest.approx(10 * RISE) and rl[f"k{s}2"]["rise"] == pytest.approx(8 * RISE)
        assert rl[f"k{s}2"]["base_offset"] + rl[f"k{s}2"]["rise"] == pytest.approx(3000)

    def flat(k):
        return [c for pt in rl[k]["path"] for c in pt]
    assert flat("kL1") == pytest.approx([4000, 16550, 5500, 16550]) and flat("kR2") == pytest.approx([5500, 15450, 8500, 15450])
    # 층참이 없는 스케치 계단 난간은 이전처럼 kL·kR 한 줄씩
    sketch(base, {"path": [[0, 20000], [4000, 20000]]}, eid=SK[:-1] + "9")
    assert [params(base, x)["src_tag"] for x in ok(base.run("railing.from_stair", {"stair": SK[:-1] + "9"}))["created"]] == ["kL", "kR"]
    # 계단을 옮기고 돌리면 난간이 따라간다(다시 만든 난간선과 같다)
    m = ok(base.run("stair.move", {"id": SK, "dx": 200, "dy": 0}))
    assert sorted(m["railings"]["moved"]) == sorted(r["created"])
    ok(base.run("element.rotate", {"ids": [SK], "center": [0, 0], "angle": 45}))
    exp = {s["tag"]: s for s in C._stair_rail_specs(params(base, SK), "both", 50.0, True)}
    for x in r["created"]:
        q = params(base, x)
        assert [c for pt in q["path"] for c in pt] == pytest.approx([c for pt in exp[q["src_tag"]]["path"] for c in pt], abs=1e-3)
        assert q["base_offset"] == pytest.approx(exp[q["src_tag"]]["base_offset"]) and q["rise"] == pytest.approx(exp[q["src_tag"]]["rise"])


def test_3_층참_편집_고치기_이동_회전_대칭_복사_되돌리기_저장(base):
    sketch(base, LAND)
    h0 = base.eng.state_hash()
    ok(base.run("stair.set", {"id": SK, "risers": 20, "width": 1000}))
    p = params(base, SK)
    assert p["risers"] == 20 and p["sketch"]["landings"] == [1]
    assert err(base.run("stair.set", {"id": SK, "risers": 3}))["code"] == "CONSTRAINT"
    ok(base.run("stair.move", {"id": SK, "dx": 100, "dy": -50}))
    ok(base.run("element.rotate", {"ids": [SK], "center": [0, 0], "angle": 30}))
    ok(base.run("element.mirror", {"ids": [SK], "axis": [0, 0, 0, 1000]}))
    assert params(base, SK)["sketch"]["landings"] == [1] and K.row_params_error("stair", params(base, SK)) is None
    ok(base.run("element.copy", {"ids": [SK], "dx": 8000, "dy": 0, "new_ids": [SK[:-1] + "8"]}))
    assert params(base, SK[:-1] + "8")["sketch"]["landings"] == [1]
    # 층참 바꾸기: 다른 변을 층참으로, 층참 없애기(키가 없어진다)
    ok(base.run("stair.set", {"id": SK, "sketch": {"path": [[0, 0], [3000, 0], [4500, 0], [7500, 0]], "landings": [0, 2]}}))
    assert params(base, SK)["sketch"]["landings"] == [0, 2] and len(lay_of(base)["landings"]) == 2
    ok(base.run("stair.set", {"id": SK, "sketch": {"path": [[0, 0], [3000, 0], [4500, 0], [7500, 0]], "landings": []}}))
    assert "landings" not in params(base, SK)["sketch"]
    for _ in range(7):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0
    ok(base.run("history.redo", {}))
    h1 = base.eng.state_hash()
    base.close()
    s2 = kit.Session(base.path, create=False)
    try:
        assert s2.eng.state_hash() == h1 and params(s2, SK)["risers"] == 20 and params(s2, SK)["sketch"]["landings"] == [1]
        ok(s2.run("stair.delete", {"id": SK}))
        ok(s2.run("history.undo", {}))
        assert params(s2, SK)["sketch"]["landings"] == [1]
    finally:
        s2.close()


def test_3_층참_IFC_단_묶음과_층참_슬래브_분리_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ids = [SK, SK[:-1] + "9", SK[:-1] + "8", SK[:-1] + "7"]
        ok(b.run("stair.sketch", {"id": ids[0], "level": F.LV1, **LAND}))                                                                  # 직선 + 층참 + 직선
        ok(b.run("stair.sketch", {"id": ids[1], "level": F.LV1, "path": [[0, 30000], [1500, 30000], [5000, 30000]], "landings": [0],
                                  "width": 1200, "height": 3000, "risers": 18}))                                                          # 층참이 맨 앞
        ok(b.run("stair.sketch", {"id": ids[2], "level": F.LV1, "path": [[0, 40000], [3000, 40000], [4500, 40000], [7500, 40000], [9000, 40000], [12000, 40000]],
                                  "landings": [1, 3], "width": 1200, "height": 3000, "risers": 24}))                                      # 층참 둘, 단 묶음 셋
        ok(b.run("stair.sketch", {"id": ids[3], "level": F.LV1, "path": [[0, 50000], [3000, 50000], [3000, 53000], [3000, 54500], [3000, 57500]],
                                  "corners": [3, 0, 0], "landings": [2], "width": 1200, "height": 3000, "risers": 24}))                  # 모서리 + 층참
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        names = {ok(b.run("element.get", {"id": x}))["params"]["name"]: x for x in ids}
        by = {names[e.Name]: e for e in f.by_type("IfcStair") if e.Name in names}

        def kids(e):
            return [o for r in e.IsDecomposedBy for o in r.RelatedObjects]
        assert by[ids[0]].PredefinedType == "TWO_STRAIGHT_RUN_STAIR" and by[ids[1]].PredefinedType == "STRAIGHT_RUN_STAIR"   # 단 묶음 2 개 / 1 개(층참이 맨 앞)
        assert by[ids[2]].PredefinedType == "NOTDEFINED" and by[ids[3]].PredefinedType == "QUARTER_WINDING_STAIR"
        e = by[ids[0]]
        fl = sorted((o for o in kids(e) if o.is_a("IfcStairFlight")), key=lambda o: o.GlobalId)
        sl = [o for o in kids(e) if o.is_a("IfcSlab")]
        assert e.Representation is None and len(fl) == 2 and len(sl) == 1
        assert {o.GlobalId for o in fl} == {part_guid(ids[0], "sketch"), part_guid(ids[0], "sketch:1")} and sl[0].GlobalId == part_guid(ids[0], "slanding:0")
        assert sl[0].PredefinedType == "LANDING" and len(sl[0].Representation.Representations[0].Items) == 1
        for o in fl:   # 단 묶음: 디딤판 8 + 층참으로 오르는 챌판/위층으로 오르는 챌판 = 챌판 9, 챌판 합 18
            assert (o.NumberOfRisers, o.NumberOfTreads, o.PredefinedType) == (9, 8, "STRAIGHT") and o.TreadLength == pytest.approx(375.0)
            assert len(o.Representation.Representations[0].Items) == 8
        assert sum(o.NumberOfRisers for o in fl) == LAND["risers"]
        # 층참이 맨 앞이면 단 묶음 하나(나머지 디딤판 16)와 층참
        e1 = by[ids[1]]
        assert [o.is_a() for o in kids(e1)].count("IfcStairFlight") == 1 and [o.is_a() for o in kids(e1)].count("IfcSlab") == 1
        fl1 = next(o for o in kids(e1) if o.is_a("IfcStairFlight"))
        assert (fl1.NumberOfRisers, fl1.NumberOfTreads) == (17, 16)
        e2 = by[ids[2]]
        assert sorted(o.is_a() for o in kids(e2)) == ["IfcSlab", "IfcSlab", "IfcStairFlight", "IfcStairFlight", "IfcStairFlight"]   # 단 묶음 셋 + 층참 둘
        e3 = by[ids[3]]   # 모서리 쐐기가 든 단 묶음은 WINDER
        assert {o.PredefinedType for o in kids(e3) if o.is_a("IfcStairFlight")} == {"WINDER", "STRAIGHT"}
        assert file_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "t3.ifc"))
        assert file_errors(f3) == [] and {e.ShapeType for e in f3.by_type("IfcStair")} >= {"TWO_STRAIGHT_RUN_STAIR", "NOTDEFINED"}
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        assert ok(b.run("element.get", {"id": ids[0]}, project="imp.dobim"))["params"]["sketch"]["landings"] == [1]
        # 층참을 없애면 단 묶음 하나(STRAIGHT_RUN_STAIR)로 돌아가고 되돌리면 같다
        ok(b.run("stair.set", {"id": ids[0], "sketch": {"path": LAND["path"]}}))
        nm0 = params(b, ids[0])["name"]
        f4 = ifcopenshell.open(str(b.export("t4.ifc")))   # 파일 객체를 살려 둔다(역관계를 읽는다)
        e0 = next(x for x in f4.by_type("IfcStair") if x.Name == nm0)
        assert e0.PredefinedType == "STRAIGHT_RUN_STAIR" and [o.is_a() for o in kids(e0)] == ["IfcStairFlight"]
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()


# ---------------------------------------------------------------- 4. 균형 돌음 형상
def _bal_fan(lay):
    """균형 돌음 모서리의 쐐기 디딤판들과 안쪽 모서리 점."""
    wed = [t for t in lay["treads"] if "winder" in t]
    c = next(s for s in lay["segs"] if s["kind"] == "corner")["c"]
    return wed, c


def test_4_균형_돌음_같은_보행선_깊이와_안쪽_모서리_점_부채(base):
    sketch(base, BAL)
    p = params(base, SK)
    assert p["sketch"]["balanced"] == [1] and p["sketch"]["corners"] == [4]
    lay = K.stair_sketch_layout(p)
    tr = lay["treads"]
    total = 2 * 2400 + 600 * math.pi / 2
    d_u = total / 17
    assert lay["length"] == pytest.approx(total) and len(tr) == 17
    wed, c = _bal_fan(lay)
    assert c == pytest.approx((3400.0, 18600.0)) and [t["winder"]["q"] for t in wed] == [0, 1, 2, 3]
    # 쐐기는 모두 보행선(폭 가운데) 위 같은 깊이 d_u = 층참 없는 보행선 길이 / 디딤판 수, 모서리 보행선 양쪽으로 ext 만큼 넓다
    ext = (4 * d_u - 600 * math.pi / 2) / 2
    assert [t["s1"] - t["s0"] for t in wed] == pytest.approx([d_u] * 4) and lay["corners"][0]["ext"] == pytest.approx(ext) and ext > 0
    assert wed[0]["s0"] == pytest.approx(2400 - ext) and wed[-1]["s1"] == pytest.approx(2400 + 600 * math.pi / 2 + ext)
    # 모든 챌판 선(쐐기 5개 선)이 안쪽 모서리 점을 지나고, 각은 보행선 위치가 같은 간격이 되도록 정해진다: tan(-꺾임 전)·호 안 = 길이/반지름·tan(뒤)
    lines = [t["risers"][0] for t in wed] + [wed[-1]["risers"][1]]
    for left, right in lines:
        assert left == pytest.approx(c)
    ang = [math.degrees(math.atan2(r[1][1] - c[1], r[1][0] - c[0])) + 90.0 for r in lines]   # 아래 직선 변에 수직인 선(-90도)에서 잰 각 φ
    st = [2400 - ext + k * d_u for k in range(5)]

    def phi(s):
        x = s - 2400
        return -math.degrees(math.atan(-x / 600)) if x < 0 else x / 600 * 180 / math.pi if x <= 600 * math.pi / 2 else 90 + math.degrees(math.atan((x - 600 * math.pi / 2) / 600))
    assert ang == pytest.approx([phi(s) for s in st], abs=1e-6)
    assert lay["corners"][0]["angles"] == pytest.approx([b - a for a, b in zip(ang, ang[1:])], abs=1e-6)
    assert ang[0] < 0 and ang[-1] > 90      # 부채는 모서리 양쪽으로 넓다
    # 쐐기 조각은 볼록·반시계, 넓이 합 4 개 + 이웃 사다리꼴 둘 = 연 모양 + 사다리꼴 둘이 덮는 만큼
    for t in tr:
        for q in t["pieces"]:
            assert K.polygon_area2([list(x) for x in q]) > 0 and convex(q)
    # 이웃 직선 디딤판 하나씩(모서리 바로 앞·뒤)은 비스듬한 챌판 선을 가진 사다리꼴: 안쪽 깊이 = 중심 + ext, 바깥 = 중심 - ext, 넓이 = 폭 x 중심 깊이
    for j in (6, 11):
        t = tr[j]
        assert t["bal_outer"] == pytest.approx((t["s1"] - t["s0"]) - ext) and area(t["pieces"][0]) == pytest.approx(1200 * (t["s1"] - t["s0"]))
        assert len(t["pieces"][0]) == 4
    assert lay["corners"][0]["outer_min"] == pytest.approx(min(tr[6]["bal_outer"], tr[11]["bal_outer"]))
    # 같은 영역을 덮는다(직선 변 둘 + 연 모양 = 폭 x 보행선 길이가 아니라 직선 폭 x 변 길이 합 + 폭^2 x tan(45도))
    assert region_area(lay) == pytest.approx(1200 * 2400 * 2 + 1200 ** 2)
    assert region_area(lay) == pytest.approx(region_area(K.stair_sketch_layout(prow({"path": BAL["path"], "corners": [4]}))))
    # 높이: 디딤판 j 윗면 = (j + 1) 챌판 높이, 층참 없음, 밑면 0
    assert [t["z"] for t in tr] == pytest.approx([(j + 1) * RISE for j in range(17)]) and all(t["zb"] == 0.0 for t in tr)
    # 보행선 위 깊이가 더 고르다: 같은 모서리를 같은 각 쐐기로 나눈 계단(이전 방식)은 쐐기 235.6 대 직선 400 으로 1.7 배 차이
    un = K.stair_sketch_layout(prow({"path": BAL["path"], "corners": [4]}))
    def dep(la):
        return [t["s1"] - t["s0"] for t in la["treads"]]
    assert max(dep(un)) / min(dep(un)) > 1.6 and max(dep(lay)) / min(dep(lay)) < 1.25
    # 수량·derived
    der = ok(base.run("element.get", {"id": SK}))["derived"]
    assert der["balanced_corners"] == 1 and der["balanced_walk_tread"] == pytest.approx(d_u) and der["winder_walkline_tread"] == pytest.approx(d_u)
    assert erow(base, SK)["volume"] == pytest.approx(K.stair_volume(p)) and K.stair_volume(p) == pytest.approx(sum(area(q) * (t["z"] - t["zb"]) for t in tr for q in t["pieces"]))


def test_4_균형_돌음_기본은_이전_같은_각_쐐기_그대로(base):
    # balanced 를 주지 않으면(또는 모두 0 이면) 이전 배치·저장 형식 그대로
    sketch(base, {k: v for k, v in BAL.items() if k != "balanced"})
    lay = lay_of(base)
    assert "balanced" not in params(base, SK)["sketch"] and lay["corners"] == [{"seg": 1, "m": 4, "sw": pytest.approx(90.0)}]
    wed, c = _bal_fan(lay)
    assert [t["s1"] - t["s0"] for t in wed] == pytest.approx([600 * math.pi / 2 / 4] * 4) and "wt" not in wed[0]
    ang = [math.degrees(math.atan2(t["risers"][0][1][1] - c[1], t["risers"][0][1][0] - c[0])) for t in wed]
    assert ang == pytest.approx([-90.0, -67.5, -45.0, -22.5])
    ok(base.run("stair.sketch", {"id": SK[:-1] + "9", "level": F.LV1, **dict(BAL, balanced=[0])}))
    assert "balanced" not in params(base, SK[:-1] + "9")["sketch"]
    # 같은 입력 모서리를 균형으로 바꾸면 디딤판 수(17)·총 오름·영역은 그대로이고 쐐기 깊이만 다르다
    lb = K.stair_sketch_layout(prow(skpart(BAL)))
    assert len(lb["treads"]) == len(lay["treads"]) and lb["treads"][-1]["z"] == pytest.approx(lay["treads"][-1]["z"]) and region_area(lb) == pytest.approx(region_area(lay))


def test_4_균형_돌음_오른쪽_꺾임_두_모서리_층참과_함께(base):
    # 오른쪽(시계) 90도, 균형 쐐기 4 + 직선 + 왼쪽 90도 균형 쐐기 4(S 자 꺾임). 모든 부채가 자기 안쪽 모서리 점을 지난다
    spec = {"path": [[0, 0], [3000, 0], [3000, -3500], [6000, -3500]], "corners": [4, 4], "balanced": [1, 1], "width": 1200, "height": 3600, "risers": 30}
    sketch(base, spec)
    lay = lay_of(base)
    cs = [s for s in lay["segs"] if s["kind"] == "corner"]
    assert [c["sw"] for c in lay["corners"]] == pytest.approx([-90.0, 90.0]) and all(c.get("bal") for c in lay["corners"])
    for s in cs:
        for t in lay["treads"]:
            if t.get("winder", {}).get("seg") == lay["segs"].index(s):
                for line in t["risers"]:
                    inner = line[0] if s["sw"] > 0 else line[1]
                    assert inner == pytest.approx(s["c"])
    for t in lay["treads"]:
        for q in t["pieces"]:
            assert K.polygon_area2([list(x) for x in q]) > 0 and convex(q)
    assert region_area(lay) == pytest.approx(region_area(K.stair_sketch_layout(prow({"path": spec["path"], "corners": [4, 4]}, height=3600, risers=30))))
    # 균형 돌음 + 층참 변(모서리와 이웃하지 않는 변)
    spec2 = {"path": [[0, 0], [3000, 0], [3000, 3000], [3000, 4500], [3000, 7500]], "corners": [4, 0, 0], "balanced": [1, 0, 0], "landings": [2], "width": 1200, "height": 3000, "risers": 24}
    sketch(base, spec2, eid=SK[:-1] + "9")
    l2 = lay_of(base, SK[:-1] + "9")
    assert len(l2["landings"]) == 1 and l2["corners"][0].get("bal") and len(l2["treads"]) == 23
    assert region_area(l2) == pytest.approx(region_area(K.stair_sketch_layout(prow({"path": spec2["path"], "corners": [4, 0, 0]}, height=3000, risers=24))))


# ---------------------------------------------------------------- 5. 균형 돌음 거절·검사 근거·도면·난간·편집·IFC
def test_5_균형_돌음_거절(base):
    def bad(**kw):
        return err(base.run("stair.sketch", dict(BAL, level=F.LV1, **kw)))
    # 모서리 디딤판 수가 보행선을 같은 깊이로 나눈 수보다 적다(337.8mm 로 942.5mm 를 나누려면 3 이상)
    e = bad(corners=[2])
    assert e["code"] == "CONSTRAINT" and "균형 돌음" in e["message"] and "최소 3" in e["message"]
    assert "2 이상" in bad(corners=[1])["message"]                                   # 층참(1)은 나눌 디딤판이 없다
    # balanced 는 꼭짓점마다 하나 / 0·1 만 / corners 와 함께
    assert bad(balanced=[1, 1])["code"] == "CONSTRAINT" and bad(balanced=[2])["code"] == "SCHEMA_INVALID"
    e = err(base.run("stair.sketch", {"level": F.LV1, "path": [[0, 0], [3000, 0], [3000, 3000]], "balanced": [1], "width": 1200, "height": 3000, "risers": 18}))
    assert e["code"] == "CONSTRAINT" and "corners" in e["message"]
    # 이웃 변이 층참이면 거절
    e = err(base.run("stair.sketch", {"level": F.LV1, "path": [[0, 0], [3000, 0], [3000, 3000], [3000, 4500]], "corners": [4, 0], "balanced": [1, 0], "landings": [0],
                                      "width": 1200, "height": 3000, "risers": 24}))
    assert e["code"] == "CONSTRAINT" and "층참" in e["message"]
    # 연장이 이웃 직선 디딤판을 너무 얇게 한다(앞 변이 짧다)
    e = bad(path=[[0, 0], [800, 0], [800, 3000]], corners=[6])
    assert e["code"] == "CONSTRAINT" and ("얇게" in e["message"] or "이웃" in e["message"]), e
    # 거절된 입력은 모델을 바꾸지 않는다
    h = base.eng.state_hash()
    bad(corners=[2])
    assert base.eng.state_hash() == h


def test_5_균형_돌음_계단_검사_근거(base):
    sketch(base, BAL)
    c = ok(base.run("stair.check", {"id": SK}))
    d_u = (2 * 2400 + 600 * math.pi / 2) / 17
    assert c["form"] == "sketch" and len(c["balanced"]) == 1
    b0 = c["balanced"][0]
    assert b0["seg"] == 1 and b0["m"] == 4 and b0["walk_tread"] == pytest.approx(d_u)
    # 안쪽 끝에서 300mm 보행선 위 가장 좁은 쐐기 깊이 = 300 x 가장 좁은 쐐기 각(라디안), 이웃 직선 디딤판 바깥쪽 깊이
    lay = lay_of(base)
    assert b0["winder_tread"] == pytest.approx(300 * math.radians(min(lay["corners"][0]["angles"]))) and b0["outer_min"] == pytest.approx(lay["corners"][0]["outer_min"])
    assert [w["code"] for w in c["warnings"]] == ["STAIR_WINDER_TREAD"]
    w = c["warnings"][0]
    assert w["value"] == pytest.approx(b0["winder_tread"], abs=1e-3) and w["walk_min"] == pytest.approx(d_u, abs=1e-3) and w["inner_min"] == pytest.approx(b0["winder_tread"], abs=1e-3)
    # 같은 모서리를 같은 각 쐐기로 나누면(이전) 근거 키 없이 같은 코드: 300 x 90도 / 4 = 117.8
    sketch(base, {k: v for k, v in BAL.items() if k != "balanced"}, eid=SK[:-1] + "9")
    c9 = ok(base.run("stair.check", {"id": SK[:-1] + "9"}))
    assert "balanced" not in c9 and [x["code"] for x in c9["warnings"]] == ["STAIR_WINDER_TREAD"] and "walk_min" not in c9["warnings"][0]
    assert c9["warnings"][0]["value"] == pytest.approx(300 * math.pi / 2 / 4, abs=1e-3)
    # 보행선(폭 가운데) 위 쐐기 깊이가 260 보다 작으면 균형 전용 경고(챌판 24: 보행선 / 23 = 249.7)
    sketch(base, dict(BAL, risers=24), eid=SK[:-1] + "8")
    c8 = ok(base.run("stair.check", {"id": SK[:-1] + "8"}))
    assert "STAIR_BALANCED_WALK" in [x["code"] for x in c8["warnings"]]
    wb = next(x for x in c8["warnings"] if x["code"] == "STAIR_BALANCED_WALK")
    assert wb["value"] == pytest.approx((2 * 2400 + 600 * math.pi / 2) / 23, abs=1e-3) and wb["limit"] == 260.0 and wb["walk_min"] == wb["value"]
    # check 인자의 별도 키·응답의 warnings 에도 같은 근거
    r = ok(base.run("stair.set", {"id": SK, "risers": 18, "name": "A", "check": True}))
    assert [x["code"] for x in r["stair_warnings"]] == ["STAIR_WINDER_TREAD"] and [x["code"] for x in r["warnings"]] == ["STAIR_WINDER_TREAD"]


def test_5_균형_돌음_도면_평면_단면_입면(base):
    ok(base.run("view.create", {"id": VP, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 3500}))
    ok(base.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [4000, 17000], "p2": [4000, 22000]}))
    ok(base.run("view.create", {"id": VE, "name": "남", "type": "elevation", "dir": "S"}))
    sketch(base, BAL)
    g = g_of(base)
    lay = lay_of(base)
    wed, c = _bal_fan(lay)
    lines = [t["risers"][0] for t in wed] + [wed[-1]["risers"][1]]
    segs = g.q(D.plan_stair_parts, VP, SK)[0]
    pts = {(round(x[1], 3), round(x[2], 3), round(x[3], 3), round(x[4], 3)) for x in segs if x[0] == "proj"}
    for left, right in lines:   # 부채 선 5 개가 안쪽 모서리 점에서 바깥 가장자리까지 그려진다(양끝 점이 평면 선분의 양끝)
        a = (round(left[0], 3), round(left[1], 3), round(right[0], 3), round(right[1], 3))
        assert a in pts or (a[2], a[3], a[0], a[1]) in pts
    # 이웃 직선 디딤판 사다리꼴의 옆선은 부채 선 끝에서 끝난다
    assert any(abs(x[3] - lines[0][1][0]) < 1e-3 and abs(x[4] - lines[0][1][1]) < 1e-3 for x in segs if x[0] == "proj")
    # 단면(x = 4000, 위 직선 구간을 가로로 자름): 절단 사각형, 입면 투영선
    assert len(g.q(D.stair_cut_rects, VS, SK)) >= 1 and g.q(D.stair_lines, VE, SK)
    same_as_full(base)


def test_5_균형_돌음_난간_편집_왕복(base):
    sketch(base, BAL)
    r = ok(base.run("railing.from_stair", {"stair": SK, "side": "both", "inset": 50}))
    rl = {params(base, x)["src_tag"]: params(base, x) for x in r["created"]}
    assert [params(base, x)["src_tag"] for x in r["created"]] == ["kL0", "kR0", "kL1", "kR1", "kL2", "kR2"]
    for s in "LR":   # 구간 난간 높이가 이어지고 처음 = 첫 챌판 위, 끝 = 위층 바닥
        z = RISE
        for i in range(3):
            q = rl[f"k{s}{i}"]
            assert q["base_offset"] == pytest.approx(z)
            z += q["rise"]
        assert z == pytest.approx(3000)
    h0 = base.eng.state_hash()
    ok(base.run("stair.move", {"id": SK, "dx": 100, "dy": 50}))
    ok(base.run("element.rotate", {"ids": [SK], "center": [0, 0], "angle": 30}))
    assert params(base, SK)["sketch"]["balanced"] == [1] and K.row_params_error("stair", params(base, SK)) is None
    exp = {s["tag"]: s for s in C._stair_rail_specs(params(base, SK), "both", 50.0, True)}
    for x in r["created"]:   # 계단을 옮기고 돌리면 난간이 따라간다
        q = params(base, x)
        assert [v for pt in q["path"] for v in pt] == pytest.approx([v for pt in exp[q["src_tag"]]["path"] for v in pt], abs=1e-3)
    ok(base.run("element.mirror", {"ids": [SK], "axis": [0, 0, 0, 1000]}))
    p = params(base, SK)
    assert p["sketch"]["balanced"] == [1] and p["sketch"]["corners"] == [4]
    lay = K.stair_sketch_layout(p)
    wed, c = _bal_fan(lay)
    assert lay["corners"][0]["sw"] == pytest.approx(-90.0)   # 대칭: 꺾는 쪽이 바뀐다
    for t in wed:
        for line in t["risers"]:
            assert line[1] == pytest.approx(c)    # 오른쪽으로 꺾으면 안쪽 점은 오른쪽 점
    # 균형 바꾸기: 끄기(키 사라짐)·다시 켜기, 되돌리기
    ok(base.run("stair.set", {"id": SK, "sketch": {"path": BAL["path"], "corners": [4], "balanced": [0]}}))
    assert "balanced" not in params(base, SK)["sketch"]
    for _ in range(4):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0
    ok(base.run("history.redo", {}))
    h1 = base.eng.state_hash()
    base.close()
    s2 = kit.Session(base.path, create=False)
    try:
        assert s2.eng.state_hash() == h1 and params(s2, SK)["sketch"]["balanced"] == [1]
    finally:
        s2.close()


def test_5_균형_돌음_IFC_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ids = [SK, SK[:-1] + "9"]
        ok(b.run("stair.sketch", {"id": ids[0], "level": F.LV1, **BAL}))
        ok(b.run("stair.sketch", {"id": ids[1], "level": F.LV1, "path": [[0, 30000], [3000, 30000], [3000, 33000], [3000, 34500], [3000, 37500]],
                                  "corners": [4, 0, 0], "balanced": [1, 0, 0], "landings": [2], "width": 1200, "height": 3000, "risers": 24}))
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        names = {ok(b.run("element.get", {"id": x}))["params"]["name"]: x for x in ids}
        by = {names[e.Name]: e for e in f.by_type("IfcStair") if e.Name in names}
        e0 = by[ids[0]]
        fl = [o for r in e0.IsDecomposedBy for o in r.RelatedObjects]
        assert e0.PredefinedType == "QUARTER_WINDING_STAIR" and len(fl) == 1 and fl[0].PredefinedType == "WINDER" and len(fl[0].Representation.Representations[0].Items) == 17
        e1 = by[ids[1]]
        k1 = [o for r in e1.IsDecomposedBy for o in r.RelatedObjects]
        assert sorted(o.is_a() for o in k1) == ["IfcSlab", "IfcStairFlight", "IfcStairFlight"] and e1.PredefinedType == "QUARTER_WINDING_STAIR"
        assert file_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"] and file_errors(ifcopenshell.open(str(tmp_path / "t3.ifc"))) == []
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        assert ok(b.run("element.get", {"id": ids[0]}, project="imp.dobim"))["params"]["sketch"]["balanced"] == [1]
        ok(b.run("stair.set", {"id": ids[0], "sketch": {"path": BAL["path"], "corners": [4]}}))
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()


# ---------------------------------------------------------------- 6. 꺾임 135도 초과
def test_6_꺾임_135도_초과_거절_사유가_분명하다(base):
    def bad(deg):
        end = [3000 + 3000 * math.cos(math.radians(deg)), 3000 * math.sin(math.radians(deg))]
        return err(base.run("stair.sketch", {"level": F.LV1, "path": [[0, 0], [3000, 0], end], "corners": [3], "width": 1200, "height": 3000, "risers": 18}))
    e = bad(150)   # 꺾임각 150도
    assert e["code"] == "CONSTRAINT" and "135" in e["message"] and "1/cos" in e["message"] and "직선 변을 사이에" in e["message"]
    # 135도에서 바깥 둘레 꼭짓점이 폭의 2.6 배까지 뻗는다는 근거가 들어 있다
    assert "2.6" in e["message"]
    e5 = bad(5)
    assert e5["code"] == "CONSTRAINT" and "원호" in e5["message"]
    # 135도 이하는 된다(경계 135도)
    ok(base.run("stair.sketch", {"id": SK, "level": F.LV1, "path": [[0, 0], [3000, 0], [3000 + 3000 * math.cos(math.radians(135)), 3000 * math.sin(math.radians(135))]],
                                  "corners": [3], "width": 1200, "height": 3000, "risers": 18}))
    assert lay_of(base)["corners"][0]["sw"] == pytest.approx(135.0, abs=1e-3)


# ---------------------------------------------------------------- 7. 무작위 입력 불변식
def test_7_무작위_입력_불변식():
    rnd = random.Random(20261126)
    valid = 0
    for it in range(3000):
        w = rnd.choice([800.0, 1000.0, 1200.0, 1400.0])
        npts = rnd.randint(3, 6)
        pts = [[0.0, 0.0]]
        ang = rnd.uniform(0, 360)
        cors, bals, turns = [], [], []
        for i in range(npts - 1):
            ln = rnd.uniform(1200, 4500)
            pts.append([pts[-1][0] + ln * math.cos(math.radians(ang)), pts[-1][1] + ln * math.sin(math.radians(ang))])
            if i < npts - 2:
                tp = rnd.choice([0, 0, 1, 1, 1])
                if tp:
                    d = rnd.choice([-1, 1]) * rnd.uniform(25, 130)
                    ang += d
                    m = rnd.randint(1, 7)
                    cors.append(m)
                    bals.append(1 if (m >= 2 and rnd.random() < 0.6) else 0)
                else:
                    cors.append(0)
                    bals.append(0)
        n_edges = npts - 1
        land = sorted(rnd.sample(range(n_edges), rnd.choice([0, 0, 1, 1, 2]) if n_edges >= 2 else 0))
        sk = {"path": pts}
        if any(cors):
            sk["corners"] = cors
        if any(bals):
            sk["balanced"] = bals
        if land:
            sk["landings"] = land
        risers = rnd.randint(10, 40)
        p = prow(sk, width=w, risers=risers, height=risers * 170.0)
        if K.row_params_error("stair", p) is not None:
            continue
        valid += 1
        lay = K.stair_sketch_layout(p)
        tr = lay["treads"]
        assert len(tr) == risers - 1, (it, sk)
        for a, b in zip(tr, tr[1:]):   # 보행선을 빈틈·겹침 없이 이어 덮는다
            assert b["s0"] == pytest.approx(a["s1"], abs=1e-6) and b["z"] - a["z"] == pytest.approx(170.0)
        assert tr[0]["s0"] == pytest.approx(0.0) and tr[-1]["s1"] == pytest.approx(lay["length"])
        for t in tr:
            assert t["s1"] - t["s0"] > 0 and t["pieces"] and t["zb"] <= t["z"]
            for q in t["pieces"]:
                assert K.polygon_area2([list(x) for x in q]) > 0 and convex(q), (it, sk)
        # 같은 영역: 층참·균형을 뺀 같은 계단(디딤판 수만 다르다)의 조각 넓이 합과 같다
        base_sk = {k: v for k, v in sk.items() if k not in ("landings", "balanced")}
        bp = prow(base_sk, width=w, risers=risers, height=risers * 170.0)
        if K.row_params_error("stair", bp) is None:
            assert region_area(lay) == pytest.approx(region_area(K.stair_sketch_layout(bp)), rel=1e-9), (it, sk)
        # 층참: 윗면은 디딤판과 같은 규칙, 밑면 = 윗면 - 150(0 이상), 뒤 디딤판은 그 밑면
        base_zb = 0.0
        for t in tr:
            if "landing" in t:
                base_zb = max(0.0, t["z"] - 150.0)
                assert t["zb"] == pytest.approx(base_zb)
            else:
                assert t["zb"] == pytest.approx(base_zb)
        # 균형 돌음: 부채 선이 모두 안쪽 모서리 점을 지난다
        for c in lay["corners"]:
            if c.get("bal"):
                s = lay["segs"][c["seg"]]
                for t in tr:
                    if t.get("winder", {}).get("seg") == c["seg"]:
                        for line in t["risers"]:
                            inner = line[0] if s["sw"] > 0 else line[1]
                            assert inner == pytest.approx(s["c"], abs=1e-6)
    assert valid >= 150, valid
