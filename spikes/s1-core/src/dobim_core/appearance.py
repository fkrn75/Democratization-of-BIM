# SPDX-License-Identifier: MPL-2.0
"""재료 이름 -> 겉모습 색(18단계). 재료를 처음 만들 때 이름의 낱말이 표에 맞으면 IFC 재료에 표면 스타일(IfcSurfaceStyleShading 색)을 붙인다.
표에 없는 재료(콘크리트·석고·단열재 등)는 스타일을 붙이지 않는다: 기존 프로젝트의 IFC 바이트·해시가 그대로다.
dobim_render 는 IFC 재료의 표면 스타일 색을 먼저 읽고, 없으면 같은 낱말 표(raster.MATERIAL_COLORS)를 쓴다(두 표는 같은 값이어야 한다: 시험)."""
from __future__ import annotations

MATERIAL_COLORS = (
    (("녹청", "patina", "동판", "copper"), (96, 156, 136)),
    (("벽돌", "brick"), (168, 86, 64)),
    (("유리", "glass"), (118, 170, 212)),
    (("석재", "stone", "화강석", "granite", "limestone", "sandstone", "대리석", "marble"), (214, 207, 190)),
    (("목재", "wood", "timber"), (150, 98, 58)),
    (("슬레이트", "slate"), (92, 96, 104)),
    (("징크", "아연", "zinc"), (128, 134, 140)),
)


def material_rgb(name) -> tuple | None:
    """재료 이름 -> (r, g, b) 0~255 또는 None. 앞에서부터 먼저 맞는 낱말."""
    if not isinstance(name, str) or not name:
        return None
    low = name.lower()
    for keys, rgb in MATERIAL_COLORS:
        if any(k in low for k in keys):
            return rgb
    return None
