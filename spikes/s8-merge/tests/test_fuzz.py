# SPDX-License-Identifier: MPL-2.0
"""무작위 두 가지 병합: S1 생성기로 만든 임의 커맨드열(실패 입력·배치·실행 취소 포함)을 두 가지에 돌려 양방향으로 병합한다.
불변식: 내부 오류가 없다 / 충돌 집합이 방향과 무관 / 깨끗하면 두 방향의 결과 해시가 같다 / 합친 뒤 IFC 투영이 일관 /
충돌 거부는 상태를 건드리지 않는다 / 모든 충돌을 해결하면 합쳐진다 / 합치고 되돌리면 병합 전 해시."""
import shutil

import pytest

import kit
import s8lib
from s8lib import hashes3

N_PAIRS = 36


def build(tmp_path, seed, n0=14, na=10, nb=10):
    anc = kit.Session(tmp_path / "anc.dobim", "anc")
    try:
        kit.generate_and_run(anc, seed, n0)
    finally:
        anc.close()
    for n in "ab":
        shutil.copyfile(tmp_path / "anc.dobim", tmp_path / f"{n}.dobim")
    for n, sd, cnt in (("a", seed * 1000 + 1, na), ("b", seed * 1000 + 2, nb)):
        s = kit.Session(tmp_path / f"{n}.dobim", "alice" if n == "a" else "bob", create=False)
        try:
            kit.generate_and_run(s, sd, cnt)
        finally:
            s.close()


def resolve_all(s, take, rounds=6):
    """미리보기를 읽고 보이는 충돌을 모두 같은 쪽으로 해결하는 일을 반복한다(해결하면 새 의미 충돌이 드러날 수 있다).
    해결한 상태로 합쳐지면 불변식(IFC 일관, 되돌리기)을 확인한다. 해결 선택지가 없는 충돌이 남으면 None."""
    rl = []
    for _ in range(rounds):
        pv = s.run("merge.preview", {"other": "other.dobim", "limit": 200, "resolve": rl})
        assert pv["ok"], pv
        items = pv["result"]["items"]
        if not items:
            break
        new = [{"key": c["key"], "take": take if take in c["take"] else c["take"][0]} for c in items if c["take"]]
        if not new or all(r["key"] in {x["key"] for x in rl} for r in new):
            return None   # 이 선택지로는 풀리지 않는 충돌(되돌릴 기여가 없음)
        rl += [r for r in new if r["key"] not in {x["key"] for x in rl}]
    h0, rev0 = hashes3(s.eng), s.eng.rev
    ap = s.run("merge.apply", {"other": "other.dobim", "resolve": rl})
    assert ap["ok"] or ap["error"]["code"] == "MERGE_CONFLICT", ap   # 내부 오류가 아니어야 한다
    if not ap["ok"]:
        return None
    assert s.eng.intent_consistency() == []
    if s.eng.rev > rev0:
        assert s.run("history.undo", {})["ok"] and hashes3(s.eng) == h0
    return len(rl)


def one_way(tmp_path, ours, theirs, tag):
    d = tmp_path / tag
    d.mkdir()
    shutil.copyfile(tmp_path / f"{ours}.dobim", d / "o.dobim")
    shutil.copyfile(tmp_path / f"{theirs}.dobim", d / "other.dobim")
    s = kit.Session(d / "o.dobim", "merger", create=False)
    try:
        h0, rev0 = hashes3(s.eng), s.eng.rev
        pv = s.run("merge.preview", {"other": "other.dobim", "limit": 200})
        assert pv["ok"], pv
        conf = pv["result"]["items"]
        ap = s.run("merge.apply", {"other": "other.dobim"})
        info = {"keys": [(c["key"], c["type"]) for c in conf], "total": pv["result"]["conflict_count"]}
        if conf:
            assert not ap["ok"] and ap["error"]["code"] == "MERGE_CONFLICT"
            assert hashes3(s.eng) == h0 and s.eng.rev == rev0
            info["resolved"] = resolve_all(s, "theirs")
        else:
            assert ap["ok"], ap
            info["hashes"] = hashes3(s.eng)
            assert s.eng.intent_consistency() == []
            if s.eng.rev > rev0:
                assert s.run("history.undo", {})["ok"] and hashes3(s.eng) == h0
        return info
    finally:
        s.close()


@pytest.mark.parametrize("seed", range(1, N_PAIRS + 1))
def test_무작위_가지_병합(tmp_path, seed):
    build(tmp_path, seed)
    ab = one_way(tmp_path, "a", "b", "ab")
    ba = one_way(tmp_path, "b", "a", "ba")
    assert ab["keys"] == ba["keys"], "충돌 집합이 방향에 따라 다르다"
    if not ab["keys"]:
        assert ab["hashes"] == ba["hashes"], "깨끗한 병합인데 방향에 따라 결과가 다르다"
