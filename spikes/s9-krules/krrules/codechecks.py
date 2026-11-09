"""IDS 로 표현하기 어려운 한국 납품 규칙의 코드 검사.

각 검사는 dict 를 돌려준다:
  status: pass | fail | na(해당 없음) | warn(자동 판정 불가·참고)
  applicable / failed: 대상 수 / 위반 수
  examples: 위반 사례 최대 5개(클래스·이름·GlobalId·이유)
조항 근거와 IFC 사상은 requirements.json 에 있다. 사상은 원문이 정한 것이 아니라 해석이다.
"""
from __future__ import annotations

import itertools
import os
import re
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as uel
import ifcopenshell.util.unit as uunit

MAX_EXAMPLES = 5
SIZE_LIMIT_BYTES = 200 * 1024 * 1024  # 시행지침 발주자편 부속서2: 원본 200MB 초과 시 분할 검토(IFC 에 준용)
MAX_SPACES = 3000          # KR-CODE-10: 공간 형상을 계산할 최대 개수(넘으면 판정 생략, 경고)
MAX_GEOM_ELEMENTS = 20000  # KR-CODE-17: 형상 중복을 계산할 최대 객체 수(넘으면 GlobalId 중복만 판정)


def _res(status, applicable=0, failed=0, examples=None, note=""):
    return {"status": status, "applicable": applicable, "failed": failed,
            "examples": (examples or [])[:MAX_EXAMPLES], "note": note}


def _ex(e, reason):
    return {"class": e.is_a(), "name": getattr(e, "Name", None),
            "global_id": getattr(e, "GlobalId", None), "reason": reason}


def _storey_of(e):
    """객체가 속한 층을 찾는다(포함 관계 -> 없으면 집합(Aggregate) 부모를 따라 올라감)."""
    seen = 0
    cur = e
    while cur is not None and seen < 10:
        if cur.is_a("IfcBuildingStorey"):
            return cur
        c = uel.get_container(cur)
        while c is not None:
            if c.is_a("IfcBuildingStorey"):
                return c
            c = uel.get_aggregate(c)
        cur = uel.get_aggregate(cur)
        seen += 1
    return None


# ---------------------------------------------------------------- 파일·형식

def schema_ok(name):
    """스키마 이름이 IFC 2x3 이상인지. IFC2X_FINAL·IFC2X2_FINAL 등 2x3 미만과 알 수 없는 이름은 거짓."""
    n = (name or "").upper()
    return n == "IFC2X3" or n.startswith("IFC4")


def check_schema_name(name):
    ok = schema_ok(name)
    return _res("pass" if ok else "fail", 1, 0 if ok else 1, [] if ok else [{"reason": f"스키마 {name}"}],
                note=f"스키마={name}")


def check_schema(f, **_):
    """KR-CODE-01: 제출 IFC 는 2x3 이상(조달청 5.2.1(2), 5.5.2(1)). IFC2X2 이하는 실행기가 헤더에서 먼저 걸러 같은 판정을 쓴다."""
    return check_schema_name(f.schema)


# 원문(조달청 5.5.2(3) 표)의 구분 코드는 대문자, 확장자 ifc 는 소문자라 대소문자를 구분한다
_PPS_NAME = re.compile(r"^(\d{2})_(.+)_(BA|BS|BM|BE|BC|BL|B|E)\.ifc$|^00_전체\.ifc$")


def check_filename_pps(f, filename="", deliverable=False, **_):
    """KR-CODE-02: 납품 IFC 파일명 = 건물번호2자리_건물명_구분(BA/BS/BM/BE/BC/BL/B/E).ifc (조달청 5.5.2(3))."""
    if not deliverable:
        return _res("na", note="납품 파일명 규칙: 납품 후보 파일에만 적용(--deliverable)")
    ok = bool(_PPS_NAME.match(filename))
    return _res("pass" if ok else "fail", 1, 0 if ok else 1,
                [] if ok else [{"reason": f"파일명 '{filename}' 이 NN_건물명_구분.ifc 형식이 아님"}])


_CHARSET = re.compile(r"^[A-Za-z0-9가-힣_\-]+$")


def check_filename_charset(f, filename="", deliverable=False, **_):
    """KR-CODE-03: BIM 데이터 파일명은 영문·한글·숫자·'-'·'_' 만(시행지침 설계자편 3.1(3)(가), 발주자편 3.6.3·부속서1 5.3 나)."""
    if not deliverable:
        return _res("na", note="납품 후보 파일에만 적용(--deliverable)")
    stem = Path(filename).stem
    ok = bool(_CHARSET.match(stem))
    return _res("pass" if ok else "fail", 1, 0 if ok else 1,
                [] if ok else [{"reason": f"파일명 '{stem}' 에 허용되지 않는 문자"}])


def _length_unit(f):
    try:
        u = uunit.get_project_unit(f, "LENGTHUNIT")
    except Exception:
        u = None
    if u is None:
        return None, None
    return getattr(u, "Prefix", None), getattr(u, "Name", None)


def check_unit_mm(f, **_):
    """KR-CODE-04: 길이 단위 mm 원칙(조달청 5.2.2(5)다)."""
    prefix, name = _length_unit(f)
    ok = (prefix == "MILLI" and name == "METRE")
    return _res("pass" if ok else "fail", 1, 0 if ok else 1,
                [] if ok else [{"reason": f"길이 단위 {prefix or ''}{name}"}], note=f"길이 단위={prefix or ''}{name}")


def check_unit_m_or_mm(f, **_):
    """KR-CODE-05: 길이 단위 m 또는 mm(국토부 기본지침·시행지침 설계자편 2.3.2(1)(가))."""
    prefix, name = _length_unit(f)
    ok = (name == "METRE" and prefix in (None, "MILLI"))
    return _res("pass" if ok else "fail", 1, 0 if ok else 1,
                [] if ok else [{"reason": f"길이 단위 {prefix or ''}{name}"}], note=f"길이 단위={prefix or ''}{name}")


def check_filesize(f, path=None, **_):
    """KR-CODE-16: 파일이 200MB 를 넘으면 분할 검토(시행지침 발주자편 부속서2 12번, 원본 기준을 IFC 에 준용)."""
    if not path:
        return _res("na")
    size = os.path.getsize(path)
    ok = size <= SIZE_LIMIT_BYTES
    return _res("pass" if ok else "warn", 1, 0 if ok else 1,
                [] if ok else [{"reason": f"{size} 바이트 > 200MiB"}], note=f"{size} 바이트")


# ---------------------------------------------------------------- 공간 구조

def check_containment(f, **_):
    """KR-CODE-06: 모든 공간객체·부위객체는 특정 층에 소속(조달청 5.2.2(6)가, 시행지침 설계자편 2.3.2(2))."""
    targets = [e for e in f.by_type("IfcElement") if not e.is_a("IfcOpeningElement") and not e.is_a("IfcVirtualElement")]
    targets += list(f.by_type("IfcSpace"))
    bad = []
    for e in targets:
        if _storey_of(e) is None:
            bad.append(_ex(e, "소속 층 없음"))
    return _res("na" if not targets else ("pass" if not bad else "fail"), len(targets), len(bad), bad)


def check_basement_names(f, **_):
    """KR-CODE-07: 지하층 이름은 'B' 또는 '지하' 로 시작(조달청 5.2.2(6)라). 지하층 = 고도<0 인 층(해석)."""
    storeys = [s for s in f.by_type("IfcBuildingStorey") if s.Elevation is not None and s.Elevation < 0]
    if not storeys:
        return _res("na", note="고도<0 인 층 없음")
    bad = [_ex(s, f"지하층 이름 '{s.Name}'") for s in storeys if not (s.Name or "").startswith(("B", "지하"))]
    return _res("pass" if not bad else "fail", len(storeys), len(bad), bad)


_SPACE_ID = re.compile(r"^(0[1-9]|[1-8][0-9]|98)\.\d{3}\.\d{2}$")


def check_space_id_format(f, **_):
    """KR-CODE-08: 실ID 형식 AA.BBB.CC (AA=시설-구역 01~89·공용 98, 조달청 부속서-1 3.1(3)). 변형 허용이라 권고 수준."""
    spaces = list(f.by_type("IfcSpace"))
    if not spaces:
        return _res("na")
    bad = [_ex(s, f"실ID '{s.Name}' 형식 불일치") for s in spaces if not _SPACE_ID.match(s.Name or "")]
    return _res("pass" if not bad else "fail", len(spaces), len(bad), bad)


_SPACE_CLASS = re.compile(r"^3[34]\d{3}$")


def check_space_class_code(f, **_):
    """KR-CODE-09: 공간분류코드는 부속서-1 3.2 목록의 5자리 세분류(33xxx·34xxx) 형식. 목록 전체 대조는 하지 않음."""
    spaces = list(f.by_type("IfcSpace"))
    if not spaces:
        return _res("na")
    bad = []
    for s in spaces:
        codes = []
        for rel in getattr(s, "HasAssociations", []) or []:
            if rel.is_a("IfcRelAssociatesClassification"):
                ref = rel.RelatingClassification
                codes.append(getattr(ref, "Identification", None) or getattr(ref, "ItemReference", None))
        if not any(c and _SPACE_CLASS.match(str(c)) for c in codes):
            bad.append(_ex(s, f"분류 코드 {codes or '없음'}"))
    return _res("pass" if not bad else "fail", len(spaces), len(bad), bad)


def _bbox(shape_cache, f, e, settings):
    if e.id() in shape_cache:
        return shape_cache[e.id()]
    box = None
    try:
        sh = ifcopenshell.geom.create_shape(settings, e)
        v = sh.geometry.verts
        xs, ys, zs = v[0::3], v[1::3], v[2::3]
        if xs:
            box = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
    except Exception:
        box = None
    shape_cache[e.id()] = box
    return box


def check_space_overlap(f, tol=0.01, **_):
    """KR-CODE-10: 같은 층의 공간객체끼리 겹치지 않음(조달청 5.2.2(3)바, 5.3(4)가).
    근사: 같은 층 공간의 축 정렬 경계상자(AABB)가 tol(m)보다 깊게 겹치면 위반. 비직사각형 방은 오탐 가능.
    층이 다른 공간은 비교하지 않고(소속 층이 없는 공간끼리만 한 묶음), 공간이 MAX_SPACES 를 넘으면 판정을 생략한다."""
    spaces = list(f.by_type("IfcSpace"))
    if len(spaces) < 2:
        return _res("na", note="공간 2개 미만")
    if len(spaces) > MAX_SPACES:
        return _res("warn", 0, 0, [{"reason": f"공간 {len(spaces)}개 > 한도 {MAX_SPACES}: 겹침 판정 생략"}],
                    note=f"공간 {len(spaces)}개가 한도 {MAX_SPACES} 초과라 판정하지 않음")
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    cache = {}
    groups = {}  # 소속 층 id(없으면 None) -> [(공간, 상자)]
    missing = 0
    for s in spaces:
        b = _bbox(cache, f, s, settings)
        if b is None:
            missing += 1
            continue
        st = _storey_of(s)
        groups.setdefault(st.id() if st is not None else None, []).append((s, b))
    bad = []
    pairs = 0
    for key in sorted(groups, key=lambda k: -1 if k is None else k):
        items = sorted(groups[key], key=lambda it: it[1][0])  # x 최솟값 순(스윕으로 후보 쌍을 줄임)
        n = len(items)
        pairs += n * (n - 1) // 2
        for i in range(n):
            a, ba = items[i]
            for k in range(i + 1, n):
                b, bb = items[k]
                if bb[0] >= ba[3] - tol:  # 이후 상자는 x 로 겹칠 수 없음
                    break
                dx = min(ba[3], bb[3]) - max(ba[0], bb[0])
                dy = min(ba[4], bb[4]) - max(ba[1], bb[1])
                dz = min(ba[5], bb[5]) - max(ba[2], bb[2])
                if dx > tol and dy > tol and dz > tol:
                    bad.append({"class": "IfcSpace", "name": f"{a.Name} / {b.Name}", "global_id": a.GlobalId,
                                "reason": f"AABB 겹침 {dx:.3f}x{dy:.3f}x{dz:.3f} m"})
    bad.sort(key=lambda e: (e["name"] or "", e["global_id"] or ""))
    note = "AABB 근사, 같은 층끼리만" + (f"; 형상 없는 공간 {missing}개 제외" if missing else "")
    if pairs == 0:
        return _res("na", note=note + "; 비교할 같은 층 공간 쌍이 없음")
    return _res("pass" if not bad else "fail", pairs, len(bad), bad, note)


def check_single_building(f, **_):
    """KR-CODE-15: 단일 건물은 단일 파일(조달청 5.2.2(7)나). 통합 파일은 건물이 여럿일 수 있어 위반이 아닌 경고."""
    n = len(f.by_type("IfcBuilding"))
    if n == 1:
        return _res("pass", 1, 0, note="IfcBuilding 1개")
    return _res("warn", 1, 1, [{"reason": f"IfcBuilding {n}개(통합 파일이면 정상)"}], note=f"IfcBuilding {n}개")


# ---------------------------------------------------------------- 좌표

def check_true_north(f, **_):
    """KR-CODE-11: 진북 방향각을 갖도록 관리(조달청 5.2.7(2)다, 시행지침 설계자편 2.3.2(1)(나)).
    해석: Model 컨텍스트의 TrueNorth 가 정의되어 있거나 IfcMapConversion 이 있으면 통과."""
    ctxs = [c for c in f.by_type("IfcGeometricRepresentationContext") if not c.is_a("IfcGeometricRepresentationSubContext")]
    has_tn = any(getattr(c, "TrueNorth", None) is not None for c in ctxs)
    has_map = bool(f.by_type("IfcMapConversion")) if f.schema != "IFC2X3" else False
    ok = has_tn or has_map
    return _res("pass" if ok else "fail", 1, 0 if ok else 1,
                [] if ok else [{"reason": "TrueNorth 미정의, IfcMapConversion 없음"}])


_KR_CRS = re.compile(r"(EPSG:)?(5173|5174|5175|5176|5178|5179|518[0-8]|32652)\b|Korea 2000|UTM.?52", re.I)


def check_crs(f, **_):
    """KR-CODE-12: 좌표계 선언 권고(시행지침 설계자편 2.3.2(1)(나): GRS80 TM / 기본지침: 통상 UTM52 계열).
    해석: IFC4 이상은 IfcMapConversion + IfcProjectedCRS 가 있고 한국 좌표계 EPSG 로 보이는지. 설계자편은 발주자가 정하면
    상대 기준 좌표계도 허용하므로 선언이 없거나 한국 좌표계가 아니어도 위반이 아니라 경고(사람 확인)다.
    IFC2X3 에는 좌표계 개체가 없어 판정하지 않는다(위경도·진북은 KR-IDS-11, KR-CODE-11)."""
    if f.schema == "IFC2X3":
        return _res("na", note="IFC2X3 에는 IfcMapConversion/IfcProjectedCRS 가 없어 좌표계 선언을 판정하지 않음")
    maps = f.by_type("IfcMapConversion")
    if not maps:
        return _res("warn", 1, 1, [{"reason": "IfcMapConversion 없음(상대 기준 좌표계를 쓰는 경우 정상일 수 있어 발주자 확인 필요)"}],
                    note="좌표계 선언 없음")
    crs = maps[0].TargetCRS
    name = getattr(crs, "Name", None) or ""
    if _KR_CRS.search(name):
        return _res("pass", 1, 0, note=f"TargetCRS={name}")
    return _res("warn", 1, 1, [{"reason": f"TargetCRS '{name}' 이 한국 좌표계로 확인되지 않음"}], note=f"TargetCRS={name}")


# ---------------------------------------------------------------- 속성·관계

_WORKCODE_TOKEN = r"[A-Z][A-Z0-9]{0,11}(?::\d+(?:\.\d+)?)?"
_WORKCODE = re.compile(rf"^{_WORKCODE_TOKEN}(?:\+{_WORKCODE_TOKEN})*$")


def check_workcode_syntax(f, **_):
    """KR-CODE-13: 조달청표준공사코드 값 구문 'D', 'D+E', 'D:200+FA11' (조달청 부속서-1 4.3). 코드 목록 대조는 하지 않음."""
    found, bad = 0, []
    for e in f.by_type("IfcElement"):
        for pset, props in uel.get_psets(e).items():
            if "조달청표준공사코드" in props:
                found += 1
                v = props["조달청표준공사코드"]
                if not isinstance(v, str) or not _WORKCODE.match(v.replace(" ", "")):
                    bad.append(_ex(e, f"공사코드 값 '{v}'"))
    if not found:
        return _res("na", note="'조달청표준공사코드' 속성을 가진 객체 없음")
    return _res("pass" if not bad else "fail", found, len(bad), bad)


MAIN_CLASSES = ("IfcWall", "IfcSlab", "IfcColumn", "IfcBeam", "IfcDoor", "IfcWindow", "IfcRoof", "IfcStair",
                "IfcRailing", "IfcCurtainWall", "IfcCovering", "IfcFooting")


def check_workcode_present(f, **_):
    """KR-CODE-19: 주요 부위 객체에 '조달청표준공사코드' 속성이 있음(조달청 부속서-1 4.2(3)). Pset 이름은 묻지 않음.
    (IDS 의 propertySet 정규식은 일치하는 모든 Pset 에 속성이 있어야 해서 이 검사에는 맞지 않아 코드로 둔다)"""
    targets = [e for c in MAIN_CLASSES for e in f.by_type(c)]
    if not targets:
        return _res("na")
    bad = []
    for e in targets:
        if not any("조달청표준공사코드" in props for props in uel.get_psets(e).values()):
            bad.append(_ex(e, "'조달청표준공사코드' 속성 없음"))
    return _res("pass" if not bad else "fail", len(targets), len(bad), bad)


def check_opening_host(f, **_):
    """KR-CODE-14: 창호는 벽에 소속(조달청 5.2.5(2)). 문·창이 개구부(IfcOpeningElement)를 채우고 그 개구부가 IfcWall 을 뚫어야 함."""
    targets = list(f.by_type("IfcDoor")) + list(f.by_type("IfcWindow"))
    if not targets:
        return _res("na")
    bad = []
    for e in targets:
        host = None
        for rel in getattr(e, "FillsVoids", []) or []:
            op = rel.RelatingOpeningElement
            for v in getattr(op, "VoidsElements", []) or []:
                if v.RelatingBuildingElement.is_a("IfcWall") or v.RelatingBuildingElement.is_a("IfcCurtainWall"):
                    host = v.RelatingBuildingElement
        if host is None:
            bad.append(_ex(e, "벽을 뚫은 개구부에 끼워져 있지 않음"))
    return _res("pass" if not bad else "fail", len(targets), len(bad), bad)


def check_duplicates(f, **_):
    """KR-CODE-17: 중복 객체 없음(시행지침 발주자편 부속서2 5번). GlobalId 중복, 또는 같은 클래스·같은 경계상자의 겹친 객체."""
    bad = []
    seen = {}
    total = 0
    for r in f.by_type("IfcRoot"):
        total += 1
        gid = r.GlobalId
        if gid in seen:
            bad.append(_ex(r, f"GlobalId 중복({seen[gid].is_a()})"))
        else:
            seen[gid] = r
    elems = [e for e in f.by_type("IfcElement") if e.Representation is not None and not e.is_a("IfcOpeningElement")]
    note = ""
    if len(elems) > MAX_GEOM_ELEMENTS:
        note = f"형상 중복 판정 생략(형상 있는 객체 {len(elems)}개 > 한도 {MAX_GEOM_ELEMENTS}); GlobalId 중복만 판정"
        elems = []
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    cache = {}
    keys = {}
    for e in elems:
        b = _bbox(cache, f, e, settings)
        if b is None:
            continue
        k = (e.is_a(), tuple(round(x, 3) for x in b))
        if k in keys:
            bad.append(_ex(e, f"{keys[k].Name} 와 같은 위치·크기의 중복"))
        else:
            keys[k] = e
    return _res("pass" if not bad else "fail", total + len(elems), len(bad), bad, note)


# ---------------------------------------------------------------- 납품 폴더(파일 시스템)

def check_package_layout(root: Path):
    """KR-CODE-18: 납품 폴더 구성(조달청 5.5.3 표). 'IFC' 폴더 아래 IFC 가 파일명 규칙을 따르고,
    '원본'·'도면' 폴더와 'BIM' 로 시작하는 보고서 폴더가 있으며, 건물마다 통합(_B) IFC 가 있어야 함.
    표의 열 구조가 PDF 텍스트에서 평탄화되어 있어 폴더 이름 해석은 사람 확인 필요."""
    root = Path(root)
    bad = []
    names = {p.name for p in root.iterdir() if p.is_dir()} if root.is_dir() else set()
    if "IFC" not in names:
        bad.append({"reason": "IFC 폴더 없음"})
    if "원본" not in names:
        bad.append({"reason": "원본 폴더 없음"})
    if "도면" not in names:
        bad.append({"reason": "도면 폴더 없음"})
    if not any(n.startswith("BIM") for n in names):
        bad.append({"reason": "BIM 보고서 폴더 없음"})
    ifcs = [p for p in (root / "IFC").rglob("*.ifc")] if (root / "IFC").is_dir() else []
    buildings = {}
    for p in ifcs:
        m = _PPS_NAME.match(p.name)
        if not m:
            bad.append({"reason": f"IFC 파일명 규칙 위반: {p.name}"})
            continue
        if m.group(1):
            buildings.setdefault(m.group(1), set()).add(m.group(3).upper())
    for no, kinds in buildings.items():
        if "B" not in kinds:
            bad.append({"reason": f"건물 {no} 통합(_B) IFC 없음"})
    return _res("pass" if not bad else "fail", 1 + len(ifcs), len(bad), bad)


# 파일 단위 코드 검사 레지스트리: id -> (함수, 심각도)
CODE_CHECKS = {
    "KR-CODE-01": check_schema,
    "KR-CODE-02": check_filename_pps,
    "KR-CODE-03": check_filename_charset,
    "KR-CODE-04": check_unit_mm,
    "KR-CODE-05": check_unit_m_or_mm,
    "KR-CODE-06": check_containment,
    "KR-CODE-07": check_basement_names,
    "KR-CODE-08": check_space_id_format,
    "KR-CODE-09": check_space_class_code,
    "KR-CODE-10": check_space_overlap,
    "KR-CODE-11": check_true_north,
    "KR-CODE-12": check_crs,
    "KR-CODE-13": check_workcode_syntax,
    "KR-CODE-14": check_opening_host,
    "KR-CODE-15": check_single_building,
    "KR-CODE-16": check_filesize,
    "KR-CODE-17": check_duplicates,
    "KR-CODE-19": check_workcode_present,
}
