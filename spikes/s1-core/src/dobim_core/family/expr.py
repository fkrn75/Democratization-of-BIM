# SPDX-License-Identifier: MPL-2.0
"""패밀리 레시피의 안전한 식 언어 (ADR-0008: 산술·조건만, 입출력·임의 코드 실행 없음).

`eval`/`exec`/`compile` 을 쓰지 않는다. 파이썬 `ast` 로 구문만 분석한 뒤, 허용 목록에 있는 노드만 직접 해석한다.

허용 문법
- 숫자(1, 2.5), 문자열('L', "R"), 불리언(true, false, True, False)
- 이름(매개변수·파생값·참조 평면·호스트 문맥·반복 변수)
- 산술 `+ - * / // % **`, 단항 `- +`
- 비교 `== != < <= > >=`(연쇄 가능), 논리 `and or not`
- 조건식 `a if 조건 else b`
- 함수 `min max abs round floor ceil sqrt clamp` (위치 인자만)
삼각함수·지수·로그는 의도적으로 뺐다: 플랫폼 libm 에 따라 마지막 자리가 달라지면 파생 해시가 OS 마다 흔들린다(ADR-0006).
각도가 필요하면 매개변수(deg)로 받는다.

자원 상한: 식 길이 300자, 노드 120개, 거듭제곱 지수 -8..8, 결과는 유한한 수여야 한다.
"""
from __future__ import annotations

import ast
import difflib
import math
from dataclasses import dataclass
from typing import Any

MAX_LEN = 300
MAX_NODES = 120
FUNCS = ("min", "max", "abs", "round", "floor", "ceil", "sqrt", "clamp")
CONSTS = {"true": True, "false": False, "True": True, "False": False}
_BINOPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**"}
_CMPOPS = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}
_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Call, ast.Name,
            ast.Constant, ast.Load, ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or, *tuple(_BINOPS), *tuple(_CMPOPS))


class ExprError(Exception):
    """식 구문·평가 오류. code 는 E_EXPR_* 계열이다."""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


@dataclass(frozen=True)
class Compiled:
    src: str
    tree: ast.Expression
    names: frozenset[str]


_CACHE: dict[str, Compiled] = {}


def compile_expr(src: str) -> Compiled:
    """식을 분석한다. 허용하지 않는 문법·함수는 여기서 거절하므로 평가 전에 정적 검사가 가능하다."""
    if not isinstance(src, str):
        raise ExprError("E_EXPR_TYPE", "식은 문자열이어야 한다")
    hit = _CACHE.get(src)
    if hit is not None:
        return hit
    if len(src) > MAX_LEN:
        raise ExprError("E_EXPR_LIMIT", f"식이 {MAX_LEN}자를 넘는다({len(src)}자)", "파생값으로 나눠 쓰세요")
    try:
        tree = ast.parse(src.strip(), mode="eval")
    except SyntaxError as ex:
        raise ExprError("E_EXPR_SYNTAX", f"식 구문 오류: {ex.msg} (열 {ex.offset})",
                        "파이썬 식 문법의 부분집합입니다. 조건은 `a if 조건 else b`, 논리는 and/or/not") from None
    except (ValueError, RecursionError, MemoryError):
        raise ExprError("E_EXPR_SYNTAX", "식을 해석할 수 없다") from None
    names: set[str] = set()
    n = 0
    call_targets = {id(p.func) for p in ast.walk(tree) if isinstance(p, ast.Call)}
    for node in ast.walk(tree):
        n += 1
        if n > MAX_NODES:
            raise ExprError("E_EXPR_LIMIT", f"식 노드가 {MAX_NODES}개를 넘는다", "파생값으로 나눠 쓰세요")
        if not isinstance(node, _ALLOWED):
            raise ExprError("E_EXPR_FORBIDDEN", f"허용하지 않는 문법: {type(node).__name__}",
                            "허용: 산술, 비교, and/or/not, 조건식, 함수 " + ", ".join(FUNCS))
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, str, bool)):
            raise ExprError("E_EXPR_FORBIDDEN", f"허용하지 않는 상수: {type(node.value).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords:
                raise ExprError("E_EXPR_FUNC", "허용하지 않는 함수 호출이다(위치 인자로 허용 목록 함수만 쓴다)",
                                "허용 함수: " + ", ".join(FUNCS))
        if isinstance(node, ast.Name):
            if node.id.startswith("_"):
                raise ExprError("E_EXPR_FORBIDDEN", f"밑줄로 시작하는 이름은 쓸 수 없다: {node.id}")
            if node.id in FUNCS:
                if id(node) not in call_targets:
                    raise ExprError("E_EXPR_FUNC", f"함수 이름 {node.id} 은 호출로만 쓸 수 있다")
            elif node.id not in CONSTS:
                names.add(node.id)
    c = Compiled(src, tree, frozenset(names))
    if len(_CACHE) < 4096:
        _CACHE[src] = c
    return c


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _kind(v: Any) -> str:
    return "bool" if isinstance(v, bool) else "number" if _num(v) else "text"


def _finite(x: Any, what: str) -> Any:
    if isinstance(x, float) and not math.isfinite(x):
        raise ExprError("E_EXPR_MATH", f"{what} 결과가 유한하지 않다")
    if isinstance(x, int) and abs(x) > 10**15:
        raise ExprError("E_EXPR_LIMIT", f"{what} 결과가 너무 크다")
    return x


def evaluate(c: Compiled, env: dict[str, Any]) -> Any:
    """환경(이름→값)에서 식을 평가한다. 값은 int/float/bool/str 이다."""
    try:
        return _ev(c.tree.body, env)
    except ExprError:
        raise
    except (OverflowError, ZeroDivisionError) as ex:   # 방어: 아래에서 먼저 검사하지만 예상 밖 경로를 구조화 오류로 바꾼다
        raise ExprError("E_EXPR_MATH", f"수식 계산 오류: {type(ex).__name__}") from None


def _ev(n: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(n, ast.Constant):
        return n.value
    if isinstance(n, ast.Name):
        if n.id in CONSTS:
            return CONSTS[n.id]
        if n.id not in env:
            near = difflib.get_close_matches(n.id, list(env), n=3)
            raise ExprError("E_EXPR_UNDEFINED", f"정의되지 않은 이름: {n.id}",
                            ("비슷한 이름: " + ", ".join(near)) if near else "params/derived/refs 에 선언하세요")
        return env[n.id]
    if isinstance(n, ast.UnaryOp):
        v = _ev(n.operand, env)
        if isinstance(n.op, ast.Not):
            if not isinstance(v, bool):
                raise ExprError("E_EXPR_TYPE", "not 의 피연산자는 불리언이어야 한다")
            return not v
        if not _num(v):
            raise ExprError("E_EXPR_TYPE", "단항 +/- 의 피연산자는 숫자여야 한다")
        return -v if isinstance(n.op, ast.USub) else v
    if isinstance(n, ast.BinOp):
        a, b = _ev(n.left, env), _ev(n.right, env)
        op = _BINOPS[type(n.op)]
        if not (_num(a) and _num(b)):
            raise ExprError("E_EXPR_TYPE", f"{op} 의 피연산자는 숫자여야 한다(받은 값: {_kind(a)}, {_kind(b)})")
        if op in ("/", "//", "%") and b == 0:
            raise ExprError("E_EXPR_MATH", "0 으로 나눌 수 없다", "분모가 0 이 되는 매개변수 범위를 min/max 로 막으세요")
        if op == "**":
            if not -8 <= b <= 8:
                raise ExprError("E_EXPR_LIMIT", "거듭제곱 지수는 -8..8 이어야 한다")
            if a == 0 and b < 0:
                raise ExprError("E_EXPR_MATH", "0 의 음수 거듭제곱")
            if a < 0 and b != int(b):
                raise ExprError("E_EXPR_MATH", "음수의 비정수 거듭제곱")
        r = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: a / b,
             "//": lambda: a // b, "%": lambda: a % b, "**": lambda: a ** b}[op]()
        return _finite(r, op)
    if isinstance(n, ast.BoolOp):
        vals = []
        for v_node in n.values:
            v = _ev(v_node, env)
            if not isinstance(v, bool):
                raise ExprError("E_EXPR_TYPE", "and/or 의 피연산자는 불리언이어야 한다")
            vals.append(v)
        return all(vals) if isinstance(n.op, ast.And) else any(vals)
    if isinstance(n, ast.Compare):
        left = _ev(n.left, env)
        for op, comp in zip(n.ops, n.comparators):
            right = _ev(comp, env)
            sym = _CMPOPS[type(op)]
            if _kind(left) != _kind(right):
                raise ExprError("E_EXPR_TYPE", f"{sym} 로 서로 다른 종류를 비교했다({_kind(left)} 와 {_kind(right)})")
            if sym in ("<", "<=", ">", ">=") and not _num(left):
                raise ExprError("E_EXPR_TYPE", f"{sym} 는 숫자끼리만 비교한다")
            if sym == "==":
                ok = left == right
            elif sym == "!=":
                ok = left != right
            elif sym == "<":
                ok = left < right
            elif sym == "<=":
                ok = left <= right
            elif sym == ">":
                ok = left > right
            else:
                ok = left >= right
            if not ok:
                return False
            left = right
        return True
    if isinstance(n, ast.IfExp):
        t = _ev(n.test, env)
        if not isinstance(t, bool):
            raise ExprError("E_EXPR_TYPE", "조건식의 조건은 불리언이어야 한다")
        return _ev(n.body if t else n.orelse, env)
    if isinstance(n, ast.Call):
        args = [_ev(a, env) for a in n.args]
        name = n.func.id   # compile_expr 가 허용 목록 이름임을 보장한다
        if not all(_num(a) for a in args):
            raise ExprError("E_EXPR_TYPE", f"{name}() 의 인자는 숫자여야 한다")
        arity = {"min": (1, 8), "max": (1, 8), "abs": (1, 1), "round": (1, 2), "floor": (1, 1), "ceil": (1, 1),
                 "sqrt": (1, 1), "clamp": (3, 3)}[name]
        if not arity[0] <= len(args) <= arity[1]:
            raise ExprError("E_EXPR_FUNC", f"{name}() 인자 개수가 맞지 않는다(필요 {arity[0]}~{arity[1]}, 받음 {len(args)})")
        if name == "min":
            return min(args)
        if name == "max":
            return max(args)
        if name == "abs":
            return abs(args[0])
        if name == "round":
            if len(args) == 2 and args[1] != int(args[1]):
                raise ExprError("E_EXPR_TYPE", "round() 의 자릿수는 정수여야 한다")
            return round(args[0], int(args[1])) if len(args) == 2 else int(round(args[0]))
        if name == "floor":
            return int(math.floor(args[0]))
        if name == "ceil":
            return int(math.ceil(args[0]))
        if name == "sqrt":
            if args[0] < 0:
                raise ExprError("E_EXPR_MATH", "sqrt() 의 인자가 음수다")
            return math.sqrt(args[0])
        x, lo, hi = args   # clamp(x, lo, hi)
        if lo > hi:
            raise ExprError("E_EXPR_MATH", "clamp() 의 하한이 상한보다 크다")
        return max(lo, min(hi, x))
    raise ExprError("E_EXPR_FORBIDDEN", f"허용하지 않는 문법: {type(n).__name__}")   # compile_expr 가 이미 걸러 도달하지 않는다
