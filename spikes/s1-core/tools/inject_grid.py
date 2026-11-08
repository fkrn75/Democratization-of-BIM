# SPDX-License-Identifier: MPL-2.0
"""IFC 호출과 SQL 쓰기 실패 주입 격자: 어느 시점에 어느 IFC 호출·SQL 쓰기가 실패해도 '라이브 엔진 = 디스크에서 새로 연 엔진'이어야 한다.

시나리오(속성·재료·요소 생성/삭제·undo/redo·dry-run·배치·투영) x 실패시킬 지점(IFC 함수 11종 + SQL 쓰기 5종) x 방식(한 번 / 계속) x 몇 번째 호출부터.
SQL 쓰기는 엔진의 연결을 감싸는 대리 객체로 주입한다(sqlite3.Connection 은 속성을 바꿀 수 없다): 문장이 패턴으로 시작하면 OperationalError.
각 경우에 대해 검사한다.
  1. 예외가 봉투 없이 새지 않는다(실패는 오류 봉투).
  2. 실패하는 동안 오염된 엔진은 정상 응답을 주지 않는다(ENGINE_TAINTED 또는 복구한 뒤 응답).
  3. 주입을 걷고 다음 호출이 지나면 오염 표시가 없고, 상태·파생값·IFC 해시와 undo/redo 스택, 리비전이 디스크와 같다.
  4. 실패한 커맨드는 디스크 상태를 바꾸지 않는다. undo, redo 뒤에도 라이브 = 디스크.
위반 문자열 목록과 요약을 돌려준다(빈 목록 = 통과).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit  # noqa: E402
from dobim_core import fixture as F  # noqa: E402
from dobim_core.engine import Engine  # noqa: E402

import ifcopenshell.api.feature as api_feature  # noqa: E402
import ifcopenshell.api.material as api_material  # noqa: E402
import ifcopenshell.api.pset as api_pset  # noqa: E402
import ifcopenshell.api.root as api_root  # noqa: E402

TARGETS = [(api_pset, "edit_pset"), (api_pset, "add_pset"), (api_pset, "remove_pset"),
           (api_material, "assign_material"), (api_material, "add_material"),
           (api_material, "unassign_material"), (api_material, "remove_material"),
           (api_root, "create_entity"), (api_root, "remove_product"),
           (api_feature, "add_feature"), (api_feature, "remove_feature")]
# SQL 쓰기 지점(엔진이 effect 를 디스크에 쓰는 문장). ("sql", 문장 시작 패턴)
SQL_TARGETS = [("sql", "INSERT OR REPLACE INTO element"), ("sql", "DELETE FROM element"),
               ("sql", "INSERT OR IGNORE INTO relation"), ("sql", "DELETE FROM relation"), ("sql", "INSERT INTO op_log")]
TARGETS = TARGETS + SQL_TARGETS


class _SqlProxy:
    """엔진 연결 대리: 패턴으로 시작하는 문장을 실행하기 전에 hook() 을 부른다(hook 이 예외를 던지면 그 문장은 실행되지 않는다)."""

    def __init__(self, real, pattern, hook):
        self._real, self._pattern, self._hook = real, pattern, hook

    def __getattr__(self, k):
        return getattr(self._real, k)

    def execute(self, sql, *a):
        if sql.lstrip().startswith(self._pattern):
            self._hook()
        return self._real.execute(sql, *a)


def _install(tgt, s, hook_factory):
    """주입 지점에 훅을 건다. hook_factory(이름) -> 인자 없는 함수(IFC 는 *a, **kw 를 받는 함수). 되돌리는 함수를 돌려준다."""
    mod, name = tgt
    if mod == "sql":
        real = s.eng.conn
        s.eng.conn = _SqlProxy(real, name, hook_factory(name))
        return lambda: setattr(s.eng, "conn", real)
    real = getattr(mod, name)
    setattr(mod, name, hook_factory(name, real))
    return lambda: setattr(mod, name, real)


W, D, W3 = F.W1, F.D1, F.W3
Z = {"id": W, "pset": "Pset_Z", "prop": "P"}
NEW_WALL = "1" + "%021d" % 900


def setup(tmp: Path, tag: str) -> kit.Session:
    s = kit.Session(tmp / f"{tag}.dobim")
    for n, a in F.SCRIPT:
        assert s.run(n, a)["ok"]
    for cmd, a in [("pset.set", {**Z, "value": 1}), ("pset.set", {"id": W, "pset": "Pset_Z", "prop": "Q", "value": "a"}),
                   ("pset.set", {"id": W, "pset": "Pset_One", "prop": "O", "value": True}),
                   ("material.set", {"id": W, "name": "Concrete"}), ("material.set", {"id": D, "name": "Timber"}),
                   ("pset.set", {"id": D, "pset": "Pset_Z", "prop": "P", "value": 7})]:
        assert s.run(cmd, a)["ok"], (cmd, a)
    s.eng.hashes()   # 투영을 한 번 끝내 둔다
    return s


def _create_wall(s):
    return s.run("wall.create", {"id": NEW_WALL, "level": F.LV1, "axis": [0, 9000, 3000, 9000], "thickness": 200, "height": 3000})


def _mark(s):
    """여러 단계 시나리오에서 마지막 단계 직전의 리비전을 적어 둔다(마지막 단계가 실패하면 이 리비전이어야 한다)."""
    s.mark = s.eng.rev


def _undo_create(s):
    _create_wall(s)
    _mark(s)
    return s.run("history.undo")


def _undo_delete(s):
    s.run("element.delete", {"id": W})
    _mark(s)
    return s.run("history.undo")


def _redo_delete(s):
    s.run("element.delete", {"id": W})
    s.run("history.undo")
    _mark(s)
    return s.run("history.redo")


def _mat_new(s):
    s.run("material.set", {"id": W, "name": None})
    _mark(s)
    return s.run("material.set", {"id": W, "name": "Steel"})


def _flush_after_change(s):
    s.run("door.set", {"id": D, "width": 1000})
    s.run("wall.move", {"id": W, "dx": 50})
    _mark(s)
    return s.run("model.hash")


def _flush_new_door(s):
    s.run("door.place", {"host": F.W2, "offset": 2000, "width": 900})
    _mark(s)
    return s.run("model.hash")


SCEN = {
    "set_typechange": lambda s: s.run("pset.set", {**Z, "value": 2.5}),
    "set_newpset": lambda s: s.run("pset.set", {"id": W, "pset": "Pset_N", "prop": "X", "value": "x"}),
    "del_one_of_two": lambda s: s.run("pset.delete", Z),
    "del_last_prop": lambda s: s.run("pset.delete", {"id": W, "pset": "Pset_One", "prop": "O"}),
    "batch_move_set_del_mat": lambda s: s.batch([
        {"command": "wall.move", "args": {"id": W, "dx": 100}},
        {"command": "pset.set", "args": {**Z, "value": 3.5}},
        {"command": "pset.delete", "args": {"id": W, "pset": "Pset_One", "prop": "O"}},
        {"command": "material.set", "args": {"id": W, "name": "Steel"}}]),
    "dry_set": lambda s: s.run("pset.set", {**Z, "value": 2.5}, dry_run=True),
    "mat_replace": lambda s: s.run("material.set", {"id": W, "name": "Steel"}),
    "mat_new_from_none": _mat_new,
    "mat_none": lambda s: s.run("material.set", {"id": W, "name": None}),
    "mat_shared": lambda s: s.run("material.set", {"id": D, "name": "Concrete"}),
    "dry_mat": lambda s: s.run("material.set", {"id": W, "name": "Steel"}, dry_run=True),
    "wall_create": _create_wall,
    "undo_wall_create": _undo_create,
    "elem_delete_wall": lambda s: s.run("element.delete", {"id": W}),
    "elem_delete_door": lambda s: s.run("element.delete", {"id": D}),
    "elem_delete_plain_wall": lambda s: s.run("element.delete", {"id": W3}),
    "undo_elem_delete": _undo_delete,
    "redo_elem_delete": _redo_delete,
    "flush_after_change": _flush_after_change,
    "flush_new_door": _flush_new_door,
}
QUICK = ("set_typechange", "mat_replace", "mat_none", "elem_delete_wall", "undo_wall_create", "undo_elem_delete", "flush_new_door")


def _state(eng):
    return {"hashes": dict(eng.hashes()), "undo": list(eng.undo_stack), "redo": list(eng.redo_stack), "head": eng.head}


def _disk(path):
    e = Engine(path, "audit")
    try:
        st = _state(e)
        return st, e.conn.execute("SELECT coalesce(max(seq),0) FROM op_log").fetchone()[0]
    finally:
        e.close()


def _core(h):
    return {k: v for k, v in h.items() if k != "rev"}


def count_calls(tmp: Path, scen: str) -> dict:
    """시나리오가 각 주입 지점을 몇 번 부르는지 센다(실패를 주입하지 않고)."""
    s = setup(tmp, f"cnt-{scen}")
    counts = Counter()
    restores = []

    def counter(name, real=None):
        if real is None:   # SQL: 문장을 세기만 한다
            def hook():
                counts[name] += 1
            return hook

        def wrap(*a, **kw):
            counts[name] += 1
            return real(*a, **kw)
        return wrap
    for tgt in TARGETS:
        restores.append(_install(tgt, s, counter))
    try:
        SCEN[scen](s)
    finally:
        for undo in reversed(restores):
            undo()
    s.close()
    return dict(counts)


def run_case(tmp: Path, scen: str, tgt, mode: str, k: int, n: int) -> dict:
    mod, name = tgt
    s = setup(tmp, f"c{n}")
    eng = s.eng
    s.mark = eng.rev
    calls = [0]

    def trip():
        calls[0] += 1
        if (mode == "once" and calls[0] == k) or (mode == "persist" and calls[0] >= k):
            if mod == "sql":
                raise sqlite3.OperationalError(f"disk I/O error 주입 {name}#{calls[0]}")
            raise RuntimeError(f"주입 {name}#{calls[0]}")

    def flaky_factory(nm, real=None):
        if real is None:
            return trip

        def flaky(*a, **kw):
            trip()
            return real(*a, **kw)
        return flaky

    res = {"scen": scen, "tgt": name, "mode": mode, "k": k, "viol": []}
    v = res["viol"]
    undo_inject = _install(tgt, s, flaky_factory)
    try:
        try:
            r = SCEN[scen](s)
        except BaseException as ex:   # 봉투 없는 예외가 샜다
            r = None
            v.append(f"봉투 없는 예외: {type(ex).__name__}")
        res["ok"] = None if r is None else r["ok"]
        res["code"] = None if r is None else (r.get("error") or {}).get("code")
        res["injected"] = calls[0] >= k
        res["calls"] = calls[0]
        if res["injected"] and mode == "persist":
            for probe in (("wall.move", {"id": F.W2, "dx": 0}), ("project.info", {})):
                try:
                    q = s.run(*probe)
                except BaseException as ex:
                    v.append(f"오염된 엔진의 {probe[0]} 가 봉투 없는 예외: {type(ex).__name__}")
                    continue
                if q["ok"] and eng.tainted is not None:
                    v.append(f"오염 상태인데 {probe[0]} 가 정상 응답")
                if not q["ok"] and q["error"]["code"] not in ("ENGINE_TAINTED", "INTERNAL"):
                    v.append(f"{probe[0]} 가 {q['error']['code']} 로 실패")
    finally:
        undo_inject()
    if not res["injected"]:
        s.close()
        return res
    try:
        q = s.run("project.info")   # 주입을 걷은 뒤 첫 호출: 오염이면 여기서 자동 복구된다
        if not q["ok"] or eng.tainted is not None:
            v.append(f"주입을 걷어도 복구되지 않음: {q.get('error', {}).get('code')} / {eng.tainted}")
            s.close()
            return res
        live = _state(eng)
        disk, mx = _disk(s.path)
        for h in ("state", "derived", "ifc"):
            if live["hashes"][h] != disk["hashes"][h]:
                v.append(f"라이브 {h} 해시 != 디스크")
        if live["undo"] != disk["undo"] or live["redo"] != disk["redo"]:
            v.append("undo/redo 스택이 디스크와 다름")
        if not (live["head"] == disk["head"] == mx):
            v.append("리비전이 디스크와 다름")
        if res["ok"] is False and disk["head"] != s.mark:
            v.append(f"실패한 커맨드가 로그를 늘렸다({s.mark} -> {disk['head']})")
        if disk["undo"]:
            s.run("history.undo")
            l2, d2 = _state(eng), _disk(s.path)[0]
            if l2["hashes"] != d2["hashes"] or l2["undo"] != d2["undo"] or l2["redo"] != d2["redo"]:
                v.append("undo 뒤 라이브 != 디스크")
            s.run("history.redo")
            if _state(eng)["hashes"] != _disk(s.path)[0]["hashes"]:
                v.append("redo 뒤 라이브 != 디스크")
        d = kit.full_check(s, tmp, tag=f"-g{n}")
        if not d["ok"]:
            v.append(f"차등 검사 실패: {d['diffs'][:1]}")
    except BaseException as ex:
        v.append(f"감사 중 예외: {type(ex).__name__}: {str(ex)[:80]}")
    s.close()
    return res


def check_inject_grid(work: Path, quick: bool = False, max_k: int = 6, ks: tuple | None = None) -> tuple[list[str], dict]:
    tmp = Path(tempfile.mkdtemp(dir=Path(work), prefix="grid-"))
    viol, rows, n = [], [], 0
    scens = [s for s in SCEN if (not quick or s in QUICK)]
    ks = ks or ((1, 2) if quick else tuple(range(1, max_k + 1)))
    for scen in scens:
        counts = count_calls(tmp, scen)
        for tgt in TARGETS:
            total = counts.get(tgt[1], 0)
            for mode in ("once", "persist"):
                for k in ks:
                    if k > total:
                        break
                    n += 1
                    r = run_case(tmp, scen, tgt, mode, k, n)
                    rows.append(r)
                    for x in r["viol"]:
                        viol.append(f"{scen}/{tgt[1]}/{mode}/k{k}: {x}")
    inj = [r for r in rows if r.get("injected")]
    summary = {"cases": len(rows), "injected": len(inj), "scenarios": len(scens), "targets": len(TARGETS),
               "violation_count": len(viol), "command_failed": sum(1 for r in inj if r["ok"] is False),
               "command_ok_despite_injection": sum(1 for r in inj if r["ok"]),
               "outcome_codes": dict(Counter(str(r["code"]) for r in inj))}
    return viol, summary | {"rows": rows}


def main():
    import argparse
    import platform
    import time
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=None)
    ns = ap.parse_args()
    t = time.time()
    viol, summ = check_inject_grid(Path(tempfile.mkdtemp(prefix="dobim-grid-")), quick=ns.quick)
    out = {"what": "IFC 호출(edit_pset 등 11종)과 SQL 쓰기(5종)를 시나리오 x 방식(한 번/계속) x k번째 호출부터 실패시킨 뒤, 라이브 엔진 = 디스크 재오픈 "
                   "(해시 3종, undo/redo 스택, 리비전, undo·redo 뒤, 차등 검사)", "elapsed_sec": round(time.time() - t, 1),
           "python": platform.python_version(), "pass": not viol, "violations": viol[:50]} | {k: v for k, v in summ.items() if k != "rows"}
    out["rows"] = [{k: v for k, v in r.items() if k != "viol"} | {"viol": len(r["viol"])} for r in summ["rows"]]
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if ns.out:
        Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.out).write_text(text, encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, ensure_ascii=False, indent=1))
    return 0 if not viol else 1


if __name__ == "__main__":
    sys.exit(main())
