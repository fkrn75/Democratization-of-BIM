# SPDX-License-Identifier: MPL-2.0
"""S1 커맨드 정의. 레지스트리 항목이 곧 기능이다 (15.1절).

이 모듈은 ifcopenshell 을 import 하지 않는다. 상태는 ctx 의 효과(emit)로만 바꾼다.
세션 커맨드(needs_project=False)는 (env, args), 나머지는 (ctx, args) 를 받는다.
"""
from __future__ import annotations

import base64
import bisect
import json
from pathlib import Path
from math import sqrt
import types
from typing import Annotated, ClassVar, Literal, Union, get_args, get_origin

from pydantic import (AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr,
                      ValidationInfo, field_validator, model_validator)

from dobim_core import fixture as F
from dobim_core import limits
from dobim_core import queries as Q
from dobim_core import registry
from dobim_core.errors import DobimError
from dobim_core.paths import resolve_in_workspace
from dobim_core.registry import command

RESERVED_PSET = "DoBIM_Intent"
Id = Annotated[str, Field(pattern=r"^[0-9A-Za-z_$]{22}$", description="IFC GlobalId (22자)")]
Num = Annotated[float, Field(allow_inf_nan=False)]
Pos = Annotated[float, Field(gt=0, allow_inf_nan=False)]
def _text_ok(s: str, info: ValidationInfo) -> str:
    if "\x00" in s or any(0xD800 <= ord(c) <= 0xDFFF for c in s):
        raise ValueError("NUL 이나 홑서로게이트는 허용하지 않는다")
    # 길이 상한은 새 입력에만 건다. 이미 로그에 있는 항목을 다시 실행할 때(replay)는 건너뛰어, 상한이 생기기 전의 옛 로그가 재생에서 깨지지 않는다.
    # (재오픈과 다른 프로세스의 쓰기 따라잡기는 효과를 직접 적용하므로 스키마를 거치지 않는다.)
    if len(s) > limits.STR_MAX and not (info.context or {}).get("replay"):
        raise ValueError(f"문자열은 {limits.STR_MAX}자 이하여야 한다(현재 {len(s)}자)")
    return s


def _only_float(v):
    if type(v) is not float:   # 범위를 넘은 정수가 float 로 조용히 바뀌는 것을 막는다
        raise ValueError("float 가 아니다")
    return v


# 속성 문자열 값의 길이 상한 255자: IFC4 의 IfcLabel·IfcIdentifier 가 255자이고 방화등급·재질·분류 코드·제품 번호·층 이름 같은 속성값이 이 틀에
# 든다(Revit 의 공유 매개변수 이름도 같은 규모). 긴 서술형 텍스트(IfcText)는 S1 속성 범위 밖이다. 상한 덕에 단건 응답(history.list 항목의 args,
# element.get 의 속성 하나, 일람표 셀)이 어떤 문자 종류로 채워져도 응답 상한 안에 든다(255자 x 최악 6바이트 = 1.5KB).
Text = Annotated[StrictStr, Field(json_schema_extra={"maxLength": limits.STR_MAX}), AfterValidator(_text_ok)]
Name = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_text_ok)]
# 속성 값: 저장값이 입력과 같아야 하므로 NaN/inf, int64 초과, NUL, 홑서로게이트는 스키마에서 거부한다.
# null(None) 도 거부한다: 표준 JSON 에는 NaN/inf 가 없어 공식 MCP SDK 클라이언트가 이를 null 로 직렬화하는데,
# null 이 "삭제"를 뜻하면 서버가 NaN 과 명시적 삭제를 구분할 수 없다. 삭제는 별도 커맨드 pset.delete 로만 한다.
PropValue = Union[StrictBool, Annotated[StrictInt, Field(ge=-(2**63), le=2**63 - 1)],
                  Annotated[StrictFloat, BeforeValidator(_only_float), Field(allow_inf_nan=False)], Text]


def _prop_name_ok(s: str) -> str:
    # ifcopenshell.util.element.get_psets 는 속성 맵마다 IFC 엔티티 번호를 "id" 키로 끼워 넣는다. 속성 이름이 "id" 이면
    # 쓰기는 성공해도 조회에서 가려지고 지울 수도 없다(상태가 속성 맵의 함수가 아니게 된다). "ID", "Id" 같은 변형은
    # 이 충돌이 없고 IFC 이름은 대소문자를 구분하므로(왕복·삭제 시험으로 확인) 정확히 "id" 만 막는다.
    if s == "id":
        raise ValueError('속성 이름 "id" 는 IFC 라이브러리가 엔티티 번호용으로 예약했다')
    return s


# 함수 객체를 직접 넘기지 않고 호출 시점에 모듈 이름을 찾는다(변이 시험이 검사를 끌 수 있게 하기 위한 간접 호출)
PsetName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]
PropName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$", description='속성 이름. "id" 는 예약'),
                     AfterValidator(lambda s: _prop_name_ok(s))]


def _optional_numeric(ann) -> bool:
    """`X | None` 이면서 X 가 숫자(float/int, 리스트 안 포함)인 주석인가."""
    def parts(a):
        if get_origin(a) is Annotated:
            return parts(get_args(a)[0])
        if get_origin(a) in (Union, types.UnionType):
            return [x for arg in get_args(a) for x in parts(arg)]
        if get_origin(a) is list:
            return [x for arg in get_args(a) for x in parts(arg)]
        return [a]
    leaves = parts(ann)
    return type(None) in leaves and any(x in (float, int) for x in leaves)


class A(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # True 이면 이 커맨드의 선택 필드는 어느 것도 명시적 null 을 받지 않는다(바꾸지 않으려면 필드를 생략한다)
    forbid_null: ClassVar[bool] = False

    @field_validator("*", mode="before")
    @classmethod
    def _no_explicit_null(cls, v, info: ValidationInfo):
        """N10: 표준 JSON 에는 NaN/inf 가 없어 공식 SDK 클라이언트가 null 로 바꿔 보낸다. `X | None = None` 필드가 null 을
        "바꾸지 않음"으로 읽으면 NaN 을 보낸 그 필드만 조용히 무시되고 ok 가 된다. 숫자 선택 필드(와 forbid_null 커맨드의
        모든 선택 필드)의 명시적 null 은 SCHEMA_INVALID 로 거부한다. 생략은 그대로 '바꾸지 않음'이다."""
        if v is None and (cls.forbid_null or _optional_numeric(cls.model_fields[info.field_name].annotation)):
            raise ValueError("null 은 허용하지 않는다(필드를 생략하면 바꾸지 않는다). NaN/inf 는 JSON 에 없어 SDK 가 null 로 보낸다")
        return v


def row(kind, ifc_class, family, params):
    return {"kind": kind, "ifc_class": ifc_class, "origin": "authored", "family_id": family, "params": params}


def _axis_len2(axis):
    dx, dy = axis[2] - axis[0], axis[3] - axis[1]
    return dx * dx + dy * dy


def _fresh_id(ctx, a, kind):
    a.id = a.id or ctx.new_id(kind)
    if a.id in ctx.st.el:
        raise DobimError("CONSTRAINT", f"이미 있는 id 다: {a.id}", path="/id")
    return a.id


# ================================================================ 프로젝트·시스템
class ProjectNew(A):
    path: str = Field(max_length=260, description="작업 폴더 안의 .dobim 경로")
    guid: str | None = Field(None, max_length=36, description="프로젝트 UUID. 생략하면 무작위, 지정하면 재현 가능")


@command("project.new", kind="session", risk="write", group="project", summary="새 프로젝트 파일(.dobim)을 만든다",
         args=ProjectNew, needs_project=False, examples=[{"path": "new-example.dobim", "guid": "5d0b1a50-0000-4a00-8000-0d0b1a5000f2"}])
def project_new(env, a: ProjectNew):
    from dobim_core.engine import Engine
    p = resolve_in_workspace(env.workspace, a.path)
    eng = Engine.create(p, env.actor, guid=a.guid)
    try:
        return {"path": p.name, "rev": eng.rev, "project_uuid": eng.project_guid_uuid}
    finally:
        eng.close()


class Empty(A):
    pass


@command("project.info", kind="query", risk="read", group="project", summary="프로젝트 리비전·요소 수·이력 깊이",
         args=Empty, examples=[{}], idempotent=True)
def project_info(ctx, a):
    g = ctx.g
    return {"project_uuid": ctx.eng.project_guid_uuid, "rev": ctx.eng.rev, "schema_version": "1",
            "counts": g.q(Q.model_stats), "undo_depth": len(ctx.eng.undo_stack),
            "redo_depth": len(ctx.eng.redo_stack)}


class Search(A):
    query: str = Field("", max_length=256)
    group: str | None = Field(None, max_length=64)
    risk: Literal["read", "write", "destructive", "code_exec"] | None = None
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("commands.search", kind="query", risk="read", group="system", summary="커맨드를 키워드·그룹·위험 등급으로 찾는다",
         args=Search, needs_project=False, idempotent=True, examples=[{"query": "wall"}, {"group": "history"}, {"limit": 5}])
def commands_search(env, a: Search):
    rows = registry.search(a.query, a.group, a.risk)
    after = _dec_cursor(a.cursor, "name")
    start = 0 if after is None else next((i for i, c in enumerate(rows) if c["name"] > after), len(rows))
    window = rows[start:start + a.limit]
    items, cut = _fit(window, cursor_of=lambda i: _enc_cursor(window[i]["name"]), trusted=True)   # 레지스트리 내용: 입력이 섞이지 않는다
    more = start + len(items) < len(rows)
    return {"items": items, "page": _page(_enc_cursor(items[-1]["name"]) if more else None, len(rows), cut)}


class Describe(A):
    name: str = Field(max_length=64)


@command("commands.describe", kind="query", risk="read", group="system", summary="커맨드 하나의 스키마와 예시",
         args=Describe, needs_project=False, idempotent=True, examples=[{"name": "wall.move"}])
def commands_describe(env, a: Describe):
    c = registry.load().get(a.name)
    if c is None:
        raise DobimError("UNKNOWN_COMMAND", f"없는 커맨드: {a.name}", path="/name",
                         hint="commands.search 로 찾으세요")
    return c.describe()


# ================================================================ 모델링
class LevelCreate(A):
    id: Id | None = None
    name: Name
    elevation: Num = Field(description="mm")


@command("level.create", kind="mutation", risk="write", group="model.level", summary="레벨(층)을 만든다",
         args=LevelCreate, since="0.1", examples=[{"name": "3F", "elevation": 6600}])
def level_create(ctx, a: LevelCreate):
    _fresh_id(ctx, a, "level")
    for lv in ctx.st.by_kind.get("level", ()):
        if ctx.st.el[lv]["params"]["name"] == a.name:
            raise DobimError("CONSTRAINT", f"같은 이름의 레벨이 있다: {a.name}", path="/name")
    ctx.put_el(a.id, row("level", "IfcBuildingStorey", "level.basic", {"name": a.name, "elevation": a.elevation}))
    return {"id": a.id}


class WallCreate(A):
    id: Id | None = None
    level: Id
    axis: Annotated[list[Num], Field(min_length=4, max_length=4, description="[x1,y1,x2,y2] mm")]
    thickness: Pos = 200.0
    height: Pos = 3000.0
    name: Name | None = None


@command("wall.create", kind="mutation", risk="write", group="model.wall", summary="벽을 만든다(축선 기준)",
         args=WallCreate, since="0.1",
         examples=[{"level": F.LV1, "axis": [0, 4000, 6000, 4000], "thickness": 200}])
def wall_create(ctx, a: WallCreate):
    _fresh_id(ctx, a, "wall")
    ctx.row(a.level, "level")
    if _axis_len2(a.axis) < 1.0:
        raise DobimError("CONSTRAINT", "벽 길이는 1mm 이상이어야 한다", path="/axis")
    a.name = a.name or f"W-{a.id[-6:]}"
    ctx.put_el(a.id, row("wall", "IfcWall", "wall.basic", {
        "name": a.name, "axis": list(a.axis), "thickness": a.thickness, "height": a.height}))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id}


class WallMove(A):
    id: Id
    dx: Num = 0.0
    dy: Num = 0.0


@command("wall.move", kind="mutation", risk="write", group="model.wall", summary="벽을 평행 이동한다",
         args=WallMove, since="0.1", examples=[{"id": F.W1, "dx": 600, "dy": 0}])
def wall_move(ctx, a: WallMove):
    r = ctx.row(a.id, "wall")
    x1, y1, x2, y2 = r["params"]["axis"]
    p = dict(r["params"], axis=[x1 + a.dx, y1 + a.dy, x2 + a.dx, y2 + a.dy])
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "axis": p["axis"]}


class WallSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    thickness: Pos | None = None
    height: Pos | None = None
    axis: Annotated[list[Num], Field(min_length=4, max_length=4)] | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.thickness, self.height, self.axis)):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("wall.set", kind="mutation", risk="write", group="model.wall", summary="벽의 이름·두께·높이·축선을 바꾼다",
         args=WallSet, since="0.1", examples=[{"id": F.W1, "thickness": 250}])
def wall_set(ctx, a: WallSet):
    r = ctx.row(a.id, "wall")
    p = dict(r["params"])
    for k in ("name", "thickness", "height"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    if a.axis is not None:
        if _axis_len2(a.axis) < 1.0:
            raise DobimError("CONSTRAINT", "벽 길이는 1mm 이상이어야 한다", path="/axis")
        p["axis"] = list(a.axis)
        length = sqrt(_axis_len2(a.axis))
        for d in ctx.rels_out("hosts", a.id):
            if ctx.st.el[d]["kind"] != "door":
                continue   # 패밀리 인스턴스는 아래 check_hosted_fit 이 개구부 범위(cut)로 정확히 본다
            dp = ctx.st.el[d]["params"]
            if dp["offset"] - dp["width"] / 2 < 0 or dp["offset"] + dp["width"] / 2 > length:
                raise DobimError("CONSTRAINT", f"호스팅된 문이 벽 밖으로 나간다: {d}", path="/axis")
    # 호스팅된 패밀리 인스턴스(finst)는 호스트 두께·높이·길이에 제약을 가진다: 새 벽 치수로 미리 재평가해 깨지면 거절한다
    from dobim_core.commands_family import check_hosted_fit
    check_hosted_fit(ctx, a.id, r["params"], p)
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


class JoinArgs(A):
    a: Id
    b: Id


def _join_pair(ctx, x: JoinArgs):
    if x.a == x.b:
        raise DobimError("CONSTRAINT", "같은 벽끼리는 조인할 수 없다", path="/b")
    ra, rb = ctx.row(x.a, "wall"), ctx.row(x.b, "wall")
    return ra, rb, min(x.a, x.b), max(x.a, x.b)


@command("join.set", kind="mutation", risk="write", group="model.join", summary="두 벽을 조인한다(끝점 정리)",
         args=JoinArgs, since="0.1",
         examples=[{"a": F.W2, "b": F.W3}])
def join_set(ctx, a: JoinArgs):
    _, _, lo, hi = _join_pair(ctx, a)
    if ctx.rels_out("on_level", lo) != ctx.rels_out("on_level", hi):
        raise DobimError("CONSTRAINT", "다른 레벨의 벽은 조인할 수 없다", path="/b")
    ctx.rel("joins", lo, hi, True)
    return {"joined": [lo, hi]}


@command("join.clear", kind="mutation", risk="write", group="model.join", summary="벽 조인을 푼다",
         args=JoinArgs, since="0.1",
         examples=[{"a": F.W1, "b": F.W2}])
def join_clear(ctx, a: JoinArgs):
    _, _, lo, hi = _join_pair(ctx, a)
    ctx.rel("joins", lo, hi, False)
    return {"cleared": [lo, hi]}


class DoorPlace(A):
    id: Id | None = None
    host: Id
    offset: Num = Field(description="호스트 축선 시작점에서 문 중심까지 mm")
    width: Pos = 900.0
    height: Pos = 2100.0
    sill: Num = Field(0.0, ge=0)
    name: Name | None = None


def _fits(ctx, host, offset, width):
    length = sqrt(_axis_len2(ctx.st.el[host]["params"]["axis"]))
    if offset - width / 2 < 0 or offset + width / 2 > length:
        raise DobimError("CONSTRAINT", f"문이 호스트 벽({length:.1f}mm) 밖으로 나간다", path="/offset")


@command("door.place", kind="mutation", risk="write", group="model.door", summary="벽에 문을 호스팅한다",
         args=DoorPlace, since="0.1",
         examples=[{"host": F.W2, "offset": 2000, "width": 900, "height": 2100}])
def door_place(ctx, a: DoorPlace):
    _fresh_id(ctx, a, "door")
    ctx.row(a.host, "wall")
    _fits(ctx, a.host, a.offset, a.width)
    a.name = a.name or f"D-{a.id[-6:]}"
    ctx.put_el(a.id, row("door", "IfcDoor", "door.single", {
        "name": a.name, "width": a.width, "height": a.height, "sill": a.sill, "offset": a.offset}))
    ctx.rel("hosts", a.host, a.id, True)
    return {"id": a.id}


class DoorSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    width: Pos | None = None
    height: Pos | None = None
    sill: Num | None = Field(None, ge=0)
    offset: Num | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.width, self.height, self.sill, self.offset)):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("door.set", kind="mutation", risk="write", group="model.door", summary="문의 이름·치수·위치를 바꾼다",
         args=DoorSet, since="0.1", examples=[{"id": F.D1, "width": 1000}])
def door_set(ctx, a: DoorSet):
    r = ctx.row(a.id, "door")
    p = dict(r["params"])
    for k in ("name", "width", "height", "sill", "offset"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    host = ctx.rels_in("hosts", a.id)[0]
    _fits(ctx, host, p["offset"], p["width"])
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


class IdArg(A):
    id: Id


def _delete_one(ctx, eid):
    for pset, props in sorted(ctx.psets(eid).items()):   # IFC 안의 삽입 순서에 기대지 않는다(로그 결정성)
        for prop in sorted(props):
            ctx.set_prop(eid, pset, prop, None)
    ctx.set_material(eid, None)
    for kind in ("joins", "on_level", "hosts", "of_type"):
        for dst in ctx.rels_out(kind, eid):
            ctx.rel(kind, eid, dst, False)
        for src in ctx.rels_in(kind, eid):
            ctx.rel(kind, src, eid, False)
    ctx.put_el(eid, None)


@command("element.delete", kind="mutation", risk="write", group="model", summary="요소를 지운다(벽이면 호스팅된 문도)",
         args=IdArg, since="0.1", examples=[{"id": F.D2}])
def element_delete(ctx, a: IdArg):
    r = ctx.row(a.id)
    gone = [a.id]
    if r["kind"] == "level":
        if ctx.rels_in("on_level", a.id):
            raise DobimError("CONSTRAINT", "벽이 있는 레벨은 지울 수 없다", path="/id")
    elif r["kind"] == "ftype":
        if ctx.rels_in("of_type", a.id):
            raise DobimError("CONSTRAINT", "인스턴스가 있는 패밀리 타입은 지울 수 없다", path="/id", hint="인스턴스를 먼저 지우세요")
    elif r["kind"] == "wall":
        for d in ctx.rels_out("hosts", a.id):
            _delete_one(ctx, d)
            gone.append(d)
    _delete_one(ctx, a.id)
    return {"deleted": gone}


class PsetSet(A):
    id: Id
    pset: PsetName
    prop: PropName
    value: PropValue = Field(description="저장할 값: bool, int64 정수, 유한한 float, 문자열. null 은 거부한다 "
                                         "(속성을 지우려면 pset.delete)")


@command("pset.set", kind="mutation", risk="write", group="model.pset", summary="속성셋 값을 쓴다(IFC 정본). 지우기는 pset.delete",
         args=PsetSet, since="0.1",
         examples=[{"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI60"}])
def pset_set(ctx, a: PsetSet):
    ctx.row(a.id)
    if a.pset == RESERVED_PSET:
        raise DobimError("CONSTRAINT", f"{RESERVED_PSET} 는 투영이 소유한 예약 속성셋이다", path="/pset")
    return {"changed": ctx.set_prop(a.id, a.pset, a.prop, a.value)}


class PsetDelete(A):
    id: Id
    pset: PsetName
    prop: PropName


@command("pset.delete", kind="mutation", risk="write", group="model.pset",
         summary="속성 하나를 지운다(IFC 정본). 없는 속성이면 변경 없음. 마지막 속성이면 빈 속성셋도 사라진다",
         args=PsetDelete, since="0.1", examples=[{"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating"}])
def pset_delete(ctx, a: PsetDelete):
    ctx.row(a.id)
    if a.pset == RESERVED_PSET:
        raise DobimError("CONSTRAINT", f"{RESERVED_PSET} 는 투영이 소유한 예약 속성셋이다", path="/pset")
    return {"changed": ctx.set_prop(a.id, a.pset, a.prop, None)}


class MatSet(A):
    id: Id
    name: Name | None = Field(description="null 이면 재료를 뗀다")


@command("material.set", kind="mutation", risk="write", group="model.pset", summary="요소에 재료를 지정한다(IFC 정본)",
         args=MatSet, since="0.1", examples=[{"id": F.W1, "name": "Concrete"}])
def material_set(ctx, a: MatSet):
    ctx.row(a.id)
    return {"changed": ctx.set_material(a.id, a.name)}


# ================================================================ 조회
def _enc_cursor(v) -> str:
    return limits.enc_cursor(v)


def _dec_cursor(c, shape: str):
    return limits.dec_cursor(c, shape)


_fit = limits.fit
_page = limits.page


RELATIONS_SHOWN = 20   # element.get 이 보여 주는 관계 수. 더 많으면 relation.list 로 이어 읽는다(레벨은 벽 수만큼 관계가 있다)


class ElementGet(A):
    id: Id
    limit: int = Field(100, ge=1, le=1000, description="속성(속성셋/속성 쌍)을 한 번에 돌려줄 최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor (속성 목록의 다음 쪽)")


@command("element.get", kind="query", risk="read", group="query",
         summary="요소 하나의 의도·속성·파생값. 속성이 많으면 page 로 나눠 돌려주고, 관계는 20건까지만(나머지는 relation.list)",
         args=ElementGet, idempotent=True, examples=[{"id": F.W1}])
def element_get(ctx, a: ElementGet):
    r = ctx.row(a.id)
    rel_all = [(k, x) for k in ("on_level", "joins", "hosts", "of_type") for x in ctx.rels_out(k, a.id) + [f"<-{x}" for x in ctx.rels_in(k, a.id)]]
    relations: dict = {}
    for k, x in rel_all[:RELATIONS_SHOWN]:
        relations.setdefault(k, []).append(x)
    params = r["params"]
    if r["kind"] == "ftype":   # 레시피 전문(수 KB)은 응답 상한을 넘기므로 요약만 보인다. 전문은 family.preview/validate 로 읽는다
        params = {**params, "recipe": {"id": r["family_id"], "bytes": len(json.dumps(params["recipe"], ensure_ascii=False))}}
    out = {"id": a.id, "kind": r["kind"], "ifc_class": r["ifc_class"], "origin": r["origin"],
           "family_id": r["family_id"], "params": params, "material": ctx.material(a.id),
           "relations": relations, "relations_total": len(rel_all)}
    if r["kind"] == "wall":
        start, end, status = ctx.g.q(Q.wall_trim, a.id)
        out["derived"] = {"trim_start": start, "trim_end": end, "length": end - start, "status": status}
    elif r["kind"] == "door":
        out["derived"] = {"world": list(ctx.g.q(Q.door_world, a.id)), "status": ctx.g.q(Q.door_status, a.id)}
    # 속성: (속성셋, 속성) 순서로 펼쳐 키셋 커서로 나눈다. 값이 길어 상한을 넘는 옛 값은 앞 255자만 보이고 clipped 에 적는다.
    psets = ctx.psets(a.id)
    pairs = [[ps, pr, psets[ps][pr]] for ps in sorted(psets) for pr in sorted(psets[ps])]
    after = _dec_cursor(a.cursor, "pair")
    start_i = 0 if after is None else next((i for i, t in enumerate(pairs) if [t[0], t[1]] > after), len(pairs))
    window, clipped = [], []
    for ps, pr, v in pairs[start_i:start_i + a.limit]:
        if isinstance(v, str):
            v, was = limits.clip_text(v)
            if was:
                clipped.append({"pset": ps, "prop": pr})
        window.append([ps, pr, v])
    items, cut = _fit(window, base={**out, "psets": {}, "clipped": clipped}, cursor_of=lambda i: _enc_cursor([window[i][0], window[i][1]]),
                      extra_bytes=420, extra_tokens=200.0)   # 잘렸을 때 붙는 경고 몫
    clipped = [c for c in clipped if any(t[0] == c["pset"] and t[1] == c["prop"] for t in items)]
    more = start_i + len(items) < len(pairs)
    nested: dict = {}
    for ps, pr, v in items:
        nested.setdefault(ps, {})[pr] = v
    out["psets"] = nested
    if clipped:
        out["clipped"] = clipped
        ctx.warn(f"길이 상한({limits.STR_MAX}자)을 넘는 옛 속성값 {len(clipped)}개는 앞 {limits.STR_MAX}자만 보인다: "
                 "전체 값은 읽을 수 없으니 pset.set 으로 짧게 다시 쓰거나 pset.delete 로 지우세요")
    if more:
        ctx.warn(f"속성 {len(pairs)}개 중 {start_i + len(items)}개까지 보였다: page.next_cursor 를 cursor 로 넘겨 이어 읽으세요")
    if len(rel_all) > RELATIONS_SHOWN:
        ctx.warn(f"관계 {len(rel_all)}건 중 {RELATIONS_SHOWN}건만 보인다: relation.list 로 이어 읽으세요")
    return {**out, "page": _page(_enc_cursor([items[-1][0], items[-1][1]]) if more else None, len(pairs),
                               cut or bool(clipped) or len(rel_all) > RELATIONS_SHOWN)}


class ElementQuery(A):
    kind: Literal["level", "wall", "door", "ftype", "finst"] | None = None
    level: Id | None = None
    name_contains: str | None = Field(None, max_length=64)
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("element.query", kind="query", risk="read", group="query", summary="요소 목록(종류·레벨·이름 필터, 페이지)",
         args=ElementQuery, idempotent=True, examples=[{"kind": "wall", "limit": 10}])
def element_query(ctx, a: ElementQuery):
    st = ctx.st
    after = _dec_cursor(a.cursor, "id")
    ids = sorted(st.by_kind.get(a.kind, ())) if a.kind else sorted(st.el)
    hits = []
    for eid in ids:
        r = st.el[eid]
        if a.level and not (eid == a.level or a.level in st.out.get(("on_level", eid), ())):
            continue
        if a.name_contains and a.name_contains.lower() not in r["params"]["name"].lower():
            continue
        hits.append(eid)
    start = 0 if after is None else next((i for i, e in enumerate(hits) if e > after), len(hits))
    window = [{"id": e, "kind": st.el[e]["kind"], "name": st.el[e]["params"]["name"]} for e in hits[start:start + a.limit]]
    items, cut = _fit(window, cursor_of=lambda i: _enc_cursor(window[i]["id"]))
    nxt = _enc_cursor(items[-1]["id"]) if start + len(items) < len(hits) else None
    return {"items": items, "page": _page(nxt, len(hits), cut)}


class RelationList(A):
    id: Id
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("relation.list", kind="query", risk="read", group="query", summary="요소의 관계(레벨·조인·호스팅), 페이지",
         args=RelationList, idempotent=True, examples=[{"id": F.W1}])
def relation_list(ctx, a: RelationList):
    ctx.row(a.id)
    # 전순서 키 (관계 종류 번호, 방향 0=나가는 1=들어오는, 상대 id). 키셋 커서는 마지막으로 읽은 항목의 키를 담는다:
    # 읽는 동안 앞쪽 관계가 생기거나 사라져도 빠짐·중복이 없고, 커서가 가리키던 관계가 사라져도 그 키 뒤에서 이어간다.
    keyed = []
    for ki, k in enumerate(("on_level", "joins", "hosts", "of_type")):
        keyed += [((ki, 0, d), {"kind": k, "src": a.id, "dst": d}) for d in ctx.rels_out(k, a.id)]
        keyed += [((ki, 1, s), {"kind": k, "src": s, "dst": a.id}) for s in ctx.rels_in(k, a.id)]
    keyed.sort(key=lambda t: t[0])
    after = _dec_cursor(a.cursor, "relation")
    rest = keyed if after is None else [t for t in keyed if t[0] > tuple(after)]
    items, cut = _fit([it for _, it in rest[:a.limit]], cursor_of=lambda i: _enc_cursor(list(rest[i][0])))
    more = len(items) < len(rest)
    nxt = _enc_cursor(list(rest[len(items) - 1][0])) if more else None
    return {"items": items, "page": _page(nxt, len(keyed), cut)}


@command("model.stats", kind="query", risk="read", group="query", summary="요소 개수와 일람표 요약",
         args=Empty, idempotent=True, examples=[{}])
def model_stats(ctx, a):
    g = ctx.g
    return {"counts": g.q(Q.model_stats), "door": g.q(Q.schedule_summary, "door"),
            "wall": g.q(Q.schedule_summary, "wall")}


@command("model.hash", kind="query", risk="read", group="query",
         summary="정규화된 의미 해시(상태·파생값·IFC 투영). 원시 파일 바이트가 아니다",
         args=Empty, idempotent=True, examples=[{}])
def model_hash(ctx, a):
    return ctx.eng.hashes()


class Validate(A):
    express_rules: bool = True


@command("model.validate", kind="query", risk="read", group="check", summary="IFC 투영의 유효성 검사(M8)",
         args=Validate, idempotent=True, examples=[{"express_rules": False}])
def model_validate(ctx, a: Validate):
    ctx.eng.flush_ifc()
    v = ctx.eng.ifc.validate(express=a.express_rules)
    # 패밀리 인스턴스를 평가하지 못하는 요소(레시피 제약을 깨는 값이 로그에 있는 프로젝트 등)는 오류로 보고한다
    bad = []
    for eid in sorted(ctx.st.by_kind.get("finst", ())):
        err = ctx.eng.g.q(Q.ifc_spec, eid).get("error")
        if err:
            bad.append(f"패밀리 인스턴스 {eid} 를 평가할 수 없다({err['code']} {err['path']}): {err['message']}")
    return {"errors": v["errors"] + len(bad), "warnings": v["warnings"], "violations": (bad + v["samples"])[:8]}


class ScheduleRows(A):
    kind: Literal["door", "wall"]
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("schedule.rows", kind="query", risk="read", group="schedule", summary="문·벽 일람표 행(페이지)",
         args=ScheduleRows, idempotent=True, examples=[{"kind": "door", "limit": 5}])
def schedule_rows(ctx, a: ScheduleRows):
    rows = ctx.g.q(Q.schedule_sorted, a.kind)
    # 키셋 커서: 마지막으로 읽은 행의 (레벨 이름, 이름, id). 행은 이 키로 전순서이므로 이진 탐색으로 그 뒤에서 이어간다.
    # 읽는 동안 행이 삭제·삽입돼도 빠짐·중복이 없다. 이름·레벨이 바뀐 행은 새 키 위치로 옮겨 가므로 읽은 위치 뒤로 가면 다시 나올 수 있다.
    after = _dec_cursor(a.cursor, "schedule")
    start = 0 if after is None else bisect.bisect_right(rows, tuple(after), key=lambda r: Q.schedule_key(a.kind, r))
    cols = list(Q.ROW_COLUMNS[a.kind])
    window, clipped = [], []
    for k, r in enumerate(rows[start:start + a.limit]):
        row_out = list(r)
        for ci, v in enumerate(row_out):
            if isinstance(v, str):
                row_out[ci], was = limits.clip_text(v)   # 옛 로그의 긴 속성값(방화등급 등)이 한 행을 터뜨리지 않게
                if was:
                    clipped.append([k, cols[ci]])
        window.append(row_out)
    keys = [list(Q.schedule_key(a.kind, r)) for r in rows[start:start + a.limit]]   # 커서는 잘리기 전 원래 키
    items, cut = _fit(window, base={"columns": cols, "clipped": clipped}, cursor_of=lambda i: _enc_cursor(keys[i]))
    clipped = [c for c in clipped if c[0] < len(items)]
    nxt = _enc_cursor(keys[len(items) - 1]) if start + len(items) < len(rows) else None
    out = {"columns": cols, "items": items, "page": _page(nxt, len(rows), cut or bool(clipped))}
    if clipped:
        out["clipped"] = [{"row": r, "column": c} for r, c in clipped]
        ctx.warn(f"길이 상한({limits.STR_MAX}자)을 넘는 옛 속성값 {len(clipped)}개는 앞 {limits.STR_MAX}자만 보인다(clipped 의 row 는 이 쪽 안의 번호)")
    return out


class ScheduleKind(A):
    kind: Literal["door", "wall"]


@command("schedule.summary", kind="query", risk="read", group="schedule", summary="일람표 합계",
         args=ScheduleKind, idempotent=True, examples=[{"kind": "wall"}])
def schedule_summary(ctx, a: ScheduleKind):
    return ctx.g.q(Q.schedule_summary, a.kind)


# ================================================================ 이력
class HistoryArgs(A):
    target: str | None = Field(None, max_length=32, description="되돌릴/다시할 트랜잭션. 생략하면 스택 맨 위 (로그에는 확정값이 남는다)")


def _history(ctx, a, undo: bool):
    txn = ctx.peek_undo() if undo else ctx.peek_redo()
    if a.target is not None and a.target != txn:
        raise DobimError("CONSTRAINT", f"스택 맨 위는 {txn} 이다", path="/target")
    a.target = txn
    entries = ctx.txn_entries(txn)
    foreign = sorted({e[1] for e in entries if e[1] != ctx.actor})
    if foreign:
        ctx.warn(f"다른 행위자({', '.join(foreign)})의 변경을 {'되돌린다' if undo else '다시 적용한다'}")
    if undo:
        effects = [e for _, _, _, _, inv in reversed(entries) for e in json.loads(inv)]
    else:
        effects = [e for _, _, _, _, inv in entries for e in _inv(json.loads(inv))]
    for e in effects:
        ctx.emit(e)
    # 같은 커맨드 이름을 건수 만큼 되풀이하지 않고 커맨드별 건수로 요약한다. 커맨드 종류는 레지스트리 크기로 묶이므로 응답이 커지지 않고
    # (그래서 잘림도 없다), ops_total 은 항목 수의 정확한 총합이다.
    counts: dict[str, int] = {}
    for e in entries:
        counts[e[2]] = counts.get(e[2], 0) + 1
    return {"txn": txn, "ops": [{"op": k, "count": n} for k, n in counts.items()], "ops_total": len(entries), "effects": len(effects)}


def _inv(effs):
    from dobim_core.engine import invert  # 지연 import: 레지스트리 로드가 엔진에 의존하지 않게
    return invert(effs)


@command("history.undo", kind="mutation", risk="write", group="history", summary="마지막 변경(트랜잭션)을 되돌린다",
         args=HistoryArgs, since="0.1", examples=[{}])
def history_undo(ctx, a: HistoryArgs):
    return _history(ctx, a, True)


@command("history.redo", kind="mutation", risk="write", group="history", summary="되돌린 변경을 다시 적용한다",
         args=HistoryArgs, since="0.1", examples=[{}])
def history_redo(ctx, a: HistoryArgs):
    return _history(ctx, a, False)


HISTORY_ARG_BYTES = 1500


class HistoryList(A):
    limit: int = Field(20, ge=1, le=500, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor (그보다 오래된 항목을 돌려준다)")


@command("history.list", kind="query", risk="read", group="history", summary="최근 로그 항목",
         args=HistoryList, idempotent=True, examples=[{"limit": 5}])
def history_list(ctx, a: HistoryList):
    before = _dec_cursor(a.cursor, "int")
    rows = ctx.eng.conn.execute(
        "SELECT seq, txn_id, actor, base_rev, op, args FROM op_log WHERE seq < ? ORDER BY seq DESC LIMIT ?",
        (before if before is not None else 2**62, a.limit + 1)).fetchall()   # 한 건 더 읽어 다음 쪽이 있는지 안다
    more_in_limit = len(rows) > a.limit
    window, clipped_rows = [], []
    for s_, t, ac, b, o, g in rows[:a.limit]:
        it, n = limits.clip_deep({"seq": s_, "txn": t, "actor": ac, "base_rev": b, "op": o, "args": json.loads(g)})
        if limits.jsize(it["args"]) > HISTORY_ARG_BYTES:   # 문자열을 자르고도 큰 인자(레시피 전문 등)는 크기만 보인다(전문은 요소 행에 있다)
            it = {**it, "args": {"omitted_bytes": len(g.encode("utf-8"))}}
            n += 1   # 자른 것으로 센다: 항목의 clipped 와 page.truncated 로 알린다
        if n:   # 상한이 생기기 전의 옛 로그에 긴 값이 있으면 앞 255자만 보이고 이 항목에 표시한다
            it = {**it, "clipped": n}
            clipped_rows.append(s_)
        window.append(it)
    depths = {"undo_depth": len(ctx.eng.undo_stack), "redo_depth": len(ctx.eng.redo_stack)}
    items, cut = _fit(window, base=depths, cursor_of=lambda i: _enc_cursor(window[i]["seq"]))
    more = more_in_limit or cut
    if any(it.get("clipped") for it in items):
        ctx.warn(f"길이 상한({limits.STR_MAX}자)을 넘는 옛 값은 앞 {limits.STR_MAX}자만 보인다(항목의 clipped = 자른 값의 수)")
    return {"items": items, **depths,
            "page": _page(_enc_cursor(items[-1]["seq"]) if more and items else None, ctx.eng.rev, cut or any(it.get("clipped") for it in items))}


class HistoryAffected(A):
    rev: int | None = Field(None, ge=1, description="변경 응답의 rev. 그 변경(트랜잭션)이 만들거나 바꾸거나 지운 요소를 돌려준다. 생략하면 현재 리비전")
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("history.affected", kind="query", risk="read", group="history",
         summary="변경 하나(rev)가 만들거나 바꾸거나 지운 요소 id 목록, 페이지(항목의 seq = 그 요소를 처음 건드린 명령의 로그 번호). 변경 응답이 잘렸을 때 나머지를 얻는다",
         args=HistoryAffected, idempotent=True, examples=[{}, {"rev": 3, "limit": 2}])
def history_affected(ctx, a: HistoryAffected):
    rev = a.rev if a.rev is not None else ctx.eng.rev
    row = ctx.eng.conn.execute("SELECT txn_id FROM op_log WHERE seq=?", (rev,)).fetchone()
    if row is None:
        raise DobimError("NOT_FOUND", f"로그에 없는 리비전이다: {rev}", path="/rev")
    from dobim_core.engine import affected_of, invert   # 지연 import: 레지스트리 로드가 엔진에 의존하지 않게
    per_entry = [(e[0], invert(json.loads(e[4]))) for e in ctx.eng.txn_entries(row[0])]   # (seq, 실행 때의 효과 순서 그대로)
    aff = affected_of([x for _, effs in per_entry for x in effs])
    first: dict[str, int] = {}   # 요소 id -> 그 요소를 처음 건드린 로그 항목(명령)의 seq. 변경 응답 results 의 seq 와 같은 번호다
    for seq, effs in per_entry:
        for x in effs:
            for eid in ((x[2], x[3]) if x[0] == "rel" else (x[1],)):
                first.setdefault(eid, seq)
    # 순서: (변경 종류, 처음 건드린 seq, id). 배치가 만든 요소가 명령 순서대로 나온다.
    keyed = sorted(((ci, first[eid], eid), {"id": eid, "change": c, "seq": first[eid]})
                   for ci, c in enumerate(("created", "modified", "deleted")) for eid in aff[c])
    after = _dec_cursor(a.cursor, "affected")
    rest = keyed if after is None else [t for t in keyed if t[0] > tuple(after)]
    counts = {c: len(aff[c]) for c in ("created", "modified", "deleted")}
    items, cut = _fit([it for _, it in rest[:a.limit]], base={"rev": rev, "txn": row[0], "counts": counts},
                      cursor_of=lambda i: _enc_cursor(list(rest[i][0])))
    more = len(items) < len(rest)
    nxt = _enc_cursor(list(rest[len(items) - 1][0])) if more else None
    return {"rev": rev, "txn": row[0], "counts": counts, "items": items, "page": _page(nxt, len(keyed), cut)}


# ================================================================ 입출력 (파괴적)
SNAP_DIR = ".dobim-snapshots"
_FORBIDDEN_SUFFIX = (".dobim", ".dobim-wal", ".dobim-shm", ".dobim-journal", ".tmp", "-wal", "-shm", "-journal")


class IfcExport(A):
    path: str = Field(max_length=260, description="작업 폴더 안의 .ifc 경로. 기존 파일은 덮어쓰며, 덮어쓰기 전에 자동 스냅숏을 남긴다")


def _target(ctx, raw: str):
    """내보내기 대상 검사: 작업 폴더 안, .ifc 확장자, 프로젝트 파일·WAL 사이드카·스냅숏 폴더가 아니어야 한다."""
    import os
    p = resolve_in_workspace(ctx.eng.workspace, raw)
    name = p.name.lower()
    if p.suffix.lower() != ".ifc" or any(name.endswith(x) for x in _FORBIDDEN_SUFFIX):
        raise DobimError("PATH_DENIED", f"내보내기 대상은 .ifc 파일이어야 한다: {p.name}", path="/path")
    proj = os.path.realpath(ctx.eng.path)
    if os.path.normcase(str(p)) in {os.path.normcase(proj + x) for x in ("", "-wal", "-shm", "-journal")}:
        raise DobimError("PATH_DENIED", "프로젝트 파일 자체는 덮어쓸 수 없다", path="/path")
    if SNAP_DIR.lower() in [x.lower() for x in p.parts]:   # 대소문자 구분 없이
        raise DobimError("PATH_DENIED", "스냅숏 폴더에는 쓸 수 없다", path="/path")
    if p.is_dir():
        raise DobimError("PATH_DENIED", f"폴더다: {p.name}", path="/path")
    return p


def _snap_dir(p, ws):
    """스냅숏 폴더. 접합점·심볼릭 링크로 작업 폴더 밖을 가리키면 거부한다(해석한 실제 경로로 검사)."""
    import os
    d = p.parent / SNAP_DIR
    real = Path(os.path.realpath(d))
    try:
        real.relative_to(Path(os.path.realpath(ws)))
    except ValueError:
        raise DobimError("PATH_DENIED", "스냅숏 폴더가 작업 폴더 밖을 가리킨다", path="/path") from None
    return d


def _snapshots(p, ws):
    d = _snap_dir(p, ws)
    out = []
    try:
        if d.is_dir():
            pre = p.name + "."
            for f in d.iterdir():
                tail = f.name[len(pre):]
                if f.name.startswith(pre) and tail.isascii() and tail.isdigit():   # ASCII 숫자만 (isdigit 는 '²' 도 참)
                    out.append((int(tail), f))
    except OSError as ex:   # 스냅숏 폴더의 읽기 거부(ACL) 등: INTERNAL 로 새지 않게 원인별 오류로 바꾼다
        raise _io_error(p, ex) from ex
    return sorted(out)


def _cleanup(path) -> None:
    """임시·반쯤 만든 파일 정리. 최선으로만 한다: 다른 프로세스가 잡고 있어 지우지 못해도 원래 오류를 가리지 않는다."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _snapshot_existing(p, ws):
    """덮어쓰기 전에 기존 파일을 .dobim-snapshots/<이름>.<번호> 로 복사한다. 번호는 결정적으로 증가한다.
    대상을 다른 프로그램이 공유 없이 열고 있으면 복사가 PermissionError(WinError 32)로 실패한다:
    원인별 오류로 바꾸고(_io_error) 반쯤 복사된 파일을 지운다."""
    import shutil
    d = _snap_dir(p, ws)
    if not p.exists():
        return None
    n = 0
    try:
        d.mkdir(exist_ok=True)
        n = max([k for k, _ in _snapshots(p, ws)], default=0) + 1
        dst = d / f"{p.name}.{n}"
        shutil.copy2(p, dst)
    except OSError as ex:
        if n:
            _cleanup(d / f"{p.name}.{n}")
        raise _io_error(p, ex) from ex
    return f"{SNAP_DIR}/{dst.name}"


def _drop_snapshot(p, snap, ws) -> None:
    if snap:
        _cleanup(_snap_dir(p, ws) / snap.rsplit("/", 1)[-1])


def _locked(p) -> DobimError:
    return DobimError("FILE_LOCKED", f"파일을 바꿀 수 없다: {p.name} (다른 프로그램이 열고 있을 수 있다)", path="/path",
                      hint="파일을 연 프로그램을 닫고 미리보기부터 다시 호출하세요")


# 윈도 오류 번호: 공유 위반(32)과 잠금 위반(33)이 잠금이다. 디스크 가득(112)과 핸들 디스크 가득(39)은 공간 부족이다.
_WIN_LOCKED = {32, 33}
_WIN_FULL = {39, 112}


def _probe(fn) -> str:
    """쓰기 시도 하나의 결과: "ok"(성공했거나 접근 거부와 무관한 실패), "denied"(접근 거부), "busy"(공유·잠금 위반)."""
    import errno
    try:
        fn()
    except OSError as ex:
        win = getattr(ex, "winerror", None)
        if win in _WIN_LOCKED or ex.errno in (errno.EBUSY, getattr(errno, "ETXTBSY", -1)):
            return "busy"
        if isinstance(ex, PermissionError) or win == 5 or ex.errno == errno.EACCES:
            return "denied"
    return "ok"


def _try_open_for_write(f: Path) -> None:
    """대상 파일을 쓰기용으로 열어 보기만 한다(내용·수정 시각을 바꾸지 않는다).
    윈도는 CRT 의 open 이 공유 위반(32)을 접근 거부(errno 13)와 구별하지 않으므로 Win32 CreateFile 을 직접 부른다.
    이름 바꾸기로 덮어쓰려면 쓰기와 삭제 권한이 둘 다 있어야 하므로 두 접근을 함께 요청하고, 공유는 모두 허용한다."""
    import sys
    if sys.platform == "win32":
        import _winapi
        _winapi.CloseHandle(_winapi.CreateFile(str(f), 0x40000000 | 0x00010000, 7, 0, 3, 0x80, 0))   # GENERIC_WRITE|DELETE, 공유 7, OPEN_EXISTING
        return
    with open(f, "r+b"):
        pass


def _try_open_for_read(f: Path) -> None:
    """파일을 읽기용으로 열어 보기만 한다. 윈도는 쓰기 판별과 같은 이유로 Win32 CreateFile 을 직접 부르고, 공유는 모두 허용한다."""
    import sys
    if sys.platform == "win32":
        import _winapi
        _winapi.CloseHandle(_winapi.CreateFile(str(f), 0x80000000, 7, 0, 3, 0x80, 0))   # GENERIC_READ, 공유 7, OPEN_EXISTING
        return
    with open(f, "rb"):
        pass


def _try_list(d: Path) -> None:
    """폴더의 목록을 열어 보기만 한다(읽기 거부 ACL 이면 접근 거부)."""
    import os
    with os.scandir(d):
        pass


def _try_create_in(d: Path) -> None:
    """폴더에 새 파일을 만들고 이름을 바꿔 본다. 덮어쓰기는 임시 파일을 만든 뒤 이름 바꾸기로 교체하므로 두 권한이 다 필요하다
    (폴더의 삭제(D) 거부는 만들기는 허용하지만 이름 바꾸기를 막는다)."""
    import os
    import uuid
    probe = d / f".dobim-probe-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    moved = probe.with_name(probe.name + ".r")
    fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        os.close(fd)
        os.replace(probe, moved)
    finally:
        _cleanup(probe)   # 판별용 파일은 어떤 경우에도 남기지 않는다
        _cleanup(moved)


def _access_state(p, ex: OSError, *, write_only: bool = False) -> str:
    """접근 거부(WinError 5)의 원인을 가른다. 대상 파일, 실패한 연산이 닿은 파일·폴더에 실제로 접근해 보아 판정한다.
    "busy" = 다른 프로세스가 잡고 있다, "denied" = 쓰기·삭제 권한(ACL 등)이 막는다, "denied_read" = 읽기 권한이 막는다,
    "ok" = 권한 문제로 볼 증거가 없다(잠금으로 본다). os.access 는 윈도에서 읽기 전용 속성만 보므로 ACL 거부를 알아내지 못한다.
    읽기 판별은 예외가 이름을 댄 경로(ex.filename·filename2)에만 한다: 복사의 원본, 폴더 목록 등. write_only 는 이름 바꾸기처럼
    쓰기만 하는 연산이라 읽기를 보지 않는다(.tmp 는 우리가 만든 파일이라 어떤 경우에도 읽기를 보지 않는다)."""
    import os
    named = []
    for f in (ex.filename, ex.filename2):
        if isinstance(f, (str, bytes, os.PathLike)):
            named.append(Path(os.fsdecode(f)))
    dirs = {p.parent, *(f.parent for f in named)}
    tmp = p.with_name(p.name + ".tmp")
    states = []
    if p.is_file():
        states.append(_probe(lambda: _try_open_for_write(p)))
    for f in named:
        if f != p and f.is_file():   # 복사 대상·.tmp 처럼 연산이 닿은 다른 파일도 쓸 수 있는지 본다(다른 프로세스가 잡은 .tmp 는 busy)
            states.append(_probe(lambda f=f: _try_open_for_write(f)))
    states += [_probe(lambda d=d: _try_create_in(d)) for d in sorted(dirs, key=str) if d.is_dir()]
    if "busy" in states:
        return "busy"
    if "denied" in states:
        return "denied"
    if not write_only:
        reads = []
        for f in named:
            if f == tmp:
                continue
            if f.is_file():
                reads.append(_probe(lambda f=f: _try_open_for_read(f)))
            elif f.is_dir():
                reads.append(_probe(lambda f=f: _try_list(f)))
        if "busy" in reads:
            return "busy"
        if "denied" in reads:
            return "denied_read"
    return "ok"


def _io_error(p, ex: OSError, *, write_only: bool = False, uncertain: bool = False) -> DobimError:
    """OSError 를 원인별 오류로 나눈다. 디스크 가득을 FILE_LOCKED(파일을 닫으라는 안내)로 보고하면 사용자가 엉뚱한 곳을 찾는다.
      - 잠금(공유 위반·잠금 위반, 또는 유닉스의 EBUSY·ETXTBSY) -> FILE_LOCKED
      - 접근 거부(WinError 5, errno 13): 윈도는 '열려 있는 파일 위로 이름 바꾸기'와 '다른 프로세스가 잡은 .tmp 열기'도 접근 거부로 알린다.
        그래서 원인을 가린다: 읽기 전용 속성이거나 대상·폴더에 실제로 쓸 수 없으면(ACL 거부) PATH_DENIED(재시도해도 소용없다),
        읽는 연산이면 읽기 접근도 본다(읽기 거부 ACL -> PATH_DENIED), 다른 프로세스가 잡고 있거나 권한 문제의 증거가 없으면 FILE_LOCKED
      - 디스크·할당량 부족 -> DISK_FULL, 없는 경로 -> NOT_FOUND, 나머지 -> IO_ERROR(오류 번호를 메시지에 남긴다)
    uncertain=True 는 라이브러리가 원인을 알리지 않은 실패(ifcopenshell 의 RuntimeError)용이다: 권한·잠금 증거가 없으면 잠금으로 단정하지 않고 IO_ERROR 로 둔다."""
    import errno
    import os
    win, en = getattr(ex, "winerror", None), ex.errno
    if win in _WIN_LOCKED or en in (errno.EBUSY, getattr(errno, "ETXTBSY", -1)):
        return _locked(p)
    if win in _WIN_FULL or en in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
        return DobimError("DISK_FULL", f"디스크 공간이 모자라 쓰지 못했다: {p.name}", path="/path",
                          hint="공간을 확보한 뒤 미리보기부터 다시 호출하세요")
    if isinstance(ex, PermissionError) or win == 5 or en == errno.EACCES:
        if p.exists() and not os.access(p, os.W_OK):
            return DobimError("PATH_DENIED", f"읽기 전용이라 바꿀 수 없다: {p.name}", path="/path")
        state = _access_state(p, ex, write_only=write_only)
        if state == "denied":
            return DobimError("PATH_DENIED", f"쓸 권한이 없어 바꿀 수 없다: {p.name} (파일 또는 폴더의 접근 권한이 쓰기를 막는다)",
                              path="/path", hint="권한을 바꾸기 전에는 다시 호출해도 같은 결과다: 파일·폴더의 쓰기 권한을 확인하세요")
        if state == "denied_read":
            return DobimError("PATH_DENIED", f"읽을 권한이 없어 처리할 수 없다: {p.name} (파일 또는 스냅숏 폴더의 접근 권한이 읽기를 막는다)",
                              path="/path", hint="권한을 바꾸기 전에는 다시 호출해도 같은 결과다: 파일·스냅숏 폴더의 읽기 권한을 확인하세요")
        if uncertain and state != "busy":
            return DobimError("IO_ERROR", f"파일을 교체하지 못했다: {p.name} (원인을 가리지 못했다)", path="/path")
        return _locked(p)
    if isinstance(ex, (FileNotFoundError, NotADirectoryError)):
        return DobimError("NOT_FOUND", f"경로를 찾을 수 없다: {p.name}", path="/path")
    return DobimError("IO_ERROR", f"파일 입출력에 실패했다: {p.name} (오류 {win if win is not None else en})", path="/path")


def _replace(p, write):
    """임시 파일에 쓴 뒤 교체한다. 실패 원인은 _io_error 로 나눈다. 어느 단계에서 실패해도 .tmp 를 남기지 않는다(다른 프로세스가
    .tmp 를 잡고 있어 지우지 못하는 경우만 남는다: 그때도 원래 오류를 낸다)."""
    tmp = p.with_name(p.name + ".tmp")
    step = "open"
    try:
        tmp.open("wb").close()   # 먼저 직접 열어 본다: 이전 실패의 .tmp 를 다른 프로세스가 잡고 있거나 디스크가 가득 차면 여기서 OSError 로 분류된다
        step = "write"
        write(tmp)               # (IFC 라이브러리는 열기·교체 실패를 OSError 가 아닌 RuntimeError 로 알린다: 아래에서 좁게 분류한다)
        step = "replace"
        tmp.replace(p)
    except OSError as ex:
        _cleanup(tmp)
        raise _io_error(p, ex, write_only=step != "write") from ex   # 쓰는 단계(복원의 원본 복사 등)만 읽기 접근도 본다
    except RuntimeError as ex:
        _cleanup(tmp)
        if step == "write" and str(ex).startswith("Failed to write to path"):
            # ifcopenshell 이 파일을 쓰거나 .tmp 로 교체하지 못했을 때의 예외(작업 폴더의 삭제 거부 등). 다른 RuntimeError 는 삼키지 않는다.
            # 접근 거부처럼 보고 원인을 가린다: 권한 거부 -> PATH_DENIED, 다른 프로세스가 잡음 -> FILE_LOCKED, 증거 없음 -> IO_ERROR
            raise _io_error(p, PermissionError(13, str(ex), str(tmp)), write_only=True, uncertain=True) from ex
        raise
    except BaseException:
        _cleanup(tmp)
        raise


def _export_preview(ctx, a: IfcExport):
    p = _target(ctx, a.path)
    _snap_dir(p, ctx.eng.workspace)   # 토큰을 내주기 전에 스냅숏 폴더가 안전한지 확인한다
    return {"will_write": p.name, "overwrites": p.exists(), "snapshot_before_overwrite": p.exists()}


@command("ifc.export", kind="session", risk="destructive", group="io",
         summary="IFC4 파일로 내보낸다. 기존 파일은 덮어쓰기 전에 자동 스냅숏을 남긴다",
         args=IfcExport, preview=_export_preview, examples=[{"path": "export-example.ifc"}])
def ifc_export(ctx, a: IfcExport):
    p = _target(ctx, a.path)
    ctx.eng.flush_ifc()   # 투영한 요소 수는 프로세스 상태(캐시 유무)에 따라 달라 결과에 넣지 않는다(패리티 C)
    snap = _snapshot_existing(p, ctx.eng.workspace)
    try:
        _replace(p, lambda tmp: ctx.eng.ifc.write(str(tmp)))
    except BaseException:   # DobimError 만이 아니다: ifc.write 가 라이브러리 예외로 실패해도 스냅숏이 쌓이지 않게
        _drop_snapshot(p, snap, ctx.eng.workspace)   # 쓰지 못했으면 방금 만든 스냅숏은 필요 없다(재시도마다 쌓이지 않게)
        raise
    return {"path": p.name, "bytes": p.stat().st_size, "rev": ctx.eng.rev, "snapshot": snap}


class SnapshotList(A):
    path: str = Field(max_length=260, description="작업 폴더 안의 .ifc 경로")
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("ifc.snapshots", kind="query", risk="read", group="io", summary="IFC 내보내기 덮어쓰기 전 스냅숏 목록, 페이지",
         args=SnapshotList, idempotent=True, needs_project=True, examples=[{"path": "export-example.ifc"}])
def ifc_snapshots(ctx, a: SnapshotList):
    p = resolve_in_workspace(ctx.eng.workspace, a.path)
    allrows = [{"n": n, "name": f"{SNAP_DIR}/{f.name}", "bytes": f.stat().st_size} for n, f in _snapshots(p, ctx.eng.workspace)]
    after = _dec_cursor(a.cursor, "int")
    start = 0 if after is None else next((i for i, r in enumerate(allrows) if r["n"] > after), len(allrows))
    items, cut = _fit(allrows[start:start + a.limit])
    more = start + len(items) < len(allrows)
    return {"items": items, "page": _page(_enc_cursor(items[-1]["n"]) if more else None, len(allrows), cut)}


class IfcRestore(A):
    path: str = Field(max_length=260, description="되돌릴 .ifc 파일 경로")
    snapshot: int = Field(description="ifc.snapshots 가 알려 준 번호", ge=1)


def _restore_preview(ctx, a: IfcRestore):
    p = _target(ctx, a.path)
    found = dict(_snapshots(p, ctx.eng.workspace)).get(a.snapshot)   # 폴더 탈출도 여기서 거부된다
    if found is None:
        raise DobimError("NOT_FOUND", f"스냅숏이 없다: {a.snapshot}", path="/snapshot")
    return {"will_write": p.name, "from_snapshot": a.snapshot, "overwrites": p.exists(), "snapshot_before_overwrite": p.exists()}


@command("ifc.restore", kind="session", risk="destructive", group="io",
         summary="스냅숏으로 .ifc 파일을 되돌린다(현재 파일도 먼저 스냅숏으로 남긴다)",
         args=IfcRestore, preview=_restore_preview, examples=[{"path": "export-example.ifc", "snapshot": 1}])
def ifc_restore(ctx, a: IfcRestore):
    import shutil
    p = _target(ctx, a.path)
    src = dict(_snapshots(p, ctx.eng.workspace)).get(a.snapshot)
    if src is None:
        raise DobimError("NOT_FOUND", f"스냅숏이 없다: {a.snapshot}", path="/snapshot")
    snap = _snapshot_existing(p, ctx.eng.workspace)
    try:
        _replace(p, lambda tmp: shutil.copyfile(src, tmp))
    except BaseException:
        _drop_snapshot(p, snap, ctx.eng.workspace)
        raise
    return {"path": p.name, "restored_from": a.snapshot, "bytes": p.stat().st_size, "snapshot": snap}


# S4 패밀리 커맨드: 이 모듈의 정의가 모두 끝난 뒤에 등록한다
from dobim_core import commands_family  # noqa: E402,F401
