# SPDX-License-Identifier: MPL-2.0
"""21단계: 3D 렌더 범주 필터(only/hide, 묶음 structure|mep|arch, 고스트)와 같은 평면 겹침 잡음 억제(tie_eps)."""
import numpy as np
import pytest

from conftest import S0
from dobim_render import mesh as M
from dobim_render import raster
from dobim_render import scene as SC


def _quad(z, x0, x1, base):
    """z 높이 수평 사각형(삼각형 2개)."""
    v = np.array([[x0, 0, z], [x1, 0, z], [x1, 4, z], [x0, 4, z]], float)
    f = np.array([[0, 1, 2], [0, 2, 3]], np.int32) + base
    return v, f


def test_같은_평면에_겹친_두_요소는_tie_eps_로_먼저_그린_쪽이_고르게_이긴다():
    # 두 요소가 같은 평면에 겹치되 삼각형 분할이 달라 깊이 보간의 반올림이 엇갈린다
    va, fa = _quad(1.0, 0.0, 4.0, 0)
    vb = np.array([[0, 0, 1.0], [4, 0, 1.0], [0, 4, 1.0], [4, 4, 1.0]], float)
    fb = np.array([[0, 1, 3], [0, 3, 2]], np.int32) + 4
    v = np.concatenate([va, vb + np.array([1e-12, 0, 0])])
    f = np.concatenate([fa, fb])
    fe = np.array([0, 0, 1, 1], np.int32)
    fr = raster.render(v, f, fe, ["IfcSlab", "IfcWall"], width=200, view="iso", tie_eps=1e-6)
    ids = fr.ids[fr.ids >= 0]
    assert set(ids.tolist()) == {0}                     # 겹친 곳은 모두 먼저 그린 요소
    fr0 = raster.render(v, f, fe, ["IfcSlab", "IfcWall"], width=200, view="iso")
    assert fr0.stats["edge_pixels"] >= fr.stats["edge_pixels"]


def test_tie_eps_0_은_이전과_같은_픽셀이다():
    from dobim_render.synthetic import cube_mesh
    v, f, fe = cube_mesh()
    a = raster.render(v, f, fe, ["IfcWall"], width=320)
    b = raster.render(v, f, fe, ["IfcWall"], width=320, tie_eps=0.0)
    assert a.color.tobytes() == b.color.tobytes()


def _mesh2():
    va, fa = _quad(0.0, 0.0, 2.0, 0)
    vb, fb = _quad(1.0, 3.0, 5.0, 4)
    return M.Mesh(np.concatenate([va, vb]), np.concatenate([fa, fb]), np.array([0, 0, 1, 1], np.int32),
                  [("g1", "IfcBeam"), ("g2", "IfcPipeSegment")], [(None, None)] * 2,
                  [("IfcBeam", "IfcBuildingElement", "IfcElement"), ("IfcPipeSegment", "IfcFlowSegment", "IfcDistributionFlowElement", "IfcDistributionElement", "IfcElement")])


@pytest.mark.parametrize("only,hide,want", [
    (["structure"], None, [True, False]), (["mep"], None, [False, True]), (["arch"], None, [True, False]),
    (["IfcFlowSegment"], None, [False, True]), (None, ["IfcBeam"], [False, True]), (["IfcElement"], ["mep"], [True, False]),
])
def test_범주_필터는_묶음과_상위_클래스로_고른다(only, hide, want):
    assert SC.element_mask(_mesh2(), only, hide) == want


def test_부분_메시는_요소_번호를_다시_매긴다():
    s = SC._subset(_mesh2(), [False, True])
    assert s.elems == [("g2", "IfcPipeSegment")] and s.face_elem.tolist() == [0, 0] and len(s.faces) == 2


@pytest.mark.slow
def test_RA01_범주_필터와_고스트(tmp_path):
    ifc = str(S0 / "RA-01.ifc")
    full = SC.render_ifc(ifc, 400)
    walls = SC.render_ifc(ifc, 400, only=["IfcWall"])
    assert walls.info["visible_elements"] > 0
    assert all(walls.mesh.elems[i][1].startswith("IfcWall") for i in set(walls.frame.ids[walls.frame.ids >= 0].tolist()))
    assert walls.frame.color.shape == full.frame.color.shape
    gh = SC.render_ifc(ifc, 400, only=["IfcWall"], ghost=True)
    assert gh.info["stats"]["ghost_pixels"] > 0 and gh.info["pixel_sha256"] != walls.info["pixel_sha256"]
    assert (gh.frame.ids == walls.frame.ids).all()      # 고스트는 색만 섞고 요소 ID 버퍼는 보이는 요소 그대로
    none = SC.render_ifc(ifc, 400, only=["mep"])
    assert none.info["visible_elements"] == 0
