# SPDX-License-Identifier: MPL-2.0
"""구조 보·보 유형 커맨드(W2 열다섯 번째 PR, STR-02·STR-03·STR-07).

beamtype.create / set / duplicate / delete / list : 이름 붙은 보 유형 = 단면(사각: 폭·춤, H형강: 폭·춤·웨브·플랜지). preset 으로 KS H형강
                                                   표준 단면(beams.H_PRESETS)을 고를 수 있다. 벽·지붕·난간 유형과 같은 설계다: 보 행이 유형의 단면
                                                   사본을 갖고(params.btype·section + 관계 of_type), 유형을 고치면 그 유형의 보 행만 다시 쓴다
beam.create / set / move / type.set : 평면 두 점 사이의 수평 보(레벨·기준선 높이 offset·z 맞춤 anchor·단면 회전 roll·기둥 면 자르기 cutback)
beam.grid : 통심선을 따라 보를 한꺼번에 놓는다(통심선 교점 사이마다, 기본은 두 교점 모두에 기둥이 있는 칸만: 상용 도구의 '그리드에' 배치)
삭제는 element.delete, 복사·회전·대칭은 element.copy·rotate·mirror 가 한다(commands_edit). 형상은 beams.py 가 정하고 도면·IFC(IfcBeam 몸체 =
IfcRectangleProfileDef·IfcIShapeProfileDef 돌출, IfcBeamType + IfcRelDefinesByType)·일람표가 같은 값을 쓴다. 보가 없는 프로젝트는 바이트까지 그대로다.

2부(W2 열여덟 번째 PR): 경사 보(beam.create·set end_offset), 보끼리 접합(다른 보 평면 외곽 안에서 끝나는 보를 주 보 옆면까지 자른다: 파생값),
단면 추가(ㄷ형강 c·ㄱ형강 l·각형 강관 box·원형 강관 pipe, KS 표준 단면 preset), 보 유형 구조 재료(material: IFC4 IfcMaterialProfileSet + Usage).

3부(W2 열아홉 번째 PR): 곡선 보(beam.create·set bulge: 원호 기준선, 호를 따라 단면 스윕), 경사 보 끝면 연직 자르기(vertical_ends), 보끼리 접합 간격(gap)과
비스듬한 주 보 옆면에 맞춘 경사 자르기(skew_cut).
"""
from __future__ import annotations

from math import atan2, degrees
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
Preset = Literal[tuple(BM.PRESETS)]
Coord = Annotated[float, Field(ge=-BM.COORD_MAX, le=BM.COORD_MAX, allow_inf_nan=False)]
Pt = Annotated[list[Coord], Field(min_length=2, max_length=2, description="[x, y] mm(레벨 좌표)")]
Dim = Annotated[float, Field(gt=0, le=BM.SEC_DIM_MAX, allow_inf_nan=False)]
Bulge = Annotated[float, Field(ge=-BM.BULGE_MAX, le=BM.BULGE_MAX, allow_inf_nan=False)]
Gap = Annotated[float, Field(ge=0, le=BM.GAP_MAX, allow_inf_nan=False)]


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
    """보 bid 에 유형(tid, params tp)을 입힌다: 단면·구조 재료(W2 열여덟 번째 PR)를 유형 것으로. 유형 재료가 생기는 보에 단일 재료(material.set)가
    있으면 먼저 뗀다(구조 재료가 재료를 대신한다: 벽 층 구성과 같다. 떼는 것도 효과로 남아 되돌리기가 되살린다)."""
    r = ctx.st.el[bid]
    p = r["params"]
    q = dict(p, btype=tid, section=dict(tp["section"]))
    if "material" in tp:
        if "material" not in p and ctx.material(bid) is not None:
            ctx.set_material(bid, None)
        q["material"] = tp["material"]
    else:
        q.pop("material", None)
    _write_beam(ctx, bid, q, path)
    old = p.get("btype")
    if old != tid:
        if old is not None:
            ctx.rel("of_type", bid, old, False)
        ctx.rel("of_type", bid, tid, True)


def detach(ctx, bid: str) -> None:
    """보에서 유형을 뗀다(단면은 남는다). 유형의 구조 재료(W2 열여덟 번째 PR)는 유형의 것이라 함께 떨어진다(그 뒤 재료는 material.set 으로)."""
    r = ctx.st.el[bid]
    old = r["params"].get("btype")
    if old is None:
        return
    ctx.put_el(bid, dict(r, params={k: v for k, v in r["params"].items() if k not in ("btype", "material")}))
    ctx.rel("of_type", bid, old, False)


_DIM_KEYS = ("width", "depth", "web", "flange", "thickness")


def _section(base: dict | None, preset, shape, width, depth, web, flange, thickness=None) -> dict:
    """입력 -> 단면. preset 이 있으면 그 표준 단면, 아니면 base(없으면 사각 300x600) 위에 준 값만 바꾼다.
    원형 강관(pipe)은 width·depth 가 바깥 지름이라 하나만 주면 다른 하나도 같게 맞춘다."""
    vals = dict(zip(_DIM_KEYS, (width, depth, web, flange, thickness)))
    if preset is not None:
        if shape is not None or any(v is not None for v in vals.values()):
            raise DobimError("CONSTRAINT", "preset 과 shape·width·depth·web·flange·thickness 를 함께 줄 수 없다", path="/preset")
        return BM.preset_section(preset)
    sc = dict(base) if base else {"shape": "rect", "width": 300.0, "depth": 600.0}
    if shape is not None and shape != sc["shape"]:
        sc = {"shape": shape, "width": sc["width"], "depth": sc["depth"]}
        for k in BM.SHAPE_KEYS[shape][2:]:
            if vals[k] is None:
                raise DobimError("CONSTRAINT", f"{shape} 단면은 " + "·".join(BM.SHAPE_KEYS[shape][2:]) + " 를 준다", path=f"/{k}")
    for k, v in vals.items():
        if v is not None:
            if k not in BM.SHAPE_KEYS[sc["shape"]]:
                raise DobimError("CONSTRAINT", f"{k} 는 {sc['shape']} 단면에 없다(" + ", ".join(BM.SHAPE_KEYS[sc["shape"]]) + ")", path=f"/{k}")
            sc[k] = float(v)
    if sc["shape"] == "pipe" and (width is None) != (depth is None):   # 지름 하나만 주면 둘 다
        sc["width"] = sc["depth"] = float(width if width is not None else depth)
    why = BM.section_problem(sc)
    if why:
        raise DobimError("CONSTRAINT", f"단면이 올바르지 않다: {why}", path="/shape")
    return sc


def _pub_type(ctx, tid: str) -> dict:
    p = ctx.st.el[tid]["params"]
    out = {"id": tid, "name": p["name"], "section": dict(p["section"]), "label": BM.section_label(p["section"]), "beams": len(beams_of(ctx, tid))}
    if "material" in p:
        out["material"] = p["material"]
    return out


# ---------------------------------------------------------------- 보 유형
Shape = Literal["rect", "h", "c", "l", "box", "pipe"]
MatName = Annotated[str, Field(max_length=64)]


class _SecIn(A):
    preset: Preset | None = Field(None, description="KS 표준 단면 이름: H형강(H-춤x폭x웨브x플랜지)·ㄷ형강(C-)·등변 ㄱ형강(L-)·원형 강관(P-지름x두께)·각형 강관(B-춤x폭x두께). "
                                  "목록은 beamtype.list 의 presets")
    shape: Shape | None = Field(None, description="rect 사각(철근콘크리트 보), h H형강, c ㄷ형강, l ㄱ형강, box 각형 강관, pipe 원형 강관")
    width: Dim | None = Field(None, description="폭 mm(H·ㄷ형은 플랜지 폭 B, 원형 강관은 바깥 지름)")
    depth: Dim | None = Field(None, description="춤 mm(H·ㄷ형은 전체 높이 H, 원형 강관은 바깥 지름)")
    web: Dim | None = Field(None, description="H·ㄷ형 웨브 두께 t1 mm")
    flange: Dim | None = Field(None, description="H·ㄷ형 플랜지 두께 t2 mm")
    thickness: Dim | None = Field(None, description="ㄱ형강·강관 두께 t mm")


class BeamTypeCreate(_SecIn):
    id: Id | None = None
    name: Name = Field(description="유형 이름(프로젝트 안에서 유일)")
    material: Name | None = Field(None, description="구조 재료 이름(예: SS275, C24). 그 유형의 보 재료가 된다(IFC4 IfcMaterialProfileSet + Usage)")


@command("beamtype.create", kind="mutation", risk="write", group="model.beam",
         summary="보 유형(단면: 사각·H형강·ㄷ형강·ㄱ형강·각형/원형 강관, KS 표준 단면 preset, 선택 구조 재료)을 만든다. 보는 beam.create type 으로 놓는다",
         args=BeamTypeCreate, since="0.22",
         examples=[{"name": "G1 400x700", "shape": "rect", "width": 400, "depth": 700}, {"name": "SG1", "preset": "H-400x200x8x13"},
                   {"name": "SB1", "preset": "C-150x75x6.5x10", "material": "SS275"}])
def beamtype_create(ctx, a: BeamTypeCreate):
    if len(_types(ctx)) >= BM.BEAMTYPES_MAX:
        raise DobimError("CONSTRAINT", f"보 유형은 {BM.BEAMTYPES_MAX}개까지다", path="/name")
    _name_taken(ctx, a.name)
    sc = _section(None, a.preset, a.shape, a.width, a.depth, a.web, a.flange, a.thickness)
    C._fresh_id(ctx, a, "beamtype")
    if ctx.external_def(a.id):
        ctx.warn("가져온 파일에 이 id 의 보 유형 정의가 되살려지지 않은 채 남아 있다: 새 정의가 그 속성을 덮어쓴다")
    p = {"name": a.name, "section": sc}
    if a.material is not None:
        p["material"] = a.material
    ctx.put_el(a.id, row("beamtype", "", "beam.type", p))
    return {"id": a.id, "name": a.name, "label": BM.section_label(sc)}


class BeamTypeSet(_SecIn):
    forbid_null = True
    id: Id
    name: Name | None = None
    material: MatName | None = Field(None, description="구조 재료 이름. 빈 문자열이면 유형 재료를 뗀다")

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in ("name", "preset", "shape", "width", "depth", "web", "flange", "thickness", "material")):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("beamtype.set", kind="mutation", risk="write", group="model.beam",
         summary="보 유형을 고친다(이름·단면·구조 재료). 그 유형의 모든 보가 따라간다",
         args=BeamTypeSet, since="0.22", examples=[{"id": F.BT1, "depth": 800}, {"id": F.BT1, "material": "C24"}])
def beamtype_set(ctx, a: BeamTypeSet):
    r = type_row(ctx, a.id)
    old = r["params"]
    if a.name is not None:
        _name_taken(ctx, a.name, skip=a.id)
    p = {"name": a.name if a.name is not None else old["name"],
         "section": _section(old["section"], a.preset, a.shape, a.width, a.depth, a.web, a.flange, a.thickness)}
    mat = old.get("material") if a.material is None else (a.material or None)
    if mat is not None:
        if K.row_params_error("beamtype", dict(p, material=mat)):
            raise DobimError("CONSTRAINT", "material 이름이 올바르지 않다", path="/material")
        p["material"] = mat
    if p == old:
        return {"id": a.id, "changed": False, "beams": 0}
    ctx.put_el(a.id, dict(r, params=p))
    n = 0
    if p["section"] != old["section"] or p.get("material") != old.get("material"):   # 형상·재료 키가 바뀐 때만 보를 다시 쓴다(이름은 보 행에 없다)
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
    p = {"name": a.name, "section": dict(r["params"]["section"])}
    if "material" in r["params"]:
        p["material"] = r["params"]["material"]
    ctx.put_el(holder.id, row("beamtype", "", "beam.type", p))
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
         summary="보 유형 일람표(이름·단면·단면 표기·구조 재료·쓰는 보 수)와 KS 표준 단면 이름", args=BeamTypeList, idempotent=True, since="0.22",
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
    return {"items": items, "untyped_beams": untyped, "presets": list(BM.PRESETS),
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
    cutback: bool = Field(True, description="true 면 끝점이 기둥 단면 안이거나 다른 보(주 보)의 평면 외곽 안에 있을 때 보를 그 면까지 자른다(STR-03)")
    end_offset: Num | None = Field(None, ge=-BM.OFFSET_MAX, le=BM.OFFSET_MAX,
                                   description="끝점 기준선 높이 mm(레벨 기준). 주면 경사 보(시작 = offset, 끝 = end_offset), 없으면 수평 보")
    bulge: Bulge | None = Field(None, description="곡선 보(원호) 볼록도 tan(중심각/4): 양수 = 반시계(진행 방향 오른쪽으로 부푼다), 1 = 반원. start·end 는 호의 두 끝. "
                                "0 이거나 없으면 직선 보. 수평 보만(end_offset 과 함께 줄 수 없다)")
    vertical_ends: bool = Field(False, description="경사 보(end_offset)의 끝면을 축에 수직이 아니라 연직면으로 자른다(기둥 면과 나란히)")
    skew_cut: bool = Field(False, description="보끼리 접합에서 끝면을 주 보 옆면과 나란한 연직면으로 자른다(비스듬히 만나는 보의 경사 자르기). 직선 보만")
    gap: Gap = Field(0.0, description="보끼리 접합 간격 mm: 주 보 옆면에서 이만큼 떨어져 끝낸다(기둥 면 자르기에는 적용하지 않는다). 0 이면 붙인다")
    name: Name | None = None


def _beam_params(name, start, end, offset, anchor, roll, cutback, tid, sc, end_offset=None, material=None, bulge=None, vertical_ends=False, skew_cut=False,
                 gap=0.0) -> dict:
    p = {"name": name, "start": [float(start[0]), float(start[1])], "end": [float(end[0]), float(end[1])], "offset": float(offset),
         "anchor": anchor, "section": dict(sc)}
    if tid is not None:
        p["btype"] = tid
    if roll:
        p["roll"] = float(roll)
    if not cutback:
        p["cutback"] = False
    if end_offset is not None and float(end_offset) != float(offset):   # 경사 보(W2 열여덟 번째 PR): 끝 높이가 같으면 수평 보(키 없음)
        p["end_offset"] = float(end_offset)
    if material is not None:   # 보 유형 구조 재료의 사본(W2 열여덟 번째 PR)
        p["material"] = material
    if bulge:   # 곡선 보(W2 열아홉 번째 PR): 0 이면 직선 보(키 없음)
        p["bulge"] = float(bulge)
    if vertical_ends:
        p["vertical_ends"] = True
    if skew_cut:
        p["skew_cut"] = True
    if gap:
        p["gap"] = float(gap)
    return p


@command("beam.create", kind="mutation", risk="write", group="model.beam",
         summary="구조 보를 두 점 사이에 놓는다(레벨·기준선 높이·끝 높이(경사 보)·원호(bulge)·z 맞춤·단면 회전·기둥·보 면 자르기·접합 간격). IFC 는 IfcBeam(단면 프로필 돌출·곡선 보는 스윕) + IfcBeamType",
         args=BeamCreate, since="0.22",
         examples=[{"level": F.LV2, "type": F.BT1, "start": [0, 0], "end": [6000, 0]},
                   {"id": F.BM1, "level": F.LV2, "type": F.BT1, "start": [0, 4000], "end": [6000, 4000], "offset": -100, "anchor": "top"},
                   {"level": F.LV2, "type": F.BT1, "start": [0, 8000], "end": [6000, 8000], "offset": 0, "end_offset": 600},
                   {"level": F.LV2, "type": F.BT1, "start": [0, 12000], "end": [6000, 12000], "bulge": 0.4142135624},
                   {"level": F.LV2, "type": F.BT1, "start": [0, 16000], "end": [6000, 16000], "offset": 0, "end_offset": 600, "vertical_ends": True}])
def beam_create(ctx, a: BeamCreate):
    C._fresh_id(ctx, a, "beam")
    ctx.authored(a.level, "level", what="레벨에 보를 추가하는 것")
    tp = type_row(ctx, a.type, "/type")["params"]
    a.name = a.name or f"BM-{a.id[-6:]}"
    p = _check_beam(a.id, _beam_params(a.name, a.start, a.end, a.offset, a.anchor, a.roll, a.cutback, a.type, tp["section"], a.end_offset, tp.get("material"),
                                       a.bulge, a.vertical_ends, a.skew_cut, a.gap), "/start")
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
    end_offset: Num | None = Field(None, ge=-BM.OFFSET_MAX, le=BM.OFFSET_MAX,
                                   description="끝점 기준선 높이 mm(경사 보). offset 과 같은 값이면 수평 보가 된다. 생략하면 그대로(수평 보는 끝 높이 = offset 을 따른다)")
    bulge: Bulge | None = Field(None, description="곡선 보 볼록도(tan(중심각/4), 양수 = 반시계). 0 이면 직선 보로 되돌린다. 생략하면 그대로")
    vertical_ends: bool | None = Field(None, description="경사 보 끝면 연직 자르기(true/false). 생략하면 그대로")
    skew_cut: bool | None = Field(None, description="보끼리 접합의 경사 자르기(true/false). 생략하면 그대로")
    gap: Gap | None = Field(None, description="보끼리 접합 간격 mm. 0 이면 붙인다. 생략하면 그대로")
    name: Name | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in ("level", "start", "end", "offset", "anchor", "roll", "cutback", "end_offset", "bulge", "vertical_ends", "skew_cut", "gap",
                                                  "name")):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("beam.set", kind="mutation", risk="write", group="model.beam",
         summary="보를 고친다(두 점·레벨·기준선 높이·끝 높이(경사 보)·원호(bulge)·z 맞춤·단면 회전·기둥·보 면 자르기·접합 간격·이름). 단면은 유형이 정한다(beamtype.set·beam.type.set)",
         args=BeamSet, since="0.22", examples=[{"id": F.BM1, "offset": -150}, {"id": F.BM1, "end": [7000, 4000], "cutback": False}, {"id": F.BM1, "end_offset": 500},
                                               {"id": F.BM1, "gap": 50, "skew_cut": True},
                                               {"id": F.BM1, "end_offset": -150, "skew_cut": False, "bulge": 0.3}])   # 예시는 차례로 실행된다: 마지막이 곡선으로 되돌린다
def beam_set(ctx, a: BeamSet):
    r = ctx.authored(a.id, "beam")
    p = r["params"]
    # 경사 보의 끝 높이는 따로 고치지 않으면 그대로다(시작 높이 offset 만 바꿔도 끝은 남는다). 수평 보는 끝 = 시작이라 offset 을 따라간다
    eo = a.end_offset if a.end_offset is not None else p.get("end_offset")
    q = _beam_params(a.name if a.name is not None else p["name"], a.start if a.start is not None else p["start"], a.end if a.end is not None else p["end"],
                     a.offset if a.offset is not None else p["offset"], a.anchor if a.anchor is not None else p["anchor"],
                     a.roll if a.roll is not None else p.get("roll", 0.0), a.cutback if a.cutback is not None else p.get("cutback", True), p.get("btype"), p["section"],
                     eo, p.get("material"), a.bulge if a.bulge is not None else p.get("bulge"),
                     a.vertical_ends if a.vertical_ends is not None else p.get("vertical_ends", False),
                     a.skew_cut if a.skew_cut is not None else p.get("skew_cut", False), a.gap if a.gap is not None else p.get("gap", 0.0))
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
    if "end_offset" in p:   # 경사 보는 끝 높이도 함께 올린다
        q["end_offset"] = p["end_offset"] + a.dz
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
        ctx.warn(f"원호 통심선 {len(skipped_arc)}개는 건너뛴다(곡선 보는 beam.create bulge 로 놓는다: 원호 통심선 일괄 배치는 다음 PR)")
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
        p = _check_beam(bid, _beam_params(f"BM-{bid[-6:]}", pa, pb, a.offset, a.anchor, 0.0, True, a.type, tp["section"], None, tp.get("material")), "/grids")
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
        if "end_offset" in p:   # 경사 보(W2 열여덟 번째 PR): 끝 높이와 기울기(도)
            item["end_offset"] = p["end_offset"]
            kc, ks, _l3 = BM.slope(p)
            item["slope_deg"] = round(degrees(atan2(ks, kc)), 3)
        if "bulge" in p:   # 곡선 보(W2 열아홉 번째 PR): 볼록도·반지름·중심각(도). length 는 호 길이
            ai = BM.arc_info(p)
            item.update(bulge=p["bulge"], radius=round(ai[2], 3), sweep_deg=round(degrees(ai[5]), 3))
        for k in ("vertical_ends", "skew_cut", "gap"):
            if k in p:
                item[k] = p[k]
        if "material" in p:
            item["material"] = p["material"]
        if st.el[b]["origin"] == "authored":
            ln, cl, _ar, vol = g.q(Q.beam_measure, b)
            c = g.q(Q.beam_cut, b)
            item.update(length=round(ln, 3), cut_length=round(cl, 3), volume_m3=round(vol / 1e9, 6))
            for k, key in ((2, "start_cut"), (3, "end_cut")):   # 자른 상대: 기둥(column) 또는 주 보(beam, W2 열여덟 번째 PR)
                if c[k] is not None:
                    item[key] = {"beam" if st.el.get(c[k], {}).get("kind") == "beam" else "column": c[k], "length": round(c[k - 2], 3)}
        rows.append(item)
    items, cut = C._fit(rows, cursor_of=lambda k: C._enc_cursor(ids[start + k]))
    more = start + len(items) < len(ids)
    return {"items": items, "page": C._page(C._enc_cursor(ids[start + len(items) - 1]) if more and items else None, len(ids), cut)}
