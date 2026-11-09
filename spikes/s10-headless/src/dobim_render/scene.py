# SPDX-License-Identifier: MPL-2.0
"""IFC 파일 -> PNG 한 번에. 단계별 시간과 해시를 돌려준다."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from dobim_render import mesh as _mesh
from dobim_render import png, raster


@dataclass
class Result:
    png_bytes: bytes
    info: dict
    frame: raster.Frame
    mesh: _mesh.Mesh

    def pick(self, x: int, y: int) -> str | None:
        """픽셀 -> 요소 GlobalId (요소 ID 버퍼). 배경이면 None."""
        i = int(self.frame.ids[y, x])
        return None if i < 0 else self.mesh.elems[i][0]


def render_ifc(path: str, width: int = 1280, view: str = "iso", threads: int = 1, png_level: int = 6) -> Result:
    t0 = time.perf_counter()
    m = _mesh.extract(path, threads)
    t1 = time.perf_counter()
    fr = raster.render(m.verts, m.faces, m.face_elem, [c for _, c in m.elems], width=width, view=view)
    t2 = time.perf_counter()
    data = png.encode_rgb(fr.color, png_level)
    t3 = time.perf_counter()
    info = {
        "view": view, "width": fr.color.shape[1], "height": fr.color.shape[0],
        "elements": len(m.elems), "triangles": int(len(m.faces)),
        "mesh_sha256": m.sha256(),
        "pixel_sha256": hashlib.sha256(fr.color.tobytes()).hexdigest(),
        "id_buffer_sha256": hashlib.sha256(fr.ids.tobytes()).hexdigest(),
        "png_sha256": hashlib.sha256(data).hexdigest(), "png_bytes": len(data),
        "visible_elements": int(len(set(fr.ids[fr.ids >= 0].tolist()))),
        "stats": fr.stats,
        "seconds": {"geometry": round(t1 - t0, 3), "raster": round(t2 - t1, 3), "png": round(t3 - t2, 3),
                    "total": round(t3 - t0, 3)},
    }
    return Result(data, info, fr, m)
