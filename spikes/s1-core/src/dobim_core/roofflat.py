# SPDX-License-Identifier: MPL-2.0
"""평지붕(roof form "flat", W2 열여섯 번째 PR, ARC-19·ARC-21 잔여: 물매 0 지붕)의 순수 형상.

사용자 결정: 평지붕 = 물매 0 지붕(배수 물매는 만들지 않는다). 윗면은 수평 평면 z = base_offset(레벨 기준)이고 두께는 다른 지붕처럼 윗면에서
수직 아래로 잰다. 외곽(처마 선)은 다각형(3~128 꼭짓점, 변 1mm 이상, 오목 가능)이고 변마다 선택 볼록도(bulges: tan(중심각/4), 양수 = 반시계 호,
지붕 개구 PR #89 와 같은 규약)로 원호를 섞을 수 있다. 호는 한 바퀴 32 현으로 펼친다(개구·솔리드 기본 해상도와 같다).
처마는 외곽 변마다 내밀기(overhang 또는 변마다 overhangs, 호 변은 그 호의 모든 현)만큼 바깥으로 평행 이동한 선의 교점으로 내민다.

roof_geom 과 같은 꼴의 dict 를 돌려준다(도면·IFC·벽 상단 붙이기·개구·수량이 그대로 쓴다). 면은 하나(처마를 내민 윤곽, 오목할 수 있다)이고
pieces 는 그 볼록 조각들(가림·절단 띠), 용마루·추녀마루·골·물매 화살표는 없다. 더해서
  "eave_loop": 펼친 외곽(처마 선) 고리, "eave_src": 처마 윤곽의 변(= 펼친 외곽의 현)마다 원래 외곽 변 번호,
  "smooth": 같은 호의 두 현이 만나는 윤곽 꼭짓점 번호들(입면·단면에서 수직 모서리를 긋지 않는다: 원호는 매끈하게).
libm 을 쓰지 않는다(호는 solidops 의 사칙연산 sincos). 이 모듈은 kinds 를 import 하지 않는다(kinds 가 이 모듈을 쓴다).
"""
from __future__ import annotations

from functools import lru_cache
from math import sqrt

from dobim_core import roofpoly as RP

PTS_MAX = 128          # 외곽 꼭짓점 수 상한(외곽선 지붕과 같다)
LOOP_MAX = 256         # 펼친 외곽(호를 현으로) 점 수 상한(슬래브 경계와 같다)
SIDE_MIN = 1.0         # 외곽 변의 최소 길이 mm(외곽선 지붕과 같다)


def loop(boundary, bulges) -> tuple:
    """((펼친 고리 점들), (현마다 원래 변 번호)). 볼록도가 없으면 외곽 그대로. 점은 소수 6자리(개구 opening_loop 와 같은 규칙)."""
    b = [(float(q[0]), float(q[1])) for q in boundary]
    n = len(b)
    if not bulges or not any(bulges):
        return tuple(b), tuple(range(n))
    from dobim_core import solidops as SOP   # 늦게 읽는다(순수 모듈)
    pts, src = [], []
    for i in range(n):
        a = b[i]
        pts.append(a)
        src.append(i)
        if bulges[i]:
            for q in SOP.bulge_arc(a, b[(i + 1) % n], float(bulges[i]), None):
                pts.append((round(q[0], 6) + 0.0, round(q[1], 6) + 0.0))
                src.append(i)
    return tuple(pts), tuple(src)


def _area2(poly) -> float:
    n = len(poly)
    return sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))


def _offset(lp, ds):
    """고리 lp(반시계)의 변 k 를 바깥으로 ds[k] 만큼 평행 이동한 선들의 교점 고리. 이웃 두 변이 평행하면(같은 직선) 꼭짓점을 그대로 민다.
    거리가 모두 0 이면 고리 그대로. 내민 변이 거꾸로 뒤집히면(짧은 변이 사라지는 큰 내밀기) 문장을 돌려준다."""
    if not any(d > 0.0 for d in ds):
        return list(lp)
    m = len(lp)
    ed = []
    for k in range(m):
        p, q = lp[k], lp[(k + 1) % m]
        dx, dy = q[0] - p[0], q[1] - p[1]
        ln = sqrt(dx * dx + dy * dy)
        u = (dx / ln, dy / ln)
        nv = (u[1], -u[0])   # 바깥 법선(반시계 고리의 오른쪽)
        ed.append((p, u, nv, ds[k]))
    out = []
    for k in range(m):
        (p0, u0, n0, d0), (p1, u1, n1, d1) = ed[k - 1], ed[k]
        a0 = (p0[0] + n0[0] * d0, p0[1] + n0[1] * d0)
        a1 = (p1[0] + n1[0] * d1, p1[1] + n1[1] * d1)
        det = u0[0] * u1[1] - u0[1] * u1[0]
        if abs(det) < 1e-12:
            if abs(d0 - d1) > 1e-9:
                return f"꼭짓점 {k} 의 두 변이 한 직선 위에 있는데 내밀기가 다르다"
            out.append(a1)
            continue
        s = ((a1[0] - a0[0]) * u1[1] - (a1[1] - a0[1]) * u1[0]) / det
        out.append((a0[0] + s * u0[0], a0[1] + s * u0[1]))
    for k in range(m):   # 내민 변이 원래 변과 같은 방향이어야 한다
        p, q = out[k], out[(k + 1) % m]
        u = ed[k][1]
        if (q[0] - p[0]) * u[0] + (q[1] - p[1]) * u[1] <= 1e-6:
            return f"처마 내밀기가 커서 외곽 변 근처의 처마가 뒤집힌다(현 {k})"
    return out


@lru_cache(maxsize=512)
def _build(b, bl, os, z0):
    """(면 dict 재료) 또는 이유 문장. 입력은 튜플(캐시 열쇠)."""
    from dobim_core import kinds as K   # 늦게 읽는다(kinds 가 이 모듈을 쓴다): 다각형 검사(슬래브 경계 규칙)만 쓴다
    n = len(b)
    if not 3 <= n <= PTS_MAX:
        return f"외곽 꼭짓점은 3~{PTS_MAX}개다"
    for i in range(n):
        p, q = b[i], b[(i + 1) % n]
        if sqrt((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) < SIDE_MIN:
            return f"외곽 변 {i} 의 길이가 {SIDE_MIN:g}mm 보다 짧다"
    lp, src = loop(b, bl)
    if len(lp) > LOOP_MAX:
        return f"호를 현으로 펼친 외곽 점이 {LOOP_MAX}개를 넘는다({len(lp)}개): 호를 줄이거나 지붕을 나눈다"
    if why := K.polygon_problem(list(lp)):
        return f"외곽: {why}"
    if _area2(lp) <= 0.0:
        return "외곽(호를 펼친 고리)이 시계 방향이다"
    ol = _offset(lp, [os[s] for s in src])
    if isinstance(ol, str):
        return ol
    if why := K.polygon_problem(ol):
        return f"처마를 내민 윤곽: {why}"
    if _area2(ol) <= 0.0:
        return "처마를 내민 윤곽이 뒤집힌다"
    m = len(lp)
    smooth = tuple(k for k in range(m) if src[k] == src[k - 1] and bl and bl[src[k]])   # 같은 호의 두 현 사이 꼭짓점
    face = [(q[0], q[1], z0) for q in ol]
    parts = RP._convex_parts(RP._drop_collinear([tuple(q) for q in ol]))
    if abs(sum(_area2(pc) for pc in parts) - _area2(ol)) > 1e-6 * max(1.0, abs(_area2(ol))):
        return "처마 윤곽을 볼록 조각으로 나누지 못했다"
    pl = (0.0, 0.0, z0)
    return (tuple(face), tuple(tuple((q[0], q[1], z0) for q in pc) for pc in parts), pl, tuple(lp), tuple(src), tuple(ol), smooth)


def problem(boundary, bulges, overhangs) -> str | None:
    """평지붕 외곽·볼록도·내밀기가 형상을 만들 수 있는가(안 되면 이유 문장)."""
    r = _build(tuple((float(q[0]), float(q[1])) for q in boundary), tuple(float(v) for v in bulges) if bulges else (),
               tuple(float(o) for o in overhangs), 0.0)
    return r if isinstance(r, str) else None


def geom(boundary, bulges, overhangs, base_offset) -> dict:
    """roof_geom 과 같은 꼴의 dict(새 목록이라 고쳐도 캐시는 그대로다)."""
    r = _build(tuple((float(q[0]), float(q[1])) for q in boundary), tuple(float(v) for v in bulges) if bulges else (),
               tuple(float(o) for o in overhangs), float(base_offset))
    if isinstance(r, str):
        raise ValueError(r)
    face, parts, pl, lp, src, ol, smooth = r
    z0 = float(base_offset)
    return {"faces": [list(face)], "ridges": [], "hips": [], "valleys": [], "perimeter": list(face), "arrows": [], "outline": list(ol), "planes": [pl],
            "pieces": [(list(pc), pl) for pc in parts], "fpitch": [0.0], "zlow": z0, "ztop": z0,
            "eave_loop": list(lp), "eave_src": list(src), "smooth": list(smooth)}
