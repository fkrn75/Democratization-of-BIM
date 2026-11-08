# SPDX-License-Identifier: MPL-2.0
"""외래 IFC 가져오기 계획 (S2, ADR-0005): 들어온 파일의 요소를 authored / foreign / detached 로 분류한다.

분류 규칙(요소 = 코어 뼈대가 아닌 IfcProduct):
- DoBIM_Intent 가 있고, 현재 코어 스키마로 해석되고(레벨·벽·문), 가리키는 대상(레벨·호스트·조인 상대)이 있으면 의도를 복원해 후보가 된다.
- 후보의 의도를 새 IFC 모델에 다시 투영해, 요소 단위 투영 클로저 해시(ifc_model.Closure)를 들어온 요소의 해시와 비교한다.
  같으면 authored(의도가 형상의 정본), 다르면 detached(들어온 형상을 그대로 동결, 마지막 의도는 낡은 사본으로 남는다).
- 후보가 아니면 foreign(IFC 가 형상의 정본, 속성만 편집).

이 모듈은 파일을 읽어 계획(ImportPlan)을 만들 뿐 프로젝트를 만들지 않는다. 엔진이 계획을 SQLite 에 적는다.
"""
from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field

import ifcopenshell.util.element as ue

from dobim_core import queries as Q
from dobim_core.errors import DobimError
from dobim_core.graph import Graph
from dobim_core.ifc_model import (INTENT_PSET, Closure, IfcModel, closure_diff, detect_skeleton, open_ifc_bytes)

MAX_IMPORT_BYTES = 256 * 1024 * 1024
KIND_OF = {"IfcBuildingStorey": "level", "IfcWall": "wall", "IfcDoor": "door"}
FAMILY = {"level": "level.basic", "wall": "wall.basic", "door": "door.single"}
_ID = re.compile(r"^[0-9A-Za-z_$]{22}$")
NAME_MAX = 255


@dataclass
class ImportPlan:
    schema: str
    rows: list[dict] = field(default_factory=list)       # {id, kind, ifc_class, origin, family_id, params, projection_hash}
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


def _name_of(p) -> str:
    n = getattr(p, "Name", None)
    return (n if isinstance(n, str) else "")[:NAME_MAX]


# ---------------------------------------------------------------- 계획
def plan_import(data: bytes) -> ImportPlan:
    from dobim_core.engine import State   # 지연 import: 엔진이 이 모듈을 가져온다

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
    sk = detect_skeleton(f)
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

    # 1) 의도 복원 후보
    cand: dict[str, dict] = {}
    for p in prods:
        kind = KIND_OF.get(p.is_a())
        if kind is None:
            continue
        pset = ue.get_pset(p, INTENT_PSET, should_inherit=False)
        text = pset.get("Intent") if pset else None
        if not isinstance(text, str):
            continue
        try:
            cand[p.GlobalId] = _parse_intent(kind, text)
        except (ValueError, TypeError, KeyError) as ex:
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
            if bad:
                del cand[g]
                plan.warnings.append(f"{g}: 의도의 {bad} → 외래로 둔다")
                changed = True

    # 3) 후보를 저작 행으로 만들고 투영 명세를 계산한다
    rows: dict[str, dict] = {}
    rels: set[tuple] = set()
    for g, it in cand.items():
        cls = {"level": "IfcBuildingStorey", "wall": "IfcWall", "door": "IfcDoor"}[it["kind"]]
        rows[g] = {"kind": it["kind"], "ifc_class": cls, "origin": "authored", "family_id": FAMILY[it["kind"]], "params": it["params"]}
        if it["kind"] == "wall":
            rels.add(("on_level", g, it["level"]))
            rels.update(("joins", min(g, j), max(g, j)) for j in it["joins"])
        elif it["kind"] == "door":
            rels.add(("hosts", it["host"], g))
    st = State()
    for g, r in rows.items():
        st.apply_el(g, None, r)
    for k, s_, d_ in rels:
        st.apply_rel(k, s_, d_, True)
    graph = Graph(_Inputs(st))

    scratch = IfcModel(str(uuid.UUID(int=0)))
    order = {"level": 0, "wall": 1, "door": 2}
    for g in sorted(rows, key=lambda g: (order[rows[g]["kind"]], g)):
        scratch.create_identity(g, rows[g]["kind"])
        scratch.project(g, graph.q(Q.ifc_spec, g))
    want, have = Closure(scratch.f, scratch.infra), Closure(f, infra)
    for g in rows:
        w_hash, w_c = want.digest(scratch.ents[g])
        h_hash, h_c = have.digest(f.by_guid(g))
        if w_hash != h_hash:
            reasons = closure_diff(w_c, h_c)
            rows[g]["origin"] = "detached"
            plan.detached.append({"id": g, "reasons": reasons})
            plan.warnings.append(f"{g}: 들어온 형상이 의도의 투영과 다르다({', '.join(reasons[:4])}) → 분리됨(동결)")
        rows[g]["projection_hash"] = h_hash

    # 4) 외래 요소
    owned: set[str] = set()   # 저작 문의 개구부는 문 클로저에 속한다: 요소 행을 만들지 않는다
    for g, r in rows.items():
        if r["origin"] == "authored" and r["kind"] == "door":
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
    # 5) 공간 소속(on_level): 외래·분리 요소도 층에 담긴 관계를 남긴다(레벨 삭제 검사와 element.query level 필터가 쓴다)
    for p in prods:
        g = p.GlobalId
        if g not in rows or rows[g]["kind"] != "foreign":
            continue   # 저작·분리 벽은 의도의 level 로 이미 on_level 을 가졌고, 문은 호스트 벽을 따라간다
        c = ue.get_container(p, should_get_direct=True)
        if c is not None and c.GlobalId in rows and c.is_a("IfcBuildingStorey"):
            rels.add(("on_level", g, c.GlobalId))
    plan.rows = [{"id": g, **r} for g, r in sorted(rows.items())]
    plan.rels = sorted(rels)
    return plan
