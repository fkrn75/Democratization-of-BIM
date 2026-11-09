# SPDX-License-Identifier: MPL-2.0
"""S8 음성 대조(변이 시험): 결함을 심은 병합을 판정기에 걸어 잡히는지 확인한다.

사용: python run_mutants.py [--runs 3] [--out results/s8_mutants.json]
통과 조건: 변이마다 판정기가 실패를 하나 이상 보고한다(= 잡았다). 안 잡힌 변이가 있으면 종료 코드 1.
먼저 변이 없는 기준선이 합격인지 확인한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import s8check  # noqa: E402
import s8mutants  # noqa: E402
from run_scenarios import run_children  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=3)
    ns = ap.parse_args()
    results = {}
    prev = {}
    if ns.only and ns.out and Path(ns.out).exists():   # 일부 변이만 다시 돌리면 기존 결과 파일에 합친다
        prev = json.loads(Path(ns.out).read_text(encoding="utf-8"))["results"]
        results = {k: v for k, v in prev.items() if k != "baseline"}
    base_runs, _ = run_children(ns.runs, None, "", ns.workers)
    base_fails = s8check.check(base_runs)
    results["baseline"] = {"failures": len(base_fails), "pass": not base_fails}
    caught = 0
    for name, desc in s8mutants.MUTANTS.items():
        if ns.only and name not in ns.only.split(","):
            continue
        results.pop(name, None)
        runs, _ = run_children(ns.runs, name, "", ns.workers)
        fails = s8check.check(runs)
        cats = sorted({f.split("]")[0].lstrip("[") for f in fails})
        results[name] = {"description": desc, "caught": bool(fails), "failure_count": len(fails), "categories": cats,
                         "examples": fails[:3]}
        print(f"{name}: {'잡힘' if fails else '못 잡음'} ({len(fails)}건, {cats})", flush=True)
    caught = sum(1 for k, v in results.items() if k != "baseline" and v["caught"])
    n = len([k for k in results if k != "baseline"])
    out = {"mutants": n, "caught": caught, "baseline_pass": not base_fails, "results": results}
    if ns.out:
        Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return 0 if (caught == n and not base_fails) else 1


if __name__ == "__main__":
    sys.exit(main())
