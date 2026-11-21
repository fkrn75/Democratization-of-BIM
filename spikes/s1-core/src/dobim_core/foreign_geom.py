# SPDX-License-Identifier: MPL-2.0
"""외래 IFC 요소의 형상 입력 (1단계 외래 도면 경로, 설계: spikes/phase1-drawings/elements-design.md 29절).

의도(DoBIM_Intent)가 없는 외래 요소는 IFC 가 형상의 정본이다. 도면은 그 형상(ifcopenshell.geom 의 삼각형 메시)을 잘라 그린다.
이 모듈은 그래프 입력 ("fgeom", id) 의 값을 만든다. drawing.py 는 순수 모듈(파일·외부 라이브러리 금지)이라 형상 계산을 여기 둔다.

값(불변 튜플):
- 층(IfcBuildingStorey): ("storey", 고도 mm). 평면 뷰가 외래 층을 레벨로 쓸 때 절단 높이의 기준.
- 그리는 요소(IfcElement 중 개구부·가상 요소 제외): ("mesh", 범주, 꼭짓점((x,y,z) mm, 0.01mm 반올림, 용접), 삼각형((i,j,k), ...)).
- 공간(IfcSpace, 2차): ("space", 실명, 실번호, 바닥 무게중심 (x, y) mm, 바닥 면적 mm², (최저 z, 최고 z) mm). 평면 뷰가 실명·면적 문자를 놓는 데 쓴다.
  실명은 LongName(없으면 Name), 실번호는 LongName 이 있을 때의 Name. 바닥은 아래를 보는 삼각형(법선 z < 0)을 수평면에 투영한 것이다. 형상이 없거나 이름이 없으면 ("spatial",).
- 그 밖의 공간 구조(IfcSpatialElement: 대지·건물 등): ("spatial",). 자기 형상 없이 부분으로 이루어진 요소: ("aggregate",). 개구부 등 형상 특징·가상 요소: ("skip",). 셋 다 그리지 않고 생략으로 세지 않는다.
- 그 밖(형상 없음·그리지 않는 종류·형상 계산 실패·상한 초과): None. 도면은 이 요소를 생략하고 개수를 알린다.

문·창 평면 기호 입력(외래 도면 3차, 그래프 입력 ("fopen", id), opening 함수):
- IfcDoor: ("door", 여닫이 방식 또는 None, 원점 (x, y) mm, 국소 x 축 (x, y), 국소 y 축 (x, y), 전체 폭 mm 또는 None)
- IfcWindow: ("window", 칸 나눔 또는 None, 창짝 여닫이 방식들(튜플), 원점, 국소 x 축, 국소 y 축, 전체 폭 mm 또는 None)
  여닫이 방식은 요소의 OperationType(IFC4)이 정해져 있으면 그것, 아니면 유형(IfcDoorType·IfcDoorStyle)의 값이다. NOTDEFINED·USERDEFINED 는 None.
  창의 칸 나눔은 PartitioningType(IFC4 요소, 없으면 유형) 또는 IFC2x3 IfcWindowStyle.OperationType, 창짝은 유형의 IfcWindowPanelProperties.OperationType.
  배치가 수직으로 서 있지 않은(국소 x·y 축이 수평이 아닌) 문·창, 문·창이 아닌 요소는 None(기호 없이 형상 그대로 그린다).

결정성: 같은 파일·같은 ifcopenshell 판이면 같은 메시다. 좌표는 0.01mm 로 반올림하고 같은 좌표를 한 꼭짓점으로 합친다.
외래 요소의 형상은 바뀌지 않으므로(속성만 편집, ADR-0005) 값은 IfcModel 하나에 한 번만 계산해 둔다(_MEMO 속성).
미리 계산(IO-04 4차, 대형 외래 모델 성능): 첫 형상 요청 때 그리는 종류의 외래 요소 전체를 ifcopenshell.geom.iterator(여러 스레드)로 한꺼번에
삼각형화해 둔다(_RAW 속성). 요소 하나의 삼각형화는 스레드와 무관하게 같은 입력·설정의 같은 계산이라 create_shape 와 같은 메시다(표본 기준선으로 확인).
iterator 가 내지 못한 요소(실패·표현 없음)는 예전처럼 create_shape 로 하나씩 계산한다. 요소가 PREFETCH_MIN 개보다 적으면 미리 계산하지 않는다.
저장 캐시(ADR-0017): 가져올 때 그리는 외래 요소 전체의 정리 메시(_clean 결과)를 위 길 그대로 한 번 계산해(compute_all) 프로젝트 파일에 담는다(pack).
열 때 키(원본 IFC sha256·ifcopenshell 판·삼각형화 설정·정리 규칙 판·미리 계산 문턱)가 같고 바이트가 온전하면(unpack) 삼각형화 없이 그 메시를 쓴다(_STORE).
캐시는 파생물이다: 없거나 키가 다르거나 깨졌으면 지금처럼 계산하고 값은 같다.
"""
from __future__ import annotations

ROUND = 2                  # 꼭짓점 반올림 자릿수(mm 아래 0.01)
TRIS_MAX = 200_000         # 요소 하나의 삼각형 상한. 넘으면 그리지 않고 생략으로 센다(도면 시간 보호)
_MEMO = "_fgeom_memo"
_RAW = "_fgeom_raw"        # 미리 계산한 삼각형화 {GlobalId: (verts, faces)}
PREFETCH_MIN = 64          # 외래 그리는 요소가 이보다 많을 때만 iterator 로 미리 계산한다(작은 모델은 스레드 준비 비용이 더 크다)
_STORE = "_fgeom_store"    # 프로젝트 파일에 저장한 정리 메시(Store). 있으면 _mesh 가 삼각형화 대신 쓴다
SETTINGS = (("use-world-coords", True),)   # 삼각형화 설정(세계 좌표 m). 개구부 불리언 등 나머지는 ifcopenshell 기본값(판 번호가 캐시 키에 든다)
MESH_FORMAT = "1"          # _clean 의 정리 규칙(반올림·용접·퇴화 제거·정규화·상한)이나 저장 바이트 배치를 바꾸면 올린다(저장 캐시 무효화)

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


_CAT_MEMO: dict = {}   # {스키마 포함 클래스 이름: 범주}: 범주는 엔터티 클래스(상속)로만 정해진다(속도만)


def category(ent) -> str | None:
    """IFC 엔터티의 선 범주. 그리지 않는 종류면 None. 같은 스키마·같은 클래스는 값이 같아 클래스마다 한 번만 판정한다."""
    k = ent.is_a(True)
    if k in _CAT_MEMO:
        return _CAT_MEMO[k]
    val = _category(ent)
    _CAT_MEMO[k] = val
    return val


def _category(ent) -> str | None:
    if not ent.is_a("IfcElement") or any(ent.is_a(c) for c in SKIP):
        return None
    for cls, cat in CATEGORY:
        if ent.is_a(cls):
            return cat
    return "light"


def _settings():
    import ifcopenshell.geom
    s = ifcopenshell.geom.settings()
    for k, v in SETTINGS:   # 세계 좌표(m). 개구부 불리언은 기본값대로 적용된다
        s.set(k, v)
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


def _model_settings(model):
    st = getattr(model, "_fgeom_settings", None)
    if st is None:
        st = _settings()
        model._fgeom_settings = st
    return st


def _drawable(model, ids) -> list:
    """ids 중 미리 계산·저장 대상: 모델에 있고 그리는 종류(category)이며 표현이 있는 요소(ids 순서)."""
    ents = [model.ents[i] for i in ids if i in model.ents]
    return [e for e in ents if category(e) is not None and getattr(e, "Representation", None) is not None]


def prefetch(model, ids) -> int:
    """외래 요소 ids 중 그리는 종류(category 가 있는 IfcElement)를 iterator 로 한꺼번에 삼각형화해 _RAW 에 둔다. 미리 계산한 수를 돌려준다.
    이미 했거나 요소가 PREFETCH_MIN 개보다 적거나 iterator 를 시작하지 못하면 아무것도 하지 않는다(_mesh 가 하나씩 계산한다)."""
    if getattr(model, _RAW, None) is not None:
        return 0
    setattr(model, _RAW, {})
    ents = _drawable(model, ids)
    if len(ents) < PREFETCH_MIN:
        return 0
    import os
    import ifcopenshell.geom
    raw = {}
    try:
        it = ifcopenshell.geom.iterator(_model_settings(model), model.f, max(1, os.cpu_count() or 1), include=ents)
        if it.initialize():
            while True:
                sh = it.get()
                raw[sh.guid] = (tuple(sh.geometry.verts), tuple(sh.geometry.faces))
                if not it.next():
                    break
    except Exception:   # iterator 를 쓸 수 없으면 하나씩 계산하는 예전 길로
        raw = {}
    setattr(model, _RAW, raw)
    return len(raw)


def _raw_geom(model, ent):
    """요소 하나의 삼각형화 (꼭짓점 m, 면 색인). 미리 계산한 값이 있으면 그것, 없으면 create_shape. 계산하지 못하면 None."""
    import ifcopenshell.geom
    got = (getattr(model, _RAW, None) or {}).get(getattr(ent, "GlobalId", None))
    if got is not None:
        return got
    try:
        sh = ifcopenshell.geom.create_shape(_model_settings(model), ent)
    except Exception:   # 표현이 없거나 형상 커널이 못 푸는 요소: 생략으로 센다
        return None
    return sh.geometry.verts, sh.geometry.faces


def _clean(vs, fs):
    """삼각형화를 도면 입력 메시로 정리한다: mm 로 바꿔 0.01mm 반올림, 같은 좌표 용접, 퇴화 삼각형 제거, 회전 정규화.
    (꼭짓점들, 정렬한 삼각형들) 또는 None(비었거나 상한 초과). 저장 캐시(MESH_FORMAT)는 이 함수의 결과를 담는다."""
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
    return tuple(verts), tuple(sorted(tris))


def _mesh(model, ent, cat):
    store = getattr(model, _STORE, None)
    gid = getattr(ent, "GlobalId", None)
    if store is not None and gid in store:   # 저장한 정리 메시(가져올 때 계산했다, 아래 "저장 캐시")
        g = store.get(gid)
    else:
        raw = _raw_geom(model, ent)
        g = None if raw is None else _clean(*raw)
    return None if g is None else ("mesh", cat) + g


def _text(v) -> str | None:
    """IFC 문자열 속성을 문자로(앞뒤 공백 제거, 앞 40자). 비었거나 문자열이 아니면 None."""
    return v.strip()[:40] if isinstance(v, str) and v.strip() else None


def _space(model, ent):
    """IfcSpace 의 ("space", 실명, 실번호, (cx, cy), 면적 mm², (z0, z1)). 형상·이름이 없으면 ("spatial",)."""
    long_name, name = _text(getattr(ent, "LongName", None)), _text(getattr(ent, "Name", None))
    title, number = (long_name, name) if long_name else (name, None)
    if title is None:
        return ("spatial",)
    m = _mesh(model, ent, "space")
    if m is None:
        return ("spatial",)
    _, _, verts, tris = m
    area = sx = sy = 0.0
    for i, j, k in tris:   # 아래를 보는 삼각형(바닥)의 수평 투영: 넓이와 넓이 가중 무게중심
        a, b, c = verts[i], verts[j], verts[k]
        cr = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])   # 2 x 투영 넓이(부호 = 법선 z 의 부호)
        if cr >= 0:
            continue
        w = -cr / 2.0
        area += w
        sx += w * (a[0] + b[0] + c[0]) / 3.0
        sy += w * (a[1] + b[1] + c[1]) / 3.0
    if area < 1.0:
        return ("spatial",)
    zs = [p[2] for p in verts]
    return ("space", title, number, (round(sx / area, ROUND), round(sy / area, ROUND)), round(area, ROUND), (min(zs), max(zs)))


_OPEN_MEMO = "_fopen_memo"
_UNDEF = (None, "NOTDEFINED", "USERDEFINED")


def _enum(v) -> str | None:
    """IFC 열거값을 문자로. 정하지 않음(NOTDEFINED·USERDEFINED·없음)은 None."""
    return None if v in _UNDEF or not isinstance(v, str) else v


def _type_of(ent):
    import ifcopenshell.util.element as ue
    try:
        return ue.get_type(ent)
    except Exception:   # 유형 관계가 깨진 파일: 유형 없이 본다
        return None


def _opening_frame(model, ent):
    """(원점 (x, y) mm, 국소 x 축 (x, y), 국소 y 축 (x, y)) 또는 None(배치 없음·수직이 아님)."""
    import ifcopenshell.util.placement as up
    import ifcopenshell.util.unit as uu
    if getattr(ent, "ObjectPlacement", None) is None:
        return None
    m = up.get_local_placement(ent.ObjectPlacement)
    scale = uu.calculate_unit_scale(model.f) * 1000.0
    xa = [float(m[i][0]) for i in range(3)]
    ya = [float(m[i][1]) for i in range(3)]
    if abs(xa[2]) > 1e-6 or abs(ya[2]) > 1e-6:
        return None   # 기울어진 문·창: 평면 기호를 정할 수 없다
    nx, ny = (xa[0] ** 2 + xa[1] ** 2) ** 0.5, (ya[0] ** 2 + ya[1] ** 2) ** 0.5
    if nx < 1e-9 or ny < 1e-9:
        return None

    def r(c):
        c = round(c, 9)
        return 0.0 if c == 0 else c
    org = tuple(0.0 if c == 0 else c for c in (round(float(m[0][3]) * scale, ROUND), round(float(m[1][3]) * scale, ROUND)))
    return org, (r(xa[0] / nx), r(xa[1] / nx)), (r(ya[0] / ny), r(ya[1] / ny))


def _overall_width(model, ent) -> float | None:
    import ifcopenshell.util.unit as uu
    w = getattr(ent, "OverallWidth", None)
    if not isinstance(w, (int, float)) or w <= 0:
        return None
    return round(float(w) * uu.calculate_unit_scale(model.f) * 1000.0, ROUND)


def _opening_value(model, ent):
    is_door, is_window = ent.is_a("IfcDoor"), ent.is_a("IfcWindow")
    if not (is_door or is_window):
        return None
    fr = _opening_frame(model, ent)
    if fr is None:
        return None
    t = _type_of(ent)
    width = _overall_width(model, ent)
    if is_door:
        op = _enum(getattr(ent, "OperationType", None)) or (_enum(getattr(t, "OperationType", None)) if t is not None else None)
        return ("door", op) + fr + (width,)
    part = _enum(getattr(ent, "PartitioningType", None))
    if part is None and t is not None:   # IFC4 유형의 칸 나눔, IFC2x3 IfcWindowStyle 은 OperationType 이 칸 나눔이다
        part = _enum(getattr(t, "PartitioningType", None)) or (_enum(getattr(t, "OperationType", None)) if t.is_a("IfcWindowStyle") else None)
    panels = []
    for ps in ((getattr(t, "HasPropertySets", None) or ()) if t is not None else ()):
        if ps.is_a("IfcWindowPanelProperties"):
            panels.append(_enum(getattr(ps, "OperationType", None)) or "NOTDEFINED")
    return ("window", part, tuple(panels)) + fr + (width,)


def opening(model, row, eid):
    """그래프 입력 ("fopen", eid) 의 값(외래 도면 3차). 외래 문·창이 아니면 None. 외래 요소의 IFC 는 바뀌지 않으므로 한 번만 계산해 둔다."""
    if row is None or row["kind"] != "foreign" or eid not in model.ents:
        return None
    memo = getattr(model, _OPEN_MEMO, None)
    if memo is None:
        memo = {}
        setattr(model, _OPEN_MEMO, memo)
    if eid not in memo:
        memo[eid] = _opening_value(model, model.ents[eid])
    return memo[eid]


_KLASS_MEMO: dict = {}   # {스키마 포함 클래스 이름: "storey" | "space" | "spatial" | None}(속도만)


def _klass(ent):
    """load 가 가르는 공간 구조 종류. 엔터티 클래스(상속)로만 정해져 클래스마다 한 번만 판정한다(예전 is_a 순서와 같다)."""
    k = ent.is_a(True)
    if k not in _KLASS_MEMO:
        _KLASS_MEMO[k] = ("storey" if ent.is_a("IfcBuildingStorey") else "space" if ent.is_a("IfcSpace")
                          else "spatial" if ent.is_a("IfcSpatialElement") else None)
    return _KLASS_MEMO[k]


def load(model, row, eid, foreign_ids=None):
    """그래프 입력 ("fgeom", eid) 의 값. row 는 요소 행(없으면 None). foreign_ids 는 외래 요소 id 들을 돌려주는 함수(첫 요청 때 미리 계산할 대상)."""
    if row is None or row["kind"] != "foreign" or eid not in model.ents:
        return None
    if foreign_ids is not None and getattr(model, _RAW, None) is None:
        prefetch(model, foreign_ids())
    memo = getattr(model, _MEMO, None)
    if memo is None:
        memo = {}
        setattr(model, _MEMO, memo)
    if eid in memo:
        return memo[eid]
    ent = model.ents[eid]
    kl = _klass(ent)
    if kl == "storey":
        z = _storey_z(model, ent)
        val = None if z is None else ("storey", z)
    elif kl == "space":
        val = _space(model, ent)   # 공간: 실명 문자를 놓을 바닥 위치(생략으로 세지 않는다)
    elif kl == "spatial":
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


# ---------------------------------------------------------------- 저장 캐시(ADR-0017)
def cache_key(base_sha256: str) -> str:
    """저장 캐시의 키. 이 중 하나라도 다르면 저장한 메시를 쓰지 않고 다시 계산한다: 원본 IFC(외래 기반 sha256), ifcopenshell 판(형상 커널 포함),
    삼각형화 설정, 정리 규칙 판과 그 상수, 미리 계산 문턱(iterator 와 create_shape 중 어느 길로 계산했는지가 갈린다)."""
    import json
    import ifcopenshell
    return json.dumps({"base_sha256": base_sha256, "ifcopenshell": ifcopenshell.version, "settings": [list(kv) for kv in SETTINGS],
                       "mesh_format": MESH_FORMAT, "round": ROUND, "tris_max": TRIS_MAX, "prefetch_min": PREFETCH_MIN},
                      sort_keys=True, separators=(",", ":"))


def compute_all(model, ids) -> dict:
    """ids(외래 요소 id 들) 중 저장 대상 요소 전체의 정리 메시 {GlobalId: (꼭짓점들, 삼각형들) 또는 None}. 뷰가 요청할 때와 같은 길이다:
    먼저 prefetch(문턱 이상이면 iterator), 그다음 요소마다 미리 계산한 값 또는 create_shape 를 _clean 으로 정리한다."""
    prefetch(model, ids)
    out = {}
    for e in _drawable(model, ids):
        raw = _raw_geom(model, e)
        out[e.GlobalId] = None if raw is None else _clean(*raw)
    return out


def _le(a) -> bytes:
    """array 를 리틀 엔디언 바이트로(저장 바이트 배치는 기계와 무관하게 고정)."""
    import sys
    if sys.byteorder != "little":
        a = a.__copy__()
        a.byteswap()
    return a.tobytes()


def _from_le(code: str, data: bytes):
    import sys
    from array import array
    a = array(code)
    a.frombytes(data)
    if sys.byteorder != "little":
        a.byteswap()
    return a


def _digest(cols) -> str:
    import hashlib
    h = hashlib.sha256()
    for b in cols:
        h.update(len(b).to_bytes(8, "little"))
        h.update(b)
    return h.hexdigest()


def pack(meshes: dict) -> tuple:
    """정리 메시들을 저장 바이트로: (ids, counts, verts, tris, digest). 앞 넷은 zlib 압축이다.
    ids = GlobalId 를 줄바꿈으로 이은 ASCII(정렬), counts = 요소마다 (꼭짓점 수, 삼각형 수) int64(None 이면 -1, -1),
    verts = 꼭짓점 좌표 float64(반올림한 값 그대로라 되읽어도 같은 수), tris = 요소 안 꼭짓점 색인 int32. digest = 압축한 넷의 sha256."""
    import zlib
    from array import array
    ids = sorted(meshes)
    counts, verts, tris = array("q"), array("d"), array("i")
    for gid in ids:
        g = meshes[gid]
        if g is None:
            counts.extend((-1, -1))
            continue
        vs, ts = g
        counts.extend((len(vs), len(ts)))
        for p in vs:
            verts.extend(p)
        for t in ts:
            tris.extend(t)
    cols = tuple(zlib.compress(b, 6) for b in ("\n".join(ids).encode("ascii"), _le(counts), _le(verts), _le(tris)))
    return cols + (_digest(cols),)


class Store:
    """저장 캐시를 푼 것. 요소 메시는 요청할 때 튜플로 만든다(_clean 의 결과와 같은 값). dict 처럼 in·get 을 쓴다."""

    def __init__(self, index: dict, verts, tris):
        self._index, self._verts, self._tris = index, verts, tris

    def __contains__(self, gid) -> bool:
        return gid in self._index

    def __len__(self) -> int:
        return len(self._index)

    def get(self, gid):
        ent = self._index.get(gid)
        if ent is None:
            return None
        v0, nv, t0, nt = ent
        it = iter(self._verts[v0 * 3:(v0 + nv) * 3])
        verts = tuple(zip(it, it, it))
        it = iter(self._tris[t0 * 3:(t0 + nt) * 3])
        return verts, tuple(zip(it, it, it))


def unpack(ids_z: bytes, counts_z: bytes, verts_z: bytes, tris_z: bytes, digest: str) -> Store | None:
    """pack 의 바이트를 푼다. 다이제스트·길이가 하나라도 맞지 않으면 None(캐시가 깨졌다: 부르는 쪽이 다시 계산한다)."""
    try:
        if _digest((ids_z, counts_z, verts_z, tris_z)) != digest:
            return None
        import zlib
        raw_ids = zlib.decompress(ids_z).decode("ascii")
        ids = raw_ids.split("\n") if raw_ids else []
        counts = _from_le("q", zlib.decompress(counts_z))
        verts = _from_le("d", zlib.decompress(verts_z))
        tris = _from_le("i", zlib.decompress(tris_z))
    except Exception:   # 압축·바이트가 깨졌다
        return None
    if len(counts) != 2 * len(ids) or len(set(ids)) != len(ids):
        return None
    index: dict = {}
    v0 = t0 = 0
    for k, gid in enumerate(ids):
        nv, nt = counts[2 * k], counts[2 * k + 1]
        if nv == -1 and nt == -1:
            index[gid] = None
            continue
        if nv <= 0 or nt <= 0:
            return None
        index[gid] = (v0, nv, t0, nt)
        v0 += nv
        t0 += nt
    if v0 * 3 != len(verts) or t0 * 3 != len(tris):
        return None
    return Store(index, verts, tris)


def attach(model, store) -> None:
    """저장(또는 방금 계산한) 정리 메시를 모델에 붙인다. 저장 대상 요소는 _mesh 가 이것을 쓰므로 미리 계산(_RAW)은 하지 않는다."""
    setattr(model, _STORE, store)
    setattr(model, _RAW, {})
