# SPDX-License-Identifier: MPL-2.0
"""S8 결정성: 같은 입력이 프로세스·PYTHONHASHSEED·실행 순서·시각이 달라도 같은 결과(상태/파생/IFC 해시, 로그 효과, 충돌 보고)를 낸다.
음성 대조: 결정성을 깨는 변이를 심으면 판정기가 잡는다."""
import pytest

import s8check
import s8mutants
from run_scenarios import run_children

QUICK = "s01_independent,s03_same_field,s05_host_deleted_vs_door_placed,s15_pset_concurrent,s20_multiple_conflicts"


def test_별도_프로세스_3회_결정성_일부_시나리오():
    runs, cfg = run_children(3, None, QUICK, workers=3)
    assert len({c["PYTHONHASHSEED"] for c in cfg}) == 3
    assert s8check.check(runs, QUICK.split(",")) == []


@pytest.mark.slow
def test_별도_프로세스_4회_결정성_전체_시나리오():
    runs, _ = run_children(4, None, "", workers=4)
    assert s8check.check(runs) == []


@pytest.mark.slow
@pytest.mark.parametrize("name", list(s8mutants.MUTANTS))
def test_음성_대조_변이를_판정기가_잡는다(name):
    runs, _ = run_children(3, name, "", workers=3)
    assert s8check.check(runs), f"{name} 을 못 잡았다"
