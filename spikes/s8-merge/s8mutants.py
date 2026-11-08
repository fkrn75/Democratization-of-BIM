# SPDX-License-Identifier: MPL-2.0
"""S8 변이: 병합 구현에 결함을 일부러 심어 판정기(s8check)가 잡는지 본다(음성 대조).

install(name) 은 dobim_core.merge 의 모듈 수준 함수를 바꿔 끼운다. 자식 프로세스에서만 부른다.
결정성을 깨는 변이(M1~M2), 조용한 덮어쓰기(M3~M5), 의미 검사 생략(M6), 로그 해석 오류(M7~M9)로 나뉜다.
"""
from __future__ import annotations

import time

from dobim_core import merge as M

MUTANTS: dict[str, str] = {
    "M1_set_order": "정렬 대신 집합 순회 순서에 의존(PYTHONHASHSEED 에 따라 효과 순서가 달라진다)",
    "M2_time_order": "시각 기반 정렬(실행 시각에 따라 효과 순서가 달라진다)",
    "M3_last_writer_wins": "같은 필드 충돌을 로그 번호가 큰 쪽이 이기게 조용히 해결",
    "M4_ours_wins": "같은 필드 충돌을 항상 우리 쪽이 이기게 조용히 해결(방향 비대칭)",
    "M5_delete_modify_silent": "삭제 대 수정 충돌 보고를 지워 조용히 덮음",
    "M6_no_semantic": "의미 충돌 검사 생략",
    "M7_fold_skips_history": "실행 취소·다시 실행 항목을 접을 때 무시",
    "M8_fork_zero": "공통 조상을 빈 프로젝트로 잘못 잡음",
    "M9_fork_ours_len": "공통 조상을 우리 로그 끝으로 잘못 잡음",
    "M10_semantic_not_introduced": "병합이 새로 만든 위반만이 아니라 부모가 가진 위반도 충돌로 보고",
}


def install(name: str) -> None:
    if name == "M1_set_order":
        orig = M._order
        M._order = lambda items, key=None: list(set(items)) if key is None else orig(items, key)
    elif name == "M2_time_order":
        orig = M._order
        M._order = lambda items, key=None: (sorted(items, key=lambda x: (time.time_ns() * 2654435761 + hash(repr(x))) % 1000003)
                                            if key is None else orig(items, key))
    elif name == "M3_last_writer_wins":
        def force(u, vb, vo, vt, to, tt):
            so, st = (to.get(u) or (0,))[0], (tt.get(u) or (0,))[0]
            return (vo if so >= st else vt,)
        M._force = force
    elif name == "M4_ours_wins":
        M._force = lambda u, vb, vo, vt, to, tt: (vo,)
    elif name == "M5_delete_modify_silent":
        orig = M.build_plan

        def bp(a, b, resolve=None):
            p = orig(a, b, resolve)
            p.conflicts = [c for c in p.conflicts if c["type"] != "DELETE_MODIFY"]
            return p
        M.build_plan = bp
    elif name == "M6_no_semantic":
        M.violations = lambda view: []
    elif name == "M7_fold_skips_history":
        orig = M.fold
        M.fold = lambda rows, units=None, touch=None: orig([r for r in rows if not r[4].startswith("history.")], units, touch)
    elif name == "M8_fork_zero":
        M.fork_count = lambda a, b: 0
    elif name == "M9_fork_ours_len":
        M.fork_count = lambda a, b: len(a)
    elif name == "M10_semantic_not_introduced":
        orig = M.vkey
        M.vkey = lambda v: ("__never__", v["rule"], v["elements"], v["disc"], id(v))
        del orig
    else:
        raise KeyError(name)
