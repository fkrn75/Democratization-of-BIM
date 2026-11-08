# SPDX-License-Identifier: MPL-2.0
"""증분 벡터 도면 질의 (스파이크 S3, 버릴 코드).

벽 의도 데이터(축선·두께·높이·조인·문)에서 평면·단면·입면의 2D 선분을 직접 만든다 ("HLR-lite").
queries.py 와 같은 규칙을 따른다: 순수 함수, 시각·난수·파일 접근 금지, 플랫폼 libm(sin, cos, hypot) 금지(sqrt 와 사칙연산만).
그래서 같은 입력이면 어느 프로세스에서나 같은 선분이 나오고, RefGraph(캐시 없음)와 Graph(증분)의 결과를 비교할 수 있다.

캐시 단위 (아키텍처 5·7절의 view.cut_lines(V,E) / view.proj_lines(V,E) 에 해당):
    wall_solid(w)            뷰와 무관한 벽 형상(조인 보정 범위 + 문 개구부로 나눈 띠(band))
    plan_pieces(V,w)         평면 절단 조각          vpieces(V,w)   단면·입면 조각(뷰 좌표)
    *_bbox(V,w), *_nbrs(V,w) 뷰 안 경계 상자와 가릴 수 있는 이웃 (이웃 집합이 같으면 전파가 멈춘다)
    plan_wall_lines(V,w)     벽 하나가 뷰에 기여하는 선분 / vwall_lines(V,w)
    view_lines(V)            뷰 하나의 최종 선분(겹침 병합 포함)

좌표: 평면은 (x, y) mm 위가 +y. 단면·입면은 (s, z): s 는 오른쪽, z 는 위. 가림 판정용 깊이 w 는 보는 사람에서 멀어질수록 크다.
알려진 한계는 spikes/s3-drawings/results/report.md 에 적었다.
"""
from __future__ import annotations

import hashlib
import json
from math import sqrt

from dobim_core import queries as Q
from dobim_core.graph import CLEAN

EPS = 0.01          # 기하 허용 오차(mm): 같은 선·같은 점 판정
EPS_V = 0.5         # 가림 판정에서 "충분히 앞"으로 칠 깊이 차(mm)
SEG_MIN = 0.05      # 이보다 짧은 선분은 버린다
BIG = 1.0e9
ARC_N = 8           # 문 회전 호의 분할 수
NBR_PAD = 1.0       # 이웃 판정 경계 상자 여유(mm)
CORNER_FILL = True  # L 조인 모서리를 채워 그린다 (코어 투영은 축선 교점까지만 잘라 모서리가 비어 있다)
MIN_SIN = 0.2       # 조인 각이 너무 얕으면(sin 이 이보다 작으면) 모서리 보정을 하지 않는다
PRIORITY = {"cut": 3, "symbol": 2, "proj": 1}

ELEV_DIR = {"N": (0.0, -1.0), "E": (-1.0, 0.0), "S": (0.0, 1.0), "W": (1.0, 0.0)}   # 건물을 바라보는 방향
VIEW_TYPES = ("plan", "section", "elevation")


# ================================================================ 범위 질의
def all_walls(g):
    out = []
    for b in range(Q.NB):
        out.extend(Q.bucket_ids(g, "wall", b))
    return tuple(sorted(out))


def all_views(g):
    out = []
    for b in range(Q.NB):
        out.extend(Q.bucket_ids(g, "view", b))
    return tuple(sorted(out))


def level_walls(g, lv):
    return tuple(sorted(Q.rel_in(g, "on_level", lv)))


def view_cfg(g, v):
    """뷰 설정을 계산용 튜플로 푼다.
    평면: ("plan", 레벨, 절단 z, 보이는 깊이, 축척)
    그 밖: (종류, ox, oy, 보는방향 dx, dy, 오른쪽 rx, ry, near, far, 절단 여부, 축척)"""
    row = Q.el(g, v)
    if row is None or row["kind"] != "view":
        return None
    p = row["params"]
    t, scale = p["type"], p["scale"]
    if t == "plan":
        lv = p["level"]
        if Q.el(g, lv) is None:
            return ("plan", None, 0.0, 0.0, scale)
        _, elev = g.q(Q.level_info, lv)
        return ("plan", lv, elev + p["cut_height"], p["view_depth"], scale)
    if t == "elevation":
        d = ELEV_DIR[p["dir"]]
        return ("elev", 0.0, 0.0, d[0], d[1], d[1], -d[0], -BIG, BIG, False, scale)
    x1, y1 = p["p1"]
    x2, y2 = p["p2"]
    dx, dy = x2 - x1, y2 - y1
    length = sqrt(dx * dx + dy * dy)
    lx, ly = dx / length, dy / length
    fx, fy = (-ly, lx) if not p.get("flip") else (ly, -lx)    # 보는 방향: 선의 왼쪽(기본) 또는 오른쪽
    return ("sec", x1, y1, fx, fy, fy, -fx, 0.0, p["depth"], True, scale)


def scope_walls(g, v):
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    if cfg[0] == "plan":
        return () if cfg[1] is None else g.q(level_walls, cfg[1])
    return g.q(all_walls)


# ================================================================ 벽 형상 (뷰와 무관)
def wall_range(g, w):
    """벽 지역 x 범위. 코어의 조인 자르기(wall_trim)에 L 조인 모서리 보정을 더한다: id 가 작은 벽이 상대 벽 반 두께만큼 더 나간다."""
    start, end, status = g.q(Q.wall_trim, w)
    if not CORNER_FILL or status != "ok":
        return (start, end)
    x, y, c, s, length = g.q(Q.wall_frame, w)
    for o in g.q(Q.wall_joins, w):
        if not w < o:
            continue
        pt = g.q(Q.join_point, w, o)
        if pt is None:
            continue
        ox, oy, oc, os_, olen = g.q(Q.wall_frame, o)
        t = (pt[0] - x) * c + (pt[1] - y) * s
        to = (pt[0] - ox) * oc + (pt[1] - oy) * os_
        if min(abs(to), abs(to - olen)) > Q.JOIN_MAX_EXT:
            continue                                      # 상대 벽의 끝이 아니라 중간에 닿는 T 조인: 보정 없음
        sin = c * os_ - s * oc
        if abs(sin) < MIN_SIN:
            continue
        ext = Q.el(g, o)["params"]["thickness"] / 2.0 / abs(sin)
        if abs(t) <= abs(t - length):
            if abs(t) <= Q.JOIN_MAX_EXT:
                start = min(start, t - ext)
        elif abs(t - length) <= Q.JOIN_MAX_EXT:
            end = max(end, t + ext)
    return (start, end)


def wall_openings(g, w):
    """호스팅된 문의 개구부: (u0, u1, z0, z1) 정렬 튜플. 벽 범위 안으로 자른다."""
    start, end, _ = g.q(Q.wall_trim, w)
    p = Q.el(g, w)["params"]
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    zb, zt = elev, elev + p["height"]
    out = []
    for d in g.q(Q.wall_hosted, w):
        dp = Q.el(g, d)["params"]
        u0 = max(start, dp["offset"] - dp["width"] / 2.0)
        u1 = min(end, dp["offset"] + dp["width"] / 2.0)
        z0 = max(zb, elev + dp["sill"])
        z1 = min(zt, elev + dp["sill"] + dp["height"])
        if u1 - u0 > EPS and z1 - z0 > EPS:
            out.append((u0, u1, z0, z1))
    return tuple(sorted(out))


def _bands(start, end, zb, zt, ops):
    """높이를 개구부 경계로 나눠, 각 띠에서 막힌 구간을 뺀 속이 찬 u 구간을 만든다. 같은 구간이 이어지는 띠는 합친다."""
    zs = sorted({zb, zt, *(o[2] for o in ops), *(o[3] for o in ops)})
    bands = []
    for z0, z1 in zip(zs, zs[1:]):
        if z1 - z0 < EPS:
            continue
        mid = (z0 + z1) / 2.0
        cur, ivs = start, []
        for a, b in sorted((o[0], o[1]) for o in ops if o[2] < mid < o[3]):
            if a > cur + EPS:
                ivs.append((cur, a))
            cur = max(cur, b)
        if end > cur + EPS:
            ivs.append((cur, end))
        ivs = tuple(ivs)
        if bands and bands[-1][2] == ivs:
            bands[-1] = (bands[-1][0], z1, ivs)
        else:
            bands.append((z0, z1, ivs))
    return tuple(bands)


def wall_solid(g, w):
    """(x, y, c, s, half, start, end, zb, zt, openings, bands). 벽 지역 (u,v) -> 세계 (x+u*c-v*s, y+u*s+v*c)."""
    x, y, c, s, _ = g.q(Q.wall_frame, w)
    p = Q.el(g, w)["params"]
    start, end = g.q(wall_range, w)
    ops = g.q(wall_openings, w)
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    zb, zt = elev, elev + p["height"]
    return (x, y, c, s, p["thickness"] / 2.0, start, end, zb, zt, ops, _bands(start, end, zb, zt, ops))


def _world(sol, u, v):
    return (sol[0] + u * sol[2] - v * sol[3], sol[1] + u * sol[3] + v * sol[2])


def _quad(sol, a, b):
    h = sol[4]
    return (_world(sol, a, -h), _world(sol, b, -h), _world(sol, b, h), _world(sol, a, h))   # 반시계


# ================================================================ 기하 도우미
def _bbox_pts(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _inside_interval(p0, ux, uy, length, q):
    """선분 p0 + t*u (0<=t<=length) 이 볼록 다각형 q(반시계) 의 안쪽(경계에서 EPS 이상)에 있는 t 구간."""
    lo, hi = 0.0, length
    n = len(q)
    for i in range(n):
        a, b = q[i], q[(i + 1) % n]
        ex, ey = b[0] - a[0], b[1] - a[1]
        el_ = sqrt(ex * ex + ey * ey)
        if el_ < 1e-12:
            continue
        nx, ny = ey / el_, -ex / el_                       # 바깥 법선
        d0 = (p0[0] - a[0]) * nx + (p0[1] - a[1]) * ny + EPS   # 안쪽이려면 d0 + t*sl < 0
        sl = ux * nx + uy * ny
        if -1e-12 < sl < 1e-12:
            if d0 >= 0.0:
                return None
        elif sl > 0.0:
            hi = min(hi, -d0 / sl)
        else:
            lo = max(lo, -d0 / sl)
        if hi - lo <= EPS:
            return None
    return (lo, hi) if hi - lo > EPS else None


def outline_edges(mine, others, my_key):
    """볼록 다각형(반시계) 묶음 mine 의 합집합 윤곽 중, others 와 합쳐질 때 남는 변만 돌려준다.
    다른 조각의 안쪽에 든 부분과, 반대 방향으로 맞닿은 부분(내부 이음매)은 지운다.
    같은 방향으로 겹친 부분은 (키, 번호) 가 작은 쪽만 남긴다. others: [(키, 다각형)]. 반환: [(p0, p1)]"""
    allp = [(my_key, i, p) for i, p in enumerate(mine)] + [(k, 0, p) for k, p in others]
    boxes = [_bbox_pts(p) for _, _, p in allp]
    out = []
    for i, poly in enumerate(mine):
        n = len(poly)
        for j in range(n):
            p0, p1 = poly[j], poly[(j + 1) % n]
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            length = sqrt(dx * dx + dy * dy)
            if length < EPS:
                continue
            ux, uy = dx / length, dy / length
            ex0, ey0 = min(p0[0], p1[0]) - EPS, min(p0[1], p1[1]) - EPS
            ex1, ey1 = max(p0[0], p1[0]) + EPS, max(p0[1], p1[1]) + EPS
            cuts = []
            for idx, (k, oi, q) in enumerate(allp):
                if k == my_key and oi == i:
                    continue
                bb = boxes[idx]
                if bb[2] < ex0 or bb[0] > ex1 or bb[3] < ey0 or bb[1] > ey1:
                    continue
                iv = _inside_interval(p0, ux, uy, length, q)
                if iv is not None:
                    cuts.append(iv)
                m = len(q)
                for r in range(m):
                    r0, r1 = q[r], q[(r + 1) % m]
                    c0 = (r0[0] - p0[0]) * uy - (r0[1] - p0[1]) * ux
                    c1 = (r1[0] - p0[0]) * uy - (r1[1] - p0[1]) * ux
                    if abs(c0) > EPS or abs(c1) > EPS:
                        continue
                    t0 = (r0[0] - p0[0]) * ux + (r0[1] - p0[1]) * uy
                    t1 = (r1[0] - p0[0]) * ux + (r1[1] - p0[1]) * uy
                    lo, hi = max(min(t0, t1), 0.0), min(max(t0, t1), length)
                    if hi - lo <= EPS:
                        continue
                    if t1 < t0 or (k, oi) < (my_key, i):    # 반대 방향(내부 이음매) 또는 같은 방향이면서 상대가 우선
                        cuts.append((lo, hi))
            out.extend(_keep(p0, ux, uy, length, cuts))
    return out


def _keep(p0, ux, uy, length, cuts):
    cuts.sort()
    segs, cur = [], 0.0
    for lo, hi in cuts:
        if lo > cur + EPS:
            segs.append((cur, lo))
        cur = max(cur, hi)
    if length > cur + EPS:
        segs.append((cur, length))
    return [((p0[0] + a * ux, p0[1] + a * uy), (p0[0] + b * ux, p0[1] + b * uy)) for a, b in segs if b - a > SEG_MIN]


# ================================================================ 평면
def plan_pieces(g, v, w):
    """(절단 조각, 투영 조각): 절단 높이에서 자른 벽의 속이 찬 구간 사각형들 / 절단면 아래에 끝나는 벽의 윤곽."""
    cfg = g.q(view_cfg, v)
    sol = g.q(wall_solid, w)
    cutz, depth = cfg[2], cfg[3]
    zb, zt = sol[7], sol[8]
    cut, proj = [], []
    if zb - EPS <= cutz <= zt + EPS:
        for z0, z1, ivs in sol[10]:
            if z0 - EPS <= cutz <= z1 + EPS:
                cut = [_quad(sol, a, b) for a, b in ivs]
                break
    elif zt < cutz - EPS and zt >= cutz - depth:
        proj = [_quad(sol, sol[5], sol[6])]
    return (tuple(cut), tuple(proj))


def plan_bbox(g, v, w):
    cut, proj = g.q(plan_pieces, v, w)
    pts = [p for q in cut + proj for p in q]
    return _bbox_pts(pts) if pts else None


def _nbrs(g, bbfn, v, w):
    me = g.q(bbfn, v, w)
    if me is None:
        return ()
    out = []
    for o in g.q(scope_walls, v):
        if o == w:
            continue
        bb = g.q(bbfn, v, o)
        if bb is not None and bb[0] <= me[2] + NBR_PAD and me[0] <= bb[2] + NBR_PAD \
                and bb[1] <= me[3] + NBR_PAD and me[1] <= bb[3] + NBR_PAD:
            out.append(o)
    return tuple(out)


def plan_nbrs(g, v, w):
    return _nbrs(g, plan_bbox, v, w)


def _door_symbol(sol, op):
    """문 한 개의 평면 기호: 열린 문짝 선 + 1/4 원 호. +v 쪽으로 열리고 경첩은 개구부의 시작 쪽이다.
    호는 유리식 매개화로 sin/cos 없이 그린다: 각 = 2*atan(t) (t 0..1 -> 0..90도)."""
    u0, u1 = op[0], op[1]
    h = sol[4]
    width = u1 - u0
    hinge = (u0, h)
    pts = []
    for i in range(ARC_N + 1):
        t = i / ARC_N
        k = 1.0 + t * t
        pts.append(_world(sol, hinge[0] + width * (1.0 - t * t) / k, hinge[1] + width * (2.0 * t) / k))
    out = [("symbol", *_world(sol, *hinge), *_world(sol, u0, h + width))]
    for a, b in zip(pts, pts[1:]):
        out.append(("symbol", a[0], a[1], b[0], b[1]))
    return out


def plan_wall_lines(g, v, w):
    cfg = g.q(view_cfg, v)
    cut, proj = g.q(plan_pieces, v, w)
    sol = g.q(wall_solid, w)
    lines = []
    if cut or proj:
        nb = g.q(plan_nbrs, v, w)
        if cut:
            others = [(o, q) for o in nb for q in g.q(plan_pieces, v, o)[0]]
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(cut), others, w)]
        if proj:
            others = [(o, q) for o in nb for q in g.q(plan_pieces, v, o)[1]]
            lines += [("proj", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(proj), others, w)]
    if cut:
        for op in sol[9]:
            if op[2] <= cfg[2] + EPS:
                lines += _door_symbol(sol, op)
    return tuple(lines)


# ================================================================ 단면·입면 (뷰 좌표 (s, w, z))
def _sw(cfg, p):
    px, py = p[0] - cfg[1], p[1] - cfg[2]
    return (px * cfg[5] + py * cfg[6], px * cfg[3] + py * cfg[4])


def _clip_w(poly, lo, hi):
    """볼록 다각형을 lo <= w <= hi 로 자른다 (Sutherland-Hodgman). 새 꼭짓점의 w 는 경계값 그대로 둔다."""
    for bound, keep_ge in ((lo, True), (hi, False)):
        if abs(bound) >= BIG:
            continue
        out = []
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            ina = (a[1] >= bound) if keep_ge else (a[1] <= bound)
            inb = (b[1] >= bound) if keep_ge else (b[1] <= bound)
            if ina:
                out.append(a)
            if ina != inb:
                tt = (bound - a[1]) / (b[1] - a[1])
                out.append((a[0] + tt * (b[0] - a[0]), bound))
        poly = out
        if len(poly) < 3:
            return []
    return poly


def _area2(poly):
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))


def vpieces(g, v, w):
    """벽을 속이 찬 볼록 기둥 조각들로: ((z0, z1, 다각형(s,w)), ...). near/far 로 자른 뒤 남는 것만."""
    cfg = g.q(view_cfg, v)
    sol = g.q(wall_solid, w)
    out = []
    for z0, z1, ivs in sol[10]:
        for a, b in ivs:
            poly = [_sw(cfg, p) for p in _quad(sol, a, b)]
            poly = _clip_w(poly, cfg[7], cfg[8])
            if len(poly) >= 3 and _area2(poly) > 2 * EPS:
                out.append((z0, z1, tuple(poly)))
    return tuple(out)


def vbbox(g, v, w):
    ps = g.q(vpieces, v, w)
    if not ps:
        return None
    ss = [p[0] for _, _, poly in ps for p in poly]
    return (min(ss), min(z0 for z0, _, _ in ps), max(ss), max(z1 for _, z1, _ in ps))


def vnbrs(g, v, w):
    return _nbrs(g, vbbox, v, w)


def vcut_rects(g, v, w):
    """단면: 절단면(w=near)과 만나는 조각의 (s, z) 사각형. 입면은 없음."""
    cfg = g.q(view_cfg, v)
    if not cfg[9]:
        return ()
    near = cfg[7]
    rects = []
    for z0, z1, poly in g.q(vpieces, v, w):
        ss = [p[0] for p in poly if abs(p[1] - near) <= EPS]
        if len(ss) < 2:
            continue
        s0, s1 = min(ss), max(ss)
        if s1 - s0 > EPS:
            rects.append(((s0, z0), (s1, z0), (s1, z1), (s0, z1)))
    return tuple(rects)


def _clip_edge(e0, e1, near, far):
    """3D 선분 (s,w,z) 을 near<=w<=far 로 자른다. 밖이면 None."""
    dw = e1[1] - e0[1]
    t0, t1 = 0.0, 1.0
    if abs(dw) < 1e-12:
        if e0[1] < near - EPS or e0[1] > far + EPS:
            return None
        return (e0, e1)
    ta, tb = (near - e0[1]) / dw, (far - e0[1]) / dw
    if ta > tb:
        ta, tb = tb, ta
    t0, t1 = max(t0, ta), min(t1, tb)
    if t1 - t0 <= 1e-9:
        return None
    lerp = lambda t: (e0[0] + t * (e1[0] - e0[0]), e0[1] + t * dw, e0[2] + t * (e1[2] - e0[2]))   # noqa: E731
    return (lerp(t0), lerp(t1)) if (t0 > 0.0 or t1 < 1.0) else (e0, e1)


def vedges(g, v, w):
    """벽의 날카로운 모서리(수직·수평)를 뷰 좌표 3D 선분 (s,w,z) 쌍으로. 문 개구부의 앞뒤 윤곽과 두께 방향 모서리를 포함한다."""
    cfg = g.q(view_cfg, v)
    sol = g.q(wall_solid, w)
    near, far = cfg[7], cfg[8]
    h, start, end, zb, zt, ops = sol[4], sol[5], sol[6], sol[7], sol[8], sol[9]

    def pt(u, vv, z):
        s_, w_ = _sw(cfg, _world(sol, u, vv))
        return (s_, w_, z)

    raw = []
    corners = [(start, -h), (end, -h), (end, h), (start, h)]
    for u, vv in corners:                                          # 수직 모서리
        raw.append((pt(u, vv, zb), pt(u, vv, zt)))
    for i in range(4):                                             # 밑·윗면 수평 모서리
        for z in (zb, zt):
            raw.append((pt(*corners[i], z), pt(*corners[(i + 1) % 4], z)))
    for u0, u1, z0, z1 in ops:
        zs = (z1,) + ((z0,) if z0 > zb + EPS else ())
        for vv in (-h, h):
            for u in (u0, u1):
                raw.append((pt(u, vv, z0), pt(u, vv, z1)))        # 문설주
            for z in zs:
                raw.append((pt(u0, vv, z), pt(u1, vv, z)))        # 인방·문턱
        for u in (u0, u1):
            for z in zs:
                raw.append((pt(u, -h, z), pt(u, h, z)))           # 개구부 안쪽 면(두께 방향)
    out = []
    for e0, e1 in raw:
        if abs(e1[0] - e0[0]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue                                               # 화면에서 한 점으로 보이는 선
        c = _clip_edge(e0, e1, near, far)
        if c is not None:
            out.append(c)
    return tuple(out)


def _hidden_h(poly, e0, e1):
    """수평 선분(z 고정) e0->e1 (s,w,z) 중 다각형 조각(z 범위는 호출 쪽이 확인)에 가려지는 t 구간들."""
    smin = min(p[0] for p in poly) + 1e-6
    smax = max(p[0] for p in poly) - 1e-6
    ds, dw = e1[0] - e0[0], e1[1] - e0[1]
    res = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if b[0] - a[0] <= 1e-9:
            continue                                               # 반시계 다각형에서 오른쪽으로 가는 변이 아랫변(관찰자 쪽)이다
        m = (b[1] - a[1]) / (b[0] - a[0])
        lo, hi = max(a[0], smin), min(b[0], smax)
        if hi <= lo:
            continue
        # 가림 조건: w(t) - 아랫변(s(t)) > EPS_V  ->  A + B t > 0
        aa = e0[1] - a[1] - m * (e0[0] - a[0]) - EPS_V
        bb = dw - m * ds
        if abs(ds) < 1e-9:
            if not lo <= e0[0] <= hi:
                continue
            ta, tb = 0.0, 1.0
        else:
            ta, tb = (lo - e0[0]) / ds, (hi - e0[0]) / ds
            if ta > tb:
                ta, tb = tb, ta
            ta, tb = max(ta, 0.0), min(tb, 1.0)
        if abs(bb) < 1e-12:
            if aa <= 0.0:
                continue
        elif bb > 0.0:
            ta = max(ta, -aa / bb)
        else:
            tb = min(tb, -aa / bb)
        if tb - ta > 1e-9:
            res.append((ta, tb))
    return res


def _hit_point(poly, s, w):
    smin = min(p[0] for p in poly)
    smax = max(p[0] for p in poly)
    if s <= smin + 1e-6 or s >= smax - 1e-6:
        return False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if b[0] - a[0] > 1e-9 and a[0] <= s <= b[0]:
            if w - (a[1] + (b[1] - a[1]) * (s - a[0]) / (b[0] - a[0])) > EPS_V:
                return True
    return False


def visible_parts(e0, e1, occluders):
    """3D 선분 e0->e1 의 보이는 매개변수 구간들. occluders: ((z0, z1, 다각형(s,w)), ...) — 속이 찬 기둥 조각."""
    hidden = []
    dz = e1[2] - e0[2]
    vertical = abs(e1[0] - e0[0]) < 1e-9 and abs(e1[1] - e0[1]) < 1e-9
    zlo, zhi = min(e0[2], e1[2]), max(e0[2], e1[2])
    for z0, z1, poly in occluders:
        if vertical:
            if z1 <= zlo or z0 >= zhi:
                continue
            if _hit_point(poly, e0[0], e0[1]):
                ta, tb = (max(z0, zlo) - e0[2]) / dz, (min(z1, zhi) - e0[2]) / dz
                hidden.append((min(ta, tb), max(ta, tb)))
        else:
            if not z0 + EPS < e0[2] < z1 - EPS:
                continue
            hidden.extend(_hidden_h(poly, e0, e1))
    if not hidden:
        return [(0.0, 1.0)]
    hidden.sort()
    out, cur = [], 0.0
    for a, b in hidden:
        if a > cur + 1e-9:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < 1.0 - 1e-9:
        out.append((cur, 1.0))
    return out


def vwall_lines(g, v, w):
    cfg = g.q(view_cfg, v)
    nb = g.q(vnbrs, v, w)
    occ = list(g.q(vpieces, v, w))
    for o in nb:
        occ.extend(g.q(vpieces, v, o))
    lines = []
    if occ:
        for e0, e1 in g.q(vedges, v, w):
            for t0, t1 in visible_parts(e0, e1, occ):
                x0, z0 = e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2])
                x1, z1 = e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])
                lines.append(("proj", x0, z0, x1, z1))
    if cfg[9]:
        mine = g.q(vcut_rects, v, w)
        if mine:
            others = [(o, q) for o in nb for q in g.q(vcut_rects, v, o)]
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(mine), others, w)]
    return tuple(lines)


# ================================================================ 뷰 전체
def merge_lines(segs):
    """선분 묶음을 정규화한다: 같은 직선 위에서 겹치거나 이어진 선분은 하나로, 절단선(cut)이 덮는 부분은 아래 등급에서 지운다.
    결과는 정렬된 ((등급, x0, y0, x1, y1), ...) 이고 좌표는 마이크로미터로 반올림한다."""
    groups: dict = {}
    for cls, x0, y0, x1, y1 in segs:
        dx, dy = x1 - x0, y1 - y0
        length = sqrt(dx * dx + dy * dy)
        if length < SEG_MIN:
            continue
        ux, uy = dx / length, dy / length
        if ux < -1e-9 or (abs(ux) <= 1e-9 and uy < 0.0):
            ux, uy = -ux, -uy
        off = -uy * x0 + ux * y0
        key = (round(ux, 5), round(uy, 5), round(off, 2))
        grp = groups.setdefault(key, [ux, uy, off, {}])
        gx, gy = grp[0], grp[1]                       # 같은 직선 묶음은 첫 선분의 방향으로 매개변수를 잰다
        t0, t1 = sorted((gx * x0 + gy * y0, gx * x1 + gy * y1))
        grp[3].setdefault(cls, []).append((t0, t1))
    out = []
    for key in sorted(groups):
        ux, uy, off, by_cls = groups[key]
        merged = {}
        for cls, ivs in by_cls.items():
            ivs.sort()
            m = []
            for a, b in ivs:
                if m and a <= m[-1][1] + EPS:
                    m[-1][1] = max(m[-1][1], b)
                else:
                    m.append([a, b])
            merged[cls] = m
        if "cut" in merged:
            for cls in merged:
                if cls != "cut":
                    merged[cls] = _subtract(merged[cls], merged["cut"])
        for cls in sorted(merged):
            for a, b in merged[cls]:
                if b - a > SEG_MIN:
                    out.append((cls, round(-uy * off + ux * a, 3), round(ux * off + uy * a, 3),
                                round(-uy * off + ux * b, 3), round(ux * off + uy * b, 3)))
    return tuple(out)


def _subtract(ivs, cuts):
    out = []
    for a, b in ivs:
        cur = a
        for c0, c1 in cuts:
            if c1 <= cur or c0 >= b:
                continue
            if c0 > cur:
                out.append([cur, c0])
            cur = max(cur, c1)
        if cur < b:
            out.append([cur, b])
    return out


def view_lines(g, v):
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    fn = plan_wall_lines if cfg[0] == "plan" else vwall_lines
    segs = []
    for w in g.q(scope_walls, v):
        segs.extend(g.q(fn, v, w))
    return merge_lines(segs)


def view_fills(g, v):
    """절단 단면의 채움 다각형(평면: 벽 절단 사각형, 단면: 절단 사각형). 입면은 없음."""
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    out = []
    for w in g.q(scope_walls, v):
        if cfg[0] == "plan":
            out.extend(g.q(plan_pieces, v, w)[0])
        else:
            out.extend(g.q(vcut_rects, v, w))
    return tuple(tuple((round(p[0], 3), round(p[1], 3)) for p in poly) for poly in out)


def view_summary(lines):
    by = {}
    for ln in lines:
        by[ln[0]] = by.get(ln[0], 0) + 1
    if lines:
        xs = [c for ln in lines for c in (ln[1], ln[3])]
        ys = [c for ln in lines for c in (ln[2], ln[4])]
        bbox = [min(xs), min(ys), max(xs), max(ys)]
    else:
        bbox = None
    digest = hashlib.sha256(json.dumps(lines, separators=(",", ":")).encode("ascii")).hexdigest()[:16]
    return {"segments": len(lines), "by_class": dict(sorted(by.items())), "bbox": bbox, "digest": digest}


def view_cache_state(g, vids):
    """뷰마다 선분 캐시의 상태: "fresh"(최신), "stale"(입력이 바뀌어 다시 계산 필요), "new"(아직 한 번도 안 만듦)."""
    out = {}
    for v in vids:
        node = g.nodes.get((view_lines, v))
        out[v] = "new" if node is None else ("fresh" if node.state == CLEAN else "stale")
    return out


def views_depending_on(g, input_keys, vids):
    """입력 노드들에서 역의존을 따라가 도달하는 뷰 선분 노드의 뷰 id. 아직 계산한 적 없는 뷰(캐시 없음)는 보수적으로 포함한다."""
    seen, todo = set(), []
    for k in input_keys:
        n = g.nodes.get(k)
        if n is not None:
            todo.append(n)
    hit = set()
    while todo:
        n = todo.pop()
        if id(n) in seen:
            continue
        seen.add(id(n))
        if n.key[0] is view_lines:
            hit.add(n.key[1])
        todo.extend(n.rdeps)
    return sorted(hit | {v for v in vids if (view_lines, v) not in g.nodes})
