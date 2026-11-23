# SPDX-License-Identifier: MPL-2.0
"""임의 다각형 외곽의 경사 지붕(roof form "poly", W1 1-F 4차, ARC-18)의 순수 형상.

입력: 외곽 꼭짓점(반시계), 변마다 물매의 tan(0 = 박공 변: 경사면을 만들지 않고 수직으로 끝난다), 처마 내밀기, 처마 높이.
변 i(외곽 b[i] -> b[i+1])의 경사면은 처마 선(그 변)에서 안쪽으로 물매대로 오르는 평면 h_i(p) = z0 + k_i * d_i(p) 이다(d_i = 변에서 안쪽까지 거리).
지붕 윗면 = 그 점에서 "유효한" 경사면 가운데 가장 낮은 것. 변 i 가 유효한 곳은 이웃 두 변과의 이등분선 안쪽이다:
  볼록 꼭짓점이면 h_i <= h_이웃 쪽, 오목 꼭짓점이면 h_i >= h_이웃 쪽(골짜기). 박공 이웃은 볼록 꼭짓점에서 제약이 없다(오목 꼭짓점에는 박공 변을 둘 수 없다).
볼록 외곽이면 이것이 정확한 지붕 골격(같은 물매면 직선 골격)이고, L·T·U 처럼 오목 꼭짓점이 있어도 이웃 이등분선이 먼 변의 평면이 끼어드는 것을 막는다.
결과는 면마다 이어 붙였을 때 높이가 끊기지 않고 외곽을 빈틈없이 덮는지 검사한다(어긋나면 그 외곽은 거절: 지붕을 나눠 만든다).
libm 을 쓰지 않는다(sqrt 만): 같은 입력이면 어느 플랫폼에서나 같은 값이다. 이 모듈은 kinds 를 import 하지 않는다(kinds 가 이 모듈을 쓴다)."""
from __future__ import annotations

from functools import lru_cache
from math import sqrt

POLY_PTS_MAX = 32       # 외곽 꼭짓점 수 상한
FACES_MAX = 64          # 경사면(IFC 부속 IfcSlab) 수 상한
SNAP = 1e-4             # 같은 점으로 보는 거리 mm
AREA2_MIN = 0.02        # 버리는 조각의 넓이 x 2 (mm²)
Z_TOL = 0.5             # 면 경계에서 두 면의 높이 차 허용 mm
ON_TOL = 1e-3           # 점이 선분 위에 있다고 보는 거리 mm


def _area2(poly) -> float:
    n = len(poly)
    return sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))


def _clip(poly, a, b, c):
    """볼록 다각형을 a x + b y + c >= 0 쪽으로 자른다(Sutherland-Hodgman)."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        fp, fq = a * p[0] + b * p[1] + c, a * q[0] + b * q[1] + c
        if fp >= 0.0:
            out.append(p)
        if (fp >= 0.0) != (fq >= 0.0):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def _ok(poly) -> bool:
    return len(poly) >= 3 and _area2(poly) > AREA2_MIN


def _edges(b):
    """변마다 (시작점, 단위 방향, 안쪽 법선, 길이)."""
    out = []
    n = len(b)
    for i in range(n):
        p, q = b[i], b[(i + 1) % n]
        dx, dy = q[0] - p[0], q[1] - p[1]
        ln = sqrt(dx * dx + dy * dy)
        u = (dx / ln, dy / ln)
        out.append((p, u, (-u[1], u[0]), ln))
    return out


def _seg_cross(p1, p2, p3, p4) -> bool:
    """두 선분이 만나거나 닿는가(끝점 포함)."""
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)

    def on(a, b, c):
        return min(a[0], b[0]) - 1e-9 <= c[0] <= max(a[0], b[0]) + 1e-9 and min(a[1], b[1]) - 1e-9 <= c[1] <= max(a[1], b[1]) + 1e-9
    o1, o2, o3, o4 = orient(p1, p2, p3), orient(p1, p2, p4), orient(p3, p4, p1), orient(p3, p4, p2)
    if o1 != o2 and o3 != o4:
        return True
    return (o1 == 0 and on(p1, p2, p3)) or (o2 == 0 and on(p1, p2, p4)) or (o3 == 0 and on(p3, p4, p1)) or (o4 == 0 and on(p3, p4, p2))


def _simple(b) -> bool:
    n = len(b)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if _seg_cross(b[i], b[(i + 1) % n], b[j], b[(j + 1) % n]):
                return False
    return True


def _outline(b, ed, o):
    """처마를 내민 외곽: 변마다 바깥으로 o 만큼 평행 이동한 선의 교점(o = 0 이면 외곽 그대로)."""
    if o <= 0.0:
        return [tuple(q) for q in b]
    n = len(b)
    out = []
    for i in range(n):
        (p0, u0, n0, _), (p1, u1, n1, _) = ed[i - 1], ed[i]
        c0 = n0[0] * p0[0] + n0[1] * p0[1] - o
        c1 = n1[0] * p1[0] + n1[1] * p1[1] - o
        det = n0[0] * n1[1] - n0[1] * n1[0]
        out.append(((c0 * n1[1] - c1 * n0[1]) / det, (n0[0] * c1 - n1[0] * c0) / det))
    return out


def _convex_vertex(ed, i) -> bool:
    """꼭짓점 i(변 i-1 과 변 i 사이)가 볼록인가."""
    u0, u1 = ed[i - 1][1], ed[i][1]
    return u0[0] * u1[1] - u0[1] * u1[0] > 0.0


def _triangulate(poly):
    """단순 다각형(반시계)을 귀 자르기로 삼각형들로(결정적: 앞에서부터 첫 귀)."""
    idx = list(range(len(poly)))
    tris = []

    def inside(a, b, c, p):
        d1 = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        d2 = (c[0] - b[0]) * (p[1] - b[1]) - (c[1] - b[1]) * (p[0] - b[0])
        d3 = (a[0] - c[0]) * (p[1] - c[1]) - (a[1] - c[1]) * (p[0] - c[0])
        return d1 >= -1e-9 and d2 >= -1e-9 and d3 >= -1e-9
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        m = len(idx)
        for k in range(m):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % m]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 1e-9:
                continue
            if any(inside(a, b, c, poly[j]) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append([a, b, c])
            idx.pop(k)
            break
        else:
            break
    if len(idx) == 3:
        tris.append([poly[j] for j in idx])
    return tris


def _convex_parts(poly):
    """단순 다각형(반시계)을 볼록 조각들로: 삼각형으로 나눈 뒤 이웃 조각을 합쳐도 볼록이면 합친다(Hertel-Mehlhorn, 결정적)."""
    parts = [list(t) for t in _triangulate(poly)]

    def is_convex(pl):
        n = len(pl)
        for i in range(n):
            a, b, c = pl[i - 1], pl[i], pl[(i + 1) % n]
            if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) < -1e-9:
                return False
        return True

    def merge(p, q):
        """p 의 변 (a,b) 와 q 의 변 (b,a) 가 같으면 합친 고리, 아니면 None."""
        for i in range(len(p)):
            a, b = p[i], p[(i + 1) % len(p)]
            for j in range(len(q)):
                if q[j] == b and q[(j + 1) % len(q)] == a:
                    ring = p[:i + 1] + [q[(j + k) % len(q)] for k in range(2, len(q))] + p[i + 1:]
                    return ring
        return None
    changed = True
    while changed:
        changed = False
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                m = merge(parts[i], parts[j])
                if m is not None and is_convex(m):
                    parts[i] = _drop_collinear(m)
                    parts.pop(j)
                    changed = True
                    break
            if changed:
                break
    return parts


def _drop_collinear(ring):
    out = list(ring)
    i = 0
    while len(out) > 3 and i < len(out):
        a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
        cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        dt = (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1])
        la = sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
        lc = sqrt((c[0] - b[0]) ** 2 + (c[1] - b[1]) ** 2)
        if la < SNAP or lc < SNAP or (abs(cr) <= 1e-9 * la * lc + 1e-12 and dt > 0):
            out.pop(i)
            i = max(i - 1, 0)
        else:
            i += 1
    return out


def _plane(e, k, z0):
    """변 e(시작점, 단위 방향, 안쪽 법선, 길이)와 tan k 의 경사면 z = a x + b y + c."""
    p, _u, nv, _ = e
    return (k * nv[0], k * nv[1], z0 - k * (nv[0] * p[0] + nv[1] * p[1]))


def _validity(ed, ks, planes, o):
    """변마다 그 경사면이 유효한 곳의 반평면들 [(A,B,C): A x + B y + C >= 0]. 박공 변은 None."""
    n = len(ed)
    out = []
    for i in range(n):
        if ks[i] <= 0.0:
            out.append(None)
            continue
        p, _u, nv, _ = ed[i]
        # 변 뒤쪽(바깥)으로는 처마 내밀기만큼만 유효하다. 오목 꼭짓점의 이웃이 더 가파르면 골이 이웃 처마 띠를 지나므로 그 비율만큼 더 둔다
        reach = 1.0
        for j, vtx in ((i - 1, i), ((i + 1) % n, (i + 1) % n)):
            if ks[j] > 0.0 and not _convex_vertex(ed, vtx):
                reach = max(reach, ks[j] / ks[i])
        hs = [(nv[0], nv[1], -(nv[0] * p[0] + nv[1] * p[1]) + o * reach + 1.0)]
        for j, vtx in ((i - 1, i), ((i + 1) % n, (i + 1) % n)):
            if ks[j] <= 0.0:
                continue   # 박공 이웃(볼록 꼭짓점만 허용)은 제약이 없다
            d = (planes[j][0] - planes[i][0], planes[j][1] - planes[i][1], planes[j][2] - planes[i][2])   # h_j - h_i
            hs.append(d if _convex_vertex(ed, vtx) else (-d[0], -d[1], -d[2]))
        out.append(hs)
    return out


def _vals(poly, h):
    return [h[0] * q[0] + h[1] * q[1] + h[2] for q in poly]


def _face_cells(i, parts, valid, planes, ks):
    """변 i 의 경사면이 지붕 윗면이 되는 곳: 볼록 조각들."""
    cells = []
    for pt in parts:
        c = pt
        for h in valid[i]:
            c = _clip(c, *h)
            if not _ok(c):
                break
        if _ok(c):
            cells.append(c)
    a_i = planes[i]
    for f in range(len(planes)):
        if f == i or ks[f] <= 0.0 or not cells:
            continue
        d = (planes[f][0] - a_i[0], planes[f][1] - a_i[1], planes[f][2] - a_i[2])   # h_f - h_i >= 0 이면 i 가 낮다
        flat = abs(d[0]) < 1e-12 and abs(d[1]) < 1e-12
        lose_all = flat and (d[2] < -1e-9 or (abs(d[2]) <= 1e-9 and f < i))   # 같은 평면이면 번호가 작은 변이 가진다
        if flat and not lose_all:
            continue
        new = []
        for c in cells:
            if not lose_all and min(_vals(c, d)) >= -1e-9:
                new.append(c)          # 조각 전체에서 i 가 낮다
                continue
            if any(max(_vals(c, h)) <= 1e-9 for h in valid[f]):
                new.append(c)          # 조각 전체에서 f 가 유효하지 않다
                continue
            rest = c
            for h in valid[f]:
                if min(_vals(rest, h)) >= -1e-9:
                    continue
                out = _clip(rest, -h[0], -h[1], -h[2])
                if _ok(out):
                    new.append(out)    # f 가 유효하지 않은 쪽은 남긴다
                rest = _clip(rest, *h)
                if not _ok(rest):
                    break
            if _ok(rest) and not lose_all:
                kept = _clip(rest, *d)
                if _ok(kept):
                    new.append(kept)
        cells = new
    return cells


class _Snap:
    """가까운 점(SNAP 안)을 한 점으로 맞춘다(먼저 들어온 점이 대표)."""

    def __init__(self, tol=SNAP):
        self.pts = []
        self.grid = {}
        self.tol = tol   # 외곽선 지붕(roofskel)은 좌표 크기에 맞춘 허용치를 준다(1mm 미만)

    def key(self, p):
        gx, gy = int(p[0] // 1.0), int(p[1] // 1.0)
        tol = self.tol
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k in self.grid.get((gx + dx, gy + dy), ()):
                    q = self.pts[k]
                    if abs(q[0] - p[0]) <= tol and abs(q[1] - p[1]) <= tol:
                        return k
        self.pts.append((p[0], p[1]))
        self.grid.setdefault((gx, gy), []).append(len(self.pts) - 1)
        return len(self.pts) - 1


def _on_seg(a, b, p) -> float | None:
    """p 가 선분 a-b 위(끝점 제외)에 있으면 매개변수 t, 아니면 None."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 <= 0.0:
        return None
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    if t <= 1e-9 or t >= 1.0 - 1e-9:
        return None
    cx, cy = a[0] + t * dx - p[0], a[1] + t * dy - p[1]
    return t if cx * cx + cy * cy <= ON_TOL * ON_TOL else None


def _split_edges(edges, snap):
    """방향 변 (ka, kb) 를 그 위에 놓인 다른 점들에서 나눈다(T 접점)."""
    out = []
    keys = sorted({k for e in edges for k in e})
    for ka, kb in edges:
        a, b = snap.pts[ka], snap.pts[kb]
        mids = []
        for k in keys:
            if k in (ka, kb):
                continue
            t = _on_seg(a, b, snap.pts[k])
            if t is not None:
                mids.append((t, k))
        chain = [ka] + [k for _, k in sorted(mids)] + [kb]
        out.extend((chain[j], chain[j + 1]) for j in range(len(chain) - 1) if chain[j] != chain[j + 1])
    return out


def _pseudo_angle(dx, dy) -> float:
    """atan2 대신 쓰는 단조 각도(0~4, 반시계로 증가)."""
    s = abs(dx) + abs(dy)
    if s == 0.0:
        return 0.0
    r = dx / s
    return (1.0 - r) if dy >= 0 else (3.0 + r)


def _loops(cells, snap):
    """같은 면의 볼록 조각들을 이어 붙인 바깥 고리들(반시계, 점은 snap 번호). 안쪽으로 감기는 고리가 있으면 None(면 안에 구멍)."""
    edges = []
    for c in cells:
        ks = [snap.key(q) for q in c]
        n = len(ks)
        edges.extend((ks[j], ks[(j + 1) % n]) for j in range(n) if ks[j] != ks[(j + 1) % n])
    edges = _split_edges(edges, snap)
    cnt = {}
    for e in edges:
        cnt[e] = cnt.get(e, 0) + 1
    left = []
    for e in edges:
        r = (e[1], e[0])
        if cnt.get(e, 0) <= 0:
            continue
        if cnt.get(r, 0) > 0:
            cnt[e] -= 1
            cnt[r] -= 1
            continue
        cnt[e] -= 1
        left.append(e)
    outs = {}
    for a, b in left:
        outs.setdefault(a, []).append(b)
    used = set()
    loops = []
    for start in sorted(outs):
        for first in sorted(outs[start]):
            if (start, first) in used:
                continue
            ring = [start]
            prev, cur = start, first
            used.add((start, first))
            guard = 0
            while cur != start and guard < 100000:
                guard += 1
                ring.append(cur)
                cand = [nx for nx in outs.get(cur, ()) if (cur, nx) not in used]
                if not cand:
                    return None
                pa, pb = snap.pts[prev], snap.pts[cur]
                din = _pseudo_angle(pb[0] - pa[0], pb[1] - pa[1])

                def turn(nx, pb=pb, din=din):
                    q = snap.pts[nx]
                    return (_pseudo_angle(q[0] - pb[0], q[1] - pb[1]) - din + 2.0) % 4.0   # 작을수록 시계 방향으로 꺾는다
                nx = min(cand, key=lambda k: (turn(k), k))
                used.add((cur, nx))
                prev, cur = cur, nx
            ring = _drop_collinear_keys(ring, snap)
            if len(ring) >= 3:
                if _area2([snap.pts[k] for k in ring]) <= 0.0:
                    return None
                loops.append(ring)
    return loops


def _drop_collinear_keys(ring, snap):
    """고리(snap 번호)에서 한 직선 위의 가운데 점을 뺀다(점이 snap 으로 서로 달라 좌표로 되찾는다)."""
    pts = [snap.pts[k] for k in ring]
    kept = set(_drop_collinear(pts))
    return [k for k, p in zip(ring, pts) if p in kept]


def _height(plane, p):
    return plane[0] * p[0] + plane[1] * p[1] + plane[2]


def _point_in(poly, p) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x > p[0]:
                inside = not inside
    return inside


@lru_cache(maxsize=256)
def _build(b, ks, o, z0):
    """형상을 만들거나 거절 이유 문자열을 돌려준다(튜플로만 이루어진 불변 값: 캐시해도 안전하다)."""
    ed = _edges(b)
    n = len(b)
    planes = [_plane(ed[i], ks[i], z0) if ks[i] > 0.0 else None for i in range(n)]
    outline = _outline(b, ed, o)
    if _area2(outline) <= 0.0 or not _simple(outline):
        return "처마를 내민 외곽이 꼬인다(처마 내밀기를 줄이거나 짧은 변을 늘려라)"
    for i in range(n):   # 내민 변이 거꾸로 뒤집히면 안 된다
        a, c = outline[i], outline[(i + 1) % n]
        u = ed[i][1]
        if (c[0] - a[0]) * u[0] + (c[1] - a[1]) * u[1] <= 0.0:
            return "처마를 내민 외곽이 꼬인다(처마 내밀기를 줄이거나 짧은 변을 늘려라)"
    valid = _validity(ed, ks, planes, o)
    parts = _convex_parts(outline)
    snap = _Snap()
    for q in outline:
        snap.key(q)
    faces = []   # (변 번호, 고리 snap 번호들)
    for i in range(n):
        if ks[i] <= 0.0:
            continue
        cells = _face_cells(i, parts, valid, planes, ks)
        if not cells:
            continue
        lps = _loops(cells, snap)
        if lps is None:
            return "이 외곽에서는 경사면을 이어 붙일 수 없다(면 안에 구멍이 생긴다: 지붕을 둘 이상으로 나눠라)"
        faces.extend((i, lp) for lp in lps)
    if not faces:
        return "경사면이 하나도 만들어지지 않는다"
    if len(faces) > FACES_MAX:
        return f"경사면이 {FACES_MAX}개를 넘는다(외곽을 단순하게 하라)"
    return _finish(faces, snap, planes, [e[2] for e in ed], outline)


def _finish(faces, snap, planes, normals, outline):
    """면 고리들(면마다 (변 번호, snap 번호 고리))을 검사하고 마무리한다: 면이 외곽을 빈틈없이 덮는가, 면 경계에서 높이가 이어지는가,
    용마루·추녀마루·골 분류, 윗면 바깥 둘레, 물매 화살표. planes[i]·normals[i](안쪽 법선)는 변 i 의 것. 거절이면 이유 문장(외곽선 지붕 roofskel 도 쓴다)."""
    n = len(outline)
    tot = sum(_area2([snap.pts[k] for k in lp]) for _, lp in faces)
    want = _area2(outline)
    if abs(tot - want) > 1e-6 * want + 2.0:
        return "이 외곽에서는 경사면이 지붕을 빈틈없이 덮지 못한다(지붕을 둘 이상으로 나눠라)"
    # 면 경계: 모든 고리의 변을 모든 점에서 나눈 뒤, 반대 방향 짝이 있으면 두 면의 높이가 같아야 하고 없으면 외곽 위여야 한다
    owner = {}
    allk = sorted({k for _, lp in faces for k in lp})
    for fi, (i, lp) in enumerate(faces):
        es = [(lp[j], lp[(j + 1) % len(lp)]) for j in range(len(lp))]
        for e in _split_edges_with(es, snap, allk):
            owner[e] = fi
    zat = {}
    inner = []
    for (ka, kb), fi in sorted(owner.items()):
        i = faces[fi][0]
        for k in (ka, kb):
            zat.setdefault(k, _height(planes[i], snap.pts[k]))
        fj = owner.get((kb, ka))
        if fj is None:
            a, c = snap.pts[ka], snap.pts[kb]
            m = ((a[0] + c[0]) / 2.0, (a[1] + c[1]) / 2.0)
            if not any(_dist_seg(outline[j], outline[(j + 1) % n], m) <= ON_TOL for j in range(n)):
                return "이 외곽에서는 경사면 사이에 틈이 생긴다(지붕을 둘 이상으로 나눠라)"
            continue
        j = faces[fj][0]
        for k in (ka, kb):
            if abs(_height(planes[i], snap.pts[k]) - _height(planes[j], snap.pts[k])) > Z_TOL:
                return "이 외곽에서는 경사면의 높이가 이어지지 않는다(지붕을 둘 이상으로 나눠라)"
        if fi < fj:
            inner.append((ka, kb, i, j))
    # 용마루(수평)·추녀마루(볼록 접힘)·골(오목 접힘)
    kinds = {"ridge": [], "hip": [], "valley": []}
    for ka, kb, i, j in inner:
        a, c = snap.pts[ka], snap.pts[kb]
        za, zc = _height(planes[i], a), _height(planes[i], c)
        if abs(za - zc) <= Z_TOL:
            kinds["ridge"].append((ka, kb))
            continue
        dx, dy = c[0] - a[0], c[1] - a[1]
        ln = sqrt(dx * dx + dy * dy)
        m = ((a[0] + c[0]) / 2.0 + dy / ln * 10.0, (a[1] + c[1]) / 2.0 - dx / ln * 10.0)   # 면 i(왼쪽)의 반대편(면 j 쪽)
        kinds["hip" if _height(planes[i], m) > _height(planes[j], m) else "valley"].append((ka, kb))

    def p3(k):
        q = snap.pts[k]
        return (q[0], q[1], zat[k])

    def chains(segs):
        """이어지고 한 직선 위인 선분들을 합친다."""
        segs = [tuple(sorted(s)) for s in segs]
        segs.sort()
        merged = True
        while merged:
            merged = False
            for x in range(len(segs)):
                for y in range(x + 1, len(segs)):
                    s, t = segs[x], segs[y]
                    common = set(s) & set(t)
                    if len(common) != 1:
                        continue
                    c = common.pop()
                    e0, e1 = (s[0] if s[1] == c else s[1]), (t[0] if t[1] == c else t[1])
                    A, B, C = snap.pts[e0], snap.pts[c], snap.pts[e1]
                    cr = (B[0] - A[0]) * (C[1] - B[1]) - (B[1] - A[1]) * (C[0] - B[0])
                    l1 = sqrt((B[0] - A[0]) ** 2 + (B[1] - A[1]) ** 2)
                    l2 = sqrt((C[0] - B[0]) ** 2 + (C[1] - B[1]) ** 2)
                    if abs(cr) <= 1e-9 * l1 * l2 + 1e-12 and abs(zat[e0] - zat[c] - (zat[c] - zat[e1]) * l1 / l2) <= Z_TOL:
                        segs[x] = tuple(sorted((e0, e1)))
                        segs.pop(y)
                        merged = True
                        break
                if merged:
                    break
        return tuple((p3(a), p3(c)) for a, c in sorted(segs))
    face3, pieces, arrows, fks = [], [], [], []
    for i, lp in faces:
        pl = planes[i]
        ring = [snap.pts[k] for k in lp]
        face3.append(tuple((q[0], q[1], _height(pl, q)) for q in ring))
        fks.append(i)
        cps = _convex_parts(ring)
        for cp in cps:
            pieces.append((tuple((q[0], q[1], _height(pl, q)) for q in cp), pl))
        a2 = _area2(ring)
        cx = sum((ring[j][0] + ring[(j + 1) % len(ring)][0]) * (ring[j][0] * ring[(j + 1) % len(ring)][1] - ring[(j + 1) % len(ring)][0] * ring[j][1])
                 for j in range(len(ring))) / (3.0 * a2)
        cy = sum((ring[j][1] + ring[(j + 1) % len(ring)][1]) * (ring[j][0] * ring[(j + 1) % len(ring)][1] - ring[(j + 1) % len(ring)][0] * ring[j][1])
                 for j in range(len(ring))) / (3.0 * a2)
        if not _point_in(ring, (cx, cy)):   # 오목한 면은 가장 큰 볼록 조각의 무게중심
            big = max(cps, key=_area2)
            cx, cy = sum(q[0] for q in big) / len(big), sum(q[1] for q in big) / len(big)
        nv = normals[i]
        arrows.append(((cx, cy), (-nv[0], -nv[1])))
    # 윗면 바깥 둘레: 내민 외곽의 꼭짓점과 그 변 위에 놓인 면 꼭짓점(박공 끝의 용마루 끝 등)
    perim = []
    for j in range(n):
        a, c = outline[j], outline[(j + 1) % n]
        ka = snap.key(a)
        mids = sorted((t, k) for k in allk if k != ka and (t := _on_seg(a, c, snap.pts[k])) is not None)
        for k in [ka] + [k for _, k in mids]:
            if k not in zat:
                return "이 외곽에서는 처마 둘레의 높이를 정할 수 없다"
            perim.append(p3(k))
    zl = min(q[2] for q in perim)
    zt = max(q[2] for fc in face3 for q in fc)
    return (tuple(face3), tuple(pieces), tuple(planes[i] for i in fks), tuple(fks), chains(kinds["ridge"]), chains(kinds["hip"]),
            chains(kinds["valley"]), tuple(perim), tuple(arrows), tuple(outline), zl, zt)


def _split_edges_with(edges, snap, keys):
    out = []
    for ka, kb in edges:
        a, b = snap.pts[ka], snap.pts[kb]
        mids = sorted((t, k) for k in keys if k not in (ka, kb) and (t := _on_seg(a, b, snap.pts[k])) is not None)
        chain = [ka] + [k for _, k in mids] + [kb]
        out.extend((chain[j], chain[j + 1]) for j in range(len(chain) - 1))
    return out


def _dist_seg(a, b, p) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 <= 0.0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    cx, cy = a[0] + t * dx - p[0], a[1] + t * dy - p[1]
    return sqrt(cx * cx + cy * cy)


def problem(b, slopes, ks, overhang, side_min) -> str | None:
    """외곽·변 물매가 지붕을 만들 수 있는가(안 되면 이유 문장). b 는 반시계 [x,y] 목록, slopes 는 변마다 도(0 = 박공), ks 는 그 tan."""
    n = len(b)
    if not 3 <= n <= POLY_PTS_MAX:
        return f"다각형 외곽은 꼭짓점 3~{POLY_PTS_MAX}개여야 한다"
    if not isinstance(slopes, (list, tuple)) or len(slopes) != n:
        return "slopes 는 외곽의 변 수(꼭짓점 수)만큼 있어야 한다"
    if _area2(b) <= 0:
        return "외곽은 반시계 방향이어야 한다"
    ed = _edges(b) if all(sqrt((b[(i + 1) % n][0] - b[i][0]) ** 2 + (b[(i + 1) % n][1] - b[i][1]) ** 2) > 0 for i in range(n)) else None
    if ed is None or any(e[3] < side_min for e in ed):
        return f"외곽의 변은 {side_min:g}mm 이상이어야 한다"
    for i in range(n):
        u0, u1 = ed[i - 1][1], ed[i][1]
        if abs(u0[0] * u1[1] - u0[1] * u1[0]) < 1e-3:
            return f"꼭짓점 {i} 의 두 변이 한 직선 위에 있다(그 꼭짓점을 빼라)"
    if not _simple(b):
        return "외곽이 스스로 겹친다"
    if not any(k > 0.0 for k in ks):
        return "경사 변(물매 > 0)이 하나 이상 있어야 한다"
    for i in range(n):
        if not _convex_vertex(ed, i) and (ks[i - 1] <= 0.0 or ks[i] <= 0.0):
            return f"오목 꼭짓점 {i} 에 박공 변(물매 0)이 붙어 있다(오목 꼭짓점의 두 변은 경사 변이어야 한다)"
    r = _build(tuple((float(q[0]), float(q[1])) for q in b), tuple(float(k) for k in ks), float(overhang), 0.0)
    return r if isinstance(r, str) else None


def geom(b, slopes, ks, overhang, base_offset) -> dict:
    """roof_geom 과 같은 꼴의 dict(새 목록이라 고쳐도 캐시는 그대로다). faces 는 면 고리(오목할 수 있다), pieces 는 [(볼록 다각형 3D, 평면)]."""
    r = _build(tuple((float(q[0]), float(q[1])) for q in b), tuple(float(k) for k in ks), float(overhang), float(base_offset))
    if isinstance(r, str):
        raise ValueError(r)
    face3, pieces, planes, fks, ridges, hips, valleys, perim, arrows, outline, zl, zt = r
    return {"faces": [list(fc) for fc in face3], "ridges": list(ridges), "hips": list(hips), "valleys": list(valleys), "perimeter": list(perim),
            "arrows": list(arrows), "outline": list(outline), "planes": list(planes), "pieces": [(list(fc), pl) for fc, pl in pieces],
            "fpitch": [float(slopes[i]) for i in fks], "zlow": zl, "ztop": zt}
