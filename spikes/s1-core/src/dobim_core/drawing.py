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
알려진 한계는 spikes/s3-drawings/report.md 에 적었다.
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
ARC_N = 8           # 문 회전 호의 분할 수(90도당)
CIRC_N = 24         # 원 단면을 다각형으로 근사할 때 분할 수
PI = 3.141592653589793
NBR_PAD = 1.0       # 이웃 판정 경계 상자 여유(mm)
CORNER_FILL = True  # L 조인 모서리를 채워 그린다 (코어 투영은 축선 교점까지만 잘라 모서리가 비어 있다)
MIN_SIN = 0.2       # 조인 각이 너무 얕으면(sin 이 이보다 작으면) 모서리 보정을 하지 않는다
PRIORITY = {"cut": 3, "symbol": 2, "proj": 1}

ELEV_DIR = {"N": (0.0, -1.0), "E": (-1.0, 0.0), "S": (0.0, 1.0), "W": (1.0, 0.0)}   # 건물을 바라보는 방향
VIEW_TYPES = ("plan", "section", "elevation")


# ================================================================ 범위 질의
def is_drawn_wall(g, w) -> bool:
    """도면이 그리는 벽인가: 종류가 벽이고 저작(authored)일 때만. 외래(foreign)는 형상 정보가 없고(params 에 axis 가 없다),
    분리(detached)된 벽의 params 는 외부 도구가 형상을 바꾸기 전의 낡은 의도라 그리면 틀린 선이 나온다(ADR-0005)."""
    row = Q.el(g, w)
    return row is not None and row["kind"] == "wall" and row["origin"] == "authored"


def _raw_walls(g):
    """벽 종류 요소 전체(출처 무관)."""
    out = []
    for b in range(Q.NB):
        out.extend(Q.bucket_ids(g, "wall", b))
    return tuple(sorted(out))


def all_walls(g):
    return tuple(w for w in g.q(_raw_walls) if g.q(is_drawn_wall, w))


def all_views(g):
    out = []
    for b in range(Q.NB):
        out.extend(Q.bucket_ids(g, "view", b))
    return tuple(sorted(out))


def level_walls(g, lv):
    # on_level 에는 외래 요소(kind foreign)와 패밀리 인스턴스(finst)도 걸린다: 그리는 벽만 남긴다
    return tuple(w for w in sorted(Q.rel_in(g, "on_level", lv)) if g.q(is_drawn_wall, w))


def view_omitted(g, v):
    """뷰가 그리지 않고 생략한 모델 요소 개수 {"foreign": 외래 요소, "detached": 분리된 벽}. 생략은 조용하지 않게 응답에 알린다.
    평면은 그 레벨에 놓인 것만, 단면·입면은 모델 전체를 센다."""
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return {"foreign": 0, "detached": 0}
    if cfg[0] == "plan":
        ids = () if cfg[1] is None else Q.rel_in(g, "on_level", cfg[1])
        foreign = sum(1 for e in ids if (Q.el(g, e) or {}).get("kind") == "foreign")
        detached = sum(1 for e in ids if (r := Q.el(g, e)) is not None and r["kind"] == "wall" and r["origin"] == "detached")
        return {"foreign": foreign, "detached": detached}
    detached = sum(1 for w in g.q(_raw_walls) if Q.el(g, w)["origin"] == "detached")
    return {"foreign": Q.count(g, "foreign"), "detached": detached}


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
        lrow = Q.el(g, lv)
        if lrow is None or lrow["kind"] != "level":   # 없거나 레벨이 아닌 요소(예전 데이터의 잘못된 참조)면 고아 뷰로 그린다(터지지 않게)
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


def hosted_opening(g, d, elev):
    """벽에 호스팅된 요소 하나가 벽에 내는 개구부 (u0, u1, z0, z1)(벽 지역 길이 방향 u, 절대 높이 z). 개구부가 없으면 None.
    호스팅되는 것은 문(door: offset ± width/2, 문턱 sill)만이 아니다: 패밀리 인스턴스(finst)는 sill 이 없고 평가된 개구부 범위(cut)와
    elevation 을 가진다(S3 가 모든 호스팅 요소를 문이라고 가정해 KeyError 로 죽던 자리). 그 밖의 종류는 개구부가 없다."""
    row = Q.el(g, d)
    if row is None:
        return None
    p = row["params"]
    if row["kind"] == "door":
        return (p["offset"] - p["width"] / 2.0, p["offset"] + p["width"] / 2.0, elev + p["sill"], elev + p["sill"] + p["height"])
    if row["kind"] == "finst":
        spec = g.q(Q.ifc_spec, d)
        cut = spec.get("cut")
        if not cut:
            return None   # 평가 오류이거나 개구부 없는 인스턴스
        return (spec["offset"] + cut["x"][0], spec["offset"] + cut["x"][1],
                elev + spec["elevation"] + cut["z"][0], elev + spec["elevation"] + cut["z"][1])
    return None


def wall_openings(g, w):
    """호스팅된 요소(문·창·패밀리 인스턴스)의 개구부: (u0, u1, z0, z1) 정렬 튜플. 벽 범위 안으로 자른다."""
    return _openings(g, w, False)


def wall_door_openings(g, w):
    """호스팅된 문(kind door)만의 개구부. 평면 문 기호(열린 문짝 + 호)는 이 개구부에만 그린다 — 창에 호를 그리면 문으로 읽힌다."""
    return _openings(g, w, True)


def _openings(g, w, doors_only):
    start, end, _ = g.q(Q.wall_trim, w)
    p = Q.el(g, w)["params"]
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    zb, zt = elev, elev + p["height"]
    out = []
    for d in g.q(Q.wall_hosted, w):
        if doors_only and Q.el(g, d)["kind"] != "door":
            continue
        box = g.q(hosted_opening, d, elev)
        if box is None:
            continue
        u0 = max(start, box[0])
        u1 = min(end, box[1])
        z0 = max(zb, box[2])
        z1 = min(zt, box[3])
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


# ================================================================ 패밀리 요소 (창·패밀리 문·기둥)
# 패밀리 인스턴스(finst)의 평가 결과(ifc_spec: 평면 기호 plan, 형상 body)를 그대로 그린다. 새 요소 종류를 만들지 않고 이미 IFC 로 나가는 패밀리를 쓴다.
def _floor(x):
    """바닥 함수(math.floor 대신: 이 모듈은 math 에서 sqrt 만 쓴다). int() 는 0 쪽으로 자르므로 음수를 보정한다."""
    i = int(x)
    return i if i <= x else i - 1


def _sincos_deg(a):
    """도 -> (sin, cos). 플랫폼 libm 을 쓰지 않는다(결정성): 90도 단위로 줄인 뒤 테일러 급수(사칙연산만)."""
    a = a - 360.0 * _floor(a / 360.0)
    k = _floor((a + 45.0) / 90.0)
    x = (a - 90.0 * k) * PI / 180.0
    x2 = x * x
    sn = cs = 0.0
    tn, tc = x, 1.0
    for i in range(1, 9):
        sn += tn
        cs += tc
        tn = -tn * x2 / ((2 * i) * (2 * i + 1))
        tc = -tc * x2 / ((2 * i - 1) * (2 * i))
    return [(sn, cs), (cs, -sn), (-sn, -cs), (-cs, sn)][k % 4]


def _freeze_plan(items):
    out = []
    for it in items:
        t, st = it["t"], it.get("s", "projection")
        if t == "line":
            out.append(("line", tuple(it["a"]), tuple(it["b"]), st))
        elif t == "arc":
            out.append(("arc", tuple(it["c"]), it["r"], it["a0"], it["a1"], st))
        elif t == "circle":
            out.append(("circle", tuple(it["c"]), it["r"], st))
        else:
            out.append(("poly", tuple(tuple(p) for p in it["pts"]), bool(it["closed"]), st))
    return tuple(out)


def _circle_pts(c, r, n=CIRC_N):
    pts = []
    for i in range(n):
        sn, cs = _sincos_deg(360.0 * i / n)
        pts.append((c[0] + r * cs, c[1] + r * sn))
    return pts


def _plan_items(items, to_world, fills=None):
    """평면 기호 항목들 -> [(등급, x0, y0, x1, y1)]. 절단 스타일은 'cut'(굵은 선), 투영은 'symbol'. fills 가 리스트면 닫힌 절단 도형의 채움 다각형도 모은다."""
    segs = []

    def add(p0, p1, st):
        a, b = to_world(*p0), to_world(*p1)
        segs.append(("cut" if st == "cut" else "symbol", a[0], a[1], b[0], b[1]))

    for it in items:
        t, st = it[0], it[-1]
        if t == "line":
            add(it[1], it[2], st)
        elif t == "arc":
            _, c, r, a0, a1, _ = it
            n = max(1, -int(-((a1 - a0) / 90.0 * ARC_N) // 1))
            pts = []
            for i in range(n + 1):
                sn, cs = _sincos_deg(a0 + (a1 - a0) * i / n)
                pts.append((c[0] + r * cs, c[1] + r * sn))
            for p0, p1 in zip(pts, pts[1:]):
                add(p0, p1, st)
        else:
            if t == "circle":
                pts, closed = _circle_pts(it[1], it[2]), True
            else:
                pts, closed = list(it[1]), it[2]
            for i in range(len(pts) - (0 if closed else 1)):
                add(pts[i], pts[(i + 1) % len(pts)], st)
            if closed and st == "cut" and fills is not None:
                fills.append(tuple(to_world(*p) for p in pts))
    return segs


def _body_boxes(body):
    """body 의 xz 평면 직사각 압출을 상자 (u0, u1, z0, z1, n0, n1) 로. 구멍(cuts)은 부모 두께 안으로 자른 상자를 더한다. 다른 형상은 건너뛴다."""
    out = []
    for b in body:
        if b.get("k") != "extrude" or b.get("plane") != "xz" or b["profile"].get("shape") != "rect":
            continue
        pr, (n0, n1) = b["profile"], b["range"]
        out.append((pr["u"][0], pr["u"][1], pr["v"][0], pr["v"][1], n0, n1))
        for c in b.get("cuts", ()):
            if c.get("plane") == "xz" and c["profile"].get("shape") == "rect":
                cp, (c0, c1) = c["profile"], c["range"]
                out.append((cp["u"][0], cp["u"][1], cp["v"][0], cp["v"][1], max(n0, c0), min(n1, c1)))
    return tuple(out)


def hosted_fam(g, w):
    """벽에 호스팅된 패밀리 인스턴스(창·패밀리 문)의 그림 정보 (u0, u1, z0, z1, offset, zbase, 평면 기호, 상자들). 정렬 튜플.
    u 는 벽 지역 길이 방향, z 는 절대 높이. 개구부가 없는 인스턴스(평가 오류 등)는 그리지 않는다."""
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    out = []
    for d in g.q(Q.wall_hosted, w):
        row = Q.el(g, d)
        if row is None or row["kind"] != "finst":
            continue
        spec = g.q(Q.ifc_spec, d)
        cut = spec.get("cut")
        if not cut:
            continue
        zb = elev + spec["elevation"]
        out.append((spec["offset"] + cut["x"][0], spec["offset"] + cut["x"][1], zb + cut["z"][0], zb + cut["z"][1],
                    spec["offset"], zb, _freeze_plan(spec["plan"]), _body_boxes(spec["body"])))
    return tuple(sorted(out, key=repr))


def _prism_poly(pr):
    """body 의 xy 압출 프로필 -> 지역 좌표 볼록 다각형(반시계). 지원: rect·circle·polygon."""
    p = pr["profile"]
    sh = p.get("shape")
    if sh == "rect":
        (u0, u1), (v0, v1) = p["u"], p["v"]
        return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    if sh == "circle":
        return _circle_pts(p["center"], p["r"])
    if sh == "polygon":
        return [tuple(q) for q in p["points"]]
    return None


def free_fam(g, e):
    """호스트 없이 레벨에 서는 패밀리 인스턴스(기둥 등)의 그림 정보 (x, y, 회전도, zb, zt, 평면 기호, 각기둥들). 그릴 형상이 없으면 None.
    각기둥은 (z0, z1, 지역 다각형). 높이는 body 의 xy 압출 범위에서 온다."""
    row = Q.el(g, e)
    if row is None or row["kind"] != "finst" or g.q(Q.finst_host, e):
        return None
    spec = g.q(Q.ifc_spec, e)
    if spec.get("error") or not spec.get("position"):
        return None
    _, elev = g.q(Q.level_info, g.q(Q.finst_level, e))
    zb0 = elev + spec["elevation"]
    prisms = []
    for b in spec["body"]:
        if b.get("k") != "extrude" or b.get("plane") != "xy":
            continue
        poly = _prism_poly(b)
        if poly is None:
            continue
        if _area2(poly) < 0:
            poly.reverse()
        prisms.append((zb0 + b["range"][0], zb0 + b["range"][1], tuple(poly)))
    if not prisms:
        return None
    return (spec["position"][0], spec["position"][1], spec.get("rotation", 0.0) or 0.0, min(p[0] for p in prisms), max(p[1] for p in prisms),
            _freeze_plan(spec["plan"]), tuple(prisms))


def _free_world(ff):
    """지역(u, v) -> 세계 좌표 변환 함수(회전 후 이동)."""
    sn, cs = _sincos_deg(ff[2])
    return lambda u, v: (ff[0] + u * cs - v * sn, ff[1] + u * sn + v * cs)


def all_free_fams(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "finst", b) if g.q(free_fam, e) is not None)
    return tuple(sorted(out))


def scope_fams(g, v):
    """뷰가 그리는 자립형 패밀리 인스턴스(기둥 등). 평면은 그 레벨에 놓인 것만, 단면·입면은 모델 전체."""
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    if cfg[0] == "plan":
        if cfg[1] is None:
            return ()
        return tuple(e for e in sorted(Q.rel_in(g, "on_level", cfg[1])) if g.q(free_fam, e) is not None)
    return g.q(all_free_fams)


def plan_fam_parts(g, v, e):
    """평면의 자립형 요소: (선분들, 채움 다각형들). 절단 높이가 요소 높이 범위에 들 때만 그린다(기둥 단면 + 기호)."""
    cfg = g.q(view_cfg, v)
    ff = g.q(free_fam, e)
    if ff is None or cfg[0] != "plan" or not (ff[3] - EPS <= cfg[2] <= ff[4] + EPS):
        return ((), ())
    fills: list = []
    segs = _plan_items(ff[5], _free_world(ff), fills)
    return (tuple(segs), tuple(fills))


def _fam_vpieces(g, v, e):
    """단면·입면에서 자립형 요소의 속이 찬 조각 ((z0, z1, 다각형(s,w)), ...)."""
    cfg = g.q(view_cfg, v)
    ff = g.q(free_fam, e)
    if ff is None or cfg[0] == "plan":
        return ()
    tw = _free_world(ff)
    out = []
    for z0, z1, poly in ff[6]:
        sw = [_sw(cfg, tw(*p)) for p in poly]
        if _area2(sw) < 0:
            sw.reverse()
        sw = _clip_w(sw, cfg[7], cfg[8])
        if len(sw) >= 3 and _area2(sw) > 2 * EPS:
            out.append((z0, z1, tuple(sw)))
    return tuple(out)


def vfam_pieces(g, v, e):
    return g.q(_fam_vpieces, v, e)


def vfam_bbox(g, v, e):
    ps = g.q(_fam_vpieces, v, e)
    if not ps:
        return None
    ss = [p[0] for _, _, poly in ps for p in poly]
    return (min(ss), min(z0 for z0, _, _ in ps), max(ss), max(z1 for _, z1, _ in ps))


def vfam_cut_rects(g, v, e):
    """단면: 절단면(w=near)과 만나는 기둥 조각의 (s, z) 사각형."""
    cfg = g.q(view_cfg, v)
    if cfg[0] == "plan" or not cfg[9]:
        return ()
    rects = []
    for z0, z1, poly in g.q(_fam_vpieces, v, e):
        ss = [p[0] for p in poly if abs(p[1] - cfg[7]) <= EPS]
        if len(ss) >= 2 and max(ss) - min(ss) > EPS:
            s0, s1 = min(ss), max(ss)
            rects.append(((s0, z0), (s1, z0), (s1, z1), (s0, z1)))
    return tuple(rects)


def vfam_edges(g, v, e):
    """자립형 요소의 날카로운 모서리: 각기둥의 수직 모서리와 위·아랫면 둘레(뷰 좌표 3D 선분)."""
    cfg = g.q(view_cfg, v)
    ff = g.q(free_fam, e)
    if ff is None or cfg[0] == "plan":
        return ()
    tw = _free_world(ff)
    raw = []
    for z0, z1, poly in ff[6]:
        pts = [_sw(cfg, tw(*p)) for p in poly]
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            raw.append(((a[0], a[1], z0), (a[0], a[1], z1)))
            for z in (z0, z1):
                raw.append(((a[0], a[1], z), (b[0], b[1], z)))
    out = []
    for e0, e1 in raw:
        if abs(e1[0] - e0[0]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        c = _clip_edge(e0, e1, cfg[7], cfg[8])
        if c is not None:
            out.append(c)
    return tuple(out)


def vfam_nbr_pieces(g, v, e):
    """가림 후보: 같은 뷰의 벽과 다른 기둥 중 경계 상자가 겹치는 것의 조각."""
    me = g.q(vfam_bbox, v, e)
    if me is None:
        return ()
    occ = []
    for w in g.q(scope_walls, v):
        bb = g.q(vbbox, v, w)
        if bb is not None and bb[0] <= me[2] + NBR_PAD and me[0] <= bb[2] + NBR_PAD and bb[1] <= me[3] + NBR_PAD and me[1] <= bb[3] + NBR_PAD:
            occ.extend(g.q(vpieces, v, w))
    for o in g.q(scope_fams, v):
        if o != e:
            occ.extend(g.q(_fam_vpieces, v, o))
    return tuple(occ)


def fam_lines(g, v, e):
    """자립형 요소 하나가 뷰에 기여하는 선분."""
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    if cfg[0] == "plan":
        return g.q(plan_fam_parts, v, e)[0]
    mine = list(g.q(_fam_vpieces, v, e))
    occ = mine + list(g.q(vfam_nbr_pieces, v, e))
    lines = []
    if occ:
        for e0, e1 in g.q(vfam_edges, v, e):
            for t0, t1 in visible_parts(e0, e1, occ):
                lines.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]),
                              e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    if cfg[9]:
        rects = g.q(vfam_cut_rects, v, e)
        if rects:
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(rects), [], e)]
    return tuple(lines)


def vfam_hosted_edges(g, v, w):
    """벽에 호스팅된 패밀리 인스턴스(창)의 상자 모서리(창틀 바깥·안쪽 틀·유리)를 뷰 좌표 3D 선분으로."""
    cfg = g.q(view_cfg, v)
    sol = g.q(wall_solid, w)
    if cfg is None or cfg[0] == "plan":
        return ()
    raw = []

    def pt(u, n, z):
        s_, w_ = _sw(cfg, _world(sol, u, n))
        return (s_, w_, z)

    for u0, u1, _, _, off, zb, _, boxes in g.q(hosted_fam, w):
        for bu0, bu1, bz0, bz1, n0, n1 in boxes:
            us, zs, ns = (off + bu0, off + bu1), (zb + bz0, zb + bz1), (n0, n1)
            for n in ns:                                           # 앞·뒤 면의 사각형 둘레
                for z in zs:
                    raw.append((pt(us[0], n, z), pt(us[1], n, z)))
                for u in us:
                    raw.append((pt(u, n, zs[0]), pt(u, n, zs[1])))
            for u in us:                                           # 두께 방향 모서리
                for z in zs:
                    raw.append((pt(u, ns[0], z), pt(u, ns[1], z)))
    out = []
    for e0, e1 in raw:
        if abs(e1[0] - e0[0]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        c = _clip_edge(e0, e1, cfg[7], cfg[8])
        if c is not None:
            out.append(c)
    return tuple(out)


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
        for op in g.q(wall_door_openings, w):
            if op[2] <= cfg[2] + EPS and cfg[2] <= op[3] + EPS:   # 절단면이 문 개구부 높이 안에 있을 때만(문 위를 자르면 벽이 이어져 문이 안 보인다)
                lines += _door_symbol(sol, op)
        for f in g.q(hosted_fam, w):                              # 창·패밀리 문: 패밀리가 평가한 평면 기호(창은 벽 두께를 가로지르는 평행선)
            if f[2] <= cfg[2] + EPS and cfg[2] <= f[3] + EPS:
                lines += _plan_items(f[6], lambda u, vv, _o=f[4]: _world(sol, _o + u, vv))
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
    for fe in g.q(scope_fams, v):                       # 기둥이 앞에 있으면 벽 선을 가린다
        occ.extend(g.q(_fam_vpieces, v, fe))
    lines = []
    if occ:
        for e0, e1 in g.q(vedges, v, w) + g.q(vfam_hosted_edges, v, w):
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
    for e in g.q(scope_fams, v):
        segs.extend(g.q(fam_lines, v, e))
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
    for e in g.q(scope_fams, v):
        if cfg[0] == "plan":
            out.extend(g.q(plan_fam_parts, v, e)[1])
        else:
            out.extend(g.q(vfam_cut_rects, v, e))
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


def views_depending_on(g, input_keys, vids, include_not_fresh=True):
    """입력 노드들에서 역의존을 따라가 도달하는 뷰 선분 노드의 뷰 id(살아 있는 뷰 vids 안에서만).
    보수적으로 더 포함하는 뷰 (놓치는 것보다 많이 알리는 쪽이 안전하다):
      - 아직 계산한 적 없는 뷰(캐시 없음): 읽은 입력을 모른다. 항상 포함.
      - 다시 계산하지 않아 낡은(stale) 뷰: 의존 목록이 마지막 계산 때 것이라, 그 뒤 새로 읽게 될 입력(새 벽 등)을 모른다.
        include_not_fresh=False 면 뺀다(시험이 키 따라가기만 따로 검사하려고 쓴다)."""
    live = set(vids)
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
    state = view_cache_state(g, vids)
    extra = {v for v, s in state.items() if s == "new" or (include_not_fresh and s != "fresh")}
    return sorted((hit | extra) & live)


# 뷰 하나에 딸린 질의 노드의 함수들 (첫 인자가 뷰 id). 뷰를 지우면 이 노드들을 그래프에서 치운다.
VIEW_SCOPED = frozenset({view_cfg, scope_walls, view_omitted, plan_pieces, plan_bbox, plan_nbrs, plan_wall_lines, vpieces, vbbox, vnbrs,
                         vcut_rects, vedges, vwall_lines, view_lines, view_fills,
                         scope_fams, plan_fam_parts, _fam_vpieces, vfam_bbox, vfam_cut_rects, vfam_edges, vfam_nbr_pieces, fam_lines,
                         vfam_hosted_edges})


def forget_views(g, vids) -> int:
    """지워진 뷰의 질의 노드(선분·조각·경계 상자 등)를 그래프에서 치운다. 치운 노드 수를 돌려준다.
    뷰 id 는 다른 요소와 겹치지 않으므로 (함수, 뷰 id, ...) 노드만 정확히 골라낸다."""
    vids = set(vids)
    if not vids:
        return 0
    return g.drop([k for k in g.nodes if k[0] in VIEW_SCOPED and k[1] in vids])
