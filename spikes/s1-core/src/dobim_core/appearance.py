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


# ---------------------------------------------------------------- 재료 외관 정의(W2 다섯 번째 PR, DATA-05·VIS-07)
# 기본 재료 라이브러리: 재료 이름의 낱말 -> (색, 투명도 0~1, 광택 0~1). material.define 이 색을 주지 않으면 이 값으로 시작한다.
# 위 18단계 표(MATERIAL_COLORS)에 맞는 재료는 그 표가 먼저 정한다(같은 이름이면 IFC 기본 색과 라이브러리 색이 같다). 표에 없는 재료는 아래 추가 표.
# 라이브러리는 정의를 만들 때만 쓴다: 정의하지 않은 재료의 IFC 는 18단계 그대로다(기존 프로젝트의 IFC 바이트 불변).
LIB_TABLE = (   # MATERIAL_COLORS 와 같은 순서: (투명도, 광택)
    (0.0, 0.3),    # 녹청·동판
    (0.0, 0.05),   # 벽돌
    (0.6, 0.9),    # 유리
    (0.0, 0.2),    # 석재
    (0.0, 0.15),   # 목재
    (0.0, 0.1),    # 슬레이트
    (0.0, 0.4),    # 징크
)
LIB_EXTRA = (
    (("콘크리트", "concrete", "시멘트", "cement", "몰탈", "모르타르", "mortar"), (190, 190, 186), 0.0, 0.05),
    (("알루미늄", "aluminium", "aluminum"), (192, 196, 202), 0.0, 0.6),
    (("강재", "스틸", "steel", "스테인리스", "stainless", "철"), (122, 126, 134), 0.0, 0.5),
    (("석고", "gypsum", "plaster"), (236, 234, 228), 0.0, 0.05),
    (("단열", "insulation"), (232, 214, 120), 0.0, 0.0),
    (("타일", "tile", "세라믹", "ceramic"), (220, 216, 206), 0.0, 0.5),
    (("도장", "페인트", "paint"), (240, 240, 236), 0.0, 0.2),
    (("아스팔트", "asphalt"), (70, 70, 72), 0.0, 0.05),
)
assert len(LIB_TABLE) == len(MATERIAL_COLORS)


def library_appearance(name) -> dict | None:
    """재료 이름 -> 라이브러리 기본 외관 {"color": [r,g,b], "transparency", "shininess"} 또는 None(라이브러리에 없음)."""
    if not isinstance(name, str) or not name:
        return None
    low = name.lower()
    for (keys, rgb), (t, s) in zip(MATERIAL_COLORS, LIB_TABLE):
        if any(k in low for k in keys):
            return {"color": list(rgb), "transparency": t, "shininess": s}
    for keys, rgb, t, s in LIB_EXTRA:
        if any(k in low for k in keys):
            return {"color": list(rgb), "transparency": t, "shininess": s}
    return None


def library_rows() -> list:
    """라이브러리 전체(조회용): [{"keys", "color", "transparency", "shininess"}]."""
    rows = [(keys, rgb, t, s) for (keys, rgb), (t, s) in zip(MATERIAL_COLORS, LIB_TABLE)] + list(LIB_EXTRA)
    return [{"keys": list(keys), "color": list(rgb), "transparency": t, "shininess": s} for keys, rgb, t, s in rows]


def appearance_key(p: dict) -> tuple:
    """정의 파라미터(또는 라이브러리 값) -> 비교·투영용 튜플 (r, g, b, 투명도, 광택)."""
    r, g, b = p["color"]
    return (int(r), int(g), int(b), float(p["transparency"]), float(p["shininess"]))
