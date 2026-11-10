"""표준 IFC 슬롯 + 한국어 사용자 속성을 함께 인정하는 판정(OR 판정).

배경(검증 결과표, 2026-11-10): 조달청 부속서-8 사용자 가이드(Revit·ArchiCAD)는 실ID·부재명·사업정보·분류 등을
소프트웨어 사용자 정의 속성으로 넣게 하므로, 표준 슬롯 하나만 정답으로 두면 가이드대로 만든 모델이 오탐된다.
여기 함수들은 IDS 의 엄격한 표준 슬롯 판정을 대체한다(run.py 가 같은 id 의 IDS 결과 대신 쓴다).
.ids 파일에는 표준 슬롯만 보는 엄격판이 그대로 남아 있어, 다른 IDS 도구로 돌리면 사용자 속성 모델은 위반으로 나온다.

모든 함수는 f(ifcopenshell.file) -> 결과 dict 이고, 결과의 found_via 에 "어디서 값을 찾았는지" 개수를 적는다.
원문이 IFC 위치를 정하지 않으므로 이 사상은 해석이며 실제 Revit/ArchiCAD 내보내기 파일로는 아직 시험하지 못했다.
"""
from __future__ import annotations

import ifcopenshell.util.element as uel

from .codechecks import MAIN_CLASSES
from .common import _ex, _res, class_refs, find_prop, find_user_prop, ok, ok_number, ok_part_class, space_real_id

# 부위 객체 부재명 사용자 속성 이름 후보(Revit 은 유형 주석, 조달청 가이드는 부재명)
MEMBER_NAME_PROPS = ["부재명", "유형 주석", "Type Comments"]


def _evaluate(entities, finder, fail_reason, note="", extra_note=None):
    """대상마다 finder(e) -> 찾은 위치 문자열 또는 None. 하나도 못 찾으면 위반."""
    if not entities:
        return _res("na", note="적용 대상 객체 없음")
    bad, via = [], {}
    for e in entities:
        loc = finder(e)
        if loc is None:
            bad.append(_ex(e, fail_reason))
        else:
            via[loc] = via.get(loc, 0) + 1
    n = note + (extra_note(len(entities)) if extra_note else "")
    return _res("pass" if not bad else "fail", len(entities), len(bad), bad, n, found_via=via)


def _dedupe(items):
    seen, out = set(), []
    for e in items:
        if e.id() not in seen:
            seen.add(e.id())
            out.append(e)
    return out


# ---------------------------------------------------------------- 공간(REQ-015, REQ-017)

def space_real_id_present(f, **_):
    """KR-IDS-04: 실에는 실ID 가 있어야 함: 이름이 '실ID' 인 속성을 우선, IfcSpace.Name 은 보조로 인정(REQ-015)."""
    def finder(s):
        _, via = space_real_id(s)
        return via
    return _evaluate(list(f.by_type("IfcSpace")), finder, "실ID 속성도 IfcSpace.Name 도 없음",
                     note="정답은 실ID 속성이고 Name 값은 보조로만 인정(Name 이 있다고 실ID 가 있다는 뜻은 아니며 실번호일 수 있음). 형식은 KR-CODE-08 이 확인")


def space_class_present(f, **_):
    """KR-IDS-05: 실에는 공간분류 코드가 있어야 함: 분류 참조 또는 '공간분류코드' 속성(REQ-017)."""
    def finder(s):
        if class_refs(s):
            return "분류 참조(IfcClassificationReference)"
        if find_prop(s, ["공간분류코드"]):
            return "속성 공간분류코드"
        return None
    return _evaluate(list(f.by_type("IfcSpace")), finder, "분류 참조도 '공간분류코드' 속성도 없음")


# ---------------------------------------------------------------- 부위(REQ-022, REQ-025·028)

def proxy_classified(f, **_):
    """KR-IDS-06: 범용객체에는 부위분류 정보가 있어야 함: 분류 참조 또는 '건설정보분류체계-부위분류' 속성(REQ-025·028)."""
    def finder(e):
        if class_refs(e):
            return "분류 참조(IfcClassificationReference)"
        if find_prop(e, ["건설정보분류체계-부위분류"], check=ok_part_class):
            return "속성 건설정보분류체계-부위분류"
        return None
    proxies = list(f.by_type("IfcBuildingElementProxy"))
    return _evaluate(proxies, finder, "분류 참조도 '건설정보분류체계-부위분류' 속성도 없음",
                     extra_note=lambda n: f"범용객체 {n}개(비율 판단은 하지 않고 정보로만 노출)")


def member_name_present(f, **_):
    """KR-IDS-07: 구조·건축 부위에는 부재명이 있어야 함: Pset_*Common.Reference / ObjectType / 유형 객체 Name / 사용자 속성(REQ-022)."""
    def finder(e):
        if find_prop(e, ["Reference"], pset_re=r"^Pset_.*Common$"):
            return "Pset_*Common.Reference"
        if ok(getattr(e, "ObjectType", None)):
            return "ObjectType"
        try:
            t = uel.get_type(e)
        except Exception:
            t = None
        if t is not None and t is not e and ok(getattr(t, "Name", None)):
            return "유형 객체 Name"
        if find_prop(e, MEMBER_NAME_PROPS):
            return "사용자 속성(부재명·유형 주석)"
        return None
    targets = _dedupe([e for c in MAIN_CLASSES for e in f.by_type(c)])
    return _evaluate(targets, finder, "Reference·ObjectType·유형 객체 Name·부재명/유형 주석 속성 모두 없음",
                     note="인스턴스 Name 은 도구가 항상 채워 부재명 판정에 쓰지 않음. 대상은 구조·건축 부위 12종(기계·전기는 자율)")


# ---------------------------------------------------------------- 사업정보(REQ-034~037)

def project_name_present(f, **_):
    """KR-IDS-13: 프로젝트에 사업명이 있어야 함: IfcProject.Name/LongName 또는 '사업명' 속성(REQ-034)."""
    def finder(p):
        if ok(p.Name) or ok(getattr(p, "LongName", None)):
            return "IfcProject.Name/LongName"
        if find_user_prop(f, p, ["사업명"]):
            return "속성 사업명"
        return None
    return _evaluate(list(f.by_type("IfcProject")), finder, "Name·LongName·'사업명' 속성 모두 없음")


def site_address_present(f, **_):
    """KR-IDS-12: 대지에 주소가 있어야 함: IfcSite.SiteAddress(AddressLines 비어 있지 않음) 또는 '주소' 속성(REQ-035)."""
    def finder(s):
        addr = getattr(s, "SiteAddress", None)
        if addr is not None and ok(getattr(addr, "AddressLines", None)):
            return "IfcSite.SiteAddress"
        if find_user_prop(f, s, ["주소"]):
            return "속성 주소"
        return None
    return _evaluate(list(f.by_type("IfcSite")), finder, "SiteAddress(AddressLines)도 '주소' 속성도 없음(빈 주소 객체는 없는 것으로 봄)")


def building_use_present(f, **_):
    """KR-IDS-14: 건물에 용도가 있어야 함: Pset_BuildingCommon.OccupancyType 또는 '용도' 속성(REQ-036)."""
    def finder(b):
        if find_prop(b, ["OccupancyType"], pset_re=r"^Pset_BuildingCommon$"):
            return "Pset_BuildingCommon.OccupancyType"
        if find_user_prop(f, b, ["용도"]):
            return "속성 용도"
        return None
    return _evaluate(list(f.by_type("IfcBuilding")), finder, "OccupancyType 도 '용도' 속성도 없음")


def _storeys_of(b):
    """건물에 집합(IfcRelAggregates)으로 속한 층만(다른 건물의 층은 세지 않음)."""
    out = []
    for rel in getattr(b, "IsDecomposedBy", None) or []:
        out += [o for o in rel.RelatedObjects if o.is_a("IfcBuildingStorey")]
    return out


def _storey_counts(b):
    """(층 수, 지상층 수, 지하층 수). AboveGround 값이 없는 층은 지상/지하 어느 쪽에도 세지 않는다."""
    st = _storeys_of(b)
    up = down = 0
    for s in st:
        hit = find_prop(s, ["AboveGround"], pset_re=r"^Pset_BuildingStoreyCommon$")
        if hit:
            v = hit[2]
            if v is True or (isinstance(v, str) and v.upper() == "TRUE"):
                up += 1
            elif v is False or (isinstance(v, str) and v.upper() == "FALSE"):
                down += 1
    return len(st), up, down


def building_floors_present(f, **_):
    """KR-IDS-15: 건물에 층수 정보가 있어야 함: NumberOfStoreys / '층수' 속성(0 보다 큰 값) / 그 건물에 속한 층 개체 수(REQ-036).
    NumberOfStoreys 는 정수 하나라 '지하4층, 지상10층' 을 못 담으므로 이 속성 하나에 고정하지 않는다."""
    def finder(b):
        if find_prop(b, ["NumberOfStoreys"], pset_re=r"^Pset_BuildingCommon$", check=ok_number):
            return "Pset_BuildingCommon.NumberOfStoreys"
        if find_user_prop(f, b, ["층수"], check=ok_number):
            return "속성 층수"
        if _storeys_of(b):
            return "층 개체 수(그 건물의 IfcBuildingStorey)"
        return None
    bs = list(f.by_type("IfcBuilding"))
    summ = "; ".join(f"{b.Name}: 층 {c[0]}개(지상 {c[1]}, 지하 {c[2]})" for b in bs for c in [_storey_counts(b)])
    return _evaluate(bs, finder, "NumberOfStoreys·'층수' 속성(0 초과)·소속 층 개체 모두 없음", note=summ)


_BASE_QTO = r"^(Qto_.*BaseQuantities|BaseQuantities)$"  # IFC4 Qto_*BaseQuantities, IFC2X3 BaseQuantities


def _area_check(f, entities, std_slots, user_names, fail_reason):
    """건축면적·연면적·대지면적 공통: 표준 슬롯(들) 또는 한국어 사용자 속성. 값 존재만 보고 기하 합과의 일치는 판정하지 않는다."""
    def finder(e):
        for names, rx, label in std_slots:
            if find_prop(e, names, pset_re=rx, check=ok_number):
                return label
        if find_user_prop(f, e, user_names, check=ok_number):
            return "속성 " + user_names[0]
        return None
    return _evaluate(entities, finder, fail_reason,
                     note="값 존재만 확인(법적 산정 면적과 기하 합의 일치는 판정하지 않음). IFC2X3 도 판정")


def building_gross_area_present(f, **_):
    """KR-IDS-16: 건물에 연면적이 있어야 함: Qto GrossFloorArea / Pset_BuildingCommon.GrossPlannedArea / '연면적' 속성(REQ-037)."""
    return _area_check(
        f, list(f.by_type("IfcBuilding")),
        [(["GrossFloorArea"], _BASE_QTO, "Qto_BuildingBaseQuantities.GrossFloorArea"),
         (["GrossPlannedArea"], r"^Pset_BuildingCommon$", "Pset_BuildingCommon.GrossPlannedArea")],
        ["연면적"], "GrossFloorArea·GrossPlannedArea·'연면적' 속성 모두 없음")


def building_footprint_present(f, **_):
    """KR-IDS-17: 건물에 건축면적이 있어야 함: Qto_BuildingBaseQuantities.FootprintArea 또는 '건축면적' 속성(REQ-037)."""
    return _area_check(
        f, list(f.by_type("IfcBuilding")),
        [(["FootprintArea"], _BASE_QTO, "Qto_BuildingBaseQuantities.FootprintArea")],
        ["건축면적"], "FootprintArea·'건축면적' 속성 모두 없음(IFC2X3 에는 표준 대체 슬롯이 없어 사용자 속성이 필요)")


def site_area_present(f, **_):
    """KR-IDS-18: 대지에 대지면적이 있어야 함: Qto_SiteBaseQuantities.GrossArea / Pset_SiteCommon.TotalArea / '대지면적' 속성(REQ-037)."""
    return _area_check(
        f, list(f.by_type("IfcSite")),
        [(["GrossArea"], _BASE_QTO, "Qto_SiteBaseQuantities.GrossArea"),
         (["TotalArea"], r"^Pset_SiteCommon$", "Pset_SiteCommon.TotalArea")],
        ["대지면적"], "GrossArea·TotalArea·'대지면적' 속성 모두 없음")


# id -> 판정 함수. run.py 가 같은 id 의 IDS(엄격 슬롯) 결과 대신 이 결과를 쓴다.
OVERRIDES = {
    "KR-IDS-04": space_real_id_present,
    "KR-IDS-05": space_class_present,
    "KR-IDS-06": proxy_classified,
    "KR-IDS-07": member_name_present,
    "KR-IDS-12": site_address_present,
    "KR-IDS-13": project_name_present,
    "KR-IDS-14": building_use_present,
    "KR-IDS-15": building_floors_present,
    "KR-IDS-16": building_gross_area_present,
    "KR-IDS-17": building_footprint_present,
    "KR-IDS-18": site_area_present,
}
