# SPDX-License-Identifier: MPL-2.0
"""외래 IFC 가져오기 계획 (S2, ADR-0005): 들어온 파일의 요소를 authored / foreign / detached 로 분류한다.

분류 규칙(요소 = 코어 뼈대가 아닌 IfcProduct):
- DoBIM_Intent 가 있고, 현재 코어 스키마로 해석되고(레벨·벽·문, 패밀리 타입·인스턴스: ADR-0014), 가리키는 대상(레벨·호스트·조인 상대, 인스턴스의 타입)이
  있으면 의도를 복원해 후보가 된다. 패밀리 후보는 레시피·값을 S4 의 규칙으로 평가해 일관될 때만 후보가 된다(family.restore).
- 후보의 의도를 새 IFC 모델에 다시 투영해, 요소 단위 투영 클로저 해시(ifc_model.Closure)를 들어온 요소의 해시와 비교한다.
  같으면 authored(의도가 형상의 정본), 다르면 detached(들어온 형상을 그대로 동결, 마지막 의도는 낡은 사본으로 남는다).
- 후보가 아니면 foreign(IFC 가 형상의 정본, 속성만 편집). 패밀리 타입(IfcTypeObject)은 후보일 때만 요소(ftype)가 되고, 아니면 요소가 아닌 채 파일에 남는다.
- 분리된 패밀리 타입의 인스턴스는 타입이 낡은 정의이므로 함께 분리한다(인스턴스를 다시 투영하면 변조된 타입의 표현 지도를 가리키게 된다).
- 길이 단위가 밀리미터가 아니면(의도의 값은 mm 다) 후보 전체를 detached 로 둔다: 단위는 프로젝트 수준의 변경이라 요소 하나하나의 클로저에
  나타나지 않기 때문이다. 분리된 레벨이 있으면 그 위의 저작 요소가 레벨 배치를 따라 움직인다는 경고를 더한다.
- 뷰·치수·태그(IFC 신원이 없는 의도 요소)의 정의는 프로젝트의 속성셋 DoBIM_Defs 에 실려 있다(ADR-0015). 정의마다 viewdefs.parse_def 로 검증하고(모양·범위·변조 해시),
  가리키는 레벨·뷰·대상이 파일 안에서 어떤 상태인지 보고 되살린다. 레벨·벽·문이 저작이면 그대로, 파일에 없으면 고아로(커맨드로 지운 뒤와 같다),
  있는데 저작이 아니면(외래·분리) 되살리지 않는다(낡은 값을 재게 된다). 되살리지 않은 정의는 파일에 그대로 남고 내보낼 때도 건드리지 않는다.
- 뼈대(Site/Building)는 구조·이름으로 알아보고 위경도·주소 같은 비형상 속성은 보지 않는다. 뼈대의 형상이 바뀌었으면 경고한다(ifc_model.skeleton_check).

이 모듈은 파일을 읽어 계획(ImportPlan)을 만들 뿐 프로젝트를 만들지 않는다. 엔진이 계획을 SQLite 에 적는다.
"""
from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field

import ifcopenshell
import ifcopenshell.util.element as ue
import ifcopenshell.util.unit as uunit

from dobim_core import queries as Q
from dobim_core.errors import DobimError
from dobim_core.graph import Graph
from dobim_core.family import restore as FR
from dobim_core import annot as AN
from dobim_core import viewdefs as VD
from dobim_core.kinds import ELEMENT_KINDS, FAMILY_OF_KIND, IMPORTABLE_BY_CLASS
from dobim_core.ifc_model import (INTENT_PSET, Closure, IfcModel, closure_diff, open_ifc_bytes, skeleton_check)

MAX_IMPORT_BYTES = 256 * 1024 * 1024
MAX_MM = 1e9   # 의도 숫자의 상한(1000 km). 이보다 큰 값은 제곱하는 계산이 넘치므로 해석하지 않고 외래로 둔다
KIND_OF = IMPORTABLE_BY_CLASS   # 요소 종류 표의 정본은 kinds.py 다(의도를 복원할 수 있는 종류만 뽑은 것)
FAMILY = FAMILY_OF_KIND
_ID = re.compile(r"^[0-9A-Za-z_$]{22}$")
NAME_MAX = 255


@dataclass
class ImportPlan:
    schema: str
    # {id, kind, ifc_class, origin, family_id, params, projection_hash, spec_hash}
    # projection_hash = 가져오는 순간 들어온 요소의 클로저 해시(불변, 조회용). 변조 판정은 가져오는 순간 한 번뿐이다: 이후 외부 파일이 바뀌면
    # 다시 가져와야 알 수 있다. spec_hash = 가져오는 순간 저작 요소의 투영 명세 해시(명세가 그대로면 첫 투영에서 들어온 엔티티를 그대로 둔다).
    rows: list[dict] = field(default_factory=list)
    rels: list[tuple] = field(default_factory=list)      # (kind, src, dst)
    warnings: list[str] = field(default_factory=list)
    detached: list[dict] = field(default_factory=list)   # {id, reasons}
    skeleton: bool = False
    skipped: int = 0                                      # 요소로 세지 않은 제품 수(저작 문의 개구부)

    def counts(self) -> dict:
        c = {"authored": 0, "foreign": 0, "detached": 0}
        for r in self.rows:
            c[r["origin"]] += 1
        return c


# ---------------------------------------------------------------- 의도 해석
def _num(v, *, pos=False, nonneg=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError("유한한 숫자가 아니다")
    v = float(v)
    if abs(v) > MAX_MM:
        raise ValueError("값이 범위(±1e9)를 벗어났다")
    if pos and not v > 0:
        raise ValueError("0 보다 커야 한다")
    if nonneg and v < 0:
        raise ValueError("0 이상이어야 한다")
    return v


def _name(v):
    if not isinstance(v, str) or not 1 <= len(v) <= 64 or "\x00" in v or any(0xD800 <= ord(c) <= 0xDFFF for c in v):
        raise ValueError("이름이 올바르지 않다")
    return v


def _parse_intent(kind: str, text: str) -> dict:
    """DoBIM_Intent 텍스트를 현재 코어 스키마로 해석한다. 못 하면 ValueError. 숫자는 float 로 맞춘다(저작 행과 같은 모양)."""
    d = json.loads(text)
    extra = {"level": (), "wall": ("level", "joins"), "door": ("host",)}[kind]
    if not isinstance(d, dict) or set(d) != {"kind", "params", "family_id", *extra} or d["kind"] != kind or d["family_id"] != FAMILY[kind]:
        raise ValueError("의도 모양이 현재 스키마와 다르다")
    p = d["params"]
    if not isinstance(p, dict):
        raise ValueError("params 가 객체가 아니다")
    if kind == "level":
        keys, out = {"name", "elevation"}, None
        if set(p) != keys:
            raise ValueError("레벨 params 키가 다르다")
        out = {"name": _name(p["name"]), "elevation": _num(p["elevation"])}
    elif kind == "wall":
        if set(p) != {"name", "axis", "thickness", "height"} or not isinstance(p["axis"], list) or len(p["axis"]) != 4:
            raise ValueError("벽 params 가 다르다")
        axis = [_num(x) for x in p["axis"]]
        if (axis[2] - axis[0]) ** 2 + (axis[3] - axis[1]) ** 2 < 1.0:
            raise ValueError("벽 길이가 1mm 미만이다")
        out = {"name": _name(p["name"]), "axis": axis, "thickness": _num(p["thickness"], pos=True), "height": _num(p["height"], pos=True)}
    else:
        if set(p) != {"name", "width", "height", "sill", "offset"}:
            raise ValueError("문 params 가 다르다")
        out = {"name": _name(p["name"]), "width": _num(p["width"], pos=True), "height": _num(p["height"], pos=True),
               "sill": _num(p["sill"], nonneg=True), "offset": _num(p["offset"])}
    res = {"kind": kind, "params": out}
    if kind == "wall":
        lv, joins = d["level"], d["joins"]
        if not isinstance(lv, str) or not _ID.match(lv) or not isinstance(joins, list) or not all(isinstance(j, str) and _ID.match(j) for j in joins):
            raise ValueError("레벨·조인 참조가 올바르지 않다")
        res["level"], res["joins"] = lv, sorted(set(joins))
    elif kind == "door":
        if not isinstance(d["host"], str) or not _ID.match(d["host"]):
            raise ValueError("호스트 참조가 올바르지 않다")
        res["host"] = d["host"]
    return res


def _finst_dependency_error(g: str, it: dict, cand: dict, cls_of: dict) -> str | None:
    """패밀리 인스턴스 후보가 가리키는 타입·호스트·레벨이 후보로 있고, 레시피로 평가해 의도가 일관된지 확인한다(없거나 어긋나면 이유 문장)."""
    t = cand.get(it["type"])
    if t is None or t["kind"] != "ftype":
        return "패밀리 타입이 없다"
    host = cand.get(it["host"]) if "host" in it else None
    if "host" in it:
        if host is None or host["kind"] != "wall":
            return "호스트 벽이 없다"
    elif cand.get(it["level"], {}).get("kind") != "level":
        return "레벨 참조가 없다"
    try:
        cls = FR.check_finst(it, {"params": t["params"], "family_id": t["family_id"]}, host["params"] if host else None)
    except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as ex:
        return f"패밀리 값이 일관되지 않다({str(ex)[:60]})"
    if cls_of[g] != cls:
        return f"IFC 클래스({cls_of[g]})가 레시피의 클래스({cls})와 다르다"
    return None


class _Inputs:
    """계획 단계의 그래프 입력(요소 행과 관계만 있으면 투영 명세를 계산할 수 있다)."""

    def __init__(self, st):
        self.st = st

    def load(self, key):
        st, t = self.st, key[0]
        if t == "el":
            return st.el.get(key[1])
        if t == "rel_out":
            return tuple(st.out.get((key[1], key[2]), ()))
        if t == "rel_in":
            return tuple(st.inn.get((key[1], key[2]), ()))
        raise KeyError(key)


def _read_defs(f, plan: "ImportPlan") -> dict[str, object]:
    """프로젝트 속성셋 DoBIM_Defs 의 속성 {이름: 텍스트(문자열이 아니면 None)}. 프로젝트가 하나가 아니거나 속성셋이 둘 이상이면 모호해서 읽지 않는다.
    같은 이름의 속성이 둘이면 어느 쪽도 믿지 않는다(그 이름은 빠진다)."""
    projs = f.by_type("IfcProject")
    if len(projs) != 1:
        return {}
    psets = []
    for r in projs[0].IsDefinedBy or ():
        d = r.RelatingPropertyDefinition if r.is_a("IfcRelDefinesByProperties") else None
        if isinstance(d, ifcopenshell.entity_instance) and d.is_a("IfcPropertySet") and d.Name == VD.DEFS_PSET:
            psets.append(d)
    if not psets:
        return {}
    if len(psets) > 1:
        plan.warnings.append(f"속성셋 {VD.DEFS_PSET} 가 {len(psets)}개라 뷰·주석 정의를 읽지 않는다")
        return {}
    out: dict[str, object] = {}
    dup: set[str] = set()
    for p in psets[0].HasProperties or ():
        name = getattr(p, "Name", None)
        if not isinstance(name, str):
            continue
        if name in out or name in dup:
            dup.add(name)
            out.pop(name, None)
            continue
        v = getattr(p.NominalValue, "wrappedValue", None) if p.is_a("IfcPropertySingleValue") and p.NominalValue is not None else None
        out[name] = v if isinstance(v, str) else None
        if len(out) > VD.MAX_DEFS:
            plan.warnings.append(f"뷰·주석 정의가 {VD.MAX_DEFS}개를 넘어 나머지는 읽지 않는다")
            break
    for name in sorted(dup)[:20]:
        plan.warnings.append(f"{name[:22]}: 같은 이름의 정의 속성이 둘 이상이라 되살리지 않는다")
    return out


def _restore_defs(f, rows: dict, rels: set, taken: set, plan: "ImportPlan") -> None:
    """뷰·치수·태그를 되살려 rows·rels 에 더한다(ADR-0015). rows 는 요소 판정이 끝난 상태(외래 행 포함), taken 은 파일 안의 모든 id(제품·타입·뼈대)다.
    되살리지 못한 정의는 이유를 경고로 알리고 건너뛴다(오류로 죽지 않는다)."""
    defs = _read_defs(f, plan)
    if not defs:
        return
    skip = lambda eid, why: plan.warnings.append(f"{eid[:22]}: 뷰·주석 정의를 되살리지 않는다({why})")   # noqa: E731
    parsed: dict[str, dict] = {}
    for eid in sorted(defs):
        if eid in taken or eid in rows:
            skip(eid, "정의 id 가 요소의 GlobalId 와 겹친다")
            continue
        try:
            parsed[eid] = VD.parse_def(eid, defs[eid])
        except ValueError as ex:
            skip(eid, str(ex)[:100])
    names: dict[str, list] = {}
    for eid, d in parsed.items():
        if d["kind"] == "view":
            names.setdefault(d["params"]["name"], []).append(eid)
    for nm, ids in sorted(names.items()):
        if len(ids) > 1:   # 뷰 이름은 유일해야 한다(view.create 의 규칙): 겹치면 어느 쪽이 원본인지 알 수 없다
            for eid in ids:
                del parsed[eid]
                skip(eid, "같은 이름의 뷰가 더 있다")

    def state(g):   # (종류, 출처) 또는 None(파일에 없다)
        r = rows.get(g)
        if r is not None:
            return r["kind"], r["origin"]
        return ("?", "foreign") if g in taken else None

    ok: dict[str, dict] = {}
    for eid in sorted(e for e, d in parsed.items() if d["kind"] == "view"):
        p = parsed[eid]["params"]
        if p["type"] == "plan":
            st = state(p["level"])
            if st is not None and st != ("level", "authored"):   # 레벨이 없으면 고아 뷰(저작 상태에서도 생긴다), 있는데 저작 레벨이 아니면 낡은 값을 쓴다
                skip(eid, "평면 뷰가 가리키는 레벨이 저작이 아니다(분리·외래·다른 종류)")
                continue
        ok[eid] = parsed[eid]
    for eid in sorted(e for e, d in parsed.items() if d["kind"] != "view"):
        d = parsed[eid]
        p = d["params"]
        v = ok.get(p["view"])
        if v is None:
            skip(eid, "속한 뷰가 없거나 되살려지지 않았다")
            continue
        vt = v["params"]["type"]
        why = None
        if d["kind"] == "dim":
            if ("at" in p) == (vt == "plan"):
                why = "치수의 종류(평면·높이)가 뷰의 종류와 맞지 않는다"
            for k in ("a", "b"):
                rk, rid, _role = AN.parse_ref(p[k])
                st = state(rid)
                if st is not None and st != (rk, "authored"):
                    why = f"치수 참조 {k} 의 대상이 저작 {rk} 가 아니다"
        else:
            st = state(p["target"])
            if vt != "plan":
                why = "태그는 평면 뷰에 둔다"
            elif st is not None and (st[1] != "authored" or not VD.tag_fields_ok(st[0], p["fields"])):
                why = "태그 대상이 저작 벽·문이 아니거나 표시 필드가 맞지 않는다"
        if why:
            skip(eid, why)
            continue
        ok[eid] = d
    for eid, d in ok.items():
        rows[eid] = {"kind": d["kind"], "ifc_class": "", "origin": "authored", "family_id": d["family_id"], "params": d["params"],
                     "projection_hash": None}
        if d["kind"] != "view":
            rels.add(("annot_of", eid, d["params"]["view"]))


def _name_of(p) -> str:
    n = getattr(p, "Name", None)
    return (n if isinstance(n, str) else "")[:NAME_MAX]


# ---------------------------------------------------------------- 계획
def plan_import(data: bytes) -> ImportPlan:
    from dobim_core.engine import State, digest   # 지연 import: 엔진이 이 모듈을 가져온다

    if len(data) > MAX_IMPORT_BYTES:
        raise DobimError("IMPORT_INVALID", f"파일이 너무 크다(상한 {MAX_IMPORT_BYTES // 2**20}MB)", path="/source")
    try:
        f = open_ifc_bytes(data)
    except Exception as ex:
        raise DobimError("IMPORT_INVALID", f"IFC 를 읽을 수 없다({type(ex).__name__})", path="/source") from None
    schema = f.schema
    if schema != "IFC4":
        raise DobimError("UNSUPPORTED_SCHEMA", f"IFC4 만 가져올 수 있다(이 파일: {schema})", path="/source",
                         hint="IFC2x3·IFC4X3 은 S2 범위 밖이다: IFC4 로 다시 내보내세요")
    plan = ImportPlan(schema=schema)
    sk, sk_warning = skeleton_check(f)
    if sk_warning:
        plan.warnings.append(sk_warning)
    infra = {sk[0].GlobalId, sk[1].GlobalId} if sk else set()
    plan.skeleton = bool(sk)
    prods = [p for p in f.by_type("IfcProduct") if p.GlobalId not in infra]
    seen: set[str] = set()
    for p in prods:
        g = p.GlobalId
        if not isinstance(g, str) or not _ID.match(g):
            raise DobimError("IMPORT_INVALID", "GlobalId 가 22자 형식이 아닌 요소가 있다", path="/source",
                             hint="요소를 id 로 가리킬 수 없다: 22자 GlobalId 로 다시 내보내세요")
        if g in seen:
            raise DobimError("IMPORT_INVALID", f"GlobalId 가 겹치는 요소가 있다: {g}", path="/source")
        seen.add(g)
    types = [t for t in f.by_type("IfcTypeObject") if isinstance(getattr(t, "GlobalId", None), str)]
    for t in types:   # 타입 객체도 같은 id 공간을 쓴다(요소 id 로 가리킨다): 제품이나 다른 타입과 겹치면 id 로 가리킬 수 없다
        g = t.GlobalId
        if g in seen:
            raise DobimError("IMPORT_INVALID", f"GlobalId 가 겹치는 요소가 있다: {g}", path="/source")
        seen.add(g)

    # 1) 의도 복원 후보. 문·벽·레벨은 IFC 클래스로 종류를 정하고, 패밀리(ftype/finst)는 의도가 스스로 밝힌 종류를 따른다(인스턴스도 IfcDoor 일 수 있다)
    cand: dict[str, dict] = {}
    cls_of: dict[str, str] = {}   # 패밀리 후보 GlobalId -> 파일 안의 실제 IFC 클래스(레시피의 클래스와 일치하는지 검사한다)
    for p in [*prods, *types]:
        is_type = p.is_a("IfcTypeObject")
        pset = ue.get_pset(p, INTENT_PSET, should_inherit=False)
        text = pset.get("Intent") if pset else None
        if not isinstance(text, str):
            continue
        claimed = FR.claimed_kind(text)
        kind = claimed if claimed in ("ftype", "finst") else KIND_OF.get(p.is_a())
        if is_type and kind != "ftype":
            continue   # 타입 객체는 패밀리 타입 의도만 본다(그 밖의 타입은 요소가 아니라 파일에 그대로 남는다)
        if kind is None:
            continue
        try:
            if kind in ("ftype", "finst"):
                if not _ID.match(p.GlobalId):
                    raise ValueError("GlobalId 가 22자 형식이 아니다")
                d = FR.load_intent(text)
                cand[p.GlobalId] = FR.parse_ftype(d) if kind == "ftype" else FR.parse_finst(d)
                cls_of[p.GlobalId] = p.is_a()
            else:
                cand[p.GlobalId] = _parse_intent(kind, text)
        except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as ex:   # 넘침(OverflowError)도 해석 실패다
            plan.warnings.append(f"{p.GlobalId}: DoBIM_Intent 를 해석할 수 없어 외래로 둔다({str(ex)[:80]})")
    # 2) 가리키는 대상이 있는 것만 남긴다(고정점)
    changed = True
    while changed:
        changed = False
        for g, it in list(cand.items()):
            bad = None
            if it["kind"] == "wall":
                if g in it["joins"] or cand.get(it["level"], {}).get("kind") != "level":
                    bad = "레벨 참조가 없다"
                elif any(cand.get(j, {}).get("kind") != "wall" or g not in cand[j]["joins"] for j in it["joins"]):
                    bad = "조인 상대가 없거나 대칭이 아니다"
                elif any(cand[j]["level"] != it["level"] for j in it["joins"]):
                    bad = "다른 레벨의 벽과 조인돼 있다"
            elif it["kind"] == "door":
                if cand.get(it["host"], {}).get("kind") != "wall":
                    bad = "호스트 벽이 없다"
            elif it["kind"] == "ftype":
                if cls_of[g] != it["ifc_class"]:
                    bad = f"IFC 클래스({cls_of[g]})가 레시피의 타입 클래스({it['ifc_class']})와 다르다"
            elif it["kind"] == "finst":
                bad = _finst_dependency_error(g, it, cand, cls_of)
            if bad:
                del cand[g]
                plan.warnings.append(f"{g}: 의도의 {bad} → 외래로 둔다")
                changed = True

    # 3) 후보를 저작 행으로 만들고 투영 명세를 계산한다
    rows: dict[str, dict] = {}
    rels: set[tuple] = set()
    for g, it in cand.items():
        if it["kind"] in ("ftype", "finst"):
            rows[g] = {"kind": it["kind"], "ifc_class": cls_of[g], "origin": "authored", "family_id": it["family_id"], "params": it["params"]}
        else:
            cls = {"level": "IfcBuildingStorey", "wall": "IfcWall", "door": "IfcDoor"}[it["kind"]]
            rows[g] = {"kind": it["kind"], "ifc_class": cls, "origin": "authored", "family_id": FAMILY[it["kind"]], "params": it["params"]}
        if it["kind"] == "wall":
            rels.add(("on_level", g, it["level"]))
            rels.update(("joins", min(g, j), max(g, j)) for j in it["joins"])
        elif it["kind"] == "door":
            rels.add(("hosts", it["host"], g))
        elif it["kind"] == "finst":
            rels.add(("of_type", g, it["type"]))
            rels.add(("hosts", it["host"], g) if "host" in it else ("on_level", g, it["level"]))
    st = State()
    for g, r in rows.items():
        st.apply_el(g, None, r)
    for k, s_, d_ in rels:
        st.apply_rel(k, s_, d_, True)
    graph = Graph(_Inputs(st))

    scratch = IfcModel(str(uuid.UUID(int=0)))
    projected: set[str] = set()
    prev_kind = None
    for g in sorted(rows, key=lambda g: (ELEMENT_KINDS[rows[g]["kind"]].order, g)):
        if rows[g]["kind"] != prev_kind:   # 종류가 바뀌면 모은 관계를 반영한다(문의 개구부는 벽이 담긴 층을 따라가는 등 아래 종류가 윗 종류의 관계를 읽는다)
            scratch.flush_rels()
            prev_kind = rows[g]["kind"]
        # 호스트가 없는 패밀리 인스턴스(자립형)만 일괄로 담는다: 같은 층·타입에 N개면 관계에 하나씩 더하는 것이 이차 시간이다.
        # 문과 호스팅 인스턴스는 개구부를 채울 때(add_filling) 담긴 층을 읽으므로 바로 반영한다
        try:
            spec = graph.q(Q.ifc_spec, g)
            scratch.defer_rels = rows[g]["kind"] == "finst" and not spec["host"]
            scratch.create_identity(g, rows[g]["kind"], rows[g]["ifc_class"])
            scratch.project(g, spec)
            projected.add(g)
        except Exception as ex:   # 레시피가 그 IFC 클래스에 쓸 수 없는 속성을 요구하는 등: 의도의 투영을 만들 수 없으면 분리한다(안전한 쪽)
            rows[g]["origin"] = "detached"
            plan.detached.append({"id": g, "reasons": ["의도를 투영할 수 없다"]})
            plan.warnings.append(f"{g}: 의도를 투영할 수 없다({type(ex).__name__}: {str(ex)[:60]}) → 분리됨(동결)")
    scratch.flush_rels()
    want, have = Closure(scratch.f, scratch.infra), Closure(f, infra)
    for g in rows:
        typed = rows[g]["kind"] == "finst"
        h_hash, h_c = have.digest(f.by_guid(g), typed)
        if g in projected:
            w_hash, w_c = want.digest(scratch.ents[g], typed)
            if w_hash != h_hash:
                reasons = closure_diff(w_c, h_c)
                rows[g]["origin"] = "detached"
                plan.detached.append({"id": g, "reasons": reasons})
                plan.warnings.append(f"{g}: 들어온 형상이 의도의 투영과 다르다({', '.join(reasons[:4])}) → 분리됨(동결)")
        rows[g]["projection_hash"] = h_hash
    # 분리된 패밀리 타입의 인스턴스는 함께 분리한다: 타입이 낡은 정의라 인스턴스를 다시 투영하면 변조된 표현 지도를 가리킨다
    for g in sorted(rows):
        r = rows[g]
        if r["kind"] == "finst" and r["origin"] == "authored" and rows[r["params"]["type"]]["origin"] != "authored":
            r["origin"] = "detached"
            plan.detached.append({"id": g, "reasons": ["패밀리 타입이 분리됨"]})
            plan.warnings.append(f"{g}: 패밀리 타입 {r['params']['type']} 가 분리돼 인스턴스도 분리됨(동결)")
    for g in rows:
        if rows[g]["origin"] == "authored":   # 가져온 뒤 명세가 이 값 그대로면 첫 투영에서 들어온 엔티티를 그대로 둔다(engine.flush_ifc)
            rows[g]["spec_hash"] = digest(graph.q(Q.ifc_spec, g))
    # 길이 단위: 의도의 값은 mm 다. 단위 표기만 바뀐 파일은 요소 클로저가 같아 보이므로 후보 전체를 분리한다
    unit_ok = True
    if rows:
        try:
            scale = uunit.calculate_unit_scale(f)
        except Exception:
            scale = None
        if scale is None or abs(scale - 0.001) > 1e-9:
            unit_ok = False
            n = 0
            for g, r in rows.items():
                if r["origin"] == "authored":
                    r["origin"] = "detached"
                    r.pop("spec_hash", None)
                    plan.detached.append({"id": g, "reasons": ["길이 단위"]})
                    n += 1
            if n:
                plan.warnings.append(f"길이 단위가 밀리미터가 아니다: 의도의 값(mm)을 해석할 수 없어 의도가 있는 요소 {n}개를 모두 분리했다(동결)")

    # 4) 외래 요소
    owned: set[str] = set()   # 저작 문·패밀리 인스턴스의 개구부는 그 요소의 클로저에 속한다: 요소 행을 만들지 않는다
    for g, r in rows.items():
        if r["origin"] == "authored" and r["kind"] in ("door", "finst"):
            for rel in f.get_inverse(f.by_guid(g)):
                if rel.is_a("IfcRelFillsElement") and rel.RelatedBuildingElement == f.by_guid(g):
                    owned.add(rel.RelatingOpeningElement.GlobalId)
    for p in prods:
        g = p.GlobalId
        if g in rows:
            continue
        if g in owned:
            plan.skipped += 1
            continue
        rows[g] = {"kind": "foreign", "ifc_class": p.is_a(), "origin": "foreign", "family_id": None,
                   "params": {"name": _name_of(p)}, "projection_hash": None}
    # 4b) 뷰·치수·태그(정의가 속성셋에 실려 있다, ADR-0015)
    taken = seen | infra
    projs = f.by_type("IfcProject")
    if projs and isinstance(getattr(projs[0], "GlobalId", None), str):
        taken.add(projs[0].GlobalId)
    if unit_ok:
        _restore_defs(f, rows, rels, taken, plan)
    elif _read_defs(f, plan):   # 정의의 값(mm)도 길이 단위가 밀리미터일 때만 해석한다(요소의 의도와 같은 이유)
        plan.warnings.append("길이 단위가 밀리미터가 아니라 뷰·주석 정의를 되살리지 않는다")
    # 5) 공간 소속(on_level): 외래·분리 요소도 층에 담긴 관계를 남긴다(레벨 삭제 검사와 element.query level 필터가 쓴다)
    for p in prods:
        g = p.GlobalId
        if g not in rows or rows[g]["kind"] != "foreign":
            continue   # 저작·분리 벽은 의도의 level 로 이미 on_level 을 가졌고, 문은 호스트 벽을 따라간다
        c = ue.get_container(p, should_get_direct=True)
        if c is not None and c.GlobalId in rows and c.is_a("IfcBuildingStorey"):
            rels.add(("on_level", g, c.GlobalId))
    # 분리된 레벨: 그 위의 저작 요소는 분리되지 않았지만 배치가 레벨 배치를 따르므로 레벨 고도 변경이 절대 위치에 그대로 반영된다
    for lv in [d["id"] for d in plan.detached if rows[d["id"]]["kind"] == "level"]:
        above = [g for k, g, dst in rels if k == "on_level" and dst == lv and rows[g]["origin"] == "authored"]
        if above:
            plan.warnings.append(f"{lv}: 분리된 레벨 위에 저작 요소 {len(above)}개가 있다. 이 요소들은 레벨 배치를 따르므로 "
                                 "레벨의 고도 변경이 절대 위치에 반영된다(의도의 고도는 낡은 값이다)")
    plan.rows = [{"id": g, **r} for g, r in sorted(rows.items())]
    plan.rels = sorted(rels)
    return plan
