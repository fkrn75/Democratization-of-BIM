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

from .common import (MAX_EXAMPLES, _ex, _res, class_codes, find_pset_dict, find_prop,  # noqa: F401
                    ok as _ok, space_real_id)

SIZE_LIMIT_BYTES = 200 * 1000 * 1000  # 시행지침 발주자편 부속서2: 원본 200MB(원문은 MB) 초과 시 분할 검토(IFC 에 준용)
ZIP_ALLOWED_BYTES = 300 * 1000 * 1000  # 조달청 5.5.3(3)라: IFC 가 300MB 를 넘으면 zip 압축 허용
MAX_SPACES = 3000          # KR-CODE-10: 공간 형상을 계산할 최대 개수(넘으면 판정 생략, 경고)
MAX_GEOM_ELEMENTS = 20000  # KR-CODE-17: 형상 중복을 계산할 최대 객체 수(넘으면 GlobalId 중복만 판정)


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
    # 발주자편의 200MB 는 '원본' 데이터 기준이고, 조달청 5.5.3(3)라 는 IFC 300MB 초과 시 zip 을 허용한다.
    # 원본 크기는 도구가 모르므로 IFC 크기로 대신 경고한다.
    if size > ZIP_ALLOWED_BYTES:
        extra = "IFC 300MB 초과이므로 zip 압축 제출이 허용됨(조달청 5.5.3(3)라)"
    else:
        extra = "IFC 300MB 이하라 압축 없이 제출 가능(조달청 5.5.3(3)라)"
    why = f"{size} 바이트 > 200MB(발주자편 원본 기준 분할 검토선, 원본 크기는 도구가 모름); {extra}"
    return _res("pass" if ok else "warn", 1, 0 if ok else 1, [] if ok else [{"reason": why}],
                note=f"{size} 바이트; 원본 200MB 분할 검토 / IFC 300MB 초과 시 zip 허용")


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


_FOUNDATION_PREFIX = ("기초", "foundation")  # 최하층 바닥구조체·기초는 독립된 층(5.2.2(6)다)이라 지하층 이름 규칙 대상에서 제외


def _above_ground(s):
    """Pset_BuildingStoreyCommon.AboveGround -> True/False/None(없음·UNKNOWN)."""
    hit = find_prop(s, ["AboveGround"], pset_re=r"^Pset_BuildingStoreyCommon$")
    if not hit:
        return None
    v = hit[2]
    if v is True or (isinstance(v, str) and v.strip().upper() == "TRUE"):
        return True
    if v is False or (isinstance(v, str) and v.strip().upper() == "FALSE"):
        return False
    return None


def check_basement_names(f, **_):
    """KR-CODE-07: 지하층 이름은 'B' 또는 '지하' 로 시작(조달청 5.2.2(6)라).
    해석: 지하층 판정은 Pset_BuildingStoreyCommon.AboveGround=FALSE 를 우선한다(위반이면 fail).
    AboveGround 가 없는 층에 한해 Elevation<0 은 모델 원점이 지표가 아닐 수 있어 '의심' 으로만 보고 경고(warn)한다.
    이름이 기초/Foundation 으로 시작하는 층은 독립된 기초층이라 제외한다."""
    sure, suspect = [], []
    for s in f.by_type("IfcBuildingStorey"):
        if (s.Name or "").strip().lower().startswith(_FOUNDATION_PREFIX):
            continue
        ag = _above_ground(s)
        if ag is False:
            sure.append(s)
        elif ag is None and s.Elevation is not None and s.Elevation < 0:
            suspect.append(s)
    if not sure and not suspect:
        return _res("na", note="지하층 없음(AboveGround=FALSE 인 층도, AboveGround 없이 고도<0 인 층도 없음)")

    def bad_name(s):
        return not (s.Name or "").startswith(("B", "지하"))
    bad_sure = [_ex(s, f"지하층(AboveGround=FALSE) 이름 '{s.Name}'") for s in sure if bad_name(s)]
    bad_sus = [_ex(s, f"고도<0 이라 지하층으로 의심(AboveGround 없음) 이름 '{s.Name}'") for s in suspect if bad_name(s)]
    via = {}
    if sure:
        via["AboveGround=FALSE"] = len(sure)
    if suspect:
        via["Elevation<0(AboveGround 없음, 의심)"] = len(suspect)
    status = "fail" if bad_sure else ("warn" if bad_sus else "pass")
    return _res(status, len(sure) + len(suspect), len(bad_sure) + len(bad_sus), bad_sure + bad_sus,
                note="AboveGround 우선, Elevation<0 은 경고급 보조", found_via=via)


_SPACE_ID = re.compile(r"^(0[1-9]|[1-8][0-9]|98)\.\d{3}\.\d{2}$")


def check_space_id_format(f, **_):
    """KR-CODE-08: 실ID 형식 AA.BBB.CC (AA=시설-구역 01~89·공용 98, 조달청 부속서-1 3.1(3)). 변형 허용이라 권고 수준.
    검사 대상 값: 이름이 '실ID' 인 속성을 우선하고 없으면 IfcSpace.Name(보조). 실번호와 실ID 는 별개라 Name 이 실번호여도
    실ID 속성이 형식을 지키면 통과한다."""
    spaces = list(f.by_type("IfcSpace"))
    if not spaces:
        return _res("na")
    bad, via = [], {}
    for s in spaces:
        val, where = space_real_id(s)
        if where:
            via[where] = via.get(where, 0) + 1
        if val is None or not _SPACE_ID.match(val):
            bad.append(_ex(s, f"실ID '{val}'({where or '위치 없음'}) 형식 불일치"))
    return _res("pass" if not bad else "fail", len(spaces), len(bad), bad, found_via=via)


_SPACE_CLASS = re.compile(r"^3[34]\d{3}$")


def check_space_class_code(f, **_):
    """KR-CODE-09: 공간분류코드는 부속서-1 3.2 목록의 5자리 세분류(33xxx·34xxx) 형식. 목록 전체 대조는 하지 않음."""
    spaces = list(f.by_type("IfcSpace"))
    if not spaces:
        return _res("na")
    bad = []
    for s in spaces:
        codes = class_codes(s)
        hit = find_prop(s, ["공간분류코드"])  # 가이드가 사용자 속성으로 입력하게 할 수 있어 함께 인정
        if hit:
            codes.append(hit[2])
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


def _space_key(sp):
    """같은 실을 가리키는 공간인지 보는 열쇠: (실ID 값, 실명). 둘 다 없으면 None."""
    rid, _ = space_real_id(sp)
    ln = getattr(sp, "LongName", None)
    ln = ln if _ok(ln) else None
    return None if (rid is None and ln is None) else (rid, ln)


def _contains(a, b, tol):
    """경계상자 a 가 b 를 포함하는가(tol 허용)."""
    return all(a[i] <= b[i] + tol for i in range(3)) and all(a[i + 3] >= b[i + 3] - tol for i in range(3))


def _sweep(items, tol, found):
    """x 최솟값 순 스윕으로 AABB 가 tol 보다 깊게 겹치는 쌍을 found{(id,id): 사례}에 모은다."""
    items = sorted(items, key=lambda it: it[1][0])
    n = len(items)
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
                found[tuple(sorted((a.id(), b.id())))] = {
                    "class": "IfcSpace", "name": f"{a.Name} / {b.Name}", "global_id": a.GlobalId,
                    "reason": f"AABB 겹침 {dx:.3f}x{dy:.3f}x{dz:.3f} m"}


def _volume(b):
    return (b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2])


SAME_BOX_TOL = 0.02  # m. 서로 이 안에서 포함하면 '동일한 상자'(두 세트가 아니라 중복)


def _nested_chain(grp, tol):
    """같은 열쇠의 공간 목록이 '큰 것 > 작은 것' 로 엄격히 포함되는 사슬이면 큰 순서대로 돌려준다. 아니면 None.
    서로 거의 같은 상자(중복)가 끼어 있거나 포함 관계가 아니면 사슬이 아니다."""
    order = sorted(grp, key=lambda it: -_volume(it[1]))
    for a, b in zip(order, order[1:]):
        if not _contains(a[1], b[1], tol):
            return None
        if _contains(b[1], a[1], SAME_BOX_TOL):  # 거의 동일: 두 세트가 아니라 중복
            return None
    return order


def _split_two_sets(items, tol):
    """한 층의 공간 목록에서 '벽중심선 세트 / 벽내부선 세트' 두 벌 납품(5.2.3(1)나, 정의 1장)을 가려낸다.
    같은 (실ID, 실명) 열쇠를 가진 공간이 둘 이상이고 큰 것이 작은 것을 엄격히 포함하는 사슬이면(상자가 거의 같으면 중복이라 제외)
    크기 순위별로 세트(0=가장 큼 …)에 나눈다. 사슬이 아닌 공간과 열쇠가 하나뿐인 공간은 모든 세트에 넣어 비교한다.
    사슬이 하나도 없으면 None(세트 분리 안 함). 두 세트를 IFC 에서 구별하는 표식은 원문에 규정이 없어 이 포함 관계로 근사한다.
    돌려주는 값: (세트 목록, 모든 세트에 들어간 공간 수)"""
    by_key = {}
    for it in items:
        k = _space_key(it[0])
        if k is not None:
            by_key.setdefault(k, []).append(it)
    ranked, paired = [], set()
    for grp in by_key.values():
        if len(grp) < 2:
            continue
        chain = _nested_chain(grp, tol)
        if chain is None:
            continue
        for rank, it in enumerate(chain):
            while len(ranked) <= rank:
                ranked.append([])
            ranked[rank].append(it)
            paired.add(it[0].id())
    if not paired:
        return None
    rest = [it for it in items if it[0].id() not in paired]
    return [r + rest for r in ranked], len(rest)


def check_space_overlap(f, tol=0.01, spaces=None, **_):
    """KR-CODE-10: 같은 층의 공간객체끼리 겹치지 않음(조달청 5.2.2(3)바, 5.3(4)가).
    근사: 같은 층 공간의 축 정렬 경계상자(AABB)가 tol(m)보다 깊게 겹치면 위반. 비직사각형 방은 오탐 가능.
    조달청은 측면 경계를 벽중심선·벽내부선 두 가지로 모두 납품받으므로(5.2.3(1)나, FAQ), 같은 층에 같은 (실ID, 실명) 공간이
    둘씩 있고 한쪽이 다른 쪽을 포함하면 두 세트로 나눠 세트 안에서만 비교한다. 두 세트를 가르는 IFC 표식은 원문에 없어
    경계상자 포함 관계로 근사한 것이며 발주처 확인이 필요하다. 쌍을 못 지은 겹침은 위반으로 둔다.
    층이 다른 공간은 비교하지 않고(소속 층이 없는 공간끼리만 한 묶음), 공간이 MAX_SPACES 를 넘으면 판정을 생략한다.
    spaces 를 주면 그 공간들만 비교한다(세움터 프로파일이 면적 객체를 뺀 실 공간만 넘길 때 쓴다). 기본은 모든 IfcSpace."""
    spaces = list(f.by_type("IfcSpace")) if spaces is None else list(spaces)
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
    found = {}
    pairs = 0
    two_set_storeys = 0
    for key in sorted(groups, key=lambda k: -1 if k is None else k):
        items = groups[key]
        n = len(items)
        split = _split_two_sets(items, tol)
        if split is None:
            pairs += n * (n - 1) // 2
            _sweep(items, tol, found)
            continue
        two_set_storeys += 1
        sets, shared = split
        sizes = [len(x) for x in sets]
        pairs += sum(n * (n - 1) // 2 for n in sizes) - (len(sets) - 1) * (shared * (shared - 1) // 2)
        for st_items in sets:
            _sweep(st_items, tol, found)
    bad = sorted(found.values(), key=lambda e: (e["name"] or "", e["global_id"] or ""))
    note = "AABB 근사, 같은 층끼리만" + (f"; 형상 없는 공간 {missing}개 제외" if missing else "")
    if two_set_storeys:
        note += f"; 벽중심/벽내부 두 세트로 보고 세트 안에서만 비교한 층 {two_set_storeys}개(세트 구별 표식은 포함 관계 근사)"
    if pairs == 0:
        return _res("na", note=note + "; 비교할 같은 층 공간 쌍이 없음")
    return _res("pass" if not bad else "fail", pairs, len(bad), bad, note)


def check_single_building(f, **_):
    """KR-CODE-15: 단일 건물은 단일 파일(조달청 5.2.2(7)나). 통합 파일은 건물이 여럿일 수 있어 위반이 아닌 경고."""
    n = len(f.by_type("IfcBuilding"))
    if n == 1:
        return _res("pass", 1, 0, note="IfcBuilding 1개")
    why = f"IfcBuilding {n}개(토목 전용 파일 등이면 정상)" if n == 0 else f"IfcBuilding {n}개(통합 파일이면 정상)"
    return _res("warn", 1, 1, [{"reason": why}], note=f"IfcBuilding {n}개")


# ---------------------------------------------------------------- 좌표

def _is_default_north(c):
    """TrueNorth 가 도구 기본값(0,1)인가: 의도적으로 입력한 진북인지 알 수 없다."""
    tn = getattr(c, "TrueNorth", None)
    r = [round(float(x), 6) for x in (getattr(tn, "DirectionRatios", None) or [])]
    return len(r) >= 2 and r[0] == 0.0 and r[1] == 1.0 and all(x == 0.0 for x in r[2:])


def check_true_north(f, **_):
    """KR-CODE-11: 진북 방향각을 갖도록 관리(조달청 5.2.7(2)다, 시행지침 설계자편 2.3.2(1)(나)).
    해석: Model 컨텍스트의 TrueNorth 가 정의되어 있거나, IfcMapConversion(IFC2X3 은 ePSet_MapConversion 속성세트)이 있으면 통과.
    TrueNorth 가 도구 기본값 (0,1) 이면 통과는 시키되 '의도 불명' 으로 노트에 남긴다(합격선은 발주처가 정함).
    지반레벨(IfcSite.RefElevation) 유무도 노트로 노출한다."""
    ctxs = [c for c in f.by_type("IfcGeometricRepresentationContext") if not c.is_a("IfcGeometricRepresentationSubContext")]
    tn_ctxs = [c for c in ctxs if getattr(c, "TrueNorth", None) is not None]
    has_tn = bool(tn_ctxs)
    if f.schema == "IFC2X3":
        has_map = find_pset_dict(f, "ePSet_MapConversion") is not None
        map_label = "ePSet_MapConversion"
    else:
        has_map = bool(f.by_type("IfcMapConversion"))
        map_label = "IfcMapConversion"
    ok = has_tn or has_map
    via = {}
    if has_tn:
        via["TrueNorth"] = len(tn_ctxs)
    if has_map:
        via[map_label] = 1
    notes = []
    if has_tn and not has_map and all(_is_default_north(c) for c in tn_ctxs):
        notes.append("TrueNorth 가 기본값(0,1)이라 의도적으로 입력한 진북인지 불명(발주처 확인)")
    sites = list(f.by_type("IfcSite"))
    if sites:
        n_el = sum(1 for st in sites if getattr(st, "RefElevation", None) is not None)
        notes.append(f"IfcSite.RefElevation(지반레벨) {'있음' if n_el else '없음'}")
    return _res("pass" if ok else "fail", 1, 0 if ok else 1,
                [] if ok else [{"reason": f"TrueNorth 미정의, {map_label} 없음"}], note="; ".join(notes), found_via=via)


# 한국 좌표계: EPSG 5173~5188(GRS80/Bessel TM 계열), 32652(UTM 52N). 기본지침의 'UTM84-52S' 의 S 는 UTM 위도 띠(북위 32~40도)
# 표기이고 남반구가 아니므로 북반구 52구역(EPSG:32652)으로 본다. 남반구 32752 는 한국 좌표계가 아니다.
_KR_CRS = re.compile(r"(?<![0-9A-Za-z])(EPSG:)?(5173|5174|5175|5176|5178|5179|518[0-8]|32652)\b|Korea 2000|UTM(?:84)?[-_ .]?52", re.I)


def check_crs(f, **_):
    """KR-CODE-12: 좌표계 선언 권고(시행지침 설계자편 2.3.2(1)(나): GRS80 TM / 기본지침: 통상 UTM52 계열).
    해석: IFC4 이상은 IfcMapConversion + IfcProjectedCRS, IFC2X3 은 ePSet_MapConversion·ePSet_ProjectedCRS 속성세트가 있고
    한국 좌표계 EPSG 로 보이는지. 설계자편은 발주자가 정하면 상대 기준 좌표계도 허용하므로 선언이 없거나 한국 좌표계가
    아니어도 위반이 아니라 경고(사람 확인)다. 기본지침 'UTM84-52S' 의 52S 는 북반구 52구역(EPSG:32652)으로 해석한다.
    IFC2X3 에 ePSet_* 도 없으면 판정하지 않는다(위경도·진북은 KR-IDS-11, KR-CODE-11)."""
    if f.schema == "IFC2X3":
        crs_ps = find_pset_dict(f, "ePSet_ProjectedCRS")
        map_ps = find_pset_dict(f, "ePSet_MapConversion")
        if crs_ps is None and map_ps is None:
            return _res("na", note="IFC2X3 에는 IfcMapConversion/IfcProjectedCRS 가 없어 좌표계 선언을 판정하지 않음"
                                   "(ePSet_MapConversion/ePSet_ProjectedCRS 속성세트도 없음)")
        name = str((crs_ps or {}).get("Name") or "")
        via = {"ePSet_ProjectedCRS": 1} if crs_ps is not None else {"ePSet_MapConversion(CRS 이름 없음)": 1}
        if _KR_CRS.search(name):
            return _res("pass", 1, 0, note=f"ePSet_ProjectedCRS.Name={name}", found_via=via)
        return _res("warn", 1, 1, [{"reason": f"ePSet_ProjectedCRS.Name '{name}' 이 한국 좌표계로 확인되지 않음"}],
                    note=f"ePSet_ProjectedCRS.Name={name}", found_via=via)
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


_WORKCODE_PSET_PREFIX = "조달청표준공사코드"


def _is_archicad_workcode_pset(pset_name):
    """ArchiCAD 경로: IFC 속성세트 이름이 '조달청표준공사코드-세부공종' 이고 속성 이름은 구분 내용(가변)이다."""
    return str(pset_name).startswith(_WORKCODE_PSET_PREFIX)


def check_workcode_syntax(f, **_):
    """KR-CODE-13: 조달청표준공사코드 값 구문 'D', 'D+E', 'D:200+FA11' (조달청 부속서-1 4.3). 코드 목록 대조는 하지 않음.
    구문은 속성 이름이 정확히 '조달청표준공사코드'(Revit 매개변수)일 때만 본다. ArchiCAD 세부공종 Pset 의 값은 구문이 달라 검사하지 않는다."""
    found, bad, skipped = 0, [], 0
    for e in f.by_type("IfcElement"):
        for pset, props in uel.get_psets(e).items():
            if "조달청표준공사코드" in props:
                found += 1
                v = props["조달청표준공사코드"]
                if not isinstance(v, str) or not _WORKCODE.match(v.replace(" ", "")):
                    bad.append(_ex(e, f"공사코드 값 '{v}'"))
            elif _is_archicad_workcode_pset(pset):
                skipped += 1  # ArchiCAD 세부공종 속성: 이름이 구분 내용(가변), 값 구문이 달라 존재만 인정
    extra = f"ArchiCAD '조달청표준공사코드-세부공종' Pset {skipped}개는 구문 검사에서 제외(KR-CODE-19 가 존재만 확인)" if skipped else ""
    if not found:
        return _res("na", note=("'조달청표준공사코드' 속성을 가진 객체 없음" + ("; " + extra if extra else "")))
    return _res("pass" if not bad else "fail", found, len(bad), bad, note=extra)


MAIN_CLASSES = ("IfcWall", "IfcSlab", "IfcColumn", "IfcBeam", "IfcDoor", "IfcWindow", "IfcRoof", "IfcStair",
                "IfcRailing", "IfcCurtainWall", "IfcCovering", "IfcFooting")


def check_workcode_present(f, **_):
    """KR-CODE-19: 주요 부위 객체에 조달청표준공사코드가 있음(조달청 부속서-1 4.2(3)). 이름이 '조달청표준공사코드' 인 속성(Revit,
    Pset 이름 불문) 또는 속성세트 이름이 '조달청표준공사코드' 로 시작하는 Pset(ArchiCAD '조달청표준공사코드-세부공종')을 인정한다.
    (IDS 의 propertySet 정규식은 일치하는 모든 Pset 에 속성이 있어야 해서 이 검사에는 맞지 않아 코드로 둔다)"""
    targets = [e for c in MAIN_CLASSES for e in f.by_type(c)]
    if not targets:
        return _res("na")
    bad, via = [], {}
    for e in targets:
        where = None
        for pname, props in uel.get_psets(e).items():
            if "조달청표준공사코드" in props:
                where = "속성 조달청표준공사코드"
                break
            if _is_archicad_workcode_pset(pname) and any(k != "id" and _ok(v) for k, v in props.items()):
                where = "Pset 조달청표준공사코드-세부공종(ArchiCAD)"
        if where:
            via[where] = via.get(where, 0) + 1
        else:
            bad.append(_ex(e, "'조달청표준공사코드' 속성도 '조달청표준공사코드-세부공종' Pset 도 없음"))
    return _res("pass" if not bad else "fail", len(targets), len(bad), bad, found_via=via)


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
    note = (note + "; " if note else "") + "숨김 객체·불필요한 저장 뷰는 IFC 에 남지 않아 판정하지 않음"
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
