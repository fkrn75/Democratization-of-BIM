# SPDX-License-Identifier: MPL-2.0
"""외래 IFC 요소의 형상 입력 (1단계 외래 도면 경로, 설계: spikes/phase1-drawings/elements-design.md 29절).

의도(DoBIM_Intent)가 없는 외래 요소는 IFC 가 형상의 정본이다. 도면은 그 형상(ifcopenshell.geom 의 삼각형 메시)을 잘라 그린다.
이 모듈은 그래프 입력 ("fgeom", id) 의 값을 만든다. drawing.py 는 순수 모듈(파일·외부 라이브러리 금지)이라 형상 계산을 여기 둔다.

값(불변 튜플):
- 층(IfcBuildingStorey): ("storey", 고도 mm). 평면 뷰가 외래 층을 레벨로 쓸 때 절단 높이의 기준.
- 그리는 요소(IfcElement 중 개구부·가상 요소 제외): ("mesh", 범주, 꼭짓점((x,y,z) mm, 0.01mm 반올림, 용접), 삼각형((i,j,k), ...)).
- 공간 구조(IfcSpatialElement: 대지·건물·공간 등): ("spatial",). 자기 형상 없이 부분으로 이루어진 요소: ("aggregate",). 개구부 등 형상 특징·가상 요소: ("skip",). 셋 다 그리지 않고 생략으로 세지 않는다.
- 그 밖(형상 없음·그리지 않는 종류·형상 계산 실패·상한 초과): None. 도면은 이 요소를 생략하고 개수를 알린다.

결정성: 같은 파일·같은 ifcopenshell 판이면 같은 메시다(단일 스레드 create_shape). 좌표는 0.01mm 로 반올림하고 같은 좌표를 한 꼭짓점으로 합친다.
외래 요소의 형상은 바뀌지 않으므로(속성만 편집, ADR-0005) 값은 IfcModel 하나에 한 번만 계산해 둔다(_MEMO 속성).
"""
from __future__ import annotations

ROUND = 2                  # 꼭짓점 반올림 자릿수(mm 아래 0.01)
TRIS_MAX = 200_000         # 요소 하나의 삼각형 상한. 넘으면 그리지 않고 생략으로 센다(도면 시간 보호)
_MEMO = "_fgeom_memo"

# 범주: 절단선 굵기를 정한다. heavy = 절단선(cut), medium = 기호 굵기(symbol), light = 투영 굵기(proj). 투영선은 모두 proj.
# 순서대로 처음 맞는 상위 클래스를 쓴다(is_a 는 상속을 따른다). 표에 없는 IfcElement 는 light.
CATEGORY = (
    ("IfcWall", "heavy"), ("IfcColumn", "heavy"), ("IfcSlab", "heavy"), ("IfcBeam", "heavy"), ("IfcRoof", "heavy"),
    ("IfcStair", "heavy"), ("IfcStairFlight", "heavy"), ("IfcRamp", "heavy"), ("IfcRampFlight", "heavy"),
    ("IfcFooting", "heavy"), ("IfcPile", "heavy"), ("IfcChimney", "heavy"), ("IfcCurtainWall", "heavy"),
    ("IfcDoor", "medium"), ("IfcWindow", "medium"), ("IfcRailing", "medium"), ("IfcMember", "medium"),
    ("IfcPlate", "medium"), ("IfcCovering", "medium"), ("IfcBuildingElementProxy", "medium"),
)
# 그리지 않는 IfcElement: 개구부·돌출 같은 형상 특징(IfcFeatureElement)은 호스트 형상에 이미 반영되고, 가상 요소는 경계일 뿐이다.
SKIP = ("IfcFeatureElement", "IfcVirtualElement")


def category(ent) -> str | None:
    """IFC 엔터티의 선 범주. 그리지 않는 종류면 None."""
    if not ent.is_a("IfcElement") or any(ent.is_a(c) for c in SKIP):
        return None
    for cls, cat in CATEGORY:
        if ent.is_a(cls):
            return cat
    return "light"


def _settings():
    import ifcopenshell.geom
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)   # 세계 좌표(m). 개구부 불리언은 기본값대로 적용된다
    return s


def _storey_z(model, ent) -> float | None:
    """층의 절대 고도(mm): 배치 행렬의 z(프로젝트 단위) x 단위 배율. 배치가 없으면 Elevation 속성."""
    import ifcopenshell.util.placement as up
    import ifcopenshell.util.unit as uu
    scale = uu.calculate_unit_scale(model.f) * 1000.0
    if getattr(ent, "ObjectPlacement", None) is not None:
        return round(float(up.get_local_placement(ent.ObjectPlacement)[2][3]) * scale, ROUND)
    if getattr(ent, "Elevation", None) is not None:
        return round(float(ent.Elevation) * scale, ROUND)
    return None


def _mesh(model, ent, cat):
    import ifcopenshell.geom
    st = getattr(model, "_fgeom_settings", None)
    if st is None:
        st = _settings()
        model._fgeom_settings = st
    try:
        sh = ifcopenshell.geom.create_shape(st, ent)
    except Exception:   # 표현이 없거나 형상 커널이 못 푸는 요소: 생략으로 센다
        return None
    vs, fs = sh.geometry.verts, sh.geometry.faces
    if not vs or not fs or len(fs) // 3 > TRIS_MAX:
        return None
    index: dict = {}
    verts: list = []
    remap = []
    for i in range(0, len(vs), 3):
        p = (round(vs[i] * 1000.0, ROUND), round(vs[i + 1] * 1000.0, ROUND), round(vs[i + 2] * 1000.0, ROUND))
        p = tuple(0.0 if c == 0 else c for c in p)   # -0.0 을 0.0 으로(같은 좌표가 같은 키가 되게)
        k = index.get(p)
        if k is None:
            k = index[p] = len(verts)
            verts.append(p)
        remap.append(k)
    tris = set()
    for i in range(0, len(fs), 3):
        a, b, c = remap[fs[i]], remap[fs[i + 1]], remap[fs[i + 2]]
        if a == b or b == c or a == c:
            continue   # 반올림으로 퇴화한 삼각형
        m = min(a, b, c)   # 회전만 정규화(감김 방향 = 바깥 법선은 유지)
        t = (a, b, c) if m == a else (b, c, a) if m == b else (c, a, b)
        tris.add(t)
    if not tris:
        return None
    return ("mesh", cat, tuple(verts), tuple(sorted(tris)))


def load(model, row, eid):
    """그래프 입력 ("fgeom", eid) 의 값. row 는 요소 행(없으면 None)."""
    if row is None or row["kind"] != "foreign" or eid not in model.ents:
        return None
    memo = getattr(model, _MEMO, None)
    if memo is None:
        memo = {}
        setattr(model, _MEMO, memo)
    if eid in memo:
        return memo[eid]
    ent = model.ents[eid]
    if ent.is_a("IfcBuildingStorey"):
        z = _storey_z(model, ent)
        val = None if z is None else ("storey", z)
    elif ent.is_a("IfcSpatialElement"):
        val = ("spatial",)   # 대지·건물·공간·구역: 선으로 그리는 요소가 아니다(생략으로 세지 않는다)
    else:
        cat = category(ent)
        if cat is None:   # 개구부·돌출(형상 특징: 호스트 형상에 이미 반영됨)·가상 요소·IfcElement 가 아닌 제품: 그리지 않는 종류
            val = ("skip",) if ent.is_a("IfcFeatureElement") or ent.is_a("IfcVirtualElement") else None
        else:
            val = _mesh(model, ent, cat)
        if val is None and cat is not None and any(r.is_a("IfcRelAggregates") and r.RelatedObjects for r in getattr(ent, "IsDecomposedBy", ()) or ()):
            val = ("aggregate",)   # 자기 형상 없이 부분(계단참·경사면 등)으로 이루어진 요소: 부분이 그려진다(생략으로 세지 않는다)
    memo[eid] = val
    return val
