# SPDX-License-Identifier: MPL-2.0
"""2D 작도 커맨드(27단계, 설계: docs/2d-editing.md). 뷰 위에 모델과 무관한 2D 요소(선·폴리선·호·원·문자·치수·채움 영역)를 그리고 고친다.

draft.create   요소 하나 생성(모양 + 점 + 모양별 값 + 스타일)
draft.set      값 바꾸기(점·모양별 값·스타일·층·이름. 모양과 뷰는 바꾸지 않는다)
draft.delete / draft.move / draft.copy / draft.rotate / draft.mirror   여러 요소(ids 또는 view+group)에 한 번에
draft.offset   선·폴리선·영역·호·원의 평행 복사본
draft.trim / draft.extend   선·열린 폴리선의 한쪽 끝을 경계 요소와 만나는 곳까지 자르거나 늘인다
draft.group    상세 그룹(이름) 묶기·풀기
draft.list     목록(뷰·층·그룹으로 거르기)
draft.layer.set / draft.layer.delete / draft.layer.list   층 스타일(선굵기·선종류·색)
모든 변경은 커맨드 로그에 남아 되돌리기·다시하기·병합·IFC 왕복(DoBIM_Defs)을 그대로 탄다. 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from math import atan2, degrees, hypot
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from dobim_core import drafting as DR
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core.commands import (A, Id, Name, _dec_cursor, _delete_one, _det_id, _enc_cursor, _fit, _fresh_id, _page, command, row)
from dobim_core.errors import DobimError

VCoord = Annotated[float, Field(allow_inf_nan=False, ge=-K.DRAFT_COORD_MAX, le=K.DRAFT_COORD_MAX)]
Pt = Annotated[list[VCoord], Field(min_length=2, max_length=2, description="[x,y] 뷰 좌표 mm(평면 x,y / 단면·입면 s,z)")]
Pts = Annotated[list[Pt], Field(min_length=1, max_length=K.DRAFT_PTS_MAX,
                                description="점 목록. line·dim 2개, polyline 2개 이상, region 3개 이상(경계), arc·circle 중심 1개, text 기준점 1개")]
Angle = Annotated[float, Field(allow_inf_nan=False, ge=-3600, le=3600)]


def _layer_ok(s: str) -> str:
    if not K.layer_name_ok(s):
        raise ValueError("층 이름은 1~64자, 앞뒤 공백·제어 문자·DXF 금지 문자(<>/\\\":;?*|=`) 없이")
    return s


LayerName = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_layer_ok)]
Color = Annotated[str, Field(pattern=r"^(#[0-9A-Fa-f]{6}|bylayer)$", description='"#rrggbb" 또는 "bylayer"(층 스타일을 따른다)')]
Weight = Literal["thin", "fine", "medium", "thick", "bylayer"]
Linetype = Literal["continuous", "dashed", "hidden", "center", "dashdot", "dotted", "bylayer"]
DText = Annotated[str, Field(max_length=K.DRAFT_TEXT_MAX)]
SEL_MAX = 500
DIM_OFF_DEFAULT = 600.0     # 치수선 간격 기본값(뷰 mm, 1:100 이면 용지 6mm)
_STYLE_DOC = {"weight": "선굵기 thin 0.09·fine 0.18·medium 0.25·thick 0.5mm(용지). 생략·bylayer = 층 스타일",
              "linetype": "선종류. 생략·bylayer = 층 스타일", "color": '선·글 색 "#rrggbb". 생략·bylayer = 층 스타일(영역 채움 색은 기본 회색)'}


def _bylayer(v):
    return None if v in (None, "bylayer") else (v.lower() if isinstance(v, str) and v.startswith("#") else v)


def _check(ctx, params, path="/pts"):
    err = K.row_params_error("draft", params)
    if err is not None:
        raise DobimError("CONSTRAINT", err, path=path)


def _draft(ctx, eid):
    r = ctx.row(eid)
    if r["kind"] != "draft":
        raise DobimError("CONSTRAINT", f"2D 작도 요소가 아니다: {r['kind']}", path="/id")
    return r


def _pts(v):
    return [[float(x), float(y)] for x, y in v]


OFF_SHAPES = ("dim", "dim_arc", "dim_chain", "dim_base")   # off 생략 시 DIM_OFF_DEFAULT 를 쓰는 모양
A0_SHAPES = ("text", "dim_radius", "dim_diameter", "dim_chain", "dim_base")   # a0 하나가 회전각·방향인 모양


def _view_rule(ctx, p):
    """뷰 종류에 따라 값을 잴 수 없는 표고·경사는 글(text)을 직접 받는다(평면·배치도는 높이를 모른다)."""
    vt = ctx.st.el[p["view"]]["params"]["type"]
    if p["text"]:
        return
    if p["shape"] == "spot_slope" and vt not in ("section", "elevation"):
        raise DobimError("CONSTRAINT", "평면·배치도의 경사 기호는 높이를 몰라 값을 잴 수 없다: text 로 경사를 준다", path="/text",
                         hint='예: "text": "i=1/50"')
    if p["shape"] == "spot_slope" and abs(p["pts"][1][0] - p["pts"][0][0]) < 1e-6:
        raise DobimError("CONSTRAINT", "경사 기호의 두 점이 수직선 위에 있어 기울기를 잴 수 없다: text 로 준다", path="/pts")
    if p["shape"] == "spot_elev" and vt == "site":
        raise DobimError("CONSTRAINT", "배치도의 표고는 높이를 몰라 값을 잴 수 없다: text 로 준다", path="/text")
    if p["shape"] in ("spot_elev", "spot_coord") and vt == "drafting":   # 제도 뷰(W1 연장 VIEW-07)의 좌표는 모델과 무관하다
        raise DobimError("CONSTRAINT", "제도 뷰는 모델이 없어 표고·좌표 값을 잴 수 없다: text 로 준다", path="/text",
                         hint='예: "text": "EL+3,300"')


# ================================================================ 생성·수정
class DraftCreate(A):
    id: Id | None = None
    view: Id = Field(description="요소를 둘 뷰(평면·단면·입면·배치도·제도 뷰)")
    shape: Literal["line", "polyline", "arc", "circle", "text", "region", "dim", "dim_angle", "dim_radius", "dim_diameter", "dim_arc",
                   "dim_chain", "dim_base", "spot_elev", "spot_coord", "spot_slope", "mask", "cloud"] = Field(
        description="line 선분, polyline 꺾은선(closed 로 닫기), arc 호(중심·r·a0->a1 반시계 도), circle 원, text 문자(a0 = 회전각), region 채움 영역(fill), "
                    "dim 두 점 사이 정렬 치수(off = 치수선 간격, 왼쪽 +), dim_angle 각도 치수(pts = 꼭짓점·첫 방향 점·둘째 방향 점, r = 치수 호 반지름, 반시계), "
                    "dim_radius 반지름·dim_diameter 지름 치수(pts = 중심, r, a0 = 치수선 방향), dim_arc 호 길이 치수(중심·r·a0->a1, off = 치수 호 간격, 바깥 +), "
                    "dim_chain 연속·dim_base 기준선 치수(점 2개 이상, a0 = 측정 방향, off = 첫 점에서 치수선까지 간격, 왼쪽 +), "
                    "spot_elev 표고·spot_coord 좌표(pts = 재는 점·글 위치), spot_slope 경사 화살표(pts = 높은 점·낮은 점), "
                    "mask 마스킹 영역(아래 모델 선을 가린다, closed 면 경계선을 그린다), cloud 리비전 구름(경계, r = 호 크기 뷰 mm·0 이면 용지 8mm, text = 리비전 번호)")
    pts: Pts
    closed: bool = Field(False, description="polyline 만: 닫힌 꺾은선")
    r: Annotated[float, Field(allow_inf_nan=False, ge=0, le=K.DRAFT_COORD_MAX)] = Field(0.0, description="arc·circle 반지름(뷰 mm)")
    a0: Angle = Field(0.0, description="arc 시작각, text 회전각(도, 반시계)")
    a1: Angle = Field(0.0, description="arc 끝각(도, 반시계)")
    text: DText = Field("", description="text 의 글, 치수·표고·좌표·경사는 값 대신 쓸 글(비우면 잰 값), cloud 는 리비전 번호")
    size: Annotated[float, Field(allow_inf_nan=False, ge=0.5, le=100)] = Field(2.5, description="text·dim 글 높이(용지 mm)")
    off: Annotated[float, Field(allow_inf_nan=False, ge=-1e5, le=1e5)] | None = Field(None, description=f"dim·dim_arc·dim_chain·dim_base 치수선 간격(뷰 mm, 생략하면 {DIM_OFF_DEFAULT:g})")
    fill: Literal["none", "solid", "hatch", "cross"] = Field("none", description="region 채움: solid 칠, hatch 45도 해치, cross 교차 해치")
    align: Literal["start", "middle", "end"] = Field("start", description="text 정렬")
    layer: LayerName = DR.DEFAULT_LAYER
    weight: Weight | None = Field(None, description=_STYLE_DOC["weight"])
    linetype: Linetype | None = Field(None, description=_STYLE_DOC["linetype"])
    color: Color | None = Field(None, description=_STYLE_DOC["color"])
    group: Name | None = Field(None, description="상세 그룹 이름(같은 뷰에서 같은 이름이면 한 묶음)")
    name: Name | None = None


@command("draft.create", kind="mutation", risk="write", group="draft",
         summary="뷰에 2D 작도 요소(선·폴리선·호·원·문자·치수·채움 영역)를 만든다",
         args=DraftCreate, since="0.9",
         examples=[{"id": F.DR1, "view": F.V1, "shape": "line", "pts": [[0, -1500], [6000, -1500]], "linetype": "dashed"},
                   {"view": F.V1, "shape": "text", "pts": [[3000, 2000]], "text": "상세 참조", "size": 3.0, "align": "middle"},
                   {"view": F.V1, "shape": "region", "pts": [[500, 500], [2000, 500], [2000, 1500], [500, 1500]], "fill": "hatch"}])
def draft_create(ctx, a: DraftCreate):
    _fresh_id(ctx, a, "draft")
    ctx.row(a.view, "view")
    params = {"name": a.name or f"DR-{a.id[-6:]}", "view": a.view, "shape": a.shape, "pts": _pts(a.pts), "closed": a.closed,
              "r": a.r, "a0": a.a0, "a1": a.a1, "text": a.text, "size": a.size,
              "off": (a.off if a.off is not None else DIM_OFF_DEFAULT) if a.shape in OFF_SHAPES else (a.off or 0.0),
              "fill": a.fill, "align": a.align, "layer": a.layer,
              "weight": _bylayer(a.weight), "linetype": _bylayer(a.linetype), "color": _bylayer(a.color), "group": a.group}
    if a.shape in DR.POLY_SHAPES and K.polygon_area2(params["pts"]) < 0:   # 경계는 반시계로 저장한다(슬래브와 같은 규칙)
        params["pts"] = params["pts"][::-1]
    _check(ctx, params)
    _view_rule(ctx, params)
    ctx.put_el(a.id, row("draft", "", "draft.basic", params))
    ctx.rel("annot_of", a.id, a.view, True)
    return {"id": a.id}


class DraftSet(A):
    forbid_null = True
    id: Id
    pts: Pts | None = None
    closed: bool | None = None
    r: Annotated[float, Field(allow_inf_nan=False, ge=0, le=K.DRAFT_COORD_MAX)] | None = None
    a0: Angle | None = None
    a1: Angle | None = None
    text: DText | None = None
    size: Annotated[float, Field(allow_inf_nan=False, ge=0.5, le=100)] | None = None
    off: Annotated[float, Field(allow_inf_nan=False, ge=-1e5, le=1e5)] | None = None
    fill: Literal["none", "solid", "hatch", "cross"] | None = None
    align: Literal["start", "middle", "end"] | None = None
    layer: LayerName | None = None
    weight: Weight | None = None
    linetype: Linetype | None = None
    color: Color | None = None
    name: Name | None = None

    @model_validator(mode="after")
    def _any(self):
        if len(self.model_fields_set - {"id"}) == 0:
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("draft.set", kind="mutation", risk="write", group="draft",
         summary="2D 작도 요소의 점·모양별 값·스타일(층·선굵기·선종류·색, bylayer 로 되돌림)·이름을 바꾼다",
         args=DraftSet, since="0.9", examples=[{"id": F.DR1, "weight": "thick", "color": "#c00000"}])
def draft_set(ctx, a: DraftSet):
    r = _draft(ctx, a.id)
    p = dict(r["params"])
    for k in a.model_fields_set - {"id"}:
        v = getattr(a, k)
        p[k] = _pts(v) if k == "pts" else (_bylayer(v) if k in ("weight", "linetype", "color") else v)
    if p["shape"] in DR.POLY_SHAPES and K.polygon_area2(p["pts"]) < 0:
        p["pts"] = p["pts"][::-1]
    _check(ctx, p, "/" + (sorted(a.model_fields_set - {"id"})[0]))
    _view_rule(ctx, p)
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


# ================================================================ 여러 요소 편집
class Sel(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=SEL_MAX)] | None = Field(None, description="대상 요소 id 목록")
    view: Id | None = Field(None, description="group 과 함께: 그 뷰의 상세 그룹 전체")
    group: Name | None = Field(None, description="view 와 함께: 상세 그룹 이름")

    @model_validator(mode="after")
    def _one(self):
        if (self.ids is None) == (self.group is None) or (self.group is None) != (self.view is None):
            raise ValueError("ids 또는 (view + group) 중 하나만 준다")
        return self


def _select(ctx, a: Sel) -> list:
    if a.ids is not None:
        if len(set(a.ids)) != len(a.ids):
            raise DobimError("CONSTRAINT", "같은 id 가 두 번 있다", path="/ids")
        for i in a.ids:
            _draft(ctx, i)
        return sorted(a.ids)
    ctx.row(a.view, "view")
    out = sorted(i for i in ctx.rels_in("annot_of", a.view) if ctx.st.el[i]["kind"] == "draft" and ctx.st.el[i]["params"]["group"] == a.group)
    if not out:
        raise DobimError("NOT_FOUND", f"그 뷰에 상세 그룹이 없다: {a.group}", path="/group")
    return out


def _map_params(p, fpt, ang_fn=None, mirror=False):
    """점 변환 fpt 를 요소 하나에 적용한 새 파라미터. ang_fn 은 각(호의 a0·a1, 문자 회전)을 바꾸는 함수."""
    q = dict(p, pts=[[round(c, 6) for c in fpt(x)] for x in p["pts"]])
    if ang_fn is not None and p["shape"] in ("arc", "dim_arc"):
        a0, a1 = ang_fn(p["a0"]), ang_fn(p["a1"])
        q["a0"], q["a1"] = (round(a1 % 360.0, 6), round(a0 % 360.0, 6)) if mirror else (round(a0 % 360.0, 6), round(a1 % 360.0, 6))
    elif ang_fn is not None and p["shape"] in A0_SHAPES:
        q["a0"] = round(ang_fn(p["a0"]) % 360.0, 6)
    if mirror and p["shape"] == "dim":   # 치수선이 대칭 쪽에 오도록 두 점 순서를 바꾼다(off 는 진행 방향 왼쪽)
        q["pts"] = q["pts"][::-1]
    if mirror and p["shape"] in ("dim_chain", "dim_base"):   # 측정 방향이 대칭되면 왼쪽이 오른쪽이 된다
        q["off"] = -p["off"]
    if mirror and p["shape"] == "dim_angle":   # 반시계로 재는 두 방향의 순서를 바꿔야 같은 각을 잰다
        q["pts"] = [q["pts"][0], q["pts"][2], q["pts"][1]]
    if p["shape"] in DR.POLY_SHAPES and K.polygon_area2(q["pts"]) < 0:
        q["pts"] = q["pts"][::-1]
    return q


def _apply(ctx, ids, fn, path):
    for i in ids:
        r = ctx.st.el[i]
        q = fn(r["params"])
        _check(ctx, q, path)
        ctx.put_el(i, dict(r, params=q))


class DraftMove(Sel):
    dx: Annotated[float, Field(allow_inf_nan=False, ge=-1e7, le=1e7)] = 0.0
    dy: Annotated[float, Field(allow_inf_nan=False, ge=-1e7, le=1e7)] = 0.0


@command("draft.move", kind="mutation", risk="write", group="draft", summary="2D 작도 요소들을 평행 이동한다(dx, dy 뷰 mm)",
         args=DraftMove, since="0.9", examples=[{"ids": [F.DR1], "dx": 500, "dy": -200}])
def draft_move(ctx, a: DraftMove):
    ids = _select(ctx, a)
    _apply(ctx, ids, lambda p: _map_params(p, lambda x: (x[0] + a.dx, x[1] + a.dy)), "/dx")
    return {"ids": ids}


class DraftCopy(DraftMove):
    count: int = Field(1, ge=1, le=100, description="복사 개수(k 번째 복사본은 k*(dx, dy) 만큼 옮긴다: 배열 복사)")
    to_view: Id | None = Field(None, description="다른 뷰로 복사(좌표는 그대로 쓴다)")
    as_group: Name | None = Field(None, description="복사본의 상세 그룹 이름(생략하면 그룹 없음)")


@command("draft.copy", kind="mutation", risk="write", group="draft", summary="2D 작도 요소들을 복사한다(배열 count, 다른 뷰 to_view)",
         args=DraftCopy, since="0.9", examples=[{"ids": [F.DR1], "dy": 1000, "count": 3}])
def draft_copy(ctx, a: DraftCopy):
    ids = _select(ctx, a)
    view = a.to_view
    if view is not None:
        ctx.row(view, "view")
    if len(ids) * a.count > SEL_MAX:
        raise DobimError("CONSTRAINT", f"복사본은 한 번에 {SEL_MAX}개까지다", path="/count")
    made = []
    for k in range(1, a.count + 1):
        for i in ids:
            p = ctx.st.el[i]["params"]
            q = _map_params(p, lambda x, k=k: (x[0] + a.dx * k, x[1] + a.dy * k))
            nid = ctx.new_id("draft")
            q.update(view=view or p["view"], group=a.as_group, name=f"DR-{nid[-6:]}")
            _check(ctx, q, "/dx")
            ctx.put_el(nid, row("draft", "", "draft.basic", q))
            ctx.rel("annot_of", nid, q["view"], True)
            made.append(nid)
    return {"ids": made}


class DraftRotate(Sel):
    center: Pt
    angle: Angle = Field(description="회전각(도, 반시계)")


@command("draft.rotate", kind="mutation", risk="write", group="draft", summary="2D 작도 요소들을 점 center 중심으로 회전한다",
         args=DraftRotate, since="0.9", examples=[{"ids": [F.DR1], "center": [3000, -1500], "angle": 90}])
def draft_rotate(ctx, a: DraftRotate):
    ids = _select(ctx, a)
    _apply(ctx, ids, lambda p: _map_params(p, lambda x: DR.rotate_pt(x, a.center, a.angle), lambda t: t + a.angle), "/angle")
    return {"ids": ids}


class DraftMirror(Sel):
    axis: Annotated[list[Pt], Field(min_length=2, max_length=2, description="대칭축 위의 두 점 [[x,y],[x,y]]")]
    keep: bool = Field(False, description="참이면 원본을 두고 대칭 복사본을 만든다")


@command("draft.mirror", kind="mutation", risk="write", group="draft", summary="2D 작도 요소들을 축에 대해 대칭한다(keep 이면 원본을 두고 복사본)",
         args=DraftMirror, since="0.9", examples=[{"ids": [F.DR1], "axis": [[0, 0], [1000, 0]], "keep": True}])
def draft_mirror(ctx, a: DraftMirror):
    ids = _select(ctx, a)
    (p0, p1) = a.axis
    if hypot(p1[0] - p0[0], p1[1] - p0[1]) < 1e-6:
        raise DobimError("CONSTRAINT", "대칭축의 두 점이 같다", path="/axis")
    th2 = 2 * degrees(atan2(p1[1] - p0[1], p1[0] - p0[0]))

    def fn(p):
        return _map_params(p, lambda x: DR.mirror_pt(x, p0, p1), lambda t: th2 - t, mirror=True)

    if not a.keep:
        _apply(ctx, ids, fn, "/axis")
        return {"ids": ids}
    made = []
    for i in ids:
        p = ctx.st.el[i]["params"]
        q = fn(p)
        nid = ctx.new_id("draft")
        q.update(group=None, name=f"DR-{nid[-6:]}")
        _check(ctx, q, "/axis")
        ctx.put_el(nid, row("draft", "", "draft.basic", q))
        ctx.rel("annot_of", nid, q["view"], True)
        made.append(nid)
    return {"ids": made}


class DraftDelete(Sel):
    pass


@command("draft.delete", kind="mutation", risk="write", group="draft", summary="2D 작도 요소들을 지운다(ids 또는 view+group)",
         args=DraftDelete, since="0.9", examples=[{"ids": [F.DR1]}])
def draft_delete(ctx, a: DraftDelete):
    ids = _select(ctx, a)
    for i in ids:
        _delete_one(ctx, i)
    return {"deleted": ids}


class DraftOffset(A):
    id: Id
    distance: Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)] = Field(
        description="간격(뷰 mm). 선·폴리선·영역은 점 순서 진행 방향 왼쪽이 +, 원·호는 바깥이 +")
    new_id: Id | None = None


@command("draft.offset", kind="mutation", risk="write", group="draft", summary="선·폴리선·영역·호·원의 평행 복사본(오프셋)을 만든다",
         args=DraftOffset, since="0.9", examples=[{"id": F.DR1, "distance": 300}])
def draft_offset(ctx, a: DraftOffset):
    r = _draft(ctx, a.id)
    p = r["params"]
    if a.distance == 0:
        raise DobimError("CONSTRAINT", "간격이 0 이다", path="/distance")
    s = p["shape"]
    q = dict(p, group=None)
    if s in ("line", "polyline", "region"):
        q["pts"] = DR.offset_polyline(p["pts"], a.distance, closed=s == "region" or p["closed"])
    elif s in ("circle", "arc"):
        q["r"] = round(p["r"] + a.distance, 6)
        if q["r"] <= 0:
            raise DobimError("CONSTRAINT", "안쪽 간격이 반지름보다 크다", path="/distance")
    else:
        raise DobimError("CONSTRAINT", f"{s} 는 오프셋할 수 없다(선·폴리선·영역·호·원만)", path="/id")
    nid = a.new_id or ctx.new_id("draft")
    if a.new_id is not None and nid in ctx.st.el:
        raise DobimError("CONSTRAINT", f"이미 있는 id 다: {nid}", path="/new_id")
    q["name"] = f"DR-{nid[-6:]}"
    _check(ctx, q, "/distance")
    ctx.put_el(nid, row("draft", "", "draft.basic", q))
    ctx.rel("annot_of", nid, q["view"], True)
    return {"id": nid}


class DraftTrim(A):
    id: Id = Field(description="자르거나 늘일 선·열린 폴리선")
    boundary: Id = Field(description="경계 요소(선·폴리선·영역·치수선·원·호, 같은 뷰)")
    end: Literal["start", "end"] = Field(description="자르거나 늘일 쪽 끝")


def _trim_extend(ctx, a: DraftTrim, extend: bool):
    r = _draft(ctx, a.id)
    p = r["params"]
    if p["shape"] not in ("line", "polyline") or p["closed"]:
        raise DobimError("CONSTRAINT", "자르기·연장은 선·열린 폴리선만", path="/id")
    if a.boundary == a.id:
        raise DobimError("CONSTRAINT", "자기 자신을 경계로 쓸 수 없다", path="/boundary")
    b = _draft(ctx, a.boundary)["params"]
    if b["view"] != p["view"]:
        raise DobimError("CONSTRAINT", "경계 요소가 다른 뷰에 있다", path="/boundary")
    if b["shape"] == "text":
        raise DobimError("CONSTRAINT", "문자는 경계가 될 수 없다", path="/boundary")
    pts = [list(x) for x in p["pts"]]
    ia, ib = (-2, -1) if a.end == "end" else (1, 0)   # 끝 선분: ia -> ib 방향(ib 가 움직일 끝)
    s0, s1 = pts[ia], pts[ib]
    ts = DR.boundary_hits(s0, s1, b)
    eps = 1e-6
    cand = [t for t in ts if t > 1 + eps] if extend else [t for t in ts if eps < t < 1 - eps]
    if not cand:
        raise DobimError("CONSTRAINT", "그 끝 쪽에서 경계와 만나는 곳이 없다" if extend else "그 끝 선분이 경계와 만나지 않는다", path="/boundary",
                         hint="늘이려면 draft.extend, 자르려면 draft.trim" )
    t = min(cand) if extend else max(cand)
    pts[ib] = [round(s0[0] + t * (s1[0] - s0[0]), 6), round(s0[1] + t * (s1[1] - s0[1]), 6)]
    q = dict(p, pts=pts)
    _check(ctx, q, "/boundary")
    ctx.put_el(a.id, dict(r, params=q))
    return {"id": a.id, "end_point": pts[ib]}


@command("draft.trim", kind="mutation", risk="write", group="draft", summary="선·열린 폴리선의 한쪽 끝을 경계와 만나는 곳까지 자른다",
         args=DraftTrim, since="0.9", examples=[{"id": F.DR1, "boundary": F.DR2, "end": "end"}])
def draft_trim(ctx, a: DraftTrim):
    return _trim_extend(ctx, a, False)


@command("draft.extend", kind="mutation", risk="write", group="draft", summary="선·열린 폴리선의 한쪽 끝을 경계와 만나는 곳까지 늘인다",
         args=DraftTrim, since="0.9", examples=[{"id": F.DR1, "boundary": F.DR3, "end": "end"}])
def draft_extend(ctx, a: DraftTrim):
    return _trim_extend(ctx, a, True)


class DraftGroup(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=SEL_MAX)]
    group: Name | None = Field(None, description="묶을 상세 그룹 이름(같은 뷰 안에서 쓰인다)")
    clear: bool = Field(False, description="참이면 그룹을 푼다(group 은 주지 않는다)")

    @model_validator(mode="after")
    def _one(self):
        if (self.group is None) != self.clear:
            raise ValueError("group 또는 clear=true 중 하나만 준다")
        return self


@command("draft.group", kind="mutation", risk="write", group="draft", summary="2D 작도 요소들을 상세 그룹으로 묶거나 푼다",
         args=DraftGroup, since="0.9", examples=[{"ids": [F.DR1], "group": "창호 상세 A"}])
def draft_group(ctx, a: DraftGroup):
    ids = _select(ctx, Sel(ids=a.ids))
    views = {ctx.st.el[i]["params"]["view"] for i in ids}
    if len(views) > 1:
        raise DobimError("CONSTRAINT", "상세 그룹은 한 뷰 안의 요소만 묶는다", path="/ids")
    _apply(ctx, ids, lambda p: dict(p, group=a.group), "/group")
    return {"ids": ids, "group": a.group}


class DraftList(A):
    view: Id | None = None
    layer: LayerName | None = None
    group: Name | None = None
    limit: int = Field(100, ge=1, le=1000, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


def _item(ctx, i):
    p = ctx.st.el[i]["params"]
    out = {"id": i, "name": p["name"], "view": p["view"], "shape": p["shape"], "layer": p["layer"],
           "bbox": [round(v, 3) for v in DR.bbox_of(p)]}
    for k in ("group", "weight", "linetype", "color"):
        if p[k] is not None:
            out[k] = p[k]
    if p["text"]:
        out["text"] = p["text"]
    vals = DR.dim_values(p)
    if p["shape"] in ("dim_chain", "dim_base"):
        out["values"] = [round(v, 3) for v in vals]
    elif vals:
        out["value"] = round(vals[0], 3)
    return out


@command("draft.list", kind="query", risk="read", group="draft", summary="2D 작도 요소 목록(뷰·층·그룹으로 거르기)",
         args=DraftList, idempotent=True, examples=[{}, {"view": F.V1, "limit": 5}])
def draft_list(ctx, a: DraftList):
    after = _dec_cursor(a.cursor, "id")
    ids = sorted(i for i in ctx.st.by_kind.get("draft", ())
                 if (a.view is None or ctx.st.el[i]["params"]["view"] == a.view) and (a.layer is None or ctx.st.el[i]["params"]["layer"] == a.layer)
                 and (a.group is None or ctx.st.el[i]["params"]["group"] == a.group))
    start = 0 if after is None else next((k for k, e in enumerate(ids) if e > after), len(ids))
    window = [_item(ctx, i) for i in ids[start:start + a.limit]]
    items, cut = _fit(window, cursor_of=lambda k: _enc_cursor(window[k]["id"]))
    nxt = _enc_cursor(items[-1]["id"]) if items and start + len(items) < len(ids) else None
    return {"items": items, "page": _page(nxt, len(ids), cut)}


# ================================================================ 층 스타일
def _layer_id(ctx, name):
    """이름의 층 스타일 요소 id(없으면 None). 가져온 프로젝트의 층은 결정적 id 가 아닐 수 있어 이름으로 찾는다."""
    return next((i for i in sorted(ctx.st.by_kind.get("dlayer", ())) if ctx.st.el[i]["params"]["name"] == name), None)


class LayerSet(A):
    name: LayerName
    weight: Literal["thin", "fine", "medium", "thick"] | None = Field(None, description="선굵기(새 층이면 생략 시 fine)")
    linetype: Literal["continuous", "dashed", "hidden", "center", "dashdot", "dotted"] | None = Field(None, description="선종류(새 층이면 생략 시 continuous)")
    color: Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")] | None = Field(None, description='색 "#rrggbb"(새 층이면 생략 시 #000000)')


@command("draft.layer.set", kind="mutation", risk="write", group="draft",
         summary="2D 작도 층 스타일(선굵기·선종류·색)을 만들거나 바꾼다. bylayer 요소가 따른다",
         args=LayerSet, since="0.9", examples=[{"name": "A-DRAFT-HIDDEN", "weight": "thin", "linetype": "hidden", "color": "#555555"}])
def draft_layer_set(ctx, a: LayerSet):
    lid = _layer_id(ctx, a.name)
    if lid is None:
        lid = _det_id("draft.layer", a.name)
        if lid in ctx.st.el:
            raise DobimError("CONSTRAINT", f"이미 있는 id 다: {lid}", path="/name")
        if ctx.external_def(lid):
            ctx.warn("가져온 파일에 이 id 의 층 스타일 정의가 되살려지지 않은 채 남아 있다: 새 정의가 그 속성을 덮어쓴다")
        dw, dl, dc = DR.DEFAULT_STYLE
        p = {"name": a.name, "weight": a.weight or dw, "linetype": a.linetype or dl, "color": (a.color or dc).lower()}
        ctx.put_el(lid, row("dlayer", "", "draft.layer", p))
        return {"id": lid, "created": True}
    r = ctx.row(lid)
    p = dict(r["params"])
    for k in ("weight", "linetype", "color"):
        v = getattr(a, k)
        if v is not None:
            p[k] = v.lower() if k == "color" else v
    ctx.put_el(lid, dict(r, params=p))
    return {"id": lid, "created": False}


class LayerDelete(A):
    name: LayerName


@command("draft.layer.delete", kind="mutation", risk="write", group="draft",
         summary="2D 작도 층 스타일을 지운다(그 층의 요소는 남고 기본 스타일로 그린다)",
         args=LayerDelete, since="0.9", examples=[{"name": "A-DRAFT"}])
def draft_layer_delete(ctx, a: LayerDelete):
    lid = _layer_id(ctx, a.name)
    if lid is None:
        raise DobimError("NOT_FOUND", f"층 스타일이 없다: {a.name}", path="/name")
    used = sum(1 for i in ctx.st.by_kind.get("draft", ()) if ctx.st.el[i]["params"]["layer"] == a.name)
    if used:
        ctx.warn(f"이 층의 요소 {used}개는 남고 기본 스타일(fine·continuous·#000000)로 그린다")
    ctx.put_el(lid, None)
    return {"deleted": [lid], "elements_on_layer": used}


class LayerList(A):
    pass


@command("draft.layer.list", kind="query", risk="read", group="draft", summary="2D 작도 층 스타일 목록과 층별 요소 수",
         args=LayerList, idempotent=True, examples=[{}])
def draft_layer_list(ctx, a: LayerList):
    counts: dict[str, int] = {}
    for i in ctx.st.by_kind.get("draft", ()):
        n = ctx.st.el[i]["params"]["layer"]
        counts[n] = counts.get(n, 0) + 1
    styled = {}
    for i in ctx.st.by_kind.get("dlayer", ()):
        p = ctx.st.el[i]["params"]
        styled[p["name"]] = {"id": i, "weight": p["weight"], "linetype": p["linetype"], "color": p["color"]}
    items = []
    for n in sorted(set(counts) | set(styled)):
        st = styled.get(n)
        items.append({"name": n, "elements": counts.get(n, 0), **(st if st else {"style": "default"})})
    return {"items": items[:200], "count": len(items), "default_style": dict(zip(("weight", "linetype", "color"), DR.DEFAULT_STYLE))}
