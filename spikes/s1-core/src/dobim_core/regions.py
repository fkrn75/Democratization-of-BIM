# SPDX-License-Identifier: MPL-2.0
"""벽 중심선으로 닫힌 영역(방)을 찾는 순수 평면 기하(space.from_walls 가 쓴다). ifcopenshell·엔진을 가져오지 않는다.

방법: 벽 축선들을 서로의 교점·T 접합점에서 잘라 평면 그래프를 만들고, 한쪽만 이어진 가지(막다른 벽)를 걷어낸 뒤, 반변(half-edge) 따라가기로 면을 얻는다.
왼쪽 면을 도는 것(반시계)이 방이고, 시계 방향으로 도는 것은 바깥 면이다. 결과는 입력 순서와 무관하다(벽을 키로 정렬하고 다각형은 가장 왼쪽-아래 꼭짓점에서 시작한다).
섬: `find_faces(..., islands=True)` 는 방마다 "방 안에 따로 떨어진 닫힌 벽 고리(다른 연결 성분)"의 바깥 둘레를 구멍(`holes`)으로 함께 낸다(15단계).
곡선 벽(W2 ARC-04)은 부르는 쪽이 조각 경계의 현들로 나눠 준다(arcgeom.center_segs). 섬이 가지(다리)로 이어진 아령 모양이면 바깥 둘레가 자기 접촉이라 구멍을 못 낸다.
"""
from __future__ import annotations

from math import atan2, hypot

SNAP = 1.0   # mm. 이 안의 점은 같은 점으로 본다


def _key(p):
    return (round(p[0] / SNAP), round(p[1] / SNAP))


def _split_points(segs):
    """각 선분(축선)을 나눌 매개변수 t 목록: 끝점과, 다른 선분과의 교점(교차·T 접합)."""
    n = len(segs)
    cuts = [[0.0, 1.0] for _ in range(n)]
    for i in range(n):
        (ax, ay), (bx, by) = segs[i][0], segs[i][1]
        dx, dy = bx - ax, by - ay
        for j in range(i + 1, n):
            (cx, cy), (ex, ey) = segs[j][0], segs[j][1]
            fx, fy = ex - cx, ey - cy
            den = dx * fy - dy * fx
            li, lj = hypot(dx, dy), hypot(fx, fy)
            if abs(den) > 1e-9 * li * lj:
                t = ((cx - ax) * fy - (cy - ay) * fx) / den
                u = ((cx - ax) * dy - (cy - ay) * dx) / den
                ti, tj = SNAP / li, SNAP / lj
                if -ti <= t <= 1 + ti and -tj <= u <= 1 + tj:
                    cuts[i].append(min(1.0, max(0.0, t)))
                    cuts[j].append(min(1.0, max(0.0, u)))
            else:
                # 평행: 겹치는 끝점들이 서로의 위에 놓이면 그 점으로 자른다(이어진 한 직선 위 벽들)
                for (px, py), owner, ox, oy, qx, qy in (((cx, cy), i, ax, ay, bx, by), ((ex, ey), i, ax, ay, bx, by)):
                    t = ((px - ox) * dx + (py - oy) * dy) / (li * li)
                    if -SNAP / li <= t <= 1 + SNAP / li and abs((px - ox) * dy - (py - oy) * dx) / li <= SNAP:
                        cuts[i].append(min(1.0, max(0.0, t)))
                for (px, py), owner, ox, oy in (((ax, ay), j, cx, cy), ((bx, by), j, cx, cy)):
                    t = ((px - ox) * fx + (py - oy) * fy) / (lj * lj)
                    if -SNAP / lj <= t <= 1 + SNAP / lj and abs((px - ox) * fy - (py - oy) * fx) / lj <= SNAP:
                        cuts[j].append(min(1.0, max(0.0, t)))
    return cuts


def _area2(poly):
    n = len(poly)
    return sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))


def _canonical(poly):
    """가장 왼쪽-아래 꼭짓점에서 시작하게 돌린다(결정적 표현)."""
    k = min(range(len(poly)), key=lambda i: (poly[i][0], poly[i][1]))
    return poly[k:] + poly[:k]


def _tidy(poly):
    """연속한 같은 점·공선(직진) 꼭짓점을 걷어낸다."""
    pts = [p for i, p in enumerate(poly) if i == 0 or hypot(p[0] - poly[i - 1][0], p[1] - poly[i - 1][1]) > SNAP / 2]
    if len(pts) > 1 and hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= SNAP / 2:
        pts.pop()
    out = pts
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            dt = (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1])
            if abs(cr) <= 1e-6 * max(1.0, abs(dt)) and dt > 0:
                del out[i]
                changed = True
                break
    return out


def _clean_ring(poly, ed):
    """공선 꼭짓점을 걷어내되 변의 두께 목록을 맞춘다(각 꼭짓점에서 직진이고 앞 변과 같은 벽이면 같은 변). 가장 왼쪽-아래 꼭짓점에서 시작한다. 3개 미만이면 None."""
    keep = []
    n = len(poly)
    for i in range(n):
        a, b, c = poly[i - 1], poly[i], poly[(i + 1) % n]
        cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        dt = (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1])
        straight = abs(cr) <= 1e-6 * max(1.0, abs(dt)) and dt > 0 and ed[i - 1] == ed[i]
        if not straight:
            keep.append(i)
    if len(keep) < 3:
        return None
    poly2 = [poly[i] for i in keep]
    ed2 = [ed[i] for i in keep]   # 변 = 꼭짓점 i 에서 다음 꼭짓점까지: 남긴 꼭짓점에서 출발하는 변의 벽
    k0 = min(range(len(poly2)), key=lambda i: (poly2[i][0], poly2[i][1]))
    return {"center": poly2[k0:] + poly2[:k0], "edges": ed2[k0:] + ed2[:k0]}


def find_faces(walls, islands: bool = False):
    """walls = [(키, (x1,y1), (x2,y2), 두께), ...] -> 방 목록 [{"center": 중심선 다각형(반시계), "edges": [(키, 두께), ...](다각형 변마다)}, ...].
    방은 (가장 왼쪽-아래 꼭짓점, 면적) 순으로 정렬한다.
    islands=True 이면 방마다 "holes"(방 안의 섬: 이어지지 않은 다른 벽 덩어리의 바깥 둘레 = 시계 방향 고리 {"center","edges"} 목록)를 더한다.
    바깥 둘레는 반대 방향(시계)으로 도는 면이다(왼쪽이 방 쪽이라 inner_polygon(..., outer=True) 가 섬의 바깥 면으로 넓힌다). 섬 안의 섬은 가장 안쪽 방의 구멍이다."""
    walls = sorted(walls, key=lambda w: w[0])
    segs = [(w[1], w[2], w[0], w[3]) for w in walls if hypot(w[2][0] - w[1][0], w[2][1] - w[1][1]) > SNAP]
    cuts = _split_points(segs)
    pos: dict = {}      # 격자 키 -> 대표 좌표(처음 본 것: 정렬된 벽 순서라 결정적)
    edges: dict = {}    # (격자 키 u, 격자 키 v) 정규화 -> (벽 키, 두께). 같은 두 점을 잇는 벽이 둘이면 앞선 키를 쓴다
    for (a, b, key, th), ts in zip(segs, cuts):
        ts = sorted(set(round(t, 9) for t in ts))
        pts = []
        for t in ts:
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            k = _key(p)
            pos.setdefault(k, p)
            if not pts or pts[-1] != k:
                pts.append(k)
        for u, v in zip(pts, pts[1:]):
            if u != v:
                edges.setdefault((min(u, v), max(u, v)), (key, th))
    adj: dict = {}
    for (u, v) in edges:
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    # 막다른 가지를 걷어낸다
    leaf = [k for k, nb in adj.items() if len(nb) <= 1]
    while leaf:
        k = leaf.pop()
        for nb in list(adj.get(k, ())):
            adj[nb].discard(k)
            edges.pop((min(k, nb), max(k, nb)), None)
            if len(adj[nb]) <= 1:
                leaf.append(nb)
        adj.pop(k, None)
    order = {}
    for k, nb in adj.items():
        px, py = pos[k]
        order[k] = sorted(nb, key=lambda w: (atan2(pos[w][1] - py, pos[w][0] - px), w))
    comp: dict = {}     # 노드 -> 연결 성분 번호(정렬된 노드 순으로 매겨 결정적)
    ncomp = 0
    for u in sorted(adj):
        if u in comp:
            continue
        comp[u] = ncomp
        todo = [u]
        while todo:
            x = todo.pop()
            for y in adj[x]:
                if y not in comp:
                    comp[y] = ncomp
                    todo.append(y)
        ncomp += 1
    seen = set()
    faces = []
    outers: dict = {}   # 연결 성분 -> 바깥 둘레(시계 방향) 고리
    for u in sorted(adj):
        for v in order[u]:
            if (u, v) in seen:
                continue
            ring, a, b = [], u, v
            while (a, b) not in seen:
                seen.add((a, b))
                ring.append((a, b))
                nb = order[b]
                w = nb[(nb.index(a) - 1) % len(nb)]
                a, b = b, w
            poly = [pos[x] for x, _ in ring]
            if len(poly) >= 3 and _area2(poly) > 1e-6:
                faces.append({"center": poly, "edges": [edges[(min(x, y), max(x, y))] for x, y in ring], "comp": comp[u]})
            elif islands and len(poly) >= 3 and _area2(poly) < -1e-6:
                outers.setdefault(comp[u], {"center": poly, "edges": [edges[(min(x, y), max(x, y))] for x, y in ring]})
    out = []
    for f in faces:
        # 공선 꼭짓점을 걷어내되 변의 두께 목록을 맞춘다: 각 꼭짓점(변의 시작점)에서 직진이면 앞 변과 같은 변으로 본다
        cl = _clean_ring(f["center"], f["edges"])
        if cl is None:
            continue
        cl["comp"] = f["comp"]
        out.append(cl)
    out.sort(key=lambda f: (f["center"][0][0], f["center"][0][1], _area2(f["center"])))
    if not islands:
        for f in out:
            f.pop("comp", None)
        return out
    rings = {}
    for c, r in sorted(outers.items()):
        cl = _clean_ring(r["center"], r["edges"])
        if cl is not None:
            rings[c] = cl
    for f in out:
        # 이 방 안(다른 연결 성분)에 있는 섬의 바깥 둘레 후보. 후보 안에 또 다른 후보가 있으면 그것은 후보 안쪽 방의 구멍이다
        cand = [(c, r) for c, r in sorted(rings.items()) if c != f["comp"] and point_in(f["center"], r["center"][0])]
        f["holes"] = [r for c, r in cand if not any(c2 != c and point_in(r2["center"], r["center"][0]) for c2, r2 in cand)]
    for f in out:
        f.pop("comp", None)
    return out


def point_in(poly, pt) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) / (b[1] - a[1]) * (b[0] - a[0]):
            inside = not inside
    return inside


def inner_polygon(face, outer: bool = False):
    """방의 안쪽 둘레: 중심선 변마다 벽 두께의 절반만큼 안쪽(왼쪽)으로 옮긴 선들의 교점. 안 되면 None(자기 교차·퇴화).
    outer=True 는 섬의 바깥 둘레(시계 방향 고리)용이다: 왼쪽이 섬 밖이라 섬 벽의 바깥 면으로 넓혀지고, 결과는 반시계로 뒤집어 돌려준다."""
    poly, ed = face["center"], face["edges"]
    n = len(poly)
    lines = []
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ln = hypot(b[0] - a[0], b[1] - a[1])
        ux, uy = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
        h = ed[i][1] / 2.0
        lines.append(((a[0] - uy * h, a[1] + ux * h), (ux, uy)))
    out = []
    for i in range(n):
        (p0, d0), (p1, d1) = lines[i - 1], lines[i]   # 꼭짓점 i 에서 만나는 앞 변·뒤 변
        den = d0[0] * d1[1] - d0[1] * d1[0]
        if abs(den) < 1e-9:   # 평행: 두께가 다른 공선 벽의 이음. 앞 변 끝과 뒤 변 시작 두 점을 모두 둔다
            c = poly[i]
            out.append((c[0] + (p0[0] - (poly[i - 1][0])), c[1] + (p0[1] - (poly[i - 1][1]))))
            out.append(p1)
            continue
        t = ((p1[0] - p0[0]) * d1[1] - (p1[1] - p0[1]) * d1[0]) / den
        out.append((p0[0] + d0[0] * t, p0[1] + d0[1] * t))
    out = _tidy(out)
    if outer:
        out = out[::-1]
    if len(out) < 3 or _area2(out) <= 1e-6:
        return None
    return _canonical(out)
