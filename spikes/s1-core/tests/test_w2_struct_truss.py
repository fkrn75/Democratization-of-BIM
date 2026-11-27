# SPDX-License-Identifier: MPL-2.0
"""W2 스물다섯 번째 PR(구조 보 6부: 트러스와 보 시스템 후속).

1. 트러스 순수 형상(BM.truss_members): Pratt·Howe·Warren 부재 수·마디·사재 방향·경사 트러스·검사
2. 트러스 명령: 만들기·재현(손으로 놓은 보·가새와 같은 형상)·고치기(자식 재생성·id 이어받기)·옮기기·지우기·복사·회전·대칭·거절
3. 소유 규약: 자식 부재를 따로 고치면 거절(태그는 허용)·유형 추종·유형 지우기 보호·되돌리기·저장
4. 트러스 출력: 도면(평면·입면·단면 채움·증분 = 전체)·일람표·IFC(IfcElementAssembly TRUSS 집합·검증·왕복)
5. 보 시스템 복사·회전·대칭(자식 재생성)과 시스템·트러스 태그
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import sys
from math import atan2, degrees, hypot, sqrt

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import annot as AN
from dobim_core import beams as BM
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V, V2, V3 = F.V1, F.V2, "4" + "0" * 19 + "v3"
BT, BW = ("9" + "0" * 19 + x for x in ("c1", "c2"))                      # 보 유형: 현 H-250 계열, 웹 L 형강
TR2 = "9" + "0" * 19 + "t2"
IBT, IBW, ITR = ("3" + "0" * 19 + x for x in ("c8", "c9", "t8"))        # IFC 왕복용(첫 글자가 유효한 GlobalId)


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
def ts(base):
    ok(base.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(base.run("beamtype.create", {"id": BT, "name": "TC1", "preset": "H-250x125x6x9"}))
    ok(base.run("beamtype.create", {"id": BW, "name": "TW1", "preset": "L-75x75x9"}))
    return base


def tp(**kw):
    """순수 형상 시험용 트러스 params."""
    p = {"name": "T", "start": [0.0, 0.0], "end": [12000.0, 0.0], "offset": 3000.0, "height": 2000.0, "pattern": "pratt", "panels": 4, "btype": BT, "children": []}
    p.update(kw)
    return p


def mk(s, **kw):
    a = {"id": F.TR1, "level": F.LV1, "type": BT, "start": [0, 0, 3000], "end": [12000, 0, 3000], "height": 2000, "pattern": "pratt", "panels": 4}
    a.update(kw)
    return ok(s.run("truss.create", a))


def kids(s, tid=F.TR1):
    return list(get(s, tid)["params"]["children"])


def snap(s):
    st = s.eng.st
    return {e: st.el[e]["params"] for k in ("beam", "beamsys", "truss") for e in st.by_kind.get(k, ())}


def tags(s, v=V):
    """뷰의 태그 그림 항목 {태그 id: 항목}."""
    return {it[1]: it for it in g_of(s).q(AN.view_annots, v) if it[0] == "tag"}


def text_of(s, t):
    st = g_of(s).q(AN.annot_state, t)
    assert st[:2] == ("tag", "ok"), st
    return st[2]


def by_name(members):
    return {m[1]: m for m in members}


# ---------------------------------------------------------------- 1. 순수 형상
def test_1_Pratt_부재와_마디():
    mem = BM.truss_members(tp())
    assert [m[0] for m in mem] == ["chord"] * 2 + ["post"] * 5 + ["diag"] * 4                       # 현 2 + 수직재 칸 + 1 + 사재 칸
    d = by_name(mem)
    assert d["BC"][2:] == ((0.0, 0.0, 3000.0), (12000.0, 0.0, 3000.0)) and d["TC"][2:] == ((0.0, 0.0, 5000.0), (12000.0, 0.0, 5000.0))   # 상현 = 하현 + 높이
    assert [d[f"V{i}"][2:] for i in range(5)] == [((3000.0 * i, 0.0, 3000.0), (3000.0 * i, 0.0, 5000.0)) for i in range(5)]               # 수직재: 모든 마디, 아래 -> 위
    # 사재는 가운데를 향해 내려간다(바깥 위 마디 -> 안쪽 아래 마디): 왼쪽 두 칸은 위 -> 아래, 오른쪽 두 칸은 아래 -> 위(왼쪽 마디 -> 오른쪽 마디 순서)
    assert d["D1"][2:] == ((0.0, 0.0, 5000.0), (3000.0, 0.0, 3000.0)) and d["D2"][2:] == ((3000.0, 0.0, 5000.0), (6000.0, 0.0, 3000.0))
    assert d["D3"][2:] == ((6000.0, 0.0, 3000.0), (9000.0, 0.0, 5000.0)) and d["D4"][2:] == ((9000.0, 0.0, 3000.0), (12000.0, 0.0, 5000.0))
    # 칸 수 2: 사재 둘이 대칭
    d2 = by_name(BM.truss_members(tp(panels=2)))
    assert d2["D1"][2:] == ((0.0, 0.0, 5000.0), (6000.0, 0.0, 3000.0)) and d2["D2"][2:] == ((6000.0, 0.0, 3000.0), (12000.0, 0.0, 5000.0)) and len(d2) == 2 + 3 + 2


def test_1_Howe와_Warren():
    d = by_name(BM.truss_members(tp(pattern="howe")))
    assert len(d) == 11 and "V2" in d
    # Howe 는 Pratt 의 반대: 사재가 가운데를 향해 올라간다
    assert d["D1"][2:] == ((0.0, 0.0, 3000.0), (3000.0, 0.0, 5000.0)) and d["D2"][2:] == ((3000.0, 0.0, 3000.0), (6000.0, 0.0, 5000.0))
    assert d["D3"][2:] == ((6000.0, 0.0, 5000.0), (9000.0, 0.0, 3000.0)) and d["D4"][2:] == ((9000.0, 0.0, 5000.0), (12000.0, 0.0, 3000.0))
    w = BM.truss_members(tp(pattern="warren", panels=5))
    assert [m[0] for m in w] == ["chord", "chord"] + ["diag"] * 5                                   # 수직재 없이 사재만(칸 수 홀수도 된다)
    dw = by_name(w)
    ends = [(dw[f"D{k}"][2], dw[f"D{k}"][3]) for k in range(1, 6)]
    for k, (a, b) in enumerate(ends):                                                                 # 지그재그: 짝수 칸 아래 -> 위, 홀수 칸 위 -> 아래
        assert (a[2], b[2]) == ((3000.0, 5000.0) if k % 2 == 0 else (5000.0, 3000.0))
        assert a[0] == 2400.0 * k and b[0] == 2400.0 * (k + 1)
    # 사재는 마디에서 이웃 사재와 만난다(이어진 지그재그)
    assert all(ends[k][1] == ends[k + 1][0] for k in range(4))


def test_1_경사_트러스와_마디_좌표():
    p = tp(end_offset=4500.0, end=[10000.0, 6000.0], panels=3, pattern="warren")
    bot, top = BM.truss_nodes(p)
    assert len(bot) == 4 and bot[0] == (0.0, 0.0, 3000.0) and bot[3] == (10000.0, 6000.0, 4500.0)
    assert bot[1] == (3333.333333, 2000.0, 3500.0) and bot[2] == (6666.666667, 4000.0, 4000.0)       # 평면은 칸을 같게, 높이는 선형
    assert all(t[:2] == b[:2] and t[2] == b[2] + 2000.0 for b, t in zip(bot, top))
    assert BM.truss_dir_deg(p) == pytest.approx(degrees(atan2(6000, 10000)), abs=1e-5)
    assert BM.truss_dir_deg(tp(end=[0.0, 5000.0])) == 90.0 and BM.truss_dir_deg(tp(end=[-5000.0, 0.0])) == 180.0 and BM.truss_dir_deg(tp(end=[0.0, -5000.0])) == -90.0


def test_1_검사():
    assert BM.truss_problem(tp()) is None and BM.truss_problem(tp(pattern="warren", panels=7)) is None
    assert "짝수" in BM.truss_problem(tp(panels=5)) and "짝수" in BM.truss_problem(tp(pattern="howe", panels=3))   # Pratt·Howe 는 가운데 칸이 없어야 한다
    assert "칸 수" in BM.truss_problem(tp(panels=1)) and "칸 수" in BM.truss_problem(tp(panels=BM.TRUSS_PANELS_MAX + 1))
    assert "높이" in BM.truss_problem(tp(height=50.0)) and "높이" in BM.truss_problem(tp(height=BM.TRUSS_HEIGHT_MAX + 1))
    assert "평면 길이" in BM.truss_problem(tp(end=[300.0, 0.0]))                                       # 칸 하나가 100 mm 미만
    assert "80도" in BM.truss_problem(tp(panels=2, end=[1000.0, 0.0], height=9000.0))                 # 사재가 너무 섰다
    assert "80도" in BM.truss_problem(tp(end_offset=3000.0 + 12000.0 * 6.0))                           # 경사 현이 80도를 넘는다
    assert "end_offset" in BM.truss_problem(tp(end_offset=3000.0))                                    # 같으면 키를 적지 않는다
    assert BM.truss_problem(tp(end=[60000.0, 0.0], panels=BM.TRUSS_PANELS_MAX)) is None                # 칸 100 개(부재 203 개)
    assert K.row_params_error("truss", tp()) is None and K.row_params_error("truss", tp(panels=4.0)) is not None   # 칸 수는 정수


def test_1_수직재_행의_기하():
    """수직재는 평면 길이 0 의 보 행(post_dir)이다: 경사 보의 90도 극한(축 = +z)이라 형상·평면 외곽·체적이 보와 같은 식이다."""
    sc = BM.preset_section("H-250x125x6x9")
    p = {"name": "V", "start": [1000.0, 2000.0], "end": [1000.0, 2000.0], "offset": 3000.0, "end_offset": 5000.0, "anchor": "center", "section": sc, "cutback": False,
         "brace": True, "truss": "9" * 22, "post_dir": 0.0}
    assert BM.beam_problem(p) is None
    assert BM.axis(p) == (1000.0, 2000.0, 1.0, 0.0, 0.0) and BM.slope(p) == (0.0, 1.0, 2000.0) and BM.length3(p) == 2000.0
    verts, faces = BM.mesh(p, 3000.0, (0.0, 0.0))
    zs = [v[2] for v in verts]
    assert min(zs) == 3000.0 and max(zs) == 5000.0                                                    # 수직 각기둥
    xs, ys = [v[0] for v in verts], [v[1] for v in verts]
    assert (min(xs), max(xs)) == (875.0, 1125.0) and (min(ys), max(ys)) == (1937.5, 2062.5)           # 단면 춤(250)은 진행 방향(x), 폭(125)은 왼쪽(y)
    assert BM.volume(p, 2000.0) == pytest.approx(BM.section_area(sc) * 2000.0)
    rect = BM.plan_rect(p, (0.0, 0.0))
    assert (min(q[0] for q in rect), max(q[0] for q in rect), min(q[1] for q in rect), max(q[1] for q in rect)) == (875.0, 1125.0, 1937.5, 2062.5)
    p90 = dict(p, post_dir=90.0)                                                                       # 트러스가 y 방향이면 춤이 y 로 놓인다
    v90, _ = BM.mesh(p90, 3000.0, (0.0, 0.0))
    assert (min(v[0] for v in v90), max(v[0] for v in v90)) == (937.5, 1062.5) and (min(v[1] for v in v90), max(v[1] for v in v90)) == (1875.0, 2125.0)
    assert BM.beam_problem({k: v for k, v in p.items() if k != "truss"}) is None                      # 트러스를 푼 뒤의 독립 수직재(truss 키가 없다)
    # 거절: 가새 표식·같은 평면 점·위로 선 길이·보 시스템 자식·곡선·단면 회전·간격
    for bad in (dict(p, brace=None), dict(p, end=[1000.0, 2001.0]), dict(p, end_offset=3000.0), dict(p, end_offset=2900.0), dict(p, end_offset=3005.0),
                dict(p, roll=10.0), dict(p, gap=50.0), dict(p, bsys="9" * 22), dict(p, bulge=0.3), dict(p, vertical_ends=True), dict(p, skew_cut=True),
                dict(p, cutback=None)):                                                                # 트러스 자식은 cutback false 여야 한다
        q = {k: v for k, v in bad.items() if v is not None}
        assert BM.beam_problem(q) is not None, bad
    assert K.row_params_error("beam", dict(p, post_dir=180.0)) is None and K.row_params_error("beam", dict(p, post_dir=-180.0)) is not None


# ---------------------------------------------------------------- 2. 명령
def tail(name: str) -> str:
    return name.rsplit("-", 1)[1]


def geom(s, tid=F.TR1) -> dict:
    """트러스 자식의 부재별 기하 {꼬리: (시작, 끝, offset, end_offset, post_dir)}."""
    out = {}
    for b in kids(s, tid):
        q = get(s, b)["params"]
        out[tail(q["name"])] = (tuple(q["start"]), tuple(q["end"]), q["offset"], q.get("end_offset", q["offset"]), q.get("post_dir"))
    return out


def flat(x):
    return [v for t in x for v in (flat(t) if isinstance(t, tuple) else [t])] if isinstance(x, tuple) else [x]


def same_geom(a: dict, b: dict) -> bool:
    if set(a) != set(b):
        return False
    return all(flat(a[k][:4]) == pytest.approx(flat(b[k][:4]), abs=1e-3) and (a[k][4] is None) == (b[k][4] is None) for k in a)


def test_2_이전에는_수직재를_못_놓고_트러스는_부재를_한_번에_낸다(ts):
    """재현: 트러스가 없으면 현·사재를 beam.create·brace.create 로 하나씩 놓았고, 수직재는 가새(80도 한계)로도 놓을 수 없었다. 트러스는 같은 형상을 한 번에 낸다."""
    e = err(ts.run("brace.create", {"level": F.LV1, "type": BT, "start": [0, 0, 3000], "end": [0, 0, 5000]}))
    assert e["code"] == "CONSTRAINT"                                                                  # 수직재(기둥 몫): 이전 동작 그대로
    r = mk(ts, pattern="warren")                                                                      # 수직재 없는 Warren 은 손으로 놓을 수 있다
    rows = {tail(x["name"]): x for x in ok(ts.run("beam.list", {}))["items"]}
    assert len(rows) == 6 and set(rows) == {"BC", "TC", "D1", "D2", "D3", "D4"}
    ok(ts.run("element.delete", {"id": F.TR1}))                                                       # 지우고 같은 부재를 하나씩 놓는다
    assert not ts.eng.st.by_kind.get("beam")
    for nm, off in (("BC", 3000), ("TC", 5000)):
        ok(ts.run("beam.create", {"id": "9" + "0" * 19 + f"m{nm[0]}", "level": F.LV1, "type": BT, "start": [0, 0], "end": [12000, 0], "offset": off, "anchor": "center",
                                  "cutback": False, "name": f"M-{nm}"}))
    for k, (x0, x1, z0, z1) in enumerate(((0, 3000, 3000, 5000), (3000, 6000, 5000, 3000), (6000, 9000, 3000, 5000), (9000, 12000, 5000, 3000)), 1):
        ok(ts.run("brace.create", {"id": "9" + "0" * 19 + f"m{k}", "level": F.LV1, "type": BT, "start": [x0, 0, z0], "end": [x1, 0, z1], "cutback": False, "name": f"M-D{k}"}))
    man = {tail(x["name"]): x for x in ok(ts.run("beam.list", {}))["items"]}
    assert set(man) == set(rows)
    for k, m in man.items():
        t = rows[k]
        assert (t["start"], t["end"], t["offset"], t.get("end_offset"), t["length"], t["cut_length"], t["volume_m3"]) == \
               (m["start"], m["end"], m["offset"], m.get("end_offset"), m["length"], m["cut_length"], m["volume_m3"]), k
        assert t["anchor"] == m["anchor"] == "center" and t["section"] == m["section"] and bool(t.get("brace")) == bool(m.get("brace"))


def test_2_트러스_만들기(ts):
    r = mk(ts, name="TR-A")
    assert r["count"] == 11 and len(set(r["children"])) == 11
    p = get(ts, F.TR1)["params"]
    assert p == {"name": "TR-A", "start": [0.0, 0.0], "end": [12000.0, 0.0], "offset": 3000.0, "height": 2000.0, "pattern": "pratt", "panels": 4, "btype": BT,
                 "children": r["children"]}
    assert get(ts, F.TR1)["kind"] == "truss" and ts.eng.st.el[F.TR1]["ifc_class"] == "IfcElementAssembly"
    rows = {x["id"]: x for x in ok(ts.run("beam.list", {}))["items"]}
    assert set(rows) == set(r["children"])
    chords = [rows[b] for b in r["children"][:2]]
    assert [c["name"] for c in chords] == ["TR-A-BC", "TR-A-TC"] and [c["offset"] for c in chords] == [3000.0, 5000.0] and all("brace" not in c for c in chords)
    webs = [rows[b] for b in r["children"][2:]]
    assert [w["name"] for w in webs][:2] == ["TR-A-V0", "TR-A-V1"] and all(w["brace"] is True and w["truss"] == F.TR1 for w in webs)
    posts = [w for w in webs if "post_dir" in w]
    assert len(posts) == 5 and all(w["slope_deg"] == 90.0 and w["length"] == 2000.0 and w["start"] == w["end"] for w in posts)
    assert all(ts.eng.st.el[b]["ifc_class"] == ("IfcBeam" if i < 2 else "IfcMember") for i, b in enumerate(r["children"]))
    for b in r["children"]:
        q = get(ts, b)["params"]
        assert q["cutback"] is False and q["anchor"] == "center" and q["btype"] == BT and q["truss"] == F.TR1 and q["section"]["shape"] == "h"
    ls = ok(ts.run("truss.list", {"level": F.LV1}))["items"][0]
    assert ls["id"] == F.TR1 and ls["beams"] == 11 and ls["members"] == {"chords": 2, "posts": 5, "diagonals": 4} and ls["type"] == "TC1" and ls["web_type"] == "TC1"
    assert ls["total_length"] == pytest.approx(sum(x["cut_length"] for x in rows.values()), abs=1e-2) and ls["start"] == [0.0, 0.0, 3000.0] and ls["end"] == [12000.0, 0.0, 3000.0]
    assert ok(ts.run("truss.list", {"level": F.LV2}))["items"] == []
    assert ok(ts.run("model.validate", {}))["errors"] == 0
    st = ok(ts.run("model.stats", {}))["counts"]
    assert st["truss"] == 1 and st["beam"] == 11
    # 웹 유형을 따로 주면 웹만 그 단면이다. 경사 트러스(끝 높이가 다르다)와 Howe·Warren
    r2 = mk(ts, id=TR2, web_type=BW, start=[0, 5000, 3000], end=[10000, 5000, 3500], pattern="howe", panels=6)
    assert r2["count"] == 2 + 7 + 6
    p2 = get(ts, TR2)["params"]
    assert p2["wtype"] == BW and p2["end_offset"] == 3500.0 and p2["btype"] == BT
    for i, b in enumerate(r2["children"]):
        q = get(ts, b)["params"]
        assert q["btype"] == (BT if i < 2 else BW) and q["section"]["shape"] == ("h" if i < 2 else "l")
    assert get(ts, r2["children"][0])["params"]["end_offset"] == 3500.0 and get(ts, r2["children"][1])["params"]["end_offset"] == 5500.0   # 상현도 경사
    assert ok(ts.run("beamtype.list", {}))["items"][0]["beams"] + ok(ts.run("beamtype.list", {}))["items"][1]["beams"] == 11 + 15            # 유형 관계
    assert ok(ts.run("model.validate", {}))["errors"] == 0
    mk(ts, id="9" + "0" * 19 + "t3", pattern="warren", panels=5, start=[0, 9000, 0], end=[10000, 9000, 0])
    assert len(kids(ts, "9" + "0" * 19 + "t3")) == 7


def test_2_고치면_자식이_다시_만들어진다(ts):
    r = mk(ts)
    first = r["children"]
    before = snap(ts)
    # 칸 수를 늘리면 앞 자식 id 는 그대로 이어받고(역할이 같은 자리), 새 자식이 생긴다
    r2 = ok(ts.run("truss.set", {"id": F.TR1, "panels": 6}))
    assert r2["changed"] is True and r2["count"] == 2 + 7 + 6 and r2["children"][:11] == first
    assert sorted(tail(get(ts, b)["params"]["name"]) for b in r2["children"]) == sorted(["BC", "TC"] + [f"V{i}" for i in range(7)] + [f"D{i}" for i in range(1, 7)])
    # 형식을 바꾸면 같은 id 가 다른 부재가 된다. Warren 은 수직재가 없어 자식이 줄고 남는 것은 지운다
    r3 = ok(ts.run("truss.set", {"id": F.TR1, "pattern": "warren"}))
    assert r3["count"] == 2 + 6 and r3["children"] == r2["children"][:8] and all(b not in ts.eng.st.el for b in r2["children"][8:])
    assert all("post_dir" not in get(ts, b)["params"] for b in r3["children"])
    assert sorted(geom(ts)) == sorted(["BC", "TC"] + [f"D{i}" for i in range(1, 7)])
    # 높이·두 점(경사)·이름
    ok(ts.run("truss.set", {"id": F.TR1, "height": 2400, "end": [12000, 0, 3600], "name": "ZZ", "panels": 4, "pattern": "howe"}))
    g = geom(ts)
    assert g["TC"][2:4] == (5400.0, 6000.0) and g["BC"][2:4] == (3000.0, 3600.0) and g["V2"][:2] == ((6000.0, 0.0), (6000.0, 0.0)) and g["V2"][2:4] == (3300.0, 5700.0)
    assert all(get(ts, b)["params"]["name"].startswith("ZZ-") for b in kids(ts))
    # 유형: 현만 바꾸면 웹은 현 유형을 따라간다(웹 유형을 따로 주지 않았다). 웹 유형을 따로 주면 웹만 바뀐다
    ok(ts.run("truss.set", {"id": F.TR1, "type": BW}))
    assert all(get(ts, b)["params"]["btype"] == BW for b in kids(ts)) and "wtype" not in get(ts, F.TR1)["params"]
    ok(ts.run("truss.set", {"id": F.TR1, "web_type": BT}))
    ps = [get(ts, b)["params"] for b in kids(ts)]
    assert [q["btype"] for q in ps[:2]] == [BW, BW] and all(q["btype"] == BT for q in ps[2:]) and get(ts, F.TR1)["params"]["wtype"] == BT
    ok(ts.run("truss.set", {"id": F.TR1, "web_type": BW}))                                               # 현과 같아지면 따로 적지 않는다
    assert "wtype" not in get(ts, F.TR1)["params"]
    # 레벨
    ok(ts.run("truss.set", {"id": F.TR1, "level": F.LV2}))
    assert {x["level"] for x in ok(ts.run("beam.list", {}))["items"]} == {F.LV2} and ok(ts.run("truss.list", {"level": F.LV2}))["items"][0]["id"] == F.TR1
    assert ok(ts.run("model.validate", {}))["errors"] == 0
    assert ok(ts.run("truss.set", {"id": F.TR1, "panels": 4}))["changed"] is False                      # 같은 값이면 변화 없음
    # 되돌리기 전부(바꾼 것 7번: 칸 수·형식·높이 묶음·유형 3번 중 마지막 되돌림·레벨): 처음 상태와 같다
    for _ in range(7):
        ok(ts.run("history.undo", {}))
    assert snap(ts) == before
    ok(ts.run("history.redo", {}))
    assert len(kids(ts)) == 2 + 7 + 6


def test_2_옮기기와_지우기와_복사(ts):
    r = mk(ts)
    first = r["children"]
    ok(ts.run("truss.move", {"id": F.TR1, "dx": 500, "dy": 300, "dz": -100}))
    p = get(ts, F.TR1)["params"]
    assert p["start"] == [500.0, 300.0] and p["end"] == [12500.0, 300.0] and p["offset"] == 2900.0 and kids(ts) == first
    assert geom(ts)["V0"][:4] == ((500.0, 300.0), (500.0, 300.0), 2900.0, 4900.0)
    ok(ts.run("history.undo", {}))
    base_geom = geom(ts)
    # 복사: 트러스만 골라도 자식 부재가 새로 생긴다(새 트러스의 자식이고 원본 자식은 그대로)
    c = ok(ts.run("element.copy", {"ids": [F.TR1], "dx": 0, "dy": 6000, "new_ids": [TR2]}))
    assert c["ids"] == [TR2] and kids(ts) == first and set(kids(ts, TR2)).isdisjoint(first) and len(kids(ts, TR2)) == 11
    g2 = geom(ts, TR2)
    assert same_geom(g2, {k: ((v[0][0], v[0][1] + 6000.0), (v[1][0], v[1][1] + 6000.0), v[2], v[3], v[4]) for k, v in base_geom.items()})
    assert get(ts, TR2)["params"]["name"] == "TR-0000t2" and all(get(ts, b)["params"]["name"].startswith("TR-0000t2-") for b in kids(ts, TR2))   # 자동 이름은 새 id 로
    assert ok(ts.run("beamtype.list", {}))["items"][0]["beams"] == 22                                   # 복사본의 부재도 같은 유형을 쓴다
    ok(ts.run("element.copy", {"ids": [F.TR1], "level": F.LV2}))                                         # 다른 레벨로(높이는 레벨 기준 그대로)
    assert sorted(x["level"] for x in ok(ts.run("truss.list", {}))["items"]) == sorted([F.LV1, F.LV1, F.LV2])
    ok(ts.run("element.copy", {"ids": [F.TR1], "dx": 0, "dy": -6000, "count": 2}))                      # 배열
    assert len(ok(ts.run("truss.list", {}))["items"]) == 5 and ok(ts.run("model.validate", {}))["errors"] == 0
    # 지우기: 자식 부재까지 지우고 되돌리기가 모두 되살린다
    before = snap(ts)
    d = ok(ts.run("element.delete", {"id": TR2}))
    assert sorted(d["deleted"]) == sorted([TR2, *g_kids(ts, d)]) and len(d["deleted"]) == 12
    assert not any(b in ts.eng.st.el for b in d["deleted"])
    ok(ts.run("history.undo", {}))
    assert snap(ts) == before


def g_kids(s, d):
    return [x for x in d["deleted"] if x != TR2]


def test_2_회전과_대칭은_자식을_다시_만든다(ts):
    mk(ts, end=[12000, 0, 4000])                                                                           # 경사 트러스로 시험한다
    old = geom(ts)
    first = kids(ts)
    ok(ts.run("element.rotate", {"ids": [F.TR1], "center": [3000, 1000], "angle": 90}))
    assert kids(ts) == first                                                                               # 제자리 변환은 자식 id 를 이어받는다
    rot = lambda p: (3000.0 - (p[1] - 1000.0), 1000.0 + (p[0] - 3000.0))   # noqa: E731
    want = {k: (rot(v[0]), rot(v[1]), v[2], v[3], None if v[4] is None else 90.0) for k, v in old.items()}
    assert same_geom(geom(ts), want) and all(geom(ts)[k][4] == 90.0 for k in geom(ts) if k.startswith("V"))
    ok(ts.run("history.undo", {}))
    assert geom(ts) == old
    # 세로 축(x = 6000)에 대칭: 두 점의 순서가 그대로라 부재 k 는 원본 부재 k 의 상이다(방향 180도)
    ok(ts.run("element.mirror", {"ids": [F.TR1], "axis": [6000, 0, 6000, 1000]}))
    mir = {k: ((12000.0 - v[0][0], v[0][1]), (12000.0 - v[1][0], v[1][1]), v[2], v[3], None if v[4] is None else 180.0) for k, v in old.items()}
    assert same_geom(geom(ts), mir) and kids(ts) == first
    ok(ts.run("history.undo", {}))
    # 대칭 복사(keep): 원본은 그대로이고 상이 새 트러스로 생긴다
    m = ok(ts.run("element.mirror", {"ids": [F.TR1], "axis": [0, -2000, 1000, -2000], "keep": True, "new_ids": [TR2]}))
    assert m["ids"] == [TR2] and geom(ts) == old
    assert same_geom(geom(ts, TR2), {k: ((v[0][0], -4000.0 - v[0][1]), (v[1][0], -4000.0 - v[1][1]), v[2], v[3], None if v[4] is None else -0.0 + 0.0) for k, v in old.items()})
    assert ok(ts.run("model.validate", {}))["errors"] == 0
    # 원형 배열
    ok(ts.run("element.array_radial", {"ids": [F.TR1], "center": [6000, 0], "step": 120, "count": 2}))
    assert len(ok(ts.run("truss.list", {}))["items"]) == 4 and len(ts.eng.st.by_kind["beam"]) == 44
    assert ok(ts.run("model.validate", {}))["errors"] == 0


# ---------------------------------------------------------------- 3. 소유 규약·거절·저장
def test_3_자식_부재는_따로_고치지_못한다(ts):
    r = mk(ts)
    c0, c_post, c_diag = r["children"][0], r["children"][2], r["children"][-1]
    for b in (c0, c_post, c_diag):
        for name, args in (("beam.set", {"id": b, "offset": 0}), ("beam.move", {"id": b, "dx": 10}), ("element.delete", {"id": b}),
                           ("beam.type.set", {"ids": [b], "type": BW}), ("element.copy", {"ids": [b], "dx": 100, "dy": 0}),
                           ("element.rotate", {"ids": [b], "center": [0, 0], "angle": 10}), ("element.mirror", {"ids": [b], "axis": [0, 0, 0, 1000]}),
                           ("material.set", {"id": b, "name": "Steel"}), ("brace.set", {"id": b, "name": "x"})):
            e = err(ts.run(name, args))
            assert e["code"] == "CONSTRAINT" and "트러스" in e["message"], (name, e)
    assert "truss.set" in err(ts.run("beam.set", {"id": c0, "offset": 0}))["hint"]
    # 태그는 달 수 있고 자식이 지워지면 고아로 남는다(조용히 지우지 않는다)
    t = ok(ts.run("annot.tag.create", {"view": V, "target": c_diag, "fields": ["name"], "align": "leader"}))["id"]
    ok(ts.run("truss.set", {"id": F.TR1, "panels": 2}))                                                    # 사재 D4 가 사라진다
    assert t in ts.eng.st.el
    it = ok(ts.run("annot.list", {}))["items"][0]
    assert it["id"] == t and it["status"] == "orphan" and it["reasons"] == [{"ref": "target", "why": "target_deleted"}]
    # 트러스 요소 자체는 보 명령으로 못 고치고, 트러스 명령은 트러스가 아닌 요소에 거절
    assert err(ts.run("beam.set", {"id": F.TR1, "offset": 0}))["code"] in ("NOT_FOUND", "CONSTRAINT")
    assert err(ts.run("truss.set", {"id": c0, "panels": 2}))["code"] in ("NOT_FOUND", "CONSTRAINT")
    assert err(ts.run("truss.move", {"id": c0, "dx": 10}))["code"] in ("NOT_FOUND", "CONSTRAINT")
    assert ok(ts.run("model.validate", {}))["errors"] == 0


def test_3_유형을_고치면_자식이_따라가고_쓰는_유형은_못_지운다(ts):
    r = mk(ts, web_type=BW)
    ok(ts.run("beamtype.set", {"id": BT, "preset": "H-300x150x6.5x9", "material": "SS275"}))
    ok(ts.run("beamtype.set", {"id": BW, "preset": "L-90x90x10"}))
    for i, b in enumerate(r["children"]):
        q = get(ts, b)["params"]
        assert q["truss"] == F.TR1
        if i < 2:
            assert q["section"]["depth"] == 300.0 and q["material"] == "SS275"                          # 현 = 현 유형
        else:
            assert q["section"] == {"shape": "l", "width": 90.0, "depth": 90.0, "thickness": 10.0} and "material" not in q   # 웹 = 웹 유형
    for t in (BT, BW):
        assert err(ts.run("beamtype.delete", {"id": t}))["code"] == "CONSTRAINT"                       # 부재가 쓴다
        e = err(ts.run("beamtype.delete", {"id": t, "detach": True}))                                    # 트러스는 유형으로 자식을 다시 만든다: 떼어 낼 수 없다
        assert e["code"] == "CONSTRAINT" and e["details"]["trusses"] == [F.TR1]
        assert err(ts.run("element.delete", {"id": t}))["code"] == "CONSTRAINT"
    ok(ts.run("element.delete", {"id": F.TR1}))
    assert ok(ts.run("beamtype.delete", {"id": BT}))["deleted"] == [BT]                                  # 트러스를 지우면 지울 수 있다


def test_3_거절(ts):
    base = {"level": F.LV1, "type": BT, "start": [0, 0, 3000], "end": [12000, 0, 3000], "height": 2000}

    def bad(code, **kw):
        a = {**base, **kw}
        for k in [k for k, v in a.items() if v is None]:
            a.pop(k)
        e = err(ts.run("truss.create", a))
        assert e["code"] in code if isinstance(code, tuple) else e["code"] == code, e
        return e
    S_ = ("SCHEMA_INVALID", "SCHEMA")
    assert "짝수" in bad("CONSTRAINT", panels=5)["message"]                                              # Pratt 는 짝수
    assert "짝수" in bad("CONSTRAINT", pattern="howe", panels=3)["message"]
    assert "80도" in bad("CONSTRAINT", panels=2, end=[1000, 0, 3000], height=9000, pattern="warren")["message"]   # 사재가 너무 섰다
    assert "80도" in bad("CONSTRAINT", end=[12000, 0, 3000 + 12000 * 6])["message"]                      # 경사 현
    assert "평면 길이" in bad("CONSTRAINT", end=[300, 0, 3000])["message"]
    bad("CONSTRAINT", end=[0, 0, 3000])                                                                  # 길이 0
    bad(S_, height=50)
    bad(S_, height=BM.TRUSS_HEIGHT_MAX + 1)
    bad(S_, panels=1)
    bad(S_, panels=BM.TRUSS_PANELS_MAX + 1)
    bad(S_, pattern="fink")
    bad(S_, start=[0, 0])                                                                                # 높이가 없다
    bad(S_, height=None)
    bad("NOT_FOUND", type="9" * 22)
    bad("NOT_FOUND", web_type="9" * 22)
    bad("NOT_FOUND", level="9" * 22)
    bad("NOT_FOUND", type=V)                                                                             # 보 유형이 아니다
    assert not ts.eng.st.by_kind.get("truss") and not ts.eng.st.by_kind.get("beam")                      # 거절은 아무것도 남기지 않는다
    ok(ts.run("truss.create", {"id": F.TR1, **base}))
    assert err(ts.run("truss.create", {"id": F.TR1, **base}))["code"] == "CONSTRAINT"                    # 같은 id
    ids = list(ts.eng.st.by_kind["beam"])[:2]
    assert err(ts.run("truss.create", {"id": TR2, **base, "child_ids": [ids[0]]}))["code"] == "CONSTRAINT"   # 이미 있는 id 를 자식으로
    snap0 = snap(ts)
    e = err(ts.run("truss.set", {"id": F.TR1, "panels": 5}))                                             # 고쳐서 못 쓰게 되는 것도 거절하고 그대로 둔다
    assert e["code"] == "CONSTRAINT" and "짝수" in e["message"] and snap(ts) == snap0
    assert err(ts.run("truss.set", {"id": F.TR1, "height": 20000, "panels": 100}))["code"] == "CONSTRAINT" and snap(ts) == snap0
    assert err(ts.run("truss.set", {"id": F.TR1}))["code"] in S_
    assert err(ts.run("truss.set", {"id": F.TR1, "type": "9" * 22}))["code"] == "NOT_FOUND"
    assert err(ts.run("truss.move", {"id": F.TR1, "dz": 1e9}))["code"] == "CONSTRAINT" and snap(ts) == snap0


def test_3_풀면_부재가_독립_부재로_남는다(ts):
    r = mk(ts, start=[0, -9000, 3000], end=[6000, -9000, 3000], panels=2)
    first = r["children"]
    before = snap(ts)
    rr = ok(ts.run("truss.ungroup", {"id": F.TR1}))
    assert rr["count"] == 2 + 3 + 2 and sorted(rr["released"]) == sorted(first)
    assert F.TR1 not in ts.eng.st.el and all("truss" not in get(ts, b)["params"] for b in first)
    assert ok(ts.run("model.stats", {}))["counts"].get("truss") is None and ok(ts.run("model.validate", {}))["errors"] == 0
    chord, post, diag = first[0], first[3], first[-1]                                                      # 하현, 수직재 V1(x = 3000), 사재 D2
    assert ts.eng.st.el[chord]["ifc_class"] == "IfcBeam" and ts.eng.st.el[post]["ifc_class"] == "IfcMember" and get(ts, post)["params"]["post_dir"] == 0.0
    # 이제 보통 부재다: 보·가새 명령으로 고칠 수 있다(수직재는 평면 점이 같게 지켜야 하고, 단면 방향 post_dir 은 그대로 남는다)
    ok(ts.run("beam.move", {"id": post, "dx": 100, "dy": 50, "dz": 10}))
    q = get(ts, post)["params"]
    assert q["start"] == q["end"] == [3100.0, -8950.0] and (q["offset"], q["end_offset"]) == (3010.0, 5010.0) and q["post_dir"] == 0.0 and q["brace"] is True
    ok(ts.run("beam.set", {"id": post, "end_offset": 5500, "name": "P"}))
    assert get(ts, post)["params"]["end_offset"] == 5500.0 and get(ts, post)["params"]["post_dir"] == 0.0
    e = err(ts.run("beam.set", {"id": post, "start": [3000, -8950]}))                                    # 평면 점이 어긋나면 수직재가 아니다
    assert e["code"] == "CONSTRAINT" and "평면 점" in e["message"]
    assert err(ts.run("beam.set", {"id": post, "end_offset": 3000}))["code"] == "CONSTRAINT"              # 아래로 서는 것도 안 된다
    ok(ts.run("brace.set", {"id": post, "name": "P2"}))
    ok(ts.run("beam.type.set", {"ids": [post], "type": BW}))
    assert get(ts, post)["params"]["section"]["shape"] == "l" and get(ts, post)["params"]["post_dir"] == 0.0
    ok(ts.run("beam.set", {"id": diag, "cutback": True}))                                                 # 사재의 접합 자르기도 이제 켤 수 있다
    # 복사·회전·대칭은 단면 방향 post_dir 도 돌린다
    c = ok(ts.run("element.copy", {"ids": [post], "dx": 0, "dy": -1000}))["ids"][0]
    assert get(ts, c)["params"]["start"] == [3100.0, -9950.0] and get(ts, c)["params"]["post_dir"] == 0.0
    ok(ts.run("element.rotate", {"ids": [c], "center": [3100, -9950], "angle": 90}))
    assert get(ts, c)["params"]["post_dir"] == 90.0 and get(ts, c)["params"]["start"] == [3100.0, -9950.0]
    ok(ts.run("element.rotate", {"ids": [c], "center": [3100, -9950], "angle": 135}))
    assert get(ts, c)["params"]["post_dir"] == -135.0
    ok(ts.run("element.mirror", {"ids": [c], "axis": [0, -9950, 1000, -9950]}))                           # 가로 축: 방향 -135 -> 135
    assert get(ts, c)["params"]["post_dir"] == 135.0
    ok(ts.run("element.delete", {"id": c}))
    assert ok(ts.run("model.validate", {}))["errors"] == 0
    same_as_full(ts, (V,))
    # 되돌리기: 푼 것까지 모두 돌아온다
    for _ in range(11):
        ok(ts.run("history.undo", {}))
    assert snap(ts) == before and ok(ts.run("model.validate", {}))["errors"] == 0


def test_3_저장하고_다시_열기(tmp_path):
    s = kit.Session(tmp_path / "b.dobim")
    try:
        for name, args in F.SCRIPT:
            ok(s.run(name, args))
        ok(s.run("beamtype.create", {"id": BT, "name": "TC1", "preset": "H-250x125x6x9"}))
        ok(s.run("beamtype.create", {"id": BW, "name": "TW1", "preset": "L-75x75x9"}))
        mk(s, web_type=BW, end=[12000, 0, 3500], panels=6, pattern="howe")
        before = snap(s)
        lst = ok(s.run("truss.list", {}))["items"]
    finally:
        s.close()
    s = kit.Session(tmp_path / "b.dobim", create=False)
    try:
        assert snap(s) == before and ok(s.run("truss.list", {}))["items"] == lst
        assert ok(s.run("model.validate", {}))["errors"] == 0
        assert ok(s.run("truss.set", {"id": F.TR1, "pattern": "warren"}))["changed"] is True              # 다시 열어도 고칠 수 있다
    finally:
        s.close()


# ---------------------------------------------------------------- 4. 출력
def abs_area(poly) -> float:
    return abs(D._area2(list(poly))) / 2.0


def test_4_도면(ts):
    r = mk(ts, start=[0, -9000, 3000], end=[12000, -9000, 3000])
    ids = r["children"]
    ok(ts.run("view.create", {"id": V3, "name": "S", "type": "elevation", "dir": "S"}))
    ok(ts.run("view.create", {"id": V2, "name": "SEC", "type": "section", "p1": [1500, -9500], "p2": [1500, -8500]}))
    g = g_of(ts)
    # 평면: 절단 높이(1200) 위의 부재는 외곽 사각형 숨은선 파선. 수직재는 단면이 놓이는 작은 사각형(춤 250 은 트러스 방향, 폭 125 는 직각)
    post = ids[2 + 1]                                                                                      # V1(x = 3000)
    hid = g.q(D.beam_lines, V, post)
    assert hid and all(x[0] == "proj" for x in hid)
    xs, ys = sorted({x[1] for x in hid} | {x[3] for x in hid}), sorted({x[2] for x in hid} | {x[4] for x in hid})
    assert xs[0] == pytest.approx(2875.0) and xs[-1] == pytest.approx(3125.0) and ys[0] == pytest.approx(-9062.5) and ys[-1] == pytest.approx(-8937.5)
    # 입면(남쪽): 수직재 플랜지 모서리가 연직선(z 3000~5000)으로, 상·하현 윗면·아랫면이 수평선으로 보인다
    el = g.q(D.view_lines, V3)
    assert ("proj", 3125.0, 3000.0, 3125.0, 5000.0) in el and ("proj", 2875.0, 3000.0, 2875.0, 5000.0) in el
    assert ("proj", 0.0, 2875.0, 12000.0, 2875.0) in el and ("proj", 0.0, 5125.0, 12000.0, 5125.0) in el
    # 단면(x = 1500, 칸 한가운데): 하현·상현(단면적 그대로)과 사재 D1(비스듬한 절단: 단면적 / cos) 이 잘린다. 수직재는 이 자리에 없다
    fills = {b: [abs_area(pl) for pl in g.q(D.beam_fills, V2, b)] for b in ids}
    area = BM.section_area(BM.preset_section("H-250x125x6x9"))
    assert sum(fills[ids[0]]) == pytest.approx(area) and sum(fills[ids[1]]) == pytest.approx(area)
    d1 = ids[2 + 5]
    assert sum(fills[d1]) == pytest.approx(area * sqrt(3000.0 ** 2 + 2000.0 ** 2) / 3000.0, rel=1e-6)
    assert all(not fills[b] for b in ids[2:7] + ids[8:])
    same_as_full(ts, (V, V2, V3))
    ok(ts.run("truss.set", {"id": F.TR1, "panels": 6, "pattern": "warren"}))
    same_as_full(ts, (V, V2, V3))
    ok(ts.run("truss.move", {"id": F.TR1, "dx": 100}))
    same_as_full(ts, (V, V2, V3))
    ok(ts.run("history.undo", {}))
    ok(ts.run("history.undo", {}))
    same_as_full(ts, (V, V2, V3))
    # GUI 장면: 자식 부재가 요소별로 들어간다
    from dobim_core import scene as SC
    sc = g_of(ts).q(SC.view_scene, V3)
    assert {t[1] for t in sc if t[0] == "beam"} >= set(ids)
    # 일람표: 보 범주에 현은 IfcBeam, 웹은 IfcMember 행으로 든다. 재료 물량도 같다
    t = ok(ts.run("schedule.table", {"category": "beams", "fields": ["name", "ifc_class", "length", "volume"], "units": "mm"}))
    rows = {x[0]: x for x in t["items"]}
    assert len(rows) == 11 and rows["TR-000057-BC"][1] == "IfcBeam" and rows["TR-000057-V2"][1] == "IfcMember" and rows["TR-000057-D1"][1] == "IfcMember"
    assert rows["TR-000057-V2"][2] == pytest.approx(2000.0) and rows["TR-000057-V2"][3] == pytest.approx(area * 2000.0)


def destructive(s, name, args):
    r = s.run(name, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


def kids_of(b, tid, project):
    return ok(b.run("element.get", {"id": tid}, project=project))["params"]["children"]


def test_4_IFC_트러스(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("beamtype.create", {"id": IBT, "name": "TC1", "preset": "H-250x125x6x9", "material": "SS275"}))
        ok(b.run("beamtype.create", {"id": IBW, "name": "TW1", "preset": "L-75x75x9"}))
        ok(b.run("truss.create", {"id": ITR, "level": F.LV1, "type": IBT, "web_type": IBW, "start": [0, -9000, 3000], "end": [12000, -9000, 3600], "height": 2000,
                                  "pattern": "pratt", "panels": 4}))
        ok(b.run("beam.create", {"id": "3" + "0" * 19 + "n1", "level": F.LV1, "type": IBT, "start": [0, -3000], "end": [6000, -3000], "offset": 2400}))
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        asm = f.by_guid(ITR)
        assert asm.is_a("IfcElementAssembly") and asm.PredefinedType == "TRUSS" and asm.AssemblyPlace == "NOTDEFINED" and asm.Representation is None
        assert len(asm.IsDecomposedBy) == 1
        parts = [x for r in asm.IsDecomposedBy for x in r.RelatedObjects]
        chords, webs = [x for x in parts if x.is_a("IfcBeam")], [x for x in parts if x.is_a("IfcMember")]
        assert len(parts) == 11 and len(chords) == 2 and len(webs) == 9 and all(x.PredefinedType == "BRACE" for x in webs) and all(x.PredefinedType == "BEAM" for x in chords)
        # 집합의 부분은 층에 담기지 않고 트러스 전체만 층에 담긴다
        assert all(not x.ContainedInStructure for x in parts) and [r.RelatingStructure.Name for r in asm.ContainedInStructure] == ["1F"]
        # 현은 보 유형(IfcBeamType), 웹은 부재 유형(IfcMemberType BRACE: IfcMember 는 IfcBeamType 을 받을 수 없다)
        assert {r.RelatingType.is_a() for x in chords for r in x.IsTypedBy} == {"IfcBeamType"} and {r.RelatingType.Name for x in chords for r in x.IsTypedBy} == {"TC1"}
        assert {r.RelatingType.is_a() for x in webs for r in x.IsTypedBy} == {"IfcMemberType"} and {r.RelatingType.Name for x in webs for r in x.IsTypedBy} == {"TW1"}
        # 몸체: 현·사재는 프로필 돌출, 수직재는 위를 향한 돌출(길이 = 두 현 기준선 사이 높이)
        post = next(x for x in webs if x.Name.endswith("-V1"))
        sw = post.Representation.Representations[0].Items[0]
        assert sw.is_a("IfcExtrudedAreaSolid") and sw.Depth == pytest.approx(2000.0) and sw.SweptArea.is_a("IfcLShapeProfileDef")
        assert tuple(sw.Position.Axis.DirectionRatios) == (0.0, 0.0, 1.0)
        diag = next(x for x in webs if x.Name.endswith("-D1"))                                              # 위 왼쪽 마디(z 5000) -> 아래 안쪽 마디(z 3150)
        assert diag.Representation.Representations[0].Items[0].Depth == pytest.approx(sqrt(3000.0 ** 2 + 1850.0 ** 2))
        assert len(f.by_type("IfcMaterialProfileSetUsage")) == 3                                            # 현 2 + 보 1 이 구조 재료(SS275). 웹 유형은 재료가 없다
        # IFC 의 형상이 도면(beams.mesh)과 같다: 부재마다 세계 좌표 경계 상자가 같다(수직재의 위를 향한 돌출 틀·경사 현과 사재 포함)
        st = ifcopenshell.geom.settings()
        st.set("use-world-coords", True)
        for x in parts:
            q = ok(b.run("element.get", {"id": x.GlobalId}))["params"]
            mv, _fc = BM.mesh(q, q["offset"], (0.0, 0.0))                                                    # 1F 의 높이는 0
            vs = ifcopenshell.geom.create_shape(st, x).geometry.verts
            got = [f(vs[k::3]) * 1000.0 for k in range(3) for f in (min, max)]   # 형상 API 는 미터로 돌려준다
            want = [f(v[k] for v in mv) for k in range(3) for f in (min, max)]
            assert got == pytest.approx(want, abs=1e-2), (x.Name, got, want)
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "t3.ifc"))
        a3 = f3.by_guid(ITR)
        assert file_errors(f3) == [] and a3.is_a("IfcElementAssembly") and a3.PredefinedType == "TRUSS" and len([x for r in a3.IsDecomposedBy for x in r.RelatedObjects]) == 11
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"] and r["result"]["elements"]["detached"] == 0, r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)                                                  # 트러스·자식·유형까지 같다
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        assert ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
        ok(b.run("truss.set", {"id": ITR, "panels": 6}, project="imp.dobim"))                              # 가져온 트러스도 고칠 수 있다
        assert len(kids_of(b, ITR, "imp.dobim")) == 15
        # 고치고 되돌리기·지우기가 IFC 를 원래대로 돌린다
        ok(b.run("truss.set", {"id": ITR, "pattern": "warren"}))
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
        ok(b.run("element.delete", {"id": ITR}))
        f4 = ifcopenshell.open(str(b.export("t4.ifc")))
        assert not f4.by_type("IfcElementAssembly") and not f4.by_type("IfcMember") and not f4.by_type("IfcMemberType")
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
        # 풀면 집합이 사라지고 부재가 층에 담긴다(수직재 = 평면 길이 0 의 IfcMember BRACE 도 그대로 나간다). 되돌리면 같은 IFC
        ok(b.run("truss.ungroup", {"id": ITR}))
        f5 = ifcopenshell.open(str(b.export("t5.ifc")))
        assert not f5.by_type("IfcElementAssembly") and len(f5.by_type("IfcMember")) == 9 and all(x.ContainedInStructure for x in f5.by_type("IfcMember"))
        assert file_errors(f5) == [] and sum(1 for x in f5.by_type("IfcMember") if x.PredefinedType == "BRACE") == 9
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
        assert file_errors(ifcopenshell.open(str(b.export("t6.ifc")))) == []
    finally:
        b.close()


# ---------------------------------------------------------------- 5. 보 시스템 복사·회전·대칭과 태그
BS1, BS2 = F.BS1, "9" + "0" * 19 + "s2"
RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


def bsys(s, sid=BS1, **kw):
    a = {"id": sid, "level": F.LV1, "type": BT, "boundary": RECT, "angle": 0, "spacing": 1500, "align": "start", "offset": 2400}
    a.update(kw)
    return ok(s.run("beamsys.create", a))


def segset(s, sid) -> frozenset:
    """보 시스템 자식의 두 점 쌍(순서 무관) 집합(소수 3자리)."""
    out = []
    for b in get(s, sid)["params"]["children"]:
        q = get(s, b)["params"]
        out.append(frozenset({(round(q["start"][0] + 0.0, 3), round(q["start"][1] + 0.0, 3)), (round(q["end"][0] + 0.0, 3), round(q["end"][1] + 0.0, 3))}))
    return frozenset(out)


def moved(segs, f) -> frozenset:
    return frozenset(frozenset(f(p) for p in sg) for sg in segs)


def same_segs(a, b, tol=0.01) -> bool:
    """선분 집합(두 점 쌍, 순서 무관)이 허용 tol(mm) 안에서 같은가: 회전·대칭 뒤 6자리 반올림 차이를 받는다."""
    la, lb = [tuple(s) for s in a], [tuple(s) for s in b]
    if len(la) != len(lb):
        return False

    def near(p, q):
        return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol
    for sa in la:
        hit = next((i for i, sb in enumerate(lb) if (near(sa[0], sb[0]) and near(sa[1], sb[1])) or (near(sa[0], sb[1]) and near(sa[1], sb[0]))), None)
        if hit is None:
            return False
        lb.pop(hit)
    return True


def test_5_이전에는_보_시스템을_옮겨_복사할_수_없었다(ts):
    """재현: 보 시스템이 element.copy 계열의 대상 종류가 아니라 시스템을 복사·회전·대칭하려면 시스템을 새로 만들어야 했다(경계·간격을 다시 입력)."""
    r = bsys(ts)
    assert len(r["children"]) == 2 and segset(ts, BS1) == frozenset({frozenset({(0.0, 1500.0), (6000.0, 1500.0)}), frozenset({(0.0, 3000.0), (6000.0, 3000.0)})})
    c = ok(ts.run("element.copy", {"ids": [BS1], "dx": 0, "dy": 6000, "new_ids": [BS2]}))                # 이제 대상이다
    assert c["ids"] == [BS2]


def test_5_보_시스템_복사(ts):
    first = bsys(ts)["children"]
    ok(ts.run("element.copy", {"ids": [BS1], "dx": 2000, "dy": 6000, "new_ids": [BS2]}))
    p2 = get(ts, BS2)["params"]
    assert p2["boundary"] == [[2000.0, 6000.0], [8000.0, 6000.0], [8000.0, 10000.0], [2000.0, 10000.0]] and p2["spacing"] == 1500.0 and p2["align"] == "start"
    assert p2["children"] != first and set(p2["children"]).isdisjoint(first) and len(p2["children"]) == 2 and get(ts, BS1)["params"]["children"] == first   # 자식은 새로 만든다
    assert segset(ts, BS2) == moved(segset(ts, BS1), lambda p: (p[0] + 2000.0, p[1] + 6000.0))
    for b in p2["children"]:
        q = get(ts, b)["params"]
        assert q["bsys"] == BS2 and q["btype"] == BT and q["offset"] == 2400.0 and q["name"].startswith("BS-0000s2-")
    assert ok(ts.run("beamtype.list", {}))["items"][0]["beams"] + ok(ts.run("beamtype.list", {}))["items"][1]["beams"] == 4
    # 다른 레벨로 복사·배열, 복사본도 시스템으로 고칠 수 있다
    ok(ts.run("element.copy", {"ids": [BS1], "level": F.LV2}))
    assert sorted(x["level"] for x in ok(ts.run("beamsys.list", {}))["items"]) == sorted([F.LV1, F.LV1, F.LV2])
    ok(ts.run("element.copy", {"ids": [BS1], "dx": 0, "dy": -6000, "count": 2}))
    assert len(ts.eng.st.by_kind["beamsys"]) == 5 and len(ts.eng.st.by_kind["beam"]) == 10
    ok(ts.run("beamsys.set", {"id": BS2, "spacing": 1000}))
    assert len(get(ts, BS2)["params"]["children"]) == 3 and ok(ts.run("model.validate", {}))["errors"] == 0
    # 되돌리기
    for _ in range(4):
        ok(ts.run("history.undo", {}))
    assert list(ts.eng.st.by_kind["beamsys"]) == [BS1] and len(ts.eng.st.by_kind["beam"]) == 2


def test_5_보_시스템_회전과_대칭(ts):
    first = bsys(ts, angle=30, spacing=700)["children"]                                                  # 경사 방향, 한쪽에서 재는 간격
    base = segset(ts, BS1)
    n = len(first)
    assert n >= 5
    # 회전: 자리는 그대로 따라간다(방향이 180도를 넘겨 뒤집혀도 정렬을 맞바꿔 같은 자리를 지킨다)
    for ang in (90, 150, -45, 200):
        ok(ts.run("element.rotate", {"ids": [BS1], "center": [3000, 1000], "angle": ang}))
        sn, cs = K.sincos_deg(float(ang))
        want = moved(base, lambda p, sn=sn, cs=cs: (3000.0 + cs * (p[0] - 3000.0) - sn * (p[1] - 1000.0), 1000.0 + sn * (p[0] - 3000.0) + cs * (p[1] - 1000.0)))
        assert same_segs(segset(ts, BS1), want), ang
        assert get(ts, BS1)["params"]["children"] == first                                               # 제자리 변환은 자식 id 를 이어받는다
        assert 0.0 <= get(ts, BS1)["params"]["angle"] < 180.0 and K.polygon_area2(get(ts, BS1)["params"]["boundary"]) > 0
        ok(ts.run("history.undo", {}))
        assert segset(ts, BS1) == base
    # 대칭(세로 축·가로 축·비스듬한 축): 같은 자리의 상이다(반시계 경계와 정렬을 지킨다)
    for axis, f in (([6000, -500, 6000, 4500], lambda p: (12000.0 - p[0], p[1])), ([-500, 2000, 6500, 2000], lambda p: (p[0], 4000.0 - p[1])),
                    ([0, 0, 1000, 1000], lambda p: (p[1], p[0]))):
        ok(ts.run("element.mirror", {"ids": [BS1], "axis": axis}))
        assert same_segs(segset(ts, BS1), moved(base, f)), axis
        assert K.polygon_area2(get(ts, BS1)["params"]["boundary"]) > 0 and 0.0 <= get(ts, BS1)["params"]["angle"] < 180.0
        ok(ts.run("history.undo", {}))
        assert segset(ts, BS1) == base
    # 대칭 복사
    m = ok(ts.run("element.mirror", {"ids": [BS1], "axis": [0, 4500, 1000, 4500], "keep": True, "new_ids": [BS2]}))
    assert m["ids"] == [BS2] and segset(ts, BS1) == base and same_segs(segset(ts, BS2), moved(base, lambda p: (p[0], 9000.0 - p[1])))
    # 개수·가운데 정렬도 같은 자리를 지킨다
    ok(ts.run("beamsys.set", {"id": BS2, "count": 3, "align": "center"}))
    ok(ts.run("element.rotate", {"ids": [BS2], "center": [0, 0], "angle": 100}))
    assert len(get(ts, BS2)["params"]["children"]) >= 3 and ok(ts.run("model.validate", {}))["errors"] == 0


def test_5_보_시스템_회전_정렬_맞바꿈_세부(ts):
    """직선의 방향이 같아도 진행 방향 벡터가 180도 뒤집히면 왼쪽·오른쪽이 바뀐다: 정렬 start 의 시스템을 180도 돌리면 end 가 되어 같은 자리를 지킨다."""
    bsys(ts, angle=0, spacing=1500, align="start")
    base = segset(ts, BS1)                                                                               # 위로 1500·3000 (왼쪽 = 아래 가장자리에서)
    ok(ts.run("element.rotate", {"ids": [BS1], "center": [3000, 2000], "angle": 180}))
    p = get(ts, BS1)["params"]
    assert p["angle"] == 0.0 and p["align"] == "end"
    assert same_segs(segset(ts, BS1), moved(base, lambda q: (6000.0 - q[0], 4000.0 - q[1])))
    ok(ts.run("element.rotate", {"ids": [BS1], "center": [3000, 2000], "angle": 90}))                    # 90도: 방향 90, 정렬 그대로
    assert get(ts, BS1)["params"]["angle"] == 90.0 and get(ts, BS1)["params"]["align"] == "end"


def test_5_보_시스템과_트러스_태그(ts):
    bsys(ts, offset=2400)
    mk(ts, start=[0, -9000, 3000], end=[0, -3000, 3000], pattern="warren", panels=3, web_type=BW)         # 트러스는 y 방향
    p = get(ts, BS1)["params"]
    t1 = ok(ts.run("annot.tag.create", {"view": V, "target": BS1, "fields": ["type", "size", "spacing", "count"]}))["id"]
    t2 = ok(ts.run("annot.tag.create", {"view": V, "target": F.TR1, "fields": ["type", "web_type", "pattern", "panels"]}))["id"]
    t3 = ok(ts.run("annot.tag.create", {"view": V, "target": F.TR1, "fields": ["height", "length", "section", "size"], "align": "leader"}))["id"]
    assert text_of(ts, t1) == "TC1 H-250x125 1500 2" and text_of(ts, t2) == "TC1 TW1 Warren 3" and text_of(ts, t3) == "2000 6000 H-250x125x6x9 H-250x125"
    tg = tags(ts)
    # 보 시스템: 경계 무게중심에 작은 보 방향으로 눕는다(지시선 없음). 트러스: 두 점의 가운데에 진행 방향으로. leader 는 이전 지시선 태그의 모양이다
    assert tg[t1][2] == (3000.0, 2000.0) and tg[t1][3] == (3000.0, 2000.0) and tg[t1][5] == (1.0, 0.0)
    assert tg[t2][2] == (0.0, -6000.0) and tg[t2][5] == (0.0, 1.0) and len(tg[t3]) == 5 and tg[t3][3] == (400.0, -5600.0)
    assert get(ts, t1)["params"]["align"] == "axis" and get(ts, t2)["params"]["off"] == [0.0, 0.0] and "align" not in get(ts, t3)["params"]
    # 시스템·트러스를 옮기면 태그가 따라가고 값이 바뀌면 글이 바뀐다
    ok(ts.run("beamsys.set", {"id": BS1, "spacing": 1000, "angle": 90}))
    assert text_of(ts, t1) == "TC1 H-250x125 1000 5" and tags(ts)[t1][5] == (0.0, 1.0)
    ok(ts.run("truss.move", {"id": F.TR1, "dx": 500}))
    assert tags(ts)[t2][2] == (500.0, -6000.0)
    ok(ts.run("truss.set", {"id": F.TR1, "panels": 5, "start": [500, -9000, 3000], "end": [500, -3000, 4000]}))
    assert text_of(ts, t2) == "TC1 TW1 Warren 5" and text_of(ts, t3) == "2000 6082.8 H-250x125x6x9 H-250x125"          # 경사 트러스의 길이는 실길이(sqrt(6000^2 + 1000^2))
    # 한 번에: annot.tag.all 은 이미 태그가 있는 대상을 건너뛴다. 다른 레벨의 시스템은 태그를 달 수 없다
    ok(ts.run("beamsys.create", {"id": BS2, "level": F.LV2, "type": BT, "boundary": RECT, "spacing": 1000}))
    assert err(ts.run("annot.tag.create", {"view": V, "target": BS2, "fields": ["name"]}))["code"] == "CONSTRAINT"
    r = ok(ts.run("annot.tag.all", {"view": V, "kinds": ["beamsys", "truss"], "fields": ["name"]}))
    assert r["created"] == []
    ok(ts.run("view.create", {"id": V3, "name": "2F", "type": "plan", "level": F.LV2, "cut_height": 1200}))
    r2 = ok(ts.run("annot.tag.all", {"view": V3, "kinds": ["beamsys", "truss"], "fields": ["name", "level"], "align": "leader"}))
    assert [x["target"] for x in r2["tags"]] == [BS2] and len(tags(ts, V3)[r2["created"][0]]) == 5
    # 대상이 아닌 범주에 축 방향 태그는 거절
    assert err(ts.run("annot.tag.all", {"view": V, "kinds": ["wall"], "fields": ["name"], "align": "axis"}))["code"] == "CONSTRAINT"
    # 시스템·트러스가 지워지면 태그는 고아로 남는다(조용히 지우지 않는다)
    ok(ts.run("element.delete", {"id": F.TR1}))
    assert t2 in ts.eng.st.el and g_of(ts).q(AN.annot_state, t2)[1] == "orphan"
    # 잘못된 필드: 보 시스템에는 web_type·pattern 이 없고 트러스에는 spacing·count 가 없다
    ok(ts.run("truss.create", {"id": F.TR1, "level": F.LV1, "type": BT, "start": [0, -9000, 3000], "end": [6000, -9000, 3000], "height": 1500, "panels": 2}))
    assert err(ts.run("annot.tag.create", {"view": V, "target": BS1, "fields": ["pattern"]}))["code"] == "CONSTRAINT"
    assert err(ts.run("annot.tag.create", {"view": V, "target": F.TR1, "fields": ["spacing"]}))["code"] == "CONSTRAINT"
    assert ok(ts.run("model.validate", {}))["errors"] == 0


def test_5_자식_부재에도_보_태그를_단다(ts):
    """트러스 자식(수직재 포함)은 보 행이라 보 태그(80절)를 그대로 쓴다: 위치 = 자른 뒤 축의 가운데(수직재는 마디 자리), 방향 = 진행 방향."""
    r = mk(ts, start=[0, -9000, 3000], end=[0, -3000, 3000])                                              # y 방향 트러스
    out = ok(ts.run("annot.tag.all", {"view": V, "kinds": ["beam"], "fields": ["name", "length"]}))
    assert len(out["created"]) == 11
    tg = tags(ts)
    assert all(g_of(ts).q(AN.annot_state, t)[:2] == ("tag", "ok") for t in out["created"])
    by_target = {x["target"]: x["id"] for x in out["tags"]}
    post = r["children"][2 + 1]                                                                            # V1: (0, -7500)
    t = by_target[post]
    assert tg[t][2] == (0.0, -7500.0) and tg[t][5] == (0.0, 1.0) and text_of(ts, t) == "TR-000057-V1 2000"
    chord = by_target[r["children"][0]]
    assert tg[chord][2] == (0.0, -6000.0) and tg[chord][5] == (0.0, 1.0) and text_of(ts, chord) == "TR-000057-BC 6000"
    diag = by_target[r["children"][-1]]
    assert text_of(ts, diag).startswith("TR-000057-D4 ")
    assert ok(ts.run("model.validate", {}))["errors"] == 0


def test_5_시스템_태그도_겹침_회피_배치를_따른다(ts):
    """W2 스물여섯 번째 PR(태그 겹침 회피)과 함께: 같은 자리에 겹친 보 시스템·트러스 태그를 annot.tag.arrange 가 떼어 놓고(축 방향 태그는 시스템 방향을 따라 미끄러진다),
    annot.tag.create avoid 로 새 태그만 비켜 단다."""
    bsys(ts)
    mk(ts, start=[0, -9000, 3000], end=[6000, -9000, 3000], pattern="warren", panels=3)
    a1 = ok(ts.run("annot.tag.create", {"view": V, "target": BS1, "fields": ["type", "size"]}))["id"]
    a2 = ok(ts.run("annot.tag.create", {"view": V, "target": F.TR1, "fields": ["type", "pattern"]}))["id"]
    r = ok(ts.run("annot.tag.create", {"view": V, "target": F.TR1, "fields": ["height", "length"], "avoid": True}))
    assert r["placed"]["unresolved"] is False
    out = ok(ts.run("annot.tag.arrange", {"view": V}))
    assert out["unresolved"] == [] and out["skipped"] == []
    for t in (a1, a2, r["id"]):
        assert g_of(ts).q(AN.annot_state, t)[:2] == ("tag", "ok")
    tg = tags(ts)
    assert len({tg[t][3] for t in (a1, a2, r["id"])}) == 3                                                  # 글 자리가 서로 다르다
    assert all(len(tg[t]) in (6, 8) for t in (a1, a2, r["id"]))
    assert ok(ts.run("model.validate", {}))["errors"] == 0


def test_5_태그_출력_SVG와_DXF(ts):
    bsys(ts)
    mk(ts, start=[0, -9000, 3000], end=[6000, -9000, 3000], pattern="howe", panels=2)
    ok(ts.run("annot.tag.create", {"view": V, "target": BS1, "fields": ["type", "size"]}))
    ok(ts.run("annot.tag.create", {"view": V, "target": F.TR1, "fields": ["pattern", "panels"]}))

    def exp(name):
        assert destructive(ts, "view.export", {"id": V, "path": name, "overwrite": True})["ok"]
        return (ts.path.parent / name).read_bytes()
    svg = exp("a.svg").decode("utf-8")
    assert "TC1 H-250x125" in svg and "Howe 2" in svg and svg.count("rotate(0.00)") >= 2 and 'text-anchor="middle"' in svg
    assert exp("a.pdf").startswith(b"%PDF") and "Howe 2" in exp("a.dxf").decode("utf-8")
