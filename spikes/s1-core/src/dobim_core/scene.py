# SPDX-License-Identifier: MPL-2.0
"""평면 GUI 가 쓰는 뷰 장면(B1, 설계: docs/2d-editing.md 2.3절, ADR-0016).

view_lines 는 모든 요소의 선분을 한데 병합해 어느 요소의 선인지 잃는다. 여기서는 같은 요소별 질의(plan_wall_lines·fam_lines ...)를
요소마다 따로 모아 **요소 id 가 붙은** 선분·채움·문자로 돌려준다(GUI·에이전트가 같이 쓴다). 화면과 도면이 같은 원천이 되도록
요소별 선분에도 view_lines 와 같은 규칙을 건다:
  - 요소 하나 안에서는 merge_lines 로 정규화한다(같은 직선 위 겹친 선분 합치기, 그 요소의 절단선이 덮는 낮은 등급 지우기).
  - 요소 사이에서는 어느 요소의 절단선(cut)이 덮는 다른 요소의 낮은 등급 선분을 지운다(view_lines 의 병합과 같은 결과).
  - 잘라내기(crop)는 view_lines 와 같은 함수(_crop_lines·_crop_polys)로 자른다.
  그래서 등급·직선별로 모은 덮는 구간은 view_lines 와 같다(시험 test_gui_scene.py). 다른 점은 요소 사이 같은 등급의 맞닿은 선분을
  하나로 잇지 않는다는 것뿐이다(요소마다 따로 그려야 선택 강조가 된다).

모든 함수는 그래프 질의(g.q)라서 바뀐 요소만 다시 계산된다(증분 = 전체 계산, 시험). 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from math import hypot, sqrt

from dobim_core import annot as AN
from dobim_core import drafting as DRF
from dobim_core import drawing as D
from dobim_core import queries as Q

SNAP_KINDS = ("end", "mid", "int", "axis", "perp", "ortho")   # geom.snap 후보 종류(앞일수록 같은 거리에서 먼저)
SNAP_PAIR_MAX = 200     # 교점을 찾을 때 고려하는 근처 선분 수 상한(근처 선분이 이보다 많으면 가까운 것부터)


def _key(cls_seg):
    """선분 하나의 직선 키·방향·구간(merge_lines 와 같은 정규화)."""
    _, x0, y0, x1, y1 = cls_seg[:5]
    dx, dy = x1 - x0, y1 - y0
    length = sqrt(dx * dx + dy * dy)
    if length < D.SEG_MIN:
        return None
    ux, uy = dx / length, dy / length
    if ux < -1e-9 or (abs(ux) <= 1e-9 and uy < 0.0):
        ux, uy = -ux, -uy
    off = -uy * x0 + ux * y0
    key = (round(ux, 5), round(uy, 5), round(off, 2))
    t0, t1 = sorted((ux * x0 + uy * y0, ux * x1 + uy * y1))
    return key, ux, uy, off, t0, t1


def _union(ivs):
    m = []
    for a, b in sorted(ivs):
        if m and a <= m[-1][1] + D.EPS:
            m[-1][1] = max(m[-1][1], b)
        else:
            m.append([a, b])
    return m


def _minus_cut(lines, cuts):
    """요소 하나의 선분(merge_lines 결과)에서 다른 요소들의 절단선이 덮는 낮은 등급 부분을 지운다."""
    out = []
    for ln in lines:
        if ln[0] == "cut":
            out.append(ln)
            continue
        k = _key(ln)
        if k is None or k[0] not in cuts:
            out.append(ln)
            continue
        key, ux, uy, off, t0, t1 = k
        for a, b in D._subtract([[t0, t1]], cuts[key]):
            if b - a > D.SEG_MIN:
                out.append((ln[0], round(-uy * off + ux * a, 3), round(ux * off + uy * a, 3),
                            round(-uy * off + ux * b, 3), round(ux * off + uy * b, 3)))
    return tuple(out)


def _round_polys(polys):
    return tuple(tuple((round(p[0], 3), round(p[1], 3)) for p in poly) for poly in polys)


def _el_parts(g, v, cfg, kind, e):
    """요소 하나의 (선분들, 채움들, 문자들). view_lines·view_fills·view_labels 와 같은 요소별 질의를 쓴다."""
    plan = cfg[0] == "plan"
    lines, fills, texts = [], [], []
    if kind == "wall":
        lines.extend(g.q(D.plan_wall_lines if plan else D.vwall_lines, v, e))
        if plan:
            lines.extend(g.q(D.plan_fire_lines, v, e))
            lines.extend(g.q(D.plan_wall_glazing, v, e))
            fills.extend(g.q(D.plan_pieces, v, e)[0])
            texts.extend(g.q(D.plan_fire_labels, v, e))
        else:
            fills.extend(g.q(D.vcut_rects, v, e))
            if cfg[0] == "elev":
                fin = g.q(D.elev_finish, v, e)
                lines.extend(fin[0])
                texts.extend(fin[1])
    elif kind == "fam":
        lines.extend(g.q(D.fam_lines, v, e))
        fills.extend(g.q(D.plan_fam_parts, v, e)[1] if plan else g.q(D.vfam_cut_rects, v, e))
    elif kind == "slab":
        lines.extend(g.q(D.slab_lines, v, e))
        fills.extend(g.q(D.plan_slab_parts, v, e)[1] if plan else g.q(D.slab_cut_rects, v, e))
        if plan:
            texts.extend(g.q(D.plan_roof_marks, v, e)[1])
    elif kind == "stair":
        lines.extend(g.q(D.stair_lines, v, e))
        if not plan:
            fills.extend(g.q(D.stair_cut_rects, v, e))
    elif kind == "railing":
        lines.extend(g.q(D.railing_lines, v, e))
    elif kind == "elevator":
        lines.extend(g.q(D.elevator_lines, v, e))
        if plan:
            texts.extend(g.q(D.plan_elevator_labels, v, e))
    elif kind == "roof":
        lines.extend(g.q(D.roof_lines, v, e))
        if plan:
            texts.extend(g.q(D.roof_plan_parts, v, e)[1])
        else:
            fills.extend(g.q(D.roof_cut_rects, v, e))
    elif kind == "solid":
        lines.extend(g.q(D.solid_lines, v, e))
    elif kind == "foreign":
        lines.extend(g.q(D.foreign_lines, v, e))
        fills.extend(g.q(D.foreign_fills, v, e))   # 외래 절단면 채움(외래 IFC 2차, view_fills 와 같은 질의)
    elif kind == "fspace":
        texts.extend(g.q(D.foreign_space_labels, v, e))   # 외래 공간(IfcSpace) 실명·면적(외래 IFC 2차, view_labels 와 같은 질의)
    elif kind == "grid":
        gm = g.q(D.grid_marks, v, e)
        lines.extend(gm[0])
        texts.extend(gm[1])
    elif kind == "space":
        texts.extend(g.q(D.plan_space_labels, v, e))
    return lines, fills, texts


def hosted_symbols(g, v, w):
    """평면에서 벽 w 의 선분 중 호스팅 요소(문·창·패밀리 문)의 기호 선분: ((요소 id, 선분들), ...).
    drawing.plan_wall_lines 가 벽 선분에 섞어 그리는 기호를 같은 함수(_door_symbol·_plan_items)로 요소별로 다시 만든다.
    view_scene 은 이 선분을 벽에서 빼서 그 요소의 것으로 돌린다(정확히 같은 선분만 빼므로 어긋나도 선이 사라지지는 않는다)."""
    cfg = g.q(D.view_cfg, v)
    if cfg is None or cfg[0] != "plan" or D.is_rcp(cfg):   # 반사 천장 평면(W1 1-C 3차)에는 문·창 기호가 없다
        return ()
    cut, _ = g.q(D.plan_pieces, v, w)
    sol = g.q(D.wall_solid, w)
    if not (cut or sol[7] - D.EPS <= cfg[2] <= sol[8] + D.EPS):
        return ()
    start, end, _ = g.q(Q.wall_trim, w)
    p = Q.el(g, w)["params"]
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    zb, zt = elev + D.WT.base(p), elev + D._top_rel(p)
    out = []
    for d in g.q(Q.wall_hosted, w):
        row = Q.el(g, d)
        if row is None:
            continue
        lines = []
        if row["kind"] == "door":   # drawing._openings(doors_only) 와 같은 자르기
            box = g.q(D.hosted_opening, d, elev)
            if box is None:
                continue
            op = (max(start, box[0]), min(end, box[1]), max(zb, box[2]), min(zt, box[3]))
            if op[1] - op[0] > D.EPS and op[3] - op[2] > D.EPS and op[2] <= cfg[2] + D.EPS and cfg[2] <= op[3] + D.EPS:
                lines = D._door_symbol(sol, op)
        elif row["kind"] == "finst":   # drawing.hosted_fam 과 같은 기호
            spec = g.q(Q.ifc_spec, d)
            c = spec.get("cut")
            if not c:
                continue
            fzb = elev + spec["elevation"]
            plan = D._freeze_plan(spec["plan"])
            if D.FS.hosted_flipped(spec.get("rotation")):
                plan = D._flip_plan(plan)
            if fzb + c["z"][0] <= cfg[2] + D.EPS and cfg[2] <= fzb + c["z"][1] + D.EPS:
                lines = D._plan_items(plan, lambda u, vv, _o=spec["offset"]: D._world(sol, _o + u, vv))
        if lines:
            out.append((d, tuple(lines)))
    return tuple(out)


def _view_parts(g, v, cfg):
    """특정 요소에 붙지 않는 뷰 단위 그림(대지 뷰 전체, 입면 표지판, 대지 단면 지반선, 치수 문자)."""
    lines, texts = [], []
    if cfg[0] == "site":
        lines.extend(g.q(D.site_lines, v))
        texts.extend(g.q(D.site_labels, v))
    elif cfg[0] == "elev":
        lines.extend(g.q(D.site_sign_lines, v))
        texts.extend(g.q(D.site_sign_labels, v))
    elif cfg[0] == "sec":
        lines.extend(g.q(D.site_section_lines, v))
        texts.extend(g.q(D.site_section_labels, v))
    if cfg[0] == "plan" and len(cfg) == 6:   # 반사 천장 평면의 천장선·면적 평면의 면적 그림(W1 1-C 3차)
        from dobim_core import viewplans as VP
        lines.extend(g.q(VP.mode_lines, v))
        if cfg[5] != "rcp":
            texts.extend(g.q(VP.area_labels, v))
    if "dims" in g.q(D.view_opts, v):
        texts.extend(g.q(D.view_dim_labels, v))
    return lines, texts


def _bbox(lines, fills, texts, extra=()):
    xs, ys = [], []
    for ln in lines:
        xs += [ln[1], ln[3]]
        ys += [ln[2], ln[4]]
    for poly in fills:
        xs += [p[0] for p in poly]
        ys += [p[1] for p in poly]
    for t in texts:
        xs.append(t[0])
        ys.append(t[1])
    for x, y in extra:
        xs.append(x)
        ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _crop_texts(texts, c):
    return [t for t in texts if c[0] - D.EPS <= t[0] <= c[2] + D.EPS and c[1] - D.EPS <= t[1] <= c[3] + D.EPS]


def view_scene(g, v):
    """뷰의 장면: ((종류, id, 선분들, 채움들, 문자들, 작도·주석 항목들, 경계 상자), ...) 를 종류·id 순으로.
    선분 = (등급, x0, y0, x1, y1), 채움 = ((x, y), ...), 문자 = (x, y, 문자, 높이 mm, 앵커).
    작도(draft) 항목은 drafting.draft_items, 주석(dim·tag)은 annot.annot_draw 의 그림 항목 그대로다(이미 id 가 붙어 있다).
    뷰 단위 그림(대지 뷰 전체·표지판·지반선·치수 문자)은 종류 "view", id = 뷰 id 로 담는다. 뷰가 없으면 빈 값."""
    cfg = g.q(D.view_cfg, v)
    if cfg is None:   # 모델이 없는 뷰: 범례 뷰(W1 1-C 3차)의 뷰 단위 그림 하나 + 2D 작도(제도 뷰 W1 연장 VIEW-07). 둘 다 없으면 빈 값
        from dobim_core import viewplans as VP
        ll, lt = g.q(VP.legend_lines, v), g.q(VP.legend_labels, v)
        out = [("view", v, ll, (), lt, (), _bbox(ll, (), lt))] if ll or lt else []
        return _with_items(g, v, out, ())
    crop = g.q(D.view_opts, v).get("crop")
    groups = []   # (종류, id, 선분 원본, 채움, 문자)
    if cfg[0] != "site":
        scopes = (("wall", D.scope_walls), ("fam", D.scope_fams), ("slab", D.scope_slabs), ("stair", D.scope_stairs),
                  ("railing", D.scope_railings), ("elevator", D.scope_elevators), ("roof", D.scope_roofs),
                  ("solid", D.scope_solids), ("foreign", D.scope_foreign), ("space", D.scope_spaces), ("fspace", D.scope_foreign_spaces))
        for kind, fn in scopes:
            if kind in ("space", "fspace") and cfg[0] != "plan":
                continue
            for e in g.q(fn, v):
                lines, fills, texts = g.q(_el_parts_q, v, kind, e)
                if kind == "wall":
                    hs = g.q(hosted_symbols, v, e)
                    if hs:   # 문·창 기호는 그 요소의 것으로(벽 선분에서 같은 선분을 하나씩 뺀다)
                        rest = list(lines)
                        for d, sym in hs:
                            mine = []
                            for ln in sym:
                                if ln in rest:
                                    rest.remove(ln)
                                    mine.append(ln)
                            if mine:
                                groups.append((Q.el(g, d)["kind"], d, tuple(mine), (), ()))
                        lines = tuple(rest)
                groups.append((Q.el(g, e)["kind"], e, lines, fills, texts))
        for gr in g.q(D.all_grids):
            ln, _, tx = g.q(_el_parts_q, v, "grid", gr)
            if ln or tx:
                groups.append(("grid", gr, ln, (), tx))
    vl, vt = _view_parts(g, v, cfg)
    vdims = g.q(D.site_dims, v) + g.q(D.view_dims, v)   # 뷰가 계산해 붙이는 치수(대지 경계·계단·승강기): 요소가 아니라 뷰에 딸린다
    if vl or vt:
        groups.append(("view", v, tuple(vl), (), tuple(vt)))
    # 요소별 정규화·잘라내기 뒤, 모든 요소의 절단선이 덮는 구간을 모은다
    normed = []
    cuts: dict = {}
    for kind, e, lines, fills, texts in groups:
        ml = D.merge_lines(lines)
        if crop is not None:
            ml = D._crop_lines(ml, crop)
        for ln in ml:
            if ln[0] == "cut":
                k = _key(ln)
                if k is not None:
                    cuts.setdefault(k[0], []).append((k[4], k[5]))
        fl = _round_polys(fills)
        if crop is not None:
            fl = D._crop_polys(fl, crop)
            texts = _crop_texts(texts, crop)
        normed.append((kind, e, ml, fl, tuple(texts)))
    cuts = {k: _union(ivs) for k, ivs in cuts.items()}
    out = []
    for kind, e, ml, fl, tx in normed:
        ml = _minus_cut(ml, cuts) if cuts else ml
        if ml or fl or tx:
            out.append((kind, e, ml, fl, tx, (), _bbox(ml, fl, tx)))
    return _with_items(g, v, out, vdims)


def _with_items(g, v, out, vdims):
    """2D 작도·주석: 요소 id 별로 묶는다(이미 그림 항목에 id 가 있다). out(요소별 장면 행)에 더해 종류·id 순으로 돌려준다."""
    by: dict = {}
    for it in g.q(DRF.view_drafts, v):
        by.setdefault(("draft", it[1]), []).append(it)
    for it in g.q(AN.view_annots, v):
        by.setdefault(("dim" if it[0] == "adim" else (it[0] if it[0] in ("dim", "tag") else it[2]), it[1]), []).append(it)
    if vdims:
        by[("view", v)] = list(vdims)
    for (kind, e), items in by.items():
        pts = [p for it in items for p in (DRF.item_points(it) if kind == "draft" else _annot_points(it))]
        prev = next((i for i, t in enumerate(out) if t[0] == kind and t[1] == e), None)
        if prev is not None:   # 뷰 단위 그림(선분·문자)에 뷰 치수를 더한다
            t = out[prev]
            out[prev] = t[:5] + (tuple(items), _bbox(t[2], t[3], t[4], pts))
        else:
            out.append((kind, e, (), (), (), tuple(items), _bbox((), (), (), pts)))
    out.sort(key=lambda t: (t[0], t[1]))
    return tuple(out)


def _annot_segs(it):
    """주석 그림 항목의 선분 (x0, y0, x1, y1) 들: 치수는 보조선 둘·치수선, 태그는 기준점에서 문자까지 지시선."""
    if it[0] == "dim":
        return [tuple(it[2]), tuple(it[3]), tuple(it[4])]
    if it[0] == "tag":
        return [tuple(it[2]) + tuple(it[3])]
    if it[0] == "adim":   # 각도·반지름·지름 치수(W1 1-B 2차)
        return [tuple(s) for s in it[2]]
    return []


def _annot_points(it):
    """주석 그림 항목의 대표 점(경계 상자·집기용): 선분 끝점과 문자 위치. 고아·무효 주석은 놓인 자리 한 점."""
    if it[0] == "dim":
        return [p for s in _annot_segs(it) for p in ((s[0], s[1]), (s[2], s[3]))] + [tuple(it[6])]
    if it[0] == "tag":
        return [tuple(it[2]), tuple(it[3])]
    if it[0] == "adim":
        return [p for s in _annot_segs(it) for p in ((s[0], s[1]), (s[2], s[3]))] + [tuple(it[5])]
    return [(it[4], it[5])]


def _el_parts_q(g, v, kind, e):
    """요소 하나의 그림(그래프 노드: 그 요소의 입력이 바뀔 때만 다시 계산된다)."""
    lines, fills, texts = _el_parts(g, v, g.q(D.view_cfg, v), kind, e)
    return tuple(lines), tuple(fills), tuple(texts)


def scene_digest(scene) -> str:
    return hashlib.sha256(json.dumps(scene, separators=(",", ":"), ensure_ascii=False, default=list).encode("utf-8")).hexdigest()[:16]


# ================================================================ 집기·스냅(순수 기하)
def _seg_dist(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L2))
    return hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def _in_poly(px, py, poly) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > py) != (y1 > py) and px < x0 + (py - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _item_segs(kind, it):
    """작도·주석 그림 항목에서 집기·스냅에 쓸 선분 (x0, y0, x1, y1) 들."""
    if kind == "draft":
        if it[0] == "line":
            return [tuple(it[5:9])]
        if it[0] == "fill":
            poly = it[4]
            return [(poly[i][0], poly[i][1], poly[(i + 1) % len(poly)][0], poly[(i + 1) % len(poly)][1]) for i in range(len(poly))]
        return []
    return _annot_segs(it)


def pick(scene, x, y, tol, limit):
    """점 (x, y) 에서 tol 안에 걸리는 요소들: [(거리, 종류, id, 걸린 것)] 가까운 순(같으면 절단선 > 채움 > 나머지, 그다음 id).
    채움 다각형 안이면 거리 0, 문자는 기준점까지 거리. 경계 상자가 tol 밖인 요소는 건너뛴다."""
    hits = []
    for kind, e, lines, fills, texts, items, bb in scene:
        if bb is None or x < bb[0] - tol or x > bb[2] + tol or y < bb[1] - tol or y > bb[3] + tol:
            continue
        best = None   # (거리, 순위, 걸린 것)
        for ln in lines:
            d = _seg_dist(x, y, *ln[1:5])
            if d <= tol:
                c = (d, 0 if ln[0] in ("cut", "cutn") else 2, "line:" + ln[0])
                best = c if best is None or c < best else best
        for poly in fills:
            if _in_poly(x, y, poly):
                c = (0.0, 1, "fill")
                best = c if best is None or c < best else best
        for t in texts:
            d = hypot(x - t[0], y - t[1])
            if d <= tol:
                c = (d, 3, "text")
                best = c if best is None or c < best else best
        for it in items:
            for s in _item_segs(kind, it):
                d = _seg_dist(x, y, *s)
                if d <= tol:
                    c = (d, 2, kind)
                    best = c if best is None or c < best else best
            if kind == "draft" and it[0] == "fill" and _in_poly(x, y, it[4]):
                best = (0.0, 1, "fill") if best is None or (0.0, 1) < best[:2] else best
        if best is not None:
            hits.append((round(best[0], 3), best[1], e, kind, best[2]))
    hits.sort()
    return [(d, kind, e, what) for d, _, e, kind, what in hits[:limit]]


def _isect(a, b):
    """두 선분의 교점(평행이거나 안 만나면 None)."""
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return x1 + t * (x2 - x1), y1 + t * (y2 - y1)
    return None


def _foot(px, py, s):
    """점에서 선분(직선)으로 내린 수선의 발. 발이 선분 밖이면 None."""
    x0, y0, x1, y1 = s
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        return None
    t = ((px - x0) * dx + (py - y0) * dy) / L2
    if t < -1e-9 or t > 1 + 1e-9:
        return None
    return x0 + t * dx, y0 + t * dy


def snap(scene, x, y, tol, kinds, frm, axes, limit):
    """점 (x, y) 근처 스냅 후보: [(거리, 종류, x, y, 요소 id 들)] 가까운 순(같으면 SNAP_KINDS 순서).
    end 끝점, mid 중점, int 교점(요소 안·요소 사이), axis 벽 축선의 끝점·중점(axes: [(id, x0, y0, x1, y1)]),
    perp 기준점 frm 에서 근처 선분으로 내린 수선의 발, ortho frm 을 지나는 가로·세로선 위로 (x, y) 를 옮긴 점."""
    segs = []   # (거리, 선분, 요소 id)
    for kind, e, lines, fills, texts, items, bb in scene:
        if bb is None or x < bb[0] - tol or x > bb[2] + tol or y < bb[1] - tol or y > bb[3] + tol:
            continue
        cand = [tuple(ln[1:5]) for ln in lines]
        for poly in fills:
            cand += [(poly[i][0], poly[i][1], poly[(i + 1) % len(poly)][0], poly[(i + 1) % len(poly)][1]) for i in range(len(poly))]
        for it in items:
            cand += _item_segs(kind, it)
        for s in cand:
            d = _seg_dist(x, y, *s)
            if d <= tol:
                segs.append((d, s, e))
    segs.sort(key=lambda t: (t[0], t[2], t[1]))
    segs = segs[:SNAP_PAIR_MAX]
    out = {}

    def add(kind, px, py, ids):
        d = hypot(px - x, py - y)
        if d > tol:
            return
        k = (kind, round(px, 3), round(py, 3))
        prev = out.get(k)
        merged = tuple(sorted(set(ids) | set(prev[1] if prev else ())))
        out[k] = (round(d, 3), merged)

    if "end" in kinds or "mid" in kinds:
        for _, s, e in segs:
            if "end" in kinds:
                add("end", s[0], s[1], (e,))
                add("end", s[2], s[3], (e,))
            if "mid" in kinds:
                add("mid", (s[0] + s[2]) / 2, (s[1] + s[3]) / 2, (e,))
    if "int" in kinds:
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                p = _isect(segs[i][1], segs[j][1])
                if p is not None:
                    a, b = segs[i][1], segs[j][1]
                    ends = {(round(a[0], 3), round(a[1], 3)), (round(a[2], 3), round(a[3], 3))}
                    if (round(p[0], 3), round(p[1], 3)) in ends and (round(p[0], 3), round(p[1], 3)) in {(round(b[0], 3), round(b[1], 3)), (round(b[2], 3), round(b[3], 3))}:
                        continue   # 두 선분이 끝점끼리 만나는 곳은 끝점 후보가 이미 있다
                    add("int", p[0], p[1], (segs[i][2], segs[j][2]))
    if "axis" in kinds:
        for e, x0, y0, x1, y1 in axes:
            add("axis", x0, y0, (e,))
            add("axis", x1, y1, (e,))
            add("axis", (x0 + x1) / 2, (y0 + y1) / 2, (e,))
    if frm is not None:
        fx, fy = frm
        if "perp" in kinds:
            for _, s, e in segs:
                p = _foot(fx, fy, s)
                if p is not None:
                    add("perp", p[0], p[1], (e,))
        if "ortho" in kinds:
            add("ortho", fx, y, ())   # 세로선 위(x 를 기준점에 맞춤)
            add("ortho", x, fy, ())   # 가로선 위
    order = {k: i for i, k in enumerate(SNAP_KINDS)}
    res = sorted(((d, k[0], k[1], k[2], ids) for k, (d, ids) in out.items()), key=lambda t: (t[0], order[t[1]], t[2], t[3]))
    return res[:limit]


# 뷰를 지우면 뷰에 딸린 질의 노드를 치운다(drawing.forget_views 가 이 집합을 본다). 공용 파일(drawing.py)을 고치지 않으려고 여기서 늘린다
D.VIEW_SCOPED = D.VIEW_SCOPED | frozenset({view_scene, _el_parts_q, hosted_symbols})
