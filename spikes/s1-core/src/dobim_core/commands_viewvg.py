# SPDX-License-Identifier: MPL-2.0
"""뷰 가시성·그래픽 재정의(VIEW-13), 뷰 필터(VIEW-14), 뷰 템플릿(VIEW-15) 커맨드 (W1 1-C).

view.vg.*       : 범주별 숨기기·색·절단/투영 선 굵기·하프톤(Revit 의 Visibility/Graphics). 주석 범주(dim·tag·draft)는 숨기기만.
view.filter.*   : 조건(요소 값 규칙) 기반 재정의. 목록의 위가 우선이다(Revit 의 뷰 필터와 같다). 필터 정의는 뷰·템플릿에 담긴다.
view.template.* : 뷰 설정 묶음(가시성·필터·축척). 뷰가 템플릿을 따르면 뷰의 가시성·필터 대신 템플릿 것을 쓰고, 뷰에서는 고칠 수 없다.
                  템플릿을 떼거나 지우면 그때의 설정을 뷰에 복사해 둔다(도면이 갑자기 바뀌지 않는다).
대상(id)은 뷰 또는 뷰 템플릿이다. 설정은 뷰·템플릿 정의(params)에 담겨 DoBIM_Defs 로 IFC 를 왕복하고 실행 취소·병합을 그대로 탄다.
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core.commands import A, Id, IdArg, Name, _fresh_id, command, row
from dobim_core.errors import DobimError

Cat = Literal["wall", "door", "finst", "slab", "stair", "railing", "elevator", "roof", "solid", "foreign", "space", "grid", "dim", "tag", "draft"]
FCat = Literal["wall", "door", "finst", "slab", "stair", "railing", "elevator", "roof", "solid", "foreign", "space"]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$", description='선·채움 색 "#rrggbb"')]
Weight = Annotated[float, Field(ge=K.VG_WEIGHT[0], le=K.VG_WEIGHT[1], allow_inf_nan=False, description="선 굵기(용지 mm)")]
OvrKey = Literal["hidden", "color", "cut_weight", "proj_weight", "halftone"]
assert set(Cat.__args__) == set(K.VG_CATS) and set(FCat.__args__) == set(K.VG_FILTER_CATS) and set(OvrKey.__args__) == set(K.VG_OVR_KEYS)
_OVR_DOC = {"hidden": "참이면 숨긴다", "color": '선(과 절단면 채움) 색 "#rrggbb"', "cut_weight": "절단선 굵기(용지 mm)",
            "proj_weight": "투영·기호·해치 선 굵기(용지 mm)", "halftone": "참이면 하프톤(색을 흰색 쪽으로 절반)"}


class Rule(A):
    field: str = Field(min_length=1, max_length=160, description="요소 값: name·level·material·ifc_class·keynote·category·fire_rating·thickness·width·height "
                                                                    '또는 "pset:속성셋.속성"(예: pset:Pset_WallCommon.IsExternal)')
    op: Literal["eq", "ne", "contains", "not_contains", "begins", "gt", "ge", "lt", "le", "has", "missing"] = Field(
        description="같음·다름·포함·불포함·시작·초과·이상·미만·이하·값 있음·값 없음(글 비교는 대소문자 무시, 둘 다 수면 수로 비교)")
    value: str = Field("", max_length=K.VG_VALUE_MAX, description="비교 값(has·missing 은 비운다, gt·ge·lt·le 는 수)")

    @model_validator(mode="after")
    def _ok(self):
        if not K.vg_rule_ok({"field": self.field, "op": self.op, "value": self.value}):
            raise ValueError("규칙이 올바르지 않다(필드 이름, has·missing 의 빈 값, 대소 비교의 수 값을 확인)")
        return self


# ---------------------------------------------------------------- 공용
def _target(ctx, tid, write=True):
    """뷰 또는 뷰 템플릿 행. 쓰기면 템플릿을 따르는 뷰는 거절한다(템플릿이 정한다)."""
    r = ctx.st.el.get(tid)
    if r is not None and r["kind"] == "vtemplate":
        return r
    r = ctx.row(tid, "view")
    _no_legend(r)
    if write and r["params"].get("template") is not None:
        raise DobimError("CONSTRAINT", "이 뷰는 뷰 템플릿을 따른다: 가시성·필터는 템플릿에서 고친다", path="/id",
                         hint="템플릿 id 로 같은 커맨드를 쓰거나 view.template.apply 에 template=null 을 주어 템플릿을 뗀다")
    return r


def _no_legend(r):
    if r["params"]["type"] == "legend":   # 범례 뷰(W1 1-C 3차)는 모델 범주를 그리지 않는다
        raise DobimError("CONSTRAINT", "범례 뷰에는 가시성·필터·템플릿을 쓰지 않는다", path="/id")
    if r["params"]["type"] == "drafting":   # 제도 뷰(W1 연장 VIEW-07)도 모델이 없다(2D 작도 층은 draft.layer.* 로 다룬다)
        raise DobimError("CONSTRAINT", "제도 뷰에는 가시성·필터·템플릿을 쓰지 않는다", path="/id", hint="2D 작도 층의 선 굵기·선 종류·색은 draft.layer.set 으로 바꾼다")


def _put(ctx, tid, r, p):
    for k in ("vg", "filters"):   # 빈 설정은 키를 지운다(설정이 없는 뷰의 저장 형식 그대로: 도면 바이트 불변)
        if k in p and not p[k]:
            del p[k]
    ctx.put_el(tid, dict(r, params=p))


def _ovr_of(a, keys) -> dict:
    o = {}
    for k in keys:
        v = getattr(a, k)
        if v is None or v is False:
            continue
        o[k] = v.lower() if k == "color" else (float(v) if k in ("cut_weight", "proj_weight") else True)
    return o


def _effective(st, vid):
    """뷰에 걸리는 (출처, 템플릿 id, vg, filters)."""
    p = st.el[vid]["params"]
    t = p.get("template")
    tr = st.el.get(t) if t is not None else None
    if tr is not None and tr["kind"] == "vtemplate":
        return "template", t, tr["params"].get("vg", {}), tr["params"].get("filters", [])
    return "view", None, p.get("vg", {}), p.get("filters", [])


# ================================================================ 가시성·그래픽 재정의(VIEW-13)
class VgSet(A):
    id: Id = Field(description="뷰 또는 뷰 템플릿 id")
    category: Cat = Field(description="범주(요소 종류). 주석 범주 dim·tag·draft 는 hidden 만")
    hidden: bool | None = Field(None, description=_OVR_DOC["hidden"] + ", 거짓이면 다시 보인다")
    color: Color | None = Field(None, description=_OVR_DOC["color"])
    cut_weight: Weight | None = Field(None, description=_OVR_DOC["cut_weight"])
    proj_weight: Weight | None = Field(None, description=_OVR_DOC["proj_weight"])
    halftone: bool | None = Field(None, description=_OVR_DOC["halftone"])
    unset: list[OvrKey] = Field(default_factory=list, max_length=5, description="지울 재정의 항목")
    clear: bool = Field(False, description="참이면 이 범주의 재정의를 모두 지운다(다른 필드와 함께 못 쓴다)")

    @model_validator(mode="after")
    def _any(self):
        given = {k for k in K.VG_OVR_KEYS if getattr(self, k) is not None}
        if self.clear and (given or self.unset):
            raise ValueError("clear 는 다른 재정의 필드와 함께 못 쓴다")
        if not (self.clear or given or self.unset):
            raise ValueError("바꿀 재정의가 하나도 없다")
        if given & set(self.unset):
            raise ValueError("같은 항목을 주면서 지울 수 없다")
        return self


@command("view.vg.set", kind="mutation", risk="write", group="view.vg",
         summary="뷰(또는 뷰 템플릿)의 범주별 가시성·그래픽 재정의(숨기기·색·선 굵기·하프톤)를 바꾼다",
         args=VgSet, since="0.10",
         examples=[{"id": F.V1, "category": "wall", "color": "#c00000", "cut_weight": 0.7}, {"id": F.V1, "category": "dim", "hidden": True}])
def view_vg_set(ctx, a: VgSet):
    r = _target(ctx, a.id)
    p = dict(r["params"])
    vg = {k: dict(v) for k, v in p.get("vg", {}).items()}
    if a.clear:
        vg.pop(a.category, None)
    else:
        if a.category in K.VG_ANNO_CATS and any(getattr(a, k) is not None for k in ("color", "cut_weight", "proj_weight", "halftone")):
            raise DobimError("CONSTRAINT", f"주석 범주({a.category})는 숨기기만 한다", path="/category")
        o = vg.get(a.category, {})
        for k in a.unset:
            o.pop(k, None)
        for k in K.VG_OVR_KEYS:
            v = getattr(a, k)
            if v is None:
                continue
            if v is False:   # hidden·halftone 거짓 = 끄기
                o.pop(k, None)
            else:
                o.update(_ovr_of(a, (k,)))
        if o:
            vg[a.category] = o
        else:
            vg.pop(a.category, None)
    p["vg"] = vg
    _put(ctx, a.id, r, p)
    return {"id": a.id, "category": a.category, "override": vg.get(a.category, {})}


@command("view.vg.get", kind="query", risk="read", group="view.vg",
         summary="뷰에 걸린 가시성·그래픽 재정의·필터(템플릿을 따르면 템플릿 것)와 숨김·재정의된 요소 수",
         args=IdArg, idempotent=True, examples=[{"id": F.V1}])
def view_vg_get(ctx, a: IdArg):
    r = ctx.st.el.get(a.id)
    if r is not None and r["kind"] == "vtemplate":
        p = r["params"]
        return {"id": a.id, "kind": "vtemplate", "vg": p.get("vg", {}), "filters": p.get("filters", []),
                "views": sorted(v for v in ctx.st.by_kind.get("view", ()) if ctx.st.el[v]["params"].get("template") == a.id)}
    ctx.row(a.id, "view")
    from dobim_core import viewvg as VG
    src, t, vg, fl = _effective(ctx.st, a.id)
    d = ctx.g.q(VG.vg_view, a.id, False)
    return {"id": a.id, "kind": "view", "source": src, "template": t, "vg": vg, "filters": fl,
            "elements": d[5] if d is not None else {"hidden": 0, "styled": 0}}


# ================================================================ 뷰 필터(VIEW-14)
class FilterSet(A):
    id: Id = Field(description="뷰 또는 뷰 템플릿 id")
    name: Name = Field(description="필터 이름(같은 이름이 있으면 바꾼다)")
    cats: list[FCat] = Field(min_length=1, max_length=len(K.VG_FILTER_CATS), description="필터가 거는 범주")
    rules: list[Rule] = Field(default_factory=list, max_length=K.VG_RULES_MAX, description="모두 맞아야 걸린다(AND). 비우면 범주 전체")
    hidden: bool | None = Field(None, description=_OVR_DOC["hidden"])
    color: Color | None = Field(None, description=_OVR_DOC["color"])
    cut_weight: Weight | None = Field(None, description=_OVR_DOC["cut_weight"])
    proj_weight: Weight | None = Field(None, description=_OVR_DOC["proj_weight"])
    halftone: bool | None = Field(None, description=_OVR_DOC["halftone"])
    index: int | None = Field(None, ge=0, le=K.VG_FILTERS_MAX - 1, description="우선순위 자리(0 = 맨 위). 생략하면 바꿀 때는 제자리, 새 필터는 맨 아래")

    @model_validator(mode="after")
    def _ok(self):
        if len(set(self.cats)) != len(self.cats):
            raise ValueError("같은 범주를 두 번 줄 수 없다")
        if not any(getattr(self, k) for k in K.VG_OVR_KEYS):
            raise ValueError("재정의가 하나도 없다(hidden·color·cut_weight·proj_weight·halftone 중 하나 이상)")
        return self


@command("view.filter.set", kind="mutation", risk="write", group="view.vg",
         summary="뷰(또는 뷰 템플릿)에 조건 기반 필터를 더하거나 바꾼다(규칙에 맞는 요소를 숨기거나 색·굵기·하프톤으로 재정의)",
         args=FilterSet, since="0.10",
         examples=[{"id": F.V1, "name": "두꺼운 벽", "cats": ["wall"], "rules": [{"field": "thickness", "op": "ge", "value": "200"}], "color": "#0050c0"}])
def view_filter_set(ctx, a: FilterSet):
    r = _target(ctx, a.id)
    p = dict(r["params"])
    fl = [dict(f) for f in p.get("filters", [])]
    f = {"name": a.name, "cats": list(a.cats), "rules": [x.model_dump() for x in a.rules], **_ovr_of(a, K.VG_OVR_KEYS)}
    at = next((i for i, x in enumerate(fl) if x["name"] == a.name), None)
    if at is not None:
        fl.pop(at)
    elif len(fl) >= K.VG_FILTERS_MAX:
        raise DobimError("CONSTRAINT", f"필터는 {K.VG_FILTERS_MAX}개까지다", path="/name")
    i = a.index if a.index is not None else (at if at is not None else len(fl))
    fl.insert(min(i, len(fl)), f)
    p["filters"] = fl
    _put(ctx, a.id, r, p)
    return {"id": a.id, "name": a.name, "index": fl.index(f), "count": len(fl)}


class FilterDelete(A):
    id: Id = Field(description="뷰 또는 뷰 템플릿 id")
    name: Name


@command("view.filter.delete", kind="mutation", risk="write", group="view.vg", summary="뷰(또는 뷰 템플릿)에서 필터를 뺀다",
         args=FilterDelete, since="0.10", examples=[{"id": F.V1, "name": "두꺼운 벽"}])
def view_filter_delete(ctx, a: FilterDelete):
    r = _target(ctx, a.id)
    p = dict(r["params"])
    fl = [f for f in p.get("filters", []) if f["name"] != a.name]
    if len(fl) == len(p.get("filters", [])):
        raise DobimError("NOT_FOUND", f"필터를 찾을 수 없다: {a.name}", path="/name")
    p["filters"] = fl
    _put(ctx, a.id, r, p)
    return {"id": a.id, "deleted": a.name, "count": len(fl)}


# ================================================================ 뷰 템플릿(VIEW-15)
Scale = Annotated[int, Field(ge=1, le=1000, description="축척 분모(템플릿을 따르는 뷰에 쓴다)")]


def _tname_free(ctx, name, me=None):
    for t in ctx.st.by_kind.get("vtemplate", ()):
        if t != me and ctx.st.el[t]["params"]["name"] == name:
            raise DobimError("CONSTRAINT", f"같은 이름의 뷰 템플릿이 있다: {name}", path="/name")


def _users(st, tid):
    return sorted(v for v in st.by_kind.get("view", ()) if st.el[v]["params"].get("template") == tid)


def _freeze(ctx, vid, tp):
    """뷰를 템플릿에서 떼고 그때의 가시성·필터를 뷰에 복사한다."""
    r = ctx.st.el[vid]
    p = {k: v for k, v in r["params"].items() if k not in ("template", "vg", "filters")}
    for k in ("vg", "filters"):
        if tp.get(k):
            p[k] = tp[k]
    ctx.put_el(vid, dict(r, params=p))


class TemplateCreate(A):
    id: Id | None = None
    name: Name
    from_view: Id | None = Field(None, description="이 뷰의 가시성·필터·축척을 복사해 만든다(뷰가 템플릿을 따르면 그 템플릿 것)")
    scale: Scale | None = None


@command("view.template.create", kind="mutation", risk="write", group="view.template",
         summary="뷰 템플릿(가시성·그래픽 재정의·필터·축척 묶음)을 만든다. 뷰에서 복사할 수 있다",
         args=TemplateCreate, since="0.10", examples=[{"id": F.VT1, "name": "평면 1:100 기본", "scale": 100}])
def view_template_create(ctx, a: TemplateCreate):
    if len(ctx.st.by_kind.get("vtemplate", ())) >= 500:
        raise DobimError("CONSTRAINT", "뷰 템플릿은 500개까지다", path="/")
    _tname_free(ctx, a.name)
    p = {"name": a.name}
    if a.from_view is not None:
        vr = ctx.row(a.from_view, "view")
        _s, _t, vg, fl = _effective(ctx.st, a.from_view)
        if vg:
            p["vg"] = vg
        if fl:
            p["filters"] = fl
        sc = vr["params"]["scale"]
        if type(sc) is int and 1 <= sc <= 1000:
            p["scale"] = sc
    if a.scale is not None:
        p["scale"] = a.scale
    _fresh_id(ctx, a, "vtemplate")
    ctx.put_el(a.id, row("vtemplate", "", "view.template", p))
    return {"id": a.id, "name": a.name}


class TemplateSet(A):
    id: Id
    name: Name | None = None
    scale: Scale | None = None
    clear_scale: bool = Field(False, description="참이면 템플릿이 축척을 정하지 않는다")

    @model_validator(mode="after")
    def _any(self):
        if self.name is None and self.scale is None and not self.clear_scale:
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.scale is not None and self.clear_scale:
            raise ValueError("scale 과 clear_scale 은 함께 못 쓴다")
        return self


@command("view.template.set", kind="mutation", risk="write", group="view.template",
         summary="뷰 템플릿의 이름·축척을 바꾼다(축척은 따르는 뷰에 함께 쓴다). 가시성·필터는 view.vg.set·view.filter.set 에 템플릿 id 를 준다",
         args=TemplateSet, since="0.10", examples=[{"id": F.VT1, "scale": 50}])
def view_template_set(ctx, a: TemplateSet):
    r = ctx.row(a.id, "vtemplate")
    p = dict(r["params"])
    if a.name is not None:
        _tname_free(ctx, a.name, a.id)
        p["name"] = a.name
    if a.clear_scale:
        p.pop("scale", None)
    elif a.scale is not None:
        p["scale"] = a.scale
    ctx.put_el(a.id, dict(r, params=p))
    users = _users(ctx.st, a.id)
    if a.scale is not None:
        for v in users:
            vr = ctx.st.el[v]
            if vr["params"]["scale"] != a.scale:
                ctx.put_el(v, dict(vr, params=dict(vr["params"], scale=a.scale)))
    return {"id": a.id, "views": users}


@command("view.template.delete", kind="mutation", risk="write", group="view.template",
         summary="뷰 템플릿을 지운다. 따르던 뷰는 템플릿에서 떼고 그때의 가시성·필터를 뷰에 복사한다",
         args=IdArg, since="0.10", examples=[{"id": F.VT1}])
def view_template_delete(ctx, a: IdArg):
    r = ctx.row(a.id, "vtemplate")
    users = _users(ctx.st, a.id)
    for v in users:
        _freeze(ctx, v, r["params"])
    ctx.put_el(a.id, None)
    return {"deleted": [a.id], "detached": users}


class TemplateApply(A):
    view: Id
    template: Id | None = Field(description="따를 뷰 템플릿 id. null 이면 템플릿을 떼고 그때의 설정을 뷰에 복사한다")


@command("view.template.apply", kind="mutation", risk="write", group="view.template",
         summary="뷰가 뷰 템플릿을 따르게 하거나(템플릿에 축척이 있으면 뷰 축척도 바꾼다) 템플릿을 뗀다",
         args=TemplateApply, since="0.10", examples=[{"view": F.V1, "template": F.VT1}])
def view_template_apply(ctx, a: TemplateApply):
    vr = ctx.row(a.view, "view")
    _no_legend(vr)
    vp = vr["params"]
    old = vp.get("template")
    if a.template is None:
        if old is None:
            raise DobimError("CONSTRAINT", "이 뷰는 뷰 템플릿을 따르지 않는다", path="/template")
        tr = ctx.st.el.get(old)
        _freeze(ctx, a.view, tr["params"] if tr is not None and tr["kind"] == "vtemplate" else {"vg": vp.get("vg"), "filters": vp.get("filters")})
        return {"view": a.view, "template": None}
    tp = ctx.row(a.template, "vtemplate")["params"]
    p = {k: v for k, v in vp.items() if k not in ("vg", "filters")}   # 뷰 자신의 설정은 템플릿이 덮는다(떼면 템플릿 것을 복사한다)
    p["template"] = a.template
    if "scale" in tp:
        p["scale"] = tp["scale"]
    ctx.put_el(a.view, dict(vr, params=p))
    return {"view": a.view, "template": a.template, "scale": p["scale"]}


class TemplateList(A):
    pass


@command("view.template.list", kind="query", risk="read", group="view.template", summary="뷰 템플릿 목록(설정 요약·따르는 뷰)",
         args=TemplateList, idempotent=True, examples=[{}])
def view_template_list(ctx, a: TemplateList):
    out = []
    for t in sorted(ctx.st.by_kind.get("vtemplate", ())):
        p = ctx.st.el[t]["params"]
        out.append({"id": t, "name": p["name"], "scale": p.get("scale"), "categories": sorted(p.get("vg", {})),
                    "filters": [f["name"] for f in p.get("filters", [])], "views": _users(ctx.st, t)})
    return {"items": out}
