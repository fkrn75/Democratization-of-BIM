# SPDX-License-Identifier: MPL-2.0
"""IFC 쪽 상태와 투영 (ADR-0005).

정본 분담:
- IFC 가 정본인 것: 속성셋, 재료. 이 모듈이 ifcopenshell.api 로만 고친다.
- 파생물: 저작 요소의 형상·배치. spec(순수 값)에서만 만들고 직접 편집 경로는 없다.
- 요소의 신원(GlobalId + 클래스)은 요소 생성 즉시 만들고, 형상·관계 투영은 flush 에서 늦게 한다.

이 모듈만 ifcopenshell.file 을 수정한다. 커맨드는 엔진의 효과(effect)를 통해서만 여기에 닿는다.
"""
from __future__ import annotations

import binascii
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
import ifcopenshell.api.style as api_style
import ifcopenshell.api.unit as api_unit
import ifcopenshell.guid
import ifcopenshell.util.element as ue
import ifcopenshell.util.unit as uunit
import ifcopenshell.validate

from dobim_core import appearance as AP
from dobim_core import arcgeom as AG
from dobim_core import wallcut as WC
from dobim_core.errors import DobimError
from dobim_core.kinds import FIXED_IFC_CLASS, SLAB_OPENINGS_MAX, STAIR_LANDINGS_MAX, parapet_ring, roof_geom, stair_layout
from dobim_core import roofeaves as RE
from dobim_core.solids import OPENINGS_MAX
from dobim_core.roofpoly import FACES_MAX as _POLY_FACES_MAX   # 다각형 외곽 지붕(W1 1-F 4차)의 경사면 수 상한(직사각형은 4)
from dobim_core.roofskel import FACES_MAX as _SKEL_FACES_MAX   # 외곽선 지붕(W2 여덟 번째 PR)의 경사면 수 상한
ROOF_FACES_MAX = max(_POLY_FACES_MAX, _SKEL_FACES_MAX)
from dobim_core.hostvoid import VOID_MARGIN   # 지붕·천장 개구 솔리드 여유(W2 열네 번째 PR)
from dobim_core.kinds import _plane_of as plane_of
from dobim_core.family import ifcproj as fp
from dobim_core.viewdefs import DEFS_PSET


def _poly_roof_type(slopes) -> str:
    """다각형 외곽 지붕(W1 1-F 4차)의 IfcRoof PredefinedType: 경사 변 하나 = 외쪽, 모두 경사 = 모임, 박공 변이 섞이면 모임 박공."""
    n = sum(1 for v in slopes if v > 0)
    return "SHED_ROOF" if n == 1 else ("HIP_ROOF" if n == len(slopes) else "HIPPED_GABLE_ROOF")


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


def pairs_label(layers) -> str:
    """층 구성 ((재료, 두께), ...) 의 대표 재료: 가장 두꺼운 층(같으면 앞쪽). kinds.layers_label 과 같은 규칙이다."""
    best = layers[0]
    for x in layers[1:]:
        if x[1] > best[1]:
            best = x
    return best[0]


LAYER_MARK = "DoBIM"   # 코어가 투영한 층 구성(IfcMaterialLayerSet)의 Description. 외래 파일의 층 구성과 구분한다
GRID_MARK = "DoBIM grid"   # 통심선 IfcGrid 투영(W2 열 번째 PR)의 Description 머리: 투영이 소유한다는 표식(뒤에 명세 서명)
GROUP_MARK = "DoBIM model group"   # 모델 그룹 IfcGroup 투영(W2 열한 번째 PR)의 Description 머리(뒤에 배치 id)
ASM_MARK = "DoBIM assembly"        # 조립품 IfcElementAssembly 투영(W2 열한 번째 PR)의 Description 머리(뒤에 조립품 id)
WALLTYPE_MARK = "DoBIM wall type"   # 벽 유형 투영(W2 일곱 번째 PR)의 IfcRelDefinesByType·유형 층 집합 Description: 투영이 소유한다는 표식
RAIL_PART_LABELS = ("balusters", "posts", "rails")   # 유형 난간의 부재 이름표(W2 열세 번째 PR, 이름표 순서 = 만드는 순서)
RAILTYPE_MARK = "DoBIM railing type"   # 난간 유형 투영(W2 열세 번째 PR)의 표식(벽·지붕 유형과 같은 쓰임)
BEAMTYPE_MARK = "DoBIM beam type"   # 보 유형 투영(W2 열다섯 번째 PR)의 표식(벽·지붕·난간 유형과 같은 쓰임)
ROOFTYPE_MARK = "DoBIM roof type"   # 지붕 유형 투영(W2 여덟 번째 PR)의 표식(벽 유형과 같은 쓰임)
# 처마 부속(W2 여덟 번째 PR, ARC-20)의 IFC 클래스·PredefinedType·ObjectType. IFC4 IfcCoveringTypeEnum 에 FASCIA·SOFFIT 가 없어 USERDEFINED + ObjectType 로 적는다
EAVE_IFC = {"fascia": ("IfcCovering", {"PredefinedType": "USERDEFINED", "ObjectType": "FASCIA"}),
            "soffit": ("IfcCovering", {"PredefinedType": "USERDEFINED", "ObjectType": "SOFFIT"}),
            "gutter": ("IfcPipeSegment", {"PredefinedType": "GUTTER"})}


def our_usage(m) -> bool:
    """코어가 투영한 층 구성 지정(IfcMaterialLayerSetUsage)인가. 이 지정은 투영이 소유한다(재료 이름 하나로 읽거나 부속 스냅숏으로 다루지 않는다)."""
    if m is None:
        return False
    if m.is_a("IfcMaterialProfileSetUsage"):   # 보의 구조 재료(W2 열여덟 번째 PR): 코어가 투영한 단면 재료 집합 지정도 같은 규칙이다
        return m.ForProfileSet is not None and getattr(m.ForProfileSet, "Description", None) == LAYER_MARK
    return (m.is_a("IfcMaterialLayerSetUsage") and m.ForLayerSet is not None
            and getattr(m.ForLayerSet, "Description", None) == LAYER_MARK)


def usage_set(m):
    """코어 지정(our_usage)이 가리키는 재료 집합(층 집합 또는 단면 집합)."""
    return m.ForProfileSet if m.is_a("IfcMaterialProfileSetUsage") else m.ForLayerSet


def is_site_element(e) -> bool:
    """대지 요소(site 종류)의 IFC 엔티티인가: IfcSite 이고 다른 IfcSite 에 집합돼 있고 의도(DoBIM_Intent)를 갖는다."""
    if not e.is_a("IfcSite"):
        return False
    par = ue.get_aggregate(e)
    return par is not None and par.is_a("IfcSite") and ue.get_pset(e, INTENT_PSET, should_inherit=False) is not None


def skeleton_check(f):
    """코어가 직접 만드는 뼈대(IfcProject "dobim" > IfcSite "Site" > IfcBuilding "Building")를 구조로 알아본다. (뼈대 | None, 경고 | None).
    인식 기준은 구조(프로젝트·사이트·건물이 각각 하나이고 집합 사슬이 이어짐), 이름, 형상 없음, 항등 배치다.
    위경도·주소·설명·긴 이름 같은 **비형상 속성은 보지 않는다**: 외부 도구가 사이트에 위경도만 적어도 뼈대는 뼈대다(요소 분류에 영향이 없다).
    형상(표현·배치)이 바뀐 뼈대는 그대로 두면 아래 요소의 배치 기준이 달라지므로 보수적으로 뼈대로 보지 않고(사이트·건물은 외래 요소가 되고
    저작 요소는 분리된다) 그 사실을 경고로 알린다. GlobalId 는 프로젝트 UUID 에서 파생돼 파일마다 달라 기준으로 쓸 수 없다."""
    # 대지 요소(site 종류: 뼈대 대지 아래의 하위 IfcSite + DoBIM_Intent)는 뼈대가 아니다: 세지 않는다
    projs, sites, blds = f.by_type("IfcProject"), [s for s in f.by_type("IfcSite") if not is_site_element(s)], f.by_type("IfcBuilding")
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
#   "3" 슬래브 종류 신설(IfcSlab 이 의도 복원 대상: 가져온 프로젝트를 다시 판정한다)
#   "4" 계단 종류 신설(IfcStair 이 의도 복원 대상)
#   "5" 공간 종류 신설(IfcSpace 이 의도 복원 대상)
#   "6" 시트 정의 신설(비 IFC 정의 속성에 sheet 가 더해진다)
#   "7" L 조인 모서리를 코어 형상으로 닫는다(벽 윤곽 wall_footprint 가 queries.wall_extent 를 쓴다: id 작은 벽이 상대 벽 반 두께 연장).
#       이전 형식으로 내보낸 IFC 의 L 조인 벽은 형상이 달라 가져오면 분리(동결)로 판정된다(안전한 쪽)
#   "8" 대지 종류 신설(IfcSite 이 의도 복원 대상: 뼈대 대지 아래 하위 IfcSite 로 투영하고 뼈대 판정은 그것을 세지 않는다)
#   "9" 벽·슬래브 층 구성(layers), 문 문짝 두께(leaf), 공간 천장 높이(ceiling_height) 선택 파라미터 신설. 층 구성은 IfcMaterialLayerSetUsage 로 투영한다
#       (재료 지정은 클로저 밖이라 클로저 해시는 그대로이고, 의도 해석 모양만 넓어졌다). 입력이 없는 프로젝트는 요소 행·IFC 가 이전과 같다
#   "10" 꺾인 계단(층참)과 슬래브 구멍. 꺾인 계단은 IfcStair 아래 IfcStairFlight 둘과 층참 IfcSlab(LANDING)으로 집합하고, 슬래브 구멍은 IfcOpeningElement +
#       IfcRelVoidsElement 로 투영한다(둘 다 투영이 소유하는 부속: part_guid 로 GlobalId 가 결정적이다). 클로저에 부속의 형상이 들어간다.
#       직선 계단·구멍 없는 슬래브는 요소 행·IFC·클로저가 이전과 같다
#   "11" 승강기(IfcTransportElement ELEVATOR, 카 상자 한 몸체)와 지붕 슬래브(IfcSlab PredefinedType ROOF, 배수 물매·배수구는 의도에만 둔다).
#       승강기 종류가 없고 슬래브가 지붕이 아닌 프로젝트는 요소 행·IFC·클로저가 이전과 같다
#   "12" 대지의 선택 속성(지반 표고점·조경/공개공지 영역·주차 계획·간판)과 대지 단면 뷰(section 의 with_site). 모두 의도(DoBIM_Intent)·뷰 정의로 왕복하고
#       IFC 형상은 바뀌지 않는다. 이 속성이 없는 프로젝트는 요소 행·IFC·클로저가 이전과 같다
#   "13" 뷰의 선택 옵션(crop 잘라내기·dims 계단/승강기 치수 자동 표기·finish 입면 외부 마감)과 시트 방위 각 기본값(대지의 정북 각). 뷰 정의(DoBIM_Defs)로 왕복하고
#       IFC 형상은 바뀌지 않는다. 이 옵션이 없는 뷰는 정의·IFC·클로저가 이전과 같다
#   "14" 경사 지붕(IfcRoof 아래 경사면마다 IfcSlab ROOF 를 집합한다)과 지붕 슬래브의 파라펫(슬래브에 집합한 IfcWall PARAPET). 클로저에 슬래브의 집합 부속이
#       더해졌다(이전에는 계단만 부속을 담았다: 외부 도구가 슬래브에 부속을 붙인 파일은 가져올 때 분리로 판정된다, 안전한 쪽). 승강기의 선택 입력(피트·기계실·승강장 문·
#       소유 슬래브 구멍)은 의도로만 왕복하고 IFC 형상은 그대로다. 지붕·파라펫·부속이 없는 프로젝트는 요소 행·IFC·클로저가 이전과 같다
#   (15단계: 올리지 않았다. 난간의 출처 표식(src·src_tag·src_inset)은 의도로만 왕복하고, 섬 구멍 있는 공간(holes)의 몸체는 IfcArbitraryProfileDefWithVoids 로 나가며 클로저가
#    형상을 그대로 펼쳐 담는다. 구멍이 없는 공간·출처가 없는 난간은 요소 행·IFC·클로저가 이전과 같다)
#   "15" 일반 솔리드 종류 신설(IfcFacetedBrep 한 몸체, 클래스는 범주가 정하고 가져오기는 의도가 밝힌 종류 solid 를 따른다)과 벽의 모양 개구부(벽이 소유하는
#       IfcOpeningElement 부속: 클로저에 벽을 뚫는 개구부로 들어간다). 솔리드가 없고 벽에 openings 가 없는 프로젝트는 요소 행·IFC·클로저가 이전과 같다
CLOSURE_FORMAT = "15"


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
        out["aggregated"] = sorted(self.ref(r.RelatingObject) for r in _rels_of(e, inv, "Decomposes", "IfcRelAggregates", "RelatedObjects")
                                   if not (r.RelatingObject.is_a("IfcElementAssembly") and _asm_owned(r.RelatingObject)))   # 조립품 소속(W2 열한 번째 PR)은 정의가 정본이라 형상 클로저가 아니다
        # 이 요소를 뚫는 개구부들. 개구부의 형상은 채우는 문의 클로저가 소유하므로 여기서는 "누가 채우는가"만 본다(GlobalId 는 신원이라
        # 외부 도구가 바꿔도 형상이 아니다). 채우는 요소가 없는 개구부나 외래 요소가 채우는 개구부가 더해지면 벽이 달라진 것이다
        voids = []
        for r in inv:
            if r.is_a("IfcRelVoidsElement") and r.RelatingBuildingElement == e:
                op = r.RelatedOpeningElement
                v = {"filler": sorted(self.ref(x.RelatedBuildingElement) for x in self.f.get_inverse(op)
                                      if x.is_a("IfcRelFillsElement"))}
                if e.is_a("IfcSlab"):   # 슬래브 구멍은 채우는 요소가 없어 구멍의 형상(경계·위치)을 슬래브가 소유한다
                    v["shape"] = self.shape(op)
                voids.append(v)
        if voids:
            out["voids"] = sorted(voids, key=lambda x: json.dumps(x, sort_keys=True))
        if e.is_a("IfcStair") or e.is_a("IfcRoof") or e.is_a("IfcSlab") or e.is_a("IfcCurtainWall"):   # 커튼월의 멀리언·패널(W2 ARC-22)도   # 꺾인 계단의 부속(단 묶음·층참)·지붕의 경사면·슬래브의 파라펫은 그 요소가 소유한다
            kids = [self.shape(k) for r in inv if r.is_a("IfcRelAggregates") and r.RelatingObject == e for k in r.RelatedObjects]
            if kids:
                out["parts"] = sorted(kids, key=lambda x: json.dumps(x, sort_keys=True))
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
        elif k == "parts":
            out.append("계단 부속(단 묶음·층참)")
        else:
            out.append({"t": "클래스", "contained": "공간 소속", "aggregated": "집합 소속", "typed_by": "타입 지정"}.get(k, k))
    return out


_NS_BYTES = _NS.bytes
# IFC GlobalId 22자는 128비트 수를 6비트씩(첫 글자는 위 2비트) IFC 자모로 적은 것이다. 앞에 0 4비트·뒤에 0 12비트를 붙인 18바이트를
# 표준 base64 로 적으면 앞 22자가 같은 6비트 묶음이 되므로, 자모만 바꿔 끼운다
_GUID64 = bytes.maketrans(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
                          b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$")


def _guid5(name: str) -> str:
    """ifcopenshell.guid.compress(uuid.uuid5(_NS, name).hex) 와 같은 값을 UUID 객체·정규식 없이 바로 계산한다.
    프로젝트를 열 때 벽마다 부속 이름표 수십 개의 GlobalId 를 맞춰 보므로(수십만 회) 이 경로가 열기 시간의 큰 몫이었다.
    같음은 tests/test_guid5.py 가 원래 식과 대조해 지킨다."""
    h = bytearray(hashlib.sha1(_NS_BYTES + name.encode("utf-8")).digest()[:16])
    h[6] = (h[6] & 0x0F) | 0x50   # 판 5
    h[8] = (h[8] & 0x3F) | 0x80   # RFC 4122 변형
    b = (int.from_bytes(h, "big") << 12).to_bytes(18, "big")
    return binascii.b2a_base64(b, newline=False)[:22].translate(_GUID64).decode("ascii")


def part_guid(eid: str, label: str) -> str:
    """투영이 소유하는 부속(꺾인 계단의 단 묶음·층참, 슬래브 구멍)의 GlobalId: 요소 id 와 이름표만의 결정적 함수다(opening_guid 와 같은 이유)."""
    return _guid5(f"part:{eid}:{label}")


def _asm_owned(e) -> bool:
    """투영이 만든 조립품 IfcElementAssembly 인가(W2 열한 번째 PR, foreign.asm_owned 와 같은 판정)."""
    d = getattr(e, "Description", None)
    return isinstance(d, str) and d.startswith(ASM_MARK + " ") and len(d) == len(ASM_MARK) + 23 and e.GlobalId == part_guid(d[-22:], "assembly")


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
    if r.is_a("IfcRelDefinesByType") and getattr(r, "Description", None) in (WALLTYPE_MARK, ROOFTYPE_MARK):   # 벽·지붕 유형 지정(W2 일곱·여덟 번째 PR)은 투영이 소유한다
        return True
    if r.is_a("IfcRelDefinesByProperties"):
        d = r.RelatingPropertyDefinition
        return isinstance(d, ifcopenshell.entity_instance) and d.is_a("IfcPropertySet")
    if r.is_a("IfcRelAssociatesMaterial"):
        return r.RelatingMaterial.is_a("IfcMaterial") or our_usage(r.RelatingMaterial)
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
        self.parts: dict[str, dict[str, Any]] = {}   # 요소 id -> {이름표: 부속 엔티티} (꺾인 계단의 단 묶음·층참, 슬래브 구멍. 투영 소유)
        self._cw_sig: dict[str, dict[str, tuple]] = {}   # 커튼월 id -> {이름표: 마지막으로 투영한 부속 명세} (같으면 다시 쓰지 않는다, W2 ARC-22)
        self._cw_axis: dict[str, Any] = {}   # 커튼월 id -> 부속 배치가 함께 쓰는 단위 IfcAxis2Placement3D
        self._cw_mats: dict[str, dict] = {}   # 커튼월 id -> {부속 종류: 재료 이름} 커튼월별 재료(W2 열 번째 PR, 없으면 기본 재료)
        self._wt_sig: dict[str, tuple] = {}   # 벽 유형 id -> 마지막으로 맞춘 (이름, 기능, 두께, 층 구성) (W2 일곱 번째 PR)
        self._wt_scanned = False              # 가져온 파일의 낡은 벽 유형을 한 번 훑었는가
        self._rt_sig: dict[str, tuple] = {}   # 지붕 유형 id -> 마지막으로 맞춘 (이름, 기능, 두께, 층 구성) (W2 여덟 번째 PR)
        self._rt_scanned = False
        self._lt_sig: dict[str, tuple] = {}   # 난간 유형 id -> 마지막으로 맞춘 (이름, 구성) (W2 열세 번째 PR)
        self._lt_scanned = False
        self._bt_sig: dict[str, tuple] = {}   # 보 유형 id -> 마지막으로 맞춘 (이름,) (W2 열다섯 번째 PR)
        self._bt_scanned = False
        self._mt_sig: dict[str, tuple] = {}   # 가새가 쓰는 보 유형 id -> 마지막으로 맞춘 (이름, 재료) (IfcMemberType, W2 스물네 번째 PR)
        self._mt_scanned = False
        self._grid_ent = None                 # 투영한 IfcGrid(W2 열 번째 PR, ARC-38) 또는 None
        self._grid_sig: str | None = None     # 그 IfcGrid 의 명세 서명(Description 끝에 적는다: 같으면 다시 쓰지 않는다)
        self._grid_scanned = False            # 가져온 파일의 투영 IfcGrid 를 한 번 훑었는가
        self._grp_sig: dict[str, tuple] = {}  # 모델 그룹 배치 id -> 마지막으로 맞춘 명세(W2 열한 번째 PR, IfcGroup)
        self._asm_sig: dict[str, tuple] = {}  # 조립품 id -> 마지막으로 맞춘 명세(W2 열한 번째 PR, IfcElementAssembly)
        self._grp_scanned = False             # 가져온 파일의 투영 IfcGroup·IfcElementAssembly 를 한 번 훑었는가
        self._eave_mats: dict[str, dict] = {}   # 지붕 id -> {처마 부속 종류: 재료 이름} (W2 아홉 번째 PR, 마지막 투영의 값)
        self.container_of: dict[str, str] = {}  # 요소 id -> 현재 담긴 층 id (투영 상태)
        self._mats: dict[str, Any] = {}         # 재료 이름 -> 엔티티
        self._appearance: dict[str, tuple] = {}   # 재료 외관 정의(W2 다섯 번째 PR): 재료 이름 -> (r,g,b,투명도,광택). 엔진이 정의 행에서 맞춘다
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
        self._axis: Any = None                  # 'Axis' 하위 문맥(곡선 벽 W2 ARC-04 이 처음 쓸 때 찾거나 만든다)
        # 복원까지 실패해 IFC 쪽이 로그와 어긋났을 수 있으면 그 이유가 들어간다. 엔진이 이 표시를 보고 로그에서
        # 투영을 다시 만든다(만들지 못하면 쓰기를 거부한다). 예외를 삼키고 넘어가는 일은 없어야 한다.
        self.tainted: str | None = None
        self.infra: set[str] = set()            # 코어가 만든 뼈대(site/building)의 GlobalId: 요소가 아니다
        self.base_ents: set[str] = set()        # 가져온 파일의 엔티티를 그대로 이어받은 요소 id (아직 이 세션에서 다시 만들지 않은 것)
        self._opening_gid: dict[str, str] = {}  # 문 id -> 개구부 GlobalId (가져온 파일의 값을 다시 투영해도 유지한다)
        self.pending_styles: dict[str, list] = {}   # 지우기를 되돌린 요소 id -> 새 몸체 솔리드에 입힐 스타일 스냅숏
        # 뷰·주석 정의(ADR-0015): IfcProject 의 속성셋 DoBIM_Defs 에서 이 모델이 소유한 속성(이름 = 요소 id)의 현재 텍스트.
        # 소유하지 않은 속성(되살리지 않은 외래·손상 정의)은 건드리지 않는다. 정의가 하나도 없으면 속성셋도 만들지 않는다(정의 없는 프로젝트의 IFC 는 그대로다).
        self._defs_text: dict[str, str] = {}
        # 가져온 파일에 있던, 이 모델이 소유하지 않은 정의 속성의 원본: {이름: (값 타입 이름, 값) | None(단일값 속성이 아니라 복원 불가)}.
        # 같은 id 의 정의를 새로 만들면 이 속성을 덮어쓰고, 그 정의가 사라지면(지우기·되돌리기) 원본을 되돌려 놓는다 → 경로와 무관하게 결과가 같다.
        self._ext_defs: dict[str, tuple | None] = {}
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
        self.site_root = site                   # 대지 요소(site 종류)가 집합될 뼈대 대지
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
        self.site_root = sk[0] if sk is not None else None
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
        keep = (set(self.ents) | self.infra | {o.GlobalId for o in self.openings.values()}
                | {p.GlobalId for ps in self.parts.values() for p in ps.values()} | self.grid_owned_gids())   # 통심선 IfcGrid(W2 열 번째 PR)는 정의 동기화가 맞춘다
        gone = [g for g in self._by_gid if g not in keep]
        mats, lsets = [], []
        for g in sorted(gone, key=lambda g: (not self._by_gid[g].is_a("IfcOpeningElement"), g)):
            try:
                e = self.f.by_guid(g)
            except RuntimeError:
                continue
            if e.is_a("IfcOpeningElement"):
                api_feature.remove_feature(self.f, feature=e)
            else:
                m = ue.get_material(e, should_inherit=False)
                if our_usage(m):
                    lsets.append(usage_set(m))
                elif m is not None:
                    mats.append(m)
                self._remove_product(e)
        for m in mats:   # 지운 요소만 쓰던 재료는 세션 안에서 지울 때와 같이 없앤다(다시 열어도 같은 해시)
            try:
                self._gc_material(m)
            except RuntimeError:
                pass   # 이미 지워졌다
        for ls in lsets:
            try:
                self._drop_layer_set(ls)
            except RuntimeError:
                pass
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
        """기존 표현이 있으면 항목만 교체한다. 표현·배치 엔티티는 유지해 다른 요소의 참조가 끊기지 않는다. solid 는 목록일 수 있다(스타일은 첫 항목에 입힌다)."""
        f = self.f
        items = list(solid) if isinstance(solid, (list, tuple)) else [solid]
        solid = items[0]
        shape = product.Representation
        if shape is None:
            rep = f.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", items)
            product.Representation = f.createIfcProductDefinitionShape(None, None, [rep])
            self._restore_style(product, solid)
            return
        rep = shape.Representations[0]
        old = list(rep.Items)
        rep.Items = items
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
                if kind in ("space", "site"):   # 공간은 층에, 대지는 뼈대 대지에 집합(IfcRelAggregates)으로 분해된다
                    if e.Decomposes:
                        self.container_of[eid] = e.Decomposes[0].RelatingObject.GlobalId
                elif kind != "ftype":
                    c = ue.get_container(e, should_get_direct=True)
                    if c is not None:
                        self.container_of[eid] = c.GlobalId
                if kind in ("door", "finst"):
                    for r in f.get_inverse(e):
                        if r.is_a("IfcRelFillsElement") and r.RelatedBuildingElement == e:
                            self.openings[eid] = r.RelatingOpeningElement
                            self._opening_gid[eid] = r.RelatingOpeningElement.GlobalId
                if kind in ("slab", "stair", "roof", "wall", "railing", "solid"):   # 난간 부재(W2 열세 번째 PR), 천장판 개구(W2 열네 번째 PR)
                    self._adopt_parts(eid, kind, e)
                elif kind == "curtwall":   # 커튼월 부속(W2 ARC-22)
                    self._cw_adopt(eid, e)
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
        if self.kinds.get(eid) == "curtwall":   # 커튼월 부속(멀리언·패널, W2 ARC-22)과 집합·재료 관계
            self._cw_sync(eid, None)
            self._cw_axis.pop(eid, None)
            self._cw_mats.pop(eid, None)
        if eid in self.parts:   # 부속(단 묶음·층참·슬래브 구멍)이 먼저 간다
            self._sync_parts(eid, {})
        self._gc_names(self._eave_mats.pop(eid, None) or {})
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
        cur_mat = ue.get_material(e, should_inherit=False)
        lset = usage_set(cur_mat) if our_usage(cur_mat) else None
        self._remove_product(e)   # 실패하면 ents 가 그대로라 상태가 설명 가능하다(순서: 지운 뒤에 목록에서 뺀다)
        if lset is not None:   # 요소와 함께 사라진 코어 층 구성(고아)을 치운다: 재료 목록이 상태의 함수여야 한다
            self._drop_layer_set(lset)
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
        own = {o.GlobalId for o in self.openings.values()} | {p.GlobalId for ps in self.parts.values() for p in ps.values()}
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
        return m is not None and not m.is_a("IfcMaterial") and not our_usage(m)

    # ------------------------------------------------------------ 속성셋·재료 (정본)
    def get_psets(self, eid: str) -> dict:
        out = {}
        for name, props in ue.get_psets(self.ents[eid], psets_only=True, should_inherit=False).items():
            if name == INTENT_PSET:
                continue
            if self.f.by_id(props["id"]).is_a("IfcPreDefinedPropertySet"):   # 창 패널(24단계) 같은 정의 속성셋은 투영(의도)의 몫이다: 일반 속성으로 읽지 않는다
                continue
            out[name] = {k: plain(v) for k, v in props.items() if k != "id"}
        return out

    def get_material(self, eid: str) -> str | None:
        m = ue.get_material(self.ents[eid], should_inherit=False)
        return None if our_usage(m) else material_label(m)   # 코어가 투영한 층 구성은 의도(층 구성)가 정본이다: 재료 이름 입력으로는 읽지 않는다

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
            if cur is not None and not our_usage(cur):   # 코어가 투영한 층 구성은 재료 이름을 뗀다고 떼지 않는다(투영이 정한다)
                api_material.unassign_material(f, products=[e])
                self._gc_material(cur)
                if eid in self.parts:
                    self._mirror_material(eid)
            return
        mat = self._named_material(name)
        ls = usage_set(cur) if our_usage(cur) else None
        api_material.assign_material(f, products=[e], material=mat)
        if ls is not None:
            self._drop_layer_set(ls)
        elif cur is not None and cur != mat:
            self._gc_material(cur)
        if eid in self.parts:   # 집합 부속(계단 조각·지붕 경사면 등)도 따른다(W2 실사용 결함 6)
            self._mirror_material(eid)

    def _named_material(self, name: str):
        mat = self._mats.get(name)
        if mat is None:
            mat = api_material.add_material(self.f, name=name)
            self._mats[name] = mat
            self._add_style(mat, name)
        return mat

    # ------------------------------------------------------------ 재료 외관(18단계 표 + W2 다섯 번째 PR 외관 정의, DATA-05·VIS-07)
    def style_want(self, name: str):
        """재료 이름 name 의 IfcMaterial 이 가져야 할 표면 스타일 서명. 외관 정의가 있으면 ("R", r, g, b, 투명도, 광택)(IfcSurfaceStyleRendering),
        없고 18단계 표에 맞으면 ("S", r, g, b)(IfcSurfaceStyleShading, 기존 그대로), 둘 다 아니면 None(스타일 없음)."""
        ap = self._appearance.get(name)
        if ap is not None:
            return ("R",) + tuple(ap)
        rgb = AP.material_rgb(name)
        return None if rgb is None else ("S",) + tuple(rgb)

    def _add_style(self, mat, name: str) -> None:
        want = self.style_want(name)
        if want is None:
            return
        f = self.f
        st = api_style.add_style(f, name=name)
        if want[0] == "S":   # 겉모습 표에 있는 재료(벽돌·동판·석재·유리 등, 18단계): 재료에 표면 스타일 색을 붙인다
            api_style.add_surface_style(f, style=st, ifc_class="IfcSurfaceStyleShading",
                                        attributes={"SurfaceColour": {"Name": None, "Red": want[1] / 255.0, "Green": want[2] / 255.0, "Blue": want[3] / 255.0}})
        else:   # 외관 정의: 색·투명도·광택(광택 = 반사 색 비율, 거칠기 = 1 - 광택. 광택 0 이면 둘 다 비운다)
            _, r, g, b, t, s = want
            col = f.createIfcColourRgb(None, r / 255.0, g / 255.0, b / 255.0)
            spec = f.create_entity("IfcNormalisedRatioMeasure", s) if s > 0 else None
            rough = f.create_entity("IfcSpecularRoughness", round(1.0 - s, 6)) if s > 0 else None
            rend = f.createIfcSurfaceStyleRendering(col, t, None, None, None, None, spec, rough, "NOTDEFINED")
            st.Styles = list(st.Styles or ()) + [rend]
        api_style.assign_material_style(f, material=mat, style=st, context=self.body)

    @staticmethod
    def style_sig(mat):
        """재료에 지금 붙은 표면 스타일의 서명(style_want 와 같은 꼴). 스타일이 없으면 None, 코어가 만들 수 없는 모양(여러 스타일·텍스처·
        IFC2x3 의 스타일 묶음 등)이면 "other"."""
        reps = list(getattr(mat, "HasRepresentation", None) or ())
        if not reps:
            return None
        items = [it for rep in reps for r in rep.Representations for it in r.Items]
        sts = [st for it in items for st in (getattr(it, "Styles", None) or ())]
        if len(sts) == 1 and sts[0].is_a("IfcPresentationStyleAssignment"):   # IFC2x3 에서 올린 파일: 스타일 묶음 하나는 벗겨 본다
            sts = list(sts[0].Styles or ())
        if len(reps) != 1 or len(items) != 1 or len(sts) != 1 or not sts[0].is_a("IfcSurfaceStyle") or len(sts[0].Styles or ()) != 1:
            return "other"
        ss = sts[0].Styles[0]
        c = getattr(ss, "SurfaceColour", None)
        if c is None or not c.is_a("IfcColourRgb"):
            return "other"
        rgb = tuple(int(round(max(0.0, min(1.0, v)) * 255)) for v in (c.Red, c.Green, c.Blue))
        if ss.is_a() == "IfcSurfaceStyleShading":
            return ("S",) + rgb if getattr(ss, "Transparency", None) is None else "other"
        if ss.is_a() == "IfcSurfaceStyleRendering":
            sp = ss.SpecularColour
            s = float(sp.wrappedValue) if sp is not None and sp.is_a("IfcNormalisedRatioMeasure") else 0.0
            if (sp is not None and not sp.is_a("IfcNormalisedRatioMeasure")) or any(getattr(ss, k) is not None for k in (
                    "DiffuseColour", "TransmissionColour", "DiffuseTransmissionColour", "ReflectionColour")):
                return "other"
            return ("R",) + rgb + (float(ss.Transparency or 0.0), s)
        return "other"

    def appearance_owned(self, name: str) -> bool:
        """이 이름의 재료 스타일을 코어가 바꿔도 되는가: IFC 에 그 재료가 없거나, 붙은 스타일이 지금 코어가 만들 모양과 같다.
        가져온 파일의 재료가 다른 도구의 스타일(텍스처·여러 스타일 등)을 가지면 False(외관 정의가 그 스타일을 지우지 않게 한다)."""
        mat = self._mats.get(name)
        return mat is None or self.style_sig(mat) == self.style_want(name)

    def appearance_of(self, name: str):
        """IFC 재료에 지금 붙은 외관 {"color", "transparency", "shininess"} 또는 None(재료나 읽을 수 있는 색이 없다). 조회용."""
        mat = self._mats.get(name)
        sig = None if mat is None else self.style_sig(mat)
        if sig is None or sig == "other":
            return None
        return {"color": list(sig[1:4]), "transparency": sig[4] if sig[0] == "R" else 0.0, "shininess": sig[5] if sig[0] == "R" else 0.0}

    def set_appearance(self, amap: dict, restyle: bool = True) -> None:
        """외관 정의 표({재료 이름: (r,g,b,투명도,광택)})를 바꾼다. restyle 이면 값이 달라진 이름의 IFC 재료 스타일을 다시 붙인다
        (재료가 IFC 에 없으면 할 일이 없다: 처음 지정될 때 _named_material 이 새 표로 붙인다). 로그에서 투영을 다시 만들 때는 재료를
        만들기 전에 restyle=False 로 표만 놓는다."""
        old, self._appearance = self._appearance, dict(amap)
        if not restyle:
            return
        for name in sorted(set(old) | set(amap)):
            if old.get(name) == amap.get(name) or name not in self._mats:
                continue
            mat = self._mats[name]
            if self.style_sig(mat) == self.style_want(name):
                continue
            self._strip_styles(mat)
            self._add_style(mat, name)

    def _strip_styles(self, mat) -> None:
        """재료 표현(IfcMaterialDefinitionRepresentation)·표현·스타일 항목·스타일 묶음과, 다른 곳이 쓰지 않는 스타일을 지운다(고아 엔티티를 남기지 않는다)."""
        f = self.f
        for mdr in list(mat.HasRepresentation or ()):
            reps = list(mdr.Representations or ())
            items = [it for r in reps for it in (r.Items or ())]
            psas, styles = [], []
            for it in items:
                for st in it.Styles or ():
                    if st.is_a("IfcPresentationStyleAssignment"):
                        psas.append(st)
                        styles.extend(st.Styles or ())
                    else:
                        styles.append(st)
            f.remove(mdr)
            for x in reps + items + psas:   # 앞의 것을 지워야 뒤의 것이 고아가 된다(재료 표현 -> 표현 -> 항목 -> 묶음)
                if not f.get_inverse(x):
                    f.remove(x)
            seen = set()
            for st in styles:
                if st.id() not in seen and not f.get_inverse(st):
                    seen.add(st.id())
                    api_style.remove_style(f, style=st)

    def _gc_names(self, names: dict) -> None:
        """처마 부속 종류별 재료(W2 아홉 번째 PR) 가운데 이제 아무도 쓰지 않는 것을 지운다. 부속을 지울 때 관계가 함께 사라져
        _mirror_material 이 옛 재료를 보지 못하는 경로(지붕 삭제·처마 부속 떼기·재료 바꾸기)를 맡는다."""
        for name in sorted(set(names.values())):
            mat = self._mats.get(name)
            if mat is not None:
                try:
                    self._gc_material(mat)
                except RuntimeError:
                    pass   # 이미 지웠다

    def _gc_material(self, mat) -> None:
        """사용처가 없는 재료는 지운다. 재료 목록이 상태의 함수가 되어야 취소 후 해시가 복원된다."""
        if mat.is_a("IfcMaterial") and not [x for x in self.f.get_inverse(mat) if not x.is_a("IfcMaterialDefinitionRepresentation")]:
            self._mats.pop(mat.Name, None)
            # 재료의 표면 스타일(18단계)도 함께 치운다(취소 후 해시 복원): 표현·스타일 항목은 remove_material 이 지우고, 스타일 자체는 여기서 지운다
            styles = [st for rep in (mat.HasRepresentation or ()) for r in rep.Representations for it in r.Items for st in (it.Styles or ())]
            api_material.remove_material(self.f, material=mat)
            for st in styles:
                if not self.f.get_inverse(st):
                    api_style.remove_style(self.f, style=st)
        elif our_usage(mat) and not self.f.get_inverse(mat):   # 지정이 끊긴 코어 층 구성: 층·층 집합·쓰던 재료까지 치운다
            self._drop_layer_set(usage_set(mat))
        elif mat.is_a("IfcMaterialLayerSet") and getattr(mat, "Description", None) == LAYER_MARK:   # 지붕 부속이 쓰던 층 집합(W2 아홉 번째 PR)
            self._drop_layer_set(mat)

    def _drop_layer_set(self, ls) -> None:
        """코어 층 구성(IfcMaterialLayerSet)을 지운다. 다른 지정이 아직 쓰면 둔다. 층이 쓰던 재료는 사용처가 없으면 함께 지운다.
        보의 단면 재료 집합(IfcMaterialProfileSet, W2 열여덟 번째 PR)도 같은 규칙이다(_drop_profile_set)."""
        if ls.is_a("IfcMaterialProfileSet"):
            self._drop_profile_set(ls)
            return
        if any(r.is_a("IfcMaterialLayerSetUsage") or r.is_a("IfcRelAssociatesMaterial") for r in self.f.get_inverse(ls)):
            return   # 아직 쓰는 지정이 있다(층 사용, 또는 지붕 부속의 층 집합 지정: W2 아홉 번째 PR)
        mats = [ly.Material for ly in ls.MaterialLayers if ly.Material is not None]
        api_material.remove_material_set(self.f, material=ls)
        for m in mats:
            try:
                self._gc_material(m)
            except RuntimeError:
                pass   # 이미 지웠다(같은 재료를 쓴 층이 여럿)

    def _assign_layers(self, e, layers, axis: str, sense: str, offset: float, label: str) -> None:
        """요소(벽·슬래브)에 층 구성 지정(IfcMaterialLayerSetUsage)을 맞춘다. layers = ((재료, 두께), ...) 를 IFC 순서(기준선에서 멀어지는 방향)로 준다.
        이미 같은 구성이 지정돼 있으면 건드리지 않는다(멱등: 가져온 파일을 다시 투영해도 같다). 다른 재료 지정(외래 재료 포함)은 대체한다:
        층 구성이 있는 요소의 재료는 층 구성이다. label = 층 집합 이름(가장 두꺼운 층의 재료 = kinds.layers_label)."""
        f = self.f
        cur = ue.get_material(e, should_inherit=False)
        want = [(n, float(t)) for n, t in layers]
        if our_usage(cur) and cur.is_a("IfcMaterialLayerSetUsage"):
            have = [(ly.Material.Name, float(ly.LayerThickness)) for ly in cur.ForLayerSet.MaterialLayers]
            if (have == want and cur.ForLayerSet.LayerSetName == label and cur.LayerSetDirection == axis and cur.DirectionSense == sense
                    and abs(float(cur.OffsetFromReferenceLine or 0.0) - offset) < 1e-6):
                return
        old_ls = usage_set(cur) if our_usage(cur) else None
        ls = api_material.add_material_set(f, name=label, set_type="IfcMaterialLayerSet")
        ls.Description = LAYER_MARK
        for n, t in want:
            ly = api_material.add_layer(f, layer_set=ls, material=self._named_material(n))
            api_material.edit_layer(f, layer=ly, attributes={"LayerThickness": t})
        api_material.assign_material(f, products=[e], type="IfcMaterialLayerSetUsage", material=ls)
        usage = ue.get_material(e, should_inherit=False)
        api_material.edit_layer_usage(f, usage=usage, attributes={"LayerSetDirection": axis, "DirectionSense": sense, "OffsetFromReferenceLine": float(offset)})
        if old_ls is not None:
            self._drop_layer_set(old_ls)
        elif cur is not None:
            self._gc_material(cur)

    def _clear_layers(self, e) -> None:
        """투영한 층 구성이 있으면 뗀다(요소가 층 구성을 잃었다). 단일 재료 지정(재료 이름)은 그대로 둔다."""
        cur = ue.get_material(e, should_inherit=False)
        if our_usage(cur):
            ls = usage_set(cur)
            api_material.unassign_material(self.f, products=[e])
            self._drop_layer_set(ls)

    # ------------------------------------------------------------ 보의 단면 재료(W2 열여덟 번째 PR, IFC4 IfcMaterialProfileSet + Usage)
    def _drop_profile_set(self, ps) -> None:
        """코어 단면 재료 집합(IfcMaterialProfileSet, Description = LAYER_MARK 또는 보 유형 표식)을 지운다. 다른 지정이 아직 쓰면 둔다.
        단면 재료(IfcMaterialProfile)·그 프로필(집합 전용 사본)·사용처가 없어진 재료도 함께 지운다."""
        f = self.f
        if any(r.is_a("IfcMaterialProfileSetUsage") or r.is_a("IfcRelAssociatesMaterial") for r in f.get_inverse(ps)):
            return
        mps = list(ps.MaterialProfiles or ())
        mats = [mp.Material for mp in mps if mp.Material is not None]
        profs = [mp.Profile for mp in mps if mp.Profile is not None]
        f.remove(ps)
        for mp in mps:
            if not f.get_inverse(mp):
                f.remove(mp)
        for pr in profs:
            if not f.get_inverse(pr):
                pos = getattr(pr, "Position", None)
                f.remove(pr)
                if pos is not None and not f.get_inverse(pos):
                    ue.remove_deep2(f, pos)
        for m in mats:
            try:
                self._gc_material(m)
            except RuntimeError:
                pass   # 이미 지웠다

    @staticmethod
    def _profile_args(sc: dict) -> tuple:
        """단면 -> (IFC 매개변수 프로필 클래스, 치수 속성들): 사각 IfcRectangleProfileDef·H IfcIShapeProfileDef·ㄷ IfcUShapeProfileDef·
        ㄱ IfcLShapeProfileDef·각형 강관 IfcRectangleHollowProfileDef·원형 강관 IfcCircleHollowProfileDef. 모두 IFC2X3 에도 있다."""
        sh = sc["shape"]
        w, d = float(sc["width"]), float(sc["depth"])
        if sh == "h":
            return "IfcIShapeProfileDef", {"OverallWidth": w, "OverallDepth": d, "WebThickness": float(sc["web"]), "FlangeThickness": float(sc["flange"])}
        if sh == "c":
            return "IfcUShapeProfileDef", {"Depth": d, "FlangeWidth": w, "WebThickness": float(sc["web"]), "FlangeThickness": float(sc["flange"])}
        if sh == "l":
            return "IfcLShapeProfileDef", {"Depth": d, "Width": w, "Thickness": float(sc["thickness"])}
        if sh == "box":
            return "IfcRectangleHollowProfileDef", {"XDim": w, "YDim": d, "WallThickness": float(sc["thickness"])}
        if sh == "pipe":
            return "IfcCircleHollowProfileDef", {"Radius": w / 2.0, "WallThickness": float(sc["thickness"])}
        return "IfcRectangleProfileDef", {"XDim": w, "YDim": d}

    def _beam_profile(self, sc: dict, name: str, pos):
        """단면 -> IFC 매개변수 프로필 엔티티(_profile_args). pos = 프로필 배치(없으면 None)."""
        cls, attrs = self._profile_args(sc)
        return self.f.create_entity(cls, ProfileType="AREA", ProfileName=name, Position=pos, **attrs)

    def _profile_set(self, mat: str, label: str, sc: dict, mark: str):
        """재료 하나 + 단면 프로필 하나의 IfcMaterialProfileSet(이름 = 단면 표기, Description = mark). 프로필은 집합 전용 사본(배치 없음:
        단면 가운데가 원점, 몸체의 맞춤·회전은 사용(Usage)의 CardinalPoint·몸체 배치가 담는다)."""
        f = self.f
        mp = f.create_entity("IfcMaterialProfile", Name=label, Material=self._named_material(mat), Profile=self._beam_profile(sc, label, None))
        return f.create_entity("IfcMaterialProfileSet", Name=label, Description=mark, MaterialProfiles=[mp])

    @staticmethod
    def _profile_sig(ps) -> tuple | None:
        """단면 재료 집합의 비교용 서명 (재료 이름, 집합 이름, 프로필 클래스, 치수 속성들)."""
        mps = ps.MaterialProfiles or ()
        if len(mps) != 1 or mps[0].Material is None or mps[0].Profile is None:
            return None
        pr = mps[0].Profile
        info = pr.get_info(include_identifier=False, recursive=False)
        return (mps[0].Material.Name, ps.Name, pr.is_a(), tuple(sorted((k, float(v)) for k, v in info.items() if isinstance(v, float))))

    def _want_sig(self, mat: str, label: str, sc: dict) -> tuple:
        cls, attrs = self._profile_args(sc)
        return (mat, label, cls, tuple(sorted(attrs.items())))

    def _assign_profiles(self, eid: str, mat: str, label: str, sc: dict, cardinal: int) -> None:
        """보에 단면 재료 지정(IfcMaterialProfileSetUsage, 보마다 집합 하나)을 맞춘다. 이미 같으면 건드리지 않는다(멱등: 엔티티를 만들지 않고 비교한다).
        다른 재료 지정(단일 재료·외래 재료)은 대체한다: 구조 재료가 있는 보의 재료는 그것이다(벽 층 구성과 같은 규칙). 관계 GlobalId = part_guid(보, "beam:mat")."""
        f = self.f
        e = self.ents[eid]
        cur = ue.get_material(e, should_inherit=False)
        if our_usage(cur) and cur.is_a("IfcMaterialProfileSetUsage") and self._profile_sig(cur.ForProfileSet) == self._want_sig(mat, label, sc) \
                and cur.CardinalPoint == cardinal:
            return
        old_set = usage_set(cur) if our_usage(cur) else None
        if cur is not None:
            api_material.unassign_material(f, products=[e])   # 사용(Usage)도 함께 지운다
        new = self._profile_set(mat, label, sc, LAYER_MARK)
        usage = f.create_entity("IfcMaterialProfileSetUsage", ForProfileSet=new, CardinalPoint=cardinal)
        f.create_entity("IfcRelAssociatesMaterial", GlobalId=part_guid(eid, "beam:mat"), RelatedObjects=[e], RelatingMaterial=usage)
        if old_set is not None:
            self._drop_layer_set(old_set)
        elif cur is not None:
            self._gc_material(cur)

    # ------------------------------------------------------------ 투영 (늦게)
    def project(self, eid: str, spec: dict) -> None:
        kind = spec["kind"]
        {"level": self._project_level, "wall": self._project_wall, "door": self._project_door, "slab": self._project_slab, "stair": self._project_stair, "space": self._project_space, "railing": self._project_railing, "elevator": self._project_elevator, "roof": self._project_roof,
         "site": self._project_site, "solid": self._project_solid, "curtwall": self._project_curtwall, "stackwall": self._project_stackwall, "beam": self._project_beam, "beamsys": self._project_beamsys, "truss": self._project_truss, "ftype": lambda i, sp: fp.project_type(self, i, sp), "finst": lambda i, sp: fp.project_inst(self, i, sp)}[kind](eid, spec)
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

    def _uncontain(self, eid) -> None:
        """요소를 층에서 뺀다(담겨 있을 때만)."""
        if eid in self.container_of:
            self.flush_rels()
            api_spatial.unassign_container(self.f, products=[self.ents[eid]])
            self.container_of.pop(eid)

    # ------------------------------------------------------------ 스택 벽(W2 일곱 번째 PR, ARC-09)
    # 스택 벽 = 형상 없는 IfcWall(층에 담긴다) + 부분 벽 IfcWall 들의 IfcRelAggregates(GlobalId = part_guid(스택 벽, "agg"), 대상은 구간 순서).
    # Revit 의 IFC 내보내기와 같은 짜임이다(스택 벽 = 부분 벽을 모은 집합 요소). 부분 벽은 층에 담지 않는다(집합의 부분은 전체만 층에 담긴다)
    def _project_stackwall(self, eid, spec):
        e = self.ents[eid]
        e.Name = spec["name"]
        self._contain(eid, spec["level"])
        lp = self.ents[spec["level"]].ObjectPlacement
        if e.ObjectPlacement is None:
            self._place(e, lp, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        elif e.ObjectPlacement.PlacementRelTo != lp:
            e.ObjectPlacement.PlacementRelTo = lp
        self._stack_rel(eid, spec["parts"])

    # ------------------------------------------------------------ 보 시스템(W2 스물네 번째 PR, STR-02 후속)
    # 보 시스템 = 형상 없는 IfcElementAssembly(ObjectType BEAM_SYSTEM, PredefinedType USERDEFINED, 층에 담긴다) + 자식 IfcBeam 들의 IfcRelAggregates(GlobalId =
    # part_guid(시스템, "agg"), 대상은 보 순서). 근거: IFC 의 요소 조립(IfcElementAssembly)은 요소를 모은 하나의 요소이고 분해 관계 IfcRelAggregates 로 부분을 단다.
    # IFC4 의 IfcElementAssemblyTypeEnum 에는 보 시스템 값이 없어 USERDEFINED + ObjectType 으로 알린다. 부분 보는 층에 담지 않는다(집합의 부분은 전체만 층에 담긴다)
    def _project_beamsys(self, eid, spec):
        e = self.ents[eid]
        e.Name = spec["name"]
        if hasattr(e, "ObjectType") and e.ObjectType != "BEAM_SYSTEM":
            e.ObjectType = "BEAM_SYSTEM"
        if hasattr(e, "PredefinedType") and e.PredefinedType != "USERDEFINED":
            e.PredefinedType = "USERDEFINED"
        if hasattr(e, "AssemblyPlace") and e.AssemblyPlace != "NOTDEFINED":
            e.AssemblyPlace = "NOTDEFINED"
        self._contain(eid, spec["level"])
        lp = self.ents[spec["level"]].ObjectPlacement
        if e.ObjectPlacement is None:
            self._place(e, lp, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        elif e.ObjectPlacement.PlacementRelTo != lp:
            e.ObjectPlacement.PlacementRelTo = lp
        self._stack_rel(eid, spec["parts"])

    # ------------------------------------------------------------ 트러스(W2 스물다섯 번째 PR, STR-08)
    # 트러스 = 형상 없는 IfcElementAssembly(PredefinedType TRUSS, AssemblyPlace NOTDEFINED, 층에 담긴다) + 자식 부재(현 IfcBeam, 웹 IfcMember BRACE)의 IfcRelAggregates
    # (GlobalId = part_guid(트러스, "agg"), 대상은 자식 순서). 근거: IFC4·IFC2X3 의 IfcElementAssemblyTypeEnum 에 TRUSS 가 있고(스키마로 확인), 요소 조립의 분해는
    # IfcRelAggregates 로 단다. 보 시스템과 달리 ObjectType 은 쓰지 않는다(열거값이 있다). 부분 부재는 층에 담지 않는다(집합의 부분은 전체만 층에 담긴다)
    def _project_truss(self, eid, spec):
        e = self.ents[eid]
        e.Name = spec["name"]
        if hasattr(e, "PredefinedType") and e.PredefinedType != "TRUSS":
            e.PredefinedType = "TRUSS"
        if hasattr(e, "AssemblyPlace") and e.AssemblyPlace != "NOTDEFINED":
            e.AssemblyPlace = "NOTDEFINED"
        self._contain(eid, spec["level"])
        lp = self.ents[spec["level"]].ObjectPlacement
        if e.ObjectPlacement is None:
            self._place(e, lp, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        elif e.ObjectPlacement.PlacementRelTo != lp:
            e.ObjectPlacement.PlacementRelTo = lp
        self._stack_rel(eid, spec["parts"])

    def _stack_rel(self, eid: str, parts) -> None:
        f = self.f
        e = self.ents[eid]
        gid = part_guid(eid, "agg")
        rel = next((r for r in (getattr(e, "IsDecomposedBy", None) or ()) if r.GlobalId == gid), None)
        want = [self.ents[c] for c in parts if c in self.ents]
        if not want:
            if rel is not None:
                f.remove(rel)
        elif rel is None:
            f.createIfcRelAggregates(gid, None, None, None, e, want)
        elif [x.id() for x in rel.RelatedObjects] != [x.id() for x in want]:
            rel.RelatedObjects = want

    # ------------------------------------------------------------ 벽 유형(W2 일곱 번째 PR, ARC-11)
    # 벽 유형 = IfcWallType(GlobalId = part_guid(유형 id, "walltype"), PredefinedType STANDARD, ElementType = 기능) + 유형에 붙는 IfcMaterialLayerSet
    # (IfcRelAssociatesMaterial, GlobalId = part_guid(유형, "walltype:mat")) + 그 유형의 벽들을 묶는 IfcRelDefinesByType(GlobalId = part_guid(유형, "walltype:rel"),
    # 대상은 벽 id 순서). 벽 자신의 층 구성 지정(IfcMaterialLayerSetUsage)은 이전과 같다. 정의(이름·층 구성)는 DoBIM_Defs 가 정본이고 이것은 투영이다.
    # 유형이 하나도 없으면 아무것도 만들지 않는다(기존 프로젝트의 IFC 그대로)
    def _by_guid(self, gid: str):
        try:
            return self.f.by_guid(gid)
        except RuntimeError:
            return None

    def sync_wall_types(self, m: dict) -> None:
        """m = {유형 id: (이름, 기능, 두께, 층 구성, 벽 id 들)} 에 IFC 를 맞춘다(엔진 flush 가 투영 뒤에 부른다)."""
        if self.base_mode and not self._wt_scanned:
            self._wt_scanned = True
            self._wt_scan_base({part_guid(t, "walltype") for t in m})
        if not m and not self._wt_sig:
            return
        for tid in sorted(set(self._wt_sig) - set(m)):
            self._wt_drop(tid)
        for tid in sorted(m):
            name, func, _thick, layers, walls = m[tid]
            sig = m[tid][:4]
            t = self._by_guid(part_guid(tid, "walltype"))
            if t is None:
                t = self.f.create_entity("IfcWallType", GlobalId=part_guid(tid, "walltype"), Name=name, Tag=tid, PredefinedType="STANDARD")
                self._wt_sig.pop(tid, None)
            if self._wt_sig.get(tid) != sig:
                if t.Tag != tid:   # Tag = 유형 id(GlobalId 가 그 id 의 결정적 함수라 투영 소유를 알아본다)
                    t.Tag = tid
                if t.Name != name:
                    t.Name = name
                if t.ElementType != func:
                    t.ElementType = func
                if t.PredefinedType != "STANDARD":
                    t.PredefinedType = "STANDARD"
                self._wt_material(tid, t, name, layers)
                self._wt_sig[tid] = sig
            self._wt_rel(tid, t, walls)

    def _wt_material(self, tid: str, t, name: str, layers, sfx: str = "walltype", mark: str = WALLTYPE_MARK) -> None:
        f = self.f
        mr = self._by_guid(part_guid(tid, f"{sfx}:mat"))
        cur = mr.RelatingMaterial if mr is not None else None
        if cur is not None and cur.is_a("IfcMaterialLayerSet") and cur.LayerSetName == name and \
                [(ly.Material.Name if ly.Material is not None else None, float(ly.LayerThickness)) for ly in cur.MaterialLayers] == [tuple(x) for x in layers]:
            return
        if mr is not None:
            f.remove(mr)
            if cur is not None and cur.is_a("IfcMaterialLayerSet") and not f.get_inverse(cur):
                self._drop_type_set(cur)
        if not layers:
            return
        lys = [f.create_entity("IfcMaterialLayer", Material=self._named_material(n), LayerThickness=float(th)) for n, th in layers]
        ls = f.create_entity("IfcMaterialLayerSet", MaterialLayers=lys, LayerSetName=name, Description=mark)
        f.create_entity("IfcRelAssociatesMaterial", GlobalId=part_guid(tid, f"{sfx}:mat"), RelatedObjects=[t], RelatingMaterial=ls)

    def _drop_type_set(self, ls) -> None:
        """유형 층 집합(Description = WALLTYPE_MARK)과 층을 지우고, 쓰던 재료가 다른 데서 안 쓰이면 함께 지운다."""
        f = self.f
        lys = list(ls.MaterialLayers)
        mats = [ly.Material for ly in lys if ly.Material is not None]
        f.remove(ls)
        for ly in lys:
            if not f.get_inverse(ly):
                f.remove(ly)
        for m_ in mats:
            try:
                self._gc_material(m_)
            except RuntimeError:
                pass

    def _wt_rel(self, tid: str, t, walls, sfx: str = "walltype", mark: str = WALLTYPE_MARK) -> None:
        f = self.f
        rel = self._by_guid(part_guid(tid, f"{sfx}:rel"))
        want = [self.ents[w] for w in walls if w in self.ents]
        if not want:
            if rel is not None:
                f.remove(rel)
        elif rel is None:
            f.create_entity("IfcRelDefinesByType", GlobalId=part_guid(tid, f"{sfx}:rel"), Description=mark, RelatedObjects=want, RelatingType=t)
        elif [x.id() for x in rel.RelatedObjects] != [x.id() for x in want] or rel.RelatingType != t:
            rel.RelatedObjects = want
            rel.RelatingType = t

    def _wt_drop(self, tid: str) -> None:
        t = self._by_guid(part_guid(tid, "walltype"))
        if t is not None:
            self._wt_drop_ent(t)
        self._wt_sig.pop(tid, None)

    @staticmethod
    def _wt_owned(f, t, sfx: str = "walltype", mark: str = WALLTYPE_MARK) -> bool:
        """투영이 만든 벽(지붕) 유형인가: Tag 가 유형 id 이고 GlobalId 가 그 id 의 결정적 값이다(또는 표식 붙은 타입 지정 관계·유형 층 집합이 있다)."""
        tag = getattr(t, "Tag", None)
        if isinstance(tag, str) and len(tag) == 22 and t.GlobalId == part_guid(tag, sfx):
            return True
        for r in f.get_inverse(t):
            if r.is_a("IfcRelDefinesByType") and r.Description == mark:
                return True
            if r.is_a("IfcRelAssociatesMaterial") and (r.RelatingMaterial.is_a("IfcMaterialLayerSet") or r.RelatingMaterial.is_a("IfcMaterialProfileSet")) \
                    and r.RelatingMaterial.Description == mark:   # 보 유형 단면 재료 집합(W2 열여덟 번째 PR)
                return True
        return False

    def _wt_drop_ent(self, t, mark: str = WALLTYPE_MARK) -> None:
        """벽(지붕) 유형 엔티티와 그 타입 지정 관계·유형 층 집합을 지운다."""
        f = self.f
        gid = t.GlobalId
        for r in list(f.get_inverse(t)):
            if r.is_a("IfcRelDefinesByType") and r.Description == mark:
                f.remove(r)
            elif r.is_a("IfcRelAssociatesMaterial") and r.RelatingMaterial.is_a("IfcMaterialLayerSet") and r.RelatingMaterial.Description == mark:
                ls = r.RelatingMaterial
                f.remove(r)
                if not f.get_inverse(ls):
                    self._drop_type_set(ls)
            elif r.is_a("IfcRelAssociatesMaterial") and r.RelatingMaterial.is_a("IfcMaterialProfileSet") and r.RelatingMaterial.Description == mark:
                ps = r.RelatingMaterial   # 보 유형 단면 재료 집합(W2 열여덟 번째 PR)
                f.remove(r)
                self._drop_profile_set(ps)
        if f.get_inverse(t):   # 다른 도구가 붙인 관계(속성셋 등)가 남았다: 제품 지우기가 함께 치운다
            api_root.remove_product(f, product=t)
        else:
            f.remove(t)
        if self.base_mode:
            self._types_by_gid.pop(gid, None)

    def _wt_scan_base(self, keep: set, cls: str = "IfcWallType", sfx: str = "walltype", mark: str = WALLTYPE_MARK) -> None:
        """가져온 파일(작업 사본)에 남은, 투영이 만든 벽(지붕) 유형 중 지금 유형이 아닌 것을 지운다(가져온 뒤 지운 유형: 다시 열어도 같은 IFC)."""
        for t in list(self.f.by_type(cls)):
            if t.GlobalId not in keep and self._wt_owned(self.f, t, sfx, mark):
                self._wt_drop_ent(t, mark)

    # ------------------------------------------------------------ 난간 유형(W2 열세 번째 PR, ARC-31): 벽·지붕 유형과 같은 투영
    # IfcRailingType(GlobalId = part_guid(유형 id, "railtype"), PredefinedType = 발루스터가 있으면 BALUSTRADE 아니면 GUARDRAIL, Tag = 유형 id) + 그 유형의
    # 난간(IfcRailing)들을 묶는 IfcRelDefinesByType(part_guid(유형, "railtype:rel")). IFC2X3 에도 IfcRailingType 이 있어 그대로 나간다. 유형 재료 집합은 두지 않는다
    def sync_rail_types(self, m: dict) -> None:
        """m = {유형 id: (이름, 발루스터 있음, 난간 id 들)} 에 IFC 를 맞춘다. 유형이 없으면 아무것도 하지 않는다."""
        if self.base_mode and not self._lt_scanned:
            self._lt_scanned = True
            self._wt_scan_base({part_guid(t, "railtype") for t in m}, "IfcRailingType", "railtype", RAILTYPE_MARK)
        if not m and not self._lt_sig:
            return
        for tid in sorted(set(self._lt_sig) - set(m)):
            t = self._by_guid(part_guid(tid, "railtype"))
            if t is not None:
                self._wt_drop_ent(t, RAILTYPE_MARK)
            self._lt_sig.pop(tid, None)
        for tid in sorted(m):
            name, bal, rails = m[tid]
            pre = "BALUSTRADE" if bal else "GUARDRAIL"
            t = self._by_guid(part_guid(tid, "railtype"))
            if t is None:
                t = self.f.create_entity("IfcRailingType", GlobalId=part_guid(tid, "railtype"), Name=name, Tag=tid, PredefinedType=pre)
                self._lt_sig.pop(tid, None)
            if self._lt_sig.get(tid) != (name, bal):
                if t.Tag != tid:
                    t.Tag = tid
                if t.Name != name:
                    t.Name = name
                if t.PredefinedType != pre:
                    t.PredefinedType = pre
                self._lt_sig[tid] = (name, bal)
            self._wt_rel(tid, t, rails, "railtype", RAILTYPE_MARK)

    # ------------------------------------------------------------ 보 유형(W2 열다섯 번째 PR, STR-02): 난간 유형과 같은 투영
    # IfcBeamType(GlobalId = part_guid(유형 id, "beamtype"), PredefinedType BEAM, Tag = 유형 id) + 그 유형의 보(IfcBeam)들을 묶는
    # IfcRelDefinesByType(part_guid(유형, "beamtype:rel")). IFC2X3 에도 IfcBeamType(BEAM)이 있어 그대로 나간다. 단면은 보마다의 몸체 프로필이 담는다
    def sync_beam_types(self, m: dict, braces: dict | None = None) -> None:
        """m = {유형 id: (이름, 보 id 들, 재료 묶음)} 에 IFC 를 맞춘다. 유형이 없으면 아무것도 하지 않는다.
        braces(W2 스물네 번째 PR) = 가새가 쓰는 유형의 같은 표: IfcMemberType(PredefinedType BRACE, GlobalId = part_guid(유형, "beamtype:member"))로 묶는다
        (IFC 는 IfcMember 가 IfcBeamType 을 쓰는 것을 허용하지 않는다). 가새가 없으면 이전과 같다."""
        self._sync_btypes(m, "IfcBeamType", "beamtype", "BEAM", self._bt_sig, "_bt_scanned")
        self._sync_btypes(braces or {}, "IfcMemberType", "beamtype:member", "BRACE", self._mt_sig, "_mt_scanned")

    def _sync_btypes(self, m: dict, cls: str, sfx: str, pre: str, sigs: dict, scanned_attr: str) -> None:
        if self.base_mode and not getattr(self, scanned_attr):
            setattr(self, scanned_attr, True)
            self._wt_scan_base({part_guid(t, sfx) for t in m}, cls, sfx, BEAMTYPE_MARK)
        if not m and not sigs:
            return
        for tid in sorted(set(sigs) - set(m)):
            t = self._by_guid(part_guid(tid, sfx))
            if t is not None:
                self._wt_drop_ent(t, BEAMTYPE_MARK)
            sigs.pop(tid, None)
        for tid in sorted(m):
            name, beams, mat = m[tid]
            t = self._by_guid(part_guid(tid, sfx))
            if t is None:
                t = self.f.create_entity(cls, GlobalId=part_guid(tid, sfx), Name=name, Tag=tid, PredefinedType=pre)
                sigs.pop(tid, None)
            if sigs.get(tid) != (name, mat):
                if t.Tag != tid:
                    t.Tag = tid
                if t.Name != name:
                    t.Name = name
                if t.PredefinedType != pre:
                    t.PredefinedType = pre
                self._bt_material(tid, t, mat, sfx)
                sigs[tid] = (name, mat)
            self._wt_rel(tid, t, beams, sfx, BEAMTYPE_MARK)

    def _bt_material(self, tid: str, t, mat, sfx: str = "beamtype") -> None:
        """보 유형 재료(W2 열여덟 번째 PR): IfcBeamType 에 IfcMaterialProfileSet(재료 + 단면 프로필, Description = BEAMTYPE_MARK)을
        IfcRelAssociatesMaterial(part_guid(유형, "beamtype:mat"))로 붙인다. mat = (재료, 단면 표기, 단면 (키, 값) 들) 또는 None(재료 없음 = 붙이지 않는다).
        보마다의 사용(IfcMaterialProfileSetUsage)은 보 투영이 소유한다(벽 유형 층 집합과 벽의 층 사용처럼 따로 둔다)."""
        f = self.f
        mr = self._by_guid(part_guid(tid, f"{sfx}:mat"))
        cur = mr.RelatingMaterial if mr is not None else None
        if mat is not None:
            sc = dict(mat[2])
            if cur is not None and cur.is_a("IfcMaterialProfileSet") and self._profile_sig(cur) == self._want_sig(mat[0], mat[1], sc):
                return
        if mr is not None:
            f.remove(mr)
            if cur is not None and cur.is_a("IfcMaterialProfileSet"):
                self._drop_profile_set(cur)
        if mat is None:
            return
        ps = self._profile_set(mat[0], mat[1], dict(mat[2]), BEAMTYPE_MARK)
        f.create_entity("IfcRelAssociatesMaterial", GlobalId=part_guid(tid, f"{sfx}:mat"), RelatedObjects=[t], RelatingMaterial=ps)

    # ------------------------------------------------------------ 지붕 유형(W2 여덟 번째 PR): 벽 유형과 같은 투영
    # IfcRoofType(GlobalId = part_guid(유형 id, "rooftype"), PredefinedType NOTDEFINED, ElementType = 기능, Tag = 유형 id) + 유형 IfcMaterialLayerSet
    # (part_guid(유형, "rooftype:mat")) + 그 유형의 지붕(IfcRoof)들을 묶는 IfcRelDefinesByType(part_guid(유형, "rooftype:rel")). IFC2x3 은 IfcSlabType(ROOF)로 내린다
    def sync_roof_types(self, m: dict) -> None:
        """m = {유형 id: (이름, 기능, 두께, 층 구성, 지붕 id 들)} 에 IFC 를 맞춘다(엔진 flush 가 투영 뒤에 부른다). 유형이 없으면 아무것도 하지 않는다."""
        if self.base_mode and not self._rt_scanned:
            self._rt_scanned = True
            keep = {part_guid(t, "rooftype") for t in m}
            self._wt_scan_base(keep, "IfcRoofType", "rooftype", ROOFTYPE_MARK)
            self._wt_scan_base(keep, "IfcSlabType", "rooftype", ROOFTYPE_MARK)   # IFC2x3 에서 올린 파일: 지붕 유형이 IfcSlabType(ROOF)로 왔다
        if not m and not self._rt_sig:
            return
        for tid in sorted(set(self._rt_sig) - set(m)):
            t = self._by_guid(part_guid(tid, "rooftype"))
            if t is not None:
                self._wt_drop_ent(t, ROOFTYPE_MARK)
            self._rt_sig.pop(tid, None)
        for tid in sorted(m):
            name, func, _thick, layers, roofs = m[tid]
            sig = m[tid][:4]
            t = self._by_guid(part_guid(tid, "rooftype"))
            if t is not None and not t.is_a("IfcRoofType"):   # IFC2x3 에서 올린 IfcSlabType(ROOF): IFC4 는 IfcRoof 의 유형이 IfcRoofType 이어야 한다
                self._wt_drop_ent(t, ROOFTYPE_MARK)
                t = None
            if t is None:
                t = self.f.create_entity("IfcRoofType", GlobalId=part_guid(tid, "rooftype"), Name=name, Tag=tid, PredefinedType="NOTDEFINED")
                self._rt_sig.pop(tid, None)
            if self._rt_sig.get(tid) != sig:
                if t.Tag != tid:
                    t.Tag = tid
                if t.Name != name:
                    t.Name = name
                if t.ElementType != func:
                    t.ElementType = func
                if t.PredefinedType != "NOTDEFINED":
                    t.PredefinedType = "NOTDEFINED"
                self._wt_material(tid, t, name, layers, "rooftype", ROOFTYPE_MARK)
                self._rt_sig[tid] = sig
            self._wt_rel(tid, t, roofs, "rooftype", ROOFTYPE_MARK)

    # ------------------------------------------------------------ 통심선 IfcGrid(W2 열 번째 PR, ARC-38)
    # 통심선(grid)은 비 IFC 의도 요소(정의는 DoBIM_Defs)이고 이것은 그 투영이다: 모델 전체에 IfcGrid 하나(GlobalId = part_guid(건물, "grid"),
    # 건물에 담긴다(IfcRelContainedInSpatialStructure, part_guid(건물, "grid:contain")), 배치 = 건물 좌표계). 축은 IfcGridAxis(AxisTag = 기호,
    # 직선 = 2D IfcPolyline, 원호 = IfcTrimmedCurve(IfcCircle, 끝은 직교 좌표 점)). U 축 = 세로에 가까운 직선(|dy| > |dx|), V 축 = 나머지 직선과 원호,
    # 축마다 기호·id 순서. IFC 는 U·V 축이 모두 하나 이상이어야 해서 한 방향 통심선만 있으면 IfcGrid 를 내보내지 않는다. 형상 표현은 두지 않는다(축 곡선이 정본).
    # 명세 서명을 Description("DoBIM grid <서명>")에 적어, 가져온 파일의 IfcGrid 가 지금 명세와 같으면 그대로 둔다(내보내기 바이트가 같다)
    def _grid_owned(self, e) -> bool:
        d = getattr(e, "Description", None)
        return self.building is not None and e.GlobalId == part_guid(self.building.GlobalId, "grid") and isinstance(d, str) and d.startswith(GRID_MARK)

    @staticmethod
    def grid_want(m: dict):
        """{통심선 id: (기호, 선분, 원호 정보 또는 None)} -> (U 축들, V 축들) 또는 None(한 방향만). 축 = (기호, ("line", x1, y1, x2, y2) | ("arc", ...))."""
        us, vs = [], []
        for gid in sorted(m, key=lambda k: (m[k][0], k)):
            name, ln, ai = m[gid]
            if ai is not None:
                cx, cy, r, a0, sg, sw = ai
                p0, p1 = (float(ln[0]), float(ln[1])), (float(ln[2]), float(ln[3]))
                vs.append((name, ("arc", float(cx), float(cy), float(r), p0, p1, sg > 0)))
            else:
                ax = (name, ("line",) + tuple(float(x) for x in ln))
                (us if abs(ln[3] - ln[1]) > abs(ln[2] - ln[0]) else vs).append(ax)
        if (not us or not vs) and len(us) + len(vs) >= 2:   # 한 묶음뿐(방사 통심선 부채꼴 등): 방향각(0~180도) 평균의 양쪽으로 나눈다
            allx = [x for x in us + vs if x[1][0] == "line"]
            if len(allx) == len(us) + len(vs):
                def ang(x):
                    _, x1, y1, x2, y2 = x[1]
                    return AG.atan2(y2 - y1, x2 - x1) % AG.PI
                mean = sum(ang(x) for x in allx) / len(allx)
                us = [x for x in allx if ang(x) < mean - 1e-9]
                vs = [x for x in allx if ang(x) >= mean - 1e-9]
        if not us or not vs:
            return None
        return (tuple(us), tuple(vs))

    def _grid_drop(self, e) -> None:
        f = self.f
        for ax in list(e.UAxes or ()) + list(e.VAxes or ()) + list(getattr(e, "WAxes", None) or ()):
            cur = ax.AxisCurve
            f.remove(ax)
            ue.remove_deep2(f, cur)
        for r in list(f.get_inverse(e)):
            if r.is_a("IfcRelContainedInSpatialStructure"):
                if len(r.RelatedElements) <= 1:
                    f.remove(r)
                else:
                    r.RelatedElements = [x for x in r.RelatedElements if x != e]
        pl = e.ObjectPlacement
        f.remove(e)
        if pl is not None and not f.get_inverse(pl):
            ue.remove_deep2(f, pl)

    def sync_grids(self, m: dict) -> None:
        """m = {통심선 id: (기호, 선분 [x1,y1,x2,y2], 원호 정보 (cx,cy,r,a0,sg,sw) 또는 None)} 에 IfcGrid 를 맞춘다(엔진 flush 가 투영 뒤에 부른다).
        통심선이 없으면(투영한 적도 없으면) 아무것도 하지 않는다(기존 프로젝트의 IFC 그대로)."""
        if self.building is None:
            return
        want = self.grid_want(m) if m else None
        sig = None if want is None else hashlib.sha256(json.dumps(want).encode("utf-8")).hexdigest()[:16]
        if self.base_mode and not self._grid_scanned:
            self._grid_scanned = True
            for e in list(self.f.by_type("IfcGrid")):
                if self._grid_owned(e):
                    if sig is not None and e.Description == f"{GRID_MARK} {sig}" and self._grid_ent is None:
                        self._grid_ent, self._grid_sig = e, sig
                    else:
                        self._grid_drop(e)
        if sig == self._grid_sig:
            return
        if self._grid_ent is not None:
            self._grid_drop(self._grid_ent)
            self._grid_ent, self._grid_sig = None, None
        if want is None:
            return
        f = self.f
        bg = self.building.GlobalId

        def curve(c):
            if c[0] == "line":
                return f.createIfcPolyline([f.createIfcCartesianPoint([c[1], c[2]]), f.createIfcCartesianPoint([c[3], c[4]])])
            _, cx, cy, r, p0, p1, ccw = c
            circ = f.createIfcCircle(f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([cx, cy]), None), r)
            return f.createIfcTrimmedCurve(circ, [f.createIfcCartesianPoint(list(p0))], [f.createIfcCartesianPoint(list(p1))], ccw, "CARTESIAN")
        us = [f.createIfcGridAxis(name, curve(c), True) for name, c in want[0]]
        vs = [f.createIfcGridAxis(name, curve(c), True) for name, c in want[1]]
        pl = f.createIfcLocalPlacement(self.building.ObjectPlacement, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([0.0, 0.0, 0.0]), None, None))
        e = f.create_entity("IfcGrid", GlobalId=part_guid(bg, "grid"), Name="Grid", Description=f"{GRID_MARK} {sig}", ObjectPlacement=pl, UAxes=us, VAxes=vs)
        f.create_entity("IfcRelContainedInSpatialStructure", GlobalId=part_guid(bg, "grid:contain"), RelatedElements=[e], RelatingStructure=self.building)
        self._grid_ent, self._grid_sig = e, sig

    def grid_owned_gids(self) -> set:
        """투영이 만든 IfcGrid·조립품 IfcElementAssembly(W2 열한 번째 PR)의 GlobalId(가져오기·가지치기가 요소로 세지 않는다)."""
        return {e.GlobalId for e in self.f.by_type("IfcGrid") if self._grid_owned(e)} | self._asm_owned_gids()

    # ------------------------------------------------------------ 모델 그룹·조립품(W2 열한 번째 PR, ARC-46·COL-11)
    # 모델 그룹 배치 = IfcGroup(GlobalId = part_guid(배치 id, "mgroup"), Name = 배치 이름, ObjectType = 정의 이름, Description = "DoBIM model group <배치 id>")
    #   + IfcRelAssignsToGroup(part_guid(배치, "mgroup:rel"), 부속 = 부속 번호 순서). 그룹은 제품이 아니라 배치·형상이 없다(부속이 형상의 정본).
    # 조립품 = IfcElementAssembly(part_guid(조립품 id, "assembly"), Name, ObjectType = 유형 이름, PredefinedType USERDEFINED, AssemblyPlace NOTDEFINED,
    #   Description = "DoBIM assembly <조립품 id>", 배치 = 첫 부속의 층 좌표계 원점, 형상 없음) + 그 층(없으면 건물)에 담는 IfcRelContainedInSpatialStructure
    #   (part_guid(조립품, "assembly:contain")) + 부속을 모으는 IfcRelAggregates(part_guid(조립품, "assembly:agg"), 부속 순서). 부속의 층 담기는 그대로 둔다.
    # 정의(DoBIM_Defs)가 정본이고 이것은 투영이다. 그룹·조립품이 없으면 아무것도 만들지 않는다(기존 프로젝트의 IFC 그대로)
    def _asm_owned_gids(self) -> set:
        return {e.GlobalId for e in self.f.by_type("IfcElementAssembly") if _asm_owned(e)}

    @staticmethod
    def _grp_owned(e) -> bool:
        d = getattr(e, "Description", None)
        return isinstance(d, str) and d.startswith(GROUP_MARK + " ") and len(d) == len(GROUP_MARK) + 23 and e.GlobalId == part_guid(d[-22:], "mgroup")

    def _grp_drop(self, e) -> None:
        f = self.f
        for r in list(f.get_inverse(e)):
            if r.is_a("IfcRelAssignsToGroup") and r.RelatingGroup == e:
                f.remove(r)
        if f.get_inverse(e):   # 다른 도구가 붙인 관계(속성셋 등)
            api_root.remove_product(f, product=e)
        else:
            f.remove(e)

    def _asm_drop(self, e) -> None:
        f = self.f
        for r in list(f.get_inverse(e)):
            if r.is_a("IfcRelAggregates") and r.RelatingObject == e:
                f.remove(r)
            elif r.is_a("IfcRelContainedInSpatialStructure"):
                if len(r.RelatedElements) <= 1:
                    f.remove(r)
                else:
                    r.RelatedElements = [x for x in r.RelatedElements if x != e]
        pl = e.ObjectPlacement
        if f.get_inverse(e):
            api_root.remove_product(f, product=e)
        else:
            f.remove(e)
            if pl is not None and not f.get_inverse(pl):
                ue.remove_deep2(f, pl)

    def _grp_scan(self, keep: set) -> None:
        """가져온 파일(작업 사본)에 남은, 투영이 만든 그룹·조립품 중 지금 것이 아닌 것을 지운다(가져온 뒤 지운 것: 다시 열어도 같은 IFC)."""
        for e in list(self.f.by_type("IfcGroup")):
            if e.is_a() == "IfcGroup" and self._grp_owned(e) and e.GlobalId not in keep:
                self._grp_drop(e)
        for e in list(self.f.by_type("IfcElementAssembly")):
            if _asm_owned(e) and e.GlobalId not in keep:
                self._asm_drop(e)

    def _members_rel(self, gid: str, cls: str, src_attr: str, dst_attr: str, owner, want: list) -> None:
        """owner 의 묶음 관계(GlobalId gid)를 부속 want 에 맞춘다. 부속이 없으면 관계를 지운다."""
        f = self.f
        rel = self._by_guid(gid)
        if not want:
            if rel is not None:
                f.remove(rel)
        elif rel is None:
            f.create_entity(cls, GlobalId=gid, **{src_attr: owner, dst_attr: want})
        elif [x.id() for x in getattr(rel, dst_attr)] != [x.id() for x in want] or getattr(rel, src_attr) != owner:
            setattr(rel, dst_attr, want)
            setattr(rel, src_attr, owner)

    def sync_groups(self, groups: dict, asms: dict) -> None:
        """groups = {배치 id: (이름, 정의 이름, 부속 id 들)}, asms = {조립품 id: (이름, 유형, 층 id 또는 None, 부속 id 들)} 에 IFC 를 맞춘다(엔진 flush 가 투영 뒤에 부른다)."""
        if self.base_mode and not self._grp_scanned:
            self._grp_scanned = True
            self._grp_scan({part_guid(g, "mgroup") for g in groups} | {part_guid(a, "assembly") for a in asms})
        if not groups and not asms and not self._grp_sig and not self._asm_sig:
            return
        f = self.f
        for gid in sorted(set(self._grp_sig) - set(groups)):
            e = self._by_guid(part_guid(gid, "mgroup"))
            if e is not None:
                self._grp_drop(e)
            self._grp_sig.pop(gid)
        for aid in sorted(set(self._asm_sig) - set(asms)):
            e = self._by_guid(part_guid(aid, "assembly"))
            if e is not None:
                self._asm_drop(e)
            self._asm_sig.pop(aid)
        for gid in sorted(groups):
            name, tname, members = groups[gid]
            want = [self.ents[m] for m in members if m in self.ents]
            sig = (name, tname, tuple(x.id() for x in want))
            e = self._by_guid(part_guid(gid, "mgroup"))
            if e is not None and self._grp_sig.get(gid) == sig:
                continue
            if e is None:
                e = f.create_entity("IfcGroup", GlobalId=part_guid(gid, "mgroup"), Name=name, Description=f"{GROUP_MARK} {gid}", ObjectType=tname)
            else:
                for k, v in (("Name", name), ("ObjectType", tname), ("Description", f"{GROUP_MARK} {gid}")):
                    if getattr(e, k) != v:
                        setattr(e, k, v)
            self._members_rel(part_guid(gid, "mgroup:rel"), "IfcRelAssignsToGroup", "RelatingGroup", "RelatedObjects", e, want)
            self._grp_sig[gid] = sig
        for aid in sorted(asms):
            name, tname, level, members = asms[aid]
            want = [self.ents[m] for m in members if m in self.ents]
            host = self.ents[level] if level in self.ents else self.building
            sig = (name, tname, level, tuple(x.id() for x in want))
            e = self._by_guid(part_guid(aid, "assembly"))
            if e is not None and self._asm_sig.get(aid) == sig:
                continue
            rel_to = host.ObjectPlacement if host is not None else None
            if e is None:
                e = f.create_entity("IfcElementAssembly", GlobalId=part_guid(aid, "assembly"), Name=name, Description=f"{ASM_MARK} {aid}", ObjectType=tname,
                                    ObjectPlacement=self._placement(rel_to), AssemblyPlace="NOTDEFINED", PredefinedType="USERDEFINED")
            else:
                for k, v in (("Name", name), ("ObjectType", tname), ("Description", f"{ASM_MARK} {aid}"), ("AssemblyPlace", "NOTDEFINED"), ("PredefinedType", "USERDEFINED")):
                    if getattr(e, k) != v:
                        setattr(e, k, v)
                if e.ObjectPlacement is None:
                    e.ObjectPlacement = self._placement(rel_to)
                elif e.ObjectPlacement.PlacementRelTo != rel_to:
                    e.ObjectPlacement.PlacementRelTo = rel_to
            if host is not None:
                cg = part_guid(aid, "assembly:contain")
                cur = [r for r in f.get_inverse(e) if r.is_a("IfcRelContainedInSpatialStructure")]
                if not (len(cur) == 1 and cur[0].GlobalId == cg and cur[0].RelatingStructure == host and list(cur[0].RelatedElements) == [e]):
                    for r in cur:
                        if len(r.RelatedElements) <= 1:
                            f.remove(r)
                        else:
                            r.RelatedElements = [x for x in r.RelatedElements if x != e]
                    f.create_entity("IfcRelContainedInSpatialStructure", GlobalId=cg, RelatedElements=[e], RelatingStructure=host)
            self._members_rel(part_guid(aid, "assembly:agg"), "IfcRelAggregates", "RelatingObject", "RelatedObjects", e, want)
            self._asm_sig[aid] = sig

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
        if "part_of" in spec:   # 스택 벽 부분 벽(W2 일곱 번째 PR): 층이 아니라 스택 벽(전체)에 집합된다. 층에는 전체만 담긴다
            self._uncontain(eid)
        else:
            self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, (x, y, 0.0), (c, sn, 0.0))
        if "arc" in spec:   # 곡선 벽(W2 ARC-04): 바깥·안쪽 호와 양 끝 선의 닫힌 복합 곡선을 돌출하고, 축 표현(IfcTrimmedCurve(IfcCircle))을 단다
            rep0 = e.Representation.Representations[0] if e.Representation is not None else None
            if "top" in spec:   # 상단 윤곽(W2 세 번째 PR): 윗변이 기울어 돌출이 아니다. 조각마다 닫힌 다면체(IfcFacetedBrep) 여럿, 표현 종류 Brep
                self._set_body(e, self._arc_top_breps(spec))
                e.Representation.Representations[0].RepresentationType = "Brep"
            else:
                self._set_body(e, self._arc_solid(spec))
                if rep0 is not None and rep0.RepresentationType == "Brep":   # 상단 윤곽을 뗐다: 돌출로 되돌린다
                    rep0.RepresentationType = "SweptSolid"
            self._set_axis(e, self._arc_curve(spec["arc"], spec["footprint"][0][0], spec["footprint"][1][0], 0.0))
        elif "top" in spec:   # 벽 상단 윤곽(21단계): 지역 (u, z) 수직 윤곽(바닥 ~ 상단 윤곽)을 두께 방향으로 돌출한다(평면 윤곽은 늘 직사각형이라 같은 형상)
            self._set_body(e, self._vprofile_solid(spec["top"], spec["thickness"] / 2.0, spec["thickness"]))
        else:
            self._set_body(e, self._solid(spec["footprint"], spec["height"], spec.get("base", 0.0)))   # 바닥 오프셋(22단계): 배치는 레벨 그대로, 몸체만 올리고 내린다
        if "cuts" in spec:   # 벽 잘라내기(23단계): 몸체 = IfcBooleanResult(DIFFERENCE) 사슬(상자 하나씩 뺀다), 표현 종류 CSG. 없으면 몸체는 이전과 같다
            self._cut_body(e, spec["cuts"], spec["cut_range"])
        elif e.Representation.Representations[0].RepresentationType == "CSG":   # 잘라내기를 뗐다: 표현 종류를 돌출로 되돌린다
            e.Representation.Representations[0].RepresentationType = "SweptSolid"
        layers = spec.get("layers")   # 벽 층 구성: 지역 +v 방향 순서(첫 층 = -v 면), 기준선(축선)에서 -두께/2 만큼 떨어져 시작한다
        if layers:
            self._assign_layers(e, layers, "AXIS2", "POSITIVE", -sum(t for _, t in layers) / 2.0, pairs_label(layers))
        else:
            self._clear_layers(e)
        # 모양 개구부(18단계): 벽 지역 (u, z) 윤곽을 두께 방향(지역 y)으로 벽보다 양쪽 10mm 더 길게 돌출한 IfcOpeningElement. 없으면 부속도 없다(이전과 같다)
        ops = spec.get("openings", ())
        xops = spec.get("xopenings", ())   # 다른 벽의 창이 함께 자르는 개구부(25단계): 부속 키는 인스턴스 id 라 순서가 바뀌어도 GlobalId 가 같다
        if ops or xops or spec.get("voids") or eid in self.parts:
            half = spec.get("thickness", 0.0) / 2.0 + 10.0
            wanted = {f"opening:{i}": {"cls": "IfcOpeningElement", "name": f"{spec['name']}-opening{i + 1}", "rel": "void",
                                       "solid": (lambda lp=lp: self._vprofile_solid(lp, half, 2.0 * half))} for i, lp in enumerate(ops)}
            for i, at in enumerate(spec.get("openings_at", ())):   # 곡선 벽(W2 세 번째 PR): 개구부 가운데의 접선 틀에서 휜 두께를 다 뚫는다
                wanted[f"opening:{i}"]["solid"] = (lambda lp=ops[i], at=at: self._vprofile_solid_at(lp, at))
            for fid, lp in xops:
                wanted[f"xopening:{fid}"] = {"cls": "IfcOpeningElement", "name": f"{spec['name']}-xopening:{fid}", "rel": "void",
                                             "solid": (lambda lp=lp: self._vprofile_solid(lp, half, 2.0 * half))}
            for oid, oname, lp, at in spec.get("voids", ()):   # 독립 벽 개구(W2 세 번째 PR, ARC-34): 이름표 void:<개구 id>, 곡선 벽은 접선 틀
                wanted[f"void:{oid}"] = {"cls": "IfcOpeningElement", "name": f"{oname}-void:{oid}", "rel": "void",
                                         "solid": (lambda lp=lp, at=at: self._vprofile_solid(lp, half, 2.0 * half) if at is None else self._vprofile_solid_at(lp, at))}
            for oid, ps in spec.get("void_psets", ()):   # 독립 개구의 속성(W2 아홉 번째 PR)
                wanted[f"void:{oid}"]["psets"] = ps
            self._sync_parts(eid, wanted)

    # ------------------------------------------------------------ 곡선 벽(W2 ARC-04)
    @staticmethod
    def _arc_pt(arc, u, v):
        """배치 틀 안의 호 지역 (u, v) -> (x, y)(arcgeom.point 와 같은 식)."""
        cx, cy, r, a0, sg, _ = arc
        sn, cs = AG.sincos(a0 + sg * u / r)
        rr = r - sg * v
        return (round(cx + rr * cs, 6) + 0.0, round(cy + rr * sn, 6) + 0.0)

    def _arc_curve(self, arc, u0, u1, v):
        """반지름 r - sg·v 의 원을 u0 -> u1 로 자른 IfcTrimmedCurve(끝은 직교 좌표 점, 진행 방향은 SenseAgreement)."""
        f = self.f
        cx, cy, r, a0, sg, _ = arc
        circ = f.createIfcCircle(f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([float(cx), float(cy)]), None), float(r - sg * v))
        p0, p1 = self._arc_pt(arc, u0, v), self._arc_pt(arc, u1, v)
        return f.createIfcTrimmedCurve(circ, [f.createIfcCartesianPoint(list(p0))], [f.createIfcCartesianPoint(list(p1))], sg > 0, "CARTESIAN")

    def _arc_solid(self, spec):
        """곡선 벽 몸체: 오른쪽 면 호(v = -h, u0 -> u1), 끝 선, 왼쪽 면 호(v = +h, u1 -> u0), 시작 선의 닫힌 IfcCompositeCurve 를 바닥에서 높이만큼 돌출."""
        f = self.f
        arc, h = spec["arc"], spec["thickness"] / 2.0
        u0, u1 = spec["footprint"][0][0], spec["footprint"][1][0]
        right = self._arc_curve(arc, u0, u1, -h)
        left = self._arc_curve(arc, u1, u0, h)
        left.SenseAgreement = not left.SenseAgreement   # 왼쪽 면은 거꾸로(u1 -> u0) 돈다
        def seg(c):
            return f.createIfcCompositeCurveSegment("CONTINUOUS", True, c)
        def line(a, b):
            return f.createIfcPolyline([f.createIfcCartesianPoint(list(a)), f.createIfcCartesianPoint(list(b))])
        cc = f.createIfcCompositeCurve([seg(right), seg(line(self._arc_pt(arc, u1, -h), self._arc_pt(arc, u1, h))),
                                        seg(left), seg(line(self._arc_pt(arc, u0, h), self._arc_pt(arc, u0, -h)))], False)
        prof = f.createIfcArbitraryClosedProfileDef("AREA", None, cc)
        pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([0.0, 0.0, float(spec.get("base", 0.0))]), None, None)
        return f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([0.0, 0.0, 1.0]), float(spec["height"]))

    def _arc_top_breps(self, spec):
        """상단 윤곽 곡선 벽 몸체: 윤곽 점(조각 경계 점을 넣은 것) 사이 구간마다 바닥 ~ 윗변의 닫힌 다면체 하나(바깥을 향하게 면 방향을 맞춘다).
        윗면은 기울어진 두 반지름 선 사이라 평면이 아니어서 삼각형 둘로 나눈다. 단(같은 u 두 점)은 이웃 다면체의 높이 차로 생긴다."""
        f = self.f
        arc, h = spec["arc"], spec["thickness"] / 2.0
        zb = float(spec.get("base", 0.0))
        loop = spec["top"]                      # [(start, zb), (end, zb)] + 윗변(끝 -> 시작)
        top = list(loop[2:])[::-1]
        dense = [top[0]]
        for (u0, z0), (u1, z1) in zip(top, top[1:]):
            if u1 - u0 > 1e-6:
                for u in AG.breaks(arc, u0, u1)[1:-1]:
                    dense.append((u, z0 + (z1 - z0) * (u - u0) / (u1 - u0)))
            dense.append((u1, z1))
        out = []
        for (ua, za), (ub, zc) in zip(dense, dense[1:]):
            if ub - ua <= 1e-6:
                continue
            ra, rb, lb, la = self._arc_pt(arc, ua, -h), self._arc_pt(arc, ub, -h), self._arc_pt(arc, ub, h), self._arc_pt(arc, ua, h)
            vs = [(*ra, zb), (*rb, zb), (*lb, zb), (*la, zb), (*ra, za), (*rb, zc), (*lb, zc), (*la, za)]
            vs = [tuple(round(float(c), 6) + 0.0 for c in v) for v in vs]
            faces = [(0, 3, 2, 1), (4, 5, 6), (4, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
            cx, cy, cz = (sum(v[k] for v in vs) / 8.0 for k in range(3))
            fixed = []
            for fc in faces:   # Newell 법선이 몸체 중심에서 면 중심으로 가는 방향과 같게(바깥을 향하게)
                nx = ny = nz = 0.0
                for i in range(len(fc)):
                    p, q = vs[fc[i]], vs[fc[(i + 1) % len(fc)]]
                    nx += (p[1] - q[1]) * (p[2] + q[2])
                    ny += (p[2] - q[2]) * (p[0] + q[0])
                    nz += (p[0] - q[0]) * (p[1] + q[1])
                fx, fy, fz = (sum(vs[i][k] for i in fc) / len(fc) for k in range(3))
                fixed.append(fc if nx * (fx - cx) + ny * (fy - cy) + nz * (fz - cz) >= 0.0 else tuple(reversed(fc)))
            out.append(self._brep(vs, [[list(fc)] for fc in fixed]))
        return out

    def _axis_ctx(self):
        """'Axis' 하위 문맥(Model/Axis/GRAPH_VIEW). 곡선 벽이 처음 필요할 때 만든다(곡선 벽이 없는 모델의 IFC 는 그대로다)."""
        if self._axis is None:
            for c in self.f.by_type("IfcGeometricRepresentationSubContext"):
                if c.ContextIdentifier == "Axis" and c.ContextType == "Model" and c.TargetView == "GRAPH_VIEW":
                    self._axis = c
                    break
            else:
                self._axis = api_context.add_context(self.f, context_type="Model", context_identifier="Axis", target_view="GRAPH_VIEW",
                                                     parent=self.body.ParentContext)
        return self._axis

    def _set_axis(self, product, curve):
        """벽의 축 표현(Axis, Curve2D)을 바꾼다. 몸체 표현은 늘 첫 번째다(_set_body)."""
        f = self.f
        shape = product.Representation
        reps = list(shape.Representations)
        old = [r for r in reps[1:] if r.RepresentationIdentifier == "Axis"]
        rep = f.createIfcShapeRepresentation(self._axis_ctx(), "Axis", "Curve2D", [curve])
        shape.Representations = [reps[0]] + [r for r in reps[1:] if r not in old] + [rep]
        for r in old:
            for it in list(r.Items):
                ue.remove_deep2(f, it)
            f.remove(r)

    def _cut_body(self, e, cuts, rng):
        """벽 몸체(이미 투영된 돌출 하나)에서 상자들을 뺀다(23단계). 벽 면과 같은 평면에 놓인 상자 면은 FACE_PAD 만큼 벽 밖으로 내밀어
        같은 평면 빼기의 수치 잡음(얇은 막)을 피한다(결과 형상은 같다). 상자가 모두 범위 밖이면 몸체는 돌출 그대로(SweptSolid)."""
        f = self.f
        rep = e.Representation.Representations[0]
        body = rep.Items[0]
        u0, u1, half, zb, zt = rng
        pad = WC.FACE_PAD
        for b in cuts:
            a0 = b[0] - pad if b[0] <= u0 + WC.CUT_MIN else b[0]
            a1 = b[1] + pad if b[1] >= u1 - WC.CUT_MIN else b[1]
            v0 = b[2] - pad if b[2] <= -half + WC.CUT_MIN else b[2]
            v1 = b[3] + pad if b[3] >= half - WC.CUT_MIN else b[3]
            z0 = b[4] - pad if b[4] <= zb + WC.CUT_MIN else b[4]
            z1 = b[5] + pad if b[5] >= zt - WC.CUT_MIN else b[5]
            box = self._solid(((a0, v0), (a1, v0), (a1, v1), (a0, v1)), z1 - z0, z0)
            body = f.createIfcBooleanResult("DIFFERENCE", body, box)
        if body is not rep.Items[0]:
            for st in list(rep.Items[0].StyledByItem or ()):   # 스타일(색)은 최종 항목으로 옮긴다
                st.Item = body
            rep.Items = [body]
            rep.RepresentationType = "CSG"
        elif rep.RepresentationType != "SweptSolid":
            rep.RepresentationType = "SweptSolid"

    def _vprofile_solid(self, loop, y0, depth):
        """지역 (u, z) 수직 윤곽을 지역 y = y0 에서 -y 방향으로 depth 만큼 돌출한 솔리드(축 (0,-1,0), 기준 방향 (1,0,0): 윤곽의 y 가 위쪽 z 가 된다)."""
        f = self.f
        pl = f.createIfcPolyline([f.createIfcCartesianPoint([float(u), float(z)]) for u, z in list(loop) + [loop[0]]])
        prof = f.createIfcArbitraryClosedProfileDef("AREA", None, pl)
        pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([0.0, float(y0), 0.0]), f.createIfcDirection([0.0, -1.0, 0.0]), f.createIfcDirection([1.0, 0.0, 0.0]))
        return f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([0.0, 0.0, 1.0]), float(depth))

    def _vprofile_solid_at(self, loop, at):
        """곡선 벽의 모양 개구부: (u, z) 윤곽을 가운데 u 기준으로 옮겨, 축 위 점 (x, y)·접선 (tx, ty) 틀에서 왼쪽 면 밖(반 깊이 hd)부터
        오른쪽으로 2·hd 돌출한다(_vprofile_solid 의 접선 틀 판, at = queries.host_at)."""
        f = self.f
        x, y, tx, ty, hd = at
        uc = (min(u for u, _ in loop) + max(u for u, _ in loop)) / 2.0
        pl = f.createIfcPolyline([f.createIfcCartesianPoint([float(u - uc), float(z)]) for u, z in list(loop) + [loop[0]]])
        prof = f.createIfcArbitraryClosedProfileDef("AREA", None, pl)
        org = [round(float(x - ty * hd), 6) + 0.0, round(float(y + tx * hd), 6) + 0.0, 0.0]
        pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint(org), f.createIfcDirection([float(ty), float(-tx), 0.0]),
                                          f.createIfcDirection([float(tx), float(ty), 0.0]))
        return f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([0.0, 0.0, 1.0]), float(2.0 * hd))

    def _brep(self, verts, faces):
        """다면체(solids.solid_mesh 의 값) -> IfcFacetedBrep. 꼭짓점 점 엔티티는 면끼리 공유한다."""
        f = self.f
        pts = [f.createIfcCartesianPoint([float(x), float(y), float(z)]) for x, y, z in verts]
        out = []
        for face in faces:
            bounds = [f.createIfcFaceOuterBound(f.createIfcPolyLoop([pts[i] for i in face[0]]), True)]
            bounds += [f.createIfcFaceBound(f.createIfcPolyLoop([pts[i] for i in lp]), True) for lp in face[1:]]
            out.append(f.createIfcFace(bounds))
        return f.createIfcFacetedBrep(f.createIfcClosedShell(out))

    def _project_solid(self, eid, spec):
        """일반 솔리드(18단계): 레벨 좌표계 원점에 IfcFacetedBrep 하나. 범주가 정한 PredefinedType·ObjectType 을 적는다."""
        e = self.ents[eid]
        e.Name = spec["name"]
        if hasattr(e, "PredefinedType") and e.PredefinedType != spec["predefined"]:
            if e.PredefinedType == "USERDEFINED" and not spec["object_type"]:   # 범주를 바꿔 사용자 정의 종류를 뗐다(25단계): 이름표도 뗀다
                e.ObjectType = None
            e.PredefinedType = spec["predefined"]   # 같은 클래스 안에서 범주를 바꾸면(예: 위생기구 종류) 종류도 따라간다. 처음 만들 때 None 이면 그대로
        if spec["object_type"]:
            e.ObjectType = spec["object_type"]
        self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        if "csg" in spec:   # 불리언(W2 여섯 번째 PR): 피연산자 IfcFacetedBrep 들의 IfcBooleanResult 사슬, 표현 종류 CSG
            self._set_body(e, self._csg_item(spec["csg"]))
            e.Representation.Representations[0].RepresentationType = "CSG"
        else:
            self._set_body(e, self._brep(spec["verts"], spec["faces"]))
            e.Representation.Representations[0].RepresentationType = "Brep"
        if spec.get("voids") or eid in self.parts:   # 천장판 개구(W2 열네 번째 PR): 판을 수직으로 관통하는 IfcOpeningElement(이름표 void:<개구 id>). 없으면 이전과 같다
            zs = [v[2] for v in spec["verts"]]
            z0, depth = min(zs) - VOID_MARGIN, max(zs) - min(zs) + 2.0 * VOID_MARGIN
            vps = dict(spec.get("void_psets", ()))
            wanted = {}
            for oid, oname, lp in spec.get("voids", ()):
                wanted[f"void:{oid}"] = {"cls": "IfcOpeningElement", "name": f"{oname}-void:{oid}", "rel": "void", "solid": (lambda lp=lp: self._solid(lp, depth, z0=z0))}
                if oid in vps:
                    wanted[f"void:{oid}"]["psets"] = vps[oid]
            self._sync_parts(eid, wanted)

    def _project_beam(self, eid, spec):
        """구조 보(W2 열다섯 번째 PR, STR-02): 배치 = 레벨 좌표계 안의 기준선 시작점(x 축 = 보 방향), 몸체 = 단면 프로필
        (사각 IfcRectangleProfileDef·H형 IfcIShapeProfileDef, 이름 = 단면 표기)을 보 방향으로 돌출한 IfcExtrudedAreaSolid(SweptSolid).
        돌출 틀: 축(Z) = 배치 x(보 방향), 참조(X) = 배치 y(보 왼쪽), 그래서 프로필 (a, b) = (왼쪽, 위)다. 프로필 배치가 기준선에서 본 단면 가운데와 회전(roll)을 담는다."""
        f = self.f
        e = self.ents[eid]
        e.Name = spec["name"]
        pre = "BRACE" if spec.get("brace") else "BEAM"   # 가새(W2 스물네 번째 PR)는 IfcMember(BRACE). IFC2X3 의 IfcMember 에는 PredefinedType 이 없다
        if hasattr(e, "PredefinedType") and e.PredefinedType != pre:
            e.PredefinedType = pre
        if "part_of" in spec:   # 보 시스템 자식(W2 스물네 번째 PR): 층이 아니라 보 시스템(전체)에 집합된다. 층에는 전체만 담긴다
            self._uncontain(eid)
        else:
            self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, spec["origin"], (spec["xdir"][0], spec["xdir"][1], 0.0))
        sc = dict(spec["section"])
        arc = spec.get("arc")
        if arc:   # 곡선 보(W2 열아홉 번째 PR): 단면 프로필을 원호 기준선을 따라 스윕(IfcSurfaceCurveSweptAreaSolid, 아래)
            ca, cb_ = float(spec["center"][0]), float(spec["center"][1])
            # 스윕 틀: 프로필 (x, y) = (위, 오른쪽)(기준 곡면 법선 = 위, y = 접선 x 위). 코어 단면 (a, b) = (왼쪽, 위) 는 (x, y) = (b, -a)
            ppos = f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([cb_, -ca]), f.createIfcDirection([float(spec["roll"][1]), -float(spec["roll"][0])]))
            prof = self._beam_profile(sc, spec["profile_name"], ppos)
            r, sg, phi, end = arc
            P3 = lambda *v: f.createIfcCartesianPoint([float(x) for x in v])
            # 원 = 중심(배치 안 (0, sg r)) 둘레, 축 = sg(시계 호는 아래 -z 로 뒤집어 매개변수 증가 방향 = 진행 방향), x 방향 = 중심에서 시작점. 끝은 직교 좌표 점 + 매개변수(라디안)
            circ = f.createIfcCircle(f.createIfcAxis2Placement3D(P3(0.0, sg * r, 0.0), f.createIfcDirection([0.0, 0.0, float(sg)]), f.createIfcDirection([0.0, -float(sg), 0.0])), float(r))
            tc = f.createIfcTrimmedCurve(circ, [P3(0.0, 0.0, 0.0), f.createIfcParameterValue(0.0)], [P3(end[0], end[1], 0.0), f.createIfcParameterValue(float(phi))], True, "CARTESIAN")
            plane = f.createIfcPlane(f.createIfcAxis2Placement3D(P3(0.0, 0.0, 0.0), None, None))
            self._set_body(e, f.createIfcSurfaceCurveSweptAreaSolid(prof, f.createIfcAxis2Placement3D(P3(0.0, 0.0, 0.0), None, None), tc, 0.0, float(phi), plane))
            e.Representation.Representations[0].RepresentationType = "AdvancedSweptSolid"
        else:
            ppos = f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([float(spec["center"][0]), float(spec["center"][1])]),
                                               f.createIfcDirection([float(spec["roll"][0]), float(spec["roll"][1])]))
            prof = self._beam_profile(sc, spec["profile_name"], ppos)   # 단면 모양별 매개변수 프로필(ㄷ·ㄱ·강관: W2 열여덟 번째 PR)
            # 경사 보(W2 열여덟 번째 PR): 배치는 수평 그대로 두고 돌출 틀의 축(Z)을 배치 xz 면 안에서 (cos, 0, sin) 으로 기울인다. 참조(X) = 배치 y(왼쪽)라
            # 프로필 y 는 축에 수직인 위쪽이다(단면이 축에 수직). 수평 보는 이전과 같은 (1, 0, 0)
            sl = spec.get("slope")
            ax3 = [float(sl[0]), 0.0, float(sl[1])] if sl else [1.0, 0.0, 0.0]
            clips = spec.get("clips")
            if clips:   # 끝면이 축에 수직이 아니다(연직 끝면·경사 자르기, W2 열아홉 번째 PR): 돌출을 끝마다 더 길게 하고 연직 반공간 둘로 잘라낸다(IfcBooleanClippingResult)
                lo, hi = spec["ext"]
                org = [float(lo) * ax3[0], 0.0, float(lo) * ax3[2]]
                pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint(org), f.createIfcDirection(ax3), f.createIfcDirection([0.0, 1.0, 0.0]))
                item = f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([0.0, 0.0, 1.0]), float(hi - lo))
                for (pt, nrm) in clips:
                    pl = f.createIfcPlane(f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([float(v) for v in pt]), f.createIfcDirection([float(nrm[0]), float(nrm[1]), 0.0]),
                                                                      f.createIfcDirection([0.0, 0.0, 1.0])))
                    item = f.createIfcBooleanClippingResult("DIFFERENCE", item, f.createIfcHalfSpaceSolid(pl, True))
                self._set_body(e, item)
                e.Representation.Representations[0].RepresentationType = "Clipping"
            else:
                pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([0.0, 0.0, 0.0]), f.createIfcDirection(ax3), f.createIfcDirection([0.0, 1.0, 0.0]))
                self._set_body(e, f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([0.0, 0.0, 1.0]), float(spec["length"])))
                e.Representation.Representations[0].RepresentationType = "SweptSolid"
        if "material" in spec:   # 구조 재료(W2 열여덟 번째 PR): IfcMaterialProfileSetUsage -> IfcMaterialProfileSet(재료 + 단면 프로필)
            self._assign_profiles(eid, spec["material"], spec["profile_name"], sc, spec["cardinal"])
        else:
            cur = ue.get_material(e, should_inherit=False)
            if our_usage(cur) and cur.is_a("IfcMaterialProfileSetUsage"):   # 구조 재료를 잃었다(유형에서 뗌·유형 재료 지움)
                ps = cur.ForProfileSet
                api_material.unassign_material(f, products=[e])
                self._drop_profile_set(ps)

    def _csg_item(self, node):
        """(꼭짓점, 면, ((연산, 하위), ...)) -> IfcFacetedBrep 또는 IfcBooleanResult(DIFFERENCE·UNION·INTERSECTION) 사슬."""
        verts, faces, subs = node
        item = self._brep(verts, faces)
        for op, sub in subs:
            item = self.f.createIfcBooleanResult(op.upper(), item, self._csg_item(sub))
        return item

    def _project_slab(self, eid, spec):
        """슬래브: 레벨 좌표계 원점에 경계 프로필을 압출한다. 윗면 = 레벨 + top_offset 이므로 솔리드는 (top_offset - 두께)에서 두께만큼 +Z 로 올린다."""
        e = self.ents[eid]
        e.Name = spec["name"]
        e.PredefinedType = "ROOF" if spec.get("use") == "roof" else "FLOOR"
        self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        self._set_body(e, self._solid(spec["boundary"], spec["thickness"], z0=spec["top_offset"] - spec["thickness"]))
        layers = spec.get("layers")   # 슬래브 층 구성은 윗면에서 아래로 준다. IFC 는 기준(레벨 원점)에서 +Z 방향 순서라 뒤집고, 첫 층은 솔리드 밑면에서 시작한다
        if layers:
            self._assign_layers(e, tuple(reversed(layers)), "AXIS3", "POSITIVE", spec["top_offset"] - spec["thickness"], pairs_label(layers))
        else:
            self._clear_layers(e)
        # 구멍(계단실·샤프트): 슬래브를 뚫는 IfcOpeningElement. 솔리드는 슬래브 두께보다 위아래로 10mm 더 길게 한다(경계면과 겹치지 않게)
        z0 = spec["top_offset"] - spec["thickness"] - 10.0
        depth = spec["thickness"] + 20.0
        wanted = {f"opening:{i}": {"cls": "IfcOpeningElement", "name": f"{spec['name']}-opening{i + 1}", "rel": "void",
                                   "solid": (lambda o=o: self._solid(o, depth, z0=z0))} for i, o in enumerate(spec.get("openings", ()))}
        for oid, oname, o in spec.get("voids", ()):   # 독립 수직·샤프트 개구(W2 세 번째 PR, ARC-34): 이름표 void:<개구 id>
            wanted[f"void:{oid}"] = {"cls": "IfcOpeningElement", "name": f"{oname}-void:{oid}", "rel": "void",
                                     "solid": (lambda o=o: self._solid(o, depth, z0=z0))}
        for oid, ps in spec.get("void_psets", ()):   # 독립 개구의 속성(W2 아홉 번째 PR)
            wanted[f"void:{oid}"]["psets"] = ps
        if "parapet" in spec:   # 지붕 슬래브 둘레의 파라펫: 슬래브에 집합한 IfcWall(PARAPET). 바깥 경계와 안쪽 고리 사이를 윗면 위로 높이만큼 압출한다
            ph, pt = spec["parapet"]
            ring = parapet_ring([list(q) for q in spec["boundary"]], pt)
            wanted["parapet"] = {"cls": "IfcWall", "name": f"{spec['name']}-parapet", "rel": "aggregate", "attrs": {"PredefinedType": "PARAPET"},
                                 "solid": (lambda: self._ring_solid(spec["boundary"], ring, ph, spec["top_offset"]))}
        if wanted or eid in self.parts:
            self._sync_parts(eid, wanted)

    # ------------------------------------------------------------ 투영 소유 부속 (꺾인 계단의 단 묶음·층참, 슬래브 구멍)
    @staticmethod
    def _part_labels(kind: str) -> list[str]:
        if kind == "slab":
            return [f"opening:{i}" for i in range(SLAB_OPENINGS_MAX)] + ["parapet"]
        if kind == "wall":   # 모양 개구부(18단계)
            return [f"opening:{i}" for i in range(OPENINGS_MAX)]
        if kind == "solid":   # 천장판 개구(W2 열네 번째 PR)만 있다(이름으로 찾는다)
            return []
        if kind == "railing":   # 유형 난간의 부재(W2 열세 번째 PR)
            return list(RAIL_PART_LABELS)
        if kind == "roof":
            return [f"face:{i}" for i in range(ROOF_FACES_MAX)]
        # spiral·sketch: 방사·스케치 계단(W2 열일곱 번째 PR). 스케치 계단 층참(W2 스물세 번째 PR)은 단 묶음 sketch:1.. 과 층참 slanding:0.. 으로 나뉜다
        return ["flight:0", "flight:1", "landing", "winder", "spiral", "sketch"] + [f"sketch:{i}" for i in range(1, STAIR_LANDINGS_MAX + 1)] + [f"slanding:{i}" for i in range(STAIR_LANDINGS_MAX)]

    def _adopt_parts(self, eid: str, kind: str, e) -> None:
        """가져온 파일에서 요소에 이미 달린 부속(GlobalId 가 part_guid 와 같고 요소와 관계가 이어진 것)을 이어받는다."""
        have = self.parts.setdefault(eid, {})
        for label in self._part_labels(kind):
            p = self._by_gid.get(part_guid(eid, label))
            if p is None:
                continue
            linked = (any(r.RelatingBuildingElement == e for r in (getattr(p, "VoidsElements", None) or ())) if label.startswith("opening:")
                      else any(r.RelatingObject == e for r in (getattr(p, "Decomposes", None) or ())))
            if linked:
                have[label] = p
        if kind == "wall":   # 다른 벽의 창이 함께 자르는 개구부(25단계): 이름표의 인스턴스 id 를 이름에서 읽고 GlobalId(part_guid)로 확인한다
            for r in getattr(e, "HasOpenings", None) or ():
                p = r.RelatedOpeningElement
                name = p.Name if isinstance(p.Name, str) else ""
                cut = name.rfind("-xopening:")
                if cut < 0:
                    continue
                label = "xopening:" + name[cut + len("-xopening:"):]
                if label not in have and p.GlobalId == part_guid(eid, label):
                    have[label] = p
        if kind == "roof":   # 처마 부속(W2 여덟 번째 PR): 이름 끝의 eave:<...> 를 읽고 GlobalId(part_guid)로 확인한다
            for r in getattr(e, "IsDecomposedBy", None) or ():
                for p in r.RelatedObjects:
                    name = p.Name if isinstance(p.Name, str) else ""
                    cut = name.rfind("-eave:")
                    if cut < 0:
                        continue
                    label = name[cut + 1:]
                    if label not in have and p.GlobalId == part_guid(eid, label):
                        have[label] = p
        if kind == "roof":   # 지붕 개구(W2 열네 번째 PR): 면 IfcSlab 을 뚫는 개구의 이름 끝 void:<개구 id>:<면 번호> 를 읽고 GlobalId 로 확인한다
            for flb in sorted(lb for lb in have if lb.startswith("face:")):
                for r in getattr(have[flb], "HasOpenings", None) or ():
                    p = r.RelatedOpeningElement
                    name = p.Name if isinstance(p.Name, str) else ""
                    cut = name.rfind("-void:")
                    if cut < 0:
                        continue
                    label = "void:" + name[cut + len("-void:"):]
                    if label not in have and p.GlobalId == part_guid(eid, label):
                        have[label] = p
        if kind in ("wall", "slab", "solid"):   # 독립 개구 객체(W2 세 번째 PR, ARC-34; 천장판 W2 열네 번째 PR): 이름 끝의 void:<개구 id> 를 읽고 GlobalId(part_guid)로 확인한다
            for r in getattr(e, "HasOpenings", None) or ():
                p = r.RelatedOpeningElement
                name = p.Name if isinstance(p.Name, str) else ""
                cut = name.rfind("-void:")
                if cut < 0:
                    continue
                label = "void:" + name[cut + len("-void:"):]
                if label not in have and p.GlobalId == part_guid(eid, label):
                    have[label] = p
        if not have:
            self.parts.pop(eid, None)

    def _drop_part(self, eid: str, label: str) -> None:
        part = self.parts[eid].pop(label)
        if part.is_a("IfcOpeningElement"):
            api_feature.remove_feature(self.f, feature=part)
        else:
            self._remove_product(part)

    def _sync_parts(self, eid: str, wanted: dict) -> None:
        """요소의 부속을 wanted({이름표: {"cls","name","rel"("void"|"aggregate"),"attrs","solid"}})에 맞춘다. 이름표 순서로 만들어 결정적이다.
        없는 것은 만들고(GlobalId = part_guid), 있는 것은 제자리에서 고치고, 더는 없는 것은 지운다."""
        f = self.f
        host = self.ents[eid]
        have = self.parts.setdefault(eid, {})
        on_part = any("on" in w for w in wanted.values()) or any(lb.count(":") > 1 and lb.startswith("void:") for lb in have)
        # 다른 부속을 뚫는 개구(지붕 면 개구, W2 열네 번째 PR)는 그 부속보다 먼저 지운다. 그런 개구가 없으면 순서는 이전과 같다(이름표 순)
        for label in sorted(set(have) - set(wanted), key=(lambda lb: (not lb.startswith("void:"), lb)) if on_part else None):
            self._drop_part(eid, label)
        for label in sorted(wanted):
            w = wanted[label]
            part = have.get(label)
            if part is not None and not part.is_a(w["cls"]):
                self._drop_part(eid, label)
                part = None
            if part is not None and "on" in w and not any(r.RelatingBuildingElement == have.get(w["on"]) for r in (part.VoidsElements or ())):
                self._drop_part(eid, label)   # 뚫는 부속이 바뀌었다(지붕 면 부속을 다시 만든 경우): 개구를 새로 단다
                part = None
            if part is None:
                part = api_root.create_entity(f, ifc_class=w["cls"])
                part.GlobalId = part_guid(eid, label)
                have[label] = part
                if w["rel"] == "void":
                    api_feature.add_feature(f, feature=part, element=have[w["on"]] if "on" in w else host)
                else:
                    api_aggregate.assign_object(f, products=[part], relating_object=host)
            part.Name = w["name"]
            for k, v in w.get("attrs", {}).items():
                setattr(part, k, v)
            self._place(part, (have[w["on"]] if "on" in w else host).ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
            self._set_body(part, w["solid"]())
            if "reptype" in w and part.Representation.Representations[0].RepresentationType != w["reptype"]:   # 처마 부속(W2 여덟 번째 PR)은 Brep
                part.Representation.Representations[0].RepresentationType = w["reptype"]
            if "psets" in w or label.startswith("void:"):   # 독립 개구의 속성(W2 아홉 번째 PR): 개구 부속 IfcOpeningElement 의 속성셋
                self._part_psets(part, w.get("psets", ()))
        self._mirror_material(eid)
        if not have:
            self.parts.pop(eid, None)

    def _part_psets(self, part, want) -> None:
        """투영 소유 부속의 속성셋을 want(((속성셋, ((속성, 값), ...)), ...))에 맞춘다. 같으면 건드리지 않고, 다르면 지우고 정렬된 순서로 다시 만든다
        (부속 속성은 의도 행이 정본이고 이것은 투영이다)."""
        f = self.f
        have = {n: {k: plain(v) for k, v in props.items() if k != "id"} for n, props in ue.get_psets(part, psets_only=True, should_inherit=False).items()}
        want_d = {n: dict(props) for n, props in want}
        if have == want_d:
            return
        for n, props in ue.get_psets(part, psets_only=True, should_inherit=False).items():
            api_pset.remove_pset(f, product=part, pset=f.by_id(props["id"]))
        for n, props in want:
            ps = api_pset.add_pset(f, product=part, name=n)
            api_pset.edit_pset(f, pset=ps, properties=dict(props))

    def _mirror_material(self, eid: str) -> None:
        """집합 부속(꺾인 계단의 단 묶음·참·돌음, 지붕 경사면, 슬래브 파라펫)이 요소의 단순 재료(IfcMaterial)를 따르게 한다(W2 실사용 결함 6).
        부속의 재료 지정은 무리마다 관계 하나(GlobalId = part_guid(요소, "material"), 부속 전부)로만 둔다: 어떤 순서로 만들고 고쳐도 IFC 가
        같은 모양이라 증분 == 전체 재생성·실행 취소 후 해시가 지켜진다. 요소에 재료가 없거나 층 구성이면 부속의 재료 지정을 뗀다.
        개구부(빼기 부속)는 재료를 갖지 않는다.
        지붕(W2 아홉 번째 PR, 실사용 결함 2): 층 구성 지붕은 부속이 그 층 집합(IfcMaterialLayerSet)을 따른다. 경사면 IfcSlab 은 면마다 기울어진
        일반 IfcSlab(표준 판이 아니다)이라 층 사용(Usage)이 아니라 층 집합 자체를 지정한다(IFC4·IFC2X3 IfcMaterialSelect 가 받는 형식).
        처마 부속 종류에 재료를 주면(roof.eaves.set material) 그 종류는 관계 하나(part_guid(요소, "material:eave:<종류>"))로 그 재료를 갖는다."""
        if self.kinds.get(eid) == "curtwall":   # 커튼월 부속(W2 ARC-22)은 종류마다 제 재료를 갖는다(_cw_rels)
            return
        f = self.f
        labeled = [(lb, p) for lb, p in sorted((self.parts.get(eid) or {}).items()) if not p.is_a("IfcFeatureElementSubtraction")]
        host = self.ents.get(eid)
        m = ue.get_material(host, should_inherit=False) if host is not None else None
        want = m if (m is not None and m.is_a("IfcMaterial")) else None
        if want is None and self.kinds.get(eid) == "roof" and our_usage(m):
            want = m.ForLayerSet
        gid = part_guid(eid, "material")
        em = self._eave_mats.get(eid) if self.kinds.get(eid) == "roof" else {}
        if em is None:   # 이 세션에서 아직 투영하지 않은 지붕(가져온 파일): 파일에 있는 종류별 관계에서 읽는다
            em = {}
            for k in EAVE_IFC:
                try:
                    r = f.by_guid(part_guid(eid, f"material:eave:{k}"))
                except RuntimeError:
                    continue
                if r.is_a("IfcRelAssociatesMaterial") and r.RelatingMaterial is not None and r.RelatingMaterial.is_a("IfcMaterial"):
                    em[k] = r.RelatingMaterial.Name
        groups = {gid: [[], want]}   # 관계 GlobalId -> [부속들, 재료]
        gof = {}
        for lb, p in labeled:
            g = gid
            if lb.startswith("eave:") and lb.split(":")[1] in em:
                k = lb.split(":")[1]
                g = part_guid(eid, f"material:eave:{k}")
                if g not in groups:
                    groups[g] = [[], self._named_material(em[k])]
            groups[g][0].append(p)
            gof[p.id()] = g
        if self.kinds.get(eid) == "roof":   # 부속이 없어진(또는 재료를 뗀) 처마 종류의 관계도 찾아 지운다
            for k in EAVE_IFC:
                groups.setdefault(part_guid(eid, f"material:eave:{k}"), [[], None])
        canon, old_mats = {}, []
        for g in groups:
            try:
                r = f.by_guid(g)
            except RuntimeError:
                r = None
            if r is not None and r.is_a("IfcRelAssociatesMaterial"):
                canon[g] = r
        mine = {r.id() for r in canon.values()}
        for _lb, p in labeled:
            for r in list(p.HasAssociations or ()):
                if not r.is_a("IfcRelAssociatesMaterial") or r == canon.get(gof[p.id()]):
                    continue
                if r.id() not in mine:
                    old_mats.append(r.RelatingMaterial)   # 부속에 따로 붙은 재료 지정(외부 도구 등)은 뗀다
                rest = tuple(x for x in r.RelatedObjects if x != p)
                if rest:
                    r.RelatedObjects = rest
                elif r.id() in mine:
                    r.RelatedObjects = ()   # 다른 무리의 관계: 아래에서 그 무리를 맞출 때 다시 채우거나 지운다
                else:
                    f.remove(r)
        for g, (ps, w) in groups.items():
            c = canon.get(g)
            if w is None or not ps:
                if c is not None:
                    old_mats.append(c.RelatingMaterial)
                    f.remove(c)
            elif c is None:
                f.createIfcRelAssociatesMaterial(g, None, None, None, ps, w)
            else:
                if c.RelatingMaterial != w:
                    old_mats.append(c.RelatingMaterial)
                    c.RelatingMaterial = w
                if list(c.RelatedObjects) != ps:
                    c.RelatedObjects = ps
        for om in old_mats:   # 이제 아무도 쓰지 않는 재료는 지운다(재료 목록이 상태의 함수여야 취소 후 해시가 복원된다)
            if om is not None and all(om != w for _, w in groups.values()):
                try:
                    self._gc_material(om)
                except RuntimeError:
                    pass   # 이미 지웠다

    def _stair_solid(self, run, width, n, rise, zbase, zbot):
        """단 묶음 하나의 톱니 옆모습을 폭 방향으로 압출한 솔리드. 챌판 n 개가 zbase 에서 올라가고 밑은 zbot 까지 채운다."""
        f = self.f
        x1, y1, x2, y2 = run
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        td = length / (n - 1)
        pts = [(0.0, zbot)]
        for k in range(n):
            pts.append((k * td, zbase + (k + 1) * rise))
            if k < n - 1:
                pts.append(((k + 1) * td, zbase + (k + 1) * rise))
        pts.append((length, zbot))
        pl = f.createIfcPolyline([f.createIfcCartesianPoint([float(a), float(b)]) for a, b in pts + [pts[0]]])
        prof = f.createIfcArbitraryClosedProfileDef("AREA", None, pl)
        # 솔리드 좌표계: 원점 = 걸음선 시작점에서 왼쪽으로 폭의 절반, 축(압출) = 오른쪽 법선, 기준 방향 = 걸음선 방향
        ox, oy = x1 - uy * width / 2.0, y1 + ux * width / 2.0
        pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([float(ox), float(oy), 0.0]),
                                          f.createIfcDirection([float(uy), float(-ux), 0.0]), f.createIfcDirection([float(ux), float(uy), 0.0]))
        return f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([0.0, 0.0, 1.0]), float(width))

    def _project_stair(self, eid, spec):
        """직선 계단: 톱니 옆모습(걸음선 길이 x 높이 평면)을 폭 방향으로 압출한 솔리드 하나. 압출 방향은 걸음선의 오른쪽 법선이다(그러면 옆모습의 y 가 위쪽이 된다).
        꺾인 계단(turn)은 계단 자신에 형상이 없고 단 묶음마다 IfcStairFlight, 층참은 IfcSlab(LANDING)으로 계단에 집합한다."""
        f = self.f
        e = self.ents[eid]
        e.Name = spec["name"]
        if "spiral" in spec:   # 방사 계단(W2 열일곱 번째 PR)
            self._project_spiral_stair(eid, spec)
            return
        if "sketch" in spec:   # 스케치 계단(W2 열일곱 번째 PR)
            self._project_sketch_stair(eid, spec)
            return
        turn = dict(spec["turn"]) if "turn" in spec else None
        if turn is not None and turn.get("winders") is not None:   # 돌음 계단(23.3): 층참 대신 쐐기 디딤판
            e.PredefinedType = "QUARTER_WINDING_STAIR" if turn["type"] == "L" else "HALF_WINDING_STAIR"
        else:
            e.PredefinedType = "STRAIGHT_RUN_STAIR" if turn is None else ("QUARTER_TURN_STAIR" if turn["type"] == "L" else "HALF_TURN_STAIR")
        self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        n, z0 = spec["risers"], spec["base_offset"]
        if turn is None:
            if eid in self.parts:
                self._sync_parts(eid, {})
            rise = spec["height"] / n
            self._set_body(e, self._stair_solid(spec["run"], spec["width"], n, rise, z0, z0))
            return
        lay = stair_layout({"run": spec["run"], "width": spec["width"], "height": spec["height"], "risers": n, "turn": turn})
        rise = lay["rise"]
        if e.Representation is not None:   # 직선이던 계단이 꺾였다: 계단 자신의 몸체를 없앤다
            shape = e.Representation
            e.Representation = None
            ue.remove_deep2(f, shape)
        wanted = {}
        for i, fl in enumerate(lay["flights"]):
            wanted[f"flight:{i}"] = {
                "cls": "IfcStairFlight", "name": f"{spec['name']}-flight{i + 1}", "rel": "aggregate",
                "attrs": {"NumberOfRisers": fl["n"], "NumberOfTreads": fl["n"] - 1, "RiserHeight": float(rise), "TreadLength": float(fl["td"]), "PredefinedType": "STRAIGHT"},
                "solid": (lambda fl=fl: self._stair_solid(fl["run"], spec["width"], fl["n"], rise, z0 + fl["z0"], z0 + fl["z0"] - fl["skirt"]))}
        ld = lay["landing"]
        if ld is not None:
            wanted["landing"] = {"cls": "IfcSlab", "name": f"{spec['name']}-landing", "rel": "aggregate", "attrs": {"PredefinedType": "LANDING"},
                                 "solid": (lambda: self._solid(ld["poly"], ld["t"], z0=z0 + ld["z"] - ld["t"]))}
        else:   # 돌음: 회전부 쐐기들을 IfcStairFlight(WINDER) 하나에 담는다(쐐기마다 수직 압출 솔리드). 챌판은 쐐기 사이 m - 1 개
            wd = lay["winders"]
            m = len(wd["wedges"])
            wanted["winder"] = {
                "cls": "IfcStairFlight", "name": f"{spec['name']}-winder", "rel": "aggregate",
                "attrs": {"NumberOfRisers": m - 1, "NumberOfTreads": m, "RiserHeight": float(rise), "TreadLength": float(wd["tread"]), "PredefinedType": "WINDER"},
                "solid": (lambda: [self._solid(w["poly"], w["z"] - w["zb"], z0=z0 + w["zb"]) for w in wd["wedges"]])}
        self._sync_parts(eid, wanted)

    def _project_spiral_stair(self, eid, spec):
        """방사 계단(W2 열일곱 번째 PR, ARC-29): IfcStair 자신에는 형상이 없고 IfcStairFlight 하나(부속 이름표 spiral, PredefinedType SPIRAL = 보행선이 원호인 단 묶음)가
        디딤판마다 수직 압출 솔리드(볼록 다각형)를 담는다. 계단 형식은 안쪽 반지름이 폭 이하면 SPIRAL_STAIR(중심 기둥 둘레로 도는 계단), 그보다 크면 CURVED_RUN_STAIR
        (굽은 단 묶음 하나). 근거: IFC4 IfcStairTypeEnum·IfcStairFlightTypeEnum 정의. IFC2X3 은 ShapeType 에 남고 단 묶음 PredefinedType 은 없다(기존 변환 규칙)."""
        from dobim_core.kinds import stair_spiral_layout
        f = self.f
        e = self.ents[eid]
        e.Name = spec["name"]
        sp = dict(spec["spiral"])
        e.PredefinedType = "SPIRAL_STAIR" if sp["r_in"] <= spec["width"] else "CURVED_RUN_STAIR"
        self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        if e.Representation is not None:   # 직선이던 계단이 방사 계단이 되었다: 계단 자신의 몸체를 없앤다
            shape = e.Representation
            e.Representation = None
            ue.remove_deep2(f, shape)
        lay = stair_spiral_layout({"width": spec["width"], "height": spec["height"], "risers": spec["risers"],
                                   "spiral": {"center": list(sp["center"]), "r_in": sp["r_in"], "start": sp["start"], "sweep": sp["sweep"]}})
        z0 = spec["base_offset"]
        n = spec["risers"]
        wanted = {"spiral": {"cls": "IfcStairFlight", "name": f"{spec['name']}-flight1", "rel": "aggregate",
                             "attrs": {"NumberOfRisers": n, "NumberOfTreads": n - 1, "RiserHeight": float(lay["rise"]), "TreadLength": float(lay["tread"]), "PredefinedType": "SPIRAL"},
                             "solid": (lambda: [self._solid(t["poly"], t["z"] - t["zb"], z0=z0 + t["zb"]) for t in lay["treads"]])}}
        self._sync_parts(eid, wanted)

    def _project_sketch_stair(self, eid, spec):
        """스케치 계단(W2 열일곱 번째 PR, ARC-29): 방사 계단과 같이 IfcStair 자신은 형상이 없고 IfcStairFlight 하나(이름표 sketch)가 디딤판 조각마다
        수직 압출 솔리드를 담는다. 보행선에 원호가 있으면 CURVED_RUN_STAIR + 단 묶음 CURVED(보행선이 굽은 단 묶음), 직선뿐이면 STRAIGHT_RUN_STAIR + STRAIGHT."""
        from dobim_core.kinds import stair_sketch_layout
        f = self.f
        e = self.ents[eid]
        e.Name = spec["name"]
        sk = dict(spec["sketch"])
        curved = any(sk["bulges"])
        cors = sk.get("corners", ())
        e.PredefinedType = "CURVED_RUN_STAIR" if curved else "STRAIGHT_RUN_STAIR"
        if cors:   # 꺾인 모서리(W2 스무 번째 PR): 호가 없으면 총 꺾임각으로 사분(90)·반(180)·3/4(270) 돌음·꺾임 계단, 그 밖의 각은 NOTDEFINED
            skl = stair_sketch_layout({"width": spec["width"], "height": spec["height"], "risers": spec["risers"],
                                       "sketch": {"path": [list(q) for q in sk["path"]], "corners": list(cors), **({"bulges": list(sk["bulges"])} if sk["bulges"] else {})}})
            tot = sum(abs(c["sw"]) for c in skl["corners"])
            quarters = next((q for q in (1, 2, 3) if abs(tot - 90.0 * q) <= 10.0), None)
            winding = any(c["m"] >= 2 for c in skl["corners"])
            if not curved:
                e.PredefinedType = ("NOTDEFINED" if quarters is None else
                                    {1: "QUARTER_WINDING_STAIR", 2: "HALF_WINDING_STAIR", 3: "THREE_QUARTER_WINDING_STAIR"}[quarters] if winding else
                                    {1: "QUARTER_TURN_STAIR", 2: "HALF_TURN_STAIR", 3: "THREE_QUARTER_TURN_STAIR"}[quarters])
        self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        if e.Representation is not None:   # 직선이던 계단이 스케치 계단이 되었다: 계단 자신의 몸체를 없앤다
            shape = e.Representation
            e.Representation = None
            ue.remove_deep2(f, shape)
        skp = {"path": [list(q) for q in sk["path"]]}
        if sk["bulges"]:
            skp["bulges"] = list(sk["bulges"])
        if cors:
            skp["corners"] = list(cors)
        if sk.get("landings"):
            skp["landings"] = list(sk["landings"])
        if sk.get("balanced"):
            skp["balanced"] = list(sk["balanced"])
        lay = stair_sketch_layout({"width": spec["width"], "height": spec["height"], "risers": spec["risers"], "sketch": skp})
        z0 = spec["base_offset"]
        n = spec["risers"]
        if lay["landings"] and not cors and not curved:   # 층참 변(W2 스물세 번째 PR): 직선뿐이면 단 묶음 수로 STRAIGHT_RUN_STAIR(1)·TWO_STRAIGHT_RUN_STAIR(2)·NOTDEFINED(3 이상)
            nfl = sum(1 for a_, b_ in zip([None] + lay["treads"], lay["treads"]) if "landing" not in b_ and (a_ is None or "landing" in a_))
            e.PredefinedType = {1: "STRAIGHT_RUN_STAIR", 2: "TWO_STRAIGHT_RUN_STAIR"}.get(nfl, "NOTDEFINED")
        if not lay["landings"]:   # 층참이 없으면 단 묶음 하나(이전과 같다)
            wanted = {"sketch": {"cls": "IfcStairFlight", "name": f"{spec['name']}-flight1", "rel": "aggregate",
                                 "attrs": {"NumberOfRisers": n, "NumberOfTreads": n - 1, "RiserHeight": float(lay["rise"]), "TreadLength": float(lay["td"]),
                                           "PredefinedType": "WINDER" if any(c["m"] >= 2 for c in lay["corners"]) else "CURVED" if curved else "STRAIGHT"},
                                 "solid": (lambda: [self._solid(q, t["z"] - t["zb"], z0=z0 + t["zb"]) for t in lay["treads"] for q in t["pieces"]])}}
            self._sync_parts(eid, wanted)
            return
        # 층참 변(W2 스물세 번째 PR): 층참마다 IfcSlab(LANDING)을 계단에 집합하고, 층참 사이 디딤판 묶음마다 IfcStairFlight 를 둔다(꺾인 계단 turn 과 같은 규약:
        # 단 묶음 하나의 챌판 수 = 디딤판 수 + 1 이고 층참으로 오르는 챌판은 앞 단 묶음에 센다). 층참이 맨 앞이면 앞 단 묶음은 없다.
        flights, cur = [], []
        for t in lay["treads"]:
            if "landing" in t:
                if cur:
                    flights.append(cur)
                cur = []
            else:
                cur.append(t)
        if cur:
            flights.append(cur)
        wanted = {}
        for fi, fl in enumerate(flights):
            wound = any("winder" in t and t["winder"]["m"] >= 2 for t in fl)
            wanted["sketch" if fi == 0 else f"sketch:{fi}"] = {
                "cls": "IfcStairFlight", "name": f"{spec['name']}-flight{fi + 1}", "rel": "aggregate",
                "attrs": {"NumberOfRisers": len(fl) + 1, "NumberOfTreads": len(fl), "RiserHeight": float(lay["rise"]),
                          "TreadLength": float(min(t["s1"] - t["s0"] for t in fl)), "PredefinedType": "WINDER" if wound else "CURVED" if curved else "STRAIGHT"},
                "solid": (lambda fl=fl: [self._solid(q, t["z"] - t["zb"], z0=z0 + t["zb"]) for t in fl for q in t["pieces"]])}
        for li, t in enumerate(t for t in lay["treads"] if "landing" in t):
            wanted[f"slanding:{li}"] = {"cls": "IfcSlab", "name": f"{spec['name']}-landing{li + 1}", "rel": "aggregate", "attrs": {"PredefinedType": "LANDING"},
                                        "solid": (lambda t=t: [self._solid(q, t["z"] - t["zb"], z0=z0 + t["zb"]) for q in t["pieces"]])}
        self._sync_parts(eid, wanted)

    def _project_railing(self, eid, spec):
        """난간: 손잡이(IfcSweptDiskSolid, 손잡이 윗선을 따라 3D 폴리라인)와 난간 기둥(수직 IfcSweptDiskSolid)을 한 몸체에 담는다. 배치는 레벨 좌표계 원점이다."""
        from dobim_core.kinds import RAILING_HANDRAIL_R, RAILING_POST_R, railing_geom
        f = self.f
        e = self.ents[eid]
        e.Name = spec["name"]
        e.PredefinedType = "GUARDRAIL"
        self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        gp = {"path": spec["path"], "height": spec["height"], "base_offset": spec["base_offset"], "rise": spec["rise"]}
        if "parts" in spec:   # 난간 유형(W2 열세 번째 PR)
            gp["parts"] = spec["parts"]
        g = railing_geom(gp)

        def disk(pts, r):
            pl = f.createIfcPolyline([f.createIfcCartesianPoint([float(a), float(b), float(c)]) for a, b, c in pts])
            return f.createIfcSweptDiskSolid(pl, float(r), None, None, None)
        if "parts" in spec:
            items, wanted = self._railing_members(eid, spec, g, disk)
        else:
            items, wanted = [disk(g["top"], RAILING_HANDRAIL_R)] + [disk([a, b], RAILING_POST_R) for a, b in g["posts"]], None
        self._set_body(e, items)
        if wanted is not None or eid in self.parts:   # 유형을 뗐으면 부재를 지운다. 유형 없는 난간은 이전과 같다(부속 동기화를 부르지 않는다)
            self._sync_parts(eid, wanted or {})
        # IFC4 IfcShapeRepresentationTypes: 'SweptSolid' 는 IfcExtrudedAreaSolid·IfcRevolvedAreaSolid 만 담는다. IfcSweptDiskSolid 는
        # 'AdvancedSweptSolid' 여야 검증 오류가 없다(W2 실사용 결함 1)
        e.Representation.Representations[0].RepresentationType = "AdvancedSweptSolid"

    def _railing_members(self, eid, spec, g, disk):
        """유형 난간(W2 열세 번째 PR, ARC-31): 몸체 = 상부 난간대, 부재는 종류마다 IfcMember 하나로 IfcRelAggregates(부속 GlobalId = part_guid(난간, 이름표)).
        매핑 근거: IFC4 IfcRailing 은 난간 전체(손잡이 포함)이고 그 구성 부재는 IfcMember 로 분해한다. 동자 = IfcMember POST, 중간 난간대·발루스터는
        IfcMemberTypeEnum 에 해당 값이 없어 USERDEFINED + ObjectType(RAIL·BALUSTER). 단면은 단면 폭을 지름으로 하는 원(IfcSweptDiskSolid) 근사다."""
        mem = g["members"]
        top = next(m for m in mem if m[0] == "top")
        wanted = {}
        groups = (("rails", "rail", {"PredefinedType": "USERDEFINED", "ObjectType": "RAIL"}),
                  ("posts", "post", {"PredefinedType": "POST", "ObjectType": None}),
                  ("balusters", "baluster", {"PredefinedType": "USERDEFINED", "ObjectType": "BALUSTER"}))
        for label, kind, attrs in groups:
            ms = [m for m in mem if m[0] == kind]
            if not ms:
                continue
            wanted[label] = {"cls": "IfcMember", "name": f"{spec['name']}-{label}", "rel": "aggregate", "attrs": attrs, "reptype": "AdvancedSweptSolid",
                             "solid": (lambda ms=ms: [disk(list(m[1]), m[2] / 2.0) for m in ms])}
        return [disk(top[1], top[2] / 2.0)], wanted

    def _ring_solid(self, outer, inner, depth, z0):
        """바깥 둘레와 안쪽 고리 사이(구멍 있는 프로필)를 +Z 로 depth 만큼 압출한 솔리드(파라펫)."""
        return self._voided_solid(outer, [inner], depth, z0)

    def _voided_solid(self, outer, inners, depth, z0):
        """바깥 둘레에서 구멍들(inners)을 뺀 프로필을 +Z 로 depth 만큼 압출한 솔리드(파라펫·섬 구멍이 있는 공간)."""
        f = self.f

        def loop(pts):
            return f.createIfcPolyline([f.createIfcCartesianPoint([float(x), float(y)]) for x, y in list(pts) + [pts[0]]])
        prof = f.createIfcArbitraryProfileDefWithVoids("AREA", None, loop(outer), [loop(i) for i in inners])
        pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([0.0, 0.0, float(z0)]), None, None)
        return f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([0.0, 0.0, 1.0]), float(depth))

    def _sloped_solid(self, face, thickness):
        """경사 지붕 면 하나(볼록 다각형, 3D 점)의 솔리드: 면의 평면을 프로필로 두고 수직 아래로 thickness 만큼 압출한다(압출 방향이 평면에 비스듬하다)."""
        f = self.f
        plane = plane_of(face)
        a, b, _c = plane
        nrm = (-a, -b, 1.0)
        nl = math.sqrt(nrm[0] ** 2 + nrm[1] ** 2 + nrm[2] ** 2)
        nz = (nrm[0] / nl, nrm[1] / nl, nrm[2] / nl)
        xv = (1.0, 0.0, a)
        xl = math.sqrt(1.0 + a * a)
        xv = (xv[0] / xl, xv[1] / xl, xv[2] / xl)
        yv = (nz[1] * xv[2] - nz[2] * xv[1], nz[2] * xv[0] - nz[0] * xv[2], nz[0] * xv[1] - nz[1] * xv[0])
        o = face[0]
        pts = [((q[0] - o[0]) * xv[0] + (q[1] - o[1]) * xv[1] + (q[2] - o[2]) * xv[2],
                (q[0] - o[0]) * yv[0] + (q[1] - o[1]) * yv[1] + (q[2] - o[2]) * yv[2]) for q in face]
        pl = f.createIfcPolyline([f.createIfcCartesianPoint([float(u), float(v)]) for u, v in pts + [pts[0]]])
        prof = f.createIfcArbitraryClosedProfileDef("AREA", None, pl)
        pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([float(o[0]), float(o[1]), float(o[2])]), f.createIfcDirection([float(v) for v in nz]),
                                          f.createIfcDirection([float(v) for v in xv]))
        down = (-xv[2], -yv[2], -nz[2])   # 전역 (0,0,-1) 의 로컬 성분
        return f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([float(v) for v in down]), float(thickness))

    def _face_void_solid(self, loop, plane, t):
        """지붕 면 개구(W2 열네 번째 PR): 윗면 위 경계 고리(평면 투영 loop)를 면 평면에 올린 프로필을 면 법선 반대쪽(아래)으로 판 두께(면에 수직)만큼 돌출한다.
        경계면과 겹치지 않게 법선 방향으로 VOID_MARGIN 위에서 시작해 두께 + 2 x VOID_MARGIN 만큼 내린다. 지붕 두께는 수직으로 재므로 면에 수직인 두께는 t / sqrt(1 + a² + b²)."""
        f = self.f
        a, b, c = plane
        nl = math.sqrt(a * a + b * b + 1.0)
        nz = (-a / nl, -b / nl, 1.0 / nl)
        xl = math.sqrt(1.0 + a * a)
        xv = (1.0 / xl, 0.0, a / xl)
        yv = (nz[1] * xv[2] - nz[2] * xv[1], nz[2] * xv[0] - nz[0] * xv[2], nz[0] * xv[1] - nz[1] * xv[0])
        o = (loop[0][0], loop[0][1], a * loop[0][0] + b * loop[0][1] + c)
        o = (o[0] + nz[0] * VOID_MARGIN, o[1] + nz[1] * VOID_MARGIN, o[2] + nz[2] * VOID_MARGIN)
        pts = []
        for x, y in loop:
            q = (x - o[0], y - o[1], a * x + b * y + c - o[2])
            pts.append((q[0] * xv[0] + q[1] * xv[1] + q[2] * xv[2], q[0] * yv[0] + q[1] * yv[1] + q[2] * yv[2]))
        pl = f.createIfcPolyline([f.createIfcCartesianPoint([float(u), float(v)]) for u, v in pts + [pts[0]]])
        prof = f.createIfcArbitraryClosedProfileDef("AREA", None, pl)
        pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([float(v) for v in o]), f.createIfcDirection([float(v) for v in nz]),
                                          f.createIfcDirection([float(v) for v in xv]))
        return f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([0.0, 0.0, -1.0]), float(t / nl + 2.0 * VOID_MARGIN))

    def _project_roof(self, eid, spec):
        """경사 지붕: IfcRoof 자신은 형상이 없고 경사면마다 하위 IfcSlab(PredefinedType ROOF)이 집합한다. 면의 몸체는 면 평면을 프로필로 한 비스듬한 압출이다.
        배치는 레벨 좌표계 원점이고 처마 높이는 레벨 + base_offset 이므로 레벨 높이는 읽지 않는다."""
        e = self.ents[eid]
        e.Name = spec["name"]
        if spec["form"] == "extrusion":   # 압출 지붕(W2 여덟 번째 PR): 선분 하나 = 외쪽, 둘 = 박공, 그 밖 = 볼트(곡면 근사)
            npf = len(spec["profile"]) - 1
            e.PredefinedType = "SHED_ROOF" if npf == 1 else ("GABLE_ROOF" if npf == 2 else "BARREL_ROOF")
        else:
            e.PredefinedType = ({"gable": "GABLE_ROOF", "hip": "HIP_ROOF", "shed": "SHED_ROOF", "flat": "FLAT_ROOF"}.get(spec["form"])   # 평지붕(W2 열여섯 번째 PR): FLAT_ROOF
                                or _poly_roof_type(spec.get("slopes", ())))   # poly·footprint
        self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        if e.Representation is not None:
            shape = e.Representation
            e.Representation = None
            ue.remove_deep2(self.f, shape)
        p = {"boundary": [list(q) for q in spec["boundary"]], "form": spec["form"], "pitch": spec["pitch"], "overhang": spec["overhang"], "base_offset": spec["base_offset"]}
        for k in ("ridge", "dir"):
            if k in spec:
                p[k] = spec[k]
        if "slopes" in spec:
            p["slopes"] = list(spec["slopes"])
        if "overhangs" in spec:   # 외곽선 지붕(W2 여덟 번째 PR)·평지붕(W2 열여섯 번째 PR)
            p["overhangs"] = list(spec["overhangs"])
        if "bulges" in spec:   # 평지붕 원호 변(W2 열여섯 번째 PR)
            p["bulges"] = list(spec["bulges"])
        if "axis" in spec:   # 압출 지붕(W2 여덟 번째 PR)
            p["axis"] = list(spec["axis"])
            p["profile"] = [list(q) for q in spec["profile"]]
        gm = roof_geom(p)
        wanted = {f"face:{i}": {"cls": "IfcSlab", "name": f"{spec['name']}-face{i + 1}", "rel": "aggregate", "attrs": {"PredefinedType": "ROOF"},
                                "solid": (lambda fc=fc: self._sloped_solid(fc, spec["thickness"]))} for i, fc in enumerate(gm["faces"])}
        for pc in spec.get("eaves", ()):   # 처마 부속(W2 여덟 번째 PR): 이름표 eave:<종류:변:처마변:조각>, 닫힌 육면체 IfcFacetedBrep
            cls, attrs = EAVE_IFC[pc[1]]
            wanted[f"eave:{pc[0]}"] = {"cls": cls, "name": f"{spec['name']}-eave:{pc[0]}", "rel": "aggregate", "attrs": attrs, "reptype": "Brep",
                                       "solid": (lambda pc=pc: self._brep(*RE.piece_mesh(pc)))}
        if spec.get("voids"):   # 지붕 개구(W2 열네 번째 PR): 뚫는 면 IfcSlab 마다 IfcOpeningElement(이름표 void:<개구 id>:<면 번호>)
            t = spec["thickness"]
            vz0 = gm["zlow"] - t - VOID_MARGIN
            vdepth = gm["ztop"] - gm["zlow"] + t + 2.0 * VOID_MARGIN
            vps = dict(spec.get("void_psets", ()))
            for oid, oname, mode, lp, faces in spec["voids"]:
                for i in faces:
                    lb = f"void:{oid}:{i}"
                    if mode == "face":
                        sol = (lambda lp=lp, pl=gm["planes"][i]: self._face_void_solid(lp, pl, t))
                    else:
                        sol = (lambda lp=lp: self._solid(lp, vdepth, z0=vz0))
                    wanted[lb] = {"cls": "IfcOpeningElement", "name": f"{oname}-void:{oid}:{i}", "rel": "void", "on": f"face:{i}", "solid": sol}
                    if oid in vps:
                        wanted[lb]["psets"] = vps[oid]
        old_em = self._eave_mats.get(eid) or {}
        self._eave_mats[eid] = dict(spec.get("eave_materials", ()))   # 처마 부속 종류별 재료(W2 아홉 번째 PR)
        self._sync_parts(eid, wanted)
        layers = spec.get("layers")   # 지붕 층 구성(W2 여덟 번째 PR): 윗면부터 준다. IfcRoof 에 IfcMaterialLayerSetUsage(AXIS3, 윗면에서 아래로)
        if layers:
            self._assign_layers(e, tuple(layers), "AXIS3", "NEGATIVE", float(spec["base_offset"]), pairs_label(layers))
        else:
            self._clear_layers(e)
        if eid in self.parts:   # 층 구성이 바뀌었으면 부속(경사면·처마 부속)의 층 집합 지정도 따라간다(W2 아홉 번째 PR, 실사용 결함 2)
            self._mirror_material(eid)
        self._gc_names({k: v for k, v in old_em.items() if v not in self._eave_mats[eid].values()})

    # ------------------------------------------------------------ 커튼월(W2 네 번째 PR, ARC-22)
    # IfcCurtainWall 자신은 형상이 없고(레벨 좌표계 배치), 부속(멀리언 IfcMember MULLION·패널 IfcPlate CURTAIN_PANEL·문 패널 IfcDoor)이
    # 커튼월마다 IfcRelAggregates 하나(GlobalId = part_guid(커튼월, "agg"), 대상은 이름표 순서)에 매달린다. 부속의 재료는 종류마다 IfcRelAssociatesMaterial
    # 하나(GlobalId = part_guid(커튼월, "material:<종류>")). 부속 GlobalId = part_guid(커튼월, 이름표)라 어떤 순서로 만들어도 IFC 가 같은 모양이다
    # (증분 == 전체 재생성·실행 취소 후 해시). 수천 개 부속을 빠르게: 명세가 같은 부속은 다시 쓰지 않고, 없어진 부속 엔티티는 새 이름표에 다시 쓴다.
    CW_MATERIALS = {"mullion": "알루미늄", "glass": "유리", "opaque": "불투명 패널", "door": "문"}

    @staticmethod
    def _cw_cat(w) -> str:
        return "mullion" if w[0] == "IfcMember" else w[1]

    def _cw_adopt(self, eid: str, e) -> None:
        """가져온 파일의 커튼월 부속(이름 끝 "-m:키"·"-p:키", GlobalId = part_guid)을 이어받는다."""
        have = {}
        for r in getattr(e, "IsDecomposedBy", None) or ():
            for k in r.RelatedObjects:
                name = k.Name if isinstance(k.Name, str) else ""
                cut = max(name.rfind("-m:"), name.rfind("-p:"))
                if cut < 0:
                    continue
                label = name[cut + 1:]
                if k.GlobalId == part_guid(eid, label):
                    have[label] = k
        if have:
            self.parts[eid] = have

    def _cw_rels(self, eid: str, parts: dict, cat: dict) -> None:
        """집합 관계와 종류별 재료 지정을 parts({이름표: 엔티티})·cat({이름표: 종류})에 맞춘다(대상은 이름표 순서)."""
        f = self.f
        e = self.ents[eid]
        gid = part_guid(eid, "agg")
        rel = next((r for r in (getattr(e, "IsDecomposedBy", None) or ()) if r.GlobalId == gid), None)
        want = [parts[k] for k in sorted(parts)]
        if not want:
            if rel is not None:
                f.remove(rel)
        elif rel is None:
            f.createIfcRelAggregates(gid, None, None, None, e, want)
        elif list(rel.RelatedObjects) != want:
            rel.RelatedObjects = want
        mine = self._cw_mats.get(eid) or {}
        for c, mname in sorted(self.CW_MATERIALS.items()):
            mname = mine.get(c, mname)   # 커튼월별 재료(W2 열 번째 PR)
            mg = part_guid(eid, f"material:{c}")
            try:
                mr = f.by_guid(mg)
            except RuntimeError:
                mr = None
            objs = [parts[k] for k in sorted(parts) if cat[k] == c]
            if not objs:
                if mr is not None:
                    mat = mr.RelatingMaterial
                    f.remove(mr)
                    self._gc_material(mat)
            elif mr is None:
                f.createIfcRelAssociatesMaterial(mg, None, None, None, objs, self._named_material(mname))
            else:
                if list(mr.RelatedObjects) != objs:
                    mr.RelatedObjects = objs
                old = mr.RelatingMaterial
                if not (old.is_a("IfcMaterial") and old.Name == mname):   # 재료를 바꿨다: 새 재료로 잇고 안 쓰이게 된 옛 재료는 치운다
                    mr.RelatingMaterial = self._named_material(mname)
                    self._gc_material(old)

    def _shape_solids(self, shape) -> list:
        """모양 부속(W2 열 번째 PR) -> 다각형마다 IfcExtrudedAreaSolid: 틀 (e1, e2) 평면의 다각형 프로필을 원점 + h0·n 에서 n 방향으로 (h1 - h0) 돌출(SweptSolid).
        배치 축 = n, 참조 방향 = e1 이라 프로필의 y 축이 e2 다(n = e1 x e2)."""
        f = self.f
        polys, o, e1, _e2, n, h0, h1 = shape
        out = []
        for pl in polys:
            line = f.createIfcPolyline([f.createIfcCartesianPoint([float(a), float(b)]) for a, b in list(pl) + [pl[0]]])
            prof = f.createIfcArbitraryClosedProfileDef("AREA", None, line)
            pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([float(o[0] + n[0] * h0), float(o[1] + n[1] * h0), float(o[2] + n[2] * h0)]),
                                              f.createIfcDirection([float(x) for x in n]), f.createIfcDirection([float(x) for x in e1]))
            out.append(f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([0.0, 0.0, 1.0]), float(h1 - h0)))
        return out

    def _cw_sync(self, eid: str, spec: dict | None) -> None:
        """커튼월 부속을 명세(spec["parts"], None 이면 모두 지운다)에 맞춘다."""
        f = self.f
        e = self.ents[eid]
        if spec is not None:   # 커튼월별 부속 재료(W2 열 번째 PR): 없으면 기본 재료(CW_MATERIALS)
            self._cw_mats[eid] = dict(spec.get("materials", ()))
        have = self.parts.get(eid, {})
        sig = self._cw_sig.setdefault(eid, {})
        wanted = {} if spec is None else {w[0]: w[1:] for w in spec["parts"]}
        name = "" if spec is None else spec["name"]
        gone = sorted(set(have) - set(wanted))
        pool: dict[str, list] = {}
        keep = {k: v for k, v in have.items() if k in wanted}
        for k in gone:   # 집합·재료 관계에서 먼저 뺀 뒤 새 이름표에 다시 쓰거나 지운다
            pool.setdefault(have[k].is_a(), []).append(have[k])
            sig.pop(k, None)
        if gone:
            self._cw_rels(eid, keep, {k: self._cw_cat(wanted[k]) for k in keep})
        ax = self._cw_axis.get(eid)
        if ax is not None:   # 마지막 부속과 함께 지워졌을 수 있다
            try:
                if f.by_id(ax.id()) != ax:
                    ax = None
            except RuntimeError:
                ax = None
        for label in sorted(wanted):
            w = wanted[label]
            part = keep.get(label)
            if part is not None and not part.is_a(w[0]):
                pool.setdefault(part.is_a(), []).append(part)
                keep.pop(label)
                part = None
            pname = f"{name}-{label}"
            if part is not None and sig.get(label) == w and part.Name == pname:
                continue
            if part is None:
                reuse = pool.get(w[0])
                if reuse:
                    part = reuse.pop()
                else:
                    part = f.create_entity(w[0])
                    if ax is None:
                        ax = self._cw_axis[eid] = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([0.0, 0.0, 0.0]), None, None)
                    part.ObjectPlacement = f.createIfcLocalPlacement(e.ObjectPlacement, ax)
                part.GlobalId = part_guid(eid, label)
                keep[label] = part
            part.Name = pname
            cls, sub, quad, z0, h = w[:5]
            shape = w[5] if len(w) > 5 else None   # 모양 부속(W2 열 번째 PR): 틀 다각형을 기울어진(또는 잘린) 면에서 돌출한다
            if cls == "IfcMember":
                part.PredefinedType, part.ObjectType = "MULLION", sub
            elif cls == "IfcPlate":
                part.PredefinedType, part.ObjectType = "CURTAIN_PANEL", sub
            else:
                part.PredefinedType = "DOOR"
                part.OverallHeight = float(h)
                part.OverallWidth = float(math.sqrt((quad[1][0] - quad[0][0]) ** 2 + (quad[1][1] - quad[0][1]) ** 2))
            if sig.get(label, (None, None, None, None, None))[2:] != w[2:] or part.Representation is None:
                self._set_body(part, self._shape_solids(shape) if shape is not None else self._solid(quad, h, z0))
            sig[label] = w
        for parts in pool.values():   # 남은 엔티티는 지운다(관계에서는 이미 빠졌다)
            for p in parts:
                self._remove_product(p)
        if keep:
            self.parts[eid] = keep
        else:
            self.parts.pop(eid, None)
            self._cw_sig.pop(eid, None)
        self._cw_rels(eid, keep, {k: self._cw_cat(wanted[k]) for k in keep})
        if not keep and ax is not None:   # 부속이 모두 갔으면 함께 쓰던 배치 축도(부속 삭제가 이미 지웠을 수 있다)
            try:
                if not f.get_inverse(ax):
                    f.remove(ax)
            except RuntimeError:
                pass
            self._cw_axis.pop(eid, None)

    def _project_curtwall(self, eid, spec):
        e = self.ents[eid]
        e.Name = spec["name"]
        self._contain(eid, spec["level"])
        if e.ObjectPlacement is None:   # 배치는 레벨 좌표계 원점 그대로라 처음 한 번만 만든다(부속 배치가 이것을 가리킨다)
            self._place(e, self.ents[spec["level"]].ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        elif e.ObjectPlacement.PlacementRelTo != self.ents[spec["level"]].ObjectPlacement:
            e.ObjectPlacement.PlacementRelTo = self.ents[spec["level"]].ObjectPlacement
        if self.base_mode and eid in self.parts and eid not in self._cw_sig:   # 가져온 부속: 명세를 모르므로 한 번은 모두 다시 쓴다
            self._cw_sig[eid] = {}
        self._cw_sync(eid, spec)

    def _project_elevator(self, eid, spec):
        """승강기: 카(IfcTransportElement ELEVATOR)의 상자 몸체 하나. 카 바닥은 레벨 + base_offset, 배치는 레벨 좌표계 원점이다. 샤프트는 슬래브 구멍·벽이 표현한다."""
        from dobim_core.kinds import elevator_geom, elevator_ring
        e = self.ents[eid]
        e.Name = spec["name"]
        e.PredefinedType = "ELEVATOR"
        self._contain(eid, spec["level"])
        self._place(e, self.ents[spec["level"]].ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        gp = {"shaft": spec["shaft"], "cab": spec["cab"], "base_offset": spec["base_offset"], "rise": spec["rise"]}
        if "rotation" in spec:   # 승강기 회전(W2 열일곱 번째 PR): 카 바닥 사각형을 샤프트 가운데로 돌린 다각형을 압출한다
            gp["rotation"] = spec["rotation"]
        g = elevator_geom(gp)
        self._set_body(e, self._solid(elevator_ring(gp, g["cab"]), spec["cab"][2], z0=g["z0"]))

    def _project_space(self, eid, spec):
        """공간: 경계 프로필을 레벨 좌표계 원점에서 높이만큼 압출한다. 층 소속은 포함(IfcRelContainedInSpatialStructure)이 아니라 집합(IfcRelAggregates)이다.
        분류는 ObjectType 에 둔다(비어 있으면 비운다)."""
        f = self.f
        e = self.ents[eid]
        e.Name = spec["name"]
        e.ObjectType = spec["category"] or None
        e.PredefinedType = "INTERNAL"
        lv = spec["level"]
        if self.container_of.get(eid) != lv:
            self.flush_rels()
            if eid in self.container_of:
                api_aggregate.unassign_object(f, products=[e])
            api_aggregate.assign_object(f, products=[e], relating_object=self.ents[lv])
            self.container_of[eid] = lv
        self._place(e, self.ents[lv].ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        if spec.get("holes"):   # 섬 구멍(방 안의 기둥·닫힌 벽 고리): 구멍 있는 프로필을 압출한다
            self._set_body(e, self._voided_solid(spec["boundary"], spec["holes"], spec["height"], 0.0))
        else:
            self._set_body(e, self._solid(spec["boundary"], spec["height"]))

    def _project_site(self, eid, spec):
        """대지: 뼈대 대지 아래 하위 IfcSite 로 집합하고(IfcRelAggregates), 경계는 몸체 표현(GeometricCurveSet 의 닫힌 폴리라인)으로 둔다.
        표고는 RefElevation 이다. 방위·도로는 IFC 표준 속성이 아니라 의도(DoBIM_Intent)로 실려 왕복한다."""
        f = self.f
        e = self.ents[eid]
        root = self.site_root
        if root is None:
            raise DobimError("UNSUPPORTED", "뼈대 대지가 없는 파일에는 대지를 추가할 수 없다")
        e.Name = spec["name"]
        e.CompositionType = "ELEMENT"
        e.RefElevation = None if spec["elevation"] is None else float(spec["elevation"])
        if self.container_of.get(eid) != root.GlobalId:
            self.flush_rels()
            if eid in self.container_of:
                api_aggregate.unassign_object(f, products=[e])
            api_aggregate.assign_object(f, products=[e], relating_object=root)
            self.container_of[eid] = root.GlobalId
        self._place(e, root.ObjectPlacement, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        pts = list(spec["boundary"])
        curve = f.createIfcPolyline([f.createIfcCartesianPoint([float(x), float(y)]) for x, y in pts + [pts[0]]])
        item = f.createIfcGeometricCurveSet([curve])
        shape = e.Representation
        if shape is None:
            rep = f.createIfcShapeRepresentation(self.body, "Body", "GeometricCurveSet", [item])
            e.Representation = f.createIfcProductDefinitionShape(None, None, [rep])
        else:
            rep = shape.Representations[0]
            old = list(rep.Items)
            rep.Items = [item]
            for o in old:
                ue.remove_deep2(f, o)

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
        xdir = (1.0, 0.0, 0.0)
        at = spec.get("at")
        if at is not None:   # 곡선 벽 호스트(W2 ARC-04): 벽 배치 틀 안의 축 위 점·접선
            origin, xdir = (float(at[0]), float(at[1]), float(spec["sill"])), (float(at[2]), float(at[3]), 0.0)
        self._contain(eid, spec["level"])
        # 문은 호스트 벽의 국소 좌표에 놓으므로 벽을 옮겨도 이 투영은 다시 하지 않는다.
        self._place(e, wall.ObjectPlacement, origin, xdir)
        w2, t2 = spec["width"] / 2.0, spec["wall_thickness"] / 2.0
        rect = [(-w2, -t2), (w2, -t2), (w2, t2), (-w2, t2)]
        self._set_body(e, self._solid(rect, spec["height"]))
        if at is not None:   # 개구부 상자는 휜 벽 두께를 다 뚫도록 깊게
            rect = [(-w2, -at[4]), (w2, -at[4]), (w2, at[4]), (-w2, at[4])]
        # 개구부는 투영이 소유한다. 이미 있고 이 문·벽과 이어져 있으면 제자리에서 고쳐 GlobalId·붙은 속성을 유지한다(외부 도구가 GlobalId 를
        # 바꿨어도 그대로 둔다). 없으면 새로 만든다: GlobalId 는 되돌린 지우기가 남긴 값, 없으면 문 id 에서 결정적으로 얻는다.
        op = self.openings.get(eid)
        if op is not None and op.Representation is not None and any(
                r.is_a("IfcRelVoidsElement") and r.RelatingBuildingElement == wall for r in f.get_inverse(op)) and any(
                r.is_a("IfcRelFillsElement") and r.RelatedBuildingElement == e for r in f.get_inverse(op)):
            op.Name = spec["name"] + "-opening"
            self._place(op, wall.ObjectPlacement, origin, xdir)
            self._set_body(op, self._solid(rect, spec["height"]))
            return
        self._unproject_door(eid)
        op = api_root.create_entity(f, ifc_class="IfcOpeningElement")
        op.GlobalId = self._opening_gid.get(eid) or opening_guid(eid)
        op.Name = spec["name"] + "-opening"
        self._place(op, wall.ObjectPlacement, origin, xdir)
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

    # ------------------------------------------------------------ 뷰·주석 정의 (ADR-0015)
    def _defs_pset(self, create: bool):
        """프로젝트의 DoBIM_Defs 속성셋(없고 create 가 아니면 None). 프로젝트가 정확히 하나가 아니면 None(정의를 실을 곳이 모호하다)."""
        f = self.f
        projs = f.by_type("IfcProject")
        if len(projs) != 1:
            return None
        proj = projs[0]
        for r in proj.IsDefinedBy or ():
            d = r.RelatingPropertyDefinition if r.is_a("IfcRelDefinesByProperties") else None
            if isinstance(d, ifcopenshell.entity_instance) and d.is_a("IfcPropertySet") and d.Name == DEFS_PSET:
                return d
        if not create:
            return None
        ps = api_pset.add_pset(f, product=proj, name=DEFS_PSET)
        ps.GlobalId = derived_guid(self.project_guid, "defs:pset")   # 무작위 GlobalId 면 같은 프로젝트를 세션마다 내보낼 때 ifcdiff 가 바뀐 것으로 센다
        for r in f.get_inverse(ps):
            if r.is_a("IfcRelDefinesByProperties"):
                r.GlobalId = derived_guid(self.project_guid, "defs:rel")
        return ps

    @staticmethod
    def _text_of(p):
        """속성(IfcPropertySingleValue)의 문자열 값(아니면 None)."""
        if not p.is_a("IfcPropertySingleValue") or p.NominalValue is None:
            return None
        v = getattr(p.NominalValue, "wrappedValue", None)
        return v if isinstance(v, str) else None

    def adopt_defs(self, ids) -> None:
        """가져온 파일에서 되살린 정의 id(ids)의 속성을 이 모델이 소유한 것으로 이어받는다(현재 텍스트를 기억한다: 같으면 다시 쓰지 않는다)."""
        ps = self._defs_pset(False)
        if ps is None:
            return
        have = {p.Name: self._text_of(p) for p in ps.HasProperties or ()}
        for i in ids:
            if have.get(i) is not None:
                self._defs_text[i] = have[i]
        names = [getattr(p, "Name", None) for p in ps.HasProperties or ()]
        for p in ps.HasProperties or ():
            n = getattr(p, "Name", None)
            if not isinstance(n, str) or n in self._defs_text or names.count(n) != 1:   # 이름이 겹치는 속성은 어느 쪽이 원본인지 몰라 되돌리지 않는다
                continue
            if p.is_a("IfcPropertySingleValue"):
                nv = p.NominalValue
                self._ext_defs[n] = (nv.is_a(), nv.wrappedValue) if nv is not None else (None, None)
            else:
                self._ext_defs[n] = None

    def has_ext_def(self, eid: str) -> bool:
        """가져온 파일에 이 id 의 정의 속성이 외부 것으로 있는가(새 정의를 만들면 덮어쓰게 된다)."""
        return eid in self._ext_defs and eid not in self._defs_text

    def sync_defs(self, desired: dict[str, str]) -> bool:
        """IFC 의 정의 속성을 desired({요소 id: 텍스트})와 같게 한다. 소유한 속성만 더하고·고치고·지운다. 바꾼 게 없으면 False.
        마지막 소유 속성이 지워져 속성셋이 비면 속성셋도 지운다(정의가 없던 때와 같은 IFC)."""
        removed = [i for i in self._defs_text if i not in desired]
        changed = {i: t for i, t in desired.items() if self._defs_text.get(i) != t}
        if not removed and not changed:
            return False
        ps = self._defs_pset(create=bool(changed))
        if ps is None:   # 지울 것만 있는데 속성셋이 없다(이미 없다) 또는 프로젝트가 모호해 실을 곳이 없다
            for i in removed:
                self._defs_text.pop(i, None)
            return False
        f = self.f
        try:
            restore = [i for i in removed if self._ext_defs.get(i) is not None]   # 덮어썼던 외부 정의: 지우지 않고 원본으로 되돌린다
            props = {i: None for i in removed if i not in restore}
            props.update({i: f.createIfcText(t) for i, t in sorted(changed.items())})
            api_pset.edit_pset(f, pset=ps, properties=props)
            for p in ps.HasProperties or ():
                if getattr(p, "Name", None) in restore and p.is_a("IfcPropertySingleValue"):
                    tname, val = self._ext_defs[p.Name]
                    p.NominalValue = None if tname is None else f.create_entity(tname, val)
            if not [p for p in ps.HasProperties or () if p.is_a("IfcProperty")]:
                api_pset.remove_pset(f, product=f.by_type("IfcProject")[0], pset=ps)
        except BaseException as ex:   # 어디까지 적용됐는지 모른다: 오염으로 표시한다(엔진이 로그에서 다시 만든다)
            self.tainted = f"정의 속성 갱신 실패: {type(ex).__name__}: {ex}"
            raise
        for i in removed:
            self._defs_text.pop(i, None)
        self._defs_text.update(changed)
        return True

    def defs_texts(self) -> dict[str, str]:
        """IFC 에 실려 있는 정의 텍스트(소유한 것만, 일관성 시험용)."""
        ps = self._defs_pset(False)
        if ps is None:
            return {}
        out = {}
        for p in ps.HasProperties or ():
            v = self._text_of(p)
            if p.Name in self._defs_text and v is not None:
                out[p.Name] = v
        return out

    def intent_texts(self) -> dict[str, str]:
        """IFC 에 복제된 의도 (일관성 시험용)."""
        out = {}
        for eid, e in self.ents.items():
            p = ue.get_pset(e, INTENT_PSET, should_inherit=False)
            if p is not None:
                out[eid] = p.get("Intent")
        return out

    def validate(self, express: bool = True) -> dict:
        """내보낼 파일과 같은 내용을 검사한다: 첫 글자가 무효한 GlobalId(사용자 id)는 ifc.export 처럼 바꾼 사본으로 본다(globalid.py)."""
        from dobim_core import globalid as GI
        lg = ifcopenshell.validate.json_logger()
        try:
            f, _ = GI.encoded_copy(self.f)
            ifcopenshell.validate.validate(f, lg, express_rules=express)
        except Exception as ex:  # 검증기 자체의 실패도 오류로 센다
            return {"errors": 1, "warnings": 0, "samples": [f"검증기 예외: {ex!r}"]}
        errs = [s for s in lg.statements if s["level"] in ("error", "critical")]
        warns = [s for s in lg.statements if s["level"] == "warning"]
        return {"errors": len(errs), "warnings": len(warns), "samples": [s["message"][:200] for s in errs[:8]]}

    def write(self, path: str) -> None:
        self.f.write(path)
