# SPDX-License-Identifier: MPL-2.0
"""시트 보조 요소(순수 그리기): 표, 범례, 개정 구름, 키 플랜, 통심선·그리드 기호, 단면·입면 기호, 부위 상세 표시, 부호 태그.

sheet.py 의 Sheet 에 그림 요소를 더하는 함수 모음이다(파일·시계·모델을 모른다). 입력은 호출자가 모델에서 뽑아 넘긴 값뿐이다.
모든 함수는 그룹(sh.begin)으로 감싸 SVG 에서 부분을 찾을 수 있게 한다(시험·판정이 쓴다). 기능을 쓰지 않으면 아무것도 그리지 않는다.
"""
from __future__ import annotations

from math import atan2, cos, degrees, pi, radians, sin, sqrt

from dobim_core import drawing_export as DX

INK = "#000000"
GRAY = "#555555"
RED = "#c62828"


def _est(s: str, size: float) -> float:
    return sum(size * (1.0 if ord(c) >= 0x2E80 else 0.58) for c in s)


def clip_text(s: str, size: float, maxw: float) -> str:
    """폭에 맞게 자르고 …를 붙인다."""
    if _est(s, size) <= maxw:
        return s
    out = ""
    for c in s:
        if _est(out + c + "…", size) > maxw:
            break
        out += c
    return out + "…"


def dashdot(sh, x0, y0, x1, y1, w=0.25, color=INK):
    """1점쇄선: 긴 선·점을 번갈아 개별 선분으로 그린다(PDF·SVG 모두 같은 그림)."""
    dx, dy = x1 - x0, y1 - y0
    length = sqrt(dx * dx + dy * dy)
    if length < 1e-6:
        return 0
    ux, uy = dx / length, dy / length
    pat = (6.0, 1.2, 0.6, 1.2)
    t, k, n = 0.0, 0, 0
    while t < length - 1e-9:
        seg = min(pat[k % 4], length - t)
        if k % 2 == 0:
            sh.line(x0 + ux * t, y0 + uy * t, x0 + ux * (t + seg), y0 + uy * (t + seg), w, color)
            n += 1
        t += seg
        k += 1
    return n


# ---------------------------------------------------------------- 표
def draw_table(sh, x, y, title: str, cols: list[tuple[str, float]], rows: list[list[str]], gid: str, size: float = 2.6, rh: float = 5.6) -> float:
    """표 한 개. 반환: 차지한 높이(mm). 머리글 줄은 옅은 회색 채움."""
    tw = sum(c[1] for c in cols)
    sh.begin(gid, **{"data-rows": len(rows)})
    sh.text(x, y + 3.6, 3.6, title, bold=True)
    y += 6.0
    sh.rect(x, y, tw, rh, 0.25, INK, "#e8e8e8")
    cx = x
    for head, cw in cols:
        sh.text(cx + 1.2, y + rh - 1.6, size, clip_text(head, size, cw - 2.0), bold=True)
        cx += cw
    for i, r in enumerate(rows):
        yy = y + rh * (i + 1)
        sh.rect(x, yy, tw, rh, 0.18)
        cx = x
        for (head, cw), v in zip(cols, r):
            sh.text(cx + 1.2, yy + rh - 1.6, size, clip_text(str(v), size, cw - 2.0))
            cx += cw
    cx = x
    for head, cw in cols[:-1]:
        cx += cw
        sh.line(cx, y, cx, y + rh * (len(rows) + 1), 0.18)
    sh.end()
    return 6.0 + rh * (len(rows) + 1) + 6.0


# ---------------------------------------------------------------- 범례
LEGEND_H = 6.0 + 6.0 * (3 + 1 + 1 + 1 + 1 + 4 + 1 + 5) + 6.0     # 범례 블록 높이(행 수 x 6mm)


def legend_block(sh, x, y, style: dict) -> float:
    """선 종류·해치·기호 범례. style: 선 클래스 -> (굵기, 색). 실제 도면이 쓰는 선 굵기 체계와 같은 값을 쓴다."""
    sh.begin("legend", **{"data-line-types": "solid,dashed,dashdot", "data-hatch": "45deg"})
    sh.text(x, y + 3.6, 3.6, "범례", bold=True)
    y0 = y
    y += 6.0
    rows = (("cut", "절단선(벽·기둥·슬래브·계단의 잘린 단면)"), ("proj", "보이는 선(투영·입면 윤곽)"), ("symbol", "기호선(창·문·계단·방화벽 기호)"))
    w = 0
    for cls, desc in rows:
        wd, col = style[cls]
        sh.line(x + 1.0, y + 3.0, x + 21.0, y + 3.0, wd, col)
        sh.text(x + 25.0, y + 4.0, 2.6, f"{desc}  {wd:g}mm")
        y += 6.0
        w += 1
    sh.rect(x + 1.0, y + 0.6, 20.0, 4.8, 0.13, GRAY)                                  # 해치 견본: 45도 가는선
    for k in range(-5, 21):
        c = 1.0 + 0.6 + k * 1.6
        xa, xb = max(1.0, c - 5.4), min(21.0, c)
        if xa < xb:
            sh.line(x + xa, y + c - xa, x + xb, y + c - xb, style["hatch"][0], style["hatch"][1])
    sh.text(x + 25.0, y + 4.0, 2.6, f"해치: 45도 가는선(외벽 마감 재료)  {style['hatch'][0]:g}mm")
    y += 6.0
    sh.line(x + 1.0, y + 3.0, x + 21.0, y + 3.0, 0.25, "#333333", (4.0, 1.2))
    sh.text(x + 25.0, y + 4.0, 2.6, "파선: 방화벽 중심선, 레벨 기준선")
    y += 6.0
    dashdot(sh, x + 1.0, y + 3.0, x + 21.0, y + 3.0, 0.25)
    sh.text(x + 25.0, y + 4.0, 2.6, "1점쇄선: 통심선(기준선), 단면 절단선, 부위 상세 표시")
    y += 6.0
    sh.line(x + 1.0, y + 3.0, x + 21.0, y + 3.0, 0.13, DX.ANNOT_COLOR)
    sh.text(x + 25.0, y + 4.0, 2.6, "치수선·보조선 0.13mm(파랑, 눈금 45도)")
    y += 6.0
    for sym, desc in (("D1", "D: 문 부호(일람표 참조)"), ("W1", "W: 창 부호(일람표 참조)"), ("WT1", "WT: 벽체 부호(벽체 일람표 참조)"), ("101", "실번호(좌상단부터 시계방향)")):
        sh.circle(x + 11.0, y + 3.0, 2.4, 0.25)
        sh.text(x + 11.0, y + 3.9, 2.0, sym, "middle")
        sh.text(x + 25.0, y + 4.0, 2.6, desc)
        y += 6.0
    sh.circle(x + 11.0, y + 3.0, 2.4, 0.25)
    sh.text(x + 11.0, y + 3.9, 2.0, "A", "middle")
    sh.text(x + 25.0, y + 4.0, 2.6, "통심선 기호(원 + 번호)")
    y += 6.0
    for ab, desc in (("FW", "방화벽(등급 병기)"), ("FD", "방화문(등급 병기)"), ("EI", "내화 성능(차염·단열) 등급"), ("FL", "마감 바닥 레벨"), ("GL", "지반 레벨")):
        sh.text(x + 11.0, y + 4.0, 2.8, ab, "middle", bold=True)
        sh.text(x + 25.0, y + 4.0, 2.6, f"약어: {desc}")
        y += 6.0
    sh.end()
    return y - y0 + 6.0


def notes_block(sh, x, y, lines: list[str], maxw: float) -> float:
    """주기(노트) 블록. 줄은 번호를 붙여 적는다."""
    sh.begin("notes", **{"data-lines": len(lines)})
    sh.text(x, y + 3.6, 3.6, "주기", bold=True)
    yy = y + 6.0
    for i, s in enumerate(lines, 1):
        sh.text(x + 1.0, yy + 4.0, 2.6, clip_text(f"{i}. {s}", 2.6, maxw))
        yy += 5.2
    sh.end()
    return yy - y + 4.0


# ---------------------------------------------------------------- 키 플랜
def hatch_rect(sh, x0, y0, x1, y1, step=1.3, w=0.13, color=INK):
    """직사각형 안을 45도 가는선으로 채운다(용지 좌표). 만든 선 개수를 돌려준다."""
    n = 0
    c = x0 + y0
    while c <= x1 + y1:
        xa, xb = max(x0, c - y1), min(x1, c - y0)
        if xa < xb:
            sh.line(xa, c - xa, xb, c - xb, w, color)
            n += 1
        c += step
    return n


def key_plan(sh, x, y, w, h, data: dict, zone):
    """키 플랜: 평면 뷰의 선을 칸 안에 맞춰 줄여 그리고, zone(모델 mm [x0,y0,x1,y1], None 이면 전체)을 해칭한다."""
    lines = [ln for ln in data["lines"] if ln[0] in ("cut", "proj")]
    pts = [(ln[1], ln[2]) for ln in lines] + [(ln[3], ln[4]) for ln in lines]
    sh.begin("key-plan", **{"data-view": data.get("name", ""), "data-zone": "all" if zone is None else ",".join(f"{v:g}" for v in zone)})
    if not pts:
        sh.end()
        return 0
    bx0, bx1 = min(p[0] for p in pts), max(p[0] for p in pts)
    by0, by1 = min(p[1] for p in pts), max(p[1] for p in pts)
    pad = 4.0
    s = min((w - 2 * pad) / max(bx1 - bx0, 1.0), (h - 2 * pad) / max(by1 - by0, 1.0))
    ox = x + (w - (bx1 - bx0) * s) / 2.0
    oy = y + (h - (by1 - by0) * s) / 2.0
    X = lambda v: ox + (v - bx0) * s            # noqa: E731
    Y = lambda v: oy + (by1 - v) * s            # noqa: E731
    z = zone if zone is not None else (bx0, by0, bx1, by1)
    zx0, zx1 = X(min(z[0], z[2])), X(max(z[0], z[2]))
    zy0, zy1 = Y(max(z[1], z[3])), Y(min(z[1], z[3]))
    n = hatch_rect(sh, zx0, zy0, zx1, zy1, 1.4, 0.13, RED)
    for _, a, b, c, d in ((ln[0], ln[1], ln[2], ln[3], ln[4]) for ln in lines):
        sh.line(X(a), Y(b), X(c), Y(d), 0.13, GRAY)
    sh.rect(zx0, zy0, zx1 - zx0, zy1 - zy0, 0.25, RED)
    sh.end()
    return n


# ---------------------------------------------------------------- 개정 구름
def cloud(sh, x0, y0, x1, y1, color=RED, bump=7.0):
    """사각형 둘레를 따라 바깥으로 볼록한 호를 이어 구름 표시를 닫힌 선 하나로 그린다."""
    pts = []
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = sqrt(dx * dx + dy * dy)
        ux, uy = dx / length, dy / length
        nx, ny = uy, -ux
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        if (mx - cx) * nx + (my - cy) * ny < 0:
            nx, ny = -nx, -ny
        n = max(1, int(length / bump + 0.5))
        step = length / n
        r = step / 2.0
        for j in range(n):
            mid = (j + 0.5) * step
            for k in range(0, 10):
                t = pi * k / 9.0
                along = mid - r * cos(t)
                out = r * 0.9 * sin(t)
                pts.append((a[0] + ux * along + nx * out, a[1] + uy * along + ny * out))
    sh.poly(pts, None, 0.35, color)
    return len(pts)


def triangle_tag(sh, x, y, label: str, color=RED):
    """개정 번호 표시: 삼각형 안에 번호."""
    sh.poly([(x, y - 3.2), (x + 3.0, y + 2.2), (x - 3.0, y + 2.2)], None, 0.3, color)
    sh.text(x, y + 1.6, 2.0, label, "middle", color)


# ---------------------------------------------------------------- 뷰 위 기호
def bubble(sh, cx, cy, label: str, r=3.3, color=INK, size=2.3):
    k = getattr(sh, "sym_k", 1.0)          # 기호 크기 배율(작성기준 프로파일). 1 이면 값이 그대로다
    r, size = r * k, size * k
    sh.circle(cx, cy, r, 0.3, color)
    sh.text(cx, cy + size * 0.35, size, label, "middle", color)


REF_R = 4.4   # 뷰 참조 원 반지름(용지 mm, 기호 배율 1)


def ref_bubble(sh, cx, cy, number: str, sheet: str, color=INK):
    """뷰 참조 기호 머리(W1 1-C 2차, VIEW-25): 원을 가로선으로 나눠 위에 상세 번호, 아래에 시트 번호(Revit 의 단면 머리와 같은 뜻)."""
    k = getattr(sh, "sym_k", 1.0)
    r = REF_R * k
    sh.circle(cx, cy, r, 0.3, color, "#ffffff")
    sh.line(cx - r, cy, cx + r, cy, 0.25, color)
    ns = (1.9 if len(number) <= 3 else 1.4) * k
    ss = (1.5 if len(sheet) <= 5 else (1.15 if len(sheet) <= 7 else 0.9)) * k
    sh.text(cx, cy - 0.9 * k, ns, number, "middle", color)
    sh.text(cx, cy + ss + 0.7 * k, ss, sheet, "middle", color)


def projection_symbol(sh, x, y, w, h, method="third"):
    """투상법 기호(KS 제3각법·제1각법): 원뿔대를 정면에서 본 모양(사다리꼴 + 중심선)과 옆(원 둘)에서 본 모양을 나란히 놓는다.
    제3각법은 정면도가 왼쪽·우측면도(동심원)가 오른쪽, 제1각법은 그 반대다. (x, y)는 기호 칸의 왼쪽 위, 크기 w x h(용지 mm)."""
    sh.begin("projection-symbol", **{"data-method": "third-angle" if method == "third" else "first-angle"})
    cy = y + h / 2.0
    fw, circ = w * 0.36, h * 0.46
    fx = x + (0.0 if method == "third" else w - fw)
    cx = x + (w - circ) if method == "third" else x + circ
    top, bot = h * 0.22, h * 0.50           # 사다리꼴 위·아래 반폭
    sh.poly([(fx, cy - top), (fx + fw, cy - bot), (fx + fw, cy + bot), (fx, cy + top)], None, 0.25)
    dashdot(sh, fx - 1.5, cy, fx + fw + 1.5, cy, 0.18)
    sh.circle(cx, cy, circ, 0.25)
    sh.circle(cx, cy, circ * 0.5, 0.25)
    dashdot(sh, cx - circ - 1.5, cy, cx + circ + 1.5, cy, 0.18)
    sh.end()


def dim_run(sh, ax, ay, bx, by, off, text, h=2.5, color=None):
    """치수 한 개(용지 mm 좌표): 보조선·치수선·45도 눈금·치수 문자. off 는 a->b 방향 기준 (-uy, ux) 쪽으로 치수선이 떨어지는 거리(용지 mm)다."""
    color = color or DX.ANNOT_COLOR
    dx, dy = bx - ax, by - ay
    ln = sqrt(dx * dx + dy * dy)
    if ln < 1e-6:
        return
    ux, uy = dx / ln, dy / ln
    nx, ny = -uy * off, ux * off
    ext = 1.5 if off >= 0 else -1.5
    ex, ey = -uy * ext, ux * ext
    w_dim, w_ext = getattr(sh, "dim_w", (0.13, 0.13))     # 치수선·보조선 굵기(작성기준 프로파일의 선 굵기 위계). 기본은 둘 다 0.13
    for px, py in ((ax, ay), (bx, by)):
        sh.line(px, py, px + nx + ex, py + ny + ey, w_ext, color)
    sh.line(ax + nx, ay + ny, bx + nx, by + ny, w_dim, color)
    kx, ky = (ux - uy) * 0.64, (uy + ux) * 0.64
    for px, py in ((ax + nx, ay + ny), (bx + nx, by + ny)):
        sh.line(px - kx, py - ky, px + kx, py + ky, 0.25, color)
    ang = degrees(atan2(uy, ux))
    ang = ang - 180.0 if ang > 90.0 else (ang + 180.0 if ang <= -90.0 else ang)
    a_ = radians(ang)
    sh.text((ax + bx) / 2.0 + nx + 0.8 * sin(a_), (ay + by) / 2.0 + ny - 0.8 * cos(a_), h, text, "middle", color, ang)


def view_dims(sh, vtype, dims, data, TX, TY, fmt, h):
    """시트 치수(E-04 입면 치수 사슬, L-09 복도·계단 폭). dims 는 모델에서 뽑은 값(sheet_model), fmt 는 치수 문자 형식 함수."""
    if not dims:
        return
    if vtype == "elevation":
        e = dims.get("elevation")
        bb = view_bounds(data)
        if not e or bb is None:
            return
        n = len(e["chain"]) + 1
        sh.begin("elevation-dims", **{"data-count": n})
        y = TY(bb[1])
        for a, b, t in e["chain"]:
            dim_run(sh, TX(a), y, TX(b), y, ELEV_DIM_OFF[0], fmt(t), h)
        a, b, t = e["overall"]
        dim_run(sh, TX(a), y, TX(b), y, ELEV_DIM_OFF[1], fmt(t), h)
        sh.end()
        return
    for key, gid in (("corridor", "corridor-dims"), ("stair", "stair-dims")):
        items = dims.get(key)
        if not items:
            continue
        sh.begin(gid, **{"data-count": len(items)})
        for x0, y0, x1, y1, t in items:
            dim_run(sh, TX(x0), TY(y0), TX(x1), TY(y1), PLAN_DIM_OFF, fmt(t), h)
        sh.end()


ELEV_DIM_OFF = (7.0, 12.0)     # 입면 치수 사슬·전체 폭의 치수선이 건물 아래로 떨어지는 거리(용지 mm)
PLAN_DIM_OFF = 3.0             # 평면 폭 치수선이 잰 선에서 떨어지는 거리(용지 mm)
ELEV_DIM_SPACE = 14.0          # 입면 치수가 뷰 아래에 필요로 하는 여유(용지 mm: 뷰 배치가 이만큼 더 비운다)


def view_bounds(data):
    pts = [(ln[1], ln[2]) for ln in data["lines"]] + [(ln[3], ln[4]) for ln in data["lines"]]
    for poly in data["fills"]:
        pts += list(poly)
    if not pts:
        return None
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def grid_lines(sh, data, TX, TY, grids):
    """통심선(1점쇄선)과 그리드 기호(원 + 번호). 평면 뷰 범위 밖으로 5mm 나가 끝에 원을 둔다."""
    bb = view_bounds(data)
    if bb is None or not grids:
        return
    sh.begin("grid-lines", **{"data-count": len(grids)})
    k = getattr(sh, "sym_k", 1.0)
    ext = 5.0
    for g in grids:
        lab, pos = g["label"], g["pos"]
        if g["axis"] == "x":
            xa = TX(pos)
            ya, yb = TY(bb[3]) - ext, TY(bb[1]) + ext
            dashdot(sh, xa, ya, xa, yb)
            bubble(sh, xa, ya - 3.3 * k, lab)
            bubble(sh, xa, yb + 3.3 * k, lab)
        else:
            ya = TY(pos)
            xa, xb = TX(bb[0]) - ext, TX(bb[2]) + ext
            dashdot(sh, xa, ya, xb, ya)
            bubble(sh, xa - 3.3 * k, ya, lab)
            bubble(sh, xb + 3.3 * k, ya, lab)
    sh.end()


def section_marks(sh, data, TX, TY, sections, elevs):
    """평면 뷰에 단면 절단선 기호(양 끝 원 + 보는 방향 화살표)와 입면 방향 기호(원 + 삼각형)를 그린다."""
    bb = view_bounds(data)
    if bb is None:
        return
    if sections:
        sh.begin("section-markers", **{"data-count": len(sections)})
        for s in sections:
            (x1, y1), (x2, y2) = s["p1"], s["p2"]
            a, b = (TX(x1), TY(y1)), (TX(x2), TY(y2))
            dashdot(sh, a[0], a[1], b[0], b[1], 0.35)
            fx, fy = s["dir"]
            ux, uy = fx, -fy                                              # 용지 좌표(y 아래)로 옮긴 보는 방향
            for (px, py), sgn in ((a, -1.0), (b, 1.0)):
                ex, ey = b[0] - a[0], b[1] - a[1]
                el = sqrt(ex * ex + ey * ey) or 1.0
                if s.get("ref"):   # 뷰 참조(W1 1-C 2차, VIEW-25): 상세 번호 / 시트 번호
                    cx, cy = px + sgn * ex / el * REF_R, py + sgn * ey / el * REF_R
                    ref_bubble(sh, cx, cy, *s["ref"])
                else:
                    cx, cy = px + sgn * ex / el * 3.3, py + sgn * ey / el * 3.3
                    bubble(sh, cx, cy, s["label"], 3.3, INK, 1.9)
                tip = (px + ux * 6.0, py + uy * 6.0)
                sh.poly([tip, (px + ux * 2.4 - uy * 1.4, py + uy * 2.4 + ux * 1.4), (px + ux * 2.4 + uy * 1.4, py + uy * 2.4 - ux * 1.4)], INK, 0.0)
        sh.end()
    if elevs:
        sh.begin("elevation-markers", **{"data-count": len(elevs)})
        cx, cy = (TX(bb[0]) + TX(bb[2])) / 2.0, (TY(bb[1]) + TY(bb[3])) / 2.0
        for e in elevs:
            dx, dy = e["dir"]                                             # 건물을 바라보는 방향(모델 좌표)
            px, py = cx - dx * ((TX(bb[2]) - TX(bb[0])) / 2.0 + 9.0), cy + dy * ((TY(bb[1]) - TY(bb[3])) / 2.0 + 9.0)
            ux, uy = dx, -dy
            if e.get("ref"):   # 뷰 참조(W1 1-C 2차): 번호 / 시트 번호 원 + 바깥으로 보는 방향 삼각형
                ref_bubble(sh, px, py, *e["ref"])
                r = REF_R * getattr(sh, "sym_k", 1.0)
                sh.poly([(px + ux * (r + 2.4), py + uy * (r + 2.4)), (px - uy * 1.8 + ux * (r - 0.4), py + ux * 1.8 + uy * (r - 0.4)),
                         (px + uy * 1.8 + ux * (r - 0.4), py - ux * 1.8 + uy * (r - 0.4))], INK, 0.0)
                sh.text(px, py - r - 1.4, 2.0, e["label"], "middle")
                continue
            sh.circle(px, py, 3.3, 0.3)
            sh.poly([(px + ux * 3.0, py + uy * 3.0), (px - uy * 1.8 - ux * 1.0, py + ux * 1.8 - uy * 1.0), (px + uy * 1.8 - ux * 1.0, py - ux * 1.8 - uy * 1.0)], INK, 0.0)
            sh.text(px, py - 4.6, 2.0, e["label"], "middle")
        sh.end()


def callouts(sh, view_id, TX, TY, items):
    """부위 상세 표시: 1점쇄선 사각형과 부호 원(오른쪽 위). 상세 도면은 같은 부호로 연결한다."""
    mine = [c for c in items if c["view"] == view_id]
    if not mine:
        return
    sh.begin("detail-callouts", **{"data-count": len(mine)})
    for c in mine:
        x0, y0, x1, y1 = c["rect"]
        xa, xb = TX(min(x0, x1)), TX(max(x0, x1))
        ya, yb = TY(max(y0, y1)), TY(min(y0, y1))
        for p, q in (((xa, ya), (xb, ya)), ((xb, ya), (xb, yb)), ((xb, yb), (xa, yb)), ((xa, yb), (xa, ya))):
            dashdot(sh, p[0], p[1], q[0], q[1], 0.25)
        if c.get("ref"):   # 콜아웃 뷰가 시트에 번호와 함께 놓였다(W1 1-C 2차): 번호 / 시트 번호
            ref_bubble(sh, xb, ya - REF_R * getattr(sh, "sym_k", 1.0), *c["ref"])
        else:
            bubble(sh, xb, ya - 3.3, c["label"], 3.0, INK, 2.0)
    sh.end()


def tag_items(sh, gid, items, TX, TY, circle=True):
    """부호 태그(문 D·창 W·벽체 WT·실번호): items = [(x, y, 글)] 모델 좌표."""
    if not items:
        return
    sh.begin(gid, **{"data-count": len(items)})
    k = getattr(sh, "sym_k", 1.0)
    for x, y, text in items:
        px, py = TX(x), TY(y)
        if circle:
            sh.circle(px, py, (2.4 if len(text) < 4 else 3.0) * k, 0.25, INK, "#ffffff")
        sh.text(px, py + 0.7 * k, 1.9 * k, text, "middle")
    sh.end()
