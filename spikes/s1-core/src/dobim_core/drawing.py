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

from dobim_core import kinds as K
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
CORNER_FILL = Q.CORNER_EXTEND  # L 조인 모서리는 코어 형상(Q.wall_extent)이 닫는다: 도면과 IFC 가 같다
PRIORITY = {"cut": 3, "symbol": 2, "proj": 1}

ELEV_DIR = {"N": (0.0, -1.0), "E": (-1.0, 0.0), "S": (0.0, 1.0), "W": (1.0, 0.0)}   # 건물을 바라보는 방향
VIEW_TYPES = ("plan", "section", "elevation", "site")


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
    if cfg is None or cfg[0] == "site":
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
    배치도: ("site", None, 0, 0, 축척)  (모델 전체를 위에서 본 대지·건물 외곽 뷰)
    그 밖: (종류, ox, oy, 보는방향 dx, dy, 오른쪽 rx, ry, near, far, 절단 여부, 축척)"""
    row = Q.el(g, v)
    if row is None or row["kind"] != "view":
        return None
    p = row["params"]
    t, scale = p["type"], p["scale"]
    if t == "site":
        return ("site", None, 0.0, 0.0, scale)
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
    if cfg[0] == "site":
        return ()
    if cfg[0] == "plan":
        return () if cfg[1] is None else g.q(level_walls, cfg[1])
    return g.q(all_walls)


# ================================================================ 벽 형상 (뷰와 무관)
def wall_range(g, w):
    """벽 지역 x 범위: 코어의 벽 형상 범위(Q.wall_extent: 조인 자르기 + L 조인 모서리 연장)다. IFC 투영과 같은 값이라 도면과 IFC 형상이 같다."""
    return g.q(Q.wall_extent, w)


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
    if cfg is None or cfg[0] == "site":
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
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
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


# ================================================================ 슬래브 (kinds "slab": 레벨 + 경계 다각형 + 두께)
# 슬래브는 z 범위가 [밑면, 윗면]인 각기둥이다(경계가 오목해도 된다). 가림 판정용 조각은 경계를 삼각형으로 나눈 볼록 조각이다.
#   평면: 절단 높이가 슬래브 두께 안이면 경계 윤곽을 절단선 + 절단 채움으로(아니면 그리지 않는다: 평면은 벽이 윤곽을 준다)
#   단면: 절단면과 경계의 교차 구간 x 두께 = 절단 사각형(cut 선 + 채움), 벽 절단과 합집합 윤곽(이음매 선 제거). 절단면 뒤의 모서리는 투영선
#   입면: 두께 띠의 모서리(위·아랫면 둘레 + 수직 모서리). 앞에 선 벽·기둥·다른 슬래브가 가린다
def slab_info(g, s):
    """(밑면 z, 윗면 z, 경계 다각형(반시계 튜플)) 또는 None. 저작(authored) 슬래브만 그린다(분리된 슬래브의 의도는 낡은 값이다)."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "slab" or row["origin"] != "authored":
        return None
    zb, zt = g.q(Q.slab_zrange, s)
    poly = [tuple(p) for p in row["params"]["boundary"]]
    if _area2(poly) < 0:
        poly.reverse()
    return (zb, zt, tuple(poly))


def all_slabs(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "slab", b) if g.q(slab_info, e) is not None)
    return tuple(sorted(out))


def scope_slabs(g, v):
    """뷰가 그리는 슬래브. 평면은 그 레벨에 놓인 것만, 단면·입면은 모델 전체."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    if cfg[0] == "plan":
        if cfg[1] is None:
            return ()
        return tuple(e for e in sorted(Q.rel_in(g, "on_level", cfg[1])) if g.q(slab_info, e) is not None)
    return g.q(all_slabs)


def _in_tri(p, a, b, c):
    """점 p 가 삼각형 abc(반시계)의 안쪽이거나 경계 위인가."""
    for u, w in ((a, b), (b, c), (c, a)):
        if (w[0] - u[0]) * (p[1] - u[1]) - (w[1] - u[1]) * (p[0] - u[0]) < -1e-9:
            return False
    return True


def slab_tris(g, s):
    """경계 다각형(오목 가능, 반시계)을 볼록 삼각형들로 나눈다. 귀 자르기, 항상 앞 번호의 귀부터라 결정적이다."""
    info = g.q(slab_info, s)
    if info is None:
        return ()
    poly = info[2]
    idx = list(range(len(poly)))
    tris = []
    while len(idx) > 3:
        n = len(idx)
        pick = None
        for k in range(n):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % n]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 1e-9:
                continue                                       # 오목이거나 공선인 꼭짓점은 귀가 아니다
            if any(_in_tri(poly[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            pick = k
            tris.append((a, b, c))
            break
        if pick is None:                                       # 귀가 없다: 공선 꼭짓점을 하나 버리고(면적 불변), 그것도 없으면 부채꼴로 마무리
            for k in range(n):
                a, b, c = poly[idx[k - 1]], poly[idx[k]], poly[idx[(k + 1) % n]]
                if abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) <= 1e-9:
                    pick = k
                    break
            if pick is None:
                for k in range(1, n - 1):
                    tris.append((poly[idx[0]], poly[idx[k]], poly[idx[k + 1]]))
                return tuple(tris)
        del idx[pick]
    if len(idx) == 3:
        tris.append((poly[idx[0]], poly[idx[1]], poly[idx[2]]))
    return tuple(tris)


def plan_slab_parts(g, v, s):
    """평면의 슬래브: (선분들, 채움 다각형들). 절단 높이가 두께 범위에 들 때만 경계 윤곽을 절단선·절단 채움으로 그린다."""
    cfg = g.q(view_cfg, v)
    info = g.q(slab_info, s)
    if info is None or cfg is None or cfg[0] != "plan" or not (info[0] - EPS <= cfg[2] <= info[1] + EPS):
        return ((), ())
    poly = info[2]
    n = len(poly)
    segs = tuple(("cut", poly[i][0], poly[i][1], poly[(i + 1) % n][0], poly[(i + 1) % n][1]) for i in range(n))
    return (segs, (poly,))


def _slab_vpieces(g, v, s):
    """단면·입면에서 슬래브의 속이 찬 볼록 조각 ((z0, z1, 다각형(s,w)), ...)."""
    cfg = g.q(view_cfg, v)
    info = g.q(slab_info, s)
    if info is None or cfg is None or cfg[0] == "plan":
        return ()
    out = []
    for tri in g.q(slab_tris, s):
        sw = [_sw(cfg, p) for p in tri]
        if _area2(sw) < 0:
            sw.reverse()
        sw = _clip_w(sw, cfg[7], cfg[8])
        if len(sw) >= 3 and _area2(sw) > 2 * EPS:
            out.append((info[0], info[1], tuple(sw)))
    return tuple(out)


def slab_vbbox(g, v, s):
    ps = g.q(_slab_vpieces, v, s)
    if not ps:
        return None
    ss = [p[0] for _, _, poly in ps for p in poly]
    return (min(ss), min(z0 for z0, _, _ in ps), max(ss), max(z1 for _, z1, _ in ps))


def slab_cut_rects(g, v, s):
    """단면: 절단면(w=near)과 슬래브 경계의 교차 구간마다 (s, z) 사각형(오목 경계도 짝수-홀수 규칙으로 구간이 여럿일 수 있다)."""
    cfg = g.q(view_cfg, v)
    info = g.q(slab_info, s)
    if info is None or cfg is None or cfg[0] == "plan" or not cfg[9]:
        return ()
    near = cfg[7]
    pts = [_sw(cfg, p) for p in info[2]]
    xs = []
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if (a[1] <= near) != (b[1] <= near):
            xs.append(a[0] + (near - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
    xs.sort()
    rects = []
    for i in range(0, len(xs) - 1, 2):
        s0, s1 = xs[i], xs[i + 1]
        if s1 - s0 > EPS:
            rects.append(((s0, info[0]), (s1, info[0]), (s1, info[1]), (s0, info[1])))
    return tuple(rects)


def slab_vedges(g, v, s):
    """슬래브의 모서리: 경계 둘레의 위·아랫면 수평선과 꼭짓점의 수직 모서리(뷰 좌표 3D 선분 (s,w,z))."""
    cfg = g.q(view_cfg, v)
    info = g.q(slab_info, s)
    if info is None or cfg is None or cfg[0] == "plan":
        return ()
    zb, zt, poly = info
    pts = [_sw(cfg, p) for p in poly]
    n = len(pts)
    raw = []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        raw.append(((a[0], a[1], zb), (a[0], a[1], zt)))
        for z in (zb, zt):
            raw.append(((a[0], a[1], z), (b[0], b[1], z)))
    out = []
    for e0, e1 in raw:
        if abs(e1[0] - e0[0]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        c = _clip_edge(e0, e1, cfg[7], cfg[8])
        if c is not None:
            out.append(c)
    return tuple(out)


def _bb_hit(a, b):
    return a[0] <= b[2] + NBR_PAD and b[0] <= a[2] + NBR_PAD and a[1] <= b[3] + NBR_PAD and b[1] <= a[3] + NBR_PAD


def slab_nbr_walls(g, v, s):
    """슬래브와 경계 상자가 겹치는 같은 뷰의 벽(가림 후보·절단 윤곽 합집합 상대)."""
    me = g.q(slab_vbbox, v, s)
    if me is None:
        return ()
    return tuple(w for w in g.q(scope_walls, v) if (bb := g.q(vbbox, v, w)) is not None and _bb_hit(me, bb))


def slab_lines(g, v, s):
    """슬래브 하나가 뷰에 기여하는 선분."""
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    if cfg[0] == "plan":
        return g.q(plan_slab_parts, v, s)[0]
    mine = list(g.q(_slab_vpieces, v, s))
    nb = g.q(slab_nbr_walls, v, s)
    occ = mine[:]
    for w in nb:
        occ.extend(g.q(vpieces, v, w))
    for o in g.q(scope_slabs, v):
        if o != s:
            occ.extend(g.q(_slab_vpieces, v, o))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    lines = []
    if occ:
        for e0, e1 in g.q(slab_vedges, v, s):
            for t0, t1 in visible_parts(e0, e1, occ):
                lines.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]),
                              e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    if cfg[9]:
        rects = g.q(slab_cut_rects, v, s)
        if rects:
            others = [(w, q) for w in nb for q in g.q(vcut_rects, v, w)]
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(rects), others, s)]
    return tuple(lines)


# ================================================================ 계단 (kinds "stair": 직선 계단, 설계: elements-design.md 6절)
# 계단은 디딤판마다 속이 찬 볼록 각기둥(아래 끝 ~ 그 디딤판 윗면)의 모임이다. 디딤판은 챌판 수 - 1 개(마지막 챌판 위는 위층 바닥).
#   평면: 윤곽·디딤판 선(투영선), 걸음선 + 화살촉(symbol), 절단 높이가 계단 높이 안이면 그 위치의 절단선(cut)과 그 위쪽 생략
#   단면: 절단면과 만나는 디딤 조각마다 (s, z) 사각형(cut 선 + 채움), 벽·슬래브와 합집합 윤곽
#   입면: 옆모습 윤곽(양 옆면)과 디딤판 앞 모서리를 투영선으로
STAIR_ARROW = 0.3   # 화살촉 길이 = 폭 x STAIR_ARROW (mm, 상한 400)


def stair_info(g, s):
    """(아래 z, 위 z, 시작 x, y, 걸음 방향 ux, uy, 걸음선 길이, 폭, 챌판 수, 디딤판 깊이, 챌판 높이) 또는 None. 저작 계단만 그린다."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "stair" or row["origin"] != "authored":
        return None
    zb, zt = g.q(Q.stair_zrange, s)
    p = row["params"]
    x1, y1, x2, y2 = p["run"]
    dx, dy = x2 - x1, y2 - y1
    length = sqrt(dx * dx + dy * dy)
    n = p["risers"]
    return (zb, zt, x1, y1, dx / length, dy / length, length, p["width"], n, length / (n - 1), (zt - zb) / n)


def all_stairs(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "stair", b) if g.q(stair_info, e) is not None)
    return tuple(sorted(out))


def scope_stairs(g, v):
    """뷰가 그리는 계단. 평면은 그 레벨에 놓인 것만, 단면·입면은 모델 전체."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    if cfg[0] == "plan":
        if cfg[1] is None:
            return ()
        return tuple(e for e in sorted(Q.rel_in(g, "on_level", cfg[1])) if g.q(stair_info, e) is not None)
    return g.q(all_stairs)


def _st_pt(info, t, m):
    """걸음선 위 t(mm), 왼쪽으로 m(mm) 만큼 떨어진 점의 세계 좌표."""
    return (info[2] + info[4] * t - info[5] * m, info[3] + info[5] * t + info[4] * m)


def plan_stair_parts(g, v, s):
    """평면의 계단: (선분들, 채움들). 채움은 없다."""
    cfg = g.q(view_cfg, v)
    info = g.q(stair_info, s)
    if info is None or cfg is None or cfg[0] != "plan" or cfg[2] < info[0] - EPS:
        return ((), ())
    zb, zt, _, _, _, _, length, width, n, td, rise = info
    h = width / 2.0
    broken = cfg[2] < zt - EPS
    k_b = min(_floor((cfg[2] - zb) / rise), n - 1) if broken else n - 1
    tmax = k_b * td if broken else length
    segs = []

    def seg(cls, a, b):
        segs.append((cls, a[0], a[1], b[0], b[1]))

    seg("proj", _st_pt(info, 0.0, h), _st_pt(info, 0.0, -h))                  # 첫 챌판 쪽 끝선
    seg("proj", _st_pt(info, 0.0, h), _st_pt(info, tmax, h))                  # 양 옆선
    seg("proj", _st_pt(info, 0.0, -h), _st_pt(info, tmax, -h))
    if not broken:
        seg("proj", _st_pt(info, length, h), _st_pt(info, length, -h))
    for k in range(1, n - 1):                                                 # 디딤판 사이 선(절단 위치 아래만)
        if k * td < tmax - EPS:
            seg("proj", _st_pt(info, k * td, h), _st_pt(info, k * td, -h))
    # 걸음선과 오르는 방향 화살촉
    t_start = min(td * 0.5, tmax)
    t_end = tmax - min(td * 0.3, tmax * 0.1) if broken else length - min(td * 0.5, length * 0.1)
    if t_end - t_start > EPS:
        seg("symbol", _st_pt(info, t_start, 0.0), _st_pt(info, t_end, 0.0))
        hl = min(width * STAIR_ARROW, 400.0, t_end - t_start)
        for sgn in (1.0, -1.0):
            seg("symbol", _st_pt(info, t_end, 0.0), _st_pt(info, t_end - hl, sgn * hl * 0.4))
    if broken:                                                                # 절단선: 계단을 가로지르는 대각선
        e = min(td * 0.5, 150.0)
        seg("cut", _st_pt(info, tmax - e, h), _st_pt(info, tmax + e, -h))
    return (tuple(segs), ())


def _stair_vpieces(g, v, s):
    """단면·입면에서 계단의 속이 찬 볼록 조각 ((z0, z1, 다각형(s,w)), ...): 디딤판마다 하나."""
    cfg = g.q(view_cfg, v)
    info = g.q(stair_info, s)
    if info is None or cfg is None or cfg[0] == "plan":
        return ()
    zb, _, _, _, _, _, _, width, n, td, rise = info
    h = width / 2.0
    out = []
    for k in range(n - 1):
        quad = [_sw(cfg, _st_pt(info, t, m)) for t, m in ((k * td, -h), ((k + 1) * td, -h), ((k + 1) * td, h), (k * td, h))]
        if _area2(quad) < 0:
            quad.reverse()
        quad = _clip_w(quad, cfg[7], cfg[8])
        if len(quad) >= 3 and _area2(quad) > 2 * EPS:
            out.append((zb, zb + (k + 1) * rise, tuple(quad)))
    return tuple(out)


def stair_vbbox(g, v, s):
    ps = g.q(_stair_vpieces, v, s)
    if not ps:
        return None
    ss = [p[0] for _, _, poly in ps for p in poly]
    return (min(ss), min(z0 for z0, _, _ in ps), max(ss), max(z1 for _, z1, _ in ps))


def stair_cut_rects(g, v, s):
    """단면: 절단면(w=near)과 각 디딤 조각의 교차 구간 x (바닥 ~ 디딤판 윗면) 사각형."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "plan" or not cfg[9]:
        return ()
    near = cfg[7]
    rects = []
    for z0, z1, poly in g.q(_stair_vpieces, v, s):
        xs = []
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            if abs(a[1] - near) <= EPS:
                xs.append(a[0])
            if (a[1] < near - EPS) != (b[1] < near - EPS) and abs(b[1] - a[1]) > EPS:
                xs.append(a[0] + (near - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
        if len(xs) >= 2 and max(xs) - min(xs) > EPS:
            s0, s1 = min(xs), max(xs)
            rects.append(((s0, z0), (s1, z0), (s1, z1), (s0, z1)))
    return tuple(rects)


def stair_vedges(g, v, s):
    """계단의 모서리(뷰 좌표 3D 선분 (s,w,z)): 양 옆면의 톱니 윤곽과 디딤판 앞 모서리(폭 방향)."""
    cfg = g.q(view_cfg, v)
    info = g.q(stair_info, s)
    if info is None or cfg is None or cfg[0] == "plan":
        return ()
    zb, _, _, _, _, _, length, width, n, td, rise = info
    h = width / 2.0
    prof = [(0.0, zb)]
    for k in range(n):
        prof.append((k * td, zb + (k + 1) * rise))
        if k < n - 1:
            prof.append(((k + 1) * td, zb + (k + 1) * rise))
    prof.append((length, zb))
    raw = []
    for m in (-h, h):
        pts = [(_sw(cfg, _st_pt(info, t, m)), z) for t, z in prof]
        for i in range(len(pts)):
            (a, za), (b, zb2) = pts[i], pts[(i + 1) % len(pts)]
            raw.append(((a[0], a[1], za), (b[0], b[1], zb2)))
    for t, z in prof[1:-1]:                                                   # 디딤판·챌판 모서리가 만나는 꼭짓점마다 폭 방향 선
        a, b = _sw(cfg, _st_pt(info, t, -h)), _sw(cfg, _st_pt(info, t, h))
        raw.append(((a[0], a[1], z), (b[0], b[1], z)))
    out = []
    for e0, e1 in raw:
        if abs(e1[0] - e0[0]) < EPS and abs(e1[1] - e0[1]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        c = _clip_edge(e0, e1, cfg[7], cfg[8])
        if c is not None:
            out.append(c)
    return tuple(out)


def stair_nbr_walls(g, v, s):
    """계단과 경계 상자가 겹치는 같은 뷰의 벽(가림 후보·절단 윤곽 합집합 상대)."""
    me = g.q(stair_vbbox, v, s)
    if me is None:
        return ()
    return tuple(w for w in g.q(scope_walls, v) if (bb := g.q(vbbox, v, w)) is not None and _bb_hit(me, bb))


def stair_lines(g, v, s):
    """계단 하나가 뷰에 기여하는 선분."""
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    if cfg[0] == "plan":
        return g.q(plan_stair_parts, v, s)[0]
    mine = list(g.q(_stair_vpieces, v, s))
    nb = g.q(stair_nbr_walls, v, s)
    occ = mine[:]
    for w in nb:
        occ.extend(g.q(vpieces, v, w))
    for o in g.q(scope_stairs, v):
        if o != s:
            occ.extend(g.q(_stair_vpieces, v, o))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    lines = []
    if occ:
        for e0, e1 in g.q(stair_vedges, v, s):
            for t0, t1 in visible_parts(e0, e1, occ):
                lines.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]),
                              e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    if cfg[9]:
        rects = g.q(stair_cut_rects, v, s)
        if rects:
            others = [(w, q) for w in nb for q in g.q(vcut_rects, v, w)]
            others += [(sl, q) for sl in g.q(scope_slabs, v) for q in g.q(slab_cut_rects, v, sl)]
            others += [(o, q) for o in g.q(scope_stairs, v) if o != s for q in g.q(stair_cut_rects, v, o)]
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(rects), others, s)]
    return tuple(lines)


# ================================================================ 공간 (kinds "space") 과 문자 층
# 공간은 선·채움을 만들지 않는다(윤곽은 벽이 준다). 평면에 실명과 면적 문자(labels)만 놓는다.
# 문자 항목: (x, y, 문자, 글자 높이 mm(용지 위), 앵커 "middle"). 좌표는 선분과 같은 뷰 좌표(평면 x,y / 단면·입면 s,z).
LABEL_NAME_H, LABEL_SUB_H = 2.5, 2.0


def space_info(g, s):
    """(경계 다각형(반시계 튜플), 실명, 분류) 또는 None. 저작 공간만 그린다."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "space" or row["origin"] != "authored":
        return None
    p = row["params"]
    poly = [tuple(q) for q in p["boundary"]]
    if _area2(poly) < 0:
        poly.reverse()
    return (tuple(poly), p["name"], p["category"])


def all_spaces(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "space", b) if g.q(space_info, e) is not None)
    return tuple(sorted(out))


def scope_spaces(g, v):
    """평면은 그 레벨에 놓인 공간만, 단면·입면은 없다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "plan" or cfg[1] is None:
        return ()
    return tuple(e for e in sorted(Q.rel_in(g, "on_level", cfg[1])) if g.q(space_info, e) is not None)


def polygon_centroid(poly):
    """다각형의 면적 무게중심. 면적이 0 에 가까우면 꼭짓점 평균."""
    a2 = _area2(poly)
    n = len(poly)
    if abs(a2) < 1e-6:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    cx = cy = 0.0
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        c = p[0] * q[1] - q[0] * p[1]
        cx += (p[0] + q[0]) * c
        cy += (p[1] + q[1]) * c
    return (cx / (3.0 * a2), cy / (3.0 * a2))


def area_text(poly) -> str:
    """면적 문자: ㎡, 소수 둘째 자리."""
    return f"{abs(_area2(poly)) / 2.0 / 1.0e6:.2f}㎡"


def plan_space_labels(g, v, s):
    """평면의 공간 하나: 실명(위)과 면적(아래) 문자."""
    cfg = g.q(view_cfg, v)
    info = g.q(space_info, s)
    if info is None or cfg is None or cfg[0] != "plan":
        return ()
    cx, cy = polygon_centroid(info[0])
    sc = cfg[4]
    return ((round(cx, 3), round(cy + 0.9 * sc, 3), info[1], LABEL_NAME_H, "middle"),
            (round(cx, 3), round(cy - 2.0 * sc, 3), area_text(info[0]), LABEL_SUB_H, "middle"))


def view_labels(g, v):
    """뷰의 문자 층: ((x, y, 문자, 높이 mm, 앵커), ...). 선분과 달리 병합하지 않고 만든 순서(공간 id 순 등)를 유지한다."""
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    if cfg[0] == "site":
        return g.q(site_labels, v)
    out = []
    for s in g.q(scope_spaces, v):
        out.extend(g.q(plan_space_labels, v, s))
    for w in g.q(scope_walls, v):
        if cfg[0] == "plan":
            out.extend(g.q(plan_fire_labels, v, w))
        elif cfg[0] == "elev":
            out.extend(g.q(elev_finish, v, w)[1])
    return tuple(out)


# ================================================================ 방화(#3)·외부 마감(#6): 기존 속성만 읽는다 (새 종류 없음, 설계 8절)
#   평면: Pset_WallCommon.FireRating 이 있는 벽 -> 중심선 파선(symbol) + "FW 등급" 문자, Pset_DoorCommon.FireRating 이 있는 문 -> "FD 등급" 문자
#   입면: Pset_WallCommon.IsExternal 이 참이고 재료가 있는 벽 -> 앞면 보이는 부분에 45도 해치(hatch) + 재료 이름 문자
FIRE_DASH, FIRE_GAP = 4.0, 1.2     # 방화벽 중심선 파선의 마디·간격(용지 mm)
HATCH_STEP = 3.5                   # 해치 가로 간격(용지 mm)
LABEL_FIRE_H = 2.0


def _prop(g, e, pset, prop):
    return Q.psets(g, e).get(pset, {}).get(prop)


def fire_rating(g, e, pset):
    """방화 등급 문자(앞 16자). 값이 없거나 문자열이 아니거나 비어 있으면 None."""
    v = _prop(g, e, pset, "FireRating")
    return v.strip()[:16] if isinstance(v, str) and v.strip() else None


def _truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "yes", "y", "1", "예", "외부"))


def plan_fire_lines(g, v, w):
    """방화벽(FireRating 이 있는 벽)의 중심선 파선. 마디 길이는 용지 위 일정하다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "plan" or fire_rating(g, w, "Pset_WallCommon") is None:
        return ()
    x, y, c, s, _ = g.q(Q.wall_frame, w)
    start, end, _st = g.q(Q.wall_trim, w)
    dash, gap = FIRE_DASH * cfg[4], FIRE_GAP * cfg[4]
    out = []
    t = start
    while t < end - SEG_MIN:
        t1 = min(t + dash, end)
        out.append(("symbol", x + c * t, y + s * t, x + c * t1, y + s * t1))
        t = t1 + gap
    return tuple(out)


def plan_fire_labels(g, v, w):
    """방화벽·방화문 문자: 벽은 왼쪽, 문은 오른쪽 바깥에 놓는다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "plan":
        return ()
    x, y, c, s, _ = g.q(Q.wall_frame, w)
    half = Q.el(g, w)["params"]["thickness"] / 2.0 + 1.6 * cfg[4]
    out = []
    fr = fire_rating(g, w, "Pset_WallCommon")
    if fr is not None:
        start, end, _st = g.q(Q.wall_trim, w)
        m = (start + end) / 2.0
        out.append((round(x + c * m - s * half, 3), round(y + s * m + c * half, 3), "FW " + fr, LABEL_FIRE_H, "middle"))
    for d in g.q(Q.wall_hosted, w):
        row = Q.el(g, d)
        fd = fire_rating(g, d, "Pset_DoorCommon") if row is not None and row["kind"] == "door" else None
        if fd is not None:
            wx, wy, _z = g.q(Q.door_world, d)
            out.append((round(wx + s * half, 3), round(wy - c * half, 3), "FD " + fd, LABEL_FIRE_H, "middle"))
    return tuple(out)


def _text_w(text, size):
    """문자 층 글의 대략 폭(용지 mm). 한글 등 넓은 글자는 글자 높이만큼, 그 밖은 0.58배(drawing_export.label_width 와 같은 규칙)."""
    return sum(size * (1.0 if ord(c) >= 0x2E80 else 0.58) for c in text)


def _visible_slanted(e0, e1, occluders):
    """z 가 변하는(대각선) 선분의 보이는 매개변수 구간들. 가림 조각의 z 범위 안에 드는 구간만 가려진다(수평·수직은 visible_parts)."""
    dz = e1[2] - e0[2]
    if abs(dz) < 1e-9:
        return visible_parts(e0, e1, occluders)
    hidden = []
    for z0, z1, poly in occluders:
        ta, tb = (z0 + EPS - e0[2]) / dz, (z1 - EPS - e0[2]) / dz
        if ta > tb:
            ta, tb = tb, ta
        ta, tb = max(ta, 0.0), min(tb, 1.0)
        if tb - ta <= 1e-9:
            continue
        for a, b in _hidden_h(poly, e0, e1):
            lo, hi = max(a, ta), min(b, tb)
            if hi - lo > 1e-9:
                hidden.append((lo, hi))
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


def elev_finish(g, v, w):
    """입면의 외부 마감: (해치 선분들, 재료 문자들). IsExternal 이 참이고 재료 이름이 있는 벽만, 앞면의 보이는 부분에 45도 해치."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "elev" or not _truthy(_prop(g, w, "Pset_WallCommon", "IsExternal")):
        return ((), ())
    mat = Q.material(g, w)
    pieces = g.q(vpieces, v, w)
    if not isinstance(mat, str) or not mat.strip() or not pieces:
        return ((), ())
    occ = _wall_occluders(g, v, w)
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    step = HATCH_STEP * cfg[10]
    text = mat.strip()[:24]
    need = _text_w(text, LABEL_FIRE_H) * cfg[10]               # 글이 들어갈 만큼 넓은 조각에만 재료 문자를 놓는다(벽 끝면 같은 좁은 조각은 해치만)
    lines = []
    best = None
    for z0, z1, poly in pieces:
        s0, s1 = min(p[0] for p in poly), max(p[0] for p in poly)
        wf = min(p[1] for p in poly)                           # 앞면 깊이
        if s1 - s0 <= EPS or z1 - z0 <= EPS:
            continue
        mine = []
        k = _floor((s0 - z1) / step) + 1                       # 세계 격자에 맞춘 해치선 번호(c = s - z): 이웃 조각과 선이 이어진다
        while k * step < s1 - z0:
            c = k * step
            sa, sb = max(s0, c + z0), min(s1, c + z1)
            if sb - sa > SEG_MIN:
                e0, e1 = (sa, wf, sa - c), (sb, wf, sb - c)
                for t0, t1 in _visible_slanted(e0, e1, occ):
                    if (t1 - t0) * (sb - sa) * 1.4142 < SEG_MIN:   # 가림 경계에 남은 부스러기 선분은 버린다(병합 단계도 같은 길이로 거른다)
                        continue
                    mine.append(("hatch", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]),
                                 e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
            k += 1
        lines.extend(mine)
        if mine and s1 - s0 >= need and (best is None or len(mine) > best[0]):   # 보이는 해치가 가장 많은 넓은 조각의 보이는 부분 가운데에 재료 문자를 놓는다
            xs = [c for ln in mine for c in (ln[1], ln[3])]
            zs = [c for ln in mine for c in (ln[2], ln[4])]
            best = (len(mine), (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0)
    labels = ()
    if best is not None:                                        # 앞의 벽에 가려 해치가 하나도 안 보이는 벽에는 문자도 없다
        labels = ((round(best[1], 3), round(best[2], 3), text, LABEL_FIRE_H, "middle"),)
    return (tuple(lines), labels)


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


# ================================================================ 큰 축척(1:50 이하) 표현 수준 (SC-09, B-17)
# 시트·내보내기가 detail 을 켤 때만 더하는 가는 선들이다(view_lines 는 그대로라 1:100 기본 도면과 켜지 않은 출력은 바이트가 같다).
# 모델에 마감 층 구성이 없으므로 규약값을 쓴다: 마감 두께 FINISH_T, 문짝 두께 LEAF_T. 재료가 지정된 벽에만 마감선을 긋는다.
DETAIL_SCALE = 50      # 뷰 축척이 1:이 값 이하(1:50, 1:20 ...)일 때 상세 표현
FINISH_T = 20.0        # 벽 마감 두께 규약(mm): 벽 양면에서 이만큼 안쪽에 마감선
LEAF_T = 40.0          # 문짝 두께 규약(mm)
DETAIL_MIN_T = 100.0   # 이보다 얇은 벽은 마감선을 긋지 않는다


def _cut_intervals(sol, cutz):
    """절단 높이에서 벽이 속이 찬 구간들 ((a, b), ...)."""
    if sol[7] - EPS <= cutz <= sol[8] + EPS:
        for z0, z1, ivs in sol[10]:
            if z0 - EPS <= cutz <= z1 + EPS:
                return tuple(ivs)
    return ()


def _door_leaf_map(g, w):
    """벽의 문 개구부 (u0, u1, z0, z1) -> 문 속성의 문짝 두께(leaf). 입력이 없는 문은 넣지 않는다(규약값을 쓴다)."""
    start, end, _ = g.q(Q.wall_trim, w)
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    zb, zt = elev, elev + Q.el(g, w)["params"]["height"]
    out = {}
    for d in g.q(Q.wall_hosted, w):
        row = Q.el(g, d)
        if row["kind"] != "door" or "leaf" not in row["params"]:
            continue
        box = g.q(hosted_opening, d, elev)
        if box is None:
            continue
        u0, u1, z0, z1 = max(start, box[0]), min(end, box[1]), max(zb, box[2]), min(zt, box[3])
        if u1 - u0 > EPS and z1 - z0 > EPS:
            out[(u0, u1, z0, z1)] = row["params"]["leaf"]
    return out


def plan_detail_lines(g, v, w):
    """평면 벽 하나의 상세 선: 재료가 지정된 벽의 양면 마감선(가는 hatch 선, 이웃 벽 절단 조각 안쪽은 지운다)과, 문의 문짝 두께·문턱선.
    큰 축척(DETAIL_SCALE 이하)의 평면에서만 낸다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "plan" or cfg[1] is None or cfg[4] > DETAIL_SCALE:
        return ()
    sol = g.q(wall_solid, w)
    ivs = _cut_intervals(sol, cfg[2])
    if not ivs:
        return ()
    out = []
    h = sol[4]
    layers = Q.el(g, w)["params"].get("layers")
    if layers:   # 층 구성 입력이 있으면 층 경계마다 선(첫 층의 바깥 면 = 지역 -v 면, 마지막 층 쪽 = +v 면)
        offs = [-h + c for c in K.layer_bounds(layers)[1:-1]]
    elif Q.material(g, w) is not None and 2.0 * h >= DETAIL_MIN_T:   # 입력이 없으면 규약: 재료가 있는 벽은 양면에서 FINISH_T 안쪽
        offs = [-(h - FINISH_T), h - FINISH_T]
    else:
        offs = []
    if offs:
        others = [q for o in g.q(plan_nbrs, v, w) for q in g.q(plan_pieces, v, o)[0]]
        ux, uy = sol[2], sol[3]
        for a, b in ivs:
            for off in offs:
                p0 = _world(sol, a, off)
                cuts = []
                for q in others:
                    iv = _inside_interval(p0, ux, uy, b - a, q)
                    if iv is not None:
                        cuts.append(iv)
                for q0, q1 in _keep(p0, ux, uy, b - a, cuts):
                    out.append(("hatch", q0[0], q0[1], q1[0], q1[1]))
    leaf_of = g.q(_door_leaf_map, w)
    for op in g.q(wall_door_openings, w):
        if op[2] <= cfg[2] + EPS and cfg[2] <= op[3] + EPS:
            u0, u1 = op[0], op[1]
            width = u1 - u0
            leaf = leaf_of.get(op, LEAF_T)   # 문 속성(leaf)이 있으면 그 값, 없으면 규약값
            for pa, pb in (((u0 + leaf, h), (u0 + leaf, h + width)), ((u0, h + width), (u0 + leaf, h + width))):   # 문짝 두께(경첩 쪽 문짝 옆선과 끝 마구리)
                out.append(("symbol", *_world(sol, *pa), *_world(sol, *pb)))
            out.append(("hatch", *_world(sol, u0, 0.0), *_world(sol, u1, 0.0)))    # 문턱선(개구부를 가로지르는 가는 선)
    return tuple(out)


# ---- 1:50 단면·입면 상세(SC-09, B-17): 벽 층 경계선·층별 절단 해치, 슬래브 두께 층, 천장선, 창대(입면)
# 층 구성(layers) 입력이 있는 벽·슬래브, 천장 높이(ceiling_height)가 있는 공간, 창에만 더한다. 입력이 없으면 아무것도 더하지 않는다.
LAYER_HATCH_MIN = 1.5     # 층 해치는 용지 위 두께가 이 값(mm) 이상인 층에만 긋는다(마감층처럼 얇은 층은 경계선만)
LAYER_HATCH_STEP = 2.0    # 층 해치 간격(용지 mm)
SILL_DROP, SILL_EXT = 40.0, 50.0   # 창대: 창 아래로 내려오는 두께(mm)와 양쪽으로 더 나가는 길이(mm). 규약값


def _sec_chord(cfg, sol, a, b, vv):
    """벽 지역의 선(u = a..b, v = vv)과 절단면(w = near)이 만나는 s. 만나지 않으면 None."""
    s0, w0 = _sw(cfg, _world(sol, a, vv))
    s1, w1 = _sw(cfg, _world(sol, b, vv))
    if abs(w1 - w0) < 1e-9:
        return None
    t = (cfg[7] - w0) / (w1 - w0)
    if t < -1e-9 or t > 1.0 + 1e-9:
        return None
    return s0 + t * (s1 - s0)


def _hatch_pattern(material) -> int:
    """층 재료마다 정해지는 해치 무늬 번호(0: 45도, 1: 135도, 2: 격자). 같은 재료는 늘 같은 무늬다(결정적)."""
    return sum(ord(c) for c in material) % 3


def _hatch_rect(s0, s1, z0, z1, pattern, step, others):
    """절단 사각형 (s0..s1) x (z0..z1) 안의 해치 선분들 ("hatch" 등급). 선은 세계 격자(s -+ z = 간격의 정수배)에 맞춰 이웃 조각과 이어진다.
    others = 이 사각형 안에서 지울 다른 절단 사각형들 (rs0, rs1, rz0, rz1)(안쪽에 든 부분을 뺀다)."""
    out = []
    fams = (((1, 1),) if pattern == 0 else ((-1, 1),) if pattern == 1 else ((1, 1), (-1, 1)))   # (기울기 부호, 간격 배율)
    for sign, _ in fams:
        stp = step * (2.0 if pattern == 2 else 1.0)
        if sign > 0:   # z = s - c
            k = _floor((s0 - z1) / stp) + 1
            lim = s1 - z0
        else:          # z = c - s
            k = _floor((s0 + z0) / stp) + 1
            lim = s1 + z1
        while k * stp < lim:
            c = k * stp
            if sign > 0:
                sa, sb = max(s0, c + z0), min(s1, c + z1)
            else:
                sa, sb = max(s0, c - z1), min(s1, c - z0)
            k += 1
            if sb - sa <= SEG_MIN:
                continue
            cuts = []
            for rs0, rs1, rz0, rz1 in others:
                lo, hi = (c + rz0, c + rz1) if sign > 0 else (c - rz1, c - rz0)
                lo, hi = max(lo, rs0), min(hi, rs1)
                if hi - lo > EPS:
                    cuts.append((lo, hi))
            for a, b in _subtract([(sa, sb)], sorted(cuts)):
                if b - a > SEG_MIN:
                    z_a, z_b = (a - c, b - c) if sign > 0 else (c - a, c - b)
                    out.append(("hatch", a, z_a, b, z_b))
    return out


def _cut_boxes(rects):
    """절단 사각형 다각형들 -> (s0, s1, z0, z1) 상자들."""
    return [(min(p[0] for p in r), max(p[0] for p in r), min(p[1] for p in r), max(p[1] for p in r)) for r in rects]


def vwall_detail(g, v, w):
    """단면에서 층 구성(layers)이 있는 벽의 상세: 절단면의 층 경계선과 층별 절단 해치(가는 hatch 선). 큰 축척(DETAIL_SCALE 이하)의 단면만.
    이웃 벽의 절단 사각형 안쪽은 지운다(경계선·해치가 이웃 벽 위로 나가지 않게)."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "sec" or cfg[10] > DETAIL_SCALE:
        return ()
    layers = Q.el(g, w)["params"].get("layers")
    if not layers:
        return ()
    sol = g.q(wall_solid, w)
    h = sol[4]
    bounds = K.layer_bounds(layers)
    others = _cut_boxes([q for o in g.q(vnbrs, v, w) for q in g.q(vcut_rects, v, o)])
    step = LAYER_HATCH_STEP * cfg[10]
    out = []
    for z0, z1, ivs in sol[10]:
        for a, b in ivs:
            ss = [_sec_chord(cfg, sol, a, b, -h + c) for c in bounds]
            if any(x is None for x in ss):
                continue   # 절단면이 조각 끝을 스쳐 층 경계가 다 만나지 않는 조각은 그리지 않는다
            for x in ss[1:-1]:   # 층 경계선: 바깥 면 둘은 절단 윤곽이 이미 그린다
                zc = [(max(rz0, z0), min(rz1, z1)) for rs0, rs1, rz0, rz1 in others if rs0 + EPS < x < rs1 - EPS and rz1 > z0 and rz0 < z1]
                for q0, q1 in _subtract([[z0, z1]], sorted(zc)):
                    if q1 - q0 > SEG_MIN:
                        out.append(("hatch", x, q0, x, q1))
            for i, ly in enumerate(layers):
                lo, hi = sorted((ss[i], ss[i + 1]))
                if hi - lo >= LAYER_HATCH_MIN * cfg[10]:
                    out.extend(_hatch_rect(lo, hi, z0, z1, _hatch_pattern(ly["material"]), step, others))
    return tuple(out)


def slab_detail_lines(g, v, s):
    """단면에서 층 구성(layers)이 있는 슬래브의 상세: 절단 사각형 안의 두께 층 경계선과 층별 절단 해치. 벽 절단 사각형 안쪽은 지운다(벽이 우선)."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "sec" or cfg[10] > DETAIL_SCALE:
        return ()
    info = g.q(slab_info, s)
    layers = Q.el(g, s)["params"].get("layers") if info is not None else None
    if not layers:
        return ()
    others = _cut_boxes([q for wl in g.q(slab_nbr_walls, v, s) for q in g.q(vcut_rects, v, wl)])
    step = LAYER_HATCH_STEP * cfg[10]
    bounds = K.layer_bounds(layers)
    out = []
    for r in g.q(slab_cut_rects, v, s):
        s0, s1 = min(p[0] for p in r), max(p[0] for p in r)
        zt = max(p[1] for p in r)
        zs = [zt - c for c in bounds]   # 윗면에서 아래로
        for z in zs[1:-1]:
            cuts = [(max(rs0, s0), min(rs1, s1)) for rs0, rs1, rz0, rz1 in others if rz0 + EPS < z < rz1 - EPS]
            for q0, q1 in _subtract([[s0, s1]], sorted(cuts)):
                if q1 - q0 > SEG_MIN:
                    out.append(("hatch", q0, z, q1, z))
        for i, ly in enumerate(layers):
            if zs[i] - zs[i + 1] >= LAYER_HATCH_MIN * cfg[10]:
                out.extend(_hatch_rect(s0, s1, zs[i + 1], zs[i], _hatch_pattern(ly["material"]), step, others))
    return tuple(out)


def space_ceiling_lines(g, v, sp):
    """단면에서 천장 높이(ceiling_height)가 있는 공간의 천장선: 절단면이 공간 경계를 지나는 구간마다 레벨 + 천장 높이에 가는 보이는 선. 큰 축척의 단면만."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "sec" or cfg[10] > DETAIL_SCALE:
        return ()
    info = g.q(space_info, sp)
    ch = Q.el(g, sp)["params"].get("ceiling_height") if info is not None else None
    if ch is None:
        return ()
    z = g.q(Q.level_info, g.q(Q.space_level, sp))[1] + ch
    near = cfg[7]
    pts = [_sw(cfg, p) for p in info[0]]
    xs = []
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if (a[1] <= near) != (b[1] <= near):
            xs.append(a[0] + (near - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
    xs.sort()
    return tuple(("proj", xs[i], z, xs[i + 1], z) for i in range(0, len(xs) - 1, 2) if xs[i + 1] - xs[i] > SEG_MIN)


def _view_occluders(g, v, w):
    """벽 w 의 면 위 선을 가릴 수 있는 것들: 벽 조각(+이음매) + 기둥·슬래브·계단 조각(vwall_lines 와 같다)."""
    occ = _wall_occluders(g, v, w)
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    return occ


def vwin_detail(g, v, w):
    """입면에서 벽에 호스팅된 창의 창대(창 아래 밖으로 나온 선 3개): 앞 면(관찰자 쪽 벽 면)에 그리고 앞의 것에 가려지는 부분은 뺀다. 큰 축척(DETAIL_SCALE 이하)의 입면만.
    창 프레임(틀 폭·유리)은 패밀리 형상이라 기본 입면이 이미 그린다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "elev" or cfg[10] > DETAIL_SCALE:
        return ()
    sol = g.q(wall_solid, w)
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    h = sol[4]
    wins = []
    for d in g.q(Q.wall_hosted, w):
        row = Q.el(g, d)
        if row["kind"] == "finst" and str(row["family_id"]).startswith("window"):
            box = g.q(hosted_opening, d, elev)
            if box is not None:
                wins.append(box)
    if not wins:
        return ()
    vv = -h if _sw(cfg, _world(sol, 0.0, -h))[1] <= _sw(cfg, _world(sol, 0.0, h))[1] else h   # 관찰자에 가까운 벽 면

    def pt(u, z):
        s_, w_ = _sw(cfg, _world(sol, u, vv))
        return (s_, w_, z)

    occ = _view_occluders(g, v, w)
    out = []
    for u0, u1, z0, _z1 in sorted(wins):
        zl = z0 - SILL_DROP
        edges = [(pt(u0 - SILL_EXT, zl), pt(u1 + SILL_EXT, zl)), (pt(u0 - SILL_EXT, zl), pt(u0 - SILL_EXT, z0)), (pt(u1 + SILL_EXT, zl), pt(u1 + SILL_EXT, z0))]
        for e0, e1 in edges:
            for t0, t1 in visible_parts(e0, e1, occ):
                out.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]), e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    return tuple(out)


def view_detail_lines(g, v):
    """뷰의 큰 축척 상세 선(병합한 선분 묶음). 평면: 마감선·문짝 두께·문턱선. 단면: 벽·슬래브 층 경계선·층별 해치와 천장선. 입면: 창대. 배치도는 비어 있다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    segs = []
    if cfg[0] == "plan":
        if cfg[4] > DETAIL_SCALE:
            return ()
        for w in g.q(scope_walls, v):
            segs.extend(g.q(plan_detail_lines, v, w))
    elif cfg[0] == "sec":
        if cfg[10] > DETAIL_SCALE:
            return ()
        for w in g.q(scope_walls, v):
            segs.extend(g.q(vwall_detail, v, w))
        for sl in g.q(scope_slabs, v):
            segs.extend(g.q(slab_detail_lines, v, sl))
        for sp in g.q(all_spaces):
            segs.extend(g.q(space_ceiling_lines, v, sp))
    else:
        if cfg[10] > DETAIL_SCALE:
            return ()
        for w in g.q(scope_walls, v):
            segs.extend(g.q(vwin_detail, v, w))
    return merge_lines(segs)


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


def _bridges(g, v, w):
    """벽 하나의 위아래로 맞닿은 두 띠가 같은 u 구간에서 모두 속이 찬 곳의 가는 가림 조각(이음매 z 위로 +-2*EPS).
    문 개구부 때문에 벽의 띠가 높이로 나뉘면 띠 경계(문 윗선 z)가 어느 조각 안쪽도 아니라서, 앞벽의 속이 찬 곳 뒤의 선이 그 높이에서만 보이는 선으로
    남았다(가림 판정은 z 가 조각 안쪽일 때만 가린다). 두 띠가 모두 막힌 곳은 같은 벽이 막은 것이므로 이 조각으로 이음매를 메운다."""
    cfg = g.q(view_cfg, v)
    sol = g.q(wall_solid, w)
    out = []
    bands = sol[10]
    for (_a0, a1, aiv), (b0, _b1, biv) in zip(bands, bands[1:]):
        if abs(a1 - b0) > EPS:
            continue
        for p0, p1 in aiv:
            for q0, q1 in biv:
                lo, hi = max(p0, q0), min(p1, q1)
                if hi - lo <= EPS:
                    continue
                poly = _clip_w([_sw(cfg, p) for p in _quad(sol, lo, hi)], cfg[7], cfg[8])
                if len(poly) >= 3 and _area2(poly) > 2 * EPS:
                    out.append((a1 - 2 * EPS, a1 + 2 * EPS, tuple(poly)))
    return tuple(out)


def _wall_occluders(g, v, w):
    """벽 w 의 선을 가릴 수 있는 벽 조각들: w 와 이웃 벽의 속이 찬 조각 + 띠 이음매를 메우는 조각."""
    occ = list(g.q(vpieces, v, w))
    occ.extend(_bridges(g, v, w))
    for o in g.q(vnbrs, v, w):
        occ.extend(g.q(vpieces, v, o))
        occ.extend(_bridges(g, v, o))
    return occ


def vwall_lines(g, v, w):
    cfg = g.q(view_cfg, v)
    nb = g.q(vnbrs, v, w)
    occ = _wall_occluders(g, v, w)
    for fe in g.q(scope_fams, v):                       # 기둥이 앞에 있으면 벽 선을 가린다
        occ.extend(g.q(_fam_vpieces, v, fe))
    sls = g.q(scope_slabs, v)
    for sl in sls:                                      # 슬래브가 앞에 있으면 벽 선을 가린다
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):                     # 계단이 앞에 있으면 벽 선을 가린다
        occ.extend(g.q(_stair_vpieces, v, st))
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
            others += [(sl, q) for sl in sls for q in g.q(slab_cut_rects, v, sl)]   # 벽과 슬래브가 맞닿은 이음매 선을 지운다
            others += [(st, q) for st in g.q(scope_stairs, v) for q in g.q(stair_cut_rects, v, st)]   # 계단도 같다
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(mine), others, w)]
    return tuple(lines)


# ================================================================ 대지·배치도 (kinds "site", 뷰 종류 "site")
# 배치도 뷰는 모델 전체를 위에서 본 한 장이다: 대지 경계(굵은 1점쇄선), 건물 외곽(벽 윤곽·슬래브 경계의 합집합 바깥 선), 접한 도로(폭만큼 띠),
# 방위표(정북 각), 대지 경계와 건물 사이 이격 거리 치수(연관 치수와 같은 주석 항목), 대지면적·도로·표고·축척 문자.
SITE_DASH, SITE_GAP, SITE_DOT = 6.0, 1.5, 0.8   # 대지 경계 1점쇄선의 마디·간격·점(용지 mm)
NORTH_R = 7.0                                    # 방위표 원 반지름(용지 mm)
SITE_LABEL_H, SITE_SUB_H = 3.0, 2.5              # 문자 높이(용지 mm)
SITE_PAD = 0.5                                   # 바깥 쪽 판정용 탐침 거리(mm)


def site_info(g, s):
    """(경계 다각형(반시계 튜플), 이름, 정북 각, 도로 ((이름, 변 번호, 폭), ...), 표고) 또는 None. 저작 대지만 그린다."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "site" or row["origin"] != "authored":
        return None
    p = row["params"]
    poly = tuple(tuple(q) for q in p["boundary"])
    return (poly, p["name"], p["north_angle"], tuple((r["name"], r["edge"], r["width"]) for r in p["roads"]), p["elevation"])


def all_sites(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "site", b) if g.q(site_info, e) is not None)
    return tuple(sorted(out))


def building_polys(g):
    """건물이 차지하는 평면 다각형들(모든 레벨의 그리는 벽 윤곽 + 저작 슬래브 경계), 모두 반시계. 레벨은 구분하지 않는다(배치도는 건물 전체의 외곽)."""
    out = []
    for w in g.q(all_walls):
        x, y, c, s, _ = g.q(Q.wall_frame, w)
        out.append(tuple((x + u * c - v * s, y + u * s + v * c) for u, v in g.q(Q.wall_footprint, w)))
    for sl in g.q(all_slabs):
        out.append(g.q(slab_info, sl)[2])
    return tuple(out)


def _hit_ts(a, b, c, d):
    """선분 a->b 위에서 선분 c->d 와 만나는(겹치면 두 끝이 닿는) 매개변수 t 들."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    qx, qy = c[0] - a[0], c[1] - a[1]
    l2 = rx * rx + ry * ry
    den = rx * sy - ry * sx
    if abs(den) <= 1e-9 * sqrt(l2) * sqrt(sx * sx + sy * sy):    # 평행
        if abs(qx * ry - qy * rx) > EPS * sqrt(l2):
            return ()
        return ((qx * rx + qy * ry) / l2, ((d[0] - a[0]) * rx + (d[1] - a[1]) * ry) / l2)
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    return (t,) if -1e-9 <= u <= 1.0 + 1e-9 else ()


def _in_poly(p, poly) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            inside = not inside
    return inside


def _poly_bbox(poly):
    return (min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly))


ESCAPE_DIRS = ((1.0, 0.0137), (-1.0, 0.0211), (0.0173, 1.0), (0.0239, -1.0), (1.0, 1.0123), (-1.0, 1.0171), (1.0, -1.0097), (-1.0, -1.0149))   # 살짝 비튼 8방향(꼭짓점을 스치지 않게)


def _escapes(p, edges) -> bool:
    """점 p 에서 어느 한 방향으로 건물 변에 한 번도 부딪히지 않고 무한히 나갈 수 있는가(건물 밖에서 보이는 자리인가). 닫힌 방 안쪽이면 모든 방향이 막힌다."""
    for dx, dy in ESCAPE_DIRS:
        blocked = False
        for a, b in edges:
            if (dx > 0 and max(a[0], b[0]) < p[0]) or (dx < 0 and min(a[0], b[0]) > p[0]) or (dy > 0 and max(a[1], b[1]) < p[1]) or (dy < 0 and min(a[1], b[1]) > p[1]):
                continue
            ex, ey = b[0] - a[0], b[1] - a[1]
            den = dx * ey - dy * ex
            if abs(den) < 1e-12:
                continue
            qx, qy = a[0] - p[0], a[1] - p[1]
            t = (qx * ey - qy * ex) / den
            u = (qx * dy - qy * dx) / den
            if t > 1e-9 and 0.0 <= u <= 1.0:
                blocked = True
                break
        if not blocked:
            return True
    return False


def building_outline(g):
    """건물 다각형들의 합집합 바깥 선분: ((x0, y0, x1, y1), ...). 다른 다각형 안쪽에 들어가거나 이웃과 맞닿은 변은 뺀다.
    각 변을 다른 다각형 변과의 교점에서 쪼개고, 조각 중점에서 바깥으로 조금 나간 점이 다른 다각형 안이면 안쪽 이음매다.
    닫힌 방 안쪽을 향한 변(그 점에서 어느 방향으로도 건물 밖으로 나갈 수 없는 자리)은 외곽이 아니라서 뺀다: 외벽 안쪽 면·칸막이 윤곽이 배치도에 남지 않는다."""
    polys = g.q(building_polys)
    boxes = [_poly_bbox(p) for p in polys]
    edges = [(P[k], P[(k + 1) % len(P)]) for P in polys for k in range(len(P))]
    out = []
    for i, P in enumerate(polys):
        others = [polys[j] for j in range(len(polys)) if j != i and _bb_hit(boxes[i], boxes[j])]
        n = len(P)
        for k in range(n):
            a, b = P[k], P[(k + 1) % n]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = sqrt(dx * dx + dy * dy)
            if length < SEG_MIN:
                continue
            ts = [0.0, 1.0]
            for Qp in others:
                m = len(Qp)
                for q in range(m):
                    ts.extend(t for t in _hit_ts(a, b, Qp[q], Qp[(q + 1) % m]) if 0.0 < t < 1.0)
            ts = sorted(set(round(t, 9) for t in ts))
            nx, ny = dy / length, -dx / length      # 반시계 다각형의 바깥(진행 방향의 오른쪽)
            for t0, t1 in zip(ts, ts[1:]):
                if (t1 - t0) * length <= SEG_MIN:
                    continue
                tm = (t0 + t1) / 2.0
                probe = (a[0] + tm * dx + nx * SITE_PAD, a[1] + tm * dy + ny * SITE_PAD)
                if any(_in_poly(probe, Qp) for Qp in others):
                    continue
                if not _escapes(probe, edges):
                    continue
                out.append((a[0] + t0 * dx, a[1] + t0 * dy, a[0] + t1 * dx, a[1] + t1 * dy))
    return tuple(out)


def _dash_dot(x0, y0, x1, y1, scale):
    """선분을 1점쇄선 조각(긴 마디, 간격, 점, 간격)으로 쪼갠다. 마디 길이는 용지 위 일정하다. 모두 절단선 등급이다."""
    dx, dy = x1 - x0, y1 - y0
    length = sqrt(dx * dx + dy * dy)
    if length < SEG_MIN:
        return []
    ux, uy = dx / length, dy / length
    pat = ((SITE_DASH * scale, True), (SITE_GAP * scale, False), (SITE_DOT * scale, True), (SITE_GAP * scale, False))
    out, t, i = [], 0.0, 0
    while t < length - SEG_MIN:
        seg, on = pat[i % 4]
        t1 = min(t + seg, length)
        if on:
            out.append(("cut", x0 + ux * t, y0 + uy * t, x0 + ux * t1, y0 + uy * t1))
        t, i = t1, i + 1
    return out


def _edge_frame(poly, i):
    """경계 변 i: (시작, 끝, 길이, 바깥 단위 법선)."""
    a, b = poly[i], poly[(i + 1) % len(poly)]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = sqrt(dx * dx + dy * dy)
    return a, b, length, (dy / length, -dx / length)


def _road_box(poly, edge, width):
    a, b, _length, (nx, ny) = _edge_frame(poly, edge)
    xs = (a[0], b[0], a[0] + nx * width, b[0] + nx * width)
    ys = (a[1], b[1], a[1] + ny * width, b[1] + ny * width)
    return (min(xs), min(ys), max(xs), max(ys))


def _site_extent(g):
    """배치도에 놓이는 대지·도로의 경계 상자들과 방위 각. 대지가 없으면 ((), None)."""
    boxes, north = [], None
    for s in g.q(all_sites):
        poly, _name, north_, roads, _el = g.q(site_info, s)
        north = north_ if north is None else north
        boxes.append(_poly_bbox(poly))
        boxes.extend(_road_box(poly, edge, width) for _rn, edge, width in roads)
    return tuple(boxes), north


def _north_symbol(boxes, scale, north):
    """방위표: (선분들, 문자들, 경계 상자). 대지·도로 묶음의 오른쪽 위 바깥에 놓는다. 정북은 도면 위에서 시계방향으로 north 도 돈 방향이다."""
    r = NORTH_R * scale
    cx, cy = max(b[2] for b in boxes) + r + 6.0 * scale, max(b[3] for b in boxes) - r
    sn, cs = _sincos_deg(north)                  # 정북 방향 = (sin, cos)
    pts = _circle_pts((cx, cy), r)
    segs = [("symbol", pts[k][0], pts[k][1], pts[(k + 1) % len(pts)][0], pts[(k + 1) % len(pts)][1]) for k in range(len(pts))]
    px, py = cs, -sn                             # 정북에 수직인 오른쪽
    tip = (cx + sn * r * 0.92, cy + cs * r * 0.92)
    left = (cx - sn * r * 0.62 - px * r * 0.38, cy - cs * r * 0.62 - py * r * 0.38)
    right = (cx - sn * r * 0.62 + px * r * 0.38, cy - cs * r * 0.62 + py * r * 0.38)
    mid = (cx - sn * r * 0.28, cy - cs * r * 0.28)
    for p, q in ((tip, left), (left, mid), (mid, right), (right, tip)):
        segs.append(("cut", p[0], p[1], q[0], q[1]))
    lab = [(round(cx + sn * (r + 2.4 * scale), 3), round(cy + cs * (r + 2.4 * scale) - 1.4 * scale, 3), "N", 4.0, "middle")]
    return segs, lab, (cx - r, cy - r, cx + r, cy + r)


def site_setbacks(g, s):
    """대지 경계 변마다 건물까지의 이격: ((변 번호, 거리, 경계 위 발, 건물 꼭짓점), ...). 변에 수직으로 내린 발이 변 안에 드는 건물 꼭짓점 중 가장 가까운 것.
    거리는 경계 안쪽이 양수(건물이 경계를 넘으면 음수). 건물이 없으면 비어 있다."""
    info = g.q(site_info, s)
    polys = g.q(building_polys)
    if info is None or not polys:
        return ()
    poly = info[0]
    verts = sorted({(round(x, 3), round(y, 3)) for P in polys for x, y in P})
    out = []
    for i in range(len(poly)):
        a, b, length, (nx, ny) = _edge_frame(poly, i)
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        best = None
        for vx, vy in verts:
            t = (vx - a[0]) * ux + (vy - a[1]) * uy
            if t < -EPS or t > length + EPS:
                continue
            d = -((vx - a[0]) * nx + (vy - a[1]) * ny)      # 안쪽이 양수
            if best is None or d < best[0] - 1e-9:
                best = (d, (a[0] + t * ux, a[1] + t * uy), (vx, vy))
        if best is not None:
            out.append((i, best[0], best[1], best[2]))
    return tuple(out)


def _dim_item(aid, p, q, scale, text):
    """p -> q 를 재는 치수 주석 항목(annot.annot_draw 의 "dim" 항목과 같은 모양: 보조선 둘, 치수선, 글, 글 위치, 치수선 방향)."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = sqrt(dx * dx + dy * dy)
    ux, uy = dx / length, dy / length
    h = 1.2 * scale
    r = lambda *vs: tuple(round(v, 3) + 0.0 for v in vs)   # noqa: E731
    e0 = r(p[0] + uy * h, p[1] - ux * h, p[0] - uy * h, p[1] + ux * h)
    e1 = r(q[0] + uy * h, q[1] - ux * h, q[0] - uy * h, q[1] + ux * h)
    return ("dim", aid, e0, e1, r(p[0], p[1], q[0], q[1]), text, r((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0), r(ux, uy))


def _fmt_mm(v) -> str:
    r = round(v)
    return str(int(r)) if abs(v - r) < 0.05 else f"{v:.1f}"


def site_dims(g, v):
    """배치도의 치수: 대지 경계에서 건물까지 이격 거리, 접한 도로의 폭. 연관 치수 주석과 같은 항목(id 는 'site:...')이다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "site":
        return ()
    scale = cfg[4]
    out = []
    for s in g.q(all_sites):
        poly, _name, _north, roads, _el = g.q(site_info, s)
        for i, d, foot, vert in g.q(site_setbacks, s):
            if abs(d) < 1.0:
                continue
            out.append(_dim_item(f"site:{s[-6:]}:setback:{i}", foot, vert, scale, _fmt_mm(d)))
        for _rn, edge, width in roads:
            a, b, _length, (nx, ny) = _edge_frame(poly, edge)
            m = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            out.append(_dim_item(f"site:{s[-6:]}:road:{edge}", m, (m[0] + nx * width, m[1] + ny * width), scale, _fmt_mm(width)))
    return tuple(out)


def site_lines(g, v):
    """배치도의 선분: 대지 경계(1점쇄선), 건물 외곽, 도로 띠(바깥 변·양 옆), 방위표."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "site":
        return ()
    scale = cfg[4]
    segs = []
    for s in g.q(all_sites):
        poly, _name, _north, roads, _el = g.q(site_info, s)
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            segs.extend(_dash_dot(a[0], a[1], b[0], b[1], scale))
        for _rn, edge, width in roads:
            a, b, _length, (nx, ny) = _edge_frame(poly, edge)
            a2, b2 = (a[0] + nx * width, a[1] + ny * width), (b[0] + nx * width, b[1] + ny * width)
            segs += [("proj", a[0], a[1], a2[0], a2[1]), ("proj", b[0], b[1], b2[0], b2[1]), ("proj", a2[0], a2[1], b2[0], b2[1])]
    segs.extend(("cut", x0, y0, x1, y1) for x0, y0, x1, y1 in g.q(building_outline))
    boxes, north = g.q(_site_extent)
    if boxes:
        segs.extend(_north_symbol(boxes, scale, north)[0])
    return merge_lines(segs)


def site_labels(g, v):
    """배치도의 문자: 대지 이름·면적·표고, 도로 이름·폭·접도 길이, 방위 N, 축척."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "site":
        return ()
    scale = cfg[4]
    out = []
    for s in g.q(all_sites):
        poly, name, _north, roads, el = g.q(site_info, s)
        cx, _cy = polygon_centroid(poly)
        top = max(q[1] for q in poly)       # 건물과 겹치지 않게 대지 위쪽 안에 놓는다
        out.append((round(cx, 3), round(top - 7.0 * scale, 3), name, SITE_LABEL_H, "middle"))
        out.append((round(cx, 3), round(top - 10.0 * scale, 3), f"대지면적 {area_text(poly)}", SITE_SUB_H, "middle"))
        if el is not None:
            out.append((round(cx, 3), round(top - 13.0 * scale, 3), f"대지 표고 EL {el / 1000.0:+.3f}m", SITE_SUB_H, "middle"))
        for rn, edge, width in roads:
            a, b, length, (nx, ny) = _edge_frame(poly, edge)
            m = (a[0] + (b[0] - a[0]) * 0.22, a[1] + (b[1] - a[1]) * 0.22)      # 변 한가운데는 폭 치수가 차지한다
            lx, ly = m[0] + nx * width * 0.5, m[1] + ny * width * 0.5
            out.append((round(lx, 3), round(ly + 0.6 * scale, 3), f"{rn or '도로'} 폭 {_fmt_mm(width)}", SITE_SUB_H, "middle"))
            out.append((round(lx, 3), round(ly - 2.4 * scale, 3), f"접도 길이 {_fmt_mm(length)}", SITE_SUB_H, "middle"))
    boxes, north = g.q(_site_extent)
    if boxes:
        _sym, lab, bb = _north_symbol(boxes, scale, north)
        out.extend(lab)
        out.append((round((bb[0] + bb[2]) / 2.0, 3), round(bb[1] - 4.0 * scale, 3), f"S=1:{scale}", SITE_SUB_H, "middle"))
    return tuple(out)


# ================================================================ 숨은 선 (옵션: sheet.export 의 profile.hidden_lines)
def _complement(vis):
    """보이는 구간 [(t0, t1), ...](정렬) -> 가려진 구간들(0..1 안)."""
    out, cur = [], 0.0
    for a, b in vis:
        if a > cur + 1e-9:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < 1.0 - 1e-9:
        out.append((cur, 1.0))
    return out


def vwall_hidden(g, v, w):
    """단면·입면에서 벽 하나의 가려진 모서리 선분 (등급 "hid", s, z, s, z)들. vwall_lines 와 같은 가림 판정(visible_parts)의 여집합이다.
    벽의 바깥 모서리와 문 개구부 윤곽이 앞에 있는 벽·기둥·슬래브·계단에 가려지는 부분이다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] in ("plan", "site"):
        return ()
    occ = _wall_occluders(g, v, w)
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    out = []
    for e0, e1 in g.q(vedges, v, w) + g.q(vfam_hosted_edges, v, w):
        for t0, t1 in _complement(visible_parts(e0, e1, occ)):
            out.append(("hid", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]), e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    return tuple(out)


def view_hidden_lines(g, v):
    """뷰의 숨은 선(파선으로 그릴 선분, 등급 "hid"). 단면·입면만 있다(평면·배치도는 없다). 보이는 선과 같은 직선 위에 겹치는 부분은 뺀다(실선이 우선)."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] in ("plan", "site"):
        return ()
    hid = []
    for w in g.q(scope_walls, v):
        hid.extend(g.q(vwall_hidden, v, w))
    if not hid:
        return ()
    vis = [("cut",) + tuple(ln[1:]) for ln in g.q(view_lines, v)]
    merged = merge_lines(vis + [("proj",) + h[1:] for h in hid])
    return tuple(("hid",) + ln[1:] for ln in merged if ln[0] == "proj")


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
        if "cutn" in merged:   # 비내력벽 절단선(선 굵기 위계를 켠 뷰에만 있다)이 덮는 곳은 아래 등급에서 지운다. 내력벽 절단선(cut)이 먼저 덮는다
            for cls in merged:
                if cls not in ("cut", "cutn"):
                    merged[cls] = _subtract(merged[cls], merged["cutn"])
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
    if cfg[0] == "site":
        return g.q(site_lines, v)
    fn = plan_wall_lines if cfg[0] == "plan" else vwall_lines
    segs = []
    for w in g.q(scope_walls, v):
        segs.extend(g.q(fn, v, w))
        if cfg[0] == "plan":
            segs.extend(g.q(plan_fire_lines, v, w))
        elif cfg[0] == "elev":
            segs.extend(g.q(elev_finish, v, w)[0])
    for e in g.q(scope_fams, v):
        segs.extend(g.q(fam_lines, v, e))
    for sl in g.q(scope_slabs, v):
        segs.extend(g.q(slab_lines, v, sl))
    for st in g.q(scope_stairs, v):
        segs.extend(g.q(stair_lines, v, st))
    return merge_lines(segs)


def _falsy(v) -> bool:
    return v is False or (isinstance(v, str) and v.strip().lower() in ("false", "no", "n", "0", "아니오", "비내력"))


def is_nonbearing(g, w) -> bool:
    """벽이 비내력벽인가: Pset_WallCommon.LoadBearing 이 명시적으로 거짓일 때만(값이 없으면 구분하지 않는다: 내력벽처럼 그린다)."""
    return _falsy(_prop(g, w, "Pset_WallCommon", "LoadBearing"))


def view_lines_hier(g, v):
    """선 굵기 위계(sheet.export profile.line_hierarchy)용 뷰 선분: view_lines 와 같되 비내력벽(LoadBearing 이 거짓)의 절단선은 "cutn" 등급이다(LN-04).
    비내력벽이 없으면 view_lines 와 같은 값이다. 평면·단면의 벽 절단선만 나눈다(입면에는 절단선이 없다)."""
    cfg = g.q(view_cfg, v)
    walls = g.q(scope_walls, v)
    if cfg is None or cfg[0] in ("site", "elev") or not any(g.q(is_nonbearing, w) for w in walls):
        return g.q(view_lines, v)
    fn = plan_wall_lines if cfg[0] == "plan" else vwall_lines
    segs = []
    for w in walls:
        ws = g.q(fn, v, w)
        if g.q(is_nonbearing, w):
            ws = tuple(("cutn",) + ln[1:] if ln[0] == "cut" else ln for ln in ws)
        segs.extend(ws)
        if cfg[0] == "plan":
            segs.extend(g.q(plan_fire_lines, v, w))
    for e in g.q(scope_fams, v):
        segs.extend(g.q(fam_lines, v, e))
    for sl in g.q(scope_slabs, v):
        segs.extend(g.q(slab_lines, v, sl))
    for st in g.q(scope_stairs, v):
        segs.extend(g.q(stair_lines, v, st))
    return merge_lines(segs)


def view_fills(g, v):
    """절단 단면의 채움 다각형(평면: 벽 절단 사각형, 단면: 절단 사각형). 입면은 없음."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
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
    for sl in g.q(scope_slabs, v):
        if cfg[0] == "plan":
            out.extend(g.q(plan_slab_parts, v, sl)[1])
        else:
            out.extend(g.q(slab_cut_rects, v, sl))
    for st in g.q(scope_stairs, v):
        if cfg[0] != "plan":
            out.extend(g.q(stair_cut_rects, v, st))
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
VIEW_SCOPED = frozenset({view_cfg, scope_walls, view_omitted, plan_pieces, plan_bbox, plan_nbrs, plan_wall_lines, plan_detail_lines, view_detail_lines, vpieces, vbbox, vnbrs,
                         vwall_detail, slab_detail_lines, space_ceiling_lines, vwin_detail, view_lines_hier,
                         vcut_rects, vedges, vwall_lines, view_lines, view_fills,
                         scope_fams, plan_fam_parts, _fam_vpieces, vfam_bbox, vfam_cut_rects, vfam_edges, vfam_nbr_pieces, fam_lines,
                         vfam_hosted_edges, scope_slabs, plan_slab_parts, _slab_vpieces, slab_vbbox, slab_cut_rects, slab_vedges, slab_nbr_walls, slab_lines,
                         scope_stairs, plan_stair_parts, _stair_vpieces, stair_vbbox, stair_cut_rects, stair_vedges, stair_nbr_walls, stair_lines,
                         scope_spaces, plan_space_labels, view_labels, site_lines, site_labels, site_dims, vwall_hidden, view_hidden_lines, plan_fire_lines, plan_fire_labels, elev_finish})


def forget_views(g, vids) -> int:
    """지워진 뷰의 질의 노드(선분·조각·경계 상자 등)를 그래프에서 치운다. 치운 노드 수를 돌려준다.
    뷰 id 는 다른 요소와 겹치지 않으므로 (함수, 뷰 id, ...) 노드만 정확히 골라낸다."""
    vids = set(vids)
    if not vids:
        return 0
    return g.drop([k for k in g.nodes if k[0] in VIEW_SCOPED and k[1] in vids])
