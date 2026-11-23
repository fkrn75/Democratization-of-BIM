# SPDX-License-Identifier: MPL-2.0
"""IFC2x3 TC1 입출력(W1: IO-03). 세움터 등 IFC2x3 제출처용.

정본은 IFC4 투영(ifc_model)이다. IFC2x3 은 그 투영을 복사해 스키마를 내리는 변환으로 만든다(IFC4 투영·출력은 건드리지 않는다).
변환은 ifcopenshell 의 스키마 이전기(ifcopenshell.util.schema.Migrator)를 쓰고, 이전기가 못 하는 곳을 여기서 채운다:

  * IFC2x3 에 없는 클래스 -> 가까운 IFC2x3 클래스(CLASS_MAP). 원래 클래스·PredefinedType 은 ObjectType 에 남긴다
    (IFC2x3 의 USERDEFINED 관행: ObjectType 이 사용자 정의 분류를 담는다)
  * IfcDoorType·IfcWindowType -> IfcDoorStyle·IfcWindowStyle(필수 ConstructionType·Sizeable 채움)
  * IFC2x3 에서 필수인 IfcOwnerHistory 를 하나 만들어 모든 IfcRoot 에 붙인다(IFC4 투영은 선택이라 비워 둔다)
  * IFC2x3 에 없는 속성(문·창·기둥 등의 PredefinedType, 문 OperationType 등)은 손실로 경고 목록에 모은다

읽기(to_ifc4)는 반대 방향: IFC2x3 파일을 IFC4 로 올려 기존 가져오기(foreign)가 읽게 한다.
"""
from __future__ import annotations

import contextlib
import io

import ifcopenshell
import ifcopenshell.util.schema as US

# IFC4 클래스 -> IFC2x3 클래스(이전기의 표에 없는 것만). 설비 단말·기기는 IFC2x3 의 일반 분배 요소 클래스로 내린다
CLASS_MAP = {
    "IfcSanitaryTerminal": "IfcFlowTerminal", "IfcAirTerminal": "IfcFlowTerminal", "IfcLightFixture": "IfcFlowTerminal",
    "IfcFireSuppressionTerminal": "IfcFlowTerminal", "IfcElectricAppliance": "IfcFlowTerminal",
    "IfcFan": "IfcFlowMovingDevice", "IfcBoiler": "IfcEnergyConversionDevice", "IfcUnitaryEquipment": "IfcEnergyConversionDevice",
    "IfcTank": "IfcFlowStorageDevice", "IfcValve": "IfcFlowController", "IfcFlowMeter": "IfcFlowController",
    "IfcElectricDistributionBoard": "IfcFlowController",
    "IfcPipeSegment": "IfcFlowSegment", "IfcDuctSegment": "IfcFlowSegment", "IfcCableCarrierSegment": "IfcFlowSegment",
    "IfcPipeFitting": "IfcFlowFitting",
    "IfcSensor": "IfcDistributionControlElement", "IfcUnitaryControlElement": "IfcDistributionControlElement",
    "IfcGeographicElement": "IfcBuildingElementProxy",
    "IfcFurniture": "IfcFurnishingElement",
    "IfcDoorType": "IfcDoorStyle", "IfcWindowType": "IfcWindowStyle",
    "IfcRoofType": "IfcSlabType",   # 지붕 유형(W2 여덟 번째 PR): IFC2x3 에 IfcRoofType 이 없다. IfcSlabType(ROOF)로 내린다
}

APP = ("DoBIM", "DoBIM", "DoBIM IFC2x3 export")   # (개발 조직, 응용 프로그램 식별자, 전체 이름)


def _owner_history(f, version: str, stamp: int):
    """IFC2x3 필수 IfcOwnerHistory 하나(사람·조직은 응용 프로그램 이름으로 둔다: 사용자 신원을 파일에 넣지 않는다)."""
    org = f.create_entity("IfcOrganization", Name=APP[0])
    person = f.create_entity("IfcPerson", FamilyName=APP[0])
    pao = f.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=org)
    app = f.create_entity("IfcApplication", ApplicationDeveloper=org, Version=version, ApplicationFullName=APP[2], ApplicationIdentifier=APP[1])
    return f.create_entity("IfcOwnerHistory", OwningUser=pao, OwningApplication=app, ChangeAction="NOCHANGE", CreationDate=stamp)


class _Losses:
    def __init__(self):
        self.items: dict = {}

    def add(self, ifc_class: str, what: str):
        k = (ifc_class, what)
        self.items[k] = self.items.get(k, 0) + 1

    def as_list(self) -> list:
        return [{"class": c, "loss": w, "count": n} for (c, w), n in sorted(self.items.items())]


def _attr_names(f, cls: str) -> set:
    decl = ifcopenshell.ifcopenshell_wrapper.schema_by_name(f.schema_identifier).declaration_by_name(cls)
    return {a.name() for a in decl.all_attributes()}


def _enum_items(f, name: str) -> set:
    t = ifcopenshell.ifcopenshell_wrapper.schema_by_name(f.schema_identifier).declaration_by_name(name)
    return set(t.enumeration_items())


def _set_key(x):
    if not isinstance(x, ifcopenshell.entity_instance):
        return ("", str(x))
    info = x.get_info(include_identifier=False, recursive=False)
    return (x.is_a(), str(info.get("GlobalId") or ""), str(info.get("UnitType") or ""), str(info.get("Name") or ""), str(x.id()).zfill(12))


def _canonical_sets(f) -> None:
    """SET 속성(순서 없는 집합)의 원소 순서를 정한다. IFC4 투영은 세션마다 집합 순서가 달라질 수 있어(단위 집합 등)
    이전기가 재귀로 매기는 개체 번호와 파일 길이가 달라진다: 같은 모델이면 같은 IFC2x3 구조가 나오게 미리 정렬한다."""
    schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(f.schema_identifier)
    sets: dict = {}
    for e in f:
        cls = e.is_a()
        if cls not in sets:
            idx = []
            decl = schema.declaration_by_name(cls)
            for i, a in enumerate(decl.all_attributes()):
                ag = a.type_of_attribute().as_aggregation_type()
                if ag is not None and ag.type_of_aggregation_string() == "set":
                    idx.append(i)
            sets[cls] = idx
        for i in sets[cls]:
            v = e[i]
            if isinstance(v, tuple) and len(v) > 1 and all(isinstance(x, ifcopenshell.entity_instance) for x in v):
                e[i] = sorted(v, key=_set_key)


def to_ifc2x3(src: ifcopenshell.file, version: str = "0", stamp: int = 0) -> tuple[ifcopenshell.file, list]:
    """IFC4 파일 -> (IFC2x3 파일, 손실 목록). src 는 바꾸지 않는다(문자열로 복사해 작업한다)."""
    work = ifcopenshell.file.from_string(src.to_string())
    losses = _Losses()
    oh = _owner_history(work, version, stamp)
    for e in work.by_type("IfcRoot"):
        if e.OwnerHistory is None:
            e.OwnerHistory = oh
    # 손실 기록용: 원래 클래스·PredefinedType·ObjectType
    before, part = {}, {}
    for e in work.by_type("IfcObject") + work.by_type("IfcTypeObject"):
        before[e.GlobalId] = (e.is_a(), getattr(e, "PredefinedType", None), getattr(e, "ObjectType", None))
        part[e.GlobalId] = getattr(e, "PartitioningType", None)
    _canonical_sets(work)
    out = ifcopenshell.file(schema="IFC2X3")
    m = US.Migrator()
    m.class_4_to_2x3 = {**m.class_4_to_2x3, **CLASS_MAP}
    m.preprocess(work, out)
    with contextlib.redirect_stdout(io.StringIO()):   # 이전기의 진단 출력(IFC2x3 에만 있는 선택 속성을 못 채웠다 등)은 손실이 아니다
        for e in work:
            m.migrate(e, out)
    amap = m.attributes_mapping[("IFC4", "IFC2X3")]
    for e in work:   # 값이 있는데 IFC2x3 클래스에 같은 이름(또는 이전기 대응표의 이름)이 없는 속성 = 손실
        nid = m.migrated_ids.get(e.id())
        if nid is None:
            continue
        ne = out.by_id(nid)
        tgt = _attr_names(out, ne.is_a())
        mapped = set(amap.get(ne.is_a(), {}).keys()) | set(amap.get(ne.is_a(), {}).values())
        for k, v in e.get_info(include_identifier=False, recursive=False).items():
            if k == "PartitioningType" and ne.is_a() == "IfcWindowStyle":   # IFC2x3 IfcWindowStyle.OperationType 이 같은 열거값을 담는다(아래에서 옮긴다)
                continue
            if k in ("type", "PredefinedType") or v is None or v == () or k in tgt or k in mapped:
                continue
            losses.add(e.is_a(), f"속성 {k}")
    for e in out.by_type("IfcObject") + out.by_type("IfcTypeObject"):
        b = before.get(e.GlobalId)
        if b is None:
            continue
        cls0, pre, otype = b
        new_cls = e.is_a()
        attrs = _attr_names(out, new_cls)
        note = []
        if new_cls != cls0:
            note.append(cls0)
            losses.add(cls0, f"클래스 -> {new_cls}")
        # PredefinedType 이 IFC2x3 의 어떤 속성(ShapeType·InteriorOrExteriorSpace·OperationType 등)에 남았으면 손실이 아니다
        if pre is not None and pre not in e.get_info(include_identifier=False, recursive=False).values():
            if pre not in ("NOTDEFINED", "USERDEFINED"):
                note.append(pre)
            losses.add(cls0, "PredefinedType" if "PredefinedType" not in attrs else f"PredefinedType {pre}(IFC2x3 열거에 없음)")
        if note and "ObjectType" in attrs:   # 원래 분류를 ObjectType 에 남긴다(이미 값이 있으면 그 뒤에)
            tag = ".".join(note)
            e.ObjectType = f"{otype}:{tag}" if otype and otype != tag else tag
        elif note and "ElementType" in attrs and not getattr(e, "ElementType", None):
            e.ElementType = ".".join(note)
        if cls0 == "IfcRoofType" and new_cls == "IfcSlabType":   # 지붕 유형(W2 여덟 번째 PR): 지붕 판 유형으로 적는다
            e.PredefinedType = "ROOF"
        if new_cls in ("IfcDoorStyle", "IfcWindowStyle"):   # IFC2x3 필수 속성
            if e.ConstructionType is None:
                e.ConstructionType = "NOTDEFINED"
            if e.Sizeable is None:
                e.Sizeable = False
            if e.OperationType is None:
                pt = part.get(e.GlobalId)
                ok = new_cls == "IfcWindowStyle" and pt in _enum_items(out, "IfcWindowStyleOperationEnum")
                if new_cls == "IfcWindowStyle" and pt is not None and not ok:
                    losses.add(cls0, f"PartitioningType {pt}(IFC2x3 열거에 없음)")
                e.OperationType = pt if ok else "NOTDEFINED"
            if e.ParameterTakesPrecedence is None:
                e.ParameterTakesPrecedence = False
    # IFC2x3 의 IfcStyledItem.Styles 는 스타일 묶음(IfcPresentationStyleAssignment)의 집합이다(IFC4 는 스타일을 바로 담는다). 이전기가 그대로 옮긴
    # 스타일을 묶음 하나로 싼다(재료 겉모습 18단계·재료 외관 W2 다섯 번째 PR: 이것이 없으면 IFC2x3 스키마 검증 오류)
    for si in sorted(out.by_type("IfcStyledItem"), key=lambda x: x.id()):
        sts = list(si.Styles or ())
        if sts and not all(s.is_a("IfcPresentationStyleAssignment") for s in sts):
            keep = [s for s in sts if s.is_a("IfcPresentationStyleAssignment")]
            si.Styles = keep + [out.createIfcPresentationStyleAssignment([s for s in sts if not s.is_a("IfcPresentationStyleAssignment")])]
    # IfcShapeRepresentationTypes 규칙: IfcSweptDiskSolid 는 'SweptSolid' 가 아니라 'AdvancedSweptSolid' 다(IFC4 도 같다. 투영은 W2 부터
    # 처음부터 AdvancedSweptSolid 로 쓰고, 여기서는 그 전에 내보낸 파일을 가져온 사본 등에 남은 'SweptSolid' 를 고친다)
    for r in out.by_type("IfcShapeRepresentation"):
        if r.RepresentationType == "SweptSolid" and any(i.is_a("IfcSweptDiskSolid") for i in r.Items):
            r.RepresentationType = "AdvancedSweptSolid"
    return out, losses.as_list()


def ifc4_only_geometry(src: ifcopenshell.file) -> list:
    """IFC2x3 에 없는 형상 클래스(IfcTriangulatedFaceSet 등)를 쓰는 개체 클래스 목록. 지금 투영은 쓰지 않는다(시험이 지킨다)."""
    bad = US.geometry_classes_introduced_after("IFC2X3", "IFC4")
    return sorted({g.is_a() for g in src.by_type("IfcRepresentationItem") if g.is_a() in bad})


def to_ifc4(src: ifcopenshell.file) -> tuple[ifcopenshell.file, list]:
    """IFC2x3 파일 -> (IFC4 파일, 경고 목록). 가져오기(foreign)가 IFC4 만 읽으므로 먼저 올린다."""
    out = ifcopenshell.file(schema="IFC4")
    m = US.Migrator()
    m.preprocess(src, out)
    log = io.StringIO()
    warns: dict = {}
    with contextlib.redirect_stdout(log):
        for e in src:
            try:
                m.migrate(e, out)
            except Exception as ex:   # 올릴 수 없는 개체는 건너뛰고 경고한다
                k = f"{e.is_a()} 을(를) IFC4 로 올리지 못했다: {type(ex).__name__}"
                warns[k] = warns.get(k, 0) + 1
    return out, [f"{k} ({n}개)" for k, n in sorted(warns.items())]
