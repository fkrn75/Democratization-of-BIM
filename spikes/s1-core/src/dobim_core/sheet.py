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
from dobim_core import sheet_tables as ST
from dobim_core.pdf_font import PdfFont

# 용지(W1 1-E, IO-17: A0~A4). 이름 그대로면 가로, 끝에 P 를 붙이면 세로(예: A4P). 값은 (폭, 높이) mm
_ISO_A = {"A0": (1189.0, 841.0), "A1": (841.0, 594.0), "A2": (594.0, 420.0), "A3": (420.0, 297.0), "A4": (297.0, 210.0)}
PAPERS = {**_ISO_A, **{k + "P": (h, w) for k, (w, h) in _ISO_A.items()}}
STANDARD_PAPERS = ("A1", "A3")   # 작성기준 프로파일(drawing.profile)이 적는 납품 기본 용지(가로). 나머지는 인쇄 선택지
OUTER = 10.0            # 용지 가장자리 -> 윤곽선
PAD = 8.0               # 윤곽선 -> 도면 영역
GAP = 12.0              # 뷰 사이 간격
CAP_H = 12.0            # 뷰 아래 도면명 줄 높이
TB_W = 85.0             # 표제란 너비(오른쪽 띠)
KP_H = 36.0             # 키 플랜 칸 높이(키 플랜을 줄 때만)
INK = "#000000"
GRAY = "#555555"
FONT = DX.FONT
HIDDEN_DASH = (2.4, 1.2)   # 숨은 선 파선의 (마디, 간격) 용지 mm
NONBEARING_CUT_W = 0.35   # 선 굵기 위계(옵션)에서 비내력벽(LoadBearing 거짓) 절단선 굵기: 내력벽 절단선(0.50)보다 가늘고 창호·기호선(0.25)보다 굵다
HIER_DIM_W = (0.25, 0.18)  # 선 굵기 위계(옵션)에서 치수선·보조선 굵기: 치수선은 창호·기호선과 같은 층, 보조선은 보이는 선 층


class SheetError(ValueError):
    """시트에 올릴 수 없다(뷰가 용지에 안 들어감 등). 메시지에 고치는 방법이 들어 있다."""


class Sheet:
    def __init__(self, paper: str):
        self.paper = paper
        self.w, self.h = PAPERS[paper]
        self.prims: list[tuple] = []
        self.placed: list[dict] = []          # 배치 결과(뷰 id, 위치·크기 mm, 축척) - 시험·응답용
        self.dim_w = (0.13, 0.13)             # 치수선·보조선 굵기(build_sheet 가 프로파일의 선 굵기 위계로 바꾼다)

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


def fmt_dim(text: str, prof) -> str:
    """치수 문자 형식(작성기준 프로파일): 소수 자릿수·천단위 쉼표(DM-10). 프로파일이 없거나 숫자가 아니면 그대로."""
    if not prof or (prof.get("dim_decimals") is None and not prof.get("dim_thousands")):
        return text
    try:
        v = float(text)
    except ValueError:
        return text
    d = prof.get("dim_decimals")
    if d is None:
        d = len(text.split(".")[1]) if "." in text else 0
    return f"{v:,.{d}f}" if prof.get("dim_thousands") else f"{v:.{d}f}"


# ---------------------------------------------------------------- 뷰 -> 그림 요소
def view_prims(sh: Sheet, data, tx, ty, datums, dx0, dx1, style=None, prof=None):
    """뷰 한 장의 선·채움·기준선·주석을 시트 좌표로 옮겨 그린다(drawing_export.render_svg 와 같은 그림, 같은 굵기 체계)."""
    th = (prof or {}).get("text_height") or {}
    h_dim, h_tag = th.get("dimension", 2.5), th.get("tag", 2.5)
    h_lab = {2.5: th.get("room_name"), 2.0: th.get("room_area")}     # 실명·면적 문자 높이(뷰 데이터의 기본 높이 -> 프로파일 값)
    for poly in data["fills"]:
        sh.poly([(tx(x), ty(y)) for x, y in poly], fill="#cfcfcf")
    for name, z in datums:
        sh.line(tx(dx0), ty(z), tx(dx1), ty(z), DX.DATUM_W, "#555555", (3.0, 1.5))
        sh.text(tx(dx0) + 1.0, ty(z) - 0.8, th.get("datum", 2.6), DX.datum_label(name, z), color="#555555")
    st = style or DX.STYLE
    for cls in DX.LINE_ORDER:
        wd, col = st[cls]
        for _, x0, y0, x1, y1 in (ln for ln in data["lines"] if ln[0] == cls):
            sh.line(tx(x0), ty(y0), tx(x1), ty(y1), wd, col)
    cutn = [ln for ln in data["lines"] if ln[0] == "cutn"]
    if cutn:                                                  # 비내력벽 절단선(선 굵기 위계 옵션): 절단선보다 한 단계 가늘다
        sh.begin("cut-nonbearing", **{"data-count": len(cutn)})
        for _, x0, y0, x1, y1 in cutn:
            sh.line(tx(x0), ty(y0), tx(x1), ty(y1), NONBEARING_CUT_W, st["cut"][1])
        sh.end()
    hid = [ln for ln in data["lines"] if ln[0] == "hid"]
    if hid:                                                   # 숨은 선(옵션): 보이는 선(proj)과 같은 굵기의 파선
        sh.begin("hidden-lines", **{"data-count": len(hid)})
        for _, x0, y0, x1, y1 in hid:
            sh.line(tx(x0), ty(y0), tx(x1), ty(y1), st["proj"][0], st["proj"][1], HIDDEN_DASH)
        sh.end()
    w_dim, w_ext = sh.dim_w
    for x, y, text, size, anchor in data.get("labels", ()):
        if th:
            size = (th.get("fire_mark") if text.startswith(("FW ", "FD ")) else h_lab.get(size)) or size
        sh.text(tx(x), ty(y), size, text, anchor, "#000000")
    for it in data.get("annots", ()):
        t, aid = it[0], it[1]
        if t == "dim":
            _, _, e0, e1, ln, text, tpos, u = it
            for e in (e0, e1):
                sh.line(tx(e[0]), ty(e[1]), tx(e[2]), ty(e[3]), w_ext, DX.ANNOT_COLOR)
            sh.line(tx(ln[0]), ty(ln[1]), tx(ln[2]), ty(ln[3]), w_dim, DX.ANNOT_COLOR)
            upx, upy = u[0], -u[1]
            for px, py in ((tx(ln[0]), ty(ln[1])), (tx(ln[2]), ty(ln[3]))):
                kx, ky = (upx - upy) * 0.64, (upy + upx) * 0.64
                sh.line(px - kx, py - ky, px + kx, py + ky, 0.25, DX.ANNOT_COLOR)
            ang = degrees(atan2(upy, upx))
            ang = ang - 180.0 if ang > 90.0 else (ang + 180.0 if ang <= -90.0 else ang)
            a = radians(ang)
            sh.text(tx(tpos[0]) + 0.8 * sin(a), ty(tpos[1]) - 0.8 * cos(a), h_dim, fmt_dim(text, prof), "middle", DX.ANNOT_COLOR, ang)
        elif t == "adim":   # 각도·반지름·지름 치수(W1 1-B 2차): 글은 R·Ø·° 가 붙어 작성기준 숫자 형식을 타지 않는다
            _, _, segs, ticks, text, tpos, u = it
            for e in segs:
                sh.line(tx(e[0]), ty(e[1]), tx(e[2]), ty(e[3]), w_dim, DX.ANNOT_COLOR)
            for x, y, ux, uy in ticks:
                kx, ky = (ux + uy) * 0.64, (-uy + ux) * 0.64
                sh.line(tx(x) - kx, ty(y) - ky, tx(x) + kx, ty(y) + ky, 0.25, DX.ANNOT_COLOR)
            ang = DX.adim_text_angle(u)
            a = radians(ang)
            sh.text(tx(tpos[0]) + 0.8 * sin(a), ty(tpos[1]) - 0.8 * cos(a), h_dim, text, "middle", DX.ANNOT_COLOR, ang)
        elif t == "tag" and len(it) > 5:   # 보 축 방향 태그(W2 스물한 번째 PR): 지시선 없이 보 방향으로 눕힌 글
            _, _, _anc, lab, text, u = it
            ang = DX.adim_text_angle(u)
            a = radians(ang)
            sh.text(tx(lab[0]) + 0.8 * sin(a), ty(lab[1]) - 0.8 * cos(a), h_tag, text, "middle", "#8a3b00", ang)
        elif t == "tag":
            _, _, anc, lab, text = it
            sh.line(tx(anc[0]), ty(anc[1]), tx(lab[0]), ty(lab[1]), 0.13, "#8a3b00")
            sh.circle(tx(anc[0]), ty(anc[1]), 0.5, 0.0, "#8a3b00", "#8a3b00")
            sh.text(tx(lab[0]) + 0.6, ty(lab[1]), h_tag, text, color="#8a3b00")
        else:
            _, _, kind, reason, x, y = it
            what = "치수" if kind == "dim" else "태그"
            label = f"{'고아' if t == 'orphan' else '무효'} {what} {aid[-6:]}: {DX.REASON_KO.get(reason, reason)}"
            px, py = tx(x), ty(y)
            sh.line(px - 1, py - 1, px + 1, py + 1, 0.13, DX.ORPHAN_COLOR)
            sh.line(px - 1, py + 1, px + 1, py - 1, 0.13, DX.ORPHAN_COLOR)
            sh.circle(px, py, 1.6, 0.13, DX.ORPHAN_COLOR)
            sh.text(px + 2.2, py + 0.8, 2.5, label, color=DX.ORPHAN_COLOR)
    if data.get("drafts"):   # 2D 작도(27단계): 없으면 그림 요소는 이전과 같다
        draft_prims(sh, data["drafts"], tx, ty, float(data["scale"]))


def draft_prims(sh: Sheet, items, tx, ty, scale):
    """2D 작도 그림 항목 -> 시트 그림 요소. 요소마다 그룹 "draft-<모양 종류>" 에 data-layer(층 이름, DXF 층이 된다)·data-id 를 붙인다."""
    cur = None
    for it in items:
        if it[1] != cur:
            if cur is not None:
                sh.end()
            cur = it[1]
            sh.begin(f"draft-{it[0]}", **{"data-layer": it[2], "data-id": cur})
        k = it[0]
        if k == "line":
            sh.line(tx(it[5]), ty(it[6]), tx(it[7]), ty(it[8]), it[3], it[4])
        elif k == "circle":
            sh.circle(tx(it[5]), ty(it[6]), it[7] / scale, it[3], it[4])
        elif k == "fill":
            sh.poly([(tx(x), ty(y)) for x, y in it[4]], fill=it[3])
        else:
            _, _, _, col, x, y, size, text, anchor, ang = it
            sh.text(tx(x), ty(y), size, text, anchor, col, -ang if ang else 0.0)
    if cur is not None:
        sh.end()


def build_view_page(data) -> Sheet:
    """뷰 한 장을 자기 크기의 용지(여백·제목 줄 포함, drawing_export.render_svg 와 같은 크기·그림)로 놓는다(view.image·뷰 PNG 용). 시트 틀은 없다."""
    w, h, tx, ty, datums, (dx0, dx1) = DX._frame(data)
    sh = Sheet("A3")
    sh.w, sh.h = w, h
    view_prims(sh, data, tx, ty, datums, dx0, dx1)
    sh.text(DX.MARGIN, h - 5.0, 5.0, DX.title_text(data), color=INK)
    return sh


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
    has_views = bool(scales)
    uniform = len(set(scales)) == 1
    primary = scales[0] if has_views else None
    y = y0

    def cell(h, label, value, size, max_lines=2, bold=False):
        nonlocal y
        sh.line(x0, y + h, x1, y + h, 0.25)
        sh.text(x0 + 2.0, y + 3.6, 2.5, label, color=GRAY)
        lines = wrap(value, size, TB_W - 5.0, max_lines)
        for i, s in enumerate(lines):
            sh.text(x0 + 3.0, y + 3.6 + 2.0 + size * 1.05 * (i + 1), size, s, bold=bold)
        y += h
    kp = spec.get("key_plan")
    if kp:                                                    # 키 플랜: 표제란 맨 위(오른쪽 위)
        sh.line(x0, y + KP_H, x1, y + KP_H, 0.25)
        sh.text(x0 + 2.0, y + 3.6, 2.5, "키 플랜", color=GRAY)
        ST.key_plan(sh, x0 + 2.0, y + 4.5, TB_W - 4.0, KP_H - 6.0, kp["data"], kp.get("zone"))
        y += KP_H
    ext = bool(spec.get("client") or spec.get("office"))
    cell(26.0 if not ext else 20.0, "프로젝트명", spec["project"], 4.5, 3 if not ext else 2)
    if ext:
        sh.begin("client-office")
        cell(11.0, "건축주", spec.get("client") or "-", 4.0, 1)
        cell(11.0, "설계사무소", spec.get("office") or "-", 4.0, 1)
        sh.end()
    cell(30.0, "도면명", spec["title"], 5.0, 3, True)
    cell(18.0, "도면번호", spec["number"], 7.0, 1, True)
    cell(14.0, "축척", (f"1:{primary}" if uniform else "도면참조") if has_views else "해당없음", 5.0, 1)
    cell(14.0, "작성일", spec["date"], 5.0, 1)
    if (spec.get("profile") or {}).get("projection") == "third":       # 투상법 칸(옵션): 제3각법 기호
        sh.line(x0, y + 16.0, x1, y + 16.0, 0.25)
        sh.text(x0 + 2.0, y + 3.6, 2.5, "투상법", color=GRAY)
        sh.text(x0 + 3.0, y + 11.0, 4.5, "제3각법")
        ST.projection_symbol(sh, x0 + 44.0, y + 2.4, 36.0, 12.0, "third")
        y += 16.0
    revs = spec.get("revisions") or []
    if revs:
        cell(10.0, "개정차수", revs[-1]["rev"], 4.0, 1)
    signers = spec.get("signers") or []
    if signers:
        sh.begin("signature-block", **{"data-count": len(signers)})
        rh = 7.5
        sh.text(x0 + 2.0, y + 3.6, 2.5, "서명·날인", color=GRAY)
        yy = y + 5.0
        for sg in signers:
            sh.line(x0, yy, x1, yy, 0.18)
            sh.text(x0 + 2.0, yy + 4.8, 2.6, ST.clip_text(sg["role"], 2.6, 20.0))
            sh.text(x0 + 24.0, yy + 4.8, 3.0, ST.clip_text(sg["name"] or "", 3.0, 18.0))
            sh.text(x0 + 44.0, yy + 5.0, 2.2, "(서명)", color=GRAY)
            sh.text(x0 + 66.0, yy + 5.0, 2.2, "(인)", color=GRAY)
            yy += rh
        sh.line(x0, yy, x1, yy, 0.25)
        sh.end()
        y = yy
    # 아래에서부터: 방위표(42), 축척 표시(26). 뷰가 없는 시트(색인)는 두 칸을 두지 않는다
    north_h, bar_h = (42.0, 26.0) if has_views else (0.0, 0.0)
    ybar, ynorth = y1 - north_h - bar_h, y1 - north_h
    if y > ybar + 1e-6:   # 작은 용지(A4 가로 등)에 칸이 많으면 축척 막대·방위표와 겹친다
        raise SheetError(f"표제란 칸이 {sh.paper} 용지 높이에 다 들어가지 않는다. 큰 용지나 세로(P)를 쓰거나 서명·건축주 칸을 줄이세요")
    # 개정란: 남는 높이에 표(개정·일자·내용)를 둔다. 개정 입력이 있으면 그 내용을 적는다(많으면 최근 것부터 칸 수만큼)
    free = ybar - y
    if free >= 18.0:
        sh.begin("revision-table", **({"data-count": len(revs)} if revs else {}))
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
        for i, rv in enumerate(revs[-(n - 1):] if n > 1 else []):
            yy = ty0 + 6.0 * (i + 1)
            sh.text(x0 + 2.0, yy + 4.2, 2.6, ST.clip_text(rv["rev"], 2.6, 9.0))
            sh.text(c1 + 2.0, yy + 4.2, 2.2, rv["date"])
            sh.text(c2 + 2.0, yy + 4.2, 2.4, ST.clip_text(rv["desc"], 2.4, x1 - c2 - 3.0))
        sh.end()
    if has_views:
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
    prof = spec.get("profile")
    sh.sym_k = (prof or {}).get("symbol_scale", 1.0)       # 기호 크기 배율(작성기준 프로파일). 주지 않으면 1
    sh.dim_w = HIER_DIM_W if (prof or {}).get("line_hierarchy") else (0.13, 0.13)   # 치수선·보조선 굵기(작성기준 프로파일의 선 굵기 위계)
    sh.begin("sheet-border")
    sh.rect(OUTER, OUTER, sh.w - 2 * OUTER, sh.h - 2 * OUTER, 0.7)      # 윤곽선
    sh.end()
    area_x0, area_y0 = OUTER + PAD, OUTER + PAD
    area_x1, area_y1 = sh.w - OUTER - TB_W - PAD, sh.h - OUTER - PAD
    nat = [_natural(v) for v in views]
    dims = spec.get("dims") or {}
    extra = [ST.ELEV_DIM_SPACE if (dims.get(v.get("id")) or {}).get("elevation") else 0.0 for v in views]   # 입면 치수가 있는 뷰는 아래를 더 비운다
    vps = spec.get("viewports") or {}   # 뷰포트(W1 1-C 2차): 위치(at)를 준 뷰는 자동 배치에서 빼고 그 자리에 둔다
    manual = {i: vps[v.get("id")]["at"] for i, v in enumerate(views) if (vps.get(v.get("id")) or {}).get("at")}
    auto = [i for i in range(len(views)) if i not in manual]
    pos, x, y, row_h = [], area_x0, area_y0, 0.0
    for i in auto:
        (cw, ch, *_), ex_ = nat[i], extra[i]
        bh = ch + CAP_H + ex_
        if cw > area_x1 - area_x0 or bh > area_y1 - area_y0:
            raise SheetError(f"뷰가 {sh.paper} 용지에 들어가지 않는다({cw:.0f}x{ch:.0f}mm). 더 큰 용지를 쓰거나 뷰의 축척을 키우세요(예: 1:100 -> 1:200)")
        if x > area_x0 and x + cw > area_x1:
            x, y, row_h = area_x0, y + row_h + GAP, 0.0
        pos.append((x, y))
        x += cw + GAP
        row_h = max(row_h, bh)
    if (not pos and not manual) or (pos and pos[-1][1] + row_h > area_y1 + 1e-6):
        raise SheetError(f"뷰 {len(views)}장이 {sh.paper} 용지 한 장에 다 들어가지 않는다. 용지를 키우거나 뷰를 나눠 시트를 여러 장 만드세요")
    at: dict[int, tuple] = {}
    if pos:   # 묶음 전체를 도면 영역 가운데로
        gx0 = min(p[0] for p in pos)
        gx1 = max(p[0] + nat[i][0] for p, i in zip(pos, auto))
        gy1 = pos[-1][1] + row_h
        dx = (area_x0 + area_x1) / 2.0 - (gx0 + gx1) / 2.0
        dy = ((area_y0 + area_y1) / 2.0 - (area_y0 + gy1) / 2.0)
        at = {i: (px + dx, py + dy) for i, (px, py) in zip(auto, pos)}
    for i, (mx, my) in manual.items():
        cw, ch = nat[i][0], nat[i][1]
        if mx < area_x0 - 1e-6 or my < area_y0 - 1e-6 or mx + cw > area_x1 + 1e-6 or my + ch + CAP_H + extra[i] > area_y1 + 1e-6:
            raise SheetError(f"뷰포트 at [{mx:g}, {my:g}] 에 놓은 뷰({cw:.0f}x{ch:.0f}mm, 제목 포함)가 도면 영역 "
                             f"[{area_x0:g}, {area_y0:g}]~[{area_x1:g}, {area_y1:g}] 을 벗어난다")
        at[i] = (float(mx), float(my))
    for i, (v, (cw, ch, tx, ty, datums, ex0, ex1), ex_) in enumerate(zip(views, nat, extra)):
        ox, oy = at[i]
        f = DX.MARGIN
        sh.begin(f"view-{v.get('id') or len(sh.placed)}", **{"data-view": v.get("id") or "", "data-scale": v["scale"]})
        TX, TY = (lambda X, _tx=tx, _ox=ox: _ox + _tx(X) - f), (lambda Y, _ty=ty, _oy=oy: _oy + _ty(Y) - f)
        view_prims(sh, v, TX, TY, datums, ex0, ex1, _style(spec), prof)
        _view_extras(sh, spec, v, TX, TY)
        vp = vps.get(v.get("id"))
        cap = f"{v['name']}" if vp is None or not vp.get("title") else vp["title"]
        ch_cap = ((prof or {}).get("text_height") or {}).get("caption", 4.0)
        if vp is not None and vp.get("number"):
            view_title(sh, ox, oy + ch + ex_, vp["number"], cap, v["scale"], ch_cap)
        else:
            sh.text(ox, oy + ch + 7.0 + ex_, ch_cap, cap, bold=True)
            sh.text(ox + est_w(cap, ch_cap) + 4.0, oy + ch + 7.0 + ex_, ch_cap * 0.875, f"S=1:{v['scale']}")
        sh.end()
        sh.placed.append({"view": v.get("id"), "name": v["name"], "scale": v["scale"], "x": round(ox, 2), "y": round(oy, 2),
                          "w": round(cw, 2), "h": round(ch, 2), **({"number": vp["number"]} if vp and vp.get("number") else {})})
    for rv in spec.get("revisions") or []:                    # 개정 구름: 개정한 뷰를 구름으로 둘러싸고 차수 삼각형을 붙인다
        pl = next((q for q in sh.placed if q["view"] == rv.get("view")), None) if rv.get("view") else None
        if pl:
            sh.begin("revision-cloud", **{"data-rev": rv["rev"], "data-view": pl["view"]})
            ST.cloud(sh, pl["x"] - 2.5, pl["y"] - 2.5, pl["x"] + pl["w"] + 2.5, pl["y"] + pl["h"] + 2.5)
            ST.triangle_tag(sh, pl["x"] + pl["w"] + 2.5, pl["y"] - 6.5, rv["rev"])
            sh.end()
    sh.begin("title-block")
    title_block(sh, spec, [v["scale"] for v in views])
    sh.end()
    return sh


# ---------------------------------------------------------------- 인쇄 설정(W1 1-F 5차, IO-17): 여백·축척 맞춤
def print_layout(sh: Sheet, paper: str, margin: float, fit: bool) -> tuple[Sheet, dict]:
    """다 만든 시트를 인쇄 용지 paper 에 옮긴다. 시트 배치는 그대로 두고 그림 전체를 한 배율 k 로 바꾼다(선 굵기·글 크기·파선도 같은 배율).
    fit 참: 네 변 여백 margin 안에 시트 전체가 들어가는 가장 큰 배율(확대도 한다). 거짓: 실제 크기(k = 1)이고 여백 안에 들어가지 않으면 SheetError.
    어느 쪽이든 여백 안 영역의 가운데에 놓는다. 돌려주는 정보: 인쇄 용지·여백·배율(%)·놓인 위치."""
    pw, ph = PAPERS[paper]
    aw, ah = pw - 2 * margin, ph - 2 * margin
    if aw <= 0 or ah <= 0:
        raise SheetError(f"여백 {margin:g}mm 가 {paper} 용지보다 크다")
    k = min(aw / sh.w, ah / sh.h) if fit else 1.0
    if not fit and (sh.w > aw + 1e-6 or sh.h > ah + 1e-6):
        raise SheetError(f"{sh.paper} 시트({sh.w:g}x{sh.h:g}mm)가 {paper} 용지의 여백 {margin:g}mm 안({aw:g}x{ah:g}mm)에 실제 크기로 들어가지 않는다. "
                         "fit 을 켜거나(축척 맞춤) 더 큰 용지·작은 여백을 쓰세요")
    ox, oy = margin + (aw - k * sh.w) / 2.0, margin + (ah - k * sh.h) / 2.0
    out = Sheet(paper)
    out.placed = [dict(q, x=round(ox + k * q["x"], 2), y=round(oy + k * q["y"], 2), w=round(k * q["w"], 2), h=round(k * q["h"], 2)) for q in sh.placed]
    X, Y = (lambda v: ox + k * v), (lambda v: oy + k * v)
    info = {"paper": paper, "margin": margin, "fit": fit, "zoom": round(k * 100.0, 3), "origin": [round(ox, 3), round(oy, 3)]}
    out.prims.append(("g", "print", {"data-sheet-paper": sh.paper, "data-zoom": f"{info['zoom']:g}"}))
    for p in sh.prims:
        t = p[0]
        if t == "line":
            _, x0, y0, x1, y1, w, c, dash = p
            out.prims.append(("line", X(x0), Y(y0), X(x1), Y(y1), w * k, c, (dash[0] * k, dash[1] * k) if dash else None))
        elif t == "poly":
            _, pts, fill, w, c = p
            out.prims.append(("poly", [(X(x), Y(y)) for x, y in pts], fill, w * k, c))
        elif t == "circle":
            _, cx, cy, r, w, c, fill = p
            out.prims.append(("circle", X(cx), Y(cy), r * k, w * k, c, fill))
        elif t == "text":
            _, x, y, size, s, anchor, c, ang, bold = p
            out.prims.append(("text", X(x), Y(y), size * k, s, anchor, c, ang, bold))
        else:   # 그룹 표지("g", "/g")는 그대로
            out.prims.append(p)
    out.prims.append(("/g",))
    return out, info


def view_title(sh: Sheet, x, y, number: str, title: str, scale, h: float = 4.0):
    """번호가 있는 뷰 제목(W1 1-C 2차, DOC-02): 상세 번호 원 + 제목(굵게) + 밑줄 + 축척. (x, y) = 뷰 틀의 왼쪽 아래(용지 mm)."""
    r = 4.5
    cx, cy = x + r, y + 6.0
    sh.begin("view-title", **{"data-number": number})
    sh.circle(cx, cy, r, 0.35)
    ns = 3.0 if len(number) <= 2 else (2.2 if len(number) <= 4 else 1.6)
    sh.text(cx, cy + ns * 0.35, ns, number, "middle")
    tx = x + 2.0 * r + 2.0
    sh.text(tx, cy - 1.0, h, title, bold=True)
    sh.line(x + 2.0 * r, cy + 0.6, tx + est_w(title, h) + 2.0, cy + 0.6, 0.5)
    sh.text(tx, cy + 4.6, h * 0.7, f"S=1:{scale}")
    sh.end()


def _style(spec: dict):
    """선 굵기 프로파일(별도 작성기준): 선 종류별 굵기만 바꾼다. 없으면 기본 체계(None)."""
    lp = spec.get("line_profile")
    if not lp:
        return None
    return {k: (lp.get(k, wc[0]), wc[1]) for k, wc in DX.STYLE.items()}


def _view_extras(sh: Sheet, spec: dict, v: dict, TX, TY):
    """뷰 위에 얹는 보조 기호(통심선·단면 기호·부위 상세·부호 태그). spec 에 해당 입력이 없으면 아무것도 그리지 않는다."""
    vid = v.get("id")
    if v["type"] == "plan":
        ST.grid_lines(sh, v, TX, TY, spec.get("grids"))
        ST.section_marks(sh, v, TX, TY, spec.get("sections"), spec.get("elevs"))
        tg = (spec.get("tags") or {}).get(vid) or {}
        ST.tag_items(sh, "room-numbers", tg.get("room"), TX, TY)
        ST.tag_items(sh, "opening-tags", tg.get("opening"), TX, TY)
        ST.tag_items(sh, "wall-tags", tg.get("wall"), TX, TY)
    ST.callouts(sh, vid, TX, TY, (spec.get("callouts") or []) + (spec.get("vcallouts") or []))   # vcallouts = 콜아웃 뷰(W1 1-C 2차)
    dm = (spec.get("dims") or {}).get(vid)
    if dm:
        prof = spec.get("profile")
        h = ((prof or {}).get("text_height") or {}).get("dimension", 2.5)
        ST.view_dims(sh, v["type"], dm, v, TX, TY, lambda t: fmt_dim(t, prof), h)


# ---------------------------------------------------------------- 색인 시트(뷰 없음): 도면목록표·범례·주기·일람표
INDEX_COLS = {
    "drawing_list": ("도면목록표", [("도면번호", 26.0), ("도면명", 74.0), ("공종", 16.0), ("축척", 18.0), ("비고", 24.0)]),
    "levels": ("레벨 노트(FL·GL)", [("레벨", 24.0), ("FL(mm)", 24.0), ("GL 대비(mm)", 32.0), ("비고", 38.0)]),
    "openings": ("창호 일람표", [("부호", 14.0), ("구분", 16.0), ("폭", 16.0), ("높이", 16.0), ("하단높이", 18.0), ("내화", 16.0), ("수량", 14.0), ("설치 층", 28.0)]),
    "walls": ("벽체 일람표", [("부호", 14.0), ("구분", 16.0), ("두께", 16.0), ("재료", 28.0), ("내화", 16.0), ("수량", 14.0), ("설치 층", 28.0)]),
    "materials": ("재료표(외벽·내벽 마감)", [("부위", 18.0), ("재료(품명)", 36.0), ("규격(두께 mm)", 28.0), ("내화 성능", 22.0), ("적용 벽 수", 20.0), ("적용 층", 26.0)]),
    # W1 1-B 3차: 리비전 일정표(DOC-15)·키노트 범례(ANN-09)
    "revisions": ("리비전 일정표", [("차수", 12.0), ("일자", 22.0), ("내용", 50.0), ("발행", 12.0), ("발행처", 24.0), ("발행자", 20.0), ("시트", 36.0)]),
    "keynotes": ("키노트 범례", [("키", 24.0), ("설명", 80.0), ("태그 수", 16.0)]),
}
INDEX_GID = {"drawing_list": "drawing-list", "levels": "level-note", "openings": "opening-schedule", "walls": "wall-schedule", "materials": "material-schedule",
             "revisions": "revision-schedule", "keynotes": "keynote-legend"}


def build_index_sheet(spec: dict, tables: dict) -> Sheet:
    """뷰 없이 표만 놓는 색인 시트. tables: drawing_list/levels/openings/walls/materials 행 목록, notes 줄 목록, legend(참이면 범례)."""
    sh = Sheet(spec["paper"])
    sh.begin("sheet-border")
    sh.rect(OUTER, OUTER, sh.w - 2 * OUTER, sh.h - 2 * OUTER, 0.7)
    sh.end()
    ax0, ay0 = OUTER + PAD, OUTER + PAD
    ax1, ay1 = sh.w - OUTER - TB_W - PAD, sh.h - OUTER - PAD
    x, y, colw = ax0, ay0, 0.0
    blocks = []
    if tables.get("legend"):
        blocks.append(("legend", 118.0, ST.LEGEND_H))
    for key in ("drawing_list", "levels", "openings", "walls", "materials", "revisions", "keynotes"):
        rows = tables.get(key)
        if rows is not None:
            cols = INDEX_COLS[key][1]
            blocks.append((key, sum(c[1] for c in cols), 6.0 + 5.6 * (len(rows) + 1) + 6.0))
    if tables.get("notes"):
        blocks.append(("notes", 118.0, 6.0 + 5.2 * len(tables["notes"]) + 4.0))
    for key, bw, bh in blocks:
        if bw > ax1 - ax0 or bh > ay1 - ay0:
            raise SheetError(f"표 '{key}'가 {sh.paper} 용지에 들어가지 않는다. 더 큰 용지(A1)를 쓰거나 행 수를 줄이세요")
        if y > ay0 and y + bh > ay1:
            x, y, colw = x + colw + GAP, ay0, 0.0
        if x + bw > ax1 + 1e-6:
            raise SheetError(f"표가 {sh.paper} 용지 한 장에 다 들어가지 않는다. 더 큰 용지(A1)를 쓰거나 표 수를 줄이세요")
        if key == "legend":
            ST.legend_block(sh, x, y, _style(spec) or DX.STYLE)
        elif key == "notes":
            ST.notes_block(sh, x, y, tables["notes"], bw - 2.0)
        else:
            ST.draw_table(sh, x, y, INDEX_COLS[key][0], INDEX_COLS[key][1], tables[key], INDEX_GID[key])
        y += bh
        colw = max(colw, bw)
    sh.begin("title-block")
    title_block(sh, spec, [])
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
    body, dropped = _pdf_page_body(sh, font)
    return _pdf_one_page(sh, spec, font, body, k), dropped


def _pdf_page_body(sh: Sheet, font: PdfFont | None) -> tuple[str, bool]:
    """시트 한 장의 PDF 콘텐츠 스트림(글꼴 GID 로 바꾸기 전 문자열)과 글자를 '?' 로 바꿨는지.
    글꼴이 있으면 쓴 글자를 font 에 모은다(부분집합은 모든 쪽 본문을 만든 뒤 한 번 만든다)."""
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
    return "\n".join(c), dropped


def _pdf_one_page(sh: Sheet, spec: dict, font: PdfFont | None, body: str, k: float) -> bytes:
    """한 쪽 PDF 파일(이전 render_sheet_pdf 의 뒷부분 그대로: 객체 번호·바이트가 같다)."""
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
    return bytes(buf)


def _pdf_text(s: str) -> str:
    """PDF 글 문자열(UTF-16BE 16진, 책갈피·문서 제목용)."""
    return "<FEFF" + s.encode("utf-16-be").hex().upper() + ">"


def render_sheets_pdf(pages: list[tuple[Sheet, dict]], font: PdfFont | None = None, title: str | None = None) -> tuple[bytes, bool]:
    """여러 시트를 한 PDF 로(DOC-14 일괄 출력). 쪽마다 자기 용지 크기를 쓰고, 시트마다 책갈피(도면번호 도면명)를 단다.
    글꼴은 모든 쪽이 하나를 같이 쓴다(쓴 글자 전체의 부분집합 하나). 반환: (바이트, 글자를 '?' 로 바꿨는지)"""
    k = 72.0 / 25.4
    bodies = [_pdf_page_body(sh, font) for sh, _ in pages]          # 먼저 모든 쪽의 글자를 모은 뒤 부분집합 글꼴을 만든다
    dropped = any(d for _, d in bodies)
    contents = [font.finalize_stream(b) if font is not None else b.encode("ascii") for b, _ in bodies]
    n = len(pages)
    # 객체 번호: 1 Catalog, 2 Pages, 3 Info, 4 Outlines, 5.. 글꼴, 그다음 쪽마다 (Page, Contents, 책갈피 항목)
    if font is not None:
        fobjs, t0 = font.objects(5)
    else:
        fobjs, t0 = [b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"], 5
    first = 5 + len(fobjs)
    page_id = [first + 3 * i for i in range(n)]
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R /Outlines 4 0 R /PageMode /UseOutlines >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{x} 0 R' for x in page_id)}] /Count {n} >>".encode(),
        ("<< /Title " + _pdf_text(title or "") + " /Creator (Democratization of BIM) >>").encode("ascii"),
        f"<< /Type /Outlines /First {page_id[0] + 2} 0 R /Last {page_id[-1] + 2} 0 R /Count {n} >>".encode(),
    ] + fobjs
    res = f"/Font << /F1 {t0} 0 R >>"
    for i, ((sh, spec), comp) in enumerate(zip(pages, (zlib.compress(c, 9) for c in contents))):
        pid = page_id[i]
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {sh.w * k:.2f} {sh.h * k:.2f}] /Resources << {res} >> /Contents {pid + 1} 0 R >>".encode())
        objs.append(f"<< /Length {len(comp)} /Filter /FlateDecode >>\nstream\n".encode() + comp + b"\nendstream")
        link = (f" /Prev {page_id[i - 1] + 2} 0 R" if i else "") + (f" /Next {page_id[i + 1] + 2} 0 R" if i + 1 < n else "")
        objs.append(("<< /Title " + _pdf_text(f"{spec['number']} {spec['title']}") + f" /Parent 4 0 R{link} /Dest [{pid} 0 R /Fit] >>").encode("ascii"))
    buf = bytearray(b"%PDF-1.5\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        buf += f"{o:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 3 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
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
