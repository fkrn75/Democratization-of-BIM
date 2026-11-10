"""한국 납품 규칙 실행기: IDS(.ids) 검증 + 코드 검사를 한 번에 돌려 JSON 으로 쓴다.

사용:  python -m krrules.run [ifc] [--package 폴더] [--out results/x.json] [--energy] [--deliverable] [--as-name 00_건물_BA.ifc] [--profile procurement|seumteo]
  ifc           : 검사할 IFC 파일. 생략하면 --package 만 검사한다.
  --package DIR : 납품 폴더 구성(KR-CODE-18)도 검사한다(조달청 5.5.3). ifc 와 함께 쓰면 결과에 합친다.
  --energy      : 에너지 검토(환경 시뮬레이션)를 수행하는 사업으로 보고 조건부 규칙(KR-IDS-08/09/10)도 판정한다.
                  없으면 그 규칙은 해당 없음으로 두고 위반 집계에 넣지 않는다.
  --deliverable : 납품 후보로 보고 파일명 규칙(KR-CODE-02/03)도 판정. 없으면 해당 없음.
  --as-name     : 파일명 규칙을 이 이름으로 판정(표본 파일명이 납품 이름이 아닐 때 시험용)
  --profile     : 납품처 프로파일. procurement(기본, 조달청+국토부) 또는 seumteo(세움터 초안, 기본 꺼짐).
                  기본 결과는 프로파일 도입 전과 같다. 모르는 이름은 오류 JSON(UNKNOWN_PROFILE)과 종료 코드 2.
종료 코드: 0 = 결과를 만듦(위반이 있어도 0, 위반은 JSON 에 있다), 2 = 입력을 읽지 못함(오류 JSON 을 씀).
결과에는 절대 경로를 쓰지 않는다(파일 이름만).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element as uel
from ifctester import ids as ids_mod
from ifctester import reporter

from . import catalog, profiles, seumteo, slots
from .codechecks import CODE_CHECKS, MAX_EXAMPLES, check_package_layout, check_schema_name, schema_ok
from .ids_build import THERMAL_5, THERMAL_6

ROOT = Path(__file__).resolve().parent.parent
IDS_PATH = ROOT / "ids" / "kr_delivery_construction.ids"
PROFILE = "조달청 v2.1 실시설계 + 국토부 시행지침(설계자·발주자 편)"

# 열려 있는지 확인하는 구간 크기(헤더·꼬리 판독용)
_HEAD_BYTES = 8192
_TAIL_BYTES = 4096

# 파일 하나만으로 판정하는(IFC 를 열지 않아도 되는) 검사: 스키마를 열 수 없을 때도 돌린다
_NO_MODEL_CHECKS = {"KR-CODE-01", "KR-CODE-02", "KR-CODE-03", "KR-CODE-16"}

# IDS 가 엄격한 표준 위치(Pset_*Common)만 보므로, 값이 다른 Pset 에 있는 경우를 실행기가 다시 판정하는 규칙
# id -> (속성 이름, 대상 클래스 목록 또는 None(IDS 가 정한 대상 그대로))
_TOLERANT = {
    "KR-IDS-08": ("IsExternal", None),
    "KR-IDS-09": ("ThermalTransmittance", THERMAL_6),
    "KR-IDS-10": ("ThermalTransmittance", THERMAL_5),
}


class InputError(Exception):
    """입력 파일을 검사할 수 없을 때(빈 파일, STEP 이 아님, 끝이 잘림, 열기 실패 등)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 입력 읽기

_SCHEMA_RE = re.compile(rb"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'", re.I)


def inspect_input(path: Path):
    """파일을 열기 전에 헤더·꼬리를 훑어 오류를 가려내고 헤더의 스키마 이름을 돌려준다(.ifc 텍스트 STEP 만).

    .ifczip/.ifcxml 은 훑지 않고 None 을 돌려준다(열기 실패는 load_model 이 잡는다).
    """
    if not path.exists():
        raise InputError("NOT_FOUND", "파일이 없음")
    if not path.is_file():
        raise InputError("NOT_A_FILE", "파일이 아님")
    size = path.stat().st_size
    if size == 0:
        raise InputError("EMPTY_FILE", "빈 파일(0바이트)")
    if path.suffix.lower() != ".ifc":
        return None
    with open(path, "rb") as fp:
        head = fp.read(_HEAD_BYTES)
        fp.seek(max(0, size - _TAIL_BYTES))
        tail = fp.read(_TAIL_BYTES)
    if not head.lstrip(b"\xef\xbb\xbf \t\r\n").upper().startswith(b"ISO-10303-21"):
        raise InputError("NOT_STEP", "STEP 파일이 아님(ISO-10303-21 로 시작하지 않음)")
    tail_u = tail.upper()
    if b"END-ISO-10303-21" not in tail_u or b"ENDSEC" not in tail_u:
        raise InputError("TRUNCATED", "끝이 잘린 파일(ENDSEC/END-ISO-10303-21 이 파일 끝에 없음)")
    m = _SCHEMA_RE.search(head)
    if not m:
        raise InputError("NO_FILE_SCHEMA", "헤더에 FILE_SCHEMA 가 없음")
    return m.group(1).decode("ascii", "replace")


_LOG_TS = re.compile(r"\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] ")


def load_model(path: Path):
    """ifcopenshell 로 연다. 실패하면 InputError(OPEN_FAILED). 메시지에 경로가 섞이지 않게 파일 이름으로 바꾼다.

    돌려주는 값: (모델, 파서 오류 목록). 열리긴 했지만 구문 오류가 있으면(깨진 줄 등) 목록에 담아 결과에 적는다."""
    try:
        ifcopenshell.get_log()  # 이전 로그를 비운다
        f = ifcopenshell.open(str(path))
        log = ifcopenshell.get_log()
    except Exception as e:  # ifcopenshell 은 형식 오류에 RuntimeError/IOError 등을 던진다
        msg = str(e).replace(str(path), path.name).replace(str(path.parent), "")
        raise InputError("OPEN_FAILED", f"{type(e).__name__}: {msg}"[:200])
    errors = [_LOG_TS.sub("", ln)[:160] for ln in log.splitlines() if ln.startswith("[error]")]
    return f, errors


# ---------------------------------------------------------------- IDS

def _label(req):
    lab = req.get("label") or ""
    return lab.rsplit(".", 1)[-1] if "{" in lab else lab


def _truthy(v):
    return v is True or (isinstance(v, str) and v.strip().upper() == "TRUE")


def _prop_value(e, name):
    """객체(유형 포함)의 모든 Pset 에서 속성 값을 찾는다(Pset 이름·값 타입 불문). 없거나 빈 값·UNKNOWN 이면 None."""
    for pset in uel.get_psets(e, psets_only=True).values():
        v = pset.get(name)
        if v is not None and v != "" and v != "UNKNOWN":
            return v
    return None


def _reconcile(f, ident, applicable_ids, failed):
    """엄격한 IDS 결과를 '값이 다른 Pset 에 있으면 통과'로 다시 판정한다(오탐·미탐 줄이기).

    failed: {entity id: {"class","name","global_id","reasons"}} 를 고쳐 쓴다.
    돌려주는 값: (applicable_ids 확장 집합, 통과로 바꾼 개수)
    """
    prop, classes = _TOLERANT[ident]
    tolerated = 0
    for eid in list(failed):
        if _prop_value(f.by_id(eid), prop) is not None:
            del failed[eid]
            tolerated += 1
    applicable = set(applicable_ids)
    if classes:  # 열관류율 규칙: 외기 면(IsExternal=TRUE) 판정도 Pset 이름 불문으로 넓힌다
        for cls in classes:
            for e in f.by_type(cls):
                if e.id() in applicable or not _truthy(_prop_value(e, "IsExternal")):
                    continue
                applicable.add(e.id())
                if _prop_value(e, prop) is None:
                    failed[e.id()] = {"class": e.is_a(), "name": getattr(e, "Name", None),
                                      "global_id": getattr(e, "GlobalId", None),
                                      "reasons": [f"{prop} 없음"]}
                else:
                    tolerated += 1
    return applicable, tolerated


def run_ids(f, ids_path: Path = IDS_PATH):
    """IDS 파일을 읽어 검증하고 spec 별 요약을 돌려준다."""
    doc = ids_mod.open(str(ids_path), validate=True)
    # ifctester 의 읽기 구현이 specification 의 identifier 를 버리므로 XML 에서 직접 읽어 순서대로 짝짓는다
    ns = {"i": "http://standards.buildingsmart.org/IDS"}
    idents = [e.get("identifier") for e in ET.parse(str(ids_path)).getroot().findall(".//i:specification", ns)]
    doc.validate(f)
    rep = reporter.Json(doc)
    rep.report()
    data = rep.to_string()
    data = json.loads(data) if isinstance(data, str) else data
    out = {}
    for spec, raw, ident in zip(doc.specifications, data["specifications"], idents):
        applicable_ids = {e["id"] for e in raw.get("applicable_entities") or []}
        applicable = raw.get("total_applicable", 0)
        # 위반 객체를 모아 같은 객체의 여러 사유를 합친다
        failed = {}
        for req in raw.get("requirements", []):
            for ent in req.get("failed_entities", []) or []:
                item = failed.setdefault(ent.get("id"), {"class": ent.get("class"), "name": ent.get("name"),
                                                         "global_id": ent.get("global_id"), "reasons": []})
                why = ent.get("reason") or req.get("description") or ""
                lab = _label(req)
                text = f"{lab}: {why}" if lab else why
                if text not in item["reasons"]:
                    item["reasons"].append(text)
        tolerated = 0
        if ident in _TOLERANT and raw.get("is_ifc_version", True):
            applicable_ids, tolerated = _reconcile(f, ident, applicable_ids, failed)
            applicable = len(applicable_ids)
        n_failed = len(failed)
        note = ""
        if not raw.get("is_ifc_version", True):
            status, note = "na", f"IFC 버전 {f.schema} 은 이 규칙 대상이 아님"
        elif spec.minOccurs and spec.minOccurs >= 1:
            # 존재 요구: 적용 대상(층/실 등)이 최소 1개 있어야 통과
            status = "pass" if applicable >= 1 else "fail"
            note = f"대상 {applicable}개"
        elif applicable == 0:
            status, note = "na", "적용 대상 객체 없음"
        else:
            status = "pass" if n_failed == 0 else "fail"
        if tolerated:
            note = (note + "; " if note else "") + f"표준 Pset 이름이 아니거나 값 타입이 달라도 값이 있는 {tolerated}개는 통과로 재판정"
        examples = [{"class": v["class"], "name": v["name"], "global_id": v["global_id"],
                     "reason": "; ".join(v["reasons"])[:160]} for v in failed.values()]
        examples.sort(key=lambda e: (e["class"] or "", e["name"] or "", e["global_id"] or ""))
        out[ident] = {
            "status": status, "applicable": applicable, "failed": n_failed if status != "fail" or applicable else 1,
            "examples": examples[:MAX_EXAMPLES], "note": note, "title": spec.name,
        }
        if ident in _TOLERANT:
            out[ident]["tolerated"] = tolerated
        if status == "fail" and spec.minOccurs and spec.minOccurs >= 1 and applicable == 0:
            out[ident]["examples"] = [{"reason": "필수 대상이 하나도 없음"}]
    return out


# ---------------------------------------------------------------- 결과 조립

def _check_title(cid, fn):
    if cid == "KR-CODE-18":
        return "납품 폴더 구성(조달청 5.5.3)"
    return (fn.__doc__ or cid).strip().splitlines()[0].split(": ", 1)[-1]


def _entry(cid, mode, sev, r, title):
    reqs = [q["id"] for q in catalog.REQUIREMENTS if cid in q["checks"]]
    return {"id": cid, "title": title, "mode": mode, "severity": sev, "requirements": reqs, **r}


def _summarize(checks, energy):
    """위반 집계. 조건부 규칙은 일반 집계(pass/fail/...)에서 빼고 'conditional' 로 따로 센다."""
    summary = {}
    cond = {"enabled": bool(energy)}
    for c in checks:
        bucket = cond if c["severity"] == "조건부" else summary
        bucket[c["status"]] = bucket.get(c["status"], 0) + 1
    summary["conditional"] = cond
    summary["total"] = len(checks)
    return summary


def _guard(fn, *a, **kw):
    """검사 하나가 예외로 죽어도 전체를 멈추지 않고 error 로 기록한다."""
    try:
        return fn(*a, **kw)
    except Exception as e:
        return {"status": "error", "applicable": 0, "failed": 0, "examples": [],
                "note": f"검사 중 예외 {type(e).__name__}: {str(e)[:120]}"}


def _energy_off():
    return {"status": "na", "applicable": 0, "failed": 0, "examples": [], "conditional_off": True,
            "note": "조건부 규칙: 에너지 검토(환경 시뮬레이션) 수행 시에만 적용. --energy 로 켬"}


def _check_profile(profile):
    if not profiles.known(profile):
        raise InputError("UNKNOWN_PROFILE", f"알 수 없는 프로파일 '{str(profile)[:40]}' (가능: {', '.join(profiles.PROFILE_IDS)})")


def run_file(path: Path, deliverable=False, as_name=None, ids_path: Path = IDS_PATH, energy=False, package=None,
             profile=profiles.DEFAULT_PROFILE):
    """IFC 하나를 검사한다. 읽을 수 없으면 InputError. IFC2X3 미만 스키마는 KR-CODE-01 위반 결과로 돌려준다.
    ids_path 에 사용자 IDS 를 주면 표준 슬롯 + 사용자 속성 OR 판정(slots.OVERRIDES)을 쓰지 않고 그 IDS 의 결과를 그대로 쓴다
    (기본 IDS 일 때만 OR 판정으로 대체).
    profile: procurement(기본, 결과가 프로파일 도입 전과 바이트 단위로 같음) 또는 seumteo(초안). 모르는 이름은 InputError(UNKNOWN_PROFILE)."""
    _check_profile(profile)
    if profile == "seumteo":
        return _run_file_seumteo(Path(path), deliverable, as_name, energy, package)
    path = Path(path)
    filename = as_name or path.name
    header_schema = inspect_input(path)
    legacy = header_schema is not None and not schema_ok(header_schema)
    f, parse_errors = (None, []) if legacy else load_model(path)
    use_override = Path(ids_path).resolve() == IDS_PATH.resolve()
    ids_res = {} if legacy else _guard_ids(f, ids_path)
    checks = []
    for cid, (mode, sev) in catalog.CHECKS.items():
        if cid == "KR-CODE-18":
            continue  # 폴더 단위 검사는 --package 로
        if mode == "ids":
            if sev == "조건부" and not energy:
                checks.append(_entry(cid, mode, sev, _energy_off(), _ids_title(cid)))
                continue
            if legacy:
                checks.append(_entry(cid, mode, sev, _legacy_na(header_schema), _ids_title(cid)))
                continue
            r = ids_res.get(cid)
            if cid in slots.OVERRIDES and use_override and not (r and r.get("status") == "error"):
                # 표준 IFC 슬롯 + 한국어 사용자 속성 OR 판정으로 IDS(엄격 슬롯) 결과를 대체한다(slots.py).
                # IDS 엔진이 죽어 error 가 된 규칙은 대체하지 않고 error 를 그대로 전파한다
                checks.append(_entry(cid, mode, sev, _guard(slots.OVERRIDES[cid], f), _ids_title(cid)))
                continue
            if r is None:
                continue
            title = r.pop("title")
        else:
            fn = CODE_CHECKS.get(cid)
            if fn is None:
                continue
            title = _check_title(cid, fn)
            if legacy and cid not in _NO_MODEL_CHECKS:
                r = _legacy_na(header_schema)
            elif legacy and cid == "KR-CODE-01":
                r = check_schema_name(header_schema)
            elif legacy:
                r = _guard(fn, None, path=str(path), filename=filename, deliverable=deliverable)
            else:
                r = _guard(fn, f, path=str(path), filename=filename, deliverable=deliverable)
        checks.append(_entry(cid, mode, sev, r, title))
    if package is not None:
        checks.append(_package_entry(package))
    return {
        "file": path.name, "sha256": _sha(path), "schema": header_schema if legacy else f.schema,
        "profile": PROFILE, "energy_mode": bool(energy),
        "deliverable_mode": bool(deliverable), "name_checked_as": filename,
        "unreadable_schema": bool(legacy),
        **({"parse_errors": {"count": len(parse_errors), "samples": parse_errors[:3]}} if parse_errors else {}),
        "summary": _summarize(checks, energy), "checks": checks,
    }


# ---------------------------------------------------------------- 세움터 프로파일(초안)

# 파일 하나만으로 판정하는 세움터 검사(IFC 를 열지 않아도 됨)
_NO_MODEL_SEU = {"KR-SEU-01", "KR-SEU-03"}


def _scope_entry(entry, scope, **extra):
    """세움터 결과의 규칙 항목에 소속(common/procurement/seumteo)과 설명 키를 덧붙인다."""
    entry["scope"] = scope
    entry.update(extra)
    return entry


def _skipped_entry(cid, mode, sev):
    note, seu = profiles.seumteo_skip_note(cid)
    r = {"status": "na", "applicable": 0, "failed": 0, "examples": [], "note": note, "excluded_by_profile": True}
    if seu:
        r["replaced_by"] = seu
    title = _ids_title(cid) if mode == "ids" else _check_title(cid, CODE_CHECKS.get(cid))
    return _scope_entry(_entry(cid, mode, sev, r, title), catalog.CHECK_SCOPE[cid])


def _seu_entry(cid, r, title):
    mode, sev, reqs, olds = catalog.SEUMTEO_CHECKS[cid]
    e = {"id": cid, "title": title, "mode": mode, "severity": sev, "requirements": list(reqs), **r}
    return _scope_entry(e, "seumteo", **({"replaces": list(olds)} if olds else {}))


def _seu_title(fn):
    return (fn.__doc__ or "").strip().splitlines()[0].split(": ", 1)[-1]


def _run_file_seumteo(path: Path, deliverable, as_name, energy, package):
    """세움터 프로파일(초안)로 IFC 하나를 검사한다. 공통 규칙은 그대로(또는 대상만 달리) 돌리고, 조달청 전용 규칙은 해당 없음으로
    표시하며, KR-SEU-* 를 더한다. 입력 점검·열기는 기본 프로파일과 같다."""
    filename = as_name or path.name
    header_schema = inspect_input(path)
    legacy = header_schema is not None and not schema_ok(header_schema)
    f, parse_errors = (None, []) if legacy else load_model(path)
    ids_res = {} if legacy else _guard_ids(f, IDS_PATH)
    checks = []
    for cid, (mode, sev) in catalog.CHECKS.items():
        scope = catalog.CHECK_SCOPE[cid]
        if cid == "KR-CODE-18":
            if package is not None:  # 폴더 검사는 --package 일 때만: 조달청 전용이라 해당 없음으로만 남긴다
                checks.append(_skipped_entry(cid, mode, sev))
            continue
        if scope == "procurement":
            checks.append(_skipped_entry(cid, mode, sev))
            continue
        if mode == "ids":  # 공통 IDS 규칙(KR-IDS-01, 11)
            if legacy:
                checks.append(_scope_entry(_entry(cid, mode, sev, _legacy_na(header_schema), _ids_title(cid)), scope))
                continue
            r = ids_res.get(cid)
            if r is None:
                continue
            title = r.pop("title")
        else:
            orig = CODE_CHECKS[cid]
            fn = seumteo.ADAPTED.get(cid, orig)
            title = _check_title(cid, orig)
            if legacy and cid not in _NO_MODEL_CHECKS:
                r = _legacy_na(header_schema)
            elif legacy and cid == "KR-CODE-01":
                r = check_schema_name(header_schema)
            elif legacy:
                r = _guard(fn, None, path=str(path), filename=filename, deliverable=deliverable)
            else:
                r = _guard(fn, f, path=str(path), filename=filename, deliverable=deliverable)
        extra = {"profile_adapted": True} if cid in seumteo.ADAPTED else {}
        checks.append(_scope_entry(_entry(cid, mode, sev, r, title), scope, **extra))
    for cid, fn in seumteo.SEU_CHECKS.items():
        if legacy and cid not in _NO_MODEL_SEU:
            r = _legacy_na(header_schema)
        else:
            r = _guard(fn, None if legacy else f, path=str(path), filename=filename, deliverable=deliverable)
        checks.append(_seu_entry(cid, r, _seu_title(fn)))
    if package is not None:
        checks.append(_seu_package_entry(package))
    return {
        **_seumteo_head(),
        "file": path.name, "sha256": _sha(path), "schema": header_schema if legacy else f.schema,
        "profile": profiles.SEUMTEO_LABEL, "energy_mode": False,
        **({"energy_note": "세움터 프로파일은 에너지 조건부 규칙(KR-IDS-08/09/10)을 판정하지 않아 --energy 를 무시함"} if energy else {}),
        "deliverable_mode": bool(deliverable), "name_checked_as": filename,
        "unreadable_schema": bool(legacy),
        **({"parse_errors": {"count": len(parse_errors), "samples": parse_errors[:3]}} if parse_errors else {}),
        "summary": _summarize(checks, False), "checks": checks,
    }


def _seumteo_head():
    """세움터 결과 맨 위의 표식: 프로파일 id, 초안 경고, 소속별 규칙 수."""
    return {"profile_id": "seumteo", "draft_warning": profiles.DRAFT_WARNING, "scope_counts": profiles.scope_counts()}


def _seu_package_entry(root):
    cid, fn = seumteo.PACKAGE_CHECK
    return _seu_entry(cid, _guard(fn, Path(root)), _seu_title(fn))


def _guard_ids(f, ids_path):
    try:
        return run_ids(f, ids_path)
    except Exception as e:  # IDS 엔진 자체가 죽으면 IDS 규칙 전체를 error 로 표시
        msg = f"IDS 실행 중 예외 {type(e).__name__}: {str(e)[:120]}"
        return {cid: {"status": "error", "applicable": 0, "failed": 0, "examples": [], "note": msg,
                      "title": _ids_title(cid)}
                for cid, (m, _) in catalog.CHECKS.items() if m == "ids"}


_IDS_TITLES = None


def _ids_title(cid):
    """IDS 파일에서 규칙 이름을 읽는다(실행하지 못해도 제목은 채우기 위해)."""
    global _IDS_TITLES
    if _IDS_TITLES is None:
        ns = {"i": "http://standards.buildingsmart.org/IDS"}
        root = ET.parse(str(IDS_PATH)).getroot()
        _IDS_TITLES = {e.get("identifier"): e.get("name") for e in root.findall(".//i:specification", ns)}
    return _IDS_TITLES.get(cid, cid)


def _legacy_na(schema):
    return {"status": "na", "applicable": 0, "failed": 0, "examples": [],
            "note": f"스키마 {schema} 는 IFC 2x3 미만이라 이 도구가 열 수 없어 판정하지 않음(KR-CODE-01 위반)"}


def _package_entry(root):
    r = _guard(check_package_layout, Path(root))
    mode, sev = catalog.CHECKS["KR-CODE-18"]
    return _entry("KR-CODE-18", mode, sev, r, _check_title("KR-CODE-18", None))


def run_package(root, profile=profiles.DEFAULT_PROFILE):
    """납품 폴더 구성만 검사한다(IFC 는 열지 않음). profile 이 seumteo 면 세움터 폴더 규칙(KR-SEU-02)을 쓴다."""
    _check_profile(profile)
    root = Path(root)
    if profile == "seumteo":
        if not root.is_dir():
            raise InputError("NOT_A_DIRECTORY", "납품 폴더가 아님")
        checks = [_seu_package_entry(root)]
        return {**_seumteo_head(), "package": root.name, "profile": profiles.SEUMTEO_LABEL,
                "summary": _summarize(checks, False), "checks": checks}
    if not root.is_dir():
        raise InputError("NOT_A_DIRECTORY", "납품 폴더가 아님")
    checks = [_package_entry(root)]
    return {"package": root.name, "profile": PROFILE, "summary": _summarize(checks, False), "checks": checks}


def error_result(path: Path, err: InputError, profile=None):
    """오류 JSON. profile 은 요청받은 프로파일 이름(UNKNOWN_PROFILE 일 때 그대로 적음). 생략하면 기존과 같은 값."""
    return {"file": Path(path).name, "profile": profile if profile is not None else PROFILE, "status": "error",
            "error": {"code": err.code, "message": err.message}}


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ifc", nargs="?")
    ap.add_argument("--package")
    ap.add_argument("--out")
    ap.add_argument("--energy", action="store_true")
    ap.add_argument("--deliverable", action="store_true")
    ap.add_argument("--as-name")
    ap.add_argument("--profile", default=profiles.DEFAULT_PROFILE)
    a = ap.parse_args(argv)
    if not a.ifc and not a.package:
        ap.error("ifc 파일이나 --package 폴더 중 하나는 필요함")
    try:
        if a.ifc:
            res = run_file(Path(a.ifc), a.deliverable, a.as_name, energy=a.energy, package=a.package, profile=a.profile)
        else:
            res = run_package(a.package, profile=a.profile)
    except InputError as e:
        res = error_result(Path(a.ifc or a.package), e, profile=a.profile if e.code == "UNKNOWN_PROFILE" else None)
        text = json.dumps(res, ensure_ascii=False, indent=2)
        if a.out:
            Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"{res['file']} 오류 {e.code}: {e.message}", file=sys.stderr)
        return 2
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    s = res["summary"]
    cond = s["conditional"]
    parts = [f"{k}={v}" for k, v in s.items() if k not in ("conditional", "total")]
    cparts = " ".join(f"{k}={v}" for k, v in cond.items() if k != "enabled")
    label = f"{res['file']} [{res['schema']}]" if "file" in res else f"폴더 {res['package']}"
    print(f"{label} " + " ".join(parts) + f" total={s['total']} | 조건부({'켬' if cond['enabled'] else '꺼짐'}) {cparts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
