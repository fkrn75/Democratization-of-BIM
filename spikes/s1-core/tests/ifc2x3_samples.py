# SPDX-License-Identifier: MPL-2.0
"""IFC2x3 TC1 가져오기 시험용 표본 생성기(W1 1-F 5차, IO-03). 외부 파일·현장 자료 없이 이 코드가 직접 만든다.

DoBIM 내보내기를 거치지 않은, 다른 저작 도구가 흔히 쓰는 IFC2x3 꼴로 쓴다.
- IfcWallStandardCase + IfcMaterialLayerSetUsage, 축(Axis) 표현과 몸체(SweptSolid) 표현
- 개구부(IfcOpeningElement + IfcRelVoidsElement)를 채우는 IfcDoor(IfcDoorStyle)·IfcWindow(IfcWindowStyle) - 2x3 전용 스타일 클래스
- IfcSlab, IfcColumn, IfcSpace(이름·긴 이름), IfcFurnishingElement, IfcFlowTerminal + IfcSanitaryTerminalType(2x3 의 설비 꼴)
- IfcOwnerHistory 필수(2x3), 층 2개, 속성셋(Pset_WallCommon)
길이 단위는 mm 또는 m(scale 로 모든 길이를 곱한다). 같은 건물을 두 단위로 써서 단위 환산을 시험한다. GlobalId 는 이름에서 결정적으로 만든다."""
from __future__ import annotations

import uuid

import ifcopenshell
import ifcopenshell.guid

NS = uuid.UUID("5d0b1a50-0000-4a00-8000-0d0b1a523203")


def gid(name: str) -> str:
    return ifcopenshell.guid.compress(uuid.uuid5(NS, name).hex)


class _B:
    def __init__(self, unit: str):
        assert unit in ("mm", "m")
        self.s = 1.0 if unit == "mm" else 0.001     # mm 수치에 곱해 파일 단위로
        self.f = ifcopenshell.file(schema="IFC2X3")
        f = self.f
        person = f.create_entity("IfcPerson", Id="t", FamilyName="Sample")
        org = f.create_entity("IfcOrganization", Name="Sample Org")
        po = f.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=org)
        app = f.create_entity("IfcApplication", ApplicationDeveloper=org, Version="1", ApplicationFullName="Synthetic 2x3 writer", ApplicationIdentifier="synth2x3")
        self.oh = f.create_entity("IfcOwnerHistory", OwningUser=po, OwningApplication=app, ChangeAction="ADDED", CreationDate=1700000000)
        self.origin = self.p3(0, 0, 0)
        self.z = f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
        self.x = f.create_entity("IfcDirection", DirectionRatios=(1.0, 0.0, 0.0))
        self.ctx = f.create_entity("IfcGeometricRepresentationContext", ContextIdentifier="Model", ContextType="Model", CoordinateSpaceDimension=3,
                                   Precision=1e-5, WorldCoordinateSystem=f.create_entity("IfcAxis2Placement3D", Location=self.origin))
        if unit == "mm":
            lu = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE")
        else:
            lu = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
        units = f.create_entity("IfcUnitAssignment", Units=[lu, f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE"),
                                                             f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE"),
                                                             f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")])
        self.project = f.create_entity("IfcProject", GlobalId=gid("project"), OwnerHistory=self.oh, Name="2x3 표본",
                                       RepresentationContexts=[self.ctx], UnitsInContext=units)

    # ---- 기본 도구
    def L(self, v):
        return float(v) * self.s

    def p3(self, x, y, z):
        return self.f.create_entity("IfcCartesianPoint", Coordinates=(float(x), float(y), float(z)))

    def place(self, rel, x=0, y=0, z=0, xdir=(1.0, 0.0, 0.0)):
        ax = self.f.create_entity("IfcAxis2Placement3D", Location=self.p3(self.L(x), self.L(y), self.L(z)), Axis=self.z,
                                  RefDirection=self.f.create_entity("IfcDirection", DirectionRatios=tuple(float(c) for c in xdir)))
        return self.f.create_entity("IfcLocalPlacement", PlacementRelTo=rel, RelativePlacement=ax)

    def box(self, x0, y0, z0, dx, dy, dz):
        """국소 좌표의 축 정렬 상자(직사각형 단면을 z 로 밀어낸 것)."""
        f = self.f
        prof = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=self.L(dx), YDim=self.L(dy),
                               Position=f.create_entity("IfcAxis2Placement2D", Location=f.create_entity("IfcCartesianPoint", Coordinates=(self.L(x0 + dx / 2), self.L(y0 + dy / 2)))))
        return f.create_entity("IfcExtrudedAreaSolid", SweptArea=prof, Position=f.create_entity("IfcAxis2Placement3D", Location=self.p3(0, 0, self.L(z0))),
                               ExtrudedDirection=self.z, Depth=self.L(dz))

    def shape(self, items, rtype="SweptSolid", ident="Body", extra=()):
        f = self.f
        reps = [f.create_entity("IfcShapeRepresentation", ContextOfItems=self.ctx, RepresentationIdentifier=ident, RepresentationType=rtype, Items=items)]
        return f.create_entity("IfcProductDefinitionShape", Representations=list(extra) + reps)

    def product(self, cls, name, plc, rep, **kw):
        return self.f.create_entity(cls, GlobalId=gid(name), OwnerHistory=self.oh, Name=name, ObjectPlacement=plc, Representation=rep, **kw)

    def rel(self, cls, name, **kw):
        return self.f.create_entity(cls, GlobalId=gid("rel:" + name), OwnerHistory=self.oh, **kw)

    def pset(self, name, target, props: dict):
        f = self.f
        vals = []
        for k, v in props.items():
            nv = f.create_entity("IfcBoolean", v) if isinstance(v, bool) else (f.create_entity("IfcReal", v) if isinstance(v, float) else f.create_entity("IfcLabel", v))
            vals.append(f.create_entity("IfcPropertySingleValue", Name=k, NominalValue=nv))
        ps = f.create_entity("IfcPropertySet", GlobalId=gid(f"pset:{name}:{target.Name}"), OwnerHistory=self.oh, Name=name, HasProperties=vals)
        self.rel("IfcRelDefinesByProperties", f"{name}:{target.Name}", RelatedObjects=[target], RelatingPropertyDefinition=ps)


def build(unit: str = "mm") -> ifcopenshell.file:
    """6x4m 단층 방(벽 4·바닥·문·창·기둥·공간·가구·세면기) + 2층 벽 하나. 단위만 다르고 같은 건물이다."""
    b = _B(unit)
    f = b.f
    site_p = b.place(None)
    site = b.product("IfcSite", "대지", site_p, None, CompositionType="ELEMENT")
    bld_p = b.place(site_p)
    bld = b.product("IfcBuilding", "건물", bld_p, None, CompositionType="ELEMENT")
    st1_p = b.place(bld_p)
    st1 = b.product("IfcBuildingStorey", "1층", st1_p, None, CompositionType="ELEMENT", Elevation=b.L(0))
    st2_p = b.place(bld_p, z=3000)
    st2 = b.product("IfcBuildingStorey", "2층", st2_p, None, CompositionType="ELEMENT", Elevation=b.L(3000))
    b.rel("IfcRelAggregates", "p-site", RelatingObject=b.project, RelatedObjects=[site])
    b.rel("IfcRelAggregates", "site-b", RelatingObject=site, RelatedObjects=[bld])
    b.rel("IfcRelAggregates", "b-st", RelatingObject=bld, RelatedObjects=[st1, st2])

    layer = f.create_entity("IfcMaterialLayer", Material=f.create_entity("IfcMaterial", Name="콘크리트"), LayerThickness=b.L(200))
    lset = f.create_entity("IfcMaterialLayerSet", MaterialLayers=[layer], LayerSetName="벽 200")
    walls = []
    # (이름, 시작 x, y, 방향, 길이): 축선이 벽 중심, 국소 x = 축 방향, 두께는 국소 y 로 -100..100
    for name, x, y, d, ln, plc_rel, h in (("벽 남", 0, 0, (1, 0, 0), 6000, st1_p, 3000), ("벽 동", 6000, 0, (0, 1, 0), 4000, st1_p, 3000),
                                          ("벽 북", 6000, 4000, (-1, 0, 0), 6000, st1_p, 3000), ("벽 서", 0, 4000, (0, -1, 0), 4000, st1_p, 3000),
                                          ("2층 벽", 0, 0, (1, 0, 0), 6000, st2_p, 2800)):
        plc = b.place(plc_rel, x, y, 0, d)
        axis = f.create_entity("IfcPolyline", Points=[f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0)),
                                                      f.create_entity("IfcCartesianPoint", Coordinates=(b.L(ln), 0.0))])
        axis_rep = f.create_entity("IfcShapeRepresentation", ContextOfItems=b.ctx, RepresentationIdentifier="Axis", RepresentationType="Curve2D", Items=[axis])
        w = b.product("IfcWallStandardCase", name, plc, b.shape([b.box(0, -100, 0, ln, 200, h)], extra=[axis_rep]))
        b.rel("IfcRelAssociatesMaterial", "mat:" + name, RelatedObjects=[w],
              RelatingMaterial=f.create_entity("IfcMaterialLayerSetUsage", ForLayerSet=lset, LayerSetDirection="AXIS2", DirectionSense="POSITIVE",
                                               OffsetFromReferenceLine=b.L(-100)))
        b.pset("Pset_WallCommon", w, {"IsExternal": True, "LoadBearing": name != "2층 벽", "Reference": "W200"})
        walls.append(w)

    slab = b.product("IfcSlab", "바닥", b.place(st1_p), b.shape([b.box(-100, -100, -200, 6200, 4200, 200)]), PredefinedType="FLOOR")

    # 문: 남쪽 벽(국소 x 1000..1900), 창: 동쪽 벽(국소 x 1500..2700, 높이 900..2100)
    def opening(wall, name, u0, du, z0, dz):
        op = b.product("IfcOpeningElement", "개구부 " + name, b.place(wall.ObjectPlacement), b.shape([b.box(u0, -150, z0, du, 300, dz)]))
        b.rel("IfcRelVoidsElement", "void:" + name, RelatingBuildingElement=wall, RelatedOpeningElement=op)
        return op

    op_d = opening(walls[0], "문", 1000, 900, 0, 2100)
    door = b.product("IfcDoor", "현관문", b.place(op_d.ObjectPlacement), b.shape([b.box(1000, -20, 0, 900, 40, 2100)]), OverallHeight=b.L(2100), OverallWidth=b.L(900))
    b.rel("IfcRelFillsElement", "fill:문", RelatingOpeningElement=op_d, RelatedBuildingElement=door)
    dstyle = f.create_entity("IfcDoorStyle", GlobalId=gid("door style"), OwnerHistory=b.oh, Name="외여닫이 900", OperationType="SINGLE_SWING_LEFT",
                             ConstructionType="WOOD", ParameterTakesPrecedence=False, Sizeable=False)
    b.rel("IfcRelDefinesByType", "type:문", RelatedObjects=[door], RelatingType=dstyle)
    op_w = opening(walls[1], "창", 1500, 1200, 900, 1200)
    win = b.product("IfcWindow", "동쪽 창", b.place(op_w.ObjectPlacement), b.shape([b.box(1500, -30, 900, 1200, 60, 1200)]), OverallHeight=b.L(1200), OverallWidth=b.L(1200))
    b.rel("IfcRelFillsElement", "fill:창", RelatingOpeningElement=op_w, RelatedBuildingElement=win)
    wstyle = f.create_entity("IfcWindowStyle", GlobalId=gid("window style"), OwnerHistory=b.oh, Name="고정창 1200", ConstructionType="ALUMINIUM",
                             OperationType="SINGLE_PANEL", ParameterTakesPrecedence=False, Sizeable=False)
    b.rel("IfcRelDefinesByType", "type:창", RelatedObjects=[win], RelatingType=wstyle)

    col = b.product("IfcColumn", "기둥", b.place(st1_p, 3000, 2000), b.shape([b.box(-150, -150, 0, 300, 300, 3000)]))
    furn = b.product("IfcFurnishingElement", "탁자", b.place(st1_p, 1500, 2500), b.shape([b.box(-600, -400, 0, 1200, 800, 720)]))
    basin = b.product("IfcFlowTerminal", "세면기", b.place(st1_p, 5000, 3500), b.shape([b.box(-250, -200, 750, 500, 400, 150)]))
    btype = f.create_entity("IfcSanitaryTerminalType", GlobalId=gid("basin type"), OwnerHistory=b.oh, Name="세면기 500", ElementType="세면기",
                            PredefinedType="WASHHANDBASIN")
    b.rel("IfcRelDefinesByType", "type:세면기", RelatedObjects=[basin], RelatingType=btype)
    space = b.product("IfcSpace", "101", b.place(st1_p), b.shape([b.box(100, 100, 0, 5800, 3800, 2700)]), LongName="거실",
                      CompositionType="ELEMENT", InteriorOrExteriorSpace="INTERNAL")
    b.rel("IfcRelAggregates", "st-space", RelatingObject=st1, RelatedObjects=[space])
    b.rel("IfcRelContainedInSpatialStructure", "st1", RelatingStructure=st1, RelatedElements=walls[:4] + [slab, door, win, col, furn, basin])
    b.rel("IfcRelContainedInSpatialStructure", "st2", RelatingStructure=st2, RelatedElements=[walls[4]])
    return f


def write(path, unit: str = "mm") -> list[str]:
    """표본을 path 에 쓰고 IfcProduct GlobalId 목록(이름순)을 돌려준다."""
    f = build(unit)
    f.write(str(path))
    return sorted(e.GlobalId for e in f.by_type("IfcProduct"))
