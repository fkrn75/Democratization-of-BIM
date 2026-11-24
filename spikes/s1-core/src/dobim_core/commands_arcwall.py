# SPDX-License-Identifier: MPL-2.0
"""곡선(원호) 벽 커맨드(W2 ARC-04).

wall.arc      곡선 벽을 만든다: 세 점(시작·호 위의 점·끝) 또는 중심·반지름·시작각·끝각. 두께·높이·바닥 오프셋·상단 구속은 직선 벽(wall.create)과 같다.
wall.arc_set  곡선 벽의 축을 바꾼다(같은 두 입력 방식). 호스팅된 문·창이 새 호 길이 밖으로 나가면 거절한다.

저장: params.axis = [시작 x, y, 끝 x, y], params.arc = [호 가운데 점 x, y](소수 6자리). arc 키가 없으면 직선 벽이고 행은 이전과 같다.
지역 u(문·창 offset, 치수 trim.*)는 호를 따라 잰 거리다. 형상·도면·IFC 는 arcgeom 이 정한다.
W2 세 번째 PR 부터 곡선 벽도 받는 편집: 분할(wall.split, 호 위 거리)·동심 간격띄우기(wall.offset)·트림/연장(wall.extend_to·wall.corner, 호를 따라)·
T 조인 줄기(join.tee)·상단 붙이기(wall.attach_top, 조각 현마다 잰 윤곽)·사각 모양 개구부(wall.set openings shape rect)·독립 벽 개구(opening.wall).
아직 받지 않는 것(kinds.ARC_EXCLUSIVE): 잘라내기(cuts). 아치·원 모양 개구부도 직선 벽만 한다.
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from dobim_core import arcgeom as AG
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core.commands import A, Id, LocLine, Name, Num, Point2, Pos, _apply_top_level, _fresh_id, _wall_checked, command, row
from dobim_core.errors import DobimError

Angle = Annotated[float, Field(ge=-3600.0, le=3600.0, allow_inf_nan=False)]


class _ArcAxis(A):
    points: Annotated[list[Point2], Field(min_length=3, max_length=3)] | None = Field(
        None, description="세 점 [[시작 x,y], [호 위의 점 x,y], [끝 x,y]] mm. 가운데 점은 호 위 어디든 된다(저장은 호 가운데 점)")
    center: Point2 | None = Field(None, description="중심 [x,y] mm(radius·start_angle·end_angle 과 함께)")
    radius: Pos | None = Field(None, description="축 반지름 mm(벽 두께 중심)")
    start_angle: Angle | None = Field(None, description="시작각(도, +x 가 0, 반시계 +)")
    end_angle: Angle | None = Field(None, description="끝각(도). end_angle - start_angle 이 + 면 반시계, - 면 시계로 돈다(0 < |차| < 360)")

    @model_validator(mode="after")
    def _one(self):
        cen = [self.center, self.radius, self.start_angle, self.end_angle]
        if self.points is not None and any(v is not None for v in cen):
            raise ValueError("points 와 center·radius·start_angle·end_angle 은 함께 줄 수 없다")
        if self.points is None and any(v is None for v in cen):
            raise ValueError("points(세 점) 또는 center·radius·start_angle·end_angle 넷을 모두 준다")
        if self.points is None and not 0.0 < abs(self.end_angle - self.start_angle) < 360.0:
            raise ValueError("끝각과 시작각의 차는 0 보다 크고 360 보다 작아야 한다")
        return self


def _axis_of(a: _ArcAxis) -> tuple[list, list]:
    """입력 -> (axis, arc). 세 점이 한 직선 위면 CONSTRAINT."""
    if a.points is not None:
        p0, pm, p1 = a.points
        axis = [float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1])]
        if AG.circle3(p0, pm, p1) is None or (axis[2] - axis[0]) ** 2 + (axis[3] - axis[1]) ** 2 < 1.0:
            raise DobimError("CONSTRAINT", "세 점이 한 직선 위에 있거나 시작·끝이 겹친다", path="/points", hint="직선 벽은 wall.create 를 쓰세요")
        return axis, AG.canonical_mid(axis, pm)
    return AG.from_center(a.center[0], a.center[1], a.radius, a.start_angle, a.end_angle)


def _checked(p: dict, path: str) -> dict:
    why = AG.problem(p["axis"], p["arc"], p["thickness"])
    if why:
        raise DobimError("CONSTRAINT", f"곡선 벽이 올바르지 않다: {why}", path=path)
    return _wall_checked(p)


class WallArc(_ArcAxis):
    id: Id | None = None
    level: Id
    thickness: Pos = 200.0
    height: Pos = 3000.0
    name: Name | None = None
    base_offset: Num | None = Field(None, description="벽 바닥 오프셋(레벨 기준 mm). 생략·0 이면 레벨에서 시작")
    top_level: Id | None = Field(None, description="상단 레벨(상단 구속). 주면 height 를 계산한다(height 와 함께 줄 수 없다)")
    top_offset: Num | None = Field(None, description="상단 레벨 기준 상단 오프셋 mm(top_level 과 함께)")
    type: Id | None = Field(None, description="벽 유형(W2 일곱 번째 PR): 두께·층 구성·코어를 유형에서 받는다(thickness 를 함께 주지 않는다)")
    location_line: LocLine | None = Field(None, description="위치선(W2 일곱 번째 PR): 입력 호가 이 선이다(몸체는 동심 호로 놓인다). 생략하면 벽 중심선")

    @model_validator(mode="after")
    def _top(self):
        if self.top_level is not None and "height" in self.model_fields_set:
            raise ValueError("height 와 top_level 은 함께 줄 수 없다(top_level 이면 높이를 계산한다)")
        if self.top_offset and self.top_level is None:
            raise ValueError("top_offset 은 top_level 과 함께 준다")
        return self


@command("wall.arc", kind="mutation", risk="write", group="model.wall",
         summary="곡선(원호) 벽 생성: 세 점(시작·호 위의 점·끝) 또는 중심·반지름·시작각·끝각. 두께·높이·층 구성·문·창 호스팅은 직선 벽과 같다",
         args=WallArc, since="0.15",
         examples=[{"level": F.LV1, "points": [[0, 4000], [3000, 5500], [6000, 4000]], "thickness": 200},
                   {"level": F.LV1, "center": [3000, 0], "radius": 3000, "start_angle": 180, "end_angle": 360, "height": 3000}])
def wall_arc(ctx, a: WallArc):
    _fresh_id(ctx, a, "wall")
    ctx.authored(a.level, "level", what="레벨에 벽을 추가하는 것")
    axis, mid = _axis_of(a)
    a.name = a.name or f"W-{a.id[-6:]}"
    p = {"name": a.name, "axis": axis, "thickness": a.thickness, "height": a.height, "arc": mid}
    if a.base_offset:
        p["base_offset"] = float(a.base_offset)
    if a.top_level is not None:
        p["top_level"] = a.top_level
        if a.top_offset:
            p["top_offset"] = float(a.top_offset)
        _apply_top_level(ctx, p, a.level)
    tid = None
    if a.type is not None or a.location_line is not None:   # 벽 유형·위치선(W2 일곱 번째 PR): 없으면 행은 이전과 같다(응답의 축은 입력 호 그대로)
        from dobim_core import commands_walltype as CWT
        p, tid = CWT.creation_options(ctx, p, a.type, a.location_line, "height" in a.model_fields_set or a.top_level is not None,
                                      "thickness" in a.model_fields_set)
    _checked(p, "/points" if a.points is not None else "/radius")
    ctx.put_el(a.id, row("wall", "IfcWall", "wall.basic", p))
    ctx.rel("on_level", a.id, a.level, True)
    if tid is not None:
        ctx.rel("of_type", a.id, tid, True)
    return {"id": a.id, "axis": axis, "arc": mid, "radius": round(AG.info(axis, mid)[2], 3), "length": round(AG.length(AG.info(axis, mid)), 3)}


class WallArcSet(_ArcAxis):
    id: Id


@command("wall.arc_set", kind="mutation", risk="write", group="model.wall",
         summary="곡선 벽의 축(호)을 바꾼다: 세 점 또는 중심·반지름·시작각·끝각. 직선 벽에 주면 곡선 벽이 된다. 문·창은 호 위 거리(offset)를 지킨다",
         args=WallArcSet, since="0.15", examples=[{"id": F.W1, "points": [[0, 0], [3000, -1200], [6000, 0]]}])
def wall_arc_set(ctx, a: WallArcSet):
    r = ctx.authored(a.id, "wall", what="곡선 축 바꾸기")
    axis, mid = _axis_of(a)
    p = dict(r["params"], axis=axis, arc=mid)
    bad = [k for k in K.ARC_EXCLUSIVE if k in p]
    if bad:
        raise DobimError("CONSTRAINT", f"이 벽에는 곡선 벽이 아직 받지 않는 {bad[0]} 가 있다", path="/id",
                         hint=f"곡선 벽은 아직 {K.ARC_EXCLUSIVE_LABEL} 를 지원하지 않는다: 먼저 떼세요")
    _checked(p, "/points" if a.points is not None else "/radius")
    length = AG.length(AG.info(axis, mid))
    for d in sorted(ctx.rels_out("hosts", a.id)):
        dr = ctx.st.el[d]
        if dr["kind"] != "door":
            continue
        dp = dr["params"]
        if not K.door_span_ok(dp["offset"] - dp["width"] / 2, dp["offset"] + dp["width"] / 2, 0.0, length):   # 벽 끝 허용(W2 열세 번째 PR)
            raise DobimError("CONSTRAINT", f"호스팅된 문이 새 호 길이({length:.1f}mm) 밖으로 나간다: {d}", path="/id")
    from dobim_core.commands_family import check_hosted_fit
    check_hosted_fit(ctx, a.id, r["params"], p)
    if p == r["params"]:
        return {"id": a.id, "changed": False}
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "changed": True, "axis": axis, "arc": mid, "length": round(length, 3)}
