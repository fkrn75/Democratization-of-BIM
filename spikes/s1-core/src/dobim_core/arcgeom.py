# SPDX-License-Identifier: MPL-2.0
"""곡선(원호) 벽의 축 기하(W2 ARC-04). 순수 함수: 시각·난수·파일 접근 금지, libm 의 sin·cos·atan 대신 사칙연산과 sqrt 만 쓴다
(플랫폼마다 끝자리가 다를 수 있어 도면·IFC 해시가 흔들린다: queries.py 와 같은 규칙).

벽 params: axis = [x1, y1, x2, y2](호의 시작·끝점), arc = [mx, my](호 위의 한 점, 저장값은 호의 가운데 점). arc 키가 없으면 직선 벽이다.
지역 좌표: u = 축 시작점에서 호를 따라 잰 거리(0 ~ 호 길이), v = 진행 방향 왼쪽 거리(직선 벽과 같은 뜻: 왼쪽 면 = +두께/2).
  세계 = 중심 + (r - sg·v)·(cos θ, sin θ), θ = a0 + sg·u/r. sg = +1 반시계, -1 시계(왼쪽이 중심 쪽이면 반시계다).
도면은 호를 같은 각도 간격의 조각(facet)으로 나눠 그린다: 조각 경계는 축 전체(0 ~ 호 길이)를 n 등분한 u 값이라 벽을 잘라도(조인·개구부)
이웃 조각과 꼭짓점이 맞는다. 조각 하나는 두 반지름 사이의 사다리꼴(볼록)이라 직선 벽의 사각 조각을 쓰는 가림·윤곽 계산을 그대로 쓴다.
"""
from __future__ import annotations

from math import sqrt

PI = 3.141592653589793
TWO_PI = 2.0 * PI
FACET_MAX_RAD = 5.0 * PI / 180.0   # 도면 조각 하나가 덮는 중심각 상한(5도)
FACET_SAG = 2.0                     # 조각 현과 호의 최대 거리(mm) 상한: 반지름이 크면 조각을 더 잘게 나눈다
FACET_N_MAX = 720                   # 벽 하나의 조각 수 상한
ARC_R_MAX = 1.0e6                   # 반지름 상한(mm, 1km). 더 크면 직선 벽으로 그린다
ARC_SAG_MIN = 1.0                   # 호 가운데 점이 현에서 이 거리(mm) 이상 떨어져야 곡선 벽이다(아니면 직선 벽을 쓴다)
ARC_INNER_MIN = 1.0                 # 안쪽 면 반지름(r - 두께/2)의 하한(mm)


# ---------------------------------------------------------------- 결정적 삼각함수(사칙연산과 sqrt 만)
_SC: dict = {}          # sincos 값 기억(같은 각은 늘 같은 값이라 결과와 무관, 속도만). 너무 커지면 비운다
_SC_MAX = 200000


def sincos(t: float) -> tuple:
    """(sin t, cos t). 90도 단위로 줄여 |x| <= π/4 에서 테일러 급수 고정 12항(사칙연산만, kinds.sincos_deg 와 같은 방식의 라디안판)."""
    v = _SC.get(t)
    if v is not None:
        return v
    k = round(t / (PI / 2.0))
    x = t - k * (PI / 2.0)
    x2 = x * x
    sn = cs = 0.0
    tn, tc = x, 1.0
    for i in range(1, 13):
        sn += tn
        cs += tc
        tn = -tn * x2 / ((2 * i) * (2 * i + 1))
        tc = -tc * x2 / ((2 * i - 1) * (2 * i))
    v = ((sn, cs), (cs, -sn), (-sn, -cs), (-cs, sn))[k % 4]
    if len(_SC) >= _SC_MAX:
        _SC.clear()
    _SC[t] = v
    return v


def _atan_small(z: float) -> float:
    """|z| <= 1 의 atan. 반각 공식을 두 번 써 |z| < 0.2 로 줄인 뒤 급수 30항(annot._atan_small 과 같다)."""
    k = 1.0
    for _ in range(2):
        z = z / (1.0 + sqrt(1.0 + z * z))
        k *= 2.0
    acc, term, z2 = 0.0, z, z * z
    for n in range(30):
        acc += term / (2 * n + 1) * (1.0 if n % 2 == 0 else -1.0)
        term *= z2
    return k * acc


def atan2(y: float, x: float) -> float:
    """(-π, π] 의 atan2. (0, 0) 은 0."""
    if x == 0.0 and y == 0.0:
        return 0.0
    if abs(x) >= abs(y):
        a = _atan_small(y / x)
        if x > 0:
            return a
        return a + PI if y >= 0 else a - PI
    a = PI / 2.0 - _atan_small(x / abs(y))
    return a if y > 0 else -a


# ---------------------------------------------------------------- 호 정의
def _r6(v: float) -> float:
    return round(v, 6) + 0.0


def circle3(p0, pm, p1):
    """세 점을 지나는 원 (cx, cy, r). 세 점이 한 직선 위(또는 겹침)면 None."""
    ax, ay = pm[0] - p0[0], pm[1] - p0[1]
    bx, by = p1[0] - p0[0], p1[1] - p0[1]
    d = 2.0 * (ax * by - ay * bx)
    if abs(d) < 1e-12:
        return None
    a2, b2 = ax * ax + ay * ay, bx * bx + by * by
    ux = (by * a2 - ay * b2) / d
    uy = (ax * b2 - bx * a2) / d
    return (p0[0] + ux, p0[1] + uy, sqrt(ux * ux + uy * uy))


def info(axis, mid):
    """곡선 벽 축의 계산값 (cx, cy, r, a0, sg, sw): 중심·반지름·시작각(라디안)·방향(+1 반시계, -1 시계)·중심각 크기(0 < sw < 2π).
    세 점(시작·mid·끝)이 한 직선 위면 None(params 검사가 막으므로 정상 흐름에서는 없다)."""
    p0, p1 = (axis[0], axis[1]), (axis[2], axis[3])
    c = circle3(p0, mid, p1)
    if c is None:
        return None
    cx, cy, r = c
    cr = (mid[0] - p0[0]) * (p1[1] - p0[1]) - (mid[1] - p0[1]) * (p1[0] - p0[0])
    sg = 1.0 if cr > 0 else -1.0   # (mid - 시작) x (끝 - 시작) > 0 이면 시작 -> mid -> 끝 이 반시계로 돈다
    a0 = atan2(p0[1] - cy, p0[0] - cx)
    a1 = atan2(p1[1] - cy, p1[0] - cx)
    sw = (sg * (a1 - a0)) % TWO_PI
    if sw <= 0.0:
        sw = TWO_PI
    return (cx, cy, r, a0, sg, sw)


def length(ai) -> float:
    """호 길이(축 길이)."""
    return ai[2] * ai[5]


def point(ai, u: float, v: float = 0.0) -> tuple:
    """지역 (u, v) -> 세계 (x, y)."""
    cx, cy, r, a0, sg, _ = ai
    sn, cs = sincos(a0 + sg * u / r)
    rr = r - sg * v
    return (cx + rr * cs, cy + rr * sn)


def tangent(ai, u: float) -> tuple:
    """u 에서의 단위 진행 방향."""
    _, _, r, a0, sg, _ = ai
    sn, cs = sincos(a0 + sg * u / r)
    return (-sg * sn, sg * cs)


def u_of(ai, p) -> float:
    """세계 점 p 를 축 지역 u 로(중심에서 본 각도로). 호 가운데를 중심으로 ±π 범위의 각도 차를 쓴다(축 밖으로 늘인 끝도 가까운 쪽으로)."""
    cx, cy, r, a0, sg, sw = ai
    d = sg * (atan2(p[1] - cy, p[0] - cx) - a0)
    half = sw / 2.0
    d = (d - half + PI) % TWO_PI - PI + half
    return d * r


def canonical_mid(axis, through) -> list:
    """시작·끝과 호 위의 한 점(through)으로 정한 호의 가운데 점 [mx, my](저장값, 소수 6자리)."""
    ai = info(axis, through)
    return [_r6(x) for x in point(ai, length(ai) / 2.0)]


def from_center(cx, cy, r, a0_deg, a1_deg) -> tuple:
    """중심·반지름·시작각·끝각(도, 반시계 +)으로 정한 호 -> (axis, mid). sweep = a1 - a0 (음수면 시계 방향)."""
    sweep = (a1_deg - a0_deg) * PI / 180.0
    t0 = a0_deg * PI / 180.0
    pts = []
    for t in (t0, t0 + sweep / 2.0, t0 + sweep):
        sn, cs = sincos(t)
        pts.append((_r6(cx + r * cs), _r6(cy + r * sn)))
    return [pts[0][0], pts[0][1], pts[2][0], pts[2][1]], [pts[1][0], pts[1][1]]


def problem(axis, mid, thickness: float) -> str | None:
    """곡선 벽 축이 올바른가. 문제가 있으면 이유 문자열."""
    dx, dy = axis[2] - axis[0], axis[3] - axis[1]
    ch = sqrt(dx * dx + dy * dy)
    if ch < 1.0:
        return "곡선 벽의 시작·끝점이 1mm 이상 떨어져야 한다"
    sag = abs((mid[0] - axis[0]) * dy - (mid[1] - axis[1]) * dx) / ch
    if sag < ARC_SAG_MIN:
        return f"호 위의 점이 시작·끝을 잇는 현에서 {ARC_SAG_MIN:g}mm 이상 떨어져야 한다(거의 직선이면 직선 벽을 쓰세요)"
    ai = info(axis, mid)
    if ai is None:
        return "세 점이 한 직선 위에 있다"
    if ai[2] > ARC_R_MAX:
        return f"반지름이 {ARC_R_MAX:g}mm 를 넘는다(직선 벽을 쓰세요)"
    if ai[2] - thickness / 2.0 < ARC_INNER_MIN:
        return "반지름이 벽 두께의 반보다 커야 한다(안쪽 면 반지름이 1mm 이상)"
    return None


# ---------------------------------------------------------------- 도면 조각
def facet_n(ai) -> int:
    """축 전체(0 ~ 호 길이)를 나누는 조각 수: 중심각 5도 이하, 현-호 거리 FACET_SAG 이하."""
    r, sw = ai[2], ai[5]
    step = FACET_MAX_RAD
    k = 2.0 * sqrt(2.0 * FACET_SAG / r)   # 현-호 거리 = r(1 - cos(θ/2)) ≈ r θ²/8
    if k < step:
        step = k
    n = int(sw / step)
    if n * step < sw:
        n += 1
    return max(1, min(FACET_N_MAX, n))


def breaks(ai, a: float, b: float) -> list:
    """[a, b] 를 조각 경계에서 나눈 u 값들(양 끝 포함, 오름차순). 조각 간격 = 호 길이 / n, 경계는 u = k·간격(축 밖으로 늘인 곳도 같은 간격)."""
    step = length(ai) / facet_n(ai)
    out = [a]
    k = int(a / step)
    if k * step <= a:
        k += 1
    while k * step < b:
        u = k * step
        if u - out[-1] > 1e-6 and b - u > 1e-6:
            out.append(u)
        k += 1
    out.append(b)
    return out


def split_ivs(ai, ivs) -> tuple:
    """u 구간들을 조각 경계에서 나눈다."""
    out = []
    for a, b in ivs:
        us = breaks(ai, a, b)
        out.extend(zip(us, us[1:]))
    return tuple(out)


# ---------------------------------------------------------------- 교점(조인)
def line_hits(ai, x, y, c, s) -> list:
    """원(중심 cx,cy 반지름 r) 과 직선 (x,y)+t(c,s) 의 교점들."""
    cx, cy, r = ai[0], ai[1], ai[2]
    fx, fy = x - cx, y - cy
    b = fx * c + fy * s
    q = fx * fx + fy * fy - r * r
    disc = b * b - q
    if disc < 0.0:
        return []
    sq = sqrt(disc)
    return [(x + t * c, y + t * s) for t in (-b - sq, -b + sq)]


def circle_hits(a1, a2) -> list:
    """두 원의 교점들."""
    dx, dy = a2[0] - a1[0], a2[1] - a1[1]
    d2 = dx * dx + dy * dy
    if d2 < 1e-12:
        return []
    d = sqrt(d2)
    r1, r2 = a1[2], a2[2]
    a = (r1 * r1 - r2 * r2 + d2) / (2.0 * d)
    h2 = r1 * r1 - a * a
    if h2 < 0.0:
        return []
    h = sqrt(h2)
    mx, my = a1[0] + a * dx / d, a1[1] + a * dy / d
    return [(mx - h * dy / d, my + h * dx / d), (mx + h * dy / d, my - h * dx / d)]


def center_segs(key, p) -> list:
    """벽 중심선 선분 [(키, (x1, y1), (x2, y2), 두께)]: 직선 벽은 축선 하나, 곡선 벽은 조각 경계의 현들(키 = "id#번호").
    벽 중심선으로 닫힌 영역 찾기(space.from_walls·법정 면적)가 쓴다."""
    x1, y1, x2, y2 = p["axis"]
    if "arc" not in p:
        return [(key, (x1, y1), (x2, y2), p["thickness"])]
    ai = info(p["axis"], p["arc"])
    us = breaks(ai, 0.0, length(ai))
    pts = [(x1, y1)] + [point(ai, u) for u in us[1:-1]] + [(x2, y2)]
    return [(f"{key}#{i:03d}", a, b, p["thickness"]) for i, (a, b) in enumerate(zip(pts, pts[1:]))]
