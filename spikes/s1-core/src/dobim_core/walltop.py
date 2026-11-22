# SPDX-License-Identifier: MPL-2.0
"""벽 상단 윤곽(top_profile, 21단계)의 순수 함수: 검사·값 계산·자르기·면적과, 지붕·경사 판·슬래브에 붙이는 윤곽 계산(wall.attach_top).

벽 상단 윤곽은 축선 따라 거리 s(축 시작점 0 ~ 축 길이)와 레벨 기준 높이 z 의 점 목록 [[s, z], ...] 이다.
  - s 는 줄지 않는다. 같은 s 가 이웃한 두 점은 수직 단(step)이다(세 점 이상 같은 s 는 안 된다). 첫·끝 두 점은 단이 아니다.
  - 점 사이는 직선(선형 보간)이다. 벽 형상 범위가 축 밖으로 나가면(L 조인 연장) 끝 점의 높이를 그대로 늘인다.
벽의 몸체는 지역 (u, z) 수직 윤곽(바닥 0 ~ 상단 윤곽)을 두께 방향으로 돌출한 것이다(평면 윤곽이 늘 직사각형이라 정확하다).
이 모듈은 ifcopenshell 을 import 하지 않고 kinds 를 맨 위에서 import 하지 않는다(kinds 가 이 모듈을 쓴다: 지붕 형상만 늦게 읽는다).
"""
from __future__ import annotations

from math import isfinite

from dobim_core import solids as SO

TOP_PTS_MAX = 256       # 상단 윤곽 점 수 상한
TOP_S_TOL = 0.01        # 첫 점 s = 0, 끝 점 s = 축 길이를 이만큼(mm) 안에서 받는다
TOP_EPS = 1e-6


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v)


def profile_problem(v, length: float) -> str | None:
    """상단 윤곽 값이 올바른가(축 길이 length). 문제가 있으면 이유 문자열."""
    if not isinstance(v, list) or not 2 <= len(v) <= TOP_PTS_MAX:
        return f"상단 윤곽은 점 2~{TOP_PTS_MAX}개여야 한다"
    for q in v:
        if not isinstance(q, list) or len(q) != 2 or not all(_num(x) for x in q):
            return "상단 윤곽의 점은 [s, z] 숫자 쌍이어야 한다"
        if q[1] <= 0:
            return "상단 윤곽의 높이 z 는 0 보다 커야 한다"
    if abs(v[0][0]) > TOP_S_TOL or abs(v[-1][0] - length) > TOP_S_TOL:
        return f"상단 윤곽은 s = 0 에서 시작해 s = 축 길이({length:.3f}) 에서 끝나야 한다"
    for i in range(len(v) - 1):
        ds = v[i + 1][0] - v[i][0]
        if ds < 0:
            return "상단 윤곽의 s 는 줄지 않아야 한다"
        if ds == 0:
            if i == 0 or i == len(v) - 2:
                return "상단 윤곽의 첫·끝 구간은 수직 단일 수 없다"
            if v[i + 1][1] == v[i][1]:
                return "상단 윤곽에 같은 점이 두 번 있다"
            if i + 2 < len(v) and v[i + 2][0] == v[i][0]:
                return "상단 윤곽에 같은 s 가 세 점 이상이다"
    return None


BASE_OFFSET_MAX = 100000.0   # 벽 바닥 오프셋(22단계)의 절댓값 상한 mm


def base(p: dict) -> float:
    """벽 바닥의 레벨 기준 높이(22단계 base_offset, 상용 도구의 Base Offset). 키가 없으면 0(이전과 같다)."""
    return float(p.get("base_offset", 0.0))


def top_rel(p: dict) -> float:
    """벽 윗면의 레벨 기준 높이: 상단 윤곽이 있으면 가장 높은 점, 없으면 바닥 오프셋 + height(height 는 바닥에서 잰다)."""
    return zmax(p["top_profile"]) if "top_profile" in p else base(p) + p["height"]


def norm(v) -> list:
    """저장 모양(float 쌍 목록)."""
    return [[float(q[0]), float(q[1])] for q in v]


def eval_at(prof, u: float, right: bool = True) -> float:
    """u 에서의 상단 높이. 단(같은 s 두 점) 위에서는 right 면 오른쪽(큰 u 쪽) 값, 아니면 왼쪽 값. 범위 밖은 끝 점 높이."""
    if u <= prof[0][0]:
        return float(prof[0][1])
    if u >= prof[-1][0]:
        return float(prof[-1][1])
    for i in range(len(prof) - 1):
        s0, z0 = prof[i]
        s1, z1 = prof[i + 1]
        if s1 <= s0:
            continue
        if (s0 <= u < s1) if right else (s0 < u <= s1):
            return z0 + (z1 - z0) * (u - s0) / (s1 - s0)
    return float(prof[-1][1])


def clip(prof, a: float, b: float) -> list:
    """[a, b] 로 자른 상단 윤곽 점 목록 [(u, z), ...]: 첫 점 (a, 오른쪽 값), 끝 점 (b, 왼쪽 값), 사이의 점(단 포함). 같은 점이 이어지면 하나로."""
    pts = [(float(a), eval_at(prof, a, True))]
    pts += [(float(s), float(z)) for s, z in prof if a < s < b]
    pts.append((float(b), eval_at(prof, b, False)))
    out = []
    for q in pts:
        if not out or abs(q[0] - out[-1][0]) > TOP_EPS or abs(q[1] - out[-1][1]) > TOP_EPS:
            out.append(q)
    return out


def loop(prof, start: float, end: float, z0: float = 0.0) -> list:
    """지역 (u, z) 수직 윤곽(반시계): 바닥 z0 의 [start, end] 위로 상단 윤곽까지. IFC 몸체의 돌출 프로필이다."""
    return [(float(start), float(z0)), (float(end), float(z0))] + clip(prof, start, end)[::-1]


def zmax(prof, a: float | None = None, b: float | None = None) -> float:
    pts = prof if a is None else clip(prof, a, b)
    return max(float(q[1]) for q in pts)


def zmin(prof, a: float | None = None, b: float | None = None) -> float:
    pts = prof if a is None else clip(prof, a, b)
    return min(float(q[1]) for q in pts)


def area(prof, a: float, b: float) -> float:
    """[a, b] 구간에서 바닥(0)부터 상단 윤곽까지의 입면 면적(mm²)."""
    cp = clip(prof, a, b)
    return sum((u1 - u0) * (z0 + z1) / 2.0 for (u0, z0), (u1, z1) in zip(cp, cp[1:]))


def segments(cp):
    """자른 윤곽의 기울어진(또는 수평) 구간들 (u0, z0, u1, z1). 단(수직)은 뺀다."""
    return [(u0, z0, u1, z1) for (u0, z0), (u1, z1) in zip(cp, cp[1:]) if u1 - u0 > TOP_EPS]


def ivs_between(cp, lo: float, hi: float) -> list:
    """상단 높이 t(u) 가 lo < t < hi 인 u 구간들(자른 윤곽 cp 위, 이어지는 구간은 합친다)."""
    out = []
    for u0, z0, u1, z1 in segments(cp):
        ta, tb = 0.0, 1.0
        dz = z1 - z0
        for bound, keep_gt in ((lo, True), (hi, False)):
            if abs(dz) < 1e-12:
                ok = z0 > bound if keep_gt else z0 < bound
                if not ok:
                    ta, tb = 1.0, 0.0
                continue
            t = (bound - z0) / dz
            if (dz > 0) == keep_gt:
                ta = max(ta, t)
            else:
                tb = min(tb, t)
        if tb - ta <= 1e-12:
            continue
        a, b = u0 + ta * (u1 - u0), u0 + tb * (u1 - u0)
        if b - a <= TOP_EPS:
            continue
        if out and abs(out[-1][1] - a) <= TOP_EPS:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def split(prof, at: float, length: float):
    """축 at 에서 나눈 두 벽의 상단 윤곽(시작 쪽, 끝 쪽: 끝 쪽은 s 를 at 만큼 당긴다)."""
    w = [[u, z] for u, z in clip(prof, 0.0, at)]
    n = [[u - at, z] for u, z in clip(prof, at, length)]
    w[0][0], w[-1][0], n[0][0], n[-1][0] = 0.0, float(at), 0.0, float(length - at)
    return w, n


def under_problem(prof, openings=(), boxes=()) -> str | None:
    """모양 개구부(윤곽)·호스팅 개구부 상자 (u0, u1, z0, z1) 가 상단 윤곽 아래에 있는가. 윤곽 꼭짓점과 상자 위 모서리를 본다
    (상단 윤곽이 오목하게 꺾이는 점은 개구부 꼭대기 높이 이상이어야 한다: 보수적인 근사)."""
    def chk(pts, u0, u1, ztop):
        for u, z in pts:
            if z > min(eval_at(prof, u, True), eval_at(prof, u, False)) + 1e-6:
                return False
        return all(z >= ztop - 1e-6 for s, z in prof if u0 < s < u1)
    for i, o in enumerate(openings):
        u0, u1, _, _, zt = SO.opening_box(o)
        if not chk(SO.opening_loop(o), u0, u1, zt):
            return f"{i + 1}번 모양 개구부가 벽 상단 윤곽 위로 나간다"
    for u0, u1, _z0, z1 in boxes:
        if not chk(((u0, z1), (u1, z1)), u0, u1, z1):
            return "호스팅된 문이 벽 상단 윤곽 위로 나간다"
    return None


# ---------------------------------------------------------------- 붙이기(wall.attach_top)
def _plane3(loop3):
    """3D 평면 다각형 -> (nx, ny, nz, d): nx x + ny y + nz z = d (newell 법선, 길이 정규화 없음)."""
    n = SO.newell(loop3)
    p = loop3[0]
    return (n[0], n[1], n[2], n[0] * p[0] + n[1] * p[1] + n[2] * p[2])


def target_faces(kind: str, p: dict, face: str, dz: float) -> list:
    """대상 요소의 붙일 면들: [(평면 다각형 고리들 [(x,y)...](첫 고리 바깥, 나머지 구멍), (a, b, c): z = a x + b y + c)].
    face "bottom" 은 아래를 향한 면, "top" 은 위를 향한 면. dz 는 대상 레벨 높이 - 벽 레벨 높이(z 를 벽 레벨 기준으로 옮긴다)."""
    out = []

    def add(loops, a, b, c):
        out.append(([[(float(q[0]), float(q[1])) for q in lp] for lp in loops], (a, b, c + dz)))

    if kind == "roof":
        from dobim_core.kinds import roof_geom   # 늦게 읽는다(kinds 가 이 모듈을 쓴다)
        q = {k: p[k] for k in ("boundary", "form", "pitch", "overhang", "base_offset", "ridge", "dir", "slopes") if k in p}
        gm = roof_geom(q)
        sub = 0.0 if face == "top" else float(p["thickness"])
        for fc, (a, b, c) in zip(gm["faces"], gm["planes"]):
            add([[(x, y) for x, y, _ in fc]], a, b, c - sub)
        return out
    if kind == "slab":
        z = float(p["top_offset"]) - (0.0 if face == "top" else float(p["thickness"]))
        add([p["boundary"]] + list(p.get("openings", ())), 0.0, 0.0, z)
        return out
    verts, faces = SO.solid_mesh(p)   # 솔리드: 위(아래)를 향한 평면 면마다
    for fc in faces:
        lps = [[verts[i] for i in lp] for lp in fc]
        nx, ny, nz, d = _plane3(lps[0])
        ln = (nx * nx + ny * ny + nz * nz) ** 0.5
        if ln < 1e-12 or (nz / ln < 1e-6 if face == "top" else nz / ln > -1e-6):
            continue   # 수직 면·반대를 향한 면
        add([[(v[0], v[1]) for v in lp] for lp in lps], -nx / nz, -ny / nz, d / nz)
    return out


def attach_profile_arc(ai, height: float, faces: list, face: str):
    """곡선 벽(W2 ARC-04) 축 위의 상단 윤곽: 호를 도면 조각(facet) 경계에서 나눈 현마다 attach_profile 을 재고 현 위 거리 s 를 호 위 거리 u 로
    옮겨 잇는다(도면·IFC 가 조각마다 곧게 그리는 것과 같은 근사, 현과 호의 차는 arcgeom.FACET_SAG 이하). 덮인 구간이 없으면 (None, [])."""
    from dobim_core import arcgeom as AG   # 순수 모듈(늦게 읽는다)
    n = AG.length(ai)
    us = AG.breaks(ai, 0.0, n)
    pts, covered, hit = [], [], False
    for ua, ub in zip(us, us[1:]):
        pa, pb = AG.point(ai, ua), AG.point(ai, ub)
        chord = ((pb[0] - pa[0]) ** 2 + (pb[1] - pa[1]) ** 2) ** 0.5
        prof, cov = attach_profile([pa[0], pa[1], pb[0], pb[1]], height, faces, face)
        k = (ub - ua) / chord
        seg = [[ua, float(height)], [ub, float(height)]] if prof is None else [[ua + s * k, z] for s, z in prof]
        seg[0][0], seg[-1][0] = ua, ub
        hit = hit or prof is not None
        for a, b in cov:
            a, b = ua + a * k, ua + b * k
            if covered and abs(covered[-1][1] - a) < 1e-3:
                covered[-1] = (covered[-1][0], b)
            else:
                covered.append((a, b))
        for s, z in seg:
            q = (round(s, 3) + 0.0, round(z, 3) + 0.0)
            if pts and abs(pts[-1][0] - q[0]) < 1e-9 and abs(pts[-1][1] - q[1]) < 1e-3:
                continue
            pts.append(q)
    if not hit:
        return None, []
    simp = []   # 한 직선 위의 가운데 점을 뺀다(단은 남는다): attach_profile 과 같은 규칙
    for q in pts:
        while len(simp) >= 2:
            (sa, za), (sb, zb) = simp[-2], simp[-1]
            if sb - sa > 1e-9 and q[0] - sb > 1e-9 and abs((zb - za) * (q[0] - sb) - (q[1] - zb) * (sb - sa)) <= 1e-6 * (q[0] - sa):
                simp.pop()
            else:
                break
        simp.append(q)
    simp[0] = (0.0, simp[0][1])
    simp[-1] = (n, simp[-1][1])
    return [[s, z] for s, z in simp], [[round(a, 3) + 0.0, round(b, 3) + 0.0] for a, b in covered]


def _inside(pt, loops) -> bool:
    if SO.point_in(pt, loops[0]) < 0:
        return False
    return all(SO.point_in(pt, h) < 0 for h in loops[1:])


def attach_profile(axis, height: float, faces: list, face: str):
    """축선 [x1,y1,x2,y2] 위에서 대상 면들의 높이로 상단 윤곽을 만든다. 면이 없는 구간은 height 그대로(그 경계는 수직 단).
    아랫면(bottom)은 겹치는 면 중 가장 낮은 면, 윗면(top)은 가장 높은 면을 쓴다. (윤곽, 덮인 구간들) 을 돌려준다. 덮인 구간이 없으면 (None, [])."""
    x1, y1, x2, y2 = (float(v) for v in axis)
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / length, dy / length
    spans = []   # (s0, s1, 기울기, 절편): z(s) = k s + m
    for loops, (a, b, c) in faces:
        cuts = {0.0, length}
        for lp in loops:
            n = len(lp)
            for i in range(n):
                (px, py), (qx, qy) = lp[i], lp[(i + 1) % n]
                ex, ey = qx - px, qy - py
                den = ux * ey - uy * ex
                if abs(den) < 1e-12:
                    continue   # 축선과 나란한 변(축선 위에 겹친 변은 이웃 변의 교점이 구간 끝을 준다)
                wx, wy = px - x1, py - y1
                s = (wx * ey - wy * ex) / den
                t = (wx * uy - wy * ux) / den
                if -1e-9 <= t <= 1 + 1e-9 and 0.0 < s < length:
                    cuts.add(s)
        cs = sorted(cuts)
        for s0, s1 in zip(cs, cs[1:]):
            if s1 - s0 < 1e-6:
                continue
            sm = (s0 + s1) / 2.0
            if _inside((x1 + sm * ux, y1 + sm * uy), loops):
                k, m = a * ux + b * uy, a * x1 + b * y1 + c
                if spans and spans[-1][1] == s0 and spans[-1][2:] == (k, m):
                    spans[-1] = (spans[-1][0], s1, k, m)
                else:
                    spans.append((s0, s1, k, m))
    if not spans:
        return None, []
    bps = {0.0, length}
    for s0, s1, _, _ in spans:
        bps.update((s0, s1))
    for i, (a0, a1, ka, ma) in enumerate(spans):   # 겹친 두 면의 교차점(아래·위 껍질이 바뀌는 곳)
        for b0, b1, kb, mb in spans[i + 1:]:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi - lo > 1e-6 and abs(ka - kb) > 1e-12:
                s = (mb - ma) / (ka - kb)
                if lo < s < hi:
                    bps.add(s)
    bs = sorted(bps)
    pick = min if face == "bottom" else max
    pieces = []   # (s0, s1, z0, z1)
    covered = []
    for s0, s1 in zip(bs, bs[1:]):
        if s1 - s0 < 1e-6:
            continue
        sm = (s0 + s1) / 2.0
        act = [(k, m) for a0, a1, k, m in spans if a0 <= sm <= a1]
        if act:
            k, m = pick(act, key=lambda km: km[0] * sm + km[1])
            pieces.append((s0, s1, k * s0 + m, k * s1 + m))
            if covered and abs(covered[-1][1] - s0) < 1e-6:
                covered[-1] = (covered[-1][0], s1)
            else:
                covered.append((s0, s1))
        else:
            pieces.append((s0, s1, float(height), float(height)))
    pts = []
    for s0, s1, z0, z1 in pieces:
        for q in ((s0, z0), (s1, z1)):
            q = (round(q[0], 3) + 0.0, round(q[1], 3) + 0.0)
            if pts and abs(pts[-1][0] - q[0]) < 1e-9 and abs(pts[-1][1] - q[1]) < 1e-3:
                continue
            pts.append(q)
    simp = []   # 한 직선 위의 가운데 점을 뺀다(단은 남는다)
    for q in pts:
        while len(simp) >= 2:
            (sa, za), (sb, zb) = simp[-2], simp[-1]
            if sb - sa > 1e-9 and q[0] - sb > 1e-9 and abs((zb - za) * (q[0] - sb) - (q[1] - zb) * (sb - sa)) <= 1e-6 * (q[0] - sa):
                simp.pop()
            else:
                break
        simp.append(q)
    simp[0] = (0.0, simp[0][1])
    simp[-1] = (length, simp[-1][1])
    return [[s, z] for s, z in simp], [[round(a, 3) + 0.0, round(b, 3) + 0.0] for a, b in covered]
