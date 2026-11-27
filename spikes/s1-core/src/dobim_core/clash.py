# SPDX-License-Identifier: MPL-2.0
"""간섭 검토의 순수 계산(W2 스물여덟 번째 PR, COL-08). 요소의 3D 겹침을 찾는다. 모델을 바꾸지 않는다.

구조
  1. 볼록 다면체(Cx): 형상은 모두 볼록 조각의 합으로 나눈다(벽·슬래브는 윤곽을 사다리꼴로, 보는 beams.pieces, 계단은 stair_pieces, 솔리드 부재는 단면 사다리꼴 ...).
  2. 넓은 단계: 요소별 경계 상자를 균일 격자 해시에 넣어 후보 쌍만 만든다(O(n²) 가 아니다).
  3. 좁은 단계: 후보 쌍의 조각끼리 분리축 정리(SAT)로 겹침 깊이(최소 이동 거리)를 구한다. 깊이가 허용 오차 이하(면 접촉 포함)면 간섭이 아니다.
  4. 상세(보고할 쌍만): 한 다면체를 다른 다면체의 면 평면들로 잘라 겹친 부피와 도심(대표점)을 구한다.

형상의 근거: 벽·보·솔리드 부재·슬래브·계단·패밀리 기둥 등은 IFC 로 내보내는 몸체와 같은 값(정확, basis exact)이다. 지붕·난간·승강기·커튼월·
볼록하지 않은 일반 솔리드·회전·스윕 패밀리 몸체는 경계 상자나 외곽 근사(basis approx)다. 근사 형상이 낀 쌍은 보고서에 그렇게 적는다.
이 모듈은 ifcopenshell 을 import 하지 않고 시각·난수·파일 접근·libm(sin·cos·hypot·atan2) 없이 sqrt 와 사칙연산만 쓴다.
"""
from __future__ import annotations

from math import sqrt

from dobim_core import arcgeom as AG
from dobim_core import beams as BM
from dobim_core import kinds as K
from dobim_core import queries as Q
from dobim_core import solidops as SX
from dobim_core import solids as SO
from dobim_core import walltop as WT

EPS_TOUCH = 1e-6        # mm: 겹침 깊이가 이 이하면 면 접촉(분리)으로 본다
EPS_PT = 1e-7           # mm: 평면 위·아래 판정
DEFAULT_TOL = 1.0       # mm: 기본 허용 오차(겹침 깊이가 이보다 커야 간섭)
GRID_MIN, GRID_MAX = 500.0, 20000.0   # 넓은 단계 격자 칸 mm 의 하한·상한
BIG_CELLS = 2000        # 한 요소가 이보다 많은 칸에 걸치면 격자에 넣지 않고 따로 비교한다
RAIL_T = 60.0           # 난간 근사 상자의 두께 mm(경로 양쪽으로 반씩)

SHAPE_KINDS = ("wall", "door", "slab", "roof", "stair", "railing", "elevator", "solid", "finst", "beam", "curtwall")


# ---------------------------------------------------------------- 벡터
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _len(a):
    return sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _mean(pts):
    n = float(len(pts))
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n, sum(p[2] for p in pts) / n)


def _newell(pts):
    """다각형의 면적 벡터(오른손 법선 방향, 크기 = 넓이의 2배)."""
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return (nx, ny, nz)


def _pseudo_angle(dx, dy):
    """atan2 대신 쓰는 단조 의사 각도(0~4): 점들을 한 방향으로 돌며 정렬하는 데만 쓴다."""
    s = abs(dx) + abs(dy)
    if s == 0.0:
        return 0.0
    p = dx / s
    return 1.0 - p if dy >= 0.0 else 3.0 + p


# ---------------------------------------------------------------- 볼록 다면체
class Cx:
    """볼록 다면체. 면은 바깥에서 볼 때 반시계 꼭짓점 번호 고리, planes = ((nx, ny, nz), d) 는 바깥 단위 법선과 n.p = d(안쪽은 n.p <= d)."""
    __slots__ = ("verts", "loops", "planes", "edges", "lo", "hi", "vol")

    def __init__(self, verts, loops):
        self.verts = tuple(verts)
        c = _mean(self.verts)
        fixed, planes = [], []
        for lp in loops:
            pts = [self.verts[i] for i in lp]
            nv = _newell(pts)
            ln = _len(nv)
            if ln < 1e-9:
                continue
            n = (nv[0] / ln, nv[1] / ln, nv[2] / ln)
            fc = _mean(pts)
            if _dot(n, _sub(fc, c)) < 0.0:
                lp = tuple(reversed(lp))
                n = (-n[0], -n[1], -n[2])
            d = _dot(n, fc)
            fixed.append(tuple(lp))
            if not any(abs(pn[0] - n[0]) < 1e-9 and abs(pn[1] - n[1]) < 1e-9 and abs(pn[2] - n[2]) < 1e-9 and abs(pd - d) < 1e-6 for pn, pd in planes):
                planes.append((n, d))
        self.loops = tuple(fixed)
        self.planes = tuple(planes)
        seen, edges = set(), []
        for lp in fixed:
            for i in range(len(lp)):
                a, b = lp[i], lp[(i + 1) % len(lp)]
                k = (a, b) if a < b else (b, a)
                if k in seen:
                    continue
                seen.add(k)
                v = _sub(self.verts[b], self.verts[a])
                ln = _len(v)
                if ln < 1e-9:
                    continue
                v = (v[0] / ln, v[1] / ln, v[2] / ln)
                if v[0] < -1e-9 or (abs(v[0]) <= 1e-9 and (v[1] < -1e-9 or (abs(v[1]) <= 1e-9 and v[2] < 0.0))):
                    v = (-v[0], -v[1], -v[2])
                if not any(abs(e[0] - v[0]) < 1e-9 and abs(e[1] - v[1]) < 1e-9 and abs(e[2] - v[2]) < 1e-9 for e in edges):
                    edges.append(v)
        self.edges = tuple(edges)
        xs, ys, zs = [v[0] for v in self.verts], [v[1] for v in self.verts], [v[2] for v in self.verts]
        self.lo = (min(xs), min(ys), min(zs))
        self.hi = (max(xs), max(ys), max(zs))
        vol = 0.0
        for lp in fixed:
            pts = [self.verts[i] for i in lp]
            av = _newell(pts)
            vol += abs(_dot(av, _sub(pts[0], c))) / 6.0
        self.vol = vol

    @staticmethod
    def rings(r0, r1):
        """같은 꼭짓점 수의 볼록 고리 둘(밑·윗면)을 옆면으로 이은 다면체. 퇴화(부피 0)면 None."""
        n = len(r0)
        if n < 3 or len(r1) != n:
            return None
        verts = list(r0) + list(r1)
        loops = [tuple(range(n)), tuple(range(n, 2 * n))]
        for i in range(n):
            j = (i + 1) % n
            loops.append((i, j, n + j, n + i))
        c = Cx(verts, loops)
        return c if c.vol > 1e-6 and len(c.planes) >= 4 else None

    @staticmethod
    def mesh(verts, loops):
        """다면체가 볼록이면 Cx, 아니면 None(모든 꼭짓점이 모든 면 평면의 안쪽에 있어야 한다)."""
        c = Cx(verts, loops)
        if c.vol <= 1e-6 or len(c.planes) < 4:
            return None
        for n, d in c.planes:
            for v in c.verts:
                if _dot(n, v) > d + 1e-6:
                    return None
        return c


def box_cx(lo, hi):
    """축 정렬 상자. 한 축이라도 두께가 없으면 None."""
    if any(hi[k] - lo[k] <= 1e-6 for k in range(3)):
        return None
    r0 = [(lo[0], lo[1], lo[2]), (hi[0], lo[1], lo[2]), (hi[0], hi[1], lo[2]), (lo[0], hi[1], lo[2])]
    r1 = [(p[0], p[1], hi[2]) for p in r0]
    return Cx.rings(r0, r1)


def prism_cx(poly, zb, zt):
    """평면 볼록 다각형(x, y)을 zb~zt 로 돌출한 다면체."""
    if zt - zb <= 1e-6:
        return None
    return Cx.rings([(p[0], p[1], zb) for p in poly], [(p[0], p[1], zt) for p in poly])


# ---------------------------------------------------------------- 겹침 깊이(분리축 정리)와 겹친 부분
def _span(c, ax):
    d0 = d1 = _dot(ax, c.verts[0])
    for v in c.verts[1:]:
        t = _dot(ax, v)
        if t < d0:
            d0 = t
        elif t > d1:
            d1 = t
    return d0, d1


def depth(a: Cx, b: Cx) -> float:
    """두 볼록 다면체의 겹침 깊이 = 겹침을 풀기 위한 최소 이동 거리(분리축 정리: 두 다면체의 면 법선과 모서리 쌍의 외적 축). 분리되었거나 면만 닿으면 0."""
    for k in range(3):
        if a.hi[k] - b.lo[k] <= EPS_TOUCH or b.hi[k] - a.lo[k] <= EPS_TOUCH:
            return 0.0
    best = 1e300
    for n, _d in a.planes + b.planes:
        a0, a1 = _span(a, n)
        b0, b1 = _span(b, n)
        ov = min(a1 - b0, b1 - a0)
        if ov <= EPS_TOUCH:
            return 0.0
        if ov < best:
            best = ov
    for ea in a.edges:
        for eb in b.edges:
            n = _cross(ea, eb)
            ln = _len(n)
            if ln < 1e-9:
                continue
            n = (n[0] / ln, n[1] / ln, n[2] / ln)
            a0, a1 = _span(a, n)
            b0, b1 = _span(b, n)
            ov = min(a1 - b0, b1 - a0)
            if ov <= EPS_TOUCH:
                return 0.0
            if ov < best:
                best = ov
    return best


def _clip(faces, n, d):
    """다각형 면들을 반공간 n.p <= d 로 자른다(닫힌 다면체면 마개 면을 더한다). 남는 것이 없으면 []."""
    out, cap = [], []
    for poly in faces:
        m = len(poly)
        dist = [_dot(n, p) - d for p in poly]
        res = []
        for i in range(m):
            p, q = poly[i], poly[(i + 1) % m]
            dp, dq = dist[i], dist[(i + 1) % m]
            if dp <= EPS_PT:
                res.append(p)
            if (dp < -EPS_PT and dq > EPS_PT) or (dp > EPS_PT and dq < -EPS_PT):
                t = dp / (dp - dq)
                res.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, p[2] + (q[2] - p[2]) * t))
        if len(res) >= 3:
            out.append(res)
            if all(abs(_dot(n, p) - d) <= EPS_PT for p in res):
                cap.append(None)   # 이 면이 이미 평면 위에 있다: 마개가 따로 필요 없다
    if not out or cap:
        return out
    pts = []
    for poly in out:
        for p in poly:
            if abs(_dot(n, p) - d) <= EPS_PT and not any(abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6 and abs(p[2] - q[2]) < 1e-6 for q in pts):
                pts.append(p)
    if len(pts) >= 3:
        m = _mean(pts)
        ref = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
        e1 = _cross(ref, n)
        l1 = _len(e1)
        e1 = (e1[0] / l1, e1[1] / l1, e1[2] / l1)
        e2 = _cross(n, e1)
        pts.sort(key=lambda p: _pseudo_angle(_dot(_sub(p, m), e1), _dot(_sub(p, m), e2)))
        out.append(pts)
    return out


def overlap_poly(a: Cx, b: Cx):
    """겹친 부분의 (부피 mm³, 도심 (x, y, z), 꼭짓점 목록). 안 겹치면 (0.0, None, []). a 의 면을 b 의 면 평면들로 자른다."""
    faces = [[a.verts[i] for i in lp] for lp in a.loops]
    for n, d in b.planes:
        faces = _clip(faces, n, d)
        if not faces:
            return 0.0, None, []
    allp = [p for f in faces for p in f]
    c0 = _mean(allp)
    vol = 0.0
    acc = [0.0, 0.0, 0.0]
    for poly in faces:
        if len(poly) < 3:
            continue
        p0 = poly[0]
        av = [0.0, 0.0, 0.0]
        ta, cx = 0.0, [0.0, 0.0, 0.0]
        for i in range(1, len(poly) - 1):
            u, v = _sub(poly[i], p0), _sub(poly[i + 1], p0)
            cr = _cross(u, v)
            av[0] += cr[0]
            av[1] += cr[1]
            av[2] += cr[2]
            t2 = _len(cr)
            ta += t2
            for k in range(3):
                cx[k] += t2 * (p0[k] + poly[i][k] + poly[i + 1][k]) / 3.0
        if ta <= 0.0:
            continue
        cf = (cx[0] / ta, cx[1] / ta, cx[2] / ta)
        pv = abs(_dot(av, _sub(p0, c0))) / 6.0
        vol += pv
        for k in range(3):
            acc[k] += pv * (c0[k] + 0.75 * (cf[k] - c0[k]))
    if vol <= 1e-9:
        return 0.0, None, []
    return vol, (acc[0] / vol, acc[1] / vol, acc[2] / vol), allp


def overlap(a: Cx, b: Cx):
    """겹친 부분의 (부피 mm³, 도심). 안 겹치면 (0.0, None)."""
    v, c, _pts = overlap_poly(a, b)
    return v, c


# ---------------------------------------------------------------- 형상(요소 -> 볼록 조각)
class Shape:
    """요소 하나의 볼록 조각들과 경계 상자. exact = IFC 몸체와 같은 형상(아니면 근사)."""
    __slots__ = ("pieces", "exact", "lo", "hi")

    def __init__(self, pieces, exact=True):
        self.pieces = tuple(p for p in pieces if p is not None)
        self.exact = exact
        if self.pieces:
            self.lo = tuple(min(p.lo[k] for p in self.pieces) for k in range(3))
            self.hi = tuple(max(p.hi[k] for p in self.pieces) for k in range(3))
        else:
            self.lo = self.hi = (0.0, 0.0, 0.0)


def _ccw2(pts):
    pts = [(float(p[0]), float(p[1])) for p in pts]
    return pts if SO.area2(pts) >= 0 else pts[::-1]


def plan_pieces(outer, holes=()):
    """평면(2D) 윤곽(+구멍)을 볼록 조각 다각형들로. 구멍 없는 볼록 윤곽은 하나, 나머지는 가로 띠 사다리꼴."""
    outer = _ccw2(outer)
    if not holes and SX._convex2(outer):
        return [outer]
    return SX._trapezoids([outer] + [[(float(q[0]), float(q[1])) for q in h] for h in holes])


def _rect_loop(u0, u1, z0, z1):
    return [(u0, z0), (u1, z0), (u1, z1), (u0, z1)]


def _bbox2(lp):
    us, zs = [q[0] for q in lp], [q[1] for q in lp]
    return min(us), max(us), min(zs), max(zs)


def _usable_holes(outer, cands, top_profile=None):
    """벽 (u, z) 윤곽 안에 온전히 드는 서로 겹치지 않는 구멍만 받는다(그렇지 않은 구멍은 무시해 벽이 더 막힌 쪽으로 보수적으로 둔다)."""
    ob = _bbox2(outer)
    taken, boxes = [], []
    for lp in cands:
        b = _bbox2(lp)
        if b[0] < ob[0] - 1e-6 or b[1] > ob[1] + 1e-6 or b[2] < ob[2] - 1e-6 or b[3] > ob[3] + 1e-6:
            continue
        if top_profile is not None and b[3] > WT.zmin(top_profile, b[0], b[1]) + 1e-6:
            continue
        if any(b[0] < o[1] and o[0] < b[1] and b[2] < o[3] and o[2] < b[3] for o in boxes):
            continue
        taken.append(lp)
        boxes.append(b)
    return taken


def _wall_shape(g, e):
    p = Q.el(g, e)["params"]
    elev = g.q(Q.level_info, g.q(Q.wall_level, e))[1]
    half = p["thickness"] / 2.0
    ai = g.q(Q.wall_arc, e)
    base = WT.base(p)
    top = WT.top_rel(p)
    if ai is not None:   # 곡선 벽: 호를 현으로 나눈 사다리꼴 각기둥(근사). 개구부는 보지 않는다
        n = AG.facet_n(ai)
        ln = AG.length(ai)
        out = []
        for k in range(n):
            u0, u1 = ln * k / n, ln * (k + 1) / n
            q = [AG.point(ai, u0, -half), AG.point(ai, u1, -half), AG.point(ai, u1, half), AG.point(ai, u0, half)]
            out.append(prism_cx(q, elev + base, elev + top))
        return Shape(out, False)
    x, y, c, s, _ = g.q(Q.wall_frame, e)
    start, end = g.q(Q.wall_extent, e)
    prof = p.get("top_profile")
    outer = WT.loop(prof, start, end, base) if prof is not None else _rect_loop(start, end, base, top)
    cands = []
    for o in p.get("openings", ()):
        cands.append([(float(q[0]), float(q[1])) for q in SO.opening_loop(o)])
    for _o, _n, d in (g.q(Q.wall_voids, e) if g.q(Q.opening_objs) else ()):
        cands.append([(float(q[0]), float(q[1])) for q in SO.opening_loop(d)])
    for d in g.q(Q.wall_hosted, e):   # 호스팅 문·창의 개구부
        row = Q.el(g, d)
        if row["kind"] == "door":
            dp = row["params"]
            cands.append(_rect_loop(dp["offset"] - dp["width"] / 2.0, dp["offset"] + dp["width"] / 2.0, dp["sill"], dp["sill"] + dp["height"]))
        else:
            bx = g.q(Q.finst_box, d)
            if bx is not None:
                cands.append(_rect_loop(bx[0], bx[1], bx[2], bx[3]))
    holes = _usable_holes(outer, cands, prof)

    def to3(u, z, v):
        return (x + u * c - v * s, y + u * s + v * c, elev + z)
    out = []
    for q in plan_pieces(outer, holes):
        out.append(Cx.rings([to3(u, z, -half) for u, z in q], [to3(u, z, half) for u, z in q]))
    return Shape(out, True)


def _door_shape(g, e):
    p = Q.el(g, e)["params"]
    h = g.q(Q.door_host, e)
    if g.q(Q.wall_arc, h) is not None:
        return None
    hp = Q.el(g, h)["params"]
    elev = g.q(Q.level_info, g.q(Q.wall_level, h))[1]
    x, y, c, s, _ = g.q(Q.wall_frame, h)
    half = hp["thickness"] / 2.0
    u0, u1 = p["offset"] - p["width"] / 2.0, p["offset"] + p["width"] / 2.0
    z0, z1 = elev + p["sill"], elev + p["sill"] + p["height"]

    def to3(u, z, v):
        return (x + u * c - v * s, y + u * s + v * c, z)
    r = [(u0, z0), (u1, z0), (u1, z1), (u0, z1)]
    return Shape([Cx.rings([to3(u, z, -half) for u, z in r], [to3(u, z, half) for u, z in r])], True)


def _slab_shape(g, e):
    p = Q.el(g, e)["params"]
    zb, zt = g.q(Q.slab_zrange, e)
    holes = [[(float(q[0]), float(q[1])) for q in h] for h in p.get("openings", ())]
    if g.q(Q.opening_objs):
        holes += [[(float(q[0]), float(q[1])) for q in lp] for _o, _n, lp in g.q(Q.slab_voids, e)]
    return Shape([prism_cx(q, zb, zt) for q in plan_pieces(p["boundary"], holes)], True)


def _beam_shape(g, e):
    p = Q.el(g, e)["params"]
    c = g.q(Q.beam_cut, e)
    z_ref = g.q(Q.beam_zref, e)
    ends = g.q(Q.beam_ends, e)
    out = []
    for pc in BM.pieces(p, z_ref, c[:2], ends):
        out.append(Cx.rings(list(pc[:4]), list(pc[4:])))
    return Shape(out, True)


def _extrude_section(outer, holes, to3, length):
    """단면(바깥 + 구멍)을 to3(2D, t) 로 놓고 length 만큼 돌출한 볼록 조각들. 속이 빈 단면은 바깥 윤곽만 쓴다(덕트·강관 속은 간섭 대상이 아니다)."""
    out = []
    for q in plan_pieces(outer, ()):
        out.append(Cx.rings([to3(pt, 0.0) for pt in q], [to3(pt, length) for pt in q]))
    return out


def _solid_shape(g, e):
    p = g.q(Q.solid_params, e)
    elev = g.q(Q.level_info, g.q(Q.solid_level, e))[1]
    form = p["form"]
    if "ops" not in p:
        if form == "prism":
            z0, z1 = elev + float(p["z0"]), elev + float(p["z1"])
            holes = [[(float(q[0]), float(q[1])) for q in h] for h in p.get("holes", ())]
            return Shape([prism_cx(q, z0, z1) for q in plan_pieces(p["polygon"], holes)], True)
        if form == "member":
            o, D, S_, U, ln = SO.member_frame(p)
            outer, _holes = SO.member_section(p["section"], p["anchor"], p.get("res"))

            def to3(q, t):
                return (o[0] + q[0] * S_[0] + q[1] * U[0] + t * D[0], o[1] + q[0] * S_[1] + q[1] * U[1] + t * D[1], elev + o[2] + q[0] * S_[2] + q[1] * U[2] + t * D[2])
            return Shape(_extrude_section(outer, (), to3, ln), True)
        if form == "panel":
            x1, y1, ux, uy, _ = SO.panel_frame(p["line"])
            h = p["thickness"] / 2.0
            nx, ny = -uy, ux
            holes = [[(float(q[0]), float(q[1])) for q in SO.opening_loop(o_)] for o_ in p.get("openings", ())]
            out = []
            for q in plan_pieces(p["profile"], _usable_holes(_ccw2(p["profile"]), holes)):
                out.append(Cx.rings([(x1 + u * ux + h * nx, y1 + u * uy + h * ny, elev + z) for u, z in q],
                                    [(x1 + u * ux - h * nx, y1 + u * uy - h * ny, elev + z) for u, z in q]))
            return Shape(out, True)
    verts, faces = SO.solid_mesh(p)   # 그 밖의 형식(로프트·스윕·회전·메시·판·불리언): 볼록이면 그대로, 아니면 경계 상자(근사)
    if not verts:
        return None
    vv = [(v[0], v[1], v[2] + elev) for v in verts]
    loops = [tuple(f[0]) for f in faces if f]
    cx = Cx.mesh(vv, loops)
    if cx is not None:
        return Shape([cx], True)
    return Shape([box_cx((min(v[0] for v in vv), min(v[1] for v in vv), min(v[2] for v in vv)),
                         (max(v[0] for v in vv), max(v[1] for v in vv), max(v[2] for v in vv)))], False)


def _stair_shape(g, e):
    p = Q.el(g, e)["params"]
    zb, _zt = g.q(Q.stair_zrange, e)
    return Shape([prism_cx(_ccw2(poly), zb + z0, zb + z1) for poly, z0, z1 in K.stair_pieces(p)], True)


def _roof_shape(g, e):
    p = Q.el(g, e)["params"]
    zb, zt = g.q(Q.roof_zrange, e)
    pts = [(float(q[0]), float(q[1])) for q in p["boundary"]]
    oh = max([float(p.get("overhang", 0.0))] + [float(v) for v in p.get("overhangs", ())])
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return Shape([box_cx((min(xs) - oh, min(ys) - oh, zb), (max(xs) + oh, max(ys) + oh, zt))], False)


def _railing_shape(g, e):
    p = Q.el(g, e)["params"]
    elev = g.q(Q.level_info, g.q(Q.railing_level, e))[1]
    z0 = elev + p["base_offset"]
    z1 = z0 + p["height"]
    out = []
    path = p["path"]
    for a, b in zip(path, path[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = sqrt(dx * dx + dy * dy)
        if ln < 1e-6:
            continue
        nx, ny = -dy / ln * RAIL_T / 2.0, dx / ln * RAIL_T / 2.0
        q = [(a[0] - nx, a[1] - ny), (b[0] - nx, b[1] - ny), (b[0] + nx, b[1] + ny), (a[0] + nx, a[1] + ny)]
        out.append(prism_cx(q, z0, z1))
    return Shape(out, False)


def _elevator_shape(g, e):
    p = Q.el(g, e)["params"]
    elev = g.q(Q.level_info, g.q(Q.elevator_level, e))[1]
    gm = K.elevator_geom(p)
    ring = K.elevator_ring(p, gm["shaft"])
    return Shape([prism_cx(_ccw2(ring), elev + gm["pit_z0"], elev + gm["mach_z1"])], False)


def _curtain_shape(g, e):
    p = Q.el(g, e)["params"]
    lay = g.q(Q.curtain_layout, e)
    base = g.q(Q.curtain_base, e)
    rects = [(q, z0, z1) for _k, _d, _t, q, z0, z1 in lay["mullions"]] + [(q, z0, z1) for _k, _pk, q, z0, z1, _c, _t in lay["panels"]]
    if not rects:
        return None
    zlo, zhi = base + min(r[1] for r in rects), base + max(r[2] for r in rects)
    if "arc" not in p:   # 직선 커튼월: 축 방향으로 놓인 상자(부속 평면 사각형을 축 틀에 투영)
        x0, y0, x1, y1 = p["axis"]
        dx, dy = x1 - x0, y1 - y0
        ln = sqrt(dx * dx + dy * dy)
        tx, ty = dx / ln, dy / ln
        us = [(q_[0] - x0) * tx + (q_[1] - y0) * ty for r in rects for q_ in r[0]]
        vs = [-(q_[0] - x0) * ty + (q_[1] - y0) * tx for r in rects for q_ in r[0]]
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        quad = [(x0 + u * tx - v * ty, y0 + u * ty + v * tx) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
        return Shape([prism_cx(quad, zlo, zhi)], False)
    xs = [q_[0] for r in rects for q_ in r[0]]
    ys = [q_[1] for r in rects for q_ in r[0]]
    return Shape([box_cx((min(xs), min(ys), zlo), (max(xs), max(ys), zhi))], False)


def _finst_shape(g, e):
    h = g.q(Q.finst_host, e)
    spec = g.q(Q.ifc_spec, e)
    if spec.get("error"):
        return None
    if h:   # 호스팅(문·창 패밀리): 호스트 벽 두께를 가로지르는 개구부 상자
        if g.q(Q.wall_arc, h) is not None:
            return None
        bx = g.q(Q.finst_box, e)
        if bx is None:
            return None
        elev = g.q(Q.level_info, g.q(Q.wall_level, h))[1]
        x, y, c, s, _ = g.q(Q.wall_frame, h)
        half = Q.el(g, h)["params"]["thickness"] / 2.0
        r = [(bx[0], elev + bx[2]), (bx[1], elev + bx[2]), (bx[1], elev + bx[3]), (bx[0], elev + bx[3])]
        return Shape([Cx.rings([(x + u * c + half * s, y + u * s - half * c, z) for u, z in r], [(x + u * c - half * s, y + u * s + half * c, z) for u, z in r])], True)
    if not spec.get("position"):
        return None
    from dobim_core.family.validate import _aabb
    zb = g.q(Q.level_info, g.q(Q.finst_level, e))[1] + spec["elevation"]
    sn, cs = SO.sincos_deg(float(spec.get("rotation", 0.0) or 0.0))
    x0, y0 = spec["position"][0], spec["position"][1]

    def w2(u, v):
        return (x0 + u * cs - v * sn, y0 + u * sn + v * cs)
    out, exact = [], True
    for b in spec["body"]:
        pr = b.get("profile") if b.get("k") == "extrude" else None
        if pr is not None and b.get("plane") == "xy" and not b.get("rotate") and pr.get("shape") in ("circle", "polygon", "rect"):
            sh = pr["shape"]
            if sh == "rect":
                (u0, u1), (v0, v1) = pr["u"], pr["v"]
                loc = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
            elif sh == "circle":
                loc = SO._circle(pr["r"], pr["center"][0], pr["center"][1])
            else:
                loc = [tuple(q) for q in pr["points"]]
            for q in plan_pieces([w2(u, v) for u, v in loc]):
                out.append(prism_cx(q, zb + b["range"][0], zb + b["range"][1]))
            continue
        lo, hi = _aabb(b)   # 그 밖의 몸체(다른 평면·회전·스윕·회전체): 로컬 경계 상자를 배치 회전만큼 돌린 상자(근사)
        is_box = pr is not None and pr.get("shape") == "rect" and not b.get("rotate")
        exact = exact and is_box
        quad = [w2(lo[0], lo[1]), w2(hi[0], lo[1]), w2(hi[0], hi[1]), w2(lo[0], hi[1])]
        out.append(prism_cx(quad, zb + lo[2], zb + hi[2]))
    return Shape(out, exact) if out else None


_BUILDERS = {"wall": _wall_shape, "door": _door_shape, "slab": _slab_shape, "roof": _roof_shape, "stair": _stair_shape, "railing": _railing_shape,
             "elevator": _elevator_shape, "solid": _solid_shape, "finst": _finst_shape, "beam": _beam_shape, "curtwall": _curtain_shape}


def build_shape(g, e):
    """요소의 형상(Shape) 또는 None(형상이 없거나 지원하지 않는 종류, 또는 퇴화). 그래프 캐시에 넣지 않는다(5만 요소의 조각을 붙들지 않는다)."""
    row = Q.el(g, e)
    if row is None:
        return None
    fn = _BUILDERS.get(row["kind"])
    if fn is None or row["origin"] != "authored":
        return None
    try:
        sh = fn(g, e)
    except (KeyError, ValueError, ZeroDivisionError, IndexError, TypeError, AttributeError):   # 오류 상태(깨진 패밀리 값 등)의 요소는 형상이 없다
        return None
    return sh if sh is not None and sh.pieces else None


# ---------------------------------------------------------------- 선택·의도된 접합

def linked_pairs(g, ids) -> set:
    """설계상 의도된 접합 쌍 {(작은 id, 큰 id)}: 호스팅(벽-문·창), 조인(벽-벽), 보 끝이 닿은 기둥·주 보, 함께 자르는 창의 벽, 난간의 원천, 승강기가 뚫는 슬래브."""
    out = set()

    def add(a, b):
        if a and b and a != b:
            out.add((a, b) if a < b else (b, a))
    for e in ids:
        row = Q.el(g, e)
        k, p = row["kind"], row["params"]
        if k == "wall":
            for d in g.q(Q.wall_hosted, e):
                add(e, d)
            for o in g.q(Q.wall_joins, e):
                add(e, o)
        elif k == "door":
            add(e, g.q(Q.door_host, e))
        elif k == "finst":
            add(e, g.q(Q.finst_host, e))
            for w in p.get("also_cuts", ()):
                add(e, w)
        elif k == "beam":
            c = g.q(Q.beam_cut, e)
            add(e, c[2])
            add(e, c[3])
        elif k == "railing":
            add(e, p.get("src"))
        elif k == "elevator":
            for s in p.get("hole_slabs", ()):
                add(e, s)
    return out


# ---------------------------------------------------------------- 넓은 단계
def _cell(v, size):
    return int(v // size)


def candidate_pairs(boxes_a, boxes_b, tol, same):
    """경계 상자가 (tol 만큼 부풀려) 겹치는 후보 쌍의 정렬된 목록. boxes_* = [(id, lo, hi)](id 순). same 이면 한 묶음 안의 쌍(작은 id, 큰 id).
    균일 격자 해시라 요소 수에 거의 선형이다. 아주 큰 요소(칸이 많이 걸치는)는 격자에 넣지 않고 따로 비교한다."""
    pad = max(tol, 0.0) * 0.5 + EPS_TOUCH
    ext = sorted(max(b[2][k] - b[1][k] for k in range(3)) for b in boxes_b) if boxes_b else [GRID_MIN]
    size = min(GRID_MAX, max(GRID_MIN, 2.0 * ext[len(ext) // 2]))
    grid, big = {}, []
    for i, (_id, lo, hi) in enumerate(boxes_b):
        c0 = [_cell(lo[k] - pad, size) for k in range(3)]
        c1 = [_cell(hi[k] + pad, size) for k in range(3)]
        n = (c1[0] - c0[0] + 1) * (c1[1] - c0[1] + 1) * (c1[2] - c0[2] + 1)
        if n > BIG_CELLS:
            big.append(i)
            continue
        for x in range(c0[0], c1[0] + 1):
            for y in range(c0[1], c1[1] + 1):
                for z in range(c0[2], c1[2] + 1):
                    grid.setdefault((x, y, z), []).append(i)

    def hit(la, ha, lb, hb):
        return all(la[k] - pad < hb[k] and lb[k] - pad < ha[k] for k in range(3))
    pairs = set()
    for ida, la, ha in boxes_a:
        c0 = [_cell(la[k] - pad, size) for k in range(3)]
        c1 = [_cell(ha[k] + pad, size) for k in range(3)]
        n = (c1[0] - c0[0] + 1) * (c1[1] - c0[1] + 1) * (c1[2] - c0[2] + 1)
        if n > BIG_CELLS:
            cand = range(len(boxes_b))
        else:
            seen = set(big)
            for x in range(c0[0], c1[0] + 1):
                for y in range(c0[1], c1[1] + 1):
                    for z in range(c0[2], c1[2] + 1):
                        seen.update(grid.get((x, y, z), ()))
            cand = sorted(seen)
        for ib in cand:
            idb, lb, hb = boxes_b[ib]
            if ida == idb or (same and ida > idb):
                continue
            if hit(la, ha, lb, hb):
                pairs.add((ida, idb) if ida < idb else (idb, ida))
    return sorted(pairs)


# ---------------------------------------------------------------- 검사
class Run:
    """검사 한 번의 형상 캐시(요소별 한 번만 만든다)."""

    def __init__(self, g):
        self.g = g
        self.shapes: dict = {}

    def shape(self, e):
        if e not in self.shapes:
            self.shapes[e] = build_shape(self.g, e)
        return self.shapes[e]


def _width(pts, n):
    lo = hi = _dot(n, pts[0])
    for p in pts[1:]:
        t = _dot(n, p)
        if t < lo:
            lo = t
        elif t > hi:
            hi = t
    return hi - lo


def pair_overlap(sa: Shape, sb: Shape):
    """두 요소가 겹친 부분 (부피 mm³(모든 조각 쌍의 합), 깊이 mm, 대표점). 겹침이 없으면 (0.0, 0.0, None).
    겹친 덩어리(조각 쌍의 겹침 다면체가 서로 닿는 것끼리 묶는다)가 여럿이면 부피가 가장 큰 덩어리를 본다.
    깊이 = 그 덩어리의 최소 두께(세계 축과 관여한 조각 면 법선들 방향의 폭 중 가장 작은 것), 대표점 = 그 덩어리의 도심.
    예) 덕트가 벽을 가로지르면 깊이는 벽 두께(또는 덕트 높이 중 작은 쪽)이고, 면이 0.5mm 파고들면 0.5 다. 조각을 어떻게 나누어도 같은 값이다."""
    items = []   # (부피, 도심, 꼭짓점, 평면 법선 목록)
    for pa in sa.pieces:
        for pb in sb.pieces:
            if any(pa.hi[k] - pb.lo[k] <= EPS_TOUCH or pb.hi[k] - pa.lo[k] <= EPS_TOUCH for k in range(3)):
                continue
            if depth(pa, pb) <= EPS_TOUCH:   # 분리축 정리로 닿기만 하거나 떨어진 조각 쌍은 겹침이 아니다
                continue
            v, c, pts = overlap_poly(pa, pb)
            if c is not None:
                items.append((v, c, pts, [pl[0] for pl in pa.planes + pb.planes]))
    if not items:
        return 0.0, 0.0, None
    # 덩어리 묶기용 경계 상자
    boxes = [(tuple(min(p[k] for p in it[2]) for k in range(3)), tuple(max(p[k] for p in it[2]) for k in range(3))) for it in items]
    root = list(range(len(items)))

    def find(i):
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = boxes[i], boxes[j]
            if all(a[0][k] <= b[1][k] + 1e-6 and b[0][k] <= a[1][k] + 1e-6 for k in range(3)):
                root[find(j)] = find(i)
    groups: dict = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    best = max(groups.values(), key=lambda grp: (round(sum(items[x][0] for x in grp), 3), -min(boxes[x][0][0] for x in grp)))
    tv = sum(items[x][0] for x in best)
    pt = tuple(sum(items[x][0] * items[x][1][k] for x in best) / tv for k in range(3))
    pts = [p for x in best for p in items[x][2]]
    axes = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    for x in best:
        for n in items[x][3]:
            if not any(abs(n[0] - m[0]) < 1e-6 and abs(n[1] - m[1]) < 1e-6 and abs(n[2] - m[2]) < 1e-6 for m in axes):
                axes.append(n)
    dep = min(_width(pts, n) for n in axes)
    return sum(it[0] for it in items), dep, pt


def check(g, ids_a, ids_b, tol, include_connected=False, approx=True):
    """간섭 쌍을 찾는다. ids_b 가 None 이면 ids_a 안의 쌍. 반환 {"pairs": [(a, b, 깊이, exact, 부피, 대표점)](깊이 큰 순, 같으면 id 순), "no_shape": [...],
    "candidates": 넓은 단계 후보 쌍 수, "run": Run}. a < b 로 정규화한 쌍이라 같은 쌍이 두 번 나오지 않는다."""
    run = Run(g)
    same = ids_b is None
    ib = ids_a if same else ids_b
    boxes_a, boxes_b, none = [], [], []
    for e in sorted(set(ids_a) | set(ib)):
        sh = run.shape(e)
        if sh is None:
            none.append(e)
    for src, dst in ((ids_a, boxes_a), (ib, boxes_b)):
        for e in sorted(set(src)):
            sh = run.shapes.get(e)
            if sh is None or (not approx and not sh.exact):
                continue
            dst.append((e, sh.lo, sh.hi))
    cand = candidate_pairs(boxes_a, boxes_b, tol, same)
    linked = set() if include_connected else linked_pairs(g, sorted(set(e for pr in cand for e in pr)))
    out = []
    for a, b in cand:
        if (a, b) in linked:
            continue
        sa, sb = run.shapes[a], run.shapes[b]
        vol, d, pt = pair_overlap(sa, sb)
        if pt is not None and d > max(tol, EPS_TOUCH):
            out.append((a, b, d, sa.exact and sb.exact, vol, pt))
    out.sort(key=lambda t: (-round(t[2], 3), t[0], t[1]))
    return {"pairs": out, "no_shape": sorted(none), "candidates": len(cand), "run": run}