# SPDX-License-Identifier: MPL-2.0
"""S1 커맨드 정의. 레지스트리 항목이 곧 기능이다 (15.1절).

이 모듈은 ifcopenshell 을 import 하지 않는다. 상태는 ctx 의 효과(emit)로만 바꾼다.
세션 커맨드(needs_project=False)는 (env, args), 나머지는 (ctx, args) 를 받는다.
"""
from __future__ import annotations

import base64
import json
from math import sqrt
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from dobim_core import fixture as F
from dobim_core import queries as Q
from dobim_core import registry
from dobim_core.errors import DobimError
from dobim_core.paths import resolve_in_workspace
from dobim_core.registry import command

RESERVED_PSET = "DoBIM_Intent"
Id = Annotated[str, Field(pattern=r"^[0-9A-Za-z_$]{22}$", description="IFC GlobalId (22자)")]
Num = Annotated[float, Field(allow_inf_nan=False)]
Pos = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Name = Annotated[str, Field(min_length=1, max_length=64)]
PropValue = Union[StrictBool, StrictInt, StrictFloat, StrictStr, None]


class A(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    path: str = Field(description="작업 폴더 안의 .dobim 경로")
    guid: str | None = Field(None, description="프로젝트 UUID. 생략하면 무작위, 지정하면 재현 가능")


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
    query: str = ""
    group: str | None = None
    risk: Literal["read", "write", "destructive", "code_exec"] | None = None


@command("commands.search", kind="query", risk="read", group="system", summary="커맨드를 키워드·그룹·위험 등급으로 찾는다",
         args=Search, needs_project=False, idempotent=True, examples=[{"query": "wall"}, {"group": "history"}])
def commands_search(env, a: Search):
    items = registry.search(a.query, a.group, a.risk)
    return {"items": items, "page": {"next_cursor": None, "total": len(items)}}


class Describe(A):
    name: str


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
            dp = ctx.st.el[d]["params"]
            if dp["offset"] - dp["width"] / 2 < 0 or dp["offset"] + dp["width"] / 2 > length:
                raise DobimError("CONSTRAINT", f"호스팅된 문이 벽 밖으로 나간다: {d}", path="/axis")
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
    for pset, props in ctx.psets(eid).items():
        for prop in sorted(props):
            ctx.set_prop(eid, pset, prop, None)
    ctx.set_material(eid, None)
    for kind in ("joins", "on_level", "hosts"):
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
    elif r["kind"] == "wall":
        for d in ctx.rels_out("hosts", a.id):
            _delete_one(ctx, d)
            gone.append(d)
    _delete_one(ctx, a.id)
    return {"deleted": gone}


class PsetSet(A):
    id: Id
    pset: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    prop: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    value: PropValue = Field(description="null 이면 속성을 지운다")


@command("pset.set", kind="mutation", risk="write", group="model.pset", summary="속성셋 값을 쓴다(IFC 정본)",
         args=PsetSet, since="0.1",
         examples=[{"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI60"}])
def pset_set(ctx, a: PsetSet):
    ctx.row(a.id)
    if a.pset == RESERVED_PSET:
        raise DobimError("CONSTRAINT", f"{RESERVED_PSET} 는 투영이 소유한 예약 속성셋이다", path="/pset")
    return {"changed": ctx.set_prop(a.id, a.pset, a.prop, a.value)}


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
    return base64.urlsafe_b64encode(json.dumps(v).encode()).decode()


def _dec_cursor(c):
    if c is None:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(c.encode()))
    except Exception:
        raise DobimError("SCHEMA_INVALID", "잘못된 커서다", path="/cursor") from None


class ElementGet(A):
    id: Id


@command("element.get", kind="query", risk="read", group="query", summary="요소 하나의 의도·속성·파생값",
         args=ElementGet, idempotent=True, examples=[{"id": F.W1}])
def element_get(ctx, a: ElementGet):
    r = ctx.row(a.id)
    out = {"id": a.id, "kind": r["kind"], "ifc_class": r["ifc_class"], "origin": r["origin"],
           "family_id": r["family_id"], "params": r["params"], "psets": ctx.psets(a.id),
           "material": ctx.material(a.id),
           "relations": {k: ctx.rels_out(k, a.id) + [f"<-{x}" for x in ctx.rels_in(k, a.id)]
                         for k in ("on_level", "joins", "hosts") if ctx.rels_out(k, a.id) or ctx.rels_in(k, a.id)}}
    if r["kind"] == "wall":
        start, end, status = ctx.g.q(Q.wall_trim, a.id)
        out["derived"] = {"trim_start": start, "trim_end": end, "length": end - start, "status": status}
    elif r["kind"] == "door":
        out["derived"] = {"world": list(ctx.g.q(Q.door_world, a.id)), "status": ctx.g.q(Q.door_status, a.id)}
    return out


class ElementQuery(A):
    kind: Literal["level", "wall", "door"] | None = None
    level: Id | None = None
    name_contains: str | None = None
    limit: int = Field(100, ge=1, le=1000)
    cursor: str | None = None


@command("element.query", kind="query", risk="read", group="query", summary="요소 목록(종류·레벨·이름 필터, 페이지)",
         args=ElementQuery, idempotent=True, examples=[{"kind": "wall", "limit": 10}])
def element_query(ctx, a: ElementQuery):
    st = ctx.st
    after = _dec_cursor(a.cursor)
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
    page = hits[start:start + a.limit]
    nxt = _enc_cursor(page[-1]) if start + a.limit < len(hits) else None
    items = [{"id": e, "kind": st.el[e]["kind"], "name": st.el[e]["params"]["name"]} for e in page]
    return {"items": items, "page": {"next_cursor": nxt, "total": len(hits)}}


@command("relation.list", kind="query", risk="read", group="query", summary="요소의 관계(레벨·조인·호스팅)",
         args=ElementGet, idempotent=True, examples=[{"id": F.W1}])
def relation_list(ctx, a: ElementGet):
    ctx.row(a.id)
    out = []
    for k in ("on_level", "joins", "hosts"):
        out += [{"kind": k, "src": a.id, "dst": d} for d in ctx.rels_out(k, a.id)]
        out += [{"kind": k, "src": s, "dst": a.id} for s in ctx.rels_in(k, a.id)]
    return {"items": out, "page": {"next_cursor": None, "total": len(out)}}


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
    return {"errors": v["errors"], "warnings": v["warnings"], "violations": v["samples"]}


class ScheduleRows(A):
    kind: Literal["door", "wall"]
    limit: int = Field(100, ge=1, le=1000)
    cursor: str | None = None


@command("schedule.rows", kind="query", risk="read", group="schedule", summary="문·벽 일람표 행(페이지)",
         args=ScheduleRows, idempotent=True, examples=[{"kind": "door", "limit": 5}])
def schedule_rows(ctx, a: ScheduleRows):
    rows = ctx.g.q(Q.schedule_sorted, a.kind)
    start = _dec_cursor(a.cursor) or 0
    page = rows[start:start + a.limit]
    nxt = _enc_cursor(start + a.limit) if start + a.limit < len(rows) else None
    cols = Q.ROW_COLUMNS[a.kind]
    return {"columns": list(cols), "items": [list(r) for r in page],
            "page": {"next_cursor": nxt, "total": len(rows)}}


class ScheduleKind(A):
    kind: Literal["door", "wall"]


@command("schedule.summary", kind="query", risk="read", group="schedule", summary="일람표 합계",
         args=ScheduleKind, idempotent=True, examples=[{"kind": "wall"}])
def schedule_summary(ctx, a: ScheduleKind):
    return ctx.g.q(Q.schedule_summary, a.kind)


# ================================================================ 이력
class HistoryArgs(A):
    target: str | None = Field(None, description="되돌릴/다시할 트랜잭션. 생략하면 스택 맨 위 (로그에는 확정값이 남는다)")


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
    return {"txn": txn, "ops": [e[2] for e in entries], "effects": len(effects)}


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


class HistoryList(A):
    limit: int = Field(20, ge=1, le=500)


@command("history.list", kind="query", risk="read", group="history", summary="최근 로그 항목",
         args=HistoryList, idempotent=True, examples=[{"limit": 5}])
def history_list(ctx, a: HistoryList):
    rows = ctx.eng.conn.execute(
        "SELECT seq, txn_id, actor, base_rev, op, args FROM op_log ORDER BY seq DESC LIMIT ?", (a.limit,)).fetchall()
    items = [{"seq": s, "txn": t, "actor": ac, "base_rev": b, "op": o, "args": json.loads(g)}
             for s, t, ac, b, o, g in rows]
    return {"items": items, "undo_depth": len(ctx.eng.undo_stack), "redo_depth": len(ctx.eng.redo_stack),
            "page": {"next_cursor": None, "total": ctx.eng.rev}}


# ================================================================ 입출력 (파괴적)
SNAP_DIR = ".dobim-snapshots"
_FORBIDDEN_SUFFIX = (".dobim", ".dobim-wal", ".dobim-shm", ".dobim-journal", ".tmp", "-wal", "-shm", "-journal")


class IfcExport(A):
    path: str = Field(description="작업 폴더 안의 .ifc 경로. 기존 파일은 덮어쓰며, 덮어쓰기 전에 자동 스냅숏을 남긴다")


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
    if SNAP_DIR in p.parts:
        raise DobimError("PATH_DENIED", "스냅숏 폴더에는 쓸 수 없다", path="/path")
    if p.is_dir():
        raise DobimError("PATH_DENIED", f"폴더다: {p.name}", path="/path")
    return p


def _snapshots(p):
    d = p.parent / SNAP_DIR
    out = []
    if d.is_dir():
        for f in d.iterdir():
            pre = p.name + "."
            if f.name.startswith(pre) and f.name[len(pre):].isdigit():
                out.append((int(f.name[len(pre):]), f))
    return sorted(out)


def _snapshot_existing(p):
    """덮어쓰기 전에 기존 파일을 .dobim-snapshots/<이름>.<번호> 로 복사한다. 번호는 결정적으로 증가한다."""
    import shutil
    if not p.exists():
        return None
    d = p.parent / SNAP_DIR
    d.mkdir(exist_ok=True)
    n = max([k for k, _ in _snapshots(p)], default=0) + 1
    dst = d / f"{p.name}.{n}"
    shutil.copy2(p, dst)
    return f"{SNAP_DIR}/{dst.name}"


def _replace(p, write):
    """임시 파일에 쓴 뒤 교체한다. 대상이 열려 있으면 FILE_LOCKED."""
    tmp = p.with_name(p.name + ".tmp")
    write(tmp)
    try:
        tmp.replace(p)
    except OSError as ex:
        tmp.unlink(missing_ok=True)
        raise DobimError("FILE_LOCKED", f"파일을 바꿀 수 없다: {p.name}") from ex


def _export_preview(ctx, a: IfcExport):
    p = _target(ctx, a.path)
    return {"will_write": p.name, "overwrites": p.exists(), "snapshot_before_overwrite": p.exists()}


@command("ifc.export", kind="session", risk="destructive", group="io",
         summary="IFC4 파일로 내보낸다. 기존 파일은 덮어쓰기 전에 자동 스냅숏을 남긴다",
         args=IfcExport, preview=_export_preview, examples=[{"path": "export-example.ifc"}])
def ifc_export(ctx, a: IfcExport):
    p = _target(ctx, a.path)
    ctx.eng.flush_ifc()   # 투영한 요소 수는 프로세스 상태(캐시 유무)에 따라 달라 결과에 넣지 않는다(패리티 C)
    snap = _snapshot_existing(p)
    _replace(p, lambda tmp: ctx.eng.ifc.write(str(tmp)))
    return {"path": p.name, "bytes": p.stat().st_size, "rev": ctx.eng.rev, "snapshot": snap}


@command("ifc.snapshots", kind="query", risk="read", group="io", summary="IFC 내보내기 덮어쓰기 전 스냅숏 목록",
         args=IfcExport, idempotent=True, needs_project=True, examples=[{"path": "export-example.ifc"}])
def ifc_snapshots(ctx, a: IfcExport):
    import os
    p = resolve_in_workspace(ctx.eng.workspace, a.path)
    items = [{"n": n, "name": f"{SNAP_DIR}/{f.name}", "bytes": f.stat().st_size} for n, f in _snapshots(p)]
    return {"items": items, "page": {"next_cursor": None, "total": len(items)}}


class IfcRestore(A):
    path: str = Field(description="되돌릴 .ifc 파일 경로")
    snapshot: int = Field(description="ifc.snapshots 가 알려 준 번호", ge=1)


def _restore_preview(ctx, a: IfcRestore):
    p = _target(ctx, a.path)
    found = dict(_snapshots(p)).get(a.snapshot)
    if found is None:
        raise DobimError("NOT_FOUND", f"스냅숏이 없다: {a.snapshot}", path="/snapshot")
    return {"will_write": p.name, "from_snapshot": a.snapshot, "overwrites": p.exists(), "snapshot_before_overwrite": p.exists()}


@command("ifc.restore", kind="session", risk="destructive", group="io",
         summary="스냅숏으로 .ifc 파일을 되돌린다(현재 파일도 먼저 스냅숏으로 남긴다)",
         args=IfcRestore, preview=_restore_preview, examples=[{"path": "export-example.ifc", "snapshot": 1}])
def ifc_restore(ctx, a: IfcRestore):
    import shutil
    p = _target(ctx, a.path)
    src = dict(_snapshots(p)).get(a.snapshot)
    if src is None:
        raise DobimError("NOT_FOUND", f"스냅숏이 없다: {a.snapshot}", path="/snapshot")
    snap = _snapshot_existing(p)
    _replace(p, lambda tmp: shutil.copyfile(src, tmp))
    return {"path": p.name, "restored_from": a.snapshot, "bytes": p.stat().st_size, "snapshot": snap}
