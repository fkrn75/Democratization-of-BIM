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
"""
from __future__ import annotations

from math import sqrt

from dobim_core import arcgeom as AG

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
    why = axis_problem(p)
    if why:
        raise ValueError(why)
    length, pt, tg, ai = frame(p)
    height = p["height"]
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
            mullions.append((f"V{fu[j]}:{fz[i]}", "V", t, quad(a, b, d / 2.0), z0, z1))
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
            mullions.append((f"H{fz[i]}:{fu[j]}", "H", t, quad(a, b, d / 2.0), z0, z1))
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
            panels.append((key, kind, q, zs[i], zs[i + 1], cover, trims))
    return {"length": length, "height": height, "us": tuple(us), "zs": tuple(zs), "pts": tuple(pts),
            "mullions": tuple(mullions), "panels": tuple(panels)}


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
    length = frame(p)[0]
    us = [0.0] + grid_lines(p["u_grid"], p.get("u_add", ()), p.get("u_remove", ()), length) + [length]
    zs = [0.0] + grid_lines(p["v_grid"], p.get("v_add", ()), p.get("v_remove", ()), p["height"]) + [p["height"]]
    fu, fz = [fmt(u) for u in us], [fmt(z) for z in zs]
    mk = {f"V{u}:{z}" for u in fu for z in fz[:-1]} | {f"H{z}:{u}" for z in fz for u in fu[:-1]}
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
