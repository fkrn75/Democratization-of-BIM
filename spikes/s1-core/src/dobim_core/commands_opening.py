# SPDX-License-Identifier: MPL-2.0
"""독립 개구 객체 커맨드(W2 세 번째 PR, ARC-34: 상용 도구의 수직 개구·샤프트 개구·벽 개구).

opening.vertical  슬래브 하나를 경계 다각형으로 수직 관통하는 개구(지붕 슬래브 use=roof 포함).
opening.shaft     레벨 + base_offset 에서 height 까지 z 범위의 저작 슬래브를 경계 다각형으로 한 번에 관통하는 샤프트 개구(나중에 만든 슬래브도 관통한다).
opening.wall      벽 하나에 문·창 없는 사각·원 개구(offset 은 축 시작점에서 호 위 거리, sill 은 레벨 기준). 곡선 벽은 사각만.
opening.delete    독립 개구를 지운다(호스트는 그대로 메워진다).

저장: kind "opening" 비 IFC 의도 요소(DoBIM_Defs 로 왕복). 호스트 params 는 바꾸지 않는다: 도면(slab_info 구멍·벽 모양 개구부)과 IFC(호스트마다
IfcOpeningElement + IfcRelVoidsElement 부속, GlobalId = part_guid(호스트, "void:<개구 id>"))가 질의(queries.slab_voids·wall_voids)로 뺀다.
호스트를 지우면 개구는 고아로 남고 그리지 않는다(치수의 고아와 같은 규칙). 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import solids as SO
from dobim_core import walltop as WT
from dobim_core.commands import A, Id, Name, Num, Pos, SlabBoundary, _fresh_id, _slab_boundary, command, row, wall_len
from dobim_core.errors import DobimError


def _count_ok(ctx) -> None:
    if len(ctx.st.by_kind.get("opening", ())) >= K.OPENINGS_OBJ_MAX:
        raise DobimError("CONSTRAINT", f"독립 개구는 {K.OPENINGS_OBJ_MAX}개까지다", path="/id")


def _voids_of(ctx, host: str, skip: str | None = None) -> list:
    """이 슬래브를 가리키는 수직 개구 경계들(skip 제외)."""
    return [ctx.st.el[o]["params"]["boundary"] for o in sorted(ctx.st.by_kind.get("opening", ()))
            if o != skip and ctx.st.el[o]["params"].get("mode") == "vertical" and ctx.st.el[o]["params"]["host"] == host]


def _shafts_on(ctx, s: str) -> list:
    """슬래브 s 를 관통하는 샤프트 개구 경계들(queries.slab_voids 와 같은 규칙)."""
    out = []
    for o in sorted(ctx.st.by_kind.get("opening", ())):
        p = ctx.st.el[o]["params"]
        if p.get("mode") != "shaft" or ctx.st.el.get(p["level"]) is None:
            continue
        z0 = ctx.st.el[p["level"]]["params"]["elevation"] + p.get("base_offset", 0.0)
        if s in shaft_slabs(ctx, p["boundary"], z0, z0 + p["height"]):
            out.append(p["boundary"])
    return out


def _slab_zr(ctx, s: str) -> tuple:
    p = ctx.st.el[s]["params"]
    lv = ctx.rels_out("on_level", s)[0]
    top = ctx.st.el[lv]["params"]["elevation"] + p["top_offset"]
    return (top - p["thickness"], top)


class OpeningVertical(A):
    id: Id | None = None
    host: Id = Field(description="관통할 슬래브(지붕 슬래브 포함)")
    boundary: SlabBoundary
    name: Name | None = None


@command("opening.vertical", kind="mutation", risk="write", group="model.opening",
         summary="수직 개구: 슬래브 하나를 경계 다각형으로 관통하는 독립 개구(슬래브 params 는 그대로, 도면·IFC 가 뺀다). IFC 는 IfcOpeningElement + IfcRelVoidsElement",
         args=OpeningVertical, since="0.16", examples=[{"host": F.S1, "boundary": [[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]}])
def opening_vertical(ctx, a: OpeningVertical):
    r = ctx.authored(a.host, "slab", what="수직 개구 호스트")
    b = _slab_boundary(a.boundary)
    holes = [list(map(list, o)) for o in r["params"].get("openings", ())] + _voids_of(ctx, a.host) + _shafts_on(ctx, a.host)
    why = K.openings_problem(r["params"]["boundary"], holes + [b])
    if why:
        raise DobimError("CONSTRAINT", f"수직 개구가 슬래브에 맞지 않는다: {why}", path="/boundary", hint="슬래브 경계 안쪽, 다른 구멍과 겹치지 않게")
    _count_ok(ctx)
    _fresh_id(ctx, a, "opening")
    a.name = a.name or f"OP-{a.id[-6:]}"
    ctx.put_el(a.id, row("opening", "", "opening.void", {"name": a.name, "mode": "vertical", "host": a.host, "boundary": b}))
    return {"id": a.id, "host": a.host, "area_m2": round(abs(K.polygon_area2(b)) / 2e6, 6)}


class OpeningShaft(A):
    id: Id | None = None
    level: Id = Field(description="샤프트 바닥 레벨")
    boundary: SlabBoundary
    height: Annotated[float, Field(gt=0, le=K.SHAFT_HEIGHT_MAX, allow_inf_nan=False)] = Field(description="바닥(레벨 + base_offset)에서 위로 mm")
    base_offset: Num | None = Field(None, description="레벨 기준 바닥 오프셋 mm(생략·0 이면 레벨에서)")
    name: Name | None = None


def shaft_slabs(ctx, boundary, z0: float, z1: float, skip: str | None = None) -> list:
    """샤프트가 관통하는 저작 슬래브(queries.slab_voids 와 같은 규칙: z 범위가 겹치고 경계가 슬래브 경계 안이며 슬래브 구멍과 겹치지 않는다)."""
    out = []
    for s in sorted(ctx.st.by_kind.get("slab", ())):
        r = ctx.st.el[s]
        if r["origin"] != "authored":
            continue
        zb, zt = _slab_zr(ctx, s)
        if z0 <= zt + 1e-6 and zb < z1 - 1e-6 and all(K.point_in_polygon(q, r["params"]["boundary"]) == 1 for q in boundary) and not any(
                K.point_in_polygon(q, h) >= 0 for h in r["params"].get("openings", ()) for q in boundary):
            out.append(s)
    return out


@command("opening.shaft", kind="mutation", risk="write", group="model.opening",
         summary="샤프트 개구: 레벨 범위(바닥 레벨 + base_offset 에서 height 까지)의 슬래브를 경계 다각형으로 한 번에 관통하는 독립 개구. 평면은 경계 + X 기호",
         args=OpeningShaft, since="0.16", examples=[{"level": F.LV1, "boundary": [[4000, 500], [5000, 500], [5000, 1500], [4000, 1500]], "height": 6600}])
def opening_shaft(ctx, a: OpeningShaft):
    lv = ctx.authored(a.level, "level", what="샤프트 개구 바닥 레벨")
    b = _slab_boundary(a.boundary)
    _count_ok(ctx)
    _fresh_id(ctx, a, "opening")
    a.name = a.name or f"SH-{a.id[-6:]}"
    p = {"name": a.name, "mode": "shaft", "level": a.level, "boundary": b, "height": float(a.height)}
    if a.base_offset:
        p["base_offset"] = float(a.base_offset)
    z0 = lv["params"]["elevation"] + p.get("base_offset", 0.0)
    cut = shaft_slabs(ctx, b, z0, z0 + p["height"])
    for s in cut:   # 관통하는 슬래브의 수직 개구와 겹치면 거절한다
        for v in _voids_of(ctx, s):
            if K.openings_problem(ctx.st.el[s]["params"]["boundary"], [v, b]):
                raise DobimError("CONSTRAINT", f"샤프트 개구가 슬래브 {s} 의 수직 개구와 겹친다", path="/boundary")
    ctx.put_el(a.id, row("opening", "", "opening.void", p))
    return {"id": a.id, "slabs": cut, "bottom": z0, "top": z0 + p["height"]}


class OpeningWall(A):
    id: Id | None = None
    host: Id = Field(description="뚫을 벽(곡선 벽은 사각만)")
    shape: Literal["rect", "circle"] = "rect"
    offset: Num = Field(description="벽 축 시작점에서 개구 가운데까지 mm(곡선 벽은 호 위 거리)")
    sill: Num = Field(0.0, description="레벨 기준 개구 아래 끝 mm")
    width: Pos = 1000.0
    height: Pos = 1000.0
    name: Name | None = None


def wall_fit(ctx, host: str, d: dict, skip: str | None = None, path: str = "/offset") -> None:
    """벽 개구 d 가 벽 입면(축 길이 x 바닥~윗면, 상단 윤곽) 안이고 모양 개구부·다른 벽 개구·문 상자와 겹치지 않는가."""
    p = ctx.st.el[host]["params"]
    ln = wall_len(p)
    b0 = WT.base(p)
    top = p.get("top_profile")
    outer = WT.loop(top, 0.0, ln, b0) if top is not None else [(0.0, b0), (ln, b0), (ln, b0 + p["height"]), (0.0, b0 + p["height"])]
    others = list(p.get("openings", ())) + [K.opening_shape(ctx.st.el[o]["params"]) for o in sorted(ctx.st.by_kind.get("opening", ()))
                                            if o != skip and ctx.st.el[o]["params"].get("mode") == "wall" and ctx.st.el[o]["params"]["host"] == host]
    why = SO.openings_problem(outer, others + [d], absolute_sill=True) or (WT.under_problem(top, [d]) if top is not None else None)
    if why:
        raise DobimError("CONSTRAINT", f"벽 개구가 벽에 맞지 않는다: {why}", path=path)
    from dobim_core.commands import _hosted_span
    u0, u1 = d["offset"] - d["width"] / 2.0, d["offset"] + d["width"] / 2.0
    for h in sorted(ctx.rels_out("hosts", host)):
        lo, hi = _hosted_span(ctx, h)
        if lo < u1 - 1e-6 and u0 < hi - 1e-6 and ctx.st.el[h]["kind"] == "door":
            hp = ctx.st.el[h]["params"]
            if hp["sill"] < d["sill"] + d["height"] - 1e-6 and d["sill"] < hp["sill"] + hp["height"] - 1e-6:
                raise DobimError("CONSTRAINT", f"벽 개구가 문·창 {h} 와 겹친다", path=path)


@command("opening.wall", kind="mutation", risk="write", group="model.opening",
         summary="벽 개구: 벽 하나에 문·창 없는 사각·원 개구를 독립 객체로 낸다(벽 params 는 그대로). 곡선 벽은 사각만(호 위 거리 offset)",
         args=OpeningWall, since="0.16", examples=[{"host": F.W2, "offset": 2800, "sill": 900, "width": 800, "height": 1200},
                                                    {"host": F.W1, "shape": "circle", "offset": 4500, "sill": 1500, "width": 600, "height": 600}])
def opening_wall(ctx, a: OpeningWall):
    r = ctx.authored(a.host, "wall", what="벽 개구 호스트")
    if a.shape == "circle" and abs(a.width - a.height) > 1e-6:
        raise DobimError("CONSTRAINT", "원 개구는 폭과 높이가 같아야 한다", path="/height")
    if a.shape != "rect" and "arc" in r["params"]:
        raise DobimError("CONSTRAINT", "곡선 벽의 벽 개구는 사각만 된다", path="/shape")
    d = {"shape": a.shape, "offset": float(a.offset), "sill": float(a.sill), "width": float(a.width), "height": float(a.height)}
    wall_fit(ctx, a.host, d)
    _count_ok(ctx)
    _fresh_id(ctx, a, "opening")
    a.name = a.name or f"OP-{a.id[-6:]}"
    ctx.put_el(a.id, row("opening", "", "opening.void", {"name": a.name, "mode": "wall", "host": a.host, **d}))
    return {"id": a.id, "host": a.host, "area_m2": round(abs(SO.area2(SO.opening_loop(d))) / 2e6, 6)}


class OpeningDelete(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=200, description="지울 독립 개구 id 목록")]


@command("opening.delete", kind="mutation", risk="write", group="model.opening", summary="독립 개구(수직·샤프트·벽)를 지운다(호스트는 메워진다)",
         args=OpeningDelete, since="0.16", examples=[{"ids": [F.OP1]}])
def opening_delete(ctx, a: OpeningDelete):
    ids = list(dict.fromkeys(a.ids))
    for i in ids:
        ctx.row(i, "opening")
    for i in ids:
        ctx.put_el(i, None)
    return {"deleted": ids}
