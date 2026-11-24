# SPDX-License-Identifier: MPL-2.0
"""모델 그룹(ARC-46·COL-11)·조립품 커맨드(W2 열한 번째 PR).

모델 그룹(Revit 의 모델 그룹과 같은 짜임: 그룹 정의 + 배치 여러 개, 배치마다 자기 부속 요소)
  mgroup.create   요소들을 묶어 정의(mgtype)와 첫 배치(mgroup)를 만든다. 벽을 고르면 거기 놓인 문·패밀리 인스턴스가 함께 든다
  mgroup.place    배치를 하나 더 놓는다(원본 배치의 부속을 배치 틀 at·angle·mirror 로 복사, 다른 레벨로도)
  mgroup.move / rotate / mirror   배치를 제자리에서 옮긴다(부속이 함께 간다. mirror keep 이면 대칭 복사 배치)
  mgroup.sync     한 배치에서 고친 부속을 같은 정의의 다른 배치에 반영한다(Revit 의 그룹 편집 "완료"와 같다: 부속 번호가 같은 요소를 배치 틀로 옮겨 다시 쓴다)
  mgroup.exclude  배치에서 부속을 뺀다(지운다). 그 번호는 동기화가 다시 만들지 않는다
  mgroup.add      배치에 요소를 더한다(새 부속 번호. 다른 배치는 동기화 때 받는다)
  mgroup.ungroup  배치를 푼다(부속은 보통 요소로 남는다). 마지막 배치면 정의도 지운다
  mgroup.list     정의·배치 목록
조립품(Revit 의 Assembly)
  assembly.create / set   요소 묶음을 이름·유형 이름 있는 조립품 하나로(IFC IfcElementAssembly + IfcRelAggregates). 한 요소는 조립품 하나에만
  assembly.list           조립품 단위 일람표: 부속 수·범주별 수·길이·면적·부피 합(유형별 합계)

배치 틀: 정의 좌표 p 의 세계 좌표 = R(angle)·S(mirror)·p + at (S = 정의 x 축 대칭). 두 배치 사이의 변환 = T_to ∘ T_from⁻¹ 이 이동·회전·대칭(xform.Xf) 하나다.
그룹·조립품은 비 IFC 의도 요소(정의는 DoBIM_Defs)다. 2D 상세 그룹(draft.group)과는 다른 것이다. 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

import copy as _copy
from typing import Annotated, Literal

from pydantic import Field, model_validator

from dobim_core import commands as C
from dobim_core import commands_edit as CE
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import schedules as S
from dobim_core import xform as X
from dobim_core.commands import A, Id, Name, Num, Point2, command, row
from dobim_core.errors import DobimError

IDS_MAX = K.GROUP_MEMBERS_MAX
ANS_IDS = 100


# ---------------------------------------------------------------- 공통 도우미
def _grow(ctx, gid: str, path: str = "/id") -> dict:
    r = ctx.st.el.get(gid)
    if r is None or r["kind"] != "mgroup":
        raise DobimError("NOT_FOUND", f"모델 그룹 배치를 찾을 수 없다: {gid}", path=path, hint="mgroup.list 로 배치를 보세요")
    ctx.authored(gid, what="모델 그룹")
    return r


def _instances(ctx, tid: str) -> list:
    return sorted(g for g in ctx.st.by_kind.get("mgroup", ()) if ctx.st.el[g]["params"]["type"] == tid)


def _group_of(ctx, eid: str):
    for g in sorted(ctx.st.by_kind.get("mgroup", ())):
        if eid in ctx.st.el[g]["params"]["members"]:
            return g
    return None


def _asm_of(ctx, eid: str):
    for a in sorted(ctx.st.by_kind.get("assembly", ())):
        if eid in ctx.st.el[a]["params"]["members"]:
            return a
    return None


def _frame(p: dict) -> tuple:
    return (float(p["at"][0]), float(p["at"][1])), float(p["angle"]), bool(p.get("mirror", False))


def between(src: tuple, dst: tuple) -> X.Xf:
    """배치 틀 src -> dst 로 옮기는 평면 변환(T_dst ∘ T_src⁻¹). 틀 = ((x, y), 각, 대칭)."""
    (fx, fy), tf, mf = src
    (tx, ty), tt, mt = dst
    if mf == mt:
        deg = (tt - tf) % 360.0
        if deg == 0.0:
            return X.move(tx - fx, ty - fy)
        sn, cs = K.sincos_deg(deg)
        a, b, c, d = cs, -sn, sn, cs
        return X.Xf(a, b, c, d, tx - (a * fx + b * fy), ty - (c * fx + d * fy), kind="rotate", deg=deg)
    two = (tt + tf) % 360.0   # R(tt)·S·R(-tf) = R(tt + tf)·S: 방향각 (tt + tf)/2 인 축에 대한 대칭
    s2, c2 = K.sincos_deg(two)
    a, b, c, d = c2, s2, s2, -c2
    return X.Xf(a, b, c, d, tx - (a * fx + b * fy), ty - (c * fx + d * fy), kind="mirror", phi=two / 2.0)


def _apply(xf: X.Xf, fr: tuple) -> tuple:
    """배치 틀 fr 에 평면 변환 xf 를 걸은 새 틀."""
    (x, y), ang, m = fr
    return tuple(float(v) for v in xf.pt(x, y)), xf.ang(ang) % 360.0, m != xf.mirror


def _frame_params(p: dict, fr: tuple) -> dict:
    (x, y), ang, m = fr
    out = dict(p, at=[x + 0.0, y + 0.0], angle=X._r6(ang) % 360.0)
    if m:
        out["mirror"] = True
    else:
        out.pop("mirror", None)
    return out


def _level_of(ctx, eid: str):
    lv = ctx.rels_out("on_level", eid)
    return lv[0] if lv else None


def _common_level(ctx, ids: list):
    """부속 중 처음으로 레벨에 놓인 요소의 레벨(배치의 레벨). 레벨에 놓인 부속이 없으면 None."""
    return next((lv for e in ids if (lv := _level_of(ctx, e)) is not None), None)


def _put_group(ctx, gid: str, r: dict, p: dict) -> None:
    why = K.row_params_error("mgroup", p)
    if why:
        raise DobimError("CONSTRAINT", f"모델 그룹 배치가 올바르지 않게 된다: {why}", path="/id")
    ctx.put_el(gid, dict(r, params=p))


def _members_ok(ctx, ids: list, what: str, kinds: tuple, *, path: str = "/ids") -> None:
    st = ctx.st
    for i, eid in enumerate(ids):
        r = st.el.get(eid)
        if r is None:
            raise DobimError("NOT_FOUND", f"요소를 찾을 수 없다: {eid}", path=f"{path}/{i}")
        if r["kind"] not in kinds:
            raise DobimError("CONSTRAINT", f"{r['kind']} 는 {what} 부속이 될 수 없다: {eid}", path=f"{path}/{i}", hint="·".join(kinds))
        ctx.authored(eid, what=what)
        if "stack" in r["params"]:
            raise DobimError("CONSTRAINT", f"스택 벽의 부분 벽은 {what}에 넣을 수 없다: {eid}", path=f"{path}/{i}")


def _mapped(ctx, s: str, t: str, xf: X.Xf, idmap: dict, lvl: tuple, warn_top: list) -> dict:
    """원본 부속 s 를 xf 로 옮긴 행(대상 id t 의 행). idmap = 원본 -> 대상 id(같은 번호의 부속), lvl = (원본 레벨, 대상 레벨)."""
    st = ctx.st
    r = st.el[s]
    kind, p = r["kind"], _copy.deepcopy(r["params"])
    if kind == "door" or (kind == "finst" and "offset" in p):   # 호스팅 요소: 대칭이면 축선이 뒤집혀 offset 이 바뀐다(element.copy 와 같은 규칙)
        if xf.mirror:
            host = ctx.rels_in("hosts", s)[0]
            n = X.wall_len(st.el[host]["params"])
            lo, hi = C._hosted_span(ctx, s)
            p["offset"] = X._r6(p["offset"] + n - lo - hi)
        if "also_cuts" in p:
            if any(w not in idmap for w in p["also_cuts"]):
                raise DobimError("CONSTRAINT", f"패밀리 인스턴스 {s} 가 함께 자르는 벽(also_cuts)이 그룹에 없다", path="/id")
            p["also_cuts"] = [idmap[w] for w in p["also_cuts"]]
    else:
        p = X.params(kind, p, xf)
    if kind == "railing" and "src" in p:   # 난간의 출처: 원본도 그룹에 있으면 그 짝을, 아니면 독립 난간이 된다
        if p["src"] in idmap:
            p["src"] = idmap[p["src"]]
        else:
            p = {k: v for k, v in p.items() if k not in C.RAIL_SRC}
    if kind == "wall" and "top_level" in p and lvl[0] != lvl[1] and _level_of(ctx, s) == lvl[0]:
        p.pop("top_level")   # 다른 층으로 옮기면 상단 구속을 떼고 높이를 값으로 둔다(element.copy 와 같다)
        p.pop("top_offset", None)
        warn_top.append(t)
    old = st.el.get(t)
    if "name" in p:
        p["name"] = old["params"]["name"] if old is not None and "name" in old["params"] else CE._renamed(p["name"], s, t)
    return dict(r, origin="authored", params=CE._check(kind, t, p))


def _write_members(ctx, pairs: list, xf: X.Xf, lvl: tuple) -> list:
    """pairs = [(원본 id, 대상 id)]: 대상 행을 원본을 xf 로 옮긴 값으로 쓰고(없으면 만든다) 관계·속성·재료를 원본과 같은 짜임으로 맞춘다.
    호스트(벽)가 호스팅 요소보다 먼저 온다. 그룹 밖 요소와의 조인·대상 레벨이 아닌 층은 대상 것을 둔다."""
    idmap = dict(pairs)
    warn_top: list = []
    for s, t in pairs:
        ctx.put_el(t, _mapped(ctx, s, t, xf, idmap, lvl, warn_top))
    for s, t in pairs:
        want_host = {idmap[h] for h in ctx.rels_in("hosts", s) if h in idmap}
        for h in ctx.rels_in("hosts", t):
            if h not in want_host:
                ctx.rel("hosts", h, t, False)
        for h in sorted(want_host):
            ctx.rel("hosts", h, t, True)
        for lv in ctx.rels_out("on_level", s):
            want = lvl[1] if lv == lvl[0] else lv
            for cur in ctx.rels_out("on_level", t):
                if cur != want:
                    ctx.rel("on_level", t, cur, False)
            ctx.rel("on_level", t, want, True)
        want_t = set(ctx.rels_out("of_type", s))
        for cur in ctx.rels_out("of_type", t):
            if cur not in want_t:
                ctx.rel("of_type", t, cur, False)
        for ty in sorted(want_t):
            ctx.rel("of_type", t, ty, True)
        inside = set(idmap.values())
        want_j = {idmap[o] for o in ctx.rels_out("joins", s) + ctx.rels_in("joins", s) if o in idmap}
        for o in ctx.rels_out("joins", t) + ctx.rels_in("joins", t):
            if o in inside and o not in want_j:
                ctx.rel("joins", min(t, o), max(t, o), False)
        for o in sorted(want_j):
            ctx.rel("joins", min(t, o), max(t, o), True)
        sp, tp = ctx.psets(s), ctx.psets(t)
        for pset in sorted(set(sp) | set(tp)):
            for prop in sorted(set(sp.get(pset, {})) | set(tp.get(pset, {}))):
                ctx.set_prop(t, pset, prop, sp.get(pset, {}).get(prop))
        ctx.set_material(t, ctx.material(s))
    return warn_top


def _sorted_members(ctx, p: dict) -> list:
    """(번호, 부속 id) 를 호스트 먼저(호스팅 요소는 뒤) 번호 순서로."""
    pairs = sorted(zip(p["keys"], p["members"]))
    return [x for x in pairs if not ctx.rels_in("hosts", x[1])] + [x for x in pairs if ctx.rels_in("hosts", x[1])]


# ---------------------------------------------------------------- 모델 그룹 만들기·배치
class GroupCreate(A):
    id: Id | None = Field(None, description="배치 id(비우면 자동)")
    type_id: Id | None = Field(None, description="정의 id(비우면 자동)")
    ids: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX)] = Field(description="묶을 요소. 벽에 놓인 문·패밀리 인스턴스는 함께 든다")
    name: Name | None = Field(None, description="정의(그룹 유형) 이름. 비우면 '모델 그룹 N'")
    origin: Point2 | None = Field(None, description="배치 기준점 [x,y] mm(배치·회전의 기준. 비우면 [0,0])")


@command("mgroup.create", kind="mutation", risk="write", group="model.group",
         summary="모델 그룹(ARC-46): 요소들을 묶어 그룹 정의와 첫 배치를 만든다. 배치를 더 놓고(mgroup.place) 한 배치를 고쳐 다른 배치에 반영(mgroup.sync)한다. IFC 는 배치마다 IfcGroup",
         args=GroupCreate, since="0.22", examples=[{"ids": [F.W1, F.W2], "name": "세대 A", "origin": [0, 0]}])
def mgroup_create(ctx, a: GroupCreate):
    sel = CE._pick(ctx, a.ids, "모델 그룹", copy=True)
    _members_ok(ctx, sel, "모델 그룹", K.GROUP_KINDS)
    for i, e in enumerate(sel):
        g = _group_of(ctx, e)
        if g is not None:
            raise DobimError("CONSTRAINT", f"이미 모델 그룹 배치 {g} 의 부속이다: {e}", path="/ids", hint="mgroup.exclude 나 mgroup.ungroup 으로 먼저 빼세요")
    names = {ctx.st.el[t]["params"]["name"] for t in ctx.st.by_kind.get("mgtype", ())}
    name = a.name
    if name is None:
        k = len(names) + 1
        while f"모델 그룹 {k}" in names:
            k += 1
        name = f"모델 그룹 {k}"
    elif name in names:
        raise DobimError("CONSTRAINT", f"같은 이름의 그룹 정의가 있다: {name}", path="/name", hint="mgroup.place 로 그 정의의 배치를 놓으세요")
    C._fresh_id(ctx, a, "mgroup")
    if a.type_id is None:
        a.type_id = ctx.new_id("mgtype")
    if a.type_id in ctx.st.el or a.type_id == a.id:
        raise DobimError("CONSTRAINT", f"이미 있는 id 다: {a.type_id}", path="/type_id")
    ctx.put_el(a.type_id, row("mgtype", "", "group.type", {"name": name}))
    p = {"name": name, "type": a.type_id, "members": list(sel), "keys": list(range(len(sel))),
         "at": [float(a.origin[0]), float(a.origin[1])] if a.origin else [0.0, 0.0], "angle": 0.0}
    lv = _common_level(ctx, sel)
    if lv is not None:
        p["level"] = lv
    _put_group(ctx, a.id, row("mgroup", "", "group.model", {}), p)
    return {"id": a.id, "type": a.type_id, "name": name, "count": len(sel), "members": sel[:ANS_IDS]}


def _place(ctx, src_id: str, fr: tuple, level, new_id, new_ids) -> tuple:
    """원본 배치의 지금 부속을 틀 fr 로 복사한 새 배치. (새 배치 id, 새 부속 id 들, 경고 벽)."""
    r = _grow(ctx, src_id)
    p = r["params"]
    pairs = _sorted_members(ctx, p)
    for _k, m in pairs:
        if m not in ctx.st.el:
            raise DobimError("CONSTRAINT", f"배치 {src_id} 의 부속 {m} 이 없다", path="/id")
        ctx.authored(m, what="모델 그룹 배치")
    src_lv = p.get("level")
    if level is not None:
        ctx.authored(level, "level", what="레벨로 그룹 배치")
        if src_lv is None:
            raise DobimError("CONSTRAINT", "레벨에 놓인 부속이 없는 그룹은 level 을 쓰지 않는다", path="/level")
        if level != src_lv and any((lv := _level_of(ctx, m)) is not None and lv != src_lv for _k, m in pairs):
            raise DobimError("CONSTRAINT", "다른 레벨로 놓을 그룹의 부속은 한 레벨에 있어야 한다", path="/level")
    dst_lv = level or src_lv
    if new_ids is not None:
        if len(new_ids) != len(pairs) or len(set(new_ids)) != len(new_ids):
            raise DobimError("CONSTRAINT", f"new_ids 가 {len(new_ids)}개인데 만들 부속은 {len(pairs)}개다", path="/new_ids", details={"expected": len(pairs)})
        for i, nid in enumerate(new_ids):
            if nid in ctx.st.el or C._in_split_history(ctx, nid):
                raise DobimError("CONSTRAINT", f"쓸 수 없는 id 다: {nid}", path=f"/new_ids/{i}")
    ids = list(new_ids) if new_ids is not None else [ctx.new_id(ctx.st.el[m]["kind"]) for _k, m in pairs]
    gid = new_id or ctx.new_id("mgroup")
    if gid in ctx.st.el or gid in ids:
        raise DobimError("CONSTRAINT", f"이미 있는 id 다: {gid}", path="/new_id")
    xf = between(_frame(p), fr)
    warn = _write_members(ctx, [(m, t) for (_k, m), t in zip(pairs, ids)], xf, (src_lv, dst_lv))
    q = {"name": p["name"], "type": p["type"], "members": ids, "keys": [k for k, _m in pairs]}
    q = _frame_params(q, fr)
    if dst_lv is not None:
        q["level"] = dst_lv
    if p.get("excluded"):
        q["excluded"] = list(p["excluded"])
    _put_group(ctx, gid, row("mgroup", "", "group.model", {}), q)
    return gid, ids, warn


class GroupPlace(A):
    id: Id = Field(description="원본 배치(그 지금 부속을 복사한다)")
    at: Point2 = Field(description="새 배치의 기준점 [x,y] mm")
    angle: Annotated[float, Field(ge=-360, le=360, allow_inf_nan=False)] = Field(0.0, description="새 배치의 회전(도, 반시계 +, 정의 기준)")
    mirror: bool = Field(False, description="true 면 정의를 x 축 대칭한 배치")
    level: Id | None = Field(None, description="이 레벨에 놓는다(비우면 원본 배치의 레벨)")
    new_id: Id | None = Field(None, description="새 배치 id(비우면 자동)")
    new_ids: Annotated[list[Id], Field(max_length=IDS_MAX)] | None = Field(None, description="새 부속 id(원본 배치의 부속 번호 순서, 호스트 먼저). 비우면 자동")


@command("mgroup.place", kind="mutation", risk="write", group="model.group",
         summary="모델 그룹 배치를 하나 더 놓는다: 원본 배치의 부속을 기준점·회전·대칭으로 복사(다른 레벨로도). 같은 정의라 mgroup.sync 가 함께 맞춘다",
         args=GroupPlace, since="0.22", examples=[{"id": F.MG1, "at": [0, 6000]}, {"id": F.MG1, "at": [12000, 0], "angle": 90, "level": F.LV2}])
def mgroup_place(ctx, a: GroupPlace):
    gid, ids, warn = _place(ctx, a.id, ((float(a.at[0]), float(a.at[1])), a.angle % 360.0, a.mirror), a.level, a.new_id, a.new_ids)
    a.new_id, a.new_ids = gid, ids   # 로그에 새 id 를 남겨 재생이 같은 id 를 만든다
    if warn:
        ctx.warn(f"다른 층으로 놓은 벽 {len(warn)}개는 상단 구속을 떼고 높이를 값으로 두었다")
    return {"id": gid, "count": len(ids), "members": ids[:ANS_IDS]}


# ---------------------------------------------------------------- 제자리 이동·회전·대칭
def _move_inplace(ctx, gid: str, xf: X.Xf) -> dict:
    r = _grow(ctx, gid)
    p = r["params"]
    ms = [m for _k, m in sorted(zip(p["keys"], p["members"])) if m in ctx.st.el]
    for m in ms:
        if any(h not in ms for h in ctx.rels_in("hosts", m)):
            raise DobimError("CONSTRAINT", f"그룹 부속 {m} 의 호스트 벽이 그룹 밖에 있다", path="/id")
    sel = CE._pick(ctx, [m for m in ms if not ctx.rels_in("hosts", m)], "모델 그룹 이동") if ms else []
    out = CE._transform(ctx, sel, xf) if sel else {"count": 0}
    _put_group(ctx, gid, r, _frame_params(p, _apply(xf, _frame(p))))
    return {"id": gid, **out}


class GroupMove(A):
    id: Id
    dx: Num
    dy: Num


@command("mgroup.move", kind="mutation", risk="write", group="model.group", summary="모델 그룹 배치를 부속과 함께 평행 이동",
         args=GroupMove, since="0.22", examples=[{"id": F.MG1, "dx": 0, "dy": -1000}])
def mgroup_move(ctx, a: GroupMove):
    if a.dx == 0 and a.dy == 0:
        raise DobimError("CONSTRAINT", "dx·dy 가 모두 0 이다", path="/dx")
    return _move_inplace(ctx, a.id, X.move(a.dx, a.dy))


class GroupRotate(A):
    id: Id
    center: Point2 = Field(description="회전 중심 [x,y] mm")
    angle: Annotated[float, Field(ge=-360, le=360, allow_inf_nan=False)] = Field(description="도, 반시계 +")


@command("mgroup.rotate", kind="mutation", risk="write", group="model.group", summary="모델 그룹 배치를 부속과 함께 점 중심으로 회전",
         args=GroupRotate, since="0.22", examples=[{"id": F.MG1, "center": [3000, 2000], "angle": 90}])
def mgroup_rotate(ctx, a: GroupRotate):
    if a.angle % 360.0 == 0:
        raise DobimError("CONSTRAINT", "회전각이 0(360의 배수)이다", path="/angle")
    return _move_inplace(ctx, a.id, X.rotate(a.center[0], a.center[1], a.angle))


class GroupMirror(A):
    id: Id
    axis: Annotated[list[Num], Field(min_length=4, max_length=4)] = Field(description="대칭축 위 두 점 [x1,y1,x2,y2] mm")
    keep: bool = Field(False, description="true 면 원본 배치를 두고 대칭 배치를 하나 더 놓는다(같은 정의)")
    new_id: Id | None = None
    new_ids: Annotated[list[Id], Field(max_length=IDS_MAX)] | None = None

    @model_validator(mode="after")
    def _ids(self):
        if (self.new_id is not None or self.new_ids is not None) and not self.keep:
            raise ValueError("new_id·new_ids 는 keep 과 함께 쓴다")
        return self


@command("mgroup.mirror", kind="mutation", risk="write", group="model.group", summary="모델 그룹 배치를 축에 대해 대칭(keep 이면 대칭 배치를 하나 더)",
         args=GroupMirror, since="0.22", examples=[{"id": F.MG1, "axis": [0, -1000, 6000, -1000]}, {"id": F.MG1, "axis": [7000, 0, 7000, 4000], "keep": True}])
def mgroup_mirror(ctx, a: GroupMirror):
    x1, y1, x2, y2 = a.axis
    if (x2 - x1) ** 2 + (y2 - y1) ** 2 < 1.0:
        raise DobimError("CONSTRAINT", "대칭축 두 점이 1mm 이상 떨어져야 한다", path="/axis")
    xf = X.mirror(x1, y1, x2, y2)
    if not a.keep:
        return _move_inplace(ctx, a.id, xf)
    p = _grow(ctx, a.id)["params"]
    gid, ids, _w = _place(ctx, a.id, _apply(xf, _frame(p)), None, a.new_id, a.new_ids)
    a.new_id, a.new_ids = gid, ids
    return {"id": gid, "count": len(ids), "members": ids[:ANS_IDS]}


# ---------------------------------------------------------------- 동기화·빼기·더하기·풀기
class GroupSync(A):
    id: Id = Field(description="고친 배치(이 배치의 부속을 같은 정의의 다른 배치에 반영한다)")
    new_ids: Annotated[list[Id], Field(max_length=IDS_MAX * 20)] | None = Field(None, description="다른 배치에 새로 만드는 부속 id(배치 id 순·번호 순). 비우면 자동")


@command("mgroup.sync", kind="mutation", risk="write", group="model.group",
         summary="한 배치에서 고친 부속(형상·속성·재료·유형·호스트·조인)을 같은 정의의 다른 배치에 반영한다(Revit 그룹 편집 완료). 다른 배치에서 뺀 번호는 건너뛰고, 없는 번호는 만든다",
         args=GroupSync, since="0.22", examples=[{"id": F.MG1}])
def mgroup_sync(ctx, a: GroupSync):
    r = _grow(ctx, a.id)
    p = r["params"]
    src = _sorted_members(ctx, p)
    for _k, m in src:
        if m not in ctx.st.el:
            raise DobimError("CONSTRAINT", f"배치 {a.id} 의 부속 {m} 이 없다", path="/id")
    given = list(a.new_ids) if a.new_ids is not None else None
    made, updated, targets = [], 0, []
    for gid in _instances(ctx, p["type"]):
        if gid == a.id:
            continue
        g = ctx.st.el[gid]
        q = g["params"]
        ctx.authored(gid, what="모델 그룹")
        have = dict(zip(q["keys"], q["members"]))
        skip = set(q.get("excluded", ()))
        pairs, new_keys = [], []
        for k, m in src:
            if k in skip:
                continue
            if k in have:
                if have[k] not in ctx.st.el or ctx.st.el[have[k]]["kind"] != ctx.st.el[m]["kind"]:
                    continue   # 밖에서 지워졌거나 종류가 다르다: 건드리지 않는다
                ctx.authored(have[k], what="모델 그룹 동기화")
                pairs.append((m, have[k]))
                updated += 1
            else:
                if given is not None:
                    if not given:
                        raise DobimError("CONSTRAINT", "new_ids 가 모자란다", path="/new_ids")
                    nid = given.pop(0)
                    if nid in ctx.st.el or C._in_split_history(ctx, nid):
                        raise DobimError("CONSTRAINT", f"쓸 수 없는 id 다: {nid}", path="/new_ids")
                else:
                    nid = ctx.new_id(ctx.st.el[m]["kind"])
                pairs.append((m, nid))
                new_keys.append((k, nid))
                made.append(nid)
        _write_members(ctx, pairs, between(_frame(p), _frame(q)), (p.get("level"), q.get("level", p.get("level"))))
        if new_keys:
            ks = list(q["keys"]) + [k for k, _n in new_keys]
            mem = list(q["members"]) + [n for _k, n in new_keys]
            order = sorted(range(len(ks)), key=lambda i: ks[i])
            _put_group(ctx, gid, g, dict(q, keys=[ks[i] for i in order], members=[mem[i] for i in order]))
        targets.append(gid)
    if given:
        raise DobimError("CONSTRAINT", f"new_ids 가 {len(given)}개 남는다", path="/new_ids")
    a.new_ids = made
    return {"id": a.id, "instances": targets[:ANS_IDS], "updated": updated, "created": made[:ANS_IDS]}


class GroupExclude(A):
    id: Id
    members: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX)] = Field(description="이 배치에서 뺄 부속(지운다). 벽을 빼면 거기 놓인 문도 빠진다")


@command("mgroup.exclude", kind="mutation", risk="write", group="model.group",
         summary="배치에서 부속을 뺀다(지운다). 뺀 번호는 동기화가 이 배치에 다시 만들지 않는다(Revit 의 그룹 부속 제외)",
         args=GroupExclude, since="0.22", examples=[{"id": F.MG1, "members": [F.W2]}])
def mgroup_exclude(ctx, a: GroupExclude):
    _grow(ctx, a.id)
    for i, m in enumerate(a.members):
        if m not in ctx.st.el[a.id]["params"]["members"]:
            raise DobimError("CONSTRAINT", f"이 배치의 부속이 아니다: {m}", path=f"/members/{i}")
    gone = []
    for m in a.members:
        if m in ctx.st.el:
            gone += C.element_delete(ctx, C.IdArg(id=m))["deleted"]   # 지우면 _delete_one 이 배치에서 빼고 번호를 excluded 에 적는다
    return {"id": a.id, "deleted": gone[:ANS_IDS], "excluded": ctx.st.el[a.id]["params"].get("excluded", [])}


class GroupAdd(A):
    id: Id
    ids: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX)] = Field(description="더할 요소(벽의 문·패밀리 인스턴스는 함께 든다)")


@command("mgroup.add", kind="mutation", risk="write", group="model.group",
         summary="배치에 요소를 더한다(새 부속 번호). 같은 정의의 다른 배치는 mgroup.sync 로 받는다",
         args=GroupAdd, since="0.22", examples=[{"id": F.MG1, "ids": [F.W3]}])
def mgroup_add(ctx, a: GroupAdd):
    r = _grow(ctx, a.id)
    p = r["params"]
    sel = [e for e in CE._pick(ctx, a.ids, "모델 그룹", copy=True) if e not in p["members"]]
    _members_ok(ctx, sel, "모델 그룹", K.GROUP_KINDS)
    for e in sel:
        g = _group_of(ctx, e)
        if g is not None:
            raise DobimError("CONSTRAINT", f"이미 모델 그룹 배치 {g} 의 부속이다: {e}", path="/ids")
    used = set(p["keys"]) | set(p.get("excluded", ()))
    for gid in _instances(ctx, p["type"]):   # 다른 배치가 쓴 번호도 피한다(그 배치의 부속과 겹치지 않게)
        q = ctx.st.el[gid]["params"]
        used |= set(q["keys"]) | set(q.get("excluded", ()))
    k0 = max(used) + 1 if used else 0
    keys = list(p["keys"]) + list(range(k0, k0 + len(sel)))
    if keys and max(keys) >= K.GROUP_KEY_MAX:
        raise DobimError("CONSTRAINT", "부속 번호가 상한을 넘는다", path="/ids")
    _put_group(ctx, a.id, r, dict(p, keys=keys, members=list(p["members"]) + sel))
    return {"id": a.id, "added": sel[:ANS_IDS], "count": len(keys)}


class GroupUngroup(A):
    id: Id


@command("mgroup.ungroup", kind="mutation", risk="write", group="model.group",
         summary="배치를 푼다: 부속은 보통 요소로 남는다. 정의의 마지막 배치면 정의도 지운다",
         args=GroupUngroup, since="0.22", examples=[{"id": F.MG1}])
def mgroup_ungroup(ctx, a: GroupUngroup):
    r = _grow(ctx, a.id)
    tid = r["params"]["type"]
    ctx.put_el(a.id, None)
    gone_type = False
    if tid in ctx.st.el and not _instances(ctx, tid):
        ctx.put_el(tid, None)
        gone_type = True
    return {"id": a.id, "released": r["params"]["members"][:ANS_IDS], "type_deleted": gone_type}


class GroupList(A):
    pass


@command("mgroup.list", kind="query", risk="read", group="model.group", summary="모델 그룹 정의와 배치(기준점·각·대칭·부속 수)",
         args=GroupList, idempotent=True, since="0.22", examples=[{}])
def mgroup_list(ctx, a: GroupList):
    st = ctx.st
    out = []
    for t in sorted(st.by_kind.get("mgtype", ())):
        insts = []
        for g in _instances(ctx, t):
            q = st.el[g]["params"]
            insts.append({"id": g, "at": q["at"], "angle": q["angle"], "mirror": bool(q.get("mirror", False)), "level": q.get("level"),
                          "members": len(q["members"]), "excluded": len(q.get("excluded", ()))})
        out.append({"type": t, "name": st.el[t]["params"]["name"], "instances": insts})
    return {"types": out}


# ---------------------------------------------------------------- 지울 때 묶음에서 빼기(element.delete 등 _delete_one 이 부른다)
def unlink(ctx, eid: str) -> None:
    """지운 요소를 모델 그룹 배치(번호는 excluded 로)·조립품 부속에서 뺀다. 그룹·조립품이 없으면 아무것도 하지 않는다(효과 없음)."""
    st = ctx.st
    for g in sorted(st.by_kind.get("mgroup", ())):
        r = st.el[g]
        p = r["params"]
        if eid in p["members"]:
            i = p["members"].index(eid)
            ex = sorted(set(p.get("excluded", ())) | {p["keys"][i]})
            ctx.put_el(g, dict(r, params=dict(p, members=p["members"][:i] + p["members"][i + 1:], keys=p["keys"][:i] + p["keys"][i + 1:], excluded=ex)))
    for asm in sorted(st.by_kind.get("assembly", ())):
        r = st.el[asm]
        p = r["params"]
        if eid in p["members"]:
            left = [m for m in p["members"] if m != eid]
            ctx.put_el(asm, dict(r, params=dict(p, members=left)) if left else None)   # 부속이 하나도 안 남으면 조립품도 없앤다


# ---------------------------------------------------------------- 조립품
class AsmCreate(A):
    id: Id | None = None
    ids: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX)] = Field(description="조립품 부속(벽·문·슬래브·계단·난간·지붕·솔리드·패밀리 인스턴스·커튼월·승강기)")
    name: Name | None = Field(None, description="조립품 이름(비우면 ASM-<id 끝 6자>)")
    type: Name | None = Field(None, description="조립품 유형 이름(같은 유형끼리 일람표에서 묶인다). 비우면 이름")


def _asm_members(ctx, ids: list, skip: str | None = None) -> None:
    if len(set(ids)) != len(ids):
        raise DobimError("CONSTRAINT", "같은 id 가 두 번 있다", path="/ids")
    _members_ok(ctx, ids, "조립품", K.ASSEMBLY_KINDS)
    for i, e in enumerate(ids):
        o = _asm_of(ctx, e)
        if o is not None and o != skip:
            raise DobimError("CONSTRAINT", f"이미 조립품 {o} 의 부속이다(한 요소는 조립품 하나에만): {e}", path=f"/ids/{i}")


@command("assembly.create", kind="mutation", risk="write", group="model.assembly",
         summary="조립품(COL-11): 요소 묶음을 이름·유형 있는 조립품 하나로. IFC 는 IfcElementAssembly + IfcRelAggregates, 일람표는 assembly.list",
         args=AsmCreate, since="0.22", examples=[{"ids": [F.W1, F.D1], "name": "현관 벽체", "type": "벽체 유닛"}])
def assembly_create(ctx, a: AsmCreate):
    _asm_members(ctx, a.ids)
    C._fresh_id(ctx, a, "assembly")
    a.name = a.name or f"ASM-{a.id[-6:]}"
    p = {"name": a.name, "type": a.type or a.name, "members": list(a.ids)}
    why = K.row_params_error("assembly", p)
    if why:
        raise DobimError("CONSTRAINT", f"조립품이 올바르지 않다: {why}", path="/ids")
    ctx.put_el(a.id, row("assembly", "", "assembly.basic", p))
    return {"id": a.id, "name": a.name, "type": p["type"], "count": len(a.ids)}


class AsmSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    type: Name | None = None
    add: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX)] | None = None
    remove: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX)] | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(v is None for v in (self.name, self.type, self.add, self.remove)):
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


@command("assembly.set", kind="mutation", risk="write", group="model.assembly", summary="조립품 이름·유형 바꾸기, 부속 더하기·빼기(부속이 하나도 안 남으면 거절)",
         args=AsmSet, since="0.22", examples=[{"id": F.AS1, "type": "벽체 유닛 B", "add": [F.W2]}])
def assembly_set(ctx, a: AsmSet):
    r = ctx.authored(a.id, "assembly", what="조립품")
    p = dict(r["params"])
    ms = list(p["members"])
    for i, e in enumerate(a.remove or ()):
        if e not in ms:
            raise DobimError("CONSTRAINT", f"이 조립품의 부속이 아니다: {e}", path=f"/remove/{i}")
        ms.remove(e)
    add = [e for e in (a.add or ()) if e not in ms]
    if add:
        _asm_members(ctx, add, skip=a.id)
    ms += add
    if not ms:
        raise DobimError("CONSTRAINT", "부속이 하나도 남지 않는다", path="/remove", hint="조립품을 없애려면 element.delete")
    if a.name is not None:
        p["name"] = a.name
    if a.type is not None:
        p["type"] = a.type
    p["members"] = ms
    why = K.row_params_error("assembly", p)
    if why:
        raise DobimError("CONSTRAINT", f"조립품이 올바르지 않게 된다: {why}", path="/id")
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "name": p["name"], "type": p["type"], "count": len(ms)}


SUM_FIELDS = ("length", "area", "volume")


class AsmList(A):
    group_by: Literal["assembly", "type"] = Field("assembly", description="assembly 조립품마다 한 행, type 유형별 합계(조립품 수 포함)")
    units: Literal["mm", "m"] = Field("m", description="길이·면적·부피 단위(m 이면 m·m²·m³)")


@command("assembly.list", kind="query", risk="read", group="model.assembly",
         summary="조립품 일람표: 조립품(또는 유형)마다 부속 수·범주별 수·길이·면적·부피 합(부속의 일람표 행을 더한다)",
         args=AsmList, idempotent=True, since="0.22", examples=[{}, {"group_by": "type", "units": "mm"}])
def assembly_list(ctx, a: AsmList):
    st, g = ctx.st, ctx.g
    rows = []
    for aid in sorted(st.by_kind.get("assembly", ())):
        p = st.el[aid]["params"]
        cats: dict = {}
        tot = {k: 0.0 for k in SUM_FIELDS}
        n = 0
        for m in p["members"]:
            if m not in st.el:
                continue
            n += 1
            er = S.convert_units(g.q(S.element_row, m), a.units, S.FIELD_UNIT)
            c = er.get("category") or st.el[m]["kind"]
            cats[c] = cats.get(c, 0) + 1
            for k in SUM_FIELDS:
                v = er.get(k)
                if isinstance(v, (int, float)):
                    tot[k] += v
        rows.append({"id": aid, "name": p["name"], "type": p["type"], "count": n, "categories": dict(sorted(cats.items())),
                     **{k: round(v, 6) for k, v in tot.items()}})
    if a.group_by == "type":
        by: dict = {}
        for r_ in rows:
            t = by.setdefault(r_["type"], {"type": r_["type"], "assemblies": 0, "count": 0, "categories": {}, **{k: 0.0 for k in SUM_FIELDS}})
            t["assemblies"] += 1
            t["count"] += r_["count"]
            for c, v in r_["categories"].items():
                t["categories"][c] = t["categories"].get(c, 0) + v
            for k in SUM_FIELDS:
                t[k] = round(t[k] + r_[k], 6)
        rows = [dict(v, categories=dict(sorted(v["categories"].items()))) for _k, v in sorted(by.items())]
    return {"rows": rows, "units": a.units}
