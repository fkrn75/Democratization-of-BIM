# SPDX-License-Identifier: MPL-2.0
"""이전 스파이크 결과(각 스파이크 브랜치의 results/*.json)와 통합본에서 다시 잰 결과를 비교한다.

사용: python compare.py <이전 폴더> <새 폴더> [--out compare.json]
두 폴더는 같은 파일 이름의 JSON 을 가진다. 값을 경로(a.b[3].c)로 펴서 맞대 본다.
- 시간·처리량처럼 실행마다 달라지는 값(키 이름이 ms, sec, time, elapsed, per_sec 등을 포함)은 "변동"으로 따로 센다: 불일치로 세지 않고 이전·새 값을 둘 다 적는다.
- 그 밖의 값(개수·판정·해시·통과 여부)이 다르면 "불일치"다. 해시는 실행 환경(시각)에 안 기대는 값이라 다르면 안 된다.
- 한쪽에만 있는 경로는 "이전만"/"새것만"으로 센다(통합으로 더해진 필드가 여기 든다).
절대 경로·사용자명은 결과에 넣지 않는다.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

VOLATILE = re.compile(r"(^|[_.\[])(ms|sec|secs|seconds|time|times|elapsed|duration|dur|per_sec|rate|speedup|ts|date|started|platform|python|version|rss|mb|kb)([_.\]]|$)", re.I)
VOLATILE_SUFFIX = re.compile(r"(_ms|_s|_sec|_us|_ns|_mb|_kb|_bytes_per_s|_per_s)$", re.I)


def flatten(obj, path=""):
    if isinstance(obj, dict):
        for k in sorted(obj):
            yield from flatten(obj[k], f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flatten(v, f"{path}[{i}]")
    else:
        yield path, obj


def is_volatile(path: str) -> bool:
    last = re.sub(r"(\[\d+\])+$", "", path).split(".")[-1]   # place_ms[3] -> place_ms
    return bool(VOLATILE.search(path) or VOLATILE_SUFFIX.search(last))


def compare_file(old, new, limit=25) -> dict:
    fo, fn = dict(flatten(old)), dict(flatten(new))
    same = 0
    differ, volatile = [], []
    for p in sorted(set(fo) & set(fn)):
        a, b = fo[p], fn[p]
        if a == b:
            same += 1
        elif is_volatile(p) and isinstance(a, (int, float)) and isinstance(b, (int, float)):
            volatile.append({"path": p, "old": a, "new": b})
        else:
            differ.append({"path": p, "old": a if not isinstance(a, str) else a[:60], "new": b if not isinstance(b, str) else b[:60]})
    return {"same": same, "differ": len(differ), "volatile": len(volatile), "only_old": len(set(fo) - set(fn)), "only_new": len(set(fn) - set(fo)),
            "differ_examples": differ[:limit], "volatile_examples": volatile[:8],
            "only_old_examples": sorted(set(fo) - set(fn))[:8], "only_new_examples": sorted(set(fn) - set(fo))[:8]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--out", default=None)
    ns = ap.parse_args()
    out = {}
    for f in sorted(Path(ns.old).glob("*.json")):
        g = Path(ns.new) / f.name
        if not g.exists():
            out[f.name] = {"missing_new": True}
            continue
        out[f.name] = compare_file(json.loads(f.read_text(encoding="utf-8")), json.loads(g.read_text(encoding="utf-8")))
    for g in sorted(Path(ns.new).glob("*.json")):
        if not (Path(ns.old) / g.name).exists():
            out[g.name] = {"missing_old": True}
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if ns.out:
        Path(ns.out).write_text(text, encoding="utf-8")
    for name, r in out.items():
        print(name, {k: v for k, v in r.items() if not k.endswith("examples")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
