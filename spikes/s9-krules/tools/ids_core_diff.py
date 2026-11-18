# SPDX-License-Identifier: MPL-2.0
"""S9 시험 전체를 돌리면서 IDS 평가마다 ifctester(스파이크 실행기)와 코어 평가기(dobim_core.krules.ids_eval) 결과를 대조하는 pytest 플러그인.

코어는 ifctester 를 쓰지 않는다(전이 의존 bcf-client 가 GPLv3). 코어 평가기가 S9 의 ifctester 판정과 같다는 증거를 남기려고 둔다.
ifctester 가 있는 S9 전용 venv(spikes/s0-baseline/.venv)에서 실행한다:
  cd spikes/s9-krules && PYTHONPATH=tools python -m pytest -q -s -p ids_core_diff tests
끝에 'IDSDIFF {calls: n, mismatch: 0}' 을 찍는다. 불일치가 있으면 MISMATCH 줄에 규칙 id 와 두 결과를 찍는다(시험 판정은 스파이크 값 그대로).
"""
import json
import sys
from pathlib import Path

W = Path(__file__).resolve().parent.parent.parent   # spikes/
sys.path.insert(0, str(W / "s1-core" / "src"))
sys.path.insert(0, str(W / "s9-krules"))
from krrules import run as spike  # noqa: E402
from dobim_core.krules import runner as core  # noqa: E402

STATS = {"calls": 0, "mismatch": 0}
_orig = spike.run_ids


def both(f, ids_path=spike.IDS_PATH):
    a = _orig(f, ids_path)
    b = core.run_ids(f, ids_path)
    STATS["calls"] += 1
    if json.dumps(a, sort_keys=True, ensure_ascii=False) != json.dumps(b, sort_keys=True, ensure_ascii=False):
        STATS["mismatch"] += 1
        for k in a:
            if a[k] != b.get(k):
                print("MISMATCH", k, json.dumps(a[k], ensure_ascii=False)[:400], "||", json.dumps(b.get(k), ensure_ascii=False)[:400])
    return a


spike.run_ids = both


def pytest_sessionfinish(session, exitstatus):
    print("\nIDSDIFF", STATS)
