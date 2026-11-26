# SPDX-License-Identifier: MPL-2.0
"""W2 스물네 번째 PR(구조 보 5부: 보 시스템과 가새).

1. 보 시스템 순수 형상(BM.system_positions·system_segments): 정렬 3종·개수·방향·오목한 경계·고리 찾기
2. 보 시스템 명령: 만들기(직접 경계·보 고리)·손으로 놓은 보와 같은 형상·경계 보 옆면 자르기·고치기(자식 재생성·id 이어받기)·옮기기·풀기·지우기·거절
3. 소유 규약: 자식 보를 따로 고치면 거절(태그는 허용)·유형 추종·유형 지우기 보호·되돌리기·저장
4. 보 시스템 출력: 도면(평면·단면 채움·증분 = 전체)·일람표·model.stats·IFC(IfcElementAssembly 집합·검증·왕복)
5. 가새: 두 점(높이 포함)·기둥 면 끝 자르기·연직 끝면·거절·복사·대칭·도면(입면·단면)·태그
6. 가새 IFC: IfcMember(BRACE)·IfcMemberType·IFC2X3·왕복·보와 같은 유형 함께 쓰기
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import sys
from math import atan2, cos, degrees, hypot, pi, sin, sqrt, tan

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

V, V2 = F.V1, F.V2
BT, BG = ("9" + "0" * 19 + x for x in ("c1", "c2"))                      # 보 유형: 작은 보 200x400, 큰 보 400x700
GB = ["9" + "0" * 19 + f"a{i}" for i in range(4)]                         # 경계를 이루는 큰 보 4개
BS2 = "9" + "0" * 19 + "s2"
C1 = "0" * 20 + "d1"
IBT, IBS, IBR = ("3" + "0" * 19 + x for x in ("c7", "s7", "r7"))          # IFC 왕복용(첫 글자가 유효한 GlobalId)
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


def get(s, e):
    return ok(s.run("element.get", {"id": e}))


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


@pytest.fixture
def bs(base):
    ok(base.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(base.run("beamtype.create", {"id": BT, "name": "S1", "width": 200, "depth": 400}))
    ok(base.run("beamtype.create", {"id": BG, "name": "G1", "width": 400, "depth": 700}))
    return base


def girders(s, x0=0, y0=-9000, w=6000, h=4000, level=F.LV1, offset=2400):
    """경계를 이루는 큰 보 네 개(반시계 고리: 아래·오른쪽·위·왼쪽)."""
    pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    for i, bid in enumerate(GB):
        ok(s.run("beam.create", {"id": bid, "level": level, "type": BG, "start": list(pts[i]), "end": list(pts[(i + 1) % 4]), "offset": offset}))


def kids(s, sid):
    return list(get(s, sid)["params"]["children"])


def seg(s, b):
    p = get(s, b)["params"]
    return tuple(p["start"]), tuple(p["end"])


def column(s, cid, pos, w=500):
    ok(s.run("family.place", {"id": cid, "family": "column.section", "type_values": {"section": "rect", "width": w, "depth": w},
                              "values": {"height": 3300}, "level": F.LV1, "position": list(pos)}))


def snap(s):
    """보·보 시스템 행 전체(id -> params)."""
    st = s.eng.st
    return {e: st.el[e]["params"] for k in ("beam", "beamsys") for e in st.by_kind.get(k, ())}


def dist_to_boundary(pt, poly) -> float:
    best = 1e18
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((pt[0] - x1) * dx + (pt[1] - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, hypot(pt[0] - (x1 + t * dx), pt[1] - (y1 + t * dy)))
    return best


# ---------------------------------------------------------------- 1. 순수 형상
def sp(**kw):
    p = {"boundary": [list(map(float, q)) for q in RECT], "angle": 0.0, "align": "center", "spacing": 1000.0}
    p.update(kw)
    return p


def test_1_정렬과_개수_자리():
    # 폭 4000: 가운데 정렬은 대칭(바깥 보가 모서리에 닿지 않는 가장 큰 개수), start·end 는 그 쪽 모서리에서 간격마다
    assert BM.system_positions(0.0, 4000.0, 1000.0, None, "center") == [500.0, 1500.0, 2500.0, 3500.0]
    assert BM.system_positions(0.0, 4000.0, 1000.0, None, "start") == [1000.0, 2000.0, 3000.0]
    assert BM.system_positions(0.0, 4000.0, 1500.0, None, "start") == [1500.0, 3000.0]
    assert BM.system_positions(0.0, 4000.0, 1500.0, None, "end") == [1000.0, 2500.0]
    assert BM.system_positions(0.0, 3000.0, 1000.0, None, "center") == [500.0, 1500.0, 2500.0]
    assert BM.system_positions(0.0, 4000.0, 2000.0, None, "center") == [1000.0, 3000.0]
    assert BM.system_positions(0.0, 4000.0, None, 3, "center") == [1000.0, 2000.0, 3000.0]       # 개수는 정렬과 무관: 폭을 개수 + 1 칸으로
    assert BM.system_positions(0.0, 4000.0, None, 3, "start") == [1000.0, 2000.0, 3000.0]
    assert BM.system_positions(0.0, 4000.0, 5000.0, None, "center") == [2000.0]                  # 간격이 폭보다 넓어도 가운데 정렬은 가운데에 하나
    assert BM.system_positions(0.0, 4000.0, 5000.0, None, "start") == []
    assert BM.system_positions(0.0, 4000.0, 4000.0, None, "start") == []                         # 모서리 자리에는 놓지 않는다
    assert BM.system_positions(0.0, 1.5, 100.0, None, "center") == []
    assert len(BM.system_positions(0.0, 100000.0, 50.0, None, "start")) > BM.SYS_COUNT_MAX         # 상한을 넘으면 알린다
    assert len(BM.system_positions(0.0, 100000.0, 50.0, None, "center")) > BM.SYS_COUNT_MAX


def test_1_방향과_오목한_경계():
    segs = BM.system_segments(sp())
    assert segs == [((0.0, 500.0), (6000.0, 500.0)), ((0.0, 1500.0), (6000.0, 1500.0)), ((0.0, 2500.0), (6000.0, 2500.0)), ((0.0, 3500.0), (6000.0, 3500.0))]
    s90 = BM.system_segments(sp(angle=90.0, spacing=2000.0))                                       # 90도: x 방향으로 간격, 보는 y 로 달린다
    assert s90 == [((5000.0, 0.0), (5000.0, 4000.0)), ((3000.0, 0.0), (3000.0, 4000.0)), ((1000.0, 0.0), (1000.0, 4000.0))]
    # 30도: 보는 방향 30도, 이웃 보 사이 수직 거리 = 간격, 두 끝은 경계 위
    s30 = BM.system_segments(sp(angle=30.0, spacing=700.0, align="start"))
    assert len(s30) >= 8
    c, s_ = cos(pi / 6), sin(pi / 6)
    vs = []
    for a, b in s30:
        assert degrees(atan2(b[1] - a[1], b[0] - a[0])) == pytest.approx(30.0, abs=1e-6)
        assert dist_to_boundary(a, RECT) < 1e-3 and dist_to_boundary(b, RECT) < 1e-3
        vs.append(-a[0] * s_ + a[1] * c)
    assert [round(y - x, 6) for x, y in zip(vs, vs[1:])] == [700.0] * (len(vs) - 1)
    # 오목한 L 형: 노치 선 위 자리는 변이 있는 쪽 구간만, 노치 안쪽 자리는 한 구간
    L = [[0.0, 0.0], [6000.0, 0.0], [6000.0, 2000.0], [2000.0, 2000.0], [2000.0, 4000.0], [0.0, 4000.0]]
    sl = BM.system_segments(sp(boundary=L, spacing=1000.0, align="start"))
    assert sl == [((0.0, 1000.0), (6000.0, 1000.0)), ((0.0, 2000.0), (2000.0, 2000.0)), ((0.0, 3000.0), (2000.0, 3000.0))]
    # ㄷ 형 경계에서 한 자리가 둘로 갈라진다(홀짝 규칙)
    U = [[0.0, 0.0], [6000.0, 0.0], [6000.0, 4000.0], [4000.0, 4000.0], [4000.0, 1000.0], [2000.0, 1000.0], [2000.0, 4000.0], [0.0, 4000.0]]
    su = BM.system_segments(sp(boundary=U, spacing=1500.0, align="start"))
    assert su == [((0.0, 1500.0), (2000.0, 1500.0)), ((4000.0, 1500.0), (6000.0, 1500.0)), ((0.0, 3000.0), (2000.0, 3000.0)), ((4000.0, 3000.0), (6000.0, 3000.0))]


def test_1_고리_찾기():
    sq = [((0, 0), (10, 0)), ((10, 0), (10, 5)), ((10, 5), (0, 5)), ((0, 5), (0, 0))]
    assert BM.loop_from_segments(sq) == [(0, 0), (10, 0), (10, 5), (0, 5)]
    assert BM.loop_from_segments([((0, 0), (10, 0)), ((10, 5), (10, 0)), ((10, 5), (0, 5)), ((0, 0), (0, 5))]) == [(0, 0), (10, 0), (10, 5), (0, 5)]   # 방향이 섞여도
    assert BM.loop_from_segments(sq[:3]) is None                                                    # 열린 사슬
    assert BM.loop_from_segments(sq + [((0, 0), (10, 5))]) is None                                   # 끝점을 셋이 공유
    assert BM.loop_from_segments(sq + [((20, 0), (30, 0)), ((30, 0), (30, 5)), ((30, 5), (20, 0))]) is None   # 고리가 둘
    assert BM.loop_from_segments([((0, 0), (10, 0)), ((10.4, 0.3), (10, 5)), ((10, 5), (0, 5)), ((0, 5), (0.5, -0.4))]) is not None   # 허용 1 mm


# ---------------------------------------------------------------- 2. 명령
def test_2_이전에는_보를_하나씩_놓고_시스템은_같은_형상을_한_번에_낸다(bs):
    """재현: 시스템 없이 칸을 채우려면 보마다 beam.create 를 했다(끝은 경계 보 옆면에서 이미 잘렸다). 시스템은 같은 형상을 한 번에 낸다."""
    girders(bs)
    r = ok(bs.run("beamsys.create", {"id": F.BS1, "level": F.LV1, "type": BT, "from_elements": GB, "spacing": 1000, "offset": 2400}))
    assert r["count"] == 4
    lst = {x["id"]: x for x in ok(bs.run("beam.list", {}))["items"]}
    sysrows = {lst[k]["start"][1]: lst[k] for k in r["children"]}
    assert sorted(sysrows) == [-8500.0, -7500.0, -6500.0, -5500.0]
    ok(bs.run("element.delete", {"id": F.BS1}))                                                          # 시스템을 지우고 같은 끝점에 손으로 놓는다
    for i, y in enumerate((-8500, -7500, -6500, -5500)):
        bid = "9" + "0" * 19 + f"m{i}"
        ok(bs.run("beam.create", {"id": bid, "level": F.LV1, "type": BT, "start": [0, y], "end": [6000, y], "offset": 2400}))
    man = {x["start"][1]: x for x in ok(bs.run("beam.list", {}))["items"] if x["id"].startswith("90000000000000000000m")}
    for y, k in sysrows.items():
        m = man[y]
        assert k["start"] == m["start"] and k["end"] == m["end"]
        assert (k["length"], k["cut_length"], k["volume_m3"]) == (m["length"], m["cut_length"], m["volume_m3"]) == (6000.0, 5600.0, 0.448)
        assert k["start_cut"] == m["start_cut"] == {"beam": GB[3], "length": 200.0} and k["end_cut"] == m["end_cut"] == {"beam": GB[1], "length": 200.0}   # 경계 보 옆면(폭 400 의 반)에서 끝난다
        assert k["bsys"] == F.BS1 and "bsys" not in m


def test_2_보_시스템_만들기(bs):
    r = ok(bs.run("beamsys.create", {"id": F.BS1, "level": F.LV2, "type": BT, "boundary": RECT[::-1], "angle": 0, "spacing": 1000, "offset": 100, "anchor": "bottom"}))
    assert r["count"] == 4 and len(set(r["children"])) == 4
    p = get(bs, F.BS1)["params"]
    assert p["boundary"] == [[0.0, 0.0], [6000.0, 0.0], [6000.0, 4000.0], [0.0, 4000.0]] and K.polygon_area2(p["boundary"]) > 0   # 시계 방향으로 줘도 반시계로 저장
    assert p["name"] == f"BS-{F.BS1[-6:]}" and p["children"] == r["children"] and p["btype"] == BT and p["spacing"] == 1000.0 and "count" not in p
    assert ok(bs.run("beamsys.list", {"level": F.LV2}))["items"][0]["id"] == F.BS1
    rows = {x["id"]: x for x in ok(bs.run("beam.list", {"level": F.LV2}))["items"]}
    assert set(rows) == set(r["children"])
    for b in r["children"]:
        q = get(bs, b)["params"]
        assert q["bsys"] == F.BS1 and q["btype"] == BT and q["offset"] == 100.0 and q["anchor"] == "bottom" and q["section"] == {"shape": "rect", "width": 200.0, "depth": 400.0}
        assert rows[b]["length"] == 6000.0 and rows[b]["level"] == F.LV2
    ls = ok(bs.run("beamsys.list", {}))["items"][0]
    assert ls["id"] == F.BS1 and ls["beams"] == 4 and ls["spacing"] == 1000.0 and ls["total_length"] == 24000.0 and ls["volume_m3"] == pytest.approx(1.92) and ls["type"] == "S1"
    assert ok(bs.run("beamsys.list", {"level": F.LV1}))["items"] == []
    assert ok(bs.run("model.validate", {}))["errors"] == 0
    # 개수 지정·정렬·방향
    r2 = ok(bs.run("beamsys.create", {"id": BS2, "level": F.LV1, "type": BT, "boundary": RECT, "angle": 90, "count": 5, "name": "BS-X"}))
    assert r2["count"] == 5
    xs = sorted(seg(bs, b)[0][0] for b in r2["children"])
    assert xs == [1000.0, 2000.0, 3000.0, 4000.0, 5000.0]
    assert get(bs, BS2)["params"]["count"] == 5 and get(bs, r2["children"][0])["params"]["name"].startswith("BS-X-")
    # 놓일 자리: 보 목록·model.validate
    assert ok(bs.run("model.validate", {}))["errors"] == 0


def test_2_경계를_보와_벽_고리로(bs):
    girders(bs)
    r = ok(bs.run("beamsys.create", {"id": F.BS1, "level": F.LV1, "type": BT, "from_elements": GB, "count": 3}))
    p = get(bs, F.BS1)["params"]
    assert sorted(map(tuple, p["boundary"])) == [(0.0, -9000.0), (0.0, -5000.0), (6000.0, -9000.0), (6000.0, -5000.0)] and K.polygon_area2(p["boundary"]) > 0
    ys = sorted(seg(bs, b)[0][1] for b in r["children"])
    assert ys == [-8000.0, -7000.0, -6000.0]
    # 그 순간의 축을 옮겨 적는다: 이후 경계 보를 옮겨도 시스템 경계는 그대로
    ok(bs.run("beam.move", {"id": GB[0], "dy": 100}))
    assert get(bs, F.BS1)["params"]["boundary"] == p["boundary"]
    # 벽 고리(기준 프로젝트의 벽 3개 + 닫는 벽)
    w4 = "1" + "0" * 20 + "4"
    ok(bs.run("wall.create", {"id": w4, "level": F.LV1, "axis": [0, 4000, 0, 0], "thickness": 200, "height": 3000}))
    r3 = ok(bs.run("beamsys.create", {"id": BS2, "level": F.LV2, "type": BT, "from_elements": [F.W1, F.W2, F.W3, w4], "spacing": 1000, "offset": 3000}))
    assert r3["count"] == 4 and sorted(seg(bs, b)[0][1] for b in r3["children"]) == [500.0, 1500.0, 2500.0, 3500.0]
    e = err(bs.run("beamsys.create", {"level": F.LV1, "type": BT, "from_elements": [F.W1, F.W2, F.W3], "spacing": 1000}))
    assert e["code"] == "CONSTRAINT" and "닫힌 고리" in e["message"]                                     # 열린 사슬
    e = err(bs.run("beamsys.create", {"level": F.LV1, "type": BT, "from_elements": [F.W1, F.W2, F.D1], "spacing": 1000}))
    assert e["code"] == "NOT_FOUND"                                                                       # 보·벽이 아니다


def test_2_고치면_자식이_다시_만들어진다(bs):
    r = ok(bs.run("beamsys.create", {"id": F.BS1, "level": F.LV1, "type": BT, "boundary": RECT, "spacing": 1000, "offset": 2400}))
    first = r["children"]
    assert len(first) == 4
    before = snap(bs)
    # 간격을 넓히면 줄어든 자식은 지우고 앞 자식 id 를 이어받는다
    r2 = ok(bs.run("beamsys.set", {"id": F.BS1, "spacing": 2000}))
    assert r2["count"] == 2 and r2["children"] == first[:2]
    assert all(b not in bs.eng.st.el for b in first[2:])
    assert sorted(seg(bs, b)[0][1] for b in r2["children"]) == [1000.0, 3000.0]
    # 좁히면 앞 id 는 그대로, 늘어난 것은 새로 만든다
    r3 = ok(bs.run("beamsys.set", {"id": F.BS1, "spacing": 500}))
    assert r3["count"] == 8 and r3["children"][:2] == first[:2] and len(set(r3["children"])) == 8
    # 개수로 바꾸면 spacing 이 빠지고 count 가 든다
    r4 = ok(bs.run("beamsys.set", {"id": F.BS1, "count": 2, "angle": 90}))
    p = get(bs, F.BS1)["params"]
    assert "spacing" not in p and p["count"] == 2 and p["angle"] == 90.0 and r4["count"] == 2
    assert sorted(seg(bs, b)[0][0] for b in kids(bs, F.BS1)) == [2000.0, 4000.0]
    # 같은 값으로 고치면 변화 없음
    assert ok(bs.run("beamsys.set", {"id": F.BS1, "count": 2}))["changed"] is False
    # 정렬·오프셋·맞춤·이름·유형·레벨
    ok(bs.run("beamsys.set", {"id": F.BS1, "spacing": 1500, "align": "end", "angle": 0, "offset": 200, "anchor": "center", "name": "ZZ", "type": BG, "level": F.LV2}))
    assert sorted(seg(bs, b)[0][1] for b in kids(bs, F.BS1)) == [1000.0, 2500.0]
    for b in kids(bs, F.BS1):
        q = get(bs, b)["params"]
        assert q["offset"] == 200.0 and q["anchor"] == "center" and q["btype"] == BG and q["section"]["width"] == 400.0 and q["name"].startswith("ZZ-")
    assert all(ok(bs.run("beam.list", {"level": F.LV2}))["items"][i]["bsys"] == F.BS1 for i in range(2))
    assert ok(bs.run("beam.list", {"level": F.LV1}))["items"] == []
    assert ok(bs.run("beamtype.list", {}))["items"][0]["beams"] + ok(bs.run("beamtype.list", {}))["items"][1]["beams"] == 2     # 유형 관계가 따라 옮겨졌다
    assert ok(bs.run("model.validate", {}))["errors"] == 0
    # 경계를 바꾸면 자리를 다시 잡는다
    ok(bs.run("beamsys.set", {"id": F.BS1, "boundary": [[0, 0], [3000, 0], [3000, 2000], [0, 2000]], "spacing": 500, "align": "start"}))
    assert sorted(seg(bs, b)[0][1] for b in kids(bs, F.BS1)) == [500.0, 1000.0, 1500.0] and all(seg(bs, b)[1][0] == 3000.0 for b in kids(bs, F.BS1))
    # 되돌리기 전부(바꾼 것 5번, 변화 없는 호출은 이력을 남기지 않는다): 처음 상태와 같다
    for _ in range(5):
        ok(bs.run("history.undo", {}))
    assert snap(bs) == before
    ok(bs.run("history.redo", {}))
    assert len(kids(bs, F.BS1)) == 2


def test_2_옮기기와_풀기와_지우기(bs):
    r = ok(bs.run("beamsys.create", {"id": F.BS1, "level": F.LV1, "type": BT, "boundary": RECT, "spacing": 1000, "offset": 2400}))
    first = r["children"]
    ok(bs.run("beamsys.move", {"id": F.BS1, "dx": 500, "dy": 300, "dz": -100}))
    assert get(bs, F.BS1)["params"]["boundary"][0] == [500.0, 300.0] and get(bs, F.BS1)["params"]["offset"] == 2300.0
    assert kids(bs, F.BS1) == first and sorted(seg(bs, b)[0] for b in first) == [(500.0, 800.0), (500.0, 1800.0), (500.0, 2800.0), (500.0, 3800.0)]
    assert get(bs, first[0])["params"]["offset"] == 2300.0
    # 풀기: 자식은 보통 보로 남고 시스템 요소만 사라진다
    before = snap(bs)
    rr = ok(bs.run("beamsys.ungroup", {"id": F.BS1}))
    assert rr["count"] == 4 and sorted(rr["released"]) == sorted(first)
    assert F.BS1 not in bs.eng.st.el and all("bsys" not in get(bs, b)["params"] for b in first)
    ok(bs.run("beam.move", {"id": first[0], "dx": 10}))                                                  # 이제 보통 보다
    ok(bs.run("history.undo", {}))
    ok(bs.run("history.undo", {}))
    assert snap(bs) == before
    # 지우기: 자식 보까지 지우고 되돌리기가 모두 되살린다
    d = ok(bs.run("element.delete", {"id": F.BS1}))
    assert sorted(d["deleted"]) == sorted([F.BS1, *first]) and not any(b in bs.eng.st.el for b in first)
    assert ok(bs.run("model.stats", {}))["counts"].get("beamsys") is None and ok(bs.run("model.stats", {}))["counts"].get("beam") is None
    ok(bs.run("history.undo", {}))
    assert snap(bs) == before


def test_3_자식_보는_따로_고치지_못한다(bs):
    r = ok(bs.run("beamsys.create", {"id": F.BS1, "level": F.LV1, "type": BT, "boundary": RECT, "spacing": 1000, "offset": 2400}))
    c0 = r["children"][0]
    for name, args in (("beam.set", {"id": c0, "offset": 0}), ("beam.move", {"id": c0, "dx": 10}), ("element.delete", {"id": c0}),
                       ("beam.type.set", {"ids": [c0], "type": BG}), ("element.copy", {"ids": [c0], "dx": 100, "dy": 0}),
                       ("element.rotate", {"ids": [c0], "center": [0, 0], "angle": 10}), ("element.mirror", {"ids": [c0], "axis": [0, 0, 0, 1000]}),
                       ("material.set", {"id": c0, "name": "Steel"})):
        e = err(bs.run(name, args))
        assert e["code"] == "CONSTRAINT" and "보 시스템" in e["message"], (name, e)
    assert "beamsys.set" in err(bs.run("beam.set", {"id": c0, "offset": 0}))["hint"]
    # 태그는 달 수 있고 자식이 지워지면 고아로 남는다(조용히 지우지 않는다)
    t = ok(bs.run("annot.tag.create", {"view": V, "target": c0, "fields": ["name"]}))["id"]
    ok(bs.run("beamsys.set", {"id": F.BS1, "spacing": 2000}))                                            # 자식이 줄어도 태그 요소는 남는다
    assert t in bs.eng.st.el
    # 시스템 요소 자체는 보 명령으로 못 고친다
    assert err(bs.run("beam.set", {"id": F.BS1, "offset": 0}))["code"] in ("NOT_FOUND", "CONSTRAINT")
    # 시스템 종류가 아닌 요소에 beamsys 명령
    assert err(bs.run("beamsys.set", {"id": c0, "spacing": 500}))["code"] in ("NOT_FOUND", "CONSTRAINT")
    # element.copy 가 시스템 자체를 대상으로 하는 것은 이번 범위 밖이다(대상 종류가 아니다)
    assert err(bs.run("element.copy", {"ids": [F.BS1], "dx": 100, "dy": 0}))["code"] == "CONSTRAINT"
    assert ok(bs.run("model.validate", {}))["errors"] == 0


def test_3_유형을_고치면_자식이_따라가고_쓰는_유형은_못_지운다(bs):
    r = ok(bs.run("beamsys.create", {"id": F.BS1, "level": F.LV1, "type": BT, "boundary": RECT, "spacing": 1000, "offset": 2400}))
    ok(bs.run("beamtype.set", {"id": BT, "width": 250, "depth": 500, "material": "SS275"}))
    for b in r["children"]:
        q = get(bs, b)["params"]
        assert q["section"]["width"] == 250.0 and q["section"]["depth"] == 500.0 and q["material"] == "SS275" and q["bsys"] == F.BS1
    assert err(bs.run("beamtype.delete", {"id": BT}))["code"] == "CONSTRAINT"                    # 보가 쓴다
    e = err(bs.run("beamtype.delete", {"id": BT, "detach": True}))                                # 시스템은 유형으로 자식을 다시 만든다: 떼어 낼 수 없다
    assert e["code"] == "CONSTRAINT" and F.BS1 in e["details"]["systems"]
    assert err(bs.run("element.delete", {"id": BT}))["code"] == "CONSTRAINT"
    ok(bs.run("beamsys.ungroup", {"id": F.BS1}))
    assert ok(bs.run("beamtype.delete", {"id": BT, "detach": True}))["detached"] == 4              # 풀면 보통 보라 떼어 낼 수 있다


def test_3_거절(bs):
    base = {"level": F.LV1, "type": BT, "boundary": RECT, "spacing": 1000}

    def bad(code, **kw):
        a = {**base, **kw}
        for k in [k for k, v in a.items() if v is None]:
            a.pop(k)
        e = err(bs.run("beamsys.create", a))
        assert e["code"] in code if isinstance(code, tuple) else e["code"] == code, e
        return e
    bad("CONSTRAINT", boundary=[[0, 0], [6000, 4000], [6000, 0], [0, 4000]])                       # 자기 교차
    bad("CONSTRAINT", boundary=[[0, 0], [6000, 0], [3000, 0]])                                     # 면적 0
    bad(("SCHEMA_INVALID", "SCHEMA"), boundary=[[0, 0], [6000, 0]])                                 # 꼭짓점 둘
    bad(("SCHEMA_INVALID", "SCHEMA"), spacing=1000, count=3)                                        # 간격과 개수를 함께
    bad(("SCHEMA_INVALID", "SCHEMA"), spacing=None)                                                 # 둘 다 없음
    bad(("SCHEMA_INVALID", "SCHEMA"), boundary=None)                                                # 경계 없음
    bad(("SCHEMA_INVALID", "SCHEMA"), from_elements=GB)                                             # 경계 둘
    bad(("SCHEMA_INVALID", "SCHEMA"), spacing=10)                                                   # 간격 하한 50
    bad(("SCHEMA_INVALID", "SCHEMA"), spacing=None, count=0)
    bad(("SCHEMA_INVALID", "SCHEMA"), spacing=None, count=BM.SYS_COUNT_MAX + 1)
    bad(("SCHEMA_INVALID", "SCHEMA"), angle=180)
    bad(("SCHEMA_INVALID", "SCHEMA"), angle=-1)
    bad("CONSTRAINT", spacing=5000, align="start")                                                  # 보가 하나도 안 생긴다
    e = bad("CONSTRAINT", spacing=50, boundary=[[0, 0], [100000, 0], [100000, 40000], [0, 40000]])  # 800개 > 500
    assert "500" in e["message"]
    bad("NOT_FOUND", type="9" * 22)
    bad("NOT_FOUND", level="9" * 22)
    bad("NOT_FOUND", level=BT)                                                                      # 레벨이 아니다(그 id 의 레벨이 없다)
    assert not bs.eng.st.by_kind.get("beamsys") and not bs.eng.st.by_kind.get("beam")               # 거절은 아무것도 남기지 않는다
    ok(bs.run("beamsys.create", {"id": F.BS1, **base}))
    e = err(bs.run("beamsys.create", {"id": F.BS1, **base}))
    assert e["code"] == "CONSTRAINT"                                                                # 같은 id
    e = err(bs.run("beamsys.set", {"id": F.BS1, "spacing": 5000, "align": "start"}))                # 고쳐서 보가 없어지는 것도 거절하고 그대로 둔다
    assert e["code"] == "CONSTRAINT" and len(kids(bs, F.BS1)) == 4
    assert err(bs.run("beamsys.set", {"id": F.BS1}))["code"] in ("SCHEMA_INVALID", "SCHEMA")
    assert err(bs.run("beamsys.set", {"id": F.BS1, "spacing": 500, "count": 2}))["code"] in ("SCHEMA_INVALID", "SCHEMA")
    ids = [x for x in bs.eng.st.by_kind["beam"]][:2]
    assert err(bs.run("beamsys.create", {"id": BS2, **base, "child_ids": [ids[0]]}))["code"] == "CONSTRAINT"   # 이미 있는 id 를 자식으로


def test_3_저장하고_다시_열기(tmp_path):
    s = kit.Session(tmp_path / "b.dobim")
    try:
        for name, args in F.SCRIPT:
            ok(s.run(name, args))
        ok(s.run("beamtype.create", {"id": BT, "name": "S1", "width": 200, "depth": 400}))
        ok(s.run("beamsys.create", {"id": F.BS1, "level": F.LV1, "type": BT, "boundary": RECT, "spacing": 800, "align": "start", "angle": 20, "offset": 2400}))
        ok(s.run("brace.create", {"id": F.BR1, "level": F.LV1, "type": BT, "start": [0, -3000, 0], "end": [3000, -3000, 3000]}))
        before = snap(s)
        lst = ok(s.run("beam.list", {}))["items"]
    finally:
        s.close()
    s = kit.Session(tmp_path / "b.dobim", create=False)
    try:
        assert snap(s) == before and ok(s.run("beam.list", {}))["items"] == lst
        assert ok(s.run("model.validate", {}))["errors"] == 0
        assert ok(s.run("beamsys.set", {"id": F.BS1, "spacing": 900}))["changed"] is True            # 다시 열어도 고칠 수 있다
    finally:
        s.close()


# ---------------------------------------------------------------- 4. 출력
def test_4_도면과_일람표(bs):
    girders(bs)
    r = ok(bs.run("beamsys.create", {"id": F.BS1, "level": F.LV1, "type": BT, "from_elements": GB, "spacing": 1000, "offset": 2400}))
    c0 = sorted(r["children"], key=lambda b: seg(bs, b)[0][1])[0]
    g = g_of(bs)
    # 평면: 절단 높이(1200) 위의 보는 숨은선 파선, 외곽 사각형(자른 길이 5600 x 폭 200)
    hid = g.q(D.beam_lines, V, c0)
    assert hid and all(x[0] == "proj" for x in hid)
    xs = sorted({x[1] for x in hid} | {x[3] for x in hid})
    assert xs[0] == pytest.approx(200.0) and xs[-1] == pytest.approx(5800.0)                          # 경계 보 옆면에서 끝난다
    # 단면: 시스템을 가로지르는 절단(x = 3000)은 작은 보 4개 + 경계 보 2개의 단면
    ok(bs.run("view.create", {"id": V2, "name": "S", "type": "section", "p1": [3000, -9500], "p2": [3000, -4500]}))
    g = g_of(bs)
    f = [pl for b in r["children"] for pl in g.q(D.beam_fills, V2, b)]
    assert len(f) == 4 and all(abs(D._area2(list(pl))) / 2.0 == pytest.approx(200 * 400) for pl in f)
    same_as_full(bs, (V, V2))
    ok(bs.run("beamsys.set", {"id": F.BS1, "spacing": 1500, "align": "start"}))
    same_as_full(bs, (V, V2))
    ok(bs.run("beamsys.move", {"id": F.BS1, "dx": 100}))
    same_as_full(bs, (V, V2))
    ok(bs.run("history.undo", {}))
    ok(bs.run("history.undo", {}))
    same_as_full(bs, (V, V2))
    # GUI 장면: 자식 보가 요소별로 들어간다
    from dobim_core import scene as SC
    sc = g_of(bs).q(SC.view_scene, V2)
    assert {t[1] for t in sc if t[0] == "beam"} >= set(r["children"])
    # 일람표·재료 물량·model.stats
    t = ok(bs.run("schedule.table", {"category": "beams", "fields": ["name", "type", "length", "volume"], "units": "mm"}))
    kids_rows = [x for x in t["items"] if x[1] == "S1"]
    assert len(kids_rows) == 4 and all(x[2] == pytest.approx(5600.0) and x[3] == pytest.approx(200 * 400 * 5600.0) for x in kids_rows)
    to = ok(bs.run("schedule.takeoff", {"categories": ["beams"], "by_level": False}))
    assert to["items"][0][3] >= 8
    st = ok(bs.run("model.stats", {}))["counts"]
    assert st["beamsys"] == 1 and st["beam"] == 8 and st["beamtype"] == 2


def test_4_IFC_보_시스템(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("beamtype.create", {"id": IBT, "name": "B1", "preset": "H-400x200x8x13", "material": "SS275"}))
        ok(b.run("beamsys.create", {"id": IBS, "level": F.LV1, "type": IBT, "boundary": [[0, -9000], [6000, -9000], [6000, -5000], [0, -5000]], "spacing": 1000, "offset": 2400}))
        ok(b.run("beam.create", {"id": "3" + "0" * 19 + "n1", "level": F.LV1, "type": IBT, "start": [0, -3000], "end": [6000, -3000], "offset": 2400}))
        h = b.hashes()
        out = b.export("b.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        asm = f.by_guid(IBS)
        assert asm.is_a("IfcElementAssembly") and asm.PredefinedType == "USERDEFINED" and asm.ObjectType == "BEAM_SYSTEM" and asm.Representation is None
        parts = [x for r in asm.IsDecomposedBy for x in r.RelatedObjects]
        assert len(parts) == 4 and all(x.is_a("IfcBeam") and x.PredefinedType == "BEAM" for x in parts)
        assert len(asm.IsDecomposedBy) == 1
        # 집합의 부분은 층에 담기지 않고 시스템 전체만 층에 담긴다
        assert all(not x.ContainedInStructure for x in parts) and [r.RelatingStructure.Name for r in asm.ContainedInStructure] == ["1F"]
        # 자식 보는 보통 보처럼 프로필 몸체 + 유형 + 구조 재료 사용
        sw = parts[0].Representation.Representations[0].Items[0]
        assert sw.is_a("IfcExtrudedAreaSolid") and sw.SweptArea.is_a("IfcIShapeProfileDef") and sw.Depth == pytest.approx(6000.0)   # 경계 보가 없어 자르지 않는다
        assert [r.RelatingType.Name for r in parts[0].IsTypedBy] == ["B1"] and len(f.by_type("IfcMaterialProfileSetUsage")) == 5
        assert b.destructive("ifc.export", {"path": "b3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "b3.ifc"))
        assert file_errors(f3) == [] and f3.by_guid(IBS).is_a("IfcElementAssembly") and len([x for r in f3.by_guid(IBS).IsDecomposedBy for x in r.RelatedObjects]) == 4
        r = b.import_ifc("b.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"] and r["result"]["elements"]["detached"] == 0, r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)                                             # 시스템·자식·유형까지 같다
        assert b.export("b2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        assert ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
        ok(b.run("beamsys.set", {"id": IBS, "spacing": 2000}, project="imp.dobim"))                  # 가져온 시스템도 고칠 수 있다
        assert len(kids_of(b, IBS, "imp.dobim")) == 2
        # 고치고 되돌리기·지우기·풀기가 IFC 를 원래대로 돌린다
        ok(b.run("beamsys.set", {"id": IBS, "spacing": 2000}))
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
        ok(b.run("element.delete", {"id": IBS}))
        assert not [e for e in ifcopenshell.open(str(b.export("b4.ifc"))).by_type("IfcElementAssembly")]
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
        ok(b.run("beamsys.ungroup", {"id": IBS}))
        f5 = ifcopenshell.open(str(b.export("b5.ifc")))
        assert not f5.by_type("IfcElementAssembly") and all(x.ContainedInStructure for x in f5.by_type("IfcBeam")) and file_errors(f5) == []
    finally:
        b.close()


def kids_of(b, sid, project):
    return ok(b.run("element.get", {"id": sid}, project=project))["params"]["children"]


# ---------------------------------------------------------------- 5. 가새
def test_5_가새_만들기와_수량(bs):
    r = ok(bs.run("brace.create", {"id": F.BR1, "level": F.LV1, "type": BT, "start": [0, -3000, 0], "end": [3000, -3000, 3000], "name": "BR-A"}))
    assert r == {"id": F.BR1}
    p = get(bs, F.BR1)["params"]
    assert p["brace"] is True and p["start"] == [0.0, -3000.0] and p["end"] == [3000.0, -3000.0] and p["offset"] == 0.0 and p["end_offset"] == 3000.0
    assert p["anchor"] == "center" and p["btype"] == BT and bs.eng.st.el[F.BR1]["ifc_class"] == "IfcMember"
    it = ok(bs.run("beam.list", {}))["items"][0]
    assert it["brace"] is True and it["slope_deg"] == 45.0 and it["length"] == pytest.approx(3000 * sqrt(2))
    assert it["volume_m3"] == pytest.approx(200 * 400 * 3000 * sqrt(2) / 1e9, rel=1e-5)
    # 높이가 같으면 수평 가새(평면 대각 가새), 거꾸로 내려가는 가새도 된다
    ok(bs.run("brace.create", {"id": "9" + "0" * 19 + "r1", "level": F.LV1, "type": BT, "start": [0, -6000, 2000], "end": [4000, -4000, 2000]}))
    assert "end_offset" not in get(bs, "9" + "0" * 19 + "r1")["params"]
    ok(bs.run("brace.create", {"id": "9" + "0" * 19 + "r2", "level": F.LV1, "type": BT, "start": [0, -8000, 3000], "end": [1000, -8000, 0]}))
    assert get(bs, "9" + "0" * 19 + "r2")["params"]["end_offset"] == 0.0
    # 고치기: 두 점 한쪽만 주면 다른 쪽은 그대로
    ok(bs.run("brace.set", {"id": F.BR1, "end": [3000, -3000, 2000], "anchor": "top", "name": "BR-B"}))
    p = get(bs, F.BR1)["params"]
    assert p["start"] == [0.0, -3000.0] and p["offset"] == 0.0 and p["end_offset"] == 2000.0 and p["anchor"] == "top" and p["name"] == "BR-B" and p["brace"] is True
    ok(bs.run("brace.set", {"id": F.BR1, "start": [0, -3000, 2000]}))                                   # 두 높이가 같아지면 수평
    assert "end_offset" not in get(bs, F.BR1)["params"] and get(bs, F.BR1)["params"]["brace"] is True
    ok(bs.run("brace.set", {"id": F.BR1, "end": [3000, -3000, 3000], "level": F.LV2}))
    assert ok(bs.run("beam.list", {"level": F.LV2}))["items"][0]["id"] == F.BR1
    ok(bs.run("history.undo", {}))
    assert ok(bs.run("beam.list", {"level": F.LV1}))["items"][0]["brace"] is True
    # 보 명령도 표식을 지키고(beam.set·move·type.set) 보와 같은 유형 단면을 쓴다
    ok(bs.run("beam.set", {"id": F.BR1, "gap": 50, "roll": 10}))
    ok(bs.run("beam.move", {"id": F.BR1, "dz": 100}))
    ok(bs.run("beamtype.set", {"id": BT, "preset": "H-300x150x6.5x9"}))
    p = get(bs, F.BR1)["params"]
    assert p["brace"] is True and p["section"]["shape"] == "h" and p["gap"] == 50.0 and p["roll"] == 10.0
    assert ok(bs.run("model.validate", {}))["errors"] == 0
    # 거절
    e = err(bs.run("brace.create", {"level": F.LV1, "type": BT, "start": [0, 0, 0], "end": [100, 0, 3000]}))
    assert e["code"] == "CONSTRAINT" and "80도" in e["message"]                                          # 거의 선 것은 기둥의 몫
    assert err(bs.run("brace.create", {"level": F.LV1, "type": BT, "start": [0, 0, 0], "end": [0, 0, 3000]}))["code"] == "CONSTRAINT"   # 수직
    assert err(bs.run("brace.create", {"level": F.LV1, "type": BT, "start": [0, 0, 0], "end": [5, 0, 0]}))["code"] == "CONSTRAINT"       # 너무 짧다
    assert err(bs.run("brace.create", {"level": F.LV1, "type": BT, "start": [0, 0], "end": [3000, 0, 0]}))["code"] in ("SCHEMA_INVALID", "SCHEMA")
    assert err(bs.run("brace.create", {"level": F.LV1, "type": BT, "start": [0, 0, 0], "end": [3000, 0, 3000], "vertical_ends": True, "gap": 2000}))["code"] in ("SCHEMA_INVALID", "SCHEMA")
    assert err(bs.run("brace.set", {"id": "9" + "0" * 19 + "n9", "name": "x"}))["code"] == "NOT_FOUND"
    ok(bs.run("beam.create", {"id": "9" + "0" * 19 + "n9", "level": F.LV1, "type": BT, "start": [0, 2000], "end": [3000, 2000]}))
    assert err(bs.run("brace.set", {"id": "9" + "0" * 19 + "n9", "name": "x"}))["code"] == "CONSTRAINT"       # 보는 가새가 아니다
    assert err(bs.run("beam.set", {"id": F.BR1, "bulge": 0.4}))["code"] == "CONSTRAINT"                 # 가새는 직선이다


def test_5_가새_끝_자르기(bs):
    column(bs, C1, (0, 0))
    ok(bs.run("brace.create", {"id": F.BR1, "level": F.LV1, "type": BT, "start": [0, 0, 0], "end": [3000, 0, 3000]}))
    it = [x for x in ok(bs.run("beam.list", {}))["items"] if x["id"] == F.BR1][0]
    assert it["start_cut"] == {"column": C1, "length": pytest.approx(250 * sqrt(2), abs=1e-3)}            # 기둥 면(평면 250 mm)까지 축 방향으로 환산해 줄인다
    assert it["cut_length"] == pytest.approx(3000 * sqrt(2) - 250 * sqrt(2), abs=1e-3)
    ok(bs.run("brace.set", {"id": F.BR1, "cutback": False}))
    assert "start_cut" not in [x for x in ok(bs.run("beam.list", {}))["items"] if x["id"] == F.BR1][0]
    ok(bs.run("brace.set", {"id": F.BR1, "cutback": True, "vertical_ends": True}))                        # 끝면을 기둥 면과 나란한 연직면으로
    it = [x for x in ok(bs.run("beam.list", {}))["items"] if x["id"] == F.BR1][0]
    assert it["vertical_ends"] is True and it["start_cut"]["column"] == C1
    ends = g_of(bs).q(Q.beam_ends, F.BR1)
    assert ends is not None
    # 가새 끝이 보 옆면에 닿으면 그 면까지(끝점 자르기): 큰 보 옆면
    ok(bs.run("beam.create", {"id": GB[0], "level": F.LV1, "type": BG, "start": [3000, -2000], "end": [3000, 2000], "offset": 3000, "anchor": "center"}))
    ok(bs.run("brace.set", {"id": F.BR1, "cutback": True, "vertical_ends": False}))
    it = [x for x in ok(bs.run("beam.list", {}))["items"] if x["id"] == F.BR1][0]
    assert it["end_cut"] == {"beam": GB[0], "length": pytest.approx(200 * sqrt(2), abs=1e-3)}             # 보 폭 400 의 반만큼 평면에서 줄인다
    ok(bs.run("beam.move", {"id": GB[0], "dx": 500}))                                                     # 접합은 파생값: 보를 옮기면 따라간다
    assert "end_cut" not in [x for x in ok(bs.run("beam.list", {}))["items"] if x["id"] == F.BR1][0]


def test_5_가새_복사와_대칭_도면(bs):
    ok(bs.run("brace.create", {"id": F.BR1, "level": F.LV1, "type": BT, "start": [0, -3000, 0], "end": [3000, -3000, 3000]}))
    c = ok(bs.run("element.copy", {"ids": [F.BR1], "dx": 0, "dy": -2000, "new_ids": ["9" + "0" * 19 + "r5"]}))
    cp = get(bs, "9" + "0" * 19 + "r5")
    assert cp["params"]["brace"] is True and cp["params"]["start"] == [0.0, -5000.0] and bs.eng.st.el["9" + "0" * 19 + "r5"]["ifc_class"] == "IfcMember"
    ok(bs.run("element.mirror", {"ids": [F.BR1], "axis": [1500, 0, 1500, 1000], "keep": True, "new_ids": ["9" + "0" * 19 + "r6"]}))
    mp = get(bs, "9" + "0" * 19 + "r6")["params"]
    assert mp["brace"] is True and mp["start"] == [3000.0, -3000.0] and mp["end"] == [0.0, -3000.0] and mp["offset"] == 0.0 and mp["end_offset"] == 3000.0
    ok(bs.run("element.rotate", {"ids": [F.BR1], "center": [0, -3000], "angle": 90}))
    assert get(bs, F.BR1)["params"]["end"] == pytest.approx([0.0, 0.0], abs=1e-3) and get(bs, F.BR1)["params"]["brace"] is True
    # 평면: 경사 보 규약(평면 외곽 사각형, 절단 높이 아래 걸친 부분의 채움). 입면·단면에서 보인다
    VS = "4" + "0" * 19 + "e8"
    ok(bs.run("view.create", {"id": VS, "name": "남측", "type": "elevation", "dir": "S"}))
    ok(bs.run("view.create", {"id": V2, "name": "S", "type": "section", "p1": [1500, -4000], "p2": [1500, -2000]}))
    g = g_of(bs)
    pl = g.q(D.beam_lines, V, "9" + "0" * 19 + "r5")
    assert pl                                                                                              # 평면에 그린다(기울어 절단 높이에 걸친 부분은 채움)
    sec = g.q(D.beam_lines, V2, "9" + "0" * 19 + "r5")
    assert any(x[0] == "cut" for x in sec) and len(g.q(D.beam_fills, V2, "9" + "0" * 19 + "r5")) == 1
    el = g.q(D.view_lines, VS)
    assert el
    same_as_full(bs, (V, V2, VS))
    ok(bs.run("brace.set", {"id": "9" + "0" * 19 + "r5", "end": [3000, -5000, 1000]}))
    same_as_full(bs, (V, V2, VS))
    # 태그: 보와 같다
    ok(bs.run("annot.tag.create", {"view": V, "target": "9" + "0" * 19 + "r5", "fields": ["type", "size"]}))
    # 일람표: 보 범주에 IfcMember 행
    t = ok(bs.run("schedule.table", {"category": "beams", "fields": ["name", "ifc_class", "length"], "units": "mm"}))
    assert {x[1] for x in t["items"]} == {"IfcMember"}


def test_5_입면에서_가새가_가린다(bs):
    """경사 보와 같은 가림 조각: 가새가 벽 윗선의 일부를 가린다."""
    VS = "4" + "0" * 19 + "e8"
    ok(bs.run("view.create", {"id": VS, "name": "남측", "type": "elevation", "dir": "S"}))
    ok(bs.run("brace.create", {"id": F.BR1, "level": F.LV1, "type": BG, "start": [500, 800, 3600], "end": [5500, 800, 2400], "anchor": "center"}))
    top = lambda: [x for x in g_of(bs).q(D.vwall_lines, VS, F.W1) if abs(x[2] - 3000) < 1e-6 and abs(x[4] - 3000) < 1e-6]
    assert len(g_of(bs).q(D._beam_vpieces, VS, F.BR1)) > 0
    ok(bs.run("element.delete", {"id": F.BR1}))
    assert top()


# ---------------------------------------------------------------- 6. 가새 IFC
def test_6_IFC_가새(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("family.place", {"id": "1" + "0" * 19 + "c7", "family": "column.section", "type_values": {"section": "rect", "width": 500, "depth": 500},
                                  "values": {"height": 3300}, "level": F.LV1, "position": [0, -3000]}))
        ok(b.run("beamtype.create", {"id": IBT, "name": "SG1", "preset": "H-300x150x6.5x9", "material": "SS275"}))
        ok(b.run("beam.create", {"id": "3" + "0" * 19 + "n1", "level": F.LV1, "type": IBT, "start": [0, -6000], "end": [6000, -6000], "offset": 2400}))
        ok(b.run("brace.create", {"id": IBR, "level": F.LV1, "type": IBT, "start": [0, -3000, 0], "end": [3000, -3000, 3000], "vertical_ends": True}))
        ok(b.run("beamtype.create", {"id": "3" + "0" * 19 + "c8", "name": "PB", "preset": "P-114.3x4.5"}))
        ok(b.run("brace.create", {"id": "3" + "0" * 19 + "r8", "level": F.LV1, "type": "3" + "0" * 19 + "c8", "start": [6000, -3000, 0], "end": [3000, -3000, 3000]}))
        h = b.hashes()
        out = b.export("b.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        e = f.by_guid(IBR)
        assert e.is_a("IfcMember") and e.PredefinedType == "BRACE"
        sw = e.Representation.Representations[0].Items
        assert sw[0].is_a("IfcBooleanClippingResult")                                                        # 연직 끝면은 반공간 자르기
        t = [r.RelatingType for r in e.IsTypedBy]
        assert t and t[0].is_a("IfcMemberType") and t[0].Name == "SG1" and t[0].PredefinedType == "BRACE"     # IfcMember 는 IfcMemberType 을 쓴다
        assert sorted(x.Name for x in f.by_type("IfcMemberType")) == ["PB", "SG1"] and sorted(x.Name for x in f.by_type("IfcBeamType")) == ["PB", "SG1"]
        bm = f.by_guid("3" + "0" * 19 + "n1")
        assert bm.is_a("IfcBeam") and [r.RelatingType.is_a("IfcBeamType") for r in bm.IsTypedBy] == [True]   # 같은 유형을 쓰는 보는 IfcBeamType
        mt = [x for x in f.by_type("IfcMemberType") if x.Name == "SG1"][0]
        assert any(r.RelatingMaterial.is_a("IfcMaterialProfileSet") for r in mt.HasAssociations)             # 유형 재료
        assert sorted(u.CardinalPoint for u in f.by_type("IfcMaterialProfileSetUsage")) == [5, 8]               # 가새(center)·보(top)
        assert b.destructive("ifc.export", {"path": "b3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "b3.ifc"))
        assert file_errors(f3) == [] and f3.by_guid(IBR).is_a("IfcMember") and len(f3.by_type("IfcMemberType")) == 2
        r = b.import_ifc("b.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"] and r["result"]["elements"]["detached"] == 0, r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)                                                     # 가새 표식·IfcMember 클래스·유형까지 같다
        assert b.export("b2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        p = ok(b.run("element.get", {"id": IBR}, project="imp.dobim"))
        assert p["params"]["brace"] is True
        # 되돌리기·지우기: 유형 엔티티까지 따라간다
        ok(b.run("beamtype.set", {"id": IBT, "preset": "H-200x100x5.5x8"}))
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
        ok(b.run("element.delete", {"id": IBR}))
        ok(b.run("element.delete", {"id": "3" + "0" * 19 + "r8"}))
        f4 = ifcopenshell.open(str(b.export("b4.ifc")))
        assert not f4.by_type("IfcMember") and not f4.by_type("IfcMemberType") and file_errors(f4) == []
        ok(b.run("history.undo", {}))
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()
