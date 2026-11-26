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
    if xf.mirror:   # 대칭은 축이 뒤집혀 u 로 잰 값을 L - u 로(W2 세 번째 PR: 첫 PR 이 이 조건을 곡선 벽 조건으로 바꿔 직선 벽 대칭이 뒤집지 않던 결함을 되돌린다)
        n = wall_len(p)   # 곡선 벽은 호 길이(현 길이가 아니다)
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
    if "spiral" in p:   # 방사 계단(W2 열일곱 번째 PR): 중심을 옮기고 시작 각을 돌린다. 대칭이면 회전 방향이 바뀐다(총 회전각 부호)
        sp = p["spiral"]
        c = xf.pt(sp["center"][0], sp["center"][1])
        return dict(p, spiral=dict(sp, center=c, start=xf.ang(sp["start"]), sweep=-sp["sweep"] if xf.mirror else sp["sweep"]))
    if "sketch" in p:   # 스케치 계단(W2 열일곱 번째 PR): 보행선 꼭짓점을 옮긴다(오르는 순서 그대로). 대칭이면 호의 회전 방향이 바뀐다(볼록도 부호)
        sk = p["sketch"]
        q = dict(sk, path=[xf.pt(a[0], a[1]) for a in sk["path"]])
        if xf.mirror and "bulges" in sk:
            q["bulges"] = [-b + 0.0 for b in sk["bulges"]]
        return dict(p, sketch=q)
    q = dict(p, run=_line(xf, p["run"], False))   # 걸음선은 오르는 방향이라 뒤집지 않는다
    if xf.mirror and "turn" in p:   # 대칭이면 꺾는 쪽이 바뀐다
        q["turn"] = dict(p["turn"], side="right" if p["turn"]["side"] == "left" else "left")
    return q


def elevator(p: dict, xf: Xf) -> dict:
    """승강기(W2 열일곱 번째 PR): 샤프트 가운데를 옮기고(크기는 그대로) 회전각을 돌린다. 대칭은 회전각 2φ - θ 에 로컬 y 를 뒤집은 것과 같아
    문 면 N·S 가 바뀐다(카는 가운데라 그대로). 회전각 0 이면 rotation 키를 두지 않는다."""
    x1, y1, x2, y2 = p["shaft"]
    hw, hd = (x2 - x1) / 2.0, (y2 - y1) / 2.0
    if xf.kind == "move":
        q = dict(p, shaft=[x1 + xf.m[4], y1 + xf.m[5], x2 + xf.m[4], y2 + xf.m[5]])
        return q
    c = xf.pt((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    q = dict(p, shaft=[_r6(c[0] - hw), _r6(c[1] - hd), _r6(c[0] + hw), _r6(c[1] + hd)])
    a = xf.ang(p.get("rotation", 0.0))
    q.pop("rotation", None)
    if a:
        q["rotation"] = a
    if xf.mirror and "doors" in p and p["doors"]["face"] in ("N", "S"):
        q["doors"] = dict(p["doors"], face="S" if p["doors"]["face"] == "N" else "N")
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
    if "axis" in p:   # 압출 지붕(W2 여덟 번째 PR): 축을 옮기고, 대칭이면 프로파일의 왼쪽·오른쪽이 바뀐다(s -> -s, 순서 뒤집기)
        a0, a1 = xf.pt(p["axis"][0], p["axis"][1]), xf.pt(p["axis"][2], p["axis"][3])
        q["axis"] = [a0[0], a0[1], a1[0], a1[1]]
        if xf.mirror:
            q["profile"] = [[-s, z] for s, z in reversed(p["profile"])]
    if xf.mirror and "bulges" in p:   # 평지붕 원호 변(W2 열여섯 번째 PR): 같은 변을 따라 옮긴다(대칭이 호를 뒤집고 외곽 순서를 뒤집어 다시 뒤집으므로 부호는 그대로)
        n = len(p["boundary"])
        q["bulges"] = [p["bulges"][(n - 2 - j) % n] for j in range(n)]
    if xf.mirror and ("overhangs" in p or "eaves" in p):   # 외곽선 지붕의 변마다 내밀기·처마 부속의 변 번호(W2 여덟 번째 PR)도 같은 변을 따라 옮긴다
        n = len(p["boundary"])
        if "overhangs" in p:
            q["overhangs"] = [p["overhangs"][(n - 2 - j) % n] for j in range(n)]
        if "eaves" in p:
            q["eaves"] = {k: dict(d, edges=sorted((n - 2 - i) % n for i in d["edges"])) for k, d in p["eaves"].items()}
    return q


def grid(p: dict, xf: Xf) -> dict:
    q = dict(p, line=_line(xf, p["line"], False))
    if "arc" in p:   # 원호 통심선(W2 열 번째 PR): 호 위의 가운데 점도 옮긴다(대칭이어도 가운데 점은 가운데 점이다)
        q["arc"] = xf.pt(*p["arc"])
    return q


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
    elif f == "revolve":   # 회전체(W2 여섯 번째 PR): 축 위 점·축 방향·기준 방향을 옮긴다
        q.update(_revolve(p, xf))
    elif f == "sweep3d":   # 3D 스윕(W2 여섯 번째 PR): 경로·호 경유점. 대칭이면 단면의 a(왼쪽)가 오른쪽이 된다(부재 단면은 좌우 대칭이라 그대로)
        q["rail"] = [xf.pt(x, y) + [z] for x, y, z in p["rail"]]
        if "via" in p:
            q["via"] = [None if m is None else xf.pt(m[0], m[1]) + [m[2]] for m in p["via"]]
        if xf.mirror and "profile" in p:
            n = len(p["profile"])
            q["profile"] = [[_r6(-a), b] for a, b in p["profile"]][::-1]
            if "bulges" in p:   # 뒤집은 목록의 변 j 는 원래 변 n-2-j 를 거꾸로 간다: 대칭(부호 반전)과 역방향(부호 반전)이 상쇄된다
                q["bulges"] = [p["bulges"][(n - 2 - j) % n] for j in range(n)]
    else:   # mesh: 대칭이면 면 고리를 뒤집어 바깥 방향을 지킨다
        q["vertices"] = [xf.pt(x, y) + [z] for x, y, z in p["vertices"]]
        if xf.mirror:
            q["faces"] = [list(fc)[::-1] for fc in p["faces"]]
    if "ops" in p:   # 불리언 피연산자(W2 여섯 번째 PR)도 같은 변환
        q["ops"] = [dict(o, solid=solid(o["solid"], xf)) for o in p["ops"]]
    return q


def _vec(xf: Xf, v) -> list:
    """방향 벡터(평면 성분만 선형 변환, z 그대로)."""
    a, b, c, d, _, _ = xf.m
    if xf.kind == "move":
        return [float(v[0]), float(v[1]), float(v[2])]
    return [_r6(a * v[0] + b * v[1]), _r6(c * v[0] + d * v[1]), float(v[2])]


def _revolve(p: dict, xf: Xf) -> dict:
    from dobim_core import solidops as SX
    o = xf.pt(p["origin"][0], p["origin"][1]) + [p["origin"][2]]
    if xf.kind == "move":
        return {"origin": o}
    _, A, R, T = SX.revolve_frame(p)
    A2, R2 = _vec(xf, A), _vec(xf, R)
    if xf.mirror:   # 대칭은 회전 방향을 뒤집는다: [0, 각] 이 [-각, 0] 이 되므로 기준 방향을 -각만큼 돌린다
        T2 = SX._cross(A2, R2)
        s, c = SX.sincos(p["angle"])
        R2 = [_r6(c * R2[k] - s * T2[k]) for k in range(3)]
    return {"origin": o, "axis": A2, "ref": R2}


def beam(p: dict, xf: Xf) -> dict:
    """구조 보(W2 열다섯 번째 PR): 두 점을 옮긴다(순서 그대로). 대칭이면 왼쪽이 오른쪽이 되므로 단면 회전(roll)의 부호를 바꾼다(180 은 그대로)."""
    q = dict(p)
    q["start"] = xf.pt(p["start"][0], p["start"][1])
    q["end"] = xf.pt(p["end"][0], p["end"][1])
    if xf.mirror and "roll" in p and p["roll"] != 180.0:
        q["roll"] = -p["roll"]
    if xf.mirror and "bulge" in p:   # 곡선 보(W2 열아홉 번째 PR): 대칭은 호의 반시계·시계를 뒤집는다(부풀어 오른 쪽이 거울 쪽으로)
        q["bulge"] = -p["bulge"] + 0.0
    return q


BY_KIND = {"wall": wall, "slab": slab, "space": space, "stair": stair, "railing": railing, "roof": roof, "grid": grid, "solid": solid, "beam": beam, "elevator": elevator}


def params(kind: str, p: dict, xf: Xf) -> dict:
    """요소 params 의 평면 변환. 호스팅 요소(문·호스팅 패밀리)는 호스트를 따르므로 여기서 다루지 않는다."""
    if kind == "finst":
        return finst_free(p, xf)
    return BY_KIND[kind](p, xf)
