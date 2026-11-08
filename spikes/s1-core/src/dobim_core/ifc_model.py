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
IFC_CLASS = {"level": "IfcBuildingStorey", "wall": "IfcWall", "door": "IfcDoor"}
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


def detect_skeleton(f):
    """코어가 직접 만드는 뼈대(IfcProject "dobim" > IfcSite "Site" > IfcBuilding "Building", 항등 배치, 다른 속성 없음)인가.
    맞으면 (site, building) 을 돌려준다. 이 둘은 요소가 아니라 투영이 소유한 기반 구조다(가져오기에서 요소 행을 만들지 않는다).
    아니면 None: 외래 파일의 사이트·건물은 외래 요소다."""
    projs, sites, blds = f.by_type("IfcProject"), f.by_type("IfcSite"), f.by_type("IfcBuilding")
    if len(projs) != 1 or len(sites) != 1 or len(blds) != 1:
        return None
    proj, site, bld = projs[0], sites[0], blds[0]
    if proj.Name != "dobim" or site.Name != "Site" or bld.Name != "Building":
        return None
    for e in (site, bld):
        for k, v in e.get_info(include_identifier=False, recursive=False).items():
            if k not in ("type", "GlobalId", "Name", "ObjectPlacement", "OwnerHistory", "CompositionType") and v is not None:
                return None
    if ue.get_aggregate(site) != proj or ue.get_aggregate(bld) != site:
        return None

    def identity(pl, rel_to):
        if pl is None or pl.PlacementRelTo != rel_to:
            return False
        a = pl.RelativePlacement
        return (tuple(a.Location.Coordinates) == (0.0, 0.0, 0.0) and tuple(a.Axis.DirectionRatios) == (0.0, 0.0, 1.0)
                and tuple(a.RefDirection.DirectionRatios) == (1.0, 0.0, 0.0))

    if not identity(site.ObjectPlacement, None) or not identity(bld.ObjectPlacement, site.ObjectPlacement):
        return None
    return site, bld


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
        if e.is_a("IfcProduct"):
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
    형상 표현·배치·속성(이름·치수)·공간 소속·집합 소속, 문이면 개구부 관계(개구부의 형상·배치·벽). 속성셋·재료는 IFC 가 정본이라 뺀다
    (속성만 바뀐 요소는 분리되지 않는다). 다른 요소를 가리키는 배치·관계는 그 요소의 GlobalId 로 가리킨다(요소의 형상이 바뀐 것이 의존
    요소의 클로저를 바꾸지 않게). 코어가 만든 뼈대(site/building)는 GlobalId 가 프로젝트마다 달라서 값으로 펼친다."""

    def __init__(self, f, infra: set[str]):
        self.f = f
        self.infra = infra
        self.val, _ = make_norm()

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
            owners = [o for o in self.f.get_inverse(rel) if o.is_a("IfcProduct") and o.ObjectPlacement == rel]
            if len(owners) == 1 and owners[0].GlobalId not in self.infra:
                out["to"] = {"ref": owners[0].GlobalId}
            else:
                out["to"] = self.place(rel)
        return out

    def shape(self, e, skip=("GlobalId", "OwnerHistory", "ObjectPlacement")):
        info = e.get_info(include_identifier=False, recursive=False)
        t = info.pop("type")
        return {"t": t, "attrs": {k: self.val(v) for k, v in info.items() if k not in skip}, "placement": self.place(e.ObjectPlacement)}

    def of(self, e) -> dict:
        out = self.shape(e)
        inv = self.f.get_inverse(e)
        out["contained"] = sorted(self.ref(r.RelatingStructure) for r in inv
                                  if r.is_a("IfcRelContainedInSpatialStructure") and e in r.RelatedElements)
        out["aggregated"] = sorted(self.ref(r.RelatingObject) for r in inv if r.is_a("IfcRelAggregates") and e in r.RelatedObjects)
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

    def digest(self, e) -> tuple[str, dict]:
        c = self.of(e)
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
        else:
            out.append({"t": "클래스", "contained": "공간 소속", "aggregated": "집합 소속"}.get(k, k))
    return out


def opening_guid(door_id: str) -> str:
    """저작 문의 개구부 GlobalId: 문 id 만의 결정적 함수다. 프로젝트 UUID 에 기대지 않으므로 내보낸 파일을 다른 프로젝트로 가져와
    다시 투영해도(또는 삭제·실행 취소로 개구부를 다시 만들어도) 같은 값이 나온다(S2)."""
    return ifcopenshell.guid.compress(uuid.uuid5(_NS, f"opening:{door_id}").hex)


class IfcModel:
    def __init__(self, project_guid: str, base: bytes | None = None):
        self.project_guid = project_guid
        self.base_mode = base is not None
        self.ents: dict[str, Any] = {}
        self.openings: dict[str, Any] = {}      # 문 id -> 개구부 엔티티 (투영 소유)
        self.container_of: dict[str, str] = {}  # 요소 id -> 현재 담긴 층 id (투영 상태)
        self._mats: dict[str, Any] = {}         # 재료 이름 -> 엔티티
        # 복원까지 실패해 IFC 쪽이 로그와 어긋났을 수 있으면 그 이유가 들어간다. 엔진이 이 표시를 보고 로그에서
        # 투영을 다시 만든다(만들지 못하면 쓰기를 거부한다). 예외를 삼키고 넘어가는 일은 없어야 한다.
        self.tainted: str | None = None
        self.infra: set[str] = set()            # 코어가 만든 뼈대(site/building)의 GlobalId: 요소가 아니다
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
        for m in self.f.by_type("IfcMaterial"):
            self._mats.setdefault(m.Name, m)

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

    def prune(self) -> None:
        """가져온 뒤 지워진 저작 요소의 엔티티를 작업 사본에서 없앤다(요소·뼈대·저작 문의 개구부가 아닌 제품)."""
        keep = set(self.ents) | self.infra | {o.GlobalId for o in self.openings.values()}
        gone = [g for g in self._by_gid if g not in keep]
        for g in sorted(gone, key=lambda g: (not self._by_gid[g].is_a("IfcOpeningElement"), g)):
            try:
                e = self.f.by_guid(g)
            except RuntimeError:
                continue
            if e.is_a("IfcOpeningElement"):
                api_feature.remove_feature(self.f, feature=e)
            else:
                api_root.remove_product(self.f, product=e)

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
            return
        rep = shape.Representations[0]
        old = list(rep.Items)
        rep.Items = [solid]
        for o in old:
            ue.remove_deep2(f, o)

    # ------------------------------------------------------------ 신원 (즉시)
    def create_identity(self, eid: str, kind: str) -> None:
        f = self.f
        if self.base_mode:
            e = self._by_gid.get(eid)
            if e is not None:   # 가져온 파일에 이미 있는 요소: 새로 만들지 않고 이어받는다
                self.ents[eid] = e
                c = ue.get_container(e, should_get_direct=True)
                if c is not None:
                    self.container_of[eid] = c.GlobalId
                if kind == "door":
                    for r in f.get_inverse(e):
                        if r.is_a("IfcRelFillsElement") and r.RelatedBuildingElement == e:
                            self.openings[eid] = r.RelatingOpeningElement
                return
            if kind == "foreign":
                raise DobimError("INTERNAL", f"가져온 파일에 외래 요소가 없다: {eid}")
            self.require_authoring()
        e = api_root.create_entity(f, ifc_class=IFC_CLASS[kind])
        e.GlobalId = eid
        if kind == "level":
            api_aggregate.assign_object(f, products=[e], relating_object=self.building)
            e.ObjectPlacement = self._placement(self.building.ObjectPlacement)
        self.ents[eid] = e

    def remove_identity(self, eid: str) -> None:
        f = self.f
        e = self.ents[eid]
        self._unproject_door(eid)
        api_root.remove_product(f, product=e)   # 실패하면 ents 가 그대로라 상태가 설명 가능하다(순서: 지운 뒤에 목록에서 뺀다)
        self.ents.pop(eid)
        self.container_of.pop(eid, None)
        if self.base_mode:
            self._by_gid.pop(eid, None)   # 지운 엔티티를 실행 취소가 이어받지 않게

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
        cur = ue.get_pset(e, pset, should_inherit=False)
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
        {"level": self._project_level, "wall": self._project_wall, "door": self._project_door}[kind](eid, spec)
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
        if eid in self.container_of:
            api_spatial.unassign_container(f, products=[e])
        api_spatial.assign_container(f, products=[e], relating_structure=self.ents[level_id])
        self.container_of[eid] = level_id

    def _project_wall(self, eid, spec):
        e = self.ents[eid]
        e.Name = spec["name"]
        x, y = spec["origin"]
        c, sn = spec["xdir"]
        # 층에 담는 호출이 배치를 건드릴 수 있으므로 담은 뒤에 배치를 확정한다
        self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, (x, y, 0.0), (c, sn, 0.0))
        self._set_body(e, self._solid(spec["footprint"], spec["height"]))

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
        # 개구부는 투영이 소유한다: 문이 바뀌면 다시 만들고, 문 id 에서 결정적 GlobalId 를 얻는다.
        self._unproject_door(eid)
        op = api_root.create_entity(f, ifc_class="IfcOpeningElement")
        op.GlobalId = opening_guid(eid)
        op.Name = spec["name"] + "-opening"
        self._place(op, wall.ObjectPlacement, origin, (1.0, 0.0, 0.0))
        op.Representation = f.createIfcProductDefinitionShape(None, None, [
            f.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", [self._solid(rect, spec["height"])])])
        api_feature.add_feature(f, feature=op, element=wall)
        api_feature.add_filling(f, opening=op, element=e)
        self.openings[eid] = op

    # ------------------------------------------------------------ 의미 해시·검증·내보내기
    def semantic_dump(self) -> list:
        """STEP 번호와 무작위 GlobalId 를 뺀 정규화 덤프. OS·실행 순서와 무관해야 한다 (S0 발견 7)."""
        val, ent = make_norm()

        f = self.f
        prods = []
        for e in f.by_type("IfcProduct"):
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
