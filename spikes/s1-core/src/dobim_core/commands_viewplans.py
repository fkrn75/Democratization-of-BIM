# SPDX-License-Identifier: MPL-2.0
"""반사 천장 평면(VIEW-02)·면적 평면(VIEW-21)·범례 뷰(VIEW-09) 커맨드 (W1 1-C 3차).

view.rcp       : 반사 천장 평면(평면 뷰 + rcp). 절단면에서 위를 본다: 절단선은 평면과 같고, 투영은 절단면 위 보이는 깊이 안의 것만
                 (위층 슬래브 밑면·구멍, 매단 패밀리, 천장 높이가 있는 공간의 천장선과 'CH 높이' 문자). 문·창 기호는 없다.
view.area_plan : 면적 평면(평면 뷰 + area). gross = 그 층 벽 윤곽 합집합의 바깥선(외벽 바깥면) 면적, rooms = 공간별 면적과 합계.
view.legend    : 범례 뷰(뷰 종류 legend). symbols = 모델의 문(폭·높이별)·패밀리 유형 기호, materials = 벽·슬래브 재료 해치 견본.
view.areas     : 면적 평면의 면적 목록·합계.
설정은 뷰 정의의 키라 DoBIM_Defs 로 IFC 를 왕복하고 실행 취소·병합을 그대로 탄다. view.set(이름·축척·절단 높이·깊이)·view.delete·시트 배치는 기존 커맨드를 쓴다.
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core import queries as Q
from dobim_core import viewplans as VP
from dobim_core.commands import A, Id, Name, _fresh_id, command, row
from dobim_core.errors import DobimError


def _plan(ctx, a, extra: dict) -> dict:
    _fresh_id(ctx, a, "view")
    C._view_name_free(ctx, a.name)
    if not Q.plan_level_ok(ctx.st.el.get(a.level)):
        raise DobimError("NOT_FOUND", f"level 를 찾을 수 없다: {a.level}", path="/level")
    params = {"name": a.name, "type": "plan", "level": a.level, "cut_height": a.cut_height, "view_depth": a.view_depth, "scale": a.scale, **extra}
    ctx.put_el(a.id, row("view", "", "view.basic", params))
    return {"id": a.id}


class ViewRcp(A):
    id: Id | None = None
    name: Name
    level: Id = Field(description="레벨 id")
    cut_height: C.VCoord = Field(2300.0, description="레벨 바닥에서 절단면까지 mm")
    view_depth: C.VCoord = Field(2000.0, ge=0, description="절단면 위로 보이는 깊이 mm")
    scale: C.Scale = 100


@command("view.rcp", kind="mutation", risk="write", group="view", summary="반사 천장 평면(위를 보는 평면)을 만든다",
         args=ViewRcp, since="0.10", examples=[{"id": F.VR1, "name": "1F 반사 천장 평면", "level": F.LV1}])
def view_rcp(ctx, a: ViewRcp):
    """절단선은 평면과 같고, 투영은 절단면 위(위층 슬래브 밑면·매단 패밀리·공간 천장선). 계단·난간·승강기·경사 지붕·솔리드·외래 요소는 아직 그리지 않는다."""
    return _plan(ctx, a, {"rcp": True})


class ViewAreaPlan(A):
    id: Id | None = None
    name: Name
    level: Id = Field(description="레벨 id")
    scheme: Literal["gross", "rooms"] = Field(description="gross = 바닥면적(벽 윤곽 바깥선), rooms = 공간별 면적")
    cut_height: C.VCoord = 1200.0
    view_depth: C.VCoord = Field(300.0, ge=0)
    scale: C.Scale = 100


@command("view.area_plan", kind="mutation", risk="write", group="view", summary="면적 평면을 만든다",
         args=ViewAreaPlan, since="0.10", examples=[{"id": F.VA1, "name": "1F 바닥면적", "level": F.LV1, "scheme": "gross"}])
def view_area_plan(ctx, a: ViewAreaPlan):
    """평면 그림 위에 면적 경계·해치·면적 문자를 얹는다. 값은 view.areas 로 읽는다."""
    return _plan(ctx, a, {"area": a.scheme})


class ViewLegend(A):
    id: Id | None = None
    name: Name
    content: Literal["symbols", "materials"] = Field(description="symbols = 문·패밀리 유형 기호, materials = 재료 해치 견본")
    scale: C.Scale = 50


@command("view.legend", kind="mutation", risk="write", group="view", summary="범례 뷰를 만든다",
         args=ViewLegend, since="0.10", examples=[{"id": F.VL1, "name": "창호 범례", "content": "symbols"}])
def view_legend(ctx, a: ViewLegend):
    """모델에서 쓰는 종류를 행마다 기호 + 이름으로 늘어놓는 뷰. 모델이 바뀌면 다시 그릴 때 따라간다."""
    _fresh_id(ctx, a, "view")
    C._view_name_free(ctx, a.name)
    ctx.put_el(a.id, row("view", "", "view.basic", {"name": a.name, "type": "legend", "legend": a.content, "scale": a.scale}))
    return {"id": a.id}


class ViewAreas(A):
    id: Id = Field(description="면적 평면 id")


@command("view.areas", kind="query", risk="read", group="view", summary="면적 평면의 면적 목록·합계",
         args=ViewAreas, idempotent=True, examples=[{"id": F.VA1}])
def view_areas(ctx, a: ViewAreas):
    """면적은 ㎡(소수 둘째 자리)."""
    p = ctx.row(a.id, "view")["params"]
    if "area" not in p:
        raise DobimError("CONSTRAINT", "면적 평면이 아니다(view.area_plan 으로 만든다)", path="/id")
    items = ctx.g.q(VP.area_items, a.id)
    return {"id": a.id, "scheme": p["area"], "items": [{"name": n, "area_m2": round(ar / 1.0e6, 2)} for n, _e, ar, _at in items],
            "total_m2": round(sum(it[2] for it in items) / 1.0e6, 2)}
