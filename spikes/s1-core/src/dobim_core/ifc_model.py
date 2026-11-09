# SPDX-License-Identifier: MPL-2.0
"""IFC 쪽 상태와 투영 (ADR-0005).

정본 분담:
- IFC 가 정본인 것: 속성셋, 재료. 이 모듈이 ifcopenshell.api 로만 고친다.
- 파생물: 저작 요소의 형상·배치. spec(순수 값)에서만 만들고 직접 편집 경로는 없다.
- 요소의 신원(GlobalId + 클래스)은 요소 생성 즉시 만들고, 형상·관계 투영은 flush 에서 늦게 한다.

이 모듈만 ifcopenshell.file 을 수정한다. 커맨드는 엔진의 효과(effect)를 통해서만 여기에 닿는다.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
from typing import Any

import ifcopenshell
import ifcopenshell.api.aggregate as api_aggregate
import ifcopenshell.api.context as api_context
import ifcopenshell.api.feature as api_feature
import ifcopenshell.api.material as api_material
import ifcopenshell.api.project as api_project
import ifcopenshell.api.pset as api_pset
import ifcopenshell.api.root as api_root
import ifcopenshell.api.spatial as api_spatial
import ifcopenshell.api.unit as api_unit
import ifcopenshell.guid
import ifcopenshell.util.element as ue
import ifcopenshell.util.unit as uunit
import ifcopenshell.validate

from dobim_core.errors import DobimError
from dobim_core.kinds import FIXED_IFC_CLASS
from dobim_core.family import ifcproj as fp

def _rels_of(e, inv, attr: str, rel_cls: str, rel_attr: str) -> list:
    """e 가 rel_attr(관계의 대상 목록)에 들어 있는 rel_cls 관계들. IFC 의 역속성(attr)을 쓰면 엔진이 인덱스로 찾는다.
    `e in r.RelatedObjects` 는 관계의 대상 목록 전체를 훑어서, 같은 관계에 요소가 N개 달리면(한 층에 포함된 요소 전부) 요소마다 O(N) 이라
    가져오기가 이차 시간이 된다. 역속성이 없는 클래스(공간 요소 등)만 예전 방식으로 거른다."""
    try:
        rels = getattr(e, attr)
    except AttributeError:
        return [r for r in inv if r.is_a(rel_cls) and e in getattr(r, rel_attr)]
    return [r for r in (rels or ()) if r.is_a(rel_cls)]


def check_value(value) -> None:
    """IFC 에 안전하게 저장할 수 없는 속성 값을 쓰기 전에 거른다(NaN, inf, int64 초과, NUL, 홑서로게이트)."""
    bad = None
    if isinstance(value, float) and not math.isfinite(value):
        bad = "NaN/inf 는 저장할 수 없다"
    elif isinstance(value, int) and not isinstance(value, bool) and not -2**63 <= value < 2**63:
        bad = "정수는 int64 범위여야 한다"
    elif isinstance(value, str) and ("\x00" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value)):
        bad = "문자열에 NUL 이나 홑서로게이트를 넣을 수 없다"
    if bad:
        raise DobimError("CONSTRAINT", bad, path="/value")


INTENT_PSET = "DoBIM_Intent"   # 투영이 소유하는 예약 속성셋. 정본(의도 저장소)에서 매번 다시 쓴다
IFC_CLASS = FIXED_IFC_CLASS   # 코어가 직접 정하는 종류의 IFC 클래스(정본은 kinds.py). 패밀리·외래 요소는 행의 ifc_class 를 쓴다
_NS = uuid.UUID("5d0b1a50-0000-4a00-8000-0d0b1a5000b1")


def derived_guid(project_guid: str, label: str) -> str:
    """파생 엔티티의 결정적 GlobalId (ADR-0006 불변식: 시각·난수 금지)."""
    return ifcopenshell.guid.compress(uuid.uuid5(_NS, f"{project_guid}/{label}").hex)


def open_ifc_bytes(data: bytes):
    """IFC 바이트를 파일로 연다. 임시 파일은 열자마자 지운다(외래 파일은 비공개일 수 있다)."""
    fd, name = tempfile.mkstemp(suffix=".ifc")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return ifcopenshell.open(name)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def plain(v):
    """속성 값을 JSON 으로 안전하게 직렬화되는 값으로 바꾼다(외래 파일의 열거·참조·표 값 등). 표현할 수 없으면 문자열."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, ifcopenshell.entity_instance):
        return f"#{v.is_a()}"
    return str(v)


def material_label(m) -> str | None:
    """재료 지정의 이름표. 외래 파일은 층 구성·프로파일 같은 복합 재료를 쓰므로 종류별로 이름을 고른다."""
    if m is None:
        return None
    if m.is_a("IfcMaterial"):
        return m.Name
    inner = getattr(m, "ForLayerSet", None) or getattr(m, "ForProfileSet", None) or m
    for attr in ("LayerSetName", "Name"):
        try:
            v = getattr(inner, attr)
        except AttributeError:
            continue
        if v:
            return v
    return inner.is_a()


def skeleton_check(f):
    """코어가 직접 만드는 뼈대(IfcProject "dobim" > IfcSite "Site" > IfcBuilding "Building")를 구조로 알아본다. (뼈대 | None, 경고 | None).
    인식 기준은 구조(프로젝트·사이트·건물이 각각 하나이고 집합 사슬이 이어짐), 이름, 형상 없음, 항등 배치다.
    위경도·주소·설명·긴 이름 같은 **비형상 속성은 보지 않는다**: 외부 도구가 사이트에 위경도만 적어도 뼈대는 뼈대다(요소 분류에 영향이 없다).
    형상(표현·배치)이 바뀐 뼈대는 그대로 두면 아래 요소의 배치 기준이 달라지므로 보수적으로 뼈대로 보지 않고(사이트·건물은 외래 요소가 되고
    저작 요소는 분리된다) 그 사실을 경고로 알린다. GlobalId 는 프로젝트 UUID 에서 파생돼 파일마다 달라 기준으로 쓸 수 없다."""
    projs, sites, blds = f.by_type("IfcProject"), f.by_type("IfcSite"), f.by_type("IfcBuilding")
    if len(projs) != 1 or len(sites) != 1 or len(blds) != 1:
        return None, None
    proj, site, bld = projs[0], sites[0], blds[0]
    if proj.Name != "dobim" or site.Name != "Site" or bld.Name != "Building":
        return None, None
    if ue.get_aggregate(site) != proj or ue.get_aggregate(bld) != site:
        return None, None

    def identity(pl, rel_to):
        if pl is None or pl.PlacementRelTo != rel_to:
            return False
        a = pl.RelativePlacement
        try:
            return (tuple(a.Location.Coordinates) == (0.0, 0.0, 0.0) and tuple(a.Axis.DirectionRatios) == (0.0, 0.0, 1.0)
                    and tuple(a.RefDirection.DirectionRatios) == (1.0, 0.0, 0.0))
        except (AttributeError, TypeError):
            return False

    if (site.Representation is not None or bld.Representation is not None
            or not identity(site.ObjectPlacement, None) or not identity(bld.ObjectPlacement, site.ObjectPlacement)):
        return None, ("뼈대(Site/Building)의 형상(배치 또는 표현)이 바뀌었다: 뼈대로 보지 않고 사이트·건물을 외래 요소로 둔다. "
                      "뼈대에 매달린 요소(레벨)는 분리된다(동결)")
    return (site, bld), None


def detect_skeleton(f):
    """skeleton_check 의 (site, building) 만. 뼈대가 아니면 None: 외래 파일의 사이트·건물은 외래 요소다."""
    return skeleton_check(f)[0]


# 요소 클로저·의미 해시가 쓰는 정규화(make_norm)와 클로저 구성의 형식 번호. 정규화나 클로저 구성을 바꾸면 올린다.
# 가져올 때 저장한 element.projection_hash 가 이 형식의 해시이므로, 열 때 번호가 다르면 가져온 프로젝트를 다시 판정한다(Engine._rejudge_import).
#   "1" S2 스파이크(IfcProduct 만 참조로 접는다)   "2" 통합(IfcTypeObject 도 참조로 접는다: 패밀리 타입, S4)
CLOSURE_FORMAT = "2"


def make_norm():
    """STEP 번호·소유 이력을 뺀 정규화 함수 (val, ent). 의미 해시(semantic_dump)와 요소 클로저가 같은 규칙을 쓴다."""
    memo: dict[int, Any] = {}

    def val(v):
        if isinstance(v, ifcopenshell.entity_instance):
            return ent(v)
        if isinstance(v, (tuple, list)):
            out = [val(x) for x in v]
            # 관계의 대상 목록은 집합이다. 삽입 순서가 해시에 새면 안 된다.
            if out and all(isinstance(x, dict) and set(x) == {"ref"} for x in out):
                out.sort(key=lambda x: x["ref"])
            return out
        if isinstance(v, float):
            r = round(v, 6) + 0.0
            return r
        return v

    def ent(e):
        if e.is_a("IfcProduct") or e.is_a("IfcTypeObject"):
            return {"ref": e.GlobalId}
        k = e.id()   # 0 이면 파일에 독립 번호가 없는 값 래퍼(IfcLabel 등)다. 캐시하면 값이 섞인다
        if k and k in memo:
            return memo[k]
        info = e.get_info(include_identifier=False, recursive=False)
        out = {"t": info.pop("type")}
        for name, v in info.items():
            if name in ("GlobalId", "OwnerHistory"):
                continue
            out[name] = val(v)
        for setattr_ in ("HasProperties", "Units"):  # IFC 에서 집합인 속성: 순서는 부산물이다
            if setattr_ in out:
                out[setattr_].sort(key=lambda x: json.dumps(x, sort_keys=True))
        if k:
            memo[k] = out
        return out

    return val, ent


class Closure:
    """요소 하나의 투영 클로저(ADR-0005 변조 감지용): 투영이 소유하는 부분만 정규화해 담는다.
    형상 표현·배치·속성(이름·치수)·공간 소속·집합 소속, 문이면 개구부 관계(개구부의 형상·배치·벽), 벽이면 자기를 뚫는 개구부와 그것을 채우는
    요소. 속성셋·재료·스타일(색)·개구부의 GlobalId 는 IFC 가 정본이거나 신원이라 뺀다(속성만 바뀌거나 색이 입혀진 요소는 분리되지 않는다). 다른 요소를 가리키는 배치·관계는 그 요소의 GlobalId 로 가리킨다(요소의 형상이 바뀐 것이 의존
    요소의 클로저를 바꾸지 않게). 코어가 만든 뼈대(site/building)는 GlobalId 가 프로젝트마다 달라서 값으로 펼친다."""

    def __init__(self, f, infra: set[str]):
        self.f = f
        self.infra = infra
        self.val, _ = make_norm()
        self._owners: dict[int, list] = {}   # 배치 번호 -> 그 배치를 쓰는 제품들(닫힌 파일 전용: 같은 층 배치를 N개 요소가 공유하면 요소마다 훑는 것이 이차 시간이다)

    def ref(self, x):
        return "@infra" if x.GlobalId in self.infra else x.GlobalId

    def place(self, pl):
        if pl is None:
            return None
        out = {"at": self.val(pl.RelativePlacement)}
        rel = pl.PlacementRelTo
        if rel is None:
            out["to"] = None
        else:
            owners = self._owners.get(rel.id())
            if owners is None:
                owners = self._owners[rel.id()] = [o for o in self.f.get_inverse(rel) if o.is_a("IfcProduct") and o.ObjectPlacement == rel]
            if len(owners) == 1 and owners[0].GlobalId not in self.infra:
                out["to"] = {"ref": owners[0].GlobalId}
            else:
                out["to"] = self.place(rel)
        return out

    def shape(self, e, skip=("GlobalId", "OwnerHistory", "ObjectPlacement")):
        info = e.get_info(include_identifier=False, recursive=False)
        t = info.pop("type")
        return {"t": t, "attrs": {k: self.val(v) for k, v in info.items() if k not in skip}, "placement": self.place(e.ObjectPlacement)}

    def type_of_entity(self, e) -> dict:
        """패밀리 타입(IfcTypeObject)의 클로저: 이름·속성·표현 지도(형상)만 본다. 타입에 붙은 속성셋(HasPropertySets)은 IFC 가 정본이라 뺀다
        (DoBIM_Intent 도 속성셋이다). 타입은 배치가 없다."""
        info = e.get_info(include_identifier=False, recursive=False)
        t = info.pop("type")
        return {"t": t, "attrs": {k: self.val(v) for k, v in info.items() if k not in ("GlobalId", "OwnerHistory", "HasPropertySets")}}

    def of(self, e, typed: bool = False) -> dict:
        """typed=True 는 패밀리 인스턴스(finst): 투영이 소유하는 타입 지정(IfcRelDefinesByType)도 클로저에 넣는다. 문·벽·레벨은 넣지 않는다
        (외부 도구가 타입을 붙여도 형상이 아니므로 분리하지 않는다)."""
        if e.is_a("IfcTypeObject"):
            return self.type_of_entity(e)
        out = self.shape(e)
        inv = self.f.get_inverse(e)
        if typed:
            out["typed_by"] = sorted(r.RelatingType.GlobalId for r in _rels_of(e, inv, "IsTypedBy", "IfcRelDefinesByType", "RelatedObjects"))
        out["contained"] = sorted(self.ref(r.RelatingStructure)
                                  for r in _rels_of(e, inv, "ContainedInStructure", "IfcRelContainedInSpatialStructure", "RelatedElements"))
        out["aggregated"] = sorted(self.ref(r.RelatingObject) for r in _rels_of(e, inv, "Decomposes", "IfcRelAggregates", "RelatedObjects"))
        # 이 요소를 뚫는 개구부들. 개구부의 형상은 채우는 문의 클로저가 소유하므로 여기서는 "누가 채우는가"만 본다(GlobalId 는 신원이라
        # 외부 도구가 바꿔도 형상이 아니다). 채우는 요소가 없는 개구부나 외래 요소가 채우는 개구부가 더해지면 벽이 달라진 것이다
        voids = []
        for r in inv:
            if r.is_a("IfcRelVoidsElement") and r.RelatingBuildingElement == e:
                op = r.RelatedOpeningElement
                voids.append({"filler": sorted(self.ref(x.RelatedBuildingElement) for x in self.f.get_inverse(op)
                                               if x.is_a("IfcRelFillsElement"))})
        if voids:
            out["voids"] = sorted(voids, key=lambda x: json.dumps(x, sort_keys=True))
        fills = [r for r in inv if r.is_a("IfcRelFillsElement") and r.RelatedBuildingElement == e]
        if fills:
            ops = []
            for r in fills:
                op = r.RelatingOpeningElement
                d = self.shape(op)
                d["voids"] = sorted(self.ref(v.RelatingBuildingElement) for v in self.f.get_inverse(op) if v.is_a("IfcRelVoidsElement"))
                ops.append(d)
            out["opening"] = sorted(ops, key=lambda x: json.dumps(x, sort_keys=True))
        return out

    def digest(self, e, typed: bool = False) -> tuple[str, dict]:
        c = self.of(e, typed)
        blob = json.dumps(c, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest(), c


def closure_diff(a: dict, b: dict) -> list[str]:
    """두 클로저가 어느 부분에서 다른지(분리 사유)."""
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) == b.get(k):
            continue
        if k == "attrs":
            out += [f"속성 {n}" for n in sorted(set(a[k]) | set(b[k])) if a[k].get(n) != b[k].get(n)]
        elif k == "placement":
            out.append("배치")
        elif k == "opening":
            out.append("개구부 관계")
        elif k == "voids":
            out.append("벽에 뚫린 개구부")
        else:
            out.append({"t": "클래스", "contained": "공간 소속", "aggregated": "집합 소속", "typed_by": "타입 지정"}.get(k, k))
    return out


def opening_guid(door_id: str) -> str:
    """저작 문의 개구부 GlobalId: 문 id 만의 결정적 함수다. 프로젝트 UUID 에 기대지 않으므로 내보낸 파일을 다른 프로젝트로 가져와
    다시 투영해도(또는 삭제·실행 취소로 개구부를 다시 만들어도) 같은 값이 나온다(S2)."""
    return ifcopenshell.guid.compress(uuid.uuid5(_NS, f"opening:{door_id}").hex)


# ---------------------------------------------------------------- 외래 부속 스냅숏 (S2)
# 저작 요소를 지우면 remove_product 가 요소에 매달린 외래 관계(타입 지정, 수량 세트, 분류, 문서, 복합 재료, 그룹 등)를 함께 없앤다.
# 실행 취소가 이를 되돌리려면 지우기 전에 그 관계를 효과("att")로 로그에 적어야 한다. 아래는 그 스냅숏(JSON 트리)과 복원이다.
# 코어가 직접 만들고 지우거나 다른 효과(pset·mat)가 기록하는 관계(공간 소속, 집합, 개구부, 일반 속성셋, 단순 재료)는 뺀다.
_CORE_RELS = ("IfcRelContainedInSpatialStructure", "IfcRelAggregates", "IfcRelFillsElement", "IfcRelVoidsElement")


def _is_core_rel(r) -> bool:
    if any(r.is_a(c) for c in _CORE_RELS):
        return True
    if r.is_a("IfcRelDefinesByProperties"):
        d = r.RelatingPropertyDefinition
        return isinstance(d, ifcopenshell.entity_instance) and d.is_a("IfcPropertySet")
    if r.is_a("IfcRelAssociatesMaterial"):
        return r.RelatingMaterial.is_a("IfcMaterial")
    return False


def _tree(f, v, me, top=False):
    """속성 값을 JSON 트리로. me = 지우는 요소({"self"}), 다른 제품은 {"p": GlobalId}, 다른 요소도 쓰는 루트 객체는 {"g": GlobalId} 참조,
    다른 곳에서 쓰는 비루트 객체는 "s" 표시를 단 채 펼친다(복원 때 같은 내용의 기존 객체를 쓴다). top=True 는 이 객체를 반드시 펼친다."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (tuple, list)):
        return [_tree(f, x, me) for x in v]
    if not isinstance(v, ifcopenshell.entity_instance):
        return str(v)
    if me is not None and v == me:
        return {"self": 1}
    if v.id() == 0:   # 파일에 독립 번호가 없는 값 래퍼(IfcLabel 등)
        return {"w": v.is_a(), "v": _tree(f, v.wrappedValue, me)}
    if v.is_a("IfcProduct"):
        return {"p": v.GlobalId}
    rooted = hasattr(v, "GlobalId")
    shared = len({i.id() for i in f.get_inverse(v)}) > 1
    if shared and rooted and not top:
        return {"g": v.GlobalId}
    info = v.get_info(include_identifier=False, recursive=False)
    t = info.pop("type")
    d: dict = {"t": t, "a": {k: _tree(f, x, me) for k, x in info.items() if k not in ("GlobalId", "OwnerHistory")}}
    if rooted:
        d["gid"] = v.GlobalId
    elif shared:
        d["s"] = 1
    return d


def _strip_flags(t):
    if isinstance(t, list):
        return [_strip_flags(x) for x in t]
    if isinstance(t, dict):
        return {k: _strip_flags(x) for k, x in t.items() if k != "s"}
    return t


class _Missing(Exception):
    """복원할 때 가리키는 제품이 아직 없다(같은 트랜잭션에서 함께 지워진 요소). 해당 항목만 건너뛴다."""


class _Restorer:
    def __init__(self, f, me):
        self.f, self.me = f, me
        self._idx: dict[str, dict[str, Any]] = {}

    def _equal(self, cls: str, t: dict, orphan_only: bool):
        """같은 내용의 기존 객체. 다른 요소가 쓰던 것(공유)이면 어느 것이든, 이 요소만 쓰던 것이면 지우기가 남긴 고아(참조 없음)만 쓴다
        (ifcopenshell 의 재료 지정 해제는 층 구성 같은 하위 객체를 지우지 않고 남긴다: 되살릴 때 중복을 만들지 않는다)."""
        idx = self._idx.get(cls)
        if idx is None:
            idx = self._idx[cls] = {}
            for e in self.f.by_type(cls, include_subtypes=False):
                idx.setdefault(json.dumps(_strip_flags(_tree(self.f, e, None)), sort_keys=True), []).append(e)
        for e in idx.get(json.dumps(_strip_flags(t), sort_keys=True), ()):
            if not orphan_only or not self.f.get_inverse(e):
                return e
        return None

    def build(self, t):
        f = self.f
        if isinstance(t, list):
            out = []
            for x in t:
                try:
                    out.append(self.build(x))
                except _Missing:
                    continue   # 목록의 한 항목만 없으면 그 항목을 뺀다
            return out
        if not isinstance(t, dict):
            return t
        if "self" in t:
            return self.me
        if "p" in t or "g" in t:
            try:
                return f.by_guid(t.get("p") or t["g"])
            except RuntimeError:
                raise _Missing() from None
        if "w" in t:
            return f.create_entity(t["w"], self.build(t["v"]))
        cls = t["t"]
        if t.get("gid"):
            try:
                return f.by_guid(t["gid"])
            except RuntimeError:
                pass
        if not t.get("gid"):
            ex = self._equal(cls, t, orphan_only=not t.get("s"))
            if ex is not None:
                return ex
        kw = {k: self.build(v) for k, v in t["a"].items()}
        if t.get("gid"):
            kw["GlobalId"] = t["gid"]
        return f.create_entity(cls, **kw)


def _has_self(t) -> bool:
    if isinstance(t, list):
        return any(_has_self(x) for x in t)
    return isinstance(t, dict) and "self" in t


class IfcModel:
    def __init__(self, project_guid: str, base: bytes | None = None):
        self.project_guid = project_guid
        self.base_mode = base is not None
        self.ents: dict[str, Any] = {}
        self.openings: dict[str, Any] = {}      # 문 id -> 개구부 엔티티 (투영 소유)
        self.container_of: dict[str, str] = {}  # 요소 id -> 현재 담긴 층 id (투영 상태)
        self._mats: dict[str, Any] = {}         # 재료 이름 -> 엔티티
        # 패밀리 투영 상태(S4): 요소 종류, 타입별 IfcRelDefinesByType, 인스턴스의 타입, 평면 문맥(필요할 때만 만든다)
        self.kinds: dict[str, str] = {}
        self.typerels: dict[str, Any] = {}
        self._typerel_gid: dict[str, str] = {}  # 타입 id -> 타입 지정 관계의 GlobalId (가져온 파일의 값을 관계를 다시 만들 때도 유지한다)
        self.type_of: dict[str, str] = {}
        # 일괄 모드(검증용 임시 모델 전용): 층 담기·타입 관계 추가를 모았다가 flush_rels 에서 한 번에 한다. ifcopenshell 은 관계의
        # 대상 목록에 하나를 더할 때마다 목록 전체를 다시 쓰므로(O(N)) 같은 층·타입에 N개를 하나씩 넣으면 이차 시간이다.
        # 켜 두면 관계를 읽기 전에 flush_rels 를 불러야 한다.
        self.defer_rels = False
        self._pend_contain: dict[str, list] = {}   # 층 id -> 담을 요소들
        self._pend_type: dict[str, list] = {}      # 타입 id -> 타입 관계에 더할 인스턴스들
        self.type_maps: dict[str, bool] = {}
        self.plan_ctx: Any = None
        # 복원까지 실패해 IFC 쪽이 로그와 어긋났을 수 있으면 그 이유가 들어간다. 엔진이 이 표시를 보고 로그에서
        # 투영을 다시 만든다(만들지 못하면 쓰기를 거부한다). 예외를 삼키고 넘어가는 일은 없어야 한다.
        self.tainted: str | None = None
        self.infra: set[str] = set()            # 코어가 만든 뼈대(site/building)의 GlobalId: 요소가 아니다
        self.base_ents: set[str] = set()        # 가져온 파일의 엔티티를 그대로 이어받은 요소 id (아직 이 세션에서 다시 만들지 않은 것)
        self._opening_gid: dict[str, str] = {}  # 문 id -> 개구부 GlobalId (가져온 파일의 값을 다시 투영해도 유지한다)
        self.pending_styles: dict[str, list] = {}   # 지우기를 되돌린 요소 id -> 새 몸체 솔리드에 입힐 스타일 스냅숏
        if base is not None:
            self._open_base(base)
            return
        f = api_project.create_file(version="IFC4")
        self.f = f
        proj = api_root.create_entity(f, ifc_class="IfcProject", name="dobim")
        # 단위는 순서를 고정해 직접 만든다. assign_unit 기본값은 프로세스마다 순서가 달라 파일 바이트가 흔들린다.
        api_unit.assign_unit(f, units=[api_unit.add_si_unit(f, unit_type="LENGTHUNIT", prefix="MILLI"),
                                       api_unit.add_si_unit(f, unit_type="AREAUNIT"),
                                       api_unit.add_si_unit(f, unit_type="VOLUMEUNIT")])
        model = api_context.add_context(f, context_type="Model")
        self._body = api_context.add_context(f, context_type="Model", context_identifier="Body",
                                             target_view="MODEL_VIEW", parent=model)
        site = api_root.create_entity(f, ifc_class="IfcSite", name="Site")
        bldg = api_root.create_entity(f, ifc_class="IfcBuilding", name="Building")
        for e, label in ((proj, "project"), (site, "site"), (bldg, "building")):
            e.GlobalId = derived_guid(project_guid, label)
        site.ObjectPlacement = self._placement(None)
        bldg.ObjectPlacement = self._placement(site.ObjectPlacement)
        api_aggregate.assign_object(f, products=[site], relating_object=proj)
        api_aggregate.assign_object(f, products=[bldg], relating_object=site)
        self.building = bldg
        self.infra = {site.GlobalId, bldg.GlobalId}

    # ------------------------------------------------------------ 외래 기반 (S2)
    def _open_base(self, data: bytes) -> None:
        """가져온 파일을 작업 사본으로 연다. 요소는 GlobalId 로 찾아 쓰고(create_identity), 파일 자체는 건드리지 않는다."""
        self.f = open_ifc_bytes(data)
        self._body = None
        sk = detect_skeleton(self.f)
        if sk is not None:
            self.infra = {sk[0].GlobalId, sk[1].GlobalId}
        blds = self.f.by_type("IfcBuilding")
        self.building = sk[1] if sk is not None else (blds[0] if blds else None)
        self._by_gid = {e.GlobalId: e for e in self.f.by_type("IfcProduct")}
        # 패밀리 타입(IfcTypeObject): 가져오기가 의도를 복원한 타입(ftype)만 요소가 된다. 요소가 아닌 타입은 파일에 그대로 남아야 하므로
        # 제품 색인(_by_gid, prune 이 요소 아닌 것을 지운다)과 따로 둔다
        self._types_by_gid = {e.GlobalId: e for e in self.f.by_type("IfcTypeObject") if getattr(e, "GlobalId", None)}
        for m in self.f.by_type("IfcMaterial"):
            self._mats.setdefault(m.Name, m)
        # 평면 기호 문맥('Plan' 하위 문맥 FootPrint): 파일에 이미 있으면 그것을 쓴다(패밀리 투영이 중복해서 만들지 않게)
        for c in self.f.by_type("IfcGeometricRepresentationSubContext"):
            if c.ContextType == "Plan" and c.ContextIdentifier == "FootPrint" and c.TargetView == "PLAN_VIEW":
                self.plan_ctx = c
                break

    @property
    def body(self):
        """투영이 쓰는 Body 하위 컨텍스트. 외래 파일이면 처음 필요할 때 찾거나 만든다."""
        if self._body is None:
            f = self.f
            for c in f.by_type("IfcGeometricRepresentationSubContext"):
                if c.ContextIdentifier == "Body" and c.ContextType == "Model" and c.TargetView == "MODEL_VIEW":
                    self._body = c
                    break
            else:
                parents = [c for c in f.by_type("IfcGeometricRepresentationContext")
                           if not c.is_a("IfcGeometricRepresentationSubContext") and c.ContextType == "Model"]
                parent = parents[0] if parents else api_context.add_context(f, context_type="Model")
                self._body = api_context.add_context(f, context_type="Model", context_identifier="Body",
                                                     target_view="MODEL_VIEW", parent=parent)
        return self._body

    def require_authoring(self) -> None:
        """외래 기반 파일에 저작 요소를 새로 만들 수 있는지(건물이 있고 단위가 mm). 투영 값이 mm 이기 때문이다."""
        if self.building is None:
            raise DobimError("UNSUPPORTED", "건물(IfcBuilding)이 없는 파일에는 저작 요소를 추가할 수 없다")
        try:
            scale = uunit.calculate_unit_scale(self.f)
        except Exception:
            scale = None
        if scale is None or abs(scale - 0.001) > 1e-9:
            raise DobimError("UNSUPPORTED", "길이 단위가 밀리미터가 아닌 파일에는 저작 요소를 추가할 수 없다")

    def prune(self, import_types: frozenset | set = frozenset()) -> None:
        """가져온 뒤 지워진 저작 요소의 엔티티를 작업 사본에서 없앤다(요소·뼈대·저작 문의 개구부가 아닌 제품).
        import_types = 시작 상태 기록(seq 0)에 요소(ftype)로 들어 있던 패밀리 타입의 id: 지금 요소가 아니면 지워진 것이다.
        요소로 들어오지 않은 타입(외래 타입)은 제품이 아니므로 건드리지 않는다."""
        keep = set(self.ents) | self.infra | {o.GlobalId for o in self.openings.values()}
        gone = [g for g in self._by_gid if g not in keep]
        mats = []
        for g in sorted(gone, key=lambda g: (not self._by_gid[g].is_a("IfcOpeningElement"), g)):
            try:
                e = self.f.by_guid(g)
            except RuntimeError:
                continue
            if e.is_a("IfcOpeningElement"):
                api_feature.remove_feature(self.f, feature=e)
            else:
                m = ue.get_material(e, should_inherit=False)
                if m is not None:
                    mats.append(m)
                self._remove_product(e)
        for m in mats:   # 지운 요소만 쓰던 재료는 세션 안에서 지울 때와 같이 없앤다(다시 열어도 같은 해시)
            try:
                self._gc_material(m)
            except RuntimeError:
                pass   # 이미 지워졌다
        for g in gone:   # 지운 엔티티를 가리키는 낡은 참조가 남으면 지우기를 되돌릴 때 이미 지워진 엔티티를 이어받는다
            self._by_gid.pop(g, None)
        for g in sorted(import_types - set(self.ents)):   # 제품(인스턴스)을 먼저 지운 뒤라 타입에 매달린 지정 관계는 비어 있다
            t = self._types_by_gid.pop(g, None)
            if t is not None:
                self._remove_product(t)

    def _remove_product(self, e) -> None:
        """제품을 지우고, 그 때문에 대상이 비게 된 외래 관계(분류·문서·그룹 등)도 지운다(빈 관계는 IFC 로 유효하지 않다)."""
        f = self.f
        rels = [r.id() for r in f.get_inverse(e) if r.is_a("IfcRelationship") and not _is_core_rel(r)] if self.base_mode else []
        api_root.remove_product(f, product=e)
        for i in rels:
            try:
                r = f.by_id(i)
            except RuntimeError:
                continue
            if any(isinstance(v, tuple) and not v and k.startswith("Related") for k, v in r.get_info(recursive=False).items()):
                ue.remove_deep2(f, r)

    # ------------------------------------------------------------ 저수준 도우미
    def _placement(self, rel_to, origin=(0.0, 0.0, 0.0), xdir=(1.0, 0.0, 0.0)):
        f = self.f
        return f.createIfcLocalPlacement(rel_to, f.createIfcAxis2Placement3D(
            f.createIfcCartesianPoint([float(v) for v in origin]), f.createIfcDirection([0.0, 0.0, 1.0]),
            f.createIfcDirection([float(v) for v in xdir])))

    def _place(self, e, rel_to, origin, xdir):
        """배치를 (다시) 지정한다. 배치 엔티티는 유지해 이를 참조하는 다른 요소의 관계가 끊기지 않는다."""
        f = self.f
        axis = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([float(v) for v in origin]),
                                           f.createIfcDirection([0.0, 0.0, 1.0]),
                                           f.createIfcDirection([float(v) for v in xdir]))
        pl = e.ObjectPlacement
        if pl is None:
            e.ObjectPlacement = f.createIfcLocalPlacement(rel_to, axis)
            return
        old = pl.RelativePlacement
        pl.PlacementRelTo = rel_to
        pl.RelativePlacement = axis
        if old is not None:
            ue.remove_deep2(f, old)

    def _solid(self, pts, depth, z0=0.0):
        f = self.f
        pl = f.createIfcPolyline([f.createIfcCartesianPoint([float(x), float(y)]) for x, y in list(pts) + [pts[0]]])
        prof = f.createIfcArbitraryClosedProfileDef("AREA", None, pl)
        pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([0.0, 0.0, float(z0)]), None, None)
        return f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([0.0, 0.0, 1.0]), float(depth))

    def _set_body(self, product, solid):
        """기존 표현이 있으면 항목만 교체한다. 표현·배치 엔티티는 유지해 다른 요소의 참조가 끊기지 않는다."""
        f = self.f
        shape = product.Representation
        if shape is None:
            rep = f.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", [solid])
            product.Representation = f.createIfcProductDefinitionShape(None, None, [rep])
            self._restore_style(product, solid)
            return
        rep = shape.Representations[0]
        old = list(rep.Items)
        rep.Items = [solid]
        # 외부 도구가 몸체에 붙인 스타일(색)·레이어 지정은 새 솔리드로 옮긴다: 외형은 의도가 정한 형상이 아니라 요소가 가진 속성이다.
        # 형상이 실제로 바뀌어 다시 투영해도 스타일은 유지한다(항목이 여럿이면 첫 스타일만, 나머지는 버린다)
        moved = False
        for o in old:
            for st in list(o.StyledByItem or ()):
                if moved:
                    ue.remove_deep2(f, st)
                else:
                    st.Item = solid
                    moved = True
            for lay in [x for x in f.get_inverse(o) if x.is_a("IfcPresentationLayerAssignment")]:
                lay.AssignedItems = tuple(solid if x == o else x for x in lay.AssignedItems)
        for o in old:
            ue.remove_deep2(f, o)
        if not moved:
            self._restore_style(product, solid)

    def _restore_style(self, product, solid) -> None:
        """지우기를 되돌린 요소의 스타일 스냅숏이 있으면 새 솔리드에 입힌다(요소가 처음 투영될 때 한 번)."""
        pend = self.pending_styles.pop(product.GlobalId, None)
        if not pend:
            return
        rs = _Restorer(self.f, None)
        item = pend[0]
        styles = rs.build(item["styles"])
        if styles:
            self.f.createIfcStyledItem(solid, styles, item.get("name"))

    # ------------------------------------------------------------ 신원 (즉시)
    def _adopt_family_state(self, eid: str, kind: str, e) -> None:
        """가져온 파일의 패밀리 타입·인스턴스가 이미 갖고 있는 투영 상태(타입 지정 관계, 표현 지도 유무)를 이어받는다.
        이어받지 않으면 첫 재투영이 타입 지정 관계를 하나 더 만든다(같은 인스턴스가 관계 두 개에 걸린다)."""
        if kind == "finst":
            for r in _rels_of(e, self.f.get_inverse(e), "IsTypedBy", "IfcRelDefinesByType", "RelatedObjects"):
                tid = r.RelatingType.GlobalId
                self.type_of[eid] = tid
                self.typerels.setdefault(tid, r)
                self._typerel_gid.setdefault(tid, r.GlobalId)
                break
        elif kind == "ftype":
            if e.RepresentationMaps:
                self.type_maps[eid] = True
            for r in self.f.get_inverse(e):
                if r.is_a("IfcRelDefinesByType") and r.RelatingType == e:
                    self.typerels.setdefault(eid, r)
                    self._typerel_gid.setdefault(eid, r.GlobalId)
                    break

    def create_identity(self, eid: str, kind: str, ifc_class: str | None = None) -> None:
        f = self.f
        if self.base_mode:
            e = (self._types_by_gid if kind == "ftype" else self._by_gid).get(eid)
            if e is not None:   # 가져온 파일에 이미 있는 요소: 새로 만들지 않고 이어받는다
                self.kinds[eid] = kind
                self.ents[eid] = e
                self.base_ents.add(eid)
                if kind != "ftype":
                    c = ue.get_container(e, should_get_direct=True)
                    if c is not None:
                        self.container_of[eid] = c.GlobalId
                if kind in ("door", "finst"):
                    for r in f.get_inverse(e):
                        if r.is_a("IfcRelFillsElement") and r.RelatedBuildingElement == e:
                            self.openings[eid] = r.RelatingOpeningElement
                            self._opening_gid[eid] = r.RelatingOpeningElement.GlobalId
                self._adopt_family_state(eid, kind, e)
                return
            if kind == "foreign":
                raise DobimError("INTERNAL", f"가져온 파일에 외래 요소가 없다: {eid}")
            self.require_authoring()
        e = api_root.create_entity(f, ifc_class=IFC_CLASS.get(kind) or ifc_class)
        self.kinds[eid] = kind
        e.GlobalId = eid
        if kind == "level":
            api_aggregate.assign_object(f, products=[e], relating_object=self.building)
            e.ObjectPlacement = self._placement(self.building.ObjectPlacement)
        self.ents[eid] = e

    def remove_identity(self, eid: str) -> list:
        """요소의 IFC 신원을 지운다. 벽이면 그 벽에 뚫린 개구부가 (ifcopenshell 의 remove_product 로) 함께 사라지므로,
        그 개구부를 투영 상태(self.openings)에서도 먼저 뺀다: 안 그러면 다음 투영이 이미 없는 개구부를 또 지우려다 터진다
        (분할로 문이 새 벽으로 옮겨진 뒤 투영하고 분할을 undo 하면 새 벽이 지워지는 경로). 개구부를 잃은 문 id 목록을 돌려주면
        엔진이 그 문의 투영 스탬프를 지워 다음 투영 때 다시 만들게 한다(스펙이 되돌아와도 스탬프가 같아 건너뛰는 일을 막는다)."""
        f = self.f
        e = self.ents[eid]
        self._unproject_door(eid)
        lost = []
        for rel in list(getattr(e, "HasOpenings", None) or ()):
            op = rel.RelatedOpeningElement.id()   # 엔티티 래퍼는 접근할 때마다 새로 만들어지므로 번호로 비교한다
            for d, o in list(self.openings.items()):
                if o.id() == op:
                    self._unproject_door(d)
                    self.base_ents.discard(d)   # 개구부를 잃었다: 가져온 엔티티가 그대로 유효하다는 가정(투영 건너뛰기)이 깨졌으니 다시 투영해야 한다
                    lost.append(d)
        kind = self.kinds.get(eid)
        if kind == "finst":
            fp.unproject_inst(self, eid)
        elif kind == "ftype":
            fp.unproject_type(self, eid)
        self._remove_product(e)   # 실패하면 ents 가 그대로라 상태가 설명 가능하다(순서: 지운 뒤에 목록에서 뺀다)
        self.ents.pop(eid)
        self.kinds.pop(eid, None)
        self.container_of.pop(eid, None)
        self.base_ents.discard(eid)
        self._opening_gid.pop(eid, None)
        self.pending_styles.pop(eid, None)
        if self.base_mode:
            self._by_gid.pop(eid, None)   # 지운 엔티티를 실행 취소가 이어받지 않게
            self._types_by_gid.pop(eid, None)
        return sorted(lost)

    # ------------------------------------------------------------ 외래 부속 (S2)
    def capture_attachments(self, eid: str) -> dict | None:
        """지우기 전에 요소에 매달린 외래 부속(타입·수량 세트·분류·복합 재료 등의 관계, 몸체 스타일, 개구부 GlobalId)을 JSON 으로 뜬다.
        외래 기반 프로젝트에서만 의미가 있다. 없으면 None."""
        if not self.base_mode:
            return None
        f, e = self.f, self.ents[eid]
        kind = self.kinds.get(eid)
        # 패밀리 인스턴스의 타입 지정 관계와 패밀리 타입 자신의 지정 관계(인스턴스를 모은 것)는 투영 소유다
        own_type_rel = self.typerels.get(self.type_of.get(eid)) if kind == "finst" else self.typerels.get(eid) if kind == "ftype" else None
        rels = [_tree(f, r, e, top=True) for r in f.get_inverse(e)
                if r.is_a("IfcRelationship") and not _is_core_rel(r) and (own_type_rel is None or r.id() != own_type_rel.id())]
        out: dict = {}
        if rels:
            out["rels"] = sorted(rels, key=lambda t: json.dumps(t, sort_keys=True))
        styles = []
        if not e.is_a("IfcTypeObject") and e.Representation is not None:   # 타입 객체는 몸체 표현이 아니라 표현 지도를 가진다(투영이 다시 만든다)
            for shape in e.Representation.Representations or ():
                for it in shape.Items or ():
                    for st in it.StyledByItem or ():
                        styles.append({"name": st.Name, "styles": [_tree(f, x, e) for x in st.Styles]})
        if styles:
            out["styles"] = styles
        op = self.openings.get(eid)
        if op is not None and op.GlobalId != opening_guid(eid):
            out["opening_gid"] = op.GlobalId
        return out or None

    def restore_attachments(self, eid: str, blob: dict) -> None:
        """capture_attachments 가 뜬 부속을 되살린다(지우기를 되돌릴 때). 관계는 바로, 스타일은 몸체가 투영될 때 입힌다."""
        f, e = self.f, self.ents[eid]
        rs = _Restorer(f, e)
        for t in blob.get("rels", ()):
            try:
                self._restore_rel(rs, e, t)
            except _Missing:
                continue   # 가리키는 제품이 아직 없다: 그 제품의 부속이 되살아날 때 이 관계가 함께 만들어진다
        if blob.get("styles"):
            self.pending_styles[eid] = blob["styles"]
        if blob.get("opening_gid"):
            self._opening_gid[eid] = blob["opening_gid"]

    def _restore_rel(self, rs, e, t: dict) -> None:
        f = self.f
        try:
            ex = f.by_guid(t["gid"])
        except RuntimeError:
            ex = None
        if ex is not None:   # 다른 요소와 공유하던 관계가 남아 있다: 이 요소를 대상에 되돌려 넣는다
            for k, v in t["a"].items():
                if not _has_self(v):
                    continue
                cur = getattr(ex, k)
                if isinstance(v, list):
                    if e not in (cur or ()):
                        setattr(ex, k, list(cur or ()) + [e])
                elif cur != e:
                    setattr(ex, k, e)
            return
        kw = {k: rs.build(v) for k, v in t["a"].items()}
        kw["GlobalId"] = t["gid"]
        f.create_entity(t["t"], **kw)

    def foreign_voids(self, eid: str) -> list[str]:
        """벽을 뚫는 개구부 중 코어가 만들지 않은 것(외래)의 GlobalId. 벽을 지우면 이것들도 함께 지워진다."""
        e = self.ents[eid]
        own = {o.GlobalId for o in self.openings.values()}
        return sorted(r.RelatedOpeningElement.GlobalId for r in self.f.get_inverse(e)
                      if r.is_a("IfcRelVoidsElement") and r.RelatingBuildingElement == e and r.RelatedOpeningElement.GlobalId not in own)

    def foreign_typed(self, eid: str) -> list[str]:
        """패밀리 타입에 타입 지정(IfcRelDefinesByType)으로 붙은 요소 중 코어의 패밀리 인스턴스가 아닌 것(외래)의 GlobalId.
        타입을 지우면 이것들의 타입 지정이 사라진다."""
        e = self.ents[eid]
        own = {g for g, t in self.type_of.items() if t == eid}
        return sorted({o.GlobalId for r in self.f.get_inverse(e) if r.is_a("IfcRelDefinesByType") and r.RelatingType == e
                       for o in r.RelatedObjects if o.GlobalId not in own})

    def has_complex_material(self, eid: str) -> bool:
        """재료가 단일 IfcMaterial 이 아니다(층 구성·프로파일 등). 이름 하나로 되살릴 수 없어 부속 스냅숏이 맡는다."""
        m = ue.get_material(self.ents[eid], should_inherit=False)
        return m is not None and not m.is_a("IfcMaterial")

    # ------------------------------------------------------------ 속성셋·재료 (정본)
    def get_psets(self, eid: str) -> dict:
        out = {}
        for name, props in ue.get_psets(self.ents[eid], psets_only=True, should_inherit=False).items():
            if name == INTENT_PSET:
                continue
            out[name] = {k: plain(v) for k, v in props.items() if k != "id"}
        return out

    def get_material(self, eid: str) -> str | None:
        return material_label(ue.get_material(self.ents[eid], should_inherit=False))

    def set_prop(self, eid: str, pset: str, prop: str, value) -> None:
        """속성 하나를 쓴다. 실패하면 원래 값으로 복원하고 예외를 다시 던진다(삭제 뒤 실패해도 속성이 사라지지 않는다).
        복원마저 실패하면 삼키지 않고 self.tainted 에 남긴다(엔진이 로그에서 투영을 다시 만든다)."""
        check_value(value)
        f = self.f
        e = self.ents[eid]
        cur = ue.get_pset(e, pset, should_inherit=False)   # 인스턴스가 타입의 속성셋을 물려받아 보이므로 끈다(타입 것을 고치면 안 된다)
        if cur is None:
            if value is None:
                return
            ps = api_pset.add_pset(f, product=e, name=pset)
            old = None
        else:
            ps = f.by_id(cur["id"])
            if any(r.is_a("IfcRelDefinesByProperties") and len(r.RelatedObjects) > 1 for r in f.get_inverse(ps)):
                raise DobimError("UNSUPPORTED", f"여러 요소가 공유하는 속성셋은 고칠 수 없다: {pset}", path="/pset")
            old = cur.get(prop)
        # edit_pset 은 기존 속성의 타입을 따라 값을 강제 변환한다(1 다음 2.5 가 2 로 저장). 타입이 바뀌면
        # 속성을 먼저 지우고 새로 만들어 저장값이 입력과 정확히 같게 한다.
        try:
            if old is not None and value is not None and type(old) is not type(value):
                api_pset.edit_pset(f, pset=ps, properties={prop: None})
            api_pset.edit_pset(f, pset=ps, properties={prop: value})
        except BaseException:
            try:   # 지운 뒤 실패했을 수 있으니 원래 값을 되살린다
                api_pset.edit_pset(f, pset=ps, properties={prop: None})
                if old is not None:
                    api_pset.edit_pset(f, pset=ps, properties={prop: old})
            except BaseException as ex:
                self.tainted = f"속성 복원 실패 {pset}/{prop}: {type(ex).__name__}: {ex}"
            try:   # 실패해도 빈 속성셋을 남기지 않는다 (상태는 속성 맵의 함수여야 한다)
                self._drop_empty_pset(e, pset, ps)
            except BaseException as ex:
                self.tainted = self.tainted or f"빈 속성셋 정리 실패 {pset}: {type(ex).__name__}: {ex}"
            raise
        self._drop_empty_pset(e, pset, ps)   # 성공 경로: 실패하면 호출자(엔진)가 실패로 다룬다

    def _drop_empty_pset(self, e, pset: str, ps) -> None:
        remain = ue.get_pset(e, pset, should_inherit=False)
        if remain is not None and not [k for k in remain if k != "id"]:
            api_pset.remove_pset(self.f, product=e, pset=ps)

    def set_material(self, eid: str, name: str | None) -> None:
        f = self.f
        e = self.ents[eid]
        cur = ue.get_material(e, should_inherit=False)
        if name is None:
            if cur is not None:
                api_material.unassign_material(f, products=[e])
                self._gc_material(cur)
            return
        mat = self._mats.get(name)
        if mat is None:
            mat = api_material.add_material(f, name=name)
            self._mats[name] = mat
        api_material.assign_material(f, products=[e], material=mat)
        if cur is not None and cur != mat:
            self._gc_material(cur)

    def _gc_material(self, mat) -> None:
        """사용처가 없는 재료는 지운다. 재료 목록이 상태의 함수가 되어야 취소 후 해시가 복원된다."""
        if mat.is_a("IfcMaterial") and not self.f.get_inverse(mat):
            self._mats.pop(mat.Name, None)
            api_material.remove_material(self.f, material=mat)

    # ------------------------------------------------------------ 투영 (늦게)
    def project(self, eid: str, spec: dict) -> None:
        kind = spec["kind"]
        {"level": self._project_level, "wall": self._project_wall, "door": self._project_door,
         "ftype": lambda i, sp: fp.project_type(self, i, sp), "finst": lambda i, sp: fp.project_inst(self, i, sp)}[kind](eid, spec)
        # 의도를 IFC 에도 복제한다. 정본은 의도 저장소이고 이 속성셋은 매번 덮어쓴다.
        e = self.ents[eid]
        text = json.dumps(spec["intent"], sort_keys=True, separators=(",", ":"))
        cur = ue.get_pset(e, INTENT_PSET, should_inherit=False)
        if cur is None or cur.get("Intent") != text:
            self.set_prop(eid, INTENT_PSET, "Intent", text)

    def _project_level(self, eid, spec):
        e = self.ents[eid]
        e.Name = spec["name"]
        e.Elevation = float(spec["elevation"])
        e.ObjectPlacement.RelativePlacement.Location.Coordinates = (0.0, 0.0, float(spec["elevation"]))

    def _contain(self, eid, level_id):
        """요소를 층에 담는다. 층이 바뀐 경우에만 관계를 다시 만든다."""
        f = self.f
        e = self.ents[eid]
        if self.container_of.get(eid) == level_id:
            return
        if self.defer_rels and eid not in self.container_of:
            self._pend_contain.setdefault(level_id, []).append(e)
            self.container_of[eid] = level_id
            return
        self.flush_rels()
        if eid in self.container_of:
            api_spatial.unassign_container(f, products=[e])
        api_spatial.assign_container(f, products=[e], relating_structure=self.ents[level_id])
        self.container_of[eid] = level_id

    def flush_rels(self) -> None:
        """일괄 모드에서 모아 둔 층 담기·타입 관계 추가를 반영한다(모은 것이 없으면 아무것도 하지 않는다)."""
        pend, self._pend_contain = self._pend_contain, {}
        for level_id, els in pend.items():
            api_spatial.assign_container(self.f, products=els, relating_structure=self.ents[level_id])
        pend, self._pend_type = self._pend_type, {}
        for tid, els in pend.items():
            rel = self.typerels[tid]
            rel.RelatedObjects = list(rel.RelatedObjects) + els

    def _project_wall(self, eid, spec):
        e = self.ents[eid]
        e.Name = spec["name"]
        x, y = spec["origin"]
        c, sn = spec["xdir"]
        # 층에 담는 호출이 배치를 건드릴 수 있으므로 담은 뒤에 배치를 확정한다
        self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, (x, y, 0.0), (c, sn, 0.0))
        self._set_body(e, self._solid(spec["footprint"], spec["height"]))

    def derived_guid_for(self, label: str) -> str:
        return derived_guid(self.project_guid, label)

    def unproject_opening(self, eid):
        self._unproject_door(eid)

    def _unproject_door(self, eid):
        op = self.openings.get(eid)
        if op is not None:
            api_feature.remove_feature(self.f, feature=op)   # 개구부 엔티티와 관계를 함께 지운다
            del self.openings[eid]

    def _project_door(self, eid, spec):
        f = self.f
        e = self.ents[eid]
        wall = self.ents[spec["host"]]
        e.Name = spec["name"]
        e.OverallWidth = float(spec["width"])
        e.OverallHeight = float(spec["height"])
        origin = (float(spec["offset"]), 0.0, float(spec["sill"]))
        self._contain(eid, spec["level"])
        # 문은 호스트 벽의 국소 좌표에 놓으므로 벽을 옮겨도 이 투영은 다시 하지 않는다.
        self._place(e, wall.ObjectPlacement, origin, (1.0, 0.0, 0.0))
        w2, t2 = spec["width"] / 2.0, spec["wall_thickness"] / 2.0
        rect = [(-w2, -t2), (w2, -t2), (w2, t2), (-w2, t2)]
        self._set_body(e, self._solid(rect, spec["height"]))
        # 개구부는 투영이 소유한다. 이미 있고 이 문·벽과 이어져 있으면 제자리에서 고쳐 GlobalId·붙은 속성을 유지한다(외부 도구가 GlobalId 를
        # 바꿨어도 그대로 둔다). 없으면 새로 만든다: GlobalId 는 되돌린 지우기가 남긴 값, 없으면 문 id 에서 결정적으로 얻는다.
        op = self.openings.get(eid)
        if op is not None and op.Representation is not None and any(
                r.is_a("IfcRelVoidsElement") and r.RelatingBuildingElement == wall for r in f.get_inverse(op)) and any(
                r.is_a("IfcRelFillsElement") and r.RelatedBuildingElement == e for r in f.get_inverse(op)):
            op.Name = spec["name"] + "-opening"
            self._place(op, wall.ObjectPlacement, origin, (1.0, 0.0, 0.0))
            self._set_body(op, self._solid(rect, spec["height"]))
            return
        self._unproject_door(eid)
        op = api_root.create_entity(f, ifc_class="IfcOpeningElement")
        op.GlobalId = self._opening_gid.get(eid) or opening_guid(eid)
        op.Name = spec["name"] + "-opening"
        self._place(op, wall.ObjectPlacement, origin, (1.0, 0.0, 0.0))
        op.Representation = f.createIfcProductDefinitionShape(None, None, [
            f.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", [self._solid(rect, spec["height"])])])
        api_feature.add_feature(f, feature=op, element=wall)
        api_feature.add_filling(f, opening=op, element=e)
        self.openings[eid] = op
        self._opening_gid[eid] = op.GlobalId

    # ------------------------------------------------------------ 의미 해시·검증·내보내기
    def semantic_dump(self) -> list:
        """STEP 번호와 무작위 GlobalId 를 뺀 정규화 덤프. OS·실행 순서와 무관해야 한다 (S0 발견 7)."""
        val, ent = make_norm()

        f = self.f
        prods = []
        for e in list(f.by_type("IfcProduct")) + list(f.by_type("IfcTypeObject")):
            info = e.get_info(include_identifier=False, recursive=False)
            d = {"t": info.pop("type")}
            for name, v in info.items():
                if name == "OwnerHistory":
                    continue
                d[name] = val(v)
            prods.append(d)
        prods.sort(key=lambda d: d["GlobalId"])
        rels = [json.dumps(ent(r), sort_keys=True, separators=(",", ":")) for r in f.by_type("IfcRelationship")]
        rels.sort()
        others = [json.dumps(ent(e), sort_keys=True, separators=(",", ":"))
                  for t in ("IfcProject", "IfcMaterial") for e in f.by_type(t)]
        # S2: 몸체에 입힌 스타일(요소별), 어느 요소에도 속하지 않은 스타일, 타입 객체, 수량 세트. 비어 있으면 아무것도 더하지 않는다
        # (코어가 직접 만든 모델의 해시는 그대로다)
        reached: set[int] = set()
        for e in f.by_type("IfcProduct"):
            if e.Representation is None:
                continue
            for shape in e.Representation.Representations or ():
                for it in shape.Items or ():
                    for st in it.StyledByItem or ():
                        reached.add(st.id())
                        others.append(json.dumps({"styled": e.GlobalId, "name": st.Name, "styles": val(st.Styles)},
                                                 sort_keys=True, separators=(",", ":")))
        for st in f.by_type("IfcStyledItem"):
            if st.id() not in reached:
                others.append(json.dumps({"styled_other": {"name": st.Name, "styles": val(st.Styles)}}, sort_keys=True, separators=(",", ":")))
        for t in ("IfcTypeObject", "IfcElementQuantity"):
            others.extend(json.dumps(ent(e), sort_keys=True, separators=(",", ":")) for e in f.by_type(t))
        others.sort()
        return [prods, rels, others]

    def semantic_hash(self) -> str:
        blob = json.dumps(self.semantic_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def intent_texts(self) -> dict[str, str]:
        """IFC 에 복제된 의도 (일관성 시험용)."""
        out = {}
        for eid, e in self.ents.items():
            p = ue.get_pset(e, INTENT_PSET, should_inherit=False)
            if p is not None:
                out[eid] = p.get("Intent")
        return out

    def validate(self, express: bool = True) -> dict:
        lg = ifcopenshell.validate.json_logger()
        try:
            ifcopenshell.validate.validate(self.f, lg, express_rules=express)
        except Exception as ex:  # 검증기 자체의 실패도 오류로 센다
            return {"errors": 1, "warnings": 0, "samples": [f"검증기 예외: {ex!r}"]}
        errs = [s for s in lg.statements if s["level"] in ("error", "critical")]
        warns = [s for s in lg.statements if s["level"] == "warning"]
        return {"errors": len(errs), "warnings": len(warns), "samples": [s["message"][:200] for s in errs[:8]]}

    def write(self, path: str) -> None:
        self.f.write(path)
