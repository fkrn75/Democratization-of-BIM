# SPDX-License-Identifier: MPL-2.0
"""W2 스무 번째 PR: 스케치 계단 후속(ARC-29) — 꺾인 모서리 보행선(모서리 돌음 디딤판·층참), 계단 검사 전 형식 확대, GUI 동작.

이전(열일곱 번째 PR)은 스케치 계단의 꺾인 모서리를 거절했고(꺾인 계단은 turn 만), 계단 검사 경고는 방사·스케치 계단에만 붙었으며,
GUI 동작 목록에 방사·스케치 계단이 없었다.
1. 재현: 이전 거절 -> corners 로 만든다(접선 연속 입력은 이전과 같다)
2. 모서리 형상: 폭/2 반지름 접하는 보행선 원호, 같은 각의 쐐기 디딤판(균등 보행선 분할)·층참(1), 구간별 디딤판 정수 분배, 거절들
3. 도면: 평면(쐐기 챌판 선·절단선)·단면 절단·입면, 증분 = 전체
4. 편집·난간: stair.set·move·rotate·mirror·되돌리기·저장 왕복, 구간별 난간의 높이가 디딤판과 맞는다
5. IFC: 돌음·꺾임 계단 형식, 단 묶음 WINDER, IFC4·IFC2X3 검증 오류 0, 왕복 해시·바이트
6. 계단 검사: stair.check 와 check 인자(직선·꺾인·돌음 포함), 기존 응답 불변
7. GUI 동작: stair.spiral·stair.sketch 매핑
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import json
import math
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
VP = "4" + "0" * 19 + "p1"     # 1F 평면(절단 1200)
VS = "4" + "0" * 19 + "s1"     # 모서리를 지나는 단면(x = 4000)
VE = "4" + "0" * 19 + "e1"     # 남측 입면
# 직선 3000(동쪽) -> 왼쪽(북) 90도 꺾임 -> 직선 3000. 폭 1200: 접하는 원호가 변 끝에서 600 물러나고 안쪽 모서리 점 (3400, 12600), 모서리 3 디딤판
CORNER = {"path": [[1000, 12000], [4000, 12000], [4000, 15000]], "corners": [3], "width": 1200, "height": 3000, "risers": 18}


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
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [4000, 11000], "p2": [4000, 16000]}))
    ok(s.run("view.create", {"id": VE, "name": "남", "type": "elevation", "dir": "S"}))


def same_as_full(s, vs=(VP, VS, VE)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in vs:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)


def area(poly):
    return abs(K.polygon_area2([list(q) for q in poly])) / 2.0


def sketch(s, **kw):
    return ok(s.run("stair.sketch", {"id": SK, "level": F.LV1, **dict(CORNER, **kw)}))


def lay_of(s, eid=SK):
    return K.stair_sketch_layout(params(s, eid))


# ---------------------------------------------------------------- 1. 재현
def test_1_재현_꺾인_모서리_거절과_corners(base):
    # 이전: 접선이 끊기는 꼭짓점은 거절(안내는 꺾인 계단 turn). corners 없이 주면 지금도 같다
    e = err(base.run("stair.sketch", {k: v for k, v in CORNER.items() if k != "corners"} | {"level": F.LV1}))
    assert e["code"] == "CONSTRAINT" and "꺾인다" in e["message"] and "corners" in e["message"]
    # corners 로 모서리 디딤판 수를 주면 만들어진다
    r = sketch(base)
    p = params(base, SK)
    assert p["sketch"]["corners"] == [3] and "bulges" not in p["sketch"] and "run" not in p
    # 보행선 길이 = 직선 2400 x 2 + 접하는 원호(반지름 600 x 90도)
    assert r["length"] == pytest.approx(2 * 2400 + 600 * math.pi / 2)
    # 모두 0 이면 키를 두지 않는다(접선이 이어진 입력은 이전과 같다)
    ok(base.run("stair.sketch", {"id": SK[:-1] + "9", "level": F.LV1, "path": [[0, 0], [3000, 0], [6000, 0]], "corners": [0]}))
    assert params(base, SK[:-1] + "9")["sketch"] == {"path": [[0.0, 0.0], [3000.0, 0.0], [6000.0, 0.0]]}
    # 직선 보행선 스케치·원호 스케치의 행은 그대로다
    ok(base.run("stair.sketch", {"id": SK[:-1] + "8", "level": F.LV1, "path": [[0, 20000], [4000, 20000]]}))
    assert set(params(base, SK[:-1] + "8")["sketch"]) == {"path"}


# ---------------------------------------------------------------- 2. 형상·수량
def test_2_모서리_형상_쐐기_디딤판과_수량(base):
    r = sketch(base)
    p = params(base, SK)
    lay = K.stair_sketch_layout(p)
    segs = lay["segs"]
    assert [s["kind"] for s in segs] == ["line", "corner", "line"]
    cs = segs[1]
    assert cs["c"] == pytest.approx((3400.0, 12600.0)) and cs["r"] == 600.0 and cs["sw"] == pytest.approx(90.0) and cs["len"] == pytest.approx(600 * math.pi / 2)
    assert segs[0]["len"] == pytest.approx(2400.0) and segs[2]["a"] == pytest.approx((4000.0, 12600.0)) and segs[2]["len"] == pytest.approx(2400.0)
    # 디딤판 17개: 쐐기 3개 + 직선 구간 14개(구간 길이가 같아 7 + 7). 구간 디딤 깊이 = 2400 / 7
    tr = lay["treads"]
    assert len(tr) == 17 and [("winder" in t) for t in tr] == [False] * 7 + [True] * 3 + [False] * 7
    assert lay["td"] == pytest.approx(2400 / 7) and r["length"] == pytest.approx(lay["length"])
    for a, b in zip(tr, tr[1:]):   # 디딤판은 보행선을 빈틈·겹침 없이 이어 덮고 높이는 디딤판마다 챌판 하나
        assert b["s0"] == pytest.approx(a["s1"]) and b["z"] - a["z"] == pytest.approx(3000 / 18)
    # 쐐기 셋: 안쪽 모서리 점에서 같은 각(30도)으로 퍼지고 보행선 위 길이가 같다. 볼록·반시계. 넓이 합 = 폭 x 폭
    wed = [t for t in tr if "winder" in t]
    assert [t["winder"]["q"] for t in wed] == [0, 1, 2] and all(t["winder"]["m"] == 3 for t in wed)
    for t in wed:
        poly = t["pieces"][0]
        assert (3400.0, 12600.0) in [(round(x, 6), round(y, 6)) for x, y in poly]
        assert K.polygon_area2([list(q) for q in poly]) > 0
        n = len(poly)
        for i in range(n):
            a, b, c = poly[i], poly[(i + 1) % n], poly[(i + 2) % n]
            assert (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) >= -1e-6
        assert t["s1"] - t["s0"] == pytest.approx(600 * math.pi / 6)
    assert sum(area(t["pieces"][0]) for t in wed) == pytest.approx(1200 * 1200)
    ang = [math.degrees(math.atan2(rl[1][1] - 12600, rl[1][0] - 3400)) for rl in [t["risers"][0] for t in wed]]   # 챌판 선이 안쪽 모서리 점에서 나가는 각
    assert ang == pytest.approx([-90.0, -60.0, -30.0], abs=1e-6)
    assert wed[0]["risers"][0][0] == pytest.approx((3400, 12600)) and wed[0]["risers"][0][1] == pytest.approx((3400, 11400))
    # 모서리 앞뒤 직선 구간은 폭 x 구간 길이의 사각형 디딤판이고 마지막 디딤판은 위 끝(4000, 15000)까지다
    assert area(tr[0]["pieces"][0]) == pytest.approx(1200 * 2400 / 7)
    assert [pytest.approx(q) for q in tr[-1]["risers"][1]] == [(3400, 15000), (4600, 15000)]   # (왼쪽, 오른쪽)
    # 체적·일람표·derived
    assert erow(base, SK)["volume"] == pytest.approx(K.stair_volume(p)) and erow(base, SK)["tread"] == pytest.approx(2400 / 7)
    assert erow(base, SK)["length"] == pytest.approx(lay["length"])
    der = ok(base.run("element.get", {"id": SK}))["derived"]
    assert der["corners"] == 1 and der["winder_treads"] == 3 and der["arcs"] == 0 and der["walkline_length"] == pytest.approx(lay["length"])
    assert der["winder_walkline_tread"] == pytest.approx(600 * math.pi / 6)
    assert der["tread_depth"] == pytest.approx(2400 / 7) and der["treads"] == 17
    # 모서리 점 둘레 반지름 폭/2 보행선 위 값은 쐐기 깊이(314.2) -> 계단 검사는 300mm 보행선(157.1mm)이라 경고, 직선 구간 342.9mm 는 통과
    assert [w["code"] for w in r["warnings"]] == ["STAIR_WINDER_TREAD"] and r["warnings"][0]["value"] == pytest.approx(300 * math.pi / 6, abs=1e-3)


def test_2_모서리_층참_쐐기_수_오른쪽_꺾임_두_모서리(base):
    # 층참(모서리 디딤판 1): 쐐기 하나 = 안쪽 모서리 점 + 폭 x 폭 정사각형(바깥 모서리 포함 4점)
    sketch(base, corners=[1])
    lay = lay_of(base)
    w = [t for t in lay["treads"] if "winder" in t]
    assert len(w) == 1 and len(w[0]["pieces"][0]) == 4 and area(w[0]["pieces"][0]) == pytest.approx(1200 * 1200)
    assert lay["td"] == pytest.approx(2400 / 8) and sum(1 for t in lay["treads"] if "winder" not in t) == 16
    assert all(c["m"] == 1 for c in lay["corners"]) and K.stair_rule_warnings(params(base, SK)) == []   # 층참은 디딤판 깊이를 재지 않는다
    # 오른쪽(시계) 꺾임 + 대각 + 두 모서리(45도 2 디딤판, 45도 층참). 구간 길이 비례 분배(최대 나머지법): 합 = 디딤판 - 모서리 디딤판
    p2 = {"path": [[0, 0], [3000, 0], [5000, -2000], [5000, -5000]], "corners": [2, 1]}
    ok(base.run("stair.sketch", {"id": SK[:-1] + "9", "level": F.LV1, **p2, "width": 1200, "height": 3000, "risers": 18}))
    lay2 = lay_of(base, SK[:-1] + "9")
    assert [s["kind"] for s in lay2["segs"]] == ["line", "corner", "line", "corner", "line"] and [c["sw"] for c in lay2["corners"]] == pytest.approx([-45.0, -45.0])
    runs = [t for t in lay2["treads"] if "winder" not in t]
    assert len(lay2["treads"]) == 17 and len(runs) == 14 and sum(1 for t in lay2["treads"] if "winder" in t) == 3
    per = [sum(1 for t in runs if lay2["starts"][i] - 1e-6 <= t["s0"] < lay2["starts"][i] + lay2["segs"][i]["len"] - 1e-6) for i in (0, 2, 4)]
    assert per == [5, 4, 5]
    for t in lay2["treads"]:   # 어느 디딤판도 볼록하고 반시계다(오른쪽 꺾임의 쐐기도)
        for q in t["pieces"]:
            assert K.polygon_area2([list(x) for x in q]) > 0
    # 지정한 꺾임각이 아닌 모서리(60도)
    p3 = {"path": [[0, 30000], [3000, 30000], [3000 + 3000 * math.cos(math.radians(60)), 30000 + 3000 * math.sin(math.radians(60))]], "corners": [2]}
    ok(base.run("stair.sketch", {"id": SK[:-1] + "8", "level": F.LV1, **p3, "width": 1200, "height": 3000, "risers": 18}))
    l3 = lay_of(base, SK[:-1] + "8")
    assert l3["corners"][0]["sw"] == pytest.approx(60.0) and l3["corners"][0]["m"] == 2
    # 접하는 원호 위 쐐기 둘의 넓이 합 = 연 모양(안쪽 모서리 점·두 끝·바깥 모서리) 넓이 = 폭^2 x tan(꺾임각/2), 직선 디딤판 넓이 합 = 폭 x 직선 변 길이 합(변은 접점까지 줄었다)
    assert sum(area(t["pieces"][0]) for t in l3["treads"] if "winder" in t) == pytest.approx(1200 ** 2 * math.tan(math.radians(30)))
    assert sum(area(q) for t in l3["treads"] if "winder" not in t for q in t["pieces"]) == pytest.approx(1200 * sum(s["len"] for s in l3["segs"] if s["kind"] == "line"))
    assert [pytest.approx(s["len"]) for s in l3["segs"] if s["kind"] == "line"] == [3000 - 600 * math.tan(math.radians(30)), 3000 - 600 * math.tan(math.radians(30))]


def test_2_모서리_거절(base):
    def bad(**kw):
        return err(base.run("stair.sketch", dict(CORNER, level=F.LV1, **kw)))
    # 꺾임각 범위: 5도(너무 완만) / 150도(너무 급함)
    e = bad(path=[[0, 0], [3000, 0], [6000, 262.7]])
    assert e["code"] == "CONSTRAINT" and "꺾임각" in e["message"]
    e = bad(path=[[0, 0], [3000, 0], [3000 - 3000 * math.cos(math.radians(30)), 1500]])
    assert e["code"] == "CONSTRAINT" and "꺾임각" in e["message"]
    # 접선이 이어진 꼭짓점에 모서리를 주면 꺾임각 0 -> 거절
    assert "꺾임각" in bad(path=[[0, 0], [3000, 0], [6000, 0]])["message"]
    # 모서리는 직선 변 사이에서만(원호 변 이웃)
    e = bad(path=[[0, 0], [3000, 0], [4000, 1000], [4000, 3000]], bulges=[0, 0.41421356, 0], corners=[0, 3])
    assert e["code"] == "CONSTRAINT" and "직선 변" in e["message"]
    # 변이 모서리 원호에 다 먹힌다(꼭짓점 사이 600 < 접점 물러남 600 x 2)
    e = bad(path=[[0, 0], [3000, 0], [3000, 900], [5000, 900]], corners=[3, 3])
    assert e["code"] == "CONSTRAINT" and "1mm" in e["message"]
    # 디딤판 수 모자람: 디딤판 4개에 모서리 3 + 구간 2
    e = bad(risers=5)
    assert e["code"] == "CONSTRAINT" and "챌판 수가 모자란다" in e["message"]
    # corners 개수가 꼭짓점 수 - 2 가 아니다 / 범위 밖
    assert bad(corners=[1, 1])["code"] == "CONSTRAINT"
    assert bad(corners=[13])["code"] == "SCHEMA_INVALID"
    assert bad(corners=[-1])["code"] == "SCHEMA_INVALID"
    # 거절된 입력은 모델을 바꾸지 않는다
    h = base.eng.state_hash()
    bad(risers=5)
    assert base.eng.state_hash() == h


# ---------------------------------------------------------------- 3. 도면
def _pivot_angles(segs):
    """평면 선분 중 안쪽 모서리 점(3400, 12600)에서 나가는 선의 각(도, 반올림) 집합."""
    out = set()
    for x in segs:
        if x[0] != "proj":
            continue
        for (ax, ay), (bx, by) in (((x[1], x[2]), (x[3], x[4])), ((x[3], x[4]), (x[1], x[2]))):
            if math.hypot(ax - 3400, ay - 12600) < 1e-6 and math.hypot(bx - ax, by - ay) > 1e-6:
                out.add(round(math.degrees(math.atan2(by - ay, bx - ax))))
    return out


def test_3_도면_평면_단면_입면(base):
    views(base)
    ok(base.run("view.create", {"id": VS[:-1] + "2", "name": "S2", "type": "section", "p1": [500, 12000], "p2": [5000, 12000]}))   # 아래 직선 구간을 길게 자르는 단면
    sketch(base)
    g = g_of(base)
    segs, fills = g.q(D.plan_stair_parts, VP, SK)
    # 절단 1200: 윗면 <= 1200 인 디딤판 7개(직선 구간)의 윤곽 + 첫 안 보이는 쐐기 시작의 절단선 하나 + 보행선 화살표. 쐐기 챌판 선은 아직 없다
    assert fills == () and sum(1 for x in segs if x[0] == "cut") == 1 and sum(1 for x in segs if x[0] == "symbol") >= 3
    pts = {(round(x[1], 3), round(x[2], 3)) for x in segs if x[0] == "proj"} | {(round(x[3], 3), round(x[4], 3)) for x in segs if x[0] == "proj"}
    assert max(q[0] for q in pts) == pytest.approx(3400) and (3400.0, 12600.0) in pts and _pivot_angles(segs) == {180}
    cut = next(x for x in segs if x[0] == "cut")
    assert (cut[1], cut[2]) == pytest.approx((3250, 12600), abs=1e-6) and cut[4] == pytest.approx(11400)   # 직선 계단처럼 비스듬히: 안쪽 옆선 150 앞에서 바깥 둘레(쐐기 안)로
    # 절단 1500: 쐐기 둘(디딤판 9개)까지 보인다 -> 안쪽 모서리 점에서 나가는 챌판 선 둘(-90도·-60도)
    ok(base.run("view.set", {"id": VP, "cut_height": 1500}))
    s2 = g_of(base).q(D.plan_stair_parts, VP, SK)[0]
    assert _pivot_angles(s2) == {180, -90, -60} and sum(1 for x in s2 if x[0] == "cut") == 1
    # 절단이 계단 위면 전부, 절단선 없음, 안쪽 모서리 점에서 챌판 선이 -90·-60·-30·0 도와 안쪽 옆선(180·90도)
    ok(base.run("view.set", {"id": VP, "cut_height": 3500}))
    s3 = g_of(base).q(D.plan_stair_parts, VP, SK)[0]
    assert not any(x[0] == "cut" for x in s3) and _pivot_angles(s3) == {180, -90, -60, -30, 0, 90}
    ok(base.run("view.set", {"id": VP, "cut_height": 1200}))
    g = g_of(base)
    # 단면 1(x = 4000: 쐐기들과 위 직선 구간): 절단 사각형, 절단선. 단면 2(y = 12000: 아래 직선 구간 7개 + 쐐기)
    rects = g.q(D.stair_cut_rects, VS, SK)
    assert len(rects) >= 2 + 7 and any(x[0] == "cut" for x in g.q(D.stair_lines, VS, SK))
    assert max(r[2][1] for r in rects) <= 3000 + 1e-6
    r2 = g.q(D.stair_cut_rects, VS[:-1] + "2", SK)
    assert len(r2) >= 7 and any(x[0] == "cut" for x in g.q(D.stair_lines, VS[:-1] + "2", SK))
    # 입면: 디딤판 모서리 투영선, 수직 모서리(챌판 양 끝: 17 디딤판 x 2 + 모서리 쐐기의 안쪽 모서리 점 공유)
    assert g.q(D.stair_lines, VE, SK) and all(x[0] == "proj" for x in g.q(D.stair_lines, VE, SK))
    ve = g.q(D.stair_vedges, VE, SK)
    assert len([e for e in ve if abs(e[0][0] - e[1][0]) < 1e-9 and abs(e[0][2] - e[1][2]) > 1e-9]) >= 17 * 2
    same_as_full(base, (VP, VS, VE, VS[:-1] + "2"))


def test_3_치수_태그_상세범위(base):
    sketch(base)
    ok(base.run("view.create", {"id": VP, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200, "dims": True}))
    g = g_of(base)
    w = [d for d in g.q(D.view_dims, VP) if d[1].endswith(":width:0")]
    assert len(w) == 1 and w[0][5] == "1200"
    assert any("18단" in x[2] for x in g.q(D.view_dim_labels, VP))
    ext = D.detail_extent(g, SK, "plan", None)
    assert ext[0] <= 1000 + 1 and ext[2] >= 4600 - 1 and ext[3] >= 15000 - 1
    ok(base.run("annot.tag.create", {"view": VP, "target": SK, "fields": ["name", "risers"]}))
    from dobim_core import annot as AN
    x, y = AN.tag_anchor(g_of(base), SK)
    lay = lay_of(base)
    assert (x, y) == pytest.approx(K.sketch_walk_pt(lay, lay["length"] / 2.0)[0])


# ---------------------------------------------------------------- 4. 편집·난간
def test_4_편집_이동_회전_대칭_형식_바꾸기_저장(base):
    sketch(base)
    h0 = base.eng.state_hash()
    r = ok(base.run("stair.set", {"id": SK, "risers": 20, "width": 1000}))
    p = params(base, SK)
    assert p["risers"] == 20 and p["width"] == 1000.0 and p["sketch"]["corners"] == [3] and "warnings" in r
    assert err(base.run("stair.set", {"id": SK, "risers": 5}))["code"] == "CONSTRAINT"                  # 디딤판 4개 < 모서리 3 + 구간 2
    assert err(base.run("stair.set", {"id": SK, "width": 6000}))["code"] == "CONSTRAINT"                # 접하는 원호가 변을 다 먹는다
    m = ok(base.run("stair.move", {"id": SK, "dx": 100, "dy": -50}))
    assert m["path"][0] == [1100.0, 11950.0] and params(base, SK)["sketch"]["corners"] == [3]
    ok(base.run("element.rotate", {"ids": [SK], "center": [0, 0], "angle": 30}))
    assert K.row_params_error("stair", params(base, SK)) is None and lay_of(base)["corners"][0]["sw"] == pytest.approx(90.0)
    ok(base.run("element.mirror", {"ids": [SK], "axis": [0, 0, 0, 1000]}))                              # 대칭: 꺾는 쪽이 바뀐다(corners 는 꼭짓점 순서 그대로)
    assert params(base, SK)["sketch"]["corners"] == [3] and lay_of(base)["corners"][0]["sw"] == pytest.approx(-90.0)
    ok(base.run("element.copy", {"ids": [SK], "dx": 8000, "dy": 0, "new_ids": [SK[:-1] + "8"]}))
    assert params(base, SK[:-1] + "8")["sketch"]["corners"] == [3]
    # 모서리 바꾸기: 층참으로, 모서리 없는 경로로(접선 입력), 직선으로
    ok(base.run("stair.set", {"id": SK, "sketch": {"path": [[0, 0], [3000, 0], [3000, 3000]], "corners": [1]}}))
    assert params(base, SK)["sketch"]["corners"] == [1] and len(lay_of(base)["corners"]) == 1
    ok(base.run("stair.set", {"id": SK, "sketch": {"path": [[0, 0], [5000, 0]]}}))
    assert params(base, SK)["sketch"] == {"path": [[0.0, 0.0], [5000.0, 0.0]]}
    ok(base.run("stair.set", {"id": SK, "run": [0, 0, 5000, 0]}))
    assert "sketch" not in params(base, SK)
    for _ in range(8):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0
    ok(base.run("history.redo", {}))
    h1 = base.eng.state_hash()
    base.close()
    s2 = kit.Session(base.path, create=False)
    try:
        assert s2.eng.state_hash() == h1 and params(s2, SK)["risers"] == 20 and params(s2, SK)["sketch"]["corners"] == [3]
        ok(s2.run("stair.delete", {"id": SK}))
        ok(s2.run("history.undo", {}))
        assert params(s2, SK)["sketch"]["corners"] == [3]
    finally:
        s2.close()


def test_4_모서리_난간_구간별_높이와_따라가기(base):
    sketch(base)
    r = ok(base.run("railing.from_stair", {"stair": SK, "side": "both", "inset": 50}))
    rl = {params(base, x)["src_tag"]: params(base, x) for x in r["created"]}
    assert sorted(rl) == ["kL0", "kL1", "kL2", "kR0", "kR1", "kR2"] and [params(base, x)["src_tag"] for x in r["created"]] == ["kL0", "kR0", "kL1", "kR1", "kL2", "kR2"]
    rise = 3000 / 18
    for s in "LR":   # 난간선은 구간마다 이어지고 높이가 이어진다: 처음 = 첫 챌판 위, 끝 = 위층 바닥. 구간 rise = 구간 디딤판 수 x 챌판 높이(7·3·7)
        z = rise
        for i, nt in enumerate((7, 3, 7)):
            q = rl[f"k{s}{i}"]
            assert q["base_offset"] == pytest.approx(z) and q["rise"] == pytest.approx(nt * rise)
            z += q["rise"]
        assert z == pytest.approx(3000)

    def flat(k):
        return [c for pt in rl[k]["path"] for c in pt]
    assert flat("kL0") == pytest.approx([1000, 12550, 3400, 12550])
    assert flat("kL1") == pytest.approx([3400, 12550, 3450, 12550, 3450, 12600])      # 안쪽: 모서리 점 둘레를 50 들여 돈다
    assert flat("kR1") == pytest.approx([3400, 11450, 4550, 11450, 4550, 12600])     # 바깥: 바깥 둘레를 50 들인 두 변
    assert flat("kR2") == pytest.approx([4550, 12600, 4550, 15000])
    # 모서리가 없으면 이전처럼 kL·kR 한 줄씩(원호 안·바깥 난간은 자기 길이에 비례해 오른다)
    ok(base.run("stair.sketch", {"id": SK[:-1] + "9", "level": F.LV1, "path": [[0, 20000], [4000, 20000]]}))
    r2 = ok(base.run("railing.from_stair", {"stair": SK[:-1] + "9"}))
    assert [params(base, x)["src_tag"] for x in r2["created"]] == ["kL", "kR"]
    # 계단을 옮기고 돌리면 난간이 따라간다(다시 만든 난간선과 같다)
    m = ok(base.run("stair.move", {"id": SK, "dx": 200, "dy": 0}))
    assert sorted(m["railings"]["moved"]) == sorted(r["created"])
    ok(base.run("element.rotate", {"ids": [SK], "center": [0, 0], "angle": 45}))
    exp = {s["tag"]: s for s in C._stair_rail_specs(params(base, SK), "both", 50.0, True)}
    for x in r["created"]:
        q = params(base, x)
        assert [c for pt in q["path"] for c in pt] == pytest.approx([c for pt in exp[q["src_tag"]]["path"] for c in pt], abs=1e-3)
        assert q["base_offset"] == pytest.approx(exp[q["src_tag"]]["base_offset"]) and q["rise"] == pytest.approx(exp[q["src_tag"]]["rise"])
    # 모서리를 없애면 새 계단에 그 난간 자리가 없어 lost 로 남는다(이전 난간은 건드리지 않는다)
    lost = ok(base.run("stair.set", {"id": SK, "sketch": {"path": [[0, 0], [5000, 0]]}}))["railings"]["lost"]
    assert len(lost) == 6


# ---------------------------------------------------------------- 5. IFC
def test_5_모서리_계단_IFC_형식과_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ids = [SK, SK[:-1] + "9", SK[:-1] + "8", SK[:-1] + "7"]
        ok(b.run("stair.sketch", {"id": ids[0], "level": F.LV1, **CORNER}))                                             # 90도 + 쐐기 3
        ok(b.run("stair.sketch", {"id": ids[1], "level": F.LV1, **dict(CORNER, path=[[11000, 12000], [14000, 12000], [14000, 15000]], corners=[1])}))   # 90도 층참
        ok(b.run("stair.sketch", {"id": ids[2], "level": F.LV1, "path": [[21000, 12000], [24000, 12000], [26000, 14000]], "corners": [2], "width": 1000,
                                  "height": 3000, "risers": 18}))                                                        # 45도: 사분 아님
        ok(b.run("stair.sketch", {"id": ids[3], "level": F.LV1, "path": [[0, 30000], [3000, 30000], [3000, 33000], [3000, 36000], [6000, 36000]],
                                  "corners": [2, 0, 2], "width": 1200, "height": 3000, "risers": 24}))                   # 90 + 직선 + 90(반 돌음)
        ok(b.run("railing.from_stair", {"stair": ids[0]}))
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        names = {ok(b.run("element.get", {"id": x}))["params"]["name"]: x for x in ids}
        by = {names[e.Name]: e for e in f.by_type("IfcStair") if e.Name in names}
        assert by[ids[0]].PredefinedType == "QUARTER_WINDING_STAIR" and by[ids[1]].PredefinedType == "QUARTER_TURN_STAIR"
        assert by[ids[2]].PredefinedType == "NOTDEFINED" and by[ids[3]].PredefinedType == "HALF_WINDING_STAIR"
        for i, (typ, solids) in enumerate((("WINDER", 17), ("STRAIGHT", 17), ("WINDER", 17), ("WINDER", 24))):   # 마지막은 접선 꼭짓점(corners 0)을 지나는 디딤판이 조각 둘이다
            e = by[ids[i]]
            fl = [o for r in e.IsDecomposedBy for o in r.RelatedObjects]
            assert e.Representation is None and len(fl) == 1 and fl[0].is_a("IfcStairFlight") and fl[0].GlobalId == part_guid(ids[i], "sketch")
            assert fl[0].PredefinedType == typ and len(fl[0].Representation.Representations[0].Items) == solids, (i, typ, solids)
        p0 = params(b, ids[0])
        assert next(o for r in by[ids[0]].IsDecomposedBy for o in r.RelatedObjects).TreadLength == pytest.approx(K.stair_sketch_layout(p0)["td"])
        assert file_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "t3.ifc"))
        assert file_errors(f3) == [] and {e.ShapeType for e in f3.by_type("IfcStair")} >= {"QUARTER_WINDING_STAIR", "QUARTER_TURN_STAIR", "HALF_WINDING_STAIR"}
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        assert ok(b.run("element.get", {"id": ids[0]}, project="imp.dobim"))["params"]["sketch"]["corners"] == [3]
        # 모서리를 지우면(접선 경로) 형식이 직선으로 돌아가고 되돌리면 같다
        ok(b.run("stair.set", {"id": ids[0], "sketch": {"path": [[1000, 12000], [4000, 12000]]}}))
        nm0 = params(b, ids[0])["name"]
        e = next(x for x in ifcopenshell.open(str(b.export("t4.ifc"))).by_type("IfcStair") if x.Name == nm0)
        assert e.PredefinedType == "STRAIGHT_RUN_STAIR"
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()


# ---------------------------------------------------------------- 6. 계단 검사(모든 형식)
def _codes(w):
    return [x["code"] for x in w]


def test_6_계단_검사_직선_꺾인_돌음_별도_키(base):
    # 기본 응답은 그대로다: 키는 id 하나
    r = ok(base.run("stair.create", {"id": ST, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18}))
    assert r == {"id": ST}
    assert "stair_warnings" not in ok(base.run("stair.set", {"id": ST, "risers": 18, "name": "A"}))
    # 검사 질의: 걸음선 4000 -> 디딤 235.3mm < 260 경고, 챌판 166.7mm 통과
    c = ok(base.run("stair.check", {"id": ST}))
    assert c["form"] == "straight" and _codes(c["warnings"]) == ["STAIR_TREAD"] and c["warnings"][0]["value"] == pytest.approx(4000 / 17, abs=1e-3)
    # check 인자: 응답에 별도 키 stair_warnings(같은 값). 없으면 이전과 같다
    r2 = ok(base.run("stair.create", {"id": ST[:-1] + "9", "level": F.LV1, "run": [0, 0, 7000, 0], "height": 3600, "check": True}))
    assert set(r2) == {"id", "stair_warnings"} and _codes(r2["stair_warnings"]) == ["STAIR_RISE"] and r2["stair_warnings"][0]["value"] == pytest.approx(200.0)
    assert ok(base.run("stair.create", {"id": ST[:-1] + "8", "level": F.LV1, "run": [0, 5000, 7000, 5000], "check": True}))["stair_warnings"] == []   # 통과하면 빈 목록
    s = ok(base.run("stair.set", {"id": ST, "run": [1000, 1000, 6500, 1000], "check": True}))
    assert s["stair_warnings"] == [] and "warnings" not in s
    # 꺾인 계단(층참): 단 묶음 디딤 깊이를 같은 규칙으로(걸음선 2000 / 5 = 400 통과). 돌음(winders): 회전부 보행선(안쪽 끝 300mm) 디딤 깊이도
    t1 = {"id": ST[:-1] + "7", "level": F.LV1, "run": [0, 9000, 2000, 9000], "width": 900, "risers": 18, "turn": {"type": "L", "side": "left", "risers_a": 6}}
    assert ok(base.run("stair.create", dict(t1, check=True)))["stair_warnings"] == []
    t2 = dict(t1, id=ST[:-1] + "6", run=[0, 12000, 1500, 12000])   # 걸음선 1500 / 5 = 300 통과·돌음 아님
    ok(base.run("stair.create", dict(t2, turn=dict(t1["turn"], winders=3))))
    c6 = ok(base.run("stair.check", {"id": ST[:-1] + "6"}))
    assert c6["form"] == "turn" and _codes(c6["warnings"]) == ["STAIR_WINDER_TREAD"] and c6["warnings"][0]["value"] == pytest.approx(300 * math.pi / 2 / 3, abs=1e-3)
    # 방사 계단은 이전처럼 응답의 warnings, 같은 규칙의 stair.check
    r3 = ok(base.run("stair.spiral", {"id": ST[:-1] + "5", "level": F.LV1, "center": [10000, 6000], "radius_in": 300, "radius_out": 1500, "sweep": 360}))
    assert _codes(r3["warnings"]) == ["STAIR_TREAD"] and "stair_warnings" not in r3
    c5 = ok(base.run("stair.check", {"id": ST[:-1] + "5"}))
    assert c5["warnings"] == r3["warnings"] and c5["form"] == "spiral"
    # 거절: 계단이 아닌 요소·없는 id
    assert err(base.run("stair.check", {"id": F.W1}))["code"] == "NOT_FOUND"
    assert err(base.run("stair.check", {"id": ST[:-1] + "4"}))["code"] == "NOT_FOUND"


def test_6_계단_검사_기준값은_그대로(base):
    # 기준 18/26cm·300mm 보행선: 경계값 정확히 통과, 경계 아래만 경고
    assert K.stair_rule_warnings({"width": 1200, "height": 18 * 180, "risers": 18, "run": [0, 0, 17 * 260, 0]}) == []
    w = K.stair_rule_warnings({"width": 1200, "height": 18 * 181, "risers": 18, "run": [0, 0, 17 * 259, 0]})
    assert _codes(w) == ["STAIR_RISE", "STAIR_TREAD"] and w[0]["limit"] == 180.0 and w[1]["limit"] == 260.0
    # 돌음 회전부: 300mm 보행선 위 깊이 = 300 x 꺾임각(라디안) / 디딤판 수. 층참(1)은 디딤판이 아니라 재지 않는다
    # 90도: 2 = 235.6mm·3 = 157.1mm·5 = 94.2mm 경고. 120도: 2 = 314.2mm 통과·3 = 209.4mm 경고
    for deg, m, bad in ((90, 1, False), (90, 2, True), (90, 3, True), (90, 5, True), (120, 2, False), (120, 3, True)):
        end = [6000 + 6000 * math.cos(math.radians(deg)), 6000 * math.sin(math.radians(deg))]
        p = {"name": "x", "width": 1200, "height": 3000, "risers": 24, "base_offset": 0.0, "sketch": {"path": [[0, 0], [6000, 0], end], "corners": [m]}}
        assert K.row_params_error("stair", p) is None
        assert (_codes(K.stair_rule_warnings(p)) == ["STAIR_WINDER_TREAD"]) is bad, (deg, m)
        if bad:
            assert K.stair_rule_warnings(p)[-1]["value"] == pytest.approx(300 * math.radians(deg) / m, abs=1e-3)
    # 폭이 600 이하면 보행선은 폭/2
    p = {"name": "x", "width": 500, "height": 3000, "risers": 24, "base_offset": 0.0, "sketch": {"path": [[0, 0], [3000, 0], [3000, 3000]], "corners": [3]}}
    assert K.stair_rule_warnings(p)[0]["value"] == pytest.approx(250 * math.pi / 6, abs=1e-3)


# ---------------------------------------------------------------- 7. GUI 동작
def test_7_GUI_동작에_방사_스케치_계단():
    import parity_g as PG
    acts = json.loads(PG.DEFAULT_ACTIONS.read_text(encoding="utf-8"))
    by = {a["id"]: a for a in acts["actions"]}
    assert by["stair.spiral"]["command"] == "stair.spiral" and by["stair.sketch"]["command"] == "stair.sketch" and by["stair.check"]["command"] == "stair.check"
    res = PG.check(acts, PG.load_exempt_yaml(PG.DEFAULT_EXEMPT.read_text(encoding="utf-8")))
    assert res["pass"], res["errors"]
