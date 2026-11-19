# SPDX-License-Identifier: MPL-2.0
"""모델 편집 커맨드(W1 1-F 첫 PR): 레벨 고치기(ARC-37), 요소 복사·배열·회전·대칭(UI-03).

level.set       레벨 이름·높이를 바꾼다. 높이를 바꾸면 상단이 레벨에 묶인 벽의 height 를 다시 계산한다.
element.copy    요소들을 (dx, dy) 만큼 count 번 복사한다(선형 배열). level 을 주면 그 레벨로 복사한다.
element.rotate  요소들을 점 center 중심으로 angle 도 회전한다.
element.mirror  요소들을 축 [x1,y1,x2,y2] 에 대해 대칭으로 옮긴다(keep 이면 원본을 두고 대칭 복사본을 만든다).
벽을 고르면 거기 호스팅된 문·패밀리 인스턴스가 함께 간다. 계단·슬래브가 낸 난간은 원본을 따라간다(이동 커맨드와 같은 규칙).
형상 계산은 순수 모듈 xform 이 한다. 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

import copy as _copy
from typing import Annotated

from pydantic import Field, model_validator

from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import xform as X
from dobim_core.commands import (RAIL_SRC, A, Id, Name, Num, _apply_top_level, _follow_railings, _hosted_span, _in_split_history,
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
                n = X.axis_len(st.el[host]["params"]["axis"])
                lo, hi = _hosted_span(ctx, e)
                ctx.put_el(e, dict(r, params=dict(p, offset=X._r6(p["offset"] + n - lo - hi))))
            continue
        if kind == "railing" and p.get("src") in s and e in followed:
            continue
        new = _check(kind, e, X.params(kind, p, xf))
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
                n = X.axis_len(st.el[host]["params"]["axis"])
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
