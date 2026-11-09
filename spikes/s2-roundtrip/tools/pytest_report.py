# SPDX-License-Identifier: MPL-2.0
"""pytest 를 돌려 결과 JSON(results/s2_pytest.json)을 pytest 의 출력에서 **스크립트로** 만든다(손으로 적지 않는다).

사용: python pytest_report.py [--out 결과.json] [--markers "not slow"] [pytest 인자...]
- 기본은 spikes/s1-core 의 전체 시험(느린 시험 포함). --markers 로 거를 수 있다.
- 결과 JSON 에는 경로·사용자 정보를 넣지 않는다. 환경변수는 설정 여부만 적는다.
- 종료 코드: pytest 의 종료 코드.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parents[1] / "s1-core"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(HERE.parent / "results" / "s2_pytest.json"))
    ap.add_argument("--markers", default="", help='예: "not slow"')
    ap.add_argument("extra", nargs="*")
    a = ap.parse_args(argv)
    td = Path(tempfile.mkdtemp(prefix="dobim-pytest-"))
    xml = td / "junit.xml"
    cmd = [sys.executable, "-m", "pytest", "-q", "-rs", "--durations=5", f"--junitxml={xml}", "-p", "no:cacheprovider", *a.extra]
    if a.markers:
        cmd += ["-m", a.markers]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=CORE, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    wall = time.perf_counter() - t0
    tail = (p.stdout or "")[-4000:]
    root = ET.parse(xml).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    cases = [c for s in suites for c in s.iter("testcase")]

    def status(c):
        if c.find("failure") is not None:
            return "failed"
        if c.find("error") is not None:
            return "error"
        if c.find("skipped") is not None:
            return "skipped"
        return "passed"

    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for c in cases:
        counts[status(c)] += 1
    s2 = [c for c in cases if "test_s2_" in c.get("classname", "")]
    skipped = sorted({(c.find("skipped").get("message") or "")[:120] for c in cases if c.find("skipped") is not None})
    slow = sorted(((float(c.get("time", 0)), c.get("classname", "").split(".")[-1] + "::" + c.get("name", "")[:60]) for c in cases), reverse=True)[:5]
    out = {
        "command": "pytest -q -rs --durations=5" + (f' -m "{a.markers}"' if a.markers else " (느린 시험 포함)") + " (spikes/s1-core)",
        "collected": len(cases),
        "passed": counts["passed"], "failed": counts["failed"], "errors": counts["error"], "skipped": counts["skipped"],
        "skipped_reasons": skipped,
        "s1_baseline_tests": len(cases) - len(s2), "s2_tests": len(s2),
        "s2_tests_passed": sum(1 for c in s2 if status(c) == "passed"),
        "slowest_s": [{"test": n, "seconds": round(t, 1)} for t, n in slow],
        "wall_seconds": round(wall),
        "pytest_summary_line": next((ln for ln in reversed(tail.splitlines()) if re.search(r"\d+ (passed|failed)", ln)), None),
        "environment": {"DOBIM_IFCDIFF_PYTHON": "설정함" if os.environ.get("DOBIM_IFCDIFF_PYTHON") else "없음",
                        "DOBIM_REVIT_DIR": "설정함(로컬 비공개 표본)" if os.environ.get("DOBIM_REVIT_DIR") else "없음"},
        "platform": platform.system() + " " + platform.release(), "python": platform.python_version(),
        "exit_code": p.returncode,
    }
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("collected", "passed", "failed", "errors", "skipped", "wall_seconds")}, ensure_ascii=False))
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
