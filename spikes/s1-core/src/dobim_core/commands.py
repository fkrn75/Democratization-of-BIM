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
from dobim_core import kinds as K
from dobim_core import wallcut as WC
from dobim_core import walltop as WT
from dobim_core.kinds import KIND_NAMES, READ_REL_NAMES, REL_KINDS, is_corridor, param_ref_targets, polygon_area2, polygon_problem
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


def wall_len(p: dict) -> float:
    """벽 축 길이(곡선 벽 W2 ARC-04 은 호 길이: 문·창 offset 을 재는 길이)."""
    if "arc" in p:
        from dobim_core import arcgeom as AG
        return AG.length(AG.info(p["axis"], p["arc"]))
    return sqrt(_axis_len2(p["axis"]))


def no_arc(ctx, w: str, what: str, path: str = "/id") -> None:
    """곡선 벽(W2 ARC-04)이 아직 받지 않는 편집이면 거절한다."""
    if "arc" in ctx.st.el[w]["params"]:
        raise DobimError("CONSTRAINT", f"곡선 벽은 아직 {what} 를 지원하지 않는다: {w}", path=path,
                         hint="곡선 벽의 축은 wall.arc_set 으로 바꾼다")


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
    if kind in ("view", "dim", "tag", "sheet", "grid", "draft", "revision", "vtemplate", "larea", "opening") and ctx.external_def(a.id):   # 가져온 파일에 되살리지 않은 외부 정의가 이 id 로 있다
        ctx.warn(f"가져온 파일에 이 id 의 {kind} 정의가 되살려지지 않은 채 남아 있다: 새 정의가 그 속성을 덮어쓴다(지우거나 되돌리면 원래 정의가 돌아온다)")
    return a.id


# ================================================================ 프로젝트·시스템
class ProjectNew(A):
    path: str = Field(max_length=260, description="작업 폴더 안의 .dobim 경로")
    guid: str | None = Field(None, max_length=36, description="프로젝트 UUID. 생략하면 무작위, 지정하면 재현 가능")
    template: Literal["kr-small"] | None = Field(None, description="내장 템플릿: 레벨·뷰·뷰 템플릿·시트·키노트를 미리 채운다")
    template_file: str | None = Field(None, max_length=260, description="작업 폴더 안의 템플릿 JSON(dobim.template/1 커맨드 목록, $이름 은 id)")

    @model_validator(mode="after")
    def _one_template(self):
        if self.template is not None and self.template_file is not None:
            raise ValueError("template 과 template_file 은 함께 쓸 수 없다")
        return self


def _template_cmds(env, a: ProjectNew, guid: str) -> list:
    """템플릿을 읽어 (커맨드, 검증한 인자) 목록으로 만든다. 프로젝트 파일을 만들기 전에 다 검사한다."""
    from dobim_core import dispatch
    from dobim_core import project_template as PT
    if a.template is not None:
        tpl = PT.load_builtin(a.template)
    else:
        tpl = PT.load_file(resolve_in_workspace(env.workspace, a.template_file, must_exist=True))
    out = []
    for i, (name, args) in enumerate(PT.resolve(tpl, guid)):
        try:
            cmd = dispatch.get_command(name)
            if cmd.kind != "mutation" or name.startswith("history."):
                raise DobimError("USAGE", f"템플릿에는 모델을 바꾸는 커맨드만 넣을 수 있다: {name}", path="/command")
            out.append((cmd, dispatch.parse_args(cmd, args)))
        except DobimError as ex:
            ex.path = f"/template/commands/{i}{ex.path or ''}"
            raise
    return out


@command("project.new", kind="session", risk="write", group="project", summary="프로젝트 생성(템플릿을 주면 미리 채운다)",
         args=ProjectNew, needs_project=False, examples=[{"path": "new-example.dobim", "guid": "5d0b1a50-0000-4a00-8000-0d0b1a5000f2"},
                                                         {"path": "tpl-example.dobim", "guid": "5d0b1a50-0000-4a00-8000-0d0b1a5000f4", "template": "kr-small"}])
def project_new(env, a: ProjectNew):
    import uuid
    from dobim_core.engine import Engine
    p = resolve_in_workspace(env.workspace, a.path)
    use_tpl = a.template is not None or a.template_file is not None
    guid = a.guid
    if use_tpl and guid is None:   # 템플릿 기호의 id 는 프로젝트 UUID 로 정한다: 파일을 만들기 전에 정해 둔다
        guid = str(uuid.uuid4())
    try:
        cmds = _template_cmds(env, a, str(uuid.UUID(guid))) if use_tpl else []
    except ValueError:   # guid 가 UUID 꼴이 아니다(템플릿 없을 때는 엔진이 같은 검사를 한다)
        raise DobimError("SCHEMA_INVALID", "guid 는 UUID 여야 한다", path="/guid") from None
    if cmds and p.exists():
        raise DobimError("FILE_EXISTS", f"이미 있는 파일이다: {p.name}", path="/path")
    eng = Engine.create(p, env.actor, guid=guid)
    try:
        out = {"path": p.name, "rev": eng.rev, "project_uuid": eng.project_guid_uuid}
        if cmds:   # 한 트랜잭션: 한 번의 실행 취소로 빈 프로젝트가 된다
            r = eng.mutate(cmds)
            kinds: dict = {}
            for e in r["affected"]["created"]:
                k = eng.st.el[e]["kind"]
                kinds[k] = kinds.get(k, 0) + 1
            out.update(rev=r["rev"], template={"name": a.template or Path(a.template_file).name, "commands": len(cmds), "created": dict(sorted(kinds.items()))})
            if r["warnings"]:
                out["warnings"] = r["warnings"][:10]
    except BaseException:
        eng.close()
        for suffix in ("", "-wal", "-shm", "-journal"):   # 템플릿이 실패하면 반쯤 채운 프로젝트 파일을 남기지 않는다
            try:
                Path(str(p) + suffix).unlink(missing_ok=True)
            except OSError:
                pass
        raise
    eng.close()
    return out


class Empty(A):
    pass


@command("project.info", kind="query", risk="read", group="project", summary="프로젝트 정보",
         args=Empty, examples=[{}], idempotent=True)
def project_info(ctx, a):
    g = ctx.g
    out = {"project_uuid": ctx.eng.project_guid_uuid, "rev": ctx.eng.rev, "schema_version": ctx.eng.schema_version,
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
    section: str | None = Field(None, max_length=96, description="나눠 받는 상세 한 부분(응답 split.sections 의 이름): def:<정의> 또는 property:<인자>")


# describe 응답 나눔(W1 연장, S11 크기 기준 '도구 응답 8,000토큰 이내'): MCP 는 같은 봉투를 text 와 structuredContent 에 한 벌씩 싣는다.
# 결과 JSON 이 이 바이트를 넘는 커맨드는 요약(정의 $defs 와 큰 인자 스키마를 뺀 것)만 주고, 뺀 부분은 section 으로 하나씩 받게 한다.
# 9,000바이트 x 두 벌 = 실측 토큰/바이트 최대 0.40(cl100k) 기준 약 7,200토큰 + 봉투 뼈대. 넘지 않는 커맨드의 응답은 이전과 같다(하위 호환).
DESCRIBE_MAX_BYTES = 9_000


def _desc_size(x) -> int:
    return len(json.dumps(x, ensure_ascii=False).encode("utf-8"))


def describe_parts(c) -> tuple[dict, dict[str, dict]]:
    """커맨드 describe 의 (기본 응답, 나눈 부분 이름 -> 그 부분 스키마). 응답이 작으면 부분은 비어 있고 기본 응답은 전체다."""
    full = c.describe()
    if _desc_size(full) <= DESCRIBE_MAX_BYTES:
        return full, {}
    import copy
    sch = copy.deepcopy(full["input_schema"])
    parts: dict[str, dict] = {f"def:{k}": v for k, v in sorted(sch.pop("$defs", {}).items())}
    props = sch.get("properties", {})
    out = dict(full, input_schema=sch, split={
        "reason": f"전체 스키마가 {DESCRIBE_MAX_BYTES}바이트를 넘어 나눴다(도구 응답 크기 기준)",
        "how": "commands.describe 에 section 을 주면 그 부분의 전체 스키마를 돌려준다. $ref '#/$defs/X' 는 section 'def:X' 다",
        "sections": sorted(parts)})
    # 정의를 뺀 뒤에도 크면 가장 큰 인자 스키마부터 형식·짧은 설명만 남긴 자리표로 바꾼다(같은 크기면 이름 순)
    while _desc_size(out) > DESCRIBE_MAX_BYTES:
        left = [(-_desc_size(v), k) for k, v in props.items() if f"property:{k}" not in parts]
        if not left:
            break
        _, k = min(left)
        parts[f"property:{k}"] = props[k]
        d = str(props[k].get("description", ""))
        props[k] = {**({"type": props[k]["type"]} if "type" in props[k] else {}),
                    "description": d if len(d) <= 60 else d[:60] + "…", "section": f"property:{k}"}
        out["split"]["sections"] = sorted(parts)
    return out, parts


@command("commands.describe", kind="query", risk="read", group="system", summary="커맨드 스키마·예시(큰 스키마는 요약과 부분별 상세)",
         args=Describe, needs_project=False, idempotent=True, examples=[{"name": "wall.move"}, {"name": "solid.create", "section": "def:MemberSection"}])
def commands_describe(env, a: Describe):
    c = registry.load().get(a.name)
    if c is None:
        raise DobimError("UNKNOWN_COMMAND", f"없는 커맨드: {a.name}", path="/name",
                         hint="commands.search 로 찾으세요")
    head, parts = describe_parts(c)
    if a.section is None:
        return head
    if a.section not in parts:
        raise DobimError("NOT_FOUND", f"나눈 부분이 없다: {a.section}", path="/section",
                         hint=("이 커맨드의 부분: " + ", ".join(sorted(parts))) if parts else "이 커맨드는 나누지 않았다(section 없이 전체가 온다)")
    kind, _, key = a.section.partition(":")
    out = {"name": c.name, "section": a.section, "schema": parts[a.section]}
    if kind == "property":
        out["required"] = key in head["input_schema"].get("required", [])
    return out


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


class OpeningIn(A):
    """모양 개구부 하나(18단계): 벽·판에 뚫는 사각·반원 아치·뾰족 아치·원 구멍."""
    shape: Literal["rect", "round", "pointed", "circle"] = Field(description="rect 사각, round 반원 머리, pointed 뾰족 아치, circle 원(폭 = 높이)")
    offset: Num = Field(description="개구부 중심의 길이 방향 위치 mm(벽은 축선 시작점, 판은 line 시작점에서)")
    sill: Num = Field(description="개구부 바닥 높이 mm(벽은 레벨 기준 0 이상(바닥 오프셋이 있으면 벽 바닥 이상), 판은 레벨 기준 z)")
    width: Pos = Field(description="개구부 폭 mm")
    height: Pos = Field(description="개구부 전체 높이 mm(아치 꼭대기까지)")
    rise: Pos | None = Field(None, description="뾰족 아치의 아치 높이 mm(폭/2 이상 높이 이하). 생략하면 정삼각 아치(폭 x 0.866)")
    fill: Literal["none", "glazing", "door"] | None = Field(None, description="채움(기록·평면 표시용): none 빈 구멍, glazing 유리, door 출입구")


Openings = Annotated[list[OpeningIn], Field(max_length=64, description="모양 개구부 목록(0~64개). 빈 목록이면 개구부를 모두 뗀다")]


def _openings_in(ops) -> list:
    """입력 개구부 -> 행에 저장하는 모양(없는 선택 키는 두지 않는다)."""
    out = []
    for o in ops:
        d = {"shape": o.shape, "offset": float(o.offset), "sill": float(o.sill), "width": float(o.width), "height": float(o.height)}
        if o.rise is not None:
            d["rise"] = float(o.rise)
        if o.fill is not None:
            d["fill"] = o.fill
        out.append(d)
    return out


def _wall_checked(p: dict) -> dict:
    why = K.row_params_error("wall", p)
    if why:
        raise DobimError("CONSTRAINT", f"벽이 올바르지 않다: {why}", path="/openings" if "openings" in why else ("/top_profile" if "top_profile" in why else ("/cuts" if "cuts" in why else None)))
    return p


TopProfile = Annotated[list[Annotated[list[Num], Field(min_length=2, max_length=2)]], Field(max_length=WT.TOP_PTS_MAX, description=(
    "벽 상단 윤곽(21단계): 축선 따라 거리 s(축 시작점 0 ~ 축 길이)와 레벨 기준 높이 z 의 점 [[s, z], ...]. s 오름차순, 양 끝 포함, 2점 이상, z > 0. "
    "같은 s 두 점은 수직 단. wall.set 에 빈 목록을 주면 윤곽을 뗀다(상단이 height 로 수평)"))]


Cuts = Annotated[list[Annotated[list[Num], Field(min_length=6, max_length=6)]], Field(max_length=WC.CUTS_MAX, description=(
    "벽 잘라내기(23단계): 다른 부재가 벽에서 빼낸 상자 [[u0, u1, v0, v1, z0, z1], ...]. u = 축 시작점에서 축 방향 거리, v = 축선에서 왼쪽 거리"
    "(벽 몸체 -두께/2 ~ +두께/2), z = 레벨 기준 높이. 형상에 쓸 때 벽 범위로 자른다"))]


def _door_box(dp: dict) -> tuple:
    return (dp["offset"] - dp["width"] / 2.0, dp["offset"] + dp["width"] / 2.0, dp["sill"], dp["sill"] + dp["height"])


def _hosted_under_top(ctx, wid, p: dict) -> None:
    """상단 윤곽이 있는 벽: 호스팅된 문의 위 끝이 윤곽 아래인가(21단계). 패밀리 인스턴스는 보지 않는다(한계: elements-design 23.2)."""
    top = p.get("top_profile")
    if top is None:
        return
    boxes = [_door_box(ctx.st.el[d]["params"]) for d in sorted(ctx.rels_out("hosts", wid)) if ctx.st.el[d]["kind"] == "door"]
    why = WT.under_problem(top, boxes=boxes)
    if why:
        raise DobimError("CONSTRAINT", f"벽이 올바르지 않다: {why}", path="/top_profile")


def _door_under_top(ctx, host, dp: dict) -> None:
    """문을 놓거나 고칠 때: 호스트 벽에 상단 윤곽이 있으면 문 위 끝이 그 아래여야 한다(21단계).
    바닥 오프셋(22단계)이 0 보다 크면 문턱이 벽 바닥 아래로 내려갈 수 없다."""
    if dp["sill"] < WT.base(ctx.st.el[host]["params"]) - 1e-9:
        raise DobimError("CONSTRAINT", "문턱이 호스트 벽 바닥(base_offset) 아래다", path="/sill")
    top = ctx.st.el[host]["params"].get("top_profile")
    if top is not None and WT.under_problem(top, boxes=[_door_box(dp)]):
        raise DobimError("CONSTRAINT", "문이 호스트 벽의 상단 윤곽 위로 나간다", path="/height")


def _apply_top_level(ctx, p: dict, base_level: str) -> None:
    """상단 구속(24단계, 상용 도구의 Top Constraint): p 에 top_level 이 있으면 height 를 (상단 레벨 높이 + top_offset) - (바닥 레벨 높이 + base_offset) 로 맞춘다.
    여러 층에 걸친 벽을 상단 레벨로 정한다. 레벨 높이는 만든 뒤 바뀌지 않으므로(레벨 고치기 커맨드 없음) 값으로 저장한 height 가 정본이다."""
    tl = p.get("top_level")
    if tl is None:
        return
    lrow = ctx.st.el.get(tl)
    if lrow is None or lrow["kind"] != "level":
        raise DobimError("NOT_FOUND", f"상단 레벨이 없다: {tl}", path="/top_level")
    if "top_profile" in p:
        raise DobimError("CONSTRAINT", "상단 윤곽(top_profile)이 있는 벽은 상단 레벨로 구속할 수 없다", path="/top_level",
                         hint="wall.detach_top 으로 윤곽을 뗀 뒤 구속하세요")
    top = lrow["params"]["elevation"] + float(p.get("top_offset", 0.0))
    bottom = ctx.st.el[base_level]["params"]["elevation"] + WT.base(p)
    if top - bottom < 1.0:
        raise DobimError("CONSTRAINT", f"상단({top:g})이 벽 바닥({bottom:g})보다 1mm 이상 높아야 한다", path="/top_level")
    p["height"] = float(top - bottom)


class WallCreate(A):
    id: Id | None = None
    level: Id
    axis: Annotated[list[Num], Field(min_length=4, max_length=4, description="[x1,y1,x2,y2] mm")]
    thickness: Pos = 200.0
    height: Pos = 3000.0
    name: Name | None = None
    openings: Openings | None = Field(None, description="모양 개구부(아치 창·원창·출입구). IFC 는 모양 윤곽의 IfcOpeningElement + IfcRelVoidsElement")
    top_profile: TopProfile | None = Field(None, description="벽 상단 윤곽(경사·박공 벽): 생략하면 상단은 height 로 수평")
    base_offset: Num | None = Field(None, description="벽 바닥 오프셋(레벨 기준 mm, 상용 도구의 Base Offset). 음수면 레벨 아래에서 시작한다. height 는 바닥에서 잰다. "
                                                     "생략·0 이면 레벨에서 시작(키 없음)")
    top_level: Id | None = Field(None, description="상단 레벨(24단계, 상용 도구의 Top Constraint). 주면 height 를 상단 레벨 + top_offset 까지로 계산한다(height 와 함께 줄 수 없다). "
                                                    "여러 층에 걸친 벽의 평면 절단은 지나가는 각 층 평면에 그린다")
    top_offset: Num | None = Field(None, description="상단 레벨 기준 상단 오프셋 mm(top_level 과 함께). 생략·0 이면 키 없음")

    @model_validator(mode="after")
    def _top(self):
        if self.top_level is not None and "height" in self.model_fields_set:
            raise ValueError("height 와 top_level 은 함께 줄 수 없다(top_level 이면 높이를 계산한다)")
        if self.top_offset and self.top_level is None:
            raise ValueError("top_offset 은 top_level 과 함께 준다")
        return self


@command("wall.create", kind="mutation", risk="write", group="model.wall", summary="벽 생성",
         args=WallCreate, since="0.1",
         examples=[{"level": F.LV1, "axis": [0, 4000, 6000, 4000], "thickness": 200},
                   {"level": F.LV1, "axis": [0, 6000, 6000, 6000], "thickness": 600, "height": 6000,
                    "openings": [{"shape": "pointed", "offset": 1500, "sill": 900, "width": 900, "height": 3000, "fill": "glazing"},
                                 {"shape": "circle", "offset": 4000, "sill": 3600, "width": 1200, "height": 1200}]}])
def wall_create(ctx, a: WallCreate):
    _fresh_id(ctx, a, "wall")
    ctx.authored(a.level, "level", what="레벨에 벽을 추가하는 것")
    if _axis_len2(a.axis) < 1.0:
        raise DobimError("CONSTRAINT", "벽 길이는 1mm 이상이어야 한다", path="/axis")
    a.name = a.name or f"W-{a.id[-6:]}"
    p = {"name": a.name, "axis": list(a.axis), "thickness": a.thickness, "height": a.height}
    if a.openings:   # 개구부가 없으면 행은 이전과 같다
        p["openings"] = _openings_in(a.openings)
    if a.top_profile:   # 상단 윤곽이 없으면 행은 이전과 같다
        p["top_profile"] = WT.norm(a.top_profile)
    if a.base_offset:   # 바닥 오프셋(22단계): 0 이면 키가 없다(행·해시 이전과 같다)
        p["base_offset"] = float(a.base_offset)
    if a.top_level is not None:   # 상단 구속(24단계): 없으면 행은 이전과 같다
        p["top_level"] = a.top_level
        if a.top_offset:
            p["top_offset"] = float(a.top_offset)
        _apply_top_level(ctx, p, a.level)
    if a.openings or a.top_profile or a.base_offset:
        _wall_checked(p)
    ctx.put_el(a.id, row("wall", "IfcWall", "wall.basic", p))
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
    if "arc" in p:   # 곡선 벽(W2 ARC-04): 호 위의 점도 같이 옮긴다
        p["arc"] = [p["arc"][0] + a.dx, p["arc"][1] + a.dy]
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "axis": p["axis"]}


class WallSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    thickness: Pos | None = None
    height: Pos | None = None
    axis: Annotated[list[Num], Field(min_length=4, max_length=4)] | None = None
    openings: Openings | None = Field(None, description="모양 개구부 목록을 통째로 바꾼다(빈 목록 = 모두 뗀다)")
    top_profile: TopProfile | None = Field(None, description="벽 상단 윤곽을 통째로 바꾼다(빈 목록 = 뗀다)")
    base_offset: Num | None = Field(None, description="벽 바닥 오프셋(레벨 기준 mm). 0 이면 뗀다. 바닥만 움직이고 height 는 그대로다(상단도 함께 움직인다). "
                                                     "상단 레벨로 구속된 벽이면 상단은 그대로고 height 가 바뀐다")
    cuts: Cuts | None = Field(None, description="벽 잘라내기 상자 목록을 통째로 바꾼다(빈 목록 = 뗀다). 보통은 wall.cut_by 가 대상 부재에서 계산한다")
    top_level: Id | None = Field(None, description="상단 레벨로 구속한다(24단계). height 를 주면 구속을 뗀다")
    top_offset: Num | None = Field(None, description="상단 레벨 기준 상단 오프셋 mm(구속된 벽 또는 top_level 과 함께). 0 이면 키를 뗀다")

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.thickness, self.height, self.axis, self.openings, self.top_profile, self.base_offset, self.cuts,
                                   self.top_level, self.top_offset)):
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.height is not None and (self.top_level is not None or self.top_offset is not None):
            raise ValueError("height 와 top_level·top_offset 은 함께 줄 수 없다")
        return self


@command("wall.set", kind="mutation", risk="write", group="model.wall", summary="벽 속성 변경",
         args=WallSet, since="0.1", examples=[{"id": F.W1, "thickness": 250}])
def wall_set(ctx, a: WallSet):
    r = ctx.authored(a.id, "wall")
    p = dict(r["params"])
    if a.thickness is not None and "layers" in p and abs(a.thickness - p["thickness"]) > 1e-9:
        raise DobimError("CONSTRAINT", "층 구성이 있는 벽의 두께는 wall.layers 로 층을 바꿔 정한다", path="/thickness", hint="wall.layers 로 층 구성을 바꾸세요(층 두께 합 = 벽 두께)")
    for k in ("name", "thickness", "height"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    if a.axis is not None:
        no_arc(ctx, a.id, "wall.set 의 axis(직선 축)", path="/axis")
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
    if a.openings is not None:
        if a.openings:
            p["openings"] = _openings_in(a.openings)
        else:
            p.pop("openings", None)
    if a.top_profile is not None:
        if a.top_profile:
            p["top_profile"] = WT.norm(a.top_profile)
        else:
            p.pop("top_profile", None)
    if a.base_offset is not None:   # 바닥 오프셋(22단계): 0 이면 키를 뗀다
        if a.base_offset:
            p["base_offset"] = float(a.base_offset)
        else:
            p.pop("base_offset", None)
    if a.cuts is not None:   # 벽 잘라내기(23단계): 빈 목록이면 키를 뗀다
        if a.cuts:
            p["cuts"] = WC.norm(a.cuts)
        else:
            p.pop("cuts", None)
    if a.height is not None and "top_level" in p:   # 상단 구속(24단계): 높이를 직접 주면 구속을 뗀다(상용 도구의 Unconnected Height)
        p.pop("top_level")
        p.pop("top_offset", None)
        ctx.warn("height 를 직접 주어 상단 레벨 구속(top_level)을 뗐다")
    if a.top_level is not None:
        p["top_level"] = a.top_level
    if a.top_offset is not None:
        if "top_level" not in p:
            raise DobimError("CONSTRAINT", "상단 레벨로 구속되지 않은 벽이다(top_level 을 함께 주세요)", path="/top_offset")
        if a.top_offset:
            p["top_offset"] = float(a.top_offset)
        else:
            p.pop("top_offset", None)
    _apply_top_level(ctx, p, ctx.rels_out("on_level", a.id)[0])   # 구속된 벽은 바닥 오프셋·상단이 바뀌면 높이를 다시 계산한다
    if a.top_profile is None and "top_profile" in p and WT.profile_problem(p["top_profile"], sqrt(_axis_len2(p["axis"]))):
        raise DobimError("CONSTRAINT", "상단 윤곽이 있는 벽의 축 길이가 바뀌면 윤곽이 맞지 않는다", path="/axis",
                         hint="새 top_profile 을 함께 주거나, wall.detach_top 으로 뗀 뒤 wall.attach_top 으로 다시 붙이세요")
    if "openings" in p or "top_profile" in p or "base_offset" in p or "cuts" in p or "arc" in p:   # 축·높이가 바뀌어 개구부가 벽 밖으로 나가면 거절한다(곡선 벽은 두께가 반지름 안이어야 한다)
        _wall_checked(p)
    _hosted_under_top(ctx, a.id, p)
    if WT.base(p) > 0.0:   # 바닥 오프셋(22단계): 호스팅된 문의 문턱이 새 바닥 아래면 거절한다
        for d in sorted(ctx.rels_out("hosts", a.id)):
            if ctx.st.el[d]["kind"] == "door" and ctx.st.el[d]["params"]["sill"] < WT.base(p) - 1e-9:
                raise DobimError("CONSTRAINT", f"호스팅된 문의 문턱이 새 벽 바닥 아래다: {d}", path="/base_offset")
    from dobim_core.commands_family import check_hosted_fit
    check_hosted_fit(ctx, a.id, r["params"], p)
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


class LayerIn(A):
    material: Name = Field(description="층 재료 이름(예: 석고보드, 콘크리트)")
    thickness: Pos = Field(description="층 두께 mm")


Layers = Annotated[list[LayerIn], Field(min_length=1, max_length=8, description="층 구성(재료·두께 목록). 합이 요소 두께와 같아야 한다")]


class WallLayers(A):
    id: Id
    layers: Layers | None = Field(description="벽 층 구성: 벽 지역 +v 쪽 순서(축선의 왼쪽 면이 마지막 층)로 첫 층부터 준다. 층 두께의 합이 벽 두께가 된다. null 이면 층 구성을 뗀다(재료는 비게 된다)")


@command("wall.layers", kind="mutation", risk="write", group="model.wall", summary="벽 마감 층 구성(재료·두께 목록) 지정. IFC 는 IfcMaterialLayerSetUsage 로 나간다",
         args=WallLayers, since="0.3",
         examples=[{"id": F.W1, "layers": [{"material": "석고보드", "thickness": 20}, {"material": "콘크리트", "thickness": 160}, {"material": "치장벽돌", "thickness": 20}]}])
def wall_layers(ctx, a: WallLayers):
    r = ctx.authored(a.id, "wall", what="층 구성 지정")
    p = dict(r["params"])
    if a.layers is None:
        p.pop("layers", None)
    else:
        ls = [{"material": x.material, "thickness": x.thickness} for x in a.layers]
        ctx.set_material(a.id, None)   # 단일 재료가 있으면 먼저 뗀다(층 구성이 재료를 대신한다). 이미 층 구성이면 할 일이 없다
        p["layers"] = ls
        p["thickness"] = sum(x["thickness"] for x in ls)
        from dobim_core.commands_family import check_hosted_fit
        check_hosted_fit(ctx, a.id, r["params"], p)
    if p == r["params"]:
        return {"id": a.id, "changed": False}
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "changed": True, "thickness": p["thickness"]}


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
    for w, o in ((lo, hi), (hi, lo)):   # T 조인 표식(W1 1-F 3차)도 뗀다: 그 끝이 이 상대와의 조인 지점일 때만
        _tee_mark(ctx, w, o, False)
    return {"cleared": [lo, hi]}


def _tee_mark(ctx, stem, host, on: bool):
    """줄기 벽 stem 의 params.tee 에 host 와의 조인 지점 쪽 끝을 넣거나 뺀다. 넣을 끝을 못 찾으면 None, 아니면 끝 이름."""
    r = ctx.st.el[stem]
    sp, hp = r["params"], ctx.st.el[host]["params"]
    if "arc" in sp:   # 곡선 줄기(W2 ARC-04): 원과 몸통 축의 교점, 끝은 호 위 거리로 고른다
        pt = any_join_pt(stem, sp, host, hp)
        side = None if pt is None else any_tee_end(sp, list(K.WALL_TEE_ENDS), pt)
        if side is None:
            return None
    else:
        pt = _join_pt(stem, sp["axis"], host, hp["axis"]) if "arc" not in hp else _arc_hit(sp, hp)
        if pt is None:
            return None
        side = Q.tee_end_at(_frame_of(sp["axis"]), list(K.WALL_TEE_ENDS), pt)
    if side is None:
        return None
    cur = list(sp.get("tee", ()))
    new = sorted(set(cur) | {side}) if on else [x for x in cur if x != side]
    if new != cur:
        np_ = dict(sp)
        if new:
            np_["tee"] = new
        else:
            np_.pop("tee", None)
        ctx.put_el(stem, dict(r, params=np_))
    return side


class JoinTee(A):
    stem: Id = Field(description="끝이 몸통 벽 중간에 닿는 줄기 벽")
    host: Id = Field(description="몸통 벽(자르지 않는다)")


@command("join.tee", kind="mutation", risk="write", group="model.join",
         summary="T 조인: 줄기 벽 끝을 몸통 벽 면에서 멈추고 몸통은 자르지 않는다. 평면은 맞닿는 선을 지운다",
         args=JoinTee, since="0.13", examples=[{"stem": "1" + "0" * 20 + "5", "host": F.W1}])   # 줄기 벽은 parity 준비가 만든다(x=4500 세로 벽)
def join_tee(ctx, a: JoinTee):
    _, _, lo, hi = _join_pair(ctx, JoinArgs(a=a.stem, b=a.host))
    if ctx.rels_out("on_level", lo) != ctx.rels_out("on_level", hi):
        raise DobimError("CONSTRAINT", "다른 레벨의 벽은 조인할 수 없다", path="/host")
    sp, hp = ctx.st.el[a.stem]["params"], ctx.st.el[a.host]["params"]
    if "arc" in sp:   # 곡선 줄기(W2 ARC-04): 줄기의 원과 몸통 축(직선 또는 원)의 교점(두 벽 끝에 가까운 쪽)
        pt = any_join_pt(a.stem, sp, a.host, hp)
        if pt is None:
            raise DobimError("CONSTRAINT", "곡선 줄기 벽의 원이 몸통 벽 축과 만나지 않는다", path="/host")
        th, hl = any_u(hp, pt), wall_len(hp)
    elif "arc" in hp:   # 곡선 몸통(W2 ARC-04): 줄기 축선과 호의 원이 만나는 점(줄기 끝에 가까운 쪽)
        from dobim_core import arcgeom as AG
        pt = _arc_hit(sp, hp)
        if pt is None:
            raise DobimError("CONSTRAINT", "줄기 벽 축선이 곡선 몸통 벽의 원과 만나지 않는다", path="/host")
        ai = AG.info(hp["axis"], hp["arc"])
        th, hl = AG.u_of(ai, pt), AG.length(ai)
    else:
        pt = _join_pt(a.stem, sp["axis"], a.host, hp["axis"])
        if pt is None:
            raise DobimError("CONSTRAINT", "나란한 벽은 T 조인할 수 없다", path="/host")
        hx, hy, hc, hs, hl = _frame_of(hp["axis"])
        th = (pt[0] - hx) * hc + (pt[1] - hy) * hs
    if not -Q.JOIN_MAX_EXT <= th <= hl + Q.JOIN_MAX_EXT:
        raise DobimError("CONSTRAINT", "줄기 벽 축선이 몸통 벽 근처를 지나지 않는다", path="/host")
    side = _tee_mark(ctx, a.stem, a.host, True)
    if side is None:
        raise DobimError("CONSTRAINT", f"줄기 벽 끝이 몸통 벽 축선에서 {Q.JOIN_MAX_EXT:g}mm 안에 있지 않다", path="/stem")
    ctx.rel("joins", lo, hi, True)
    return {"stem": a.stem, "host": a.host, "end": side}


def _ends_touch(pa: dict, pb: dict, tol: float) -> bool:
    """두 벽의 끝점 중 맞닿은(거리 tol 안) 쌍이 있는가."""
    ea, eb = pa["axis"], pb["axis"]
    return any((ea[i] - eb[j]) ** 2 + (ea[i + 1] - eb[j + 1]) ** 2 <= tol * tol for i in (0, 2) for j in (0, 2))


class JoinAuto(A):
    level: Id = Field(description="이 레벨의 벽끼리만 본다")
    tol: Annotated[float, Field(ge=0, le=500, allow_inf_nan=False)] = Field(50.0, description="두 벽 축선 교점이 각 벽 끝에서 (상대 벽 두께/2 + tol) 안이면 L 조인으로 본다(mm)")
    tee: bool = Field(False, description="참이면 T 조인도 잇는다(한 벽 끝만 교점 가까이, 교점이 상대 벽 안: join.tee 와 같다)")


@command("join.auto", kind="mutation", risk="write", group="model.join",
         summary="레벨의 벽 모서리(L 조인)를 자동으로 잇는다: 두 벽 축선의 교점이 두 벽 모두의 끝 가까이에 있으면 join.set 과 같은 조인을 건다(T 조인·나란한 벽은 건드리지 않는다)",
         args=JoinAuto, since="0.6", examples=[{"level": F.LV1}])
def join_auto(ctx, a: JoinAuto):
    ctx.authored(a.level, "level", what="벽 자동 조인")
    walls = sorted(w for w in ctx.rels_in("on_level", a.level) if (r := ctx.st.el.get(w)) is not None and r["kind"] == "wall" and r["origin"] == "authored")
    info = {}
    for w in walls:
        x1, y1, x2, y2 = ctx.st.el[w]["params"]["axis"]
        ln = sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        info[w] = (x1, y1, (x2 - x1) / ln, (y2 - y1) / ln, ln, ctx.st.el[w]["params"]["thickness"])
    joined, tees = [], []
    for i, wa in enumerate(walls):
        xa, ya, ca, sa, la, ta = info[wa]
        for wb in walls[i + 1:]:
            xb, yb, cb, sb, lb, tb = info[wb]
            if "arc" in ctx.st.el[wa]["params"] or "arc" in ctx.st.el[wb]["params"]:   # 곡선 벽(W2 ARC-04): 두 벽의 끝점이 (두께/2 + tol) 안이면 L 조인
                if wb not in ctx.rels_out("joins", wa) and _ends_touch(ctx.st.el[wa]["params"], ctx.st.el[wb]["params"], max(ta, tb) / 2.0 + a.tol):
                    ctx.rel("joins", wa, wb, True)
                    joined.append([wa, wb])
                continue
            det = ca * sb - sa * cb
            if abs(det) < 0.05:   # 나란한 벽(3도 안)은 모서리가 아니다
                continue
            t = ((xb - xa) * sb - (yb - ya) * cb) / det
            u = ((xb - xa) * sa - (yb - ya) * ca) / det
            ea = min(abs(t), abs(t - la))
            eb = min(abs(u), abs(u - lb))
            if wb in ctx.rels_out("joins", wa):
                continue
            if ea <= tb / 2.0 + a.tol and eb <= ta / 2.0 + a.tol:
                ctx.rel("joins", wa, wb, True)
                joined.append([wa, wb])
            elif a.tee:   # T 조인(W1 1-F 3차): 한쪽 끝만 교점 가까이 있고 교점이 상대 벽 축선 안(양 끝에서 상대 두께/2 + tol 넘게 안쪽)
                for stem, host, e_s, pos, lh, t_s, t_h in ((wa, wb, ea, u, lb, ta, tb), (wb, wa, eb, t, la, tb, ta)):
                    if e_s <= t_h / 2.0 + a.tol and t_s / 2.0 + a.tol < pos < lh - t_s / 2.0 - a.tol:
                        if _tee_mark(ctx, stem, host, True) is not None:
                            ctx.rel("joins", wa, wb, True)
                            tees.append([stem, host])
                        break
    return {"joined": joined, "count": len(joined), **({"tees": tees} if a.tee else {})}


# ================================================================ 벽 분할 (S5: 안정 참조의 규칙을 시험하려고 코어에 더한 모델링 커맨드)
def _frame_of(axis):
    dx, dy = axis[2] - axis[0], axis[3] - axis[1]
    length = sqrt(dx * dx + dy * dy)
    return axis[0], axis[1], dx / length, dy / length, length


def _arc_hit(sp, hp):
    """직선 벽 sp 축선과 곡선 벽 hp 의 원이 만나는 점 중 직선 벽 끝에 가까운 것(queries._arc_join_point 와 같은 고르기 규칙). 없으면 None."""
    from dobim_core import arcgeom as AG
    x, y, c, s, _ = _frame_of(sp["axis"])
    cand = AG.line_hits(AG.info(hp["axis"], hp["arc"]), x, y, c, s)
    if not cand:
        return None
    ends = [(sp["axis"][0], sp["axis"][1]), (sp["axis"][2], sp["axis"][3]), (hp["axis"][0], hp["axis"][1]), (hp["axis"][2], hp["axis"][3])]
    return min(cand, key=lambda p: min((p[0] - e[0]) ** 2 + (p[1] - e[1]) ** 2 for e in ends[:2]) + min((p[0] - e[0]) ** 2 + (p[1] - e[1]) ** 2 for e in ends[2:]))


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


def _trim_raw(wid, axis, partners, tees=None):
    """queries.wall_trim 과 같은 규칙으로 (시작, 끝)을 원시 축선에서 계산한다. partners = [(상대 id, 상대 축선)], tees = {상대 id: 상대 params.tee}."""
    x, y, c, s, length = _frame_of(axis)
    start, end = 0.0, length
    for oid, oax in sorted(partners):
        p = _join_pt(wid, axis, oid, oax)
        if p is None:
            continue
        if tees and tees.get(oid) and Q.tee_end_at(_frame_of(oax), tees[oid], p):
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


# ---- 곡선 벽(W2 ARC-04)도 받는 원시 params 계산(queries 의 join_point·wall_trim·tee_end_at 과 같은 규칙). 직선 벽끼리는 위 함수 그대로다
def any_u(p: dict, pt) -> float:
    """세계 점 pt 의 벽 지역 u(직선 벽: 축선 투영, 곡선 벽: 호 위 거리)."""
    if "arc" in p:
        from dobim_core import arcgeom as AG
        return AG.u_of(AG.info(p["axis"], p["arc"]), pt)
    x, y, c, s, _ = _frame_of(p["axis"])
    return (pt[0] - x) * c + (pt[1] - y) * s


def any_join_pt(wa, pa: dict, wb, pb: dict):
    """두 벽(직선·곡선)의 조인 교점(queries.join_point 와 같다). 만나지 않으면 None."""
    if "arc" not in pa and "arc" not in pb:
        return _join_pt(wa, pa["axis"], wb, pb["axis"])
    from dobim_core import arcgeom as AG
    if wa > wb:
        wa, pa, wb, pb = wb, pb, wa, pa
    aa = AG.info(pa["axis"], pa["arc"]) if "arc" in pa else None
    ab = AG.info(pb["axis"], pb["arc"]) if "arc" in pb else None
    if aa is not None and ab is not None:
        cand = AG.circle_hits(aa, ab)
    else:
        arc, line = (aa, pb) if aa is not None else (ab, pa)
        x, y, c, s, _ = _frame_of(line["axis"])
        cand = AG.line_hits(arc, x, y, c, s)
    if not cand:
        return None

    def cost(q):
        return sum(min((q[0] - e[0]) ** 2 + (q[1] - e[1]) ** 2 for e in ((p["axis"][0], p["axis"][1]), (p["axis"][2], p["axis"][3]))) for p in (pa, pb))
    return min(cand, key=cost)


def any_tee_end(p: dict, tee, pt):
    """점 pt 가 벽 p 의 줄기 끝(tee)인가: "start"|"end"|None(queries.tee_end_at 과 같은 규칙, 곡선 벽은 호 위 거리)."""
    if not tee:
        return None
    if "arc" not in p:
        return Q.tee_end_at(_frame_of(p["axis"]), tee, pt)
    t, length = any_u(p, pt), wall_len(p)
    end = "start" if abs(t) <= abs(t - length) else "end"
    return end if end in tee and min(abs(t), abs(t - length)) <= Q.JOIN_MAX_EXT else None


def trim_any(wid, p: dict, partners) -> tuple:
    """queries.wall_trim 과 같은 규칙의 (시작, 끝). partners = [(상대 id, 상대 params)]. 곡선 벽도 받는다."""
    length = wall_len(p)
    start, end = 0.0, length
    for oid, op in sorted(partners, key=lambda x: x[0]):
        pt = any_join_pt(wid, p, oid, op)
        if pt is None or any_tee_end(op, op.get("tee"), pt):
            continue
        t = any_u(p, pt)
        if abs(t) <= abs(t - length):
            if abs(t) <= Q.JOIN_MAX_EXT:
                start = t
        elif abs(t - length) <= Q.JOIN_MAX_EXT:
            end = t
    if end - start < Q.MIN_TRIMMED:
        return (0.0, length)
    return (start, end)


def wall_void_ids(ctx, w: str) -> list:
    """벽 w 를 가리키는 독립 벽 개구(W2 세 번째 PR, ARC-34) id 목록."""
    return [o for o in sorted(ctx.st.by_kind.get("opening", ())) if ctx.st.el[o]["params"].get("mode") == "wall" and ctx.st.el[o]["params"]["host"] == w]


def move_wall_voids(ctx, w: str, fn, host=None) -> None:
    """벽 w 의 독립 벽 개구 offset 을 fn(offset) 으로 옮긴다(host 를 주면 그 벽으로 옮겨 단다). 길이 방향을 바꾸는 편집(분할·트림·대칭·간격띄우기)이 쓴다."""
    for o in wall_void_ids(ctx, w):
        r = ctx.st.el[o]
        q = dict(r["params"], offset=round(fn(r["params"]["offset"]), 6) + 0.0)
        if host is not None:
            q["host"] = host
        ctx.put_el(o, dict(r, params=q))


def _xcut_refs(ctx, wid: str) -> list:
    """이 벽을 also_cuts(25단계)로 가리키는 패밀리 인스턴스 id(이 벽에 호스팅된 것은 벽과 함께 지워지므로 뺀다)."""
    own = set(ctx.rels_out("hosts", wid))
    return [f for f in sorted(ctx.st.by_kind.get("finst", ())) if f not in own and wid in ctx.st.el[f]["params"].get("also_cuts", ())]


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
    xcut = _xcut_refs(ctx, a.id)
    if xcut:   # 함께 자르는 벽(25단계): 나누면 어느 조각을 자를지 모호하다
        raise DobimError("CONSTRAINT", f"패밀리 인스턴스 {len(xcut)}개가 이 벽을 함께 자른다(also_cuts): 나눌 수 없다", path="/id",
                         hint="family.set 으로 also_cuts 를 뗀 뒤 나누고 다시 지정하세요", details={"instances": xcut[:20]})
    x1, y1, x2, y2 = p["axis"]
    _, _, c, s, length = _frame_of(p["axis"])
    arc_w = arc_n = None
    if "arc" in p:   # 곡선 벽(W2 ARC-04): at 은 호 위 거리, 두 조각은 같은 원 위의 호(가운데 점을 다시 구한다)
        from dobim_core import arcgeom as AG
        ai = AG.info(p["axis"], p["arc"])
        length = AG.length(ai)
    if a.at < 1.0 or length - a.at < 1.0:
        raise DobimError("CONSTRAINT", f"분할 위치는 양쪽 조각이 1mm 이상 남아야 한다(벽 길이 {length:.1f}mm)", path="/at")
    if "arc" in p:
        sx, sy = (round(v, 6) + 0.0 for v in AG.point(ai, a.at))
        arc_w = [round(v, 6) + 0.0 for v in AG.point(ai, a.at / 2.0)]
        arc_n = [round(v, 6) + 0.0 for v in AG.point(ai, (a.at + length) / 2.0)]
        for ax_, mid_ in (([x1, y1, sx, sy], arc_w), ([sx, sy, x2, y2], arc_n)):
            why = AG.problem(ax_, mid_, p["thickness"])
            if why:
                raise DobimError("CONSTRAINT", f"나눈 조각이 곡선 벽이 되지 못한다: {why}", path="/at", hint="조각이 너무 짧다: 분할 위치를 옮기세요")
    nid = a.new_id = a.new_id or ctx.new_id("wall")    # 로그에 새 벽 id 를 명시해 재생이 같은 id 를 만들게 한다
    if nid in ctx.st.el:
        raise DobimError("CONSTRAINT", f"이미 있는 id 다: {nid}", path="/new_id")
    if _in_split_history(ctx, nid):   # 지워진 후계 id 로 다시 나누면 succ(X -> X) 순환이 생긴다
        raise DobimError("CONSTRAINT", f"벽 분할 이력에 쓰인 id 라 다시 쓸 수 없다: {nid}", path="/new_id",
                         hint="new_id 를 생략하거나 새 id 를 쓰세요")
    if arc_w is None:
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
    if arc_w is None and not any("arc" in ctx.st.el[o]["params"] for o in partners):   # 직선 벽끼리: 이전 계산 그대로
        pax = [(o, ctx.st.el[o]["params"]["axis"]) for o in partners]
        to_n, keep_w = [], []
        for o, oax in pax:
            pt = _join_pt(a.id, p["axis"], o, oax)
            t = None if pt is None else (pt[0] - x1) * c + (pt[1] - y1) * s
            (to_n if t is not None and t >= a.at else keep_w).append((o, oax))
        otee = {o: ctx.st.el[o]["params"].get("tee") for o in partners}
        b0, b1 = _trim_raw(a.id, p["axis"], pax, otee)
        w0, w1 = _trim_raw(a.id, ax_w, keep_w, otee)
        n0, n1 = _trim_raw(nid, ax_n, to_n, otee)
        tol = 1e-6
    else:   # 곡선 벽(W2 ARC-04)이 끼면 호 위 거리로 같은 검사를 한다(조각의 끝점은 소수 6자리라 허용 오차를 조금 넓힌다)
        pp = [(o, ctx.st.el[o]["params"]) for o in partners]
        to_n, keep_w = [], []
        for o, op in pp:
            pt = any_join_pt(a.id, p, o, op)
            t = None if pt is None else any_u(p, pt)
            (to_n if t is not None and t >= a.at else keep_w).append((o, op))
        pw_ = dict(p, axis=ax_w, **({"arc": arc_w} if arc_w else {}))
        pn_ = dict(p, axis=ax_n, **({"arc": arc_n} if arc_n else {}))
        b0, b1 = trim_any(a.id, p, pp)
        w0, w1 = trim_any(a.id, pw_, keep_w)
        n0, n1 = trim_any(nid, pn_, to_n)
        tol = 1e-4
        to_n = [(o, op["axis"]) for o, op in to_n]
    if abs(w0 - b0) > tol or abs(w1 - a.at) > tol or abs(n0) > tol or abs(n1 - (b1 - a.at)) > tol:
        raise DobimError("CONSTRAINT", "이 위치에서 나누면 조인 정리(trim)가 달라진다", path="/at",
                         hint="조인 지점에서 1000mm 넘게 떨어진 곳에서 나누세요")
    voids_n = []
    for o in wall_void_ids(ctx, a.id):   # 독립 벽 개구(W2 세 번째 PR, ARC-34)도 놓인 쪽 벽으로 간다. 가로지르면 거절한다
        vp = ctx.st.el[o]["params"]
        if vp["offset"] - vp["width"] / 2.0 >= a.at:
            voids_n.append(o)
        elif vp["offset"] + vp["width"] / 2.0 > a.at:
            raise DobimError("CONSTRAINT", f"분할 위치가 벽 개구를 가로지른다: {o}", path="/at")
    ops_w, ops_n = [], []
    for o in p.get("openings", ()):   # 모양 개구부(18단계)는 놓인 쪽 벽으로 간다. 가로지르면 거절한다
        h2 = o["width"] / 2.0
        if o["offset"] + h2 <= a.at:
            ops_w.append(dict(o))
        elif o["offset"] - h2 >= a.at:
            ops_n.append(dict(o, offset=o["offset"] - a.at))
        else:
            raise DobimError("CONSTRAINT", "분할 위치가 모양 개구부를 가로지른다", path="/at")
    name = (p["name"][:62] + "-b")
    pw = dict(p, axis=ax_w)
    if arc_w is not None:
        pw["arc"] = arc_w
    tee_n = "end" in p.get("tee", ())   # T 조인 줄기 끝(W1 1-F 3차): 시작 끝은 W, 끝 쪽 끝은 새 벽이 가진다
    if "tee" in p:
        if "start" in p["tee"]:
            pw["tee"] = ["start"]
        else:
            pw.pop("tee")
    top_n = None
    if "top_profile" in p:   # 상단 윤곽(21단계)은 분할점에서 둘로 나눈다(끝 쪽은 s 를 당긴다)
        pw["top_profile"], top_n = WT.split(p["top_profile"], a.at, length)
    if "openings" in p:
        if ops_w:
            pw["openings"] = ops_w
        else:
            pw.pop("openings")
    cuts_n = None
    if "cuts" in p:   # 벽 잘라내기(23단계): 상자를 분할점에서 나눠 놓인 쪽 벽으로(끝 쪽은 u 를 당긴다)
        cuts_w, cuts_n = WC.split(p["cuts"], a.at)
        if cuts_w:
            pw["cuts"] = cuts_w
        else:
            pw.pop("cuts")
    ctx.put_el(a.id, dict(r, params=pw))
    np_ = {"name": name, "axis": ax_n, "thickness": p["thickness"], "height": p["height"]}
    if arc_n is not None:   # 곡선 벽(W2 ARC-04): 끝 쪽 조각도 같은 원 위의 호
        np_["arc"] = arc_n
    if "base_offset" in p:   # 바닥 오프셋(22단계)은 끝 쪽 새 벽도 같다
        np_["base_offset"] = p["base_offset"]
    for k in ("top_level", "top_offset"):   # 상단 구속(24단계)도 끝 쪽 새 벽이 같다
        if k in p:
            np_[k] = p[k]
    if cuts_n:
        np_["cuts"] = cuts_n
    if "layers" in p:   # 층 구성이 있으면 끝 쪽 새 벽도 같은 층 구성(복사)
        np_["layers"] = [dict(x) for x in p["layers"]]
    if ops_n:
        np_["openings"] = ops_n
    if top_n is not None:
        np_["top_profile"] = top_n
    if tee_n:
        np_["tee"] = ["end"]
    ctx.put_el(nid, row("wall", "IfcWall", "wall.basic", np_))
    ctx.rel("on_level", nid, ctx.rels_out("on_level", a.id)[0], True)
    for o in voids_n:
        r_ = ctx.st.el[o]
        ctx.put_el(o, dict(r_, params=dict(r_["params"], host=nid, offset=r_["params"]["offset"] - a.at)))
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
    leaf: Pos | None = Field(None, description="문짝 두께 mm(1:50 평면 상세가 쓴다). 주지 않으면 규약값(40)")


def _fits(ctx, host, offset, width):
    length = wall_len(ctx.st.el[host]["params"])   # 곡선 벽은 호 길이
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
    dp = {"name": a.name, "width": a.width, "height": a.height, "sill": a.sill, "offset": a.offset}
    _door_under_top(ctx, a.host, dp)
    if a.leaf is not None:   # 입력이 없으면 파라미터에 키가 없다(저장 형식·해시 그대로)
        dp["leaf"] = a.leaf
    ctx.put_el(a.id, row("door", "IfcDoor", "door.single", dp))
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
    leaf: Pos | None = Field(None, description="문짝 두께 mm")
    clear_leaf: bool = Field(False, description="참이면 문짝 두께 입력을 지운다(규약값으로 돌아간다)")

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.width, self.height, self.sill, self.offset, self.leaf)) and not self.clear_leaf:
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.leaf is not None and self.clear_leaf:
            raise ValueError("leaf 와 clear_leaf 를 함께 줄 수 없다")
        return self


@command("door.set", kind="mutation", risk="write", group="model.door", summary="문 속성 변경",
         args=DoorSet, since="0.1", examples=[{"id": F.D1, "width": 1000}])
def door_set(ctx, a: DoorSet):
    r = ctx.authored(a.id, "door")
    p = dict(r["params"])
    for k in ("name", "width", "height", "sill", "offset", "leaf"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    if a.clear_leaf:
        p.pop("leaf", None)
    host = ctx.rels_in("hosts", a.id)[0]
    _fits(ctx, host, p["offset"], p["width"])
    _door_under_top(ctx, host, p)
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
    released = _release_railings(ctx, eid) if ctx.row(eid)["kind"] in ("stair", "slab") else []   # 이 원본이 낸 난간은 독립 난간으로 남는다
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
    return released


@command("element.delete", kind="mutation", risk="write", group="model", summary="요소 삭제(벽이면 문 포함)",
         args=IdArg, since="0.1", examples=[{"id": F.D2}])
def element_delete(ctx, a: IdArg):
    r = ctx.authored(a.id, what="삭제")
    gone = [a.id]
    if r["kind"] == "level":
        if ctx.rels_in("on_level", a.id):
            raise DobimError("CONSTRAINT", "벽이 있는 레벨은 지울 수 없다", path="/id")
        tied = [w for w in sorted(ctx.st.by_kind.get("wall", ())) if ctx.st.el[w]["params"].get("top_level") == a.id]
        if tied:   # 상단 구속(24단계): 이 레벨에 상단이 묶인 벽이 있다
            raise DobimError("CONSTRAINT", f"벽 {len(tied)}개의 상단이 이 레벨에 묶여 있어 지울 수 없다", path="/id",
                             hint="wall.set 으로 height 를 주어 구속을 떼세요", details={"walls": tied[:20]})
    elif r["kind"] == "ftype":
        if ctx.rels_in("of_type", a.id):
            raise DobimError("CONSTRAINT", "인스턴스가 있는 패밀리 타입은 지울 수 없다", path="/id", hint="인스턴스를 먼저 지우세요")
        ft = ctx.foreign_typed(a.id)
        if ft:   # 다른 도구가 이 타입에 붙인 요소: 타입이 사라지면 그 요소의 타입 지정이 조용히 사라진다(벽의 외래 개구부와 같은 경고)
            ctx.warn(f"이 타입에 타입 지정으로 붙은 외래 요소 {len(ft)}개의 타입 지정이 타입과 함께 사라진다(실행 취소로 되살아나지 않는다): {', '.join(ft[:3])}"
                     + (" 외" if len(ft) > 3 else ""))
    elif r["kind"] == "wall":
        xcut = _xcut_refs(ctx, a.id)
        if xcut:   # 함께 자르는 벽(25단계): 다른 벽의 창이 이 벽을 also_cuts 로 가리킨다
            raise DobimError("CONSTRAINT", f"패밀리 인스턴스 {len(xcut)}개가 이 벽을 함께 자른다(also_cuts)", path="/id",
                             hint="family.set 으로 그 인스턴스의 also_cuts 에서 이 벽을 빼세요", details={"instances": xcut[:20]})
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
    released = _delete_one(ctx, a.id)
    return {"deleted": gone, **({"railings_released": released} if released else {})}


# ---------------------------------------------------------------- 슬래브 (설계: spikes/phase1-drawings/elements-design.md 4절)
SlabCoord = Annotated[float, Field(allow_inf_nan=False, ge=-1e7, le=1e7)]
SlabPt = Annotated[list[SlabCoord], Field(min_length=2, max_length=2, description="[x,y] mm (±1e7)")]
SlabBoundary = Annotated[list[SlabPt], Field(min_length=3, max_length=256,
                                              description="경계 다각형 꼭짓점 [[x,y],...] mm(3~256개). 시계 방향으로 주면 반시계로 뒤집어 저장한다. 자기 교차 금지")]


def _slab_boundary(pts) -> list:
    """경계를 float 쌍 목록으로 정규화한다(반시계로 저장). 쓸 수 없는 모양이면 CONSTRAINT."""
    b = [[float(x), float(y)] for x, y in pts]
    why = polygon_problem(b)
    if why:
        raise DobimError("CONSTRAINT", f"슬래브 경계가 올바르지 않다: {why}", path="/boundary")
    return b if polygon_area2(b) > 0 else b[::-1]


SlabOpenings = Annotated[list[SlabBoundary], Field(min_length=1, max_length=32,
                                                   description="구멍(계단실·샤프트) 다각형 목록 [[[x,y],...], ...] mm. 각 구멍은 슬래브 경계 안쪽에 있고 서로 겹치지 않는다(최대 32개). 시계 방향으로 주면 반시계로 저장한다")]


def _slab_openings(pts_list) -> list:
    out = []
    for pts in pts_list:
        b = [[float(x), float(y)] for x, y in pts]
        why = polygon_problem(b)
        if why:
            raise DobimError("CONSTRAINT", f"슬래브 구멍이 올바르지 않다: {why}", path="/openings")
        out.append(b if polygon_area2(b) > 0 else b[::-1])
    return out


def _slab_checked(p: dict) -> dict:
    """슬래브 params 의 검사(구멍이 경계 안쪽·서로 겹치지 않음, 지붕의 물매·배수구 포함). 안 되면 CONSTRAINT."""
    why = K.row_params_error("slab", p)
    if why:
        raise DobimError("CONSTRAINT", f"슬래브가 올바르지 않다: {why}", path="/openings")
    return p


class SlabFall(A):
    """지붕 슬래브의 배수 물매(표기용: 슬래브 형상은 평평하다. 물매는 마감 모르타르 등으로 만든다)."""
    dir: Num = Field(description="물이 흐르는 방향 도(+x 가 0, 반시계). 지붕 평면에 화살표로 그린다")
    pct: Annotated[float, Field(gt=0, le=K.ROOF_FALL_PCT_MAX, allow_inf_nan=False)] = Field(description="물매 %(예: 2 = 1/50)")


SlabDrains = Annotated[list[SlabPt], Field(min_length=1, max_length=32, description="배수구(드레인) 위치 [[x,y],...] mm(최대 32). 슬래브 경계 안쪽·구멍 밖이어야 한다")]


class SlabParapet(A):
    """지붕 슬래브 둘레의 파라펫(슬래브 경계 안쪽으로 두께, 윗면 위로 높이)."""
    height: Annotated[float, Field(gt=0, le=K.PARAPET_H_MAX, allow_inf_nan=False)] = Field(description="슬래브 윗면 위로 올라온 높이 mm")
    thickness: Annotated[float, Field(gt=0, le=K.PARAPET_T_MAX, allow_inf_nan=False)] = Field(description="두께 mm(슬래브 경계에서 안쪽으로)")


def _slab_roof_params(p: dict) -> bool:
    return "use" in p or "fall" in p or "drains" in p or "parapet" in p


class SlabCreate(A):
    id: Id | None = None
    level: Id
    boundary: SlabBoundary
    thickness: Pos = 200.0
    top_offset: Num = Field(0.0, description="레벨 높이에서 윗면까지 mm (윗면 = 레벨 + top_offset, 두께는 아래로)")
    name: Name | None = None
    openings: SlabOpenings | None = Field(None, description="구멍(계단실·샤프트) 다각형 목록. 평면에 X 표시로 그리고 면적에서 뺀다. IFC 는 IfcOpeningElement + IfcRelVoidsElement 로 나간다")
    use: Literal["roof"] | None = Field(None, description="roof 이면 지붕 슬래브다(IFC PredefinedType ROOF). 지붕 평면 뷰가 윤곽·물매 화살표·배수구를 그린다")
    fall: SlabFall | None = Field(None, description="지붕 슬래브의 배수 물매(방향·%). use=roof 일 때만")
    drains: SlabDrains | None = Field(None, description="지붕 슬래브의 배수구 위치. use=roof 일 때만")
    parapet: SlabParapet | None = Field(None, description="지붕 슬래브 둘레의 파라펫(높이·두께). use=roof 일 때만. IFC 는 슬래브에 집합한 IfcWall(PARAPET)로 나간다")


@command("slab.create", kind="mutation", risk="write", group="model.slab", summary="슬래브 생성(레벨 + 경계 다각형 + 두께)",
         args=SlabCreate, since="0.2",
         examples=[{"level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200},
                   {"level": F.LV2, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "use": "roof", "fall": {"dir": 180, "pct": 2}, "drains": [[500, 2000]]}])
def slab_create(ctx, a: SlabCreate):
    _fresh_id(ctx, a, "slab")
    ctx.authored(a.level, "level", what="레벨에 슬래브를 추가하는 것")
    boundary = _slab_boundary(a.boundary)
    a.name = a.name or f"S-{a.id[-6:]}"
    sp = {"name": a.name, "boundary": boundary, "thickness": a.thickness, "top_offset": a.top_offset}
    if a.openings is not None:
        sp["openings"] = _slab_openings(a.openings)
    if a.use is not None:
        sp["use"] = a.use
    if a.fall is not None:
        sp["fall"] = {"dir": float(a.fall.dir), "pct": float(a.fall.pct)}
    if a.drains is not None:
        sp["drains"] = [[float(x), float(y)] for x, y in a.drains]
    if a.parapet is not None:
        sp["parapet"] = {"height": float(a.parapet.height), "thickness": float(a.parapet.thickness)}
    if "openings" in sp or _slab_roof_params(sp):
        _slab_checked(sp)
    ctx.put_el(a.id, row("slab", "IfcSlab", "slab.basic", sp))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id}


class SlabSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    boundary: SlabBoundary | None = None
    thickness: Pos | None = None
    top_offset: Num | None = None
    openings: SlabOpenings | None = Field(None, description="구멍 목록을 이 값으로 바꾼다(전체 교체)")
    clear_openings: bool = Field(False, description="참이면 구멍을 모두 지운다")
    use: Literal["floor", "roof"] | None = Field(None, description="roof = 지붕 슬래브로 바꾼다, floor = 일반 슬래브로 되돌린다(물매·배수구도 함께 지운다)")
    fall: SlabFall | None = Field(None, description="지붕 슬래브의 배수 물매를 이 값으로 바꾼다")
    clear_fall: bool = Field(False, description="참이면 배수 물매를 지운다")
    drains: SlabDrains | None = Field(None, description="배수구 목록을 이 값으로 바꾼다(전체 교체)")
    clear_drains: bool = Field(False, description="참이면 배수구를 모두 지운다")
    parapet: SlabParapet | None = Field(None, description="파라펫(높이·두께)을 이 값으로 바꾼다. 지붕 슬래브(use=roof)만")
    clear_parapet: bool = Field(False, description="참이면 파라펫을 지운다")

    @model_validator(mode="after")
    def _any(self):
        if (all(v is None for v in (self.name, self.boundary, self.thickness, self.top_offset, self.openings, self.use, self.fall, self.drains, self.parapet))
                and not (self.clear_openings or self.clear_fall or self.clear_drains or self.clear_parapet)):
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.parapet is not None and self.clear_parapet:
            raise ValueError("parapet 과 clear_parapet 을 함께 줄 수 없다")
        if self.openings is not None and self.clear_openings:
            raise ValueError("openings 와 clear_openings 를 함께 줄 수 없다")
        if (self.fall is not None and self.clear_fall) or (self.drains is not None and self.clear_drains):
            raise ValueError("fall·drains 와 그 clear_ 를 함께 줄 수 없다")
        return self


@command("slab.set", kind="mutation", risk="write", group="model.slab", summary="슬래브 속성 변경(경계·두께·윗면 오프셋·이름)",
         args=SlabSet, since="0.2", examples=[{"id": F.S1, "thickness": 250}])
def slab_set(ctx, a: SlabSet):
    r = ctx.authored(a.id, "slab")
    p = dict(r["params"])
    if a.thickness is not None and "layers" in p and abs(a.thickness - p["thickness"]) > 1e-9:
        raise DobimError("CONSTRAINT", "층 구성이 있는 슬래브의 두께는 slab.layers 로 층을 바꿔 정한다", path="/thickness", hint="slab.layers 로 층 구성을 바꾸세요(층 두께 합 = 슬래브 두께)")
    for k in ("name", "thickness", "top_offset"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    if a.boundary is not None:
        p["boundary"] = _slab_boundary(a.boundary)
    if a.openings is not None:
        p["openings"] = _slab_openings(a.openings)
    if a.clear_openings:
        p.pop("openings", None)
    if a.use == "floor":   # 일반 슬래브로 되돌리면 지붕 전용 입력도 함께 간다
        for k in ("use", "fall", "drains", "parapet"):
            p.pop(k, None)
    elif a.use == "roof":
        p["use"] = "roof"
    if a.fall is not None:
        p["fall"] = {"dir": float(a.fall.dir), "pct": float(a.fall.pct)}
    if a.clear_fall:
        p.pop("fall", None)
    if a.drains is not None:
        p["drains"] = [[float(x), float(y)] for x, y in a.drains]
    if a.clear_drains:
        p.pop("drains", None)
    if a.parapet is not None:
        p["parapet"] = {"height": float(a.parapet.height), "thickness": float(a.parapet.thickness)}
    if a.clear_parapet:
        p.pop("parapet", None)
    if "openings" in p or _slab_roof_params(p):
        _slab_checked(p)
    rail = _follow_railings(ctx, a.id, r["params"], p)   # 이 슬래브가 낸 난간이 따라간다
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, **({"railings": rail} if rail else {})}


class SlabLayers(A):
    id: Id
    layers: Layers | None = Field(description="슬래브 층 구성: 윗면에서 아래로 첫 층부터 준다(바닥 마감, 모르타르, 콘크리트 ...). 층 두께의 합이 슬래브 두께가 된다. null 이면 층 구성을 뗀다")


@command("slab.layers", kind="mutation", risk="write", group="model.slab", summary="슬래브 두께 층 구성(재료·두께 목록) 지정. IFC 는 IfcMaterialLayerSetUsage 로 나간다",
         args=SlabLayers, since="0.3",
         examples=[{"id": F.S1, "layers": [{"material": "마감 모르타르", "thickness": 50}, {"material": "철근콘크리트", "thickness": 150}]}])
def slab_layers(ctx, a: SlabLayers):
    r = ctx.authored(a.id, "slab", what="층 구성 지정")
    p = dict(r["params"])
    if a.layers is None:
        p.pop("layers", None)
    else:
        ls = [{"material": x.material, "thickness": x.thickness} for x in a.layers]
        ctx.set_material(a.id, None)
        p["layers"] = ls
        p["thickness"] = sum(x["thickness"] for x in ls)
    if p == r["params"]:
        return {"id": a.id, "changed": False}
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "changed": True, "thickness": p["thickness"]}


class SlabMove(A):
    id: Id
    dx: Num = 0.0
    dy: Num = 0.0


@command("slab.move", kind="mutation", risk="write", group="model.slab", summary="슬래브를 평행 이동한다",
         args=SlabMove, since="0.2", examples=[{"id": F.S1, "dx": 600, "dy": 0}])
def slab_move(ctx, a: SlabMove):
    r = ctx.authored(a.id, "slab")
    b = [[x + a.dx, y + a.dy] for x, y in r["params"]["boundary"]]
    p = dict(r["params"], boundary=b)
    if "openings" in p:   # 구멍도 함께 옮긴다
        p["openings"] = [[[x + a.dx, y + a.dy] for x, y in o] for o in p["openings"]]
    if "drains" in p:   # 배수구도 함께 옮긴다
        p["drains"] = [[x + a.dx, y + a.dy] for x, y in p["drains"]]
    rail = _follow_railings(ctx, a.id, r["params"], p)
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "boundary": b, **({"railings": rail} if rail else {})}


@command("slab.delete", kind="mutation", risk="write", group="model.slab", summary="슬래브 삭제",
         args=IdArg, since="0.2", examples=[{"id": F.S1}])
def slab_delete(ctx, a: IdArg):
    ctx.authored(a.id, "slab", what="삭제")
    released = _delete_one(ctx, a.id)
    return {"deleted": [a.id], **({"railings_released": released} if released else {})}


# ---------------------------------------------------------------- 계단 (설계: spikes/phase1-drawings/elements-design.md 6절)
StairRun = Annotated[list[SlabCoord], Field(min_length=4, max_length=4, description="걸음선 [x1,y1,x2,y2] mm(±1e7). 아래 첫 챌판에서 위 끝(위층 바닥 가장자리)까지, 길이 1mm 이상")]
StairRisers = Annotated[int, Field(ge=2, le=100, description="챌판 수(디딤판은 하나 적다)")]


def _stair_run(run) -> list:
    r = [float(v) for v in run]
    if _axis_len2(r) < 1.0:
        raise DobimError("CONSTRAINT", "계단 걸음선 길이는 1mm 이상이어야 한다", path="/run")
    return r


class StairTurn(A):
    """꺾인 계단(층참 있는 L형·U형) 입력. 걸음선(run)은 층참 아래 단 묶음의 걸음선이다."""
    type: Literal["L", "U"] = Field(description="L = 층참에서 90도 꺾임, U = 층참에서 180도 되돌아 오름")
    side: Literal["left", "right"] = Field("left", description="꺾는 쪽(걸음선 진행 방향 기준)")
    risers_a: int = Field(ge=2, le=100, description="층참 아래 단 묶음의 챌판 수(2 이상, 전체 챌판 수 - 2 이하). 층참 윗면은 이 챌판 수만큼 올라온 높이다")
    landing: Pos | None = Field(None, description="층참 깊이 mm(걸음선 방향). 비우면 계단 폭. 폭보다 작을 수 없다")
    gap: Num | None = Field(None, ge=0, description="U형에서 두 단 묶음 사이 간격 mm(비우면 100). L형은 쓰지 않는다")
    winders: int | None = Field(None, ge=K.STAIR_WINDERS_MIN, le=K.STAIR_WINDERS_MAX,
                                description="돌음(와인더) 계단: 층참 대신 회전부를 이 수의 쐐기 디딤판으로 채운다(회전 중심에서 같은 각 분할). "
                                            "위 단 묶음 챌판 = 전체 - risers_a - winders + 1. L형은 회전부가 폭 x 폭(landing 비움)")


def _stair_turn(t) -> dict:
    out = {"type": t.type, "side": t.side, "risers_a": t.risers_a, "landing": None if t.landing is None else float(t.landing),
           "gap": (K.STAIR_GAP_DEFAULT if t.gap is None else float(t.gap)) if t.type == "U" else 0.0}
    if t.winders is not None:   # 없으면 키 자체가 없다(기존 계단 바이트 불변)
        out["winders"] = t.winders
    return out


def _stair_checked(p: dict) -> dict:
    """계단 params 의 검사(챌판 분배·층참 깊이 포함). 안 되면 CONSTRAINT."""
    why = K.row_params_error("stair", p)
    if why:
        raise DobimError("CONSTRAINT", f"계단이 올바르지 않다: {why}", path="/turn")
    return p


class StairCreate(A):
    id: Id | None = None
    level: Id
    run: StairRun
    width: Pos = 1200.0
    height: Pos = Field(3000.0, gt=0, allow_inf_nan=False, description="전체 오름 mm(아래 끝에서 위층 바닥까지)")
    risers: StairRisers = 18
    base_offset: Num = Field(0.0, description="레벨 높이에서 아래 끝까지 mm")
    name: Name | None = None
    turn: StairTurn | None = Field(None, description="꺾인 계단(층참 있는 L형·U형). 주면 run 은 층참 아래 단 묶음의 걸음선이다. IFC 는 IfcStair 아래 IfcStairFlight 둘과 층참 IfcSlab 으로 나간다")


@command("stair.create", kind="mutation", risk="write", group="model.stair", summary="계단 생성(레벨 + 걸음선 + 폭 + 오름 + 챌판 수, 선택: 꺾인 계단 turn)",
         args=StairCreate, since="0.2",
         examples=[{"level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18}])
def stair_create(ctx, a: StairCreate):
    _fresh_id(ctx, a, "stair")
    ctx.authored(a.level, "level", what="레벨에 계단을 추가하는 것")
    run = _stair_run(a.run)
    a.name = a.name or f"ST-{a.id[-6:]}"
    p = {"name": a.name, "run": run, "width": a.width, "height": a.height, "risers": a.risers, "base_offset": a.base_offset}
    if a.turn is not None:
        p["turn"] = _stair_turn(a.turn)
        _stair_checked(p)
    ctx.put_el(a.id, row("stair", "IfcStair", "stair.straight", p))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id}


class StairSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    run: StairRun | None = None
    width: Pos | None = None
    height: Pos | None = None
    risers: StairRisers | None = None
    base_offset: Num | None = None
    turn: StairTurn | None = Field(None, description="꺾임(층참)을 주거나 바꾼다")
    clear_turn: bool = Field(False, description="참이면 꺾임을 지워 직선 계단으로 되돌린다")

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.run, self.width, self.height, self.risers, self.base_offset, self.turn)) and not self.clear_turn:
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.turn is not None and self.clear_turn:
            raise ValueError("turn 과 clear_turn 을 함께 줄 수 없다")
        return self


@command("stair.set", kind="mutation", risk="write", group="model.stair", summary="계단 속성 변경(걸음선·폭·오름·챌판 수·오프셋·이름·꺾임)",
         args=StairSet, since="0.2", examples=[{"id": F.ST1, "risers": 17}])
def stair_set(ctx, a: StairSet):
    r = ctx.authored(a.id, "stair")
    p = dict(r["params"])
    for k in ("name", "width", "height", "risers", "base_offset"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    if a.run is not None:
        p["run"] = _stair_run(a.run)
    if a.turn is not None:
        p["turn"] = _stair_turn(a.turn)
    if a.clear_turn:
        p.pop("turn", None)
    if "turn" in p:
        _stair_checked(p)
    rail = _follow_railings(ctx, a.id, r["params"], p)   # 이 계단이 낸 난간이 따라간다
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, **({"railings": rail} if rail else {})}


@command("stair.move", kind="mutation", risk="write", group="model.stair", summary="계단을 평행 이동한다",
         args=SlabMove, since="0.2", examples=[{"id": F.ST1, "dx": 600, "dy": 0}])
def stair_move(ctx, a: SlabMove):
    r = ctx.authored(a.id, "stair")
    x1, y1, x2, y2 = r["params"]["run"]
    run = [x1 + a.dx, y1 + a.dy, x2 + a.dx, y2 + a.dy]
    new = dict(r["params"], run=run)
    rail = _follow_railings(ctx, a.id, r["params"], new)
    ctx.put_el(a.id, dict(r, params=new))
    return {"id": a.id, "run": run, **({"railings": rail} if rail else {})}


@command("stair.delete", kind="mutation", risk="write", group="model.stair", summary="계단 삭제",
         args=IdArg, since="0.2", examples=[{"id": F.ST1}])
def stair_delete(ctx, a: IdArg):
    ctx.authored(a.id, "stair", what="삭제")
    released = _delete_one(ctx, a.id)
    return {"deleted": [a.id], **({"railings_released": released} if released else {})}


# ---------------------------------------------------------------- 공간 (설계: elements-design.md 7절)
SpaceHoles = Annotated[list[SlabBoundary], Field(min_length=1, max_length=32,
                                                 description="섬 구멍(방 안의 따로 떨어진 닫힌 벽 고리·기둥) 다각형 목록 [[[x,y],...], ...] mm. 각 구멍은 공간 경계 안쪽에 있고 서로 겹치지 않는다(최대 32개). 면적에서 뺀다")]


def _space_holes(pts_list) -> list:
    out = []
    for pts in pts_list:
        b = [[float(x), float(y)] for x, y in pts]
        why = polygon_problem(b)
        if why:
            raise DobimError("CONSTRAINT", f"공간 구멍이 올바르지 않다: {why}", path="/holes")
        out.append(b if polygon_area2(b) > 0 else b[::-1])
    return out


def _space_checked(p: dict) -> dict:
    why = K.row_params_error("space", p)
    if why:
        raise DobimError("CONSTRAINT", f"공간이 올바르지 않다: {why}", path="/holes")
    return p


class SpaceCreate(A):
    id: Id | None = None
    level: Id
    boundary: SlabBoundary
    name: Name | None = None
    category: Text = Field("", description="분류(예: 복도, 사무실). 비어 있어도 된다. 복도 판정에 쓴다")
    height: Pos = 2700.0
    ceiling_height: Pos | None = Field(None, description="천장(반자) 마감 높이 mm(레벨 위). 공간 높이 이하. 주면 1:50 단면 상세가 천장선을 긋는다")
    holes: SpaceHoles | None = Field(None, description="섬 구멍(방 안의 기둥·닫힌 내부 벽 고리). 면적에서 빼고 IFC 몸체도 구멍 있는 프로필로 나간다")


@command("space.create", kind="mutation", risk="write", group="model.space", summary="공간(방) 생성(레벨 + 경계 다각형 + 실명·분류)",
         args=SpaceCreate, since="0.2",
         examples=[{"level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "name": "사무실", "category": "사무실"}])
def space_create(ctx, a: SpaceCreate):
    _fresh_id(ctx, a, "space")
    ctx.authored(a.level, "level", what="레벨에 공간을 추가하는 것")
    boundary = _slab_boundary(a.boundary)
    a.name = a.name or f"SP-{a.id[-6:]}"
    sp = {"name": a.name, "category": a.category, "boundary": boundary, "height": a.height}
    if a.ceiling_height is not None:
        if a.ceiling_height > a.height:
            raise DobimError("CONSTRAINT", "천장 높이가 공간 높이보다 크다", path="/ceiling_height")
        sp["ceiling_height"] = a.ceiling_height
    if a.holes is not None:
        sp["holes"] = _space_holes(a.holes)
        _space_checked(sp)
    ctx.put_el(a.id, row("space", "IfcSpace", "space.basic", sp))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id}


class SpaceSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    category: Text | None = None
    boundary: SlabBoundary | None = None
    height: Pos | None = None
    ceiling_height: Pos | None = Field(None, description="천장(반자) 마감 높이 mm")
    clear_ceiling: bool = Field(False, description="참이면 천장 높이 입력을 지운다")
    holes: SpaceHoles | None = Field(None, description="섬 구멍 목록을 이 값으로 바꾼다(전체 교체)")
    clear_holes: bool = Field(False, description="참이면 섬 구멍을 모두 지운다")

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.category, self.boundary, self.height, self.ceiling_height, self.holes)) and not (self.clear_ceiling or self.clear_holes):
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.ceiling_height is not None and self.clear_ceiling:
            raise ValueError("ceiling_height 와 clear_ceiling 을 함께 줄 수 없다")
        if self.holes is not None and self.clear_holes:
            raise ValueError("holes 와 clear_holes 를 함께 줄 수 없다")
        return self


@command("space.set", kind="mutation", risk="write", group="model.space", summary="공간 속성 변경(실명·분류·경계·높이)",
         args=SpaceSet, since="0.2", examples=[{"id": F.SP1, "category": "복도"}])
def space_set(ctx, a: SpaceSet):
    r = ctx.authored(a.id, "space")
    p = dict(r["params"])
    for k in ("name", "category", "height", "ceiling_height"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    if a.clear_ceiling:
        p.pop("ceiling_height", None)
    if "ceiling_height" in p and p["ceiling_height"] > p["height"]:
        raise DobimError("CONSTRAINT", "천장 높이가 공간 높이보다 크다", path="/ceiling_height")
    if a.boundary is not None:
        p["boundary"] = _slab_boundary(a.boundary)
    if a.holes is not None:
        p["holes"] = _space_holes(a.holes)
    if a.clear_holes:
        p.pop("holes", None)
    if "holes" in p:
        _space_checked(p)
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


@command("space.move", kind="mutation", risk="write", group="model.space", summary="공간을 평행 이동한다",
         args=SlabMove, since="0.2", examples=[{"id": F.SP1, "dx": 600, "dy": 0}])
def space_move(ctx, a: SlabMove):
    r = ctx.authored(a.id, "space")
    b = [[x + a.dx, y + a.dy] for x, y in r["params"]["boundary"]]
    p = dict(r["params"], boundary=b)
    if "holes" in p:   # 섬 구멍도 함께 옮긴다
        p["holes"] = [[[x + a.dx, y + a.dy] for x, y in o] for o in p["holes"]]
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "boundary": b}


@command("space.delete", kind="mutation", risk="write", group="model.space", summary="공간 삭제",
         args=IdArg, since="0.2", examples=[{"id": F.SP1}])
def space_delete(ctx, a: IdArg):
    ctx.authored(a.id, "space", what="삭제")
    _delete_one(ctx, a.id)
    return {"deleted": [a.id]}


# ---------------------------------------------------------------- 난간 (설계: elements-design.md 12절)
RailingPath = Annotated[list[SlabPt], Field(min_length=2, max_length=256, description="난간선 꼭짓점 [[x,y],...] mm(열린 선, 2~256개, 이웃 꼭짓점은 1mm 이상 떨어져야 한다). 닫으려면 첫 점을 끝에 다시 준다")]


RAIL_SRC = ("src", "src_tag", "src_inset")   # 난간의 출처 표식(원본 계단·슬래브 id, 난간 자리 표식, 계단 옆 들임). 없으면 키 자체가 없다


def _railing_row(name, path, height, base_offset, rise, src=None) -> dict:
    p = {"name": name, "path": [[float(x), float(y)] for x, y in path], "height": float(height), "base_offset": float(base_offset), "rise": float(rise)}
    if src:
        p.update(src)
    why = K.row_params_error("railing", p)
    if why:
        raise DobimError("CONSTRAINT", f"난간이 올바르지 않다: {why}", path="/path")
    return p


class RailingCreate(A):
    id: Id | None = None
    level: Id
    path: RailingPath
    height: Pos = Field(K.RAILING_HEIGHT_DEFAULT, description="난간 높이 mm(바닥 마감에서 손잡이 윗면까지)")
    base_offset: Num = Field(0.0, description="레벨 높이에서 난간선 시작점의 바닥 마감까지 mm")
    rise: Num = Field(0.0, description="난간선 끝에서 바닥 마감이 시작점보다 더 높은 만큼 mm(계단 옆 경사 난간이면 오름, 평평하면 0). 선 길이에 비례해 오른다")
    name: Name | None = None


@command("railing.create", kind="mutation", risk="write", group="model.railing", summary="난간 생성(레벨 + 난간선 + 높이, 선택: 경사 rise). IFC 는 IfcRailing 으로 나간다",
         args=RailingCreate, since="0.3", examples=[{"level": F.LV1, "path": [[0, 0], [3000, 0]], "height": 1100}])
def railing_create(ctx, a: RailingCreate):
    _fresh_id(ctx, a, "railing")
    ctx.authored(a.level, "level", what="레벨에 난간을 추가하는 것")
    a.name = a.name or f"RL-{a.id[-6:]}"
    ctx.put_el(a.id, row("railing", "IfcRailing", "railing.basic", _railing_row(a.name, a.path, a.height, a.base_offset, a.rise)))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id}


class RailingSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    path: RailingPath | None = None
    height: Pos | None = None
    base_offset: Num | None = None
    rise: Num | None = None
    clear_source: bool = Field(False, description="참이면 난간의 출처(계단·슬래브) 연동을 끊는다: 이후 원본을 바꿔도 따라가지 않는다")

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.path, self.height, self.base_offset, self.rise)) and not self.clear_source:
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("railing.set", kind="mutation", risk="write", group="model.railing", summary="난간 속성 변경(난간선·높이·시작 높이·rise·이름, 출처 연동 끊기)",
         args=RailingSet, since="0.3", examples=[{"id": F.RL1, "height": 1200}])
def railing_set(ctx, a: RailingSet):
    r = ctx.authored(a.id, "railing")
    p = dict(r["params"])
    for k in ("name", "height", "base_offset", "rise"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    if a.path is not None:
        p["path"] = a.path
    src = None if a.clear_source else {k: p[k] for k in RAIL_SRC if k in p}   # 출처 표식은 사용자가 끊기 전까지 그대로 둔다
    ctx.put_el(a.id, dict(r, params=_railing_row(p["name"], p["path"], p["height"], p["base_offset"], p["rise"], src)))
    return {"id": a.id}


@command("railing.move", kind="mutation", risk="write", group="model.railing", summary="난간을 평행 이동한다",
         args=SlabMove, since="0.3", examples=[{"id": F.RL1, "dx": 600, "dy": 0}])
def railing_move(ctx, a: SlabMove):
    r = ctx.authored(a.id, "railing")
    path = [[x + a.dx, y + a.dy] for x, y in r["params"]["path"]]
    ctx.put_el(a.id, dict(r, params=dict(r["params"], path=path)))
    return {"id": a.id, "path": path}


@command("railing.delete", kind="mutation", risk="write", group="model.railing", summary="난간 삭제",
         args=IdArg, since="0.3", examples=[{"id": F.RL1}])
def railing_delete(ctx, a: IdArg):
    ctx.authored(a.id, "railing", what="삭제")
    _delete_one(ctx, a.id)
    return {"deleted": [a.id]}


def _new_railing(ctx, level, name_stem, path, height, base_offset, rise, src=None, rid: str | None = None) -> str:
    rid = rid or ctx.new_id("railing")
    ctx.put_el(rid, row("railing", "IfcRailing", "railing.basic", _railing_row(f"{name_stem}-{rid[-6:]}", path, height, base_offset, rise, src)))
    ctx.rel("on_level", rid, level, True)
    return rid


# ---- 난간 연동(15단계): railing.from_stair·from_slab 이 만든 난간은 원본(계단·슬래브)을 따라간다
# 출처는 난간 쪽에 적는다: src(원본 id)·src_tag(어느 난간 자리인지)·src_inset(계단 옆 들임). 난간 선은 원본 파라미터의 순수 함수(_stair_rail_specs·_slab_rail_spec)라
# 원본이 바뀌면 `옛 원본에서 만든 난간선` 과 지금 난간선이 같은지로 사용자가 고쳤는지를 가린다(같으면 새 원본의 난간선으로 바꾸고, 다르면 건드리지 않고 lost).
RAIL_TOL = 1e-3   # 난간선 비교 허용 mm


def _stair_rail_specs(p: dict, side: str, inset: float, landing: bool) -> list:
    """계단 파라미터에서 난간 자리들: [{"tag", "path", "base_offset", "rise"}]. 단 묶음마다 왼쪽(`f{i}L`)·오른쪽(`f{i}R`), 층참은 `landing`, 돌음 회전부 바깥 둘레는 `winder`. 순서는 생성 순서다."""
    lay = K.stair_layout(p)
    w, base = p["width"], p["base_offset"]
    rise = lay["rise"]
    inset = min(inset, w / 2.0 - 1.0)
    out = []
    for i, fl in enumerate(lay["flights"]):
        x1, y1, x2, y2 = fl["run"]
        ln = fl["length"]
        ux, uy = (x2 - x1) / ln, (y2 - y1) / ln
        for sgn, name in ((1.0, "left"), (-1.0, "right")):
            if side not in ("both", name):
                continue
            m = sgn * (w / 2.0 - inset)
            path = [[x1 - uy * m, y1 + ux * m], [x2 - uy * m, y2 + ux * m]]
            out.append({"tag": f"f{i}{'L' if sgn > 0 else 'R'}", "path": path, "base_offset": base + fl["z0"] + rise, "rise": (fl["n"] - 1) * rise})
    ld = lay["landing"]
    if ld is not None and landing:
        t = p["turn"]
        poly = ld["poly"]          # (층참 가까운 변 시작, 먼 변 시작, 먼 변 끝, 가까운 변 끝) 이 걸음선 왼쪽 + 기준 (t,m) 좌표의 네 꼭짓점
        p0, p1, p2, p3 = (list(q) for q in poly)   # (가까운 변 시작, 먼 변 시작, 먼 변 끝, 가까운 변 끝): 걸음선 방향 t 축과 왼쪽 m 축의 직사각형

        def unit(u, v):
            d = ((v[0] - u[0]) ** 2 + (v[1] - u[1]) ** 2) ** 0.5
            return ((v[0] - u[0]) / d, (v[1] - u[1]) / d)
        ta, mb = unit(p0, p1), unit(p0, p3)

        def mv(q, st, sm):
            return [q[0] + ta[0] * st * inset + mb[0] * sm * inset, q[1] + ta[1] * st * inset + mb[1] * sm * inset]
        i0, i1, i2, i3 = mv(p0, 1, 1), mv(p1, -1, 1), mv(p2, -1, -1), mv(p3, 1, -1)   # 각 꼭짓점을 안쪽으로 inset 만큼
        if t["type"] == "U":
            path = [i0, i1, i2, i3]                                    # 먼 변과 양 옆 세 변
        elif t["side"] == "left":
            path = [i0, i1, i2]                                        # 오른쪽 옆변 + 먼 변(왼쪽 옆변은 위 단 묶음이 떠난다)
        else:
            path = [i3, i2, i1]                                        # 왼쪽 옆변 + 먼 변
        out.append({"tag": "landing", "path": path, "base_offset": base + ld["z"], "rise": 0.0})
    wd = lay.get("winders")
    if wd is not None and landing and side in ("both", "right" if p["turn"]["side"] == "left" else "left"):   # 돌음 회전부의 바깥 둘레(23.3): 아래 단 묶음 바깥 난간 끝에서 위 단 묶음 바깥 난간 시작까지, 쐐기 수만큼 오른다
        t = p["turn"]
        x1, y1, x2, y2 = p["run"]
        ln = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        ux, uy = (x2 - x1) / ln, (y2 - y1) / ln
        sg = 1.0 if t["side"] == "left" else -1.0
        h = w / 2.0
        far_t = ln + (w if t["type"] == "L" else (t["landing"] if t["landing"] is not None else w))

        def lp(tt, mm):
            return [x1 + ux * tt - uy * mm, y1 + uy * tt + ux * mm]
        if t["type"] == "L":
            loc = [(ln, -sg * (h - inset)), (far_t - inset, -sg * (h - inset)), (far_t - inset, sg * h)]
        else:
            far = sg * (h + t["gap"] + w - inset)
            loc = [(ln, -sg * (h - inset)), (far_t - inset, -sg * (h - inset)), (far_t - inset, far), (ln, far)]
        k = t["risers_a"]
        out.append({"tag": "winder", "path": [lp(*q) for q in loc], "base_offset": base + k * rise, "rise": len(wd["wedges"]) * rise})
    return out


def _slab_ring(p: dict, opening):
    """슬래브의 난간 둘레 고리: 바깥 경계(opening 이 None) 또는 구멍 하나. 구멍이 없으면 None."""
    if opening is None:
        return p["boundary"]
    ops = p.get("openings", ())
    return ops[opening] if opening < len(ops) else None


def _slab_rail_runs(ring, edges) -> list:
    """고리의 변 번호 목록(edges 가 None 이면 모든 변)에서 난간 한 줄들: [(tag 조각, 꼭짓점 목록)]. 이어진 변은 한 줄, 모든 변이면 닫힌 한 줄이다."""
    n = len(ring)
    chosen = sorted(set(range(n) if edges is None else edges))
    if len(chosen) == n:
        return [("all", [ring[i] for i in range(n)] + [ring[0]])]
    start = next(i for i in range(n) if i not in chosen)   # 선택되지 않은 변 다음에서 시작해 한 바퀴 돈다
    order = [(start + 1 + k) % n for k in range(n)]
    cur: list[int] = []
    runs = []
    for e in order:
        if e in chosen:
            cur.append(e)
        elif cur:
            runs.append((f"{cur[0]}+{len(cur)}", [ring[cur[0]]] + [ring[(e2 + 1) % n] for e2 in cur]))
            cur = []
    if cur:
        runs.append((f"{cur[0]}+{len(cur)}", [ring[cur[0]]] + [ring[(e2 + 1) % n] for e2 in cur]))
    return runs


def _slab_tag(opening, run_tag: str) -> str:
    return f"{'outer' if opening is None else f'o{opening}'}:{run_tag}"


def _slab_rail_spec(p: dict, tag: str):
    """슬래브 파라미터에서 표식(`outer:3+2`·`o1:all`)이 가리키는 난간 자리 {"path", "base_offset", "rise"}. 그 자리가 이제 없으면 None."""
    which, _, run = tag.partition(":")
    if which == "outer":
        opening = None
    elif which.startswith("o") and which[1:].isdigit():
        opening = int(which[1:])
    else:
        return None
    ring = _slab_ring(p, opening)
    if ring is None:
        return None
    n = len(ring)
    if run == "all":
        path = [list(ring[i]) for i in range(n)] + [list(ring[0])]
    else:
        a, _, c = run.partition("+")
        if not (a.isdigit() and c.isdigit()) or int(a) >= n or not 1 <= int(c) < n:
            return None
        a, c = int(a), int(c)
        path = [list(ring[a])] + [list(ring[(a + k + 1) % n]) for k in range(c)]
    return {"path": path, "base_offset": p["top_offset"], "rise": 0.0}


def _rail_expected(src_kind: str, params: dict, tag: str, inset):
    """원본 파라미터와 난간 표식에서 난간 자리 {"path", "base_offset", "rise"}(없으면 None). 계단 표식은 `f{i}L|R`·`landing`, 슬래브 표식은 `outer|o{k}:{시작}+{개수}|all`."""
    if src_kind == "slab":
        return _slab_rail_spec(params, tag)
    if src_kind != "stair":
        return None
    for s in _stair_rail_specs(params, "both", 50.0 if inset is None else inset, True):
        if s["tag"] == tag:
            return s
    return None


def _rail_same(a: dict, b: dict) -> bool:
    return (len(a["path"]) == len(b["path"]) and all(abs(p[0] - q[0]) <= RAIL_TOL and abs(p[1] - q[1]) <= RAIL_TOL for p, q in zip(a["path"], b["path"]))
            and abs(a["base_offset"] - b["base_offset"]) <= RAIL_TOL and abs(a["rise"] - b["rise"]) <= RAIL_TOL)


def _follow_railings(ctx, src_id: str, old_p: dict, new_p: dict) -> dict:
    """원본(계단·슬래브) src_id 의 params 가 old_p 에서 new_p 로 바뀔 때, 그것이 낸 난간(src 표식)을 새 난간선으로 바꾼다. 응답 {"moved": [id], "lost": [{"id","reason"}]}(소유한 난간이 없으면 빈 사전).
    옛 원본에서 만든 난간선과 지금 난간선이 다르면(사용자가 고쳤다) 건드리지 않고 lost, 새 원본에 그 자리가 없어도 lost 다. 난간 높이·이름은 사용자 것이라 그대로 둔다."""
    st = ctx.st
    owned = [rid for rid in sorted(st.by_kind.get("railing", ())) if st.el[rid]["params"].get("src") == src_id]
    if not owned:
        return {}
    kind = st.el[src_id]["kind"]
    moved, lost = [], []
    for rid in owned:
        rr = st.el[rid]
        rp = rr["params"]
        old = _rail_expected(kind, old_p, rp["src_tag"], rp.get("src_inset"))
        new = _rail_expected(kind, new_p, rp["src_tag"], rp.get("src_inset"))
        if old is not None and new is not None and _rail_same(old, new):
            continue   # 이 난간 자리는 바뀌지 않았다
        cur = {"path": rp["path"], "base_offset": rp["base_offset"], "rise": rp["rise"]}
        if rr["origin"] != "authored" or old is None or not _rail_same(old, cur):
            lost.append({"id": rid, "reason": "사용자가 고쳤거나 옛 원본과 맞지 않아 따라가지 않았다"})
            continue
        if new is None:
            lost.append({"id": rid, "reason": "새 원본에 이 난간 자리가 없다"})
            continue
        np_ = dict(rp, path=[[float(x), float(y)] for x, y in new["path"]], base_offset=float(new["base_offset"]), rise=float(new["rise"]))
        if K.row_params_error("railing", np_):
            lost.append({"id": rid, "reason": "새 난간선이 올바르지 않다"})
            continue
        ctx.put_el(rid, dict(rr, params=np_))
        moved.append(rid)
    return {"moved": moved, "lost": lost} if moved or lost else {}


def _release_railings(ctx, src_id: str) -> list:
    """원본(계단·슬래브)이 지워질 때 그것이 낸 난간의 출처 표식을 지워 독립 난간으로 남긴다(난간은 지우지 않는다). 풀어 준 난간 id 목록."""
    st = ctx.st
    out = []
    for rid in sorted(st.by_kind.get("railing", ())):
        rr = st.el[rid]
        if rr["params"].get("src") == src_id:
            ctx.put_el(rid, dict(rr, params={k: v for k, v in rr["params"].items() if k not in RAIL_SRC}))
            out.append(rid)
    return out


def _rail_ids(ctx, ids, n: int) -> list:
    """자동 생성 난간의 id(24단계). ids 를 주면 생성 순서대로 그 id 를 쓴다(개수가 같아야 한다, 다른 생성 커맨드의 id 와 같은 규칙).
    비우면 [None]*n(엔진이 새 id 를 만든다)."""
    if ids is None:
        return [None] * n
    if len(ids) != n:
        raise DobimError("CONSTRAINT", f"ids 가 {len(ids)}개인데 만들 난간은 {n}개다", path="/ids",
                         hint="ids 를 비우고 한 번 실행해 개수·순서를 본 뒤(같은 입력이면 같은 순서) 그 개수만큼 주세요", details={"expected": n})
    if len(set(ids)) != n:
        raise DobimError("CONSTRAINT", "ids 에 같은 id 가 두 번 있다", path="/ids")
    for i, rid in enumerate(ids):
        if rid in ctx.st.el:
            raise DobimError("CONSTRAINT", f"이미 있는 id 다: {rid}", path=f"/ids/{i}")
        if _in_split_history(ctx, rid):
            raise DobimError("CONSTRAINT", f"벽 분할 이력에 쓰인 id 라 다시 쓸 수 없다: {rid}", path=f"/ids/{i}")
    return list(ids)


RailIds = Field(None, max_length=64, description="만들 난간의 id 목록(생성 순서대로, 개수가 같아야 한다). 비우면 자동")


class RailingFromStair(A):
    stair: Id
    ids: list[Id] | None = RailIds
    side: Literal["both", "left", "right"] = Field("both", description="어느 쪽에 둘지(단 묶음 걸음 방향의 왼쪽·오른쪽·양쪽)")
    inset: Num = Field(50.0, ge=0, description="계단 옆면에서 안쪽으로 들이는 거리 mm")
    height: Pos = K.RAILING_HEIGHT_DEFAULT
    landing: bool = Field(True, description="꺾인 계단이면 층참의 트인 가장자리에도 난간을 둔다")


@command("railing.from_stair", kind="mutation", risk="write", group="model.railing",
         summary="계단의 단 묶음 옆(과 꺾인 계단 층참의 트인 가장자리)에 난간을 자동 생성. 같은 입력이면 같은 id·순서",
         args=RailingFromStair, since="0.3", examples=[{"stair": F.ST1, "side": "both"}])
def railing_from_stair(ctx, a: RailingFromStair):
    r = ctx.authored(a.stair, "stair", what="난간을 붙이는 것")
    level = ctx.rels_out("on_level", a.stair)[0]
    out = []
    specs = _stair_rail_specs(r["params"], a.side, a.inset, a.landing)
    for s, rid in zip(specs, _rail_ids(ctx, a.ids, len(specs))):
        out.append(_new_railing(ctx, level, "RL", s["path"], a.height, s["base_offset"], s["rise"], {"src": a.stair, "src_tag": s["tag"], "src_inset": float(a.inset)}, rid))
    return {"created": out}


class RailingFromSlab(A):
    slab: Id
    ids: list[Id] | None = RailIds
    opening: int | None = Field(None, ge=0, le=31, description="구멍 번호(0부터). 비우면 슬래브 바깥 경계")
    edges: list[Annotated[int, Field(ge=0, le=255)]] | None = Field(None, max_length=256, description="난간을 둘 변 번호(꼭짓점 i 와 i+1 사이가 변 i). 비우면 모든 변(닫힌 한 줄)")
    height: Pos = K.RAILING_HEIGHT_DEFAULT


@command("railing.from_slab", kind="mutation", risk="write", group="model.railing",
         summary="슬래브 가장자리(바깥 경계 또는 구멍)에 난간을 자동 생성. 이어진 변은 한 난간이다. 같은 입력이면 같은 id·순서",
         args=RailingFromSlab, since="0.3", examples=[{"slab": F.S1, "edges": [0, 1]}])
def railing_from_slab(ctx, a: RailingFromSlab):
    r = ctx.authored(a.slab, "slab", what="난간을 붙이는 것")
    level = ctx.rels_out("on_level", a.slab)[0]
    p = r["params"]
    if a.opening is None:
        ring = p["boundary"]
    else:
        if a.opening >= len(p.get("openings", ())):
            raise DobimError("CONSTRAINT", f"구멍 {a.opening} 이 없다(구멍 {len(p.get('openings', ()))}개)", path="/opening")
        ring = p["openings"][a.opening]
    n = len(ring)
    chosen = sorted(set(range(n) if a.edges is None else a.edges))
    if any(e >= n for e in chosen):
        raise DobimError("CONSTRAINT", f"변 번호가 경계 변 수({n})를 넘는다", path="/edges")
    if not chosen:
        raise DobimError("CONSTRAINT", "변이 하나도 없다", path="/edges")
    top = p["top_offset"]
    runs = _slab_rail_runs(ring, chosen if len(chosen) < n else None)
    out = [_new_railing(ctx, level, "RL", pts, a.height, top, 0.0, {"src": a.slab, "src_tag": _slab_tag(a.opening, tag)}, rid)
           for (tag, pts), rid in zip(runs, _rail_ids(ctx, a.ids, len(runs)))]
    return {"created": out}


class SpaceFromWalls(A):
    level: Id
    points: list[SlabPt] | None = Field(None, max_length=64, description="점 지정: 각 점을 둘러싼 벽 영역마다 공간 하나를 만든다 [[x,y],...] mm(최대 64개). 점이 닫힌 영역에 없으면 거절한다")
    all: bool = Field(False, description="참이면 레벨의 벽이 이루는 닫힌 영역 전부에 공간을 만든다(이미 같은 경계의 공간이 있는 영역은 건너뛴다)")
    edge: Literal["inner", "center"] = Field("inner", description="inner = 벽 안쪽 면(실 면적), center = 벽 중심선(구조 중심 면적)")
    category: Text = Field("", description="새 공간의 분류")
    height: Pos = 2700.0
    min_area_m2: Num = Field(0.5, ge=0, description="all 일 때 이보다 작은 영역(기둥 틈 같은)은 건너뛴다")
    islands: bool = Field(True, description="참이면 방 안의 섬(이어지지 않은 닫힌 내부 벽 고리)과 자립형 기둥을 구멍으로 빼 면적에 반영한다(공간의 holes). 거짓이면 이전처럼 섬을 무시한다")

    @model_validator(mode="after")
    def _one(self):
        if (self.points is None) == (not self.all):
            raise ValueError("points 와 all 중 정확히 하나를 줘야 한다")
        if self.points is not None and not self.points:
            raise ValueError("points 가 비어 있다")
        return self


def _free_columns(ctx, level) -> list:
    """레벨의 저작 기둥(호스트 없이 서는 column 계열 패밀리 인스턴스)의 바닥 윤곽 [(id, 세계 좌표 반시계 다각형)] (id 순). 가장 넓은 각기둥 단면을 쓴다."""
    g = ctx.fresh()
    out = []
    for e in sorted(ctx.rels_in("on_level", level)):
        r = ctx.row(e)
        if r["kind"] != "finst" or r["origin"] != "authored" or not str(r.get("family_id", "")).startswith("column"):
            continue
        ff = g.q(D.free_fam, e)
        if ff is None:
            continue
        pr = max(ff[6], key=lambda t: abs(D._area2(t[2])))
        w = D._free_world(ff)
        out.append((e, [(round(x, 3), round(y, 3)) for x, y in (w(u, v) for u, v in pr[2])]))
    return out


def _room_holes(ctx, a, RG, face, poly, columns) -> list:
    """방 하나의 섬 구멍: 방 안의 닫힌 내부 벽 고리(바깥 면까지 넓힌 윤곽)와 방 안에 완전히 서 있는 기둥. 방 경계에 닿거나 서로 겹치는 것은 건너뛴다(경고)."""
    cand = []
    for h in face.get("holes", ()):
        if a.edge == "inner":
            hp = RG.inner_polygon(h, outer=True)
            if hp is None:
                ctx.warn("섬(방 안의 닫힌 벽 고리)의 바깥 면을 구하지 못해 구멍으로 빼지 않았다")
                continue
        else:
            hp = RG._canonical(h["center"][::-1])
        cand.append([(round(x, 3), round(y, 3)) for x, y in hp])
    rings = [h["center"] for h in face.get("holes", ())]   # 섬 안쪽에 선 기둥은 그 안쪽 방의 것이다
    for eid, cp in columns:
        cx, cy = sum(p[0] for p in cp) / len(cp), sum(p[1] for p in cp) / len(cp)
        if RG.point_in(poly, (cx, cy)) and not any(RG.point_in(r, (cx, cy)) for r in rings):
            cand.append(cp)
    kept: list = []
    for hp in cand:
        if polygon_problem([list(q) for q in hp]):
            continue
        if K.openings_problem([list(q) for q in poly], [[list(q) for q in o] for o in [*kept, hp]]) is None:
            kept.append(hp)
        else:
            ctx.warn("방 경계에 닿거나 다른 구멍과 겹치는 섬·기둥은 구멍으로 빼지 않았다")
    return kept


@command("space.from_walls", kind="mutation", risk="write", group="model.space",
         summary="레벨의 벽으로 닫힌 영역을 찾아 공간 자동 생성(점 지정 또는 전체). 같은 입력이면 같은 id·순서",
         args=SpaceFromWalls, since="0.3", examples=[{"level": F.LV1, "all": True}])
def space_from_walls(ctx, a: SpaceFromWalls):
    from dobim_core import regions as RG
    ctx.authored(a.level, "level", what="레벨에 공간을 추가하는 것")
    walls = []
    for w in sorted(ctx.rels_in("on_level", a.level)):
        r = ctx.row(w)
        if r["kind"] != "wall" or r["origin"] != "authored":
            continue
        if "arc" in r["params"]:   # 곡선 벽(W2 ARC-04): 중심선을 조각 현들로
            from dobim_core import arcgeom as AG
            walls.extend(AG.center_segs(w, r["params"]))
            continue
        x1, y1, x2, y2 = r["params"]["axis"]
        walls.append((w, (x1, y1), (x2, y2), r["params"]["thickness"]))
    faces = RG.find_faces(walls, islands=a.islands)
    columns = _free_columns(ctx, a.level) if a.islands else []   # 방 안에 선 기둥의 바닥 윤곽(구멍 후보)
    existing = []   # 이 레벨의 저작 공간 경계(같은 경계가 이미 있으면 건너뛴다)
    for sp in sorted(ctx.rels_in("on_level", a.level)):
        r = ctx.row(sp)
        if r["kind"] == "space" and r["origin"] == "authored":
            existing.append([(float(x), float(y)) for x, y in r["params"]["boundary"]])

    def shape(face):
        if a.edge == "inner":
            poly = RG.inner_polygon(face)
            if poly is not None:
                return [(round(x, 3), round(y, 3)) for x, y in poly]
            ctx.warn("벽 안쪽 면을 구하지 못한 영역은 벽 중심선으로 만들었다")
        return [(round(x, 3), round(y, 3)) for x, y in face["center"]]

    picked: list = []
    if a.points is not None:
        for pt in a.points:
            hit = [f for f in faces if RG.point_in(f["center"], pt)]
            if not hit:
                raise DobimError("CONSTRAINT", f"점 ({pt[0]:g}, {pt[1]:g}) 을 둘러싼 닫힌 벽 영역이 없다", path="/points",
                                 hint="벽이 이 점을 완전히 둘러싸는지(끊긴 곳이 없는지) 확인하세요. 문 자리는 벽이 이어져 있으면 된다")
            f = min(hit, key=lambda f: RG._area2(f["center"]))   # 겹치면 가장 작은 영역(섬 안쪽 방)
            if f not in picked:
                picked.append(f)
    else:
        picked = [f for f in faces if RG._area2(f["center"]) / 2.0 / 1e6 >= a.min_area_m2]
    created, skipped = [], []
    for f in picked:
        poly = shape(f)
        key = sorted(poly)
        holes = _room_holes(ctx, a, RG, f, poly, columns) if a.islands else []
        if any(len(e) == len(poly) and sorted(e) == key for e in existing):
            skipped.append({"boundary": [list(q) for q in poly], "reason": "같은 경계의 공간이 이미 있다"})
            continue
        why = polygon_problem([list(q) for q in poly])
        if why:
            skipped.append({"boundary": [list(q) for q in poly], "reason": f"경계가 올바르지 않다: {why}"})
            continue
        sid = ctx.new_id("space")
        sp = {"name": f"SP-{sid[-6:]}", "category": a.category, "boundary": [[x, y] for x, y in poly], "height": a.height}
        if holes:   # 섬 구멍(없으면 키 자체가 없다: 섬 없는 방의 행은 이전과 같다)
            sp["holes"] = [[[x, y] for x, y in h] for h in holes]
        ctx.put_el(sid, row("space", "IfcSpace", "space.basic", sp))
        ctx.rel("on_level", sid, a.level, True)
        existing.append(list(poly))
        created.append(sid)
    if not created and a.points is None:
        ctx.warn("닫힌 벽 영역이 없거나 모두 이미 공간이다")
    return {"created": created, "skipped": skipped, "regions": len(picked)}


# ---------------------------------------------------------------- 승강기 (설계: elements-design.md 13절)
ElevatorShaft = Annotated[list[SlabCoord], Field(min_length=4, max_length=4, description="샤프트 안쪽 사각형 [x1,y1,x2,y2] mm(두 모서리, 순서 무관. 각 변 800mm 이상). 카는 이 가운데에 놓인다")]
ElevatorCab = Annotated[list[Pos], Field(min_length=3, max_length=3, description="카 크기 [폭(x), 깊이(y), 높이] mm. 폭·깊이는 샤프트 안쪽 이하, 높이는 샤프트 높이 이하")]


ELEV_OPT = ("pit", "machine", "doors", "stops", "hole_slabs", "hole_clearance")   # 승강기의 선택 파라미터(없으면 키 자체가 없다)


def _elevator_row(name, shaft, cab, base_offset, rise, extra=None) -> dict:
    x1, y1, x2, y2 = (float(v) for v in shaft)
    p = {"name": name, "shaft": [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], "cab": [float(v) for v in cab],
         "base_offset": float(base_offset), "rise": float(rise)}
    p.update(extra or {})
    why = K.row_params_error("elevator", p)
    if why:
        raise DobimError("CONSTRAINT", f"승강기가 올바르지 않다: {why}", path="/shaft")
    return p


def _elev_extra(p: dict) -> dict:
    """승강기 행 params 에서 선택 파라미터만."""
    return {k: p[k] for k in ELEV_OPT if k in p}


class ElevatorDoors(A):
    """승강장 문 개구부: 샤프트의 한 면에 정차층(levels)마다 같은 크기로 낸다(면 가운데, 정차층 바닥에서 위로)."""
    face: Literal["N", "E", "S", "W"] = Field(description="문이 있는 샤프트 면(N = +y 변, S = -y 변, E = +x 변, W = -x 변)")
    w: Pos = Field(description="문 폭 mm(샤프트 면보다 200mm 이상 좁아야 한다)")
    h: Pos = Field(description="문 높이 mm")
    levels: list[Id] = Field(min_length=1, max_length=K.ELEVATOR_STOPS_MAX, description="승강장 문이 있는 층(레벨 id). 레벨 높이가 샤프트 높이 범위에 들어야 한다")


def _check_doors(ctx, p: dict, lv_elev: float) -> None:
    """승강장 문(doors·stops)이 있으면 층마다 레벨이고 문이 샤프트 높이 범위 안에 드는지 본다."""
    if "doors" not in p:
        return
    lo, hi = lv_elev + p["base_offset"] - p.get("pit", 0.0), lv_elev + p["base_offset"] + p["rise"]
    for lid in p["stops"]:
        r = ctx.st.el.get(lid)
        if r is None or r["kind"] != "level":
            raise DobimError("CONSTRAINT", f"승강장 문의 층이 레벨이 아니다: {lid}", path="/doors/levels")
        z = r["params"]["elevation"]
        if z < lo - 1.0 or z + p["doors"]["h"] > hi + 1.0:
            raise DobimError("CONSTRAINT", f"승강장 문이 샤프트 높이 범위({lo:g}~{hi:g})를 벗어난다: 층 {r['params']['name']}(높이 {z:g}), 문 높이 {p['doors']['h']:g}", path="/doors")


def _doors_params(d: ElevatorDoors) -> dict:
    return {"doors": {"face": d.face, "w": float(d.w), "h": float(d.h)}, "stops": list(dict.fromkeys(d.levels))}


class ElevatorCreate(A):
    id: Id | None = None
    level: Id = Field(description="가장 아래 정차층(카가 서는 레벨)")
    shaft: ElevatorShaft
    cab: ElevatorCab | None = Field(None, description="카 크기. 비우면 1100x1400x2200")
    base_offset: Num = Field(0.0, description="레벨 높이에서 카 바닥(샤프트 아래)까지 mm")
    rise: Pos | None = Field(None, description="샤프트 높이 mm(카 바닥에서 샤프트 위까지). to_level 과 둘 중 하나를 준다")
    to_level: Id | None = Field(None, description="가장 위 정차층. 주면 샤프트 위 = 그 레벨 높이 + overrun 이 되도록 rise 를 정한다(그때의 값이 저장된다: 이후 레벨을 옮겨도 따라가지 않는다)")
    overrun: Num = Field(0.0, ge=0, description="to_level 위로 더 올라가는 샤프트 높이 mm(승강로 오버헤드). to_level 과 함께만 쓴다")
    name: Name | None = None
    pit: Annotated[float, Field(gt=0, le=K.ELEVATOR_PIT_MAX, allow_inf_nan=False)] | None = Field(None, description="피트 깊이 mm(카 바닥 아래로 더 내려간 샤프트 바닥). 단면·상세에 표기")
    machine_height: Annotated[float, Field(gt=0, le=K.ELEVATOR_MACHINE_MAX, allow_inf_nan=False)] | None = Field(None, description="기계실 높이 mm(샤프트 위에 같은 평면 크기로 얹는다)")
    doors: ElevatorDoors | None = Field(None, description="층별 승강장 문 개구부(면·폭·높이·층 목록)")

    @model_validator(mode="after")
    def _one(self):
        if (self.rise is None) == (self.to_level is None):
            raise ValueError("rise 와 to_level 중 정확히 하나를 줘야 한다")
        if self.to_level is None and self.overrun:
            raise ValueError("overrun 은 to_level 과 함께만 쓴다")
        return self


@command("elevator.create", kind="mutation", risk="write", group="model.elevator",
         summary="승강기 생성(샤프트 안쪽 사각형 + 카 크기 + 높이 또는 위 정차층, 선택: 피트·기계실·승강장 문). IFC 는 IfcTransportElement(ELEVATOR) 카 상자로 나간다",
         args=ElevatorCreate, since="0.3", examples=[{"level": F.LV1, "shaft": [4000, 500, 5800, 2300], "rise": 6000}])
def elevator_create(ctx, a: ElevatorCreate):
    _fresh_id(ctx, a, "elevator")
    lv = ctx.authored(a.level, "level", what="레벨에 승강기를 추가하는 것")
    rise = a.rise
    if a.to_level is not None:
        top = ctx.authored(a.to_level, "level", what="승강기의 위 정차층")
        rise = top["params"]["elevation"] + a.overrun - (lv["params"]["elevation"] + a.base_offset)
        if rise <= 0:
            raise DobimError("CONSTRAINT", "위 정차층이 카 바닥보다 높아야 한다", path="/to_level")
    a.name = a.name or f"EV-{a.id[-6:]}"
    extra = {}
    if a.pit is not None:
        extra["pit"] = float(a.pit)
    if a.machine_height is not None:
        extra["machine"] = {"height": float(a.machine_height)}
    if a.doors is not None:
        extra.update(_doors_params(a.doors))
    p = _elevator_row(a.name, a.shaft, a.cab or K.ELEVATOR_CAB_DEFAULT, a.base_offset, rise, extra)
    _check_doors(ctx, p, lv["params"]["elevation"])
    ctx.put_el(a.id, row("elevator", "IfcTransportElement", "elevator.basic", p))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id, "rise": rise}


class ElevatorSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    shaft: ElevatorShaft | None = None
    cab: ElevatorCab | None = None
    base_offset: Num | None = None
    rise: Pos | None = None
    pit: Annotated[float, Field(gt=0, le=K.ELEVATOR_PIT_MAX, allow_inf_nan=False)] | None = Field(None, description="피트 깊이 mm")
    clear_pit: bool = Field(False, description="참이면 피트를 지운다")
    machine_height: Annotated[float, Field(gt=0, le=K.ELEVATOR_MACHINE_MAX, allow_inf_nan=False)] | None = Field(None, description="기계실 높이 mm")
    clear_machine: bool = Field(False, description="참이면 기계실을 지운다")
    doors: ElevatorDoors | None = Field(None, description="승강장 문 개구부를 이 값으로 바꾼다(전체 교체)")
    clear_doors: bool = Field(False, description="참이면 승강장 문을 지운다")

    @model_validator(mode="after")
    def _any(self):
        if (all(v is None for v in (self.name, self.shaft, self.cab, self.base_offset, self.rise, self.pit, self.machine_height, self.doors))
                and not (self.clear_pit or self.clear_machine or self.clear_doors)):
            raise ValueError("바꿀 필드가 하나도 없다")
        for v, c in ((self.pit, self.clear_pit), (self.machine_height, self.clear_machine), (self.doors, self.clear_doors)):
            if v is not None and c:
                raise ValueError("값과 그 clear_ 를 함께 줄 수 없다")
        return self


def _hole_rect(shaft, c) -> list:
    """승강기 샤프트의 슬래브 구멍(사방 c 만큼 넓힌 반시계 사각형). elevator.cut_slabs 가 내는 구멍과 같은 값이다."""
    x1, y1, x2, y2 = shaft
    return [[x1 - c, y1 - c], [x2 + c, y1 - c], [x2 + c, y2 + c], [x1 - c, y2 + c]]


def _same_ring(a, b) -> bool:
    return sorted(map(tuple, a)) == sorted(map(tuple, b))


def _follow_holes(ctx, eid, old_p: dict, new_p: dict) -> dict:
    """승강기가 소유한 슬래브 구멍(hole_slabs: 승강기가 elevator.cut_slabs 로 낸 슬래브 id)을 샤프트 변경(이동·크기·여유)에 맞춰 바꾼다.
    소유 구멍은 `옛 샤프트 + 옛 여유` 사각형과 꼭짓점이 같은 구멍 하나뿐이다: 사용자가 직접 낸 구멍(같은 모양이라도 소유 슬래브가 아니면, 또는 모양이 다르면)은
    건드리지 않는다. 구멍이 사라졌거나 사용자가 고친 슬래브는 따라가지 않고 `lost` 로 알린다. 새 구멍이 슬래브 경계 밖이거나 다른 구멍과 겹치면 거절한다(CONSTRAINT)."""
    slabs = old_p.get("hole_slabs")
    if not slabs:
        return {}
    old_hole = _hole_rect(old_p["shaft"], old_p["hole_clearance"])
    new_hole = _hole_rect(new_p["shaft"], new_p["hole_clearance"])
    if _same_ring(old_hole, new_hole):
        return {}
    moved, lost, rails = [], [], []
    for sid in slabs:
        sr = ctx.st.el.get(sid)
        if sr is None or sr["kind"] != "slab" or sr["origin"] != "authored":
            lost.append(sid)
            continue
        ops = [list(o) for o in sr["params"].get("openings", ())]
        idx = next((i for i, o in enumerate(ops) if _same_ring(o, old_hole)), None)
        if idx is None:
            lost.append(sid)
            continue
        ops[idx] = new_hole
        np_ = dict(sr["params"], openings=ops)
        why = K.row_params_error("slab", np_)
        if why:
            raise DobimError("CONSTRAINT", f"슬래브 {sid} 의 승강기 구멍을 옮길 수 없다: {why}", path="/shaft",
                             hint="이동량을 줄이거나 슬래브(경계·다른 구멍)를 먼저 고치세요")
        rail = _follow_railings(ctx, sid, sr["params"], np_)   # 구멍 둘레에 둔 난간(railing.from_slab opening)도 따라간다
        ctx.put_el(sid, dict(sr, params=np_))
        moved.append(sid)
        if rail:
            rails.append({"slab": sid, **rail})
    return {"moved": moved, "lost": lost, **({"railings": rails} if rails else {})}


@command("elevator.set", kind="mutation", risk="write", group="model.elevator",
         summary="승강기 속성 변경(샤프트·카 크기·카 바닥 높이·샤프트 높이·이름·피트·기계실·승강장 문). 샤프트가 바뀌면 승강기가 낸 슬래브 구멍이 따라간다",
         args=ElevatorSet, since="0.3", examples=[{"id": F.EL1, "cab": [1400, 1600, 2300]}])
def elevator_set(ctx, a: ElevatorSet):
    r = ctx.authored(a.id, "elevator")
    old = r["params"]
    p = dict(old)
    for k in ("name", "shaft", "cab", "base_offset", "rise"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    extra = _elev_extra(p)
    if a.pit is not None:
        extra["pit"] = float(a.pit)
    if a.clear_pit:
        extra.pop("pit", None)
    if a.machine_height is not None:
        extra["machine"] = {"height": float(a.machine_height)}
    if a.clear_machine:
        extra.pop("machine", None)
    if a.doors is not None:
        extra.update(_doors_params(a.doors))
    if a.clear_doors:
        extra.pop("doors", None)
        extra.pop("stops", None)
    new = _elevator_row(p["name"], p["shaft"], p["cab"], p["base_offset"], p["rise"], extra)
    _check_doors(ctx, new, ctx.st.el[ctx.rels_out("on_level", a.id)[0]]["params"]["elevation"])
    out = {"id": a.id}
    holes = _follow_holes(ctx, a.id, old, new)
    ctx.put_el(a.id, dict(r, params=new))
    if holes:
        out["holes"] = holes
    return out


@command("elevator.move", kind="mutation", risk="write", group="model.elevator",
         summary="승강기를 평행 이동한다(샤프트가 따라간다. elevator.cut_slabs 가 낸 슬래브 구멍은 함께 옮겨지고, 사용자가 직접 낸 구멍은 건드리지 않는다)",
         args=SlabMove, since="0.3", examples=[{"id": F.EL1, "dx": 600, "dy": 0}])
def elevator_move(ctx, a: SlabMove):
    r = ctx.authored(a.id, "elevator")
    x1, y1, x2, y2 = r["params"]["shaft"]
    shaft = [x1 + a.dx, y1 + a.dy, x2 + a.dx, y2 + a.dy]
    new = dict(r["params"], shaft=shaft)
    out = {"id": a.id, "shaft": shaft}
    holes = _follow_holes(ctx, a.id, r["params"], new)
    ctx.put_el(a.id, dict(r, params=new))
    if holes:
        out["holes"] = holes
    return out


@command("elevator.delete", kind="mutation", risk="write", group="model.elevator", summary="승강기 삭제(슬래브 구멍은 남는다)",
         args=IdArg, since="0.3", examples=[{"id": F.EL1}])
def elevator_delete(ctx, a: IdArg):
    ctx.authored(a.id, "elevator", what="삭제")
    _delete_one(ctx, a.id)
    return {"deleted": [a.id]}


class ElevatorCutSlabs(A):
    id: Id
    clearance: Num = Field(0.0, ge=0, le=500, description="샤프트 안쪽 사각형을 사방으로 이만큼(mm) 넓혀 구멍을 낸다")


@command("elevator.cut_slabs", kind="mutation", risk="write", group="model.elevator",
         summary="승강기 샤프트가 지나는 슬래브(밑면이 샤프트 위 아래, 윗면이 샤프트 바닥 위인 저작 슬래브)에 샤프트 모양의 구멍을 낸다. 낸 구멍은 승강기가 소유해(hole_slabs) 이후 elevator.move·set 에 따라간다. "
                 "이미 같은 구멍이 있으면 건너뛰고(소유하지 않는다), 경계 밖·다른 구멍과 겹치면 건너뛰며 이유를 알린다. 같은 입력이면 같은 결과",
         args=ElevatorCutSlabs, since="0.3", examples=[{"id": F.EL1}])
def elevator_cut_slabs(ctx, a: ElevatorCutSlabs):
    r = ctx.authored(a.id, "elevator", what="슬래브 구멍을 내는 것")
    g = ctx.fresh()
    ev = r["params"]
    if ev.get("hole_slabs") and ev["hole_clearance"] != float(a.clearance):   # 여유를 바꾸면 이미 소유한 구멍도 새 여유로 맞춘다
        new = dict(ev, hole_clearance=float(a.clearance))
        _follow_holes(ctx, a.id, ev, new)
        ctx.put_el(a.id, dict(r, params=new))
        r = ctx.authored(a.id, "elevator", what="슬래브 구멍을 내는 것")
        ev = r["params"]
    gm = K.elevator_geom(ev)
    lv = g.q(Q.level_info, ctx.rels_out("on_level", a.id)[0])[1]
    z0, z1 = lv + gm["z0"], lv + gm["z1"]
    c = a.clearance
    hole = _hole_rect(gm["shaft"], c)
    cut, already, skipped = [], [], []
    for sid in sorted(ctx.st.by_kind.get("slab", ())):
        sr = ctx.st.el[sid]
        if sr["origin"] != "authored":
            continue
        zb, zt = g.q(Q.slab_zrange, sid)
        if not (zt > z0 + 1.0 and zb < z1 - 1.0):   # 샤프트의 높이 범위를 지나지 않는 슬래브(카가 서는 바닥 슬래브 포함)는 건드리지 않는다
            continue
        p = sr["params"]
        have = [sorted(map(tuple, o)) for o in p.get("openings", ())]
        if sorted(map(tuple, hole)) in have:
            already.append(sid)
            continue
        why = K.openings_problem(p["boundary"], [*p.get("openings", ()), hole])
        if why:
            skipped.append({"slab": sid, "reason": why})
            continue
        ctx.put_el(sid, dict(sr, params=dict(p, openings=[*p.get("openings", ()), hole])))
        cut.append(sid)
    if cut:   # 낸 구멍을 승강기가 소유한다(기존 소유 목록에 더한다)
        owned = sorted(set(ev.get("hole_slabs", ())) | set(cut))
        ctx.put_el(a.id, dict(r, params=dict(ev, hole_slabs=owned, hole_clearance=float(c))))
    if not cut and not already:
        ctx.warn("샤프트가 지나는 슬래브가 없거나 모두 구멍을 낼 수 없었다")
    return {"cut": cut, "already": already, "skipped": skipped}


# ---------------------------------------------------------------- 경사 지붕 (설계: elements-design.md 16절)
RoofRect = Annotated[list[SlabPt], Field(min_length=3, max_length=32, description="외곽 꼭짓점 mm. gable·hip·shed 는 직사각형 4개, poly 는 다각형 3~32개. 시계 방향이면 뒤집어 저장. 변 500mm 이상")]
RoofSlopes = Annotated[list[Annotated[float, Field(ge=0, le=K.ROOF_PITCH_MAX, allow_inf_nan=False)]], Field(min_length=3, max_length=32)]
SLOPES_DESC = "poly 의 변마다 물매 도(변 i = 꼭짓점 i→i+1). 0 = 박공 변. 생략하면 모두 pitch"


def _roof_boundary(pts, form: str = "gable", slopes=None):
    """외곽을 반시계로 맞춘다(뒤집으면 변마다 물매도 같은 변을 따라 옮긴다). (외곽, 물매 목록 또는 None)."""
    b = [[float(x), float(y)] for x, y in pts]
    sl = None if slopes is None else [float(v) for v in slopes]
    if sl is not None and len(sl) != len(b):
        raise DobimError("CONSTRAINT", f"slopes 는 외곽의 변 수({len(b)})만큼 있어야 한다", path="/slopes")
    if polygon_area2(b) < 0:
        b = b[::-1]
        if sl is not None:   # 뒤집은 외곽의 변 j = 원래 변 n-2-j
            n = len(sl)
            sl = [sl[(n - 2 - j) % n] for j in range(n)]
    if form != "poly":
        why = K.roof_rect_problem(b)
        if why:
            raise DobimError("CONSTRAINT", f"지붕 외곽이 올바르지 않다: {why}", path="/boundary", hint="직사각형이 아니면 form=poly")
    return b, sl


def _roof_checked(p: dict) -> dict:
    why = K.row_params_error("roof", p)
    if why:
        raise DobimError("CONSTRAINT", f"지붕이 올바르지 않다: {why}")
    return p


class RoofCreate(A):
    id: Id | None = None
    level: Id = Field(description="지붕이 놓인 레벨(처마 높이 = 레벨 + base_offset)")
    boundary: RoofRect
    form: Literal["gable", "hip", "shed", "poly"] = Field(description="gable 박공, hip 모임, shed 외쪽, poly 다각형 외곽(변마다 물매·박공)")
    pitch: Annotated[float, Field(gt=0, le=K.ROOF_PITCH_MAX, allow_inf_nan=False)] = Field(description="물매 각도 도(0 초과 75 이하)")
    slopes: RoofSlopes | None = Field(None, description=SLOPES_DESC)
    overhang: Annotated[float, Field(ge=0, le=K.ROOF_OVERHANG_MAX, allow_inf_nan=False)] = Field(0.0, description="처마 내밀기 mm(외곽에서 물매대로 더 내민다)")
    base_offset: Num = Field(0.0, description="레벨 높이에서 처마 선(외곽의 변)까지 mm")
    thickness: Pos = Field(200.0, description="지붕 두께 mm(윗면에서 수직 아래로)")
    ridge: Literal["long", "short"] | None = Field(None, description="박공의 용마루 방향: long(기본, 긴 변 방향)·short(짧은 변 방향). 박공에만")
    dir: Num | None = Field(None, description="외쪽 지붕에서 물이 흐르는 방향 도(+x 가 0, 반시계). 외쪽에만, 외쪽이면 필수")
    name: Name | None = None


def _roof_row(a_form, boundary, pitch, overhang, base_offset, thickness, ridge, dir_, name, slopes=None) -> dict:
    p = {"name": name, "boundary": boundary, "form": a_form, "pitch": float(pitch), "overhang": float(overhang), "base_offset": float(base_offset), "thickness": float(thickness)}
    if ridge == "short":
        p["ridge"] = "short"
    if dir_ is not None:
        p["dir"] = float(dir_)
    if a_form == "poly":
        p["slopes"] = slopes if slopes is not None else [float(pitch)] * len(boundary)
    elif slopes is not None:
        raise DobimError("CONSTRAINT", "slopes 는 form=poly 에만 줄 수 있다", path="/slopes")
    return _roof_checked(p)


@command("roof.create", kind="mutation", risk="write", group="model.roof",
         summary="경사 지붕 생성(외곽 + 형식 + 물매 + 처마 내밀기·높이). poly 는 다각형 외곽에 변마다 물매·박공(L·T 모임 지붕, 골 포함). IFC 는 IfcRoof 아래 경사면마다 IfcSlab(ROOF)",
         args=RoofCreate, since="0.4",
         examples=[{"level": F.LV2, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "form": "gable", "pitch": 30, "overhang": 600},
                   {"level": F.LV2, "boundary": [[0, 0], [9000, 0], [9000, 4000], [4000, 4000], [4000, 8000], [0, 8000]], "form": "poly", "pitch": 30,
                    "slopes": [30, 0, 30, 30, 0, 30]}])
def roof_create(ctx, a: RoofCreate):
    _fresh_id(ctx, a, "roof")
    ctx.authored(a.level, "level", what="레벨에 지붕을 추가하는 것")
    a.name = a.name or f"RF-{a.id[-6:]}"
    b, sl = _roof_boundary(a.boundary, a.form, a.slopes)
    p = _roof_row(a.form, b, a.pitch, a.overhang, a.base_offset, a.thickness, a.ridge, a.dir, a.name, sl)
    ctx.put_el(a.id, row("roof", "IfcRoof", "roof.basic", p))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id}


class RoofSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    boundary: RoofRect | None = None
    form: Literal["gable", "hip", "shed", "poly"] | None = Field(None, description="형식을 바꾼다. shed 는 dir 도 필요")
    pitch: Annotated[float, Field(gt=0, le=K.ROOF_PITCH_MAX, allow_inf_nan=False)] | None = None
    overhang: Annotated[float, Field(ge=0, le=K.ROOF_OVERHANG_MAX, allow_inf_nan=False)] | None = None
    base_offset: Num | None = None
    thickness: Pos | None = None
    ridge: Literal["long", "short"] | None = None
    dir: Num | None = None
    slopes: RoofSlopes | None = Field(None, description="poly 의 변마다 물매(0 = 박공). 꼭짓점 수가 바뀌면 함께 준다")

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.boundary, self.form, self.pitch, self.overhang, self.base_offset, self.thickness, self.ridge, self.dir, self.slopes)):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("roof.set", kind="mutation", risk="write", group="model.roof", summary="경사 지붕 속성 변경(외곽·형식·물매·변마다 물매·처마·두께·용마루·물 흐름 방향·이름). poly 에서 pitch 만 주면 경사 변 물매가 모두 바뀐다",
         args=RoofSet, since="0.4", examples=[{"id": F.RF1, "pitch": 25}])
def roof_set(ctx, a: RoofSet):
    r = ctx.authored(a.id, "roof")
    p = dict(r["params"])
    old_form = p["form"]
    for k in ("name", "form", "pitch", "overhang", "base_offset", "thickness"):
        if getattr(a, k) is not None:
            p[k] = float(getattr(a, k)) if k in ("pitch", "overhang", "base_offset", "thickness") else getattr(a, k)
    sl = None
    if a.boundary is not None:
        p["boundary"], sl = _roof_boundary(a.boundary, p["form"], a.slopes)
    elif a.slopes is not None:
        sl = [float(v) for v in a.slopes]
        if len(sl) != len(p["boundary"]):
            raise DobimError("CONSTRAINT", f"slopes 는 외곽의 변 수({len(p['boundary'])})만큼 있어야 한다", path="/slopes")
    if p["form"] == "poly":
        if sl is not None:
            p["slopes"] = sl
        elif old_form != "poly" or len(p.get("slopes", ())) != len(p["boundary"]):
            if old_form == "poly" and len(set(p.get("slopes", ()))) > 1:
                raise DobimError("CONSTRAINT", "외곽 꼭짓점 수가 바뀌었다: 변마다 물매 slopes 도 함께 준다", path="/slopes")
            p["slopes"] = [p["pitch"]] * len(p["boundary"])
        elif a.pitch is not None:   # pitch 만 바꾸면 경사 변(물매 > 0) 모두
            p["slopes"] = [p["pitch"] if v > 0 else 0.0 for v in p["slopes"]]
    else:
        if sl is not None:
            raise DobimError("CONSTRAINT", "slopes 는 form=poly 에만 줄 수 있다", path="/slopes")
        p.pop("slopes", None)
        if a.boundary is None and old_form == "poly":
            why = K.roof_rect_problem(p["boundary"])
            if why:
                raise DobimError("CONSTRAINT", f"지붕 외곽이 올바르지 않다: {why}", path="/boundary", hint="직사각형 외곽을 함께 준다")
    if a.ridge is not None:
        if a.ridge == "short":
            p["ridge"] = "short"
        else:
            p.pop("ridge", None)
    if a.dir is not None:
        p["dir"] = float(a.dir)
    if p["form"] != "shed":
        p.pop("dir", None)
    if p["form"] != "gable":
        p.pop("ridge", None)
    ctx.put_el(a.id, dict(r, params=_roof_checked(p)))
    return {"id": a.id}


@command("roof.move", kind="mutation", risk="write", group="model.roof", summary="경사 지붕을 평행 이동한다",
         args=SlabMove, since="0.4", examples=[{"id": F.RF1, "dx": 600, "dy": 0}])
def roof_move(ctx, a: SlabMove):
    r = ctx.authored(a.id, "roof")
    b = [[x + a.dx, y + a.dy] for x, y in r["params"]["boundary"]]
    ctx.put_el(a.id, dict(r, params=dict(r["params"], boundary=b)))
    return {"id": a.id, "boundary": b}


@command("roof.delete", kind="mutation", risk="write", group="model.roof", summary="경사 지붕 삭제",
         args=IdArg, since="0.4", examples=[{"id": F.RF1}])
def roof_delete(ctx, a: IdArg):
    ctx.authored(a.id, "roof", what="삭제")
    _delete_one(ctx, a.id)
    return {"deleted": [a.id]}


# ---------------------------------------------------------------- 통심선 (19단계, 설계: elements-design.md 21절)
# 모델 전체에 하나씩 있는 기준선(Revit 그리드). 평면 뷰는 선(1점쇄선)과 끝 원 기호를, 입면·단면은 보는 방향과 나란한 통심선을 세로선으로 그린다.
GridCoord = Annotated[float, Field(allow_inf_nan=False, ge=-1e7, le=1e7)]


def _grid_name_free(ctx, name, me=None):
    for gid in ctx.st.by_kind.get("grid", ()):
        if gid != me and ctx.st.el[gid]["params"]["name"] == name:
            raise DobimError("CONSTRAINT", f"같은 기호의 통심선이 있다: {name}", path="/name")


class GridCreate(A):
    id: Id | None = None
    name: Annotated[str, Field(min_length=1, max_length=16)] = Field(description="통심선 기호(예: X1, A). 모델 안에서 유일")
    line: Annotated[list[GridCoord], Field(min_length=4, max_length=4)] = Field(description="평면 선분 [x1,y1,x2,y2] mm")
    bubbles: Literal["both", "start", "end", "none"] = Field("both", description="기호 원을 둘 끝")


@command("grid.create", kind="mutation", risk="write", group="model.grid", summary="통심선(그리드) 생성: 평면에 1점쇄선+원 기호, 입면·단면에 세로선", args=GridCreate, since="0.6",
         examples=[{"name": "X1", "line": [0, -2000, 0, 12000]}, {"name": "Y1", "line": [-2000, 0, 15000, 0], "bubbles": "start"}])
def grid_create(ctx, a: GridCreate):
    _fresh_id(ctx, a, "grid")
    _grid_name_free(ctx, a.name)
    p = {"name": a.name, "line": [float(v) for v in a.line], "bubbles": a.bubbles}
    why = K.row_params_error("grid", p)
    if why:
        raise DobimError("CONSTRAINT", f"통심선이 올바르지 않다: {why}", path="/line")
    ctx.put_el(a.id, row("grid", "", "grid.basic", p))
    return {"id": a.id}


class GridSet(A):
    forbid_null = True
    id: Id
    name: Annotated[str, Field(min_length=1, max_length=16)] | None = None
    line: Annotated[list[GridCoord], Field(min_length=4, max_length=4)] | None = None
    bubbles: Literal["both", "start", "end", "none"] | None = None


@command("grid.set", kind="mutation", risk="write", group="model.grid", summary="통심선 기호·선분·원 위치 변경", args=GridSet, since="0.6",
         examples=[{"id": "g" + "0" * 21, "line": [0, -3000, 0, 12000]}])
def grid_set(ctx, a: GridSet):
    r = ctx.row(a.id, "grid")
    p = dict(r["params"])
    if a.name is not None:
        _grid_name_free(ctx, a.name, a.id)
        p["name"] = a.name
    if a.line is not None:
        p["line"] = [float(v) for v in a.line]
    if a.bubbles is not None:
        p["bubbles"] = a.bubbles
    why = K.row_params_error("grid", p)
    if why:
        raise DobimError("CONSTRAINT", f"통심선이 올바르지 않다: {why}", path="/line")
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


class GridMove(A):
    id: Id
    dx: Num = 0.0
    dy: Num = 0.0


@command("grid.move", kind="mutation", risk="write", group="model.grid", summary="통심선을 평행 이동한다(dx, dy)", args=GridMove, since="0.6",
         examples=[{"id": "g" + "0" * 21, "dx": 300}])
def grid_move(ctx, a: GridMove):
    r = ctx.row(a.id, "grid")
    x1, y1, x2, y2 = r["params"]["line"]
    ctx.put_el(a.id, dict(r, params=dict(r["params"], line=[x1 + a.dx, y1 + a.dy, x2 + a.dx, y2 + a.dy])))
    return {"id": a.id}


@command("grid.delete", kind="mutation", risk="write", group="model.grid", summary="통심선 삭제", args=IdArg, since="0.6", examples=[{"id": "g" + "0" * 21}])
def grid_delete(ctx, a: IdArg):
    ctx.row(a.id, "grid")
    ctx.put_el(a.id, None)
    return {"deleted": [a.id]}


# ---------------------------------------------------------------- 일반 솔리드 (18단계, 설계: elements-design.md 17절)
# 벽·지붕 같은 정형 요소로 표현할 수 없는 부재(부벽 단·각뿔/팔각 첨탑·소첨탑·띠돌림·박공 메움·경사 판·지반)를 돌출·로프트·판·스윕·메시로 만든다.
# 형상 검사와 다면체는 solids.py 가 정본이다. IFC 클래스는 범주(category)가 정한다. z 는 놓인 레벨 기준이다.
SoCoord = Annotated[float, Field(allow_inf_nan=False, ge=-1e7, le=1e7)]
SoPt2 = Annotated[list[SoCoord], Field(min_length=2, max_length=2)]
SoPt3 = Annotated[list[SoCoord], Field(min_length=3, max_length=3)]
SoPoly = Annotated[list[SoPt2], Field(min_length=1, max_length=256)]
SoCategory = Literal[tuple(K.SO.SOLID_CATEGORIES)]


class LoftSection(A):
    z: SoCoord = Field(description="단면 높이 mm(레벨 기준)")
    polygon: SoPoly = Field(description="단면 다각형 [[x,y],...]. 맨 아래·맨 위 단면은 꼭짓점 하나(정점)여도 된다")


class MemberSection(A):
    shape: Literal["rect", "circle", "h", "box", "pipe"] = Field(description="단면 모양: rect 사각, circle 원(지름 = width = depth), h H/I형강, box 각형 강관(속 빈 사각), pipe 원형 관(속 빈 원)")
    width: Pos = Field(description="단면 폭 mm(원은 지름)")
    depth: Pos = Field(description="단면 깊이(춤) mm(원은 지름)")
    web: Pos | None = Field(None, description="h: 웨브 두께 mm")
    flange: Pos | None = Field(None, description="h: 플랜지 두께 mm")
    wall: Pos | None = Field(None, description="box·pipe: 관 두께 mm")


class SolidGeom(A):
    form: Literal["prism", "loft", "panel", "sweep", "mesh", "member", "plate"] | None = Field(None, description="prism 수직 돌출, loft 단면 잇기(각뿔·첨탑), panel 수직면 위 윤곽 판(+개구부), sweep 평면 경로를 따른 단면(띠돌림), mesh 다면체, member 3D 축선을 따른 곧은 부재(보·가새·관·덕트), plate 기운 평면 위 윤곽 판(+구멍: 지붕판·경사 슬래브)")
    polygon: SoPoly | None = Field(None, description="prism·plate: 평면 윤곽 [[x,y],...] (3~256)")
    holes: Annotated[list[SoPoly], Field(min_length=0, max_length=64)] | None = Field(None, description="plate: 구멍 윤곽 목록(빈 목록이면 뗀다)")
    base: SoPt3 | None = Field(None, description="plate: 윗면 위의 한 점 [x,y,z] (z 는 레벨 기준)")
    pitch: Annotated[float, Field(ge=0, le=80, allow_inf_nan=False)] | None = Field(None, description="plate: 물매(도). 0 이면 수평 판")
    dir: Annotated[float, Field(ge=-360, le=360, allow_inf_nan=False)] | None = Field(None, description="plate: 내려가는 방향(+x 에서 반시계 도)")
    z0: SoCoord | None = Field(None, description="prism: 아랫면 z mm(레벨 기준)")
    z1: SoCoord | None = Field(None, description="prism: 윗면 z mm(레벨 기준)")
    sections: Annotated[list[LoftSection], Field(min_length=2, max_length=32)] | None = Field(None, description="loft: 아래에서 위로 단면 목록(꼭짓점 수가 같아야 한다)")
    line: Annotated[list[SoCoord], Field(min_length=4, max_length=4)] | None = Field(None, description="panel: 판이 놓인 수직면의 기준선 [x1,y1,x2,y2]. 판 두께는 이 선의 양쪽으로 반씩")
    thickness: Pos | None = Field(None, description="panel: 판 두께 mm / plate: 수직 두께 mm(윗면에서 아래로)")
    profile: SoPoly | None = Field(None, description="panel: 윤곽 [[u,z],...] (u = line 시작점에서 길이 방향, z = 레벨 기준) / sweep: 단면 [[o,z],...] (o = 경로 진행 방향 왼쪽 거리)")
    openings: Openings | None = Field(None, description="panel: 모양 개구부(offset 은 line 시작점 기준, sill 은 레벨 기준 z). 빈 목록이면 뗀다")
    path: Annotated[list[SoPt2], Field(min_length=2, max_length=256)] | None = Field(None, description="sweep: 평면 경로 [[x,y],...] (첫 점을 끝에 다시 주면 닫힌 고리)")
    vertices: Annotated[list[SoPt3], Field(min_length=4, max_length=4096)] | None = Field(None, description="mesh: 꼭짓점 [[x,y,z],...] (z 는 레벨 기준)")
    faces: Annotated[list[Annotated[list[Annotated[int, Field(ge=0, le=4095)]], Field(min_length=3, max_length=64)]], Field(min_length=4, max_length=8192)] | None = Field(None, description="mesh: 면 = 꼭짓점 번호 목록(바깥에서 보아 반시계). 닫힌 다면체여야 한다")
    start: SoPt3 | None = Field(None, description="member: 축선 시작점 [x,y,z] (z 는 레벨 기준)")
    end: SoPt3 | None = Field(None, description="member: 축선 끝점 [x,y,z]")
    section: MemberSection | None = Field(None, description="member: 단면")
    anchor: Literal["top", "center", "bottom"] | None = Field(None, description="member: 축선이 지나는 단면 위치(기본 top: 보 윗면 가운데. 관·덕트는 center)")


_SO_KEYS = {"prism": ("polygon", "z0", "z1"), "loft": ("sections",), "panel": ("line", "thickness", "profile", "openings"), "sweep": ("path", "profile"), "mesh": ("vertices", "faces"), "member": ("start", "end", "section", "anchor"),
           "plate": ("polygon", "holes", "thickness", "base", "pitch", "dir")}


def _solid_geom(a: SolidGeom, base: dict | None) -> dict:
    """입력 형상 필드 -> 행 params 의 형상 부분. base 가 있으면(solid.set) 바꾸지 않은 키를 이어받는다. 형태가 바뀌면 이전 형태 키는 버린다."""
    form = a.form or (base or {}).get("form")
    if form is None:
        raise DobimError("CONSTRAINT", "solid.form 이 필요하다", path="/form")
    out = {"form": form}
    if base and base.get("form") == form:
        for k in _SO_KEYS[form]:
            if k in base:
                out[k] = base[k]
    for k in _SO_KEYS[form]:
        v = getattr(a, k)
        if v is None:
            continue
        if k in ("z0", "z1", "thickness"):
            out[k] = float(v)
        elif k == "line":
            out[k] = [float(x) for x in v]
        elif k in ("start", "end", "base"):
            out[k] = [float(x) for x in v]
        elif k in ("pitch", "dir"):
            out[k] = float(v)
        elif k == "holes":
            if v:
                out[k] = [[[float(c) for c in q] for q in h] for h in v]
            else:
                out.pop(k, None)
        elif k == "section":
            out[k] = {"shape": v.shape, **{n: float(getattr(v, n)) for n in ("width", "depth", "web", "flange", "wall") if getattr(v, n) is not None}}
        elif k == "anchor":
            out[k] = v
        elif k in ("polygon", "profile", "path", "vertices"):
            out[k] = [[float(c) for c in q] for q in v]
        elif k == "sections":
            out[k] = [{"z": float(s_.z), "polygon": [[float(c) for c in q] for q in s_.polygon]} for s_ in v]
        elif k == "faces":
            out[k] = [list(f) for f in v]
        elif k == "openings":
            if v:
                out[k] = _openings_in(v)
            else:
                out.pop(k, None)
    for k in ("polygon", "z0", "z1", "sections", "line", "thickness", "profile", "path", "vertices", "faces", "openings", "start", "end", "section", "anchor", "holes", "base", "pitch", "dir"):
        if k not in _SO_KEYS[form] and getattr(a, k) is not None:
            raise DobimError("CONSTRAINT", f"{k} 는 form={form} 에 쓰지 않는다", path=f"/{k}")
    if form == "member":
        out.setdefault("anchor", "top")
    if form == "plate":
        out.setdefault("pitch", 0.0)
        out.setdefault("dir", 0.0)
        if base and base.get("form") == "plate" and "grid" in base:   # 천장 격자(W1 1-F 3차)는 ceiling.set 이 고친다
            out["grid"] = base["grid"]
    return out


def _solid_checked(p: dict) -> dict:
    why = K.row_params_error("solid", p)
    if why:
        raise DobimError("CONSTRAINT", f"솔리드가 올바르지 않다: {why}")
    return p


class SolidCreate(SolidGeom):
    id: Id | None = None
    level: Id = Field(description="놓인 레벨(형상의 z 는 이 레벨 높이 기준)")
    category: SoCategory = Field("proxy", description="부재 범주(IFC 분류를 정한다): " + ", ".join(f"{k}={v[0]}" + (f"({v[1]})" if v[1] else "") for k, v in K.SO.SOLID_CATEGORIES.items()))
    name: Name | None = None


@command("solid.create", kind="mutation", risk="write", group="model.solid",
         summary="일반 솔리드 생성(돌출 prism·단면 잇기 loft·수직 판 panel(+아치 개구부)·스윕 sweep·메시 mesh). 범주가 IFC 클래스를 정한다(IfcWall·IfcRoof·IfcColumn·IfcCovering 등). IFC 는 IfcFacetedBrep",
         args=SolidCreate, since="0.5",
         examples=[{"level": F.LV1, "category": "buttress", "form": "prism", "polygon": [[0, -900], [600, -900], [600, -100], [0, -100]], "z0": 0, "z1": 2400},
                   {"level": F.LV2, "category": "spire", "form": "loft", "sections": [{"z": 0, "polygon": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]}, {"z": 3000, "polygon": [[500, 500]]}]},
                   {"level": F.LV2, "category": "beam", "form": "member", "start": [0, 0, 0], "end": [6000, 0, 0], "section": {"shape": "rect", "width": 400, "depth": 600}}])
def solid_create(ctx, a: SolidCreate):
    _fresh_id(ctx, a, "solid")
    ctx.authored(a.level, "level", what="레벨에 솔리드를 추가하는 것")
    if a.form is None:
        raise DobimError("CONSTRAINT", "solid.form 이 필요하다", path="/form")
    a.name = a.name or f"SO-{a.id[-6:]}"
    p = _solid_checked({"name": a.name, "category": a.category, **_solid_geom(a, None)})
    ctx.put_el(a.id, row("solid", K.SO.SOLID_CATEGORIES[a.category][0], "solid.generic", p))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id}


class SolidSet(SolidGeom):
    forbid_null = True
    id: Id
    name: Name | None = None
    category: SoCategory | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in ("name", "category", "form", "polygon", "z0", "z1", "sections", "line", "thickness", "profile", "openings", "path", "vertices", "faces", "start", "end", "section", "anchor", "holes", "base", "pitch", "dir")):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("solid.set", kind="mutation", risk="write", group="model.solid", summary="솔리드 속성 변경(이름·범주·형태·형상 필드). 형태를 바꾸면 그 형태의 필드를 모두 준다",
         args=SolidSet, since="0.5", examples=[{"id": F.SO1, "z1": 3000}])
def solid_set(ctx, a: SolidSet):
    r = ctx.authored(a.id, "solid")
    old = r["params"]
    p = {"name": a.name or old["name"], "category": a.category or old["category"], **_solid_geom(a, old)}
    p = _solid_checked(p)
    if K.SO.SOLID_CATEGORIES[p["category"]][0] != r["ifc_class"]:   # IFC 신원(클래스)은 만들 때 정해진다
        raise DobimError("CONSTRAINT", f"범주를 바꾸면 IFC 클래스가 달라진다({r['ifc_class']} -> {K.SO.SOLID_CATEGORIES[p['category']][0]})", path="/category",
                         hint="같은 IFC 클래스의 범주로 바꾸거나, 지우고 새로 만드세요")
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


class SolidMove(A):
    id: Id
    dx: Num = 0.0
    dy: Num = 0.0
    dz: Num = 0.0


def _solid_moved(p: dict, dx: float, dy: float, dz: float) -> dict:
    f = p["form"]
    q = dict(p)
    if f == "prism":
        q["polygon"] = [[x + dx, y + dy] for x, y in p["polygon"]]
        q["z0"], q["z1"] = p["z0"] + dz, p["z1"] + dz
    elif f == "loft":
        q["sections"] = [{"z": s_["z"] + dz, "polygon": [[x + dx, y + dy] for x, y in s_["polygon"]]} for s_ in p["sections"]]
    elif f == "panel":
        x1, y1, x2, y2 = p["line"]
        q["line"] = [x1 + dx, y1 + dy, x2 + dx, y2 + dy]
        q["profile"] = [[u, z + dz] for u, z in p["profile"]]
        if "openings" in p:
            q["openings"] = [dict(o, sill=o["sill"] + dz) for o in p["openings"]]
    elif f == "sweep":
        q["path"] = [[x + dx, y + dy] for x, y in p["path"]]
        q["profile"] = [[o, z + dz] for o, z in p["profile"]]
    elif f == "plate":
        q["polygon"] = [[x + dx, y + dy] for x, y in p["polygon"]]
        if "holes" in p:
            q["holes"] = [[[x + dx, y + dy] for x, y in h] for h in p["holes"]]
        q["base"] = [p["base"][0] + dx, p["base"][1] + dy, p["base"][2] + dz]
    elif f == "member":
        q["start"] = [p["start"][0] + dx, p["start"][1] + dy, p["start"][2] + dz]
        q["end"] = [p["end"][0] + dx, p["end"][1] + dy, p["end"][2] + dz]
    else:
        q["vertices"] = [[x + dx, y + dy, z + dz] for x, y, z in p["vertices"]]
    return q


@command("solid.move", kind="mutation", risk="write", group="model.solid", summary="솔리드를 평행 이동한다(dx, dy, dz)",
         args=SolidMove, since="0.5", examples=[{"id": F.SO1, "dx": 600, "dy": 0}])
def solid_move(ctx, a: SolidMove):
    r = ctx.authored(a.id, "solid")
    p = _solid_checked(_solid_moved(r["params"], float(a.dx), float(a.dy), float(a.dz)))
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


@command("solid.delete", kind="mutation", risk="write", group="model.solid", summary="솔리드 삭제",
         args=IdArg, since="0.5", examples=[{"id": F.SO1}])
def solid_delete(ctx, a: IdArg):
    ctx.authored(a.id, "solid", what="삭제")
    _delete_one(ctx, a.id)
    return {"deleted": [a.id]}


# ---------------------------------------------------------------- 대지 (설계: elements-design.md 9절)
class SiteRoad(A):
    name: Text = Field("", max_length=64, description="도로 이름(비워도 된다)")
    edge: int = Field(ge=0, le=255, description="도로가 접한 경계 변 번호(입력한 경계에서 꼭짓점 i 와 i+1 사이가 변 i, 마지막 변은 마지막 꼭짓점과 처음 꼭짓점 사이)")
    width: Pos = Field(description="도로 폭 mm")


SiteRoads = Annotated[list[SiteRoad], Field(max_length=8, description="대지에 접한 도로(변마다 하나)")]


# 14절: 대지의 선택 속성(지반 표고점·조경/공개공지 영역·주차 계획·간판). 없으면 행에 키가 없다(입력이 없는 프로젝트는 저장 형식·해시가 그대로)
SiteTerrain = Annotated[list[Annotated[list[Num], Field(min_length=3, max_length=3)]], Field(
    min_length=1, max_length=K.SITE_TERRAIN_MAX,
    description="지반 표고점 [[x,y,z],...] mm(최대 64). z 는 모델 기준 높이(1층 바닥 0, 아래는 음수). 단면의 지반선은 이 점들을 거리 역가중으로 보간한다")]


class SiteZone(A):
    use: Literal["landscape", "open_space"] = Field(description="landscape = 조경, open_space = 공개공지")
    name: Annotated[str, Field(max_length=64), AfterValidator(_text_ok)] = ""
    boundary: SlabBoundary = Field(description="영역 다각형 [[x,y],...] mm. 대지 경계 안(경계 위 포함)이어야 한다. 시계 방향이면 반시계로 저장")


SiteZones = Annotated[list[SiteZone], Field(min_length=1, max_length=K.SITE_ZONES_MAX, description="조경·공개공지 영역(최대 32). 배치도에 해치·면적 문자와 합계를 그린다")]
SitePath = Annotated[list[SlabPt], Field(min_length=2, max_length=K.PARK_PATH_PTS_MAX, description="꺾임점 [[x,y],...] (2~16개)")]


class ParkBay(A):
    origin: SlabPt = Field(description="배열 시작 모서리 [x,y] mm. 대지 안이어야 한다")
    angle: Num = Field(0.0, ge=-360.0, le=360.0, description="배열 방향 각(도, +x 가 0, 반시계). 주차면은 이 방향으로 나란히 놓이고 깊이는 그 왼쪽으로 뻗는다")
    count: int = Field(ge=1, le=K.PARK_COUNT_MAX, description="주차면 수")
    w: Pos = Field(2500.0, description="주차면 폭(배열 방향) mm")
    d: Pos = Field(5000.0, description="주차면 깊이 mm")
    accessible: bool = Field(False, description="참이면 장애인 전용 배열(대수를 따로 센다)")


class ParkAisle(A):
    path: SitePath
    width: Pos = Field(6000.0, description="차로 폭 mm")


class ParkRoute(A):
    path: SitePath = Field(description="진출입 동선 [[x,y],...]: 첫 점이 진입, 끝 점이 화살표 머리")


class SiteParking(A):
    bays: list[ParkBay] | None = Field(None, min_length=1, max_length=K.PARK_BAY_GROUPS_MAX, description="주차면 배열(규격 사각형을 나란히)")
    aisles: list[ParkAisle] | None = Field(None, min_length=1, max_length=K.PARK_PATHS_MAX, description="차로")
    routes: list[ParkRoute] | None = Field(None, min_length=1, max_length=K.PARK_PATHS_MAX, description="진출입 동선 화살표")

    @model_validator(mode="after")
    def _some(self):
        if self.bays is None and self.aisles is None and self.routes is None:
            raise ValueError("bays, aisles, routes 중 하나는 있어야 한다")
        return self


class SiteSign(A):
    name: Name
    kind: Literal["sign", "address"] = Field("sign", description="sign = 간판, address = 건물번호판")
    face: Literal["N", "E", "S", "W"] = Field(description="붙는 벽 면이 바라보는 방위(입면 뷰 dir 과 같은 면에 그려진다)")
    center: SlabPt = Field(description="면 위 중심의 평면 위치 [x,y] mm")
    z: Num = Field(description="아래 모서리 높이(모델 기준, 1층 바닥 0) mm")
    w: Pos = Field(description="폭 mm")
    h: Pos = Field(description="높이 mm")
    text: Annotated[str, Field(max_length=64), AfterValidator(_text_ok)] = ""


SiteSurvey = Annotated[list[Annotated[float, Field(allow_inf_nan=False, ge=-1e12, le=1e12)]], Field(min_length=2, max_length=2, description=(
    "측량 기준점(ANN-04): 프로젝트 원점(0,0)의 측량 좌표 [E, N] mm. 주면 평면·배치도 좌표 기호(spot_coord)가 정북 각(north_angle)으로 돌린 뒤 이 값을 더해 "
    "측량 좌표로 적는다. 없으면 프로젝트 원점 기준"))]
SiteSigns = Annotated[list[SiteSign], Field(min_length=1, max_length=K.SITE_SIGNS_MAX, description="간판·건물번호판(최대 32). 입면 뷰에 사각형과 크기 문자를 그린다")]


def _ccw(b):
    return b if polygon_area2(b) > 0 else b[::-1]


def _site_extras(terrain, zones, parking, signs, survey=None) -> dict:
    """대지 선택 속성 입력을 저장 모양(숫자 float, 영역은 반시계, accessible 은 참일 때만)으로 만든다. 주지 않은 것은 키가 없다."""
    out: dict = {}
    if terrain is not None:
        out["terrain"] = [[float(v) for v in q] for q in terrain]
    if zones is not None:
        out["zones"] = [{"use": z.use, "name": z.name, "boundary": _ccw([[float(x), float(y)] for x, y in z.boundary])} for z in zones]
    if parking is not None:
        pk: dict = {}
        if parking.bays is not None:
            pk["bays"] = [dict({"origin": [float(b.origin[0]), float(b.origin[1])], "angle": float(b.angle), "count": b.count, "w": float(b.w), "d": float(b.d)},
                               **({"accessible": True} if b.accessible else {})) for b in parking.bays]
        if parking.aisles is not None:
            pk["aisles"] = [{"path": [[float(x), float(y)] for x, y in a.path], "width": float(a.width)} for a in parking.aisles]
        if parking.routes is not None:
            pk["routes"] = [{"path": [[float(x), float(y)] for x, y in r.path]} for r in parking.routes]
        out["parking"] = pk
    if signs is not None:
        out["signs"] = [{"name": g.name, "kind": g.kind, "face": g.face, "center": [float(g.center[0]), float(g.center[1])], "z": float(g.z), "w": float(g.w),
                         "h": float(g.h), "text": g.text} for g in signs]
    if survey is not None:
        out["survey"] = [float(v) for v in survey]
    return out


def _site_check(params) -> None:
    """대지 행 검사(영역·주차면 시작점이 대지 안인지 등). 코어 행 검사와 같은 것이다."""
    why = K.row_params_error("site", params)
    if why:
        raise DobimError("CONSTRAINT", f"대지가 올바르지 않다: {why}")


def _site_geometry(boundary_in, roads_in) -> tuple[list, list]:
    """경계(반시계로 저장)와 도로를 정규화한다. 시계 방향 입력은 경계를 뒤집으므로 도로의 변 번호도 저장한 경계 기준으로 옮긴다."""
    b = [[float(x), float(y)] for x, y in boundary_in]
    why = polygon_problem(b)
    if why:
        raise DobimError("CONSTRAINT", f"대지 경계가 올바르지 않다: {why}", path="/boundary")
    n = len(b)
    flip = polygon_area2(b) <= 0
    roads, seen = [], set()
    for r in roads_in:
        if r.edge >= n:
            raise DobimError("CONSTRAINT", f"도로 변 번호가 경계 변 수({n})를 넘는다: {r.edge}", path="/roads")
        e = (n - 2 - r.edge) % n if flip else r.edge   # 뒤집은 경계에서 같은 변의 번호
        if e in seen:
            raise DobimError("CONSTRAINT", f"한 변에 도로를 둘 이상 줄 수 없다: 변 {r.edge}", path="/roads")
        seen.add(e)
        roads.append({"name": r.name, "edge": e, "width": r.width})
    roads.sort(key=lambda r: r["edge"])
    return (b[::-1] if flip else b), roads


class SiteCreate(A):
    id: Id | None = None
    boundary: SlabBoundary = Field(description="대지 경계 다각형 [[x,y],...] mm(3~256개, 자기 교차 금지). 시계 방향으로 주면 반시계로 뒤집어 저장한다")
    name: Name | None = None
    north_angle: Num = Field(0.0, ge=-360.0, le=360.0, description="정북이 도면 위쪽에서 시계방향으로 몇 도 돌아 있는가(0 이면 +y 가 정북)")
    roads: SiteRoads = Field(default_factory=list)
    elevation: Num | None = Field(None, description="대지 표고 mm(선택). IfcSite RefElevation 으로 실린다")
    terrain: SiteTerrain | None = None
    zones: SiteZones | None = None
    parking: SiteParking | None = None
    signs: SiteSigns | None = None
    survey: SiteSurvey | None = None


@command("site.create", kind="mutation", risk="write", group="model.site", summary="대지 생성(경계 다각형 + 방위 + 접한 도로)",
         args=SiteCreate, since="0.5",
         examples=[{"boundary": [[-6000, -6000], [18000, -6000], [18000, 14000], [-6000, 14000]], "name": "대지", "north_angle": 0,
                    "roads": [{"name": "남측 도로", "edge": 0, "width": 8000}]}])
def site_create(ctx, a: SiteCreate):
    _fresh_id(ctx, a, "site")
    boundary, roads = _site_geometry(a.boundary, a.roads)
    a.name = a.name or f"SITE-{a.id[-6:]}"
    params = {"name": a.name, "boundary": boundary, "north_angle": a.north_angle, "roads": roads, "elevation": a.elevation,
              **_site_extras(a.terrain, a.zones, a.parking, a.signs, a.survey)}
    _site_check(params)
    ctx.put_el(a.id, row("site", "IfcSite", "site.basic", params))
    return {"id": a.id}


class SiteSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    boundary: SlabBoundary | None = None
    north_angle: Num | None = Field(None, ge=-360.0, le=360.0)
    roads: SiteRoads | None = Field(None, description="주면 도로 목록을 통째로 바꾼다(빈 목록이면 도로 없음)")
    elevation: Num | None = None
    clear_elevation: bool = Field(False, description="참이면 대지 표고를 지운다")
    terrain: SiteTerrain | None = Field(None, description="주면 지반 표고점을 통째로 바꾼다")
    zones: SiteZones | None = Field(None, description="주면 조경·공개공지 영역을 통째로 바꾼다")
    parking: SiteParking | None = Field(None, description="주면 주차 계획을 통째로 바꾼다")
    signs: SiteSigns | None = Field(None, description="주면 간판·건물번호판을 통째로 바꾼다")
    survey: SiteSurvey | None = None
    clear: list[Literal["terrain", "zones", "parking", "signs", "survey"]] = Field(default_factory=list, description="지울 선택 속성(지반 표고점·영역·주차 계획·간판·측량 기준점)")

    @model_validator(mode="after")
    def _any(self):
        if (all(v is None for v in (self.name, self.boundary, self.north_angle, self.roads, self.elevation, self.terrain, self.zones, self.parking, self.signs,
                                    self.survey))
                and not self.clear_elevation and not self.clear):
            raise ValueError("바꿀 필드가 하나도 없다")
        if any(getattr(self, k) is not None for k in self.clear):
            raise ValueError("clear 로 지우는 속성을 함께 줄 수 없다")
        if self.clear_elevation and self.elevation is not None:
            raise ValueError("elevation 과 clear_elevation 을 함께 줄 수 없다")
        return self


@command("site.set", kind="mutation", risk="write", group="model.site", summary="대지 속성 변경(이름·경계·방위·도로·표고·지반 표고점·조경/공개공지 영역·주차 계획·간판)",
         args=SiteSet, since="0.5", examples=[{"id": F.SI1, "north_angle": 30},
                                              {"id": F.SI1, "terrain": [[0, 0, 0], [6000, 4000, 300]], "signs": [{"name": "S1", "face": "S", "center": [3000, 0], "z": 2200, "w": 2400, "h": 600}]}])
def site_set(ctx, a: SiteSet):
    r = ctx.authored(a.id, "site")
    p = dict(r["params"])
    for k in ("name", "north_angle", "elevation"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    if a.clear_elevation:
        p["elevation"] = None
    if a.boundary is not None or a.roads is not None:
        keep = [SiteRoad(name=x["name"], edge=x["edge"], width=x["width"]) for x in p["roads"]]
        p["boundary"], p["roads"] = _site_geometry(a.boundary if a.boundary is not None else p["boundary"], a.roads if a.roads is not None else keep)
    for k in a.clear:
        p.pop(k, None)
    p.update(_site_extras(a.terrain, a.zones, a.parking, a.signs, a.survey))
    _site_check(p)
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


@command("site.delete", kind="mutation", risk="write", group="model.site", summary="대지 삭제",
         args=IdArg, since="0.5", examples=[{"id": F.SI1}])
def site_delete(ctx, a: IdArg):
    ctx.authored(a.id, "site", what="삭제")
    _delete_one(ctx, a.id)
    return {"deleted": [a.id]}


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
    pparam_check(ctx, pparam_of(ctx.st, a.pset, a.prop), a.id, a.value)
    return {"changed": ctx.set_prop(a.id, a.pset, a.prop, a.value)}


def pparam_of(st, pset: str, prop: str) -> dict | None:
    """속성셋.속성에 걸린 프로젝트 매개변수 정의(DATA-03). 없으면 None(검사 없이 이전과 같다)."""
    for i in st.by_kind.get("pparam", ()):
        p = st.el[i]["params"]
        if p["pset"] == pset and p["name"] == prop:
            return p
    return None


def pparam_check(ctx, d: dict | None, eid: str, value) -> None:
    """프로젝트 매개변수가 정의된 속성을 쓸 때: 묶은 종류의 요소에만, 정의한 형식의 값만."""
    if d is None:
        return
    kind = ctx.st.el[eid]["kind"]
    if kind not in d["kinds"]:
        raise DobimError("CONSTRAINT", f"프로젝트 매개변수 {d['pset']}.{d['name']} 는 {', '.join(d['kinds'])} 에만 쓴다({kind})", path="/id",
                         hint="param.define 으로 묶을 종류를 바꾸세요")
    if not K.pparam_value_ok(d["type"], value):
        raise DobimError("CONSTRAINT", f"프로젝트 매개변수 {d['pset']}.{d['name']} 의 형식은 {d['type']} 다", path="/value")


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
        if "base_offset" in r["params"]:   # 바닥 오프셋(22단계): 레벨 기준 바닥·윗면 높이. 없으면 응답은 그대로
            out["derived"].update({"bottom_rel": WT.base(r["params"]), "top_rel": WT.top_rel(r["params"])})
        if "top_profile" in r["params"]:   # 상단 윤곽(21단계): 높이 범위·입면 면적·부피(개구부 빼기 전). 없으면 응답은 그대로
            tp, th = r["params"]["top_profile"], r["params"]["thickness"]
            gross = WT.area(tp, start, end) - WT.base(r["params"]) * (end - start)
            out["derived"].update({"top_min": WT.zmin(tp, start, end), "top_max": WT.zmax(tp, start, end),
                                   "gross_area_m2": gross / 1e6, "volume_m3": gross * th / 1e9})
        if "cuts" in r["params"]:   # 벽 잘라내기(23단계): 빼낸 부피(겹친 상자는 한 번만)와 상자 수. 없으면 응답은 그대로
            boxes = ctx.g.q(Q.wall_cut_boxes, a.id)
            out["derived"].update({"cut_boxes": len(boxes), "cut_volume_m3": WC.union_volume(boxes) / 1e9})
        xarea, xn = ctx.g.q(Q.wall_xcut_area, a.id)
        hosts_x = any(ctx.st.el[d]["params"].get("also_cuts") for d in ctx.rels_out("hosts", a.id) if d in ctx.st.el)
        if xn or hosts_x:   # 26단계: also_cuts 로 함께 잘린 벽(또는 그런 창의 호스트)만 순 면적·부피를 더한다. 없으면 응답은 그대로
            net = ctx.g.q(Q.wall_row, a.id)[7]
            th = r["params"]["thickness"]
            out["derived"].update({"xcut_openings": xn, "xcut_area_m2": xarea / 1e6, "xcut_volume_m3": xarea * th / 1e9,
                                   "net_area_m2": net / 1e6, "net_volume_m3": net * th / 1e9})
    elif r["kind"] == "door":
        out["derived"] = {"world": list(ctx.g.q(Q.door_world, a.id)), "status": ctx.g.q(Q.door_status, a.id)}
    elif r["kind"] == "slab":
        zb, zt = ctx.g.q(Q.slab_zrange, a.id)
        out["derived"] = {"bottom": zb, "top": zt}
        if r["params"].get("use") == "roof":   # 지붕 슬래브만 더해진다(일반 슬래브의 응답은 그대로)
            out["derived"].update({"roof": True, "drains": len(r["params"].get("drains", ()))})
        if "openings" in r["params"]:   # 구멍이 있으면 면적(㎡)은 구멍을 뺀 값이다(구멍 없는 슬래브의 응답은 그대로)
            gross = abs(polygon_area2(r["params"]["boundary"])) / 2.0 / 1e6
            holes = sum(abs(polygon_area2(o)) for o in r["params"]["openings"]) / 2.0 / 1e6
            out["derived"].update({"area_m2": gross - holes, "opening_area_m2": holes, "openings": len(r["params"]["openings"])})
    elif r["kind"] == "space":
        b = r["params"]["boundary"]
        out["derived"] = {"area_m2": abs(polygon_area2(b)) / 2.0 / 1e6, "corridor": is_corridor(r["params"]["name"], r["params"]["category"])}
        if "holes" in r["params"]:   # 섬 구멍이 있으면 면적(㎡)은 구멍을 뺀 값이다(구멍 없는 공간의 응답은 그대로)
            holes = sum(abs(polygon_area2(o)) for o in r["params"]["holes"]) / 2.0 / 1e6
            out["derived"].update({"area_m2": out["derived"]["area_m2"] - holes, "hole_area_m2": holes, "holes": len(r["params"]["holes"])})
    elif r["kind"] == "site":
        b = r["params"]["boundary"]
        out["derived"] = {"area_m2": abs(polygon_area2(b)) / 2.0 / 1e6, "roads": len(r["params"]["roads"])}
        if "zones" in r["params"]:   # 선택 속성이 있을 때만 더해진다(없으면 응답은 그대로)
            la, op = D.zone_totals(r["params"]["zones"])
            out["derived"].update({"landscape_m2": la, "open_space_m2": op})
        if "parking" in r["params"]:
            total, acc = D.parking_counts(r["params"]["parking"])
            out["derived"].update({"parking_bays": total, "parking_accessible": acc})
        if "signs" in r["params"]:
            out["derived"]["signs"] = len(r["params"]["signs"])
        if "terrain" in r["params"]:
            out["derived"]["terrain_points"] = len(r["params"]["terrain"])
    elif r["kind"] == "stair":
        zb, zt = ctx.g.q(Q.stair_zrange, a.id)
        p = r["params"]
        out["derived"] = {"bottom": zb, "top": zt, "riser_height": p["height"] / p["risers"],
                          "tread_depth": sqrt(_axis_len2(p["run"])) / (p["risers"] - 1)}
        if "turn" in p and "winders" in p["turn"]:   # 돌음 계단 수량(23.3): 직선 디딤판·쐐기 디딤판 수, 단 묶음별 챌판, 보행선 위 쐐기 디딤 길이
            lay = K.stair_layout(p)
            fa, fb = lay["flights"]
            out["derived"].update({"tread_depth": fa["td"], "flight_risers": [fa["n"], fb["n"]], "winder_treads": p["turn"]["winders"],
                                   "straight_treads": fa["n"] - 1 + fb["n"] - 1, "treads": p["risers"] - 1, "winder_walkline_tread": lay["winders"]["tread"]})
    elif r["kind"] == "elevator":
        gm = K.elevator_geom(r["params"])
        lv = ctx.g.q(Q.level_info, ctx.g.q(Q.elevator_level, a.id))[1]
        sx, sy = gm["shaft"][2] - gm["shaft"][0], gm["shaft"][3] - gm["shaft"][1]
        out["derived"] = {"bottom": lv + gm["z0"], "top": lv + gm["z1"], "shaft_area_m2": sx * sy / 1e6,
                          "cab_area_m2": r["params"]["cab"][0] * r["params"]["cab"][1] / 1e6}
        if "pit" in r["params"]:   # 선택 입력이 있을 때만 더해진다(없으면 응답은 그대로)
            out["derived"]["pit_bottom"] = lv + gm["pit_z0"]
        if "machine" in r["params"]:
            out["derived"]["machine_top"] = lv + gm["mach_z1"]
        if "stops" in r["params"]:
            out["derived"]["stops"] = len(r["params"]["stops"])
        if "hole_slabs" in r["params"]:
            out["derived"]["hole_slabs"] = len(r["params"]["hole_slabs"])
    elif r["kind"] == "roof":
        gm = K.roof_geom(r["params"])
        lv = ctx.g.q(Q.level_info, ctx.g.q(Q.roof_level, a.id))[1]
        out["derived"] = {"eave_low": lv + gm["zlow"], "top": lv + gm["ztop"], "outline_area_m2": abs(polygon_area2(gm["outline"])) / 1e6, "faces": len(gm["faces"])}
    elif r["kind"] == "railing":
        gm = K.railing_geom(r["params"])
        lv = ctx.g.q(Q.level_info, ctx.g.q(Q.railing_level, a.id))[1]
        pts = r["params"]["path"]
        out["derived"] = {"length": sum(sqrt((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) for i in range(len(pts) - 1)),
                          "start_top": lv + gm["top"][0][2], "end_top": lv + gm["top"][-1][2], "posts": len(gm["posts"])}
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
        if p["type"] == "plan" and not Q.plan_level_ok(st.el.get(p["level"])):
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
_PLAN_ONLY, _SEC_ONLY, _ELEV_ONLY, _SITE_ONLY = ("level", "cut_height", "view_depth"), ("p1", "p2", "depth", "flip", "with_site"), ("dir",), ()
# 선택 필드(없으면 키 자체가 없다): crop 은 평면·단면·입면, dims 는 평면·단면, finish 는 입면, with_site 는 단면(위 _SEC_ONLY). around·margin 은 만들 때만 받는 입력이다
_OPT_FIELDS = {"plan": ("crop", "dims"), "section": ("crop", "dims"), "elevation": ("crop", "finish"), "site": ()}
_OPT_ALL = ("crop", "dims", "finish")


class ViewCreate(A):
    id: Id | None = None
    name: Name
    type: Literal["plan", "section", "elevation", "site"] = Field(description="뷰 종류. site 는 배치도(모델 전체를 위에서 본 대지·건물 외곽·도로·방위·이격 거리)")
    level: Id | None = Field(None, description="평면: 레벨 id (필수)")
    cut_height: VCoord = Field(1200.0, description="평면: 레벨 바닥에서 절단면까지 mm (±1e7)")
    view_depth: VCoord = Field(300.0, ge=0, description="평면: 절단면 아래로 투영선을 보일 깊이 mm (0..1e7)")
    p1: Point2 | None = Field(None, description="단면: 절단선 시작점 (필수)")
    p2: Point2 | None = Field(None, description="단면: 절단선 끝점 (필수)")
    depth: VCoord = Field(20000.0, gt=0, description="단면: 절단면 뒤로 보이는 거리 mm (0 초과 1e7 이하)")
    flip: bool = Field(False, description="단면: 거짓이면 절단선 진행 방향의 왼쪽을 본다, 참이면 오른쪽")
    with_site: bool = Field(False, description="단면: 참이면 대지 단면(대지 경계 안쪽의 지반선·GL·대지 경계 표시를 건물 단면과 함께 그린다). 거짓이면 키 자체가 없다")
    dir: Literal["N", "E", "S", "W"] | None = Field(None, description="입면: 건물을 바라보는 면의 방위 (N = 북측 입면, 바깥에서 남쪽을 봄)")
    scale: Scale = 100
    crop: Annotated[list[VCoord], Field(min_length=4, max_length=4)] | None = Field(None, description="부위 상세: 뷰를 이 사각형 [x1, y1, x2, y2] 안으로 자른다(뷰 좌표 mm: 평면은 모델 x,y, 단면·입면은 가로 s·높이 z). 선·채움·문자를 사각형으로 잘라 그린다. 없으면 키 자체가 없다")
    dims: bool = Field(False, description="평면·단면: 참이면 뷰에 보이는 계단(폭·디딤판·챌판·층참)과 승강기(샤프트·카 치수, 단면은 카 높이·오버헤드·피트)의 치수를 자동으로 표기한다. 거짓이면 키 자체가 없다")
    finish: bool = Field(False, description="입면: 참이면 외부 마감(IsExternal 이 참인 벽의 재료)의 해치와 재료 문자를 그린다. 거짓이면 그리지 않는다(입면 관행: 벽 전체에 해치를 깔지 않는다). 거짓이면 키 자체가 없다")
    around: Id | None = Field(None, description="부위 상세 편의: 계단·승강기 id. 주면 그 요소의 뷰 안 범위 + margin 으로 crop 을 정해 저장한다(요소 참조는 저장하지 않는다: 이후 요소를 옮겨도 crop 은 그대로). crop 과 함께 못 쓴다")
    margin: VCoord = Field(1000.0, ge=0, description="around 와 함께: 요소 범위 둘레 여유 mm(0..1e7)")

    @model_validator(mode="after")
    def _by_type(self):
        given = self.model_fields_set
        own = {"plan": _PLAN_ONLY, "section": _SEC_ONLY, "elevation": _ELEV_ONLY, "site": _SITE_ONLY}[self.type]
        # 기본값과 같은 값은 쓰지 않은 것으로 본다: 로그에는 기본값까지 적혀 있어(log_args) 단면·입면 뷰의 로그를 다시 실행하면
        # cut_height 같은 기본값이 '쓰지 않는 필드'로 거절됐다(S5 의 전체 재생 검사가 찾은 S3 결함)
        stray = sorted(f for f in (given & {*_PLAN_ONLY, *_SEC_ONLY, *_ELEV_ONLY, *_OPT_ALL}) - set(own) - set(_OPT_FIELDS[self.type])
                       if getattr(self, f) != type(self).model_fields[f].default)
        if self.around is not None and self.crop is not None:
            raise ValueError("around 와 crop 은 함께 쓸 수 없다")
        if self.around is not None and self.type not in ("plan", "section", "elevation"):
            raise ValueError("around 는 평면·단면·입면 뷰에만 쓴다")
        if self.crop is not None and not (self.crop[0] < self.crop[2] and self.crop[1] < self.crop[3]):
            raise ValueError("crop 은 [x1, y1, x2, y2] 이고 x1<x2, y1<y2 여야 한다")
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
                   {"name": "대지 종단면", "type": "section", "p1": [3000, -4000], "p2": [3000, 8000], "with_site": True, "scale": 200},
                   {"name": "남측 입면", "type": "elevation", "dir": "S", "scale": 50},
                   {"name": "1F 평면 계단·승강기 치수", "type": "plan", "level": F.LV1, "scale": 20, "crop": [0, 0, 3000, 3000], "dims": True},
                   {"name": "남측 입면 마감", "type": "elevation", "dir": "S", "finish": True}])
def view_create(ctx, a: ViewCreate):
    _fresh_id(ctx, a, "view")
    _view_name_free(ctx, a.name)
    if a.type == "plan":
        if not Q.plan_level_ok(ctx.st.el.get(a.level)):   # 레벨 또는 외래 IFC 층(1단계 외래 도면 경로)
            raise DobimError("NOT_FOUND", f"level 를 찾을 수 없다: {a.level}", path="/level")
        params = {"name": a.name, "type": "plan", "level": a.level, "cut_height": a.cut_height, "view_depth": a.view_depth, "scale": a.scale}
    elif a.type == "section":
        params = {"name": a.name, "type": "section", "p1": list(a.p1), "p2": list(a.p2), "depth": a.depth, "flip": a.flip, "scale": a.scale}
        if a.with_site:
            params["with_site"] = True
    elif a.type == "site":
        params = {"name": a.name, "type": "site", "scale": a.scale}
    else:
        params = {"name": a.name, "type": "elevation", "dir": a.dir, "scale": a.scale}
    crop = list(a.crop) if a.crop is not None else None
    if a.around is not None:   # 부위 상세 편의: 요소의 뷰 안 범위 + 여유로 잘라내기 사각형을 정해 저장한다(요소 참조는 남기지 않는다)
        crop = _around_crop(ctx, a.around, params, a.margin)
    if crop is not None:
        params["crop"] = crop
    if a.dims:
        params["dims"] = True
    if a.finish:
        params["finish"] = True
    ctx.put_el(a.id, row("view", "", "view.basic", params))
    return {"id": a.id, **({"crop": crop} if a.around is not None else {})}


def _around_crop(ctx, eid, params, margin) -> list:
    """계단·승강기 eid 가 뷰(params) 안에서 차지하는 범위 + margin 의 잘라내기 사각형 [x1,y1,x2,y2]."""
    r = ctx.row(eid)
    if r["kind"] not in ("stair", "elevator"):
        raise DobimError("CONSTRAINT", f"around 는 계단·승강기 id 여야 한다: {r['kind']}", path="/around")
    if params["type"] == "plan":
        kind, cfg = "plan", None
    else:
        kind, cfg = "sec", D.cfg_from_params(params)
    ext = D.detail_extent(ctx.fresh(), eid, kind, cfg)
    if ext is None:
        raise DobimError("CONSTRAINT", "저작 계단·승강기만 범위를 잴 수 있다", path="/around")
    return [round(ext[0] - margin, 3), round(ext[1] - margin, 3), round(ext[2] + margin, 3), round(ext[3] + margin, 3)]


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
    with_site: bool | None = Field(None, description="단면: 참이면 대지 단면으로, 거짓이면 일반 단면으로 되돌린다")
    dir: Literal["N", "E", "S", "W"] | None = None
    scale: Scale | None = None
    crop: Annotated[list[VCoord], Field(min_length=4, max_length=4)] | None = Field(None, description="부위 상세 잘라내기 사각형 [x1, y1, x2, y2](뷰 좌표 mm)")
    clear_crop: bool | None = Field(None, description="참이면 잘라내기를 없앤다(crop 키 삭제)")
    dims: bool | None = Field(None, description="평면·단면: 참이면 계단·승강기 치수 자동 표기를 켠다, 거짓이면 끈다(키 삭제)")
    finish: bool | None = Field(None, description="입면: 참이면 외부 마감 해치·재료 문자를 켠다, 거짓이면 끈다(키 삭제)")

    @model_validator(mode="after")
    def _any(self):
        if len(self.model_fields_set - {"id"}) == 0:
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.crop is not None and (self.clear_crop or not (self.crop[0] < self.crop[2] and self.crop[1] < self.crop[3])):
            raise ValueError("crop 은 [x1, y1, x2, y2](x1<x2, y1<y2)이고 clear_crop 과 함께 못 쓴다")
        return self


@command("view.set", kind="mutation", risk="write", group="view", summary="뷰 설정을 바꾼다",
         args=ViewSet, since="0.1", examples=[{"id": F.V1, "cut_height": 1500}, {"id": F.V1, "dims": True, "crop": [0, 0, 4000, 4000]}])
def view_set(ctx, a: ViewSet):
    r = ctx.row(a.id, "view")
    p = dict(r["params"])
    allowed = {"plan": _PLAN_ONLY, "section": _SEC_ONLY, "elevation": _ELEV_ONLY, "site": _SITE_ONLY, "legend": (), "drafting": ()}[p["type"]]
    for k in ("name", "scale", "cut_height", "view_depth", *_SEC_ONLY, *_ELEV_ONLY):
        v = getattr(a, k)
        if v is None:
            continue
        if k not in ("name", "scale") and k not in allowed:
            raise DobimError("CONSTRAINT", f"{p['type']} 뷰에는 쓰지 않는 필드다: {k}", path=f"/{k}")
        if k == "with_site":   # 거짓이면 키를 지운다(일반 단면의 저장 형식 그대로)
            if v:
                p[k] = True
            else:
                p.pop(k, None)
            continue
        p[k] = list(v) if k in ("p1", "p2") else v
    if a.name is not None:
        _view_name_free(ctx, a.name, a.id)
    for k in ("crop", "dims", "finish"):   # 선택 옵션: 거짓·없애기는 키를 지운다(옵션이 없는 뷰의 저장 형식 그대로)
        v = getattr(a, k)
        if v is None and not (k == "crop" and a.clear_crop):
            continue
        if k not in K.VIEW_OPTIONAL[p["type"]]:
            raise DobimError("CONSTRAINT", f"{p['type']} 뷰에는 쓰지 않는 필드다: {k}", path=f"/{k}")
        if k == "crop" and a.clear_crop:
            if "parent" in p:   # 콜아웃 뷰(W1 1-C 2차): 잘라내기가 곧 콜아웃 범위다
                raise DobimError("CONSTRAINT", "콜아웃 뷰의 잘라내기는 없앨 수 없다(범위는 crop 으로 바꾼다)", path="/clear_crop")
            p.pop(k, None)
        elif k == "crop":
            p[k] = list(v)
        elif v:
            p[k] = True
        else:
            p.pop(k, None)
    if p["type"] == "section" and _seg_len2(p["p1"], p["p2"]) < 1.0:
        raise DobimError("CONSTRAINT", "절단선 길이는 1mm 이상이어야 한다", path="/p2")
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


@command("view.delete", kind="mutation", risk="write", group="view", summary="뷰를 지운다",
         args=IdArg, since="0.1", examples=[{"id": F.V1}])
def view_delete(ctx, a: IdArg):
    ctx.row(a.id, "view")
    used = sorted(sid for sid in ctx.st.by_kind.get("sheet", ()) if a.id in ctx.st.el[sid]["params"]["views"] or ctx.st.el[sid]["params"]["key_plan"] == a.id)
    if used:   # 시트 정의가 이 뷰를 올리고 있다: 조용히 시트를 고치거나 지우지 않는다
        raise DobimError("CONSTRAINT", f"시트가 이 뷰를 쓴다: {', '.join(used)}", path="/id", hint="sheet.define 으로 시트에서 뷰를 빼거나 sheet.delete 로 시트를 지우세요")
    kids = sorted(v for v in ctx.st.by_kind.get("view", ()) if ctx.st.el[v]["params"].get("parent") == a.id)
    if kids:   # 콜아웃 뷰(W1 1-C 2차)가 이 뷰를 부모로 둔다
        raise DobimError("CONSTRAINT", f"콜아웃 뷰가 이 뷰를 부모로 쓴다: {', '.join(kids)}", path="/id", hint="콜아웃 뷰를 먼저 지우세요")
    gone = _cascade_view_annots(ctx, a.id)
    ctx.put_el(a.id, None)
    return {"deleted": [a.id, *gone]}


class ViewList(A):
    type: Literal["plan", "section", "elevation", "site", "legend", "drafting"] | None = None
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


PngMaxPx = Annotated[int, Field(ge=64, le=4096, description="PNG 의 긴 변 화소 상한(기본 1600). 큰 시트도 이 안으로 줄인다. .png 일 때만 쓴다")]
PngCrop = Annotated[list[Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]], Field(
    min_length=4, max_length=4, description="PNG 로 잘라 낼 영역: 용지 비율 [x0, y0, x1, y1](0~1, 왼쪽 위가 0,0). 비우면 전체. 잘라 내면 그 영역에 max_px 를 쓴다")]


class ViewExport(A):
    id: Id
    path: str = Field(max_length=260, description="작업 폴더 안의 .svg·.pdf·.png·.dxf 경로 (확장자가 형식을 정한다). 응답에는 경로·크기·요약만 담는다. DXF 는 모델 mm 실척(문자 높이·파선은 뷰 축척을 곱한 값), R2013 UTF-8, 범주별 층이다. PDF 는 한글 글꼴(시스템의 Noto Sans KR·나눔고딕, OFL)을 부분집합으로 내장한다(못 찾으면 Helvetica 로 내려가 한글이 물음표가 되고 경고한다). PNG 는 순수 파이썬 래스터(같은 입력이면 같은 바이트)")
    overwrite: bool = Field(False, description="거짓이면 이미 있는 파일을 덮어쓰지 않는다. 참이면 덮어쓰며 덮어쓰기 전에 자동 스냅숏을 남긴다(이 커맨드는 destructive 라 확인 토큰이 필요하다)")
    max_px: PngMaxPx = 1600
    crop: PngCrop | None = None


def _warn_omitted(ctx, omitted: dict) -> None:
    """도면이 그리지 못한 외래 요소·분리된 벽이 있으면 조용히 넘기지 않고 알린다(S2 × S3)."""
    if omitted["foreign"] or omitted["detached"]:
        ctx.warn(f"이 뷰는 외래 요소 {omitted['foreign']}개와 분리된 벽 {omitted['detached']}개를 그리지 않았다"
                 "(외래는 IFC 형상이 없거나 그리지 않는 종류(공간·개구부 등)이고, 분리된 벽의 의도 값은 낡았다)")


def _warn_fsym(ctx, vid) -> None:
    """외래 문·창의 여닫이 방식을 몰라 기본 기호로 그렸으면 알린다(외래 도면 3차)."""
    n = ctx.g.q(D.foreign_symbol_defaults, vid)
    if n:
        ctx.warn(f"이 뷰는 외래 문·창 {n}개의 여닫이 방식(IfcDoor OperationType·IfcWindow 칸 나눔·창짝 방식)을 몰라 기본 기호"
                 "(문: 왼쪽 외여닫이, 창: 칸 하나)로 그렸다. IFC 의 방식 값을 확인하라")


def _render_view(ctx, vid, detail=False, hidden=False, hier=False):
    """뷰의 선분·채움·기준선(레벨)·제목을 내보내기용 데이터로 묶는다. detail 이면 큰 축척(1:50 이하) 상세 선을 더한다(SC-09).
    hier 이면 비내력벽(LoadBearing 거짓)의 절단선을 "cutn" 등급으로 나눈다(LN-04, 선 굵기 위계)."""
    r = ctx.row(vid, "view")
    p = r["params"]
    levels = sorted([(ctx.st.el[l]["params"]["name"], ctx.st.el[l]["params"]["elevation"]) for l in ctx.st.by_kind.get("level", ())]
                    + list(ctx.g.q(D.foreign_datums)), key=lambda t: (t[1], t[0]))   # 외래 IFC 층의 기준선(외래 도면 2차): 없으면 이전과 같다
    from dobim_core import viewvg as VG
    vgd = ctx.g.q(VG.vg_view, vid, hier)
    if vgd is not None:   # 가시성·그래픽 재정의·필터·템플릿(W1 1-C): 설정이 없는 뷰는 아래 원래 경로(바이트 불변)
        return _render_view_vg(ctx, vid, p, levels, vgd, detail, hidden)
    lines = ctx.g.q(D.view_lines_hier if hier else D.view_lines, vid)
    if detail:
        lines = lines + ctx.g.q(D.view_detail_lines, vid)
    if hidden:
        lines = lines + ctx.g.q(D.view_hidden_lines, vid)
    _warn_omitted(ctx, ctx.g.q(D.view_omitted, vid))
    _warn_fsym(ctx, vid)
    return {"type": p["type"], "name": p["name"], "scale": p["scale"], "lines": lines, "fills": ctx.g.q(D.view_fills, vid),
            "datums": levels, "annots": ctx.g.q(AN.view_annots, vid) + ctx.g.q(D.site_dims, vid) + ctx.g.q(D.view_dims, vid), "labels": ctx.g.q(D.view_labels, vid),
            **_drafts_of(ctx, vid), **_mode_of(p)}


MODEL_LESS_VIEWS = ("legend", "drafting")   # 모델을 자르지 않는 뷰 종류(범례 뷰 W1 1-C 3차, 제도 뷰 W1 연장 VIEW-07)


def _mode_of(p) -> dict:
    """반사 천장 평면·면적 평면(W1 1-C 3차)의 제목 종류. 모드가 없는 뷰는 키를 만들지 않는다(기존 도면 바이트 불변)."""
    return {"mode": "rcp"} if p.get("rcp") else {"mode": "area"} if "area" in p else {}


def _render_view_vg(ctx, vid, p, levels, vgd, detail, hidden):
    """재정의가 걸린 뷰의 내보내기 데이터(viewvg.vg_view). 재정의 항목은 2D 작도 항목과 같은 모양이라 drafts 앞에 둔다(마스킹은 그 위를 가린다)."""
    from dobim_core import drafting as DRF
    from dobim_core import viewvg as VG
    lines, fills, labels, vitems, hid, _n = vgd
    if detail:
        lines = lines + ctx.g.q(D.view_detail_lines, vid)
    if hidden:
        lines = lines + ctx.g.q(D.view_hidden_lines, vid)
    _warn_omitted(ctx, ctx.g.q(D.view_omitted, vid))
    _warn_fsym(ctx, vid)
    annots = tuple(it for it in ctx.g.q(AN.view_annots, vid) + ctx.g.q(D.site_dims, vid) + ctx.g.q(D.view_dims, vid) if VG.annot_kind(it) not in hid)
    items = vitems + (() if "draft" in hid else ctx.g.q(DRF.view_drafts, vid))
    out = {"type": p["type"], "name": p["name"], "scale": p["scale"], "lines": lines, "fills": fills, "datums": levels, "annots": annots, "labels": labels, **_mode_of(p)}
    return dict(out, drafts=items) if items else out


def _drafts_of(ctx, vid) -> dict:
    """뷰의 2D 작도 그림 항목(27단계). 2D 요소가 없으면 키를 만들지 않는다(기존 도면 바이트 불변)."""
    from dobim_core import drafting as DRF
    items = ctx.g.q(DRF.view_drafts, vid)
    return {"drafts": items} if items else {}


def png_of(ctx, sh, a) -> dict:
    """시트·뷰 페이지의 그림 요소를 PNG 로 그린다(.png 내보내기와 view.image·sheet.image 가 함께 쓴다). 글꼴이 없으면 글자 자리만 그리고 경고한다."""
    from dobim_core import raster as RA
    try:
        rr = RA.render(sh.prims, sh.w, sh.h, max_px=a.max_px, crop=a.crop)
    except RA.RasterError as ex:
        raise DobimError("CONSTRAINT", str(ex), path="/crop" if a.crop is not None else "/max_px")
    if rr["font"] is None:
        ctx.warn("한글 글꼴(Noto Sans KR·나눔고딕)을 찾지 못해 PNG 의 글자를 칸 모양으로만 그렸다. 글꼴을 설치하거나 환경 변수 DOBIM_PDF_FONT 에 .ttf 경로를 주세요")
    return rr


def _view_export_target(ctx, a: ViewExport):
    """내보내기 대상 검사(미리보기와 실행이 같이 쓴다): 작업 폴더 안, .svg/.pdf, 스냅숏 폴더·폴더·대체 데이터 스트림(:) 아님, 덮어쓰기 동의."""
    p = resolve_in_workspace(ctx.eng.workspace, a.path)
    ext = p.suffix.lower()
    if ext not in (".svg", ".pdf", ".png", ".dxf"):
        raise DobimError("PATH_DENIED", f"내보내기 대상은 .svg·.pdf·.png·.dxf 여야 한다: {p.name}", path="/path")
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
         summary="SVG·PDF·PNG·DXF 내보내기",   # commands.search 가 응답 상한 안에 들도록 짧게(destructive 는 risk 에 나온다)
         args=ViewExport, since="0.1", preview=_view_export_preview,
         examples=[{"id": F.V1, "path": "view-example.svg", "overwrite": True}])
def view_export(ctx, a: ViewExport):
    p, ext = _view_export_target(ctx, a)
    data = _render_view(ctx, a.id)
    dropped, font_name, px, dxf = False, None, None, None
    if ext == ".svg":
        blob = DX.render_svg(data)
    elif ext == ".dxf":
        from dobim_core import dxf_export as DXF
        from dobim_core import sheet as SH
        blob, dxf = DXF.render_dxf(SH.build_view_page(data), model=DXF.view_model_xform(data), captions=(DX.title_text(data),))
    elif ext == ".png":
        from dobim_core import sheet as SH
        rr = png_of(ctx, SH.build_view_page(data), a)
        blob, px = rr["png"], [rr["width"], rr["height"]]
        font_name = rr["font"]
    else:
        from dobim_core import pdf_font as PF
        font = PF.load_pdf_font()                  # 시트와 같은 글꼴 내장 경로: 한글이 '?' 가 되지 않게
        blob, dropped = DX.render_pdf(data, font)
        font_name = font.name if font else None
        if font is None:
            ctx.warn("한글 글꼴(Noto Sans KR·나눔고딕)을 찾지 못해 PDF 의 한글이 '?' 로 나왔다. 글꼴을 설치하거나 환경 변수 DOBIM_PDF_FONT 에 .ttf 경로를 주세요")
        elif font.missing:
            ctx.warn(f"글꼴에 없는 글자 {len(font.missing)}개는 빈칸으로 나왔다: {''.join(font.missing[:20])}")
    snap = _snapshot_existing(p, ctx.eng.workspace)
    try:
        _replace(p, lambda tmp: tmp.write_bytes(blob))
    except BaseException:   # 쓰지 못했으면 방금 만든 스냅숏은 필요 없다(재시도마다 쌓이지 않게)
        _drop_snapshot(p, snap, ctx.eng.workspace)
        raise
    out = {"path": p.name, "bytes": len(blob), "format": ext[1:], "segments": len(data["lines"]), "fills": len(data["fills"]), "annotations": len(data["annots"]), "labels": len(data["labels"]),
           "font_embedded": font_name, "non_ascii_dropped": dropped, "digest": D.view_summary(data["lines"])["digest"], "rev": ctx.eng.rev, "snapshot": snap}
    if px is not None:
        out["pixels"] = px
    if dxf is not None:
        out["dxf"] = dxf
    return out


class SheetSigner(A):
    role: Name = Field(description="서명 역할(예: 설계자, 관계전문기술자)")
    name: Text = Field("", description="성명(비우면 빈칸)")


class SheetRevision(A):
    rev: Name = Field(description="개정 차수(예: 1, A)")
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="개정일 YYYY-MM-DD")
    desc: Text = Field(description="개정 내용")
    view: Id | None = Field(None, description="구름 표시로 둘러쌀 뷰(시트에 올린 뷰). 생략하면 개정란에만 적는다")


class SheetGrid(A):
    label: Name = Field(max_length=4, description="통심선 번호·기호(예: X1, Y1)")
    axis: Literal["x", "y"] = Field(description="x: 세로선(x=pos), y: 가로선(y=pos)")
    pos: Num = Field(description="통심선 위치(모델 mm)")


class SheetCallout(A):
    view: Id = Field(description="상세 표시를 둘 뷰(시트에 올린 뷰)")
    rect: list[Num] = Field(min_length=4, max_length=4, description="상세 부위 [x0, y0, x1, y1](모델 mm)")
    label: Name = Field(max_length=4, description="상세 부호")


PaperMm = Annotated[float, Field(ge=0.0, le=1200.0, allow_inf_nan=False)]


class SheetViewport(A):
    """뷰포트(W1 1-C 2차)"""
    view: Id = Field(description="뷰(views 안)")
    at: list[PaperMm] | None = Field(None, min_length=2, max_length=2, description="틀 왼쪽 위 [x, y] 용지 mm")
    number: Name | None = Field(None, max_length=8, description="상세 번호(기호 머리 참조)")
    title: Name | None = Field(None, description="제목(생략=뷰 이름)")


# 시트 용지(W1 1-E, IO-17): A0~A4 가로, 끝에 P 를 붙이면 세로. 크기는 sheet.PAPERS
Paper = Literal["A0", "A1", "A2", "A3", "A4", "A0P", "A1P", "A2P", "A3P", "A4P"]
assert get_args(Paper) == K.PAPER_NAMES, "용지 이름은 kinds.PAPER_NAMES 와 같아야 한다"


class SheetFrame(A):
    """sheet.export·sheet.index 가 함께 쓰는 표제란·개정·선 굵기 입력. 모두 선택이며, 주지 않으면 시트는 이전과 같다."""
    client: Text = Field("", description="표제란 건축주(주면 건축주·설계사무소 영역이 생긴다)")
    office: Text = Field("", description="표제란 설계사무소")
    signers: list[SheetSigner] = Field(default_factory=list, max_length=6, description="서명·날인 영역(역할·성명 줄). 서명·인은 빈칸으로 둔다")
    revisions: list[SheetRevision] = Field(default_factory=list, max_length=20, description="개정 이력(개정란에 적고, view 가 있으면 구름 표시)")
    line_profile: dict[Literal["cut", "proj", "symbol", "hatch"], Annotated[float, Field(ge=0.05, le=2.0, allow_inf_nan=False)]] | None = Field(
        None, description="별도 작성기준 선 굵기 프로파일(mm): 선 종류별로 바꾼다. 생략하면 기본 체계")


TextHeightKey = Literal["dimension", "caption", "datum", "tag", "room_name", "room_area", "fire_mark"]


class SheetProfile(A):
    """별도 작성기준 프로파일(B-09): 문자 높이·치수 형식·기호 크기. 모두 선택이며, 주지 않은 값은 기본 체계다. 시트 파일에만 적용한다(저장하는 시트 정의에는 넣지 않는다)."""
    text_height: dict[TextHeightKey, Annotated[float, Field(ge=1.0, le=10.0, allow_inf_nan=False)]] | None = Field(
        None, description="문자 높이(용지 mm). dimension 치수 문자(기본 2.5), caption 뷰 도면명(4.0, 축척 문자는 7/8), datum 레벨 문자(2.6), tag 부호 태그(2.5), "
                          "room_name 실명(2.5), room_area 면적(2.0), fire_mark 방화 표기(2.0)")
    dim_decimals: Annotated[int, Field(ge=0, le=3)] | None = Field(None, description="치수 문자의 소수 자릿수(생략하면 값 그대로: 정수면 정수)")
    dim_thousands: bool | None = Field(None, description="참이면 네 자리 이상 숫자에 세 자리마다 쉼표(DM-10)")
    symbol_scale: Annotated[float, Field(ge=0.5, le=2.0, allow_inf_nan=False)] | None = Field(None, description="기호 크기 배율(그리드·단면·상세 원, 부호 태그 원·글자). 생략하면 1")
    hidden_lines: bool | None = Field(None, description="참이면 단면·입면 뷰에 숨은 선(앞의 벽·기둥·슬래브·계단에 가려진 모서리와 개구부 윤곽)을 파선으로 더한다(LN-03). 평면·배치도는 없다")
    line_hierarchy: bool | None = Field(None, description="참이면 선 굵기 위계를 적용한다(LN-04): 절단선 > 창호·기호선 = 치수선 > 보이는 선(입면·마감) > 가는선·해칭. 기본 체계는 치수선이 보이는 선보다 가늘다")
    projection: Literal["third"] | None = Field(None, description="투상법. third 면 표제란에 '제3각법' 칸과 기호를 넣는다(T-11)")


def site_north(ctx) -> float:
    """시트 표제란 방위표의 기본 각: 모델의 대지(id 순 첫 번째)의 정북 각, 대지가 없으면 0. 배치도 방위표(site_info 의 north)와 같은 값이다."""
    g = ctx.fresh()   # 변경 커맨드(sheet.define)에서도 같은 배치의 앞 대지 입력이 보이도록 기준 그래프로 읽는다
    sites = g.q(D.all_sites)
    return float(g.q(D.site_info, sites[0])[2]) if sites else 0.0


# 인쇄 설정(W1 1-F 5차, IO-17): 다 만든 시트를 인쇄 용지에 옮길 때의 여백·축척 맞춤. 시트 배치·표기 축척은 그대로다.
# sheet.render 의 선택 인자다(sheet.export 인자에는 넣지 않는다: sheet.export describe 가 C-4 응답 크기 측정의 가장 큰 describe 가 되지 않게).
# 클래스 설명 문장은 describe 에 실리므로 주석으로 둔다.
class PrintSetup(A):
    paper: Paper | None = Field(None, description="인쇄 용지(생략=시트 용지)")
    margin: Annotated[float, Field(ge=0.0, le=50.0, allow_inf_nan=False)] = Field(5.0, description="네 변 여백 mm")
    fit: bool = Field(True, description="참=여백 안에 맞춰 확대·축소(축척 맞춤), 거짓=실제 크기(안 들어가면 거절)")


class SheetExport(SheetFrame):
    views: list[Id] = Field(min_length=1, max_length=12, description="시트에 놓을 뷰 id 목록(앞에서부터 왼쪽 위 -> 오른쪽 -> 아래 순). 뷰는 자기 축척 그대로 놓인다")
    path: str = Field(max_length=260, description="작업 폴더 안의 .svg·.pdf·.png·.dxf 경로. PDF 는 한글 글꼴(시스템의 Noto Sans KR·나눔고딕, OFL)을 부분집합으로 내장한다. PNG 는 max_px·crop 을 쓴다. DXF 는 R2013(AC1027, UTF-8) 이며 단위 mm, 층은 범주별(A-CUT 절단선·A-DIM 치수 등), 한글 문자는 글꼴 스타일 DOBIM_KR(malgun.ttf), 치수는 분해한 선과 문자다(용지 mm 1:1, 뷰는 자기 축척)")
    paper: Paper = Field("A3", description="용지 A0~A4(가로, 끝에 P 면 세로)")
    project_name: Text = Field("", description="표제란 프로젝트명(비우면 '(프로젝트명 없음)')")
    sheet_name: Text = Field("", description="표제란 도면명(비우면 첫 뷰 이름)")
    sheet_number: Text = Field("A-001", description="표제란 도면번호")
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="작성일 YYYY-MM-DD(생략하면 오늘 날짜)")
    north_angle: Num | None = Field(None, ge=-360.0, le=360.0, description="정북이 도면 위쪽에서 시계방향으로 몇 도 돌아 있는가(0 이면 도면 위가 정북). 생략하면 모델에 대지가 있을 때 대지의 정북 각(site.north_angle: 배치도 방위표와 같은 값, 진북과 도북의 불일치 방지), 없으면 0")
    overwrite: bool = Field(False, description="거짓이면 이미 있는 파일을 덮어쓰지 않는다. 참이면 덮어쓰며 덮어쓰기 전에 자동 스냅숏을 남긴다(확인 토큰 필요)")
    grids: list[SheetGrid] = Field(default_factory=list, max_length=40, description="통심선(1점쇄선)과 그리드 기호를 평면 뷰에 그린다")
    callouts: list[SheetCallout] = Field(default_factory=list, max_length=12, description="부위 상세 표시(1점쇄선 사각형 + 부호)")
    marks: bool = Field(False, description="참이면 모델의 단면 뷰(절단선 기호)와 입면 뷰(방향 기호)를 평면 뷰에 기호로 그린다")
    tags: list[Literal["room", "opening", "wall"]] = Field(default_factory=list, max_length=3, description="평면 뷰에 부호 태그를 자동으로 단다: room 실번호, opening 문·창 부호, wall 벽체 부호")
    key_plan: Id | None = Field(None, description="키 플랜으로 쓸 평면 뷰(표제란 오른쪽 위에 줄여 그리고 해당 영역을 해칭한다)")
    key_zone: list[Num] | None = Field(None, min_length=4, max_length=4, description="키 플랜에서 해칭할 영역 [x0, y0, x1, y1](모델 mm). 생략하면 전체")
    dims: list[Literal["elevation", "corridor", "stair"]] = Field(default_factory=list, max_length=3, description=(
        "시트 치수 자동 배치(옵션): elevation 입면 뷰 아래에 벽 중심선 치수 사슬과 전체 폭(E-04), corridor 평면의 복도(분류·실명이 복도인 공간) 폭, "
        "stair 평면의 계단 폭(L-09)"))
    detail: bool = Field(False, description="참이면 큰 축척(1:50 이하) 뷰에 상세 표현을 더한다(재료가 있는 벽의 마감선·문짝 두께·문턱선). 1:100 이상의 작은 축척 뷰는 그대로")
    profile: SheetProfile | None = Field(None, description="별도 작성기준 프로파일: 문자 높이·치수 형식(소수·천단위 쉼표)·기호 크기. 생략하면 기본 체계(시트는 이전과 바이트까지 같다)")
    viewports: list[SheetViewport] | None = Field(None, min_length=1, max_length=12, description="뷰포트(위치·상세 번호·제목). 생략=이전과 같다")
    max_px: PngMaxPx = 1600
    crop: PngCrop | None = None


def _sheet_preview(ctx, a: SheetExport):
    for v in a.views:
        ctx.row(v, "view")   # 없는 뷰는 토큰을 내주기 전에 거절한다
    p, ext = _view_export_target(ctx, a)
    _snap_dir(p, ctx.eng.workspace)
    return {"will_write": p.name, "format": ext[1:], "paper": a.paper, "views": len(a.views), "overwrites": p.exists(), "snapshot_before_overwrite": p.exists()}


def build_sheet_for(ctx, a, sheet_id=None):
    """시트 인자(SheetExport 모양)로 시트(그림 요소 목록)와 spec 을 만든다. sheet.export 와 sheet.image 가 함께 쓴다.
    sheet_id = 저장된 시트(sheet.render·sheet.image id): 그 시트를 싣는 프로젝트 리비전도 개정란에 적는다(W1 1-B 3차)."""
    import datetime

    from dobim_core import sheet as SH
    if len(set(a.views)) != len(a.views):
        raise DobimError("CONSTRAINT", "같은 뷰를 두 번 올릴 수 없다", path="/views")
    from dobim_core import viewref as VR
    if (err := VR.viewports_error(a.views, a.viewports)) is not None:   # 뷰포트(W1 1-C 2차)
        raise DobimError("CONSTRAINT", err, path="/viewports")
    hidden = bool(a.profile is not None and a.profile.hidden_lines)
    hier = bool(a.profile is not None and a.profile.line_hierarchy)
    datas = [dict(_render_view(ctx, v, a.detail, hidden, hier), id=v) for v in a.views]
    spec = {"paper": a.paper, "project": a.project_name or "(프로젝트명 없음)", "title": a.sheet_name or datas[0]["name"],
            "number": a.sheet_number, "date": a.date or datetime.date.today().isoformat(),
            "north_deg": a.north_angle if a.north_angle is not None else site_north(ctx)}
    from dobim_core import sheet_extras as SE
    spec.update(SE.frame_spec(a))
    from dobim_core import commands_revision as CR
    if mrev := CR.sheet_revision_rows(ctx.st, sheet_id, a.views):   # 프로젝트 리비전(ANN-17): 없으면 spec 은 이전과 같다
        spec["revisions"] = list(spec.get("revisions") or []) + mrev
    spec.update(SE.profile_spec(a))
    spec.update(SE.view_extras(ctx, a, datas))
    VR.sheet_spec(ctx, a, sheet_id, spec)   # 뷰포트·뷰 참조·콜아웃 뷰 기호(W1 1-C 2차): 입력이 없으면 spec 은 이전과 같다
    try:
        sh = SH.build_sheet(spec, datas)
    except SH.SheetError as ex:
        raise DobimError("CONSTRAINT", str(ex), path="/views")
    return sh, spec


@command("sheet.export", kind="session", risk="destructive", group="view",
         summary="시트(표제란)로 SVG·PDF·PNG·DXF 내보내기",
         args=SheetExport, since="0.2", preview=_sheet_preview,
         examples=[{"views": [F.V1], "path": "sheet-example.svg", "paper": "A3", "project_name": "예시 프로젝트", "sheet_number": "A-101", "overwrite": True}])
def sheet_export(ctx, a: SheetExport):
    """뷰를 A1·A3 시트에 놓고 윤곽선·표제란(프로젝트명·도면명·도면번호·축척·작성일·개정란·축척 막대·방위표)을 그려 내보낸다."""
    return export_sheet(ctx, a)


def print_sheet(ctx, sh, paper, ext, print_setup: PrintSetup | None, label: str = ""):
    """인쇄 설정(IO-17, sheet.render·sheet.render_set): 시트를 인쇄 용지로 옮긴다(주지 않으면 그대로). 반환: (시트, 인쇄 정보 또는 None)"""
    if print_setup is None:
        return sh, None
    from dobim_core import sheet as SH
    if ext == ".dxf":
        raise DobimError("CONSTRAINT", "DXF 는 용지 mm 1:1 로 내므로 인쇄 설정(print)을 받지 않는다", path="/print", hint="SVG·PDF·PNG 로 내보내세요")
    try:
        sh, pinfo = SH.print_layout(sh, print_setup.paper or paper, float(print_setup.margin), print_setup.fit)
    except SH.SheetError as ex:
        raise DobimError("CONSTRAINT", f"{label}{ex}", path="/print") from None
    if abs(pinfo["zoom"] - 100.0) > 1e-9:
        ctx.warn(f"{label}축척 맞춤으로 {pinfo['zoom']:g}% 로 인쇄했다: 도면의 축척 표기와 축척 막대는 시트 용지 기준이라 인쇄물의 실제 축척과 다르다")
    return sh, pinfo


def export_sheet(ctx, a: SheetExport, sheet_id=None, print_setup: PrintSetup | None = None):
    """sheet.export·sheet.render 의 본체. sheet_id = 저장된 시트(그 시트를 싣는 리비전을 개정란에 적는다)."""
    from dobim_core import pdf_font as PF
    from dobim_core import sheet as SH
    from dobim_core import sheet_extras as SE
    p, ext = _view_export_target(ctx, a)
    sh, spec = build_sheet_for(ctx, a, sheet_id)
    sh, pinfo = print_sheet(ctx, sh, a.paper, ext, print_setup)
    dropped, font_name, px, dxf = False, None, None, None
    if ext == ".svg":
        blob = SH.render_sheet_svg(sh, spec)
    elif ext == ".dxf":
        from dobim_core import dxf_export as DXF
        blob, dxf = DXF.render_dxf(sh, style=SH._style(spec))
    elif ext == ".png":
        rr = png_of(ctx, sh, a)
        blob, px, font_name = rr["png"], [rr["width"], rr["height"]], rr["font"]
    else:
        font = PF.load_pdf_font()
        blob, dropped = SH.render_sheet_pdf(sh, spec, font)
        font_name = font.name if font else None
        if font is None:
            ctx.warn("한글 글꼴(Noto Sans KR·나눔고딕)을 찾지 못해 PDF 의 한글이 '?' 로 나왔다. 글꼴을 설치하거나 환경 변수 DOBIM_PDF_FONT 에 .ttf 경로를 주세요")
        elif font.missing:
            ctx.warn(f"글꼴에 없는 글자 {len(font.missing)}개는 빈칸으로 나왔다: {''.join(font.missing[:20])}")
    snap = _snapshot_existing(p, ctx.eng.workspace)
    try:
        _replace(p, lambda tmp: tmp.write_bytes(blob))
    except BaseException:
        _drop_snapshot(p, snap, ctx.eng.workspace)
        raise
    out = {"path": p.name, "bytes": len(blob), "format": ext[1:], "paper": a.paper, "views": sh.placed, "sheet_number": a.sheet_number,
           "number_check": SE.number_check(a.sheet_number, p.stem),
           "font_embedded": font_name, "non_ascii_dropped": dropped, "rev": ctx.eng.rev, "snapshot": snap}
    if pinfo is not None:
        out["print"] = pinfo
    if px is not None:
        out["pixels"] = px
    if dxf is not None:
        out["dxf"] = dxf
    return out


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
RefStr = Annotated[str, Field(pattern=r"^(wall|door|level|slab|space|grid|finst):[0-9A-Za-z_$]{22}#[a-z]{3,9}(\.[a-z]{3,9}){0,2}$", max_length=48,
                              description="안정 참조 '종류:id#역할'. wall: axis.start axis.end(분할 뒤에도 원래 끝) own.end(이 벽 자신의 끝) trim.start trim.end own.trim.end axis face.left face.right base top arc arc.left arc.right(곡선 벽의 축·면 호: 호 길이·반지름 치수) / door: center jamb.start jamb.end sill head / level: elevation / slab: top bottom / space: ceiling(천장 마감 높이, 공간에 ceiling_height 가 있을 때) / grid: line start end(통심선) / finst: center jamb.start jamb.end(호스팅 창 개구부 양 끝) base circle(자립 기둥의 원 단면, 반지름·지름 치수)")]
Off = Annotated[float, Field(allow_inf_nan=False, ge=-1e5, le=1e5)]
_ANNOT_KINDS = ("dim", "tag")


def _ref_parts(ctx, ref, field):
    """참조 문법·요소 존재·역할을 검사해 (종류, id, 역할)을 돌려준다."""
    parts = AN.parse_ref(ref)
    if parts is None:
        kind = ref.split(":", 1)[0]
        roles = ", ".join(list(AN.ROLE_TYPES.get(kind, {})) + (list(AN.ARC_ROLES) if kind == "wall" else [])) or "wall, door, level"
        raise DobimError("CONSTRAINT", f"없는 참조 역할이다: {ref[:40]}", path=field, hint=f"{kind} 의 역할: {roles}")
    ctx.authored(parts[1], parts[0], what="치수 대상(낡은 형상을 재게 된다)")
    return parts


def _level_of(ctx, kind, eid):
    """주석 대상이 놓인 레벨. 통심선은 레벨이 없다(None: 모든 평면 뷰에 보인다)."""
    if kind == "level":
        return eid
    if kind == "grid":
        return None
    if kind in ("door", "finst"):
        h = ctx.rels_in("hosts", eid)
        eid = h[0] if h else eid   # 자립 패밀리 인스턴스는 레벨에 직접 선다
    return ctx.rels_out("on_level", eid)[0]


def _dim_checked(ctx, view, a_ref, b_ref, at, measure=None, along=None):
    """치수 정의를 검사하고 (높이 치수인지, 해석 결과 둘, 기준 그래프)를 돌려준다. 지금 메모리 상태를 직접 읽는다.
    measure(W1 1-B 2차): angle = 평행하지 않은 선 참조 둘, radius·diameter = 같은 원 참조 하나(a = b). 모두 평면 치수다.
    along(W1 연장 ANN-02): 연속·기준선 치수의 한 칸. 정한 방향으로 투영해 재고, 선 참조는 그 방향에 수직이어야 한다(평면 치수만)."""
    vr = ctx.row(view, "view")
    pa, pb = _ref_parts(ctx, a_ref, "/a"), _ref_parts(ctx, b_ref, "/b")
    ta, tb = AN.role_type(pa[0], pa[2]), AN.role_type(pb[0], pb[2])
    vertical = ta == "z" and tb == "z"
    if (ta == "z") != (tb == "z"):
        raise DobimError("CONSTRAINT", "높이 참조와 평면 참조는 한 치수에 섞을 수 없다", path="/b")
    if measure == "angle" and (ta, tb) != ("line", "line"):
        raise DobimError("CONSTRAINT", "각도 치수는 선 참조 두 개를 잰다", path="/b", hint="선 역할: wall axis·face.left·face.right, grid line")
    if measure in ("radius", "diameter") and (ta not in ("circle", "arc") or a_ref != b_ref):
        raise DobimError("CONSTRAINT", "반지름·지름 치수는 원 참조 하나를 잰다(b 는 생략하거나 a 와 같게)", path="/a",
                         hint="원 역할: finst:<자립 기둥 id>#circle(원 단면이 있는 기둥), wall:<곡선 벽 id>#arc·arc.left·arc.right")
    if measure == "arc" and (ta != "arc" or a_ref != b_ref):
        raise DobimError("CONSTRAINT", "호 길이 치수는 곡선 벽의 호 참조 하나를 잰다(b 는 생략하거나 a 와 같게)", path="/a",
                         hint="호 역할: wall:<곡선 벽 id>#arc(축), #arc.left(왼쪽 면), #arc.right(오른쪽 면)")
    if measure is None and ("circle" in (ta, tb) or "arc" in (ta, tb)):
        raise DobimError("CONSTRAINT", "원·호 참조는 반지름·지름·호 길이 치수(measure)에만 쓴다", path="/a")
    for pr, fld in ((pa, "/a"), (pb, "/b")):   # 곡선 벽(W2 ARC-04)의 선 역할(축선·면선)은 곧은 선이 아니라 선형·각도 치수에 쓰지 않는다
        if pr[0] == "wall" and AN.role_type(pr[0], pr[2]) == "line" and "arc" in ctx.st.el.get(pr[1], {}).get("params", {}):
            raise DobimError("CONSTRAINT", "곡선 벽의 축선·면은 곧은 선이 아니다", path=fld,
                             hint="곡선 벽은 호 역할(arc·arc.left·arc.right)로 호 길이·반지름 치수를 재거나, 끝점(trim.start·trim.end)을 잰다")
    vt = vr["params"]["type"]
    if vt in MODEL_LESS_VIEWS:
        raise DobimError("CONSTRAINT", "모델이 없는 뷰(범례·제도 뷰)에는 모델 치수를 두지 않는다", path="/view",
                         hint="제도 뷰의 치수는 draft.create 의 dim 계열 모양으로 그린다")
    if vertical and vt == "plan":
        raise DobimError("CONSTRAINT", "높이 치수는 단면·입면 뷰에 둔다", path="/view")
    if not vertical and vt != "plan":
        raise DobimError("CONSTRAINT", "평면 치수는 평면 뷰에 둔다", path="/view")
    if not vertical:
        for pr, fld in ((pa, "/a"), (pb, "/b")):
            lv = _level_of(ctx, pr[0], pr[1])
            if lv is not None and lv != vr["params"]["level"]:
                raise DobimError("CONSTRAINT", "치수 대상이 이 평면 뷰의 레벨에 있지 않다", path=fld)
    if at is not None and not vertical:
        raise DobimError("CONSTRAINT", "at 은 높이 치수에만 쓴다", path="/at")
    g = ctx.fresh()
    ra, rb = g.q(AN.ref_resolve, a_ref), g.q(AN.ref_resolve, b_ref)
    for r_, fld in ((ra, "/a"), (rb, "/b")):
        if r_[0] == "orphan":     # 요소는 있어도 끝 역할이 분할 후계 삭제로 고아일 수 있다
            raise DobimError("CONSTRAINT", f"참조를 풀 수 없다: {r_[1]}", path=fld)
    if along is not None and vertical:
        raise DobimError("CONSTRAINT", "투영 방향(along)은 평면 치수에만 쓴다", path="/along")
    st, val = AN.measure(ra, rb, measure) if along is None else AN.measure_along(ra, rb, along)
    if st == "invalid" and val == "skew":
        raise DobimError("CONSTRAINT", "선 참조가 재는 방향에 수직이 아니다", path="/b",
                         hint="연속·기준선 치수의 선 참조(벽 면·축선, 통심선)는 서로 평행하고 재는 방향(along)에 수직이어야 한다")
    if st != "ok":
        raise DobimError("CONSTRAINT", f"잴 수 없는 참조 쌍이다: {val}", path="/b",
                         hint="선 참조 두 개는 평행해야 한다" if val == "not_parallel" else ("각도 치수의 두 선은 평행하지 않아야 한다" if val == "parallel" else None))
    if measure is not None and AN.more_geometry({"measure": measure, "off": 600.0}, ra, rb, val) is None:
        raise DobimError("CONSTRAINT", "두 선의 교점이 너무 멀다(거의 평행)", path="/b")
    return vertical, ra, rb, g


def _dim_home(g, p, ra, rb):
    """치수의 놓인 자리(고아·무효 표시 위치): 선형·높이는 재는 두 점의 중점, 각도·반지름·지름은 글 위치."""
    if "measure" in p:
        return list(AN.more_geometry(p, ra, rb, AN.measure(ra, rb, p["measure"])[1])[3])
    if "along" in p:   # 연속·기준선 치수의 한 칸: 치수선 위 글 위치
        return list(AN.along_geometry(p, ra, rb)[3])
    (ax, ay), (bx, by) = AN.dim_ends(g, p, ra, rb)
    return [round((ax + bx) / 2, 3), round((ay + by) / 2, 3)]


def _dir_fp(ctx, ref):
    """참조가 가리키는 벽 축선의 방향 지문(방향에 의존하는 역할일 때만, 아니면 None)."""
    kind, eid, role = AN.parse_ref(ref)
    if not AN.uses_direction(kind, role):
        return None
    if kind == "grid":
        return AN.unit_dir(ctx.st.el[eid]["params"]["line"])
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
    b: RefStr | None = Field(None, description="둘째 참조. 반지름·지름 치수는 생략한다(a 와 같다)")
    offset: Off = Field(600.0, description="치수선 간격 mm (재는 방향 왼쪽이 +). 각도 치수는 호의 반지름(음수면 맞꼭지각 쪽)")
    at: Point2 | None = Field(None, description="높이 치수: 치수선을 그릴 평면 위치 [x,y] (생략하면 [0,0])")
    measure: Literal["angle", "radius", "diameter", "arc"] | None = Field(
        None, description="생략 = 선형(거리). angle = 두 선 사이 각도(도), radius·diameter = 자립 기둥 원 단면(finst:<id>#circle) 또는 곡선 벽 호(wall:<id>#arc·arc.left·arc.right)의 반지름·지름, "
                          "arc = 곡선 벽 호의 호 길이(조인 정리 범위). 모두 모델 연관이라 요소를 옮기거나 바꾸면 따라간다")
    name: Name | None = None


@command("annot.dim.create", kind="mutation", risk="write", group="annot",
         summary="연관 치수 생성",
         args=DimCreate, since="0.1",
         examples=[{"view": F.V1, "a": f"wall:{F.W1}#trim.start", "b": f"wall:{F.W1}#trim.end"}])
def annot_dim_create(ctx, a: DimCreate):
    _fresh_id(ctx, a, "dim")
    b = a.b if a.b is not None else (a.a if a.measure in ("radius", "diameter", "arc") else None)
    if b is None:
        raise DobimError("CONSTRAINT", "b 가 필요하다(반지름·지름·호 길이 치수만 생략한다)", path="/b")
    vertical, ra, rb, g = _dim_checked(ctx, a.view, a.a, b, a.at, a.measure)
    params = {"name": a.name or f"DIM-{a.id[-6:]}", "view": a.view, "a": a.a, "b": b, "off": a.offset, "home": [0.0, 0.0],
              "dirs": {"a": _dir_fp(ctx, a.a), "b": _dir_fp(ctx, b)}}
    if vertical:
        params["at"] = list(a.at or [0.0, 0.0])
    if a.measure is not None:   # 선형 치수 행에는 키가 없다(이전과 같다)
        params["measure"] = a.measure
    params["home"] = _dim_home(g, params, ra, rb)
    return _new_annot(ctx, a, "dim", params)


# ---------------------------------------------------------------- 높이 치수 자동 배치 (법정 별표 2: 건축물 높이·각 층 높이·반자높이)
HEIGHT_KINDS = ("floor", "clear", "overall")
HEIGHT_OFF = {"floor": 600.0, "clear": 1300.0, "overall": 2000.0}   # 치수선이 대상에서 왼쪽으로 떨어지는 거리(mm). 안쪽부터 층고·반자·전체 순
HEIGHT_GAP = 500.0                                                  # 뷰 그림의 왼쪽 끝에서 보조선 기준선까지의 여유(mm)
HEIGHT_PREFIX = "H-"                                                # 자동 높이 치수의 이름 접두사: 다시 실행하면 이 접두사의 치수를 지우고 다시 만든다
_ID_ALPHA = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


def _det_id(*parts) -> str:
    """부분 문자열들에서 결정적으로 얻는 22자 id(IFC GlobalId 와 같은 알파벳, 첫 글자 0~3). 같은 입력은 어느 PC·프로세스에서나 같은 id 다."""
    import hashlib
    n = int.from_bytes(hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()[:16], "big")
    out = []
    for _ in range(22):
        out.append(_ID_ALPHA[n & 63])
        n >>= 6
    return "".join(reversed(out))


class DimHeights(A):
    view: Id = Field(description="단면·입면 뷰")
    kinds: Annotated[list[Literal["floor", "clear", "overall"]], Field(
        min_length=1, max_length=3,
        description="floor=각 층의 높이(층고: 이웃한 레벨 사이), clear=반자높이(레벨에서 그 레벨 공간의 가장 낮은 천장 마감 높이(space.ceiling_height)까지. 천장 높이 입력이 없으면 그 위 가장 가까운 슬래브 밑면으로 근사), "
                    "overall=건축물 최고 높이(가장 낮은 레벨에서 벽·슬래브의 가장 높은 윗면)")] = ["floor", "clear", "overall"]
    at: Point2 | None = Field(None, description="치수선을 그릴 평면 위치 [x,y]. 생략하면 뷰 그림의 왼쪽 바깥에 자동으로 둔다")


@command("annot.dim.heights", kind="mutation", risk="write", group="annot",
         summary="단면·입면에 높이 치수(층고·반자높이·최고 높이)를 자동 배치한다. 다시 실행하면 이전 자동 치수를 지우고 다시 만든다",
         args=DimHeights, since="0.2", examples=[{"view": F.V2, "kinds": ["floor", "overall"]}])
def annot_dim_heights(ctx, a: DimHeights):
    vr = ctx.row(a.view, "view")
    if vr["params"]["type"] in ("plan",) + MODEL_LESS_VIEWS:
        raise DobimError("CONSTRAINT", "높이 치수는 단면·입면 뷰에 둔다", path="/view")
    g = ctx.fresh()
    st = ctx.st
    levels = sorted(((st.el[i]["params"]["elevation"], i) for i in st.by_kind.get("level", ()) if st.el[i]["origin"] == "authored"))
    if not levels:
        raise DobimError("CONSTRAINT", "높이 치수를 만들 레벨이 없다", path="/view", hint="level.create 로 레벨을 먼저 만드세요")
    walls = [(i, g.q(AN.ref_resolve, f"wall:{i}#top")[1]) for i in sorted(st.by_kind.get("wall", ())) if st.el[i]["origin"] == "authored"]
    slabs = [(i, g.q(Q.slab_zrange, i)) for i in sorted(st.by_kind.get("slab", ())) if st.el[i]["origin"] == "authored"]
    plan: list = []   # [(종류, 이름, a 참조, b 참조)]
    if "floor" in a.kinds:
        for (e0, l0), (e1, l1) in zip(levels, levels[1:]):
            if e1 > e0:
                plan.append(("floor", f"{HEIGHT_PREFIX}층고-{st.el[l0]['params']['name']}", f"level:{l0}#elevation", f"level:{l1}#elevation"))
    if "clear" in a.kinds:
        for e0, l0 in levels:
            # 천장 높이 입력(space.ceiling_height)이 있는 공간이 그 레벨에 있으면 가장 낮은 천장 마감까지를 반자높이로 한다(없으면 슬래브 밑면 근사 그대로)
            ceil = sorted((st.el[i]["params"]["ceiling_height"], i) for i in ctx.rels_in("on_level", l0)
                          if st.el[i]["kind"] == "space" and st.el[i]["origin"] == "authored" and "ceiling_height" in st.el[i]["params"])
            above = sorted((zr[0], i) for i, zr in slabs if zr[0] > e0 + 1.0)   # 레벨 위의 슬래브 중 밑면이 가장 낮은 것(자기 바닥 슬래브는 밑면이 레벨 아래라 빠진다)
            if ceil:
                plan.append(("clear", f"{HEIGHT_PREFIX}반자-{st.el[l0]['params']['name']}", f"level:{l0}#elevation", f"space:{ceil[0][1]}#ceiling"))
            elif above:
                plan.append(("clear", f"{HEIGHT_PREFIX}반자-{st.el[l0]['params']['name']}", f"level:{l0}#elevation", f"slab:{above[0][1]}#bottom"))
    if "overall" in a.kinds:
        tops = [(z, "wall", i) for i, z in walls] + [(zr[1], "slab", i) for i, zr in slabs]
        if tops:
            ztop, kind, tid = sorted(tops, key=lambda t: (-t[0], t[1] != "wall", t[2]))[0]   # 같은 높이면 벽을 우선한다
            low_e, low_l = levels[0]
            if ztop > low_e:
                plan.append(("overall", f"{HEIGHT_PREFIX}최고높이", f"level:{low_l}#elevation", f"{kind}:{tid}#top"))
    removed = []
    for an in sorted(ctx.rels_in("annot_of", a.view)):   # 이전 자동 치수는 지운다(같은 id 로 다시 만든다)
        r = st.el[an]
        if r["kind"] == "dim" and r["params"]["name"].startswith(HEIGHT_PREFIX):
            _delete_one(ctx, an)
            removed.append(an)
    if not plan:
        ctx.warn("만들 높이 치수가 없다(레벨이 하나뿐이거나 벽·슬래브가 없다)")
        return {"view": a.view, "created": [], "removed": removed, "dims": []}
    if a.at is not None:
        at = list(a.at)
    else:
        cfg = g.q(D.view_cfg, a.view)
        lines = g.q(D.view_lines, a.view)
        s0 = min((min(ln[1], ln[3]) for ln in lines), default=0.0) - HEIGHT_GAP
        at = [round(cfg[1] + s0 * cfg[5], 3), round(cfg[2] + s0 * cfg[6], 3)]   # 보는 방향 기준 s 가 s0 인 평면 위치(뷰의 오른쪽 방향 = cfg[5], cfg[6])
    out = []
    for kind, name, ra, rb in plan:
        did = _det_id("annot.dim.heights", a.view, name)
        r = annot_dim_create(ctx, DimCreate(id=did, view=a.view, a=ra, b=rb, offset=HEIGHT_OFF[kind], at=at, name=name))
        val = AN.measure(g.q(AN.ref_resolve, ra), g.q(AN.ref_resolve, rb))
        out.append({"id": r["id"], "kind": kind, "name": name, "a": ra, "b": rb, "value": val[1] if val[0] == "ok" else None})
    return {"view": a.view, "created": [d["id"] for d in out], "removed": removed, "dims": out}


class TagCreate(A):
    id: Id | None = None
    view: Id = Field(description="태그를 둘 평면 뷰")
    target: Id = Field(description="벽·문·슬래브·공간·계단·패밀리 인스턴스(창·기둥 등)·지붕·난간. 지붕·난간은 다중 범주 필드(name·level·material·ifc_class)만")
    fields: Annotated[list[Annotated[str, Field(max_length=16)]], Field(min_length=1, max_length=4)] = ["name"]
    offset: Annotated[list[Off], Field(min_length=2, max_length=2)] = [400.0, 400.0]
    name: Name | None = None


def _tag_checked(ctx, view, target, fields):
    vr = ctx.row(view, "view")
    tr = ctx.authored(target, what="태그 대상(낡은 값을 보이게 된다)")
    if vr["params"]["type"] != "plan":
        raise DobimError("CONSTRAINT", "태그는 평면 뷰에 둔다", path="/view")
    if tr["kind"] not in AN.TAG_FIELDS:
        raise DobimError("CONSTRAINT", f"태그는 {'·'.join(AN.TAG_FIELDS)} 에만 단다: {tr['kind']}", path="/target")
    bad = [f for f in fields if f not in AN.TAG_ALLOWED[tr["kind"]]]
    if bad:
        raise DobimError("CONSTRAINT", f"{tr['kind']} 에 없는 표시 필드: {', '.join(bad)}", path="/fields",
                         hint="가능: " + ", ".join(AN.TAG_ALLOWED[tr["kind"]]))
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


TagKind = Literal["wall", "door", "slab", "space", "stair", "finst", "roof", "railing"]


class TagAll(A):
    view: Id = Field(description="태그를 둘 평면 뷰")
    kinds: Annotated[list[TagKind], Field(min_length=1, max_length=8)] = Field(
        description="태그할 범주(여러 개면 다중 범주 태그). finst = 패밀리 인스턴스(창·기둥 등)")
    fields: Annotated[list[Annotated[str, Field(max_length=16)]], Field(min_length=1, max_length=4)] = Field(
        ["name"], description="표시 필드. 여러 범주에 쓰려면 모든 범주에 있는 필드(name·level·material·ifc_class)")
    offset: Annotated[list[Off], Field(min_length=2, max_length=2)] = [400.0, 400.0]


@command("annot.tag.all", kind="mutation", risk="write", group="annot",
         summary="평면 뷰 레벨의 아직 태그가 없는 요소에 범주별로 태그를 한꺼번에 단다(다중 범주 태그)",
         args=TagAll, since="0.7", examples=[{"view": F.V1, "kinds": ["wall", "door"], "fields": ["name", "ifc_class"]}])
def annot_tag_all(ctx, a: TagAll):
    vr = ctx.row(a.view, "view")
    if vr["params"]["type"] != "plan":
        raise DobimError("CONSTRAINT", "태그는 평면 뷰에 둔다", path="/view")
    kinds = sorted(set(a.kinds))
    for k in kinds:
        bad = [f for f in a.fields if f not in AN.TAG_ALLOWED[k]]
        if bad:
            raise DobimError("CONSTRAINT", f"{k} 에 없는 표시 필드: {', '.join(bad)}", path="/fields",
                             hint="여러 범주에 함께 쓰는 필드: " + ", ".join(AN.MULTI_FIELDS))
    st, lv = ctx.st, vr["params"]["level"]
    tagged = {st.el[t]["params"]["target"] for t in ctx.rels_in("annot_of", a.view) if st.el[t]["kind"] == "tag"}
    out = []
    for k in kinds:
        for e in sorted(st.by_kind.get(k, ())):
            if e in tagged or st.el[e]["origin"] != "authored" or _level_of(ctx, k, e) != lv:
                continue
            tid = _det_id("annot.tag.all", a.view, e)
            if tid in st.el:   # 같은 id 의 다른 요소가 있다(정상 흐름에서는 없다): 건너뛴다
                continue
            annot_tag_create(ctx, TagCreate(id=tid, view=a.view, target=e, fields=list(a.fields), offset=list(a.offset)))
            out.append({"id": tid, "target": e, "kind": k})
    if not out:
        ctx.warn("새로 단 태그가 없다(대상이 없거나 이미 태그가 있다)")
    return {"view": a.view, "created": [t["id"] for t in out], "tags": out}


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
        if p.get("measure") in ("radius", "diameter", "arc") and a.a is not None and a.b is None:
            p["b"] = p["a"]   # 반지름·지름은 원 참조 하나(a = b)
        vertical, ra, rb, g = _dim_checked(ctx, p["view"], p["a"], p["b"], p.get("at"), p.get("measure"), p.get("along"))
        dirs = dict(p.get("dirs") or {})
        for side, given in (("a", a.a), ("b", a.b)):   # 다시 묶은 쪽만 지문을 새로 잡는다(같은 참조를 다시 주면 경고를 확인한 것으로 본다)
            if given is not None:
                dirs[side] = _dir_fp(ctx, given)
        p["dirs"] = {"a": dirs.get("a"), "b": dirs.get("b")}
        p["home"] = _dim_home(g, p, ra, rb)
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
        if "measure" in p:
            out["measure"] = p["measure"]
        if "group" in p:   # 연속·기준선 치수 묶음(W1 연장 ANN-02)
            out.update(group=p["group"], chain=p["gk"])
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


SEU_USE_CODE = r"^A[0-9]{5}$"   # 세움터 용도구획 AIS_분류코드 형식(A+5자리, krules/seumteo.py). 코드 표 자체는 근거에 없어 형식만 본다


class LevelUseCode(A):
    level: Id = Field(description="레벨 id")
    code: str = Field(pattern=SEU_USE_CODE, description="그 층 용도구획의 AIS_분류코드(A+5자리, 사용자가 준다: 코드 표 대조 안 함, 확인 필요)")


class SeumteoAreas(A):
    use_code: str = Field(pattern=SEU_USE_CODE, description="용도구획 기본 AIS_분류코드(A+5자리 용도 코드). 세움터 용도 코드 표는 근거 문서에 없어 "
                                                            "사용자가 준 값을 그대로 싣고 확인 필요로 표시한다")
    level_use_codes: Annotated[list[LevelUseCode], Field(max_length=200)] | None = Field(None, description="레벨별 용도구획 코드(없는 레벨은 use_code)")
    site: Id | None = Field(None, description="대지 id(area.legal 과 같음). 생략하면 저작 대지가 하나일 때 그것")
    site_area_m2: float | None = Field(None, gt=0, le=1e8, description="대지면적 ㎡ 를 직접 준다(대지 경계 대신, 이때 대지면적 객체는 형상 없음)")
    ground: float = Field(0.0, ge=-1e6, le=1e6, description="지표면 표고 mm(area.legal 과 같음)")
    basement_levels: Annotated[list[Id], Field(max_length=200)] | None = Field(None, description="지하층으로 볼 레벨 id 목록(area.legal 과 같음)")


class IfcExport(A):
    path: str = Field(max_length=260, description="작업 폴더 안의 .ifc 경로. 기존 파일은 덮어쓰며, 덮어쓰기 전에 자동 스냅숏을 남긴다")
    schema_: Literal["IFC4", "IFC2X3"] = Field("IFC4", alias="schema", description="IFC4(기본, 정본 투영 그대로) 또는 IFC2X3(TC1, 세움터 등 제출용: IFC4 투영을 스키마 변환하고 손실 항목을 losses 로 알린다)")
    seumteo_areas: SeumteoAreas | None = Field(None, description="세움터 제출용 면적 객체(용도구획 A·건축면적 B·대지면적 S)를 IfcSpace + AIS_ 속성으로 더한다"
                                                                 "(값은 area.legal 과 같은 계산). 생략하면 정본 투영 그대로(기존과 같은 바이트)")


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
         summary="IFC4·IFC2x3 내보내기",
         args=IfcExport, preview=_export_preview, examples=[{"path": "export-example.ifc"}, {"path": "export-example-2x3.ifc", "schema": "IFC2X3"},
                                                     {"path": "export-example-seumteo.ifc", "seumteo_areas": {"use_code": "A00000"}}])   # A00000: 형식만 맞춘 자리표시 값
def ifc_export(ctx, a: IfcExport):
    from dobim_core import globalid as GI
    p = _target(ctx, a.path)
    ctx.eng.flush_ifc()   # 투영한 요소 수는 프로세스 상태(캐시 유무)에 따라 달라 결과에 넣지 않는다(패리티 C)
    seu = None
    src = ctx.eng.ifc.f
    if a.seumteo_areas is not None:   # 세움터 면적 객체: 정본의 사본에만 더한다(옵션을 끄면 이 길을 타지 않아 출력 바이트 그대로)
        from dobim_core import commands_area as CA
        from dobim_core import seumteo_ifc as SI
        plan = CA.seumteo_plan(ctx, a.seumteo_areas)   # 레벨·대지 id 오류는 스냅숏을 만들기 전에 낸다
        src = SI.copy_file(src)
        seu = {"objects": SI.add_areas(src, ctx.eng.ifc, plan["objects"]), "use_codes": plan["use_codes"],
               "building_m2": plan["legal"]["building"]["area_m2"], "site_m2": (plan["legal"]["site"] or {}).get("area_m2"),
               "gross_floor_m2": plan["legal"]["gross_floor_m2"], "unverified": plan["unverified"][:20]}
    snap = _snapshot_existing(p, ctx.eng.workspace)
    losses = None
    if a.schema_ == "IFC2X3":   # IFC4 투영을 복사해 내린다(정본 투영·IFC4 출력은 그대로)
        from dobim_core import __version__
        from dobim_core import ifc2x3 as X2
        try:
            f2, losses = X2.to_ifc2x3(src, __version__, 0)   # 작성 시각 0: IFC4 출력처럼 같은 모델이면 같은 내용(시계에 기대지 않는다)
        except Exception as ex:
            _drop_snapshot(p, snap, ctx.eng.workspace)
            raise DobimError("CONSTRAINT", f"IFC2x3 로 바꾸지 못했다({type(ex).__name__}: {str(ex)[:200]})", path="/schema")
        if seu is not None:
            SI.fix_2x3(f2)
        remapped = GI.encode(f2)
        writer = f2.write
        out_f = f2
    elif seu is not None:
        remapped = GI.encode(src)
        writer = src.write
        out_f = src
    else:
        out_f, remapped = GI.encoded_copy(src)   # 무효한 GlobalId 가 없으면 정본 투영을 그대로 쓴다(출력 바이트 이전과 같다)
        writer = ctx.eng.ifc.write if not remapped else out_f.write
        out_f = out_f if remapped else None
    if seu is not None:   # 내보낼 파일을 저장소 krules 세움터 검사(면적 객체 4종)로 다시 본다
        try:
            seu["self_check"] = SI.self_check(out_f)
        except BaseException:
            _drop_snapshot(p, snap, ctx.eng.workspace)
            raise
    try:
        _replace(p, lambda tmp: writer(str(tmp)))
    except BaseException:   # DobimError 만이 아니다: ifc.write 가 라이브러리 예외로 실패해도 스냅숏이 쌓이지 않게
        _drop_snapshot(p, snap, ctx.eng.workspace)   # 쓰지 못했으면 방금 만든 스냅숏은 필요 없다(재시도마다 쌓이지 않게)
        raise
    out = {"path": p.name, "bytes": p.stat().st_size, "rev": ctx.eng.rev, "snapshot": snap}
    if remapped:   # 무효한 GlobalId 가 없으면 응답은 이전과 같다(있으면 경고 한 줄만 더한다: 응답 키는 그대로)
        ctx.warn(f"첫 글자가 0~3 이 아닌 id {remapped}개는 IFC GlobalId 로 무효라 파일에서는 유효한 값으로 바꿔 썼다. 원래 id 는 IfcProject 의 "
                 f"{GI.IDMAP_PSET} 에 있고 ifc.import 가 되돌린다. 새 요소 id 는 첫 글자를 0~3 으로 쓰세요")
    if losses is not None:   # IFC4 응답은 이전과 같다
        out["schema"] = "IFC2X3"
        out["losses"] = losses[:40]
        out["losses_total"] = len(losses)
        if losses:
            ctx.warn(f"IFC2x3 에 담지 못한 항목 {len(losses)}종(클래스 대체·PredefinedType 등): losses 를 보세요. 원래 분류는 ObjectType 에 남겼다")
    if seu is not None:   # 옵션을 켰을 때만 붙인다(끄면 응답도 이전과 같다)
        out["seumteo_areas"] = seu
        ctx.warn("세움터 면적 객체: 용도 코드·IFC 위치·제외 플래그는 근거 문서로 확인하지 못했다(seumteo_areas.unverified, 확인 필요)")
    return out


class IfcImport(A):
    source: str = Field(max_length=260, description="작업 폴더 안의 IFC4·IFC2x3 파일(.ifc). 읽기만 한다. IFC2x3 은 IFC4 로 올려서 가져온다")
    path: str = Field(max_length=260, description="새로 만들 프로젝트(.dobim) 경로. 이미 있으면 FILE_EXISTS")
    guid: str | None = Field(None, max_length=36, description="프로젝트 UUID. 생략하면 무작위")


@command("ifc.import", kind="session", risk="write", group="io", summary="외래 IFC4 를 새 프로젝트로 가져온다: 요소를 authored·foreign·detached 로 분류하고 원본을 외래 기반으로 보존",
         args=IfcImport, needs_project=False,
         examples=[{"source": "import-example.ifc", "path": "imported-example.dobim", "guid": "5d0b1a50-0000-4a00-8000-0d0b1a5000f3"}])
def ifc_import(env, a: IfcImport):
    """새 프로젝트를 만든다(project.new 와 같다: 기존 프로젝트에 합치지 않는다). DoBIM_Intent 가 있고 투영이 들어온 형상과 같은 요소는
    authored, 의도가 있지만 형상이 다르면 detached(동결, 경고), 의도가 없거나 해석할 수 없으면 foreign. 원본 바이트는 SQLite 에 보존한다."""
    from dobim_core import globalid as GI
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
    converted = None
    if b"IFC2X3" in data[:4096].upper():   # IFC2x3 은 IFC4 로 올려서 가져온다(외래 기반으로는 올린 IFC4 를 보존한다)
        from dobim_core import ifc2x3 as X2
        from dobim_core.ifc_model import open_ifc_bytes
        try:
            f0 = open_ifc_bytes(data)
        except Exception as ex:
            raise DobimError("IMPORT_INVALID", f"IFC 를 읽을 수 없다({type(ex).__name__})", path="/source") from None
        if f0.schema == "IFC2X3":
            f4, conv_warn = X2.to_ifc4(f0)
            data, converted = f4.to_string().encode("utf-8"), conv_warn
    n_bytes = len(data)
    data = GI.prepare_import(data)   # DoBIM_IdMap 이 있으면 GlobalId 를 원래 id 로, 옛 'SweptSolid' 난간 표현은 'AdvancedSweptSolid' 로(그 밖에는 그대로)
    plan = plan_import(data)
    if converted is not None:
        plan.warnings[:0] = ["IFC2x3 파일을 IFC4 로 올려 가져왔다(외래 기반은 올린 IFC4)"] + converted
    eng = Engine.create_imported(dst, data, plan, env.actor, guid=a.guid)
    try:
        counts = plan.counts()
        shown = [{"id": d["id"], "reasons": d["reasons"][:4]} for d in plan.detached[:20]]
        return {"path": dst.name, "rev": eng.rev, "project_uuid": eng.project_guid_uuid, "schema": "IFC2X3" if converted is not None else plan.schema, "bytes": n_bytes,
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
from dobim_core import commands_sheet  # noqa: E402,F401
from dobim_core import commands_walltop  # noqa: E402,F401
from dobim_core import commands_draft  # noqa: E402,F401
from dobim_core import commands_schedule  # noqa: E402,F401
from dobim_core import commands_gui  # noqa: E402,F401
from dobim_core import commands_rules  # noqa: E402,F401
from dobim_core import commands_revision  # noqa: E402,F401
from dobim_core import commands_viewvg  # noqa: E402,F401
from dobim_core import commands_viewref  # noqa: E402,F401
from dobim_core import commands_viewplans  # noqa: E402,F401
from dobim_core import commands_edit  # noqa: E402,F401
from dobim_core import commands_param  # noqa: E402,F401
from dobim_core import commands_area  # noqa: E402,F401
from dobim_core import commands_dimchain  # noqa: E402,F401
from dobim_core import commands_check  # noqa: E402,F401
from dobim_core import commands_drawcheck  # noqa: E402,F401
from dobim_core import commands_arch  # noqa: E402,F401
from dobim_core import commands_arcwall  # noqa: E402,F401
from dobim_core import commands_opening  # noqa: E402,F401   독립 개구 객체(W2 세 번째 PR, ARC-34)
from dobim_core import commands_curtain  # noqa: E402,F401   커튼월(W2 네 번째 PR, ARC-22)
