# SPDX-License-Identifier: MPL-2.0
"""2D 작도(27단계, 설계: docs/2d-editing.md): 뷰 위의 모델과 무관한 2D 요소를 도면 그림 항목으로 바꾸는 질의와 순수 기하 도우미.

요소(kinds "draft")의 좌표는 그 뷰의 2D 좌표(평면 x,y / 단면·입면 s,z mm, view_lines 와 같은 공간)이고, 선굵기·문자 높이·선종류 마디는 용지 mm 다.
그림 항목(뷰 좌표, 용지 크기는 mm):
  ("line", id, 층, 굵기 mm, 색, x0, y0, x1, y1)
  ("circle", id, 층, 굵기 mm, 색, cx, cy, r)            r 은 뷰 좌표 mm(실선 원만. 파선 원은 선분으로 나눈다)
  ("fill", id, 층, 색, ((x, y), ...))                   채움 영역(solid)
  ("text", id, 층, 색, x, y, 높이 mm, 글, 앵커, 각도)   각도는 뷰 좌표에서 반시계 도(용지에서는 부호가 바뀐다)
선종류(파선·1점쇄선 등)는 경로를 따라 미리 마디로 나눠 실선 조각으로 낸다: SVG·PDF·PNG·DXF 어느 출력이든 같은 모양이 나온다(렌더러가 선종류를 몰라도 된다).
2D 요소가 없는 뷰는 빈 값이고, 내보내기는 "drafts" 키 자체를 만들지 않는다(기존 도면 바이트 불변).
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from math import atan2, ceil, cos, degrees, hypot, radians, sin, sqrt

from dobim_core import drawing as D
from dobim_core import queries as Q

WEIGHT_MM = {"thin": 0.09, "fine": 0.18, "medium": 0.25, "thick": 0.50}   # 기존 선 등급(해치·투영·기호·절단)과 같은 굵기 체계
LINETYPE_MM = {"continuous": None, "dashed": (3.0, 1.5), "hidden": (1.5, 0.75), "dotted": (0.2, 0.8),
               "center": (12.0, 1.5, 2.0, 1.5), "dashdot": (6.0, 1.5, 0.5, 1.5)}   # 그리기·띄우기 마디(용지 mm)
DEFAULT_LAYER = "A-DRAFT"
DEFAULT_STYLE = ("fine", "continuous", "#000000")   # 층 스타일이 없을 때(층 이름만 있고 draft.layer.set 을 안 했을 때)
REGION_FILL = "#cfcfcf"                             # 색을 주지 않은 채움 영역(절단 채움과 같은 회색)
HATCH_STEP = 2.5                                    # 해치 간격(용지 mm)
ARC_SEG_DEG = 5.0                                   # 호·원을 선분으로 나눌 때 한 조각의 각(도)
DIM_GAP, DIM_EXT, DIM_TICK, DIM_TEXT_GAP = 1.0, 2.0, 0.7, 0.8   # 치수 보조선 띄움·넘침, 눈금 반 길이, 글 띄움(용지 mm)
EPS = 1e-9


# ================================================================ 순수 기하
def arc_points(c, r, a0, a1, closed=False) -> list:
    """중심 c, 반지름 r, a0 -> a1 반시계 호(도)의 꺾은선 점. closed 면 한 바퀴(원)."""
    sweep = 360.0 if closed else (a1 - a0) % 360.0 or 360.0
    n = max(2, int(ceil(sweep / ARC_SEG_DEG)))
    return [(c[0] + r * cos(radians(a0 + sweep * k / n)), c[1] + r * sin(radians(a0 + sweep * k / n))) for k in range(n + 1)]


def dash_path(pts, pattern, unit) -> list:
    """꺾은선을 따라 이어지는 마디 패턴으로 나눈 실선 조각 [(x0, y0, x1, y1)]. pattern 이 None 이면 그대로. unit = 용지 mm -> 뷰 mm."""
    segs = [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(len(pts) - 1)]
    segs = [s for s in segs if hypot(s[2] - s[0], s[3] - s[1]) > EPS]
    if pattern is None:
        return segs
    pat = [m * unit for m in pattern]
    out, k, left = [], 0, pat[0]        # k = 지금 마디 번호(짝수 = 그린다), left = 그 마디에 남은 길이
    for x0, y0, x1, y1 in segs:
        ln = hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / ln, (y1 - y0) / ln
        t = 0.0
        while t < ln - EPS:
            step = min(left, ln - t)
            if k % 2 == 0:
                out.append((x0 + ux * t, y0 + uy * t, x0 + ux * (t + step), y0 + uy * (t + step)))
            t += step
            left -= step
            if left <= EPS:
                k = (k + 1) % len(pat)
                left = pat[k]
    return out


def rotate_pt(p, c, ang):
    ca, sa = cos(radians(ang)), sin(radians(ang))
    dx, dy = p[0] - c[0], p[1] - c[1]
    return [c[0] + dx * ca - dy * sa, c[1] + dx * sa + dy * ca]


def mirror_pt(p, a, b):
    """선 a-b 에 대한 대칭점."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
    fx, fy = a[0] + t * dx, a[1] + t * dy
    return [2 * fx - p[0], 2 * fy - p[1]]


def offset_polyline(pts, d, closed=False):
    """꺾은선을 진행 방향 왼쪽(+)으로 d 만큼 평행 이동한다(꺾인 곳은 이웃 두 변의 연장 교점, 나란하면 그 점을 그대로 옮긴다)."""
    n = len(pts)
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n if closed else n - 1)]
    lines = []
    for a, b in edges:
        ln = hypot(b[0] - a[0], b[1] - a[1])
        nx, ny = -(b[1] - a[1]) / ln * d, (b[0] - a[0]) / ln * d
        lines.append(((a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny)))
    out = []
    for i in range(n):
        if not closed and i == 0:
            out.append(list(lines[0][0]))
            continue
        if not closed and i == n - 1:
            out.append(list(lines[-1][1]))
            continue
        l0, l1 = lines[i - 1], lines[i % len(lines)]
        x = line_x(l0[0], l0[1], l1[0], l1[1])
        out.append(list(x[0]) if x is not None else list(l1[0]))
    return [[round(x, 6), round(y, 6)] for x, y in out]


def line_x(a, b, c, d):
    """무한선 a-b 와 c-d 의 교점 ((x, y), t, u) (t·u 는 각 선분에서의 매개변수). 나란하면 None."""
    rx, ry, sx, sy = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) < EPS * max(1.0, hypot(rx, ry) * hypot(sx, sy)):
        return None
    t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / den
    u = ((c[0] - a[0]) * ry - (c[1] - a[1]) * rx) / den
    return (a[0] + t * rx, a[1] + t * ry), t, u


def boundary_hits(a, b, p) -> list:
    """무한선 a-b 가 경계 요소(params p)와 만나는 매개변수 t 목록(a=0, b=1). 선·폴리선·영역·치수 선분, 원·호."""
    s = p["shape"]
    out = []
    if s in ("line", "polyline", "region", "dim"):
        pts = p["pts"]
        closed = s == "region" or (s == "polyline" and p["closed"])
        for i in range(len(pts) if closed else len(pts) - 1):
            c, d = pts[i], pts[(i + 1) % len(pts)]
            x = line_x(a, b, c, d)
            if x is not None and -1e-9 <= x[2] <= 1 + 1e-9:
                out.append(x[1])
    elif s in ("circle", "arc"):
        (cx, cy), r = p["pts"][0], p["r"]
        dx, dy = b[0] - a[0], b[1] - a[1]
        fx, fy = a[0] - cx, a[1] - cy
        qa, qb, qc = dx * dx + dy * dy, 2 * (fx * dx + fy * dy), fx * fx + fy * fy - r * r
        disc = qb * qb - 4 * qa * qc
        if disc >= 0:
            for t in ((-qb - sqrt(disc)) / (2 * qa), (-qb + sqrt(disc)) / (2 * qa)):
                if s == "arc":
                    ang = degrees(atan2(a[1] + t * dy - cy, a[0] + t * dx - cx))
                    if (ang - p["a0"]) % 360.0 > (p["a1"] - p["a0"]) % 360.0 + 1e-9:
                        continue
                out.append(t)
    return out


def bbox_of(p) -> list:
    """요소가 차지하는 뷰 좌표 범위 [x0, y0, x1, y1](원·호는 원 전체, 문자는 기준점)."""
    if p["shape"] in ("circle", "arc"):
        (cx, cy), r = p["pts"][0], p["r"]
        return [cx - r, cy - r, cx + r, cy + r]
    xs, ys = [q[0] for q in p["pts"]], [q[1] for q in p["pts"]]
    return [min(xs), min(ys), max(xs), max(ys)]


def _hatch(poly, ang, step) -> list:
    """다각형(짝홀 규칙) 안의 ang 도 방향 평행선(간격 step)."""
    ca, sa = cos(radians(ang)), sin(radians(ang))
    loc = [(x * ca + y * sa, -x * sa + y * ca) for x, y in poly]     # 선 방향이 u 축이 되게 돌린 좌표
    v0, v1 = min(q[1] for q in loc), max(q[1] for q in loc)
    out = []
    k = int(ceil(v0 / step))
    while k * step < v1:
        v = k * step
        us = []
        for i in range(len(loc)):
            (u0, w0), (u1, w1) = loc[i], loc[(i + 1) % len(loc)]
            if (w0 <= v < w1) or (w1 <= v < w0):
                us.append(u0 + (v - w0) * (u1 - u0) / (w1 - w0))
        us.sort()
        for j in range(0, len(us) - 1, 2):
            a, b = us[j], us[j + 1]
            out.append((a * ca - v * sa, a * sa + v * ca, b * ca - v * sa, b * sa + v * ca))
        k += 1
    return out


def _r(v):
    return round(v, 3)


# ================================================================ 그래프 질의
def draft_layers(g):
    """층 스타일 {이름: (굵기, 선종류, 색)}. 층 스타일 요소가 바뀌면 이 값만 다시 계산된다."""
    out = {}
    for b in range(Q.NB):
        for lid in Q.bucket_ids(g, "dlayer", b):
            p = Q.el(g, lid)["params"]
            out[p["name"]] = (p["weight"], p["linetype"], p["color"])
    return tuple(sorted(out.items()))


def resolved_style(g, p) -> tuple:
    """요소의 실제 (굵기 mm, 선 마디, 색): 요소 값이 없으면(null) 층 스타일, 층 스타일이 없으면 기본값."""
    lay = dict(g.q(draft_layers)).get(p["layer"], DEFAULT_STYLE)
    w = p["weight"] or lay[0]
    lt = p["linetype"] or lay[1]
    return WEIGHT_MM[w], LINETYPE_MM[lt], p["color"] or lay[2]


def draft_items(g, d):
    """2D 요소 하나의 그림 항목(뷰 좌표). 뷰가 없으면 빈 값."""
    row = Q.el(g, d)
    if row is None:
        return ()
    p = row["params"]
    vr = Q.el(g, p["view"])
    if vr is None or vr["kind"] != "view":
        return ()
    sc = float(vr["params"]["scale"])
    w, pat, col = resolved_style(g, p)
    lay, s, pts = p["layer"], p["shape"], p["pts"]
    out = []

    def path(ps):
        for x0, y0, x1, y1 in dash_path(ps, pat, sc):
            out.append(("line", d, lay, w, col, _r(x0), _r(y0), _r(x1), _r(y1)))

    if s == "line":
        path(pts)
    elif s == "polyline":
        path(pts + [pts[0]] if p["closed"] else pts)
    elif s == "region":
        poly = tuple((_r(x), _r(y)) for x, y in pts)
        if p["fill"] == "solid":
            out.append(("fill", d, lay, p["color"] or REGION_FILL, poly))
        elif p["fill"] in ("hatch", "cross"):
            for ang in ((45.0,) if p["fill"] == "hatch" else (45.0, 135.0)):
                for x0, y0, x1, y1 in _hatch(pts, ang, HATCH_STEP * sc):
                    out.append(("line", d, lay, WEIGHT_MM["thin"], col, _r(x0), _r(y0), _r(x1), _r(y1)))
        path(pts + [pts[0]])
    elif s == "circle":
        c, r = pts[0], p["r"]
        if pat is None:
            out.append(("circle", d, lay, w, col, _r(c[0]), _r(c[1]), _r(r)))
        else:
            path(arc_points(c, r, 0.0, 360.0, closed=True))
    elif s == "arc":
        path(arc_points(pts[0], p["r"], p["a0"], p["a1"]))
    elif s == "text":
        out.append(("text", d, lay, col, _r(pts[0][0]), _r(pts[0][1]), p["size"], p["text"], p["align"], round(p["a0"], 4)))
    elif s == "dim":
        out.extend(_dim_items(d, p, w, col, sc))
    return tuple(out)


def _dim_items(d, p, w, col, sc):
    """정렬 치수(두 점 사이 길이): 보조선 둘·치수선·45도 눈금·값 글. 값 글은 p['text'] 가 있으면 그 글."""
    (x1, y1), (x2, y2) = p["pts"]
    ln = hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / ln, (y2 - y1) / ln
    nx, ny = -uy, ux
    off = p["off"]
    sg = 1.0 if off >= 0 else -1.0
    lay = p["layer"]
    out = []
    d1, d2 = (x1 + nx * off, y1 + ny * off), (x2 + nx * off, y2 + ny * off)
    for (bx, by), (ex, ey) in (((x1, y1), d1), ((x2, y2), d2)):
        g0 = min(DIM_GAP * sc, abs(off))
        out.append(("line", d, lay, WEIGHT_MM["thin"], col, _r(bx + nx * sg * g0), _r(by + ny * sg * g0),
                    _r(ex + nx * sg * DIM_EXT * sc), _r(ey + ny * sg * DIM_EXT * sc)))
    out.append(("line", d, lay, w, col, _r(d1[0]), _r(d1[1]), _r(d2[0]), _r(d2[1])))
    tk = DIM_TICK * sc
    for cx, cy in (d1, d2):
        kx, ky = (ux + nx) * tk, (uy + ny) * tk
        out.append(("line", d, lay, WEIGHT_MM["medium"], col, _r(cx - kx), _r(cy - ky), _r(cx + kx), _r(cy + ky)))
    ang = degrees(atan2(uy, ux))
    if ang > 90.0 + 1e-9:
        ang -= 180.0
    elif ang <= -90.0 + 1e-9:
        ang += 180.0
    a = radians(ang)
    tx, ty = (d1[0] + d2[0]) / 2 - sin(a) * DIM_TEXT_GAP * sc, (d1[1] + d2[1]) / 2 + cos(a) * DIM_TEXT_GAP * sc   # 글은 읽는 방향 위쪽에 띄운다
    text = p["text"] or f"{ln:.0f}"
    out.append(("text", d, lay, col, _r(tx), _r(ty), p["size"], text, "middle", round(ang, 4)))
    return out


def view_drafts(g, v):
    """뷰에 속한 2D 요소의 그림 항목(요소 id 순). 2D 요소가 없으면 빈 값."""
    out = []
    for a in sorted(Q.rel_in(g, "annot_of", v)):
        r = Q.el(g, a)
        if r is not None and r["kind"] == "draft":
            out.extend(g.q(draft_items, a))
    return tuple(out)


def item_points(it) -> list:
    """그림 항목이 차지하는 대표 뷰 좌표(용지 크기 계산용). 문자 폭은 계산하는 쪽이 더한다."""
    k = it[0]
    if k == "line":
        return [(it[5], it[6]), (it[7], it[8])]
    if k == "circle":
        return [(it[5] - it[7], it[6] - it[7]), (it[5] + it[7], it[6] + it[7])]
    if k == "fill":
        return list(it[4])
    return [(it[4], it[5])]


# 뷰를 지우면 뷰에 딸린 질의 노드를 치운다(drawing.forget_views 가 이 집합을 본다). 공용 파일(drawing.py)을 고치지 않으려고 여기서 늘린다
D.VIEW_SCOPED = D.VIEW_SCOPED | frozenset({view_drafts})
