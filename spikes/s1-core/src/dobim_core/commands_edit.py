# SPDX-License-Identifier: MPL-2.0
"""모델 편집 커맨드(W1 1-F): 레벨 고치기(ARC-37), 요소 복사·배열·회전·대칭·벽 간격띄우기·트림/연장(UI-03), 일괄 편집(UI-04).

level.set       레벨 이름·높이를 바꾼다. 높이를 바꾸면 상단이 레벨에 묶인 벽의 height 를 다시 계산한다.
element.copy    요소들을 (dx, dy) 만큼 count 번 복사한다(선형 배열). level 을 주면 그 레벨로 복사한다.
element.rotate  요소들을 점 center 중심으로 angle 도 회전한다.
element.mirror  요소들을 축 [x1,y1,x2,y2] 에 대해 대칭으로 옮긴다(keep 이면 원본을 두고 대칭 복사본을 만든다).
element.array_radial  점 중심 원형 배열 복사(2차). wall.offset 벽 간격띄우기, wall.extend_to·wall.corner 트림/연장(2차).
pset.set_many·material.set_many·wall.set_many  여러 요소(ids 또는 where 거르기)를 한 커맨드로 고친다(2차, 하나라도 실패하면 전체 거절).
벽을 고르면 거기 호스팅된 문·패밀리 인스턴스가 함께 간다. 계단·슬래브가 낸 난간은 원본을 따라간다(이동 커맨드와 같은 규칙).
형상 계산은 순수 모듈 xform 이 한다. 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

import copy as _copy
from typing import Annotated, Literal

from pydantic import Field, model_validator

from dobim_core import arcgeom as AG
from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import xform as X
from dobim_core.commands import (RAIL_SRC, RESERVED_PSET, A, Id, Name, Num, Pos, PropName, PropValue, PsetName, _apply_top_level, _follow_railings, _hosted_span, _in_split_history,
                                 _xcut_refs, command)
from dobim_core.errors import DobimError

# 편집할 수 있는 종류. 승강기(슬래브 구멍 소유)·대지·레벨·뷰·주석은 뺀다. 통심선은 기호가 유일해야 해서 복사는 안 된다
EDIT_KINDS = ("wall", "slab", "space", "stair", "railing", "roof", "solid", "finst", "grid", "door")
COPY_MAX = 500          # 한 번에 만드는 요소 수 상한
IDS_MAX = 200           # 고르는 요소 수 상한
ANS_IDS = 100           # 응답에 싣는 새 id 수 상한(응답 크기 상한)


# ================================================================ 레벨
class LevelSet(A):
    forbid_null = True
    id: Id
    name: Name | None = None
    elevation: Num | None = Field(None, description="mm. 위 요소는 레벨 기준이라 함께 움직인다")

    @model_validator(mode="after")
    def _any(self):
        if self.name is None and self.elevation is None:
            raise ValueError("바꿀 필드가 하나도 없다")
        return self


def _level_walls(ctx, lid: str) -> list:
    """높이를 바꾸면 height 를 다시 계산할 벽: 상단이 이 레벨에 묶였거나, 이 레벨에 놓이고 상단이 다른 레벨에 묶인 벽."""
    st = ctx.st
    on = set(ctx.rels_in("on_level", lid))
    return [w for w in sorted(st.by_kind.get("wall", ())) if "top_level" in st.el[w]["params"]
            and (st.el[w]["params"]["top_level"] == lid or w in on)]


@command("level.set", kind="mutation", risk="write", group="model.level", summary="레벨 이름·높이 변경(상단이 묶인 벽 높이 재계산)",
         args=LevelSet, since="0.11", examples=[{"id": F.LV2, "elevation": 3600}, {"id": F.LV2, "name": "2층"}])
def level_set(ctx, a: LevelSet):
    r = ctx.authored(a.id, "level", what="레벨")
    p = dict(r["params"])
    if a.name is not None and a.name != p["name"]:
        for lv in ctx.st.by_kind.get("level", ()):
            if lv != a.id and ctx.st.el[lv]["params"]["name"] == a.name:
                raise DobimError("CONSTRAINT", f"같은 이름의 레벨이 있다: {a.name}", path="/name")
        p["name"] = a.name
    walls = []
    if a.elevation is not None and float(a.elevation) != p["elevation"]:
        p["elevation"] = float(a.elevation)
        _level_guards(ctx, a.id)
        ctx.put_el(a.id, dict(r, params=p))
        for w in _level_walls(ctx, a.id):   # 상단 구속(24단계): 레벨 높이로 계산해 저장한 height 를 다시 맞춘다
            wr = ctx.authored(w, "wall", what="상단 구속 높이 재계산")
            wp = dict(wr["params"])
            try:
                _apply_top_level(ctx, wp, ctx.rels_out("on_level", w)[0])
            except DobimError as ex:
                raise DobimError("CONSTRAINT", f"벽 {w} 의 높이를 맞출 수 없다: {ex.message}", path="/elevation") from None
            if wp != wr["params"]:
                ctx.put_el(w, dict(wr, params=wp))
                walls.append(w)
        _elevator_guard(ctx, a.id)
    else:
        ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "elevation": p["elevation"], **({"walls": walls[:ANS_IDS]} if walls else {})}


def _level_guards(ctx, lid: str) -> None:
    """함께 자르는 벽(25단계)이 다른 레벨에 걸쳐 있으면 높이를 바꿀 수 없다(호스트 지역 상자가 레벨 높이 차로 계산된다)."""
    st = ctx.st
    for f in sorted(st.by_kind.get("finst", ())):
        also = st.el[f]["params"].get("also_cuts")
        if not also:
            continue
        hosts = ctx.rels_in("hosts", f)
        lv = {ctx.rels_out("on_level", w)[0] for w in [*hosts, *also] if w in st.el}
        if lid in lv and len(lv) > 1:
            raise DobimError("CONSTRAINT", f"패밀리 인스턴스 {f} 가 다른 레벨의 벽을 함께 자른다(also_cuts): 높이를 바꿀 수 없다", path="/elevation",
                             hint="family.set 으로 also_cuts 를 뗀 뒤 바꾸세요")


def _elevator_guard(ctx, lid: str) -> None:
    """승강장 문이 있는 승강기: 바뀐 높이에서도 정차층이 샤프트 높이 범위 안이어야 한다."""
    from dobim_core.commands import _check_doors
    st = ctx.st
    for e in sorted(st.by_kind.get("elevator", ())):
        p = st.el[e]["params"]
        base = ctx.rels_out("on_level", e)[0]
        if lid == base or lid in p.get("stops", ()):
            _check_doors(ctx, p, st.el[base]["params"]["elevation"])


# ================================================================ 고르기
def _pick(ctx, ids: list, what: str, *, copy: bool = False) -> list:
    """고른 요소 + 벽에 호스팅된 요소(뒤에 붙인다). 호스팅 요소만 고르면(호스트 없이) 거절한다."""
    if len(set(ids)) != len(ids):
        raise DobimError("CONSTRAINT", "ids 에 같은 id 가 두 번 있다", path="/ids")
    st = ctx.st
    for i, eid in enumerate(ids):
        r = st.el.get(eid)
        if r is None:
            raise DobimError("NOT_FOUND", f"요소를 찾을 수 없다: {eid}", path=f"/ids/{i}")
        if r["kind"] not in EDIT_KINDS or (copy and r["kind"] == "grid"):
            raise DobimError("CONSTRAINT", f"{r['kind']} 는 {what} 대상이 아니다: {eid}", path=f"/ids/{i}",
                             hint="벽·문·슬래브·공간·계단·난간·지붕·솔리드·패밀리 인스턴스" + ("" if copy else "·통심선"))
        ctx.authored(eid, what=what)
        host = ctx.rels_in("hosts", eid)
        if host and host[0] not in ids:
            raise DobimError("CONSTRAINT", f"호스팅된 요소는 호스트 벽과 함께 고른다: {eid}", path=f"/ids/{i}", hint=f"호스트 {host[0]} 를 ids 에 넣으세요")
    out = [e for e in ids if not ctx.rels_in("hosts", e)]
    for e in list(out):
        if st.el[e]["kind"] == "wall":
            for d in sorted(ctx.rels_out("hosts", e)):
                ctx.authored(d, what=what)
                out.append(d)
    return out


def _check(kind: str, eid: str, p: dict) -> dict:
    why = K.row_params_error(kind, p)
    if why:
        raise DobimError("CONSTRAINT", f"{kind} {eid} 가 올바르지 않게 된다: {why}", path="/ids")
    return p


def _guard_in_place(ctx, sel: list) -> None:
    """회전·대칭(제자리 변환): 함께 자르기·승강기 구멍처럼 고르지 않은 요소와 위치로 묶인 관계가 있으면 거절한다."""
    st = ctx.st
    s = set(sel)
    for e in sel:
        r = st.el[e]
        if r["kind"] == "finst" and any(w not in s for w in r["params"].get("also_cuts", ())):
            raise DobimError("CONSTRAINT", f"패밀리 인스턴스 {e} 가 함께 자르는 벽(also_cuts)도 골라야 한다", path="/ids")
        if r["kind"] == "wall" and any(f not in s for f in _xcut_refs(ctx, e)):
            raise DobimError("CONSTRAINT", f"벽 {e} 를 함께 자르는 패밀리 인스턴스(호스트 벽 포함)도 골라야 한다", path="/ids")
        if r["kind"] == "slab":
            for ev in sorted(st.by_kind.get("elevator", ())):
                if e in st.el[ev]["params"].get("hole_slabs", ()):
                    raise DobimError("CONSTRAINT", f"슬래브 {e} 에 승강기 {ev} 가 낸 구멍이 있다", path="/ids", hint="승강기를 따로 옮기거나 구멍을 정리하세요")


def _transform(ctx, sel: list, xf: X.Xf) -> dict:
    """제자리 변환(회전·대칭). 원본(계단·슬래브)이 낸 난간은 원본을 따라간다(고른 난간이 따라가지 못하면 직접 변환한다)."""
    st = ctx.st
    s = set(sel)
    _guard_in_place(ctx, sel)
    followed, lost = set(), []
    order = [e for e in sel if st.el[e]["kind"] != "railing"] + [e for e in sel if st.el[e]["kind"] == "railing"]
    for e in order:
        r = st.el[e]
        kind, p = r["kind"], r["params"]
        if kind == "door" or (kind == "finst" and "offset" in p):   # 호스팅 요소: 대칭이면 축선이 뒤집혀 offset 이 바뀐다
            if xf.mirror:
                host = ctx.rels_in("hosts", e)[0]
                n = X.wall_len(st.el[host]["params"])
                lo, hi = _hosted_span(ctx, e)
                ctx.put_el(e, dict(r, params=dict(p, offset=X._r6(p["offset"] + n - lo - hi))))
            continue
        if kind == "railing" and p.get("src") in s and e in followed:
            continue
        new = _check(kind, e, X.params(kind, p, xf))
        if kind == "wall" and xf.mirror:   # 독립 벽 개구(W2 세 번째 PR, ARC-34): 축이 뒤집히면 u -> L - u
            n = X.wall_len(p)
            C.move_wall_voids(ctx, e, lambda u: n - u)
        if kind in ("stair", "slab"):
            fr = _follow_railings(ctx, e, p, new)
            followed.update(fr.get("moved", ()))
            lost += [x["id"] for x in fr.get("lost", ())]
        ctx.put_el(e, dict(r, params=new))
    return {"count": len(sel), **({"railings_not_followed": lost[:ANS_IDS]} if lost else {})}


# ================================================================ 복사
def _new_ids(ctx, given, n: int) -> list:
    if given is None:
        return [None] * n
    if len(given) != n:
        raise DobimError("CONSTRAINT", f"new_ids 가 {len(given)}개인데 만들 요소는 {n}개다", path="/new_ids",
                         hint="new_ids 를 비우고 한 번 실행해 개수를 본 뒤 그 개수만큼 주세요", details={"expected": n})
    if len(set(given)) != n:
        raise DobimError("CONSTRAINT", "new_ids 에 같은 id 가 두 번 있다", path="/new_ids")
    for i, nid in enumerate(given):
        if nid in ctx.st.el or _in_split_history(ctx, nid):
            raise DobimError("CONSTRAINT", f"쓸 수 없는 id 다(이미 있거나 분할 이력에 쓰였다): {nid}", path=f"/new_ids/{i}")
    return list(given)


def _renamed(name: str, src: str, nid: str) -> str:
    """자동 이름(접두-원본 id 끝 6자)이면 새 id 로 바꾼다. 사용자가 준 이름은 그대로 둔다."""
    pre, _, tail = name.rpartition("-")
    return f"{pre}-{nid[-6:]}" if pre and tail == src[-6:] else name


def _copy_set(ctx, sel: list, xf: X.Xf, level, ids: list, warn_top: list) -> list:
    """sel 을 xf 로 옮긴 복사본 한 벌. ids = 새 id 목록(None 이면 자동). (원본 -> 복사본) 순서 목록."""
    st = ctx.st
    mp = {}
    for e, nid in zip(sel, ids):
        mp[e] = nid or ctx.new_id(st.el[e]["kind"])
    s = set(sel)
    for e in sel:
        r = st.el[e]
        kind, p = r["kind"], _copy.deepcopy(r["params"])
        nid = mp[e]
        hosted = kind == "door" or (kind == "finst" and "offset" in p)
        if hosted:
            if xf.mirror:
                host = ctx.rels_in("hosts", e)[0]
                n = X.wall_len(st.el[host]["params"])
                lo, hi = _hosted_span(ctx, e)
                p["offset"] = X._r6(p["offset"] + n - lo - hi)
            if "also_cuts" in p:
                if any(w not in s for w in p["also_cuts"]):
                    raise DobimError("CONSTRAINT", f"패밀리 인스턴스 {e} 가 함께 자르는 벽(also_cuts)도 골라야 한다", path="/ids")
                p["also_cuts"] = [mp[w] for w in p["also_cuts"]]
        else:
            p = X.params(kind, p, xf)
        if kind == "railing" and "src" in p:   # 난간의 출처: 원본도 복사하면 새 원본을 가리키고, 아니면 독립 난간이 된다
            if p["src"] in s:
                p["src"] = mp[p["src"]]
            else:
                p = {k: v for k, v in p.items() if k not in RAIL_SRC}
        if kind == "wall" and level is not None and "top_level" in p and ctx.rels_out("on_level", e)[0] != level:
            p.pop("top_level")   # 다른 층으로 복사하면 상단 구속을 떼고 높이를 값으로 둔다
            p.pop("top_offset", None)
            warn_top.append(nid)
        if "name" in p and kind != "grid":
            p["name"] = _renamed(p["name"], e, nid)
        ctx.put_el(nid, dict(r, origin="authored", params=_check(kind, nid, p)))
    for e in sel:   # 관계: 층·호스트·타입·조인(둘 다 복사한 벽끼리)
        nid = mp[e]
        for h in ctx.rels_in("hosts", e):
            ctx.rel("hosts", mp[h], nid, True)
        for lv in ctx.rels_out("on_level", e):
            ctx.rel("on_level", nid, level or lv, True)
        for t in ctx.rels_out("of_type", e):
            ctx.rel("of_type", nid, t, True)
        for o in ctx.rels_out("joins", e):
            if o in s:
                ctx.rel("joins", min(nid, mp[o]), max(nid, mp[o]), True)
        for pset, props in sorted(ctx.psets(e).items()):
            for prop in sorted(props):
                ctx.set_prop(nid, pset, prop, props[prop])
        ctx.set_material(nid, ctx.material(e))
    return [mp[e] for e in sel]


class ElementCopy(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX)] = Field(description="복사할 요소. 벽의 문·패밀리 인스턴스는 함께 간다")
    dx: Num = 0.0
    dy: Num = 0.0
    count: Annotated[int, Field(ge=1, le=50)] = Field(1, description="배열 개수. k번째 복사본은 k*(dx,dy)")
    level: Id | None = Field(None, description="이 레벨로 복사(count 1). 원본은 한 레벨에 있어야 한다")
    new_ids: Annotated[list[Id], Field(max_length=COPY_MAX)] | None = Field(None, description="새 id(복사본 순서). 비우면 자동")


@command("element.copy", kind="mutation", risk="write", group="model", summary="요소 복사·선형 배열·다른 층으로 복사",
         args=ElementCopy, since="0.11", examples=[{"ids": [F.W1], "dx": 0, "dy": -3000}, {"ids": [F.W2], "dx": 3000, "count": 2},
                                                    {"ids": [F.W1, F.W2], "level": F.LV2}])
def element_copy(ctx, a: ElementCopy):
    sel = _pick(ctx, a.ids, "복사", copy=True)
    if a.level is not None:
        if a.count != 1:
            raise DobimError("CONSTRAINT", "level 은 count 1 과만 쓴다", path="/count")
        ctx.authored(a.level, "level", what="레벨로 복사")
        src = {lv for e in sel for lv in ctx.rels_out("on_level", e)}
        if len(src) > 1:
            raise DobimError("CONSTRAINT", "다른 레벨로 복사할 요소는 한 레벨에 있어야 한다", path="/level")
    elif a.dx == 0 and a.dy == 0:
        raise DobimError("CONSTRAINT", "dx·dy 가 모두 0 이면 제자리에 겹친다", path="/dx", hint="level 을 주거나 이동량을 주세요")
    n = len(sel) * a.count
    if n > COPY_MAX:
        raise DobimError("CONSTRAINT", f"한 번에 {COPY_MAX}개까지 만든다(이번 {n}개)", path="/count")
    ids = _new_ids(ctx, a.new_ids, n)
    out, warn_top = [], []
    for k in range(1, a.count + 1):
        out += _copy_set(ctx, sel, X.move(a.dx * k, a.dy * k), a.level, ids[(k - 1) * len(sel):k * len(sel)], warn_top)
    a.new_ids = out   # 로그에 새 id 를 남겨 재생이 같은 id 를 만든다
    if warn_top:
        ctx.warn(f"다른 층으로 복사한 벽 {len(warn_top)}개는 상단 구속을 떼고 높이를 값으로 두었다")
    return {"count": len(out), "ids": out[:ANS_IDS], **({"truncated": True} if len(out) > ANS_IDS else {})}


# ================================================================ 회전·대칭
class ElementRotate(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX)] = Field(description="회전할 요소. 벽의 문·패밀리 인스턴스는 함께 돈다")
    center: Annotated[list[Num], Field(min_length=2, max_length=2)] = Field(description="회전 중심 [x,y] mm")
    angle: Annotated[float, Field(ge=-360, le=360, allow_inf_nan=False)] = Field(description="도, 반시계 +")


@command("element.rotate", kind="mutation", risk="write", group="model", summary="요소를 점 중심으로 회전",
         args=ElementRotate, since="0.11", examples=[{"ids": [F.W1, F.W2, F.W3], "center": [3000, 2000], "angle": 90}])
def element_rotate(ctx, a: ElementRotate):
    if a.angle % 360.0 == 0:
        raise DobimError("CONSTRAINT", "회전각이 0(360의 배수)이다", path="/angle")
    sel = _pick(ctx, a.ids, "회전")
    return _transform(ctx, sel, X.rotate(a.center[0], a.center[1], a.angle))


class ElementMirror(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX)] = Field(description="대칭할 요소. 벽의 문·패밀리 인스턴스는 함께 간다")
    axis: Annotated[list[Num], Field(min_length=4, max_length=4)] = Field(description="대칭축 위 두 점 [x1,y1,x2,y2] mm")
    keep: bool = Field(False, description="true 면 원본을 두고 대칭 복사본을 만든다")
    new_ids: Annotated[list[Id], Field(max_length=COPY_MAX)] | None = Field(None, description="keep 의 새 id. 비우면 자동")


@command("element.mirror", kind="mutation", risk="write", group="model", summary="요소를 축에 대해 대칭(keep 이면 복사)",
         args=ElementMirror, since="0.11", examples=[{"ids": [F.W1], "axis": [0, -1000, 6000, -1000]},
                                                      {"ids": [F.W2], "axis": [3000, 0, 3000, 4000], "keep": True}])
def element_mirror(ctx, a: ElementMirror):
    x1, y1, x2, y2 = a.axis
    if (x2 - x1) ** 2 + (y2 - y1) ** 2 < 1.0:
        raise DobimError("CONSTRAINT", "대칭축 두 점이 1mm 이상 떨어져야 한다", path="/axis")
    if a.new_ids is not None and not a.keep:
        raise DobimError("CONSTRAINT", "new_ids 는 keep 과 함께 쓴다", path="/new_ids")
    xf = X.mirror(x1, y1, x2, y2)
    if not a.keep:
        return _transform(ctx, _pick(ctx, a.ids, "대칭"), xf)
    sel = _pick(ctx, a.ids, "대칭 복사", copy=True)
    if len(sel) > COPY_MAX:
        raise DobimError("CONSTRAINT", f"한 번에 {COPY_MAX}개까지 만든다", path="/ids")
    out = _copy_set(ctx, sel, xf, None, _new_ids(ctx, a.new_ids, len(sel)), [])
    a.new_ids = out
    return {"count": len(out), "ids": out[:ANS_IDS], **({"truncated": True} if len(out) > ANS_IDS else {})}


# ================================================================ 원형 배열(W1 1-F 2차, UI-03)
class ElementArrayRadial(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX)] = Field(description="배열할 요소. 벽의 문·패밀리 인스턴스는 함께 간다")
    center: Annotated[list[Num], Field(min_length=2, max_length=2)] = Field(description="회전 중심 [x,y] mm")
    step: Annotated[float, Field(ge=-360, le=360, allow_inf_nan=False)] = Field(description="복사본 사이 각(도, 반시계 +). k번째 복사본은 k*step")
    count: Annotated[int, Field(ge=1, le=50)] = Field(description="복사본 개수(원본 제외)")
    new_ids: Annotated[list[Id], Field(max_length=COPY_MAX)] | None = Field(None, description="새 id(복사본 순서). 비우면 자동")


@command("element.array_radial", kind="mutation", risk="write", group="model", summary="요소를 점 중심으로 원형 배열 복사",
         args=ElementArrayRadial, since="0.12", examples=[{"ids": [F.W1], "center": [3000, 2000], "step": 90, "count": 3}])
def element_array_radial(ctx, a: ElementArrayRadial):
    for k in range(1, a.count + 1):
        if (a.step * k) % 360.0 == 0:
            raise DobimError("CONSTRAINT", f"{k}번째 복사본이 원본과 겹친다(각 {a.step * k:g}도)", path="/count")
    sel = _pick(ctx, a.ids, "원형 배열", copy=True)
    n = len(sel) * a.count
    if n > COPY_MAX:
        raise DobimError("CONSTRAINT", f"한 번에 {COPY_MAX}개까지 만든다(이번 {n}개)", path="/count")
    ids = _new_ids(ctx, a.new_ids, n)
    out = []
    for k in range(1, a.count + 1):
        xf = X.rotate(a.center[0], a.center[1], a.step * k)
        out += _copy_set(ctx, sel, xf, None, ids[(k - 1) * len(sel):k * len(sel)], [])
    a.new_ids = out   # 로그에 새 id 를 남겨 재생이 같은 id 를 만든다
    return {"count": len(out), "ids": out[:ANS_IDS], **({"truncated": True} if len(out) > ANS_IDS else {})}


# ================================================================ 벽 간격띄우기·트림/연장(W1 1-F 2차, UI-03)
def _walls(ctx, ids: list, what: str) -> None:
    """곡선 벽(W2 ARC-04)도 받는다: 간격띄우기는 동심 호, 트림/연장은 호를 따라 늘이거나 자른다."""
    if len(set(ids)) != len(ids):
        raise DobimError("CONSTRAINT", "ids 에 같은 id 가 두 번 있다", path="/ids")
    for i, w in enumerate(ids):
        r = ctx.st.el.get(w)
        if r is None or r["kind"] != "wall":
            raise DobimError("NOT_FOUND", f"벽을 찾을 수 없다: {w}", path=f"/ids/{i}")
        ctx.authored(w, "wall", what=what)


def _arc_offset(p: dict, d: float, path: str) -> tuple:
    """곡선 벽 params 를 축 왼쪽(+v)으로 d 만큼 띄운 동심 호 params 와 호 길이 비 k(= 새 반지름 / 반지름).
    지역 u 로 잰 값(모양 개구부 offset, 상단 윤곽·잘라내기 u)은 같은 각도에 남도록 k 배 한다."""
    ai = AG.info(p["axis"], p["arc"])
    L = AG.length(ai)
    r2 = ai[2] - ai[4] * d
    if r2 - p["thickness"] / 2.0 < AG.ARC_INNER_MIN:
        raise DobimError("CONSTRAINT", f"간격띄우기한 곡선 벽의 반지름({r2:.1f}mm)이 벽 두께의 반보다 작다", path=path,
                         hint="곡선 벽을 중심 쪽으로 띄울 때는 반지름보다 작게 띄우세요")
    k = r2 / ai[2]
    a0 = AG.point(ai, 0.0, d)
    a1 = AG.point(ai, L, d)
    q = dict(p, axis=[X._r6(a0[0]), X._r6(a0[1]), X._r6(a1[0]), X._r6(a1[1])], arc=[X._r6(v) for v in AG.point(ai, L / 2.0, d)])
    if "openings" in p:
        q["openings"] = [dict(o, offset=X._r6(o["offset"] * k)) for o in p["openings"]]
    if "top_profile" in p:
        q["top_profile"] = [[X._r6(u * k), z] for u, z in p["top_profile"]]
    if "cuts" in p:
        q["cuts"] = [[X._r6(cu[0] * k), X._r6(cu[1] * k), *cu[2:]] for cu in p["cuts"]]
    return q, k


def _arc_offset_apply(ctx, w: str, q: dict, k: float, path: str) -> None:
    """곡선 벽 w 를 동심 호 params q 로 바꾸고, 호스팅된 문·패밀리 인스턴스의 offset 을 k 배(같은 각도)로 옮긴다. 새 호 밖으로 나가면 거절."""
    r = ctx.st.el[w]
    _check("wall", w, q)
    ctx.put_el(w, dict(r, params=q))
    n = AG.length(AG.info(q["axis"], q["arc"]))
    C.move_wall_voids(ctx, w, lambda u: u * k)   # 독립 벽 개구(W2 세 번째 PR, ARC-34)도 같은 각도에
    for d in sorted(ctx.rels_out("hosts", w)):
        dr = ctx.st.el[d]
        if "offset" not in dr["params"]:
            continue
        ctx.put_el(d, dict(dr, params=dict(dr["params"], offset=X._r6(dr["params"]["offset"] * k))))
        lo, hi = _hosted_span(ctx, d)
        if lo < -1e-6 or hi > n + 1e-6:
            raise DobimError("CONSTRAINT", f"간격띄우기한 곡선 벽({n:.1f}mm) 밖으로 호스팅 요소가 나간다: {d}", path=path)


def _left(axis, d: float) -> tuple:
    """축선 왼쪽(+v)으로 d mm 옮기는 (dx, dy). 소수 6자리로 반올림(축이 수평·수직이면 정확한 값)."""
    _, _, c, s, _ = C._frame_of(axis)
    return X._r6(-s * d), X._r6(c * d)


class WallOffset(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX)] = Field(description="벽. 각 벽을 제 축선의 왼쪽(+)·오른쪽(-)으로 띄운다")
    distance: Num = Field(description="mm. + 는 축선 진행 방향 왼쪽")
    keep: bool = Field(True, description="true 면 원본을 두고 띄운 복사본을 만든다. false 면 벽을 옮긴다")
    new_ids: Annotated[list[Id], Field(max_length=COPY_MAX)] | None = Field(None, description="keep 의 새 id. 비우면 자동")


@command("wall.offset", kind="mutation", risk="write", group="model.wall", summary="벽을 축선에 수직으로 간격띄우기(복사 또는 이동)",
         args=WallOffset, since="0.12", examples=[{"ids": [F.W1], "distance": -1500}, {"ids": [F.W2], "distance": 600, "keep": False}])
def wall_offset(ctx, a: WallOffset):
    if a.distance == 0:
        raise DobimError("CONSTRAINT", "distance 가 0 이면 제자리다", path="/distance")
    if a.new_ids is not None and not a.keep:
        raise DobimError("CONSTRAINT", "new_ids 는 keep 과 함께 쓴다", path="/new_ids")
    _walls(ctx, a.ids, "간격띄우기")
    st = ctx.st
    # 곡선 벽(W2 ARC-04)은 동심 호(왼쪽이 중심 쪽이면 반지름이 distance 만큼 작아진다), 직선 벽은 평행 이동
    arcs = {w: _arc_offset(st.el[w]["params"], a.distance, f"/ids/{i}") for i, w in enumerate(a.ids) if "arc" in st.el[w]["params"]}
    moves = {w: _left(st.el[w]["params"]["axis"], a.distance) for w in a.ids if w not in arcs}
    if not a.keep:
        for i, w in enumerate(a.ids):
            if w in arcs:
                _pick(ctx, [w], "간격띄우기")
                _arc_offset_apply(ctx, w, *arcs[w], f"/ids/{i}")
            else:
                _transform(ctx, _pick(ctx, [w], "간격띄우기"), X.move(*moves[w]))
        return {"count": len(a.ids)}
    sels = [_pick(ctx, [w], "간격띄우기", copy=True) for w in a.ids]
    n = sum(len(s) for s in sels)
    if n > COPY_MAX:
        raise DobimError("CONSTRAINT", f"한 번에 {COPY_MAX}개까지 만든다(이번 {n}개)", path="/ids")
    ids = _new_ids(ctx, a.new_ids, n)
    out, mp, i = [], {}, 0
    for j, (w, sel) in enumerate(zip(a.ids, sels)):
        got = _copy_set(ctx, sel, X.move(0.0, 0.0) if w in arcs else X.move(*moves[w]), None, ids[i:i + len(sel)], [])
        if w in arcs:   # 제자리 복사본을 동심 호로 바꾼다(이름은 복사 규칙 그대로)
            q, k = arcs[w]
            _arc_offset_apply(ctx, got[0], dict(q, name=st.el[got[0]]["params"]["name"]), k, f"/ids/{j}")
        i += len(sel)
        mp[w] = got[0]
        out += got
    for w in a.ids:   # 둘 다 띄운 벽끼리는 조인도 옮긴다
        for o in ctx.rels_out("joins", w):
            if o in mp:
                ctx.rel("joins", min(mp[w], mp[o]), max(mp[w], mp[o]), True)
    a.new_ids = out
    return {"count": len(out), "ids": out[:ANS_IDS], **({"truncated": True} if len(out) > ANS_IDS else {})}


def _line_hit(ax, bx):
    """두 축선(무한 직선)의 교점(소수 6자리). 평행이면 None."""
    x1, y1, c1, s1, _ = C._frame_of(ax)
    x2, y2, c2, s2, _ = C._frame_of(bx)
    det = c1 * s2 - s1 * c2
    if -C.Q.PARALLEL_EPS < det < C.Q.PARALLEL_EPS:
        return None
    t = ((x2 - x1) * s2 - (y2 - y1) * c2) / det
    return X._r6(x1 + t * c1), X._r6(y1 + t * s1)


def _hits_any(pa: dict, pb: dict) -> list:
    """두 벽 축(직선은 무한 직선, 곡선 벽은 원)의 교점 후보(소수 6자리). 곡선 벽(W2 ARC-04)이 낄 때만 쓴다."""
    aa = AG.info(pa["axis"], pa["arc"]) if "arc" in pa else None
    ab = AG.info(pb["axis"], pb["arc"]) if "arc" in pb else None
    if aa is not None and ab is not None:
        cand = AG.circle_hits(aa, ab)
    else:
        arc, line = (aa, pb) if aa is not None else (ab, pa)
        x, y, c, s_, _ = C._frame_of(line["axis"])
        cand = AG.line_hits(arc, x, y, c, s_)
    return [(X._r6(q[0]), X._r6(q[1])) for q in cand]


def _end_gap(p: dict, pt, end=None) -> float:
    """교점 pt 가 벽 p 의 끝(end, 생략하면 가까운 끝)에서 축을 따라 떨어진 거리."""
    t, n = C.any_u(p, pt), C.wall_len(p)
    if end == "start":
        return abs(t)
    if end == "end":
        return abs(t - n)
    return min(abs(t), abs(t - n))


def _near_end_any(p: dict, pt) -> str:
    if "arc" not in p:
        return _near_end(p["axis"], pt)
    t, n = C.any_u(p, pt), C.wall_len(p)
    return "start" if abs(t) < abs(t - n) else "end"


def _near_end(axis, pt) -> str:
    x, y, c, s, n = C._frame_of(axis)
    t = (pt[0] - x) * c + (pt[1] - y) * s
    return "start" if abs(t) < abs(t - n) else "end"


def _to_point(ctx, w: str, end: str, pt, path: str) -> bool:
    """벽 w 의 한 끝을 축선 위 점 pt 로 옮긴다(트림 또는 연장). 시작점이 움직이면 축선 거리로 잰 값
    (개구부·호스팅 요소 offset, 잘라내기 u)을 그만큼 당겨 제자리에 둔다. 나머지 검사는 wall.set 이 한다."""
    r = ctx.st.el[w]
    p = r["params"]
    if "arc" in p:   # 곡선 벽(W2 ARC-04): 끝을 호를 따라 옮긴다(u 는 호 위 거리). 같은 원 위의 새 호가 된다
        ai = AG.info(p["axis"], p["arc"])
        n = AG.length(ai)
        t = AG.u_of(ai, pt)
    else:
        x, y, c, s, n = C._frame_of(p["axis"])
        t = (pt[0] - x) * c + (pt[1] - y) * s
    if (end == "end" and t < 1.0) or (end == "start" and t > n - 1.0):
        raise DobimError("CONSTRAINT", f"벽 {w} 의 {end} 끝을 옮기면 길이가 1mm 미만이거나 방향이 뒤집힌다", path=path)
    new = [p["axis"][0], p["axis"][1], pt[0], pt[1]] if end == "end" else [pt[0], pt[1], p["axis"][2], p["axis"][3]]
    if new == p["axis"]:
        return False
    mid = None
    if "arc" in p:
        u0, u1 = (0.0, t) if end == "end" else (t, n)
        if u1 - u0 >= AG.TWO_PI * ai[2] - 1.0:
            raise DobimError("CONSTRAINT", f"곡선 벽 {w} 를 늘이면 원을 한 바퀴 넘게 돈다", path=path)
        mid = [X._r6(v) for v in AG.point(ai, (u0 + u1) / 2.0)]
    if "top_profile" in p:
        raise DobimError("CONSTRAINT", f"벽 {w} 에 상단 윤곽이 있어 길이를 바꿀 수 없다", path=path, hint="wall.detach_top 으로 뗀 뒤 다시 붙이세요")
    sh = X._r6(t) if end == "start" else 0.0
    if sh:
        q = dict(p)
        if "openings" in p:
            q["openings"] = [dict(o, offset=X._r6(o["offset"] - sh)) for o in p["openings"]]
        if "cuts" in p:
            q["cuts"] = [[X._r6(cu[0] - sh), X._r6(cu[1] - sh), *cu[2:]] for cu in p["cuts"]]
        ctx.put_el(w, dict(r, params=q))
        for d in sorted(ctx.rels_out("hosts", w)):
            dr = ctx.st.el[d]
            ctx.authored(d, what="트림/연장")
            ctx.put_el(d, dict(dr, params=dict(dr["params"], offset=X._r6(dr["params"]["offset"] - sh))))
        C.move_wall_voids(ctx, w, lambda u: u - sh)   # 독립 벽 개구(W2 세 번째 PR, ARC-34)도 제자리에
    try:
        if mid is not None:
            from dobim_core import commands_arcwall as CA
            CA.wall_arc_set(ctx, CA.WallArcSet(id=w, points=[new[:2], mid, new[2:]]))
        else:
            C.wall_set(ctx, C.WallSet(id=w, axis=new))
    except DobimError as ex:
        raise DobimError(ex.code, f"벽 {w}: {ex.message}", path=path, hint=ex.hint, details=ex.details) from None
    return True


class WallExtendTo(A):
    id: Id = Field(description="자르거나 늘일 벽")
    to: Id = Field(description="경계 벽(축선을 무한 직선으로 본다. 곡선 벽이면 그 원)")
    end: Literal["start", "end"] | None = Field(None, description="옮길 끝. 생략하면 교점에 가까운 끝")


@command("wall.extend_to", kind="mutation", risk="write", group="model.wall", summary="벽 한 끝을 경계 벽 축선까지 자르거나 늘인다",
         args=WallExtendTo, since="0.12", examples=[{"id": F.W1, "to": F.W2, "end": "end"}])
def wall_extend_to(ctx, a: WallExtendTo):
    if a.id == a.to:
        raise DobimError("CONSTRAINT", "경계는 다른 벽이어야 한다", path="/to")
    _walls(ctx, [a.id], "트림/연장")
    ctx.row(a.to, "wall")
    pa, pb = ctx.st.el[a.id]["params"], ctx.st.el[a.to]["params"]
    if "arc" in pa or "arc" in pb:   # 곡선 벽(W2 ARC-04): 원과 축의 교점 중 옮길 끝(생략하면 가까운 끝)에서 축을 따라 가장 가까운 것
        cand = _hits_any(pa, pb)
        if not cand:
            raise DobimError("CONSTRAINT", "벽 축이 경계 벽의 축(원)과 만나지 않는다", path="/to")
        pt = min(cand, key=lambda q: (_end_gap(pa, q, a.end), q))
        end = a.end or _near_end_any(pa, pt)
        changed = _to_point(ctx, a.id, end, pt, "/id")
        return {"id": a.id, "end": end, "point": list(pt), "changed": changed}
    pt = _line_hit(ctx.st.el[a.id]["params"]["axis"], ctx.st.el[a.to]["params"]["axis"])
    if pt is None:
        raise DobimError("CONSTRAINT", "두 벽이 평행해 만나지 않는다", path="/to")
    end = a.end or _near_end(ctx.st.el[a.id]["params"]["axis"], pt)
    changed = _to_point(ctx, a.id, end, pt, "/id")
    return {"id": a.id, "end": end, "point": list(pt), "changed": changed}


class WallCorner(A):
    a: Id
    b: Id
    join: bool = Field(True, description="모서리를 맞춘 뒤 두 벽을 조인한다(같은 레벨이어야 한다)")


@command("wall.corner", kind="mutation", risk="write", group="model.wall", summary="두 벽을 축선 교점까지 자르거나 늘여 모서리로 잇는다",
         args=WallCorner, since="0.12", examples=[{"a": F.W1, "b": F.W2}])
def wall_corner(ctx, a: WallCorner):
    if a.a == a.b:
        raise DobimError("CONSTRAINT", "같은 벽끼리는 모서리를 만들 수 없다", path="/b")
    _walls(ctx, [a.a, a.b], "트림/연장")
    pa, pb = ctx.st.el[a.a]["params"], ctx.st.el[a.b]["params"]
    if "arc" in pa or "arc" in pb:   # 곡선 벽(W2 ARC-04): 교점 후보 중 두 벽 끝에서 축을 따라 잰 거리의 합이 가장 작은 것
        cand = _hits_any(pa, pb)
        if not cand:
            raise DobimError("CONSTRAINT", "두 벽의 축(원)이 만나지 않는다", path="/b")
        pt = min(cand, key=lambda q: (_end_gap(pa, q) + _end_gap(pb, q), q))
    else:
        pt = _line_hit(pa["axis"], pb["axis"])
    if pt is None:
        raise DobimError("CONSTRAINT", "두 벽이 평행해 만나지 않는다", path="/b")
    if a.join and ctx.rels_out("on_level", a.a) != ctx.rels_out("on_level", a.b):
        raise DobimError("CONSTRAINT", "다른 레벨의 벽은 조인할 수 없다", path="/b", hint="join 을 false 로 주세요")
    ends = {w: _near_end_any(ctx.st.el[w]["params"], pt) for w in (a.a, a.b)}
    for w, path in ((a.a, "/a"), (a.b, "/b")):
        _to_point(ctx, w, ends[w], pt, path)
    if a.join:
        ctx.rel("joins", min(a.a, a.b), max(a.a, a.b), True)
    return {"point": list(pt), "ends": ends, "joined": a.join}


# ================================================================ 일괄 편집(W1 1-F 2차, UI-04)
BULK_MAX = 1000   # 한 번에 고치는 요소 수 상한


class Where(A):
    kind: Literal[K.KIND_NAMES] | None = None
    level: Id | None = None
    name_contains: str | None = Field(None, min_length=1, max_length=64)


class Bulk(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=BULK_MAX)] | None = Field(None, description="대상 id. where 와 둘 중 하나")
    where: Where | None = Field(None, description="element.query 와 같은 거르기. 실행 때 ids 로 풀어 로그에 남긴다")

    @model_validator(mode="after")
    def _one(self):
        if (self.ids is None) == (self.where is None):
            raise ValueError("ids 와 where 중 정확히 하나를 준다")
        return self


def _targets(ctx, a: Bulk, kind: str | None = None) -> list:
    """대상 id(준 순서 또는 id 순). where 는 ids 로 바꿔 둔다(재생이 같은 대상을 고친다)."""
    st = ctx.st
    if a.ids is not None:
        if len(set(a.ids)) != len(a.ids):
            raise DobimError("CONSTRAINT", "ids 에 같은 id 가 두 번 있다", path="/ids")
        for i, e in enumerate(a.ids):
            r = st.el.get(e)
            if r is None or (kind and r["kind"] != kind):
                raise DobimError("NOT_FOUND", f"{kind or '요소'} 를 찾을 수 없다: {e}", path=f"/ids/{i}")
        return list(a.ids)
    w = a.where
    if kind and w.kind not in (None, kind):
        raise DobimError("CONSTRAINT", f"이 커맨드는 {kind} 만 고친다", path="/where/kind")
    k = kind or w.kind
    hits = []
    for e in (sorted(st.by_kind.get(k, ())) if k else sorted(st.el)):
        if w.level and not (e == w.level or w.level in st.out.get(("on_level", e), ())):
            continue
        if w.name_contains and w.name_contains.lower() not in st.el[e]["params"]["name"].lower():
            continue
        hits.append(e)
    if not hits:
        raise DobimError("NOT_FOUND", "where 에 맞는 요소가 없다", path="/where")
    if len(hits) > BULK_MAX:
        raise DobimError("CONSTRAINT", f"한 번에 {BULK_MAX}개까지 고친다(맞는 요소 {len(hits)}개)", path="/where", hint="where 를 좁히세요")
    a.ids, a.where = hits, None
    return hits


def _each(ids: list, fn) -> list:
    """요소마다 fn 을 부르고 바뀐 id 를 모은다. 하나라도 실패하면 전체가 실패한다(어느 요소인지 path 에 적는다)."""
    out = []
    for i, e in enumerate(ids):
        try:
            if fn(e):
                out.append(e)
        except DobimError as ex:
            raise DobimError(ex.code, f"{e}: {ex.message}", path=f"/ids/{i}", hint=ex.hint, details=ex.details) from None
    return out


def _bulk_ans(ids: list, changed: list) -> dict:
    return {"count": len(ids), "changed": len(changed), "ids": changed[:ANS_IDS], **({"truncated": True} if len(changed) > ANS_IDS else {})}


class PsetSetMany(Bulk):
    pset: PsetName
    prop: PropName
    value: PropValue = Field(description="bool·정수·실수·문자열")


@command("pset.set_many", kind="mutation", risk="write", group="model.pset", summary="여러 요소에 같은 속성 쓰기(ids 또는 where)",
         args=PsetSetMany, since="0.12", examples=[{"where": {"kind": "door"}, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI30"}])
def pset_set_many(ctx, a: PsetSetMany):
    if a.pset == RESERVED_PSET:
        raise DobimError("CONSTRAINT", f"{RESERVED_PSET} 는 투영이 소유한 예약 속성셋이다", path="/pset")
    ids = _targets(ctx, a)
    d = C.pparam_of(ctx.st, a.pset, a.prop)   # 프로젝트 매개변수(DATA-03): 정의가 있으면 종류·형식 검사
    return _bulk_ans(ids, _each(ids, lambda e: (C.pparam_check(ctx, d, e, a.value), ctx.set_prop(e, a.pset, a.prop, a.value))[1]))


class MaterialSetMany(Bulk):
    name: Name | None = Field(description="null 이면 재료를 뗀다")


@command("material.set_many", kind="mutation", risk="write", group="model.pset", summary="여러 요소에 같은 재료 지정(ids 또는 where)",
         args=MaterialSetMany, since="0.12", examples=[{"where": {"kind": "slab"}, "name": "Concrete"}])
def material_set_many(ctx, a: MaterialSetMany):
    ids = _targets(ctx, a)

    def one(e):
        ctx.authored(e, what="재료 지정")
        return ctx.set_material(e, a.name)
    return _bulk_ans(ids, _each(ids, one))


WALL_MANY = ("thickness", "height", "base_offset", "top_level", "top_offset")


class WallSetMany(Bulk):
    forbid_null = True
    thickness: Pos | None = None
    height: Pos | None = None
    base_offset: Num | None = None
    top_level: Id | None = None
    top_offset: Num | None = None

    @model_validator(mode="after")
    def _any(self):
        if all(getattr(self, k) is None for k in WALL_MANY):
            raise ValueError("바꿀 필드가 하나도 없다")
        if self.height is not None and (self.top_level is not None or self.top_offset is not None):
            raise ValueError("height 와 top_level·top_offset 은 함께 줄 수 없다")
        return self


@command("wall.set_many", kind="mutation", risk="write", group="model.wall", summary="여러 벽의 두께·높이·바닥·상단 구속을 한 번에(ids 또는 where)",
         args=WallSetMany, since="0.12", examples=[{"where": {"level": F.LV1}, "height": 3200}])
def wall_set_many(ctx, a: WallSetMany):
    ids = _targets(ctx, a, "wall")
    vals = {k: getattr(a, k) for k in WALL_MANY if getattr(a, k) is not None}

    def one(w):
        before = ctx.st.el[w]["params"]
        C.wall_set(ctx, C.WallSet(id=w, **vals))
        return ctx.st.el[w]["params"] != before
    return _bulk_ans(ids, _each(ids, one))
