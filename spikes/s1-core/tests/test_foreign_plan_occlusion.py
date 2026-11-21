# SPDX-License-Identifier: MPL-2.0
"""외래 IFC 도면 5차(IO-04): 평면에서 외래 요소가 절단 높이 위아래 관계대로 저작 선을 가린다.
- 절단면 아래 저작 요소(낮은 벽 윗면·솔리드 윗면 모서리 등)와 절단면 사이에 외래 면(예: 외래 슬래브)이 있으면 그 아래 저작 선이 숨는다
- 절단면에서 잘린 외래 몸체(절단 영역)가 덮는 저작 투영선도 숨는다
- 저작 요소보다 아래에 있는 외래 면은 가리지 않는다. 저작 절단선은 가리지 않는다
- 반사 천장 평면(외래를 그리지 않는다)·외래 없는 뷰는 새 질의가 None 이라 값이 이전과 같다
혼합 모델은 test_foreign_occlusion 처럼 저작 모델을 IFC 로 내보낸 뒤 일부 요소의 의도 속성셋을 지워 다시 가져와 만든다."""
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

L800, L300 = kit.mkid("3", 911), kit.mkid("3", 912)
WL1, WL2 = kit.mkid("1", 911), kit.mkid("1", 912)    # 낮은 저작 벽(높이 500): 외래 슬래브 밑 / 외래 벽이 가로지른다
FW = kit.mkid("1", 913)                              # 외래가 될 높은 벽(절단면에 잘린다)
FS, FL = kit.mkid("6", 911), kit.mkid("6", 912)      # 외래가 될 슬래브: z 700~800(저작 위) / z 200~300(저작 아래)
SO = kit.mkid("7", 911)                              # 저작 솔리드(z 0~400): 윗면 모서리 높이가 끝점마다 있다
VP, VR = kit.mkid("4", 911), kit.mkid("4", 912)
P = "mixp.dobim"
FOREIGN = {FW, FS, FL}


def _conf(run, name, args):
    r = run(name, args)
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = run(name, args, confirm=r["error"]["details"]["confirm_token"])
    assert r["ok"], r
    return r


def _mixed(ws: Path, foreign_ids) -> dispatch.Env:
    s = kit.Session(ws / "src.dobim")
    for name, args in [
        ("level.create", {"id": F.LV1, "name": "1F", "elevation": 0}),
        ("level.create", {"id": L300, "name": "L300", "elevation": 300}),
        ("level.create", {"id": L800, "name": "L800", "elevation": 800}),
        ("wall.create", {"id": WL1, "level": F.LV1, "axis": [0, 0, 4000, 0], "thickness": 200, "height": 500}),
        ("wall.create", {"id": WL2, "level": F.LV1, "axis": [5000, 0, 8000, 0], "thickness": 200, "height": 500}),
        ("wall.create", {"id": FW, "level": F.LV1, "axis": [6500, -1000, 6500, 1000], "thickness": 200, "height": 3000}),
        ("slab.create", {"id": FS, "level": L800, "boundary": [[1000, -1000], [3000, -1000], [3000, 3500], [1000, 3500]], "thickness": 100}),
        ("slab.create", {"id": FL, "level": L300, "boundary": [[4500, -2000], [9000, -2000], [9000, -1500], [4500, -1500]], "thickness": 100}),
        ("solid.create", {"id": SO, "level": F.LV1, "form": "prism", "polygon": [[1500, 3000], [2500, 3000], [2500, 4000], [1500, 4000]], "z0": 0, "z1": 400}),
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
    _conf(run, "view.create", {"id": VP, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200, "view_depth": 1200})
    _conf(run, "view.rcp", {"id": VR, "name": "1F 반사 천장", "level": F.LV1})
    return env


@pytest.fixture
def mixp(tmp_path):
    env = _mixed(tmp_path, FOREIGN)
    yield env
    env.close()


def _hx(lines, y, cls="proj"):
    """y 에 놓인 가로 선분들의 x 구간(정렬, 반올림)."""
    return sorted((round(min(ln[1], ln[3]), 1), round(max(ln[1], ln[3]), 1)) for ln in lines
                  if ln[0] == cls and abs(ln[2] - y) < 0.01 and abs(ln[4] - y) < 0.01 and abs(ln[1] - ln[3]) > 0.01)


def _vy(lines, x, cls="proj"):
    """x 에 놓인 세로 선분들의 y 구간(정렬, 반올림)."""
    return sorted((round(min(ln[2], ln[4]), 1), round(max(ln[2], ln[4]), 1)) for ln in lines
                  if ln[0] == cls and abs(ln[1] - x) < 0.01 and abs(ln[3] - x) < 0.01 and abs(ln[2] - ln[4]) > 0.01)


def test_외래_슬래브_아래의_저작_벽_윗면이_숨는다(mixp):
    g = mixp.engine(P).g
    assert g.q(D._fview, VP) is not None
    wl = g.q(D.plan_wall_lines, VP, WL1)
    for y in (100.0, -100.0):   # 낮은 벽(윗면 500) 위 z 700~800 에 외래 슬래브(x 1000~3000): 그 밑 윗면 윤곽이 숨는다
        assert _hx(wl, y) == [(0.0, 1000.0), (3000.0, 4000.0)], (y, _hx(wl, y))
    lines = g.q(D.view_lines, VP)
    for y in (100.0, -100.0):
        assert _hx(lines, y) == [(0.0, 1000.0), (3000.0, 4000.0), (5000.0, 6400.0), (6600.0, 8000.0)], (y, _hx(lines, y))   # 같은 y 의 WL2 도 함께
    assert _vy(lines, 0.0) == [(-100.0, 100.0)] and _vy(lines, 4000.0) == [(-100.0, 100.0)]   # 슬래브 밖 끝선은 그대로


def test_잘린_외래_벽_몸체가_덮는_저작_선이_숨고_아래_외래_슬래브는_가리지_않는다(mixp):
    g = mixp.engine(P).g
    wl = g.q(D.plan_wall_lines, VP, WL2)
    for y in (100.0, -100.0):   # 외래 벽(x 6400~6600)이 절단면에 잘린다: 그 몸체 속(절단 영역) 아래 낮은 벽 윗면 윤곽이 숨는다
        assert _hx(wl, y) == [(5000.0, 6400.0), (6600.0, 8000.0)], (y, _hx(wl, y))
    lines = g.q(D.view_lines, VP)
    assert _vy(lines, 6400.0, "cut") and _vy(lines, 6600.0, "cut")   # 외래 벽 절단선(외래 쪽은 그대로)
    assert _hx(lines, -1500.0) == [(4500.0, 9000.0)]   # 저작보다 아래(z 200~300)의 외래 슬래브 윤곽은 보이고, 아무것도 가리지 않는다


def test_솔리드_윗면_모서리는_끝점_높이로_가려진다(mixp):
    g = mixp.engine(P).g
    sl = g.q(D.solid_lines, VP, SO)
    # 솔리드(x 1500~2500, y 3000~4000, 윗면 400) 위 외래 슬래브(y ~3500): y 3000 모서리는 다 숨고, 옆 모서리는 3500~4000 만
    assert _hx(sl, 3000.0) == []
    assert _hx(sl, 4000.0) == [(1500.0, 2500.0)]
    assert _vy(sl, 1500.0) == [(3500.0, 4000.0)] and _vy(sl, 2500.0) == [(3500.0, 4000.0)]


def test_저작_절단선과_반사_천장_평면과_외래_없는_뷰는_그대로(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    mixed, pure = _mixed(tmp_path / "a", FOREIGN), _mixed(tmp_path / "b", set())
    gm, gp = mixed.engine(P).g, pure.engine(P).g
    assert gp.q(D._fview, VP) is None and gm.q(D._fview, VR) is None   # 반사 천장 평면은 외래를 그리지 않는다(scope_foreign 이 비어 있다)
    for w in (WL1, WL2):
        assert gm.q(D.plan_wall_lines, VR, w) == gp.q(D.plan_wall_lines, VR, w), w
        # 절단선은 가리지 않는다: 저작 벽의 cut 등급은 외래가 있어도 같다
        assert [ln for ln in gm.q(D.plan_wall_lines, VP, w) if ln[0] == "cut"] == [ln for ln in gp.q(D.plan_wall_lines, VP, w) if ln[0] == "cut"]
    mixed.close()
    pure.close()


def test_외래가_위에_없으면_저작_선은_모두_저작일_때와_같다(tmp_path):
    """외래 요소가 저작 선 위에 없으면(저작 아래의 외래 슬래브만 있으면) 저작 선은 모두 저작인 모델과 같다."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    mixed, pure = _mixed(tmp_path / "a", {FL}), _mixed(tmp_path / "b", set())
    gm, gp = mixed.engine(P).g, pure.engine(P).g
    assert gm.q(D._fview, VP) is not None and gp.q(D._fview, VP) is None
    for w in (WL1, WL2, FW):
        assert gm.q(D.plan_wall_lines, VP, w) == gp.q(D.plan_wall_lines, VP, w), w
    assert gm.q(D.solid_lines, VP, SO) == gp.q(D.solid_lines, VP, SO)
    assert gm.q(D.slab_lines, VP, FS) == gp.q(D.slab_lines, VP, FS)
    mixed.close()
    pure.close()


def test_증분_그래프와_전체_계산이_같고_저작_편집을_따라간다(mixp):
    eng = mixp.engine(P)
    run = lambda n, a: _conf(lambda n_, a_, **kw: dispatch.run(mixp, n_, a_, project=P, **kw), n, a)   # noqa: E731
    eng.g.q(D.view_lines, VP)
    run("wall.move", {"id": WL1, "dx": 0, "dy": 6000})   # 낮은 벽을 외래 슬래브 밖(y 6000)으로: 이제 가리는 것이 없다
    ref = RefGraph(_Inputs(eng))
    assert eng.g.q(D.view_lines, VP) == ref.q(D.view_lines, VP)
    assert _hx(eng.g.q(D.view_lines, VP), 6100.0) == [(0.0, 4000.0)]
