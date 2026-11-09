# SPDX-License-Identifier: MPL-2.0
"""패밀리 → IFC 투영 (ADR-0008 결정 3). ifcopenshell 을 쓰므로 코어 계층에서는 ifc_model.py 와 validate.py 만 이 모듈을 부른다.

투영 규칙
- 타입 요소(`ftype`) 하나 = `IfcXxxType` 하나 + 본체 `IfcRepresentationMap` + (평면 기호가 있으면) 평면 `IfcRepresentationMap`.
  타입 형상은 인스턴스 매개변수를 기본값으로 둔 평가 결과다.
- 인스턴스(`finst`) 형상이 타입 형상과 같으면 `IfcMappedItem` 으로 타입의 표현 지도를 가리킨다.
  다르면(인스턴스 매개변수가 형상을 바꿨거나 호스트 두께가 다르면) 인스턴스가 자기 표현을 가진다(ADR-0008: "개별 표현").
- 호스트가 있는 인스턴스는 `IfcOpeningElement` 를 만들어 벽에 `IfcRelVoidsElement`, 인스턴스에 `IfcRelFillsElement` 로 잇는다.
- 평면 기호: 'Plan' 하위 문맥(FootPrint)의 `IfcGeometricCurveSet`. 선 스타일(cut/projection/hidden)과 다른 축척은 IFC 에 못 담아
  `DoBIM_Intent`(레시피 전문)에만 남는다 — 알려진 손실이다.
"""
from __future__ import annotations

import math
from typing import Any

import ifcopenshell
import ifcopenshell.guid
import ifcopenshell.api.context as api_context
import ifcopenshell.api.feature as api_feature
import ifcopenshell.api.root as api_root
import ifcopenshell.util.element as ue

_ZERO = [0.0, 0.0, 0.0]


def _pt3(f, p):
    return f.createIfcCartesianPoint([float(c) for c in p])


def _dir(f, d):
    return f.createIfcDirection([float(c) for c in d])


def _ax3(f, loc, axis, ref):
    return f.createIfcAxis2Placement3D(_pt3(f, loc), _dir(f, axis), _dir(f, ref))


def _ax2(f, c):
    return f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([float(c[0]), float(c[1])]), None)


def _profile(f, p: dict):
    s = p["shape"]
    if s == "rect":
        (u0, u1), (v0, v1) = p["u"], p["v"]
        return f.createIfcRectangleProfileDef("AREA", None, _ax2(f, ((u0 + u1) / 2, (v0 + v1) / 2)), float(u1 - u0), float(v1 - v0))
    if s == "circle":
        return f.createIfcCircleProfileDef("AREA", None, _ax2(f, p["center"]), float(p["r"]))
    pts = list(p["points"])
    pl = f.createIfcPolyline([f.createIfcCartesianPoint([float(x), float(y)]) for x, y in pts + [pts[0]]])
    return f.createIfcArbitraryClosedProfileDef("AREA", None, pl)


def _extrude_item(f, it: dict):
    a0, a1 = it["range"]
    plane = it["plane"]
    if plane == "xy":
        pos = _ax3(f, (0, 0, a0), (0, 0, 1), (1, 0, 0))
    elif plane == "xz":
        pos = _ax3(f, (0, a1, 0), (0, -1, 0), (1, 0, 0))   # 평면 법선이 -y 라 y=a1 에서 -y 로 돌출한다
    else:
        pos = _ax3(f, (a0, 0, 0), (1, 0, 0), (0, 1, 0))
    return f.createIfcExtrudedAreaSolid(_profile(f, it["profile"]), pos, _dir(f, (0, 0, 1)), float(a1 - a0))


_AX = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def _segment_item(f, it: dict):
    a, b = it["start"], it["end"]
    d = [b[i] - a[i] for i in range(3)]
    ln = math.sqrt(sum(c * c for c in d))
    u = [c / ln for c in d]
    n = _AX[it["normal"]]
    w = [u[1] * n[2] - u[2] * n[1], u[2] * n[0] - u[0] * n[2], u[0] * n[1] - u[1] * n[0]]   # 단면의 w 방향 = 법선 × 진행 방향
    wl = math.sqrt(sum(c * c for c in w))
    w = [c / wl for c in w]
    pr = it["profile"]
    if pr["shape"] == "rect":
        prof = f.createIfcRectangleProfileDef("AREA", None, _ax2(f, (0, 0)), float(pr["w"]), float(pr["h"]))
    else:
        prof = f.createIfcCircleProfileDef("AREA", None, _ax2(f, (0, 0)), float(pr["r"]))
    return f.createIfcExtrudedAreaSolid(prof, _ax3(f, a, u, w), _dir(f, (0, 0, 1)), float(ln))


def solid_entity(f, it: dict):
    """형상 하나. 공극(cuts)이 있으면 IfcBooleanResult(DIFFERENCE) 체인을 IfcCsgSolid 로 감싼다
    (IFC4 의 'Clipping' 표현은 반공간 절단만 허용해서, 일반 공극은 IfcSolidModel 인 IfcCsgSolid 로 담는다)."""
    base = _extrude_item(f, it) if it["k"] == "extrude" else _segment_item(f, it)
    cuts = it.get("cuts", ())
    if not cuts:
        return base
    for cut in cuts:
        base = f.createIfcBooleanResult("DIFFERENCE", base, _extrude_item(f, cut) if cut["k"] == "extrude" else _segment_item(f, cut))
    return f.createIfcCsgSolid(base)


def body_representation(model, items: list[dict]):
    """본체 표현(IfcShapeRepresentation) 하나. 항목이 없으면 None."""
    f = model.f
    if not items:
        return None
    sols = [solid_entity(f, it) for it in items]
    typ = "SolidModel" if any(it.get("cuts") for it in items) else "SweptSolid"
    return f.createIfcShapeRepresentation(model.body, "Body", typ, sols)


def _curve(f, p: dict):
    t = p["t"]
    P = lambda xy: f.createIfcCartesianPoint([float(xy[0]), float(xy[1])])   # noqa: E731
    if t == "line":
        return f.createIfcPolyline([P(p["a"]), P(p["b"])])
    if t == "polyline":
        pts = list(p["pts"]) + ([p["pts"][0]] if p["closed"] else [])
        return f.createIfcPolyline([P(q) for q in pts])
    circ = f.createIfcCircle(_ax2(f, p["c"]), float(p["r"]))
    if t == "circle":
        return circ
    cx, cy = p["c"]
    r = p["r"]
    s = (round(cx + r * math.cos(math.radians(p["a0"])), 6), round(cy + r * math.sin(math.radians(p["a0"])), 6))
    e = (round(cx + r * math.cos(math.radians(p["a1"])), 6), round(cy + r * math.sin(math.radians(p["a1"])), 6))
    return f.createIfcTrimmedCurve(circ, [P(s)], [P(e)], True, "CARTESIAN")


def plan_context(model):
    """'Plan' 하위 문맥. 처음 필요할 때 만들고 쓰는 표현이 없어지면 지운다(없는 모델의 IFC 해시가 바뀌지 않게)."""
    if model.plan_ctx is None:
        parent = model.body.ParentContext
        model.plan_ctx = api_context.add_context(model.f, context_type="Plan", context_identifier="FootPrint",
                                                 target_view="PLAN_VIEW", parent=parent)
    return model.plan_ctx


def gc_plan_context(model) -> None:
    ctx = model.plan_ctx
    if ctx is not None and not model.f.get_inverse(ctx):
        model.f.remove(ctx)
        model.plan_ctx = None


def plan_representation(model, prims: list[dict]):
    if not prims:
        return None
    f = model.f
    cs = f.createIfcGeometricCurveSet([_curve(f, p) for p in prims])
    return f.createIfcShapeRepresentation(plan_context(model), "FootPrint", "GeometricCurveSet", [cs])


# ---------------------------------------------------------------- 정리
def _drop_rep_deep(model, rep) -> None:
    """표현 하나를 지운다. 매핑 항목은 표현 지도(타입 소유)를 건드리지 않고 항목과 변환만 지운다."""
    f = model.f
    for item in list(rep.Items or []):
        if item.is_a("IfcMappedItem"):
            tgt = item.MappingTarget
            f.remove(item)
            if tgt is not None:
                ue.remove_deep2(f, tgt)
        else:
            ue.remove_deep2(f, item)
    f.remove(rep)


def _drop_product_rep(model, e) -> None:
    pds = e.Representation
    if pds is None:
        return
    e.Representation = None
    for rep in list(pds.Representations or []):
        _drop_rep_deep(model, rep)
    model.f.remove(pds)


def _set_attr(e, name: str, value: Any) -> None:
    setattr(e, name, value)


def _apply_attrs(e, attrs: dict) -> None:
    for k, v in attrs.items():
        _set_attr(e, k, v)


_ATTRS: dict[str, frozenset] = {}


def attr_names(ifc_class: str) -> frozenset:
    """IFC4 스키마에서 클래스의 모든 속성 이름(상속 포함)."""
    hit = _ATTRS.get(ifc_class)
    if hit is None:
        import ifcopenshell.ifcopenshell_wrapper as w
        d = w.schema_by_name("IFC4").declaration_by_name(ifc_class)
        hit = _ATTRS[ifc_class] = frozenset(x.name() for x in d.all_attributes())
    return hit


def has_attr(e, name: str) -> bool:
    return name in attr_names(e.is_a())


# ---------------------------------------------------------------- 타입
def project_type(model, eid: str, spec: dict) -> None:
    f = model.f
    t = model.ents[eid]
    t.Name = spec["name"]
    if has_attr(t, "PredefinedType"):
        t.PredefinedType = spec["predefined"]
    _apply_attrs(t, spec["attrs"])
    # 표현 지도는 통째로 다시 만든다. 타입 내용이 바뀌면 인스턴스 스펙도 같이 바뀌어(타입 행에 의존) 같은 flush 에서 다시 투영된다
    _clear_type_maps(model, t)
    body = body_representation(model, spec["body"])
    plan = plan_representation(model, spec["plan"])
    maps = []
    for rep in (body, plan):
        if rep is not None:
            origin = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint(list(_ZERO)), None, None)
            maps.append(f.createIfcRepresentationMap(origin, rep))
    t.RepresentationMaps = maps or None
    model.type_maps[eid] = True
    gc_plan_context(model)


def _clear_type_maps(model, t) -> None:
    f = model.f
    for m in list(t.RepresentationMaps or []):
        rep = m.MappedRepresentation
        t.RepresentationMaps = [x for x in t.RepresentationMaps if x != m] or None
        f.remove(m)
        _drop_rep_deep(model, rep)


def unproject_type(model, eid: str) -> None:
    t = model.ents.get(eid)
    if t is None:
        return
    model.type_maps.pop(eid, None)
    _clear_type_maps(model, t)
    model.typerels.pop(eid, None)
    gc_plan_context(model)


# ---------------------------------------------------------------- 인스턴스
def _type_rel(model, type_eid: str, inst_eid: str):
    """타입 하나에 관계 하나(IfcRelDefinesByType)를 두고 인스턴스를 거기에 모은다."""
    f = model.f
    t = model.ents[type_eid]
    e = model.ents[inst_eid]
    rel = model.typerels.get(type_eid)
    if rel is None:
        rel = f.createIfcRelDefinesByType(ifcopenshell.guid.new(), None, None, None, [e], t)
        model.typerels[type_eid] = rel
    elif e not in rel.RelatedObjects:
        rel.RelatedObjects = list(rel.RelatedObjects) + [e]
    model.type_of[inst_eid] = type_eid


def _untype(model, inst_eid: str) -> None:
    tid = model.type_of.pop(inst_eid, None)
    if tid is None:
        return
    rel = model.typerels.get(tid)
    e = model.ents[inst_eid]
    if rel is None:
        return
    rest = [x for x in rel.RelatedObjects if x != e]
    if rest:
        rel.RelatedObjects = rest
    else:
        model.f.remove(rel)
        model.typerels.pop(tid, None)


def unproject_inst(model, eid: str) -> None:
    """인스턴스의 투영을 걷어낸다: 개구부, 타입 관계, 표현(타입 소유 지도는 건드리지 않는다)."""
    e = model.ents.get(eid)
    if e is None:
        return
    model.unproject_opening(eid)
    _untype(model, eid)
    _drop_product_rep(model, e)
    gc_plan_context(model)


def project_inst(model, eid: str, spec: dict) -> None:
    f = model.f
    e = model.ents[eid]
    e.Name = spec["name"]
    if has_attr(e, "PredefinedType"):
        e.PredefinedType = spec["predefined"]
    _apply_attrs(e, spec["attrs"])
    model._contain(eid, spec["level"])
    if spec["host"]:
        wall = model.ents[spec["host"]]
        model._place(e, wall.ObjectPlacement, (spec["offset"], 0.0, spec["elevation"]), (1.0, 0.0, 0.0))
    else:
        rot = math.radians(spec["rotation"])
        x, y = spec["position"]
        model._place(e, model.ents[spec["level"]].ObjectPlacement, (x, y, spec["elevation"]), (math.cos(rot), math.sin(rot), 0.0))
    # 표현: 기존 것을 걷어내고 새로 만든다(매핑이면 타입 지도 참조, 아니면 자기 항목)
    _drop_product_rep(model, e)
    reps = []
    tid = spec["type"]
    tmaps = list(model.ents[tid].RepresentationMaps or [])
    for kind, same, own in (("Body", spec["same_body"], lambda: body_representation(model, spec["body"])),
                            ("FootPrint", spec["same_plan"], lambda: plan_representation(model, spec["plan"]))):
        rep = None
        if same:
            m = next((m for m in tmaps if m.MappedRepresentation.RepresentationIdentifier == kind), None)
            if m is not None:
                tgt = f.createIfcCartesianTransformationOperator3D(None, None, f.createIfcCartesianPoint(list(_ZERO)), None, None)
                item = f.createIfcMappedItem(m, tgt)
                ctx = model.body if kind == "Body" else plan_context(model)
                rep = f.createIfcShapeRepresentation(ctx, kind, "MappedRepresentation", [item])
        else:
            rep = own()
        if rep is not None:
            reps.append(rep)
    e.Representation = f.createIfcProductDefinitionShape(None, None, reps) if reps else None
    _type_rel(model, tid, eid)
    # 개구부: 호스트가 있고 cut 이 있으면 인스턴스가 바뀔 때마다 다시 만든다
    model.unproject_opening(eid)
    if spec["host"] and spec["cut"]:
        wall = model.ents[spec["host"]]
        cx, cz = spec["cut"]["x"], spec["cut"]["z"]
        t2 = spec["wall_thickness"] / 2.0
        item = {"k": "extrude", "plane": "xz", "profile": {"shape": "rect", "u": cx, "v": cz}, "range": [-t2, t2]}
        op = api_root.create_entity(f, ifc_class="IfcOpeningElement")
        op.GlobalId = model.derived_guid_for(f"opening:{eid}")
        op.Name = spec["name"] + "-opening"
        model._place(op, wall.ObjectPlacement, (spec["offset"], 0.0, spec["elevation"]), (1.0, 0.0, 0.0))
        op.Representation = f.createIfcProductDefinitionShape(None, None, [
            f.createIfcShapeRepresentation(model.body, "Body", "SweptSolid", [_extrude_item(f, item)])])
        api_feature.add_feature(f, feature=op, element=wall)
        api_feature.add_filling(f, opening=op, element=e)
        model.openings[eid] = op
    gc_plan_context(model)
