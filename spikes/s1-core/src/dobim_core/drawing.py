# SPDX-License-Identifier: MPL-2.0
"""증분 벡터 도면 질의 (스파이크 S3, 버릴 코드).

벽 의도 데이터(축선·두께·높이·조인·문)에서 평면·단면·입면의 2D 선분을 직접 만든다 ("HLR-lite").
queries.py 와 같은 규칙을 따른다: 순수 함수, 시각·난수·파일 접근 금지, 플랫폼 libm(sin, cos, hypot) 금지(sqrt 와 사칙연산만).
그래서 같은 입력이면 어느 프로세스에서나 같은 선분이 나오고, RefGraph(캐시 없음)와 Graph(증분)의 결과를 비교할 수 있다.

캐시 단위 (아키텍처 5·7절의 view.cut_lines(V,E) / view.proj_lines(V,E) 에 해당):
    wall_solid(w)            뷰와 무관한 벽 형상(조인 보정 범위 + 문 개구부로 나눈 띠(band))
    plan_pieces(V,w)         평면 절단 조각          vpieces(V,w)   단면·입면 조각(뷰 좌표)
    *_bbox(V,w), *_nbrs(V,w) 뷰 안 경계 상자와 가릴 수 있는 이웃 (이웃 집합이 같으면 전파가 멈춘다)
    plan_wall_lines(V,w)     벽 하나가 뷰에 기여하는 선분 / vwall_lines(V,w)
    view_lines(V)            뷰 하나의 최종 선분(겹침 병합 포함)

좌표: 평면은 (x, y) mm 위가 +y. 단면·입면은 (s, z): s 는 오른쪽, z 는 위. 가림 판정용 깊이 w 는 보는 사람에서 멀어질수록 크다.
알려진 한계는 spikes/s3-drawings/report.md 에 적었다.
"""
from __future__ import annotations

import hashlib
import json
from math import sqrt

from dobim_core import kinds as K
from dobim_core import queries as Q
from dobim_core import solids as SO
from dobim_core import wallcut as WC
from dobim_core import walltop as WT
from dobim_core.family import spec as FS
from dobim_core.graph import CLEAN

EPS = 0.01          # 기하 허용 오차(mm): 같은 선·같은 점 판정
EPS_V = 0.5         # 가림 판정에서 "충분히 앞"으로 칠 깊이 차(mm)
SEG_MIN = 0.05      # 이보다 짧은 선분은 버린다
BIG = 1.0e9
ARC_N = 8           # 문 회전 호의 분할 수(90도당)
CIRC_N = 24         # 원 단면을 다각형으로 근사할 때 분할 수
PI = 3.141592653589793
NBR_PAD = 1.0       # 이웃 판정 경계 상자 여유(mm)
NBR_CELL = 4000.0   # 이웃 판정 격자 색인의 칸 폭(mm). 결과는 칸 폭과 무관하다(후보만 줄인다)
NBR_MAX_CELLS = 256  # 상자 하나가 이보다 많은 칸에 걸치면 칸에 넣지 않고 늘 후보로 본다
CORNER_FILL = Q.CORNER_EXTEND  # L 조인 모서리는 코어 형상(Q.wall_extent)이 닫는다: 도면과 IFC 가 같다
PRIORITY = {"cut": 3, "symbol": 2, "proj": 1}

ELEV_DIR = {"N": (0.0, -1.0), "E": (-1.0, 0.0), "S": (0.0, 1.0), "W": (1.0, 0.0)}   # 건물을 바라보는 방향
VIEW_TYPES = ("plan", "section", "elevation", "site")


# ================================================================ 범위 질의
def is_drawn_wall(g, w) -> bool:
    """도면이 그리는 벽인가: 종류가 벽이고 저작(authored)일 때만. 외래(foreign)는 형상 정보가 없고(params 에 axis 가 없다),
    분리(detached)된 벽의 params 는 외부 도구가 형상을 바꾸기 전의 낡은 의도라 그리면 틀린 선이 나온다(ADR-0005)."""
    row = Q.el(g, w)
    return row is not None and row["kind"] == "wall" and row["origin"] == "authored"


def _raw_walls(g):
    """벽 종류 요소 전체(출처 무관)."""
    out = []
    for b in range(Q.NB):
        out.extend(Q.bucket_ids(g, "wall", b))
    return tuple(sorted(out))


def all_walls(g):
    return tuple(w for w in g.q(_raw_walls) if g.q(is_drawn_wall, w))


def all_views(g):
    out = []
    for b in range(Q.NB):
        out.extend(Q.bucket_ids(g, "view", b))
    return tuple(sorted(out))


def level_walls(g, lv):
    # on_level 에는 외래 요소(kind foreign)와 패밀리 인스턴스(finst)도 걸린다: 그리는 벽만 남긴다
    return tuple(w for w in sorted(Q.rel_in(g, "on_level", lv)) if g.q(is_drawn_wall, w))


def view_omitted(g, v):
    """뷰가 그리지 않고 생략한 모델 요소 개수 {"foreign": 외래 요소, "detached": 분리된 벽}. 생략은 조용하지 않게 응답에 알린다.
    외래 요소는 형상 기반 경로(foreign_lines)가 그리지 못한 것(형상 없음·그리지 않는 종류)만 센다. 외래 층은 레벨이라 세지 않는다.
    평면은 그 레벨에 놓인 것만, 단면·입면은 모델 전체를 센다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return {"foreign": 0, "detached": 0}
    if cfg[0] == "plan":
        ids = () if cfg[1] is None else Q.rel_in(g, "on_level", cfg[1])
        foreign = foreign_omitted(g, ids)
        detached = sum(1 for e in ids if (r := Q.el(g, e)) is not None and r["kind"] == "wall" and r["origin"] == "detached")
        return {"foreign": foreign, "detached": detached}
    detached = sum(1 for w in g.q(_raw_walls) if Q.el(g, w)["origin"] == "detached")
    return {"foreign": foreign_omitted(g, g.q(_raw_foreign)) if Q.count(g, "foreign") else 0, "detached": detached}


def view_cfg(g, v):
    """뷰 설정을 계산용 튜플로 푼다.
    평면: ("plan", 레벨, 절단 z, 보이는 깊이, 축척) + 모드가 있으면 (모드,): "rcp" 반사 천장 평면(보이는 깊이는 절단면 위로), "gross"·"rooms" 면적 평면(W1 1-C 3차)
    범례 뷰(legend)는 모델을 자르지 않으므로 None 이다(모델 질의는 고아 뷰처럼 비고, viewplans 가 범례를 그린다)
    배치도: ("site", None, 0, 0, 축척)  (모델 전체를 위에서 본 대지·건물 외곽 뷰)
    그 밖: (종류, ox, oy, 보는방향 dx, dy, 오른쪽 rx, ry, near, far, 절단 여부, 축척)"""
    row = Q.el(g, v)
    if row is None or row["kind"] != "view":
        return None
    p = row["params"]
    t, scale = p["type"], p["scale"]
    if t == "site":
        return ("site", None, 0.0, 0.0, scale)
    if t == "legend":
        return None
    if t == "plan":
        mode = ("rcp",) if p.get("rcp") else (p["area"],) if "area" in p else ()   # 모드가 없는 평면은 이전과 같은 5칸이다
        lv = p["level"]
        lrow = Q.el(g, lv)
        if not Q.plan_level_ok(lrow):   # 없거나 레벨이 아닌 요소(예전 데이터의 잘못된 참조)면 고아 뷰로 그린다(터지지 않게)
            return ("plan", None, 0.0, 0.0, scale) + mode
        if lrow["kind"] == "level":
            _, elev = g.q(Q.level_info, lv)
        else:   # 외래 IFC 층(1단계 외래 도면 경로): 고도는 IFC 배치에서 읽는다
            fg = Q.fgeom(g, lv)
            if fg is None or fg[0] != "storey":
                return ("plan", None, 0.0, 0.0, scale) + mode
            elev = fg[1]
        return ("plan", lv, elev + p["cut_height"], p["view_depth"], scale) + mode
    return cfg_from_params(p)


def is_rcp(cfg) -> bool:
    """반사 천장 평면(W1 1-C 3차, VIEW-02)인가: 절단면에서 위를 본다(절단선은 평면과 같고, 투영은 절단면 위 보이는 깊이 안의 것만, 문·창 기호 없음)."""
    return cfg is not None and len(cfg) == 6 and cfg[5] == "rcp"


def cfg_from_params(p):
    """단면·입면 뷰 파라미터 -> 계산용 튜플(그래프 없이 파라미터만으로 정해진다: view.create 의 around 가 뷰를 만들기 전에 범위를 재는 데도 쓴다)."""
    scale = p["scale"]
    if p["type"] == "elevation":
        d = ELEV_DIR[p["dir"]]
        return ("elev", 0.0, 0.0, d[0], d[1], d[1], -d[0], -BIG, BIG, False, scale)
    x1, y1 = p["p1"]
    x2, y2 = p["p2"]
    dx, dy = x2 - x1, y2 - y1
    length = sqrt(dx * dx + dy * dy)
    lx, ly = dx / length, dy / length
    fx, fy = (-ly, lx) if not p.get("flip") else (ly, -lx)    # 보는 방향: 선의 왼쪽(기본) 또는 오른쪽
    return ("sec", x1, y1, fx, fy, fy, -fx, 0.0, p["depth"], True, scale)


def scope_walls(g, v):
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    if cfg[0] == "site":
        return ()
    if cfg[0] == "plan":
        if cfg[1] is None:
            return ()
        own = g.q(level_walls, cfg[1])
        extra = g.q(spanning_walls, cfg[1], cfg[2])
        return tuple(sorted(own + extra)) if extra else own
    return g.q(all_walls)


def spanning_walls(g, lv, cutz):
    """다른 레벨에 놓였지만 이 평면의 절단 높이를 지나가는 벽(24단계: 상단 레벨로 구속한 여러 층 벽). 절단만 더하고 투영은 더하지 않는다
    (아래층 벽의 윗면이 보이는 깊이 안에 있어도 그 층 평면 몫이다). 구속(top_level)이 있는 벽만 본다: height 만 큰 벽은 이전처럼
    제 레벨 평면에만 그린다(기존 모델의 도면이 바이트까지 같다)."""
    out = []
    for w in Q.tied_walls(g):
        if not g.q(is_drawn_wall, w):
            continue
        p = Q.el(g, w)["params"]
        wl = g.q(Q.wall_level, w)
        if wl == lv:
            continue
        _, elev = g.q(Q.level_info, wl)
        if elev + WT.base(p) + EPS < cutz < elev + _top_rel(p) - EPS:
            out.append(w)
    return tuple(out)


# ================================================================ 벽 형상 (뷰와 무관)
def wall_range(g, w):
    """벽 지역 x 범위: 코어의 벽 형상 범위(Q.wall_extent: 조인 자르기 + L 조인 모서리 연장)다. IFC 투영과 같은 값이라 도면과 IFC 형상이 같다."""
    return g.q(Q.wall_extent, w)


def hosted_opening(g, d, elev):
    """벽에 호스팅된 요소 하나가 벽에 내는 개구부 (u0, u1, z0, z1)(벽 지역 길이 방향 u, 절대 높이 z). 개구부가 없으면 None.
    호스팅되는 것은 문(door: offset ± width/2, 문턱 sill)만이 아니다: 패밀리 인스턴스(finst)는 sill 이 없고 평가된 개구부 범위(cut)와
    elevation 을 가진다(S3 가 모든 호스팅 요소를 문이라고 가정해 KeyError 로 죽던 자리). 그 밖의 종류는 개구부가 없다."""
    row = Q.el(g, d)
    if row is None:
        return None
    p = row["params"]
    if row["kind"] == "door":
        return (p["offset"] - p["width"] / 2.0, p["offset"] + p["width"] / 2.0, elev + p["sill"], elev + p["sill"] + p["height"])
    if row["kind"] == "finst":
        spec = g.q(Q.ifc_spec, d)
        cut = spec.get("cut")
        if not cut:
            return None   # 평가 오류이거나 개구부 없는 인스턴스
        return (spec["offset"] + cut["x"][0], spec["offset"] + cut["x"][1],
                elev + spec["elevation"] + cut["z"][0], elev + spec["elevation"] + cut["z"][1])
    return None


def wall_openings(g, w):
    """호스팅된 요소(문·창·패밀리 인스턴스)의 개구부: (u0, u1, z0, z1) 정렬 튜플. 벽 범위 안으로 자른다."""
    return _openings(g, w, False)


def wall_door_openings(g, w):
    """호스팅된 문(kind door)만의 개구부. 평면 문 기호(열린 문짝 + 호)는 이 개구부에만 그린다 — 창에 호를 그리면 문으로 읽힌다."""
    return _openings(g, w, True)


def _openings(g, w, doors_only):
    start, end, _ = g.q(Q.wall_trim, w)
    p = Q.el(g, w)["params"]
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    zb, zt = elev + WT.base(p), elev + _top_rel(p)
    out = []
    for d in g.q(Q.wall_hosted, w):
        if doors_only and Q.el(g, d)["kind"] != "door":
            continue
        box = g.q(hosted_opening, d, elev)
        if box is None:
            continue
        u0 = max(start, box[0])
        u1 = min(end, box[1])
        z0 = max(zb, box[2])
        z1 = min(zt, box[3])
        if u1 - u0 > EPS and z1 - z0 > EPS:
            out.append((u0, u1, z0, z1))
    if not doors_only:   # 다른 벽에 호스팅된 창이 이 벽도 함께 자르는 개구부(25단계 also_cuts). 없으면 이전과 같다
        for _f, box in g.q(Q.wall_xcuts, w):
            u0, u1 = max(start, box[0]), min(end, box[1])
            z0, z1 = max(zb, elev + box[2]), min(zt, elev + box[3])
            if u1 - u0 > EPS and z1 - z0 > EPS:
                out.append((u0, u1, z0, z1))
    if not doors_only:   # 모양 개구부(18단계)의 사각 부분: 아치 머리·원은 띠에서 속이 찬 것으로 두고 윤곽선만 따로 그린다(가림 근사)
        for box, _loop, _fill in g.q(wall_shape_openings, w):
            if box is not None and box[0] >= start - EPS and box[1] <= end + EPS:
                out.append(box)
    return tuple(sorted(out))


def _bands(start, end, zb, zt, ops):
    """높이를 개구부 경계로 나눠, 각 띠에서 막힌 구간을 뺀 속이 찬 u 구간을 만든다. 같은 구간이 이어지는 띠는 합친다."""
    zs = sorted({zb, zt, *(o[2] for o in ops), *(o[3] for o in ops)})
    bands = []
    for z0, z1 in zip(zs, zs[1:]):
        if z1 - z0 < EPS:
            continue
        mid = (z0 + z1) / 2.0
        cur, ivs = start, []
        for a, b in sorted((o[0], o[1]) for o in ops if o[2] < mid < o[3]):
            if a > cur + EPS:
                ivs.append((cur, a))
            cur = max(cur, b)
        if end > cur + EPS:
            ivs.append((cur, end))
        ivs = tuple(ivs)
        if bands and bands[-1][2] == ivs:
            bands[-1] = (bands[-1][0], z1, ivs)
        else:
            bands.append((z0, z1, ivs))
    return tuple(bands)


def _top_rel(p) -> float:
    """벽 윗면의 레벨 기준 높이: 상단 윤곽(21단계)이 있으면 가장 높은 점, 없으면 바닥 오프셋(22단계) + height."""
    return WT.top_rel(p)


def wall_solid(g, w):
    """(x, y, c, s, half, start, end, zb, zt, openings, bands, top). 벽 지역 (u,v) -> 세계 (x+u*c-v*s, y+u*s+v*c).
    top: 상단 윤곽(21단계)이 있으면 형상 범위로 자른 절대 높이 점들 ((u, z), ...), 없으면 None. 있으면 zt 는 가장 높은 점이고 띠(bands)는 zt 까지의
    사각 띠다(윤곽 위의 빈 곳을 빼지 않았다): 띠를 쓰는 곳은 wall_top_pieces(정확한 볼록 조각)·wall_top_steps(가림용 계단 근사)를 쓴다."""
    x, y, c, s, _ = g.q(Q.wall_frame, w)
    p = Q.el(g, w)["params"]
    start, end = g.q(wall_range, w)
    ops = g.q(wall_openings, w)
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    top = None
    b0 = elev + WT.base(p)   # 바닥 오프셋(22단계): 없으면 레벨 높이 그대로
    if "top_profile" in p:
        top = tuple((u, elev + z) for u, z in WT.clip(p["top_profile"], start, end))
        zb, zt = b0, max(z for _, z in top)
    else:
        zb, zt = b0, b0 + p["height"]
    return (x, y, c, s, p["thickness"] / 2.0, start, end, zb, zt, ops, _bands(start, end, zb, zt, ops), top)


# ---- 벽 상단 윤곽(21단계, 설계: elements-design.md 23.2). 상단 윤곽이 없는 벽은 이 함수들을 거치지 않는다(도면 바이트 그대로)
WALL_TOP_STEP = 20.0   # 가림 조각(계단 근사)의 높이 단계(mm): 기울어진 상단 아래 이 높이 이하의 삼각형만 가림에서 빠진다


def _below_line(poly, u0, t0, u1, t1):
    """볼록 다각형 (u, z) 를 직선 z = t(u) (u0 -> t0, u1 -> t1) 아래로 자른다(Sutherland-Hodgman)."""
    k = (t1 - t0) / (u1 - u0)

    def f(q):
        return q[1] - (t0 + k * (q[0] - u0))
    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        fa, fb = f(a), f(b)
        if fa <= 0.0:
            out.append(a)
        if fa < 0.0 < fb or fb < 0.0 < fa:
            t = fa / (fa - fb)
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    clean = []
    for q in out:
        if not clean or abs(q[0] - clean[-1][0]) > 1e-9 or abs(q[1] - clean[-1][1]) > 1e-9:
            clean.append(q)
    if len(clean) > 1 and abs(clean[0][0] - clean[-1][0]) <= 1e-9 and abs(clean[0][1] - clean[-1][1]) <= 1e-9:
        clean.pop()
    return clean if len(clean) >= 3 and _area2(clean) > 2 * EPS else []


def wall_top_pieces(g, w):
    """상단 윤곽 벽의 속이 찬 볼록 조각들 ((u0, u1, (u,z) 다각형 반시계), ...): 띠의 구간을 윤곽 꺾임점에서 나눠 윤곽 아래로 자른다(정확)."""
    sol = g.q(wall_solid, w)
    out = []
    for z0, z1, ivs in sol[10]:
        for a, b in ivs:
            for u0, t0, u1, t1 in WT.segments(WT.clip(sol[11], a, b)):
                poly = _below_line([(u0, z0), (u1, z0), (u1, z1), (u0, z1)], u0, t0, u1, t1)
                if poly:
                    out.append((u0, u1, tuple(poly)))
    return tuple(out)


def wall_top_steps(g, w):
    """상단 윤곽 벽의 계단 근사 띠 ((z0, z1, ((u0, u1),)), ...): 윤곽 아래에 다 드는 사각 조각만 둔다(보수적: 가림·층 해치가 쓴다).
    기울어진 구간은 높이 WALL_TOP_STEP 이하가 되게 나눠 각 조각의 위를 그 구간의 낮은 끝 높이로 둔다."""
    sol = g.q(wall_solid, w)
    out = []

    def push(z0, z1, u0, u1):
        if out and out[-1][0] == z0 and out[-1][1] == z1 and abs(out[-1][2][0][1] - u0) <= 1e-9:
            out[-1] = (z0, z1, ((out[-1][2][0][0], u1),))
        else:
            out.append((z0, z1, ((u0, u1),)))
    for z0, z1, ivs in sol[10]:
        for a, b in ivs:
            for u0, t0, u1, t1 in WT.segments(WT.clip(sol[11], a, b)):
                if min(t0, t1) >= z1:
                    push(z0, z1, u0, u1)
                    continue
                if max(t0, t1) <= z0 + EPS:
                    continue
                n = max(1, int(abs(t1 - t0) / WALL_TOP_STEP + 1.0 - 1e-9))
                for i in range(n):
                    p0, p1 = u0 + (u1 - u0) * i / n, u0 + (u1 - u0) * (i + 1) / n
                    ztop = min(z1, t0 + (t1 - t0) * i / n, t0 + (t1 - t0) * (i + 1) / n)
                    if ztop - z0 > EPS:
                        push(z0, ztop, p0, p1)
    return tuple(out)


# ---- 벽 잘라내기(23단계, 설계: elements-design.md 25.1). cuts 가 없는 벽은 이 함수들을 거치지 않는다(도면 바이트 그대로)
def wall_cut_solids(g, w):
    """잘라내기가 있는 벽(상단 윤곽 없음)의 속이 찬 상자들 ((u0, u1, v0, v1, z0, z1), ...): 띠(개구부를 뺀 구간) x 두께에서 잘라낸 상자를 뺀다.
    절단(평면 절단 조각·단면 절단 사각형)만 이 값을 쓴다. 가림(vpieces)은 띠 그대로다: 빼낸 자리는 자른 부재가 채우고 있다(근사, 설계 메모)."""
    sol = g.q(wall_solid, w)
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    voids = WC.shift_z(g.q(Q.wall_cut_boxes, w), elev)
    h = sol[4]
    solids = [(a, b, -h, h, z0, z1) for z0, z1, ivs in sol[10] for a, b in ivs]
    return WC.subtract(solids, voids)


def _has_cuts(g, w) -> bool:
    return bool(g.q(Q.wall_cut_boxes, w)) and g.q(wall_solid, w)[11] is None


def _cut_at(g, w, cutz):
    """잘라내기 벽을 높이 cutz 에서 자른 속이 찬 (u0, u1, v0, v1) 사각형들. 잘라낸 상자가 그 높이를 품지 않으면 () (띠 그대로 쓴다)."""
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    if not any(b[4] + elev < cutz - EPS and cutz + EPS < b[5] + elev for b in g.q(Q.wall_cut_boxes, w)):
        return ()
    # 아래 끝 포함·위 끝 제외(경계 높이에서 위아래 두 조각을 함께 세지 않는다)
    return tuple(sorted((q[0], q[1], q[2], q[3]) for q in g.q(wall_cut_solids, w) if q[4] <= cutz + EPS < q[5]))


def _quad_v(sol, a, b, v0, v1):
    return (_world(sol, a, v0), _world(sol, b, v0), _world(sol, b, v1), _world(sol, a, v1))   # 반시계


def _wall_bands(g, w, sol):
    """띠 목록: 상단 윤곽 벽은 계단 근사 띠, 아니면 wall_solid 의 띠 그대로."""
    return sol[10] if sol[11] is None else g.q(wall_top_steps, w)


def _isect_ivs(xs, ys):
    """정렬된 두 구간 목록의 교집합."""
    out = []
    for a0, a1 in xs:
        for b0, b1 in ys:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi - lo > EPS:
                out.append((lo, hi))
    return tuple(out)


def _u_of(cfg, sol, s_, w_):
    """뷰 좌표 (s, w) -> 벽 지역 u."""
    px = cfg[1] + s_ * cfg[5] + w_ * cfg[3]
    py = cfg[2] + s_ * cfg[6] + w_ * cfg[4]
    return (px - sol[0]) * sol[2] + (py - sol[1]) * sol[3]


def _clip_u(poly, lo, hi):
    """볼록 다각형 (u, z) 를 lo <= u <= hi 로 자른다."""
    for bound, keep_ge in ((lo, True), (hi, False)):
        out = []
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            ina = a[0] >= bound if keep_ge else a[0] <= bound
            inb = b[0] >= bound if keep_ge else b[0] <= bound
            if ina:
                out.append(a)
            if ina != inb:
                t = (bound - a[0]) / (b[0] - a[0])
                out.append((bound, a[1] + t * (b[1] - a[1])))
        poly = out
        if len(poly) < 3:
            return []
    return poly


def _z_span(poly, u):
    """볼록 다각형 (u, z) 를 수직선 u 로 자른 z 범위 (낮은, 높은). 만나지 않으면 None."""
    zs = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if abs(a[0] - u) <= 1e-9:
            zs.append(a[1])
        if (a[0] - u) * (b[0] - u) < 0.0:
            zs.append(a[1] + (u - a[0]) / (b[0] - a[0]) * (b[1] - a[1]))
    return (min(zs), max(zs)) if zs else None


def _vcut_top(g, v, w):
    """단면: 상단 윤곽 벽의 절단 다각형((s, z) 반시계). 절단면이 벽을 지나는 현의 u 범위로 조각을 자르고 u -> s 로 옮긴다(기울어진 윗변이 그대로 나온다)."""
    cfg = g.q(view_cfg, v)
    sol = g.q(wall_solid, w)
    near = cfg[7]
    out = []
    for u0, u1, poly in g.q(wall_top_pieces, w):
        q = _clip_w([_sw(cfg, p) for p in _quad(sol, u0, u1)], cfg[7], cfg[8])
        if len(q) < 3 or _area2(q) <= 2 * EPS:
            continue
        ss = [p[0] for p in q if abs(p[1] - near) <= EPS]
        if len(ss) < 2:
            continue
        s0, s1 = min(ss), max(ss)
        if s1 - s0 <= EPS:
            continue
        ua, ub = _u_of(cfg, sol, s0, near), _u_of(cfg, sol, s1, near)
        if abs(ub - ua) < 1e-6:   # 축에 수직으로 자른다: 그 u 에서의 높이 범위
            span = _z_span(poly, max(u0, min(u1, (ua + ub) / 2.0)))
            if span is not None and span[1] - span[0] > EPS:
                out.append(((s0, span[0]), (s1, span[0]), (s1, span[1]), (s0, span[1])))
            continue
        cp = _clip_u(list(poly), min(ua, ub), max(ua, ub))
        if not cp:
            continue
        k = (s1 - s0) / (ub - ua)
        pts = [(s0 + (u - ua) * k, z) for u, z in cp]
        if _area2(pts) < 0:
            pts = pts[::-1]
        if _area2(pts) > 2 * EPS:
            out.append(tuple(pts))
    return tuple(out)


def _top_vedges(sol, pt):
    """입면·단면용 상단 윤곽 벽의 바깥 모서리: 양 끝 수직 모서리, 밑면 둘레, 두 면의 상단 윤곽 선(기울어진 선·단), 꺾이는 곳의 두께 방향 선."""
    h, start, end, zb, top = sol[4], sol[5], sol[6], sol[7], sol[11]
    raw = []
    for u, z in ((start, top[0][1]), (end, top[-1][1])):
        for vv in (-h, h):
            raw.append((pt(u, vv, zb), pt(u, vv, z)))
        raw.append((pt(u, -h, zb), pt(u, h, zb)))
        raw.append((pt(u, -h, z), pt(u, h, z)))
    for vv in (-h, h):
        raw.append((pt(start, vv, zb), pt(end, vv, zb)))
        for (ua, za), (ub, zc) in zip(top, top[1:]):
            raw.append((pt(ua, vv, za), pt(ub, vv, zc)))
    for i in range(1, len(top) - 1):
        (ua, za), (u, z), (ub, zc) = top[i - 1], top[i], top[i + 1]
        if u - ua <= 1e-9 or ub - u <= 1e-9 or abs((z - za) * (ub - u) - (zc - z) * (u - ua)) > 1e-6 * (ub - ua):
            raw.append((pt(u, -h, z), pt(u, h, z)))
    return raw


def _world(sol, u, v):
    return (sol[0] + u * sol[2] - v * sol[3], sol[1] + u * sol[3] + v * sol[2])


def _quad(sol, a, b):
    h = sol[4]
    return (_world(sol, a, -h), _world(sol, b, -h), _world(sol, b, h), _world(sol, a, h))   # 반시계


# ================================================================ 기하 도우미
def _bbox_pts(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _inside_interval(p0, ux, uy, length, q):
    """선분 p0 + t*u (0<=t<=length) 이 볼록 다각형 q(반시계) 의 안쪽(경계에서 EPS 이상)에 있는 t 구간."""
    lo, hi = 0.0, length
    n = len(q)
    for i in range(n):
        a, b = q[i], q[(i + 1) % n]
        ex, ey = b[0] - a[0], b[1] - a[1]
        el_ = sqrt(ex * ex + ey * ey)
        if el_ < 1e-12:
            continue
        nx, ny = ey / el_, -ex / el_                       # 바깥 법선
        d0 = (p0[0] - a[0]) * nx + (p0[1] - a[1]) * ny + EPS   # 안쪽이려면 d0 + t*sl < 0
        sl = ux * nx + uy * ny
        if -1e-12 < sl < 1e-12:
            if d0 >= 0.0:
                return None
        elif sl > 0.0:
            hi = min(hi, -d0 / sl)
        else:
            lo = max(lo, -d0 / sl)
        if hi - lo <= EPS:
            return None
    return (lo, hi) if hi - lo > EPS else None


def outline_edges(mine, others, my_key):
    """볼록 다각형(반시계) 묶음 mine 의 합집합 윤곽 중, others 와 합쳐질 때 남는 변만 돌려준다.
    다른 조각의 안쪽에 든 부분과, 반대 방향으로 맞닿은 부분(내부 이음매)은 지운다.
    같은 방향으로 겹친 부분은 (키, 번호) 가 작은 쪽만 남긴다. others: [(키, 다각형)]. 반환: [(p0, p1)]"""
    allp = [(my_key, i, p) for i, p in enumerate(mine)] + [(k, 0, p) for k, p in others]
    boxes = [_bbox_pts(p) for _, _, p in allp]
    out = []
    for i, poly in enumerate(mine):
        n = len(poly)
        for j in range(n):
            p0, p1 = poly[j], poly[(j + 1) % n]
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            length = sqrt(dx * dx + dy * dy)
            if length < EPS:
                continue
            ux, uy = dx / length, dy / length
            ex0, ey0 = min(p0[0], p1[0]) - EPS, min(p0[1], p1[1]) - EPS
            ex1, ey1 = max(p0[0], p1[0]) + EPS, max(p0[1], p1[1]) + EPS
            cuts = []
            for idx, (k, oi, q) in enumerate(allp):
                if k == my_key and oi == i:
                    continue
                bb = boxes[idx]
                if bb[2] < ex0 or bb[0] > ex1 or bb[3] < ey0 or bb[1] > ey1:
                    continue
                iv = _inside_interval(p0, ux, uy, length, q)
                if iv is not None:
                    cuts.append(iv)
                m = len(q)
                for r in range(m):
                    r0, r1 = q[r], q[(r + 1) % m]
                    c0 = (r0[0] - p0[0]) * uy - (r0[1] - p0[1]) * ux
                    c1 = (r1[0] - p0[0]) * uy - (r1[1] - p0[1]) * ux
                    if abs(c0) > EPS or abs(c1) > EPS:
                        continue
                    t0 = (r0[0] - p0[0]) * ux + (r0[1] - p0[1]) * uy
                    t1 = (r1[0] - p0[0]) * ux + (r1[1] - p0[1]) * uy
                    lo, hi = max(min(t0, t1), 0.0), min(max(t0, t1), length)
                    if hi - lo <= EPS:
                        continue
                    if t1 < t0 or (k, oi) < (my_key, i):    # 반대 방향(내부 이음매) 또는 같은 방향이면서 상대가 우선
                        cuts.append((lo, hi))
            out.extend(_keep(p0, ux, uy, length, cuts))
    return out


def _keep(p0, ux, uy, length, cuts):
    cuts.sort()
    segs, cur = [], 0.0
    for lo, hi in cuts:
        if lo > cur + EPS:
            segs.append((cur, lo))
        cur = max(cur, hi)
    if length > cur + EPS:
        segs.append((cur, length))
    return [((p0[0] + a * ux, p0[1] + a * uy), (p0[0] + b * ux, p0[1] + b * uy)) for a, b in segs if b - a > SEG_MIN]


# ================================================================ 평면
def plan_pieces(g, v, w):
    """(절단 조각, 투영 조각): 절단 높이에서 자른 벽의 속이 찬 구간 사각형들 / 절단면 아래에 끝나는 벽의 윤곽."""
    cfg = g.q(view_cfg, v)
    sol = g.q(wall_solid, w)
    cutz, depth = cfg[2], cfg[3]
    zb, zt = sol[7], sol[8]
    cut, proj = [], []
    if sol[11] is not None:   # 상단 윤곽(21단계): 윤곽이 절단 높이보다 높은 구간은 절단, 낮은 구간(보이는 깊이 안)은 투영
        cut = [_quad(sol, a, b) for a, b in _cut_intervals(sol, cutz)]
        if is_rcp(cfg):   # 반사 천장 평면: 절단면 아래는 보이지 않는다
            return (tuple(cut), ())
        proj = [_quad(sol, a, b) for a, b in WT.ivs_between(sol[11], max(zb, cutz - depth) - EPS, cutz - EPS)]
        return (tuple(cut), tuple(proj))
    if zb - EPS <= cutz <= zt + EPS:
        for z0, z1, ivs in sol[10]:
            if z0 - EPS <= cutz <= z1 + EPS:
                cut = [_quad(sol, a, b) for a, b in ivs]
                break
        if g.q(_has_cuts, w) and g.q(_cut_at, w, cutz):   # 벽 잘라내기(23단계): 절단 높이를 품는 상자를 뺀 (u, v) 조각
            cut = [_quad_v(sol, q[0], q[1], q[2], q[3]) for q in g.q(_cut_at, w, cutz)]
    elif is_rcp(cfg):   # 반사 천장 평면: 바닥이 절단면 위 보이는 깊이 안에 있는 벽만 윤곽(아래에서 올려다본 밑면)
        if cutz + EPS < zb <= cutz + depth:
            proj = [_quad(sol, sol[5], sol[6])]
    elif zt < cutz - EPS and zt >= cutz - depth:
        proj = [_quad(sol, sol[5], sol[6])]
    return (tuple(cut), tuple(proj))


def plan_bbox(g, v, w):
    cut, proj = g.q(plan_pieces, v, w)
    pts = [p for q in cut + proj for p in q]
    return _bbox_pts(pts) if pts else None


def _nbr_index(g, bbfn, v):
    """뷰 하나의 벽 경계 상자 격자 색인(과업 ⑤ 성능: 이웃 판정이 벽 수의 제곱으로 늘던 것을 칸 조회로 줄인다).
    값: (범위 벽들(scope_walls 순서), 경계 상자들, {칸: (번호, ...)}, {벽: 번호}, 칸이 너무 많은 상자 번호들).
    상자는 자기가 걸친 칸마다 들어간다. 두 상자가 NBR_PAD 여유로 겹치면 공통 점이 있고 그 점의 칸을 둘 다 가지므로 후보에서 빠지지 않는다."""
    walls = g.q(scope_walls, v)
    boxes, cells, big = [], {}, []
    for i, o in enumerate(walls):
        bb = g.q(bbfn, v, o)
        boxes.append(bb)
        if bb is None:
            continue
        x0, x1 = int(bb[0] // NBR_CELL), int(bb[2] // NBR_CELL)
        y0, y1 = int(bb[1] // NBR_CELL), int(bb[3] // NBR_CELL)
        if (x1 - x0 + 1) * (y1 - y0 + 1) > NBR_MAX_CELLS:
            big.append(i)   # 아주 긴 벽: 칸에 넣지 않고 늘 후보로 본다
            continue
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                cells.setdefault((cx, cy), []).append(i)
    return (walls, tuple(boxes), {k: tuple(x) for k, x in cells.items()}, {o: i for i, o in enumerate(walls)}, tuple(big))


def _nbrs(g, bbfn, idxfn, v, w):
    """w 와 경계 상자(NBR_PAD 여유)가 겹치는 범위 안 다른 벽들(scope_walls 순서). 색인으로 후보만 보고 판정식은 그대로다."""
    me = g.q(bbfn, v, w)
    if me is None:
        return ()
    walls, boxes, cells, pos, big = g.q(idxfn, v)[:5]
    x0, x1 = int((me[0] - NBR_PAD) // NBR_CELL), int((me[2] + NBR_PAD) // NBR_CELL)
    y0, y1 = int((me[1] - NBR_PAD) // NBR_CELL), int((me[3] + NBR_PAD) // NBR_CELL)
    cand = set(big)
    if (x1 - x0 + 1) * (y1 - y0 + 1) > NBR_MAX_CELLS:
        cand = range(len(walls))
    else:
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                cand.update(cells.get((cx, cy), ()))
    me_i = pos.get(w)
    out = []
    for i in sorted(cand):
        if i == me_i:
            continue
        bb = boxes[i]
        if bb is not None and bb[0] <= me[2] + NBR_PAD and me[0] <= bb[2] + NBR_PAD \
                and bb[1] <= me[3] + NBR_PAD and me[1] <= bb[3] + NBR_PAD:
            out.append(walls[i])
    return tuple(out)


def plan_nbr_index(g, v):
    return _nbr_index(g, plan_bbox, v)


def plan_nbrs(g, v, w):
    return _nbrs(g, plan_bbox, plan_nbr_index, v, w)


# ================================================================ 패밀리 요소 (창·패밀리 문·기둥)
# 패밀리 인스턴스(finst)의 평가 결과(ifc_spec: 평면 기호 plan, 형상 body)를 그대로 그린다. 새 요소 종류를 만들지 않고 이미 IFC 로 나가는 패밀리를 쓴다.
def _floor(x):
    """바닥 함수(math.floor 대신: 이 모듈은 math 에서 sqrt 만 쓴다). int() 는 0 쪽으로 자르므로 음수를 보정한다."""
    i = int(x)
    return i if i <= x else i - 1


def _sincos_deg(a):
    """도 -> (sin, cos). 플랫폼 libm 을 쓰지 않는다(결정성): 90도 단위로 줄인 뒤 테일러 급수(사칙연산만)."""
    a = a - 360.0 * _floor(a / 360.0)
    k = _floor((a + 45.0) / 90.0)
    x = (a - 90.0 * k) * PI / 180.0
    x2 = x * x
    sn = cs = 0.0
    tn, tc = x, 1.0
    for i in range(1, 9):
        sn += tn
        cs += tc
        tn = -tn * x2 / ((2 * i) * (2 * i + 1))
        tc = -tc * x2 / ((2 * i - 1) * (2 * i))
    return [(sn, cs), (cs, -sn), (-sn, -cs), (-cs, sn)][k % 4]


def _freeze_plan(items):
    out = []
    for it in items:
        t, st = it["t"], it.get("s", "projection")
        if t == "line":
            out.append(("line", tuple(it["a"]), tuple(it["b"]), st))
        elif t == "arc":
            out.append(("arc", tuple(it["c"]), it["r"], it["a0"], it["a1"], st))
        elif t == "circle":
            out.append(("circle", tuple(it["c"]), it["r"], st))
        else:
            out.append(("poly", tuple(tuple(p) for p in it["pts"]), bool(it["closed"]), st))
    return tuple(out)


def _circle_pts(c, r, n=CIRC_N):
    pts = []
    for i in range(n):
        sn, cs = _sincos_deg(360.0 * i / n)
        pts.append((c[0] + r * cs, c[1] + r * sn))
    return pts


def _plan_items(items, to_world, fills=None):
    """평면 기호 항목들 -> [(등급, x0, y0, x1, y1)]. 절단 스타일은 'cut'(굵은 선), 투영은 'symbol'. fills 가 리스트면 닫힌 절단 도형의 채움 다각형도 모은다."""
    segs = []

    def add(p0, p1, st):
        a, b = to_world(*p0), to_world(*p1)
        segs.append(("cut" if st == "cut" else "symbol", a[0], a[1], b[0], b[1]))

    for it in items:
        t, st = it[0], it[-1]
        if t == "line":
            add(it[1], it[2], st)
        elif t == "arc":
            _, c, r, a0, a1, _ = it
            n = max(1, -int(-((a1 - a0) / 90.0 * ARC_N) // 1))
            pts = []
            for i in range(n + 1):
                sn, cs = _sincos_deg(a0 + (a1 - a0) * i / n)
                pts.append((c[0] + r * cs, c[1] + r * sn))
            for p0, p1 in zip(pts, pts[1:]):
                add(p0, p1, st)
        else:
            if t == "circle":
                pts, closed = _circle_pts(it[1], it[2]), True
            else:
                pts, closed = list(it[1]), it[2]
            for i in range(len(pts) - (0 if closed else 1)):
                add(pts[i], pts[(i + 1) % len(pts)], st)
            if closed and st == "cut" and fills is not None:
                fills.append(tuple(to_world(*p) for p in pts))
    return segs


def _body_boxes(body):
    """body 의 xz 평면 직사각 압출을 상자 (u0, u1, z0, z1, n0, n1) 로. 구멍(cuts)은 부모 두께 안으로 자른 상자를 더한다. 다른 형상은 건너뛴다."""
    out = []
    for b in body:
        if b.get("k") != "extrude" or b.get("plane") != "xz" or b["profile"].get("shape") != "rect":
            continue
        pr, (n0, n1) = b["profile"], b["range"]
        out.append((pr["u"][0], pr["u"][1], pr["v"][0], pr["v"][1], n0, n1))
        for c in b.get("cuts", ()):
            if c.get("plane") == "xz" and c["profile"].get("shape") == "rect":
                cp, (c0, c1) = c["profile"], c["range"]
                out.append((cp["u"][0], cp["u"][1], cp["v"][0], cp["v"][1], max(n0, c0), min(n1, c1)))
    return tuple(out)


def hosted_fam(g, w):
    """벽에 호스팅된 패밀리 인스턴스(창·패밀리 문)의 그림 정보 (u0, u1, z0, z1, offset, zbase, 평면 기호, 상자들). 정렬 튜플.
    u 는 벽 지역 길이 방향, z 는 절대 높이. 개구부가 없는 인스턴스(평가 오류 등)는 그리지 않는다."""
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    out = []
    for d in g.q(Q.wall_hosted, w):
        row = Q.el(g, d)
        if row is None or row["kind"] != "finst":
            continue
        spec = g.q(Q.ifc_spec, d)
        cut = spec.get("cut")
        if not cut:
            continue
        zb = elev + spec["elevation"]
        plan, boxes = _freeze_plan(spec["plan"]), _body_boxes(spec["body"])
        if FS.hosted_flipped(spec.get("rotation")):   # 뒤집은 인스턴스(20단계): 지역 (u, v) -> (-u, -v). 개구부는 대칭이라 cut 범위는 그대로
            plan, boxes = _flip_plan(plan), tuple((-b[1], -b[0], b[2], b[3], -b[5], -b[4]) for b in boxes)
        out.append((spec["offset"] + cut["x"][0], spec["offset"] + cut["x"][1], zb + cut["z"][0], zb + cut["z"][1],
                    spec["offset"], zb, plan, boxes))
    return tuple(sorted(out, key=repr))


def _flip_plan(items):
    """얼린 평면 기호를 지역 원점 둘레로 반 바퀴 돌린다((u, v) -> (-u, -v), 호는 시작·끝 각에 180 을 더한다)."""
    def neg(p):
        return (-p[0], -p[1])
    out = []
    for it in items:
        t = it[0]
        if t == "line":
            out.append(("line", neg(it[1]), neg(it[2]), it[3]))
        elif t == "arc":
            out.append(("arc", neg(it[1]), it[2], it[3] + 180.0, it[4] + 180.0, it[5]))
        elif t == "circle":
            out.append(("circle", neg(it[1]), it[2], it[3]))
        else:
            out.append(("poly", tuple(neg(p) for p in it[1]), it[2], it[3]))
    return tuple(out)


def _prism_poly(pr):
    """body 의 xy 압출 프로필 -> 지역 좌표 볼록 다각형(반시계). 지원: rect·circle·polygon."""
    p = pr["profile"]
    sh = p.get("shape")
    if sh == "rect":
        (u0, u1), (v0, v1) = p["u"], p["v"]
        return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    if sh == "circle":
        return _circle_pts(p["center"], p["r"])
    if sh == "polygon":
        return [tuple(q) for q in p["points"]]
    return None


def free_fam(g, e):
    """호스트 없이 레벨에 서는 패밀리 인스턴스(기둥 등)의 그림 정보 (x, y, 회전도, zb, zt, 평면 기호, 각기둥들). 그릴 형상이 없으면 None.
    각기둥은 (z0, z1, 지역 다각형). 높이는 body 의 xy 압출 범위에서 온다."""
    row = Q.el(g, e)
    if row is None or row["kind"] != "finst" or g.q(Q.finst_host, e):
        return None
    spec = g.q(Q.ifc_spec, e)
    if spec.get("error") or not spec.get("position"):
        return None
    _, elev = g.q(Q.level_info, g.q(Q.finst_level, e))
    zb0 = elev + spec["elevation"]
    prisms = []
    for b in spec["body"]:
        if b.get("k") != "extrude" or b.get("plane") != "xy":
            continue
        poly = _prism_poly(b)
        if poly is None:
            continue
        if _area2(poly) < 0:
            poly.reverse()
        prisms.append((zb0 + b["range"][0], zb0 + b["range"][1], tuple(poly)))
    if not prisms:
        return None
    return (spec["position"][0], spec["position"][1], spec.get("rotation", 0.0) or 0.0, min(p[0] for p in prisms), max(p[1] for p in prisms),
            _freeze_plan(spec["plan"]), tuple(prisms))


def _free_world(ff):
    """지역(u, v) -> 세계 좌표 변환 함수(회전 후 이동)."""
    sn, cs = _sincos_deg(ff[2])
    return lambda u, v: (ff[0] + u * cs - v * sn, ff[1] + u * sn + v * cs)


def all_free_fams(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "finst", b) if g.q(free_fam, e) is not None)
    return tuple(sorted(out))


def scope_fams(g, v):
    """뷰가 그리는 자립형 패밀리 인스턴스(기둥 등). 평면은 그 레벨에 놓인 것만, 단면·입면은 모델 전체."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    if cfg[0] == "plan":
        if cfg[1] is None:
            return ()
        return tuple(e for e in sorted(Q.rel_in(g, "on_level", cfg[1])) if g.q(free_fam, e) is not None)
    return g.q(all_free_fams)


def plan_fam_parts(g, v, e):
    """평면의 자립형 요소: (선분들, 채움 다각형들). 절단 높이가 요소 높이 범위에 들 때만 그린다(기둥 단면 + 기호)."""
    cfg = g.q(view_cfg, v)
    ff = g.q(free_fam, e)
    if ff is not None and is_rcp(cfg) and cfg[2] + EPS < ff[3] <= cfg[2] + cfg[3]:   # 반사 천장 평면: 절단면 위에 매단 요소(조명 등)는 평면 기호를 투영선으로
        return (tuple(("proj",) + x[1:] for x in _plan_items(ff[5], _free_world(ff))), ())
    if ff is None or cfg[0] != "plan" or not (ff[3] - EPS <= cfg[2] <= ff[4] + EPS):
        return ((), ())
    fills: list = []
    segs = _plan_items(ff[5], _free_world(ff), fills)
    return (tuple(segs), tuple(fills))


def _fam_vpieces(g, v, e):
    """단면·입면에서 자립형 요소의 속이 찬 조각 ((z0, z1, 다각형(s,w)), ...)."""
    cfg = g.q(view_cfg, v)
    ff = g.q(free_fam, e)
    if ff is None or cfg[0] == "plan":
        return ()
    tw = _free_world(ff)
    out = []
    for z0, z1, poly in ff[6]:
        sw = [_sw(cfg, tw(*p)) for p in poly]
        if _area2(sw) < 0:
            sw.reverse()
        sw = _clip_w(sw, cfg[7], cfg[8])
        if len(sw) >= 3 and _area2(sw) > 2 * EPS:
            out.append((z0, z1, tuple(sw)))
    return tuple(out)


def vfam_pieces(g, v, e):
    return g.q(_fam_vpieces, v, e)


def vfam_bbox(g, v, e):
    ps = g.q(_fam_vpieces, v, e)
    if not ps:
        return None
    ss = [p[0] for _, _, poly in ps for p in poly]
    return (min(ss), min(z0 for z0, _, _ in ps), max(ss), max(z1 for _, z1, _ in ps))


def vfam_cut_rects(g, v, e):
    """단면: 절단면(w=near)과 만나는 기둥 조각의 (s, z) 사각형."""
    cfg = g.q(view_cfg, v)
    if cfg[0] == "plan" or not cfg[9]:
        return ()
    rects = []
    for z0, z1, poly in g.q(_fam_vpieces, v, e):
        ss = [p[0] for p in poly if abs(p[1] - cfg[7]) <= EPS]
        if len(ss) >= 2 and max(ss) - min(ss) > EPS:
            s0, s1 = min(ss), max(ss)
            rects.append(((s0, z0), (s1, z0), (s1, z1), (s0, z1)))
    return tuple(rects)


def vfam_edges(g, v, e):
    """자립형 요소의 날카로운 모서리: 각기둥의 수직 모서리와 위·아랫면 둘레(뷰 좌표 3D 선분)."""
    cfg = g.q(view_cfg, v)
    ff = g.q(free_fam, e)
    if ff is None or cfg[0] == "plan":
        return ()
    tw = _free_world(ff)
    raw = []
    for z0, z1, poly in ff[6]:
        pts = [_sw(cfg, tw(*p)) for p in poly]
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            raw.append(((a[0], a[1], z0), (a[0], a[1], z1)))
            for z in (z0, z1):
                raw.append(((a[0], a[1], z), (b[0], b[1], z)))
    out = []
    for e0, e1 in raw:
        if abs(e1[0] - e0[0]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        c = _clip_edge(e0, e1, cfg[7], cfg[8])
        if c is not None:
            out.append(c)
    return tuple(out)


def vfam_nbr_pieces(g, v, e):
    """가림 후보: 같은 뷰의 벽과 다른 기둥 중 경계 상자가 겹치는 것의 조각."""
    me = g.q(vfam_bbox, v, e)
    if me is None:
        return ()
    occ = []
    for w in g.q(scope_walls, v):
        bb = g.q(vbbox, v, w)
        if bb is not None and bb[0] <= me[2] + NBR_PAD and me[0] <= bb[2] + NBR_PAD and bb[1] <= me[3] + NBR_PAD and me[1] <= bb[3] + NBR_PAD:
            occ.extend(g.q(vpieces, v, w))
    for o in g.q(scope_fams, v):
        if o != e:
            occ.extend(g.q(_fam_vpieces, v, o))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    for rf in g.q(scope_roofs, v):   # 경사 지붕 판이 앞에 있으면 기둥 윗선 같은 선을 가린다
        occ.extend(g.q(_roof_solid_vpieces, v, rf))
    return tuple(occ)


def fam_lines(g, v, e):
    """자립형 요소 하나가 뷰에 기여하는 선분."""
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    if cfg[0] == "plan":
        return g.q(plan_fam_parts, v, e)[0]
    mine = list(g.q(_fam_vpieces, v, e))
    occ = mine + list(g.q(vfam_nbr_pieces, v, e))
    lines = []
    if occ:
        for e0, e1 in g.q(vfam_edges, v, e):
            for t0, t1 in visible_parts(e0, e1, occ):
                lines.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]),
                              e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    if cfg[9]:
        rects = g.q(vfam_cut_rects, v, e)
        if rects:
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(rects), [], e)]
    return tuple(lines)


def vfam_hosted_edges(g, v, w):
    """벽에 호스팅된 패밀리 인스턴스(창)의 상자 모서리(창틀 바깥·안쪽 틀·유리)를 뷰 좌표 3D 선분으로."""
    cfg = g.q(view_cfg, v)
    sol = g.q(wall_solid, w)
    if cfg is None or cfg[0] == "plan":
        return ()
    raw = []

    def pt(u, n, z):
        s_, w_ = _sw(cfg, _world(sol, u, n))
        return (s_, w_, z)

    for u0, u1, _, _, off, zb, _, boxes in g.q(hosted_fam, w):
        for bu0, bu1, bz0, bz1, n0, n1 in boxes:
            us, zs, ns = (off + bu0, off + bu1), (zb + bz0, zb + bz1), (n0, n1)
            for n in ns:                                           # 앞·뒤 면의 사각형 둘레
                for z in zs:
                    raw.append((pt(us[0], n, z), pt(us[1], n, z)))
                for u in us:
                    raw.append((pt(u, n, zs[0]), pt(u, n, zs[1])))
            for u in us:                                           # 두께 방향 모서리
                for z in zs:
                    raw.append((pt(u, ns[0], z), pt(u, ns[1], z)))
    out = []
    for e0, e1 in raw:
        if abs(e1[0] - e0[0]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        c = _clip_edge(e0, e1, cfg[7], cfg[8])
        if c is not None:
            out.append(c)
    return tuple(out)


# ================================================================ 슬래브 (kinds "slab": 레벨 + 경계 다각형 + 두께)
# 슬래브는 z 범위가 [밑면, 윗면]인 각기둥이다(경계가 오목해도 된다). 가림 판정용 조각은 경계를 삼각형으로 나눈 볼록 조각이다.
#   평면: 절단 높이가 슬래브 두께 안이면 경계 윤곽을 절단선 + 절단 채움으로(아니면 그리지 않는다: 평면은 벽이 윤곽을 준다)
#   단면: 절단면과 경계의 교차 구간 x 두께 = 절단 사각형(cut 선 + 채움), 벽 절단과 합집합 윤곽(이음매 선 제거). 절단면 뒤의 모서리는 투영선
#   입면: 두께 띠의 모서리(위·아랫면 둘레 + 수직 모서리). 앞에 선 벽·기둥·다른 슬래브가 가린다
def slab_info(g, s):
    """(밑면 z, 윗면 z, 경계 다각형(반시계 튜플), 구멍 다각형들(반시계 튜플의 튜플, 없으면 빈 튜플)) 또는 None. 저작(authored) 슬래브만 그린다(분리된 슬래브의 의도는 낡은 값이다)."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "slab" or row["origin"] != "authored":
        return None
    zb, zt = g.q(Q.slab_zrange, s)
    poly = [tuple(p) for p in row["params"]["boundary"]]
    if _area2(poly) < 0:
        poly.reverse()
    holes = tuple(tuple(tuple(q) for q in o) for o in row["params"].get("openings", ()))
    return (zb, zt, tuple(poly), holes)


def all_slabs(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "slab", b) if g.q(slab_info, e) is not None)
    return tuple(sorted(out))


def scope_slabs(g, v):
    """뷰가 그리는 슬래브. 평면은 그 레벨에 놓인 것만, 단면·입면은 모델 전체."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    if cfg[0] == "plan":
        if cfg[1] is None:
            return ()
        own = tuple(e for e in sorted(Q.rel_in(g, "on_level", cfg[1])) if g.q(slab_info, e) is not None)
        if is_rcp(cfg):   # 반사 천장 평면: 밑면이 절단면 위 보이는 깊이 안에 있는 다른 층 슬래브(위층 바닥 = 천장)도 본다
            up = [e for e in g.q(all_slabs) if cfg[2] + EPS < g.q(slab_info, e)[0] <= cfg[2] + cfg[3]]
            return tuple(sorted(set(own) | set(up)))
        return own
    return g.q(all_slabs)


def _in_tri(p, a, b, c):
    """점 p 가 삼각형 abc(반시계)의 안쪽이거나 경계 위인가."""
    for u, w in ((a, b), (b, c), (c, a)):
        if (w[0] - u[0]) * (p[1] - u[1]) - (w[1] - u[1]) * (p[0] - u[0]) < -1e-9:
            return False
    return True


SEAM = 1e-3   # 구멍 있는 슬래브를 사다리꼴로 나눌 때 이웃 조각이 겹치는 폭 mm(이음매에 놓인 세로선이 두 조각 사이로 빠지지 않게)


def _trapezoids(poly, holes):
    """구멍이 있는 다각형(바깥 경계 poly + 구멍들, 모두 반시계 튜플)을 x 축 방향 사다리꼴(볼록 사각형·삼각형)들로 나눈다.
    꼭짓점 x 좌표마다 세로로 잘라 각 띠에서 변들을 y 로 정렬해 짝수-홀수 규칙으로 속이 찬 구간을 얻는다(구멍은 자연히 빠진다). 결정적이다.
    띠는 오른쪽으로 SEAM 만큼 겹쳐 이웃 띠와의 이음매 위의 선이 빠지지 않게 한다."""
    rings = [poly, *holes]
    xs = sorted({p[0] for r in rings for p in r})
    out = []
    for xa, xb in zip(xs, xs[1:]):
        if xb - xa <= 1e-9:
            continue
        xm = (xa + xb) / 2.0
        ext = SEAM if xb < xs[-1] - 1e-9 else 0.0   # 맨 오른쪽 띠는 바깥 경계를 넘지 않는다
        cross = []
        for r in rings:
            n = len(r)
            for i in range(n):
                p, q = r[i], r[(i + 1) % n]
                if p[0] == q[0]:
                    continue
                lo, hi = (p, q) if p[0] < q[0] else (q, p)
                if lo[0] <= xa + 1e-9 and hi[0] >= xb - 1e-9:   # 이 띠를 가로지르는 변
                    sl = (hi[1] - lo[1]) / (hi[0] - lo[0])
                    cross.append((lo[1] + sl * (xm - lo[0]), lo[1] + sl * (xa - lo[0]), lo[1] + sl * (xb + ext - lo[0])))
        cross.sort()
        for k in range(0, len(cross) - 1, 2):
            (_, a0, a1), (_, b0, b1) = cross[k], cross[k + 1]
            quad = [(xa, a0), (xb + ext, a1), (xb + ext, b1), (xa, b0)]
            pts = [q for i, q in enumerate(quad) if i == 0 or (abs(q[0] - quad[i - 1][0]) > 1e-9 or abs(q[1] - quad[i - 1][1]) > 1e-9)]
            if len(pts) >= 3 and abs(_area2(pts)) > 1e-6:
                out.append(tuple(pts))
    return tuple(out)


def slab_tris(g, s):
    """경계 다각형(오목 가능, 반시계)을 볼록 조각들로 나눈다. 구멍이 없으면 삼각형(귀 자르기, 항상 앞 번호의 귀부터라 결정적이다).
    구멍이 있으면 구멍을 뺀 사다리꼴들이다(구멍 너머가 슬래브에 가리지 않게: 15단계)."""
    info = g.q(slab_info, s)
    if info is None:
        return ()
    if info[3]:
        return _trapezoids(info[2], info[3])
    poly = info[2]
    idx = list(range(len(poly)))
    tris = []
    while len(idx) > 3:
        n = len(idx)
        pick = None
        for k in range(n):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % n]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 1e-9:
                continue                                       # 오목이거나 공선인 꼭짓점은 귀가 아니다
            if any(_in_tri(poly[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            pick = k
            tris.append((a, b, c))
            break
        if pick is None:                                       # 귀가 없다: 공선 꼭짓점을 하나 버리고(면적 불변), 그것도 없으면 부채꼴로 마무리
            for k in range(n):
                a, b, c = poly[idx[k - 1]], poly[idx[k]], poly[idx[(k + 1) % n]]
                if abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) <= 1e-9:
                    pick = k
                    break
            if pick is None:
                for k in range(1, n - 1):
                    tris.append((poly[idx[0]], poly[idx[k]], poly[idx[k + 1]]))
                return tuple(tris)
        del idx[pick]
    if len(idx) == 3:
        tris.append((poly[idx[0]], poly[idx[1]], poly[idx[2]]))
    return tuple(tris)


def _hole_x(poly):
    """구멍 하나의 X 표시 선분들(꼭짓점 넷이면 두 대각선, 볼록이면 무게중심에서 꼭짓점들로, 오목이면 없음)."""
    n = len(poly)
    if n == 4:
        return [(poly[0], poly[2]), (poly[1], poly[3])]
    if all((poly[(i + 1) % n][0] - poly[i][0]) * (poly[(i + 2) % n][1] - poly[(i + 1) % n][1])
           - (poly[(i + 1) % n][1] - poly[i][1]) * (poly[(i + 2) % n][0] - poly[(i + 1) % n][0]) >= 0 for i in range(n)):
        cx, cy = sum(q[0] for q in poly) / n, sum(q[1] for q in poly) / n
        return [((cx, cy), q) for q in poly]
    return []


def _x2(p, q, r):
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _free_bridge(a, b, edges) -> bool:
    """선분 ab 가 edges 의 선분과 끝점 접촉 외에는 만나지 않는가(ab 의 끝점을 공유하는 선분은 건너뛴다)."""
    for c, d in edges:
        if a == c or a == d or b == c or b == d:
            continue
        if _x2(a, b, c) * _x2(a, b, d) <= 0 and _x2(c, d, a) * _x2(c, d, b) <= 0:
            return False
    return True


def _in_ring(pt, ring) -> bool:
    """짝수-홀수 규칙으로 점이 고리 안인가."""
    x, y = pt
    c = False
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) / (b[1] - a[1]) * (b[0] - a[0]):
            c = not c
    return c


BRIDGE_WORK_MAX = 40000   # 구멍 잇기 후보(고리 꼭짓점 x 구멍 꼭짓점) 수 상한: 넘으면 구멍을 비우지 않는다(옛 채움)


def fill_ring(poly, holes):
    """구멍이 있는 슬래브의 절단 채움: 경계(반시계)에 구멍(시계 방향으로 뒤집음)을 가는 틈으로 이어 붙인 고리 하나(비영·짝수-홀수 규칙 모두 구멍이 빈다).
    구멍은 왼쪽 끝 꼭짓점(x, y) 순으로 하나씩, 이미 이어진 고리의 꼭짓점과 가장 가까우면서 어떤 변과도 만나지 않는 쌍으로 잇는다(결정적).
    이을 수 없으면 None(채움은 경계만)."""
    ring = list(poly)
    todo = sorted((list(reversed(h)) for h in holes), key=lambda h: min(h))
    if len(ring) * sum(len(h) for h in todo) > BRIDGE_WORK_MAX:
        return None
    for k, h in enumerate(todo):
        others = [(o[i], o[(i + 1) % len(o)]) for o in todo[k + 1:] for i in range(len(o))]
        edges = [(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))] + [(h[i], h[(i + 1) % len(h)]) for i in range(len(h))] + others
        cands = sorted(((ring[i][0] - h[j][0]) ** 2 + (ring[i][1] - h[j][1]) ** 2, i, j) for i in range(len(ring)) for j in range(len(h)))
        pick = None
        for _, i, j in cands:
            a, b = ring[i], h[j]
            mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            if _free_bridge(a, b, edges) and _in_ring(mid, ring) and not _in_ring(mid, h) and not any(_in_ring(mid, o) for o in todo[k + 1:]):
                pick = (i, j)
                break
        if pick is None:
            return None
        i, j = pick
        ring = ring[:i + 1] + h[j:] + h[:j + 1] + [ring[i]] + ring[i + 1:]
    return tuple(ring)


def plan_slab_parts(g, v, s):
    """평면의 슬래브: (선분들, 채움 다각형들). 절단 높이가 두께 범위에 들 때만 경계 윤곽을 절단선·절단 채움으로 그린다(구멍 윤곽도 절단선이다. 채움은 구멍을 비운다: 구멍이 있으면 fill_ring 의 고리 하나).
    슬래브가 절단 높이 아래(윗면 < 절단 높이)이고 구멍이 있으면 내려다 보이는 구멍을 윤곽(proj)과 X 표시(symbol)로 그린다."""
    cfg = g.q(view_cfg, v)
    info = g.q(slab_info, s)
    if info is None or cfg is None or cfg[0] != "plan":
        return ((), ())
    holes = info[3]
    if is_rcp(cfg) and not info[0] - EPS <= cfg[2] <= info[1] + EPS:   # 반사 천장 평면: 위 슬래브는 밑면 윤곽·구멍(X), 아래 슬래브는 그리지 않는다
        if not cfg[2] + EPS < info[0] <= cfg[2] + cfg[3]:
            return ((), ())
        segs = []
        for ring in (info[2],) + holes:
            m = len(ring)
            segs += [("proj", ring[i][0], ring[i][1], ring[(i + 1) % m][0], ring[(i + 1) % m][1]) for i in range(m)]
        for o in holes:
            segs += [("symbol", a[0], a[1], b[0], b[1]) for a, b in _hole_x(o)]
        return (tuple(segs), ())
    if info[0] - EPS <= cfg[2] <= info[1] + EPS:
        poly = info[2]
        n = len(poly)
        segs = tuple(("cut", poly[i][0], poly[i][1], poly[(i + 1) % n][0], poly[(i + 1) % n][1]) for i in range(n))
        for o in holes:
            m = len(o)
            segs += tuple(("cut", o[i][0], o[i][1], o[(i + 1) % m][0], o[(i + 1) % m][1]) for i in range(m))
        return (segs, (fill_ring(poly, holes) or poly if holes else poly,))
    if cfg[2] > info[1] + EPS:
        segs = []
        for o in holes:
            m = len(o)
            segs += [("proj", o[i][0], o[i][1], o[(i + 1) % m][0], o[(i + 1) % m][1]) for i in range(m)]
            segs += [("symbol", a[0], a[1], b[0], b[1]) for a, b in _hole_x(o)]
        segs += g.q(plan_roof_marks, v, s)[0]   # 지붕 슬래브(use=roof)만 윤곽·물매 화살표·배수구가 더해진다
        fills = ()
        par = g.q(slab_parapet, s)
        if par is not None and cfg[2] <= info[1] + par[2] + EPS:   # 절단 높이가 파라펫 안: 파라펫(바깥 둘레와 안쪽 고리 사이)을 절단선·절단 채움으로
            outer, inner, _ph = par
            for ring in (outer, inner):
                m = len(ring)
                segs += [("cut", ring[i][0], ring[i][1], ring[(i + 1) % m][0], ring[(i + 1) % m][1]) for i in range(m)]
            fills = (fill_ring(list(outer), [list(inner)]) or outer,)
        if segs:
            return (tuple(segs), fills)
    return ((), ())


def slab_parapet(g, s):
    """지붕 슬래브의 파라펫 (바깥 둘레 튜플, 안쪽 고리 튜플, 높이) 또는 None(파라펫이 없다). 안쪽 고리는 kinds.parapet_ring(마이터)이다."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "slab" or row["origin"] != "authored" or "parapet" not in row["params"]:
        return None
    p = row["params"]
    ring = K.parapet_ring([list(q) for q in p["boundary"]], p["parapet"]["thickness"])
    if ring is None:
        return None
    return (tuple(tuple(q) for q in p["boundary"]), tuple(ring), p["parapet"]["height"])


# ---- 지붕 평면 (설계: elements-design.md 13절): use=roof 슬래브가 절단 높이 아래에 있을 때 윤곽(proj), 배수 방향 화살표(symbol), 배수구(symbol), 물매 문자
ROOF_ARROW_LEN = 12.0      # 물매 화살표 길이(용지 mm)
ROOF_DRAIN_R = 1.5         # 배수구 기호 반지름(용지 mm)


def _arrow_label_pos(cx, cy, dx, dy, text, sc):
    """물매 화살표(중심 cx,cy · 방향 dx,dy)의 옆에 놓는 가로 글의 (x, y 기준선). 글 상자(폭·높이)가 화살표 축 쪽 옆면에 닿지 않도록 수직 방향으로 상자 반 크기 + 틈만큼 띄운다.
    글은 가로로만 쓰므로 화살표가 세로에 가까울수록 폭의 절반만큼 옆으로 간다(예전에는 1.8 고정이라 세로 화살표에서 글이 축에 걸렸다)."""
    nx, ny = -dy, dx
    half_w = _text_w(text, LABEL_SUB_H) * sc / 2.0
    half_h = 0.4 * LABEL_SUB_H * sc
    d = abs(nx) * half_w + abs(ny) * half_h + 1.2 * sc
    return round(cx + nx * d, 3), round(cy + ny * d - 0.35 * LABEL_SUB_H * sc, 3)


def slab_roof(g, s):
    """지붕 슬래브의 ((물이 흐르는 방향 도, 물매 %) 또는 None, 배수구들) 또는 None(지붕이 아니다)."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "slab" or row["origin"] != "authored" or row["params"].get("use") != "roof":
        return None
    p = row["params"]
    fall = p.get("fall")
    return ((fall["dir"], fall["pct"]) if fall else None, tuple(tuple(q) for q in p.get("drains", ())))


def plan_roof_marks(g, v, s):
    """평면의 지붕 슬래브 하나: (선분들, 문자들). 절단 높이가 슬래브 윗면 아래가 아닌 평면(= 지붕을 내려다보는 지붕 평면)에서만 만든다."""
    cfg = g.q(view_cfg, v)
    info = g.q(slab_info, s)
    roof = g.q(slab_roof, s)
    if cfg is None or cfg[0] != "plan" or info is None or roof is None or cfg[2] <= info[1] + EPS or is_rcp(cfg):
        return ((), ())
    poly = info[2]
    n = len(poly)
    sc = cfg[4]
    segs = [("proj", poly[i][0], poly[i][1], poly[(i + 1) % n][0], poly[(i + 1) % n][1]) for i in range(n)]
    par = g.q(slab_parapet, s)
    if par is not None:   # 파라펫: 절단 높이가 파라펫 안이면 바깥 둘레는 절단선이 그리므로 빼고, 위면 위(파라펫 윗면 위)면 안쪽 고리를 투영선으로 더한다
        if cfg[2] <= info[1] + par[2] + EPS:
            segs = []
        else:
            m = len(par[1])
            segs += [("proj", par[1][i][0], par[1][i][1], par[1][(i + 1) % m][0], par[1][(i + 1) % m][1]) for i in range(m)]
    labels = []
    fall, drains = roof
    if fall is not None:
        cx, cy = polygon_centroid(poly)
        xs, ys = [q[0] for q in poly], [q[1] for q in poly]
        ln = min(ROOF_ARROW_LEN * sc, 0.4 * min(max(xs) - min(xs), max(ys) - min(ys)))
        sn, cs = _sincos_deg(fall[0])
        if ln > SEG_MIN:
            tail, head = (cx - cs * ln / 2.0, cy - sn * ln / 2.0), (cx + cs * ln / 2.0, cy + sn * ln / 2.0)
            segs.append(("symbol", tail[0], tail[1], head[0], head[1]))
            hl = ln * 0.25
            for sg in (1.0, -1.0):   # 화살촉: 진행 방향 뒤로 ±20도 벌어진 두 선
                bs, bc = _sincos_deg(fall[0] + 180.0 + sg * 20.0)
                segs.append(("symbol", head[0], head[1], head[0] + bc * hl, head[1] + bs * hl))
            lx, ly = _arrow_label_pos(cx, cy, cs, sn, f"물매 {fall[1]:g}%", sc)
            labels.append((lx, ly, f"물매 {fall[1]:g}%", LABEL_SUB_H, "middle"))
    for d in drains:   # 배수구: 원과 십자
        r = ROOF_DRAIN_R * sc
        ring = _circle_pts(d, r, 12)
        segs += [("symbol", ring[i][0], ring[i][1], ring[(i + 1) % 12][0], ring[(i + 1) % 12][1]) for i in range(12)]
        segs.append(("symbol", d[0] - r, d[1], d[0] + r, d[1]))
        segs.append(("symbol", d[0], d[1] - r, d[0], d[1] + r))
    return (tuple(segs), tuple(labels))


def _slab_vpieces(g, v, s):
    """단면·입면에서 슬래브의 속이 찬 볼록 조각 ((z0, z1, 다각형(s,w)), ...)."""
    cfg = g.q(view_cfg, v)
    info = g.q(slab_info, s)
    if info is None or cfg is None or cfg[0] == "plan":
        return ()
    out = []
    for tri in g.q(slab_tris, s):
        sw = [_sw(cfg, p) for p in tri]
        if _area2(sw) < 0:
            sw.reverse()
        sw = _clip_w(sw, cfg[7], cfg[8])
        if len(sw) >= 3 and _area2(sw) > 2 * EPS:
            out.append((info[0], info[1], tuple(sw)))
    par = g.q(slab_parapet, s)
    if par is not None:   # 파라펫: 바깥 둘레와 안쪽 고리 사이를 변마다 사다리꼴 기둥 조각으로(윗면 위로 높이만큼)
        outer, inner, ph = par
        n = len(outer)
        for i in range(n):
            quad = [_sw(cfg, p) for p in (outer[i], outer[(i + 1) % n], inner[(i + 1) % n], inner[i])]
            if _area2(quad) < 0:
                quad.reverse()
            quad = _clip_w(quad, cfg[7], cfg[8])
            if len(quad) >= 3 and _area2(quad) > 2 * EPS:
                out.append((info[1], info[1] + ph, tuple(quad)))
    return tuple(out)


def slab_vbbox(g, v, s):
    ps = g.q(_slab_vpieces, v, s)
    if not ps:
        return None
    ss = [p[0] for _, _, poly in ps for p in poly]
    return (min(ss), min(z0 for z0, _, _ in ps), max(ss), max(z1 for _, z1, _ in ps))


def slab_cut_rects(g, v, s):
    """단면: 절단면(w=near)과 슬래브 경계의 교차 구간마다 (s, z) 사각형(오목 경계도 짝수-홀수 규칙으로 구간이 여럿일 수 있다)."""
    cfg = g.q(view_cfg, v)
    info = g.q(slab_info, s)
    if info is None or cfg is None or cfg[0] == "plan" or not cfg[9]:
        return ()
    near = cfg[7]
    xs = []
    for ring in (info[2], *info[3]):   # 구멍 둘레도 같은 짝수-홀수 규칙으로 센다(구멍 안 구간은 빠진다)
        pts = [_sw(cfg, p) for p in ring]
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            if (a[1] <= near) != (b[1] <= near):
                xs.append(a[0] + (near - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
    xs.sort()
    rects = []
    for i in range(0, len(xs) - 1, 2):
        s0, s1 = xs[i], xs[i + 1]
        if s1 - s0 > EPS:
            rects.append(((s0, info[0]), (s1, info[0]), (s1, info[1]), (s0, info[1])))
    par = g.q(slab_parapet, s)
    if par is not None:   # 파라펫: 바깥 둘레·안쪽 고리의 교차를 짝수-홀수 규칙으로 세면 파라펫 띠 구간이 된다
        px = []
        for ring in (par[0], par[1]):
            pts = [_sw(cfg, p) for p in ring]
            n = len(pts)
            for i in range(n):
                a, b = pts[i], pts[(i + 1) % n]
                if (a[1] <= near) != (b[1] <= near):
                    px.append(a[0] + (near - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
        px.sort()
        for i in range(0, len(px) - 1, 2):
            s0, s1 = px[i], px[i + 1]
            if s1 - s0 > EPS:
                rects.append(((s0, info[1]), (s1, info[1]), (s1, info[1] + par[2]), (s0, info[1] + par[2])))
    return tuple(rects)


def slab_vedges(g, v, s):
    """슬래브의 모서리: 경계 둘레의 위·아랫면 수평선과 꼭짓점의 수직 모서리(뷰 좌표 3D 선분 (s,w,z))."""
    cfg = g.q(view_cfg, v)
    info = g.q(slab_info, s)
    if info is None or cfg is None or cfg[0] == "plan":
        return ()
    zb, zt, poly = info[:3]
    pts = [_sw(cfg, p) for p in poly]
    n = len(pts)
    raw = []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        raw.append(((a[0], a[1], zb), (a[0], a[1], zt)))
        for z in (zb, zt):
            raw.append(((a[0], a[1], z), (b[0], b[1], z)))
    par = g.q(slab_parapet, s)
    if par is not None:   # 파라펫: 바깥 둘레·안쪽 고리의 윗선과 바깥 꼭짓점의 수직 모서리
        for ring in (par[0], par[1]):
            pp = [_sw(cfg, p) for p in ring]
            m = len(pp)
            for i in range(m):
                a, b = pp[i], pp[(i + 1) % m]
                raw.append(((a[0], a[1], zt + par[2]), (b[0], b[1], zt + par[2])))
                raw.append(((a[0], a[1], zt), (a[0], a[1], zt + par[2])))
    out = []
    for e0, e1 in raw:
        if abs(e1[0] - e0[0]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        c = _clip_edge(e0, e1, cfg[7], cfg[8])
        if c is not None:
            out.append(c)
    return tuple(out)


def _bb_hit(a, b):
    return a[0] <= b[2] + NBR_PAD and b[0] <= a[2] + NBR_PAD and a[1] <= b[3] + NBR_PAD and b[1] <= a[3] + NBR_PAD


def slab_nbr_walls(g, v, s):
    """슬래브와 경계 상자가 겹치는 같은 뷰의 벽(가림 후보·절단 윤곽 합집합 상대)."""
    me = g.q(slab_vbbox, v, s)
    if me is None:
        return ()
    return tuple(w for w in g.q(scope_walls, v) if (bb := g.q(vbbox, v, w)) is not None and _bb_hit(me, bb))


def slab_lines(g, v, s):
    """슬래브 하나가 뷰에 기여하는 선분."""
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    if cfg[0] == "plan":
        return g.q(plan_slab_parts, v, s)[0]
    mine = list(g.q(_slab_vpieces, v, s))
    nb = g.q(slab_nbr_walls, v, s)
    occ = mine[:]
    for w in nb:
        occ.extend(g.q(vpieces, v, w))
    for o in g.q(scope_slabs, v):
        if o != s:
            occ.extend(g.q(_slab_vpieces, v, o))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for rf in g.q(scope_roofs, v):
        occ.extend(g.q(_roof_solid_vpieces, v, rf))
    lines = []
    if occ:
        for e0, e1 in g.q(slab_vedges, v, s):
            for t0, t1 in visible_parts(e0, e1, occ):
                lines.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]),
                              e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    if cfg[9]:
        rects = g.q(slab_cut_rects, v, s)
        if rects:
            others = [(w, q) for w in nb for q in g.q(vcut_rects, v, w)]
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(rects), others, s)]
    return tuple(lines)


# ================================================================ 계단 (kinds "stair": 직선 계단, 설계: elements-design.md 6절)
# 계단은 디딤판마다 속이 찬 볼록 각기둥(아래 끝 ~ 그 디딤판 윗면)의 모임이다. 디딤판은 챌판 수 - 1 개(마지막 챌판 위는 위층 바닥).
#   평면: 윤곽·디딤판 선(투영선), 걸음선 + 화살촉(symbol), 절단 높이가 계단 높이 안이면 그 위치의 절단선(cut)과 그 위쪽 생략
#   단면: 절단면과 만나는 디딤 조각마다 (s, z) 사각형(cut 선 + 채움), 벽·슬래브와 합집합 윤곽
#   입면: 옆모습 윤곽(양 옆면)과 디딤판 앞 모서리를 투영선으로
STAIR_ARROW = 0.3   # 화살촉 길이 = 폭 x STAIR_ARROW (mm, 상한 400)


def stair_info(g, s):
    """(아래 z, 위 z, 시작 x, y, 걸음 방향 ux, uy, 걸음선 길이, 폭, 챌판 수, 디딤판 깊이, 챌판 높이) 또는 None. 저작 계단만 그린다."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "stair" or row["origin"] != "authored":
        return None
    zb, zt = g.q(Q.stair_zrange, s)
    p = row["params"]
    x1, y1, x2, y2 = p["run"]
    dx, dy = x2 - x1, y2 - y1
    length = sqrt(dx * dx + dy * dy)
    n = p["risers"]
    return (zb, zt, x1, y1, dx / length, dy / length, length, p["width"], n, length / (n - 1), (zt - zb) / n)


def all_stairs(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "stair", b) if g.q(stair_info, e) is not None)
    return tuple(sorted(out))


def scope_stairs(g, v):
    """뷰가 그리는 계단. 평면은 그 레벨에 놓인 것만, 단면·입면은 모델 전체."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    if cfg[0] == "plan":
        if cfg[1] is None or is_rcp(cfg):   # 반사 천장 평면(W1 1-C 3차)은 아직 그리지 않는다
            return ()
        return tuple(e for e in sorted(Q.rel_in(g, "on_level", cfg[1])) if g.q(stair_info, e) is not None)
    return g.q(all_stairs)


def _st_pt(info, t, m):
    """걸음선 위 t(mm), 왼쪽으로 m(mm) 만큼 떨어진 점의 세계 좌표."""
    return (info[2] + info[4] * t - info[5] * m, info[3] + info[5] * t + info[4] * m)


def stair_geom(g, s):
    """꺾인 계단 포함 계단의 형상: ((단 묶음 정보, ...), 층참 또는 None). 단 묶음 정보 = stair_info 와 같은 11개 + 밑으로 더 채우는 두께(skirt).
    층참 = (다각형, 밑면 z, 윗면 z). 직선 계단은 단 묶음 하나(stair_info 와 값이 같다), 층참 없음."""
    info = g.q(stair_info, s)
    if info is None:
        return None
    zb, zt, _, _, _, _, _, width, n, _, rise = info
    p = Q.el(g, s)["params"]
    lay = K.stair_layout(p)
    flights = []
    for fl in lay["flights"]:
        x1, y1, x2, y2 = fl["run"]
        ln = fl["length"]
        z0 = zb + fl["z0"]
        flights.append((z0, zt if fl["n"] == n else z0 + fl["n"] * rise, x1, y1, (x2 - x1) / ln, (y2 - y1) / ln, ln, width, fl["n"], fl["td"], rise, fl["skirt"]))
    ld = lay["landing"]
    return (tuple(flights), None if ld is None else (tuple(tuple(q) for q in ld["poly"]), zb + ld["z"] - ld["t"], zb + ld["z"]))


def stair_winders(g, s):
    """돌음(와인더) 계단의 회전부(elements-design.md 23.3) 또는 None:
    (쐐기들 ((다각형, 밑면 z, 윗면 z, 시작 각, 끝 각, 시작 반직선 끝점, 가운데 각 바깥 둘레 점), ...), 회전 중심, 보행선 반지름, 회전 각, e0, e90). z 는 절대 높이."""
    info = g.q(stair_info, s)
    if info is None:
        return None
    t = Q.el(g, s)["params"].get("turn")
    if t is None or t.get("winders") is None:
        return None
    wd = K.stair_layout(Q.el(g, s)["params"])["winders"]
    zb = info[0]
    wedges = tuple((tuple(tuple(q) for q in w["poly"]), zb + w["zb"], zb + w["z"], w["a0"], w["a1"], w["q0"], w["qm"]) for w in wd["wedges"])
    return (wedges, wd["center"], wd["radius"], wd["sweep"], wd["e0"], wd["e90"])


def _winder_pt(wg, f, r=None):
    """회전부 보행선(또는 반지름 r 의 원호) 위 각 f(도)의 점."""
    sn, cs = K.sincos_deg(f)
    (cx, cy), rr, e0, e9 = wg[1], (wg[2] if r is None else r), wg[4], wg[5]
    return (cx + rr * (cs * e0[0] + sn * e9[0]), cy + rr * (cs * e0[1] + sn * e9[1]))


def _plan_winders(fa, fb, wg, cutz, width):
    """돌음 계단의 평면(아래 단 묶음이 다 보일 때만 부른다): 보이는 쐐기(윗면 <= 절단 높이)의 윤곽(proj), 보행선 원호 화살표(symbol),
    회전부 안에서 끊기면 첫 안 보이는 쐐기를 가로지르는 절단선(cut)과 화살촉, 다 보이면 위 단 묶음(걸음선은 위 단 묶음 화살표가 이어받는다)."""
    wedges = wg[0]
    vis = [w for w in wedges if w[2] <= cutz + EPS]
    segs = []
    for w in vis:
        poly = w[0]
        segs += [("proj", poly[i][0], poly[i][1], poly[(i + 1) % len(poly)][0], poly[(i + 1) % len(poly)][1]) for i in range(len(poly))]
    whole = len(vis) == len(wedges)
    a_end = wg[3] if whole else (vis[-1][3] + vis[-1][4]) / 2.0
    nseg = max(2, int(a_end / 10.0 + 0.5))
    pts = [_winder_pt(wg, a_end * i / nseg) for i in range(nseg + 1)]
    segs += [("symbol", pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(nseg)]
    if whole:
        segs += _plan_flight(fb, cutz, arrow_from=0.0)
        return segs
    sn, cs = K.sincos_deg(a_end)                                              # 화살촉: 원호 끝 접선 방향
    e0, e9 = wg[4], wg[5]
    tx, ty = -sn * e0[0] + cs * e9[0], -sn * e0[1] + cs * e9[1]
    hl = min(width * STAIR_ARROW, 400.0, wg[2] * 0.5)
    ex, ey = pts[-1]
    for sgn in (1.0, -1.0):
        segs.append(("symbol", ex, ey, ex - tx * hl - sgn * ty * hl * 0.4, ey - ty * hl + sgn * tx * hl * 0.4))
    nxt = wedges[len(vis)]                                                    # 절단선: 첫 안 보이는 쐐기를 안쪽(보이는 쐐기 변)에서 바깥 둘레로 가로지르는 대각선
    (cx, cy), q0, qm = wg[1], nxt[5], nxt[6]
    segs.append(("cut", cx + 0.2 * (q0[0] - cx), cy + 0.2 * (q0[1] - cy), qm[0], qm[1]))
    return segs


def _plan_flight(info, cutz, head=True, arrow_from=None, arrow_to_end=False):
    """단 묶음 하나의 평면 선분(스택: 윤곽·디딤판 선(proj), 걸음선 화살표(symbol), 절단선(cut)). 절단 높이가 아래 끝보다 낮으면 그리지 않는다."""
    if cutz < info[0] - EPS:
        return []
    zb, zt, _, _, _, _, length, width, n, td, rise = info[:11]
    h = width / 2.0
    broken = cutz < zt - EPS
    k_b = min(_floor((cutz - zb) / rise), n - 1) if broken else n - 1
    tmax = k_b * td if broken else length
    segs = []

    def seg(cls, a, b):
        segs.append((cls, a[0], a[1], b[0], b[1]))

    seg("proj", _st_pt(info, 0.0, h), _st_pt(info, 0.0, -h))                  # 첫 챌판 쪽 끝선
    seg("proj", _st_pt(info, 0.0, h), _st_pt(info, tmax, h))                  # 양 옆선
    seg("proj", _st_pt(info, 0.0, -h), _st_pt(info, tmax, -h))
    if not broken:
        seg("proj", _st_pt(info, length, h), _st_pt(info, length, -h))
    for k in range(1, n - 1):                                                 # 디딤판 사이 선(절단 위치 아래만)
        if k * td < tmax - EPS:
            seg("proj", _st_pt(info, k * td, h), _st_pt(info, k * td, -h))
    # 걸음선과 오르는 방향 화살촉
    t_start = min(td * 0.5, tmax) if arrow_from is None else arrow_from
    t_end = tmax - min(td * 0.3, tmax * 0.1) if broken else (length if arrow_to_end else length - min(td * 0.5, length * 0.1))
    if t_end - t_start > EPS:
        seg("symbol", _st_pt(info, t_start, 0.0), _st_pt(info, t_end, 0.0))
        if head:
            hl = min(width * STAIR_ARROW, 400.0, t_end - t_start)
            for sgn in (1.0, -1.0):
                seg("symbol", _st_pt(info, t_end, 0.0), _st_pt(info, t_end - hl, sgn * hl * 0.4))
    if broken:                                                                # 절단선: 계단을 가로지르는 대각선
        e = min(td * 0.5, 150.0)
        seg("cut", _st_pt(info, tmax - e, h), _st_pt(info, tmax + e, -h))
    return segs


def plan_stair_parts(g, v, s):
    """평면의 계단: (선분들, 채움들). 채움은 없다. 꺾인 계단은 단 묶음마다 위 규칙을 따르고, 절단 높이가 층참 윗면 이상이면 층참 윤곽(proj)과 이어 꺾이는 걸음선 화살표를 더한다."""
    cfg = g.q(view_cfg, v)
    info = g.q(stair_info, s)
    if info is None or cfg is None or cfg[0] != "plan" or cfg[2] < info[0] - EPS:
        return ((), ())
    flights, ld = g.q(stair_geom, s)
    wg = g.q(stair_winders, s)
    if wg is not None:                                                        # 돌음 계단: 아래 단 묶음이 다 보이면 회전부 쐐기를 잇는다
        fa, fb = flights
        seen = cfg[2] >= fa[1] - EPS
        segs = _plan_flight(fa, cfg[2], head=not seen, arrow_to_end=seen)
        if seen:
            segs += _plan_winders(fa, fb, wg, cfg[2], fa[7])
        return (tuple(segs), ())
    if ld is None:                                                            # 직선 계단: 기존 규약 그대로
        return (tuple(_plan_flight(flights[0], cfg[2])), ())
    fa, fb = flights
    land_seen = cfg[2] >= ld[2] - EPS                                         # 절단 높이가 층참 윗면 이상이면 층참 위가 보인다
    segs = _plan_flight(fa, cfg[2], head=not land_seen, arrow_to_end=land_seen)
    if land_seen:
        poly = ld[0]
        n = len(poly)
        segs += [("proj", poly[i][0], poly[i][1], poly[(i + 1) % n][0], poly[(i + 1) % n][1]) for i in range(n)]
        # 꺾이는 걸음선: A 끝 -> 층참 안 꺾임 점들 -> B 시작(B 의 화살표가 이어받는다)
        a_end = _st_pt(fa, fa[6], 0.0)
        b_start = _st_pt(fb, 0.0, 0.0)
        mid = ((a_end[0] + b_start[0]) / 2.0, (a_end[1] + b_start[1]) / 2.0)
        cx, cy = sum(q[0] for q in poly) / 4.0, sum(q[1] for q in poly) / 4.0   # 층참 중심을 지나게 해 층참 안에서 꺾이는 것을 분명히 한다
        pts = [a_end, (cx, cy), b_start] if abs(cx - mid[0]) + abs(cy - mid[1]) > EPS else [a_end, b_start]
        segs += [("symbol", pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(len(pts) - 1)]
        segs += _plan_flight(fb, cfg[2], arrow_from=0.0)
    return (tuple(segs), ())


def _stair_vpieces(g, v, s):
    """단면·입면에서 계단의 속이 찬 볼록 조각 ((z0, z1, 다각형(s,w)), ...): 단 묶음의 디딤판마다 하나, 꺾인 계단은 층참 판 하나가 더해진다."""
    cfg = g.q(view_cfg, v)
    info = g.q(stair_info, s)
    if info is None or cfg is None or cfg[0] == "plan":
        return ()
    flights, ld = g.q(stair_geom, s)
    out = []
    for fi in flights:
        zb, _, _, _, _, _, _, width, n, td, rise, skirt = fi
        h = width / 2.0
        for k in range(n - 1):
            quad = [_sw(cfg, _st_pt(fi, t, m)) for t, m in ((k * td, -h), ((k + 1) * td, -h), ((k + 1) * td, h), (k * td, h))]
            if _area2(quad) < 0:
                quad.reverse()
            quad = _clip_w(quad, cfg[7], cfg[8])
            if len(quad) >= 3 and _area2(quad) > 2 * EPS:
                out.append((zb - skirt, zb + (k + 1) * rise, tuple(quad)))
    if ld is not None:
        quad = [_sw(cfg, q) for q in ld[0]]
        if _area2(quad) < 0:
            quad.reverse()
        quad = _clip_w(quad, cfg[7], cfg[8])
        if len(quad) >= 3 and _area2(quad) > 2 * EPS:
            out.append((ld[1], ld[2], tuple(quad)))
    wg = g.q(stair_winders, s)
    for w in (() if wg is None else wg[0]):                                   # 돌음 쐐기: 볼록 각기둥(쐐기 각 90도 이하)
        quad = [_sw(cfg, q) for q in w[0]]
        if _area2(quad) < 0:
            quad.reverse()
        quad = _clip_w(quad, cfg[7], cfg[8])
        if len(quad) >= 3 and _area2(quad) > 2 * EPS:
            out.append((w[1], w[2], tuple(quad)))
    return tuple(out)


def stair_vbbox(g, v, s):
    ps = g.q(_stair_vpieces, v, s)
    if not ps:
        return None
    ss = [p[0] for _, _, poly in ps for p in poly]
    return (min(ss), min(z0 for z0, _, _ in ps), max(ss), max(z1 for _, z1, _ in ps))


def stair_cut_rects(g, v, s):
    """단면: 절단면(w=near)과 각 디딤 조각의 교차 구간 x (바닥 ~ 디딤판 윗면) 사각형."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "plan" or not cfg[9]:
        return ()
    near = cfg[7]
    rects = []
    for z0, z1, poly in g.q(_stair_vpieces, v, s):
        xs = []
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            if abs(a[1] - near) <= EPS:
                xs.append(a[0])
            if (a[1] < near - EPS) != (b[1] < near - EPS) and abs(b[1] - a[1]) > EPS:
                xs.append(a[0] + (near - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
        if len(xs) >= 2 and max(xs) - min(xs) > EPS:
            s0, s1 = min(xs), max(xs)
            rects.append(((s0, z0), (s1, z0), (s1, z1), (s0, z1)))
    return tuple(rects)


def stair_vedges(g, v, s):
    """계단의 모서리(뷰 좌표 3D 선분 (s,w,z)): 단 묶음 양 옆면의 톱니 윤곽과 디딤판 앞 모서리(폭 방향), 층참의 위·아랫면 둘레와 수직 모서리."""
    cfg = g.q(view_cfg, v)
    info = g.q(stair_info, s)
    if info is None or cfg is None or cfg[0] == "plan":
        return ()
    flights, ld = g.q(stair_geom, s)
    raw = []
    for fi in flights:
        zb, _, _, _, _, _, length, width, n, td, rise, skirt = fi
        h = width / 2.0
        zbot = zb - skirt
        prof = [(0.0, zbot)]
        for k in range(n):
            prof.append((k * td, zb + (k + 1) * rise))
            if k < n - 1:
                prof.append(((k + 1) * td, zb + (k + 1) * rise))
        prof.append((length, zbot))
        for m in (-h, h):
            pts = [(_sw(cfg, _st_pt(fi, t, m)), z) for t, z in prof]
            for i in range(len(pts)):
                (a, za), (b, zb2) = pts[i], pts[(i + 1) % len(pts)]
                raw.append(((a[0], a[1], za), (b[0], b[1], zb2)))
        for t, z in prof[1:-1]:                                               # 디딤판·챌판 모서리가 만나는 꼭짓점마다 폭 방향 선
            a, b = _sw(cfg, _st_pt(fi, t, -h)), _sw(cfg, _st_pt(fi, t, h))
            raw.append(((a[0], a[1], z), (b[0], b[1], z)))
    if ld is not None:
        pts = [_sw(cfg, q) for q in ld[0]]
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            raw.append(((a[0], a[1], ld[1]), (a[0], a[1], ld[2])))
            for z in (ld[1], ld[2]):
                raw.append(((a[0], a[1], z), (b[0], b[1], z)))
    wg = g.q(stair_winders, s)
    for w in (() if wg is None else wg[0]):                                   # 돌음 쐐기: 윗면 둘레·수직 모서리, 밑면 둘레
        pts = [_sw(cfg, q) for q in w[0]]
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            raw.append(((a[0], a[1], w[1]), (a[0], a[1], w[2])))
            for z in (w[1], w[2]):
                raw.append(((a[0], a[1], z), (b[0], b[1], z)))
    out = []
    for e0, e1 in raw:
        if abs(e1[0] - e0[0]) < EPS and abs(e1[1] - e0[1]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        c = _clip_edge(e0, e1, cfg[7], cfg[8])
        if c is not None:
            out.append(c)
    return tuple(out)


def stair_nbr_walls(g, v, s):
    """계단과 경계 상자가 겹치는 같은 뷰의 벽(가림 후보·절단 윤곽 합집합 상대)."""
    me = g.q(stair_vbbox, v, s)
    if me is None:
        return ()
    return tuple(w for w in g.q(scope_walls, v) if (bb := g.q(vbbox, v, w)) is not None and _bb_hit(me, bb))


def stair_lines(g, v, s):
    """계단 하나가 뷰에 기여하는 선분."""
    cfg = g.q(view_cfg, v)
    if cfg is None:
        return ()
    if cfg[0] == "plan":
        return g.q(plan_stair_parts, v, s)[0]
    mine = list(g.q(_stair_vpieces, v, s))
    nb = g.q(stair_nbr_walls, v, s)
    occ = mine[:]
    for w in nb:
        occ.extend(g.q(vpieces, v, w))
    for o in g.q(scope_stairs, v):
        if o != s:
            occ.extend(g.q(_stair_vpieces, v, o))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for rf in g.q(scope_roofs, v):
        occ.extend(g.q(_roof_solid_vpieces, v, rf))
    lines = []
    if occ:
        for e0, e1 in g.q(stair_vedges, v, s):
            for t0, t1 in visible_parts(e0, e1, occ):
                lines.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]),
                              e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    if cfg[9]:
        rects = g.q(stair_cut_rects, v, s)
        if rects:
            others = [(w, q) for w in nb for q in g.q(vcut_rects, v, w)]
            others += [(sl, q) for sl in g.q(scope_slabs, v) for q in g.q(slab_cut_rects, v, sl)]
            others += [(o, q) for o in g.q(scope_stairs, v) if o != s for q in g.q(stair_cut_rects, v, o)]
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(rects), others, s)]
    return tuple(lines)


# ================================================================ 난간 (kinds "railing": 난간선 + 높이 + 경사, 설계: elements-design.md 12절)
# 난간은 손잡이 윗선(3D 폴리라인)과 난간 기둥(수직선)이다. 형상은 kinds.railing_geom 이 정한다(IFC 투영과 같은 값).
#   평면: 바닥 마감선이 절단 높이 이하인 부분의 난간선을 투영선으로(경사 난간은 절단 높이에서 끊는다)
#   단면·입면: 손잡이선과 기둥을 투영선으로. 앞에 선 벽·슬래브·계단·패밀리가 가린다. 난간은 다른 요소를 가리지 않는다(가는 선)
def railing_info(g, r):
    """((바닥 마감 3D 점들), (손잡이 윗선 3D 점들), (((x,y,z0),(x,y,z1)) 기둥들)) 절대 높이, 또는 None. 저작 난간만 그린다."""
    row = Q.el(g, r)
    if row is None or row["kind"] != "railing" or row["origin"] != "authored":
        return None
    lv = g.q(Q.level_info, g.q(Q.railing_level, r))[1]
    gm = K.railing_geom(row["params"])

    def up(q):
        return (q[0], q[1], q[2] + lv)
    return (tuple(up(q) for q in gm["surface"]), tuple(up(q) for q in gm["top"]), tuple((up(a), up(b)) for a, b in gm["posts"]))


def all_railings(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "railing", b) if g.q(railing_info, e) is not None)
    return tuple(sorted(out))


def scope_railings(g, v):
    """뷰가 그리는 난간. 평면은 그 레벨에 놓인 것만, 단면·입면은 모델 전체."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    if cfg[0] == "plan":
        if cfg[1] is None or is_rcp(cfg):   # 반사 천장 평면(W1 1-C 3차)은 아직 그리지 않는다
            return ()
        return tuple(e for e in sorted(Q.rel_in(g, "on_level", cfg[1])) if g.q(railing_info, e) is not None)
    return g.q(all_railings)


def railing_lines(g, v, r):
    """난간 하나가 뷰에 기여하는 선분."""
    cfg = g.q(view_cfg, v)
    info = g.q(railing_info, r)
    if cfg is None or info is None:
        return ()
    surf, top, posts = info
    if cfg[0] == "plan":
        out = []
        c = cfg[2]
        for i in range(len(surf) - 1):
            (x0, y0, z0), (x1, y1, z1) = surf[i], surf[i + 1]
            if z0 > c + EPS and z1 > c + EPS:
                continue
            lo, hi = 0.0, 1.0
            if z0 > c + EPS or z1 > c + EPS:   # 경사 난간이 절단 높이를 가로지른다: 아래쪽 부분만
                t = (c - z0) / (z1 - z0)
                if z0 <= c + EPS:
                    hi = t
                else:
                    lo = t
            ax, ay, bx, by = x0 + (x1 - x0) * lo, y0 + (y1 - y0) * lo, x0 + (x1 - x0) * hi, y0 + (y1 - y0) * hi
            if sqrt((bx - ax) ** 2 + (by - ay) ** 2) > SEG_MIN:
                out.append(("proj", ax, ay, bx, by))
        return tuple(out)
    raw = []
    for i in range(len(top) - 1):
        raw.append((top[i], top[i + 1]))
    raw.extend(posts)
    occ = []
    for w in g.q(scope_walls, v):
        occ.extend(g.q(vpieces, v, w))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for rf in g.q(scope_roofs, v):
        occ.extend(g.q(_roof_solid_vpieces, v, rf))
    lines = []
    for a, b in raw:
        pa, pb = _sw(cfg, a), _sw(cfg, b)
        e0, e1 = (pa[0], pa[1], a[2]), (pb[0], pb[1], b[2])
        if abs(e1[0] - e0[0]) < EPS and abs(e1[1] - e0[1]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        c = _clip_edge(e0, e1, cfg[7], cfg[8])
        if c is None:
            continue
        e0, e1 = c
        for t0, t1 in (visible_parts(e0, e1, occ) if occ else [(0.0, 1.0)]):
            lines.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]), e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    return tuple(lines)


# ================================================================ 승강기 (kinds "elevator": 샤프트 안쪽 사각형 + 카, 설계: elements-design.md 13절)
# 형상은 kinds.elevator_geom 이 정한다(IFC 투영·슬래브 구멍과 같은 값).
#   평면: 절단 높이가 샤프트 높이 범위에 드는 평면마다(승강기가 놓인 레벨만이 아니라 지나는 모든 층) 샤프트 윤곽·카 윤곽(proj), 카의 대각 X(symbol), "EV" 문자
#   단면·입면: 샤프트 상자와 카 상자의 모서리를 투영선으로(앞의 벽·슬래브·계단·패밀리가 가린다). 승강기는 다른 요소를 가리지 않는다
def elevator_info(g, e):
    """(샤프트 사각형 (x1,y1,x2,y2), 카 사각형, 샤프트 아래 z, 샤프트 위 z, 카 천장 z) 절대 높이, 또는 None. 저작 승강기만 그린다."""
    row = Q.el(g, e)
    if row is None or row["kind"] != "elevator" or row["origin"] != "authored":
        return None
    lv = g.q(Q.level_info, g.q(Q.elevator_level, e))[1]
    gm = K.elevator_geom(row["params"])
    return (gm["shaft"], gm["cab"], gm["z0"] + lv, gm["z1"] + lv, gm["cab_z1"] + lv)


def all_elevators(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "elevator", b) if g.q(elevator_info, e) is not None)
    return tuple(sorted(out))


def elevator_ext(g, e):
    """승강기의 선택 입력(피트·기계실·승강장 문)을 그리기 값으로: {"pit_z0": 피트 바닥 절대 z(피트가 없으면 카 바닥), "mach_z1": 기계실 윗면 절대 z(없으면 샤프트 위),
    "doors": None 또는 {"edge": ((x0,y0),(x1,y1)) 문 양끝, "normal": 면 바깥 법선, "w", "h", "stops": ((레벨 id, 레벨 높이), ...) 높이순}} 또는 None(저작 승강기가 아니다).
    승강장 문은 레벨 높이가 샤프트 높이 범위(문 높이 포함)에 드는 정차층만 낸다(레벨이 지워졌거나 범위 밖이면 건너뛴다)."""
    row = Q.el(g, e)
    if row is None or row["kind"] != "elevator" or row["origin"] != "authored":
        return None
    p = row["params"]
    lv = g.q(Q.level_info, g.q(Q.elevator_level, e))[1]
    gm = K.elevator_geom(p)
    doors = None
    if "doors" in p:
        edge, nrm = K.elevator_door_edge(p)
        zs = []
        for lid in p["stops"]:
            lr = Q.el(g, lid)
            if lr is None or lr["kind"] != "level":
                continue
            z = lr["params"]["elevation"]
            if z >= lv + gm["pit_z0"] - 1.0 and z + p["doors"]["h"] <= lv + gm["z1"] + 1.0:
                zs.append((lid, z))
        zs.sort(key=lambda t: (t[1], t[0]))
        doors = {"edge": edge, "normal": nrm, "w": p["doors"]["w"], "h": p["doors"]["h"], "stops": tuple(zs)}
    return {"pit_z0": lv + gm["pit_z0"], "mach_z1": lv + gm["mach_z1"], "doors": doors}


def scope_elevators(g, v):
    """뷰가 그리는 승강기. 평면은 절단 높이가 샤프트 높이 범위(피트 바닥·기계실 윗면까지, 아래·위 포함)에 드는 것 전부, 단면·입면은 모델 전체."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    allv = g.q(all_elevators)
    if cfg[0] == "plan":
        if cfg[1] is None or is_rcp(cfg):   # 반사 천장 평면(W1 1-C 3차)은 아직 그리지 않는다
            return ()
        return tuple(e for e in allv if (ex := g.q(elevator_ext, e))["pit_z0"] - EPS <= cfg[2] <= ex["mach_z1"] + EPS)
    return allv


def _box_edges(x1, y1, x2, y2, z0, z1):
    """직육면체의 12 모서리(3D 선분)."""
    c = ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
    out = [((x, y, z0), (x, y, z1)) for x, y in c]
    for z in (z0, z1):
        out += [((c[i][0], c[i][1], z), (c[(i + 1) % 4][0], c[(i + 1) % 4][1], z)) for i in range(4)]
    return out


def plan_elevator_labels(g, v, e):
    cfg = g.q(view_cfg, v)
    info = g.q(elevator_info, e)
    if info is None or cfg is None or cfg[0] != "plan":
        return ()
    x1, y1, x2, y2 = info[1]
    word = "EV" if info[2] - EPS <= cfg[2] <= info[3] + EPS else ("기계실" if cfg[2] > info[3] else "피트")   # 카가 있는 높이만 EV, 위는 기계실, 아래는 피트
    return ((round((x1 + x2) / 2.0, 3), round((y1 + y2) / 2.0 + 0.6 * cfg[4], 3), word, LABEL_SUB_H, "middle"),)


def elevator_lines(g, v, e):
    """승강기 하나가 뷰에 기여하는 선분."""
    cfg = g.q(view_cfg, v)
    info = g.q(elevator_info, e)
    if cfg is None or info is None:
        return ()
    shaft, cab, z0, z1, zc = info
    ext = g.q(elevator_ext, e)
    if cfg[0] == "plan":
        out = []
        in_cab = z0 - EPS <= cfg[2] <= z1 + EPS   # 카가 있는 높이. 피트(카 바닥 아래)·기계실(샤프트 위)에서는 샤프트 윤곽만 그린다
        door = ext["doors"] if ext["doors"] is not None and in_cab and cfg[1] in {lid for lid, _z in ext["doors"]["stops"]} else None
        for k_, (x1, y1, x2, y2) in enumerate((shaft, cab) if in_cab else (shaft,)):
            pts = ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
            for i in range(4):
                a, b = pts[i], pts[(i + 1) % 4]
                if door is not None and k_ == 0 and _on_door_edge(a, b, door):
                    out += _door_gap_lines(a, b, door)   # 승강장 문 개구부: 면의 선을 문 폭만큼 비우고 문설주·문 선을 그린다
                else:
                    out.append(("proj", a[0], a[1], b[0], b[1]))
        if in_cab:
            x1, y1, x2, y2 = cab
            out += [("symbol", x1, y1, x2, y2), ("symbol", x1, y2, x2, y1)]
        return tuple(out)
    raw = _box_edges(*shaft, ext["pit_z0"], z1) + _box_edges(*cab, z0, zc)
    if ext["mach_z1"] > z1 + EPS:   # 기계실: 샤프트 위의 같은 평면 크기 상자
        raw += _box_edges(*shaft, z1, ext["mach_z1"])
    if ext["doors"] is not None:   # 승강장 문 개구부: 샤프트 면 위의 사각형(정차층마다)
        (xa, ya), (xb, yb) = ext["doors"]["edge"]
        for _lid, zl in ext["doors"]["stops"]:
            zt_ = zl + ext["doors"]["h"]
            raw += [((xa, ya, zl), (xb, yb, zl)), ((xb, yb, zl), (xb, yb, zt_)), ((xb, yb, zt_), (xa, ya, zt_)), ((xa, ya, zt_), (xa, ya, zl))]
    occ = []
    for w in g.q(scope_walls, v):
        occ.extend(g.q(vpieces, v, w))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for rf in g.q(scope_roofs, v):   # 경사 지붕 밑에 든 샤프트 선은 지붕이 가린다(없으면 이 반복은 비어 있다)
        occ.extend(g.q(_roof_vpieces, v, rf))
    lines = []
    for a, b in raw:
        pa, pb = _sw(cfg, a), _sw(cfg, b)
        e0, e1 = (pa[0], pa[1], a[2]), (pb[0], pb[1], b[2])
        if abs(e1[0] - e0[0]) < EPS and abs(e1[1] - e0[1]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        c = _clip_edge(e0, e1, cfg[7], cfg[8])
        if c is None:
            continue
        e0, e1 = c
        if abs(e1[0] - e0[0]) < EPS and abs(e1[2] - e0[2]) < EPS:   # 보는 방향과 나란한 모서리(점으로 보인다)
            continue
        for t0, t1 in (visible_parts(e0, e1, occ) if occ else [(0.0, 1.0)]):
            lines.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]), e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    return tuple(lines)


DOOR_JAMB = 150.0   # 평면의 승강장 문설주 깊이 mm(샤프트 안쪽으로)


def _on_door_edge(a, b, door) -> bool:
    """샤프트 윤곽의 변 ab 가 승강장 문이 있는 면인가(문 양끝이 모두 이 변 위에 있다)."""
    (xa, ya), (xb, yb) = door["edge"]
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = sqrt(dx * dx + dy * dy)
    return all(abs((px - a[0]) * dy - (py - a[1]) * dx) / ln < EPS for px, py in ((xa, ya), (xb, yb)))


def _door_gap_lines(a, b, door):
    """변 ab 를 문 폭만큼 비운 두 선분(proj)과 문설주 둘·문 선(symbol)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = sqrt(dx * dx + dy * dy)
    ux, uy = dx / ln, dy / ln
    ts = sorted(((px - a[0]) * ux + (py - a[1]) * uy) for px, py in door["edge"])
    out = []
    if ts[0] > SEG_MIN:
        out.append(("proj", a[0], a[1], a[0] + ux * ts[0], a[1] + uy * ts[0]))
    if ln - ts[1] > SEG_MIN:
        out.append(("proj", a[0] + ux * ts[1], a[1] + uy * ts[1], b[0], b[1]))
    nx, ny = door["normal"]
    ends = [(a[0] + ux * t, a[1] + uy * t) for t in ts]
    inner = [(px - nx * DOOR_JAMB, py - ny * DOOR_JAMB) for px, py in ends]
    for (px, py), (qx, qy) in zip(ends, inner):
        out.append(("symbol", px, py, qx, qy))
    out.append(("symbol", inner[0][0], inner[0][1], inner[1][0], inner[1][1]))
    return out


# ================================================================ 경사 지붕 (kinds "roof": 직사각형 외곽 + 박공·모임·외쪽 + 물매 + 처마 내밀기, 설계: elements-design.md 16절)
# 형상은 kinds.roof_geom 이 정한다(IFC 투영과 같은 값). 지붕은 얇은 판이라 단면·입면에서 윗면(경사면) 모서리와 두께(처마 끝 아랫선·수직 모서리)를 선으로 그린다.
#   평면: 절단 높이가 지붕 꼭대기(가장 높은 윗면)보다 높은 평면(지붕 평면: 지붕 전체를 내려다본다)에서 처마 내민 윤곽·외곽(처마 선)·용마루·추녀마루(proj)와 물매 화살표·"물매 N°" 문자(symbol)
#   단면: 절단면이 경사면을 자른 띠(수직 두께)를 절단선·채움으로(합집합 윤곽), 절단면 뒤의 경사면 모서리는 투영선
#   입면·단면: 경사 지붕이 가리는 것 = 윗면 아래로 처마 끝 높이까지의 쐐기. 높이를 16 조각으로 나눈 수평 단면(각 조각 윗면 높이의 단면: 보수적으로 작게)의 볼록 기둥 조각이다
#   벽·슬래브·계단·패밀리가 지붕을 가린다(지붕 모서리는 경사선이라 _visible_slanted). 벽이 지붕에 가려지는 것은 판정하지 않는다(벽은 처마 높이에서 끝난다)
ROOF_SLICES = 16
ROOF_SOLID_PIECE_MM = 0.5    # 지붕 판 가림 조각의 높이(mm): 판 아랫면 쪽 가림 경계 오차는 이 값 이하
ROOF_SOLID_LEAD_MM = 0.55    # 판 윗면 쪽으로 평면에서 이만큼(mm) 더 가린다: 윗면 위에 정확히 얹힌 선(처마 높이 = 벽 윗면)을 EPS_V 깊이 판정 안에서 가리려는 여유
ROOF_ARROW_BARB = (0.9396926207859084, 0.3420201433256687)   # 화살촉 20도의 (cos, sin)


def roof_info(g, r):
    """저작 지붕 하나의 절대 높이 형상 dict(roof_geom 의 값에 레벨 높이를 더한다) 또는 None. 분리된 지붕의 의도는 낡은 값이라 그리지 않는다."""
    row = Q.el(g, r)
    if row is None or row["kind"] != "roof" or row["origin"] != "authored":
        return None
    lv = g.q(Q.level_info, g.q(Q.roof_level, r))[1]
    p = row["params"]
    gm = K.roof_geom(p)

    def up(q):
        return (q[0], q[1], q[2] + lv)
    return {"faces": tuple(tuple(up(q) for q in fc) for fc in gm["faces"]), "ridges": tuple((up(a), up(b)) for a, b in gm["ridges"]),
            "hips": tuple((up(a), up(b)) for a, b in gm["hips"]), "perimeter": tuple(up(q) for q in gm["perimeter"]), "arrows": tuple(gm["arrows"]),
            "outline": tuple(gm["outline"]), "boundary": tuple(tuple(q) for q in p["boundary"]), "planes": tuple((a, b, c + lv) for a, b, c in gm["planes"]),
            "zlow": gm["zlow"] + lv, "ztop": gm["ztop"] + lv, "t": p["thickness"], "pitch": p["pitch"], "form": p["form"]}


def all_roofs(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "roof", b) if g.q(roof_info, e) is not None)
    return tuple(sorted(out))


def scope_roofs(g, v):
    """뷰가 그리는 경사 지붕. 평면은 그 레벨에 놓인 것만, 단면·입면은 모델 전체."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    if cfg[0] == "plan":
        if cfg[1] is None or is_rcp(cfg):   # 반사 천장 평면(W1 1-C 3차)은 아직 그리지 않는다
            return ()
        return tuple(e for e in sorted(Q.rel_in(g, "on_level", cfg[1])) if g.q(roof_info, e) is not None)
    return g.q(all_roofs)


def _clip_half(poly, a, b, k):
    """볼록 다각형을 a x + b y + k >= 0 쪽으로 자른다(Sutherland-Hodgman)."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        fp, fq = a * p[0] + b * p[1] + k, a * q[0] + b * q[1] + k
        if fp >= 0.0:
            out.append(p)
        if (fp >= 0.0) != (fq >= 0.0):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def roof_slices(g, r):
    """지붕이 가리는 쐐기(윗면 아래 ~ 처마 끝 높이)를 높이 16 조각으로: ((z0, z1, 평면 볼록 다각형(반시계)), ...). 조각의 다각형은 그 조각 윗면 높이의 수평 단면이다."""
    info = g.q(roof_info, r)
    if info is None:
        return ()
    zl, zt = info["zlow"], info["ztop"]
    out = []
    for i in range(ROOF_SLICES):
        z0, z1 = zl + (zt - zl) * i / ROOF_SLICES, zl + (zt - zl) * (i + 1) / ROOF_SLICES
        poly = list(info["outline"])
        for a, b, c in info["planes"]:
            poly = _clip_half(poly, a, b, c - z1)
            if len(poly) < 3:
                break
        if len(poly) >= 3 and abs(_area2(poly)) > 2 * EPS:
            out.append((z0, z1, tuple(poly)))
    return tuple(out)


def _roof_vpieces(g, v, r):
    """단면·입면에서 지붕이 가리는 볼록 기둥 조각 ((z0, z1, 다각형(s,w)), ...)."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "plan":
        return ()
    out = []
    for z0, z1, poly in g.q(roof_slices, r):
        sw = [_sw(cfg, p) for p in poly]
        if _area2(sw) < 0:
            sw.reverse()
        sw = _clip_w(sw, cfg[7], cfg[8])
        if len(sw) >= 3 and _area2(sw) > 2 * EPS:
            out.append((z0, z1, tuple(sw)))
    return tuple(out)


def roof_solid_slices(g, r):
    """지붕 판(윗면 ~ 두께만큼 아래)의 실제 부피를 높이 조각으로: ((z0, z1, 평면 볼록 다각형), ...). 쐐기(roof_slices)와 달리 판 아래 빈 곳(처마 밑)은 채우지 않는다.
    조각의 다각형은 면마다 {조각 아랫면 - 윗면 쪽 여유 <= 윗면 높이 <= 조각 윗면 + 두께}: 조각의 높이 구간 어디서든 판이 덮는 자리의 합집합이다(가림 판정은 다각형의 관찰자 쪽 변만 쓰므로 가장자리의 접촉도 가린 것으로 친다).
    조각 높이는 ROOF_SOLID_PIECE_MM(0.5mm) 이라 판 아랫면 쪽 가림 경계는 0.5mm 이하로만 더 가린다(예전엔 두께의 1/4 만큼 덜 가렸다). 윗면 쪽은 ROOF_SOLID_LEAD_MM 의 평면 여유만큼 더 가린다.
    조각은 면마다 판 두께 구간에서만 만든다(윗면이 닿는 높이 범위 [면의 최저 윗면 - 두께, 면의 최고 윗면]). 벽 윗선처럼 지붕 판 안에 묻힌 선과 처마 끝 아래 선을 가리는 데 쓴다."""
    info = g.q(roof_info, r)
    if info is None or info["t"] <= EPS:
        return ()
    t = info["t"]
    out = []
    for fc, (a, b, c) in zip(info["faces"], info["planes"]):
        tops = [q[2] for q in fc]
        zb, zt = min(tops) - t, max(tops)                 # 이 면의 판이 차지하는 높이 범위
        n = max(1, int((zt - zb) / ROOF_SOLID_PIECE_MM + 1.0 - 1e-9))   # 올림(math.ceil 대신: 이 모듈은 sqrt 만 쓴다)
        h = (zt - zb) / n
        xy = [(q[0], q[1]) for q in fc]
        lead = ROOF_SOLID_LEAD_MM * sqrt(a * a + b * b)                          # 평면 여유를 높이로 환산(윗면 기울기 배)
        for i in range(n):
            z0, z1 = zb + h * i, zb + h * (i + 1)
            poly = _clip_half(xy, a, b, c - z0 + lead)                         # 윗면이 조각 아랫면(-여유) 이상인 쪽
            if len(poly) >= 3:
                poly = _clip_half(poly, -a, -b, z1 + t - c)                    # 조각 윗면 + 두께 이하인 쪽
            if len(poly) >= 3 and abs(_area2(poly)) > 2 * EPS:
                out.append((z0 - 2 * EPS, z1 + 2 * EPS, tuple(poly)))          # z 를 조금 겹쳐 이음매 높이의 선이 두 조각 사이로 새지 않게 한다
    return tuple(out)


def _roof_solid_vpieces(g, v, r):
    """단면·입면에서 지붕 판이 가리는 볼록 기둥 조각 ((z0, z1, 다각형(s,w)), ...)."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] in ("plan", "site"):
        return ()
    out = []
    for z0, z1, poly in g.q(roof_solid_slices, r):
        sw = [_sw(cfg, p) for p in poly]
        if _area2(sw) < 0:
            sw.reverse()
        sw = _clip_w(sw, cfg[7], cfg[8])
        if len(sw) >= 3 and _area2(sw) > 2 * EPS:
            out.append((z0, z1, tuple(sw)))
    return tuple(out)


def roof_edges3(g, r):
    """지붕의 모서리(세계 좌표 3D 선분, 중복 없음): 경사면 윗면의 둘레(용마루·추녀마루 포함), 바깥 둘레의 아랫선(두께만큼 아래)과 꼭짓점의 수직 모서리."""
    info = g.q(roof_info, r)
    if info is None:
        return ()
    seen, out = set(), []

    def add(a, b):
        key = tuple(sorted((tuple(round(c, 3) for c in a), tuple(round(c, 3) for c in b))))
        if key not in seen:
            seen.add(key)
            out.append((a, b))
    for fc in info["faces"]:
        for i in range(len(fc)):
            add(fc[i], fc[(i + 1) % len(fc)])
    pm, t = info["perimeter"], info["t"]
    for i in range(len(pm)):
        a, b = pm[i], pm[(i + 1) % len(pm)]
        add((a[0], a[1], a[2] - t), (b[0], b[1], b[2] - t))
        add((a[0], a[1], a[2] - t), a)
    return tuple(out)


def roof_cut_rects(g, v, r):
    """단면: 절단면(w=near)이 경사면을 자른 띠(수직 두께) 다각형들 ((s,z) 네 점, 반시계). 면마다 하나(합집합 윤곽은 outline_edges 가 낸다)."""
    cfg = g.q(view_cfg, v)
    info = g.q(roof_info, r)
    if info is None or cfg is None or cfg[0] == "plan" or not cfg[9]:
        return ()
    near = cfg[7]
    out = []
    for fc in info["faces"]:
        pts = [(_sw(cfg, q), q[2]) for q in fc]
        xs = []
        n = len(pts)
        for i in range(n):
            (a, za), (b, zb) = pts[i], pts[(i + 1) % n]
            if (a[1] <= near) != (b[1] <= near):
                t = (near - a[1]) / (b[1] - a[1])
                xs.append((a[0] + t * (b[0] - a[0]), za + t * (zb - za)))
        if len(xs) < 2:
            continue
        xs.sort()
        (sa, za), (sb, zb) = xs[0], xs[-1]
        if sb - sa > EPS:
            out.append(((sa, za - info["t"]), (sb, zb - info["t"]), (sb, zb), (sa, za)))
    return tuple(out)


def roof_plan_parts(g, v, r):
    """평면의 지붕 하나: (선분들, 문자들). 절단 높이가 지붕 꼭대기보다 높은 평면(지붕 평면)에서만 만든다(지붕을 자르는 높이의 평면에는 그리지 않는다)."""
    cfg = g.q(view_cfg, v)
    info = g.q(roof_info, r)
    if cfg is None or cfg[0] != "plan" or info is None or cfg[2] <= info["ztop"] + EPS:
        return ((), ())
    sc = cfg[4]
    segs, labels = [], []
    for ring in (info["outline"], info["boundary"]):   # 처마를 내민 윤곽과 외곽(처마 선)
        n = len(ring)
        segs += [("proj", ring[i][0], ring[i][1], ring[(i + 1) % n][0], ring[(i + 1) % n][1]) for i in range(n)]
    for a, b in info["ridges"] + info["hips"]:
        segs.append(("proj", a[0], a[1], b[0], b[1]))
    if info["ridges"]:
        a, b = info["ridges"][0]
        labels.append((round((a[0] + b[0]) / 2.0, 3), round((a[1] + b[1]) / 2.0 + 0.6 * sc, 3), "용마루", LABEL_SUB_H, "middle"))
    bc, bs = ROOF_ARROW_BARB
    for fc, ((cx, cy), (dx, dy)) in zip(info["faces"], info["arrows"]):
        xs, ys = [q[0] for q in fc], [q[1] for q in fc]
        ln = min(ROOF_ARROW_LEN * sc, 0.4 * min(max(xs) - min(xs), max(ys) - min(ys)))
        if ln <= SEG_MIN:
            continue
        tail, head = (cx - dx * ln / 2.0, cy - dy * ln / 2.0), (cx + dx * ln / 2.0, cy + dy * ln / 2.0)
        segs.append(("symbol", tail[0], tail[1], head[0], head[1]))
        hl = ln * 0.25
        for sg in (1.0, -1.0):   # 화살촉: 진행 방향 뒤로 +-20도 벌어진 두 선
            ux, uy = -dx, -dy
            segs.append(("symbol", head[0], head[1], head[0] + (ux * bc - sg * uy * bs) * hl, head[1] + (sg * ux * bs + uy * bc) * hl))
        pn, pc = K.sincos_deg(info["pitch"])   # 물매 표기: 각도와 가로 10 에 대한 세로(관행 "물매 5/10")
        txt = f"물매 {info['pitch']:g}°({pn / pc * 10:.1f}/10)"
        lx, ly = _arrow_label_pos(cx, cy, dx, dy, txt, sc)
        labels.append((lx, ly, txt, LABEL_SUB_H, "middle"))
    return (tuple(segs), tuple(labels))


def roof_lines(g, v, r):
    """경사 지붕 하나가 뷰에 기여하는 선분."""
    cfg = g.q(view_cfg, v)
    info = g.q(roof_info, r)
    if cfg is None or info is None:
        return ()
    if cfg[0] == "plan":
        return g.q(roof_plan_parts, v, r)[0]
    mine = list(g.q(_roof_vpieces, v, r))
    occ = mine[:]
    for w in g.q(scope_walls, v):
        occ.extend(g.q(vpieces, v, w))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for o in g.q(scope_roofs, v):
        if o != r:
            occ.extend(g.q(_roof_vpieces, v, o))
    lines = []
    for a, b in g.q(roof_edges3, r):
        pa, pb = _sw(cfg, a), _sw(cfg, b)
        e0, e1 = (pa[0], pa[1], a[2]), (pb[0], pb[1], b[2])
        if abs(e1[0] - e0[0]) < EPS and abs(e1[1] - e0[1]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        c = _clip_edge(e0, e1, cfg[7], cfg[8])
        if c is None:
            continue
        e0, e1 = c
        if abs(e1[0] - e0[0]) < EPS and abs(e1[2] - e0[2]) < EPS:   # 화면에서 한 점으로 보이는 선
            continue
        for t0, t1 in (_visible_slanted(e0, e1, occ) if occ else [(0.0, 1.0)]):
            lines.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]), e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    if cfg[9]:
        rects = g.q(roof_cut_rects, v, r)
        if rects:
            others = [(w, q) for w in g.q(scope_walls, v) for q in g.q(vcut_rects, v, w)]
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(rects), others, r)]
    return tuple(lines)


# ================================================================ 공간 (kinds "space") 과 문자 층
# 공간은 선·채움을 만들지 않는다(윤곽은 벽이 준다). 평면에 실명과 면적 문자(labels)만 놓는다.
# 문자 항목: (x, y, 문자, 글자 높이 mm(용지 위), 앵커 "middle"). 좌표는 선분과 같은 뷰 좌표(평면 x,y / 단면·입면 s,z).
LABEL_NAME_H, LABEL_SUB_H = 2.5, 2.0


def space_info(g, s):
    """(경계 다각형(반시계 튜플), 실명, 분류) 또는 None. 저작 공간만 그린다."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "space" or row["origin"] != "authored":
        return None
    p = row["params"]
    poly = [tuple(q) for q in p["boundary"]]
    if _area2(poly) < 0:
        poly.reverse()
    return (tuple(poly), p["name"], p["category"])


def space_holes(g, s):
    """저작 공간의 섬 구멍 다각형들(반시계 튜플의 튜플). 없으면 빈 튜플."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "space" or row["origin"] != "authored":
        return ()
    return tuple(tuple(tuple(q) for q in o) for o in row["params"].get("holes", ()))


def all_spaces(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "space", b) if g.q(space_info, e) is not None)
    return tuple(sorted(out))


def scope_spaces(g, v):
    """평면은 그 레벨에 놓인 공간만, 단면·입면은 없다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "plan" or cfg[1] is None:
        return ()
    return tuple(e for e in sorted(Q.rel_in(g, "on_level", cfg[1])) if g.q(space_info, e) is not None)


def polygon_centroid(poly):
    """다각형의 면적 무게중심. 면적이 0 에 가까우면 꼭짓점 평균."""
    a2 = _area2(poly)
    n = len(poly)
    if abs(a2) < 1e-6:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    cx = cy = 0.0
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        c = p[0] * q[1] - q[0] * p[1]
        cx += (p[0] + q[0]) * c
        cy += (p[1] + q[1]) * c
    return (cx / (3.0 * a2), cy / (3.0 * a2))


def area_text(poly, holes=()) -> str:
    """면적 문자: ㎡, 소수 둘째 자리. holes(섬 구멍)가 있으면 그 면적을 뺀다."""
    a2 = abs(_area2(poly)) - sum(abs(_area2(h)) for h in holes)
    return f"{a2 / 2.0 / 1.0e6:.2f}㎡"


def label_point(poly, holes):
    """공간 문자의 기준점: 무게중심. 섬 구멍(기둥·닫힌 벽 고리) 안이면 구멍 밖의 가장 가까운 격자점으로 옮긴다(결정적)."""
    cx, cy = polygon_centroid(poly)
    if not holes or not any(K.point_in_polygon((cx, cy), [list(q) for q in h]) >= 0 for h in holes):
        return (cx, cy)
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    outer = [list(q) for q in poly]
    hs = [[list(q) for q in h] for h in holes]
    best = None
    for i in range(1, 16):
        for j in range(1, 16):
            q = (min(xs) + (max(xs) - min(xs)) * i / 16.0, min(ys) + (max(ys) - min(ys)) * j / 16.0)
            if K.point_in_polygon(q, outer) == 1 and all(K.point_in_polygon(q, h) < 0 for h in hs):
                d = (q[0] - cx) ** 2 + (q[1] - cy) ** 2
                if best is None or d < best[0]:
                    best = (d, q)
    return (cx, cy) if best is None else best[1]


def plan_space_labels(g, v, s):
    """평면의 공간 하나: 실명(위)과 면적(아래) 문자."""
    cfg = g.q(view_cfg, v)
    info = g.q(space_info, s)
    if info is None or cfg is None or cfg[0] != "plan":
        return ()
    if len(cfg) == 6:   # 반사 천장 평면·면적 평면(W1 1-C 3차)
        from dobim_core import viewplans as VP
        return VP.mode_space_labels(g, cfg, s, info)
    if cfg[1] is not None and cfg[2] > g.q(Q.level_info, cfg[1])[1] + Q.el(g, s)["params"]["height"] + EPS:
        return ()   # 절단 높이가 공간 윗면보다 높은 평면(지붕 평면)은 방을 자르지 못하고 지붕 위에서 내려다본다: 방 이름·면적은 관행상 쓰지 않는다
    holes = g.q(space_holes, s)
    cx, cy = label_point(info[0], holes) if holes else polygon_centroid(info[0])
    sc = cfg[4]
    return ((round(cx, 3), round(cy + 0.9 * sc, 3), info[1], LABEL_NAME_H, "middle"),
            (round(cx, 3), round(cy - 2.0 * sc, 3), area_text(info[0], holes), LABEL_SUB_H, "middle"))


def view_labels(g, v):
    """뷰의 문자 층: ((x, y, 문자, 높이 mm, 앵커), ...). 선분과 달리 병합하지 않고 만든 순서(공간 id 순 등)를 유지한다."""
    cfg = g.q(view_cfg, v)
    if cfg is None:
        from dobim_core import viewplans as VP   # 범례 뷰(W1 1-C 3차): 범례가 아니면 빈 값
        return g.q(VP.legend_labels, v)
    if cfg[0] == "site":
        return g.q(site_labels, v)
    out = []
    for s in g.q(scope_spaces, v):
        out.extend(g.q(plan_space_labels, v, s))
    for s in g.q(scope_foreign_spaces, v):   # 외래 공간(IfcSpace)의 실명·면적(2차): 외래 공간이 없으면 문자는 이전과 같다
        out.extend(g.q(foreign_space_labels, v, s))
    if cfg[0] == "plan":
        for sl in g.q(scope_slabs, v):
            out.extend(g.q(plan_roof_marks, v, sl)[1])   # 지붕 슬래브의 물매 문자(지붕이 아니면 없다)
        for e in g.q(scope_elevators, v):
            out.extend(g.q(plan_elevator_labels, v, e))
        for rf in g.q(scope_roofs, v):
            out.extend(g.q(roof_plan_parts, v, rf)[1])   # 경사 지붕의 용마루·물매 문자(지붕 평면에서만)
    for w in g.q(scope_walls, v):
        if cfg[0] == "plan":
            out.extend(g.q(plan_fire_labels, v, w))
        elif cfg[0] == "elev":
            out.extend(g.q(elev_finish, v, w)[1])
    if cfg[0] == "elev":
        out.extend(g.q(site_sign_labels, v))
    elif cfg[0] == "sec":
        out.extend(g.q(site_section_labels, v))
    out.extend(g.q(view_grid_labels, v))   # 통심선 기호 문자(19단계)
    if len(cfg) == 6 and cfg[5] != "rcp":   # 면적 평면의 면적 이름·값·합계(W1 1-C 3차)
        from dobim_core import viewplans as VP
        out.extend(g.q(VP.area_labels, v))
    opts = g.q(view_opts, v)
    if "crop" in opts:   # 부위 상세: 잘라낸 사각형 밖의 문자는 뺀다
        c = opts["crop"]
        out = [t for t in out if c[0] - EPS <= t[0] <= c[2] + EPS and c[1] - EPS <= t[1] <= c[3] + EPS]
    if "dims" in opts:   # 계단·승강기 치수 표기에 딸린 문자(잘라내기와 무관하게 요소 곁에 놓는다)
        out.extend(g.q(view_dim_labels, v))
    return tuple(out)


# ================================================================ 방화(#3)·외부 마감(#6): 기존 속성만 읽는다 (새 종류 없음, 설계 8절)
#   평면: Pset_WallCommon.FireRating 이 있는 벽 -> 중심선 파선(symbol) + "FW 등급" 문자, Pset_DoorCommon.FireRating 이 있는 문 -> "FD 등급" 문자
#   입면: Pset_WallCommon.IsExternal 이 참이고 재료가 있는 벽 -> 앞면 보이는 부분에 45도 해치(hatch) + 재료 이름 문자
FIRE_DASH, FIRE_GAP = 4.0, 1.2     # 방화벽 중심선 파선의 마디·간격(용지 mm)
HATCH_STEP = 3.5                   # 해치 가로 간격(용지 mm)
LABEL_FIRE_H = 2.0


def _prop(g, e, pset, prop):
    return Q.psets(g, e).get(pset, {}).get(prop)


def fire_rating(g, e, pset):
    """방화 등급 문자(앞 16자). 값이 없거나 문자열이 아니거나 비어 있으면 None."""
    v = _prop(g, e, pset, "FireRating")
    return v.strip()[:16] if isinstance(v, str) and v.strip() else None


def _truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "yes", "y", "1", "예", "외부"))


def plan_fire_lines(g, v, w):
    """방화벽(FireRating 이 있는 벽)의 중심선 파선. 마디 길이는 용지 위 일정하다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "plan" or fire_rating(g, w, "Pset_WallCommon") is None:
        return ()
    x, y, c, s, _ = g.q(Q.wall_frame, w)
    start, end, _st = g.q(Q.wall_trim, w)
    dash, gap = FIRE_DASH * cfg[4], FIRE_GAP * cfg[4]
    out = []
    t = start
    while t < end - SEG_MIN:
        t1 = min(t + dash, end)
        out.append(("symbol", x + c * t, y + s * t, x + c * t1, y + s * t1))
        t = t1 + gap
    return tuple(out)


def plan_fire_labels(g, v, w):
    """방화벽·방화문 문자: 벽은 왼쪽, 문은 오른쪽 바깥에 놓는다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "plan":
        return ()
    x, y, c, s, _ = g.q(Q.wall_frame, w)
    half = Q.el(g, w)["params"]["thickness"] / 2.0 + 1.6 * cfg[4]
    out = []
    fr = fire_rating(g, w, "Pset_WallCommon")
    if fr is not None:
        start, end, _st = g.q(Q.wall_trim, w)
        m = (start + end) / 2.0
        out.append((round(x + c * m - s * half, 3), round(y + s * m + c * half, 3), "FW " + fr, LABEL_FIRE_H, "middle"))
    for d in (() if is_rcp(cfg) else g.q(Q.wall_hosted, w)):   # 반사 천장 평면(W1 1-C 3차)에는 문이 없다
        row = Q.el(g, d)
        fd = fire_rating(g, d, "Pset_DoorCommon") if row is not None and row["kind"] == "door" else None
        if fd is not None:
            wx, wy, _z = g.q(Q.door_world, d)
            out.append((round(wx + s * half, 3), round(wy - c * half, 3), "FD " + fd, LABEL_FIRE_H, "middle"))
    return tuple(out)


def _text_w(text, size):
    """문자 층 글의 대략 폭(용지 mm). 한글 등 넓은 글자는 글자 높이만큼, 그 밖은 0.58배(drawing_export.label_width 와 같은 규칙)."""
    return sum(size * (1.0 if ord(c) >= 0x2E80 else 0.58) for c in text)


def _visible_slanted(e0, e1, occluders):
    """z 가 변하는(대각선) 선분의 보이는 매개변수 구간들. 가림 조각의 z 범위 안에 드는 구간만 가려진다(수평·수직은 visible_parts)."""
    dz = e1[2] - e0[2]
    if abs(dz) < 1e-9:
        return visible_parts(e0, e1, occluders)
    hidden = []
    for z0, z1, poly in occluders:
        ta, tb = (z0 + EPS - e0[2]) / dz, (z1 - EPS - e0[2]) / dz
        if ta > tb:
            ta, tb = tb, ta
        ta, tb = max(ta, 0.0), min(tb, 1.0)
        if tb - ta <= 1e-9:
            continue
        for a, b in _hidden_h(poly, e0, e1):
            lo, hi = max(a, ta), min(b, tb)
            if hi - lo > 1e-9:
                hidden.append((lo, hi))
    if not hidden:
        return [(0.0, 1.0)]
    hidden.sort()
    out, cur = [], 0.0
    for a, b in hidden:
        if a > cur + 1e-9:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < 1.0 - 1e-9:
        out.append((cur, 1.0))
    return out


def elev_finish(g, v, w):
    """입면의 외부 마감: (해치 선분들, 재료 문자들). IsExternal 이 참이고 재료 이름이 있는 벽만, 앞면의 보이는 부분에 45도 해치."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "elev" or "finish" not in g.q(view_opts, v) or not _truthy(_prop(g, w, "Pset_WallCommon", "IsExternal")):
        return ((), ())   # 외부 마감 해치·재료 문자는 뷰의 finish 옵션을 켠 입면에만 그린다(관행: 벽 전체에 해치를 깔지 않는다)
    mat = g.input(("mat", w))   # 외부 마감은 material.set 으로 지정한 마감 재료만 쓴다(층 구성의 가장 두꺼운 층 = 구조체 이름(철근콘크리트)을 입면에 깔지 않는다)
    pieces = g.q(vpieces, v, w)
    if not isinstance(mat, str) or not mat.strip() or not pieces:
        return ((), ())
    occ = _wall_occluders(g, v, w)
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    step = HATCH_STEP * cfg[10]
    text = mat.strip()[:24]
    need = _text_w(text, LABEL_FIRE_H) * cfg[10]               # 글이 들어갈 만큼 넓은 조각에만 재료 문자를 놓는다(벽 끝면 같은 좁은 조각은 해치만)
    lines = []
    best = None
    for z0, z1, poly in pieces:
        s0, s1 = min(p[0] for p in poly), max(p[0] for p in poly)
        wf = min(p[1] for p in poly)                           # 앞면 깊이
        if s1 - s0 <= EPS or z1 - z0 <= EPS:
            continue
        mine = []
        k = _floor((s0 - z1) / step) + 1                       # 세계 격자에 맞춘 해치선 번호(c = s - z): 이웃 조각과 선이 이어진다
        while k * step < s1 - z0:
            c = k * step
            sa, sb = max(s0, c + z0), min(s1, c + z1)
            if sb - sa > SEG_MIN:
                e0, e1 = (sa, wf, sa - c), (sb, wf, sb - c)
                for t0, t1 in _visible_slanted(e0, e1, occ):
                    if (t1 - t0) * (sb - sa) * 1.4142 < SEG_MIN:   # 가림 경계에 남은 부스러기 선분은 버린다(병합 단계도 같은 길이로 거른다)
                        continue
                    mine.append(("hatch", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]),
                                 e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
            k += 1
        lines.extend(mine)
        if mine and s1 - s0 >= need and (best is None or len(mine) > best[0]):   # 보이는 해치가 가장 많은 넓은 조각의 보이는 부분 가운데에 재료 문자를 놓는다
            xs = [c for ln in mine for c in (ln[1], ln[3])]
            zs = [c for ln in mine for c in (ln[2], ln[4])]
            best = (len(mine), (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0)
    labels = ()
    if best is not None:                                        # 앞의 벽에 가려 해치가 하나도 안 보이는 벽에는 문자도 없다
        labels = ((round(best[1], 3), round(best[2], 3), text, LABEL_FIRE_H, "middle"),)
    return (tuple(lines), labels)


def _door_symbol(sol, op):
    """문 한 개의 평면 기호: 열린 문짝 선 + 1/4 원 호. +v 쪽으로 열리고 경첩은 개구부의 시작 쪽이다.
    호는 유리식 매개화로 sin/cos 없이 그린다: 각 = 2*atan(t) (t 0..1 -> 0..90도)."""
    u0, u1 = op[0], op[1]
    h = sol[4]
    width = u1 - u0
    hinge = (u0, h)
    pts = []
    for i in range(ARC_N + 1):
        t = i / ARC_N
        k = 1.0 + t * t
        pts.append(_world(sol, hinge[0] + width * (1.0 - t * t) / k, hinge[1] + width * (2.0 * t) / k))
    out = [("symbol", *_world(sol, *hinge), *_world(sol, u0, h + width))]
    for a, b in zip(pts, pts[1:]):
        out.append(("symbol", a[0], a[1], b[0], b[1]))
    return out


def plan_wall_lines(g, v, w):
    cfg = g.q(view_cfg, v)
    cut, proj = g.q(plan_pieces, v, w)
    sol = g.q(wall_solid, w)
    lines = []
    if cut or proj:
        nb = g.q(plan_nbrs, v, w)
        if cut:
            others = [(o, q) for o in nb for q in g.q(plan_pieces, v, o)[0]]
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(cut), others, w)]
        if proj:
            others = [(o, q) for o in nb for q in g.q(plan_pieces, v, o)[1]]
            lines += [("proj", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(proj), others, w)]
    if (cut or sol[7] - EPS <= cfg[2] <= sol[8] + EPS) and not is_rcp(cfg):   # 20단계: 개구부가 벽 길이를 다 차지해 절단 조각이 없어도 기호는 그린다
        for op in g.q(wall_door_openings, w):
            if op[2] <= cfg[2] + EPS and cfg[2] <= op[3] + EPS:   # 절단면이 문 개구부 높이 안에 있을 때만(문 위를 자르면 벽이 이어져 문이 안 보인다)
                lines += _door_symbol(sol, op)
        for f in g.q(hosted_fam, w):                              # 창·패밀리 문: 패밀리가 평가한 평면 기호(창은 벽 두께를 가로지르는 평행선)
            if f[2] <= cfg[2] + EPS and cfg[2] <= f[3] + EPS:
                lines += _plan_items(f[6], lambda u, vv, _o=f[4]: _world(sol, _o + u, vv))
    return tuple(lines)


# ================================================================ 큰 축척(1:50 이하) 표현 수준 (SC-09, B-17)
# 시트·내보내기가 detail 을 켤 때만 더하는 가는 선들이다(view_lines 는 그대로라 1:100 기본 도면과 켜지 않은 출력은 바이트가 같다).
# 모델에 마감 층 구성이 없으므로 규약값을 쓴다: 마감 두께 FINISH_T, 문짝 두께 LEAF_T. 재료가 지정된 벽에만 마감선을 긋는다.
DETAIL_SCALE = 50      # 뷰 축척이 1:이 값 이하(1:50, 1:20 ...)일 때 상세 표현
FINISH_T = 20.0        # 벽 마감 두께 규약(mm): 벽 양면에서 이만큼 안쪽에 마감선
LEAF_T = 40.0          # 문짝 두께 규약(mm)
DETAIL_MIN_T = 100.0   # 이보다 얇은 벽은 마감선을 긋지 않는다


def _cut_intervals(sol, cutz):
    """절단 높이에서 벽이 속이 찬 구간들 ((a, b), ...)."""
    if sol[7] - EPS <= cutz <= sol[8] + EPS:
        for z0, z1, ivs in sol[10]:
            if z0 - EPS <= cutz <= z1 + EPS:
                if sol[11] is not None:   # 상단 윤곽(21단계): 윤곽이 절단 높이보다 높은 구간만
                    return _isect_ivs(ivs, WT.ivs_between(sol[11], cutz, BIG))
                return tuple(ivs)
    return ()


def _door_leaf_map(g, w):
    """벽의 문 개구부 (u0, u1, z0, z1) -> 문 속성의 문짝 두께(leaf). 입력이 없는 문은 넣지 않는다(규약값을 쓴다)."""
    start, end, _ = g.q(Q.wall_trim, w)
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    zb, zt = elev + WT.base(Q.el(g, w)["params"]), elev + _top_rel(Q.el(g, w)["params"])
    out = {}
    for d in g.q(Q.wall_hosted, w):
        row = Q.el(g, d)
        if row["kind"] != "door" or "leaf" not in row["params"]:
            continue
        box = g.q(hosted_opening, d, elev)
        if box is None:
            continue
        u0, u1, z0, z1 = max(start, box[0]), min(end, box[1]), max(zb, box[2]), min(zt, box[3])
        if u1 - u0 > EPS and z1 - z0 > EPS:
            out[(u0, u1, z0, z1)] = row["params"]["leaf"]
    return out


def plan_detail_lines(g, v, w):
    """평면 벽 하나의 상세 선: 재료가 지정된 벽의 양면 마감선(가는 hatch 선, 이웃 벽 절단 조각 안쪽은 지운다)과, 문의 문짝 두께·문턱선.
    큰 축척(DETAIL_SCALE 이하)의 평면에서만 낸다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "plan" or cfg[1] is None or cfg[4] > DETAIL_SCALE:
        return ()
    sol = g.q(wall_solid, w)
    ivs = _cut_intervals(sol, cfg[2])
    if not ivs:
        return ()
    out = []
    h = sol[4]
    layers = Q.el(g, w)["params"].get("layers")
    if layers:   # 층 구성 입력이 있으면 층 경계마다 선(첫 층의 바깥 면 = 지역 -v 면, 마지막 층 쪽 = +v 면)
        offs = [-h + c for c in K.layer_bounds(layers)[1:-1]]
    elif Q.material(g, w) is not None and 2.0 * h >= DETAIL_MIN_T:   # 입력이 없으면 규약: 재료가 있는 벽은 양면에서 FINISH_T 안쪽
        offs = [-(h - FINISH_T), h - FINISH_T]
    else:
        offs = []
    if offs:
        others = [q for o in g.q(plan_nbrs, v, w) for q in g.q(plan_pieces, v, o)[0]]
        ux, uy = sol[2], sol[3]
        for a, b in ivs:
            for off in offs:
                p0 = _world(sol, a, off)
                cuts = []
                for q in others:
                    iv = _inside_interval(p0, ux, uy, b - a, q)
                    if iv is not None:
                        cuts.append(iv)
                for q0, q1 in _keep(p0, ux, uy, b - a, cuts):
                    out.append(("hatch", q0[0], q0[1], q1[0], q1[1]))
    leaf_of = g.q(_door_leaf_map, w)
    for op in g.q(wall_door_openings, w):
        if op[2] <= cfg[2] + EPS and cfg[2] <= op[3] + EPS:
            u0, u1 = op[0], op[1]
            width = u1 - u0
            leaf = leaf_of.get(op, LEAF_T)   # 문 속성(leaf)이 있으면 그 값, 없으면 규약값
            for pa, pb in (((u0 + leaf, h), (u0 + leaf, h + width)), ((u0, h + width), (u0 + leaf, h + width))):   # 문짝 두께(경첩 쪽 문짝 옆선과 끝 마구리)
                out.append(("symbol", *_world(sol, *pa), *_world(sol, *pb)))
            out.append(("hatch", *_world(sol, u0, 0.0), *_world(sol, u1, 0.0)))    # 문턱선(개구부를 가로지르는 가는 선)
    return tuple(out)


# ---- 1:50 단면·입면 상세(SC-09, B-17): 벽 층 경계선·층별 절단 해치, 슬래브 두께 층, 천장선, 창대(입면)
# 층 구성(layers) 입력이 있는 벽·슬래브, 천장 높이(ceiling_height)가 있는 공간, 창에만 더한다. 입력이 없으면 아무것도 더하지 않는다.
LAYER_HATCH_MIN = 1.5     # 층 해치는 용지 위 두께가 이 값(mm) 이상인 층에만 긋는다(마감층처럼 얇은 층은 경계선만)
LAYER_HATCH_STEP = 2.0    # 층 해치 간격(용지 mm)
SILL_DROP, SILL_EXT = 40.0, 50.0   # 창대: 창 아래로 내려오는 두께(mm)와 양쪽으로 더 나가는 길이(mm). 규약값


def _sec_chord(cfg, sol, a, b, vv):
    """벽 지역의 선(u = a..b, v = vv)과 절단면(w = near)이 만나는 s. 만나지 않으면 None."""
    s0, w0 = _sw(cfg, _world(sol, a, vv))
    s1, w1 = _sw(cfg, _world(sol, b, vv))
    if abs(w1 - w0) < 1e-9:
        return None
    t = (cfg[7] - w0) / (w1 - w0)
    if t < -1e-9 or t > 1.0 + 1e-9:
        return None
    return s0 + t * (s1 - s0)


def _hatch_pattern(material) -> int:
    """층 재료마다 정해지는 해치 무늬 번호(0: 45도, 1: 135도, 2: 격자). 같은 재료는 늘 같은 무늬다(결정적)."""
    return sum(ord(c) for c in material) % 3


def _hatch_rect(s0, s1, z0, z1, pattern, step, others):
    """절단 사각형 (s0..s1) x (z0..z1) 안의 해치 선분들 ("hatch" 등급). 선은 세계 격자(s -+ z = 간격의 정수배)에 맞춰 이웃 조각과 이어진다.
    others = 이 사각형 안에서 지울 다른 절단 사각형들 (rs0, rs1, rz0, rz1)(안쪽에 든 부분을 뺀다)."""
    out = []
    fams = (((1, 1),) if pattern == 0 else ((-1, 1),) if pattern == 1 else ((1, 1), (-1, 1)))   # (기울기 부호, 간격 배율)
    for sign, _ in fams:
        stp = step * (2.0 if pattern == 2 else 1.0)
        if sign > 0:   # z = s - c
            k = _floor((s0 - z1) / stp) + 1
            lim = s1 - z0
        else:          # z = c - s
            k = _floor((s0 + z0) / stp) + 1
            lim = s1 + z1
        while k * stp < lim:
            c = k * stp
            if sign > 0:
                sa, sb = max(s0, c + z0), min(s1, c + z1)
            else:
                sa, sb = max(s0, c - z1), min(s1, c - z0)
            k += 1
            if sb - sa <= SEG_MIN:
                continue
            cuts = []
            for rs0, rs1, rz0, rz1 in others:
                lo, hi = (c + rz0, c + rz1) if sign > 0 else (c - rz1, c - rz0)
                lo, hi = max(lo, rs0), min(hi, rs1)
                if hi - lo > EPS:
                    cuts.append((lo, hi))
            for a, b in _subtract([(sa, sb)], sorted(cuts)):
                if b - a > SEG_MIN:
                    z_a, z_b = (a - c, b - c) if sign > 0 else (c - a, c - b)
                    out.append(("hatch", a, z_a, b, z_b))
    return out


def _cut_boxes(rects):
    """절단 사각형 다각형들 -> (s0, s1, z0, z1) 상자들."""
    return [(min(p[0] for p in r), max(p[0] for p in r), min(p[1] for p in r), max(p[1] for p in r)) for r in rects]


def vwall_detail(g, v, w):
    """단면에서 층 구성(layers)이 있는 벽의 상세: 절단면의 층 경계선과 층별 절단 해치(가는 hatch 선). 큰 축척(DETAIL_SCALE 이하)의 단면만.
    이웃 벽의 절단 사각형 안쪽은 지운다(경계선·해치가 이웃 벽 위로 나가지 않게)."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "sec" or cfg[10] > DETAIL_SCALE:
        return ()
    layers = Q.el(g, w)["params"].get("layers")
    if not layers:
        return ()
    sol = g.q(wall_solid, w)
    h = sol[4]
    bounds = K.layer_bounds(layers)
    others = _cut_boxes([q for o in g.q(vnbrs, v, w) for q in g.q(vcut_rects, v, o)])
    step = LAYER_HATCH_STEP * cfg[10]
    out = []
    for z0, z1, ivs in _wall_bands(g, w, sol):
        for a, b in ivs:
            ss = [_sec_chord(cfg, sol, a, b, -h + c) for c in bounds]
            if any(x is None for x in ss):
                continue   # 절단면이 조각 끝을 스쳐 층 경계가 다 만나지 않는 조각은 그리지 않는다
            for x in ss[1:-1]:   # 층 경계선: 바깥 면 둘은 절단 윤곽이 이미 그린다
                zc = [(max(rz0, z0), min(rz1, z1)) for rs0, rs1, rz0, rz1 in others if rs0 + EPS < x < rs1 - EPS and rz1 > z0 and rz0 < z1]
                for q0, q1 in _subtract([[z0, z1]], sorted(zc)):
                    if q1 - q0 > SEG_MIN:
                        out.append(("hatch", x, q0, x, q1))
            for i, ly in enumerate(layers):
                lo, hi = sorted((ss[i], ss[i + 1]))
                if hi - lo >= LAYER_HATCH_MIN * cfg[10]:
                    out.extend(_hatch_rect(lo, hi, z0, z1, _hatch_pattern(ly["material"]), step, others))
    return tuple(out)


def slab_detail_lines(g, v, s):
    """단면에서 층 구성(layers)이 있는 슬래브의 상세: 절단 사각형 안의 두께 층 경계선과 층별 절단 해치. 벽 절단 사각형 안쪽은 지운다(벽이 우선)."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "sec" or cfg[10] > DETAIL_SCALE:
        return ()
    info = g.q(slab_info, s)
    layers = Q.el(g, s)["params"].get("layers") if info is not None else None
    if not layers:
        return ()
    others = _cut_boxes([q for wl in g.q(slab_nbr_walls, v, s) for q in g.q(vcut_rects, v, wl)])
    step = LAYER_HATCH_STEP * cfg[10]
    bounds = K.layer_bounds(layers)
    out = []
    for r in g.q(slab_cut_rects, v, s):
        s0, s1 = min(p[0] for p in r), max(p[0] for p in r)
        zt = max(p[1] for p in r)
        zs = [zt - c for c in bounds]   # 윗면에서 아래로
        for z in zs[1:-1]:
            cuts = [(max(rs0, s0), min(rs1, s1)) for rs0, rs1, rz0, rz1 in others if rz0 + EPS < z < rz1 - EPS]
            for q0, q1 in _subtract([[s0, s1]], sorted(cuts)):
                if q1 - q0 > SEG_MIN:
                    out.append(("hatch", q0, z, q1, z))
        for i, ly in enumerate(layers):
            if zs[i] - zs[i + 1] >= LAYER_HATCH_MIN * cfg[10]:
                out.extend(_hatch_rect(s0, s1, zs[i + 1], zs[i], _hatch_pattern(ly["material"]), step, others))
    return tuple(out)


def space_ceiling_lines(g, v, sp):
    """단면에서 천장 높이(ceiling_height)가 있는 공간의 천장선: 절단면이 공간 경계를 지나는 구간마다 레벨 + 천장 높이에 가는 보이는 선. 큰 축척의 단면만."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "sec" or cfg[10] > DETAIL_SCALE:
        return ()
    info = g.q(space_info, sp)
    ch = Q.el(g, sp)["params"].get("ceiling_height") if info is not None else None
    if ch is None:
        return ()
    z = g.q(Q.level_info, g.q(Q.space_level, sp))[1] + ch
    near = cfg[7]
    pts = [_sw(cfg, p) for p in info[0]]
    xs = []
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if (a[1] <= near) != (b[1] <= near):
            xs.append(a[0] + (near - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
    xs.sort()
    return tuple(("proj", xs[i], z, xs[i + 1], z) for i in range(0, len(xs) - 1, 2) if xs[i + 1] - xs[i] > SEG_MIN)


def _view_occluders(g, v, w):
    """벽 w 의 면 위 선을 가릴 수 있는 것들: 벽 조각(+이음매) + 기둥·슬래브·계단 조각(vwall_lines 와 같다)."""
    occ = _wall_occluders(g, v, w)
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    return occ


def vwin_detail(g, v, w):
    """입면에서 벽에 호스팅된 창의 창대(창 아래 밖으로 나온 선 3개): 앞 면(관찰자 쪽 벽 면)에 그리고 앞의 것에 가려지는 부분은 뺀다. 큰 축척(DETAIL_SCALE 이하)의 입면만.
    창 프레임(틀 폭·유리)은 패밀리 형상이라 기본 입면이 이미 그린다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "elev" or cfg[10] > DETAIL_SCALE:
        return ()
    sol = g.q(wall_solid, w)
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    h = sol[4]
    wins = []
    for d in g.q(Q.wall_hosted, w):
        row = Q.el(g, d)
        if row["kind"] == "finst" and str(row["family_id"]).startswith("window"):
            box = g.q(hosted_opening, d, elev)
            if box is not None:
                wins.append(box)
    if not wins:
        return ()
    vv = -h if _sw(cfg, _world(sol, 0.0, -h))[1] <= _sw(cfg, _world(sol, 0.0, h))[1] else h   # 관찰자에 가까운 벽 면

    def pt(u, z):
        s_, w_ = _sw(cfg, _world(sol, u, vv))
        return (s_, w_, z)

    occ = _view_occluders(g, v, w)
    out = []
    for u0, u1, z0, _z1 in sorted(wins):
        zl = z0 - SILL_DROP
        edges = [(pt(u0 - SILL_EXT, zl), pt(u1 + SILL_EXT, zl)), (pt(u0 - SILL_EXT, zl), pt(u0 - SILL_EXT, z0)), (pt(u1 + SILL_EXT, zl), pt(u1 + SILL_EXT, z0))]
        for e0, e1 in edges:
            for t0, t1 in visible_parts(e0, e1, occ):
                out.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]), e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    return tuple(out)


def view_detail_lines(g, v):
    """뷰의 큰 축척 상세 선(병합한 선분 묶음). 평면: 마감선·문짝 두께·문턱선. 단면: 벽·슬래브 층 경계선·층별 해치와 천장선. 입면: 창대. 배치도는 비어 있다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    segs = []
    if cfg[0] == "plan":
        if cfg[4] > DETAIL_SCALE or is_rcp(cfg):
            return ()
        for w in g.q(scope_walls, v):
            segs.extend(g.q(plan_detail_lines, v, w))
    elif cfg[0] == "sec":
        if cfg[10] > DETAIL_SCALE:
            return ()
        for w in g.q(scope_walls, v):
            segs.extend(g.q(vwall_detail, v, w))
        for sl in g.q(scope_slabs, v):
            segs.extend(g.q(slab_detail_lines, v, sl))
        for sp in g.q(all_spaces):
            segs.extend(g.q(space_ceiling_lines, v, sp))
    else:
        if cfg[10] > DETAIL_SCALE:
            return ()
        for w in g.q(scope_walls, v):
            segs.extend(g.q(vwin_detail, v, w))
    return _crop_if(g, v, merge_lines(segs))


# ================================================================ 단면·입면 (뷰 좌표 (s, w, z))
def _sw(cfg, p):
    px, py = p[0] - cfg[1], p[1] - cfg[2]
    return (px * cfg[5] + py * cfg[6], px * cfg[3] + py * cfg[4])


def _clip_w(poly, lo, hi):
    """볼록 다각형을 lo <= w <= hi 로 자른다 (Sutherland-Hodgman). 새 꼭짓점의 w 는 경계값 그대로 둔다."""
    for bound, keep_ge in ((lo, True), (hi, False)):
        if abs(bound) >= BIG:
            continue
        out = []
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            ina = (a[1] >= bound) if keep_ge else (a[1] <= bound)
            inb = (b[1] >= bound) if keep_ge else (b[1] <= bound)
            if ina:
                out.append(a)
            if ina != inb:
                tt = (bound - a[1]) / (b[1] - a[1])
                out.append((a[0] + tt * (b[0] - a[0]), bound))
        poly = out
        if len(poly) < 3:
            return []
    return poly


def _area2(poly):
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))


def vpieces(g, v, w):
    """벽을 속이 찬 볼록 기둥 조각들로: ((z0, z1, 다각형(s,w)), ...). near/far 로 자른 뒤 남는 것만."""
    cfg = g.q(view_cfg, v)
    sol = g.q(wall_solid, w)
    out = []
    for z0, z1, ivs in _wall_bands(g, w, sol):
        for a, b in ivs:
            poly = [_sw(cfg, p) for p in _quad(sol, a, b)]
            poly = _clip_w(poly, cfg[7], cfg[8])
            if len(poly) >= 3 and _area2(poly) > 2 * EPS:
                out.append((z0, z1, tuple(poly)))
    return tuple(out)


def vbbox(g, v, w):
    ps = g.q(vpieces, v, w)
    if not ps:
        return None
    ss = [p[0] for _, _, poly in ps for p in poly]
    return (min(ss), min(z0 for z0, _, _ in ps), max(ss), max(z1 for _, z1, _ in ps))


def vnbr_index(g, v):
    """_nbr_index 에 벽마다 가림 조각의 가장 앞 깊이(w 최소, 조각이 없으면 None)를 붙인다(vnbrs_front 가 뒤쪽 이웃을 거른다)."""
    idx = _nbr_index(g, vbbox, v)
    wmin = tuple(min((b[2] for b in g.q(_wall_occ_boxes, v, o)), default=None) for o in idx[0])
    return idx + (wmin,)


def vnbrs(g, v, w):
    return _nbrs(g, vbbox, vnbr_index, v, w)


def vcut_rects(g, v, w):
    """단면: 절단면(w=near)과 만나는 조각의 (s, z) 사각형. 입면은 없음."""
    cfg = g.q(view_cfg, v)
    if not cfg[9]:
        return ()
    if g.q(wall_solid, w)[11] is not None:   # 상단 윤곽(21단계): 기울어진 윗변의 절단 다각형
        return g.q(_vcut_top, v, w)
    near = cfg[7]
    rects = []
    if g.q(_has_cuts, w):   # 벽 잘라내기(23단계): 잘라낸 상자를 뺀 상자마다 절단면 위 s 범위
        sol = g.q(wall_solid, w)
        for u0, u1, v0, v1, z0, z1 in g.q(wall_cut_solids, w):
            poly = _clip_w([_sw(cfg, p) for p in _quad_v(sol, u0, u1, v0, v1)], cfg[7], cfg[8])
            if len(poly) < 3 or _area2(poly) <= 2 * EPS:
                continue
            ss = [p[0] for p in poly if abs(p[1] - near) <= EPS]
            if len(ss) >= 2 and max(ss) - min(ss) > EPS:
                rects.append(((min(ss), z0), (max(ss), z0), (max(ss), z1), (min(ss), z1)))
        return tuple(rects)
    for z0, z1, poly in g.q(vpieces, v, w):
        ss = [p[0] for p in poly if abs(p[1] - near) <= EPS]
        if len(ss) < 2:
            continue
        s0, s1 = min(ss), max(ss)
        if s1 - s0 > EPS:
            rects.append(((s0, z0), (s1, z0), (s1, z1), (s0, z1)))
    return tuple(rects)


def _clip_edge(e0, e1, near, far):
    """3D 선분 (s,w,z) 을 near<=w<=far 로 자른다. 밖이면 None."""
    dw = e1[1] - e0[1]
    t0, t1 = 0.0, 1.0
    if abs(dw) < 1e-12:
        if e0[1] < near - EPS or e0[1] > far + EPS:
            return None
        return (e0, e1)
    ta, tb = (near - e0[1]) / dw, (far - e0[1]) / dw
    if ta > tb:
        ta, tb = tb, ta
    t0, t1 = max(t0, ta), min(t1, tb)
    if t1 - t0 <= 1e-9:
        return None
    lerp = lambda t: (e0[0] + t * (e1[0] - e0[0]), e0[1] + t * dw, e0[2] + t * (e1[2] - e0[2]))   # noqa: E731
    return (lerp(t0), lerp(t1)) if (t0 > 0.0 or t1 < 1.0) else (e0, e1)


def vedges(g, v, w):
    """벽의 날카로운 모서리(수직·수평)를 뷰 좌표 3D 선분 (s,w,z) 쌍으로. 문 개구부의 앞뒤 윤곽과 두께 방향 모서리를 포함한다."""
    cfg = g.q(view_cfg, v)
    sol = g.q(wall_solid, w)
    near, far = cfg[7], cfg[8]
    h, start, end, zb, zt, ops = sol[4], sol[5], sol[6], sol[7], sol[8], sol[9]

    def pt(u, vv, z):
        s_, w_ = _sw(cfg, _world(sol, u, vv))
        return (s_, w_, z)

    raw = []
    corners = [(start, -h), (end, -h), (end, h), (start, h)]
    if sol[11] is not None:                                        # 상단 윤곽(21단계): 기울어진 윗변
        raw.extend(_top_vedges(sol, pt))
    else:
        for u, vv in corners:                                      # 수직 모서리
            raw.append((pt(u, vv, zb), pt(u, vv, zt)))
        for i in range(4):                                         # 밑·윗면 수평 모서리
            for z in (zb, zt):
                raw.append((pt(*corners[i], z), pt(*corners[(i + 1) % 4], z)))
    shapes = g.q(wall_shape_openings, w)                           # 모양 개구부(18단계): 사각 상자 대신 실제 윤곽을 그린다
    shaped = {s_[0] for s_ in shapes if s_[0] is not None}
    raw.extend(_wall_shape_edges(sol, shapes, pt))
    for u0, u1, z0, z1 in ops:
        if (u0, u1, z0, z1) in shaped:
            continue
        zs = (z1,) + ((z0,) if z0 > zb + EPS else ())
        for vv in (-h, h):
            for u in (u0, u1):
                raw.append((pt(u, vv, z0), pt(u, vv, z1)))        # 문설주
            for z in zs:
                raw.append((pt(u0, vv, z), pt(u1, vv, z)))        # 인방·문턱
        for u in (u0, u1):
            for z in zs:
                raw.append((pt(u, -h, z), pt(u, h, z)))           # 개구부 안쪽 면(두께 방향)
    out = []
    for e0, e1 in raw:
        if abs(e1[0] - e0[0]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue                                               # 화면에서 한 점으로 보이는 선
        c = _clip_edge(e0, e1, near, far)
        if c is not None:
            out.append(c)
    return tuple(out)


def _hidden_h(poly, e0, e1):
    """수평 선분(z 고정) e0->e1 (s,w,z) 중 다각형 조각(z 범위는 호출 쪽이 확인)에 가려지는 t 구간들."""
    smin = min(p[0] for p in poly) + 1e-6
    smax = max(p[0] for p in poly) - 1e-6
    ds, dw = e1[0] - e0[0], e1[1] - e0[1]
    res = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if b[0] - a[0] <= 1e-9:
            continue                                               # 반시계 다각형에서 오른쪽으로 가는 변이 아랫변(관찰자 쪽)이다
        m = (b[1] - a[1]) / (b[0] - a[0])
        lo, hi = max(a[0], smin), min(b[0], smax)
        if hi <= lo:
            continue
        # 가림 조건: w(t) - 아랫변(s(t)) > EPS_V  ->  A + B t > 0
        aa = e0[1] - a[1] - m * (e0[0] - a[0]) - EPS_V
        bb = dw - m * ds
        if abs(ds) < 1e-9:
            if not lo <= e0[0] <= hi:
                continue
            ta, tb = 0.0, 1.0
        else:
            ta, tb = (lo - e0[0]) / ds, (hi - e0[0]) / ds
            if ta > tb:
                ta, tb = tb, ta
            ta, tb = max(ta, 0.0), min(tb, 1.0)
        if abs(bb) < 1e-12:
            if aa <= 0.0:
                continue
        elif bb > 0.0:
            ta = max(ta, -aa / bb)
        else:
            tb = min(tb, -aa / bb)
        if tb - ta > 1e-9:
            res.append((ta, tb))
    return res


def _hit_point(poly, s, w):
    smin = min(p[0] for p in poly)
    smax = max(p[0] for p in poly)
    if s <= smin + 1e-6 or s >= smax - 1e-6:
        return False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if b[0] - a[0] > 1e-9 and a[0] <= s <= b[0]:
            if w - (a[1] + (b[1] - a[1]) * (s - a[0]) / (b[0] - a[0])) > EPS_V:
                return True
    return False


def visible_parts(e0, e1, occluders):
    """3D 선분 e0->e1 의 보이는 매개변수 구간들. occluders: ((z0, z1, 다각형(s,w)), ...) — 속이 찬 기둥 조각."""
    hidden = []
    dz = e1[2] - e0[2]
    vertical = abs(e1[0] - e0[0]) < 1e-9 and abs(e1[1] - e0[1]) < 1e-9
    zlo, zhi = min(e0[2], e1[2]), max(e0[2], e1[2])
    for z0, z1, poly in occluders:
        if vertical:
            if z1 <= zlo or z0 >= zhi:
                continue
            if _hit_point(poly, e0[0], e0[1]):
                ta, tb = (max(z0, zlo) - e0[2]) / dz, (min(z1, zhi) - e0[2]) / dz
                hidden.append((min(ta, tb), max(ta, tb)))
        else:
            if not z0 + EPS < e0[2] < z1 - EPS:
                continue
            hidden.extend(_hidden_h(poly, e0, e1))
    if not hidden:
        return [(0.0, 1.0)]
    hidden.sort()
    out, cur = [], 0.0
    for a, b in hidden:
        if a > cur + 1e-9:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < 1.0 - 1e-9:
        out.append((cur, 1.0))
    return out


def _bridges(g, v, w):
    """벽 하나의 위아래로 맞닿은 두 띠가 같은 u 구간에서 모두 속이 찬 곳의 가는 가림 조각(이음매 z 위로 +-2*EPS).
    문 개구부 때문에 벽의 띠가 높이로 나뉘면 띠 경계(문 윗선 z)가 어느 조각 안쪽도 아니라서, 앞벽의 속이 찬 곳 뒤의 선이 그 높이에서만 보이는 선으로
    남았다(가림 판정은 z 가 조각 안쪽일 때만 가린다). 두 띠가 모두 막힌 곳은 같은 벽이 막은 것이므로 이 조각으로 이음매를 메운다."""
    cfg = g.q(view_cfg, v)
    sol = g.q(wall_solid, w)
    out = []
    bands = _wall_bands(g, w, sol)
    for (_a0, a1, aiv), (b0, _b1, biv) in zip(bands, bands[1:]):
        if abs(a1 - b0) > EPS:
            continue
        for p0, p1 in aiv:
            for q0, q1 in biv:
                lo, hi = max(p0, q0), min(p1, q1)
                if hi - lo <= EPS:
                    continue
                poly = _clip_w([_sw(cfg, p) for p in _quad(sol, lo, hi)], cfg[7], cfg[8])
                if len(poly) >= 3 and _area2(poly) > 2 * EPS:
                    out.append((a1 - 2 * EPS, a1 + 2 * EPS, tuple(poly)))
    return tuple(out)


def _wall_occluders(g, v, w):
    """벽 w 의 선을 가릴 수 있는 벽 조각들: w 와 이웃 벽의 속이 찬 조각 + 띠 이음매를 메우는 조각."""
    occ = list(g.q(vpieces, v, w))
    occ.extend(g.q(_bridges, v, w))   # 질의로 캐시한다(과업 ⑤: 이웃마다 다시 계산하던 비용). 값은 같다
    for o in g.q(vnbrs, v, w):
        occ.extend(g.q(vpieces, v, o))
        occ.extend(g.q(_bridges, v, o))
    return occ


CULL_PROBE = (2, 8, 32)   # 앞에서부터 이만큼 모았을 때마다 다 가려졌는지 본다(값과 무관, 속도만)
CULL_TOL = 1.0e-3   # 가림 후보 거르기 여유(mm). 판정식의 부동소수 오차(1e-9 수준)보다 훨씬 커서 거른 조각은 실제로 아무 구간도 가리지 않는다


def _W_KEY(b):
    """가림 조각 경계 값의 w 최소(정렬 열쇠)."""
    return b[2]


def _occ_boxed(occ):
    """가림 조각마다 경계 값을 붙인다: (s 최소, s 최대, w 최소, z0, z1, 조각)."""
    out = []
    for pc in occ:
        poly = pc[2]
        out.append((min(p[0] for p in poly), max(p[0] for p in poly), min(p[1] for p in poly), pc[0], pc[1], pc))
    return out


def _wall_occ_boxes(g, v, w):
    """벽 하나의 가림 조각(속이 찬 조각 + 띠 이음매 조각)과 경계 값. 이웃 벽마다 다시 재지 않도록 질의로 캐시한다."""
    return tuple(sorted(_occ_boxed(g.q(vpieces, v, w) + g.q(_bridges, v, w)), key=_W_KEY))   # 앞(w 최소)부터: 합칠 때 정렬이 빠르다


def vnbrs_front(g, v, w):
    """(앞쪽 이웃, 이웃 중 가림 조각이 있는 벽이 있는가). 앞쪽 이웃 = vnbrs 중 가림 조각의 가장 앞 깊이가 w 의 어느 선분보다
    충분히 앞(w 최소 < 선분 w 최대 - EPS_V + CULL_TOL)인 벽. 그보다 뒤인 벽의 조각은 _visible_culled 가 모두 거르므로 빼도 값이 같다.
    이웃마다 조각을 당기지 않도록 색인에 적어 둔 깊이를 쓴다(과업 ⑤). 값이 같으면 vwall_lines 로 전파가 멈춘다."""
    nb = g.q(vnbrs, v, w)
    if not nb:
        return ((), False)
    idx = g.q(vnbr_index, v)
    pos, wmin = idx[3], idx[5]
    edges = g.q(vedges, v, w) + g.q(vfam_hosted_edges, v, w)
    any_occ = any(wmin[pos[o]] is not None for o in nb)
    if not edges:
        return ((), any_occ)
    w_cut = max(max(e0[1], e1[1]) for e0, e1 in edges) - EPS_V + CULL_TOL
    return (tuple(o for o in nb if wmin[pos[o]] is not None and wmin[pos[o]] < w_cut), any_occ)


def _other_occ_boxes(g, v):
    """벽이 아닌 가림 조각(기둥 등 패밀리, 슬래브, 계단, 경사 지붕 판)과 경계 값, 앞(w 최소)부터. 벽마다 다시 재지 않도록 뷰에 하나만 둔다(과업 ⑤).
    조각 목록은 vwall_lines 가 예전에 벽마다 모으던 것과 같다."""
    occ = []
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    for rf in g.q(scope_roofs, v):
        occ.extend(g.q(_roof_solid_vpieces, v, rf))
    return tuple(sorted(_occ_boxed(occ), key=_W_KEY))


def _visible_culled(e0, e1, boxed):
    """boxed: _occ_boxed 값을 w 최소 오름차순으로 정렬한 목록.
    visible_parts(e0, e1, 전체 가림 조각)과 같은 값을 더 빨리 낸다(수평·수직 선분 전용, 과업 ⑤ 깊이 컬링).
    1) 걸러내기: s 범위·z 범위가 닿지 않거나 선분보다 충분히 뒤(w 최소 >= 선분 w 최대 - EPS_V)인 조각은 어떤 구간도 가리지 않으므로 뺀다.
    2) 앞에서부터: 남은 조각을 앞(w 최소가 작은) 순서로 몇 개만 먼저 보아 선분 전체가 가려지면 바로 빈 목록을 낸다.
       가려진 구간들의 합집합이 [0, 1] 을 빈틈없이 덮으면 조각을 더 보태도 덮는다(visible_parts 의 합집합 계산은 단조다).
    3) 다 덮이지 않으면 남은 조각 전체로 visible_parts 를 그대로 부른다. 가려진 구간 목록은 정렬하므로 조각 순서와 무관하다."""
    s_lo, s_hi = (e0[0], e1[0]) if e0[0] <= e1[0] else (e1[0], e0[0])
    z_lo, z_hi = (e0[2], e1[2]) if e0[2] <= e1[2] else (e1[2], e0[2])
    w_hi = e0[1] if e0[1] >= e1[1] else e1[1]
    w_cut = w_hi - EPS_V + CULL_TOL
    vertical = abs(e1[0] - e0[0]) < 1e-9 and abs(e1[1] - e0[1]) < 1e-9   # visible_parts 와 같은 판정
    ez, es = e0[2], e0[0]
    cand = []
    for b in boxed:   # boxed 는 앞(w 최소)부터 정렬돼 있다
        if b[2] >= w_cut:
            break   # 뒤는 모두 이 선분보다 뒤다
        if vertical:   # visible_parts·_hit_point 가 건너뛰는 조건을 그대로 쓴다(같은 부동소수 식)
            if b[4] <= z_lo or b[3] >= z_hi or es <= b[0] + 1e-6 or es >= b[1] - 1e-6:
                continue
        elif not (b[3] + EPS < ez < b[4] - EPS) or b[0] >= s_hi + CULL_TOL or b[1] <= s_lo - CULL_TOL:
            continue
        cand.append(b[5])
        if len(cand) in CULL_PROBE and not visible_parts(e0, e1, cand):
            return []   # 앞의 조각 몇 개만으로 다 가려졌다: 나머지를 보태도 다 가려진다
    if not cand:
        return [(0.0, 1.0)]
    return visible_parts(e0, e1, cand)


def vwall_lines(g, v, w):
    cfg = g.q(view_cfg, v)
    front, nb_occ = g.q(vnbrs_front, v, w)
    # 가림 조각: 벽(자기 + 앞쪽 이웃)은 경계 값과 함께 캐시한 _wall_occ_boxes(= _wall_occluders 와 같은 조각), 그 밖의 요소는 아래 목록(과업 ⑤)
    wall_boxes = [b for x in (w,) + front for b in g.q(_wall_occ_boxes, v, x)]
    sls = g.q(scope_slabs, v)
    other = g.q(_other_occ_boxes, v)                    # 기둥·슬래브·계단·경사 지붕 판이 앞에 있으면 벽 선을 가린다
    solid = _solid_wall_occ(g, v, w)                    # 돌출·판 솔리드(부벽 등)가 앞에 있으면 벽 선을 가린다(18단계)
    occ = [b[5] for b in other] + list(solid)
    lines = []
    if wall_boxes or occ or nb_occ:   # 원래 조건(_wall_occluders 전체 + 그 밖의 조각이 하나라도 있으면)과 같다
        edges = g.q(vedges, v, w) + g.q(vfam_hosted_edges, v, w)
        boxed, full = [], None
        if edges:   # 이 벽의 어느 선분보다도 충분히 뒤에 있거나 높이가 닿지 않는 조각은 어떤 선도 가리지 않는다(_visible_culled 와 같은 거르기)
            w_cut = max(max(e0[1], e1[1]) for e0, e1 in edges) - EPS_V + CULL_TOL
            z_lo = min(min(e0[2], e1[2]) for e0, e1 in edges) - CULL_TOL
            z_hi = max(max(e0[2], e1[2]) for e0, e1 in edges) + CULL_TOL
            boxed = [b for b in wall_boxes if b[2] < w_cut]
            boxed += [b for b in other if b[2] < w_cut and b[3] < z_hi and b[4] > z_lo]
            boxed += [b for b in _occ_boxed(solid) if b[2] < w_cut]
            boxed.sort(key=_W_KEY)   # 이미 정렬된 묶음을 이어 붙인 목록이라 정렬이 빠르다
        for e0, e1 in edges:
            slanted = abs(e1[2] - e0[2]) > 1e-9 and (abs(e1[0] - e0[0]) > 1e-9 or abs(e1[1] - e0[1]) > 1e-9)   # 아치 윤곽의 현(18단계)
            if slanted and full is None:
                full = [b[5] for b in wall_boxes] + occ     # 비스듬한 선은 전체 조각으로 판정한다(가려진 구간을 정렬하므로 순서와 무관)
            for t0, t1 in (_visible_slanted(e0, e1, full) if slanted else _visible_culled(e0, e1, boxed)):
                x0, z0 = e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2])
                x1, z1 = e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])
                lines.append(("proj", x0, z0, x1, z1))
    if cfg[9]:
        mine = g.q(vcut_rects, v, w)
        if mine:
            nb = g.q(vnbrs, v, w)
            others = [(o, q) for o in nb for q in g.q(vcut_rects, v, o)]
            others += [(sl, q) for sl in sls for q in g.q(slab_cut_rects, v, sl)]   # 벽과 슬래브가 맞닿은 이음매 선을 지운다
            others += [(st, q) for st in g.q(scope_stairs, v) for q in g.q(stair_cut_rects, v, st)]   # 계단도 같다
            lines += [("cut", a[0], a[1], b[0], b[1]) for a, b in outline_edges(list(mine), others, w)]
    return tuple(lines)


# ================================================================ 대지·배치도 (kinds "site", 뷰 종류 "site")
# 배치도 뷰는 모델 전체를 위에서 본 한 장이다: 대지 경계(굵은 1점쇄선), 건물 외곽(벽 윤곽·슬래브 경계의 합집합 바깥 선), 접한 도로(폭만큼 띠),
# 방위표(정북 각), 대지 경계와 건물 사이 이격 거리 치수(연관 치수와 같은 주석 항목), 대지면적·도로·표고·축척 문자.
SITE_DASH, SITE_GAP, SITE_DOT = 6.0, 1.5, 0.8   # 대지 경계 1점쇄선의 마디·간격·점(용지 mm)
NORTH_R = 7.0                                    # 방위표 원 반지름(용지 mm)
SITE_LABEL_H, SITE_SUB_H = 3.0, 2.5              # 문자 높이(용지 mm)
SITE_PAD = 0.5                                   # 바깥 쪽 판정용 탐침 거리(mm)


def site_info(g, s):
    """(경계 다각형(반시계 튜플), 이름, 정북 각, 도로 ((이름, 변 번호, 폭), ...), 표고) 또는 None. 저작 대지만 그린다."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "site" or row["origin"] != "authored":
        return None
    p = row["params"]
    poly = tuple(tuple(q) for q in p["boundary"])
    return (poly, p["name"], p["north_angle"], tuple((r["name"], r["edge"], r["width"]) for r in p["roads"]), p["elevation"])


def all_sites(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "site", b) if g.q(site_info, e) is not None)
    return tuple(sorted(out))


def building_polys(g):
    """건물이 차지하는 평면 다각형들(모든 레벨의 그리는 벽 윤곽 + 저작 슬래브 경계), 모두 반시계. 레벨은 구분하지 않는다(배치도는 건물 전체의 외곽)."""
    out = []
    for w in g.q(all_walls):
        x, y, c, s, _ = g.q(Q.wall_frame, w)
        out.append(tuple((x + u * c - v * s, y + u * s + v * c) for u, v in g.q(Q.wall_footprint, w)))
    for sl in g.q(all_slabs):
        out.append(g.q(slab_info, sl)[2])
    return tuple(out)


def _hit_ts(a, b, c, d):
    """선분 a->b 위에서 선분 c->d 와 만나는(겹치면 두 끝이 닿는) 매개변수 t 들."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    qx, qy = c[0] - a[0], c[1] - a[1]
    l2 = rx * rx + ry * ry
    den = rx * sy - ry * sx
    if abs(den) <= 1e-9 * sqrt(l2) * sqrt(sx * sx + sy * sy):    # 평행
        if abs(qx * ry - qy * rx) > EPS * sqrt(l2):
            return ()
        return ((qx * rx + qy * ry) / l2, ((d[0] - a[0]) * rx + (d[1] - a[1]) * ry) / l2)
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    return (t,) if -1e-9 <= u <= 1.0 + 1e-9 else ()


def _in_poly(p, poly) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            inside = not inside
    return inside


def _poly_bbox(poly):
    return (min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly))


ESCAPE_DIRS = ((1.0, 0.0137), (-1.0, 0.0211), (0.0173, 1.0), (0.0239, -1.0), (1.0, 1.0123), (-1.0, 1.0171), (1.0, -1.0097), (-1.0, -1.0149))   # 살짝 비튼 8방향(꼭짓점을 스치지 않게)


def _escapes(p, edges) -> bool:
    """점 p 에서 어느 한 방향으로 건물 변에 한 번도 부딪히지 않고 무한히 나갈 수 있는가(건물 밖에서 보이는 자리인가). 닫힌 방 안쪽이면 모든 방향이 막힌다."""
    for dx, dy in ESCAPE_DIRS:
        blocked = False
        for a, b in edges:
            if (dx > 0 and max(a[0], b[0]) < p[0]) or (dx < 0 and min(a[0], b[0]) > p[0]) or (dy > 0 and max(a[1], b[1]) < p[1]) or (dy < 0 and min(a[1], b[1]) > p[1]):
                continue
            ex, ey = b[0] - a[0], b[1] - a[1]
            den = dx * ey - dy * ex
            if abs(den) < 1e-12:
                continue
            qx, qy = a[0] - p[0], a[1] - p[1]
            t = (qx * ey - qy * ex) / den
            u = (qx * dy - qy * dx) / den
            if t > 1e-9 and 0.0 <= u <= 1.0:
                blocked = True
                break
        if not blocked:
            return True
    return False


def building_outline(g):
    """건물 다각형들의 합집합 바깥 선분: ((x0, y0, x1, y1), ...). 다른 다각형 안쪽에 들어가거나 이웃과 맞닿은 변은 뺀다.
    각 변을 다른 다각형 변과의 교점에서 쪼개고, 조각 중점에서 바깥으로 조금 나간 점이 다른 다각형 안이면 안쪽 이음매다.
    닫힌 방 안쪽을 향한 변(그 점에서 어느 방향으로도 건물 밖으로 나갈 수 없는 자리)은 외곽이 아니라서 뺀다: 외벽 안쪽 면·칸막이 윤곽이 배치도에 남지 않는다."""
    return outline_of(g.q(building_polys))


def outline_of(polys):
    """반시계 다각형들의 합집합 바깥 선분(building_outline 의 계산, 면적 평면도 쓴다). 선분은 원래 변의 방향(반시계)을 지킨다."""
    boxes = [_poly_bbox(p) for p in polys]
    edges = [(P[k], P[(k + 1) % len(P)]) for P in polys for k in range(len(P))]
    out = []
    for i, P in enumerate(polys):
        others = [polys[j] for j in range(len(polys)) if j != i and _bb_hit(boxes[i], boxes[j])]
        n = len(P)
        for k in range(n):
            a, b = P[k], P[(k + 1) % n]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = sqrt(dx * dx + dy * dy)
            if length < SEG_MIN:
                continue
            ts = [0.0, 1.0]
            for Qp in others:
                m = len(Qp)
                for q in range(m):
                    ts.extend(t for t in _hit_ts(a, b, Qp[q], Qp[(q + 1) % m]) if 0.0 < t < 1.0)
            ts = sorted(set(round(t, 9) for t in ts))
            nx, ny = dy / length, -dx / length      # 반시계 다각형의 바깥(진행 방향의 오른쪽)
            for t0, t1 in zip(ts, ts[1:]):
                if (t1 - t0) * length <= SEG_MIN:
                    continue
                tm = (t0 + t1) / 2.0
                probe = (a[0] + tm * dx + nx * SITE_PAD, a[1] + tm * dy + ny * SITE_PAD)
                if any(_in_poly(probe, Qp) for Qp in others):
                    continue
                if not _escapes(probe, edges):
                    continue
                out.append((a[0] + t0 * dx, a[1] + t0 * dy, a[0] + t1 * dx, a[1] + t1 * dy))
    return tuple(out)


def _dash_dot(x0, y0, x1, y1, scale):
    """선분을 1점쇄선 조각(긴 마디, 간격, 점, 간격)으로 쪼갠다. 마디 길이는 용지 위 일정하다. 모두 절단선 등급이다."""
    dx, dy = x1 - x0, y1 - y0
    length = sqrt(dx * dx + dy * dy)
    if length < SEG_MIN:
        return []
    ux, uy = dx / length, dy / length
    pat = ((SITE_DASH * scale, True), (SITE_GAP * scale, False), (SITE_DOT * scale, True), (SITE_GAP * scale, False))
    out, t, i = [], 0.0, 0
    while t < length - SEG_MIN:
        seg, on = pat[i % 4]
        t1 = min(t + seg, length)
        if on:
            out.append(("cut", x0 + ux * t, y0 + uy * t, x0 + ux * t1, y0 + uy * t1))
        t, i = t1, i + 1
    return out


def _edge_frame(poly, i):
    """경계 변 i: (시작, 끝, 길이, 바깥 단위 법선)."""
    a, b = poly[i], poly[(i + 1) % len(poly)]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = sqrt(dx * dx + dy * dy)
    return a, b, length, (dy / length, -dx / length)


def _road_box(poly, edge, width):
    a, b, _length, (nx, ny) = _edge_frame(poly, edge)
    xs = (a[0], b[0], a[0] + nx * width, b[0] + nx * width)
    ys = (a[1], b[1], a[1] + ny * width, b[1] + ny * width)
    return (min(xs), min(ys), max(xs), max(ys))


def _site_extent(g):
    """배치도에 놓이는 대지·도로의 경계 상자들과 방위 각. 대지가 없으면 ((), None)."""
    boxes, north = [], None
    for s in g.q(all_sites):
        poly, _name, north_, roads, _el = g.q(site_info, s)
        north = north_ if north is None else north
        boxes.append(_poly_bbox(poly))
        boxes.extend(_road_box(poly, edge, width) for _rn, edge, width in roads)
    return tuple(boxes), north


def _north_symbol(boxes, scale, north):
    """방위표: (선분들, 문자들, 경계 상자). 대지·도로 묶음의 오른쪽 위 바깥에 놓는다. 정북은 도면 위에서 시계방향으로 north 도 돈 방향이다."""
    r = NORTH_R * scale
    cx, cy = max(b[2] for b in boxes) + r + 6.0 * scale, max(b[3] for b in boxes) - r
    sn, cs = _sincos_deg(north)                  # 정북 방향 = (sin, cos)
    pts = _circle_pts((cx, cy), r)
    segs = [("symbol", pts[k][0], pts[k][1], pts[(k + 1) % len(pts)][0], pts[(k + 1) % len(pts)][1]) for k in range(len(pts))]
    px, py = cs, -sn                             # 정북에 수직인 오른쪽
    tip = (cx + sn * r * 0.92, cy + cs * r * 0.92)
    left = (cx - sn * r * 0.62 - px * r * 0.38, cy - cs * r * 0.62 - py * r * 0.38)
    right = (cx - sn * r * 0.62 + px * r * 0.38, cy - cs * r * 0.62 + py * r * 0.38)
    mid = (cx - sn * r * 0.28, cy - cs * r * 0.28)
    for p, q in ((tip, left), (left, mid), (mid, right), (right, tip)):
        segs.append(("cut", p[0], p[1], q[0], q[1]))
    lab = [(round(cx + sn * (r + 2.4 * scale), 3), round(cy + cs * (r + 2.4 * scale) - 1.4 * scale, 3), "N", 4.0, "middle")]
    return segs, lab, (cx - r, cy - r, cx + r, cy + r)


def site_setbacks(g, s):
    """대지 경계 변마다 건물까지의 이격: ((변 번호, 거리, 경계 위 발, 건물 꼭짓점), ...). 변에 수직으로 내린 발이 변 안에 드는 건물 꼭짓점 중 가장 가까운 것.
    거리는 경계 안쪽이 양수(건물이 경계를 넘으면 음수). 건물이 없으면 비어 있다."""
    info = g.q(site_info, s)
    polys = g.q(building_polys)
    if info is None or not polys:
        return ()
    poly = info[0]
    verts = sorted({(round(x, 3), round(y, 3)) for P in polys for x, y in P})
    out = []
    for i in range(len(poly)):
        a, b, length, (nx, ny) = _edge_frame(poly, i)
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        best = None
        for vx, vy in verts:
            t = (vx - a[0]) * ux + (vy - a[1]) * uy
            if t < -EPS or t > length + EPS:
                continue
            d = -((vx - a[0]) * nx + (vy - a[1]) * ny)      # 안쪽이 양수
            if best is None or d < best[0] - 1e-9:
                best = (d, (a[0] + t * ux, a[1] + t * uy), (vx, vy))
        if best is not None:
            out.append((i, best[0], best[1], best[2]))
    return tuple(out)


def _dim_item(aid, p, q, scale, text):
    """p -> q 를 재는 치수 주석 항목(annot.annot_draw 의 "dim" 항목과 같은 모양: 보조선 둘, 치수선, 글, 글 위치, 치수선 방향)."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = sqrt(dx * dx + dy * dy)
    ux, uy = dx / length, dy / length
    h = 1.2 * scale
    r = lambda *vs: tuple(round(v, 3) + 0.0 for v in vs)   # noqa: E731
    e0 = r(p[0] + uy * h, p[1] - ux * h, p[0] - uy * h, p[1] + ux * h)
    e1 = r(q[0] + uy * h, q[1] - ux * h, q[0] - uy * h, q[1] + ux * h)
    return ("dim", aid, e0, e1, r(p[0], p[1], q[0], q[1]), text, r((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0), r(ux, uy))


def _fmt_mm(v) -> str:
    r = round(v)
    return str(int(r)) if abs(v - r) < 0.05 else f"{v:.1f}"


def site_dims(g, v):
    """배치도의 치수: 대지 경계에서 건물까지 이격 거리, 접한 도로의 폭. 연관 치수 주석과 같은 항목(id 는 'site:...')이다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "site":
        return ()
    scale = cfg[4]
    out = []
    for s in g.q(all_sites):
        poly, _name, _north, roads, _el = g.q(site_info, s)
        for i, d, foot, vert in g.q(site_setbacks, s):
            if abs(d) < 1.0:
                continue
            out.append(_dim_item(f"site:{s[-6:]}:setback:{i}", foot, vert, scale, _fmt_mm(d)))
        for _rn, edge, width in roads:
            a, b, _length, (nx, ny) = _edge_frame(poly, edge)
            m = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            out.append(_dim_item(f"site:{s[-6:]}:road:{edge}", m, (m[0] + nx * width, m[1] + ny * width), scale, _fmt_mm(width)))
    return tuple(out)


def site_lines(g, v):
    """배치도의 선분: 대지 경계(1점쇄선), 건물 외곽, 도로 띠(바깥 변·양 옆), 방위표."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "site":
        return ()
    scale = cfg[4]
    segs = []
    for s in g.q(all_sites):
        poly, _name, _north, roads, _el = g.q(site_info, s)
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            segs.extend(_dash_dot(a[0], a[1], b[0], b[1], scale))
        for _rn, edge, width in roads:
            a, b, _length, (nx, ny) = _edge_frame(poly, edge)
            a2, b2 = (a[0] + nx * width, a[1] + ny * width), (b[0] + nx * width, b[1] + ny * width)
            segs += [("proj", a[0], a[1], a2[0], a2[1]), ("proj", b[0], b[1], b2[0], b2[1]), ("proj", a2[0], a2[1], b2[0], b2[1])]
        segs.extend(site_extra_lines(g, s, scale))       # 조경·공개공지·주차(없으면 비어 있다)
    segs.extend(("cut", x0, y0, x1, y1) for x0, y0, x1, y1 in g.q(building_outline))
    boxes, north = g.q(_site_extent)
    if boxes:
        segs.extend(_north_symbol(boxes, scale, north)[0])
    return merge_lines(segs)


def site_labels(g, v):
    """배치도의 문자: 대지 이름·면적·표고, 도로 이름·폭·접도 길이, 방위 N, 축척."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "site":
        return ()
    scale = cfg[4]
    out = []
    for s in g.q(all_sites):
        poly, name, _north, roads, el = g.q(site_info, s)
        cx, _cy = polygon_centroid(poly)
        top = max(q[1] for q in poly)       # 건물과 겹치지 않게 대지 위쪽 안에 놓는다
        out.append((round(cx, 3), round(top - 7.0 * scale, 3), name, SITE_LABEL_H, "middle"))
        out.append((round(cx, 3), round(top - 10.0 * scale, 3), f"대지면적 {area_text(poly)}", SITE_SUB_H, "middle"))
        if el is not None:
            out.append((round(cx, 3), round(top - 13.0 * scale, 3), f"대지 표고 EL {el / 1000.0:+.3f}m", SITE_SUB_H, "middle"))
        for rn, edge, width in roads:
            a, b, length, (nx, ny) = _edge_frame(poly, edge)
            m = (a[0] + (b[0] - a[0]) * 0.22, a[1] + (b[1] - a[1]) * 0.22)      # 변 한가운데는 폭 치수가 차지한다
            lx, ly = m[0] + nx * width * 0.5, m[1] + ny * width * 0.5
            out.append((round(lx, 3), round(ly + 0.6 * scale, 3), f"{rn or '도로'} 폭 {_fmt_mm(width)}", SITE_SUB_H, "middle"))
            out.append((round(lx, 3), round(ly - 2.4 * scale, 3), f"접도 길이 {_fmt_mm(length)}", SITE_SUB_H, "middle"))
        out.extend(site_extra_labels(g, s, poly, scale))
    boxes, north = g.q(_site_extent)
    if boxes:
        _sym, lab, bb = _north_symbol(boxes, scale, north)
        out.extend(lab)
        out.append((round((bb[0] + bb[2]) / 2.0, 3), round(bb[1] - 4.0 * scale, 3), f"S=1:{scale}", SITE_SUB_H, "middle"))
    return tuple(out)


# ================================================================ 대지의 선택 속성 (설계: elements-design.md 14절)
# 지반 표고점(terrain)·조경/공개공지 영역(zones)·주차 계획(parking)·간판(signs)은 site 행의 선택 파라미터다. 없으면 아래 함수는 모두 빈 값이고
# 기존 도면은 바이트까지 그대로다. 배치도에는 영역 해치·면적·주차면·차로·동선을, 입면에는 간판 사각형을, 대지 단면 뷰(section 의 with_site)에는 지반선을 그린다.
ZONE_LABEL = {"landscape": "조경", "open_space": "공개공지"}
SIGN_LABEL = {"sign": "간판", "address": "건물번호판"}
ARROW_L, ARROW_COS, ARROW_SIN = 3.0, 0.9063077870366499, 0.42261826174069944   # 동선 화살표 머리 길이(용지 mm)와 25도의 cos·sin
SEC_STEP = 1000.0            # 대지 단면 지반선의 표본 간격(mm)
SEC_STEP_MAX = 400           # 지반선 표본 수 상한
SEC_TICK = 500.0             # 지반선 아래 사선 눈금 길이(mm)
SEC_MARK_DOWN, SEC_MARK_UP = 1500.0, 3000.0   # 대지 경계 표시 세로선이 지반 아래·위로 뻗는 길이(mm)


def site_extra(g, s):
    """대지의 선택 속성 중 있는 것만 담은 dict(없으면 빈 dict). 저작 대지만."""
    row = Q.el(g, s)
    if row is None or row["kind"] != "site" or row["origin"] != "authored":
        return {}
    p = row["params"]
    return {k: p[k] for k in K.SITE_OPTIONAL if k in p}


def terrain_z(pts, x, y):
    """지반 표고 보간: 표고점 [[x,y,z],...] 에서 거리 제곱 역가중 평균(점 위면 그 점의 z). 사칙연산만이라 결정적이다."""
    num = den = 0.0
    for px, py, pz in pts:
        d2 = (px - x) * (px - x) + (py - y) * (py - y)
        if d2 < 1e-6:
            return pz
        w = 1.0 / d2
        num += w * pz
        den += w
    return num / den


def _poly_hatch(poly, use, step_mm):
    """다각형 안의 해치 선분. 조경 = 45도, 공개공지 = 135도. 선은 세계 격자(간격의 정수배)에 맞춘다(영역이 달라도 무늬가 이어진다)."""
    k = -1.0 if use == "landscape" else 1.0           # f = k*x + y 가 일정한 선
    dc = step_mm * 1.4142135623730951
    fs = [k * q[0] + q[1] for q in poly]
    lo, hi = min(fs), max(fs)
    out = []
    n = len(poly)
    m = _floor(lo / dc) + 1
    while m * dc < hi:
        c = m * dc
        m += 1
        xs = []
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            fa, fb = fs[i], fs[(i + 1) % n]
            if (fa <= c < fb) or (fb <= c < fa):
                t = (c - fa) / (fb - fa)
                xs.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
        xs.sort()
        for j in range(0, len(xs) - 1, 2):
            if abs(xs[j + 1][0] - xs[j][0]) > SEG_MIN:
                out.append(("hatch", xs[j][0], xs[j][1], xs[j + 1][0], xs[j + 1][1]))
    return out


def zone_totals(zones):
    """(조경 면적 ㎡ 합, 공개공지 면적 ㎡ 합). 영역끼리 겹치는지는 보지 않는다(입력 책임)."""
    t = {"landscape": 0.0, "open_space": 0.0}
    for z in zones:
        t[z["use"]] += abs(_area2(z["boundary"])) / 2.0 / 1.0e6
    return t["landscape"], t["open_space"]


def parking_bays(pk):
    """주차면 배열 -> [(배열 번호, 장애인 여부, 사각형 꼭짓점 4개), ...]. 배열은 시작 모서리에서 angle 방향으로 폭 w 씩, 깊이 d 는 그 왼쪽으로 뻗는다."""
    out = []
    for gi, b in enumerate(pk.get("bays", ())):
        sn, cs = _sincos_deg(b["angle"])
        ox, oy = b["origin"]
        for i in range(b["count"]):
            p0 = (ox + cs * b["w"] * i, oy + sn * b["w"] * i)
            p1 = (p0[0] + cs * b["w"], p0[1] + sn * b["w"])
            p2 = (p1[0] - sn * b["d"], p1[1] + cs * b["d"])
            p3 = (p0[0] - sn * b["d"], p0[1] + cs * b["d"])
            out.append((gi, bool(b.get("accessible")), (p0, p1, p2, p3)))
    return out


def parking_counts(pk):
    """(주차 대수 합, 그 중 장애인 전용). 주차면 배열이 없으면 (0, 0)."""
    total = sum(b["count"] for b in pk.get("bays", ()))
    acc = sum(b["count"] for b in pk.get("bays", ()) if b.get("accessible"))
    return total, acc


def site_extra_lines(g, s, scale):
    """배치도에 더하는 선분: 조경·공개공지 영역 윤곽(proj)과 해치, 주차면(proj)·차로 양쪽 선(proj)·진출입 동선과 화살표(symbol)."""
    ex = g.q(site_extra, s)
    segs = []
    for z in ex.get("zones", ()):
        b = z["boundary"]
        segs.extend(("proj", b[i][0], b[i][1], b[(i + 1) % len(b)][0], b[(i + 1) % len(b)][1]) for i in range(len(b)))
        segs.extend(_poly_hatch(b, z["use"], HATCH_STEP * scale))
    for px, py, _pz in ex.get("terrain", ()):   # 지반 표고점: 작은 십자(문자는 site_extra_labels)
        h = 1.0 * scale
        segs += [("symbol", px - h, py, px + h, py), ("symbol", px, py - h, px, py + h)]
    pk = ex.get("parking")
    if pk:
        for _gi, _acc, c in parking_bays(pk):
            segs.extend(("proj", c[i][0], c[i][1], c[(i + 1) % 4][0], c[(i + 1) % 4][1]) for i in range(4))
        for a in pk.get("aisles", ()):
            path, h = a["path"], a["width"] / 2.0
            for i in range(len(path) - 1):
                (x0, y0), (x1, y1) = path[i], path[i + 1]
                dx, dy = x1 - x0, y1 - y0
                ln = sqrt(dx * dx + dy * dy)
                if ln < SEG_MIN:
                    continue
                nx, ny = -dy / ln * h, dx / ln * h
                segs.append(("proj", x0 + nx, y0 + ny, x1 + nx, y1 + ny))
                segs.append(("proj", x0 - nx, y0 - ny, x1 - nx, y1 - ny))
        for r in pk.get("routes", ()):
            path = r["path"]
            for i in range(len(path) - 1):
                segs.append(("symbol", path[i][0], path[i][1], path[i + 1][0], path[i + 1][1]))
            (x0, y0), (x1, y1) = path[-2], path[-1]
            dx, dy = x1 - x0, y1 - y0
            ln = sqrt(dx * dx + dy * dy)
            if ln >= SEG_MIN:
                ux, uy = dx / ln, dy / ln
                hl = ARROW_L * scale
                for sg in (1.0, -1.0):                     # 화살표 머리: 끝점에서 뒤로 ±25도
                    wx = ux * ARROW_COS - sg * uy * ARROW_SIN
                    wy = uy * ARROW_COS + sg * ux * ARROW_SIN
                    segs.append(("symbol", x1, y1, x1 - wx * hl, y1 - wy * hl))
            sx, sy = path[0]                                # 진입 표시: 시작점의 짧은 가로대
            dx, dy = path[1][0] - sx, path[1][1] - sy
            ln = sqrt(dx * dx + dy * dy)
            if ln >= SEG_MIN:
                bx, by = -dy / ln * ARROW_L * scale / 2.0, dx / ln * ARROW_L * scale / 2.0
                segs.append(("symbol", sx + bx, sy + by, sx - bx, sy - by))
    return segs


def site_extra_labels(g, s, poly, scale):
    """배치도에 더하는 문자: 영역 이름·면적, 영역 합계, 주차 대수(배열마다와 합계)."""
    ex = g.q(site_extra, s)
    out = []
    cx, _cy = polygon_centroid(poly)
    top = max(q[1] for q in poly)
    for px, py, pz in ex.get("terrain", ()):   # 지반 표고점의 높이(모델 기준 mm): 배치도에 대지의 고저차를 보인다
        out.append((round(px, 3), round(py + 1.4 * scale, 3), f"지반 {pz:+.0f}", SITE_SUB_H, "middle"))
    zones = ex.get("zones", ())
    for z in zones:
        zx, zy = polygon_centroid(z["boundary"])
        out.append((round(zx, 3), round(zy, 3), f"{z['name'] or ZONE_LABEL[z['use']]} {area_text(z['boundary'])}", SITE_SUB_H, "middle"))
    site_a = abs(_area2(poly)) / 2.0 / 1.0e6
    row = 16.0
    if zones:
        la, op = zone_totals(zones)
        for use, a in (("landscape", la), ("open_space", op)):
            if any(z["use"] == use for z in zones):
                out.append((round(cx, 3), round(top - row * scale, 3), f"{ZONE_LABEL[use]} 면적 합계 {a:.2f}㎡(대지면적의 {a / site_a * 100.0:.1f}%)", SITE_SUB_H, "middle"))
                row += 3.0
    pk = ex.get("parking")
    if pk and pk.get("bays"):
        total, acc = parking_counts(pk)
        for b in pk["bays"]:
            sn, cs = _sincos_deg(b["angle"])
            ox, oy = b["origin"]
            ex_, ey_ = b["w"] * b["count"] / 2.0, b["d"] / 2.0         # 배열의 가운데(깊이 쪽 절반)
            out.append((round(ox + cs * ex_ - sn * ey_, 3), round(oy + sn * ex_ + cs * ey_, 3),
                        ("장애인 " if b.get("accessible") else "주차 ") + f"{b['count']}대", SITE_SUB_H, "middle"))
        out.append((round(cx, 3), round(top - row * scale, 3), f"주차 합계 {total}대" + (f"(장애인 전용 {acc}대)" if acc else ""), SITE_SUB_H, "middle"))
    return out


def _elev_signs(g, v):
    """입면 뷰가 바라보는 면에 붙은 간판·번호판: ((간판 dict, 가로 시작 s0, 끝 s1, 아래 z0, 위 z1), ...). 없으면 빈 튜플."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "elev":
        return ()
    d = Q.el(g, v)["params"]["dir"]
    out = []
    for s in g.q(all_sites):
        for sg in g.q(site_extra, s).get("signs", ()):
            if sg["face"] != d:
                continue
            sx = _sw(cfg, sg["center"])[0]
            out.append((sg, sx - sg["w"] / 2.0, sx + sg["w"] / 2.0, sg["z"], sg["z"] + sg["h"]))
    return tuple(out)


def site_sign_lines(g, v):
    """입면의 간판·건물번호판 사각형 윤곽(symbol)."""
    segs = []
    for _sg, s0, s1, z0, z1 in g.q(_elev_signs, v):
        segs += [("symbol", s0, z0, s1, z0), ("symbol", s1, z0, s1, z1), ("symbol", s1, z1, s0, z1), ("symbol", s0, z1, s0, z0)]
    return tuple(segs)


def site_sign_labels(g, v):
    """입면의 간판·건물번호판 문자: 사각형 위에 종류·이름·크기(폭x높이), 안에 문구."""
    cfg = g.q(view_cfg, v)
    out = []
    for sg, s0, s1, z0, z1 in g.q(_elev_signs, v):
        sc = cfg[10]
        mid = (s0 + s1) / 2.0
        out.append((round(mid, 3), round(z1 + 1.0 * sc, 3), f"{SIGN_LABEL[sg['kind']]} {sg['name']} {_fmt_mm(sg['w'])}x{_fmt_mm(sg['h'])}", SITE_SUB_H, "middle"))
        if sg["text"]:
            out.append((round(mid, 3), round((z0 + z1) / 2.0 - 0.8 * sc, 3), sg["text"], 2.0, "middle"))
    return tuple(out)


def _site_sec(g, v):
    """대지 단면 뷰(section 의 with_site)가 지나는 대지 구간: (뷰 파라미터, ((대지 id, ((t0, t1), ...)), ...)). 구간은 절단선(0..1) 중 대지 경계 안쪽이다.
    with_site 가 아닌 뷰는 None."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "sec":
        return None
    p = Q.el(g, v)["params"]
    if not p.get("with_site"):
        return None
    a, b = tuple(p["p1"]), tuple(p["p2"])
    out = []
    for s in g.q(all_sites):
        poly = g.q(site_info, s)[0]
        ts = {0.0, 1.0}
        for i in range(len(poly)):
            ts.update(t for t in _hit_ts(a, b, poly[i], poly[(i + 1) % len(poly)]) if 0.0 < t < 1.0)
        ts = sorted(ts)
        inside = []
        for t0, t1 in zip(ts, ts[1:]):
            tm = (t0 + t1) / 2.0
            if K.point_in_polygon((a[0] + (b[0] - a[0]) * tm, a[1] + (b[1] - a[1]) * tm), [list(q) for q in poly]) >= 0:
                if inside and abs(inside[-1][1] - t0) < 1e-12:
                    inside[-1] = (inside[-1][0], t1)
                else:
                    inside.append((t0, t1))
        out.append((s, tuple(inside)))
    return p, tuple(out)


def _gl_z(g, s):
    """GL(지표면 기준선) 높이: 건물 외곽 경계 상자 가운데의 지반 표고. 건물이 없으면 표고점 z 의 평균. 표고점이 없으면 0."""
    polys = g.q(building_polys)
    pts = g.q(site_extra, s).get("terrain", ())
    if not pts:
        return 0.0
    if polys:
        x0 = min(q[0] for P in polys for q in P)
        x1 = max(q[0] for P in polys for q in P)
        y0 = min(q[1] for P in polys for q in P)
        y1 = max(q[1] for P in polys for q in P)
        return terrain_z(pts, (x0 + x1) / 2.0, (y0 + y1) / 2.0)
    return sum(q[2] for q in pts) / len(pts)


def site_section_lines(g, v):
    """대지 단면(section 의 with_site): 지반선(cut), 지반 아래 사선 눈금(hatch), GL 수평선(proj), 대지 경계 표시 세로선(symbol). 건물은 단면 뷰가 이미 그린다."""
    cfg = g.q(view_cfg, v)
    info = g.q(_site_sec, v)
    if info is None:
        return ()
    p, sites = info
    a, b = p["p1"], p["p2"]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = sqrt(dx * dx + dy * dy)
    segs = []
    for s, inside in sites:
        pts = g.q(site_extra, s).get("terrain", ())
        gl = g.q(_gl_z, s)
        for t0, t1 in inside:
            n = min(SEC_STEP_MAX, int((t1 - t0) * length / SEC_STEP) + 1)
            prev = None
            for i in range(n + 1):
                t = t0 + (t1 - t0) * i / n
                x, y = a[0] + dx * t, a[1] + dy * t
                z = terrain_z(pts, x, y) if pts else 0.0
                sx = _sw(cfg, (x, y))[0]
                if prev is not None:
                    segs.append(("cut", prev[0], prev[1], sx, z))
                    segs.append(("hatch", sx, z, sx - SEC_TICK, z - SEC_TICK))
                prev = (sx, z)
            s0, s1 = _sw(cfg, (a[0] + dx * t0, a[1] + dy * t0))[0], _sw(cfg, (a[0] + dx * t1, a[1] + dy * t1))[0]
            segs.append(("proj", s0, gl, s1, gl))
            for t in (t0, t1):
                if 1e-9 < t < 1.0 - 1e-9:
                    x, y = a[0] + dx * t, a[1] + dy * t
                    z = terrain_z(pts, x, y) if pts else 0.0
                    sx = _sw(cfg, (x, y))[0]
                    segs.append(("symbol", sx, z - SEC_MARK_DOWN, sx, z + SEC_MARK_UP))
    return tuple(segs)


def site_section_labels(g, v):
    """대지 단면의 문자: GL 높이(모델 기준·표고가 있으면 EL 도), 대지 경계 표시."""
    cfg = g.q(view_cfg, v)
    info = g.q(_site_sec, v)
    if info is None:
        return ()
    p, sites = info
    a, b = p["p1"], p["p2"]
    dx, dy = b[0] - a[0], b[1] - a[1]
    out = []
    for s, inside in sites:
        ex = g.q(site_extra, s)
        pts = ex.get("terrain", ())
        gl = g.q(_gl_z, s)
        el = g.q(site_info, s)[4]
        for t0, t1 in inside:
            s0 = _sw(cfg, (a[0] + dx * t0, a[1] + dy * t0))[0]
            txt = f"GL {gl:+.0f}" + (f"(EL {(el + gl) / 1000.0:+.3f}m)" if el is not None else "")
            out.append((round(s0 + 4000.0, 3), round(gl + 0.8 * cfg[10], 3), txt, SITE_SUB_H, "middle"))
            for t in (t0, t1):
                if 1e-9 < t < 1.0 - 1e-9:
                    x, y = a[0] + dx * t, a[1] + dy * t
                    z = terrain_z(pts, x, y) if pts else 0.0
                    out.append((round(_sw(cfg, (x, y))[0], 3), round(z + SEC_MARK_UP + 0.8 * cfg[10], 3), "대지 경계", SITE_SUB_H, "middle"))
    return tuple(out)


# ================================================================ 숨은 선 (옵션: sheet.export 의 profile.hidden_lines)
def _complement(vis):
    """보이는 구간 [(t0, t1), ...](정렬) -> 가려진 구간들(0..1 안)."""
    out, cur = [], 0.0
    for a, b in vis:
        if a > cur + 1e-9:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < 1.0 - 1e-9:
        out.append((cur, 1.0))
    return out


def vwall_hidden(g, v, w):
    """단면·입면에서 벽 하나의 가려진 모서리 선분 (등급 "hid", s, z, s, z)들. vwall_lines 와 같은 가림 판정(visible_parts)의 여집합이다.
    벽의 바깥 모서리와 문 개구부 윤곽이 앞에 있는 벽·기둥·슬래브·계단에 가려지는 부분이다."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] in ("plan", "site"):
        return ()
    occ = _wall_occluders(g, v, w)
    for fe in g.q(scope_fams, v):
        occ.extend(g.q(_fam_vpieces, v, fe))
    for sl in g.q(scope_slabs, v):
        occ.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        occ.extend(g.q(_stair_vpieces, v, st))
    for rf in g.q(scope_roofs, v):
        occ.extend(g.q(_roof_solid_vpieces, v, rf))
    out = []
    for e0, e1 in g.q(vedges, v, w) + g.q(vfam_hosted_edges, v, w):
        for t0, t1 in _complement(visible_parts(e0, e1, occ)):
            out.append(("hid", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]), e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    return tuple(out)


def view_hidden_lines(g, v):
    """뷰의 숨은 선(파선으로 그릴 선분, 등급 "hid"). 단면·입면만 있다(평면·배치도는 없다). 보이는 선과 같은 직선 위에 겹치는 부분은 뺀다(실선이 우선)."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] in ("plan", "site"):
        return ()
    hid = []
    for w in g.q(scope_walls, v):
        hid.extend(g.q(vwall_hidden, v, w))
    if not hid:
        return ()
    vis = [("cut",) + tuple(ln[1:]) for ln in g.q(view_lines, v)]
    merged = merge_lines(vis + [("proj",) + h[1:] for h in hid])
    return _crop_if(g, v, tuple(("hid",) + ln[1:] for ln in merged if ln[0] == "proj"))


# ================================================================ 부위 상세 뷰: 잘라내기(crop)·계단·승강기 치수 자동 표기(dims) (설계: elements-design.md 15절)
# 뷰의 선택 파라미터이고 없으면 키 자체가 없다: 이 옵션이 없는 뷰는 아래 함수들이 모두 빈 값이라 도면이 바이트까지 그대로다.
#   crop: 뷰 좌표 사각형(평면 x,y / 단면·입면 s,z)으로 선·채움·문자를 잘라 그린다(1:20~1:50 부위 상세).
#   dims: 평면·단면에 보이는 계단·승강기의 치수를 연관 치수 항목(annot 의 "dim" 과 같은 모양, id 는 "detail:...")과 문자로 단다.
#         계단 평면: 단 묶음의 폭·디딤판 수x깊이=길이, 층참의 깊이·폭, 챌판 수·높이·디딤 깊이 문자 / 계단 단면(걸음선이 단면과 나란한 단 묶음):
#         첫 챌판 높이·첫 디딤판 깊이·디딤 길이 사슬·오름 높이 사슬, 난간 높이, 층참 높이 / 승강기 평면: 카·샤프트 폭·깊이 / 승강기 단면: 카 높이·오버헤드(OH)·승강로 높이·피트
#   finish(입면): elev_finish 가 읽는다(외부 마감 해치·재료 문자).
DIM_OFF = 6.0      # 치수선이 대상에서 떨어지는 거리(용지 mm)
DIM_OFF2 = 12.0    # 두 번째 줄(바깥) 치수선


def view_opts(g, v):
    """뷰의 선택 파라미터 {"crop": (x1,y1,x2,y2), "dims": True, "finish": True}(있는 것만). 뷰가 없으면 빈 dict."""
    row = Q.el(g, v)
    if row is None or row["kind"] != "view":
        return {}
    p = row["params"]
    out = {}
    if "crop" in p:
        out["crop"] = tuple(float(c) for c in p["crop"])
    for k in ("dims", "finish"):
        if p.get(k) is True:
            out[k] = True
    return out


def _crop_seg(ln, c):
    """선분 (등급, x0, y0, x1, y1)을 사각형 c=(x1,y1,x2,y2)로 자른다(Liang-Barsky). 밖이면 None, 안이면 그대로."""
    cls, x0, y0, x1, y1 = ln[:5]
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x0 - c[0]), (dx, c[2] - x0), (-dy, y0 - c[1]), (dy, c[3] - y0)):
        if abs(pp) < 1e-12:
            if qq < -EPS:
                return None
            continue
        r = qq / pp
        if pp < 0:
            if r > t1:
                return None
            t0 = max(t0, r)
        else:
            if r < t0:
                return None
            t1 = min(t1, r)
    if t1 - t0 < 1e-12:
        return None
    if t0 <= 0.0 and t1 >= 1.0:
        return ln
    return (cls, round(x0 + t0 * dx, 6), round(y0 + t0 * dy, 6), round(x0 + t1 * dx, 6), round(y0 + t1 * dy, 6)) + tuple(ln[5:])


def _crop_lines(segs, c):
    out = []
    for ln in segs:
        r = _crop_seg(ln, c)
        if r is not None and (r[3] - r[1]) ** 2 + (r[4] - r[2]) ** 2 >= SEG_MIN * SEG_MIN:
            out.append(r)
    return tuple(out)


def _crop_if(g, v, segs):
    c = g.q(view_opts, v).get("crop")
    return segs if c is None else _crop_lines(segs, c)


def _crop_polys(polys, c):
    """채움 다각형들을 사각형 c 로 자른다(Sutherland-Hodgman). 안에 든 것은 그대로, 면적이 없어진 것은 뺀다."""
    out = []
    for poly in polys:
        pts = list(poly)
        for axis, bound, keep_ge in ((0, c[0], True), (0, c[2], False), (1, c[1], True), (1, c[3], False)):
            nxt = []
            for i in range(len(pts)):
                a, b = pts[i], pts[(i + 1) % len(pts)]
                ina = (a[axis] >= bound) if keep_ge else (a[axis] <= bound)
                inb = (b[axis] >= bound) if keep_ge else (b[axis] <= bound)
                if ina:
                    nxt.append(a)
                if ina != inb:
                    t = (bound - a[axis]) / (b[axis] - a[axis])
                    nxt.append((bound, a[1] + t * (b[1] - a[1])) if axis == 0 else (a[0] + t * (b[0] - a[0]), bound))
            pts = nxt
            if not pts:
                break
        if len(pts) >= 3 and abs(_area2(pts)) > 2 * EPS:
            out.append(tuple((round(p[0], 3), round(p[1], 3)) for p in pts) if tuple(pts) != tuple(poly) else tuple(poly))
    return tuple(out)


def _dim_off(aid, p, q, nrm, off, scale, text):
    """p -> q 를 재는 치수 항목(annot_draw 의 "dim" 과 같은 모양). 치수선은 p·q 에서 단위 법선 nrm 방향으로 off(mm) 떨어지고,
    보조선은 대상에서 0.8mm(용지) 띄워 시작해 치수선 너머 1.2mm 까지 간다. 길이 0 이면 None."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    ln = sqrt(dx * dx + dy * dy)
    if ln < 1.0:
        return None
    ux, uy = dx / ln, dy / ln
    gap, ext = 0.8 * scale, 1.2 * scale
    a = (p[0] + nrm[0] * off, p[1] + nrm[1] * off)
    b = (q[0] + nrm[0] * off, q[1] + nrm[1] * off)
    r = lambda *vs: tuple(round(x, 3) + 0.0 for x in vs)   # noqa: E731
    e0 = r(p[0] + nrm[0] * gap, p[1] + nrm[1] * gap, a[0] + nrm[0] * ext, a[1] + nrm[1] * ext)
    e1 = r(q[0] + nrm[0] * gap, q[1] + nrm[1] * gap, b[0] + nrm[0] * ext, b[1] + nrm[1] * ext)
    return ("dim", aid, e0, e1, r(a[0], a[1], b[0], b[1]), text, r((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0), r(ux, uy))


def detail_extent(g, e, kind, cfg):
    """계단·승강기 하나가 뷰 좌표에서 차지하는 범위 (x1, y1, x2, y2). kind 는 "plan" 또는 "sec"(단면·입면), cfg 는 단면·입면의 view_cfg 튜플(평면은 None).
    평면은 모델 x,y, 단면·입면은 가로 s 와 절대 높이 z. 계단·승강기가 아니거나 그리지 않는 것이면 None. 그래프 질의가 아니라 보통 함수다(뷰가 아직 없을 때도 잰다)."""
    pts: list = []
    zs: list = []
    st = g.q(stair_info, e)
    if st is not None:
        flights, ld = g.q(stair_geom, e)
        for fi in flights:
            h = fi[7] / 2.0
            pts += [_st_pt(fi, 0.0, h), _st_pt(fi, 0.0, -h), _st_pt(fi, fi[6], h), _st_pt(fi, fi[6], -h)]
            zs += [fi[0] - fi[11], fi[1]]
        if ld is not None:
            pts += list(ld[0])
            zs += [ld[1], ld[2]]
        wg = g.q(stair_winders, e)
        for w in (() if wg is None else wg[0]):
            pts += list(w[0])
            zs += [w[1], w[2]]
    else:
        ev = g.q(elevator_info, e)
        if ev is None:
            return None
        x1, y1, x2, y2 = ev[0]
        pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        ex = g.q(elevator_ext, e)
        zs = [ex["pit_z0"], ex["mach_z1"]]   # 피트·기계실이 없으면 샤프트 아래·위와 같다
    if kind == "plan":
        return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
    ss = [_sw(cfg, p)[0] for p in pts]
    return (min(ss), min(zs), max(ss), max(zs))


def _hits(ext, crop) -> bool:
    return ext is not None and (crop is None or not (ext[2] < crop[0] or ext[0] > crop[2] or ext[3] < crop[1] or ext[1] > crop[3]))


def _plan_stair_dims(g, s, cfg):
    info = g.q(stair_info, s)
    if info is None or cfg[2] < info[0] - EPS:
        return []
    flights, ld = g.q(stair_geom, s)
    sc, tag = cfg[4], s[-6:]
    out = []
    for i, fi in enumerate(flights):
        _zb, _zt, _x, _y, ux, uy, length, width, n, td, _rise, _sk = fi
        h = width / 2.0
        out.append(_dim_off(f"detail:{tag}:tread:{i}", _st_pt(fi, 0.0, h), _st_pt(fi, length, h), (-uy, ux), DIM_OFF * sc, sc, f"{_fmt_mm(td)}x{n - 1}={_fmt_mm(length)}"))
        t_w, sg = (0.0, -1.0) if i == 0 else (length, 1.0)   # 폭 치수는 첫 단 묶음은 시작 끝에, 둘째는 위 끝에(층참 쪽을 피한다)
        out.append(_dim_off(f"detail:{tag}:width:{i}", _st_pt(fi, t_w, h), _st_pt(fi, t_w, -h), (sg * ux, sg * uy), DIM_OFF * sc, sc, _fmt_mm(width)))
    if ld is not None:
        poly = ld[0]
        sgn = 1.0 if _area2(poly) > 0 else -1.0
        for k, nm in ((0, "a"), (1, "b")):
            a, b = poly[k], poly[(k + 1) % len(poly)]
            ln = sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
            if ln >= 1.0:
                out.append(_dim_off(f"detail:{tag}:landing:{nm}", a, b, (sgn * (b[1] - a[1]) / ln, -sgn * (b[0] - a[0]) / ln), DIM_OFF * sc, sc, f"층참 {_fmt_mm(ln)}"))
    return [d for d in out if d is not None]


def _sec_stair_dims(g, s, cfg):
    info = g.q(stair_info, s)
    if info is None:
        return []
    flights, _ld = g.q(stair_geom, s)
    sc, tag = cfg[10], s[-6:]
    rx, ry = cfg[5], cfg[6]
    out = []
    for i, fi in enumerate(flights):
        zb, zt, x1, y1, ux, uy, length, _w, n, td, rise, skirt = fi
        cs = ux * rx + uy * ry
        if abs(cs) < 0.999:   # 걸음선이 단면과 나란하지 않은 단 묶음은 옆모습이 아니라서 치수를 달지 않는다
            continue
        sg = 1.0 if cs > 0 else -1.0
        s0 = _sw(cfg, (x1, y1))[0]
        s1 = s0 + sg * length
        out.append(_dim_off(f"detail:{tag}:run:{i}", (s0, zb - skirt), (s1, zb - skirt), (0.0, -1.0), DIM_OFF * sc, sc, f"{_fmt_mm(td)}x{n - 1}={_fmt_mm(length)}"))
        out.append(_dim_off(f"detail:{tag}:rise:{i}", (s1, zb), (s1, zt), (sg, 0.0), DIM_OFF * sc, sc, f"{_fmt_mm(rise)}x{n}={_fmt_mm(zt - zb)}"))
        out.append(_dim_off(f"detail:{tag}:riser:{i}", (s0, zb), (s0, zb + rise), (-sg, 0.0), DIM_OFF * 0.7 * sc, sc, _fmt_mm(rise)))
        out.append(_dim_off(f"detail:{tag}:tread1:{i}", (s0, zb + rise), (s0 + sg * td, zb + rise), (0.0, 1.0), DIM_OFF * 0.7 * sc, sc, _fmt_mm(td)))
    return [d for d in out if d is not None]


def _sec_railing_dims(g, r, cfg):
    info = g.q(railing_info, r)
    if info is None:
        return []
    surf, top, _posts = info
    x, y, z = surf[0]
    s = _sw(cfg, (x, y))[0]
    return [d for d in (_dim_off(f"detail:{r[-6:]}:rail", (s, z), (s, top[0][2]), (-1.0, 0.0), DIM_OFF * 0.7 * cfg[10], cfg[10], f"난간 {_fmt_mm(top[0][2] - z)}"),) if d is not None]


def _plan_elev_dims(g, e, cfg):
    info = g.q(elevator_info, e)
    if info is None:
        return []
    (sx1, sy1, sx2, sy2), (cx1, cy1, cx2, cy2) = info[0], info[1]
    sc, tag = cfg[4], e[-6:]
    out = [_dim_off(f"detail:{tag}:cabw", (cx1, cy1), (cx2, cy1), (0.0, -1.0), (cy1 - sy1) + DIM_OFF * 0.8 * sc, sc, f"카 {_fmt_mm(cx2 - cx1)}"),
           _dim_off(f"detail:{tag}:shaftw", (sx1, sy1), (sx2, sy1), (0.0, -1.0), DIM_OFF * 1.6 * sc, sc, f"샤프트 {_fmt_mm(sx2 - sx1)}"),
           _dim_off(f"detail:{tag}:cabd", (cx2, cy1), (cx2, cy2), (1.0, 0.0), (sx2 - cx2) + DIM_OFF * 0.8 * sc, sc, f"카 {_fmt_mm(cy2 - cy1)}"),
           _dim_off(f"detail:{tag}:shaftd", (sx2, sy1), (sx2, sy2), (1.0, 0.0), DIM_OFF * 1.6 * sc, sc, f"샤프트 {_fmt_mm(sy2 - sy1)}")]
    if not info[2] - EPS <= cfg[2] <= info[3] + EPS:   # 피트·기계실 높이의 평면에는 카가 없다: 샤프트 치수만
        out = [out[1], out[3]]
    ex = g.q(elevator_ext, e)
    if ex["doors"] is not None and cfg[1] in {lid for lid, _z in ex["doors"]["stops"]} and info[2] - EPS <= cfg[2] <= info[3] + EPS:   # 승강장 문 폭(문이 있는 층 평면)
        (xa, ya), (xb, yb) = ex["doors"]["edge"]
        nx, ny = ex["doors"]["normal"]
        far = nx < 0 or ny < 0   # 샤프트 치수가 있는 -y·-x 쪽 면은 더 바깥에 단다
        out.append(_dim_off(f"detail:{tag}:door", (xa, ya), (xb, yb), (nx, ny), DIM_OFF * (2.4 if far else 0.9) * sc, sc, f"문 {_fmt_mm(ex['doors']['w'])}"))
    return [d for d in out if d is not None]


def _sec_elev_dims(g, e, cfg):
    info = g.q(elevator_info, e)
    if info is None:
        return []
    (x1, y1, x2, y2), _cab, z0, z1, zc = info
    ss = [_sw(cfg, p) for p in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))]
    if max(p[1] for p in ss) < cfg[7] - EPS or min(p[1] for p in ss) > cfg[8] + EPS:   # 단면 깊이 밖
        return []
    smin, smax = min(p[0] for p in ss), max(p[0] for p in ss)
    sc, tag = cfg[10], e[-6:]
    lv = g.q(Q.level_info, g.q(Q.elevator_level, e))[1]
    off, off2 = DIM_OFF * sc, DIM_OFF2 * sc
    out = [_dim_off(f"detail:{tag}:cab", (smax, z0), (smax, zc), (1.0, 0.0), off, sc, f"카 {_fmt_mm(zc - z0)}"),
           _dim_off(f"detail:{tag}:oh", (smax, zc), (smax, z1), (1.0, 0.0), off, sc, f"OH {_fmt_mm(z1 - zc)}"),
           _dim_off(f"detail:{tag}:shaft", (smax, z0), (smax, z1), (1.0, 0.0), off2, sc, f"승강로 {_fmt_mm(z1 - z0)}")]
    ex = g.q(elevator_ext, e)
    zb = ex["pit_z0"]   # 피트 바닥(피트 입력이 없으면 카 바닥과 같다)
    if zb < lv - 1.0:
        out.append(_dim_off(f"detail:{tag}:pit", (smin, zb), (smin, lv), (-1.0, 0.0), off, sc, f"피트 {_fmt_mm(lv - zb)}"))
    if ex["mach_z1"] > z1 + 1.0:
        out.append(_dim_off(f"detail:{tag}:machine", (smax, z1), (smax, ex["mach_z1"]), (1.0, 0.0), off, sc, f"기계실 {_fmt_mm(ex['mach_z1'] - z1)}"))
    return [d for d in out if d is not None]


def view_dims(g, v):
    """dims 옵션을 켠 평면·단면 뷰의 계단·승강기 치수 항목들(annot 의 "dim" 항목과 같은 모양). 옵션이 없으면 빈 값. crop 이 있으면 그 사각형과 겹치는 요소만."""
    cfg = g.q(view_cfg, v)
    opts = g.q(view_opts, v)
    if cfg is None or "dims" not in opts or cfg[0] not in ("plan", "sec"):
        return ()
    crop = opts.get("crop")
    plan = cfg[0] == "plan"
    out = []
    for s in g.q(scope_stairs, v):
        if _hits(detail_extent(g, s, "plan" if plan else "sec", None if plan else cfg), crop):
            out.extend(_plan_stair_dims(g, s, cfg) if plan else _sec_stair_dims(g, s, cfg))
    for e in g.q(scope_elevators, v):
        if _hits(detail_extent(g, e, "plan" if plan else "sec", None if plan else cfg), crop):
            out.extend(_plan_elev_dims(g, e, cfg) if plan else _sec_elev_dims(g, e, cfg))
    if not plan:
        seen = set()   # 계단 양옆 난간처럼 단면에서 같은 자리·같은 높이로 겹치는 난간 치수는 하나만 단다
        for r in g.q(scope_railings, v):
            info = g.q(railing_info, r)
            if info is None:
                continue
            sr = _sw(cfg, info[0][0])[0]
            key = (round(sr, 1), round(info[0][0][2], 1), round(info[1][0][2] - info[0][0][2], 1))
            if key not in seen and (crop is None or crop[0] <= sr <= crop[2]):
                seen.add(key)
                out.extend(_sec_railing_dims(g, r, cfg))
    return tuple(out)


def view_dim_labels(g, v):
    """dims 옵션 뷰의 문자: 계단 평면은 단수·챌판 높이·디딤 깊이(걸음선 오른쪽), 계단 단면은 층참 높이."""
    cfg = g.q(view_cfg, v)
    opts = g.q(view_opts, v)
    if cfg is None or "dims" not in opts or cfg[0] not in ("plan", "sec"):
        return ()
    crop = opts.get("crop")
    plan = cfg[0] == "plan"
    sc = cfg[4] if plan else cfg[10]
    out = []
    for s in g.q(scope_stairs, v):
        info = g.q(stair_info, s)
        if not _hits(detail_extent(g, s, "plan" if plan else "sec", None if plan else cfg), crop):
            continue
        flights, ld = g.q(stair_geom, s)
        if plan:
            if cfg[2] < info[0] - EPS:
                continue
            fi = flights[0]
            h = fi[7] / 2.0
            x, y = _st_pt(fi, fi[6] / 2.0, -h - 4.0 * sc)
            out.append((round(x, 3), round(y, 3), f"{info[8]}단 챌판 {_fmt_mm(info[10])} 디딤 {_fmt_mm(info[9])}", LABEL_SUB_H, "middle"))
        elif ld is not None:
            sx = sum(_sw(cfg, q)[0] for q in ld[0]) / len(ld[0])
            out.append((round(sx, 3), round(ld[2] + 1.5 * sc, 3), f"층참 +{_fmt_mm(ld[2] - flights[0][0])}", LABEL_SUB_H, "middle"))
    return tuple(out)


# ================================================================ 뷰 전체
def merge_lines(segs):
    """선분 묶음을 정규화한다: 같은 직선 위에서 겹치거나 이어진 선분은 하나로, 절단선(cut)이 덮는 부분은 아래 등급에서 지운다.
    결과는 정렬된 ((등급, x0, y0, x1, y1), ...) 이고 좌표는 마이크로미터로 반올림한다."""
    groups: dict = {}
    for cls, x0, y0, x1, y1 in segs:
        dx, dy = x1 - x0, y1 - y0
        length = sqrt(dx * dx + dy * dy)
        if length < SEG_MIN:
            continue
        ux, uy = dx / length, dy / length
        if ux < -1e-9 or (abs(ux) <= 1e-9 and uy < 0.0):
            ux, uy = -ux, -uy
        off = -uy * x0 + ux * y0
        key = (round(ux, 5), round(uy, 5), round(off, 2))
        grp = groups.setdefault(key, [ux, uy, off, {}])
        gx, gy = grp[0], grp[1]                       # 같은 직선 묶음은 첫 선분의 방향으로 매개변수를 잰다
        t0, t1 = sorted((gx * x0 + gy * y0, gx * x1 + gy * y1))
        grp[3].setdefault(cls, []).append((t0, t1))
    out = []
    for key in sorted(groups):
        ux, uy, off, by_cls = groups[key]
        merged = {}
        for cls, ivs in by_cls.items():
            ivs.sort()
            m = []
            for a, b in ivs:
                if m and a <= m[-1][1] + EPS:
                    m[-1][1] = max(m[-1][1], b)
                else:
                    m.append([a, b])
            merged[cls] = m
        if "cut" in merged:
            for cls in merged:
                if cls != "cut":
                    merged[cls] = _subtract(merged[cls], merged["cut"])
        if "cutn" in merged:   # 비내력벽 절단선(선 굵기 위계를 켠 뷰에만 있다)이 덮는 곳은 아래 등급에서 지운다. 내력벽 절단선(cut)이 먼저 덮는다
            for cls in merged:
                if cls not in ("cut", "cutn"):
                    merged[cls] = _subtract(merged[cls], merged["cutn"])
        for cls in sorted(merged):
            for a, b in merged[cls]:
                if b - a > SEG_MIN:
                    out.append((cls, round(-uy * off + ux * a, 3), round(ux * off + uy * a, 3),
                                round(-uy * off + ux * b, 3), round(ux * off + uy * b, 3)))
    return tuple(out)


def _subtract(ivs, cuts):
    out = []
    for a, b in ivs:
        cur = a
        for c0, c1 in cuts:
            if c1 <= cur or c0 >= b:
                continue
            if c0 > cur:
                out.append([cur, c0])
            cur = max(cur, c1)
        if cur < b:
            out.append([cur, b])
    return out


# ================================================================ 일반 솔리드 (kinds "solid", 18단계, 설계: elements-design.md 17절)
# 형상은 solids.solid_mesh 의 다면체다(IFC 투영과 같은 값). 그리는 모서리는 경계이거나 이웃 면이 5도 넘게 꺾이는 모서리다(같은 평면을 나눈 대각선은 그리지 않는다).
#   평면: 절단 높이의 수평면으로 자른 선(cut)과, 절단면 아래 보이는 깊이 안에서 위를 향한 면에 붙은 모서리(proj). 레벨과 무관하게 높이 범위가 걸치는 솔리드를 그린다
#   단면·입면: 보는 쪽을 향한 면에 붙은 모서리(뒷면끼리의 모서리는 그리지 않는다)를 투영선으로, 단면은 절단면으로 자른 선(cut)을 더한다.
#   가림: 벽·슬래브·계단·지붕·패밀리 조각과 돌출(prism)·판(panel) 솔리드의 볼록 조각이 가린다. 로프트·스윕·메시는 가리지 않는다(알려진 한계)
#   벽 선도 돌출·판 솔리드(부벽 등)가 앞에 있으면 가려진다. 솔리드가 없는 프로젝트의 도면은 이전과 같다
OCC_BIN = 2000.0    # 가림 조각 색인의 s 방향 칸 폭(mm)


def solid_info(g, e):
    """(꼭짓점(절대 z), 면, 모서리, 면 법선, 경계 상자(x0,y0,z0,x1,y1,z1)) 또는 None. 저작 솔리드만 그린다."""
    row = Q.el(g, e)
    if row is None or row["kind"] != "solid" or row["origin"] != "authored":
        return None
    lv = g.q(Q.level_info, g.q(Q.solid_level, e))[1]
    verts, faces = SO.solid_mesh(row["params"])
    verts = tuple((x, y, z + lv) for x, y, z in verts)
    edges, nrm = SO.feature_edges(verts, faces)
    return (verts, tuple(tuple(tuple(lp) for lp in f) for f in faces), edges, nrm, SO.mesh_bbox(verts))


def all_solids(g):
    out = []
    for b in range(Q.NB):
        out.extend(e for e in Q.bucket_ids(g, "solid", b) if g.q(solid_info, e) is not None)
    return tuple(sorted(out))


def scope_solids(g, v):
    """뷰가 그리는 솔리드. 평면은 높이 범위가 (절단 높이 - 보이는 깊이, 절단 높이) 에 걸치는 것, 단면·입면은 모델 전체."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    if cfg[0] == "plan":
        if cfg[1] is None or is_rcp(cfg):   # 반사 천장 평면(W1 1-C 3차)은 아직 그리지 않는다
            return ()
        c, lo = cfg[2], cfg[2] - cfg[3]
        return tuple(e for e in g.q(all_solids) if (bb := g.q(solid_info, e)[4])[2] <= c + EPS and bb[5] >= lo - EPS)
    return g.q(all_solids)


def _solid_vpieces(g, v, e):
    """단면·입면에서 솔리드가 가리는 볼록 기둥 조각 ((z0, z1, 다각형(s,w)), ...): 돌출은 윤곽의 삼각형, 판은 윤곽을 높이 띠로 나눈 사각형. 그 밖은 없다.
    판의 띠는 위아래로 2*EPS 겹친다: 띠 경계 높이의 수평선(구멍 둘레)이 어느 띠 안쪽도 아니라서 보이는 선으로 남지 않게(벽의 _bridges 와 같은 이유)."""
    cfg = g.q(view_cfg, v)
    row = Q.el(g, e)
    p = row["params"]
    lv = g.q(Q.level_info, g.q(Q.solid_level, e))[1]
    out = []

    def add(z0, z1, poly):
        poly = _clip_w([_sw(cfg, q) for q in poly], cfg[7], cfg[8])
        if len(poly) >= 3 and _area2(poly) > 2 * EPS:
            out.append((z0, z1, tuple(poly)))
    if p["form"] == "prism":
        for tri in SO.triangulate(SO.ccw(p["polygon"])):
            add(p["z0"] + lv, p["z1"] + lv, tri)
    elif p["form"] == "panel":
        x1, y1, ux, uy, _ = SO.panel_frame(p["line"])
        h = p["thickness"] / 2.0
        loops = [SO.ccw(p["profile"])] + [SO.opening_loop(o) for o in p.get("openings", ())]
        zs = sorted({q[1] for lp in loops for q in lp})
        for za, zb in zip(zs, zs[1:]):
            if zb - za < EPS:
                continue
            for u0, u1 in SO.scan_intervals(loops, (za + zb) / 2.0):
                add(za + lv - 2 * EPS, zb + lv + 2 * EPS, [(x1 + u * ux - vv * uy, y1 + u * uy + vv * ux) for u, vv in ((u0, -h), (u1, -h), (u1, h), (u0, h))])
    return tuple(out)


def _occ_index(g, v):
    """뷰 하나의 가림 조각 전체와 s 방향 칸 색인: (조각들, 경계 상자들 (s0, s1, w0, z0, z1), {칸: (번호, ...)})."""
    pieces = []
    for w in g.q(scope_walls, v):
        pieces.extend(g.q(vpieces, v, w))
        pieces.extend(_bridges(g, v, w))
    for sl in g.q(scope_slabs, v):
        pieces.extend(g.q(_slab_vpieces, v, sl))
    for st in g.q(scope_stairs, v):
        pieces.extend(g.q(_stair_vpieces, v, st))
    for fe in g.q(scope_fams, v):
        pieces.extend(g.q(_fam_vpieces, v, fe))
    for rf in g.q(scope_roofs, v):
        pieces.extend(g.q(_roof_solid_vpieces, v, rf))
    for e in g.q(scope_solids, v):
        pieces.extend(g.q(_solid_vpieces, v, e))
    boxes, bins = [], {}
    for i, (z0, z1, poly) in enumerate(pieces):
        s0, s1 = min(q[0] for q in poly), max(q[0] for q in poly)
        boxes.append((s0, s1, min(q[1] for q in poly), z0, z1))
        for k in range(int(s0 // OCC_BIN), int(s1 // OCC_BIN) + 1):
            bins.setdefault(k, []).append(i)
    return (tuple(pieces), tuple(boxes), {k: tuple(x) for k, x in bins.items()})


def _occ_near(idx, s0, s1, z0, z1, wmax):
    """색인에서 (s, z) 범위가 겹치고 wmax 보다 앞(작은 w)에 걸친 조각들."""
    pieces, boxes, bins = idx
    seen, out = set(), []
    for k in range(int(s0 // OCC_BIN), int(s1 // OCC_BIN) + 1):
        for i in bins.get(k, ()):
            if i in seen:
                continue
            seen.add(i)
            b = boxes[i]
            if b[0] <= s1 + EPS and b[1] >= s0 - EPS and b[3] <= z1 + EPS and b[4] >= z0 - EPS and b[2] < wmax:
                out.append(pieces[i])
    return out


def _vis(e0, e1, occ):
    """보이는 매개변수 구간: 수평·수직은 visible_parts, 비스듬한 선은 _visible_slanted."""
    if not occ:
        return [(0.0, 1.0)]
    vertical = abs(e1[0] - e0[0]) < 1e-9 and abs(e1[1] - e0[1]) < 1e-9
    if vertical or abs(e1[2] - e0[2]) < 1e-9:
        return visible_parts(e0, e1, occ)
    return _visible_slanted(e0, e1, occ)


def solid_lines(g, v, e):
    """솔리드 하나가 뷰에 기여하는 선분."""
    cfg = g.q(view_cfg, v)
    info = g.q(solid_info, e)
    if cfg is None or info is None:
        return ()
    verts, faces, edges, nrm, _bb = info
    out = []
    if cfg[0] == "plan":
        c, lo = cfg[2], cfg[2] - cfg[3]
        for a, b in SO.plane_section(verts, faces, nrm, (0.0, 0.0, 1.0), c):
            if sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) > SEG_MIN:
                out.append(("cut", a[0], a[1], b[0], b[1]))
        for ia, ib, fl in edges:
            if len(fl) == 2 and all(nrm[f][2] <= 1e-6 for f in fl):
                continue   # 위를 향한 면에 붙지 않은 모서리(옆면끼리·밑면)는 위에서 보이지 않거나 윤곽과 겹친다
            a, b = verts[ia], verts[ib]
            t0, t1 = 0.0, 1.0
            dz = b[2] - a[2]
            for bound, keep_le in ((c, True), (lo, False)):
                da = a[2] - bound
                if abs(dz) < 1e-12:
                    if (da > EPS) if keep_le else (da < -EPS):
                        t0, t1 = 1.0, 0.0
                    continue
                t = -da / dz
                if (dz > 0) == keep_le:
                    t1 = min(t1, t)
                else:
                    t0 = max(t0, t)
            if t1 - t0 <= 1e-9:
                continue
            ax, ay = a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0
            bx, by = a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1
            if sqrt((bx - ax) ** 2 + (by - ay) ** 2) > SEG_MIN:
                out.append(("proj", ax, ay, bx, by))
        return tuple(out)
    d = (cfg[3], cfg[4], 0.0)
    idx = g.q(_occ_index, v)
    if cfg[9]:
        c = cfg[1] * d[0] + cfg[2] * d[1] + cfg[7]
        for a, b in SO.plane_section(verts, faces, nrm, d, c):
            sa, sb = _sw(cfg, a), _sw(cfg, b)
            if sqrt((sb[0] - sa[0]) ** 2 + (b[2] - a[2]) ** 2) > SEG_MIN:
                out.append(("cut", sa[0], a[2], sb[0], b[2]))
    for ia, ib, fl in edges:
        if len(fl) == 2 and all(nrm[f][0] * d[0] + nrm[f][1] * d[1] >= -1e-9 for f in fl):
            continue   # 뒷면끼리의 모서리
        a, b = verts[ia], verts[ib]
        pa, pb = _sw(cfg, a), _sw(cfg, b)
        e0, e1 = (pa[0], pa[1], a[2]), (pb[0], pb[1], b[2])
        if abs(e1[0] - e0[0]) < EPS and abs(e1[2] - e0[2]) < EPS:
            continue
        cl = _clip_edge(e0, e1, cfg[7], cfg[8])
        if cl is None:
            continue
        e0, e1 = cl
        occ = _occ_near(idx, min(e0[0], e1[0]), max(e0[0], e1[0]), min(e0[2], e1[2]), max(e0[2], e1[2]), max(e0[1], e1[1]))
        for t0, t1 in _vis(e0, e1, occ):
            out.append(("proj", e0[0] + t0 * (e1[0] - e0[0]), e0[2] + t0 * (e1[2] - e0[2]), e0[0] + t1 * (e1[0] - e0[0]), e0[2] + t1 * (e1[2] - e0[2])))
    return tuple(out)


def _solid_wall_occ(g, v, w):
    """벽 w 의 선을 가릴 수 있는 솔리드 조각(벽의 화면 범위와 겹치는 것만). 솔리드가 없으면 빈 목록."""
    if not g.q(scope_solids, v):
        return []
    bb = g.q(vbbox, v, w)
    if bb is None:
        return []
    pieces = []
    for e in g.q(scope_solids, v):
        pieces.extend(g.q(_solid_vpieces, v, e))
    return [p for p in pieces if min(q[0] for q in p[2]) <= bb[2] + EPS and max(q[0] for q in p[2]) >= bb[0] - EPS and p[0] <= bb[3] + EPS and p[1] >= bb[1] - EPS]


# ---------------------------------------------------------------- 벽의 모양 개구부(18단계): 입면 윤곽선과 평면 유리선
def wall_shape_openings(g, w):
    """벽의 모양 개구부: ((사각 부분 상자 (u0,u1,z0,z_spring) 절대 z 또는 None, 윤곽 ((u, z) 절대), 채움), ...)."""
    p = Q.el(g, w)["params"]
    if "openings" not in p:
        return ()
    _, elev = g.q(Q.level_info, g.q(Q.wall_level, w))
    out = []
    for o in p["openings"]:
        u0, u1, z0, zs, _zt = SO.opening_box(o)
        box = (u0, u1, elev + z0, elev + zs) if zs - z0 > EPS else None
        out.append((box, tuple((u, elev + z) for u, z in SO.opening_loop(o)), o.get("fill", "none")))
    return tuple(out)


def plan_wall_glazing(g, v, w):
    """평면: 유리 채움(glazing) 모양 개구부가 절단 높이에 걸치면 개구부 가운데에 유리선 하나(proj)."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "plan":
        return ()
    sol = g.q(wall_solid, w)
    out = []
    for box, loop, fill in g.q(wall_shape_openings, w):
        if fill != "glazing":
            continue
        for u0, u1 in SO.scan_intervals([list(loop)], cfg[2]):
            a, b = _world(sol, u0, 0.0), _world(sol, u1, 0.0)
            out.append(("proj", a[0], a[1], b[0], b[1]))
    return tuple(out)


def _wall_shape_edges(sol, shapes, pt):
    """입면용 모양 개구부 윤곽선: 두 면의 윤곽(현) + 두께 방향 선(사각 부분 모서리)."""
    h = sol[4]
    raw = []
    for box, loop, _fill in shapes:
        n = len(loop)
        for vv in (-h, h):
            for i in range(n):
                (ua, za), (ub, zb) = loop[i], loop[(i + 1) % n]
                raw.append((pt(ua, vv, za), pt(ub, vv, zb)))
        if box is not None:
            u0, u1, z0, zs = box
            for u in (u0, u1):
                for z in ((z0, zs) if z0 > sol[7] + EPS else (zs,)):
                    raw.append((pt(u, -h, z), pt(u, h, z)))
    return raw


# ================================================================ 통심선 (kinds "grid", 19단계, 설계: elements-design.md 21절)
# 평면: 선분을 1점쇄선(symbol)으로, 끝(bubbles)에 원 기호와 기호 문자. 입면·단면: 보는 방향과 나란한(1도 안) 통심선을 세로 1점쇄선으로, 위 끝에 원 기호.
# 통심선이 없으면 선분·문자는 이전과 같다.
GRID_R = 4.0                          # 원 기호 반지름(용지 mm)
GRID_DASH, GRID_GAP, GRID_DOT = 12.0, 1.5, 1.0   # 1점쇄선 마디(용지 mm)
GRID_TEXT_H = 2.5                     # 기호 문자 높이(용지 mm)
GRID_EXT = 1000.0                     # 입면·단면 세로선이 맨 아래·맨 위 레벨 밖으로 나가는 길이(mm)
GRID_PAR = 0.01745                    # 나란함 판정(sin 1도)


def all_grids(g):
    out = []
    for b in range(Q.NB):
        out.extend(Q.bucket_ids(g, "grid", b))
    return tuple(sorted(out))


def _level_z_range(g):
    zs = []
    for b in range(Q.NB):
        zs.extend(g.q(Q.level_info, lv)[1] for lv in Q.bucket_ids(g, "level", b))
    return (min(zs), max(zs)) if zs else (0.0, 0.0)


def _grid_dash_dot(cls, a, b, sc):
    """a -> b 1점쇄선(마디·점·간격은 용지 위 일정)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = sqrt(dx * dx + dy * dy)
    if ln < SEG_MIN:
        return []
    ux, uy = dx / ln, dy / ln
    pat = ((GRID_DASH * sc, True), (GRID_GAP * sc, False), (GRID_DOT * sc, True), (GRID_GAP * sc, False))
    out, t, i = [], 0.0, 0
    while t < ln - SEG_MIN:
        step, draw = pat[i % 4]
        t1 = min(t + step, ln)
        if draw:
            out.append((cls, a[0] + ux * t, a[1] + uy * t, a[0] + ux * t1, a[1] + uy * t1))
        t, i = t1, i + 1
    return out


def _circle_segs(cls, c, r):
    pts = [(c[0] + r * cs, c[1] + r * sn) for cs, sn in (_sincos_deg(360.0 * k / CIRC_N) for k in range(CIRC_N))]
    return [(cls, pts[k][0], pts[k][1], pts[(k + 1) % CIRC_N][0], pts[(k + 1) % CIRC_N][1]) for k in range(CIRC_N)]


def grid_marks(g, v, gr):
    """통심선 하나의 (선분들, 문자들) — 뷰 좌표(평면 x,y / 입면·단면 s,z)."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ((), ())
    p = Q.el(g, gr)["params"]
    x1, y1, x2, y2 = p["line"]
    sc = cfg[4] if cfg[0] == "plan" else cfg[10]
    r = GRID_R * sc
    if cfg[0] == "plan":
        a, b = (x1, y1), (x2, y2)
        ends = {"both": (0, 1), "start": (0,), "end": (1,), "none": ()}[p["bubbles"]]
    else:
        ln = sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        ux, uy = (x2 - x1) / ln, (y2 - y1) / ln
        if abs(ux * cfg[4] - uy * cfg[3]) > GRID_PAR:   # 보는 방향과 나란하지 않다(점으로 투영되지 않는다)
            return ((), ())
        s0, w0 = _sw(cfg, (x1, y1))
        _s1, w1 = _sw(cfg, (x2, y2))
        if max(w0, w1) < cfg[7] or min(w0, w1) > cfg[8]:   # 단면: 보이는 깊이 범위 밖
            return ((), ())
        z0, z1 = g.q(_level_z_range)
        a, b = (s0, z0 - GRID_EXT), (s0, z1 + GRID_EXT)
        ends = (1,) if p["bubbles"] != "none" else ()
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = sqrt(dx * dx + dy * dy)
    ux, uy = dx / ln, dy / ln
    segs = _grid_dash_dot("symbol", a, b, sc)
    labels = []
    for k in ends:
        e, sg = (a, -1.0) if k == 0 else (b, 1.0)
        c = (e[0] + sg * ux * r, e[1] + sg * uy * r)   # 원은 선 끝 바깥에 붙는다
        segs.extend(_circle_segs("symbol", c, r))
        labels.append((round(c[0], 3), round(c[1] - 0.35 * GRID_TEXT_H * sc, 3), p["name"], GRID_TEXT_H, "middle"))
    return (tuple(segs), tuple(labels))


def view_grid_lines(g, v):
    out = []
    for gr in g.q(all_grids):
        out.extend(g.q(grid_marks, v, gr)[0])
    return tuple(out)


def view_grid_labels(g, v):
    out = []
    for gr in g.q(all_grids):
        out.extend(g.q(grid_marks, v, gr)[1])
    return tuple(out)


def view_lines(g, v):
    cfg = g.q(view_cfg, v)
    if cfg is None:
        from dobim_core import viewplans as VP   # 범례 뷰(W1 1-C 3차): 범례가 아니면 빈 값
        return g.q(VP.legend_lines, v)
    if cfg[0] == "site":
        return g.q(site_lines, v)
    fn = plan_wall_lines if cfg[0] == "plan" else vwall_lines
    segs = []
    for w in g.q(scope_walls, v):
        segs.extend(g.q(fn, v, w))
        if cfg[0] == "plan":
            segs.extend(g.q(plan_fire_lines, v, w))
            segs.extend(g.q(plan_wall_glazing, v, w))
        elif cfg[0] == "elev":
            segs.extend(g.q(elev_finish, v, w)[0])
    for e in g.q(scope_fams, v):
        segs.extend(g.q(fam_lines, v, e))
    for sl in g.q(scope_slabs, v):
        segs.extend(g.q(slab_lines, v, sl))
    for st in g.q(scope_stairs, v):
        segs.extend(g.q(stair_lines, v, st))
    for rl in g.q(scope_railings, v):
        segs.extend(g.q(railing_lines, v, rl))
    for ev in g.q(scope_elevators, v):
        segs.extend(g.q(elevator_lines, v, ev))
    for rf in g.q(scope_roofs, v):
        segs.extend(g.q(roof_lines, v, rf))
    for so in g.q(scope_solids, v):   # 일반 솔리드(18단계): 없으면 선분은 이전과 같다
        segs.extend(g.q(solid_lines, v, so))
    for fe in g.q(scope_foreign, v):   # 외래 IFC 요소(1단계 외래 도면 경로): 형상 있는 외래 요소가 없으면 선분은 이전과 같다
        segs.extend(g.q(foreign_lines, v, fe))
    if cfg[0] == "elev":
        segs.extend(g.q(site_sign_lines, v))
    elif cfg[0] == "sec":
        segs.extend(g.q(site_section_lines, v))
    segs.extend(g.q(view_grid_lines, v))   # 통심선(19단계): 없으면 선분은 이전과 같다
    if len(cfg) == 6:   # 반사 천장 평면의 천장선·면적 평면의 면적 경계·해치(W1 1-C 3차)
        from dobim_core import viewplans as VP
        segs.extend(g.q(VP.mode_lines, v))
    return _crop_if(g, v, merge_lines(segs))


def _falsy(v) -> bool:
    return v is False or (isinstance(v, str) and v.strip().lower() in ("false", "no", "n", "0", "아니오", "비내력"))


def is_nonbearing(g, w) -> bool:
    """벽이 비내력벽인가: Pset_WallCommon.LoadBearing 이 명시적으로 거짓일 때만(값이 없으면 구분하지 않는다: 내력벽처럼 그린다)."""
    return _falsy(_prop(g, w, "Pset_WallCommon", "LoadBearing"))


def view_lines_hier(g, v):
    """선 굵기 위계(sheet.export profile.line_hierarchy)용 뷰 선분: view_lines 와 같되 비내력벽(LoadBearing 이 거짓)의 절단선은 "cutn" 등급이다(LN-04).
    비내력벽이 없으면 view_lines 와 같은 값이다. 평면·단면의 벽 절단선만 나눈다(입면에는 절단선이 없다)."""
    cfg = g.q(view_cfg, v)
    walls = g.q(scope_walls, v)
    if cfg is None or cfg[0] in ("site", "elev") or not (any(g.q(is_nonbearing, w) for w in walls) or g.q(foreign_nonbearing)):
        return g.q(view_lines, v)
    fnb = g.q(foreign_nonbearing)   # 외래 비내력벽(2차): 없으면 빈 값
    fn = plan_wall_lines if cfg[0] == "plan" else vwall_lines
    segs = []
    for w in walls:
        ws = g.q(fn, v, w)
        if g.q(is_nonbearing, w):
            ws = tuple(("cutn",) + ln[1:] if ln[0] == "cut" else ln for ln in ws)
        segs.extend(ws)
        if cfg[0] == "plan":
            segs.extend(g.q(plan_fire_lines, v, w))
            segs.extend(g.q(plan_wall_glazing, v, w))
    for e in g.q(scope_fams, v):
        segs.extend(g.q(fam_lines, v, e))
    for sl in g.q(scope_slabs, v):
        segs.extend(g.q(slab_lines, v, sl))
    for st in g.q(scope_stairs, v):
        segs.extend(g.q(stair_lines, v, st))
    for rl in g.q(scope_railings, v):
        segs.extend(g.q(railing_lines, v, rl))
    for ev in g.q(scope_elevators, v):
        segs.extend(g.q(elevator_lines, v, ev))
    for rf in g.q(scope_roofs, v):
        segs.extend(g.q(roof_lines, v, rf))
    for so in g.q(scope_solids, v):   # 일반 솔리드(18단계): 없으면 선분은 이전과 같다
        segs.extend(g.q(solid_lines, v, so))
    for fe in g.q(scope_foreign, v):   # 외래 IFC 요소: 외래 벽(IfcWall)의 LoadBearing 이 거짓이면 절단선을 cutn 으로(2차)
        fl = g.q(foreign_lines, v, fe)
        if fe in fnb:
            fl = tuple(("cutn",) + ln[1:] if ln[0] == "cut" else ln for ln in fl)
        segs.extend(fl)
    if cfg[0] == "elev":
        segs.extend(g.q(site_sign_lines, v))
    elif cfg[0] == "sec":
        segs.extend(g.q(site_section_lines, v))
    segs.extend(g.q(view_grid_lines, v))   # 통심선(19단계): 없으면 선분은 이전과 같다
    if len(cfg) == 6:   # 반사 천장 평면의 천장선·면적 평면의 면적 경계·해치(W1 1-C 3차)
        from dobim_core import viewplans as VP
        segs.extend(g.q(VP.mode_lines, v))
    return _crop_if(g, v, merge_lines(segs))


def view_fills(g, v):
    """절단 단면의 채움 다각형(평면: 벽 절단 사각형, 단면: 절단 사각형). 입면은 없음."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site":
        return ()
    out = []
    for w in g.q(scope_walls, v):
        if cfg[0] == "plan":
            out.extend(g.q(plan_pieces, v, w)[0])
        else:
            out.extend(g.q(vcut_rects, v, w))
    for e in g.q(scope_fams, v):
        if cfg[0] == "plan":
            out.extend(g.q(plan_fam_parts, v, e)[1])
        else:
            out.extend(g.q(vfam_cut_rects, v, e))
    for sl in g.q(scope_slabs, v):
        if cfg[0] == "plan":
            out.extend(g.q(plan_slab_parts, v, sl)[1])
        else:
            out.extend(g.q(slab_cut_rects, v, sl))
    for st in g.q(scope_stairs, v):
        if cfg[0] != "plan":
            out.extend(g.q(stair_cut_rects, v, st))
    if cfg[0] != "plan":
        for rf in g.q(scope_roofs, v):
            out.extend(g.q(roof_cut_rects, v, rf))
    for fe in g.q(scope_foreign, v):   # 외래 IFC 요소의 절단면 채움(2차): heavy 범주만. 외래 요소가 없으면 채움은 이전과 같다
        out.extend(g.q(foreign_fills, v, fe))
    res = tuple(tuple((round(p[0], 3), round(p[1], 3)) for p in poly) for poly in out)
    crop = g.q(view_opts, v).get("crop")
    return res if crop is None else _crop_polys(res, crop)


def view_summary(lines):
    by = {}
    for ln in lines:
        by[ln[0]] = by.get(ln[0], 0) + 1
    if lines:
        xs = [c for ln in lines for c in (ln[1], ln[3])]
        ys = [c for ln in lines for c in (ln[2], ln[4])]
        bbox = [min(xs), min(ys), max(xs), max(ys)]
    else:
        bbox = None
    digest = hashlib.sha256(json.dumps(lines, separators=(",", ":")).encode("ascii")).hexdigest()[:16]
    return {"segments": len(lines), "by_class": dict(sorted(by.items())), "bbox": bbox, "digest": digest}


def view_cache_state(g, vids):
    """뷰마다 선분 캐시의 상태: "fresh"(최신), "stale"(입력이 바뀌어 다시 계산 필요), "new"(아직 한 번도 안 만듦)."""
    out = {}
    for v in vids:
        node = g.nodes.get((view_lines, v))
        out[v] = "new" if node is None else ("fresh" if node.state == CLEAN else "stale")
    return out


def views_depending_on(g, input_keys, vids, include_not_fresh=True):
    """입력 노드들에서 역의존을 따라가 도달하는 뷰 선분 노드의 뷰 id(살아 있는 뷰 vids 안에서만).
    보수적으로 더 포함하는 뷰 (놓치는 것보다 많이 알리는 쪽이 안전하다):
      - 아직 계산한 적 없는 뷰(캐시 없음): 읽은 입력을 모른다. 항상 포함.
      - 다시 계산하지 않아 낡은(stale) 뷰: 의존 목록이 마지막 계산 때 것이라, 그 뒤 새로 읽게 될 입력(새 벽 등)을 모른다.
        include_not_fresh=False 면 뺀다(시험이 키 따라가기만 따로 검사하려고 쓴다)."""
    live = set(vids)
    seen, todo = set(), []
    for k in input_keys:
        n = g.nodes.get(k)
        if n is not None:
            todo.append(n)
    hit = set()
    while todo:
        n = todo.pop()
        if id(n) in seen:
            continue
        seen.add(id(n))
        if n.key[0] is view_lines:
            hit.add(n.key[1])
        todo.extend(n.rdeps)
    state = view_cache_state(g, vids)
    extra = {v for v, s in state.items() if s == "new" or (include_not_fresh and s != "fresh")}
    return sorted((hit | extra) & live)


# 뷰 하나에 딸린 질의 노드의 함수들 (첫 인자가 뷰 id). 뷰를 지우면 이 노드들을 그래프에서 치운다.
VIEW_SCOPED = frozenset({view_cfg, scope_walls, view_omitted, plan_pieces, plan_bbox, plan_nbr_index, plan_nbrs, vnbr_index, plan_wall_lines, plan_detail_lines, view_detail_lines, vpieces, vbbox, vnbrs,
                         vwall_detail, slab_detail_lines, space_ceiling_lines, vwin_detail, view_lines_hier,
                         vcut_rects, _vcut_top, vedges, vwall_lines, _bridges, _wall_occ_boxes, vnbrs_front, _other_occ_boxes, view_lines, view_fills,
                         scope_fams, plan_fam_parts, _fam_vpieces, vfam_bbox, vfam_cut_rects, vfam_edges, vfam_nbr_pieces, fam_lines,
                         scope_solids, _solid_vpieces, solid_lines, _occ_index, plan_wall_glazing,
                         vfam_hosted_edges, scope_slabs, plan_slab_parts, _slab_vpieces, slab_vbbox, slab_cut_rects, slab_vedges, slab_nbr_walls, slab_lines,
                         scope_stairs, scope_railings, railing_lines, scope_elevators, elevator_lines, plan_elevator_labels, plan_roof_marks, scope_roofs, _roof_vpieces, _roof_solid_vpieces, roof_cut_rects, roof_plan_parts, roof_lines, plan_stair_parts, _stair_vpieces, stair_vbbox, stair_cut_rects, stair_vedges, stair_nbr_walls, stair_lines,
                         scope_spaces, plan_space_labels, view_labels, site_lines, site_labels, site_dims, _elev_signs, site_sign_lines, site_sign_labels, _site_sec, _gl_z, site_section_lines, site_section_labels, vwall_hidden, view_hidden_lines, plan_fire_lines, plan_fire_labels, elev_finish, view_opts, view_dims, view_dim_labels,
                         grid_marks, view_grid_lines, view_grid_labels})


def forget_views(g, vids) -> int:
    """지워진 뷰의 질의 노드(선분·조각·경계 상자 등)를 그래프에서 치운다. 치운 노드 수를 돌려준다.
    뷰 id 는 다른 요소와 겹치지 않으므로 (함수, 뷰 id, ...) 노드만 정확히 골라낸다."""
    vids = set(vids)
    if not vids:
        return 0
    return g.drop([k for k in g.nodes if k[0] in VIEW_SCOPED and k[1] in vids])


# ================================================================ 외래 IFC 요소 (1단계 외래 도면 경로, 설계: elements-design.md 29절)
# 의도(DoBIM_Intent)가 없는 외래 요소는 IFC 형상(foreign_geom: ifcopenshell.geom 삼각형 메시)을 잘라·투영해 그린다. 의도가 있는 저작 요소는 기존 경로 그대로다.
#   절단선: 평면은 절단 높이 수평면, 단면은 절단면으로 메시를 자른 선. 등급은 범주(foreign_geom.CATEGORY)를 따른다: heavy=cut, medium=symbol, light=proj.
#   투영선: 메시의 날카로운 모서리(이웃 면이 5도 넘게 꺾이거나 경계) 중 보이는 범위(평면: 절단 높이~보이는 깊이, 단면: near~far)에 든 부분.
#   가림(외래끼리): 뷰 좌표 (a, b, d) 에서 모서리 점보다 앞(d 가 작은)에 있는 면(삼각형을 보이는 범위 앞쪽 경계로 자른 볼록 다각형)이 덮으면 숨는다.
#     절단면에서 잘린 몸체 안(절단 영역: 단면 선분 묶음의 짝홀 규칙)도 그 뒤를 가린다(잘린 몸체 뚜껑).
#   가림(저작 요소가 외래 선을): 단면·입면에서 기존 가림 조각(_occ_index)이 가린다. 외래 요소가 저작 요소 선을 가리지는 않는다(혼합 모델의 알려진 한계).
#   외래 요소가 없으면 모든 함수가 빈 값이라 기존 도면은 바이트 그대로다.
FOREIGN_CUT_CLS = {"heavy": "cut", "medium": "symbol", "light": "proj"}
F_TOL = 1.0       # 가림 깊이 허용(mm): 같은 면에 놓인 모서리가 자기 면에 가려지지 않게
F_BIN = 2000.0    # 가림 면 색인의 a 방향 칸 폭(mm)


def _raw_foreign(g):
    """외래 종류 요소 전체(형상 유무 무관)."""
    out = []
    for b in range(Q.NB):
        out.extend(Q.bucket_ids(g, "foreign", b))
    return tuple(sorted(out))


def foreign_info(g, e):
    """(범주, 꼭짓점(절대, mm), 면, 모서리, 면 법선, 경계 상자) 또는 None. 형상이 있고 그리는 종류인 외래 요소만(층·공간·개구부·형상 없음은 None)."""
    row = Q.el(g, e)
    if row is None or row["kind"] != "foreign":
        return None
    fg = Q.fgeom(g, e)
    if fg is None or fg[0] != "mesh":
        return None
    _, cat, verts, tris = fg
    faces = tuple((t,) for t in tris)
    edges, nrm = SO.feature_edges(verts, faces)
    return (cat, verts, faces, edges, nrm, SO.mesh_bbox(verts))


def all_foreign(g):
    return tuple(e for e in g.q(_raw_foreign) if g.q(foreign_info, e) is not None)


def _f_range(cfg):
    """보이는 깊이 범위 (d0, d1): 평면은 절단 높이에서 아래로 보이는 깊이, 단면은 near~far, 입면은 제한 없음."""
    return (0.0, cfg[3]) if cfg[0] == "plan" else (cfg[7], cfg[8])


def _f_pt(cfg, p):
    """세계 점 -> 뷰 좌표 (a, b, d). 평면 (x, y, 절단 z - z), 단면·입면 (s, z, w). d 는 보는 사람에게서 멀수록 크다."""
    if cfg[0] == "plan":
        return (p[0], p[1], cfg[2] - p[2])
    s, w = _sw(cfg, p)
    return (s, p[2], w)


def scope_foreign(g, v):
    """뷰가 그리는 외래 요소. 평면은 높이 범위가 (절단 높이 - 보이는 깊이, 절단 높이) 에 걸치는 것(레벨 소속과 무관), 단면은 깊이 범위에 걸치는 것, 입면은 전부."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] == "site" or (cfg[0] == "plan" and (cfg[1] is None or is_rcp(cfg))):   # 반사 천장 평면(W1 1-C 3차)은 아직 그리지 않는다
        return ()
    out = []
    d0, d1 = _f_range(cfg)
    for e in g.q(all_foreign):
        bb = g.q(foreign_info, e)[5]
        if cfg[0] == "plan":
            if bb[2] <= cfg[2] + EPS and bb[5] >= cfg[2] - cfg[3] - EPS:
                out.append(e)
            continue
        ws = [_sw(cfg, (x, y))[1] for x in (bb[0], bb[3]) for y in (bb[1], bb[4])]
        if max(ws) >= d0 - EPS and min(ws) <= d1 + EPS:
            out.append(e)
    return tuple(out)


def _f_cut(g, v, e):
    """외래 요소를 절단면으로 자른 선분 ((a0, b0, a1, b1), ...) (뷰 좌표). 입면·잘라내지 않는 뷰는 빈 값."""
    cfg = g.q(view_cfg, v)
    info = g.q(foreign_info, e)
    if cfg is None or info is None or cfg[0] == "site" or (cfg[0] != "plan" and not cfg[9]):
        return ()
    _cat, verts, faces, _edges, nrm, _bb = info
    if cfg[0] == "plan":
        axis, c = (0.0, 0.0, 1.0), cfg[2]
    else:
        axis = (cfg[3], cfg[4], 0.0)
        c = cfg[1] * cfg[3] + cfg[2] * cfg[4] + cfg[7]
    out = []
    for p, q in SO.plane_section(verts, faces, nrm, axis, c):
        a, b = _f_pt(cfg, p), _f_pt(cfg, q)
        if sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) > SEG_MIN:
            out.append((a[0], a[1], b[0], b[1]))
    return tuple(out)


def _f_occ(g, v):
    """뷰 하나의 외래 가림 자료 (면 조각들, 조각 상자들, {칸: (번호, ...)}, 절단 영역들).
    면 조각 = (볼록 다각형((a, b), ... 반시계), (pa, pb, pc): 그 면의 깊이 d = pa*a + pb*b + pc). 상자 = (a0, a1, b0, b1, 가장 앞 d).
    절단 영역 = (상자 (a0, a1, b0, b1), 단면 선분들): 요소 하나의 절단면 단면이다. 안쪽 점(짝홀 규칙)은 잘린 몸체 속이라 그 뒤가 보이지 않는다."""
    cfg = g.q(view_cfg, v)
    d0, d1 = _f_range(cfg)
    polys, boxes, bins, regions = [], [], {}, []
    for e in g.q(scope_foreign, v):
        _cat, verts, faces, _edges, _nrm, _bb = g.q(foreign_info, e)
        P = [_f_pt(cfg, p) for p in verts]
        for (tri,) in faces:
            t = [P[i] for i in tri]
            ds = [q[2] for q in t]
            if min(ds) > d1 or max(ds) < d0:
                continue   # 보이는 범위 뒤(가릴 것이 없다) 또는 통째로 앞(평면: 절단 높이 위, 단면: 절단면 앞)
            ux, uy, ud = t[1][0] - t[0][0], t[1][1] - t[0][1], t[1][2] - t[0][2]
            vx, vy, vd = t[2][0] - t[0][0], t[2][1] - t[0][1], t[2][2] - t[0][2]
            na, nb, nd = uy * vd - ud * vy, ud * vx - ux * vd, ux * vy - uy * vx
            if abs(nd) < 1e-6:
                continue   # 보는 방향과 나란한 면: 넓이가 없어 아무것도 가리지 않는다
            plane = (-na / nd, -nb / nd, t[0][2] + (na * t[0][0] + nb * t[0][1]) / nd)
            poly = t
            if d0 > -BIG and min(ds) < d0:   # 앞쪽 경계(평면: 절단 높이, 단면: 절단면)로 잘라 앞부분을 버린다
                out = []
                for i in range(len(poly)):
                    a, b = poly[i], poly[(i + 1) % len(poly)]
                    if a[2] >= d0:
                        out.append(a)
                    if (a[2] >= d0) != (b[2] >= d0):
                        k = (d0 - a[2]) / (b[2] - a[2])
                        out.append((a[0] + k * (b[0] - a[0]), a[1] + k * (b[1] - a[1]), d0))
                poly = out
                if len(poly) < 3:
                    continue
            p2 = [(q[0], q[1]) for q in poly]
            ar = _area2(p2)
            if abs(ar) < 1e-6:
                continue
            if ar < 0:
                p2.reverse()
            i = len(polys)
            polys.append((tuple(p2), plane))
            a0, a1 = min(q[0] for q in p2), max(q[0] for q in p2)
            boxes.append((a0, a1, min(q[1] for q in p2), max(q[1] for q in p2), min(q[2] for q in poly)))
            for k in range(int(a0 // F_BIN), int(a1 // F_BIN) + 1):
                bins.setdefault(k, []).append(i)
        segs = g.q(_f_cut, v, e)
        if segs:
            regions.append(((min(min(s[0], s[2]) for s in segs), max(max(s[0], s[2]) for s in segs),
                             min(min(s[1], s[3]) for s in segs), max(max(s[1], s[3]) for s in segs)), segs))
    return (tuple(polys), tuple(boxes), {k: tuple(x) for k, x in bins.items()}, tuple(regions))


def _f_inside(segs, pa, pb) -> bool:
    """점 (pa, pb) 가 단면 선분 묶음 안인가(+a 방향 반직선 짝홀 규칙, 반열린 b 규칙)."""
    n = 0
    for a0, b0, a1, b1 in segs:
        if (b0 > pb) != (b1 > pb):
            x = a0 + (pb - b0) / (b1 - b0) * (a1 - a0)
            if x > pa:
                n += 1
    return n % 2 == 1


def _f_hidden(occ, A, B) -> list:
    """뷰 좌표 선분 A-B((a, b, d) 두 점) 에서 외래 면·절단 영역에 가려지는 매개변수 구간들 [(t0, t1), ...]."""
    polys, boxes, bins, regions = occ
    da, db = B[0] - A[0], B[1] - A[1]
    dd = B[2] - A[2]
    lo_a, hi_a = min(A[0], B[0]), max(A[0], B[0])
    lo_b, hi_b = min(A[1], B[1]), max(A[1], B[1])
    dmax = max(A[2], B[2])
    out = []
    seen = set()
    for k in range(int(lo_a // F_BIN), int(hi_a // F_BIN) + 1):
        for i in bins.get(k, ()):
            if i in seen:
                continue
            seen.add(i)
            bx = boxes[i]
            if bx[0] > hi_a + EPS or bx[1] < lo_a - EPS or bx[2] > hi_b + EPS or bx[3] < lo_b - EPS or bx[4] >= dmax - F_TOL:
                continue
            poly, (pa, pb, pc) = polys[i]
            t0, t1 = 0.0, 1.0
            n = len(poly)
            for j in range(n):
                p, q = poly[j], poly[(j + 1) % n]
                ex, ey = q[0] - p[0], q[1] - p[1]
                num = ex * (A[1] - p[1]) - ey * (A[0] - p[0])
                den = ex * db - ey * da
                if abs(den) < 1e-12:
                    if num < 0:
                        t0, t1 = 1.0, 0.0
                        break
                    continue
                t = -num / den
                if den > 0:
                    t0 = max(t0, t)
                else:
                    t1 = min(t1, t)
                if t1 - t0 <= 1e-9:
                    break
            if t1 - t0 <= 1e-9:
                continue
            # 모서리 깊이 - 면 깊이 - 허용: 양수면 면이 앞에 있다(선분 위에서 일차식)
            fa = (A[2] + t0 * dd) - (pa * (A[0] + t0 * da) + pb * (A[1] + t0 * db) + pc) - F_TOL
            fb = (A[2] + t1 * dd) - (pa * (A[0] + t1 * da) + pb * (A[1] + t1 * db) + pc) - F_TOL
            if fa > 0 and fb > 0:
                out.append((t0, t1))
            elif fa > 0 or fb > 0:
                tc = t0 + (t1 - t0) * fa / (fa - fb)
                out.append((t0, tc) if fa > 0 else (tc, t1))
    for (ra0, ra1, rb0, rb1), segs in regions:
        if ra0 > hi_a + EPS or ra1 < lo_a - EPS or rb0 > hi_b + EPS or rb1 < lo_b - EPS:
            continue
        ts = [0.0, 1.0]
        for a0, b0, a1, b1 in segs:   # 선분과 단면 선분의 교점
            ex, ey = a1 - a0, b1 - b0
            den = da * ey - db * ex
            if abs(den) < 1e-12:
                continue
            t = ((a0 - A[0]) * ey - (b0 - A[1]) * ex) / den
            u = ((a0 - A[0]) * db - (b0 - A[1]) * da) / den
            if 0.0 < t < 1.0 and -1e-9 <= u <= 1.0 + 1e-9:
                ts.append(t)
        ts.sort()
        for t0, t1 in zip(ts, ts[1:]):
            if t1 - t0 <= 1e-9:
                continue
            tm = (t0 + t1) / 2.0
            if A[2] + tm * dd > F_TOL and _f_inside(segs, A[0] + tm * da, A[1] + tm * db):
                out.append((t0, t1))
    return out


def _f_visible(hidden) -> list:
    """[0, 1] 에서 가려진 구간들을 뺀 보이는 구간들."""
    vis = [(0.0, 1.0)]
    for h0, h1 in sorted(hidden):
        nxt = []
        for a, b in vis:
            if h1 <= a or h0 >= b:
                nxt.append((a, b))
                continue
            if h0 > a:
                nxt.append((a, h0))
            if h1 < b:
                nxt.append((h1, b))
        vis = nxt
    return vis


def foreign_lines(g, v, e):
    """외래 요소 하나가 뷰에 기여하는 선분: 절단선(범주 등급)과 보이는 투영선(proj)."""
    cfg = g.q(view_cfg, v)
    info = g.q(foreign_info, e)
    if cfg is None or info is None or cfg[0] == "site":
        return ()
    cat, verts, _faces, edges, _nrm, _bb = info
    out = [(FOREIGN_CUT_CLS[cat],) + s for s in g.q(_f_cut, v, e)]
    d0, d1 = _f_range(cfg)
    occ = g.q(_f_occ, v)
    idx = g.q(_occ_index, v) if cfg[0] != "plan" else None
    P = [_f_pt(cfg, p) for p in verts]
    for ia, ib, _fl in edges:
        A, B = P[ia], P[ib]
        t0, t1 = 0.0, 1.0
        dd = B[2] - A[2]
        for bound, keep_ge in ((d0, True), (d1, False)):   # 보이는 깊이 범위로 자른다
            if abs(bound) >= BIG:
                continue
            da_ = A[2] - bound
            if abs(dd) < 1e-12:
                if (da_ < -EPS) if keep_ge else (da_ > EPS):
                    t0, t1 = 1.0, 0.0
                continue
            t = -da_ / dd
            if (dd > 0) == keep_ge:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
        if t1 - t0 <= 1e-9:
            continue
        A2 = (A[0] + t0 * (B[0] - A[0]), A[1] + t0 * (B[1] - A[1]), A[2] + t0 * dd)
        B2 = (A[0] + t1 * (B[0] - A[0]), A[1] + t1 * (B[1] - A[1]), A[2] + t1 * dd)

        def lerp(t, A2=A2, B2=B2):
            return (A2[0] + t * (B2[0] - A2[0]), A2[1] + t * (B2[1] - A2[1]))
        if sqrt((B2[0] - A2[0]) ** 2 + (B2[1] - A2[1]) ** 2) <= SEG_MIN:
            continue   # 보는 방향과 나란한 모서리(점으로 보인다)
        hid = _f_hidden(occ, A2, B2)
        if idx is not None and idx[0]:   # 저작 요소의 가림 조각(단면·입면): 기존 _vis 의 보이는 구간의 여집합을 더한다
            e0, e1 = (A2[0], A2[2], A2[1]), (B2[0], B2[2], B2[1])   # (s, w, z)
            near = _occ_near(idx, min(e0[0], e1[0]), max(e0[0], e1[0]), min(e0[2], e1[2]), max(e0[2], e1[2]), max(e0[1], e1[1]))
            if near:
                hid.extend(_f_complement(_vis(e0, e1, near)))
        for a, b in _f_visible(hid):
            p, q = lerp(a), lerp(b)
            if sqrt((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) > SEG_MIN:
                out.append(("proj", p[0], p[1], q[0], q[1]))
    return tuple(out)


def _f_complement(vis) -> list:
    """[0, 1] 에서 보이는 구간들(정렬됨)의 여집합 = 가려진 구간들."""
    out, last = [], 0.0
    for a, b in vis:
        if a > last:
            out.append((last, a))
        last = max(last, b)
    if last < 1.0:
        out.append((last, 1.0))
    return out


def foreign_omitted(g, ids) -> int:
    """ids 중 그리지 않은 외래 요소 수(형상이 없거나 그리지 않는 종류). 층·공간 구조·부분으로 이루어진 요소·개구부 등(foreign_geom 의 storey/spatial/aggregate/skip)은 세지 않는다."""
    n = 0
    for e in ids:
        r = Q.el(g, e)
        if r is not None and r["kind"] == "foreign" and g.q(foreign_info, e) is None:
            fg = Q.fgeom(g, e)
            if fg is None or fg[0] not in ("storey", "spatial", "space", "aggregate", "skip"):
                n += 1
    return n


# ---------------------------------------------------------------- 외래 도면 2차 (설계: elements-design.md 30절)
#   (1) 절단면 채움: heavy 범주의 절단 선분을 고리로 이어(끝점 0.01mm 반올림 기준) 짝홀 포함 관계로 바깥·구멍을 나누고,
#       구멍은 폭 0 다리(keyhole)로 바깥 고리에 이어 다각형 하나로 만든다(채움은 구멍 없는 다각형 목록이다).
#   (2) 비내력벽: 외래 벽(IfcWall 계열)의 Pset_WallCommon.LoadBearing 이 거짓이면 선 굵기 위계에서 절단선이 cutn 이다(저작 벽과 같은 규칙).
#   (3) 공간 실명: 외래 IfcSpace 의 실명(LongName, 없으면 Name)·실번호·바닥 면적을 바닥 무게중심에 쓴다(절단 높이가 공간 높이 범위 안인 평면만).
#   (4) 레벨 기준선: 외래 층(IfcBuildingStorey)의 이름·고도를 단면·입면 기준선에 더한다(commands._render_view).
F_WALLS = frozenset({"IfcWall", "IfcWallStandardCase", "IfcWallElementedCase"})
F_NOFILL_PLAN = frozenset({"IfcStair", "IfcStairFlight", "IfcRamp", "IfcRampFlight", "IfcRoof"})   # 저작 요소와 같이 평면에서는 채우지 않는다


def foreign_nonbearing(g):
    """LoadBearing 이 거짓인 외래 벽 id 들(형상 있는 것만). 없으면 빈 값."""
    return tuple(e for e in g.q(all_foreign) if Q.el(g, e).get("ifc_class") in F_WALLS and g.q(is_nonbearing, e))


def _f_pip(pt, poly) -> bool:
    """점이 다각형 안인가(짝홀, 반열린 규칙)."""
    x, y = pt
    n, inside = len(poly), False
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x0 + (y - y0) / (y1 - y0) * (x1 - x0) > x:
            inside = not inside
    return inside


def _f_loops(segs) -> list:
    """절단 선분들을 닫힌 고리들로 잇는다(끝점 0.01mm 반올림 기준). 닫히지 않는 사슬은 버린다.
    여러 몸체가 맞닿은 메시(층 구성 벽 등)는 맞닿은 면이 같은 선분을 두 번 만든다: 다른 선분의 끝점(T 꼭짓점)에서 선분을 쪼갠 뒤
    같은 선분이 짝수 번 나오면 지우고 홀수 번이면 하나 남긴다(경계의 mod 2 합 = 맞닿은 몸체 합집합의 경계)."""
    raw = [((round(a0, 2), round(b0, 2)), (round(a1, 2), round(b1, 2))) for a0, b0, a1, b1 in segs]
    raw = [e for e in raw if e[0] != e[1]]
    bins: dict = {}
    for p in sorted({p for e in raw for p in e}):
        bins.setdefault((int(p[0] // F_BIN), int(p[1] // F_BIN)), []).append(p)
    count: dict = {}
    for p, q in raw:
        dx, dy = q[0] - p[0], q[1] - p[1]
        ll = dx * dx + dy * dy
        ln = sqrt(ll)
        ts = []
        for i in range(int(min(p[0], q[0]) // F_BIN), int(max(p[0], q[0]) // F_BIN) + 1):
            for j in range(int(min(p[1], q[1]) // F_BIN), int(max(p[1], q[1]) // F_BIN) + 1):
                for r in bins.get((i, j), ()):
                    t = ((r[0] - p[0]) * dx + (r[1] - p[1]) * dy) / ll
                    if 1e-9 < t < 1.0 - 1e-9 and abs(dx * (r[1] - p[1]) - dy * (r[0] - p[0])) / ln <= 0.02:
                        ts.append((t, r))
        pts = [p] + [r for _t, r in sorted(ts)] + [q]
        for a, b in zip(pts, pts[1:]):
            if a != b:
                k = (a, b) if a < b else (b, a)
                count[k] = count.get(k, 0) + 1
    edges = sorted(k for k, n in count.items() if n % 2)
    adj: dict = {}
    for i, (p, q) in enumerate(edges):
        adj.setdefault(p, []).append(i)
        adj.setdefault(q, []).append(i)
    used = [False] * len(edges)
    loops = []
    for i, (start, cur) in enumerate(edges):
        if used[i]:
            continue
        used[i] = True
        loop = [start]
        while cur != start:
            loop.append(cur)
            nxt = next((j for j in adj[cur] if not used[j]), None)
            if nxt is None:
                break
            used[nxt] = True
            p, q = edges[nxt]
            cur = q if p == cur else p
        if cur == start and len(loop) >= 3 and abs(_area2(loop)) > 1e-6:
            loops.append(loop)
    return loops


def _f_bridge(outer, hole):
    """구멍(시계 방향)을 바깥 고리(반시계)에 폭 0 다리로 잇는다: 구멍의 가장 오른쪽 점에서 +a 방향 반직선이 처음 만나는 변에 다리를 놓는다."""
    h = max(range(len(hole)), key=lambda i: (hole[i][0], hole[i][1], -i))
    hx, hy = hole[h]
    best = None
    n = len(outer)
    for k in range(n):
        (x0, y0), (x1, y1) = outer[k], outer[(k + 1) % n]
        if (y0 > hy) != (y1 > hy):
            x = x0 + (hy - y0) / (y1 - y0) * (x1 - x0)
            if x >= hx and (best is None or x < best[0]):
                best = (x, k)
    if best is None:
        return None
    x, k = best
    ip = (x, hy)
    ring = hole[h:] + hole[:h]
    return outer[:k + 1] + [ip] + ring + [hole[h], ip] + outer[k + 1:]


def _f_fill_polys(segs) -> tuple:
    """절단 선분 묶음 -> 채움 다각형들(구멍은 다리로 이은 다각형 하나)."""
    loops = _f_loops(segs)
    if not loops:
        return ()
    areas = [abs(_area2(lp)) for lp in loops]
    inside = [[j for j in range(len(loops)) if j != i and areas[j] > areas[i] and _f_pip(loops[i][0], loops[j])] for i in range(len(loops))]
    depth = [len(x) for x in inside]
    out = []
    for i, lp in enumerate(loops):
        if depth[i] % 2:
            continue
        poly = lp if _area2(lp) > 0 else lp[::-1]
        holes = [j for j in range(len(loops)) if depth[j] == depth[i] + 1 and i in inside[j]]
        holes.sort(key=lambda j: (-max(p[0] for p in loops[j]), j))
        for j in holes:
            hl = loops[j] if _area2(loops[j]) < 0 else loops[j][::-1]
            merged = _f_bridge(poly, hl)
            if merged is not None:
                poly = merged
        out.append(tuple(poly))
    return tuple(out)


def foreign_fills(g, v, e):
    """외래 요소 하나의 절단면 채움 다각형(heavy 범주만, 평면에서는 계단·경사로·지붕 제외)."""
    cfg = g.q(view_cfg, v)
    info = g.q(foreign_info, e)
    if cfg is None or info is None or info[0] != "heavy":
        return ()
    if cfg[0] == "plan" and Q.el(g, e).get("ifc_class") in F_NOFILL_PLAN:
        return ()
    return _f_fill_polys(g.q(_f_cut, v, e))


def _raw_foreign_spaces(g):
    """실명 문자를 놓을 수 있는 외래 공간(foreign_geom 값이 "space")."""
    return tuple(e for e in g.q(_raw_foreign) if (Q.fgeom(g, e) or ("",))[0] == "space")


def scope_foreign_spaces(g, v):
    """평면 뷰가 실명을 쓰는 외래 공간: 절단 높이가 공간의 높이 범위 안인 것."""
    cfg = g.q(view_cfg, v)
    if cfg is None or cfg[0] != "plan" or cfg[1] is None:
        return ()
    out = []
    for e in g.q(_raw_foreign_spaces):
        z0, z1 = Q.fgeom(g, e)[5]
        if z0 - EPS <= cfg[2] <= z1 + EPS:
            out.append(e)
    return tuple(out)


def foreign_space_labels(g, v, e):
    """외래 공간 하나: 실명(위)과 "실번호 면적"(아래) 문자."""
    cfg = g.q(view_cfg, v)
    fg = Q.fgeom(g, e)
    if cfg is None or cfg[0] != "plan" or fg is None or fg[0] != "space":
        return ()
    _, title, number, (cx, cy), area, _z = fg
    sc = cfg[4]
    sub = f"{area / 1.0e6:.2f}㎡" if number is None else f"{number}  {area / 1.0e6:.2f}㎡"
    return ((round(cx, 3), round(cy + 0.9 * sc, 3), title, LABEL_NAME_H, "middle"),
            (round(cx, 3), round(cy - 2.0 * sc, 3), sub, LABEL_SUB_H, "middle"))


def foreign_datums(g):
    """외래 층(IfcBuildingStorey)의 기준선 ((이름, 고도 mm), ...). 고도는 IFC 배치(foreign_geom "storey"). 없으면 빈 값."""
    out = []
    for e in g.q(_raw_foreign):
        row = Q.el(g, e)
        fg = Q.fgeom(g, e)
        if row.get("ifc_class") == "IfcBuildingStorey" and fg is not None and fg[0] == "storey":
            out.append((str(row["params"].get("name") or e), fg[1]))
    return tuple(sorted(out, key=lambda t: (t[1], t[0])))


VIEW_SCOPED = VIEW_SCOPED | frozenset({scope_foreign, _f_cut, _f_occ, foreign_lines,
                                       foreign_fills, scope_foreign_spaces, foreign_space_labels})   # 외래 도면 경로의 뷰별 질의(forget_views 가 치운다)
