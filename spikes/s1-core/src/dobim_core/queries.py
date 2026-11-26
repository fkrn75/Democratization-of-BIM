# SPDX-License-Identifier: MPL-2.0
"""순수 파생 질의. 시각·난수·파일 접근 금지 (ADR-0006 불변식, 시험이 AST로 검사한다).

단위는 mm. math.hypot, sin, cos 처럼 플랫폼 libm 에 달린 함수를 쓰지 않는다.
sqrt 와 사칙연산은 IEEE 754 로 결정적이다.
"""
from __future__ import annotations

from math import sqrt

from dobim_core import arcgeom as AG
from dobim_core import beams as BM
from dobim_core import curtain as CW
from dobim_core.family import spec as FS
from dobim_core import solids as SO
from dobim_core import wallcut as WC
from dobim_core import walltop as WT

JOIN_MAX_EXT = 1000.0   # 조인 지점이 벽 끝에서 이 거리 안일 때만 조인이 성립
MIN_TRIMMED = 1.0       # 조인 후 벽 길이 하한. 미달이면 조인을 무시하고 경고 상태로 둔다
MIN_SIN = 0.2           # 조인 각이 너무 얕으면(sin 이 이보다 작으면) L 조인 모서리 연장을 하지 않는다
CORNER_EXTEND = True    # L 조인 모서리를 코어 형상으로 닫는다(id 가 작은 벽이 상대 벽 반 두께만큼 더 나간다). 도면과 IFC 가 같은 형상을 쓴다(BIM 지침 B-02)
PARALLEL_EPS = 1e-9


# 요소 종류의 정본은 kinds.py 다. NON_IFC_KINDS(신원·속성셋·재료가 없는 뷰·주석), NB, bucket_of 는 거기서 가져온다
from dobim_core.kinds import NB, NON_IFC_KINDS, bucket_of, layers_label  # noqa: E402,F401


def el(g, eid):
    return g.input(("el", eid))


def bucket_ids(g, kind, b):
    return g.input(("bucket", kind, b))


def count(g, kind):
    return g.input(("count", kind))


def rel_out(g, kind, src):
    return g.input(("rel_out", kind, src))


def rel_in(g, kind, dst):
    return g.input(("rel_in", kind, dst))


def xcut_finsts(g):
    """함께 자르는 벽(also_cuts)이 있는 패밀리 인스턴스 id(25단계). 그런 인스턴스가 없으면 빈 값이고 벽 형상은 이전과 같다."""
    return g.input(("xcut_finsts",))


def fgeom(g, e):
    """외래 요소의 IFC 형상 입력(foreign_geom.load): ("storey", 고도) / ("mesh", 범주, 꼭짓점, 삼각형) / None."""
    return g.input(("fgeom", e))


def fopen(g, e):
    """외래 문·창의 평면 기호 입력(foreign_geom.opening, 외래 도면 3차): ("door", ...) / ("window", ...) / None."""
    return g.input(("fopen", e))


def plan_level_ok(row) -> bool:
    """평면 뷰의 레벨이 될 수 있는 요소 행인가: 레벨, 또는 외래 IFC 층(IfcBuildingStorey, 고도는 IFC 배치에서 읽는다)."""
    return row is not None and (row["kind"] == "level" or (row["kind"] == "foreign" and row.get("ifc_class") == "IfcBuildingStorey"))


def tied_walls(g):
    """상단 레벨로 구속된 벽 id(24단계). 다른 벽을 편집해도 바뀌지 않는 입력이라 평면이 모든 벽에 의존하지 않는다."""
    return g.input(("tied_walls",))


def arc_walls(g):
    """곡선(원호) 벽 id 집합(W2 ARC-04). 비어 있으면 조인·도면 계산은 직선 벽 경로 그대로다(기존 모델의 값이 바이트까지 같다)."""
    return g.input(("arc_walls",))


def psets(g, eid):
    return g.input(("pset", eid))


def material(g, eid):
    """재료 이름표. 층 구성이 있는 벽·슬래브는 층 구성이 재료를 정한다(가장 두꺼운 층의 재료): 층 구성은 요소 파라미터(의도)라 이 값도 그 입력 하나에서 나온다."""
    row = g.input(("el", eid))
    if row is not None and row["kind"] in ("wall", "slab", "roof") and "layers" in row["params"]:   # 지붕 층 구성: W2 여덟 번째 PR
        return layers_label(row["params"]["layers"])
    if row is not None and row["kind"] == "beam" and "material" in row["params"]:   # 보 유형의 구조 재료(W2 열여덟 번째 PR)가 재료를 정한다
        return row["params"]["material"]
    return g.input(("mat", eid))


# ---------------------------------------------------------------- 벽
def wall_frame(g, w):
    x1, y1, x2, y2 = el(g, w)["params"]["axis"]
    dx, dy = x2 - x1, y2 - y1
    length = sqrt(dx * dx + dy * dy)
    return (x1, y1, dx / length, dy / length, length)


def wall_arc(g, w):
    """곡선(원호) 벽 축의 계산값 arcgeom.info (cx, cy, r, a0, sg, sw). 직선 벽은 None.
    wall_frame 은 곡선 벽에서도 시작점·현 방향·현 길이 틀(IFC 배치·직선 전용 경로의 폴백)이고, 축 위 거리 u 는 호를 따라 잰다."""
    p = el(g, w)["params"]
    return AG.info(p["axis"], p["arc"]) if "arc" in p else None


def axis_u(g, w, p):
    """세계 점 p 를 벽 축 지역 u 로(직선 벽: 축선 투영, 곡선 벽: 호 위 거리)."""
    ai = g.q(wall_arc, w)
    if ai is not None:
        return AG.u_of(ai, p)
    x, y, c, s, _ = g.q(wall_frame, w)
    return (p[0] - x) * c + (p[1] - y) * s


def axis_len(g, w):
    """벽 축 길이(곡선 벽은 호 길이)."""
    ai = g.q(wall_arc, w)
    return AG.length(ai) if ai is not None else g.q(wall_frame, w)[4]


def axis_dir(g, w, u):
    """축 지역 u 에서의 단위 진행 방향."""
    ai = g.q(wall_arc, w)
    if ai is not None:
        return AG.tangent(ai, u)
    _, _, c, s, _ = g.q(wall_frame, w)
    return (c, s)


def axis_pt(g, w, u, v=0.0):
    """벽 지역 (u, v) -> 세계 (x, y)."""
    ai = g.q(wall_arc, w)
    if ai is not None:
        return AG.point(ai, u, v)
    x, y, c, s, _ = g.q(wall_frame, w)
    return (x + u * c - v * s, y + u * s + v * c)


def wall_level(g, w):
    return rel_out(g, "on_level", w)[0]


def wall_joins(g, w):
    """조인 상대 목록. joins 는 src<dst 로 저장되므로 양쪽을 합친다."""
    return tuple(sorted(rel_out(g, "joins", w) + rel_in(g, "joins", w)))


def _ends(g, w):
    p = el(g, w)["params"]["axis"]
    return ((p[0], p[1]), (p[2], p[3]))


def _arc_join_point(g, a, b):
    """곡선 벽이 낀 조인의 교점(W2 ARC-04): 호의 원과 상대 축(직선 또는 원)의 교점 중 두 벽의 끝에 가장 가까운 것. 만나지 않으면 None."""
    aa, ab = g.q(wall_arc, a), g.q(wall_arc, b)
    if aa is not None and ab is not None:
        cand = AG.circle_hits(aa, ab)
    else:
        arc, line = (aa, b) if aa is not None else (ab, a)
        x, y, c, s, _ = g.q(wall_frame, line)
        cand = AG.line_hits(arc, x, y, c, s)
    if not cand:
        return None

    def cost(p):
        return sum(min((p[0] - e[0]) ** 2 + (p[1] - e[1]) ** 2 for e in _ends(g, w)) for w in (a, b))
    return min(cand, key=cost)


def join_point(g, a, b):
    """두 벽 축선의 교점. 벽 축선에서 계산하므로 벽끼리 순환하지 않는다 (ADR-0006).
    곡선 벽(W2 ARC-04)이 끼면 원과 축의 교점(_arc_join_point). 곡선 벽이 없는 모델은 아래 직선 교점 그대로다."""
    arcs = arc_walls(g)
    if arcs and (a in arcs or b in arcs):
        return _arc_join_point(g, a, b)
    x1, y1, cx1, sy1, _ = g.q(wall_frame, a)
    x2, y2, cx2, sy2, _ = g.q(wall_frame, b)
    det = cx1 * sy2 - sy1 * cx2
    if -PARALLEL_EPS < det < PARALLEL_EPS:
        return None
    t = ((x2 - x1) * sy2 - (y2 - y1) * cx2) / det
    return (x1 + t * cx1, y1 + t * sy1)


def tee_end_at(frame, tee, p):
    """T 조인(W1 1-F 3차): 점 p 가 벽(frame = wall_frame 값, tee = params 의 tee)의 줄기 끝인가. 끝은 p 에 가까운 쪽, 조인 거리 안이어야 한다.
    맞으면 "start"|"end", 아니면 None. commands._trim_raw 가 같은 규칙을 원시 축선에서 쓴다."""
    if not tee:
        return None
    x, y, c, s, length = frame
    t = (p[0] - x) * c + (p[1] - y) * s
    end = "start" if abs(t) <= abs(t - length) else "end"
    return end if end in tee and min(abs(t), abs(t - length)) <= JOIN_MAX_EXT else None


def _tee_of(g, w):
    return el(g, w)["params"].get("tee")


def _stem_side(g, w, tee, p):
    """tee_end_at 의 곡선 벽(W2 ARC-04)도 받는 판: 곡선 줄기는 끝을 호 위 거리로 고른다. 직선 벽은 tee_end_at 그대로다."""
    if not tee:
        return None
    if g.q(wall_arc, w) is None:
        return tee_end_at(g.q(wall_frame, w), tee, p)
    t, length = g.q(axis_u, w, p), g.q(axis_len, w)
    end = "start" if abs(t) <= abs(t - length) else "end"
    return end if end in tee and min(abs(t), abs(t - length)) <= JOIN_MAX_EXT else None


def _is_stem_at(g, o, p):
    """상대 벽 o 가 점 p 에서 T 조인 줄기인가(곡선 줄기는 W2 세 번째 PR 부터: _stem_side)."""
    tee = _tee_of(g, o)
    return bool(tee) and _stem_side(g, o, tee, p) is not None



def wall_trim(g, w):
    """(시작, 끝, 상태): 벽 지역 x 좌표. 원점은 축선 시작점이다.
    T 조인(W1 1-F 3차): 조인 지점이 상대 벽의 줄기 끝(tee)이면 이 벽은 몸통이라 자르지 않는다.
    곡선 벽(W2 ARC-04)은 u 를 호를 따라 잰다(_arc_trim). 곡선 벽이 없는 모델은 아래 코드 그대로다."""
    arcs = arc_walls(g)
    if arcs and w in arcs:
        return _arc_trim(g, w)
    x, y, c, s, length = g.q(wall_frame, w)
    start, end = 0.0, length
    for other in g.q(wall_joins, w):
        p = g.q(join_point, min(w, other), max(w, other))
        if p is None:
            continue
        if _is_stem_at(g, other, p):
            continue
        t = (p[0] - x) * c + (p[1] - y) * s
        if abs(t) <= abs(t - length):
            if abs(t) <= JOIN_MAX_EXT:
                start = t
        elif abs(t - length) <= JOIN_MAX_EXT:
            end = t
    if end - start < MIN_TRIMMED:
        return (0.0, length, "degenerate_join")
    return (start, end, "ok")


def _arc_trim(g, w):
    """곡선 벽의 wall_trim: 조인 교점의 u 를 호 위 거리로 잰다(규칙은 직선 벽과 같다)."""
    length = g.q(axis_len, w)
    start, end = 0.0, length
    for other in g.q(wall_joins, w):
        p = g.q(join_point, min(w, other), max(w, other))
        if p is None or _is_stem_at(g, other, p):
            continue
        t = g.q(axis_u, w, p)
        if abs(t) <= abs(t - length):
            if abs(t) <= JOIN_MAX_EXT:
                start = t
        elif abs(t - length) <= JOIN_MAX_EXT:
            end = t
    if end - start < MIN_TRIMMED:
        return (0.0, length, "degenerate_join")
    return (start, end, "ok")


def _arc_extent(g, w):
    """곡선 벽이 낀 wall_extent(W2 ARC-04): 직선 벽 규칙과 같되 u·방향을 axis_u·axis_dir 로 잰다(조인 지점의 접선 방향으로 사인을 잰다).
    T 조인 줄기(직선·곡선)는 몸통 반 두께/|sin| 만큼 물러난다(조인 지점 접선 기준 근사, 곡선 줄기는 호를 따라 물러난다)."""
    start, end, status = g.q(wall_trim, w)
    if not CORNER_EXTEND or status != "ok":
        return (start, end)
    length = g.q(axis_len, w)
    tee = _tee_of(g, w)
    if tee:   # 줄기가 곡선 몸통에 닿거나, 곡선 줄기가 몸통에 닿는다
        for o in g.q(wall_joins, w):
            pt = g.q(join_point, min(w, o), max(w, o))
            if pt is None or _is_stem_at(g, o, pt):
                continue
            side = _stem_side(g, w, tee, pt)
            if side is None:
                continue
            c, s = g.q(axis_dir, w, g.q(axis_u, w, pt))
            oc, os_ = g.q(axis_dir, o, g.q(axis_u, o, pt))
            sin = c * os_ - s * oc
            if side is None or abs(sin) < MIN_SIN:
                continue
            ext = el(g, o)["params"]["thickness"] / 2.0 / abs(sin)
            if side == "start" and end - (start + ext) >= MIN_TRIMMED:
                start += ext
            elif side == "end" and (end - ext) - start >= MIN_TRIMMED:
                end -= ext
    for o in g.q(wall_joins, w):
        if not w < o:
            continue
        pt = g.q(join_point, w, o)
        if pt is None:
            continue
        if tee and _stem_side(g, w, tee, pt) or _is_stem_at(g, o, pt):
            continue
        t = g.q(axis_u, w, pt)
        to = g.q(axis_u, o, pt)
        olen = g.q(axis_len, o)
        if min(abs(to), abs(to - olen)) > JOIN_MAX_EXT:
            continue                                      # 상대 벽의 중간에 닿는 T 조인: 연장 없음
        c, s = g.q(axis_dir, w, t)
        oc, os_ = g.q(axis_dir, o, to)
        sin = c * os_ - s * oc
        if abs(sin) < MIN_SIN:
            continue
        ext = el(g, o)["params"]["thickness"] / 2.0 / abs(sin)
        if abs(t) <= abs(t - length):
            if abs(t) <= JOIN_MAX_EXT:
                start = min(start, t - ext)
        elif abs(t - length) <= JOIN_MAX_EXT:
            end = max(end, t + ext)
    return (start, end)


def wall_extent(g, w):
    """벽 형상의 지역 x 범위(시작, 끝). 조인 자르기(wall_trim)가 축선 교점에서 자르면 L 조인 바깥 모서리에 반 두께 노치가 남는다.
    그래서 id 가 작은 벽이 상대 벽 반 두께(/|sin|)만큼 더 나가 모서리를 닫는다. T 조인(상대 벽 중간에 닿음)·얕은 각·퇴화한 조인은 연장하지 않는다.
    IFC 투영의 벽 윤곽(wall_footprint)과 도면(drawing.wall_range)이 모두 이 값을 쓴다: 도면과 IFC 가 같은 형상이다.
    wall_trim(논리 길이: 치수 참조·일람표 길이·문 범위)은 그대로 축선 교점이다.
    곡선 벽(W2 ARC-04)이 이 벽이거나 조인 상대면 _arc_extent(곡선 벽이 없는 모델은 아래 코드 그대로다)."""
    arcs = arc_walls(g)
    if arcs and (w in arcs or any(o in arcs for o in g.q(wall_joins, w))):
        return _arc_extent(g, w)
    start, end, status = g.q(wall_trim, w)
    if not CORNER_EXTEND or status != "ok":
        return (start, end)
    x, y, c, s, length = g.q(wall_frame, w)
    tee = _tee_of(g, w)
    if tee:   # T 조인 줄기(W1 1-F 3차): 줄기 끝은 몸통 벽 면에서 멈춘다(축선 교점에서 몸통 반 두께/|sin| 만큼 물러난다)
        for o in g.q(wall_joins, w):
            pt = g.q(join_point, min(w, o), max(w, o))
            if pt is None or _is_stem_at(g, o, pt):
                continue
            side = tee_end_at((x, y, c, s, length), tee, pt)
            ox, oy, oc, os_, olen = g.q(wall_frame, o)
            sin = c * os_ - s * oc
            if side is None or abs(sin) < MIN_SIN:
                continue
            ext = el(g, o)["params"]["thickness"] / 2.0 / abs(sin)
            if side == "start" and end - (start + ext) >= MIN_TRIMMED:
                start += ext
            elif side == "end" and (end - ext) - start >= MIN_TRIMMED:
                end -= ext
    for o in g.q(wall_joins, w):
        if not w < o:
            continue
        pt = g.q(join_point, w, o)
        if pt is None:
            continue
        if tee and tee_end_at((x, y, c, s, length), tee, pt) or _is_stem_at(g, o, pt):
            continue                                      # T 조인(줄기·몸통): 모서리 연장 없음
        ox, oy, oc, os_, olen = g.q(wall_frame, o)
        t = (pt[0] - x) * c + (pt[1] - y) * s
        to = (pt[0] - ox) * oc + (pt[1] - oy) * os_
        if min(abs(to), abs(to - olen)) > JOIN_MAX_EXT:
            continue                                      # 상대 벽의 끝이 아니라 중간에 닿는 T 조인: 연장 없음
        sin = c * os_ - s * oc
        if abs(sin) < MIN_SIN:
            continue
        ext = el(g, o)["params"]["thickness"] / 2.0 / abs(sin)
        if abs(t) <= abs(t - length):
            if abs(t) <= JOIN_MAX_EXT:
                start = min(start, t - ext)
        elif abs(t - length) <= JOIN_MAX_EXT:
            end = max(end, t + ext)
    return (start, end)


def wall_length(g, w):
    start, end, _ = g.q(wall_trim, w)
    return end - start


def wall_footprint(g, w):
    """벽 지역 좌표의 평면 윤곽(L 조인 모서리 연장 포함: wall_extent). 벽을 평행 이동해도 조인이 그대로면 값이 같다(조기 차단)."""
    start, end = g.q(wall_extent, w)
    half = el(g, w)["params"]["thickness"] / 2.0
    return ((start, -half), (end, -half), (end, half), (start, half))


# ---------------------------------------------------------------- 독립 개구 객체(W2 세 번째 PR, ARC-34)
def opening_objs(g):
    """저작 독립 개구 객체 ((id, params), ...) id 순. 없으면 빈 값(호스트 형상·도면·IFC 는 이전과 같다)."""
    if not count(g, "opening"):
        return ()
    out = []
    for b in range(NB):
        for o in bucket_ids(g, "opening", b):
            r = el(g, o)
            if r is not None and r["origin"] == "authored":
                out.append((o, r["params"]))
    return tuple(sorted(out, key=lambda x: x[0]))


def shaft_zrange(g, o):
    """샤프트 개구의 절대 z 범위 (아래, 위). 레벨이 없으면(고아) None."""
    p = el(g, o)["params"]
    li = el(g, p["level"])
    if li is None or li["kind"] != "level":
        return None
    z0 = li["params"]["elevation"] + p.get("base_offset", 0.0)
    return (z0, z0 + p["height"])


def slab_voids(g, s):
    """슬래브를 관통하는 독립 개구 ((개구 id, 이름, 경계 다각형), ...): 이 슬래브를 가리키는 수직 개구 + z 범위가 슬래브 두께와 겹치고(샤프트 바닥에 윗면이 닿는 바닥 레벨 슬래브도 관통, 위 끝은 뺀다) 경계가
    슬래브 경계 안에 드는 샤프트 개구(슬래브 구멍과 겹치지 않는다). 없으면 빈 값."""
    objs = g.q(opening_objs)
    if not objs:
        return ()
    from dobim_core import kinds as K
    p = el(g, s)["params"]
    out = []
    for o, op in objs:
        if op["mode"] == "vertical" and op["host"] == s:
            out.append((o, op["name"], K.opening_loop(op)))   # 원호 경계(W2 열네 번째 PR)는 펼친 고리. 볼록도가 없으면 boundary 그대로다
        elif op["mode"] == "shaft":
            zr = g.q(shaft_zrange, o)
            if zr is None:
                continue
            zb, zt = g.q(slab_zrange, s)
            lp = K.opening_loop(op)
            if zr[0] <= zt + 1e-6 and zb < zr[1] - 1e-6 and all(K.point_in_polygon(q, p["boundary"]) == 1 for q in lp) and not any(
                    K.point_in_polygon(q, h) >= 0 for h in p.get("openings", ()) for q in lp):
                out.append((o, op["name"], lp))
    return tuple(out)


def roof_gm(g, r):
    """저작 지붕의 형상(kinds.roof_geom, 레벨 기준 z). 지붕 개구(W2 열네 번째 PR)의 면 찾기·IFC·도면이 같이 쓴다."""
    from dobim_core import kinds as K
    return K.roof_geom(el(g, r)["params"])


def roof_zrange(g, r):
    """지붕 판이 차지하는 절대 z 범위 (가장 낮은 윗면 - 두께, 가장 높은 윗면)."""
    gm = g.q(roof_gm, r)
    lv = g.q(level_info, g.q(roof_level, r))[1]
    return (lv + gm["zlow"] - el(g, r)["params"]["thickness"], lv + gm["ztop"])


def roof_voids(g, r):
    """경사 지붕을 뚫는 독립 개구 ((개구 id, 이름, 모드, 경계 고리), ...)(W2 열네 번째 PR): 이 지붕을 가리키는 수직·면 개구 + also 에 roof 가 있고
    z 범위가 지붕 판과 겹치는 샤프트. 처마를 내민 외곽(outline) 안쪽이고 앞의 개구와 겹치지 않는 것만(면 개구는 넓이 중심이 든 면 안에 다 들어야 한다).
    지붕을 고쳐 맞지 않게 된 개구는 빠진다(그리지도 IFC 로 내지도 않는다). 없으면 빈 값."""
    objs = g.q(opening_objs)
    if not objs:
        return ()
    from dobim_core import hostvoid as HV
    from dobim_core import kinds as K
    cand = []
    for o, op in objs:
        if op["mode"] in ("vertical", "face") and op["host"] == r:
            cand.append((o, op["name"], op["mode"], K.opening_loop(op)))
        elif op["mode"] == "shaft" and "roof" in op.get("also", ()):
            zr = g.q(shaft_zrange, o)
            if zr is None:
                continue
            zb, zt = g.q(roof_zrange, r)
            if zr[0] <= zt + 1e-6 and zb < zr[1] - 1e-6:
                cand.append((o, op["name"], "shaft", K.opening_loop(op)))
    if not cand:
        return ()
    gm = g.q(roof_gm, r)
    out, acc = [], []
    for c in cand:
        lp = c[3]
        if c[2] == "face":
            i = HV.face_of(gm, lp)
            if i is None or not HV.inside(lp, HV.face_xy(gm["faces"][i])):
                continue
        if K.openings_problem(gm["outline"], acc + [lp]) is not None:
            continue
        acc.append(lp)
        out.append(c)
    return tuple(out)


def solid_zrange(g, e):
    """솔리드의 절대 z 범위(꼭짓점 경계 상자)."""
    verts, _ = SO.solid_mesh(el(g, e)["params"])
    lv = g.q(level_info, g.q(solid_level, e))[1]
    return (lv + min(v[2] for v in verts), lv + max(v[2] for v in verts))


def solid_voids(g, e):
    """천장판(범주 ceiling 의 판)을 뚫는 독립 개구 ((개구 id, 이름, 경계 고리), ...)(W2 열네 번째 PR): 이 판을 가리키는 수직 개구 + also 에 ceiling 이 있고
    z 범위가 판과 겹치는 샤프트. 판 윤곽 안쪽이고 판의 구멍(holes)·앞의 개구와 겹치지 않는 것만. 없으면 빈 값."""
    objs = g.q(opening_objs)
    if not objs:
        return ()
    p = el(g, e)["params"]
    if p.get("category") != "ceiling" or p.get("form") != "plate":
        return ()
    from dobim_core import kinds as K
    cand = []
    for o, op in objs:
        if op["mode"] == "vertical" and op["host"] == e:
            cand.append((o, op["name"], K.opening_loop(op)))
        elif op["mode"] == "shaft" and "ceiling" in op.get("also", ()):
            zr = g.q(shaft_zrange, o)
            if zr is None:
                continue
            zb, zt = g.q(solid_zrange, e)
            if zr[0] <= zt + 1e-6 and zb < zr[1] - 1e-6:
                cand.append((o, op["name"], K.opening_loop(op)))
    out, acc = [], []
    holes = [tuple(tuple(q) for q in h) for h in p.get("holes", ())]
    for c in cand:
        if K.openings_problem(p["polygon"], holes + acc + [c[2]]) is not None:
            continue
        acc.append(c[2])
        out.append(c)
    return tuple(out)


def solid_params(g, e):
    """도면이 그리는 솔리드 params: 천장판 개구(W2 열네 번째 PR)가 있으면 그 고리를 판의 구멍(holes)으로 더한다(IFC 는 몸체 그대로 + IfcOpeningElement).
    개구가 없으면 행 params 그대로다."""
    p = el(g, e)["params"]
    vs = g.q(solid_voids, e) if g.q(opening_objs) else ()
    if not vs:
        return p
    return dict(p, holes=[list(map(list, h)) for h in p.get("holes", ())] + [[list(q) for q in lp] for _o, _n, lp in vs])


def wall_voids(g, w):
    """벽을 뚫는 독립 벽 개구 ((개구 id, 이름, 모양 개구부 꼴 dict), ...). 없으면 빈 값."""
    from dobim_core import kinds as K
    return tuple((o, op["name"], K.opening_shape(op)) for o, op in g.q(opening_objs) if op["mode"] == "wall" and op["host"] == w)


def wall_hosted(g, w):
    return rel_out(g, "hosts", w)


def wall_xcuts(g, w):
    """다른 벽에 호스팅된 인스턴스가 이 벽을 함께 자르는 개구부(25단계 also_cuts): ((인스턴스 id, (u0, u1, z0, z1)), ...).
    u 는 이 벽의 지역 좌표, z 는 이 벽 레벨 기준이다. 호스트 지역 좌표의 개구부를 세계로 옮긴 뒤 이 벽 축선에 투영한다."""
    out = []
    for f in xcut_finsts(g):
        p = el(g, f)["params"]
        if w not in p.get("also_cuts", ()):
            continue
        h = g.q(finst_host, f)
        if h is None:
            continue
        spec = g.q(ifc_spec, f)
        cut = spec.get("cut")
        if not cut:
            continue
        hx, hy, hc, hs, _ = g.q(wall_frame, h)
        ox, oy, oc, os_, _ = g.q(wall_frame, w)
        us = [((hx + u * hc) - ox) * oc + ((hy + u * hs) - oy) * os_ for u in (spec["offset"] + cut["x"][0], spec["offset"] + cut["x"][1])]
        dz = g.q(level_info, g.q(wall_level, h))[1] - g.q(level_info, g.q(wall_level, w))[1]
        box = (min(us), max(us), dz + spec["elevation"] + cut["z"][0], dz + spec["elevation"] + cut["z"][1])
        # 26단계: 창 끝이 이 벽 끝에 맞닿기만 하는 경우(also_cuts 허용 오차 안)는 이 벽에서 비울 것이 없다.
        # 벽 형상 범위와 겹치는 폭·높이가 XCUT_MIN 이하인 상자는 빼서 도면·IFC 에 빈(0 폭) 개구부가 생기지 않게 한다.
        # 25단계 규칙(겹침 > 0.5mm 만 받음)으로 놓인 상자는 늘 남으므로 이전 결과는 그대로다
        if _overlap(box, g.q(wall_extent, w), _wall_zrange(el(g, w)["params"])) is None:
            continue
        out.append((f, box))
    return tuple(out)


XCUT_MIN = 0.5   # mm: 함께 자르는 개구부가 벽과 이보다 적게 겹치면(맞닿기만 하면) 그 벽에는 개구부가 없다(commands_family.FIT_TOL 과 같다)


def _wall_zrange(p) -> tuple:
    """벽 몸체의 레벨 기준 높이 범위(바닥 오프셋 ~ 윗면). 일람표 면적 빼기와 함께 자르기 판정에 쓴다."""
    return (WT.base(p), WT.top_rel(p))


def _overlap(box, urange, zrange):
    """개구부 상자 (u0, u1, z0, z1) 를 벽 범위로 자른 (폭, 높이). 어느 쪽이든 XCUT_MIN 이하이면 None."""
    du = min(box[1], urange[1]) - max(box[0], urange[0])
    dz = min(box[3], zrange[1]) - max(box[2], zrange[0])
    if du <= XCUT_MIN or dz <= XCUT_MIN:
        return None
    return (du, dz)


def finst_box(g, f):
    """호스팅 패밀리 인스턴스 개구부의 호스트 지역 상자 (u0, u1, z0, z1), z 는 호스트 레벨 기준. 개구부가 없으면 None."""
    spec = g.q(ifc_spec, f)
    cut = spec.get("cut")
    if not cut:
        return None
    return (spec["offset"] + cut["x"][0], spec["offset"] + cut["x"][1], spec["elevation"] + cut["z"][0], spec["elevation"] + cut["z"][1])


def wall_xcut_area(g, w):
    """26단계: also_cuts 로 생긴 이 벽의 개구부 넓이(입면, mm²). (다른 벽의 창이 이 벽을 자르는 넓이, 개수).
    상자는 이 벽의 논리 범위(wall_trim 시작~끝, 바닥~윗면)로 잘라 잰다. 맞닿기만 하는 벽은 넓이 0 이다."""
    p = el(g, w)["params"]
    start, end, _ = g.q(wall_trim, w)
    area, n = 0.0, 0
    for _f, box in g.q(wall_xcuts, w):
        ov = _overlap(box, (start, end), _wall_zrange(p))
        if ov is not None:
            area += ov[0] * ov[1]
            n += 1
    return (area, n)


def wall_cut_boxes(g, w):
    """벽 잘라내기(23단계) 상자를 지금 벽 형상 범위(조인 자르기 범위·두께·바닥~윗면, 레벨 기준 z)로 자른 값. 키가 없으면 ()."""
    p = el(g, w)["params"]
    if "cuts" not in p:
        return ()
    start, end = g.q(wall_extent, w)
    return WC.clip(p["cuts"], start, end, p["thickness"] / 2.0, WT.base(p), WT.top_rel(p))


# ---------------------------------------------------------------- 슬래브
def slab_level(g, s):
    return rel_out(g, "on_level", s)[0]


def slab_zrange(g, s):
    """(밑면 z, 윗면 z) 절대 높이. 윗면 = 레벨 높이 + top_offset, 두께는 윗면에서 아래로."""
    p = el(g, s)["params"]
    top = g.q(level_info, g.q(slab_level, s))[1] + p["top_offset"]
    return (top - p["thickness"], top)


# ---------------------------------------------------------------- 계단
def stair_level(g, s):
    return rel_out(g, "on_level", s)[0]


def stair_zrange(g, s):
    """(아래 끝 z, 위 끝 z) 절대 높이. 아래 끝 = 레벨 높이 + base_offset, 위 끝 = 아래 끝 + 전체 오름."""
    p = el(g, s)["params"]
    base = g.q(level_info, g.q(stair_level, s))[1] + p["base_offset"]
    return (base, base + p["height"])


# ---------------------------------------------------------------- 난간
def railing_level(g, s):
    return rel_out(g, "on_level", s)[0]


# ---------------------------------------------------------------- 승강기
def elevator_level(g, s):
    return rel_out(g, "on_level", s)[0]


# ---------------------------------------------------------------- 지붕(경사)
def roof_level(g, s):
    return rel_out(g, "on_level", s)[0]


# ---------------------------------------------------------------- 일반 솔리드(18단계)
def solid_level(g, s):
    return rel_out(g, "on_level", s)[0]


# ---------------------------------------------------------------- 스택 벽(W2 일곱 번째 PR, ARC-09)
def stack_level(g, s):
    return rel_out(g, "on_level", s)[0]


# ---------------------------------------------------------------- 커튼월(W2 네 번째 PR, ARC-22)
def curtain_level(g, c):
    return rel_out(g, "on_level", c)[0]


def curtain_layout(g, c):
    """커튼월 배치(curtain.layout). 행 params 만의 함수라 커튼월 하나가 바뀔 때만 다시 계산한다(검사를 통과한 행이라 실패하지 않는다)."""
    return CW.layout(el(g, c)["params"])


def curtain_base(g, c):
    """커튼월 바닥(경사 커튼 시스템은 기준선)의 절대 높이 = 레벨 + base_offset. 부속 z 는 이 높이 기준이다."""
    return g.q(level_info, g.q(curtain_level, c))[1] + el(g, c)["params"].get("base_offset", 0.0)


def curtain_z(g, c):
    """커튼월의 (바닥, 윗면) 절대 높이. 경사 커튼 시스템(W2 열 번째 PR)은 부속이 차지하는 범위."""
    p = el(g, c)["params"]
    z0 = g.q(level_info, g.q(curtain_level, c))[1] + p.get("base_offset", 0.0)
    if p.get("form") == "sloped":
        lo, hi = CW.zrange(g.q(curtain_layout, c))
        return (z0 + lo, z0 + hi)
    return (z0, z0 + p["height"])


def curtain_stats(g, c):
    """(멀리언 수, 패널 수, 유형별 멀리언 수 {유형: (개수, 길이 합 mm)}, 종류별 패널 수 {종류: (개수, 면적 합 m2)})."""
    lay = g.q(curtain_layout, c)
    by_t, by_p = {}, {}
    for m in lay["mullions"]:
        t = m[2]
        n = CW.part_length(lay, m)   # 모양 부속(W2 열 번째 PR)은 잘린 길이. 없으면 이전과 같은 식
        a, b = by_t.get(t, (0, 0.0))
        by_t[t] = (a + 1, b + n)
    for x in lay["panels"]:
        kind = x[1]
        a, b = by_p.get(kind, (0, 0.0))
        by_p[kind] = (a + 1, b + CW.panel_area(lay, x) / 1e6)
    return (len(lay["mullions"]), len(lay["panels"]), by_t, by_p)


def curtain_ids(g):
    out = []
    for b in range(NB):
        out.extend(bucket_ids(g, "curtwall", b))
    return tuple(sorted(out))


# ---------------------------------------------------------------- 구조 보(W2 열다섯 번째 PR, STR-02·STR-03)
def beam_level(g, b):
    return rel_out(g, "on_level", b)[0]


def beam_ids(g):
    out = []
    for b in range(NB):
        out.extend(bucket_ids(g, "beam", b))
    return tuple(sorted(out))


def column_feet(g, e):
    """기둥(호스트 없이 레벨에 서는 IfcColumn 패밀리 인스턴스)의 각기둥 ((아래 z, 위 z, 경계 상자, 세계 다각형 반시계), ...) 절대 높이.
    기둥이 아니거나 그릴 각기둥이 없으면 (). 단면은 body 의 xy 압출 프로필(사각·원(32각형)·다각형)이다(도면 free_fam 과 같은 원천)."""
    row = el(g, e)
    if row is None or row["kind"] != "finst" or g.q(finst_host, e) or g.q(finst_ifc_class, e) != "IfcColumn":
        return ()
    spec = g.q(ifc_spec, e)
    if spec.get("error") or not spec.get("position"):
        return ()
    zb = g.q(level_info, g.q(finst_level, e))[1] + spec["elevation"]
    sn, cs = SO.sincos_deg(float(spec.get("rotation", 0.0) or 0.0))
    x0, y0 = spec["position"][0], spec["position"][1]
    out = []
    for b in spec["body"]:
        if b.get("k") != "extrude" or b.get("plane") != "xy" or b.get("rotate"):
            continue
        pr = b["profile"]
        sh = pr.get("shape")
        if sh == "rect":
            (u0, u1), (v0, v1) = pr["u"], pr["v"]
            loc = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        elif sh == "circle":
            loc = SO._circle(pr["r"], pr["center"][0], pr["center"][1])
        elif sh == "polygon":
            loc = [tuple(q) for q in pr["points"]]
        else:
            continue
        poly = SO.ccw([(x0 + u * cs - v * sn, y0 + u * sn + v * cs) for u, v in loc])
        xs, ys = [q[0] for q in poly], [q[1] for q in poly]
        out.append((zb + b["range"][0], zb + b["range"][1], (min(xs), min(ys), max(xs), max(ys)), tuple(poly)))
    return tuple(out)


def beam_columns(g):
    """보 끝을 자를 수 있는 기둥 각기둥 전체 ((기둥 id, 아래 z, 위 z, 경계 상자, 다각형), ...) id 순. 보가 있을 때만 당긴다."""
    out = []
    for b in range(NB):
        for e in bucket_ids(g, "finst", b):
            out.extend((e,) + f for f in g.q(column_feet, e))
    return tuple(sorted(out, key=lambda t: (t[0], t[1], t[2])))


def beam_zref(g, b):
    """보 기준선의 절대 높이(레벨 + offset)."""
    return g.q(level_info, g.q(beam_level, b))[1] + el(g, b)["params"]["offset"]


def _to_axis(p, c):
    """평면 길이로 잰 자르기(beams.trims)를 축 방향(실길이) 길이로. 수평 보는 그대로."""
    if not BM.sloped(p):
        return c
    cs = BM.slope(p)[0]
    return (c[0] / cs, c[1] / cs, c[2], c[3])


def beam_col_cut(g, b):
    """기둥 면 자르기만 (시작에서 줄인 길이, 끝에서 줄인 길이, 시작 기둥, 끝 기둥)(축 방향 길이). 기둥이 없으면 (0, 0, None, None).
    보끼리 접합의 주 보 형상은 이것으로 정한다(보끼리 자르기는 서로를 읽지 않아 순환이 없다)."""
    p = el(g, b)["params"]
    if p.get("cutback") is False or not g.q(count, "finst"):
        return (0.0, 0.0, None, None)
    return _to_axis(p, BM.trims(p, BM.end_zranges(p, g.q(beam_zref, b)), g.q(beam_columns)))


def beam_post(g, b):
    """보 b 를 다른 보 끝이 닿는 '주 보'로 볼 때의 (id, 아래 z, 위 z, 평면 경계 상자, 평면 외곽 사각형, 순위) 또는 None(저작 보가 아니다).
    형상은 기둥 면 자르기만 반영한다. 순위가 작을수록 주 보다: 춤이 큰 것, 같으면 폭이 큰 것, 같으면 id 가 작은 것."""
    row = el(g, b)
    if row is None or row["kind"] != "beam" or row["origin"] != "authored":
        return None
    p = row["params"]
    c = g.q(beam_col_cut, b)
    rect = tuple(BM.plan_rect(p, c[:2]))
    lo, hi = BM.z_range(p, g.q(beam_zref, b), c[:2])
    xs, ys = [q[0] for q in rect], [q[1] for q in rect]
    return (b, lo, hi, (min(xs), min(ys), max(xs), max(ys)), rect, (-float(p["section"]["depth"]), -float(p["section"]["width"]), b))


BEAM_CELL = 5000.0   # 보끼리 접합 후보를 찾는 평면 격자 칸 mm


def beam_post_index(g):
    """{(칸 x, 칸 y): (주 보 후보 id, ...)}: 평면 경계 상자가 걸친 칸마다. 보가 둘 이상일 때만 당긴다."""
    out: dict = {}
    for b in g.q(beam_ids):
        pt = g.q(beam_post, b)
        if pt is None:
            continue
        bb = pt[3]
        for i in range(int((bb[0] - 0.01) // BEAM_CELL), int((bb[2] + 0.01) // BEAM_CELL) + 1):
            for j in range(int((bb[1] - 0.01) // BEAM_CELL), int((bb[3] + 0.01) // BEAM_CELL) + 1):
                out.setdefault((i, j), []).append(b)
    return {k: tuple(v) for k, v in out.items()}


def beam_mains(g, b):
    """보 b 의 끝을 자르는 주 보 후보 ((id, 아래 z, 위 z, 경계 상자, 사각형), ...) id 순(W2 열여덟 번째 PR, 보끼리 접합).
    b 의 한 끝이 다른 보 m 의 평면 외곽 안에 있으면 m 이 후보다. 단 m 의 끝도 b 의 외곽 안이고(L 결합처럼 서로 끝이 닿는다) b 의 순위가 앞서면
    b 가 주 보라 m 에게 잘리지 않는다(주 보가 이긴다). 높이 겹침은 trims 가 본다."""
    p = el(g, b)["params"]
    idx = g.q(beam_post_index)
    me = g.q(beam_post, b)
    cand = set()
    for k in (0, 1):
        x, y = (p["start"] if k == 0 else p["end"])
        for dx in (-0.01, 0.01):   # 칸 경계 위의 끝점은 이웃 칸도 본다
            for dy in (-0.01, 0.01):
                cand.update(idx.get((int((x + dx) // BEAM_CELL), int((y + dy) // BEAM_CELL)), ()))
    cand.discard(b)
    out = []
    for m in sorted(cand):
        pm = g.q(beam_post, m)
        if not (BM.end_inside(p, 0, pm) or BM.end_inside(p, 1, pm)):
            continue
        q = el(g, m)["params"]
        if me is not None and (BM.end_inside(q, 0, me) or BM.end_inside(q, 1, me)) and me[5] < pm[5]:
            continue   # 서로 끝이 닿고 b 가 주 보다
        out.append(pm[:5])
    return tuple(out)


def beam_cut_ex(g, b):
    """beam_cut 의 앞 넷 + (시작 변 방향, 끝 변 방향)(W2 열아홉 번째 PR): 주 보로 자른 끝에서 그 주 보 외곽 변의 평면 단위 방향(경사 자르기용), 아니면 None.
    보끼리 접합 간격(gap)은 주 보로 자른 끝에만 더한다(축 방향 길이). 자를 것이 없으면 (0, 0, None, None, None, None)."""
    p = el(g, b)["params"]
    if p.get("cutback") is False:
        return (0.0, 0.0, None, None, None, None)
    mains = g.q(beam_mains, b) if g.q(count, "beam") > 1 else ()
    if not mains:
        return g.q(beam_col_cut, b) + (None, None)
    cols = g.q(beam_columns) if g.q(count, "finst") else ()
    gap = float(p.get("gap", 0.0))
    cs = BM.slope(p)[0] if BM.sloped(p) else 1.0   # 간격은 축 방향 길이로 주고 trims 는 평면 길이로 잰다
    c = BM.trims_ex(p, BM.end_zranges(p, g.q(beam_zref, b)), tuple(cols) + mains, gap * cs, frozenset(m[0] for m in mains))
    return _to_axis(p, c[:4]) + c[4:]


def beam_cut(g, b):
    """기둥·보 면 자르기 (시작에서 줄인 길이, 끝에서 줄인 길이, 시작 쪽 기둥·주 보 id, 끝 쪽 id)(축 방향 길이, beams.trims). 자를 것이 없으면 (0, 0, None, None).
    보끼리 접합(W2 열여덟 번째 PR): 주 보(beam_mains)를 기둥과 같은 규칙으로 넘긴다. 기둥과 주 보가 함께 닿으면 더 많이 줄이는 쪽.
    곡선 보(W2 열아홉 번째 PR)는 길이를 호를 따라 잰다. 간격(gap)·변 방향은 beam_cut_ex."""
    return g.q(beam_cut_ex, b)[:4]


def beam_ends(g, b):
    """끝면이 축에 수직이 아닌 보의 끝면 기울기(beams.end_shears) 또는 None(경사 보 연직 끝면·경사 자르기가 없다)."""
    p = el(g, b)["params"]
    if "vertical_ends" not in p and "skew_cut" not in p:
        return None
    c = g.q(beam_cut_ex, b)
    return BM.end_shears(p, (c[4], c[5]))


def beam_measure(g, b):
    """(축 실길이, 자른 실길이, 단면적 mm2, 체적 mm3). 경사 보는 3D 길이, 곡선 보는 호 길이다(곡선 보 체적은 beams.volume)."""
    p = el(g, b)["params"]
    ln = BM.length3(p)
    c = g.q(beam_cut, b)
    cl = ln - c[0] - c[1]
    a = BM.section_area(p["section"])
    return (ln, cl, a, BM.volume(p, cl, g.q(beam_ends, b)))


def beam_tagpos(g, b):
    """보 태그 자리 (x, y, 접선 x, 접선 y)(W2 스물한 번째 PR): 기둥·보 면으로 자른 뒤 남은 축의 가운데(곡선 보는 호 가운데)와 그 점의 진행 방향. beams.mid_frame."""
    return BM.mid_frame(el(g, b)["params"], g.q(beam_cut, b)[:2])


# ---------------------------------------------------------------- 공간
def space_level(g, s):
    return rel_out(g, "on_level", s)[0]


# ---------------------------------------------------------------- 레벨
def level_info(g, lv):
    p = el(g, lv)["params"]
    return (p["name"], p["elevation"])


# ---------------------------------------------------------------- 문
def door_host(g, d):
    return rel_in(g, "hosts", d)[0]


def door_world(g, d):
    """문 원점의 세계 좌표. 호스트 축선 기준이므로 벽을 따라간다."""
    w = g.q(door_host, d)
    p = el(g, d)["params"]
    _, elev = g.q(level_info, g.q(wall_level, w))
    off = p["offset"]
    ai = g.q(wall_arc, w)
    if ai is not None:   # 곡선 벽(W2 ARC-04): 호 위 거리 offset 의 점
        x, y = AG.point(ai, off)
        return (x, y, elev + p["sill"])
    x, y, c, s, _ = g.q(wall_frame, w)
    return (x + off * c, y + off * s, elev + p["sill"])


def door_status(g, d):
    """조인으로 벽이 줄어 문이 벽 밖으로 나갔는지 표시한다. 조용히 옮기지 않는다."""
    w = g.q(door_host, d)
    start, end, _ = g.q(wall_trim, w)
    p = el(g, d)["params"]
    half = p["width"] / 2.0
    return "ok" if start <= p["offset"] - half and p["offset"] + half <= end else "outside_trimmed_host"   # 사실 표시(벽 끝 허용 DOOR_END_ALLOW 안이어도 넘으면 표시한다)


# ---------------------------------------------------------------- 패밀리 인스턴스 (S4)
def finst_type(g, e):
    return rel_out(g, "of_type", e)[0]


def finst_host(g, e):
    r = rel_in(g, "hosts", e)
    return r[0] if r else None


def finst_level(g, e):
    h = g.q(finst_host, e)
    return g.q(wall_level, h) if h else rel_out(g, "on_level", e)[0]


# ---------------------------------------------------------------- 일람표 (T0)
def _pset_val(g, e, pset, prop):
    return psets(g, e).get(pset, {}).get(prop)


def door_row(g, d):
    p = el(g, d)["params"]
    w = g.q(door_host, d)
    lname, elev = g.q(level_info, g.q(wall_level, w))
    wx, wy, wz = g.q(door_world, d)
    return (d, p["name"], w, lname, elev, p["width"], p["height"], p["sill"], p["offset"], wx, wy, wz,
            _pset_val(g, d, "Pset_DoorCommon", "FireRating"),
            _pset_val(g, d, "Pset_DoorCommon", "IsExternal"),
            material(g, d), g.q(door_status, d))


def wall_row(g, w):
    p = el(g, w)["params"]
    lname, elev = g.q(level_info, g.q(wall_level, w))
    length = g.q(wall_length, w)
    gross = length * p["height"]
    height = p["height"]
    if "top_profile" in p:   # 벽 상단 윤곽(21단계): 입면 면적은 윤곽 아래 넓이, 높이 칸은 가장 높은 점(바닥 오프셋 22단계: 벽 바닥에서 잰다)
        start, end, _ = g.q(wall_trim, w)
        b0 = WT.base(p)
        gross = WT.area(p["top_profile"], start, end) - b0 * (end - start)
        height = WT.zmax(p["top_profile"], start, end) - b0
    opening = 0.0
    hosted = g.q(wall_hosted, w)
    for d in hosted:
        dp = el(g, d)["params"]
        if dp.get("also_cuts"):   # 26단계: 함께 자르는 창은 넘친 부분이 이웃 벽 몫이라 호스트 범위로 자른 넓이만 뺀다(없으면 이전과 같다)
            start, end, _ = g.q(wall_trim, w)
            box = g.q(finst_box, d)
            ov = _overlap(box, (start, end), _wall_zrange(p)) if box is not None else None
            opening += ov[0] * ov[1] if ov is not None else 0.0
            continue
        opening += dp["width"] * dp["height"]
    for o in p.get("openings", ()):   # 모양 개구부(18단계)의 실제 윤곽 면적
        opening += abs(SO.area2(SO.opening_loop(o))) / 2.0
    if g.q(opening_objs):   # 독립 벽 개구(W2 세 번째 PR, ARC-34)도 같은 넓이로 뺀다. 없으면 행은 그대로
        for _o, _n, d in g.q(wall_voids, w):
            opening += abs(SO.area2(SO.opening_loop(d))) / 2.0
    if "cuts" in p:   # 벽 잘라내기(23단계): 두께를 다 뚫는 상자의 입면 넓이를 뺀다(한쪽 면만 파인 상자는 부피에만 든다). 없으면 행은 그대로
        opening += WC.through_area(g.q(wall_cut_boxes, w), p["thickness"] / 2.0)
    opening += g.q(wall_xcut_area, w)[0]   # 26단계: 다른 벽의 창이 함께 자르는 개구부(also_cuts). 없으면 0 이라 행은 그대로
    return (w, p["name"], lname, elev, length, height, p["thickness"], gross - opening, len(hosted),
            _pset_val(g, w, "Pset_WallCommon", "FireRating"),
            _pset_val(g, w, "Pset_WallCommon", "IsExternal"),
            material(g, w), g.q(wall_trim, w)[2])


ROW_COLUMNS = {
    "door": ("id", "name", "host", "level", "elevation", "width", "height", "sill", "offset",
             "world_x", "world_y", "world_z", "fire_rating", "is_external", "material", "status"),
    "wall": ("id", "name", "level", "elevation", "length", "height", "thickness", "net_area", "doors",
             "fire_rating", "is_external", "material", "status"),
}
_SORT_IDX = {"door": 3, "wall": 2}  # 정렬 키: (레벨 이름, 이름, id)


def bucket_rows(g, kind, b):
    """버킷 하나의 일람표 행. id 순서로 고정해 합계의 부동소수 합산 순서도 결정적이다."""
    fn = door_row if kind == "door" else wall_row
    return tuple(g.q(fn, e) for e in g.q(bucket_ids, kind, b))


def schedule_digest(g, kind):
    """일람표 전체의 최신 값. 버킷 값의 튜플이므로 한 요소가 바뀌면 한 버킷만 새 객체가 된다."""
    return tuple(g.q(bucket_rows, kind, b) for b in range(NB))


def bucket_summary(g, kind, b):
    rows = g.q(bucket_rows, kind, b)
    if kind == "wall":
        return (len(rows), sum(r[4] for r in rows), sum(r[7] for r in rows))
    return (len(rows), sum(r[5] for r in rows), 0.0)


def schedule_summary(g, kind):
    n, a, c = 0, 0.0, 0.0
    for b in range(NB):
        sn, sa, sc = g.q(bucket_summary, kind, b)
        n, a, c = n + sn, a + sa, c + sc
    if kind == "wall":
        return {"count": n, "total_length": a, "total_net_area": c}
    return {"count": n, "total_width": a}


def schedule_key(kind, row):
    """일람표 행의 전순서 키 (레벨 이름, 이름, id). 키셋 커서가 마지막으로 읽은 행의 키를 담는다."""
    return (row[_SORT_IDX[kind]], row[1], row[0])


def schedule_sorted(g, kind):
    """정렬된 전체 행. T0 가 아니라 schedule.rows 조회 때만 당긴다."""
    i = _SORT_IDX[kind]
    rows = [r for bucket in g.q(schedule_digest, kind) for r in bucket]
    rows.sort(key=lambda r: (r[i], r[1], r[0]))
    return tuple(rows)


def finst_ifc_class(g, e):
    """패밀리 인스턴스의 IFC 클래스(타입이 가진 레시피의 ifc.class)."""
    rec = el(g, g.q(finst_type, e))["params"].get("recipe") or {}
    return (rec.get("ifc") or {}).get("class")


def bucket_finst_classes(g, b):
    """버킷 하나의 패밀리 인스턴스를 IFC 클래스별로 센 값((클래스, 수), ...). 인스턴스 하나가 바뀌면 그 버킷만 다시 센다."""
    n: dict = {}
    for e in g.q(bucket_ids, "finst", b):
        c = g.q(finst_ifc_class, e)
        n[c] = n.get(c, 0) + 1
    return tuple(sorted(n.items(), key=lambda kv: str(kv[0])))


def model_stats(g):
    out = {k: g.q(count, k) for k in ("level", "wall", "door")}
    for k in ("slab", "stair", "space", "railing", "elevator", "roof", "site", "solid"):
        n = g.q(count, k)
        if n:   # 슬래브·계단·공간·난간·대지가 없는 프로젝트의 응답·해시는 그대로다
            out[k] = n
    nf = g.q(count, "finst")
    if nf:   # 패밀리로 놓은 문(IfcDoor 레시피)도 문으로 센다(W2 실사용 결함 4). 창은 window, 인스턴스 전체는 finst. 인스턴스가 없으면 그대로다
        by: dict = {}
        for b in range(NB):
            for c, n in g.q(bucket_finst_classes, b):
                by[c] = by.get(c, 0) + n
        out["door"] += by.get("IfcDoor", 0)
        if by.get("IfcWindow"):
            out["window"] = by["IfcWindow"]
        out["finst"] = nf
    nc = g.q(count, "curtwall")
    if nc:   # 커튼월(W2 네 번째 PR, ARC-22): 커튼월·멀리언·패널 수. 커튼월이 없으면 응답은 그대로다
        out["curtwall"] = nc
        st = [g.q(curtain_stats, c) for c in g.q(curtain_ids)]
        out["mullion"] = sum(s[0] for s in st)
        out["curtain_panel"] = sum(s[1] for s in st)
        # 커튼월 문 패널도 IFC 에서는 IfcDoor 다: counts.door = IFC 의 IfcDoor 수(W2 아홉 번째 PR, 실사용 결함 3). 문 패널이 없으면 그대로다
        out["door"] += sum(s[3].get("door", (0, 0.0))[0] for s in st)
    for k in ("walltype", "stackwall", "rooftype", "railtype", "beam", "beamtype"):   # 구조 보·보 유형(W2 열다섯 번째 PR)·난간 유형(W2 열세 번째 PR)·벽 유형·스택 벽(W2 일곱 번째 PR)·지붕 유형(W2 여덟 번째 PR): 없으면 응답은 그대로다
        n = g.q(count, k)
        if n:
            out[k] = n
    return out


def door_summary(g):
    """model.stats 의 door 요약: IFC 의 IfcDoor 전체(저작 문 + IfcDoor 패밀리 인스턴스 + 커튼월 문 패널)의 수와 폭 합(W2 아홉 번째 PR, 실사용 결함 3).
    count 는 counts.door 와 같다. 패밀리 문의 폭은 벽을 뚫는 개구부 폭(호스트 없는 인스턴스는 values 의 width, 없으면 0), 문 패널의 폭은 패널 현 길이다.
    저작 문 밖의 문이 있을 때만 by_source(door·family·curtain_panel 별 수)를 더한다(저작 문만 있는 프로젝트의 응답은 그대로다).
    일람표 요약(schedule_summary "door")은 저작 문 일람표 그대로다."""
    s = g.q(schedule_summary, "door")
    n, w = s["count"], s["total_width"]
    nf, wf = 0, 0.0
    if g.q(count, "finst"):
        for b in range(NB):
            for e in g.q(bucket_ids, "finst", b):
                if g.q(finst_ifc_class, e) != "IfcDoor":
                    continue
                spec = g.q(ifc_spec, e)
                cut = spec.get("cut")
                nf += 1
                if cut:
                    wf += cut["x"][1] - cut["x"][0]
                else:
                    v = (el(g, e)["params"].get("values") or {}).get("width")
                    wf += float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
    nc, wc = 0, 0.0
    if g.q(count, "curtwall"):
        for c in g.q(curtain_ids):
            lay = g.q(curtain_layout, c)
            for x in lay["panels"]:
                if x[1] == "door":
                    nc += 1
                    wc += CW.panel_width(lay, x)
    if not nf and not nc:
        return s
    return {"count": n + nf + nc, "total_width": w + wf + wc, "by_source": {"door": n, "family": nf, "curtain_panel": nc}}


def t0_state(g):
    """T0 동기 층 전체: 일람표 값 + 요약 + 개수. 변경 커맨드 직후 항상 최신이어야 한다."""
    return (g.q(schedule_digest, "door"), g.q(schedule_digest, "wall"),
            g.q(schedule_summary, "door"), g.q(schedule_summary, "wall"), g.q(model_stats))


# ---------------------------------------------------------------- IFC 투영 명세 (순수 값)
def intent_of(g, e):
    """DoBIM_Intent 에 복제할 의도. 파라미터와 의도 소유 관계만 담는다."""
    row = el(g, e)
    out = {"kind": row["kind"], "params": row["params"], "family_id": row["family_id"]}
    if row["kind"] == "finst":
        out["type"] = g.q(finst_type, e)
        h = g.q(finst_host, e)
        out["host" if h else "level"] = h or g.q(finst_level, e)
    elif row["kind"] == "wall":
        out["level"] = g.q(wall_level, e)
        out["joins"] = list(g.q(wall_joins, e))
    elif row["kind"] in ("slab", "stair", "space", "railing", "elevator", "roof", "solid", "curtwall", "stackwall", "beam"):
        out["level"] = g.q({"slab": slab_level, "stair": stair_level, "space": space_level, "railing": railing_level, "elevator": elevator_level, "roof": roof_level, "solid": solid_level,
                            "curtwall": curtain_level, "stackwall": stack_level, "beam": beam_level}[row["kind"]], e)
    elif row["kind"] == "door":
        out["host"] = g.q(door_host, e)
    return out


def wall_arc_local(g, w):
    """곡선 벽 축을 IFC 배치 틀(원점 = 축 시작점, x = 현 방향) 안의 값으로: (중심 x, y, 반지름, 시작각, 방향, 중심각). 벽을 옮기거나 돌려도 같다."""
    cx, cy, r, a0, sg, sw = g.q(wall_arc, w)
    x, y, c, s, _ = g.q(wall_frame, w)
    dx, dy = cx - x, cy - y
    return (dx * c + dy * s, -dx * s + dy * c, r, a0 - AG.atan2(s, c), sg, sw)


def host_at(g, w, u, half_w):
    """곡선 벽(W2 ARC-04)에 호스팅된 요소의 IFC 배치: 벽 배치 틀 안의 축 위 점·접선 (x, y, 접선 x, 접선 y)과 개구부 상자가 벽 두께를 다 뚫는
    반 깊이(호가 접선에서 휘어 나가는 만큼 더한다). 직선 벽이면 None(명세는 이전과 같다)."""
    ai = g.q(wall_arc, w)
    if ai is None:
        return None
    lcx, lcy, r, la0, sg, _ = g.q(wall_arc_local, w)
    sn, cs = AG.sincos(la0 + sg * u / r)
    half = el(g, w)["params"]["thickness"] / 2.0
    ri = r - half
    sag = ri - sqrt(ri * ri - half_w * half_w) if half_w < ri else ri
    return (lcx + r * cs, lcy + r * sn, -sg * sn, sg * cs, half + sag + 10.0)


def _void_psets(g, oids):
    """독립 개구들의 속성 ((개구 id, ((속성셋, ((속성, 값), ...)), ...)), ...). 속성이 없는 개구는 뺀다(W2 아홉 번째 PR)."""
    out = []
    for o in oids:
        ps = el(g, o)["params"].get("psets")
        if ps:
            out.append((o, tuple((k, tuple(sorted(v.items()))) for k, v in sorted(ps.items()))))
    return tuple(out)


def ifc_spec(g, e):
    """요소 하나를 IFC 로 투영할 내용. 저작 요소의 형상·배치는 이 값에서만 나온다 (ADR-0005).

    문의 배치는 호스트 벽의 국소 좌표라 벽을 옮겨도 이 값은 변하지 않는다(조기 차단).
    """
    row = el(g, e)
    kind = row["kind"]
    p = row["params"]
    if kind == "foreign":   # 외래 요소는 투영하지 않는다(IFC 가 정본)
        return {"kind": kind}
    intent = g.q(intent_of, e)
    if kind == "level":
        return {"kind": kind, "name": p["name"], "elevation": p["elevation"], "intent": intent}
    if kind == "ftype":
        return {**FS.ftype_spec(p), "intent": intent}
    if kind == "finst":
        tid = g.q(finst_type, e)
        h = g.q(finst_host, e)
        host = None
        if h:
            wp = el(g, h)["params"]
            host = {"host_thickness": wp["thickness"], "host_height": wp["height"]}
        spec = {**FS.finst_spec(el(g, tid)["params"], p, tid, h, g.q(finst_level, e), host, tolerant=True), "intent": intent}
        if h and "arc" in el(g, h)["params"]:   # 곡선 벽 호스트(W2 ARC-04): 축 위 점·접선에 놓는다. 직선 호스트면 명세는 이전과 같다
            cut = spec.get("cut")
            half_w = max(abs(cut["x"][0]), abs(cut["x"][1])) if cut else 0.0
            spec["at"] = g.q(host_at, h, p["offset"], half_w)
        return spec
    if kind == "wall":
        x, y, c, s, _ = g.q(wall_frame, e)
        spec = {"kind": kind, "name": p["name"], "level": g.q(wall_level, e), "origin": (x, y), "xdir": (c, s),
                "footprint": g.q(wall_footprint, e), "height": p["height"], "intent": intent}
        if "base_offset" in p:   # 바닥 오프셋(22단계): 몸체가 레벨 + base 에서 시작한다. 없으면 명세는 이전과 같다
            spec["base"] = WT.base(p)
        if "layers" in p:   # 층 구성이 없으면 명세는 이전과 같다
            spec["layers"] = tuple((x["material"], x["thickness"]) for x in p["layers"])
        if "openings" in p:   # 모양 개구부(18단계): 없으면 명세는 이전과 같다
            spec["openings"] = tuple(tuple(SO.opening_loop(o)) for o in p["openings"])
            spec["thickness"] = p["thickness"]
        if "top_profile" in p:   # 벽 상단 윤곽(21단계): 지역 (u, z) 수직 윤곽을 두께로 돌출한다. 없으면 명세는 이전과 같다
            fp = spec["footprint"]
            spec["top"] = tuple(WT.loop(p["top_profile"], fp[0][0], fp[1][0], WT.base(p)))
            spec["thickness"] = p["thickness"]
        xc = g.q(wall_xcuts, e)
        if xc:   # 다른 벽의 창이 함께 자르는 개구부(25단계 also_cuts): (인스턴스 id, 지역 (u, z) 사각 윤곽). 없으면 명세는 이전과 같다
            spec["xopenings"] = tuple((f, ((b[0], b[2]), (b[1], b[2]), (b[1], b[3]), (b[0], b[3]))) for f, b in xc)
            spec["thickness"] = p["thickness"]
        if "cuts" in p:   # 벽 잘라내기(23단계): 몸체에서 뺄 상자(형상 범위로 자른 값). 없으면 명세는 이전과 같다
            spec["cuts"] = g.q(wall_cut_boxes, e)
            spec["cut_range"] = (spec["footprint"][0][0], spec["footprint"][1][0], p["thickness"] / 2.0, WT.base(p), WT.top_rel(p))
        vs = g.q(wall_voids, e) if g.q(opening_objs) else ()
        if vs:   # 독립 벽 개구(W2 세 번째 PR, ARC-34): (개구 id, 이름, 지역 (u, z) 윤곽, 곡선 벽이면 host_at). 없으면 명세는 이전과 같다
            spec["voids"] = tuple((o, n, tuple(SO.opening_loop(d)), g.q(host_at, e, d["offset"], d["width"] / 2.0)) for o, n, d in vs)
            spec["thickness"] = p["thickness"]
            vp = _void_psets(g, [v[0] for v in vs])
            if vp:   # 독립 개구의 속성(W2 아홉 번째 PR): IfcOpeningElement 부속의 속성셋. 없으면 명세는 이전과 같다
                spec["void_psets"] = vp
        if "stack" in p:   # 스택 벽 부분 벽(W2 일곱 번째 PR): 층이 아니라 스택 벽에 집합된다. 없으면 명세는 이전과 같다
            spec["part_of"] = p["stack"]
        if "arc" in p:   # 곡선 벽(W2 ARC-04): 배치 틀(시작점, 현 방향) 안의 호 (중심 x, y, 반지름, 시작각, 방향, 중심각). footprint 의 u 는 호 위 거리다
            spec["arc"] = g.q(wall_arc_local, e)
            spec["thickness"] = p["thickness"]
            if "openings" in p:   # 사각 모양 개구부(W2 세 번째 PR): 개구부 가운데 u 의 축 위 점·접선 틀(문과 같은 host_at)에 놓는다
                spec["openings_at"] = tuple(g.q(host_at, e, o["offset"], o["width"] / 2.0) for o in p["openings"])
        return spec
    if kind == "solid":
        # 배치는 레벨 좌표계(원점·무회전)이고 형상 z 는 레벨 기준이므로 레벨 높이는 읽지 않는다. 형상은 solids.solid_mesh 가 정한다(도면과 같은 값)
        verts, faces = SO.solid_mesh(p)
        cls, pre, otype = SO.SOLID_CATEGORIES[p["category"]]
        spec = {"kind": kind, "name": p["name"], "level": g.q(solid_level, e), "ifc_class": cls, "predefined": pre, "object_type": otype,
                "verts": tuple(verts), "faces": tuple(tuple(tuple(lp) for lp in f) for f in faces), "intent": intent}
        if "ops" in p:   # 불리언(W2 여섯 번째 PR): IFC 는 피연산자 다면체의 IfcBooleanResult 사슬. 없으면 명세는 이전과 같다
            spec["csg"] = SO.csg_tree(p)
        vs = g.q(solid_voids, e) if g.q(opening_objs) else ()
        if vs:   # 천장판 개구(W2 열네 번째 PR): (개구 id, 이름, 경계). 몸체는 그대로, IfcOpeningElement 가 뺀다. 없으면 명세는 이전과 같다
            spec["voids"] = vs
            vp = _void_psets(g, [v[0] for v in vs])
            if vp:
                spec["void_psets"] = vp
        return spec
    if kind == "beam":
        # 구조 보(W2 열다섯 번째 PR): 배치 = 레벨 좌표계 안의 기준선 시작점(기둥 면 자르기 반영, z = offset)·x 축 = 보 방향. 몸체는 단면 프로필
        # (IfcRectangleProfileDef·IfcIShapeProfileDef)을 보 방향으로 돌출한다. 프로필 가운데(center)는 기준선에서 본 (a, b), roll 은 프로필 회전(도)
        ax, ay, ux, uy, ln = BM.axis(p)
        c = g.q(beam_cut, e)
        sc = p["section"]
        cb = {"top": -sc["depth"] / 2.0, "center": 0.0, "bottom": sc["depth"] / 2.0}[p["anchor"]]
        r = p.get("roll", 0.0)
        sn, cs = SO.sincos_deg(float(r)) if r else (0.0, 1.0)
        if BM.sloped(p):   # 경사 보(W2 열여덟 번째 PR): 돌출 방향이 배치 xz 면 안에서 기운다(slope = (cos, sin)). 원점·길이는 축(실길이)을 따라 잰다
            kc, ks, l3 = BM.slope(p)
            spec = {"kind": kind, "name": p["name"], "level": g.q(beam_level, e),
                    "origin": (ax + ux * kc * c[0], ay + uy * kc * c[0], p["offset"] + ks * c[0]), "xdir": (ux, uy), "slope": (kc, ks),
                    "length": l3 - c[0] - c[1], "section": tuple(sorted(sc.items())), "profile_name": BM.section_label(sc), "center": (-cb * sn, cb * cs),
                    "roll": (cs, sn), "intent": intent}
        elif BM.curved(p):   # 곡선 보(W2 열아홉 번째 PR): 배치 = 자른 시작점에서 호의 접선 방향. 기준선은 배치 안의 원호(arc = (반지름, 방향 +1 반시계, 중심각, 끝점))
            ai = BM.arc_info(p)
            r_, sg_ = ai[2], ai[4]
            phi = (r_ * ai[5] - c[0] - c[1]) / r_
            x0_, y0_ = AG.point(ai, c[0], 0.0)
            tx_, ty_ = AG.tangent(ai, c[0])
            sp_, cp_ = AG.sincos(phi)
            spec = {"kind": kind, "name": p["name"], "level": g.q(beam_level, e), "origin": (x0_, y0_, p["offset"]), "xdir": (tx_, ty_),
                    "arc": (r_, sg_, phi, (r_ * sp_, sg_ * r_ * (1.0 - cp_))), "length": r_ * phi, "section": tuple(sorted(sc.items())),
                    "profile_name": BM.section_label(sc), "center": (-cb * sn, cb * cs), "roll": (cs, sn), "intent": intent}
        else:
            spec = {"kind": kind, "name": p["name"], "level": g.q(beam_level, e), "origin": (ax + ux * c[0], ay + uy * c[0], p["offset"]), "xdir": (ux, uy),
                    "length": ln - c[0] - c[1], "section": tuple(sorted(sc.items())), "profile_name": BM.section_label(sc), "center": (-cb * sn, cb * cs),
                    "roll": (cs, sn), "intent": intent}
        ends = g.q(beam_ends, e)
        if ends is not None:   # 끝면이 축에 수직이 아니다(연직 끝면·경사 자르기, W2 열아홉 번째 PR): 연직 자르기 면 둘과 돌출 여유. 없으면 명세는 이전과 같다
            spec["clips"], spec["ext"] = BM.clip_planes(p, c[:2], ends)
        if "material" in p:   # 구조 재료(W2 열여덟 번째 PR): IFC4 IfcMaterialProfileSetUsage(맞춤 = 기준선 자리의 CardinalPoint). 없으면 명세는 이전과 같다
            spec["material"] = p["material"]
            spec["cardinal"] = {"top": 8, "center": 5, "bottom": 2}[p["anchor"]]
        return spec
    if kind == "slab":
        # 배치는 레벨 좌표계(원점·무회전)이고 윗면은 레벨 + top_offset 이므로 레벨 높이는 읽지 않는다(레벨을 옮기면 배치가 따라간다)
        spec = {"kind": kind, "name": p["name"], "level": g.q(slab_level, e), "boundary": tuple(tuple(q) for q in p["boundary"]),
                "thickness": p["thickness"], "top_offset": p["top_offset"], "intent": intent}
        if "layers" in p:
            spec["layers"] = tuple((x["material"], x["thickness"]) for x in p["layers"])
        if "openings" in p:   # 구멍이 없으면 명세는 이전과 같다
            spec["openings"] = tuple(tuple(tuple(q) for q in o) for o in p["openings"])
        vs = g.q(slab_voids, e) if g.q(opening_objs) else ()
        if vs:   # 독립 수직·샤프트 개구(W2 세 번째 PR, ARC-34): (개구 id, 이름, 경계). 없으면 명세는 이전과 같다
            spec["voids"] = vs
            vp = _void_psets(g, [v[0] for v in vs])
            if vp:   # 독립 개구의 속성(W2 아홉 번째 PR): 없으면 명세는 이전과 같다
                spec["void_psets"] = vp
        if "use" in p:   # 지붕 슬래브(PredefinedType ROOF). 물매·배수구는 IFC 가 아니라 의도로만 왕복한다(명세에 넣지 않는다)
            spec["use"] = p["use"]
        if "parapet" in p:   # 파라펫: 슬래브에 집합한 IfcWall(PARAPET) 부속. 없으면 명세는 이전과 같다
            spec["parapet"] = (p["parapet"]["height"], p["parapet"]["thickness"])
        return spec
    if kind == "roof":
        # 배치는 레벨 좌표계(원점·무회전)이고 처마 높이는 레벨 + base_offset 이므로 레벨 높이는 읽지 않는다
        spec = {"kind": kind, "name": p["name"], "level": g.q(roof_level, e), "boundary": tuple(tuple(q) for q in p["boundary"]), "form": p["form"], "pitch": p["pitch"],
                "overhang": p["overhang"], "base_offset": p["base_offset"], "thickness": p["thickness"], "intent": intent}
        for k in ("ridge", "dir"):
            if k in p:
                spec[k] = p[k]
        if "slopes" in p:   # 다각형 외곽 지붕(W1 1-F 4차): 없으면 명세는 이전과 같다
            spec["slopes"] = tuple(p["slopes"])
        if "overhangs" in p:   # 외곽선 지붕의 변마다 내밀기(W2 여덟 번째 PR): 없으면 명세는 이전과 같다
            spec["overhangs"] = tuple(p["overhangs"])
        if "bulges" in p:   # 평지붕의 변마다 볼록도(W2 열여섯 번째 PR): 없으면 명세는 이전과 같다
            spec["bulges"] = tuple(p["bulges"])
        if "axis" in p:   # 압출 지붕(W2 여덟 번째 PR): 축·프로파일
            spec["axis"] = tuple(p["axis"])
            spec["profile"] = tuple(tuple(q) for q in p["profile"])
        if "layers" in p:   # 지붕 층 구성(W2 여덟 번째 PR): 윗면부터 ((재료, 두께), ...). 없으면 명세는 이전과 같다
            spec["layers"] = tuple((x["material"], x["thickness"]) for x in p["layers"])
        if "eaves" in p:   # 처마 부속 조각(W2 여덟 번째 PR): (이름표, 종류, 평면 사각형, 바닥 z, 윗면 z). 없으면 명세는 이전과 같다
            from dobim_core.kinds import roof_eave_pieces
            spec["eaves"] = tuple(roof_eave_pieces(p))
            em = tuple(sorted((k, d["material"]) for k, d in p["eaves"].items() if "material" in d))
            if em:   # 처마 부속 종류별 재료(W2 아홉 번째 PR): 없으면 명세는 이전과 같다(부속은 지붕 재료를 따른다)
                spec["eave_materials"] = em
        if "parapet" in p:   # 평지붕 파라펫 조각(W2 스물두 번째 PR): (이름표, 종류, 평면 사각형, 바닥 z, 윗면 z). 없으면 명세는 이전과 같다
            from dobim_core.kinds import roof_parapet_pieces
            spec["parapet"] = tuple(roof_parapet_pieces(p))
        if "fall" in p:   # 평지붕 배수 물매(W2 스물두 번째 PR): 윗면이 기울어진 평면 하나. 없으면 명세는 이전과 같다
            spec["fall"] = (p["fall"]["dir"], p["fall"]["pct"])
        if "drains" in p:   # 평지붕 배수구(W2 스물두 번째 PR): ((x, y, 윗면 z), ...)와 지름. 없으면 명세는 이전과 같다
            from dobim_core.kinds import ROOF_DRAIN_D_DEFAULT, roof_geom
            a_, b_, c_ = roof_geom(p)["planes"][0]
            spec["drains"] = tuple((float(x), float(y), a_ * x + b_ * y + c_) for x, y in p["drains"])
            spec["drain_d"] = float(p.get("drain_diameter", ROOF_DRAIN_D_DEFAULT))
        vs = g.q(roof_voids, e) if g.q(opening_objs) else ()
        if vs:   # 지붕 개구(W2 열네 번째 PR): (개구 id, 이름, 수직/면, 경계 고리, 뚫는 면 번호들). 없으면 명세는 이전과 같다
            from dobim_core import hostvoid as HV
            gm = g.q(roof_gm, e)
            spec["voids"] = tuple((o, n, "face" if m == "face" else "vertical", lp, HV.roof_void(gm, p["thickness"], m, lp)["faces"]) for o, n, m, lp in vs)
            vp = _void_psets(g, [v[0] for v in vs])
            if vp:
                spec["void_psets"] = vp
        return spec
    if kind == "stackwall":   # 스택 벽(W2 일곱 번째 PR): 형상 없이 레벨 좌표계에 놓이고 부분 벽(구간 순서)을 집합한다
        return {"kind": kind, "name": p["name"], "level": g.q(stack_level, e), "parts": tuple(p["parts"]), "intent": intent}
    if kind == "curtwall":
        # 커튼월(W2 네 번째 PR, ARC-22): 자신은 형상 없이 레벨 좌표계에 놓이고, 부속(멀리언 IfcMember·패널 IfcPlate·문 패널 IfcDoor)이 IfcRelAggregates 로 매달린다.
        # 부속 = (이름표, IFC 클래스, 유형 또는 패널 종류, 평면 사각형(레벨 좌표), 바닥 z(레벨 기준), 높이). 이름표 순서라 결정적이다
        lay = g.q(curtain_layout, e)
        base = p.get("base_offset", 0.0)
        shp = lay.get("shapes", {})

        def sh(k):   # 모양 부속(W2 열 번째 PR: 경사 커튼 시스템·상단 윤곽에서 잘린 부속): 틀 원점 z 에 바닥 오프셋을 더한 모양을 끝에 붙인다. 없으면 부속 명세는 이전과 같다
            s_ = shp.get(k)
            return () if s_ is None else ((s_[0], (s_[1][0], s_[1][1], s_[1][2] + base)) + s_[2:7],)
        parts = [(f"m:{k}", "IfcMember", t, q, z0 + base, z1 - z0) + sh(k) for k, _d, t, q, z0, z1 in lay["mullions"]]
        parts += [(f"p:{k}", "IfcDoor" if pk == "door" else "IfcPlate", pk, q, z0 + base, z1 - z0) + sh(k) for k, pk, q, z0, z1, _c, _t in lay["panels"]]
        spec = {"kind": kind, "name": p["name"], "level": g.q(curtain_level, e), "parts": tuple(sorted(parts)), "intent": intent}
        if "materials" in p:   # 커튼월별 부속 재료(W2 열 번째 PR): 없으면 명세는 이전과 같다(기본 재료)
            spec["materials"] = tuple(sorted(p["materials"].items()))
        return spec
    if kind == "site":
        # 배치는 건물 아래 뼈대 대지 좌표계(원점·무회전)다. 방위·도로는 IFC 가 아니라 의도(DoBIM_Intent)로 왕복한다
        return {"kind": kind, "name": p["name"], "boundary": tuple(tuple(q) for q in p["boundary"]), "elevation": p["elevation"], "intent": intent}
    if kind == "space":
        spec = {"kind": kind, "name": p["name"], "category": p["category"], "level": g.q(space_level, e),
                "boundary": tuple(tuple(q) for q in p["boundary"]), "height": p["height"], "intent": intent}
        if "holes" in p:   # 섬 구멍(15단계): 구멍 있는 프로필로 나간다. 없으면 명세는 이전과 같다
            spec["holes"] = tuple(tuple(tuple(q) for q in o) for o in p["holes"])
        return spec
    if kind == "railing":
        # 배치는 레벨 좌표계(원점·무회전)이고 높이는 레벨 + base_offset 이므로 레벨 높이는 읽지 않는다
        spec = {"kind": kind, "name": p["name"], "level": g.q(railing_level, e), "path": tuple(tuple(q) for q in p["path"]), "height": p["height"],
                "base_offset": p["base_offset"], "rise": p["rise"], "intent": intent}
        if "parts" in p:   # 난간 유형(W2 열세 번째 PR): 부재 구성. 없으면 명세는 이전과 같다
            spec["parts"] = p["parts"]
        return spec
    if kind == "elevator":
        # 배치는 레벨 좌표계(원점·무회전)이고 카 바닥은 레벨 + base_offset 이므로 레벨 높이는 읽지 않는다
        spec = {"kind": kind, "name": p["name"], "level": g.q(elevator_level, e), "shaft": tuple(p["shaft"]), "cab": tuple(p["cab"]),
                "base_offset": p["base_offset"], "rise": p["rise"], "intent": intent}
        if "rotation" in p:   # 승강기 회전(W2 열일곱 번째 PR). 없으면 명세는 이전과 같다
            spec["rotation"] = p["rotation"]
        return spec
    if kind == "stair" and "sketch" in p:   # 스케치 계단(W2 열일곱 번째 PR): 걸음선 대신 sketch
        sk = p["sketch"]
        return {"kind": kind, "name": p["name"], "level": g.q(stair_level, e), "width": p["width"], "height": p["height"], "risers": p["risers"],
                "base_offset": p["base_offset"],
                "sketch": (("bulges", tuple(sk.get("bulges", ()))), *((("corners", tuple(sk["corners"])),) if "corners" in sk else ()),   # 꺾인 모서리(W2 스무 번째 PR): 없으면 명세는 이전과 같다
                           *((("landings", tuple(sk["landings"])),) if "landings" in sk else ()),   # 층참 변·균형 돌음(W2 스물세 번째 PR): 없으면 명세는 이전과 같다
                           *((("balanced", tuple(sk["balanced"])),) if "balanced" in sk else ()),
                           ("path", tuple(tuple(q) for q in sk["path"]))),
                "intent": intent}
    if kind == "stair" and "spiral" in p:   # 방사 계단(W2 열일곱 번째 PR): 걸음선 대신 spiral
        sp = p["spiral"]
        return {"kind": kind, "name": p["name"], "level": g.q(stair_level, e), "width": p["width"], "height": p["height"], "risers": p["risers"],
                "base_offset": p["base_offset"], "spiral": (("center", tuple(sp["center"])), ("r_in", sp["r_in"]), ("start", sp["start"]), ("sweep", sp["sweep"])),
                "intent": intent}
    if kind == "stair":
        # 배치는 레벨 좌표계(원점·무회전)이고 아래 끝은 레벨 + base_offset 이므로 레벨 높이는 읽지 않는다
        spec = {"kind": kind, "name": p["name"], "level": g.q(stair_level, e), "run": tuple(p["run"]), "width": p["width"],
                "height": p["height"], "risers": p["risers"], "base_offset": p["base_offset"], "intent": intent}
        if "turn" in p:   # 직선 계단이면 명세는 이전과 같다
            spec["turn"] = tuple(sorted(p["turn"].items()))
        return spec
    w = g.q(door_host, e)
    t = el(g, w)["params"]["thickness"]
    spec = {"kind": kind, "name": p["name"], "host": w, "level": g.q(wall_level, w), "offset": p["offset"],
            "sill": p["sill"], "width": p["width"], "height": p["height"], "wall_thickness": t, "intent": intent}
    if "arc" in el(g, w)["params"]:   # 곡선 벽 호스트(W2 ARC-04): 축 위 점·접선에 놓는다. 직선 호스트면 명세는 이전과 같다
        spec["at"] = g.q(host_at, w, p["offset"], p["width"] / 2.0)
    return spec


def derived_bundle(g, eids):
    """모든 파생값. 차등 시험이 증분 그래프와 기준 그래프를 비교하는 대상이다."""
    out = {"door": g.q(schedule_sorted, "door"), "wall": g.q(schedule_sorted, "wall"),
           "door_summary": g.q(schedule_summary, "door"), "wall_summary": g.q(schedule_summary, "wall"),
           "stats": g.q(model_stats), "specs": {}, "trim": {}, "world": {}}
    for e in eids:
        k = el(g, e)["kind"]
        if k in NON_IFC_KINDS:
            continue
        out["specs"][e] = g.q(ifc_spec, e)
        if k == "wall":
            out["trim"][e] = g.q(wall_trim, e)
        elif k == "door":
            out["world"][e] = (g.q(door_world, e), g.q(door_status, e))
    return out
