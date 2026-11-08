"""시험용 IFC 합성기: 조달청 v2.1 규칙을 모두 만족하도록 처음부터 만든 작은 모델(mm 단위, IFC4).

'통과 가능성' 을 보여주는 양성 대조군이다. 변이 함수로 규칙을 하나씩 깨서 검사가 잡는지 본다.
표본(RA-01 등)은 규칙 대부분을 위반하므로 이런 대조군이 없으면 검사가 통과할 수 있는지 알 수 없다.
"""
from __future__ import annotations

import numpy as np
import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.classification
import ifcopenshell.api.context
import ifcopenshell.api.feature
import ifcopenshell.api.geometry
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import ifcopenshell.api.georeference


def _place(f, el, x, y, z):
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    ifcopenshell.api.geometry.edit_object_placement(f, product=el, matrix=m, is_si=False)


# 표현 생성 함수(add_*_representation)의 길이 인자는 SI(m) 로 받아 프로젝트 단위(mm)로 환산된다.


def build_compliant(unit: str = "MILLIMETERS") -> ifcopenshell.file:
    k = {"MILLIMETERS": 1.0, "CENTIMETERS": 0.1, "METERS": 0.001}[unit]  # mm 값을 프로젝트 단위로 바꾸는 배율
    f = ifcopenshell.api.project.create_file(version="IFC4")
    project = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="OO 건물 신축공사")
    ifcopenshell.api.unit.assign_unit(f, length={"is_metric": True, "raw": unit})
    model = ifcopenshell.api.context.add_context(f, context_type="Model")
    body = ifcopenshell.api.context.add_context(
        f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model)

    site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite", name="대지")
    site.RefLatitude = (37, 33, 59, 0)
    site.RefLongitude = (126, 58, 40, 0)
    addr = f.create_entity("IfcPostalAddress", AddressLines=["서울시 OO구 OO동 OO번지"], Town="서울", Country="KR")
    site.SiteAddress = addr
    building = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuilding", name="본관")
    ifcopenshell.api.aggregate.assign_object(f, products=[site], relating_object=project)
    ifcopenshell.api.aggregate.assign_object(f, products=[building], relating_object=site)

    # 건축규모(해석한 사상): Pset_BuildingCommon / Qto_BuildingBaseQuantities / Qto_SiteBaseQuantities
    ps = ifcopenshell.api.pset.add_pset(f, product=building, name="Pset_BuildingCommon")
    ifcopenshell.api.pset.edit_pset(f, pset=ps, properties={"OccupancyType": "업무시설", "NumberOfStoreys": 2})
    q = ifcopenshell.api.pset.add_qto(f, product=building, name="Qto_BuildingBaseQuantities")
    ifcopenshell.api.pset.edit_qto(f, qto=q, properties={"GrossFloorArea": 200.0, "FootprintArea": 100.0})
    q = ifcopenshell.api.pset.add_qto(f, product=site, name="Qto_SiteBaseQuantities")
    ifcopenshell.api.pset.edit_qto(f, qto=q, properties={"GrossArea": 900.0})

    # 분류 체계(공간분류코드)
    cls = ifcopenshell.api.classification.add_classification(f, classification="조달청 공간분류")

    storeys = []
    for name, elev in (("B1", -3000.0 * k), ("1F", 0.0)):
        st = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuildingStorey", name=name)
        st.Elevation = elev
        ifcopenshell.api.aggregate.assign_object(f, products=[st], relating_object=building)
        storeys.append((st, elev))

    # 층마다 실 둘(나란히 붙어 있어 경계만 닿고 겹치지 않음) + 외벽 하나 + 문 하나
    for idx, (st, elev) in enumerate(storeys):
        for sub, (sx, long_name) in enumerate(((2500.0, "사무실" if idx else "기계실"), (7500.0, "회의실"))):
            space = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSpace", name=f"01.00{idx + 1}.0{sub + 1}")
            space.LongName = long_name
            rep = ifcopenshell.api.geometry.add_profile_representation(
                f, context=body, profile=f.createIfcRectangleProfileDef("AREA", None, None, 5000.0 * k, 4000.0 * k), depth=2.8)
            ifcopenshell.api.geometry.assign_representation(f, product=space, representation=rep)
            _place(f, space, sx * k, 2000.0 * k, elev)
            ifcopenshell.api.aggregate.assign_object(f, products=[space], relating_object=st)
            ifcopenshell.api.classification.add_reference(
                f, products=[space], identification="33202" if idx else "33405", name="분류", classification=cls)

        wall = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name=f"W{idx + 1}")
        wrep = ifcopenshell.api.geometry.add_wall_representation(f, context=body, length=5.0, height=2.8, thickness=0.2)
        ifcopenshell.api.geometry.assign_representation(f, product=wall, representation=wrep)
        _place(f, wall, 0.0, 0.0, elev)
        ifcopenshell.api.spatial.assign_container(f, products=[wall], relating_structure=st)
        pw = ifcopenshell.api.pset.add_pset(f, product=wall, name="Pset_WallCommon")
        ifcopenshell.api.pset.edit_pset(f, pset=pw, properties={"IsExternal": True, "ThermalTransmittance": 0.21})
        pc = ifcopenshell.api.pset.add_pset(f, product=wall, name="PPS_Info")
        ifcopenshell.api.pset.edit_pset(f, pset=pc, properties={"조달청표준공사코드": "D:200+FA11"})

        door = ifcopenshell.api.root.create_entity(f, ifc_class="IfcDoor", name=f"D{idx + 1}")
        drep = ifcopenshell.api.geometry.add_wall_representation(f, context=body, length=0.9, height=2.1, thickness=0.1)
        ifcopenshell.api.geometry.assign_representation(f, product=door, representation=drep)
        _place(f, door, 1000.0 * k, 0.0, elev)
        ifcopenshell.api.spatial.assign_container(f, products=[door], relating_structure=st)
        pd = ifcopenshell.api.pset.add_pset(f, product=door, name="Pset_DoorCommon")
        ifcopenshell.api.pset.edit_pset(f, pset=pd, properties={"IsExternal": True, "ThermalTransmittance": 1.5})
        pc = ifcopenshell.api.pset.add_pset(f, product=door, name="PPS_Info")
        ifcopenshell.api.pset.edit_pset(f, pset=pc, properties={"조달청표준공사코드": "LA1"})
        opening = ifcopenshell.api.root.create_entity(f, ifc_class="IfcOpeningElement", name=f"OP{idx + 1}")
        orep = ifcopenshell.api.geometry.add_wall_representation(f, context=body, length=0.9, height=2.1, thickness=0.3)
        ifcopenshell.api.geometry.assign_representation(f, product=opening, representation=orep)
        _place(f, opening, 1000.0 * k, -50.0 * k, elev)
        ifcopenshell.api.feature.add_feature(f, feature=opening, element=wall)
        ifcopenshell.api.feature.add_filling(f, opening=opening, element=door)

    # 좌표: 진북 + 한국 TM 중부원점(EPSG:5186)
    ifcopenshell.api.georeference.add_georeferencing(f)
    ifcopenshell.api.georeference.edit_georeferencing(
        f, projected_crs={"Name": "EPSG:5186", "GeodeticDatum": "GRS80", "MapProjection": "TM"},
        coordinate_operation={"Eastings": 200000.0, "Northings": 600000.0, "OrthogonalHeight": 0.0,
                              "XAxisAbscissa": 1.0, "XAxisOrdinate": 0.0, "Scale": 1.0})
    return f
