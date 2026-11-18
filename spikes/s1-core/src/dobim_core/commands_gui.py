# SPDX-License-Identifier: MPL-2.0
"""평면 GUI 가 쓰는 조회 커맨드(B1, 설계: docs/2d-editing.md 2.3절, ADR-0016). GUI 전용이 아니다: 에이전트도 같은 것을 쓴다(패리티 G).

view.scene   뷰의 그림을 요소 id 가 붙은 선분·채움·문자·작도/주석 항목으로(타일 bbox·요소 ids 거르기, 쪽 나누기)
view.pick    뷰 좌표 한 점(+허용 거리)에 걸리는 요소 id 목록(가까운 순)
geom.snap    점 근처의 끝점·중점·교점·벽 축선·수선의 발·직교 스냅 후보

셋 다 조회(query·read)라 저장소를 바꾸지 않는다. 그림은 scene.view_scene(그래프 질의)에서 나오므로 바뀐 요소만 다시 계산된다.
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import limits
from dobim_core import scene as SC
from dobim_core.commands import A, Id, _dec_cursor, _enc_cursor, _fit, _page, command

VNum = Annotated[float, Field(allow_inf_nan=False, ge=-1.0e9, le=1.0e9)]
VPt = Annotated[list[VNum], Field(min_length=2, max_length=2, description="[x,y] 뷰 좌표 mm(평면 x,y / 단면·입면 s,z)")]
Tol = Annotated[float, Field(gt=0, le=1.0e6, allow_inf_nan=False, description="허용 거리(뷰 mm). 생략하면 용지 3mm(= 3 x 축척)")]
ROW_BYTES = 1500         # 쪽 나누기 단위(행 하나)의 목표 크기. 선분이 많은 요소는 여러 행(part)으로 나눈다
IDS_MAX = 500
PAPER_TOL_MM = 3.0       # 기본 허용 거리(용지 mm)
PART_KEYS = ("lines", "fills", "texts", "items")


def _rnd(x, nd):
    if isinstance(x, float):
        r = round(x, nd)
        return int(r) if nd == 0 else r + 0.0
    if isinstance(x, (tuple, list)):
        return [_rnd(y, nd) for y in x]
    return x


def scene_rows(g, v, decimals):
    """view_scene 을 응답 행으로: 요소마다 {"id","kind","bb", "lines"·"fills"·"texts"·"items"(있는 것만)}, 큰 요소는 part 로 나눈다.
    좌표는 decimals 자리로 반올림한다. 작도·주석 항목에서 행 id 와 같은 id 칸은 뺀다(행이 이미 가진다). 그래프 노드라 쪽마다 다시 만들지 않는다."""
    rows = []
    for kind, e, lines, fills, texts, items, bb in g.q(SC.view_scene, v):
        its = [(it[:1] + it[2:]) if len(it) > 1 and it[1] == e else it for it in items]
        entries = [("lines", _rnd(x, decimals)) for x in lines] + [("fills", _rnd(x, decimals)) for x in fills] + \
                  [("texts", _rnd(x, decimals)) for x in texts] + [("items", _rnd(x, decimals)) for x in its]
        parts, cur, size = [], {}, 0
        for k, val in entries:
            b = limits.jsize(val)
            if cur and size + b > ROW_BYTES:
                parts.append(cur)
                cur, size = {}, 0
            cur.setdefault(k, []).append(val)
            size += b
        parts.append(cur)
        head = {"id": e, "kind": kind, "bb": _rnd(list(bb), decimals) if bb else None}
        for i, p in enumerate(parts):
            r = dict(head)
            if len(parts) > 1:
                r["part"] = [i + 1, len(parts)]
            r.update({k: p[k] for k in PART_KEYS if k in p})
            rows.append(r)
    return tuple(rows)


def scene_digest(g, v):
    """전체 장면(거르기 전)의 요약: 요소 수·선분 수·경계 상자·digest. 화면이 받은 쪽들이 같은 장면인지 맞춰 보는 데 쓴다."""
    sc = g.q(SC.view_scene, v)
    bbs = [t[6] for t in sc if t[6] is not None]
    bbox = [min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs)] if bbs else None
    return {"elements": len(sc), "segments": sum(len(t[2]) for t in sc), "fills": sum(len(t[3]) for t in sc),
            "texts": sum(len(t[4]) for t in sc), "items": sum(len(t[5]) for t in sc), "bbox": bbox, "digest": SC.scene_digest(sc)}


D.VIEW_SCOPED = D.VIEW_SCOPED | frozenset({scene_rows, scene_digest})


def _hit_bb(bb, box) -> bool:
    return bb is not None and not (bb[2] < box[0] or bb[0] > box[2] or bb[3] < box[1] or bb[1] > box[3])


class ViewScene(A):
    id: Id
    bbox: list[VNum] | None = Field(None, min_length=4, max_length=4, description="타일: [x0,y0,x1,y1] 뷰 좌표. 경계 상자가 이 사각형과 겹치는 요소만(요소는 자르지 않고 통째로)")
    ids: list[Id] | None = Field(None, min_length=1, max_length=IDS_MAX, description="이 요소들만(바뀐 요소만 다시 받을 때: view.affected·history.affected 의 id)")
    kinds: list[Annotated[str, Field(min_length=1, max_length=32)]] | None = Field(None, min_length=1, max_length=32, description="이 종류만(wall, door, finst, slab, draft, dim, view ...)")
    decimals: int = Field(1, ge=0, le=3, description="좌표 소수 자릿수(mm). 화면에는 1 이면 충분하고 토큰이 준다")
    limit: int = Field(200, ge=1, le=1000, description="최대 행 수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor (행 번호). 같은 rev·같은 거르기 인자로 이어 읽는다")


@command("view.scene", kind="query", risk="read", group="view",
         summary="요소 id 가 붙은 뷰 그림(GUI·집기용)",
         args=ViewScene, idempotent=True, since="0.2",
         examples=[{"id": F.V1}, {"id": F.V1, "bbox": [-500, -500, 6500, 500], "decimals": 0}])
def view_scene(ctx, a: ViewScene):
    r = ctx.row(a.id, "view")
    g = ctx.g
    rows = g.q(scene_rows, a.id, a.decimals)
    if a.bbox is not None or a.ids is not None or a.kinds is not None:
        box = None if a.bbox is None else (min(a.bbox[0], a.bbox[2]), min(a.bbox[1], a.bbox[3]), max(a.bbox[0], a.bbox[2]), max(a.bbox[1], a.bbox[3]))
        want, kinds = (set(a.ids) if a.ids is not None else None), (set(a.kinds) if a.kinds is not None else None)
        rows = [x for x in rows if (want is None or x["id"] in want) and (kinds is None or x["kind"] in kinds) and (box is None or _hit_bb(x["bb"], box))]
    start = _dec_cursor(a.cursor, "int") or 0
    window = list(rows[start:start + a.limit])
    head = {"id": a.id, "type": r["params"]["type"], "scale": r["params"]["scale"], "summary": g.q(scene_digest, a.id),
            "columns": {"lines": ["class", "x0", "y0", "x1", "y1"], "fills": "[[x,y],...]", "texts": ["x", "y", "text", "height", "anchor"]}}
    D_omit = g.q(D.view_omitted, a.id)
    if D_omit["foreign"] or D_omit["detached"]:
        head["omitted"] = D_omit
    items, cut = _fit(window, base=head, cursor_of=lambda i: _enc_cursor(start + i + 1))
    nxt = _enc_cursor(start + len(items)) if start + len(items) < len(rows) else None
    return {**head, "items": items, "page": _page(nxt, len(rows), cut)}


def _tol(ctx, vid, tol):
    return tol if tol is not None else PAPER_TOL_MM * float(ctx.st.el[vid]["params"]["scale"])


class ViewPick(A):
    id: Id
    x: VNum
    y: VNum
    tol: Tol | None = None
    limit: int = Field(10, ge=1, le=100, description="최대 개수(가까운 순)")


@command("view.pick", kind="query", risk="read", group="view",
         summary="뷰 좌표 한 점에 걸리는 요소(가까운 순)",
         args=ViewPick, idempotent=True, since="0.2",
         examples=[{"id": F.V1, "x": 3000, "y": 0}, {"id": F.V1, "x": 6000, "y": 2000, "tol": 50, "limit": 3}])
def view_pick(ctx, a: ViewPick):
    ctx.row(a.id, "view")
    tol = _tol(ctx, a.id, a.tol)
    hits = SC.pick(ctx.g.q(SC.view_scene, a.id), a.x, a.y, tol, a.limit)
    rows = [{"id": e, "kind": k, "dist": d, "hit": what} for d, k, e, what in hits]
    items, cut = _fit(rows, base={"id": a.id, "x": a.x, "y": a.y, "tol": tol}, cursor_of=lambda i: "")
    return {"id": a.id, "x": a.x, "y": a.y, "tol": tol, "items": items, "page": _page(None, len(rows), cut)}


SnapKind = Literal["end", "mid", "int", "axis", "perp", "ortho"]


class GeomSnap(A):
    view: Id
    x: VNum
    y: VNum
    tol: Tol | None = None
    kinds: list[SnapKind] | None = Field(None, min_length=1, max_length=6, description="찾을 후보 종류. 생략하면 전부. perp·ortho 는 from 이 있어야 나온다")
    from_: VPt | None = Field(None, alias="from", description="그리기 기준점(앞 점). 수선의 발(perp)·직교(ortho, 가로·세로) 후보에 쓴다")
    limit: int = Field(10, ge=1, le=100, description="최대 후보 수(가까운 순, 같은 거리면 end > mid > int > axis > perp > ortho)")


@command("geom.snap", kind="query", risk="read", group="geom",
         summary="점 근처의 스냅 후보(끝점·중점·교점·축선·수선·직교)",
         args=GeomSnap, idempotent=True, since="0.2",
         examples=[{"view": F.V1, "x": 6020, "y": 30}, {"view": F.V1, "x": 3010, "y": 1900, "from": [0, 2000], "kinds": ["ortho", "perp"]}])
def geom_snap(ctx, a: GeomSnap):
    r = ctx.row(a.view, "view")
    tol = _tol(ctx, a.view, a.tol)
    kinds = set(a.kinds or SC.SNAP_KINDS)
    axes = []
    if "axis" in kinds and r["params"]["type"] == "plan":   # 벽 축선(그리기 이어 붙이기의 기준): 평면 뷰에서만
        for w in ctx.g.q(D.scope_walls, a.view):
            ax = ctx.st.el[w]["params"].get("axis")
            if ax:
                axes.append((w, *ax))
    cands = SC.snap(ctx.g.q(SC.view_scene, a.view), a.x, a.y, tol, kinds, a.from_, axes, a.limit)
    rows = [{"kind": k, "x": px, "y": py, "dist": d, "ids": list(ids)} for d, k, px, py, ids in cands]
    items, cut = _fit(rows, base={"view": a.view, "tol": tol, "best": rows[0] if rows else None}, cursor_of=lambda i: "")
    return {"view": a.view, "tol": tol, "best": items[0] if items else None, "items": items, "page": _page(None, len(rows), cut)}
