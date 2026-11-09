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
from math import isfinite
from typing import Callable, NamedTuple

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


def _level_params(p):
    return _check_fields("level", p, {"name": _name, "elevation": num_ok})


def _wall_params(p):
    def axis(v):   # 벽 길이 1mm 이상(wall.create 의 규칙)
        return (isinstance(v, list) and len(v) == 4 and all(num_ok(x) for x in v)
                and (v[2] - v[0]) ** 2 + (v[3] - v[1]) ** 2 >= 1.0)
    return _check_fields("wall", p, {"name": _name, "axis": axis, "thickness": _pos, "height": _pos})


def _door_params(p):
    return _check_fields("door", p, {"name": _name, "width": _pos, "height": _pos, "sill": lambda v: num_ok(v) and v >= 0, "offset": num_ok})


_SCALE = lambda v: num_ok(v) and v > 0   # noqa: E731


def _view_params(p):
    t = p.get("type") if isinstance(p, dict) else None
    if t == "plan":
        return _check_fields("view", p, {"name": _name, "type": lambda v: v == "plan", "level": id_ok, "cut_height": num_ok,
                                         "view_depth": lambda v: num_ok(v) and v >= 0, "scale": _SCALE})
    if t == "section":
        return _check_fields("view", p, {"name": _name, "type": lambda v: v == "section", "p1": _pt_ok, "p2": _pt_ok,
                                         "depth": _pos, "flip": lambda v: type(v) is bool, "scale": _SCALE})
    if t == "elevation":
        return _check_fields("view", p, {"name": _name, "type": lambda v: v == "elevation", "dir": lambda v: v in ("N", "E", "S", "W"),
                                         "scale": _SCALE})
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
    ElementKind("ftype", True, None, True, 3, _ftype_params),
    ElementKind("finst", True, None, True, 4, _finst_params, geometric=True, refs=(("type", "id", "of_type"),)),
    ElementKind("foreign", True, None, False, 5, _foreign_params),
    ElementKind("view", False, None, False, 6, _view_params, refs=(("level", "id", None),), ref_kinds=(("level", ("level",)),)),   # 평면 뷰만 level 이 있다
    ElementKind("dim", False, None, False, 7, _dim_params, refs=(("view", "id", "annot_of"), ("a", "ref", None), ("b", "ref", None)),
                ref_kinds=(("view", ("view",)),)),
    ElementKind("tag", False, None, False, 7, _tag_params, refs=(("view", "id", "annot_of"), ("target", "id", None)),
                ref_kinds=(("view", ("view",)), ("target", ("wall", "door")))),
)}

NON_IFC_KINDS = frozenset(k for k, v in ELEMENT_KINDS.items() if not v.ifc)      # 신원·속성셋·재료가 없는 의도 요소(뷰 정의, 치수·태그)
PROJECTED_KINDS = tuple(k.name for k in sorted(ELEMENT_KINDS.values(), key=lambda k: (k.order, k.name)) if k.projected)
FIXED_IFC_CLASS = {k: v.ifc_class for k, v in ELEMENT_KINDS.items() if v.ifc_class}   # 코어가 직접 정하는 IFC 클래스
KIND_NAMES = tuple(ELEMENT_KINDS)
# 의도 복원(S2 가져오기)이 IFC 클래스로 종류를 정하는 것: IFC 클래스 -> 종류. 패밀리 타입·인스턴스(ftype/finst)는 클래스가 레시피마다 달라 의도가 스스로 밝힌
# 종류를 따른다(family.restore, ADR-0014). 뷰·주석은 IFC 신원이 없어 IFC 에서 복원할 수 없다(알려진 한계)
IMPORTABLE_BY_CLASS = {"IfcBuildingStorey": "level", "IfcWall": "wall", "IfcDoor": "door"}
FAMILY_OF_KIND = {"level": "level.basic", "wall": "wall.basic", "door": "door.single"}


def _remap_ref(form: str, v, m: dict):
    """참조 값 하나에서 id 를 m(옛 id -> 새 id)으로 바꾼다. 모양이 다른 값은 그대로 둔다(검사는 다른 곳의 일이다)."""
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
    RelKind("on_level", _fs("wall", "finst", "foreign"), _fs("level"), guard=("src",), violation="LEVEL_DELETED", message="벽이 놓인 레벨이 지워졌다"),
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
