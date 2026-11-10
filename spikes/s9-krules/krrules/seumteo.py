"""세움터 프로파일(초안) 전용 검사. 소속 seumteo 인 KR-SEU-* 와 공통 검사의 세움터용 변형을 둔다.

초안 경고: 근거는 세움터 BIM 설계기준서 v1.0(안, 2021-12)이며 공식 배포본을 확보하지 못했고(비공식 사본으로만 대조),
실제 세움터식 내보내기 파일로는 시험하지 못했다. 설계기준서는 IFC 엔티티·속성세트(Pset) 이름을 한 번도 지정하지 않는다(Revit 용어와
내보내기 설정만 적음). 그래서 아래 가정은 전부 해석이며 실제 파일로 확인하기 전에는 판정을 위반(fail)으로 세우지 않고
경고(warn)·정보(info)로 낮춘 곳이 많다.

가정 1. 속성 이름이 `AIS_*` 이면 어떤 속성세트(Pset)에 있든 찾는다(Pset 이름은 사본에 없어 불문). 이름 비교는 공백 무시·소문자.
가정 2. 세움터의 면적 객체(용도·방화구획, 건축면적, 대지면적)는 IFC 에서 IfcSpace 로 나올 수 있다고 본다. 그러면 실 공간과 섞이므로
        `AIS_분류코드` 값이 면적 객체 코드(A+5자리=용도구획, B=건축면적, F=방화구획, S=대지면적)이거나 면적 객체 전용 속성
        (AIS_연면적제외·AIS_용적률제외·AIS_바닥면적제외·방화구획여부·AIS_실사용·AIS_공구상면적)이 있으면 면적 객체로 구분해
        실 공간 검사(실명·분류코드·중첩)에서 뺀다. 같은 속성 이름 `AIS_분류코드` 가 층(L지상 등)·면적·룸에 모두 쓰여 값 형태로 가른다.
가정 3. 룸 `AIS_분류코드` 는 부속서 2-2 의 5자리 기능분류(33xxx·34xxx) 형식이다.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import codechecks, slots
from .codechecks import MAIN_CLASSES, SIZE_LIMIT_BYTES
from .common import _ex, _res, class_codes, class_refs, norm, ok, ok_number, ok_part_class, psets

AIS_CODE = "AIS_분류코드"
MAX_EX = 5
_AREA_CODE = re.compile(r"^(A\d{5}|B|F|S)$")
_ROOM_CODE = re.compile(r"^3[34]\d{3}$")
_STOREY_CODE = re.compile(r"^L(지상|지하|옥탑|피트)$")
_KIND = {"A": "용도구획", "B": "건축면적", "F": "방화구획", "S": "대지면적"}
_AREA_MARKERS = ["AIS_연면적제외", "AIS_용적률제외", "AIS_바닥면적제외", "방화구획여부", "AIS_실사용", "AIS_공구상면적"]
_AREA_VALUE_NAMES = ["면적", "Area", "NetFloorArea", "GrossFloorArea", "GrossArea", "NetArea", "GrossPlannedArea", "FootprintArea"]
# 면적 객체가 실려 나올 수 있는 클래스(가정 2). 스키마에 없는 클래스는 건너뛴다.
_CARRIERS = ("IfcSpace", "IfcZone", "IfcSpatialZone", "IfcAnnotation", "IfcBuildingElementProxy", "IfcGroup", "IfcSite", "IfcBuilding")

# 파일명(안): 프로젝트명_동명_BG|BA|BS_ALL.ifc, 프로젝트명_ALL_BC_ALL.ifc. 대형·단위세대는 마지막 칸이 부위·세대 이름이라
# ALL 로 고정하지 않고 밑줄 없는 한 칸으로 넓혔다(예시가 안 안에 있음). 대소문자는 구분한다.
_SEU_NAME = re.compile(r"\A(?:(?P<proj>.+)_(?P<bldg>[^_]+)_(?P<kind>BG|BA|BS)_(?P<part>[^_.]+)|(?P<proj2>.+)_ALL_BC_ALL)\.ifc\Z")


# ---------------------------------------------------------------- 속성 탐색 도구

def flat_props(e):
    """객체(유형 객체 포함)의 모든 Pset·수량세트 속성을 {정규화한 속성 이름: 값} 하나로 합친다(Pset 이름 불문, 먼저 나온 값 우선)."""
    out = {}
    for props in psets(e).values():
        for k, v in props.items():
            if k != "id":
                out.setdefault(norm(k), v)
    return out


def ais_value(flat, name=AIS_CODE):
    """합친 속성에서 name 의 값(유효할 때만). 문자열은 앞뒤 공백을 걷는다."""
    v = flat.get(norm(name))
    if not ok(v):
        return None
    return v.strip() if isinstance(v, str) else v


def area_kind(e, flat=None):
    """면적 객체면 종류(용도구획·건축면적·방화구획·대지면적), 아니면 None(가정 2). 단서가 약하면 실 공간으로 남겨 검사한다.
    면적 객체로 보는 조건: (1) AIS_분류코드 값이 A+5자리·B·F·S 이고, 그에 더해 (2a) 면적 객체 전용 속성(AIS_연면적제외 등)이
    값과 무관하게 이름으로 붙어 있거나 (2b) 실 정체(LongName·분류 참조·공간분류코드)가 없고 면적 값이 있을 것.
    마커 속성만 있거나 코드만 있으면 면적 객체로 보지 않는다(실의 코드 오입력·무관한 플래그가 검사를 빠져나가는 것을 막는다)."""
    flat = flat_props(e) if flat is None else flat
    v = ais_value(flat)
    if isinstance(v, str):
        if _AREA_CODE.match(v):
            marker = any(norm(m) in flat for m in _AREA_MARKERS)
            no_identity = (not ok(getattr(e, "LongName", None)) and not class_refs(e)
                           and ais_value(flat, "공간분류코드") is None and _has_area_value(flat))
            if marker or no_identity:
                return _KIND[v[0]]
        return None  # 그 밖의 값(룸 코드이거나 형식 오류, 단서가 약한 코드)은 실 공간으로 두고 KR-SEU-06 이 본다
    return None


def _by_type(f, cls):
    try:
        return list(f.by_type(cls))
    except Exception:  # 스키마에 없는 클래스
        return []


def split_spaces(f):
    """IfcSpace 를 (실 공간 목록, 면적 객체로 보이는 [(공간, 종류)] 목록)으로 나눈다."""
    rooms, areas = [], []
    for s in _by_type(f, "IfcSpace"):
        k = area_kind(s)
        if k is None:
            rooms.append(s)
        else:
            areas.append((s, k))
    return rooms, areas


def area_objects(f, kind=None):
    """면적 객체 후보 [(객체, 종류, 합친 속성)]: IfcSpace 외 몇 가지 클래스도 훑는다(가정 2). kind 를 주면 그 종류만."""
    seen, out = set(), []
    for cls in _CARRIERS:
        for e in _by_type(f, cls):
            if e.id() in seen:
                continue
            seen.add(e.id())
            flat = flat_props(e)
            k = area_kind(e, flat)
            if k is not None and (kind is None or k == kind):
                out.append((e, k, flat))
    return out


def _has_area_value(flat):
    return any(ok_number(flat.get(norm(n))) for n in _AREA_VALUE_NAMES)


def _with_excluded(res, areas):
    """실 공간 검사에서 면적 객체로 보고 뺀 개수와 예시(이름·GlobalId·코드)를 결과에 노출한다."""
    if not areas:
        return res
    ex = [{"name": s.Name, "global_id": s.GlobalId, "code": ais_value(flat_props(s))} for s, _ in areas[:MAX_EX]]
    note = (res.get("note") + "; " if res.get("note") else "") + f"면적 객체로 보고 제외한 IfcSpace {len(areas)}개(AIS_분류코드+전용 속성 단서, 추정)"
    return dict(res, note=note, excluded_area_objects={"count": len(areas), "examples": ex})


def _soften(res, why, to="warn"):
    """fail 을 to(warn·info)로 낮추고 이유를 노트에 붙인다. 판정 근거가 해석이라 위반으로 세우지 않을 때 쓴다."""
    if res.get("status") != "fail":
        return res
    note = (res.get("note") + "; " if res.get("note") else "") + why
    return dict(res, status=to, note=note)


# ---------------------------------------------------------------- 파일·폴더

def check_filename_seumteo(f, filename="", deliverable=False, **_):
    """KR-SEU-01: 납품 IFC 파일명 = 프로젝트명_동명_BG|BA|BS_ALL.ifc 또는 프로젝트명_ALL_BC_ALL.ifc(세움터 설계기준서 v1.0(안) 5.1.3, 조달청 KR-CODE-02 대체)."""
    if not deliverable:
        return _res("na", note="납품 파일명 규칙: 납품 후보 파일에만 적용(--deliverable)")
    ok_ = bool(_SEU_NAME.match(filename))
    return _res("pass" if ok_ else "fail", 1, 0 if ok_ else 1,
                [] if ok_ else [{"reason": f"파일명 '{filename}' 이 프로젝트명_동명_BG|BA|BS_구분.ifc / 프로젝트명_ALL_BC_ALL.ifc 형식이 아님"}],
                note="(안) 표시가 붙은 절의 형식. 마지막 칸은 ALL·부위·세대 이름 모두 인정")


def check_package_seumteo(root: Path):
    """KR-SEU-02: 납품 폴더 구성: 최상위 'BIM'(IFC)·'도면'(PDF) 폴더, BIM 아래 IFC 가 세움터 파일명 형식(5.1.3 폴더 안, 조달청 KR-CODE-18 대체)."""
    root = Path(root)
    names = {p.name for p in root.iterdir() if p.is_dir()} if root.is_dir() else set()
    miss = [{"reason": f"{n} 폴더 없음"} for n in ("BIM", "도면") if n not in names]
    ifcs = sorted((root / "BIM").rglob("*.ifc")) if (root / "BIM").is_dir() else []
    bad_names = [{"reason": f"IFC 파일명 규칙 위반: {p.name}"} for p in ifcs if not _SEU_NAME.match(p.name)]
    if "BIM" in names and not ifcs:
        miss.append({"reason": "BIM 폴더에 IFC 없음"})
    status = "fail" if bad_names else ("warn" if miss else "pass")
    return _res(status, 1 + len(ifcs), len(bad_names) + len(miss), bad_names + miss,
                note="폴더 구성은 (안)이며 사본이 평탄화돼 있어 사람 확인 필요. 파일명 위반은 fail, 폴더 누락은 warn")


def check_compression_size(f, path=None, filename="", **_):
    """KR-SEU-03: IFC 는 압축하지 않은 상태로 제출함이 원칙이고 용량 상한은 없음(세움터 설계기준서 v1.0(안) 5장, 조달청 KR-CODE-16 대체)."""
    name_zip = Path(filename or "").suffix.lower() in (".ifczip", ".zip")
    magic_zip = False
    size = None
    if path and Path(path).is_file():
        size = Path(path).stat().st_size
        with open(path, "rb") as fp:
            magic_zip = fp.read(4) == b"PK\x03\x04"
    if name_zip or magic_zip:
        return _res("warn", 1, 1, [{"reason": "압축 파일로 보임(확장자 .ifczip/.zip 또는 zip 시그니처): 세움터는 IFC 를 압축하지 않고 제출함이 원칙"}],
                    note="압축하지 말 것(권고). 조달청의 300MB 초과 zip 허용은 세움터에 적용하지 않음")
    if size is not None and size > SIZE_LIMIT_BYTES:
        return _res("warn", 1, 1, [{"reason": f"{size} 바이트: 세움터에는 용량 상한이 없으나 불필요한 정보를 제거해 크기를 줄이도록 함"}],
                    note="원본 200MB 분할 검토선(국토부 발주자편)을 참고 값으로만 씀. 압축하지 말 것")
    return _res("pass", 1, 0, note="압축 파일 아님; 세움터는 용량 상한을 두지 않고 비압축 제출이 원칙")


# ---------------------------------------------------------------- 실 공간(면적 객체 제외)

def check_room_name(f, **_):
    """KR-SEU-04: 실 공간(면적 객체 제외)에는 실명이 있어야 함: IfcSpace.LongName 우선, Name 보조(세움터 업무지침서: 공간명이 모델에 있어야 조회, IFC 슬롯은 사본에 없음)."""
    rooms, areas = split_spaces(f)

    def finder(s):
        if ok(getattr(s, "LongName", None)):
            return "IfcSpace.LongName"
        if ok(s.Name):
            return "IfcSpace.Name(보조, 추정)"
        return None
    res = slots._evaluate(rooms, finder, "LongName 도 Name 도 없음",
                          note="실명 IFC 슬롯은 사본에 없어 추정이라 없으면 위반이 아니라 경고")
    return _with_excluded(_soften(res, "추정 슬롯(IFC 위치 미상)이라 경고로 낮춤"), areas)


def _room_code_finder(s):
    if class_refs(s):
        return "분류 참조(IfcClassificationReference)"
    flat = flat_props(s)
    if ais_value(flat, "공간분류코드") is not None:
        return "속성 공간분류코드"
    if ais_value(flat) is not None:
        return "속성 AIS_분류코드(Pset 불문)"
    return None


def check_room_class_present(f, **_):
    """KR-SEU-05: 실 공간(면적 객체 제외)에는 공간분류코드가 있어야 함: 분류 참조, '공간분류코드' 속성 또는 'AIS_분류코드' 속성(설계기준서 부속서 1-2, 조달청 KR-IDS-05 대체)."""
    rooms, areas = split_spaces(f)
    res = slots._evaluate(rooms, _room_code_finder, "분류 참조·'공간분류코드'·'AIS_분류코드' 모두 없음",
                          note="세움터는 룸 분류코드를 AIS_분류코드 에 넣게 함(같은 이름이 층·면적에도 쓰여 값 형태로 대상을 가름)")
    return _with_excluded(res, areas)


def check_room_class_format(f, **_):
    """KR-SEU-06: 실 공간(면적 객체 제외)의 분류코드는 5자리 기능분류(33xxx·34xxx) 형식(설계기준서 부속서 2-2, 조달청 KR-CODE-09 대체). 목록 전체 대조는 하지 않음."""
    rooms, areas = split_spaces(f)
    n, bad = 0, []
    for s in rooms:
        flat = flat_props(s)
        codes = [c for c in class_codes(s) if ok(c)]
        for nm in ("공간분류코드", AIS_CODE):
            v = ais_value(flat, nm)
            if v is not None:
                codes.append(v)
        if not codes:
            continue  # 코드 자체가 없는 경우는 KR-SEU-05 몫
        n += 1
        if not any(_ROOM_CODE.match(str(c).strip()) for c in codes):
            bad.append(_ex(s, f"분류 코드 {codes} 이 룸 코드(5자리 33xxx·34xxx)도 면적 객체 코드(A+5자리·B·F·S)도 아님"))
    if not n:
        return _with_excluded(_res("na", note="분류코드가 있는 실 공간 없음(없는 경우는 KR-SEU-05)"), areas)
    return _with_excluded(_res("pass" if not bad else "fail", n, len(bad), bad,
                               note="형식만 확인(부속서 2-2 의 236개 목록 대조 안 함)"), areas)


def check_room_overlap(f, **kw):
    """KR-CODE-10: 같은 층의 공간객체끼리 겹치지 않음(조달청 5.2.2(3)바, 세움터 설계기준서 2.4 공간객체 중첩 금지). 세움터 프로파일에서는 면적 객체로 보이는 IfcSpace 를 빼고 실 공간끼리만 비교."""
    spaces = list(f.by_type("IfcSpace"))
    rooms, areas = split_spaces(f)
    res = codechecks.check_space_overlap(f, spaces=rooms, **kw)
    if areas:
        res = _with_excluded(res, areas)
        res["note"] += ": 실·용도구획·방화구획 사이 겹침은 허용"
    return res


def check_area_overlap(f, **_):
    """KR-SEU-13: 같은 종류 면적 객체(용도구획끼리, 방화구획끼리)는 서로 겹치지 않음(설계기준서 2.4). 종류 구분이 추정이라 위반이 아니라 경고."""
    _, areas = split_spaces(f)
    bad, pairs = [], 0
    for kind in ("용도구획", "방화구획"):
        objs = [s for s, k in areas if k == kind]
        if len(objs) < 2:
            continue
        r = codechecks.check_space_overlap(f, spaces=objs)
        pairs += r["applicable"] if r["status"] in ("pass", "fail") else 0
        if r["status"] == "fail":
            bad += [dict(e, reason=f"{kind}: " + e["reason"]) for e in r["examples"]]
    note = "면적 객체가 IfcSpace 로 나오고 AIS_분류코드 값(A+5자리·F)으로 종류가 가려진다는 추정에 기대므로 위반이 아니라 경고"
    if not pairs and not bad:
        return _res("na", note="같은 종류 면적 객체 쌍 없음; " + note)
    return _res("warn" if bad else "pass", pairs, len(bad), bad, note)


# ---------------------------------------------------------------- 부위(범용객체·공종·부위분류)

def check_proxy_part_class(f, **_):
    """KR-SEU-07: 범용객체(IfcBuildingElementProxy)에는 부위분류 정보가 있어야 함: 분류 참조, '부위분류' 또는 '건설정보분류체계-부위분류' 속성(설계기준서 부속서 1-6 범용객체, 조달청 KR-IDS-06 대체)."""
    def finder(e):
        if class_refs(e):
            return "분류 참조(IfcClassificationReference)"
        flat = flat_props(e)
        for nm in ("부위분류", "건설정보분류체계-부위분류"):
            if ok_part_class(flat.get(norm(nm))):
                return "속성 " + nm
        return None
    res = slots._evaluate(list(f.by_type("IfcBuildingElementProxy")), finder, "분류 참조도 '부위분류' 속성도 없음",
                          extra_note=lambda n: f"범용객체 {n}개")
    return _soften(res, "세움터 사본 안에서 부위분류는 범용객체에 필수(부속서 1-6)이면서 필수 입력 대상 아님(부속서 2-5)이라 경고로 낮춤")


def check_part_work_class(f, **_):
    """KR-SEU-08: 부위 객체의 공종분류·부위분류·조달청표준공사코드 속성(설계기준서 부속서 1-6 부위분류·공종분류, 세움터 필수 아님, 조달청 KR-CODE-19 대체)."""
    targets = [e for c in MAIN_CLASSES for e in f.by_type(c)]
    if not targets:
        return _res("na", note="적용 대상 부위 객체 없음")
    names = ["공종분류", "부위분류", "조달청표준공사코드"]
    via, missing = {}, []
    for e in targets:
        flat = flat_props(e)
        hit = next((n for n in names if ok(flat.get(norm(n)))), None)
        if hit:
            via["속성 " + hit] = via.get("속성 " + hit, 0) + 1
        else:
            missing.append(e)
    note = "세움터는 부위분류·공종분류 입력이 필수 아님(부속서 2-5·2-6)이라 없어도 위반이 아님; 정보로만 노출"
    return _res("pass" if not missing else "info", len(targets), len(missing), [_ex(e, "공종분류·부위분류·조달청표준공사코드 속성 없음") for e in missing],
                note, found_via=via)


# ---------------------------------------------------------------- 면적 객체(용도·면적), 시설개요

def _area_kind_check(f, kind, label, extra=""):
    objs = area_objects(f, kind)
    if not objs:
        return _res("info", 0, 0, note=f"{label} 면적 객체를 찾지 못함: 면적 객체의 IFC 위치가 사본에 없어 위반으로 보지 않음"
                                      "(개요·대지 모델이 아닌 파일이면 정상)" + extra)
    no_area = [(e, k) for e, k, flat in objs if not _has_area_value(flat)]
    note = f"{label} 면적 객체 {len(objs)}개 발견(속성 AIS_분류코드 값 기준, 클래스: " + \
        ", ".join(sorted({e.is_a() for e, _, _ in objs})) + ")" + extra
    if no_area:
        return _res("info", len(objs), len(no_area), [_ex(e, "면적 값 속성/수량을 못 찾음(형상에서 계산될 수도 있어 위반 아님)") for e, _ in no_area],
                    note, found_via={f"면적 객체({label})": len(objs)})
    return _res("pass", len(objs), 0, note=note, found_via={f"면적 객체({label})": len(objs)})


def check_use_area_objects(f, **_):
    """KR-SEU-09: 용도·연면적은 면적 객체(용도구획: AIS_분류코드 A+5자리)에서 읽음(설계기준서 부속서 1-3, 업무지침서 서식입력, 조달청 KR-IDS-14·16 대체)."""
    res = _area_kind_check(f, "용도구획", "용도구획", extra="; 연면적 제외 플래그(AIS_연면적제외 등)와 합산 일치는 판정하지 않음")
    return res


def check_footprint_object(f, **_):
    """KR-SEU-10: 건축면적은 면적 객체(AIS_분류코드 B)에서 읽음(설계기준서 부속서 1-4, 조달청 KR-IDS-17 대체)."""
    return _area_kind_check(f, "건축면적", "건축면적")


def check_site_area_object(f, **_):
    """KR-SEU-11: 대지면적은 면적 객체(AIS_분류코드 S)에서 읽음(설계기준서 부속서 1-4, 조달청 KR-IDS-18 대체)."""
    return _area_kind_check(f, "대지면적", "대지면적")


def check_facility_overview(f, **_):
    """KR-SEU-12: 시설개요 속성(사업명·주소·층수)은 세움터 필수 입력 대상이 아님(설계기준서 부속서 1-1, 조달청 KR-IDS-12·13·15 대체). 있는지만 알림."""
    parts = [("사업명", slots.project_name_present), ("주소", slots.site_address_present), ("층수", slots.building_floors_present)]
    seen = []
    for label, fn in parts:
        r = fn(f)
        seen.append((label, r["status"]))
    txt = ", ".join(f"{lab}={'있음' if st == 'pass' else '없음' if st == 'fail' else '대상 없음'}" for lab, st in seen)
    return _res("info", len(parts), 0, note=f"세움터 필수 아님(건축인허가 작성 때 세움터 시스템에서 입력): {txt}")


def check_bil30(f, **_):
    """KR-SEU-14: 형상 표현수준은 BIL 30 기본(설계기준서 2.1·부속서 3). 조달청 BIL 40 과 다르며 형상 상세도는 이 도구가 판정하지 않음."""
    return _res("info", 0, 0, note="BIL 30 이 기본(조달청은 BIL 40). 형상 상세도 평가는 구현하지 않아 판정 없음")


# ---------------------------------------------------------------- 추가 후보 노트(C-1~C-11, 판정 없음)

def _scan(f):
    """속성 이름(정규화) -> [(객체 클래스, 객체 id)]. 세움터 AIS_ 후보 노트용(Pset 이름 불문)."""
    hits = {}
    classes = set(_CARRIERS) | set(MAIN_CLASSES) | {"IfcBuildingStorey", "IfcProject", "IfcDoor", "IfcWindow"}
    seen = set()
    for cls in sorted(classes):
        for e in _by_type(f, cls):
            if e.id() in seen:
                continue
            seen.add(e.id())
            for k in flat_props(e):
                hits.setdefault(k, []).append((e.is_a(), e.id()))
    return hits


def _n(hits, name, cls=None):
    return sum(1 for c, _ in hits.get(norm(name), []) if cls is None or c == cls)


def check_candidate_notes(f, **_):
    """KR-SEU-15: 세움터 추가 후보 11건(C-1~C-11)의 관찰 노트. 판정하지 않고 모델에서 찾은 개수만 알림(설계기준서 v1.0(안) 부속서 1·2.4·5)."""
    hits = _scan(f)
    storeys = list(f.by_type("IfcBuildingStorey"))
    rooms, areas = split_spaces(f)
    kinds = {}
    for _, k in areas:
        kinds[k] = kinds.get(k, 0) + 1
    ws = sum(1 for s in storeys if s.Name and any(ch.isspace() for ch in s.Name))
    st_ok = sum(1 for s in storeys if _STOREY_CODE.match(str(ais_value(flat_props(s)) or "")))
    doors = len(f.by_type("IfcDoor"))
    wins = len(f.by_type("IfcWindow"))
    overview = [n for n in ("시설종류", "대표용도코드", "지역지구", "도로현황", "구조") if hits.get(norm(n))]
    part_cls = sum(1 for k in ("부위분류", "공종분류") if hits.get(norm(k)))
    area_nodes = area_objects(f)
    notes = [
        {"id": "C-1", "topic": "층 이름 공백 금지", "observed": f"층 {len(storeys)}개 중 이름에 공백이 있는 층 {ws}개"},
        {"id": "C-2", "topic": "층 AIS_분류코드(L지상·L지하·L옥탑·L피트)", "observed": f"층 {len(storeys)}개 중 형식이 맞는 값 {st_ok}개"},
        {"id": "C-3", "topic": "개요 면적 객체와 분류코드(A+5자리·F·B), 방화구획여부·제외 플래그",
         "observed": f"면적 객체로 보이는 IfcSpace {len(areas)}개 {dict(sorted(kinds.items()))}; 방화구획여부 {_n(hits, '방화구획여부')}개, "
                     f"AIS_연면적제외 {_n(hits, 'AIS_연면적제외')}·AIS_용적률제외 {_n(hits, 'AIS_용적률제외')}·AIS_바닥면적제외 {_n(hits, 'AIS_바닥면적제외')}개"},
        {"id": "C-4", "topic": "대지 면적 객체(S), AIS_실사용, 프로젝트 AIS_공구상면적",
         "observed": f"AIS_실사용 {_n(hits, 'AIS_실사용')}개, AIS_공구상면적 {_n(hits, 'AIS_공구상면적')}개, 대지면적 객체 {sum(1 for _, k, _ in area_nodes if k == '대지면적')}개"},
        {"id": "C-5", "topic": "룸 AIS_분류코드, AIS_ 불린 4종, 마감 8종",
         "observed": f"실 공간 {len(rooms)}개 중 AIS_분류코드 있음 {sum(1 for s in rooms if ais_value(flat_props(s)) is not None)}개; "
                     f"AIS_비상용승강기·피난용승강기·특별피난계단·피난계단 속성 {sum(_n(hits, n) for n in ('AIS_비상용승강기', 'AIS_피난용승강기', 'AIS_특별피난계단', 'AIS_피난계단'))}개; "
                     f"마감 속성(바닥·걸레받이·벽·천장 마감) {sum(_n(hits, n) for n in ('AIS_바닥마감', 'AIS_걸레받이마감', 'AIS_벽마감', 'AIS_천장마감'))}개"},
        {"id": "C-6", "topic": "문 AIS_방화등급, 창 AIS_배연창·외부노출여부, 주차 AIS_주차구획",
         "observed": f"문 {doors}개 중 AIS_방화등급 {_n(hits, 'AIS_방화등급', 'IfcDoor')}개; 창 {wins}개 중 AIS_배연창 {_n(hits, 'AIS_배연창', 'IfcWindow')}개, "
                     f"외부노출여부 {_n(hits, '외부노출여부', 'IfcWindow')}개; AIS_주차구획 {_n(hits, 'AIS_주차구획')}개"},
        {"id": "C-7", "topic": "시설개요 속성 이름(시설종류·대표용도코드·지역지구·도로현황·구조)",
         "observed": "찾은 이름: " + (", ".join(overview) if overview else "없음") + "(필수 아님)"},
        {"id": "C-8", "topic": "부위 부위분류·공종분류 별칭", "observed": f"부위분류·공종분류 속성 이름 {part_cls}종 발견(필수 아님)"},
        {"id": "C-9", "topic": "파일명·폴더 규칙", "observed": "KR-SEU-01(파일명)·KR-SEU-02(폴더)가 판정"},
        {"id": "C-10", "topic": "개요·건축·구조·대지 모델 4종 간 일관성(건물명·좌표 공유)",
         "observed": f"이 파일 IfcBuilding {len(f.by_type('IfcBuilding'))}개; 여러 파일을 서로 비교하지 않음"},
        {"id": "C-11", "topic": "서식입력용 면적 객체의 면적 값과 제외 플래그",
         "observed": f"면적 객체 후보 {len(area_nodes)}개 중 면적 값 속성이 있는 것 {sum(1 for _, _, fl in area_nodes if _has_area_value(fl))}개"},
    ]
    return _res("info", len(notes), 0, note="판정 없음: 설계기준서(안)가 요구하나 IFC 위치가 사본에 없는 후보를 개수로만 알림",
                examples=[]) | {"candidates": notes}


# id -> 판정 함수(세움터 전용 검사). catalog.SEUMTEO_CHECKS 와 같은 키여야 한다(시험이 확인).
SEU_CHECKS = {
    "KR-SEU-01": check_filename_seumteo,
    "KR-SEU-03": check_compression_size,
    "KR-SEU-04": check_room_name,
    "KR-SEU-05": check_room_class_present,
    "KR-SEU-06": check_room_class_format,
    "KR-SEU-07": check_proxy_part_class,
    "KR-SEU-08": check_part_work_class,
    "KR-SEU-09": check_use_area_objects,
    "KR-SEU-10": check_footprint_object,
    "KR-SEU-11": check_site_area_object,
    "KR-SEU-12": check_facility_overview,
    "KR-SEU-13": check_area_overlap,
    "KR-SEU-14": check_bil30,
    "KR-SEU-15": check_candidate_notes,
}
# 폴더 검사 KR-SEU-02 는 파일이 아니라 폴더를 받아 따로 둔다(조달청 KR-CODE-18 과 같은 처리)
PACKAGE_CHECK = ("KR-SEU-02", check_package_seumteo)

# 소속 common 이지만 세움터에서는 대상을 달리 잡아 돌리는 검사(id -> 세움터용 함수)
ADAPTED = {"KR-CODE-10": check_room_overlap}
