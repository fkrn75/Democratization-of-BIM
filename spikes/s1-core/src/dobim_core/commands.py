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

from dobim_core import annot as AN
from dobim_core import drawing as D
from dobim_core import drawing_export as DX
from dobim_core import fixture as F
from dobim_core import limits, merge
from dobim_core import queries as Q
from dobim_core import registry
from dobim_core.errors import DobimError
from dobim_core.kinds import KIND_NAMES, READ_REL_NAMES, REL_KINDS, param_ref_targets
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


# 요소를 지울 때 끊는 관계(분할 이력 succ 제외). 로그의 효과 순서가 바뀌지 않게 순서는 고정하고, 레지스트리와 같은 집합인지 import 때 확인한다
DELETE_REL_NAMES = ("joins", "on_level", "hosts", "annot_of", "of_type")
assert set(DELETE_REL_NAMES) == {k for k, r in REL_KINDS.items() if not r.dangling_ok}, "kinds.REL_KINDS 에 관계를 더했으면 DELETE_REL_NAMES 도 고친다"


def _hosted_span(ctx, d) -> tuple:
    """벽에 호스팅된 요소가 벽 길이 방향으로 차지하는 구간 (lo, hi). 문은 offset ± width/2, 패밀리 인스턴스는 평가한 개구부(cut) 범위."""
    dr = ctx.st.el[d]
    dp = dr["params"]
    if dr["kind"] == "finst":
        cut = ctx.fresh().q(Q.ifc_spec, d).get("cut")
        if cut:
            return dp["offset"] + cut["x"][0], dp["offset"] + cut["x"][1]
    return dp["offset"] - dp["width"] / 2, dp["offset"] + dp["width"] / 2


def _in_split_history(ctx, eid) -> bool:
    """분할 이력(관계 succ)에 한 번이라도 쓰인 id 인가(지워진 벽 포함: 이력은 지워져도 남는다)."""
    return bool(ctx.rels_out("succ", eid) or ctx.rels_in("succ", eid))


def _fresh_id(ctx, a, kind):
    a.id = a.id or ctx.new_id(kind)
    if a.id in ctx.st.el:
        raise DobimError("CONSTRAINT", f"이미 있는 id 다: {a.id}", path="/id")
    if _in_split_history(ctx, a.id):   # 지워진 벽의 id 를 다시 쓰면 분할 이력을 따라가는 참조가 엉뚱한 새 벽을 가리킨다
        raise DobimError("CONSTRAINT", f"벽 분할 이력에 쓰인 id 라 다시 쓸 수 없다: {a.id}", path="/id",
                         hint="새 id 를 쓰거나 생략하세요")
    return a.id


# ================================================================ 프로젝트·시스템
class ProjectNew(A):
    path: str = Field(max_length=260, description="작업 폴더 안의 .dobim 경로")
    guid: str | None = Field(None, max_length=36, description="프로젝트 UUID. 생략하면 무작위, 지정하면 재현 가능")


@command("project.new", kind="session", risk="write", group="project", summary="프로젝트 생성",
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


@command("project.info", kind="query", risk="read", group="project", summary="프로젝트 정보",
         args=Empty, examples=[{}], idempotent=True)
def project_info(ctx, a):
    g = ctx.g
    out = {"project_uuid": ctx.eng.project_guid_uuid, "rev": ctx.eng.rev, "schema_version": "1",
           "counts": g.q(Q.model_stats), "undo_depth": len(ctx.eng.undo_stack),
           "redo_depth": len(ctx.eng.redo_stack)}
    if ctx.eng.has_base:   # 외래 IFC 를 가져온 프로젝트: 요소의 출처별 개수
        out["origins"] = {k: ctx.eng.st.origins.get(k, 0) for k in ("authored", "foreign", "detached")}
        if ctx.eng.rejudged:   # 해시 형식이 바뀌어 다시 판정하며 동결된 요소가 있다(열 때 한 번)
            out["rejudged_frozen"] = len(ctx.eng.rejudged)
            ctx.warn(f"해시 형식이 바뀌어 가져온 프로젝트를 다시 판정했고 저작 요소 {len(ctx.eng.rejudged)}개를 분리(동결)했다")
    return out


class Search(A):
    query: str = Field("", max_length=256)
    group: str | None = Field(None, max_length=64)
    risk: Literal["read", "write", "destructive", "code_exec"] | None = None
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("commands.search", kind="query", risk="read", group="system", summary="커맨드를 찾는다",
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


@command("commands.describe", kind="query", risk="read", group="system", summary="커맨드 스키마·예시",
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


@command("level.create", kind="mutation", risk="write", group="model.level", summary="레벨 생성",
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


@command("wall.create", kind="mutation", risk="write", group="model.wall", summary="벽 생성",
         args=WallCreate, since="0.1",
         examples=[{"level": F.LV1, "axis": [0, 4000, 6000, 4000], "thickness": 200}])
def wall_create(ctx, a: WallCreate):
    _fresh_id(ctx, a, "wall")
    ctx.authored(a.level, "level", what="레벨에 벽을 추가하는 것")
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
    r = ctx.authored(a.id, "wall")
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


@command("wall.set", kind="mutation", risk="write", group="model.wall", summary="벽 속성 변경",
         args=WallSet, since="0.1", examples=[{"id": F.W1, "thickness": 250}])
def wall_set(ctx, a: WallSet):
    r = ctx.authored(a.id, "wall")
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
    ra, rb = ctx.authored(x.a, "wall", what="조인"), ctx.authored(x.b, "wall", what="조인")
    return ra, rb, min(x.a, x.b), max(x.a, x.b)


@command("join.set", kind="mutation", risk="write", group="model.join", summary="벽 조인",
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


# ================================================================ 벽 분할 (S5: 안정 참조의 규칙을 시험하려고 코어에 더한 모델링 커맨드)
def _frame_of(axis):
    dx, dy = axis[2] - axis[0], axis[3] - axis[1]
    length = sqrt(dx * dx + dy * dy)
    return axis[0], axis[1], dx / length, dy / length, length


def _join_pt(wa, axa, wb, axb):
    """queries.join_point 와 같은 계산(작은 id 가 앞). 변경 커맨드가 낡은 그래프 없이 원시 축선에서 직접 계산한다."""
    if wa > wb:
        wa, axa, wb, axb = wb, axb, wa, axa
    x1, y1, c1, s1, _ = _frame_of(axa)
    x2, y2, c2, s2, _ = _frame_of(axb)
    det = c1 * s2 - s1 * c2
    if -Q.PARALLEL_EPS < det < Q.PARALLEL_EPS:
        return None
    t = ((x2 - x1) * s2 - (y2 - y1) * c2) / det
    return (x1 + t * c1, y1 + t * s1)


def _trim_raw(wid, axis, partners):
    """queries.wall_trim 과 같은 규칙으로 (시작, 끝)을 원시 축선에서 계산한다. partners = [(상대 id, 상대 축선)]."""
    x, y, c, s, length = _frame_of(axis)
    start, end = 0.0, length
    for oid, oax in sorted(partners):
        p = _join_pt(wid, axis, oid, oax)
        if p is None:
            continue
        t = (p[0] - x) * c + (p[1] - y) * s
        if abs(t) <= abs(t - length):
            if abs(t) <= Q.JOIN_MAX_EXT:
                start = t
        elif abs(t - length) <= Q.JOIN_MAX_EXT:
            end = t
    if end - start < Q.MIN_TRIMMED:
        return (0.0, length)
    return (start, end)


def _assert_succ_acyclic(ctx, start) -> None:
    """불변식: 분할 이력(succ)은 순환하지 않는다. start 에서 이력을 따라가 같은 벽을 두 번 만나면 거절한다
    (new_id 검사가 막으므로 정상 흐름에서는 일어나지 않는다. 손상된 로그를 재생할 때의 마지막 안전망)."""
    seen, x = {start}, start
    while True:
        nxt = ctx.rels_out("succ", x)
        if not nxt:
            return
        x = nxt[0]
        if x in seen:
            raise DobimError("CONSTRAINT", f"벽 분할 이력이 순환한다: {x}", path="/new_id")
        seen.add(x)


class WallSplit(A):
    id: Id
    at: Pos = Field(description="축선 시작점에서 분할 위치 mm")
    new_id: Id | None = Field(None, description="끝 쪽 새 벽의 id (생략하면 결정적으로 만든다)")


@command("wall.split", kind="mutation", risk="write", group="model.wall",
         summary="벽 분할(참조 유지)",
         args=WallSplit, since="0.1", examples=[{"id": F.W1, "at": 3000}])
def wall_split(ctx, a: WallSplit):
    """벽 W 를 분할점에서 둘로 나눈다. W 는 시작 쪽을 지키고, 끝 쪽은 새 벽 N 이 된다.
    - 형상이 그대로여야 한다: 조인 보정(trim)이 분할로 달라지면 거절한다(문을 가로지르는 분할도 거절).
    - 문은 놓인 쪽 벽으로 옮겨진다(id 유지, 오프셋만 이동). 조인은 조인 지점이 있는 쪽 벽이 가진다. 속성·재료는 복사한다.
    - 분할 이력은 관계 succ(W -> N)로 남는다. 주석 참조의 끝 역할(axis.end, trim.end)이 이 관계를 따라가 값이 변하지 않는다.
    - 지워진 벽도 이력에는 남으므로, 이력에 한 번이라도 쓰인 id 는 new_id 로 다시 쓸 수 없다(succ 순환·엉뚱한 부활 방지)."""
    r = ctx.authored(a.id, "wall", what="분할")   # S5 의 분할이 S2 의 저작 확인을 거치지 않던 틈
    p = r["params"]
    x1, y1, x2, y2 = p["axis"]
    _, _, c, s, length = _frame_of(p["axis"])
    if a.at < 1.0 or length - a.at < 1.0:
        raise DobimError("CONSTRAINT", f"분할 위치는 양쪽 조각이 1mm 이상 남아야 한다(벽 길이 {length:.1f}mm)", path="/at")
    nid = a.new_id = a.new_id or ctx.new_id("wall")    # 로그에 새 벽 id 를 명시해 재생이 같은 id 를 만들게 한다
    if nid in ctx.st.el:
        raise DobimError("CONSTRAINT", f"이미 있는 id 다: {nid}", path="/new_id")
    if _in_split_history(ctx, nid):   # 지워진 후계 id 로 다시 나누면 succ(X -> X) 순환이 생긴다
        raise DobimError("CONSTRAINT", f"벽 분할 이력에 쓰인 id 라 다시 쓸 수 없다: {nid}", path="/new_id",
                         hint="new_id 를 생략하거나 새 id 를 쓰세요")
    sx, sy = x1 + a.at * c, y1 + a.at * s
    ax_w, ax_n = [x1, y1, sx, sy], [sx, sy, x2, y2]
    doors_n = []
    for d in sorted(ctx.rels_out("hosts", a.id)):
        lo, hi = _hosted_span(ctx, d)
        if hi <= a.at:
            continue
        ctx.authored(d, what="분할로 옮겨지는 호스팅 요소")
        if lo >= a.at:
            doors_n.append(d)
        else:
            raise DobimError("CONSTRAINT", f"분할 위치가 문을 가로지른다: {d}", path="/at")
    partners = sorted(ctx.rels_out("joins", a.id) + ctx.rels_in("joins", a.id))
    pax = [(o, ctx.st.el[o]["params"]["axis"]) for o in partners]
    to_n, keep_w = [], []
    for o, oax in pax:
        pt = _join_pt(a.id, p["axis"], o, oax)
        t = None if pt is None else (pt[0] - x1) * c + (pt[1] - y1) * s
        (to_n if t is not None and t >= a.at else keep_w).append((o, oax))
    b0, b1 = _trim_raw(a.id, p["axis"], pax)
    w0, w1 = _trim_raw(a.id, ax_w, keep_w)
    n0, n1 = _trim_raw(nid, ax_n, to_n)
    tol = 1e-6
    if abs(w0 - b0) > tol or abs(w1 - a.at) > tol or abs(n0) > tol or abs(n1 - (b1 - a.at)) > tol:
        raise DobimError("CONSTRAINT", "이 위치에서 나누면 조인 정리(trim)가 달라진다", path="/at",
                         hint="조인 지점에서 1000mm 넘게 떨어진 곳에서 나누세요")
    name = (p["name"][:62] + "-b")
    ctx.put_el(a.id, dict(r, params=dict(p, axis=ax_w)))
    ctx.put_el(nid, row("wall", "IfcWall", "wall.basic", {"name": name, "axis": ax_n, "thickness": p["thickness"], "height": p["height"]}))
    ctx.rel("on_level", nid, ctx.rels_out("on_level", a.id)[0], True)
    for d in doors_n:
        dr = ctx.st.el[d]
        ctx.rel("hosts", a.id, d, False)
        ctx.rel("hosts", nid, d, True)
        ctx.put_el(d, dict(dr, params=dict(dr["params"], offset=dr["params"]["offset"] - a.at)))
    for o, _ in to_n:
        ctx.rel("joins", min(a.id, o), max(a.id, o), False)
        ctx.rel("joins", min(nid, o), max(nid, o), True)
    for nx in ctx.rels_out("succ", a.id):          # 이미 끝을 넘겨준 이력이 있으면 N 이 이어받는다(W -> N -> 기존 후계)
        ctx.rel("succ", a.id, nx, False)
        ctx.rel("succ", nid, nx, True)
    ctx.rel("succ", a.id, nid, True)
    _assert_succ_acyclic(ctx, a.id)
    for pset, props in sorted(ctx.psets(a.id).items()):
        for prop in sorted(props):
            ctx.set_prop(nid, pset, prop, props[prop])
    ctx.set_material(nid, ctx.material(a.id))
    return {"id": a.id, "new": nid, "split_at": [sx, sy], "doors_moved": doors_n}


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


@command("door.place", kind="mutation", risk="write", group="model.door", summary="문 배치",
         args=DoorPlace, since="0.1",
         examples=[{"host": F.W2, "offset": 2000, "width": 900, "height": 2100}])
def door_place(ctx, a: DoorPlace):
    _fresh_id(ctx, a, "door")
    ctx.authored(a.host, "wall", what="문 호스팅")
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


@command("door.set", kind="mutation", risk="write", group="model.door", summary="문 속성 변경",
         args=DoorSet, since="0.1", examples=[{"id": F.D1, "width": 1000}])
def door_set(ctx, a: DoorSet):
    r = ctx.authored(a.id, "door")
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


def _cascade_view_annots(ctx, vid) -> list:
    """뷰를 지울 때 그 뷰에 속한 주석(치수·태그)도 같은 트랜잭션에서 지운다. 응답의 deleted 에 나열되므로 조용하지 않고, undo 로 함께 돌아온다."""
    gone = []
    for an in sorted(ctx.rels_in("annot_of", vid)):
        _delete_one(ctx, an)
        gone.append(an)
    return gone


def _delete_one(ctx, eid):
    ctx.attach(eid)   # 외래 부속(타입·수량 세트·분류·스타일 등)을 지우기 전에 효과로 남긴다: 실행 취소가 되살린다
    for pset, props in sorted(ctx.psets(eid).items()):   # IFC 안의 삽입 순서에 기대지 않는다(로그 결정성)
        for prop in sorted(props):
            ctx.set_prop(eid, pset, prop, None)
    if not ctx.complex_material(eid):   # 층 구성 같은 복합 재료는 이름 하나로 되살릴 수 없어 부속 스냅숏이 맡는다
        ctx.set_material(eid, None)
    for kind in DELETE_REL_NAMES:   # 분할 이력(succ)은 지우지 않는다: 지워진 벽의 id 를 가리킨 채 남아 주석 참조가 이어진다
        for dst in ctx.rels_out(kind, eid):
            ctx.rel(kind, eid, dst, False)
        for src in ctx.rels_in(kind, eid):
            ctx.rel(kind, src, eid, False)
    ctx.put_el(eid, None)


@command("element.delete", kind="mutation", risk="write", group="model", summary="요소 삭제(벽이면 문 포함)",
         args=IdArg, since="0.1", examples=[{"id": F.D2}])
def element_delete(ctx, a: IdArg):
    r = ctx.authored(a.id, what="삭제")
    gone = [a.id]
    if r["kind"] == "level":
        if ctx.rels_in("on_level", a.id):
            raise DobimError("CONSTRAINT", "벽이 있는 레벨은 지울 수 없다", path="/id")
    elif r["kind"] == "ftype":
        if ctx.rels_in("of_type", a.id):
            raise DobimError("CONSTRAINT", "인스턴스가 있는 패밀리 타입은 지울 수 없다", path="/id", hint="인스턴스를 먼저 지우세요")
    elif r["kind"] == "wall":
        fv = ctx.foreign_voids(a.id)
        if fv:   # 다른 도구가 이 벽에 뚫은 개구부는 벽과 함께 사라진다: 개구부는 형상을 가진 제품이라 실행 취소로 되살리지 않는다
            ctx.warn(f"이 벽을 뚫는 외래 개구부 {len(fv)}개가 벽과 함께 지워진다(실행 취소로 되살아나지 않는다): {', '.join(fv[:3])}"
                     + (" 외" if len(fv) > 3 else ""))
        for d in ctx.rels_out("hosts", a.id):
            ctx.authored(d, what="호스팅된 문의 삭제")
            _delete_one(ctx, d)
            gone.append(d)
    elif r["kind"] == "view":
        gone += _cascade_view_annots(ctx, a.id)
    _delete_one(ctx, a.id)
    return {"deleted": gone}


class PsetSet(A):
    id: Id
    pset: PsetName
    prop: PropName
    value: PropValue = Field(description="저장할 값: bool, int64 정수, 유한한 float, 문자열. null 은 거부한다 "
                                         "(속성을 지우려면 pset.delete)")


@command("pset.set", kind="mutation", risk="write", group="model.pset", summary="속성 쓰기",
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
         summary="속성 삭제",
         args=PsetDelete, since="0.1", examples=[{"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating"}])
def pset_delete(ctx, a: PsetDelete):
    ctx.row(a.id)
    if a.pset == RESERVED_PSET:
        raise DobimError("CONSTRAINT", f"{RESERVED_PSET} 는 투영이 소유한 예약 속성셋이다", path="/pset")
    return {"changed": ctx.set_prop(a.id, a.pset, a.prop, None)}


class MatSet(A):
    id: Id
    name: Name | None = Field(description="null 이면 재료를 뗀다")


@command("material.set", kind="mutation", risk="write", group="model.pset", summary="재료 지정",
         args=MatSet, since="0.1", examples=[{"id": F.W1, "name": "Concrete"}])
def material_set(ctx, a: MatSet):
    ctx.authored(a.id, what="재료 지정")
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
         summary="요소 상세·파생값",
         args=ElementGet, idempotent=True, examples=[{"id": F.W1}])
def element_get(ctx, a: ElementGet):
    r = ctx.row(a.id)
    rel_all = [(k, x) for k in READ_REL_NAMES for x in ctx.rels_out(k, a.id) + [f"<-{x}" for x in ctx.rels_in(k, a.id)]]
    relations: dict = {}
    for k, x in rel_all[:RELATIONS_SHOWN]:
        relations.setdefault(k, []).append(x)
    params = r["params"]
    if r["kind"] == "ftype":   # 레시피 전문(수 KB)은 응답 상한을 넘기므로 요약만 보인다. 전문은 family.preview/validate 로 읽는다
        params = {**params, "recipe": {"id": r["family_id"], "bytes": len(json.dumps(params["recipe"], ensure_ascii=False))}}
    out = {"id": a.id, "kind": r["kind"], "ifc_class": r["ifc_class"], "origin": r["origin"],
           "family_id": r["family_id"], "params": params, "material": ctx.material(a.id),
           "relations": relations, "relations_total": len(rel_all)}
    if r["origin"] != "authored":
        ctx.warn("외래 요소(IFC 가 정본)다: 형상은 동결돼 있고 속성만 편집할 수 있다" if r["origin"] == "foreign" else
                 "분리됨: 외부 도구가 형상을 바꿔 동결됐다. params·derived 는 마지막 의도(낡은 값)이고 실제 형상은 IFC 에 있다. 속성만 편집할 수 있다")
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
    kind: Literal[KIND_NAMES] | None = None   # 종류 목록의 정본은 kinds.ELEMENT_KINDS
    origin: Literal["authored", "foreign", "detached"] | None = Field(None, description="출처: authored=의도가 정본, foreign=IFC 가 정본, detached=외부 도구가 형상을 바꿔 동결됨")
    level: Id | None = None
    name_contains: str | None = Field(None, max_length=64)
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("element.query", kind="query", risk="read", group="query", summary="요소 목록",
         args=ElementQuery, idempotent=True, examples=[{"kind": "wall", "limit": 10}])
def element_query(ctx, a: ElementQuery):
    st = ctx.st
    after = _dec_cursor(a.cursor, "id")
    ids = sorted(st.by_kind.get(a.kind, ())) if a.kind else sorted(st.el)
    hits = []
    for eid in ids:
        r = st.el[eid]
        if a.origin and r["origin"] != a.origin:
            continue
        if a.level and not (eid == a.level or a.level in st.out.get(("on_level", eid), ())):
            continue
        if a.name_contains and a.name_contains.lower() not in r["params"]["name"].lower():
            continue
        hits.append(eid)
    start = 0 if after is None else next((i for i, e in enumerate(hits) if e > after), len(hits))
    window = [{"id": e, "kind": st.el[e]["kind"], "name": st.el[e]["params"]["name"],
               **({"origin": st.el[e]["origin"]} if st.el[e]["origin"] != "authored" else {})} for e in hits[start:start + a.limit]]
    items, cut = _fit(window, cursor_of=lambda i: _enc_cursor(window[i]["id"]))
    nxt = _enc_cursor(items[-1]["id"]) if start + len(items) < len(hits) else None
    return {"items": items, "page": _page(nxt, len(hits), cut)}


class RelationList(A):
    id: Id
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("relation.list", kind="query", risk="read", group="query", summary="요소의 관계 목록",
         args=RelationList, idempotent=True, examples=[{"id": F.W1}])
def relation_list(ctx, a: RelationList):
    ctx.row(a.id)
    # 전순서 키 (관계 종류 번호, 방향 0=나가는 1=들어오는, 상대 id). 키셋 커서는 마지막으로 읽은 항목의 키를 담는다:
    # 읽는 동안 앞쪽 관계가 생기거나 사라져도 빠짐·중복이 없고, 커서가 가리키던 관계가 사라져도 그 키 뒤에서 이어간다.
    keyed = []
    for ki, k in enumerate(READ_REL_NAMES):
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
         summary="의미 해시",
         args=Empty, idempotent=True, examples=[{}])
def model_hash(ctx, a):
    return ctx.eng.hashes()


class Validate(A):
    express_rules: bool = True


@command("model.validate", kind="query", risk="read", group="check", summary="IFC 투영 유효성 검사",
         args=Validate, idempotent=True, examples=[{"express_rules": False}])
def model_validate(ctx, a: Validate):
    ctx.eng.flush_ifc()
    v = ctx.eng.ifc.validate(express=a.express_rules)
    dangling = _dangling_view_levels(ctx)
    samples = [f"평면 뷰 '{name}' 의 레벨이 없다: {lv}" for name, lv in dangling]
    # 패밀리 인스턴스를 평가하지 못하는 요소(레시피 제약을 깨는 값이 로그에 있는 프로젝트 등)는 오류로 보고한다
    bad = []
    for eid in sorted(ctx.st.by_kind.get("finst", ())):
        err = ctx.eng.g.q(Q.ifc_spec, eid).get("error")
        if err:
            bad.append(f"패밀리 인스턴스 {eid} 를 평가할 수 없다({err['code']} {err['path']}): {err['message']}")
    # 파라미터 안의 id 참조와 관계가 같은 뜻을 담는 곳(치수·태그의 view ↔ annot_of, 인스턴스의 type ↔ of_type)이 어긋나면 오류다:
    # 한쪽만 옮겨진 병합 결과 등이 둘이 다른 대상을 가리키게 한다
    skew = _param_rel_skew(ctx)
    return {"errors": v["errors"] + len(dangling) + len(bad) + len(skew), "warnings": v["warnings"],
            "violations": (bad + skew + v["samples"] + samples)[:8]}


def _param_rel_skew(ctx) -> list:
    """kinds.ELEMENT_KINDS[종류].refs 가 관계와 짝지은 파라미터 참조가 관계와 다른 대상을 가리키는 요소의 문장 목록."""
    st = ctx.st
    out = []
    for eid in sorted(st.el):
        row = st.el[eid]
        for name, rel, target in param_ref_targets(row["kind"], row["params"]):
            ends = sorted(st.out.get((rel, eid), ()))
            if ends != ([target] if target is not None else []):
                out.append(f"{row['kind']} {eid} 의 params.{name} 은 {str(target)[:22]} 인데 관계 {rel} 은 {', '.join(e[:22] for e in ends) or '없음'} 을 가리킨다")
    return out


def _dangling_view_levels(ctx) -> list:
    """평면 뷰가 가리키는 레벨이 없는(지워진) 경우 [(뷰 이름, 레벨 id)]. 이런 뷰는 선분이 비어 도면이 조용히 사라진다."""
    st = ctx.st
    out = []
    for vid in sorted(st.by_kind.get("view", ())):
        p = st.el[vid]["params"]
        if p["type"] == "plan" and st.el.get(p["level"], {}).get("kind") != "level":
            out.append((p["name"], p["level"]))
    return out


class ScheduleRows(A):
    kind: Literal["door", "wall"]
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("schedule.rows", kind="query", risk="read", group="schedule", summary="문·벽 일람표 행",
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


# ================================================================ 뷰·도면 (S3)
# 뷰 좌표·거리는 ±1e7mm(10km)로 제한한다: 제곱이 넘치거나(OverflowError) 무한대·NaN 기하가 되는 입력을 스키마에서 거른다
VCoord = Annotated[float, Field(allow_inf_nan=False, ge=-1e7, le=1e7)]
Point2 = Annotated[list[VCoord], Field(min_length=2, max_length=2, description="[x,y] mm (±1e7)")]
Scale = Annotated[int, Field(strict=True, ge=1, le=1000, description="축척 분모 (100 = 1:100)")]
_PLAN_ONLY, _SEC_ONLY, _ELEV_ONLY = ("level", "cut_height", "view_depth"), ("p1", "p2", "depth", "flip"), ("dir",)


class ViewCreate(A):
    id: Id | None = None
    name: Name
    type: Literal["plan", "section", "elevation"]
    level: Id | None = Field(None, description="평면: 레벨 id (필수)")
    cut_height: VCoord = Field(1200.0, description="평면: 레벨 바닥에서 절단면까지 mm (±1e7)")
    view_depth: VCoord = Field(300.0, ge=0, description="평면: 절단면 아래로 투영선을 보일 깊이 mm (0..1e7)")
    p1: Point2 | None = Field(None, description="단면: 절단선 시작점 (필수)")
    p2: Point2 | None = Field(None, description="단면: 절단선 끝점 (필수)")
    depth: VCoord = Field(20000.0, gt=0, description="단면: 절단면 뒤로 보이는 거리 mm (0 초과 1e7 이하)")
    flip: bool = Field(False, description="단면: 거짓이면 절단선 진행 방향의 왼쪽을 본다, 참이면 오른쪽")
    dir: Literal["N", "E", "S", "W"] | None = Field(None, description="입면: 건물을 바라보는 면의 방위 (N = 북측 입면, 바깥에서 남쪽을 봄)")
    scale: Scale = 100

    @model_validator(mode="after")
    def _by_type(self):
        given = self.model_fields_set
        own = {"plan": _PLAN_ONLY, "section": _SEC_ONLY, "elevation": _ELEV_ONLY}[self.type]
        # 기본값과 같은 값은 쓰지 않은 것으로 본다: 로그에는 기본값까지 적혀 있어(log_args) 단면·입면 뷰의 로그를 다시 실행하면
        # cut_height 같은 기본값이 '쓰지 않는 필드'로 거절됐다(S5 의 전체 재생 검사가 찾은 S3 결함)
        stray = sorted(f for f in (given & {*_PLAN_ONLY, *_SEC_ONLY, *_ELEV_ONLY}) - set(own)
                       if getattr(self, f) != type(self).model_fields[f].default)
        if stray:
            raise ValueError(f"{self.type} 뷰에는 쓰지 않는 필드다: {', '.join(stray)}")
        if self.type == "plan" and self.level is None:
            raise ValueError("평면 뷰에는 level 이 필요하다")
        if self.type == "section":
            if self.p1 is None or self.p2 is None:
                raise ValueError("단면 뷰에는 p1, p2 가 필요하다")
            if _seg_len2(self.p1, self.p2) < 1.0:
                raise ValueError("절단선 길이는 1mm 이상이어야 한다")
        if self.type == "elevation" and self.dir is None:
            raise ValueError("입면 뷰에는 dir 이 필요하다")
        return self


def _seg_len2(p1, p2) -> float:
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    return dx * dx + dy * dy   # ** 대신 곱셈: 좌표 범위가 제한돼 넘치지 않는다


def _view_name_free(ctx, name, except_id=None):
    for v in ctx.st.by_kind.get("view", ()):
        if v != except_id and ctx.st.el[v]["params"]["name"] == name:
            raise DobimError("CONSTRAINT", f"같은 이름의 뷰가 있다: {name}", path="/name")


@command("view.create", kind="mutation", risk="write", group="view",
         summary="뷰 생성",
         args=ViewCreate, since="0.1",
         examples=[{"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200},
                   {"name": "A-A 단면", "type": "section", "p1": [3000, -2000], "p2": [3000, 6000]},
                   {"name": "남측 입면", "type": "elevation", "dir": "S", "scale": 50}])
def view_create(ctx, a: ViewCreate):
    _fresh_id(ctx, a, "view")
    _view_name_free(ctx, a.name)
    if a.type == "plan":
        ctx.row(a.level, "level")
        params = {"name": a.name, "type": "plan", "level": a.level, "cut_height": a.cut_height, "view_depth": a.view_depth, "scale": a.scale}
    elif a.type == "section":
        params = {"name": a.name, "type": "section", "p1": list(a.p1), "p2": list(a.p2), "depth": a.depth, "flip": a.flip, "scale": a.scale}
    else:
        params = {"name": a.name, "type": "elevation", "dir": a.dir, "scale": a.scale}
    ctx.put_el(a.id, row("view", "", "view.basic", params))
    return {"id": a.id}


class ViewSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    cut_height: VCoord | None = None
    view_depth: VCoord | None = Field(None, ge=0)
    p1: Point2 | None = None
    p2: Point2 | None = None
    depth: VCoord | None = Field(None, gt=0)
    flip: bool | None = None
    dir: Literal["N", "E", "S", "W"] | None = None
    scale: Scale | None = None

    @model_validator(mode="after")
    def _any(self):
        if len(self.model_fields_set - {"id"}) == 0:
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("view.set", kind="mutation", risk="write", group="view", summary="뷰 설정을 바꾼다",
         args=ViewSet, since="0.1", examples=[{"id": F.V1, "cut_height": 1500}])
def view_set(ctx, a: ViewSet):
    r = ctx.row(a.id, "view")
    p = dict(r["params"])
    allowed = {"plan": _PLAN_ONLY, "section": _SEC_ONLY, "elevation": _ELEV_ONLY}[p["type"]]
    for k in ("name", "scale", "cut_height", "view_depth", *_SEC_ONLY, *_ELEV_ONLY):
        v = getattr(a, k)
        if v is None:
            continue
        if k not in ("name", "scale") and k not in allowed:
            raise DobimError("CONSTRAINT", f"{p['type']} 뷰에는 쓰지 않는 필드다: {k}", path=f"/{k}")
        p[k] = list(v) if k in ("p1", "p2") else v
    if a.name is not None:
        _view_name_free(ctx, a.name, a.id)
    if p["type"] == "section" and _seg_len2(p["p1"], p["p2"]) < 1.0:
        raise DobimError("CONSTRAINT", "절단선 길이는 1mm 이상이어야 한다", path="/p2")
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


@command("view.delete", kind="mutation", risk="write", group="view", summary="뷰를 지운다",
         args=IdArg, since="0.1", examples=[{"id": F.V1}])
def view_delete(ctx, a: IdArg):
    ctx.row(a.id, "view")
    gone = _cascade_view_annots(ctx, a.id)
    ctx.put_el(a.id, None)
    return {"deleted": [a.id, *gone]}


class ViewList(A):
    type: Literal["plan", "section", "elevation"] | None = None
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("view.list", kind="query", risk="read", group="view", summary="뷰 목록과 캐시 상태",
         args=ViewList, idempotent=True, examples=[{}, {"type": "plan"}])
def view_list(ctx, a: ViewList):
    st = ctx.st
    after = _dec_cursor(a.cursor, "id")
    ids = [v for v in sorted(st.by_kind.get("view", ())) if a.type is None or st.el[v]["params"]["type"] == a.type]
    start = 0 if after is None else next((i for i, e in enumerate(ids) if e > after), len(ids))
    cache = D.view_cache_state(ctx.g, ids[start:start + a.limit])
    window = [{"id": v, **st.el[v]["params"], "cache": cache[v]} for v in ids[start:start + a.limit]]
    items, cut = _fit(window, cursor_of=lambda i: _enc_cursor(window[i]["id"]))
    nxt = _enc_cursor(items[-1]["id"]) if start + len(items) < len(ids) else None
    return {"items": items, "page": _page(nxt, len(ids), cut)}


class ViewLinework(A):
    id: Id
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor (선분 번호)")


@command("view.linework", kind="query", risk="read", group="view",
         summary="뷰 선분 통계와 목록",
         args=ViewLinework, idempotent=True, examples=[{"id": F.V1, "limit": 5}])
def view_linework(ctx, a: ViewLinework):
    r = ctx.row(a.id, "view")
    lines = ctx.g.q(D.view_lines, a.id)
    start = _dec_cursor(a.cursor, "int") or 0
    window = [list(ln) for ln in lines[start:start + a.limit]]
    summary = D.view_summary(lines)
    omitted = ctx.g.q(D.view_omitted, a.id)
    head = {"id": a.id, "name": r["params"]["name"], "type": r["params"]["type"], "summary": summary,
            "omitted": omitted, "columns": ["class", "x0", "y0", "x1", "y1"]}
    _warn_omitted(ctx, omitted)
    items, cut = _fit(window, base=head, cursor_of=lambda i: _enc_cursor(start + i + 1))
    nxt = _enc_cursor(start + len(items)) if start + len(items) < len(lines) else None
    return {**head, "items": items, "page": _page(nxt, len(lines), cut)}


class ViewExport(A):
    id: Id
    path: str = Field(max_length=260, description="작업 폴더 안의 .svg 또는 .pdf 경로 (확장자가 형식을 정한다). 응답에는 경로·크기·요약만 담는다. PDF 는 글꼴을 내장하지 않아 한글을 물음표로 쓴다")
    overwrite: bool = Field(False, description="거짓이면 이미 있는 파일을 덮어쓰지 않는다. 참이면 덮어쓰며 덮어쓰기 전에 자동 스냅숏을 남긴다(이 커맨드는 destructive 라 확인 토큰이 필요하다)")


def _warn_omitted(ctx, omitted: dict) -> None:
    """도면이 그리지 못한 외래 요소·분리된 벽이 있으면 조용히 넘기지 않고 알린다(S2 × S3)."""
    if omitted["foreign"] or omitted["detached"]:
        ctx.warn(f"이 뷰는 외래 요소 {omitted['foreign']}개와 분리된 벽 {omitted['detached']}개를 그리지 않았다"
                 "(외래는 형상 정보가 없고, 분리된 벽의 의도 값은 낡았다)")


def _render_view(ctx, vid):
    """뷰의 선분·채움·기준선(레벨)·제목을 내보내기용 데이터로 묶는다."""
    r = ctx.row(vid, "view")
    p = r["params"]
    levels = sorted(((ctx.st.el[l]["params"]["name"], ctx.st.el[l]["params"]["elevation"]) for l in ctx.st.by_kind.get("level", ())),
                    key=lambda t: (t[1], t[0]))
    lines = ctx.g.q(D.view_lines, vid)
    _warn_omitted(ctx, ctx.g.q(D.view_omitted, vid))
    return {"type": p["type"], "name": p["name"], "scale": p["scale"], "lines": lines, "fills": ctx.g.q(D.view_fills, vid),
            "datums": levels, "annots": ctx.g.q(AN.view_annots, vid)}


def _view_export_target(ctx, a: ViewExport):
    """내보내기 대상 검사(미리보기와 실행이 같이 쓴다): 작업 폴더 안, .svg/.pdf, 스냅숏 폴더·폴더·대체 데이터 스트림(:) 아님, 덮어쓰기 동의."""
    p = resolve_in_workspace(ctx.eng.workspace, a.path)
    ext = p.suffix.lower()
    if ext not in (".svg", ".pdf"):
        raise DobimError("PATH_DENIED", f"내보내기 대상은 .svg 또는 .pdf 여야 한다: {p.name}", path="/path")
    if SNAP_DIR.lower() in [x.lower() for x in p.parts] or p.is_dir() or ":" in p.name:
        raise DobimError("PATH_DENIED", f"쓸 수 없는 경로다: {p.name}", path="/path")
    if p.exists() and not a.overwrite:
        raise DobimError("FILE_EXISTS", f"이미 있는 파일이다: {p.name}", path="/path", hint="overwrite=true 로 덮어쓰거나 다른 이름을 쓰세요")
    return p, ext


def _view_export_preview(ctx, a: ViewExport):
    ctx.row(a.id, "view")   # 없는 뷰는 토큰을 내주기 전에 거절한다
    p, ext = _view_export_target(ctx, a)
    _snap_dir(p, ctx.eng.workspace)   # 토큰을 내주기 전에 스냅숏 폴더가 안전한지 확인한다
    return {"will_write": p.name, "format": ext[1:], "overwrites": p.exists(), "snapshot_before_overwrite": p.exists()}


# destructive 인 이유: overwrite=true 는 작업 폴더의 기존 파일을 바꾼다(아키텍처의 파괴적 커맨드 규칙: 파일 덮어쓰기 = 확인 토큰 + 자동 스냅숏).
# ifc.export 와 같은 절차를 탄다: 1회차 미리보기·확인 토큰, 덮어쓰기 전 .dobim-snapshots/<이름>.<번호> 스냅숏(ifc.snapshots 로 목록).
# 새 파일 내보내기도 같은 절차를 탄다: 확인 없이 쓰는 길을 두지 않는다(overwrite 인자는 토큰이 묶는 인자에 들어간다).
@command("view.export", kind="session", risk="destructive", group="view",
         summary="SVG·PDF 내보내기",   # commands.search 가 응답 상한 안에 들도록 짧게(destructive 는 risk 에 나온다)
         args=ViewExport, since="0.1", preview=_view_export_preview,
         examples=[{"id": F.V1, "path": "view-example.svg", "overwrite": True}])
def view_export(ctx, a: ViewExport):
    p, ext = _view_export_target(ctx, a)
    data = _render_view(ctx, a.id)
    dropped = False
    if ext == ".svg":
        blob = DX.render_svg(data)
    else:
        blob, dropped = DX.render_pdf(data)
    snap = _snapshot_existing(p, ctx.eng.workspace)
    try:
        _replace(p, lambda tmp: tmp.write_bytes(blob))
    except BaseException:   # 쓰지 못했으면 방금 만든 스냅숏은 필요 없다(재시도마다 쌓이지 않게)
        _drop_snapshot(p, snap, ctx.eng.workspace)
        raise
    return {"path": p.name, "bytes": len(blob), "format": ext[1:], "segments": len(data["lines"]), "fills": len(data["fills"]), "annotations": len(data["annots"]),
            "non_ascii_dropped": dropped, "digest": D.view_summary(data["lines"])["digest"], "rev": ctx.eng.rev, "snapshot": snap}


class ViewAffected(A):
    rev: int | None = Field(None, ge=1, description="변경 응답의 rev. 그 변경이 건드린 입력(요소·목록·관계)에 의존하는 뷰와 다시 계산 안 한 뷰(보수적)를 돌려준다. 생략하면 지금 다시 계산해야 하는(stale·new) 뷰")


@command("view.affected", kind="query", risk="read", group="view",
         summary="영향 뷰 조회",
         args=ViewAffected, idempotent=True, examples=[{}, {"rev": 3}])
def view_affected(ctx, a: ViewAffected):
    vids = sorted(ctx.st.by_kind.get("view", ()))
    g = ctx.g
    if a.rev is None:
        state = D.view_cache_state(g, vids)
        hit = [v for v in vids if state[v] != "fresh"]
        basis = "cache"
    else:
        row_ = ctx.eng.conn.execute("SELECT txn_id FROM op_log WHERE seq=?", (a.rev,)).fetchone()
        if row_ is None:
            raise DobimError("NOT_FOUND", f"로그에 없는 리비전이다: {a.rev}", path="/rev")
        from dobim_core.engine import input_keys_of, invert   # 지연 import: 레지스트리 로드가 엔진에 의존하지 않게
        # 효과가 건드린 그래프 입력 키 전체(요소·버킷·개수·관계)에서 역의존을 따라간다. 요소 id 만 보면 새 요소·아직 읽히지 않은 입력을 놓친다
        keys = input_keys_of([x for e in ctx.eng.txn_entries(row_[0]) for x in invert(json.loads(e[4]))])
        hit = D.views_depending_on(g, keys, vids)   # vids 는 살아 있는 뷰만이라 지워졌거나 생성 취소된 뷰는 나오지 않는다
        basis = "dependency"
    items = [{"id": v, "name": ctx.st.el[v]["params"]["name"], "type": ctx.st.el[v]["params"]["type"]} for v in hit]
    return {"basis": basis, "count": len(items), "items": items[:200], "truncated": len(items) > 200}


# ================================================================ 주석: 치수·태그 (S5)
# 주석은 의도 요소(kind dim, tag)이고 IFC 로 투영하지 않는다(IFC 해시 불변). 값은 저장하지 않고 항상 파생이다(그래프 질의).
# 대상이 사라지면 조용히 지우지 않고 고아로 표시한다(annot.check). 뷰를 지우면 그 뷰의 주석도 같이 지운다(응답에 나열, undo 로 복귀).
RefStr = Annotated[str, Field(pattern=r"^(wall|door|level):[0-9A-Za-z_$]{22}#[a-z]{3,9}(\.[a-z]{3,9}){0,2}$", max_length=48,
                              description="안정 참조 '종류:id#역할'. wall: axis.start axis.end(분할 뒤에도 원래 끝) own.end(이 벽 자신의 끝) trim.start trim.end own.trim.end axis face.left face.right base top / door: center jamb.start jamb.end sill head / level: elevation")]
Off = Annotated[float, Field(allow_inf_nan=False, ge=-1e5, le=1e5)]
_ANNOT_KINDS = ("dim", "tag")


def _ref_parts(ctx, ref, field):
    """참조 문법·요소 존재·역할을 검사해 (종류, id, 역할)을 돌려준다."""
    parts = AN.parse_ref(ref)
    if parts is None:
        kind = ref.split(":", 1)[0]
        roles = ", ".join(AN.ROLE_TYPES.get(kind, {})) or "wall, door, level"
        raise DobimError("CONSTRAINT", f"없는 참조 역할이다: {ref[:40]}", path=field, hint=f"{kind} 의 역할: {roles}")
    ctx.authored(parts[1], parts[0], what="치수 대상(낡은 형상을 재게 된다)")
    return parts


def _level_of(ctx, kind, eid):
    if kind == "level":
        return eid
    if kind == "door":
        eid = ctx.rels_in("hosts", eid)[0]
    return ctx.rels_out("on_level", eid)[0]


def _dim_checked(ctx, view, a_ref, b_ref, at):
    """치수 정의를 검사하고 (높이 치수인지, 해석 결과 둘, 기준 그래프)를 돌려준다. 지금 메모리 상태를 직접 읽는다."""
    vr = ctx.row(view, "view")
    pa, pb = _ref_parts(ctx, a_ref, "/a"), _ref_parts(ctx, b_ref, "/b")
    ta, tb = AN.ROLE_TYPES[pa[0]][pa[2]], AN.ROLE_TYPES[pb[0]][pb[2]]
    vertical = ta == "z" and tb == "z"
    if (ta == "z") != (tb == "z"):
        raise DobimError("CONSTRAINT", "높이 참조와 평면 참조는 한 치수에 섞을 수 없다", path="/b")
    vt = vr["params"]["type"]
    if vertical and vt == "plan":
        raise DobimError("CONSTRAINT", "높이 치수는 단면·입면 뷰에 둔다", path="/view")
    if not vertical and vt != "plan":
        raise DobimError("CONSTRAINT", "평면 치수는 평면 뷰에 둔다", path="/view")
    if not vertical:
        for pr, fld in ((pa, "/a"), (pb, "/b")):
            if _level_of(ctx, pr[0], pr[1]) != vr["params"]["level"]:
                raise DobimError("CONSTRAINT", "치수 대상이 이 평면 뷰의 레벨에 있지 않다", path=fld)
    if at is not None and not vertical:
        raise DobimError("CONSTRAINT", "at 은 높이 치수에만 쓴다", path="/at")
    g = ctx.fresh()
    ra, rb = g.q(AN.ref_resolve, a_ref), g.q(AN.ref_resolve, b_ref)
    for r_, fld in ((ra, "/a"), (rb, "/b")):
        if r_[0] == "orphan":     # 요소는 있어도 끝 역할이 분할 후계 삭제로 고아일 수 있다
            raise DobimError("CONSTRAINT", f"참조를 풀 수 없다: {r_[1]}", path=fld)
    st, val = AN.measure(ra, rb)
    if st != "ok":
        raise DobimError("CONSTRAINT", f"잴 수 없는 참조 쌍이다: {val}", path="/b",
                         hint="선 참조 두 개는 평행해야 한다" if val == "not_parallel" else None)
    return vertical, ra, rb, g


def _dir_fp(ctx, ref):
    """참조가 가리키는 벽 축선의 방향 지문(방향에 의존하는 역할일 때만, 아니면 None)."""
    kind, eid, role = AN.parse_ref(ref)
    if not AN.uses_direction(kind, role):
        return None
    w = eid if kind == "wall" else ctx.rels_in("hosts", eid)[0]
    return AN.unit_dir(ctx.st.el[w]["params"]["axis"])


def _new_annot(ctx, a, kind, params):
    ctx.put_el(a.id, row(kind, "", f"annot.{kind}", params))
    ctx.rel("annot_of", a.id, a.view, True)
    return {"id": a.id}


class DimCreate(A):
    id: Id | None = None
    view: Id = Field(description="치수를 그릴 뷰. 평면 치수는 평면 뷰, 높이 치수는 단면·입면 뷰")
    a: RefStr
    b: RefStr
    offset: Off = Field(600.0, description="치수선 간격 mm (재는 방향 왼쪽이 +)")
    at: Point2 | None = Field(None, description="높이 치수: 치수선을 그릴 평면 위치 [x,y] (생략하면 [0,0])")
    name: Name | None = None


@command("annot.dim.create", kind="mutation", risk="write", group="annot",
         summary="연관 치수 생성",
         args=DimCreate, since="0.1",
         examples=[{"view": F.V1, "a": f"wall:{F.W1}#trim.start", "b": f"wall:{F.W1}#trim.end"}])
def annot_dim_create(ctx, a: DimCreate):
    _fresh_id(ctx, a, "dim")
    vertical, ra, rb, g = _dim_checked(ctx, a.view, a.a, a.b, a.at)
    params = {"name": a.name or f"DIM-{a.id[-6:]}", "view": a.view, "a": a.a, "b": a.b, "off": a.offset, "home": [0.0, 0.0],
              "dirs": {"a": _dir_fp(ctx, a.a), "b": _dir_fp(ctx, a.b)}}
    if vertical:
        params["at"] = list(a.at or [0.0, 0.0])
    (ax, ay), (bx, by) = AN.dim_ends(g, params, ra, rb)
    params["home"] = [round((ax + bx) / 2, 3), round((ay + by) / 2, 3)]
    return _new_annot(ctx, a, "dim", params)


class TagCreate(A):
    id: Id | None = None
    view: Id = Field(description="태그를 둘 평면 뷰")
    target: Id = Field(description="벽 또는 문")
    fields: Annotated[list[Annotated[str, Field(max_length=16)]], Field(min_length=1, max_length=4)] = ["name"]
    offset: Annotated[list[Off], Field(min_length=2, max_length=2)] = [400.0, 400.0]
    name: Name | None = None


def _tag_checked(ctx, view, target, fields):
    vr = ctx.row(view, "view")
    tr = ctx.authored(target, what="태그 대상(낡은 값을 보이게 된다)")
    if vr["params"]["type"] != "plan":
        raise DobimError("CONSTRAINT", "태그는 평면 뷰에 둔다", path="/view")
    if tr["kind"] not in AN.TAG_FIELDS:
        raise DobimError("CONSTRAINT", f"태그는 벽·문에만 단다: {tr['kind']}", path="/target")
    bad = [f for f in fields if f not in AN.TAG_FIELDS[tr["kind"]]]
    if bad:
        raise DobimError("CONSTRAINT", f"{tr['kind']} 에 없는 표시 필드: {', '.join(bad)}", path="/fields",
                         hint="가능: " + ", ".join(AN.TAG_FIELDS[tr["kind"]]))
    if _level_of(ctx, tr["kind"], target) != vr["params"]["level"]:
        raise DobimError("CONSTRAINT", "태그 대상이 이 평면 뷰의 레벨에 있지 않다", path="/target")


@command("annot.tag.create", kind="mutation", risk="write", group="annot",
         summary="태그 생성",
         args=TagCreate, since="0.1", examples=[{"view": F.V1, "target": F.D1, "fields": ["name", "width"]}])
def annot_tag_create(ctx, a: TagCreate):
    _fresh_id(ctx, a, "tag")
    _tag_checked(ctx, a.view, a.target, a.fields)
    ax, ay = AN.tag_anchor(ctx.fresh(), a.target)
    params = {"name": a.name or f"TAG-{a.id[-6:]}", "view": a.view, "target": a.target, "fields": list(a.fields),
              "off": list(a.offset), "home": [round(ax + a.offset[0], 3), round(ay + a.offset[1], 3)]}
    return _new_annot(ctx, a, "tag", params)


class AnnotRebind(A):
    forbid_null = True
    id: Id
    a: RefStr | None = Field(None, description="치수: 새 첫째 참조")
    b: RefStr | None = Field(None, description="치수: 새 둘째 참조")
    target: Id | None = Field(None, description="태그: 새 대상")
    fields: Annotated[list[Annotated[str, Field(max_length=16)]], Field(min_length=1, max_length=4)] | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.a, self.b, self.target, self.fields)):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("annot.rebind", kind="mutation", risk="write", group="annot",
         summary="고아 주석 다시 묶기",
         args=AnnotRebind, since="0.1", examples=[{"id": F.A1, "b": f"wall:{F.W1}#axis.end"}])
def annot_rebind(ctx, a: AnnotRebind):
    r = ctx.row(a.id)
    if r["kind"] not in _ANNOT_KINDS:
        raise DobimError("CONSTRAINT", f"주석이 아니다: {r['kind']}", path="/id")
    p = dict(r["params"])
    if r["kind"] == "dim":
        if a.target is not None or a.fields is not None:
            raise DobimError("CONSTRAINT", "치수에는 a, b 만 쓴다", path="/target")
        p["a"], p["b"] = a.a or p["a"], a.b or p["b"]
        vertical, ra, rb, g = _dim_checked(ctx, p["view"], p["a"], p["b"], p.get("at"))
        dirs = dict(p.get("dirs") or {})
        for side, given in (("a", a.a), ("b", a.b)):   # 다시 묶은 쪽만 지문을 새로 잡는다(같은 참조를 다시 주면 경고를 확인한 것으로 본다)
            if given is not None:
                dirs[side] = _dir_fp(ctx, given)
        p["dirs"] = {"a": dirs.get("a"), "b": dirs.get("b")}
        (ax, ay), (bx, by) = AN.dim_ends(g, p, ra, rb)
        p["home"] = [round((ax + bx) / 2, 3), round((ay + by) / 2, 3)]
    else:
        if a.a is not None or a.b is not None:
            raise DobimError("CONSTRAINT", "태그에는 target, fields 만 쓴다", path="/a")
        p["target"], p["fields"] = a.target or p["target"], list(a.fields or p["fields"])
        _tag_checked(ctx, p["view"], p["target"], p["fields"])
        ax, ay = AN.tag_anchor(ctx.fresh(), p["target"])
        p["home"] = [round(ax + p["off"][0], 3), round(ay + p["off"][1], 3)]
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


@command("annot.delete", kind="mutation", risk="write", group="annot", summary="주석 하나를 지운다",
         args=IdArg, since="0.1", examples=[{"id": F.A1}])
def annot_delete(ctx, a: IdArg):
    r = ctx.row(a.id)
    if r["kind"] not in _ANNOT_KINDS:
        raise DobimError("CONSTRAINT", f"주석이 아니다: {r['kind']}", path="/id")
    _delete_one(ctx, a.id)
    return {"deleted": [a.id]}


def _annot_item(ctx, aid):
    """주석 하나의 목록 항목: 정의 + 파생 상태."""
    r = ctx.st.el[aid]
    p = r["params"]
    st = ctx.g.q(AN.annot_state, aid)
    out = {"id": aid, "kind": r["kind"], "name": p["name"], "view": p["view"], "status": st[1]}
    if r["kind"] == "dim":
        out.update(a=p["a"], b=p["b"])
    else:
        out.update(target=p["target"], fields=p["fields"])
    flags = ctx.g.q(AN.annot_flags, aid)
    if flags:
        out["warnings"] = list(flags)
    if st[1] == "ok":
        out["value" if r["kind"] == "dim" else "text"] = st[2]
    elif st[1] == "orphan":
        out["reasons"] = [{"ref": w, "why": why} for w, why in st[2]]
    else:
        out["why"] = st[2]
    return out


class AnnotList(A):
    view: Id | None = None
    kind: Literal["dim", "tag"] | None = None
    status: Literal["ok", "orphan", "invalid"] | None = Field(None, description="orphan = 참조 대상이 사라져 값을 낼 수 없는 주석")
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("annot.list", kind="query", risk="read", group="annot", summary="주석 목록·고아 상태",
         args=AnnotList, idempotent=True, examples=[{}, {"status": "orphan", "limit": 5}])
def annot_list(ctx, a: AnnotList):
    after = _dec_cursor(a.cursor, "id")
    ids = sorted(i for k in _ANNOT_KINDS if a.kind in (None, k) for i in ctx.st.by_kind.get(k, ()))
    ids = [i for i in ids if a.view is None or ctx.st.el[i]["params"]["view"] == a.view]
    if a.status:
        ids = [i for i in ids if ctx.g.q(AN.annot_state, i)[1] == a.status]
    start = 0 if after is None else next((i for i, e in enumerate(ids) if e > after), len(ids))
    window = [_annot_item(ctx, i) for i in ids[start:start + a.limit]]
    items, cut = _fit(window, cursor_of=lambda i: _enc_cursor(window[i]["id"]))
    nxt = _enc_cursor(items[-1]["id"]) if start + len(items) < len(ids) else None
    return {"items": items, "page": _page(nxt, len(ids), cut)}


class AnnotCheck(A):
    limit: int = Field(20, ge=1, le=1000, description="문제 주석을 한 번에 나열할 최대 개수")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("annot.check", kind="query", risk="read", group="annot", summary="주석 점검(정상·고아·무효)",
         args=AnnotCheck, idempotent=True, examples=[{}])
def annot_check(ctx, a: AnnotCheck):
    ids = ctx.g.q(AN.all_annots)
    orphan, invalid, ok, problems, warned, warn_kinds = {}, {}, 0, [], [], {}
    for i in ids:
        st = ctx.g.q(AN.annot_state, i)
        flags = ctx.g.q(AN.annot_flags, i)
        for fl in flags:
            k = fl.split(":", 1)[0]
            warn_kinds[k] = warn_kinds.get(k, 0) + 1
        if flags:
            warned.append(i)
        if st[1] == "ok":
            ok += 1
            continue
        bucket = orphan if st[1] == "orphan" else invalid
        key = st[2][0][1] if st[1] == "orphan" else st[2]
        bucket[key] = bucket.get(key, 0) + 1
        problems.append(i)
    listed = sorted(set(problems) | set(warned))   # 목록에는 고아·무효와 경고가 걸린 주석을 함께 낸다
    dangling = sum(1 for i in ids if ctx.st.el[i]["params"]["view"] not in ctx.st.el)   # 뷰가 없는 주석(있으면 결함)
    after = _dec_cursor(a.cursor, "id")
    start = 0 if after is None else next((k for k, e in enumerate(listed) if e > after), len(listed))
    window = [_annot_item(ctx, i) for i in listed[start:start + a.limit]]
    head = {"total": len(ids), "ok": ok, "orphan": dict(sorted(orphan.items())), "invalid": dict(sorted(invalid.items())),
            "warnings": dict(sorted(warn_kinds.items())), "dangling_view": dangling, "problems": len(problems)}
    items, cut = _fit(window, base=head, cursor_of=lambda i: _enc_cursor(window[i]["id"]))
    nxt = _enc_cursor(items[-1]["id"]) if start + len(items) < len(listed) else None
    return {**head, "items": items, "page": _page(nxt, len(listed), cut)}


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
    # 되돌리기·다시 실행도 동결 규칙을 지킨다: 다시 판정으로 동결된 요소의 동결 전 편집은 되돌릴 수 없다(효과를 그대로 적용하면 저작으로 되살아난다).
    # 다른 효과를 하나라도 적용하기 전에 모두 확인해 거절이 아무것도 남기지 않게 한다. 이 트랜잭션이 스스로 만드는 요소는 지금 상태에 없으므로 걸리지 않는다
    for e in effects:
        if e[0] in ("el", "mat") and (row := ctx.st.el.get(e[1])) is not None:
            ctx._frozen_check(e[1], row, "동결 전 편집")
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


@command("history.undo", kind="mutation", risk="write", group="history", summary="되돌리기",
         args=HistoryArgs, since="0.1", examples=[{}])
def history_undo(ctx, a: HistoryArgs):
    return _history(ctx, a, True)


@command("history.redo", kind="mutation", risk="write", group="history", summary="다시 실행",
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
         summary="변경이 건드린 요소 목록",
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


# ================================================================ 병합 (S8)
class Resolution(A):
    key: str = Field(min_length=1, max_length=128, description="merge.preview 가 알려 준 충돌 키(items[].key) 그대로")
    take: Literal["ours", "theirs", "both"] = Field(description="ours = 이 프로젝트 쪽, theirs = other 프로젝트 쪽. 의미 충돌은 반대쪽이 기여한 변경을 조상 값으로 되돌린다. "
                                                              "both = ID_COLLISION 에만: 우리 요소는 그대로 두고 상대 요소를 새 id 로 옮겨 둘 다 살린다(새 id 는 결정적)")


class MergeBase(A):
    other: str = Field(max_length=260, description="작업 폴더 안의 상대 프로젝트(.dobim). 같은 조상에서 갈라진 복사본이어야 한다. 읽기 전용으로 열며 바꾸지 않는다")
    other_rev: int | None = Field(None, ge=0, description="상대 로그 길이를 이 값으로 고정한다(미리보기 때의 값). 다르면 REV_CONFLICT. 생략하면 현재 값을 쓰고 기록한다")
    resolve: list[Resolution] = Field(default_factory=list, max_length=200,
                                      description="명시적 해결 지시. 충돌은 자동으로 해결하지 않는다: 여기 없는 충돌이 하나라도 남으면 합치지 않는다")

    @field_validator("resolve")
    @classmethod
    def _unique_keys(cls, v):
        keys = [r.key for r in v]
        if len(keys) != len(set(keys)):
            raise ValueError("같은 충돌 키를 두 번 해결할 수 없다")
        return v


class MergeApply(MergeBase):
    other_fp: str | None = Field(None, max_length=64, description="기록 전용: 합친 상대 로그의 지문(생략). 값을 주어도 실행 때 실제 지문으로 덮어쓴다. "
                                                                  "같은 상대를 다시 병합할 때 앞선 병합을 공통 조상으로 쓰는 데 읽는다")


def _merge_plan(ctx, a: MergeBase):
    import os
    eng = ctx.eng
    other = resolve_in_workspace(eng.workspace, a.other, must_exist=True)
    if os.path.normcase(os.path.realpath(other)) == os.path.normcase(os.path.realpath(eng.path)):
        raise DobimError("CONSTRAINT", "자기 자신과는 병합할 수 없다", path="/other")
    their_uuid, theirs = merge.load_other(other)
    if their_uuid != eng.project_guid_uuid:
        raise DobimError("CONSTRAINT", "같은 프로젝트에서 갈라진 파일이 아니다(project_uuid 가 다르다)", path="/other")
    if a.other_rev is not None and a.other_rev != len(theirs):
        raise DobimError("REV_CONFLICT", f"상대 리비전이 어긋났다: 기대 {a.other_rev}, 현재 {len(theirs)}", path="/other_rev",
                         details={"expected": a.other_rev, "actual": len(theirs)})
    ours = merge.read_rows(eng.conn)
    for who, rows, has_base in (("이 프로젝트", ours, eng.has_base), ("상대 프로젝트", theirs, getattr(theirs, "has_base", False))):
        if has_base and merge.genesis_of(rows) is None:   # 통합 전에 가져온 프로젝트: 시작 상태가 로그에 없어 병합할 수 없다
            raise DobimError("CONSTRAINT", f"{who}는 시작 상태 기록 없이 가져온 프로젝트라 병합할 수 없다", path="/other",
                             hint="ifc.import 로 다시 가져오세요(시작 상태 기록은 가져올 때만 만들 수 있다)")
    return merge.build_plan(ours, theirs, [{"key": r.key, "take": r.take} for r in a.resolve], eng.project_guid_uuid)


class MergePreview(MergeBase):
    limit: int = Field(20, ge=1, le=200, description="충돌 항목 최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("merge.preview", kind="query", risk="read", group="merge",
         summary="다른 프로젝트 파일과의 병합을 미리 본다: 공통 조상, 충돌(같은 필드·삭제 대 수정·id 충돌·의미 충돌)을 키·양쪽 op 와 함께 보고. 아무것도 바꾸지 않는다",
         args=MergePreview, idempotent=True, examples=[{"other": "other.dobim", "limit": 5}])
def merge_preview(ctx, a: MergePreview):
    p = _merge_plan(ctx, a)
    if p.unused:
        ctx.warn(f"충돌과 맞지 않는 해결 지시 {len(p.unused)}건은 무시했다(이미 합쳐졌거나 키가 틀림): {p.unused[0]}")
    after = _dec_cursor(a.cursor, "name")
    start = 0 if after is None else next((i for i, c in enumerate(p.conflicts) if c["key"] > after), len(p.conflicts))
    window = [limits.clip_deep(c)[0] for c in p.conflicts[start:start + a.limit]]
    by_type: dict[str, int] = {}
    for c in p.conflicts:
        by_type[c["type"]] = by_type.get(c["type"], 0) + 1
    ne, nt = p.new_ours, p.new_theirs   # 기준(공통 접두 또는 앞선 병합) 이후 새 항목 수
    relation = "same" if ne == 0 and nt == 0 else ("ours_behind" if ne == 0 else ("ours_ahead" if nt == 0 else "diverged"))
    base = {"fork_rev": p.fork, "base": {"kind": p.base_kind, "rev": p.base_rev or p.fork},
            "ours_rev": p.ours_rev, "other_rev": p.other_rev, "relation": relation, "stats": p.stats,
            "conflict_count": len(p.conflicts), "by_type": by_type, "plan_digest": p.digest(), "resolved": len(p.resolved),
            "semantic_checked": p.semantic_checked}
    items, cut = _fit(window, base=base, cursor_of=lambda i: _enc_cursor(window[i]["key"]),
                      extra_bytes=800, extra_tokens=500.0)   # 아래 경고 세 가지(해결 키 무시·의미 검사 보류·잘림)가 붙을 몫
    nxt = _enc_cursor(items[-1]["key"]) if start + len(items) < len(p.conflicts) and items else None
    if not p.semantic_checked:
        ctx.warn("필드·요소 충돌이 남아 있어 의미 충돌(호스트 삭제, 문 범위, 중복 등)은 아직 검사하지 않았다: resolve 로 해결한 뒤 다시 미리보세요")
    if cut:
        ctx.warn(f"충돌 {len(p.conflicts)}건 중 일부만 보인다: page.next_cursor 를 cursor 로 넘겨 이어 읽으세요")
    return {**base, "items": items, "page": _page(nxt, len(p.conflicts), cut)}


@command("merge.apply", kind="mutation", risk="write", group="merge",
         summary="다른 프로젝트 파일의 변경을 이 프로젝트에 합친다(3-way, 필드 단위). 충돌이 하나라도 남으면 합치지 않는다. 결과는 로그 항목 하나라 history.undo 로 되돌린다",
         args=MergeApply, since="0.1", examples=[{"other": "other.dobim"}])
def merge_apply(ctx, a: MergeApply):
    merge.check_batch_position(ctx)
    p = _merge_plan(ctx, a)
    if not p.clean:
        raise merge.conflict_error(p)
    # 로그를 접은 우리 쪽 상태가 엔진의 실제 상태와 같아야 한다(다르면 효과의 old 가 어긋난다)
    if merge.rows_of(p.ours) != ctx.st.el or sorted(u[1:] for u in p.ours if u[0] == "rel") != ctx.st.relations():
        raise DobimError("INTERNAL", "로그를 접은 상태가 엔진 상태와 다르다")
    if p.unused:
        ctx.warn(f"충돌과 맞지 않는 해결 지시 {len(p.unused)}건은 무시했다(이미 합쳐졌거나 키가 틀림): {p.unused[0]}")
    a.other_rev = p.other_rev   # 로그에 실제로 합친 상대 리비전과 그 접두의 지문을 남긴다(다음 병합의 공통 조상 계산이 읽는다)
    a.other_fp = p.other_fp
    cnt = merge.emit_plan(ctx, p)
    return {"fork_rev": p.fork, "ours_rev": p.ours_rev, "other_rev": p.other_rev, "changes": cnt,
            "resolved": {"count": len(p.resolved), "keys": p.resolved[:5]}, "plan_digest": p.digest()}


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
         summary="IFC4 내보내기",
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


class IfcImport(A):
    source: str = Field(max_length=260, description="작업 폴더 안의 IFC4 파일(.ifc). 읽기만 한다")
    path: str = Field(max_length=260, description="새로 만들 프로젝트(.dobim) 경로. 이미 있으면 FILE_EXISTS")
    guid: str | None = Field(None, max_length=36, description="프로젝트 UUID. 생략하면 무작위")


@command("ifc.import", kind="session", risk="write", group="io", summary="외래 IFC4 를 새 프로젝트로 가져온다: 요소를 authored·foreign·detached 로 분류하고 원본을 외래 기반으로 보존",
         args=IfcImport, needs_project=False,
         examples=[{"source": "import-example.ifc", "path": "imported-example.dobim", "guid": "5d0b1a50-0000-4a00-8000-0d0b1a5000f3"}])
def ifc_import(env, a: IfcImport):
    """새 프로젝트를 만든다(project.new 와 같다: 기존 프로젝트에 합치지 않는다). DoBIM_Intent 가 있고 투영이 들어온 형상과 같은 요소는
    authored, 의도가 있지만 형상이 다르면 detached(동결, 경고), 의도가 없거나 해석할 수 없으면 foreign. 원본 바이트는 SQLite 에 보존한다."""
    from dobim_core.engine import Engine
    from dobim_core.foreign import plan_import
    src = resolve_in_workspace(env.workspace, a.source, must_exist=True)
    if src.suffix.lower() != ".ifc":
        raise DobimError("PATH_DENIED", f"가져올 파일은 .ifc 여야 한다: {src.name}", path="/source")
    dst = resolve_in_workspace(env.workspace, a.path)
    if dst.exists():
        raise DobimError("FILE_EXISTS", f"이미 있는 파일이다: {dst.name}", path="/path")
    try:
        data = src.read_bytes()
    except OSError as ex:
        raise _io_error(src, ex) from ex
    plan = plan_import(data)
    eng = Engine.create_imported(dst, data, plan, env.actor, guid=a.guid)
    try:
        counts = plan.counts()
        shown = [{"id": d["id"], "reasons": d["reasons"][:4]} for d in plan.detached[:20]]
        return {"path": dst.name, "rev": eng.rev, "project_uuid": eng.project_guid_uuid, "schema": plan.schema, "bytes": len(data),
                "elements": counts, "skeleton": plan.skeleton, "detached": shown, "detached_total": len(plan.detached),
                "warnings": [w[:240] for w in plan.warnings[:20]], "warnings_total": len(plan.warnings)}
    finally:
        eng.close()


class SnapshotList(A):
    path: str = Field(max_length=260, description="작업 폴더 안의 .ifc 경로")
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("ifc.snapshots", kind="query", risk="read", group="io", summary="IFC 스냅숏 목록",
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
         summary="스냅숏으로 복원",
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
