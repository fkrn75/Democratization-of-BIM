# SPDX-License-Identifier: MPL-2.0
"""지붕 유형·지붕 층 구성·처마 부속 커맨드(W2 여덟 번째 PR, ARC-19·ARC-20, 지붕 쪽 ARC-11).

rooftype.create / set / duplicate / delete / list : 이름 붙은 지붕 유형(두께 또는 층 구성(윗면부터) + 기능). 벽 유형(PR #82)과 같은 설계다:
                                                   지붕 행이 유형의 두께·층 구성을 복사해 갖고(params.rtype + 관계 of_type), 유형을 고치면 그 유형의
                                                   지붕 행만 다시 쓴다(증분 갱신: 바뀐 지붕만 도면·IFC 를 다시 계산한다)
roof.type.set   : 지붕에 유형을 입히거나 뗀다(뗀 지붕은 층 구성을 제 것으로 남긴다)
roof.layers     : 유형 없는 지붕의 층 구성(윗면부터, 합 = 두께). 단면에서 층 경계선, IFC 는 IfcMaterialLayerSetUsage
roof.eaves.set  : 처마 부속 fascia·gutter·soffit 를 지붕 변(외곽 변 번호)에 단다·바꾼다·뗀다. 형상은 지붕 형상(roof_geom)을 따라가므로
                  지붕을 고치면 함께 바뀐다(조각 계산: roofeaves). IFC 는 IfcRoof 에 집합한 부속(IfcCovering·IfcPipeSegment GUTTER)
roof.eaves      : 지붕 변마다 물매·내밀기·처마 길이와 단 처마 부속, 종류별 조각 수·길이
지붕 형상·도면·IFC 는 계속 지붕 행만 읽으므로 이 커맨드를 쓰지 않은 지붕은 바이트까지 그대로다. 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from math import sqrt
from typing import Annotated, Literal

from pydantic import Field, model_validator

from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import roofeaves as RE
from dobim_core.commands import A, Id, Name, Pos, command, row
from dobim_core.errors import DobimError

IDS_MAX = 500
Function = Literal["roof", "canopy", "eave", "glazing"]
assert tuple(Function.__args__) == K.ROOF_FUNCTIONS


# ---------------------------------------------------------------- 공통 도우미
def _types(ctx) -> list:
    return sorted(ctx.st.by_kind.get("rooftype", ()))


def type_row(ctx, tid: str, path: str = "/id") -> dict:
    r = ctx.st.el.get(tid)
    if r is None or r["kind"] != "rooftype":
        raise DobimError("NOT_FOUND", f"지붕 유형을 찾을 수 없다: {tid}", path=path, hint="rooftype.list 로 유형을 보세요")
    return r


def roofs_of(ctx, tid: str) -> list:
    return sorted(r for r in ctx.rels_in("of_type", tid) if ctx.st.el[r]["kind"] == "roof")


def _name_taken(ctx, name: str, skip: str | None = None) -> None:
    for t in _types(ctx):
        if t != skip and ctx.st.el[t]["params"]["name"] == name:
            raise DobimError("CONSTRAINT", f"같은 이름의 지붕 유형이 있다: {name}", path="/name")


def type_fields(tp: dict) -> dict:
    """유형 params 에서 지붕 행으로 복사하는 키(두께·층 구성)."""
    out = {"thickness": tp["thickness"]}
    if "layers" in tp:
        out["layers"] = [dict(x) for x in tp["layers"]]
    return out


def with_type(p: dict, tid: str, tp: dict) -> dict:
    """지붕 params p 에 유형 tp 를 입힌 새 params."""
    q = dict(p)
    q.pop("layers", None)
    q.update(type_fields(tp))
    q["rtype"] = tid
    return q


def _type_params(name, layers, thickness, function) -> dict:
    if layers is None and thickness is None:
        raise DobimError("CONSTRAINT", "layers(층 구성) 또는 thickness(두께)를 준다", path="/layers")
    p: dict = {"name": name}
    if layers is not None:
        ls = [{"material": x["material"], "thickness": float(x["thickness"])} for x in layers]
        p["thickness"] = float(sum(x["thickness"] for x in ls))
        if thickness is not None and abs(thickness - p["thickness"]) > K.LAYER_SUM_TOL:
            raise DobimError("CONSTRAINT", f"thickness({thickness:g})가 층 두께의 합({p['thickness']:g})과 다르다", path="/thickness")
        p["layers"] = ls
    else:
        p["thickness"] = float(thickness)
    if function is not None:
        p["function"] = function
    why = K.row_params_error("rooftype", p)
    if why:
        raise DobimError("CONSTRAINT", f"지붕 유형이 올바르지 않다: {why}", path="/layers")
    return p


def _write_roof(ctx, rid: str, q: dict, path: str) -> None:
    r = ctx.st.el[rid]
    if q == r["params"]:
        return
    why = K.row_params_error("roof", q)
    if why:
        raise DobimError("CONSTRAINT", f"지붕 {rid} 가 올바르지 않게 된다: {why}", path=path)
    ctx.put_el(rid, dict(r, params=q))


def retype(ctx, rid: str, tid: str, tp: dict, path: str) -> None:
    """지붕 rid 에 유형(tid, params tp)을 입힌다."""
    r = ctx.st.el[rid]
    p = r["params"]
    q = with_type(p, tid, tp)
    if "layers" in q and ctx.material(rid) is not None:
        ctx.set_material(rid, None)   # 층 구성이 재료를 대신한다(slab.layers 와 같다)
    _write_roof(ctx, rid, q, path)
    old = p.get("rtype")
    if old != tid:
        if old is not None:
            ctx.rel("of_type", rid, old, False)
        ctx.rel("of_type", rid, tid, True)


def detach(ctx, rid: str) -> None:
    """지붕에서 유형을 뗀다(두께·층 구성은 제 것으로 남는다)."""
    r = ctx.st.el[rid]
    old = r["params"].get("rtype")
    if old is None:
        return
    q = dict(r["params"])
    q.pop("rtype")
    ctx.put_el(rid, dict(r, params=q))
    ctx.rel("of_type", rid, old, False)


def _pub_type(ctx, tid: str) -> dict:
    p = ctx.st.el[tid]["params"]
    return {"id": tid, "name": p["name"], "thickness": p["thickness"], "layers": [dict(x) for x in p["layers"]] if "layers" in p else None,
            "function": p.get("function"), "roofs": len(roofs_of(ctx, tid))}


# ---------------------------------------------------------------- 지붕 유형
class _LayerIn(A):
    material: Name = Field(description="층 재료 이름")
    thickness: Pos = Field(description="층 두께 mm(수직)")


TypeLayers = Annotated[list[_LayerIn], Field(min_length=1, max_length=K.LAYERS_MAX, description="층 구성: 윗면(바깥) 층부터 아래로. 합이 유형 두께")]


class RoofTypeCreate(A):
    id: Id | None = None
    name: Name = Field(description="유형 이름(프로젝트 안에서 유일)")
    layers: TypeLayers | None = None
    thickness: Pos | None = Field(None, description="층 구성 없이 두께만 주는 유형 mm(layers 와 함께 주면 합과 같아야 한다)")
    function: Function | None = Field(None, description="기능: roof 일반 지붕·canopy 캐노피·eave 처마·glazing 유리 지붕")


@command("rooftype.create", kind="mutation", risk="write", group="model.roof",
         summary="지붕 유형(이름 붙은 두께 또는 층 구성(윗면부터)·기능)을 만든다. 지붕은 roof.type.set 이나 roof.create type 으로 입힌다",
         args=RoofTypeCreate, since="0.20",
         examples=[{"name": "처마 425 복합", "layers": [{"material": "우레탄 방수", "thickness": 5}, {"material": "무근 콘크리트", "thickness": 150},
                                                   {"material": "단열재", "thickness": 220}, {"material": "철근콘크리트", "thickness": 50}],
                    "function": "eave"},
                   {"name": "유리 지붕 28", "thickness": 28, "function": "glazing"}])
def rooftype_create(ctx, a: RoofTypeCreate):
    if len(_types(ctx)) >= K.ROOFTYPES_MAX:
        raise DobimError("CONSTRAINT", f"지붕 유형은 {K.ROOFTYPES_MAX}개까지다", path="/name")
    _name_taken(ctx, a.name)
    p = _type_params(a.name, [x.model_dump() for x in a.layers] if a.layers is not None else None, a.thickness, a.function)
    C._fresh_id(ctx, a, "rooftype")
    if ctx.external_def(a.id):
        ctx.warn("가져온 파일에 이 id 의 지붕 유형 정의가 되살려지지 않은 채 남아 있다: 새 정의가 그 속성을 덮어쓴다")
    ctx.put_el(a.id, row("rooftype", "", "roof.type", p))
    return {"id": a.id, "name": a.name, "thickness": p["thickness"]}


class RoofTypeSet(A):
    forbid_null = False
    id: Id
    name: Name | None = None
    layers: TypeLayers | None = Field(None, description="층 구성을 통째로 바꾼다")
    thickness: Pos | None = Field(None, description="층 구성 없는 유형의 두께를 바꾼다(층 구성이 있는 유형은 layers 로)")
    function: Function | None = None
    clear: Annotated[list[Literal["layers", "function"]], Field(max_length=2)] | None = Field(None, description="뗄 키(layers 를 떼면 두께만 남는다)")

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.layers, self.thickness, self.function, self.clear)):
            raise ValueError("바꿀 필드가 하나도 없다")
        for k in self.clear or ():
            if getattr(self, k) is not None:
                raise ValueError(f"{k} 를 주면서 clear 에 넣을 수 없다")
        return self


@command("rooftype.set", kind="mutation", risk="write", group="model.roof",
         summary="지붕 유형을 고친다. 그 유형의 모든 지붕이 따라간다(두께·층 구성)",
         args=RoofTypeSet, since="0.20", examples=[{"id": F.RT1, "layers": [{"material": "아스팔트 싱글", "thickness": 10}, {"material": "합판", "thickness": 12},
                                                                             {"material": "단열재", "thickness": 180}]}])
def rooftype_set(ctx, a: RoofTypeSet):
    r = type_row(ctx, a.id)
    old = r["params"]
    if a.name is not None:
        _name_taken(ctx, a.name, skip=a.id)
    clear = set(a.clear or ())
    layers = None if "layers" in clear else ([x.model_dump() for x in a.layers] if a.layers is not None else old.get("layers"))
    if a.thickness is not None and layers is not None and a.layers is None:
        raise DobimError("CONSTRAINT", "층 구성이 있는 유형의 두께는 layers 로 바꾼다", path="/thickness")
    thickness = a.thickness if a.thickness is not None else (old["thickness"] if layers is None else None)
    p = _type_params(a.name if a.name is not None else old["name"], layers, thickness,
                     None if "function" in clear else (a.function if a.function is not None else old.get("function")))
    if p == old:
        return {"id": a.id, "changed": False, "roofs": 0}
    ctx.put_el(a.id, dict(r, params=p))
    n = 0
    if type_fields(p) != type_fields(old):   # 형상 키(두께·층)가 바뀐 때만 지붕을 다시 쓴다(이름·기능은 지붕 행에 없다)
        for rid in roofs_of(ctx, a.id):
            before = ctx.st.el[rid]["params"]
            retype(ctx, rid, a.id, p, "/id")
            n += ctx.st.el[rid]["params"] != before
    return {"id": a.id, "changed": True, "roofs": n}


class RoofTypeDup(A):
    id: Id = Field(description="복제할 유형")
    name: Name = Field(description="새 유형 이름")
    new_id: Id | None = None


@command("rooftype.duplicate", kind="mutation", risk="write", group="model.roof", summary="지붕 유형을 새 이름으로 복제한다(지붕은 옮기지 않는다)",
         args=RoofTypeDup, since="0.20", examples=[{"id": F.RT1, "name": "처마 450 복합"}])
def rooftype_duplicate(ctx, a: RoofTypeDup):
    r = type_row(ctx, a.id)
    if len(_types(ctx)) >= K.ROOFTYPES_MAX:
        raise DobimError("CONSTRAINT", f"지붕 유형은 {K.ROOFTYPES_MAX}개까지다", path="/name")
    _name_taken(ctx, a.name)
    holder = RoofTypeCreate(name=a.name, id=a.new_id, thickness=1.0)
    C._fresh_id(ctx, holder, "rooftype")
    p = {**r["params"], "name": a.name}
    if "layers" in p:
        p["layers"] = [dict(x) for x in r["params"]["layers"]]
    ctx.put_el(holder.id, row("rooftype", "", "roof.type", p))
    return {"id": holder.id, "name": a.name}


class RoofTypeDelete(A):
    id: Id
    detach: bool = Field(False, description="true 면 이 유형의 지붕에서 유형을 떼고(층 구성은 지붕에 남는다) 지운다. false 면 쓰는 지붕이 있을 때 거절")


@command("rooftype.delete", kind="mutation", risk="write", group="model.roof", summary="지붕 유형을 지운다(쓰는 지붕이 있으면 detach 로 떼어 낸다)",
         args=RoofTypeDelete, since="0.20", examples=[{"id": F.RT1, "detach": True}])
def rooftype_delete(ctx, a: RoofTypeDelete):
    type_row(ctx, a.id)
    roofs = roofs_of(ctx, a.id)
    if roofs and not a.detach:
        raise DobimError("CONSTRAINT", f"지붕 {len(roofs)}개가 이 유형을 쓴다", path="/id", hint="detach: true 로 지붕에서 떼고 지우거나 roof.type.set 으로 바꾸세요",
                         details={"roofs": roofs[:20]})
    for rid in roofs:
        detach(ctx, rid)
    ctx.put_el(a.id, None)
    return {"deleted": [a.id], "detached": len(roofs)}


class RoofTypeList(A):
    limit: int = Field(100, ge=1, le=K.ROOFTYPES_MAX, description="최대 개수")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("rooftype.list", kind="query", risk="read", group="model.roof",
         summary="지붕 유형 일람표(이름·두께·층 구성·기능·쓰는 지붕 수)", args=RoofTypeList, idempotent=True, since="0.20",
         examples=[{}, {"limit": 5}])
def rooftype_list(ctx, a: RoofTypeList):
    ids = sorted(_types(ctx), key=lambda t: (ctx.st.el[t]["params"]["name"], t))
    after = C._dec_cursor(a.cursor, "pair")   # 키셋 커서: 마지막으로 읽은 (이름, id)
    keys = [[ctx.st.el[t]["params"]["name"], t] for t in ids]
    start = 0 if after is None else next((k for k, n in enumerate(keys) if n > after), len(keys))
    rows = [_pub_type(ctx, t) for t in ids[start:start + a.limit]]
    items, cut = C._fit(rows, cursor_of=lambda k: C._enc_cursor(keys[start + k]))
    more = start + len(items) < len(ids)
    untyped = sum(1 for rr in ctx.st.by_kind.get("roof", ()) if "rtype" not in ctx.st.el[rr]["params"])
    return {"items": items, "untyped_roofs": untyped, "page": C._page(C._enc_cursor(keys[start + len(items) - 1]) if more and items else None, len(ids), cut)}


# ---------------------------------------------------------------- 지붕에 유형·층 구성
RoofIds = Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX, description="지붕 id 들")]


class RoofTypeAssign(A):
    ids: RoofIds
    type: Id | None = Field(description="입힐 지붕 유형 id. null 이면 유형을 뗀다(층 구성은 지붕에 남는다)")


@command("roof.type.set", kind="mutation", risk="write", group="model.roof",
         summary="지붕에 지붕 유형을 입히거나(두께·층 구성을 유형 것으로) 뗀다",
         args=RoofTypeAssign, since="0.20", examples=[{"ids": [F.RF1], "type": F.RT1}])
def roof_type_set(ctx, a: RoofTypeAssign):
    if len(set(a.ids)) != len(a.ids):
        raise DobimError("CONSTRAINT", "ids 에 같은 id 가 두 번 있다", path="/ids")
    tp = type_row(ctx, a.type, "/type")["params"] if a.type is not None else None
    n = 0
    for i, rid in enumerate(a.ids):
        if ctx.st.el.get(rid, {}).get("kind") != "roof":
            raise DobimError("NOT_FOUND", f"지붕을 찾을 수 없다: {rid}", path=f"/ids/{i}")
        before = ctx.authored(rid, "roof", what="유형 지정")["params"]
        if tp is None:
            detach(ctx, rid)
        else:
            retype(ctx, rid, a.type, tp, f"/ids/{i}")
        n += ctx.st.el[rid]["params"] != before
    return {"count": len(a.ids), "changed": n}


class RoofLayers(A):
    id: Id
    layers: C.Layers | None = Field(description="지붕 층 구성: 윗면(바깥)에서 아래로 첫 층부터 준다(방수, 단열, 슬래브 ...). 층 두께(수직)의 합이 지붕 두께가 된다. null 이면 뗀다")


@command("roof.layers", kind="mutation", risk="write", group="model.roof",
         summary="지붕 두께 층 구성(재료·두께 목록, 윗면부터). 단면에서 층 경계선, IFC 는 IfcMaterialLayerSetUsage. 유형 있는 지붕은 rooftype.set 으로",
         args=RoofLayers, since="0.20",
         examples=[{"id": F.RF1, "layers": [{"material": "기와", "thickness": 30}, {"material": "합판", "thickness": 20}, {"material": "단열재", "thickness": 150}]}])
def roof_layers(ctx, a: RoofLayers):
    r = ctx.authored(a.id, "roof", what="층 구성 지정")
    p = dict(r["params"])
    if "rtype" in p:
        raise DobimError("CONSTRAINT", "유형 있는 지붕의 층 구성은 유형이 정한다", path="/layers",
                         hint="rooftype.set 으로 유형을 고치거나 roof.type.set 의 type null 로 유형을 뗀 뒤 지정하세요")
    if a.layers is None:
        p.pop("layers", None)
    else:
        ls = [{"material": x.material, "thickness": x.thickness} for x in a.layers]
        ctx.set_material(a.id, None)
        p["layers"] = ls
        p["thickness"] = float(sum(x["thickness"] for x in ls))
    if p == r["params"]:
        return {"id": a.id, "changed": False}
    _write_roof(ctx, a.id, p, "/layers")
    return {"id": a.id, "changed": True, "thickness": p["thickness"]}


# ---------------------------------------------------------------- 처마 부속
EAVE_MAT_DESC = ("이 종류 부속의 재료 이름(W2 아홉 번째 PR). 생략하면 지붕 재료를 따른다(단순 재료면 그 재료, 층 구성이면 그 층 집합). "
                 "IFC 는 종류마다 IfcRelAssociatesMaterial 하나")


class _Fascia(A):
    edges: list[Annotated[int, Field(strict=True, ge=0, lt=128)]] | Literal["all"] = Field(description="붙일 외곽 변 번호들(변 i = 꼭짓점 i→i+1) 또는 all")
    width: Pos = Field(K.EAVE_DEFAULT["fascia"]["width"], le=K.EAVE_DIM_MAX, description="널 두께(처마 선에서 바깥으로) mm")
    depth: Pos = Field(K.EAVE_DEFAULT["fascia"]["depth"], le=K.EAVE_DIM_MAX, description="지붕 윗면 모서리에서 아래로 mm")
    material: Name | None = Field(None, description=EAVE_MAT_DESC)


class _Gutter(A):
    edges: list[Annotated[int, Field(strict=True, ge=0, lt=128)]] | Literal["all"] = Field(description="붙일 외곽 변 번호들 또는 all(수평 처마 조각에만 단다)")
    width: Pos = Field(K.EAVE_DEFAULT["gutter"]["width"], le=K.EAVE_DIM_MAX, description="홈통 폭 mm(fascia 바깥으로)")
    depth: Pos = Field(K.EAVE_DEFAULT["gutter"]["depth"], le=K.EAVE_DIM_MAX, description="홈통 깊이 mm")
    material: Name | None = Field(None, description=EAVE_MAT_DESC)


class _Soffit(A):
    edges: list[Annotated[int, Field(strict=True, ge=0, lt=128)]] | Literal["all"] = Field(description="붙일 외곽 변 번호들 또는 all(수평 처마이고 내밀기가 있는 변만)")
    thickness: Pos = Field(K.EAVE_DEFAULT["soffit"]["thickness"], le=K.EAVE_DIM_MAX, description="처마 반자 판 두께 mm")
    material: Name | None = Field(None, description=EAVE_MAT_DESC)


class RoofEavesSet(A):
    forbid_null = True
    id: Id
    fascia: _Fascia | None = Field(None, description="처마 널(주면 이 값으로 바꾼다, 주지 않으면 그대로)")
    gutter: _Gutter | None = Field(None, description="홈통")
    soffit: _Soffit | None = Field(None, description="처마 반자")
    clear: Annotated[list[Literal["fascia", "gutter", "soffit"]], Field(max_length=3)] | None = Field(None, description="뗄 처마 부속 종류")
    numbering: Literal["stored", "input"] = Field("stored", description="edges 번호의 기준. stored(기본) = 저장된 외곽(반시계) 순서, input = roof.create·roof.set 에 준 외곽 순서. "
                                                  "둘은 외곽을 시계 방향으로 준 지붕에서만 다르다(반시계로 뒤집어 저장: 저장 변 j = 입력 변 n-2-j). roof.eaves 의 input_edge 로 대응을 본다")

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in ("fascia", "gutter", "soffit", "clear")):
            raise ValueError("바꿀 처마 부속이 하나도 없다(fascia·gutter·soffit 를 주거나 clear 로 뗀다)")
        for k in self.clear or ():
            if getattr(self, k) is not None:
                raise ValueError(f"{k} 를 주면서 clear 에 넣을 수 없다")
        return self


@command("roof.eaves.set", kind="mutation", risk="write", group="model.roof",
         summary="처마 부속(fascia 처마 널·gutter 홈통·soffit 처마 반자)을 지붕 변에 단다·바꾼다·뗀다(clear). 지붕 형상을 따라가며(모서리 연귀), IFC 는 IfcRoof 에 집합한 IfcCovering·IfcPipeSegment(GUTTER)",
         args=RoofEavesSet, since="0.20",
         examples=[{"id": F.RF1, "fascia": {"edges": "all", "width": 30, "depth": 250}, "gutter": {"edges": [0, 2]}, "soffit": {"edges": [0, 2], "thickness": 15}},
                   {"id": F.RF1, "clear": ["gutter"]}])
def roof_eaves_set(ctx, a: RoofEavesSet):
    r = ctx.authored(a.id, "roof", what="처마 부속 지정")
    p = dict(r["params"])
    n = len(p["boundary"])
    flip = bool(p.get("cw_input"))
    ev = {k: dict(v) for k, v in p.get("eaves", {}).items()}
    warned = False
    for kind in K.EAVE_KINDS:
        v = getattr(a, kind)
        if kind in (a.clear or ()):
            ev.pop(kind, None)
            continue
        if v is None:
            continue
        d = v.model_dump()
        if d.get("material") is None:
            d.pop("material", None)
        es = list(range(n)) if d["edges"] == "all" else d["edges"]
        if any(x >= n for x in es):
            raise DobimError("CONSTRAINT", f"{kind}.edges 의 변 번호는 0~{n - 1} 이어야 한다(외곽 꼭짓점 {n}개)", path=f"/{kind}/edges")
        if flip and d["edges"] != "all":
            if a.numbering == "input":   # 입력 외곽 번호 -> 저장 번호(W2 아홉 번째 PR, 실사용 결함 5)
                es = [stored_edge(x, n) for x in es]
            elif not warned:
                warned = True
                ctx.warn(f"이 지붕은 외곽을 시계 방향으로 받아 뒤집어 저장했다: edges 는 저장 순서 번호로 읽었다(입력 변 i = 저장 변 {n - 2}-i mod {n}). "
                         "입력 외곽 번호로 주려면 numbering=input")
        if len(set(es)) != len(es):
            raise DobimError("CONSTRAINT", f"{kind}.edges 에 같은 변이 두 번 있다", path=f"/{kind}/edges")
        d["edges"] = sorted(es)
        ev[kind] = {k: (float(x) if k not in ("edges", "material") else x) for k, x in d.items()}
    if ev:
        p["eaves"] = ev
    else:
        p.pop("eaves", None)
    if p == r["params"]:
        return {"id": a.id, "changed": False}
    try:
        pcs = K.roof_eave_pieces(p)
    except ValueError as ex:
        raise DobimError("CONSTRAINT", f"처마 부속을 만들 수 없다: {ex}", path="/id") from ex
    _write_roof(ctx, a.id, p, "/id")
    got = RE.summary(pcs)
    for kind in ev:   # 단 변이 있는데 조각이 하나도 없으면 알린다(예: 경사 처마에 홈통, 내밀기 없는 변에 반자)
        if kind not in got:
            ctx.warn(f"{kind}: 고른 변에 만들 수 있는 조각이 없다(홈통은 수평 처마, 반자는 수평 처마이고 내밀기가 있는 변에만)")
    return {"id": a.id, "changed": True, "eaves": got}


class RoofEaves(A):
    id: Id


def stored_edge(i: int, n: int) -> int:
    """시계 방향으로 받은 외곽의 입력 변 i 가 뒤집어 저장한 외곽에서 몇 번 변인가(자기 역함수다: 저장 변 j 의 입력 번호도 같은 식)."""
    return (n - 2 - i) % n


@command("roof.eaves", kind="query", risk="read", group="model.roof",
         summary="지붕 변마다 물매·처마 내밀기·처마 길이·처마 높이 구간과 단 처마 부속, 종류별 조각 수·길이", args=RoofEaves, idempotent=True, since="0.20",
         examples=[{"id": F.RF1}])
def roof_eaves(ctx, a: RoofEaves):
    r = ctx.row(a.id, "roof")
    p = r["params"]
    gm = K.roof_geom(p)
    n = len(p["boundary"])
    oh = K.roof_overhangs(p) if p["form"] == "footprint" else [float(p["overhang"])] * n
    runs = RE.eave_runs([tuple(q) for q in p["boundary"]], oh, [tuple(q) for q in gm["outline"]])
    m = len(gm["outline"])
    ev = p.get("eaves", {})
    edges = []
    for i in range(n):
        ln, zs = 0.0, []
        for k in runs.get(i, ()):
            q0, q1 = gm["outline"][k], gm["outline"][(k + 1) % m]
            ln += sqrt((q1[0] - q0[0]) ** 2 + (q1[1] - q0[1]) ** 2)
            zs += [z for _, (_x, _y, z) in RE._top_line(gm, q0, q1)]
        e = {"edge": i, "overhang": oh[i], "eave_length": round(ln, 3), "on": [k for k in K.EAVE_KINDS if i in ev.get(k, {}).get("edges", ())]}
        if p.get("cw_input"):   # 시계 방향으로 받은 외곽: 이 변의 입력 번호와 양 끝점(W2 아홉 번째 PR). 반시계 입력이면 응답은 그대로다
            e["input_edge"] = stored_edge(i, n)
            e["from"], e["to"] = list(p["boundary"][i]), list(p["boundary"][(i + 1) % n])
        if "slopes" in p:
            e["slope"] = p["slopes"][i]
        if zs:
            e["eave_z"] = [round(min(zs), 3), round(max(zs), 3)]
        edges.append(e)
    return {"id": a.id, "edges": edges, "eaves": RE.summary(K.roof_eave_pieces(p)), "settings": {k: {x: v for x, v in d.items() if x != "edges"} for k, d in ev.items()}}
