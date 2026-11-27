# SPDX-License-Identifier: MPL-2.0
"""태그 지시선·겹침 회피 배치 (W2 스물여섯 번째 PR, ANN-06 후속).

- annot.tag.set: 태그의 글 자리(간격)·지시선(켜기·끄기, 끝점 부착·자유, 꺾임점)·글 맞춤을 손으로 정한다. 손으로 옮긴 태그는 고정(pin)이라 자동 배치가 건드리지 않는다.
- annot.tag.arrange: 뷰 하나의 태그를 서로, 그리고 치수·문자와 겹치지 않게 결정적으로 배치한다(고정 태그는 움직이지 않는 장애물, 순수 계산은 annot_layout).
- annot.tag.create·all 의 avoid 옵션은 만든 태그에 같은 배치를 건다.
- 단면·입면 뷰(W2 스물일곱 번째 PR): 위 명령이 모두 단면·입면 뷰에서도 된다. 좌표는 뷰의 (가로 s, 높이 z) mm 이고 대표점은 AN.tag_view_anchor 다.
- annot.tag.sheet_check: 시트에 올린 뷰들의 태그가 시트 용지 위에서 겹치는지 보고만 한다(자리를 바꾸지 않는다).
- 지시선 끝점 end fixed: 뷰 좌표 절대 끝점(대상을 옮겨도 그 자리에 남는다).

간격은 모두 대상 대표점(AN.tag_anchor) 기준이라 대상을 옮기면 글·끝점·꺾임점이 함께 따라간다(기존 주석 추종 규약).
저장 형식: 태그 행의 선택 키 leader·side·pin 은 쓸 때만 생긴다. 이 키가 없는 태그의 행·그림 항목·도면은 이전과 같다."""
from __future__ import annotations

from math import hypot
from typing import Annotated, Literal

from pydantic import Field

from dobim_core import annot as AN
from dobim_core import annot_layout as LY
from dobim_core import beams as BM
from dobim_core import drafting as DRF
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import queries as Q
from dobim_core.commands import A, Id, Off, command
from dobim_core.errors import DobimError

Pair = Annotated[list[Off], Field(min_length=2, max_length=2)]


class LeaderSpec(A):
    on: bool = Field(True, description="지시선을 그릴지. 지시선 태그(가로 글)는 기본 켜짐, 보 축 방향 태그는 기본 꺼짐")
    end: Literal["attached", "free", "fixed"] = Field(
        "attached", description="attached = 지시선이 대상 대표점에 붙는다(부착 끝점), free = tip 이 가리키는 자리에서 끝난다(자유 끝점: 대표점 기준이라 대상과 함께 이동), "
                                "fixed = 뷰 좌표 절대 끝점(대상을 옮겨도 그 자리에 남는다)")
    tip: Pair | None = Field(None, description="free·fixed 일 때 필수. free: 지시선 끝점 = 대상 대표점 + tip(mm, 대상을 옮기면 같이 따라간다), "
                                              "fixed: 뷰 좌표 [x, y](mm, 평면 뷰는 모델 좌표, 단면·입면 뷰는 [가로, 높이]) 그대로")
    bend: Pair | None = Field(None, description="꺾임점 = 대상 대표점 + bend(mm). 지시선이 끝점 -> 꺾임점 -> 글 자리로 꺾인다")


class TagSet(A):
    forbid_null = True
    id: Id = Field(description="태그 요소 id")
    offset: Pair | None = Field(None, description="글 자리 = 대상 대표점 + offset(mm). 손으로 옮기는 것이라 고정(pin)된다")
    leader: LeaderSpec | None = Field(None, description="지시선 설정(켜기·끄기, 끝점 부착·자유, 꺾임점). 통째로 바꾼다")
    side: Literal["start", "middle", "end"] | None = Field(
        None, description="가로 글을 글 자리의 오른쪽(start, 기본)·가운데(middle)·왼쪽(end)에 놓는다. 보 축 방향 글은 항상 가운데 맞춤")
    pin: bool | None = Field(None, description="true = 자동 배치(annot.tag.arrange)가 옮기지 않는다, false = 풀어 준다. 생략하면 offset·leader·side 를 준 때만 고정")
    reset: bool = Field(False, description="true = 간격·지시선·글 맞춤·고정을 모두 기본으로 되돌린다(다른 필드와 함께 쓰지 않는다)")


def _default_off(p):
    return [0.0, 0.0] if p.get("align") == "axis" else [400.0, 400.0]


def _leader_store(p, spec: LeaderSpec):
    """지시선 설정을 저장 형식으로 바꾼다(기본과 같으면 키를 두지 않는다)."""
    if not spec.on and (spec.tip is not None or spec.bend is not None):
        raise DobimError("CONSTRAINT", "지시선을 끈 태그에는 tip·bend 를 두지 않는다", path="/leader")
    if spec.end in ("free", "fixed") and spec.tip is None:
        raise DobimError("CONSTRAINT", f"{'자유' if spec.end == 'free' else '절대'} 끝점(end {spec.end})에는 tip 이 필요하다", path="/leader/tip",
                         hint="free: tip = 대상 대표점에서 끝점까지 [dx, dy] mm, fixed: tip = 뷰 좌표 [x, y] mm")
    if spec.end == "attached" and spec.tip is not None:
        raise DobimError("CONSTRAINT", "tip 은 자유·절대 끝점(end free·fixed)에만 쓴다", path="/leader/tip")
    ld: dict = {"on": bool(spec.on)}
    if spec.end in ("free", "fixed"):
        ld.update(end=spec.end, tip=[float(spec.tip[0]), float(spec.tip[1])])
    if spec.bend is not None:
        ld["bend"] = [float(spec.bend[0]), float(spec.bend[1])]
    return None if ld == {"on": p.get("align") != "axis"} else ld   # 기본(지시선 태그 켜짐, 보 축 방향 태그 꺼짐)과 같으면 키를 두지 않는다


@command("annot.tag.set", kind="mutation", risk="write", group="annot",
         summary="태그의 글 자리·지시선(켜기·끄기, 끝점 부착·자유, 꺾임점)·글 맞춤을 정한다(손으로 옮긴 태그는 고정)",
         args=TagSet, since="0.25", examples=[{"id": F.TG1, "offset": [900, 600], "leader": {"on": True, "bend": [500, 600]}}])
def annot_tag_set(ctx, a: TagSet):
    r = ctx.row(a.id)
    if r["kind"] != "tag":
        raise DobimError("CONSTRAINT", f"태그가 아니다: {r['kind']}", path="/id")
    p = dict(r["params"])
    if ctx.st.el.get(p["target"]) is None:
        raise DobimError("CONSTRAINT", "대상이 사라진 태그(고아)다", path="/id", hint="annot.rebind 로 다시 묶은 뒤 고친다")
    if a.reset:
        if any(v is not None for v in (a.offset, a.leader, a.side, a.pin)):
            raise DobimError("CONSTRAINT", "reset 은 다른 필드와 함께 쓰지 않는다", path="/reset")
        for k in ("leader", "side", "pin"):
            p.pop(k, None)
        p["off"] = _default_off(p)
    else:
        if all(v is None for v in (a.offset, a.leader, a.side, a.pin)):
            raise DobimError("CONSTRAINT", "바꿀 필드가 하나도 없다", path="/id")
        if a.offset is not None:
            p["off"] = [float(a.offset[0]), float(a.offset[1])]
        if a.leader is not None:
            ld = _leader_store(p, a.leader)
            if ld is None:
                p.pop("leader", None)
            else:
                p["leader"] = ld
        if a.side is not None:
            if a.side == "start":
                p.pop("side", None)
            else:
                if p.get("align") == "axis":
                    raise DobimError("CONSTRAINT", "보 축 방향 글은 항상 가운데 맞춤이다(side 를 쓰지 않는다)", path="/side")
                p["side"] = a.side
        pin = a.pin if a.pin is not None else True   # 손으로 정한 것은 고정이 기본이다
        if pin:
            p["pin"] = True
        else:
            p.pop("pin", None)
    anc = AN.tag_anchor_in(ctx.fresh(), p["view"], p["target"])   # 단면·입면 뷰는 뷰의 (가로, 높이), 대상이 안 보이면 None(home 은 그대로)
    if anc is not None:
        p["home"] = [round(anc[0] + p["off"][0], 3), round(anc[1] + p["off"][1], 3)]
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "off": p["off"], "leader": p.get("leader"), "side": p.get("side", "start"), "pin": bool(p.get("pin"))}


# ---------------------------------------------------------------- 겹침 회피 배치
def _dim_obstacles(it, scale, boxes, segs):
    """치수 그림 항목(dim·adim)의 글 상자와 치수선·보조선."""
    if it[0] == "dim":
        _, _, e0, e1, ln, text, tpos, u = it
        segs += [tuple(e0), tuple(e1), tuple(ln)]
    else:
        _, _, ss, _ticks, text, tpos, u = it
        segs += [tuple(s) for s in ss]
    boxes.append(LY.tag_box(tuple(tpos), text, tuple(u), "start", scale))   # 치수 글은 보 축 방향 글과 같은 자리 규칙(가운데 맞춤, 기준선이 0.8 mm 위)


def _scene(ctx, view, scale):
    """뷰의 움직이지 않는 장애물과 태그: (태그 항목 {id: 항목}, 글 상자들, 선분들)."""
    g = ctx.fresh()
    tags, boxes, segs = {}, [], []
    for it in g.q(AN.view_annots, view) + g.q(D.view_dims, view):
        if it[0] in ("dim", "adim"):
            _dim_obstacles(it, scale, boxes, segs)
        elif it[0] == "tag":
            tags[it[1]] = it
    for x, y, text, size, anchor in g.q(D.view_labels, view):   # 방 이름·면적 같은 문자 층
        boxes.append(LY.text_box_at((x, y), text, size, anchor, 0.0, scale))
    for it in g.q(DRF.view_drafts, view):   # 2D 작도 문자
        if it[0] == "text":
            _, _, _, _, x, y, size, text, anchor, ang = it
            boxes.append(LY.text_box_at((x, y), text, size, anchor, -ang, scale))
    return tags, boxes, segs


def arrange_tags(ctx, view, ids=None, new=()):
    """뷰의 태그를 겹치지 않게 배치하고 바뀐 것을 params 에 쓴다. ids = 배치할 태그(없으면 뷰의 모든 태그), 고정(pin)된 태그와 배치 대상이 아닌 태그는 장애물이다.
    반환: {"moved": [...], "kept": 안 옮긴 수, "pinned": 고정이라 건너뛴 수, "unresolved": [...], "skipped": [...]}"""
    vr = ctx.row(view, "view")
    sec = vr["params"]["type"] in AN.SECTION_VIEW_TYPES   # 단면·입면 뷰(W2 스물일곱 번째 PR): 같은 배치, 좌표는 뷰의 (가로, 높이)
    if vr["params"]["type"] != "plan" and not sec:
        raise DobimError("CONSTRAINT", "태그는 평면·단면·입면 뷰에 둔다", path="/view")
    scale = float(vr["params"]["scale"])
    items, boxes, segs = _scene(ctx, view, scale)
    want = set(ids) if ids is not None else None
    todo, pinned, skipped, hidden = [], [], [], []
    for tid in sorted(ctx.rels_in("annot_of", view)):
        row = ctx.st.el[tid]
        if row["kind"] != "tag":
            continue
        if want is not None and tid not in want:
            it = items.get(tid)   # 배치 대상이 아닌 태그: 움직이지 않는 장애물
            if it is not None:
                _fix(it, scale, boxes, segs)
            continue
        it = items.get(tid)
        if it is None and sec and ctx.fresh().q(AN.tag_text, tid)[0] == "ok":   # 대상이 이 뷰에 보이지 않아 그리지 않는 태그: 옮길 것도 장애물도 아니다
            hidden.append(tid)
        elif it is None:   # 고아·무효 태그: 그림 항목이 태그가 아니라 자리를 잡을 수 없다
            skipped.append(tid)
        elif row["params"].get("pin"):
            pinned.append(tid)
            _fix(it, scale, boxes, segs)
        else:
            p = row["params"]
            anc, _lab, text, u, _pts, _side = LY.tag_parts(it)
            todo.append({"id": tid, "anc": tuple(anc), "text": text, "dir": None if u is None else tuple(u), "off": tuple(p["off"]), "side": p.get("side", "start"),
                         "span": _beam_span(ctx, p["target"]) if u is not None else None})
    res, bad = LY.arrange(todo, boxes, segs, scale)
    g = ctx.fresh()
    moved = []
    for tg in sorted(todo, key=lambda t: t["id"]):
        off, side = res[tg["id"]]
        if tuple(off) == tg["off"] and side == tg["side"]:
            continue
        r = ctx.st.el[tg["id"]]
        p = dict(r["params"])
        p["off"] = [float(off[0]), float(off[1])]
        if side == "start":
            p.pop("side", None)
        else:
            p["side"] = side
        _auto_leader(p, LY.lead_on(tg["dir"], off), LY.lead_start(tg["dir"], off))
        ax, ay = AN.tag_anchor_in(g, view, p["target"])
        p["home"] = [round(ax + p["off"][0], 3), round(ay + p["off"][1], 3)]
        ctx.put_el(tg["id"], dict(r, params=p))
        moved.append({"id": tg["id"], "off": p["off"], "side": side, "leader": p.get("leader", {"on": p.get("align") != "axis"}).get("on", True)})
    out = {"moved": moved, "kept": len(todo) - len(moved), "pinned": len(pinned), "unresolved": bad, "skipped": sorted(skipped)}
    if hidden:   # 단면·입면 뷰에서만 생기는 키(평면 뷰 응답은 이전과 같다)
        out["hidden"] = sorted(hidden)
    return out


def _beam_span(ctx, target):
    """보 축 방향 태그가 보를 따라 미끄러질 수 있는 한쪽 길이(보 가운데에서 한쪽 끝까지): 기둥·보 면으로 자른 뒤 남은 축 길이의 절반."""
    row = ctx.st.el[target]
    p = row["params"]
    if row["kind"] == "truss":   # 트러스 태그(W2 스물다섯 번째 PR): 두 점의 가운데에서 한쪽 끝까지(평면 길이의 절반)
        return hypot(p["end"][0] - p["start"][0], p["end"][1] - p["start"][1]) / 2.0
    if row["kind"] == "beamsys":   # 보 시스템 태그: 경계 무게중심에서 작은 보 방향의 한쪽 끝까지(경계를 그 방향에 투영한 길이의 절반)
        sn, cs = K.sincos_deg(float(p["angle"]))
        us = [x * cs + y * sn for x, y in p["boundary"]]
        return (max(us) - min(us)) / 2.0
    cut = ctx.fresh().q(Q.beam_cut, target)
    return max(0.0, (BM.length3(p) - float(cut[0]) - float(cut[1])) / 2.0)


def _fix(it, scale, boxes, segs):
    """움직이지 않는 태그 하나를 장애물로 더한다(글 상자 + 지시선 선분)."""
    _anc, lab, text, u, pts, side = LY.tag_parts(it)
    boxes.append(LY.tag_box(tuple(lab), text, None if u is None else tuple(u), side, scale))
    segs += [tuple(a) + tuple(b) for a, b in zip(pts, pts[1:])]


def _auto_leader(p, lifted, start):
    """배치가 태그를 옮긴 뒤의 지시선 규칙: 보 축 방향 태그가 보 가운데선에서 위로 들리면(lifted) 지시선을 켜고, 시작점은 글 바로 밑의 보 위 점이다
    (start = 대표점 기준 간격: 0 이면 대표점에 부착, 아니면 자유 끝점 tip). 꺾임점은 버린다. 보 위에 있으면(가운데나 보를 따라 미끄러진 자리) 키를 지운다(기본 꺼짐).
    지시선 태그는 켜짐 상태를 그대로 두되 꺾임점만 버린다(글이 옮겨져 꺾임점이 맞지 않는다)."""
    ld = p.get("leader")
    if p.get("align") == "axis":
        if not lifted:
            p.pop("leader", None)
            return
        new = {"on": True}
        if abs(start[0]) > 1e-6 or abs(start[1]) > 1e-6:
            new.update(end="free", tip=[start[0], start[1]])
        p["leader"] = new
    elif ld is not None and "bend" in ld:
        new = {k: v for k, v in ld.items() if k != "bend"}
        if new == {"on": True}:
            p.pop("leader", None)
        else:
            p["leader"] = new


class TagArrange(A):
    view: Id = Field(description="태그를 배치할 평면·단면·입면 뷰")
    ids: Annotated[list[Id], Field(max_length=5000)] | None = Field(None, description="배치할 태그(생략하면 뷰의 모든 태그). 나머지 태그는 움직이지 않는 장애물이다")


@command("annot.tag.arrange", kind="mutation", risk="write", group="annot",
         summary="뷰의 태그를 서로, 그리고 치수·문자와 겹치지 않게 결정적으로 배치한다(손으로 옮긴 고정 태그는 그대로, 보 태그는 보를 벗어나면 지시선이 켜진다)",
         args=TagArrange, since="0.25", examples=[{"view": F.V1}])
def annot_tag_arrange(ctx, a: TagArrange):
    out = arrange_tags(ctx, a.view, a.ids)
    if out["unresolved"]:
        ctx.warn(f"겹침 없는 자리를 못 찾은 태그 {len(out['unresolved'])}개(가장 덜 겹치는 자리에 놓았다): " + ", ".join(t[-6:] for t in out["unresolved"][:10]))
    if out["skipped"]:
        ctx.warn(f"고아·무효 태그 {len(out['skipped'])}개는 건너뛴다")
    if out.get("hidden"):
        ctx.warn(f"대상이 이 뷰에 보이지 않아 그리지 않는 태그 {len(out['hidden'])}개는 건너뛴다")
    return {"view": a.view, **out}


# ---------------------------------------------------------------- 시트 단위 겹침 검사 (W2 스물일곱 번째 PR)
class TagSheetCheck(A):
    id: Id = Field(description="저장된 시트 정의의 id")
    within: bool = Field(False, description="true = 같은 뷰 안의 태그끼리도 본다(기본은 서로 다른 뷰의 태그 사이만: 한 뷰 안은 annot.tag.arrange 가 푼다)")
    limit: int = Field(100, ge=1, le=1000, description="나열할 겹침 쌍의 최대 개수(전체 개수는 count)")
    boxes: bool = Field(False, description="true = 시트에 올린 모든 태그의 글 상자(시트 용지 mm [x0, y0, x1, y1], y 는 아래로)도 돌려준다(손으로 옮길 자리를 잡을 때)")


def sheet_tag_boxes(ctx, sheet_id):
    """시트에 올린 뷰들의 태그 글 상자를 시트 용지 mm 좌표로: ([(뷰 id, 태그 id, 글, (x0, y0, x1, y1)), ...], 올린 뷰 id 들).
    시트 배치(sh.placed)와 뷰 -> 시트 변환(tx·ty)은 시트를 그릴 때와 같은 함수를 쓴다. 글 높이는 시트 작성기준 프로파일의 태그 글 높이다."""
    from types import SimpleNamespace

    from dobim_core import commands as C
    from dobim_core import commands_sheet as CS
    from dobim_core import drawing_export as DX
    from dobim_core import sheet as SH
    sargs = CS._export_args(ctx, SimpleNamespace(id=sheet_id, path="check.svg", overwrite=False, max_px=1600, crop=None))
    sh, spec = C.build_sheet_for(ctx, sargs, sheet_id)
    hidden = bool(sargs.profile is not None and sargs.profile.hidden_lines)
    hier = bool(sargs.profile is not None and sargs.profile.line_hierarchy)
    size = float(((spec.get("profile") or {}).get("text_height") or {}).get("tag", LY.TAG_H))
    boxes, placed = [], []
    for pl in sh.placed:
        data = dict(C._render_view(ctx, pl["view"], sargs.detail, hidden, hier), id=pl["view"])
        _cw, _ch, tx, ty, _datums, _x0, _x1 = SH._natural(data)
        ox, oy, scale = pl["x"] - DX.MARGIN, pl["y"] - DX.MARGIN, float(data["scale"])
        placed.append(pl["view"])
        for it in data.get("annots", ()):
            if it[0] != "tag":
                continue
            _anc, lab, text, u, _pts, side = LY.tag_parts(it)
            bx = LY.tag_box(tuple(lab), text, None if u is None else tuple(u), side, scale, size)
            xs, ys = (ox + tx(bx[0]), ox + tx(bx[2])), (oy + ty(bx[1]), oy + ty(bx[3]))   # 시트 용지 좌표는 y 가 아래로 자란다
            boxes.append((pl["view"], it[1], text, (min(xs), min(ys), max(xs), max(ys))))
    return boxes, placed


def sheet_tag_overlaps(boxes, within=False):
    """겹친 태그 쌍 [(a, b, 겹친 넓이 mm²)]: x 로 정렬해 이웃만 본다. 쌍은 (뷰, 태그 id) 순으로 안정적이다."""
    order = sorted(boxes, key=lambda b: (b[3][0], b[0], b[1]))
    out = []
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if b[3][0] >= a[3][2]:
                break
            if a[0] == b[0] and not within:
                continue
            ar = LY.overlap_area(a[3], b[3])
            if ar > 0.01:
                first, second = sorted((a, b), key=lambda t: (t[0], t[1]))
                out.append((first, second, round(ar, 2)))
    out.sort(key=lambda t: (t[0][0], t[0][1], t[1][0], t[1][1]))
    return out


@command("annot.tag.sheet_check", kind="query", risk="read", group="annot",
         summary="시트에 올린 뷰들의 태그가 시트 용지 위에서 서로 겹치는지 알린다(자리는 바꾸지 않고 보고만)",
         args=TagSheetCheck, idempotent=True, since="0.25", examples=[{"id": F.SH1}])
def annot_tag_sheet_check(ctx, a: TagSheetCheck):
    boxes, placed = sheet_tag_boxes(ctx, a.id)
    ov = sheet_tag_overlaps(boxes, a.within)
    if ov:
        ctx.warn(f"시트 위에서 겹치는 태그 {len(ov)}쌍(자리는 바꾸지 않았다): 뷰의 태그는 annot.tag.arrange 나 annot.tag.set 으로 옮기세요")
    rows = [{"a": {"view": x[0], "tag": x[1], "text": x[2]}, "b": {"view": y[0], "tag": y[1], "text": y[2]}, "area_mm2": ar} for x, y, ar in ov[:a.limit]]
    out = {"sheet": a.id, "views": placed, "tags": len(boxes), "count": len(ov), "overlaps": rows, "truncated": len(ov) > a.limit}
    if a.boxes:
        out["boxes"] = [{"view": v, "tag": t, "text": x, "box": [round(c, 2) for c in b]} for v, t, x, b in sorted(boxes, key=lambda q: (q[0], q[1]))]
    return out
