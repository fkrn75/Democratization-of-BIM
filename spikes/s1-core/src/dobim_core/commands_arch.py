# SPDX-License-Identifier: MPL-2.0
"""건축 요소 커맨드(W1 1-F 3차): 천장(ARC-21)과 경사로(ARC-32).

새 요소 종류를 만들지 않고 일반 솔리드(solid)로 저장한다. IFC 클래스는 범주가 정한다(천장 IfcCovering CEILING, 경사로 IfcRamp).
ceiling.create  공간 경계(또는 직접 준 윤곽)로 수평 천장판을 만든다. 천장 높이 = 판 밑면(레벨 기준). 격자(grid)를 주면 반사 천장 평면에 격자를 긋는다.
ceiling.set     천장 높이·두께·격자를 고친다(윤곽·이동·삭제는 solid.set·solid.move·element.delete).
ramp.create     걸음선(run, 아래 -> 위)과 오름(rise)으로 경사로를 만든다. 걸음선을 따라 옆 윤곽(경사면 + 아래·위 참)을 세운 수직 판(panel)이고
                폭이 판 두께다. 평면은 걸음선에 오름 화살표를 더한다.
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from math import sqrt
from typing import Annotated

from pydantic import Field, model_validator

from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core.commands import A, Id, Name, Pos, command
from dobim_core.errors import DobimError

CEIL_T = 15.0           # 천장판 두께 기본값 mm(석고보드 한 겹 + 여유)
RAMP_SLOPE_WARN = 1.0 / 12.0   # 이보다 급하면 경고(장애인 편의시설 기준 1/12)
RAMP_SLOPE_MAX = 1.0 / 3.0     # 이보다 급하면 거절(계단으로)

Coord = Annotated[float, Field(allow_inf_nan=False, ge=-1e7, le=1e7)]
Pt2 = Annotated[list[Coord], Field(min_length=2, max_length=2)]
Poly = Annotated[list[Pt2], Field(min_length=3, max_length=256)]
Grid = Annotated[list[Annotated[float, Field(ge=100, le=5000, allow_inf_nan=False)]], Field(min_length=2, max_length=2)]
NonNeg = Annotated[float, Field(ge=0, le=1e5, allow_inf_nan=False)]


def _grid(size, origin, poly) -> dict:
    """격자 값. 원점을 주지 않으면 윤곽 경계 상자 가운데(방 가운데 맞춤)."""
    if origin is None:
        xs, ys = [q[0] for q in poly], [q[1] for q in poly]
        origin = [(min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0]
    return {"size": [float(v) for v in size], "origin": [float(v) for v in origin]}


def _checked(p: dict) -> dict:
    why = K.row_params_error("solid", p)
    if why:
        raise DobimError("CONSTRAINT", f"솔리드가 올바르지 않다: {why}")
    return p


# ================================================================ 천장
class CeilingCreate(A):
    id: Id | None = None
    space: Id | None = Field(None, description="이 공간의 경계·섬 구멍·레벨로 만든다")
    level: Id | None = Field(None, description="space 없이 만들 때 레벨")
    boundary: Poly | None = Field(None, description="space 없이 만들 때 윤곽 [[x,y],...]")
    height: Pos | None = Field(None, description="천장 높이 mm(레벨에서 판 밑면까지). 생략하면 공간의 ceiling_height")
    thickness: Pos = Field(CEIL_T, description="판 두께 mm(밑면에서 위로)")
    grid: Grid | None = Field(None, description="천장 격자 간격 [x, y] mm(반사 천장 평면에 긋는다)")
    grid_origin: Pt2 | None = Field(None, description="격자 원점(생략하면 윤곽 가운데)")
    name: Name | None = None

    @model_validator(mode="after")
    def _src(self):
        free = (self.level is not None, self.boundary is not None)
        if not ((self.space is not None and free == (False, False)) or (self.space is None and free == (True, True))):
            raise ValueError("space 하나, 또는 level 과 boundary 를 함께 준다")
        if self.grid_origin is not None and self.grid is None:
            raise ValueError("grid_origin 은 grid 와 함께 준다")
        return self


@command("ceiling.create", kind="mutation", risk="write", group="model.solid",
         summary="천장판(IfcCovering CEILING): 공간 경계나 윤곽으로 수평 판, 밑면 = 천장 높이. 격자는 반사 천장 평면에 긋는다",
         args=CeilingCreate, since="0.13",
         examples=[{"level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "height": 2400, "grid": [600, 600]}])
def ceiling_create(ctx, a: CeilingCreate):
    C._fresh_id(ctx, a, "solid")
    holes = []
    if a.space is not None:
        sp = ctx.authored(a.space, "space", what="공간으로 천장을 만드는 것")["params"]
        level = ctx.rels_out("on_level", a.space)[0]
        poly = [[float(c) for c in q] for q in sp["boundary"]]
        holes = [[[float(c) for c in q] for q in h] for h in sp.get("holes", ())]
        height = a.height if a.height is not None else sp.get("ceiling_height")
        if height is None:
            raise DobimError("CONSTRAINT", "공간에 천장 높이(ceiling_height)가 없다: height 를 주세요", path="/height")
        name = a.name or f"천장-{sp['name']}"[:64]
    else:
        level = a.level
        ctx.authored(level, "level", what="레벨에 천장을 추가하는 것")
        poly = [[float(c) for c in q] for q in a.boundary]
        if a.height is None:
            raise DobimError("CONSTRAINT", "height 가 필요하다(공간 없이 만들 때)", path="/height")
        height = a.height
        name = a.name or f"CL-{a.id[-6:]}"
    t = float(a.thickness)
    p = {"name": name, "category": "ceiling", "form": "plate", "polygon": poly, "thickness": t,
         "base": [poly[0][0], poly[0][1], float(height) + t], "pitch": 0.0, "dir": 0.0}
    if holes:
        p["holes"] = holes
    if a.grid is not None:
        p["grid"] = _grid(a.grid, a.grid_origin, poly)
    ctx.put_el(a.id, C.row("solid", K.SO.SOLID_CATEGORIES["ceiling"][0], "solid.generic", _checked(p)))
    ctx.rel("on_level", a.id, level, True)
    return {"id": a.id, "height": float(height)}


class CeilingSet(A):
    forbid_null = True
    id: Id
    height: Pos | None = Field(None, description="천장 높이 mm(판 밑면)")
    thickness: Pos | None = None
    grid: Grid | None = Field(None, description="격자 간격 [x, y] mm")
    grid_origin: Pt2 | None = None
    clear_grid: bool = Field(False, description="참이면 격자를 뗀다")

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.height, self.thickness, self.grid, self.grid_origin)) and not self.clear_grid:
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.clear_grid and (self.grid is not None or self.grid_origin is not None):
            raise ValueError("clear_grid 와 grid 를 함께 줄 수 없다")
        return self


def _ceiling_row(ctx, eid: str) -> dict:
    r = ctx.authored(eid, "solid", what="천장")
    p = r["params"]
    if p["category"] != "ceiling" or p["form"] != "plate" or p["pitch"] != 0:
        raise DobimError("CONSTRAINT", "천장판(범주 ceiling 의 수평 판)이 아니다", path="/id")
    return r


@command("ceiling.set", kind="mutation", risk="write", group="model.solid",
         summary="천장판의 높이·두께·격자를 고친다",
         args=CeilingSet, since="0.13", examples=[{"id": F.SO1, "height": 2600, "grid": [600, 1200]}])
def ceiling_set(ctx, a: CeilingSet):
    r = _ceiling_row(ctx, a.id)
    p = dict(r["params"])
    t0 = p["thickness"]
    height = a.height if a.height is not None else p["base"][2] - t0
    t = float(a.thickness) if a.thickness is not None else t0
    p["thickness"] = t
    p["base"] = [p["base"][0], p["base"][1], float(height) + t]
    if a.clear_grid:
        p.pop("grid", None)
    elif a.grid is not None or a.grid_origin is not None:
        size = a.grid or (p.get("grid") or {}).get("size")
        if size is None:
            raise DobimError("CONSTRAINT", "격자가 없는 천장이다: grid 를 주세요", path="/grid_origin")
        origin = a.grid_origin or (p["grid"]["origin"] if "grid" in p and a.grid is None else None)
        p["grid"] = _grid(size, origin, p["polygon"])
    ctx.put_el(a.id, dict(r, params=_checked(p)))
    return {"id": a.id, "height": float(height)}


# ================================================================ 경사로
class RampCreate(A):
    id: Id | None = None
    level: Id
    run: Annotated[list[Coord], Field(min_length=4, max_length=4)] = Field(description="경사 구간 걸음선 [x1,y1,x2,y2] (아래 -> 위, 평면 길이)")
    rise: Pos = Field(description="오름 높이 mm")
    width: Pos = Field(1200.0, description="폭 mm(걸음선 양쪽으로 반씩)")
    thickness: Pos = Field(150.0, description="판 두께 mm(걷는 면에서 수직 아래로)")
    base: Coord = Field(0.0, description="아래 끝 걷는 면 높이 mm(레벨 기준)")
    landing_bottom: NonNeg = Field(0.0, description="아래 참 길이 mm(걸음선 앞)")
    landing_top: NonNeg = Field(0.0, description="위 참 길이 mm(걸음선 뒤)")
    name: Name | None = None


def ramp_profile(length, rise, t, z0, lb, lt):
    """옆 윤곽 [[u, z], ...](반시계): u 는 판 기준선(아래 참 시작) 시작점에서. 걷는 면에서 수직 t 만큼 아래가 밑면이다."""
    u1, u2 = lb, lb + length
    u3 = u2 + lt
    bottom = [(0.0, z0 - t), (u1, z0 - t), (u2, z0 + rise - t), (u3, z0 + rise - t)]
    top = [(u3, z0 + rise), (u2, z0 + rise), (u1, z0), (0.0, z0)]
    out = []
    for q in bottom + top:
        if not out or (abs(q[0] - out[-1][0]) > 1e-9 or abs(q[1] - out[-1][1]) > 1e-9):
            out.append(q)
    if abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    return [[round(u, 6) + 0.0, round(z, 6) + 0.0] for u, z in out]


@command("ramp.create", kind="mutation", risk="write", group="model.solid",
         summary="경사로(IfcRamp): 걸음선·오름·폭·아래/위 참. 평면에 오름 화살표. 1/12 보다 급하면 경고",
         args=RampCreate, since="0.13",
         examples=[{"level": F.LV1, "run": [0, -3000, 6000, -3000], "rise": 450, "width": 1500, "landing_top": 1500}])
def ramp_create(ctx, a: RampCreate):
    C._fresh_id(ctx, a, "solid")
    ctx.authored(a.level, "level", what="레벨에 경사로를 추가하는 것")
    x1, y1, x2, y2 = (float(v) for v in a.run)
    ln = sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    if ln < 100.0:
        raise DobimError("CONSTRAINT", "경사 구간 걸음선은 100mm 이상이어야 한다", path="/run")
    slope = a.rise / ln
    if slope > RAMP_SLOPE_MAX:
        raise DobimError("CONSTRAINT", f"경사가 너무 급하다(1/{ln / a.rise:.1f}): 1/3 보다 완만해야 한다", path="/rise", hint="계단(stair.create)을 쓰세요")
    if slope > RAMP_SLOPE_WARN + 1e-12:
        ctx.warn(f"경사 1/{ln / a.rise:.1f} 이 1/12 보다 급하다(장애인 편의시설 기준)")
    ux, uy = (x2 - x1) / ln, (y2 - y1) / ln
    lb, lt = float(a.landing_bottom), float(a.landing_top)
    line = [x1 - lb * ux, y1 - lb * uy, x2 + lt * ux, y2 + lt * uy]
    p = {"name": a.name or f"RP-{a.id[-6:]}", "category": "ramp", "form": "panel", "line": [round(v, 6) + 0.0 for v in line],
         "thickness": float(a.width), "profile": ramp_profile(ln, float(a.rise), float(a.thickness), float(a.base), lb, lt)}
    ctx.put_el(a.id, C.row("solid", K.SO.SOLID_CATEGORIES["ramp"][0], "solid.generic", _checked(p)))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id, "slope_pct": round(slope * 100.0, 3), "length": round(ln, 3)}
