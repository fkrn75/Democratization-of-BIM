# SPDX-License-Identifier: MPL-2.0
"""CPU 진단 렌더: 시간(1280px RA-01 10초 이내), 결정성, ID 버퍼, PNG 왕복."""
import hashlib
import time

import numpy as np
import pytest

from conftest import S0
from dobim_render import png, raster
from dobim_render.synthetic import cube_mesh
from dobim_render.scene import render_ifc


def test_합성_정육면체_픽셀해시가_고정이다(expected):
    v, f, fe = cube_mesh()
    fr = raster.render(v, f, fe, ["IfcWall"], width=640)
    assert hashlib.sha256(fr.color.tobytes()).hexdigest() == expected["render"]["cube_pixel_sha256"]
    assert fr.stats["covered_pixels"] > 10000


def test_정육면체는_같은_입력에_같은_픽셀(tmp_path):
    v, f, fe = cube_mesh()
    a = raster.render(v, f, fe, ["IfcWall"], width=320)
    b = raster.render(v, f, fe, ["IfcWall"], width=320)
    assert a.color.tobytes() == b.color.tobytes() and (a.ids == b.ids).all()


def test_png_왕복은_무손실이다():
    v, f, fe = cube_mesh()
    fr = raster.render(v, f, fe, ["IfcWall"], width=200)
    assert (png.decode_rgb(png.encode_rgb(fr.color)) == fr.color).all()


@pytest.mark.slow
def test_RA01_1280px_10초_이내_결정적(expected):
    ifc = S0 / "RA-01.ifc"
    t0 = time.perf_counter()
    r1 = render_ifc(str(ifc), 1280)
    wall = time.perf_counter() - t0
    assert wall < 10.0, wall                       # 합격 기준(목표)
    assert r1.info["width"] == 1280
    r2 = render_ifc(str(ifc), 1280, threads=4)     # 스레드 수가 달라도 같은 결과
    assert r1.info["mesh_sha256"] == r2.info["mesh_sha256"]
    assert r1.info["pixel_sha256"] == r2.info["pixel_sha256"]
    assert r1.info["mesh_sha256"] == expected["render"]["s0_ra01"]["mesh_sha256"]
    assert r1.info["pixel_sha256"] == expected["render"]["s0_ra01"]["pixel_sha256"]
    assert r1.info["png_sha256"] == expected["render"]["s0_ra01"]["png_sha256"]


@pytest.mark.slow
def test_요소ID_버퍼로_픽셀에서_GlobalId와_클래스를_얻는다():
    r = render_ifc(str(S0 / "RA-01.ifc"), 1280)
    ids = r.frame.ids
    assert r.info["visible_elements"] >= 10
    ys, xs = np.nonzero(ids >= 0)
    # 화면 가운데 근처의 픽셀 하나를 골라 GlobalId 가 메시 요소 표와 맞는다
    k = len(ys) // 2
    gid = r.pick(int(xs[k]), int(ys[k]))
    assert gid in {g for g, _ in r.mesh.elems}
    assert r.pick(0, 0) is None                    # 모서리는 배경
    classes = {c for _, c in r.mesh.elems}
    assert {"IfcWall", "IfcSlab", "IfcDoor", "IfcWindow"} <= classes


@pytest.mark.slow
def test_코어가_내보낸_IFC도_렌더된다(tmp_path):
    """코어 CLI 로 만든 모델(벽 14 + 문 5)이 그대로 그려진다."""
    import json
    import subprocess
    import sys
    from conftest import ROOT, child_env
    p = subprocess.run([sys.executable, str(ROOT / "ra01_headless.py"), "--workspace", str(tmp_path / "ws"), "--python", sys.executable,
                        "--env-mode", "minimal"], capture_output=True, env=child_env(), stdin=subprocess.DEVNULL, timeout=600)
    assert p.returncode == 0
    r = render_ifc(str(tmp_path / "ws" / "ra01.ifc"), 1280)
    assert r.info["elements"] >= 19 and r.info["stats"]["covered_pixels"] > 20000
    json.dumps(r.info)
