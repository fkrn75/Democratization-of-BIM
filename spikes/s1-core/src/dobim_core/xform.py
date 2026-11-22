# SPDX-License-Identifier: MPL-2.0
"""평면 변환(이동·회전·대칭)을 요소 params 에 적용하는 순수 함수(W1 1-F, UI-03).

element.copy·element.rotate·element.mirror 가 쓴다. ifcopenshell·엔진을 모른다(입력 사전 -> 새 사전).
- 이동은 더하기만 한다(반올림 없음: wall.move 등 개별 이동 커맨드와 같은 값).
- 회전·대칭은 결과 좌표를 소수 6자리(1e-6 mm)로 반올림한다(90도 회전의 cos 가 정확히 0 이 되도록 kinds.sincos_deg 를 쓴다).
- 대칭은 좌우가 바뀌므로 다각형은 순서를 뒤집어 방향(반시계)을 지키고, 벽·수직 판은 축선을 뒤집어 '왼쪽 면'(마감 층·잘라내기 v)이 그대로 왼쪽에 오게 한다.
  그래서 축선 위의 길이 좌표(개구부 offset·상단 윤곽 u·잘라내기 u)는 L - u 가 된다.
"""
from __future__ import annotations

import math
from math import sqrt

from dobim_core.kinds import sincos_deg


def _r6(v: float) -> float:
    return round(v, 6) + 0.0   # -0.0 을 0.0 으로


class Xf:
    """x' = a x + b y + e, y' = c x + d y + f. ang 은 방향(도, +x 가 0·반시계)의 변환."""

    def __init__(self, a, b, c, d, e, f, *, kind: str, deg: float = 0.0, phi: float = 0.0):
        self.m = (a, b, c, d, e, f)
        self.kind = kind          # "move" | "rotate" | "mirror"
        self.deg = deg            # rotate: 회전각
        self.phi = phi            # mirror: 대칭축 방향각(도)

    @property
    def mirror(self) -> bool:
        return self.kind == "mirror"

    def pt(self, x, y) -> list:
        a, b, c, d, e, f = self.m
        if self.kind == "move":
            return [x + e, y + f]
        return [_r6(a * x + b * y + e), _r6(c * x + d * y + f)]

    def ang(self, v: float) -> float:
        if self.kind == "move":
            return v
        out = (v + self.deg) if self.kind == "rotate" else (2.0 * self.phi - v)
        return _r6(out % 360.0) % 360.0


def move(dx: float, dy: float) -> Xf:
    return Xf(1.0, 0.0, 0.0, 1.0, float(dx), float(dy), kind="move")


def rotate(cx: float, cy: float, deg: float) -> Xf:
    sn, cs = sincos_deg(float(deg))
    return Xf(cs, -sn, sn, cs, cx - cs * cx + sn * cy, cy - sn * cx - cs * cy, kind="rotate", deg=float(deg))


def mirror(x1: float, y1: float, x2: float, y2: float) -> Xf:
    """두 점을 지나는 축에 대한 대칭. 반사 행렬은 사칙연산만 쓴다. 방향각은 축이 수평·수직이면 정확한 값, 아니면 atan2."""
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    c2, s2 = (dx * dx - dy * dy) / l2, 2.0 * dx * dy / l2
    if dy == 0:
        phi = 0.0
    elif dx == 0:
        phi = 90.0
    else:
        phi = math.degrees(math.atan2(dy, dx))
    return Xf(c2, s2, s2, -c2, x1 - c2 * x1 - s2 * y1, y1 - s2 * x1 + c2 * y1, kind="mirror", phi=phi)


def _pts(xf: Xf, pts, keep_order=False) -> list:
    out = [xf.pt(q[0], q[1]) + [float(z) for z in q[2:]] for q in pts]
    return out[::-1] if xf.mirror and not keep_order else out


def _line(xf: Xf, ln, reverse_on_mirror: bool) -> list:
    p, q = xf.pt(ln[0], ln[1]), xf.pt(ln[2], ln[3])
    if xf.mirror and reverse_on_mirror:
        p, q = q, p
    return [p[0], p[1], q[0], q[1]]


def axis_len(ln) -> float:
    return sqrt((ln[2] - ln[0]) ** 2 + (ln[3] - ln[1]) ** 2)


def wall_len(p: dict) -> float:
    """벽 축 길이(곡선 벽 W2 ARC-04 은 호 길이: 대칭이 L - u 로 뒤집는 길이 좌표의 L)."""
    if "arc" in p:
        from dobim_core import arcgeom as AG
        return AG.length(AG.info(p["axis"], p["arc"]))
    return axis_len(p["axis"])


def _flip_u(items, length: float) -> list:
    """상단 윤곽·수직 판 윤곽 [[u,z],...] 의 u -> L - u (순서를 뒤집어 방향을 지킨다)."""
    return [[_r6(length - u), z] for u, z in items][::-1]


def _flip_openings(ops, length: float) -> list:
    return [dict(o, offset=_r6(length - o["offset"])) for o in ops]


def wall(p: dict, xf: Xf) -> dict:
    q = dict(p, axis=_line(xf, p["axis"], True))
    if "arc" in p:   # 곡선 벽(W2 ARC-04): 호 위의 점도 옮긴다(대칭이면 축이 뒤집혀도 가운데 점은 가운데 점이다)
        q["arc"] = xf.pt(*p["arc"])
        n = axis_len(p["axis"])
        if "openings" in p:
            q["openings"] = _flip_openings(p["openings"], n)
        if "top_profile" in p:
            q["top_profile"] = _flip_u(p["top_profile"], n)
        if "cuts" in p:   # [u0, u1, v0, v1, z0, z1]: 왼쪽 면이 그대로라 v 는 그대로
            q["cuts"] = [[_r6(n - c[1]), _r6(n - c[0]), c[2], c[3], c[4], c[5]] for c in p["cuts"]]
    return q


def slab(p: dict, xf: Xf) -> dict:
    q = dict(p, boundary=_pts(xf, p["boundary"]))
    if "openings" in p:
        q["openings"] = [_pts(xf, o) for o in p["openings"]]
    if "drains" in p:
        q["drains"] = _pts(xf, p["drains"], keep_order=True)
    if "fall" in p:
        q["fall"] = dict(p["fall"], dir=xf.ang(p["fall"]["dir"]))
    return q


def space(p: dict, xf: Xf) -> dict:
    q = dict(p, boundary=_pts(xf, p["boundary"]))
    if "holes" in p:
        q["holes"] = [_pts(xf, o) for o in p["holes"]]
    return q


def stair(p: dict, xf: Xf) -> dict:
    q = dict(p, run=_line(xf, p["run"], False))   # 걸음선은 오르는 방향이라 뒤집지 않는다
    if xf.mirror and "turn" in p:   # 대칭이면 꺾는 쪽이 바뀐다
        q["turn"] = dict(p["turn"], side="right" if p["turn"]["side"] == "left" else "left")
    return q


def railing(p: dict, xf: Xf) -> dict:
    return dict(p, path=_pts(xf, p["path"], keep_order=True))   # 난간선 방향(rise)은 그대로


def roof(p: dict, xf: Xf) -> dict:
    q = dict(p, boundary=_pts(xf, p["boundary"]))
    if "dir" in p:
        q["dir"] = xf.ang(p["dir"])
    if "slopes" in p and xf.mirror:   # 대칭이면 외곽을 뒤집는다: 뒤집은 외곽의 변 j = 원래 변 n-2-j
        n = len(p["slopes"])
        q["slopes"] = [p["slopes"][(n - 2 - j) % n] for j in range(n)]
    return q


def grid(p: dict, xf: Xf) -> dict:
    return dict(p, line=_line(xf, p["line"], False))


def finst_free(p: dict, xf: Xf) -> dict:
    pos = xf.pt(p["position"][0], p["position"][1]) + [float(z) for z in p["position"][2:]]
    return dict(p, position=pos, rotation=xf.ang(p["rotation"]))


def solid(p: dict, xf: Xf) -> dict:
    f = p["form"]
    q = dict(p)
    if f == "prism":
        q["polygon"] = _pts(xf, p["polygon"])
    elif f == "loft":   # 모든 단면을 같은 식으로 뒤집으므로 꼭짓점 대응이 유지된다
        q["sections"] = [dict(s, polygon=_pts(xf, s["polygon"])) for s in p["sections"]]
    elif f == "panel":
        q["line"] = _line(xf, p["line"], True)
        if xf.mirror:
            n = axis_len(p["line"])
            q["profile"] = _flip_u(p["profile"], n)
            if "openings" in p:
                q["openings"] = _flip_openings(p["openings"], n)
    elif f == "sweep":
        q["path"] = _pts(xf, p["path"], keep_order=True)
        if xf.mirror:   # 단면의 o(진행 방향 왼쪽 거리)는 오른쪽이 된다
            q["profile"] = [[_r6(-o), z] for o, z in p["profile"]][::-1]
    elif f == "plate":
        q["polygon"] = _pts(xf, p["polygon"])
        if "holes" in p:
            q["holes"] = [_pts(xf, h) for h in p["holes"]]
        q["base"] = xf.pt(p["base"][0], p["base"][1]) + [p["base"][2]]
        q["dir"] = xf.ang(p["dir"])
    elif f == "member":
        q["start"] = xf.pt(p["start"][0], p["start"][1]) + [p["start"][2]]
        q["end"] = xf.pt(p["end"][0], p["end"][1]) + [p["end"][2]]
    else:   # mesh: 대칭이면 면 고리를 뒤집어 바깥 방향을 지킨다
        q["vertices"] = [xf.pt(x, y) + [z] for x, y, z in p["vertices"]]
        if xf.mirror:
            q["faces"] = [list(fc)[::-1] for fc in p["faces"]]
    return q


BY_KIND = {"wall": wall, "slab": slab, "space": space, "stair": stair, "railing": railing, "roof": roof, "grid": grid, "solid": solid}


def params(kind: str, p: dict, xf: Xf) -> dict:
    """요소 params 의 평면 변환. 호스팅 요소(문·호스팅 패밀리)는 호스트를 따르므로 여기서 다루지 않는다."""
    if kind == "finst":
        return finst_free(p, xf)
    return BY_KIND[kind](p, xf)
