"""검증 반영(2026-11-10) 회귀 시험: 표준 IFC 슬롯 + 한국어 사용자 속성을 함께 인정하는 판정.

조달청 부속서-8 사용자 가이드는 실ID·부재명·사업정보 등을 소프트웨어 사용자 정의 속성으로 넣게 하므로,
수정마다 세 가지를 시험한다.
  (a) 표준 슬롯만 쓴 모델은 통과
  (b) 한국어 사용자 속성만 쓴 모델도 통과(이전에는 오탐이던 것)
  (c) 둘 다 없는 모델은 위반
Revit/ArchiCAD 가 실제로 내보낸 파일이 아니라 합성 모델이다(해석 근거는 report.md).
"""
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import ifcopenshell  # noqa: E402
import ifcopenshell.api.aggregate  # noqa: E402
import ifcopenshell.api.classification  # noqa: E402
import ifcopenshell.api.geometry  # noqa: E402
import ifcopenshell.api.georeference  # noqa: E402
import ifcopenshell.api.project  # noqa: E402
import ifcopenshell.api.pset  # noqa: E402
import ifcopenshell.api.root  # noqa: E402
import ifcopenshell.api.spatial  # noqa: E402
import ifcopenshell.api.type  # noqa: E402
import ifcopenshell.guid  # noqa: E402
import ifcopenshell.util.element as uel  # noqa: E402
import ifcopenshell.util.placement  # noqa: E402

import fixtures  # noqa: E402
from krrules import codechecks, run  # noqa: E402

MAIN = ("IfcWall", "IfcDoor")


def _run(f, tmp_path, name="00_본관_BA.ifc", **kw):
    p = tmp_path / name
    f.write(str(p))
    res = run.run_file(p, **kw)
    return {c["id"]: c for c in res["checks"]}, res


def _failed(checks):
    return {k for k, v in checks.items() if v["status"] in ("fail", "warn")}


def _user_pset(f, product, name, props):
    ps = ifcopenshell.api.pset.add_pset(f, product=product, name=name)
    ifcopenshell.api.pset.edit_pset(f, pset=ps, properties=props)


@pytest.fixture()
def base():
    return fixtures.build_compliant()


# ---------------------------------------------------------------- REQ-015 실ID (KR-IDS-04, KR-CODE-08)

def test_real_id_standard_slot_name_is_auxiliary(base, tmp_path):
    # (a) 표준 슬롯(IfcSpace.Name)만 쓴 모델: 통과하되 보조 위치로 기록
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-04"]["status"] == "pass" and checks["KR-CODE-08"]["status"] == "pass"
    assert checks["KR-IDS-04"]["found_via"] == {"Name 값(보조, 실번호일 수 있음)": 4}


def test_real_id_user_property_with_room_number_name(base, tmp_path):
    # (b) 가이드대로: Name 은 실번호(302), 실ID 는 사용자 속성. 이전에는 KR-CODE-08 이 오탐
    for i, sp in enumerate(base.by_type("IfcSpace")):
        real = sp.Name
        sp.Name = f"30{i}"
        _user_pset(base, sp, "PPS_Room", {"실ID": real})
    checks, _ = _run(base, tmp_path)
    assert not _failed(checks), _failed(checks)
    assert checks["KR-IDS-04"]["found_via"] == {"속성 실ID": 4}
    assert checks["KR-CODE-08"]["found_via"] == {"속성 실ID": 4}


def test_real_id_property_only_without_name(base, tmp_path):
    for sp in base.by_type("IfcSpace"):
        real = sp.Name
        sp.Name = None
        _user_pset(base, sp, "PPS_Room", {"실ID": real})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-04"]["status"] == "pass" and checks["KR-CODE-08"]["status"] == "pass"


def test_real_id_neither_present(base, tmp_path):
    # (c) 실ID 속성도 Name 도 없음
    sp = base.by_type("IfcSpace")[0]
    sp.Name = None
    checks, _ = _run(base, tmp_path)
    assert {"KR-IDS-04", "KR-CODE-08"} <= _failed(checks)
    assert checks["KR-IDS-04"]["failed"] == 1


def test_real_id_property_preferred_over_name(base, tmp_path):
    # 실ID 속성이 있으면 그 값으로 형식을 판정한다(Name 이 형식에 맞아도 속성이 틀리면 위반)
    sp = base.by_type("IfcSpace")[0]
    _user_pset(base, sp, "PPS_Room", {"실ID": "BAD-1"})
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-CODE-08"}


# ---------------------------------------------------------------- REQ-022 부재명 (KR-IDS-07)

def _clear_references(f):
    for cls in MAIN:
        for e in f.by_type(cls):
            for name, d in uel.get_psets(e, psets_only=True).items():
                if "Reference" in d:
                    ifcopenshell.api.pset.edit_pset(f, pset=f.by_id(d["id"]), properties={"Reference": None})


def test_member_name_standard_reference(base, tmp_path):
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-07"]["status"] == "pass"
    assert checks["KR-IDS-07"]["found_via"] == {"Pset_*Common.Reference": 4}


def test_member_name_instance_name_alone_is_not_enough(base, tmp_path):
    # (c) 인스턴스 Name(W1, D1 …)만 있고 Reference·ObjectType·유형명·사용자 속성이 없으면 위반(이전에는 항상 통과)
    _clear_references(base)
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-IDS-07"}
    assert checks["KR-IDS-07"]["failed"] == 4


def test_member_name_object_type_and_type_object(base, tmp_path):
    _clear_references(base)
    for w in base.by_type("IfcWall"):
        w.ObjectType = "A-1"
    wt = ifcopenshell.api.root.create_entity(base, ifc_class="IfcDoorType", name="D-TYPE")
    ifcopenshell.api.type.assign_type(base, related_objects=list(base.by_type("IfcDoor")), relating_type=wt)
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-07"]["status"] == "pass", checks["KR-IDS-07"]["examples"]
    assert checks["KR-IDS-07"]["found_via"] == {"ObjectType": 2, "유형 객체 Name": 2}


@pytest.mark.parametrize("prop", ["부재명", "유형 주석", "Type Comments"])
def test_member_name_user_property(base, tmp_path, prop):
    # (b) Revit 유형 주석, 조달청 가이드의 부재명 속성
    _clear_references(base)
    for cls in MAIN:
        for e in base.by_type(cls):
            _user_pset(base, e, "PPS_Member", {prop: "1W1"})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-07"]["status"] == "pass"
    assert checks["KR-IDS-07"]["found_via"] == {"사용자 속성(부재명·유형 주석)": 4}


def test_member_name_empty_values_do_not_count(base, tmp_path):
    _clear_references(base)
    for e in base.by_type("IfcWall"):
        _user_pset(base, e, "PPS_Member", {"부재명": "  "})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-07"]["failed"] == 4


def test_member_name_limited_to_structure_architecture(base, tmp_path):
    # 기계·전기 부위(여기서는 배관 부속)는 부재명 검사 대상이 아니다
    pipe = ifcopenshell.api.root.create_entity(base, ifc_class="IfcFlowSegment", name="P1")
    ifcopenshell.api.spatial.assign_container(base, products=[pipe], relating_structure=base.by_type("IfcBuildingStorey")[0])
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-07"]["applicable"] == 4 and checks["KR-IDS-07"]["status"] == "pass"


# ---------------------------------------------------------------- REQ-025·028 범용객체 (KR-IDS-06)

def _add_proxy(f):
    px = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuildingElementProxy", name="PX")
    ifcopenshell.api.spatial.assign_container(f, products=[px], relating_structure=f.by_type("IfcBuildingStorey")[0])
    return px


def test_proxy_classification_reference(base, tmp_path):
    px = _add_proxy(base)
    ifcopenshell.api.classification.add_reference(
        base, products=[px], identification="E01", name="분류", classification=base.by_type("IfcClassification")[0])
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-06"]["status"] == "pass"
    assert checks["KR-IDS-06"]["found_via"] == {"분류 참조(IfcClassificationReference)": 1}
    assert "범용객체 1개" in checks["KR-IDS-06"]["note"]


def test_proxy_user_property_part_classification(base, tmp_path):
    # (b) 가이드: 속성정보 건설정보분류체계-부위분류(Revit 문자 매개변수)
    px = _add_proxy(base)
    _user_pset(base, px, "PPS_Info", {"건설정보분류체계-부위분류": "E0101"})
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == set()
    assert checks["KR-IDS-06"]["found_via"] == {"속성 건설정보분류체계-부위분류": 1}


def test_proxy_without_any_classification(base, tmp_path):
    px = _add_proxy(base)
    _user_pset(base, px, "PPS_Info", {"건설정보분류체계-부위분류": ""})   # 빈 값은 입력한 것이 아님
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-IDS-06"}


# ---------------------------------------------------------------- REQ-018 공간 겹침 (KR-CODE-10)

def _add_inner_set(f):
    """모든 공간마다 같은 실ID·실명의 더 작은 공간(벽내부선 세트)을 하나씩 더 만든다. 원래 것은 벽중심선 세트."""
    body = next(c for c in f.by_type("IfcGeometricRepresentationSubContext"))
    for sp in list(f.by_type("IfcSpace")):
        inner = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSpace", name=sp.Name)
        inner.LongName = sp.LongName
        rep = ifcopenshell.api.geometry.add_profile_representation(
            f, context=body, profile=f.createIfcRectangleProfileDef("AREA", None, None, 4800.0, 3800.0), depth=2.6)
        ifcopenshell.api.geometry.assign_representation(f, product=inner, representation=rep)
        m = ifcopenshell.util.placement.get_local_placement(sp.ObjectPlacement)
        ifcopenshell.api.geometry.edit_object_placement(f, product=inner, matrix=m, is_si=False)
        st = next(r.RelatingObject for r in sp.Decomposes)
        ifcopenshell.api.aggregate.assign_object(f, products=[inner], relating_object=st)


def test_two_space_sets_wall_center_and_inner_are_not_overlap(base, tmp_path):
    # 벽중심선 세트 + 벽내부선 세트를 한 파일·한 층에 납품하는 것은 정상이다(5.2.3(1)나, FAQ)
    _add_inner_set(base)
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-10"]["status"] == "pass", checks["KR-CODE-10"]["examples"]
    assert "두 세트" in checks["KR-CODE-10"]["note"]
    assert checks["KR-CODE-10"]["applicable"] == 4          # 층마다 (중심 세트 1쌍 + 내부 세트 1쌍)


def test_two_space_sets_still_flag_overlap_inside_a_set(base, tmp_path):
    from test_krrules import _stack_spaces
    _add_inner_set(base)
    _stack_spaces(base, same_storey=True)
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-10"]["status"] == "fail"


def test_contained_space_with_different_id_is_still_overlap(base, tmp_path):
    # 실ID·실명이 다른데 포함 관계인 공간은 두 세트가 아니라 진짜 겹침이다
    _add_inner_set(base)
    inner = [s for s in base.by_type("IfcSpace") if s.Name == "01.001.01"][-1]
    inner.Name = "01.001.99"
    inner.LongName = "다른방"
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-10"]["status"] == "fail"


# ---------------------------------------------------------------- REQ-023·024 공사코드 (KR-CODE-19, KR-CODE-13)

def _drop_workcode_psets(f):
    for cls in MAIN:
        for e in f.by_type(cls):
            d = uel.get_psets(e, psets_only=True).get("PPS_Info")
            if d:
                ifcopenshell.api.pset.remove_pset(f, product=e, pset=f.by_id(d["id"]))


def test_workcode_archicad_pset_is_accepted(base, tmp_path):
    # (b) ArchiCAD: 속성세트 이름 '조달청표준공사코드-세부공종', 속성 이름은 구분 내용(가변), 값 구문도 다름
    _drop_workcode_psets(base)
    for cls in MAIN:
        for e in base.by_type(cls):
            _user_pset(base, e, "조달청표준공사코드-세부공종", {"[철물]철물": "철물 앵커 abc", "[성능]열관류율": "0.21"})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-19"]["status"] == "pass"
    assert checks["KR-CODE-19"]["found_via"] == {"Pset 조달청표준공사코드-세부공종(ArchiCAD)": 4}
    # 구문 검사는 속성 이름이 조달청표준공사코드 일 때만: ArchiCAD 값은 위반으로 잡지 않는다
    assert checks["KR-CODE-13"]["status"] == "na" and "ArchiCAD" in checks["KR-CODE-13"]["note"]


def test_workcode_standard_named_property(base, tmp_path):
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-19"]["found_via"] == {"속성 조달청표준공사코드": 4}


def test_workcode_neither_present(base, tmp_path):
    _drop_workcode_psets(base)
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-19"]["status"] == "fail" and checks["KR-CODE-19"]["failed"] == 4


def test_workcode_syntax_still_checked_for_named_property(base, tmp_path):
    wall = base.by_type("IfcWall")[0]
    ps = base.by_id(uel.get_psets(wall, psets_only=True)["PPS_Info"]["id"])
    ifcopenshell.api.pset.edit_pset(base, pset=ps, properties={"조달청표준공사코드": "콘크리트"})
    _user_pset(base, wall, "조달청표준공사코드-세부공종", {"[철물]철물": "임의 값"})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-13"]["status"] == "fail" and checks["KR-CODE-13"]["failed"] == 1


# ---------------------------------------------------------------- REQ-034~037 사업정보 (KR-IDS-12~18)

INFO_IDS = ["KR-IDS-12", "KR-IDS-13", "KR-IDS-14", "KR-IDS-15", "KR-IDS-16", "KR-IDS-17", "KR-IDS-18"]


def _strip_standard_project_info(f):
    f.by_type("IfcProject")[0].Name = None
    f.by_type("IfcSite")[0].SiteAddress = None
    b = f.by_type("IfcBuilding")[0]
    s = f.by_type("IfcSite")[0]
    for obj, names in ((b, ("Pset_BuildingCommon", "Qto_BuildingBaseQuantities")), (s, ("Qto_SiteBaseQuantities",))):
        for n in names:
            d = uel.get_psets(obj).get(n)
            if d:
                ifcopenshell.api.pset.remove_pset(f, product=obj, pset=f.by_id(d["id"]))


def test_project_info_standard_slots_pass(base, tmp_path):
    checks, _ = _run(base, tmp_path)
    assert {c: checks[c]["status"] for c in INFO_IDS} == {c: "pass" for c in INFO_IDS}


def test_project_info_missing_everywhere(base, tmp_path):
    # (c) 표준 슬롯도 사용자 속성도 없음. 층수만은 층 개체 수에서 계산되므로 통과한다(층이 있는 한)
    _strip_standard_project_info(base)
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-IDS-12", "KR-IDS-13", "KR-IDS-14", "KR-IDS-16", "KR-IDS-17", "KR-IDS-18"}
    assert checks["KR-IDS-15"]["found_via"] == {"층 개체 수(그 건물의 IfcBuildingStorey)": 1}


def test_project_info_floors_missing_without_storeys(base, tmp_path):
    _strip_standard_project_info(base)
    for st in list(base.by_type("IfcBuildingStorey")):
        base.remove(st)
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-15"]["status"] == "fail"


def test_project_info_korean_user_properties(base, tmp_path):
    # (b) 부속서-8 가이드: 프로젝트 정보에 사업명·주소·용도·층수·건축면적·연면적·대지면적을 매개변수로 입력
    _strip_standard_project_info(base)
    proj = base.by_type("IfcProject")[0]
    _user_pset(base, proj, "프로젝트 정보", {"사업명": "OO 신축", "주소": "서울시 OO구", "용도": "업무시설", "층수": "지하4층, 지상10층",
                                          "건축면적": 5990.42, "연면적": 80341.36, "대지면적": 9940.9})
    checks, _ = _run(base, tmp_path)
    assert not _failed(checks), _failed(checks)
    assert checks["KR-IDS-15"]["found_via"] == {"속성 층수": 1}
    assert checks["KR-IDS-12"]["found_via"] == {"속성 주소": 1}


def test_project_info_empty_address_object_is_not_an_address(base, tmp_path):
    base.by_type("IfcSite")[0].SiteAddress = base.create_entity("IfcPostalAddress", Town="서울")   # AddressLines 없음
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-12"]["status"] == "fail"


def test_project_name_from_long_name(base, tmp_path):
    p = base.by_type("IfcProject")[0]
    p.Name = None
    p.LongName = "OO 건물 신축공사"
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-13"]["status"] == "pass"


def test_project_info_gross_floor_area_alternative_slots(base, tmp_path):
    _strip_standard_project_info(base)
    b = base.by_type("IfcBuilding")[0]
    _user_pset(base, b, "Pset_BuildingCommon", {"GrossPlannedArea": 200.0})
    _user_pset(base, base.by_type("IfcSite")[0], "Pset_SiteCommon", {"TotalArea": 900.0})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-16"]["found_via"] == {"Pset_BuildingCommon.GrossPlannedArea": 1}
    assert checks["KR-IDS-18"]["found_via"] == {"Pset_SiteCommon.TotalArea": 1}
    assert checks["KR-IDS-17"]["status"] == "fail"      # 건축면적은 대체 표준 슬롯이 없어 사용자 속성이 필요


def _raw_pset(f, obj, name, props):
    """IFC2X3 파일에 속성세트를 직접 만든다(api 는 소유자 이력이 필요해 쓰지 않음)."""
    def val(v):
        if isinstance(v, str):
            return f.create_entity("IfcLabel", v)
        return f.create_entity("IfcAreaMeasure", float(v))
    ps = f.create_entity("IfcPropertySet", GlobalId=ifcopenshell.guid.new(), Name=name,
                         HasProperties=[f.create_entity("IfcPropertySingleValue", Name=k, NominalValue=val(v))
                                        for k, v in props.items()])
    f.create_entity("IfcRelDefinesByProperties", GlobalId=ifcopenshell.guid.new(), RelatedObjects=[obj],
                    RelatingPropertyDefinition=ps)


def _ifc2x3_project(areas=True):
    f = ifcopenshell.api.project.create_file(version="IFC2X3")
    proj = f.create_entity("IfcProject", GlobalId=ifcopenshell.guid.new(), Name="OO 신축")
    site = f.create_entity("IfcSite", GlobalId=ifcopenshell.guid.new(), Name="대지")
    bld = f.create_entity("IfcBuilding", GlobalId=ifcopenshell.guid.new(), Name="본관")
    if areas:
        _raw_pset(f, bld, "Pset_BuildingCommon", {"GrossPlannedArea": 200.0})
        _raw_pset(f, site, "Pset_SiteCommon", {"TotalArea": 900.0})
        _raw_pset(f, bld, "건축개요", {"건축면적": 100.0})
    return f, proj, site, bld


def test_ifc2x3_area_checks_are_no_longer_skipped(tmp_path):
    # 이전에는 IFC2X3 에서 연면적·건축면적·대지면적 검사가 통째로 해당없음이었다
    f, *_ = _ifc2x3_project(areas=True)
    checks, res = _run(f, tmp_path)
    assert res["schema"] == "IFC2X3"
    assert {checks[c]["status"] for c in ("KR-IDS-16", "KR-IDS-17", "KR-IDS-18")} == {"pass"}
    assert not [c for c in checks.values() if c["status"] == "error"]
    f, *_ = _ifc2x3_project(areas=False)
    checks, _ = _run(f, tmp_path)
    assert {checks[c]["status"] for c in ("KR-IDS-16", "KR-IDS-17", "KR-IDS-18")} == {"fail"}


# ---------------------------------------------------------------- REQ-012 지하층 (KR-CODE-07)

def _storey(f, elev_negative):
    return next(s for s in f.by_type("IfcBuildingStorey") if (s.Elevation < 0) == elev_negative)


def test_basement_above_ground_false_is_authoritative(base, tmp_path):
    first = _storey(base, False)            # 고도 0 인 1층이어도 AboveGround=FALSE 면 지하층으로 판정
    first.Name = "Lower"
    _user_pset(base, first, "Pset_BuildingStoreyCommon", {"AboveGround": base.createIfcLogical(False)})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-07"]["status"] == "fail"
    assert checks["KR-CODE-07"]["found_via"]["AboveGround=FALSE"] == 1
    first.Name = "B1"                        # 이름을 지키면 통과
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-07"]["status"] == "pass"


def test_basement_elevation_only_is_warning(base, tmp_path):
    _storey(base, True).Name = "Lower"        # AboveGround 없이 고도<0 : 의심이라 경고급
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-07"]["status"] == "warn"


def test_basement_above_ground_true_overrides_negative_elevation(base, tmp_path):
    neg = _storey(base, True)
    neg.Name = "Lower"
    _user_pset(base, neg, "Pset_BuildingStoreyCommon", {"AboveGround": base.createIfcLogical(True)})   # 원점이 지표가 아닌 경우
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-07"]["status"] == "na"


def test_basement_foundation_storey_excluded(base, tmp_path):
    neg = _storey(base, True)
    neg.Name = "기초"
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-07"]["status"] == "na"
    neg.Name = "Foundation"
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-07"]["status"] == "na"


# ---------------------------------------------------------------- REQ-040·041·051 좌표

def _model_ctx(f):
    return next(c for c in f.by_type("IfcGeometricRepresentationContext") if not c.is_a("IfcGeometricRepresentationSubContext"))


def test_true_north_default_value_is_noted(base, tmp_path):
    ifcopenshell.api.georeference.remove_georeferencing(base)
    _model_ctx(base).TrueNorth = base.createIfcDirection((0.0, 1.0))
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-11"]["status"] == "pass" and "기본값(0,1)" in checks["KR-CODE-11"]["note"]
    _model_ctx(base).TrueNorth = base.createIfcDirection((0.1, 0.99))
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-11"]["status"] == "pass" and "기본값" not in checks["KR-CODE-11"]["note"]
    assert "RefElevation" in checks["KR-CODE-11"]["note"]


def test_ifc2x3_epset_georeference_is_accepted(tmp_path):
    f, proj, *_ = _ifc2x3_project(areas=False)
    checks, _ = _run(f, tmp_path)
    assert checks["KR-CODE-11"]["status"] == "fail" and checks["KR-CODE-12"]["status"] == "na"   # (c) 아무 선언 없음
    _raw_pset(f, proj, "ePSet_MapConversion", {"Eastings": 200000.0, "Northings": 600000.0})
    _raw_pset(f, proj, "ePSet_ProjectedCRS", {"Name": "EPSG:5186"})
    checks, _ = _run(f, tmp_path)
    assert checks["KR-CODE-11"]["status"] == "pass"                    # (b)
    assert checks["KR-CODE-12"]["status"] == "pass" and checks["KR-CODE-12"]["found_via"] == {"ePSet_ProjectedCRS": 1}


def test_ifc2x3_epset_non_korean_crs_is_warning(tmp_path):
    f, proj, *_ = _ifc2x3_project(areas=False)
    _raw_pset(f, proj, "ePSet_ProjectedCRS", {"Name": "EPSG:32760"})
    checks, _ = _run(f, tmp_path)
    assert checks["KR-CODE-12"]["status"] == "warn"


@pytest.mark.parametrize("name,expected", [("UTM84-52S", "pass"), ("UTM 52N", "pass"), ("EPSG:32652", "pass"),
                                           ("EPSG:32752", "warn"), ("UTM-K", "warn")])
def test_crs_utm52_reading(base, tmp_path, name, expected):
    # 기본지침 'UTM84-52S' 의 S 는 위도 띠 표기(북반구 52구역): 남반구 EPSG:32752 와 혼동하지 않는다
    base.by_type("IfcProjectedCRS")[0].Name = name
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-12"]["status"] == expected, name


# ---------------------------------------------------------------- REQ-017 공간분류코드 속성

def _drop_space_classification(f, sp):
    # 같은 코드의 분류 관계는 여러 공간이 공유하므로 이 공간만 떼어 낸다
    for rel in list(sp.HasAssociations):
        if rel.is_a("IfcRelAssociatesClassification"):
            ifcopenshell.api.classification.remove_reference(f, reference=rel.RelatingClassification, products=[sp])


def test_space_class_code_user_property(base, tmp_path):
    sp = base.by_type("IfcSpace")[0]
    _drop_space_classification(base, sp)
    checks, _ = _run(base, tmp_path)
    assert {"KR-IDS-05", "KR-CODE-09"} <= _failed(checks)              # (c)
    _user_pset(base, sp, "PPS_Room", {"공간분류코드": "33202"})
    checks, _ = _run(base, tmp_path)
    assert not _failed(checks)                                         # (b)
    assert checks["KR-IDS-05"]["found_via"]["속성 공간분류코드"] == 1


# ---------------------------------------------------------------- REQ-007 파일 크기, REQ-008

def test_filesize_message_has_both_limits(base, tmp_path, monkeypatch):
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-16"]["status"] == "pass"                    # (a) 작은 파일
    monkeypatch.setattr(codechecks, "SIZE_LIMIT_BYTES", 10)
    checks, _ = _run(base, tmp_path)
    r = checks["KR-CODE-16"]["examples"][0]["reason"]
    assert checks["KR-CODE-16"]["status"] == "warn" and "200MB" in r and "300MB" in r and "원본" in r
    assert "압축 없이" in r                                              # 300MB 이하라 zip 불필요
    monkeypatch.setattr(codechecks, "ZIP_ALLOWED_BYTES", 5)
    checks, _ = _run(base, tmp_path)
    assert "zip" in checks["KR-CODE-16"]["examples"][0]["reason"]


def test_zero_buildings_warning_text(tmp_path):
    f = ifcopenshell.api.project.create_file(version="IFC4")
    r = codechecks.check_single_building(f)
    assert r["status"] == "warn" and "토목" in r["examples"][0]["reason"]
