# SPDX-License-Identifier: MPL-2.0
"""지붕·천장 독립 개구의 순수 기하(W2 열네 번째 PR, ARC-19·ARC-21·ARC-34 잔여: 지붕·천장·슬래브 개구).

독립 개구 객체(opening)가 경사 지붕(roof)을 뚫는 방식은 둘이다.
  수직(vertical·shaft): 평면 경계 다각형을 수직으로 관통한다. 지붕 면이 여럿이면(용마루를 걸친 개구) 겹치는 면마다 뚫는다.
  면(face): 상용 도구의 면 개구(면에 수직). 평면 경계는 면 윗면 위의 스케치를 평면에 투영한 것이고, 구멍 벽은 면에 수직이다.
    지붕 두께는 수직으로 잰 값이라(roof_geom·도면·IFC 가 같다) 윗면 점 P 에서 면 법선을 따라 아랫면까지 내려간 점은
    평면에서 t·(a, b)/(1 + a² + b²) 만큼 밀린다(면 z = a x + b y + c). 그 밀린 고리가 아랫면 구멍이다.
도면(단면 절단 띠·입면 모서리·평면 윤곽)·IFC(면 IfcSlab 마다 IfcOpeningElement)·일람표(경사면 넓이)가 이 값을 같이 쓴다.
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from math import sqrt

from dobim_core import kinds as K
from dobim_core import solids as SO

AREA_EPS = 1.0          # 이 넓이(mm²)보다 작게 겹치면 겹치지 않은 것으로 본다(면 경계에 닿기만 한 개구)
VOID_MARGIN = 10.0      # IFC 개구 솔리드를 호스트보다 위아래로 더 길게(경계면과 겹치지 않게, 슬래브 구멍과 같은 값)


def area2(poly) -> float:
    n = len(poly)
    return sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))


def _clip_half(poly, a, b, k):
    """다각형을 a x + b y + k >= 0 쪽으로 자른다(Sutherland-Hodgman: 오목한 대상도 넓이는 맞다)."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        fp, fq = a * p[0] + b * p[1] + k, a * q[0] + b * q[1] + k
        if fp >= 0.0:
            out.append(p)
        if (fp >= 0.0) != (fq >= 0.0):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def clip_convex(subj, clip):
    """대상 다각형을 볼록 다각형(반시계) clip 안쪽으로 자른다."""
    out = [(float(q[0]), float(q[1])) for q in subj]
    n = len(clip)
    for i in range(n):
        if len(out) < 3:
            return []
        a, b = clip[i], clip[(i + 1) % n]
        # 왼쪽(안쪽) = (b - a) x (p - a) >= 0
        out = _clip_half(out, -(b[1] - a[1]), b[0] - a[0], (b[1] - a[1]) * a[0] - (b[0] - a[0]) * a[1])
    return out


def overlap_area(loop, poly) -> float:
    """loop(단순 다각형)과 poly(단순 다각형, 오목 가능)가 겹치는 넓이: poly 를 삼각형으로 나눠 각각으로 자른 넓이의 합."""
    tot = 0.0
    for tri in SO.triangulate(SO.ccw([(q[0], q[1]) for q in poly])):
        c = clip_convex(loop, tri)
        if len(c) >= 3:
            tot += abs(area2(c)) / 2.0
    return tot


def inside(loop, poly) -> bool:
    """loop 가 poly 안에 다 드는가(겹친 넓이 = loop 넓이, 상대 1e-6)."""
    a = abs(area2(loop)) / 2.0
    return a > 0.0 and overlap_area(loop, poly) >= a * (1.0 - 1e-6) - 1e-6


def centroid(loop):
    """다각형 넓이 중심."""
    n = len(loop)
    a = cx = cy = 0.0
    for i in range(n):
        p, q = loop[i], loop[(i + 1) % n]
        c = p[0] * q[1] - q[0] * p[1]
        a += c
        cx += (p[0] + q[0]) * c
        cy += (p[1] + q[1]) * c
    if abs(a) < 1e-12:
        return (sum(q[0] for q in loop) / n, sum(q[1] for q in loop) / n)
    return (cx / (3.0 * a), cy / (3.0 * a))


def face_xy(fc):
    return [(q[0], q[1]) for q in fc]


def face_of(gm, loop):
    """면 개구가 놓인 지붕 면 번호(경계 넓이 중심이 든 첫 면) 또는 None."""
    c = centroid(loop)
    for i, fc in enumerate(gm["faces"]):
        if K.point_in_polygon(c, face_xy(fc)) >= 0:
            return i
    return None


def faces_hit(gm, loop) -> tuple:
    """수직 개구가 겹치는 지붕 면 번호들(겹친 넓이 > AREA_EPS)."""
    return tuple(i for i, fc in enumerate(gm["faces"]) if overlap_area(loop, face_xy(fc)) > AREA_EPS)


def shift(plane, t: float):
    """면 개구의 아랫면 고리가 평면에서 밀리는 양 t·(a, b)/(1 + a² + b²)."""
    a, b = plane[0], plane[1]
    k = t / (1.0 + a * a + b * b)
    return (a * k, b * k)


def shifted(loop, d):
    return tuple((q[0] + d[0], q[1] + d[1]) for q in loop)


def roof_void(gm, t: float, mode: str, loop):
    """지붕 개구 하나의 형상 dict 또는 None(면을 못 찾은 면 개구):
      {"faces": 뚫는 면 번호들, "top": 윗면 구멍 평면 고리, "bot": 아랫면 구멍 평면 고리, "plane": 면 개구의 면 평면(수직이면 None)}."""
    loop = tuple((float(q[0]), float(q[1])) for q in loop)
    if mode == "face":
        i = face_of(gm, loop)
        if i is None:
            return None
        pl = gm["planes"][i]
        return {"faces": (i,), "top": loop, "bot": shifted(loop, shift(pl, t)), "plane": tuple(pl)}
    return {"faces": faces_hit(gm, loop), "top": loop, "bot": loop, "plane": None}


def face_area(gm, i: int, loops) -> float:
    """면 i 에서 구멍 고리들(평면)이 차지하는 경사면 넓이(평면 겹침 넓이 x sqrt(1 + 기울기²))."""
    a, b, _c = gm["planes"][i]
    xy = face_xy(gm["faces"][i])
    return sum(overlap_area(lp, xy) for lp in loops) * sqrt(1.0 + a * a + b * b)


# ---------------------------------------------------------------- 도면 도우미
def seg_outside(a, b, loops) -> list:
    """평면 선분 a-b 에서 구멍 고리들 밖에 있는 매개변수 구간 [(t0, t1), ...](고리 경계 위는 밖으로 본다)."""
    ts = {0.0, 1.0}
    dx, dy = b[0] - a[0], b[1] - a[1]
    for lp in loops:
        n = len(lp)
        for i in range(n):
            p, q = lp[i], lp[(i + 1) % n]
            ex, ey = q[0] - p[0], q[1] - p[1]
            den = dx * ey - dy * ex
            if abs(den) < 1e-12:
                continue
            t = ((p[0] - a[0]) * ey - (p[1] - a[1]) * ex) / den
            u = ((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / den
            if 0.0 < t < 1.0 and -1e-9 <= u <= 1.0 + 1e-9:
                ts.add(t)
    ts = sorted(ts)
    out = []
    for t0, t1 in zip(ts, ts[1:]):
        if t1 - t0 < 1e-9:
            continue
        m = ((t0 + t1) / 2.0)
        pt = (a[0] + dx * m, a[1] + dy * m)
        if any(K.point_in_polygon(pt, lp) == 1 for lp in loops):
            continue
        if out and abs(out[-1][1] - t0) < 1e-12:
            out[-1] = (out[-1][0], t1)
        else:
            out.append((t0, t1))
    return out


def line_inside(loop_sw, near: float) -> list:
    """(s, w) 좌표 고리가 직선 w = near 위에서 차지하는 s 구간들 [(s0, s1), ...](짝홀)."""
    xs = []
    n = len(loop_sw)
    for i in range(n):
        p, q = loop_sw[i], loop_sw[(i + 1) % n]
        if (p[1] <= near) != (q[1] <= near):
            xs.append(p[0] + (near - p[1]) / (q[1] - p[1]) * (q[0] - p[0]))
    xs.sort()
    return [(xs[j], xs[j + 1]) for j in range(0, len(xs) - 1, 2) if xs[j + 1] - xs[j] > 1e-9]


def minus(iv, holes) -> list:
    """구간 iv=(a, b) 에서 구멍 구간들을 뺀 구간들."""
    out = [iv]
    for h0, h1 in sorted(holes):
        nxt = []
        for a, b in out:
            if h1 <= a or h0 >= b:
                nxt.append((a, b))
                continue
            if h0 > a:
                nxt.append((a, h0))
            if h1 < b:
                nxt.append((h1, b))
        out = nxt
    return [(a, b) for a, b in out if b - a > 1e-6]


def seg_in_convex(a, b, poly):
    """평면 선분 a-b 가 볼록 다각형(어느 방향이든) 안에 드는 매개변수 구간 (t0, t1) 또는 None(Cyrus-Beck)."""
    sg = 1.0 if area2(poly) >= 0.0 else -1.0
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        ex, ey = q[0] - p[0], q[1] - p[1]
        f0 = sg * (ex * (a[1] - p[1]) - ey * (a[0] - p[0]))   # 안쪽이면 >= 0
        fd = sg * (ex * dy - ey * dx)
        if abs(fd) < 1e-15:
            if f0 < -1e-9:
                return None
            continue
        t = -f0 / fd
        if fd > 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t1 - t0 < 1e-12:
            return None
    return (t0, t1)


CORNER_COS = 0.94    # 구멍 벽 모서리를 긋는 꼭짓점: 변 방향이 20도(cos 20 = 0.9397) 넘게 꺾이는 곳. 원호를 펼친 꼭짓점(한 바퀴 32 현 = 11.25도)은 매끈한 벽이라 긋지 않는다


def corners(loop) -> list:
    """고리에서 변 방향이 20도 넘게 꺾이는 꼭짓점 번호들."""
    n = len(loop)
    out = []
    for i in range(n):
        a, p, b = loop[i - 1], loop[i], loop[(i + 1) % n]
        ux, uy, vx, vy = p[0] - a[0], p[1] - a[1], b[0] - p[0], b[1] - p[1]
        lu, lv = sqrt(ux * ux + uy * uy), sqrt(vx * vx + vy * vy)
        if lu < 1e-9 or lv < 1e-9 or (ux * vx + uy * vy) / (lu * lv) < CORNER_COS:
            out.append(i)
    return out


def void_edges3(voids, pieces, t: float) -> list:
    """지붕 개구들의 3D 모서리 [(a, b), ...]: voids = ((모드, 윗면 고리, 아랫면 고리, 면 평면 또는 None), ...), pieces = ((볼록 조각 3D, 평면), ...)(절대 z).
    면 개구는 그 면 평면 위의 윗면 고리·두께만큼 아래의 밀린 아랫면 고리·꼭짓점마다 면에 수직인 구멍 벽 모서리,
    수직 개구는 고리 변을 볼록 조각마다 잘라 그 조각 평면 높이(윗면)와 두께만큼 아래(아랫면), 꼭짓점마다 수직 모서리.
    구멍 벽 모서리는 꺾이는 꼭짓점(corners)에만 긋는다(원호 구멍의 벽은 매끈하다)."""
    out = []
    for _m, top, bot, pl in voids:
        n = len(top)
        cn = set(corners(top))
        if pl is not None:
            a, b, c = pl
            for i in range(n):
                p, q = top[i], top[(i + 1) % n]
                p2, q2 = bot[i], bot[(i + 1) % n]
                pt, qt = (p[0], p[1], a * p[0] + b * p[1] + c), (q[0], q[1], a * q[0] + b * q[1] + c)
                out.append((pt, qt))
                out.append(((p2[0], p2[1], a * p2[0] + b * p2[1] + c - t), (q2[0], q2[1], a * q2[0] + b * q2[1] + c - t)))
                if i in cn:
                    out.append((pt, (p2[0], p2[1], a * p2[0] + b * p2[1] + c - t)))
            continue
        for i in range(n):
            p, q = top[i], top[(i + 1) % n]
            for fc, (a, b, c) in pieces:
                iv = seg_in_convex(p, q, [(r[0], r[1]) for r in fc])
                if iv is None:
                    continue
                s0 = (p[0] + iv[0] * (q[0] - p[0]), p[1] + iv[0] * (q[1] - p[1]))
                s1 = (p[0] + iv[1] * (q[0] - p[0]), p[1] + iv[1] * (q[1] - p[1]))
                z0, z1 = a * s0[0] + b * s0[1] + c, a * s1[0] + b * s1[1] + c
                out.append(((s0[0], s0[1], z0), (s1[0], s1[1], z1)))
                out.append(((s0[0], s0[1], z0 - t), (s1[0], s1[1], z1 - t)))
        for p in (top[i] for i in sorted(cn)):
            for fc, (a, b, c) in pieces:
                if K.point_in_polygon(p, [(r[0], r[1]) for r in fc]) >= 0:
                    z = a * p[0] + b * p[1] + c
                    out.append(((p[0], p[1], z), (p[0], p[1], z - t)))
                    break
    return out
