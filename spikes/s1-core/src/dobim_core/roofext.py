# SPDX-License-Identifier: MPL-2.0
"""압출 지붕(roof form "extrusion", W2 여덟 번째 PR, ARC-19 여유 몫)의 순수 형상: 입면 프로파일을 길이 방향으로 돌출한다.

입력: axis [x1, y1, x2, y2] = 돌출 방향 선(시작 -> 끝, 길이 = 돌출 길이), profile [[s, z], ...] = 단면 위 꺾은선(s = axis 진행 방향 왼쪽으로의
수평 거리 mm, z = 레벨 + base_offset 기준 높이 mm, s 는 엄격히 늘어난다). 곡면(볼트·아치) 지붕은 촘촘한 꺾은선으로 준다.
윗면 = 꺾은선의 선분마다 axis 방향으로 돌출한 평면 사각형. 평면 외곽 = s 범위 x 돌출 길이의 직사각형(boundary 와 같다).
roof_geom 과 같은 꼴의 dict 를 돌려준다(도면·IFC·벽 상단 붙이기가 그대로 쓴다). 안쪽 접힘선: 양쪽이 내려가면 용마루, 올라가면 골, 나머지(꺾임)는 추녀마루 목록.
libm 을 쓰지 않는다(sqrt 만, 물매 각도는 sincos_deg 이분법). 이 모듈은 kinds 를 import 하지 않는다(sincos 는 인자로 받는다).
"""
from __future__ import annotations

from math import sqrt

PROFILE_MAX = 65        # 프로파일 꼭짓점 수 상한
SEG_SLOPE_MIN = 1e-3    # 선분 물매(tan) 하한: 평평한 선분은 물이 흐를 방향이 없다
SLOPE_MAX_TAN = 3.7320508075688776   # tan 75도(지붕 물매 상한)


def frame(axis):
    """(시작점, 진행 단위 벡터, 왼쪽 단위 벡터, 길이)."""
    x1, y1, x2, y2 = (float(v) for v in axis)
    dx, dy = x2 - x1, y2 - y1
    ln = sqrt(dx * dx + dy * dy)
    u = (dx / ln, dy / ln)
    return (x1, y1), u, (-u[1], u[0]), ln


def rect(axis, profile):
    """평면 외곽(반시계 4점): s 최소·최대 x 돌출 길이."""
    o, u, w, ln = frame(axis)
    s0, s1 = float(profile[0][0]), float(profile[-1][0])

    def P(t, s):
        return [o[0] + u[0] * t + w[0] * s, o[1] + u[1] * t + w[1] * s]
    return [P(0.0, s0), P(ln, s0), P(ln, s1), P(0.0, s1)]


def problem(axis, profile) -> str | None:
    """축·프로파일이 지붕을 만들 수 있는가(안 되면 이유 문장)."""
    if not isinstance(axis, list) or len(axis) != 4:
        return "axis 는 [x1, y1, x2, y2] 여야 한다"
    if (axis[2] - axis[0]) ** 2 + (axis[3] - axis[1]) ** 2 < 1.0:
        return "돌출 길이(axis 길이)가 1mm 보다 짧다"
    if not isinstance(profile, list) or not 2 <= len(profile) <= PROFILE_MAX:
        return f"프로파일은 꼭짓점 2~{PROFILE_MAX}개여야 한다"
    for i in range(len(profile) - 1):
        (sa, za), (sb, zb) = profile[i], profile[i + 1]
        if sb - sa < 1.0:
            return f"프로파일의 s 는 1mm 이상씩 늘어나야 한다(꼭짓점 {i + 1})"
        k = abs(zb - za) / (sb - sa)
        if k < SEG_SLOPE_MIN:
            return f"프로파일 선분 {i} 가 평평하다(물이 흐를 방향이 없다: 아주 조금이라도 기울인다)"
        if k > SLOPE_MAX_TAN:
            return f"프로파일 선분 {i} 의 물매가 75도를 넘는다"
    return None


def _deg_of_tan(k, sincos_deg) -> float:
    """tan -> 도(0~75, 이분법 60회: 결정적)."""
    lo, hi = 0.0, 75.0
    for _ in range(60):
        m = (lo + hi) / 2.0
        sn, cs = sincos_deg(m)
        if sn / cs < k:
            lo = m
        else:
            hi = m
    return round((lo + hi) / 2.0, 9)


def geom(axis, profile, base_offset, sincos_deg) -> dict:
    """roof_geom 과 같은 꼴의 dict."""
    o, u, w, ln = frame(axis)
    z0 = float(base_offset)
    prof = [(float(s), float(z) + z0) for s, z in profile]

    def P(t, s, z):
        return (o[0] + u[0] * t + w[0] * s, o[1] + u[1] * t + w[1] * s, z)
    faces, planes, arrows, fpitch = [], [], [], []
    for i in range(len(prof) - 1):
        (sa, za), (sb, zb) = prof[i], prof[i + 1]
        k = (zb - za) / (sb - sa)   # s 방향 기울기
        fc = [P(0.0, sa, za), P(ln, sa, za), P(ln, sb, zb), P(0.0, sb, zb)]   # 반시계(w 가 u 의 왼쪽)
        faces.append(fc)
        # z = za + k (s - sa), s = w . (p - o)
        a, b = k * w[0], k * w[1]
        planes.append((a, b, za - k * sa - a * o[0] - b * o[1]))
        d = (-w[0], -w[1]) if k > 0 else (w[0], w[1])   # 물이 흐르는 방향(낮은 쪽)
        c = P(ln / 2.0, (sa + sb) / 2.0, 0.0)
        arrows.append(((c[0], c[1]), d))
        fpitch.append(_deg_of_tan(abs(k), sincos_deg))
    ridges, hips, valleys = [], [], []
    for i in range(1, len(prof) - 1):
        s, z = prof[i]
        ka = prof[i][1] - prof[i - 1][1]
        kb = prof[i + 1][1] - prof[i][1]
        seg = (P(0.0, s, z), P(ln, s, z))
        if ka > 0 and kb < 0:
            ridges.append(seg)
        elif ka < 0 and kb > 0:
            valleys.append(seg)
        else:
            hips.append(seg)
    outline = [(q[0], q[1]) for q in (P(0.0, prof[0][0], 0.0), P(ln, prof[0][0], 0.0), P(ln, prof[-1][0], 0.0), P(0.0, prof[-1][0], 0.0))]
    perim = [P(0.0, prof[0][0], prof[0][1]), P(ln, prof[0][0], prof[0][1])]   # 처마 윤곽 반시계 순서: s 최소 변, 끝 마구리(s 증가), s 최대 변, 시작 마구리(s 감소)
    perim += [P(ln, s, z) for s, z in prof[1:-1]]
    perim += [P(ln, prof[-1][0], prof[-1][1]), P(0.0, prof[-1][0], prof[-1][1])]
    perim += [P(0.0, s, z) for s, z in reversed(prof[1:-1])]
    zs = [z for _, z in prof]
    return {"faces": faces, "ridges": ridges, "hips": hips, "valleys": valleys, "perimeter": perim, "arrows": arrows, "outline": outline, "planes": planes,
            "pieces": list(zip(faces, planes)), "fpitch": fpitch, "zlow": min(zs), "ztop": max(zs)}
