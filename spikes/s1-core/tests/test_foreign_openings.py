# SPDX-License-Identifier: MPL-2.0
"""외래 IFC 도면 3차(IO-04): 외래 문·창의 평면 기호.
- 문: IfcDoor OperationType(요소, 없으면 유형)대로 문짝 선 + 1/4 호. 창: 창틀 양면 선 + 유리선(미서기·세로 칸 나눔)
- 절단면이 문·창 높이 범위 안인 평면에서만 형상 대신 기호를 그린다. 단면·입면·반사 천장 평면은 그대로
- 방식을 모르면 기본 기호 + 경고. 외래 문·창이 없으면 새 질의는 빈 값(기존 도면 바이트 불변)
표본은 저장소의 RA-01(ifcopenshell.api, 문 SINGLE_SWING_LEFT·창 SINGLE_PANEL)과 순수 함수 단위 시험이다."""
import sys
from math import sqrt
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "spikes" / "phase1-drawings"))
import foreign_corpus as FC  # noqa: E402

from dobim_core import dispatch  # noqa: E402
from dobim_core import drawing as D  # noqa: E402
from dobim_core import foreign_symbols as FS  # noqa: E402
from dobim_core import queries as Q  # noqa: E402
from dobim_core.engine import _Inputs  # noqa: E402
from dobim_core.graph import RefGraph  # noqa: E402


@pytest.fixture
def ra(tmp_path):
    """RA-01 표본을 가져온 프로젝트와 정해진 뷰(평면 2·입면 4·단면 2)."""
    p = FC.sample_path("RA01")
    if p is None:
        pytest.skip("RA-01 표본이 없다")
    env = dispatch.Env(tmp_path, "t")
    FC._import(env, p)
    FC.render_project(env)
    yield env
    env.close()


def _views(eng):
    return {eng.st.el[v]["params"]["name"]: v for v in eng.st.by_kind["view"]}


def _by_name(eng, cls, name):
    return next(e for e in sorted(eng.st.by_kind["foreign"]) if eng.st.el[e]["ifc_class"] == cls and eng.st.el[e]["params"].get("name") == name)


def test_문_여닫이_방식과_배치를_읽는다(ra):
    eng = ra.engine(FC.PROJECT)
    g = eng.g
    d1 = _by_name(eng, "IfcDoor", "D-01")
    fo = g.q(Q.fopen, d1)
    assert fo == ("door", "SINGLE_SWING_LEFT", (8000.0, 0.0), (1.0, 0.0), (0.0, 1.0), 900.0)
    w1 = _by_name(eng, "IfcWindow", "WIN-01")
    assert g.q(Q.fopen, w1)[:3] == ("window", "SINGLE_PANEL", ())
    wall = next(e for e in sorted(eng.st.by_kind["foreign"]) if eng.st.el[e]["ifc_class"] == "IfcWall")
    assert g.q(Q.fopen, wall) is None


def test_평면에는_문짝_호와_창_기호를_그린다(ra):
    eng = ra.engine(FC.PROJECT)
    g = eng.g
    vs = _views(eng)
    plan = vs["plan-0"]
    d1 = _by_name(eng, "IfcDoor", "D-01")
    segs, default = g.q(D.foreign_opening_symbol, plan, d1)
    assert not default and all(s[0] == "symbol" for s in segs) and len(segs) == 1 + D.ARC_N
    # 왼쪽 외여닫이: 경첩은 국소 x 가 작은 쪽(문짝 폭 900, 메시 가운데 정렬), 문짝은 +y(국소 y) 쪽으로 900 열린다
    leaf = segs[0]
    assert abs((leaf[4] - leaf[2]) - 900.0) < 0.01 and leaf[1] == leaf[3]
    ends = {(round(s[1], 1), round(s[2], 1)) for s in segs[1:]} | {(round(s[3], 1), round(s[4], 1)) for s in segs[1:]}
    assert (round(leaf[1] + 900.0, 1), round(leaf[2], 1)) in ends and (round(leaf[3], 1), round(leaf[4], 1)) in ends
    for s in segs[1:]:   # 호의 점은 경첩에서 문짝 폭만큼 떨어져 있다
        for x, y in ((s[1], s[2]), (s[3], s[4])):
            assert abs(sqrt((x - leaf[1]) ** 2 + (y - leaf[2]) ** 2) - 900.0) < 0.05
    # 기호를 그리는 문·창은 형상 선 대신 기호만 낸다(foreign_lines = 기호)
    assert g.q(D.foreign_lines, plan, d1) == segs
    w1 = _by_name(eng, "IfcWindow", "WIN-01")
    ws, wdef = g.q(D.foreign_opening_symbol, plan, w1)
    assert not wdef and len(ws) == 3 and all(abs(abs(s[3] - s[1]) - 1200.0) < 0.01 and s[2] == s[4] for s in ws)   # 창틀 양면 + 유리선, 폭 1200
    assert g.q(D.foreign_symbol_defaults, plan) == 0
    # 단면·입면은 형상 그대로(기호 없음)
    for name in ("elev-S", "sec-x"):
        assert g.q(D.foreign_opening_symbol, vs[name], d1) is None


def test_절단면이_높이_범위_밖이면_기호를_그리지_않는다(ra):
    eng = ra.engine(FC.PROJECT)
    g = eng.g
    lv = eng.st.el[_views(eng)["plan-0"]]["params"]["level"]
    r = dispatch.run(ra, "view.create", {"id": "4F" + "9" * 20, "name": "high", "type": "plan", "level": lv, "cut_height": 2500, "view_depth": 1200}, project=FC.PROJECT)
    assert r["ok"], r
    d1 = _by_name(eng, "IfcDoor", "D-01")
    assert g.q(D.foreign_opening_symbol, "4F" + "9" * 20, d1) is None   # 문 높이 2100 위를 자른다
    r = dispatch.run(ra, "view.rcp", {"id": "4R" + "9" * 20, "name": "rcp", "level": lv, "cut_height": 1200}, project=FC.PROJECT)
    assert r["ok"], r
    assert g.q(D.foreign_opening_symbol, "4R" + "9" * 20, d1) is None   # 반사 천장 평면은 문·창 기호가 없다


def test_3차_질의도_증분_그래프와_전체_계산이_같다(ra):
    eng = ra.engine(FC.PROJECT)
    ref = RefGraph(_Inputs(eng))
    for v in sorted(eng.st.by_kind["view"]):
        assert eng.g.q(D.view_lines, v) == ref.q(D.view_lines, v), v
        assert eng.g.q(D.foreign_symbol_defaults, v) == ref.q(D.foreign_symbol_defaults, v), v


def test_외래_문창이_없으면_3차_질의는_빈_값이다(base):
    from dobim_core import fixture as F
    assert base.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1})["ok"]
    g = base.eng.g
    assert g.q(D.foreign_symbol_defaults, F.V1) == 0
    assert g.q(Q.fopen, F.D1) is None   # 저작 문은 외래 입력이 없다


def _ends(segs):
    return [(round(s[0], 3), round(s[1], 3), round(s[2], 3), round(s[3], 3)) for s in segs]


@pytest.mark.parametrize("op,leaves,arcs", [
    ("SINGLE_SWING_LEFT", [(0.0, 100.0, 0.0, 1100.0)], 1), ("SINGLE_SWING_RIGHT", [(1000.0, 100.0, 1000.0, 1100.0)], 1),
    ("DOUBLE_SWING_LEFT", [(0.0, 100.0, 0.0, 1100.0), (0.0, 0.0, 0.0, -1000.0)], 2),
    ("DOUBLE_DOOR_SINGLE_SWING", [(0.0, 100.0, 0.0, 600.0), (1000.0, 100.0, 1000.0, 600.0)], 2),
    ("DOUBLE_DOOR_SINGLE_SWING_OPPOSITE_LEFT", [(0.0, 100.0, 0.0, 600.0), (1000.0, 0.0, 1000.0, -500.0)], 2),
    ("DOUBLE_DOOR_DOUBLE_SWING", [(0.0, 100.0, 0.0, 600.0), (0.0, 0.0, 0.0, -500.0), (1000.0, 100.0, 1000.0, 600.0), (1000.0, 0.0, 1000.0, -500.0)], 4),
])
def test_문_방식별_기호(op, leaves, arcs):
    segs, default = FS.door_local(op, 0.0, 1000.0, 0.0, 100.0)
    assert not default
    got = _ends(segs)
    for lf in leaves:
        assert lf in got, (op, lf)
    assert len(segs) == arcs * (1 + FS.ARC_N)


def test_미닫이_고정_문짝과_모르는_방식():
    s, d = FS.door_local("SLIDING_TO_LEFT", 0.0, 1000.0, 0.0, 100.0)
    assert not d and _ends(s)[0] == (0.0, 75.0, 1000.0, 75.0) and len(s) == 3   # 문짝 선 + 왼쪽 화살 머리
    s, d = FS.door_local("DOUBLE_DOOR_SLIDING", 0.0, 1000.0, 0.0, 100.0)
    assert not d and len(s) == 6 and _ends(s)[:2] == [(0.0, 75.0, 550.0, 75.0), (450.0, 25.0, 1000.0, 25.0)]
    s, d = FS.door_local("SWING_FIXED_RIGHT", 0.0, 1000.0, 0.0, 100.0)
    assert not d and (0.0, 50.0, 500.0, 50.0) in _ends(s) and (1000.0, 100.0, 1000.0, 600.0) in _ends(s)
    for op in (None, "REVOLVING", "ROLLINGUP", "FOLDING_TO_LEFT"):
        s, d = FS.door_local(op, 0.0, 1000.0, 0.0, 100.0)
        assert d and s == FS.door_local("SINGLE_SWING_LEFT", 0.0, 1000.0, 0.0, 100.0)[0], op


def test_창_칸_나눔과_미서기():
    s, d = FS.window_local("SINGLE_PANEL", (), 0.0, 1200.0, 0.0, 60.0)
    assert not d and _ends(s) == [(0.0, 0.0, 1200.0, 0.0), (0.0, 60.0, 1200.0, 60.0), (0.0, 30.0, 1200.0, 30.0)]
    s, d = FS.window_local("TRIPLE_PANEL_VERTICAL", (), 0.0, 1200.0, 0.0, 60.0)
    assert not d and (400.0, 0.0, 400.0, 60.0) in _ends(s) and (800.0, 0.0, 800.0, 60.0) in _ends(s)
    s, d = FS.window_local(None, ("SLIDINGHORIZONTAL", "SLIDINGHORIZONTAL"), 0.0, 1200.0, 0.0, 60.0)
    assert not d and _ends(s)[2:] == [(0.0, 40.0, 660.0, 40.0), (540.0, 20.0, 1200.0, 20.0)]
    s, d = FS.window_local(None, ("NOTDEFINED",), 0.0, 1200.0, 0.0, 60.0)
    assert d and len(s) == 3


def test_배치_축을_따라_세계_좌표로_놓는다():
    # 국소 x = 세계 +y, 국소 y = 세계 -x 인 문(RA-01 D-02 와 같은 배치), 메시는 국소 (0..900, 0..100)
    fo = ("door", "SINGLE_SWING_RIGHT", (4000.0, 2000.0), (0.0, 1.0), (-1.0, 0.0), 800.0)
    pts = [(0.0, 0.0), (900.0, 0.0), (900.0, 100.0), (0.0, 100.0)]
    segs, d = FS.symbol(fo, pts)
    assert not d
    # 문짝 800 은 메시 폭 900 의 가운데(국소 50..850), 오른쪽 경첩 = 국소 u 850, 여는 면 = 국소 w 100 -> 세계 (3900, 2850)
    assert segs[0] == ("symbol", 3900.0, 2850.0, 3100.0, 2850.0)
    assert FS.symbol(None, pts) is None and FS.symbol(fo, [(0.0, 0.0), (0.5, 0.0)]) is None
