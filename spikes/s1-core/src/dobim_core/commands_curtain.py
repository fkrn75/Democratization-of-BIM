# SPDX-License-Identifier: MPL-2.0
"""커튼월 커맨드(W2 네 번째 PR, ARC-22: 상용 도구의 커튼월·커튼 그리드·멀리언·커튼 패널).

curtain.create       벽 축(직선 또는 곡선) + 높이 + 격자 규칙(세로 u·가로 v: 고정 개수·고정 간격·최대 간격) + 멀리언 유형으로 커튼월을 만든다.
curtain.set          축·높이·바닥 오프셋·격자 규칙·유형·교차부 우선순위·외곽 멀리언·패널 두께를 바꾼다.
curtain.grid         격자선 하나를 더하기·빼기·옮기기(규칙으로 생긴 선도, 개별로 더한 선도).
curtain.mullion.set  멀리언(격자선 한 칸)을 지우거나 유형을 바꾸거나 기본으로 되돌린다.
curtain.panel.set    패널(칸)을 유리·불투명·문·빈 칸으로 바꾼다.
curtain.type.set     멀리언 유형(사각 단면 폭·깊이)을 더하거나 고친다.
curtain.parts        멀리언·패널·격자선 목록과 유형별 집계(일람표). 쪽 나누기.
curtain.move         평행 이동. 지우기는 element.delete.

저장: kind "curtwall" 행 하나(params 는 curtain.py 머리말). 멀리언·패널은 행이 아니라 키("V<u>:<z0>"·"H<z>:<u0>"·"P<u0>:<z0>")로 가리키는 부속이고
IFC 는 IfcCurtainWall 아래 IfcRelAggregates 로 IfcMember·IfcPlate·IfcDoor 가 매달린다(GlobalId = part_guid(커튼월, "m:키"·"p:키")).
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dobim_core import arcgeom as AG
from dobim_core import curtain as CW
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core.commands import A, Id, Name, Num, _delete_one, _fresh_id, command, row  # noqa: F401
from dobim_core.errors import DobimError

Coord = Annotated[float, Field(allow_inf_nan=False, ge=-1e7, le=1e7)]
Axis = Annotated[list[Coord], Field(min_length=4, max_length=4, description="축 [x1,y1,x2,y2] mm(커튼월 면의 가운데 선, 시작 -> 끝)")]
Pt = Annotated[list[Coord], Field(min_length=2, max_length=2)]
Dim = Annotated[float, Field(gt=0, le=CW.TYPE_DIM_MAX, allow_inf_nan=False)]
TypeName = Annotated[str, Field(min_length=1, max_length=32)]
Key = Annotated[str, Field(min_length=2, max_length=K.CW_KEY_MAX, pattern=r"^[VHP][0-9.\-]+:[0-9.\-]+$")]


class Grid(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: Literal["none", "count", "spacing", "max_spacing"] = Field(description="none 격자선 없음, count 같은 간격 칸 수, spacing 고정 간격, max_spacing 이 간격 이하의 같은 간격")
    value: Annotated[float, Field(gt=0, le=1e6, allow_inf_nan=False)] | None = Field(None, description="count 면 칸 수(정수), spacing·max_spacing 이면 간격 mm")
    justify: Literal["start", "center", "end"] | None = Field(None, description="spacing 에서 남는 몫을 둘 자리의 반대쪽: start 시작에서 재기(기본), center 가운데 맞춤, end 끝에서 재기")

    @model_validator(mode="after")
    def _chk(self):
        if self.rule == "none" and self.value is not None:
            raise ValueError("rule none 은 value 를 받지 않는다")
        if self.rule != "none" and self.value is None:
            raise ValueError("value 가 필요하다")
        if self.rule == "count" and (not float(self.value).is_integer() or self.value > CW.LINES_MAX + 1):
            raise ValueError(f"count 는 1 ~ {CW.LINES_MAX + 1} 의 정수다")
        if self.justify is not None and self.rule != "spacing":
            raise ValueError("justify 는 spacing 에만")
        return self


def _grid(g: Grid) -> dict:
    out = {"rule": g.rule}
    if g.value is not None:
        out["value"] = float(g.value)
    if g.justify is not None and g.justify != "start":
        out["justify"] = g.justify
    return out


def _checked(p: dict) -> dict:
    p = CW.prune(p)
    why = K.row_params_error("curtwall", p)
    if why:
        raise DobimError("CONSTRAINT", f"커튼월이 올바르지 않다: {why}", hint="격자 간격·멀리언 폭·크기 상한(길이 400m·높이 200m·칸 6만)을 확인하세요")
    return p


def _arc_mid(axis, through):
    why = AG.problem(axis, through, 0.0)
    if why:
        raise DobimError("CONSTRAINT", f"곡선 축이 올바르지 않다: {why}", path="/arc_through")
    return AG.canonical_mid(axis, through)


def _summary(ctx, cid: str) -> dict:
    lay = CW.layout(ctx.st.el[cid]["params"])
    return {"id": cid, "length": round(lay["length"], 3), "u_lines": [round(u, 3) for u in lay["us"][1:-1]][:50], "v_lines": [round(z, 3) for z in lay["zs"][1:-1]][:50],
            "cells": (len(lay["us"]) - 1) * (len(lay["zs"]) - 1), "mullions": len(lay["mullions"]), "panels": len(lay["panels"])}


class CurtainCreate(A):
    id: Id | None = None
    level: Id = Field(description="커튼월이 놓인 레벨(바닥 = 레벨 + base_offset)")
    axis: Axis
    arc_through: Pt | None = Field(None, description="곡선 커튼월: 호 위의 한 점 [x,y](시작·끝과 함께 원호를 정한다). 격자선 사이는 현으로 잇는 꺾인 면이다")
    height: Annotated[float, Field(gt=0, le=CW.HEIGHT_MAX, allow_inf_nan=False)] = Field(description="높이 mm")
    base_offset: Num = Field(0.0, description="레벨에서 커튼월 바닥까지 mm")
    u_grid: Grid = Field(description="세로 격자선(축 방향) 규칙")
    v_grid: Grid = Field(description="가로 격자선(높이 방향) 규칙")
    mullion_width: Dim = Field(50.0, description="기본 멀리언 폭 mm(입면 방향)")
    mullion_depth: Dim = Field(150.0, description="기본 멀리언 깊이 mm(두께 방향)")
    border: Literal["same", "none", "other"] = Field("same", description="외곽 멀리언: same 기본 유형, none 없음, other 외곽 유형(border_width·border_depth)")
    border_width: Dim | None = None
    border_depth: Dim | None = None
    join: Literal["vertical", "horizontal"] = Field("vertical", description="교차부 우선순위: vertical 세로 연속(가로대가 끊긴다), horizontal 가로 연속")
    panel_thickness: Annotated[float, Field(gt=0, le=CW.PANEL_T_MAX, allow_inf_nan=False)] = Field(24.0, description="패널 두께 mm")
    name: Name | None = None


@command("curtain.create", kind="mutation", risk="write", group="model.curtain",
         summary="커튼월 생성: 벽 축(직선·곡선) + 높이 + 커튼 그리드(고정 개수·고정 간격·최대 간격) + 멀리언 유형. 멀리언·패널은 격자 칸마다의 부속, IFC 는 IfcCurtainWall 아래 IfcMember·IfcPlate",
         args=CurtainCreate, since="0.17",
         examples=[{"level": F.LV1, "axis": [0, -2000, 6000, -2000], "height": 3000, "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "count", "value": 2}},
                   {"level": F.LV1, "axis": [0, -3000, 6000, -3000], "arc_through": [3000, -4500], "height": 3000, "u_grid": {"rule": "max_spacing", "value": 1200},
                    "v_grid": {"rule": "none"}, "border": "other", "border_width": 80, "border_depth": 200}])
def curtain_create(ctx, a: CurtainCreate):
    _fresh_id(ctx, a, "curtwall")
    ctx.authored(a.level, "level", what="레벨에 커튼월을 추가하는 것")
    a.name = a.name or f"CW-{a.id[-6:]}"
    types = {"기본": {"width": float(a.mullion_width), "depth": float(a.mullion_depth)}}
    mullion = {"interior": "기본"}
    if a.border == "same":
        mullion["border"] = "기본"
    elif a.border == "other":
        if a.border_width is None or a.border_depth is None:
            raise DobimError("CONSTRAINT", "border other 는 border_width·border_depth 가 필요하다", path="/border_width")
        types["외곽"] = {"width": float(a.border_width), "depth": float(a.border_depth)}
        mullion["border"] = "외곽"
    p = {"name": a.name, "axis": [float(x) for x in a.axis], "height": float(a.height), "u_grid": _grid(a.u_grid), "v_grid": _grid(a.v_grid),
         "types": types, "mullion": mullion, "join": a.join, "panel_thickness": float(a.panel_thickness)}
    if a.arc_through is not None:
        p["arc"] = _arc_mid(p["axis"], a.arc_through)
    if a.base_offset:
        p["base_offset"] = float(a.base_offset)
    ctx.put_el(a.id, row("curtwall", "IfcCurtainWall", "curtwall.basic", _checked(p)))
    ctx.rel("on_level", a.id, a.level, True)
    return _summary(ctx, a.id)


class CurtainSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    axis: Axis | None = Field(None, description="새 축. 곡선이던 커튼월은 arc_through 를 함께 주지 않으면 직선이 된다")
    arc_through: Pt | None = None
    height: Annotated[float, Field(gt=0, le=CW.HEIGHT_MAX, allow_inf_nan=False)] | None = None
    base_offset: Num | None = None
    u_grid: Grid | None = None
    v_grid: Grid | None = None
    interior_type: TypeName | None = Field(None, description="내부 멀리언 유형(types 에 있는 이름)")
    border_type: TypeName | None = Field(None, description="외곽 멀리언 유형 이름, 또는 none(외곽 멀리언 없음)")
    join: Literal["vertical", "horizontal"] | None = None
    panel_thickness: Annotated[float, Field(gt=0, le=CW.PANEL_T_MAX, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in ("name", "axis", "arc_through", "height", "base_offset", "u_grid", "v_grid", "interior_type", "border_type", "join", "panel_thickness")):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("curtain.set", kind="mutation", risk="write", group="model.curtain",
         summary="커튼월 고치기: 축·곡선·높이·바닥 오프셋·격자 규칙·내부/외곽 멀리언 유형·교차부 우선순위·패널 두께·이름. 격자가 바뀌어 없어진 칸의 재정의는 버린다",
         args=CurtainSet, since="0.17", examples=[{"id": F.CW1, "v_grid": {"rule": "spacing", "value": 1000}, "join": "horizontal"}])
def curtain_set(ctx, a: CurtainSet):
    r = ctx.authored(a.id, "curtwall")
    p = dict(r["params"])
    for k in ("name", "join"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    for k in ("height", "panel_thickness"):
        if getattr(a, k) is not None:
            p[k] = float(getattr(a, k))
    if a.base_offset is not None:
        if a.base_offset:
            p["base_offset"] = float(a.base_offset)
        else:
            p.pop("base_offset", None)
    if a.axis is not None:
        p["axis"] = [float(x) for x in a.axis]
        p.pop("arc", None)
    if a.arc_through is not None:
        p["arc"] = _arc_mid(p["axis"], a.arc_through)
    for k in ("u_grid", "v_grid"):
        if getattr(a, k) is not None:
            p[k] = _grid(getattr(a, k))
    m = dict(p["mullion"])
    if a.interior_type is not None:
        if a.interior_type not in p["types"]:
            raise DobimError("CONSTRAINT", f"없는 멀리언 유형이다: {a.interior_type}", path="/interior_type", hint="curtain.type.set 으로 먼저 만드세요")
        m["interior"] = a.interior_type
    if a.border_type is not None:
        if a.border_type == "none":
            m.pop("border", None)
        elif a.border_type not in p["types"]:
            raise DobimError("CONSTRAINT", f"없는 멀리언 유형이다: {a.border_type}", path="/border_type", hint="curtain.type.set 으로 먼저 만드세요")
        else:
            m["border"] = a.border_type
    p["mullion"] = m
    ctx.put_el(a.id, dict(r, params=_checked(p)))
    return _summary(ctx, a.id)


class CurtainGrid(A):
    id: Id
    dir: Literal["u", "v"] = Field(description="u 세로 격자선(축 방향 위치), v 가로 격자선(바닥에서 높이)")
    op: Literal["add", "remove", "move"]
    at: Num = Field(description="add: 새 선 위치, remove·move: 지금 있는 선 위치(1mm 안) mm")
    to: Num | None = Field(None, description="move: 새 위치 mm")


@command("curtain.grid", kind="mutation", risk="write", group="model.curtain",
         summary="커튼 그리드 격자선 하나 더하기·빼기·옮기기(u 는 축 시작점에서 호 위 거리, v 는 커튼월 바닥에서). 규칙으로 생긴 선도 뺄 수 있다",
         args=CurtainGrid, since="0.17", examples=[{"id": F.CW1, "dir": "u", "op": "add", "at": 750}])
def curtain_grid(ctx, a: CurtainGrid):
    r = ctx.authored(a.id, "curtwall")
    p = dict(r["params"])
    lay = CW.layout(p)
    cur = list(lay["us"] if a.dir == "u" else lay["zs"])[1:-1]
    size = lay["length"] if a.dir == "u" else lay["height"]
    adds, rems = list(p.get(f"{a.dir}_add", ())), list(p.get(f"{a.dir}_remove", ()))

    def remove(x):
        hit = [c for c in cur if abs(c - x) <= CW.MERGE_TOL]
        if not hit:
            raise DobimError("CONSTRAINT", f"{a.dir} 격자선이 {x:g} 에 없다", path="/at", hint="curtain.parts what=grid 로 격자선 위치를 보세요")
        c = hit[0]
        mine = [v for v in adds if abs(v - c) <= CW.MERGE_TOL]
        if mine:
            adds.remove(mine[0])
        else:
            rems.append(float(c))
        cur.remove(c)

    def add(x):
        if not CW.EDGE_TOL < x < size - CW.EDGE_TOL:
            raise DobimError("CONSTRAINT", f"격자선은 0 과 {size:.1f} 사이여야 한다", path="/to" if a.op == "move" else "/at")
        if any(abs(c - x) <= CW.MERGE_TOL for c in cur):
            raise DobimError("CONSTRAINT", f"{x:g} 에 이미 격자선이 있다", path="/at")
        back = [v for v in rems if abs(v - x) <= CW.REMOVE_TOL]
        if back:
            rems.remove(back[0])
        else:
            adds.append(float(x))
    if a.op == "add":
        add(a.at)
    elif a.op == "remove":
        remove(a.at)
    else:
        if a.to is None:
            raise DobimError("CONSTRAINT", "move 는 to 가 필요하다", path="/to")
        remove(a.at)
        add(a.to)
    for k, v in ((f"{a.dir}_add", adds), (f"{a.dir}_remove", rems)):
        if v:
            p[k] = sorted(v)
        else:
            p.pop(k, None)
    ctx.put_el(a.id, dict(r, params=_checked(p)))
    return _summary(ctx, a.id)


class CurtainMullionSet(A):
    id: Id
    keys: Annotated[list[Key], Field(min_length=1, max_length=5000)] = Field(description="멀리언 키 목록(curtain.parts 의 key, 예: V1500:0)")
    type: TypeName = Field(description="유형 이름, delete(지우기), default(격자 기본으로 되돌리기)")


@command("curtain.mullion.set", kind="mutation", risk="write", group="model.curtain",
         summary="멀리언(격자선 한 칸)을 지우거나(delete) 유형을 바꾸거나 기본으로 되돌린다(default). 지운 자리는 패널이 이어진다",
         args=CurtainMullionSet, since="0.17", examples=[{"id": F.CW1, "keys": ["V1500:0"], "type": "delete"}])
def curtain_mullion_set(ctx, a: CurtainMullionSet):
    r = ctx.authored(a.id, "curtwall")
    p = dict(r["params"])
    mk, _pk = CW.all_keys(p)
    bad = [k for k in a.keys if k[0] == "P" or k not in mk]
    if bad:
        raise DobimError("CONSTRAINT", f"없는 멀리언 키다: {bad[0]}", path="/keys", hint="curtain.parts what=mullions 로 키를 보세요", details={"keys": bad[:20]})
    if a.type not in ("delete", "default") and a.type not in p["types"]:
        raise DobimError("CONSTRAINT", f"없는 멀리언 유형이다: {a.type}", path="/type", hint="curtain.type.set 으로 먼저 만드세요")
    mo = dict(p.get("mullion_overrides", {}))
    for k in a.keys:
        if a.type == "default":
            mo.pop(k, None)
        else:
            mo[k] = "" if a.type == "delete" else a.type
    lay = CW.layout(dict(p, mullion_overrides={}) if mo else p) if a.type not in ("delete", "default") else None
    if lay is not None:   # 기본과 같은 유형은 재정의로 남기지 않는다(저장 형식이 경로와 무관하게 같다)
        dflt = {m[0]: m[2] for m in lay["mullions"]}
        for k in a.keys:
            if dflt.get(k) == a.type:
                mo.pop(k, None)
    if mo:
        p["mullion_overrides"] = dict(sorted(mo.items()))
    else:
        p.pop("mullion_overrides", None)
    ctx.put_el(a.id, dict(r, params=_checked(p)))
    return _summary(ctx, a.id)


class CurtainPanelSet(A):
    id: Id
    keys: Annotated[list[Key], Field(min_length=1, max_length=5000)] = Field(description="패널 키 목록(curtain.parts 의 key, 예: P0:0)")
    type: Literal["glass", "opaque", "door", "empty"] = Field(description="glass 유리(기본), opaque 불투명 패널, door 문 패널(IFC IfcDoor), empty 빈 칸")


@command("curtain.panel.set", kind="mutation", risk="write", group="model.curtain",
         summary="패널(격자 칸)을 유리·불투명 패널·문 패널·빈 칸으로 바꾼다. 문 패널은 IFC IfcDoor, 평면에 문 열림 기호",
         args=CurtainPanelSet, since="0.17", examples=[{"id": F.CW1, "keys": ["P0:0"], "type": "door"}])
def curtain_panel_set(ctx, a: CurtainPanelSet):
    r = ctx.authored(a.id, "curtwall")
    p = dict(r["params"])
    _mk, pk = CW.all_keys(p)
    bad = [k for k in a.keys if k not in pk]
    if bad:
        raise DobimError("CONSTRAINT", f"없는 패널 키다: {bad[0]}", path="/keys", hint="curtain.parts what=panels 로 키를 보세요", details={"keys": bad[:20]})
    po = dict(p.get("panel_overrides", {}))
    for k in a.keys:
        if a.type == CW.DEFAULT_PANEL:
            po.pop(k, None)
        else:
            po[k] = a.type
    if po:
        p["panel_overrides"] = dict(sorted(po.items()))
    else:
        p.pop("panel_overrides", None)
    ctx.put_el(a.id, dict(r, params=_checked(p)))
    return _summary(ctx, a.id)


class CurtainTypeSet(A):
    id: Id
    name: TypeName = Field(description="멀리언 유형 이름(없으면 더하고, 있으면 고친다)")
    width: Dim = Field(description="폭 mm(입면 방향)")
    depth: Dim = Field(description="깊이 mm(두께 방향)")


@command("curtain.type.set", kind="mutation", risk="write", group="model.curtain",
         summary="멀리언 유형(사각 단면 폭·깊이) 더하기·고치기. 그 유형을 쓰는 멀리언이 모두 바뀐다",
         args=CurtainTypeSet, since="0.17", examples=[{"id": F.CW1, "name": "중간대", "width": 60, "depth": 200}])
def curtain_type_set(ctx, a: CurtainTypeSet):
    r = ctx.authored(a.id, "curtwall")
    p = dict(r["params"])
    types = dict(p["types"])
    if a.name not in types and len(types) >= CW.TYPES_MAX:
        raise DobimError("CONSTRAINT", f"멀리언 유형은 {CW.TYPES_MAX}개까지다", path="/name")
    types[a.name] = {"width": float(a.width), "depth": float(a.depth)}
    p["types"] = types
    ctx.put_el(a.id, dict(r, params=_checked(p)))
    return {**_summary(ctx, a.id), "types": sorted(types)}


class CurtainMove(A):
    id: Id
    dx: Num
    dy: Num


@command("curtain.move", kind="mutation", risk="write", group="model.curtain", summary="커튼월 평행 이동(멀리언·패널 재정의는 그대로 따라간다)",
         args=CurtainMove, since="0.17", examples=[{"id": F.CW1, "dx": 0, "dy": -500}])
def curtain_move(ctx, a: CurtainMove):
    r = ctx.authored(a.id, "curtwall")
    p = dict(r["params"])
    x1, y1, x2, y2 = p["axis"]
    p["axis"] = [x1 + a.dx, y1 + a.dy, x2 + a.dx, y2 + a.dy]
    if "arc" in p:
        p["arc"] = [p["arc"][0] + a.dx, p["arc"][1] + a.dy]
    ctx.put_el(a.id, dict(r, params=_checked(p)))
    return {"id": a.id, "axis": p["axis"]}


class CurtainParts(A):
    id: Id
    what: Literal["mullions", "panels", "grid", "summary"] = Field("summary", description="mullions 멀리언, panels 패널, grid 격자선 위치, summary 유형별 집계만")
    key: Key | None = Field(None, description="이 키 하나만(조회)")
    limit: int = Field(200, ge=1, le=5000, description="최대 개수. 응답 크기 상한(약 8,000토큰)에 걸리면 이보다 적게 돌려주고 page.truncated=true 와 "
                       "page.next_cursor(다음 쪽)를 준다. 한 번에 더 많이 받으려면 compact=true(배열 행, 약 4배)·summary=false")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")
    offset: int | None = Field(None, ge=0, le=10_000_000, description="커서 대신 몇 번째 항목부터(0부터). 원자 script 처럼 앞 응답을 못 읽는 곳에서 "
                               "offset 을 미리 나눠 부른다(응답의 next_offset 이 다음 시작, 잘렸으면 page.truncated=true). cursor 와 함께 줄 수 없다")
    compact: bool = Field(False, description="true 면 items 를 열 이름(result.columns) 순서의 배열로 준다(키 이름을 되풀이하지 않아 한 쪽에 약 4배)")
    summary: bool = Field(True, description="false 면 집계(summary)를 빼고 목록만(쪽마다 같은 집계를 다시 받지 않는다)")


def _seg_len(q) -> float:
    return ((q[1][0] - q[0][0]) ** 2 + (q[1][1] - q[0][1]) ** 2) ** 0.5


@command("curtain.parts", kind="query", risk="read", group="model.curtain",
         summary="커튼월의 멀리언·패널·격자선 목록(키·유형·위치·길이·면적, IFC 이름표 m:키·p:키)과 유형별 개수·길이·면적 집계(일람표)",
         args=CurtainParts, idempotent=True, since="0.17", examples=[{"id": F.CW1}, {"id": F.CW1, "what": "mullions", "limit": 5}])
def curtain_parts(ctx, a: CurtainParts):
    from dobim_core import queries as Q
    ctx.row(a.id, "curtwall")
    g = ctx.g
    lay = g.q(Q.curtain_layout, a.id)
    p = ctx.st.el[a.id]["params"]
    nm, npn, by_t, by_p = g.q(Q.curtain_stats, a.id)
    summary = {"mullions": nm, "panels": npn,
               "by_type": {t: {"count": c, "length_m": round(s / 1000.0, 3), **p["types"][t]} for t, (c, s) in sorted(by_t.items())},
               "by_panel": {k: {"count": c, "area_m2": round(s, 3)} for k, (c, s) in sorted(by_p.items())},
               "join": p["join"], "length": round(lay["length"], 3), "height": p["height"]}
    if a.what == "summary":
        return summary
    if a.what == "grid":
        rows = [{"dir": "u", "at": round(u, 3)} for u in lay["us"][1:-1]] + [{"dir": "v", "at": round(z, 3)} for z in lay["zs"][1:-1]]
    elif a.what == "mullions":
        rows = [{"key": k, "label": f"m:{k}", "dir": d, "type": t, "at": float(k[1:].split(":")[0]),
                 "from": round(z0, 3) if d == "V" else float(k.split(":")[1]), "to": round(z1, 3) if d == "V" else None,
                 "length": round((z1 - z0) if d == "V" else _seg_len(q), 3)} for k, d, t, q, z0, z1 in lay["mullions"]]
        for x in rows:
            if x["to"] is None:
                x.pop("to")
    else:
        rows = [{"key": k, "label": f"p:{k}", "type": t, "z0": round(z0, 3), "z1": round(z1, 3), "width": round(_seg_len(q), 3),
                 "area_m2": round(_seg_len(q) * (z1 - z0) / 1e6, 6)} for k, t, q, z0, z1, _c, _t in lay["panels"]]
    if a.key is not None:
        rows = [x for x in rows if x.get("key") == a.key]
        if not rows:
            raise DobimError("NOT_FOUND", f"이 커튼월에 없는 키다: {a.key}", path="/key")
    if a.offset is not None and a.cursor is not None:
        raise DobimError("CONSTRAINT", "offset 과 cursor 는 함께 줄 수 없다", path="/offset")
    start = a.offset if a.offset is not None else C_dec(a.cursor)
    base = {"summary": summary} if a.summary else {}
    cols = None
    if a.compact:   # 열 이름 한 벌 + 배열 행(W2 아홉 번째 PR, 실사용 결함 4: 4,721 패널을 쪽 수십 번에 다 받는다)
        cols = list(_COLS[a.what])
        rows = [[_cv(x.get(c)) for c in cols] for x in rows]
        base["columns"] = cols
    sub = rows[start:start + a.limit]
    from dobim_core import limits
    hint = a.offset is not None or a.compact
    extra = {"next_offset": 10_000_000} if hint else {}
    items, cut = limits.fit(sub, base={**base, **extra}, cursor_of=lambda k: limits.enc_cursor(start + k + 1))
    more = start + len(items) < len(rows)
    out = {**base, "items": items}
    if hint:   # 다음 시작 번호(끝이면 null). 잘렸는지는 page.truncated, 다음 쪽 커서는 page.next_cursor
        out["next_offset"] = start + len(items) if more else None
    out["page"] = limits.page(limits.enc_cursor(start + len(items)) if more else None, len(rows), cut)
    return out


def _cv(v):
    """배열 행의 값: 정수인 실수는 정수로(1200.0 -> 1200, 한 쪽에 더 담는다)."""
    return int(v) if isinstance(v, float) and v.is_integer() and abs(v) < 2**53 else v


_COLS = {"mullions": ("key", "dir", "type", "at", "from", "to", "length"),   # label = "m:"+key, 가로 멀리언의 to 는 null
         "panels": ("key", "type", "z0", "z1", "width", "area_m2"),          # label = "p:"+key
         "grid": ("dir", "at")}


def C_dec(c) -> int:
    from dobim_core import limits
    v = limits.dec_cursor(c, "int")
    return 0 if v is None else v
