# SPDX-License-Identifier: MPL-2.0
"""순수 파생 질의. 시각·난수·파일 접근 금지 (ADR-0006 불변식, 시험이 AST로 검사한다).

단위는 mm. math.hypot, sin, cos 처럼 플랫폼 libm 에 달린 함수를 쓰지 않는다.
sqrt 와 사칙연산은 IEEE 754 로 결정적이다.
"""
from __future__ import annotations

from math import sqrt

from dobim_core.family import spec as FS

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


def psets(g, eid):
    return g.input(("pset", eid))


def material(g, eid):
    """재료 이름표. 층 구성이 있는 벽·슬래브는 층 구성이 재료를 정한다(가장 두꺼운 층의 재료): 층 구성은 요소 파라미터(의도)라 이 값도 그 입력 하나에서 나온다."""
    row = g.input(("el", eid))
    if row is not None and row["kind"] in ("wall", "slab") and "layers" in row["params"]:
        return layers_label(row["params"]["layers"])
    return g.input(("mat", eid))


# ---------------------------------------------------------------- 벽
def wall_frame(g, w):
    x1, y1, x2, y2 = el(g, w)["params"]["axis"]
    dx, dy = x2 - x1, y2 - y1
    length = sqrt(dx * dx + dy * dy)
    return (x1, y1, dx / length, dy / length, length)


def wall_level(g, w):
    return rel_out(g, "on_level", w)[0]


def wall_joins(g, w):
    """조인 상대 목록. joins 는 src<dst 로 저장되므로 양쪽을 합친다."""
    return tuple(sorted(rel_out(g, "joins", w) + rel_in(g, "joins", w)))


def join_point(g, a, b):
    """두 벽 축선의 교점. 벽 축선에서 계산하므로 벽끼리 순환하지 않는다 (ADR-0006)."""
    x1, y1, cx1, sy1, _ = g.q(wall_frame, a)
    x2, y2, cx2, sy2, _ = g.q(wall_frame, b)
    det = cx1 * sy2 - sy1 * cx2
    if -PARALLEL_EPS < det < PARALLEL_EPS:
        return None
    t = ((x2 - x1) * sy2 - (y2 - y1) * cx2) / det
    return (x1 + t * cx1, y1 + t * sy1)


def wall_trim(g, w):
    """(시작, 끝, 상태): 벽 지역 x 좌표. 원점은 축선 시작점이다."""
    x, y, c, s, length = g.q(wall_frame, w)
    start, end = 0.0, length
    for other in g.q(wall_joins, w):
        p = g.q(join_point, min(w, other), max(w, other))
        if p is None:
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


def wall_extent(g, w):
    """벽 형상의 지역 x 범위(시작, 끝). 조인 자르기(wall_trim)가 축선 교점에서 자르면 L 조인 바깥 모서리에 반 두께 노치가 남는다.
    그래서 id 가 작은 벽이 상대 벽 반 두께(/|sin|)만큼 더 나가 모서리를 닫는다. T 조인(상대 벽 중간에 닿음)·얕은 각·퇴화한 조인은 연장하지 않는다.
    IFC 투영의 벽 윤곽(wall_footprint)과 도면(drawing.wall_range)이 모두 이 값을 쓴다: 도면과 IFC 가 같은 형상이다.
    wall_trim(논리 길이: 치수 참조·일람표 길이·문 범위)은 그대로 축선 교점이다."""
    start, end, status = g.q(wall_trim, w)
    if not CORNER_EXTEND or status != "ok":
        return (start, end)
    x, y, c, s, length = g.q(wall_frame, w)
    for o in g.q(wall_joins, w):
        if not w < o:
            continue
        pt = g.q(join_point, w, o)
        if pt is None:
            continue
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


def wall_hosted(g, w):
    return rel_out(g, "hosts", w)


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
    x, y, c, s, _ = g.q(wall_frame, w)
    p = el(g, d)["params"]
    _, elev = g.q(level_info, g.q(wall_level, w))
    off = p["offset"]
    return (x + off * c, y + off * s, elev + p["sill"])


def door_status(g, d):
    """조인으로 벽이 줄어 문이 벽 밖으로 나갔는지 표시한다. 조용히 옮기지 않는다."""
    w = g.q(door_host, d)
    start, end, _ = g.q(wall_trim, w)
    p = el(g, d)["params"]
    half = p["width"] / 2.0
    return "ok" if start <= p["offset"] - half and p["offset"] + half <= end else "outside_trimmed_host"


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
    opening = 0.0
    hosted = g.q(wall_hosted, w)
    for d in hosted:
        dp = el(g, d)["params"]
        opening += dp["width"] * dp["height"]
    return (w, p["name"], lname, elev, length, p["height"], p["thickness"], gross - opening, len(hosted),
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


def model_stats(g):
    out = {k: g.q(count, k) for k in ("level", "wall", "door")}
    for k in ("slab", "stair", "space", "railing", "elevator", "roof", "site"):
        n = g.q(count, k)
        if n:   # 슬래브·계단·공간·난간·대지가 없는 프로젝트의 응답·해시는 그대로다
            out[k] = n
    return out


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
    elif row["kind"] in ("slab", "stair", "space", "railing", "elevator", "roof"):
        out["level"] = g.q({"slab": slab_level, "stair": stair_level, "space": space_level, "railing": railing_level, "elevator": elevator_level, "roof": roof_level}[row["kind"]], e)
    elif row["kind"] == "door":
        out["host"] = g.q(door_host, e)
    return out


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
        return {**FS.finst_spec(el(g, tid)["params"], p, tid, h, g.q(finst_level, e), host, tolerant=True), "intent": intent}
    if kind == "wall":
        x, y, c, s, _ = g.q(wall_frame, e)
        spec = {"kind": kind, "name": p["name"], "level": g.q(wall_level, e), "origin": (x, y), "xdir": (c, s),
                "footprint": g.q(wall_footprint, e), "height": p["height"], "intent": intent}
        if "layers" in p:   # 층 구성이 없으면 명세는 이전과 같다
            spec["layers"] = tuple((x["material"], x["thickness"]) for x in p["layers"])
        return spec
    if kind == "slab":
        # 배치는 레벨 좌표계(원점·무회전)이고 윗면은 레벨 + top_offset 이므로 레벨 높이는 읽지 않는다(레벨을 옮기면 배치가 따라간다)
        spec = {"kind": kind, "name": p["name"], "level": g.q(slab_level, e), "boundary": tuple(tuple(q) for q in p["boundary"]),
                "thickness": p["thickness"], "top_offset": p["top_offset"], "intent": intent}
        if "layers" in p:
            spec["layers"] = tuple((x["material"], x["thickness"]) for x in p["layers"])
        if "openings" in p:   # 구멍이 없으면 명세는 이전과 같다
            spec["openings"] = tuple(tuple(tuple(q) for q in o) for o in p["openings"])
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
        return spec
    if kind == "site":
        # 배치는 건물 아래 뼈대 대지 좌표계(원점·무회전)다. 방위·도로는 IFC 가 아니라 의도(DoBIM_Intent)로 왕복한다
        return {"kind": kind, "name": p["name"], "boundary": tuple(tuple(q) for q in p["boundary"]), "elevation": p["elevation"], "intent": intent}
    if kind == "space":
        return {"kind": kind, "name": p["name"], "category": p["category"], "level": g.q(space_level, e),
                "boundary": tuple(tuple(q) for q in p["boundary"]), "height": p["height"], "intent": intent}
    if kind == "railing":
        # 배치는 레벨 좌표계(원점·무회전)이고 높이는 레벨 + base_offset 이므로 레벨 높이는 읽지 않는다
        return {"kind": kind, "name": p["name"], "level": g.q(railing_level, e), "path": tuple(tuple(q) for q in p["path"]), "height": p["height"],
                "base_offset": p["base_offset"], "rise": p["rise"], "intent": intent}
    if kind == "elevator":
        # 배치는 레벨 좌표계(원점·무회전)이고 카 바닥은 레벨 + base_offset 이므로 레벨 높이는 읽지 않는다
        return {"kind": kind, "name": p["name"], "level": g.q(elevator_level, e), "shaft": tuple(p["shaft"]), "cab": tuple(p["cab"]),
                "base_offset": p["base_offset"], "rise": p["rise"], "intent": intent}
    if kind == "stair":
        # 배치는 레벨 좌표계(원점·무회전)이고 아래 끝은 레벨 + base_offset 이므로 레벨 높이는 읽지 않는다
        spec = {"kind": kind, "name": p["name"], "level": g.q(stair_level, e), "run": tuple(p["run"]), "width": p["width"],
                "height": p["height"], "risers": p["risers"], "base_offset": p["base_offset"], "intent": intent}
        if "turn" in p:   # 직선 계단이면 명세는 이전과 같다
            spec["turn"] = tuple(sorted(p["turn"].items()))
        return spec
    w = g.q(door_host, e)
    t = el(g, w)["params"]["thickness"]
    return {"kind": kind, "name": p["name"], "host": w, "level": g.q(wall_level, w), "offset": p["offset"],
            "sill": p["sill"], "width": p["width"], "height": p["height"], "wall_thickness": t, "intent": intent}


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
