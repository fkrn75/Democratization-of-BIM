# SPDX-License-Identifier: MPL-2.0
"""레시피 정적 분석과 평가(순수 함수). ifcopenshell·파일·시각·난수를 쓰지 않는다 (ADR-0006: queries 가 이 모듈을 부른다).

흐름
1. `load_recipe(dict)` : 구조 검사(recipe.py) + 정적 분석(이름 해석·순환·수준 분류) → (Recipe, Plan). 결과는 해시로 캐시한다.
2. `evaluate(recipe, plan, type_values, inst_values, host)` : 값 검사(타입·범위) → 파생값 DAG 평가 → 제약 → 형상·평면 기호·IFC 속성 해석.
   결과는 JSON 으로 직렬화되는 순수 값이다(숫자는 소수 6자리로 반올림).

좌표계(로컬): x = 호스트 축선 방향(문 폭 방향), y = 호스트 두께 방향(벽 중심선이 0), z = 위쪽. 원점은 개구부 중심의 배치 높이.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from dobim_core.family import expr as E
from dobim_core.family.recipe import (HOST_NAMES, RESERVED_NAMES, Extrude, ExprVal, FamilyError, Issue, Param, Recipe,
                                      Sweep, check_json_value, parse_recipe, ptr)

MAX_REPEAT = 50
MAX_ITEMS = 400
MAX_SAMPLES = 120
MAX_PRIMS = 200
COORD_MAX = 100_000.0   # mm: 100 m. 이보다 큰 좌표는 단위 실수로 본다
DEFAULT_HOST = {"host_thickness": 200.0, "host_height": 3000.0}
_PLANE_AXES = {"xy": ("x", "y", "z"), "xz": ("x", "z", "y"), "yz": ("y", "z", "x")}   # (u축, v축, 돌출축)


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(obj: Any) -> str:
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


def r6(x: float) -> float:
    return round(float(x), 6) + 0.0


# ---------------------------------------------------------------- 식 순회
@dataclass(frozen=True)
class ExprSite:
    path: str
    raw: Any              # 숫자·불리언 리터럴 또는 식 문자열
    extra: frozenset      # 이 위치에서만 보이는 이름(반복 변수)
    type_only: bool       # 타입 수준 값만 참조해야 하는 위치(타입 속성셋·타입 속성)
    want: str             # number | bool | any


def _sites(r: Recipe) -> list[ExprSite]:
    out: list[ExprSite] = []

    def add(path, raw, want="number", extra=frozenset(), type_only=False, value=False):
        if value and not isinstance(raw, ExprVal):
            return   # 속성 값의 리터럴(문자열 포함)은 식이 아니다
        if isinstance(raw, ExprVal):
            raw = raw.expr
            want = "any"
        out.append(ExprSite(path, raw, extra, type_only, want))

    for k, v in r.derived.items():
        add(ptr("derived", k), v, "any")
    for k, v in r.refs.items():
        add(ptr("refs", k, "at"), v.at)
    for i, c in enumerate(r.constraints):
        add(ptr("constraints", i, "expr"), c.expr, "bool")
    if r.host and r.host.cut:
        for ax in ("x", "z"):
            for j, v in enumerate(getattr(r.host.cut, ax)):
                add(ptr("host", "cut", ax, j), v)
    for k, v in r.ifc.type_attrs.items():
        add(ptr("ifc", "type_attrs", k), v, "any", type_only=True, value=True)
    for k, v in r.ifc.attrs.items():
        add(ptr("ifc", "attrs", k), v, "any", value=True)
    for pn, ps in r.psets.items():
        for k, v in ps.props.items():
            add(ptr("psets", pn, "props", k), v, "any", type_only=(ps.on == "type"), value=True)
    for i, s in enumerate(r.solids):
        base = ("solids", i)
        extra = frozenset([s.repeat.var]) if s.repeat else frozenset()
        if s.repeat:
            add(ptr(*base, "repeat", "count"), s.repeat.count)
        if s.when is not None:
            add(ptr(*base, "when"), s.when, "bool", extra)
        if isinstance(s, Extrude):
            for j, v in enumerate(s.range):
                add(ptr(*base, "range", j), v, extra=extra)
            p = s.profile
            if p.shape == "rect":
                for ax in ("u", "v"):
                    for j, v in enumerate(getattr(p, ax)):
                        add(ptr(*base, "profile", ax, j), v, extra=extra)
            elif p.shape == "circle":
                for j, v in enumerate(p.center):
                    add(ptr(*base, "profile", "center", j), v, extra=extra)
                add(ptr(*base, "profile", "r"), p.r, extra=extra)
            else:
                for j, pt in enumerate(p.points):
                    for m, v in enumerate(pt):
                        add(ptr(*base, "profile", "points", j, m), v, extra=extra)
        else:
            for j, pt in enumerate(s.path):
                for m, v in enumerate(pt):
                    add(ptr(*base, "path", j, m), v, extra=extra)
            if s.profile.shape == "rect":
                add(ptr(*base, "profile", "w"), s.profile.w, extra=extra)
                add(ptr(*base, "profile", "h"), s.profile.h, extra=extra)
            else:
                add(ptr(*base, "profile", "r"), s.profile.r, extra=extra)
    for scale, prims in r.plan_symbol.items():
        for i, pr in enumerate(prims):
            base = ("plan_symbol", scale, i)
            if pr.when is not None:
                add(ptr(*base, "when"), pr.when, "bool")
            t = pr.type
            if t == "line":
                for key, pt in (("from", pr.from_), ("to", pr.to)):
                    for j, v in enumerate(pt):
                        add(ptr(*base, key, j), v)
            elif t == "arc":
                for j, v in enumerate(pr.center):
                    add(ptr(*base, "center", j), v)
                for key in ("radius", "start", "end"):
                    add(ptr(*base, key), getattr(pr, key))
            elif t == "circle":
                for j, v in enumerate(pr.center):
                    add(ptr(*base, "center", j), v)
                add(ptr(*base, "radius"), pr.radius)
            elif t == "rect":
                for ax in ("u", "v"):
                    for j, v in enumerate(getattr(pr, ax)):
                        add(ptr(*base, ax, j), v)
            else:
                for j, pt in enumerate(pr.points):
                    for m, v in enumerate(pt):
                        add(ptr(*base, "points", j, m), v)
    return out


# ---------------------------------------------------------------- 정적 분석
@dataclass
class Plan:
    """정적 분석 결과: 파생값·참조 평면의 평가 순서와 각 이름의 수준."""
    order: tuple[tuple[str, str], ...]      # (종류 derived|refs, 이름) 위상 순서
    level: dict[str, str]                   # 이름 → "type" | "instance"
    hosted: bool


def analyze(r: Recipe) -> tuple[Plan | None, list[Issue]]:
    issues: list[Issue] = []
    names: dict[str, str] = {}              # 이름 → 선언 위치
    for group, d in (("params", r.params), ("derived", r.derived), ("refs", r.refs)):
        for k in d:
            if k in RESERVED_NAMES:
                issues.append(Issue("E_NAME_RESERVED", ptr(group, k), f"예약된 이름이다: {k}",
                                    "host_thickness/host_height 와 함수 이름은 선언할 수 없다"))
            elif k in names:
                issues.append(Issue("E_NAME_DUPLICATE", ptr(group, k), f"이름이 {names[k]} 와 겹친다: {k}",
                                    "params/derived/refs 는 한 이름공간을 쓴다"))
            else:
                names[k] = group
    hosted = r.host is not None
    host_names = set(HOST_NAMES) if hosted else set()
    known = set(names) | host_names

    # 식 컴파일과 이름 해석
    deps: dict[str, set[str]] = {}
    used: set[str] = set()
    compiled: dict[str, E.Compiled] = {}
    names_of: dict[str, frozenset] = {}
    sites = _sites(r)
    for s in sites:
        if not isinstance(s.raw, str):
            continue
        try:
            c = E.compile_expr(s.raw)
        except E.ExprError as ex:
            issues.append(Issue(ex.code, s.path, ex.message, ex.hint))
            continue
        compiled[s.path] = c
        names_of[s.path] = c.names
        for nm in c.names:
            used.add(nm)
            if nm in s.extra:
                continue
            if nm not in known:
                if nm in HOST_NAMES and not hosted:
                    issues.append(Issue("E_HOST_CONTEXT", s.path, f"{nm} 은 host 가 있는 패밀리에서만 쓴다",
                                        "host 블록을 선언하거나 이 이름을 쓰지 마세요(자립형 패밀리에는 호스트 문맥이 없다)"))
                else:
                    near = difflib.get_close_matches(nm, sorted(known | set(s.extra)), n=3)
                    issues.append(Issue("E_UNDEFINED_NAME", s.path, f"정의되지 않은 이름: {nm}",
                                        ("비슷한 이름: " + ", ".join(near)) if near else "params/derived/refs 에 선언하세요"))
        key = _node_of(s.path)
        if key:
            deps.setdefault(key, set()).update(n for n in c.names if n in names)
    # 반복 변수가 선언 이름과 겹치는지, void 의 cuts 대상이 있는지
    ids: dict[str, int] = {}
    for i, sd in enumerate(r.solids):
        if sd.id in ids:
            issues.append(Issue("E_SOLID_ID_DUPLICATE", ptr("solids", i, "id"), f"solid id 가 겹친다: {sd.id}"))
        ids[sd.id] = i
        if sd.repeat and (sd.repeat.var in known or sd.repeat.var in RESERVED_NAMES):
            issues.append(Issue("E_NAME_DUPLICATE", ptr("solids", i, "repeat", "var"),
                                f"반복 변수가 선언된 이름과 겹친다: {sd.repeat.var}", "다른 이름(i, k 등)을 쓰세요"))
    for i, sd in enumerate(r.solids):
        for j, t in enumerate(sd.cuts):
            if t not in ids or r.solids[ids[t]].role != "solid":
                issues.append(Issue("E_CUT_TARGET", ptr("solids", i, "cuts", j), f"자를 대상 solid 를 찾을 수 없다: {t}",
                                    "role=solid 인 형상의 id 를 쓰세요(반복 형상은 기본 id 로 지정하면 모든 반복에 적용)"))
    if "default" not in r.plan_symbol:
        issues.append(Issue("E_PLAN_DEFAULT", "/plan_symbol", "plan_symbol 에 default 키가 없다",
                            "축척을 가리지 않는 기본 기호를 default 에 쓰세요"))
    else:
        if not r.plan_symbol["default"]:
            issues.append(Issue("E_PLAN_DEFAULT", "/plan_symbol/default", "default 평면 기호가 비어 있다"))
    for scale in r.plan_symbol:
        if scale != "default" and not re.match(r"^1:[1-9][0-9]{0,3}$", scale):
            issues.append(Issue("E_PLAN_SCALE", ptr("plan_symbol", scale), f"축척 키 형식 오류: {scale}", "default 또는 1:50 같은 형식"))

    # 순환과 위상 정렬 (파생값·참조 평면만 노드)
    graph = {n: sorted(deps.get(ptr("derived", n) if names[n] == "derived" else ptr("refs", n, "at"), set()) & {m for m in names if names[m] != "params"})
             for n in names if names[n] != "params"}
    order, cyc = _toposort(graph)
    if cyc:
        issues.append(Issue("E_CYCLE", ptr(names[cyc[0]], cyc[0]), "파생값 순환 참조: " + " -> ".join(cyc),
                            "파생값은 DAG 여야 한다. 순환을 끊을 매개변수를 하나 두세요"))
    if any(i.level == "error" for i in issues):
        return None, issues

    # 수준(type/instance) 전파
    level: dict[str, str] = {}
    for k, p in r.params.items():
        level[k] = p.kind
    for h in host_names:
        level[h] = "instance"
    for n in order:
        group = names[n]
        site = ptr("derived", n) if group == "derived" else ptr("refs", n, "at")
        used_here = set(names_of.get(site, ()))   # 파생값이 직접 쓰는 모든 이름(호스트 문맥 포함)
        level[n] = "instance" if any(level.get(d) == "instance" for d in used_here) else "type"
    for s in sites:
        if not s.type_only or s.path not in compiled:
            continue
        bad = sorted(n for n in compiled[s.path].names if level.get(n) == "instance")
        if bad:
            issues.append(Issue("E_TYPE_REFERENCES_INSTANCE", s.path,
                                "타입 수준 값이 인스턴스 수준 이름을 참조한다: " + ", ".join(bad),
                                "이 속성셋을 on=instance 로 옮기거나 해당 매개변수를 kind=type 으로 바꾸세요"))
    for n, g in names.items():
        if n not in used:
            issues.append(Issue("W_UNUSED", ptr(g, n), f"{g} 의 {n} 을 어디에서도 쓰지 않는다", "필요 없으면 지우세요", "warning"))
    if any(i.level == "error" for i in issues):
        return None, issues
    return Plan(tuple((names[n], n) for n in order), level, hosted), issues


def _node_of(path: str) -> str | None:
    """식 위치가 파생값/참조 노드의 것이면 그 이름을, 아니면 None."""
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] == "derived":
        return path
    if len(parts) >= 4 and parts[1] == "refs" and parts[3] == "at":
        return path
    return None


def _toposort(graph: dict[str, list[str]]) -> tuple[list[str], list[str] | None]:
    state: dict[str, int] = {}
    out: list[str] = []
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        st = state.get(n, 0)
        if st == 2:
            return None
        if st == 1:
            i = stack.index(n)
            return stack[i:] + [n]
        state[n] = 1
        stack.append(n)
        for m in graph.get(n, ()):
            c = visit(m)
            if c:
                return c
        stack.pop()
        state[n] = 2
        out.append(n)
        return None

    for n in sorted(graph):
        c = visit(n)
        if c:
            return out, c
    return out, None


_LOAD_CACHE: OrderedDict[str, tuple[Recipe, Plan]] = OrderedDict()   # 정확한 LRU(가득 차도 새 레시피를 캐시한다)
_LOAD_MAX = 256


def load_recipe(data: dict) -> tuple[Recipe, Plan]:
    """구조 검사 + 정적 분석. 실패하면 FamilyError. 같은 레시피는 캐시에서 돌려준다."""
    check_json_value(data)   # NaN/Infinity·거대 정수·깊은 중첩은 digest(canon)·구조 검사 전에 구조화 오류로 거절한다
    key = digest(data)
    hit = _LOAD_CACHE.get(key)
    if hit is not None:
        _LOAD_CACHE.move_to_end(key)
        return hit
    r = parse_recipe(data)
    plan, issues = analyze(r)
    errs = [i for i in issues if i.level == "error"]
    if errs:
        raise FamilyError(errs)
    assert plan is not None
    _LOAD_CACHE[key] = (r, plan)
    while len(_LOAD_CACHE) > _LOAD_MAX:
        _LOAD_CACHE.popitem(last=False)
    return r, plan


# ---------------------------------------------------------------- 값 검사
def _bad(code, path, msg, hint=None):
    return FamilyError(Issue(code, path, msg, hint))


def coerce_param(name: str, p: Param, v: Any, path: str) -> Any:
    t = p.type
    if t in ("number", "int"):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise _bad("E_PARAM_TYPE", path, f"{name}: 숫자가 필요하다(받은 값: {v!r})")
        if isinstance(v, float) and not math.isfinite(v):
            raise _bad("E_PARAM_TYPE", path, f"{name}: 유한한 수여야 한다")
        if abs(v) > 1e15:
            raise _bad("E_PARAM_RANGE", path, f"{name}: 값이 ±1e15 를 넘는다", "단위(mm)를 확인하세요")
        if t == "int" and v != int(v):
            raise _bad("E_PARAM_TYPE", path, f"{name}: 정수가 필요하다(받은 값: {v!r})")
        if p.min is not None and v < p.min or p.max is not None and v > p.max:
            raise _bad("E_PARAM_RANGE", path, f"{name}={v} 가 범위 [{p.min}, {p.max}] 밖이다",
                       "범위 안의 값을 쓰거나 레시피의 min/max 를 조정하세요")
        return int(v) if t == "int" else float(v)
    if t == "bool":
        if not isinstance(v, bool):
            raise _bad("E_PARAM_TYPE", path, f"{name}: true/false 가 필요하다(받은 값: {v!r})")
        return v
    if t == "enum":
        if v not in (p.values or []):
            raise _bad("E_PARAM_RANGE", path, f"{name}={v!r} 는 허용 값이 아니다", "허용: " + ", ".join(p.values or []))
        return v
    if not isinstance(v, str) or len(v) > 255:
        raise _bad("E_PARAM_TYPE", path, f"{name}: 255자 이하 문자열이 필요하다")
    return v


def resolve_values(r: Recipe, type_values: dict | None, inst_values: dict | None, base: str = "") -> tuple[dict, dict]:
    """주어진 값을 검사해 (타입값, 인스턴스값)을 완전한 사전으로 돌려준다(없는 것은 기본값).
    키가 틀렸거나 type/instance 종류가 어긋나면 오류. 범위·타입도 여기서 거른다."""
    out = {"type": {}, "instance": {}}
    given = {"type": dict(type_values or {}), "instance": dict(inst_values or {})}
    other = {"type": "instance", "instance": "type"}
    label = {"type": "type_values", "instance": "values"}
    for kind in ("type", "instance"):
        for k in given[kind]:
            if k not in r.params:
                near = difflib.get_close_matches(k, list(r.params), n=3)
                raise _bad("E_UNKNOWN_PARAM", ptr(label[kind], k) if not base else base + ptr(k), f"알 수 없는 매개변수: {k}",
                           ("비슷한 이름: " + ", ".join(near)) if near else "허용: " + ", ".join(sorted(r.params)))
            if r.params[k].kind != kind:
                raise _bad("E_WRONG_KIND", ptr(label[kind], k), f"{k} 는 {r.params[k].kind} 매개변수라 {label[kind]} 에 둘 수 없다",
                           f"{label[other[kind]]} 에 넣으세요")
    for k, p in r.params.items():
        v = given[p.kind].get(k, p.default)
        out[p.kind][k] = coerce_param(k, p, v, ptr(label[p.kind], k))
    return out["type"], out["instance"]


# ---------------------------------------------------------------- 식 평가 도우미
def _ev(raw: Any, env: dict, path: str, want: str = "number") -> Any:
    try:
        v = E.evaluate(E.compile_expr(raw), env) if isinstance(raw, str) else raw
    except E.ExprError as ex:
        raise FamilyError(Issue(ex.code, path, ex.message, ex.hint)) from None
    if want == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise _bad("E_EXPR_TYPE", path, f"숫자가 필요한 곳에 {type(v).__name__} 값이 나왔다: {v!r}", "식이 숫자를 내는지 확인하세요")
        if not math.isfinite(v):
            raise _bad("E_EXPR_MATH", path, "유한하지 않은 수가 나왔다")
        return float(v)
    if want == "bool":
        if not isinstance(v, bool):
            raise _bad("E_EXPR_TYPE", path, f"조건이 필요한 곳에 불리언이 아닌 값이 나왔다: {v!r}", "비교(==, <) 또는 true/false 를 쓰세요")
        return v
    return v


def _num_checked(x: float, path: str) -> float:
    if abs(x) > COORD_MAX:
        raise _bad("E_GEOMETRY_RANGE", path, f"좌표/치수 {x:g} 가 ±{COORD_MAX:g}mm 를 넘는다", "단위(mm) 실수를 확인하세요")
    return x


def build_env(r: Recipe, plan: Plan, tv: dict, iv: dict, host: dict | None) -> dict:
    """매개변수 + 호스트 문맥 + 파생값·참조 평면(위상 순서)을 평가해 환경을 만든다."""
    env: dict[str, Any] = {**tv, **iv}
    if plan.hosted:
        h = {**DEFAULT_HOST, **(host or {})}
        for k in HOST_NAMES:
            env[k] = float(h[k])
    for group, name in plan.order:
        if group == "derived":
            env[name] = _ev(r.derived[name], env, ptr("derived", name), "any")
        else:
            env[name] = _ev(r.refs[name].at, env, ptr("refs", name, "at"))
    return env


def check_constraints(r: Recipe, env: dict) -> None:
    for i, c in enumerate(r.constraints):
        ok = _ev(c.expr, env, ptr("constraints", i, "expr"), "bool")
        if not ok:
            raise _bad("E_CONSTRAINT", ptr("constraints", i), f"제약 위반: {c.message}",
                       "이 값 조합이 허용 범위 안에서 가능하면 매개변수 min/max 를 좁히거나 제약을 고치세요")


# ---------------------------------------------------------------- 형상 해석
def _rect_u(vals, env, path, label):
    a, b = (_num_checked(_ev(v, env, f"{path}/{j}"), f"{path}/{j}") for j, v in enumerate(vals))
    if not b > a:
        raise _bad("E_DEGENERATE", path, f"{label} 의 끝({b:g})이 시작({a:g})보다 커야 한다", "치수가 0 이하가 되는 매개변수 범위를 막으세요")
    return [r6(a), r6(b)]


def _pt2(pt, env, path):
    return [r6(_num_checked(_ev(v, env, f"{path}/{j}"), f"{path}/{j}")) for j, v in enumerate(pt)]


def _area2(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))


def _resolve_extrude(s: Extrude, env: dict, base: str) -> dict:
    p = s.profile
    pp = base + "/profile"
    if p.shape == "rect":
        prof = {"shape": "rect", "u": _rect_u(p.u, env, pp + "/u", "u"), "v": _rect_u(p.v, env, pp + "/v", "v")}
    elif p.shape == "circle":
        r = _num_checked(_ev(p.r, env, pp + "/r"), pp + "/r")
        if r <= 0:
            raise _bad("E_DEGENERATE", pp + "/r", f"원 반지름이 0 이하다({r:g})", "반지름이 양수가 되도록 범위를 막으세요")
        prof = {"shape": "circle", "center": _pt2(p.center, env, pp + "/center"), "r": r6(r)}
    else:
        pts = [_pt2(pt, env, f"{pp}/points/{j}") for j, pt in enumerate(p.points)]
        if abs(_area2(pts)) < 1e-6:
            raise _bad("E_DEGENERATE", pp + "/points", "다각형 넓이가 0 이다(점이 한 직선 위에 있다)")
        prof = {"shape": "polygon", "points": pts}
    a0, a1 = (_num_checked(_ev(v, env, f"{base}/range/{j}"), f"{base}/range/{j}") for j, v in enumerate(s.range))
    if not a1 > a0:
        raise _bad("E_DEGENERATE", base + "/range", f"돌출 범위의 끝({a1:g})이 시작({a0:g})보다 커야 한다",
                   "range 는 돌출축(xy→z, xz→y, yz→x)의 [시작, 끝] 절대 좌표입니다")
    return {"k": "extrude", "plane": s.plane, "profile": prof, "range": [r6(a0), r6(a1)]}


_AX = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def _resolve_sweep(s: Sweep, env: dict, base: str) -> list[dict]:
    pts = []
    for j, pt in enumerate(s.path):
        pts.append([_num_checked(_ev(v, env, f"{base}/path/{j}/{m}"), f"{base}/path/{j}/{m}") for m, v in enumerate(pt)])
    pp = base + "/profile"
    if s.profile.shape == "rect":
        w = _num_checked(_ev(s.profile.w, env, pp + "/w"), pp + "/w")
        h = _num_checked(_ev(s.profile.h, env, pp + "/h"), pp + "/h")
        if w <= 0 or h <= 0:
            raise _bad("E_DEGENERATE", pp, f"스윕 단면 치수가 0 이하다(w={w:g}, h={h:g})")
        prof = {"shape": "rect", "w": r6(w), "h": r6(h)}
        half = w / 2.0
    else:
        rad = _num_checked(_ev(s.profile.r, env, pp + "/r"), pp + "/r")
        if rad <= 0:
            raise _bad("E_DEGENERATE", pp + "/r", f"스윕 단면 반지름이 0 이하다({rad:g})")
        prof = {"shape": "circle", "r": r6(rad)}
        half = rad
    segs = list(zip(pts, pts[1:]))
    if s.closed:
        if len(pts) < 3:
            raise _bad("E_DEGENERATE", base + "/path", "closed 경로는 점이 3개 이상이어야 한다")
        segs.append((pts[-1], pts[0]))
    ax = _AX[s.normal]
    out = []
    n = len(segs)
    for i, (a, b) in enumerate(segs):
        d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
        ln = math.sqrt(sum(c * c for c in d))
        if ln < 1e-6:
            raise _bad("E_DEGENERATE", f"{base}/path/{i}", "경로 구간의 길이가 0 이다", "연속한 두 점이 같지 않게 하세요")
        u = [c / ln for c in d]
        if abs(sum(u[k] * ax[k] for k in range(3))) > 0.999:
            raise _bad("E_SWEEP_NORMAL", f"{base}/path/{i}", f"경로 구간이 normal 축({s.normal})과 평행이라 단면 방향을 정할 수 없다",
                       "normal 을 경로와 수직인 축으로 바꾸세요(벽 면에 놓인 틀은 normal=y)")
        # 꺾이는 점(안쪽 점, 닫힌 경로의 모든 점)에서는 단면 반폭만큼 겹쳐 모서리를 메운다. 열린 경로의 양 끝은 그대로 둔다
        ext0 = half if (s.closed or i > 0) else 0.0
        ext1 = half if (s.closed or i < n - 1) else 0.0
        a2 = [a[k] - u[k] * ext0 for k in range(3)]
        b2 = [b[k] + u[k] * ext1 for k in range(3)]
        out.append({"k": "segment", "start": [r6(c) for c in a2], "end": [r6(c) for c in b2], "normal": s.normal, "profile": prof})
    return out


def resolve_solids(r: Recipe, env: dict) -> tuple[list[dict], list[str]]:
    """형상 목록을 해석한다. when=false 인 형상은 제외, repeat 는 펼치고, void 는 대상 solid 의 cuts 로 붙인다."""
    solids: list[dict] = []     # role=solid 항목 (id, base, ...)
    voids: list[tuple[list[str], dict]] = []
    for i, s in enumerate(r.solids):
        base = ptr("solids", i)
        count, var = 1, None
        if s.repeat:
            c = _ev(s.repeat.count, env, base + "/repeat/count")
            if c != int(c) or not 0 <= c <= MAX_REPEAT:
                raise _bad("E_REPEAT_RANGE", base + "/repeat/count", f"반복 횟수는 0..{MAX_REPEAT} 의 정수여야 한다(받은 값: {c:g})",
                           "횟수를 만드는 매개변수에 min/max 를 두세요")
            count, var = int(c), s.repeat.var
        for idx in range(count):
            e2 = dict(env, **({var: idx} if var else {}))
            if s.when is not None and not _ev(s.when, e2, base + "/when", "bool"):
                continue
            sid = s.id if not s.repeat else f"{s.id}[{idx}]"
            items = [_resolve_extrude(s, e2, base)] if isinstance(s, Extrude) else _resolve_sweep(s, e2, base)
            for j, it in enumerate(items):
                it = dict(it, id=sid if len(items) == 1 else f"{sid}.{j}", base=s.id)
                if s.role == "void":
                    voids.append((s.cuts, it))
                else:
                    solids.append(it)
            if len(solids) + len(voids) > MAX_ITEMS:
                raise _bad("E_LIMIT", base, f"형상이 {MAX_ITEMS}개를 넘는다", "repeat 횟수를 줄이세요")
    for targets, v in voids:
        for sd in solids:
            if sd["base"] in targets:
                sd.setdefault("cuts", []).append({k: v[k] for k in v if k not in ("base",)})
    for sd in solids:
        sd.pop("base", None)
    return solids, sorted({v["base"] for _, v in voids})


def resolve_plan(r: Recipe, env: dict) -> dict:
    out: dict[str, list] = {}
    total = 0
    for scale, prims in r.plan_symbol.items():
        lst = []
        for i, pr in enumerate(prims):
            base = ptr("plan_symbol", scale, i)
            if pr.when is not None and not _ev(pr.when, env, base + "/when", "bool"):
                continue
            t = pr.type
            if t == "line":
                lst.append({"t": "line", "a": _pt2(pr.from_, env, base + "/from"), "b": _pt2(pr.to, env, base + "/to"), "s": pr.style})
            elif t == "arc":
                rad = _ev(pr.radius, env, base + "/radius")
                if rad <= 0:
                    raise _bad("E_DEGENERATE", base + "/radius", f"호 반지름이 0 이하다({rad:g})")
                a0, a1 = _ev(pr.start, env, base + "/start"), _ev(pr.end, env, base + "/end")
                if not a1 > a0 or a1 - a0 > 360:
                    raise _bad("E_DEGENERATE", base, f"호 각도 범위는 start < end <= start+360 (deg) 이어야 한다(start={a0:g}, end={a1:g})",
                               "각도는 도(deg), 반시계 방향이 +")
                lst.append({"t": "arc", "c": _pt2(pr.center, env, base + "/center"), "r": r6(rad), "a0": r6(a0), "a1": r6(a1), "s": pr.style})
            elif t == "circle":
                rad = _ev(pr.radius, env, base + "/radius")
                if rad <= 0:
                    raise _bad("E_DEGENERATE", base + "/radius", f"원 반지름이 0 이하다({rad:g})")
                lst.append({"t": "circle", "c": _pt2(pr.center, env, base + "/center"), "r": r6(rad), "s": pr.style})
            elif t == "rect":
                u = _rect_u(pr.u, env, base + "/u", "u")
                v = _rect_u(pr.v, env, base + "/v", "v")
                lst.append({"t": "polyline", "pts": [[u[0], v[0]], [u[1], v[0]], [u[1], v[1]], [u[0], v[1]]], "closed": True, "s": pr.style})
            else:
                pts = [_pt2(pt, env, f"{base}/points/{j}") for j, pt in enumerate(pr.points)]
                lst.append({"t": "polyline", "pts": pts, "closed": pr.closed, "s": pr.style})
        total += len(lst)
        if total > MAX_PRIMS:
            raise _bad("E_LIMIT", "/plan_symbol", f"평면 기호 요소가 {MAX_PRIMS}개를 넘는다")
        out[scale] = lst
    return out


def _value(raw: Any, env: dict, path: str) -> Any:
    v = _ev(raw.expr if isinstance(raw, ExprVal) else raw, env, path, "any") if isinstance(raw, ExprVal) else raw
    if isinstance(v, float):
        if not math.isfinite(v):
            raise _bad("E_EXPR_MATH", path, "유한하지 않은 수가 나왔다")
        v = r6(v)
    if isinstance(v, str) and len(v) > 255:
        raise _bad("E_VALUE_LIMIT", path, "문자열 값은 255자 이하여야 한다")
    if isinstance(v, int) and not isinstance(v, bool) and abs(v) >= 2**63:
        raise _bad("E_VALUE_LIMIT", path, "정수가 int64 범위를 넘는다")
    return v


# ---------------------------------------------------------------- 전체 평가
def evaluate(r: Recipe, plan: Plan, type_values: dict | None, inst_values: dict | None, host: dict | None = None) -> dict:
    """레시피를 평가한다. 반환은 JSON 직렬화 가능한 순수 값이다."""
    tv, iv = resolve_values(r, type_values, inst_values)
    env = build_env(r, plan, tv, iv, host)
    check_constraints(r, env)
    body, void_ids = resolve_solids(r, env)
    cut = None
    if r.host and r.host.cut:
        cut = {"x": _rect_u(r.host.cut.x, env, "/host/cut/x", "x"), "z": _rect_u(r.host.cut.z, env, "/host/cut/z", "z")}
    plan_sym = resolve_plan(r, env)
    type_attrs = {k: _value(v, env, ptr("ifc", "type_attrs", k)) for k, v in r.ifc.type_attrs.items()}
    attrs = {k: _value(v, env, ptr("ifc", "attrs", k)) for k, v in r.ifc.attrs.items()}
    psets: dict[str, dict] = {"type": {}, "instance": {}}
    for pn, ps in r.psets.items():
        psets[ps.on][pn] = {k: _value(v, env, ptr("psets", pn, "props", k)) for k, v in ps.props.items()}
    return {"type_values": tv, "values": iv, "body": body, "void_ids": void_ids, "cut": cut, "plan": plan_sym,
            "type_attrs": type_attrs, "attrs": attrs, "psets": psets,
            "body_hash": digest(body), "plan_hash": digest(plan_sym.get("default", []))}


def sample_points(r: Recipe) -> list[tuple[dict, dict, dict]]:
    """범위 검사용 표본: 기본값, 각 숫자 매개변수의 min/max, 모든 min/모든 max, 각 열거값, 호스트 두께 변형.
    (타입값, 인스턴스값, 호스트 문맥) 튜플 목록. 조합 폭발을 피하려고 최대 60개로 자른다."""
    def vals(**over):
        tv = {k: p.default for k, p in r.params.items() if p.kind == "type"}
        iv = {k: p.default for k, p in r.params.items() if p.kind == "instance"}
        for k, v in over.items():
            (tv if r.params[k].kind == "type" else iv)[k] = v
        return tv, iv

    out = [(*vals(), {})]
    for k, p in r.params.items():
        if p.type in ("number", "int"):
            for edge in (p.min, p.max):
                if edge is not None and edge != p.default:
                    out.append((*vals(**{k: edge}), {}))
        elif p.type == "enum":
            for v in (p.values or [])[:8]:
                if v != p.default:
                    out.append((*vals(**{k: v}), {}))
        elif p.type == "bool":
            out.append((*vals(**{k: not p.default}), {}))
    nums = [k for k, p in r.params.items() if p.type in ("number", "int") and p.min is not None and p.max is not None]
    mins = {k: r.params[k].min for k in nums}
    maxs = {k: r.params[k].max for k in nums}
    if nums:
        out.append((*vals(**mins), {}))
        out.append((*vals(**maxs), {}))
    if r.host:
        for t in (100.0, 300.0, 600.0):
            out.append((*vals(), {"host_thickness": t}))
    # 쌍별 극단: 한 매개변수는 최소, 다른 하나는 최대(폭은 최소인데 모따기는 최대 같은 조합이 제약을 깨는 경우)
    for a in nums:
        for b in nums:
            if a != b:
                out.append((*vals(**{a: mins[a], b: maxs[b]}), {}))
    return out[:MAX_SAMPLES]
