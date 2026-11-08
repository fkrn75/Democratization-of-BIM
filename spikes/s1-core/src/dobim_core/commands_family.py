# SPDX-License-Identifier: MPL-2.0
"""S4 패밀리 커맨드: family.list / validate / register / place / set / preview.

레시피(JSON, ADR-0008)를 요소 저장소에 올리는 경로는 이렇다.
  family.register : 레시피 + 타입 매개변수 값 → 타입 요소(ftype, IfcXxxType 로 투영). 타입 수준 속성셋을 IFC 에 쓴다.
  family.place    : 타입(또는 패밀리 id + 타입 값) + 인스턴스 값 + 호스트/레벨 → 인스턴스 요소(finst). 개구부·속성셋 포함.
이 모듈은 ifcopenshell 을 import 하지 않는다(무거운 검증은 family.validate 안에서 지연 import).
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import Field, StrictStr, model_validator

from dobim_core import commands as C
from dobim_core import limits
from dobim_core.commands import A, Id, Name, Num, PropValue, command
from dobim_core.errors import DobimError
from dobim_core.family import evaluate as FE
from dobim_core.family import spec as FS
from dobim_core.family.recipe import ID_RE, FamilyError, load_json_text
from dobim_core.paths import resolve_in_workspace

FamilyId = Annotated[str, Field(pattern=ID_RE, max_length=64, description="레시피 id (예: door.single)")]
Values = Annotated[dict[str, PropValue], Field(max_length=64)]
MAX_ISSUES = 20
MAX_RECIPE_FILE = 64 * 1024
HISTORY_ARG_LIMIT = 1500


def _fam_error(ex: FamilyError, what: str = "패밀리") -> DobimError:
    iss = ex.issues
    first = iss[0]
    return DobimError("FAMILY_INVALID", f"{what} 검증 실패: {first.message}", path=first.path or None, hint=first.hint,
                      details={"total": len(iss), "issues": [i.to_dict() for i in iss[:5]]})


# ---------------------------------------------------------------- 레시피 출처
class Source(A):
    family: FamilyId | None = Field(None, description="기본 라이브러리의 레시피 id (family.list 로 찾는다)")
    path: str | None = Field(None, max_length=260, description="작업 폴더 안의 레시피 .json 경로")
    recipe: dict[str, Any] | None = Field(None, description="레시피 JSON 객체를 직접 넘긴다(64KB 이하)")

    @model_validator(mode="after")
    def _one(self):
        n = sum(x is not None for x in (self.family, self.path, self.recipe))
        if n != 1:
            raise ValueError("family, path, recipe 중 정확히 하나를 지정해야 한다")
        return self


def _load_source(workspace, a: Source) -> dict:
    from dobim_core.family import library
    if a.recipe is not None:
        return json.loads(json.dumps(a.recipe))   # 호출자의 객체와 요소 행이 같은 사본을 공유하지 않게 한다
    if a.path is not None:
        p = resolve_in_workspace(workspace, a.path, must_exist=True)
        if p.stat().st_size > MAX_RECIPE_FILE:
            raise DobimError("CONSTRAINT", f"레시피 파일이 {MAX_RECIPE_FILE // 1024}KB 를 넘는다", path="/path")
        try:
            return load_json_text(p.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            raise DobimError("SCHEMA_INVALID", "레시피 파일이 UTF-8 이 아니다", path="/path") from None
        except FamilyError as ex:
            raise _fam_error(ex, "레시피 파일") from None
    data = library.load(a.family)
    if data is None:
        raise DobimError("NOT_FOUND", f"기본 라이브러리에 없는 패밀리다: {a.family}", path="/family", hint="family.list 로 확인하세요")
    return data


# ---------------------------------------------------------------- family.list
class FamilyList(A):
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("family.list", kind="query", risk="read", group="family", summary="기본 패밀리 라이브러리의 레시피 목록(id·IFC 클래스·호스팅·매개변수 이름)",
         args=FamilyList, needs_project=False, idempotent=True, since="0.2", examples=[{}])
def family_list(env, a: FamilyList):
    from dobim_core.family import library
    rows = []
    for fid in library.ids():
        try:
            r, _ = FE.load_recipe(library.load(fid))
        except FamilyError:
            rows.append({"id": fid, "error": "레시피가 검증을 통과하지 못한다"})
            continue
        rows.append({"id": r.id, "name": r.name, "ifc_class": r.ifc.class_, "hosted": r.host is not None,
                     "type_params": [k for k, p in r.params.items() if p.kind == "type"],
                     "instance_params": [k for k, p in r.params.items() if p.kind == "instance"]})
    after = C._dec_cursor(a.cursor, "name")
    start = 0 if after is None else next((i for i, x in enumerate(rows) if x["id"] > after), len(rows))
    window = rows[start:start + a.limit]
    items, cut = C._fit(window, cursor_of=lambda i: C._enc_cursor(window[i]["id"]), trusted=True)
    more = start + len(items) < len(rows)
    return {"items": items, "page": C._page(C._enc_cursor(items[-1]["id"]) if more else None, len(rows), cut)}


# ---------------------------------------------------------------- family.validate
class FamilyValidate(Source):
    level: Literal["static", "full"] = Field("full", description="static=스키마·식·참조·범위·형상 매개변수, full=여기에 IFC 투영·유효성·표준 속성셋·형상 생성까지")


@command("family.validate", kind="query", risk="read", group="family",
         summary="레시피를 검증한다(스키마·식·순환/미정의 참조·범위·형상·IFC 유효성·표준 속성셋). 오류는 경로·이유·힌트로 돌려준다",
         args=FamilyValidate, needs_project=False, idempotent=True, since="0.2",
         examples=[{"family": "door.single", "level": "static"}, {"family": "column.section"}])
def family_validate(env, a: FamilyValidate):
    from dobim_core.family.validate import validate_recipe
    try:
        data = _load_source(env.workspace, a)
    except DobimError as ex:
        if ex.code != "FAMILY_INVALID":
            raise
        return {"ok": False, "level": a.level, "checks": {"schema": "fail"}, "total_errors": ex.details.get("total", 1),
                "errors": ex.details.get("issues", []), "warnings": []}
    res = validate_recipe(data, a.level)
    res["errors"] = [_clip_issue(i) for i in res["errors"][:MAX_ISSUES]]
    res["warnings"] = [_clip_issue(i) for i in res["warnings"][:MAX_ISSUES // 2]]
    return res


def _clip_issue(i: dict) -> dict:
    return {k: (limits.clip_chars(v, 300) if isinstance(v, str) else v) for k, v in i.items()}


# ---------------------------------------------------------------- 공통: 타입 만들기
def _recipe_of(ctx, family_id: str) -> dict:
    """프로젝트에 올라온 타입에서, 없으면 기본 라이브러리에서 레시피를 찾는다."""
    from dobim_core.family import library
    for tid in sorted(ctx.st.by_kind.get("ftype", ())):
        row = ctx.st.el[tid]
        if row["family_id"] == family_id:
            return row["params"]["recipe"]
    data = library.load(family_id)
    if data is None:
        raise DobimError("NOT_FOUND", f"패밀리를 찾을 수 없다: {family_id}", path="/family",
                         hint="family.list 로 라이브러리를 보거나 family.register 로 레시피를 먼저 올리세요")
    return data


def _check_values_keys(vals: dict, what: str) -> None:
    for k, v in vals.items():
        if isinstance(v, float) and v != v:
            raise DobimError("SCHEMA_INVALID", f"{what}.{k}: NaN 은 허용하지 않는다", path=f"/{what}/{k}")


def _make_type(ctx, tid: str, recipe: dict, r, plan, tv: dict, name: str | None) -> None:
    rhash = FS.recipe_hash_of(recipe)
    FS.remember(rhash, recipe)
    try:
        ev = FE.evaluate(r, plan, tv, None, None)
    except FamilyError as ex:
        raise _fam_error(ex, "타입") from None
    name = name or f"{r.name} {FE.digest({'t': tv})[:6]}"
    ctx.put_el(tid, {"kind": "ftype", "ifc_class": r.ifc.type_class or (r.ifc.class_ + "Type"), "origin": "authored", "family_id": r.id,
                     "params": {"name": name, "recipe": recipe, "recipe_hash": rhash, "type_values": tv}})
    for pset, props in sorted(ev["psets"]["type"].items()):
        for prop, v in sorted(props.items()):
            ctx.set_prop(tid, pset, prop, v)


class FamilyRegister(Source):
    id: Id | None = None
    type_values: Values = Field(default_factory=dict, description="타입 매개변수 값(생략한 것은 레시피 기본값). 인스턴스 매개변수는 place 에서 준다")
    name: Name | None = None


@command("family.register", kind="mutation", risk="write", group="family",
         summary="레시피로 패밀리 타입을 만든다(IfcXxxType 로 투영, 타입 수준 속성셋 기록). 같은 레시피·값이면 같은 타입 id",
         args=FamilyRegister, since="0.2",
         examples=[{"family": "door.single", "type_values": {"width": 1000}, "name": "D1000"}])
def family_register(ctx, a: FamilyRegister):
    data = _load_source(ctx.eng.workspace, a)
    try:
        r, plan = FE.load_recipe(data)
        tv, _ = FE.resolve_values(r, a.type_values, None)
    except FamilyError as ex:
        raise _fam_error(ex) from None
    rhash = FS.recipe_hash_of(data)
    explicit = a.id is not None
    tid = a.id or ctx.det_id(f"ftype:{rhash}:{FE.canon(tv)}")
    if tid in ctx.st.el:
        if explicit or ctx.st.el[tid]["kind"] != "ftype":
            raise DobimError("CONSTRAINT", f"이미 있는 id 다: {tid}", path="/id")
        return {"id": tid, "family": r.id, "created": False}
    _make_type(ctx, tid, data, r, plan, tv, a.name)
    return {"id": tid, "family": r.id, "created": True}


# ---------------------------------------------------------------- family.place / set
class FamilyPlace(A):
    id: Id | None = None
    type: Id | None = Field(None, description="family.register 가 돌려준 타입 id. 생략하면 family 로 타입을 만들거나 찾는다")
    family: FamilyId | None = Field(None, description="타입을 지정하지 않을 때 레시피 id(프로젝트에 올린 것 또는 기본 라이브러리)")
    type_values: Values = Field(default_factory=dict, description="family 로 지정했을 때 타입 매개변수 값")
    values: Values = Field(default_factory=dict, description="인스턴스 매개변수 값(생략한 것은 기본값)")
    host: Id | None = Field(None, description="호스트 벽(호스팅 패밀리)")
    offset: Num | None = Field(None, description="호스트 축선 시작점에서 패밀리 원점(개구부 중심)까지 mm")
    level: Id | None = Field(None, description="레벨(자립형 패밀리)")
    position: Annotated[list[Num], Field(min_length=2, max_length=2)] | None = Field(None, description="[x, y] mm, 레벨 기준(자립형)")
    rotation: Num = Field(0.0, description="자립형 패밀리의 z 축 회전 deg")
    elevation: Num = Field(0.0, description="원점의 높이 mm(호스팅이면 벽 아래, 자립형이면 레벨 위)")
    name: Name | None = None

    @model_validator(mode="after")
    def _shape(self):
        if (self.type is None) == (self.family is None):
            raise ValueError("type 과 family 중 정확히 하나를 지정해야 한다")
        if self.type is not None and self.type_values:
            raise ValueError("type 을 지정하면 type_values 는 줄 수 없다(타입은 이미 값이 정해졌다)")
        hosted = self.host is not None or self.offset is not None
        free = self.level is not None or self.position is not None
        if hosted == free:
            raise ValueError("host+offset(호스팅) 또는 level+position(자립형) 중 한 쪽만 지정해야 한다")
        if hosted and (self.host is None or self.offset is None):
            raise ValueError("호스팅 배치는 host 와 offset 이 모두 필요하다")
        if free and (self.level is None or self.position is None):
            raise ValueError("자립형 배치는 level 과 position 이 모두 필요하다")
        return self


def _inst_geometry(ctx, tp: dict, values: dict, host_id: str | None, offset, elevation):
    """인스턴스 평가 + 호스트 적합성 검사. (평가 결과, 호스트 문맥)을 돌려준다."""
    host_ctx = None
    if host_id is not None:
        wp = ctx.row(host_id, "wall")["params"]
        host_ctx = {"host_thickness": wp["thickness"], "host_height": wp["height"]}
    try:
        ev = FS.finst_eval(tp, {"values": values}, host_ctx)
    except FamilyError as ex:
        raise _fam_error(ex, "인스턴스") from None
    if host_id is not None and ev["cut"]:
        length = C.sqrt(C._axis_len2(ctx.st.el[host_id]["params"]["axis"]))
        x0, x1 = ev["cut"]["x"]
        z0, z1 = ev["cut"]["z"]
        if offset + x0 < 0 or offset + x1 > length:
            raise DobimError("CONSTRAINT", f"개구부({offset + x0:.0f}..{offset + x1:.0f}mm)가 호스트 벽({length:.1f}mm) 밖으로 나간다", path="/offset")
        if elevation + z0 < 0 or elevation + z1 > _wall_height(ctx, host_id):
            raise DobimError("CONSTRAINT", "개구부가 호스트 벽의 높이 밖으로 나간다", path="/elevation")
    return ev, host_ctx


def _wall_height(ctx, host_id: str) -> float:
    return ctx.st.el[host_id]["params"]["height"]


def _inst_params(a_name, tid, values, ev, hosted, offset, position, rotation, elevation) -> dict:
    p: dict = {"name": a_name, "type": tid, "values": values, "rotation": rotation, "elevation": elevation}
    if hosted:
        cut = ev["cut"]
        p["offset"] = offset
        p["width"] = (cut["x"][1] - cut["x"][0]) if cut else 0.0
        p["height"] = (cut["z"][1] - cut["z"][0]) if cut else 0.0
    else:
        p["position"] = list(position)
    return p


@command("family.place", kind="mutation", risk="write", group="family",
         summary="패밀리 인스턴스를 배치한다(벽에 호스팅하거나 레벨에 자립). 타입이 없으면 만든다. 개구부·속성셋·IFC 타입 관계 포함",
         args=FamilyPlace, since="0.2",
         examples=[{"family": "door.single", "values": {"swing": "R"}, "host": C.F.W3, "offset": 1500},
                   {"family": "column.section", "type_values": {"section": "round"}, "level": C.F.LV1, "position": [1000, 1000]}])
def family_place(ctx, a: FamilyPlace):
    C._fresh_id(ctx, a, "finst")
    if a.type is not None:
        trow = ctx.row(a.type, "ftype")
        tid, tp = a.type, trow["params"]
        r, plan = FE.load_recipe(tp["recipe"])
        recipe, made = tp["recipe"], False
    else:
        recipe = _recipe_of(ctx, a.family)
        try:
            r, plan = FE.load_recipe(recipe)
            tv, _ = FE.resolve_values(r, a.type_values, None)
        except FamilyError as ex:
            raise _fam_error(ex) from None
        rhash = FS.recipe_hash_of(recipe)
        tid = ctx.det_id(f"ftype:{rhash}:{FE.canon(tv)}")
        made = tid not in ctx.st.el
        if made:
            _make_type(ctx, tid, recipe, r, plan, tv, None)
        tp = ctx.st.el[tid]["params"]
    hosted = a.host is not None
    if hosted != (r.host is not None):
        raise DobimError("CONSTRAINT", f"{r.id} 는 {'호스팅(host+offset)' if r.host else '자립형(level+position)'} 패밀리다",
                         path="/host" if r.host else "/level")
    try:
        _, iv = FE.resolve_values(r, tp["type_values"], a.values)
    except FamilyError as ex:
        raise _fam_error(ex) from None
    if hosted:
        ctx.row(a.host, "wall")
    else:
        ctx.row(a.level, "level")
    ev, _ = _inst_geometry(ctx, tp, iv, a.host, a.offset, a.elevation)
    name = a.name or f"{r.id.split('.')[0][:1].upper()}-{a.id[-6:]}"
    ctx.put_el(a.id, {"kind": "finst", "ifc_class": r.ifc.class_, "origin": "authored", "family_id": r.id,
                      "params": _inst_params(name, tid, iv, ev, hosted, a.offset, a.position, a.rotation, a.elevation)})
    ctx.rel("of_type", a.id, tid, True)
    if hosted:
        ctx.rel("hosts", a.host, a.id, True)
    else:
        ctx.rel("on_level", a.id, a.level, True)
    for pset, props in sorted(ev["psets"]["instance"].items()):
        for prop, v in sorted(props.items()):
            ctx.set_prop(a.id, pset, prop, v)
    return {"id": a.id, "type": tid, "type_created": bool(made) if a.type is None else False}


class FamilySet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    values: Values = Field(default_factory=dict, description="바꿀 인스턴스 매개변수 값(나머지는 그대로)")
    offset: Num | None = None
    position: Annotated[list[Num], Field(min_length=2, max_length=2)] | None = None
    rotation: Num | None = None
    elevation: Num | None = None

    @model_validator(mode="after")
    def _any(self):
        if not self.values and all(v is None for v in (self.name, self.offset, self.position, self.rotation, self.elevation)):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("family.set", kind="mutation", risk="write", group="family",
         summary="패밀리 인스턴스의 이름·인스턴스 매개변수·위치·회전·높이를 바꾼다(타입 값은 바꾸지 못한다: 새 타입으로 다시 배치)",
         args=FamilySet, since="0.2", examples=[{"id": C.F.D1, "name": "X"}])
def family_set(ctx, a: FamilySet):
    r_ = ctx.row(a.id, "finst")
    p = dict(r_["params"])
    tp = ctx.st.el[p["type"]]["params"]
    r, plan = FE.load_recipe(tp["recipe"])
    hosted = "offset" in p
    if hosted and a.position is not None or not hosted and a.offset is not None:
        raise DobimError("CONSTRAINT", "이 인스턴스의 배치 방식과 맞지 않는 위치 필드다", path="/offset" if not hosted else "/position")
    try:
        _, iv = FE.resolve_values(r, tp["type_values"], {**p["values"], **a.values})
    except FamilyError as ex:
        raise _fam_error(ex) from None
    host_id = ctx.rels_in("hosts", a.id)[0] if hosted else None
    offset = a.offset if a.offset is not None else p.get("offset")
    elevation = a.elevation if a.elevation is not None else p["elevation"]
    ev, _ = _inst_geometry(ctx, tp, iv, host_id, offset, elevation)
    np_ = _inst_params(a.name or p["name"], p["type"], iv, ev, hosted, offset, a.position or p.get("position"),
                       a.rotation if a.rotation is not None else p["rotation"], elevation)
    ctx.put_el(a.id, dict(r_, params=np_))
    for pset, props in sorted(ev["psets"]["instance"].items()):
        for prop, v in sorted(props.items()):
            ctx.set_prop(a.id, pset, prop, v)
    return {"id": a.id}


# ---------------------------------------------------------------- family.preview
class FamilyPreview(A):
    type: Id | None = None
    family: FamilyId | None = None
    type_values: Values = Field(default_factory=dict)
    values: Values = Field(default_factory=dict)
    host_thickness: Num | None = Field(None, description="호스팅 패밀리의 호스트 두께 mm (생략하면 200)")
    scale: StrictStr = Field("default", pattern=r"^(default|1:[1-9][0-9]{0,3})$", description="평면 기호 축척 키")

    @model_validator(mode="after")
    def _one(self):
        if (self.type is None) == (self.family is None):
            raise ValueError("type 과 family 중 정확히 하나를 지정해야 한다")
        return self


PREVIEW_ITEMS = 24


@command("family.preview", kind="query", risk="read", group="family",
         summary="타입/패밀리 + 값을 평가해 형상 요약·개구부·평면 기호 도형·속성셋을 보여 준다(요소를 만들지 않는다)",
         args=FamilyPreview, idempotent=True, since="0.2",
         examples=[{"family": "door.single", "values": {"swing": "R"}, "scale": "1:200"}])
def family_preview(ctx, a: FamilyPreview):
    if a.type is not None:
        tp = ctx.row(a.type, "ftype")["params"]
        recipe, tv = tp["recipe"], tp["type_values"]
    else:
        recipe = _recipe_of(ctx, a.family)
        tv = a.type_values
    try:
        r, plan = FE.load_recipe(recipe)
        tvv, ivv = FE.resolve_values(r, tv, a.values)
        host = {"host_thickness": a.host_thickness} if (a.host_thickness is not None and r.host) else None
        ev = FE.evaluate(r, plan, tvv, ivv, host)
    except FamilyError as ex:
        raise _fam_error(ex) from None
    scale = a.scale if a.scale in ev["plan"] else "default"
    prims = ev["plan"][scale]
    body = [{"id": it["id"], "k": it["k"], "cuts": len(it.get("cuts", ()))} for it in ev["body"]]
    out = {"family": r.id, "ifc_class": r.ifc.class_, "type_class": r.ifc.type_class, "scale_used": scale,
           "type_values": tvv, "values": ivv, "cut": ev["cut"], "body_count": len(body), "body": body[:PREVIEW_ITEMS],
           "plan": prims[:PREVIEW_ITEMS], "psets": ev["psets"], "scales": sorted(ev["plan"])}
    if len(body) > PREVIEW_ITEMS or len(prims) > PREVIEW_ITEMS:
        ctx.warn(f"형상/기호가 {PREVIEW_ITEMS}개를 넘어 앞부분만 보인다(body_count={len(body)}, plan={len(prims)})")
    return out
