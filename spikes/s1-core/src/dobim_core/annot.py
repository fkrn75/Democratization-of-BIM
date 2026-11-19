# SPDX-License-Identifier: MPL-2.0
"""주석(치수·태그)의 안정 참조와 파생값 (스파이크 S5, 버릴 코드).

참조 체계 (아키텍처 6절: 면·모서리를 인덱스로 부르지 않고 의미 이름으로 부른다):
    참조 문자열 = "<종류>:<요소 id>#<역할>"      예) wall:AbC…#trim.end, door:…#jamb.start, level:…#elevation
    역할(role)은 위상 인덱스가 아니라 의미 이름이다. 코너가 다시 계산돼 면·모서리 개수가 바뀌어도 이름은 그대로다.

    wall   axis.start / axis.end   축선 양 끝점(조인 보정 전)             pt
           trim.start / trim.end   조인으로 정리된 벽 양 끝점(축선 위)     pt
           own.end / own.trim.end  이 벽 자신의 현재 끝점(분할 이력을 따라가지 않는다)  pt
           axis / face.left / face.right  축선, 진행 방향 왼쪽·오른쪽 면선   line
           base / top              벽 밑·윗면 높이(레벨 + 벽 높이)         z
    door   center / jamb.start / jamb.end  호스트 축선 위 문 중심·문틀 양쪽  pt
           sill / head             문턱·문 상단 높이                       z
    level  elevation               레벨 높이                              z
    slab   top / bottom            슬래브 윗면·밑면 높이(레벨 + top_offset, 두께는 아래로)  z
    space  ceiling                 공간의 천장(반자) 마감 높이(레벨 + ceiling_height). 천장 높이 입력이 없으면 target_deleted 고아  z

요소 id 만으로 부족한 경우의 규칙:
    - 벽 분할(wall.split): 분할은 벽의 "끝"을 새 벽에 넘긴다. 분할 이력은 관계 succ(원래 벽 -> 끝 쪽을 가진 벽)로 남고,
      끝 역할(axis.end, trim.end)은 이 관계를 끝까지 따라가 사슬 끝 벽의 같은 역할로 해석된다("원래 끝", chain). 그래서 치수 값이 변하지 않는다.
      사슬 중간 벽이 지워져도 succ 는 지워진 벽을 지나 이어지므로, 사슬 끝 벽이 살아 있으면 값이 나온다.
      사슬 끝 벽까지 없을 때만 successor_deleted 다. 분할 뒤 이 벽 자신의 끝을 재려면 own.end / own.trim.end 를 쓴다.
      시작·선·면·높이 역할은 원래 벽에 남는다(분할 뒤에도 같은 직선이고 같은 시작점이다).
    - 한 번이라도 succ 에 쓰인 id 는 다시 만들 수 없다(wall.split 의 new_id 와 모든 생성 커맨드가 거절한다). 그래서 succ 는 순환하지 않는다.
    - 문 이동·문 폭 변경·조인 변경·벽 이동은 id 와 역할이 그대로이고 기하만 달라진다. 참조는 끊기지 않고 값이 따라간다.
    - 문이 분할로 다른 벽에 옮겨져도 문 id 가 그대로라 참조가 유지된다(호스트는 문의 속성이지 참조의 일부가 아니다).

고아(orphan) 규칙: 해석할 수 없으면 값을 만들지 않고 이유와 함께 고아로 표시한다. 조용히 지우지도, 다른 곳을 가리키지도 않는다.
    target_deleted     참조 대상 요소가 없다(사용자가 지웠다)                    정상적 고아
    successor_deleted  분할로 끝을 이어받은 벽이 없다(그 벽을 지웠다)            정상적 고아
    bad_ref            문법이 틀렸거나 그 종류에 없는 역할(생성 때 거절하므로 정상 흐름에서는 나오지 않는다)  고아 위반
    대상이 살아 있는데 고아로 나오면(또는 대상이 없는데 값이 나오면) 결함이다. 시험의 오라클이 이를 세운다.

경고(고아가 아니다): direction_changed — 치수를 만들 때 방향에 의존하는 참조(시작·끝·왼쪽·오른쪽·문틀)의 벽 축선 단위 방향을
    params.dirs 에 지문으로 저장하고, 지금 방향과 내적이 음수(90도 넘게 돌았거나 반전)이면 알린다. 값은 그대로 따라가지만
    "시작"이 다른 쪽 끝이 된 것이므로 사람이 확인해야 한다(annot.check 의 warnings). annot.rebind 로 같은 참조를 다시 묶으면 지문이 갱신된다.
    값이 유한하지 않으면(좌표가 1e308 근처라 제곱이 넘침) 값을 만들지 않고 invalid:non_finite 로 낸다.

queries.py 와 같은 규칙: 순수 함수, 시각·난수·파일 접근 금지, sqrt 와 사칙연산만.
"""
from __future__ import annotations

from math import isfinite, sqrt

from dobim_core import drawing as D
from dobim_core import kinds as K
from dobim_core import queries as Q

ROLE_TYPES = {
    "wall": {"axis.start": "pt", "axis.end": "pt", "trim.start": "pt", "trim.end": "pt", "own.end": "pt", "own.trim.end": "pt",
             "axis": "line", "face.left": "line", "face.right": "line", "base": "z", "top": "z"},
    "door": {"center": "pt", "jamb.start": "pt", "jamb.end": "pt", "sill": "z", "head": "z"},
    "level": {"elevation": "z"},
    "slab": {"top": "z", "bottom": "z"},
    "space": {"ceiling": "z"},
    # W1 1-B 2차(ANN-02): 통심선·패밀리 인스턴스(창·기둥 등)에 붙는 연관 치수. 통심선을 옮기거나 기둥·창을 옮기면 값이 따라간다
    "grid": {"line": "line", "start": "pt", "end": "pt"},
    "finst": {"center": "pt", "jamb.start": "pt", "jamb.end": "pt", "base": "z", "circle": "circle"},
}
END_ROLES = frozenset({"axis.end", "trim.end"})           # 분할 이력(succ)을 따라가는 역할
OWN_ROLE = {"own.end": "axis.end", "own.trim.end": "trim.end"}   # 이력을 따라가지 않는 짝
DIR_ROLES = {"wall": frozenset({"axis.start", "axis.end", "trim.start", "trim.end", "own.end", "own.trim.end", "face.left", "face.right"}),
             "door": frozenset({"center", "jamb.start", "jamb.end"}),
             "finst": frozenset({"jamb.start", "jamb.end"}),   # 호스팅 인스턴스의 문틀 양쪽은 호스트 벽 방향에 걸린다
             "grid": frozenset({"line"})}   # 각도 치수는 선의 방향으로 잰다(방향이 뒤집히면 값이 180 - θ 가 된다)
MEASURES = ("angle", "radius", "diameter")   # 선형(기본, 키 없음) 밖의 치수 종류(dim params.measure). 선형 치수 행은 이전과 같다
FAR_VERTEX = 1e8   # 각도 치수의 꼭짓점(두 선의 교점)이 원점에서 이보다 멀면 그리지 않는다(거의 평행: 무효 far_vertex)
ARC_N = 24         # 각도 치수 호를 나누는 선분 수
TAG_FIELDS = {
    "wall": ("name", "thickness", "height", "length", "fire_rating", "material", "level"),
    "door": ("name", "width", "height", "sill", "fire_rating", "material", "level", "host"),
    # W1 1-B(ANN-06·08): 슬래브·공간·계단 태그. material 은 모든 종류에 있어 재료 태그로 쓴다
    "slab": ("name", "thickness", "area", "top_offset", "material", "level"),
    "space": ("name", "category", "area", "height", "level"),
    "stair": ("name", "width", "height", "risers", "material", "level"),
    # W1 1-B 2차(ANN-06): 패밀리 인스턴스(창·기둥·패밀리 문 등). category = 범주 이름(창·기둥…), family = 패밀리 id
    "finst": ("name", "category", "family", "width", "height", "elevation", "material", "level", "host"),
    # 다중 범주 태그(ANN-08)만 다는 종류: 지붕·난간
    "roof": (), "railing": (),
}
# 다중 범주 태그(ANN-08): 이 필드들만 쓰는 태그는 태그 가능한 모든 종류에 달 수 있다(대상을 다른 범주로 다시 묶어도 그대로 쓴다).
# ifc_class = 요소의 IFC 클래스(범주를 묻는 공통 필드: 공간의 category 는 공간 용도라 이름을 달리 둔다)
MULTI_FIELDS = ("name", "level", "material", "ifc_class")
TAG_ALLOWED = {k: v + tuple(f for f in MULTI_FIELDS if f not in v) for k, v in TAG_FIELDS.items()}   # 종류별로 쓸 수 있는 필드 전체(종류 고유 + 다중 범주)
FINST_CATEGORY = {"IfcWindow": "창", "IfcColumn": "기둥", "IfcDoor": "문", "IfcFurnishingElement": "가구", "IfcBeam": "보",
                  "IfcMember": "부재", "IfcPlate": "판", "IfcBuildingElementProxy": "일반 부재"}
ORPHAN_REASONS = ("target_deleted", "successor_deleted", "bad_ref")
assert set(TAG_FIELDS) == set(K.TAG_TARGET_KINDS) and MEASURES == K.DIM_MEASURES   # 가져오기 검사표와 같아야 한다
PARALLEL_EPS = 1e-9
RND = 3                 # 그림 좌표 반올림 자릿수(mm)


# ================================================================ 참조 문법
def make_ref(kind: str, eid: str, role: str) -> str:
    return f"{kind}:{eid}#{role}"


def parse_ref(ref: str):
    """(종류, id, 역할) 또는 None. 문법만 본다(요소가 있는지는 해석 때)."""
    try:
        head, role = ref.split("#", 1)
        kind, eid = head.split(":", 1)
    except ValueError:
        return None
    if kind not in ROLE_TYPES or role not in ROLE_TYPES[kind] or len(eid) != 22:
        return None
    return kind, eid, role


def ref_type(ref: str):
    p = parse_ref(ref)
    return None if p is None else ROLE_TYPES[p[0]][p[2]]


# ================================================================ 방향 지문
def unit_dir(axis):
    """축선 [x1,y1,x2,y2] 의 단위 방향 (소수 6자리). 길이 0 이면 None."""
    dx, dy = axis[2] - axis[0], axis[3] - axis[1]
    ln = sqrt(dx * dx + dy * dy)
    if not isfinite(ln) or ln < 1e-9:
        return None
    return [round(dx / ln, 6), round(dy / ln, 6)]


def uses_direction(kind: str, role: str) -> bool:
    return role in DIR_ROLES.get(kind, ())


def dir_line(g, kind: str, eid: str):
    """방향 지문을 잡는 선분 [x1,y1,x2,y2]: 벽은 축선, 문·호스팅 인스턴스는 호스트 벽 축선, 통심선은 선분. 없으면 None(자립 인스턴스)."""
    if kind == "grid":
        return Q.el(g, eid)["params"]["line"]
    if kind == "door":
        eid = g.q(Q.door_host, eid)
    elif kind == "finst":
        eid = g.q(Q.finst_host, eid)
        if eid is None:
            return None
    return Q.el(g, eid)["params"]["axis"]


# ================================================================ 참조 해석 (그래프 노드: 참조 문자열 하나가 노드 하나)
def ref_resolve(g, ref):
    """참조를 기하로 푼다. 반환:
        ("pt", x, y) | ("line", px, py, dx, dy)(단위 방향) | ("z", z) | ("orphan", 이유)"""
    p = parse_ref(ref)
    if p is None:
        return ("orphan", "bad_ref")
    kind, eid, role = p
    row = Q.el(g, eid)
    if row is None:
        return ("orphan", "target_deleted")
    if row["kind"] != kind:
        return ("orphan", "bad_ref")
    if kind == "wall":
        return _wall_ref(g, eid, role, row)
    if kind == "door":
        return _door_ref(g, eid, role, row)
    if kind == "slab":
        zb, zt = g.q(Q.slab_zrange, eid)
        return ("z", zt if role == "top" else zb)
    if kind == "space":
        ch = row["params"].get("ceiling_height")
        if ch is None:
            return ("orphan", "target_deleted")   # 천장 높이 입력이 지워졌다: 정상적 고아(대상이 사라진 것과 같다)
        return ("z", g.q(Q.level_info, g.q(Q.space_level, eid))[1] + ch)
    if kind == "grid":
        x1, y1, x2, y2 = row["params"]["line"]
        if role == "start":
            return ("pt", x1, y1)
        if role == "end":
            return ("pt", x2, y2)
        dx, dy = x2 - x1, y2 - y1
        ln = sqrt(dx * dx + dy * dy)
        return ("line", (x1 + x2) / 2.0, (y1 + y2) / 2.0, dx / ln, dy / ln)   # 길이는 1mm 이상(grid 파라미터 검사)
    if kind == "finst":
        return _finst_ref(g, eid, role, row)
    return ("z", g.q(Q.level_info, eid)[1])


def _finst_ref(g, e, role, row):
    """패밀리 인스턴스(창·기둥 등) 참조. 호스팅: 호스트 축선 위 삽입점(center)·개구부 양 끝(jamb), 자립: 배치점(center)·가장 큰 원 단면(circle).
    그 인스턴스에 없는 역할(자립 인스턴스의 jamb, 원 단면이 없는 기둥의 circle)은 대상이 없는 것과 같아 target_deleted 고아다
    (타입을 바꿔 원 단면이 사라질 수 있다: 조용히 다른 곳을 가리키지 않는다)."""
    p = row["params"]
    h = g.q(Q.finst_host, e)
    if role == "base":
        return ("z", g.q(Q.level_info, g.q(Q.finst_level, e))[1] + p["elevation"])
    if h is not None:
        if role == "circle":
            return ("orphan", "target_deleted")
        x, y, c, s, _ = g.q(Q.wall_frame, h)
        t = p["offset"]
        if role != "center":
            cut = g.q(Q.ifc_spec, e).get("cut")
            if not cut:
                return ("orphan", "target_deleted")
            t += cut["x"][0] if role == "jamb.start" else cut["x"][1]
        return ("pt", x + t * c, y + t * s)
    if role in ("jamb.start", "jamb.end"):
        return ("orphan", "target_deleted")
    px, py = p["position"][0], p["position"][1]
    if role == "center":
        return ("pt", px, py)
    best = None
    for b in g.q(Q.ifc_spec, e).get("body") or ():
        pr = b.get("profile") or {}
        if b.get("k") == "extrude" and b.get("plane") == "xy" and pr.get("shape") == "circle" and (best is None or pr["r"] > best[1]):
            best = (pr["center"], pr["r"])
    if best is None:
        return ("orphan", "target_deleted")
    sn, cs = D._sincos_deg(p.get("rotation", 0.0) or 0.0)
    u, v = best[0][0], best[0][1]
    return ("circle", px + u * cs - v * sn, py + u * sn + v * cs, best[1])


def chain_tail(g, w):
    """분할 이력(succ)을 끝까지 따라간 마지막 벽 id(지워진 벽을 지나도 이어진다). 순환이면 None(불변식 위반: 정상 흐름에서는 없다)."""
    seen, x = {w}, w
    while True:
        nxt = Q.rel_out(g, "succ", x)
        if not nxt:
            return x
        x = nxt[0]
        if x in seen:
            return None
        seen.add(x)


def _wall_ref(g, w, role, row):
    if role in OWN_ROLE:                          # 자기 끝: 이력을 따라가지 않고 이 벽의 같은 끝 역할로 바로 푼다
        role = OWN_ROLE[role]
    elif role in END_ROLES:                       # 분할로 끝을 넘겨준 벽이면 사슬 끝 벽의 같은 역할로 해석한다
        tail = chain_tail(g, w)
        if tail is None:
            return ("orphan", "bad_ref")
        if tail != w:
            if Q.el(g, tail) is None:
                return ("orphan", "successor_deleted")
            return g.q(ref_resolve, make_ref("wall", tail, role))
    p = row["params"]
    if role == "axis.start":
        return ("pt", p["axis"][0], p["axis"][1])
    if role == "axis.end":
        return ("pt", p["axis"][2], p["axis"][3])
    x, y, c, s, length = g.q(Q.wall_frame, w)
    if role in ("trim.start", "trim.end"):
        start, end, _ = g.q(Q.wall_trim, w)
        t = start if role == "trim.start" else end
        return ("pt", x + t * c, y + t * s)
    if role in ("base", "top"):
        elev = g.q(Q.level_info, g.q(Q.wall_level, w))[1]
        return ("z", elev + D.WT.base(p) if role == "base" else elev + D._top_rel(p))   # 바닥 오프셋(22단계)   # 상단 윤곽(21단계)이 있으면 가장 높은 점
    mx, my = x + c * length / 2.0, y + s * length / 2.0      # 직선의 기준점: 축선 중점(그림용, 값에는 쓰이지 않는다)
    if role == "axis":
        return ("line", mx, my, c, s)
    h = p["thickness"] / 2.0 * (1.0 if role == "face.left" else -1.0)
    return ("line", mx - s * h, my + c * h, c, s)           # 왼쪽 법선 = (-s, c)


def _door_ref(g, d, role, row):
    p = row["params"]
    w = g.q(Q.door_host, d)
    if role in ("sill", "head"):
        elev = g.q(Q.level_info, g.q(Q.wall_level, w))[1]
        z = elev + p["sill"]
        return ("z", z if role == "sill" else z + p["height"])
    x, y, c, s, _ = g.q(Q.wall_frame, w)
    half = p["width"] / 2.0
    t = p["offset"] + (0.0 if role == "center" else (-half if role == "jamb.start" else half))
    return ("pt", x + t * c, y + t * s)


# ================================================================ 치수 값
def measure(ra, rb, mode=None):
    """두 해석 결과 사이의 거리(mode 없음) 또는 각도(도, "angle")·반지름("radius")·지름("diameter"). ("ok", 값) 또는 ("invalid", 이유).
    평면 거리와 높이 거리는 섞지 않는다. 좌표가 너무 커서 제곱이 넘치면 inf/nan 이 나오는데, 이 값으로 글자를 만들면 터지므로 값 없음(non_finite)으로 낸다."""
    st, v = _measure(ra, rb) if mode is None else _measure_more(ra, rb, mode)
    if st == "ok" and not isfinite(v):
        return ("invalid", "non_finite")
    return st, v


# ---------------------------------------------------------------- 삼각함수(사칙연산과 sqrt 만: libm 의 sin·cos·atan 은 플랫폼마다 끝자리가 다를 수 있다)
PI = 3.141592653589793
DEG = 180.0 / PI


def _atan_small(z):
    """|z| <= 1 의 atan. 반각 공식 atan(z) = 2 atan(z / (1 + sqrt(1 + z²))) 을 두 번 써 |z| < 0.2 로 줄인 뒤 급수 30항(고정)."""
    k = 1.0
    for _ in range(2):
        z = z / (1.0 + sqrt(1.0 + z * z))
        k *= 2.0
    acc, term, z2 = 0.0, z, z * z
    for n in range(30):
        acc += term / (2 * n + 1) * (1.0 if n % 2 == 0 else -1.0)
        term *= z2
    return k * acc


def _atan2_pos(y, x):
    """y > 0 인 atan2(라디안, 0 < 값 < π)."""
    if abs(x) >= y:
        a = _atan_small(y / x)
        return a if x > 0 else a + PI
    return PI / 2.0 - _atan_small(x / y)


def _sincos(t):
    """(sin t, cos t). t 를 [-π, π] 로 줄인 뒤 테일러 급수 고정 항수."""
    t -= round(t / (2.0 * PI)) * 2.0 * PI
    sn, cs, term_s, term_c, t2 = 0.0, 0.0, t, 1.0, t * t
    for n in range(22):
        sn += term_s
        cs += term_c
        term_s *= -t2 / ((2 * n + 2) * (2 * n + 3))
        term_c *= -t2 / ((2 * n + 1) * (2 * n + 2))
    return sn, cs


def _rot(ux, uy, t):
    """단위 벡터 (ux, uy) 를 t 라디안 돌린다."""
    sn, cs = _sincos(t)
    return ux * cs - uy * sn, ux * sn + uy * cs


def _measure_more(ra, rb, mode):
    """W1 1-B 2차(ANN-02): 모델 연관 각도·반지름·지름."""
    if "orphan" in (ra[0], rb[0]):
        return ("invalid", "orphan_ref")
    if mode == "angle":
        if ra[0] != "line" or rb[0] != "line":
            return ("invalid", "mixed")
        if abs(ra[3] * rb[4] - ra[4] * rb[3]) <= PARALLEL_EPS:
            return ("invalid", "parallel")
        return ("ok", _atan2_pos(abs(ra[3] * rb[4] - ra[4] * rb[3]), ra[3] * rb[3] + ra[4] * rb[4]) * DEG)   # 0 < θ < 180
    if ra[0] != "circle" or ra[1:] != rb[1:]:   # 반지름·지름은 같은 원 하나(a = b)를 잰다
        return ("invalid", "mixed")
    return ("ok", ra[3] * (2.0 if mode == "diameter" else 1.0))


def _measure(ra, rb):
    ta, tb = ra[0], rb[0]
    if "orphan" in (ta, tb):
        return ("invalid", "orphan_ref")
    if "circle" in (ta, tb):   # 원은 반지름·지름 치수만 잰다
        return ("invalid", "mixed")
    if ta == "z" and tb == "z":
        return ("ok", abs(ra[1] - rb[1]))
    if "z" in (ta, tb):
        return ("invalid", "mixed")
    if ta == "pt" and tb == "pt":
        dx, dy = rb[1] - ra[1], rb[2] - ra[2]
        return ("ok", sqrt(dx * dx + dy * dy))
    if ta == "line" and tb == "line":
        if abs(ra[3] * rb[4] - ra[4] * rb[3]) > PARALLEL_EPS:
            return ("invalid", "not_parallel")
        return ("ok", abs(ra[3] * (rb[2] - ra[2]) - ra[4] * (rb[1] - ra[1])))
    pt, ln = (ra, rb) if ta == "pt" else (rb, ra)
    return ("ok", abs(ln[3] * (pt[2] - ln[2]) - ln[4] * (pt[1] - ln[1])))


def dim_value(g, d):
    """치수 하나의 상태: ("ok", 값) | ("orphan", ((a|b, 이유), ...)) | ("invalid", 이유) | ("gone",).
    두 참조 노드만 읽으므로 두 참조의 값이 그대로면 여기서 전파가 멈춘다(조기 차단)."""
    row = Q.el(g, d)
    if row is None:
        return ("gone",)
    ra = g.q(ref_resolve, row["params"]["a"])
    rb = g.q(ref_resolve, row["params"]["b"])
    bad = tuple((w, r[1]) for w, r in (("a", ra), ("b", rb)) if r[0] == "orphan")
    if bad:
        return ("orphan", bad)
    return measure(ra, rb, row["params"].get("measure"))


def dim_flags(g, d):
    """치수의 경고: ("direction_changed:a" | "direction_changed:b", ...). 만들 때 저장한 방향 지문과 지금 벽 축선 방향의 내적이 음수이면 낸다.
    사슬 끝 역할(axis.end, trim.end)은 값을 내는 사슬 끝 벽의 방향도 본다. 고아·무효와는 별개의 경고다(값은 그대로 따라간다)."""
    row = Q.el(g, d)
    if row is None or row["kind"] != "dim":
        return ()
    p = row["params"]
    dirs = p.get("dirs") or {}
    out = []
    for side in ("a", "b"):
        fp = dirs.get(side)
        pr = parse_ref(p[side])
        if fp is None or pr is None:
            continue
        kind, eid, role = pr
        cand = []
        tgt = Q.el(g, eid)
        if tgt is not None and tgt["kind"] == kind:   # 참조 종류와 다른 요소(예전 데이터의 잘못된 참조)는 고아로 해석되니 방향도 보지 않는다
            if kind == "wall":
                cand.append(("wall", eid))
                if role in END_ROLES:
                    tail = chain_tail(g, eid)
                    if tail is not None and tail != eid and Q.el(g, tail) is not None:
                        cand.append(("wall", tail))
            elif kind in ("door", "finst", "grid"):
                cand.append((kind, eid))
        for ck, w in cand:
            ln = dir_line(g, ck, w)
            cur = None if ln is None else unit_dir(ln)
            if cur is not None and cur[0] * fp[0] + cur[1] * fp[1] < 0:
                out.append(f"direction_changed:{side}")
                break
    return tuple(out)


def annot_flags(g, a):
    """주석 하나의 경고 목록(치수만 있다)."""
    return g.q(dim_flags, a)


# ================================================================ 태그 텍스트
def fmt_num(v) -> str:
    """mm 값을 글자로. 0.05 미만 차이는 정수로 본다. 유한하지 않은 값은 호출 쪽이 먼저 걸러야 하며, 오면 '-' 로 낸다(터지지 않게)."""
    if not isfinite(v):
        return "-"
    r = round(v)
    return str(int(r)) if abs(v - r) < 0.05 else f"{v:.1f}"


class _NonFinite(Exception):
    """태그 필드 값이 유한하지 않다(좌표가 너무 커서 길이가 넘쳤다). tag_text 가 받아 invalid:non_finite 로 낸다."""


def _field(g, kind, eid, row, name):
    p = row["params"]
    if name == "name":
        return p["name"]
    if name == "ifc_class":   # 다중 범주 태그(ANN-08)
        return K.FIXED_IFC_CLASS.get(kind) or row.get("ifc_class") or "-"
    if kind in ("finst", "roof", "railing"):
        return _field_finst(g, kind, eid, row, name)
    if name in ("width", "height", "sill", "thickness", "elevation", "top_offset"):
        return fmt_num(p[name])
    if kind in ("slab", "space", "stair"):
        return _field_more(g, kind, eid, p, name)
    if kind == "wall":
        lv = g.q(Q.wall_level, eid)
        if name == "length":
            n = g.q(Q.wall_length, eid)
            if not isfinite(n):
                raise _NonFinite()
            return fmt_num(n)
        pset = "Pset_WallCommon"
    else:
        w = g.q(Q.door_host, eid)
        lv = g.q(Q.wall_level, w)
        if name == "host":
            return Q.el(g, w)["params"]["name"]
        pset = "Pset_DoorCommon"
    if name == "level":
        return g.q(Q.level_info, lv)[0]
    if name == "material":
        m = Q.material(g, eid)
        return "-" if m is None else str(m)
    v = Q.psets(g, eid).get(pset, {}).get("FireRating")
    return "-" if v is None else str(v)


def _field_finst(g, kind, eid, row, name):
    """패밀리 인스턴스(창·기둥 등)·지붕·난간 태그 필드(W1 1-B 2차). 자립 인스턴스에 없는 값(폭·높이·호스트)은 '-'."""
    p = row["params"]
    if name == "material":
        m = Q.material(g, eid)
        return "-" if m is None else str(m)
    if name == "level":
        lv = g.q(Q.finst_level, eid) if kind == "finst" else Q.rel_out(g, "on_level", eid)[0]
        return g.q(Q.level_info, lv)[0]
    if name == "category":
        c = row.get("ifc_class") or "-"
        return FINST_CATEGORY.get(c, c)
    if name == "family":
        return str(row.get("family_id") or "-")
    if name == "host":
        h = g.q(Q.finst_host, eid)
        return "-" if h is None else Q.el(g, h)["params"]["name"]
    if name in ("width", "height") and name not in p:
        return "-"
    return fmt_num(p[name])   # width·height(호스팅 개구부)·elevation


def _field_more(g, kind, eid, p, name):
    """슬래브·공간·계단 태그 필드(W1 1-B)."""
    if name == "level":
        return g.q(Q.level_info, Q.rel_out(g, "on_level", eid)[0])[0]
    if name == "material":
        m = Q.material(g, eid)
        return "-" if m is None else str(m)
    if name == "area":   # 경계 면적에서 개구부(슬래브)·구멍(공간)을 뺀다
        return D.area_text(p["boundary"], p.get("openings" if kind == "slab" else "holes", ()))
    if name == "category":
        return p["category"] or "-"
    return str(p[name])   # risers


def tag_text(g, t):
    """태그 하나의 상태: ("ok", 글) | ("orphan", ((target, 이유),)) | ("invalid", 이유) | ("gone",)."""
    row = Q.el(g, t)
    if row is None:
        return ("gone",)
    p = row["params"]
    tgt = Q.el(g, p["target"])
    if tgt is None:
        return ("orphan", (("target", "target_deleted"),))
    if tgt["kind"] not in TAG_FIELDS or any(f not in TAG_ALLOWED[tgt["kind"]] for f in p["fields"]):
        return ("orphan", (("target", "bad_ref"),))
    try:
        return ("ok", " ".join(_field(g, tgt["kind"], p["target"], tgt, f) for f in p["fields"]))
    except _NonFinite:
        return ("invalid", "non_finite")


def annot_state(g, a):
    """주석 하나의 값 상태(차등 시험·annot.list·annot.check 가 쓰는 단일 입구)."""
    row = Q.el(g, a)
    if row is None:
        return ("gone",)
    return (row["kind"], *(g.q(dim_value, a) if row["kind"] == "dim" else g.q(tag_text, a)))


# ================================================================ 그림 (뷰 좌표, SVG 내보내기용)
def _r(*vals):
    # + 0.0 은 -0.0 을 0.0 으로 바꾼다: 증분 그래프는 값이 같으면(-0.0 == 0.0) 옛 값을 그대로 두므로, 부호 있는 0 이 남으면
    # 기준 그래프와 해시가 달라진다(축선 반전을 섞은 임의 편집열이 찾았다)
    return tuple(round(v, RND) + 0.0 for v in vals)


def _foot(pt, ln):
    t = (pt[1] - ln[1]) * ln[3] + (pt[2] - ln[2]) * ln[4]
    return ("pt", ln[1] + t * ln[3], ln[2] + t * ln[4])


def dim_ends(g, p, ra, rb):
    """치수가 재는 두 점 (뷰 좌표). 평면: 점-점은 그대로, 점-선·선-선은 수선의 발. 높이: 뷰 좌표 (s, z)."""
    if ra[0] == "z":
        cfg = g.q(D.view_cfg, p["view"])
        at = p.get("at") or [0.0, 0.0]
        s = D._sw(cfg, at)[0] if cfg is not None and cfg[0] != "plan" else at[0]
        return (s, ra[1]), (s, rb[1])
    if ra[0] == "line" and rb[0] == "line":
        ra = ("pt", ra[1], ra[2])
    if ra[0] == "line":
        ra = _foot(rb, ra)
    if rb[0] == "line":
        rb = _foot(ra, rb)
    return (ra[1], ra[2]), (rb[1], rb[2])


def dim_geometry(g, p, ra, rb):
    """치수 그림: 보조선 2개, 치수선, 글 위치·방향. 모두 뷰 좌표. 치수선은 재는 방향의 왼쪽으로 off 만큼 떨어진다."""
    (ax, ay), (bx, by) = dim_ends(g, p, ra, rb)
    dx, dy = bx - ax, by - ay
    ln = sqrt(dx * dx + dy * dy)
    ux, uy = (dx / ln, dy / ln) if ln > 1e-9 else (1.0, 0.0)
    nx, ny = -uy, ux
    off = p["off"]
    ox, oy = nx * off, ny * off
    ext = 150.0 if off >= 0 else -150.0                # 보조선이 치수선을 넘는 길이
    e0 = _r(ax, ay, ax + ox + nx * ext, ay + oy + ny * ext)
    e1 = _r(bx, by, bx + ox + nx * ext, by + oy + ny * ext)
    line = _r(ax + ox, ay + oy, bx + ox, by + oy)
    return e0, e1, line, _r((ax + bx) / 2.0 + ox, (ay + by) / 2.0 + oy), _r(ux, uy)


def fmt_deg(v) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") + "°"   # 2D 작도 각도 치수(drafting.fmt_deg)와 같은 꼴


def more_geometry(p, ra, rb, value):
    """각도·반지름·지름 치수 그림 (선분들, 눈금들 (x, y, 접선 ux, uy), 글, 글 위치, 글 방향). 평면 좌표. 그릴 수 없으면 None(꼭짓점이 너무 멀다).
    각도: 두 선의 교점에서 반지름 |off|(0 이면 600) 의 호를 a 방향에서 b 방향으로 그린다. off 가 음수면 맞꼭지각 쪽(값은 같다).
    반지름: 원 중심에서 45도 방향으로 원둘레까지. 지름: 중심을 지나는 45도 선분."""
    mode = p["measure"]
    if mode == "angle":
        ax, ay, adx, ady = ra[1], ra[2], ra[3], ra[4]
        bx, by, bdx, bdy = rb[1], rb[2], rb[3], rb[4]
        cr = adx * bdy - ady * bdx
        t = ((bx - ax) * bdy - (by - ay) * bdx) / cr
        px, py = ax + t * adx, ay + t * ady
        if not (isfinite(px) and isfinite(py)) or abs(px) > FAR_VERTEX or abs(py) > FAR_VERTEX:
            return None
        rad = abs(p["off"]) if abs(p["off"]) >= 1.0 else 600.0
        sg = 1.0 if cr > 0 else -1.0
        s0 = -1.0 if p["off"] < 0 else 1.0
        d0 = (adx * s0, ady * s0)                    # 호의 시작 방향(음수 off = 맞꼭지각 쪽)
        th = sg * value / DEG
        dirs = [_rot(d0[0], d0[1], th * i / ARC_N) for i in range(ARC_N + 1)]
        pts = [(px + rad * ux, py + rad * uy) for ux, uy in dirs]
        d1, dm = dirs[-1], _rot(d0[0], d0[1], th / 2.0)
        ext = rad + 150.0
        segs = [_r(px, py, px + ext * d0[0], py + ext * d0[1]), _r(px, py, px + ext * d1[0], py + ext * d1[1])]
        segs += [_r(*pts[i], *pts[i + 1]) for i in range(ARC_N)]
        ticks = (_r(*pts[0], -d0[1] * sg, d0[0] * sg), _r(*pts[-1], -d1[1] * sg, d1[0] * sg))
        return tuple(segs), ticks, fmt_deg(value), _r(px + rad * dm[0], py + rad * dm[1]), _r(-dm[1] * sg, dm[0] * sg)
    cx, cy, r = ra[1], ra[2], ra[3]
    k = sqrt(0.5)
    rim = (cx + r * k, cy + r * k)
    if mode == "radius":
        seg, ticks, txt, tpos = _r(cx, cy, *rim), (_r(*rim, k, k),), "R" + fmt_num(value), _r(cx + r * k / 2.0, cy + r * k / 2.0)
    else:
        seg, ticks, txt, tpos = _r(cx - r * k, cy - r * k, *rim), (_r(cx - r * k, cy - r * k, k, k), _r(*rim, k, k)), "Ø" + fmt_num(value), _r(cx, cy)
    return (seg,), ticks, txt, tpos, _r(k, k)


def annot_draw(g, a):
    """주석 하나의 그림 항목. 정상이면 ("dim"|"tag", ...), 고아·무효면 ("orphan", id, 종류, 이유, x, y)."""
    row = Q.el(g, a)
    if row is None:
        return None
    p = row["params"]
    kind = row["kind"]
    st = g.q(dim_value if kind == "dim" else tag_text, a)
    if st[0] == "orphan":
        return ("orphan", a, kind, st[1][0][1], *_r(*p["home"]))
    if st[0] != "ok":
        return ("invalid", a, kind, st[1], *_r(*p["home"]))
    if kind == "dim" and "measure" in p:   # W1 1-B 2차: 각도·반지름·지름(그림 항목 adim). 선형 치수의 그림은 이전과 같다
        ra, rb = g.q(ref_resolve, p["a"]), g.q(ref_resolve, p["b"])
        geo = more_geometry(p, ra, rb, st[1])
        if geo is None:
            return ("invalid", a, kind, "far_vertex", *_r(*p["home"]))
        if not all(isfinite(v) for part in (*geo[0], *geo[1], geo[3], geo[4]) for v in part):
            return ("invalid", a, kind, "non_finite", *_r(*p["home"]))
        return ("adim", a, *geo)
    if kind == "dim":
        ra, rb = g.q(ref_resolve, p["a"]), g.q(ref_resolve, p["b"])
        e0, e1, line, tpos, u = dim_geometry(g, p, ra, rb)
        if not all(isfinite(v) for part in (e0, e1, line, tpos, u) for v in part):   # 값은 유한해도 보조선 계산이 넘칠 수 있다
            return ("invalid", a, kind, "non_finite", *_r(*p["home"]))
        return ("dim", a, e0, e1, line, fmt_num(st[1]), tpos, u)
    ax, ay = tag_anchor(g, p["target"])
    if not (isfinite(ax) and isfinite(ay)):
        return ("invalid", a, kind, "non_finite", *_r(*p["home"]))
    return ("tag", a, _r(ax, ay), _r(ax + p["off"][0], ay + p["off"][1]), st[1])


def tag_anchor(g, target):
    """태그가 가리키는 대상의 평면 대표점: 벽은 축선 중점, 문은 중심, 슬래브·공간·지붕은 경계 무게중심, 계단은 걸음선 중점,
    패밀리 인스턴스는 삽입점, 난간은 경로 가운데 선분의 중점."""
    row = Q.el(g, target)
    if row["kind"] in ("slab", "space", "roof"):
        return D.polygon_centroid(row["params"]["boundary"])
    if row["kind"] == "finst":   # 창·기둥: 삽입점(호스팅은 호스트 축선 위)
        r = g.q(ref_resolve, make_ref("finst", target, "center"))
        return (r[1], r[2])
    if row["kind"] == "railing":   # 난간: 경로 가운데 선분의 중점
        path = row["params"]["path"]
        i = max(1, len(path) // 2)
        return ((path[i - 1][0] + path[i][0]) / 2.0, (path[i - 1][1] + path[i][1]) / 2.0)
    if row["kind"] == "stair":
        x0, y0, x1, y1 = row["params"]["run"]
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    if row["kind"] == "wall":
        x, y, c, s, length = g.q(Q.wall_frame, target)
        return (x + c * length / 2.0, y + s * length / 2.0)
    r = g.q(ref_resolve, make_ref("door", target, "center"))
    return (r[1], r[2])


def view_annots(g, v):
    """뷰에 속한 주석들의 그림 항목(id 순). 주석이 바뀌면 이 뷰만 다시 계산된다."""
    return tuple(it for it in (g.q(annot_draw, a) for a in sorted(Q.rel_in(g, "annot_of", v)) if Q.el(g, a) is None or Q.el(g, a)["kind"] in ("dim", "tag"))
                 if it is not None)   # 같은 관계로 뷰에 속한 2D 작도 요소(draft, 27단계)는 drafting.view_drafts 가 그린다


# ================================================================ 전체 질의
def all_annots(g):
    out = []
    for kind in ("dim", "tag"):
        for b in range(Q.NB):
            out.extend(Q.bucket_ids(g, kind, b))
    return tuple(sorted(out))


def annot_bundle(g):
    """차등 시험용: 모든 주석의 값 상태와 그림. 증분 그래프와 기준 그래프가 같아야 한다."""
    ids = g.q(all_annots)
    return {"state": {a: g.q(annot_state, a) for a in ids}, "draw": {a: g.q(annot_draw, a) for a in ids},
            "flags": {a: g.q(annot_flags, a) for a in ids}}


def classify(st) -> str:
    """상태 -> ok | orphan:<이유> | invalid:<이유> | gone"""
    if st[0] == "gone":
        return "gone"
    s = st[1]
    if s == "ok":
        return "ok"
    if s == "orphan":
        return "orphan:" + st[2][0][1]
    return f"{s}:{st[2]}"


# 질의 노드가 주석 전용인지 알아보기 위한 함수 집합(계측용: 편집 하나가 다시 계산시킨 주석 노드 수)
ANNOT_FNS = frozenset({ref_resolve, dim_value, dim_flags, annot_flags, tag_text, annot_state, annot_draw, view_annots})
