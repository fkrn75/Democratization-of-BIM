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

from dobim_core import arcgeom as AG
from dobim_core import beams as BM
from dobim_core import curtain as CW
from dobim_core import roofpoly as RP
from dobim_core import roofeaves as RE
from dobim_core import roofext as RX
from dobim_core import roofflat as RFL
from dobim_core import roofskel as RS
from dobim_core import solids as SO
from dobim_core import wallcut as WC
from dobim_core import walltop as WT
from dobim_core import walltypes as WTY

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


WALL_TEE_ENDS = ("start", "end")


ARC_EXCLUSIVE = ("cuts",)   # 곡선 벽(W2 ARC-04)이 아직 함께 갖지 않는 키(잘라내기). T 조인 줄기·상단 윤곽·사각 모양 개구부는 W2 세 번째 PR 부터 받는다
ARC_EXCLUSIVE_LABEL = "잘라내기"


def tee_ok(v) -> bool:
    """벽의 T 조인 줄기 끝(W1 1-F 3차): ["start"], ["end"], ["start", "end"] 중 하나. 그 끝이 조인 상대 벽의 중간에 닿는다."""
    return isinstance(v, list) and v in (["start"], ["end"], ["start", "end"])


def _wall_params(p):
    def axis(v):   # 벽 길이 1mm 이상(wall.create 의 규칙)
        return (isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v)
                and (v[2] - v[0]) ** 2 + (v[3] - v[1]) ** 2 >= 1.0)
    err = _check_opt("wall", p, {"name": _name, "axis": axis, "thickness": _pos, "height": _pos}, {"layers": layers_ok, "openings": lambda v: isinstance(v, list), "top_profile": lambda v: isinstance(v, list),
                                                                                                         "base_offset": lambda v: num_ok(v) and abs(v) <= WT.BASE_OFFSET_MAX,
                                                                                                         "cuts": lambda v: WC.cuts_problem(v) is None,   # 벽 잘라내기(23단계)
                                                                                                         "top_level": id_ok, "top_offset": lambda v: num_ok(v) and v != 0,
                                                                                                         "tee": tee_ok,   # T 조인 줄기 끝(W1 1-F 3차)
                                                                                                         "arc": _pt_ok,   # 곡선(원호) 벽(W2 ARC-04): 호 위의 점(가운데)
                                                                                                         "wtype": id_ok, "loc": lambda v: v in WTY.LOC_LINES[1:],   # 벽 유형·위치선(W2 일곱 번째 PR)
                                                                                                         "core": lambda v: isinstance(v, list), "stack": id_ok})   # 코어 범위·스택 벽 부분(W2 일곱 번째 PR)
    if err is None and "arc" in p:   # 곡선 벽(W2 ARC-04): 아직 받지 않는 형상 키와 함께 둘 수 없다. 축 검사는 arcgeom.problem
        bad = [k for k in ARC_EXCLUSIVE if k in p]
        if bad:
            return f"wall.arc 와 {bad[0]} 는 함께 둘 수 없다(곡선 벽은 아직 {ARC_EXCLUSIVE_LABEL} 를 지원하지 않는다)"
        why = AG.problem(p["axis"], p["arc"], p["thickness"])
        if why:
            return f"wall.arc: {why}"
        if any(not isinstance(o, dict) or o.get("shape") != "rect" for o in p.get("openings", ())):   # 곡선 벽의 모양 개구부는 사각만(W2 세 번째 PR)
            return "wall.openings: 곡선 벽의 모양 개구부는 사각(shape rect)만 된다"
    if err is None and "top_offset" in p and "top_level" not in p:   # 상단 구속(24단계): 오프셋은 상단 레벨과 함께만
        return "wall.top_offset 은 top_level 과 함께만 둔다"
    if err is None and "top_level" in p and "top_profile" in p:
        return "wall.top_level 과 top_profile 은 함께 둘 수 없다"
    if err is None and "layers" in p and abs(layers_total(p["layers"]) - p["thickness"]) > LAYER_SUM_TOL:
        return "wall.layers 두께의 합이 벽 두께와 다르다"
    if err is None and "core" in p and not ("layers" in p and WTY.core_ok(p["core"], len(p["layers"]))):   # 코어 범위(W2 일곱 번째 PR): 층 번호 [a, b)
        return "wall.core 는 층 구성의 층 번호 범위 [a, b] 여야 한다"
    top = p.get("top_profile") if err is None else None   # 벽 상단 윤곽(21단계): 없으면 이전과 같다
    if top is not None:
        ax = p["axis"]
        why = WT.profile_problem(top, AG.length(AG.info(ax, p["arc"])) if "arc" in p else sqrt((ax[2] - ax[0]) ** 2 + (ax[3] - ax[1]) ** 2))   # 곡선 벽은 호 길이
        if why:
            return f"wall.top_profile: {why}"
        if "base_offset" in p and WT.zmin(top) <= p["base_offset"]:   # 바닥 오프셋(22단계): 윤곽은 벽 바닥보다 높아야 한다
            return "wall.top_profile: 상단 윤곽의 높이 z 는 벽 바닥(base_offset)보다 커야 한다"
    if err is None and "openings" in p:   # 모양 개구부(18단계): 벽 입면 사각형(축 길이 x 높이) 안, 서로 겹치지 않는다
        ax = p["axis"]
        ln = AG.length(AG.info(ax, p["arc"])) if "arc" in p else sqrt((ax[2] - ax[0]) ** 2 + (ax[3] - ax[1]) ** 2)   # 곡선 벽은 호 길이(u 는 호 위 거리)
        b0 = WT.base(p)   # 바닥 오프셋(22단계): 개구부 높이는 레벨 기준이고 벽은 b0 에서 시작한다
        outer = WT.loop(top, 0.0, ln, b0) if top is not None else [(0.0, b0), (ln, b0), (ln, b0 + p["height"]), (0.0, b0 + p["height"])]
        why = SO.openings_problem(outer, p["openings"], absolute_sill="base_offset" in p) or (WT.under_problem(top, p["openings"]) if top is not None else None)
        if why:
            return f"wall.openings: {why}"
    return err


# 문이 호스트 벽 끝을 넘어도 되는 길이(W2 열세 번째 PR, ARC-23). 문 폭은 문틀 바깥 치수이고, 짧은 토막 벽이나 문 폭과 거의 같은 벽에서는
# 문틀이 이웃 벽(또는 벽 끝 마감)에 묻힌다. 그 한도를 문틀 단면 폭의 상한(기본 문 레시피 frame_w 최대 100mm)으로 둔다. 넘는 만큼은 벽 범위로 잘라
# 뚫고(도면은 이미 벽 범위로 자른다), 배치하는 커맨드는 경고한다. 0 이면 이전 규칙(축 안)과 같다.
DOOR_END_ALLOW = 100.0


def door_overhang(lo: float, hi: float, start: float, end: float) -> float:
    """문 구간 [lo, hi] 가 벽 범위 [start, end] 를 넘는 길이(양 끝 중 큰 쪽, 안이면 0)."""
    return max(0.0, start - lo, hi - end)


def door_span_ok(lo: float, hi: float, start: float, end: float, eps: float = 0.0) -> bool:
    """문 구간이 벽 범위를 DOOR_END_ALLOW 안으로만 넘는가."""
    return door_overhang(lo, hi, start, end) <= DOOR_END_ALLOW + eps


def _door_params(p):
    return _check_opt("door", p, {"name": _name, "width": _pos, "height": _pos, "sill": lambda v: num_ok(v) and v >= 0, "offset": num_ok}, {"leaf": _pos})


_SCALE = lambda v: num_ok(v) and v > 0   # noqa: E731


def _crop_ok(v) -> bool:
    """뷰 잘라내기 사각형 [x1, y1, x2, y2](뷰 좌표 mm: 평면은 x,y / 단면·입면은 s,z). x1<x2, y1<y2."""
    return isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v) and v[0] < v[2] and v[1] < v[3]


_TRUE = lambda v: v is True   # noqa: E731

# ---------------------------------------------------------------- 뷰 가시성·그래픽 재정의(VIEW-13)·뷰 필터(VIEW-14)·뷰 템플릿(VIEW-15) (W1 1-C)
# vg      = {범주: 재정의}. 재정의 = {"hidden": true, "color": "#rrggbb", "cut_weight": mm, "proj_weight": mm, "halftone": true} 중 하나 이상.
#           주석 범주(dim·tag·draft)는 숨기기만 한다.
# filters = [{"name", "cats": [범주], "rules": [{"field", "op", "value"}], 재정의...}] 위가 우선. 규칙은 모두 맞아야(AND) 걸린다(규칙이 없으면 범주 전체).
# template = 뷰 템플릿(vtemplate) id. 템플릿이 있으면 뷰의 vg·filters 대신 템플릿 것을 쓴다(Revit 처럼 템플릿이 정한다).
VG_MODEL_CATS = ("wall", "door", "finst", "slab", "stair", "railing", "elevator", "roof", "solid", "foreign", "space", "grid")
VG_ANNO_CATS = ("dim", "tag", "draft")
VG_CATS = VG_MODEL_CATS + VG_ANNO_CATS
VG_FILTER_CATS = tuple(c for c in VG_MODEL_CATS if c != "grid")   # 필터는 모델 요소에만 건다
VG_OVR_KEYS = ("hidden", "color", "cut_weight", "proj_weight", "halftone")
VG_FIELDS = ("name", "level", "material", "ifc_class", "keynote", "category", "fire_rating", "thickness", "width", "height")   # 그 밖은 "pset:속성셋.속성"
VG_OPS = ("eq", "ne", "contains", "not_contains", "begins", "gt", "ge", "lt", "le", "has", "missing")
VG_NUM_OPS = ("gt", "ge", "lt", "le")
VG_FILTERS_MAX = 32
VG_RULES_MAX = 8
VG_VALUE_MAX = 255
VG_WEIGHT = (0.05, 2.0)       # 재정의 선 굵기 범위(용지 mm)


def vg_num(s):
    """필터 규칙 값의 수(못 읽거나 유한하지 않으면 None)."""
    try:
        x = float(s)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def vg_field_ok(v) -> bool:
    if v in VG_FIELDS:
        return True
    if not (text_ok(v, 8, 160) and v.startswith("pset:")):
        return False
    ps, dot, pr = v[5:].partition(".")
    return bool(ps and dot and pr) and not any(ord(c) < 32 for c in v)


def _vg_w(v) -> bool:
    return num_ok(v) and VG_WEIGHT[0] <= v <= VG_WEIGHT[1]


_VG_OVR_CHECK = {"hidden": _TRUE, "halftone": _TRUE, "color": lambda c: color_ok(c), "cut_weight": _vg_w, "proj_weight": _vg_w}


def vg_ovr_ok(o, anno=False) -> bool:
    """재정의 하나: 키가 하나 이상, 값이 올바르다. 주석 범주는 hidden 만."""
    if not isinstance(o, dict) or not o or not set(o) <= set(VG_OVR_KEYS) or (anno and set(o) != {"hidden"}):
        return False
    return all(_VG_OVR_CHECK[k](x) for k, x in o.items())


def vg_ok(v) -> bool:
    return isinstance(v, dict) and len(v) > 0 and all(k in VG_CATS and vg_ovr_ok(o, k in VG_ANNO_CATS) for k, o in v.items())


def vg_rule_ok(r) -> bool:
    if not (isinstance(r, dict) and set(r) == {"field", "op", "value"} and vg_field_ok(r["field"]) and r["op"] in VG_OPS
            and text_ok(r["value"], 0, VG_VALUE_MAX)):
        return False
    if r["op"] in ("has", "missing"):
        return r["value"] == ""
    return r["op"] not in VG_NUM_OPS or vg_num(r["value"]) is not None


def vg_filters_ok(v) -> bool:
    if not isinstance(v, list) or not 0 < len(v) <= VG_FILTERS_MAX:
        return False
    names = set()
    for f in v:
        if not isinstance(f, dict) or not {"name", "cats", "rules"} <= set(f):
            return False
        ovr = {k: x for k, x in f.items() if k not in ("name", "cats", "rules")}
        cats, rules = f["cats"], f["rules"]
        if not (_name(f["name"]) and f["name"] not in names and isinstance(cats, list) and cats and len(set(cats)) == len(cats)
                and all(c in VG_FILTER_CATS for c in cats) and isinstance(rules, list) and len(rules) <= VG_RULES_MAX
                and all(vg_rule_ok(r) for r in rules) and vg_ovr_ok(ovr)):
            return False
        names.add(f["name"])
    return True


def _vtemplate_params(p):
    return _check_opt("vtemplate", p, {"name": _name}, {"vg": vg_ok, "filters": vg_filters_ok, "scale": lambda v: type(v) is int and 1 <= v <= 1000})


_VG_VIEW_KEYS = ("vg", "filters", "template")
# 뷰의 선택 파라미터(없으면 키 자체가 없다): crop 잘라내기(부위 상세 뷰), dims 승강기·계단 치수 자동 표기(평면·단면), finish 입면의 외부 마감 해치·재료 문자(입면),
# vg·filters·template 가시성·그래픽 재정의·필터·템플릿(W1 1-C, 모든 종류)
# parent 콜아웃 뷰의 부모 뷰 id(W1 1-C 2차, VIEW-06): 부모와 같은 종류이고 crop 이 콜아웃 범위다(부모 뷰에 콜아웃 기호로 그린다)
# rcp 반사 천장 평면·area 면적 평면("gross"·"rooms")(W1 1-C 3차, VIEW-02·21): 평면만, 둘 중 하나. legend 범례 뷰(VIEW-09)는 선택 키가 없다
# area "legal" 법정 바닥면적 평면(W1 연장 ARC-36): 벽 중심선 바닥면적 + 그 층의 법정 면적 객체(larea)
# drafting 제도 뷰(W1 연장 VIEW-07): 모델이 없어 선택 키가 없다(이름·축척만)
VIEW_OPTIONAL = {"plan": ("crop", "dims") + _VG_VIEW_KEYS + ("parent", "rcp", "area"), "section": ("with_site", "crop", "dims") + _VG_VIEW_KEYS + ("parent",),
                 "elevation": ("crop", "finish") + _VG_VIEW_KEYS + ("parent",), "site": _VG_VIEW_KEYS, "legend": (), "drafting": ()}
AREA_SCHEMES = ("gross", "rooms", "legal")
LEGEND_KINDS = ("symbols", "materials")
_VIEW_OPT_CHECK = {"with_site": _TRUE, "crop": _crop_ok, "dims": _TRUE, "finish": _TRUE, "vg": vg_ok, "filters": vg_filters_ok, "template": id_ok, "parent": id_ok,
                   "rcp": _TRUE, "area": lambda v: v in AREA_SCHEMES}


def _view_params(p):
    t = p.get("type") if isinstance(p, dict) else None
    if t == "plan":
        if "rcp" in p and "area" in p:
            return "view 는 반사 천장 평면과 면적 평면을 함께 가질 수 없다"
        return _check_opt("view", p, {"name": _name, "type": lambda v: v == "plan", "level": id_ok, "cut_height": num_ok,
                                      "view_depth": lambda v: num_ok(v) and v >= 0, "scale": _SCALE}, {k: _VIEW_OPT_CHECK[k] for k in VIEW_OPTIONAL["plan"]})
    if t == "section":
        return _check_opt("view", p, {"name": _name, "type": lambda v: v == "section", "p1": _pt_ok, "p2": _pt_ok,
                                      "depth": _pos, "flip": lambda v: type(v) is bool, "scale": _SCALE}, {k: _VIEW_OPT_CHECK[k] for k in VIEW_OPTIONAL["section"]})
    if t == "elevation":
        return _check_opt("view", p, {"name": _name, "type": lambda v: v == "elevation", "dir": lambda v: v in ("N", "E", "S", "W"),
                                      "scale": _SCALE}, {k: _VIEW_OPT_CHECK[k] for k in VIEW_OPTIONAL["elevation"]})
    if t == "legend":   # 범례 뷰(W1 1-C 3차): 모델을 자르지 않는다
        return _check_opt("view", p, {"name": _name, "type": lambda v: v == "legend", "legend": lambda v: v in LEGEND_KINDS, "scale": _SCALE}, {})
    if t == "drafting":   # 제도 뷰(W1 연장 VIEW-07): 모델 없이 2D 작도만 담는다
        return _check_opt("view", p, {"name": _name, "type": lambda v: v == "drafting", "scale": _SCALE}, {})
    if t == "site":   # 배치도: 모델 전체를 위에서 본 뷰라 레벨·절단 높이가 없다
        return _check_opt("view", p, {"name": _name, "type": lambda v: v == "site", "scale": _SCALE}, {k: _VIEW_OPT_CHECK[k] for k in VIEW_OPTIONAL["site"]})
    return "view 의 종류가 올바르지 않다"


def _ref(v) -> bool:
    return text_ok(v, 1, 80)


DIM_MEASURES = ("angle", "radius", "diameter", "arc")   # 선형 밖의 치수 종류(annot.MEASURES 와 같다). 선형 치수는 measure 키가 없다
DIM_CHAIN_KINDS = ("chain", "baseline")          # 연속·기준선 치수 묶음 종류(annot.CHAIN_KINDS 와 같다)


def unit_ok(v) -> bool:
    """길이 1 인 평면 방향 [ux, uy](허용 1e-6)."""
    return _pt_ok(v) and abs(v[0] * v[0] + v[1] * v[1] - 1.0) <= 1e-6


def _dim_params(p):
    if isinstance(p, dict) and ("along" in p or "dl" in p):   # W1 연장(ANN-02): 연속·기준선 치수의 한 칸(평면 선형 치수만, measure·at 과 함께 둘 수 없다)
        if not ("along" in p and "dl" in p) or "measure" in p or "at" in p or not unit_ok(p["along"]) or not _pt_ok(p["dl"]):
            return "dim.along·dl 값이 올바르지 않다"
        p = {k: v for k, v in p.items() if k not in ("along", "dl")}
    if isinstance(p, dict) and ("group" in p or "gk" in p):   # 연속·기준선 치수 묶음 표시(높이 치수에도 쓴다)
        if not ("group" in p and "gk" in p) or not id_ok(p["group"]) or p["gk"] not in DIM_CHAIN_KINDS:
            return "dim.group·gk 값이 올바르지 않다"
        p = {k: v for k, v in p.items() if k not in ("group", "gk")}
    base = p
    if isinstance(p, dict) and "at" in p:   # 높이 치수만 at 이 있다
        base = {k: v for k, v in p.items() if k != "at"}
        if not _pt_ok(p["at"]):
            return "dim.at 값이 올바르지 않다"
    if isinstance(p, dict) and "measure" in p:   # W1 1-B 2차: 각도·반지름·지름(평면 치수만, at 과 함께 둘 수 없다)
        if p["measure"] not in DIM_MEASURES or "at" in p:
            return "dim.measure 값이 올바르지 않다"
        base = {k: v for k, v in p.items() if k != "measure"}
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
    if "offset" in p and "also_cuts" in p:   # 함께 자르는 벽(25단계): 호스팅 인스턴스만, 1~4개의 서로 다른 벽 id
        v = p["also_cuts"]
        if not (isinstance(v, list) and 1 <= len(v) <= 4 and all(id_ok(x) for x in v) and len(set(v)) == len(v)):
            return "finst.also_cuts 값이 올바르지 않다"
        spec["also_cuts"] = lambda v: True
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


# ---- 방사(원형·나선) 계단(W2 열일곱 번째 PR, ARC-29): 중심 둘레로 같은 각의 디딤판이 돈다. 걸음선(run)·꺾임(turn) 대신 params.spiral 하나를 둔다.
#   spiral = {"center": [x, y] 중심, "r_in": 안쪽 반지름 mm(0 이면 중심 기둥 없이 한 점), "start": 첫 챌판 각(도, +x 가 0·반시계, [0, 360)),
#             "sweep": 총 회전각(도, + 반시계·- 시계, 0 이 아니고 절댓값 STAIR_SPIRAL_SWEEP_MAX 이하)}
#   바깥 반지름 = r_in + width. 디딤판 = 챌판 수 - 1 개(마지막 챌판 위는 위층 바닥, 직선 계단과 같은 규약), 디딤판 하나의 각 = sweep / (챌판 수 - 1).
STAIR_SPIRAL_KEYS = frozenset(("center", "r_in", "start", "sweep"))
STAIR_SPIRAL_SWEEP_MAX = 720.0   # 총 회전각 상한(두 바퀴)
STAIR_SPIRAL_STEP_MAX = 90.0     # 디딤판 하나의 각 상한(볼록 조각·실무 범위)
STAIR_SPIRAL_R_MAX = 100000.0    # 안쪽 반지름 상한 mm
STAIR_SPIRAL_T = 150.0           # 디딤판 밑으로 더 채우는 두께 mm(아래 챌판 위치에서 이만큼 아래가 밑면. 바로 아래 디딤판과 이어진 톱니 띠가 된다)
STAIR_SPIRAL_ARC = 11.25         # 바깥 원호를 나누는 현 하나의 각 상한(도, 원 한 바퀴 32 현 = 평지붕 원호와 같은 분할)
STAIR_WALK_OFFSET = 300.0        # 보행선: 안쪽 끝에서 이만큼(폭의 절반 이하) 떨어진 원(피난·방화규칙 15조 3항 '돌음계단 단너비는 좁은 너비 끝부분에서 30cm 위치에서 측정')
STAIR_RISE_MAX = 180.0           # 계단 검사: 챌판 높이 상한 mm(주택건설기준 16조·피난·방화규칙 15조 2항 중·고등학교 기준과 같은 18cm)
STAIR_TREAD_MIN = 260.0          # 계단 검사: 디딤판 깊이(단너비) 하한 mm(같은 근거 26cm)


def _spiral_ok(v) -> bool:
    return (isinstance(v, dict) and set(v) == STAIR_SPIRAL_KEYS and _pt_ok(v["center"]) and num_ok(v["r_in"]) and 0 <= v["r_in"] <= STAIR_SPIRAL_R_MAX
            and num_ok(v["start"]) and 0 <= v["start"] < 360.0 and num_ok(v["sweep"]) and v["sweep"] != 0 and abs(v["sweep"]) <= STAIR_SPIRAL_SWEEP_MAX)


def stair_spiral_problem(p) -> str | None:
    """방사 계단의 교차 검사(디딤판 하나의 각). 안 되면 이유 문장."""
    step = abs(p["spiral"]["sweep"]) / (p["risers"] - 1)
    if step > STAIR_SPIRAL_STEP_MAX + 1e-9:
        return f"디딤판 하나의 각({step:g}도)이 {STAIR_SPIRAL_STEP_MAX:g}도를 넘는다(챌판 수를 늘리거나 총 회전각을 줄이세요)"
    return None


def _spiral_pt(c, r, a):
    sn, cs = sincos_deg(a)
    return (c[0] + r * cs, c[1] + r * sn)


def stair_spiral_layout(p) -> dict:
    """방사 계단의 형상 배치(순수 함수: 그리기·IFC·수량·난간이 같은 값을 쓴다). z 는 계단 아래 끝 기준 상대 높이.
      {"treads": [{"poly": ((x,y),...) 반시계 볼록 다각형, "zb": 밑면, "z": 윗면, "a0": 시작 각, "a1": 끝 각}, ...],
       "center", "r_in", "r_out", "r_mid": 폭 가운데 반지름(진행 화살표), "r_walk": 보행선 반지름, "start", "sweep", "step": 디딤판 하나의 각(부호 있음),
       "rise": 챌판 높이, "tread": 보행선 위 디딤판 깊이, "walk_len": 보행선 길이(첫 챌판 ~ 위 끝)}
    디딤판 j(0..n-2): 각 start + step x j ~ start + step x (j+1), 윗면 = (j+1) x 챌판 높이, 밑면 = max(0, j x 챌판 높이 - STAIR_SPIRAL_T).
    다각형 = 안쪽 시작점, 바깥 원호(STAIR_SPIRAL_ARC 이하 현으로 나눈 점들), 안쪽 끝점(안쪽 반지름 0 이면 중심 한 점). 안쪽은 현 하나라 다각형이 볼록하다.
    각은 sincos_deg(사칙연산 급수)로 구해 플랫폼 libm 에 기대지 않는다."""
    sp = p["spiral"]
    c = (float(sp["center"][0]), float(sp["center"][1]))
    n, w = p["risers"], p["width"]
    ri = float(sp["r_in"])
    ro = ri + w
    rise = p["height"] / n
    m = n - 1
    step = sp["sweep"] / m
    k = max(1, int(-(-abs(step) // STAIR_SPIRAL_ARC)))
    treads = []
    for j in range(m):
        a0, a1 = sp["start"] + step * j, sp["start"] + step * (j + 1)
        outer = [_spiral_pt(c, ro, a0 + (a1 - a0) * i / k) for i in range(k + 1)]
        poly = ([_spiral_pt(c, ri, a0)] + outer + [_spiral_pt(c, ri, a1)]) if ri > 0 else ([c] + outer)
        a2 = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))
        if a2 < 0:   # 반시계로 맞춘다(시계 방향 계단)
            poly.reverse()
        corners = (outer[0], outer[-1]) + ((poly[0], poly[-1]) if ri > 0 else (c,))   # 각기둥의 실제 모서리(바깥 원호 중간 현 꼭짓점은 매끈한 면이라 뺀다)
        treads.append({"poly": tuple(poly), "zb": max(0.0, j * rise - STAIR_SPIRAL_T), "z": (j + 1) * rise, "a0": a0, "a1": a1, "corners": corners})
    rw = ri + min(STAIR_WALK_OFFSET, w / 2.0)
    rad = 3.141592653589793 / 180.0
    return {"treads": treads, "center": c, "r_in": ri, "r_out": ro, "r_mid": ri + w / 2.0, "r_walk": rw, "start": float(sp["start"]), "sweep": float(sp["sweep"]),
            "step": step, "rise": rise, "tread": rw * abs(step) * rad, "walk_len": rw * abs(sp["sweep"]) * rad}


def stair_spiral_frame(p) -> tuple:
    """방사 계단의 '시작 틀': (시작점 x, y = 폭 가운데 원 위 첫 챌판 점, 진행 방향 ux, uy = 그 점의 접선(오르는 쪽), 폭 가운데 원호 길이).
    걸음선 왼쪽(+) 이 반시계 계단은 안쪽, 시계 계단은 바깥쪽이다. 직선 계단의 걸음선 틀을 쓰는 곳(폭 치수·태그 위치)이 같은 규약으로 쓴다."""
    sp = p["spiral"]
    rm = sp["r_in"] + p["width"] / 2.0
    sn, cs = sincos_deg(sp["start"])
    g = 1.0 if sp["sweep"] > 0 else -1.0
    return (sp["center"][0] + rm * cs, sp["center"][1] + rm * sn, -sn * g, cs * g, rm * abs(sp["sweep"]) * 3.141592653589793 / 180.0)


def stair_pieces(p) -> list:
    """계단의 속이 찬 볼록 조각 [(다각형, 밑면 z, 윗면 z), ...] (계단 아래 끝 기준 상대 높이): 직선·꺾인 단 묶음은 디딤판마다, 층참·돌음 쐐기·방사 디딤판. 체적 수량이 쓴다."""
    if "spiral" in p:
        return [(t["poly"], t["zb"], t["z"]) for t in stair_spiral_layout(p)["treads"]]
    if "sketch" in p:
        return [(q, t["zb"], t["z"]) for t in stair_sketch_layout(p)["treads"] for q in t["pieces"]]
    lay = stair_layout(p)
    out = []
    w = p["width"]
    for fl in lay["flights"]:
        x1, y1, x2, y2 = fl["run"]
        ln = fl["length"]
        ux, uy = (x2 - x1) / ln, (y2 - y1) / ln
        h = w / 2.0

        def q(t, mm):
            return (x1 + ux * t - uy * mm, y1 + uy * t + ux * mm)
        for k in range(fl["n"] - 1):
            out.append(((q(k * fl["td"], -h), q((k + 1) * fl["td"], -h), q((k + 1) * fl["td"], h), q(k * fl["td"], h)),
                        fl["z0"] - fl["skirt"], fl["z0"] + (k + 1) * lay["rise"]))
    if lay["landing"] is not None:
        ld = lay["landing"]
        out.append((ld["poly"], ld["z"] - ld["t"], ld["z"]))
    for wd in (lay["winders"]["wedges"] if "winders" in lay else ()):
        out.append((wd["poly"], wd["zb"], wd["z"]))
    return out


def stair_volume(p) -> float:
    """계단 체적 mm³ = 볼록 조각마다 평면 넓이 x 높이의 합."""
    tot = 0.0
    for poly, zb, zt in stair_pieces(p):
        a2 = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))
        tot += abs(a2) / 2.0 * (zt - zb)
    return tot


def stair_rule_warnings(p) -> list:
    """계단 검사(경고, 거절하지 않는다): 챌판 높이 > STAIR_RISE_MAX, 디딤판 깊이 < STAIR_TREAD_MIN. 방사 계단은 보행선(안쪽 끝에서 300mm) 위 디딤 길이로 잰다.
    반환 [{"code", "message", "value", "limit"}]. 용도별 기준(초등학교 16cm·관람석 등)은 사용자가 확인한다."""
    rise = p["height"] / p["risers"]
    if "spiral" in p:
        tread = stair_spiral_layout(p)["tread"]
    elif "sketch" in p:   # 스케치 계단: 사용자가 그린 보행선 위 디딤 깊이
        tread = stair_sketch_layout(p)["td"]
    else:
        tread = stair_layout(p)["flights"][0]["td"]
    out = []
    if rise > STAIR_RISE_MAX + 1e-9:
        out.append({"code": "STAIR_RISE", "message": f"챌판 높이 {rise:.1f}mm 가 {STAIR_RISE_MAX:g}mm 를 넘는다", "value": round(rise, 3), "limit": STAIR_RISE_MAX})
    if tread < STAIR_TREAD_MIN - 1e-9:
        where = "보행선(안쪽 끝에서 300mm) 위 " if "spiral" in p else ""
        out.append({"code": "STAIR_TREAD", "message": f"{where}디딤판 깊이 {tread:.1f}mm 가 {STAIR_TREAD_MIN:g}mm 보다 작다", "value": round(tread, 3), "limit": STAIR_TREAD_MIN})
    wt = stair_winder_tread(p)
    if wt is not None and wt < STAIR_TREAD_MIN - 1e-9:   # 돌음 회전부(꺾인 모서리·turn.winders): 안쪽 끝에서 300mm 보행선 위 디딤 깊이(W2 스무 번째 PR)
        out.append({"code": "STAIR_WINDER_TREAD", "message": f"돌음 회전부의 보행선(회전 안쪽 끝에서 {STAIR_WALK_OFFSET:g}mm) 위 디딤판 깊이 {wt:.1f}mm 가 {STAIR_TREAD_MIN:g}mm 보다 작다",
                    "value": round(wt, 3), "limit": STAIR_TREAD_MIN})
    return out


def stair_winder_tread(p) -> float | None:
    """돌음 회전부 디딤판의 보행선 위 깊이 중 가장 작은 값(없으면 None). 보행선은 회전 안쪽 끝(스케치 모서리 점·돌음 계단 회전 중심에서 폭/2 만큼 떨어진 안쪽 가장자리)에서
    STAIR_WALK_OFFSET(폭의 절반 이하) 떨어진 원이다(방사 계단과 같은 피난·방화규칙 15조 3항 기준). 층참(모서리 디딤판 수 1)은 디딤판이 아니라 재지 않는다."""
    w = p["width"]
    if "sketch" in p:
        ts = [min(STAIR_WALK_OFFSET, w / 2.0) * abs(c["sw"]) * _PI / 180.0 / c["m"] for c in stair_sketch_layout(p)["corners"] if c["m"] >= 2]
        return min(ts) if ts else None
    t = p.get("turn")
    if t is not None and t.get("winders") is not None and "run" in p:
        wd = stair_layout(p)["winders"]   # 회전 중심에서 안쪽 가장자리까지 = 보행선 반지름 - 폭/2(L 은 0, U 는 간격/2)
        return (wd["radius"] - w / 2.0 + min(STAIR_WALK_OFFSET, w / 2.0)) * _PI * wd["sweep"] / (180.0 * t["winders"])
    return None


# ---- 스케치 계단(W2 열일곱 번째 PR, ARC-29): 보행선(꺾은선, 변마다 선택 볼록도 = 원호) 하나 + 폭. 보행선 길이를 디딤판 수(챌판 수 - 1)로 같게 나눠
# 디딤판마다 보행선에 수직인 챌판 선 사이를 채운다(보행선 = 계단 폭 가운데). 이웃 변은 접선이 이어져야 한다(직선 + 원호 보행선). 꺾인 모서리는 corners 로 지정한 꼭짓점만(아래).
#   sketch = {"path": [[x, y], ...] 2~STAIR_SKETCH_PTS 점, "bulges": [변마다 tan(중심각/4), + 반시계] (선택, 하나라도 0 이 아니면 둔다),
#             "corners": [안쪽 꼭짓점마다 모서리 디딤판 수 0~STAIR_CORNER_M_MAX] (선택, 하나라도 0 이 아니면 둔다)}
# 꺾인 모서리(W2 스무 번째 PR): corners[i] > 0 인 꼭짓점은 접선이 끊겨도 된다. 그 모서리는 보행선이 반지름 폭/2 의 접하는 원호(중심 = 안쪽 모서리 점)로 이어지고
# 원호 위 같은 길이로 나눈 쐐기 디딤판 corners[i] 개(1 이면 층참 하나)가 안쪽 모서리 점에서 퍼진다(균등 보행선 분할 돌음 계단, 기존 turn.winders 와 같은 방식).
STAIR_SKETCH_PTS = 64
STAIR_SKETCH_TANGENT = 0.5   # 이웃 변 접선이 이어졌다고 보는 각 허용(도)
STAIR_CORNER_MIN, STAIR_CORNER_MAX = 10.0, 135.0   # 모서리 꺾임각 범위(도): 더 작으면 원호로 잇고, 더 크면 두 모서리로 나눈다
STAIR_CORNER_M_MAX = STAIR_WINDERS_MAX   # 모서리 하나의 디딤판 수 상한(1 = 층참)
_PI = 3.141592653589793


def _sketch_ok(v) -> bool:
    if not isinstance(v, dict) or not {"path"} <= set(v) <= {"path", "bulges", "corners"}:
        return False
    pts = v["path"]
    if not (isinstance(pts, list) and 2 <= len(pts) <= STAIR_SKETCH_PTS and all(_pt_ok(q) for q in pts)):
        return False
    if any((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2 < 1.0 for i in range(len(pts) - 1)):
        return False
    if "bulges" in v:
        b = v["bulges"]
        if not (isinstance(b, list) and len(b) == len(pts) - 1 and all(num_ok(x) and abs(x) <= 1.0 for x in b) and any(b)):
            return False
    if "corners" in v:
        c = v["corners"]
        if not (isinstance(c, list) and len(c) == len(pts) - 2 and all(type(x) is int and 0 <= x <= STAIR_CORNER_M_MAX for x in c) and any(c)):
            return False
    return True


def sketch_raw_segs(sk) -> list:
    """보행선 변들(모서리 처리 전 그린 그대로): 직선 {"kind": "line", "a", "u": 단위 방향, "len"} 또는 원호 {"kind": "arc", "c": 중심, "r", "a0": 시작 각(도), "sw": 부호 있는 중심각(도), "len"}."""
    pts = [(float(q[0]), float(q[1])) for q in sk["path"]]
    bs = sk.get("bulges") or [0.0] * (len(pts) - 1)
    out = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        c = sqrt(dx * dx + dy * dy)
        bu = float(bs[i])
        if bu == 0:
            out.append({"kind": "line", "a": a, "u": (dx / c, dy / c), "len": c})
            continue
        d = c * (1.0 - bu * bu) / (4.0 * bu)          # 현 가운데에서 중심까지(왼쪽 법선 방향, 부호 있음): solidops.bulge_arc 와 같은 규약
        cx, cy = (a[0] + b[0]) / 2.0 - dy / c * d, (a[1] + b[1]) / 2.0 + dx / c * d
        r = c * (1.0 + bu * bu) / (4.0 * abs(bu))
        sw = 4.0 * AG.atan2(bu, 1.0) * 180.0 / 3.141592653589793
        a0 = AG.atan2(a[1] - cy, a[0] - cx) * 180.0 / 3.141592653589793
        out.append({"kind": "arc", "c": (cx, cy), "r": r, "a0": a0, "sw": sw, "len": r * abs(sw) * 3.141592653589793 / 180.0})
    return out


def _corner_turn(sa, sb, h):
    """직선 변 sa 의 끝에서 직선 변 sb 로 꺾이는 모서리: (부호 있는 꺾임각 도(+ 왼쪽 = 반시계), 접하는 원호가 변 끝에서 물러나는 거리 d = h x tan(각/2)).
    h = 폭/2 = 보행선 원호 반지름. 각이 STAIR_CORNER_MAX 이하일 때만 d 가 뜻을 가진다."""
    u0, u1 = sa["u"], sb["u"]
    th = AG.atan2(u0[0] * u1[1] - u0[1] * u1[0], u0[0] * u1[0] + u0[1] * u1[1]) * 180.0 / _PI
    sn, cs = sincos_deg(abs(th) / 2.0)
    return th, (h * sn / cs if cs > 1e-9 else float("inf"))


def sketch_segs(sk, w=None) -> list:
    """보행선 변들. 모서리(sk["corners"])가 없으면 그린 그대로(sketch_raw_segs)다. 모서리가 있으면(폭 w 필요) 꺾인 꼭짓점마다 두 직선 변의 끝을 d 만큼 줄이고
    그 사이에 접하는 원호 {"kind": "corner", "c": 안쪽 모서리 점, "r": 폭/2, "a0", "sw", "len", "w": 폭, "m": 디딤판 수} 를 끼운다. 모서리 변 하나의 보행선은
    안쪽 모서리 점 둘레 반지름 폭/2 의 원이다(꺾는 쪽 폭/2 = 안쪽 모서리 점, 반대쪽 폭/2 = 바깥 둘레)."""
    raw = sketch_raw_segs(sk)
    cors = sk.get("corners")
    if not cors or not any(cors):
        return raw
    h = w / 2.0
    n = len(raw)
    cut0, cut1 = [0.0] * n, [0.0] * n   # 변의 시작·끝에서 줄이는 길이
    info = {}
    for i in range(n - 1):
        if cors[i]:
            th, d = _corner_turn(raw[i], raw[i + 1], h)
            cut1[i], cut0[i + 1] = d, d
            info[i] = th
    out = []
    for i, s in enumerate(raw):
        if s["kind"] == "line" and (cut0[i] or cut1[i]):
            u = s["u"]
            s = {"kind": "line", "a": (s["a"][0] + u[0] * cut0[i], s["a"][1] + u[1] * cut0[i]), "u": u, "len": s["len"] - cut0[i] - cut1[i]}
        out.append(s)
        if i in info:
            th = info[i]
            g = 1.0 if th > 0 else -1.0
            u0 = raw[i]["u"]
            a0 = AG.atan2(-g * u0[0], g * u0[1]) * 180.0 / _PI
            d = cut1[i]
            tx, ty = raw[i + 1]["a"][0] - u0[0] * d, raw[i + 1]["a"][1] - u0[1] * d   # 접점(변 끝에서 d 앞)
            c = (tx - g * h * u0[1], ty + g * h * u0[0])                                  # 안쪽 모서리 점: 접점에서 꺾는 쪽으로 폭/2
            out.append({"kind": "corner", "c": c, "r": h, "a0": a0, "sw": th, "len": h * abs(th) * _PI / 180.0, "w": w, "m": cors[i]})
    return out


def seg_pt(s, t, m=0.0):
    """변 s 위 길이 t 의 보행선 점에서 왼쪽으로 m 떨어진 점. 모서리 변(kind "corner")은 m = 0 이면 보행선 원 위의 점이다. m != 0 이면 안쪽 모서리 점에서 같은 각으로 나간
    반직선 위에서 모서리 점으로부터 거리 rr(= 반지름 -/+ m, 안쪽 가장자리 0 ~ 바깥 가장자리 폭)를 바깥 둘레까지의 거리 비율로 늘린 점이다. 바깥 둘레는 모서리 점에서
    두 단 묶음의 바깥 가장자리 선(폭만큼 떨어진 평행선 둘)이 만나는 꺾은선이라 바깥 가장자리(rr = 폭)는 그 위에, 안쪽으로 들인 난간선(rr < 폭)은 그것과 닮은 꼴이다."""
    if s["kind"] == "line":
        ux, uy = s["u"]
        return (s["a"][0] + ux * t - uy * m, s["a"][1] + uy * t + ux * m)
    sn, cs = sincos_deg(s["a0"] + s["sw"] * t / s["len"])
    rr = s["r"] - (1.0 if s["sw"] > 0 else -1.0) * m   # 반시계 호의 왼쪽은 중심 쪽
    if s["kind"] == "corner" and m != 0:
        ph = abs(s["sw"]) * t / s["len"]
        rr = rr / sincos_deg(min(ph, abs(s["sw"]) - ph))[1]   # 바깥 둘레의 두 변은 모서리 점에서 폭만큼 떨어진 평행선이라 반직선 길이 = 거리 / cos(각)
    return (s["c"][0] + rr * cs, s["c"][1] + rr * sn)


def seg_tan(s, t):
    """변 s 위 길이 t 의 진행 방향(단위)."""
    if s["kind"] == "line":
        return s["u"]
    sn, cs = sincos_deg(s["a0"] + s["sw"] * t / s["len"])
    g = 1.0 if s["sw"] > 0 else -1.0
    return (-sn * g, cs * g)


def stair_sketch_problem(p) -> str | None:
    """스케치 계단의 교차 검사: 이웃 변의 접선이 이어지는가(모서리로 지정한 꼭짓점은 꺾임각 범위·직선 변 둘 사이인가), 원호 반지름이 폭의 절반보다 큰가,
    모서리가 변을 다 먹지 않는가, 디딤판 수가 모서리와 구간마다 모자라지 않은가."""
    sk = p["sketch"]
    segs = sketch_raw_segs(sk)
    h = p["width"] / 2.0
    cors = sk.get("corners") or [0] * (len(segs) - 1)
    for i, s in enumerate(segs):
        if s["kind"] == "arc" and s["r"] <= h + 1.0:
            return f"보행선 {i + 1}번 원호의 반지름({s['r']:.0f})이 계단 폭의 절반({h:g}) + 1mm 보다 커야 한다"
        if i:
            t0, t1 = seg_tan(segs[i - 1], segs[i - 1]["len"]), seg_tan(s, 0.0)
            cr, dt = t0[0] * t1[1] - t0[1] * t1[0], t0[0] * t1[0] + t0[1] * t1[1]
            if cors[i - 1] == 0:
                if dt <= 0 or abs(cr) > sincos_deg(STAIR_SKETCH_TANGENT)[0]:
                    return (f"보행선 {i + 1}번 꼭짓점에서 꺾인다(이웃 변의 접선이 이어져야 한다: 직선과 원호를 접하게 잇거나, 꺾인 모서리는 corners 로 모서리 디딤판 수를 주세요)")
                continue
            if segs[i - 1]["kind"] != "line" or s["kind"] != "line":
                return f"보행선 {i + 1}번 꼭짓점의 모서리(corners)는 직선 변 둘 사이에서만 쓸 수 있다"
            th, d = _corner_turn(segs[i - 1], s, h)
            if not STAIR_CORNER_MIN <= abs(th) <= STAIR_CORNER_MAX:
                return (f"보행선 {i + 1}번 꼭짓점의 꺾임각({abs(th):.1f}도)이 {STAIR_CORNER_MIN:g}~{STAIR_CORNER_MAX:g}도 밖이다"
                        f"(더 완만하면 원호로 잇고, 더 급하면 모서리 둘로 나누세요)")
    if sk.get("corners"):
        left = [s["len"] for s in segs]   # 모서리마다 접하는 원호가 양쪽 직선 변에서 d 만큼 가져간 나머지
        for i, m in enumerate(cors):
            if m:
                d = _corner_turn(segs[i], segs[i + 1], h)[1]
                left[i] -= d
                left[i + 1] -= d
        for i, (s, ln) in enumerate(zip(segs, left)):
            if ln < 1.0:
                return f"보행선 {i + 1}번 변이 모서리 원호에 다 먹혀 1mm 가 남지 않는다(꼭짓점을 더 띄우거나 계단 폭을 줄이세요)"
        runs = 1 + sum(1 for m in cors if m)
        if p["risers"] - 1 - sum(cors) < runs:
            return (f"챌판 수가 모자란다: 디딤판 {p['risers'] - 1}개 중 모서리 디딤판이 {sum(cors)}개라 직선·원호 구간 {runs}개에 하나씩도 돌아가지 않는다")
    return None


def _split_counts(ls, r) -> list:
    """구간 길이 ls 에 디딤판 r 개(r >= 구간 수)를 길이에 비례해 나눈다(구간마다 최소 1, 최대 나머지법·동률은 앞 구간 먼저). 결정적이다."""
    ideal = [r * x / sum(ls) for x in ls]
    cnt = [max(1, int(v)) for v in ideal]
    while sum(cnt) < r:
        k = max(range(len(ls)), key=lambda i: (ideal[i] - cnt[i], -i))
        cnt[k] += 1
    while sum(cnt) > r:
        k = min((i for i in range(len(ls)) if cnt[i] > 1), key=lambda i: (ideal[i] - cnt[i], i))
        cnt[k] -= 1
    return cnt


def stair_sketch_layout(p) -> dict:
    """스케치 계단의 형상 배치(순수 함수: 그리기·IFC·수량·난간이 같은 값을 쓴다). z 는 계단 아래 끝 기준 상대 높이.
      {"segs": 보행선 변들(모서리가 있으면 직선·원호 변 사이에 kind "corner" 원호 변), "starts": 변마다 보행선 위 시작 길이, "length": 보행선 길이(모서리는 접하는 원호로 돈다),
       "td": 디딤판 깊이(보행선 위, 직선·원호 구간 디딤판 중 가장 짧은 값. 모서리가 없으면 모두 같다), "rise", "corners": [{"seg": 변 번호, "m": 디딤판 수, "sw": 꺾임각(도)}, ...],
       "treads": [{"s0", "s1": 보행선 위 시작·끝 길이, "z": 윗면, "zb": 밑면(0 = 직선 계단처럼 아래 끝까지 속이 찬 덩어리), "pieces": [볼록 사각형(모서리 디딤판은 쐐기 다각형), ...],
                   "sides": [((x,y),(x,y)), ...] 옆선 조각(왼쪽·오른쪽, 쐐기는 바깥 둘레), "risers": (챌판 선 시작, 끝) 두 개(시작·끝 챌판, 각 (왼쪽 점, 오른쪽 점)),
                   "winder": 모서리 디딤판이면 {"seg", "q": 모서리 안 번호, "m"} 아니면 없다}, ...]}
    모서리가 없으면 보행선 길이를 디딤판 수(챌판 수 - 1)로 같게 나눈다. 모서리가 있으면 모서리 디딤판(원호를 같은 길이로 m 개)을 뺀 나머지 디딤판을 직선·원호 구간(run)
    길이에 비례해 정수로 나누고 구간 안에서 같게 나눈다. 디딤판의 보행선 구간을 변마다 자르고, 원호 조각은 STAIR_SPIRAL_ARC 이하로 더 나눠 볼록 사각형(안쪽·바깥 현)을 만든다."""
    segs = sketch_segs(p["sketch"], p["width"])
    n, h = p["risers"], p["width"] / 2.0
    rise = p["height"] / n
    total = sum(s["len"] for s in segs)
    starts = []
    acc = 0.0
    for s in segs:
        starts.append(acc)
        acc += s["len"]
    spans = []   # (시작, 끝, 모서리 정보 또는 None)
    if not any(s["kind"] == "corner" for s in segs):
        td = total / (n - 1)
        for j in range(n - 1):
            spans.append((j * td, (j + 1) * td if j < n - 2 else total, None))
    else:
        groups = []   # 구간(run) = 이어진 직선·원호 변 번호들, 모서리 = 변 하나
        for i, s in enumerate(segs):
            if s["kind"] == "corner":
                groups.append(("corner", [i]))
            elif groups and groups[-1][0] == "run":
                groups[-1][1].append(i)
            else:
                groups.append(("run", [i]))

        def end_of(i):
            return starts[i + 1] if i + 1 < len(segs) else total
        runs = [g for g in groups if g[0] == "run"]
        cnt = _split_counts([end_of(g[1][-1]) - starts[g[1][0]] for g in runs], n - 1 - sum(s["m"] for s in segs if s["kind"] == "corner"))
        k = 0
        for kind, ids in groups:
            if kind == "run":
                a, b, c = starts[ids[0]], end_of(ids[-1]), cnt[k]
                td_k = (b - a) / c
                k += 1
                for i in range(c):
                    spans.append((a + i * td_k, a + (i + 1) * td_k if i < c - 1 else b, None))
            else:
                ci = ids[0]
                s, m = segs[ci], segs[ci]["m"]
                for q in range(m):
                    spans.append((starts[ci] + s["len"] * q / m, starts[ci] + s["len"] * (q + 1) / m if q < m - 1 else end_of(ci), (ci, q, m)))
        td = min(s1 - s0 for s0, s1, w in spans if w is None)

    def at(sv):
        """보행선 길이 sv 의 (변, 변 안 길이)."""
        for i in range(len(segs) - 1, -1, -1):
            if sv >= starts[i] - 1e-9:
                return segs[i], min(max(sv - starts[i], 0.0), segs[i]["len"])
        return segs[0], 0.0

    def cut(sv):
        s, t = at(sv)
        return (seg_pt(s, t, h), seg_pt(s, t, -h))
    treads = []
    for j, (s0, s1, wd) in enumerate(spans):
        pieces, sides = [], []
        if wd is not None:   # 모서리 디딤판: 안쪽 모서리 점에서 퍼지는 쐐기(같은 각), 바깥은 폭만큼 떨어진 두 변 둘레
            ci, q, m = wd
            s = segs[ci]
            out = -(1.0 if s["sw"] > 0 else -1.0) * h   # 바깥 가장자리 쪽 옆 거리(왼쪽 +)
            t0, t1 = s["len"] * q / m, s["len"] * (q + 1) / m
            half = s["len"] / 2.0
            pts = [seg_pt(s, t0, out)] + ([seg_pt(s, half, out)] if t0 < half - 1e-9 and t1 > half + 1e-9 else []) + [seg_pt(s, t1, out)]
            wedge = [s["c"]] + pts
            if sum(wedge[e][0] * wedge[(e + 1) % len(wedge)][1] - wedge[(e + 1) % len(wedge)][0] * wedge[e][1] for e in range(len(wedge))) < 0:
                wedge.reverse()
            pieces.append(tuple(wedge))
            sides += [(pts[e], pts[e + 1]) for e in range(len(pts) - 1)]
            treads.append({"s0": s0, "s1": s1, "z": (j + 1) * rise, "zb": 0.0, "pieces": pieces, "sides": sides,
                           "risers": (cut(s0), cut(s1)), "winder": {"seg": ci, "q": q, "m": m}})
            continue
        for i, s in enumerate(segs):
            if s["kind"] == "corner":
                continue
            lo, hi = max(s0, starts[i]), min(s1, starts[i] + s["len"])
            if hi - lo <= 1e-9:
                continue
            t0, t1 = lo - starts[i], hi - starts[i]
            k = 1 if s["kind"] == "line" else max(1, int(-(-(abs(s["sw"]) * (t1 - t0) / s["len"]) // STAIR_SPIRAL_ARC)))
            for q in range(k):
                ta, tb = t0 + (t1 - t0) * q / k, t0 + (t1 - t0) * (q + 1) / k
                quad = [seg_pt(s, ta, -h), seg_pt(s, tb, -h), seg_pt(s, tb, h), seg_pt(s, ta, h)]
                a2 = sum(quad[e][0] * quad[(e + 1) % 4][1] - quad[(e + 1) % 4][0] * quad[e][1] for e in range(4))
                if a2 < 0:
                    quad.reverse()
                pieces.append(tuple(quad))
                sides += [(seg_pt(s, ta, h), seg_pt(s, tb, h)), (seg_pt(s, ta, -h), seg_pt(s, tb, -h))]
        treads.append({"s0": s0, "s1": s1, "z": (j + 1) * rise, "zb": 0.0, "pieces": pieces, "sides": sides, "risers": (cut(s0), cut(s1))})
    return {"segs": segs, "starts": starts, "length": total, "td": td, "rise": rise, "treads": treads,
            "corners": [{"seg": i, "m": s["m"], "sw": s["sw"]} for i, s in enumerate(segs) if s["kind"] == "corner"]}


def sketch_walk_z(lay, sv) -> float:
    """보행선 길이 sv 위치의 계단 높이(계단 아래 끝 기준 상대): 디딤판 하나를 지나는 동안 챌판 높이 하나씩 곧게 오른다(첫 디딤판 시작 0, 위 끝 (챌판 수 - 1) x 챌판 높이)."""
    tr = lay["treads"]
    for j in range(len(tr) - 1, -1, -1):
        if sv >= tr[j]["s0"] - 1e-9 or j == 0:
            d = tr[j]["s1"] - tr[j]["s0"]
            f = min(max((sv - tr[j]["s0"]) / d, 0.0), 1.0) if d > 0 else 0.0
            return (j + f) * lay["rise"]
    return 0.0


def sketch_walk_pt(lay, sv, m=0.0):
    """스케치 계단 보행선 길이 sv 의 점(왼쪽 m)과 진행 방향."""
    segs, starts = lay["segs"], lay["starts"]
    for i in range(len(segs) - 1, -1, -1):
        if sv >= starts[i] - 1e-9 or i == 0:
            t = min(max(sv - starts[i], 0.0), segs[i]["len"])
            return seg_pt(segs[i], t, m), seg_tan(segs[i], t)


def sketch_offset_path(lay, m, idx=None) -> list:
    """보행선을 왼쪽으로 m 들인 꺾은선(원호는 STAIR_SPIRAL_ARC 이하 현, 모서리 변은 바깥 둘레를 닮은 꼴로 줄인 세 점: 시작·꺾임·끝). 난간·평면 옆선이 쓴다.
    idx 를 주면 그 변 번호들만 따라간다(모서리 구간마다 따로 만드는 난간)."""
    out = []
    for si, s in enumerate(lay["segs"]):
        if idx is not None and si not in idx:
            continue
        if s["kind"] == "corner":
            ts = (0.0, s["len"] / 2.0, s["len"])
        else:
            k = 1 if s["kind"] == "line" else max(1, int(-(-abs(s["sw"]) // STAIR_SPIRAL_ARC)))
            ts = [s["len"] * q / k for q in range(k + 1)]
        for q, t in enumerate(ts):
            if out and q == 0:
                continue
            out.append(list(seg_pt(s, t, m)))
    return out


def _stair_params(p):
    if isinstance(p, dict) and "sketch" in p:   # 스케치 계단(W2 열일곱 번째 PR): 걸음선·꺾임·방사 없이 sketch 하나
        if "run" in p or "turn" in p or "spiral" in p:
            return "stair.sketch 가 있으면 stair.run·turn·spiral 을 둘 수 없다"
        err = _check_opt("stair", p, {"name": _name, "width": _pos, "height": _pos,
                                      "risers": lambda v: type(v) is int and STAIR_RISERS_MIN <= v <= STAIR_RISERS_MAX, "base_offset": num_ok},
                         {"sketch": _sketch_ok})
        return err or stair_sketch_problem(p)
    if isinstance(p, dict) and "spiral" in p:   # 방사 계단(W2 열일곱 번째 PR): 걸음선·꺾임 없이 spiral 하나
        if "run" in p or "turn" in p:
            return "stair.spiral 이 있으면 stair.run·stair.turn 을 둘 수 없다"
        err = _check_opt("stair", p, {"name": _name, "width": _pos, "height": _pos,
                                      "risers": lambda v: type(v) is int and STAIR_RISERS_MIN <= v <= STAIR_RISERS_MAX, "base_offset": num_ok},
                         {"spiral": _spiral_ok})
        return err or stair_spiral_problem(p)

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
    turn.winders 가 있으면 "landing" 은 None 이고 "winders" 키에 돌음 회전부가 온다(_winder_layout).
    방사 계단(spiral)은 단 묶음·층참이 없고 "spiral" 키에 stair_spiral_layout 이 온다."""
    if "spiral" in p:
        return {"flights": [], "landing": None, "rise": p["height"] / p["risers"], "spiral": stair_spiral_layout(p)}
    if "sketch" in p:
        return {"flights": [], "landing": None, "rise": p["height"] / p["risers"], "sketch": stair_sketch_layout(p)}
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
                     {"src": id_ok, "src_tag": lambda v: text_ok(v, 1, RAILING_TAG_MAX), "src_inset": lambda v: num_ok(v) and v >= 0,
                      "rltype": id_ok, "parts": lambda v: rail_parts_ok(v, p.get("height"))})
    if err is None and ("rltype" in p) and ("parts" not in p):
        return "railing.rltype 이 있으면 railing.parts(유형 구성 사본)도 있어야 한다"
    if err is None and "parts" in p and rail_member_count(p["path"], p["parts"]) > RAILING_MEMBERS_MAX:
        return f"railing.parts: 난간 부재(동자·발루스터)가 {RAILING_MEMBERS_MAX}개를 넘는다(간격을 키우세요)"
    if err is None and ("src" in p) != ("src_tag" in p):
        return "railing.src 와 railing.src_tag 는 함께 있어야 한다"
    if err is None and "src_inset" in p and "src" not in p:
        return "railing.src_inset 은 railing.src 가 있을 때만 둘 수 있다"
    return err


# ---- 난간 유형(W2 열세 번째 PR, ARC-31): 상부 난간대·중간 난간대·발루스터·동자(난간 기둥). 벽·지붕 유형과 같은 설계로 난간 행이 유형의
# 구성 사본(params.parts)과 높이를 갖고(params.rltype + 관계 of_type), 유형을 고치면 그 유형의 난간 행을 다시 쓴다. parts 가 없는 난간은 이전과 같다.
#   parts = {"top": {"w", "d"}             상부 난간대 단면(폭 w = 수평, 춤 d = 수직) mm. 윗면 = 난간 높이
#            "rails": [{"z", "w", "d"}, ...] 중간 난간대: 바닥 마감에서 난간대 중심까지 z(0 < z < 높이 - 상부 춤), 0~RAIL_MIDS_MAX 개
#            "baluster": {"spacing", "w", "bottom"} | 없음   발루스터 간격 상한·단면 한 변·바닥 마감에서 아래 끝까지. 동자 사이를 같은 간격(상한 이하)으로 나눠
#                                                         양 끝(동자 자리)은 비운다(시작·끝 처리 = 동자에 맞춘 가운데 정렬). 위 끝 = 상부 난간대 아랫면
#            "post": {"spacing", "w"}}      동자: 꼭짓점마다 + 그 사이를 간격 상한 이하로 등분(기존 난간 기둥 규칙과 같다), 단면 한 변
RAIL_MIDS_MAX = 8
RAILTYPES_MAX = 1000
BALUSTER_SPACING_MIN = 50.0    # 발루스터 간격 하한 mm(난간 하나에 수천 개가 생기지 않게)
POST_SPACING_MIN = 200.0       # 동자 간격 하한 mm
RAILING_MEMBERS_MAX = 20000    # 난간 하나의 부재(동자 + 발루스터) 상한


def _sec_ok(v, keys=("w", "d")) -> bool:
    return isinstance(v, dict) and set(v) == set(keys) and all(_pos(v[k]) and v[k] <= 1000.0 for k in keys if k in ("w", "d"))


def rail_parts_ok(v, height=None) -> bool:
    if not isinstance(v, dict) or not {"top", "rails", "post"} <= set(v) <= {"top", "rails", "post", "baluster"}:
        return False
    if not _sec_ok(v["top"]) or not isinstance(v["rails"], list) or len(v["rails"]) > RAIL_MIDS_MAX:
        return False
    for x in v["rails"]:
        if not (isinstance(x, dict) and set(x) == {"z", "w", "d"} and _pos(x["z"]) and _pos(x["w"]) and _pos(x["d"]) and x["w"] <= 1000.0 and x["d"] <= 1000.0):
            return False
        if height is not None and _pos(height) and not x["z"] + x["d"] / 2.0 <= height - v["top"]["d"] + 1e-9:
            return False
    po = v["post"]
    if not (isinstance(po, dict) and set(po) == {"spacing", "w"} and num_ok(po["spacing"]) and po["spacing"] >= POST_SPACING_MIN and _pos(po["w"]) and po["w"] <= 1000.0):
        return False
    if "baluster" in v:
        b = v["baluster"]
        if not (isinstance(b, dict) and set(b) == {"spacing", "w", "bottom"} and num_ok(b["spacing"]) and b["spacing"] >= BALUSTER_SPACING_MIN
                and _pos(b["w"]) and b["w"] <= 1000.0 and num_ok(b["bottom"]) and b["bottom"] >= 0):
            return False
        if height is not None and _pos(height) and not b["bottom"] < height - v["top"]["d"]:
            return False
    return height is None or (_pos(height) and v["top"]["d"] < height)


def rail_member_count(path, parts) -> int:
    """유형 난간의 동자 + 발루스터 수(형상을 만들지 않고 센다). 행 검사가 RAILING_MEMBERS_MAX 와 견준다."""
    n = 0
    ps, bal = parts["post"]["spacing"], parts.get("baluster")
    for i in range(len(path) - 1):
        ln = sqrt((path[i + 1][0] - path[i][0]) ** 2 + (path[i + 1][1] - path[i][1]) ** 2)
        k = int(max(1, -(-ln // ps)))
        n += k
        if bal is not None:
            n += k * max(0, int(-(-(ln / k) // bal["spacing"])) - 1)
    return n + 1


def _railtype_params(p):
    err = _check_opt("railtype", p, {"name": _name, "height": _pos, "parts": lambda v: rail_parts_ok(v, p.get("height"))}, {})
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
    out = {"surface": surface, "top": top, "posts": posts}
    if "parts" in p:   # 난간 유형(W2 열세 번째 PR): 부재별 형상. 없으면 결과는 이전과 같다
        out["members"] = rail_members(p, surface, seg)
        out["posts"] = [m[1] for m in out["members"] if m[0] in ("post", "baluster")]
        out["rails"] = [m[1] for m in out["members"] if m[0] == "rail"]
    return out


def rail_members(p, surface, seg) -> list:
    """유형 난간의 부재 목록 [(종류, 점들, 단면 폭), ...]. 종류: "top"(상부 난간대 중심선), "rail"(중간 난간대 중심선), "post"(동자), "baluster".
    좌표는 레벨 기준. 상부 난간대 중심선 높이 = 높이 - 춤/2, 동자는 바닥 마감 ~ 상부 난간대 아랫면, 발루스터는 bottom ~ 상부 난간대 아랫면."""
    parts, h = p["parts"], p["height"]
    td = parts["top"]["d"]
    under = h - td

    def lift(dz):
        return [(x, y, z + dz) for x, y, z in surface]
    out = [("top", lift(h - td / 2.0), parts["top"]["w"])]
    for r in parts["rails"]:
        out.append(("rail", lift(r["z"]), r["w"]))
    ps, pw = parts["post"]["spacing"], parts["post"]["w"]
    bal = parts.get("baluster")
    n_all = 0
    for i, (x, y, z) in enumerate(surface):
        out.append(("post", ((x, y, z), (x, y, z + under)), pw))
        if i >= len(seg):
            break
        nx, ny, nz = surface[i + 1]
        n = int(max(1, -(-seg[i] // ps)))
        n_all += n
        for k in range(n):   # 구간을 n 칸으로: 칸 사이 동자, 칸 안 발루스터
            f0, f1 = k / n, (k + 1) / n
            if k:
                px, py, pz = x + (nx - x) * f0, y + (ny - y) * f0, z + (nz - z) * f0
                out.append(("post", ((px, py, pz), (px, py, pz + under)), pw))
            if bal is None:
                continue
            bay = seg[i] / n
            m = int(-(-bay // bal["spacing"]))   # 칸을 m 등분(간격 상한 이하), 양 끝(동자)은 비우고 m-1 개
            for j in range(1, m):
                f = f0 + (f1 - f0) * j / m
                bx, by, bz = x + (nx - x) * f, y + (ny - y) * f, z + (nz - z) * f
                out.append(("baluster", ((bx, by, bz + bal["bottom"]), (bx, by, bz + under)), bal["w"]))
    return out


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
                      "hole_clearance": lambda v: num_ok(v) and 0 <= v <= ELEVATOR_HOLE_CLEAR_MAX,
                      "rotation": lambda v: num_ok(v) and 0 < v < 360.0})
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


# ---- 승강기 회전(W2 열일곱 번째 PR, ARC-33): 선택 키 rotation(도, 0 < 값 < 360, 반시계). 샤프트 [x1,y1,x2,y2] 는 회전 전(로컬) 사각형이고
# 승강기 전체(샤프트·카·피트·기계실·승강장 문)가 샤프트 가운데를 중심으로 이 각만큼 돈다. 문 면(N·E·S·W)은 로컬 방위다. 키가 없으면 이전과 같다(회전 0).
def elevator_rot(p):
    """승강기 회전 (중심 x, 중심 y, sin, cos) 또는 None(회전 없음)."""
    a = p.get("rotation")
    if not a:
        return None
    x1, y1, x2, y2 = p["shaft"]
    sn, cs = sincos_deg(a)
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0, sn, cs)


def rot_pt(rot, x, y):
    """로컬 점 (x, y) 를 승강기 회전(elevator_rot)으로 돌린 세계 점. rot 이 None 이면 그대로."""
    if rot is None:
        return (x, y)
    cx, cy, sn, cs = rot
    dx, dy = x - cx, y - cy
    return (cx + cs * dx - sn * dy, cy + sn * dx + cs * dy)


def rot_vec(rot, x, y):
    """방향 벡터 (x, y) 를 승강기 회전으로 돌린다."""
    if rot is None:
        return (x, y)
    _cx, _cy, sn, cs = rot
    return (cs * x - sn * y, sn * x + cs * y)


def elevator_ring(p, rect):
    """승강기 로컬 사각형 rect (x1,y1,x2,y2) 의 세계 꼭짓점 4개(반시계, 회전 반영)."""
    rot = elevator_rot(p)
    x1, y1, x2, y2 = rect
    return [rot_pt(rot, x, y) for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))]


# ---------------------------------------------------------------- 경사 지붕 (kinds "roof", 설계: elements-design.md 16절)
ROOF_FORMS = ("gable", "hip", "shed", "poly", "footprint", "extrusion", "flat")   # 박공(맞배)·모임(우진각)·외쪽·다각형 외곽(변마다 물매 또는 박공, W1 1-F 4차)·외곽선 지붕·압출 지붕(W2 여덟 번째 PR)·평지붕(W2 열여섯 번째 PR)
ROOF_POLY_FORMS = ("poly", "footprint")   # 변마다 물매(slopes)를 갖는 형식
ROOF_OUTLINE_FORMS = ("footprint", "flat")   # 임의 다각형 외곽(변 1mm 이상, 128 꼭짓점)과 변마다 처마 내밀기(overhangs)를 받는 형식
# 평지붕(form "flat", W2 열여섯 번째 PR, 사용자 결정 "평지붕은 물매 0으로"): 물매 0(pitch 0 으로 저장) 수평 윗면 z = base_offset, 두께는 아래로.
#   외곽 다각형(오목 가능) + 변마다 선택 볼록도(bulges, 원호) + 처마 내밀기(overhang 또는 overhangs). 배수 물매는 만들지 않는다. 형상은 roofflat
ROOF_PITCH_MAX = 75.0       # 물매 각도 상한(도)
ROOF_OVERHANG_MAX = 3000.0  # 처마 내밀기 상한 mm
ROOF_SIDE_MIN = 500.0       # 외곽 직사각형의 최소 변 길이 mm
# 외곽선 지붕(footprint, W2 여덟 번째 PR, ARC-19): 가중 직선 골격(roofskel). 변마다 물매·처마 내밀기, 짧은 변(1mm 이상)·오목 외곽·물매가 다른 변 모두
# 처마 부속(ARC-20): 지붕 변을 따라가는 fascia(처마 널)·gutter(홈통)·soffit(처마 반자). 지붕 params.eaves 에 종류마다 {edges: 외곽 변 번호들, 치수}
EAVE_KINDS = ("fascia", "gutter", "soffit")
EAVE_DIMS = {"fascia": ("width", "depth"), "gutter": ("width", "depth"), "soffit": ("thickness",)}
EAVE_DEFAULT = {"fascia": {"width": 30.0, "depth": 250.0}, "gutter": {"width": 150.0, "depth": 120.0}, "soffit": {"thickness": 15.0}}
EAVE_DIM_MAX = 2000.0       # 처마 부속 치수 상한 mm
ROOFTYPES_MAX = 1000        # 지붕 유형 수 상한


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


def _slope_ok(v) -> bool:
    return num_ok(v) and 0 <= v <= ROOF_PITCH_MAX


def roof_tans(slopes) -> list:
    """변마다 물매(도)의 tan(0 = 박공 변). sincos_deg 로 계산한다(도면·IFC 가 같은 값)."""
    out = []
    for s in slopes:
        if s <= 0:
            out.append(0.0)
        else:
            sn, cs = sincos_deg(float(s))
            out.append(sn / cs)
    return out


def eaves_ok(v, n: int) -> bool:
    """처마 부속 {종류: {"edges": [외곽 변 번호, ...](오름차순·겹침 없음), 치수...}}."""
    if not isinstance(v, dict) or not v or not set(v) <= set(EAVE_KINDS):
        return False
    for k, d in v.items():
        dims = EAVE_DIMS[k]
        if not isinstance(d, dict) or set(d) - {"material"} != {"edges", *dims}:   # material: 종류별 재료 이름(W2 아홉 번째 PR, 선택)
            return False
        if "material" in d and not _name(d["material"]):
            return False
        es = d["edges"]
        if not (isinstance(es, list) and es and all(type(x) is int and 0 <= x < n for x in es) and es == sorted(set(es))):
            return False
        if not all(_pos(d[x]) and d[x] <= EAVE_DIM_MAX for x in dims):
            return False
    return True


def roof_overhangs(p) -> list:
    """외곽선 지붕의 변마다 처마 내밀기(없으면 모두 overhang)."""
    return [float(x) for x in p["overhangs"]] if "overhangs" in p else [float(p["overhang"])] * len(p["boundary"])


def _roof_params(p):
    flat = isinstance(p, dict) and p.get("form") == "flat"   # 평지붕(W2 열여섯 번째 PR): 물매 0 으로 저장한다
    pts_max = RS.PTS_MAX if isinstance(p, dict) and p.get("form") in ROOF_OUTLINE_FORMS else RP.POLY_PTS_MAX
    err = _check_opt("roof", p, {"name": _name, "boundary": lambda v: isinstance(v, list) and 3 <= len(v) <= pts_max and all(_pt_ok(q) for q in v),
                                 "form": lambda v: v in ROOF_FORMS,
                                 "pitch": lambda v: (num_ok(v) and v == 0) if flat else (_pos(v) and v <= ROOF_PITCH_MAX), "overhang": lambda v: num_ok(v) and 0 <= v <= ROOF_OVERHANG_MAX,
                                 "base_offset": num_ok, "thickness": _pos},
                     {"ridge": lambda v: v == "short", "dir": num_ok,
                      "slopes": lambda v: isinstance(v, list) and len(v) == len(p.get("boundary", ())) and all(_slope_ok(x) for x in v),
                      # 외곽선 지붕·지붕 유형·층 구성·처마 부속(W2 여덟 번째 PR): 없으면 행은 이전과 같다
                      "overhangs": lambda v: isinstance(v, list) and len(v) == len(p.get("boundary", ())) and all(num_ok(x) and 0 <= x <= ROOF_OVERHANG_MAX for x in v),
                      "rtype": id_ok, "layers": layers_ok, "eaves": lambda v: eaves_ok(v, len(p.get("boundary", ()))),
                      "cw_input": lambda v: v is True,   # 시계 방향으로 받은 외곽(W2 아홉 번째 PR): 형상·IFC 형상과 무관, 처마 부속 변 번호 대응에만
                      "axis": lambda v: isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v),
                      "profile": lambda v: isinstance(v, list) and all(_pt_ok(q) for q in v),
                      # 평지붕의 변마다 볼록도(W2 열여섯 번째 PR, 원호 외곽): 없으면 행은 이전과 같다
                      "bulges": lambda v: isinstance(v, list) and len(v) == len(p.get("boundary", ())) and all(num_ok(x) and abs(x) <= 1.0 for x in v) and any(v)})
    if err is not None:
        return err
    if "bulges" in p and p["form"] != "flat":
        return "roof.bulges(변마다 볼록도)는 평지붕(flat)에만 둘 수 있다"
    if p["form"] == "extrusion" or "axis" in p or "profile" in p:   # 압출 지붕(W2 여덟 번째 PR): 입면 프로파일을 axis 방향으로 돌출, 외곽은 그 직사각형
        if p["form"] != "extrusion":
            return "roof.axis·roof.profile 은 압출 지붕(extrusion)에만 둘 수 있다"
        if "axis" not in p or "profile" not in p:
            return "roof: 압출 지붕(extrusion)은 axis 와 profile 이 필요하다"
        if any(k in p for k in ("slopes", "overhangs", "ridge", "dir")) or p["overhang"] != 0:
            return "roof: 압출 지붕(extrusion)은 slopes·overhangs·ridge·dir·처마 내밀기를 쓰지 않는다(프로파일이 정한다)"
        if why := RX.problem(p["axis"], p["profile"]):
            return f"roof.profile: {why}"
        want = sorted((round(q[0], 2), round(q[1], 2)) for q in RX.rect(p["axis"], p["profile"]))
        if len(p["boundary"]) != 4 or sorted((round(q[0], 2), round(q[1], 2)) for q in p["boundary"]) != want:
            return "roof.boundary 는 압출 지붕의 평면 직사각형(axis x 프로파일 폭)이어야 한다"
        return None
    if "layers" in p and abs(layers_total(p["layers"]) - p["thickness"]) > LAYER_SUM_TOL:
        return "roof.layers 두께의 합이 지붕 두께와 다르다"
    if "overhangs" in p and p["form"] not in ROOF_OUTLINE_FORMS:
        return "roof.overhangs(변마다 처마 내밀기)는 외곽선 지붕(footprint)·평지붕(flat)에만 둘 수 있다"
    if p["form"] == "flat":   # 평지붕(W2 열여섯 번째 PR): 물매 0, 외곽 다각형(+ 볼록도) + 처마 내밀기
        if any(k in p for k in ("slopes", "ridge", "dir")):
            return "roof: 평지붕(flat)은 slopes·ridge·dir 를 쓰지 않는다(물매 0)"
        if why := RFL.problem(p["boundary"], p.get("bulges"), roof_overhangs(p)):
            return f"roof.boundary: {why}"
        return None
    if p["form"] == "footprint":
        if "slopes" not in p:
            return "roof: 외곽선 지붕(footprint)은 변마다 물매 slopes 가 필요하다"
        if "ridge" in p or "dir" in p:
            return "roof.ridge·roof.dir 은 외곽선 지붕(footprint)에 둘 수 없다"
        if why := RS.problem(p["boundary"], roof_tans(p["slopes"]), roof_overhangs(p)):
            return f"roof.boundary: {why}"
        return None
    if p["form"] == "poly":
        if "slopes" not in p:
            return "roof: 다각형 외곽 지붕(poly)은 변마다 물매 slopes 가 필요하다"
        if "ridge" in p or "dir" in p:
            return "roof.ridge·roof.dir 은 다각형 외곽 지붕(poly)에 둘 수 없다"
        if why := RP.problem(p["boundary"], p["slopes"], roof_tans(p["slopes"]), p["overhang"], ROOF_SIDE_MIN):
            return f"roof.boundary: {why}"
        return None
    if "slopes" in p:
        return "roof.slopes 는 다각형 외곽 지붕(poly)에만 둘 수 있다"
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
       "zlow": 둘레의 가장 낮은 z, "ztop": 가장 높은 z, "valleys": 골(직사각형은 없음), "pieces": [(볼록 다각형, 평면)] 면의 볼록 조각, "fpitch": 면마다 물매 도}
    form "poly"(다각형 외곽, 변마다 물매 slopes, 0 = 박공 변)는 roofpoly.geom 이 같은 꼴로 만든다."""
    if p["form"] == "poly":   # 다각형 외곽: 변마다 경사면(roofpoly). faces 는 오목할 수 있고 볼록 조각은 pieces, 골은 valleys
        return RP.geom(p["boundary"], p["slopes"], roof_tans(p["slopes"]), p["overhang"], p["base_offset"])
    if p["form"] == "extrusion":   # 압출 지붕(W2 여덟 번째 PR): 프로파일 선분마다 평면 사각형
        return RX.geom(p["axis"], p["profile"], p["base_offset"], sincos_deg)
    if p["form"] == "flat":   # 평지붕(W2 열여섯 번째 PR): 수평 면 하나(처마를 내민 윤곽), 볼록 조각 pieces, eave_loop·eave_src·smooth
        return RFL.geom(p["boundary"], p.get("bulges"), roof_overhangs(p), p["base_offset"])
    if p["form"] == "footprint":   # 외곽선 지붕(W2 여덟 번째 PR): 가중 직선 골격(roofskel). eave_src = 처마 윤곽 변마다 원래 외곽 변 번호
        return RS.geom(p["boundary"], p["slopes"], roof_tans(p["slopes"]), roof_overhangs(p), p["base_offset"])
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
    return {"faces": faces, "ridges": ridges, "hips": hips, "valleys": [], "perimeter": perim, "arrows": arrows, "outline": outline, "planes": planes,
            "pieces": list(zip(faces, planes)), "fpitch": [float(p["pitch"])] * len(faces),
            "zlow": min(q[2] for q in perim), "ztop": max(q[2] for fc in faces for q in fc)}


def roof_eave_pieces(p) -> list:
    """지붕 params 의 처마 부속 조각들(roofeaves.pieces, 레벨 기준 z). 처마 부속이 없으면 빈 목록."""
    if "eaves" not in p:
        return []
    oh = roof_overhangs(p) if p["form"] in ROOF_OUTLINE_FORMS else [float(p["overhang"])] * len(p["boundary"])
    gm = roof_geom(p)
    if p["form"] == "flat" and "bulges" in p:   # 원호 외곽 평지붕(W2 열여섯 번째 PR): 펼친 외곽의 현을 변으로 본다(호 변 i 에 단 부속 = 그 호의 모든 현)
        q, oh = flat_eave_params(p, gm, oh)
        return RE.pieces(q, gm, oh)
    return RE.pieces(p, gm, oh)


def flat_eave_params(p, gm, oh):
    """원호 외곽 평지붕의 처마 부속 계산용 (params, 현마다 내밀기): 외곽 = 펼친 고리, 처마 부속 edges = 단 변의 모든 현 번호."""
    src = gm["eave_src"]
    ev = {k: dict(d, edges=[j for j, s in enumerate(src) if s in set(d["edges"])]) for k, d in p["eaves"].items()}
    return dict(p, boundary=[list(q) for q in gm["eave_loop"]], eaves=ev), [oh[s] for s in src]


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


# 시트 용지 이름(sheet.PAPERS 와 같은 집합: commands 가 import 때 확인한다). P 로 끝나면 세로
PAPER_NAMES = ("A0", "A1", "A2", "A3", "A4", "A0P", "A1P", "A2P", "A3P", "A4P")


def _sheet_params(p):
    """시트 정의: 뷰 배치(앞에서부터 왼쪽 위 -> 오른쪽 -> 아래)와 표제란 인자. 값의 범위·참조는 정의를 되살릴 때(viewdefs) 마저 검사한다.
    viewports(W1 1-C 2차, DOC-02·VIEW-25) 는 선택 키다(없으면 키 자체가 없다: 이전 시트 정의의 저장 형식 그대로)."""
    return _check_opt("sheet", p, {
        "name": _name, "views": lambda v: isinstance(v, list) and 1 <= len(v) <= 12 and all(id_ok(x) for x in v),
        "paper": lambda v: v in PAPER_NAMES, "project_name": lambda v: text_ok(v), "sheet_name": lambda v: text_ok(v), "sheet_number": lambda v: text_ok(v),
        "date": lambda v: v is None or text_ok(v, 10, 10), "north_angle": num_ok, "client": lambda v: text_ok(v), "office": lambda v: text_ok(v),
        "signers": lambda v: _json_ok(v) and isinstance(v, list), "revisions": lambda v: _json_ok(v) and isinstance(v, list),
        "grids": lambda v: _json_ok(v) and isinstance(v, list), "callouts": lambda v: _json_ok(v) and isinstance(v, list), "marks": lambda v: type(v) is bool,
        "tags": lambda v: isinstance(v, list) and all(text_ok(x, 1, 16) for x in v), "key_plan": lambda v: v is None or id_ok(v),
        "key_zone": lambda v: v is None or (isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v)),
        "line_profile": lambda v: v is None or (_json_ok(v) and isinstance(v, dict))},
        {"viewports": lambda v: _json_ok(v) and isinstance(v, list) and 0 < len(v) <= 12})


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


SITE_OPTIONAL = ("terrain", "zones", "parking", "signs", "survey")   # 대지의 선택 파라미터(없으면 키 자체가 없다)


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
                      "parking": _parking_ok, "signs": lambda v: isinstance(v, list) and 1 <= len(v) <= SITE_SIGNS_MAX and all(_sign_ok(z) for z in v),
                      # 측량 기준점(W1 1-B 3차, ANN-04): 프로젝트 원점의 측량 좌표 [E, N] mm. 좌표 기호가 정북 각으로 돌려 이 값을 더한다
                      "survey": lambda v: isinstance(v, list) and len(v) == 2 and all(num_ok(x) and abs(x) <= 1e12 for x in v)})
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
    err = _check_opt("grid", p, {"name": lambda v: text_ok(v, 1, 16), "line": lambda v: isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v),
                                 "bubbles": lambda v: v in GRID_BUBBLES}, {"arc": _pt_ok})
    if err:
        return err
    x1, y1, x2, y2 = p["line"]
    if (x2 - x1) ** 2 + (y2 - y1) ** 2 < 1.0:
        return "grid.line 길이는 1mm 이상이어야 한다"
    if "arc" in p:   # 원호 통심선(W2 열 번째 PR, ARC-38): line = 호의 시작·끝, arc = 호의 가운데 점(곡선 벽과 같은 규칙)
        why = AG.problem(p["line"], p["arc"], 0.0)
        if why:
            return f"grid.arc: {why}"
    return None


def _solid_params(p):
    """일반 솔리드(18단계): 형상 검사는 solids.solid_problem 이 정본이다."""
    return SO.solid_problem(p)


def _foreign_params(p):
    return _check_fields("foreign", p, {"name": lambda v: text_ok(v, 0, 255)})


# ---------------------------------------------------------------- 2D 작도(27단계 2D 편집, 설계: docs/2d-editing.md)
# draft  = 뷰 위의 모델과 무관한 2D 요소(선·폴리선·호·원·문자·치수·채움 영역). 좌표는 그 뷰의 2D 좌표(평면 x,y / 단면·입면 s,z, view_lines 와 같은 공간) mm.
# dlayer = 2D 요소 층의 스타일(선굵기·선종류·색). 요소의 weight·linetype·color 가 null 이면 층 스타일을 따른다(bylayer).
DRAFT_SHAPES = ("line", "polyline", "arc", "circle", "text", "region", "dim",
                "dim_angle", "dim_radius", "dim_diameter", "dim_arc", "dim_chain", "dim_base",   # W1 1-B 치수 종류
                "spot_elev", "spot_coord", "spot_slope", "mask", "cloud")                          # 표고·좌표·경사, 마스킹 영역, 리비전 구름
DRAFT_PTS_NEED = {"line": (2, 2), "polyline": (2, 256), "region": (3, 256), "arc": (1, 1), "circle": (1, 1), "text": (1, 1), "dim": (2, 2),
                  "dim_angle": (3, 3), "dim_radius": (1, 1), "dim_diameter": (1, 1), "dim_arc": (1, 1), "dim_chain": (2, 256), "dim_base": (2, 256),
                  "spot_elev": (2, 2), "spot_coord": (2, 2), "spot_slope": (2, 2), "mask": (3, 256), "cloud": (3, 256)}
_DRAFT_R = frozenset({"arc", "circle", "dim_angle", "dim_radius", "dim_diameter", "dim_arc"})   # 반지름 r > 0 이 필요한 모양(cloud 는 0 = 기본 호 크기)
_DRAFT_A0 = frozenset({"text", "dim_radius", "dim_diameter", "dim_chain", "dim_base"})          # a0 하나만 쓰는 모양(회전각·방향)
_DRAFT_TEXT = frozenset({"text", "dim", "dim_angle", "dim_radius", "dim_diameter", "dim_arc", "spot_elev", "spot_coord", "spot_slope", "cloud"})
_DRAFT_OFF = frozenset({"dim", "dim_arc", "dim_chain", "dim_base"})
_DRAFT_POLY = frozenset({"region", "mask", "cloud"})
DRAFT_WEIGHTS = ("thin", "fine", "medium", "thick")
DRAFT_LINETYPES = ("continuous", "dashed", "hidden", "center", "dashdot", "dotted")
DRAFT_FILLS = ("none", "solid", "hatch", "cross")
DRAFT_ALIGNS = ("start", "middle", "end")
DRAFT_PTS_MAX = 256                # 폴리선·영역 꼭짓점 상한(슬래브 경계와 같다)
DRAFT_COORD_MAX = 1e7              # 뷰 좌표 상한(commands.VCoord 와 같다)
DRAFT_TEXT_MAX = 255
DRAFT_LAYER_BAD = set('<>/\\":;?*|=`')   # DXF 층 이름에 쓸 수 없는 글자


def layer_name_ok(v) -> bool:
    """2D 작도 층 이름: 1~64자, 제어 문자·DXF 금지 문자·앞뒤 공백 없음."""
    return text_ok(v, 1, NAME_MAX) and v == v.strip() and not any(c in DRAFT_LAYER_BAD or ord(c) < 32 for c in v)


def color_ok(v) -> bool:
    return isinstance(v, str) and len(v) == 7 and v[0] == "#" and all(c in "0123456789abcdef" for c in v[1:])


def _dpt(v) -> bool:
    return _pt_ok(v) and all(abs(x) <= DRAFT_COORD_MAX for x in v)


def draft_shape_problem(p) -> str | None:
    """2D 요소의 모양별 검사(안 되면 이유). 커맨드와 가져오기·병합이 같은 규칙을 쓴다."""
    s, pts = p["shape"], p["pts"]
    need = DRAFT_PTS_NEED[s]
    if not need[0] <= len(pts) <= need[1]:
        return f"{s} 의 점은 {need[0]}~{need[1]}개여야 한다(현재 {len(pts)}개)"
    if p["closed"] and s not in ("polyline", "mask"):
        return "closed 는 폴리선(닫기)·마스킹 영역(경계선 보이기)에만 쓴다"

    def same(a, b):
        return (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 < 1e-6

    if s in ("line", "dim", "spot_slope") and same(pts[0], pts[1]):
        return f"{s} 의 두 점이 같다"
    if s == "dim_angle" and (same(pts[0], pts[1]) or same(pts[0], pts[2])):
        return "각도 치수의 재는 점이 꼭짓점과 같다"
    if s == "polyline" and any(pts[i] == pts[i + 1] for i in range(len(pts) - 1)):
        return "폴리선에 이어진 같은 점이 있다"
    if s in _DRAFT_POLY and (err := polygon_problem(pts)) is not None:
        return f"{s} 경계: {err}"
    if s in _DRAFT_R and not p["r"] > 0:
        return f"{s} 은 반지름 r 을 0 보다 크게 둔다"
    if s not in _DRAFT_R and s != "cloud" and p["r"] != 0:
        return "반지름 r 은 호·원·각도·반지름·지름·호 길이 치수·구름(호 크기)에만 쓴다"
    if abs(p["a0"]) > 3600 or abs(p["a1"]) > 3600:
        return "각이 범위(±3600도)를 벗어났다"
    if s in ("arc", "dim_arc"):
        if abs((p["a1"] - p["a0"]) % 360.0) < 1e-9:
            return "호의 시작각·끝각이 같다"
    elif s in _DRAFT_A0:
        if p["a1"] != 0:
            return f"{s} 은 각 a0 하나만 쓴다"
    elif p["a0"] != 0 or p["a1"] != 0:
        return "a0·a1 은 호·호 길이 치수(시작각·끝각), 문자(회전각), 반지름·지름·연속·기준선 치수(방향 a0)에만 쓴다"
    if s == "dim_angle" and not 1e-6 < (_ang(pts[0], pts[2]) - _ang(pts[0], pts[1])) % 360.0 < 360.0 - 1e-6:
        return "각도 치수의 두 방향이 같다"
    if s == "dim_arc" and p["r"] + p["off"] <= 0:
        return "호 길이 치수선의 반지름(r + off)이 0 이하다"
    if s in ("dim_chain", "dim_base"):
        u = (_cos_deg(p["a0"]), _sin_deg(p["a0"]))
        ss = [x * u[0] + y * u[1] for x, y in pts]
        if max(ss) - min(ss) < 1e-6:
            return "연속·기준선 치수의 점이 측정 방향(a0)으로 모두 같은 위치다"
    if s == "text" and not p["text"].strip():
        return "문자 요소의 text 는 비울 수 없다"
    if s not in _DRAFT_TEXT and p["text"]:
        return "text 는 문자·치수(값 대신 쓸 글)·표고·좌표·경사·구름(리비전 번호)에만 쓴다"
    if s != "region" and p["fill"] != "none":
        return "fill 은 영역에만 쓴다"
    if s not in _DRAFT_OFF and p["off"] != 0:
        return "off 는 치수(선형·호 길이·연속·기준선)에만 쓴다"
    if s != "text" and p["align"] != "start":
        return "align 은 문자에만 쓴다"
    return None


def _ang(a, b):
    from math import atan2, degrees
    return degrees(atan2(b[1] - a[1], b[0] - a[0]))


def _cos_deg(v):
    from math import cos, radians
    return cos(radians(v))


def _sin_deg(v):
    from math import radians, sin
    return sin(radians(v))


def _draft_params(p):
    err = _check_fields("draft", p, {
        "name": _name, "view": id_ok, "shape": lambda v: v in DRAFT_SHAPES,
        "pts": lambda v: isinstance(v, list) and all(_dpt(x) for x in v),
        "closed": lambda v: type(v) is bool, "r": lambda v: num_ok(v) and 0 <= v <= DRAFT_COORD_MAX,
        "a0": num_ok, "a1": num_ok, "text": lambda v: text_ok(v, 0, DRAFT_TEXT_MAX),
        "size": lambda v: num_ok(v) and 0.5 <= v <= 100, "off": lambda v: num_ok(v) and abs(v) <= 1e5,
        "fill": lambda v: v in DRAFT_FILLS, "align": lambda v: v in DRAFT_ALIGNS,
        "layer": layer_name_ok, "weight": lambda v: v is None or v in DRAFT_WEIGHTS,
        "linetype": lambda v: v is None or v in DRAFT_LINETYPES, "color": lambda v: v is None or color_ok(v),
        "group": lambda v: v is None or _name(v)})
    return err or draft_shape_problem(p)


def _dlayer_params(p):
    return _check_fields("dlayer", p, {"name": layer_name_ok, "weight": lambda v: v in DRAFT_WEIGHTS,
                                       "linetype": lambda v: v in DRAFT_LINETYPES, "color": color_ok})


# ---------------------------------------------------------------- 리비전·키노트(W1 1-B 3차, ANN-17·DOC-15·ANN-09)
# revision = 프로젝트 리비전(Revit 의 Sheet Issues/Revisions): 순번·표기·일자·내용·발행 상태와 그 리비전을 싣는 시트.
#            시트 개정란은 sheets 에 든 시트와, 올린 뷰에 표기(name)가 같은 리비전 구름(draft cloud 의 text)이 있는 시트에 리비전을 적는다.
# keynote  = 키노트 표의 한 줄(키 -> 설명). 요소의 키노트는 속성셋 DoBIM_Keynote.Key 에 두고 태그 필드 keynote 로 단다.
REVISION_SEQ_MAX = 999
REVISIONS_MAX = 200          # 프로젝트 리비전 수 상한
REVISION_SHEETS_MAX = 200    # 리비전 하나가 직접 싣는 시트 수 상한
KEYNOTE_KEY_MAX = 16
KEYNOTE_TEXT_MAX = 255
KEYNOTES_MAX = 2000          # 키노트 표 줄 수 상한
_DATE = lambda v: isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-" and (v[:4] + v[5:7] + v[8:]).isdigit()   # noqa: E731


def revision_mark_ok(v) -> bool:
    return text_ok(v, 1, 8) and v.strip() == v


def keynote_key_ok(v) -> bool:
    return text_ok(v, 1, KEYNOTE_KEY_MAX) and v.strip() == v and not any(c in v for c in " \t\n\r")


def _revision_params(p):
    return _check_fields("revision", p, {
        "name": revision_mark_ok, "seq": lambda v: type(v) is int and 1 <= v <= REVISION_SEQ_MAX, "date": _DATE,
        "desc": lambda v: text_ok(v, 0, 255), "issued_to": lambda v: text_ok(v, 0, NAME_MAX), "issued_by": lambda v: text_ok(v, 0, NAME_MAX),
        "issued": lambda v: type(v) is bool,
        "sheets": lambda v: isinstance(v, list) and len(v) <= REVISION_SHEETS_MAX and all(id_ok(x) for x in v) and len(set(v)) == len(v)})


def _keynote_params(p):
    return _check_fields("keynote", p, {"name": keynote_key_ok, "text": lambda v: text_ok(v, 0, KEYNOTE_TEXT_MAX)})


# ---------------------------------------------------------------- 프로젝트 매개변수(W1 연장, DATA-03)
# pparam = Revit 의 프로젝트 매개변수: 속성셋.속성 하나를 값 형식과 묶을 요소 종류(범주)로 정의한다. 값은 요소의 속성셋에 그대로 두고(pset.set·pset.set_many),
#          정의가 있으면 그 속성을 쓸 때 형식·종류를 검사한다. 정의는 비 IFC 의도 요소라 DoBIM_Defs 로 IFC 를 왕복한다.
PPARAM_TYPES = ("text", "number", "integer", "bool")
PPARAMS_MAX = 500            # 프로젝트 매개변수 정의 수 상한
PPARAM_DESC_MAX = 255
_PSET_NAME_OK = lambda v: (isinstance(v, str) and 1 <= len(v) <= 64 and v[0].isascii() and v[0].isalpha()   # noqa: E731
                           and all(c.isascii() and (c.isalnum() or c == "_") for c in v))


def pparam_kinds() -> tuple:
    """프로젝트 매개변수를 묶을 수 있는 종류: 속성셋을 가지는 IFC 종류(외래 요소 제외)."""
    return tuple(sorted(k for k, v in ELEMENT_KINDS.items() if v.ifc and k != "foreign"))


def pparam_value_ok(t: str, v) -> bool:
    """값 v 가 형식 t 에 맞는가. number 는 정수·실수(참·거짓 제외), integer 는 정수만."""
    if t == "bool":
        return type(v) is bool
    if t == "integer":
        return type(v) is int
    if t == "number":
        return type(v) in (int, float)
    return isinstance(v, str)


def _pparam_params(p):
    return _check_fields("pparam", p, {
        "name": lambda v: _PSET_NAME_OK(v) and v != "id", "pset": _PSET_NAME_OK, "type": lambda v: v in PPARAM_TYPES,
        "kinds": lambda v: isinstance(v, list) and 1 <= len(v) and v == sorted(set(v)) and all(k in pparam_kinds() for k in v),
        "desc": lambda v: text_ok(v, 0, PPARAM_DESC_MAX)})


# ---------------------------------------------------------------- 재료 외관 정의(W2 다섯 번째 PR, DATA-05·VIS-07)
# matdef = Revit 의 재료 외관(Appearance/Shading): 재료 이름 하나의 표면 색·투명도·광택. 요소에는 재료 이름만 두고(material.set),
#          IFC 투영은 그 이름의 IfcMaterial 에 표면 스타일(IfcSurfaceStyleRendering)을 붙인다. 비 IFC 의도 요소라 DoBIM_Defs 로 IFC 를 왕복한다
MATDEFS_MAX = 1000           # 재료 외관 정의 수 상한


def _unit_ok(v) -> bool:
    return type(v) is float and isfinite(v) and 0.0 <= v <= 1.0


def _matdef_params(p):
    return _check_fields("matdef", p, {
        "name": _name, "color": lambda v: isinstance(v, list) and len(v) == 3 and all(type(c) is int and 0 <= c <= 255 for c in v),
        "transparency": _unit_ok, "shininess": _unit_ok})


# ---------------------------------------------------------------- 벽 유형·스택 벽(W2 일곱 번째 PR, ARC-11·ARC-09)
# walltype = Revit 의 기본 벽 유형(시스템 패밀리 유형): 이름 붙은 두께 또는 층 구성 + 코어 범위·기능·기본 높이. 벽 행이 유형의 층 구성을 복사해 갖고
#            유형을 고치면 그 유형의 벽 행을 모두 다시 쓴다. 비 IFC 의도 요소라 DoBIM_Defs 로 왕복하고, IFC 투영은 IfcWallType(엔진 flush 가 맞춘다)
# stackwall = Revit 의 스택 벽: 높이 구간마다 벽 유형이 다른 벽들(부분 벽 = 보통 벽 행, params.stack)을 한 요소로 묶는다.
#             IFC 는 형상 없는 IfcWall 하나가 부분 IfcWall 들을 IfcRelAggregates 로 모은다
def _walltype_params(p):
    err = _check_opt("walltype", p, {"name": _name, "thickness": _pos},
                     {"layers": layers_ok, "core": lambda v: isinstance(v, list), "function": lambda v: v in WTY.FUNCTIONS, "height": _pos})
    if err is None and "layers" in p and abs(layers_total(p["layers"]) - p["thickness"]) > LAYER_SUM_TOL:
        return "walltype.layers 두께의 합이 유형 두께와 다르다"
    if err is None and "core" in p and not ("layers" in p and WTY.core_ok(p["core"], len(p["layers"]))):
        return "walltype.core 는 층 구성의 층 번호 범위 [a, b] 여야 한다"
    return err


# 지붕 유형(W2 여덟 번째 PR, ARC-11·ARC-19 지붕 층 구성): 벽 유형과 같은 설계. 이름 붙은 두께 또는 층 구성(윗면부터) + 기능(지붕 피복) + 재료.
# 지붕 행이 유형의 두께·층 구성을 복사해 갖고(params.rtype + 관계 of_type) 유형을 고치면 그 유형의 지붕 행을 모두 다시 쓴다. IFC 투영은 IfcRoofType
ROOF_FUNCTIONS = ("roof", "canopy", "eave", "glazing")   # 일반 지붕·캐노피·처마·유리 지붕


def _rooftype_params(p):
    err = _check_opt("rooftype", p, {"name": _name, "thickness": _pos}, {"layers": layers_ok, "function": lambda v: v in ROOF_FUNCTIONS})
    if err is None and "layers" in p and abs(layers_total(p["layers"]) - p["thickness"]) > LAYER_SUM_TOL:
        return "rooftype.layers 두께의 합이 유형 두께와 다르다"
    return err


def _stack_seg_ok(v) -> bool:
    return (isinstance(v, dict) and set(v) == {"type", "height", "offset"} and id_ok(v["type"]) and num_ok(v["offset"])
            and (v["height"] is None or _pos(v["height"])))


def _stackwall_params(p):
    def axis(v):
        return (isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v)
                and (v[2] - v[0]) ** 2 + (v[3] - v[1]) ** 2 >= 1.0)
    err = _check_opt("stackwall", p, {"name": _name, "axis": axis, "height": _pos, "line": lambda v: v in WTY.LOC_LINES,
                                      "segments": lambda v: isinstance(v, list) and 2 <= len(v) <= WTY.STACK_SEGS_MAX and all(_stack_seg_ok(x) for x in v),
                                      "parts": lambda v: isinstance(v, list) and all(id_ok(x) for x in v) and len(set(v)) == len(v)},
                     {"arc": _pt_ok, "base_offset": lambda v: num_ok(v) and v != 0 and abs(v) <= WT.BASE_OFFSET_MAX})
    if err is None and len(p["parts"]) != len(p["segments"]):
        return "stackwall.parts 수가 구간 수와 다르다"
    if err is None:
        try:
            WTY.stack_heights(p["segments"], p["height"])
        except ValueError as ex:
            return f"stackwall.segments: {ex}"
    return err


# ---------------------------------------------------------------- 법정 면적 객체(W1 연장, ARC-36·ANA-04)
# larea = Revit 의 면적 계획 속 면적(Area)에 해당한다: 건축법 시행령 119조의 산입·불산입을 근거 코드와 함께 그린 다각형.
#         대상(대지·건축·바닥면적)과 효과(더함·뺌·용적률만 뺌·노대)는 근거 코드가 정한다(manual 만 사용자가 고른다). 비 IFC 의도 요소라 DoBIM_Defs 로 왕복한다
def _larea_params(p):
    from dobim_core import legal_area as LA
    err = _check_opt("larea", p, {"name": _name, "target": lambda v: v in LA.TARGETS, "rule": lambda v: v in LA.RULES, "effect": lambda v: v in LA.EFFECTS,
                                  "boundary": _poly_pts_ok, "note": lambda v: text_ok(v, 0, LA.NOTE_MAX)},
                     {"level": id_ok, "contact_length": _pos})
    if err:
        return err
    if not LA.rule_fits(p["rule"], p["target"], p["effect"]):
        return "larea 의 근거(rule)와 대상·효과가 맞지 않는다"
    if ("level" in p) != (p["target"] == "floor"):
        return "larea.level 은 바닥면적(floor) 대상에만, 반드시 둔다"
    if ("contact_length" in p) != (p["effect"] == "balcony"):
        return "larea.contact_length 는 노대(119-1-3-나)에만, 반드시 둔다"
    if p["rule"] == LA.MANUAL and not p["note"].strip():
        return "larea 사용자 조정(manual)은 근거를 note 에 적는다"
    return None


# ---------------------------------------------------------------- 독립 개구 객체(W2 세 번째 PR, ARC-34)
# opening = Revit 의 수직 개구·샤프트 개구·벽 개구 도구가 만드는 독립 개구. 호스트(슬래브·벽)의 params 를 바꾸지 않고 객체로 남아,
#   호스트를 그리고 IFC 로 낼 때 함께 뺀다(IFC 는 호스트마다 IfcOpeningElement + IfcRelVoidsElement 부속, GlobalId = part_guid(호스트, "void:<id>")).
#   mode "vertical": 슬래브 하나(host)를 경계 다각형(boundary)으로 수직 관통. "shaft": 레벨(level) + base_offset 에서 height 까지의 z 범위에서
#   경계 다각형이 경계 안에 드는 모든 저작 슬래브를 한 번에 관통. "wall": 벽 하나(host)에 사각(rect)·원(circle) 개구(문·창 없음, 호 위 거리 offset).
#   비 IFC 의도 요소(뷰·법정 면적 객체와 같다): 정의는 DoBIM_Defs 로 왕복한다
#   W2 열네 번째 PR(지붕·천장 개구): mode "vertical" 의 호스트가 경사 지붕(roof)·천장판(solid 범주 ceiling 의 판)도 된다. mode "face": 경사 지붕 면 하나에
#   면에 수직인 개구(평면 경계 = 면 윗면 위 스케치의 평면 투영). 경계 변마다 선택 볼록도(bulges, 원호), 샤프트는 선택 also(["roof", "ceiling"]: 그 범위의
#   지붕·천장도 관통). 선택 키는 쓸 때만 생긴다(쓰지 않은 프로젝트의 행·도면·IFC 는 그대로다)
OPENING_MODES = ("vertical", "shaft", "wall", "face")
OPENING_ALSO = ("roof", "ceiling")
OPENING_WALL_SHAPES = ("rect", "circle")
OPENINGS_OBJ_MAX = 500     # 독립 개구 객체 수 상한
SHAFT_HEIGHT_MAX = 500000.0


OPENING_PSETS_MAX = 64   # 독립 개구 하나의 속성 수 상한(W2 아홉 번째 PR)


def _opening_psets_ok(v) -> bool:
    """독립 개구의 속성 {속성셋: {속성: 값}}(값 = bool·int64·유한 float·문자열 255자 이하). 비어 있으면 키를 두지 않는다."""
    import re
    nm = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    if not isinstance(v, dict) or not v:
        return False
    n = 0
    for ps, props in v.items():
        if not (isinstance(ps, str) and nm.match(ps) and ps != "DoBIM_Intent" and isinstance(props, dict) and props):
            return False
        for k, x in props.items():
            n += 1
            if not (isinstance(k, str) and nm.match(k) and k != "id"):
                return False
            if isinstance(x, bool) or (isinstance(x, int) and -2**63 <= x < 2**63) or (isinstance(x, float) and num_ok(x)):
                continue
            if isinstance(x, str) and len(x) <= 255 and "\x00" not in x:
                continue
            return False
    return n <= OPENING_PSETS_MAX


def opening_bulges_problem(boundary, bulges) -> str | None:
    """경계 변마다 볼록도(W2 열네 번째 PR): 꼭짓점 수와 같은 길이, |값| <= 1(반원), 0 이 아닌 값이 하나 이상, 펼친 고리가 올바른 반시계 다각형."""
    from dobim_core import solidops as SOP
    if not isinstance(bulges, list) or len(bulges) != len(boundary) or not all(num_ok(b) and abs(b) <= SOP.BULGE_MAX for b in bulges):
        return "opening.bulges 는 꼭짓점마다 하나(변 i -> i+1 의 볼록도 tan(중심각/4), -1~1)다"
    if not any(bulges):
        return "opening.bulges 가 모두 0 이다(직선 경계는 bulges 를 두지 않는다)"
    lp = opening_loop({"boundary": boundary, "bulges": bulges})
    why = polygon_problem(lp)
    if why:
        return f"opening.bulges 로 펼친 경계가 올바르지 않다: {why}"
    if polygon_area2(lp) <= 0:
        return "opening.bulges 로 펼친 경계가 시계 방향이다"
    return None


def opening_loop(p) -> tuple:
    """수직·면·샤프트 개구의 실제 경계 고리((x, y), ...): 볼록도가 있으면 호를 현으로 펼친다(기존 원 32각형 규칙, 소수 6자리). 없으면 boundary 그대로."""
    b = p["boundary"]
    if "bulges" not in p:
        return tuple((q[0], q[1]) for q in b)
    from dobim_core import solidops as SOP
    return tuple((round(x, 6) + 0.0, round(y, 6) + 0.0) for x, y in SOP.bulge_loop(b, p["bulges"], None))


def _opening_params(p):
    if isinstance(p, dict) and "psets" in p:   # 속성(W2 아홉 번째 PR, 선택): 없으면 행은 이전과 같다
        if not _opening_psets_ok(p["psets"]):
            return "opening.psets 는 {속성셋: {속성: 값}} 이어야 한다(값은 bool·정수·유한 실수·255자 이하 문자열)"
        p = {k: v for k, v in p.items() if k != "psets"}
    if not isinstance(p, dict) or p.get("mode") not in OPENING_MODES:
        return "opening.mode 는 vertical·shaft·wall·face 중 하나다"
    m = p["mode"]
    if m in ("vertical", "shaft", "face") and "bulges" in p:   # 원호 경계(W2 열네 번째 PR, 선택)
        if not _poly_pts_ok(p.get("boundary")):
            return "opening.boundary 값이 올바르지 않다"
        if why := opening_bulges_problem(p["boundary"], p["bulges"]):
            return why
    bl = {"bulges": lambda v: True}
    if m in ("vertical", "face"):
        return _check_opt("opening", p, {"name": _name, "mode": lambda v: True, "host": id_ok, "boundary": _poly_pts_ok}, bl)
    if m == "shaft":
        err = _check_opt("opening", p, {"name": _name, "mode": lambda v: True, "level": id_ok, "boundary": _poly_pts_ok,
                                        "height": lambda v: _pos(v) and v <= SHAFT_HEIGHT_MAX},
                         {"base_offset": lambda v: num_ok(v) and v != 0 and abs(v) <= WT.BASE_OFFSET_MAX, **bl,
                          "also": lambda v: isinstance(v, list) and 1 <= len(v) and v == sorted(set(v)) and all(x in OPENING_ALSO for x in v)})
        return err
    err = _check_fields("opening", p, {"name": _name, "mode": lambda v: True, "host": id_ok, "shape": lambda v: v in OPENING_WALL_SHAPES,
                                       "offset": num_ok, "sill": num_ok, "width": _pos, "height": _pos})
    if err is None and p["shape"] == "circle" and abs(p["width"] - p["height"]) > 1e-6:
        return "opening: 원 개구는 폭과 높이가 같아야 한다"
    return err


def opening_shape(p) -> dict:
    """벽 개구 객체를 벽 모양 개구부(walls openings 한 항목)와 같은 꼴로."""
    return {"shape": p["shape"], "offset": p["offset"], "sill": p["sill"], "width": p["width"], "height": p["height"]}


# ---------------------------------------------------------------- 커튼월(W2 네 번째 PR, ARC-22)
# curtwall = 벽 축(직선·곡선) + 높이를 가진 커튼 시스템. 격자(규칙 + 개별 더하기·빼기)가 칸을 나누고, 멀리언(격자선 한 칸마다)·패널(칸마다)은
#   params 의 재정의로 하나씩 고칠 수 있는 부속이다(행은 커튼월 하나: 수천 개 멀리언도 행 하나라 열기·도면·병합이 가볍다).
#   IFC 는 IfcCurtainWall + IfcRelAggregates 로 IfcMember(MULLION)·IfcPlate(CURTAIN_PANEL)·IfcDoor(문 패널), GlobalId = part_guid(커튼월, "m:키"·"p:키").
#   배치 기하는 curtain.py 가 정한다(도면·IFC 가 같은 값을 쓴다)
CW_KEY_MAX = 40


def _cw_rule_ok(v) -> bool:
    if not isinstance(v, dict) or v.get("rule") not in CW.RULES:
        return False
    r = v["rule"]
    if r == "none":
        return set(v) == {"rule"}
    if r == "count":
        return set(v) == {"rule", "value"} and num_ok(v["value"]) and float(v["value"]).is_integer() and 1 <= v["value"] <= CW.LINES_MAX + 1
    keys = {"rule", "value", "justify"} if r == "spacing" else {"rule", "value"}
    return set(v) - {"justify"} == {"rule", "value"} and set(v) <= keys and _pos(v["value"]) and v.get("justify", "start") in CW.JUSTIFY


def _cw_types_ok(v) -> bool:
    return (isinstance(v, dict) and 1 <= len(v) <= CW.TYPES_MAX and all(
        text_ok(k, 1, 32) and isinstance(t, dict) and set(t) == {"width", "depth"} and all(_pos(t[x]) and t[x] <= CW.TYPE_DIM_MAX for x in t)
        for k, t in v.items()))


def _cw_list_ok(v) -> bool:
    return isinstance(v, list) and 1 <= len(v) <= CW.ADDS_MAX and all(num_ok(x) for x in v)


def _cw_keys_ok(v, vals) -> bool:
    return (isinstance(v, dict) and 1 <= len(v) <= 2 * CW.CELLS_MAX + 4 * CW.LINES_MAX
            and all(text_ok(k, 2, CW_KEY_MAX) and k[0] in "VHPE" and x in vals for k, x in v.items()))


def _cw_profile_ok(v) -> bool:
    """상단 윤곽(W2 열 번째 PR): [[u, z], ...] 숫자 쌍. 모양 규칙(벽 상단 윤곽과 같다)은 curtain.layout 이 본다."""
    return isinstance(v, list) and 2 <= len(v) <= WT.TOP_PTS_MAX and all(isinstance(q, list) and len(q) == 2 and all(num_ok(x) for x in q) for q in v)


def _cw_materials_ok(v) -> bool:
    """커튼월별 부속 재료(W2 열 번째 PR): {종류: 재료 이름} 1~4개."""
    return isinstance(v, dict) and 1 <= len(v) <= len(CW.MATERIAL_CATS) and all(k in CW.MATERIAL_CATS and text_ok(x, 1, NAME_MAX) for k, x in v.items())


def _cw_merges_ok(v) -> bool:
    """칸 합치기(W2 열한 번째 PR): [[대표 키, 끝 키], ...] 1개 이상, 키는 패널 키 "P<u>:<z>", 대표 키와 끝 키는 다르다."""
    return (isinstance(v, list) and 1 <= len(v) <= CW.ADDS_MAX
            and all(isinstance(m, list) and len(m) == 2 and m[0] != m[1] and all(text_ok(k, 3, 64) and CW.cell_of(k) is not None for k in m) for m in v))


def _curtwall_params(p):
    common = {"name": _name, "axis": lambda v: isinstance(v, list) and len(v) == 4 and all(num_ok(x) and abs(x) <= 1e7 for x in v),
              "u_grid": _cw_rule_ok, "v_grid": _cw_rule_ok, "types": _cw_types_ok,
              "mullion": lambda v: isinstance(v, dict) and "interior" in v and set(v) <= {"interior", "border"} and all(isinstance(x, str) for x in v.values()),
              "join": lambda v: v in CW.JOINS, "panel_thickness": lambda v: _pos(v) and v <= CW.PANEL_T_MAX}
    opt = {"base_offset": lambda v: num_ok(v) and v != 0 and abs(v) <= WT.BASE_OFFSET_MAX,
           "u_add": _cw_list_ok, "u_remove": _cw_list_ok, "v_add": _cw_list_ok, "v_remove": _cw_list_ok,
           "mullion_overrides": lambda v: True, "panel_overrides": lambda v: True, "materials": _cw_materials_ok,
           "merges": _cw_merges_ok}   # 칸 합치기(W2 열한 번째 PR)
    if isinstance(p, dict) and p.get("form") == "sloped":   # 경사 커튼 시스템(W2 열 번째 PR): 높이·곡선·상단 윤곽 대신 물매·외곽
        err = _check_opt("curtwall", p, {**common, "form": lambda v: v in CW.FORMS, "slope": num_ok,
                                         "boundary": lambda v: isinstance(v, list) and 3 <= len(v) <= CW.BOUNDARY_MAX and all(_pt_ok(q) and all(abs(x) <= 1e7 for x in q) for q in v)}, opt)
    else:
        err = _check_opt("curtwall", p, {**common, "height": lambda v: _pos(v) and v <= CW.HEIGHT_MAX}, {**opt, "arc": _pt_ok, "top_profile": _cw_profile_ok})
    if err is not None:
        return err
    types = p["types"]
    if any(x not in types for x in p["mullion"].values()):
        return "curtwall.mullion: 유형 이름이 types 에 없다"
    if "mullion_overrides" in p and not _cw_keys_ok(p["mullion_overrides"], set(types) | {""}):
        return "curtwall.mullion_overrides 값이 올바르지 않다(키 V·H, 값은 유형 이름 또는 빈 문자열)"
    if "panel_overrides" in p and not _cw_keys_ok(p["panel_overrides"], set(CW.PANEL_TYPES) - {CW.DEFAULT_PANEL}):
        return "curtwall.panel_overrides 값이 올바르지 않다(키 P, 값은 opaque·door·empty)"
    why = CW.problem(p)
    return f"curtwall: {why}" if why else None


# ---------------------------------------------------------------- 구조 보(W2 열다섯 번째 PR, STR-02·STR-03·STR-07)
# beam = 평면 두 점 사이의 수평 보(IfcBeam). 단면(사각·H형)은 보 유형(beamtype)의 사본을 행이 갖고(params.btype + 관계 of_type), 유형을 고치면
#   그 유형의 보 행만 다시 쓴다(벽·지붕·난간 유형과 같은 설계). 형상은 beams.py 가 정한다(도면·IFC·수량이 같은 값)
def _beamtype_params(p):
    # 선택 material: 구조 재료 이름(W2 열여덟 번째 PR). 그 유형의 보가 사본을 갖고 IFC4 는 IfcMaterialProfileSet 으로 낸다
    return _check_opt("beamtype", p, {"name": _name, "section": lambda v: BM.section_problem(v) is None}, {"material": _name})


def _beam_coord(v) -> bool:
    return _pt_ok(v) and all(abs(x) <= BM.COORD_MAX for x in v)


def _beam_params(p):
    err = _check_opt("beam", p, {"name": _name, "start": _beam_coord, "end": _beam_coord,
                                 "offset": lambda v: num_ok(v) and abs(v) <= BM.OFFSET_MAX, "anchor": lambda v: v in BM.ANCHORS,
                                 "section": lambda v: BM.section_problem(v) is None},
                     {"btype": id_ok, "roll": lambda v: num_ok(v) and v != 0 and -180.0 < v <= 180.0, "cutback": lambda v: v is False,
                      "end_offset": lambda v: num_ok(v) and abs(v) <= BM.OFFSET_MAX, "material": _name,   # 경사 보·구조 재료(W2 열여덟 번째 PR)
                      "bulge": lambda v: num_ok(v) and v != 0 and abs(v) <= BM.BULGE_MAX, "vertical_ends": lambda v: v is True,   # 곡선 보·경사 보 끝면·
                      "skew_cut": lambda v: v is True, "gap": lambda v: num_ok(v) and 0 < v <= BM.GAP_MAX})                        # 보끼리 접합 옵션(W2 열아홉 번째 PR)
    if err is not None:
        return err
    why = BM.beam_problem(p)
    return f"beam: {why}" if why else None


# 태그 대상 종류(annot.TAG_FIELDS 와 같다: 가져오기가 복원한 태그 대상을 이 종류로 검사한다)
TAG_TARGET_KINDS = ("wall", "door", "slab", "space", "stair", "finst", "roof", "railing")


# ---------------------------------------------------------------- 모델 그룹·조립품(W2 열한 번째 PR, ARC-46·COL-11)
# mgtype   = 모델 그룹 정의(Revit 의 모델 그룹 유형): 이름만. 배치(mgroup)가 정의를 가리킨다
# mgroup   = 모델 그룹 배치: 부속 요소 id(members)와 정의 안의 부속 번호(keys: 같은 정의의 다른 배치에서 같은 번호가 같은 부속), 배치 틀(at·angle·mirror),
#            레벨(level: 부속이 레벨에 놓일 때), 이 배치에서 뺀 부속 번호(excluded). 정의 동기화(mgroup.sync)가 번호로 부속을 맞춘다
# assembly = 조립품(Revit 의 Assembly): 이름·유형 이름 + 부속 요소 id. IFC 는 IfcElementAssembly + IfcRelAggregates
# 셋 다 비 IFC 의도 요소라 DoBIM_Defs 로 왕복한다(정의 = 정본). IFC 투영(IfcGroup·IfcElementAssembly)은 엔진 flush 가 맞춘다.
# 2D 상세 그룹(draft.group, 뷰 안 2D 요소 묶음)과는 다른 것이다: 이름(mgroup·assembly)·커맨드 접두가 겹치지 않는다
GROUP_MEMBERS_MAX = 500     # 그룹·조립품 하나의 부속 수 상한
GROUP_KEY_MAX = 100000      # 그룹 부속 번호 상한
GROUP_KINDS = ("wall", "door", "slab", "space", "stair", "railing", "roof", "solid", "finst")   # 모델 그룹 부속(element.copy 가 다루는 모델 요소, 통심선 제외)
ASSEMBLY_KINDS = ("wall", "door", "slab", "stair", "railing", "roof", "solid", "finst", "curtwall", "elevator")   # 조립품 부속(IFC 제품인 저작 모델 요소)


def _grp_ids_ok(v, lo=1, hi=GROUP_MEMBERS_MAX) -> bool:
    return isinstance(v, list) and lo <= len(v) <= hi and all(id_ok(x) for x in v) and len(set(v)) == len(v)


def _grp_keys_ok(v, lo=1) -> bool:
    return (isinstance(v, list) and lo <= len(v) <= GROUP_MEMBERS_MAX and all(type(x) is int and 0 <= x < GROUP_KEY_MAX for x in v)
            and len(set(v)) == len(v))


def _mgtype_params(p):
    return _check_fields("mgtype", p, {"name": _name})


def _mgroup_params(p):
    err = _check_opt("mgroup", p, {"name": _name, "type": id_ok, "members": lambda v: _grp_ids_ok(v, 0), "keys": lambda v: _grp_keys_ok(v, 0),
                                   "at": lambda v: _pt_ok(v) and all(abs(x) <= 1e7 for x in v), "angle": lambda v: num_ok(v) and 0 <= v < 360},
                     {"mirror": _TRUE, "level": id_ok, "excluded": _grp_keys_ok})
    if err is not None:
        return err
    if len(p["members"]) != len(p["keys"]):
        return "mgroup.keys 수가 members 수와 다르다"
    if set(p.get("excluded", ())) & set(p["keys"]):
        return "mgroup.excluded 가 지금 부속 번호와 겹친다"
    return None


def _assembly_params(p):
    return _check_fields("assembly", p, {"name": _name, "type": _name, "members": _grp_ids_ok})


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
    ElementKind("wall", True, "IfcWall", True, 1, _wall_params, geometric=True,
                refs=(("wtype", "id", "of_type"), ("stack", "id", None)), ref_kinds=(("wtype", ("walltype",)), ("stack", ("stackwall",)))),   # 벽 유형·스택 벽 부분(W2 일곱 번째 PR)
    ElementKind("door", True, "IfcDoor", True, 2, _door_params, geometric=True),
    ElementKind("slab", True, "IfcSlab", True, 1, _slab_params, geometric=True),
    ElementKind("stair", True, "IfcStair", True, 1, _stair_params, geometric=True),
    ElementKind("space", True, "IfcSpace", True, 1, _space_params, geometric=True),
    ElementKind("elevator", True, "IfcTransportElement", True, 1, _elevator_params, geometric=True, refs=(("stops", "ids", None), ("hole_slabs", "ids", None))),
    ElementKind("roof", True, "IfcRoof", True, 1, _roof_params, geometric=True,
                refs=(("rtype", "id", "of_type"),), ref_kinds=(("rtype", ("rooftype",)),)),   # 지붕 유형(W2 여덟 번째 PR)
    ElementKind("railing", True, "IfcRailing", True, 1, _railing_params, geometric=True, refs=(("src", "id", None), ("rltype", "id", "of_type")),
                ref_kinds=(("src", ("stair", "slab")), ("rltype", ("railtype",)))),   # 난간 유형(W2 열세 번째 PR)
    ElementKind("site", True, "IfcSite", True, 0, _site_params),
    # 일반 솔리드(18단계): 돌출·로프트·판·스윕·메시. IFC 클래스는 범주(category)가 정한다(행마다 다르다)
    ElementKind("solid", True, None, True, 1, _solid_params, geometric=True),
    ElementKind("ftype", True, None, True, 3, _ftype_params),
    ElementKind("finst", True, None, True, 4, _finst_params, geometric=True, refs=(("type", "id", "of_type"),)),
    ElementKind("foreign", True, None, False, 5, _foreign_params),
    # 통심선(19단계): 뷰처럼 IFC 신원이 없는 의도 요소. 정의는 DoBIM_Defs 로 왕복한다
    ElementKind("grid", False, None, False, 6, _grid_params),
    # 뷰 템플릿(W1 1-C, VIEW-15): 뷰 설정(가시성·그래픽 재정의·필터·축척) 묶음. 비 IFC 의도 요소라 정의는 DoBIM_Defs 로 왕복한다
    ElementKind("vtemplate", False, None, False, 5, _vtemplate_params),
    ElementKind("view", False, None, False, 6, _view_params, refs=(("level", "id", None), ("template", "id", None), ("parent", "id", None)),
                ref_kinds=(("level", ("level",)), ("template", ("vtemplate",)), ("parent", ("view",)))),   # 평면 뷰만 level 이 있다. parent 는 콜아웃 뷰만
    ElementKind("dim", False, None, False, 7, _dim_params, refs=(("view", "id", "annot_of"), ("a", "ref", None), ("b", "ref", None)),
                ref_kinds=(("view", ("view",)),)),
    ElementKind("tag", False, None, False, 7, _tag_params, refs=(("view", "id", "annot_of"), ("target", "id", None)),
                ref_kinds=(("view", ("view",)), ("target", TAG_TARGET_KINDS))),
    # 시트 정의(뷰 배치·표제란 인자): 뷰와 같은 비 IFC 의도 요소다. 뷰를 가리키는 참조는 목록(views)·중첩(revisions·callouts 의 view)이라 관계로 묻지 않는다
    ElementKind("sheet", False, None, False, 8, _sheet_params, refs=(("views", "ids", None), ("key_plan", "id", None)),
                ref_kinds=(("key_plan", ("view",)),)),
    # 2D 작도(27단계): 뷰에 속한 2D 요소(관계 annot_of 로 뷰를 지울 때 함께 지운다)와 층 스타일. 정의는 DoBIM_Defs 로 왕복한다
    ElementKind("dlayer", False, None, False, 6, _dlayer_params),
    ElementKind("draft", False, None, False, 7, _draft_params, refs=(("view", "id", "annot_of"),), ref_kinds=(("view", ("view",)),)),
    # 리비전·키노트(W1 1-B 3차): 시트처럼 비 IFC 의도 요소다. 리비전이 싣는 시트는 목록이라 관계로 묻지 않는다(지운 시트는 일정표에서 빠진다)
    ElementKind("revision", False, None, False, 9, _revision_params, refs=(("sheets", "ids", None),)),
    ElementKind("keynote", False, None, False, 6, _keynote_params),
    # 프로젝트 매개변수(W1 연장, DATA-03): 리비전·키노트처럼 비 IFC 의도 요소다
    ElementKind("pparam", False, None, False, 6, _pparam_params),
    # 법정 면적 객체(W1 연장, ARC-36): 바닥면적 대상만 레벨을 가리킨다(평면 뷰의 level 과 같은 참조)
    ElementKind("larea", False, None, False, 6, _larea_params, refs=(("level", "id", None),), ref_kinds=(("level", ("level",)),)),
    # 독립 개구 객체(W2 세 번째 PR, ARC-34): 수직(슬래브)·샤프트(레벨 범위의 슬래브들)·벽 개구. 호스트를 지우면 고아로 남는다(그리지 않는다)
    ElementKind("opening", False, None, False, 6, _opening_params, refs=(("host", "id", None), ("level", "id", None)),
                ref_kinds=(("host", ("slab", "wall", "roof", "solid")), ("level", ("level",)))),   # 지붕·천장판 호스트(W2 열네 번째 PR)
    # 커튼월(W2 네 번째 PR, ARC-22): 멀리언·패널은 행이 아니라 커튼월의 IFC 부속(IfcRelAggregates)이다
    ElementKind("curtwall", True, "IfcCurtainWall", True, 1, _curtwall_params, geometric=True),
    # 재료 외관 정의(W2 다섯 번째 PR, DATA-05·VIS-07): 프로젝트 매개변수처럼 비 IFC 의도 요소다(재료 이름이 키)
    ElementKind("matdef", False, None, False, 6, _matdef_params),
    # 벽 유형(W2 일곱 번째 PR, ARC-11): 비 IFC 의도 요소(정의는 DoBIM_Defs). 벽보다 먼저 만든다(벽이 of_type 으로 가리킨다)
    ElementKind("walltype", False, None, False, 0, _walltype_params),
    # 스택 벽(W2 일곱 번째 PR, ARC-09): 부분 벽(보통 벽 행)을 모으는 형상 없는 IfcWall. 부분 벽보다 뒤에 투영한다(집합 관계가 부분 엔티티를 가리킨다)
    ElementKind("stackwall", True, "IfcWall", True, 2, _stackwall_params, refs=(("parts", "ids", None),)),
    # 지붕 유형(W2 여덟 번째 PR): 벽 유형과 같은 비 IFC 의도 요소(정의는 DoBIM_Defs). 지붕보다 먼저 만든다
    ElementKind("rooftype", False, None, False, 0, _rooftype_params),
    ElementKind("railtype", False, None, False, 0, _railtype_params),   # 난간 유형(W2 열세 번째 PR, ARC-31)
    # 구조 보·보 유형(W2 열다섯 번째 PR, STR-02): 보는 IfcBeam, 유형은 비 IFC 의도(투영이 IfcBeamType + IfcRelDefinesByType 를 만든다)
    ElementKind("beamtype", False, None, False, 0, _beamtype_params),
    ElementKind("beam", True, "IfcBeam", True, 1, _beam_params, geometric=True, refs=(("btype", "id", "of_type"),), ref_kinds=(("btype", ("beamtype",)),)),
    # 모델 그룹 정의·배치·조립품(W2 열한 번째 PR, ARC-46·COL-11): 비 IFC 의도 요소(정의는 DoBIM_Defs). 부속을 가리키므로 모델 요소 뒤에 만든다
    ElementKind("mgtype", False, None, False, 6, _mgtype_params),
    ElementKind("mgroup", False, None, False, 7, _mgroup_params, refs=(("type", "id", None), ("members", "ids", None), ("level", "id", None)),
                ref_kinds=(("type", ("mgtype",)), ("level", ("level",)))),
    ElementKind("assembly", False, None, False, 7, _assembly_params, refs=(("members", "ids", None),)),
)}

NON_IFC_KINDS = frozenset(k for k, v in ELEMENT_KINDS.items() if not v.ifc)      # 신원·속성셋·재료가 없는 의도 요소(뷰 정의, 치수·태그)
PROJECTED_KINDS = tuple(k.name for k in sorted(ELEMENT_KINDS.values(), key=lambda k: (k.order, k.name)) if k.projected)
FIXED_IFC_CLASS = {k: v.ifc_class for k, v in ELEMENT_KINDS.items() if v.ifc_class}   # 코어가 직접 정하는 IFC 클래스
KIND_NAMES = tuple(ELEMENT_KINDS)
# 의도 복원(S2 가져오기)이 IFC 클래스로 종류를 정하는 것: IFC 클래스 -> 종류. 패밀리 타입·인스턴스(ftype/finst)는 클래스가 레시피마다 달라 의도가 스스로 밝힌
# 종류를 따른다(family.restore, ADR-0014). 뷰·주석은 IFC 신원이 없어 IFC 에서 복원할 수 없다(알려진 한계)
IMPORTABLE_BY_CLASS = {"IfcBuildingStorey": "level", "IfcWall": "wall", "IfcDoor": "door", "IfcSlab": "slab", "IfcStair": "stair", "IfcSpace": "space", "IfcRailing": "railing", "IfcTransportElement": "elevator", "IfcRoof": "roof", "IfcSite": "site", "IfcCurtainWall": "curtwall", "IfcBeam": "beam"}
FAMILY_OF_KIND = {"stackwall": "wall.stacked", "level": "level.basic", "wall": "wall.basic", "door": "door.single", "slab": "slab.basic", "stair": "stair.straight", "space": "space.basic", "railing": "railing.basic", "elevator": "elevator.basic", "roof": "roof.basic", "site": "site.basic", "solid": "solid.generic",
                  "curtwall": "curtwall.basic", "beam": "beam.basic"}


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
    RelKind("on_level", _fs("wall", "slab", "stair", "space", "railing", "elevator", "roof", "solid", "finst", "foreign", "curtwall", "stackwall", "beam"), _fs("level"), guard=("src",), violation="LEVEL_DELETED", message="벽이 놓인 레벨이 지워졌다"),
    RelKind("joins", _fs("wall"), _fs("wall"), guard=("src", "dst"), violation="JOIN_TARGET_DELETED", message="조인 대상 벽이 지워졌다"),
    RelKind("hosts", _fs("wall"), _fs("door", "finst"), guard=("src", "dst"), violation="HOST_DELETED",
            message="호스트 벽이 지워졌는데 문이 그 벽에 놓여 있다"),
    RelKind("succ", _fs("wall"), _fs("wall"), dangling_ok=True),
    RelKind("annot_of", _fs("dim", "tag", "draft"), _fs("view"), violation="VIEW_DELETED", message="주석이 속한 뷰가 지워졌다"),
    # 벽 -> 벽 유형(W2 일곱 번째 PR, ARC-11)도 같은 관계다(Revit 의 타입 지정)
    RelKind("of_type", _fs("finst", "wall", "roof", "railing", "beam"), _fs("ftype", "walltype", "rooftype", "railtype", "beamtype"), violation="TYPE_DELETED", message="인스턴스의 패밀리 타입이 지워졌다"),
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
    if any(r is not None and r["kind"] == "finst" and "also_cuts" in r["params"] for r in (e[2], e[3])):
        out.add(("xcut_finsts",))   # 함께 자르는 벽(25단계)이 있는 인스턴스 목록 입력: 그런 인스턴스가 생기거나 바뀔 때만 바뀐다
    if any(r is not None and r["kind"] == "wall" and "top_level" in r["params"] for r in (e[2], e[3])):
        out.add(("tied_walls",))   # 상단 구속 벽(24단계) 목록 입력: 구속 벽이 생기거나 바뀔 때만 바뀐다(다른 벽 편집은 평면 캐시를 흔들지 않는다)
    if any(r is not None and r["kind"] == "wall" and "arc" in r["params"] for r in (e[2], e[3])):
        out.add(("arc_walls",))   # 곡선 벽(W2 ARC-04) 목록 입력: 곡선 벽이 생기거나 바뀔 때만 바뀐다
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
