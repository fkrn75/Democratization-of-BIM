# SPDX-License-Identifier: MPL-2.0
"""벽 유형(ARC-11)·벽 위치선·스택 벽(ARC-09) 커맨드(W2 일곱 번째 PR).

walltype.create / set / duplicate / delete / list : 이름 붙은 벽 유형(두께 또는 층 구성 + 코어 범위·기능·기본 높이). 유형을 고치면 그 유형의
                                                   모든 벽 행을 다시 쓴다(증분 갱신: 바뀐 벽만 도면·IFC 를 다시 계산한다). 지울 때는 벽을 떼어 낼 수 있다
wall.type.set      : 벽에 유형을 입히거나(층 구성·두께·코어를 유형 것으로) 뗀다(지금 층 구성을 제 것으로 남긴다)
wall.location.set  : 벽 위치선(Revit 의 Location Line). 기본은 몸체를 두고 선만 바꾼다(Revit 과 같다). keep=line 이면 선을 두고 몸체를 옮긴다
wall.location      : 벽의 위치선과 그 축(곡선 벽은 호 세 점)
stackwall.create / set : 높이 구간마다 유형이 다른 부분 벽을 쌓은 스택 벽(구간마다 정렬선 기준 오프셋). 부분 벽은 보통 벽 행(params.stack)이라
                         평면은 절단 높이를 지나는 구간, 단면·입면은 구간마다 그대로 그린다. IFC 는 형상 없는 IfcWall 이 부분 IfcWall 들을 모은다

벽 행의 axis 는 계속 몸체 중심선이다(도면·IFC·조인이 그대로 읽는다). 유형·위치선을 쓰지 않은 벽은 바이트까지 그대로다.
유형을 바꾸거나 고쳐 두께가 바뀌면 위치선을 제자리에 두고 몸체를 옮긴다(Revit 과 같다). 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from dobim_core import arcgeom as AG
from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import walltop as WT
from dobim_core import walltypes as WTY
from dobim_core import xform as X
from dobim_core.commands import A, Id, Name, Num, Point2, Pos, command, row
from dobim_core.errors import DobimError

IDS_MAX = 500
LocLine = Literal["wall_center", "core_center", "finish_ext", "finish_int", "core_ext", "core_int"]
Function = Literal["exterior", "interior", "foundation", "retaining", "soffit", "core_shaft"]
Core = Annotated[list[Annotated[int, Field(strict=True, ge=0, le=8)]], Field(min_length=2, max_length=2,
                                                                                 description="코어 층 번호 범위 [a, b]: 층 a ~ b-1 이 코어(구조체). 생략하면 가장 두꺼운 층")]
assert tuple(LocLine.__args__) == WTY.LOC_LINES and tuple(Function.__args__) == WTY.FUNCTIONS


# ---------------------------------------------------------------- 공통 도우미
def _types(ctx) -> list:
    return sorted(ctx.st.by_kind.get("walltype", ()))


def _type_row(ctx, tid: str, path: str = "/id") -> dict:
    r = ctx.st.el.get(tid)
    if r is None or r["kind"] != "walltype":
        raise DobimError("NOT_FOUND", f"벽 유형을 찾을 수 없다: {tid}", path=path, hint="walltype.list 로 유형을 보세요")
    return r


def _name_taken(ctx, name: str, skip: str | None = None) -> None:
    for t in _types(ctx):
        if t != skip and ctx.st.el[t]["params"]["name"] == name:
            raise DobimError("CONSTRAINT", f"같은 이름의 벽 유형이 있다: {name}", path="/name")


def _walls_of(ctx, tid: str) -> list:
    return sorted(w for w in ctx.rels_in("of_type", tid) if ctx.st.el[w]["kind"] == "wall")


def _stacks_of(ctx, tid: str) -> list:
    return sorted(s for s in ctx.st.by_kind.get("stackwall", ()) if any(g["type"] == tid for g in ctx.st.el[s]["params"]["segments"]))


def _type_params(name, layers, thickness, core, function, height, path="/layers") -> dict:
    if layers is None and thickness is None:
        raise DobimError("CONSTRAINT", "layers(층 구성) 또는 thickness(두께)를 준다", path=path)
    p: dict = {"name": name}
    if layers is not None:
        ls = [{"material": x["material"], "thickness": float(x["thickness"])} for x in layers]
        p["thickness"] = float(sum(x["thickness"] for x in ls))
        if thickness is not None and abs(thickness - p["thickness"]) > K.LAYER_SUM_TOL:
            raise DobimError("CONSTRAINT", f"thickness({thickness:g})가 층 두께의 합({p['thickness']:g})과 다르다", path="/thickness")
        p["layers"] = ls
        if core is not None:
            p["core"] = list(core)
    else:
        if core is not None:
            raise DobimError("CONSTRAINT", "코어 범위(core)는 층 구성(layers)이 있을 때만 준다", path="/core")
        p["thickness"] = float(thickness)
    if function is not None:
        p["function"] = function
    if height is not None:
        p["height"] = float(height)
    why = K.row_params_error("walltype", p)
    if why:
        raise DobimError("CONSTRAINT", f"벽 유형이 올바르지 않다: {why}", path="/core" if "core" in why else path)
    return p


def _pub_type(ctx, tid: str) -> dict:
    p = ctx.st.el[tid]["params"]
    out = {"id": tid, "name": p["name"], "thickness": p["thickness"], "layers": [dict(x) for x in p["layers"]] if "layers" in p else None,
           "core": list(WTY.core_of(p)) if "layers" in p else None, "function": p.get("function"), "height": p.get("height"),
           "walls": len(_walls_of(ctx, tid))}
    st = _stacks_of(ctx, tid)
    if st:
        out["stacks"] = len(st)
    return out


def shift_params(p: dict, delta: float, path: str) -> tuple[dict, float]:
    """벽 params 의 몸체 중심선을 축 왼쪽(+v)으로 delta 옮긴 새 params 와 호 길이 비 k(곡선 벽 동심 호, 직선은 1)."""
    if abs(delta) < 1e-9:
        return dict(p), 1.0
    if "arc" in p:
        from dobim_core import commands_edit as CE
        return CE._arc_offset(p, delta, path)
    _, _, c, s, _ = C._frame_of(p["axis"])
    dx, dy = -s * delta, c * delta
    x1, y1, x2, y2 = p["axis"]
    return dict(p, axis=[X._r6(x1 + dx), X._r6(y1 + dy), X._r6(x2 + dx), X._r6(y2 + dy)]), 1.0


def _write_wall(ctx, w: str, q: dict, k: float, path: str) -> None:
    """벽 w 를 q 로 쓴다(검사 포함). 곡선 벽 몸체가 동심 호로 옮겨졌으면(k != 1) 호스팅 요소·독립 개구를 같은 각도로 옮긴다."""
    r = ctx.st.el[w]
    if q == r["params"]:
        return
    from dobim_core.commands_family import check_hosted_fit
    why = K.row_params_error("wall", q)
    if why:
        raise DobimError("CONSTRAINT", f"벽 {w} 가 올바르지 않게 된다: {why}", path=path)
    check_hosted_fit(ctx, w, r["params"], q)
    if "arc" in q and k != 1.0:
        from dobim_core import commands_edit as CE
        CE._arc_offset_apply(ctx, w, q, k, path)
    else:
        ctx.put_el(w, dict(r, params=q))


def retype(ctx, w: str, tid: str, tp: dict, path: str) -> None:
    """벽 w 에 유형(tid, params tp)을 입힌다. 위치선을 제자리에 두고 두께 차이만큼 몸체를 옮긴다."""
    r = ctx.st.el[w]
    p = r["params"]
    q = WTY.with_type(p, tid, tp)
    q, k = shift_params(q, WTY.loc_offset(p) - WTY.loc_offset(q), path)
    if "layers" in q and ctx.material(w) is not None:
        ctx.set_material(w, None)   # 층 구성이 재료를 대신한다(wall.layers 와 같다)
    _write_wall(ctx, w, q, k, path)
    old = p.get("wtype")
    if old != tid:
        if old is not None:
            ctx.rel("of_type", w, old, False)
        ctx.rel("of_type", w, tid, True)


def detach(ctx, w: str) -> None:
    """벽 w 에서 유형을 뗀다(층 구성·두께·코어 범위는 제 것으로 남는다)."""
    r = ctx.st.el[w]
    old = r["params"].get("wtype")
    if old is None:
        return
    q = dict(r["params"])
    q.pop("wtype")
    ctx.put_el(w, dict(r, params=q))
    ctx.rel("of_type", w, old, False)


def creation_options(ctx, p: dict, type_id: str | None, line: str | None, height_given: bool, thickness_given: bool) -> tuple[dict, str | None]:
    """wall.create·wall.arc 의 새 인자(type·location_line): 유형을 입히고, 입력 축을 위치선으로 보아 몸체 중심선으로 옮긴다.
    돌려주는 것: (새 params, 유형 id). 둘 다 없으면 p 를 그대로 돌려준다(행은 이전과 같다)."""
    if type_id is None and line in (None, "wall_center"):
        return p, None
    if type_id is not None:
        tp = _type_row(ctx, type_id, "/type")["params"]
        if thickness_given and abs(p["thickness"] - tp["thickness"]) > 1e-9:
            raise DobimError("CONSTRAINT", f"thickness 는 유형 두께({tp['thickness']:g})를 따른다: 함께 주지 않는다", path="/thickness")
        p = WTY.with_type(p, type_id, tp)
        if not height_given and "height" in tp and "top_level" not in p:
            p["height"] = tp["height"]
    if line not in (None, "wall_center"):
        p["loc"] = line
    p, _k = shift_params(p, -WTY.loc_offset(p), "/axis")
    return p, type_id


def location_axis(p: dict) -> dict:
    """벽 params -> 위치선 {"line", "offset", "axis"(+ 곡선 벽은 "arc": 호 가운데 점)}."""
    d = WTY.loc_offset(p)
    out = {"line": p.get("loc", "wall_center"), "offset": round(d, 6)}
    if "arc" in p:
        ai = AG.info(p["axis"], p["arc"])
        n = AG.length(ai)
        a0, a1, am = AG.point(ai, 0.0, d), AG.point(ai, n, d), AG.point(ai, n / 2.0, d)
        out["axis"] = [X._r6(a0[0]), X._r6(a0[1]), X._r6(a1[0]), X._r6(a1[1])]
        out["arc"] = [X._r6(am[0]), X._r6(am[1])]
    else:
        _, _, c, s, _ = C._frame_of(p["axis"])
        x1, y1, x2, y2 = p["axis"]
        out["axis"] = [X._r6(x1 - s * d), X._r6(y1 + c * d), X._r6(x2 - s * d), X._r6(y2 + c * d)]
    return out


# ---------------------------------------------------------------- 벽 유형 만들기·고치기·복제·지우기
class _LayerIn(A):
    material: Name = Field(description="층 재료 이름")
    thickness: Pos = Field(description="층 두께 mm")


TypeLayers = Annotated[list[_LayerIn], Field(min_length=1, max_length=K.LAYERS_MAX, description="층 구성: 바깥(지역 -v 면) 층부터. 합이 유형 두께")]


class WallTypeCreate(A):
    id: Id | None = None
    name: Name = Field(description="유형 이름(프로젝트 안에서 유일)")
    layers: TypeLayers | None = None
    thickness: Pos | None = Field(None, description="층 구성 없이 두께만 주는 유형 mm(layers 와 함께 주면 합과 같아야 한다)")
    core: Core | None = None
    function: Function | None = Field(None, description="기능(Revit Function): exterior 외벽·interior 내벽·foundation 기초벽·retaining 옹벽·soffit 소핏·core_shaft 코어·샤프트")
    height: Pos | None = Field(None, description="이 유형으로 벽을 만들 때 height 를 생략하면 쓰는 기본 높이 mm")


@command("walltype.create", kind="mutation", risk="write", group="model.wall",
         summary="벽 유형(이름 붙은 층 구성·두께·코어·기능·기본 높이)을 만든다. 벽은 wall.type.set 이나 wall.create type 으로 입힌다",
         args=WallTypeCreate, since="0.19",
         examples=[{"name": "외벽 RC200+단열", "layers": [{"material": "치장벽돌", "thickness": 90}, {"material": "단열재", "thickness": 50},
                                                         {"material": "철근콘크리트", "thickness": 200}, {"material": "석고보드", "thickness": 10}],
                    "core": [2, 3], "function": "exterior"},
                   {"name": "칸막이 100", "thickness": 100, "function": "interior", "height": 2700}])
def walltype_create(ctx, a: WallTypeCreate):
    if len(_types(ctx)) >= WTY.WALLTYPES_MAX:
        raise DobimError("CONSTRAINT", f"벽 유형은 {WTY.WALLTYPES_MAX}개까지다", path="/name")
    _name_taken(ctx, a.name)
    p = _type_params(a.name, [x.model_dump() for x in a.layers] if a.layers is not None else None, a.thickness, a.core, a.function, a.height)
    C._fresh_id(ctx, a, "walltype")
    if ctx.external_def(a.id):
        ctx.warn("가져온 파일에 이 id 의 벽 유형 정의가 되살려지지 않은 채 남아 있다: 새 정의가 그 속성을 덮어쓴다")
    ctx.put_el(a.id, row("walltype", "", "wall.type", p))
    return {"id": a.id, "name": a.name, "thickness": p["thickness"]}


_UNSET = object()


class WallTypeSet(A):
    forbid_null = False
    id: Id
    name: Name | None = None
    layers: TypeLayers | None = Field(None, description="층 구성을 통째로 바꾼다")
    thickness: Pos | None = Field(None, description="층 구성 없는 유형의 두께를 바꾼다(층 구성이 있는 유형은 layers 로)")
    core: Core | None = Field(None, description="코어 범위를 바꾼다. layers 를 바꾸면서 생략하면 기본값(가장 두꺼운 층)으로 돌아간다")
    function: Function | None = None
    height: Pos | None = None
    clear: Annotated[list[Literal["layers", "core", "function", "height"]], Field(max_length=4)] | None = Field(
        None, description="뗄 키(layers 를 떼면 두께만 남는다)")

    @model_validator(mode="after")
    def _any(self):
        vals = (self.name, self.layers, self.thickness, self.core, self.function, self.height, self.clear)
        if all(v is None for v in vals):
            raise ValueError("바꿀 필드가 하나도 없다")
        for k in self.clear or ():
            if getattr(self, k) is not None:
                raise ValueError(f"{k} 를 주면서 clear 에 넣을 수 없다")
        return self


@command("walltype.set", kind="mutation", risk="write", group="model.wall",
         summary="벽 유형을 고친다. 그 유형의 모든 벽(스택 벽 부분 포함)이 따라간다(위치선은 제자리, 두께 차이만큼 몸체가 옮겨진다)",
         args=WallTypeSet, since="0.19", examples=[{"id": F.WT1, "layers": [{"material": "철근콘크리트", "thickness": 250}, {"material": "석고보드", "thickness": 12}]}])
def walltype_set(ctx, a: WallTypeSet):
    r = _type_row(ctx, a.id)
    old = r["params"]
    if a.name is not None:
        _name_taken(ctx, a.name, skip=a.id)
    clear = set(a.clear or ())
    layers = None if "layers" in clear else ([x.model_dump() for x in a.layers] if a.layers is not None else old.get("layers"))
    if a.thickness is not None and layers is not None and a.layers is None:
        raise DobimError("CONSTRAINT", "층 구성이 있는 유형의 두께는 layers 로 바꾼다", path="/thickness")
    thickness = a.thickness if a.thickness is not None else (old["thickness"] if layers is None else None)
    core = None if "core" in clear or layers is None else (a.core if a.core is not None else (old.get("core") if a.layers is None else None))
    if a.core is not None and layers is None:
        raise DobimError("CONSTRAINT", "코어 범위(core)는 층 구성이 있는 유형만 갖는다", path="/core")
    if a.core is not None:
        core = a.core
    p = _type_params(a.name if a.name is not None else old["name"], layers, thickness, core,
                     None if "function" in clear else (a.function if a.function is not None else old.get("function")),
                     None if "height" in clear else (a.height if a.height is not None else old.get("height")))
    if p == old:
        return {"id": a.id, "changed": False, "walls": 0}
    ctx.put_el(a.id, dict(r, params=p))
    walls = _walls_of(ctx, a.id)
    n = 0
    if WTY.type_fields(p) != WTY.type_fields(old):   # 형상 키(두께·층·코어)가 바뀐 때만 벽을 다시 쓴다(이름·기능·기본 높이는 벽 행에 없다)
        for w in walls:
            before = ctx.st.el[w]["params"]
            retype(ctx, w, a.id, p, "/id")
            n += ctx.st.el[w]["params"] != before
    return {"id": a.id, "changed": True, "walls": n}


class WallTypeDup(A):
    id: Id = Field(description="복제할 유형")
    name: Name = Field(description="새 유형 이름")
    new_id: Id | None = None


@command("walltype.duplicate", kind="mutation", risk="write", group="model.wall", summary="벽 유형을 새 이름으로 복제한다(벽은 옮기지 않는다)",
         args=WallTypeDup, since="0.19", examples=[{"id": F.WT1, "name": "외벽 RC250"}])
def walltype_duplicate(ctx, a: WallTypeDup):
    r = _type_row(ctx, a.id)
    if len(_types(ctx)) >= WTY.WALLTYPES_MAX:
        raise DobimError("CONSTRAINT", f"벽 유형은 {WTY.WALLTYPES_MAX}개까지다", path="/name")
    _name_taken(ctx, a.name)
    holder = WallTypeCreate(name=a.name, id=a.new_id, thickness=1.0)
    C._fresh_id(ctx, holder, "walltype")
    p = {**{k: (list(v) if isinstance(v, list) else v) for k, v in r["params"].items()}, "name": a.name}
    if "layers" in p:
        p["layers"] = [dict(x) for x in r["params"]["layers"]]
    ctx.put_el(holder.id, row("walltype", "", "wall.type", p))
    return {"id": holder.id, "name": a.name}


class WallTypeDelete(A):
    id: Id
    detach: bool = Field(False, description="true 면 이 유형의 벽에서 유형을 떼고(층 구성은 벽에 남는다) 지운다. false 면 쓰는 벽이 있을 때 거절")


@command("walltype.delete", kind="mutation", risk="write", group="model.wall", summary="벽 유형을 지운다(쓰는 벽이 있으면 detach 로 떼어 낸다)",
         args=WallTypeDelete, since="0.19", examples=[{"id": F.WT1, "detach": True}])
def walltype_delete(ctx, a: WallTypeDelete):
    _type_row(ctx, a.id)
    st = _stacks_of(ctx, a.id)
    if st:
        raise DobimError("CONSTRAINT", f"스택 벽 {len(st)}개가 이 유형을 쓴다", path="/id", hint="stackwall.set 으로 구간 유형을 바꾸거나 스택 벽을 지우세요",
                         details={"stacks": st[:20]})
    walls = _walls_of(ctx, a.id)
    if walls and not a.detach:
        raise DobimError("CONSTRAINT", f"벽 {len(walls)}개가 이 유형을 쓴다", path="/id", hint="detach: true 로 벽에서 떼고 지우거나 wall.type.set 으로 바꾸세요",
                         details={"walls": walls[:20]})
    for w in walls:
        detach(ctx, w)
    ctx.put_el(a.id, None)
    return {"deleted": [a.id], "detached": len(walls)}


class WallTypeList(A):
    limit: int = Field(100, ge=1, le=WTY.WALLTYPES_MAX, description="최대 개수")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("walltype.list", kind="query", risk="read", group="model.wall",
         summary="벽 유형 일람표(이름·두께·층 구성·코어·기능·기본 높이·쓰는 벽 수)", args=WallTypeList, idempotent=True, since="0.19",
         examples=[{}, {"limit": 5}])
def walltype_list(ctx, a: WallTypeList):
    ids = sorted(_types(ctx), key=lambda t: (ctx.st.el[t]["params"]["name"], t))
    after = C._dec_cursor(a.cursor, "pair")   # 키셋 커서: 마지막으로 읽은 (이름, id)
    keys = [[ctx.st.el[t]["params"]["name"], t] for t in ids]
    start = 0 if after is None else next((k for k, n in enumerate(keys) if n > after), len(keys))
    rows = [_pub_type(ctx, t) for t in ids[start:start + a.limit]]
    items, cut = C._fit(rows, cursor_of=lambda k: C._enc_cursor(keys[start + k]))
    more = start + len(items) < len(ids)
    untyped = sum(1 for w in ctx.st.by_kind.get("wall", ()) if "wtype" not in ctx.st.el[w]["params"])
    return {"items": items, "untyped_walls": untyped, "page": C._page(C._enc_cursor(keys[start + len(items) - 1]) if more and items else None, len(ids), cut)}


# ---------------------------------------------------------------- 벽에 유형 입히기·위치선
WallIds = Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX, description="벽 id 들")]


def _wall(ctx, w: str, what: str, path: str) -> dict:
    r = ctx.st.el.get(w)
    if r is None or r["kind"] != "wall":
        raise DobimError("NOT_FOUND", f"벽을 찾을 수 없다: {w}", path=path)
    return ctx.authored(w, "wall", what=what)


class WallTypeAssign(A):
    ids: WallIds
    type: Id | None = Field(description="입힐 벽 유형 id. null 이면 유형을 뗀다(층 구성은 벽에 남는다)")


@command("wall.type.set", kind="mutation", risk="write", group="model.wall",
         summary="벽에 벽 유형을 입히거나(층 구성·두께·코어를 유형 것으로, 위치선은 제자리) 뗀다",
         args=WallTypeAssign, since="0.19", examples=[{"ids": [F.W1, F.W2], "type": F.WT1}])
def wall_type_set(ctx, a: WallTypeAssign):
    if len(set(a.ids)) != len(a.ids):
        raise DobimError("CONSTRAINT", "ids 에 같은 id 가 두 번 있다", path="/ids")
    tp = _type_row(ctx, a.type, "/type")["params"] if a.type is not None else None
    n = 0
    for i, w in enumerate(a.ids):
        before = _wall(ctx, w, "유형 지정", f"/ids/{i}")["params"]
        if tp is None:
            detach(ctx, w)
        else:
            retype(ctx, w, a.type, tp, f"/ids/{i}")
        n += ctx.st.el[w]["params"] != before
    return {"count": len(a.ids), "changed": n}


class WallLocationSet(A):
    ids: WallIds
    line: LocLine = Field(description="위치선: wall_center 벽 중심선 · core_center 코어 중심선 · finish_ext 바깥 마감면(첫 층 쪽, 축 오른쪽) · "
                                      "finish_int 안 마감면 · core_ext 코어 바깥 면 · core_int 코어 안 면")
    keep: Literal["body", "line"] = Field("body", description="body(기본) = 몸체를 두고 위치선만 바꾼다(Revit 과 같다). line = 지금 위치선을 두고 새 위치선이 그 자리에 오게 몸체를 옮긴다")


@command("wall.location.set", kind="mutation", risk="write", group="model.wall",
         summary="벽 위치선(중심선·코어 중심·마감면·코어 면)을 정한다. 유형을 바꾸거나 고칠 때 이 선이 제자리에 남는다",
         args=WallLocationSet, since="0.19", examples=[{"ids": [F.W1], "line": "finish_ext"}, {"ids": [F.W2], "line": "core_center", "keep": "line"}])
def wall_location_set(ctx, a: WallLocationSet):
    if len(set(a.ids)) != len(a.ids):
        raise DobimError("CONSTRAINT", "ids 에 같은 id 가 두 번 있다", path="/ids")
    n = 0
    for i, w in enumerate(a.ids):
        r = _wall(ctx, w, "위치선 지정", f"/ids/{i}")
        p = r["params"]
        q = dict(p)
        if a.line == "wall_center":
            q.pop("loc", None)
        else:
            q["loc"] = a.line
        k = 1.0
        if a.keep == "line":   # 지금 선(d_old)이 새 선(d_new) 자리: 몸체를 d_old - d_new 만큼 왼쪽으로
            q, k = shift_params(q, WTY.loc_offset(p) - WTY.loc_offset(q), f"/ids/{i}")
        _write_wall(ctx, w, q, k, f"/ids/{i}")
        n += ctx.st.el[w]["params"] != p
    return {"count": len(a.ids), "changed": n}


class WallLocation(A):
    ids: WallIds


@command("wall.location", kind="query", risk="read", group="model.wall",
         summary="벽의 위치선과 그 축(직선 [x1,y1,x2,y2], 곡선 벽은 호 가운데 점 arc 도), 몸체 중심선에서의 거리(왼쪽 +), 유형",
         args=WallLocation, idempotent=True, since="0.19", examples=[{"ids": [F.W1, F.W2]}])
def wall_location(ctx, a: WallLocation):
    out = []
    for i, w in enumerate(a.ids):
        r = ctx.st.el.get(w)
        if r is None or r["kind"] != "wall":
            raise DobimError("NOT_FOUND", f"벽을 찾을 수 없다: {w}", path=f"/ids/{i}")
        p = r["params"]
        if "axis" not in p:   # 외래 벽: 형상 의도가 없다
            out.append({"id": w, "line": None})
            continue
        it = {"id": w, **location_axis(p), "center_axis": list(p["axis"]), "type": p.get("wtype")}
        if "stack" in p:
            it["stack"] = p["stack"]
        out.append(it)
    return {"items": out}


# ---------------------------------------------------------------- 스택 벽
class StackSeg(A):
    type: Id = Field(description="이 구간의 벽 유형")
    height: Pos | None = Field(None, description="구간 높이 mm. 정확히 한 구간은 생략(가변: 전체 높이에서 나머지를 채운다)")
    offset: Num = Field(0.0, description="정렬선(line) 기준 이 구간 위치선의 거리 mm(축 왼쪽 +)")


StackSegs = Annotated[list[StackSeg], Field(min_length=2, max_length=WTY.STACK_SEGS_MAX, description="구간(아래부터)")]


def _seg_dicts(segs) -> list:
    return [{"type": s.type, "height": float(s.height) if s.height is not None else None, "offset": float(s.offset)} for s in segs]


def _stack_params(name, axis, arc, height, line, segments, parts, base_offset) -> dict:
    p = {"name": name, "axis": [float(x) for x in axis], "height": float(height), "line": line, "segments": segments, "parts": list(parts)}
    if arc is not None:
        p["arc"] = list(arc)
    if base_offset:
        p["base_offset"] = float(base_offset)
    why = K.row_params_error("stackwall", p)
    if why:
        raise DobimError("CONSTRAINT", f"스택 벽이 올바르지 않다: {why}", path="/segments" if "segments" in why else "/axis")
    return p


def _dummy(n: int) -> list:
    """params 검사용 자리 id(부분 벽 id 는 regenerate 가 정한다)."""
    return [f"{i:022d}" for i in range(n)]


def _arc_mid(axis, through, path: str):
    if through is None:
        return None
    if AG.circle3(axis[:2], through, axis[2:]) is None:
        raise DobimError("CONSTRAINT", "arc_through 가 축 두 끝과 한 직선 위에 있다", path=path)
    return AG.canonical_mid([float(x) for x in axis], through)


def _child_params(ctx, sid: str, sp: dict, i: int, heights: list) -> tuple[dict, str]:
    """스택 벽 sp 의 i 번째 부분 벽 params 와 유형 id."""
    seg = sp["segments"][i]
    tid = seg["type"]
    tp = _type_row(ctx, tid, f"/segments/{i}/type")["params"]
    z = sum(heights[:i]) + WT.base(sp)
    q = {"name": f"{sp['name']}-{i + 1}", "axis": list(sp["axis"]), "thickness": tp["thickness"], "height": heights[i]}
    if "arc" in sp:
        q["arc"] = list(sp["arc"])
    if z:
        q["base_offset"] = float(z)
    q = WTY.with_type(q, tid, tp)
    if sp["line"] != "wall_center":
        q["loc"] = sp["line"]
    q["stack"] = sid
    q, _k = shift_params(q, seg["offset"] - WTY.loc_offset(q), f"/segments/{i}/offset")
    return q, tid


def _hosted_fit_stack(ctx, w: str, q: dict, path: str) -> None:
    """부분 벽에 놓인 문이 새 구간(길이·바닥·높이) 안인가."""
    n = C.wall_len(q)
    b0, b1 = WT.base(q), WT.base(q) + q["height"]
    for d in sorted(ctx.rels_out("hosts", w)):
        dp = ctx.st.el[d]["params"]
        if ctx.st.el[d]["kind"] != "door":
            continue
        if dp["offset"] - dp["width"] / 2 < -1e-6 or dp["offset"] + dp["width"] / 2 > n + 1e-6 or dp["sill"] < b0 - 1e-9 or dp["sill"] + dp["height"] > b1 + 1e-9:
            raise DobimError("CONSTRAINT", f"부분 벽 {w} 에 놓인 문 {d} 가 새 구간 밖으로 나간다", path=path)


def regenerate(ctx, sid: str, sp: dict, level: str, new_ids: list | None = None) -> list:
    """스택 벽 sid 의 부분 벽을 sp 에 맞춰 만들거나 고치거나 지운다. 돌려주는 것: 부분 벽 id 목록(구간 순서)."""
    heights = WTY.stack_heights(sp["segments"], sp["height"])
    have = list(sp["parts"])
    want_n = len(sp["segments"])
    parts = have[:want_n]
    pool = list(new_ids or ())
    while len(parts) < want_n:
        nid = pool.pop(0) if pool else ctx.new_id("wall")
        if nid in ctx.st.el or C._in_split_history(ctx, nid):
            raise DobimError("CONSTRAINT", f"이미 있는 id 다: {nid}", path="/part_ids")
        parts.append(nid)
    sp = dict(sp, parts=parts)
    for w in have[want_n:]:   # 줄어든 구간의 부분 벽: 문·패밀리가 놓여 있으면 거절
        if ctx.rels_out("hosts", w):
            raise DobimError("CONSTRAINT", f"지울 부분 벽 {w} 에 놓인 문·창이 있다", path="/segments", hint="문·창을 먼저 지우거나 옮기세요")
        C._delete_one(ctx, w)   # 속성·재료·관계(조인·층·유형)를 함께 끊는다(element.delete 와 같은 길)
    for i, w in enumerate(parts):
        q, tid = _child_params(ctx, sid, sp, i, heights)
        r = ctx.st.el.get(w)
        if r is None:
            ctx.put_el(w, row("wall", "IfcWall", "wall.basic", q))
            ctx.rel("on_level", w, level, True)
            ctx.rel("of_type", w, tid, True)
            continue
        old = r["params"]
        if "arc" in q and "arc" in old and ctx.rels_out("hosts", w):   # 곡선 부분 벽: 호 길이가 바뀌면 문은 같은 각도에
            k = C.wall_len(q) / C.wall_len(old)
            if abs(k - 1.0) > 1e-12:
                for d in sorted(ctx.rels_out("hosts", w)):
                    dr = ctx.st.el[d]
                    if "offset" in dr["params"]:
                        ctx.put_el(d, dict(dr, params=dict(dr["params"], offset=X._r6(dr["params"]["offset"] * k))))
        for o in ("openings", "cuts", "tee", "top_profile"):   # 부분 벽이 따로 가진 형상 키(조인 표식 등)는 남긴다
            if o in old:
                q[o] = old[o]
        _hosted_fit_stack(ctx, w, q, "/segments")
        _write_wall(ctx, w, q, 1.0, "/segments")
        if old.get("wtype") != tid:
            if old.get("wtype"):
                ctx.rel("of_type", w, old["wtype"], False)
            ctx.rel("of_type", w, tid, True)
    return parts


class _StackGeom(A):
    axis: Annotated[list[Num], Field(min_length=4, max_length=4, description="정렬선(line)의 축 [x1,y1,x2,y2] mm")]
    arc_through: Point2 | None = Field(None, description="곡선 스택 벽: 호 위의 점 [x,y](생략하면 직선)")


class StackCreate(_StackGeom):
    id: Id | None = None
    level: Id
    name: Name | None = None
    line: LocLine = Field("wall_center", description="정렬선: 구간마다 이 위치선을 축(+ 구간 offset)에 맞춘다")
    segments: StackSegs
    height: Pos = Field(description="전체 높이 mm(가변 구간이 나머지를 채운다)")
    base_offset: Num | None = Field(None, description="스택 바닥 오프셋(레벨 기준 mm)")
    part_ids: Annotated[list[Id], Field(max_length=WTY.STACK_SEGS_MAX)] | None = Field(None, description="부분 벽 id(구간 순서). 비우면 자동")


@command("stackwall.create", kind="mutation", risk="write", group="model.wall",
         summary="스택 벽: 높이 구간마다 다른 벽 유형을 쌓는다(아래부터, 한 구간은 가변 높이). 구간은 정렬선 기준 offset 으로 맞춘다",
         args=StackCreate, since="0.19",
         examples=[{"id": F.SW1, "level": F.LV1, "axis": [0, -3000, 6000, -3000], "height": 3000, "line": "finish_ext",
                    "segments": [{"type": F.WT1, "height": 1000}, {"type": F.WT2}]}])
def stackwall_create(ctx, a: StackCreate):
    C._fresh_id(ctx, a, "stackwall")
    ctx.authored(a.level, "level", what="레벨에 벽을 추가하는 것")
    if (a.axis[2] - a.axis[0]) ** 2 + (a.axis[3] - a.axis[1]) ** 2 < 1.0:
        raise DobimError("CONSTRAINT", "벽 길이는 1mm 이상이어야 한다", path="/axis")
    if a.part_ids is not None and (len(a.part_ids) != len(a.segments) or len(set(a.part_ids)) != len(a.part_ids) or a.id in a.part_ids):
        raise DobimError("CONSTRAINT", "part_ids 는 구간 수만큼 서로 다른 id 로 준다", path="/part_ids")
    for i, s in enumerate(a.segments):
        _type_row(ctx, s.type, f"/segments/{i}/type")
    name = a.name or f"SW-{a.id[-6:]}"
    segs = _seg_dicts(a.segments)
    sp = _stack_params(name, a.axis, _arc_mid(a.axis, a.arc_through, "/arc_through"), a.height, a.line, segs, _dummy(len(segs)), a.base_offset)
    sp["parts"] = []
    parts = regenerate(ctx, a.id, sp, a.level, a.part_ids)
    sp["parts"] = parts
    ctx.put_el(a.id, row("stackwall", "IfcWall", "wall.stacked", _stack_params(name, a.axis, sp.get("arc"), a.height, a.line, segs, parts, a.base_offset)))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id, "parts": parts}


class StackSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    axis: Annotated[list[Num], Field(min_length=4, max_length=4)] | None = Field(None, description="정렬선 축을 바꾼다(곡선이면 arc_through 와 함께)")
    arc_through: Point2 | None = None
    line: LocLine | None = None
    segments: StackSegs | None = None
    height: Pos | None = None
    base_offset: Num | None = Field(None, description="0 이면 뗀다")
    part_ids: Annotated[list[Id], Field(max_length=WTY.STACK_SEGS_MAX)] | None = Field(None, description="구간이 늘 때 새 부분 벽 id")

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.axis, self.arc_through, self.line, self.segments, self.height, self.base_offset)):
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.arc_through is not None and self.axis is None:
            raise ValueError("arc_through 는 axis 와 함께 준다")
        return self


@command("stackwall.set", kind="mutation", risk="write", group="model.wall",
         summary="스택 벽의 구간(유형·높이·offset)·전체 높이·정렬선·축·이름을 고친다(부분 벽을 다시 맞춘다)",
         args=StackSet, since="0.19", examples=[{"id": F.SW1, "segments": [{"type": F.WT1, "height": 1200, "offset": 0}, {"type": F.WT2}]}])
def stackwall_set(ctx, a: StackSet):
    r = ctx.authored(a.id, "stackwall")
    old = r["params"]
    lv = ctx.rels_out("on_level", a.id)[0]
    axis = list(a.axis) if a.axis is not None else old["axis"]
    arc = _arc_mid(axis, a.arc_through, "/arc_through") if a.axis is not None else old.get("arc")
    if a.axis is not None and (axis[2] - axis[0]) ** 2 + (axis[3] - axis[1]) ** 2 < 1.0:
        raise DobimError("CONSTRAINT", "벽 길이는 1mm 이상이어야 한다", path="/axis")
    segs = _seg_dicts(a.segments) if a.segments is not None else [dict(s) for s in old["segments"]]
    for i, s in enumerate(segs):
        _type_row(ctx, s["type"], f"/segments/{i}/type")
    base = (a.base_offset if a.base_offset is not None else old.get("base_offset", 0.0))
    name = a.name if a.name is not None else old["name"]
    sp = _stack_params(name, axis, arc, a.height if a.height is not None else old["height"], a.line if a.line is not None else old["line"],
                       segs, _dummy(len(segs)), base)
    sp["parts"] = list(old["parts"])
    parts = regenerate(ctx, a.id, sp, lv, a.part_ids)
    sp["parts"] = parts
    new = _stack_params(name, axis, arc, sp["height"], sp["line"], segs, parts, base)
    if new == old:
        return {"id": a.id, "changed": False, "parts": parts}
    ctx.put_el(a.id, dict(r, params=new))
    return {"id": a.id, "changed": True, "parts": parts}


def delete_stack(ctx, sid: str, deleter) -> list:
    """스택 벽과 부분 벽(그 위의 문 포함)을 지운다. deleter = 벽 하나를 지우는 element.delete 본체. 지운 id 목록."""
    gone = []
    for w in list(ctx.st.el[sid]["params"]["parts"]):
        if w in ctx.st.el:
            gone += deleter(w)
    return gone
