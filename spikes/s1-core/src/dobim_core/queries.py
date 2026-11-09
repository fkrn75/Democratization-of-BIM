# SPDX-License-Identifier: MPL-2.0
"""순수 파생 질의. 시각·난수·파일 접근 금지 (ADR-0006 불변식, 시험이 AST로 검사한다).

단위는 mm. math.hypot, sin, cos 처럼 플랫폼 libm 에 달린 함수를 쓰지 않는다.
sqrt 와 사칙연산은 IEEE 754 로 결정적이다.
"""
from __future__ import annotations

import zlib
from math import sqrt

from dobim_core.family import spec as FS

JOIN_MAX_EXT = 1000.0   # 조인 지점이 벽 끝에서 이 거리 안일 때만 조인이 성립
MIN_TRIMMED = 1.0       # 조인 후 벽 길이 하한. 미달이면 조인을 무시하고 경고 상태로 둔다
PARALLEL_EPS = 1e-9


NON_IFC_KINDS = frozenset({"view", "dim", "tag"})   # IFC 로 투영하지 않는 의도 요소(뷰 정의 S3, 치수·태그 S5). 신원·속성셋·재료가 없다

NB = 64  # 일람표 버킷 수. 요소 하나가 바뀌면 그 버킷만 다시 계산한다


def bucket_of(eid: str) -> int:
    """결정적 버킷 번호. 파이썬 hash() 는 프로세스마다 달라 쓰지 않는다."""
    return zlib.crc32(eid.encode("ascii")) % NB


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


def wall_length(g, w):
    start, end, _ = g.q(wall_trim, w)
    return end - start


def wall_footprint(g, w):
    """벽 지역 좌표의 평면 윤곽. 벽을 평행 이동해도 조인이 그대로면 값이 같다(조기 차단)."""
    start, end, _ = g.q(wall_trim, w)
    half = el(g, w)["params"]["thickness"] / 2.0
    return ((start, -half), (end, -half), (end, half), (start, half))


def wall_hosted(g, w):
    return rel_out(g, "hosts", w)


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
    return {k: g.q(count, k) for k in ("level", "wall", "door")}


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
        return {"kind": kind, "name": p["name"], "level": g.q(wall_level, e), "origin": (x, y), "xdir": (c, s),
                "footprint": g.q(wall_footprint, e), "height": p["height"], "intent": intent}
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
