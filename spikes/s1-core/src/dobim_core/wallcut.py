# SPDX-License-Identifier: MPL-2.0
"""벽 잘라내기(`cuts`, 23단계)의 순수 함수: 다른 부재(보·인방 등)가 벽에서 빼낸 부피(상용 도구의 형상 결합 Join Geometry 에서 구조 부재가 벽을 자르는 것).

저장 모양: 벽 선택 파라미터 `cuts` = [[u0, u1, v0, v1, z0, z1], ...] (1~CUTS_MAX 개).
  u: 축선 시작점에서 축 방향 거리(mm), v: 축선에서 왼쪽(진행 방향 왼쪽 = 지역 +y) 거리(mm, 벽 몸체는 -두께/2 ~ +두께/2),
  z: 벽 레벨 기준 높이(mm, 모양 개구부 sill·상단 윤곽 z 와 같은 기준). 벽 지역 축에 나란한 상자다.
값은 그 순간의 대상 형상을 옮겨 적은 스냅숏이다(wall.cut_by, 설계 메모 25.1): 대상이 바뀌어도 벽은 따라가지 않는다(벽 상단 붙이기와 같은 판단).
형상에 쓸 때는 지금의 벽 형상 범위(조인 자르기·두께·바닥·윗면)로 자른다: 벽 밖으로 나간 부분은 뜻이 없다.
삼각함수는 쓰지 않는다(sqrt 와 사칙연산만): 같은 입력이면 어느 플랫폼에서나 같은 값이다.
이 모듈은 kinds 를 import 하지 않는다(kinds 가 이 모듈을 쓴다).
"""
from __future__ import annotations

from math import isfinite, sqrt

CUTS_MAX = 64          # 벽 하나의 잘라내기 상자 수 상한
CUT_MIN = 0.01         # 상자 한 변의 최소 길이(mm): 이보다 얇은 조각은 버린다
ALIGN_TOL = 0.01       # 대상 면이 벽 지역 축에 나란한지 볼 때의 허용 오차(mm)
VOL_RTOL = 1e-6        # 대상 부피와 경계 상자 부피가 같은지 볼 때의 상대 허용 오차
FACE_PAD = 10.0        # IFC 빼기 상자를 벽 면과 겹치는 쪽으로 더 내민 길이(mm): 같은 평면 빼기의 수치 잡음을 피한다(모양 개구부와 같은 값)


def _num(v) -> bool:
    if type(v) is int:
        return True
    return type(v) is float and isfinite(v)


def cuts_problem(v) -> str | None:
    """저장 모양 검사. 문제가 있으면 이유 문자열."""
    if not isinstance(v, list) or not 1 <= len(v) <= CUTS_MAX:
        return f"잘라내기 상자는 1~{CUTS_MAX} 개다"
    for i, b in enumerate(v):
        if not isinstance(b, list) or len(b) != 6 or not all(_num(x) for x in b):
            return f"{i} 번 상자는 수 6개 [u0, u1, v0, v1, z0, z1] 이다"
        if b[1] - b[0] < CUT_MIN or b[3] - b[2] < CUT_MIN or b[5] - b[4] < CUT_MIN:
            return f"{i} 번 상자의 범위가 비었다(u0 < u1, v0 < v1, z0 < z1)"
    return None


def norm(v) -> list:
    """저장 모양(float 목록의 목록)."""
    return [[float(x) for x in b] for b in v]


def clip(cuts, u0, u1, half, zb, zt) -> tuple:
    """상자들을 벽 형상 범위 u0..u1, -half..half, zb..zt(같은 기준)로 자른다. 빈 것은 버린다. ((u0, u1, v0, v1, z0, z1), ...)."""
    out = []
    for b in cuts:
        q = (max(u0, b[0]), min(u1, b[1]), max(-half, b[2]), min(half, b[3]), max(zb, b[4]), min(zt, b[5]))
        if q[1] - q[0] >= CUT_MIN and q[3] - q[2] >= CUT_MIN and q[5] - q[4] >= CUT_MIN:
            out.append(q)
    return tuple(out)


def shift_z(cuts, dz) -> tuple:
    return tuple((b[0], b[1], b[2], b[3], b[4] + dz, b[5] + dz) for b in cuts)


def split(cuts, at) -> tuple:
    """벽 분할(wall.split): 분할점 at 에서 상자를 둘로 나눈다. (시작 쪽 목록, 끝 쪽 목록(u 를 at 만큼 당긴다)). 빈 쪽은 []."""
    a, b = [], []
    for q in cuts:
        if q[0] < at - CUT_MIN:
            a.append([q[0], min(q[1], at), q[2], q[3], q[4], q[5]])
        if q[1] > at + CUT_MIN:
            b.append([max(q[0], at) - at, q[1] - at, q[2], q[3], q[4], q[5]])
    return a, b


# ---------------------------------------------------------------- 대상 형상 -> 벽 지역 상자 (wall.cut_by)
def to_local(axis, verts, dz) -> list:
    """세계(대상 레벨 기준) 꼭짓점 -> 벽 지역 (u, v, z). dz = 대상 레벨 높이 - 벽 레벨 높이."""
    x1, y1, x2, y2 = (float(t) for t in axis)
    ln = sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    c, s = (x2 - x1) / ln, (y2 - y1) / ln
    return [((x - x1) * c + (y - y1) * s, -(x - x1) * s + (y - y1) * c, z + dz) for x, y, z in verts]


def _volume(pts, faces) -> float:
    """닫힌 다면체의 부피(발산 정리: 고리마다 부채꼴 삼각형). 구멍 고리는 반대 방향이라 그대로 더하면 빠진다."""
    v = 0.0
    for face in faces:
        for lp in face:
            p0 = pts[lp[0]]
            for i in range(1, len(lp) - 1):
                a, b = pts[lp[i]], pts[lp[i + 1]]
                v += (p0[0] * (a[1] * b[2] - a[2] * b[1]) - p0[1] * (a[0] * b[2] - a[2] * b[0]) + p0[2] * (a[0] * b[1] - a[1] * b[0]))
    return v / 6.0


def aligned_box(pts, faces):
    """벽 지역 꼭짓점·면이 벽 축에 나란한 직육면체면 그 상자 (u0, u1, v0, v1, z0, z1), 아니면 None.
    판정: 모든 면이 u·v·z 중 한 축에 수직(면 위 점들의 그 좌표 폭 <= ALIGN_TOL)이고 부피가 경계 상자 부피와 같다(L 자 같은 오목한 모양을 거른다)."""
    for face in faces:
        q = [pts[i] for i in face[0]]
        if not any(max(p[k] for p in q) - min(p[k] for p in q) <= ALIGN_TOL for k in range(3)):
            return None
    box = (min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts),
           min(p[2] for p in pts), max(p[2] for p in pts))
    bv = (box[1] - box[0]) * (box[3] - box[2]) * (box[5] - box[4])
    if bv <= 0.0 or abs(abs(_volume(pts, faces)) - bv) > VOL_RTOL * bv + 1e-6:
        return None
    return box


def overlap(a, b):
    """두 상자의 겹침 상자, 없으면 None."""
    q = (max(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), min(a[3], b[3]), max(a[4], b[4]), min(a[5], b[5]))
    if q[1] - q[0] >= CUT_MIN and q[3] - q[2] >= CUT_MIN and q[5] - q[4] >= CUT_MIN:
        return q
    return None


def bbox(pts) -> tuple:
    return (min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts),
            min(p[2] for p in pts), max(p[2] for p in pts))


# ---------------------------------------------------------------- 상자 집합 연산 (도면·수량)
def subtract(solids, voids) -> tuple:
    """상자 목록 solids 에서 voids 를 뺀 상자들(축 정렬, 서로 겹치지 않음). 빼는 상자마다 남는 쪽을 최대 6 조각(u 앞뒤 -> v 앞뒤 -> z 앞뒤)으로 나눈다."""
    cur = [tuple(b) for b in solids]
    for vd in voids:
        nxt = []
        for b in cur:
            o = overlap(b, vd)
            if o is None:
                nxt.append(b)
                continue
            u0, u1, v0, v1, z0, z1 = b
            if o[0] - u0 >= CUT_MIN:
                nxt.append((u0, o[0], v0, v1, z0, z1))
            if u1 - o[1] >= CUT_MIN:
                nxt.append((o[1], u1, v0, v1, z0, z1))
            if o[2] - v0 >= CUT_MIN:
                nxt.append((o[0], o[1], v0, o[2], z0, z1))
            if v1 - o[3] >= CUT_MIN:
                nxt.append((o[0], o[1], o[3], v1, z0, z1))
            if o[4] - z0 >= CUT_MIN:
                nxt.append((o[0], o[1], o[2], o[3], z0, o[4]))
            if z1 - o[5] >= CUT_MIN:
                nxt.append((o[0], o[1], o[2], o[3], o[5], z1))
        cur = nxt
    return tuple(cur)


def union_volume(boxes) -> float:
    """상자들의 합집합 부피(mm^3): 겹친 곳을 두 번 세지 않는다(좌표 압축)."""
    if not boxes:
        return 0.0
    us = sorted({b[k] for b in boxes for k in (0, 1)})
    total = 0.0
    for a, b in zip(us, us[1:]):
        mid = (a + b) / 2.0
        sl = [(q[2], q[3], q[4], q[5]) for q in boxes if q[0] < mid < q[1]]
        total += (b - a) * _union_area(sl)
    return total


def _union_area(rects) -> float:
    if not rects:
        return 0.0
    xs = sorted({r[k] for r in rects for k in (0, 1)})
    total = 0.0
    for a, b in zip(xs, xs[1:]):
        mid = (a + b) / 2.0
        ivs = sorted((r[2], r[3]) for r in rects if r[0] < mid < r[1])
        cov, hi = 0.0, None
        for lo, up in ivs:
            if hi is None or lo > hi:
                cov += up - lo
                hi = up
            elif up > hi:
                cov += up - hi
                hi = up
        total += (b - a) * cov
    return total


def through_area(boxes, half) -> float:
    """벽 두께를 다 뚫는 상자들(v 가 -half..half 를 덮는다)의 입면 (u, z) 합집합 넓이(mm^2): 벽 면 넓이에서 빠지는 몫."""
    rects = [(b[0], b[1], b[4], b[5]) for b in boxes if b[2] <= -half + CUT_MIN and b[3] >= half - CUT_MIN]
    return _union_area(rects)
