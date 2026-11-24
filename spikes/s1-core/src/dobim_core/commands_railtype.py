# SPDX-License-Identifier: MPL-2.0
"""난간 유형 커맨드(W2 열세 번째 PR, ARC-31).

railtype.create / set / duplicate / delete / list : 이름 붙은 난간 유형 = 높이(상부 난간대 윗면) + 부재 구성(kinds.rail_parts_ok):
                                                   상부 난간대 단면, 중간 난간대(높이·단면) 0~8개, 발루스터(간격 상한·단면·아래 끝 높이), 동자(간격 상한·단면).
                                                   벽·지붕 유형과 같은 설계다: 난간 행이 유형의 높이·구성을 복사해 갖고(params.rltype·parts + 관계 of_type),
                                                   유형을 고치면 그 유형의 난간 행만 다시 쓴다(증분 갱신)
railing.type.set : 난간에 유형을 입히거나 뗀다(뗀 난간은 높이만 남고 구성은 지운다: 이전 형식의 난간)
railing.create·from_stair·from_slab 은 type 으로 유형을 받는다(commands.py).
형상은 kinds.railing_geom(parts 가 있으면 부재별)이 정하고 도면(단면·입면 투영선, 평면 동자)·IFC(IfcRailing 몸체 = 상부 난간대, IfcRelAggregates 로
IfcMember: 중간 난간대·동자(POST)·발루스터, IfcRailingType + IfcRelDefinesByType)가 같은 값을 쓴다. 이 커맨드를 쓰지 않은 난간은 바이트까지 그대로다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core.commands import A, Id, Name, Num, Pos, command, row
from dobim_core.errors import DobimError

IDS_MAX = 500


# ---------------------------------------------------------------- 공통 도우미
def _types(ctx) -> list:
    return sorted(ctx.st.by_kind.get("railtype", ()))


def type_row(ctx, tid: str, path: str = "/id") -> dict:
    r = ctx.st.el.get(tid)
    if r is None or r["kind"] != "railtype":
        raise DobimError("NOT_FOUND", f"난간 유형을 찾을 수 없다: {tid}", path=path, hint="railtype.list 로 유형을 보세요")
    return r


def railings_of(ctx, tid: str) -> list:
    return sorted(r for r in ctx.rels_in("of_type", tid) if ctx.st.el[r]["kind"] == "railing")


def _name_taken(ctx, name: str, skip: str | None = None) -> None:
    for t in _types(ctx):
        if t != skip and ctx.st.el[t]["params"]["name"] == name:
            raise DobimError("CONSTRAINT", f"같은 이름의 난간 유형이 있다: {name}", path="/name")


def _copy_parts(parts: dict) -> dict:
    out = {"top": dict(parts["top"]), "rails": [dict(x) for x in parts["rails"]], "post": dict(parts["post"])}
    if "baluster" in parts:
        out["baluster"] = dict(parts["baluster"])
    return out


def type_fields(tid: str, tp: dict) -> dict:
    """유형 params 에서 난간 행으로 복사하는 키(유형 id·구성)."""
    return {"rltype": tid, "parts": _copy_parts(tp["parts"])}


def _write_railing(ctx, rid: str, q: dict, path: str) -> None:
    r = ctx.st.el[rid]
    if q == r["params"]:
        return
    why = K.row_params_error("railing", q)
    if why:
        raise DobimError("CONSTRAINT", f"난간 {rid} 가 올바르지 않게 된다: {why}", path=path)
    ctx.put_el(rid, dict(r, params=q))


def retype(ctx, rid: str, tid: str, tp: dict, path: str) -> None:
    """난간 rid 에 유형(tid, params tp)을 입힌다: 높이·구성을 유형 것으로."""
    r = ctx.st.el[rid]
    p = r["params"]
    q = dict(p)
    q.update(type_fields(tid, tp))
    q["height"] = float(tp["height"])
    _write_railing(ctx, rid, q, path)
    old = p.get("rltype")
    if old != tid:
        if old is not None:
            ctx.rel("of_type", rid, old, False)
        ctx.rel("of_type", rid, tid, True)


def detach(ctx, rid: str) -> None:
    """난간에서 유형을 뗀다(높이는 남고 부재 구성은 지운다: 손잡이 + 기둥만 있는 이전 형식)."""
    r = ctx.st.el[rid]
    old = r["params"].get("rltype")
    if old is None:
        return
    q = {k: v for k, v in r["params"].items() if k not in ("rltype", "parts")}
    ctx.put_el(rid, dict(r, params=q))
    ctx.rel("of_type", rid, old, False)


def _pub_type(ctx, tid: str) -> dict:
    p = ctx.st.el[tid]["params"]
    return {"id": tid, "name": p["name"], "height": p["height"], "parts": _copy_parts(p["parts"]), "railings": len(railings_of(ctx, tid))}


# ---------------------------------------------------------------- 입력 형식
class _Sec(A):
    w: Pos = Field(description="단면 폭 mm(수평)")
    d: Pos = Field(description="단면 춤 mm(수직)")


class _Rail(A):
    z: Pos = Field(description="바닥 마감에서 중간 난간대 중심까지 mm")
    w: Pos = Field(30.0, description="단면 폭 mm")
    d: Pos = Field(30.0, description="단면 춤 mm")


class _Baluster(A):
    spacing: Pos = Field(description=f"발루스터 간격 상한 mm({K.BALUSTER_SPACING_MIN:g} 이상). 동자 사이를 같은 간격(상한 이하)으로 나누고 양 끝은 동자 자리라 비운다")
    w: Pos = Field(20.0, description="단면 한 변 mm")
    bottom: Num = Field(0.0, ge=0, description="바닥 마감에서 발루스터 아래 끝까지 mm(위 끝 = 상부 난간대 아랫면)")


class _Post(A):
    spacing: Pos = Field(K.RAILING_POST_SPACING, description=f"동자 간격 상한 mm({K.POST_SPACING_MIN:g} 이상). 난간선 꼭짓점마다 + 그 사이 등분")
    w: Pos = Field(50.0, description="단면 한 변 mm")


Rails = Annotated[list[_Rail], Field(max_length=K.RAIL_MIDS_MAX, description="중간 난간대(아래에서 위로 아무 순서, 0~8개)")]


def _parts(top, rails, baluster, post) -> dict:
    out = {"top": {"w": float(top["w"]), "d": float(top["d"])}, "rails": [{k: float(x[k]) for k in ("z", "w", "d")} for x in rails],
           "post": {"spacing": float(post["spacing"]), "w": float(post["w"])}}
    if baluster is not None:
        out["baluster"] = {k: float(baluster[k]) for k in ("spacing", "w", "bottom")}
    return out


def _type_params(name, height, parts) -> dict:
    p = {"name": name, "height": float(height), "parts": parts}
    why = K.row_params_error("railtype", p)
    if why:
        raise DobimError("CONSTRAINT", f"난간 유형이 올바르지 않다: {why}", path="/parts",
                         hint=f"중간 난간대 위 끝·발루스터 아래 끝은 상부 난간대 아랫면 아래, 발루스터 간격 {K.BALUSTER_SPACING_MIN:g}·동자 간격 {K.POST_SPACING_MIN:g} 이상")
    return p


# ---------------------------------------------------------------- 난간 유형
class RailTypeCreate(A):
    id: Id | None = None
    name: Name = Field(description="유형 이름(프로젝트 안에서 유일)")
    height: Pos = Field(K.RAILING_HEIGHT_DEFAULT, description="난간 높이 mm(바닥 마감에서 상부 난간대 윗면까지)")
    top: _Sec = Field(default_factory=lambda: _Sec(w=50.0, d=50.0), description="상부 난간대 단면")
    rails: Rails = Field(default_factory=list)
    baluster: _Baluster | None = Field(None, description="발루스터(없으면 동자와 난간대만)")
    post: _Post = Field(default_factory=lambda: _Post(), description="동자(난간 기둥)")


@command("railtype.create", kind="mutation", risk="write", group="model.railing",
         summary="난간 유형(높이·상부 난간대·중간 난간대·발루스터·동자)을 만든다. 난간은 railing.type.set 이나 railing.create type 으로 입힌다",
         args=RailTypeCreate, since="0.22",
         examples=[{"name": "발루스터 1200", "height": 1200, "top": {"w": 60, "d": 40}, "rails": [{"z": 150, "w": 40, "d": 30}],
                    "baluster": {"spacing": 110, "w": 20, "bottom": 150}, "post": {"spacing": 1500, "w": 60}},
                   {"name": "난간대 3단", "rails": [{"z": 350}, {"z": 700}]}])
def railtype_create(ctx, a: RailTypeCreate):
    if len(_types(ctx)) >= K.RAILTYPES_MAX:
        raise DobimError("CONSTRAINT", f"난간 유형은 {K.RAILTYPES_MAX}개까지다", path="/name")
    _name_taken(ctx, a.name)
    p = _type_params(a.name, a.height, _parts(a.top.model_dump(), [x.model_dump() for x in a.rails],
                                               a.baluster.model_dump() if a.baluster is not None else None, a.post.model_dump()))
    C._fresh_id(ctx, a, "railtype")
    if ctx.external_def(a.id):
        ctx.warn("가져온 파일에 이 id 의 난간 유형 정의가 되살려지지 않은 채 남아 있다: 새 정의가 그 속성을 덮어쓴다")
    ctx.put_el(a.id, row("railtype", "", "railing.type", p))
    return {"id": a.id, "name": a.name, "height": p["height"]}


class RailTypeSet(A):
    forbid_null = False
    id: Id
    name: Name | None = None
    height: Pos | None = None
    top: _Sec | None = None
    rails: Rails | None = Field(None, description="중간 난간대를 통째로 바꾼다(빈 목록이면 없앤다)")
    baluster: _Baluster | None = None
    post: _Post | None = None
    clear: Annotated[list[Literal["baluster"]], Field(max_length=1)] | None = Field(None, description="뗄 부재(baluster)")

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.height, self.top, self.rails, self.baluster, self.post, self.clear)):
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.clear and self.baluster is not None:
            raise ValueError("baluster 를 주면서 clear 에 넣을 수 없다")
        return self


@command("railtype.set", kind="mutation", risk="write", group="model.railing",
         summary="난간 유형을 고친다. 그 유형의 모든 난간이 따라간다(높이·부재 구성)",
         args=RailTypeSet, since="0.22", examples=[{"id": F.RLT1, "baluster": {"spacing": 100, "w": 16, "bottom": 100}}])
def railtype_set(ctx, a: RailTypeSet):
    r = type_row(ctx, a.id)
    old = r["params"]
    if a.name is not None:
        _name_taken(ctx, a.name, skip=a.id)
    op = old["parts"]
    bal = None if a.clear else (a.baluster.model_dump() if a.baluster is not None else op.get("baluster"))
    parts = _parts(a.top.model_dump() if a.top is not None else op["top"], [x.model_dump() for x in a.rails] if a.rails is not None else op["rails"],
                   bal, a.post.model_dump() if a.post is not None else op["post"])
    p = _type_params(a.name if a.name is not None else old["name"], a.height if a.height is not None else old["height"], parts)
    if p == old:
        return {"id": a.id, "changed": False, "railings": 0}
    ctx.put_el(a.id, dict(r, params=p))
    n = 0
    if (p["height"], p["parts"]) != (old["height"], old["parts"]):   # 형상 키가 바뀐 때만 난간을 다시 쓴다(이름은 난간 행에 없다)
        for rid in railings_of(ctx, a.id):
            before = ctx.st.el[rid]["params"]
            retype(ctx, rid, a.id, p, "/id")
            n += ctx.st.el[rid]["params"] != before
    return {"id": a.id, "changed": True, "railings": n}


class RailTypeDup(A):
    id: Id = Field(description="복제할 유형")
    name: Name = Field(description="새 유형 이름")
    new_id: Id | None = None


@command("railtype.duplicate", kind="mutation", risk="write", group="model.railing", summary="난간 유형을 새 이름으로 복제한다(난간은 옮기지 않는다)",
         args=RailTypeDup, since="0.22", examples=[{"id": F.RLT1, "name": "발루스터 1100"}])
def railtype_duplicate(ctx, a: RailTypeDup):
    r = type_row(ctx, a.id)
    if len(_types(ctx)) >= K.RAILTYPES_MAX:
        raise DobimError("CONSTRAINT", f"난간 유형은 {K.RAILTYPES_MAX}개까지다", path="/name")
    _name_taken(ctx, a.name)
    holder = RailTypeCreate(name=a.name, id=a.new_id)
    C._fresh_id(ctx, holder, "railtype")
    p = {"name": a.name, "height": r["params"]["height"], "parts": _copy_parts(r["params"]["parts"])}
    ctx.put_el(holder.id, row("railtype", "", "railing.type", p))
    return {"id": holder.id, "name": a.name}


class RailTypeDelete(A):
    id: Id
    detach: bool = Field(False, description="true 면 이 유형의 난간에서 유형을 떼고(높이는 남는다) 지운다. false 면 쓰는 난간이 있을 때 거절")


@command("railtype.delete", kind="mutation", risk="write", group="model.railing", summary="난간 유형을 지운다(쓰는 난간이 있으면 detach 로 떼어 낸다)",
         args=RailTypeDelete, since="0.22", examples=[{"id": F.RLT1, "detach": True}])
def railtype_delete(ctx, a: RailTypeDelete):
    type_row(ctx, a.id)
    rails = railings_of(ctx, a.id)
    if rails and not a.detach:
        raise DobimError("CONSTRAINT", f"난간 {len(rails)}개가 이 유형을 쓴다", path="/id", hint="detach: true 로 난간에서 떼고 지우거나 railing.type.set 으로 바꾸세요",
                         details={"railings": rails[:20]})
    for rid in rails:
        detach(ctx, rid)
    ctx.put_el(a.id, None)
    return {"deleted": [a.id], "detached": len(rails)}


class RailTypeList(A):
    limit: int = Field(100, ge=1, le=K.RAILTYPES_MAX, description="최대 개수")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("railtype.list", kind="query", risk="read", group="model.railing",
         summary="난간 유형 일람표(이름·높이·부재 구성·쓰는 난간 수)", args=RailTypeList, idempotent=True, since="0.22",
         examples=[{}, {"limit": 5}])
def railtype_list(ctx, a: RailTypeList):
    ids = sorted(_types(ctx), key=lambda t: (ctx.st.el[t]["params"]["name"], t))
    after = C._dec_cursor(a.cursor, "pair")
    keys = [[ctx.st.el[t]["params"]["name"], t] for t in ids]
    start = 0 if after is None else next((k for k, n in enumerate(keys) if n > after), len(keys))
    rows = [_pub_type(ctx, t) for t in ids[start:start + a.limit]]
    items, cut = C._fit(rows, cursor_of=lambda k: C._enc_cursor(keys[start + k]))
    more = start + len(items) < len(ids)
    untyped = sum(1 for rr in ctx.st.by_kind.get("railing", ()) if "rltype" not in ctx.st.el[rr]["params"])
    return {"items": items, "untyped_railings": untyped, "page": C._page(C._enc_cursor(keys[start + len(items) - 1]) if more and items else None, len(ids), cut)}


# ---------------------------------------------------------------- 난간에 유형
RailIdsArg = Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX, description="난간 id 들")]


class RailingTypeAssign(A):
    ids: RailIdsArg
    type: Id | None = Field(description="입힐 난간 유형 id. null 이면 유형을 뗀다(높이는 남고 부재 구성은 지운다)")


@command("railing.type.set", kind="mutation", risk="write", group="model.railing",
         summary="난간에 난간 유형을 입히거나(높이·부재 구성을 유형 것으로) 뗀다",
         args=RailingTypeAssign, since="0.22", examples=[{"ids": [F.RL1], "type": F.RLT1}])
def railing_type_set(ctx, a: RailingTypeAssign):
    if len(set(a.ids)) != len(a.ids):
        raise DobimError("CONSTRAINT", "ids 에 같은 id 가 두 번 있다", path="/ids")
    tp = type_row(ctx, a.type, "/type")["params"] if a.type is not None else None
    n = 0
    for i, rid in enumerate(a.ids):
        if ctx.st.el.get(rid, {}).get("kind") != "railing":
            raise DobimError("NOT_FOUND", f"난간을 찾을 수 없다: {rid}", path=f"/ids/{i}")
        before = ctx.authored(rid, "railing", what="유형 지정")["params"]
        if tp is None:
            detach(ctx, rid)
        else:
            retype(ctx, rid, a.type, tp, f"/ids/{i}")
        n += ctx.st.el[rid]["params"] != before
    return {"count": len(a.ids), "changed": n}
