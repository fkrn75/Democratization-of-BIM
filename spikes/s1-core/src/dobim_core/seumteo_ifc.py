# SPDX-License-Identifier: MPL-2.0
"""세움터 제출용 면적 객체를 IFC 에 싣는다(W1 연장 6번째 PR, ARC-36 마무리). ifc.export 의 seumteo_areas 옵션 전용.

정본 투영(ifc_model)은 건드리지 않는다: 정본 파일을 문자열로 복사한 파일에만 더한다. 옵션을 켜지 않으면 이 모듈은 불리지 않아
기존 내보내기 결과는 바이트 그대로다.

면적 객체 하나 = IfcSpace 하나(PredefinedType USERDEFINED, ObjectType 'AREA_용도구획' 등)와 속성셋 두 개:
  DoBIM_AIS          : AIS_분류코드(IfcLabel), 면적(IfcAreaMeasure ㎡), 용도구획이면 AIS_연면적제외·AIS_용적률제외·AIS_바닥면적제외(IfcBoolean)
  DoBIM_SeumteoNote  : 근거(건축법 시행령 119조 코드), 원본(DoBIM id), 확인필요(문장), 용도구획이면 겹침(층 벽 영역 위에 겹쳐 놓은 뺌·용적률 제외 객체)
이 형태는 krules/seumteo.py 의 가정 1(AIS_ 속성은 Pset 이름 불문)·가정 2(면적 객체는 IfcSpace, 코드 값과 전용 속성·면적 값으로 구분)를 그대로 따른다.
세움터가 실제로 읽는 IFC 위치·Pset 이름은 근거 문서에 없어 확인 필요다(commands_area.SEU_UNVERIFIED).
배치: 용도구획은 그 층(IfcBuildingStorey) 아래, 건축면적은 건물(IfcBuilding) 아래, 대지면적은 뼈대 대지(IfcSite) 아래 집합(IfcRelAggregates).
형상: 용도구획은 층 바닥에서 두께 SLAB_MM 로 압출한 몸체(같은 층 겹침 검사 KR-SEU-13 이 경계상자로 본다), 건축면적·대지면적은
평면 선분 묶음(FootPrint, GeometricCurveSet)이다. GlobalId 는 프로젝트 GUID 와 표지에서 정한다(같은 모델이면 같은 파일).
"""
from __future__ import annotations

import ifcopenshell

from dobim_core.commands_area import SEU_FLAG_NAMES

SLAB_MM = 10.0                       # 용도구획 몸체 두께(mm). 면적 객체는 2차원이라 얇은 판으로 둔다
PSET_AIS = "DoBIM_AIS"               # 세움터 Pset 이름은 근거에 없어(krules 가정 1: 불문) DoBIM 이름을 쓴다(확인 필요)
PSET_NOTE = "DoBIM_SeumteoNote"
KIND_TYPE = {"A": "AREA_용도구획", "B": "AREA_건축면적", "S": "AREA_대지면적"}
UNVERIFIED_NOTE = {"A": "용도 코드 표 대조 안 함, Pset 이름·제외 플래그 대응은 해석(확인 필요)",
                   "B": "Pset 이름은 해석, 형상은 벽 중심선 합집합 바깥선이고 면적 값은 조정 포함(확인 필요)",
                   "S": "Pset 이름은 해석(확인 필요)"}


def copy_file(f):
    """정본 투영 파일의 사본(엔티티 번호 그대로). 사본에만 면적 객체를 더한다."""
    return ifcopenshell.file.from_string(f.to_string())


def _footprint_context(f, body):
    """FootPrint 하위 문맥: 이미 있으면 그것, 없으면 몸체 문맥의 부모(Model) 아래 하나 만든다."""
    for c in f.by_type("IfcGeometricRepresentationSubContext"):
        if c.ContextIdentifier == "FootPrint":
            return c
    return f.create_entity("IfcGeometricRepresentationSubContext", ContextIdentifier="FootPrint", ContextType="Model",
                           ParentContext=body.ParentContext, TargetView="PLAN_VIEW")


def _pt2(f, x, y):
    return f.create_entity("IfcCartesianPoint", Coordinates=[float(x), float(y)])


def _placement(f, rel_to):
    origin = f.create_entity("IfcCartesianPoint", Coordinates=[0.0, 0.0, 0.0])
    return f.create_entity("IfcLocalPlacement", PlacementRelTo=rel_to,
                           RelativePlacement=f.create_entity("IfcAxis2Placement3D", Location=origin))


def _body(f, ctx_body, pts):
    pl = f.create_entity("IfcPolyline", Points=[_pt2(f, x, y) for x, y in list(pts) + [pts[0]]])
    prof = f.create_entity("IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=pl)
    pos = f.create_entity("IfcAxis2Placement3D", Location=f.create_entity("IfcCartesianPoint", Coordinates=[0.0, 0.0, 0.0]))
    solid = f.create_entity("IfcExtrudedAreaSolid", SweptArea=prof, Position=pos,
                            ExtrudedDirection=f.create_entity("IfcDirection", DirectionRatios=[0.0, 0.0, 1.0]), Depth=SLAB_MM)
    return f.create_entity("IfcShapeRepresentation", ContextOfItems=ctx_body, RepresentationIdentifier="Body",
                           RepresentationType="SweptSolid", Items=[solid])


def _footprint(f, ctx_fp, segs):
    curves = [f.create_entity("IfcPolyline", Points=[_pt2(f, x0, y0), _pt2(f, x1, y1)]) for x0, y0, x1, y1 in segs]
    item = f.create_entity("IfcGeometricCurveSet", Elements=curves)
    return f.create_entity("IfcShapeRepresentation", ContextOfItems=ctx_fp, RepresentationIdentifier="FootPrint",
                           RepresentationType="GeometricCurveSet", Items=[item])


def _pset(f, guid, name, props, obj):
    ps = f.create_entity("IfcPropertySet", GlobalId=guid(f"pset/{name}"), Name=name, HasProperties=props)
    f.create_entity("IfcRelDefinesByProperties", GlobalId=guid(f"rel/{name}"), RelatedObjects=[obj], RelatingPropertyDefinition=ps)


def _prop(f, name, cls, value):
    return f.create_entity("IfcPropertySingleValue", Name=name, NominalValue=f.create_entity(cls, value))


def add_areas(f, model, objects: list) -> dict:
    """f(정본의 사본)에 면적 객체(commands_area.seumteo_plan 의 objects)를 더한다. model = 엔진의 IfcModel(부모의 GlobalId·문맥 번호를 얻는다).
    반환: {종류: 개수}."""
    body = f.by_id(model.body.id())
    fp = None
    parents = {}
    children: dict = {}
    counts: dict = {}

    def parent_of(o):
        if o["kind"] == "A":
            key = model.ents[o["parent"]].GlobalId
        elif o["kind"] == "B":
            key = model.building.GlobalId
        else:
            key = model.site_root.GlobalId if model.site_root is not None else model.building.GlobalId
        if key not in parents:
            parents[key] = f.by_guid(key)
        return parents[key]

    for o in objects:
        def guid(tail, _l=o["label"]):
            return model.derived_guid_for(f"seumteo/{_l}/{tail}")
        par = parent_of(o)
        sp = f.create_entity("IfcSpace", GlobalId=guid("space"), Name=o["name"], Description="세움터 면적 객체(DoBIM)",
                             ObjectType=KIND_TYPE[o["kind"]], CompositionType="ELEMENT", PredefinedType="USERDEFINED")
        sp.ObjectPlacement = _placement(f, par.ObjectPlacement)
        if o["boundary"]:
            rep = _body(f, body, o["boundary"])
        elif o["outline"]:
            fp = fp or _footprint_context(f, body)
            rep = _footprint(f, fp, o["outline"])
        else:
            rep = None
        if rep is not None:
            sp.Representation = f.create_entity("IfcProductDefinitionShape", Representations=[rep])
        children.setdefault(par.GlobalId, []).append(sp)
        props = [_prop(f, "AIS_분류코드", "IfcLabel", o["code"]), _prop(f, "면적", "IfcAreaMeasure", float(o["area_m2"]))]
        if o["flags"] is not None:
            props += [_prop(f, n, "IfcBoolean", bool(v)) for n, v in zip(SEU_FLAG_NAMES, o["flags"])]
        _pset(f, guid, PSET_AIS, props, sp)
        note = [_prop(f, "근거", "IfcLabel", o["basis"]), _prop(f, "확인필요", "IfcText", UNVERIFIED_NOTE[o["kind"]])]
        if o["source"] is not None:
            note.insert(1, _prop(f, "원본", "IfcIdentifier", o["source"]))
        if o["kind"] == "A":
            note.append(_prop(f, "겹침", "IfcBoolean", bool(o["overlay"])))
        _pset(f, guid, PSET_NOTE, note, sp)
        counts[o["kind"]] = counts.get(o["kind"], 0) + 1
    for key, kids in children.items():
        f.create_entity("IfcRelAggregates", GlobalId=model.derived_guid_for(f"seumteo/agg/{key}"), RelatingObject=parents[key], RelatedObjects=kids)
    return counts


def fix_2x3(f) -> None:
    """IFC2x3 로 내린 뒤: 면적 객체의 PredefinedType(USERDEFINED)은 IFC2x3 의 필수 InteriorOrExteriorSpace 로 옮겨지지 않아 NOTDEFINED 로 채운다
    (면적 객체만, 실 공간은 이전기가 INTERNAL 을 옮긴다)."""
    kinds = set(KIND_TYPE.values())
    for e in f.by_type("IfcSpace"):
        if e.InteriorOrExteriorSpace is None and e.ObjectType in kinds:
            e.InteriorOrExteriorSpace = "NOTDEFINED"


SELF_CHECKS = ("KR-SEU-09", "KR-SEU-10", "KR-SEU-11", "KR-SEU-13")


def self_check(f) -> dict:
    """내보낸 파일을 저장소 krules 세움터 검사(면적 객체 4종)로 다시 본다: {검사 id: 상태}."""
    from dobim_core.krules import seumteo as SEU
    return {cid: SEU.SEU_CHECKS[cid](f)["status"] for cid in SELF_CHECKS}
