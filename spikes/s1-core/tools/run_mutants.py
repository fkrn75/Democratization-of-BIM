# SPDX-License-Identifier: MPL-2.0
"""변이 시험: 일부러 버그를 심어도 차등 시험이 잡아내는지 확인해 시험 자체를 증명한다.

각 변이는 모듈 함수를 바꿔치는 패치다(소스는 건드리지 않는다). 변이마다 아래 검사를 순서대로 돌려
하나라도 위반을 내거나 예외가 나면 '탐지'로 센다. 변이 없는 대조군은 모든 검사를 통과해야 한다.
  golden       : 손으로 계산한 기대값 (tools/golden.py)
  differential : 증분 그래프 vs 기준 그래프(캐시 없음) vs 로그 전체 재생 vs 파일 새로 열기 (kit.full_check)
  undo         : 실행 취소·다시 실행 뒤 해시 복원, 실패 커맨드의 원자적 롤백 (kit.undo_property)
  concurrency  : 낡은 expected_rev 거부, 따라잡기 (kit.concurrency_property)
  protocol     : 응답 크기·잘림·오류 분류 검사 (tools/protocol_checks.py). 골든 함수와 별개라 골든을 꺼도 돈다
  inject_grid  : IFC 호출·SQL 쓰기 실패 주입 후 라이브 엔진 = 디스크 재오픈 (tools/inject_grid.py, 빠른 부분)
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
import inject_grid  # noqa: E402
import protocol_checks  # noqa: E402
from dobim_core import commands, dispatch, engine, graph, ifc_model, limits, queries  # noqa: E402


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


@contextmanager
def m_delete_pset_order_unsorted():
    """실제로 시드 424242 의 10k 시험이 처음 잡은 결함: 삭제 효과가 IFC 속성셋 삽입 순서에 의존(로그 비결정)."""
    def delete_one(ctx, eid):
        for pset, props in ctx.psets(eid).items():
            for prop in sorted(props):
                ctx.set_prop(eid, pset, prop, None)
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
def m_min_trimmed_boundary():
    """경계 변이: 퇴화 판정을 end-start <= MIN_TRIMMED 로(정확히 1.0 이 퇴화가 됨)."""
    with mock.patch.object(queries, "MIN_TRIMMED", 1.0000001):
        yield


@contextmanager
def m_join_max_ext_boundary():
    """경계 변이: 조인 정리 허용 거리를 <= 1000 에서 < 1000 으로."""
    with mock.patch.object(queries, "JOIN_MAX_EXT", 999.9999999):
        yield


@contextmanager
def m_trim_tie_to_end():
    """경계 변이: 시작/끝 배정의 동률을 끝 쪽으로(<= 를 < 로)."""
    def wall_trim(g, w):
        x, y, c, s, length = g.q(queries.wall_frame, w)
        start, end = 0.0, length
        for other in g.q(queries.wall_joins, w):
            p = g.q(queries.join_point, min(w, other), max(w, other))
            if p is None:
                continue
            t = (p[0] - x) * c + (p[1] - y) * s
            if abs(t) < abs(t - length):
                if abs(t) <= queries.JOIN_MAX_EXT:
                    start = t
            elif abs(t - length) <= queries.JOIN_MAX_EXT:
                end = t
        if end - start < queries.MIN_TRIMMED:
            return (0.0, length, "degenerate_join")
        return (start, end, "ok")

    with mock.patch.object(queries, "wall_trim", wall_trim):
        yield


@contextmanager
def m_set_prop_no_restore():
    """N1 회귀: 타입이 바뀌는 쓰기가 속성을 지운 뒤 실패해도 복원하지 않는다."""
    def set_prop(self, eid, pset, prop, value):
        if getattr(self, "_failed", False):   # 엔진의 되돌리기 호출도 복원하지 않는 상태(두 겹 방어를 모두 끈 변이)
            self._failed = False
            return
        ifc_model.check_value(value)
        f, e = self.f, self.ents[eid]
        cur = ifc_model.ue.get_pset(e, pset)
        if cur is None:
            if value is None:
                return
            ps = ifc_model.api_pset.add_pset(f, product=e, name=pset)
            old = None
        else:
            ps = f.by_id(cur["id"])
            old = cur.get(prop)
        try:
            if old is not None and value is not None and type(old) is not type(value):
                ifc_model.api_pset.edit_pset(f, pset=ps, properties={prop: None})
            ifc_model.api_pset.edit_pset(f, pset=ps, properties={prop: value})
        except BaseException:
            self._failed = True
            raise
        finally:
            remain = ifc_model.ue.get_pset(e, pset)
            if remain is not None and not [k for k in remain if k != "id"]:
                ifc_model.api_pset.remove_pset(f, product=e, pset=ps)

    # 엔진은 이제 IFC 호출 실패를 오염 처리하고 로그에서 다시 만든다. 그 방어(_taint)도 함께 끄지 않으면 이 변이는 관측상 같다
    with mock.patch.object(ifc_model.IfcModel, "set_prop", set_prop), mock.patch.object(engine.Engine, "_taint", lambda self, w, ex: None):
        yield


@contextmanager
def m_no_value_validation():
    """N4 회귀: 속성 값 스키마가 NaN/inf/int64 초과/NUL/홑서로게이트를 거르지 않는다(저수준 검사도 없음)."""
    import dataclasses
    from typing import Any
    from dobim_core import registry

    class Lax(commands.A):
        id: commands.Id
        pset: str
        prop: str
        value: Any

    orig = registry.REGISTRY["pset.set"]
    registry.REGISTRY["pset.set"] = dataclasses.replace(orig, args_model=Lax)
    try:
        with mock.patch.object(ifc_model, "check_value", lambda v: None):
            yield
    finally:
        registry.REGISTRY["pset.set"] = orig


@contextmanager
def m_null_value_deletes():
    """N7 회귀: pset.set 이 value=null 을 받아 속성을 지운다(공식 SDK 가 NaN/inf 를 null 로 보내면 조용히 삭제된다)."""
    import dataclasses
    from dobim_core import registry

    class NullOk(commands.BaseModel):   # A 를 상속하면 명시적 null 거부 검사까지 따라온다: 그 검사가 없는 모델
        model_config = commands.ConfigDict(extra="forbid")
        id: commands.Id
        pset: commands.PsetName
        prop: commands.PropName
        value: commands.PropValue | None

    orig = registry.REGISTRY["pset.set"]
    registry.REGISTRY["pset.set"] = dataclasses.replace(orig, args_model=NullOk)
    try:
        yield
    finally:
        registry.REGISTRY["pset.set"] = orig


@contextmanager
def m_restore_failure_swallowed():
    """N8 회귀: 복원 중에도 실패하면 예외를 삼키고 오염 표시도 하지 않는다(이전 동작: except BaseException: pass)."""
    def taint(self, what, ex):
        return None   # 오염 표시를 하지 않는다

    def set_prop(self, eid, pset, prop, value):
        ifc_model.check_value(value)
        f, e = self.f, self.ents[eid]
        cur = ifc_model.ue.get_pset(e, pset)
        if cur is None:
            if value is None:
                return
            ps = ifc_model.api_pset.add_pset(f, product=e, name=pset)
            old = None
        else:
            ps = f.by_id(cur["id"])
            old = cur.get(prop)
        try:
            if old is not None and value is not None and type(old) is not type(value):
                ifc_model.api_pset.edit_pset(f, pset=ps, properties={prop: None})
            ifc_model.api_pset.edit_pset(f, pset=ps, properties={prop: value})
        except BaseException:
            try:
                ifc_model.api_pset.edit_pset(f, pset=ps, properties={prop: None})
                if old is not None:
                    ifc_model.api_pset.edit_pset(f, pset=ps, properties={prop: old})
            except BaseException:
                pass
            raise
        finally:
            remain = ifc_model.ue.get_pset(e, pset)
            if remain is not None and not [k for k in remain if k != "id"]:
                ifc_model.api_pset.remove_pset(f, product=e, pset=ps)

    with mock.patch.object(engine.Engine, "_taint", taint), mock.patch.object(ifc_model.IfcModel, "set_prop", set_prop):
        yield


@contextmanager
def m_taint_without_heal():
    """N8 회귀: 오염 표시는 하지만 로그에서 다시 만들지도, 쓰기를 거부하지도 않는다."""
    with mock.patch.object(engine.Engine, "_heal", lambda self: None):
        yield


@contextmanager
def m_prop_id_allowed():
    """N9 회귀: 속성 이름 "id" 를 거부하지 않는다(쓰기는 ok 인데 조회에 안 보이고 지울 수도 없다)."""
    with mock.patch.object(commands, "_prop_name_ok", lambda s: s):
        yield


def _taint_except(prefix):
    orig = engine.Engine._taint

    def taint(self, what, ex):
        if what.startswith(prefix):
            return None
        return orig(self, what, ex)
    return mock.patch.object(engine.Engine, "_taint", taint)


@contextmanager
def m_el_not_tainted():
    """D1 회귀: 요소(el) 분기가 IFC 호출 실패를 오염으로 표시하지 않는다(상태만 되돌림)."""
    with _taint_except("요소"):
        yield


@contextmanager
def m_mat_not_tainted():
    """D1 회귀: 재료 분기가 IFC 호출 실패를 오염으로 표시하지 않는다(고아·중복 재료)."""
    with _taint_except("재료"):
        yield


@contextmanager
def m_projection_not_tainted():
    """D1 회귀: 투영(flush) 중 실패를 오염으로 표시하지 않는다(고아 개구부)."""
    with _taint_except("투영"):
        yield


@contextmanager
def m_remove_identity_pops_first():
    """D1 회귀: remove_identity 가 엔티티 목록에서 먼저 빼고 IFC 삭제를 한다(실패하면 목록과 파일이 어긋남)."""
    def remove_identity(self, eid):
        f = self.f
        op = self.openings.pop(eid, None)
        if op is not None:
            ifc_model.api_feature.remove_feature(f, feature=op)
        e = self.ents.pop(eid)
        self.container_of.pop(eid, None)
        ifc_model.api_root.remove_product(f, product=e)

    with mock.patch.object(ifc_model.IfcModel, "remove_identity", remove_identity):
        yield


@contextmanager
def m_no_internal_envelope():
    """4차 회귀: 예상 밖 예외를 INTERNAL 봉투로 바꾸지 않고 그대로 올린다(조회 경로가 봉투 없이 샌다)."""
    def reraise(ex):
        raise ex
    with mock.patch.object(dispatch, "internal_error", reraise):
        yield


@contextmanager
def m_numeric_null_allowed():
    """N10 회귀: wall.set/door.set 의 명시적 null 을 바꾸지 않음으로 읽는다."""
    with mock.patch.object(commands, "_optional_numeric", lambda ann: False), \
            mock.patch.object(commands.WallSet, "forbid_null", False), mock.patch.object(commands.DoorSet, "forbid_null", False):
        yield


@contextmanager
def m_export_lock_unconverted():
    """S11 회귀: 스냅숏 복사의 PermissionError 가 FILE_LOCKED 로 바뀌지 않고 샌다."""
    def snap(p, ws):
        import shutil
        d = commands._snap_dir(p, ws)
        if not p.exists():
            return None
        d.mkdir(exist_ok=True)
        n = max([k for k, _ in commands._snapshots(p, ws)], default=0) + 1
        dst = d / f"{p.name}.{n}"
        shutil.copy2(p, dst)
        return f"{commands.SNAP_DIR}/{dst.name}"
    with mock.patch.object(commands, "_snapshot_existing", snap):
        yield


@contextmanager
def m_no_response_cap():
    """S11 회귀: 목록형 응답 크기 상한이 없다(limit 최댓값이 그대로 나간다)."""
    with mock.patch.object(limits, "RESPONSE_CAP_BYTES", 10**9), mock.patch.object(limits, "RESPONSE_CAP_TOKENS", 10**9):
        yield


@contextmanager
def m_cap_cut_silently():
    """S11 회귀: 상한에서 조용히 자른다(truncated 도 next_cursor 도 없다)."""
    with mock.patch.object(commands, "_page", lambda nc, total, trunc: {"next_cursor": None, "total": total, "truncated": False}):
        yield


@contextmanager
def m_offset_cursors():
    """A 회귀: 일람표·관계 목록의 커서가 위치 번호(오프셋)다 - 읽는 동안 앞쪽 행이 지워지거나 끼면 빠짐·중복이 난다."""
    import dataclasses
    from dobim_core import registry

    def schedule_rows(ctx, a):
        rows = ctx.g.q(queries.schedule_sorted, a.kind)
        start = limits.dec_cursor(a.cursor, "int") or 0
        cols = list(queries.ROW_COLUMNS[a.kind])
        items, cut = limits.fit([list(r) for r in rows[start:start + a.limit]], base={"columns": cols})
        nxt = limits.enc_cursor(start + len(items)) if start + len(items) < len(rows) else None
        return {"columns": cols, "items": items, "page": limits.page(nxt, len(rows), cut)}

    def relation_list(ctx, a):
        ctx.row(a.id)
        out = []
        for k in ("on_level", "joins", "hosts"):
            out += [{"kind": k, "src": a.id, "dst": d} for d in ctx.rels_out(k, a.id)]
            out += [{"kind": k, "src": s, "dst": a.id} for s in ctx.rels_in(k, a.id)]
        start = limits.dec_cursor(a.cursor, "int") or 0
        items, cut = limits.fit(out[start:start + a.limit])
        more = start + len(items) < len(out)
        return {"items": items, "page": limits.page(limits.enc_cursor(start + len(items)) if more else None, len(out), cut)}

    origs = {n: registry.REGISTRY[n] for n in ("schedule.rows", "relation.list")}
    registry.REGISTRY["schedule.rows"] = dataclasses.replace(origs["schedule.rows"], func=schedule_rows)
    registry.REGISTRY["relation.list"] = dataclasses.replace(origs["relation.list"], func=relation_list)
    try:
        yield
    finally:
        registry.REGISTRY.update(origs)


@contextmanager
def m_cursor_shape_unchecked():
    """C 회귀: 커서의 모양을 검사하지 않는다(타입이 틀리면 INTERNAL, 음수 커서는 빈 쪽이 끝없이 이어진다)."""
    import base64
    import json as _json

    def dec(c, shape):
        if c is None:
            return None
        try:
            return _json.loads(base64.urlsafe_b64decode(c.encode()))
        except Exception:
            raise limits._bad("해석할 수 없다") from None

    with mock.patch.object(limits, "dec_cursor", dec):
        yield


@contextmanager
def m_old_byte_cap():
    """D 회귀: 상한을 옛 값(바이트/3 추정으로 정한 12,000바이트)으로 두고 토큰 추정은 쓰지 않는다 - 실측 토크나이저로는 8,000토큰을 넘는다."""
    with mock.patch.object(limits, "RESPONSE_CAP_BYTES", 12_000), mock.patch.object(limits, "RESPONSE_CAP_TOKENS", 10**9):
        yield


@contextmanager
def m_mutation_uncapped():
    """E 회귀: 변경 응답(results·affected)에 상한이 없다."""
    def fit_mutation(results, affected, rev, dry_run, warnings=(), must_keep=None, how=None):
        out = dict(affected)
        out["counts"] = {k: len(affected[k]) for k in ("created", "modified", "deleted")}
        out["truncated"] = False
        return results, out, None, list(warnings)

    with mock.patch.object(limits, "fit_mutation", fit_mutation):
        yield


@contextmanager
def m_mutation_counts_shown():
    """E 회귀: affected.counts 가 정확한 건수가 아니라 잘리고 남은 길이다."""
    orig = limits.fit_mutation

    def fit_mutation(results, affected, rev, dry_run, warnings=(), must_keep=None, how=None):
        shown, out, page, warns = orig(results, affected, rev, dry_run, warnings, must_keep, how)
        out["counts"] = {k: len(out[k]) for k in ("created", "modified", "deleted")}
        return shown, out, page, warns

    with mock.patch.object(limits, "fit_mutation", fit_mutation):
        yield


# ---------------------------------------------------------------- 6차 검수 후속 변이 (M42~)
@contextmanager
def m_empty_affected_shared():
    """[1] 빈 affected 가 모든 응답이 공유하는 가변 객체다(한 응답을 고치면 다음 응답이 오염된다)."""
    shared = limits.empty_affected()
    with mock.patch.object(limits, "empty_affected", lambda: shared):
        yield


@contextmanager
def m_warnings_uncapped():
    """[2] 경고를 묶지도 자르지도 않는다(변경 없는 명령 1,000건이면 경고가 1,000줄)."""
    with mock.patch.object(limits, "group_warnings", lambda w: list(w)):
        yield


@contextmanager
def m_fit_fixed_reserve():
    """[3] 목록 자르기가 봉투 뼈대·다음 쪽 커서를 실제 크기로 재지 않고 고정 예비 900바이트로 어림한다(이모지·제어문자 이름이면 상한 초과)."""
    def old_fit(rows, base=None, cursor_of=None, extra_bytes=0, extra_tokens=0.0, trusted=False):
        used = 900 + (limits.jsize(base) if base else 0)
        out = []
        for r in rows:
            sz = limits.jsize(r)
            if out and used + sz > limits.RESPONSE_CAP_BYTES:
                return out, True
            used += sz
            out.append(r)
        return out, False

    with mock.patch.object(limits, "fit", old_fit), mock.patch.object(commands, "_fit", old_fit):
        yield


@contextmanager
def m_no_token_estimate():
    """[4] 토큰 추정을 쓰지 않는다(바이트 상한 6,000 만 본다): 무작위 한글·한자·이모지 이름이면 8,000토큰을 넘는다."""
    with mock.patch.object(limits, "RESPONSE_CAP_TOKENS", 10**9):
        yield


@contextmanager
def m_wide_chars_as_ascii():
    """[4] 비 ASCII 문자를 ASCII 와 같은 가중치(0.8)로 센다: 한글·한자·이모지 이름의 토큰을 과소평가한다."""
    with mock.patch.object(limits, "_wide_weight", lambda ch: 0.8):
        yield


@contextmanager
def m_results_unnumbered():
    """[5] 배치 results 항목에 명령 번호(index)와 seq 가 없다(잘리면 어느 명령의 결과인지 알 수 없다)."""
    with mock.patch.object(dispatch, "_number_results", lambda results, seqs: list(results)):
        yield


@contextmanager
def m_affected_without_seq():
    """[5] history.affected 항목에 seq 가 없다(잘린 results 와 이어 맞출 수 없다)."""
    import dataclasses
    from dobim_core import registry
    orig = registry.REGISTRY["history.affected"]

    def func(ctx, a):
        out = orig.func(ctx, a)
        out["items"] = [{k: v for k, v in it.items() if k != "seq"} for it in out["items"]]
        return out
    registry.REGISTRY["history.affected"] = dataclasses.replace(orig, func=func)
    try:
        yield
    finally:
        registry.REGISTRY["history.affected"] = orig


@contextmanager
def m_undo_ops_repeated():
    """[6] undo/redo 의 ops 가 커맨드 이름을 건수만큼 되풀이한다(요약 아님)."""
    import dataclasses
    from dobim_core import registry
    origs = {n: registry.REGISTRY[n] for n in ("history.undo", "history.redo")}

    def wrap(orig):
        def func(ctx, a):
            out = orig.func(ctx, a)
            out["ops"] = [o["op"] for o in out["ops"] for _ in range(o["count"])]
            return out
        return func
    for n, c in origs.items():
        registry.REGISTRY[n] = dataclasses.replace(c, func=wrap(c))
    try:
        yield
    finally:
        registry.REGISTRY.update(origs)


@contextmanager
def m_failures_droppable():
    """[7] 응답을 줄일 때 실패 결과를 지켜 주지 않는다(중간 실패가 잘려 사라진다)."""
    orig = limits.fit_mutation

    def fit_mutation(results, affected, rev, dry_run, warnings=(), must_keep=None, how=None, extra_bytes=0, extra_tokens=0.0):
        return orig(results, affected, rev, dry_run, warnings, None, how, extra_bytes, extra_tokens)

    with mock.patch.object(limits, "fit_mutation", fit_mutation):
        yield


@contextmanager
def m_script_uncapped():
    """[7] 비원자 script 가 항목마다 봉투 한 벌을 그대로 싣고 상한이 없다(옛 구현)."""
    def run_script(env, items, *, project, dry_run=False):
        results, rev, code = [], None, 0
        for item in items:
            r = dispatch.run(env, item.get("command") or item.get("name") or "", item.get("args"), project=project, dry_run=dry_run)
            results.append(r)
            rev = r.get("rev", rev)
            code = code or (0 if r["ok"] else 1)
            if not r["ok"]:
                break
        return dispatch.ok_env(rev, results) if code == 0 else {"ok": False, "rev": rev, "error": results[-1]["error"], "result": results}

    with mock.patch.object(dispatch, "run_script", run_script):
        yield


@contextmanager
def m_no_input_string_limit():
    """[8] 속성 문자열 입력 길이 상한이 없다(40,000자 값이 history.list·element.get 한 건을 120KB 로 만든다)."""
    with mock.patch.object(limits, "STR_MAX", 10**9):
        yield


@contextmanager
def m_element_get_relations_uncapped():
    """[8] element.get 의 관계 수 상한이 없다(벽이 많은 레벨의 element.get 이 상한을 넘는다)."""
    with mock.patch.object(commands, "RELATIONS_SHOWN", 10**6):
        yield


@contextmanager
def m_no_clip_legacy_values():
    """[8] 옛 로그의 긴 값을 자르지 않는다(단건이 상한을 넘고 truncated 도 알리지 않는다)."""
    with mock.patch.object(limits, "clip_text", lambda s: (s, False)):
        yield


@contextmanager
def m_replay_enforces_limit():
    """[8] 재생에도 길이 상한을 건다(상한이 생기기 전의 옛 로그가 재생에서 SCHEMA_INVALID 로 깨진다)."""
    orig = dispatch.parse_args

    def parse_args(cmd, raw, *, replay=False):
        return orig(cmd, raw, replay=False)

    with mock.patch.object(dispatch, "parse_args", parse_args):
        yield


@contextmanager
def m_sql_write_not_tainted():
    """[9] effect 의 SQL 쓰기가 실패해도 오염으로 표시하지 않는다(메모리와 IFC 만 바뀌고 디스크는 그대로)."""
    def sql(self, what, stmt, params):
        self.conn.execute(stmt, params)

    with mock.patch.object(engine.Engine, "_sql", sql):
        yield


@contextmanager
def m_snapshot_leaks_on_failure():
    """[10] 내보내기가 실패해도 방금 만든 스냅숏을 지우지 않는다(재시도마다 쌓인다)."""
    with mock.patch.object(commands, "_drop_snapshot", lambda p, snap, ws: None):
        yield


@contextmanager
def m_cleanup_raises():
    """[10] 실패 뒤 .tmp 정리가 다른 프로세스에 잡혀 PermissionError 를 내면 INTERNAL 로 샌다."""
    def cleanup(path):
        path.unlink(missing_ok=True)

    with mock.patch.object(commands, "_cleanup", cleanup):
        yield


@contextmanager
def m_all_oserror_locked():
    """[10] 디스크 가득 같은 OSError 전반을 FILE_LOCKED 로 보고한다."""
    with mock.patch.object(commands, "_io_error", lambda p, ex: commands._locked(p)):
        yield


@contextmanager
def m_replace_without_preopen():
    """[10] .tmp 를 먼저 열어 보지 않는다(다른 프로세스가 .tmp 를 잡으면 IFC 라이브러리 예외가 INTERNAL 로 샌다)."""
    def replace(p, write):
        tmp = p.with_name(p.name + ".tmp")
        try:
            write(tmp)
            tmp.replace(p)
        except OSError as ex:
            commands._cleanup(tmp)
            raise commands._io_error(p, ex) from ex
        except BaseException:
            commands._cleanup(tmp)
            raise

    with mock.patch.object(commands, "_replace", replace):
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
    ("M18 삭제 효과가 속성셋 삽입 순서에 의존(10k 시드 424242가 찾은 실제 결함)", "비결정", m_delete_pset_order_unsorted),
    ("M19 MIN_TRIMMED 정확 경계(1.0)가 퇴화로 판정됨", "경계 오류", m_min_trimmed_boundary),
    ("M20 JOIN_MAX_EXT 정확 경계(1000)가 무시됨", "경계 오류", m_join_max_ext_boundary),
    ("M21 조인 시작/끝 배정의 중간 동률을 끝 쪽으로", "경계 오류", m_trim_tie_to_end),
    ("M22 [N1] 속성 쓰기가 지운 뒤 실패해도 복원 안 함", "값 손상", m_set_prop_no_restore),
    ("M23 [N4] 속성 값 검증 없음(NaN/inf/int64 초과/NUL/서로게이트)", "값 손상", m_no_value_validation),
    ("M24 [N7] pset.set 의 value=null 이 속성 삭제(SDK 가 NaN 을 null 로 보내면 조용히 삭제)", "값 손상", m_null_value_deletes),
    ("M25 [N8] 복원 실패를 삼키고 오염 표시도 안 함", "값 손상", m_restore_failure_swallowed),
    ("M26 [N8] 오염 표시만 하고 복구·거부는 안 함", "값 손상", m_taint_without_heal),
    ("M27 [N9] 속성 이름 id 허용(쓰기 ok, 조회에 안 보임)", "값 손상", m_prop_id_allowed),
    ("M28 [D1] 요소(el) 분기가 IFC 호출 실패를 오염으로 표시하지 않음", "값 손상", m_el_not_tainted),
    ("M29 [D1] 재료(mat) 분기가 IFC 호출 실패를 오염으로 표시하지 않음", "값 손상", m_mat_not_tainted),
    ("M30 [D1] 투영(flush) 실패를 오염으로 표시하지 않음", "값 손상", m_projection_not_tainted),
    ("M31 [D1] remove_identity 가 목록에서 먼저 빼고 IFC 를 지움", "값 손상", m_remove_identity_pops_first),
    ("M32 [4차] 예상 밖 예외가 INTERNAL 봉투로 바뀌지 않음", "프로토콜", m_no_internal_envelope),
    ("M33 [N10] wall.set/door.set 의 명시적 null 허용(NaN 필드만 조용히 무시)", "값 손상", m_numeric_null_allowed),
    ("M34 [S11] 잠긴 파일의 스냅숏 복사 PermissionError 가 FILE_LOCKED 로 안 바뀜", "프로토콜", m_export_lock_unconverted),
    ("M35 [S11] 목록 응답 크기 상한 없음", "프로토콜", m_no_response_cap),
    ("M36 [S11] 상한에서 조용히 자름(truncated·next_cursor 없음)", "프로토콜", m_cap_cut_silently),
    ("M37 [A] 일람표·관계 목록 커서가 오프셋(읽는 동안 행이 바뀌면 빠짐·중복)", "프로토콜", m_offset_cursors),
    ("M38 [C] 커서 모양 검사 없음(위조 커서가 INTERNAL·무한 빈 쪽)", "프로토콜", m_cursor_shape_unchecked),
    ("M39 [D] 상한이 옛 12,000바이트(바이트/3 추정 근거, 실측 토큰 초과)", "프로토콜", m_old_byte_cap),
    ("M40 [E] 변경 응답 상한 없음(배치 600건이 36KB)", "프로토콜", m_mutation_uncapped),
    ("M41 [E] affected.counts 가 잘린 길이(정확한 건수 아님)", "프로토콜", m_mutation_counts_shown),
    ("M42 [6차-1] 빈 affected 가 공유 가변 객체", "프로토콜", m_empty_affected_shared),
    ("M43 [6차-2] 경고를 묶지도 자르지도 않음(변경 없는 배치 1,000건)", "프로토콜", m_warnings_uncapped),
    ("M44 [6차-3] 상한 판정이 커서·뼈대를 실제 크기로 재지 않고 예비 900바이트로 어림", "프로토콜", m_fit_fixed_reserve),
    ("M45 [6차-4] 토큰 추정 없음(바이트 상한만): 무작위 한글·이모지가 8,000토큰 초과", "프로토콜", m_no_token_estimate),
    ("M46 [6차-4] 비 ASCII 문자를 ASCII 가중치로 셈", "프로토콜", m_wide_chars_as_ascii),
    ("M47 [6차-5] 배치 results 에 명령 번호·seq 없음", "프로토콜", m_results_unnumbered),
    ("M48 [6차-5] history.affected 항목에 seq 없음", "프로토콜", m_affected_without_seq),
    ("M49 [6차-6] undo/redo ops 가 커맨드 이름을 건수만큼 되풀이", "프로토콜", m_undo_ops_repeated),
    ("M50 [6차-7] 응답을 줄일 때 실패 결과가 잘려 사라짐", "프로토콜", m_failures_droppable),
    ("M51 [6차-7] 비원자 script 응답에 상한이 없음(옛 구현)", "프로토콜", m_script_uncapped),
    ("M52 [6차-8] 속성 문자열 입력 길이 상한 없음", "프로토콜", m_no_input_string_limit),
    ("M53 [6차-8] element.get 관계 수 상한 없음", "프로토콜", m_element_get_relations_uncapped),
    ("M54 [6차-8] 옛 로그의 긴 값을 자르지도 표시하지도 않음", "프로토콜", m_no_clip_legacy_values),
    ("M55 [6차-8] 재생에도 길이 상한을 걸어 옛 로그가 깨짐", "프로토콜", m_replay_enforces_limit),
    ("M56 [6차-9] effect SQL 쓰기 실패를 오염으로 표시하지 않음", "값 손상", m_sql_write_not_tainted),
    ("M57 [6차-10] 내보내기 실패 뒤 스냅숏을 지우지 않음", "프로토콜", m_snapshot_leaks_on_failure),
    ("M58 [6차-10] 실패 뒤 .tmp 정리 실패가 INTERNAL 로 샘", "프로토콜", m_cleanup_raises),
    ("M59 [6차-10] OSError 전반을 FILE_LOCKED 로 보고", "프로토콜", m_all_oserror_locked),
    ("M60 [6차-10] .tmp 를 먼저 열어 보지 않아 잠긴 .tmp 가 INTERNAL 로 샘", "프로토콜", m_replace_without_preopen),
]


# ---------------------------------------------------------------- 검사 실행
def run_checks(work: Path, seeds_diff, n_diff, seeds_undo, n_undo, shrink_budget, use_golden=True) -> dict:
    """검사를 순서대로 돌려 첫 탐지를 돌려준다. 탐지가 없으면 detected=False."""
    try:
        if use_golden:
            gv = (golden.check_golden(work) + golden.check_failure_atomicity(work)
                  + golden.check_delete_and_names(work) + golden.check_restore_failure(work)
                  + golden.check_envelope_and_nulls(work) + golden.check_remove_identity_order(work)
                  + golden.check_response_cap(work) + golden.check_file_lock(work)
                  + golden.check_keyset_paging(work) + golden.check_cursor_and_batch_input(work)
                  + golden.check_mutation_cap(work) + golden.check_token_budget(work))
            if gv:
                return {"detected": True, "by": "golden", "evidence": gv[:2]}
        pv = protocol_checks.run_all(work)   # 골든과 별개: 골든을 끈 상태에서도 돈다
        if pv:
            return {"detected": True, "by": "protocol", "evidence": pv[:2]}
        gviol, _ = inject_grid.check_inject_grid(work, quick=True, ks=(1,))   # 실패 주입: 라이브 = 디스크 (골든과 별개의 기준)
        if gviol:
            return {"detected": True, "by": "inject_grid", "evidence": gviol[:2]}
        dv = kit.directed_delete_order(work)
        if dv:
            return {"detected": True, "by": "differential", "evidence": dv[:2]}
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
            # 골든을 끈 탐지: 골든이 앞서 잡아 차등·undo·동시성 시험의 몫이 가려지는 것을 막기 위해 따로 돌린다
            off = run_checks(work, seeds_diff, ns.n, seeds_undo, ns.n, 150, use_golden=False)
        res["without_golden"] = {k: v for k, v in off.items() if k in ("detected", "by", "evidence", "repro_items", "repro_from_items")}
        res.update(mutant=name, family=family, sec=round(time.time() - t1, 1))
        out["mutants"].append(res)
        print(f"{name}: {'탐지 (' + res['by'] + ')' if res['detected'] else '놓침!'}  {res.get('evidence', '')}", flush=True)
    out["detected"] = sum(1 for m in out["mutants"] if m["detected"])
    out["detected_without_golden"] = sum(1 for m in out["mutants"] if m["without_golden"]["detected"])
    out["missed_without_golden"] = [m["mutant"] for m in out["mutants"] if not m["without_golden"]["detected"]]
    out["total"] = len(out["mutants"])
    out["all_detected"] = out["detected"] == out["total"] and out["control_no_mutation"]["passes_all_checks"]
    out["elapsed_sec"] = round(time.time() - t, 1)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if ns.out:
        Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.out).write_text(text, encoding="utf-8")
    print(f"탐지 {out['detected']}/{out['total']} (골든 끈 상태 {out['detected_without_golden']}/{out['total']}), "
          f"대조군 통과 {out['control_no_mutation']['passes_all_checks']}")
    return 0 if out["all_detected"] else 1


if __name__ == "__main__":
    sys.exit(main())
