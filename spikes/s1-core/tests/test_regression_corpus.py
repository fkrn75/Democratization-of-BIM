# SPDX-License-Identifier: MPL-2.0
"""C-4 회귀 코퍼스(축소판): main 기준으로 고정한 퍼즈·실행 취소 결과가 그대로인가. 전체판은 test_slow.py 가 돌린다(tools/c4_corpus.py)."""
import copy
import json

import c4_corpus


def _base():
    return json.loads(c4_corpus.BASELINE.read_text(encoding="utf-8"))


def test_C4_축소_코퍼스가_기준값과_같다():
    got = c4_corpus.measure(quick=True)
    bad = c4_corpus.compare(got, _base()["quick"])
    assert not bad, bad[:5]


def test_C4_기준값에는_전체판과_갱신_이력이_있다():
    b = _base()
    full = b["full"]
    assert [f["seed"] for f in full["fuzz"]] == [20261107, 424242] and all(f["n_items"] == 10000 and f["ok"] for f in full["fuzz"])
    assert full["undo"]["violations"] == [] and full["undo"]["seeds"] == list(range(1, 21))
    assert full["bench_t0"]["pass_p95_50ms"] is True
    assert full["s10"]["counts"].get("failed", 0) == 0 and full["s11"]["lint_pass"] is True
    assert b["history"] and all(h["reason"] for h in b["history"])


def test_C4_비교기는_어긋남과_목표_미달을_잡고_시간_값은_무시한다():
    want = _base()["full"]
    got = copy.deepcopy(want)
    got["bench_t0"]["_p95_ms"] = 999.0                    # 시간 값은 비교하지 않는다
    assert c4_corpus.compare(got, want) == []
    got["fuzz"][0]["hashes"]["state"] = "0" * 64           # 의미 해시가 바뀌면 잡는다
    got["bench_t0"]["pass_p95_50ms"] = False               # 목표 미달도 잡는다
    bad = c4_corpus.compare(got, want)
    assert any("/fuzz/0/hashes/state" in x for x in bad) and any("pass_p95_50ms" in x for x in bad)
