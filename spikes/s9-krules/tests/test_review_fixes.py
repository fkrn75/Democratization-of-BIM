"""검수 반영(2026-11-10) 회귀 시험: 자리표시 값, 속성 인정 범위, 공간 두 세트 반례, 빈 속성세트, 경계값 등.

test_alternatives.py 의 도우미를 그대로 쓴다.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import ifcopenshell  # noqa: E402
import ifcopenshell.api.aggregate  # noqa: E402
import ifcopenshell.api.classification  # noqa: E402
import ifcopenshell.api.geometry  # noqa: E402
import ifcopenshell.api.pset  # noqa: E402
import ifcopenshell.api.root  # noqa: E402
import ifcopenshell.util.element as uel  # noqa: E402

from krrules import codechecks, run  # noqa: E402
from test_alternatives import (MAIN, _add_inner_set, _add_proxy, _clear_references, _drop_workcode_psets,  # noqa: E402,F401
                               _failed, _ifc2x3_project, _raw_pset, _run, _strip_standard_project_info, _user_pset, base)

# ---------------------------------------------------------------- 1. 자리표시 값

PLACEHOLDERS = ["", "  ", "UNKNOWN", "0", 0, 0.0, "-", "없음", "N/A", "n/a", "​", " ", "　 ​", "해당없음"]


@pytest.mark.parametrize("val", PLACEHOLDERS)
def test_placeholder_user_properties_are_not_values(base, tmp_path, val):
    _strip_standard_project_info(base)
    proj = base.by_type("IfcProject")[0]
    _user_pset(base, proj, "프로젝트 정보", {"사업명": val, "주소": val, "용도": val, "층수": val,
                                          "건축면적": val, "연면적": val, "대지면적": val})
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-IDS-12", "KR-IDS-13", "KR-IDS-14", "KR-IDS-16", "KR-IDS-17", "KR-IDS-18"}
    assert "속성 층수" not in checks["KR-IDS-15"]["found_via"]   # 층수는 층 개체 수로만 통과


def test_zero_numbers_in_standard_slots_rejected(base, tmp_path):
    b = base.by_type("IfcBuilding")[0]
    ps = base.by_id(uel.get_psets(b, psets_only=True)["Pset_BuildingCommon"]["id"])
    ifcopenshell.api.pset.edit_pset(base, pset=ps, properties={"NumberOfStoreys": 0})
    q = base.by_id(uel.get_psets(b, qtos_only=True)["Qto_BuildingBaseQuantities"]["id"])
    ifcopenshell.api.pset.edit_qto(base, qto=q, properties={"GrossFloorArea": 0.0})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-16"]["status"] == "fail"
    assert "Pset_BuildingCommon.NumberOfStoreys" not in checks["KR-IDS-15"]["found_via"]


def test_numeric_text_with_unit_is_accepted(base, tmp_path):
    _strip_standard_project_info(base)
    _user_pset(base, base.by_type("IfcProject")[0], "프로젝트 정보", {
        "사업명": "x", "주소": "y", "용도": "z", "층수": "지하4층, 지상10층",
        "건축면적": "5,990.42㎡", "연면적": "80,341.36 ㎡", "대지면적": "9940.9"})
    checks, _ = _run(base, tmp_path)
    assert not _failed(checks)


@pytest.mark.parametrize("val", ["-", "없음", "​", "0"])
def test_part_class_placeholder_rejected(base, tmp_path, val):
    px = _add_proxy(base)
    _user_pset(base, px, "PPS_Info", {"건설정보분류체계-부위분류": val})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-06"]["status"] == "fail"


def test_classification_reference_without_code_is_not_a_classification(base, tmp_path):
    px = _add_proxy(base)
    ifcopenshell.api.classification.add_reference(
        base, products=[px], identification="", name="아무거나", classification=base.by_type("IfcClassification")[0])
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-06"]["status"] == "fail"


def test_placeholder_real_id_name_is_not_a_value(base, tmp_path):
    base.by_type("IfcSpace")[0].Name = "-"
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-04"]["failed"] == 1


def test_placeholder_member_name_rejected(base, tmp_path):
    _clear_references(base)
    for e in base.by_type("IfcWall"):
        e.ObjectType = "N/A"
        _user_pset(base, e, "PPS_Member", {"부재명": "0"})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-07"]["failed"] == 4


def test_real_id_note_does_not_read_as_name_existence(base, tmp_path):
    checks, _ = _run(base, tmp_path)
    assert "실번호" in list(checks["KR-IDS-04"]["found_via"])[0] and "보조" in checks["KR-IDS-04"]["note"]


# ---------------------------------------------------------------- 2. 다른 객체 속성 인정 범위

def test_second_building_does_not_borrow_first_buildings_properties(base, tmp_path):
    _strip_standard_project_info(base)
    b1 = base.by_type("IfcBuilding")[0]
    b2 = ifcopenshell.api.root.create_entity(base, ifc_class="IfcBuilding", name="별관")
    ifcopenshell.api.aggregate.assign_object(base, products=[b2], relating_object=base.by_type("IfcSite")[0])
    _user_pset(base, b1, "건축개요", {"용도": "업무시설", "층수": "10", "연면적": 100.0, "건축면적": 50.0})
    checks, _ = _run(base, tmp_path)
    for cid in ("KR-IDS-14", "KR-IDS-15", "KR-IDS-16", "KR-IDS-17"):
        assert checks[cid]["applicable"] == 2 and checks[cid]["failed"] == 1, cid   # 별관만 위반
        assert checks[cid]["examples"][0]["name"] == "별관", cid


def test_second_site_does_not_borrow_first_sites_properties(base, tmp_path):
    _strip_standard_project_info(base)
    s1 = base.by_type("IfcSite")[0]
    ifcopenshell.api.root.create_entity(base, ifc_class="IfcSite", name="대지2")
    _user_pset(base, s1, "대지현황", {"주소": "서울", "대지면적": 900.0})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-12"]["failed"] == 1 and checks["KR-IDS-18"]["failed"] == 1
    assert checks["KR-IDS-12"]["examples"][0]["name"] == "대지2"
    # 프로젝트에 입력한 정보는 모든 대지가 공유한다
    _user_pset(base, base.by_type("IfcProject")[0], "프로젝트 정보", {"주소": "서울", "대지면적": 900.0})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-12"]["status"] == "pass" and checks["KR-IDS-18"]["status"] == "pass"


def test_floors_count_only_the_buildings_own_storeys(base, tmp_path):
    _strip_standard_project_info(base)
    b2 = ifcopenshell.api.root.create_entity(base, ifc_class="IfcBuilding", name="별관")
    ifcopenshell.api.aggregate.assign_object(base, products=[b2], relating_object=base.by_type("IfcSite")[0])
    checks, _ = _run(base, tmp_path)
    assert checks["KR-IDS-15"]["failed"] == 1 and checks["KR-IDS-15"]["examples"][0]["name"] == "별관"


# ---------------------------------------------------------------- 3. REQ-018 두 세트 반례

def _add_space(f, storey, name, long_name, w, d, x, y, h=2.8):
    body = next(c for c in f.by_type("IfcGeometricRepresentationSubContext"))
    sp = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSpace", name=name)
    sp.LongName = long_name
    rep = ifcopenshell.api.geometry.add_profile_representation(
        f, context=body, profile=f.createIfcRectangleProfileDef("AREA", None, None, w, d), depth=h)
    ifcopenshell.api.geometry.assign_representation(f, product=sp, representation=rep)
    m = np.eye(4)
    m[:3, 3] = (x, y, storey.Elevation)
    ifcopenshell.api.geometry.edit_object_placement(f, product=sp, matrix=m, is_si=False)
    ifcopenshell.api.aggregate.assign_object(f, products=[sp], relating_object=storey)
    return sp


def _first_storey(f):
    return next(s for s in f.by_type("IfcBuildingStorey") if s.Elevation == 0)


def _dup_pair(f):
    st = _first_storey(f)
    for _ in range(2):   # 같은 키·같은 상자를 두 번: 중복
        _add_space(f, st, "01.002.01", "창고", 3000.0, 3000.0, 20000.0, 2000.0)


def test_identical_same_key_spaces_are_a_duplicate_not_two_sets(base, tmp_path):
    _dup_pair(base)
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-10"]["status"] == "fail"


def test_identical_duplicate_is_flagged_even_when_a_real_two_set_exists(base, tmp_path):
    _add_inner_set(base)                                 # 정상 두 세트
    _dup_pair(base)
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-10"]["status"] == "fail" and checks["KR-CODE-10"]["failed"] == 1


def test_same_key_spaces_not_contained_are_overlap(base, tmp_path):
    # 같은 키인데 포함 관계가 아니면(옆으로 어긋남) 두 세트가 아니라 진짜 겹침: _contains 를 무력화하면 실패해야 한다
    st = _first_storey(base)
    _add_space(base, st, "01.002.01", "창고", 3000.0, 3000.0, 20000.0, 2000.0)
    _add_space(base, st, "01.002.01", "창고", 2000.0, 2000.0, 21200.0, 2000.0)   # 일부만 겹침(포함 아님)
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-10"]["status"] == "fail"


def test_three_nested_sets_are_not_flagged(base, tmp_path):
    st = _first_storey(base)
    for w, d in ((3000.0, 3000.0), (2800.0, 2800.0), (2600.0, 2600.0)):
        _add_space(base, st, "01.002.01", "창고", w, d, 20000.0, 2000.0)
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-10"]["status"] == "pass", checks["KR-CODE-10"]["examples"]


def test_three_same_key_with_one_duplicate_is_flagged(base, tmp_path):
    st = _first_storey(base)
    for w, d in ((3000.0, 3000.0), (2800.0, 2800.0), (2800.0, 2800.0)):
        _add_space(base, st, "01.002.01", "창고", w, d, 20000.0, 2000.0)
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-10"]["status"] == "fail"


# ---------------------------------------------------------------- 4~5. ArchiCAD 빈 값, IFC2X3 빈 ePSet

def test_archicad_pset_with_only_empty_values_is_not_a_workcode(base, tmp_path):
    _drop_workcode_psets(base)
    for cls in MAIN:
        for e in base.by_type(cls):
            _user_pset(base, e, "조달청표준공사코드-세부공종", {"[철물]철물": "", "[성능]열관류율": "-"})
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-19"]["status"] == "fail" and checks["KR-CODE-19"]["failed"] == 4


def test_ifc2x3_empty_epset_is_not_a_declaration(tmp_path):
    f, proj, *_ = _ifc2x3_project(areas=False)
    _raw_pset(f, proj, "ePSet_MapConversion", {})
    _raw_pset(f, proj, "ePSet_ProjectedCRS", {})
    checks, _ = _run(f, tmp_path)
    assert checks["KR-CODE-11"]["status"] == "fail" and checks["KR-CODE-12"]["status"] == "na"


# ---------------------------------------------------------------- 6. na 노트, 사용자 IDS, IDS 엔진 오류

def test_override_na_note_is_kept(base, tmp_path):
    checks, _ = _run(base, tmp_path)          # 범용객체가 없음
    assert checks["KR-IDS-06"]["status"] == "na" and "적용 대상 객체 없음" in checks["KR-IDS-06"]["note"]


def test_custom_ids_path_disables_override(base, tmp_path):
    from krrules import ids_build
    for sp in base.by_type("IfcSpace"):
        real = sp.Name
        sp.Name = None
        _user_pset(base, sp, "PPS_Room", {"실ID": real})
    p = tmp_path / "00_본관_BA.ifc"
    base.write(str(p))
    mine = tmp_path / "mine.ids"
    ids_build.write(mine)
    default = {c["id"]: c for c in run.run_file(p)["checks"]}
    custom = {c["id"]: c for c in run.run_file(p, ids_path=mine)["checks"]}
    assert default["KR-IDS-04"]["status"] == "pass"            # OR 판정: 실ID 속성
    assert custom["KR-IDS-04"]["status"] == "fail"             # 사용자 IDS: 엄격 슬롯(Name)만 봄, 대체하지 않음


def test_ids_engine_failure_propagates_to_overridden_checks(base, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("IDS 엔진 고장")
    monkeypatch.setattr(run, "run_ids", boom)
    checks, _ = _run(base, tmp_path)
    for cid in ("KR-IDS-04", "KR-IDS-07", "KR-IDS-13", "KR-IDS-18"):
        assert checks[cid]["status"] == "error", cid


# ---------------------------------------------------------------- 7. CRS 경계, 8. 파일 크기 경계

@pytest.mark.parametrize("name,expected", [("EPSG:25178", "warn"), ("EPSG:5178", "pass"), ("EPSG:5186", "pass"),
                                           ("X5178", "warn"), ("EPSG:325652", "warn")])
def test_crs_pattern_has_leading_boundary(base, tmp_path, name, expected):
    base.by_type("IfcProjectedCRS")[0].Name = name
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-12"]["status"] == expected, name


@pytest.mark.parametrize("size,status", [(200_000_000, "pass"), (200_000_001, "warn"),
                                         (200 * 1024 * 1024, "warn"),     # 200MiB(209,715,200) 는 200MB 초과
                                         (300_000_001, "warn")])
def test_filesize_is_decimal_megabytes(monkeypatch, size, status):
    monkeypatch.setattr(codechecks.os.path, "getsize", lambda p: size)
    r = codechecks.check_filesize(None, path="x.ifc")
    assert r["status"] == status
    if status == "warn":
        assert ("zip 압축 제출이 허용" in r["examples"][0]["reason"]) == (size > 300_000_000)


@pytest.mark.parametrize("val", ["0.0", "0.00 ㎡", "-3", -5.0, "추후 입력"])
def test_non_positive_or_non_numeric_text_rejected_for_numeric_fields(base, tmp_path, val):
    # 문자열 자리표시가 아니어도 면적·층수는 0 보다 큰 수가 들어 있어야 한다
    _strip_standard_project_info(base)
    _user_pset(base, base.by_type("IfcProject")[0], "프로젝트 정보", {
        "사업명": "x", "주소": "y", "용도": "z", "층수": val, "건축면적": val, "연면적": val, "대지면적": val})
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-IDS-16", "KR-IDS-17", "KR-IDS-18"}
    assert "속성 층수" not in checks["KR-IDS-15"]["found_via"]
