# SPDX-License-Identifier: MPL-2.0
"""독립 개구 객체 커맨드(W2 세 번째 PR, ARC-34: 상용 도구의 수직 개구·샤프트 개구·벽 개구. W2 열네 번째 PR: 지붕·천장 개구·면 개구·고치기).

opening.vertical  호스트 하나를 경계 다각형으로 수직 관통하는 개구. 호스트 = 슬래브(지붕 슬래브 use=roof 포함)·경사 지붕(roof)·천장판(범주 ceiling 의 판).
opening.face      경사 지붕 면 하나에 면에 수직인 개구(상용 도구의 면 개구). 경계 = 면 윗면 위 스케치의 평면 투영, 면은 경계 넓이 중심이 든 면.
opening.shaft     레벨 + base_offset 에서 height 까지 z 범위의 저작 슬래브를 경계 다각형으로 한 번에 관통하는 샤프트 개구(나중에 만든 슬래브도 관통한다).
                  also 에 roof·ceiling 을 주면 그 범위의 경사 지붕·천장판도 관통한다(상용 도구의 샤프트 개구는 바닥·지붕·천장을 자른다).
opening.wall      벽 하나에 문·창 없는 사각·원 개구(offset 은 축 시작점에서 호 위 거리, sill 은 레벨 기준). 곡선 벽은 사각만.
opening.set       개구 하나를 고친다(경계·원호·이름·호스트·샤프트 높이·벽 개구 치수). 같은 맞춤 검사를 다시 한다.
opening.move      수직·면·샤프트 개구를 평면에서 옮긴다.
opening.delete    독립 개구를 지운다(호스트는 그대로 메워진다).
경계는 변마다 선택 볼록도(bulges: tan(중심각/4), 양수 = 반시계 호)로 원호를 섞을 수 있고, circle {center, radius} 는 볼록도 사분원 넷으로 저장한다.
호는 기존 원 규칙(한 바퀴 32 현)으로 펼친 고리가 도면·IFC·수량의 경계다.

저장: kind "opening" 비 IFC 의도 요소(DoBIM_Defs 로 왕복). 호스트 params 는 바꾸지 않는다: 도면(slab_info 구멍·벽 모양 개구부·지붕 절단 띠·천장판 구멍)과 IFC(호스트마다
IfcOpeningElement + IfcRelVoidsElement 부속, GlobalId = part_guid(호스트, "void:<개구 id>"), 지붕은 면 IfcSlab 마다 "void:<개구 id>:<면 번호>")가
질의(queries.slab_voids·wall_voids·roof_voids·solid_voids)로 뺀다. 호스트를 지우면 개구는 고아로 남고 그리지 않는다(치수의 고아와 같은 규칙).
호스트를 옮기거나 고쳐 맞지 않게 된 개구는 질의가 빼고 그리지 않는다(슬래브와 같이 호스트를 따라 움직이지 않는다). 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from math import pi, tan
from typing import Annotated, Literal

from pydantic import Field, model_validator

from dobim_core import fixture as F
from dobim_core import hostvoid as HV
from dobim_core import kinds as K
from dobim_core import solids as SO
from dobim_core import walltop as WT
from dobim_core.commands import A, Id, Name, Num, Pos, SlabBoundary, _fresh_id, _slab_boundary, command, row, wall_len
from dobim_core.errors import DobimError

QUARTER = tan(pi / 8.0)   # 사분원 볼록도 tan(90도/4)


def _count_ok(ctx) -> None:
    if len(ctx.st.by_kind.get("opening", ())) >= K.OPENINGS_OBJ_MAX:
        raise DobimError("CONSTRAINT", f"독립 개구는 {K.OPENINGS_OBJ_MAX}개까지다", path="/id")


def _loop(p) -> list:
    return [list(q) for q in K.opening_loop(p)]


def _ops(ctx, skip: str | None):
    for o in sorted(ctx.st.by_kind.get("opening", ())):
        if o != skip:
            yield o, ctx.st.el[o]["params"]


def _voids_of(ctx, host: str, skip: str | None = None) -> list:
    """이 슬래브를 가리키는 수직 개구 경계들(skip 제외)."""
    return [_loop(p) for _o, p in _ops(ctx, skip) if p.get("mode") == "vertical" and p["host"] == host]


def _shaft_zr(ctx, p):
    lv = ctx.st.el.get(p["level"])
    if lv is None or lv["kind"] != "level":
        return None
    z0 = lv["params"]["elevation"] + p.get("base_offset", 0.0)
    return (z0, z0 + p["height"])


def _shafts_on(ctx, s: str, skip: str | None = None) -> list:
    """슬래브 s 를 관통하는 샤프트 개구 경계들(queries.slab_voids 와 같은 규칙)."""
    out = []
    for _o, p in _ops(ctx, skip):
        if p.get("mode") != "shaft" or (zr := _shaft_zr(ctx, p)) is None:
            continue
        lp = _loop(p)
        if s in shaft_slabs(ctx, lp, zr[0], zr[1]):
            out.append(lp)
    return out


def _slab_zr(ctx, s: str) -> tuple:
    p = ctx.st.el[s]["params"]
    lv = ctx.rels_out("on_level", s)[0]
    top = ctx.st.el[lv]["params"]["elevation"] + p["top_offset"]
    return (top - p["thickness"], top)


def _boundary(boundary, bulges, circle, path: str = "/boundary"):
    """입력 경계를 (꼭짓점 목록, 볼록도 목록 또는 None)으로 정규화한다: 펼친 고리가 반시계가 되게(필요하면 꼭짓점·볼록도를 함께 뒤집는다)."""
    if circle is not None:
        (cx, cy), r = circle.center, float(circle.radius)
        b = [[cx + r, cy], [cx, cy + r], [cx - r, cy], [cx, cy - r]]
        return [[float(x), float(y)] for x, y in b], [QUARTER] * 4
    b = [[float(x), float(y)] for x, y in boundary]
    if not bulges or not any(bulges):
        return _slab_boundary(b), None
    bl = [float(v) for v in bulges]
    if len(bl) != len(b):
        raise DobimError("CONSTRAINT", "bulges 는 꼭짓점마다 하나다(변 i -> i+1)", path="/bulges")
    if K.polygon_area2(list(K.opening_loop({"boundary": b, "bulges": bl}))) < 0:   # 시계 방향 고리: 꼭짓점을 뒤집고 변 볼록도의 부호를 바꾼다
        n = len(b)
        b, bl = b[::-1], [-bl[(n - 2 - j) % n] for j in range(n)]
    why = K.opening_bulges_problem(b, bl) or (None if K.polygon_area2(b) > 0 else "꼭짓점 다각형과 펼친 고리의 방향이 다르다")
    if why:
        raise DobimError("CONSTRAINT", f"개구 경계가 올바르지 않다: {why}", path=path)
    return b, bl


def _set_boundary(p: dict, b, bl) -> None:
    p["boundary"] = b
    if bl is None:
        p.pop("bulges", None)
    else:
        p["bulges"] = bl


# ---------------------------------------------------------------- 지붕·천장판(W2 열네 번째 PR)
def _roof_zr(ctx, r: str, gm) -> tuple:
    lv = ctx.rels_out("on_level", r)[0]
    e = ctx.st.el[lv]["params"]["elevation"]
    return (e + gm["zlow"] - ctx.st.el[r]["params"]["thickness"], e + gm["ztop"])


def _roof_accepted(ctx, r: str, gm, skip: str | None) -> list:
    """지붕 r 에 지금 뚫린 개구 고리들(queries.roof_voids 와 같은 규칙, skip 제외)."""
    cand = []
    for _o, p in _ops(ctx, skip):
        if p.get("mode") in ("vertical", "face") and p["host"] == r:
            cand.append((p["mode"], tuple(map(tuple, _loop(p)))))
        elif p.get("mode") == "shaft" and "roof" in p.get("also", ()) and (zr := _shaft_zr(ctx, p)) is not None:
            zb, zt = _roof_zr(ctx, r, gm)
            if zr[0] <= zt + 1e-6 and zb < zr[1] - 1e-6:
                cand.append(("shaft", tuple(map(tuple, _loop(p)))))
    acc = []
    for m, lp in cand:
        if m == "face":
            i = HV.face_of(gm, lp)
            if i is None or not HV.inside(lp, HV.face_xy(gm["faces"][i])):
                continue
        if K.openings_problem(gm["outline"], acc + [lp]) is None:
            acc.append(lp)
    return acc


def _roof_fit(ctx, r: str, mode: str, lp, skip: str | None, path: str = "/boundary"):
    """지붕 개구가 맞는가(안 맞으면 CONSTRAINT). 뚫는 면 번호들을 돌려준다."""
    gm = K.roof_geom(ctx.st.el[r]["params"])
    lp = tuple(map(tuple, lp))
    if mode == "face":
        i = HV.face_of(gm, lp)
        if i is None:
            raise DobimError("CONSTRAINT", "면 개구의 경계 가운데가 지붕 면 위에 있지 않다", path=path)
        if not HV.inside(lp, HV.face_xy(gm["faces"][i])):
            raise DobimError("CONSTRAINT", f"면 개구가 지붕 면 {i} 하나 안에 다 들지 않는다(면 개구는 한 면에만 낸다: 여러 면은 수직 개구로)", path=path)
    why = K.openings_problem(gm["outline"], _roof_accepted(ctx, r, gm, skip) + [lp])
    if why:
        raise DobimError("CONSTRAINT", f"지붕 개구가 지붕에 맞지 않는다: {why}", path=path, hint="처마를 내민 지붕 외곽 안쪽, 다른 개구와 겹치지 않게")
    return HV.roof_void(gm, ctx.st.el[r]["params"]["thickness"], mode, lp)["faces"]


def _is_ceiling(p) -> bool:
    return p.get("category") == "ceiling" and p.get("form") == "plate"


def _solid_zr(ctx, e: str) -> tuple:
    verts, _ = SO.solid_mesh(ctx.st.el[e]["params"])
    lv = ctx.st.el[ctx.rels_out("on_level", e)[0]]["params"]["elevation"]
    return (lv + min(v[2] for v in verts), lv + max(v[2] for v in verts))


def _ceiling_accepted(ctx, e: str, skip: str | None) -> list:
    """천장판 e 에 지금 뚫린 개구 고리들(queries.solid_voids 와 같은 규칙, skip 제외)."""
    sp = ctx.st.el[e]["params"]
    holes = [tuple(map(tuple, h)) for h in sp.get("holes", ())]
    acc = []
    for _o, p in _ops(ctx, skip):
        lp = None
        if p.get("mode") == "vertical" and p["host"] == e:
            lp = tuple(map(tuple, _loop(p)))
        elif p.get("mode") == "shaft" and "ceiling" in p.get("also", ()) and (zr := _shaft_zr(ctx, p)) is not None:
            zb, zt = _solid_zr(ctx, e)
            if zr[0] <= zt + 1e-6 and zb < zr[1] - 1e-6:
                lp = tuple(map(tuple, _loop(p)))
        if lp is not None and K.openings_problem(sp["polygon"], holes + acc + [lp]) is None:
            acc.append(lp)
    return holes + acc


def _ceiling_fit(ctx, e: str, lp, skip: str | None, path: str = "/boundary") -> None:
    sp = ctx.st.el[e]["params"]
    why = K.openings_problem(sp["polygon"], _ceiling_accepted(ctx, e, skip) + [tuple(map(tuple, lp))])
    if why:
        raise DobimError("CONSTRAINT", f"천장 개구가 천장판에 맞지 않는다: {why}", path=path, hint="천장판 윤곽 안쪽, 판의 구멍·다른 개구와 겹치지 않게")


def _host_kind(ctx, host: str, mode: str) -> str:
    """수직·면 개구 호스트의 종류(slab·roof·ceiling). 맞지 않으면 CONSTRAINT."""
    r = ctx.authored(host, None, what="개구 호스트")
    k = r["kind"]
    if mode == "face":
        if k != "roof":
            raise DobimError("CONSTRAINT", "면 개구의 호스트는 경사 지붕(roof)이다(슬래브·천장판은 수직 개구로)", path="/host")
        return "roof"
    if k == "solid" and _is_ceiling(r["params"]):
        return "ceiling"
    if k not in ("slab", "roof"):
        raise DobimError("CONSTRAINT", "수직 개구의 호스트는 슬래브·경사 지붕·천장판(범주 ceiling 의 판)이다", path="/host")
    return k


def _fit(ctx, p: dict, skip: str | None = None, path: str = "/boundary") -> dict:
    """개구 params p 가 호스트에 맞는지 검사한다(create·set 공통). 응답에 더할 값을 돌려준다."""
    mode = p["mode"]
    if mode == "wall":
        wall_fit(ctx, p["host"], K.opening_shape(p), skip=skip, path="/offset" if skip is None else path)
        return {}
    lp = _loop(p)
    if mode == "shaft":
        z0, z1 = _shaft_zr(ctx, p) or (None, None)
        if z0 is None:
            raise DobimError("NOT_FOUND", f"level 을 찾을 수 없다: {p['level']}", path="/level")
        cut = shaft_slabs(ctx, lp, z0, z1)
        for s in cut:   # 관통하는 슬래브의 수직 개구와 겹치면 거절한다
            for v in _voids_of(ctx, s, skip):
                if K.openings_problem(ctx.st.el[s]["params"]["boundary"], [v, lp]):
                    raise DobimError("CONSTRAINT", f"샤프트 개구가 슬래브 {s} 의 수직 개구와 겹친다", path=path)
        out = {"slabs": cut, "bottom": z0, "top": z1}
        also = p.get("also", ())
        if "roof" in also:   # 지붕(W2 열네 번째 PR): z 범위가 겹치고 외곽 안에 드는 지붕을 관통한다. 다른 개구와 겹치면 거절
            out["roofs"] = []
            for r in sorted(ctx.st.by_kind.get("roof", ())):
                if ctx.st.el[r]["origin"] != "authored":
                    continue
                gm = K.roof_geom(ctx.st.el[r]["params"])
                zb, zt = _roof_zr(ctx, r, gm)
                if not (z0 <= zt + 1e-6 and zb < z1 - 1e-6) or not all(K.point_in_polygon(q, gm["outline"]) == 1 for q in lp):
                    continue
                _roof_fit(ctx, r, "shaft", lp, skip, path)
                out["roofs"].append(r)
        if "ceiling" in also:   # 천장판(W2 열네 번째 PR)
            out["ceilings"] = []
            for e in sorted(ctx.st.by_kind.get("solid", ())):
                sr = ctx.st.el[e]
                if sr["origin"] != "authored" or not _is_ceiling(sr["params"]):
                    continue
                zb, zt = _solid_zr(ctx, e)
                if not (z0 <= zt + 1e-6 and zb < z1 - 1e-6) or not all(K.point_in_polygon(q, sr["params"]["polygon"]) == 1 for q in lp):
                    continue
                _ceiling_fit(ctx, e, lp, skip, path)
                out["ceilings"].append(e)
        return out
    hk = _host_kind(ctx, p["host"], mode)
    if hk == "slab":
        r = ctx.st.el[p["host"]]
        holes = [list(map(list, o)) for o in r["params"].get("openings", ())] + _voids_of(ctx, p["host"], skip) + _shafts_on(ctx, p["host"], skip)
        why = K.openings_problem(r["params"]["boundary"], holes + [lp])
        if why:
            raise DobimError("CONSTRAINT", f"수직 개구가 슬래브에 맞지 않는다: {why}", path=path, hint="슬래브 경계 안쪽, 다른 구멍과 겹치지 않게")
        return {}
    if hk == "roof":
        return {"faces": list(_roof_fit(ctx, p["host"], mode, lp, skip, path))}
    _ceiling_fit(ctx, p["host"], lp, skip, path)
    return {}


def _area(p) -> float:
    return round(abs(K.polygon_area2(K.opening_loop(p))) / 2e6, 6)


class Circle(A):
    center: Annotated[list[Num], Field(min_length=2, max_length=2)] = Field(description="원 중심 [x, y] mm")
    radius: Annotated[float, Field(gt=1.0, le=1e6, allow_inf_nan=False)] = Field(description="반지름 mm")


Bulges = Annotated[list[Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]], Field(max_length=256,
                   description="경계 변마다 볼록도 tan(중심각/4)(변 i -> i+1, 0 = 직선, 양수 = 반시계 호, 1 = 반원). 원호는 한 바퀴 32 현으로 펼친다")]


class _Shape(A):
    boundary: SlabBoundary | None = None
    bulges: Bulges | None = None
    circle: Circle | None = Field(None, description="원 개구(boundary 대신): 중심·반지름. 볼록도 사분원 넷으로 저장한다")

    @model_validator(mode="after")
    def _one(self):
        if (self.boundary is None) == (self.circle is None):
            raise ValueError("boundary 와 circle 중 하나를 준다")
        if self.circle is not None and self.bulges is not None:
            raise ValueError("circle 에는 bulges 를 주지 않는다")
        return self


class OpeningVertical(_Shape):
    id: Id | None = None
    host: Id = Field(description="관통할 슬래브(지붕 슬래브 포함)·경사 지붕·천장판(범주 ceiling 의 판)")
    name: Name | None = None


@command("opening.vertical", kind="mutation", risk="write", group="model.opening",
         summary="수직 개구: 슬래브·경사 지붕·천장판 하나를 경계 다각형(원호 가능)으로 수직 관통하는 독립 개구(호스트 params 는 그대로, 도면·IFC 가 뺀다). IFC 는 IfcOpeningElement + IfcRelVoidsElement",
         args=OpeningVertical, since="0.16", examples=[{"host": F.S1, "boundary": [[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]},
                                                         {"host": F.RF1, "circle": {"center": [1500, 1000], "radius": 400}}])
def opening_vertical(ctx, a: OpeningVertical):
    b, bl = _boundary(a.boundary, a.bulges, a.circle)
    p = {"name": "", "mode": "vertical", "host": a.host}
    _set_boundary(p, b, bl)
    extra = _fit(ctx, p)
    _count_ok(ctx)
    _fresh_id(ctx, a, "opening")
    p["name"] = a.name or f"OP-{a.id[-6:]}"
    p = {k: p[k] for k in ("name", "mode", "host", "boundary", "bulges") if k in p}
    ctx.put_el(a.id, row("opening", "", "opening.void", p))
    return {"id": a.id, "host": a.host, "area_m2": _area(p), **extra}


class OpeningFace(_Shape):
    id: Id | None = None
    host: Id = Field(description="경사 지붕(roof)")
    name: Name | None = None


@command("opening.face", kind="mutation", risk="write", group="model.opening",
         summary="면 개구: 경사 지붕 면 하나에 면에 수직인 독립 개구(경계 = 면 윗면 위 스케치의 평면 투영, 원호 가능). IFC 는 그 면 IfcSlab 의 IfcOpeningElement(면 법선 방향 돌출)",
         args=OpeningFace, since="0.23", examples=[{"host": F.RF1, "boundary": [[2500, 300], [3500, 300], [3500, 1200], [2500, 1200]]}])
def opening_face(ctx, a: OpeningFace):
    b, bl = _boundary(a.boundary, a.bulges, a.circle)
    p = {"name": "", "mode": "face", "host": a.host}
    _set_boundary(p, b, bl)
    extra = _fit(ctx, p)
    _count_ok(ctx)
    _fresh_id(ctx, a, "opening")
    p["name"] = a.name or f"OP-{a.id[-6:]}"
    p = {k: p[k] for k in ("name", "mode", "host", "boundary", "bulges") if k in p}
    ctx.put_el(a.id, row("opening", "", "opening.void", p))
    return {"id": a.id, "host": a.host, "area_m2": _area(p), "face": extra["faces"][0]}


Also = Annotated[list[Literal["roof", "ceiling"]], Field(max_length=2, description="슬래브 말고도 관통할 종류: roof(경사 지붕)·ceiling(천장판). 빈 목록이면 슬래브만")]


class OpeningShaft(_Shape):
    id: Id | None = None
    level: Id = Field(description="샤프트 바닥 레벨")
    height: Annotated[float, Field(gt=0, le=K.SHAFT_HEIGHT_MAX, allow_inf_nan=False)] = Field(description="바닥(레벨 + base_offset)에서 위로 mm")
    base_offset: Num | None = Field(None, description="레벨 기준 바닥 오프셋 mm(생략·0 이면 레벨에서)")
    also: Also | None = None
    name: Name | None = None


def shaft_slabs(ctx, boundary, z0: float, z1: float, skip: str | None = None) -> list:
    """샤프트가 관통하는 저작 슬래브(queries.slab_voids 와 같은 규칙: z 범위가 겹치고 경계가 슬래브 경계 안이며 슬래브 구멍과 겹치지 않는다)."""
    out = []
    for s in sorted(ctx.st.by_kind.get("slab", ())):
        r = ctx.st.el[s]
        if r["origin"] != "authored":
            continue
        zb, zt = _slab_zr(ctx, s)
        if z0 <= zt + 1e-6 and zb < z1 - 1e-6 and all(K.point_in_polygon(q, r["params"]["boundary"]) == 1 for q in boundary) and not any(
                K.point_in_polygon(q, h) >= 0 for h in r["params"].get("openings", ()) for q in boundary):
            out.append(s)
    return out


def _also(v) -> list | None:
    return sorted(set(v)) if v else None


@command("opening.shaft", kind="mutation", risk="write", group="model.opening",
         summary="샤프트 개구: 레벨 범위(바닥 레벨 + base_offset 에서 height 까지)의 슬래브(also 로 경사 지붕·천장판도)를 경계 다각형으로 한 번에 관통하는 독립 개구. 평면은 경계 + X 기호",
         args=OpeningShaft, since="0.16", examples=[{"level": F.LV1, "boundary": [[4000, 500], [5000, 500], [5000, 1500], [4000, 1500]], "height": 6600},
                                                      {"level": F.LV1, "circle": {"center": [4500, 1000], "radius": 300}, "height": 9000, "also": ["roof", "ceiling"]}])
def opening_shaft(ctx, a: OpeningShaft):
    ctx.authored(a.level, "level", what="샤프트 개구 바닥 레벨")
    b, bl = _boundary(a.boundary, a.bulges, a.circle)
    _count_ok(ctx)
    _fresh_id(ctx, a, "opening")
    a.name = a.name or f"SH-{a.id[-6:]}"
    p = {"name": a.name, "mode": "shaft", "level": a.level, "boundary": b, "height": float(a.height)}
    if a.base_offset:
        p["base_offset"] = float(a.base_offset)
    if bl is not None:
        p["bulges"] = bl
    if _also(a.also):
        p["also"] = _also(a.also)
    extra = _fit(ctx, p)
    ctx.put_el(a.id, row("opening", "", "opening.void", p))
    return {"id": a.id, **extra}


class OpeningWall(A):
    id: Id | None = None
    host: Id = Field(description="뚫을 벽(곡선 벽은 사각만)")
    shape: Literal["rect", "circle"] = "rect"
    offset: Num = Field(description="벽 축 시작점에서 개구 가운데까지 mm(곡선 벽은 호 위 거리)")
    sill: Num = Field(0.0, description="레벨 기준 개구 아래 끝 mm")
    width: Pos = 1000.0
    height: Pos = 1000.0
    name: Name | None = None


def wall_fit(ctx, host: str, d: dict, skip: str | None = None, path: str = "/offset") -> None:
    """벽 개구 d 가 벽 입면(축 길이 x 바닥~윗면, 상단 윤곽) 안이고 모양 개구부·다른 벽 개구·문 상자와 겹치지 않는가."""
    p = ctx.st.el[host]["params"]
    ln = wall_len(p)
    b0 = WT.base(p)
    top = p.get("top_profile")
    outer = WT.loop(top, 0.0, ln, b0) if top is not None else [(0.0, b0), (ln, b0), (ln, b0 + p["height"]), (0.0, b0 + p["height"])]
    others = list(p.get("openings", ())) + [K.opening_shape(ctx.st.el[o]["params"]) for o in sorted(ctx.st.by_kind.get("opening", ()))
                                            if o != skip and ctx.st.el[o]["params"].get("mode") == "wall" and ctx.st.el[o]["params"]["host"] == host]
    why = SO.openings_problem(outer, others + [d], absolute_sill=True) or (WT.under_problem(top, [d]) if top is not None else None)
    if why:
        raise DobimError("CONSTRAINT", f"벽 개구가 벽에 맞지 않는다: {why}", path=path)
    from dobim_core.commands import _hosted_span
    u0, u1 = d["offset"] - d["width"] / 2.0, d["offset"] + d["width"] / 2.0
    for h in sorted(ctx.rels_out("hosts", host)):
        lo, hi = _hosted_span(ctx, h)
        if lo < u1 - 1e-6 and u0 < hi - 1e-6 and ctx.st.el[h]["kind"] == "door":
            hp = ctx.st.el[h]["params"]
            if hp["sill"] < d["sill"] + d["height"] - 1e-6 and d["sill"] < hp["sill"] + hp["height"] - 1e-6:
                raise DobimError("CONSTRAINT", f"벽 개구가 문·창 {h} 와 겹친다", path=path)


def _wall_shape_ok(ctx, host: str, shape: str, width: float, height: float) -> None:
    r = ctx.authored(host, "wall", what="벽 개구 호스트")
    if shape == "circle" and abs(width - height) > 1e-6:
        raise DobimError("CONSTRAINT", "원 개구는 폭과 높이가 같아야 한다", path="/height")
    if shape != "rect" and "arc" in r["params"]:
        raise DobimError("CONSTRAINT", "곡선 벽의 벽 개구는 사각만 된다", path="/shape")


@command("opening.wall", kind="mutation", risk="write", group="model.opening",
         summary="벽 개구: 벽 하나에 문·창 없는 사각·원 개구를 독립 객체로 낸다(벽 params 는 그대로). 곡선 벽은 사각만(호 위 거리 offset)",
         args=OpeningWall, since="0.16", examples=[{"host": F.W2, "offset": 2800, "sill": 900, "width": 800, "height": 1200},
                                                    {"host": F.W1, "shape": "circle", "offset": 4500, "sill": 1500, "width": 600, "height": 600}])
def opening_wall(ctx, a: OpeningWall):
    _wall_shape_ok(ctx, a.host, a.shape, a.width, a.height)
    d = {"shape": a.shape, "offset": float(a.offset), "sill": float(a.sill), "width": float(a.width), "height": float(a.height)}
    wall_fit(ctx, a.host, d)
    _count_ok(ctx)
    _fresh_id(ctx, a, "opening")
    a.name = a.name or f"OP-{a.id[-6:]}"
    ctx.put_el(a.id, row("opening", "", "opening.void", {"name": a.name, "mode": "wall", "host": a.host, **d}))
    return {"id": a.id, "host": a.host, "area_m2": round(abs(SO.area2(SO.opening_loop(d))) / 2e6, 6)}


# ---------------------------------------------------------------- 고치기·옮기기(W2 열네 번째 PR)
class OpeningSet(A):
    forbid_null = False
    id: Id
    name: Name | None = None
    host: Id | None = Field(None, description="수직·면·벽 개구의 새 호스트")
    boundary: SlabBoundary | None = Field(None, description="수직·면·샤프트 개구의 새 경계(볼록도는 bulges 를 함께 주지 않으면 뗀다)")
    bulges: Bulges | None = Field(None, description="경계 변마다 볼록도. 경계 없이 주면 지금 경계에 입힌다. 빈 목록이면 뗀다")
    circle: Circle | None = Field(None, description="원 개구로 바꾼다(boundary 대신)")
    height: Pos | None = Field(None, description="샤프트 높이 또는 벽 개구 높이 mm")
    base_offset: Num | None = Field(None, description="샤프트 바닥 오프셋 mm(0 이면 뗀다)")
    level: Id | None = Field(None, description="샤프트 바닥 레벨")
    also: Also | None = Field(None, description="샤프트가 함께 관통할 종류(빈 목록이면 슬래브만)")
    shape: Literal["rect", "circle"] | None = None
    offset: Num | None = None
    sill: Num | None = None
    width: Pos | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in ("name", "host", "boundary", "bulges", "circle", "height", "base_offset", "level", "also", "shape", "offset", "sill", "width")):
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.circle is not None and (self.boundary is not None or self.bulges is not None):
            raise ValueError("circle 은 boundary·bulges 와 함께 주지 않는다")
        return self


_MODE_FIELDS = {"vertical": ("name", "host", "boundary", "bulges", "circle"), "face": ("name", "host", "boundary", "bulges", "circle"),
                "shaft": ("name", "boundary", "bulges", "circle", "height", "base_offset", "level", "also"),
                "wall": ("name", "host", "shape", "offset", "sill", "width", "height")}


@command("opening.set", kind="mutation", risk="write", group="model.opening",
         summary="독립 개구 하나를 고친다: 경계(원호·원)·이름·호스트, 샤프트 레벨·높이·바닥 오프셋·also, 벽 개구 모양·위치·치수. 고친 값으로 맞춤 검사를 다시 한다",
         args=OpeningSet, since="0.23", examples=[{"id": F.OP1, "offset": 2600, "width": 900}])
def opening_set(ctx, a: OpeningSet):
    r = ctx.row(a.id, "opening")
    p = dict(r["params"])
    mode = p["mode"]
    bad = [k for k in ("name", "host", "boundary", "bulges", "circle", "height", "base_offset", "level", "also", "shape", "offset", "sill", "width")
           if getattr(a, k) is not None and k not in _MODE_FIELDS[mode]]
    if bad:
        raise DobimError("CONSTRAINT", f"{mode} 개구에는 {', '.join(bad)} 를 고칠 수 없다", path=f"/{bad[0]}")
    if a.name is not None:
        p["name"] = a.name
    if mode == "wall":
        for k in ("host", "shape", "offset", "sill", "width", "height"):
            v = getattr(a, k)
            if v is not None:
                p[k] = float(v) if k in ("offset", "sill", "width", "height") else v
        _wall_shape_ok(ctx, p["host"], p["shape"], p["width"], p["height"])
    else:
        if a.host is not None:
            p["host"] = a.host
        if a.circle is not None or a.boundary is not None:
            b, bl = _boundary(a.boundary, a.bulges, a.circle)
            _set_boundary(p, b, bl)
        elif a.bulges is not None:
            if not a.bulges:
                _set_boundary(p, p["boundary"], None)
            else:
                b, bl = _boundary(p["boundary"], a.bulges, None, path="/bulges")
                _set_boundary(p, b, bl)
        if mode == "shaft":
            if a.level is not None:
                ctx.authored(a.level, "level", what="샤프트 개구 바닥 레벨")
                p["level"] = a.level
            if a.height is not None:
                p["height"] = float(a.height)
            if a.base_offset is not None:
                if a.base_offset:
                    p["base_offset"] = float(a.base_offset)
                else:
                    p.pop("base_offset", None)
            if a.also is not None:
                if _also(a.also):
                    p["also"] = _also(a.also)
                else:
                    p.pop("also", None)
    why = K.row_params_error("opening", p)
    if why:
        raise DobimError("CONSTRAINT", f"개구가 올바르지 않다: {why}")
    extra = _fit(ctx, p, skip=a.id, path="/boundary" if mode != "wall" else "/offset")
    ctx.put_el(a.id, dict(r, params=p))
    out = {"id": a.id, **extra}
    if mode != "shaft":
        out["host"] = p["host"]
    return out


class OpeningMove(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=200, description="옮길 수직·면·샤프트 개구 id 목록")]
    dx: Num = 0.0
    dy: Num = 0.0


@command("opening.move", kind="mutation", risk="write", group="model.opening",
         summary="수직·면·샤프트 개구를 평면에서 평행 이동한다(벽 개구는 opening.set offset). 옮긴 자리에서 맞춤 검사를 다시 한다",
         args=OpeningMove, since="0.23", examples=[{"ids": [F.OP1], "dx": 300, "dy": 0}])
def opening_move(ctx, a: OpeningMove):
    ids = list(dict.fromkeys(a.ids))
    rows = [ctx.row(i, "opening") for i in ids]
    for i, r in zip(ids, rows):
        if r["params"]["mode"] == "wall":
            raise DobimError("CONSTRAINT", f"벽 개구 {i} 는 opening.set offset 으로 옮긴다", path="/ids")
    for i, r in zip(ids, rows):   # 모두 옮긴 뒤 하나씩 검사한다(함께 옮기는 개구끼리는 서로 비켜 갈 수 있다)
        p = dict(r["params"], boundary=[[q[0] + a.dx, q[1] + a.dy] for q in r["params"]["boundary"]])
        ctx.put_el(i, dict(r, params=p))
    for i in ids:
        _fit(ctx, ctx.st.el[i]["params"], skip=i)
    return {"moved": ids}


class OpeningDelete(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=200, description="지울 독립 개구 id 목록")]


@command("opening.delete", kind="mutation", risk="write", group="model.opening", summary="독립 개구(수직·면·샤프트·벽)를 지운다(호스트는 메워진다)",
         args=OpeningDelete, since="0.16", examples=[{"ids": [F.OP1]}])
def opening_delete(ctx, a: OpeningDelete):
    ids = list(dict.fromkeys(a.ids))
    for i in ids:
        ctx.row(i, "opening")
    for i in ids:
        ctx.put_el(i, None)
    return {"deleted": ids}
