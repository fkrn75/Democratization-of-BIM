# SPDX-License-Identifier: MPL-2.0
"""구조 보·보 유형 커맨드(W2 열다섯 번째 PR, STR-02·STR-03·STR-07).

beamtype.create / set / duplicate / delete / list : 이름 붙은 보 유형 = 단면(사각: 폭·춤, H형강: 폭·춤·웨브·플랜지). preset 으로 KS H형강
                                                   표준 단면(beams.H_PRESETS)을 고를 수 있다. 벽·지붕·난간 유형과 같은 설계다: 보 행이 유형의 단면
                                                   사본을 갖고(params.btype·section + 관계 of_type), 유형을 고치면 그 유형의 보 행만 다시 쓴다
beam.create / set / move / type.set : 평면 두 점 사이의 수평 보(레벨·기준선 높이 offset·z 맞춤 anchor·단면 회전 roll·기둥 면 자르기 cutback)
beam.grid : 통심선을 따라 보를 한꺼번에 놓는다(통심선 교점 사이마다, 기본은 두 교점 모두에 기둥이 있는 칸만: 상용 도구의 '그리드에' 배치)
삭제는 element.delete, 복사·회전·대칭은 element.copy·rotate·mirror 가 한다(commands_edit). 형상은 beams.py 가 정하고 도면·IFC(IfcBeam 몸체 =
IfcRectangleProfileDef·IfcIShapeProfileDef 돌출, IfcBeamType + IfcRelDefinesByType)·일람표가 같은 값을 쓴다. 보가 없는 프로젝트는 바이트까지 그대로다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from dobim_core import beams as BM
from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import queries as Q
from dobim_core.commands import A, Id, Name, Num, command, row
from dobim_core.errors import DobimError

IDS_MAX = 500           # beam.type.set 이 한 번에 받는 보 수 상한
GRID_BEAMS_MAX = 2000   # beam.grid 가 한 번에 만드는 보 수 상한
ANS_IDS = 100           # 응답에 싣는 새 id 수 상한
Preset = Literal[tuple(BM.H_PRESETS)]
Coord = Annotated[float, Field(ge=-BM.COORD_MAX, le=BM.COORD_MAX, allow_inf_nan=False)]
Pt = Annotated[list[Coord], Field(min_length=2, max_length=2, description="[x, y] mm(레벨 좌표)")]
Dim = Annotated[float, Field(gt=0, le=BM.SEC_DIM_MAX, allow_inf_nan=False)]


# ---------------------------------------------------------------- 공통 도우미
def _types(ctx) -> list:
    return sorted(ctx.st.by_kind.get("beamtype", ()))


def type_row(ctx, tid: str, path: str = "/id") -> dict:
    r = ctx.st.el.get(tid)
    if r is None or r["kind"] != "beamtype":
        raise DobimError("NOT_FOUND", f"보 유형을 찾을 수 없다: {tid}", path=path, hint="beamtype.list 로 유형을 보세요")
    return r


def beams_of(ctx, tid: str) -> list:
    return sorted(b for b in ctx.rels_in("of_type", tid) if ctx.st.el[b]["kind"] == "beam")


def _name_taken(ctx, name: str, skip: str | None = None) -> None:
    for t in _types(ctx):
        if t != skip and ctx.st.el[t]["params"]["name"] == name:
            raise DobimError("CONSTRAINT", f"같은 이름의 보 유형이 있다: {name}", path="/name")


def _check_beam(bid: str, q: dict, path: str) -> dict:
    why = K.row_params_error("beam", q)
    if why:
        raise DobimError("CONSTRAINT", f"보 {bid} 가 올바르지 않게 된다: {why}", path=path)
    return q


def _write_beam(ctx, bid: str, q: dict, path: str) -> None:
    r = ctx.st.el[bid]
    if q != r["params"]:
        ctx.put_el(bid, dict(r, params=_check_beam(bid, q, path)))


def retype(ctx, bid: str, tid: str, tp: dict, path: str) -> None:
    """보 bid 에 유형(tid, params tp)을 입힌다: 단면을 유형 것으로."""
    r = ctx.st.el[bid]
    p = r["params"]
    _write_beam(ctx, bid, dict(p, btype=tid, section=dict(tp["section"])), path)
    old = p.get("btype")
    if old != tid:
        if old is not None:
            ctx.rel("of_type", bid, old, False)
        ctx.rel("of_type", bid, tid, True)


def detach(ctx, bid: str) -> None:
    """보에서 유형을 뗀다(단면은 남는다)."""
    r = ctx.st.el[bid]
    old = r["params"].get("btype")
    if old is None:
        return
    ctx.put_el(bid, dict(r, params={k: v for k, v in r["params"].items() if k != "btype"}))
    ctx.rel("of_type", bid, old, False)


def _section(base: dict | None, preset, shape, width, depth, web, flange) -> dict:
    """입력 -> 단면. preset 이 있으면 그 표준 단면, 아니면 base(없으면 사각 300x600) 위에 준 값만 바꾼다."""
    if preset is not None:
        if any(v is not None for v in (shape, width, depth, web, flange)):
            raise DobimError("CONSTRAINT", "preset 과 shape·width·depth·web·flange 를 함께 줄 수 없다", path="/preset")
        return BM.preset_section(preset)
    sc = dict(base) if base else {"shape": "rect", "width": 300.0, "depth": 600.0}
    if shape is not None and shape != sc["shape"]:
        sc = {"shape": shape, "width": sc["width"], "depth": sc["depth"]}
        if shape == "h" and (web is None or flange is None):
            raise DobimError("CONSTRAINT", "H형 단면은 web·flange 를 준다", path="/web")
    for k, v in (("width", width), ("depth", depth), ("web", web), ("flange", flange)):
        if v is not None:
            if k in ("web", "flange") and sc["shape"] != "h":
                raise DobimError("CONSTRAINT", f"{k} 는 H형 단면(shape h)에만 있다", path=f"/{k}")
            sc[k] = float(v)
    why = BM.section_problem(sc)
    if why:
        raise DobimError("CONSTRAINT", f"단면이 올바르지 않다: {why}", path="/shape")
    return sc


def _pub_type(ctx, tid: str) -> dict:
    p = ctx.st.el[tid]["params"]
    return {"id": tid, "name": p["name"], "section": dict(p["section"]), "label": BM.section_label(p["section"]), "beams": len(beams_of(ctx, tid))}


# ---------------------------------------------------------------- 보 유형
class _SecIn(A):
    preset: Preset | None = Field(None, description="KS H형강 표준 단면 이름(춤x폭x웨브x플랜지): " + ", ".join(BM.H_PRESETS))
    shape: Literal["rect", "h"] | None = Field(None, description="rect 사각(철근콘크리트 보), h H형강")
    width: Dim | None = Field(None, description="폭 mm(H형은 플랜지 폭 B)")
    depth: Dim | None = Field(None, description="춤 mm(H형은 전체 높이 H)")
    web: Dim | None = Field(None, description="H형 웨브 두께 t1 mm")
    flange: Dim | None = Field(None, description="H형 플랜지 두께 t2 mm")


class BeamTypeCreate(_SecIn):
    id: Id | None = None
    name: Name = Field(description="유형 이름(프로젝트 안에서 유일)")


@command("beamtype.create", kind="mutation", risk="write", group="model.beam",
         summary="보 유형(단면: 사각 폭·춤 또는 H형강 폭·춤·웨브·플랜지, KS H형강 표준 단면 preset)을 만든다. 보는 beam.create type 으로 놓는다",
         args=BeamTypeCreate, since="0.22",
         examples=[{"name": "G1 400x700", "shape": "rect", "width": 400, "depth": 700}, {"name": "SG1", "preset": "H-400x200x8x13"}])
def beamtype_create(ctx, a: BeamTypeCreate):
    if len(_types(ctx)) >= BM.BEAMTYPES_MAX:
        raise DobimError("CONSTRAINT", f"보 유형은 {BM.BEAMTYPES_MAX}개까지다", path="/name")
    _name_taken(ctx, a.name)
    sc = _section(None, a.preset, a.shape, a.width, a.depth, a.web, a.flange)
    C._fresh_id(ctx, a, "beamtype")
    if ctx.external_def(a.id):
        ctx.warn("가져온 파일에 이 id 의 보 유형 정의가 되살려지지 않은 채 남아 있다: 새 정의가 그 속성을 덮어쓴다")
    ctx.put_el(a.id, row("beamtype", "", "beam.type", {"name": a.name, "section": sc}))
    return {"id": a.id, "name": a.name, "label": BM.section_label(sc)}


class BeamTypeSet(_SecIn):
    forbid_null = True
    id: Id
    name: Name | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in ("name", "preset", "shape", "width", "depth", "web", "flange")):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("beamtype.set", kind="mutation", risk="write", group="model.beam",
         summary="보 유형을 고친다(이름·단면). 그 유형의 모든 보가 따라간다",
         args=BeamTypeSet, since="0.22", examples=[{"id": F.BT1, "depth": 800}])
def beamtype_set(ctx, a: BeamTypeSet):
    r = type_row(ctx, a.id)
    old = r["params"]
    if a.name is not None:
        _name_taken(ctx, a.name, skip=a.id)
    p = {"name": a.name if a.name is not None else old["name"], "section": _section(old["section"], a.preset, a.shape, a.width, a.depth, a.web, a.flange)}
    if p == old:
        return {"id": a.id, "changed": False, "beams": 0}
    ctx.put_el(a.id, dict(r, params=p))
    n = 0
    if p["section"] != old["section"]:   # 형상 키가 바뀐 때만 보를 다시 쓴다(이름은 보 행에 없다)
        for bid in beams_of(ctx, a.id):
            before = ctx.st.el[bid]["params"]
            retype(ctx, bid, a.id, p, "/id")
            n += ctx.st.el[bid]["params"] != before
    return {"id": a.id, "changed": True, "beams": n}


class BeamTypeDup(A):
    id: Id = Field(description="복제할 유형")
    name: Name = Field(description="새 유형 이름")
    new_id: Id | None = None


@command("beamtype.duplicate", kind="mutation", risk="write", group="model.beam", summary="보 유형을 새 이름으로 복제한다(보는 옮기지 않는다)",
         args=BeamTypeDup, since="0.22", examples=[{"id": F.BT1, "name": "G2 400x700"}])
def beamtype_duplicate(ctx, a: BeamTypeDup):
    r = type_row(ctx, a.id)
    if len(_types(ctx)) >= BM.BEAMTYPES_MAX:
        raise DobimError("CONSTRAINT", f"보 유형은 {BM.BEAMTYPES_MAX}개까지다", path="/name")
    _name_taken(ctx, a.name)
    holder = BeamTypeCreate(name=a.name, id=a.new_id)
    C._fresh_id(ctx, holder, "beamtype")
    ctx.put_el(holder.id, row("beamtype", "", "beam.type", {"name": a.name, "section": dict(r["params"]["section"])}))
    return {"id": holder.id, "name": a.name}


class BeamTypeDelete(A):
    id: Id
    detach: bool = Field(False, description="true 면 이 유형의 보에서 유형을 떼고(단면은 남는다) 지운다. false 면 쓰는 보가 있을 때 거절")


@command("beamtype.delete", kind="mutation", risk="write", group="model.beam", summary="보 유형을 지운다(쓰는 보가 있으면 detach 로 떼어 낸다)",
         args=BeamTypeDelete, since="0.22", examples=[{"id": F.BT1, "detach": True}])
def beamtype_delete(ctx, a: BeamTypeDelete):
    type_row(ctx, a.id)
    beams = beams_of(ctx, a.id)
    if beams and not a.detach:
        raise DobimError("CONSTRAINT", f"보 {len(beams)}개가 이 유형을 쓴다", path="/id", hint="detach: true 로 보에서 떼고 지우거나 beam.type.set 으로 바꾸세요",
                         details={"beams": beams[:20]})
    for bid in beams:
        detach(ctx, bid)
    ctx.put_el(a.id, None)
    return {"deleted": [a.id], "detached": len(beams)}


class BeamTypeList(A):
    limit: int = Field(100, ge=1, le=BM.BEAMTYPES_MAX, description="최대 개수")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("beamtype.list", kind="query", risk="read", group="model.beam",
         summary="보 유형 일람표(이름·단면·단면 표기·쓰는 보 수)와 KS H형강 표준 단면 이름", args=BeamTypeList, idempotent=True, since="0.22",
         examples=[{}, {"limit": 5}])
def beamtype_list(ctx, a: BeamTypeList):
    ids = sorted(_types(ctx), key=lambda t: (ctx.st.el[t]["params"]["name"], t))
    after = C._dec_cursor(a.cursor, "pair")
    keys = [[ctx.st.el[t]["params"]["name"], t] for t in ids]
    start = 0 if after is None else next((k for k, n in enumerate(keys) if n > after), len(keys))
    rows = [_pub_type(ctx, t) for t in ids[start:start + a.limit]]
    items, cut = C._fit(rows, cursor_of=lambda k: C._enc_cursor(keys[start + k]))
    more = start + len(items) < len(ids)
    untyped = sum(1 for b in ctx.st.by_kind.get("beam", ()) if "btype" not in ctx.st.el[b]["params"])
    return {"items": items, "untyped_beams": untyped, "presets": list(BM.H_PRESETS),
            "page": C._page(C._enc_cursor(keys[start + len(items) - 1]) if more and items else None, len(ids), cut)}


# ---------------------------------------------------------------- 보
Anchor = Literal["top", "center", "bottom"]


class BeamCreate(A):
    id: Id | None = None
    level: Id = Field(description="놓는 레벨(기준선 높이 offset 의 기준)")
    type: Id = Field(description="보 유형(beamtype.create): 단면을 유형에서 받는다")
    start: Pt
    end: Pt
    offset: Num = Field(0.0, ge=-BM.OFFSET_MAX, le=BM.OFFSET_MAX, description="기준선 높이 mm(레벨 기준). anchor top 이면 보 윗면 = 레벨 + offset")
    anchor: Anchor = Field("top", description="기준선이 지나는 단면 자리(top 윗면·center 가운데·bottom 밑면)")
    roll: Num = Field(0.0, gt=-180.0, le=180.0, description="단면 회전(도, 기준선 둘레, 시작점에서 끝점을 볼 때 반시계 +)")
    cutback: bool = Field(True, description="true 면 끝점이 기둥 단면 안에 있을 때 보를 기둥 면까지 자른다(STR-03)")
    name: Name | None = None


def _beam_params(name, start, end, offset, anchor, roll, cutback, tid, sc) -> dict:
    p = {"name": name, "start": [float(start[0]), float(start[1])], "end": [float(end[0]), float(end[1])], "offset": float(offset),
         "anchor": anchor, "section": dict(sc)}
    if tid is not None:
        p["btype"] = tid
    if roll:
        p["roll"] = float(roll)
    if not cutback:
        p["cutback"] = False
    return p


@command("beam.create", kind="mutation", risk="write", group="model.beam",
         summary="구조 보를 두 점 사이에 놓는다(레벨·기준선 높이·z 맞춤·단면 회전·기둥 면 자르기). IFC 는 IfcBeam(단면 프로필 돌출) + IfcBeamType",
         args=BeamCreate, since="0.22",
         examples=[{"level": F.LV2, "type": F.BT1, "start": [0, 0], "end": [6000, 0]},
                   {"id": F.BM1, "level": F.LV2, "type": F.BT1, "start": [0, 4000], "end": [6000, 4000], "offset": -100, "anchor": "top"}])
def beam_create(ctx, a: BeamCreate):
    C._fresh_id(ctx, a, "beam")
    ctx.authored(a.level, "level", what="레벨에 보를 추가하는 것")
    tp = type_row(ctx, a.type, "/type")["params"]
    a.name = a.name or f"BM-{a.id[-6:]}"
    p = _check_beam(a.id, _beam_params(a.name, a.start, a.end, a.offset, a.anchor, a.roll, a.cutback, a.type, tp["section"]), "/start")
    ctx.put_el(a.id, row("beam", "IfcBeam", "beam.basic", p))
    ctx.rel("on_level", a.id, a.level, True)
    ctx.rel("of_type", a.id, a.type, True)
    return {"id": a.id}


class BeamSet(A):
    forbid_null = True
    id: Id
    level: Id | None = Field(None, description="다른 레벨로(기준선 높이 offset 은 그대로)")
    start: Pt | None = None
    end: Pt | None = None
    offset: Num | None = Field(None, ge=-BM.OFFSET_MAX, le=BM.OFFSET_MAX)
    anchor: Anchor | None = None
    roll: Num | None = Field(None, gt=-180.0, le=180.0)
    cutback: bool | None = None
    name: Name | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in ("level", "start", "end", "offset", "anchor", "roll", "cutback", "name")):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("beam.set", kind="mutation", risk="write", group="model.beam",
         summary="보를 고친다(두 점·레벨·기준선 높이·z 맞춤·단면 회전·기둥 면 자르기·이름). 단면은 유형이 정한다(beamtype.set·beam.type.set)",
         args=BeamSet, since="0.22", examples=[{"id": F.BM1, "offset": -150}, {"id": F.BM1, "end": [7000, 4000], "cutback": False}])
def beam_set(ctx, a: BeamSet):
    r = ctx.authored(a.id, "beam")
    p = r["params"]
    q = _beam_params(a.name if a.name is not None else p["name"], a.start if a.start is not None else p["start"], a.end if a.end is not None else p["end"],
                     a.offset if a.offset is not None else p["offset"], a.anchor if a.anchor is not None else p["anchor"],
                     a.roll if a.roll is not None else p.get("roll", 0.0), a.cutback if a.cutback is not None else p.get("cutback", True), p.get("btype"), p["section"])
    _write_beam(ctx, a.id, q, "/id")
    if a.level is not None:
        ctx.authored(a.level, "level", what="레벨에 보를 옮기는 것")
        old = ctx.rels_out("on_level", a.id)[0]
        if old != a.level:
            ctx.rel("on_level", a.id, old, False)
            ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id}


class BeamMove(A):
    id: Id
    dx: Num = 0.0
    dy: Num = 0.0
    dz: Num = Field(0.0, description="기준선 높이(offset)에 더한다")


@command("beam.move", kind="mutation", risk="write", group="model.beam", summary="보를 평행 이동한다(dx, dy, dz)",
         args=BeamMove, since="0.22", examples=[{"id": F.BM1, "dx": 0, "dy": 600}])
def beam_move(ctx, a: BeamMove):
    r = ctx.authored(a.id, "beam")
    p = r["params"]
    q = dict(p, start=[p["start"][0] + a.dx, p["start"][1] + a.dy], end=[p["end"][0] + a.dx, p["end"][1] + a.dy], offset=p["offset"] + a.dz)
    _write_beam(ctx, a.id, q, "/dx")
    return {"id": a.id}


BeamIdsArg = Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX, description="보 id 들")]


class BeamTypeAssign(A):
    ids: BeamIdsArg
    type: Id | None = Field(description="입힐 보 유형 id. null 이면 유형을 뗀다(단면은 남는다)")


@command("beam.type.set", kind="mutation", risk="write", group="model.beam",
         summary="보에 보 유형을 입히거나(단면을 유형 것으로) 뗀다",
         args=BeamTypeAssign, since="0.22", examples=[{"ids": [F.BM1], "type": F.BT1}])
def beam_type_set(ctx, a: BeamTypeAssign):
    if len(set(a.ids)) != len(a.ids):
        raise DobimError("CONSTRAINT", "ids 에 같은 id 가 두 번 있다", path="/ids")
    tp = type_row(ctx, a.type, "/type")["params"] if a.type is not None else None
    n = 0
    for i, bid in enumerate(a.ids):
        if ctx.st.el.get(bid, {}).get("kind") != "beam":
            raise DobimError("NOT_FOUND", f"보를 찾을 수 없다: {bid}", path=f"/ids/{i}")
        before = ctx.authored(bid, "beam", what="유형 지정")["params"]
        if tp is None:
            detach(ctx, bid)
        else:
            retype(ctx, bid, a.type, tp, f"/ids/{i}")
        n += ctx.st.el[bid]["params"] != before
    return {"count": len(a.ids), "changed": n}


# ---------------------------------------------------------------- 통심선을 따라 일괄 배치
class BeamGrid(A):
    level: Id = Field(description="놓는 레벨")
    type: Id = Field(description="보 유형")
    grids: Annotated[list[Id], Field(min_length=1, max_length=500)] | None = Field(None, description="보를 놓을 통심선(없으면 모든 직선 통심선)")
    between: Literal["columns", "intersections"] = Field("columns", description="columns = 두 끝 교점 모두에 기둥이 있는 칸만(상용 도구의 '그리드에' 배치), intersections = 교점 사이 모든 칸")
    offset: Num = Field(0.0, ge=-BM.OFFSET_MAX, le=BM.OFFSET_MAX, description="기준선 높이 mm(레벨 기준)")
    anchor: Anchor = "top"


def _isect(l1, l2):
    """두 선분의 교점 (t1: l1 위 0~길이 mm, 점) 또는 None(나란하거나 선분 밖, 허용 1mm)."""
    x1, y1, x2, y2 = l1
    x3, y3, x4, y4 = l2
    dx1, dy1, dx2, dy2 = x2 - x1, y2 - y1, x4 - x3, y4 - y3
    den = dx1 * dy2 - dy1 * dx2
    ln1 = (dx1 * dx1 + dy1 * dy1) ** 0.5
    ln2 = (dx2 * dx2 + dy2 * dy2) ** 0.5
    if abs(den) < 1e-9 * ln1 * ln2:
        return None
    t = ((x3 - x1) * dy2 - (y3 - y1) * dx2) / den
    s = ((x3 - x1) * dy1 - (y3 - y1) * dx1) / den
    if not (-1.0 / ln1 <= t <= 1.0 + 1.0 / ln1 and -1.0 / ln2 <= s <= 1.0 + 1.0 / ln2):
        return None
    return (t * ln1, (round(x1 + t * dx1, 6) + 0.0, round(y1 + t * dy1, 6) + 0.0))


def _has_column(cols, pt, z0, z1) -> bool:
    for _cid, c0, c1, bb, poly in cols:
        if min(z1, c1) - max(z0, c0) > 1.0 and bb[0] - 0.01 <= pt[0] <= bb[2] + 0.01 and bb[1] - 0.01 <= pt[1] <= bb[3] + 0.01 and K.SO.point_in(pt, list(poly)) >= 0:
            return True
    return False


@command("beam.grid", kind="mutation", risk="write", group="model.beam",
         summary="통심선을 따라 보를 한꺼번에 놓는다: 직선 통심선마다 다른 통심선과의 교점 사이 칸에 보 하나(기본은 두 교점에 기둥이 있는 칸만). 같은 자리의 보는 건너뛴다",
         args=BeamGrid, since="0.22", examples=[{"level": F.LV2, "type": F.BT1, "between": "intersections"}])
def beam_grid(ctx, a: BeamGrid):
    ctx.authored(a.level, "level", what="레벨에 보를 추가하는 것")
    tp = type_row(ctx, a.type, "/type")["params"]
    st = ctx.st
    allg = sorted(st.by_kind.get("grid", ()))
    lines = {gid: st.el[gid]["params"]["line"] for gid in allg if "arc" not in st.el[gid]["params"]}
    if a.grids is not None:
        if len(set(a.grids)) != len(a.grids):
            raise DobimError("CONSTRAINT", "grids 에 같은 id 가 두 번 있다", path="/grids")
        for i, gid in enumerate(a.grids):
            if st.el.get(gid, {}).get("kind") != "grid":
                raise DobimError("NOT_FOUND", f"통심선을 찾을 수 없다: {gid}", path=f"/grids/{i}")
        sel = sorted(a.grids)
    else:
        sel = allg
    skipped_arc = [gid for gid in sel if gid not in lines]
    if skipped_arc:
        ctx.warn(f"원호 통심선 {len(skipped_arc)}개는 건너뛴다(곡선 보는 다음 PR)")
    g = ctx.fresh()
    elev = g.q(Q.level_info, a.level)[1]
    probe = {"section": tp["section"], "anchor": a.anchor}
    b0, b1 = BM.b_range(probe)
    z0, z1 = elev + a.offset + b0, elev + a.offset + b1
    cols = g.q(Q.beam_columns) if a.between == "columns" else ()
    have = set()   # 이 레벨에 이미 있는 보의 두 끝(순서 무관)
    for b in sorted(ctx.rels_in("on_level", a.level)):
        r = st.el[b]
        if r["kind"] == "beam":
            s_, e_ = tuple(r["params"]["start"]), tuple(r["params"]["end"])
            have.add((min(s_, e_), max(s_, e_)))
    spans = []
    for gid in sel:
        if gid not in lines:
            continue
        ln = lines[gid]
        pts = sorted({x for o in allg if o != gid and o in lines and (x := _isect(ln, lines[o])) is not None})
        uniq = []
        for t, pt in pts:
            if not uniq or t - uniq[-1][0] > 1.0:
                uniq.append((t, pt))
        for (ta, pa), (tb, pb) in zip(uniq, uniq[1:]):
            if a.between == "columns" and not (_has_column(cols, pa, z0, z1) and _has_column(cols, pb, z0, z1)):
                continue
            key = (min(pa, pb), max(pa, pb))
            if key in have:
                continue
            have.add(key)
            spans.append((pa, pb))
    if len(spans) > GRID_BEAMS_MAX:
        raise DobimError("CONSTRAINT", f"보가 {len(spans)}개 생긴다(한 번에 {GRID_BEAMS_MAX}개까지)", path="/grids", hint="grids 로 통심선을 나눠 여러 번 실행하세요")
    made = []
    for pa, pb in spans:
        bid = ctx.new_id("beam")
        p = _check_beam(bid, _beam_params(f"BM-{bid[-6:]}", pa, pb, a.offset, a.anchor, 0.0, True, a.type, tp["section"]), "/grids")
        ctx.put_el(bid, row("beam", "IfcBeam", "beam.basic", p))
        ctx.rel("on_level", bid, a.level, True)
        ctx.rel("of_type", bid, a.type, True)
        made.append(bid)
    if not made:
        ctx.warn("놓을 칸이 없다(교점이 둘 이상인 직선 통심선이 없거나" + (", 두 교점 모두에 기둥이 있는 칸이 없거나" if a.between == "columns" else "") + " 같은 자리에 보가 이미 있다)")
    return {"count": len(made), "ids": made[:ANS_IDS]}


# ---------------------------------------------------------------- 목록
class BeamList(A):
    level: Id | None = Field(None, description="이 레벨의 보만")
    limit: int = Field(200, ge=1, le=5000, description="최대 개수")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("beam.list", kind="query", risk="read", group="model.beam",
         summary="보 목록(유형·단면 표기·두 점·기준선 높이·축 길이·기둥 면 자르기 결과·자른 길이·체적)", args=BeamList, idempotent=True, since="0.22",
         examples=[{}, {"level": F.LV2, "limit": 5}])
def beam_list(ctx, a: BeamList):
    g = ctx.g
    st = ctx.st
    ids = sorted(b for b in st.by_kind.get("beam", ()) if a.level is None or ctx.rels_out("on_level", b) == [a.level])
    after = C._dec_cursor(a.cursor, "name")
    start = 0 if after is None else next((k for k, b in enumerate(ids) if b > after), len(ids))
    rows = []
    for b in ids[start:start + a.limit]:
        p = st.el[b]["params"]
        item = {"id": b, "name": p["name"], "type": st.el[p["btype"]]["params"]["name"] if "btype" in p else None, "section": BM.section_label(p["section"]),
                "level": ctx.rels_out("on_level", b)[0], "start": p["start"], "end": p["end"], "offset": p["offset"], "anchor": p["anchor"]}
        if "roll" in p:
            item["roll"] = p["roll"]
        if st.el[b]["origin"] == "authored":
            ln, cl, _ar, vol = g.q(Q.beam_measure, b)
            c = g.q(Q.beam_cut, b)
            item.update(length=round(ln, 3), cut_length=round(cl, 3), volume_m3=round(vol / 1e9, 6))
            if c[2] is not None:
                item["start_cut"] = {"column": c[2], "length": round(c[0], 3)}
            if c[3] is not None:
                item["end_cut"] = {"column": c[3], "length": round(c[1], 3)}
        rows.append(item)
    items, cut = C._fit(rows, cursor_of=lambda k: C._enc_cursor(ids[start + k]))
    more = start + len(items) < len(ids)
    return {"items": items, "page": C._page(C._enc_cursor(ids[start + len(items) - 1]) if more and items else None, len(ids), cut)}
