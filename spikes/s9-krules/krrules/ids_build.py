"""한국 납품 규칙 중 IDS 로 표현 가능한 것들을 ifctester 로 만들어 .ids(XML)로 쓴다.

각 specification 의 identifier 는 requirements.json 의 check_id 와 같다.
원문 조항 번호는 description 에 짧게만 적는다(원문 인용 없음).
IFC 속성 사상(예: 실명 -> IfcSpace.LongName)은 원문이 정한 것이 아니라
이 스파이크의 해석이며, instructions 에 "해석" 으로 표시한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ifctester import ids
from ifctester.facet import Restriction

ALL_VERSIONS = ["IFC2X3", "IFC4", "IFC4X3_ADD2"]
IFC4_UP = ["IFC4", "IFC4X3_ADD2"]

# 최소 부위 작성 대상으로 보는 주요 부위 클래스(조달청 5.2.2(4)나 표의 건축·구조·지붕 계열)
MAIN_ELEMENTS = [
    "IFCWALL", "IFCSLAB", "IFCCOLUMN", "IFCBEAM", "IFCDOOR", "IFCWINDOW",
    "IFCROOF", "IFCSTAIR", "IFCRAILING", "IFCCURTAINWALL", "IFCCOVERING", "IFCFOOTING",
]
# 외기면 정보(IsExternal) 대상 7종: 바닥, 벽체, 지붕, 기둥, 문, 창문, 커튼월
ENERGY_7 = ["IFCSLAB", "IFCWALL", "IFCROOF", "IFCCOLUMN", "IFCDOOR", "IFCWINDOW", "IFCCURTAINWALL"]
# 열관류율 대상: 조달청 6종(바닥 포함) / 국토부 시행지침 5종(바닥 제외)
THERMAL_6 = ["IFCSLAB", "IFCWALL", "IFCROOF", "IFCDOOR", "IFCWINDOW", "IFCCURTAINWALL"]
THERMAL_5 = ["IFCWALL", "IFCROOF", "IFCDOOR", "IFCWINDOW", "IFCCURTAINWALL"]


def _enum(values):
    return Restriction(options={"enumeration": list(values)}, base="string")


def _pat(regex):
    return Restriction(options={"pattern": regex}, base="string")


def _spec(identifier, name, description, instructions, versions=None, required_exist=False):
    s = ids.Specification(
        name=name, identifier=identifier, description=description,
        instructions=instructions, ifcVersion=versions or ALL_VERSIONS,
    )
    if required_exist:
        s.set_usage("required")  # 적용 대상이 최소 1개 있어야 함(minOccurs=1)
    return s


def build() -> ids.Ids:
    doc = ids.Ids(
        title="한국 납품 규칙 시범 IDS(조달청 v2.1 실시설계 + 국토부 시행지침 기준)",
        description="S9 스파이크: 공식 출처가 확인된 문서의 요구사항만 옮김. 속성 사상은 해석이며 사람 확인 필요.",
        version="0.1",
        author="spike-s9@example.invalid",
        purpose="납품 전 자체 점검",
    )

    # --- 공간 구조: 층·실 존재(조달청 5.2.2(4)나 최소부위: 공간 = 층, 구역, 실) ---
    s = _spec("KR-IDS-01", "층(IfcBuildingStorey)이 1개 이상 있어야 함",
              "조달청 v2.1 5.2.2(4)나 최소부위 작성대상(공간: 층)", "구역(IfcZone)은 선택으로 두고 검사하지 않음",
              required_exist=True)
    s.applicability.append(ids.Entity(name="IFCBUILDINGSTOREY"))
    doc.specifications.append(s)

    s = _spec("KR-IDS-02", "실(IfcSpace)이 1개 이상 있어야 함",
              "조달청 v2.1 5.2.2(3) 공간객체 작성, 5.2.2(4)나 최소부위(공간: 실)", "", required_exist=True)
    s.applicability.append(ids.Entity(name="IFCSPACE"))
    doc.specifications.append(s)

    # --- 공간객체 정보: 실명·실ID·공간분류코드(조달청 부속서-1 3.1) ---
    s = _spec("KR-IDS-03", "실에는 실명이 있어야 함",
              "조달청 v2.1 부속서-1 3.1(1)(2) 공간객체 입력정보: 실명",
              "해석: 실명 = IfcSpace.LongName (Revit/Archicad IFC 내보내기 관례). 원문은 IFC 속성을 지정하지 않음")
    s.applicability.append(ids.Entity(name="IFCSPACE"))
    s.requirements.append(ids.Attribute(name="LongName", cardinality="required"))
    doc.specifications.append(s)

    s = _spec("KR-IDS-04", "실에는 실ID(Name)가 있어야 함",
              "조달청 v2.1 부속서-1 3.1(1)(3) 공간객체 입력정보: 실ID",
              "해석: 실ID = IfcSpace.Name (내보내기 관례: 방 번호). 형식 검사는 KR-CODE-08")
    s.applicability.append(ids.Entity(name="IFCSPACE"))
    s.requirements.append(ids.Attribute(name="Name", cardinality="required"))
    doc.specifications.append(s)

    s = _spec("KR-IDS-05", "실에는 공간분류 분류참조가 있어야 함",
              "조달청 v2.1 부속서-1 3.1(1)(4) 공간객체 입력정보: 공간분류코드",
              "해석: IfcRelAssociatesClassification 로 연결. 코드 형식은 KR-CODE-09, 목록 전체 대조는 미구현")
    s.applicability.append(ids.Entity(name="IFCSPACE"))
    s.requirements.append(ids.Classification(system=_pat(".+"), cardinality="required"))
    doc.specifications.append(s)

    # --- 범용객체: 분류 정보 필수(조달청 5.2.2(4)마, 부속서-1 5) ---
    s = _spec("KR-IDS-06", "범용객체(IfcBuildingElementProxy)에는 분류 정보가 있어야 함",
              "조달청 v2.1 5.2.2(4)마, 부속서-1 5.1: 범용객체는 건설정보분류체계 부위분류를 입력",
              "해석: 분류 참조 존재만 검사. 부위분류 목록(E0.. 등) 대조는 미구현")
    s.applicability.append(ids.Entity(name="IFCBUILDINGELEMENTPROXY"))
    s.requirements.append(ids.Classification(system=_pat(".+"), cardinality="required"))
    doc.specifications.append(s)

    # --- 부위객체 정보: 부재명, 조달청표준공사코드(조달청 부속서-1 4.2) ---
    s = _spec("KR-IDS-07", "주요 부위 객체에는 부재명(Name)이 있어야 함",
              "조달청 v2.1 5.2.2(4)바, 부속서-1 4.2(2): 부위객체에 부재명 입력",
              "해석: 부재명 = IfcElement.Name. 원문은 소프트웨어 기본 속성 항목을 쓰라고만 함")
    s.applicability.append(ids.Entity(name=_enum(MAIN_ELEMENTS)))
    s.requirements.append(ids.Attribute(name="Name", cardinality="required"))
    doc.specifications.append(s)

    # --- 에너지 검토용 외피 정보(조달청 5.2.5(4)나, 시행지침 설계자편 2.3.2) ---
    s = _spec("KR-IDS-08", "에너지 검토 시 7종 부위에는 IsExternal 값이 있어야 함",
              "조달청 v2.1 5.2.5(4)나 / 국토부 시행지침 설계자편 2.3.2 건축: 바닥·벽체·지붕·기둥·문·창문·커튼월",
              "조건부 규칙(환경시뮬레이션 수행 시). 값이 외기 면 실제와 맞는지는 기하 판정이 필요해 검사하지 않음")
    s.applicability.append(ids.Entity(name=_enum(ENERGY_7)))
    s.requirements.append(ids.Property(propertySet=_pat("Pset_.*Common"), baseName="IsExternal",
                                       dataType="IFCBOOLEAN", cardinality="required"))
    doc.specifications.append(s)

    for ident, label, classes, src in (
        ("KR-IDS-09", "6종", THERMAL_6, "조달청 v2.1 5.2.5(4)나: 바닥·벽체·지붕·문·창문·커튼월 6종"),
        ("KR-IDS-10", "5종", THERMAL_5, "국토부 시행지침 설계자편 2.3.2 건축: 벽체·지붕·문·창문·커튼월 5종(선택 사항)"),
    ):
        s = _spec(ident, f"외기에 면한 {label} 부위에는 열관류율이 있어야 함", src,
                  "조건부: IsExternal=TRUE 인 객체만 대상. 두 문서의 대상 종류가 달라(바닥 포함 여부) 따로 둠")
        s.applicability.append(ids.Entity(name=_enum(classes)))
        s.applicability.append(ids.Property(propertySet=_pat("Pset_.*Common"), baseName="IsExternal",
                                            value="TRUE", dataType="IFCBOOLEAN"))
        s.requirements.append(ids.Property(propertySet=_pat("Pset_.*Common"), baseName="ThermalTransmittance",
                                           dataType="IFCTHERMALTRANSMITTANCEMEASURE", cardinality="required"))
        doc.specifications.append(s)

    # --- 사업정보·대지(조달청 부속서-1 2, 5.2.7(2)) ---
    s = _spec("KR-IDS-11", "대지(IfcSite)에 위도·경도가 있어야 함",
              "조달청 v2.1 5.2.7(2)가 대지 임시수준점의 GIS 좌표 / 국토부 설계자편 2.3.2(1)(나)",
              "해석: IfcSite.RefLatitude/RefLongitude. 임시수준점이 대지 기준점과 같다고 보는 단순화")
    s.applicability.append(ids.Entity(name="IFCSITE"))
    s.requirements.append(ids.Attribute(name="RefLatitude", cardinality="required"))
    s.requirements.append(ids.Attribute(name="RefLongitude", cardinality="required"))
    doc.specifications.append(s)

    s = _spec("KR-IDS-12", "대지에 주소(SiteAddress)가 있어야 함",
              "조달청 v2.1 부속서-1 2 사업정보: 대지현황(주소)", "해석: IfcSite.SiteAddress")
    s.applicability.append(ids.Entity(name="IFCSITE"))
    s.requirements.append(ids.Attribute(name="SiteAddress", cardinality="required"))
    doc.specifications.append(s)

    s = _spec("KR-IDS-13", "프로젝트에 사업명(Name)이 있어야 함",
              "조달청 v2.1 부속서-1 2 사업정보: 사업명", "해석: IfcProject.Name")
    s.applicability.append(ids.Entity(name="IFCPROJECT"))
    s.requirements.append(ids.Attribute(name="Name", cardinality="required"))
    doc.specifications.append(s)

    s = _spec("KR-IDS-14", "건물에 용도(OccupancyType)가 있어야 함",
              "조달청 v2.1 부속서-1 2 사업정보: 건축규모(용도)", "해석: Pset_BuildingCommon.OccupancyType")
    s.applicability.append(ids.Entity(name="IFCBUILDING"))
    s.requirements.append(ids.Property(propertySet="Pset_BuildingCommon", baseName="OccupancyType", cardinality="required"))
    doc.specifications.append(s)

    s = _spec("KR-IDS-15", "건물에 층수(NumberOfStoreys)가 있어야 함",
              "조달청 v2.1 부속서-1 2 사업정보: 건축규모(층수)", "해석: Pset_BuildingCommon.NumberOfStoreys")
    s.applicability.append(ids.Entity(name="IFCBUILDING"))
    s.requirements.append(ids.Property(propertySet="Pset_BuildingCommon", baseName="NumberOfStoreys", cardinality="required"))
    doc.specifications.append(s)

    s = _spec("KR-IDS-16", "건물에 연면적(GrossFloorArea)이 있어야 함",
              "조달청 v2.1 부속서-1 2 사업정보: 건축규모(연면적)",
              "해석: Qto_BuildingBaseQuantities.GrossFloorArea (IFC4 이상)", versions=IFC4_UP)
    s.applicability.append(ids.Entity(name="IFCBUILDING"))
    s.requirements.append(ids.Property(propertySet="Qto_BuildingBaseQuantities", baseName="GrossFloorArea", cardinality="required"))
    doc.specifications.append(s)

    s = _spec("KR-IDS-17", "건물에 건축면적(FootprintArea)이 있어야 함",
              "조달청 v2.1 부속서-1 2 사업정보: 건축규모(건축면적)",
              "해석: Qto_BuildingBaseQuantities.FootprintArea (IFC4 이상)", versions=IFC4_UP)
    s.applicability.append(ids.Entity(name="IFCBUILDING"))
    s.requirements.append(ids.Property(propertySet="Qto_BuildingBaseQuantities", baseName="FootprintArea", cardinality="required"))
    doc.specifications.append(s)

    s = _spec("KR-IDS-18", "대지에 대지면적(GrossArea)이 있어야 함",
              "조달청 v2.1 부속서-1 2 사업정보: 대지현황(대지면적)",
              "해석: Qto_SiteBaseQuantities.GrossArea (IFC4 이상)", versions=IFC4_UP)
    s.applicability.append(ids.Entity(name="IFCSITE"))
    s.requirements.append(ids.Property(propertySet="Qto_SiteBaseQuantities", baseName="GrossArea", cardinality="required"))
    doc.specifications.append(s)

    return doc


def write(path: Path) -> None:
    doc = build()
    doc.to_xml(str(path))


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "ids" / "kr_delivery_construction.ids"
    write(out)
    print("wrote", out.name)
