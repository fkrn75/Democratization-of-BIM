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
  선택 bulge : 곡선 보(원호, 3부). 볼록도 tan(중심각/4), 양수 = 반시계(진행 방향 오른쪽으로 부푼다: 지붕·계단·개구의 bulges 와 같은 규약).
               0 < |bulge| <= 1(반원까지). 수평 보만(end_offset·vertical_ends·skew_cut 과 함께 둘 수 없다). start·end 는 호의 두 끝이다
  선택 vertical_ends: true 면 경사 보의 끝면을 축에 수직이 아니라 연직면으로 자른다(3부, 경사 보만)
  선택 skew_cut: true 면 보끼리 접합에서 끝면을 주 보 옆면과 나란한 연직면으로 자른다(3부, 비스듬히 만나는 T 결합의 경사 자르기)
  선택 gap   : 보끼리 접합 간격 mm(3부). 주 보 옆면에서 이만큼 떨어져 끝낸다(기둥 면 자르기에는 적용하지 않는다)

단면 좌표 (a, b): a = 진행 방향 왼쪽 +, b = 단면의 위 +. 기준선이 (0, 0) 이다. 경사 보의 단면은 축에 수직이고(웨브는 연직면 안), 끝면도 축에 수직이다.
기둥 접합(STR-03, 단순화): 보 끝점이 평면에서 기둥(IfcColumn 패밀리 인스턴스)의 각기둥 단면 안에 있고 높이가 겹치면, 보 축을 그 기둥 면까지
  줄인다(끝에서 가장 가까운 경계 교차점). 보끼리 접합(2부)도 같은 규칙이다: 다른 보(주 보)의 평면 외곽 안에서 끝나는 보는 그 옆면까지 줄인다.
"""
from __future__ import annotations

from math import pi, sqrt

from dobim_core import arcgeom as AG
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
BULGE_MAX = 1.0                              # 곡선 보 볼록도 상한(반원)
GAP_MAX = 1000.0                             # 보끼리 접합 간격 상한 mm
SKEW_SIN_MIN = 0.34                          # 경사 자르기: 보 축과 주 보 옆면이 이루는 각의 사인 하한(약 20도). 더 얕으면 끝면이 너무 길어져 맞추지 않는다
ARC_FACET_RAD = 4.5 * pi / 180.0             # 곡선 보 조각 하나의 중심각 상한(4.5도: 이웃 옆면 법선 차가 모서리선 기준(약 5도) 밑이라 현 이음선이 그려지지 않는다)
ARC_N_MAX = 360                              # 곡선 보 하나의 조각 수 상한
SLAB_MM = 10.0                               # 경사 보 가림 조각의 높이 조각 두께(mm)
SLAB_POLYS = 600                             # 경사 보 하나의 가림 다각형 수 예산(조각 수 x 높이 조각 수)
SLAB_MAX = 200                               # 볼록 조각 하나의 높이 조각 수 상한

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


def size_label(sc) -> str:
    """단면 약식 표기(보 태그·도면 글: 웨브·플랜지·두께를 뺀 대표 치수): 사각 'R-폭x춤', H형강 'H-춤x폭', ㄷ형강 'C-춤x폭', ㄱ형강 'L-춤x폭',
    각형 강관 'B-춤x폭', 원형 강관 'P-지름'. 전체 표기는 section_label."""
    def f(v):
        return f"{float(v):g}"
    sh = sc["shape"]
    if sh in ("h", "c", "l", "box"):
        return f"{sh.upper()}-{f(sc['depth'])}x{f(sc['width'])}"
    if sh == "pipe":
        return f"P-{f(sc['width'])}"
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
    """축 실길이(경사 보는 3D 길이, 곡선 보는 호 길이, 수평 직선 보는 평면 길이)."""
    if curved(p):
        return arc_length(p)
    return slope(p)[2] if sloped(p) else axis(p)[4]


# ---------------------------------------------------------------- 곡선 보(3부): 원호 축
def curved(p) -> bool:
    """곡선 보인가(볼록도 bulge 가 있다)."""
    return "bulge" in p


def arc_info(p) -> tuple:
    """곡선 보 축의 (cx, cy, r, a0, sg, sw): 중심·반지름·시작각(라디안)·방향(+1 반시계)·중심각 크기. arcgeom.info 와 같은 뜻이고
    볼록도에서 바로 구한다(bulge_arc 와 같은 식: 현 가운데에서 중심까지 d = c(1 - bu^2)/(4 bu), 왼쪽 법선 방향). 삼각함수는 arcgeom 의 결정적 판."""
    x0, y0 = float(p["start"][0]), float(p["start"][1])
    x1, y1 = float(p["end"][0]), float(p["end"][1])
    dx, dy = x1 - x0, y1 - y0
    c = sqrt(dx * dx + dy * dy)
    bu = float(p["bulge"])
    d = c * (1.0 - bu * bu) / (4.0 * bu)
    cx = (x0 + x1) / 2.0 - dy / c * d
    cy = (y0 + y1) / 2.0 + dx / c * d
    r = c * (1.0 + bu * bu) / (4.0 * abs(bu))
    return (cx, cy, r, AG.atan2(y0 - cy, x0 - cx), 1.0 if bu > 0 else -1.0, 4.0 * AG._atan_small(abs(bu)))


def arc_length(p) -> float:
    """곡선 보 기준선(축)의 호 길이."""
    ai = arc_info(p)
    return ai[2] * ai[5]


def _arc_n(r, phi) -> int:
    """중심각 phi 인 호를 나누는 조각 수: 한 조각 중심각 ARC_FACET_RAD 이하, 현-호 거리 arcgeom.FACET_SAG 이하."""
    step = ARC_FACET_RAD
    k = 2.0 * sqrt(2.0 * AG.FACET_SAG / r)
    if k < step:
        step = k
    n = int(phi / step)
    if n * step < phi:
        n += 1
    return max(1, min(ARC_N_MAX, n))


def arc_stations(p, cut) -> tuple:
    """곡선 보의 조각 경계 (arc_info, 호를 따라 잰 경계 거리 u 목록(자른 구간 [cut[0], 호 길이 - cut[1]] 등분, 양 끝 포함), cos(조각 반각)).
    경계마다 단면은 호의 접선에 수직(방사 평면)이고, 단면의 왼쪽 거리 a 는 이웃 두 현에 평행한 옵셋선에 놓이도록 1/cos(반각) 배로 늘린다(마이터).
    그래서 옆면은 모두 평면이고(회전·구멍 단면도) 조각마다 일정 단면을 현을 따라 돌출한 모양이다."""
    ai = arc_info(p)
    r, sw = ai[2], ai[5]
    u0, u1 = float(cut[0]), r * sw - float(cut[1])
    n = _arc_n(r, (u1 - u0) / r)
    step = (u1 - u0) / n
    us = [u0 + step * k for k in range(n)] + [u1]
    return ai, us, AG.sincos(step / r / 2.0)[1]


def arc_xy(ai, u, cb, a) -> tuple:
    """경계 u 에서 단면의 왼쪽 거리 a 인 점의 평면 좌표(마이터 늘림 포함)."""
    return AG.point(ai, u, a / cb)


def mid_frame(p, cut) -> tuple:
    """보 태그가 놓일 자리 (x, y, 접선 x, 접선 y): 기둥·보 면으로 자른 뒤 남은 축(cut = 시작·끝에서 줄인 축 방향 길이)의 가운데 점과 그 점의 진행 방향
    (평면, 단위 벡터). 곡선 보는 호의 가운데 점과 그 점의 접선, 경사 보는 평면에 투영한 축이다(자른 길이는 실길이라 평면으로 환산)."""
    if curved(p):
        ai = arc_info(p)
        um = (float(cut[0]) + ai[2] * ai[5] - float(cut[1])) / 2.0
        x, y = AG.point(ai, um)
        tx, ty = AG.tangent(ai, um)
        return (x, y, tx, ty)
    x0, y0, ux, uy, ln = axis(p)
    kc = slope(p)[0]
    um = (float(cut[0]) * kc + ln - float(cut[1]) * kc) / 2.0
    return (x0 + ux * um, y0 + uy * um, ux, uy)


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
    if p.get("vertical_ends") is True and not sloped(p):
        return "vertical_ends 는 경사 보(end_offset)에만 줄 수 있다"
    if curved(p):
        for k in ("end_offset", "vertical_ends", "skew_cut"):
            if k in p:
                return f"bulge(곡선 보)와 {k} 는 함께 둘 수 없다(곡선 보는 수평 보다)"
        bu = abs(float(p["bulge"]))
        if ln * bu / 2.0 < AG.ARC_SAG_MIN:
            return f"호 가운데가 현에서 {AG.ARC_SAG_MIN:g} mm 이상 떨어져야 곡선 보다(거의 직선이면 bulge 를 빼세요)"
        ai = arc_info(p)
        if ai[2] > AG.ARC_R_MAX:
            return f"곡선 보 반지름이 {AG.ARC_R_MAX:g} mm 를 넘는다(직선 보를 쓰세요)"
        a0, a1 = a_range(p)
        if ai[2] - max(abs(a0), abs(a1)) * 1.002 < AG.ARC_INNER_MIN:
            return "곡선 보 반지름이 단면 폭의 반보다 커야 한다(안쪽 면 반지름이 1 mm 이상)"
        if ai[2] * ai[5] > LEN_MAX:
            return f"곡선 보 호 길이는 {LEN_MAX:g} mm 이하여야 한다"
    return None


# ---------------------------------------------------------------- 기둥·보 접합(STR-03 단순화)
def _point_in(pt, poly) -> bool:
    """점이 다각형 안이거나 경계 위(허용 오차 0.01mm)인가."""
    return SO.point_in(pt, poly) >= 0


def _crossings(ax, ay, ux, uy, ln, poly) -> list:
    """축선 (ax, ay) + t (ux, uy), 0 <= t <= ln 이 다각형 경계를 지나는 (t, 그 변의 단위 방향) 들."""
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
            el = sqrt(ex * ex + ey * ey)
            out.append((t, (ex / el, ey / el)))
    return out


def _crossings_arc(ai, ln, poly) -> list:
    """곡선 보 축(호, 길이 ln)이 다각형 경계를 지나는 (호를 따라 잰 거리 u, 그 변의 단위 방향) 들. 변과 원의 교점 중 호 안(0 <= u <= ln)에 드는 것."""
    cx, cy, r = ai[0], ai[1], ai[2]
    out = []
    n = len(poly)
    for i in range(n):
        (px, py), (qx, qy) = poly[i], poly[(i + 1) % n]
        ex, ey = qx - px, qy - py
        aa = ex * ex + ey * ey
        if aa < 1e-18:
            continue
        fx, fy = px - cx, py - cy
        bb = 2.0 * (fx * ex + fy * ey)
        disc = bb * bb - 4.0 * aa * (fx * fx + fy * fy - r * r)
        if disc < 0.0:
            continue
        sq = sqrt(disc)
        el = sqrt(aa)
        for s in ((-bb - sq) / (2.0 * aa), (-bb + sq) / (2.0 * aa)):
            if -1e-9 <= s <= 1.0 + 1e-9:
                u = AG.u_of(ai, (px + ex * s, py + ey * s))
                if -1e-6 <= u <= ln + 1e-6:
                    out.append((u, (ex / el, ey / el)))
    return out


def end_inside(p, k, post) -> bool:
    """보 p 의 끝 k(0 시작, 1 끝)가 평면에서 post(id, 아래 z, 위 z, 경계 상자, 다각형)의 다각형 안(경계 포함)인가(높이는 보지 않는다)."""
    ex, ey = p["start"] if k == 0 else p["end"]
    bb = post[3]
    return bb[0] - 0.01 <= ex <= bb[2] + 0.01 and bb[1] - 0.01 <= ey <= bb[3] + 0.01 and _point_in((float(ex), float(ey)), post[4])


def trims_ex(p, zr, columns, gap=0.0, beam_ids=()) -> tuple:
    """기둥(보) 면 자르기: (시작에서 줄인 길이, 끝에서 줄인 길이, 시작 기둥 id 또는 None, 끝 기둥 id 또는 None, 시작 변 방향, 끝 변 방향).
    길이는 직선 보는 평면 길이, 곡선 보는 호를 따라 잰 길이. 변 방향 = 자른 주 보(beam_ids 에 든 것)의 외곽 변의 평면 단위 방향(경사 자르기용), 기둥이면 None.
    zr = 보의 (아래 z, 위 z) 절대 높이(경사 보는 끝마다 ((아래, 위), (아래, 위))), columns = ((기둥 id, 아래 z, 위 z, 경계 상자 (x0, y0, x1, y1), 세계 다각형), ...).
    끝점이 기둥 단면 안(경계 포함)이고 높이가 1mm 넘게 겹치면 그 끝에서 가장 가까운 경계 교차점까지 줄인다. 여러 기둥이면 가장 많이 줄이는 것.
    남는 길이가 CUT_MIN 보다 짧아지면 줄이지 않는다(두 끝이 같은 기둥 안 등). 보끼리 접합도 주 보를 '기둥'으로 넘겨 같은 함수가 한다.
    gap(3부)은 주 보(beam_ids)로 자른 끝에만 더하는 간격이다. 간격을 더해 남는 길이가 CUT_MIN 보다 짧아지면 간격 없이 다시 본다."""
    none = (0.0, 0.0, None, None, None, None)
    if p.get("cutback") is False or not columns:
        return none
    zre = zr if isinstance(zr[0], tuple) else (zr, zr)
    cv = curved(p)
    if cv:
        ai = arc_info(p)
        ln = ai[2] * ai[5]
        ends = ((float(p["start"][0]), float(p["start"][1])), (float(p["end"][0]), float(p["end"][1])))
    else:
        ax, ay, ux, uy, ln = axis(p)
        ends = ((ax, ay), (ax + ux * ln, ay + uy * ln))
    best = [(0.0, None, None), (0.0, None, None)]
    for cid, z0, z1, bb, poly in columns:
        for k, (ex, ey) in enumerate(ends):
            if min(zre[k][1], z1) - max(zre[k][0], z0) <= 1.0:
                continue
            if not (bb[0] - 0.01 <= ex <= bb[2] + 0.01 and bb[1] - 0.01 <= ey <= bb[3] + 0.01) or not _point_in((ex, ey), poly):
                continue
            ts = _crossings_arc(ai, ln, poly) if cv else _crossings(ax, ay, ux, uy, ln, poly)
            if k == 0:
                ts = [x for x in ts if x[0] > 1e-6]
                hit = min(ts, key=lambda x: x[0]) if ts else None
                cut = hit[0] if hit else None
            else:
                ts = [x for x in ts if x[0] < ln - 1e-6]
                hit = max(ts, key=lambda x: x[0]) if ts else None
                cut = ln - hit[0] if hit else None
            if cut is not None and cut > best[k][0] + 1e-9:
                best[k] = (cut, cid, hit[1])
    for use_gap in ((True, False) if gap > 0.0 else (False,)):
        c = [best[k][0] + (gap if use_gap and best[k][1] in beam_ids else 0.0) for k in (0, 1)]
        if ln - c[0] - c[1] >= CUT_MIN:
            return (c[0], c[1], best[0][1], best[1][1],
                    best[0][2] if best[0][1] in beam_ids else None, best[1][2] if best[1][1] in beam_ids else None)
    return none


def trims(p, zr, columns) -> tuple:
    """trims_ex 의 앞 넷(자르기 길이 둘과 상대 id 둘). 간격·변 방향 없이."""
    return trims_ex(p, zr, columns)[:4]


def end_shears(p, edges) -> tuple | None:
    """끝면이 축에 수직이 아닌 보의 끝면 기울기: ((ka0, kb0), (ka1, kb1)) 또는 None(두 끝 모두 축에 수직).
    끝 하나의 끝면은 연직면이고, 단면 점 (a, b)(a = 왼쪽, b = 단면 위)의 끝 위치가 축 방향으로 ka·a + kb·b 만큼 밀린다(축 방향 길이).
    kb = 경사 보의 연직 끝면(vertical_ends 이거나 경사 자르기가 걸린 끝): 평면 위치가 b 에 안 따르게 tan(경사각) = s/c.
    ka = 경사 자르기(skew_cut): 끝이 닿은 주 보 외곽 변(edges 의 단위 방향)과 나란한 연직면. 보 축과 변이 이루는 각의 사인이 SKEW_SIN_MIN 보다 작으면 맞추지 않는다."""
    vert = p.get("vertical_ends") is True
    skew = p.get("skew_cut") is True
    if not vert and not skew or curved(p):
        return None
    c, s, _l3 = slope(p)
    tb = s / c if sloped(p) else 0.0
    _ax, _ay, ux, uy, _ln = axis(p)
    out = []
    for k in (0, 1):
        ka = 0.0
        e = edges[k] if skew and edges else None
        if e is not None:
            nx, ny = -e[1], e[0]
            nu = nx * ux + ny * uy
            ns = -nx * uy + ny * ux
            if abs(nu) >= SKEW_SIN_MIN:
                ka = -ns / (nu * c)
        out.append((ka, tb if (vert or ka != 0.0) else 0.0))
    if all(x == 0.0 and y == 0.0 for x, y in out):
        return None
    return tuple(out)


def clip_planes(p, cut, ends) -> tuple:
    """끝면이 축에 수직이 아닌 보(end_shears 가 낸 ends)의 IFC 자르기용 값: (두 끝의 연직 자르기 면, 돌출 시작·끝 어긋남).
    자르기 면 = (축 위 점의 배치 안 좌표 (x, y, z), 안쪽을 향한 수평 법선 (nx, ny)): 배치는 자른 시작점이 원점이고 x = 평면 방향이다.
    돌출은 (시작 어긋남 lo, 끝 어긋남 hi) 만큼 더 길게(축 방향) 한 뒤 두 면으로 잘라낸다(잘라낼 자리가 1mm 여유 안에 든다)."""
    c, s, _l3 = slope(p)
    ln = length3(p)
    L = ln - cut[0] - cut[1]
    _ax, _ay, ux, uy, _l = axis(p)
    outline = section_outline(p)
    planes, lo, hi = [], 0.0, L
    for k in (0, 1):
        ka, kb = ends[k]
        if ka == 0.0:
            nx, ny = (1.0, 0.0) if k == 0 else (-1.0, 0.0)
        else:
            # 끝면 평면은 옆으로 a 만큼 갈 때 평면 위치가 ka·c·a 밀리는 연직면이다: 법선 (축 성분, 왼쪽 성분) ∝ (1, -ka c)
            nl = sqrt(ka * ka * c * c + 1.0)
            nx, ny = 1.0 / nl, -ka * c / nl
            if k == 1:
                nx, ny = -nx, -ny
        t = 0.0 if k == 0 else L
        planes.append(((t * c, 0.0, t * s), (nx, ny)))
        sh = [q[0] * ka + q[1] * kb for q in outline]
        if k == 0:
            lo = min(sh)
        else:
            hi = L + max(sh)
    return tuple(planes), (lo - 1.0, hi + 1.0)


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


def _to3_ends(o, D, S, U, t0, L, ends):
    """끝면이 축에 수직이 아닌 보의 to3(2D 점, t): t == 0 은 시작 끝면, 그 밖은 끝 끝면(t = 자른 길이 L). 끝면 점은 (a, b) 에 따라 축 방향으로
    ka·a + kb·b 만큼 밀린다(end_shears). 옆면은 모두 축에 평행한 모서리를 가져 평면이다."""
    f = _to3(o, D, S, U, t0)
    (a0, b0), (a1, b1) = ends

    def g(q, t):
        if t == 0.0:
            return f(q, q[0] * a0 + q[1] * b0)
        return f(q, L + q[0] * a1 + q[1] * b1)
    return g


def _curved_mesh(p, z_ref, cut) -> tuple:
    """곡선 보 다면체: 호를 나눈 경계(arc_stations)마다 단면 바깥 윤곽(+ 강관 구멍)을 놓고 이웃 경계끼리 잇는다. 양 끝은 호의 접선에 수직인 방사 평면이다."""
    ai, us, cb = arc_stations(p, cut)
    loops = [section_outline(p)] + list(section_holes(p))
    verts, faces = [], []
    blk = []   # blk[고리][경계] = 그 고리의 첫 꼭짓점 번호
    for lp in loops:
        row = []
        for u in us:
            row.append(len(verts))
            for a, b in lp:
                x, y = arc_xy(ai, u, cb, a)
                verts.append((x, y, z_ref + b))
        blk.append(row)
    n = len(us) - 1
    faces.append([[blk[i][0] + k for k in range(len(lp))][::-1] for i, lp in enumerate(loops)])
    faces.append([[blk[i][n] + k for k in range(len(lp))] for i, lp in enumerate(loops)])
    for i, lp in enumerate(loops):
        m = len(lp)
        for s in range(n):
            b0, b1 = blk[i][s], blk[i][s + 1]
            for k in range(m):
                j = (k + 1) % m
                faces.append([[b0 + k, b0 + j, b1 + j, b1 + k]])
    return verts, faces


def mesh(p, z_ref, cut, ends=None) -> tuple:
    """(꼭짓점, 면) 다면체(solids 의 표현): 단면 바깥 윤곽(+ 강관 구멍)을 축 [cut[0], 실길이 - cut[1]] 구간에 돌출. z_ref = 기준선 시작점 절대 높이.
    ends = end_shears 값(끝면이 축에 수직이 아닌 보, 3부)이면 끝면을 연직면으로 비스듬히 자른다. 곡선 보는 호를 따라 쓴다."""
    if curved(p):
        return _curved_mesh(p, z_ref, cut)
    o, D, S, U, ln = frame(p, z_ref)
    L = ln - cut[0] - cut[1]
    if ends is None:
        return SO._extrude(section_outline(p), section_holes(p), _to3(o, D, S, U, cut[0]), None, L)
    return SO._extrude(section_outline(p), section_holes(p), _to3_ends(o, D, S, U, cut[0], L, ends), None, L)


def pieces(p, z_ref, cut, ends=None) -> list:
    """볼록 조각(사각형 단면 각기둥)마다 꼭짓점 8개 [(아래 4: 시작 끝면), (위 4: 끝 끝면)]. 절단 채움·가림이 쓴다.
    곡선 보는 현(호 조각)마다 조각을 낸다(이웃 경계의 마이터 단면 사이). ends(3부)는 mesh 와 같다."""
    if curved(p):
        ai, us, cb = arc_stations(p, cut)
        quads = section_quads(p)
        out = []
        for k in range(len(us) - 1):
            for quad in quads:
                out.append([(*arc_xy(ai, us[k], cb, a), z_ref + b) for a, b in quad] + [(*arc_xy(ai, us[k + 1], cb, a), z_ref + b) for a, b in quad])
        return out
    o, D, S, U, ln = frame(p, z_ref)
    L = ln - cut[0] - cut[1]
    if ends is None:
        f = _to3(o, D, S, U, cut[0])
        return [[f(q, 0.0) for q in quad] + [f(q, L) for q in quad] for quad in section_quads(p)]
    g = _to3_ends(o, D, S, U, cut[0], L, ends)
    return [[g(q, 0.0) for q in quad] + [g(q, L) for q in quad] for quad in section_quads(p)]


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


def plan_rect(p, cut, ends=None) -> list:
    """평면에서 본 보의 외곽(반시계 다각형, 회전 단면의 폭 범위): 숨은선·평면 가림·보끼리 접합이 쓴다. cut = 축 방향(실길이) 자른 길이.
    직선 보는 사각형 4점이다. 경사 보는 축에 수직인 단면이 평면에서 진행 방향으로도 퍼지므로(b x sin) 그 범위까지 넣는다.
    ends(3부)가 있으면 끝면이 비스듬한 평행사변형이다. 곡선 보는 호를 따라 오른쪽 가장자리(a 최소)를 가고 왼쪽 가장자리(a 최대)로 돌아오는 다각형이다."""
    ax, ay, ux, uy, ln = axis(p)
    a0, a1 = a_range(p)
    if curved(p):
        ai, us, cb = arc_stations(p, cut)
        return [arc_xy(ai, u, cb, a0) for u in us] + [arc_xy(ai, u, cb, a1) for u in reversed(us)]
    sx, sy = -uy, ux
    if ends is not None:
        c, s, l3 = slope(p)
        b0, b1 = b_range(p)
        L = l3 - cut[0] - cut[1]
        pts = []
        for k, a in ((0, a0), (1, a0), (1, a1), (0, a1)):
            ka, kb = ends[k]
            t = cut[0] if k == 0 else cut[0] + L
            xs = [(t + a * ka + b * kb) * c - b * s for b in (b0, b1)]
            x = min(xs) if k == 0 else max(xs)
            pts.append((ax + ux * x + sx * a, ay + uy * x + sy * a))
        return pts
    if not sloped(p):
        t0, t1 = cut[0], ln - cut[1]
    else:
        c, s, l3 = slope(p)
        b0, b1 = b_range(p)
        al = [t * c - b * s for t in (cut[0], l3 - cut[1]) for b in (b0, b1)]
        t0, t1 = min(al), max(al)
    return [(ax + ux * t + sx * a, ay + uy * t + sy * a) for t, a in ((t0, a0), (t1, a0), (t1, a1), (t0, a1))]


# ---------------------------------------------------------------- 수량(곡선 보)과 경사 보 가림 조각(3부)
def centroid(p) -> tuple:
    """단면 도심 (a, b)(a = 왼쪽 +, b = 위 +, 기준선 기준, 회전·기준선 반영). 곡선 보 체적(파푸스)과 끝면이 비스듬한 보 체적이 쓴다. 원형 강관은 가운데."""
    sc = p["section"]
    cb = _center_b(sc, p["anchor"])
    if sc["shape"] == "pipe":
        ac, bc = 0.0, 0.0
    else:
        tot = sx = sy = 0.0
        for a0, a1, b0, b1 in _boxes(sc):
            ar = (a1 - a0) * (b1 - b0)
            tot += ar
            sx += ar * (a0 + a1) / 2.0
            sy += ar * (b0 + b1) / 2.0
        ac, bc = sx / tot, sy / tot
    r = p.get("roll")
    if not r:
        return (ac, bc + cb)
    sn, cs = SO.sincos_deg(float(r))
    return (ac * cs - (bc + cb) * sn, ac * sn + (bc + cb) * cs)


def centroid_a(p) -> float:
    """단면 도심의 a(왼쪽 +)."""
    return centroid(p)[0]


def volume(p, cl, ends=None) -> float:
    """자른 길이 cl(기준선 길이) 보의 체적 mm3. 직선 보 = 단면적 x cl. 곡선 보는 단면 도심이 기준선에서 a 만큼 옆이면 도심의 호 길이가
    cl x (1 - sg·a/r) 라 체적 = 단면적 x 그 길이(파푸스 정리: 안쪽이 짧다). 단면이 a 대칭(사각·H·강관)이면 단면적 x cl 이다.
    끝면이 비스듬하면(ends, end_shears) 끝면마다 단면 점이 밀리는 평균(도심)만큼 길이가 달라진다: cl + (끝 밀림 - 시작 밀림)의 도심 값."""
    a = section_area(p["section"])
    if ends is not None:
        ca, cb = centroid(p)
        return a * (cl + (ends[1][0] * ca + ends[1][1] * cb) - (ends[0][0] * ca + ends[0][1] * cb))
    if not curved(p):
        return a * cl
    ai = arc_info(p)
    return a * cl * (1.0 - ai[4] * centroid_a(p) / ai[2])


def convex_clip(subject, clipper) -> list:
    """볼록 다각형(반시계) subject 를 볼록 다각형(반시계) clipper 안쪽으로 자른다(Sutherland-Hodgman). 겹치지 않으면 []."""
    out = list(subject)
    n = len(clipper)
    for i in range(n):
        if not out:
            return []
        (cx, cy), (dx, dy) = clipper[i], clipper[(i + 1) % n]
        ex, ey = dx - cx, dy - cy
        nxt = []
        m = len(out)
        for j in range(m):
            pt, qt = out[j], out[(j + 1) % m]
            fp = ex * (pt[1] - cy) - ey * (pt[0] - cx)
            fq = ex * (qt[1] - cy) - ey * (qt[0] - cx)
            if fp >= 0.0:
                nxt.append(pt)
            if (fp >= 0.0) != (fq >= 0.0):
                t = fp / (fp - fq)
                nxt.append((pt[0] + t * (qt[0] - pt[0]), pt[1] + t * (qt[1] - pt[1])))
        out = nxt
    return out


def slab_slices(pcs) -> list:
    """볼록 조각들(꼭짓점 8개씩)을 높이 조각으로 썬다: ((z0, z1, 평면 볼록 다각형(반시계)), ...). 조각의 다각형은 높이 구간 [z0, z1] 전체를 덮는 자리
    (두 높이의 수평 절단 다각형의 교집합)라 보가 실제로 가리지 않는 곳을 가리지 않는다(안쪽 근사: 기운 면 근처 한 조각 높이 만큼 덜 가린다).
    조각 높이는 SLAB_MM 이고, 가림 다각형 수가 SLAB_POLYS 를 넘지 않게 조각당 높이 조각 수를 SLAB_MAX 안에서 줄인다.
    수직 각기둥이 아닌 조각(경사 보)의 단면·입면 가림에 쓴다(지붕 판이 높이 조각으로 가리는 것과 같은 방식)."""
    out = []
    kmax = max(4, min(SLAB_MAX, SLAB_POLYS // max(1, len(pcs))))
    for pc in pcs:
        zs = [q[2] for q in pc]
        zlo, zhi = min(zs), max(zs)
        span = zhi - zlo
        if span <= 1e-6:
            continue
        k = max(1, min(kmax, int(span / SLAB_MM + 0.999999)))
        h = span / k
        cuts = [convex_order([(q[0], q[1]) for q in piece_cut(pc, (0.0, 0.0, 1.0), zlo + h * i)]) for i in range(k + 1)]
        for i in range(k):
            a, b = cuts[i], cuts[i + 1]
            if len(a) < 3 or len(b) < 3:
                continue
            poly = convex_clip(a, b)
            if len(poly) >= 3:
                poly = convex_order(poly)
            if len(poly) >= 3 and abs(sum(poly[j][0] * poly[(j + 1) % len(poly)][1] - poly[(j + 1) % len(poly)][0] * poly[j][1] for j in range(len(poly)))) > 1e-6:
                out.append((zlo + h * i, zlo + h * (i + 1), tuple(poly)))
    return out
