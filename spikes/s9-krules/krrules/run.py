"""한국 납품 규칙 실행기: IDS(.ids) 검증 + 코드 검사를 한 번에 돌려 JSON 으로 쓴다.

사용:  python -m krrules.run <ifc> [--out results/x.json] [--deliverable] [--as-name 00_건물_BA.ifc]
  --deliverable : 납품 후보로 보고 파일명 규칙(KR-CODE-02/03)도 판정. 없으면 해당 없음.
  --as-name     : 파일명 규칙을 이 이름으로 판정(표본 파일명이 납품 이름이 아닐 때 시험용)
결과에는 절대 경로를 쓰지 않는다(파일 이름만).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import ifcopenshell
from ifctester import ids as ids_mod
from ifctester import reporter

from . import catalog
from .codechecks import CODE_CHECKS, MAX_EXAMPLES

ROOT = Path(__file__).resolve().parent.parent
IDS_PATH = ROOT / "ids" / "kr_delivery_construction.ids"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
        applicable = raw.get("total_applicable", 0)
        failed = raw.get("total_applicable_fail", 0)
        if not raw.get("is_ifc_version", True):
            status, note = "na", f"IFC 버전 {f.schema} 은 이 규칙 대상이 아님"
        elif spec.minOccurs and spec.minOccurs >= 1:
            # 존재 요구: 적용 대상(층/실 등)이 최소 1개 있어야 통과
            status = "pass" if applicable >= 1 else "fail"
            note = f"대상 {applicable}개"
        elif applicable == 0:
            status, note = "na", "적용 대상 객체 없음"
        else:
            status, note = ("pass" if failed == 0 else "fail"), ""
        examples = []
        for req in raw.get("requirements", []):
            for ent in req.get("failed_entities", []) or []:
                reason = req.get("description") or ""
                examples.append({"class": ent.get("class"), "name": ent.get("name"),
                                 "global_id": ent.get("global_id"), "reason": (ent.get("reason") or reason)[:160]})
        out[ident] = {
            "status": status, "applicable": applicable, "failed": failed if status != "fail" or applicable else 1,
            "examples": examples[:MAX_EXAMPLES], "note": note, "title": spec.name,
        }
        if status == "fail" and spec.minOccurs and spec.minOccurs >= 1 and applicable == 0:
            out[ident]["examples"] = [{"reason": "필수 대상이 하나도 없음"}]
    return out


def run_file(path: Path, deliverable=False, as_name=None, ids_path: Path = IDS_PATH):
    path = Path(path)
    f = ifcopenshell.open(str(path))
    filename = as_name or path.name
    checks = []
    ids_res = run_ids(f, ids_path)
    for cid, (mode, sev) in catalog.CHECKS.items():
        if mode == "ids":
            r = ids_res.get(cid)
            if r is None:
                continue
            title = r.pop("title")
        else:
            fn = CODE_CHECKS.get(cid)
            if fn is None:
                continue  # 파일 단위가 아닌 검사(예: 폴더 구성)
            r = fn(f, path=str(path), filename=filename, deliverable=deliverable)
            title = (fn.__doc__ or cid).strip().splitlines()[0].split(": ", 1)[-1]
        reqs = [q["id"] for q in catalog.REQUIREMENTS if cid in q["checks"]]
        checks.append({"id": cid, "title": title, "mode": mode, "severity": sev, "requirements": reqs, **r})
    summary = {}
    for c in checks:
        summary[c["status"]] = summary.get(c["status"], 0) + 1
    summary["total"] = len(checks)
    return {
        "file": path.name, "sha256": _sha(path), "schema": f.schema,
        "profile": "조달청 v2.1 실시설계 + 국토부 시행지침(설계자·발주자 편)",
        "deliverable_mode": bool(deliverable), "name_checked_as": filename,
        "summary": summary, "checks": checks,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ifc")
    ap.add_argument("--out")
    ap.add_argument("--deliverable", action="store_true")
    ap.add_argument("--as-name")
    a = ap.parse_args(argv)
    res = run_file(Path(a.ifc), a.deliverable, a.as_name)
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    s = res["summary"]
    print(f"{res['file']} [{res['schema']}] " + " ".join(f"{k}={v}" for k, v in s.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
