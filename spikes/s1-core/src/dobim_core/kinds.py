# SPDX-License-Identifier: MPL-2.0
"""요소 종류·관계 종류·효과 태그의 단일 레지스트리 (G1 선행 리팩터링, docs/integration-g1.md 5절 1항).

스파이크마다 "요소 종류 목록"이 따로 있었다(엔진의 투영 순서, IFC 클래스 표, 병합의 화이트리스트, 가져오기의 분류표, 질의의 비 IFC 집합).
종류를 하나 더하면 그 목록을 전부 고쳐야 했고, 하나라도 빠지면 합친 뒤에만 드러났다(병합이 view 를 거부, 도면이 패밀리 문에서 KeyError).
이 모듈이 정본이다. 다른 모듈은 이 표에서 파생한다. ifcopenshell 을 import 하지 않는다(병합·질의 모듈이 가볍게 불러야 한다).

세 가지를 한곳에 둔다.
  ELEMENT_KINDS  요소 종류: IFC 신원 여부, 고정 IFC 클래스, 투영 대상 여부, 생성·투영 순서, 파라미터 검사
  REL_KINDS      관계 종류: 양 끝 종류, 끝이 지워져도 남을 수 있는지(succ), 저작 확인이 필요한지
  EFFECTS        효과 태그: 뒤집는 방법과 그래프 입력 키 (엔진의 invert_effect·input_keys_of·_apply_effect 가 같은 표를 쓴다)
"""
from __future__ import annotations

import zlib
from math import isfinite, sqrt
from typing import Callable, NamedTuple

from dobim_core import solids as SO

ID_LEN = 22
NAME_MAX = 64
NB = 64  # 일람표 버킷 수. 요소 하나가 바뀌면 그 버킷만 다시 계산한다


def bucket_of(eid: str) -> int:
    """결정적 버킷 번호. 파이썬 hash() 는 프로세스마다 달라 쓰지 않는다."""
    return zlib.crc32(eid.encode("ascii")) % NB


# ---------------------------------------------------------------- 값 검사 도우미 (merge 의 행 검사와 공유)
def num_ok(v) -> bool:
    if type(v) is int:
        return -(2**63) <= v < 2**63
    return type(v) is float and isfinite(v)


def text_ok(v, lo=0, hi=None) -> bool:
    return isinstance(v, str) and len(v) >= lo and (hi is None or len(v) <= hi) and "\x00" not in v


def id_ok(v) -> bool:
    return isinstance(v, str) and len(v) == ID_LEN and all(c.isascii() and (c.isalnum() or c in "_$") for c in v)


def _pt_ok(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and all(num_ok(x) for x in v)


def _json_ok(v, depth=0) -> bool:
    """JSON 으로 쓸 수 있는 값인가(유한한 수, 문자열 NUL 없음, 깊이 제한)."""
    if depth > 12:
        return False
    if v is None or type(v) is bool:
        return True
    if isinstance(v, (int, float)):
        return num_ok(v)
    if isinstance(v, str):
        return "\x00" not in v
    if isinstance(v, list):
        return all(_json_ok(x, depth + 1) for x in v)
    if isinstance(v, dict):
        return all(isinstance(k, str) and _json_ok(x, depth + 1) for k, x in v.items())
    return False


# ---------------------------------------------------------------- 파라미터 검사 (커맨드가 만들 수 있는 모양인가: 병합이 상대 로그를 믿지 않고 거른다)
def _check_fields(kind: str, p, spec: dict[str, Callable]) -> str | None:
    if not isinstance(p, dict) or set(p) != set(spec):
        return f"{kind} 의 파라미터 이름이 다르다"
    for k, ok in spec.items():
        if not ok(p[k]):
            return f"{kind}.{k} 값이 올바르지 않다"
    return None


def _name(v) -> bool:
    return text_ok(v, 1, NAME_MAX)


def _pos(v) -> bool:
    return num_ok(v) and v > 0


# ---------------------------------------------------------------- 선택 파라미터 (없으면 키 자체가 없다: 입력이 없는 프로젝트의 저장 형식·해시는 그대로)
LAYERS_MAX = 8                 # 층 구성의 층 수 상한
LAYER_SUM_TOL = 0.01           # 층 두께 합과 요소 두께가 이만큼(mm) 안에서 같아야 한다


def _check_opt(kind: str, p, spec: dict[str, Callable], opt: dict[str, Callable]) -> str | None:
    """필수 파라미터는 _check_fields 처럼 정확히 맞아야 하고, 선택 파라미터(opt)는 있으면 값이 올바라야 한다(없어도 된다)."""
    if not isinstance(p, dict):
        return f"{kind} 의 파라미터가 객체가 아니다"
    base = {k: v for k, v in p.items() if k not in opt}
    if (err := _check_fields(kind, base, spec)) is not None:
        return err
    for k, ok in opt.items():
        if k in p and not ok(p[k]):
            return f"{kind}.{k} 값이 올바르지 않다"
    return None


def layers_ok(v) -> bool:
    """층 구성 = [{"material": 재료 이름, "thickness": 두께 mm}, ...] 1~LAYERS_MAX 개."""
    return (isinstance(v, list) and 1 <= len(v) <= LAYERS_MAX
            and all(isinstance(x, dict) and set(x) == {"material", "thickness"} and text_ok(x["material"], 1, NAME_MAX) and _pos(x["thickness"]) for x in v))


def layers_total(layers) -> float:
    return sum(x["thickness"] for x in layers)


def layers_label(layers) -> str:
    """층 구성을 대표하는 재료 이름: 가장 두꺼운 층(같으면 앞쪽)의 재료. 재료 일람표·외부 마감 판정이 읽는 이름표다."""
    best = layers[0]
    for x in layers[1:]:
        if x["thickness"] > best["thickness"]:
            best = x
    return best["material"]


def layer_bounds(layers) -> list:
    """층 경계의 누적 위치(첫 층의 바깥 면 = 0, 마지막 = 총 두께). 길이 = 층 수 + 1."""
    out, acc = [0.0], 0.0
    for x in layers:
        acc += x["thickness"]
        out.append(acc)
    return out


def _level_params(p):
    return _check_fields("level", p, {"name": _name, "elevation": num_ok})


def _wall_params(p):
    def axis(v):   # 벽 길이 1mm 이상(wall.create 의 규칙)
        return (isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v)
                and (v[2] - v[0]) ** 2 + (v[3] - v[1]) ** 2 >= 1.0)
    err = _check_opt("wall", p, {"name": _name, "axis": axis, "thickness": _pos, "height": _pos}, {"layers": layers_ok, "openings": lambda v: isinstance(v, list)})
    if err is None and "layers" in p and abs(layers_total(p["layers"]) - p["thickness"]) > LAYER_SUM_TOL:
        return "wall.layers 두께의 합이 벽 두께와 다르다"
    if err is None and "openings" in p:   # 모양 개구부(18단계): 벽 입면 사각형(축 길이 x 높이) 안, 서로 겹치지 않는다
        ax = p["axis"]
        ln = sqrt((ax[2] - ax[0]) ** 2 + (ax[3] - ax[1]) ** 2)
        why = SO.openings_problem([(0.0, 0.0), (ln, 0.0), (ln, p["height"]), (0.0, p["height"])], p["openings"])
        if why:
            return f"wall.openings: {why}"
    return err


def _door_params(p):
    return _check_opt("door", p, {"name": _name, "width": _pos, "height": _pos, "sill": lambda v: num_ok(v) and v >= 0, "offset": num_ok}, {"leaf": _pos})


_SCALE = lambda v: num_ok(v) and v > 0   # noqa: E731


def _crop_ok(v) -> bool:
    """뷰 잘라내기 사각형 [x1, y1, x2, y2](뷰 좌표 mm: 평면은 x,y / 단면·입면은 s,z). x1<x2, y1<y2."""
    return isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v) and v[0] < v[2] and v[1] < v[3]


_TRUE = lambda v: v is True   # noqa: E731
# 뷰의 선택 파라미터(없으면 키 자체가 없다): crop 잘라내기(부위 상세 뷰), dims 승강기·계단 치수 자동 표기(평면·단면), finish 입면의 외부 마감 해치·재료 문자(입면)
VIEW_OPTIONAL = {"plan": ("crop", "dims"), "section": ("with_site", "crop", "dims"), "elevation": ("crop", "finish"), "site": ()}
_VIEW_OPT_CHECK = {"with_site": _TRUE, "crop": _crop_ok, "dims": _TRUE, "finish": _TRUE}


def _view_params(p):
    t = p.get("type") if isinstance(p, dict) else None
    if t == "plan":
        return _check_opt("view", p, {"name": _name, "type": lambda v: v == "plan", "level": id_ok, "cut_height": num_ok,
                                      "view_depth": lambda v: num_ok(v) and v >= 0, "scale": _SCALE}, {k: _VIEW_OPT_CHECK[k] for k in VIEW_OPTIONAL["plan"]})
    if t == "section":
        return _check_opt("view", p, {"name": _name, "type": lambda v: v == "section", "p1": _pt_ok, "p2": _pt_ok,
                                      "depth": _pos, "flip": lambda v: type(v) is bool, "scale": _SCALE}, {k: _VIEW_OPT_CHECK[k] for k in VIEW_OPTIONAL["section"]})
    if t == "elevation":
        return _check_opt("view", p, {"name": _name, "type": lambda v: v == "elevation", "dir": lambda v: v in ("N", "E", "S", "W"),
                                      "scale": _SCALE}, {k: _VIEW_OPT_CHECK[k] for k in VIEW_OPTIONAL["elevation"]})
    if t == "site":   # 배치도: 모델 전체를 위에서 본 뷰라 레벨·절단 높이가 없다
        return _check_fields("view", p, {"name": _name, "type": lambda v: v == "site", "scale": _SCALE})
    return "view 의 종류가 올바르지 않다"


def _ref(v) -> bool:
    return text_ok(v, 1, 80)


def _dim_params(p):
    if isinstance(p, dict) and "at" in p:   # 높이 치수만 at 이 있다
        base = {k: v for k, v in p.items() if k != "at"}
        if not _pt_ok(p["at"]):
            return "dim.at 값이 올바르지 않다"
    else:
        base = p
    return _check_fields("dim", base, {"name": _name, "view": id_ok, "a": _ref, "b": _ref, "off": num_ok, "home": _pt_ok,
                                       "dirs": lambda v: _json_ok(v) and isinstance(v, dict)})


def _tag_params(p):
    return _check_fields("tag", p, {"name": _name, "view": id_ok, "target": id_ok,
                                    "fields": lambda v: isinstance(v, list) and 1 <= len(v) <= 4 and all(text_ok(x, 1, 16) for x in v),
                                    "off": _pt_ok, "home": _pt_ok})


def _ftype_params(p):
    return _check_fields("ftype", p, {"name": lambda v: text_ok(v, 1, 255), "recipe": lambda v: isinstance(v, dict) and _json_ok(v),
                                      "recipe_hash": lambda v: text_ok(v, 1, 128), "type_values": lambda v: isinstance(v, dict) and _json_ok(v)})


def _finst_params(p):
    if not isinstance(p, dict):
        return "finst 의 파라미터가 객체가 아니다"
    common = {"name": lambda v: text_ok(v, 1, 255), "type": id_ok, "values": lambda v: isinstance(v, dict) and _json_ok(v),
              "rotation": num_ok, "elevation": num_ok}
    spec = dict(common, **({"offset": num_ok, "width": lambda v: num_ok(v) and v >= 0, "height": lambda v: num_ok(v) and v >= 0}
                           if "offset" in p else {"position": lambda v: isinstance(v, list) and len(v) in (2, 3) and all(num_ok(x) for x in v)}))
    return _check_fields("finst", p, spec)


# ---------------------------------------------------------------- 슬래브 경계 다각형 (순수 기하: 정수 비교가 아니라 외적 부호만 쓴다)
SLAB_PTS_MIN, SLAB_PTS_MAX = 3, 256


def polygon_area2(pts) -> float:
    """다각형의 부호 있는 면적의 2배(양수 = 반시계)."""
    n = len(pts)
    return sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))


def _seg_cross(a, b, c, d) -> bool:
    """선분 ab 와 cd 가 (끝점 접촉 포함) 만나는가."""
    def o(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (v > 0) - (v < 0)

    def on(p, q, r):   # 공선일 때 r 이 선분 pq 의 범위 안인가
        return min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and min(p[1], q[1]) <= r[1] <= max(p[1], q[1])
    o1, o2, o3, o4 = o(a, b, c), o(a, b, d), o(c, d, a), o(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return (o1 == 0 and on(a, b, c)) or (o2 == 0 and on(a, b, d)) or (o3 == 0 and on(c, d, a)) or (o4 == 0 and on(c, d, b))


def polygon_problem(pts) -> str | None:
    """슬래브 경계가 쓸 수 있는 모양인가(안 되면 이유 문장): 꼭짓점 3~256개, 면적 0 아님, 같은 꼭짓점 반복 없음, 자기 교차 없음."""
    n = len(pts)
    if not SLAB_PTS_MIN <= n <= SLAB_PTS_MAX:
        return f"꼭짓점은 {SLAB_PTS_MIN}~{SLAB_PTS_MAX}개여야 한다(현재 {n}개)"
    if len({(p[0], p[1]) for p in pts}) != n:
        return "같은 꼭짓점이 반복된다"
    if abs(polygon_area2(pts)) <= 1e-6:
        return "면적이 0 이다"
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue   # 이웃한 변은 꼭짓점을 공유한다
            if _seg_cross(a, b, pts[j], pts[(j + 1) % n]):
                return "경계가 자기 자신과 교차한다"
    return None


SLAB_OPENINGS_MAX = 32   # 슬래브 구멍 수 상한


def _poly_pts_ok(v) -> bool:
    return isinstance(v, list) and SLAB_PTS_MIN <= len(v) <= SLAB_PTS_MAX and all(_pt_ok(x) for x in v) and polygon_area2(v) > 0   # 반시계로 저장한다


def point_in_polygon(pt, poly) -> int:
    """점이 다각형 안(1)·경계 위(0)·밖(-1). 끝점 접촉은 경계로 본다."""
    x, y = pt
    n = len(poly)
    inside = False
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        cr = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
        if cr == 0 and min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1]):
            return 0
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) / (b[1] - a[1]) * (b[0] - a[0]):
            inside = not inside
    return 1 if inside else -1


def openings_problem(boundary, openings) -> str | None:
    """슬래브 구멍이 쓸 수 있는가(안 되면 이유 문장): 각 구멍은 올바른 다각형이고 슬래브 경계 안쪽에 있으며(경계와 만나지 않는다) 서로 겹치지 않는다."""
    if len(openings) > SLAB_OPENINGS_MAX:
        return f"구멍은 {SLAB_OPENINGS_MAX}개 이하여야 한다(현재 {len(openings)}개)"
    for i, o in enumerate(openings):
        why = polygon_problem(o)
        if why:
            return f"{i + 1}번 구멍이 올바르지 않다: {why}"
        if any(point_in_polygon(q, boundary) != 1 for q in o):
            return f"{i + 1}번 구멍이 슬래브 경계 안쪽에 있지 않다"
        n, m = len(o), len(boundary)
        if any(_seg_cross(o[a], o[(a + 1) % n], boundary[b], boundary[(b + 1) % m]) for a in range(n) for b in range(m)):
            return f"{i + 1}번 구멍이 슬래브 경계와 만난다"
    for i in range(len(openings)):
        for j in range(i + 1, len(openings)):
            a, b = openings[i], openings[j]
            na, nb = len(a), len(b)
            if (any(point_in_polygon(q, b) >= 0 for q in a) or any(point_in_polygon(q, a) >= 0 for q in b)
                    or any(_seg_cross(a[x], a[(x + 1) % na], b[y], b[(y + 1) % nb]) for x in range(na) for y in range(nb))):
                return f"{i + 1}번과 {j + 1}번 구멍이 겹친다"
    return None


SLAB_DRAINS_MAX = 32      # 지붕 슬래브의 배수구(드레인) 수 상한
ROOF_FALL_PCT_MAX = 100.0  # 배수 물매 상한(%)


def fall_ok(v) -> bool:
    """배수 물매 = {"dir": 물이 흐르는 방향 도(+x 가 0, 반시계), "pct": 물매 %(0 초과 100 이하)}."""
    return isinstance(v, dict) and set(v) == {"dir", "pct"} and num_ok(v["dir"]) and num_ok(v["pct"]) and 0 < v["pct"] <= ROOF_FALL_PCT_MAX


def _drains_ok(v) -> bool:
    return isinstance(v, list) and 1 <= len(v) <= SLAB_DRAINS_MAX and all(_pt_ok(q) for q in v)


PARAPET_H_MAX = 3000.0     # 파라펫 높이 상한 mm
PARAPET_T_MAX = 1000.0     # 파라펫 두께 상한 mm


def parapet_ok(v) -> bool:
    """파라펫 입력 = {"height": 슬래브 윗면 위로 올라온 높이 mm, "thickness": 두께 mm(슬래브 경계 안쪽으로)}."""
    return (isinstance(v, dict) and set(v) == {"height", "thickness"} and _pos(v["height"]) and _pos(v["thickness"])
            and v["height"] <= PARAPET_H_MAX and v["thickness"] <= PARAPET_T_MAX)


def parapet_ring(boundary, t):
    """파라펫의 안쪽 고리: 슬래브 경계(반시계)의 각 변을 안쪽으로 t 만큼 민 직선들의 이웃 교점(마이터). 쓸 수 없으면 None(너무 두꺼워 접히거나 자기 교차).
    순수 함수: 그리기·IFC 가 같은 값을 쓴다. 꼭짓점 번호는 바깥 경계와 같다."""
    n = len(boundary)
    nrm = []
    for i in range(n):
        a, b = boundary[i], boundary[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = sqrt(dx * dx + dy * dy)
        if ln < 1e-9:
            return None
        nrm.append((-dy / ln, dx / ln))
    ring = []
    for i in range(n):
        n0, n1 = nrm[i - 1], nrm[i]
        den = 1.0 + n0[0] * n1[0] + n0[1] * n1[1]
        if den < 1e-6:   # 거의 180도로 되꺾이는 꼭짓점: 마이터가 무한히 길다
            return None
        ring.append((boundary[i][0] + t * (n0[0] + n1[0]) / den, boundary[i][1] + t * (n0[1] + n1[1]) / den))
    if polygon_problem(ring) or polygon_area2(ring) <= 0:
        return None
    for i in range(n):   # 안쪽 변이 바깥 변과 같은 방향이어야 한다(두께가 변보다 커서 접히면 거꾸로 뒤집힌다)
        a, b = boundary[i], boundary[(i + 1) % n]
        c, d = ring[i], ring[(i + 1) % n]
        if (b[0] - a[0]) * (d[0] - c[0]) + (b[1] - a[1]) * (d[1] - c[1]) <= 0:
            return None
    return ring


def parapet_problem(p) -> str | None:
    """슬래브(use=roof)의 파라펫이 쓸 수 있는가(안 되면 이유 문장): 안쪽 고리가 만들어지고, 구멍·배수구를 가리지 않는다."""
    par = p["parapet"]
    ring = parapet_ring(p["boundary"], par["thickness"])
    if ring is None:
        return "두께가 슬래브 경계에 비해 너무 두꺼워 안쪽 고리를 만들 수 없다"
    if any(point_in_polygon(q, ring) < 0 for q in p.get("drains", ())):
        return "배수구가 파라펫 위에 있다"
    for o in p.get("openings", ()):
        if any(point_in_polygon(q, ring) != 1 for q in o):
            return "구멍이 파라펫(안쪽 고리) 안쪽에 있지 않다"
    return None


def _slab_params(p):
    err = _check_opt("slab", p, {"name": _name, "boundary": _poly_pts_ok, "thickness": _pos, "top_offset": num_ok},
                     {"layers": layers_ok, "openings": lambda v: isinstance(v, list) and 1 <= len(v) <= SLAB_OPENINGS_MAX and all(_poly_pts_ok(o) for o in v),
                      "use": lambda v: v == "roof", "fall": fall_ok, "drains": _drains_ok, "parapet": parapet_ok})
    if err is None and ("fall" in p or "drains" in p or "parapet" in p) and p.get("use") != "roof":
        return "slab.fall·slab.drains·slab.parapet 은 지붕 슬래브(use=roof)에만 둘 수 있다"
    if err is None and "drains" in p:
        for i, q in enumerate(p["drains"]):   # 배수구는 슬래브 경계 안쪽이고 구멍 안이 아니어야 한다
            if point_in_polygon(q, p["boundary"]) != 1 or any(point_in_polygon(q, o) >= 0 for o in p.get("openings", ())):
                return f"slab.drains: {i + 1}번 배수구가 슬래브 위(경계 안쪽, 구멍 밖)에 있지 않다"
    if err is None and "layers" in p and abs(layers_total(p["layers"]) - p["thickness"]) > LAYER_SUM_TOL:
        return "slab.layers 두께의 합이 슬래브 두께와 다르다"
    if err is None and "openings" in p and (why := openings_problem(p["boundary"], p["openings"])):
        return f"slab.openings: {why}"
    if err is None and "parapet" in p and (why := parapet_problem(p)):
        return f"slab.parapet: {why}"
    return err


STAIR_RISERS_MIN, STAIR_RISERS_MAX = 2, 100
STAIR_LANDING_T = 150.0   # 층참 두께 mm(꺾인 계단의 층참 판. 층참 위 단 묶음의 밑면 여유도 같다)
STAIR_TURN_TYPES = ("L", "U")
STAIR_SIDES = ("left", "right")
STAIR_GAP_DEFAULT = 100.0   # U형 계단 두 단 묶음 사이 간격 mm
STAIR_WINDERS_MIN, STAIR_WINDERS_MAX = 2, 12   # 돌음(와인더) 디딤판 수. 1 은 층참과 같으므로 2 부터
STAIR_TURN_KEYS = frozenset(("type", "side", "risers_a", "landing", "gap"))


def _turn_ok(v) -> bool:
    """꺾임(층참) 입력: {"type": "L"|"U", "side": "left"|"right", "risers_a": 층참 아래 단 묶음의 챌판 수, "landing": 층참 깊이 mm 또는 null(= 폭), "gap": U형 간격 mm(L 은 0)}.
    선택 키 "winders"(정수 2~12)가 있으면 층참 대신 그 수의 쐐기 디딤판(돌음 계단)으로 회전부를 채운다(elements-design.md 23.3)."""
    return (isinstance(v, dict) and (set(v) == STAIR_TURN_KEYS or set(v) == STAIR_TURN_KEYS | {"winders"}) and v["type"] in STAIR_TURN_TYPES
            and v["side"] in STAIR_SIDES and type(v["risers_a"]) is int and 2 <= v["risers_a"] <= STAIR_RISERS_MAX and (v["landing"] is None or _pos(v["landing"]))
            and num_ok(v["gap"]) and v["gap"] >= 0 and (v["type"] == "U" or v["gap"] == 0)
            and ("winders" not in v or (type(v["winders"]) is int and STAIR_WINDERS_MIN <= v["winders"] <= STAIR_WINDERS_MAX)))


def stair_turn_problem(p) -> str | None:
    """꺾인 계단의 교차 검사(챌판 분배·층참 깊이). 안 되면 이유 문장."""
    t = p.get("turn")
    if t is None:
        return None
    m = t.get("winders")
    if m is not None:   # 돌음: 위 단 묶음 챌판 = 전체 - risers_a - winders + 1 (쐐기 m 개 = 디딤판 m 개, 그 사이 챌판 m - 1 개)
        if not 2 <= t["risers_a"] <= p["risers"] - m - 1:
            return "turn.risers_a 는 2 이상이고 전체 챌판 수 - winders - 1 이하여야 한다(돌음 위 단 묶음도 챌판 둘 이상)"
        if t["type"] == "L" and t["landing"] is not None and t["landing"] != p["width"]:
            return "L형 돌음 계단의 회전부는 폭 x 폭 정사각형이다(turn.landing 은 비우거나 계단 폭과 같아야 한다)"
        if t["type"] == "U" and t["gap"] > p["width"]:
            return "U형 돌음 계단의 turn.gap 은 계단 폭 이하여야 한다(보행선 반원이 회전부 안에 들어가야 한다)"
    elif not 2 <= t["risers_a"] <= p["risers"] - 2:
        return "turn.risers_a 는 2 이상이고 전체 챌판 수 - 2 이하여야 한다(위 단 묶음도 챌판 둘 이상)"
    if t["landing"] is not None and t["landing"] < p["width"]:
        return "turn.landing(층참 깊이)이 계단 폭보다 작다"
    return None


def _stair_params(p):
    def run(v):   # 걸음선 길이 1mm 이상
        return (isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v) and (v[2] - v[0]) ** 2 + (v[3] - v[1]) ** 2 >= 1.0)
    err = _check_opt("stair", p, {"name": _name, "run": run, "width": _pos, "height": _pos,
                                  "risers": lambda v: type(v) is int and STAIR_RISERS_MIN <= v <= STAIR_RISERS_MAX, "base_offset": num_ok},
                     {"turn": _turn_ok})
    return err or stair_turn_problem(p)


def stair_layout(p) -> dict:
    """계단의 형상 배치(순수 함수: 그리기·IFC 투영이 같은 값을 쓴다). 반환:
      {"flights": [{"run": (x1,y1,x2,y2), "n": 챌판 수, "z0": 단 묶음 아래 끝(계단 아래 끝 기준 상대 높이), "skirt": 아래로 더 채우는 두께, "length": 걸음선 길이, "td": 디딤판 깊이}, ...],
       "landing": None | {"poly": ((x,y),...), "z": 윗면 상대 높이, "t": 두께}, "rise": 챌판 높이}
    직선 계단은 단 묶음 하나, 층참 없음. 꺾인 계단(turn)은 걸음선(run)이 층참 아래 단 묶음의 걸음선이고 층참은 그 끝에서 이어진다.
    걸음선 왼쪽이 + 이다. L 은 층참에서 90도, U 는 180도 꺾어 올라간다(side 가 꺾는 쪽).
    turn.winders 가 있으면 "landing" 은 None 이고 "winders" 키에 돌음 회전부가 온다(_winder_layout)."""
    x1, y1, x2, y2 = p["run"]
    n, w = p["risers"], p["width"]
    rise = p["height"] / n
    dx, dy = x2 - x1, y2 - y1
    length = sqrt(dx * dx + dy * dy)
    t = p.get("turn")
    if t is None:
        td = length / (n - 1)
        return {"flights": [{"run": (x1, y1, x2, y2), "n": n, "z0": 0.0, "skirt": 0.0, "length": length, "td": td}], "landing": None, "rise": rise}
    k = t["risers_a"]
    td = length / (k - 1)
    ux, uy = dx / length, dy / length
    lx, ly = -uy, ux   # 왼쪽 법선

    def pt(tt, mm):
        return (x1 + ux * tt - uy * mm, y1 + uy * tt + ux * mm)
    d = t["landing"] if t["landing"] is not None else w
    sg = 1.0 if t["side"] == "left" else -1.0
    lb = (n - k - 1) * td
    h = w / 2.0
    if t["type"] == "L":
        m_lo, m_hi = -h, h
        b0 = pt(length + d / 2.0, sg * h)
        b1 = (b0[0] + sg * lx * lb, b0[1] + sg * ly * lb)
    else:
        off = w + t["gap"]
        m_lo, m_hi = (-h, h + off) if sg > 0 else (-h - off, h)
        b0 = pt(length, sg * off)   # 위 단 묶음의 첫 챌판은 층참 가까운 쪽 가장자리(A 가 끝나는 선)에 놓이고 -진행 방향으로 되돌아 오른다
        b1 = (b0[0] - ux * lb, b0[1] - uy * lb)
    poly = (pt(length, m_lo), pt(length + d, m_lo), pt(length + d, m_hi), pt(length, m_hi))
    ztop = k * rise
    fa = {"run": (x1, y1, x2, y2), "n": k, "z0": 0.0, "skirt": 0.0, "length": length, "td": td}
    if t.get("winders") is not None:
        return _winder_layout(t, pt, fa, poly, n, rise, length, d, w, (ux, uy), (lx, ly), sg)
    fb = {"run": (b0[0], b0[1], b1[0], b1[1]), "n": n - k, "z0": ztop, "skirt": STAIR_LANDING_T, "length": lb, "td": td}
    return {"flights": [fa, fb], "landing": {"poly": poly, "z": ztop, "t": STAIR_LANDING_T}, "rise": rise}


def _winder_layout(t, pt, fa, region, n, rise, length, d, w, u, lft, sg) -> dict:
    """돌음(와인더) 계단의 배치. 층참 자리(회전부)를 회전 중심에서 퍼지는 같은 각의 쐐기 m 개로 나눈다.
    회전 중심 = 회전부의 안쪽 점(L: 아래 단 묶음 끝선의 꺾는 쪽 끝 = 두 단 묶음 안쪽 옆선이 만나는 모서리, U: 두 단 묶음 사이 간격의 가운데).
    보행선(단 묶음 중심선)은 회전부 안에서 이 중심 둘레의 원호(반지름 L: 폭/2, U: 폭/2 + 간격/2)로 이어지므로 같은 각 분할이 곧 보행선 위
    같은 디딤 길이 분할이다(실무 관행인 '균등 보행선 분할'과 같은 결과). 각은 sincos_deg(사칙연산 급수)로 구해 플랫폼 libm 에 기대지 않는다.
    쐐기 j(0..m-1) 윗면 = (risers_a + j) x 챌판 높이, 밑면은 모두 층참 밑면과 같은 risers_a x 챌판 높이 - 층참 두께(속이 찬 계단 덩어리).
    위 단 묶음 챌판 수 = 전체 - risers_a - m + 1, 아래 끝 = 마지막 쐐기 윗면, 밑은 쐐기 밑면까지 채운다."""
    k, m = t["risers_a"], t["winders"]
    h = w / 2.0
    c = h if t["type"] == "L" else h + t["gap"] / 2.0          # 회전 중심의 걸음선 기준 옆 거리(꺾는 쪽 +)
    sweep = 90.0 if t["type"] == "L" else 180.0
    if t["type"] == "L":   # 바깥 둘레(시작 끝 = 아래 단 묶음 끝선의 바깥쪽 끝 -> 끝 끝 = 위 단 묶음 첫 챌판선의 바깥쪽 끝), (t, m) 좌표
        outer = [(length, -sg * h), (length + w, -sg * h), (length + w, sg * h)]
    else:
        far = sg * (h + t["gap"] + w)
        outer = [(length, -sg * h), (length + d, -sg * h), (length + d, far), (length, far)]
    pc = (length, sg * c)

    def hit(f):
        """회전 중심에서 각 f(도)로 나간 반직선과 바깥 둘레의 교점: (둘레 매개변수 = 변 번호 + 변 위 비율, (t, m))."""
        sn, cs = sincos_deg(f)
        dt, dm = sn, -sg * cs   # 각 0 = 아래 단 묶음 끝선을 따라 바깥쪽, 각 sweep = 위 단 묶음이 오르는 쪽
        best = None
        for i in range(len(outer) - 1):
            a, b = outer[i], outer[i + 1]
            ex, ey = b[0] - a[0], b[1] - a[1]
            den = dt * ey - dm * ex
            if abs(den) < 1e-12:
                continue
            qx, qy = a[0] - pc[0], a[1] - pc[1]
            s_ = (qx * ey - qy * ex) / den
            v_ = (qx * dm - qy * dt) / den
            if s_ > 1e-9 and -1e-9 <= v_ <= 1.0 + 1e-9:
                v_ = min(max(v_, 0.0), 1.0)
                if best is None or i + v_ < best[0]:
                    best = (i + v_, (a[0] + ex * v_, a[1] + ey * v_))
        return best
    cuts = [(0.0, outer[0])] + [hit(sweep * j / m) for j in range(1, m)] + [(float(len(outer) - 1), outer[-1])]
    zb = k * rise - STAIR_LANDING_T
    wedges = []
    for j in range(m):
        (pa, qa), (pb, qb) = cuts[j], cuts[j + 1]
        loc = [pc, qa] + [outer[i] for i in range(len(outer)) if pa + 1e-9 < i < pb - 1e-9] + [qb]
        wp = [pt(tt, mm) for tt, mm in loc]
        a2 = sum(wp[i][0] * wp[(i + 1) % len(wp)][1] - wp[(i + 1) % len(wp)][0] * wp[i][1] for i in range(len(wp)))
        if a2 < 0:   # 반시계로 맞춘다
            wp.reverse()
        wedges.append({"poly": tuple(wp), "z": (k + j) * rise, "zb": zb, "a0": sweep * j / m, "a1": sweep * (j + 1) / m, "q0": pt(*qa),
                       "qm": pt(*hit(sweep * (j + 0.5) / m)[1])})   # 시작 반직선 끝점, 가운데 각 반직선이 바깥 둘레와 만나는 점(평면 절단선)
    nb = n - k - m + 1
    td = fa["td"]
    lb = (nb - 1) * td
    ux, uy = u
    lx, ly = lft
    if t["type"] == "L":
        b0 = pt(length + w / 2.0, sg * h)
        b1 = (b0[0] + sg * lx * lb, b0[1] + sg * ly * lb)
    else:
        b0 = pt(length, sg * (w + t["gap"]))
        b1 = (b0[0] - ux * lb, b0[1] - uy * lb)
    z_b = (k + m - 1) * rise
    fb = {"run": (b0[0], b0[1], b1[0], b1[1]), "n": nb, "z0": z_b, "skirt": z_b - zb, "length": lb, "td": td}
    return {"flights": [fa, fb], "landing": None, "rise": rise,
            "winders": {"wedges": wedges, "region": tuple(region), "center": pt(*pc), "radius": c, "sweep": sweep,
                        "e0": (uy * sg, -ux * sg), "e90": (ux, uy), "tread": c * 3.141592653589793 * sweep / (180.0 * m)}}


def winder_walk_point(wd, f):
    """돌음 회전부 보행선(회전 중심 둘레 원호) 위 각 f(도)의 세계 좌표 점. 각 0 = 아래 단 묶음 끝, 각 sweep = 위 단 묶음 시작."""
    sn, cs = sincos_deg(f)
    (cx, cy), r, e0, e9 = wd["center"], wd["radius"], wd["e0"], wd["e90"]
    return (cx + r * (cs * e0[0] + sn * e9[0]), cy + r * (cs * e0[1] + sn * e9[1]))


RAILING_PTS_MAX = 256
RAILING_HEIGHT_DEFAULT = 1100.0   # 난간 높이 mm(바닥 마감에서 손잡이 윗면까지)
RAILING_POST_SPACING = 1200.0     # 난간 기둥 간격 상한 mm(꼭짓점마다 하나, 그 사이는 이 간격 이하로 등분)
RAILING_HANDRAIL_R = 25.0         # 손잡이 반지름 mm
RAILING_POST_R = 15.0             # 난간 기둥 반지름 mm


RAILING_TAG_MAX = 64   # 난간 출처 표식(src_tag) 길이 상한


def _railing_params(p):
    def path(v):   # 꼭짓점 2~256개, 이웃한 꼭짓점이 1mm 이상 떨어져 있다(열린 선)
        return (isinstance(v, list) and 2 <= len(v) <= RAILING_PTS_MAX and all(_pt_ok(x) for x in v)
                and all((v[i + 1][0] - v[i][0]) ** 2 + (v[i + 1][1] - v[i][1]) ** 2 >= 1.0 for i in range(len(v) - 1)))
    err = _check_opt("railing", p, {"name": _name, "path": path, "height": _pos, "base_offset": num_ok, "rise": num_ok},
                     {"src": id_ok, "src_tag": lambda v: text_ok(v, 1, RAILING_TAG_MAX), "src_inset": lambda v: num_ok(v) and v >= 0})
    if err is None and ("src" in p) != ("src_tag" in p):
        return "railing.src 와 railing.src_tag 는 함께 있어야 한다"
    if err is None and "src_inset" in p and "src" not in p:
        return "railing.src_inset 은 railing.src 가 있을 때만 둘 수 있다"
    return err


def railing_geom(p) -> dict:
    """난간의 형상(순수 함수: 그리기·IFC 투영이 같은 값을 쓴다). 좌표는 레벨 기준 상대 z. 걷는 면은 base_offset 에서 시작해 선 길이에 비례해 rise 만큼 오른다.
      {"surface": [(x,y,z), ...] 바닥 마감선, "top": [(x,y,z), ...] 손잡이 윗선(= surface + height), "posts": [((x,y,z0),(x,y,z1)), ...]}"""
    path, h = p["path"], p["height"]
    seg = [sqrt((path[i + 1][0] - path[i][0]) ** 2 + (path[i + 1][1] - path[i][1]) ** 2) for i in range(len(path) - 1)]
    total = sum(seg)
    cum = [0.0]
    for ln in seg:
        cum.append(cum[-1] + ln)

    def z_at(c):
        return p["base_offset"] + p["rise"] * (c / total)
    surface = [(float(q[0]), float(q[1]), z_at(cum[i])) for i, q in enumerate(path)]
    top = [(x, y, z + h) for x, y, z in surface]
    posts = []
    for i, (x, y, z) in enumerate(surface):
        posts.append(((x, y, z), (x, y, z + h)))
        if i < len(seg):
            n = max(1, -(-seg[i] // RAILING_POST_SPACING))   # 올림: 구간을 n 등분(간격이 상한 이하)
            nx, ny, nz = surface[i + 1]
            for k in range(1, int(n)):
                f = k / n
                px, py, pz = x + (nx - x) * f, y + (ny - y) * f, z + (nz - z) * f
                posts.append(((px, py, pz), (px, py, pz + h)))
    return {"surface": surface, "top": top, "posts": posts}


ELEVATOR_SHAFT_MIN = 800.0          # 샤프트 안쪽 변의 최소 길이 mm
ELEVATOR_CAB_DEFAULT = (1100.0, 1400.0, 2200.0)   # 카 [폭(x), 깊이(y), 높이] 기본값 mm(소형 승객용)


def _shaft_ok(v) -> bool:
    """샤프트 안쪽 사각형 [x1,y1,x2,y2] (x1<x2, y1<y2, 각 변 ELEVATOR_SHAFT_MIN 이상)."""
    return (isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v)
            and v[2] - v[0] >= ELEVATOR_SHAFT_MIN and v[3] - v[1] >= ELEVATOR_SHAFT_MIN)


def _cab_ok(v) -> bool:
    return isinstance(v, list) and len(v) == 3 and all(_pos(x) for x in v)


def elevator_problem(p) -> str | None:
    """카가 샤프트 안에 들어가는가(폭·깊이가 샤프트 안쪽 이하, 높이가 샤프트 높이 이하)."""
    sx, sy = p["shaft"][2] - p["shaft"][0], p["shaft"][3] - p["shaft"][1]
    if p["cab"][0] > sx + 1e-9 or p["cab"][1] > sy + 1e-9:
        return f"카({p['cab'][0]:g}x{p['cab'][1]:g})가 샤프트 안쪽({sx:g}x{sy:g})보다 크다"
    if p["cab"][2] > p["rise"] + 1e-9:
        return f"카 높이({p['cab'][2]:g})가 샤프트 높이({p['rise']:g})보다 크다"
    return None


ELEVATOR_PIT_MAX = 5000.0       # 피트 깊이 상한 mm
ELEVATOR_MACHINE_MAX = 6000.0   # 기계실 높이 상한 mm
ELEVATOR_STOPS_MAX = 64         # 승강장 문이 있는 층 수 상한
ELEVATOR_HOLE_CLEAR_MAX = 500.0  # 슬래브 구멍 여유 상한 mm(elevator.cut_slabs 와 같다)
DOOR_FACES = ("N", "E", "S", "W")   # 샤프트 면: N = +y 변, S = -y 변, E = +x 변, W = -x 변(입면 뷰 dir 과 같은 방위)


def _ids_ok(v, hi) -> bool:
    return isinstance(v, list) and 1 <= len(v) <= hi and all(id_ok(x) for x in v) and len(set(v)) == len(v)


def elevator_door_ok(v) -> bool:
    """승강장 문 개구부 = {"face": N|E|S|W(샤프트 면), "w": 폭 mm, "h": 높이 mm}. 문은 면 가운데에 놓이고 정차층(stops) 바닥에서 시작한다."""
    return isinstance(v, dict) and set(v) == {"face", "w", "h"} and v["face"] in DOOR_FACES and _pos(v["w"]) and _pos(v["h"])


def _elevator_params(p):
    err = _check_opt("elevator", p, {"name": _name, "shaft": _shaft_ok, "cab": _cab_ok, "base_offset": num_ok, "rise": _pos},
                     {"pit": lambda v: _pos(v) and v <= ELEVATOR_PIT_MAX,
                      "machine": lambda v: isinstance(v, dict) and set(v) == {"height"} and _pos(v["height"]) and v["height"] <= ELEVATOR_MACHINE_MAX,
                      "doors": elevator_door_ok, "stops": lambda v: _ids_ok(v, ELEVATOR_STOPS_MAX),
                      "hole_slabs": lambda v: _ids_ok(v, 128),
                      "hole_clearance": lambda v: num_ok(v) and 0 <= v <= ELEVATOR_HOLE_CLEAR_MAX})
    if err is None and (why := elevator_problem(p)):
        return f"elevator: {why}"
    if err is None and ("doors" in p) != ("stops" in p):
        return "elevator.doors 와 elevator.stops 는 함께 있어야 한다"
    if err is None and ("hole_slabs" in p) != ("hole_clearance" in p):
        return "elevator.hole_slabs 와 elevator.hole_clearance 는 함께 있어야 한다"
    if err is None and "doors" in p:
        face_len = (p["shaft"][2] - p["shaft"][0]) if p["doors"]["face"] in ("N", "S") else (p["shaft"][3] - p["shaft"][1])
        if p["doors"]["w"] > face_len - 200.0 + 1e-9:
            return f"elevator.doors: 문 폭({p['doors']['w']:g})이 샤프트 면({face_len:g})보다 200mm 이상 좁아야 한다"
        if p["doors"]["h"] > p["rise"] + 1e-9:
            return "elevator.doors: 문 높이가 샤프트 높이보다 크다"
    return err


def elevator_geom(p) -> dict:
    """승강기의 형상(순수 함수: 그리기·IFC 투영·슬래브 구멍이 같은 값을 쓴다). z 는 레벨 기준 상대.
      {"shaft": (x1,y1,x2,y2), "cab": (x1,y1,x2,y2 카 바닥 사각형, 샤프트 가운데), "z0": 샤프트·카 바닥, "z1": 샤프트 위, "cab_z1": 카 천장}"""
    x1, y1, x2, y2 = p["shaft"]
    w, d, h = p["cab"]
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    z0, z1 = float(p["base_offset"]), float(p["base_offset"] + p["rise"])
    out = {"shaft": (float(x1), float(y1), float(x2), float(y2)), "cab": (cx - w / 2.0, cy - d / 2.0, cx + w / 2.0, cy + d / 2.0),
           "z0": z0, "z1": z1, "cab_z1": float(p["base_offset"] + h)}
    # 선택 입력(없으면 같은 값): 피트(카 바닥 아래로 더 내려간 샤프트 바닥), 기계실(샤프트 위 상자). 카 상자·슬래브 구멍 범위(z0~z1)는 그대로다
    out["pit_z0"] = z0 - float(p.get("pit", 0.0))
    out["mach_z1"] = z1 + float(p["machine"]["height"]) if "machine" in p else z1
    return out


def elevator_door_edge(p):
    """승강장 문의 평면 위치: ((x0,y0),(x1,y1)) 문 양끝(샤프트 면 위, 면 가운데)과 면 바깥 법선 (nx,ny). 문이 없으면 None."""
    d = p.get("doors")
    if d is None:
        return None
    x1, y1, x2, y2 = p["shaft"]
    hw = d["w"] / 2.0
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    if d["face"] == "N":
        return ((cx - hw, y2), (cx + hw, y2)), (0.0, 1.0)
    if d["face"] == "S":
        return ((cx - hw, y1), (cx + hw, y1)), (0.0, -1.0)
    if d["face"] == "E":
        return ((x2, cy - hw), (x2, cy + hw)), (1.0, 0.0)
    return ((x1, cy - hw), (x1, cy + hw)), (-1.0, 0.0)


# ---------------------------------------------------------------- 경사 지붕 (kinds "roof", 설계: elements-design.md 16절)
ROOF_FORMS = ("gable", "hip", "shed")   # 박공(맞배)·모임(우진각)·외쪽
ROOF_PITCH_MAX = 75.0       # 물매 각도 상한(도)
ROOF_OVERHANG_MAX = 3000.0  # 처마 내밀기 상한 mm
ROOF_SIDE_MIN = 500.0       # 외곽 직사각형의 최소 변 길이 mm


def roof_rect_problem(b) -> str | None:
    """지붕 외곽이 직사각형(회전해도 된다)인가(안 되면 이유 문장): 꼭짓점 4개, 서로 수직인 이웃 변, 반대 변 평행·같은 길이, 변 500mm 이상, 반시계."""
    if not isinstance(b, list) or len(b) != 4 or not all(_pt_ok(q) for q in b):
        return "외곽은 꼭짓점 4개의 직사각형이어야 한다"
    e0 = (b[1][0] - b[0][0], b[1][1] - b[0][1])
    e1 = (b[2][0] - b[1][0], b[2][1] - b[1][1])
    l0, l1 = sqrt(e0[0] ** 2 + e0[1] ** 2), sqrt(e1[0] ** 2 + e1[1] ** 2)
    if l0 < ROOF_SIDE_MIN or l1 < ROOF_SIDE_MIN:
        return f"외곽의 변은 {ROOF_SIDE_MIN:g}mm 이상이어야 한다"
    if abs(e0[0] * e1[0] + e0[1] * e1[1]) > 1e-4 * l0 * l1:
        return "외곽의 이웃한 변이 수직이 아니다"
    if abs(b[0][0] + b[2][0] - b[1][0] - b[3][0]) > 0.5 or abs(b[0][1] + b[2][1] - b[1][1] - b[3][1]) > 0.5:
        return "외곽이 직사각형이 아니다(반대 변이 평행하지 않거나 길이가 다르다)"
    if polygon_area2(b) <= 0:
        return "외곽은 반시계 방향이어야 한다"
    return None


def _roof_params(p):
    err = _check_opt("roof", p, {"name": _name, "boundary": lambda v: isinstance(v, list) and len(v) == 4 and all(_pt_ok(q) for q in v), "form": lambda v: v in ROOF_FORMS,
                                 "pitch": lambda v: _pos(v) and v <= ROOF_PITCH_MAX, "overhang": lambda v: num_ok(v) and 0 <= v <= ROOF_OVERHANG_MAX,
                                 "base_offset": num_ok, "thickness": _pos},
                     {"ridge": lambda v: v == "short", "dir": num_ok})
    if err is not None:
        return err
    if why := roof_rect_problem(p["boundary"]):
        return f"roof.boundary: {why}"
    if p["form"] == "shed" and "dir" not in p:
        return "roof: 외쪽 지붕(shed)은 물이 흐르는 방향 dir 이 필요하다"
    if p["form"] != "shed" and "dir" in p:
        return "roof.dir 은 외쪽 지붕(shed)에만 둘 수 있다"
    if "ridge" in p and p["form"] != "gable":
        return "roof.ridge 는 박공 지붕(gable)에만 둘 수 있다"
    return None


def sincos_deg(a):
    """도 -> (sin, cos). 플랫폼 libm 을 쓰지 않는다(결정성: 도면·IFC 가 같은 값을 쓴다): 90도 단위로 줄인 뒤 테일러 급수(사칙연산만). drawing._sincos_deg 와 같은 알고리즘이다."""
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


def _clean_loop(pts):
    """이웃한 같은 점을 합친다(모임 지붕의 용마루 길이가 0 이면 사다리꼴이 삼각형이 된다)."""
    def same(a, b):
        return abs(a[0] - b[0]) <= 1e-6 and abs(a[1] - b[1]) <= 1e-6 and abs(a[2] - b[2]) <= 1e-6
    out = []
    for q in pts:
        if not out or not same(q, out[-1]):
            out.append(q)
    if len(out) > 1 and same(out[0], out[-1]):
        out.pop()
    return out


def _plane_of(face):
    """볼록 다각형 면(3D 점들)의 평면 z = a x + b y + c. 수직 면이면 None."""
    (x0, y0, z0) = face[0]
    for i in range(1, len(face) - 1):
        (x1, y1, z1), (x2, y2, z2) = face[i], face[i + 1]
        det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(det) < 1e-9:
            continue
        a = ((z1 - z0) * (y2 - y0) - (z2 - z0) * (y1 - y0)) / det
        b = ((x1 - x0) * (z2 - z0) - (x2 - x0) * (z1 - z0)) / det
        return (a, b, z0 - a * x0 - b * y0)
    return None


def roof_geom(p) -> dict:
    """경사 지붕의 형상(순수 함수: 그리기·IFC 투영이 같은 값을 쓴다). 좌표는 레벨 기준 상대 z, 평면 좌표는 세계 mm.
    기준: 외곽(boundary)의 변이 처마 선(base_offset 높이)이고, 처마는 물매대로 overhang 만큼 더 내민다(끝 높이 = base_offset - tan(물매) x overhang).
    박공·모임은 용마루가 외곽의 긴 변 방향(박공의 ridge="short" 는 짧은 변 방향)이고 경사면의 물매가 같다. 외쪽은 dir(물이 흐르는 방향 도) 한 면이다.
      {"faces": [[(x,y,z), ...] 윗면 다각형(위에서 볼 때 반시계)], "ridges": [(p, q)] 용마루, "hips": [(p, q)] 추녀마루, "perimeter": [(x,y,z), ...] 윗면 바깥 둘레,
       "arrows": [((cx,cy),(dx,dy))] 경사면마다 무게중심과 물이 흐르는 단위 방향, "outline": [(x,y)] 처마를 내민 외곽(반시계), "planes": [(a,b,c)] 면마다 z=ax+by+c,
       "zlow": 둘레의 가장 낮은 z, "ztop": 가장 높은 z}"""
    b = p["boundary"]
    t_s, t_c = sincos_deg(float(p["pitch"]))
    t = t_s / t_c
    o = float(p["overhang"])
    z0 = float(p["base_offset"])
    cx = sum(q[0] for q in b) / 4.0
    cy = sum(q[1] for q in b) / 4.0
    e0 = (b[1][0] - b[0][0], b[1][1] - b[0][1])
    e1 = (b[2][0] - b[1][0], b[2][1] - b[1][1])
    l0, l1 = sqrt(e0[0] ** 2 + e0[1] ** 2), sqrt(e1[0] ** 2 + e1[1] ** 2)
    long_e, long_l, short_e, short_l = (e0, l0, e1, l1) if l0 >= l1 else (e1, l1, e0, l0)
    if p.get("ridge") == "short":   # 박공: 용마루를 짧은 변 방향으로 돌린다
        da, A, B = short_e, short_l / 2.0, long_l / 2.0
        ln_a = short_l
    else:
        da, A, B = long_e, long_l / 2.0, short_l / 2.0
        ln_a = long_l
    ua = (da[0] / ln_a, da[1] / ln_a)
    ub = (-ua[1], ua[0])      # ua 의 왼쪽 법선

    def W(l, s, z):
        return (cx + l * ua[0] + s * ub[0], cy + l * ua[1] + s * ub[1], z)
    L, S = A + o, B + o
    ze = z0 - t * o
    ridges, hips, arrows = [], [], []
    form = p["form"]
    if form == "gable":
        zr = z0 + t * B
        faces = [[W(-L, -S, ze), W(L, -S, ze), W(L, 0, zr), W(-L, 0, zr)], [W(-L, 0, zr), W(L, 0, zr), W(L, S, ze), W(-L, S, ze)]]
        ridges = [(W(-L, 0, zr), W(L, 0, zr))]
        perim = [W(-L, -S, ze), W(L, -S, ze), W(L, 0, zr), W(L, S, ze), W(-L, S, ze), W(-L, 0, zr)]
        arrows = [((cx - ub[0] * S / 2.0, cy - ub[1] * S / 2.0), (-ub[0], -ub[1])), ((cx + ub[0] * S / 2.0, cy + ub[1] * S / 2.0), (ub[0], ub[1]))]
    elif form == "hip":
        # 모임 지붕의 용마루는 긴 변 방향이다(ridge 입력 없음): A >= B
        zr = z0 + t * B
        R = A - B
        faces = [_clean_loop([W(-L, -S, ze), W(L, -S, ze), W(R, 0, zr), W(-R, 0, zr)]), _clean_loop([W(L, -S, ze), W(L, S, ze), W(R, 0, zr)]),
                 _clean_loop([W(L, S, ze), W(-L, S, ze), W(-R, 0, zr), W(R, 0, zr)]), _clean_loop([W(-L, S, ze), W(-L, -S, ze), W(-R, 0, zr)])]
        if R >= 1.0:
            ridges = [(W(-R, 0, zr), W(R, 0, zr))]
        hips = [(W(L, -S, ze), W(R, 0, zr)), (W(L, S, ze), W(R, 0, zr)), (W(-L, S, ze), W(-R, 0, zr)), (W(-L, -S, ze), W(-R, 0, zr))]
        perim = [W(-L, -S, ze), W(L, -S, ze), W(L, S, ze), W(-L, S, ze)]
        for fc, d in zip(faces, ((-ub[0], -ub[1]), (ua[0], ua[1]), (ub[0], ub[1]), (-ua[0], -ua[1]))):
            arrows.append(((sum(q[0] for q in fc) / len(fc), sum(q[1] for q in fc) / len(fc)), d))
    else:   # shed
        n_s, n_c = sincos_deg(float(p["dir"]))
        n = (n_c, n_s)
        ext = A * abs(ua[0] * n[0] + ua[1] * n[1]) + B * abs(ub[0] * n[0] + ub[1] * n[1])   # 외곽 중심에서 물이 흐르는 쪽 끝까지

        def zs(x, y):
            return z0 + t * (ext - ((x - cx) * n[0] + (y - cy) * n[1]))
        fc = [(q[0], q[1], zs(q[0], q[1])) for q in (W(-L, -S, 0), W(L, -S, 0), W(L, S, 0), W(-L, S, 0))]
        faces = [fc]
        perim = list(fc)
        arrows = [((cx, cy), n)]
    planes = [_plane_of(fc) for fc in faces]
    outline = [(q[0], q[1]) for q in (W(-L, -S, 0), W(L, -S, 0), W(L, S, 0), W(-L, S, 0))]
    return {"faces": faces, "ridges": ridges, "hips": hips, "perimeter": perim, "arrows": arrows, "outline": outline, "planes": planes,
            "zlow": min(q[2] for q in perim), "ztop": max(q[2] for fc in faces for q in fc)}


CORRIDOR_WORDS = ("복도", "corridor", "hallway")


def is_corridor(name, category) -> bool:
    """공간이 복도인가: 분류가 복도 낱말이거나 실명이 복도로 끝난다(분류도 실명도 없는 공간은 복도로 보지 않는다)."""
    cat = (category or "").strip().lower()
    nm = (name or "").strip().lower()
    return cat in CORRIDOR_WORDS or any(nm.endswith(w) for w in CORRIDOR_WORDS)


def _space_params(p):
    def boundary(v):
        return isinstance(v, list) and SLAB_PTS_MIN <= len(v) <= SLAB_PTS_MAX and all(_pt_ok(x) for x in v) and polygon_area2(v) > 0   # 반시계로 저장한다
    err = _check_opt("space", p, {"name": _name, "category": lambda v: text_ok(v, 0, 255), "boundary": boundary, "height": _pos},
                     {"ceiling_height": _pos, "holes": lambda v: isinstance(v, list) and 1 <= len(v) <= SLAB_OPENINGS_MAX and all(_poly_pts_ok(o) for o in v)})
    if err is None and "ceiling_height" in p and p["ceiling_height"] > p["height"]:
        return "space.ceiling_height 가 공간 높이보다 크다"
    if err is None and "holes" in p and (why := openings_problem(p["boundary"], p["holes"])):   # 섬 구멍 = 슬래브 구멍과 같은 규칙(경계 안쪽·서로 겹치지 않음)
        return f"space.holes: {why}"
    return err


SHEET_KEYS = ("name", "views", "paper", "project_name", "sheet_name", "sheet_number", "date", "north_angle", "client", "office", "signers", "revisions",
              "grids", "callouts", "marks", "tags", "key_plan", "key_zone", "line_profile")   # 시트 정의(sheet.define)가 저장하는 값 전부(빠짐없이 적는다)


def _sheet_params(p):
    """시트 정의: 뷰 배치(앞에서부터 왼쪽 위 -> 오른쪽 -> 아래)와 표제란 인자. 값의 범위·참조는 정의를 되살릴 때(viewdefs) 마저 검사한다."""
    return _check_fields("sheet", p, {
        "name": _name, "views": lambda v: isinstance(v, list) and 1 <= len(v) <= 12 and all(id_ok(x) for x in v),
        "paper": lambda v: v in ("A1", "A3"), "project_name": lambda v: text_ok(v), "sheet_name": lambda v: text_ok(v), "sheet_number": lambda v: text_ok(v),
        "date": lambda v: v is None or text_ok(v, 10, 10), "north_angle": num_ok, "client": lambda v: text_ok(v), "office": lambda v: text_ok(v),
        "signers": lambda v: _json_ok(v) and isinstance(v, list), "revisions": lambda v: _json_ok(v) and isinstance(v, list),
        "grids": lambda v: _json_ok(v) and isinstance(v, list), "callouts": lambda v: _json_ok(v) and isinstance(v, list), "marks": lambda v: type(v) is bool,
        "tags": lambda v: isinstance(v, list) and all(text_ok(x, 1, 16) for x in v), "key_plan": lambda v: v is None or id_ok(v),
        "key_zone": lambda v: v is None or (isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v)),
        "line_profile": lambda v: v is None or (_json_ok(v) and isinstance(v, dict))})


SITE_ROADS_MAX = 8
SITE_TERRAIN_MAX = 64     # 지반 표고점 수 상한
SITE_ZONES_MAX = 32       # 조경·공개공지 영역 수 상한
SITE_SIGNS_MAX = 32       # 간판·건물번호판 수 상한
PARK_BAY_GROUPS_MAX = 16  # 주차면 배열 수 상한
PARK_COUNT_MAX = 64       # 배열 하나의 주차면 수 상한
PARK_PATHS_MAX = 8        # 차로·진출입 동선 수 상한
PARK_PATH_PTS_MAX = 16    # 동선 한 줄의 꺾임점 수 상한
ZONE_USES = ("landscape", "open_space")
SIGN_KINDS = ("sign", "address")


SITE_OPTIONAL = ("terrain", "zones", "parking", "signs")   # 대지의 선택 파라미터(없으면 키 자체가 없다)


def norm_site_extra(v):
    """대지 선택 파라미터의 숫자를 float 로 맞춘다(저작 행과 같은 모양: 개수 count 만 정수). 명령과 IFC 가져오기가 같은 모양을 만든다."""
    if isinstance(v, bool) or isinstance(v, str) or v is None:
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list):
        return [norm_site_extra(x) for x in v]
    if isinstance(v, dict):
        return {k: (x if k == "count" else norm_site_extra(x)) for k, x in v.items()}
    return v


def _terrain_ok(v) -> bool:
    """지반 표고점 [[x, y, z], ...] (z 는 모델 기준 높이 mm: 1층 바닥 0, 아래는 음수)."""
    return isinstance(v, list) and 1 <= len(v) <= SITE_TERRAIN_MAX and all(isinstance(q, list) and len(q) == 3 and all(num_ok(x) for x in q) for q in v)


def _zone_ok(z) -> bool:
    return isinstance(z, dict) and set(z) == {"use", "name", "boundary"} and z["use"] in ZONE_USES and text_ok(z["name"], 0, NAME_MAX) and _poly_pts_ok(z["boundary"])


def _path_ok(v) -> bool:
    return isinstance(v, list) and 2 <= len(v) <= PARK_PATH_PTS_MAX and all(_pt_ok(q) for q in v)


def _bay_ok(b) -> bool:
    return (isinstance(b, dict) and set(b) - {"accessible"} == {"origin", "angle", "count", "w", "d"} and _pt_ok(b["origin"]) and num_ok(b["angle"])
            and -360.0 <= b["angle"] <= 360.0 and type(b["count"]) is int and 1 <= b["count"] <= PARK_COUNT_MAX and _pos(b["w"]) and _pos(b["d"])
            and type(b.get("accessible", False)) is bool)


def _aisle_ok(a) -> bool:
    return isinstance(a, dict) and set(a) == {"path", "width"} and _path_ok(a["path"]) and _pos(a["width"])


def _route_ok(r) -> bool:
    return isinstance(r, dict) and set(r) == {"path"} and _path_ok(r["path"])


def _parking_ok(v) -> bool:
    """주차 계획 = {"bays": [주차면 배열], "aisles": [차로], "routes": [진출입 동선]} (키는 있는 것만, 하나 이상)."""
    if not isinstance(v, dict) or not v or set(v) - {"bays", "aisles", "routes"}:
        return False
    for key, ok, cap in (("bays", _bay_ok, PARK_BAY_GROUPS_MAX), ("aisles", _aisle_ok, PARK_PATHS_MAX), ("routes", _route_ok, PARK_PATHS_MAX)):
        if key in v and not (isinstance(v[key], list) and 1 <= len(v[key]) <= cap and all(ok(x) for x in v[key])):
            return False
    return True


def _sign_ok(sg) -> bool:
    """간판·건물번호판: 입면에서 보는 면(face: 바깥 법선의 방위), 중심 [x, y], 아래 모서리 높이 z, 크기 w x h, 문구."""
    return (isinstance(sg, dict) and set(sg) == {"name", "kind", "face", "center", "z", "w", "h", "text"} and text_ok(sg["name"], 1, NAME_MAX) and sg["kind"] in SIGN_KINDS
            and sg["face"] in ("N", "E", "S", "W") and _pt_ok(sg["center"]) and num_ok(sg["z"]) and _pos(sg["w"]) and _pos(sg["h"]) and text_ok(sg["text"], 0, 64))


def _site_params(p):
    """대지(site): 경계 다각형(반시계), 정북 각, 접한 도로(경계 변 번호·폭), 대지 표고(선택).
    선택(14절): terrain 지반 표고점, zones 조경·공개공지 영역, parking 주차 계획, signs 간판·건물번호판. 없으면 키 자체가 없다."""
    def boundary(v):
        return isinstance(v, list) and SLAB_PTS_MIN <= len(v) <= SLAB_PTS_MAX and all(_pt_ok(x) for x in v) and polygon_area2(v) > 0

    err = _check_opt("site", p, {"name": _name, "boundary": boundary, "north_angle": lambda v: num_ok(v) and -360.0 <= v <= 360.0,
                                 "roads": lambda v: isinstance(v, list) and len(v) <= SITE_ROADS_MAX, "elevation": lambda v: v is None or num_ok(v)},
                     {"terrain": _terrain_ok, "zones": lambda v: isinstance(v, list) and 1 <= len(v) <= SITE_ZONES_MAX and all(_zone_ok(z) for z in v),
                      "parking": _parking_ok, "signs": lambda v: isinstance(v, list) and 1 <= len(v) <= SITE_SIGNS_MAX and all(_sign_ok(z) for z in v)})
    if err:
        return err
    n = len(p["boundary"])
    edges = set()
    for r in p["roads"]:
        if (not isinstance(r, dict) or set(r) != {"name", "edge", "width"} or not text_ok(r["name"], 0, NAME_MAX) or type(r["edge"]) is not int
                or not 0 <= r["edge"] < n or not _pos(r["width"]) or r["edge"] in edges):
            return "site.roads 항목이 올바르지 않다(이름·변 번호·폭, 변마다 도로 하나)"
        edges.add(r["edge"])
    for i, z in enumerate(p.get("zones", ())):   # 영역은 대지 안(경계 위 포함)이어야 한다
        if any(point_in_polygon(q, p["boundary"]) < 0 for q in z["boundary"]):
            return f"site.zones: {i + 1}번 영역이 대지 경계 밖으로 나간다"
    for i, b in enumerate(p.get("parking", {}).get("bays", ())):
        if point_in_polygon(b["origin"], p["boundary"]) < 0:
            return f"site.parking.bays: {i + 1}번 배열의 시작점이 대지 경계 밖이다"
    return None


GRID_BUBBLES = ("both", "start", "end", "none")


def _grid_params(p):
    """통심선(grid, 19단계): 기호 문자(1~16자), 평면 선분 [x1,y1,x2,y2](길이 1mm 이상), 기호 원 위치. 비 IFC 의도 요소(도면이 평면·입면·단면에 그린다)."""
    err = _check_fields("grid", p, {"name": lambda v: text_ok(v, 1, 16), "line": lambda v: isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v),
                                    "bubbles": lambda v: v in GRID_BUBBLES})
    if err:
        return err
    x1, y1, x2, y2 = p["line"]
    if (x2 - x1) ** 2 + (y2 - y1) ** 2 < 1.0:
        return "grid.line 길이는 1mm 이상이어야 한다"
    return None


def _solid_params(p):
    """일반 솔리드(18단계): 형상 검사는 solids.solid_problem 이 정본이다."""
    return SO.solid_problem(p)


def _foreign_params(p):
    return _check_fields("foreign", p, {"name": lambda v: text_ok(v, 0, 255)})


# ---------------------------------------------------------------- 요소 종류
class ElementKind(NamedTuple):
    name: str
    ifc: bool                    # IFC 신원(제품)을 가진다. 속성셋·재료도 이 종류만 가진다
    ifc_class: str | None        # 고정 IFC 클래스. None = 행마다 다르다(패밀리·외래) 또는 IFC 가 없다
    projected: bool              # 의도에서 IFC 로 투영한다(flush_ifc). 외래 요소는 IFC 가 정본이라 투영하지 않는다
    order: int                   # 생성·투영 순서(작을수록 먼저 만들고, 삭제는 반대). 호스트·타입·뷰가 의존하는 것보다 먼저
    params: Callable             # 파라미터 검사(오류 문장 또는 None)
    geometric: bool = False      # 도면(평면·단면·입면)이 그리는 모델 요소: 저작 요소만 그린다. 외래·분리는 생략하고 개수를 알린다
    refs: tuple = ()             # 파라미터 안의 id 참조: (파라미터 이름, 형태 "id"|"ref", 같은 뜻을 담는 관계 이름 또는 None).
                                 # "id" = 값이 요소 id, "ref" = 주석 참조 문자열 "종류:id#역할". 관계 이름이 있으면 이 요소에서 나가는 그 관계의 끝이 값과 같아야 한다.
                                 # 병합이 id 를 새로 매길 때 이 표로 params 안의 참조를 함께 옮기고, model.validate 가 관계와의 어긋남을 잡는다
    ref_kinds: tuple = ()        # 참조 대상이 가져야 할 요소 종류: (파라미터 이름, (종류, ...)). "ref" 형태(주석 참조)는 문자열 안의 종류를 따르므로 여기 없다.
                                 # 가져오기가 파일에서 복원한 참조를 검사하는 한 곳이다(정의 id·고아·외래를 이 종류로 잘못 받아들이지 않게)


ELEMENT_KINDS: dict[str, ElementKind] = {k.name: k for k in (
    ElementKind("level", True, "IfcBuildingStorey", True, 0, _level_params),
    ElementKind("wall", True, "IfcWall", True, 1, _wall_params, geometric=True),
    ElementKind("door", True, "IfcDoor", True, 2, _door_params, geometric=True),
    ElementKind("slab", True, "IfcSlab", True, 1, _slab_params, geometric=True),
    ElementKind("stair", True, "IfcStair", True, 1, _stair_params, geometric=True),
    ElementKind("space", True, "IfcSpace", True, 1, _space_params, geometric=True),
    ElementKind("elevator", True, "IfcTransportElement", True, 1, _elevator_params, geometric=True, refs=(("stops", "ids", None), ("hole_slabs", "ids", None))),
    ElementKind("roof", True, "IfcRoof", True, 1, _roof_params, geometric=True),
    ElementKind("railing", True, "IfcRailing", True, 1, _railing_params, geometric=True, refs=(("src", "id", None),), ref_kinds=(("src", ("stair", "slab")),)),
    ElementKind("site", True, "IfcSite", True, 0, _site_params),
    # 일반 솔리드(18단계): 돌출·로프트·판·스윕·메시. IFC 클래스는 범주(category)가 정한다(행마다 다르다)
    ElementKind("solid", True, None, True, 1, _solid_params, geometric=True),
    ElementKind("ftype", True, None, True, 3, _ftype_params),
    ElementKind("finst", True, None, True, 4, _finst_params, geometric=True, refs=(("type", "id", "of_type"),)),
    ElementKind("foreign", True, None, False, 5, _foreign_params),
    # 통심선(19단계): 뷰처럼 IFC 신원이 없는 의도 요소. 정의는 DoBIM_Defs 로 왕복한다
    ElementKind("grid", False, None, False, 6, _grid_params),
    ElementKind("view", False, None, False, 6, _view_params, refs=(("level", "id", None),), ref_kinds=(("level", ("level",)),)),   # 평면 뷰만 level 이 있다
    ElementKind("dim", False, None, False, 7, _dim_params, refs=(("view", "id", "annot_of"), ("a", "ref", None), ("b", "ref", None)),
                ref_kinds=(("view", ("view",)),)),
    ElementKind("tag", False, None, False, 7, _tag_params, refs=(("view", "id", "annot_of"), ("target", "id", None)),
                ref_kinds=(("view", ("view",)), ("target", ("wall", "door")))),
    # 시트 정의(뷰 배치·표제란 인자): 뷰와 같은 비 IFC 의도 요소다. 뷰를 가리키는 참조는 목록(views)·중첩(revisions·callouts 의 view)이라 관계로 묻지 않는다
    ElementKind("sheet", False, None, False, 8, _sheet_params, refs=(("views", "ids", None), ("key_plan", "id", None)),
                ref_kinds=(("key_plan", ("view",)),)),
)}

NON_IFC_KINDS = frozenset(k for k, v in ELEMENT_KINDS.items() if not v.ifc)      # 신원·속성셋·재료가 없는 의도 요소(뷰 정의, 치수·태그)
PROJECTED_KINDS = tuple(k.name for k in sorted(ELEMENT_KINDS.values(), key=lambda k: (k.order, k.name)) if k.projected)
FIXED_IFC_CLASS = {k: v.ifc_class for k, v in ELEMENT_KINDS.items() if v.ifc_class}   # 코어가 직접 정하는 IFC 클래스
KIND_NAMES = tuple(ELEMENT_KINDS)
# 의도 복원(S2 가져오기)이 IFC 클래스로 종류를 정하는 것: IFC 클래스 -> 종류. 패밀리 타입·인스턴스(ftype/finst)는 클래스가 레시피마다 달라 의도가 스스로 밝힌
# 종류를 따른다(family.restore, ADR-0014). 뷰·주석은 IFC 신원이 없어 IFC 에서 복원할 수 없다(알려진 한계)
IMPORTABLE_BY_CLASS = {"IfcBuildingStorey": "level", "IfcWall": "wall", "IfcDoor": "door", "IfcSlab": "slab", "IfcStair": "stair", "IfcSpace": "space", "IfcRailing": "railing", "IfcTransportElement": "elevator", "IfcRoof": "roof", "IfcSite": "site"}
FAMILY_OF_KIND = {"level": "level.basic", "wall": "wall.basic", "door": "door.single", "slab": "slab.basic", "stair": "stair.straight", "space": "space.basic", "railing": "railing.basic", "elevator": "elevator.basic", "roof": "roof.basic", "site": "site.basic", "solid": "solid.generic"}


def _remap_ref(form: str, v, m: dict):
    """참조 값 하나에서 id 를 m(옛 id -> 새 id)으로 바꾼다. 모양이 다른 값은 그대로 둔다(검사는 다른 곳의 일이다)."""
    if form == "ids":   # id 목록(시트가 올린 뷰)
        return [m.get(x, x) if isinstance(x, str) else x for x in v] if isinstance(v, list) else v
    if not isinstance(v, str):
        return v
    if form == "id":
        return m.get(v, v)
    head, sep, role = v.partition("#")   # "종류:id#역할"
    kind, colon, eid = head.partition(":")
    return f"{kind}:{m[eid]}#{role}" if sep and colon and eid in m else v


def remap_params(kind: str, params, m: dict):
    """파라미터 안의 id 참조를 m 으로 바꾼 새 파라미터(바꿀 게 없으면 같은 객체). 참조 위치는 ELEMENT_KINDS[kind].refs 가 정한다."""
    k = ELEMENT_KINDS.get(kind)
    if k is None or not isinstance(params, dict):
        return params
    out = params
    for name, form, _rel in k.refs:
        if name in params and (nv := _remap_ref(form, params[name], m)) != params[name]:
            out = dict(out, **{name: nv})
    if kind == "sheet":   # 개정 구름·부위 상세가 가리키는 뷰(목록 안의 객체)
        for name in ("revisions", "callouts"):
            if isinstance(out.get(name), list):
                nv = [dict(x, view=m.get(x["view"], x["view"])) if isinstance(x, dict) and isinstance(x.get("view"), str) else x for x in out[name]]
                if nv != out[name]:
                    out = dict(out, **{name: nv})
    return out


def param_ref_targets(kind: str, params) -> list:
    """관계와 같은 뜻을 담은 파라미터 참조 [(파라미터 이름, 관계 이름, 대상 id)]. 값이 문자열이 아니면 대상은 None."""
    k = ELEMENT_KINDS.get(kind)
    if k is None or not isinstance(params, dict):
        return []
    return [(name, rel, params[name] if isinstance(params[name], str) else None) for name, _f, rel in k.refs if rel and name in params]


def ref_kind_error(kind: str, name: str, target_kind) -> str | None:
    """kind 요소의 파라미터 name 이 target_kind 종류의 요소를 가리켜도 되는가(안 되면 문장). 표에 없는 파라미터는 검사하지 않는다.
    target_kind 가 None 이면 요소가 아닌 것(파일에 없거나 정의 속성 등)이다."""
    k = ELEMENT_KINDS.get(kind)
    for n, kinds in (k.ref_kinds if k else ()):
        if n == name and target_kind not in kinds:
            return f"{kind}.{name} 은 {'·'.join(kinds)} 를 가리켜야 한다(대상: {target_kind or '요소 아님'})"
    return None


def order_of(kind: str) -> int:
    return ELEMENT_KINDS[kind].order


def is_ifc(kind: str) -> bool:
    return kind not in NON_IFC_KINDS


def row_params_error(kind: str, params) -> str | None:
    k = ELEMENT_KINDS.get(kind)
    return f"알 수 없는 요소 종류: {str(kind)[:20]}" if k is None else k.params(params)


def class_error(kind: str, ifc_class) -> str | None:
    """행의 ifc_class 가 이 종류에 맞는가: 고정 클래스면 같아야 하고, 뷰·주석은 빈 문자열, 그 밖(패밀리·외래)은 Ifc 로 시작하는 문자열."""
    k = ELEMENT_KINDS[kind]
    if k.ifc_class is not None:
        return None if ifc_class == k.ifc_class else f"알 수 없는 요소 종류·IFC 클래스: {str(ifc_class)[:30]}"
    if not k.ifc:
        return None if ifc_class == "" else f"{kind} 의 IFC 클래스는 비어 있어야 한다"
    return None if isinstance(ifc_class, str) and ifc_class.startswith("Ifc") and len(ifc_class) <= 64 else f"IFC 클래스가 올바르지 않다: {str(ifc_class)[:30]}"


# ---------------------------------------------------------------- 관계 종류
class RelKind(NamedTuple):
    name: str
    src: frozenset              # 시작 끝 요소 종류
    dst: frozenset
    dangling_ok: bool = False   # 끝 요소가 지워져도 관계가 남을 수 있다(분할 이력 succ: 한 번 쓴 id 는 다시 만들 수 없어 의미가 유지된다)
    guard: tuple = ()           # 관계를 새로 맺을 때 저작 요소여야 하는 끝("src"·"dst"). 동결된 요소에 호스팅·조인·배치할 수 없다(ADR-0005).
                                # 끊는 것은 막지 않는다: 저작 문을 지울 때 분리된 호스트와의 관계도 끊어야 한다
    violation: str = "REL_DANGLING"   # 병합이 끝이 지워진 관계를 보고하는 규칙 이름
    message: str = "관계의 한쪽 끝이 없다"


def _fs(*a):
    return frozenset(a)


REL_KINDS: dict[str, RelKind] = {r.name: r for r in (
    # 레벨은 분리돼 있어도 그 위의 저작 벽이 레벨을 따른다(S2): 레벨 쪽은 막지 않고, 놓이는 요소만 저작이어야 한다
    RelKind("on_level", _fs("wall", "slab", "stair", "space", "railing", "elevator", "roof", "solid", "finst", "foreign"), _fs("level"), guard=("src",), violation="LEVEL_DELETED", message="벽이 놓인 레벨이 지워졌다"),
    RelKind("joins", _fs("wall"), _fs("wall"), guard=("src", "dst"), violation="JOIN_TARGET_DELETED", message="조인 대상 벽이 지워졌다"),
    RelKind("hosts", _fs("wall"), _fs("door", "finst"), guard=("src", "dst"), violation="HOST_DELETED",
            message="호스트 벽이 지워졌는데 문이 그 벽에 놓여 있다"),
    RelKind("succ", _fs("wall"), _fs("wall"), dangling_ok=True),
    RelKind("annot_of", _fs("dim", "tag"), _fs("view"), violation="VIEW_DELETED", message="주석이 속한 뷰가 지워졌다"),
    RelKind("of_type", _fs("finst"), _fs("ftype"), violation="TYPE_DELETED", message="인스턴스의 패밀리 타입이 지워졌다"),
)}
REL_NAMES = tuple(REL_KINDS)
READ_REL_NAMES = ("on_level", "joins", "hosts", "of_type")   # element.get / relation.list 가 보이는 관계(주석·분할 이력은 따로 읽는다)


# ---------------------------------------------------------------- 효과 태그
class EffectKind(NamedTuple):
    name: str
    swap: bool                  # True: [태그, id, 이전, 새것] (값 교환), False: [태그, ..., 이전, 새것] 이 끝 두 칸 (rel·pset)
    keys: Callable              # 효과 -> 그래프 입력 키 집합


def _keys_el(e):
    out = {("el", e[1])}
    if e[2] is None or e[3] is None:   # 생성·삭제는 버킷·개수 입력도 바꾼다
        kind = (e[3] or e[2])["kind"]
        out |= {("bucket", kind, bucket_of(e[1])), ("count", kind)}
    return out


EFFECTS: dict[str, EffectKind] = {x.name: x for x in (
    EffectKind("el", True, _keys_el),
    EffectKind("rel", False, lambda e: {("rel_out", e[1], e[2]), ("rel_in", e[1], e[3])}),
    EffectKind("pset", False, lambda e: {("pset", e[1])}),
    EffectKind("mat", True, lambda e: {("mat", e[1])}),
    # 외래 부속 스냅숏(S2)은 IFC 쪽 부속일 뿐 파생값의 입력이 아니다: 그래프 키가 없다(적용도 아무 키를 건드리지 않는다)
    EffectKind("att", True, lambda e: set()),
)}
EFFECT_TAGS = tuple(EFFECTS)


def invert_effect(e: list) -> list:
    """효과를 뒤집는다(실행 취소는 로그에 적힌 효과를 뒤집어 적용하는 것이다). 값 교환형 태그는 EFFECTS[..].swap 이 정한다."""
    t = e[0]
    if EFFECTS[t].swap:
        return [t, e[1], e[3], e[2]]
    return [t, e[1], e[2], e[3], e[5], e[4]]


def invert(effects: list) -> list:
    return [invert_effect(e) for e in reversed(effects)]
