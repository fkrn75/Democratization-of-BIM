# SPDX-License-Identifier: MPL-2.0
"""내보내는 IFC 의 GlobalId 를 항상 유효하게 하고, 가져올 때 원래 id 로 되돌린다(W2 실사용 결함 2).

IFC GlobalId 22자는 128비트 수를 6비트씩 적은 것이라 첫 글자는 0~3 만 될 수 있다. DoBIM 의 요소 id 는 같은 자모의 22자이지만
첫 글자를 검사하지 않았고(Id 형식), 기준 도면·예시·시험의 고정 id 도 첫 글자가 4 이상인 것이 많다(fixture.py, run_baseline.py).
입력에서 거절하면 그 id 를 모두 바꿔야 하고(기준 도면 SVG 에 뷰 id 가 실린다) 이미 만든 프로젝트를 이어 고칠 수 없게 된다.
그래서 요소 id(정본·상태·도면)는 그대로 두고, IFC 로 내보내는 사본에서만 무효한 GlobalId 를 결정적인 유효 값으로 바꿔 쓴다.
원래 id 는 IfcProject 의 속성셋 DoBIM_IdMap(속성 이름 = 바꾼 GlobalId, 값 = 원래 id)에 남기고, ifc.import 가 그 표를 읽어
GlobalId 를 원래 id 로 되돌린 뒤 가져온다(왕복해도 id·해시가 같다). 무효한 GlobalId 가 없으면 아무것도 바꾸지 않는다
(유효한 id 만 쓰는 프로젝트의 내보내기 바이트는 이전과 같다).
"""
from __future__ import annotations

import ifcopenshell

IDMAP_PSET = "DoBIM_IdMap"
_FIRST = frozenset("0123")


def is_valid(gid) -> bool:
    """IFC GlobalId 로 유효한가(22자, 첫 글자 0~3). 자모는 Id 형식이 이미 지킨다."""
    return isinstance(gid, str) and len(gid) == 22 and gid[0] in _FIRST


def invalid_roots(f) -> list:
    """GlobalId 가 무효한 IfcRoot 엔티티(파일 순서)."""
    return [e for e in f.by_type("IfcRoot") if not is_valid(e.GlobalId)]


def _guid(label: str) -> str:
    from dobim_core.ifc_model import _guid5   # 지연 import: ifc_model 이 크다
    return _guid5(f"idmap:{label}")


def encode(f) -> int:
    """f(내보낼 사본)의 무효한 GlobalId 를 결정적인 유효 값으로 바꾸고 대응표 속성셋을 IfcProject 에 붙인다. 바꾼 개수를 돌려준다.
    같은 모델이면 같은 값·같은 순서다(바꾼 값은 원래 id 만의 함수, 충돌하면 번호를 붙여 다시 뽑는다)."""
    bad = invalid_roots(f)
    if not bad:
        return 0
    projs = f.by_type("IfcProject")
    if len(projs) != 1:   # 대응표를 실을 곳이 모호하다: 바꾸지 않는다(검증 오류는 그대로 보인다)
        return 0
    used = {e.GlobalId for e in f.by_type("IfcRoot")}
    pairs = []
    for e in sorted(bad, key=lambda x: x.GlobalId):
        orig = e.GlobalId
        new, k = _guid(orig), 0
        while new in used:
            k += 1
            new = _guid(f"{orig}:{k}")
        used.add(new)
        e.GlobalId = new
        pairs.append((new, orig))
    props = [f.createIfcPropertySingleValue(new, None, f.createIfcIdentifier(orig), None) for new, orig in pairs]
    pid, oh = projs[0].GlobalId, projs[0].OwnerHistory   # IFC2x3 은 OwnerHistory 가 필수다: 프로젝트의 것을 같이 쓴다(IFC4 는 비어 있으면 비운다)
    ps = f.createIfcPropertySet(_guid(f"pset:{pid}"), oh, IDMAP_PSET, None, props)
    f.createIfcRelDefinesByProperties(_guid(f"rel:{pid}"), oh, None, None, [projs[0]], ps)
    return len(pairs)


def encoded_copy(f):
    """무효한 GlobalId 가 있으면 바꾼 사본(번호는 그대로)과 개수, 없으면 (f, 0). 정본 투영은 건드리지 않는다."""
    if not invalid_roots(f):
        return f, 0
    c = ifcopenshell.file.from_string(f.to_string())
    return c, encode(c)


def _upgrade_rep_types(f) -> int:
    """'SweptSolid' 인데 IfcSweptDiskSolid 를 담은 표현(W2 전 DoBIM 난간 등, IFC4 규칙 위반)을 'AdvancedSweptSolid' 로 고친다.
    지금 투영이 쓰는 값과 같아져 옛 파일의 저작 난간이 분리(동결)되지 않는다."""
    n = 0
    for r in f.by_type("IfcShapeRepresentation"):
        items = r.Items or ()
        if (r.RepresentationType == "SweptSolid" and any(i.is_a("IfcSweptDiskSolid") for i in items)
                and all(i.is_a("IfcSweptDiskSolid") or i.is_a("IfcSweptAreaSolid") for i in items)):
            r.RepresentationType = "AdvancedSweptSolid"
            n += 1
    return n


def restore_ids(f) -> int:
    """대응표(DoBIM_IdMap)를 읽어 바꾼 GlobalId 를 원래 id 로 되돌리고 표를 뗀다(메모리의 f). 되돌린 개수를 돌려준다."""
    n = 0
    for proj in f.by_type("IfcProject"):
        for rel in list(proj.IsDefinedBy or ()):
            ps = rel.RelatingPropertyDefinition if rel.is_a("IfcRelDefinesByProperties") else None
            if ps is None or not ps.is_a("IfcPropertySet") or ps.Name != IDMAP_PSET:
                continue
            by_gid = {e.GlobalId: e for e in f.by_type("IfcRoot")}
            for p in ps.HasProperties or ():
                v = getattr(p, "NominalValue", None)
                orig = getattr(v, "wrappedValue", None)
                e = by_gid.get(p.Name)
                if e is None or not isinstance(orig, str) or len(orig) != 22 or orig in by_gid:
                    continue   # 지워진 요소이거나 원래 id 가 이미 쓰인다: 바꾼 값 그대로 둔다
                e.GlobalId = orig
                by_gid[orig] = e
                n += 1
            props = list(ps.HasProperties or ())
            if len(rel.RelatedObjects) > 1:
                rel.RelatedObjects = tuple(x for x in rel.RelatedObjects if x != proj)
            else:
                f.remove(rel)
            if not [x for x in f.get_inverse(ps)]:
                f.remove(ps)
                for p in props:
                    if not f.get_inverse(p):
                        f.remove(p)
    return n


def prepare_import(data: bytes) -> bytes:
    """가져올 IFC4 바이트를 고친다: 대응표(DoBIM_IdMap)가 있으면 GlobalId 를 원래 id 로 되돌리고 표를 떼고, 옛 난간 표현 유형을 고친다.
    고칠 것이 없으면(대부분의 파일) 읽지도 않고 그대로 돌려준다."""
    has_map = IDMAP_PSET.encode("ascii") in data
    has_disk = b"IFCSWEPTDISKSOLID" in data and b"'SweptSolid'" in data   # STEP 엔티티 이름은 대문자다
    if not (has_map or has_disk):
        return data
    from dobim_core.ifc_model import open_ifc_bytes
    try:
        f = open_ifc_bytes(data)
    except Exception:
        return data   # 읽지 못하는 파일은 plan_import 가 구조화 오류로 거절한다
    if f.schema != "IFC4":
        return data
    n = restore_ids(f) if has_map else 0
    n += _upgrade_rep_types(f) if has_disk else 0
    return f.to_string().encode("utf-8") if n or has_map else data


def open_exported(path):
    """ifc.export 가 쓴 파일을 열고 GlobalId 를 원래 요소 id 로 되돌린 메모리 사본(파일은 그대로). 내보낸 파일을 요소 id 로 찾는 도구·시험용."""
    f = ifcopenshell.open(str(path))
    restore_ids(f)
    return f
