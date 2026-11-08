# SPDX-License-Identifier: MPL-2.0
"""라이선스가 불명확한 CGAL 계열 DLL 을 동결본에서 빼도 코어가 동작하는가 (완화책 시험).

단계별로 DLL 을 지우고 기준 프로젝트의 의미 해시 3종이 그대로인지 본다.
  S1 CGAL 커널 + svgfill + manifold 제거 (라이선스 불명확/별도 확인 대상)
  S2 지오메트리 계열 전부 제거 (ifcopenshell_geometry_*.dll, TK*.dll 제외 — OCCT 는 wrapper 가 직접 링크할 수 있다)
  S3 S2 + OCCT(TK*.dll) 제거
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FROZEN_DIR, RESULTS, write_json  # noqa: E402
from library_swap import fixture_run  # noqa: E402


def size(d: Path) -> int:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="s7-slim-"))
    out = {"base_bytes": size(FROZEN_DIR)}
    try:
        base = fixture_run(FROZEN_DIR / "dobim.exe", work / "w0")
        out["baseline_ok"] = base["ok"]
        stages = {
            "S1_cgal_svgfill_manifold": ["ifcopenshell_geometry_kernel_cgal*.dll", "ifcopenshell_geometry_svgfill.dll", "manifold.dll"],
            "S2_all_geometry_plugins": ["ifcopenshell_geometry_*.dll", "ifcopenshell.geometry*.dll", "manifold.dll"],
            "S3_geometry_plus_occt": ["ifcopenshell_geometry_*.dll", "ifcopenshell.geometry*.dll", "manifold.dll", "TK*.dll"],
        }
        res = {}
        for tag, pats in stages.items():
            d = work / tag
            shutil.copytree(FROZEN_DIR, d)
            gone = []
            for pat in pats:
                for f in (d / "_internal" / "ifcopenshell").glob(pat):
                    gone.append(f.name)
                    f.unlink()
            r = fixture_run(d / "dobim.exe", work / ("w_" + tag))
            res[tag] = {"removed_files": len(gone), "bytes_after": size(d), "run_ok": r["ok"],
                        "hash_equal_to_baseline": r.get("hash") == base["hash"] if r["ok"] else None,
                        "validate_ok": r.get("validate_ok"),
                        "failure": None if r["ok"] else {"stage": r.get("stage"), "stderr_tail": r.get("stderr_tail", "")[-300:]}}
            shutil.rmtree(d, ignore_errors=True)
        out["stages"] = res
    finally:
        shutil.rmtree(work, ignore_errors=True)
    write_json(RESULTS / "s7_slim_check.json", out)
    print(json.dumps({k: (v if k != "stages" else {t: {kk: vv for kk, vv in s.items() if kk != "failure"} for t, s in v.items()})
                      for k, v in out.items()}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
