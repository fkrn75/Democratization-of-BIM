# SPDX-License-Identifier: MPL-2.0
"""솔리드 모델링 확장(W2 여섯 번째 PR): 회전체(revolve)·3D 스윕(sweep3d)·곡면 해상도(res)·불리언(ops).

solids.py 와 같은 규칙을 지킨다: ifcopenshell 을 import 하지 않고 값은 결정적이다. 삼각함수(libm)를 쓰지 않는다:
각도의 sin·cos 은 solids.sincos_deg(테일러 급수), 각도 재기는 이 모듈의 atan2_deg(sqrt 로 인수를 줄인 급수)다.

곡면 해상도 res (요소별, 선택)
  {"seg": n}   원 한 바퀴를 n 현으로(8~1024). 호는 중심각에 비례해 나눈다
  {"tol": t}   현과 호 사이 최대 거리(현 높이) t mm 이하가 되는 가장 작은 4의 배수 현 수(8~1024). 반지름이 클수록 잘게
  없으면 기존 값: 부재 원 단면은 32각형(이등분), 새 형태는 한 바퀴 32현.

불리언 ops (요소별, 선택): [{"op": "difference"|"union"|"intersection", "solid": {형태 + 형태별 키(+res, +ops)}}, ...] 를 차례로 적용한다.
  계산은 이진 공간 분할(BSP) 다면체 불리언이다(평면 다각형 분할만: 사칙연산 + sqrt). 결과는 같은 평면 조각을 다시 합쳐
  면(바깥 고리 + 구멍)으로 돌려주고, 한 면의 꼭짓점이 이웃 면 모서리 위에 놓인 T 이음은 이웃 모서리에 꼭짓점을 끼워 닫는다.
  정확도: 피연산자 다면체(현으로 나눈 곡면 포함)에 대해 평면 판정 허용 오차 EPS(1e-4 mm) 안에서 정확하다.
"""
from __future__ import annotations

from math import isfinite, sqrt

from dobim_core import solids as SO

PI = 3.141592653589793
SEG_DEFAULT = 32          # res 가 없을 때 새 형태의 한 바퀴 현 수(기존 원 32각형과 같다)
SEG_MIN, SEG_MAX = 8, 1024
TOL_MIN, TOL_MAX = 0.01, 1000.0
BULGE_MAX = 1.0           # 볼록도(tan(중심각/4)) 절댓값 상한: 반원까지
REV_PROFILE_MAX = 256     # 회전체 단면 꼭짓점 수 상한(호를 펼치기 전)
RAIL_MAX = 256            # 3D 스윕 경로 꼭짓점 수 상한(호를 펼치기 전)
LOOP_MAX = 2048           # 호를 펼친 단면 꼭짓점 수 상한
MESH_VERTS_LIMIT = 200000  # 한 형태가 만드는 꼭짓점 수 상한
OPS_MAX = 16              # 요소 하나의 불리언 연산 수 상한(중첩 포함 전체)
OPS_DEPTH = 3             # 피연산자 안의 ops 중첩 깊이 상한
CSG_POLYS_LIMIT = 60000   # 불리언 입력 다각형 수 상한
OPS = ("difference", "union", "intersection")
EPS = 1e-4                # 평면 판정 허용 오차(mm)


# ---------------------------------------------------------------- 결정적 각도
def _atan(t: float) -> float:
    """atan(t) 라디안. atan(t) = 2 atan(t / (1 + sqrt(1 + t²))) 로 |t| <= 0.1 까지 줄인 뒤 급수."""
    k = 0
    while abs(t) > 0.1:
        t = t / (1.0 + sqrt(1.0 + t * t))
        k += 1
    t2 = t * t
    s, term = 0.0, t
    for i in range(14):
        s += term / (2 * i + 1)
        term = -term * t2
    return s * (1 << k)


def atan2_deg(y: float, x: float) -> float:
    """(-180, 180] 도. 사칙연산 + sqrt 만."""
    if x == 0.0 and y == 0.0:
        return 0.0
    if abs(x) >= abs(y):
        a = _atan(y / x)
        if x < 0:
            a += PI if y >= 0 else -PI
    else:
        a = (PI / 2.0 if y > 0 else -PI / 2.0) - _atan(x / y)
    return a * 180.0 / PI


def sincos(deg: float) -> tuple:
    s, c = SO.sincos_deg(float(deg))
    return s + 0.0, c + 0.0


# ---------------------------------------------------------------- 곡면 해상도
def res_problem(res) -> str | None:
    if not isinstance(res, dict) or len(res) != 1:
        return "solid.res 는 {seg: 현 수} 또는 {tol: 현 높이 mm} 하나다"
    if "seg" in res:
        n = res["seg"]
        if type(n) is not int or not SEG_MIN <= n <= SEG_MAX:
            return f"solid.res.seg 는 {SEG_MIN}~{SEG_MAX} 정수다"
        return None
    if "tol" in res:
        t = res["tol"]
        if not (SO._num(t) and TOL_MIN <= t <= TOL_MAX):
            return f"solid.res.tol 은 {TOL_MIN:g}~{TOL_MAX:g} mm 다"
        return None
    return "solid.res 는 {seg: 현 수} 또는 {tol: 현 높이 mm} 하나다"


def full_segments(res, r: float) -> int:
    """반지름 r 인 원 한 바퀴의 현 수."""
    if res is None:
        return SEG_DEFAULT
    if "seg" in res:
        return int(res["seg"])
    tol = float(res["tol"])
    n = SEG_MIN
    while n < SEG_MAX:
        s, _ = sincos(90.0 / n)          # 현 높이 = r (1 - cos(180/n)) = 2 r sin²(90/n)
        if 2.0 * r * s * s <= tol:
            break
        n += 4
    return n


def arc_segments(res, r: float, sweep: float) -> int:
    """중심각 sweep 도인 호의 현 수(1 이상)."""
    n = full_segments(res, r) * abs(sweep) / 360.0
    k = int(n)
    if n - k > 1e-9:
        k += 1
    return max(k, 1)


def circle(r: float, cy: float, res) -> list:
    """중심 (0, cy) 반지름 r 반시계 원(현 수는 res). 꼭짓점은 +x 축에서 시작한다."""
    n = full_segments(res, r)
    out = []
    for i in range(n):
        s, c = sincos(360.0 * i / n)
        out.append((r * c + 0.0, cy + r * s + 0.0))
    return out


# ---------------------------------------------------------------- 볼록도(bulge) 호
def bulges_problem(pts, bulges, what) -> str | None:
    if not isinstance(bulges, list) or len(bulges) != len(pts):
        return f"{what}.bulges 는 꼭짓점마다 하나(변 i -> i+1 의 볼록도)다"
    if not all(SO._num(b) and abs(b) <= BULGE_MAX for b in bulges):
        return f"{what}.bulges 는 -{BULGE_MAX:g}~{BULGE_MAX:g} 다(tan(중심각/4), 양수 = 반시계 호)"
    return None


def bulge_arc(a, b, bu: float, res) -> list:
    """a 에서 b 까지 볼록도 bu 인 호의 안쪽 점들(a·b 제외). 양수 = 반시계(진행 방향 오른쪽으로 부푼다)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    c = sqrt(dx * dx + dy * dy)
    if c < 1e-9 or abs(bu) < 1e-12:
        return []
    d = c * (1.0 - bu * bu) / (4.0 * bu)              # 현 가운데에서 중심까지(왼쪽 법선 방향, 부호 있음)
    cx = (a[0] + b[0]) / 2.0 - dy / c * d
    cy = (a[1] + b[1]) / 2.0 + dx / c * d
    r = c * (1.0 + bu * bu) / (4.0 * abs(bu))
    sweep = 4.0 * _atan(bu) * 180.0 / PI              # 부호 있는 중심각(도)
    a0 = atan2_deg(a[1] - cy, a[0] - cx)
    k = arc_segments(res, r, sweep)
    out = []
    for j in range(1, k):
        s, co = sincos(a0 + sweep * j / k)
        out.append((cx + r * co, cy + r * s))
    return out


def bulge_loop(pts, bulges, res) -> list:
    """닫힌 고리(볼록도 선택)를 점 목록으로 펼친다."""
    out = []
    n = len(pts)
    for i in range(n):
        a = (float(pts[i][0]), float(pts[i][1]))
        out.append(a)
        if bulges and bulges[i]:
            b = (float(pts[(i + 1) % n][0]), float(pts[(i + 1) % n][1]))
            out.extend(bulge_arc(a, b, float(bulges[i]), res))
    return out


# ---------------------------------------------------------------- 3D 벡터
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _len(a):
    return sqrt(_dot(a, a))


def _unit(a):
    ln = _len(a)
    return (a[0] / ln, a[1] / ln, a[2] / ln)


def _rot(v, k, c, s):
    """단위 축 k 둘레로 (cos c, sin s) 만큼 돌린 v(로드리게스)."""
    kv = _dot(k, v)
    kx = _cross(k, v)
    return (v[0] * c + kx[0] * s + k[0] * kv * (1.0 - c), v[1] * c + kx[1] * s + k[1] * kv * (1.0 - c), v[2] * c + kx[2] * s + k[2] * kv * (1.0 - c))


def default_ref(axis) -> list:
    """회전체의 기준 방향(각 0): 세계 x 를 축에 수직으로 투영(축이 거의 x 면 세계 y). 소수 9자리."""
    A = _unit(tuple(float(v) for v in axis))
    ref = (1.0, 0.0, 0.0) if abs(A[0]) <= 0.999 else (0.0, 1.0, 0.0)
    k = _dot(ref, A)
    u = _unit((ref[0] - k * A[0], ref[1] - k * A[1], ref[2] - k * A[2]))
    return [round(c, 9) + 0.0 for c in u]


def signed_volume(verts, faces) -> float:
    v = 0.0
    for f in faces:
        for lp in f:
            a = verts[lp[0]]
            for i in range(1, len(lp) - 1):
                b, c = verts[lp[i]], verts[lp[i + 1]]
                v += _dot(a, _cross(b, c))
    return v / 6.0


def _oriented(verts, faces):
    """쓰는 꼭짓점만 남기고(처음 나온 순서) 부피가 양수가 되게 면 방향을 맞춘다."""
    remap, vs = {}, []
    out = []
    for f in faces:
        nf = []
        for lp in f:
            nl = []
            for i in lp:
                if i not in remap:
                    remap[i] = len(vs)
                    vs.append(verts[i])
                nl.append(remap[i])
            nf.append(nl)
        out.append(nf)
    if signed_volume(vs, out) < 0:
        out = [[lp[::-1] for lp in f] for f in out]
    return vs, out


def _dedupe_ring(lp):
    out = []
    for i in lp:
        if not out or out[-1] != i:
            out.append(i)
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _vec3_ok(v) -> bool:
    return SO._pt3(v)


# ---------------------------------------------------------------- 회전체(revolve)
def revolve_loop(p) -> list:
    return bulge_loop(p["profile"], p.get("bulges"), p.get("res"))


def revolve_frame(p):
    A = _unit(tuple(float(v) for v in p["axis"]))
    ref = tuple(float(v) for v in p["ref"])
    k = _dot(ref, A)
    R = _unit((ref[0] - k * A[0], ref[1] - k * A[1], ref[2] - k * A[2]))
    T = _cross(A, R)
    return tuple(float(v) for v in p["origin"]), A, R, T


def revolve_problem(p) -> str | None:
    prof = p["profile"]
    lo = 2 if "bulges" in p else 3   # 호가 있으면 두 점(반원 둘 = 원)도 된다
    if not isinstance(prof, list) or not lo <= len(prof) <= REV_PROFILE_MAX or not all(SO._pt2(q) for q in prof):
        return f"solid.profile 는 [r, h] {lo}~{REV_PROFILE_MAX}개다(r = 축에서 거리, h = 축 방향)"
    if any(q[0] < 0 for q in prof):
        return "회전체 단면의 r 은 0 이상이어야 한다(축을 넘지 않는다)"
    if "bulges" in p:
        why = bulges_problem(prof, p["bulges"], "solid")
        if why:
            return why
    if not (_vec3_ok(p["origin"]) and _vec3_ok(p["axis"]) and _vec3_ok(p["ref"])):
        return "solid.origin·axis·ref 는 [x,y,z] 다"
    if _len(p["axis"]) < 1e-9:
        return "solid.axis 는 0 벡터가 아니어야 한다"
    A = _unit(p["axis"])
    rl = _len(p["ref"])
    if rl < 1e-9 or abs(_dot(A, p["ref"])) / rl > 0.999:
        return "solid.ref 는 축과 나란하지 않은 방향이어야 한다"
    if not (SO._num(p["angle"]) and 0 < p["angle"] <= 360):
        return "solid.angle 은 0 초과 360 이하(도)다"
    loop = revolve_loop(p)
    if len(loop) > LOOP_MAX:
        return f"회전체 단면을 호로 펼친 꼭짓점이 {LOOP_MAX}개를 넘는다(res 를 줄이세요)"
    if any(q[0] < -1e-6 for q in loop):
        return "회전체 단면의 호가 축을 넘는다(r < 0)"
    why = SO.poly_problem(loop, 3, LOOP_MAX)
    if why:
        return f"solid.profile: {why}"
    rmax = max(q[0] for q in loop)
    k = arc_segments(p.get("res"), rmax, p["angle"])
    if len(loop) * (k + 1) > MESH_VERTS_LIMIT:
        return f"회전체 꼭짓점이 {MESH_VERTS_LIMIT}개를 넘는다(res 를 줄이세요)"
    return None


def revolve_mesh(p):
    loop = revolve_loop(p)
    O, A, R, T = revolve_frame(p)
    ang = float(p["angle"])
    rmax = max(q[0] for q in loop)
    k = arc_segments(p.get("res"), rmax, ang)
    full = ang >= 360.0 - 1e-9
    rings = k if full else k + 1
    dirs = []
    for j in range(rings):
        s, c = sincos(ang * j / k)
        dirs.append((R[0] * c + T[0] * s, R[1] * c + T[1] * s, R[2] * c + T[2] * s))
    verts, idx = [], []
    for r, h in loop:
        base = _add(O, _mul(A, h))
        if r <= 1e-9:   # 축 위 꼭짓점: 모든 각도가 한 점
            verts.append(base)
            idx.append([len(verts) - 1] * rings)
        else:
            row = []
            for d in dirs:
                verts.append(_add(base, _mul(d, r)))
                row.append(len(verts) - 1)
            idx.append(row)
    n = len(loop)
    faces = []
    for i in range(n):
        i2 = (i + 1) % n
        if abs(loop[i][1] - loop[i2][1]) <= 1e-9:   # 축에 수직인 변: 원판·고리(부채꼴)를 면 하나로(같은 평면 삼각형 부채를 만들지 않는다)
            ri, ri2 = loop[i][0] > 1e-9, loop[i2][0] > 1e-9
            inc = [idx[i][j] for j in range(rings)]
            dec = [idx[i2][j] for j in range(rings)][::-1]
            if not full:
                faces.append([_dedupe_ring(inc + dec)])
            elif ri and ri2:
                faces.append([inc, dec] if loop[i][0] > loop[i2][0] else [dec, inc])
            elif ri or ri2:
                faces.append([inc if ri else dec])
            continue
        for j in range(k):
            j2 = (j + 1) % rings
            lp = _dedupe_ring([idx[i][j], idx[i][j2], idx[i2][j2], idx[i2][j]])
            if len(set(lp)) >= 3:
                faces.append([lp])
    if not full:
        faces.append([_dedupe_ring([idx[i][0] for i in range(n)])])
        faces.append([_dedupe_ring([idx[i][rings - 1] for i in range(n)][::-1])])
    return _oriented(verts, faces)


# ---------------------------------------------------------------- 3D 스윕(sweep3d)
def _arc3(a, m, b, res):
    """a 에서 m 을 지나 b 까지 3점 호의 점들(a 제외, b 포함)과 반지름."""
    u, v = _sub(m, a), _sub(b, a)
    w = _cross(u, v)
    ww = _dot(w, w)
    c = _add(a, _mul(_add(_mul(_cross(v, w), _dot(u, u)), _mul(_cross(w, u), _dot(v, v))), 1.0 / (2.0 * ww)))
    r = _len(_sub(a, c))
    e1 = _unit(_sub(a, c))
    n = _unit(w)
    e2 = _cross(n, e1)
    bc = _sub(b, c)
    th = atan2_deg(_dot(bc, e2), _dot(bc, e1))
    if th <= 0:
        th += 360.0
    k = arc_segments(res, r, th)
    out = []
    for j in range(1, k):
        s, co = sincos(th * j / k)
        out.append(_add(c, _add(_mul(e1, r * co), _mul(e2, r * s))))
    out.append(tuple(float(x) for x in b))
    return out, r


def rail_points(p):
    """펼친 경로 점들(호를 현으로), 호 반지름 최솟값(없으면 None)."""
    rail = [tuple(float(x) for x in q) for q in p["rail"]]
    via = p.get("via")
    out = [rail[0]]
    rmin = None
    for i in range(len(rail) - 1):
        m = via[i] if via else None
        if m is not None:
            pts, r = _arc3(rail[i], tuple(float(x) for x in m), rail[i + 1], p.get("res"))
            out.extend(pts)
            rmin = r if rmin is None else min(rmin, r)
        else:
            out.append(rail[i + 1])
    return out, rmin


def sweep3d_loops(p) -> tuple:
    """단면 (바깥 반시계, 구멍들 시계) (a, b): a = 진행 방향 왼쪽(수평 경로면), b = 위."""
    if "section" in p:
        return SO.member_section(p["section"], "center", p.get("res"))
    loop = bulge_loop(p["profile"], p.get("bulges"), p.get("res"))
    return SO.ccw(loop), []


def _closed_rail(rail) -> bool:
    return len(rail) > 3 and all(abs(rail[0][k] - rail[-1][k]) <= 1e-6 for k in range(3))


def sweep3d_problem(p) -> str | None:
    rail = p["rail"]
    if not isinstance(rail, list) or not 2 <= len(rail) <= RAIL_MAX or not all(_vec3_ok(q) for q in rail):
        return f"solid.rail 은 [x,y,z] 2~{RAIL_MAX}개다"
    for i in range(len(rail) - 1):
        if _len(_sub(rail[i + 1], rail[i])) < 1.0:
            return "3D 스윕 경로의 이웃 꼭짓점은 1mm 이상 떨어져야 한다"
    if "via" in p:
        via = p["via"]
        if not isinstance(via, list) or len(via) != len(rail) - 1 or not all(m is None or _vec3_ok(m) for m in via):
            return "solid.via 는 경로 구간마다 하나(null = 직선, [x,y,z] = 그 점을 지나는 호)다"
        if all(m is None for m in via):
            return "solid.via 에 호가 하나도 없다(직선만이면 via 를 빼세요)"
        for i, m in enumerate(via):
            if m is None:
                continue
            u, v = _sub(m, rail[i]), _sub(rail[i + 1], rail[i])
            lu, lv = _len(u), _len(v)
            if lu < 1.0 or _len(_sub(rail[i + 1], m)) < 1.0 or _len(_cross(u, v)) <= 1e-6 * lu * lv:
                return f"solid.via[{i}] 가 구간 양 끝과 한 직선 위이거나 너무 가깝다(호를 정할 수 없다)"
    if ("profile" in p) == ("section" in p):
        return "3D 스윕은 profile(윤곽) 과 section(부재 단면) 중 하나만 준다"
    if "section" in p:
        why = SO.member_problem({"start": [0, 0, 0], "end": [1, 0, 0], "anchor": "center", "section": p["section"]})
        if why:
            return why
        if "bulges" in p:
            return "solid.bulges 는 profile 과 함께만 쓴다"
    else:
        prof = p["profile"]
        lo = 2 if "bulges" in p else 3
        if not isinstance(prof, list) or not all(SO._pt2(q) for q in prof) or not lo <= len(prof) <= SO.SWEEP_PROFILE_MAX:
            return f"solid.profile 은 [a, b] {lo}~{SO.SWEEP_PROFILE_MAX}개다(a = 진행 방향 왼쪽, b = 위)"
        if "bulges" in p:
            why = bulges_problem(prof, p["bulges"], "solid")
            if why:
                return why
        loop = bulge_loop(prof, p.get("bulges"), p.get("res"))
        if len(loop) > LOOP_MAX:
            return f"단면을 호로 펼친 꼭짓점이 {LOOP_MAX}개를 넘는다(res 를 줄이세요)"
        why = SO.poly_problem(loop, 3, LOOP_MAX)
        if why:
            return f"solid.profile: {why}"
    pts, rmin = rail_points(p)
    outer, holes = sweep3d_loops(p)
    reach = max(sqrt(a * a + b * b) for a, b in outer)
    if rmin is not None and rmin <= reach:
        return f"호 경로의 반지름({rmin:.1f}mm)이 단면 크기({reach:.1f}mm)보다 커야 한다(단면이 자기와 겹친다)"
    closed = _closed_rail(rail)
    if closed:
        pts = pts[:-1]
    n = len(pts)
    segs = n if closed else n - 1
    for i in range(segs):
        if _len(_sub(pts[(i + 1) % n], pts[i])) < 1e-6:
            return "3D 스윕 경로를 펼친 구간 길이가 0 이다"
    rng = range(n) if closed else range(1, n - 1)
    for i in rng:
        t0 = _unit(_sub(pts[i], pts[(i - 1) % n]))
        t1 = _unit(_sub(pts[(i + 1) % n], pts[i]))
        if _dot(t0, t1) < -0.94:
            return "3D 스윕 경로가 140도 넘게 꺾인다(마이터가 너무 길어진다)"
    if (len(outer) + sum(len(h) for h in holes)) * n > MESH_VERTS_LIMIT:
        return f"3D 스윕 꼭짓점이 {MESH_VERTS_LIMIT}개를 넘는다(res 를 줄이세요)"
    return None


def _first_frame(D):
    """부재(member_frame)와 같은 규칙: 위 U = 세계 z 를 축에 수직으로 투영(축이 거의 수직이면 세계 x), 폭 S = U x D."""
    ref = (0.0, 0.0, 1.0) if abs(D[2]) <= 0.999 else (1.0, 0.0, 0.0)
    k = _dot(ref, D)
    U = _unit((ref[0] - k * D[0], ref[1] - k * D[1], ref[2] - k * D[2]))
    return _cross(U, D), U


def _transport(S, U, t0, t1):
    """t0 -> t1 최소 회전으로 틀을 옮긴다(회전 최소화 틀)."""
    ax = _cross(t0, t1)
    s = _len(ax)
    c = _dot(t0, t1)
    if s < 1e-12:
        return S, U
    k = (ax[0] / s, ax[1] / s, ax[2] / s)
    return _rot(S, k, c, s), _rot(U, k, c, s)


def sweep3d_mesh(p):
    pts, _ = rail_points(p)
    closed = _closed_rail(p["rail"])
    if closed:
        pts = pts[:-1]
    n = len(pts)
    segs = n if closed else n - 1
    tans = [_unit(_sub(pts[(i + 1) % n], pts[i])) for i in range(segs)]
    S, U = _first_frame(tans[0])
    frames = [(S, U)]
    for i in range(1, segs):
        S, U = _transport(S, U, tans[i - 1], tans[i])
        frames.append((S, U))
    if closed:   # 닫힌 경로: 한 바퀴 옮긴 틀과 처음 틀의 비틀림을 구간마다 고르게 나눠 되돌린다
        S2, _ = _transport(S, U, tans[-1], tans[0])
        S0 = frames[0][0]
        tw = atan2_deg(_dot(_cross(S0, S2), tans[0]), _dot(S0, S2))
        if abs(tw) > 1e-9:
            fr2 = []
            for i, (Si, Ui) in enumerate(frames):
                s, c = sincos(-tw * i / segs)
                fr2.append((_rot(Si, tans[i], c, s), _rot(Ui, tans[i], c, s)))
            frames = fr2
    outer, holes = sweep3d_loops(p)
    loops = [list(outer)] + [list(h) for h in holes]
    verts = []
    rings = []   # rings[i][loop] = 꼭짓점 번호 목록
    for i in range(n):
        if not closed and i == 0:
            (Si, Ui), tin, tout = frames[0], None, None
        elif not closed and i == n - 1:
            (Si, Ui), tin, tout = frames[-1], None, None
        else:
            (Si, Ui), tin, tout = frames[(i - 1) % segs], tans[(i - 1) % segs], tans[i % segs]
        m = _unit(_add(tin, tout)) if tin is not None else None
        row = []
        for lp in loops:
            ids = []
            for a, b in lp:
                x = _add(_mul(Si, a), _mul(Ui, b))
                if m is not None:
                    x = _add(x, _mul(tin, -_dot(x, m) / _dot(tin, m)))
                verts.append(_add(pts[i], x))
                ids.append(len(verts) - 1)
            row.append(ids)
        rings.append(row)
    faces = []
    for i in range(segs):
        ra, rb = rings[i], rings[(i + 1) % n]
        for li in range(len(loops)):
            a, b = ra[li], rb[li]
            m = len(a)
            for k in range(m):
                q = (k + 1) % m
                quad = [a[k], a[q], b[q], b[k]]
                if SO._planar([verts[j] for j in quad]):
                    faces.append([quad])
                else:   # 비틀림 보정으로 생긴 비평면 사각형은 두 삼각형으로(IfcFacetedBrep 의 면은 평면)
                    faces.append([[a[k], a[q], b[q]]])
                    faces.append([[a[k], b[q], b[k]]])
    if not closed:
        faces.append([rings[0][0][::-1]] + [h[::-1] for h in rings[0][1:]])
        faces.append([list(rings[-1][0])] + [list(h) for h in rings[-1][1:]])
    return _oriented(verts, faces)


# ---------------------------------------------------------------- 불리언(ops) 검사
def count_ops(p, depth=1) -> tuple:
    """(연산 수 합계, 최대 깊이)."""
    n, d = 0, 0
    for o in p.get("ops", ()):
        if isinstance(o, dict) and isinstance(o.get("solid"), dict):
            a, b = count_ops(o["solid"], depth + 1)
            n += 1 + a
            d = max(d, depth, b)
        else:
            n += 1
            d = max(d, depth)
    return n, d


def ops_problem(p, problem) -> str | None:
    """ops 목록 검사. problem(피연산자 형상 사전) -> 이유. 피연산자에는 이름·범주가 없다."""
    ops = p["ops"]
    if not isinstance(ops, list) or not 1 <= len(ops) <= OPS_MAX:
        return f"solid.ops 는 1~{OPS_MAX}개다"
    total, depth = count_ops(p)
    if total > OPS_MAX:
        return f"불리언 연산이 중첩 포함 {OPS_MAX}개를 넘는다"
    if depth > OPS_DEPTH:
        return f"불리언 피연산자 중첩은 {OPS_DEPTH}단계까지다"
    for i, o in enumerate(ops):
        if not isinstance(o, dict) or set(o) != {"op", "solid"} or o["op"] not in OPS or not isinstance(o["solid"], dict):
            return f"solid.ops[{i}] 는 {{op: {'/'.join(OPS)}, solid: 형상}} 이다"
        q = o["solid"]
        if {"name", "category"} & set(q):
            return f"solid.ops[{i}].solid 에는 name·category 를 두지 않는다"
        why = problem(q)
        if why:
            return f"solid.ops[{i}]: {why}"
    return None


# ---------------------------------------------------------------- 불리언(BSP 다면체 불리언)
class _Poly:
    __slots__ = ("v", "n", "w")

    def __init__(self, v, n, w):
        self.v = v
        self.n = n
        self.w = w

    def flipped(self):
        return _Poly(self.v[::-1], (-self.n[0], -self.n[1], -self.n[2]), -self.w)


def _plane_of(pts):
    nx = ny = nz = 0.0
    k = len(pts)
    for i in range(k):
        a, b = pts[i], pts[(i + 1) % k]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    ln = sqrt(nx * nx + ny * ny + nz * nz)
    if ln < 1e-12:
        return None
    n = (nx / ln, ny / ln, nz / ln)
    cx = sum(q[0] for q in pts) / k
    cy = sum(q[1] for q in pts) / k
    cz = sum(q[2] for q in pts) / k
    return n, n[0] * cx + n[1] * cy + n[2] * cz


def _split(n, w, poly, cf, cb, fr, bk):
    """다각형을 평면 (n, w) 로 나눈다(csg 의 고전 규칙: 같은 평면은 법선 방향으로 앞/뒤 목록에)."""
    ts = []
    ptype = 0
    for q in poly.v:
        t = n[0] * q[0] + n[1] * q[1] + n[2] * q[2] - w
        c = 2 if t < -EPS else 1 if t > EPS else 0
        ptype |= c
        ts.append((t, c))
    if ptype == 0:
        (cf if _dot(n, poly.n) > 0 else cb).append(poly)
    elif ptype == 1:
        fr.append(poly)
    elif ptype == 2:
        bk.append(poly)
    else:
        f, b = [], []
        vs = poly.v
        k = len(vs)
        for i in range(k):
            j = (i + 1) % k
            ti, ci = ts[i]
            tj, cj = ts[j]
            vi, vj = vs[i], vs[j]
            if ci != 2:
                f.append(vi)
            if ci != 1:
                b.append(vi)
            if (ci | cj) == 3:
                t = ti / (ti - tj)
                x = (vi[0] + (vj[0] - vi[0]) * t, vi[1] + (vj[1] - vi[1]) * t, vi[2] + (vj[2] - vi[2]) * t)
                f.append(x)
                b.append(x)
        if len(f) >= 3:
            fr.append(_Poly(f, poly.n, poly.w))
        if len(b) >= 3:
            bk.append(_Poly(b, poly.n, poly.w))


class _Node:
    __slots__ = ("n", "w", "front", "back", "polys")

    def __init__(self):
        self.n = None
        self.w = 0.0
        self.front = None
        self.back = None
        self.polys = []


def _build(root, polys):
    stack = [(root, polys)]
    while stack:
        nd, ps = stack.pop()
        if not ps:
            continue
        if nd.n is None:
            nd.n, nd.w = ps[0].n, ps[0].w
        fr, bk = [], []
        for p in ps:
            _split(nd.n, nd.w, p, nd.polys, nd.polys, fr, bk)
        if fr:
            if nd.front is None:
                nd.front = _Node()
            stack.append((nd.front, fr))
        if bk:
            if nd.back is None:
                nd.back = _Node()
            stack.append((nd.back, bk))


def _nodes(root):
    out, stack = [], [root]
    while stack:
        nd = stack.pop()
        out.append(nd)
        if nd.front is not None:
            stack.append(nd.front)
        if nd.back is not None:
            stack.append(nd.back)
    return out


def _clip_polys(root, polys):
    """root 트리 안쪽(뒤)에 있는 부분을 지운 다각형들."""
    out = []
    stack = [(root, polys)]
    while stack:
        nd, ps = stack.pop()
        if nd.n is None:
            out.extend(ps)
            continue
        fr, bk = [], []
        for p in ps:
            _split(nd.n, nd.w, p, fr, bk, fr, bk)
        if nd.front is not None:
            stack.append((nd.front, fr))
        else:
            out.extend(fr)
        if nd.back is not None:
            stack.append((nd.back, bk))
    return out


def _clip_to(a, b):
    for nd in _nodes(a):
        nd.polys = _clip_polys(b, nd.polys)


def _invert(a):
    for nd in _nodes(a):
        nd.polys = [p.flipped() for p in nd.polys]
        if nd.n is not None:
            nd.n = (-nd.n[0], -nd.n[1], -nd.n[2])
            nd.w = -nd.w
        nd.front, nd.back = nd.back, nd.front


def _all(a):
    out = []
    for nd in _nodes(a):
        out.extend(nd.polys)
    return out


def _tree(polys):
    t = _Node()
    _build(t, polys)
    return t


def csg(pa, pb, op):
    """다각형 목록 둘의 불리언(op) -> 다각형 목록."""
    a, b = _tree(pa), _tree(pb)
    if op == "union":
        _clip_to(a, b)
        _clip_to(b, a)
        _invert(b)
        _clip_to(b, a)
        _invert(b)
        _build(a, _all(b))
        return _all(a)
    if op == "difference":
        _invert(a)
        _clip_to(a, b)
        _clip_to(b, a)
        _invert(b)
        _clip_to(b, a)
        _invert(b)
        _build(a, _all(b))
        _invert(a)
        return _all(a)
    _invert(a)
    _clip_to(b, a)
    _invert(b)
    _clip_to(a, b)
    _clip_to(b, a)
    _build(a, _all(b))
    _invert(a)
    return _all(a)


# ---------------------------------------------------------------- 다면체 <-> 볼록 다각형
def _basis(n):
    """법선 n 에 수직인 단위 벡터 둘 (e1, e2), e1 x e2 = n."""
    ref = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    e1 = _unit(_cross(ref, n))
    e2 = _cross(n, e1)
    return e1, e2


def _convex2(pts) -> bool:
    k = len(pts)
    for i in range(k):
        a, b, c = pts[i], pts[(i + 1) % k], pts[(i + 2) % k]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) < -1e-9:
            return False
    return True


def _trapezoids(loops2):
    """2D 고리들(바깥 + 구멍, 짝수-홀수 규칙)을 가로 띠의 볼록 사다리꼴(반시계)로 나눈다."""
    ys = sorted({q[1] for lp in loops2 for q in lp})
    edges = []
    for lp in loops2:
        k = len(lp)
        for i in range(k):
            a, b = lp[i], lp[(i + 1) % k]
            if a[1] != b[1]:
                edges.append((a, b) if a[1] < b[1] else (b, a))
    out = []
    for y0, y1 in zip(ys, ys[1:]):
        if y1 - y0 <= 1e-12:
            continue
        ym = (y0 + y1) / 2.0
        xs = []
        for a, b in edges:
            if a[1] < ym < b[1]:
                k = (b[0] - a[0]) / (b[1] - a[1])
                xs.append((a[0] + (ym - a[1]) * k, a[0] + (y0 - a[1]) * k, a[0] + (y1 - a[1]) * k))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            l, r = xs[i], xs[i + 1]
            q = [(l[1], y0), (r[1], y0), (r[2], y1), (l[2], y1)]
            d = []
            for pt in q:
                if not d or abs(d[-1][0] - pt[0]) > 1e-12 or abs(d[-1][1] - pt[1]) > 1e-12:
                    d.append(pt)
            if len(d) > 1 and abs(d[0][0] - d[-1][0]) <= 1e-12 and abs(d[0][1] - d[-1][1]) <= 1e-12:
                d.pop()
            if len(d) >= 3:
                out.append(d)
    return out


def mesh_polys(verts, faces) -> list:
    """다면체 -> 볼록 평면 다각형(_Poly) 목록. 볼록한 단일 고리 면은 그대로, 나머지는 면 평면 위 가로 띠 사다리꼴로 나눈다."""
    out = []
    for f in faces:
        loops = [[tuple(verts[i]) for i in lp] for lp in f]
        pl = _plane_of(loops[0])
        if pl is None:
            continue
        n, w = pl
        e1, e2 = _basis(n)
        l2 = [[(_dot(q, e1), _dot(q, e2)) for q in lp] for lp in loops]
        if len(loops) == 1 and _convex2(l2[0]):
            out.append(_Poly([tuple(float(c) for c in q) for q in loops[0]], n, w))
            continue
        for tz in _trapezoids(l2):
            out.append(_Poly([_add(_mul(e1, u), _add(_mul(e2, v), _mul(n, w))) for u, v in tz], n, w))
    return out


# ---------------------------------------------------------------- 다각형 수프 -> 면(같은 평면 합치기 + T 이음 닫기)
class _Welder:
    """허용 오차 안의 점을 한 꼭짓점으로 모은다(격자 칸 + 이웃 칸)."""

    def __init__(self, tol):
        self.tol = tol
        self.cell = tol * 4.0
        self.grid = {}
        self.pts = []
        self.seen = {}   # 같은 좌표 값 그대로 -> 번호(대부분의 점은 이웃 조각과 같은 값을 공유한다)

    def key(self, q):
        c = self.cell
        return (int((q[0] // c)), int((q[1] // c)), int((q[2] // c)))

    def add(self, q):
        hit = self.seen.get(q)
        if hit is not None:
            return hit
        kx, ky, kz = self.key(q)
        t2 = self.tol * self.tol
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for i in self.grid.get((kx + dx, ky + dy, kz + dz), ()):
                        p = self.pts[i]
                        if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2 <= t2:
                            self.seen[q] = i
                            return i
        self.seen[q] = len(self.pts)
        self.pts.append(q)
        self.grid.setdefault((kx, ky, kz), []).append(len(self.pts) - 1)
        return len(self.pts) - 1


def _on_seg(p, a, b, tol):
    """p 가 선분 ab 안쪽(양 끝 제외)에 tol 안으로 놓이면 매개변수 t, 아니면 None."""
    d = _sub(b, a)
    dd = _dot(d, d)
    if dd < 1e-18:
        return None
    t = _dot(_sub(p, a), d) / dd
    if t <= 1e-9 or t >= 1 - 1e-9:
        return None
    x = _add(a, _mul(d, t))
    if _dot(_sub(p, x), _sub(p, x)) > tol * tol:
        return None
    return t


def _insert_tjunctions(pts, loops, cand, tol):
    """고리들의 각 변 위에 놓인 후보 꼭짓점(cand: 번호 목록, x 순 정렬)을 끼운다."""
    xs = [pts[i][0] for i in cand]
    out = []
    for lp in loops:
        nl = []
        k = len(lp)
        for j in range(k):
            ia, ib = lp[j], lp[(j + 1) % k]
            nl.append(ia)
            a, b = pts[ia], pts[ib]
            lo, hi = min(a[0], b[0]) - tol, max(a[0], b[0]) + tol
            ylo, yhi = min(a[1], b[1]) - tol, max(a[1], b[1]) + tol
            zlo, zhi = min(a[2], b[2]) - tol, max(a[2], b[2]) + tol
            # x 범위 이진 탐색
            l, r = 0, len(xs)
            while l < r:
                m = (l + r) // 2
                if xs[m] < lo:
                    l = m + 1
                else:
                    r = m
            hits = []
            for c in range(l, len(xs)):
                if xs[c] > hi:
                    break
                iv = cand[c]
                if iv == ia or iv == ib:
                    continue
                q = pts[iv]
                if q[1] < ylo or q[1] > yhi or q[2] < zlo or q[2] > zhi:
                    continue
                t = _on_seg(q, a, b, tol)
                if t is not None:
                    hits.append((t, iv))
            hits.sort()
            for _, iv in hits:
                if nl[-1] != iv:
                    nl.append(iv)
        out.append(nl)
    return out


def _proj(pts, idx, e1, e2):
    return [(_dot(pts[i], e1), _dot(pts[i], e2)) for i in idx]


def _area2d(q):
    s = 0.0
    k = len(q)
    for i in range(k):
        s += q[i][0] * q[(i + 1) % k][1] - q[(i + 1) % k][0] * q[i][1]
    return s / 2.0


def _chain(edges, pts, e1, e2):
    """방향 있는 변(시작 -> 끝)들을 고리로 잇는다. 한 꼭짓점에서 나가는 변이 여럿이면 가장 오른쪽으로 꺾는 변."""
    out_of = {}
    for a, b in edges:
        out_of.setdefault(a, []).append(b)
    loops = []
    used = set()
    for a0, b0 in edges:
        if (a0, b0) in used:
            continue
        lp = [a0]
        used.add((a0, b0))
        prev, cur = a0, b0
        guard = 0
        while cur != a0 and guard < 100000:
            guard += 1
            lp.append(cur)
            cands = [n for n in out_of.get(cur, ()) if (cur, n) not in used]
            if not cands:
                break
            if len(cands) > 1:
                pc = _proj(pts, (prev, cur), e1, e2)
                din = (pc[1][0] - pc[0][0], pc[1][1] - pc[0][1])

                def turn(n, pc=pc, din=din):
                    q = _proj(pts, (n,), e1, e2)[0]
                    do = (q[0] - pc[1][0], q[1] - pc[1][1])
                    return atan2_deg(din[0] * do[1] - din[1] * do[0], din[0] * do[0] + din[1] * do[1])
                cands.sort(key=turn)
            nxt = cands[0]
            used.add((cur, nxt))
            prev, cur = cur, nxt
        if cur == a0 and len(lp) >= 3:
            loops.append(lp)
    return loops


def _drop_collinear(lp, pts, tol):
    changed = True
    lp = list(lp)
    while changed and len(lp) > 3:
        changed = False
        k = len(lp)
        for j in range(k):
            a, b, c = pts[lp[(j - 1) % k]], pts[lp[j]], pts[lp[(j + 1) % k]]
            d = _sub(c, a)
            dd = _dot(d, d)
            if dd < 1e-18:
                continue
            t = _dot(_sub(b, a), d) / dd
            x = _add(a, _mul(d, t))
            if 0.0 < t < 1.0 and _dot(_sub(b, x), _sub(b, x)) <= tol * tol:
                lp.pop(j)
                changed = True
                break
    return lp


def polys_mesh(polys):
    """BSP 결과 다각형들 -> (verts, faces). 같은 평면 조각을 합치고 T 이음을 닫는다. 비면 None."""
    W = _Welder(EPS)
    groups = []   # [법선, w, [고리(번호 목록), ...]]
    exact = {}    # (법선, w) 값 그대로 -> 무리 번호: 나눈 조각은 원래 면의 평면 값을 그대로 물려받아 대부분 여기서 바로 찾는다
    for p in polys:
        lp = _dedupe_ring([W.add(q) for q in p.v])
        if len(set(lp)) < 3:
            continue
        key = (p.n, p.w)
        gi = exact.get(key)
        if gi is None:
            for k, g in enumerate(groups):
                if _dot(g[0], p.n) > 1.0 - 1e-9 and abs(g[1] - p.w) <= EPS:
                    gi = k
                    break
            else:
                groups.append([p.n, p.w, []])
                gi = len(groups) - 1
            exact[key] = gi
        groups[gi][2].append(lp)
    pts = W.pts
    merged = []   # (법선, [고리])
    for n, _w, loops in groups:
        e1, e2 = _basis(n)
        loops = [lp for lp in loops if abs(_area2d(_proj(pts, lp, e1, e2))) > 1e-9]
        if not loops:
            continue
        cand = sorted({i for lp in loops for i in lp}, key=lambda i: (pts[i][0], i))
        loops = _insert_tjunctions(pts, loops, cand, EPS)
        cnt = {}
        for lp in loops:
            k = len(lp)
            for j in range(k):
                e = (lp[j], lp[(j + 1) % k])
                cnt[e] = cnt.get(e, 0) + 1
        edges = []
        for (a, b), c in cnt.items():
            r = cnt.get((b, a), 0)
            for _ in range(c - min(c, r)):
                edges.append((a, b))
        edges.sort()
        out = []
        for lp in _chain(edges, pts, e1, e2):
            lp = _drop_collinear(lp, pts, EPS)
            if len(lp) >= 3 and abs(_area2d(_proj(pts, lp, e1, e2))) > 1e-6:
                out.append(lp)
        if out:
            merged.append((n, out))
    if not merged:
        return None
    allv = sorted({i for _, ls in merged for lp in ls for i in lp}, key=lambda i: (pts[i][0], i))
    faces = []
    for n, ls in merged:
        ls = _insert_tjunctions(pts, ls, allv, EPS)
        e1, e2 = _basis(n)
        outers, holes = [], []
        for lp in ls:
            (outers if _area2d(_proj(pts, lp, e1, e2)) > 0 else holes).append(lp)
        fl = [[o] for o in outers]
        for h in holes:
            q = _proj(pts, h[:1], e1, e2)[0]
            best, ba = None, None
            for fi, o in enumerate(outers):
                o2 = _proj(pts, o, e1, e2)
                if SO.point_in(q, o2) >= 0:
                    a = abs(_area2d(o2))
                    if ba is None or a < ba:
                        best, ba = fi, a
            if best is not None:
                fl[best].append(h)
        faces.extend(fl)
    # 쓰는 꼭짓점만 처음 나온 순서로 다시 번호를 매긴다
    remap, verts = {}, []
    out_faces = []
    for f in faces:
        nf = []
        for lp in f:
            nl = []
            for i in lp:
                if i not in remap:
                    remap[i] = len(verts)
                    verts.append(pts[i])
                nl.append(remap[i])
            nf.append(nl)
        out_faces.append(nf)
    return verts, out_faces


def _bbox(verts):
    return (min(v[0] for v in verts), min(v[1] for v in verts), min(v[2] for v in verts),
            max(v[0] for v in verts), max(v[1] for v in verts), max(v[2] for v in verts))


def _apart(a, b) -> bool:
    return any(a[k + 3] < b[k] - EPS or b[k + 3] < a[k] - EPS for k in range(3))


MERGE_AT = 1000   # 연산 사이 조각이 이보다 많으면 같은 평면 조각을 합쳐 다시 시작한다(조각 수가 연산마다 불어나지 않게)


def boolean_mesh(base, ops, operand_mesh):
    """base = (verts, faces), ops = [{"op","solid"}], operand_mesh(형상 사전) -> (verts, faces). 비면 None.
    잇단 같은 빼기(또는 합치기)의 도구들이 서로 경계 상자부터 떨어져 있으면 도구의 합집합 = 다각형 이어 붙이기이므로 한 번에 계산한다
    (볼트 구멍 여러 개: A - t1 - t2 - ... = A - (t1 ∪ t2 ∪ ...)). 결과는 순서대로 하나씩 한 것과 같은 입체다."""
    polys = mesh_polys(*base)
    i = 0
    while i < len(ops):
        op = ops[i]["op"]
        meshes = [operand_mesh(ops[i]["solid"])]
        boxes = [_bbox(meshes[0][0])]
        j = i + 1
        if op != "intersection":
            while j < len(ops) and ops[j]["op"] == op:
                m = operand_mesh(ops[j]["solid"])
                bb = _bbox(m[0])
                if not all(_apart(bb, x) for x in boxes):
                    break
                meshes.append(m)
                boxes.append(bb)
                j += 1
        tool = [q for m in meshes for q in mesh_polys(*m)]
        if len(polys) + len(tool) > CSG_POLYS_LIMIT:
            raise ValueError(f"불리언 입력 다각형이 {CSG_POLYS_LIMIT}개를 넘는다(res 를 줄이세요)")
        polys = csg(polys, tool, op)
        if not polys:
            return None
        i = j
        if i < len(ops) and len(polys) > MERGE_AT:
            m = polys_mesh(polys)
            if m is None:
                return None
            polys = mesh_polys(*m)
    return polys_mesh(polys)
