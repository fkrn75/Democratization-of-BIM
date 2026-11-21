# SPDX-License-Identifier: MPL-2.0
"""외래 IFC 도면 4차(IO-04): 외래 요소와 저작 요소가 한 뷰에 있을 때 앞뒤 관계대로 서로의 선을 가린다(단면·입면).
- 외래 면·외래 절단 영역(잘린 몸체)이 앞에 있으면 뒤의 저작 선(벽·슬래브 등 투영선)이 숨는다(_fvis). 숨은 선(hid)은 같은 판정의 여집합이다
- 저작 요소가 앞에 있으면 뒤의 외래 선이 숨는다(1차부터 있던 동작, 회귀 확인)
- 외래 요소가 없는 뷰·평면은 새 질의가 None 이라 값이 이전과 같다
혼합 모델은 저작 모델을 IFC 로 내보낸 뒤 일부 요소의 의도 속성셋(DoBIM_Intent)을 지워 다시 가져와 만든다(그 요소만 외래가 된다)."""
import sys
from pathlib import Path

import ifcopenshell
import pytest

from dobim_core import dispatch
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core.engine import _Inputs
from dobim_core.graph import RefGraph
from dobim_core.ifc_model import INTENT_PSET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import kit  # noqa: E402

LV2 = kit.mkid("3", 902)
WF, WB = kit.mkid("1", 901), kit.mkid("1", 902)     # 외래가 될 앞 벽, 그 뒤의 저작 벽
WR, FB = kit.mkid("1", 903), kit.mkid("1", 904)     # 저작 앞 벽, 그 뒤에서 외래가 될 벽
SL = kit.mkid("6", 901)                             # 외래 앞 벽 뒤의 저작 슬래브(2층, 고도 2000)
VE, VS, VP = kit.mkid("4", 901), kit.mkid("4", 902), kit.mkid("4", 903)
P = "mix.dobim"


def _conf(run, name, args):
    r = run(name, args)
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = run(name, args, confirm=r["error"]["details"]["confirm_token"])
    assert r["ok"], r
    return r


def _mixed(ws: Path, foreign_ids) -> dispatch.Env:
    """저작 모델 -> IFC -> foreign_ids 의 의도 속성셋을 지움 -> 다시 가져온 혼합 프로젝트(P)."""
    s = kit.Session(ws / "src.dobim")
    for name, args in [
        ("level.create", {"id": F.LV1, "name": "1F", "elevation": 0}),
        ("level.create", {"id": LV2, "name": "2F", "elevation": 2000}),
        ("wall.create", {"id": WF, "level": F.LV1, "axis": [0, 0, 4000, 0], "thickness": 200, "height": 3000}),
        ("wall.create", {"id": WB, "level": F.LV1, "axis": [1000, 5000, 3000, 5000], "thickness": 200, "height": 4000}),
        ("wall.create", {"id": WR, "level": F.LV1, "axis": [10000, 0, 14000, 0], "thickness": 200, "height": 3000}),
        ("wall.create", {"id": FB, "level": F.LV1, "axis": [11000, 3000, 13000, 3000], "thickness": 200, "height": 4000}),
        ("slab.create", {"id": SL, "level": LV2, "boundary": [[500, 2000], [3500, 2000], [3500, 4000], [500, 4000]], "thickness": 200}),
    ]:
        _conf(s.run, name, args)
    _conf(s.run, "ifc.export", {"path": "src.ifc"})
    s.close()
    f = ifcopenshell.open(str(ws / "src.ifc"))
    for el in f.by_type("IfcProduct"):
        if el.GlobalId in foreign_ids:
            for rel in list(el.IsDefinedBy or ()):
                if rel.is_a("IfcRelDefinesByProperties") and rel.RelatingPropertyDefinition.Name == INTENT_PSET:
                    f.remove(rel)
    f.write(str(ws / "mix.ifc"))
    env = dispatch.Env(ws, "t")
    r = dispatch.run(env, "ifc.import", {"source": "mix.ifc", "path": P})
    assert r["ok"], r
    assert r["result"]["elements"]["foreign"] == len(foreign_ids), r["result"]
    run = lambda n, a, **kw: dispatch.run(env, n, a, project=P, **kw)   # noqa: E731
    _conf(run, "view.create", {"id": VE, "name": "남", "type": "elevation", "dir": "S"})
    _conf(run, "view.create", {"id": VS, "name": "단면", "type": "section", "p1": [-1000, 0], "p2": [15000, 0], "depth": 8000})
    _conf(run, "view.create", {"id": VP, "name": "1F", "type": "plan", "level": F.LV1})
    return env


@pytest.fixture
def mix(tmp_path):
    env = _mixed(tmp_path, {WF, FB})
    yield env
    env.close()


def _vert(lines, s, cls="proj"):
    """s 에 놓인 세로 선분들의 z 구간(정렬, 반올림)."""
    return sorted((round(min(ln[2], ln[4]), 1), round(max(ln[2], ln[4]), 1)) for ln in lines
                  if ln[0] == cls and abs(ln[1] - s) < 0.01 and abs(ln[3] - s) < 0.01 and abs(ln[2] - ln[4]) > 0.01)


def _horiz(lines, z, cls="proj"):
    return sorted((round(min(ln[1], ln[3]), 1), round(max(ln[1], ln[3]), 1)) for ln in lines
                  if ln[0] == cls and abs(ln[2] - z) < 0.01 and abs(ln[4] - z) < 0.01 and abs(ln[1] - ln[3]) > 0.01)


def test_입면에서_외래_앞벽이_뒤의_저작_벽_선을_가린다(mix):
    eng = mix.engine(P)
    g = eng.g
    lines = g.q(D.view_lines, VE)
    # 저작 뒤 벽(x 1000~3000, 높이 4000)의 세로 모서리: 외래 앞 벽(높이 3000) 위로 나온 3000~4000 만 보인다
    for s in (1000.0, 3000.0):
        assert _vert(lines, s) == [(3000.0, 4000.0)], (s, _vert(lines, s))
    assert g.q(D._fview, VE) is not None
    # 저작 벽 하나의 질의(vwall_lines)도 같은 판정이다: 가려진 부분은 숨은 선(hid)으로 간다
    assert sorted(set(_vert(g.q(D.vwall_lines, VE, WB), 1000.0))) == [(3000.0, 4000.0)]
    hid = g.q(D.vwall_hidden, VE, WB)
    assert sorted(set(_vert(hid, 1000.0, "hid"))) == [(0.0, 3000.0)]
    assert _vert(g.q(D.view_hidden_lines, VE), 1000.0, "hid") == [(0.0, 3000.0)]


def test_입면에서_외래_앞벽이_뒤의_저작_슬래브_선을_가린다(mix):
    g = mix.engine(P).g
    sl = g.q(D.slab_lines, VE, SL)
    # 슬래브(z 1800~2000, x 500~3500)는 통째로 외래 앞 벽(x 0~4000, z 0~3000) 뒤라 입면 투영선이 없다
    assert [ln for ln in sl if ln[0] == "proj"] == []


def test_저작_앞벽은_뒤의_외래_벽_선을_가린다(mix):
    lines = mix.engine(P).g.q(D.view_lines, VE)
    for s in (11000.0, 13000.0):   # 1차부터 있던 동작(저작이 외래를 가림)
        assert _vert(lines, s) == [(3000.0, 4000.0)], (s, _vert(lines, s))


def test_단면에서_잘린_외래_벽이_뒤의_저작_선을_가린다(mix):
    g = mix.engine(P).g
    lines = g.q(D.view_lines, VS)
    assert _horiz(lines, 3000.0, "cut") and any(ln[0] == "cut" for ln in lines)   # 외래 앞 벽이 절단면에 잘린다(절단선)
    for s in (2000.0, 4000.0):   # 단면의 s 는 p1(x -1000)에서 잰다. 잘린 외래 벽 몸체 뒤라 3000 아래는 숨는다
        assert _vert(lines, s) == [(3000.0, 4000.0)], (s, _vert(lines, s))
    assert [ln for ln in g.q(D.slab_lines, VS, SL) if ln[0] == "proj"] == []
    for s in (12000.0, 14000.0):   # 잘린 저작 앞 벽 뒤의 외래 벽(1차부터 있던 동작)
        assert _vert(lines, s) == [(3000.0, 4000.0)], (s, _vert(lines, s))


def test_평면과_외래_없는_뷰는_새_질의가_None(mix, base):
    assert mix.engine(P).g.q(D._fview, VP) is None
    assert base.run("view.create", {"id": VE, "name": "남", "type": "elevation", "dir": "S"})["ok"]
    assert base.eng.g.q(D._fview, VE) is None


def test_외래가_앞에_없으면_저작_선은_모두_저작일_때와_같다(tmp_path):
    """외래 요소가 저작 선 앞에 없으면(외래가 저작 앞 벽 뒤에만 있으면) 저작 벽 선은 모두 저작인 모델과 같다."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    mixed, pure = _mixed(tmp_path / "a", {FB}), _mixed(tmp_path / "b", set())
    gm, gp = mixed.engine(P).g, pure.engine(P).g
    for v in (VE, VS):
        assert gm.q(D._fview, v) is not None and gp.q(D._fview, v) is None
        for w in (WF, WB, WR):
            assert gm.q(D.vwall_lines, v, w) == gp.q(D.vwall_lines, v, w), (v, w)
            assert gm.q(D.vwall_hidden, v, w) == gp.q(D.vwall_hidden, v, w), (v, w)
        assert gm.q(D.slab_lines, v, SL) == gp.q(D.slab_lines, v, SL), v
    mixed.close()
    pure.close()


def test_증분_그래프와_전체_계산이_같고_저작_편집을_따라간다(mix):
    eng = mix.engine(P)
    run = lambda n, a: _conf(lambda n_, a_, **kw: dispatch.run(mix, n_, a_, project=P, **kw), n, a)   # noqa: E731
    for v in (VE, VS):
        eng.g.q(D.view_lines, v)
        eng.g.q(D.view_hidden_lines, v)
    run("wall.move", {"id": WB, "dx": 4000, "dy": 0})   # 뒤 저작 벽을 외래 앞 벽 옆(x 5000~7000)으로: 이제 가리는 것이 없다
    ref = RefGraph(_Inputs(eng))
    for v in (VE, VS):
        assert eng.g.q(D.view_lines, v) == ref.q(D.view_lines, v), v
        assert eng.g.q(D.view_hidden_lines, v) == ref.q(D.view_hidden_lines, v), v
    assert _vert(eng.g.q(D.view_lines, VE), 5000.0) == [(0.0, 4000.0)]


@pytest.mark.parametrize("sid", ["BA", "BS", "RA01", "RA01-bonsai"])
def test_외래_형상_미리_계산은_하나씩_계산과_같은_메시이고_기준선도_같다(sid, tmp_path, monkeypatch):
    """첫 형상 요청 때 iterator(여러 스레드)로 미리 계산한 삼각형화가 create_shape(한 스레드) 값과 요소마다 같고,
    미리 계산으로 그린 저장소 표본 도면이 기준선(corpus/baseline.json)과 같다. 표본이 작아 문턱(PREFETCH_MIN)을 1로 낮춰 미리 계산 길을 탄다."""
    import json
    import ifcopenshell.geom
    from dobim_core import foreign_geom as FG
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "spikes" / "phase1-drawings"))
    import foreign_corpus as FC
    src = FC.sample_path(sid)
    if src is None:
        pytest.skip(f"표본 {sid} 이 없다")
    monkeypatch.setattr(FG, "PREFETCH_MIN", 1)
    env = dispatch.Env(tmp_path, "t")
    FC._import(env, src)
    eng = env.engine(FC.PROJECT)
    fs = eng.g.q(D.all_foreign)   # 형상 첫 요청: 여기서 미리 계산이 돈다
    raw = getattr(eng.ifc, FG._RAW)
    assert fs and len(raw) >= len(fs), (len(fs), len(raw))
    st = FG._settings()
    for gid, (vs, faces) in sorted(raw.items()):
        sh = ifcopenshell.geom.create_shape(st, eng.ifc.ents[gid])
        assert (tuple(sh.geometry.verts), tuple(sh.geometry.faces)) == (vs, faces), gid
    env.close()
    base = json.loads(FC.BASELINE.read_text(encoding="utf-8"))
    assert FC._views_only(FC.render_sample(src)) == base[sid]
