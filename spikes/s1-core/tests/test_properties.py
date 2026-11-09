# SPDX-License-Identifier: MPL-2.0
"""속성 시험: 임의 커맨드열에서 증분 = 전체 재생, 실행 취소 해시 복원, 동시성. 시드는 고정이다."""
import pytest

import kit


@pytest.mark.parametrize("seed", range(1, 9))
def test_증분_그래프_기준_그래프_전체_재생_새로_열기가_모두_일치한다(seed, tmp_path):
    r = kit.property_run(seed, 250, tmp_path, checkpoint_every=25, validate=True)
    assert r["ok"], (r["diffs"], kit.describe(r["items"][:30]))
    assert r["failed"] > 0 and r["succeeded"] > 100          # 실패 입력도 섞여 있어야 시험이 의미 있다


@pytest.mark.parametrize("seed", range(1, 6))
def test_실행_취소와_다시_실행은_상태_파생값_ifc_해시를_복원한다(seed, tmp_path):
    r = kit.undo_property(seed, 120, tmp_path)
    assert r["violations"] == [], r
    assert r["verified_steps"] > 100


def test_생성기는_같은_시드에서_같은_열을_낸다(tmp_path):
    a = kit.property_run(7, 80, tmp_path / "a")["items"]
    b = kit.property_run(7, 80, tmp_path / "b")["items"]
    c = kit.property_run(8, 80, tmp_path / "c")["items"]
    assert a == b and a != c


def test_교차_엔진_동시성(tmp_path):
    assert kit.concurrency_property(tmp_path) == []


def test_ddmin_은_실패를_일으키는_최소_부분열을_찾는다(monkeypatch, tmp_path):
    items = [{"op": "cmd", "name": f"c{i}", "args": {}} for i in range(40)]
    need = {"c7", "c23"}
    monkeypatch.setattr(kit, "fails", lambda cand, wd, validate=False: need <= {x["name"] for x in cand})
    small, evals = kit.shrink(items, tmp_path)
    assert {x["name"] for x in small} == need and evals < 120
