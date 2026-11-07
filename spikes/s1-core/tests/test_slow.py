# SPDX-License-Identifier: MPL-2.0
"""서브프로세스를 쓰는 합격 기준 시험(패리티 C, 교차 프로세스, 변이 시험의 축소판).
전체 규모의 측정값은 tools/*.py 를 실행해 results/*.json 에 남긴다."""
import asyncio

import pytest

import cross_process
import parity
import run_mutants
import kit

pytestmark = pytest.mark.slow


def test_패리티_C_모든_커맨드의_CLI_MCP_결과가_같다(tmp_path):
    res = asyncio.run(parity.run_parity(tmp_path))
    assert res["pass"], res["mismatches"][:2]
    assert res["commands_executed_with_examples"] == res["commands_in_registry"]
    assert res["negative_control_detects_divergence"]


def test_교차_프로세스_번갈아_쓰기와_교차_실행_취소(tmp_path):
    res = asyncio.run(cross_process.run(24, 4242, tmp_path, cli_hash_every=6))
    assert not res["hash_mismatches"] and not res["step_failures"], res
    assert res["final"]["reopen_equals_replay"] and res["final"]["equals_oracle"]
    assert res["explicit_cross_undo"]["mcp_undid_cli_writes"] and res["explicit_cross_undo"]["cli_redo_restores"]
    r = cross_process.race(tmp_path / "ws", rounds_free=1, rounds_rev=2)
    assert r["all_rev_rounds_exactly_one_winner"] and r["log_growth_equals_successful_writes"]
    assert r["reopen_equals_replay_after_race"]


@pytest.mark.parametrize("mutant", ["M01", "M05", "M08", "M10", "M12"])
def test_변이를_심으면_시험이_잡아낸다(mutant, tmp_path):
    ctl = run_mutants.run_checks(tmp_path, [101, 102], 100, [201], 80, 0)
    assert not ctl["detected"], ctl                       # 대조군은 통과해야 한다
    name, family, fn = next(m for m in run_mutants.MUTANTS if m[0].startswith(mutant))
    with fn():
        res = run_mutants.run_checks(tmp_path, [101, 102, 103, 104], 120, [201, 202], 100, 60)
    assert res["detected"], f"{name} 을 놓쳤다"
