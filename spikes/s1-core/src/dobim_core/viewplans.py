# SPDX-License-Identifier: MPL-2.0
"""반사 천장 평면(VIEW-02)·면적 평면(VIEW-21)·범례 뷰(VIEW-09)의 그림 (W1 1-C 3차).

반사 천장 평면 : 평면 뷰의 선택 키 rcp. drawing 의 평면 질의가 절단면 위를 보게 바뀌고(is_rcp), 여기서는 천장선과 천장 높이 문자를 더한다.
                 천장 = 천장 높이(ceiling_height)가 있는 공간의 경계(레벨 + 천장 높이가 절단면 위 보이는 깊이 안일 때).
면적 평면      : 평면 뷰의 선택 키 area("gross" 바닥면적 = 그 층 벽 윤곽 합집합의 바깥선, "rooms" 실별 = 공간 경계).
                 평면 그림 위에 면적 경계(symbol)·해치(hatch)·면적 이름·값을 얹는다. 공간 이름·면적 문자는 면적 항목이 대신한다.
범례 뷰        : 뷰 종류 legend(legend = "symbols" 문·창·패밀리 기호, "materials" 재료 해치). 모델을 자르지 않고 모델에서 쓰는 종류를
                 행마다 기호 + 이름으로 늘어놓는다. view_cfg 가 None 이라 다른 모델 질의는 모두 빈 값이다.
모드가 없는 평면·다른 뷰는 이 모듈을 거치지 않는다(기존 도면 바이트 불변). 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from dobim_core import drawing as D
from dobim_core import queries as Q

AREA_HATCH = 4.0        # 면적 해치 간격(용지 mm)
LEG_ROW = 14.0          # 범례 행 최소 높이(용지 mm)
LEG_GAP = 6.0           # 범례 행 사이·기호와 이름 사이 간격(용지 mm)
LEG_SWATCH = (20.0, 8.0)   # 재료 견본 크기(용지 mm)
LEG_TEXT_H = 2.5
LEG_DOOR_WALL = 300.0   # 문 기호 양옆 벽 토막 길이(mm)
LEG_DOOR_T = 200.0      # 문 기호 벽 두께(mm)


# ---------------------------------------------------------------- 공통
def _ring_edges(ring):
    n = len(ring)
    return [(ring[i], ring[(i + 1) % n]) for i in range(n)]


def _hatch_edges(edges, k, step):
    """닫힌 선분 묶음(짝홀 규칙) 안의 해치: 선 k*x + y = c(세계 격자 간격의 정수배)를 긋는다. k = 1 또는 -1(45도·135도)."""
    dc = step * 1.4142135623730951
    fs = [(k * a[0] + a[1], k * b[0] + b[1]) for a, b in edges]
    if not fs:
        return []
    lo, hi = min(min(f) for f in fs), max(max(f) for f in fs)
    out = []
    m = D._floor(lo / dc) + 1
    while m * dc < hi:
        c = m * dc
        m += 1
        xs = []
        for (a, b), (fa, fb) in zip(edges, fs):
            if (fa <= c < fb) or (fb <= c < fa):
                t = (c - fa) / (fb - fa)
                xs.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
        xs.sort()
        for j in range(0, len(xs) - 1, 2):
            if abs(xs[j + 1][0] - xs[j][0]) > D.SEG_MIN:
                out.append(("hatch", xs[j][0], xs[j][1], xs[j + 1][0], xs[j + 1][1]))
    return out


def _union_directed(segs):
    """같은 방향·같은 직선 위에서 겹치는 선분을 합친다(맞닿은 벽 윤곽이 같은 바깥선을 두 번 내는 것을 한 번으로). 방향은 지킨다."""
    from math import sqrt
    groups: dict = {}
    for x0, y0, x1, y1 in segs:
        dx, dy = x1 - x0, y1 - y0
        ln = sqrt(dx * dx + dy * dy)
        if ln < D.SEG_MIN:
            continue
        ux, uy = round(dx / ln, 6), round(dy / ln, 6)
        key = (ux, uy, round(-uy * x0 + ux * y0, 3))
        t0, t1 = ux * x0 + uy * y0, ux * x1 + uy * y1
        groups.setdefault(key, []).append((t0, t1))
    out = []
    for (ux, uy, off), ivs in sorted(groups.items()):
        ivs.sort()
        cur = None
        merged = []
        for a, b in ivs:
            if cur is not None and a <= cur[1] + D.EPS:
                cur[1] = max(cur[1], b)
            else:
                cur = [a, b]
                merged.append(cur)
        for a, b in merged:   # 점 = t*(ux,uy) + off*(-uy,ux)
            out.append((a * ux - off * uy, a * uy + off * ux, b * ux - off * uy, b * uy + off * ux))
    return tuple(out)


def _ch_text(ch) -> str:
    return f"CH {ch:,.0f}"


# ---------------------------------------------------------------- 반사 천장 평면
def _ceiling_z(g, s):
    ch = Q.el(g, s)["params"].get("ceiling_height")
    if ch is None:
        return None, None
    return g.q(Q.level_info, g.q(Q.space_level, s))[1] + ch, ch


def mode_space_labels(g, cfg, s, info):
    """모드가 있는 평면의 공간 문자. 반사 천장 평면: 실명 + 천장 높이(천장이 보일 때). 면적 평면: 없음(면적 항목이 대신한다)."""
    if cfg[5] != "rcp":
        return ()
    holes = g.q(D.space_holes, s)
    cx, cy = D.label_point(info[0], holes) if holes else D.polygon_centroid(info[0])
    sc = cfg[4]
    z, ch = _ceiling_z(g, s)
    out = [(round(cx, 3), round(cy + 0.9 * sc, 3), info[1], D.LABEL_NAME_H, "middle")]
    if z is not None and cfg[2] - D.EPS <= z <= cfg[2] + cfg[3] + D.EPS:
        out.append((round(cx, 3), round(cy - 2.0 * sc, 3), _ch_text(ch), D.LABEL_SUB_H, "middle"))
    return tuple(out)


def rcp_ceilings(g, v):
    """반사 천장 평면의 천장선: 천장이 절단면 위 보이는 깊이 안에 있는 공간의 경계·섬 구멍(투영선)."""
    cfg = g.q(D.view_cfg, v)
    out = []
    for s in g.q(D.scope_spaces, v):
        z, _ = _ceiling_z(g, s)
        if z is None or not cfg[2] - D.EPS <= z <= cfg[2] + cfg[3] + D.EPS:
            continue
        for ring in (g.q(D.space_info, s)[0],) + g.q(D.space_holes, s):
            out += [("proj", a[0], a[1], b[0], b[1]) for a, b in _ring_edges(ring)]
    return tuple(out)


# ---------------------------------------------------------------- 면적 평면
def area_items(g, v):
    """면적 평면의 면적 항목: ((이름, 경계 선분들, 면적 mm2, 문자 위치), ...). 면적 평면이 아니면 빈 값.
    gross: 그 평면이 자르는 벽(scope_walls) 바닥 윤곽 합집합의 바깥선(외벽 바깥면 기준) 하나. rooms: 공간마다(섬 구멍은 뺀다)."""
    cfg = g.q(D.view_cfg, v)
    if cfg is None or len(cfg) != 6 or cfg[5] not in ("gross", "rooms"):
        return ()
    out = []
    if cfg[5] == "gross":
        polys = []
        for w in g.q(D.scope_walls, v):
            x, y, c, s, _ = g.q(Q.wall_frame, w)
            poly = [(x + u * c - vv * s, y + u * s + vv * c) for u, vv in g.q(Q.wall_footprint, w)]
            if D._area2(poly) < 0:
                poly.reverse()
            polys.append(tuple(poly))
        segs = _union_directed(D.outline_of(tuple(polys))) if polys else ()
        a2 = sum(x0 * y1 - x1 * y0 for x0, y0, x1, y1 in segs)
        if a2 > 1.0:
            cx = sum((x0 + x1) * (x0 * y1 - x1 * y0) for x0, y0, x1, y1 in segs) / (3.0 * a2)
            cy = sum((y0 + y1) * (x0 * y1 - x1 * y0) for x0, y0, x1, y1 in segs) / (3.0 * a2)
            out.append(("바닥면적", tuple(((x0, y0), (x1, y1)) for x0, y0, x1, y1 in segs), a2 / 2.0, (cx, cy)))
    else:
        for s in g.q(D.scope_spaces, v):
            info = g.q(D.space_info, s)
            holes = g.q(D.space_holes, s)
            edges = []
            for ring in (info[0],) + holes:
                edges += _ring_edges(ring)
            area = (abs(D._area2(info[0])) - sum(abs(D._area2(h)) for h in holes)) / 2.0
            at = D.label_point(info[0], holes) if holes else D.polygon_centroid(info[0])
            out.append((info[1], tuple(edges), area, at))
    return tuple(out)


def _area_total_at(items, sc):
    xs = [p[0] for it in items for e in it[1] for p in e]
    ys = [p[1] for it in items for e in it[1] for p in e]
    return ((min(xs) + max(xs)) / 2.0, min(ys) - 8.0 * sc)


def area_lines(g, v):
    """면적 경계(symbol)와 면적마다 번갈아 45·135도 해치(hatch)."""
    cfg = g.q(D.view_cfg, v)
    out = []
    for i, (_n, edges, _a, _at) in enumerate(g.q(area_items, v)):
        out += [("symbol", a[0], a[1], b[0], b[1]) for a, b in edges]
        out += _hatch_edges(list(edges), 1.0 if i % 2 == 0 else -1.0, AREA_HATCH * cfg[4])
    return tuple(out)


def area_labels(g, v):
    """면적 이름·값 문자, 실별(rooms)이면 아래에 합계."""
    cfg = g.q(D.view_cfg, v)
    items = g.q(area_items, v)
    sc = cfg[4] if cfg is not None else 100
    out = []
    for name, _e, area, (cx, cy) in items:
        out.append((round(cx, 3), round(cy + 0.9 * sc, 3), name, D.LABEL_NAME_H, "middle"))
        out.append((round(cx, 3), round(cy - 2.0 * sc, 3), f"{area / 1.0e6:.2f}㎡", D.LABEL_SUB_H, "middle"))
    if items and cfg[5] == "rooms":
        x, y = _area_total_at(items, sc)
        out.append((round(x, 3), round(y, 3), f"합계 {len(items)}실 {sum(it[2] for it in items) / 1.0e6:.2f}㎡", D.LABEL_NAME_H, "middle"))
    return tuple(out)


def mode_lines(g, v):
    """모드가 있는 평면이 더하는 선분(drawing.view_lines 가 병합 전에 더한다)."""
    cfg = g.q(D.view_cfg, v)
    if cfg is None or len(cfg) != 6:
        return ()
    return g.q(rcp_ceilings, v) if cfg[5] == "rcp" else g.q(area_lines, v)


# ---------------------------------------------------------------- 범례 뷰
def _legend_params(g, v):
    row = Q.el(g, v)
    if row is None or row["kind"] != "view" or row["params"]["type"] != "legend":
        return None
    return row["params"]


def _ids(g, kind):
    out = []
    for b in range(Q.NB):
        out.extend(Q.bucket_ids(g, kind, b))
    return sorted(out)


def _symbol_rows(g):
    """기호 범례 행: (이름, 개수, 선분들(지역 좌표)). 문은 폭·높이별, 패밀리 인스턴스(창·패밀리 문·기둥 등)는 패밀리 유형별."""
    doors: dict = {}
    for d in _ids(g, "door"):
        p = Q.el(g, d)["params"]
        doors.setdefault((p["width"], p["height"]), []).append(d)
    rows = []
    for (wd, ht), ds in sorted(doors.items()):
        sol = (0.0, 0.0, 1.0, 0.0, LEG_DOOR_T / 2.0)   # 가상의 벽(지역 = 세계): drawing._door_symbol 을 그대로 쓴다
        h = LEG_DOOR_T / 2.0
        segs = list(D._door_symbol(sol, (0.0, wd)))
        for x0, x1 in ((-LEG_DOOR_WALL, 0.0), (wd, wd + LEG_DOOR_WALL)):
            segs += [("cut", x0, -h, x1, -h), ("cut", x0, h, x1, h), ("cut", x0 if x0 < 0 else x1, -h, x0 if x0 < 0 else x1, h)]
        segs += [("cut", 0.0, -h, 0.0, h), ("cut", wd, -h, wd, h)]
        rows.append((f"문 {wd:,.0f}×{ht:,.0f}", len(ds), segs))
    types: dict = {}
    for e in _ids(g, "finst"):
        if Q.el(g, e)["origin"] != "authored":
            continue
        types.setdefault(g.q(Q.finst_type, e), []).append(e)
    for t, es in sorted(types.items()):
        spec = g.q(Q.ifc_spec, es[0])
        if spec.get("error") or not spec.get("plan"):
            continue
        segs = D._plan_items(D._freeze_plan(spec["plan"]), lambda u, vv: (u, vv))
        trow = Q.el(g, t)
        rows.append((trow["params"]["name"] if trow is not None else t[:8], len(es), segs))
    return rows


def _materials(g):
    names = set()
    for kind in ("wall", "slab"):
        for e in _ids(g, kind):
            p = Q.el(g, e)["params"]
            if "layers" in p:
                names.update(x["material"] for x in p["layers"])
            else:
                m = Q.material(g, e)
                if isinstance(m, str) and m:
                    names.add(m)
    return sorted(names)


def legend_rows(g, v):
    """범례 뷰의 행: ((문자, 개수 또는 None, 선분들(뷰 좌표), 문자 위치), ...). 범례가 아니면 빈 값.
    행은 위에서 아래로(y 가 줄어든다), 기호 열은 x = 0 에서 시작하고 이름은 기호 열 오른쪽에 붙는다."""
    p = _legend_params(g, v)
    if p is None:
        return ()
    sc = p["scale"]
    raw = []
    if p["legend"] == "symbols":
        for name, n, segs in _symbol_rows(g):
            if segs:
                raw.append((name, n, segs))
    else:
        sw, sh = LEG_SWATCH[0] * sc, LEG_SWATCH[1] * sc
        for m in _materials(g):
            segs = [("symbol", 0.0, 0.0, sw, 0.0), ("symbol", sw, 0.0, sw, sh), ("symbol", sw, sh, 0.0, sh), ("symbol", 0.0, sh, 0.0, 0.0)]
            segs += D._hatch_rect(0.0, sw, 0.0, sh, D._hatch_pattern(m), D.LAYER_HATCH_STEP * sc, [])
            raw.append((m, None, segs))
    boxes = []
    for _n, _c, segs in raw:
        xs = [c for s in segs for c in (s[1], s[3])]
        ys = [c for s in segs for c in (s[2], s[4])]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    col = max((b[2] - b[0] for b in boxes), default=0.0)
    out, top = [], 0.0
    for (name, n, segs), b in zip(raw, boxes):
        hgt = max(b[3] - b[1], LEG_ROW * sc)
        dx, dy = -b[0], top - (hgt + (b[3] - b[1])) / 2.0 - b[1]   # 기호를 행 높이 가운데에
        moved = tuple((s[0], round(s[1] + dx, 3), round(s[2] + dy, 3), round(s[3] + dx, 3), round(s[4] + dy, 3)) for s in segs)
        text = name if n is None else f"{name} ({n})"   # 문자는 가운데 맞춤으로만 그려지므로(내보내기 공통) 폭의 절반만큼 민다
        out.append((text, n, moved, (round(col + LEG_GAP * sc + D._text_w(text, LEG_TEXT_H) * sc / 2.0, 3), round(top - hgt / 2.0 - 0.4 * LEG_TEXT_H * sc, 3))))
        top -= hgt + LEG_GAP * sc
    return tuple(out)


def legend_lines(g, v):
    return D.merge_lines([s for r in g.q(legend_rows, v) for s in r[2]]) if _legend_params(g, v) is not None else ()


def legend_labels(g, v):
    p = _legend_params(g, v)
    if p is None:
        return ()
    rows = g.q(legend_rows, v)
    if not rows:
        return ((0.0, 0.0, "(범례에 넣을 항목이 모델에 없다)", LEG_TEXT_H, "middle"),)
    return tuple((at[0], at[1], text, LEG_TEXT_H, "middle") for text, _n, _s, at in rows)


# 뷰 하나에 딸린 질의 노드(뷰를 지우면 치운다)
D.VIEW_SCOPED = D.VIEW_SCOPED | frozenset({rcp_ceilings, area_items, area_lines, area_labels, mode_lines, legend_rows, legend_lines, legend_labels})
