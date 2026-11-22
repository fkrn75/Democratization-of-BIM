# SPDX-License-Identifier: MPL-2.0
"""반사 천장 평면(VIEW-02)·면적 평면(VIEW-21)·범례 뷰(VIEW-09)의 그림 (W1 1-C 3차).

반사 천장 평면 : 평면 뷰의 선택 키 rcp. drawing 의 평면 질의가 절단면 위를 보게 바뀌고(is_rcp), 여기서는 천장선과 천장 높이 문자를 더한다.
                 천장 = 천장 높이(ceiling_height)가 있는 공간의 경계(레벨 + 천장 높이가 절단면 위 보이는 깊이 안일 때).
면적 평면      : 평면 뷰의 선택 키 area("gross" 바닥면적 = 그 층 벽 윤곽 합집합의 바깥선, "rooms" 실별 = 공간 경계,
                 "legal" 법정 바닥면적 = 그 층 벽 중심선 영역 + 그 층의 법정 면적 객체(larea, W1 연장 ARC-36)).
                 평면 그림 위에 면적 경계(symbol)·해치(hatch)·면적 이름·값을 얹는다. 공간 이름·면적 문자는 면적 항목이 대신한다.
범례 뷰        : 뷰 종류 legend(legend = "symbols" 문·창·패밀리 기호, "materials" 재료 해치). 모델을 자르지 않고 모델에서 쓰는 종류를
                 행마다 기호 + 이름으로 늘어놓는다. view_cfg 가 None 이라 다른 모델 질의는 모두 빈 값이다.
모드가 없는 평면·다른 뷰는 이 모듈을 거치지 않는다(기존 도면 바이트 불변). 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from dobim_core import drawing as D
from dobim_core import legal_area as LA
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
    """반사 천장 평면의 천장선: 천장이 절단면 위 보이는 깊이 안에 있는 공간의 경계·섬 구멍(투영선).
    천장판(W1 1-F 3차: ceiling.create 가 만든 격자 있는 천장판)은 윤곽(투영선)과 격자(해치 선)를 더한다."""
    cfg = g.q(D.view_cfg, v)
    out = []
    for s in g.q(D.scope_spaces, v):
        z, _ = _ceiling_z(g, s)
        if z is None or not cfg[2] - D.EPS <= z <= cfg[2] + cfg[3] + D.EPS:
            continue
        for ring in (g.q(D.space_info, s)[0],) + g.q(D.space_holes, s):
            out += [("proj", a[0], a[1], b[0], b[1]) for a, b in _ring_edges(ring)]
    for e in g.q(rcp_grid_ceilings, v):
        out.extend(g.q(ceiling_grid_lines, e))
    return tuple(out)


def rcp_grid_ceilings(g, v):
    """반사 천장 평면이 그리는 천장판: 뷰 레벨 위, 밑면이 절단면 위 보이는 깊이 안에 있고 격자(grid)가 있는 범주 ceiling 의 수평 판.
    격자가 없는 솔리드 천장(22단계)은 이전처럼 그리지 않는다(기존 도면 바이트 불변)."""
    cfg = g.q(D.view_cfg, v)
    out = []
    for e in g.q(D.all_solids):
        p = Q.el(g, e)["params"]
        if p["category"] != "ceiling" or p["form"] != "plate" or "grid" not in p:
            continue
        z = g.q(Q.level_info, g.q(Q.solid_level, e))[1] + p["base"][2] - p["thickness"]
        if cfg[2] - D.EPS <= z <= cfg[2] + cfg[3] + D.EPS:
            out.append(e)
    return tuple(out)


def _grid_cuts(edges, k, c):
    """닫힌 고리 선분들(짝홀 규칙)과 축 평행선(k=0: x = c 세로선, k=1: y = c 가로선)의 안쪽 구간 [(t0, t1), ...]."""
    ts = []
    for a, b in edges:
        fa, fb = a[k], b[k]
        if (fa <= c < fb) or (fb <= c < fa):
            r = (c - fa) / (fb - fa)
            ts.append(a[1 - k] + r * (b[1 - k] - a[1 - k]))
    ts.sort()
    return [(ts[j], ts[j + 1]) for j in range(0, len(ts) - 1, 2) if ts[j + 1] - ts[j] > D.SEG_MIN]


def ceiling_grid_lines(g, e):
    """천장판 하나의 반사 천장 평면 선: 윤곽·구멍(proj)과 격자(hatch, 원점에서 간격의 정수배 선을 윤곽 안만)."""
    p = Q.el(g, e)["params"]
    rings = [[tuple(q) for q in p["polygon"]]] + [[tuple(q) for q in h] for h in p.get("holes", ())]
    edges = [ed for r in rings for ed in _ring_edges(r)]
    out = [("proj", a[0], a[1], b[0], b[1]) for a, b in edges]
    gd = p["grid"]
    for k in (0, 1):
        step, org = gd["size"][k], gd["origin"][k]
        lo, hi = min(q[k] for q in rings[0]), max(q[k] for q in rings[0])
        m = D._floor((lo - org) / step) + 1
        while org + m * step < hi:
            c = org + m * step
            m += 1
            for t0, t1 in _grid_cuts(edges, k, c):
                out.append(("hatch", c, t0, c, t1) if k == 0 else ("hatch", t0, c, t1, c))
    return tuple(out)


# ---------------------------------------------------------------- 면적 평면
def area_items(g, v):
    """면적 평면의 면적 항목: ((이름, 경계 선분들, 면적 mm2, 문자 위치), ...). 면적 평면이 아니면 빈 값.
    gross: 그 평면이 자르는 벽(scope_walls) 바닥 윤곽 합집합의 바깥선(외벽 바깥면 기준) 하나. rooms: 공간마다(섬 구멍은 뺀다)."""
    cfg = g.q(D.view_cfg, v)
    if cfg is None or len(cfg) != 6 or cfg[5] not in ("gross", "rooms", "legal"):
        return ()
    if cfg[5] == "legal":   # 법정 바닥면적(ARC-36): 값은 산입 면적(빼는 객체는 음수). 층 바닥면적 합은 legal_totals 로 읽는다
        return tuple((it[1], it[4], it[6], it[7]) for it in g.q(legal_items, v))
    out = []
    if cfg[5] == "gross":
        polys = []
        for w in g.q(D.scope_walls, v):
            if "arc" in Q.el(g, w)["params"]:   # 곡선 벽(W2 ARC-04): 바깥·안쪽 면 호를 조각 꼭짓점으로 이은 윤곽
                polys.append(g.q(D.wall_plan_poly, w))
                continue
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
    if cfg[5] == "legal":
        return g.q(legal_lines, v)
    out = []
    for i, (_n, edges, _a, _at) in enumerate(g.q(area_items, v)):
        out += [("symbol", a[0], a[1], b[0], b[1]) for a, b in edges]
        out += _hatch_edges(list(edges), 1.0 if i % 2 == 0 else -1.0, AREA_HATCH * cfg[4])
    return tuple(out)


def area_labels(g, v):
    """면적 이름·값 문자, 실별(rooms)이면 아래에 합계."""
    cfg = g.q(D.view_cfg, v)
    if cfg is not None and len(cfg) == 6 and cfg[5] == "legal":
        return g.q(legal_labels, v)
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


# ---------------------------------------------------------------- 법정 면적 평면(W1 연장, ARC-36)
# 면적 평면의 scheme "legal": 건축법 시행령 119조 1항 3호의 바닥면적(그 층 저작 벽 중심선으로 닫힌 영역의 합집합)과 그 층의 바닥면적 대상
# 법정 면적 객체(larea)를 그린다. 계산은 area.legal 의 층 표와 같은 함수(faces_of·union_mm2)를 써서 값이 같다.
# 대지·건축면적 대상 객체(레벨이 없다)는 이 평면에 그리지 않는다. 법정 면적 평면이 없으면 이 경로는 타지 않는다.
LEGAL_EFFECT = {"add": "산입", "exclude": "불산입", "far": "용적률 제외", "balcony": "노대 산입"}


def faces_of(walls) -> list:
    """[(키, (x1,y1), (x2,y2), 두께), ...] -> 벽 중심선으로 닫힌 영역들(반시계 다각형). space.from_walls 와 같은 면 찾기(막다른 벽은 걷어낸다)."""
    from dobim_core import regions as RG
    return [tuple(f["center"]) for f in RG.find_faces(walls)] if walls else []


def union_segs(polys) -> tuple:
    """반시계 다각형들의 합집합 바깥 선분(면적 평면 gross 와 같은 계산)."""
    return _union_directed(D.outline_of(tuple(polys))) if polys else ()


def union_mm2(polys) -> float:
    """반시계 다각형들의 합집합 면적(mm2)."""
    segs = union_segs(polys)
    return max(0.0, sum(x0 * y1 - x1 * y0 for x0, y0, x1, y1 in segs) / 2.0)


def legal_floor_polys(g, lv):
    """레벨의 저작 벽 중심선으로 닫힌 영역들(area.legal 의 바닥면적 3호 본문과 같은 입력)."""
    walls = []
    for w in sorted(Q.rel_in(g, "on_level", lv)):
        r = Q.el(g, w)
        if r is not None and r["kind"] == "wall" and r["origin"] == "authored":
            if "arc" in r["params"]:   # 곡선 벽(W2 ARC-04): 중심선을 조각 현들로
                walls.extend(D.AG.center_segs(w, r["params"]))
                continue
            x1, y1, x2, y2 = r["params"]["axis"]
            walls.append((w, (x1, y1), (x2, y2), r["params"]["thickness"]))
    return tuple(faces_of(walls))


def legal_lareas(g, lv):
    """레벨에 놓인 바닥면적 대상 법정 면적 객체 id(정렬). 객체가 없으면 빈 값."""
    if not Q.count(g, "larea"):
        return ()
    out = []
    for b in range(Q.NB):
        for e in Q.bucket_ids(g, "larea", b):
            p = Q.el(g, e)["params"]
            if p["target"] == "floor" and p.get("level") == lv:
                out.append(e)
    return tuple(sorted(out))


def larea_counted(p) -> tuple:
    """법정 면적 객체 하나의 (면적 mm2, 산입 면적 mm2). 노대는 면적 - 접한 길이 x 1.5m, 빼는 것은 음수(area.list 와 같은 규칙)."""
    ar = abs(LA.area2(p["boundary"])) / 2.0
    if p["effect"] == "balcony":
        return ar, LA.balcony_counted(ar, p["contact_length"])
    return ar, (-ar if p["effect"] in ("exclude", "far") else ar)


def legal_items(g, v):
    """법정 면적 평면의 항목: ((id 또는 None, 이름, 근거, 효과, 경계 선분들, 면적 mm2, 산입 mm2, 문자 위치), ...).
    첫 항목은 벽 중심선 바닥면적(id None, 닫힌 벽 영역이 없으면 없다), 다음은 그 층 법정 면적 객체(id 순)."""
    cfg = g.q(D.view_cfg, v)
    if cfg is None or len(cfg) != 6 or cfg[5] != "legal" or cfg[1] is None or Q.el(g, cfg[1])["kind"] != "level":
        return ()
    out = []
    segs = union_segs(g.q(legal_floor_polys, cfg[1]))
    a2 = sum(x0 * y1 - x1 * y0 for x0, y0, x1, y1 in segs)
    if a2 > 1.0:
        cx = sum((x0 + x1) * (x0 * y1 - x1 * y0) for x0, y0, x1, y1 in segs) / (3.0 * a2)
        cy = sum((y0 + y1) * (x0 * y1 - x1 * y0) for x0, y0, x1, y1 in segs) / (3.0 * a2)
        edges = tuple(((x0, y0), (x1, y1)) for x0, y0, x1, y1 in segs)
        out.append((None, "바닥면적(벽 중심선)", "119-1-3", "walls", edges, a2 / 2.0, a2 / 2.0, (cx, cy)))
    for e in g.q(legal_lareas, cfg[1]):
        p = Q.el(g, e)["params"]
        ring = [tuple(q) for q in p["boundary"]]
        if D._area2(ring) < 0:
            ring.reverse()
        ar, c = larea_counted(p)
        out.append((e, p["name"], p["rule"], p["effect"], tuple(_ring_edges(tuple(ring))), ar, c, D.polygon_centroid(tuple(ring))))
    return tuple(out)


def legal_totals(g, v):
    """법정 면적 평면의 층 합계 ㎡: (벽 중심선, 조정 합, 바닥면적, 용적률 제외 합). area.legal 층 표와 같은 반올림(객체마다 ㎡ 둘째 자리)."""
    walls = adj = far = 0.0
    for it in g.q(legal_items, v):
        if it[0] is None:
            walls = round(it[5] / 1.0e6, 2)
        elif it[3] == "far":
            far += round(it[6] / 1.0e6, 2)
        else:
            adj += round(it[6] / 1.0e6, 2)
    adj = round(adj, 2)
    return walls, adj, round(max(0.0, walls + adj), 2), round(far, 2)


def legal_lines(g, v):
    """법정 경계(symbol): 벽 중심선 바닥면적 바깥선과 객체 경계. 객체만 해치(더함·노대 45도, 뺌·용적률 제외 135도)."""
    cfg = g.q(D.view_cfg, v)
    out = []
    for it in g.q(legal_items, v):
        out += [("symbol", a[0], a[1], b[0], b[1]) for a, b in it[4]]
        if it[0] is not None:
            out += _hatch_edges(list(it[4]), 1.0 if it[3] in ("add", "balcony") else -1.0, AREA_HATCH * cfg[4])
    return tuple(out)


def legal_value_text(it) -> str:
    """항목의 둘째 줄 문자: 벽 중심선 영역은 면적, 객체는 '근거 효과 산입 면적(부호)'."""
    c = it[6] / 1.0e6
    if it[0] is None:
        return f"{c:.2f}㎡"
    return f"{it[2]} {LEGAL_EFFECT[it[3]]} {c:+.2f}㎡"


def legal_labels(g, v):
    """항목 이름과 값, 아래에 층 바닥면적 합계(용적률 제외 객체가 있으면 덧붙인다)."""
    cfg = g.q(D.view_cfg, v)
    items = g.q(legal_items, v)
    sc = cfg[4]
    out = []
    for it in items:
        cx, cy = it[7]
        out.append((round(cx, 3), round(cy + 0.9 * sc, 3), it[1], D.LABEL_NAME_H, "middle"))
        out.append((round(cx, 3), round(cy - 2.0 * sc, 3), legal_value_text(it), D.LABEL_SUB_H, "middle"))
    if items:
        _w, _a, fl, far = g.q(legal_totals, v)
        x, y = _area_total_at([(None, it[4]) for it in items], sc)
        txt = f"바닥면적 {fl:.2f}㎡" + (f" (용적률 산정 제외 {-far:.2f}㎡)" if far else "")
        out.append((round(x, 3), round(y, 3), txt, D.LABEL_NAME_H, "middle"))
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
D.VIEW_SCOPED = D.VIEW_SCOPED | frozenset({rcp_ceilings, area_items, area_lines, area_labels, mode_lines, legend_rows, legend_lines, legend_labels,
                                         legal_items, legal_totals, legal_lines, legal_labels})
