# SPDX-License-Identifier: MPL-2.0
"""S2 측정 실행기: T1~T5 와 거절 동작을 돌려 results/*.json 을 만든다.

사용: DOBIM_IFCDIFF_PYTHON=<ifcdiff 가 있는 python> [DOBIM_REVIT_DIR=<비공개 표본 폴더>] python run_s2.py [결과 폴더]
결과에는 경로·해시·사용자 정보를 넣지 않는다. Revit 표본은 별칭(revit_ra01_*)으로만 적고 "로컬 측정, 파일 비공개"로 표시한다.
"""
from __future__ import annotations

import json
import platform
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scenarios as S  # noqa: E402
import scenarios_review as V  # noqa: E402

import ifcopenshell  # noqa: E402


def timed(fn, *a):
    t0 = time.perf_counter()
    r = fn(*a)
    r["elapsed_s"] = round(time.perf_counter() - t0, 1)
    return r


def env_info() -> dict:
    import importlib.metadata as md
    import subprocess
    import os
    py = os.environ.get("DOBIM_IFCDIFF_PYTHON") or sys.executable
    v = subprocess.run([py, "-c", "import importlib.metadata as m;print(m.version('ifcdiff'), m.version('ifcopenshell'))"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.split()
    return {"python": platform.python_version(), "platform": platform.system() + " " + platform.release(),
            "ifcopenshell_core": ifcopenshell.version, "ifcdiff": v[0] if v else None, "ifcdiff_ifcopenshell": v[1] if len(v) > 1 else None,
            "pydantic": md.version("pydantic")}


def main(argv=None) -> int:
    out = Path((argv or sys.argv[1:] or [HERE / "results"])[0])
    out.mkdir(parents=True, exist_ok=True)
    meta = env_info()
    work = Path(tempfile.mkdtemp(prefix="dobim-s2-"))

    def save(name, data):
        data = {"meta": meta, **data}
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    ba = S.ba_path()
    if ba is not None:
        save("s2_t1_t2_ba.json", {"sample": "Building-Architecture.ifc", "public": True,
                                  "t1": timed(S.t1_passthrough, ba, work / "t1_ba"), "t2": timed(S.t2_property_edit, ba, work / "t2_ba")})
    revit = {}
    for variant in ("IFC4RV", "IFC4DTV"):
        p = S.revit_path(variant)
        if p is not None:
            alias = f"revit_ra01_{variant.lower()}"
            revit[alias] = {"t1": timed(S.t1_passthrough, p, work / f"t1_{alias}"), "t2": timed(S.t2_property_edit, p, work / f"t2_{alias}")}
    if revit:
        save("s2_t3_revit_local.json", {"note": "로컬 Revit 2025 내보내기, 파일 비공개: RA-01 을 열어 내보낸 표본. 이 저장소에 파일·일부·파생 IFC 가 없다", "samples": revit})
    save("s2_t4_authored_roundtrip.json", timed(S.t4_authored_roundtrip, work / "t4"))
    save("s2_t5_tamper.json", {"t5": timed(S.t5_tamper, work / "t5"), "t5b": timed(S.t5b_dependents, work / "t5b")})
    save("s2_review_fixes.json", {"note": "독립 검수 결함(R1~R9)의 재현·수정 확인. 시험은 tests/test_s2_review.py",
                                  "r1_style_preserved": timed(V.r1_style_preserved, work / "r1"),
                                  "r2_delete_undo_attachments": timed(V.r2_delete_undo_attachments, work / "r2"),
                                  "r3_skeleton": timed(V.r3_skeleton, work / "r3"),
                                  "r4_opening_identity": timed(V.r4_opening_identity, work / "r4"),
                                  "r5_numeric_range": timed(V.r5_numeric_range, work / "r5"),
                                  "r6_misses": timed(V.r6_misses, work / "r6"),
                                  "r8_more_tampers": timed(V.r8_more_tampers, work / "r8"),
                                  "r9_foreign_void_delete_warning": timed(V.r9_foreign_void_delete_warning, work / "r9")})
    x23 = S.revit_path("IFC2x3")
    save("s2_rejections.json", {"checks": S.reject_checks(work / "rej", x23), "ifc2x3_sample_alias": "revit_ra01_ifc2x3" if x23 else None})
    print("결과를 썼다:", ", ".join(sorted(p.name for p in out.glob("s2_*.json"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
