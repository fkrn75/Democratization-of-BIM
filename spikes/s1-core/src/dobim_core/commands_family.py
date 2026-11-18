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
from dobim_core import walltop as WT
from dobim_core.commands import A, Id, Name, Num, PropValue, command
from dobim_core.errors import DobimError
from dobim_core.family import evaluate as FE
from dobim_core.family import spec as FS
from dobim_core.family.recipe import ID_RE, FamilyError, check_json_value, load_json_text
from dobim_core.paths import resolve_in_workspace

FamilyId = Annotated[str, Field(pattern=ID_RE, max_length=64, description="레시피 id (예: door.single)")]
Values = Annotated[dict[str, PropValue], Field(max_length=64)]
TRANSOMS_MAX = 4          # 가로대 목록(25단계) 상한: 레시피 매개변수 transom, transom2 .. transom4
ALSO_CUTS_MAX = 4         # 함께 자르는 벽(25단계) 상한
Transoms = Annotated[list[Annotated[Num, Field(gt=0)]], Field(max_length=TRANSOMS_MAX)] | None
AlsoCuts = Annotated[list[Id], Field(max_length=ALSO_CUTS_MAX)] | None
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
    transoms: Transoms = Field(None, description="가로대 높이 목록 mm(25단계). 레시피의 transom, transom2, ... 타입 값으로 풀린다")


def _expand_transoms(r, type_values: dict, transoms, path: str = "/transoms") -> dict:
    """가로대 목록(25단계)을 레시피 타입 매개변수 transom, transom2 .. 로 푼다. 목록이 없으면 type_values 그대로(이전과 같다).
    목록과 transom* 값을 함께 주거나, 오름차순이 아니거나, 레시피에 그만큼의 가로대 매개변수가 없으면 거절한다."""
    if transoms is None:
        return type_values
    keys = ["transom"] + [f"transom{i}" for i in range(2, TRANSOMS_MAX + 1)]
    if any(k in type_values for k in keys):
        raise DobimError("CONSTRAINT", "transoms 와 transom* 타입 값은 함께 줄 수 없다", path=path)
    if any(b <= a for a, b in zip(transoms, transoms[1:])):
        raise DobimError("CONSTRAINT", "transoms 는 아래에서 위로 오름차순이어야 한다", path=path, details={"transoms": list(transoms)})
    have = [k for k in keys if k in r.params and r.params[k].kind == "type"]
    if len(transoms) > len(have) or have != keys[:len(have)]:
        raise DobimError("CONSTRAINT", f"{r.id} 는 가로대를 {len(have)}개까지만 받는다", path=path,
                         hint="window.storefront 처럼 transom, transom2, ... 매개변수가 있는 패밀리를 쓰세요",
                         details={"given": len(transoms), "max": len(have)})
    out = dict(type_values)
    for k, v in zip(keys, transoms):
        out[k] = v
    return out


def _check_transoms(r, plan, tv: dict, transoms) -> None:
    """가로대 목록(25단계)이 모두 가로대가 됐는지 본다. 레시피는 규칙(앞 가로대보다 300 위, 위 끝보다 300 아래)을 어긴 값을 없는 것으로 보므로
    목록으로 준 값이 빠지면 조용히 넘기지 않고 거절한다. 레시피가 가로대 개수(속성 TransomCount)를 내보내지 않으면 보지 않는다."""
    if not transoms:
        return
    try:
        ev = FE.evaluate(r, plan, tv, None, None)
    except FamilyError as ex:
        raise _fam_error(ex, "타입") from None
    got = next((ps["TransomCount"] for ps in ev["psets"]["type"].values() if "TransomCount" in ps), None)
    if got is not None and got != len(transoms):
        raise DobimError("CONSTRAINT", f"가로대 {len(transoms)}개 가운데 {got}개만 가로대가 된다(창호 아래·위 끝과 앞 가로대에서 300mm 이상 떨어져야 한다)",
                         path="/transoms", details={"transoms": list(transoms), "height": tv.get("height"), "accepted": got})


@command("family.register", kind="mutation", risk="write", group="family",
         summary="레시피로 패밀리 타입을 만든다",
         args=FamilyRegister, since="0.2",
         examples=[{"family": "door.single", "type_values": {"width": 1000}, "name": "D1000"}])
def family_register(ctx, a: FamilyRegister):
    data = _load_source(ctx.eng.workspace, a)
    try:
        r, plan = FE.load_recipe(data)
    except FamilyError as ex:
        raise _fam_error(ex) from None
    tvals = _expand_transoms(r, a.type_values, a.transoms)
    try:
        tv, _ = FE.resolve_values(r, tvals, None)
    except FamilyError as ex:
        raise _fam_error(ex) from None
    _check_transoms(r, plan, tv, a.transoms)
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
    rotation: Num = Field(0.0, description="자립형: z 축 회전 deg. 호스팅: 0 또는 180(180 = 뒤집기, 문짝이 벽 반대쪽으로 열리고 경첩 쪽도 바뀐다)")
    elevation: Num = Field(0.0, description="원점의 높이 mm(호스팅이면 벽 아래, 자립형이면 레벨 위)")
    name: Name | None = None
    transoms: Transoms = Field(None, description="가로대 높이 목록 mm(창호 아래에서, 오름차순, 25단계). 레시피의 transom, transom2, ... 타입 값으로 풀린다(family 로 지정할 때만)")
    also_cuts: AlsoCuts = Field(None, description="개구부가 호스트 벽 밖(위·아래로 쌓인 벽, 같은 선으로 이어지는 벽)으로 나갈 때 함께 자를 벽 id 목록(25단계)")

    @model_validator(mode="after")
    def _shape(self):
        if (self.type is None) == (self.family is None):
            raise ValueError("type 과 family 중 정확히 하나를 지정해야 한다")
        if self.type is not None and (self.type_values or self.transoms is not None):
            raise ValueError("type 을 지정하면 type_values·transoms 는 줄 수 없다(타입은 이미 값이 정해졌다)")
        if self.also_cuts is not None and self.host is None:
            raise ValueError("also_cuts 는 호스팅 배치(host+offset)에서만 쓴다")
        hosted = self.host is not None or self.offset is not None
        free = self.level is not None or self.position is not None
        if hosted == free:
            raise ValueError("host+offset(호스팅) 또는 level+position(자립형) 중 한 쪽만 지정해야 한다")
        if hosted and (self.host is None or self.offset is None):
            raise ValueError("호스팅 배치는 host 와 offset 이 모두 필요하다")
        if free and (self.level is None or self.position is None):
            raise ValueError("자립형 배치는 level 과 position 이 모두 필요하다")
        return self


def _inst_geometry(ctx, tp: dict, values: dict, host_id: str | None, offset, elevation, rotation=0.0, also=()):
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
        check_fit(ctx, host_id, ctx.st.el[host_id]["params"], offset, elevation, ev["cut"], values, rotation, also)
    return ev, host_ctx


# ---------------------------------------------------------------- 호스트 적합 규칙(25단계, 설계: elements-design.md 27절)
FIT_TOL = 0.5             # 개구부가 벽 범위를 이만큼(mm)은 넘어도 된다(반올림 잡음)
CORNER_END_ALLOW = 500.0  # 모서리 끝(end_l/end_r = corner)은 축선 교점까지 이만큼 더 나가도 된다(place_corner 의 끝점 허용 오차 상한과 같다)
PARALLEL_COS = 0.0087     # also_cuts 벽은 호스트와 0.5도 안으로 평행해야 한다


def host_range(ctx, wid: str, wp: dict | None = None) -> tuple[float, float]:
    """벽 지역 u 범위: 축선 [0, 길이] 와 조인 형상 범위(조인 지점까지 자르기·늘리기 + L 조인 모서리 반 두께 연장, queries.wall_extent 와 같은 규칙)의 합집합.
    합집합이라 예전에 받던 위치(축선 안)는 모두 그대로 받는다. wp 를 주면 그 값(바뀔 벽 값)으로 계산한다."""
    wp = wp or ctx.st.el[wid]["params"]
    axis = wp["axis"]
    x, y, c, s, length = C._frame_of(axis)
    partners = sorted(set(ctx.rels_out("joins", wid) + ctx.rels_in("joins", wid)))
    pax = [(o, ctx.st.el[o]["params"]["axis"]) for o in partners]
    start, end = 0.0, length
    for o, oax in pax:   # queries.wall_trim 과 같은 자르기·늘리기
        pt = C._join_pt(wid, axis, o, oax)
        if pt is None:
            continue
        t = (pt[0] - x) * c + (pt[1] - y) * s
        if abs(t) <= abs(t - length):
            if abs(t) <= C.Q.JOIN_MAX_EXT:
                start = t
        elif abs(t - length) <= C.Q.JOIN_MAX_EXT:
            end = t
    ok = end - start >= C.Q.MIN_TRIMMED   # 퇴화한 조인이면 자르기·모서리 연장이 없다(queries.wall_extent 와 같다)
    if not ok:
        start, end = 0.0, length
    for o, oax in (pax if ok and C.Q.CORNER_EXTEND else ()):
        if not wid < o:
            continue
        pt = C._join_pt(wid, axis, o, oax)
        if pt is None:
            continue
        ox, oy, oc, os_, olen = C._frame_of(oax)
        t = (pt[0] - x) * c + (pt[1] - y) * s
        to = (pt[0] - ox) * oc + (pt[1] - oy) * os_
        if min(abs(to), abs(to - olen)) > C.Q.JOIN_MAX_EXT:
            continue
        sin = c * os_ - s * oc
        if abs(sin) < C.Q.MIN_SIN:
            continue
        ext = ctx.st.el[o]["params"]["thickness"] / 2.0 / abs(sin)
        if abs(t) <= abs(t - length):
            if abs(t) <= C.Q.JOIN_MAX_EXT:
                start = min(start, t - ext)
        elif abs(t - length) <= C.Q.JOIN_MAX_EXT:
            end = max(end, t + ext)
    return (min(0.0, start), max(length, end))


def _level_elev(ctx, wid: str) -> float:
    return ctx.st.el[ctx.rels_out("on_level", wid)[0]]["params"]["elevation"]


def _also_box(ctx, host_id: str, hp: dict, oid: str, i: int):
    """함께 자르는 벽 oid 의 범위를 호스트 지역 좌표(u, 호스트 레벨 기준 z)로 옮긴다. 평행·같은 선(두께 안)이 아니면 거절."""
    path = f"/also_cuts/{i}"
    if oid == host_id:
        raise DobimError("CONSTRAINT", "also_cuts 에 호스트 벽을 넣을 수 없다", path=path)
    op = ctx.authored(oid, "wall", what="개구부로 함께 자르기")["params"]
    hx, hy, hc, hs, _ = C._frame_of(hp["axis"])
    ox, oy, oc, os_, olen = C._frame_of(op["axis"])
    if abs(hc * os_ - hs * oc) > PARALLEL_COS:
        raise DobimError("CONSTRAINT", f"함께 자를 벽 {oid} 이 호스트 벽과 평행하지 않다", path=path,
                         hint="위·아래로 쌓인 벽이나 같은 선으로 이어지는 벽만 함께 자를 수 있다")
    dist = abs((ox - hx) * hs - (oy - hy) * hc)   # 호스트 축선에서 다른 벽 축선까지 수직 거리
    if dist > (hp["thickness"] + op["thickness"]) / 2.0:
        raise DobimError("CONSTRAINT", f"함께 자를 벽 {oid} 이 호스트 벽과 같은 선에 있지 않다(축선 거리 {dist:.0f}mm)", path=path,
                         details={"wall": oid, "axis_distance": round(dist, 3), "limit": (hp["thickness"] + op["thickness"]) / 2.0})
    e0, e1 = host_range(ctx, oid, op)
    us = [((ox + t * oc) - hx) * hc + ((oy + t * os_) - hy) * hs for t in (e0, e1)]
    dz = _level_elev(ctx, oid) - _level_elev(ctx, host_id)
    b = WT.base(op) + dz
    return (min(us), max(us), b, b + op["height"])


def check_fit(ctx, host_id: str, hp: dict, offset, elevation, cut: dict, values: dict, rotation, also, path_prefix: str = "") -> None:
    """개구부(호스트 지역 u·z)가 호스트 벽 안에 드는지 본다(25단계 규칙, 설계 메모 27절).
    - 가로: 축선 [0, 길이] ∪ 조인 형상 범위(host_range). 모서리 끝(end_l/end_r = corner)은 CORNER_END_ALLOW 까지 더 나가도 된다.
    - 그 밖으로 나가는 쪽(시작·끝·아래·위)은 also_cuts 의 벽 가운데 하나가 그 끝까지 닿아야 한다(쌓인 벽·이어지는 벽). 벽 사이 틈(슬래브 단부)은 비워 둔다.
    - 안 되면 CONSTRAINT 와 details(개구부·호스트 범위·넘친 길이·조인 상대·also_cuts 범위)."""
    x0, x1 = cut["x"]
    z0, z1 = cut["z"]
    u0, u1 = offset + x0, offset + x1
    w0, w1 = elevation + z0, elevation + z1
    e0, e1 = host_range(ctx, host_id, hp)
    flipped = FS.hosted_flipped(rotation)
    lo_end, hi_end = ("end_r", "end_l") if flipped else ("end_l", "end_r")   # 뒤집으면 지역 x 가 반대다
    a0 = CORNER_END_ALLOW if values.get(lo_end) == "corner" else 0.0
    a1 = CORNER_END_ALLOW if values.get(hi_end) == "corner" else 0.0
    b0 = WT.base(hp)
    top = b0 + hp["height"]
    over = {"start": max(0.0, (e0 - a0) - u0 - FIT_TOL), "end": max(0.0, u1 - (e1 + a1) - FIT_TOL),
            "bottom": max(0.0, b0 - w0 - 1e-9), "top": max(0.0, w1 - top - 1e-9)}
    if not any(over.values()):
        return
    boxes = [_also_box(ctx, host_id, hp, o, i) for i, o in enumerate(also or ())]
    for i, bx in enumerate(boxes):
        if min(u1, bx[1]) - max(u0, bx[0]) <= FIT_TOL or min(w1, bx[3]) - max(w0, bx[2]) <= FIT_TOL:
            raise DobimError("CONSTRAINT", f"함께 자를 벽 {also[i]} 이 개구부와 겹치지 않는다", path=f"{path_prefix}/also_cuts/{i}",
                             details={"wall": also[i], "opening": {"u": [u0, u1], "z": [w0, w1]}, "wall_box": {"u": bx[:2], "z": bx[2:]}})

    def u_ok(b):
        return min(w1, b[3]) - max(w0, b[2]) > FIT_TOL

    def z_ok(b):
        return min(u1, b[1]) - max(u0, b[0]) > FIT_TOL
    served = {"start": any(u_ok(b) and b[0] <= u0 + FIT_TOL for b in boxes),
              "end": any(u_ok(b) and b[1] >= u1 - FIT_TOL for b in boxes),
              "bottom": any(z_ok(b) and b[2] <= w0 + FIT_TOL for b in boxes),
              "top": any(z_ok(b) and b[3] >= w1 - FIT_TOL for b in boxes)}
    bad = [k for k, v in over.items() if v and not served[k]]
    if not bad:
        return
    length = C.sqrt(C._axis_len2(hp["axis"]))
    joined = sorted(set(ctx.rels_out("joins", host_id) + ctx.rels_in("joins", host_id)))
    details = {"opening": {"u": [round(u0, 3), round(u1, 3)], "z": [round(w0, 3), round(w1, 3)]},
               "host": {"id": host_id, "axis_length": round(length, 3), "range": [round(e0, 3), round(e1, 3)], "z": [b0, top]},
               "overflow": {k: round(v, 3) for k, v in over.items() if v}, "unserved": bad, "joined": joined[:8],
               "also_cuts": [{"id": o, "u": [round(b[0], 3), round(b[1], 3)], "z": [round(b[2], 3), round(b[3], 3)]} for o, b in zip(also or (), boxes)]}
    if any(k in ("start", "end") for k in bad):
        raise DobimError("CONSTRAINT", f"개구부({u0:.0f}..{u1:.0f}mm)가 호스트 벽({length:.1f}mm, 조인 범위 {e0:.0f}..{e1:.0f})"
                         f" 밖으로 {max(over['start'], over['end']):.0f}mm 나간다", path=path_prefix + "/offset",
                         hint="벽을 조인(join.set)해 모서리까지 잇거나, 같은 선으로 이어지는 벽을 also_cuts 로 함께 자르거나, 폭·offset 을 줄이세요",
                         details=details)
    raise DobimError("CONSTRAINT", "개구부가 호스트 벽의 높이 밖으로 나간다", path=path_prefix + "/elevation",
                     hint="위·아래로 쌓인 벽을 also_cuts 로 함께 자르거나, 벽을 상단 레벨(top_level)로 구속해 높이를 늘리세요", details=details)


def _check_hosted_rotation(ev, rotation) -> None:
    """호스팅 인스턴스의 rotation 은 0(그대로) 또는 180(뒤집기)만 받는다. 뒤집기는 개구부가 좌우 대칭일 때만(벽을 자르는 범위가 그대로여야 한다)."""
    r = float(rotation) % 360.0
    if abs(r) > 1e-6 and abs(r - 360.0) > 1e-6 and not FS.hosted_flipped(rotation):
        raise DobimError("CONSTRAINT", f"호스팅 인스턴스의 rotation 은 0 또는 180 이어야 한다({rotation:g})", path="/rotation",
                         hint="180 이면 문짝 열림 쪽과 경첩 쪽이 함께 뒤집힌다")
    cut = ev.get("cut")
    if FS.hosted_flipped(rotation) and cut and abs(cut["x"][0] + cut["x"][1]) > 1e-6:
        raise DobimError("CONSTRAINT", "개구부가 좌우 대칭이 아닌 패밀리는 뒤집을 수 없다", path="/rotation")


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
    if changed is None and WT.base(old_wp) != WT.base(new_wp):   # 바닥 오프셋(22단계)만 바뀌어도 높이 범위를 다시 본다
        changed = "base_offset"
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
        try:
            check_fit(ctx, wall_id, new_wp, p["offset"], p["elevation"], cut, p["values"], p.get("rotation", 0.0), p.get("also_cuts", ()))
        except DobimError as ex:
            high = ex.path.endswith("/elevation")
            msg = (f"호스팅된 패밀리 인스턴스 {d} 의 개구부가 새 벽 높이({new_wp['height']:g}mm) 밖으로 나간다" if high else
                   f"호스팅된 패밀리 인스턴스 {d} 의 개구부가 새 벽({length:.1f}mm) 밖으로 나간다")
            raise DobimError("CONSTRAINT", msg, path=path, hint=ex.hint, details={"instance": d, **(ex.details or {})}) from None
        w, h = x1 - x0, z1 - z0
        if w != p["width"] or h != p["height"]:
            ctx.put_el(d, dict(row, params=dict(p, width=w, height=h)))


def _inst_params(a_name, tid, values, ev, hosted, offset, position, rotation, elevation, also=()) -> dict:
    return FS.inst_params(a_name, tid, values, ev, hosted, offset, position, rotation, elevation, also)


def _also_list(also) -> tuple:
    """also_cuts(25단계) 정리: 없으면 (). 중복은 거절한다(순서는 준 대로 저장한다)."""
    if not also:
        return ()
    if len(set(also)) != len(also):
        raise DobimError("CONSTRAINT", "also_cuts 에 같은 벽이 두 번 있다", path="/also_cuts")
    return tuple(also)   # 정본은 family.spec (가져오기가 같은 식으로 대조한다)


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
        except FamilyError as ex:
            raise _fam_error(ex) from None
        tvals = _expand_transoms(r, a.type_values, a.transoms)
        try:
            tv, _ = FE.resolve_values(r, tvals, None)
        except FamilyError as ex:
            raise _fam_error(ex) from None
        _check_transoms(r, plan, tv, a.transoms)
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
    also = _also_list(a.also_cuts)
    ev, _ = _inst_geometry(ctx, tp, iv, a.host, a.offset, a.elevation, a.rotation, also)
    if hosted:
        _check_hosted_rotation(ev, a.rotation)
    name = a.name or f"{r.id.split('.')[0][:1].upper()}-{a.id[-6:]}"
    ctx.put_el(a.id, {"kind": "finst", "ifc_class": r.ifc.class_, "origin": "authored", "family_id": r.id,
                      "params": _inst_params(name, tid, iv, ev, hosted, a.offset, a.position, a.rotation, a.elevation, also)})
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
    also_cuts: AlsoCuts = Field(None, description="함께 자를 벽 id 목록(25단계). 주면 바꾼다([] 이면 뗀다), 생략하면 그대로")

    @model_validator(mode="after")
    def _any(self):
        if not self.values and all(v is None for v in (self.name, self.offset, self.position, self.rotation, self.elevation, self.also_cuts)):
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
    if a.also_cuts is not None and not hosted:
        raise DobimError("CONSTRAINT", "also_cuts 는 호스팅 인스턴스에만 쓴다", path="/also_cuts")
    also = _also_list(a.also_cuts) if a.also_cuts is not None else tuple(p.get("also_cuts", ()))
    rot = a.rotation if a.rotation is not None else p["rotation"]
    ev, _ = _inst_geometry(ctx, tp, iv, host_id, offset, elevation, rot, also)
    if hosted and a.rotation is not None:
        _check_hosted_rotation(ev, a.rotation)
    np_ = _inst_params(a.name or p["name"], p["type"], iv, ev, hosted, offset, a.position or p.get("position"),
                       rot, elevation, also)
    ctx.put_el(a.id, dict(r_, params=np_))
    for pset, props in sorted(ev["psets"]["instance"].items()):
        for prop, v in sorted(props.items()):
            ctx.set_prop(a.id, pset, prop, v)
    return {"id": a.id}


# ---------------------------------------------------------------- family.place_corner (24단계: 90도로 꺾인 모서리 창호, 25단계: 2~4개 벽 전개·모서리 판정 확장)
CORNER_COS_TOL = 0.0087   # 직각에서 0.5도 안
CORNER_REACH = 300.0      # 25단계: 조인하지 않은 두 벽의 축선 교점이 각 벽 축선 끝에서 이 거리(mm) 안이면 모서리로 본다(tol 로 바꾼다)
CORNER_SPAN_TOL = 1.0     # 가운데 벽(양 끝이 모서리)의 다리 길이와 두 모서리 사이 거리의 허용 차


class FamilyPlaceCorner(A):
    ids: Annotated[list[Id], Field(min_length=2, max_length=4)] | None = Field(None, description="다리 인스턴스 id(walls 와 같은 순서·개수). 비우면 자동")
    family: FamilyId = Field(description="모서리 끝(end_l/end_r 의 corner)을 지원하는 호스팅 패밀리(예: window.storefront)")
    walls: Annotated[list[Id], Field(min_length=2, max_length=4)] = Field(
        description="직각으로 차례로 꺾이는 벽 2~4개 [a, b, (c, d)]. 이웃한 두 벽은 축선 끝(또는 두 축선의 교점, join 으로 이어진 L)에서 만나야 한다")
    legs: Annotated[list[Annotated[Num, Field(gt=0)]], Field(min_length=2, max_length=4)] = Field(
        description="모서리(두 축선의 교점)에서 각 벽을 따라 창호가 차지하는 길이 mm(walls 와 같은 개수). 각 다리의 width 가 된다. 양 끝이 모서리인 가운데 벽은 두 모서리 사이 거리와 같아야 한다")
    type_values: Values = Field(default_factory=dict, description="다리 공통 타입 값(width 는 legs 로 정한다)")
    values: Values = Field(default_factory=dict, description="다리 공통 인스턴스 값(모서리 쪽 end_l/end_r 는 corner 로 정한다)")
    values_b: Values = Field(default_factory=dict, description="벽 b 쪽 다리(둘째)에만 덮어쓸 인스턴스 값(예: 문 위치)")
    values_by: Annotated[list[Values], Field(min_length=2, max_length=4)] | None = Field(None, description="다리별로 덮어쓸 인스턴스 값(walls 와 같은 개수, 25단계)")
    flip: Annotated[list[bool], Field(min_length=2, max_length=4)] | None = Field(None, description="다리별 뒤집기(rotation 180), walls 와 같은 개수. 생략하면 모두 false")
    tol: Annotated[Num, Field(ge=0, le=CORNER_END_ALLOW)] | None = Field(
        None, description=f"모서리 판정 허용 오차 mm(25단계): 두 축선의 교점이 각 벽 축선 끝에서 이 거리 안이면 모서리다. 생략하면 조인된 두 벽은 조인 규칙, 아니면 {CORNER_REACH:g}")
    transoms: Transoms = Field(None, description="가로대 높이 목록 mm(25단계, window.storefront 의 transom, transom2 ..)")
    name: Name | None = None

    @model_validator(mode="after")
    def _counts(self):
        n = len(self.walls)
        for k in ("legs", "ids", "flip", "values_by"):
            v = getattr(self, k)
            if v is not None and len(v) != n:
                raise ValueError(f"{k} 는 walls 와 개수가 같아야 한다({n}개)")
        return self


def _corner_between(ctx, i: int, wa: str, wb: str, axa, axb, tol):
    """이웃한 두 벽의 모서리: 두 축선(무한 직선)의 교점이 두 벽 모두의 한 끝 가까이에 있어야 한다.
    돌려주는 값: (교점, [벽 a 의 끝 'start'|'end', 교점의 지역 u], [벽 b 의 ...]). 허용 오차는 tol, 없으면 조인된 벽은 조인 규칙(JOIN_MAX_EXT), 아니면 CORNER_REACH."""
    path = f"/walls/{i + 1}"
    joined = wb in ctx.rels_out("joins", wa) or wa in ctx.rels_out("joins", wb)
    reach = float(tol) if tol is not None else (C.Q.JOIN_MAX_EXT if joined else CORNER_REACH)
    reach = max(reach, 1.0)   # 24단계(축선 끝이 1mm 안에서 맞닿음)는 늘 모서리다
    pt = C._join_pt(wa, axa, wb, axb)
    if pt is None:
        raise DobimError("CONSTRAINT", f"벽 {wa} 와 {wb} 가 평행해 모서리가 없다", path=path)
    ends, dist = [], {}
    for w, ax in ((wa, axa), (wb, axb)):
        x, y, c, s, length = C._frame_of(ax)
        t = (pt[0] - x) * c + (pt[1] - y) * s
        end = "start" if abs(t) <= abs(t - length) else "end"
        d = abs(t) if end == "start" else abs(t - length)
        dist[w] = round(d, 3)
        ends.append((end, t, (c, s)))
    far = {w: d for w, d in dist.items() if d > reach}
    if far:
        raise DobimError("CONSTRAINT", f"벽 {wa} 와 {wb} 의 축선 교점이 벽 끝에서 {max(far.values()):.0f}mm 떨어져 모서리로 보지 않는다(허용 {reach:g}mm)",
                         path=path, hint="tol 로 허용 오차를 늘리거나, 두 벽을 join.set 으로 이으세요(조인된 L 은 조인 규칙으로 모서리를 찾는다)",
                         details={"corner": [round(pt[0], 3), round(pt[1], 3)], "distance": dist, "reach": reach, "joined": joined})
    (ea, ta, da), (eb, tb, db) = ends
    sa = 1.0 if ea == "start" else -1.0   # 모서리에서 벽을 따라 멀어지는 방향
    sb = 1.0 if eb == "start" else -1.0
    if abs(da[0] * sa * db[0] * sb + da[1] * sa * db[1] * sb) > CORNER_COS_TOL:
        raise DobimError("CONSTRAINT", f"벽 {wa} 와 {wb} 가 직각으로 만나지 않는다", path=path)
    return pt, (ea, ta), (eb, tb)


@command("family.place_corner", kind="mutation", risk="write", group="family",
         summary="직각으로 차례로 꺾이는 벽 2~4개에 걸친 전개 창호를 한 단위로 놓는다. 각 다리는 모서리 쪽 끝이 corner(문틀 없음)인 호스팅 인스턴스다",
         args=FamilyPlaceCorner, since="0.4",
         examples=[{"family": "window.storefront", "walls": [C.F.W1, C.F.W2], "legs": [2500, 2000], "type_values": {"height": 2400, "bay_max": 1200}}])
def family_place_corner(ctx, a: FamilyPlaceCorner):
    n = len(a.walls)
    if len(set(a.walls)) != n:
        raise DobimError("CONSTRAINT", "같은 벽이 두 번 있다", path="/walls")
    if "width" in a.type_values:
        raise DobimError("CONSTRAINT", "width 는 legs 로 정한다", path="/type_values/width")
    rows = [ctx.authored(w, "wall", what="패밀리 인스턴스 호스팅") for w in a.walls]
    recipe = _recipe_of(ctx, a.family)
    try:
        r, _ = FE.load_recipe(recipe)
    except FamilyError as ex:
        raise _fam_error(ex) from None
    for k in ("end_l", "end_r", "width"):
        prm = r.params.get(k)
        if prm is None or (k != "width" and "corner" not in (prm.values or ())):
            raise DobimError("CONSTRAINT", f"{r.id} 는 모서리 창호를 지원하지 않는다(매개변수 {k} 가 없거나 corner 값이 없다)", path="/family",
                             hint="window.storefront 처럼 end_l/end_r 에 corner 가 있는 호스팅 패밀리를 쓰세요")
    _expand_transoms(r, a.type_values, a.transoms)   # 목록 모양만 미리 본다(다리마다 family.place 가 풀고 검사한다)
    lv = ctx.rels_out("on_level", a.walls[0])
    for i, w in enumerate(a.walls[1:], 1):
        if ctx.rels_out("on_level", w) != lv:
            raise DobimError("CONSTRAINT", "벽들이 같은 레벨에 있지 않다", path=f"/walls/{i}")
    axes = [rw["params"]["axis"] for rw in rows]
    corners = []
    sides: list[dict] = [{} for _ in range(n)]   # 벽마다 {'start'|'end': 교점의 지역 u}
    for i in range(n - 1):
        pt, (ea, ta), (eb, tb) = _corner_between(ctx, i, a.walls[i], a.walls[i + 1], axes[i], axes[i + 1], a.tol)
        for j, e, t in ((i, ea, ta), (i + 1, eb, tb)):
            if e in sides[j]:
                raise DobimError("CONSTRAINT", f"벽 {a.walls[j]} 의 같은 끝에 모서리가 둘이다(벽 순서를 확인하세요)", path=f"/walls/{j}")
            sides[j][e] = t
        corners.append(pt)
    ids = list(a.ids) if a.ids else [None] * n
    if a.ids and len(set(a.ids)) != n:
        raise DobimError("CONSTRAINT", "id 가 겹친다", path="/ids")
    flips = list(a.flip) if a.flip else [False] * n
    out = []
    for i, (w, leg) in enumerate(zip(a.walls, a.legs)):
        sd = sides[i]
        if len(sd) == 2:   # 가운데 벽: 두 모서리 사이 전체
            span = sd["end"] - sd["start"]
            if abs(span - leg) > CORNER_SPAN_TOL:
                raise DobimError("CONSTRAINT", f"가운데 벽 {w} 의 다리 길이({leg:g})가 두 모서리 사이 거리({span:.1f})와 다르다", path=f"/legs/{i}",
                                 details={"expected": round(span, 3), "given": leg})
            offset, width, cend = (sd["start"] + sd["end"]) / 2.0, span, {"start", "end"}
        else:
            e, t = next(iter(sd.items()))
            offset = t - leg / 2.0 if e == "end" else t + leg / 2.0
            width, cend = leg, {e}
        flipped = bool(flips[i])
        vals = {**a.values, **(a.values_b if i == 1 else {}), **((a.values_by or [{}] * n)[i])}
        for e in cend:   # 뒤집으면 지역 x 가 반대다
            key = ("end_r" if e == "end" else "end_l") if not flipped else ("end_l" if e == "end" else "end_r")
            vals[key] = "corner"
        nm = f"{a.name}-{'abcd'[i]}" if a.name else None
        res = family_place(ctx, FamilyPlace(id=ids[i], family=a.family, type_values={**a.type_values, "width": width}, values=vals,
                                            host=w, offset=offset, rotation=180.0 if flipped else 0.0, name=nm,
                                            **({"transoms": a.transoms} if a.transoms is not None else {})))
        out.append(res)
    for i, res in enumerate(out):   # 다리들을 한 모서리 창호로 묶는 표식(속성)
        ctx.set_prop(res["id"], "DoBIM_CornerUnit", "Leg", "abcd"[i])
        if n == 2:
            ctx.set_prop(res["id"], "DoBIM_CornerUnit", "Partner", out[1 - i]["id"])
        else:   # 3~4개 다리(25단계): 이웃 다리와 전체 묶음
            ctx.set_prop(res["id"], "DoBIM_CornerUnit", "Partner", ",".join(out[j]["id"] for j in (i - 1, i + 1) if 0 <= j < n))
            ctx.set_prop(res["id"], "DoBIM_CornerUnit", "Unit", ",".join(x["id"] for x in out))
    return {"ids": [x["id"] for x in out], "types": [x["type"] for x in out], "corner": [float(corners[0][0]), float(corners[0][1])],
            "corners": [[float(p[0]), float(p[1])] for p in corners]}


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
