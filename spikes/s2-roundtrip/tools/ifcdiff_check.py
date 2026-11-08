# SPDX-License-Identifier: MPL-2.0
"""두 IFC 파일의 의미 차이를 재서 요약 JSON 으로 낸다 (S2 합격 판정 도구).

1) 공식 ifcdiff (IfcOpenShell 배포, PyPI `ifcdiff`, LGPL-3.0)를 **별도 프로세스**로 호출한다. 소스를 이 저장소에 복사하지 않는다.
   속성·형상·형(type)·속성셋·공간 소속·집합 소속·분류를 모두 켠다. GlobalId 로 요소를 짝지어 추가·삭제·변경을 센다.
2) 엄격 보강(strict): ifcdiff 가 보지 않는 부분(관계 객체, 재료, 단위, 프로젝트)까지 코어의 정규화 덤프
   (IfcModel.semantic_dump: STEP 번호·소유 이력을 뺀 GlobalId 단위 의미 덤프)로 비교한다. 이것은 ifcdiff 가 아니라 이 저장소의 보조 검사다.

호출 파이썬은 환경변수 DOBIM_IFCDIFF_PYTHON (ifcdiff 가 설치된 venv 의 python) 으로 지정한다. 없으면 현재 인터프리터를 쓴다.
출력에는 파일 경로·GlobalId 를 넣지 않는다(--ids 를 주면 GlobalId 만 넣는다).

사용법: python ifcdiff_check.py OLD.ifc NEW.ifc [--json out.json] [--ids]
종료 코드: 차이 0 이면 0, 차이가 있으면 1, 도구 실패 2.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REL = "attributes geometry type property container aggregate classification"
_ID_KEY = re.compile(r"^root\['[^']*'\]\['id'\]$")


def _id_only(entry) -> bool:
    """ifcdiff 는 속성셋의 STEP 번호(id)가 달라져도 properties_changed 로 센다(지웠다 되돌려 속성셋이 새로 만들어진 경우 등).
    변경 내용이 전부 그런 번호 차이뿐인가. 번호는 의미가 아니므로 엄격 보강(strict)이 내용 차이를 따로 잡는다."""
    if not isinstance(entry, dict) or set(entry) != {"properties_changed"}:
        return False
    pc = entry["properties_changed"]
    return isinstance(pc, dict) and set(pc) == {"values_changed"} and all(_ID_KEY.match(k) for k in pc["values_changed"])


def run_ifcdiff(old: str, new: str, python: str | None = None, ids: bool = False) -> dict:
    py = python or os.environ.get("DOBIM_IFCDIFF_PYTHON") or sys.executable
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "diff.json"
        t0 = time.perf_counter()
        p = subprocess.run([py, "-m", "ifcdiff", "-o", str(out), "-r", REL, str(old), str(new)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=900)
        dt = time.perf_counter() - t0
        if p.returncode != 0 or not out.exists():
            raise RuntimeError(f"ifcdiff 실패(종료 {p.returncode}): {(p.stderr or p.stdout)[-300:]}")
        d = json.loads(out.read_text(encoding="utf-8"))
    kinds = collections.Counter()
    for v in d.get("changed", {}).values():
        kinds.update(v.keys() if isinstance(v, dict) else ["(기타)"])
    ver = subprocess.run([py, "-c", "import importlib.metadata as m;print(m.version('ifcdiff'), m.version('ifcopenshell'))"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.split()
    res = {"tool": "ifcdiff", "ifcdiff_version": ver[0] if ver else None, "ifcopenshell_version": ver[1] if len(ver) > 1 else None,
           "relationships": REL.split(), "added": len(d.get("added", [])), "deleted": len(d.get("deleted", [])),
           "changed": len(d.get("changed", {})), "changed_kinds": dict(sorted(kinds.items())), "elapsed_s": round(dt, 2),
           "id_only_changed": sum(1 for v in d.get("changed", {}).values() if _id_only(v))}
    res["total"] = res["added"] + res["deleted"] + res["changed"]
    if ids:
        res["ids"] = {"added": sorted(d.get("added", [])), "deleted": sorted(d.get("deleted", [])), "changed": sorted(d.get("changed", {}))}
    return res


def strict_diff(old: str, new: str, ids: bool = False) -> dict:
    """GlobalId 단위 정규화 덤프 비교. 요소(IfcProduct)는 GlobalId 로 짝짓고, 관계·프로젝트·재료는 정렬한 다중집합으로 비교한다."""
    here = Path(__file__).resolve().parents[2] / "s1-core" / "src"
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import ifcopenshell
    from dobim_core.ifc_model import IfcModel

    def dump(path):
        m = IfcModel.__new__(IfcModel)   # 파일을 열어 덤프만 쓴다(빈 프로젝트를 만들지 않는다)
        m.f = ifcopenshell.open(str(path))
        prods, rels, others = m.semantic_dump()
        return {p["GlobalId"]: p for p in prods}, collections.Counter(rels), collections.Counter(others)

    po, ro, oo = dump(old)
    pn, rn, on = dump(new)
    changed = sorted(g for g in po.keys() & pn.keys() if po[g] != pn[g])
    res = {"tool": "strict(semantic_dump)", "products_added": len(pn.keys() - po.keys()), "products_deleted": len(po.keys() - pn.keys()),
           "products_changed": len(changed), "relationships_added": sum((rn - ro).values()), "relationships_removed": sum((ro - rn).values()),
           "other_added": sum((on - oo).values()), "other_removed": sum((oo - on).values()), "products": len(po)}
    res["total"] = sum(v for k, v in res.items() if k not in ("tool", "products"))
    if ids:
        res["ids"] = {"added": sorted(pn.keys() - po.keys()), "deleted": sorted(po.keys() - pn.keys()), "changed": changed}
    return res


def id_only_summary(old: str, new: str) -> dict:
    """ifcdiff 가 센 변경 요소 수와, 그중 속성셋 번호(id)만 다른 것의 수."""
    r = run_ifcdiff(old, new)
    return {"changed": r["changed"], "id_only": r["id_only_changed"], "content": r["changed"] - r["id_only_changed"]}


def check(old, new, *, python=None, ids=False, strict=True) -> dict:
    out = {"ifcdiff": run_ifcdiff(old, new, python, ids)}
    if strict:
        out["strict"] = strict_diff(old, new, ids)
    out["differences"] = sum(v["total"] for v in out.values() if isinstance(v, dict))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--json")
    ap.add_argument("--ids", action="store_true", help="결과에 GlobalId 목록을 넣는다(비공개 표본에는 쓰지 말 것)")
    ap.add_argument("--no-strict", action="store_true")
    a = ap.parse_args(argv)
    try:
        res = check(a.old, a.new, ids=a.ids, strict=not a.no_strict)
    except Exception as ex:
        print(f"도구 실패: {ex}", file=sys.stderr)
        return 2
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
    print(text)
    return 0 if res["differences"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
