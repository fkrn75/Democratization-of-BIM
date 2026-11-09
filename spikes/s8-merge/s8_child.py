# SPDX-License-Identifier: MPL-2.0
"""자식 프로세스: 시나리오 모음을 처음부터 만들어 양방향으로 병합하고 결과 JSON 을 쓴다.

부모(run_scenarios.py, 시험)가 PYTHONHASHSEED·순서·지연을 바꿔 여러 번 띄운다. 결과가 이 변수들에 기대면 실행 사이에 달라진다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import s8lib  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--flip", type=int, default=0, help="1 이면 시나리오 순서·병합 방향 순서를 거꾸로")
    ap.add_argument("--delay", type=float, default=0.0, help="두 가지를 만드는 사이의 대기(초): 로그 시각 ts 가 달라진다")
    ap.add_argument("--mutant", default=os.environ.get("S8_MUTANT"))
    ap.add_argument("--ids", default="", help="쉼표로 구분한 시나리오 id(생략하면 전체)")
    ns = ap.parse_args()
    if ns.mutant:
        import s8mutants
        s8mutants.install(ns.mutant)
    scs = [s for s in SCENARIOS if not ns.ids or s["id"] in ns.ids.split(",")]
    dirs = s8lib.DIRS[::-1] if ns.flip else s8lib.DIRS
    if ns.flip:
        scs = scs[::-1]
    out = {}
    root = Path(tempfile.mkdtemp(prefix="s8-child-"))
    try:
        for sc in scs:
            try:
                out[sc["id"]] = s8lib.run_scenario(sc, root / sc["id"], delay=ns.delay, dirs=dirs)
            except Exception as ex:   # 변이가 엔진을 깨뜨려도 판정기가 볼 수 있게 기록한다
                out[sc["id"]] = {"id": sc["id"], "crash": f"{type(ex).__name__}: {str(ex)[:200]}", "flows": {}}
        try:
            out["_extra"] = s8lib.extra_checks(root / "_extra")
        except Exception as ex:
            out["_extra"] = {"crash": f"{type(ex).__name__}: {str(ex)[:200]}"}
        # 방향 순서를 뒤집었어도 결과 표현은 같아야 하므로 키를 정렬해 쓴다
        Path(ns.out).write_text(json.dumps(out, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
