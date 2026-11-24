# SPDX-License-Identifier: MPL-2.0
"""구조 보(beam)의 순수 형상(W2 열다섯 번째 PR, STR-02·STR-03·STR-07).

이 모듈은 ifcopenshell·엔진을 모른다(kinds·queries·drawing·commands 가 같은 값을 쓴다). 값은 결정적이다(삼각함수는 solids.sincos_deg).

보 행 params(kinds._beam_params 가 검사한다):
  start, end : [x, y] 평면 두 점(레벨 좌표 mm). 축은 수평이다(경사 보는 다음 PR)
  offset     : 기준선 높이(레벨 기준 mm). anchor 가 단면의 어디가 기준선에 오는지 정한다(top 윗면·center 가운데·bottom 밑면, 상용 도구의 z 맞춤)
  section    : 단면 {"shape": "rect", "width", "depth"} 또는 {"shape": "h", "width", "depth", "web", "flange"}(H형강: 폭 B·춤 H·웨브 t1·플랜지 t2)
  선택 roll  : 단면 회전(도, 기준선 둘레, 시작점에서 끝점을 볼 때 반시계 +). 없으면 0
  선택 btype : 보 유형 id(관계 of_type 과 같은 뜻). 유형을 고치면 그 유형의 보 행 section 을 다시 쓴다(벽·난간 유형과 같은 설계)
  선택 cutback: false 면 기둥 면 자르기를 하지 않는다(없으면 자른다)

단면 좌표 (a, b): a = 진행 방향 왼쪽 +, b = 위 +. 기준선이 (0, 0) 이다.
기둥 접합(STR-03, 단순화): 보 끝점이 평면에서 기둥(IfcColumn 패밀리 인스턴스)의 각기둥 단면 안에 있고 높이가 겹치면, 보 축을 그 기둥 면까지
  줄인다(끝에서 가장 가까운 경계 교차점). 끝면은 축에 수직이다(비스듬히 만나는 면의 경사 자르기·보끼리의 접합은 다음 PR).
"""
from __future__ import annotations

from math import sqrt

from dobim_core import solids as SO

SHAPES = ("rect", "h")                       # 사각(철근콘크리트 보, IfcRectangleProfileDef)·H형강(IfcIShapeProfileDef)
ANCHORS = ("top", "center", "bottom")        # 기준선이 지나는 단면 자리(상용 도구의 z 맞춤: 윗면이 기본)
SEC_DIM_MAX = 3000.0                         # 단면 치수 상한 mm
COORD_MAX = 1e7                              # 좌표 절댓값 상한 mm(다른 요소와 같다)
OFFSET_MAX = 1e5                             # 기준선 높이 절댓값 상한 mm
LEN_MIN = 10.0                               # 보 축 길이 하한 mm
LEN_MAX = 200000.0                           # 보 축 길이 상한 mm(200 m)
CUT_MIN = 10.0                               # 기둥 면 자르기 뒤 남아야 하는 길이 mm(못 미치면 자르지 않는다)
BEAMTYPES_MAX = 1000                         # 프로젝트의 보 유형 수 상한

# H형강 표준 단면(KS D 3502 의 H 형강 치수 표: 춤 H x 폭 B x 웨브 t1 x 플랜지 t2, mm). 필릿 반지름 r 은 형상에 넣지 않는다
H_PRESETS = {
    "H-200x100x5.5x8": (200.0, 100.0, 5.5, 8.0), "H-250x125x6x9": (250.0, 125.0, 6.0, 9.0), "H-300x150x6.5x9": (300.0, 150.0, 6.5, 9.0),
    "H-350x175x7x11": (350.0, 175.0, 7.0, 11.0), "H-400x200x8x13": (400.0, 200.0, 8.0, 13.0), "H-450x200x9x14": (450.0, 200.0, 9.0, 14.0),
    "H-500x200x10x16": (500.0, 200.0, 10.0, 16.0), "H-600x200x11x17": (600.0, 200.0, 11.0, 17.0), "H-588x300x12x20": (588.0, 300.0, 12.0, 20.0),
    "H-700x300x13x24": (700.0, 300.0, 13.0, 24.0), "H-200x200x8x12": (200.0, 200.0, 8.0, 12.0), "H-250x250x9x14": (250.0, 250.0, 9.0, 14.0),
    "H-300x300x10x15": (300.0, 300.0, 10.0, 15.0), "H-350x350x12x19": (350.0, 350.0, 12.0, 19.0), "H-400x400x13x21": (400.0, 400.0, 13.0, 21.0),
}


def _num(v) -> bool:
    return (type(v) is int or type(v) is float) and v == v and v not in (float("inf"), float("-inf"))


def preset_section(name: str) -> dict:
    """표준 단면 이름 -> section(H형강). 없는 이름은 KeyError."""
    h, b, t1, t2 = H_PRESETS[name]
    return {"shape": "h", "width": b, "depth": h, "web": t1, "flange": t2}


def section_problem(sc) -> str | None:
    """단면 검사(안 되면 이유)."""
    if not isinstance(sc, dict) or sc.get("shape") not in SHAPES:
        return f"section.shape 는 {'/'.join(SHAPES)} 중 하나다"
    need = {"shape", "width", "depth"} | ({"web", "flange"} if sc["shape"] == "h" else set())
    if set(sc) != need:
        return f"section({sc['shape']}) 의 키가 다르다(" + ", ".join(sorted(need)) + ")"
    if not all(_num(sc[k]) and 0 < sc[k] <= SEC_DIM_MAX for k in sc if k != "shape"):
        return f"section 치수는 0 보다 크고 {SEC_DIM_MAX:g} 이하여야 한다"
    if sc["shape"] == "h" and not (sc["web"] < sc["width"] and 2 * sc["flange"] < sc["depth"]):
        return "H형 단면은 web < width, 2 x flange < depth 여야 한다"
    return None


def section_label(sc) -> str:
    """단면 표기(IFC 프로필 이름·일람표): 사각 'R-폭x춤', H형강 'H-춤x폭x웨브x플랜지'(KS 표기 순서)."""
    def f(v):
        return f"{float(v):g}"
    if sc["shape"] == "h":
        return f"H-{f(sc['depth'])}x{f(sc['width'])}x{f(sc['web'])}x{f(sc['flange'])}"
    return f"R-{f(sc['width'])}x{f(sc['depth'])}"


def section_area(sc) -> float:
    """단면적 mm2."""
    w, d = float(sc["width"]), float(sc["depth"])
    if sc["shape"] == "h":
        tw, tf = float(sc["web"]), float(sc["flange"])
        return 2.0 * w * tf + tw * (d - 2.0 * tf)
    return w * d


def _center_b(sc, anchor) -> float:
    """기준선에서 단면 가운데까지의 b(위 +)."""
    d = float(sc["depth"])
    return {"top": -d / 2.0, "center": 0.0, "bottom": d / 2.0}[anchor]


def section_quads(p) -> list:
    """단면을 이루는 볼록 사각형들(각 반시계 (a, b) 4점, 회전·기준선 반영): 사각은 1개, H형은 아래 플랜지·웨브·위 플랜지 3개(겹치지 않는다).
    가림 조각·절단 채움이 쓴다(볼록 각기둥의 평면 절단은 볼록 다각형)."""
    sc = p["section"]
    w, d = float(sc["width"]), float(sc["depth"])
    cb = _center_b(sc, p["anchor"])
    h, hd = w / 2.0, d / 2.0
    if sc["shape"] == "h":
        tw, tf = float(sc["web"]) / 2.0, float(sc["flange"])
        boxes = [(-h, h, -hd, -hd + tf), (-tw, tw, -hd + tf, hd - tf), (-h, h, hd - tf, hd)]
    else:
        boxes = [(-h, h, -hd, hd)]
    return [[_rot(p, a, b + cb) for a, b in ((a0, b0), (a1, b0), (a1, b1), (a0, b1))] for a0, a1, b0, b1 in boxes]


def section_outline(p) -> list:
    """단면 바깥 윤곽(반시계 (a, b), 회전·기준선 반영). 사각 4점, H형 12점."""
    sc = p["section"]
    w, d = float(sc["width"]), float(sc["depth"])
    cb = _center_b(sc, p["anchor"])
    h, y0, y1 = w / 2.0, -d / 2.0, d / 2.0
    if sc["shape"] == "h":
        tw, tf = float(sc["web"]) / 2.0, float(sc["flange"])
        pts = [(-h, y0), (h, y0), (h, y0 + tf), (tw, y0 + tf), (tw, y1 - tf), (h, y1 - tf), (h, y1), (-h, y1), (-h, y1 - tf), (-tw, y1 - tf), (-tw, y0 + tf), (-h, y0 + tf)]
    else:
        pts = [(-h, y0), (h, y0), (h, y1), (-h, y1)]
    return [_rot(p, a, b + cb) for a, b in pts]


def _rot(p, a, b):
    """단면 점을 roll 만큼 돌린다(기준선 (0, 0) 둘레, 반시계 +). roll 이 없으면 그대로."""
    r = p.get("roll")
    if not r:
        return (a, b)
    sn, cs = SO.sincos_deg(float(r))
    return (a * cs - b * sn, a * sn + b * cs)


def b_range(p) -> tuple:
    """단면의 (b 최소, b 최대)(기준선 기준, 회전 반영)."""
    bs = [q[1] for q in section_outline(p)]
    return (min(bs), max(bs))


def a_range(p) -> tuple:
    """단면의 (a 최소, a 최대)(평면 폭 방향, 회전 반영)."""
    as_ = [q[0] for q in section_outline(p)]
    return (min(as_), max(as_))


def axis(p) -> tuple:
    """(시작 x, y, 방향 x, y(단위), 길이). 검사를 통과한 행이라 길이는 LEN_MIN 이상이다."""
    x0, y0 = float(p["start"][0]), float(p["start"][1])
    dx, dy = float(p["end"][0]) - x0, float(p["end"][1]) - y0
    ln = sqrt(dx * dx + dy * dy)
    return (x0, y0, dx / ln, dy / ln, ln)


def beam_problem(p) -> str | None:
    """보 형상 검사(필드 모양은 kinds 가 본다)."""
    why = section_problem(p["section"])
    if why:
        return why
    if p["anchor"] not in ANCHORS:
        return f"beam.anchor 는 {'/'.join(ANCHORS)} 중 하나다"
    dx, dy = p["end"][0] - p["start"][0], p["end"][1] - p["start"][1]
    ln = sqrt(dx * dx + dy * dy)
    if not LEN_MIN <= ln <= LEN_MAX:
        return f"보 길이는 {LEN_MIN:g}~{LEN_MAX:g} mm 여야 한다(현재 {ln:.1f})"
    return None


# ---------------------------------------------------------------- 기둥 접합(STR-03 단순화)
def _point_in(pt, poly) -> bool:
    """점이 다각형 안이거나 경계 위(허용 오차 0.01mm)인가."""
    return SO.point_in(pt, poly) >= 0


def _crossings(ax, ay, ux, uy, ln, poly) -> list:
    """축선 (ax, ay) + t (ux, uy), 0 <= t <= ln 이 다각형 경계를 지나는 t 들."""
    out = []
    n = len(poly)
    for i in range(n):
        (px, py), (qx, qy) = poly[i], poly[(i + 1) % n]
        ex, ey = qx - px, qy - py
        den = ux * ey - uy * ex
        if abs(den) < 1e-12:
            continue   # 나란한 변(축이 변 위를 지나는 경우는 다른 변의 교차가 정한다)
        wx, wy = px - ax, py - ay
        t = (wx * ey - wy * ex) / den
        s = (wx * uy - wy * ux) / den
        if -1e-9 <= s <= 1.0 + 1e-9 and -1e-6 <= t <= ln + 1e-6:
            out.append(t)
    return out


def trims(p, zr, columns) -> tuple:
    """기둥 면 자르기: (시작에서 줄인 길이, 끝에서 줄인 길이, 시작 기둥 id 또는 None, 끝 기둥 id 또는 None).
    zr = 보의 (아래 z, 위 z) 절대 높이, columns = ((기둥 id, 아래 z, 위 z, 경계 상자 (x0, y0, x1, y1), 세계 다각형), ...).
    끝점이 기둥 단면 안(경계 포함)이고 높이가 1mm 넘게 겹치면 그 끝에서 가장 가까운 경계 교차점까지 줄인다. 여러 기둥이면 가장 많이 줄이는 것.
    남는 길이가 CUT_MIN 보다 짧아지면 줄이지 않는다(두 끝이 같은 기둥 안 등)."""
    if p.get("cutback") is False or not columns:
        return (0.0, 0.0, None, None)
    ax, ay, ux, uy, ln = axis(p)
    ends = ((ax, ay), (ax + ux * ln, ay + uy * ln))
    best = [(0.0, None), (0.0, None)]
    for cid, z0, z1, bb, poly in columns:
        if min(zr[1], z1) - max(zr[0], z0) <= 1.0:
            continue
        for k, (ex, ey) in enumerate(ends):
            if not (bb[0] - 0.01 <= ex <= bb[2] + 0.01 and bb[1] - 0.01 <= ey <= bb[3] + 0.01) or not _point_in((ex, ey), poly):
                continue
            ts = _crossings(ax, ay, ux, uy, ln, poly)
            if k == 0:
                ts = [t for t in ts if t > 1e-6]
                cut = min(ts) if ts else None
            else:
                ts = [t for t in ts if t < ln - 1e-6]
                cut = ln - max(ts) if ts else None
            if cut is not None and cut > best[k][0] + 1e-9:
                best[k] = (cut, cid)
    if ln - best[0][0] - best[1][0] < CUT_MIN:
        return (0.0, 0.0, None, None)
    return (best[0][0], best[1][0], best[0][1], best[1][1])


# ---------------------------------------------------------------- 3D 형상(도면·IFC·수량이 같은 값)
def frame(p, z_ref) -> tuple:
    """(원점 (x, y, z) = 기준선 시작점(절대 z_ref), 방향 D, 왼쪽 S, 위 U, 길이)."""
    ax, ay, ux, uy, ln = axis(p)
    return ((ax, ay, z_ref), (ux, uy, 0.0), (-uy, ux, 0.0), (0.0, 0.0, 1.0), ln)


def _to3(o, D, S, U, t0):
    def f(q, t):
        tt = t0 + t
        return (o[0] + q[0] * S[0] + tt * D[0], o[1] + q[0] * S[1] + tt * D[1], o[2] + q[1] + tt * D[2])
    return f


def mesh(p, z_ref, cut) -> tuple:
    """(꼭짓점, 면) 다면체(solids 의 표현): 단면 바깥 윤곽을 축 [cut[0], 길이 - cut[1]] 구간에 돌출. z_ref = 기준선 절대 높이."""
    o, D, S, U, ln = frame(p, z_ref)
    return SO._extrude(section_outline(p), [], _to3(o, D, S, U, cut[0]), None, ln - cut[0] - cut[1])


def pieces(p, z_ref, cut) -> list:
    """볼록 조각(사각형 단면 각기둥)마다 꼭짓점 8개 [(아래 4: 시작 끝면), (위 4: 끝 끝면)]. 절단 채움·가림이 쓴다."""
    o, D, S, U, ln = frame(p, z_ref)
    f = _to3(o, D, S, U, cut[0])
    L = ln - cut[0] - cut[1]
    return [[f(q, 0.0) for q in quad] + [f(q, L) for q in quad] for quad in section_quads(p)]


_BOX_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))


def piece_cut(piece, axis_v, c) -> list:
    """볼록 조각 하나를 평면 (점 . axis_v) = c 로 자른 볼록 다각형의 3D 꼭짓점(정렬 전). 닿지 않으면 []."""
    pts = []
    for i, j in _BOX_EDGES:
        a, b = piece[i], piece[j]
        da = a[0] * axis_v[0] + a[1] * axis_v[1] + a[2] * axis_v[2] - c
        db = b[0] * axis_v[0] + b[1] * axis_v[1] + b[2] * axis_v[2] - c
        if (da > 0) == (db > 0):
            continue
        t = da / (da - db)
        pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t))
    return pts


def convex_order(pts2) -> list:
    """2D 점들(볼록 다각형의 꼭짓점, 순서 없음) -> 반시계 볼록 다각형(겹치는 점 제거). 3점 미만이면 []."""
    uniq = []
    for q in pts2:
        if not any(abs(q[0] - r[0]) < 1e-6 and abs(q[1] - r[1]) < 1e-6 for r in uniq):
            uniq.append(q)
    if len(uniq) < 3:
        return []
    pts = sorted(uniq)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for q in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], q) <= 1e-9:
            lo.pop()
        lo.append(q)
    for q in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], q) <= 1e-9:
            hi.pop()
        hi.append(q)
    out = lo[:-1] + hi[:-1]
    return out if len(out) >= 3 else []


def plan_rect(p, cut) -> list:
    """평면에서 본 보의 외곽 사각형(반시계 4점, 회전 단면의 폭 범위): 숨은선·평면 가림이 쓴다."""
    ax, ay, ux, uy, ln = axis(p)
    a0, a1 = a_range(p)
    t0, t1 = cut[0], ln - cut[1]
    sx, sy = -uy, ux
    return [(ax + ux * t + sx * a, ay + uy * t + sy * a) for t, a in ((t0, a0), (t1, a0), (t1, a1), (t0, a1))]
