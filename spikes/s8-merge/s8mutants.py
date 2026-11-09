# SPDX-License-Identifier: MPL-2.0
"""S8 변이: 병합 구현에 결함을 일부러 심어 판정기(s8check)가 잡는지 본다(음성 대조).

install(name) 은 dobim_core.merge 의 모듈 수준 함수를 바꿔 끼운다. 자식 프로세스에서만 부른다.
결정성을 깨는 변이(M1~M2), 조용한 덮어쓰기(M3~M5), 의미 검사 생략(M6), 로그 해석 오류(M7~M9)로 나뉜다.
"""
from __future__ import annotations

import inspect
import textwrap
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
    "M11_round_cap_8": "의미 충돌 해결 반복을 8회로 제한(소진 시 마지막 재검사 없음)",
    "M12_fork_no_txn_boundary": "공통 조상을 트랜잭션 경계로 물리지 않고 항목 단위로 자름",
    "M13_new_id_no_probe": "자동 id 가 기존 요소와 겹쳐도 다음 후보를 찾지 않음(병합 뒤 CONSTRAINT 가 반복)",
    "M14_prior_base_off": "같은 상대 재병합에서 앞선 병합을 공통 조상으로 쓰지 않음(거짓 충돌)",
    "M15_id_collision_silent": "id 충돌 보고를 지워 조용히 덮음",
    "M16_take_swapped": "해결 지시의 ours/theirs 를 뒤바꿔 적용",
    "M17_batch_guard_off": "배치 안 병합 위치 검사를 끔(앞 변경 뒤의 병합이 INTERNAL)",
    "M18_log_validation_off": "상대 로그 검증을 끔(예약 속성셋·깨진 효과가 그대로 들어옴)",
    "M19_undone_merge_counts": "취소된 병합도 공통 조상 계산에 씀(취소 뒤 다시 들여오지 못함)",
    "M20_delete_prop_is_modify": "상대가 속성을 지운 것도 삭제 대 수정의 수정으로 셈",
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
        M.fold = lambda rows, units=None, touch=None, seed=None: orig([r for r in rows if not r[4].startswith("history.")], units, touch, seed)   # seed: 통합에서 fold 에 더해진 인자(가져온 IFC 값 시드)도 그대로 넘겨 변이의 의도(이력 항목 무시)만 남긴다
    elif name == "M8_fork_zero":
        M.fork_count = lambda a, b: 0
    elif name == "M9_fork_ours_len":
        M.fork_count = lambda a, b: len(a)
    elif name == "M10_semantic_not_introduced":
        orig = M.vkey
        M.vkey = lambda v: ("__never__", v["rule"], v["elements"], v["disc"], id(v))
        del orig
    elif name == "M11_round_cap_8":
        M._rounds = lambda n: 8
    elif name == "M12_fork_no_txn_boundary":
        def naive_fork(a, b):
            n = 0
            for x, y in zip(a, b):
                if x != y:
                    break
                n += 1
            return n
        M.fork_count = naive_fork
    elif name == "M13_new_id_no_probe":
        import uuid

        import ifcopenshell.guid

        from dobim_core import engine as E

        def first_candidate(self, kind):
            self._n += 1
            u = uuid.uuid5(uuid.UUID(self.eng.project_guid_uuid), f"{kind}:{self.seq}:{self._n}")
            return ifcopenshell.guid.compress(u.hex)
        E.Ctx.new_id = first_candidate
    elif name == "M14_prior_base_off":
        M.prior_base = lambda ours_rows, theirs_rows, n: None
    elif name == "M15_id_collision_silent":
        orig = M.build_plan

        def bp(a, b, resolve=None, project_uuid=""):
            p = orig(a, b, resolve, project_uuid)
            p.conflicts = [c for c in p.conflicts if c["type"] != "ID_COLLISION"]
            return p
        M.build_plan = bp
    elif name == "M16_take_swapped":
        orig = M.build_plan

        def bp(a, b, resolve=None, project_uuid=""):
            sw = [{"key": r["key"], "take": {"ours": "theirs", "theirs": "ours"}.get(r["take"], r["take"])} for r in (resolve or [])]
            return orig(a, b, sw, project_uuid)
        M.build_plan = bp
    elif name == "M17_batch_guard_off":
        M.check_batch_position = lambda ctx: None
    elif name == "M18_log_validation_off":
        M.rows_error = lambda rows: None
    elif name == "M19_undone_merge_counts":
        M.live_txns = lambda rows: {r[1] for r in rows}
    elif name == "M20_delete_prop_is_modify":
        src = inspect.getsource(M.build_plan).replace(" and other.get(u) is not None]", "]")
        ns = dict(M.__dict__)
        exec(compile(textwrap.dedent(src), "<M20>", "exec"), ns)
        M.build_plan = ns["build_plan"]
    else:
        raise KeyError(name)
