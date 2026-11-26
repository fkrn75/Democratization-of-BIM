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

4부(W2 스물한 번째 PR): 원호 통심선 일괄 배치(beam.grid 가 원호 통심선을 호를 따라 곡선 보로 놓는다: 다른 통심선과의 교점 사이 칸마다 bulge 보 하나,
직선 통심선도 원호와의 교점에서 나눈다). 보 태그(annot.tag.create·all 의 대상 beam)와 반사 천장 평면의 보는 annot·drawing 에 있다.

5부(W2 스물네 번째 PR): 보 시스템 beamsys.create / set / move / ungroup / list(닫힌 경계 안에 방향·간격(고정 간격 또는 개수)·정렬로 평행한 작은 보를 채운다.
시스템 행이 자식 보(보통 보 행, params.bsys)를 소유하고, 경계·간격을 바꾸면 자식이 다시 만들어진다: 스택 벽의 부분 벽과 같은 짜임), 가새 brace.create / set
(두 점(높이 포함) 사이 경사 구조 부재: 보 행에 brace 표식을 둔 것이라 단면·접합·도면·태그·복사·대칭이 보와 같고 IFC 만 IfcMember(BRACE)다).

6부(W2 스물다섯 번째 PR): 트러스 truss.create / set / move / ungroup / list(두 점(높이 포함) 사이 연직면의 평행현 Pratt·Howe·Warren: 높이·칸 수·형식·현/웹 보 유형.
트러스 요소가 자식 부재(현 둘 = 보 행, 사재 = 가새 표식 보 행, 수직재 = 평면 길이 0 의 가새 표식 보 행 post_dir)를 소유하고 형식·높이·칸 수를 바꾸면 자식이 다시 만들어진다:
보 시스템과 같은 짜임), 보 시스템·트러스의 복사·회전·대칭(element.copy 계열이 요소를 옮기고 자식을 owner_regenerate 로 다시 만든다), 시스템·트러스 태그(annot).
"""
from __future__ import annotations

from math import atan2, degrees
from typing import Annotated, Literal

from pydantic import Field, model_validator

from dobim_core import arcgeom as AG
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


def systems_of(ctx, tid: str) -> list:
    """보 유형 tid 를 쓰는 보 시스템(W2 스물네 번째 PR)."""
    return sorted(s for s in ctx.st.by_kind.get("beamsys", ()) if ctx.st.el[s]["params"]["btype"] == tid)


def trusses_of(ctx, tid: str) -> list:
    """보 유형 tid 를 현 또는 웹에 쓰는 트러스(W2 스물다섯 번째 PR)."""
    return sorted(s for s in ctx.st.by_kind.get("truss", ()) if tid in (ctx.st.el[s]["params"]["btype"], ctx.st.el[s]["params"].get("wtype")))


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
    used = systems_of(ctx, a.id)
    if used:   # 보 시스템(W2 스물네 번째 PR)은 유형으로 자식을 다시 만든다: 떼어 낼 수 없다
        raise DobimError("CONSTRAINT", f"보 시스템 {len(used)}개가 이 유형을 쓴다", path="/id", hint="beamsys.set 으로 시스템의 유형을 바꾸거나 보 시스템을 지우세요",
                         details={"systems": used[:20]})
    used = trusses_of(ctx, a.id)
    if used:   # 트러스(W2 스물다섯 번째 PR)도 유형으로 자식을 다시 만든다
        raise DobimError("CONSTRAINT", f"트러스 {len(used)}개가 이 유형을 쓴다", path="/id", hint="truss.set 으로 트러스의 유형을 바꾸거나 트러스를 지우세요",
                         details={"trusses": used[:20]})
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
                 gap=0.0, brace=False, bsys=None, truss=None, post_dir=None) -> dict:
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
    if brace:   # 가새(W2 스물네 번째 PR): IFC 는 IfcMember(BRACE)
        p["brace"] = True
    if bsys is not None:   # 보 시스템의 자식(W2 스물네 번째 PR): 시스템이 정하는 파생 행
        p["bsys"] = bsys
    if truss is not None:   # 트러스의 자식 부재(W2 스물다섯 번째 PR): 트러스가 정하는 파생 행(수직 웹 부재는 post_dir 도 있다)
        p["truss"] = truss
    if post_dir is not None:
        p["post_dir"] = float(post_dir)
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
                     a.skew_cut if a.skew_cut is not None else p.get("skew_cut", False), a.gap if a.gap is not None else p.get("gap", 0.0),
                     p.get("brace", False), p.get("bsys"), p.get("truss"), p.get("post_dir"))   # 트러스를 푼 뒤 독립 부재가 된 수직 웹 부재도 이 값을 지킨다(W2 스물다섯 번째 PR)
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
    include_arcs: bool = Field(True, description="원호 통심선도 놓는다: 호를 따라 교점 사이 칸마다 곡선 보(bulge) 하나, 직선 통심선은 원호와의 교점에서도 나눈다. "
                                                 "false 면 이전처럼 원호 통심선을 건너뛰고 직선 통심선끼리만 나눈다")


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


GRID_TOL = 1.0   # 통심선 교점·칸 판정 허용(mm)


def _grid_geo(row):
    """통심선 행 -> ("line", (x1, y1, x2, y2)) 또는 ("arc", (x1, y1, x2, y2), arc_info)."""
    p = row["params"]
    if "arc" in p:
        return ("arc", tuple(p["line"]), AG.info(p["line"], p["arc"]))
    return ("line", tuple(p["line"]))


def _on_arc(ai, pt) -> float | None:
    """점 pt 가 원 위에 있다고 보고, 호(0 ~ 호 길이) 안(허용 GRID_TOL)이면 호를 따라 잰 거리 u, 아니면 None."""
    u = AG.u_of(ai, pt)
    return u if -GRID_TOL <= u <= AG.length(ai) + GRID_TOL else None


def _seg_t(ln, pt) -> float | None:
    """점 pt 가 (직선을 늘인 위의 점일 때) 선분 ln 안(허용 GRID_TOL)이면 시작에서 잰 거리 t, 아니면 None."""
    x1, y1, x2, y2 = ln
    dx, dy = x2 - x1, y2 - y1
    ln_ = (dx * dx + dy * dy) ** 0.5
    t = ((pt[0] - x1) * dx + (pt[1] - y1) * dy) / ln_
    return t if -GRID_TOL <= t <= ln_ + GRID_TOL else None


def _grid_hits(a, b) -> list:
    """통심선 a 위에서 통심선 b 와 만나는 점들 [(a 위 위치(직선은 시작에서 mm, 호는 호를 따라 mm), 점)]. 선분·호 밖의 교점은 뺀다(허용 1mm)."""
    out = []
    if a[0] == "line" and b[0] == "line":
        x = _isect(a[1], b[1])
        return [] if x is None else [x]
    if a[0] == "line":   # 직선과 호
        x1, y1, x2, y2 = a[1]
        dx, dy = x2 - x1, y2 - y1
        ln_ = (dx * dx + dy * dy) ** 0.5
        for hit in AG.line_hits(b[2], x1, y1, dx / ln_, dy / ln_):
            t = _seg_t(a[1], hit)
            if t is not None and _on_arc(b[2], hit) is not None:
                out.append((t, hit))
    elif b[0] == "line":   # 호와 직선
        x1, y1, x2, y2 = b[1]
        dx, dy = x2 - x1, y2 - y1
        ln_ = (dx * dx + dy * dy) ** 0.5
        for hit in AG.line_hits(a[2], x1, y1, dx / ln_, dy / ln_):
            u = _on_arc(a[2], hit)
            if u is not None and _seg_t(b[1], hit) is not None:
                out.append((u, hit))
    else:   # 호와 호
        for hit in AG.circle_hits(a[2], b[2]):
            u = _on_arc(a[2], hit)
            if u is not None and _on_arc(b[2], hit) is not None:
                out.append((u, hit))
    return [(round(t, 6) + 0.0, (round(pt[0], 6) + 0.0, round(pt[1], 6) + 0.0)) for t, pt in out]


def _arc_bulge(ai, ua, ub) -> float | None:
    """호의 두 위치(ua < ub, 호를 따라 mm)를 잇는 부분 호의 볼록도 sg x tan(중심각/4). 반원(중심각 > 180도)을 넘으면 None."""
    sw = (ub - ua) / ai[2]
    if sw > AG.PI + 1e-6:
        return None
    sn, cs = AG.sincos(sw / 4.0)
    return round(min(1.0, sn / cs) * ai[4], 9) + 0.0


def _span_key(s, e, bulge) -> tuple:
    """칸의 정규 키: 두 끝(순서 무관)과 볼록도(끝 순서를 바꾸면 부호가 바뀐다). 직선은 볼록도 0."""
    if s <= e:
        return (s, e, round(bulge, 6) + 0.0)
    return (e, s, round(-bulge, 6) + 0.0)


def _has_column(cols, pt, z0, z1) -> bool:
    for _cid, c0, c1, bb, poly in cols:
        if min(z1, c1) - max(z0, c0) > 1.0 and bb[0] - 0.01 <= pt[0] <= bb[2] + 0.01 and bb[1] - 0.01 <= pt[1] <= bb[3] + 0.01 and K.SO.point_in(pt, list(poly)) >= 0:
            return True
    return False


@command("beam.grid", kind="mutation", risk="write", group="model.beam",
         summary="통심선을 따라 보를 한꺼번에 놓는다: 통심선마다 다른 통심선과의 교점 사이 칸에 보 하나(원호 통심선은 호를 따라 곡선 보, 기본은 두 교점에 기둥이 있는 칸만). 같은 자리의 보는 건너뛴다",
         args=BeamGrid, since="0.22", examples=[{"level": F.LV2, "type": F.BT1, "between": "intersections"}])
def beam_grid(ctx, a: BeamGrid):
    ctx.authored(a.level, "level", what="레벨에 보를 추가하는 것")
    tp = type_row(ctx, a.type, "/type")["params"]
    st = ctx.st
    allg = sorted(st.by_kind.get("grid", ()))
    geo = {gid: _grid_geo(st.el[gid]) for gid in allg if a.include_arcs or "arc" not in st.el[gid]["params"]}   # 호를 놓지 않으면 원호 통심선은 교점 후보에서도 뺀다(이전과 같다)
    if a.grids is not None:
        if len(set(a.grids)) != len(a.grids):
            raise DobimError("CONSTRAINT", "grids 에 같은 id 가 두 번 있다", path="/grids")
        for i, gid in enumerate(a.grids):
            if st.el.get(gid, {}).get("kind") != "grid":
                raise DobimError("NOT_FOUND", f"통심선을 찾을 수 없다: {gid}", path=f"/grids/{i}")
        sel = sorted(a.grids)
    else:
        sel = allg
    skipped_arc = [gid for gid in sel if gid not in geo]
    if skipped_arc:
        ctx.warn(f"원호 통심선 {len(skipped_arc)}개는 건너뛴다(include_arcs 를 켜면 호를 따라 곡선 보로 놓는다)")
    g = ctx.fresh()
    elev = g.q(Q.level_info, a.level)[1]
    probe = {"section": tp["section"], "anchor": a.anchor}
    b0, b1 = BM.b_range(probe)
    z0, z1 = elev + a.offset + b0, elev + a.offset + b1
    cols = g.q(Q.beam_columns) if a.between == "columns" else ()
    have = set()   # 이 레벨에 이미 있는 보의 두 끝(순서 무관). 직선 칸은 곡선 보가 있어도 같은 두 끝이면 건너뛴다(이전과 같다)
    have_arc = set()   # 곡선 보: 두 끝과 볼록도(정규 키)
    for b in sorted(ctx.rels_in("on_level", a.level)):
        r = st.el[b]
        if r["kind"] == "beam":
            s_, e_ = tuple(r["params"]["start"]), tuple(r["params"]["end"])
            have.add((min(s_, e_), max(s_, e_)))
            if "bulge" in r["params"]:
                have_arc.add(_span_key(s_, e_, r["params"]["bulge"]))
    spans = []   # (시작, 끝, 볼록도 또는 None)
    bad_arc = []   # 곡선 보로 놓을 수 없는 칸의 이유
    for gid in sel:
        if gid not in geo:
            continue
        me = geo[gid]
        pts = sorted({x for o in allg if o != gid and o in geo for x in _grid_hits(me, geo[o])})
        uniq = []
        for t, pt in pts:
            if not uniq or t - uniq[-1][0] > 1.0:
                uniq.append((t, pt))
        for (ta, pa), (tb, pb) in zip(uniq, uniq[1:]):
            if a.between == "columns" and not (_has_column(cols, pa, z0, z1) and _has_column(cols, pb, z0, z1)):
                continue
            if me[0] == "arc":
                bu = _arc_bulge(me[2], ta, tb)
                if bu is None:
                    bad_arc.append("중심각이 180도를 넘는 호")
                    continue
                if bu == 0.0:
                    bad_arc.append("너무 짧은 호")
                    continue
                key = _span_key(pa, pb, bu)
                if key in have_arc:
                    continue
                why = K.row_params_error("beam", _beam_params("x", pa, pb, a.offset, a.anchor, 0.0, True, a.type, tp["section"], None, tp.get("material"), bu))
                if why:
                    bad_arc.append(why)
                    continue
                have_arc.add(key)
                spans.append((pa, pb, bu))
                continue
            key = (min(pa, pb), max(pa, pb))
            if key in have:
                continue
            have.add(key)
            spans.append((pa, pb, None))
    if bad_arc:
        ctx.warn(f"곡선 보로 놓을 수 없는 호 칸 {len(bad_arc)}개를 건너뛴다(첫 이유: {bad_arc[0]})")
    if len(spans) > GRID_BEAMS_MAX:
        raise DobimError("CONSTRAINT", f"보가 {len(spans)}개 생긴다(한 번에 {GRID_BEAMS_MAX}개까지)", path="/grids", hint="grids 로 통심선을 나눠 여러 번 실행하세요")
    made = []
    for pa, pb, bu in spans:
        bid = ctx.new_id("beam")
        p = _check_beam(bid, _beam_params(f"BM-{bid[-6:]}", pa, pb, a.offset, a.anchor, 0.0, True, a.type, tp["section"], None, tp.get("material"), bu), "/grids")
        ctx.put_el(bid, row("beam", "IfcBeam", "beam.basic", p))
        ctx.rel("on_level", bid, a.level, True)
        ctx.rel("of_type", bid, a.type, True)
        made.append(bid)
    if not made and not bad_arc:
        ctx.warn("놓을 칸이 없다(교점이 둘 이상인 통심선이 없거나" + (", 두 교점 모두에 기둥이 있는 칸이 없거나" if a.between == "columns" else "") + " 같은 자리에 보가 이미 있다)")
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
        for k in ("vertical_ends", "skew_cut", "gap", "brace", "bsys", "truss", "post_dir"):   # 가새 표식·보 시스템 자식(W2 스물네 번째 PR)·트러스 자식(W2 스물다섯 번째 PR)
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


# ---------------------------------------------------------------- 보 시스템(W2 스물네 번째 PR)
Spacing = Annotated[float, Field(ge=BM.SYS_SPACING_MIN, le=BM.SYS_SPACING_MAX, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=1, le=BM.SYS_COUNT_MAX)]
Angle = Annotated[float, Field(ge=0.0, lt=180.0, allow_inf_nan=False)]
SysBoundary = Annotated[list[Pt], Field(min_length=3, max_length=BM.SYS_BOUNDARY_MAX,
                                        description="경계 다각형 꼭짓점 [[x,y],...] mm(레벨 좌표, 3~256개). 시계 방향으로 주면 반시계로 저장한다. 자기 교차 금지")]
FromIds = Annotated[list[Id], Field(min_length=3, max_length=200, description="경계를 이루는 보·벽 id 들(축의 끝점끼리 이어져 하나의 닫힌 고리여야 한다)")]
ChildIds = Annotated[list[Id], Field(max_length=BM.SYS_COUNT_MAX)]
SysAlign = Literal["center", "start", "end"]


def _sys_boundary(ctx, boundary, from_elements) -> list:
    """직접 준 다각형 또는 보·벽 축이 이루는 닫힌 고리 -> 반시계 꼭짓점 목록(float 쌍). 안 되면 CONSTRAINT."""
    if (boundary is None) == (from_elements is None):
        raise DobimError("CONSTRAINT", "boundary 와 from_elements 중 정확히 하나를 준다", path="/boundary")
    if boundary is not None:
        pts = [[float(x), float(y)] for x, y in boundary]
    else:
        st = ctx.st
        if len(set(from_elements)) != len(from_elements):
            raise DobimError("CONSTRAINT", "from_elements 에 같은 id 가 두 번 있다", path="/from_elements")
        segs = []
        for i, eid in enumerate(from_elements):
            r = st.el.get(eid)
            if r is None or r["kind"] not in ("beam", "wall"):
                raise DobimError("NOT_FOUND", f"경계로 쓸 보·벽을 찾을 수 없다: {eid}", path=f"/from_elements/{i}")
            p = r["params"]
            if r["kind"] == "beam":
                segs.append(((p["start"][0], p["start"][1]), (p["end"][0], p["end"][1])))
                if "bulge" in p:
                    ctx.warn(f"곡선 보 {eid} 는 현(두 끝점)을 경계로 쓴다")
            else:
                segs.append(((p["axis"][0], p["axis"][1]), (p["axis"][2], p["axis"][3])))
                if "arc" in p:
                    ctx.warn(f"곡선 벽 {eid} 는 현(두 끝점)을 경계로 쓴다")
        loop = BM.loop_from_segments(segs)
        if loop is None:
            raise DobimError("CONSTRAINT", "고른 보·벽의 축이 하나의 닫힌 고리를 이루지 않는다(끝점이 1 mm 안에서 두 개씩 만나야 한다)", path="/from_elements",
                             hint="boundary 로 다각형을 직접 줄 수도 있다")
        pts = [[float(x), float(y)] for x, y in loop]
    why = K.polygon_problem(pts)
    if why:
        raise DobimError("CONSTRAINT", f"보 시스템 경계가 올바르지 않다: {why}", path="/boundary")
    return pts if K.polygon_area2(pts) > 0 else pts[::-1]


def _sys_params(name, boundary, angle, spacing, count, align, tid, offset, anchor, children) -> dict:
    p = {"name": name, "boundary": boundary, "angle": float(angle), "align": align, "btype": tid, "offset": float(offset), "anchor": anchor,
         "children": list(children)}
    if spacing is not None:
        p["spacing"] = float(spacing)
    else:
        p["count"] = int(count)
    why = K.row_params_error("beamsys", p)
    if why:
        raise DobimError("CONSTRAINT", f"보 시스템이 올바르지 않다: {why}", path="/boundary")
    return p


def _sys_child(sid: str, sp: dict, tp: dict, i: int, seg) -> dict:
    """보 시스템 sp 의 i 번째 자식 보 params(시스템이 정하는 파생 행: 기둥·주 보 끝 자르기는 보 규칙 그대로)."""
    q = _beam_params(f"{sp['name']}-{i + 1}", seg[0], seg[1], sp["offset"], sp["anchor"], 0.0, True, sp["btype"], tp["section"], None, tp.get("material"), bsys=sid)
    why = K.row_params_error("beam", q)
    if why:
        raise DobimError("CONSTRAINT", f"보 시스템의 작은 보 {i + 1} 이 올바르지 않다: {why}", path="/boundary")
    return q


def sys_regenerate(ctx, sid: str, sp: dict, level: str, new_ids: list | None = None) -> list:
    """보 시스템 sid 의 자식 보를 sp 에 맞춰 만들거나 고치거나 지운다. 자식 id 는 순서대로 이어받는다. 돌려주는 것: 자식 id 목록(보 순서)."""
    tp = type_row(ctx, sp["btype"], "/type")["params"]
    segs = BM.system_segments(sp)
    if len(segs) > BM.SYS_COUNT_MAX:
        raise DobimError("CONSTRAINT", f"작은 보가 {BM.SYS_COUNT_MAX}개를 넘는다(간격을 넓히거나 경계를 나누세요)", path="/spacing")
    if not segs:
        raise DobimError("CONSTRAINT", "경계 안에 놓을 작은 보가 하나도 없다(간격이 경계 폭보다 넓거나 경계가 너무 좁다)", path="/spacing")
    have = list(sp["children"])
    want_n = len(segs)
    kids = have[:want_n]
    pool = list(new_ids or ())
    while len(kids) < want_n:
        nid = pool.pop(0) if pool else ctx.new_id("beam")
        if nid in ctx.st.el or nid in kids or C._in_split_history(ctx, nid):
            raise DobimError("CONSTRAINT", f"이미 있는 id 다: {nid}", path="/child_ids")
        kids.append(nid)
    for b in have[want_n:]:   # 줄어든 자식: 지운다(자식 보를 대상으로 한 태그는 고아로 남는다: 조용히 지우지 않는 기존 규칙)
        if b in ctx.st.el:
            C._delete_one(ctx, b)
    for i, (b, seg) in enumerate(zip(kids, segs)):
        q = _sys_child(sid, sp, tp, i, seg)
        r = ctx.st.el.get(b)
        if r is None:
            ctx.put_el(b, row("beam", "IfcBeam", "beam.basic", q))
            ctx.rel("on_level", b, level, True)
            ctx.rel("of_type", b, sp["btype"], True)
            continue
        old = r["params"]
        if q != old:
            ctx.put_el(b, dict(r, params=q))
        if old.get("btype") != sp["btype"]:
            if old.get("btype") is not None:
                ctx.rel("of_type", b, old["btype"], False)
            ctx.rel("of_type", b, sp["btype"], True)
        cur = ctx.rels_out("on_level", b)
        if cur != [level]:
            for o in cur:
                ctx.rel("on_level", b, o, False)
            ctx.rel("on_level", b, level, True)
    return kids


class SysCommon(A):
    boundary: SysBoundary | None = None
    from_elements: FromIds | None = Field(None, description="경계를 보·벽 축으로 준다: 축의 끝점끼리 하나의 닫힌 고리를 이루는 보·벽 id(boundary 와 함께 줄 수 없다). "
                                          "그 순간의 축을 경계 다각형으로 옮겨 적는다(이후 그 보·벽을 옮겨도 따라가지 않는다)")
    angle: Angle | None = Field(None, description="작은 보가 놓이는 평면 방향(도, +x 에서 반시계, 0 이상 180 미만). 기본 0")
    spacing: Spacing | None = Field(None, description="고정 간격 mm(count 와 함께 줄 수 없다). 정렬 기준에서 이 간격마다 보를 놓는다")
    count: Count | None = Field(None, description="작은 보 개수(spacing 과 함께 줄 수 없다). 경계 폭을 개수 + 1 칸으로 같게 나눈 자리에 놓는다(정렬은 쓰지 않는다)")
    align: SysAlign | None = Field(None, description="고정 간격의 정렬: center 가운데에서 대칭(기본), start 방향에 수직인 왼쪽 가장자리에서, end 오른쪽 가장자리에서. 모서리 자리에는 보를 놓지 않는다")
    offset: Num | None = Field(None, ge=-BM.OFFSET_MAX, le=BM.OFFSET_MAX, description="기준선 높이 mm(레벨 기준). 기본 0")
    anchor: Anchor | None = Field(None, description="기준선이 지나는 단면 자리(top 기본)")
    name: Name | None = None


class BeamSysCreate(SysCommon):
    id: Id | None = None
    level: Id = Field(description="놓는 레벨(기준선 높이 offset 의 기준)")
    type: Id = Field(description="작은 보의 보 유형(beamtype.create)")
    child_ids: ChildIds | None = Field(None, description="자식 보 id(보 순서). 비우면 자동")

    @model_validator(mode="after")
    def _one(self):
        if (self.boundary is None) == (self.from_elements is None):
            raise ValueError("boundary 와 from_elements 중 정확히 하나를 준다")
        if (self.spacing is None) == (self.count is None):
            raise ValueError("spacing(고정 간격)과 count(개수) 중 정확히 하나를 준다")
        return self


@command("beamsys.create", kind="mutation", risk="write", group="model.beam",
         summary="보 시스템: 닫힌 경계(다각형 또는 보·벽 고리) 안에 방향·간격(고정 간격 또는 개수)·정렬로 평행한 작은 보를 채운다. 자식 보는 시스템이 소유하고 "
                 "(경계·간격을 바꾸면 다시 만들어진다) 끝은 경계 보 옆면에서 보끼리 접합 규칙으로 잘린다. IFC 는 IfcElementAssembly 아래 IfcBeam",
         args=BeamSysCreate, since="0.24",
         examples=[{"id": F.BS1, "level": F.LV2, "type": F.BT1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "angle": 0, "spacing": 1000, "align": "center"}])
def beamsys_create(ctx, a: BeamSysCreate):
    C._fresh_id(ctx, a, "beamsys")
    ctx.authored(a.level, "level", what="레벨에 보 시스템을 추가하는 것")
    type_row(ctx, a.type, "/type")
    if a.child_ids is not None and (len(set(a.child_ids)) != len(a.child_ids) or a.id in a.child_ids):
        raise DobimError("CONSTRAINT", "child_ids 는 서로 다른 id 로 준다", path="/child_ids")
    name = a.name or f"BS-{a.id[-6:]}"
    boundary = _sys_boundary(ctx, a.boundary, a.from_elements)
    sp = _sys_params(name, boundary, a.angle if a.angle is not None else 0.0, a.spacing, a.count, a.align or "center", a.type,
                     a.offset if a.offset is not None else 0.0, a.anchor or "top", [])
    kids = sys_regenerate(ctx, a.id, sp, a.level, a.child_ids)
    ctx.put_el(a.id, row("beamsys", "IfcElementAssembly", "beamsys.basic", dict(sp, children=kids)))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id, "count": len(kids), "children": kids[:ANS_IDS]}


class BeamSysSet(SysCommon):
    forbid_null = True
    id: Id
    level: Id | None = Field(None, description="다른 레벨로(기준선 높이 offset 은 그대로)")
    type: Id | None = Field(None, description="작은 보의 보 유형")
    child_ids: ChildIds | None = Field(None, description="작은 보가 늘 때 새 자식 보 id")

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in ("level", "type", "boundary", "from_elements", "angle", "spacing", "count", "align", "offset", "anchor", "name")):
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.boundary is not None and self.from_elements is not None:
            raise ValueError("boundary 와 from_elements 를 함께 줄 수 없다")
        if self.spacing is not None and self.count is not None:
            raise ValueError("spacing 과 count 를 함께 줄 수 없다")
        return self


@command("beamsys.set", kind="mutation", risk="write", group="model.beam",
         summary="보 시스템의 경계·방향·간격(고정 간격 또는 개수)·정렬·유형·높이·레벨·이름을 고친다(자식 보를 다시 만든다: id 는 순서대로 이어받고 남는 것은 지운다)",
         args=BeamSysSet, since="0.24", examples=[{"id": F.BS1, "spacing": 800, "align": "start"}, {"id": F.BS1, "count": 3, "angle": 90}])
def beamsys_set(ctx, a: BeamSysSet):
    r = ctx.authored(a.id, "beamsys")
    old = r["params"]
    lv = ctx.rels_out("on_level", a.id)[0]
    if a.level is not None:
        ctx.authored(a.level, "level", what="레벨에 보 시스템을 옮기는 것")
        lv = a.level
    if a.type is not None:
        type_row(ctx, a.type, "/type")
    boundary = _sys_boundary(ctx, a.boundary, a.from_elements) if (a.boundary is not None or a.from_elements is not None) else old["boundary"]
    spacing, count = old.get("spacing"), old.get("count")
    if a.spacing is not None:
        spacing, count = a.spacing, None
    elif a.count is not None:
        spacing, count = None, a.count
    if a.child_ids is not None and (len(set(a.child_ids)) != len(a.child_ids) or a.id in a.child_ids):
        raise DobimError("CONSTRAINT", "child_ids 는 서로 다른 id 로 준다", path="/child_ids")
    sp = _sys_params(a.name if a.name is not None else old["name"], boundary, a.angle if a.angle is not None else old["angle"], spacing, count,
                     a.align if a.align is not None else old["align"], a.type if a.type is not None else old["btype"],
                     a.offset if a.offset is not None else old["offset"], a.anchor if a.anchor is not None else old["anchor"], old["children"])
    kids = sys_regenerate(ctx, a.id, sp, lv, a.child_ids)
    new = dict(sp, children=kids)
    if new == old and lv == ctx.rels_out("on_level", a.id)[0]:
        return {"id": a.id, "changed": False, "count": len(kids)}
    ctx.put_el(a.id, dict(r, params=new))
    cur = ctx.rels_out("on_level", a.id)[0]
    if cur != lv:
        ctx.rel("on_level", a.id, cur, False)
        ctx.rel("on_level", a.id, lv, True)
    return {"id": a.id, "changed": True, "count": len(kids), "children": kids[:ANS_IDS]}


class BeamSysMove(A):
    id: Id
    dx: Num = 0.0
    dy: Num = 0.0
    dz: Num = Field(0.0, description="기준선 높이(offset)에 더한다")


@command("beamsys.move", kind="mutation", risk="write", group="model.beam", summary="보 시스템을 평행 이동한다(경계와 자식 보가 함께 간다)",
         args=BeamSysMove, since="0.24", examples=[{"id": F.BS1, "dx": 0, "dy": 500}])
def beamsys_move(ctx, a: BeamSysMove):
    r = ctx.authored(a.id, "beamsys")
    old = r["params"]
    lv = ctx.rels_out("on_level", a.id)[0]
    sp = dict(old, boundary=[[x + a.dx, y + a.dy] for x, y in old["boundary"]], offset=old["offset"] + a.dz)
    why = K.row_params_error("beamsys", sp)
    if why:
        raise DobimError("CONSTRAINT", f"보 시스템이 올바르지 않게 된다: {why}", path="/dx")
    kids = sys_regenerate(ctx, a.id, sp, lv)
    ctx.put_el(a.id, dict(r, params=dict(sp, children=kids)))
    return {"id": a.id, "count": len(kids)}


class BeamSysUngroup(A):
    id: Id


@command("beamsys.ungroup", kind="mutation", risk="write", group="model.beam",
         summary="보 시스템을 푼다: 자식 보는 보통 보로 남고(이후 보 명령으로 따로 고친다) 시스템 요소만 지운다",
         args=BeamSysUngroup, since="0.24", examples=[{"id": F.BS1}])
def beamsys_ungroup(ctx, a: BeamSysUngroup):
    r = ctx.authored(a.id, "beamsys")
    kids = [b for b in r["params"]["children"] if b in ctx.st.el]
    for b in kids:
        br = ctx.st.el[b]
        ctx.put_el(b, dict(br, params={k: v for k, v in br["params"].items() if k != "bsys"}))
    C._delete_one(ctx, a.id)
    return {"id": a.id, "released": kids[:ANS_IDS], "count": len(kids)}


class BeamSysList(A):
    level: Id | None = Field(None, description="이 레벨의 보 시스템만")
    limit: int = Field(200, ge=1, le=2000, description="최대 개수")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("beamsys.list", kind="query", risk="read", group="model.beam",
         summary="보 시스템 목록(경계·방향·간격 또는 개수·정렬·유형·자식 보 수·자식 보의 총 길이·체적)", args=BeamSysList, idempotent=True, since="0.24",
         examples=[{}, {"level": F.LV2, "limit": 5}])
def beamsys_list(ctx, a: BeamSysList):
    g = ctx.g
    st = ctx.st
    ids = sorted(s for s in st.by_kind.get("beamsys", ()) if a.level is None or ctx.rels_out("on_level", s) == [a.level])
    after = C._dec_cursor(a.cursor, "name")
    start = 0 if after is None else next((k for k, b in enumerate(ids) if b > after), len(ids))
    rows = []
    for s in ids[start:start + a.limit]:
        p = st.el[s]["params"]
        tr = st.el.get(p["btype"])
        item = {"id": s, "name": p["name"], "level": ctx.rels_out("on_level", s)[0], "type": tr["params"]["name"] if tr else None,
                "section": BM.section_label(tr["params"]["section"]) if tr else None, "boundary": p["boundary"], "angle": p["angle"], "align": p["align"],
                "offset": p["offset"], "anchor": p["anchor"], "beams": len(p["children"]), "children": p["children"][:ANS_IDS]}
        for k in ("spacing", "count"):
            if k in p:
                item[k] = p[k]
        ln = vol = 0.0
        for b in p["children"]:
            if b in st.el and st.el[b]["origin"] == "authored":
                m = g.q(Q.beam_measure, b)
                ln += m[1]
                vol += m[3]
        item.update(total_length=round(ln, 3), volume_m3=round(vol / 1e9, 6))
        rows.append(item)
    items, cut = C._fit(rows, cursor_of=lambda k: C._enc_cursor(ids[start + k]))
    more = start + len(items) < len(ids)
    return {"items": items, "page": C._page(C._enc_cursor(ids[start + len(items) - 1]) if more and items else None, len(ids), cut)}


# ---------------------------------------------------------------- 가새(brace, W2 스물네 번째 PR)
# 가새 = 두 점(높이 포함) 사이의 경사 구조 부재. 보 행에 brace 표식을 둔 것이다: 단면(보 유형)·기둥·보 면 끝 자르기·연직 끝면·접합 간격·평면(경사 보 규약)·
# 단면·입면 선과 가림·태그·일람표·복사·대칭이 보와 같은 코드를 쓴다. 다른 점은 IFC 뿐이다(IfcMember PredefinedType BRACE + IfcMemberType). 기울기는 80도 이하(그보다 서면 기둥).
Pt3 = Annotated[list[Coord], Field(min_length=3, max_length=3, description="[x, y, z] mm: x·y 는 레벨 좌표, z 는 레벨 기준 높이(기준선이 지나는 점)")]


class BraceCreate(A):
    id: Id | None = None
    level: Id = Field(description="놓는 레벨(두 점의 z 의 기준)")
    type: Id = Field(description="보 유형(beamtype.create): 가새의 단면을 유형에서 받는다")
    start: Pt3
    end: Pt3
    anchor: Anchor = Field("center", description="기준선이 지나는 단면 자리(가새는 단면 가운데가 기본)")
    roll: Num = Field(0.0, gt=-180.0, le=180.0, description="단면 회전(도, 기준선 둘레)")
    cutback: bool = Field(True, description="true 면 끝점이 기둥 단면 안이거나 다른 보의 평면 외곽 안에 있을 때 그 면까지 자른다")
    vertical_ends: bool = Field(False, description="끝면을 축에 수직이 아니라 연직면으로 자른다(기둥 면과 나란히). 두 점의 높이가 다를 때만")
    gap: Gap = Field(0.0, description="보끼리 접합 간격 mm")
    name: Name | None = None


def _brace_offsets(start, end):
    """두 점 [x, y, z] -> (평면 시작, 평면 끝, offset, end_offset 또는 None)."""
    return [start[0], start[1]], [end[0], end[1]], float(start[2]), (float(end[2]) if float(end[2]) != float(start[2]) else None)


@command("brace.create", kind="mutation", risk="write", group="model.beam",
         summary="가새(경사 구조 부재)를 두 점(높이 포함) 사이에 놓는다. 단면은 보 유형, 기둥·보 면 끝 자르기·연직 끝면·접합 간격은 보와 같다. IFC 는 IfcMember(BRACE) + IfcMemberType",
         args=BraceCreate, since="0.24",
         examples=[{"id": F.BR1, "level": F.LV1, "type": F.BT1, "start": [0, 0, 0], "end": [3000, 0, 3000]}])
def brace_create(ctx, a: BraceCreate):
    C._fresh_id(ctx, a, "beam")
    ctx.authored(a.level, "level", what="레벨에 가새를 추가하는 것")
    tp = type_row(ctx, a.type, "/type")["params"]
    a.name = a.name or f"BR-{a.id[-6:]}"
    s2, e2, off, eoff = _brace_offsets(a.start, a.end)
    p = _check_beam(a.id, _beam_params(a.name, s2, e2, off, a.anchor, a.roll, a.cutback, a.type, tp["section"], eoff, tp.get("material"), None, a.vertical_ends, False, a.gap,
                                       brace=True), "/start")
    ctx.put_el(a.id, row("beam", "IfcMember", "beam.basic", p))
    ctx.rel("on_level", a.id, a.level, True)
    ctx.rel("of_type", a.id, a.type, True)
    return {"id": a.id}


class BraceSet(A):
    forbid_null = True
    id: Id
    level: Id | None = Field(None, description="다른 레벨로(두 점의 z 는 그대로)")
    start: Pt3 | None = None
    end: Pt3 | None = None
    anchor: Anchor | None = None
    roll: Num | None = Field(None, gt=-180.0, le=180.0)
    cutback: bool | None = None
    vertical_ends: bool | None = None
    gap: Gap | None = None
    name: Name | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in ("level", "start", "end", "anchor", "roll", "cutback", "vertical_ends", "gap", "name")):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("brace.set", kind="mutation", risk="write", group="model.beam",
         summary="가새를 고친다(두 점(높이 포함)·레벨·z 맞춤·단면 회전·끝 자르기·연직 끝면·접합 간격·이름). 단면은 유형이 정한다(beam.type.set)",
         args=BraceSet, since="0.24", examples=[{"id": F.BR1, "end": [3000, 0, 2500]}, {"id": F.BR1, "vertical_ends": True}])
def brace_set(ctx, a: BraceSet):
    r = ctx.authored(a.id, "beam")
    p = r["params"]
    if not p.get("brace"):
        raise DobimError("CONSTRAINT", f"가새가 아닌 보다: {a.id}", path="/id", hint="보는 beam.set 으로 고치세요")
    s3 = list(a.start) if a.start is not None else [p["start"][0], p["start"][1], p["offset"]]
    e3 = list(a.end) if a.end is not None else [p["end"][0], p["end"][1], p.get("end_offset", p["offset"])]
    s2, e2, off, eoff = _brace_offsets(s3, e3)
    q = _beam_params(a.name if a.name is not None else p["name"], s2, e2, off, a.anchor if a.anchor is not None else p["anchor"], a.roll if a.roll is not None else p.get("roll", 0.0),
                     a.cutback if a.cutback is not None else p.get("cutback", True), p.get("btype"), p["section"], eoff, p.get("material"), None,
                     a.vertical_ends if a.vertical_ends is not None else p.get("vertical_ends", False), p.get("skew_cut", False),
                     a.gap if a.gap is not None else p.get("gap", 0.0), brace=True, post_dir=p.get("post_dir"))
    _write_beam(ctx, a.id, q, "/id")
    if a.level is not None:
        ctx.authored(a.level, "level", what="레벨에 가새를 옮기는 것")
        old = ctx.rels_out("on_level", a.id)[0]
        if old != a.level:
            ctx.rel("on_level", a.id, old, False)
            ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id}


# ---------------------------------------------------------------- 트러스(W2 스물다섯 번째 PR, STR-08)
# 트러스 = 두 점(높이 포함) 사이 연직면의 평행현 트러스. 트러스 요소가 자식 부재(현 2개 = IfcBeam 보 행, 웹 = 가새 표식 보 행)를 소유하고(params.truss), 형식·높이·칸 수·
# 점·유형을 바꾸면 자식이 다시 만들어진다(보 시스템·스택 벽과 같은 짜임: 자식 id 는 부재 순서대로 이어받는다). 수직재는 평면 길이가 0인 보 행(post_dir)이다.
TrussPattern = Literal["pratt", "howe", "warren"]
TrussPanels = Annotated[int, Field(ge=BM.TRUSS_PANELS_MIN, le=BM.TRUSS_PANELS_MAX)]
TrussHeight = Annotated[float, Field(ge=BM.TRUSS_HEIGHT_MIN, le=BM.TRUSS_HEIGHT_MAX, allow_inf_nan=False)]


def _truss_params(name, start3, end3, height, pattern, panels, tid, wtid, children) -> dict:
    s2, e2, off, eoff = _brace_offsets(start3, end3)
    p = {"name": name, "start": s2, "end": e2, "offset": off, "height": float(height), "pattern": pattern, "panels": int(panels), "btype": tid, "children": list(children)}
    if eoff is not None:   # 경사 트러스: 끝 높이가 같으면 수평(키 없음)
        p["end_offset"] = eoff
    if wtid is not None and wtid != tid:   # 웹 단면이 현과 같으면 따로 적지 않는다
        p["wtype"] = wtid
    why = K.row_params_error("truss", p)
    if why:
        raise DobimError("CONSTRAINT", f"트러스가 올바르지 않다: {why}", path="/start")
    return p


def _truss_types(ctx, tp: dict) -> dict:
    """트러스 tp 의 부재 종류별 (보 유형 id, 단면, 재료): chord = 현, web = 웹(수직재·사재). 웹 유형이 없으면 현과 같다."""
    ct = type_row(ctx, tp["btype"], "/type")["params"]
    wid = tp.get("wtype", tp["btype"])
    wt = type_row(ctx, wid, "/web_type")["params"]
    return {"chord": (tp["btype"], ct["section"], ct.get("material")), "web": (wid, wt["section"], wt.get("material"))}


def _truss_child(tid: str, tp: dict, types: dict, m) -> dict:
    """트러스 tid 의 부재 m = (역할, 이름 꼬리, 시작 (x, y, z), 끝)의 보 행 params(트러스가 정하는 파생 행). 기준선은 단면 가운데이고 접합 자르기는 끈다
    (부재 축이 마디에서 만나게 놓는다). 웹은 가새 표식(IFC IfcMember BRACE), 수직재는 post_dir(평면 길이 0)."""
    role, tail, a, b = m
    bt, sc, mat = types["chord" if role == "chord" else "web"]
    post = BM.truss_dir_deg(tp) if role == "post" else None
    q = _beam_params(f"{tp['name']}-{tail}", [a[0], a[1]], [b[0], b[1]], a[2], "center", 0.0, False, bt, sc, b[2], mat, None, False, False, 0.0,
                     brace=role != "chord", truss=tid, post_dir=post)
    why = K.row_params_error("beam", q)
    if why:
        raise DobimError("CONSTRAINT", f"트러스 부재 {tail} 가 올바르지 않다: {why}", path="/height")
    return q


def truss_regenerate(ctx, tid: str, tp: dict, level: str, new_ids: list | None = None) -> list:
    """트러스 tid 의 자식 부재를 tp 에 맞춰 만들거나 고치거나 지운다. 자식 id 는 부재 순서(현 둘, 수직재, 사재)대로 이어받고 늘면 새로 만들고 줄면 지운다.
    돌려주는 것: 자식 id 목록."""
    types = _truss_types(ctx, tp)
    mem = BM.truss_members(tp)
    have = list(tp["children"])
    kids = have[:len(mem)]
    pool = list(new_ids or ())
    while len(kids) < len(mem):
        nid = pool.pop(0) if pool else ctx.new_id("beam")
        if nid in ctx.st.el or nid in kids or C._in_split_history(ctx, nid):
            raise DobimError("CONSTRAINT", f"이미 있는 id 다: {nid}", path="/child_ids")
        kids.append(nid)
    for b in have[len(mem):]:   # 줄어든 자식: 지운다(자식을 대상으로 한 태그는 고아로 남는다: 조용히 지우지 않는 기존 규칙)
        if b in ctx.st.el:
            C._delete_one(ctx, b)
    for b, m in zip(kids, mem):
        q = _truss_child(tid, tp, types, m)
        bt = types["chord" if m[0] == "chord" else "web"][0]
        cls = "IfcBeam" if m[0] == "chord" else "IfcMember"
        r = ctx.st.el.get(b)
        if r is None:
            ctx.put_el(b, row("beam", cls, "beam.basic", q))
            ctx.rel("on_level", b, level, True)
            ctx.rel("of_type", b, bt, True)
            continue
        old = r["params"]
        if r["ifc_class"] != cls:   # 부재 순서가 역할을 정해 보통은 일어나지 않는다(현 둘이 앞, 웹이 뒤)
            raise DobimError("CONSTRAINT", f"트러스 자식 {b} 의 역할이 바뀌어 IFC 클래스가 달라진다", path="/child_ids")
        if q != old:
            ctx.put_el(b, dict(r, params=q))
        if old.get("btype") != bt:
            if old.get("btype") is not None:
                ctx.rel("of_type", b, old["btype"], False)
            ctx.rel("of_type", b, bt, True)
        cur = ctx.rels_out("on_level", b)
        if cur != [level]:
            for o in cur:
                ctx.rel("on_level", b, o, False)
            ctx.rel("on_level", b, level, True)
    return kids


class TrussCreate(A):
    id: Id | None = None
    level: Id = Field(description="놓는 레벨(두 점의 z 의 기준)")
    type: Id = Field(description="현(상현·하현)의 보 유형(beamtype.create): 단면을 유형에서 받는다")
    web_type: Id | None = Field(None, description="웹(수직재·사재)의 보 유형. 없으면 현과 같다")
    start: Pt3 = Field(description="[x, y, z] mm: 하현 기준선(단면 가운데)의 시작점. x·y 는 레벨 좌표, z 는 레벨 기준 높이")
    end: Pt3 = Field(description="[x, y, z] mm: 하현 기준선의 끝점(z 가 다르면 경사 트러스)")
    height: TrussHeight = Field(description="상현과 하현 기준선 사이의 수직 거리 mm")
    pattern: TrussPattern = Field("pratt", description="pratt = 수직재 + 사재가 가운데를 향해 내려감, howe = 수직재 + 사재가 가운데를 향해 올라감(둘 다 칸 수 짝수), "
                                                       "warren = 수직재 없이 사재만 지그재그")
    panels: TrussPanels = Field(4, description="칸 수(평면에서 같게 나눈다). Pratt·Howe 는 짝수")
    child_ids: ChildIds | None = Field(None, description="자식 부재 id(부재 순서: 하현, 상현, 수직재 V0..Vn, 사재 D1..Dn). 비우면 자동")
    name: Name | None = None


@command("truss.create", kind="mutation", risk="write", group="model.beam",
         summary="트러스(평행현 Pratt·Howe·Warren): 두 점(높이 포함) 사이 연직면에 상현·하현·수직재·사재를 만든다. 부재는 트러스가 소유하고(형식·높이·칸 수를 바꾸면 다시 만들어진다) "
                 "단면은 보 유형. IFC 는 IfcElementAssembly(TRUSS) 아래 현 IfcBeam·웹 IfcMember(BRACE)",
         args=TrussCreate, since="0.25",
         examples=[{"id": F.TR1, "level": F.LV2, "type": F.BT1, "start": [0, 0, 0], "end": [12000, 0, 0], "height": 2000, "pattern": "pratt", "panels": 4}])
def truss_create(ctx, a: TrussCreate):
    C._fresh_id(ctx, a, "truss")
    ctx.authored(a.level, "level", what="레벨에 트러스를 추가하는 것")
    type_row(ctx, a.type, "/type")
    if a.web_type is not None:
        type_row(ctx, a.web_type, "/web_type")
    if a.child_ids is not None and (len(set(a.child_ids)) != len(a.child_ids) or a.id in a.child_ids):
        raise DobimError("CONSTRAINT", "child_ids 는 서로 다른 id 로 준다", path="/child_ids")
    tp = _truss_params(a.name or f"TR-{a.id[-6:]}", a.start, a.end, a.height, a.pattern, a.panels, a.type, a.web_type, [])
    kids = truss_regenerate(ctx, a.id, tp, a.level, a.child_ids)
    ctx.put_el(a.id, row("truss", "IfcElementAssembly", "truss.basic", dict(tp, children=kids)))
    ctx.rel("on_level", a.id, a.level, True)
    return {"id": a.id, "count": len(kids), "children": kids[:ANS_IDS]}


class TrussSet(A):
    forbid_null = True
    id: Id
    level: Id | None = Field(None, description="다른 레벨로(두 점의 z 는 그대로)")
    type: Id | None = Field(None, description="현의 보 유형")
    web_type: Id | None = Field(None, description="웹의 보 유형(현과 같은 유형을 주면 따로 적지 않고 웹이 현 유형을 따른다)")
    start: Pt3 | None = None
    end: Pt3 | None = None
    height: TrussHeight | None = None
    pattern: TrussPattern | None = None
    panels: TrussPanels | None = None
    child_ids: ChildIds | None = Field(None, description="부재가 늘 때 새 자식 부재 id")
    name: Name | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in ("level", "type", "web_type", "start", "end", "height", "pattern", "panels", "name")):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


def _truss_pts(p: dict) -> tuple:
    return [p["start"][0], p["start"][1], p["offset"]], [p["end"][0], p["end"][1], p.get("end_offset", p["offset"])]


@command("truss.set", kind="mutation", risk="write", group="model.beam",
         summary="트러스의 두 점·높이·형식(pratt·howe·warren)·칸 수·현/웹 유형·레벨·이름을 고친다(자식 부재를 다시 만든다: id 는 부재 순서대로 이어받고 남는 것은 지운다)",
         args=TrussSet, since="0.25", examples=[{"id": F.TR1, "pattern": "warren", "panels": 5}, {"id": F.TR1, "height": 2400, "end": [12000, 0, 600]}])
def truss_set(ctx, a: TrussSet):
    r = ctx.authored(a.id, "truss")
    old = r["params"]
    lv = ctx.rels_out("on_level", a.id)[0]
    if a.level is not None:
        ctx.authored(a.level, "level", what="레벨에 트러스를 옮기는 것")
        lv = a.level
    for k, path in ((a.type, "/type"), (a.web_type, "/web_type")):
        if k is not None:
            type_row(ctx, k, path)
    if a.child_ids is not None and (len(set(a.child_ids)) != len(a.child_ids) or a.id in a.child_ids):
        raise DobimError("CONSTRAINT", "child_ids 는 서로 다른 id 로 준다", path="/child_ids")
    s3, e3 = _truss_pts(old)
    tid = a.type if a.type is not None else old["btype"]
    wtid = a.web_type if a.web_type is not None else old.get("wtype")
    tp = _truss_params(a.name if a.name is not None else old["name"], a.start if a.start is not None else s3, a.end if a.end is not None else e3,
                       a.height if a.height is not None else old["height"], a.pattern if a.pattern is not None else old["pattern"],
                       a.panels if a.panels is not None else old["panels"], tid, wtid, old["children"])
    kids = truss_regenerate(ctx, a.id, tp, lv, a.child_ids)
    new = dict(tp, children=kids)
    if new == old and lv == ctx.rels_out("on_level", a.id)[0]:
        return {"id": a.id, "changed": False, "count": len(kids)}
    ctx.put_el(a.id, dict(r, params=new))
    cur = ctx.rels_out("on_level", a.id)[0]
    if cur != lv:
        ctx.rel("on_level", a.id, cur, False)
        ctx.rel("on_level", a.id, lv, True)
    return {"id": a.id, "changed": True, "count": len(kids), "children": kids[:ANS_IDS]}


class TrussMove(A):
    id: Id
    dx: Num = 0.0
    dy: Num = 0.0
    dz: Num = Field(0.0, description="두 점의 높이(offset·end_offset)에 더한다")


@command("truss.move", kind="mutation", risk="write", group="model.beam", summary="트러스를 평행 이동한다(두 점과 자식 부재가 함께 간다)",
         args=TrussMove, since="0.25", examples=[{"id": F.TR1, "dx": 0, "dy": 500}])
def truss_move(ctx, a: TrussMove):
    r = ctx.authored(a.id, "truss")
    old = r["params"]
    lv = ctx.rels_out("on_level", a.id)[0]
    tp = dict(old, start=[old["start"][0] + a.dx, old["start"][1] + a.dy], end=[old["end"][0] + a.dx, old["end"][1] + a.dy], offset=old["offset"] + a.dz)
    if "end_offset" in old:
        tp["end_offset"] = old["end_offset"] + a.dz
    why = K.row_params_error("truss", tp)
    if why:
        raise DobimError("CONSTRAINT", f"트러스가 올바르지 않게 된다: {why}", path="/dx")
    kids = truss_regenerate(ctx, a.id, tp, lv)
    ctx.put_el(a.id, dict(r, params=dict(tp, children=kids)))
    return {"id": a.id, "count": len(kids)}


class TrussUngroup(A):
    id: Id


@command("truss.ungroup", kind="mutation", risk="write", group="model.beam",
         summary="트러스를 푼다: 자식 부재는 독립 부재로 남고(현 = 보, 사재 = 가새, 수직재 = 평면 길이 0 의 가새 표식 부재, 이후 보·가새 명령으로 따로 고친다) 트러스 요소만 지운다",
         args=TrussUngroup, since="0.25", examples=[{"id": F.TR1}])
def truss_ungroup(ctx, a: TrussUngroup):
    r = ctx.authored(a.id, "truss")
    kids = [b for b in r["params"]["children"] if b in ctx.st.el]
    for b in kids:
        br = ctx.st.el[b]
        ctx.put_el(b, dict(br, params={k: v for k, v in br["params"].items() if k != "truss"}))
    C._delete_one(ctx, a.id)
    return {"id": a.id, "released": kids[:ANS_IDS], "count": len(kids)}


class TrussList(A):
    level: Id | None = Field(None, description="이 레벨의 트러스만")
    limit: int = Field(200, ge=1, le=2000, description="최대 개수")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("truss.list", kind="query", risk="read", group="model.beam",
         summary="트러스 목록(두 점·높이·형식·칸 수·현/웹 유형·부재 수(현·수직재·사재)·자식 부재의 총 길이·체적)", args=TrussList, idempotent=True, since="0.25",
         examples=[{}, {"level": F.LV2, "limit": 5}])
def truss_list(ctx, a: TrussList):
    g = ctx.g
    st = ctx.st
    ids = sorted(s for s in st.by_kind.get("truss", ()) if a.level is None or ctx.rels_out("on_level", s) == [a.level])
    after = C._dec_cursor(a.cursor, "name")
    start = 0 if after is None else next((k for k, b in enumerate(ids) if b > after), len(ids))
    rows = []
    for s in ids[start:start + a.limit]:
        p = st.el[s]["params"]
        s3, e3 = _truss_pts(p)
        tr, wr = st.el.get(p["btype"]), st.el.get(p.get("wtype", p["btype"]))
        mem = BM.truss_members(p)
        item = {"id": s, "name": p["name"], "level": ctx.rels_out("on_level", s)[0], "start": s3, "end": e3, "height": p["height"], "pattern": p["pattern"], "panels": p["panels"],
                "type": tr["params"]["name"] if tr else None, "web_type": wr["params"]["name"] if wr else None,
                "section": BM.section_label(tr["params"]["section"]) if tr else None, "web_section": BM.section_label(wr["params"]["section"]) if wr else None,
                "members": {"chords": sum(1 for m in mem if m[0] == "chord"), "posts": sum(1 for m in mem if m[0] == "post"), "diagonals": sum(1 for m in mem if m[0] == "diag")},
                "beams": len(p["children"]), "children": p["children"][:ANS_IDS]}
        ln = vol = 0.0
        for b in p["children"]:
            if b in st.el and st.el[b]["origin"] == "authored":
                m = g.q(Q.beam_measure, b)
                ln += m[1]
                vol += m[3]
        item.update(total_length=round(ln, 3), volume_m3=round(vol / 1e9, 6))
        rows.append(item)
    items, cut = C._fit(rows, cursor_of=lambda k: C._enc_cursor(ids[start + k]))
    more = start + len(items) < len(ids)
    return {"items": items, "page": C._page(C._enc_cursor(ids[start + len(items) - 1]) if more and items else None, len(ids), cut)}


# ---------------------------------------------------------------- 자식 소유 요소(보 시스템·트러스)의 복사·회전·대칭(W2 스물다섯 번째 PR)
OWNER_KINDS = ("beamsys", "truss")


def owner_regenerate(ctx, oid: str, level: str) -> list:
    """보 시스템·트러스 oid 의 params(경계·점·방향 등)를 그대로 두고 자식을 그에 맞춰 다시 만들어 children 에 적는다(복사·회전·대칭이 쓴다)."""
    r = ctx.st.el[oid]
    p = r["params"]
    kids = sys_regenerate(ctx, oid, p, level) if r["kind"] == "beamsys" else truss_regenerate(ctx, oid, p, level)
    if kids != p["children"]:
        ctx.put_el(oid, dict(r, params=dict(p, children=kids)))
    return kids
