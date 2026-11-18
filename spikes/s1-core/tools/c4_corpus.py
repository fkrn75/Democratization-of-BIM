# SPDX-License-Identifier: MPL-2.0
"""C-4 회귀 코퍼스: main 기준으로 다시 잰 S1 벤치·퍼즈·실행 취소, S10 헤드리스, S11 (클라이언트 없는) 측정을 기준값으로 고정한다.

G1 4절·G2 6절(C-4): 스파이크 브랜치 수치뿐이던 S1 T0 벤치·10k 퍼즈·취소 시드, S10, S11 을 main 에서 다시 재서 1단계 회귀
코퍼스로 흡수한다. 이 도구는 같은 측정을 다시 돌려 results/c4_baseline.json 과 비교한다.

  python tools/c4_corpus.py [--quick] [--out 결과.json]          # 측정 + 기준값 비교(어긋나면 종료 코드 1)
  python tools/c4_corpus.py [--quick] --write-baseline "이유"      # 기준값 갱신(이유는 파일의 history 에 남는다)

비교 규칙
- 결정적 값(퍼즈의 의미 해시 3종·로그 길이·성공/실패 수·최종 요소 수, 실행 취소의 위반·검증 단계 수, S10 시험 결과 수)은 정확히 같아야 한다.
  커맨드 의미가 의도적으로 바뀌면(새 파생값 등) 해시가 바뀐다: 그때는 --write-baseline 과 이유로 갱신한다.
- 시간 값(T0 p95, 응답 시간)은 기기마다 달라 기준값과 비교하지 않고 합격 기준(T0 p95 <= 50ms)만 본다. 잰 값은 기록한다.
- S11 층 C·탐색은 LLM 클라이언트가 있어야 해 여기 넣지 않는다(C-1 이 잰다). 클라이언트 없이 결정적인 층 A(패리티 C: tools/parity.py,
  test_slow.py 가 따로 돌린다)·응답 크기(size_probe)·클라이언트 분기 금지(lint_no_client_branch)만 넣는다.

--quick 은 일반 시험(tests/test_regression_corpus.py)용 축소판이다: 퍼즈 300개·실행 취소 2시드만, 수 초 안에 끝난다.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
S1 = HERE.parent
SPIKES = S1.parent
sys.path.insert(0, str(HERE))
BASELINE = S1 / "results" / "c4_baseline.json"

FUZZ_FULL = ((20261107, 10000), (424242, 10000))   # S1 합격 기준 1 의 두 시드(results/s1_fuzz_10k*.json 과 같은 시드·개수)
FUZZ_QUICK = ((20261107, 300),)
UNDO_FULL = (tuple(range(1, 21)), 200)              # S1 합격 기준 2(results/s1_undo.json 과 같은 시드·개수)
UNDO_QUICK = ((1, 2), 60)
T0_TARGET_MS = 50.0


def fuzz(seeds) -> list[dict]:
    import kit
    out = []
    for seed, n in seeds:
        work = Path(tempfile.mkdtemp(prefix="dobim-c4-fuzz-"))
        t0 = time.perf_counter()
        r = kit.property_run(seed, n, work, checkpoint_every=500, validate=True)
        sess = kit.Session(work / f"fuzz-{seed}.dobim", create=False)
        from dobim_core import queries as Q
        counts = sess.eng.g.q(Q.model_stats)
        sess.close()
        out.append({"seed": seed, "n_items": n, "ok": r["ok"], "diffs": r["diffs"], "hashes": r["hashes"], "log_entries": r["rev"],
                    "items_succeeded": r["succeeded"], "items_failed": r["failed"], "final_counts": counts,
                    "_elapsed_sec": round(time.perf_counter() - t0, 1)})
    return out


def undo(seeds, n) -> dict:
    import kit
    work = Path(tempfile.mkdtemp(prefix="dobim-c4-undo-"))
    runs = [kit.undo_property(s, n, work) for s in seeds]
    return {"seeds": list(seeds), "items_per_seed": n, "violations": [v for r in runs for v in r["violations"]],
            "total_hash_verified_steps": sum(r["verified_steps"] for r in runs),
            "total_branch_checks": sum(r["branch_checks"] for r in runs), "total_txns": sum(r["txns"] for r in runs)}


def _run_json(argv, cwd, timeout=3600) -> tuple[int, str]:
    # S11 도구는 코어 venv 를 S11_PY 로 받는다(기본값은 s1-core/.venv: worktree 에는 없을 수 있어 지금 파이썬을 넘긴다)
    p = subprocess.run([sys.executable, *argv], cwd=cwd, capture_output=True, timeout=timeout,
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "S11_PY": sys.executable})
    return p.returncode, p.stdout.decode("utf-8", "replace")


def bench() -> dict:
    """S1 합격 기준 3: 합성 5,000요소 T0 p95. 시간 값이라 기준값과 비교하지 않고 목표만 본다."""
    out = Path(tempfile.mkdtemp(prefix="dobim-c4-bench-")) / "bench.json"
    code, _ = _run_json([str(HERE / "bench_t0.py"), "--out", str(out)], S1)
    d = json.loads(out.read_text(encoding="utf-8"))
    run = d["runs"][0]
    return {"exit": code, "elements": run["elements"], "_p95_ms": run["T0"]["p95_ms"], "_p50_ms": run["T0"]["p50_ms"],
            "_max_ms": run["T0"]["max_ms"], "pass_p95_50ms": run["T0"]["p95_ms"] <= T0_TARGET_MS}


def s10() -> dict:
    """S10 헤드리스 시험 전체(느린 시험 포함). 통과·실패·건너뜀 수가 기준값과 같아야 한다."""
    # addopts 의 -q 와 겹치면(-qq) 요약 줄이 사라지므로 addopts 를 비우고 -q 하나만 준다
    code, text = _run_json(["-m", "pytest", "-o", "addopts=", "-q", "-p", "no:cacheprovider", "tests"], SPIKES / "s10-headless")
    summary = next((ln for ln in reversed(text.strip().splitlines()) if re.search(r"\d+ (passed|failed)", ln)), "")
    counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|errors?|deselected)", summary)}
    failed = sorted(set(re.findall(r"^FAILED (\S+)", text, re.M)))
    return {"exit": code, "counts": counts, "failed": failed}


def s11() -> dict:
    """S11 중 클라이언트 없이 결정적인 두 측정: 도구 응답 크기(size_probe)와 클라이언트 분기 금지(lint)."""
    tools = SPIKES / "s11-clients" / "tools"
    tmp = Path(tempfile.mkdtemp(prefix="dobim-c4-s11-"))
    code_l, _ = _run_json([str(tools / "lint_no_client_branch.py"), str(tmp / "lint.json")], tools)
    lint = json.loads((tmp / "lint.json").read_text(encoding="utf-8")) if (tmp / "lint.json").exists() else {}
    code_s, _ = _run_json([str(tools / "size_probe.py"), str(tmp / "size.json")], tools)
    size = json.loads((tmp / "size.json").read_text(encoding="utf-8")) if (tmp / "size.json").exists() else {}
    return {"lint_exit": code_l, "lint_pass": lint.get("pass"),
            "size_exit": code_s, "over_8000_by_tiktoken": size.get("over_8000_by_tiktoken"),
            "_max_est_tokens": size.get("max_est_tokens_bytes_div3"), "_max_tiktoken_o200k": size.get("max_tiktoken_o200k"),
            "_slowest_ms": size.get("slowest_ms")}


def measure(quick: bool) -> dict:
    if quick:
        return {"fuzz": fuzz(FUZZ_QUICK), "undo": undo(*UNDO_QUICK)}
    return {"fuzz": fuzz(FUZZ_FULL), "undo": undo(*UNDO_FULL), "bench_t0": bench(), "s10": s10(), "s11": s11()}


def strip_timing(x):
    """비교에서 뺄 값(이름이 _ 로 시작하는 키: 시간·기기 의존 값)을 지운다."""
    if isinstance(x, dict):
        return {k: strip_timing(v) for k, v in x.items() if not k.startswith("_")}
    if isinstance(x, list):
        return [strip_timing(v) for v in x]
    return x


def compare(got: dict, want: dict) -> list[str]:
    """기준값과 다른 곳의 경로 목록. 시간 값은 빼고, 목표 판정(pass_*)은 참이어야 한다."""
    bad = []

    def walk(a, b, path):
        if isinstance(b, dict) and isinstance(a, dict):
            for k in sorted(set(a) | set(b)):
                if k not in a or k not in b:
                    bad.append(f"{path}/{k}: {'없음' if k not in a else '기준값에 없음'}")
                else:
                    walk(a[k], b[k], f"{path}/{k}")
        elif isinstance(b, list) and isinstance(a, list) and len(a) == len(b) and any(isinstance(x, dict) for x in b):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}/{i}")
        elif a != b:
            bad.append(f"{path}: {json.dumps(a, ensure_ascii=False)[:120]} != 기준 {json.dumps(b, ensure_ascii=False)[:120]}")

    walk(strip_timing(got), strip_timing(want), "")
    for path, v in _flags(got, ""):
        if v is not True:
            bad.append(f"{path}: 목표 미달")
    return bad


def _flags(x, path):
    if isinstance(x, dict):
        for k, v in x.items():
            if k.startswith("pass_"):
                yield f"{path}/{k}", v
            else:
                yield from _flags(v, f"{path}/{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _flags(v, f"{path}/{i}")


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=S1, capture_output=True, text=True).stdout.strip()
    except OSError:
        return "?"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--write-baseline", metavar="이유")
    a = ap.parse_args(argv)
    mode = "quick" if a.quick else "full"
    t0 = time.perf_counter()
    got = measure(a.quick)
    meta = {"mode": mode, "commit": _commit(), "platform": platform.platform(), "python": platform.python_version(),
            "elapsed_sec": round(time.perf_counter() - t0, 1)}
    if a.out:
        Path(a.out).write_text(json.dumps({"meta": meta, **got}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if a.write_baseline:
        base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {"history": []}
        base[mode] = got
        base["history"].append({"date": date.today().isoformat(), "mode": mode, "commit": meta["commit"], "reason": a.write_baseline})
        BASELINE.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({"written": mode, **meta}, ensure_ascii=False))
        return 0
    want = json.loads(BASELINE.read_text(encoding="utf-8"))[mode]
    bad = compare(got, want)
    print(json.dumps({"pass": not bad, "mismatches": bad, **meta}, ensure_ascii=False, indent=2))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
