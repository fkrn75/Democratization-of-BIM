# SPDX-License-Identifier: MPL-2.0
"""레시피 검증기. 구조화된 오류(경로·이유·힌트)로 보고한다.

검사 단계 (checks 의 키)
  schema          JSON 구조·타입·필수 키·알 수 없는 키 (recipe.py)
  references      식 구문, 미정의 이름, 파생값 순환, 타입/인스턴스 수준 위반, 호스트 문맥, solid id (evaluate.analyze)
  values          표본(기본값·각 매개변수 min/max·각 열거값·호스트 두께)에서 값 검사·식 평가·제약
  geometry        표본에서 형상 해석(퇴화·좌표 범위·반복 횟수·스윕 방향) + 어떤 표본에서도 안 보이는 solid 경고
  ifc_schema      IFC4 스키마 대조(클래스·타입 클래스·PredefinedType·속성 이름/타입/열거값)
  psets_standard  Pset_/Qto_ 접두사 속성셋을 buildingSMART 표준 템플릿과 대조(존재·적용 클래스·속성·값 종류·열거값)
  ifc_projection  코어와 같은 IfcModel 투영으로 파일을 만들고 ifcopenshell.validate(express_rules) 오류 0
  geometry_shape  ifcopenshell.geom 으로 실제 메시를 만들어 비어 있지 않고 부피가 양수인지
  psets_values    IFC 에 읽혀 나온 속성셋 값이 레시피가 평가한 값과 일치

level="static" 은 앞의 네 단계, "full" 은 전부. 앞 단계가 실패하면 뒤 단계는 skipped.

시간 예산: static 10초, full 45초(BUDGET_S). 넘으면 E_LIMIT 으로 멈춘다(read 질의가 서버를 오래 막지 않게). 정상 레시피는 full 이 5초 안팎이다.
공극 검사: 어떤 표본에서도 대상 solid 와 교차하지 않는 공극은 E_VOID_MISS(경계 상자 검사, geometry),
           절단해도 부피가 줄지 않는 solid(공극이 전부 지우거나 스치기만 함 — IfcOpenShell 은 빈 불리언 결과를 원본으로 되돌린다)는 E_SHAPE_VOLUME.
"""
from __future__ import annotations

import math
import time
import uuid
from typing import Any

from dobim_core.family import evaluate as ev
from dobim_core.family.recipe import FamilyError, Issue, Recipe, parse_recipe, ptr

_FORBIDDEN_ATTRS = {"GlobalId", "OwnerHistory", "Name", "ObjectPlacement", "Representation", "RepresentationMaps",
                    "HasPropertySets", "PredefinedType"}
_VALUE_CODES = ("E_PARAM", "E_EXPR", "E_CONSTRAINT", "E_UNKNOWN_PARAM", "E_WRONG_KIND", "E_VALUE")
BUDGET_S = {"static": 10.0, "full": 45.0}   # 검증 전체 시간 예산(초)


def _status(issues: list[Issue], prefixes: tuple[str, ...] | None = None) -> str:
    return "fail" if any(i.level == "error" and (prefixes is None or i.code.startswith(prefixes)) for i in issues) else "pass"


def validate_recipe(data: Any, level: str = "full", time_budget: float | None = None) -> dict:
    t0 = time.perf_counter()
    deadline = t0 + (BUDGET_S.get(level, 45.0) if time_budget is None else time_budget)

    def over() -> bool:
        return time.perf_counter() > deadline

    def limit_issue(where: str) -> Issue:
        return Issue("E_LIMIT", "", f"검증 시간 예산({BUDGET_S.get(level, 45.0) if time_budget is None else time_budget:g}초)을 넘어 {where}에서 멈췄다",
                     "레시피를 단순하게 하세요: repeat 횟수·공극 수·다각형 점 수·식 길이를 줄입니다")

    issues: list[Issue] = []
    checks: dict[str, str] = {k: "skipped" for k in ("schema", "references", "values", "geometry", "ifc_schema",
                                                       "psets_standard", "ifc_projection", "geometry_shape", "psets_values")}
    stats: dict[str, Any] = {}

    def done(r: Recipe | None) -> dict:
        errs = [i for i in issues if i.level == "error"]
        warns = [i for i in issues if i.level == "warning"]
        stats["seconds"] = round(time.perf_counter() - t0, 2)
        return {"ok": not errs, "family": r.id if r else (data.get("id") if isinstance(data, dict) else None), "level": level,
                "checks": checks, "total_errors": len(errs), "errors": [i.to_dict() for i in errs],
                "warnings": [i.to_dict() for i in warns], "stats": stats}

    try:
        r = parse_recipe(data)
    except FamilyError as ex:
        issues += ex.issues
        checks["schema"] = "fail"
        return done(None)
    checks["schema"] = "pass"
    plan, ai = ev.analyze(r)
    issues += ai
    checks["references"] = _status(ai)
    if plan is None:
        return done(r)

    # ---- 표본 평가 (값·형상)
    samples = ev.sample_points(r)
    stats["samples"] = len(samples)
    seen: set = set()
    seen_ids: set = set()
    good: list[tuple[dict, dict, dict, dict]] = []
    sample_issues: list[Issue] = []
    for tv, iv, host in samples:
        if over():
            issues.append(limit_issue("표본 평가"))
            return done(r)
        try:
            out = ev.evaluate(r, plan, tv, iv, host)
        except FamilyError as ex:
            for i in ex.issues:
                key = (i.code, i.path)
                if key not in seen:
                    seen.add(key)
                    sample_issues.append(Issue(i.code, i.path, f"{i.message} [표본: {_describe(r, tv, iv, host)}]", i.hint, i.level))
            continue
        good.append((tv, iv, host, out))
        for b in out["body"]:
            seen_ids.add(b["id"].split("[")[0].split(".")[0])
        seen_ids.update(out["void_ids"])
    issues += sample_issues
    checks["values"] = _status(sample_issues, _VALUE_CODES)
    checks["geometry"] = "fail" if any(i.level == "error" and not i.code.startswith(_VALUE_CODES) for i in sample_issues) else "pass"
    for i, s in enumerate(r.solids):
        if s.id not in seen_ids and good:
            issues.append(Issue("W_SOLID_NEVER_VISIBLE", ptr("solids", i), f"어떤 표본에서도 형상 {s.id} 이 만들어지지 않는다",
                                "when 조건이 항상 거짓이거나 repeat 횟수가 항상 0 이 아닌지 확인하세요", "warning"))
    void_issues = check_void_overlap(r, good) if good else []
    issues += void_issues
    if void_issues:
        checks["geometry"] = "fail"
    if any(i.level == "error" for i in sample_issues + void_issues) or not good or level == "static":
        return done(r)

    # ---- IFC 스키마 대조
    si = check_ifc_schema(r, good)
    issues += si
    checks["ifc_schema"] = _status(si)
    pi = check_psets_standard(r, good)
    issues += pi
    checks["psets_standard"] = _status(pi)
    if checks["ifc_schema"] == "fail" or checks["psets_standard"] == "fail":
        return done(r)

    # ---- IFC 투영 + 유효성 + 메시
    bi, shape_issues, value_issues = build_and_check(r, plan, good, stats, deadline)
    issues += bi
    checks["ifc_projection"] = _status(bi)
    if checks["ifc_projection"] == "pass":
        issues += shape_issues
        checks["geometry_shape"] = _status(shape_issues)
        issues += value_issues
        checks["psets_values"] = _status(value_issues)
    return done(r)


# ---------------------------------------------------------------- 공극 교차 검사 (경계 상자, 순수 파이썬)
_AXI = {"x": 0, "y": 1, "z": 2}


def _aabb(it: dict) -> tuple[list[float], list[float]]:
    """해석된 형상(돌출/스윕 구간) 하나의 축 정렬 경계 상자. 스윕 단면은 반대각선 길이로 부풀려 보수적으로(거짓 '빗나감'이 없게) 잡는다."""
    lo, hi = [0.0] * 3, [0.0] * 3
    if it["k"] == "extrude":
        au, av, ae = (_AXI[a] for a in ev._PLANE_AXES[it["plane"]])
        p = it["profile"]
        if p["shape"] == "rect":
            (u0, u1), (v0, v1) = p["u"], p["v"]
        elif p["shape"] == "circle":
            u0, u1 = p["center"][0] - p["r"], p["center"][0] + p["r"]
            v0, v1 = p["center"][1] - p["r"], p["center"][1] + p["r"]
        else:
            us, vs = [q[0] for q in p["points"]], [q[1] for q in p["points"]]
            u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        lo[au], hi[au], lo[av], hi[av] = u0, u1, v0, v1
        lo[ae], hi[ae] = it["range"]
        return lo, hi
    pr = it["profile"]
    pad = math.hypot(pr["w"], pr["h"]) / 2.0 if pr["shape"] == "rect" else pr["r"]
    for k in range(3):
        lo[k] = min(it["start"][k], it["end"][k]) - pad
        hi[k] = max(it["start"][k], it["end"][k]) + pad
    return lo, hi


def _overlap(a: tuple, b: tuple, eps: float = 1e-6) -> bool:
    return all(a[0][k] < b[1][k] - eps and b[0][k] < a[1][k] - eps for k in range(3))


def _base_id(i: str) -> str:
    return i.split("[")[0].split(".")[0]


def check_void_overlap(r: Recipe, good: list) -> list[Issue]:
    """공극이 어떤 표본에서도 자기 대상 solid 의 경계 상자와 겹치지 않으면 E_VOID_MISS. (반복 공극 중 일부만 빗나가는 것은 정상으로 본다)"""
    present: set[str] = set()
    hit: set[str] = set()
    for _tv, _iv, _h, out in good:
        for sd in out["body"]:
            cuts = sd.get("cuts")
            if not cuts:
                continue
            box = _aabb(sd)
            for cu in cuts:
                b = _base_id(cu["id"])
                present.add(b)
                if b not in hit and _overlap(_aabb(cu), box):
                    hit.add(b)
    idx = {s.id: i for i, s in enumerate(r.solids)}
    return [Issue("E_VOID_MISS", ptr("solids", idx.get(b, 0)), f"공극 {b} 가 대상 solid 와 어떤 표본에서도 교차하지 않는다(절단 효과가 없다)",
                  "공극의 위치·range 가 대상 solid 를 지나가는지 확인하세요(좌표는 호스트/패밀리 로컬 mm). 구멍을 뚫으려면 대상보다 넉넉히 길게 잡습니다")
            for b in sorted(present - hit)]


def _describe(r: Recipe, tv: dict, iv: dict, host: dict) -> str:
    diff = []
    for k, p in r.params.items():
        v = (tv if p.kind == "type" else iv).get(k)
        if v != p.default:
            diff.append(f"{k}={v}")
    diff += [f"{k}={v:g}" for k, v in host.items()]
    return ", ".join(diff) or "기본값"


# ---------------------------------------------------------------- IFC 스키마 대조
def _schema():
    import ifcopenshell.ifcopenshell_wrapper as w
    return w.schema_by_name("IFC4")


def _supers(sch, name: str) -> list[str]:
    out, d = [], sch.declaration_by_name(name)
    while d is not None:
        out.append(d.name())
        d = d.supertype()
    return out


def _attr(sch, cls: str, name: str):
    for a in sch.declaration_by_name(cls).all_attributes():
        if a.name() == name:
            return a
    return None


def _attr_kind(a) -> tuple[str, tuple | None]:
    """속성의 값 종류: ('bool'|'number'|'text'|'enum'|'unsupported', 열거값들)"""
    t = a.type_of_attribute()
    try:
        d = t.declared_type()
    except Exception:
        return "unsupported", None
    if hasattr(d, "enumeration_items"):
        return "enum", tuple(d.enumeration_items())
    s = str(d).lower()
    if "boolean" in s or "logical" in s:
        return "bool", None
    if any(k in s for k in ("real", "integer", "number")):
        return "number", None
    if "string" in s:
        return "text", None
    return "unsupported", None


def check_attr_values(sch, cls: str, attrs: dict, base: str) -> list[Issue]:
    out: list[Issue] = []
    for k, v in attrs.items():
        p = f"{base}/{k}"
        if k in _FORBIDDEN_ATTRS:
            out.append(Issue("E_IFC_ATTR_FORBIDDEN", p, f"{k} 는 코어가 정하므로 레시피에서 지정할 수 없다",
                             "Name 은 배치 때, PredefinedType 은 ifc.predefined 로 지정합니다"))
            continue
        a = _attr(sch, cls, k)
        if a is None:
            out.append(Issue("E_IFC_ATTR_UNKNOWN", p, f"{cls} 에 속성 {k} 가 없다",
                             "IFC4 스키마의 속성 이름을 쓰세요(대소문자 구분). 예: IfcDoor 는 OverallWidth, OverallHeight, OperationType"))
            continue
        kind, items = _attr_kind(a)
        if kind == "unsupported":
            out.append(Issue("E_IFC_ATTR_UNSUPPORTED", p, f"{cls}.{k} 는 단순 값(숫자·불리언·문자열·열거)이 아니라 레시피로 지정할 수 없다"))
        elif kind == "enum" and v not in items:
            out.append(Issue("E_IFC_ENUM", p, f"{cls}.{k}={v!r} 는 열거값이 아니다", "허용: " + ", ".join(items)))
        elif kind == "bool" and not isinstance(v, bool):
            out.append(Issue("E_IFC_ATTR_TYPE", p, f"{cls}.{k} 는 불리언이어야 한다(받은 값: {v!r})"))
        elif kind == "number" and (isinstance(v, bool) or not isinstance(v, (int, float))):
            out.append(Issue("E_IFC_ATTR_TYPE", p, f"{cls}.{k} 는 숫자여야 한다(받은 값: {v!r})"))
        elif kind == "text" and not isinstance(v, str):
            out.append(Issue("E_IFC_ATTR_TYPE", p, f"{cls}.{k} 는 문자열이어야 한다(받은 값: {v!r})"))
    return out


def check_ifc_schema(r: Recipe, good: list) -> list[Issue]:
    sch = _schema()
    out: list[Issue] = []
    cls = r.ifc.class_
    tcls = r.ifc.type_class or cls + "Type"
    try:
        sup = _supers(sch, cls)
    except Exception:
        return [Issue("E_IFC_CLASS", "/ifc/class", f"IFC4 에 없는 클래스다: {cls}", "예: IfcDoor, IfcWindow, IfcColumn, IfcFurniture")]
    if "IfcElement" not in sup:
        out.append(Issue("E_IFC_CLASS", "/ifc/class", f"{cls} 는 IfcElement 가 아니다", "건축 요소 클래스를 쓰세요(IfcDoor, IfcWindow, IfcColumn, IfcFurniture, IfcBeam ...)"))
    elif sch.declaration_by_name(cls).is_abstract():
        out.append(Issue("E_IFC_CLASS", "/ifc/class", f"{cls} 는 추상 클래스라 인스턴스를 만들 수 없다"))
    try:
        tsup = _supers(sch, tcls)
    except Exception:
        out.append(Issue("E_IFC_TYPE_CLASS", "/ifc/type_class", f"IFC4 에 없는 타입 클래스다: {tcls}",
                         f"{cls} 의 타입 클래스는 보통 {cls}Type 이다"))
        return out
    if "IfcElementType" not in tsup:
        out.append(Issue("E_IFC_TYPE_CLASS", "/ifc/type_class", f"{tcls} 는 IfcElementType 가 아니다"))
        return out
    pa = _attr(sch, tcls, "PredefinedType")
    if pa is None:
        if r.ifc.predefined != "NOTDEFINED":
            out.append(Issue("E_IFC_ENUM", "/ifc/predefined", f"{tcls} 에는 PredefinedType 이 없다", "predefined 를 NOTDEFINED 로 두거나 지우세요"))
    else:
        kind, items = _attr_kind(pa)
        if r.ifc.predefined not in (items or ()):
            out.append(Issue("E_IFC_ENUM", "/ifc/predefined", f"{tcls}.PredefinedType={r.ifc.predefined!r} 는 열거값이 아니다",
                             "허용: " + ", ".join(items or ())))
    # 평가된 값(모든 표본)으로 열거값·값 종류를 본다: 식으로 정해지는 속성은 표본마다 값이 다르다
    seen: set = set()
    for _tv, _iv, _h, o in good:
        for base, c, attrs in (("/ifc/type_attrs", tcls, o["type_attrs"]), ("/ifc/attrs", cls, o["attrs"])):
            for i in check_attr_values(sch, c, attrs, base):
                if (i.code, i.path, i.message) not in seen:
                    seen.add((i.code, i.path, i.message))
                    out.append(i)
    # USERDEFINED 는 ElementType/ObjectType 이 필요하다 (IFC4 WHERE 규칙)
    if r.ifc.predefined == "USERDEFINED":
        if "ElementType" not in r.ifc.type_attrs:
            out.append(Issue("E_IFC_USERDEFINED", "/ifc/type_attrs", "PredefinedType=USERDEFINED 인 타입은 ElementType 이 필요하다",
                             'type_attrs 에 "ElementType": "이름" 을 넣으세요'))
        if "ObjectType" not in r.ifc.attrs and _attr(sch, cls, "ObjectType") is not None:
            out.append(Issue("E_IFC_USERDEFINED", "/ifc/attrs", "USERDEFINED 인 인스턴스는 ObjectType 이 필요하다",
                             'attrs 에 "ObjectType": "이름" 을 넣으세요'))
    return out


# ---------------------------------------------------------------- 표준 속성셋
def _kind_of_measure(name: str | None) -> str:
    if name in ("IfcBoolean", "IfcLogical"):
        return "bool"
    if name in ("IfcLabel", "IfcIdentifier", "IfcText", "IfcDescriptiveMeasure", "IfcURIReference"):
        return "text"
    if name is None:
        return "any"
    return "number"


def check_psets_standard(r: Recipe, good: list) -> list[Issue]:
    import ifcopenshell.util.pset as up
    tmpl = up.get_template("IFC4")
    out: list[Issue] = []
    cls = r.ifc.class_
    for pn, ps in r.psets.items():
        base = ptr("psets", pn)
        if not pn.startswith(("Pset_", "Qto_")):
            continue   # 사용자 속성셋(예: DoBIM_*): 표준 대조 대상이 아니다
        t = tmpl.get_by_name(pn)
        if t is None:
            out.append(Issue("E_PSET_NOT_STANDARD", base, f"{pn} 는 buildingSMART 표준 속성셋이 아니다",
                             "Pset_ 접두사는 표준 전용입니다. 사용자 속성셋은 DoBIM_ 처럼 다른 접두사를 쓰세요"))
            continue
        if t.ApplicableEntity and cls not in [x.strip() for x in str(t.ApplicableEntity).replace("/", ",").split(",")]:
            out.append(Issue("E_PSET_APPLICABLE", base, f"{pn} 는 {t.ApplicableEntity} 에 적용되는 속성셋이다(이 패밀리: {cls})",
                             "클래스에 맞는 표준 속성셋(예: IfcDoor → Pset_DoorCommon)을 쓰세요"))
        templates = {p.Name: p for p in t.HasPropertyTemplates}
        for prop in ps.props:
            pp = f"{base}/props/{prop}"
            pt = templates.get(prop)
            if pt is None:
                near = sorted(templates)[:12]
                out.append(Issue("E_PSET_PROP_UNKNOWN", pp, f"{pn} 에 속성 {prop} 가 없다", "표준 속성: " + ", ".join(near)))
                continue
            kind = _kind_of_measure(pt.PrimaryMeasureType)
            enum_items = None
            if pt.Enumerators is not None:
                enum_items = [e.wrappedValue for e in pt.Enumerators.EnumerationValues]
                kind = "text"
            seen = set()
            for _tv, _iv, _h, o in good:
                v = o["psets"][ps.on].get(pn, {}).get(prop)
                if v is None or repr(v) in seen:
                    continue
                seen.add(repr(v))
                bad = ((kind == "bool" and not isinstance(v, bool)) or (kind == "text" and not isinstance(v, str))
                       or (kind == "number" and (isinstance(v, bool) or not isinstance(v, (int, float)))))
                if bad:
                    out.append(Issue("E_PSET_VALUE_TYPE", pp, f"{pn}.{prop} 는 {pt.PrimaryMeasureType} 이므로 {kind} 값이어야 한다(받은 값: {v!r})"))
                elif enum_items is not None and v not in enum_items:
                    out.append(Issue("E_PSET_ENUM", pp, f"{pn}.{prop}={v!r} 는 열거값이 아니다", "허용: " + ", ".join(enum_items)))
    return out


def _check_cut_reduces_volume(inst_bodies: list, settings, over) -> list[Issue]:
    """공극이 붙은 solid 마다(첫 인스턴스에서 한 번) 절단 전후 부피를 비교한다. 줄지 않으면 공극이 solid 를 전부 지우거나 스치기만 하는 것이다.
    (IfcOpenShell 은 불리언 결과가 비면 원본 형상으로 되돌려 주므로, 인스턴스 전체 부피 > 0 검사로는 못 잡는다)"""
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.shape as ush
    from dobim_core.family import ifcproj as fp
    out: list[Issue] = []
    scratch = ifcopenshell.file(schema="IFC4")
    seen: set[str] = set()

    def volume(item: dict) -> float:
        sh = ifcopenshell.geom.create_shape(settings, fp.solid_entity(scratch, item))
        return ush.get_volume(sh) if len(sh.verts) else 0.0

    for body in inst_bodies:
        for sd in body:
            if not sd.get("cuts") or _base_id(sd["id"]) in seen:
                continue
            seen.add(_base_id(sd["id"]))
            if over():
                out.append(Issue("E_LIMIT", "/solids", "검증 시간 예산을 넘어 공극 부피 검사에서 멈췄다"))
                return out
            try:
                vc = volume(sd)
                vu = volume({k: v for k, v in sd.items() if k != "cuts"})
            except Exception as ex:
                out.append(Issue("E_SHAPE_VOLUME", "/solids", f"형상 {sd['id']} 의 절단 결과를 만들 수 없다: {str(ex)[:140]}",
                                 "공극이 solid 를 모두 지우지 않는지, 공극 치수가 퇴화하지 않았는지 확인하세요"))
                continue
            if vc <= 0 or vc >= vu * (1 - 1e-6):
                out.append(Issue("E_SHAPE_VOLUME", "/solids",
                                 f"형상 {sd['id']} 은 공극으로 절단해도 부피가 줄지 않는다(절단 전 {vu:.6g}, 후 {vc:.6g}): 공극이 solid 를 전부 지우거나 거의 닿지 않는다",
                                 "공극이 solid 의 일부만 지나가도록 위치·range 를 조정하세요(전부 지우면 IfcOpenShell 이 원본을 돌려줘 오류가 가려집니다)"))
    return out


# ---------------------------------------------------------------- IFC 투영·유효성·메시
def _gid(label: str) -> str:
    import ifcopenshell.guid
    return ifcopenshell.guid.compress(uuid.uuid5(uuid.UUID("5d0b1a50-0000-4a00-8000-0d0b1a5000d4"), label).hex)


def _type_samples(r: Recipe, good: list) -> list:
    """타입 수준 값이 서로 다른 표본 대표 최대 6개(기본 + 매개변수마다 한 변형)."""
    out, seen = [], set()
    for tv, iv, host, o in good:
        key = ev.canon(tv)
        if key in seen:
            continue
        seen.add(key)
        out.append((tv, iv, host, o))
        if len(out) >= 6:
            break
    return out


def build_and_check(r: Recipe, plan, good: list, stats: dict, deadline: float | None = None) -> tuple[list[Issue], list[Issue], list[Issue]]:
    """코어와 같은 IfcModel 투영으로 IFC 를 만든다. (투영 오류, 형상 오류, 속성셋 값 오류). deadline 은 perf_counter 기준 마감 시각."""
    def over() -> bool:
        return deadline is not None and time.perf_counter() > deadline

    def limit(where: str) -> Issue:
        return Issue("E_LIMIT", "/ifc", f"검증 시간 예산을 넘어 {where}에서 멈췄다", "공극 수·반복 횟수·인스턴스 변형을 줄여 형상을 단순하게 하세요")

    from dobim_core.ifc_model import IfcModel
    from dobim_core.family import spec as FS
    build_i: list[Issue] = []
    shape_i: list[Issue] = []
    val_i: list[Issue] = []
    try:
        m = IfcModel("5d0b1a50-0000-4a00-8000-0d0b1a5000d4")
        lv = _gid("level")
        m.create_identity(lv, "level")
        m.project(lv, {"kind": "level", "name": "L1", "elevation": 0.0, "intent": {}})
        walls = {}
        for label, t in (("A", 200.0), ("B", 300.0)):
            wid = _gid("wall" + label)
            m.create_identity(wid, "wall")
            m.project(wid, {"kind": "wall", "name": "W" + label, "level": lv, "origin": (0.0, 0.0 if label == "A" else 5000.0), "xdir": (1.0, 0.0),
                            "footprint": ((0.0, -t / 2), (8000.0, -t / 2), (8000.0, t / 2), (0.0, t / 2)), "height": 3000.0, "intent": {}})
            walls[label] = (wid, t)
        rhash = ev.digest(r.model_dump(mode="json", by_alias=True))
        raw = r.model_dump(mode="json", by_alias=True)
        expected: list[tuple[str, str, dict]] = []   # (eid, 라벨, {pset: {prop: value}})
        inst_ids: list[str] = []
        inst_bodies: list[list] = []   # inst_ids 와 같은 순서의 해석된 형상(부피 감소 검사용)
        alt_iv = next((iv for tv, iv, h, o in good if iv != good[0][1] and ev.canon(tv) == ev.canon(good[0][0])), good[0][1])
        for ti, (tv, iv, host, o) in enumerate(_type_samples(r, good)):
            tid = _gid(f"type{ti}")
            tp = {"name": f"{r.name} T{ti}", "recipe": raw, "recipe_hash": rhash, "type_values": tv}
            m.create_identity(tid, "ftype", r.ifc.type_class or r.ifc.class_ + "Type")
            m.project(tid, {**FS.ftype_spec(tp), "intent": {"kind": "ftype", "params": tp}})
            expected.append((tid, f"type{ti}", o["psets"]["type"]))
            for ii, ivx in enumerate([iv] + ([alt_iv] if ti == 0 else [])):
                if over():
                    build_i.append(limit("IFC 투영"))
                    return build_i, shape_i, val_i
                eid = _gid(f"inst{ti}.{ii}")
                m.create_identity(eid, "finst", r.ifc.class_)
                wl = "B" if ii == 1 else "A"
                hosted = r.host is not None
                hctx = {"host_thickness": walls[wl][1], "host_height": 3000.0} if hosted else None
                e2 = ev.evaluate(r, plan, tv, ivx, hctx)
                cut = e2["cut"]
                ip = {"name": f"{r.id.split('.')[0][:1].upper()}-{ti}{ii}", "values": ivx, "elevation": 0.0, "rotation": 30.0 * ii}
                if hosted:
                    ip["offset"] = 4000.0
                else:
                    ip["position"] = [1000.0 + 1500.0 * ii, 1000.0 * (ti + 1)]
                spec = FS.finst_spec(tp, ip, tid, walls[wl][0] if hosted else None, lv, hctx)
                m.project(eid, {**spec, "intent": {"kind": "finst"}})
                inst_ids.append(eid)
                inst_bodies.append(e2["body"])
                expected.append((eid, f"inst{ti}.{ii}", e2["psets"]["instance"]))
                if hosted and cut is None:
                    pass
        for eid, label, psets in expected:
            for pn, props in psets.items():
                for k, v in props.items():
                    m.set_prop(eid, pn, k, v)
    except Exception as ex:   # 투영 도중 예외: 레시피가 IFC 로 투영되지 않는다
        build_i.append(Issue("E_IFC_BUILD", "/ifc", f"IFC 투영 중 예외: {type(ex).__name__}: {str(ex)[:240]}",
                             "ifc.attrs / ifc.type_attrs 값의 종류, 형상 연산을 확인하세요"))
        return build_i, shape_i, val_i

    v = m.validate(express=True)
    stats["ifc_validate_errors"] = v["errors"]
    for msg in v["samples"][:5]:
        build_i.append(Issue("E_IFC_INVALID", "/ifc", "ifcopenshell.validate 오류: " + msg.splitlines()[0][:200],
                             "표현 항목·열거값·필수 속성을 확인하세요"))
    if v["errors"] > len(v["samples"][:5]):
        build_i.append(Issue("E_IFC_INVALID", "/ifc", f"ifcopenshell.validate 오류 총 {v['errors']}건"))
    try:
        text = m.f.to_string()
        stats["ifc_bytes"] = len(text.encode("utf-8"))
        stats["ifc_entities"] = len(list(m.f))
        stats["recipe_json_bytes"] = len(ev.canon(raw).encode("utf-8"))
    except Exception:
        pass

    # ---- 메시: 인스턴스마다 실제 형상을 만들어 본다
    try:
        import ifcopenshell.geom
        import ifcopenshell.util.shape as ush
        st = ifcopenshell.geom.settings()
        shapes = 0
        for eid in inst_ids:
            if over():
                shape_i.append(limit("메시 생성"))
                break
            e = m.ents[eid]
            try:
                sh = ifcopenshell.geom.create_shape(st, e)
            except Exception as ex:
                shape_i.append(Issue("E_SHAPE_FAIL", "/solids", f"형상 생성 실패({e.Name}): {str(ex)[:160]}",
                                     "공극(cuts)·스윕 단면 치수·돌출 범위를 확인하세요"))
                continue
            vs = list(sh.geometry.verts)
            if not vs:
                shape_i.append(Issue("E_SHAPE_EMPTY", "/solids", f"형상이 비어 있다({e.Name})", "when 조건 때문에 모든 solid 가 꺼지지 않았는지 확인하세요"))
                continue
            ext = [max(vs[i::3]) - min(vs[i::3]) for i in range(3)]
            if min(ext) <= 1e-6:
                shape_i.append(Issue("E_SHAPE_FLAT", "/solids", f"형상이 한 축으로 납작하다({e.Name}): 치수 {[round(x * 1000, 3) for x in ext]}mm"))
            vol = ush.get_volume(sh.geometry)
            if not vol > 0:
                shape_i.append(Issue("E_SHAPE_VOLUME", "/solids", f"형상 부피가 0 이하다({e.Name})", "공극이 solid 를 모두 지우지 않는지 확인하세요"))
            shapes += 1
        stats["meshes_ok"] = shapes
        shape_i += _check_cut_reduces_volume(inst_bodies, st, over)
    except ImportError as ex:
        shape_i.append(Issue("E_SHAPE_FAIL", "/solids", f"ifcopenshell.geom 을 쓸 수 없다: {ex}"))

    # ---- 속성셋 값 일치: IFC 에서 읽어 레시피가 평가한 값과 비교
    for eid, label, psets in expected:
        got = m.get_psets(eid)
        for pn, props in psets.items():
            for k, want in props.items():
                have = got.get(pn, {}).get(k, "<없음>")
                same = have == want and type(have) is type(want) or (isinstance(have, (int, float)) and isinstance(want, (int, float))
                                                                      and not isinstance(have, bool) and not isinstance(want, bool) and have == want)
                if not same:
                    val_i.append(Issue("E_PSET_MISMATCH", f"/psets/{pn}/props/{k}",
                                       f"IFC 에 쓴 값이 평가값과 다르다({label}): 기대 {want!r}, IFC {have!r}"))
    return build_i, shape_i, val_i
