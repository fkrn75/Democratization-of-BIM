# SPDX-License-Identifier: MPL-2.0
"""CGAL 계열 DLL 을 동결본에서 빼도 코어가 동작하는가 (제외 결정의 근거 시험).

*제외 전 전체 빌드*(DOBIM_KEEP_NATIVE=1 로 만든 build/dist_full/dobim)를 기준으로 삼는다: 기본 빌드(build/dist)는 이미 S1 의 DLL 을 뺐기 때문이다.
단계별로 DLL 을 지우고 기준 프로젝트의 의미 해시 3종이 그대로인지 본다.
  S1 CGAL 커널 + svgfill + manifold 제거 (GMP/MPFR/libxml2 정적 링크·GPL 여부 미확인 -> spec 의 EXCLUDE_NATIVE 가 된 결정)
  S2 지오메트리 계열 전부 제거 (ifcopenshell_geometry_*.dll, TK*.dll 제외 — OCCT 는 wrapper 가 직접 링크할 수 있다)
  S3 S2 + OCCT(TK*.dll) 제거
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import S7, RESULTS, write_json  # noqa: E402
from library_swap import fixture_run  # noqa: E402


def size(d: Path) -> int:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())


FULL_DIR = Path(os.environ.get("DOBIM_FULL_DIR", S7 / "build" / "dist_full" / "dobim"))


def main() -> int:
    FROZEN_DIR = FULL_DIR   # noqa: N806 - 아래 코드가 기준 폴더 이름으로 쓴다
    if not (FROZEN_DIR / "_internal" / "ifcopenshell" / "ifcopenshell_geometry_svgfill.dll").exists():
        print("제외 전 전체 빌드가 없다: DOBIM_KEEP_NATIVE=1 python -m PyInstaller --distpath build/dist_full --workpath build/work_full freeze/dobim.spec")
        return 2
    work = Path(tempfile.mkdtemp(prefix="s7-slim-"))
    out = {"base": "제외 전 전체 빌드(dist_full)", "base_bytes": size(FROZEN_DIR)}
    try:
        base = fixture_run(FROZEN_DIR / "dobim.exe", work / "w0")
        out["baseline_ok"] = base["ok"]
        stages = {
            "S1_cgal_svgfill_manifold": ["ifcopenshell_geometry_kernel_cgal*.dll", "ifcopenshell_geometry_svgfill.dll", "manifold.dll",
                                         "ifcopenshell_geometry_kernel_manifold.dll"],
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
            res[tag]["removed_names"] = sorted(gone)
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
