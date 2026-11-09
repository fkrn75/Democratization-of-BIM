# SPDX-License-Identifier: MPL-2.0
"""S8 시나리오 실행기: 시나리오 전체를 별도 프로세스에서 R 번(해시 시드·순서·지연을 바꿔) 돌리고 판정한다.

사용: python run_scenarios.py [--runs 4] [--out results/s8_scenarios.json] [--mutant 이름]
결과 JSON: 실행 구성, 시나리오별 요약표(충돌 유형·해시), 판정 실패 목록.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import s8check  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

SEEDS = [0, 1, 31337, 987654321, 7, 2024]
CONFIGS = [  # (PYTHONHASHSEED, flip, delay)
    (SEEDS[i % len(SEEDS)], i % 2, 1.1 if i % 3 == 2 else 0.0) for i in range(12)]


def run_children(n: int, mutant: str | None = None, ids: str = "", workers: int = 4) -> tuple[list[dict], list[dict]]:
    tmp = Path(tempfile.mkdtemp(prefix="s8-runs-"))

    def one(i):
        seed, flip, delay = CONFIGS[i]
        out = tmp / f"run{i}.json"
        env = {**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONUTF8": "1"}
        if mutant:
            env["S8_MUTANT"] = mutant
        cmd = [sys.executable, str(HERE / "s8_child.py"), "--out", str(out), "--flip", str(flip), "--delay", str(delay)]
        if ids:
            cmd += ["--ids", ids]
        p = subprocess.run(cmd, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=1800)
        if p.returncode != 0 or not out.exists():
            raise SystemExit(f"자식 {i} 실패: {p.stderr.decode('utf-8', 'replace')[-800:]}")
        return json.loads(out.read_text(encoding="utf-8"))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        runs = list(ex.map(one, range(n)))
    return runs, [{"run": i, "PYTHONHASHSEED": CONFIGS[i][0], "reverse_order": bool(CONFIGS[i][1]), "branch_delay_s": CONFIGS[i][2]} for i in range(n)]


def summarize(runs: list[dict]) -> list[dict]:
    rows = []
    for sc in SCENARIOS:
        r = runs[0].get(sc["id"])
        if not r or not r.get("flows"):
            continue
        row = {"id": sc["id"], "title": sc["title"], "expect": sc["expect"], "flows": {}}
        for d, f in r["flows"].items():
            e = {"relation": f["preview"]["relation"], "fork_rev": f["preview"]["fork_rev"], "conflicts": f["preview"]["by_type"],
                 "apply": "ok" if f["apply"]["ok"] else f["apply"].get("code"), "plan_digest": f["preview"]["plan_digest"],
                 "hashes": f["apply"].get("hashes"), "preview_bytes": f["preview_envelope_bytes"]}
            if f.get("variants"):
                e["variants"] = {vn: {"rounds": v["rounds"], "apply": "ok" if v["apply"]["ok"] else v["apply"].get("code"),
                                      "hashes": v["apply"].get("hashes"), "facts_failed": v.get("facts")} for vn, v in f["variants"].items()}
            row["flows"][d] = e
        row["replay_oracle"] = {d: {k: v for k, v in o.items() if k != "hashes"} for d, o in (r.get("oracle") or {}).items()}
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--out", default=None)
    ap.add_argument("--mutant", default=None)
    ap.add_argument("--ids", default="")
    ap.add_argument("--workers", type=int, default=4)
    ns = ap.parse_args()
    runs, cfg = run_children(ns.runs, ns.mutant, ns.ids, ns.workers)
    fails = s8check.check(runs, ns.ids.split(",") if ns.ids else None)
    out = {"runs": cfg, "scenarios": len(SCENARIOS) if not ns.ids else len(ns.ids.split(",")), "mutant": ns.mutant,
           "failures": fails, "pass": not fails, "table": summarize(runs)}
    text = json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True)
    if ns.out:
        Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.out).write_text(text, encoding="utf-8")
    print(json.dumps({"pass": out["pass"], "failures": fails[:20], "n_failures": len(fails)}, ensure_ascii=False, indent=1))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
