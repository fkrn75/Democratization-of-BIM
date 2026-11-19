# SPDX-License-Identifier: MPL-2.0
"""시트·뷰 그림 요소 -> DXF (1단계 도면 엔진, LN-10).

시트(sheet.Sheet)가 이미 가진 그림 요소 목록(선·다각형·원·문자)을 DXF R2013(AC1027, ASCII, UTF-8)으로 직접 쓴다.
SVG·PDF·PNG 와 같은 요소를 그대로 옮기므로 그림이 같다.

설계 결정(이유는 docs/baseline.md 'DXF 내보내기' 절):
- 라이브러리 없이 직접 쓴다. 필요한 엔티티가 LINE·LWPOLYLINE·CIRCLE·HATCH(SOLID)·TEXT 다섯 가지뿐이고, 직접 쓰면 핸들·헤더를
  완전히 정할 수 있어 같은 입력이 같은 바이트가 된다(날짜·GUID·핸들 고정). 새 런타임 의존과 동봉 고지도 늘지 않는다.
- 치수는 DIMENSION 엔티티가 아니라 분해한 선(보조선·치수선·45도 눈금)과 문자로 쓴다. 이유: 시트의 치수는 이미 그림 요소로
  분해되어 있고(PDF·SVG 와 같은 그림), DIMENSION 은 익명 블록(*D)·치수 스타일 변수 수십 개를 함께 써야 해서 CAD 마다 다시 그린
  모양이 PDF 와 달라질 수 있다. 대신 A-DIM 층에 모아 CAD 에서 층 단위로 다루게 한다.
- 레이어는 그림 요소의 그룹(시트의 'title-block'·'hidden-lines' 등)과 선 굵기·색·파선에서 범주를 정한다(LAYERS 표).
  층마다 선 굵기(lineweight)·선 종류·색을 정하고, 층 기본값과 다른 요소만 개별 값을 쓴다.
- 문자는 TEXT 엔티티, 글꼴은 스타일 DOBIM_KR(맑은 고딕 malgun.ttf, 굵게는 DOBIM_KR_B = malgunbd.ttf). R2007 이후 DXF 는
  UTF-8 이라 한글을 그대로 담는다($DWGCODEPAGE 는 ANSI_949 로 적는다).
- 좌표: 시트는 용지 mm 1:1(뷰는 시트에 자기 축척으로 이미 줄어 있다). 뷰 하나(view.export)는 모델 mm 실척으로 되돌려 쓴다:
  선·문자 위치는 모델 좌표(평면은 X·Y, 단면·입면은 가로·높이), 문자 높이·파선 길이는 용지 mm x 축척. 선 굵기는 출력 굵기라 그대로.
- 흰 채움(부호 태그 원의 바탕 가림)은 쓰지 않는다(CAD 바탕색이 어둡거나 밝을 수 있어 흰 해치는 오히려 가린다). 윤곽선만 쓴다.
순수 모듈이다(파일·시계를 모른다).
"""
from __future__ import annotations

import re

ACADVER = "AC1027"          # AutoCAD 2013 형식: UTF-8 문자열
CODEPAGE = "ANSI_949"
FONT_FILE, FONT_FILE_BOLD = "malgun.ttf", "malgunbd.ttf"
STYLE_NAME, STYLE_BOLD = "DOBIM_KR", "DOBIM_KR_B"
# DXF 가 받는 선 굵기 값(1/100 mm). 그림 요소의 굵기(mm)는 가장 가까운 값으로 옮긴다
LW_STD = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)
INK = "#000000"
# 층: 이름 -> (ACI 색, 층 트루컬러(없으면 ACI 색 그대로), 선 굵기 1/100mm, 기본 파선(용지 mm, 없으면 실선), 설명)
LAYERS: dict[str, tuple[int, str | None, int, tuple | None, str]] = {
    "A-CUT": (7, None, 50, None, "절단선(벽·기둥·슬래브 등 잘린 면의 윤곽)"),
    "A-CUT-NB": (7, None, 35, None, "비내력벽 절단선(선 굵기 위계 옵션)"),
    "A-PROJ": (7, None, 18, None, "보이는 선(입면·마감·투상)"),
    "A-SYMB": (7, "#333333", 25, None, "창호·기호선"),
    "A-HATCH": (8, "#555555", 9, None, "해칭선"),
    "A-FILL": (9, "#cfcfcf", 0, None, "절단면 채움(솔리드 해치)"),
    "A-HIDDEN": (7, None, 18, (2.4, 1.2), "숨은 선(파선)"),
    "A-DATUM": (8, "#555555", 13, (3.0, 1.5), "레벨 기준선과 레벨 문자"),
    "A-DIM": (5, "#1a4fa0", 13, None, "치수(분해: 보조선·치수선·눈금·치수 문자)"),
    "A-TAG": (30, "#8a3b00", 13, None, "부호 태그·지시선·실번호"),
    "A-TEXT": (7, None, 18, None, "실명·면적·방화 표기 문자"),
    "A-GRID": (7, None, 25, None, "통심선(1점쇄선은 선분으로 분해)·그리드 기호"),
    "A-MARK": (7, None, 35, None, "단면·입면·상세 기호"),
    "A-ANNO-ERR": (1, "#c62828", 13, None, "고아·무효 주석 표시"),
    "A-DRAFT": (7, None, 25, None, "2D 작도 요소(선·문자·치수·해치, 그룹이 층을 정하지 않을 때)"),
    "G-CAPTION": (7, None, 25, None, "뷰 도면명·축척 문자"),
    "G-BORDER": (7, None, 70, None, "시트 윤곽선"),
    "G-TITLE": (7, None, 35, None, "표제란·개정란·서명·방위표·축척 막대·키 플랜·투상법 기호"),
    "G-REV": (7, None, 25, None, "개정 구름·차수 기호"),
    "G-TABLE": (7, None, 25, None, "도면목록표·일람표·범례·주기"),
    "G-SHEET": (7, None, 25, None, "그 밖의 시트 요소"),
}
# 시트 그룹 id -> 층(안쪽 그룹이 먼저 맞으면 그 층). 'view-*' 그룹 안의 나머지는 선 굵기·색으로 정한다
GROUP_LAYER = {
    "sheet-border": "G-BORDER", "title-block": "G-TITLE", "revision-cloud": "G-REV",
    "hidden-lines": "A-HIDDEN", "cut-nonbearing": "A-CUT-NB", "grid-lines": "A-GRID",
    "section-markers": "A-MARK", "elevation-markers": "A-MARK", "detail-callouts": "A-MARK",
    "room-numbers": "A-TAG", "opening-tags": "A-TAG", "wall-tags": "A-TAG",
    "elevation-dims": "A-DIM", "corridor-dims": "A-DIM", "stair-dims": "A-DIM",
    "legend": "G-TABLE", "notes": "G-TABLE", "drawing-list": "G-TABLE", "level-note": "G-TABLE", "opening-schedule": "G-TABLE",
    "wall-schedule": "G-TABLE", "material-schedule": "G-TABLE",
}
DRAFT_LAYER = "A-DRAFT"     # 'draft-' 로 시작하는 그룹(2D 작도 요소)의 기본 층
_BAD_NAME = re.compile(r'[<>/\\":;?*|=`,\x00-\x1f]')


def layer_props(name: str) -> tuple[int, str | None, int, tuple | None, str]:
    """층 속성. 표에 없는 층(그룹의 data-layer 로 받은 이름)은 검정·0.25mm·실선."""
    return LAYERS.get(name) or (7, None, 25, None, "그룹이 지정한 층")


def safe_layer(name: str) -> str:
    """DXF 층 이름에 못 쓰는 글자를 '_' 로 바꾸고 255자로 자른다. 비면 '0'."""
    s = _BAD_NAME.sub("_", str(name)).strip()[:255]
    return s or "0"


CLS_LAYER = {"cut": "A-CUT", "proj": "A-PROJ", "symbol": "A-SYMB", "hatch": "A-HATCH"}
DATUM_DASH = (3.0, 1.5)
ANNOT_COLOR, TAG_COLOR, ORPHAN_COLOR, FILL_GRAY = "#1a4fa0", "#8a3b00", "#c62828", "#cfcfcf"
# 가까운 ACI 를 고르는 작은 표(정확한 색은 트루컬러 420 으로 함께 쓴다)
_ACI = {1: "#ff0000", 2: "#ffff00", 3: "#00ff00", 4: "#00ffff", 5: "#0000ff", 6: "#ff00ff", 7: "#000000", 8: "#808080", 9: "#c0c0c0",
        30: "#ff7f00", 250: "#333333", 251: "#505050", 252: "#696969", 253: "#828282", 254: "#bebebe"}


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _near_aci(h: str) -> int:
    r, g, b = _rgb(h)
    return min(sorted(_ACI), key=lambda k: (lambda q: (q[0] - r) ** 2 + (q[1] - g) ** 2 + (q[2] - b) ** 2)(_rgb(_ACI[k])))


def _layer_hex(name: str) -> str:
    aci, tc = layer_props(name)[0], layer_props(name)[1]
    return tc or _ACI.get(aci, INK)


def lineweight(w: float) -> int:
    """굵기 mm -> DXF 선 굵기(1/100 mm 표준값)."""
    v = w * 100.0
    return min(LW_STD, key=lambda s: (abs(s - v), s))


def _f(v: float) -> str:
    """실수 문자열(결정적): 소수 6자리에서 반올림, 끝 0 제거, -0 은 0."""
    s = f"{v:.6f}".rstrip("0")
    if s.endswith("."):
        s += "0"
    return "0.0" if s in ("-0.0", "0.0") else s


def _txt(s: str) -> str:
    """TEXT 문자열 안전 처리: 줄바꿈·제어문자는 빈칸, '^' 는 '^ '(캐럿 제어 표기 회피), '%' 는 '%%%'(%%c 같은 특수 기호 회피)."""
    s = "".join(" " if ord(c) < 32 or c in "  " else c for c in str(s))
    return s.replace("^", "^ ").replace("%", "%%%")


def _ltype_name(dash: tuple) -> str:
    return "DOBIM_D" + "_".join(f"{v:g}".replace(".", "P") for v in dash)


# ---------------------------------------------------------------- 층 분류
def classify(p: tuple, groups: list, style_rev: dict, in_view: bool, captions: frozenset) -> str:
    """그림 요소 하나의 층. groups: 바깥 -> 안쪽 (그룹 id, 속성). style_rev: (굵기, 색) -> 선 등급(cut/proj/symbol/hatch).
    안쪽 그룹부터 본다: 속성 data-layer 가 있으면 그 이름(일반 규칙, 2D 작도 요소 등 새 요소가 층을 직접 정한다),
    id 가 GROUP_LAYER 에 있으면 그 층, 'draft-' 로 시작하면 A-DRAFT."""
    for gid, attrs in reversed(groups):
        if (attrs or {}).get("data-layer"):
            return safe_layer(attrs["data-layer"])
        if gid.startswith("draft-"):
            return DRAFT_LAYER
        if gid in GROUP_LAYER:
            return GROUP_LAYER[gid]
        if gid.startswith("view-"):
            in_view = True
            break
    if not in_view:
        return "G-SHEET"
    k = p[0]
    if k == "text":
        _, _x, _y, _size, s, anchor, col, _ang, bold = p
        if bold or s in captions or (anchor == "start" and s.startswith("S=1:")):
            return "G-CAPTION"
        return {ANNOT_COLOR: "A-DIM", TAG_COLOR: "A-TAG", ORPHAN_COLOR: "A-ANNO-ERR", "#555555": "A-DATUM"}.get(col, "A-TEXT")
    if k == "circle":
        return {TAG_COLOR: "A-TAG", ORPHAN_COLOR: "A-ANNO-ERR", ANNOT_COLOR: "A-DIM"}.get(p[5], "A-MARK")
    if k == "poly":
        _, _pts, fill, w, col = p
        if fill and not w:
            return "A-FILL" if fill == FILL_GRAY else "A-MARK"
        return CLS_LAYER.get(style_rev.get((round(w, 3), col)), "A-MARK")
    _, _x0, _y0, _x1, _y1, w, col, dash = p
    if dash == DATUM_DASH and col == "#555555":
        return "A-DATUM"
    if col in (ANNOT_COLOR, TAG_COLOR, ORPHAN_COLOR):
        return {ANNOT_COLOR: "A-DIM", TAG_COLOR: "A-TAG", ORPHAN_COLOR: "A-ANNO-ERR"}[col]
    return CLS_LAYER.get(style_rev.get((round(w, 3), col)), "A-MARK")


# ---------------------------------------------------------------- 쓰기
class _Out:
    def __init__(self):
        self.buf: list[str] = []

    def __call__(self, code: int, val) -> None:
        self.buf.append(f"{code:>3}\n{val}\n")

    def text(self) -> str:
        return "".join(self.buf)


class _Handles:
    def __init__(self):
        self.n = 0x10

    def __call__(self) -> str:
        self.n += 1
        return f"{self.n:X}"


def render_dxf(sh, style: dict | None = None, model: dict | None = None, captions=()) -> tuple[bytes, dict]:
    """시트(sh.prims·sh.w·sh.h)를 DXF 바이트로 쓴다. 반환: (바이트, 요약).

    style: 이 시트가 쓴 선 굵기 체계(선 등급 -> (굵기, 색)). 없으면 drawing_export.STYLE.
    model: 뷰 하나를 모델 mm 실척으로 되돌릴 때 {"scale", "x0", "y1", "margin"}(drawing_export._frame 의 역변환). 없으면 용지 mm.
    captions: 도면명으로 볼 문자(뷰 페이지의 제목 줄)."""
    from dobim_core import drawing_export as DX
    st = style or DX.STYLE
    style_rev = {(round(w, 3), c): cls for cls, (w, c) in st.items()}
    if model:
        # 아래 반복문의 지역 이름(x0·y1 등)과 겹치지 않게 기본 인자로 묶는다(클로저가 나중 값을 보지 않도록)
        s, mx0, my1, m = float(model["scale"]), float(model["x0"]), float(model["y1"]), float(model["margin"])
        X = lambda v, _a=mx0, _m=m, _s=s: _a + (v - _m) * _s       # noqa: E731
        Y = lambda v, _b=my1, _m=m, _s=s: _b - (v - _m) * _s       # noqa: E731
        k = s
    else:
        H = float(sh.h)
        X = lambda v: v                          # noqa: E731
        Y = lambda v: H - v                      # noqa: E731
        k = 1.0
    caps = frozenset(captions)

    # 1) 그림 요소 -> (층, 요소) 목록과 쓰인 파선 종류
    items: list[tuple[str, tuple]] = []
    groups: list[str] = []
    dashes = {d for d in (LAYERS[n][3] for n in LAYERS) if d}
    for p in sh.prims:
        if p[0] == "g":
            groups.append((str(p[1]), p[2] if len(p) > 2 else {}))
            continue
        if p[0] == "/g":
            if groups:
                groups.pop()
            continue
        layer = classify(p, groups, style_rev, model is not None, caps)
        if p[0] == "poly" and p[2] and p[2].lower() == "#ffffff" and groups and groups[-1][0].startswith("draft-"):
            p = ("mask", p[1])   # 2D 작도 마스킹 영역(흰 칠): 종이 흰색 칠은 건너뛰지만 이것은 아래 선을 가려야 하니 흰 SOLID 해치로 쓴다
        items.append((layer, p))
        if p[0] == "line" and p[7]:
            dashes.add(tuple(p[7]))
    used_layers = sorted({ly for ly, _ in items})   # 쓰인 층만 층 표에 싣는다(0 층은 늘 있다)

    h = _Handles()
    H_ = {key: h() for key in ("t_vport", "t_ltype", "t_layer", "t_style", "t_view", "t_ucs", "t_appid", "t_dimstyle", "t_brec",
                                "vport", "lt_byblock", "lt_bylayer", "lt_cont", "ly_0", "st_std", "st_kr", "st_krb", "app_acad", "ds_std",
                                "br_ms", "br_ps", "blk_ms", "end_ms", "blk_ps", "end_ps",
                                "d_root", "d_group", "d_layout", "d_mline", "d_plotset", "d_psname", "ph_normal", "ml_std", "lo_model", "lo_l1")}
    lt_h = {d: h() for d in sorted(dashes)}
    ly_h = {n: h() for n in used_layers if n != "0"}   # 0 층은 늘 따로 쓴다

    # 2) 엔티티(먼저 만들어 범위를 잰다)
    E = _Out()
    pts: list[tuple[float, float]] = []
    counts = {"LINE": 0, "LWPOLYLINE": 0, "CIRCLE": 0, "HATCH": 0, "TEXT": 0}
    per_layer: dict[str, int] = {}
    skipped = 0

    def head(kind, layer, col=None, w=None, dash=None):
        E(0, kind)
        E(5, h())
        E(330, H_["br_ms"])
        E(100, "AcDbEntity")
        E(8, layer)
        _aci, _tc, lw, ldash, _ = layer_props(layer)
        if dash is not None and tuple(dash) != (ldash or None):
            E(6, _ltype_name(tuple(dash)))
        elif dash is None and ldash and kind == "LINE":
            E(6, "Continuous")
        if col is not None and col.lower() != _layer_hex(layer):
            E(62, 7 if col == INK else _near_aci(col))
            if col != INK:
                r, g, b = _rgb(col)
                E(420, (r << 16) | (g << 8) | b)
        if w is not None and lineweight(w) != lw:
            E(370, lineweight(w))
        counts[kind] += 1
        per_layer[layer] = per_layer.get(layer, 0) + 1

    def hatch(layer, col, path_pts=None, circle=None):
        head("HATCH", layer, col)
        E(100, "AcDbHatch")
        for c_, v in ((10, 0.0), (20, 0.0), (30, 0.0), (210, 0.0), (220, 0.0), (230, 1.0)):
            E(c_, _f(v))
        E(2, "SOLID")
        E(70, 1)
        E(71, 0)
        E(91, 1)
        if circle is None:
            E(92, 3)          # 바깥 경계 + 폴리선 경로
            E(72, 0)
            E(73, 1)
            E(93, len(path_pts))
            for x, y in path_pts:
                E(10, _f(x))
                E(20, _f(y))
        else:
            cx, cy, r = circle
            E(92, 1)          # 바깥 경계(모서리 경로)
            E(93, 1)
            E(72, 2)          # 원호 모서리
            E(10, _f(cx))
            E(20, _f(cy))
            E(40, _f(r))
            E(50, _f(0.0))
            E(51, _f(360.0))
            E(73, 1)
        E(97, 0)
        E(75, 1)
        E(76, 1)
        E(98, 0)

    for layer, p in items:
        kind = p[0]
        if kind == "line":
            _, x0, y0, x1, y1, w, col, dash = p
            a, b = (X(x0), Y(y0)), (X(x1), Y(y1))
            head("LINE", layer, col, w, tuple(dash) if dash else None)
            E(100, "AcDbLine")
            for c_, v in ((10, a[0]), (20, a[1]), (30, 0.0), (11, b[0]), (21, b[1]), (31, 0.0)):
                E(c_, _f(v))
            pts += [a, b]
        elif kind == "poly":
            _, pp, fill, w, col = p
            q = [(X(x), Y(y)) for x, y in pp]
            if len(q) < 2 or (not fill and not w):
                skipped += 1
                continue
            if fill and fill.lower() != "#ffffff" and len(q) >= 3:
                hatch(layer, fill, path_pts=q)
            if w:
                head("LWPOLYLINE", layer, col, w)
                E(100, "AcDbPolyline")
                E(90, len(q))
                E(70, 1)
                for x, y in q:
                    E(10, _f(x))
                    E(20, _f(y))
            pts += q
        elif kind == "mask":
            q = [(X(x), Y(y)) for x, y in p[1]]
            hatch(layer, "#ffffff", path_pts=q)
            pts += q
        elif kind == "circle":
            _, cx, cy, r, w, col, fill = p
            c = (X(cx), Y(cy))
            if fill and fill.lower() != "#ffffff":
                hatch(layer, fill, circle=(c[0], c[1], r * k))
            if w:
                head("CIRCLE", layer, col, w)
                E(100, "AcDbCircle")
                for c_, v in ((10, c[0]), (20, c[1]), (30, 0.0), (40, r * k)):
                    E(c_, _f(v))
            if not w and not (fill and fill.lower() != "#ffffff"):
                skipped += 1
                continue
            pts += [(c[0] - r * k, c[1] - r * k), (c[0] + r * k, c[1] + r * k)]
        else:
            _, x, y, size, s, anchor, col, ang, bold = p
            t = _txt(s)
            if not t.strip():
                skipped += 1
                continue
            c = (X(x), Y(y))
            head("TEXT", layer, col)
            E(100, "AcDbText")
            for c_, v in ((10, c[0]), (20, c[1]), (30, 0.0), (40, size * k)):
                E(c_, _f(v))
            E(1, t)
            rot = (-float(ang)) % 360.0 if ang else 0.0
            if rot:
                E(50, _f(rot))
            E(7, STYLE_BOLD if bold else STYLE_NAME)
            hal = {"middle": 1, "end": 2}.get(anchor, 0)
            if hal:
                E(72, hal)
                for c_, v in ((11, c[0]), (21, c[1]), (31, 0.0)):
                    E(c_, _f(v))
            E(100, "AcDbText")
            pts.append(c)

    if pts:
        ex0, ey0 = min(q[0] for q in pts), min(q[1] for q in pts)
        ex1, ey1 = max(q[0] for q in pts), max(q[1] for q in pts)
    else:
        ex0 = ey0 = 0.0
        ex1, ey1 = (float(sh.w), float(sh.h)) if not model else (1000.0, 1000.0)
    seed = f"{h.n + 1:X}"

    O = _Out()
    # ---- HEADER
    O(0, "SECTION")
    O(2, "HEADER")
    for name, code, val in (("$ACADVER", 1, ACADVER), ("$DWGCODEPAGE", 3, CODEPAGE)):
        O(9, name)
        O(code, val)
    for name, xy in (("$INSBASE", (0.0, 0.0)), ("$EXTMIN", (ex0, ey0)), ("$EXTMAX", (ex1, ey1))):
        O(9, name)
        O(10, _f(xy[0]))
        O(20, _f(xy[1]))
        O(30, _f(0.0))
    lim = (float(sh.w), float(sh.h)) if not model else (ex1, ey1)
    for name, xy in (("$LIMMIN", (0.0, 0.0) if not model else (ex0, ey0)), ("$LIMMAX", lim)):
        O(9, name)
        O(10, _f(xy[0]))
        O(20, _f(xy[1]))
    for name, code, val in (("$LTSCALE", 40, _f(1.0)), ("$TEXTSTYLE", 7, STYLE_NAME), ("$CLAYER", 8, "0"), ("$CELTYPE", 6, "ByLayer"),
                            ("$DIMSTYLE", 2, "Standard"), ("$CMLSTYLE", 2, "Standard"), ("$LUNITS", 70, 2), ("$LUPREC", 70, 2),
                            ("$INSUNITS", 70, 4), ("$MEASUREMENT", 70, 1), ("$LWDISPLAY", 290, 1), ("$PSLTSCALE", 70, 1),
                            ("$HANDSEED", 5, seed)):
        O(9, name)
        O(code, val)
    O(0, "ENDSEC")
    # ---- CLASSES (비움)
    O(0, "SECTION")
    O(2, "CLASSES")
    O(0, "ENDSEC")
    # ---- TABLES
    O(0, "SECTION")
    O(2, "TABLES")

    def table(name, hkey, n):
        O(0, "TABLE")
        O(2, name)
        O(5, H_[hkey])
        O(330, 0)
        O(100, "AcDbSymbolTable")
        O(70, n)

    def rec(kind, handle, owner, sub, name, code5=5):
        O(0, kind)
        O(code5, handle)
        O(330, owner)
        O(100, "AcDbSymbolTableRecord")
        O(100, sub)
        O(2, name)
        O(70, 0)

    table("VPORT", "t_vport", 1)
    rec("VPORT", H_["vport"], H_["t_vport"], "AcDbViewportTableRecord", "*Active")
    vh = max(ey1 - ey0, (ex1 - ex0) / 1.6, 1.0) * 1.05
    for c_, v in ((10, 0.0), (20, 0.0), (11, 1.0), (21, 1.0), (12, (ex0 + ex1) / 2), (22, (ey0 + ey1) / 2), (13, 0.0), (23, 0.0),
                  (14, 10.0), (24, 10.0), (15, 10.0), (25, 10.0), (16, 0.0), (26, 0.0), (36, 1.0), (17, 0.0), (27, 0.0), (37, 0.0),
                  (40, vh), (41, 1.6), (42, 50.0), (43, 0.0), (44, 0.0), (50, 0.0), (51, 0.0)):
        O(c_, _f(v))
    for c_, v in ((71, 0), (72, 100), (73, 1), (74, 3), (75, 0), (76, 0), (77, 0), (78, 0), (281, 0), (65, 1)):
        O(c_, v)
    for c_, v in ((110, 0.0), (120, 0.0), (130, 0.0), (111, 1.0), (121, 0.0), (131, 0.0), (112, 0.0), (122, 1.0), (132, 0.0)):
        O(c_, _f(v))
    O(79, 0)
    O(146, _f(0.0))
    O(0, "ENDTAB")

    table("LTYPE", "t_ltype", 3 + len(lt_h))
    for hk, name, desc in (("lt_byblock", "ByBlock", ""), ("lt_bylayer", "ByLayer", ""), ("lt_cont", "Continuous", "Solid line")):
        rec("LTYPE", H_[hk], H_["t_ltype"], "AcDbLinetypeTableRecord", name)
        O(3, desc)
        O(72, 65)
        O(73, 0)
        O(40, _f(0.0))
    for d, hd in lt_h.items():
        rec("LTYPE", hd, H_["t_ltype"], "AcDbLinetypeTableRecord", _ltype_name(d))
        O(3, "DoBIM " + " ".join(f"{v:g}" for v in d) + " mm")
        O(72, 65)
        O(73, len(d))
        O(40, _f(sum(d) * k))
        for i, v in enumerate(d):
            O(49, _f(v * k if i % 2 == 0 else -v * k))
            O(74, 0)
    O(0, "ENDTAB")

    table("LAYER", "t_layer", 1 + len(ly_h))
    rec("LAYER", H_["ly_0"], H_["t_layer"], "AcDbLayerTableRecord", "0")
    for c_, v in ((62, 7), (6, "Continuous"), (370, -3), (390, H_["ph_normal"])):
        O(c_, v)
    for name, hl in ly_h.items():
        aci, tc, lw, ldash, desc = layer_props(name)
        rec("LAYER", hl, H_["t_layer"], "AcDbLayerTableRecord", name)
        O(62, aci)
        if tc:
            r, g, b = _rgb(tc)
            O(420, (r << 16) | (g << 8) | b)
        O(6, _ltype_name(ldash) if ldash else "Continuous")
        O(370, lw)
        O(390, H_["ph_normal"])
    O(0, "ENDTAB")

    table("STYLE", "t_style", 3)
    for hk, name, font in (("st_std", "Standard", "txt"), ("st_kr", STYLE_NAME, FONT_FILE), ("st_krb", STYLE_BOLD, FONT_FILE_BOLD)):
        rec("STYLE", H_[hk], H_["t_style"], "AcDbTextStyleTableRecord", name)
        for c_, v in ((40, _f(0.0)), (41, _f(1.0)), (50, _f(0.0)), (71, 0), (42, _f(2.5)), (3, font), (4, "")):
            O(c_, v)
    O(0, "ENDTAB")

    table("VIEW", "t_view", 0)
    O(0, "ENDTAB")
    table("UCS", "t_ucs", 0)
    O(0, "ENDTAB")
    table("APPID", "t_appid", 1)
    rec("APPID", H_["app_acad"], H_["t_appid"], "AcDbRegAppTableRecord", "ACAD")
    O(0, "ENDTAB")
    table("DIMSTYLE", "t_dimstyle", 1)
    O(100, "AcDbDimStyleTable")
    O(71, 1)
    O(340, H_["ds_std"])
    rec("DIMSTYLE", H_["ds_std"], H_["t_dimstyle"], "AcDbDimStyleTableRecord", "Standard", code5=105)
    O(0, "ENDTAB")
    table("BLOCK_RECORD", "t_brec", 2)
    for hk, name, lo in (("br_ms", "*Model_Space", "lo_model"), ("br_ps", "*Paper_Space", "lo_l1")):
        rec("BLOCK_RECORD", H_[hk], H_["t_brec"], "AcDbBlockTableRecord", name)
        O(340, H_[lo])
        O(70, 4)       # 삽입 단위 mm
        O(280, 1)
        O(281, 0)
    O(0, "ENDTAB")
    O(0, "ENDSEC")

    # ---- BLOCKS
    O(0, "SECTION")
    O(2, "BLOCKS")
    for bk, ek, br, name, ps in (("blk_ms", "end_ms", "br_ms", "*Model_Space", False), ("blk_ps", "end_ps", "br_ps", "*Paper_Space", True)):
        O(0, "BLOCK")
        O(5, H_[bk])
        O(330, H_[br])
        O(100, "AcDbEntity")
        if ps:
            O(67, 1)
        O(8, "0")
        O(100, "AcDbBlockBegin")
        O(2, name)
        O(70, 0)
        for c_ in (10, 20, 30):
            O(c_, _f(0.0))
        O(3, name)
        O(1, "")
        O(0, "ENDBLK")
        O(5, H_[ek])
        O(330, H_[br])
        O(100, "AcDbEntity")
        if ps:
            O(67, 1)
        O(8, "0")
        O(100, "AcDbBlockEnd")
    O(0, "ENDSEC")

    # ---- ENTITIES
    O(0, "SECTION")
    O(2, "ENTITIES")
    O.buf.append(E.text())
    O(0, "ENDSEC")

    # ---- OBJECTS
    O(0, "SECTION")
    O(2, "OBJECTS")

    def dict_obj(hk, owner, entries, kind="DICTIONARY", default=None):
        O(0, kind)
        O(5, H_[hk])
        if owner != "0":
            O(102, "{ACAD_REACTORS")
            O(330, owner)
            O(102, "}")
        O(330, owner)
        O(100, "AcDbDictionary")
        O(281, 1)
        for name, target in entries:
            O(3, name)
            O(350, target)
        if default:
            O(100, "AcDbDictionaryWithDefault")
            O(340, default)

    R = H_["d_root"]
    dict_obj("d_root", "0", [("ACAD_GROUP", H_["d_group"]), ("ACAD_LAYOUT", H_["d_layout"]), ("ACAD_MLINESTYLE", H_["d_mline"]),
                             ("ACAD_PLOTSETTINGS", H_["d_plotset"]), ("ACAD_PLOTSTYLENAME", H_["d_psname"])])
    dict_obj("d_group", R, [])
    dict_obj("d_layout", R, [("Layout1", H_["lo_l1"]), ("Model", H_["lo_model"])])
    dict_obj("d_mline", R, [("Standard", H_["ml_std"])])
    dict_obj("d_plotset", R, [])
    dict_obj("d_psname", R, [("Normal", H_["ph_normal"])], kind="ACDBDICTIONARYWDFLT", default=H_["ph_normal"])
    O(0, "ACDBPLACEHOLDER")
    O(5, H_["ph_normal"])
    O(102, "{ACAD_REACTORS")
    O(330, H_["d_psname"])
    O(102, "}")
    O(330, H_["d_psname"])
    O(0, "MLINESTYLE")
    O(5, H_["ml_std"])
    O(102, "{ACAD_REACTORS")
    O(330, H_["d_mline"])
    O(102, "}")
    O(330, H_["d_mline"])
    O(100, "AcDbMlineStyle")
    O(2, "Standard")
    O(70, 0)
    O(3, "")
    O(62, 256)
    O(51, _f(90.0))
    O(52, _f(90.0))
    O(71, 2)
    for off in (0.5, -0.5):
        O(49, _f(off))
        O(62, 256)
        O(6, "BYLAYER")
    pw, ph = (float(sh.w), float(sh.h)) if not model else (420.0, 297.0)
    for hk, name, flag, tab, br in (("lo_model", "Model", 1, 0, "br_ms"), ("lo_l1", "Layout1", 1, 1, "br_ps")):
        O(0, "LAYOUT")
        O(5, H_[hk])
        O(102, "{ACAD_REACTORS")
        O(330, H_["d_layout"])
        O(102, "}")
        O(330, H_["d_layout"])
        O(100, "AcDbPlotSettings")
        for c_, v in ((1, ""), (2, "none_device"), (4, ""), (6, "")):
            O(c_, v)
        for c_ in (40, 41, 42, 43, 44, 45, 46, 47, 48, 49):
            O(c_, _f(0.0))
        for c_, v in ((140, 0.0), (141, 0.0), (142, 1.0), (143, 1.0)):
            O(c_, _f(v))
        for c_, v in ((70, 688), (72, 1), (73, 0), (74, 5), (7, ""), (75, 16)):
            O(c_, v)
        O(147, _f(1.0))
        for c_, v in ((76, 0), (77, 2), (78, 300)):
            O(c_, v)
        O(148, _f(0.0))
        O(149, _f(0.0))
        O(100, "AcDbLayout")
        O(1, name)
        O(70, flag)
        O(71, tab)
        for c_, v in ((10, 0.0), (20, 0.0), (11, pw), (21, ph), (12, 0.0), (22, 0.0), (32, 0.0),
                      (14, ex0), (24, ey0), (34, 0.0), (15, ex1), (25, ey1), (35, 0.0), (146, 0.0),
                      (13, 0.0), (23, 0.0), (33, 0.0), (16, 1.0), (26, 0.0), (36, 0.0), (17, 0.0), (27, 1.0), (37, 0.0)):
            O(c_, _f(v))
        O(76, 0)
        O(330, H_[br])
    O(0, "ENDSEC")
    O(0, "EOF")
    blob = O.text().encode("utf-8")
    info = {"version": ACADVER, "encoding": "utf-8", "units": "mm",
            "space": "model mm 1:1 (view scale applied to text/dash)" if model else "paper mm 1:1 (views at their scale)",
            "entities": {k_: v for k_, v in counts.items() if v}, "layers": dict(sorted(per_layer.items())),
            "extents": [round(ex0, 3), round(ey0, 3), round(ex1, 3), round(ey1, 3)]}
    if skipped:
        info["skipped"] = skipped
    return blob, info


def view_model_xform(data) -> dict:
    """뷰 한 장의 용지 mm -> 모델 mm 역변환 값(drawing_export._frame 와 같은 원점). render_dxf(model=...) 에 넘긴다."""
    from dobim_core import drawing_export as DX
    _w, _h, tx, ty, _d, _x = DX._frame(data)
    s = float(data["scale"])
    return {"scale": s, "margin": DX.MARGIN, "x0": -(tx(0.0) - DX.MARGIN) * s, "y1": (ty(0.0) - DX.MARGIN) * s}
