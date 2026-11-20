# SPDX-License-Identifier: MPL-2.0
"""외래 IFC 문·창의 평면 기호 (외래 도면 3차, IO-04, 설계: spikes/phase1-drawings/baseline.md 50절).

순수 모듈: 파일·외부 라이브러리·sin/cos 를 쓰지 않는다(drawing.py 와 같은 규칙, 호는 유리식 매개화).
입력은 foreign_geom.opening 의 값(여닫이 방식·배치 축·전체 폭)과, 외래 메시를 문·창의 국소 축에 투영한 범위다.

국소 좌표 (u, w): u = 국소 x 축(폭 방향), w = 국소 y 축(벽 두께 방향). IFC 규약대로 문짝은 +w 쪽으로 열리고,
왼쪽·오른쪽은 +w 방향을 바라볼 때의 왼쪽(u 가 작은 쪽)·오른쪽이다(IfcDoorTypeOperationEnum 의 SINGLE_SWING_LEFT 등).
문짝 폭은 전체 폭(OverallWidth)이고 메시 폭의 가운데에 둔다(전체 폭이 없거나 메시보다 넓으면 메시 폭).
기호 선분은 모두 "symbol" 등급이다. 모르는 방식은 기본 기호(문: 왼쪽 외여닫이, 창: 칸 하나 유리선)로 그리고 '기본' 표시를 돌려준다.
"""
from __future__ import annotations

ARC_N = 8   # 문 회전 호의 분할 수(90도당, drawing.ARC_N 과 같다)

SWING_DOOR = {   # 방식 -> 문짝들: (경첩 쪽, 여는 쪽(+1 = +w, -1 = -w), 문짝 범위: "all" 전체, "lo"·"hi" 반쪽)
    "SINGLE_SWING_LEFT": (("lo", 1, "all"),),
    "SINGLE_SWING_RIGHT": (("hi", 1, "all"),),
    "DOUBLE_SWING_LEFT": (("lo", 1, "all"), ("lo", -1, "all")),
    "DOUBLE_SWING_RIGHT": (("hi", 1, "all"), ("hi", -1, "all")),
    "DOUBLE_DOOR_SINGLE_SWING": (("lo", 1, "lo"), ("hi", 1, "hi")),
    "DOUBLE_DOOR_DOUBLE_SWING": (("lo", 1, "lo"), ("lo", -1, "lo"), ("hi", 1, "hi"), ("hi", -1, "hi")),
    "DOUBLE_DOOR_SINGLE_SWING_OPPOSITE_LEFT": (("lo", 1, "lo"), ("hi", -1, "hi")),
    "DOUBLE_DOOR_SINGLE_SWING_OPPOSITE_RIGHT": (("lo", -1, "lo"), ("hi", 1, "hi")),
    "SWING_FIXED_LEFT": (("lo", 1, "lo"),),
    "SWING_FIXED_RIGHT": (("hi", 1, "hi"),),
}
FIXED_HALF = {"SWING_FIXED_LEFT": "hi", "SWING_FIXED_RIGHT": "lo"}   # 고정 문짝이 있는 반쪽
SLIDING_DOOR = {"SLIDING_TO_LEFT": ("lo",), "SLIDING_TO_RIGHT": ("hi",), "DOUBLE_DOOR_SLIDING": ("lo", "hi")}   # 문짝이 미끄러져 가는 쪽
DOOR_DEFAULT = "SINGLE_SWING_LEFT"
# 창 칸 나눔 -> 세로 칸막이 위치(폭 비율). 가로 나눔만 있는 것은 평면에 칸막이가 없다
WINDOW_MULLIONS = {"SINGLE_PANEL": (), "DOUBLE_PANEL_VERTICAL": (0.5,), "DOUBLE_PANEL_HORIZONTAL": (), "TRIPLE_PANEL_VERTICAL": (1.0 / 3.0, 2.0 / 3.0),
                   "TRIPLE_PANEL_BOTTOM": (0.5,), "TRIPLE_PANEL_TOP": (0.5,), "TRIPLE_PANEL_LEFT": (0.5,), "TRIPLE_PANEL_RIGHT": (0.5,),
                   "TRIPLE_PANEL_HORIZONTAL": ()}
SLIDE_OVERLAP = 0.05   # 미서기 창·미닫이 문짝이 겹치는 길이(폭 비율)
ARROW = 0.08           # 미닫이 방향 화살 머리 길이(폭 비율)


def _swing(out, hinge, free, wf, side):
    """경첩 (hinge, wf) 에서 +-w 쪽으로 열린 문짝 선과 1/4 호(free 쪽 닫힌 끝 -> 열린 끝). 각 = 2*atan(t)."""
    L = abs(free - hinge)
    out.append((hinge, wf, hinge, wf + side * L))
    sgn = 1.0 if free > hinge else -1.0
    prev = (free, wf)
    for i in range(1, ARC_N + 1):
        t = i / ARC_N
        k = 1.0 + t * t
        p = (hinge + sgn * L * (1.0 - t * t) / k, wf + side * L * (2.0 * t) / k)
        out.append(prev + p)
        prev = p


def _half(a, b, which):
    m = (a + b) / 2.0
    return (a, b) if which == "all" else (a, m) if which == "lo" else (m, b)


def door_local(op, a, b, w0, w1):
    """문 기호 선분들((u0, w0, u1, w1), ...)과 기본 기호 여부. a..b = 문짝 폭, w0..w1 = 문 두께 범위(메시)."""
    default = op not in SWING_DOOR and op not in SLIDING_DOOR
    if default:
        op = DOOR_DEFAULT
    out = []
    if op in SWING_DOOR:
        for hinge_side, side, part in SWING_DOOR[op]:
            lo, hi = _half(a, b, part)
            hinge, free = (lo, hi) if hinge_side == "lo" else (hi, lo)
            _swing(out, hinge, free, w1 if side > 0 else w0, side)
        if op in FIXED_HALF:   # 고정 문짝: 두께 가운데 선
            lo, hi = _half(a, b, FIXED_HALF[op])
            wm = (w0 + w1) / 2.0
            out.append((lo, wm, hi, wm))
        return out, default
    wm, d = (w0 + w1) / 2.0, (w1 - w0) / 4.0
    W = b - a
    ov, ar = SLIDE_OVERLAP * W, ARROW * W
    goes = SLIDING_DOOR[op]
    if len(goes) == 1:   # 외미닫이: 문짝 선(두께 가운데에서 +w 로 d) + 가는 쪽 화살
        wl = wm + d
        out.append((a, wl, b, wl))
        end, sgn = (a, 1.0) if goes[0] == "lo" else (b, -1.0)
        out += [(end, wl, end + sgn * ar, wl + ar / 2.0), (end, wl, end + sgn * ar, wl - ar / 2.0)]
        return out, default
    m = (a + b) / 2.0   # 쌍미닫이: 두 문짝이 가운데에서 겹치고 각자 바깥쪽으로 간다
    out += [(a, wm + d, m + ov, wm + d), (m - ov, wm - d, b, wm - d)]
    out += [(a, wm + d, a + ar, wm + d + ar / 2.0), (a, wm + d, a + ar, wm + d - ar / 2.0),
            (b, wm - d, b - ar, wm - d + ar / 2.0), (b, wm - d, b - ar, wm - d - ar / 2.0)]
    return out, default


def window_local(part, panels, a, b, w0, w1):
    """창 기호 선분들과 기본 기호 여부. 창틀 양면 선(w0·w1) + 유리선(가운데). 미서기(SLIDINGHORIZONTAL 창짝)는 유리선 둘이 겹친다.
    세로 칸 나눔은 칸막이 선(w0..w1)을 긋는다. 칸 나눔도 창짝 방식도 모르면 칸 하나 기본 기호."""
    known = [p for p in panels if p not in ("NOTDEFINED", "OTHEROPERATION")]
    default = part not in WINDOW_MULLIONS and not known
    out = [(a, w0, b, w0), (a, w1, b, w1)]
    wm, d = (w0 + w1) / 2.0, (w1 - w0) / 6.0
    W = b - a
    if any(p == "SLIDINGHORIZONTAL" for p in known):
        m, ov = (a + b) / 2.0, SLIDE_OVERLAP * W
        out += [(a, wm + d, m + ov, wm + d), (m - ov, wm - d, b, wm - d)]
        return out, default
    out.append((a, wm, b, wm))
    for f in WINDOW_MULLIONS.get(part, ()):
        u = a + f * W
        out.append((u, w0, u, w1))
    return out, default


def symbol(fo, local_pts):
    """외래 문·창 하나의 평면 기호: (세계 좌표 선분 (("symbol", x0, y0, x1, y1), ...), 기본 기호 여부) 또는 None.
    fo = foreign_geom.opening 값, local_pts = 메시 꼭짓점을 (u, w) 로 바꾼 점들(절단 높이와 무관한 전체 메시)."""
    if fo is None or not local_pts:
        return None
    us = [p[0] for p in local_pts]
    ws = [p[1] for p in local_pts]
    u0, u1, w0, w1 = min(us), max(us), min(ws), max(ws)
    if u1 - u0 < 1.0:
        return None
    if w1 - w0 < 1.0:   # 두께가 없는 메시(판 하나): 문짝·창틀 선이 한 줄로 겹치지 않게 1mm 를 둔다
        w0, w1 = w0 - 0.5, w1 + 0.5
    width = fo[-1]
    W = width if width is not None and width <= u1 - u0 + 0.01 else u1 - u0
    uc = (u0 + u1) / 2.0
    a, b = uc - W / 2.0, uc + W / 2.0
    if fo[0] == "door":
        segs, default = door_local(fo[1], a, b, w0, w1)
        org, xa, ya = fo[2], fo[3], fo[4]
    else:
        segs, default = window_local(fo[1], fo[2], a, b, w0, w1)
        org, xa, ya = fo[3], fo[4], fo[5]

    def world(u, w):
        return (round(org[0] + u * xa[0] + w * ya[0], 3), round(org[1] + u * xa[1] + w * ya[1], 3))
    out = []
    for s in segs:
        p, q = world(s[0], s[1]), world(s[2], s[3])
        if p != q:
            out.append(("symbol",) + p + q)
    return tuple(out), default
