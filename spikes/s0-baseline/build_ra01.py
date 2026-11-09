"""RA-01 표본 건물 생성기 (S0 1단계).

docs/roadmap.md 3절의 사양 표를 ifcopenshell.api 로 그대로 만든다.
- 결정적: GlobalId 를 호출 순서 기반 UUIDv5 로 고정하고, 헤더 시각을 고정한다.
- 산출물: RA-01.ifc, RA-01.golden.json (이 스크립트와 같은 폴더)
- 단위: 미터. 좌표는 층(IfcBuildingStorey) 기준 상대 배치.

지오메트리는 스파이크용으로 단순화했다 (간섭 없는 설계가 아님):
- 외벽 4면 x 2층, 두께 0.32 (콘크리트 200 + 단열 100 + 마감 20), 중심선이 12m x 8m 사각형.
- 내벽 3개 x 2층, 두께 0.10.  슬래브 3, 기둥 12(내측 모서리/중간), 보 8(2층 바닥 둘레).
- 계단 1(직선 18단), 공간 6(층당 3), 공간 경계 포함.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import uuid
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.feature
import ifcopenshell.api.geometry
import ifcopenshell.api.material
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.type
import ifcopenshell.api.unit
import ifcopenshell.guid
import ifcopenshell.util.element

HERE = Path(__file__).resolve().parent
OUT_IFC = HERE / "RA-01.ifc"
OUT_GOLDEN = HERE / "RA-01.golden.json"

STOREY_H = 3.3
EXT_T = 0.32
INT_T = 0.10
SLAB_T = 0.2
COL = 0.4
BEAM_W, BEAM_H = 0.3, 0.4
LX, LY = 12.0, 8.0

# 로드맵 3절 사양 표의 수량 (생성 후 검증에 사용)
SPEC_COUNTS = {
    "IfcSite": 1, "IfcBuilding": 1, "IfcBuildingStorey": 2,
    "IfcWall": 14, "IfcSlab": 3, "IfcColumn": 12, "IfcBeam": 8,
    "IfcDoor": 5, "IfcWindow": 8, "IfcStairFlight": 1, "IfcSpace": 6,
    "IfcWallType": 2, "IfcDoorType": 2, "IfcWindowType": 2,
}


# ---------------------------------------------------------------- 결정적 GlobalId
_NS = uuid.UUID("5d0b1a50-0000-4a00-8000-0d0b1a5000a1")
_counter = {"n": 0}


def _det_guid() -> str:
    _counter["n"] += 1
    u = uuid.uuid5(_NS, f"RA-01/{_counter['n']}")
    return ifcopenshell.guid.compress(u.hex)


# ---------------------------------------------------------------- 지오메트리 도우미
def pt(f, *c):
    return f.createIfcCartesianPoint([float(v) for v in c])


def dr(f, *c):
    return f.createIfcDirection([float(v) for v in c])


def axis3(f, origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0)):
    return f.createIfcAxis2Placement3D(pt(f, *origin), dr(f, *z), dr(f, *x))


def placement(f, rel_to, origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0)):
    return f.createIfcLocalPlacement(rel_to, axis3(f, origin, z, x))


def rect_profile(f, xdim, ydim, cx=0.0, cy=0.0):
    pos = f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([float(cx), float(cy)]),
                                      f.createIfcDirection([1.0, 0.0]))
    return f.createIfcRectangleProfileDef("AREA", None, pos, float(xdim), float(ydim))


def poly_profile(f, pts2d):
    pl = f.createIfcPolyline([f.createIfcCartesianPoint([float(x), float(y)])
                              for x, y in list(pts2d) + [pts2d[0]]])
    return f.createIfcArbitraryClosedProfileDef("AREA", None, pl)


def extrude(f, profile, depth, origin=(0, 0, 0)):
    return f.createIfcExtrudedAreaSolid(profile, axis3(f, origin), dr(f, 0, 0, 1), float(depth))


def set_body(f, ctx, product, solid):
    rep = f.createIfcShapeRepresentation(ctx, "Body", "SweptSolid", [solid])
    ifcopenshell.api.geometry.assign_representation(f, product=product, representation=rep)


# ---------------------------------------------------------------- 메인
def build() -> tuple[ifcopenshell.file, dict]:
    _counter["n"] = 0
    ifcopenshell.guid.new = _det_guid  # type: ignore[assignment]
    api = ifcopenshell.api

    f = api.project.create_file(version="IFC4")
    project = api.root.create_entity(f, ifc_class="IfcProject", name="RA-01")
    api.unit.assign_unit(f, length={"is_metric": True, "raw": "METERS"})  # 기본값은 mm 이므로 m 를 명시
    model = api.context.add_context(f, context_type="Model")
    body = api.context.add_context(f, context_type="Model", context_identifier="Body",
                                   target_view="MODEL_VIEW", parent=model)

    site = api.root.create_entity(f, ifc_class="IfcSite", name="Site")
    bldg = api.root.create_entity(f, ifc_class="IfcBuilding", name="RA-01")
    st1 = api.root.create_entity(f, ifc_class="IfcBuildingStorey", name="1F")
    st2 = api.root.create_entity(f, ifc_class="IfcBuildingStorey", name="2F")
    st1.Elevation, st2.Elevation = 0.0, STOREY_H
    site.ObjectPlacement = placement(f, None)
    bldg.ObjectPlacement = placement(f, site.ObjectPlacement)
    st1.ObjectPlacement = placement(f, bldg.ObjectPlacement, (0, 0, 0))
    st2.ObjectPlacement = placement(f, bldg.ObjectPlacement, (0, 0, STOREY_H))
    api.aggregate.assign_object(f, products=[site], relating_object=project)
    api.aggregate.assign_object(f, products=[bldg], relating_object=site)
    api.aggregate.assign_object(f, products=[st1, st2], relating_object=bldg)
    storeys = {"1F": st1, "2F": st2}

    # ---- 재료와 타입
    concrete = api.material.add_material(f, name="Concrete", category="concrete")
    insul = api.material.add_material(f, name="Insulation", category="insulation")
    finish = api.material.add_material(f, name="Finish", category="plaster")
    gypsum = api.material.add_material(f, name="Gypsum", category="gypsum")

    ext_set = api.material.add_material_set(f, name="EXT-320", set_type="IfcMaterialLayerSet")
    for m, t in ((finish, 0.02), (insul, 0.10), (concrete, 0.20)):  # 바깥 -> 안
        lay = api.material.add_layer(f, layer_set=ext_set, material=m)
        lay.LayerThickness = t
    int_set = api.material.add_material_set(f, name="INT-100", set_type="IfcMaterialLayerSet")
    lay = api.material.add_layer(f, layer_set=int_set, material=gypsum)
    lay.LayerThickness = INT_T

    wt_ext = api.root.create_entity(f, ifc_class="IfcWallType", predefined_type="STANDARD", name="WT-EXT-320")
    wt_int = api.root.create_entity(f, ifc_class="IfcWallType", predefined_type="PARTITIONING", name="WT-INT-100")
    api.material.assign_material(f, products=[wt_ext], type="IfcMaterialLayerSet", material=ext_set)
    api.material.assign_material(f, products=[wt_int], type="IfcMaterialLayerSet", material=int_set)

    def _type_dims(t, w, h):
        # IFC4 의 IfcDoorType/IfcWindowType 에는 폭·높이 속성이 없다 (IFC2x3 의 *Style 에만 있음).
        # 타입 수준 치수는 커스텀 Pset 으로 둔다. -> E2(타입 폭 변경)의 근거가 도구 정의에 의존함 (보고서 발견).
        ps = api.pset.add_pset(f, product=t, name="DoBIM_TypeParams")
        api.pset.edit_pset(f, pset=ps, properties={"OverallWidth": w, "OverallHeight": h})

    dt = {}
    for nm, w in (("DT-01", 0.9), ("DT-02", 0.8)):
        t = api.root.create_entity(f, ifc_class="IfcDoorType", predefined_type="DOOR", name=nm)
        t.OperationType = "SINGLE_SWING_LEFT"
        _type_dims(t, w, 2.1)
        dt[nm] = t
    wtp = {}
    for nm, w in (("WT-01", 1.2), ("WT-02", 1.8)):
        t = api.root.create_entity(f, ifc_class="IfcWindowType", predefined_type="WINDOW", name=nm)
        t.PartitioningType = "SINGLE_PANEL"
        _type_dims(t, w, 1.2)
        wtp[nm] = t

    elements: dict[str, dict] = {}  # name -> 메타 (해석적 체적 등)

    def intent(obj: dict) -> str:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def psets(prod, common_name: str, props: dict, intent_str: str | None = None):
        ps = api.pset.add_pset(f, product=prod, name=common_name)
        api.pset.edit_pset(f, pset=ps, properties=props)
        if intent_str is not None:
            ps2 = api.pset.add_pset(f, product=prod, name="DoBIM_Intent")
            api.pset.edit_pset(f, pset=ps2, properties={"Intent": intent_str})

    def contain(prod, storey_name):
        api.spatial.assign_container(f, products=[prod], relating_structure=storeys[storey_name])

    # ---- 벽
    walls: dict[str, dict] = {}

    def make_wall(name, storey, p0, p1, thick, wtype, mat_set, external, fire):
        L = math.dist(p0, p1)
        d = ((p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L)
        w = api.root.create_entity(f, ifc_class="IfcWall", predefined_type="STANDARD", name=name)
        w.ObjectPlacement = placement(f, storeys[storey].ObjectPlacement, (p0[0], p0[1], 0), (0, 0, 1), (d[0], d[1], 0))
        set_body(f, body, w, extrude(f, rect_profile(f, L, thick, L / 2, 0), STOREY_H))
        api.type.assign_type(f, related_objects=[w], relating_type=wtype, should_map_representations=False)
        api.material.assign_material(f, products=[w], type="IfcMaterialLayerSetUsage", material=mat_set)
        usage = ifcopenshell.util.element.get_material(w, should_skip_usage=False)
        usage.OffsetFromReferenceLine = -thick / 2  # 축이 두께 중앙
        contain(w, storey)
        psets(w, "Pset_WallCommon",
              {"FireRating": fire, "IsExternal": external, "LoadBearing": external, "Reference": name},
              intent({"kind": "wall", "axis": [list(p0), list(p1)], "thickness": thick,
                      "storey": storey, "height": STOREY_H, "role": "exterior" if external else "interior"}))
        walls[name] = {"el": w, "L": L, "t": thick, "cut": 0.0, "open": []}
        elements[name] = {"class": "IfcWall"}
        return w

    n = 0
    for sn in ("1F", "2F"):
        for (a, b) in (((0, 0), (LX, 0)), ((LX, 0), (LX, LY)), ((LX, LY), (0, LY)), ((0, LY), (0, 0))):
            n += 1
            make_wall(f"W-E-{n:02d}", sn, a, b, EXT_T, wt_ext, ext_set, True, "120min")
    n = 0
    hi = EXT_T / 2
    for sn in ("1F", "2F"):
        for (a, b) in (((4.0, hi), (4.0, LY - hi)), ((4.0 + INT_T / 2, 4.0), (LX - hi, 4.0)), ((8.0, hi), (8.0, 2.0))):
            n += 1
            make_wall(f"W-I-{n:02d}", sn, a, b, INT_T, wt_int, int_set, False, "60min")

    # ---- 개구부 + 문/창
    def make_opening(host_name, name, storey, u, w, h, sill, kind, tname):
        host = walls[host_name]
        wall = host["el"]
        op = api.root.create_entity(f, ifc_class="IfcOpeningElement", predefined_type="OPENING", name=f"OP-{name}")
        op.ObjectPlacement = placement(f, wall.ObjectPlacement, (u, 0, sill))
        set_body(f, body, op, extrude(f, rect_profile(f, w, host["t"] + 0.1), h))
        api.feature.add_feature(f, feature=op, element=wall)
        cls = "IfcDoor" if kind == "door" else "IfcWindow"
        el = api.root.create_entity(f, ifc_class=cls, predefined_type="DOOR" if kind == "door" else "WINDOW", name=name)
        el.OverallWidth, el.OverallHeight = w, h
        el.ObjectPlacement = placement(f, wall.ObjectPlacement, (u, 0, sill))
        set_body(f, body, el, extrude(f, rect_profile(f, w, 0.05), h))
        api.feature.add_filling(f, opening=op, element=el)
        t = dt[tname] if kind == "door" else wtp[tname]
        api.type.assign_type(f, related_objects=[el], relating_type=t, should_map_representations=False)
        contain(el, storey)
        ext = host["t"] == EXT_T
        if kind == "door":
            psets(el, "Pset_DoorCommon", {"FireRating": "60min" if ext else "30min", "IsExternal": ext, "Reference": name},
                  intent({"kind": "door", "host": host_name, "offset": u, "width": w, "height": h, "type": tname}))
        else:
            psets(el, "Pset_WindowCommon", {"FireRating": "0min", "IsExternal": True, "Reference": name,
                                            "ThermalTransmittance": 1.4})
        host["cut"] += w * h * host["t"]
        elements[name] = {"class": cls}
        return el

    # 문 5: 외부 2 (1F 남벽, 2F 남벽), 내부 3
    make_opening("W-E-01", "D-01", "1F", 8.0, 0.9, 2.1, 0.0, "door", "DT-01")
    make_opening("W-I-01", "D-02", "1F", 1.84, 0.8, 2.1, 0.0, "door", "DT-02")
    make_opening("W-I-02", "D-03", "1F", 2.95, 0.8, 2.1, 0.0, "door", "DT-02")
    make_opening("W-I-04", "D-04", "2F", 1.84, 0.8, 2.1, 0.0, "door", "DT-02")
    make_opening("W-E-05", "D-05", "2F", 8.0, 0.9, 2.1, 0.0, "door", "DT-01")
    # 창 8: 층당 남벽 2(WT-01) + 북벽 2(WT-02)
    k = 0
    for sn, south, north in (("1F", "W-E-01", "W-E-03"), ("2F", "W-E-05", "W-E-07")):
        for u in (2.5, 5.0):
            k += 1
            make_opening(south, f"WIN-{k:02d}", sn, u, 1.2, 1.2, 0.9, "window", "WT-01")
        for u in (3.0, 9.0):
            k += 1
            make_opening(north, f"WIN-{k:02d}", sn, u, 1.8, 1.2, 0.9, "window", "WT-02")

    for name, wd in walls.items():
        elements[name]["volume"] = wd["L"] * wd["t"] * STOREY_H - wd["cut"]

    # ---- 슬래브
    def make_slab(name, storey, z, x0, y0, x1, y1, ptype):
        s = api.root.create_entity(f, ifc_class="IfcSlab", predefined_type=ptype, name=name)
        s.ObjectPlacement = placement(f, storeys[storey].ObjectPlacement, (0, 0, z))
        set_body(f, body, s, extrude(f, poly_profile(f, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]), SLAB_T))
        api.material.assign_material(f, products=[s], type="IfcMaterial", material=concrete)
        contain(s, storey)
        psets(s, "Pset_SlabCommon", {"IsExternal": ptype == "ROOF", "Reference": name, "FireRating": "120min"})
        elements[name] = {"class": "IfcSlab", "volume": (x1 - x0) * (y1 - y0) * SLAB_T}
        return s

    make_slab("S-01", "1F", -SLAB_T, -hi, -hi, LX + hi, LY + hi, "BASESLAB")
    make_slab("S-02", "2F", -SLAB_T, hi, hi, LX - hi, LY - hi, "FLOOR")
    make_slab("S-03", "2F", STOREY_H - SLAB_T, hi, hi, LX - hi, LY - hi, "ROOF")

    # ---- 기둥 12
    k = 0
    cxs, cys = (hi, LX / 2, LX - hi), (hi, LY - hi)
    for sn in ("1F", "2F"):
        for cx in cxs:
            for cy in cys:
                k += 1
                c = api.root.create_entity(f, ifc_class="IfcColumn", predefined_type="COLUMN", name=f"C-{k:02d}")
                c.ObjectPlacement = placement(f, storeys[sn].ObjectPlacement, (cx, cy, 0))
                set_body(f, body, c, extrude(f, rect_profile(f, COL, COL), STOREY_H))
                api.material.assign_material(f, products=[c], type="IfcMaterial", material=concrete)
                contain(c, sn)
                psets(c, "Pset_ColumnCommon", {"Reference": f"C-{k:02d}", "LoadBearing": True, "IsExternal": False})
                elements[f"C-{k:02d}"] = {"class": "IfcColumn", "volume": COL * COL * STOREY_H}

    # ---- 보 8 (2층 바닥 둘레, 기둥 중심선 사이)
    bl = [((hi, hi), (LX / 2, hi)), ((LX / 2, hi), (LX - hi, hi)),
          ((hi, LY - hi), (LX / 2, LY - hi)), ((LX / 2, LY - hi), (LX - hi, LY - hi)),
          ((LX - hi, hi), (LX - hi, LY / 2)), ((LX - hi, LY / 2), (LX - hi, LY - hi)),
          ((hi, hi), (hi, LY / 2)), ((hi, LY / 2), (hi, LY - hi))]
    for i, (a, b) in enumerate(bl, 1):
        L = math.dist(a, b)
        d = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        nrm = (-d[1], d[0], 0)  # 국소 X (수평 직각). 국소 Z = 보 방향, Y = Z x X = 연직
        bm = api.root.create_entity(f, ifc_class="IfcBeam", predefined_type="BEAM", name=f"B-{i:02d}")
        bm.ObjectPlacement = placement(f, st2.ObjectPlacement, (a[0], a[1], -BEAM_H / 2), (d[0], d[1], 0), nrm)
        set_body(f, body, bm, extrude(f, rect_profile(f, BEAM_W, BEAM_H), L))
        api.material.assign_material(f, products=[bm], type="IfcMaterial", material=concrete)
        contain(bm, "2F")
        psets(bm, "Pset_BeamCommon", {"Reference": f"B-{i:02d}", "LoadBearing": True, "IsExternal": False})
        elements[f"B-{i:02d}"] = {"class": "IfcBeam", "volume": BEAM_W * BEAM_H * L}

    # ---- 계단 (1F -> 2F, +Y 방향, 18단)
    NR = 18
    R, T = STOREY_H / NR, 0.27
    pts = [(0.0, 0.0)]
    for i in range(NR):
        pts += [(i * T, (i + 1) * R), ((i + 1) * T, (i + 1) * R)]
    pts += [(NR * T, STOREY_H - 0.25)]
    sf = api.root.create_entity(f, ifc_class="IfcStairFlight", predefined_type="STRAIGHT", name="ST-01")
    sf.NumberOfRisers, sf.NumberOfTreads, sf.RiserHeight, sf.TreadLength = NR, NR - 1, R, T
    sf.ObjectPlacement = placement(f, st1.ObjectPlacement, (1.0, 1.0, 0), (1, 0, 0), (0, 1, 0))
    prof = poly_profile(f, pts)
    set_body(f, body, sf, extrude(f, prof, 1.2))
    api.material.assign_material(f, products=[sf], type="IfcMaterial", material=concrete)
    contain(sf, "1F")
    psets(sf, "Pset_StairFlightCommon", {"Reference": "ST-01", "NumberOfRiser": NR, "NumberOfTreads": NR - 1})
    # 계단 면적 (shoelace) x 폭 = 해석적 체적
    ar = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        ar += x0 * y1 - x1 * y0
    elements["ST-01"] = {"class": "IfcStairFlight", "volume": abs(ar) / 2 * 1.2}

    # ---- 공간 6 + 공간 경계
    rooms = [("1F", "SP-101", "Lobby", (hi, hi, 4.0 - INT_T / 2, LY - hi), ["W-E-04", "W-E-01", "W-E-03", "W-I-01"]),
             ("1F", "SP-102", "Office A", (4.0 + INT_T / 2, hi, LX - hi, 4.0 - INT_T / 2), ["W-E-01", "W-E-02", "W-I-01", "W-I-02", "W-I-03"]),
             ("1F", "SP-103", "Office B", (4.0 + INT_T / 2, 4.0 + INT_T / 2, LX - hi, LY - hi), ["W-E-03", "W-E-02", "W-I-01", "W-I-02"]),
             ("2F", "SP-201", "Lounge", (hi, hi, 4.0 - INT_T / 2, LY - hi), ["W-E-08", "W-E-05", "W-E-07", "W-I-04"]),
             ("2F", "SP-202", "Meeting A", (4.0 + INT_T / 2, hi, LX - hi, 4.0 - INT_T / 2), ["W-E-05", "W-E-06", "W-I-04", "W-I-05", "W-I-06"]),
             ("2F", "SP-203", "Meeting B", (4.0 + INT_T / 2, 4.0 + INT_T / 2, LX - hi, LY - hi), ["W-E-07", "W-E-06", "W-I-04", "W-I-05"])]
    slabs = {"1F": "S-01", "2F": "S-02"}
    for sn, nm, lname, (x0, y0, x1, y1), bnd in rooms:
        sp = api.root.create_entity(f, ifc_class="IfcSpace", predefined_type="INTERNAL", name=nm)
        sp.LongName = lname
        sp.ObjectPlacement = placement(f, storeys[sn].ObjectPlacement, (x0, y0, 0))
        set_body(f, body, sp, extrude(f, rect_profile(f, x1 - x0, y1 - y0, (x1 - x0) / 2, (y1 - y0) / 2), STOREY_H))
        api.aggregate.assign_object(f, products=[sp], relating_object=storeys[sn])
        psets(sp, "Pset_SpaceCommon", {"Reference": nm, "Category": "office", "IsExternal": False})
        elements[nm] = {"class": "IfcSpace", "volume": (x1 - x0) * (y1 - y0) * STOREY_H}
        for bn in bnd + [slabs[sn]]:
            el = walls[bn]["el"] if bn in walls else next(e for e in f.by_type("IfcSlab") if e.Name == bn)
            f.create_entity("IfcRelSpaceBoundary", GlobalId=ifcopenshell.guid.new(),
                            RelatingSpace=sp, RelatedBuildingElement=el,
                            PhysicalOrVirtualBoundary="PHYSICAL", InternalOrExternalBoundary="INTERNAL")

    # ---- 헤더 고정
    h = f.header
    h.file_name.name = "RA-01.ifc"
    h.file_name.time_stamp = "2026-11-07T00:00:00"
    h.file_name.author = ["S0"]
    h.file_name.organization = ["Democratization-of-BIM"]
    h.file_name.originating_system = "build_ra01.py"
    h.file_name.authorization = "none"
    return f, elements


def check_spec(f: ifcopenshell.file) -> None:
    """사양 표 수량과 일치하는지 확인한다 (불일치 시 즉시 실패)."""
    bad = []
    for cls, want in SPEC_COUNTS.items():
        got = len(f.by_type(cls, include_subtypes=False))
        if got != want:
            bad.append(f"{cls}: 기대 {want}, 실제 {got}")
    ext = sum(1 for w in f.by_type("IfcWall") if (ifcopenshell.util.element.get_psets(w).get("Pset_WallCommon") or {}).get("IsExternal"))
    if ext != 8:
        bad.append(f"외벽 기대 8, 실제 {ext}")
    if len(f.by_type("IfcRelVoidsElement")) != 13 or len(f.by_type("IfcRelFillsElement")) != 13:
        bad.append("개구부 관계 13쌍이 아님")
    gids = [e.GlobalId for e in f.by_type("IfcRoot")]
    if len(gids) != len(set(gids)):
        bad.append("GlobalId 중복")
    if bad:
        raise SystemExit("사양 불일치:\n  " + "\n  ".join(bad))


def make_golden(f: ifcopenshell.file, elements: dict, ifc_sha: str) -> dict:
    """골든 값: 요소별 GlobalId, 클래스, 표준 Pset, 층 구성, 의도, 해석적 체적, 관계."""
    gold_el = {}
    for e in f.by_type("IfcProduct"):
        ps = ifcopenshell.util.element.get_psets(e, psets_only=True)
        ps = {k: {pk: pv for pk, pv in v.items() if pk != "id"} for k, v in ps.items()}
        mat = ifcopenshell.util.element.get_material(e, should_skip_usage=False)
        layers = None
        if mat is not None:
            s = mat.ForLayerSet if mat.is_a("IfcMaterialLayerSetUsage") else mat
            if s.is_a("IfcMaterialLayerSet"):
                layers = [[ly.Material.Name, ly.LayerThickness] for ly in s.MaterialLayers]
        et = ifcopenshell.util.element.get_type(e)
        gold_el[e.GlobalId] = {
            "class": e.is_a(), "name": e.Name, "type": et.Name if et else None,
            "psets": ps, "layers": layers,
            "intent": (ps.get("DoBIM_Intent") or {}).get("Intent"),
            "volume_analytic": (elements.get(e.Name) or {}).get("volume") if e.is_a() != "IfcOpeningElement" else None,
        }
    rels = {
        "voids": sorted([r.RelatingBuildingElement.GlobalId, r.RelatedOpeningElement.GlobalId] for r in f.by_type("IfcRelVoidsElement")),
        "fills": sorted([r.RelatingOpeningElement.GlobalId, r.RelatedBuildingElement.GlobalId] for r in f.by_type("IfcRelFillsElement")),
    }
    counts: dict[str, int] = {}
    for e in f.by_type("IfcProduct"):
        counts[e.is_a()] = counts.get(e.is_a(), 0) + 1
    return {
        "meta": {"name": "RA-01", "schema": "IFC4", "unit": "m", "generator": "build_ra01.py",
                 "ifcopenshell": ifcopenshell.version, "ifc_sha256": ifc_sha,
                 "note": "volume_analytic 은 생성 파라미터에서 직접 계산한 값(지오메트리 엔진과 독립)"},
        "spec_counts": SPEC_COUNTS, "class_counts": dict(sorted(counts.items())),
        "elements": gold_el, "relations": rels,
    }


def run(out_ifc: Path, out_golden: Path | None) -> str:
    f, elements = build()
    check_spec(f)
    f.write(str(out_ifc))
    sha = hashlib.sha256(out_ifc.read_bytes()).hexdigest()
    if out_golden is not None:
        # 골든은 방금 쓴 파일을 다시 읽어 만든다 (파일에 실제로 들어간 값 기준)
        f2 = ifcopenshell.open(str(out_ifc))
        g = make_golden(f2, elements, sha)
        out_golden.write_text(json.dumps(g, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return sha


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_IFC
    gold = None if len(sys.argv) > 1 else OUT_GOLDEN
    print(run(out, gold))
