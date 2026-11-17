# SPDX-License-Identifier: MPL-2.0
"""일반 솔리드(돌출·로프트·판·스윕·메시)와 모양 개구부(사각·반원 아치·뾰족 아치·원)의 순수 형상 (18단계, 설계: elements-design.md 17절).

이 모듈은 ifcopenshell 을 import 하지 않는다(kinds·drawing·queries 가 가볍게 부른다). 값은 모두 결정적이다:
삼각함수(libm)를 쓰지 않고 호는 현의 중점을 반지름으로 밀어내는 반복 이등분(사칙연산 + sqrt)으로 만든다(sqrt 는 IEEE 에서 정확히 반올림된다).

솔리드 형상은 하나의 다면체 표현으로 모은다:
  verts: [(x, y, z), ...]   z 는 요소가 놓인 레벨 기준 상대 높이
  faces: [[바깥 고리, 구멍 고리, ...], ...]   고리는 verts 번호 목록. 바깥 고리는 바깥을 향한 법선의 반시계(오른손) 방향
IFC 투영(IfcFacetedBrep)과 도면(절단선·투영선)이 같은 값을 쓴다.
"""
from __future__ import annotations

from math import isfinite, sqrt

# ---------------------------------------------------------------- 상한(입력 크기)
SOLID_PTS_MAX = 256          # 다각형(윤곽·단면·판 윤곽) 꼭짓점 수 상한
SOLID_SECTIONS_MAX = 32      # 로프트 단면 수 상한
SWEEP_PATH_MAX = 256         # 스윕 경로 꼭짓점 수 상한
SWEEP_PROFILE_MAX = 64       # 스윕 단면 꼭짓점 수 상한
MESH_VERTS_MAX = 4096        # 메시 꼭짓점 수 상한
MESH_FACES_MAX = 8192        # 메시 면 수 상한
MESH_FACE_PTS_MAX = 64       # 메시 면 하나의 꼭짓점 수 상한
OPENINGS_MAX = 64            # 벽·판 하나의 모양 개구부 수 상한
ARC_DEPTH = 3                # 호 이등분 횟수: 호 하나 = 2**3 = 8 현
OPENING_SHAPES = ("rect", "round", "pointed", "circle")
OPENING_FILLS = ("none", "glazing", "door")
SOLID_FORMS = ("prism", "loft", "panel", "sweep", "mesh", "member", "plate")
MEMBER_SHAPES = ("rect", "circle", "h", "box", "pipe")   # 부재(member) 단면: 사각·원·H/I형강·각형 강관(속 빈 사각)·원형 관(속 빈 원)
MEMBER_ANCHORS = ("top", "center", "bottom")             # 부재 축선이 단면의 어디를 지나는가(보는 윗면 가운데, 관·덕트는 가운데가 관행)

# 범주 -> (IFC 클래스, PredefinedType 또는 None, ObjectType 또는 None). 범주가 IFC 분류를 정한다(프록시 남발 금지)
SOLID_CATEGORIES = {
    "proxy": ("IfcBuildingElementProxy", None, None),
    "wall": ("IfcWall", None, None),
    "roof": ("IfcRoof", None, None),
    "spire": ("IfcRoof", "USERDEFINED", "SPIRE"),
    "slab": ("IfcSlab", None, None),
    "column": ("IfcColumn", None, None),
    "buttress": ("IfcColumn", "USERDEFINED", "BUTTRESS"),
    "member": ("IfcMember", None, None),
    "plate": ("IfcPlate", None, None),
    "covering": ("IfcCovering", "MOLDING", None),
    "window": ("IfcWindow", None, None),
    "door": ("IfcDoor", None, None),
    "stair": ("IfcStair", None, None),
    "railing": ("IfcRailing", None, None),
    "footing": ("IfcFooting", None, None),
    "beam": ("IfcBeam", None, None),
    "pipe": ("IfcPipeSegment", None, None),
    "duct": ("IfcDuctSegment", None, None),
    "cable_tray": ("IfcCableCarrierSegment", None, None),
    "terrain": ("IfcGeographicElement", "TERRAIN", None),
}


def _num(v) -> bool:
    if type(v) is int:
        return -(2**63) <= v < 2**63
    return type(v) is float and isfinite(v)


def _pt2(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and all(_num(x) for x in v)


def _pt3(v) -> bool:
    return isinstance(v, list) and len(v) == 3 and all(_num(x) for x in v)


def area2(pts) -> float:
    """다각형의 부호 있는 면적의 2배(양수 = 반시계)."""
    n = len(pts)
    return sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))


def _seg_cross(a, b, c, d) -> bool:
    def o(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (v > 0) - (v < 0)

    def on(p, q, r):
        return min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and min(p[1], q[1]) <= r[1] <= max(p[1], q[1])
    o1, o2, o3, o4 = o(a, b, c), o(a, b, d), o(c, d, a), o(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return (o1 == 0 and on(a, b, c)) or (o2 == 0 and on(a, b, d)) or (o3 == 0 and on(c, d, a)) or (o4 == 0 and on(c, d, b))


def poly_problem(pts, lo=3, hi=SOLID_PTS_MAX) -> str | None:
    """단순 다각형인가(안 되면 이유): 꼭짓점 lo~hi 개, 반복 꼭짓점 없음, 면적 0 아님, 자기 교차 없음."""
    n = len(pts)
    if not lo <= n <= hi:
        return f"꼭짓점은 {lo}~{hi}개여야 한다(현재 {n}개)"
    if len({(p[0], p[1]) for p in pts}) != n:
        return "같은 꼭짓점이 반복된다"
    if abs(area2(pts)) <= 1e-6:
        return "면적이 0 이다"
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if (j + 1) % n == i or (i + 1) % n == j:
                continue
            if _seg_cross(a, b, pts[j], pts[(j + 1) % n]):
                return "윤곽이 자기 자신과 교차한다"
    return None


def ccw(pts) -> list:
    """반시계로 맞춘 꼭짓점 목록(float)."""
    out = [(float(p[0]), float(p[1])) for p in pts]
    return out if area2(out) > 0 else out[::-1]


def point_in(pt, poly) -> int:
    """점이 다각형 안(1)·경계 위(0)·밖(-1)."""
    x, y = pt
    n = len(poly)
    inside = False
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        cr = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
        if abs(cr) <= 1e-9 and min(a[0], b[0]) - 1e-9 <= x <= max(a[0], b[0]) + 1e-9 and min(a[1], b[1]) - 1e-9 <= y <= max(a[1], b[1]) + 1e-9:
            return 0
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) / (b[1] - a[1]) * (b[0] - a[0]):
            inside = not inside
    return 1 if inside else -1


# ---------------------------------------------------------------- 호(결정적)
def _arc(c, a, b, depth=ARC_DEPTH):
    """중심 c, 시작 a 에서 끝 b 까지(180도 미만) 반시계든 시계든 짧은 쪽 호의 꼭짓점들(a 포함, b 제외)."""
    r = sqrt((a[0] - c[0]) ** 2 + (a[1] - c[1]) ** 2)
    pts = [a, b]
    for _ in range(depth):
        nxt = []
        for p, q in zip(pts, pts[1:]):
            mx, my = (p[0] + q[0]) / 2.0 - c[0], (p[1] + q[1]) / 2.0 - c[1]
            m = sqrt(mx * mx + my * my)
            nxt.extend([p, (c[0] + mx * r / m, c[1] + my * r / m)])
        nxt.append(pts[-1])
        pts = nxt
    return pts[:-1]


def opening_profile(shape: str, width: float, height: float, rise: float | None = None) -> tuple:
    """모양 개구부의 윤곽 (꼭짓점 목록 (u, z) 반시계, 사각 부분 윗선 z). u 는 개구부 중심 기준, z 는 개구부 바닥 기준.
    rect: 사각. round: 반원 머리(높이 >= 폭/2). pointed: 뾰족 아치(rise = 아치 높이, 생략하면 정삼각 아치 폭*0.866). circle: 원(폭 = 높이)."""
    h2 = width / 2.0
    if shape == "rect":
        return ([(-h2, 0.0), (h2, 0.0), (h2, height), (-h2, height)], height)
    if shape == "circle":
        c = (0.0, h2)
        q = [(h2, h2), (0.0, 2 * h2), (-h2, h2), (0.0, 0.0)]
        pts = []
        for i in range(4):
            pts.extend(_arc(c, q[i], q[(i + 1) % 4]))
        return (pts, 0.0)
    if shape == "round":
        sp = height - h2
        c = (0.0, sp)
        top = (0.0, height)
        pts = [(-h2, 0.0), (h2, 0.0)] + _arc(c, (h2, sp), top) + _arc(c, top, (-h2, sp)) + ([(-h2, sp)] if sp > 1e-9 else [])
        if sp <= 1e-9:   # 머리가 바닥에서 바로 시작(반원 개구부): 바닥 양 끝이 호의 끝이다
            pts = [(h2, 0.0)] + _arc(c, (h2, 0.0), top)[1:] + _arc(c, top, (-h2, 0.0)) + [(-h2, 0.0)]
            pts = _dedupe(pts)
        return (pts, sp)
    # pointed
    r = rise if rise is not None else width * 0.8660254037844386
    sp = height - r
    cx = (r * r - h2 * h2) / width          # 오른쪽 호의 중심은 (-cx, sp), 왼쪽 호는 (cx, sp)
    top = (0.0, height)
    right = _arc((-cx, sp), (h2, sp), top)
    left = _arc((cx, sp), top, (-h2, sp))
    pts = ([(-h2, 0.0), (h2, 0.0)] if sp > 1e-9 else []) + right + left + ([(-h2, sp)] if sp > 1e-9 else [(-h2, 0.0)])
    return (_dedupe(pts), sp)


def _dedupe(pts):
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= 1e-9 and abs(out[0][1] - out[-1][1]) <= 1e-9:
        out.pop()
    return out


def opening_ok(o, *, absolute_sill=False) -> str | None:
    """모양 개구부 하나 {"shape","offset","sill","width","height"(,"rise","fill")} 가 쓸 수 있는가(안 되면 이유)."""
    if not isinstance(o, dict):
        return "개구부는 객체여야 한다"
    need = {"shape", "offset", "sill", "width", "height"}
    if not need <= set(o) or set(o) - need - {"rise", "fill"}:
        return "개구부 키가 다르다(shape·offset·sill·width·height, 선택 rise·fill)"
    if o["shape"] not in OPENING_SHAPES:
        return f"개구부 모양은 {'/'.join(OPENING_SHAPES)} 중 하나다"
    if not all(_num(o[k]) for k in ("offset", "sill", "width", "height")) or o["width"] <= 0 or o["height"] <= 0:
        return "개구부 수치가 올바르지 않다(폭·높이 > 0)"
    if not absolute_sill and o["sill"] < 0:
        return "개구부 sill 은 0 이상이어야 한다"
    if "fill" in o and o["fill"] not in OPENING_FILLS:
        return f"개구부 fill 은 {'/'.join(OPENING_FILLS)} 중 하나다"
    if "rise" in o:
        if o["shape"] != "pointed":
            return "rise 는 뾰족 아치(pointed)에만 준다"
        if not _num(o["rise"]) or not o["width"] / 2.0 <= o["rise"] <= o["height"]:
            return "뾰족 아치 rise 는 폭/2 이상 높이 이하여야 한다"
    elif o["shape"] == "pointed" and o["width"] * 0.8660254037844386 > o["height"]:
        return "뾰족 아치(정삼각 기본 rise = 폭*0.866)가 높이보다 크다: rise 를 주거나 높이를 늘린다"
    if o["shape"] == "round" and o["height"] < o["width"] / 2.0 - 1e-9:
        return "반원 아치는 높이가 폭/2 이상이어야 한다"
    if o["shape"] == "circle" and abs(o["width"] - o["height"]) > 1e-6:
        return "원 개구부는 폭과 높이가 같아야 한다"
    return None


def opening_loop(o) -> list:
    """개구부의 (u, z) 윤곽(반시계): u 는 축 시작점 기준 길이 방향, z 는 sill 기준을 더한 값(벽은 레벨 기준, 판은 레벨 기준 절대)."""
    pts, _ = opening_profile(o["shape"], float(o["width"]), float(o["height"]), o.get("rise"))
    return [(o["offset"] + u, o["sill"] + z) for u, z in pts]


def opening_box(o) -> tuple:
    """(u0, u1, z0, z_spring, z_top): 사각 부분 상자와 꼭대기. 원은 사각 부분이 없다(z_spring = z0)."""
    _, sp = opening_profile(o["shape"], float(o["width"]), float(o["height"]), o.get("rise"))
    h2 = o["width"] / 2.0
    return (o["offset"] - h2, o["offset"] + h2, o["sill"], o["sill"] + sp, o["sill"] + o["height"])


def openings_problem(loops_outer, openings, absolute_sill=False) -> str | None:
    """개구부 목록이 윤곽(u,z 다각형) 안에 있고 서로 겹치지 않는가."""
    if not isinstance(openings, list) or not 1 <= len(openings) <= OPENINGS_MAX:
        return f"개구부는 1~{OPENINGS_MAX}개여야 한다"
    boxes = []
    for i, o in enumerate(openings):
        why = opening_ok(o, absolute_sill=absolute_sill)
        if why:
            return f"{i + 1}번 개구부: {why}"
        lp = opening_loop(o)
        if any(point_in(q, loops_outer) < 0 for q in lp):
            return f"{i + 1}번 개구부가 벽(판) 윤곽 밖으로 나간다"
        u0, u1, z0, _, zt = opening_box(o)
        for j, (a0, a1, b0, b1) in enumerate(boxes):
            if u0 < a1 - 1e-6 and a0 < u1 - 1e-6 and z0 < b1 - 1e-6 and b0 < zt - 1e-6:
                return f"{j + 1}번과 {i + 1}번 개구부가 겹친다"
        boxes.append((u0, u1, z0, zt))
    return None


# ---------------------------------------------------------------- 솔리드 파라미터 검사
def _openings_key_ok(p):
    return "openings" not in p or p.get("form") == "panel"


def solid_problem(p) -> str | None:
    """solid 요소 파라미터가 쓸 수 있는 모양인가(안 되면 이유). 공통 {"name","category","form"} + 형태별 키."""
    if not isinstance(p, dict):
        return "solid 의 파라미터가 객체가 아니다"
    form = p.get("form")
    keys = {"prism": {"polygon", "z0", "z1"}, "loft": {"sections"}, "panel": {"line", "thickness", "profile"},
            "sweep": {"path", "profile"}, "mesh": {"vertices", "faces"}, "member": {"start", "end", "section", "anchor"},
            "plate": {"polygon", "thickness", "base", "pitch", "dir"}}.get(form)
    if keys is None:
        return f"solid.form 은 {'/'.join(SOLID_FORMS)} 중 하나다"
    opt = {"openings"} if form == "panel" else {"holes"} if form == "plate" else set()
    base = {"name", "category", "form"} | keys
    if not base <= set(p) or set(p) - base - opt:
        return f"solid({form}) 의 파라미터 이름이 다르다"
    name = p["name"]
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or "\x00" in name:
        return "solid.name 값이 올바르지 않다"
    if p["category"] not in SOLID_CATEGORIES:
        return "solid.category 값이 올바르지 않다"
    if form == "prism":
        if not isinstance(p["polygon"], list) or not all(_pt2(q) for q in p["polygon"]):
            return "solid.polygon 값이 올바르지 않다"
        why = poly_problem(p["polygon"])
        if why:
            return f"solid.polygon: {why}"
        if not (_num(p["z0"]) and _num(p["z1"]) and p["z1"] - p["z0"] >= 1.0):
            return "solid z1 은 z0 보다 1mm 이상 커야 한다"
    elif form == "loft":
        ss = p["sections"]
        if not isinstance(ss, list) or not 2 <= len(ss) <= SOLID_SECTIONS_MAX:
            return f"solid.sections 는 단면 2~{SOLID_SECTIONS_MAX}개여야 한다"
        n = None
        for i, s in enumerate(ss):
            if not isinstance(s, dict) or set(s) != {"z", "polygon"} or not _num(s["z"]) or not isinstance(s["polygon"], list) or not all(_pt2(q) for q in s["polygon"]):
                return f"solid.sections[{i}] 모양이 올바르지 않다({{z, polygon}})"
            m = len(s["polygon"])
            if m == 1:
                if 0 < i < len(ss) - 1:
                    return "꼭짓점 하나(정점) 단면은 맨 아래나 맨 위에만 둘 수 있다"
                continue
            why = poly_problem(s["polygon"])
            if why:
                return f"solid.sections[{i}]: {why}"
            if n is None:
                n = m
            elif m != n:
                return "로프트 단면의 꼭짓점 수가 모두 같아야 한다"
        if n is None:
            return "로프트에는 다각형 단면이 하나 이상 있어야 한다"
        if any(ss[i + 1]["z"] - ss[i]["z"] < 1.0 for i in range(len(ss) - 1)):
            return "로프트 단면 z 는 아래에서 위로 1mm 이상씩 커져야 한다"
    elif form == "panel":
        ln = p["line"]
        if not (isinstance(ln, list) and len(ln) == 4 and all(_num(x) for x in ln) and (ln[2] - ln[0]) ** 2 + (ln[3] - ln[1]) ** 2 >= 1.0):
            return "solid.line 값이 올바르지 않다([x1,y1,x2,y2], 길이 1mm 이상)"
        if not (_num(p["thickness"]) and p["thickness"] > 0):
            return "solid.thickness 는 0 보다 커야 한다"
        if not isinstance(p["profile"], list) or not all(_pt2(q) for q in p["profile"]):
            return "solid.profile 값이 올바르지 않다"
        why = poly_problem(p["profile"])
        if why:
            return f"solid.profile: {why}"
        if "openings" in p:
            why = openings_problem(ccw(p["profile"]), p["openings"], absolute_sill=True)
            if why:
                return f"solid.openings: {why}"
    elif form == "sweep":
        path, prof = p["path"], p["profile"]
        if not isinstance(path, list) or not 2 <= len(path) <= SWEEP_PATH_MAX or not all(_pt2(q) for q in path):
            return f"solid.path 는 꼭짓점 2~{SWEEP_PATH_MAX}개여야 한다"
        for i in range(len(path) - 1):
            if (path[i + 1][0] - path[i][0]) ** 2 + (path[i + 1][1] - path[i][1]) ** 2 < 1.0:
                return "스윕 경로의 이웃 꼭짓점은 1mm 이상 떨어져야 한다"
        if not isinstance(prof, list) or not all(_pt2(q) for q in prof):
            return "solid.profile 값이 올바르지 않다"
        why = poly_problem(prof, 3, SWEEP_PROFILE_MAX)
        if why:
            return f"solid.profile: {why}"
        for i in range(1, len(path) - 1):   # 되돌아가는 꺾임(180도)은 마이터가 무한대다
            ax, ay = path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]
            bx, by = path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1]
            la, lb = sqrt(ax * ax + ay * ay), sqrt(bx * bx + by * by)
            if (ax * bx + ay * by) / (la * lb) < -0.94:
                return "스윕 경로가 140도 넘게 꺾인다(마이터가 너무 길어진다)"
    elif form == "member":
        why = member_problem(p)
        if why:
            return why
    elif form == "plate":
        why = plate_problem(p)
        if why:
            return why
    else:
        vs, fs = p["vertices"], p["faces"]
        if not isinstance(vs, list) or not 4 <= len(vs) <= MESH_VERTS_MAX or not all(_pt3(q) for q in vs):
            return f"solid.vertices 는 [x,y,z] 4~{MESH_VERTS_MAX}개여야 한다"
        if not isinstance(fs, list) or not 4 <= len(fs) <= MESH_FACES_MAX:
            return f"solid.faces 는 4~{MESH_FACES_MAX}개여야 한다"
        nv = len(vs)
        for f in fs:
            if (not isinstance(f, list) or not 3 <= len(f) <= MESH_FACE_PTS_MAX or not all(type(i) is int and 0 <= i < nv for i in f)
                    or len(set(f)) != len(f)):
                return "solid.faces 의 면은 서로 다른 꼭짓점 번호 3개 이상의 목록이어야 한다"
    return None


# ---------------------------------------------------------------- 형상(다면체)
def _extrude(outer, holes, to3, d, length):
    """평면 윤곽(바깥 반시계 + 구멍 시계, 평면 2D)을 to3(2D 점, 높이 t) 로 3D 에 놓고 길이 length 만큼 돌출한 다면체.
    to3(p, 0) 은 아랫면, to3(p, length) 는 윗면이다. 평면 법선(2D 반시계의 오른손 방향)이 돌출 방향과 같아야 한다."""
    verts, faces = [], []
    loops = [outer] + list(holes)
    base = []
    for lp in loops:
        b = len(verts)
        verts.extend(to3(q, 0.0) for q in lp)
        verts.extend(to3(q, length) for q in lp)
        base.append((b, len(lp)))
    bot = [[b + i for i in range(n)][::-1] for b, n in base]
    top = [[b + n + i for i in range(n)] for b, n in base]
    faces.append(bot)
    faces.append(top)
    for b, n in base:
        for i in range(n):
            j = (i + 1) % n
            faces.append([[b + i, b + j, b + n + j, b + n + i]])
    return verts, faces


def _cw(pts):
    out = [(float(p[0]), float(p[1])) for p in pts]
    return out if area2(out) < 0 else out[::-1]


def _planar(pts) -> bool:
    if len(pts) <= 3:
        return True
    a, b, c = pts[0], pts[1], pts[2]
    n = _cross(_sub(b, a), _sub(c, a))
    ln = sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
    if ln < 1e-12:
        n = newell(pts)
        ln = sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        if ln < 1e-12:
            return True
    return all(abs(_dot(_sub(q, a), n)) / ln <= 0.01 for q in pts[3:])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def newell(pts):
    """다각형 법선(뉴웰 방법, 정규화 안 함)."""
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return (nx, ny, nz)


def panel_frame(line):
    """판의 (원점 x, y, 길이 방향 ux, uy, 길이). 왼쪽 법선은 (-uy, ux)."""
    x1, y1, x2, y2 = (float(v) for v in line)
    ln = sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    return (x1, y1, (x2 - x1) / ln, (y2 - y1) / ln, ln)


def solid_mesh(p) -> tuple:
    """솔리드 파라미터 -> (verts, faces). z 는 레벨 기준."""
    form = p["form"]
    if form == "prism":
        z0 = float(p["z0"])
        return _extrude(ccw(p["polygon"]), [], lambda q, t: (q[0], q[1], z0 + t), None, float(p["z1"]) - z0)
    if form == "panel":
        x1, y1, ux, uy, _ = panel_frame(p["line"])
        h = p["thickness"] / 2.0
        nx, ny = -uy, ux
        # (u, z) 반시계의 오른손 법선 = u x z = (uy, -ux, 0) = -왼쪽 법선. 그래서 왼쪽 면(+h)에서 시작해 -왼쪽 방향으로 두께만큼 돌출한다
        def to3(q, t):
            v = h - t
            return (x1 + q[0] * ux + v * nx, y1 + q[0] * uy + v * ny, q[1])
        holes = [_cw(opening_loop(o)) for o in p.get("openings", ())]
        return _extrude(ccw(p["profile"]), holes, to3, None, float(p["thickness"]))
    if form == "sweep":
        return _sweep(p["path"], p["profile"])
    if form == "loft":
        return _loft(p["sections"])
    if form == "member":
        return _member(p)
    if form == "plate":
        return _plate(p)
    verts = [(float(q[0]), float(q[1]), float(q[2])) for q in p["vertices"]]
    return verts, [[list(f)] for f in p["faces"]]


def _loft(sections):
    verts, faces, rings = [], [], []
    for s in sections:
        z = float(s["z"])
        pts = ccw(s["polygon"]) if len(s["polygon"]) > 1 else [(float(s["polygon"][0][0]), float(s["polygon"][0][1]))]
        b = len(verts)
        verts.extend((q[0], q[1], z) for q in pts)
        rings.append(list(range(b, b + len(pts))))
    if len(rings[0]) > 1:
        faces.append([rings[0][::-1]])
    if len(rings[-1]) > 1:
        faces.append([list(rings[-1])])
    for ra, rb in zip(rings, rings[1:]):
        n = max(len(ra), len(rb))
        for i in range(n):
            j = (i + 1) % n
            a0, a1 = ra[i % len(ra)], ra[j % len(ra)]
            b0, b1 = rb[i % len(rb)], rb[j % len(rb)]
            if len(ra) == 1:
                faces.append([[a0, b1, b0]])   # 아래가 정점: 사각형 [a0,a1,b1,b0] 에서 a0 = a1
            elif len(rb) == 1:
                faces.append([[a0, a1, b0]])
            else:
                quad = [a0, a1, b1, b0]
                if _planar([verts[k] for k in quad]):
                    faces.append([quad])
                else:   # 비틀린 옆면은 두 삼각형으로(IfcFacetedBrep 의 면은 평면이어야 한다)
                    faces.append([[a0, a1, b1]])
                    faces.append([[a0, b1, b0]])
    return verts, faces


def _sweep(path, prof):
    pts = [(float(q[0]), float(q[1])) for q in path]
    closed = len(pts) > 3 and abs(pts[0][0] - pts[-1][0]) <= 1e-6 and abs(pts[0][1] - pts[-1][1]) <= 1e-6
    if closed:
        pts = pts[:-1]
    n = len(pts)

    def seg_dir(i):
        a, b = pts[i], pts[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = sqrt(dx * dx + dy * dy)
        return (dx / ln, dy / ln)
    nseg = n if closed else n - 1
    dirs = [seg_dir(i) for i in range(nseg)]
    miters = []
    for i in range(n):
        if closed:
            d0, d1 = dirs[(i - 1) % n], dirs[i]
        else:
            d0 = dirs[max(i - 1, 0)]
            d1 = dirs[min(i, nseg - 1)]
        l0, l1 = (-d0[1], d0[0]), (-d1[1], d1[0])
        mx, my = l0[0] + l1[0], l0[1] + l1[1]
        m = sqrt(mx * mx + my * my)
        mx, my = mx / m, my / m
        k = mx * l0[0] + my * l0[1]
        miters.append((mx / k, my / k))
    loop = ccw(prof)   # (o, z) 반시계의 오른손 법선 = 진행 방향
    verts, faces = [], []
    m = len(loop)
    for i in range(n):
        for o, z in loop:
            verts.append((pts[i][0] + o * miters[i][0], pts[i][1] + o * miters[i][1], z))
    rings = [list(range(i * m, (i + 1) * m)) for i in range(n)]
    if not closed:
        faces.append([rings[0][::-1]])
        faces.append([list(rings[-1])])
    for i in range(nseg):
        ra, rb = rings[i], rings[(i + 1) % n]
        for k in range(m):
            q = (k + 1) % m
            faces.append([[ra[k], ra[q], rb[q], rb[k]]])
    return verts, faces


def mesh_bbox(verts) -> tuple:
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def face_normals(verts, faces) -> list:
    """면마다 단위 법선(바깥 고리 기준). 퇴화 면은 (0,0,0)."""
    out = []
    for f in faces:
        n = newell([verts[i] for i in f[0]])
        ln = sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        out.append((n[0] / ln, n[1] / ln, n[2] / ln) if ln > 1e-12 else (0.0, 0.0, 0.0))
    return out


FEATURE_COS = 0.9962   # 이웃 면 법선 사이 각이 약 5도를 넘으면 모서리선(같은 평면을 나눈 대각선은 그리지 않는다)


def feature_edges(verts, faces) -> tuple:
    """그리는 모서리: ((a, b, (이웃 면 번호들)), ...). 경계(면 하나)이거나 이웃 면이 꺾이는 모서리."""
    nrm = face_normals(verts, faces)
    edges: dict = {}
    for fi, f in enumerate(faces):
        for lp in f:
            k = len(lp)
            for i in range(k):
                a, b = lp[i], lp[(i + 1) % k]
                key = (a, b) if a < b else (b, a)
                edges.setdefault(key, []).append(fi)
    out = []
    for (a, b), fl in sorted(edges.items()):
        if len(fl) == 2:
            n0, n1 = nrm[fl[0]], nrm[fl[1]]
            if _dot(n0, n1) >= FEATURE_COS:
                continue
        out.append((a, b, tuple(fl)))
    return tuple(out), tuple(nrm)


def plane_section(verts, faces, nrm, axis, c) -> list:
    """다면체를 평면 (점 . axis) = c 로 자른 선분들 [((x,y,z),(x,y,z)), ...]. axis 는 단위 벡터.
    면마다 고리 변이 평면을 지나는 점을 면 위 교선 방향으로 정렬해 짝짓는다(오목·구멍 면도 짝수-홀수 규칙으로 맞다). 평면에 놓인 면은 건너뛴다."""
    out = []
    for fi, f in enumerate(faces):
        n = nrm[fi]
        dvec = _cross(n, axis)
        dl = sqrt(_dot(dvec, dvec))
        if dl < 1e-9:
            continue   # 면이 절단면과 평행
        hits = []
        for lp in f:
            k = len(lp)
            for i in range(k):
                a, b = verts[lp[i]], verts[lp[(i + 1) % k]]
                da, db = _dot(a, axis) - c, _dot(b, axis) - c
                if (da > 0) == (db > 0):
                    continue   # 반열린 규칙: 평면 위 꼭짓점(=0)은 아래쪽으로 센다
                t = da / (da - db)
                q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
                hits.append((_dot(q, dvec), q))
        hits.sort()
        for i in range(0, len(hits) - 1, 2):
            p0, p1 = hits[i][1], hits[i + 1][1]
            if abs(hits[i + 1][0] - hits[i][0]) > 1e-6:
                out.append((p0, p1))
    return out


def triangulate(poly) -> list:
    """단순 다각형(반시계)을 귀 자르기로 삼각형 [(a,b,c), ...] (꼭짓점 좌표)로 나눈다. 결정적(앞에서부터)."""
    pts = list(poly)
    idx = list(range(len(pts)))
    out = []
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        n = len(idx)
        for k in range(n):
            ia, ib, ic = idx[(k - 1) % n], idx[k], idx[(k + 1) % n]
            a, b, c = pts[ia], pts[ib], pts[ic]
            cr = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cr <= 1e-9:
                continue
            if any(_in_tri(pts[j], a, b, c) for j in idx if j not in (ia, ib, ic)):
                continue
            out.append((a, b, c))
            idx.pop(k)
            break
        else:
            break   # 귀를 못 찾음(수치 퇴화): 남은 것은 부채꼴로
    if len(idx) >= 3:
        for k in range(1, len(idx) - 1):
            out.append((pts[idx[0]], pts[idx[k]], pts[idx[k + 1]]))
    return out


def _in_tri(p, a, b, c) -> bool:
    def s(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1, d2, d3 = s(p, a, b), s(p, b, c), s(p, c, a)
    return d1 >= -1e-9 and d2 >= -1e-9 and d3 >= -1e-9


def scan_intervals(loops, z) -> list:
    """(u, z) 고리들(바깥 + 구멍)을 높이 z 의 수평선으로 잘라 속이 찬 u 구간 [(u0, u1), ...] (짝수-홀수 규칙)."""
    xs = []
    for lp in loops:
        k = len(lp)
        for i in range(k):
            a, b = lp[i], lp[(i + 1) % k]
            if (a[1] > z) != (b[1] > z):
                xs.append(a[0] + (z - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
    xs.sort()
    return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2) if xs[i + 1] - xs[i] > 1e-6]


# ---------------------------------------------------------------- 부재(member, 19단계): 3D 축선을 따라 단면을 곧게 돌출(보·가새·관·덕트·케이블 트레이)
def member_problem(p) -> str | None:
    """부재 파라미터 검사(안 되면 이유). start/end = [x,y,z](레벨 기준), section = {shape, width, depth, 선택 web/flange/wall}, anchor."""
    a, b = p["start"], p["end"]
    if not (_pt3(a) and _pt3(b)):
        return "solid.start·end 는 [x,y,z] 여야 한다"
    if (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2 < 1.0:
        return "부재 길이는 1mm 이상이어야 한다"
    if p["anchor"] not in MEMBER_ANCHORS:
        return f"solid.anchor 는 {'/'.join(MEMBER_ANCHORS)} 중 하나다"
    sc = p["section"]
    if not isinstance(sc, dict) or sc.get("shape") not in MEMBER_SHAPES:
        return f"solid.section.shape 는 {'/'.join(MEMBER_SHAPES)} 중 하나다"
    need = {"rect": set(), "circle": set(), "h": {"web", "flange"}, "box": {"wall"}, "pipe": {"wall"}}[sc["shape"]]
    if set(sc) != {"shape", "width", "depth"} | need:
        return f"solid.section({sc['shape']}) 의 키가 다르다(shape, width, depth" + "".join(", " + k for k in sorted(need)) + ")"
    if not all(_num(sc[k]) and sc[k] > 0 for k in sc if k != "shape"):
        return "solid.section 치수는 0 보다 커야 한다"
    w, d = sc["width"], sc["depth"]
    if sc["shape"] in ("circle", "pipe") and abs(w - d) > 1e-9:
        return "원형 단면은 width 와 depth 가 같아야 한다(지름)"
    if sc["shape"] == "h" and not (sc["web"] < w and 2 * sc["flange"] < d):
        return "H형 단면은 web < width, 2*flange < depth 여야 한다"
    if sc["shape"] in ("box", "pipe") and not 2 * sc["wall"] < min(w, d):
        return "속 빈 단면의 두께(wall)는 폭·깊이의 절반보다 작아야 한다"
    return None


def _circle(r, cx=0.0, cy=0.0):
    """원 근사(반시계 32각형, 사분원 4개 x 8현): 삼각함수 없이 _arc 로."""
    q = [(cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)]
    out = []
    for i in range(4):
        out.extend(_arc((cx, cy), q[i], q[(i + 1) % 4]))
    return out


def member_section(sc, anchor) -> tuple:
    """단면 (바깥 반시계, 구멍 목록(시계)) 2D (a, b). a = 폭 방향(수평 축선이면 진행 방향 왼쪽 +), b = 위쪽. anchor 가 b 원점을 정한다."""
    w, d = float(sc["width"]), float(sc["depth"])
    b0 = {"top": -d, "center": -d / 2.0, "bottom": 0.0}[anchor]
    h = w / 2.0
    shape = sc["shape"]
    if shape in ("circle", "pipe"):
        outer = _circle(h, 0.0, b0 + d / 2.0)
        holes = [_circle(h - float(sc["wall"]), 0.0, b0 + d / 2.0)[::-1]] if shape == "pipe" else []
        return outer, holes
    if shape == "h":
        tw, tf = float(sc["web"]) / 2.0, float(sc["flange"])
        y0, y1 = b0, b0 + d
        outer = [(-h, y0), (h, y0), (h, y0 + tf), (tw, y0 + tf), (tw, y1 - tf), (h, y1 - tf), (h, y1), (-h, y1), (-h, y1 - tf), (-tw, y1 - tf), (-tw, y0 + tf), (-h, y0 + tf)]
        return outer, []
    outer = [(-h, b0), (h, b0), (h, b0 + d), (-h, b0 + d)]
    if shape == "box":
        t = float(sc["wall"])
        return outer, [[(-h + t, b0 + t), (-h + t, b0 + d - t), (h - t, b0 + d - t), (h - t, b0 + t)]]
    return outer, []


def member_frame(p) -> tuple:
    """부재 좌표계 (원점, 축 방향 D, 폭 방향 S, 위 방향 U, 길이). S x U = D(돌출 방향이 단면의 오른손 법선과 같다).
    U 는 세계 z 를 축에 수직으로 투영한 것, 축이 거의 수직이면(|D.z| > 0.999) 세계 x 를 투영한다."""
    a = tuple(float(v) for v in p["start"])
    b = tuple(float(v) for v in p["end"])
    dv = _sub(b, a)
    ln = sqrt(_dot(dv, dv))
    D = (dv[0] / ln, dv[1] / ln, dv[2] / ln)
    ref = (0.0, 0.0, 1.0) if abs(D[2]) <= 0.999 else (1.0, 0.0, 0.0)
    k = _dot(ref, D)
    u = (ref[0] - k * D[0], ref[1] - k * D[1], ref[2] - k * D[2])
    lu = sqrt(_dot(u, u))
    U = (u[0] / lu, u[1] / lu, u[2] / lu)
    S = _cross(U, D)
    return a, D, S, U, ln


def _member(p):
    o, D, S, U, ln = member_frame(p)
    outer, holes = member_section(p["section"], p["anchor"])

    def to3(q, t):
        return (o[0] + q[0] * S[0] + q[1] * U[0] + t * D[0], o[1] + q[0] * S[1] + q[1] * U[1] + t * D[1], o[2] + q[0] * S[2] + q[1] * U[2] + t * D[2])
    return _extrude(outer, holes, to3, None, ln)


# ---------------------------------------------------------------- 경사 판(plate, 19단계): 임의 평면 윤곽(+구멍)을 기운 평면 위에 놓고 수직 두께만큼 아래로(지붕판·경사 슬래브)
PLATE_PITCH_MAX = 80.0


def sincos_deg(a):
    """도 -> (sin, cos). libm 없이 90도 단위로 줄인 뒤 테일러 급수(kinds.sincos_deg 와 같은 알고리즘: solids 는 kinds 를 import 하지 않는다)."""
    a = a - 360.0 * int(a / 360.0) if a >= 0 else a - 360.0 * (int(a / 360.0) - 1)
    a = a - 360.0 if a >= 360.0 else a
    k = int((a + 45.0) / 90.0)
    x = (a - 90.0 * k) * 3.141592653589793 / 180.0
    x2 = x * x
    sn = cs = 0.0
    tn, tc = x, 1.0
    for i in range(1, 9):
        sn += tn
        cs += tc
        tn = -tn * x2 / ((2 * i) * (2 * i + 1))
        tc = -tc * x2 / ((2 * i - 1) * (2 * i))
    return [(sn, cs), (cs, -sn), (-sn, -cs), (-cs, sn)][k % 4]


def plate_problem(p) -> str | None:
    """판 파라미터 검사: polygon(단순 다각형), 선택 holes(윤곽 안, 서로·윤곽과 교차 없음), thickness > 0, base [x,y,z](윗면 위의 한 점), pitch 0~80도, dir 물이 흐르는 방향(도)."""
    if not isinstance(p["polygon"], list) or not all(_pt2(q) for q in p["polygon"]):
        return "solid.polygon 값이 올바르지 않다"
    why = poly_problem(p["polygon"])
    if why:
        return f"solid.polygon: {why}"
    if not (_num(p["thickness"]) and p["thickness"] > 0):
        return "solid.thickness 는 0 보다 커야 한다"
    if not _pt3(p["base"]):
        return "solid.base 는 [x,y,z] 여야 한다"
    if not (_num(p["pitch"]) and 0 <= p["pitch"] <= PLATE_PITCH_MAX):
        return f"solid.pitch 는 0~{PLATE_PITCH_MAX:g}도여야 한다"
    if not (_num(p["dir"]) and -360.0 <= p["dir"] <= 360.0):
        return "solid.dir 은 -360~360 도여야 한다"
    holes = p.get("holes", [])
    if "holes" in p and (not isinstance(holes, list) or not 1 <= len(holes) <= OPENINGS_MAX):
        return f"solid.holes 는 1~{OPENINGS_MAX}개여야 한다"
    outer = p["polygon"]
    loops = [outer]
    for i, h in enumerate(holes):
        if not isinstance(h, list) or not all(_pt2(q) for q in h):
            return f"solid.holes[{i}] 값이 올바르지 않다"
        why = poly_problem(h)
        if why:
            return f"solid.holes[{i}]: {why}"
        if any(point_in(q, ccw(outer)) != 1 for q in h):
            return f"solid.holes[{i}] 가 윤곽 안에 있지 않다"
        loops.append(h)
    for i in range(len(loops)):          # 고리끼리 변이 교차하지 않는다(구멍끼리 겹침·윤곽에 닿음)
        for j in range(i + 1, len(loops)):
            a, b = loops[i], loops[j]
            for k in range(len(a)):
                for m in range(len(b)):
                    if _seg_cross(a[k], a[(k + 1) % len(a)], b[m], b[(m + 1) % len(b)]):
                        return "solid.holes 가 윤곽이나 다른 구멍과 겹친다"
        if i > 0 and any(point_in(q, ccw(loops[j])) == 1 for j in range(1, len(loops)) if j != i for q in loops[i][:1]):
            return "solid.holes 가 다른 구멍 안에 있다"
    return None


def plate_plane(p):
    """윗면 평면 z = a x + b y + c (레벨 기준)."""
    s, c = sincos_deg(float(p["dir"]))
    ps, pc = sincos_deg(float(p["pitch"]))
    t = ps / pc
    a, b = -t * c, -t * s   # dir 쪽으로 내려간다
    x0, y0, z0 = (float(v) for v in p["base"])
    return a, b, z0 - a * x0 - b * y0


def _plate(p):
    a, b, c = plate_plane(p)
    th = float(p["thickness"])
    loops = [ccw(p["polygon"])] + [_cw(h) for h in p.get("holes", ())]
    verts, top, bot, sides = [], [], [], []
    for lp in loops:
        base = len(verts)
        n = len(lp)
        for x, y in lp:
            verts.append((x, y, a * x + b * y + c))
        for x, y in lp:
            verts.append((x, y, a * x + b * y + c - th))
        top.append([base + i for i in range(n)])
        bot.append([base + n + i for i in range(n)][::-1])
        for i in range(n):
            j = (i + 1) % n
            sides.append([[base + n + i, base + n + j, base + j, base + i]])
    return verts, [top, bot] + sides
