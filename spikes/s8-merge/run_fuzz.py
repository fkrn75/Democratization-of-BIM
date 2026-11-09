# SPDX-License-Identifier: MPL-2.0
"""무작위 두 가지 병합 시험의 통계: 깨끗한 병합 / 충돌 유형 분포 / 불변식 위반 수. 사용: python run_fuzz.py [--pairs 120] [--out results/s8_fuzz.json]"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import s8lib  # noqa: E402,F401
import test_fuzz as T  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=120)
    ap.add_argument("--steps", type=int, default=16)
    ap.add_argument("--out", default=None)
    ns = ap.parse_args()
    stats = {"pairs": ns.pairs, "clean": 0, "conflict": 0, "types": {}, "violations": [], "symmetric_clean": 0, "resolved_ok": 0, "resolve_stuck": 0}
    for seed in range(1, ns.pairs + 1):
        d = Path(tempfile.mkdtemp(prefix="s8-fuzz-"))
        try:
            T.build(d, 1000 + seed, 14, ns.steps, ns.steps)
            ab, ba = T.one_way(d, "a", "b", "ab"), T.one_way(d, "b", "a", "ba")
            if ab["keys"] != ba["keys"]:
                stats["violations"].append(f"seed {seed}: 충돌 집합이 방향에 따라 다르다")
            if ab["keys"]:
                stats["conflict"] += 1
                stats["resolved_ok" if ab.get("resolved") else "resolve_stuck"] += 1
                for _k, t in ab["keys"]:
                    stats["types"][t] = stats["types"].get(t, 0) + 1
            else:
                stats["clean"] += 1
                if ab["hashes"] == ba["hashes"]:
                    stats["symmetric_clean"] += 1
                else:
                    stats["violations"].append(f"seed {seed}: 깨끗한 병합인데 방향에 따라 해시가 다르다")
        except AssertionError as ex:
            stats["violations"].append(f"seed {seed}: {str(ex)[:200]}")
    stats["pass"] = not stats["violations"]
    text = json.dumps(stats, ensure_ascii=False, indent=1, sort_keys=True)
    if ns.out:
        Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if stats["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
