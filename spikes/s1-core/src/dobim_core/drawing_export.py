# SPDX-License-Identifier: MPL-2.0
"""도면 선분 -> 벡터 SVG / PDF (스파이크 S3, 버릴 코드).

외부 변환기 없이 직접 쓴다: SVG 는 문자열, PDF 는 최소 구조(페이지 1장, 선·다각형·Helvetica 문자열).
- SVG 는 한글 텍스트를 그대로 담는다(글꼴은 보는 쪽 시스템 글꼴: 맑은 고딕·Noto Sans KR 순).
- PDF 는 글꼴(pdf_font, 시트와 같은 경로)이 있으면 한글을 포함해 부분집합으로 내장한다. 글꼴이 없을 때만 Helvetica 로 내려가
  ASCII 만 내고 한글은 '?' 로 바꾸며 non_ascii_dropped 로 알린다 (S0 E5 와 같은 한계).
순수 함수만 둔다(파일 접근은 호출한 커맨드가 한다).
"""
from __future__ import annotations

from math import atan2, degrees
import re
from xml.sax.saxutils import escape

# XML 1.0 이 허용하지 않는 글자(제어문자 대부분, 홑서로게이트, U+FFFE/FFFF). 사용자 글(뷰 이름·태그 필드 값)에 들어 있어도 SVG 가 깨지면 안 된다
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def _esc(s) -> str:
    """SVG 글 안전 처리: XML 금지 문자는 U+FFFD 로 바꾸고 &, <, > 를 이스케이프한다."""
    return escape(_XML_BAD.sub("�", str(s)))

MARGIN = 15.0          # 용지 여백 mm
TITLE_H = 16.0         # 제목 블록 높이 mm
FONT = "'Malgun Gothic','Noto Sans KR','Apple SD Gothic Neo',sans-serif"
STYLE = {"cut": (0.50, "#000000"), "proj": (0.18, "#000000"), "symbol": (0.25, "#333333"), "hatch": (0.09, "#555555")}
LINE_ORDER = ("hatch", "proj", "symbol", "cut")                   # 굵은 선이 위로 온다
DATUM_W = 0.13


def label_width(s: str, size: float) -> float:
    """문자 층 글의 대략 폭(용지 mm). 정확한 폭은 글꼴이 정하므로 배치·범위 계산에만 쓴다."""
    return sum(size * (1.0 if ord(c) >= 0x2E80 else 0.58) for c in s)


def _frame(data):
    """모델 좌표 -> 용지 mm 변환과 용지 크기. 반환: (width, height, tx, ty, datums)"""
    lines = data["lines"]
    scale = float(data["scale"])
    pts = [(ln[1], ln[2]) for ln in lines] + [(ln[3], ln[4]) for ln in lines]
    for poly in data["fills"]:
        pts += list(poly)
    for it in data.get("annots", ()):
        pts += _annot_pts(it)
    for lb in data.get("labels", ()):
        half = label_width(lb[2], lb[3]) * scale / 2.0
        pts += [(lb[0] - half, lb[1]), (lb[0] + half, lb[1] + lb[3] * scale)]
    for it in data.get("drafts", ()):   # 2D 작도(27단계): 없으면 범위는 이전과 같다
        pts += _draft_pts(it, scale)
    if pts:
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    else:
        x0 = y0 = 0.0
        x1 = y1 = 1000.0
    datums = []
    if data["type"] not in ("plan", "site", "legend"):
        for name, z in data.get("datums", ()):
            if y0 - 1.0 <= z <= y1 + 1.0:
                datums.append((name, z))
        x0, x1 = x0 - 1000.0, x1 + 1000.0                 # 기준선이 건물 밖으로 조금 나온다
    w = (x1 - x0) / scale + 2 * MARGIN
    h = (y1 - y0) / scale + 2 * MARGIN + TITLE_H

    def tx(x):
        return MARGIN + (x - x0) / scale

    def ty(y):
        return MARGIN + (y1 - y) / scale

    return w, h, tx, ty, datums, (x0, x1)


def _annot_pts(it):
    """주석 항목이 차지하는 대표 좌표들(용지 크기 계산용)."""
    t = it[0]
    if t == "dim":
        return [(it[2][0], it[2][1]), (it[2][2], it[2][3]), (it[3][2], it[3][3]), (it[4][0], it[4][1]), (it[4][2], it[4][3])]
    if t == "tag":
        return [tuple(it[2]), (it[3][0] + 1500.0, it[3][1])]
    if t == "adim":   # 각도·반지름·지름 치수(W1 1-B 2차)
        return [(s[0], s[1]) for s in it[2]] + [(s[2], s[3]) for s in it[2]] + [tuple(it[5])]
    return [(it[4], it[5]), (it[4] + 3000.0, it[5])]


def _draft_pts(it, scale):
    """2D 작도 그림 항목이 차지하는 대표 좌표(용지 크기 계산용). 문자는 대략 폭까지."""
    k = it[0]
    if k == "line":
        return [(it[5], it[6]), (it[7], it[8])]
    if k == "circle":
        return [(it[5] - it[7], it[6] - it[7]), (it[5] + it[7], it[6] + it[7])]
    if k == "fill":
        return list(it[4])
    w = label_width(it[7], it[6]) * scale
    return [(it[4] - w, it[5] - w), (it[4] + w, it[5] + w)]


def _anchor_dx(anchor, wd):
    return {"start": 0.0, "middle": -wd / 2.0, "end": -wd}[anchor]


def draft_svg(data, tx, ty, scale) -> list[str]:
    """2D 작도 층(SVG). 요소마다 <g class="draft" data-id data-layer> 하나. 굵기·색은 항목 값 그대로(용지 mm)."""
    items = data.get("drafts", ())
    if not items:
        return []
    out = ['<g id="drafts" stroke-linecap="round">']
    cur = None
    for it in items:
        if it[1] != cur:
            if cur is not None:
                out.append("</g>")
            cur = it[1]
            out.append(f'<g class="draft" data-id="{cur}" data-layer="{_esc(it[2])}">')
        k = it[0]
        if k == "line":
            out.append(f'<line x1="{tx(it[5]):.3f}" y1="{ty(it[6]):.3f}" x2="{tx(it[7]):.3f}" y2="{ty(it[8]):.3f}" stroke="{it[4]}" stroke-width="{it[3]:g}"/>')
        elif k == "circle":
            out.append(f'<circle cx="{tx(it[5]):.3f}" cy="{ty(it[6]):.3f}" r="{it[7] / scale:.3f}" fill="none" stroke="{it[4]}" stroke-width="{it[3]:g}"/>')
        elif k == "fill":
            out.append('<polygon points="' + " ".join(f"{tx(x):.3f},{ty(y):.3f}" for x, y in it[4]) + f'" fill="{it[3]}" stroke="none"/>')
        else:
            _, _, _, col, x, y, size, text, anchor, ang = it
            tr = f"translate({tx(x):.3f},{ty(y):.3f})" + (f" rotate({-ang:.2f})" if ang else "")
            out.append(f'<text transform="{tr}" font-size="{size:g}" fill="{col}" text-anchor="{anchor}">{_esc(text)}</text>')
    out.append("</g>")
    out.append("</g>")
    return out


def adim_text_angle(u) -> float:
    """각도·반지름·지름 치수 글의 용지 회전각(도). 글이 거꾸로 서지 않게 (-90, 90] 로 맞춘다(선형 치수와 같은 규칙)."""
    ang = degrees(atan2(-u[1], u[0]))
    return ang - 180.0 if ang > 90.0 else (ang + 180.0 if ang <= -90.0 else ang)


REASON_KO = {"target_deleted": "대상 삭제됨", "successor_deleted": "이어받은 벽 삭제됨", "bad_ref": "잘못된 참조",
             "not_parallel": "평행하지 않음", "mixed": "참조 종류 혼합", "parallel": "평행함", "far_vertex": "교점이 너무 멂"}
ANNOT_COLOR, ORPHAN_COLOR = "#1a4fa0", "#c62828"


def annot_svg(data, tx, ty) -> list[str]:
    """주석 층(SVG). 치수: 보조선·치수선·눈금·글, 태그: 지시선·글, 고아·무효: 빨간 표시와 이유. 한글은 SVG 에만 담는다."""
    items = data.get("annots", ())
    if not items:
        return []
    out = [f'<g id="annotations" font-size="2.5" fill="{ANNOT_COLOR}" stroke="{ANNOT_COLOR}" stroke-width="0.13" stroke-linecap="round">']
    for it in items:
        t, aid = it[0], it[1]
        if t == "dim":
            _, _, e0, e1, ln, text, tpos, u = it
            out.append(f'<g class="dim" data-id="{aid}">')
            for e in (e0, e1):
                out.append(f'<line x1="{tx(e[0]):.3f}" y1="{ty(e[1]):.3f}" x2="{tx(e[2]):.3f}" y2="{ty(e[3]):.3f}"/>')
            out.append(f'<line x1="{tx(ln[0]):.3f}" y1="{ty(ln[1]):.3f}" x2="{tx(ln[2]):.3f}" y2="{ty(ln[3]):.3f}"/>')
            upx, upy = u[0], -u[1]                                  # 용지 좌표는 y 가 아래로 자란다
            for px, py in ((tx(ln[0]), ty(ln[1])), (tx(ln[2]), ty(ln[3]))):
                kx, ky = (upx - upy) * 0.64, (upy + upx) * 0.64      # 45도 눈금
                out.append(f'<line x1="{px - kx:.3f}" y1="{py - ky:.3f}" x2="{px + kx:.3f}" y2="{py + ky:.3f}" stroke-width="0.25"/>')
            ang = degrees(atan2(upy, upx))
            ang = ang - 180.0 if ang > 90.0 else (ang + 180.0 if ang <= -90.0 else ang)
            out.append(f'<text transform="translate({tx(tpos[0]):.3f},{ty(tpos[1]):.3f}) rotate({ang:.2f})" text-anchor="middle" '
                       f'dy="-0.8" stroke="none">{_esc(text)}</text>')
            out.append("</g>")
        elif t == "adim":   # 각도·반지름·지름 치수(W1 1-B 2차): 선분들(호는 짧은 선분으로 나눴다)·끝 눈금·글
            _, _, segs, ticks, text, tpos, u = it
            out.append(f'<g class="dim" data-id="{aid}">')
            for e in segs:
                out.append(f'<line x1="{tx(e[0]):.3f}" y1="{ty(e[1]):.3f}" x2="{tx(e[2]):.3f}" y2="{ty(e[3]):.3f}"/>')
            for x, y, ux, uy in ticks:
                kx, ky = (ux + uy) * 0.64, (-uy + ux) * 0.64      # 45도 눈금(용지 y 는 아래로)
                out.append(f'<line x1="{tx(x) - kx:.3f}" y1="{ty(y) - ky:.3f}" x2="{tx(x) + kx:.3f}" y2="{ty(y) + ky:.3f}" stroke-width="0.25"/>')
            ang = adim_text_angle(u)
            out.append(f'<text transform="translate({tx(tpos[0]):.3f},{ty(tpos[1]):.3f}) rotate({ang:.2f})" text-anchor="middle" '
                       f'dy="-0.8" stroke="none">{_esc(text)}</text>')
            out.append("</g>")
        elif t == "tag":
            _, _, anc, lab, text = it
            out.append(f'<g class="tag" data-id="{aid}" fill="#8a3b00" stroke="#8a3b00">')
            out.append(f'<line x1="{tx(anc[0]):.3f}" y1="{ty(anc[1]):.3f}" x2="{tx(lab[0]):.3f}" y2="{ty(lab[1]):.3f}"/>')
            out.append(f'<circle cx="{tx(anc[0]):.3f}" cy="{ty(anc[1]):.3f}" r="0.5" stroke="none"/>')
            out.append(f'<text x="{tx(lab[0]) + 0.6:.3f}" y="{ty(lab[1]):.3f}" stroke="none">{_esc(text)}</text>')
            out.append("</g>")
        else:
            _, _, kind, reason, x, y = it
            what = "치수" if kind == "dim" else "태그"
            label = f"{'고아' if t == 'orphan' else '무효'} {what} {aid[-6:]}: {REASON_KO.get(reason, reason)}"
            px, py = tx(x), ty(y)
            out.append(f'<g class="{t}" data-id="{aid}" fill="{ORPHAN_COLOR}" stroke="{ORPHAN_COLOR}">')
            out.append(f'<line x1="{px - 1:.3f}" y1="{py - 1:.3f}" x2="{px + 1:.3f}" y2="{py + 1:.3f}"/>'
                       f'<line x1="{px - 1:.3f}" y1="{py + 1:.3f}" x2="{px + 1:.3f}" y2="{py - 1:.3f}"/>'
                       f'<circle cx="{px:.3f}" cy="{py:.3f}" r="1.6" fill="none" stroke-dasharray="0.6 0.4"/>')
            out.append(f'<text x="{px + 2.2:.3f}" y="{py + 0.8:.3f}" stroke="none">{_esc(label)}</text>')
            out.append("</g>")
    out.append("</g>")
    return out


def title_text(data) -> str:
    kind = {"rcp": "반사 천장 평면도", "area": "면적 평면도"}.get(data.get("mode")) or {"plan": "평면도", "section": "단면도", "elevation": "입면도", "site": "배치도", "legend": "범례"}[data["type"]]
    if kind in data["name"]:   # 뷰 이름이 종류명과 같거나 이미 포함하면 종류명을 한 번만 쓴다
        return f"{data['name']}  S=1:{data['scale']}"
    return f"{data['name']}  {kind}  S=1:{data['scale']}"


def datum_label(name, z) -> str:
    return f"{name} (+{z:.0f})" if z else f"{name} (±0)"


def render_svg(data) -> bytes:
    w, h, tx, ty, datums, (dx0, dx1) = _frame(data)
    out = [f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="{w:.2f}mm" height="{h:.2f}mm" '
           f'viewBox="0 0 {w:.2f} {h:.2f}" font-family="{FONT}">',
           f'<title>{_esc(title_text(data))}</title>',
           f'<rect x="0" y="0" width="{w:.2f}" height="{h:.2f}" fill="#ffffff"/>']
    if data["fills"]:
        out.append('<g id="fills" fill="#cfcfcf" stroke="none">')
        for poly in data["fills"]:
            out.append('<polygon points="' + " ".join(f"{tx(x):.3f},{ty(y):.3f}" for x, y in poly) + '"/>')
        out.append("</g>")
    if datums:
        out.append(f'<g id="datums" stroke="#555555" stroke-width="{DATUM_W}" stroke-dasharray="3 1.5" fill="#555555" font-size="2.6">')
        for name, z in datums:
            out.append(f'<line x1="{tx(dx0):.3f}" y1="{ty(z):.3f}" x2="{tx(dx1):.3f}" y2="{ty(z):.3f}"/>')
            out.append(f'<text x="{tx(dx0) + 1:.3f}" y="{ty(z) - 0.8:.3f}" stroke="none">{_esc(datum_label(name, z))}</text>')
        out.append("</g>")
    for cls in LINE_ORDER:
        sel = [ln for ln in data["lines"] if ln[0] == cls]
        if not sel:
            continue
        wd, col = STYLE[cls]
        out.append(f'<g id="lines-{cls}" stroke="{col}" stroke-width="{wd}" stroke-linecap="round" fill="none">')
        for _, x0, y0, x1, y1 in sel:
            out.append(f'<line x1="{tx(x0):.3f}" y1="{ty(y0):.3f}" x2="{tx(x1):.3f}" y2="{ty(y1):.3f}"/>')
        out.append("</g>")
    labels = data.get("labels", ())
    if labels:
        out.append('<g id="labels" fill="#000000" stroke="none" text-anchor="middle">')
        for x, y, text, size, _anchor in labels:
            out.append(f'<text x="{tx(x):.3f}" y="{ty(y):.3f}" font-size="{size}">{_esc(text)}</text>')
        out.append("</g>")
    out += draft_svg(data, tx, ty, float(data["scale"]))
    out += annot_svg(data, tx, ty)
    out.append(f'<text x="{MARGIN}" y="{h - 5:.2f}" font-size="5" fill="#000000">{_esc(title_text(data))}</text>')
    out.append("</svg>\n")
    return "\n".join(out).encode("utf-8")


# ---------------------------------------------------------------- PDF
def _pdf_str(s: str) -> tuple[str, bool]:
    dropped = not s.isascii()
    t = "".join(c if c.isascii() and c.isprintable() else "?" for c in s)
    return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"), dropped


def render_pdf(data, font=None) -> tuple[bytes, bool]:
    """벡터 PDF 한 쪽. font(pdf_font.PdfFont)가 있으면 한글을 포함한 모든 글을 그 글꼴 부분집합으로 내장한다(시트 PDF 와 같은 경로).
    없으면 Helvetica 로 내려가 비 ASCII 문자를 '?' 로 바꾼다. 반환: (바이트, 비 ASCII 문자를 '?' 로 바꿨는지)"""
    w, h, tx, ty, datums, (dx0, dx1) = _frame(data)
    k = 72.0 / 25.4
    px = lambda x: f"{tx(x) * k:.3f}"                       # noqa: E731
    py = lambda y: f"{(h - ty(y)) * k:.3f}"                 # noqa: E731
    dropped = False

    def txt(s_):
        """Tj 앞에 올 글 인자. 글꼴이 있으면 16진 CID 문자열, 없으면 ASCII 괄호 문자열('?' 치환은 dropped 에 모은다)."""
        nonlocal dropped
        if font is not None:
            return font.show(s_)
        t, d = _pdf_str(s_)
        dropped |= d
        return f"({t})"

    c = ["q", "1 J"]
    if data["fills"]:
        c.append("0.81 g")
        for poly in data["fills"]:
            c.append(f"{px(poly[0][0])} {py(poly[0][1])} m " + " ".join(f"{px(x)} {py(y)} l" for x, y in poly[1:]) + " h f")
    c.append("0 g")
    if datums:
        c += [f"{DATUM_W * k:.3f} w", "0.33 G", "[8 4] 0 d"]
        for name, z in datums:
            c.append(f"{px(dx0)} {py(z)} m {px(dx1)} {py(z)} l S")
        c.append("[] 0 d")
        c.append("0.33 g")
        for name, z in datums:
            c.append(f"BT /F1 {2.6 * k:.2f} Tf {tx(dx0) * k + 3:.2f} {(h - ty(z) + 0.8) * k:.2f} Td {txt(datum_label(name, z))} Tj ET")
        c.append("0 g")
    c.append("0 G")
    for cls in LINE_ORDER:
        sel = [ln for ln in data["lines"] if ln[0] == cls]
        if not sel:
            continue
        c.append(f"{STYLE[cls][0] * k:.3f} w")
        for _, x0, y0, x1, y1 in sel:
            c.append(f"{px(x0)} {py(y0)} m {px(x1)} {py(y1)} l S")
    for x, y, text, size, _anchor in data.get("labels", ()):
        c.append(f"BT /F1 {size * k:.2f} Tf {(tx(x) - label_width(text, size) / 2.0) * k:.2f} {(h - ty(y)) * k:.2f} Td {txt(text)} Tj ET")
    if data.get("drafts"):   # 2D 작도(27단계): 없으면 PDF 바이트는 이전과 같다
        c += draft_pdf(data["drafts"], px, py, tx, ty, h, k, float(data["scale"]), txt)
    c.append("0.1 0.3 0.63 RG 0.1 0.3 0.63 rg")
    for it in data.get("annots", ()):
        if it[0] == "dim":
            c.append(f"{0.13 * k:.3f} w")
            for e in (it[2], it[3], it[4]):
                c.append(f"{px(e[0])} {py(e[1])} m {px(e[2])} {py(e[3])} l S")
            c.append(f"BT /F1 {2.5 * k:.2f} Tf {tx(it[6][0]) * k:.2f} {(h - ty(it[6][1]) + 0.8) * k:.2f} Td {txt(it[5])} Tj ET")
        elif it[0] == "adim":   # 각도·반지름·지름 치수(W1 1-B 2차)
            c.append(f"{0.13 * k:.3f} w")
            for e in it[2]:
                c.append(f"{px(e[0])} {py(e[1])} m {px(e[2])} {py(e[3])} l S")
            c.append(f"BT /F1 {2.5 * k:.2f} Tf {tx(it[5][0]) * k:.2f} {(h - ty(it[5][1]) + 0.8) * k:.2f} Td {txt(it[4])} Tj ET")
        elif it[0] == "tag":
            c.append(f"{0.13 * k:.3f} w {px(it[2][0])} {py(it[2][1])} m {px(it[3][0])} {py(it[3][1])} l S")
            c.append(f"BT /F1 {2.5 * k:.2f} Tf {tx(it[3][0]) * k + 2:.2f} {(h - ty(it[3][1])) * k:.2f} Td {txt(it[4])} Tj ET")
        else:
            orphan = f"{it[0]} {it[2]} {it[1][-6:]}: {it[3]}"
            c.append(f"BT /F1 {2.5 * k:.2f} Tf {tx(it[4]) * k:.2f} {(h - ty(it[5])) * k:.2f} Td {txt(orphan)} Tj ET")
    c.append("0 G 0 g")
    c.append(f"BT /F1 {5 * k:.2f} Tf {MARGIN * k:.2f} {5 * k:.2f} Td {txt(title_text(data))} Tj ET")
    c.append("Q")
    body = "\n".join(c)
    content = font.finalize_stream(body) if font is not None else body.encode("ascii")
    if font is not None:
        fobjs, t0 = font.objects(5)                         # 5번부터 글꼴 객체 5개(Type0·CID·디스크립터·FontFile2·ToUnicode)
    else:
        fobjs, t0 = [b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"], 5
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w * k:.2f} {h * k:.2f}] /Resources << /Font << /F1 {t0} 0 R >> >> /Contents 4 0 R >>".encode(),
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            *fobjs]
    buf = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        buf += f"{o:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(buf), dropped


def _pdf_rgb(col: str) -> str:
    return " ".join(f"{int(col[i:i + 2], 16) / 255:.3f}" for i in (1, 3, 5))


def draft_pdf(items, px, py, tx, ty, h, k, scale, txt) -> list[str]:
    """2D 작도 층(PDF 그리기 명령). 원은 베지어 4조각, 문자는 회전 행렬(Tm)."""
    from math import cos, radians, sin
    c = []
    for it in items:
        kd = it[0]
        if kd == "line":
            c.append(f"{it[3] * k:.3f} w {_pdf_rgb(it[4])} RG {px(it[5])} {py(it[6])} m {px(it[7])} {py(it[8])} l S")
        elif kd == "circle":
            cx, cy, r = tx(it[5]) * k, (h - ty(it[6])) * k, it[7] / scale * k
            m = 0.5523 * r
            c.append(f"{it[3] * k:.3f} w {_pdf_rgb(it[4])} RG {cx + r:.3f} {cy:.3f} m "
                     f"{cx + r:.3f} {cy + m:.3f} {cx + m:.3f} {cy + r:.3f} {cx:.3f} {cy + r:.3f} c "
                     f"{cx - m:.3f} {cy + r:.3f} {cx - r:.3f} {cy + m:.3f} {cx - r:.3f} {cy:.3f} c "
                     f"{cx - r:.3f} {cy - m:.3f} {cx - m:.3f} {cy - r:.3f} {cx:.3f} {cy - r:.3f} c "
                     f"{cx + m:.3f} {cy - r:.3f} {cx + r:.3f} {cy - m:.3f} {cx + r:.3f} {cy:.3f} c S")
        elif kd == "fill":
            pts = it[4]
            c.append(f"{_pdf_rgb(it[3])} rg {px(pts[0][0])} {py(pts[0][1])} m " + " ".join(f"{px(x)} {py(y)} l" for x, y in pts[1:]) + " h f")
        else:
            _, _, _, col, x, y, size, text, anchor, ang = it
            a = radians(ang)
            dx = _anchor_dx(anchor, label_width(text, size))
            ox, oy = (tx(x) + dx * cos(a)) * k, (h - ty(y) + dx * sin(a)) * k
            c.append(f"{_pdf_rgb(col)} rg BT /F1 {size * k:.2f} Tf {cos(a):.4f} {sin(a):.4f} {-sin(a):.4f} {cos(a):.4f} {ox:.2f} {oy:.2f} Tm {txt(text)} Tj ET")
    c.append("0 G 0 g")
    return c
