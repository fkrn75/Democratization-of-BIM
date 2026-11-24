# SPDX-License-Identifier: MPL-2.0
"""S4 패밀리 커맨드: family.list / validate / register / load_dir / place / set / preview.

레시피(JSON, ADR-0008)를 요소 저장소에 올리는 경로는 이렇다.
  family.register : 레시피 + 타입 매개변수 값 → 타입 요소(ftype, IfcXxxType 로 투영). 타입 수준 속성셋을 IFC 에 쓴다.
  family.load_dir : 사용자 레시피 폴더(W1 1-F 5차, FAM-01) → id 마다 version 하나를 골라 기본 타입 값으로 register 한다.
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
BAY_COLS_MAX = 8          # 26단계: 한 단의 칸 열림 방식 상한(window.storefront bay1..bay8, window.framed 는 3칸)
BAY_ROWS_MAX = 5          # 26단계: 단 상한(가로대 4개 + 1)
COLS_MAX = 8              # 26단계: 세로대 폭 목록 상한(window.storefront col1..col8)
BayOp = Literal["same", "fixed", "project_out", "project_left", "project_right"]
BayRow = Annotated[list[BayOp], Field(min_length=1, max_length=BAY_COLS_MAX)]
Bays = Annotated[list[BayRow], Field(min_length=1, max_length=BAY_ROWS_MAX)] | BayRow | None
Cols = Annotated[list[Annotated[Num, Field(gt=0)]], Field(min_length=1, max_length=COLS_MAX)] | None
BAYS_DESC = ("칸별 열림 방식(26단계): 단 목록(아래 단부터) x 칸(지역 -x 부터) 의 열림 방식(same/fixed/project_out/project_left/project_right). "
             "평평한 목록 하나면 아래 단이다. window.framed 는 bay1..3, bay1_2..bay3_3 으로, window.storefront 는 한 단만(bay_row + bay1..8) 풀린다(family 로 지정할 때만)")
COLS_DESC = "세로대 폭 목록 mm(26단계, window.storefront): 왼쪽부터 칸 폭(세로대 중심 간격). 합이 구간보다 작으면 남은 폭이 마지막 칸이 된다(col1..col8 로 풀린다)"
STOREFRONT_DOOR_MIN_H = 2000.0   # 문(높이 1800 + 위 틀)이 들어가는 창호 높이 하한. 26단계에 창호 높이 하한을 400 으로 낮추며 문 쪽에만 남긴 규칙
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


_GATE_CACHE: dict[str, list] = {}   # 레시피 다이제스트 -> family.validate(static) 의 오류 목록(같은 레시피를 거듭 올릴 때 표본 평가를 다시 하지 않는다)
_GATE_CACHE_MAX = 256


def _static_errors(data: dict) -> list:
    """family.validate(level=static) 와 같은 판정에 IFC4 스키마 대조(full 의 ifc_schema)를 더한 오류 목록(Issue 사전).
    구조·식·참조·표본(기본값·min/max·열거값·호스트 두께)의 값·형상, 클래스·PredefinedType·USERDEFINED 의 ElementType/ObjectType."""
    from dobim_core.family.validate import validate_recipe
    key = FE.digest(data)
    hit = _GATE_CACHE.get(key)
    if hit is None:
        hit = validate_recipe(data, "static", ifc_schema=True)["errors"]   # IFC4 스키마 대조까지(W2 아홉 번째 PR, 실사용 결함 7)
        if len(_GATE_CACHE) >= _GATE_CACHE_MAX:
            _GATE_CACHE.pop(next(iter(_GATE_CACHE)))
        _GATE_CACHE[key] = hit
    return hit


def _gate_recipe(data: dict) -> None:
    """프로젝트에 새 레시피를 올리기 전에 family.validate(static) 와 같은 판정을 한다(W2 실사용 결함 5).
    예전에는 register 가 기본·지정 타입 값 하나만 평가해, 허용 범위의 극값에서 제약·형상이 깨지는 레시피도 올라갔다."""
    errs = _static_errors(data)
    if errs:
        first = errs[0]
        raise DobimError("FAMILY_INVALID", f"레시피 검증 실패: {first['message']}", path=first.get("path") or None, hint=first.get("hint"),
                         details={"total": len(errs), "issues": [_clip_issue(i) for i in errs[:5]],
                                  "check": "family.validate(level=static) 판정 + IFC4 스키마 대조(full 의 ifc_schema)다: family.validate 로 전체 오류를 보세요"})


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
    bays: Bays = Field(None, description=BAYS_DESC)
    cols: Cols = Field(None, description=COLS_DESC)


def _norm_bays(bays) -> list | None:
    """bays 인자를 단 목록(아래 단부터)으로 맞춘다. 평평한 목록은 아래 단 하나다."""
    if bays is None:
        return None
    return [list(bays)] if isinstance(bays[0], str) else [list(x) for x in bays]


def _expand_bays(r, type_values: dict, bays, path: str = "/bays") -> dict:
    """칸별 열림 방식 목록(26단계)을 레시피 타입 매개변수로 푼다. 목록이 없으면 그대로.
    - window.framed 형(bay_row 없음): 단 r 칸 c → bay{c}(아래 단) / bay{c}_{r}. 레시피에 그 매개변수가 없으면 거절.
    - window.storefront 형(bay_row 있음): same/fixed 아닌 값이 있는 단은 하나만 받는다(그 단 = bay_row). same 은 fixed 다."""
    rows = _norm_bays(bays)
    if rows is None:
        return type_values
    if any(k == "bay_row" or (k.startswith("bay") and k[3:4].isdigit()) for k in type_values):
        raise DobimError("CONSTRAINT", "bays 와 bay* 타입 값은 함께 줄 수 없다", path=path)
    out = dict(type_values)
    if "bay_row" in r.params:
        live = [i for i, row in enumerate(rows) if any(op not in ("same", "fixed") for op in row)]
        if len(live) > 1:
            raise DobimError("CONSTRAINT", f"{r.id} 는 열리는 칸을 한 단에만 둘 수 있다({len(live)}개 단에 있다)", path=path,
                             hint="열리는 칸이 있는 단 하나만 주세요(나머지 단은 고정)", details={"rows": [i + 1 for i in live]})
        row = rows[live[0]] if live else rows[0]
        have = [k for k in range(1, BAY_COLS_MAX + 1) if f"bay{k}" in r.params]
        if len(row) > len(have):
            raise DobimError("CONSTRAINT", f"{r.id} 는 칸 열림 방식을 {len(have)}칸까지만 받는다", path=path, details={"given": len(row), "max": len(have)})
        out["bay_row"] = (live[0] + 1) if live else 1
        for k, op in enumerate(row, 1):
            out[f"bay{k}"] = "fixed" if op == "same" else op
        return out
    for ri, row in enumerate(rows, 1):
        for ci, op in enumerate(row, 1):
            key = f"bay{ci}" if ri == 1 else f"bay{ci}_{ri}"
            if key not in r.params or r.params[key].kind != "type":
                ncol = sum(1 for c in range(1, BAY_COLS_MAX + 1) if f"bay{c}" in r.params)
                nrow = 1 + sum(1 for q in range(2, BAY_ROWS_MAX + 1) if f"bay1_{q}" in r.params) if ncol else 0
                raise DobimError("CONSTRAINT", f"{r.id} 는 {nrow}단 x {ncol}칸 까지만 열림 방식을 받는다", path=f"{path}/{ri - 1}/{ci - 1}",
                                 hint="window.framed(3단 x 3칸) 나 window.storefront 를 쓰세요", details={"rows": nrow, "cols": ncol})
            if op not in r.params[key].values:
                raise DobimError("CONSTRAINT", f"{r.id} 의 {key} 는 {op} 를 받지 않는다", path=f"{path}/{ri - 1}/{ci - 1}",
                                 details={"allowed": list(r.params[key].values)})
            out[key] = op
    return out


def _check_bays(r, plan, tv: dict, bays) -> None:
    """window.framed 형 칸 목록(26단계)이 실제 단·칸 안인지 본다: 단 수 <= 가로대 수 + 1, 칸 수 <= 멀리언 + 1.
    레시피는 없는 단·칸의 값을 무시하므로 목록으로 준 값이 버려지면 조용히 넘기지 않고 거절한다."""
    rows = _norm_bays(bays)
    if not rows or "bay_row" in r.params or "mullions" not in tv:
        return
    try:
        ev = FE.evaluate(r, plan, tv, None, None)
    except FamilyError as ex:
        raise _fam_error(ex, "타입") from None
    ntr = next((ps["TransomCount"] for ps in ev["psets"]["type"].values() if "TransomCount" in ps), 0)
    ncol = int(tv["mullions"]) + 1
    if len(rows) > ntr + 1 or any(len(row) > ncol for row in rows):
        raise DobimError("CONSTRAINT", f"bays 가 창의 단·칸({ntr + 1}단 x {ncol}칸)보다 많다", path="/bays",
                         hint="transoms 로 가로대를, mullions 로 멀리언을 먼저 정하세요",
                         details={"rows": ntr + 1, "cols": ncol, "given": [len(x) for x in rows]})


def _expand_cols(r, type_values: dict, cols, path: str = "/cols") -> dict:
    """세로대 폭 목록(26단계)을 col1..colN 타입 값으로 푼다. 목록이 없으면 그대로."""
    if cols is None:
        return type_values
    keys = [f"col{k}" for k in range(1, COLS_MAX + 1)]
    if any(k in type_values for k in keys):
        raise DobimError("CONSTRAINT", "cols 와 col* 타입 값은 함께 줄 수 없다", path=path)
    have = [k for k in keys if k in r.params and r.params[k].kind == "type"]
    if len(cols) > len(have):
        raise DobimError("CONSTRAINT", f"{r.id} 는 세로대 폭을 {len(have)}개까지만 받는다", path=path,
                         hint="window.storefront 처럼 col1, col2, ... 매개변수가 있는 패밀리를 쓰세요", details={"given": len(cols), "max": len(have)})
    out = dict(type_values)
    for k, v in zip(keys, cols):
        out[k] = v
    return out


def _check_door(r, tv: dict) -> None:
    """26단계: window.storefront 높이 하한을 400 으로 낮췄다. 문은 높이 2000 이상에서만 둘 수 있으므로(레시피는 그보다 낮으면 문을
    없는 것으로 본다) 문 폭을 준 낮은 창호는 조용히 문을 버리지 않고 거절한다."""
    if "door_w" in r.params and "height" in r.params and tv.get("door_w", 0) > 0 and tv.get("height", STOREFRONT_DOOR_MIN_H) < STOREFRONT_DOOR_MIN_H:
        raise DobimError("CONSTRAINT", f"문이 있는 창호는 높이가 {STOREFRONT_DOOR_MIN_H:g}mm 이상이어야 한다(height {tv['height']:g})",
                         path="/type_values/door_w", hint="door_w 를 0 으로 두거나 height 를 키우세요")


def _check_layout(tp: dict, ev: dict) -> None:
    """26단계 인스턴스 검사(window.storefront 형): 레시피가 맞지 않는 값을 없는 것으로 보는 칸 열림·세로대 폭을 거절한다.
    - col* 를 줬는데 이 인스턴스 구간(틀 안쪽, 모서리 끝이면 창호 끝 ~ 문)에 맞지 않으면(DoBIM_WindowParams.Columns = 0)
    - 열리는 칸 번호가 이 인스턴스의 칸 수를 넘거나, bay_row 단이 없거나, 문이 있는데 bay_row 가 아래 단이 아니면"""
    tv = tp["type_values"]
    ip = ev["psets"]["instance"].get("DoBIM_WindowParams", {})
    if tv.get("col1", 0) > 0 and ip.get("Columns") == 0:
        raise DobimError("CONSTRAINT", "세로대 폭 목록(cols)이 이 창호 구간에 맞지 않는다(각 칸은 frame_w + 100 이상, 합은 구간 폭 이하, 남은 폭은 0 또는 frame_w + 100 이상)",
                         path="/cols", details={"cols": [tv[f"col{k}"] for k in range(1, COLS_MAX + 1) if tv.get(f"col{k}", 0) > 0],
                                                "frame_w": tv.get("frame_w")})
    if "bay_row" not in tv:
        return
    live = [k for k in range(1, BAY_COLS_MAX + 1) if tv.get(f"bay{k}", "fixed") != "fixed"]
    if not live:
        return
    ntr = next((ps["TransomCount"] for ps in ev["psets"]["type"].values() if "TransomCount" in ps), 0)
    nb = ip.get("Bays", 0)
    if tv["bay_row"] > ntr + 1 or (tv["bay_row"] > 1 and ip.get("DoorWidth", 0) > 0) or max(live) > nb:
        raise DobimError("CONSTRAINT", f"열리는 칸이 창호에 없다(칸 {nb}개·단 {ntr + 1}개, 열리는 칸 {live}·단 {tv['bay_row']}; 문이 있으면 아래 단만)",
                         path="/bays", details={"bays": nb, "rows": ntr + 1, "open": live, "row": tv["bay_row"]})


def _expand_type(r, a) -> dict:
    """family 로 지정할 때 커맨드 목록 인자(transoms·bays·cols)를 타입 값으로 푼다."""
    tvals = _expand_transoms(r, a.type_values, a.transoms)
    tvals = _expand_bays(r, tvals, getattr(a, "bays", None))
    return _expand_cols(r, tvals, getattr(a, "cols", None))


def _check_type(r, plan, tv: dict, a) -> None:
    _check_transoms(r, plan, tv, a.transoms)
    _check_bays(r, plan, tv, getattr(a, "bays", None))
    _check_door(r, tv)


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
    if a.family is None:   # 사용자 레시피(path·recipe)는 family.validate(static) 와 같은 판정을 거친다. 기본 라이브러리는 시험이 지킨다
        _gate_recipe(data)
    tvals = _expand_type(r, a)
    try:
        tv, _ = FE.resolve_values(r, tvals, None)
    except FamilyError as ex:
        raise _fam_error(ex) from None
    _check_type(r, plan, tv, a)
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


# ---------------------------------------------------------------- family.load_dir (W1 1-F 5차, FAM-01)
DIR_FILES_MAX = 64        # 한 폴더에서 읽는 레시피 파일 상한(*.json, 하위 폴더는 보지 않는다)
REASON_MAX = 200          # 거절 이유 문장 길이 상한(응답 크기)


class FamilyLoadDir(A):
    path: str = Field(max_length=260, description="작업 폴더 안의 사용자 레시피 폴더. 그 바로 아래 *.json 만 읽는다(64개 이하, 파일마다 64KB 이하)")
    versions: Annotated[dict[FamilyId, Annotated[int, Field(ge=1, le=9999)]], Field(max_length=64)] = Field(
        default_factory=dict, description="id 마다 쓸 version. 생략한 id 는 폴더에서 가장 높은 version")
    builtin: Literal["skip", "override"] = Field(
        "skip", description="기본 라이브러리와 같은 id 인데 내용이 다른 레시피: skip=거절(기본), override=올린다(family 로 배치하면 이 프로젝트에 올린 쪽이 우선)")


def _reject(out: list, file: str | None, code: str, reason: str, **kw) -> None:
    row = {"file": file} if file is not None else {}
    out.append(row | kw | {"code": code, "reason": limits.clip_chars(reason, REASON_MAX)})


def _read_dir_recipes(folder, rejected: list) -> dict:
    """폴더의 *.json 을 이름순으로 읽어 검사한다. 통과한 것은 {id: {version: [(파일, 레시피, 해시), ...]}}, 실패는 rejected 에 이유와 함께 넣는다."""
    files = sorted((p for p in folder.iterdir() if p.suffix.lower() == ".json" and p.is_file()), key=lambda p: p.name)
    if len(files) > DIR_FILES_MAX:
        raise DobimError("CONSTRAINT", f"폴더에 레시피(*.json)가 {len(files)}개다({DIR_FILES_MAX}개 이하)", path="/path",
                         hint="폴더를 나눠 여러 번 불러오세요")
    root = folder.resolve()
    found: dict[str, dict[int, list]] = {}
    for p in files:
        if p.resolve().parent != root:   # 심볼릭 링크로 폴더 밖 파일을 가리키면 읽지 않는다
            _reject(rejected, p.name, "outside", "폴더 밖을 가리키는 링크라 읽지 않았다")
            continue
        if p.stat().st_size > MAX_RECIPE_FILE:
            _reject(rejected, p.name, "too_large", f"파일이 {MAX_RECIPE_FILE // 1024}KB 를 넘는다")
            continue
        try:
            data = load_json_text(p.read_text(encoding="utf-8"))
            r, _ = FE.load_recipe(data)
            errs = _static_errors(data)   # family.validate(static) 와 같은 판정(W2 실사용 결함 5)
            if errs:
                _reject(rejected, p.name, "invalid", f"{errs[0]['message']}" + (f" ({errs[0]['path']})" if errs[0].get("path") else ""))
                continue
        except UnicodeDecodeError:
            _reject(rejected, p.name, "invalid", "UTF-8 이 아니다")
            continue
        except FamilyError as ex:
            first = ex.issues[0]
            _reject(rejected, p.name, "invalid", f"{first.message}" + (f" ({first.path})" if first.path else ""))
            continue
        found.setdefault(r.id, {}).setdefault(r.version, []).append((p.name, data, FS.recipe_hash_of(data)))
    return found


@command("family.load_dir", kind="mutation", risk="write", group="family",
         summary="사용자 레시피 폴더를 불러와 패밀리 타입을 만든다(id 마다 version 하나)",
         args=FamilyLoadDir, since="0.14",
         examples=[{"path": "user-families"}, {"path": "user-families", "versions": {"user.stool": 1}}])
def family_load_dir(ctx, a: FamilyLoadDir):
    """규칙:
    - 폴더 바로 아래 *.json 을 이름순으로 읽는다. 크기 초과·UTF-8 아님·JSON 오류·레시피 검사 실패는 파일마다 이유와 함께 rejected 에 넣고 나머지는 계속한다
    - 같은 id 가 여럿이면 versions 로 준 version, 없으면 가장 높은 version 하나를 고른다. 같은 version 에 내용이 다른 파일이 둘 이상이면 그 id 는 모호해 거절한다
      (내용이 같은 파일은 하나로 본다). versions 에 준 version 이 폴더에 없으면 그 id 를 거절한다
    - 기본 라이브러리와 id 가 같고 내용이 다르면 builtin=skip(기본)은 거절, override 는 올린다. 내용까지 같으면 그대로 올린다(기본 라이브러리와 같은 타입)
    - 고른 레시피는 기본 타입 값으로 family.register 와 같은 타입을 만든다(이미 있으면 created=false). 이후 family.place family=<id> 가 이 타입의 레시피를 쓴다"""
    from dobim_core.family import library
    from types import SimpleNamespace
    folder = resolve_in_workspace(ctx.eng.workspace, a.path, must_exist=True)
    if not folder.is_dir():
        raise DobimError("CONSTRAINT", "폴더가 아니다", path="/path", hint="레시피 파일 하나는 family.register path 로 올리세요")
    rejected: list[dict] = []
    found = _read_dir_recipes(folder, rejected)
    for fid in sorted(set(a.versions) - set(found)):
        _reject(rejected, None, "version_not_found", "폴더에 이 id 의 레시피가 없다", id=fid, version=a.versions[fid])
    builtin_ids = set(library.ids())
    loaded = []
    plain = SimpleNamespace(type_values={}, transoms=None, bays=None, cols=None)
    for fid in sorted(found):
        by_ver = found[fid]
        avail = sorted(by_ver)
        ver = a.versions.get(fid, avail[-1])
        if ver not in by_ver:
            _reject(rejected, None, "version_not_found", f"version {ver} 이 폴더에 없다(있는 version {avail})", id=fid, version=ver)
            continue
        variants = {h: (name, data) for name, data, h in by_ver[ver]}
        files = [name for name, _, _ in by_ver[ver]]
        if len(variants) > 1:
            _reject(rejected, files[0], "ambiguous_version", f"version {ver} 에 내용이 다른 레시피가 {len(variants)}개다({', '.join(files[:4])})",
                    id=fid, version=ver)
            continue
        rhash, (name, data) = next(iter(variants.items()))
        override = fid in builtin_ids and rhash != FS.recipe_hash_of(library.load(fid))
        if override and a.builtin == "skip":
            _reject(rejected, name, "builtin_id", "기본 라이브러리와 같은 id 인데 내용이 다르다(id 를 바꾸거나 builtin=override)", id=fid, version=ver)
            continue
        r, plan = FE.load_recipe(data)
        try:
            tv, _ = FE.resolve_values(r, _expand_type(r, plain), None)
            _check_type(r, plan, tv, plain)
            FE.evaluate(r, plan, tv, None, None)
        except FamilyError as ex:
            _reject(rejected, name, "invalid", f"기본 타입 값으로 평가하지 못했다: {ex.issues[0].message}", id=fid, version=ver)
            continue
        except DobimError as ex:
            _reject(rejected, name, "invalid", f"기본 타입 값으로 평가하지 못했다: {ex.message}", id=fid, version=ver)
            continue
        tid = _content_tid(ctx, rhash, tv)
        created = tid not in ctx.st.el
        if created:
            _warn_same_id(ctx, r, rhash)
            _make_type(ctx, tid, data, r, plan, tv, None)
        elif ctx.st.el[tid]["kind"] != "ftype":
            _reject(rejected, name, "invalid", f"타입 id {tid} 가 다른 요소로 차 있다", id=fid, version=ver)
            continue
        row = {"id": fid, "version": ver, "file": name, "type": tid, "created": created}
        if len(avail) > 1:
            row["versions"] = avail
        if override:
            row["override"] = True
        loaded.append(row)
    if not loaded and not rejected:
        ctx.warn("폴더에 레시피(*.json)가 없다")
    return {"loaded": loaded, "rejected": rejected}


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
    also_cuts: AlsoCuts = Field(None, description="개구부가 호스트 벽 밖(위·아래로 쌓인 벽, 같은 선으로 이어지는 벽)으로 나갈 때 함께 자를 벽 id 목록(25단계). "
                                "26단계: 넘친 끝에 정확히 맞닿기만 하는(겹침 0) 같은 선 이웃 벽도 받는다")
    bays: Bays = Field(None, description=BAYS_DESC)
    cols: Cols = Field(None, description=COLS_DESC)

    @model_validator(mode="after")
    def _shape(self):
        if (self.type is None) == (self.family is None):
            raise ValueError("type 과 family 중 정확히 하나를 지정해야 한다")
        if self.type is not None and (self.type_values or self.transoms is not None or self.bays is not None or self.cols is not None):
            raise ValueError("type 을 지정하면 type_values·transoms·bays·cols 는 줄 수 없다(타입은 이미 값이 정해졌다)")
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
    _check_layout(tp, ev)
    for msg in ev.get("host_warnings", ()):   # 얇은 벽(W2 열세 번째 PR, ARC-23): 호스트 제약은 거절하지 않고 경고(문틀·문짝이 벽면 밖으로 나온다)
        ctx.warn(f"호스트 벽 {host_id}({host_ctx['host_thickness']:g}mm)이 레시피 권장을 벗어나지만 놓는다: {msg}")
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
    if "arc" in wp:   # 곡선 벽(W2 ARC-04): 축 [0, 호 길이] 만 본다(조인 연장 범위는 아직 보지 않는다)
        return (0.0, C.wall_len(wp))
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
    if "arc" in op or "arc" in hp:
        raise DobimError("CONSTRAINT", "곡선 벽은 아직 함께 자르기(also_cuts)를 지원하지 않는다", path=path)
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


def _touch_side(u0, u1, w0, w1, bx, ou, oz):
    """26단계: 함께 자를 벽 상자 bx 가 개구부에 맞닿기만 하는지(한 방향 겹침이 ±FIT_TOL 안, 다른 방향은 실제로 겹침).
    맞닿으면 개구부의 그 쪽 이름(start/end/bottom/top), 아니면 None."""
    if -FIT_TOL <= ou <= FIT_TOL and oz > FIT_TOL:
        if abs(bx[0] - u1) <= FIT_TOL:
            return "end"
        if abs(bx[1] - u0) <= FIT_TOL:
            return "start"
    if -FIT_TOL <= oz <= FIT_TOL and ou > FIT_TOL:
        if abs(bx[2] - w1) <= FIT_TOL:
            return "top"
        if abs(bx[3] - w0) <= FIT_TOL:
            return "bottom"
    return None


def check_fit(ctx, host_id: str, hp: dict, offset, elevation, cut: dict, values: dict, rotation, also, path_prefix: str = "") -> None:
    """개구부(호스트 지역 u·z)가 호스트 벽 안에 드는지 본다(25단계 규칙, 설계 메모 27절).
    - 가로: 축선 [0, 길이] ∪ 조인 형상 범위(host_range). 모서리 끝(end_l/end_r = corner)은 CORNER_END_ALLOW 까지 더 나가도 된다.
    - 그 밖으로 나가는 쪽(시작·끝·아래·위)은 also_cuts 의 벽 가운데 하나가 그 끝까지 닿아야 한다(쌓인 벽·이어지는 벽). 벽 사이 틈(슬래브 단부)은 비워 둔다.
    - 26단계: also_cuts 벽이 개구부의 넘친 끝에 정확히 맞닿기만 해도(겹침 ±FIT_TOL) 그 끝을 받는다(틀 자리 틈 = 이웃 벽 끝).
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
        ou = min(u1, bx[1]) - max(u0, bx[0])   # 개구부와 그 벽의 가로·세로 겹침(음수면 틈)
        oz = min(w1, bx[3]) - max(w0, bx[2])
        touch = _touch_side(u0, u1, w0, w1, bx, ou, oz)
        # 26단계: 겹침 0(틀 자리 틈을 두고 개구부 끝이 이웃 벽 끝에 정확히 맞닿음)도 허용 오차 안이면 받는다.
        # 맞닿기만 하는 벽은 그 쪽(start/end/bottom/top)이 실제로 넘칠 때만 의미가 있다(넘친 끝을 막는 벽). 그 벽에는 빈 개구부가 생기지 않는다(queries.wall_xcuts)
        bad_touch = touch is not None and not over[touch]
        if (ou <= FIT_TOL or oz <= FIT_TOL) and touch is None or bad_touch:
            raise DobimError("CONSTRAINT", f"함께 자를 벽 {also[i]} 이 개구부와 겹치지 않는다" + ("(맞닿은 쪽으로 개구부가 넘치지 않는다)" if bad_touch else ""),
                             path=f"{path_prefix}/also_cuts/{i}",
                             details={"wall": also[i], "opening": {"u": [u0, u1], "z": [w0, w1]}, "wall_box": {"u": bx[:2], "z": bx[2:]},
                                      **({"touch": touch} if touch else {})})

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
    length = C.wall_len(hp)
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
    if changed is None and old_wp.get("arc") != new_wp.get("arc"):   # 곡선 벽(W2 ARC-04)의 호만 바뀌었다(끝점이 같아도 호 길이가 바뀐다)
        changed = "axis"
    if changed is None:
        return
    path = "/" + changed
    length = C.wall_len(new_wp)
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
        for msg in ev.get("host_warnings", ()):   # 얇아진 벽(W2 열세 번째 PR): 거절하지 않고 경고
            ctx.warn(f"호스팅된 패밀리 인스턴스 {d} 가 새 벽 치수에서 레시피 권장을 벗어난다: {msg}")
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
        tvals = _expand_type(r, a)
        try:
            tv, _ = FE.resolve_values(r, tvals, None)
        except FamilyError as ex:
            raise _fam_error(ex) from None
        _check_type(r, plan, tv, a)
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


# ---------------------------------------------------------------- family.set_type (W1 1-F 3차, UI-04: 패밀리 유형 바꾸기)
class FamilySetType(A):
    id: Id
    type: Id | None = Field(None, description="바꿀 타입(ftype) id. 같은 패밀리여야 한다")
    type_values: Values | None = Field(None, description="또는 지금 타입 값 위에 덮어쓸 값(같은 내용의 타입을 다시 쓰거나 만든다)")

    @model_validator(mode="after")
    def _one(self):
        if (self.type is None) == (self.type_values is None):
            raise ValueError("type 과 type_values 중 하나만 준다")
        return self


@command("family.set_type", kind="mutation", risk="write", group="family",
         summary="패밀리 인스턴스의 타입을 바꾼다(같은 패밀리). 인스턴스 값·위치는 그대로 두고 새 타입으로 다시 평가한다",
         args=FamilySetType, since="0.13",
         examples=[{"id": EXAMPLE_FINST, "type_values": {"width": 1000}}])
def family_set_type(ctx, a: FamilySetType):
    r_ = ctx.authored(a.id, "finst")
    p = dict(r_["params"])
    old = p["type"]
    if a.type is not None:
        tid = a.type
        tp = ctx.row(tid, "ftype")["params"]
        r, plan = FE.load_recipe(tp["recipe"])
        made = False
    else:
        otp = ctx.st.el[old]["params"]
        recipe = otp["recipe"]
        r, plan = FE.load_recipe(recipe)
        try:
            tv, _ = FE.resolve_values(r, {**otp["type_values"], **a.type_values}, None)
        except FamilyError as ex:
            raise _fam_error(ex) from None
        _check_door(r, tv)
        tid = _content_tid(ctx, FS.recipe_hash_of(recipe), tv)
        made = tid not in ctx.st.el
        if made:
            _make_type(ctx, tid, recipe, r, plan, tv, None)
        tp = ctx.st.el[tid]["params"]
    if r.id != r_["family_id"]:
        raise DobimError("CONSTRAINT", f"다른 패밀리의 타입이다({r.id}, 인스턴스는 {r_['family_id']})", path="/type")
    if tid == old:
        raise DobimError("CONSTRAINT", "지금과 같은 타입이다", path="/type" if a.type is not None else "/type_values")
    hosted = "offset" in p
    try:
        _, iv = FE.resolve_values(r, tp["type_values"], p["values"])
    except FamilyError as ex:
        raise _fam_error(ex) from None
    host_id = ctx.rels_in("hosts", a.id)[0] if hosted else None
    also = tuple(p.get("also_cuts", ()))
    ev, _ = _inst_geometry(ctx, tp, iv, host_id, p.get("offset"), p["elevation"], p["rotation"], also)
    np_ = _inst_params(p["name"], tid, iv, ev, hosted, p.get("offset"), p.get("position"), p["rotation"], p["elevation"], also)
    ctx.put_el(a.id, dict(r_, params=np_))
    ctx.rel("of_type", a.id, old, False)
    ctx.rel("of_type", a.id, tid, True)
    for pset, props in sorted(ev["psets"]["instance"].items()):
        for prop, v in sorted(props.items()):
            ctx.set_prop(a.id, pset, prop, v)
    return {"id": a.id, "type": tid, "from": old, "type_created": made}


# ---------------------------------------------------------------- family.place_corner (24단계: 90도로 꺾인 모서리 창호, 25단계: 2~4개 벽 전개·모서리 판정 확장)
CORNER_COS_TOL = 0.0087   # 직각에서 0.5도 안
CORNER_REACH = 300.0      # 25단계: 조인하지 않은 두 벽의 축선 교점이 각 벽 축선 끝에서 이 거리(mm) 안이면 모서리로 본다(tol 로 바꾼다)
CORNER_SPAN_TOL = 1.0     # 가운데 벽(양 끝이 모서리)의 다리 길이와 두 모서리 사이 거리의 허용 차


class FamilyPlaceCorner(A):
    ids: Annotated[list[Id], Field(min_length=2, max_length=4)] | None = Field(None, description="다리 인스턴스 id(walls 와 같은 순서·개수). 비우면 자동")
    family: FamilyId = Field(description="모서리 끝(end_l/end_r 의 corner)을 지원하는 호스팅 패밀리(예: window.storefront)")
    walls: Annotated[list[Id], Field(min_length=2, max_length=4)] = Field(
        description="직각으로 차례로 꺾이는 벽 2~4개 [a, b, (c, d)]. 이웃한 두 벽은 축선 끝(또는 두 축선의 교점, join 으로 이어진 L)에서 만나야 한다. "
                    "26단계: 평행하게 어긋난(축선 거리 600mm 이하) 다음 구간도 끝이 이어지면(tol 안) 단차로 잇는다(앞 벽 끝 = 단차, 앞 다리는 문틀·뒤 다리는 corner)")
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
        return _step_between(ctx, i, wa, wb, axa, axb, reach)
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
    return pt, (ea, ta), (eb, tb), "corner"


STEP_OFFSET_MAX = 600.0   # 26단계: 평행한 다음 구간의 축선 어긋남(수직 거리) 상한 mm. 이보다 멀면 같은 창호의 단차로 보지 않는다


def _step_between(ctx, i: int, wa: str, wb: str, axa, axb, reach: float):
    """26단계: 평행하게 어긋난 다음 구간(단차). 두 축선이 평행(교점 없음)하고 수직 거리가 STEP_OFFSET_MAX 이하이며,
    벽 a 의 한 끝과 벽 b 의 한 끝이 길이 방향으로 reach 안(틈 또는 겹침)에 있고 두 벽이 서로 반대쪽으로 뻗어야 한다.
    단차 위치 = 벽 a 의 그 끝(축선 끝). 벽 b 의 지역 u 는 그 점을 b 축선에 투영한 값이다.
    돌려주는 값은 _corner_between 과 같고 마지막이 'step' 이다(앞 다리 끝은 문틀 = 단차 세로대, 뒤 다리 끝은 corner)."""
    path = f"/walls/{i + 1}"
    xa, ya, ca, sa, la = C._frame_of(axa)
    xb, yb, cb, sb, lb = C._frame_of(axb)
    off = abs((xb - xa) * sa - (yb - ya) * ca)   # 벽 b 축선에서 벽 a 축선까지 수직 거리(평행이라 어느 점이든 같다)
    if off > STEP_OFFSET_MAX:
        raise DobimError("CONSTRAINT", f"벽 {wa} 와 {wb} 가 평행하고 {off:.0f}mm 떨어져 모서리도 단차도 아니다(단차 허용 {STEP_OFFSET_MAX:g}mm)",
                         path=path, hint="평행한 다음 구간은 축선이 단차 허용 안에서 어긋나고 끝이 맞닿아야 한다",
                         details={"offset": round(off, 3), "limit": STEP_OFFSET_MAX})

    def along(px, py):
        return (px - xa) * ca + (py - ya) * sa
    bpts = {"start": (xb, yb), "end": (xb + cb * lb, yb + sb * lb)}
    best = None
    for ea, ta in (("start", 0.0), ("end", la)):
        ina = 1.0 if ea == "start" else -1.0                 # 그 끝에서 벽 a 안쪽으로 가는 길이 방향
        for eb, q in bpts.items():
            other = bpts["end" if eb == "start" else "start"]
            inb = 1.0 if along(*other) > along(*q) else -1.0  # 그 끝에서 벽 b 안쪽으로 가는 길이 방향
            if ina * inb >= 0:
                continue                                      # 같은 쪽으로 뻗으면 이어지는 구간이 아니다(겹친 평행 벽)
            gap = (along(*q) - ta) * -ina                     # + 틈, - 겹침
            if best is None or abs(gap) < abs(best[4]):
                best = (ea, ta, eb, q, gap)
    if best is None or abs(best[4]) > reach:
        raise DobimError("CONSTRAINT", f"벽 {wa} 와 {wb} 가 평행해 모서리가 없고, 끝이 이어지지 않아 단차로도 보지 않는다",
                         path=path, hint="tol 로 허용 오차를 늘리거나 벽 끝을 맞추세요",
                         details={"offset": round(off, 3), "gap": round(best[4], 3) if best else None, "reach": reach})
    ea, ta, eb, _, _ = best
    pt = (xa + ta * ca, ya + ta * sa)
    tb = (pt[0] - xb) * cb + (pt[1] - yb) * sb
    return pt, (ea, ta), (eb, tb), "step"


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
    for i, rw in enumerate(rows):
        if "arc" in rw["params"]:
            raise DobimError("CONSTRAINT", "곡선 벽은 아직 모서리 창호를 지원하지 않는다", path=f"/walls/{i}")
    axes = [rw["params"]["axis"] for rw in rows]
    corners = []
    sides: list[dict] = [{} for _ in range(n)]   # 벽마다 {'start'|'end': 교점의 지역 u}
    cends: list[set] = [set() for _ in range(n)]  # 벽마다 corner(문틀 없음)로 둘 끝. 단차(26단계)의 앞 다리 끝은 문틀로 남긴다
    joints = []
    for i in range(n - 1):
        pt, (ea, ta), (eb, tb), kind = _corner_between(ctx, i, a.walls[i], a.walls[i + 1], axes[i], axes[i + 1], a.tol)
        for j, e, t in ((i, ea, ta), (i + 1, eb, tb)):
            if e in sides[j]:
                raise DobimError("CONSTRAINT", f"벽 {a.walls[j]} 의 같은 끝에 모서리가 둘이다(벽 순서를 확인하세요)", path=f"/walls/{j}")
            sides[j][e] = t
        cends[i + 1].add(eb)
        if kind == "corner":
            cends[i].add(ea)
        corners.append(pt)
        joints.append(kind)
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
            offset, width, cend = (sd["start"] + sd["end"]) / 2.0, span, cends[i]
        else:
            e, t = next(iter(sd.items()))
            offset = t - leg / 2.0 if e == "end" else t + leg / 2.0
            width, cend = leg, cends[i]
        flipped = bool(flips[i])
        vals = {**a.values, **(a.values_b if i == 1 else {}), **((a.values_by or [{}] * n)[i])}
        for e in sorted(cend):   # 뒤집으면 지역 x 가 반대다
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
            "corners": [[float(p[0]), float(p[1])] for p in corners], "joints": joints}


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
