# SPDX-License-Identifier: MPL-2.0
"""IFC 파일 -> PNG 한 번에. 단계별 시간과 해시를 돌려준다."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import numpy as np

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


# 범주 묶음(21단계): 이름 -> 상위 클래스 포함 IFC 클래스 집합. "arch" 는 설비(mep)가 아닌 모든 것
PRESETS = {
    "structure": ("IfcBeam", "IfcColumn", "IfcFooting", "IfcPile", "IfcSlab", "IfcWall", "IfcMember", "IfcPlate"),
    "mep": ("IfcDistributionElement",),
}
GHOST_COLOR = (200, 200, 204)
GHOST_ALPHA = 77     # 고스트를 앞에 겹칠 때 섞는 비율(/256)


def _wanted(names, sup) -> bool:
    for n in names:
        if n == "arch":
            if "IfcDistributionElement" not in sup:
                return True
        elif any(c in sup for c in PRESETS.get(n, (n,))):
            return True
    return False


def element_mask(m: _mesh.Mesh, only=None, hide=None) -> list[bool]:
    """요소마다 보이는가: only(클래스·묶음 이름 목록, 상위 클래스도 맞음)에 들고 hide 에 들지 않으면 True."""
    return [(not only or _wanted(only, s)) and not (hide and _wanted(hide, s)) for s in m.supers]


def _subset(m: _mesh.Mesh, keep: list[bool]) -> _mesh.Mesh:
    """keep 인 요소만 남긴 메시(꼭짓점은 그대로, 면·요소 번호를 다시 매김)."""
    idx = np.cumsum(keep) - 1
    fk = np.array(keep, bool)[m.face_elem] if len(m.face_elem) else np.zeros(0, bool)
    return _mesh.Mesh(m.verts, m.faces[fk], idx[m.face_elem[fk]].astype(np.int32),
                      [e for e, k in zip(m.elems, keep) if k], [x for x, k in zip(m.mats, keep) if k],
                      [x for x, k in zip(m.supers, keep) if k])


def render_ifc(path: str, width: int = 1280, view: str = "iso", threads: int = 1, png_level: int = 6,
               only=None, hide=None, ghost: bool = False, tie_eps: float = 0.0) -> Result:
    """only/hide: 보일 범주(IFC 클래스 이름 또는 묶음 structure|mep|arch). ghost: 숨긴 요소를 반투명 회색으로 겹쳐 그린다.
    tie_eps: raster.render 참고(같은 평면 겹침 잡음 억제, 미터). 모두 기본값이면 이전과 같은 픽셀이다."""
    t0 = time.perf_counter()
    m = _mesh.extract(path, threads)
    t1 = time.perf_counter()
    if only or hide:
        keep = element_mask(m, only, hide)
        full, m = m, _subset(m, keep)
        r, u, _ = raster.camera_basis(view)
        x, y = raster.dot3(full.verts, r), raster.dot3(full.verts, u)
        fit = (x.min(), x.max(), y.min(), y.max()) if len(x) else None   # 숨긴 요소가 있어도 전체와 같은 틀
    else:
        fit = None
    fr = raster.render(m.verts, m.faces, m.face_elem, [c for _, c in m.elems], width=width, view=view,
                       elem_colors=[rgb or raster.material_color(nm) for nm, rgb in m.mats] if m.mats else None,
                       tie_eps=tie_eps, fit=fit)
    if (only or hide) and ghost:
        g = _subset(full, [not k for k in keep])
        gf = raster.render(g.verts, g.faces, g.face_elem, [c for _, c in g.elems], width=width, view=view,
                           elem_colors=[GHOST_COLOR] * len(g.elems), tie_eps=tie_eps, fit=fit)
        front = gf.depth > fr.depth            # 고스트가 앞에 있거나 보이는 요소가 없는 픽셀
        a = GHOST_ALPHA
        mix = (fr.color.astype(np.int32) * (256 - a) + gf.color.astype(np.int32) * a) >> 8
        fr.color[front] = mix[front].astype(np.uint8)
        fr.stats["ghost_pixels"] = int(front.sum())
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
