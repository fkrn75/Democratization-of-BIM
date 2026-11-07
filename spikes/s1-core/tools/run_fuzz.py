# SPDX-License-Identifier: MPL-2.0
"""합격 기준 1: 임의 커맨드 N개에서 증분 결과 = 전체 재생 결과. 결과를 JSON 으로 남긴다.

사용: python tools/run_fuzz.py --seed 20261107 --n 10000 --out results/s1_fuzz_10k.json
"""
import argparse
import collections
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit  # noqa: E402
from dobim_core import queries as Q  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261107)
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--checkpoint", type=int, default=500)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-validate", action="store_true")
    ns = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="dobim-fuzz-"))
    t0 = time.perf_counter()
    r = kit.property_run(ns.seed, ns.n, work, checkpoint_every=ns.checkpoint, validate=not ns.no_validate)
    items = r.pop("items")
    kinds = collections.Counter(it["name"] if it["op"] == "cmd" else "batch" for it in items)
    sess = kit.Session(work / f"fuzz-{ns.seed}.dobim", create=False)
    counts = sess.eng.g.q(Q.model_stats)
    log = kit.log_rows(sess.eng)
    ops = collections.Counter(row[3] for row in log)
    sess.close()
    out = {"seed": ns.seed, "n_items": ns.n, "elapsed_sec": round(time.perf_counter() - t0, 1),
           "ok": r["ok"], "diffs": r["diffs"], "hashes": r["hashes"], "log_entries": r["rev"],
           "items_succeeded": r["succeeded"], "items_failed": r["failed"], "final_counts": counts,
           "item_kinds": dict(kinds), "logged_ops": dict(ops), "check_seconds": r["sec"],
           "platform": platform.platform(), "python": platform.python_version()}
    if not r["ok"]:
        small, evals = kit.shrink(items, work)
        out["shrunk_repro"] = {"items": len(small), "evals": evals, "text": kit.describe(small)}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if ns.out:
        Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
