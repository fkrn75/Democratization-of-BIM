# SPDX-License-Identifier: MPL-2.0
"""구조 보(beam)의 순수 형상(W2 열다섯 번째 PR, STR-02·STR-03·STR-07 / 열여덟 번째 PR, 구조 보 2부).

이 모듈은 ifcopenshell·엔진을 모른다(kinds·queries·drawing·commands 가 같은 값을 쓴다). 값은 결정적이다(삼각함수는 solids.sincos_deg).

보 행 params(kinds._beam_params 가 검사한다):
  start, end : [x, y] 평면 두 점(레벨 좌표 mm)
  offset     : 시작점 기준선 높이(레벨 기준 mm). anchor 가 단면의 어디가 기준선에 오는지 정한다(top 윗면·center 가운데·bottom 밑면, 상용 도구의 z 맞춤)
  section    : 단면(아래 SHAPE_KEYS). 모든 단면이 width(평면 폭)·depth(춤)를 갖는다(원형 강관은 width = depth = 바깥 지름)
  선택 end_offset : 끝점 기준선 높이(경사 보, 2부). 없으면 수평 보(offset 과 같은 값은 적지 않는다)
  선택 roll  : 단면 회전(도, 기준선 둘레, 시작점에서 끝점을 볼 때 반시계 +). 없으면 0
  선택 btype : 보 유형 id(관계 of_type 과 같은 뜻). 유형을 고치면 그 유형의 보 행 section·material 을 다시 쓴다(벽·난간 유형과 같은 설계)
  선택 cutback: false 면 기둥 면·보 면 자르기를 하지 않는다(없으면 자른다)
  선택 material: 구조 재료 이름(보 유형 재료의 사본, 2부). 있으면 보의 재료는 이것이다(IFC4 IfcMaterialProfileSetUsage)

단면 좌표 (a, b): a = 진행 방향 왼쪽 +, b = 단면의 위 +. 기준선이 (0, 0) 이다. 경사 보의 단면은 축에 수직이고(웨브는 연직면 안), 끝면도 축에 수직이다.
기둥 접합(STR-03, 단순화): 보 끝점이 평면에서 기둥(IfcColumn 패밀리 인스턴스)의 각기둥 단면 안에 있고 높이가 겹치면, 보 축을 그 기둥 면까지
  줄인다(끝에서 가장 가까운 경계 교차점). 보끼리 접합(2부)도 같은 규칙이다: 다른 보(주 보)의 평면 외곽 안에서 끝나는 보는 그 옆면까지 줄인다.
"""
from __future__ import annotations

from math import pi, sqrt

from dobim_core import solids as SO

# 사각(철근콘크리트 보, IfcRectangleProfileDef)·H형강(IfcIShapeProfileDef)·ㄷ형강(IfcUShapeProfileDef)·ㄱ형강(IfcLShapeProfileDef)·
# 각형 강관(IfcRectangleHollowProfileDef)·원형 강관(IfcCircleHollowProfileDef). 키 = shape 밖의 치수 키
SHAPE_KEYS = {"rect": ("width", "depth"), "h": ("width", "depth", "web", "flange"), "c": ("width", "depth", "web", "flange"),
              "l": ("width", "depth", "thickness"), "box": ("width", "depth", "thickness"), "pipe": ("width", "depth", "thickness")}
SHAPES = tuple(SHAPE_KEYS)
ANCHORS = ("top", "center", "bottom")        # 기준선이 지나는 단면 자리(상용 도구의 z 맞춤: 윗면이 기본)
SEC_DIM_MAX = 3000.0                         # 단면 치수 상한 mm
COORD_MAX = 1e7                              # 좌표 절댓값 상한 mm(다른 요소와 같다)
OFFSET_MAX = 1e5                             # 기준선 높이 절댓값 상한 mm
LEN_MIN = 10.0                               # 보 축 길이 하한 mm(평면 길이)
LEN_MAX = 200000.0                           # 보 축 길이 상한 mm(200 m, 경사 보는 실길이)
CUT_MIN = 10.0                               # 기둥·보 면 자르기 뒤 남아야 하는 평면 길이 mm(못 미치면 자르지 않는다)
BEAMTYPES_MAX = 1000                         # 프로젝트의 보 유형 수 상한
SLOPE_MAX_DEG = 80.0                         # 경사 보 기울기 상한(도). 이보다 서면 기둥·가새의 몫이다
SLOPE_TAN_MAX = 5.671281819617709            # tan(80도)

# H형강 표준 단면(KS D 3502 의 H 형강 치수 표: 춤 H x 폭 B x 웨브 t1 x 플랜지 t2, mm). 필릿 반지름 r 은 형상에 넣지 않는다
H_PRESETS = {
    "H-200x100x5.5x8": (200.0, 100.0, 5.5, 8.0), "H-250x125x6x9": (250.0, 125.0, 6.0, 9.0), "H-300x150x6.5x9": (300.0, 150.0, 6.5, 9.0),
    "H-350x175x7x11": (350.0, 175.0, 7.0, 11.0), "H-400x200x8x13": (400.0, 200.0, 8.0, 13.0), "H-450x200x9x14": (450.0, 200.0, 9.0, 14.0),
    "H-500x200x10x16": (500.0, 200.0, 10.0, 16.0), "H-600x200x11x17": (600.0, 200.0, 11.0, 17.0), "H-588x300x12x20": (588.0, 300.0, 12.0, 20.0),
    "H-700x300x13x24": (700.0, 300.0, 13.0, 24.0), "H-200x200x8x12": (200.0, 200.0, 8.0, 12.0), "H-250x250x9x14": (250.0, 250.0, 9.0, 14.0),
    "H-300x300x10x15": (300.0, 300.0, 10.0, 15.0), "H-350x350x12x19": (350.0, 350.0, 12.0, 19.0), "H-400x400x13x21": (400.0, 400.0, 13.0, 21.0),
}
# ㄷ형강(KS D 3502 의 홈 형강 치수 표: 춤 H x 폭 B x 웨브 t1 x 플랜지 t2), 등변 ㄱ형강(같은 표준의 등변 산형강: A x B x t),
# 원형 강관(KS D 3566 일반 구조용 탄소 강관: 바깥 지름 D x 두께 t), 각형 강관(KS D 3568 일반 구조용 각형 강관: 춤 H x 폭 B x 두께 t). 필릿·모서리 반지름은 넣지 않는다
C_PRESETS = {"C-100x50x5x7.5": (100.0, 50.0, 5.0, 7.5), "C-125x65x6x8": (125.0, 65.0, 6.0, 8.0), "C-150x75x6.5x10": (150.0, 75.0, 6.5, 10.0),
             "C-200x80x7.5x11": (200.0, 80.0, 7.5, 11.0), "C-250x90x9x13": (250.0, 90.0, 9.0, 13.0), "C-300x90x9x13": (300.0, 90.0, 9.0, 13.0)}
L_PRESETS = {"L-50x50x6": (50.0, 50.0, 6.0), "L-65x65x6": (65.0, 65.0, 6.0), "L-75x75x9": (75.0, 75.0, 9.0), "L-90x90x10": (90.0, 90.0, 10.0),
             "L-100x100x10": (100.0, 100.0, 10.0), "L-130x130x12": (130.0, 130.0, 12.0)}
P_PRESETS = {"P-114.3x4.5": (114.3, 4.5), "P-139.8x4.5": (139.8, 4.5), "P-165.2x5": (165.2, 5.0), "P-216.3x5.8": (216.3, 5.8), "P-267.4x6": (267.4, 6.0)}
B_PRESETS = {"B-100x100x4.5": (100.0, 100.0, 4.5), "B-125x125x6": (125.0, 125.0, 6.0), "B-150x150x6": (150.0, 150.0, 6.0),
             "B-200x200x8": (200.0, 200.0, 8.0), "B-250x250x9": (250.0, 250.0, 9.0), "B-200x100x6": (200.0, 100.0, 6.0)}


def _preset_table() -> dict:
    out = {}
    for k, (h, b, t1, t2) in H_PRESETS.items():
        out[k] = {"shape": "h", "width": b, "depth": h, "web": t1, "flange": t2}
    for k, (h, b, t1, t2) in C_PRESETS.items():
        out[k] = {"shape": "c", "width": b, "depth": h, "web": t1, "flange": t2}
    for k, (a, b, t) in L_PRESETS.items():
        out[k] = {"shape": "l", "width": b, "depth": a, "thickness": t}
    for k, (d, t) in P_PRESETS.items():
        out[k] = {"shape": "pipe", "width": d, "depth": d, "thickness": t}
    for k, (h, b, t) in B_PRESETS.items():
        out[k] = {"shape": "box", "width": b, "depth": h, "thickness": t}
    return out


PRESETS = _preset_table()                    # 표준 단면 이름 -> section(이름 = section_label)


def _num(v) -> bool:
    return (type(v) is int or type(v) is float) and v == v and v not in (float("inf"), float("-inf"))


def preset_section(name: str) -> dict:
    """표준 단면 이름 -> section. 없는 이름은 KeyError."""
    return dict(PRESETS[name])


def section_problem(sc) -> str | None:
    """단면 검사(안 되면 이유)."""
    if not isinstance(sc, dict) or sc.get("shape") not in SHAPES:
        return f"section.shape 는 {'/'.join(SHAPES)} 중 하나다"
    need = {"shape"} | set(SHAPE_KEYS[sc["shape"]])
    if set(sc) != need:
        return f"section({sc['shape']}) 의 키가 다르다(" + ", ".join(sorted(need)) + ")"
    if not all(_num(sc[k]) and 0 < sc[k] <= SEC_DIM_MAX for k in sc if k != "shape"):
        return f"section 치수는 0 보다 크고 {SEC_DIM_MAX:g} 이하여야 한다"
    sh = sc["shape"]
    if sh in ("h", "c") and not (sc["web"] < sc["width"] and 2 * sc["flange"] < sc["depth"]):
        return ("H형" if sh == "h" else "ㄷ형") + " 단면은 web < width, 2 x flange < depth 여야 한다"
    if sh == "l" and not sc["thickness"] < min(sc["width"], sc["depth"]):
        return "ㄱ형 단면은 thickness < width, depth 여야 한다"
    if sh == "box" and not 2 * sc["thickness"] < min(sc["width"], sc["depth"]):
        return "각형 강관은 2 x thickness < width, depth 여야 한다"
    if sh == "pipe" and not (sc["width"] == sc["depth"] and 2 * sc["thickness"] < sc["width"]):
        return "원형 강관은 width = depth(바깥 지름)이고 2 x thickness < 지름이어야 한다"
    return None


def section_label(sc) -> str:
    """단면 표기(IFC 프로필 이름·일람표): 사각 'R-폭x춤', H형강 'H-춤x폭x웨브x플랜지'(KS 표기 순서), ㄷ형강 'C-춤x폭x웨브x플랜지',
    ㄱ형강 'L-춤x폭x두께', 각형 강관 'B-춤x폭x두께', 원형 강관 'P-지름x두께'."""
    def f(v):
        return f"{float(v):g}"
    sh = sc["shape"]
    if sh in ("h", "c"):
        return f"{sh.upper()}-{f(sc['depth'])}x{f(sc['width'])}x{f(sc['web'])}x{f(sc['flange'])}"
    if sh in ("l", "box"):
        return f"{'L' if sh == 'l' else 'B'}-{f(sc['depth'])}x{f(sc['width'])}x{f(sc['thickness'])}"
    if sh == "pipe":
        return f"P-{f(sc['width'])}x{f(sc['thickness'])}"
    return f"R-{f(sc['width'])}x{f(sc['depth'])}"


def section_area(sc) -> float:
    """단면적 mm2(원형 강관은 참 원: 도면의 32각형이 아니다)."""
    w, d = float(sc["width"]), float(sc["depth"])
    sh = sc["shape"]
    if sh in ("h", "c"):
        tw, tf = float(sc["web"]), float(sc["flange"])
        return 2.0 * w * tf + tw * (d - 2.0 * tf)
    if sh == "l":
        t = float(sc["thickness"])
        return t * (w + d - t)
    if sh == "box":
        t = float(sc["thickness"])
        return w * d - (w - 2.0 * t) * (d - 2.0 * t)
    if sh == "pipe":
        r0, r1 = w / 2.0, w / 2.0 - float(sc["thickness"])
        return pi * (r0 * r0 - r1 * r1)
    return w * d


def _center_b(sc, anchor) -> float:
    """기준선에서 단면 가운데(경계 상자 가운데)까지의 b(위 +)."""
    d = float(sc["depth"])
    return {"top": -d / 2.0, "center": 0.0, "bottom": d / 2.0}[anchor]


def _boxes(sc) -> list:
    """단면을 이루는 겹치지 않는 축 정렬 사각형들 (a0, a1, b0, b1), 단면 가운데 기준. ㄷ·ㄱ형은 웨브(세로 다리)가 왼쪽(a -)이다
    (IfcUShapeProfileDef·IfcLShapeProfileDef 의 프로필 좌표와 같다: 웨브가 -x, ㄱ형 가로 다리가 -y)."""
    w, d = float(sc["width"]), float(sc["depth"])
    h, hd = w / 2.0, d / 2.0
    sh = sc["shape"]
    if sh == "h":
        tw, tf = float(sc["web"]) / 2.0, float(sc["flange"])
        return [(-h, h, -hd, -hd + tf), (-tw, tw, -hd + tf, hd - tf), (-h, h, hd - tf, hd)]
    if sh == "c":
        tw, tf = float(sc["web"]), float(sc["flange"])
        return [(-h, h, -hd, -hd + tf), (-h, -h + tw, -hd + tf, hd - tf), (-h, h, hd - tf, hd)]
    if sh == "l":
        t = float(sc["thickness"])
        return [(-h, h, -hd, -hd + t), (-h, -h + t, -hd + t, hd)]
    if sh == "box":
        t = float(sc["thickness"])
        return [(-h, h, -hd, -hd + t), (h - t, h, -hd + t, hd - t), (-h, h, hd - t, hd), (-h, -h + t, -hd + t, hd - t)]
    return [(-h, h, -hd, hd)]


def section_quads(p) -> list:
    """단면을 이루는 볼록 사각형들(각 반시계 (a, b) 4점, 회전·기준선 반영): 사각은 1개, H·ㄷ형은 아래 플랜지·웨브·위 플랜지 3개, ㄱ형 2개,
    각형 강관 4개, 원형 강관은 고리 조각 32개(겹치지 않는다). 가림 조각·절단 채움이 쓴다(볼록 각기둥의 평면 절단은 볼록 다각형)."""
    sc = p["section"]
    cb = _center_b(sc, p["anchor"])
    if sc["shape"] == "pipe":
        r0 = float(sc["width"]) / 2.0
        o, i = SO._circle(r0), SO._circle(r0 - float(sc["thickness"]))
        n = len(o)
        return [[_rot(p, q[0], q[1] + cb) for q in (o[k], o[(k + 1) % n], i[(k + 1) % n], i[k])] for k in range(n)]
    return [[_rot(p, a, b + cb) for a, b in ((a0, b0), (a1, b0), (a1, b1), (a0, b1))] for a0, a1, b0, b1 in _boxes(sc)]


def section_outline(p) -> list:
    """단면 바깥 윤곽(반시계 (a, b), 회전·기준선 반영). 사각 4점, H형 12점, ㄷ형 8점, ㄱ형 6점, 각형 강관 4점, 원형 강관 32각형."""
    sc = p["section"]
    w, d = float(sc["width"]), float(sc["depth"])
    cb = _center_b(sc, p["anchor"])
    h, y0, y1 = w / 2.0, -d / 2.0, d / 2.0
    sh = sc["shape"]
    if sh == "h":
        tw, tf = float(sc["web"]) / 2.0, float(sc["flange"])
        pts = [(-h, y0), (h, y0), (h, y0 + tf), (tw, y0 + tf), (tw, y1 - tf), (h, y1 - tf), (h, y1), (-h, y1), (-h, y1 - tf), (-tw, y1 - tf), (-tw, y0 + tf), (-h, y0 + tf)]
    elif sh == "c":
        tw, tf = float(sc["web"]), float(sc["flange"])
        pts = [(-h, y0), (h, y0), (h, y0 + tf), (-h + tw, y0 + tf), (-h + tw, y1 - tf), (h, y1 - tf), (h, y1), (-h, y1)]
    elif sh == "l":
        t = float(sc["thickness"])
        pts = [(-h, y0), (h, y0), (h, y0 + t), (-h + t, y0 + t), (-h + t, y1), (-h, y1)]
    elif sh == "pipe":
        pts = SO._circle(h)
    else:
        pts = [(-h, y0), (h, y0), (h, y1), (-h, y1)]
    return [_rot(p, a, b + cb) for a, b in pts]


def section_holes(p) -> list:
    """단면 구멍(시계 (a, b) 고리들, 회전·기준선 반영): 각형·원형 강관만 하나, 나머지는 []."""
    sc = p["section"]
    sh = sc["shape"]
    if sh not in ("box", "pipe"):
        return []
    cb = _center_b(sc, p["anchor"])
    t = float(sc["thickness"])
    if sh == "pipe":
        pts = SO._circle(float(sc["width"]) / 2.0 - t)[::-1]
    else:
        h, hd = float(sc["width"]) / 2.0 - t, float(sc["depth"]) / 2.0 - t
        pts = [(-h, -hd), (-h, hd), (h, hd), (h, -hd)]
    return [[_rot(p, a, b + cb) for a, b in pts]]


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
    """(시작 x, y, 방향 x, y(단위), 평면 길이). 검사를 통과한 행이라 길이는 LEN_MIN 이상이다."""
    x0, y0 = float(p["start"][0]), float(p["start"][1])
    dx, dy = float(p["end"][0]) - x0, float(p["end"][1]) - y0
    ln = sqrt(dx * dx + dy * dy)
    return (x0, y0, dx / ln, dy / ln, ln)


def sloped(p) -> bool:
    """경사 보인가(끝점 기준선 높이가 따로 있다)."""
    return "end_offset" in p


def slope(p) -> tuple:
    """(cos, sin, 실길이): 축이 수평면과 이루는 각의 코사인·사인(끝이 높으면 sin +)과 축의 3D 길이. 수평 보는 (1, 0, 평면 길이)."""
    ln = axis(p)[4]
    if not sloped(p):
        return (1.0, 0.0, ln)
    dz = float(p["end_offset"]) - float(p["offset"])
    l3 = sqrt(ln * ln + dz * dz)
    return (ln / l3, dz / l3, l3)


def length3(p) -> float:
    """축 실길이(경사 보는 3D 길이, 수평 보는 평면 길이)."""
    return slope(p)[2] if sloped(p) else axis(p)[4]


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
    if sloped(p):
        dz = p["end_offset"] - p["offset"]
        if dz == 0:
            return "end_offset 이 offset 과 같으면 적지 않는다(수평 보)"
        if abs(dz) > ln * SLOPE_TAN_MAX:
            return f"경사 보의 기울기는 {SLOPE_MAX_DEG:g}도 이하여야 한다"
        if sqrt(ln * ln + dz * dz) > LEN_MAX:
            return f"보 실길이는 {LEN_MAX:g} mm 이하여야 한다"
    return None


# ---------------------------------------------------------------- 기둥·보 접합(STR-03 단순화)
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


def end_inside(p, k, post) -> bool:
    """보 p 의 끝 k(0 시작, 1 끝)가 평면에서 post(id, 아래 z, 위 z, 경계 상자, 다각형)의 다각형 안(경계 포함)인가(높이는 보지 않는다)."""
    ex, ey = p["start"] if k == 0 else p["end"]
    bb = post[3]
    return bb[0] - 0.01 <= ex <= bb[2] + 0.01 and bb[1] - 0.01 <= ey <= bb[3] + 0.01 and _point_in((float(ex), float(ey)), post[4])


def trims(p, zr, columns) -> tuple:
    """기둥(보) 면 자르기: (시작에서 줄인 평면 길이, 끝에서 줄인 평면 길이, 시작 기둥 id 또는 None, 끝 기둥 id 또는 None).
    zr = 보의 (아래 z, 위 z) 절대 높이(경사 보는 끝마다 ((아래, 위), (아래, 위))), columns = ((기둥 id, 아래 z, 위 z, 경계 상자 (x0, y0, x1, y1), 세계 다각형), ...).
    끝점이 기둥 단면 안(경계 포함)이고 높이가 1mm 넘게 겹치면 그 끝에서 가장 가까운 경계 교차점까지 줄인다. 여러 기둥이면 가장 많이 줄이는 것.
    남는 길이가 CUT_MIN 보다 짧아지면 줄이지 않는다(두 끝이 같은 기둥 안 등). 보끼리 접합도 주 보를 '기둥'으로 넘겨 같은 함수가 한다."""
    if p.get("cutback") is False or not columns:
        return (0.0, 0.0, None, None)
    zre = zr if isinstance(zr[0], tuple) else (zr, zr)
    ax, ay, ux, uy, ln = axis(p)
    ends = ((ax, ay), (ax + ux * ln, ay + uy * ln))
    best = [(0.0, None), (0.0, None)]
    for cid, z0, z1, bb, poly in columns:
        for k, (ex, ey) in enumerate(ends):
            if min(zre[k][1], z1) - max(zre[k][0], z0) <= 1.0:
                continue
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


def end_zranges(p, z_ref) -> tuple:
    """두 끝의 단면 높이 범위 ((아래, 위), (아래, 위)) 절대 z(자르기 전 축 끝). 수평 보는 둘이 같다."""
    b0, b1 = b_range(p)
    if not sloped(p):
        return ((z_ref + b0, z_ref + b1),) * 2
    c, s, l3 = slope(p)
    z1 = z_ref + s * l3
    return ((z_ref + c * b0, z_ref + c * b1), (z1 + c * b0, z1 + c * b1))


def z_range(p, z_ref, cut) -> tuple:
    """(아래 z, 위 z): 자른 보 전체의 높이 범위(절대). cut = 축 방향(실길이) 자른 길이."""
    b0, b1 = b_range(p)
    if not sloped(p):
        return (z_ref + b0, z_ref + b1)
    c, s, l3 = slope(p)
    zs = [z_ref + s * t + c * b for t in (cut[0], l3 - cut[1]) for b in (b0, b1)]
    return (min(zs), max(zs))


# ---------------------------------------------------------------- 3D 형상(도면·IFC·수량이 같은 값)
def frame(p, z_ref) -> tuple:
    """(원점 (x, y, z) = 기준선 시작점(절대 z_ref), 방향 D, 왼쪽 S, 단면 위 U, 실길이). 경사 보는 D 가 기울고 U 는 축에 수직(연직면 안)이다."""
    ax, ay, ux, uy, ln = axis(p)
    if not sloped(p):
        return ((ax, ay, z_ref), (ux, uy, 0.0), (-uy, ux, 0.0), (0.0, 0.0, 1.0), ln)
    c, s, l3 = slope(p)
    return ((ax, ay, z_ref), (ux * c, uy * c, s), (-uy, ux, 0.0), (-ux * s, -uy * s, c), l3)


def _to3(o, D, S, U, t0):
    if U == (0.0, 0.0, 1.0):   # 수평 보(이전과 같은 식: 부동소수 결과가 같다)
        def f(q, t):
            tt = t0 + t
            return (o[0] + q[0] * S[0] + tt * D[0], o[1] + q[0] * S[1] + tt * D[1], o[2] + q[1] + tt * D[2])
        return f

    def g(q, t):
        tt = t0 + t
        return (o[0] + q[0] * S[0] + q[1] * U[0] + tt * D[0], o[1] + q[0] * S[1] + q[1] * U[1] + tt * D[1], o[2] + q[1] * U[2] + tt * D[2])
    return g


def mesh(p, z_ref, cut) -> tuple:
    """(꼭짓점, 면) 다면체(solids 의 표현): 단면 바깥 윤곽(+ 강관 구멍)을 축 [cut[0], 실길이 - cut[1]] 구간에 돌출. z_ref = 기준선 시작점 절대 높이."""
    o, D, S, U, ln = frame(p, z_ref)
    return SO._extrude(section_outline(p), section_holes(p), _to3(o, D, S, U, cut[0]), None, ln - cut[0] - cut[1])


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
    """평면에서 본 보의 외곽 사각형(반시계 4점, 회전 단면의 폭 범위): 숨은선·평면 가림·보끼리 접합이 쓴다. cut = 축 방향(실길이) 자른 길이.
    경사 보는 축에 수직인 단면이 평면에서 진행 방향으로도 퍼지므로(b x sin) 그 범위까지 넣는다."""
    ax, ay, ux, uy, ln = axis(p)
    a0, a1 = a_range(p)
    sx, sy = -uy, ux
    if not sloped(p):
        t0, t1 = cut[0], ln - cut[1]
    else:
        c, s, l3 = slope(p)
        b0, b1 = b_range(p)
        al = [t * c - b * s for t in (cut[0], l3 - cut[1]) for b in (b0, b1)]
        t0, t1 = min(al), max(al)
    return [(ax + ux * t + sx * a, ay + uy * t + sy * a) for t, a in ((t0, a0), (t1, a0), (t1, a1), (t0, a1))]
