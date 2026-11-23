# SPDX-License-Identifier: MPL-2.0
"""커튼월(W2 네 번째 PR, ARC-22) 배치 기하. 순수 함수: 시각·난수·파일 접근 금지, libm 의 sin·cos 대신 사칙연산과 sqrt 만 쓴다
(호는 arcgeom 의 결정적 삼각함수를 쓴다. 도면·IFC 해시가 플랫폼마다 흔들리지 않게).

커튼월 params(kind "curtwall"):
  name, axis = [x1, y1, x2, y2](벽 축), arc = [mx, my](선택: 곡선 벽과 같은 뜻, 호의 가운데 점), height, base_offset(선택, 0 이 아님)
  u_grid / v_grid = {"rule": none|count|spacing|max_spacing, "value": 수, "justify": start|center|end}   (u = 축 방향 세로 격자선, v = 높이 방향 가로 격자선)
      count = 칸 수(같은 간격), spacing = 고정 간격(justify 로 남는 몫의 자리), max_spacing = 이 간격 이하가 되는 가장 적은 같은 간격
  u_add / u_remove / v_add / v_remove(선택) = 개별 격자선 더하기·빼기(위치 mm, u 는 축 시작점에서 호 위 거리, v 는 커튼월 바닥에서). 옮기기 = 빼고 더하기
  types = {유형 이름: {"width": 폭(입면 방향), "depth": 깊이(벽 두께 방향)}} 1~4개(사각 단면 멀리언)
  mullion = {"interior": 유형, "border": 유형(선택: 없으면 외곽 멀리언 없음)}
  join = vertical(세로 연속: 가로대가 세로대 사이에서 끊긴다) | horizontal(가로 연속)
  panel_thickness = 패널 두께(축 위 가운데)
  mullion_overrides(선택) = {멀리언 키: 유형 이름 | ""(지움)}, panel_overrides(선택) = {패널 키: glass|opaque|door|empty}

키: 세로 멀리언 "V<u>:<z0>"(격자선 u 의 z0 에서 시작하는 한 칸), 가로 멀리언 "H<z>:<u0>", 패널 "P<u0>:<z0>". 수는 0.1mm 로 반올림해 적는다.
  격자선 위치만의 함수라 다른 선을 더하고 빼도 그 칸의 키는 유지되고(칸이 나뉘면 새 키), IFC 부속 GlobalId = part_guid(커튼월, "m:<키>"·"p:<키>") 가 결정적이다.

형상: 모든 부속은 수직 각기둥(평면 사각형 + z 범위)이다. 세로 멀리언 = 격자선 점에서 접선 방향 폭 x 깊이, 가로 멀리언 = 이웃 격자선 점을 잇는 현을
  교차부 우선순위대로 자른 길이 x 깊이(높이 방향으로 폭만큼), 패널 = 현 x 패널 두께. 곡선 축은 격자선 사이를 현으로 잇는 꺾인 면이다(상용 도구와 같다).
외곽 멀리언은 경계 안쪽에 놓인다(바깥 면 = 커튼월 끝·바닥·윗면).

W2 열 번째 PR(ARC-22 잔여) 선택 params(없으면 키 자체가 없고 배치·도면·IFC 는 이전과 같다):
  top_profile = [[u, z], ...] 상단 윤곽(벽 상단 윤곽 walltop 과 같은 규칙: u 는 축 위 거리, z 는 커튼월 바닥 기준, 점 사이 직선·같은 u 두 점은 수직 단).
      curtain.attach 가 지붕·슬래브·솔리드 면에 붙여 채운다. 윤곽 위의 칸·멀리언은 없어지고, 걸친 칸·멀리언은 윤곽에서 잘린 모양 부속(아래 shapes)이 되며,
      윗면 외곽 멀리언(키 "H<height>:<u0>")은 윤곽을 따라가는 띠(높이 방향 폭)가 된다
  form = "sloped" 경사 커튼 시스템(유리지붕): 수직 커튼월 대신 기울어진(또는 수평) 평면 위의 커튼 그리드.
      axis = 기준선 [x1, y1, x2, y2](평면, 높이 0 = 레벨 + base_offset), slope = 기준선 왼쪽으로 오르는 물매 각(도, 0 = 수평), boundary = 평면 외곽(볼록 다각형).
      평면 좌표 a = 기준선 방향 거리, b = 면 위에서 기준선과 수직으로 잰 거리(경사 실길이). u 격자는 a 방향, v 격자는 b 방향이고 위치는 외곽의 a·b 최소에서 잰다.
      height·arc·top_profile 은 없다. 내부 멀리언은 (외곽 멀리언 폭만큼 들인) 외곽 안으로 잘리고, 외곽 멀리언은 외곽 변마다 하나(키 "E<변 번호>:0"), 패널은 칸 ∩ 외곽
  materials = {"mullion"|"glass"|"opaque"|"door": 재료 이름} 커튼월별 부속 재료(없는 종류는 기본 재료: 알루미늄·유리·불투명 패널·문)
모양 부속: 배치의 "shapes" = {키: (다각형들, 원점, e1, e2, n, h0, h1, 덧)} — 틀 좌표(a, b) 다각형(반시계)을 원점 + a·e1 + b·e2 에 놓고 n 방향 h0 ~ h1 로 돌출한 각기둥.
  원점 z 는 커튼월 바닥 기준. 덧 = 패널이면 (변마다 멀리언이 덮는지, ...) 다각형마다, 멀리언이면 (길이,). 모양이 없는 부속은 이전과 같은 수직 각기둥이다.
"""
from __future__ import annotations

from math import sqrt

from dobim_core import arcgeom as AG
from dobim_core import walltop as WT

RULES = ("none", "count", "spacing", "max_spacing")
JUSTIFY = ("start", "center", "end")
JOINS = ("vertical", "horizontal")
PANEL_TYPES = ("glass", "opaque", "door", "empty")
LEN_MAX = 400000.0          # 축 길이 상한(mm): 실건물 141 m 입면을 한 요소로
HEIGHT_MAX = 200000.0       # 높이 상한(mm)
LINES_MAX = 2000            # 방향마다 내부 격자선 수 상한
CELLS_MAX = 60000           # 칸 수 상한(멀리언은 약 두 배)
TYPES_MAX = 4               # 멀리언 유형 수 상한
TYPE_DIM_MAX = 2000.0       # 멀리언 폭·깊이 상한(mm)
PANEL_T_MAX = 500.0         # 패널 두께 상한(mm)
ADDS_MAX = 2000             # 개별 더하기·빼기 목록 길이 상한
EDGE_TOL = 1.0              # 내부 격자선은 끝에서 이만큼(mm) 안쪽이어야 한다
MERGE_TOL = 1.0             # 이보다 가까운 격자선은 같은 선이다
REMOVE_TOL = 0.5            # 빼기 위치가 이만큼 안이면 그 선을 뺀다
MIN_GAP = 1.0               # 교차부에서 잘린 멀리언·칸이 이보다 짧으면 거절한다
DEFAULT_PANEL = "glass"
ARC_STEP_MAX = 0.5          # 곡선 축에서 이웃 격자선의 중심각 상한(라디안, 약 29도): 현이 호에서 너무 멀어지지 않게
FORMS = ("sloped",)         # 수직 커튼월이 아닌 형식(W2 열 번째 PR): 경사 커튼 시스템
SLOPE_MAX = 80.0            # 경사 커튼 시스템의 물매 각 상한(도)
BOUNDARY_MAX = 64           # 경사 커튼 시스템 외곽 점 수 상한
MATERIAL_CATS = ("mullion", "glass", "opaque", "door")   # 커튼월별 재료를 줄 수 있는 부속 종류
AREA_MIN = 1.0              # 잘린 모양 부속이 이보다 작은 넓이(mm²)면 없는 것으로 본다


def fmt(x: float) -> str:
    """키에 쓰는 수: 0.1mm 반올림, 끝의 .0 은 뺀다(-0 은 0)."""
    s = f"{round(x, 1) + 0.0:.1f}"
    return s[:-2] if s.endswith(".0") else s


def frame(p):
    """(축 길이, 점 함수 u -> (x, y), 접선 함수 u -> (tx, ty), 곡선 축 정보 또는 None)."""
    if "arc" in p:
        ai = AG.info(p["axis"], p["arc"])
        return AG.length(ai), (lambda u: AG.point(ai, u)), (lambda u: AG.tangent(ai, u)), ai
    x0, y0, x1, y1 = p["axis"]
    dx, dy = x1 - x0, y1 - y0
    n = sqrt(dx * dx + dy * dy)
    tx, ty = dx / n, dy / n
    return n, (lambda u: (x0 + tx * u, y0 + ty * u)), (lambda u: (tx, ty)), None


def axis_problem(p) -> str | None:
    ax = p["axis"]
    dx, dy = ax[2] - ax[0], ax[3] - ax[1]
    if dx * dx + dy * dy < 1.0:
        return "축의 시작·끝점이 1mm 이상 떨어져야 한다"
    if "arc" in p:
        why = AG.problem(ax, p["arc"], 0.0)
        if why:
            return why
    length = frame(p)[0]
    if length > LEN_MAX:
        return f"축 길이가 {LEN_MAX:g}mm 를 넘는다"
    return None


def _rule_lines(rule: dict, length: float) -> list:
    """규칙이 만드는 내부 격자선 위치(오름차순). 끝에서 EDGE_TOL 안의 선은 버린다."""
    r = rule["rule"]
    if r == "none":
        return []
    val = rule["value"]
    if r == "count":
        n = int(val)
        return [length * k / n for k in range(1, n)]
    if r == "max_spacing":
        n = int(length / val)
        if n * val < length - 1e-9:
            n += 1
        return [length * k / n for k in range(1, max(n, 1))]
    # spacing
    j = rule.get("justify", "start")
    n = int(length / val)
    if j == "start":
        pos = [val * k for k in range(1, n + 1)]
    elif j == "end":
        pos = [length - val * k for k in range(n, 0, -1)]
    else:
        off = (length - n * val) / 2.0
        pos = [off + val * k for k in range(0, n + 1)]
    return [x for x in pos if EDGE_TOL < x < length - EDGE_TOL]


def rule_count(rule: dict, length: float) -> int:
    """규칙이 만드는 선 수의 어림(검사 전에 거대한 목록을 만들지 않게)."""
    r = rule["rule"]
    if r == "none":
        return 0
    if r == "count":
        return int(rule["value"])
    return int(length / rule["value"]) + 1


def grid_lines(rule: dict, adds, removes, length: float) -> list:
    """내부 격자선 위치(오름차순): 규칙의 선 - 빼기 + 더하기. 가까운 선(MERGE_TOL)은 하나로."""
    pos = [x for x in _rule_lines(rule, length) if not any(abs(x - r) <= REMOVE_TOL for r in removes)]
    for a in adds:
        if EDGE_TOL < a < length - EDGE_TOL:
            pos.append(float(a))
    pos.sort()
    out = []
    for x in pos:
        if not out or x - out[-1] > MERGE_TOL:
            out.append(x)
    return out


def _norm2(ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    n = sqrt(dx * dx + dy * dy)
    return (dx / n, dy / n, n) if n > 0 else (1.0, 0.0, 0.0)


def quad(a, b, half: float) -> tuple:
    """a -> b 중심선의 양쪽 half 만큼 두께를 준 사각형(반시계): 오른쪽 a, b, 왼쪽 b, a."""
    tx, ty, _ = _norm2(a[0], a[1], b[0], b[1])
    nx, ny = -ty * half, tx * half
    return ((a[0] - nx, a[1] - ny), (b[0] - nx, b[1] - ny), (b[0] + nx, b[1] + ny), (a[0] + nx, a[1] + ny))


def layout(p) -> dict:
    """배치 전체(순수 값). 문제가 있으면 ValueError(이유 문장)."""
    if p.get("form") == "sloped":   # 경사 커튼 시스템(W2 열 번째 PR)
        return _layout_sloped(p)
    why = axis_problem(p)
    if why:
        raise ValueError(why)
    length, pt, tg, ai = frame(p)
    height = p["height"]
    prof = p.get("top_profile")
    if prof is not None:   # 상단 윤곽(W2 열 번째 PR): 벽 상단 윤곽과 같은 규칙, 높이를 넘지 않는다
        why = WT.profile_problem(prof, length)
        if why:
            raise ValueError(f"상단 윤곽: {why}")
        if WT.zmax(prof) > height + EDGE_TOL:
            raise ValueError("상단 윤곽이 커튼월 높이보다 높다")
    for d, rule, size in (("u", p["u_grid"], length), ("v", p["v_grid"], height)):
        if rule_count(rule, size) > LINES_MAX + 1:
            raise ValueError(f"{d} 격자선이 {LINES_MAX}개를 넘는다")
    ui = grid_lines(p["u_grid"], p.get("u_add", ()), p.get("u_remove", ()), length)
    zi = grid_lines(p["v_grid"], p.get("v_add", ()), p.get("v_remove", ()), height)
    if len(ui) > LINES_MAX or len(zi) > LINES_MAX:
        raise ValueError(f"격자선이 방향마다 {LINES_MAX}개를 넘는다")
    us = [0.0] + ui + [length]
    zs = [0.0] + zi + [height]
    nu, nz = len(us) - 1, len(zs) - 1
    if nu * nz > CELLS_MAX:
        raise ValueError(f"칸 수({nu * nz})가 {CELLS_MAX}개를 넘는다")
    if ai is not None and any((us[j + 1] - us[j]) / ai[2] > ARC_STEP_MAX for j in range(nu)):
        raise ValueError("곡선 축의 세로 격자선 간격이 너무 넓다(이웃 선의 중심각 약 29도 이하): u_grid 를 더 촘촘히")
    types = p["types"]
    mo = p.get("mullion_overrides", {})
    po = p.get("panel_overrides", {})
    inner, border = p["mullion"]["interior"], p["mullion"].get("border")
    fu = [fmt(u) for u in us]
    fz = [fmt(z) for z in zs]

    def mtype(key, default):
        t = mo.get(key, default)
        return t if t else None   # "" = 지운 멀리언, None = 없는 외곽
    vt, ht = {}, {}   # (선, 칸) -> 유형 이름
    for j in range(nu + 1):
        dflt = border if j in (0, nu) else inner
        for i in range(nz):
            t = mtype(f"V{fu[j]}:{fz[i]}", dflt)
            if t is not None:
                vt[(j, i)] = t
    for i in range(nz + 1):
        dflt = border if i in (0, nz) else inner
        for j in range(nu):
            t = mtype(f"H{fz[i]}:{fu[j]}", dflt)
            if t is not None:
                ht[(i, j)] = t

    def vext(j, i):
        """세로선 j 가 가로선 i 와 만나는 곳에서 차지하는 u 방향 폭(외곽은 안쪽으로 전부, 내부는 반)."""
        ws = [types[vt[k]]["width"] for k in ((j, i - 1), (j, i)) if k in vt]
        if not ws:
            return 0.0
        return max(ws) if j in (0, nu) else max(ws) / 2.0

    def hext(i, j):
        ws = [types[ht[k]]["width"] for k in ((i, j - 1), (i, j)) if k in ht]
        if not ws:
            return 0.0
        return max(ws) if i in (0, nz) else max(ws) / 2.0
    vertical = p["join"] == "vertical"
    vmax, hmax = [0.0] * (nu + 1), [0.0] * (nz + 1)   # 격자선마다 가장 넓은 멀리언 폭: 이웃 선의 멀리언끼리 겹치면 거절한다
    for (j, _i), t in vt.items():
        vmax[j] = max(vmax[j], types[t]["width"])
    for (i, _j), t in ht.items():
        hmax[i] = max(hmax[i], types[t]["width"])
    for xs, wm, what in ((us, vmax, "세로"), (zs, hmax, "가로")):
        n = len(xs) - 1
        for k in range(n):
            right = wm[k] if k == 0 else wm[k] / 2.0
            left = wm[k + 1] if k + 1 == n else wm[k + 1] / 2.0
            if xs[k + 1] - xs[k] - right - left < MIN_GAP:
                raise ValueError(f"{what} 격자 간격이 멀리언 폭보다 좁다({fmt(xs[k])} ~ {fmt(xs[k + 1])})")
    pts = [pt(u) for u in us]
    shapes = {}

    def trim(key, u0, u1, z0, z1, ou, k, org, e, h, band=0.0, ecov=None):
        """상단 윤곽으로 자른다: "keep"(전부 아래: 이전과 같은 각기둥) | None(전부 위: 없음) | 위 z. 잘렸으면 모양을 shapes 에 둔다.
        u 구간 [u0, u1](축 위 거리)을 틀 좌표 a = (u - ou)·k 로 옮기고, 원점 org·방향 e(평면)·오른쪽 법선 방향 -h ~ h 로 돌출한다.
        band > 0 이면 윗면 외곽 멀리언: 윤곽에서 아래로 band 폭의 띠."""
        if band > 0.0:
            polys = _band(prof, u0, u1, band, z0)
        else:
            if WT.zmin(prof, u0, u1) >= z1 - 1e-9:
                return "keep"
            polys = _under(prof, u0, u1, z0, z1)
        if not polys:
            return None
        ab = tuple(tuple(((u - ou) * k + 0.0, z) for u, z in pl) for pl in polys)
        extra = (tuple(tuple(ecov(pl)) for pl in polys),) if ecov is not None else ((u1 - u0) * k if key[0] == "H" else max(z for pl in polys for _, z in pl) - z0,)
        shapes[key] = (ab, (org[0], org[1], 0.0), (e[0], e[1], 0.0), (0.0, 0.0, 1.0), (e[1], -e[0], 0.0), -h, h, extra)
        return max(z for pl in polys for _, z in pl)
    mullions = []
    for j in range(nu + 1):   # 세로 멀리언
        for i in range(nz):
            t = vt.get((j, i))
            if t is None:
                continue
            w, d = types[t]["width"], types[t]["depth"]
            uc = w / 2.0 if j == 0 else (length - w / 2.0 if j == nu else us[j])
            c = pt(uc) if j in (0, nu) else pts[j]
            tx, ty = tg(uc)
            a, b = (c[0] - tx * w / 2.0, c[1] - ty * w / 2.0), (c[0] + tx * w / 2.0, c[1] + ty * w / 2.0)
            z0, z1 = zs[i], zs[i + 1]
            if not vertical:
                z0, z1 = z0 + hext(i, j), z1 - hext(i + 1, j)
            if z1 - z0 < MIN_GAP:
                raise ValueError(f"가로 격자 간격이 멀리언 폭보다 좁다(z {fz[i]})")
            key = f"V{fu[j]}:{fz[i]}"
            if prof is not None:
                r = trim(key, uc - w / 2.0, uc + w / 2.0, z0, z1, uc - w / 2.0, 1.0, a, (tx, ty), d / 2.0)
                if r is None:
                    continue
                if r != "keep":
                    z1 = r
            mullions.append((key, "V", t, quad(a, b, d / 2.0), z0, z1))
    for i in range(nz + 1):   # 가로 멀리언
        for j in range(nu):
            t = ht.get((i, j))
            if t is None:
                continue
            w, d = types[t]["width"], types[t]["depth"]
            pa, pb = pts[j], pts[j + 1]
            cx, cy, n = _norm2(pa[0], pa[1], pb[0], pb[1])
            ta, tb = (vext(j, i), vext(j + 1, i)) if vertical else (0.0, 0.0)
            if n - ta - tb < MIN_GAP:
                raise ValueError(f"세로 격자 간격이 멀리언 폭보다 좁다(u {fu[j]})")
            a, b = (pa[0] + cx * ta, pa[1] + cy * ta), (pb[0] - cx * tb, pb[1] - cy * tb)
            z = zs[i]
            z0, z1 = (0.0, w) if i == 0 else ((height - w, height) if i == nz else (z - w / 2.0, z + w / 2.0))
            key = f"H{fz[i]}:{fu[j]}"
            if prof is not None:
                k = n / (us[j + 1] - us[j])
                u0, u1 = us[j] + ta / k, us[j + 1] - tb / k
                if i == nz and WT.zmin(prof, u0, u1) < height - 1e-9:   # 윗면 외곽: 윤곽을 따라가는 띠
                    r = trim(key, u0, u1, 0.0, height, us[j], k, pa, (cx, cy), d / 2.0, band=w)
                    if r is None:
                        continue
                    z0 = min(zz for pl in shapes[key][0] for _, zz in pl)
                    z1 = r
                else:
                    r = trim(key, u0, u1, z0, z1, us[j], k, pa, (cx, cy), d / 2.0)
                    if r is None:
                        continue
                    if r != "keep":
                        z1 = r
            mullions.append((key, "H", t, quad(a, b, d / 2.0), z0, z1))
    half = p["panel_thickness"] / 2.0
    panels = []
    for j in range(nu):
        pa, pb = pts[j], pts[j + 1]
        q = quad(pa, pb, half)
        for i in range(nz):
            key = f"P{fu[j]}:{fz[i]}"
            kind = po.get(key, DEFAULT_PANEL)
            if kind == "empty":
                continue
            cover = ((j, i) in vt, (j + 1, i) in vt, (i, j) in ht, (i + 1, j) in ht)
            trims = (vext(j, i) if (j, i) in vt else 0.0, vext(j + 1, i) if (j + 1, i) in vt else 0.0)
            z1 = zs[i + 1]
            if prof is not None:
                ua, ub, za, zb = us[j], us[j + 1], zs[i], zs[i + 1]
                top = (nz, j) in ht

                def ecov(pl, ua=ua, ub=ub, za=za, zb=zb, cover=cover, top=top):
                    return _edge_cover(pl, ((0, ua), (0, ub), (1, za), (1, zb)), cover, lambda _a, _b: top)
                r = trim(key, ua, ub, za, zb, ua, _norm2(pa[0], pa[1], pb[0], pb[1])[2] / (ub - ua), pa,
                         _norm2(pa[0], pa[1], pb[0], pb[1])[:2], half, ecov=ecov)
                if r is None:
                    continue
                if r != "keep":
                    z1 = r
            panels.append((key, kind, q, zs[i], z1, cover, trims))
    out = {"length": length, "height": height, "us": tuple(us), "zs": tuple(zs), "pts": tuple(pts),
           "mullions": tuple(mullions), "panels": tuple(panels)}
    if shapes:
        out["shapes"] = shapes
    return out


# ---------------------------------------------------------------- 모양 부속(W2 열 번째 PR): 상단 윤곽으로 자르기·경사 커튼 시스템
def _area2(pl) -> float:
    return sum(pl[k][0] * pl[(k + 1) % len(pl)][1] - pl[(k + 1) % len(pl)][0] * pl[k][1] for k in range(len(pl)))


def _clean(pl) -> list:
    """이어진 같은 점(고리 끝 포함)을 하나로."""
    out = []
    for q in pl:
        if not out or abs(q[0] - out[-1][0]) > 1e-9 or abs(q[1] - out[-1][1]) > 1e-9:
            out.append(q)
    while len(out) > 1 and abs(out[0][0] - out[-1][0]) <= 1e-9 and abs(out[0][1] - out[-1][1]) <= 1e-9:
        out.pop()
    return out


def _under(prof, ua, ub, za, zb) -> list:
    """칸 [ua, ub] x [za, zb] 중 상단 윤곽 아래 부분의 다각형들 [(u, z), ...](반시계). 윤곽이 za 아래로 내려가는 곳에서 나뉜다."""
    pts = WT.clip(prof, ua, ub)
    ch = []
    for k, (u, t) in enumerate(pts):
        if k:
            u0, t0 = pts[k - 1]
            if u - u0 > 1e-12:
                for s in sorted(u0 + (u - u0) * (lv - t0) / (t - t0) for lv in (za, zb) if (t0 - lv) * (t - lv) < 0):
                    ch.append((s, t0 + (t - t0) * (s - u0) / (u - u0)))
        ch.append((u, t))
    ch = [(u, min(max(t, za), zb)) for u, t in ch]
    polys, run = [], []

    def close():
        if run:
            pl = _clean([(run[0][0], za), (run[-1][0], za)] + run[::-1])
            if len(pl) >= 3 and _area2(pl) / 2.0 >= AREA_MIN:
                polys.append(tuple(pl))
            run.clear()
    for k in range(1, len(ch)):
        (u0, y0), (u1, y1) = ch[k - 1], ch[k]
        inside = (y0 + y1) / 2.0 > za + 1e-9 if u1 - u0 > 1e-12 else (max(y0, y1) > za + 1e-9)
        if inside:
            if not run:
                run.append((u0, y0))
            run.append((u1, y1))
        else:
            close()
    close()
    return polys


def _band(prof, ua, ub, w, zlo) -> list:
    """윗면 외곽 멀리언: [ua, ub] 에서 윤곽 t(u) 아래 폭 w 의 띠(아래 끝은 zlo 위로) 다각형들."""
    top = [(u, t) for u, t in WT.clip(prof, ua, ub)]
    pl = _clean([(u, max(t - w, zlo)) for u, t in top] + [(u, t) for u, t in top[::-1]])
    if len(pl) < 3 or _area2(pl) / 2.0 < AREA_MIN:
        return []
    return [tuple(pl)]


def _edge_cover(pl, sides, cover, other) -> list:
    """다각형 변마다 멀리언이 덮는가: 칸의 네 변(sides = ((축, 값) x 4): 시작·끝·아래·위)에 놓인 변은 cover, 그 밖(윤곽·외곽 변)은 other(a, b)."""
    out = []
    n = len(pl)
    for k in range(n):
        a, b = pl[k], pl[(k + 1) % n]
        for s, (ax, v) in enumerate(sides):
            if abs(a[ax] - v) < 1e-6 and abs(b[ax] - v) < 1e-6:
                out.append(bool(cover[s]))
                break
        else:
            out.append(bool(other(a, b)))
    return out


def _clip_convex(sub, win) -> list:
    """다각형 sub 를 볼록 다각형 win(반시계)으로 자른다(Sutherland-Hodgman)."""
    out = list(sub)
    n = len(win)
    for k in range(n):
        if not out:
            break
        (ax, ay), (bx, by) = win[k], win[(k + 1) % n]
        inp, out = out, []

        def side(q):
            return (bx - ax) * (q[1] - ay) - (by - ay) * (q[0] - ax)
        for m in range(len(inp)):
            p, q = inp[m - 1], inp[m]
            sp, sq = side(p), side(q)
            if sq >= 0.0:
                if sp < 0.0:
                    t = sp / (sp - sq)
                    out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
                out.append(q)
            elif sp >= 0.0:
                t = sp / (sp - sq)
                out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return _clean(out)


CONVEX_TOL = 1e-4          # 볼록 판정의 꺾임 허용(sin, 약 0.006도): 도면에서 0.1mm 로 반올림한 거의 한 직선 위 점을 오목으로 보지 않는다


def _convex(pl) -> bool:
    n = len(pl)
    for k in range(n):
        a, b, c = pl[k], pl[(k + 1) % n], pl[(k + 2) % n]
        l1 = sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
        l2 = sqrt((c[0] - b[0]) ** 2 + (c[1] - b[1]) ** 2)
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) < -CONVEX_TOL * l1 * l2:
            return False
    return True


def _inset(pl, d) -> list | None:
    """볼록 다각형(반시계)을 변마다 d 만큼 안으로 들인 다각형. 변이 사라지거나 뒤집히면 None."""
    n = len(pl)
    lines = []
    for k in range(n):
        (ax, ay), (bx, by) = pl[k], pl[(k + 1) % n]
        tx, ty, ln = _norm2(ax, ay, bx, by)
        nx, ny = -ty, tx
        lines.append((ax + nx * d, ay + ny * d, tx, ty))
    out = []
    for k in range(n):
        px, py, ux, uy = lines[k - 1]
        qx, qy, vx, vy = lines[k]
        den = ux * vy - uy * vx
        if abs(den) < 1e-12:
            return None
        s = ((qx - px) * vy - (qy - py) * vx) / den
        out.append((px + ux * s, py + uy * s))
    for k in range(n):   # 들인 변의 방향이 원래 변과 같아야 한다(뒤집히지 않았다)
        (ax, ay), (bx, by) = pl[k], pl[(k + 1) % n]
        (cx, cy), (dx, dy) = out[k], out[(k + 1) % n]
        if (bx - ax) * (dx - cx) + (by - ay) * (dy - cy) <= 1e-9:
            return None
    return out


def slope_frame(p) -> tuple:
    """경사 커튼 시스템의 평면 틀: (원점 (x, y, 0), e1, e2, n, 외곽(틀 좌표 a, b, 반시계)). e1 = 기준선 방향, e2 = 면 위 오르는 방향, n = e1 x e2(위쪽)."""
    x1, y1, x2, y2 = p["axis"]
    tx, ty, ln = _norm2(x1, y1, x2, y2)
    mx, my = -ty, tx
    sn, cs = AG.sincos(AG.PI * p["slope"] / 180.0)
    bnd = [((x - x1) * tx + (y - y1) * ty, ((x - x1) * mx + (y - y1) * my) / cs) for x, y in p["boundary"]]
    if _area2(bnd) < 0:
        bnd = bnd[::-1]
    return ((x1, y1, 0.0), (tx, ty, 0.0), (mx * cs, my * cs, sn), (ty * sn, -tx * sn, cs), bnd)


def sloped_problem(p) -> str | None:
    x1, y1, x2, y2 = p["axis"]
    if (x2 - x1) ** 2 + (y2 - y1) ** 2 < 1.0:
        return "기준선의 시작·끝점이 1mm 이상 떨어져야 한다"
    if not 0.0 <= p["slope"] <= SLOPE_MAX:
        return f"물매 각은 0 ~ {SLOPE_MAX:g}도다"
    bnd = slope_frame(p)[4]
    if not 3 <= len(bnd) <= BOUNDARY_MAX:
        return f"외곽은 점 3 ~ {BOUNDARY_MAX}개다"
    if len(_clean(bnd)) != len(bnd) or _area2(bnd) / 2.0 < 1e4:
        return "외곽이 퇴화했다(같은 점 또는 넓이 0.01m² 미만)"
    if not _convex(bnd):
        return "외곽은 볼록 다각형이어야 한다(오목하면 경사 커튼 시스템 여러 개로 나누세요)"
    a = [q[0] for q in bnd]
    b = [q[1] for q in bnd]
    if max(a) - min(a) > LEN_MAX or max(b) - min(b) > LEN_MAX:
        return f"외곽 크기가 {LEN_MAX:g}mm 를 넘는다"
    return None


def _layout_sloped(p) -> dict:
    why = sloped_problem(p)
    if why:
        raise ValueError(why)
    org, e1, e2, nv, bnd = slope_frame(p)
    amin, bmin = min(q[0] for q in bnd), min(q[1] for q in bnd)
    length, height = max(q[0] for q in bnd) - amin, max(q[1] for q in bnd) - bmin
    for d, rule, size in (("u", p["u_grid"], length), ("v", p["v_grid"], height)):
        if rule_count(rule, size) > LINES_MAX + 1:
            raise ValueError(f"{d} 격자선이 {LINES_MAX}개를 넘는다")
    us = [0.0] + grid_lines(p["u_grid"], p.get("u_add", ()), p.get("u_remove", ()), length) + [length]
    zs = [0.0] + grid_lines(p["v_grid"], p.get("v_add", ()), p.get("v_remove", ()), height) + [height]
    nu, nz = len(us) - 1, len(zs) - 1
    if nu - 1 > LINES_MAX or nz - 1 > LINES_MAX:
        raise ValueError(f"격자선이 방향마다 {LINES_MAX}개를 넘는다")
    if nu * nz > CELLS_MAX:
        raise ValueError(f"칸 수({nu * nz})가 {CELLS_MAX}개를 넘는다")
    A = [amin + u for u in us]
    B = [bmin + z for z in zs]
    types = p["types"]
    mo = p.get("mullion_overrides", {})
    po = p.get("panel_overrides", {})
    inner, border = p["mullion"]["interior"], p["mullion"].get("border")
    fu = [fmt(u) for u in us]
    fz = [fmt(z) for z in zs]

    def mtype(key, default):
        t = mo.get(key, default)
        return t if t else None
    nb = len(bnd)
    et = {k: t for k in range(nb) if (t := mtype(f"E{k}:0", border)) is not None}
    vt = {(j, i): t for j in range(1, nu) for i in range(nz) if (t := mtype(f"V{fu[j]}:{fz[i]}", inner)) is not None}
    ht = {(i, j): t for i in range(1, nz) for j in range(nu) if (t := mtype(f"H{fz[i]}:{fu[j]}", inner)) is not None}
    bw = max((types[t]["width"] for t in et.values()), default=0.0)
    win = _inset(bnd, bw) if bw > 0 else bnd
    if win is None or _area2(win) / 2.0 < AREA_MIN:
        raise ValueError("외곽 멀리언 폭을 들이면 외곽 안쪽이 남지 않는다")
    vertical = p["join"] == "vertical"
    shapes = {}

    def put(key, polys, h, extra):
        """틀 좌표 다각형들을 모양으로 두고 (평면 경계 상자 사각형, 바닥 z, 윗면 z)를 돌려준다."""
        shapes[key] = (tuple(tuple((x + 0.0, y + 0.0) for x, y in pl) for pl in polys), org, e1, e2, nv, -h, h, extra)
        xs, ys, zz = [], [], []
        for pl in polys:
            for a, b in pl:
                for s in (-h, h):
                    xs.append(org[0] + a * e1[0] + b * e2[0] + s * nv[0])
                    ys.append(org[1] + a * e1[1] + b * e2[1] + s * nv[1])
                    zz.append(b * e2[2] + s * nv[2])
        x0, x1_, y0, y1_ = min(xs), max(xs), min(ys), max(ys)
        return ((x0, y0), (x1_, y0), (x1_, y1_), (x0, y1_)), min(zz), max(zz)

    def rect(a0, a1, b0, b1):
        return [(a0, b0), (a1, b0), (a1, b1), (a0, b1)]

    def half_w(tbl, keys):
        ws = [types[tbl[k]]["width"] for k in keys if k in tbl]
        return max(ws) / 2.0 if ws else 0.0
    mullions = []
    for j in range(1, nu):   # 세로(b 방향) 멀리언
        for i in range(nz):
            t = vt.get((j, i))
            if t is None:
                continue
            w, d = types[t]["width"], types[t]["depth"]
            b0, b1 = B[i], B[i + 1]
            if not vertical:
                b0 += half_w(ht, ((i, j - 1), (i, j))) if 0 < i else 0.0
                b1 -= half_w(ht, ((i + 1, j - 1), (i + 1, j))) if i + 1 < nz else 0.0
            pl = _clip_convex(rect(A[j] - w / 2.0, A[j] + w / 2.0, b0, b1), win)
            if len(pl) < 3 or _area2(pl) / 2.0 < AREA_MIN:
                continue
            key = f"V{fu[j]}:{fz[i]}"
            q, z0, z1 = put(key, [pl], d / 2.0, (max(y for _, y in pl) - min(y for _, y in pl),))
            mullions.append((key, "V", t, q, z0, z1))
    for i in range(1, nz):   # 가로(a 방향) 멀리언
        for j in range(nu):
            t = ht.get((i, j))
            if t is None:
                continue
            w, d = types[t]["width"], types[t]["depth"]
            a0, a1 = A[j], A[j + 1]
            if vertical:
                a0 += half_w(vt, ((j, i - 1), (j, i))) if 0 < j else 0.0
                a1 -= half_w(vt, ((j + 1, i - 1), (j + 1, i))) if j + 1 < nu else 0.0
            pl = _clip_convex(rect(a0, a1, B[i] - w / 2.0, B[i] + w / 2.0), win)
            if len(pl) < 3 or _area2(pl) / 2.0 < AREA_MIN:
                continue
            key = f"H{fz[i]}:{fu[j]}"
            q, z0, z1 = put(key, [pl], d / 2.0, (max(x for x, _ in pl) - min(x for x, _ in pl),))
            mullions.append((key, "H", t, q, z0, z1))
    for k in range(nb):   # 외곽 멀리언: 외곽 변과 들인 변 사이의 띠
        t = et.get(k)
        if t is None:
            continue
        w, d = types[t]["width"], types[t]["depth"]
        inn = _inset(bnd, w)
        if inn is None:
            raise ValueError("외곽 멀리언 폭을 들이면 외곽 안쪽이 남지 않는다")
        pl = [bnd[k], bnd[(k + 1) % nb], inn[(k + 1) % nb], inn[k]]
        key = f"E{k}:0"
        ln = sqrt((bnd[(k + 1) % nb][0] - bnd[k][0]) ** 2 + (bnd[(k + 1) % nb][1] - bnd[k][1]) ** 2)
        q, z0, z1 = put(key, [pl], d / 2.0, (ln,))
        mullions.append((key, "E", t, q, z0, z1))
    half = p["panel_thickness"] / 2.0

    def on_edge(a, b):
        for k in range(nb):
            p0, p1 = bnd[k], bnd[(k + 1) % nb]
            ex, ey, ln = _norm2(p0[0], p0[1], p1[0], p1[1])
            if all(abs((q[0] - p0[0]) * ey - (q[1] - p0[1]) * ex) < 1e-6 for q in (a, b)):
                return k in et
        return False
    panels = []
    for j in range(nu):
        for i in range(nz):
            pl = _clip_convex(rect(A[j], A[j + 1], B[i], B[i + 1]), bnd)
            if len(pl) < 3 or _area2(pl) / 2.0 < AREA_MIN:
                continue
            key = f"P{fu[j]}:{fz[i]}"
            kind = po.get(key, DEFAULT_PANEL)
            if kind == "empty":
                continue
            cover = ((j, i) in vt, (j + 1, i) in vt, (i, j) in ht, (i + 1, j) in ht)
            ec = _edge_cover(pl, ((0, A[j]), (0, A[j + 1]), (1, B[i]), (1, B[i + 1])), cover, on_edge)
            q, z0, z1 = put(key, [pl], half, ((tuple(ec),),))
            panels.append((key, kind, q, z0, z1, cover, (0.0, 0.0)))
    zr = [z for m in mullions for z in m[4:6]] + [z for x in panels for z in x[3:5]]
    return {"length": length, "height": height, "us": tuple(us), "zs": tuple(zs), "pts": (), "mullions": tuple(mullions), "panels": tuple(panels),
            "shapes": shapes, "zr": (min(zr), max(zr)) if zr else (0.0, 0.0), "form": "sloped"}


def zrange(lay) -> tuple:
    """배치의 (바닥, 윗면) z(커튼월 바닥 기준): 경사 커튼 시스템은 부속의 범위, 수직 커튼월은 (0, 높이)."""
    return lay.get("zr", (0.0, lay["height"]))


def shape_mesh(sh, dz: float = 0.0) -> tuple:
    """모양 부속 -> (꼭짓점 (x, y, z + dz), 면(고리 하나씩의 튜플)). 다각형마다 윗면(+n)·아랫면·옆면의 닫힌 각기둥."""
    polys, o, e1, e2, nv, h0, h1, _x = sh
    verts, faces = [], []
    for pl in polys:
        base = len(verts)
        m = len(pl)
        for h in (h0, h1):
            for a, b in pl:
                verts.append((o[0] + a * e1[0] + b * e2[0] + h * nv[0], o[1] + a * e1[1] + b * e2[1] + h * nv[1], o[2] + dz + a * e1[2] + b * e2[2] + h * nv[2]))
        faces.append((tuple(base + m + k for k in range(m)),))
        faces.append((tuple(base + k for k in range(m - 1, -1, -1)),))
        for k in range(m):
            k1 = (k + 1) % m
            faces.append(((base + k, base + k1, base + m + k1, base + m + k),))
    return tuple(verts), tuple(faces)


def shape_area(sh) -> float:
    """모양 부속의 면적(틀 좌표 넓이, mm²)."""
    return sum(_area2(pl) / 2.0 for pl in sh[0])


def part_length(lay, m) -> float:
    """멀리언 한 개의 길이(mm): 모양 부속이면 덧의 길이, 아니면 세로는 높이 차·가로는 현 길이."""
    sh = lay.get("shapes", {}).get(m[0])
    if sh is not None:
        return sh[7][0]
    q = m[3]
    return (m[5] - m[4]) if m[1] == "V" else sqrt((q[1][0] - q[0][0]) ** 2 + (q[1][1] - q[0][1]) ** 2)


def panel_area(lay, x) -> float:
    """패널 한 개의 면적(mm²)."""
    sh = lay.get("shapes", {}).get(x[0])
    if sh is not None:
        return shape_area(sh)
    q = x[2]
    return sqrt((q[1][0] - q[0][0]) ** 2 + (q[1][1] - q[0][1]) ** 2) * (x[4] - x[3])


def panel_width(lay, x) -> float:
    """패널 폭(mm): 모양 부속이면 틀 a 방향 폭."""
    sh = lay.get("shapes", {}).get(x[0])
    if sh is not None:
        xs = [a for pl in sh[0] for a, _ in pl]
        return max(xs) - min(xs)
    q = x[2]
    return sqrt((q[1][0] - q[0][0]) ** 2 + (q[1][1] - q[0][1]) ** 2)


def problem(p) -> str | None:
    """배치를 만들 수 없으면 이유 문장."""
    try:
        layout(p)
    except ValueError as ex:
        return str(ex)
    except (KeyError, TypeError, ZeroDivisionError) as ex:
        return f"배치를 계산할 수 없다({type(ex).__name__})"
    return None


def mullion_keys(lay) -> set:
    return {m[0] for m in lay["mullions"]}


def all_keys(p) -> tuple:
    """(있을 수 있는 멀리언 키 집합(지운 것·없는 외곽 포함), 패널 키 집합(empty 포함))."""
    if p.get("form") == "sloped":   # 경사 커튼 시스템: 외곽 상자 크기의 격자 + 외곽 변 멀리언 "E<변>:0"
        bnd = slope_frame(p)[4]
        length = max(q[0] for q in bnd) - min(q[0] for q in bnd)
        height = max(q[1] for q in bnd) - min(q[1] for q in bnd)
        extra = {f"E{k}:0" for k in range(len(bnd))}
    else:
        length, height, extra = frame(p)[0], p["height"], set()
    us = [0.0] + grid_lines(p["u_grid"], p.get("u_add", ()), p.get("u_remove", ()), length) + [length]
    zs = [0.0] + grid_lines(p["v_grid"], p.get("v_add", ()), p.get("v_remove", ()), height) + [height]
    fu, fz = [fmt(u) for u in us], [fmt(z) for z in zs]
    mk = {f"V{u}:{z}" for u in fu for z in fz[:-1]} | {f"H{z}:{u}" for z in fz for u in fu[:-1]} | extra
    pk = {f"P{u}:{z}" for u in fu[:-1] for z in fz[:-1]}
    return mk, pk


def prune(p) -> dict:
    """격자가 바뀌어 더는 없는 칸을 가리키는 재정의를 뺀 params(바꿀 게 없으면 같은 객체). 빈 재정의 키는 지운다."""
    mk, pk = all_keys(p)
    out = p
    for name, keys in (("mullion_overrides", mk), ("panel_overrides", pk)):
        cur = p.get(name)
        if cur is None:
            continue
        nv = {k: v for k, v in cur.items() if k in keys}
        if nv != cur:
            out = dict(out)
            if nv:
                out[name] = nv
            else:
                out.pop(name)
    return out
