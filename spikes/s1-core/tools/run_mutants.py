# SPDX-License-Identifier: MPL-2.0
"""변이 시험: 일부러 버그를 심어도 차등 시험이 잡아내는지 확인해 시험 자체를 증명한다.

각 변이는 모듈 함수를 바꿔치는 패치다(소스는 건드리지 않는다). 변이마다 아래 검사를 순서대로 돌려
하나라도 위반을 내거나 예외가 나면 '탐지'로 센다. 변이 없는 대조군은 모든 검사를 통과해야 한다.
  golden       : 손으로 계산한 기대값 (tools/golden.py)
  differential : 증분 그래프 vs 기준 그래프(캐시 없음) vs 로그 전체 재생 vs 파일 새로 열기 (kit.full_check)
  undo         : 실행 취소·다시 실행 뒤 해시 복원, 실패 커맨드의 원자적 롤백 (kit.undo_property)
  concurrency  : 낡은 expected_rev 거부, 따라잡기 (kit.concurrency_property)
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit  # noqa: E402
import golden  # noqa: E402
from dobim_core import commands, engine, graph, ifc_model, queries  # noqa: E402


# ---------------------------------------------------------------- 변이 정의
@contextmanager
def m_touch_skip_rel():
    orig = graph.Graph.touch

    def touch(self, key):
        if key[0] in ("rel_out", "rel_in"):
            return
        return orig(self, key)

    with mock.patch.object(graph.Graph, "touch", touch):
        yield


@contextmanager
def m_touch_skip_pset():
    orig = graph.Graph.touch

    def touch(self, key):
        if key[0] == "pset":
            return
        return orig(self, key)

    with mock.patch.object(graph.Graph, "touch", touch):
        yield


@contextmanager
def m_early_cutoff_overreach():
    orig = graph.Graph._recompute

    def rec(self, node):
        was_new = node.state == graph.NEW
        old_changed = node.changed
        orig(self, node)
        if not was_new:
            node.changed = old_changed   # 값이 바뀌어도 바뀌었다고 알리지 않는다

    with mock.patch.object(graph.Graph, "_recompute", rec):
        yield


@contextmanager
def m_inverse_rel_not_swapped():
    orig = engine.invert_effect

    def inv(e):
        if e[0] == "rel":
            return list(e)
        return orig(e)

    with mock.patch.object(engine, "invert_effect", inv):
        yield


@contextmanager
def m_inverse_pset_loses_old():
    orig = engine.invert_effect

    def inv(e):
        if e[0] == "pset":
            return ["pset", e[1], e[2], e[3], e[5], None]   # 되돌릴 때 이전 값 대신 지운다
        return orig(e)

    with mock.patch.object(engine, "invert_effect", inv):
        yield


@contextmanager
def m_undo_skips_batch_member():
    orig = engine.Engine.txn_entries

    def txn_entries(self, txn):
        rows = orig(self, txn)
        return rows[1:] if len(rows) > 1 else rows   # 배치의 첫 항목을 되돌리지 않는다

    with mock.patch.object(engine.Engine, "txn_entries", txn_entries):
        yield


@contextmanager
def m_rev_check_skipped():
    orig = engine.Engine.mutate

    def mutate(self, cmds, *, expected_rev=None, dry_run=False, label=None):
        return orig(self, cmds, expected_rev=None, dry_run=dry_run, label=label)

    with mock.patch.object(engine.Engine, "mutate", mutate):
        yield


@contextmanager
def m_no_catch_up():
    with mock.patch.object(engine.Engine, "sync", lambda self: 0):
        yield


@contextmanager
def m_rollback_missing():
    with mock.patch.object(engine.Engine, "_rollback", lambda self, applied: None):
        yield


@contextmanager
def m_projection_ignores_changes():
    def flush(self):
        self.sync()
        self._touch_pending()
        n = 0
        for kind in ("level", "wall", "door"):
            for eid in sorted(self.st.by_kind.get(kind, ())):
                if eid in self._proj_stamp:      # 한 번 투영하면 다시 보지 않는다
                    continue
                spec = self.g.q(queries.ifc_spec, eid)
                self.ifc.project(eid, spec)
                self._proj_stamp[eid] = self.g.nodes[(queries.ifc_spec, eid)].changed
                n += 1
        return n

    with mock.patch.object(engine.Engine, "flush_ifc", flush):
        yield


@contextmanager
def m_delete_forgets_psets():
    def delete_one(ctx, eid):
        ctx.set_material(eid, None)
        for kind in ("joins", "on_level", "hosts"):
            for dst in ctx.rels_out(kind, eid):
                ctx.rel(kind, eid, dst, False)
            for src in ctx.rels_in(kind, eid):
                ctx.rel(kind, src, eid, False)
        ctx.put_el(eid, None)

    with mock.patch.object(commands, "_delete_one", delete_one):
        yield


@contextmanager
def m_untracked_dependency():
    def wall_hosted(g, w):
        return tuple(g.inputs.load(("rel_out", "hosts", w)))   # 의존성을 기록하지 않고 읽는다

    with mock.patch.object(queries, "wall_hosted", wall_hosted):
        yield



@contextmanager
def m_pset_type_sticks():
    """D1 회귀: edit_pset 이 기존 속성 타입으로 값을 강제 변환하게 둔다(1 다음 2.5 가 2 로 저장)."""
    def set_prop(self, eid, pset, prop, value):
        f, e = self.f, self.ents[eid]
        cur = ifc_model.ue.get_pset(e, pset)
        if cur is None:
            if value is None:
                return
            ps = ifc_model.api_pset.add_pset(f, product=e, name=pset)
        else:
            ps = f.by_id(cur["id"])
        ifc_model.api_pset.edit_pset(f, pset=ps, properties={prop: value})
        remain = ifc_model.ue.get_pset(e, pset)
        if remain is not None and not [k for k in remain if k != "id"]:
            ifc_model.api_pset.remove_pset(f, product=e, pset=ps)

    with mock.patch.object(ifc_model.IfcModel, "set_prop", set_prop):
        yield


@contextmanager
def m_door_area_width_squared():
    """검수자 변이: 벽 순면적에서 문 면적을 width*width 로 계산."""
    def wall_row(g, w):
        p = queries.el(g, w)["params"]
        lname, elev = g.q(queries.level_info, g.q(queries.wall_level, w))
        length = g.q(queries.wall_length, w)
        opening = 0.0
        hosted = g.q(queries.wall_hosted, w)
        for d in hosted:
            dp = queries.el(g, d)["params"]
            opening += dp["width"] * dp["width"]
        return (w, p["name"], lname, elev, length, p["height"], p["thickness"], length * p["height"] - opening, len(hosted),
                queries._pset_val(g, w, "Pset_WallCommon", "FireRating"),
                queries._pset_val(g, w, "Pset_WallCommon", "IsExternal"),
                queries.material(g, w), g.q(queries.wall_trim, w)[2])

    with mock.patch.object(queries, "wall_row", wall_row):
        yield


@contextmanager
def m_door_status_strict_boundary():
    """검수자 변이: 문 경계 비교를 <= 에서 < 로."""
    def door_status(g, d):
        w = g.q(queries.door_host, d)
        start, end, _ = g.q(queries.wall_trim, w)
        p = queries.el(g, d)["params"]
        half = p["width"] / 2.0
        return "ok" if start < p["offset"] - half and p["offset"] + half < end else "outside_trimmed_host"

    with mock.patch.object(queries, "door_status", door_status):
        yield


@contextmanager
def m_ctx_set_prop_no_type_check():
    """검수자 변이: Ctx.set_prop 의 '값과 타입이 같을 때만 변경 없음' 판정에서 타입 검사 제거(1 -> 1.0 이 무시됨)."""
    def set_prop(self, eid, pset, prop, value):
        old = self.eng.ifc.get_psets(eid).get(pset, {}).get(prop)
        if old == value:
            return False
        self.emit(["pset", eid, pset, prop, old, value])
        return True

    with mock.patch.object(engine.Ctx, "set_prop", set_prop):
        yield


@contextmanager
def m_redo_branch_not_cleared():
    """새 변경이 redo 가지를 버리지 않는다."""
    def note(self, op, txn):
        if op == "history.undo":
            self.redo_stack.append(self.undo_stack.pop())
        elif op == "history.redo":
            self.undo_stack.append(self.redo_stack.pop())
        elif not self.undo_stack or self.undo_stack[-1] != txn:
            self.undo_stack.append(txn)

    with mock.patch.object(engine.Engine, "_note_entry", note):
        yield

MUTANTS = [
    ("M01 관계 입력 무효화 누락", "증분 무효화 누락", m_touch_skip_rel),
    ("M02 속성셋 입력 무효화 누락", "증분 무효화 누락", m_touch_skip_pset),
    ("M03 조기 차단 과다(변경을 전파하지 않음)", "증분 무효화 누락", m_early_cutoff_overreach),
    ("M04 의존성 기록 없이 읽기(untracked read)", "증분 무효화 누락", m_untracked_dependency),
    ("M05 undo 역연산 오류: 관계 방향 미반전", "undo 역연산 오류", m_inverse_rel_not_swapped),
    ("M06 undo 역연산 오류: 속성셋 이전 값 유실", "undo 역연산 오류", m_inverse_pset_loses_old),
    ("M07 undo 가 배치 첫 항목을 건너뜀", "undo 역연산 오류", m_undo_skips_batch_member),
    ("M08 요소 삭제가 속성셋 복원 정보를 안 남김", "undo 역연산 오류", m_delete_forgets_psets),
    ("M09 실패 시 롤백 누락", "원자성 오류", m_rollback_missing),
    ("M10 expected_rev 검사 생략", "rev 검사 생략", m_rev_check_skipped),
    ("M11 쓰기 전 따라잡기 생략", "동시성 오류", m_no_catch_up),
    ("M12 IFC 투영이 변경을 무시(증분 투영 오류)", "증분 무효화 누락", m_projection_ignores_changes),
    ("M13 속성 값 타입 고착(D1 회귀)", "값 손상", m_pset_type_sticks),
    ("M14 [검수자] 벽 순면적의 문 면적을 width*width 로", "질의 의미 오류", m_door_area_width_squared),
    ("M15 [검수자] door_status 경계 <= 를 < 로", "질의 의미 오류", m_door_status_strict_boundary),
    ("M16 [검수자] Ctx.set_prop 타입 검사 제거", "값 손상", m_ctx_set_prop_no_type_check),
    ("M17 새 변경이 redo 가지를 버리지 않음", "undo/redo 오류", m_redo_branch_not_cleared),
]


# ---------------------------------------------------------------- 검사 실행
def run_checks(work: Path, seeds_diff, n_diff, seeds_undo, n_undo, shrink_budget) -> dict:
    """검사를 순서대로 돌려 첫 탐지를 돌려준다. 탐지가 없으면 detected=False."""
    try:
        gv = golden.check_golden(work)
        if gv:
            return {"detected": True, "by": "golden", "evidence": gv[:2]}
        for s in seeds_diff:
            r = kit.property_run(s, n_diff, work, checkpoint_every=20)
            if not r["ok"]:
                small, evals = kit.shrink(r["items"], work, max_evals=shrink_budget)
                return {"detected": True, "by": "differential", "seed": s, "evidence": r["diffs"][:2],
                        "repro_items": len(small), "repro_from_items": len(r["items"]), "shrink_evals": evals,
                        "repro": kit.describe(small)[:600]}
        for s in seeds_undo:
            r = kit.undo_property(s, n_undo, work)
            if r["violations"]:
                return {"detected": True, "by": "undo", "seed": s, "evidence": r["violations"][:2]}
        v = kit.concurrency_property(work)
        if v:
            return {"detected": True, "by": "concurrency", "evidence": v[:2]}
    except Exception as ex:   # 변이가 내부 불변식을 깨뜨려 예외가 나도 탐지다
        return {"detected": True, "by": "exception", "evidence": [f"{type(ex).__name__}: {str(ex)[:200]}"]}
    return {"detected": False}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--only", default=None, help="변이 번호 접두사(예: M03)")
    ns = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="dobim-mut-"))
    seeds_diff = list(range(101, 101 + ns.seeds))
    seeds_undo = list(range(201, 201 + max(2, ns.seeds // 2)))
    out = {"checks": {"differential_seeds": seeds_diff, "n_per_seed": ns.n, "undo_seeds": seeds_undo}, "mutants": []}
    t = time.time()
    ctl = run_checks(work, seeds_diff, ns.n, seeds_undo, ns.n, 0)
    out["control_no_mutation"] = {"passes_all_checks": not ctl["detected"], "detail": ctl}
    print("대조군:", "통과" if not ctl["detected"] else f"실패! {ctl}", flush=True)
    for name, family, ctxfn in MUTANTS:
        if ns.only and not name.startswith(ns.only):
            continue
        t1 = time.time()
        with ctxfn():
            res = run_checks(work, seeds_diff, ns.n, seeds_undo, ns.n, 150)
        res.update(mutant=name, family=family, sec=round(time.time() - t1, 1))
        out["mutants"].append(res)
        print(f"{name}: {'탐지 (' + res['by'] + ')' if res['detected'] else '놓침!'}  {res.get('evidence', '')}", flush=True)
    out["detected"] = sum(1 for m in out["mutants"] if m["detected"])
    out["total"] = len(out["mutants"])
    out["all_detected"] = out["detected"] == out["total"] and out["control_no_mutation"]["passes_all_checks"]
    out["elapsed_sec"] = round(time.time() - t, 1)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if ns.out:
        Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.out).write_text(text, encoding="utf-8")
    print(f"탐지 {out['detected']}/{out['total']}, 대조군 통과 {out['control_no_mutation']['passes_all_checks']}")
    return 0 if out["all_detected"] else 1


if __name__ == "__main__":
    sys.exit(main())
