# SPDX-License-Identifier: MPL-2.0
"""재료 외관(W2 다섯 번째 PR, VIS-07): 3D 렌더가 IFC 재료 표면 스타일의 색·투명도를 쓴다. 투명 재료가 없으면 이전과 같은 경로·픽셀이다."""
import sys

from conftest import S1
from dobim_render import scene as SC

sys.path.insert(0, str(S1 / "tools"))
import kit  # noqa: E402
from dobim_core import fixture as F  # noqa: E402

CWI = "3" + "0" * 19 + "c1"


def _export(tmp_path, name, defs):
    s = kit.Session(tmp_path / f"{name}.dobim")
    try:
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        assert s.run("curtain.create", {"id": CWI, "level": F.LV1, "axis": [0, -2000, 6000, -2000], "height": 3000,
                                        "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "count", "value": 2}})["ok"]
        for d in defs:
            assert s.run("material.define", d)["ok"]
        s.eng.flush_ifc()
        out = tmp_path / f"{name}.ifc"
        s.eng.ifc.write(str(out))
        return str(out)
    finally:
        s.close()


def test_투명_재료는_뒤가_비치고_요소_ID_는_앞의_투명_요소다(tmp_path):
    plain = SC.render_ifc(_export(tmp_path, "a", []), 400)
    assert "transparent_pixels" not in plain.info["stats"] and all(a == 0.0 for a in plain.mesh.alphas)
    glass = SC.render_ifc(_export(tmp_path, "b", [{"name": "유리"}, {"name": "알루미늄", "color": [20, 20, 20]}]), 400)
    st = glass.info["stats"]
    assert st["transparent_pixels"] > 0 and glass.info["pixel_sha256"] != plain.info["pixel_sha256"]
    see = {i for i, a in enumerate(glass.mesh.alphas) if a > 0}
    assert see and all(glass.mesh.elems[i][1] == "IfcPlate" for i in see)                  # 커튼월 유리 패널만 투명
    ids = set(glass.frame.ids[glass.frame.ids >= 0].tolist())
    assert see & ids                                                                         # 앞에 있는 투명 패널은 집을 수 있다
    assert glass.mesh.mats and any(rgb == (20, 20, 20) for _, rgb in glass.mesh.mats)        # 멀리언(알루미늄) 정의 색
    assert glass.frame.color.shape == plain.frame.color.shape
