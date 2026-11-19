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
from dobim_core import kinds as K
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
DIM_BASE_STEP = 7.0                                 # 기준선 치수 줄 간격(용지 mm)
ARROW_LEN, ARROW_DEG = 2.0, 15.0                    # 반지름·지름·경사 화살촉 길이(용지 mm)와 벌림 각(도)
SPOT_MARK = 1.5                                     # 표고·좌표 기호 크기(용지 mm)
CLOUD_CHORD = 8.0                                   # 리비전 구름 호 하나의 현 길이 기본값(용지 mm, r 을 0 으로 두면)
CLOUD_MAX_BUMPS = 400                               # 구름 하나의 호 개수 상한
MASK_FILL = "#ffffff"                               # 마스킹 영역 칠(종이 색). 2D 요소 중 가장 먼저 그려 아래 모델 선을 가린다
POLY_SHAPES = ("region", "mask", "cloud")           # 닫힌 경계 요소(반시계로 저장한다)
CENTER_SHAPES = ("circle", "arc", "dim_radius", "dim_diameter", "dim_arc")   # pts[0] 이 중심이고 r 이 반지름인 요소
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
    if s in ("line", "polyline", "region", "dim", "mask", "cloud"):
        pts = p["pts"]
        closed = s in POLY_SHAPES or (s == "polyline" and p["closed"])
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
    if p["shape"] in CENTER_SHAPES:
        (cx, cy), r = p["pts"][0], p["r"] + max(0.0, p["off"])
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
    vtype = vr["params"]["type"]
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
    elif s == "mask":
        out.append(("fill", d, lay, MASK_FILL, tuple((_r(x), _r(y)) for x, y in pts)))
        if p["closed"]:   # 마스킹 경계선은 closed 일 때만 그린다(기본은 보이지 않는 경계)
            path(pts + [pts[0]])
    elif s == "cloud":
        path(cloud_points(pts, p["r"] or CLOUD_CHORD * sc))
        if p["text"]:
            out.extend(_rev_mark(d, lay, w, col, pts[0], p["size"], p["text"], sc))
    elif s in ANNOT_SHAPES:
        lv = None
        if vtype == "plan" and s == "spot_elev":   # 평면 표고: 그 레벨의 슬래브·지붕 윗면 표본(W1 1-B 3차), 덮는 면이 없으면 레벨 높이
            lv = g.q(plan_spot_z, vr["params"]["level"], float(vr["params"]["cut_height"]), float(pts[0][0]), float(pts[0][1]))
        elif vtype in ("plan", "site") and s == "spot_coord":   # 측량 기준점이 있으면 측량 좌표(없으면 None = 프로젝트 원점 기준)
            lv = g.q(site_survey)
        out.extend(ANNOT_SHAPES[s](d, p, w, col, sc, vtype, lv))
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


# ================================================================ 주석·치수 확장(W1 1-B): 각도·반지름·지름·호 길이·연속·기준선 치수, 표고·좌표·경사, 구름
def _dir(ang):
    return (cos(radians(ang)), sin(radians(ang)))


def _ang_of(v):
    return degrees(atan2(v[1], v[0]))


def read_angle(ang) -> float:
    """글이 거꾸로 서지 않게 (-90, 90] 도로 맞춘다."""
    ang = (ang + 180.0) % 360.0 - 180.0
    if ang > 90.0 + 1e-9:
        ang -= 180.0
    elif ang <= -90.0 + 1e-9:
        ang += 180.0
    return ang


def fmt_len(v) -> str:
    return f"{v:.0f}"


def fmt_deg(v) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") + "°"


def _ln(out, d, lay, w, col, a, b):
    out.append(("line", d, lay, w, col, _r(a[0]), _r(a[1]), _r(b[0]), _r(b[1])))


def _txt(out, d, lay, col, pos, size, text, align, ang):
    out.append(("text", d, lay, col, _r(pos[0]), _r(pos[1]), size, text, align, round(ang, 4)))


def _ext(out, d, lay, col, base, dpt, sc):
    """치수 보조선: 재는 점 base 에서 띄움만큼 떨어져 시작해 치수선 점 dpt 를 넘침만큼 지난다. 두 점이 같으면 그리지 않는다."""
    vx, vy = dpt[0] - base[0], dpt[1] - base[1]
    ln = hypot(vx, vy)
    if ln < 1e-6:
        return
    ex, ey = vx / ln, vy / ln
    g0 = min(DIM_GAP * sc, ln)
    _ln(out, d, lay, WEIGHT_MM["thin"], col, (base[0] + ex * g0, base[1] + ey * g0), (dpt[0] + ex * DIM_EXT * sc, dpt[1] + ey * DIM_EXT * sc))


def _tick(out, d, lay, col, c, u, n, sc):
    """45도 눈금(u = 치수선 방향, n = 그에 수직)."""
    tk = DIM_TICK * sc
    kx, ky = (u[0] + n[0]) * tk, (u[1] + n[1]) * tk
    _ln(out, d, lay, WEIGHT_MM["medium"], col, (c[0] - kx, c[1] - ky), (c[0] + kx, c[1] + ky))


def _arrow(out, d, lay, w, col, tip, u, sc):
    """tip 에서 끝나는 열린 화살촉(u = 진행 방향 단위 벡터)."""
    ln = ARROW_LEN * sc
    for sg in (1.0, -1.0):
        ca, sa = cos(radians(ARROW_DEG)), sin(radians(ARROW_DEG)) * sg
        bx, by = -(u[0] * ca - u[1] * sa), -(u[0] * sa + u[1] * ca)
        _ln(out, d, lay, w, col, tip, (tip[0] + bx * ln, tip[1] + by * ln))


def _above(mid, ang, sc, extra=0.0):
    """mid 에서 읽는 방향 위쪽으로 글 띄움(+extra)만큼 옮긴 점."""
    a = radians(ang)
    k = DIM_TEXT_GAP * sc + extra
    return (mid[0] - sin(a) * k, mid[1] + cos(a) * k)


def _arc_text(out, d, lay, col, c, rad, mid_ang, size, text, sc):
    """호 바깥쪽에 호와 나란히 글을 둔다(글 위쪽이 안쪽을 보면 글 높이만큼 더 밀어낸다)."""
    ang = read_angle(mid_ang - 90.0)
    e = _dir(mid_ang)
    up = (-sin(radians(ang)), cos(radians(ang)))
    push = DIM_TEXT_GAP * sc + (0.0 if up[0] * e[0] + up[1] * e[1] >= 0 else size * sc)
    _txt(out, d, lay, col, (c[0] + e[0] * (rad + push), c[1] + e[1] * (rad + push)), size, text, "middle", ang)


def angle_sweep(p) -> float:
    """각도 치수 값(도): 꼭짓점 pts[0] 에서 pts[1] 방향부터 pts[2] 방향까지 반시계."""
    v, a, b = p["pts"]
    return (_ang_of((b[0] - v[0], b[1] - v[1])) - _ang_of((a[0] - v[0], a[1] - v[1]))) % 360.0


def arc_length(p) -> float:
    return p["r"] * radians((p["a1"] - p["a0"]) % 360.0)


def run_stations(p):
    """연속·기준선 치수: 측정 방향 u(a0), 왼쪽 수직 n, 각 점의 (u 위치, n 위치), 치수선의 n 위치."""
    u = _dir(p["a0"])
    n = (-u[1], u[0])
    st = [(x * u[0] + y * u[1], x * n[0] + y * n[1]) for x, y in p["pts"]]
    return u, n, st, st[0][1] + p["off"]


def dim_values(p) -> list:
    """치수 요소가 재는 값 목록(draft.list 와 그림이 같은 규칙). 치수가 아니면 빈 목록."""
    s = p["shape"]
    if s == "dim":
        (x1, y1), (x2, y2) = p["pts"]
        return [hypot(x2 - x1, y2 - y1)]
    if s == "dim_angle":
        return [angle_sweep(p)]
    if s == "dim_radius":
        return [p["r"]]
    if s == "dim_diameter":
        return [2.0 * p["r"]]
    if s == "dim_arc":
        return [arc_length(p)]
    if s == "dim_chain":
        us = sorted(t[0] for t in run_stations(p)[2])
        return [us[i + 1] - us[i] for i in range(len(us) - 1) if us[i + 1] - us[i] > 1e-6]
    if s == "dim_base":
        st = run_stations(p)[2]
        return sorted(abs(t[0] - st[0][0]) for t in st[1:] if abs(t[0] - st[0][0]) > 1e-6)
    return []


def _d_angle(d, p, w, col, sc, vtype, lv):
    v, a, b = p["pts"]
    r = p["r"]
    b1, sweep = _ang_of((a[0] - v[0], a[1] - v[1])), angle_sweep(p)
    out = []
    for x0, y0, x1, y1 in dash_path(arc_points(v, r, b1, b1 + sweep), None, sc):
        out.append(("line", d, p["layer"], w, col, _r(x0), _r(y0), _r(x1), _r(y1)))
    for q, bb in ((a, b1), (b, b1 + sweep)):
        e = _dir(bb)
        tip = (v[0] + e[0] * r, v[1] + e[1] * r)
        if hypot(q[0] - v[0], q[1] - v[1]) < r - 1e-6:   # 호가 재는 선 바깥에 있으면 보조선을 호까지 늘인다
            _ext(out, d, p["layer"], col, q, tip, sc)
        _tick(out, d, p["layer"], col, tip, (-e[1], e[0]), e, sc)
    _arc_text(out, d, p["layer"], col, v, r, b1 + sweep / 2.0, p["size"], p["text"] or fmt_deg(sweep), sc)
    return out


def _d_radius(d, p, w, col, sc, vtype, lv):
    c, r = p["pts"][0], p["r"]
    e = _dir(p["a0"])
    tip = (c[0] + e[0] * r, c[1] + e[1] * r)
    out = []
    _ln(out, d, p["layer"], w, col, c, tip)
    _arrow(out, d, p["layer"], w, col, tip, e, sc)
    ang = read_angle(p["a0"])
    _txt(out, d, p["layer"], col, _above(((c[0] + tip[0]) / 2, (c[1] + tip[1]) / 2), ang, sc), p["size"], p["text"] or "R" + fmt_len(r), "middle", ang)
    return out


def _d_diameter(d, p, w, col, sc, vtype, lv):
    c, r = p["pts"][0], p["r"]
    e = _dir(p["a0"])
    t1, t2 = (c[0] - e[0] * r, c[1] - e[1] * r), (c[0] + e[0] * r, c[1] + e[1] * r)
    out = []
    _ln(out, d, p["layer"], w, col, t1, t2)
    _arrow(out, d, p["layer"], w, col, t2, e, sc)
    _arrow(out, d, p["layer"], w, col, t1, (-e[0], -e[1]), sc)
    ang = read_angle(p["a0"])
    _txt(out, d, p["layer"], col, _above(c, ang, sc), p["size"], p["text"] or "Ø" + fmt_len(2.0 * r), "middle", ang)
    return out


def _d_arc(d, p, w, col, sc, vtype, lv):
    c, r, off = p["pts"][0], p["r"], p["off"]
    R = r + off
    sweep = (p["a1"] - p["a0"]) % 360.0
    out = []
    for x0, y0, x1, y1 in dash_path(arc_points(c, R, p["a0"], p["a0"] + sweep), None, sc):
        out.append(("line", d, p["layer"], w, col, _r(x0), _r(y0), _r(x1), _r(y1)))
    for bb in (p["a0"], p["a0"] + sweep):
        e = _dir(bb)
        _ext(out, d, p["layer"], col, (c[0] + e[0] * r, c[1] + e[1] * r), (c[0] + e[0] * R, c[1] + e[1] * R), sc)
        _tick(out, d, p["layer"], col, (c[0] + e[0] * R, c[1] + e[1] * R), (-e[1], e[0]), e, sc)
    _arc_text(out, d, p["layer"], col, c, R, p["a0"] + sweep / 2.0, p["size"], p["text"] or fmt_len(arc_length(p)), sc)
    return out


def _d_run(d, p, w, col, sc, vtype, lv):
    """연속 치수(dim_chain: 이웃한 점 사이를 한 줄로)·기준선 치수(dim_base: 첫 점에서 각 점까지 줄을 쌓는다)."""
    u, n, st, lvl = run_stations(p)
    lay = p["layer"]

    def at(s, h):
        return (s * u[0] + h * n[0], s * u[1] + h * n[1])

    ang = read_angle(p["a0"])
    out = []
    if p["shape"] == "dim_chain":
        for (s, h), q in zip(st, p["pts"]):
            _ext(out, d, lay, col, q, at(s, lvl), sc)
        us = sorted({round(s, 6) for s, _ in st})
        _ln(out, d, lay, w, col, at(us[0], lvl), at(us[-1], lvl))
        for s in us:
            _tick(out, d, lay, col, at(s, lvl), u, n, sc)
        for s0, s1 in zip(us, us[1:]):
            _txt(out, d, lay, col, _above(at((s0 + s1) / 2.0, lvl), ang, sc), p["size"], fmt_len(s1 - s0), "middle", ang)
        return out
    sg = 1.0 if p["off"] >= 0 else -1.0
    s0 = st[0][0]
    rows = sorted(((abs(s - s0), s, q) for (s, _h), q in zip(st[1:], p["pts"][1:]) if abs(s - s0) > 1e-6), key=lambda t: t[0])
    for k, (dist, s, q) in enumerate(rows):
        h = lvl + sg * k * DIM_BASE_STEP * sc
        _ext(out, d, lay, col, q, at(s, h), sc)
        _ln(out, d, lay, w, col, at(s0, h), at(s, h))
        _tick(out, d, lay, col, at(s0, h), u, n, sc)
        _tick(out, d, lay, col, at(s, h), u, n, sc)
        _txt(out, d, lay, col, _above(at((s0 + s) / 2.0, h), ang, sc), p["size"], fmt_len(dist), "middle", ang)
    if rows:
        _ext(out, d, lay, col, p["pts"][0], at(s0, lvl + sg * (len(rows) - 1) * DIM_BASE_STEP * sc), sc)
    return out


def _leader(out, d, lay, w, col, pt, lab):
    if hypot(lab[0] - pt[0], lab[1] - pt[1]) > 1e-6:
        _ln(out, d, lay, w, col, pt, lab)


def level_surfaces(g, lv):
    """레벨 lv 에 놓인 저작 슬래브·경사 지붕 id(정렬). 평면 표고 표본이 이 목록만 본다(다른 레벨의 요소가 바뀌어도 이 값만 다시 계산된다)."""
    out = []
    for kind in ("slab", "roof"):
        for b in range(Q.NB):
            for e in Q.bucket_ids(g, kind, b):
                row = Q.el(g, e)
                if row["origin"] == "authored" and tuple(Q.rel_out(g, "on_level", e))[:1] == (lv,):
                    out.append((kind, e))
    return tuple(sorted(out))


def plan_spot_z(g, lv, cut, x, y):
    """평면 표고점의 높이(W1 1-B 3차, ANN-04): 레벨 lv 의 슬래브 윗면(구멍 밖)과 경사 지붕 면 가운데 점 (x, y) 를 덮고 절단면(레벨 + cut) 아래인
    가장 높은 면. 덮는 면이 없으면 레벨 높이."""
    base = Q.level_info(g, lv)[1]
    best = None
    for kind, e in g.q(level_surfaces, lv):
        p = Q.el(g, e)["params"]
        if kind == "slab":
            if K.point_in_polygon((x, y), p["boundary"]) < 0 or any(K.point_in_polygon((x, y), o) == 1 for o in p.get("openings", ())):
                continue
            z = base + p["top_offset"]
        else:
            geo = K.roof_geom(p)
            zs = [a * x + b * y + c for fc, (a, b, c) in zip(geo["faces"], geo["planes"]) if K.point_in_polygon((x, y), [q[:2] for q in fc]) >= 0]
            if not zs:
                continue
            z = base + max(zs)
        if z <= base + cut and (best is None or z > best):
            best = z
    return base if best is None else best


def site_survey(g):
    """측량 기준점 변환 (E0, N0, cos, sin) — 저작 대지의 survey 와 정북 각. 없으면 None(좌표 기호는 프로젝트 원점 기준)."""
    for b in range(Q.NB):
        for e in sorted(Q.bucket_ids(g, "site", b)):
            row = Q.el(g, e)
            if row["origin"] == "authored" and "survey" in row["params"]:
                t = radians(float(row["params"]["north_angle"]))
                e0, n0 = row["params"]["survey"]
                return (e0, n0, cos(t), sin(t))
    return None


def survey_en(sv, x, y):
    """프로젝트 좌표 (x, y) -> 측량 좌표 (E, N). 정북이 도면 위에서 시계방향 θ 돌아 있으면 정북 단위 벡터는 (sin θ, cos θ), 정동은 (cos θ, -sin θ)."""
    e0, n0, c, s = sv
    return e0 + x * c - y * s, n0 + x * s + y * c


def spot_value(p, vtype, lv) -> str | None:
    """표고 값 글. 단면·입면은 점의 높이(뷰 y = z mm), 평면은 그 뷰 레벨의 슬래브·지붕 윗면 표본(plan_spot_z, 없으면 레벨 높이), 배치도는 값이 없다."""
    if vtype in ("section", "elevation"):
        z = p["pts"][0][1]
    elif vtype == "plan" and lv is not None:
        z = lv
    else:
        return None
    return f"EL{z:+,.0f}"


def _d_spot_elev(d, p, w, col, sc, vtype, lv):
    pt, lab = p["pts"]
    m = SPOT_MARK * sc
    lay = p["layer"]
    out = []
    tri = [pt, (pt[0] - m, pt[1] + m * 1.4), (pt[0] + m, pt[1] + m * 1.4), pt]   # 꼭짓점이 재는 점을 가리키는 역삼각형
    for i in range(3):
        _ln(out, d, lay, w, col, tri[i], tri[i + 1])
    _leader(out, d, lay, w, col, pt, lab)
    _txt(out, d, lay, col, _above(lab, 0.0, sc), p["size"], p["text"] or spot_value(p, vtype, lv) or "EL ?", "start", 0.0)
    return out


def _d_spot_coord(d, p, w, col, sc, vtype, lv):
    pt, lab = p["pts"]
    m = SPOT_MARK * sc
    lay = p["layer"]
    out = []
    _ln(out, d, lay, w, col, (pt[0] - m, pt[1]), (pt[0] + m, pt[1]))
    _ln(out, d, lay, w, col, (pt[0], pt[1] - m), (pt[0], pt[1] + m))
    _leader(out, d, lay, w, col, pt, lab)
    if p["text"]:
        lines = [p["text"]]
    elif vtype in ("plan", "site") and lv is not None:      # 측량 기준점 기준(W1 1-B 3차, ANN-04)
        e, n = survey_en(lv, pt[0], pt[1])
        lines = [f"N {n:,.0f}", f"E {e:,.0f}"]
    elif vtype in ("plan", "site"):
        lines = [f"N {pt[1]:,.0f}", f"E {pt[0]:,.0f}"]          # 프로젝트 원점 기준(대지에 측량 기준점이 없다)
    else:
        lines = [f"Z {pt[1]:,.0f}", f"S {pt[0]:,.0f}"]
    step = p["size"] * sc * 1.6
    for k, t in enumerate(lines):
        _txt(out, d, lay, col, _above((lab[0], lab[1] + step * (len(lines) - 1 - k)), 0.0, sc), p["size"], t, "start", 0.0)
    return out


def slope_value(p, vtype) -> str | None:
    """경사 값 글. 단면·입면만 두 점에서 잰다(높이차 / 수평 거리, %). 평면·배치도는 높이를 모르니 None."""
    if vtype not in ("section", "elevation"):
        return None
    (x0, y0), (x1, y1) = p["pts"]
    if abs(x1 - x0) < 1e-6:
        return None
    return f"{abs(y1 - y0) / abs(x1 - x0) * 100.0:.1f}%"


def _d_spot_slope(d, p, w, col, sc, vtype, lv):
    a, b = p["pts"]
    ln = hypot(b[0] - a[0], b[1] - a[1])
    u = ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln)
    out = []
    _ln(out, d, p["layer"], w, col, a, b)
    _arrow(out, d, p["layer"], w, col, b, u, sc)   # 화살표는 낮은 쪽(두 번째 점)을 가리킨다
    ang = read_angle(_ang_of(u))
    _txt(out, d, p["layer"], col, _above(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), ang, sc), p["size"],
         p["text"] or slope_value(p, vtype) or "i=?", "middle", ang)
    return out


def cloud_points(pts, chord) -> list:
    """리비전 구름 꺾은선: 반시계 경계의 각 변을 현 길이 chord 쯤으로 나눠 바깥(진행 방향 오른쪽)으로 부푼 반원을 잇는다."""
    out = [tuple(pts[0])]
    n = len(pts)
    per = sum(hypot(pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1]) for i in range(n))
    chord = max(chord, per / CLOUD_MAX_BUMPS)   # 아주 작은 현으로 선분이 폭증하지 않게 호 개수에 상한을 둔다
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        ln = hypot(b[0] - a[0], b[1] - a[1])
        k = max(1, int(round(ln / chord)))
        for j in range(k):
            q0 = (a[0] + (b[0] - a[0]) * j / k, a[1] + (b[1] - a[1]) * j / k)
            q1 = (a[0] + (b[0] - a[0]) * (j + 1) / k, a[1] + (b[1] - a[1]) * (j + 1) / k)
            mid = ((q0[0] + q1[0]) / 2, (q0[1] + q1[1]) / 2)
            a0 = _ang_of((q0[0] - mid[0], q0[1] - mid[1]))
            arc = arc_points(mid, ln / k / 2.0, a0, a0 + 180.0)   # q0 에서 반시계 반 바퀴 = 진행 방향 오른쪽(바깥)으로 부푼다
            out.extend(arc[1:-1])
            out.append(q1)
    return out


def _rev_mark(d, lay, w, col, at, size, text, sc):
    """리비전 번호 삼각형(구름의 첫 꼭짓점에 둔다)."""
    s = size * 2.2 * sc
    h = s * sqrt(3.0) / 2.0
    tri = [(at[0] - s / 2, at[1] - h / 3), (at[0] + s / 2, at[1] - h / 3), (at[0], at[1] + 2 * h / 3)]
    out = []
    for i in range(3):
        _ln(out, d, lay, w, col, tri[i], tri[(i + 1) % 3])
    _txt(out, d, lay, col, (at[0], at[1] - size * sc * 0.4), size, text, "middle", 0.0)
    return out


ANNOT_SHAPES = {"dim_angle": _d_angle, "dim_radius": _d_radius, "dim_diameter": _d_diameter, "dim_arc": _d_arc,
                "dim_chain": _d_run, "dim_base": _d_run, "spot_elev": _d_spot_elev, "spot_coord": _d_spot_coord, "spot_slope": _d_spot_slope}


def view_drafts(g, v):
    """뷰에 속한 2D 요소의 그림 항목(마스킹 영역 먼저, 그다음 요소 id 순). 2D 요소가 없으면 빈 값.
    마스킹 영역을 먼저 내야 다른 2D 요소는 가리지 않고 모델 선만 가린다(모델 선은 2D 요소보다 먼저 그린다)."""
    ids = []
    for a in Q.rel_in(g, "annot_of", v):
        r = Q.el(g, a)
        if r is not None and r["kind"] == "draft":
            ids.append((r["params"]["shape"] != "mask", a))
    out = []
    for _, a in sorted(ids):
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
