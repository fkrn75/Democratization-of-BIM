# SPDX-License-Identifier: MPL-2.0
"""콜아웃 뷰(VIEW-06)·뷰포트(DOC-02)·뷰 참조(VIEW-25) 커맨드 (W1 1-C 2차).

view.callout       : 부모 뷰(평면·단면·입면)의 한 부분을 큰 축척으로 보는 콜아웃 뷰를 만든다. 부모와 같은 종류의 뷰에 parent·crop 을 두고,
                     marks 를 켠 시트에서 부모 뷰 위에 1점쇄선 사각형과 기호 머리로 그린다. 범위는 view.set 의 crop 으로 바꾼다.
sheet.viewport.set : 저장된 시트에서 뷰 하나의 뷰포트(용지 위 위치 at·상세 번호 number·제목 title)를 정하거나 뺀다.
view.refs          : 뷰가 놓인 시트·상세 번호 참조·부모·콜아웃 뷰를 돌려준다.
뷰포트는 시트 정의(sheet)의, 콜아웃은 뷰 정의(view)의 선택 키라 DoBIM_Defs 로 IFC 를 왕복하고 실행 취소·병합을 그대로 탄다.
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core import viewref as VR
from dobim_core.commands import A, Id, Name, _fresh_id, command, row
from dobim_core.errors import DobimError

VC1 = F.VC1

_COPY = {"plan": ("level", "cut_height", "view_depth"), "section": ("p1", "p2", "depth", "flip", "with_site"), "elevation": ("dir", "finish")}


class ViewCallout(A):
    id: Id | None = None
    parent: Id = Field(description="부모 뷰(평면·단면·입면). 콜아웃 뷰는 부모와 같은 종류·설정(레벨·절단 높이·절단선·방위, 템플릿)으로 만든다")
    rect: Annotated[list[C.VCoord], Field(min_length=4, max_length=4)] = Field(description="콜아웃 범위 [x1, y1, x2, y2](부모 뷰 좌표 mm: 평면은 모델 x,y, 단면·입면은 가로 s·높이 z). "
                                                                                        "콜아웃 뷰의 잘라내기(crop)가 된다")
    name: Name = Field(description="콜아웃 뷰 이름(뷰끼리 겹치지 않는다). 첫 낱말이 부모 뷰의 기호 머리에 적힌다(상세 번호가 없을 때)")
    scale: C.Scale = 20

    @model_validator(mode="after")
    def _rect(self):
        if not (self.rect[0] < self.rect[2] and self.rect[1] < self.rect[3]):
            raise ValueError("rect 는 [x1, y1, x2, y2] 이고 x1<x2, y1<y2 여야 한다")
        return self


@command("view.callout", kind="mutation", risk="write", group="view", summary="콜아웃 뷰(부위 확대 뷰)를 만든다",
         args=ViewCallout, since="0.10", examples=[{"id": VC1, "parent": F.V1, "rect": [0, -1000, 3000, 1000], "name": "C1 벽 상세", "scale": 20}])
def view_callout(ctx, a: ViewCallout):
    """부모 뷰의 일부를 큰 축척으로 보는 뷰. 부모를 지우려면 콜아웃 뷰를 먼저 지운다."""
    pr = ctx.row(a.parent, "view")
    pp = pr["params"]
    if pp["type"] not in _COPY:
        raise DobimError("CONSTRAINT", "콜아웃은 평면·단면·입면 뷰에만 만든다(배치도 제외)", path="/parent")
    if "parent" in pp:
        raise DobimError("CONSTRAINT", "콜아웃 뷰 안의 콜아웃은 아직 없다: 부모 뷰에 직접 만드세요", path="/parent")
    _fresh_id(ctx, a, "view")
    C._view_name_free(ctx, a.name)
    params = {"name": a.name, "type": pp["type"], **{k: pp[k] for k in _COPY[pp["type"]] if k in pp}, "scale": a.scale, "crop": list(a.rect), "parent": a.parent}
    if pp.get("template") is not None:   # 부모가 따르는 뷰 템플릿을 그대로 따른다(가시성·필터가 같다). 템플릿 축척은 콜아웃 축척을 덮지 않는다
        params["template"] = pp["template"]
    ctx.put_el(a.id, row("view", "", "view.basic", params))
    return {"id": a.id, "parent": a.parent, "type": pp["type"]}


class ViewportSet(A):
    id: Id = Field(description="저장된 시트 정의 id")
    view: Id = Field(description="시트에 올린 뷰")
    at: list[C.PaperMm] | None = Field(None, min_length=2, max_length=2, description="뷰 틀의 왼쪽 위 [x, y](용지 mm). 생략하면 자동 배치")
    number: Name | None = Field(None, max_length=8, description="상세 번호(시트 안에서 겹치지 않고, 한 뷰에는 한 시트만 번호를 준다)")
    title: Name | None = Field(None, description="시트 위 제목. 생략하면 뷰 이름")
    clear: bool = Field(False, description="참이면 이 뷰의 뷰포트 설정을 뺀다(자동 배치·뷰 이름 제목으로 돌아간다). at·number·title 과 함께 못 쓴다")

    @model_validator(mode="after")
    def _one(self):
        if self.clear and (self.at is not None or self.number is not None or self.title is not None):
            raise ValueError("clear 는 at·number·title 과 함께 쓸 수 없다")
        return self


@command("sheet.viewport.set", kind="mutation", risk="write", group="view", summary="시트의 뷰포트(위치·상세 번호·제목)를 정한다",
         args=ViewportSet, since="0.10", examples=[{"id": F.SH1, "view": F.V1, "at": [40, 40], "number": "1", "title": "1층 평면도"}])
def sheet_viewport_set(ctx, a: ViewportSet):
    """뷰 하나의 뷰포트 설정을 통째로 바꾼다(준 값만 남는다). 시트 파일은 sheet.render 로 다시 낸다."""
    from dobim_core import commands_sheet as CS
    r = ctx.row(a.id, "sheet")
    p = dict(r["params"])
    if a.view not in p["views"]:
        raise DobimError("CONSTRAINT", "이 뷰는 시트에 없다(sheet.define 으로 먼저 올린다)", path="/view")
    vps = [dict(x) for x in p.get("viewports") or () if x["view"] != a.view]
    if not a.clear:
        new = C.SheetViewport(**{k: getattr(a, k) for k in ("view", "at", "number", "title") if getattr(a, k) is not None}).model_dump(mode="json", exclude_none=True)
        old = [x["view"] for x in p.get("viewports") or ()]
        vps.insert(old.index(a.view) if a.view in old else len(vps), new)   # 자리를 지킨다(저장 형식이 흔들리지 않게)
    if (err := VR.viewports_error(p["views"], vps)) is not None:
        raise DobimError("CONSTRAINT", err, path="/number")
    if vps:
        p["viewports"] = vps
    else:
        p.pop("viewports", None)
    VR.check_numbered_once(ctx.st, a.id, p)
    ctx.put_el(a.id, dict(r, params=CS.stored_params(p)))
    return {"id": a.id, "view": a.view, "viewports": len(vps)}


class ViewRefs(A):
    id: Id | None = Field(None, description="뷰 id. 생략하면 모든 뷰")


@command("view.refs", kind="query", risk="read", group="view", summary="뷰가 놓인 시트·상세 번호 참조·콜아웃",
         args=ViewRefs, idempotent=True, examples=[{}])
def view_refs(ctx, a: ViewRefs):
    """뷰마다 올린 시트(상세 번호·위치·제목), 기호 머리에 적힐 참조(번호 / 시트 번호), 부모 뷰·콜아웃 뷰."""
    if a.id is not None:
        ctx.row(a.id, "view")
    ids = [a.id] if a.id is not None else sorted(ctx.st.by_kind.get("view", ()))
    return {"items": [VR.view_refs(ctx.st, v) for v in ids]}
