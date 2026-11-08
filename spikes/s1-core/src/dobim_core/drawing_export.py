# SPDX-License-Identifier: MPL-2.0
"""도면 선분 -> 벡터 SVG / PDF (스파이크 S3, 버릴 코드).

외부 변환기 없이 직접 쓴다: SVG 는 문자열, PDF 는 최소 구조(페이지 1장, 선·다각형·Helvetica 문자열).
- SVG 는 한글 텍스트를 그대로 담는다(글꼴은 보는 쪽 시스템 글꼴: 맑은 고딕·Noto Sans KR 순).
- PDF 는 글꼴을 내장하지 않아 ASCII 문자열만 낸다. 한글은 '?' 로 바꾸고 non_ascii_dropped 로 알린다 (S0 E5 와 같은 한계).
  한글 PDF 는 글꼴 내장 또는 글자 윤곽선 변환이 필요하며 S3 범위 밖이다.
순수 함수만 둔다(파일 접근은 호출한 커맨드가 한다).
"""
from __future__ import annotations

from xml.sax.saxutils import escape

MARGIN = 15.0          # 용지 여백 mm
TITLE_H = 16.0         # 제목 블록 높이 mm
FONT = "'Malgun Gothic','Noto Sans KR','Apple SD Gothic Neo',sans-serif"
STYLE = {"cut": (0.50, "#000000"), "proj": (0.18, "#000000"), "symbol": (0.25, "#333333")}
DATUM_W = 0.13


def _frame(data):
    """모델 좌표 -> 용지 mm 변환과 용지 크기. 반환: (width, height, tx, ty, datums)"""
    lines = data["lines"]
    scale = float(data["scale"])
    pts = [(ln[1], ln[2]) for ln in lines] + [(ln[3], ln[4]) for ln in lines]
    for poly in data["fills"]:
        pts += list(poly)
    if pts:
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    else:
        x0 = y0 = 0.0
        x1 = y1 = 1000.0
    datums = []
    if data["type"] != "plan":
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


def title_text(data) -> str:
    kind = {"plan": "평면도", "section": "단면도", "elevation": "입면도"}[data["type"]]
    return f"{data['name']}  {kind}  S=1:{data['scale']}"


def datum_label(name, z) -> str:
    return f"{name} (+{z:.0f})" if z else f"{name} (±0)"


def render_svg(data) -> bytes:
    w, h, tx, ty, datums, (dx0, dx1) = _frame(data)
    out = [f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="{w:.2f}mm" height="{h:.2f}mm" '
           f'viewBox="0 0 {w:.2f} {h:.2f}" font-family="{FONT}">',
           f'<title>{escape(title_text(data))}</title>',
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
            out.append(f'<text x="{tx(dx0) + 1:.3f}" y="{ty(z) - 0.8:.3f}" stroke="none">{escape(datum_label(name, z))}</text>')
        out.append("</g>")
    for cls in ("proj", "symbol", "cut"):                    # 굵은 선이 위로 온다
        sel = [ln for ln in data["lines"] if ln[0] == cls]
        if not sel:
            continue
        wd, col = STYLE[cls]
        out.append(f'<g id="lines-{cls}" stroke="{col}" stroke-width="{wd}" stroke-linecap="round" fill="none">')
        for _, x0, y0, x1, y1 in sel:
            out.append(f'<line x1="{tx(x0):.3f}" y1="{ty(y0):.3f}" x2="{tx(x1):.3f}" y2="{ty(y1):.3f}"/>')
        out.append("</g>")
    out.append(f'<text x="{MARGIN}" y="{h - 5:.2f}" font-size="5" fill="#000000">{escape(title_text(data))}</text>')
    out.append("</svg>\n")
    return "\n".join(out).encode("utf-8")


# ---------------------------------------------------------------- PDF
def _pdf_str(s: str) -> tuple[str, bool]:
    dropped = not s.isascii()
    t = "".join(c if c.isascii() and c.isprintable() else "?" for c in s)
    return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"), dropped


def render_pdf(data) -> tuple[bytes, bool]:
    """벡터 PDF 한 쪽. 반환: (바이트, 한글 등 비 ASCII 문자를 '?' 로 바꿨는지)"""
    w, h, tx, ty, datums, (dx0, dx1) = _frame(data)
    k = 72.0 / 25.4
    px = lambda x: f"{tx(x) * k:.3f}"                       # noqa: E731
    py = lambda y: f"{(h - ty(y)) * k:.3f}"                 # noqa: E731
    dropped = False
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
            s, d = _pdf_str(datum_label(name, z))
            dropped |= d
            c.append(f"BT /F1 {2.6 * k:.2f} Tf {tx(dx0) * k + 3:.2f} {(h - ty(z) + 0.8) * k:.2f} Td ({s}) Tj ET")
        c.append("0 g")
    c.append("0 G")
    for cls in ("proj", "symbol", "cut"):
        sel = [ln for ln in data["lines"] if ln[0] == cls]
        if not sel:
            continue
        c.append(f"{STYLE[cls][0] * k:.3f} w")
        for _, x0, y0, x1, y1 in sel:
            c.append(f"{px(x0)} {py(y0)} m {px(x1)} {py(y1)} l S")
    s, d = _pdf_str(title_text(data))
    dropped |= d
    c.append(f"BT /F1 {5 * k:.2f} Tf {MARGIN * k:.2f} {5 * k:.2f} Td ({s}) Tj ET")
    c.append("Q")
    content = "\n".join(c).encode("ascii")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w * k:.2f} {h * k:.2f}] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".encode(),
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
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
