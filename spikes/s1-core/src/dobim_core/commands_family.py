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
from dobim_core.family.recipe import ID_RE, FamilyError, check_json_value, load_json_text
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
        try:
            check_json_value(a.recipe)   # NaN/Infinity·거대 정수·깊은 중첩은 직렬화 전에 거절(RecursionError/ValueError → INTERNAL 방지)
            return json.loads(json.dumps(a.recipe))
        except FamilyError as ex:
            raise _fam_error(ex, "레시피") from None   # 호출자의 객체와 요소 행이 같은 사본을 공유하지 않게 한다
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


@command("family.list", kind="query", risk="read", group="family", summary="기본 패밀리 라이브러리 목록",
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
         summary="레시피 검증(구조화 오류: 경로·이유·힌트)",
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
    res.get("stats", {}).pop("seconds", None)   # 실행 시간은 비결정적이라 응답에 넣지 않는다(CLI == MCP 패리티)
    res["errors"] = [_clip_issue(i) for i in res["errors"][:MAX_ISSUES]]
    res["warnings"] = [_clip_issue(i) for i in res["warnings"][:MAX_ISSUES // 2]]
    return res


def _clip_issue(i: dict) -> dict:
    return {k: (limits.clip_chars(v, 300) if isinstance(v, str) else v) for k, v in i.items()}


# ---------------------------------------------------------------- 공통: 타입 만들기
def _recipe_of(ctx, family_id: str) -> dict:
    """프로젝트에 올라온 타입에서, 없으면 기본 라이브러리에서 레시피를 찾는다."""
    from dobim_core.family import library
    found: dict[str, dict] = {}   # recipe_hash → 레시피(같은 id 의 서로 다른 레시피를 모은다)
    for tid in sorted(ctx.st.by_kind.get("ftype", ())):
        row = ctx.st.el[tid]
        if row["family_id"] == family_id:
            found.setdefault(row["params"]["recipe_hash"], row["params"]["recipe"])
    if found:
        top = max(r["version"] for r in found.values())
        best = [r for r in found.values() if r["version"] == top]
        if len(best) > 1:   # 같은 id·같은 version 인데 내용이 다른 레시피가 둘 이상: 어느 쪽인지 정할 수 없다
            raise DobimError("CONSTRAINT", f"레시피 id {family_id} 가 version {top} 으로 {len(best)}가지 내용이 올라와 있어 모호하다",
                             path="/family", hint="family.register 가 돌려준 타입 id 를 type 으로 지정하거나, 레시피 version 을 올려 다시 등록하세요",
                             details={"family": family_id, "version": top, "variants": len(best)})
        return best[0]   # 같은 id 의 여러 version 이 공존하면 가장 높은 version 을 쓴다
    data = library.load(family_id)
    if data is None:
        raise DobimError("NOT_FOUND", f"패밀리를 찾을 수 없다: {family_id}", path="/family",
                         hint="family.list 로 라이브러리를 보거나 family.register 로 레시피를 먼저 올리세요")
    return data


def _content_tid(ctx, rhash: str, tv: dict) -> str:
    """같은 레시피·타입 값의 타입 id(내용 주소). 평소에는 프로젝트 UUID 에서 파생한 결정적 id 다.
    가져온 프로젝트는 UUID 가 타입 id 의 출처가 아니다(타입은 내보내기 전 프로젝트의 UUID 로 정해졌다): 파생 id 의 타입이 없고
    같은 내용의 기존 타입이 있으면 그것을 쓴다(없으면 같은 타입이 둘이 된다). id 가 가장 작은 것을 고른다(결정적).
    재사용 후보는 저작(authored) 타입뿐이다: 분리·외래 타입에 새 인스턴스를 달면 인스턴스는 저작인데 타입은 아니라서
    재가져오기에서 인스턴스가 분리되고 해시가 어긋난다. 파생 id 가 비저작 요소로 이미 차 있으면 다음 파생 id 로 새 타입을 만든다."""
    key = FE.canon(tv)
    base = f"ftype:{rhash}:{key}"
    tid = ctx.det_id(base)
    st = ctx.st

    def _authored_type(i: str) -> bool:
        row = st.el[i]
        return row["kind"] == "ftype" and row.get("origin") == "authored"

    if tid in st.el and _authored_type(tid):
        return tid
    if ctx.eng.has_base:
        for other in sorted(st.by_kind.get("ftype", ())):
            p = st.el[other]["params"]
            if _authored_type(other) and p["recipe_hash"] == rhash and FE.canon(p["type_values"]) == key:
                return other
    n = 0
    while tid in st.el:   # 파생 id 가 비저작(분리·외래) 요소로 차 있다
        n += 1
        tid = ctx.det_id(f"{base}#{n}")
    return tid


def _make_type(ctx, tid: str, recipe: dict, r, plan, tv: dict, name: str | None) -> None:
    rhash = FS.recipe_hash_of(recipe)
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


def _warn_same_id(ctx, r, rhash: str) -> None:
    """같은 id 의 다른 레시피(내용 또는 version 이 다름)가 이미 있는데 새로 올리면 경고한다(family 로 배치할 때 어느 것이 선택되는지 알린다)."""
    from dobim_core.family import library
    others = {row["params"]["recipe_hash"]: row["params"]["recipe"]["version"]
              for tid in ctx.st.by_kind.get("ftype", ()) if (row := ctx.st.el[tid])["family_id"] == r.id
              and row["params"]["recipe_hash"] != rhash}
    if others:
        ctx.warn(f"레시피 id {r.id} 로 다른 내용의 레시피(version {sorted(set(others.values()))})가 이미 올라와 있다. "
                 f"family 로 배치하면 가장 높은 version({max([r.version, *others.values()])})이 선택되고, 같은 version 이면 모호해 거절된다. "
                 "id 를 바꾸거나 version 을 올리세요")
    elif r.id in library.ids() and rhash != FS.recipe_hash_of(library.load(r.id)):
        ctx.warn(f"기본 라이브러리의 {r.id} 와 같은 id 지만 내용이 다른 레시피다. family 로 배치하면 이 프로젝트에 올린 쪽이 우선한다")


class FamilyRegister(Source):
    id: Id | None = None
    type_values: Values = Field(default_factory=dict, description="타입 매개변수 값(생략한 것은 레시피 기본값). 인스턴스 매개변수는 place 에서 준다")
    name: Name | None = None


@command("family.register", kind="mutation", risk="write", group="family",
         summary="레시피로 패밀리 타입을 만든다",
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
    tid = a.id or _content_tid(ctx, rhash, tv)
    if tid in ctx.st.el:
        if explicit or ctx.st.el[tid]["kind"] != "ftype":
            raise DobimError("CONSTRAINT", f"이미 있는 id 다: {tid}", path="/id")
        return {"id": tid, "family": r.id, "created": False}
    _warn_same_id(ctx, r, rhash)
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


def check_hosted_fit(ctx, wall_id: str, old_wp: dict, new_wp: dict) -> None:
    """벽 치수(두께·높이·축선)가 바뀔 때 호스팅된 패밀리 인스턴스를 새 값으로 미리 재평가한다.

    레시피 제약(예: host_thickness >= leaf_t + 20)이 깨지거나 개구부가 새 벽 밖으로 나가면 CONSTRAINT 로 거절해
    커밋 전에 막는다. (이전에는 wall.set 이 ok 로 로그에 남고 flush 때 FamilyError → INTERNAL, 재오픈 실패였다.)
    개구부 크기가 호스트 높이에 따라 달라지는 레시피면 저장된 width/height(파생값)도 새 값으로 맞춘다.
    호스트 형상을 바꾸는 커맨드는 wall.set 뿐이다(wall.move 는 평행이동이라 두께·높이·길이가 그대로고, join 은 파생 트림만 바꾼다).
    """
    changed = next((k for k in ("thickness", "height", "axis") if old_wp[k] != new_wp[k]), None)
    if changed is None:
        return
    path = "/" + changed
    length = C.sqrt(C._axis_len2(new_wp["axis"]))
    host_ctx = {"host_thickness": new_wp["thickness"], "host_height": new_wp["height"]}
    for d in sorted(ctx.rels_out("hosts", wall_id)):
        row = ctx.st.el[d]
        if row["kind"] != "finst":
            continue
        p = row["params"]
        tp = ctx.st.el[p["type"]]["params"]
        try:
            ev = FS.finst_eval(tp, {"values": p["values"]}, host_ctx)
        except FamilyError as ex:
            first = ex.issues[0]
            raise DobimError("CONSTRAINT", f"호스팅된 패밀리 인스턴스 {d}({row['family_id']})가 새 벽 치수를 받아들이지 못한다: {first.message}",
                             path=path, hint="벽 값을 레시피 제약 안으로 두거나, 호스팅된 인스턴스를 먼저 옮기거나 지우세요",
                             details={"instance": d, "issue": first.to_dict()}) from None
        cut = ev["cut"]
        if not cut:
            continue
        x0, x1 = cut["x"]
        z0, z1 = cut["z"]
        if p["offset"] + x0 < 0 or p["offset"] + x1 > length:
            raise DobimError("CONSTRAINT", f"호스팅된 패밀리 인스턴스 {d} 의 개구부가 새 벽({length:.1f}mm) 밖으로 나간다", path=path,
                             details={"instance": d})
        if p["elevation"] + z0 < 0 or p["elevation"] + z1 > new_wp["height"]:
            raise DobimError("CONSTRAINT", f"호스팅된 패밀리 인스턴스 {d} 의 개구부가 새 벽 높이({new_wp['height']:g}mm) 밖으로 나간다", path=path,
                             details={"instance": d})
        w, h = x1 - x0, z1 - z0
        if w != p["width"] or h != p["height"]:
            ctx.put_el(d, dict(row, params=dict(p, width=w, height=h)))


def _inst_params(a_name, tid, values, ev, hosted, offset, position, rotation, elevation) -> dict:
    return FS.inst_params(a_name, tid, values, ev, hosted, offset, position, rotation, elevation)   # 정본은 family.spec (가져오기가 같은 식으로 대조한다)


EXAMPLE_FINST = "0" * 21 + "1"   # IFC GlobalId 첫 글자는 0~3 만 유효하다; family.place 예시가 만들고 family.set 예시가 고치는 인스턴스 id(예시는 이름순으로 한 프로젝트에서 실행된다)


@command("family.place", kind="mutation", risk="write", group="family",
         summary="패밀리 인스턴스를 배치한다(호스팅/자립)",
         args=FamilyPlace, since="0.2",
         examples=[{"id": EXAMPLE_FINST, "family": "door.single", "values": {"swing": "R"}, "host": C.F.W3, "offset": 1500},
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
        tid = _content_tid(ctx, rhash, tv)
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
        ctx.authored(a.host, "wall", what="패밀리 인스턴스 호스팅")   # S2 의 저작 확인: 분리·외래 벽에는 놓을 수 없다
    else:
        ctx.authored(a.level, "level", what="레벨에 패밀리 인스턴스를 놓는 것")
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
         summary="패밀리 인스턴스를 수정한다",
         args=FamilySet, since="0.2",
         examples=[{"id": EXAMPLE_FINST, "name": "현관문", "values": {"swing": "L"}, "offset": 2500}])
def family_set(ctx, a: FamilySet):
    r_ = ctx.authored(a.id, "finst")
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
         summary="타입/패밀리 평가 결과를 미리 본다",
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
