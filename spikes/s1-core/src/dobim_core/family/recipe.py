# SPDX-License-Identifier: MPL-2.0
"""패밀리 레시피 스키마(pydantic)와 구조화된 오류. ifcopenshell 을 import 하지 않는다.

레시피는 JSON 텍스트다(ADR-0008). 여기서는 *구조*만 본다. 식의 이름 해석·순환·범위·형상은 analyze.py / evaluate.py,
IFC 클래스·열거값·표준 속성셋은 validate.py 가 본다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError, model_validator

SCHEMA_ID = "dobim.family/1"
MAX_RECIPE_BYTES = 64 * 1024
NAME_RE = r"^[a-z][a-z0-9_]{0,31}$"
ID_RE = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
RESERVED_NAMES = {"host_thickness", "host_height", "true", "false", "min", "max", "abs", "round", "floor", "ceil",
                  "sqrt", "clamp"}
HOST_NAMES = ("host_thickness", "host_height")


# ---------------------------------------------------------------- 구조화된 오류
@dataclass
class Issue:
    code: str
    path: str
    message: str
    hint: str | None = None
    level: str = "error"      # error | warning

    def to_dict(self) -> dict:
        d = {"level": self.level, "code": self.code, "path": self.path, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


class FamilyError(Exception):
    """레시피 또는 값이 틀렸다. issues 는 경로·이유·힌트를 가진 구조화 오류 목록이다."""

    def __init__(self, issues: list[Issue] | Issue):
        self.issues = [issues] if isinstance(issues, Issue) else list(issues)
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in self.issues[:3]))


def ptr(*parts) -> str:
    """JSON 포인터 문자열 (/params/width/default)."""
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts) if parts else ""


# ---------------------------------------------------------------- 필드 타입
Ex = Union[StrictInt, StrictFloat, StrictStr]            # 숫자 또는 식(문자열)
When = Union[StrictBool, StrictStr, None]                # 표시 조건: 불리언 또는 식
Scalar = Union[StrictBool, StrictInt, StrictFloat, StrictStr]
Pt2 = Annotated[list[Ex], Field(min_length=2, max_length=2)]
Pt3 = Annotated[list[Ex], Field(min_length=3, max_length=3)]
Pair = Annotated[list[Ex], Field(min_length=2, max_length=2)]
Axis = Literal["x", "y", "z"]


class M(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExprVal(M):
    expr: StrictStr


Value = Union[StrictBool, StrictInt, StrictFloat, StrictStr, ExprVal]   # 속성셋·IFC 속성 값: 리터럴 또는 {"expr": 식}


class Param(M):
    kind: Literal["type", "instance"]
    type: Literal["number", "int", "bool", "enum", "text"] = "number"
    unit: Literal["mm", "deg", "none"] | None = None
    default: Scalar
    min: Union[StrictInt, StrictFloat, None] = None
    max: Union[StrictInt, StrictFloat, None] = None
    values: list[StrictStr] | None = Field(None, max_length=32)
    description: StrictStr = Field("", max_length=200)

    @model_validator(mode="after")
    def _shape(self):
        t = self.type
        unit = self.unit
        if t == "number":
            unit = unit or "mm"
        elif unit not in (None, "none"):
            raise ValueError(f"type={t} 인 매개변수의 unit 은 none 이어야 한다(받은 값: {unit})")
        else:
            unit = "none"
        self.unit = unit
        d = self.default
        if t == "enum":
            if not self.values:
                raise ValueError("enum 매개변수는 values(허용 값 목록)가 필요하다")
            if len(set(self.values)) != len(self.values):
                raise ValueError("values 에 중복이 있다")
            if d not in self.values:
                raise ValueError(f"default 가 values 에 없다: {d!r}")
        elif self.values is not None:
            raise ValueError("values 는 type=enum 에서만 쓴다")
        if t in ("number", "int"):
            if isinstance(d, (bool, str)):
                raise ValueError(f"type={t} 의 default 는 숫자여야 한다")
            if t == "int" and d != int(d):
                raise ValueError("type=int 의 default 는 정수여야 한다")
            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError("min 이 max 보다 크다")
            if self.min is not None and d < self.min or self.max is not None and d > self.max:
                raise ValueError(f"default({d}) 가 범위 [{self.min}, {self.max}] 밖이다")
        else:
            if self.min is not None or self.max is not None:
                raise ValueError("min/max 는 number/int 에서만 쓴다")
            if t == "bool" and not isinstance(d, bool):
                raise ValueError("type=bool 의 default 는 true/false 여야 한다")
            if t == "text" and not isinstance(d, str):
                raise ValueError("type=text 의 default 는 문자열이어야 한다")
            if t == "text" and len(d) > 255:
                raise ValueError("text 기본값은 255자 이하여야 한다")
        return self


class Ref(M):
    axis: Axis
    at: Ex


class Constraint(M):
    expr: StrictStr
    message: StrictStr = Field(max_length=200)


class Cut(M):
    x: Pair
    z: Pair


class Host(M):
    kinds: list[Literal["wall"]] = Field(min_length=1, max_length=1)
    cut: Cut | None = None


class IfcSpec(M):
    class_: StrictStr = Field(alias="class", pattern=r"^Ifc[A-Za-z0-9]+$")
    type_class: StrictStr | None = Field(None, pattern=r"^Ifc[A-Za-z0-9]+Type$")
    predefined: StrictStr = "NOTDEFINED"
    type_attrs: dict[str, Value] = Field(default_factory=dict)
    attrs: dict[str, Value] = Field(default_factory=dict)


class PsetSpec(M):
    on: Literal["type", "instance"]
    props: dict[str, Value] = Field(min_length=1)


# ---------------------------------------------------------------- 형상
class RectProfile(M):
    shape: Literal["rect"]
    u: Pair
    v: Pair


class CircleProfile(M):
    shape: Literal["circle"]
    center: Pt2
    r: Ex


class PolygonProfile(M):
    shape: Literal["polygon"]
    points: list[Pt2] = Field(min_length=3, max_length=32)


Profile = Annotated[Union[RectProfile, CircleProfile, PolygonProfile], Field(discriminator="shape")]


class Repeat(M):
    var: StrictStr = Field(pattern=NAME_RE)
    count: Ex


class SolidBase(M):
    id: StrictStr = Field(pattern=NAME_RE)
    role: Literal["solid", "void"] = "solid"
    cuts: list[StrictStr] = Field(default_factory=list, max_length=16)
    when: When = None
    repeat: Repeat | None = None

    @model_validator(mode="after")
    def _roles(self):
        if self.role == "void" and not self.cuts:
            raise ValueError("role=void 인 형상은 cuts(자를 대상 solid id 목록)가 필요하다")
        if self.role == "solid" and self.cuts:
            raise ValueError("cuts 는 role=void 에서만 쓴다")
        return self


class Extrude(SolidBase):
    op: Literal["extrude"]
    plane: Literal["xy", "xz", "yz"]
    profile: Profile
    range: Pair


class SweepRect(M):
    shape: Literal["rect"]
    w: Ex
    h: Ex


class SweepCircle(M):
    shape: Literal["circle"]
    r: Ex


SweepProfile = Annotated[Union[SweepRect, SweepCircle], Field(discriminator="shape")]


class Sweep(SolidBase):
    op: Literal["sweep"]
    path: list[Pt3] = Field(min_length=2, max_length=16)
    closed: StrictBool = False
    normal: Axis = "y"
    profile: SweepProfile


Solid = Annotated[Union[Extrude, Sweep], Field(discriminator="op")]


# ---------------------------------------------------------------- 평면 기호
Style = Literal["cut", "projection", "hidden"]


class PrimBase(M):
    when: When = None
    style: Style = "projection"


class PLine(PrimBase):
    type: Literal["line"]
    from_: Pt2 = Field(alias="from")
    to: Pt2


class PArc(PrimBase):
    type: Literal["arc"]
    center: Pt2
    radius: Ex
    start: Ex
    end: Ex


class PCircle(PrimBase):
    type: Literal["circle"]
    center: Pt2
    radius: Ex


class PRect(PrimBase):
    type: Literal["rect"]
    u: Pair
    v: Pair


class PPolyline(PrimBase):
    type: Literal["polyline"]
    points: list[Pt2] = Field(min_length=2, max_length=32)
    closed: StrictBool = False


Prim = Annotated[Union[PLine, PArc, PCircle, PRect, PPolyline], Field(discriminator="type")]


# ---------------------------------------------------------------- 최상위
class Recipe(M):
    schema_: Literal["dobim.family/1"] = Field(alias="schema")
    id: StrictStr = Field(pattern=ID_RE, max_length=64)
    version: StrictInt = Field(ge=1, le=9999)
    name: StrictStr = Field(min_length=1, max_length=64)
    description: StrictStr = Field("", max_length=400)
    ifc: IfcSpec
    host: Host | None = None
    params: dict[str, Param] = Field(min_length=1, max_length=64)
    derived: dict[str, Ex] = Field(default_factory=dict, max_length=64)
    refs: dict[str, Ref] = Field(default_factory=dict, max_length=32)
    constraints: list[Constraint] = Field(default_factory=list, max_length=32)
    solids: list[Solid] = Field(min_length=1, max_length=64)
    plan_symbol: dict[str, list[Prim]] = Field(min_length=1, max_length=4)
    psets: dict[str, PsetSpec] = Field(default_factory=dict, max_length=8)

    @model_validator(mode="after")
    def _names(self):
        for group, d in (("params", self.params), ("derived", self.derived), ("refs", self.refs)):
            for k in d:
                if not re.match(NAME_RE, k):
                    raise ValueError(f"{group} 이름 형식 오류: {k!r} (소문자로 시작, 소문자·숫자·밑줄, 32자 이하)")
        return self


# ---------------------------------------------------------------- 구문 분석
_HINTS = {
    "missing": "필수 키입니다. SPEC.md 의 해당 절을 확인하세요",
    "extra_forbidden": "알 수 없는 키입니다. 철자와 허용 키를 SPEC.md 에서 확인하세요(대소문자 구분)",
    "literal_error": "허용되지 않는 값입니다",
    "string_pattern_mismatch": "이름·id 형식을 지키세요(소문자 시작, 소문자·숫자·밑줄)",
    "union_tag_invalid": "구분 키(op/shape/type) 값이 허용 목록에 없습니다",
    "union_tag_not_found": "구분 키(op/shape/type)가 없습니다",
}


_TAGS = {"extrude", "sweep", "rect", "circle", "polygon", "line", "arc", "polyline"}
_WRAP = {"str", "int", "float", "bool", "ExprVal"}


def _clean_loc(loc) -> list:
    """pydantic 의 유니온 구분 값(extrude/rect/...)과 Strict 타입 이름을 경로에서 뺀다."""
    out: list = []
    for p in loc:
        if isinstance(p, str) and p.startswith("function-after["):
            continue
        if isinstance(p, str) and p in _TAGS and out and (isinstance(out[-1], int) or out[-1] == "profile"):
            continue
        out.append(p)
    # 끝에 붙는 유니온 가지 이름(str/int/float/bool/ExprVal)을 뗀다
    while out and isinstance(out[-1], str) and out[-1] in _WRAP:
        out.pop()
    return out


def pydantic_issues(ex: ValidationError) -> list[Issue]:
    out = []
    for e in ex.errors():
        msg = str(e["msg"])
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        elif e["type"] == "missing":
            msg = "필수 키가 없다"
        elif e["type"] == "extra_forbidden":
            msg = "알 수 없는 키다"
        elif e["type"] in ("literal_error", "enum"):
            msg = "허용되지 않는 값이다: " + msg.replace("Input should be ", "허용 값 ")
        elif e["type"].endswith("_type") or e["type"].endswith("_parsing"):
            msg = f"값의 종류가 맞지 않는다({msg})"
        elif e["type"] in ("string_too_short", "too_short", "string_too_long", "too_long", "greater_than_equal", "less_than_equal", "greater_than", "less_than"):
            msg = f"길이/크기 범위를 벗어났다({msg})"
        out.append(Issue("E_SCHEMA", ptr(*_clean_loc(e["loc"])), msg, _HINTS.get(e["type"])))
    # 같은 경로의 중복 오류(유니온 가지별)를 합친다
    seen, uniq = set(), []
    for i in out:
        k = (i.path, i.message)
        if k not in seen:
            seen.add(k)
            uniq.append(i)
    return uniq


def parse_recipe(data: dict) -> Recipe:
    """딕셔너리를 Recipe 로 바꾼다. 실패하면 FamilyError(E_SCHEMA...)."""
    if not isinstance(data, dict):
        raise FamilyError(Issue("E_SCHEMA", "", "레시피는 JSON 객체여야 한다"))
    try:
        size = len(json.dumps(data, ensure_ascii=False))
    except (TypeError, ValueError):
        raise FamilyError(Issue("E_SCHEMA", "", "레시피를 JSON 으로 직렬화할 수 없다")) from None
    if size > MAX_RECIPE_BYTES:
        raise FamilyError(Issue("E_LIMIT", "", f"레시피가 {MAX_RECIPE_BYTES // 1024}KB 를 넘는다({size}자)",
                                "반복(repeat)과 파생값으로 줄이세요"))
    try:
        return Recipe.model_validate(data)
    except ValidationError as ex:
        raise FamilyError(pydantic_issues(ex)) from None


def load_json_text(text: str) -> dict:
    """JSON 텍스트를 읽는다. 오류는 위치(줄·열)가 있는 구조화 오류로 낸다."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as ex:
        raise FamilyError(Issue("E_JSON", "", f"JSON 구문 오류: {ex.msg} ({ex.lineno}줄 {ex.colno}열)",
                                "주석·후행 쉼표·작은따옴표 키는 JSON 에서 허용되지 않는다")) from None
    if not isinstance(data, dict):
        raise FamilyError(Issue("E_JSON", "", "최상위가 JSON 객체여야 한다"))
    return data
