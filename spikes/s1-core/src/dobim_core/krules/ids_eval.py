# SPDX-License-Identifier: MPL-2.0
"""IDS 1.0 부분 집합 평가기(외부 IDS 라이브러리 없이 ifcopenshell 만 쓴다).

왜 따로 만드나: S9 스파이크는 ifctester 로 IDS 를 돌렸다. ifctester 자체는 LGPL-3.0 이지만 패키지 메타데이터가
bcf-client(GPLv3)를 필수 의존으로 끌고 와 코어 의존성에 넣을 수 없다(ADR-0003: GPL 금지). 그래서 코어에는 IDS 1.0 의
facet 중 entity·attribute·classification·property 네 가지만 직접 평가한다. partOf·material facet 이 있는 IDS 는
UNSUPPORTED 로 거절한다(조용히 건너뛰지 않는다).

판정 규칙은 S9 결과와 같아야 한다(규칙 37개의 기준값·시험 201개). 그래서 스파이크가 쓰던 실행 결과의 모양 —
적용 대상 목록, 요구 facet 별 (객체, 사유 문장) 목록, 사유 문장 문구(영문) — 을 그대로 낸다. 차이 1건: 분류 facet 에
system 이 없으면(값만 요구) 스파이크 실행기는 늘 실패시켰는데, IDS 1.0 사양대로 system 검사를 건너뛴다.
기본 IDS(규칙 파일)는 분류 facet 마다 system 을 주므로 결과에 영향이 없다.

xs:pattern 은 XSD 정규식을 파이썬 정규식으로 옮겨 전체 일치(fullmatch)로 본다. XSD 전용 문법(\\i \\c \\p{..} 문자 클래스 빼기)은
UNSUPPORTED 로 거절한다.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.util.classification as ucls
import ifcopenshell.util.element as uel
import ifcopenshell.util.unit as uunit

NS = "{http://standards.buildingsmart.org/IDS}"
XS = "{http://www.w3.org/2001/XMLSchema}"
_FACETS = ("entity", "partOf", "classification", "attribute", "property", "material")
_SUPPORTED = ("entity", "classification", "attribute", "property")
_NUMERIC_BOUNDS = ("minInclusive", "maxInclusive", "minExclusive", "maxExclusive")


class IdsError(Exception):
    """IDS 파일을 읽을 수 없거나(형식) 이 평가기가 지원하지 않는 구문이 있을 때."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- 값 제약(xs:restriction)

def _xsd_regex(pattern: str) -> re.Pattern:
    """XSD 정규식을 파이썬 정규식으로 옮긴다. XSD 에는 앵커가 없어 ^ $ 는 문자 그대로다(문자 클래스 밖)."""
    for bad in ("\\i", "\\I", "\\c", "\\C", "\\p", "\\P", "-["):
        if bad in pattern:
            raise IdsError("UNSUPPORTED", f"지원하지 않는 XSD 정규식 문법 {bad!r}: {pattern[:60]}")
    out, i, in_class = [], 0, False
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(pattern[i:i + 2])
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
        elif ch == "[":
            in_class = True
        elif ch in "^$":
            ch = "\\" + ch
        out.append(ch)
        i += 1
    try:
        return re.compile("".join(out))
    except re.error as e:
        raise IdsError("IDS_INVALID", f"정규식을 해석할 수 없음: {pattern[:60]} ({e})") from None


def cast_to(text: str, like: Any):
    """IDS 의 문자열 값 text 를 실제 값 like 의 타입으로 바꾼다. 바꿀 수 없으면 None.

    정수는 float 로 바꾼다(42.0 == 42, 42.3 != 42). 불리언은 'true'/'1', 'false'/'0' 을 읽고, 그 밖의 문자열은
    파이썬 bool() 규칙(빈 문자열만 거짓)을 따른다: IDS 의 'TRUE' 가 IfcBoolean 참과 같게 판정된다."""
    try:
        if isinstance(like, bool):
            if text in ("true", "1"):
                return True
            if text in ("false", "0"):
                return False
            return bool(text)
        if isinstance(like, int):
            return float(text)
        if isinstance(like, float):
            return float(text)
        if isinstance(like, str):
            return str(text)
        return type(like)(text)
    except (ValueError, TypeError):
        return None


@dataclass
class Restriction:
    """xs:restriction. options 는 XML 순서대로: enumeration 은 목록, 나머지는 값 하나(같은 종류가 여럿이면 목록)."""
    base: str = "string"
    options: dict = field(default_factory=dict)

    def matches(self, other) -> bool:
        if other is None:
            return False
        for kind, value in self.options.items():
            try:
                if kind == "enumeration":
                    if other not in [cast_to(v, other) for v in value]:
                        return False
                elif kind == "pattern":
                    if not isinstance(other, str):
                        return False
                    for p in value if isinstance(value, list) else [value]:
                        if _xsd_regex(p).fullmatch(other) is None:
                            return False
                elif kind == "length":
                    if len(str(other)) != int(value):
                        return False
                elif kind == "maxLength":
                    if len(str(other)) > int(value):
                        return False
                elif kind == "minLength":
                    if len(str(other)) < int(value):
                        return False
                elif kind == "maxExclusive":
                    if float(other) >= float(value):
                        return False
                elif kind == "maxInclusive":
                    if float(other) > float(value):
                        return False
                elif kind == "minExclusive":
                    if float(other) <= float(value):
                        return False
                elif kind == "minInclusive":
                    if float(other) < float(value):
                        return False
            except (ValueError, TypeError):
                return False
        return True

    def __str__(self):
        # 사유 이름표(label)에 쓰이는 표기. 스파이크 결과와 같은 모양(제약 dict 의 파이썬 표기)
        return str(self.options)


def same(spec_value, actual) -> bool:
    """IDS 값(문자열 또는 제약)과 실제 값이 같은가."""
    if isinstance(spec_value, Restriction):
        return spec_value.matches(actual)
    return spec_value == actual


def _approx(value: float, target: float) -> bool:
    """실수 비교: 상대 오차 1e-6 안이면 같다."""
    lo, hi = target * (1.0 - 1e-6), target * (1.0 + 1e-6)
    if target >= 0:
        return lo <= value <= hi
    return hi <= value <= lo


def _value_mismatch(spec_value, value) -> bool:
    """속성·특성 값 하나가 요구 값과 다른가(문자열 요구는 실제 타입으로 바꿔 비교, 실수는 근사)."""
    if isinstance(spec_value, str):
        if isinstance(value, str):
            return value != spec_value
        cast = cast_to(spec_value, value)
        if isinstance(value, float) and isinstance(cast, float):
            return not _approx(value, cast)
        return value != cast
    return not same(spec_value, value)


# ---------------------------------------------------------------- facet

@dataclass
class Facet:
    kind: str
    params: dict
    cardinality: str = "required"

    def get(self, key):
        return self.params.get(key)


def _reason_entity(r):
    if r["type"] == "NAME":
        return f'The entity class "{r["actual"]}" does not meet the required IFC class'
    return f'The predefined type "{r["actual"]}" does not meet the required type'


_ATTR = {"NOVALUE": "The required attribute did not exist", "PROHIBITED": "The attribute value should not have met the requirement"}
_CLS = {"NOVALUE": "The entity has no classification", "PROHIBITED": "The classification should not have met the requirement"}
_PROP = {"NOPSET": "The required property set does not exist", "NOVALUE": "The property set does not contain the required property",
         "PROHIBITED": "The property should not have met the requirement"}


def _reason_attr(r):
    if r["type"] == "FALSEY":
        return f'The attribute value "{r["actual"]}" is empty'
    if r["type"] == "VALUE":
        return f'The attribute value "{r["actual"]}" does not match the requirement'
    return _ATTR[r["type"]]


def _reason_cls(r):
    if r["type"] == "VALUE":
        return f'The references "{r["actual"]}" do not match the requirements'
    if r["type"] == "SYSTEM":
        return f'The systems "{r["actual"]}" do not match the requirements'
    return _CLS[r["type"]]


def _reason_prop(r):
    if r["type"] == "DATATYPE":
        return f'The property\'s data type "{r["actual"]}" does not match the required data type of "{r["dataType"]}"'
    if r["type"] == "VALUE":
        a = r["actual"]
        if isinstance(a, list):
            return (f'The property value "{a[0]}" does not match the requirements' if len(a) == 1
                    else f'The property values "{a}" do not match the requirements')
        return f'The property value "{a}" does not match the requirements'
    return _PROP[r["type"]]


# 판정 함수는 (통과 여부, 사유 dict 또는 None) 을 돌려준다

def _derived_class(element_type):
    return element_type.is_a().upper().removesuffix("TYPE").removesuffix("STYLE")


def _check_entity(fc: Facet, inst):
    name, ptype = fc.get("name"), fc.get("predefinedType")
    ok = same(name, inst.is_a().upper())
    reason = None
    etype = uel.get_type(inst) if not ok and inst.file.schema == "IFC2X3" else None
    if not ok and etype and etype != inst:
        actual = _derived_class(etype)   # IFC2X3: 유형 객체의 클래스로 판정(IfcWallType -> IFCWALL)
        ok = same(name, actual)
        reason = {"type": "NAME", "actual": actual}
    elif not ok:
        reason = {"type": "NAME", "actual": inst.is_a().upper()}
    if ok and ptype:
        actual = None
        if ptype == "USERDEFINED":
            ok = uel.is_userdefined_type(inst)
            if not ok:
                actual = uel.get_predefined_type(inst)
        else:
            actual = uel.get_predefined_type(inst)
            ok = same(ptype, actual)
        if not ok:
            reason = {"type": "PREDEFINEDTYPE", "actual": actual}
    return ok, reason


def _check_attribute(fc: Facet, inst):
    name, want = fc.get("name"), fc.get("value")
    if isinstance(name, str):
        names = [name]
        values = [getattr(inst, name, None)] if inst.get_attribute_category(name) == 1 else []   # 1 = 정방향 속성
    else:
        names, values = [], []
        for k, v in inst.get_info().items():
            if same(name, k) and inst.get_attribute_category(k) == 1:
                names.append(k)
                values.append(v)
    ok, reason = bool(values), None
    if not ok:
        if fc.cardinality == "optional":
            return True, None
        reason = {"type": "NOVALUE"}
    if ok:
        kept = []
        for nm, v in zip(names, values):
            empty = v is None or v == "" or v == tuple()
            if not empty:
                try:
                    empty = inst.attribute_type(inst.get_argument_index(nm)) == "LOGICAL" and v == "UNKNOWN"
                except Exception:
                    empty = False
            if not empty:
                kept.append(v)
        if kept:
            values = kept
        else:
            ok = False
            reason = {"type": "FALSEY", "actual": values if len(values) > 1 else values[0]}
    if ok and want:
        for v in values:
            if isinstance(v, ifcopenshell.entity_instance) or _value_mismatch(want, v):
                ok, reason = False, {"type": "VALUE", "actual": v}
                break
    if fc.cardinality == "prohibited":
        return (not ok), {"type": "PROHIBITED"}
    return ok, reason


def _check_classification(fc: Facet, inst):
    want, system = fc.get("value"), fc.get("system")
    leaves = ucls.get_references(inst)
    refs = set(leaves)
    for leaf in leaves:
        refs.update(ucls.get_inherited_references(leaf))
    ok, reason = bool(refs), None
    if not ok:
        if fc.cardinality == "optional":
            return True, None
        reason = {"type": "NOVALUE"}
    if ok and want:
        values = [getattr(r, "Identification", getattr(r, "ItemReference", None)) for r in refs]
        ok = any(same(want, v) for v in values)
        if not ok:
            reason = {"type": "VALUE", "actual": values}
    if ok and system is not None:
        systems = [c.Name for c in filter(None, (ucls.get_classification(r) for r in refs))]
        ok = any(same(system, s) for s in systems)
        if not ok:
            reason = {"type": "SYSTEM", "actual": systems}
    if fc.cardinality == "prohibited":
        return (not ok), {"type": "PROHIBITED"}
    return ok, reason


def _props_of(pset):
    """속성 집합(또는 수량 집합 등)의 개별 속성 객체. 미리 정의된 속성 집합은 이름만 가진 가짜 객체로 돌려준다."""
    if pset.is_a("IfcPropertySet"):
        return pset.HasProperties
    if pset.is_a("IfcElementQuantity"):
        return pset.Quantities
    if pset.is_a("IfcMaterialProperties") or pset.is_a("IfcProfileProperties"):
        return pset.Properties
    if pset.is_a("IfcPreDefinedPropertySet"):
        return [_Named(k) for k, v in pset.get_info().items() if not isinstance(v, ifcopenshell.entity_instance)]
    return []


@dataclass
class _Named:
    Name: str


def _to_si(value, unit, out_prefix=None):
    return uunit.convert(value, getattr(unit, "Prefix", None), unit.Name, out_prefix, uunit.si_type_names[unit.UnitType])


def _dtype_bad(fc: Facet, data_type):
    dt = fc.get("dataType")
    if dt and data_type.lower() != dt.lower():
        return {"type": "DATATYPE", "actual": data_type, "dataType": dt}
    return None


def _normalise(fc: Facet, inst, pe, props: dict):
    """속성 객체 pe 하나의 값을 비교할 수 있게 바꾸고(단위를 SI 로) 데이터 타입을 확인한다. 실패면 사유 dict, 아니면 None.
    지원하지 않는 속성 종류(복합 속성 등)면 'unsupported'."""
    f = inst.file
    if not isinstance(pe, ifcopenshell.entity_instance):
        return None   # 미리 정의된 속성 집합의 값
    if pe.is_a("IfcPropertySingleValue"):
        bad = _dtype_bad(fc, pe.NominalValue.is_a())
        if bad:
            return bad
        unit = uunit.get_property_unit(pe, f)
        if unit and getattr(unit, "Name", None):
            props[pe.Name] = _to_si(pe.NominalValue.wrappedValue, unit, "KILO" if unit.UnitType == "MASSUNIT" else None)
        return None
    if pe.is_a("IfcPhysicalSimpleQuantity"):
        dt = pe.declaration.as_entity().attribute_by_index(3).type_of_attribute().declared_type().name()
        bad = _dtype_bad(fc, dt)
        if bad:
            return bad
        unit = uunit.get_property_unit(pe, f)
        if unit:
            props[pe.Name] = _to_si(pe[3], unit)
        return None
    if pe.is_a("IfcPropertyEnumeratedValue"):
        if not pe.EnumerationValues:
            return {"type": "NOVALUE"}
        return _dtype_bad(fc, pe.EnumerationValues[0].is_a())
    if pe.is_a("IfcPropertyListValue"):
        if not pe.ListValues:
            return {"type": "NOVALUE"}
        bad = _dtype_bad(fc, pe.ListValues[0].is_a())
        if bad:
            return bad
        unit = uunit.get_property_unit(pe, f)
        if unit:
            props[pe.Name] = [_to_si(v, unit) for v in props[pe.Name]]
        return None
    if pe.is_a("IfcPropertyBoundedValue"):
        vals, dt = [], None
        for at in ("UpperBoundValue", "LowerBoundValue", "SetPointValue"):
            v = getattr(pe, at)
            if v is not None:
                dt = v.is_a()
                vals.append(v.wrappedValue)
        if dt is None:
            return {"type": "NOVALUE"}
        bad = _dtype_bad(fc, dt)
        if bad:
            return bad
        unit = uunit.get_property_unit(pe, f)
        props[pe.Name] = [_to_si(v, unit) for v in vals] if unit else vals
        return None
    if pe.is_a("IfcPropertyTableValue"):
        # 표 속성은 dataType 이 맞는 열의 값만 비교한다(dataType 이 없으면 비교할 값이 없어 데이터 타입 불일치)
        units = uunit.get_property_table_unit(pe, f)
        vals, dt = [], None
        want = fc.get("dataType")
        for at in ("Defining", "Defined"):
            col = getattr(pe, f"{at}Values") or ()
            if not col:
                continue
            dt = col[0].is_a()
            if want and dt.lower() == want.lower():
                cv = [v.wrappedValue for v in col]
                unit = units[f"{at}Unit"]
                vals.extend([_to_si(v, unit) for v in cv] if unit else cv)
        if not vals:
            return {"type": "DATATYPE", "actual": dt, "dataType": want}
        props[pe.Name] = vals
        return None
    return "unsupported"


def _check_property(fc: Facet, inst):
    pset_name, base, want = fc.get("propertySet"), fc.get("baseName"), fc.get("value")
    if isinstance(pset_name, str):
        p = uel.get_pset(inst, pset_name)
        psets = {pset_name: p} if p else {}
    else:
        psets = {k: v for k, v in uel.get_psets(inst).items() if same(pset_name, k)}
    ok, reason = bool(psets), None
    if not ok:
        if fc.cardinality == "optional":
            return True, None
        reason = {"type": "NOPSET"}
    if ok:
        for pname, pprops in psets.items():
            pset_ent = inst.file.by_id(pprops["id"])
            if isinstance(base, str):
                found = {}
                v = pprops.get(base)
                logical_unknown = v == "UNKNOWN" and any(
                    getattr(pe, "Name", None) == base and getattr(pe, "NominalValue", None) is not None
                    and pe.NominalValue.is_a("IfcLogical") for pe in _props_of(pset_ent))
                if not logical_unknown and v is not None and v != "":
                    found[base] = v
            else:
                found = {k: v for k, v in pprops.items() if same(base, k) and v is not None and v != ""}
            if not found:
                if fc.cardinality == "optional":
                    return True, None
                ok, reason = False, {"type": "NOVALUE"}
                break
            supported = True
            for pe in _props_of(pset_ent):
                if pe.Name not in found:
                    continue
                r = _normalise(fc, inst, pe, found)
                if r == "unsupported":
                    supported = False
                elif r is not None:
                    ok, reason = False, r
                    break
            if not supported:   # 지원하지 않는 속성 종류가 하나라도 있으면 다른 사유보다 '속성 없음'이 앞선다
                ok, reason = False, {"type": "NOVALUE"}
            if not ok:
                break
            if want:
                for v in found.values():
                    if isinstance(v, list):
                        miss = (cast_to(want, v[0]) not in v) if isinstance(want, str) else not any(same(want, x) for x in v)
                    else:
                        miss = _value_mismatch(want, v)
                    if miss:
                        ok, reason = False, {"type": "VALUE", "actual": v}
                        break
            if not ok:
                break
    if fc.cardinality == "prohibited":
        return (not ok), {"type": "PROHIBITED"}
    return ok, reason


_CHECK = {"entity": (_check_entity, _reason_entity), "attribute": (_check_attribute, _reason_attr),
          "classification": (_check_classification, _reason_cls), "property": (_check_property, _reason_prop)}


def check(fc: Facet, inst):
    """facet 하나로 객체 하나를 판정한다. (통과 여부, 사유 문장 또는 None)."""
    fn, why = _CHECK[fc.kind]
    ok, reason = fn(fc, inst)
    return ok, (None if ok else why(reason))


# ---------------------------------------------------------------- 1차 거르기(적용 대상 후보)

def _by_type(f, cls, sub=False):
    try:
        return f.by_type(cls, include_subtypes=sub)
    except Exception:   # 그 IFC 버전에 없는 클래스 이름
        return []


def _first_pass(fc: Facet, f):
    """적용 조건의 첫 facet 으로 후보 객체를 모은다(그 뒤 facet 은 후보를 걸러 낸다)."""
    if fc.kind == "entity":
        name = fc.get("name")
        if f.schema == "IFC2X3":
            out = []
            for cls in f.types():
                if same(name, cls.upper()):
                    out.extend(_by_type(f, cls))
            for et in f.by_type("IfcTypeProduct"):
                if same(name, _derived_class(et)):
                    out.extend(uel.get_types(et))
            out = sorted(set(out), key=lambda e: e.id())
        elif isinstance(name, str):
            out = list(_by_type(f, name))
        else:
            out = []
            for cls in f.types():
                if same(name, cls.upper()):
                    out.extend(_by_type(f, cls))
        if fc.get("predefinedType"):
            out = [e for e in out if check(fc, e)[0]]
        return out
    if fc.kind == "attribute":
        schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(f.schema_identifier)
        ents = {e.name(): e for e in schema.entities()}

        def drop_subtypes(ent):
            for sub in ent.subtypes():
                if ents.pop(sub.name(), None):
                    drop_subtypes(sub)

        out = []
        name = fc.get("name")
        for ename in sorted(ents):
            ent = ents.get(ename)
            if ent is None:
                continue
            if any(same(name, a.name()) for a in ent.attributes()):
                out.extend(_by_type(f, ename, True))
                drop_subtypes(ent)
        return out
    if fc.kind == "property" and f.schema != "IFC2X3":
        return [e for e in (list(f.by_type("IfcObjectDefinition")) + list(_by_type(f, "IfcMaterialDefinition"))
                            + list(_by_type(f, "IfcProfileDef"))) if check(fc, e)[0]]
    return [e for e in f.by_type("IfcObjectDefinition") if check(fc, e)[0]]


# ---------------------------------------------------------------- IDS 읽기

def _text_value(el):
    """facet 하위 값 요소(name, value 등)를 문자열 또는 Restriction 으로 읽는다."""
    if el is None:
        return None
    sv = el.find(NS + "simpleValue")
    if sv is not None:
        return sv.text or ""
    rs = el.find(XS + "restriction")
    if rs is None:
        raise IdsError("IDS_INVALID", f"값 요소 {el.tag.split('}')[-1]} 에 simpleValue 나 xs:restriction 이 없음")
    r = Restriction(base=(rs.get("base") or "xs:string").split(":")[-1])
    for c in rs:
        kind = c.tag.split("}")[-1]
        if kind == "annotation":
            continue
        v = c.get("value")
        if kind == "enumeration":
            r.options.setdefault(kind, []).append(v)
        elif kind in r.options:
            prev = r.options[kind]
            r.options[kind] = (prev if isinstance(prev, list) else [prev]) + [v]
        else:
            r.options[kind] = v
    for kind, v in r.options.items():
        if kind == "pattern":
            for p in v if isinstance(v, list) else [v]:
                _xsd_regex(p)   # 지원하지 않는 문법이면 읽을 때 거절한다
        elif kind not in ("enumeration", "length", "minLength", "maxLength", *_NUMERIC_BOUNDS):
            raise IdsError("UNSUPPORTED", f"지원하지 않는 xs:restriction 제약: {kind}")
    return r


_PARAMS = {"entity": ("name", "predefinedType"), "attribute": ("name", "value"),
           "classification": ("value", "system"), "property": ("propertySet", "baseName", "value")}


def _facet(el) -> Facet:
    kind = el.tag.split("}")[-1]
    if kind not in _FACETS:
        raise IdsError("IDS_INVALID", f"알 수 없는 facet: {kind}")
    if kind not in _SUPPORTED:
        raise IdsError("UNSUPPORTED", f"이 평가기는 {kind} facet 을 지원하지 않음(entity·attribute·classification·property 만)")
    params = {p: _text_value(el.find(NS + p)) for p in _PARAMS[kind]}
    if kind == "property":
        params["dataType"] = el.get("dataType")
        if params["baseName"] is None:
            raise IdsError("IDS_INVALID", "property facet 에 baseName 이 없음")
    if kind in ("entity", "attribute") and params["name"] is None:
        raise IdsError("IDS_INVALID", f"{kind} facet 에 name 이 없음")
    card = el.get("cardinality") or "required"
    if card not in ("required", "optional", "prohibited"):
        raise IdsError("IDS_INVALID", f"cardinality 값 오류: {card[:20]}")
    return Facet(kind, params, card)


def _occurs(v, default):
    if v is None:
        return default
    if v == "unbounded":
        return v
    try:
        return int(v)
    except ValueError:
        raise IdsError("IDS_INVALID", f"minOccurs/maxOccurs 값 오류: {v[:20]}") from None


@dataclass
class Spec:
    identifier: str | None
    name: str
    description: str
    versions: list
    min_occurs: Any
    max_occurs: Any
    applicability: list
    requirements: list


def load(path) -> list[Spec]:
    """IDS 파일을 읽는다. 형식이 틀리면 IdsError(IDS_INVALID), 지원하지 않는 구문이면 IdsError(UNSUPPORTED)."""
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError) as e:
        raise IdsError("IDS_INVALID", f"IDS XML 을 읽을 수 없음: {type(e).__name__}") from None
    if root.tag != NS + "ids":
        raise IdsError("IDS_INVALID", "IDS 1.0 문서가 아님(뿌리 요소가 ids 이고 이름공간이 IDS 여야 함)")
    box = root.find(NS + "specifications")
    if box is None or not len(box):
        raise IdsError("IDS_INVALID", "specification 이 없음")
    specs = []
    for s in box.findall(NS + "specification"):
        versions = (s.get("ifcVersion") or "").split()
        if not versions:
            raise IdsError("IDS_INVALID", "specification 에 ifcVersion 이 없음")
        app = s.find(NS + "applicability")
        if app is None or not len(app):
            raise IdsError("IDS_INVALID", "applicability 가 없거나 비어 있음")
        req = s.find(NS + "requirements")
        specs.append(Spec(
            identifier=s.get("identifier"), name=s.get("name") or "", description=s.get("description") or "",
            versions=versions, min_occurs=_occurs(app.get("minOccurs"), 0), max_occurs=_occurs(app.get("maxOccurs"), "unbounded"),
            applicability=[_facet(e) for e in app], requirements=[_facet(e) for e in req] if req is not None else []))
    return specs


# ---------------------------------------------------------------- 실행

def label(fc: Facet) -> str | None:
    """요구 facet 의 이름표(사유 앞에 붙는다). 스파이크 결과와 같은 문자열."""
    if fc.kind == "entity":
        return "IFC Class / Predefined Type" if fc.get("predefinedType") else "IFC Class"
    if fc.kind == "attribute":
        return str(fc.get("name"))
    if fc.kind == "classification":
        s, v = fc.get("system"), fc.get("value")
        return "System / Reference" if s and v else ("System" if s else "Reference")
    return f"{fc.get('propertySet')}.{fc.get('baseName')}"


@dataclass
class SpecResult:
    is_ifc_version: bool
    applicable: list
    failures: list   # [(이름표, [(객체, 사유 문장)])] 요구 facet 순서대로


def evaluate(f, spec: Spec) -> SpecResult:
    """명세 하나를 모델에 적용한다(IFC 버전이 맞지 않아도 돌려서 결과를 낸다: 버전 판정은 호출자가 한다)."""
    cands = None
    for fc in spec.applicability:
        cands = _first_pass(fc, f) if cands is None else [e for e in cands if check(fc, e)[0]]
    applicable = []
    fails = [(label(fc), []) for fc in spec.requirements]
    for e in cands or []:
        if not all(check(fc, e)[0] for fc in spec.applicability if fc.kind != "entity"):
            continue
        applicable.append(e)
        if spec.max_occurs == 0:   # 금지 명세는 요구를 보지 않는다
            continue
        for fc, (_, bucket) in zip(spec.requirements, fails):
            ok, why = check(fc, e)
            if not ok:
                bucket.append((e, why))
    return SpecResult(f.schema_identifier in spec.versions, applicable, fails)
