# SPDX-License-Identifier: MPL-2.0
"""시트(도면 용지) 배치와 SVG·PDF 내보내기 (1단계 도면 엔진).

뷰 여러 장을 A1·A3 용지 한 장에 놓고 윤곽선·표제란(프로젝트명·도면명·도면번호·축척·작성일·개정란·축척 표시 막대·방위표)을 그린다.
- 시트는 그림 요소(primitive) 목록으로 먼저 만든 뒤 SVG 와 PDF 로 각각 쓴다. 단위는 용지 mm, y 는 아래로 자란다.
  요소: ("line", x0, y0, x1, y1, 굵기, 색, 파선|None) / ("poly", 점들, 채움|None, 굵기, 색) / ("circle", cx, cy, r, 굵기, 색, 채움|None) /
        ("text", x, y, 크기mm, 글, 정렬, 색, 각도, 굵게)  (y 는 글 밑선)
- 뷰는 자기 축척 그대로 놓는다(시트가 축척을 바꾸지 않는다). 들어가지 않으면 SheetError 로 알린다.
- PDF 는 pdf_font 가 찾은 글꼴(OFL)을 부분집합으로 내장해 한글이 깨지지 않는다. 글꼴이 없으면 Helvetica 로 내려가고 한글은 '?' 가 된다(non_ascii_dropped).
순수 모듈이다(파일·ifcopenshell·시계를 모른다: 날짜는 호출자가 문자열로 준다).
"""
from __future__ import annotations

from math import atan2, cos, degrees, radians, sin
import zlib

from dobim_core import drawing_export as DX
from dobim_core.pdf_font import PdfFont

PAPERS = {"A1": (841.0, 594.0), "A3": (420.0, 297.0)}     # 가로 방향 mm
OUTER = 10.0            # 용지 가장자리 -> 윤곽선
PAD = 8.0               # 윤곽선 -> 도면 영역
GAP = 12.0              # 뷰 사이 간격
CAP_H = 12.0            # 뷰 아래 도면명 줄 높이
TB_W = 85.0             # 표제란 너비(오른쪽 띠)
INK = "#000000"
GRAY = "#555555"
FONT = DX.FONT


class SheetError(ValueError):
    """시트에 올릴 수 없다(뷰가 용지에 안 들어감 등). 메시지에 고치는 방법이 들어 있다."""


class Sheet:
    def __init__(self, paper: str):
        self.paper = paper
        self.w, self.h = PAPERS[paper]
        self.prims: list[tuple] = []
        self.placed: list[dict] = []          # 배치 결과(뷰 id, 위치·크기 mm, 축척) - 시험·응답용

    def line(self, x0, y0, x1, y1, w=0.25, color=INK, dash=None):
        self.prims.append(("line", x0, y0, x1, y1, w, color, dash))

    def poly(self, pts, fill=None, w=0.0, color=INK):
        self.prims.append(("poly", list(pts), fill, w, color))

    def circle(self, cx, cy, r, w=0.25, color=INK, fill=None):
        self.prims.append(("circle", cx, cy, r, w, color, fill))

    def text(self, x, y, size, s, anchor="start", color=INK, angle=0.0, bold=False):
        self.prims.append(("text", x, y, size, str(s), anchor, color, angle, bold))

    def begin(self, gid: str, **attrs):
        """SVG 그룹 시작(PDF 에는 영향 없음). 시험·판정이 '표제란'·'방위표' 같은 부분을 찾는 표지다."""
        self.prims.append(("g", gid, attrs))

    def end(self):
        self.prims.append(("/g",))

    def rect(self, x, y, w, h, lw=0.35, color=INK, fill=None):
        self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], fill, lw, color)


# ---------------------------------------------------------------- 글 크기 추정(배치용: 정확한 폭은 글꼴이 정한다)
def est_w(s: str, size: float) -> float:
    return sum(size * (1.0 if ord(c) >= 0x2E80 else 0.58) for c in s)


def wrap(s: str, size: float, maxw: float, max_lines: int) -> list[str]:
    """글자 단위 줄바꿈(한글은 띄어쓰기 없이도 끊긴다). 줄 수를 넘으면 마지막 줄을 …로 닫는다."""
    lines, cur = [], ""
    for c in s:
        if est_w(cur + c, size) > maxw and cur:
            lines.append(cur)
            cur = c
        else:
            cur += c
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


# ---------------------------------------------------------------- 뷰 -> 그림 요소
def view_prims(sh: Sheet, data, tx, ty, datums, dx0, dx1):
    """뷰 한 장의 선·채움·기준선·주석을 시트 좌표로 옮겨 그린다(drawing_export.render_svg 와 같은 그림, 같은 굵기 체계)."""
    for poly in data["fills"]:
        sh.poly([(tx(x), ty(y)) for x, y in poly], fill="#cfcfcf")
    for name, z in datums:
        sh.line(tx(dx0), ty(z), tx(dx1), ty(z), DX.DATUM_W, "#555555", (3.0, 1.5))
        sh.text(tx(dx0) + 1.0, ty(z) - 0.8, 2.6, DX.datum_label(name, z), color="#555555")
    for cls in ("proj", "symbol", "cut"):
        wd, col = DX.STYLE[cls]
        for _, x0, y0, x1, y1 in (ln for ln in data["lines"] if ln[0] == cls):
            sh.line(tx(x0), ty(y0), tx(x1), ty(y1), wd, col)
    for it in data.get("annots", ()):
        t, aid = it[0], it[1]
        if t == "dim":
            _, _, e0, e1, ln, text, tpos, u = it
            for e in (e0, e1):
                sh.line(tx(e[0]), ty(e[1]), tx(e[2]), ty(e[3]), 0.13, DX.ANNOT_COLOR)
            sh.line(tx(ln[0]), ty(ln[1]), tx(ln[2]), ty(ln[3]), 0.13, DX.ANNOT_COLOR)
            upx, upy = u[0], -u[1]
            for px, py in ((tx(ln[0]), ty(ln[1])), (tx(ln[2]), ty(ln[3]))):
                kx, ky = (upx - upy) * 0.64, (upy + upx) * 0.64
                sh.line(px - kx, py - ky, px + kx, py + ky, 0.25, DX.ANNOT_COLOR)
            ang = degrees(atan2(upy, upx))
            ang = ang - 180.0 if ang > 90.0 else (ang + 180.0 if ang <= -90.0 else ang)
            a = radians(ang)
            sh.text(tx(tpos[0]) + 0.8 * sin(a), ty(tpos[1]) - 0.8 * cos(a), 2.5, text, "middle", DX.ANNOT_COLOR, ang)
        elif t == "tag":
            _, _, anc, lab, text = it
            sh.line(tx(anc[0]), ty(anc[1]), tx(lab[0]), ty(lab[1]), 0.13, "#8a3b00")
            sh.circle(tx(anc[0]), ty(anc[1]), 0.5, 0.0, "#8a3b00", "#8a3b00")
            sh.text(tx(lab[0]) + 0.6, ty(lab[1]), 2.5, text, color="#8a3b00")
        else:
            _, _, kind, reason, x, y = it
            what = "치수" if kind == "dim" else "태그"
            label = f"{'고아' if t == 'orphan' else '무효'} {what} {aid[-6:]}: {DX.REASON_KO.get(reason, reason)}"
            px, py = tx(x), ty(y)
            sh.line(px - 1, py - 1, px + 1, py + 1, 0.13, DX.ORPHAN_COLOR)
            sh.line(px - 1, py + 1, px + 1, py - 1, 0.13, DX.ORPHAN_COLOR)
            sh.circle(px, py, 1.6, 0.13, DX.ORPHAN_COLOR)
            sh.text(px + 2.2, py + 0.8, 2.5, label, color=DX.ORPHAN_COLOR)


def _natural(data):
    """뷰를 자기 축척으로 그릴 때의 용지 크기(내용만, mm)와 변환 함수."""
    w, h, tx, ty, datums, (x0, x1) = DX._frame(data)
    return w - 2 * DX.MARGIN, h - 2 * DX.MARGIN - DX.TITLE_H, tx, ty, datums, x0, x1


# ---------------------------------------------------------------- 표제란
def _nice_bar(scale: int, max_mm: float = 70.0) -> tuple[float, float]:
    """축척 막대: (모델 길이 m, 용지 길이 mm). max_mm 안에 드는 가장 긴 '깔끔한' 길이."""
    best = (0.01, 0.01 * 1000.0 / scale)
    for m in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100):
        mm = m * 1000.0 / scale
        if mm <= max_mm:
            best = (m, mm)
    return best


def _fmt_m(v: float) -> str:
    return f"{v:g}"


def north_symbol(sh: Sheet, cx, cy, r, north_deg: float):
    """방위표: 원 + 북쪽 화살표 + N. north_deg = 정북이 도면 위쪽에서 시계방향으로 얼마나 돌아 있는가(0 이면 도면 위가 정북)."""
    sh.begin("north-symbol", **{"data-north-deg": f"{north_deg:g}"})
    sh.circle(cx, cy, r, 0.35)
    a = radians(north_deg)

    def rot(px, py):                                         # 중심 기준 (px, py)를 시계방향 a 만큼 돌린다(y 아래로 자람)
        return cx + px * cos(a) - py * sin(a), cy + px * sin(a) + py * cos(a)
    sh.poly([rot(0, -r * 0.92), rot(-r * 0.38, r * 0.62), rot(0, r * 0.28), rot(r * 0.38, r * 0.62)], fill=INK, w=0.2)
    nx, ny = rot(0, -r - 3.6)
    sh.text(nx, ny + 1.4, 4.0, "N", "middle", INK, 0.0, True)
    sh.end()


def title_block(sh: Sheet, spec: dict, scales: list[int]):
    x0 = sh.w - OUTER - TB_W
    y0, y1, x1 = OUTER, sh.h - OUTER, sh.w - OUTER
    sh.line(x0, y0, x0, y1, 0.5)                              # 표제란 왼쪽 경계
    uniform = len(set(scales)) == 1
    primary = scales[0]
    y = y0

    def cell(h, label, value, size, max_lines=2, bold=False):
        nonlocal y
        sh.line(x0, y + h, x1, y + h, 0.25)
        sh.text(x0 + 2.0, y + 3.6, 2.5, label, color=GRAY)
        lines = wrap(value, size, TB_W - 5.0, max_lines)
        for i, s in enumerate(lines):
            sh.text(x0 + 3.0, y + 3.6 + 2.0 + size * 1.05 * (i + 1), size, s, bold=bold)
        y += h
    cell(26.0, "프로젝트명", spec["project"], 4.5, 3)
    cell(30.0, "도면명", spec["title"], 5.0, 3, True)
    cell(18.0, "도면번호", spec["number"], 7.0, 1, True)
    cell(14.0, "축척", f"1:{primary}" if uniform else "도면참조", 5.0, 1)
    cell(14.0, "작성일", spec["date"], 5.0, 1)
    # 아래에서부터: 방위표(42), 축척 표시(26)
    north_h, bar_h = 42.0, 26.0
    ybar, ynorth = y1 - north_h - bar_h, y1 - north_h
    # 개정란: 남는 높이에 빈 표(개정·일자·내용)를 둔다
    free = ybar - y
    if free >= 18.0:
        sh.begin("revision-table")
        sh.text(x0 + 2.0, y + 3.6, 2.5, "개정", color=GRAY)
        ty0 = y + 5.0
        n = int((ybar - ty0 - 1.0) // 6.0)
        n = max(1, min(n, 8))
        c1, c2 = x0 + 12.0, x0 + 34.0
        for i in range(n + 1):
            sh.line(x0, ty0 + 6.0 * i, x1, ty0 + 6.0 * i, 0.18)
        sh.line(c1, ty0, c1, ty0 + 6.0 * n, 0.18)
        sh.line(c2, ty0, c2, ty0 + 6.0 * n, 0.18)
        for s, xx in (("차수", x0 + 2.0), ("일자", c1 + 2.0), ("내용", c2 + 2.0)):
            sh.text(xx, ty0 + 4.2, 2.5, s, color=GRAY)
        sh.end()
    sh.line(x0, ybar, x1, ybar, 0.25)
    sh.text(x0 + 2.0, ybar + 3.6, 2.5, "축척 표시" + ("" if uniform else f" (S=1:{primary} 기준)"), color=GRAY)
    m, mm = _nice_bar(primary)
    bx, by = x0 + (TB_W - mm) / 2.0, ybar + 13.0
    sh.begin("scale-bar", **{"data-scale": primary, "data-length-m": f"{m:g}"})
    sh.rect(bx, by, mm / 2.0, 3.0, 0.25, INK, INK)
    sh.rect(bx + mm / 2.0, by, mm / 2.0, 3.0, 0.25, INK, "#ffffff")
    for frac, lab in ((0.0, "0"), (0.5, _fmt_m(m / 2.0)), (1.0, _fmt_m(m) + " m")):
        sh.text(bx + mm * frac, by + 8.0, 2.8, lab, "middle")
    sh.end()
    sh.line(x0, ynorth, x1, ynorth, 0.25)
    sh.text(x0 + 2.0, ynorth + 3.6, 2.5, "방위", color=GRAY)
    north_symbol(sh, x0 + TB_W / 2.0, ynorth + 23.0, 10.0, spec["north_deg"])


# ---------------------------------------------------------------- 배치
def build_sheet(spec: dict, views: list[dict]) -> Sheet:
    """spec: paper, project, title, number, date, north_deg. views: 커맨드가 만든 뷰 데이터(+ 'id')."""
    sh = Sheet(spec["paper"])
    sh.begin("sheet-border")
    sh.rect(OUTER, OUTER, sh.w - 2 * OUTER, sh.h - 2 * OUTER, 0.7)      # 윤곽선
    sh.end()
    area_x0, area_y0 = OUTER + PAD, OUTER + PAD
    area_x1, area_y1 = sh.w - OUTER - TB_W - PAD, sh.h - OUTER - PAD
    nat = [_natural(v) for v in views]
    pos, x, y, row_h = [], area_x0, area_y0, 0.0
    for cw, ch, *_ in nat:
        bh = ch + CAP_H
        if cw > area_x1 - area_x0 or bh > area_y1 - area_y0:
            raise SheetError(f"뷰가 {sh.paper} 용지에 들어가지 않는다({cw:.0f}x{ch:.0f}mm). 더 큰 용지를 쓰거나 뷰의 축척을 키우세요(예: 1:100 -> 1:200)")
        if x > area_x0 and x + cw > area_x1:
            x, y, row_h = area_x0, y + row_h + GAP, 0.0
        pos.append((x, y))
        x += cw + GAP
        row_h = max(row_h, bh)
    if not pos or pos[-1][1] + row_h > area_y1 + 1e-6:
        raise SheetError(f"뷰 {len(views)}장이 {sh.paper} 용지 한 장에 다 들어가지 않는다. 용지를 키우거나 뷰를 나눠 시트를 여러 장 만드세요")
    # 묶음 전체를 도면 영역 가운데로
    gx0 = min(p[0] for p in pos)
    gx1 = max(p[0] + n[0] for p, n in zip(pos, nat))
    gy1 = pos[-1][1] + row_h
    dx = (area_x0 + area_x1) / 2.0 - (gx0 + gx1) / 2.0
    dy = ((area_y0 + area_y1) / 2.0 - (area_y0 + gy1) / 2.0)
    for v, (cw, ch, tx, ty, datums, ex0, ex1), (px, py) in zip(views, nat, pos):
        ox, oy = px + dx, py + dy
        f = DX.MARGIN
        sh.begin(f"view-{v.get('id') or len(sh.placed)}", **{"data-view": v.get("id") or "", "data-scale": v["scale"]})
        view_prims(sh, v, lambda X, _tx=tx, _ox=ox: _ox + _tx(X) - f, lambda Y, _ty=ty, _oy=oy: _oy + _ty(Y) - f, datums, ex0, ex1)
        cap = f"{v['name']}"
        sh.text(ox, oy + ch + 7.0, 4.0, cap, bold=True)
        sh.text(ox + est_w(cap, 4.0) + 4.0, oy + ch + 7.0, 3.5, f"S=1:{v['scale']}")
        sh.end()
        sh.placed.append({"view": v.get("id"), "name": v["name"], "scale": v["scale"], "x": round(ox, 2), "y": round(oy, 2),
                          "w": round(cw, 2), "h": round(ch, 2)})
    sh.begin("title-block")
    title_block(sh, spec, [v["scale"] for v in views])
    sh.end()
    return sh


# ---------------------------------------------------------------- SVG
def _rgb(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def render_sheet_svg(sh: Sheet, spec: dict) -> bytes:
    out = [f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="{sh.w:g}mm" height="{sh.h:g}mm" '
           f'viewBox="0 0 {sh.w:g} {sh.h:g}" font-family="{FONT}">',
           f'<title>{DX._esc(spec["number"])} {DX._esc(spec["title"])}</title>',
           f'<rect x="0" y="0" width="{sh.w:g}" height="{sh.h:g}" fill="#ffffff"/>']
    for p in sh.prims:
        k = p[0]
        if k == "g":
            at = "".join(f' {a}="{DX._esc(v)}"' for a, v in p[2].items())
            out.append(f'<g id="{DX._esc(p[1])}"{at}>')
        elif k == "/g":
            out.append("</g>")
        elif k == "line":
            _, x0, y0, x1, y1, w, c, dash = p
            d = f' stroke-dasharray="{dash[0]:g} {dash[1]:g}"' if dash else ""
            out.append(f'<line x1="{x0:.3f}" y1="{y0:.3f}" x2="{x1:.3f}" y2="{y1:.3f}" stroke="{c}" stroke-width="{w:g}" stroke-linecap="round"{d}/>')
        elif k == "poly":
            _, pts, fill, w, c = p
            st = f' stroke="{c}" stroke-width="{w:g}"' if w else ' stroke="none"'
            out.append('<polygon points="' + " ".join(f"{x:.3f},{y:.3f}" for x, y in pts) + f'" fill="{fill or "none"}"{st}/>')
        elif k == "circle":
            _, cx, cy, r, w, c, fill = p
            st = f' stroke="{c}" stroke-width="{w:g}"' if w else ' stroke="none"'
            out.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:g}" fill="{fill or "none"}"{st}/>')
        else:
            _, x, y, size, s, anchor, c, ang, bold = p
            tr = f'translate({x:.3f},{y:.3f})' + (f' rotate({ang:.2f})' if ang else "")
            wt = ' font-weight="bold"' if bold else ""
            out.append(f'<text transform="{tr}" font-size="{size:g}" fill="{c}" text-anchor="{anchor}"{wt}>{DX._esc(s)}</text>')
    out.append("</svg>\n")
    return "\n".join(out).encode("utf-8")


# ---------------------------------------------------------------- PDF
def _pdf_ascii(s: str) -> tuple[str, bool]:
    return DX._pdf_str(s)


def render_sheet_pdf(sh: Sheet, spec: dict, font: PdfFont | None = None) -> tuple[bytes, bool]:
    """벡터 PDF 한 쪽. font 가 있으면 한글을 포함한 모든 글을 그 글꼴로 내장해 쓴다. 반환: (바이트, 글자를 '?' 로 바꿨는지)"""
    k = 72.0 / 25.4
    H = sh.h
    dropped = False
    c: list[str] = ["q 1 J 1 j"]
    state = {"w": None, "S": None, "d": None, "f": None}

    def stroke_state(w, col, dash):
        if state["w"] != w:
            c.append(f"{w * k:.3f} w")
            state["w"] = w
        if state["S"] != col:
            c.append("{:.3f} {:.3f} {:.3f} RG".format(*_rgb(col)))
            state["S"] = col
        if state["d"] != dash:
            c.append(f"[{dash[0] * k:.2f} {dash[1] * k:.2f}] 0 d" if dash else "[] 0 d")
            state["d"] = dash

    def fill_state(col):
        if state["f"] != col:
            c.append("{:.3f} {:.3f} {:.3f} rg".format(*_rgb(col)))
            state["f"] = col

    X = lambda v: f"{v * k:.3f}"                           # noqa: E731
    Y = lambda v: f"{(H - v) * k:.3f}"                     # noqa: E731
    for p in sh.prims:
        kind = p[0]
        if kind in ("g", "/g"):
            continue
        if kind == "line":
            _, x0, y0, x1, y1, w, col, dash = p
            stroke_state(w, col, dash)
            c.append(f"{X(x0)} {Y(y0)} m {X(x1)} {Y(y1)} l S")
        elif kind == "poly":
            _, pts, fill, w, col = p
            path = f"{X(pts[0][0])} {Y(pts[0][1])} m " + " ".join(f"{X(x)} {Y(y)} l" for x, y in pts[1:]) + " h"
            if fill:
                fill_state(fill)
            if w:
                stroke_state(w, col, None)
            c.append(path + (" B" if fill and w else " f" if fill else " S" if w else " n"))
        elif kind == "circle":
            _, cx, cy, r, w, col, fill = p
            m = 0.5523 * r
            pts = [(cx + r, cy), (cx + r, cy + m, cx + m, cy + r, cx, cy + r), (cx - m, cy + r, cx - r, cy + m, cx - r, cy),
                   (cx - r, cy - m, cx - m, cy - r, cx, cy - r), (cx + m, cy - r, cx + r, cy - m, cx + r, cy)]
            seg = [f"{X(cx + r)} {Y(cy)} m"]
            for q in pts[1:]:
                seg.append(f"{X(q[0])} {Y(q[1])} {X(q[2])} {Y(q[3])} {X(q[4])} {Y(q[5])} c")
            if fill:
                fill_state(fill)
            if w:
                stroke_state(w, col, None)
            c.append(" ".join(seg) + " h" + (" B" if fill and w else " f" if fill else " S" if w else " n"))
        else:
            _, x, y, size, s, anchor, col, ang, bold = p
            fill_state(col)
            if font is not None:
                width = _measure(font, s, size)
                shown = font.show(s)
            else:
                t, d = _pdf_ascii(s)
                dropped |= d
                width = est_w(t, size) * 0.95
                shown = f"({t})"
            ox = -width / 2.0 if anchor == "middle" else -width if anchor == "end" else 0.0
            a = radians(ang)
            tx_, ty_ = x + ox * cos(a), y + ox * sin(a)
            c.append(f"BT /F1 {size * k:.2f} Tf {cos(a):.5f} {-sin(a):.5f} {sin(a):.5f} {cos(a):.5f} {X(tx_)} {Y(ty_)} Tm {shown} Tj ET")      # 굵게(bold)는 SVG 에서만 적용한다(굵은 글꼴을 따로 내장하지 않는다)
    c.append("Q")
    body = "\n".join(c)
    content = font.finalize_stream(body) if font is not None else body.encode("ascii")
    comp = zlib.compress(content, 9)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", None,
            f"<< /Length {len(comp)} /Filter /FlateDecode >>\nstream\n".encode() + comp + b"\nendstream"]
    if font is not None:
        fobjs, t0 = font.objects(6)                         # 6: 글꼴 사전(Type0)부터 5개. 5 는 Info
        res = f"/Font << /F1 {t0} 0 R >>"
    else:
        fobjs, t0 = [b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"], 6
        res = f"/Font << /F1 {t0} 0 R >>"
    objs[2] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {sh.w * k:.2f} {sh.h * k:.2f}] /Resources << {res} >> /Contents 4 0 R >>").encode()
    title = f"{spec['number']} {spec['title']}"
    info = "<< /Title <FEFF" + title.encode("utf-16-be").hex().upper() + "> /Creator (Democratization of BIM) >>"
    objs.append(info.encode("ascii"))                       # 5
    objs += fobjs                                           # 6...
    buf = bytearray(b"%PDF-1.5\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        buf += f"{o:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 5 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(buf), dropped


def _measure(font: PdfFont, s: str, size: float) -> float:
    """글 폭 mm. 글꼴이 있으면 그 글꼴의 진행폭(기본 굵기 기준)을, 못 읽으면 추정값."""
    try:
        f = font._measure_font
    except AttributeError:
        try:
            import io
            from fontTools.ttLib import TTFont
            f = font._measure_font = TTFont(io.BytesIO(font.data), lazy=True)
            f._cmap = f.getBestCmap()
            f._upem = f["head"].unitsPerEm
            f._order = f.getGlyphOrder()
        except Exception:
            f = font._measure_font = None
    if f is None:
        return est_w(s, size)
    hm = f["hmtx"]
    tot = 0.0
    for ch in s:
        g = f._cmap.get(ord(ch))
        tot += (hm[g][0] / f._upem) if g else 0.5
    return tot * size
