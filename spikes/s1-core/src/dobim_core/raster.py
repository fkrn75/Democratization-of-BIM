# SPDX-License-Identifier: MPL-2.0
"""시트·뷰의 그림 요소(primitive)를 PNG 로 그린다 (16단계 dobim_render). 순수 파이썬, 추가 의존 없음.

- 입력은 시트가 이미 가진 그림 요소 목록이다(sheet.Sheet.prims: 선·다각형·원·문자). SVG·PDF 와 같은 요소를 그대로 칠하므로 그림이 같다.
- 래스터는 다각형 하나를 칠하는 한 가지 길로 모은다: 선은 굵기만큼의 사각형, 원은 다각형·고리, 문자는 글꼴 윤곽을 편 다각형이다.
  다각형은 세로 4칸 부표본 x 가로 정확 면적으로 안티앨리어싱하고 감김수(nonzero)로 채운다. 부동소수 연산만 쓰며 같은 입력은 같은 PNG 바이트를 낸다.
- 문자는 pdf_font 가 찾은 글꼴(Noto Sans KR 등 OFL)의 글리프 윤곽을 fonttools 로 읽어 채운다(이미 있는 의존). 글꼴을 못 찾으면 글자 칸을 속이 빈 사각형으로 그려 자리를 보인다.
- 라이선스: GPL·AGPL 의존 없음(PyMuPDF 같은 AGPL 래스터라이저를 쓰지 않는다). GUI·디스플레이 패키지도 쓰지 않는다.
- 파일·엔진·시계를 모른다(순수 모듈). 글꼴 파일 읽기만 한다.
"""
from __future__ import annotations

import io
import struct
import zlib
from math import ceil, cos, floor, hypot, radians, sin

from dobim_core import pdf_font as PF

SUB = 4                    # 세로 부표본 수(가로는 정확한 면적)
MIN_STROKE_PX = 1.0        # 이보다 가는 선(실제 굵기가 1화소 미만)은 1화소 굵기로 그리되 농도(알파)를 굵기에 비례해 낮춘다
MIN_ALPHA = 0.3            # 가장 가는 선의 최소 농도(축소해도 가는 선이 완전히 사라지지 않게)
MAX_PX_LIMIT = 4096        # 출력 긴 변의 상한
MIN_PX = 64
MAX_PIXELS = 16_000_000    # 출력 화소 수 상한
DEFAULT_MAX_PX = 1600
FALLBACK_BOX = 0.55        # 글꼴이 없을 때 글자 칸 폭(글자 크기 배)


class RasterError(ValueError):
    """그릴 수 없는 입력(크기·영역 등). 메시지에 고치는 방법이 들어 있다."""


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


# ---------------------------------------------------------------- 글꼴 윤곽
class Glyphs:
    """글꼴의 글자별 윤곽(em 단위, 아래가 +y 인 용지 방향)과 진행폭(em). 글자는 처음 쓸 때 부분집합으로 한 번에 읽어 쌓아 두고(원 곡선 그대로),
    곡선을 펴는 분할 수(nseg)별로 다각선으로 바꿔 캐시한다."""

    _RAW: dict[str, dict[str, tuple[float, list]]] = {}     # 글꼴 경로 -> 글자 -> (진행폭, 윤곽들: 명령 목록)
    _FLAT: dict[tuple[str, int, str], list] = {}

    def __init__(self, path):
        self.path = path
        self.key = str(path)

    def ensure(self, chars: set[str]) -> None:
        store = self._RAW.setdefault(self.key, {})
        todo = sorted(c for c in chars if c not in store and c.isprintable())
        if not todo:
            return
        pf = PF.PdfFont(self.path, self.path.read_bytes(), self.path.stem)
        pf.used.update(todo)
        pf.used.add(" ")
        pf._build()                                   # 글자 부분집합 + 일반 굵기(400) 고정(PDF 와 같은 경로)
        from fontTools.pens.basePen import BasePen
        from fontTools.ttLib import TTFont
        f = TTFont(io.BytesIO(pf.subset_bytes))
        upem = f["head"].unitsPerEm
        gs = f.getGlyphSet()
        cmap = f.getBestCmap()
        hm = f["hmtx"]

        class Rec(BasePen):
            def __init__(self, gset):
                super().__init__(gset)
                self.out: list[list] = []
                self.cur: list = []

            def _moveTo(self, p):
                self.cur = [("m", p)]

            def _lineTo(self, p):
                self.cur.append(("l", p))

            def _qCurveToOne(self, p1, p2):
                self.cur.append(("q", p1, p2))

            def _curveToOne(self, p1, p2, p3):
                self.cur.append(("c", p1, p2, p3))

            def _closePath(self):
                if len(self.cur) >= 3:
                    self.out.append(self.cur)
                self.cur = []

            def _endPath(self):
                self._closePath()

        for c in todo:
            gn = cmap.get(ord(c))
            if gn is None:
                store[c] = (0.5, [])
                continue
            pen = Rec(gs)
            gs[gn].draw(pen)
            store[c] = (hm[gn][0] / upem, [[(op[0],) + tuple((x / upem, -y / upem) for x, y in op[1:]) for op in cont] for cont in pen.out])

    def adv(self, c: str) -> float:
        return self._RAW.get(self.key, {}).get(c, (0.5, []))[0]

    def contours(self, c: str, nseg: int) -> list:
        key = (self.key, nseg, c)
        got = self._FLAT.get(key)
        if got is not None:
            return got
        out = []
        for cont in self._RAW.get(self.key, {}).get(c, (0.5, []))[1]:
            pts = [cont[0][1]]
            for op in cont[1:]:
                p0 = pts[-1]
                if op[0] == "l":
                    pts.append(op[1])
                elif op[0] == "q":
                    p1, p2 = op[1], op[2]
                    for i in range(1, nseg + 1):
                        t = i / nseg
                        a, b, d = (1 - t) * (1 - t), 2 * (1 - t) * t, t * t
                        pts.append((a * p0[0] + b * p1[0] + d * p2[0], a * p0[1] + b * p1[1] + d * p2[1]))
                else:
                    p1, p2, p3 = op[1], op[2], op[3]
                    for i in range(1, nseg + 1):
                        t = i / nseg
                        a, b, d, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
                        pts.append((a * p0[0] + b * p1[0] + d * p2[0] + e * p3[0], a * p0[1] + b * p1[1] + d * p2[1] + e * p3[1]))
            out.append(pts)
        self._FLAT[key] = out
        return out


# ---------------------------------------------------------------- 캔버스
class Canvas:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.buf = bytearray(b"\xff" * (w * h * 3))

    def fill(self, contours, color: tuple[int, int, int], alpha: float = 1.0) -> None:
        """닫힌 다각선들을 감김수(nonzero) 규칙으로 칠한다. 좌표는 화소(왼쪽 위 0,0). alpha 는 전체 농도(1 이면 불투명)."""
        w, h = self.w, self.h
        y_lo = min(p[1] for c in contours for p in c)
        y_hi = max(p[1] for c in contours for p in c)
        x_lo = min(p[0] for c in contours for p in c)
        x_hi = max(p[0] for c in contours for p in c)
        if y_hi <= 0 or x_hi <= 0 or y_lo >= h or x_lo >= w:
            return
        y0, y1 = max(0, floor(y_lo)), min(h - 1, ceil(y_hi))
        x0, x1 = max(0, floor(x_lo)), min(w - 1, ceil(x_hi))
        nrows = y1 - y0 + 1
        nsub = nrows * SUB
        cross: list[list] = [[] for _ in range(nsub)]
        for c in contours:
            n = len(c)
            for i in range(n):
                xa, ya = c[i]
                xb, yb = c[(i + 1) % n]
                if ya == yb:
                    continue
                d = 1
                if ya > yb:
                    xa, ya, xb, yb = xb, yb, xa, ya
                    d = -1
                j0 = max(ceil((ya - y0) * SUB - 0.5), 0)
                j1 = min(ceil((yb - y0) * SUB - 0.5), nsub)
                if j0 >= j1:
                    continue
                slope = (xb - xa) / (yb - ya)
                base = y0 + 0.5 / SUB
                for j in range(j0, j1):
                    cross[j].append((xa + (base + j / SUB - ya) * slope, d))
        q = 1.0 / SUB
        nx = x1 - x0 + 2
        for r in range(nrows):
            cov = None
            for s in range(SUB):
                cr = cross[r * SUB + s]
                if not cr:
                    continue
                if cov is None:
                    cov, run, touch = [0.0] * nx, [0.0] * nx, set()
                cr.sort()
                wind, xs = 0, 0.0
                for x, d in cr:
                    prev = wind
                    wind += d
                    if prev == 0 and wind != 0:
                        xs = x
                    elif prev != 0 and wind == 0:
                        a, b = max(xs, x0), min(x, x1 + 1)
                        if b <= a:
                            continue
                        ia, ib = int(a), int(b)
                        if ia == ib:
                            cov[ia - x0] += (b - a) * q
                            touch.add(ia - x0)
                        else:
                            cov[ia - x0] += (ia + 1 - a) * q
                            cov[ib - x0] += (b - ib) * q
                            run[ia + 1 - x0] += q
                            run[ib - x0] -= q
                            touch.update((ia - x0, ib - x0, ia + 1 - x0))
            if cov is None:
                continue
            self._row(y0 + r, x0, cov, run, touch, color, alpha)

    def _row(self, y, x0, cov, run, touch, color, alpha=1.0) -> None:
        base = y * self.w * 3
        pos, rs = 0, 0.0
        for i in sorted(touch):
            if i > pos and rs > 0.002:
                self._seg(base, x0 + pos, x0 + i, rs * alpha, color)
            rs += run[i]
            t = cov[i] + rs
            if t > 0.002:
                self._seg(base, x0 + i, x0 + i + 1, t * alpha, color)
            pos = i + 1

    def _seg(self, base, xa, xb, c, color) -> None:
        w = self.w
        if xb > w:
            xb = w
        if xb <= xa:
            return
        buf = self.buf
        if c >= 0.998:
            buf[base + xa * 3:base + xb * 3] = bytes(color) * (xb - xa)
            return
        c256 = int(c * 256 + 0.5)
        r, g, b = color
        o = base + xa * 3
        for _ in range(xb - xa):
            d = buf[o]
            buf[o] = d + (((r - d) * c256 + 128) >> 8)
            d = buf[o + 1]
            buf[o + 1] = d + (((g - d) * c256 + 128) >> 8)
            d = buf[o + 2]
            buf[o + 2] = d + (((b - d) * c256 + 128) >> 8)
            o += 3

    def png(self) -> bytes:
        w, h = self.w, self.h
        stride = w * 3
        raw = bytearray()
        buf = self.buf
        for y in range(h):
            raw.append(0)
            raw += buf[y * stride:(y + 1) * stride]

        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- 그림 요소 -> 다각형
def _quad(p0, p1, half, ext):
    """선분 p0->p1 을 굵기(half = 반 굵기)만큼 편 사각형(양 끝 ext 만큼 연장). 길이 0 이면 None."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    ln = hypot(dx, dy)
    if ln < 1e-9:
        return None
    ux, uy = dx / ln, dy / ln
    nx, ny = -uy * half, ux * half
    ax, ay = p0[0] - ux * ext, p0[1] - uy * ext
    bx, by = p1[0] + ux * ext, p1[1] + uy * ext
    return [(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)]


def _dashes(x0, y0, x1, y1, on, off):
    """선분을 (마디, 간격) 길이대로 나눈 조각들(좌표 단위 그대로)."""
    ln = hypot(x1 - x0, y1 - y0)
    if ln < 1e-9 or on <= 0:
        return [((x0, y0), (x1, y1))]
    out, t = [], 0.0
    ux, uy = (x1 - x0) / ln, (y1 - y0) / ln
    while t < ln:
        e = min(t + on, ln)
        out.append(((x0 + ux * t, y0 + uy * t), (x0 + ux * e, y0 + uy * e)))
        t = e + off
    return out


def _circle_pts(cx, cy, r, n, ccw=True):
    from math import pi
    pts = [(cx + r * cos(2 * pi * i / n), cy + r * sin(2 * pi * i / n)) for i in range(n)]
    return pts if ccw else pts[::-1]


def pen_px(w_mm: float, k: float) -> tuple[float, float]:
    """펜 굵기(용지 mm)와 배율(화소/mm)로 (그릴 굵기 화소, 농도). 1화소 이상이면 실제 굵기 그대로·불투명.
    1화소 미만이면 굵기는 1화소로 두고 농도를 굵기에 비례해 낮춘다(최소 MIN_ALPHA): 0.13mm 가는 선과 0.7mm 굵은 선이 축소해도 위계로 보인다."""
    t = w_mm * k
    if t >= MIN_STROKE_PX:
        return t, 1.0
    return MIN_STROKE_PX, max(MIN_ALPHA, t / MIN_STROKE_PX)


def _nseg_for(px_size: float) -> int:
    return 2 if px_size < 14 else (4 if px_size < 60 else 8)


class _Page:
    """시트 한 장(용지 mm)을 캔버스에 칠하는 도우미. 영역(crop)과 배율을 받아 용지 mm -> 화소로 바꾼다."""

    def __init__(self, canvas: Canvas, k: float, ox: float, oy: float, glyphs: Glyphs | None):
        self.cv, self.k, self.ox, self.oy, self.gl = canvas, k, ox, oy, glyphs
        self.fallback_text = 0

    def P(self, x, y):
        return ((x - self.ox) * self.k, (y - self.oy) * self.k)

    def visible(self, x0, y0, x1, y1) -> bool:
        a, b = self.P(x0, y0), self.P(x1, y1)
        return not (max(a[0], b[0]) < 0 or max(a[1], b[1]) < 0 or min(a[0], b[0]) > self.cv.w or min(a[1], b[1]) > self.cv.h)

    def line(self, x0, y0, x1, y1, w, color, dash):
        wpx, alpha = pen_px(w, self.k)
        half = wpx / 2.0
        if not self.visible(min(x0, x1) - w, min(y0, y1) - w, max(x0, x1) + w, max(y0, y1) + w):
            return
        rgb = _rgb(color)
        if dash:
            quads = []
            for a, b in _dashes(x0, y0, x1, y1, dash[0], dash[1]):
                q = _quad(self.P(*a), self.P(*b), half, 0.0)
                if q:
                    quads.append(q)
            if quads:
                self.cv.fill(quads, rgb, alpha)
            return
        q = _quad(self.P(x0, y0), self.P(x1, y1), half, half)
        if q is None:   # 길이 0 의 선(점): 굵기만큼의 점
            px, py = self.P(x0, y0)
            q = [(px - half, py - half), (px + half, py - half), (px + half, py + half), (px - half, py + half)]
        self.cv.fill([q], rgb, alpha)

    def poly(self, pts, fill, w, color):
        if len(pts) < 2:
            return
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        pad = max(w, 0.0)
        if not self.visible(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad):
            return
        ppts = [self.P(x, y) for x, y in pts]
        if fill:
            self.cv.fill([ppts], _rgb(fill))
        if w:
            wpx, alpha = pen_px(w, self.k)
            half = wpx / 2.0
            quads = []
            n = len(ppts)
            for i in range(n):
                q = _quad(ppts[i], ppts[(i + 1) % n], half, half)
                if q:
                    quads.append(q)
            if quads:
                self.cv.fill(quads, _rgb(color), alpha)

    def circle(self, cx, cy, r, w, color, fill):
        if not self.visible(cx - r - w, cy - r - w, cx + r + w, cy + r + w):
            return
        px, py = self.P(cx, cy)
        rp = r * self.k
        n = max(12, min(96, int(rp * 3)))
        if fill:
            self.cv.fill([_circle_pts(px, py, rp, n)], _rgb(fill))
        if w:
            wpx, alpha = pen_px(w, self.k)
            half = wpx / 2.0
            self.cv.fill([_circle_pts(px, py, rp + half, n), _circle_pts(px, py, max(rp - half, 0.05), n, ccw=False)], _rgb(color), alpha)

    def text(self, x, y, size, s, anchor, color, angle, bold):
        px_size = size * self.k
        if px_size < 0.8 or not s:
            return
        a = radians(angle)
        ca, sa = cos(a), sin(a)
        rgb = _rgb(color)
        if self.gl is None:   # 글꼴이 없으면 글자 칸만 속이 빈 사각형으로(자리 표시)
            width = len(s) * FALLBACK_BOX * size
            ox = -width / 2.0 if anchor == "middle" else -width if anchor == "end" else 0.0
            for i, ch in enumerate(s):
                if ch == " ":
                    continue
                x0, x1 = ox + i * FALLBACK_BOX * size + 0.08 * size, ox + (i + 1) * FALLBACK_BOX * size - 0.08 * size
                corners = [(x0, 0.0), (x1, 0.0), (x1, -0.7 * size), (x0, -0.7 * size)]
                pts = [self.P(x + lx * ca - ly * sa, y + lx * sa + ly * ca) for lx, ly in corners]
                self.cv.fill([q for q in (_quad(pts[i2], pts[(i2 + 1) % 4], 0.4, 0.4) for i2 in range(4)) if q], rgb)
            self.fallback_text += 1
            return
        nseg = _nseg_for(px_size)
        gl = self.gl
        adv = [gl.adv(c) for c in s]
        width = sum(adv) * size
        ox = -width / 2.0 if anchor == "middle" else -width if anchor == "end" else 0.0
        if not self.visible(x + min(ox, 0) - size, y - 2 * size, x + max(ox + width, 0) + size, y + size) and abs(angle) < 1e-9:
            return
        k = self.k
        for off in ((0.0, 0.03 * size) if bold else (0.0,)):
            pen = ox
            for c, a_ in zip(s, adv):
                contours = gl.contours(c, nseg)
                if contours:
                    polys = []
                    for cont in contours:
                        pp = []
                        for gx, gy in cont:
                            lx, ly = pen + off + gx * size, gy * size
                            pp.append(((x + lx * ca - ly * sa - self.ox) * k, (y + lx * sa + ly * ca - self.oy) * k))
                        polys.append(pp)
                    self.cv.fill(polys, rgb)
                pen += a_ * size


def plan_size(page_w: float, page_h: float, max_px: int, crop) -> tuple[int, int, float, float, float]:
    """출력 화소 크기와 배율. crop 은 용지 비율 [x0, y0, x1, y1](0~1). 반환: (너비, 높이, 화소/mm, 영역 왼쪽 mm, 영역 위쪽 mm)"""
    if not (MIN_PX <= max_px <= MAX_PX_LIMIT):
        raise RasterError(f"max_px 는 {MIN_PX}~{MAX_PX_LIMIT} 이어야 한다: {max_px}")
    fx0, fy0, fx1, fy1 = (0.0, 0.0, 1.0, 1.0) if crop is None else crop
    if not (0.0 <= fx0 < fx1 <= 1.0 and 0.0 <= fy0 < fy1 <= 1.0):
        raise RasterError("crop 은 용지 비율 [x0, y0, x1, y1] (0 <= x0 < x1 <= 1, 0 <= y0 < y1 <= 1) 이어야 한다")
    rw, rh = (fx1 - fx0) * page_w, (fy1 - fy0) * page_h
    k = max_px / max(rw, rh)
    w, h = max(1, round(rw * k)), max(1, round(rh * k))
    if w * h > MAX_PIXELS:
        raise RasterError(f"출력이 너무 크다({w}x{h}): max_px 를 줄이거나 crop 으로 영역을 좁히세요")
    return w, h, k, fx0 * page_w, fy0 * page_h


def render(prims, page_w: float, page_h: float, *, max_px: int = DEFAULT_MAX_PX, crop=None) -> dict:
    """그림 요소를 PNG 로. 반환: {png, width, height, px_per_mm, region_mm, font, text_boxes}. 글자 크기가 화소로 1 미만이면 그리지 않는다."""
    w, h, k, ox, oy = plan_size(page_w, page_h, max_px, crop)
    path = PF.find_font_path()
    gl = None
    if path is not None:
        gl = Glyphs(path)
        chars = {c for p in prims if p[0] == "text" for c in p[4]}
        try:
            gl.ensure(chars)
        except Exception:   # 글꼴을 못 읽으면 자리 표시로 내려간다
            gl = None
    cv = Canvas(w, h)
    pg = _Page(cv, k, ox, oy, gl)
    for p in prims:
        kind = p[0]
        if kind == "line":
            _, x0, y0, x1, y1, lw, col, dash = p
            pg.line(x0, y0, x1, y1, lw, col, dash)
        elif kind == "poly":
            _, pts, fill, lw, col = p
            pg.poly(pts, fill, lw, col)
        elif kind == "circle":
            _, cx, cy, r, lw, col, fill = p
            pg.circle(cx, cy, r, lw, col, fill)
        elif kind == "text":
            _, x, y, size, s, anchor, col, ang, bold = p
            pg.text(x, y, size, s, anchor, col, ang, bold)
    return {"png": cv.png(), "width": w, "height": h, "px_per_mm": k, "region_mm": [ox, oy, ox + w / k, oy + h / k],
            "font": gl.path.name if gl is not None else None, "text_boxes": pg.fallback_text}
