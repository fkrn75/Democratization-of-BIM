# SPDX-License-Identifier: MPL-2.0
"""벽 유형·위치선·스택 벽의 순수 계산(W2 일곱 번째 PR, ARC-11·ARC-09).

벽 유형(walltype) = Revit 의 시스템 패밀리 유형(기본 벽 유형): 이름 붙은 층 구성(또는 두께)·코어 범위·기능·기본 높이.
벽 행은 유형의 층 구성을 복사해 갖고(params.wtype = 유형 id), 유형을 고치면 그 유형의 벽 행을 모두 다시 쓴다(증분 갱신).
벽 형상·도면·IFC 는 계속 벽 행만 읽으므로 유형이 없는 벽은 바이트까지 그대로다.

위치선(params.loc): Revit 의 Location Line. 벽 행의 axis 는 지금처럼 벽 몸체의 중심선이고, 위치선은 중심선에서 지역 v 방향
(축 진행 방향 왼쪽이 +)으로 loc_offset 만큼 떨어진 선이다. 층 순서는 wall.layers 와 같다: 첫 층 = 지역 -v 면 = 바깥(exterior) 쪽.
(반시계로 그린 외벽은 왼쪽이 방 쪽이다: regions.py 의 규칙과 같다.)

이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

LOC_LINES = ("wall_center", "core_center", "finish_ext", "finish_int", "core_ext", "core_int")
LOC_LABEL = {"wall_center": "벽 중심선", "core_center": "코어 중심선", "finish_ext": "마감면: 바깥", "finish_int": "마감면: 안",
             "core_ext": "코어 면: 바깥", "core_int": "코어 면: 안"}
FUNCTIONS = ("exterior", "interior", "foundation", "retaining", "soffit", "core_shaft")   # Revit 벽 유형의 기능(Function)
WALLTYPES_MAX = 1000          # 벽 유형 수 상한
STACK_SEGS_MAX = 8            # 스택 벽 구간 수 상한
STACK_SEG_MIN = 1.0           # 구간 높이 하한 mm


def core_ok(v, n: int) -> bool:
    """코어 범위 [a, b]: 층 번호 a 부터 b-1 까지가 코어(0 <= a < b <= 층 수)."""
    return (isinstance(v, list) and len(v) == 2 and all(type(x) is int for x in v) and 0 <= v[0] < v[1] <= n)


def core_of(p: dict):
    """층 구성이 있는 행의 코어 범위 (a, b). 명시가 없으면 가장 두꺼운 층(같으면 앞쪽) 하나. 층 구성이 없으면 None(몸체 전체가 코어)."""
    layers = p.get("layers")
    if not layers:
        return None
    if "core" in p:
        return tuple(p["core"])
    best = 0
    for i, x in enumerate(layers):
        if x["thickness"] > layers[best]["thickness"]:
            best = i
    return (best, best + 1)


def loc_offset(p: dict, line: str | None = None) -> float:
    """위치선의 지역 v 좌표(몸체 중심선 기준, 왼쪽 +). line 을 주지 않으면 행의 loc(없으면 벽 중심선)."""
    line = line or p.get("loc", "wall_center")
    t = p["thickness"]
    if line == "wall_center":
        return 0.0
    if line == "finish_ext":
        return -t / 2.0
    if line == "finish_int":
        return t / 2.0
    c = core_of(p)
    if c is None:
        lo, hi = 0.0, t
    else:
        acc, lo, hi = 0.0, 0.0, t
        for i, x in enumerate(p["layers"]):
            if i == c[0]:
                lo = acc
            acc += x["thickness"]
            if i == c[1] - 1:
                hi = acc
    v0 = -t / 2.0
    if line == "core_ext":
        return v0 + lo
    if line == "core_int":
        return v0 + hi
    return v0 + (lo + hi) / 2.0   # core_center


def type_fields(tp: dict) -> dict:
    """유형 params 에서 벽 행으로 복사하는 형상 키(두께·층 구성·코어 범위)."""
    out = {"thickness": tp["thickness"]}
    if "layers" in tp:
        out["layers"] = [dict(x) for x in tp["layers"]]
        if "core" in tp:
            out["core"] = list(tp["core"])
    return out


def with_type(p: dict, tid: str, tp: dict) -> dict:
    """벽 params p 에 유형 tp 를 입힌 새 params(축은 그대로: 위치선 유지 이동은 부르는 쪽이 한다)."""
    q = dict(p)
    q.pop("layers", None)
    q.pop("core", None)
    q.update(type_fields(tp))
    q["wtype"] = tid
    return q


def stack_heights(segments: list, total: float) -> list:
    """스택 구간 높이(아래부터). 높이가 None 인 구간 하나가 나머지를 채운다. 안 되면 ValueError."""
    fixed = sum(s["height"] for s in segments if s.get("height") is not None)
    var = [i for i, s in enumerate(segments) if s.get("height") is None]
    if len(var) != 1:
        raise ValueError("높이를 비운(가변) 구간이 정확히 하나여야 한다")
    rest = total - fixed
    if rest < STACK_SEG_MIN:
        raise ValueError(f"가변 구간 높이가 {STACK_SEG_MIN:g}mm 보다 작다(전체 {total:g} - 고정 {fixed:g})")
    return [rest if s.get("height") is None else s["height"] for s in segments]
