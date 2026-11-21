# SPDX-License-Identifier: MPL-2.0
"""반사 천장 평면(VIEW-02)·면적 평면(VIEW-21)·범례 뷰(VIEW-09) 커맨드 (W1 1-C 3차).

view.rcp       : 반사 천장 평면(평면 뷰 + rcp). 절단면에서 위를 본다: 절단선은 평면과 같고, 투영은 절단면 위 보이는 깊이 안의 것만
                 (위층 슬래브 밑면·구멍, 매단 패밀리, 천장 높이가 있는 공간의 천장선과 'CH 높이' 문자). 문·창 기호는 없다.
view.area_plan : 면적 평면(평면 뷰 + area). gross = 그 층 벽 윤곽 합집합의 바깥선(외벽 바깥면) 면적, rooms = 공간별 면적과 합계,
                 legal = 법정 바닥면적(벽 중심선 영역 + 그 층 법정 면적 객체 larea, 건축법 시행령 119조, W1 연장 ARC-36).
view.legend    : 범례 뷰(뷰 종류 legend). symbols = 모델의 문(폭·높이별)·패밀리 유형 기호, materials = 벽·슬래브 재료 해치 견본.
view.drafting  : 제도 뷰(뷰 종류 drafting, W1 연장 VIEW-07). 모델 없이 2D 작도(draft.*)만 담는다. 시트에 올리는 법은 다른 뷰와 같다.
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
    scheme: Literal["gross", "rooms", "legal"] = Field(description="gross = 바닥면적(벽 윤곽 바깥선), rooms = 공간별 면적, "
                                                       "legal = 법정 바닥면적(벽 중심선 영역 + 그 층 법정 면적 객체, area.define)")
    cut_height: C.VCoord = 1200.0
    view_depth: C.VCoord = Field(300.0, ge=0)
    scale: C.Scale = 100


@command("view.area_plan", kind="mutation", risk="write", group="view", summary="면적 평면을 만든다",
         args=ViewAreaPlan, since="0.10", examples=[{"id": F.VA1, "name": "1F 바닥면적", "level": F.LV1, "scheme": "gross"}])
def view_area_plan(ctx, a: ViewAreaPlan):
    """평면 그림 위에 면적 경계·해치·면적 문자를 얹는다. 값은 view.areas 로 읽는다.
    legal 은 건축법 시행령 119조 1항 3호 바닥면적(저작 벽 중심선으로 닫힌 영역 합집합, area.legal 층 표와 같은 값)과 그 층의 바닥면적 대상
    법정 면적 객체(이름·근거·산입 면적, 더함·노대는 45도, 뺌·용적률 제외는 135도 해치)를 그리고 아래에 층 바닥면적 합계를 쓴다.
    대지·건축면적 대상 객체는 레벨이 없어 그리지 않는다(area.legal·area.export 의 표에는 있다)."""
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


class ViewDrafting(A):
    id: Id | None = None
    name: Name
    scale: C.Scale = Field(20, description="축척 분모(1:N). 2D 작도의 문자 높이·파선 간격·치수 글자가 이 축척을 따른다")


@command("view.drafting", kind="mutation", risk="write", group="view", summary="제도 뷰(모델 없이 2D 작도만 담는 뷰)를 만든다",
         args=ViewDrafting, since="0.14", examples=[{"id": F.VD1, "name": "D-01 창호 상세", "scale": 10}])
def view_drafting(ctx, a: ViewDrafting):
    """Revit 의 제도 뷰(Drafting View). 모델을 자르거나 투영하지 않고 draft.* 로 그린 선·문자·채움·치수만 담는다.
    시트에 올리는 방법(sheet.define·sheet.viewport.set)·축척·이름 바꾸기(view.set)는 다른 뷰와 같다.
    다른 뷰의 2D 작도는 draft.copy 의 to_view 로 옮겨 온다. 모델 치수·태그·가시성·템플릿은 쓰지 않는다(모델이 없다)."""
    _fresh_id(ctx, a, "view")
    C._view_name_free(ctx, a.name)
    ctx.put_el(a.id, row("view", "", "view.basic", {"name": a.name, "type": "drafting", "scale": a.scale}))
    return {"id": a.id}


class ViewAreas(A):
    id: Id = Field(description="면적 평면 id")


@command("view.areas", kind="query", risk="read", group="view", summary="면적 평면의 면적 목록·합계",
         args=ViewAreas, idempotent=True, examples=[{"id": F.VA1}])
def view_areas(ctx, a: ViewAreas):
    """면적은 ㎡(소수 둘째 자리). legal 은 항목마다 근거·효과·면적·산입 면적(빼는 것은 음수)을 주고, total_m2 는 층 바닥면적
    (벽 중심선 + 용적률 제외가 아닌 객체의 산입 면적, 0 미만은 0), far_excluded_m2 는 용적률 제외 객체 합이다(지하층 판정은 area.legal)."""
    p = ctx.row(a.id, "view")["params"]
    if "area" not in p:
        raise DobimError("CONSTRAINT", "면적 평면이 아니다(view.area_plan 으로 만든다)", path="/id")
    if p["area"] == "legal":
        its = ctx.g.q(VP.legal_items, a.id)
        walls, adj, fl, far = ctx.g.q(VP.legal_totals, a.id)
        rows = [{"id": it[0], "name": it[1], "rule": it[2], "effect": it[3], "area_m2": round(it[5] / 1.0e6, 2), "counted_m2": round(it[6] / 1.0e6, 2)}
                for it in its]
        rows, cut = C._fit(rows)
        out = {"id": a.id, "scheme": "legal", "items": rows, "walls_m2": walls, "adjust_m2": adj, "total_m2": fl, "far_excluded_m2": far}
        if cut:
            out["page"] = C._page(None, len(its), cut)
        return out
    items = ctx.g.q(VP.area_items, a.id)
    return {"id": a.id, "scheme": p["area"], "items": [{"name": n, "area_m2": round(ar / 1.0e6, 2)} for n, _e, ar, _at in items],
            "total_m2": round(sum(it[2] for it in items) / 1.0e6, 2)}
