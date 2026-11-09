# SPDX-License-Identifier: MPL-2.0
"""S8 시험 도구: 시나리오 실행기. 공통 조상 프로젝트를 만들고, 두 가지(A, B)에 각자 커맨드를 실행한 뒤 양방향으로 병합한다.

한 번의 흐름(flow) = 한 방향(A<-B 또는 B<-A)의 병합 전체:
  미리보기(쪽 나누기 포함) -> 적용 -> (충돌이면) 해결 지시 변형 적용 -> 사후 검사(두 번 적용, 되돌리기, 재오픈, IFC 유효성)
결과는 모두 JSON 으로 환원 가능한 값이라 서로 다른 프로세스·실행 사이에 그대로 비교한다.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "s1-core"
for p in (CORE / "src", CORE / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import kit  # noqa: E402
from dobim_core import fixture as F  # noqa: E402
from dobim_core import merge as M  # noqa: E402
from dobim_core.engine import Engine  # noqa: E402

DIRS = ("A<-B", "B<-A")


def I(prefix: str, n: int) -> str:
    """시험용 22자 id. prefix 1=벽 2=문 3=레벨."""
    return prefix + f"{n:021d}"


# ---------------------------------------------------------------- 가지 만들기
def run_ops(sess: kit.Session, ops: list) -> None:
    """ops 항목: (이름, 인자) | ("@undo",) | ("@redo",) | ("@batch", [(이름, 인자), ...])."""
    for op in ops:
        if op[0] == "@undo":
            r = sess.run("history.undo", {})
        elif op[0] == "@redo":
            r = sess.run("history.redo", {})
        elif op[0] == "@batch":
            r = sess.batch([{"command": n, "args": a} for n, a in op[1]])
        else:
            r = sess.run(op[0], op[1])
        assert r["ok"], (op, r.get("error"))


def build_branches(sc: dict, work: Path, delay: float = 0.0) -> None:
    """work 아래에 anc.dobim(공통 조상), a.dobim, b.dobim 을 만든다."""
    work.mkdir(parents=True, exist_ok=True)
    s = kit.Session(work / "anc.dobim", "anc")
    try:
        for name, args in F.SCRIPT + list(sc.get("base", [])):
            assert s.run(name, args)["ok"], (name, args)
    finally:
        s.close()
    for n in ("a", "b"):
        shutil.copyfile(work / "anc.dobim", work / f"{n}.dobim")
    sa = kit.Session(work / "a.dobim", "alice", create=False)
    try:
        run_ops(sa, sc.get("a", []))
    finally:
        sa.close()
    if delay:
        time.sleep(delay)   # 두 가지의 ts 가 달라지게(결과가 시각에 기대면 드러난다)
    sb = kit.Session(work / "b.dobim", "bob", create=False)
    try:
        run_ops(sb, sc.get("b", []))
    finally:
        sb.close()


# ---------------------------------------------------------------- 관측
def hashes3(eng) -> dict:
    h = eng.hashes()
    return {"state": h["state"], "derived": h["derived"], "ifc": h["ifc"]}


def log_digest(path: Path, from_seq: int = 0) -> str:
    """op_log 의 (seq, 행위자, op, 인자, 역효과) 지문(시각 제외). from_seq 이후만."""
    c = sqlite3.connect(str(path))
    try:
        rows = c.execute("SELECT seq, actor, op, args, inverse FROM op_log WHERE seq > ? ORDER BY seq", (from_seq,)).fetchall()
    finally:
        c.close()
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()


def compact_conflicts(items: list) -> list:
    return [{"key": c["key"], "type": c["type"], "elements": c["elements"], "take": c["take"],
             "ours_ops": [(o["seq"], o["op"]) for o in c["ours"]["ops"]],
             "theirs_ops": [(o["seq"], o["op"]) for o in c["theirs"]["ops"]]} for c in items]


def _read_pages(sess: kit.Session, args: dict, limit: int) -> tuple[dict, list]:
    """merge.preview 를 쪽 나누기로 끝까지 읽는다. (첫 응답, 모든 항목)"""
    items, cursor, pages, first = [], None, 0, None
    while True:
        a = dict(args, limit=limit)
        if cursor:
            a["cursor"] = cursor
        r = sess.run("merge.preview", a)
        assert r["ok"], r
        first = first or r
        items += r["result"]["items"]
        pages += 1
        cursor = r["page"]["next_cursor"]
        if not cursor:
            return first, items
        assert pages <= 500


def all_conflicts(sess: kit.Session, resolve: list | None = None) -> tuple[dict, list]:
    """merge.preview 를 limit=200 으로 끝까지 읽은 것과 limit=1 쪽 나누기로 끝까지 읽은 것이 같은지 확인하고 (첫 응답, 항목)을 돌려준다.
    충돌이 많으면 응답 크기 상한 때문에 한 번에 다 오지 않으므로 둘 다 쪽 나누기로 읽는다."""
    args = {"other": "other.dobim"}
    if resolve:
        args["resolve"] = resolve
    whole, whole_items = _read_pages(sess, args, 200)
    _first, items = _read_pages(sess, args, 1)
    assert [c["key"] for c in items] == [c["key"] for c in whole_items], "쪽 나누기 결과가 한 번에 읽은 것과 다르다"
    return whole, items


def resolve_list(items: list, spec: list, direction: str, swap: bool) -> list:
    """시나리오의 해결 지정(A/B)을 이 방향의 ours/theirs 로 옮긴다. swap 이면 모두 반대쪽을 택한다."""
    out = []
    for c in items:
        take = None
        for r in spec:
            m = r["match"]
            if m.get("type") == c["type"] and all(x in c["elements"] for x in m.get("elements", [])):
                take = r["take"]
                break
        if take is None:
            continue
        if swap:
            take = "B" if take == "A" else "A"
        ours_is_a = direction == "A<-B"
        mine = (take == "A") == ours_is_a
        out.append({"key": c["key"], "take": "ours" if mine else "theirs"})
    return out


def next_auto_ids_ok(path: Path) -> dict:
    """합친 프로젝트의 복사본에서 id 를 주지 않고 레벨·벽·문을 만든다: 병합이 들여온 자동 id 와 겹쳐 CONSTRAINT 가 나면 안 된다."""
    cp = path.with_name(path.stem + "-autoid.dobim")
    shutil.copyfile(path, cp)
    s = kit.Session(cp, "autoid", create=False)
    try:
        # 먼저 기존 레벨에 벽부터 만든다: 병합 직후 첫 자동 id 가 상대의 자동 id 와 가장 겹치기 쉽다
        lvs = sorted(s.eng.st.by_kind.get("level", ()))
        ok = bool(lvs)
        last = None
        for i in range(4):
            if not ok:
                break
            w = s.run("wall.create", {"level": lvs[0], "axis": [0, 50000 + i * 700, 3000, 50000 + i * 700]})
            ok = ok and w["ok"]
            last = w
        if ok:
            ok = s.run("door.place", {"host": last["result"]["id"], "offset": 1500, "width": 900})["ok"] and                 s.run("level.create", {"name": "ZZ-auto", "elevation": 9999})["ok"]
        return {"next_auto_ids_ok": bool(ok)}
    finally:
        s.close()


def post_checks(work: Path, name: str, h_before: dict, h_after: dict, rev_after: int, resolve: list | None = None, changed: bool = True) -> dict:
    """합친 프로젝트에 대한 사후 검사. 모두 참이어야 한다."""
    path = work / name
    out = {}
    out.update(next_auto_ids_ok(path))   # 병합 직후 상태에서(뒤의 undo·재적용이 리비전을 올리기 전에) 확인한다
    s = kit.Session(path, "merger", create=False)
    try:
        eng = s.eng
        # 두 번째 적용은 변경 없음(멱등). 해결 지시가 있으면 같은 지시로 다시 적용해도 마찬가지다
        args = {"other": "other.dobim"}
        if resolve:
            args["resolve"] = resolve
        again = s.run("merge.apply", args)
        out["second_apply_noop"] = bool(again["ok"] and again["rev"] == rev_after and any("변경 없음" in w for w in again["warnings"]))
        if not changed:   # 합칠 것이 없었다: 되돌릴 병합 항목도 없다
            out["intent_consistent"] = eng.intent_consistency() == []
            out["ifc_valid"] = eng.ifc.validate(express=False)["errors"] == 0
            out["noop"] = True
            s.close()
            e2 = Engine(path, "reopen")
            try:
                out["reopen_same"] = hashes3(e2) == h_after
            finally:
                e2.close()
            return out
        # 되돌리기 -> 병합 전 해시, 다시하기 -> 병합 해시
        u = s.run("history.undo", {})
        out["undo_ok"] = bool(u["ok"])
        out["undo_restores_pre_merge"] = hashes3(eng) == h_before
        rd = s.run("history.redo", {})
        out["redo_ok"] = bool(rd["ok"])
        out["redo_restores_merge"] = hashes3(eng) == h_after
        # 되돌린 뒤 다시 합쳐도 같은 결과(실행 횟수 불변)
        s.run("history.undo", {})
        re_ap = s.run("merge.apply", args)
        out["apply_after_undo_same"] = bool(re_ap["ok"]) and hashes3(eng) == h_after
        out["intent_consistent"] = eng.intent_consistency() == []
        v = eng.ifc.validate(express=False)
        out["ifc_valid"] = v["errors"] == 0
    finally:
        s.close()
    # 다시 열어도(로그 접기만으로 상태를 만들어도) 같은 해시
    e2 = Engine(path, "reopen")
    try:
        out["reopen_same"] = hashes3(e2) == h_after
    finally:
        e2.close()
    return out


def flow(sc: dict, base_work: Path, direction: str, mutant_hook=None) -> dict:
    """한 방향의 병합 전체. base_work 의 a.dobim/b.dobim 을 읽고, 방향별 작업 폴더에서 일한다."""
    ours_src, theirs_src = ("a", "b") if direction == "A<-B" else ("b", "a")
    wd = base_work / ("flow-" + direction.replace("<-", "_"))
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    shutil.copyfile(base_work / f"{theirs_src}.dobim", wd / "other.dobim")

    def fresh(name):
        shutil.copyfile(base_work / f"{ours_src}.dobim", wd / name)
        return kit.Session(wd / name, "merger", create=False)

    res: dict = {"direction": direction}
    s = fresh("direct.dobim")
    try:
        h0 = hashes3(s.eng)
        rev0 = s.eng.rev
        whole, items = all_conflicts(s)
        pv = whole["result"]
        res["preview"] = {k: pv[k] for k in ("fork_rev", "ours_rev", "other_rev", "relation", "stats", "conflict_count", "by_type", "plan_digest")}
        res["preview"]["conflicts"] = compact_conflicts(items)
        res["preview_envelope_bytes"] = len(json.dumps(whole, ensure_ascii=False).encode("utf-8"))
        ap = s.run("merge.apply", {"other": "other.dobim"})
        if ap["ok"]:
            res["apply"] = {"ok": True, "affected": ap["affected"], "result": ap["result"], "warnings": ap["warnings"],
                            "hashes": hashes3(s.eng), "log": log_digest(wd / "direct.dobim", rev0)}
            res["apply"]["rev_delta"] = ap["rev"] - rev0
        else:
            res["apply"] = {"ok": False, "code": ap["error"]["code"], "details": ap["error"].get("details", {}).get("total"),
                            "state_untouched": hashes3(s.eng) == h0 and s.eng.rev == rev0 and ap["rev"] == rev0}
    finally:
        s.close()
    if res["apply"]["ok"]:
        res["post"] = post_checks(wd, "direct.dobim", h0, res["apply"]["hashes"], rev0 + res["apply"]["rev_delta"],
                                  changed=res["apply"]["rev_delta"] > 0)
    # 충돌이 있으면 해결 지시 변형을 적용한다
    if not res["apply"]["ok"] and sc.get("resolve"):
        res["variants"] = {}
        for vname, swap in (("spec", False), ("swap", True)):
            s = fresh(f"var-{vname}.dobim")
            try:
                h0v, rev0v = hashes3(s.eng), s.eng.rev
                rl, rounds = [], 0
                for _ in range(4):   # 해결하면 새로 드러나는 의미 충돌이 있을 수 있다: 미리보기를 다시 읽고 이어서 해결한다
                    _w, its = all_conflicts(s, rl)
                    if not its:
                        break
                    more = [x for x in resolve_list(its, sc["resolve"], direction, swap) if x["key"] not in {r["key"] for r in rl}]
                    if not more:
                        break
                    rl += more
                    rounds += 1
                pv2 = s.run("merge.preview", {"other": "other.dobim", "limit": 200, "resolve": rl})
                ap2 = s.run("merge.apply", {"other": "other.dobim", "resolve": rl})
                v = {"resolve_n": len(rl), "rounds": rounds, "preview_ok": pv2["ok"],
                     "preview_conflicts": pv2["result"]["conflict_count"] if pv2["ok"] else None,
                     "preview_digest": pv2["result"]["plan_digest"] if pv2["ok"] else None}
                if ap2["ok"]:
                    v["apply"] = {"ok": True, "hashes": hashes3(s.eng), "affected_counts": ap2["affected"]["counts"],
                                  "log": log_digest(wd / f"var-{vname}.dobim", rev0v), "result": ap2["result"]}
                else:
                    v["apply"] = {"ok": False, "code": ap2["error"]["code"], "details": ap2["error"].get("details")}
                v["facts"] = sc["facts"](s.eng, vname) if ap2["ok"] and sc.get("facts") else None
                rev_after = s.eng.rev
            finally:
                s.close()
            if v["apply"]["ok"]:
                v["post"] = post_checks(wd, f"var-{vname}.dobim", h0v, v["apply"]["hashes"], rev_after, rl, changed=rev_after > rev0v)
            res["variants"][vname] = v
    else:
        s = fresh("facts.dobim")
        try:
            ap3 = s.run("merge.apply", {"other": "other.dobim"})
            res["facts"] = sc["facts"](s.eng, "clean") if ap3["ok"] and sc.get("facts") else None
        finally:
            s.close()
    return res


# ---------------------------------------------------------------- 오라클: 상대의 커맨드를 우리 쪽에 재실행(rebase 식)
def replay_oracle(base_work: Path, direction: str) -> dict:
    """상대 가지의 (실행 취소되지 않은) 트랜잭션을 우리 가지 위에 커맨드 그대로 다시 실행해 본다. 3-way 결과와 비교하는 참고용.
    상대 로그에 조상 쪽 트랜잭션을 되돌린 항목이 있으면 재실행할 수 없어 n/a 로 둔다."""
    ours_src, theirs_src = ("a", "b") if direction == "A<-B" else ("b", "a")
    wd = base_work / ("oracle-" + direction.replace("<-", "_"))
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    shutil.copyfile(base_work / f"{ours_src}.dobim", wd / "o.dobim")
    c = sqlite3.connect(str(base_work / f"{theirs_src}.dobim"))
    try:
        t_rows = c.execute(M.LOG_SQL).fetchall()
    finally:
        c.close()
    c = sqlite3.connect(str(base_work / f"{ours_src}.dobim"))
    try:
        o_rows = c.execute(M.LOG_SQL).fetchall()
    finally:
        c.close()
    n = M.fork_count(o_rows, t_rows)
    tail = t_rows[n:]
    live: list[tuple[str, list]] = []   # (txn, [(op, args)])
    redo: list = []
    tail_txns = {r[1] for r in tail}
    for _seq, txn, _actor, _b, op, args, _inv in tail:
        if op == "history.undo":
            if not live:
                return {"status": "n/a", "why": "조상 쪽 트랜잭션의 실행 취소"}
            redo.append(live.pop())
        elif op == "history.redo":
            live.append(redo.pop())
        elif live and live[-1][0] == txn:
            live[-1][1].append((op, json.loads(args)))
        else:
            live.append((txn, [(op, json.loads(args))]))
            redo.clear()
    del tail_txns
    s = kit.Session(wd / "o.dobim", "bob", create=False)
    try:
        for _txn, cmds in live:
            if len(cmds) == 1:
                r = s.run(cmds[0][0], cmds[0][1])
            else:
                r = s.batch([{"command": o, "args": a} for o, a in cmds])
            if not r["ok"]:
                return {"status": "command_error", "code": r["error"]["code"], "op": cmds[0][0], "path": r["error"].get("path")}
        return {"status": "replayed", "hashes": hashes3(s.eng)}
    finally:
        s.close()


def run_scenario(sc: dict, work: Path, *, delay: float = 0.0, dirs=DIRS, oracle: bool = True) -> dict:
    build_branches(sc, work, delay)
    out = {"id": sc["id"], "flows": {}}
    for d in dirs:
        out["flows"][d] = flow(sc, work, d)
    if oracle:
        out["oracle"] = {d: replay_oracle(work, d) for d in dirs}
        for d in dirs:   # 재실행(rebase 식) 결과가 3-way 병합 결과와 같은가(깨끗하게 합쳐진 경우만 비교한다)
            o, ap = out["oracle"][d], out["flows"][d]["apply"]
            o["equals_merge"] = (o.get("hashes") == ap.get("hashes")) if o["status"] == "replayed" and ap["ok"] else None
    return out


# ---------------------------------------------------------------- 시나리오 밖의 독립 점검 (변이 시험이 이빨을 확인하는 데도 쓴다)
def _sess(path: Path, actor="x") -> kit.Session:
    return kit.Session(path, actor, create=False)


def extra_checks(work: Path) -> dict:
    """시나리오 틀에 안 맞는 계약을 한 번씩 확인한다. 값은 모두 True 여야 한다."""
    work.mkdir(parents=True, exist_ok=True)
    out: dict = {}
    # (1) 공통 조상은 트랜잭션 경계로 물린다
    mk = lambda seq, txn, op: (seq, txn, "x", seq - 1, op, "{}", "[]")  # noqa: E731
    a = [mk(1, "t1", "p"), mk(2, "t2", "q"), mk(3, "t2", "r1")]
    b = [mk(1, "t1", "p"), mk(2, "t2", "q"), mk(3, "t2", "r2")]
    out["fork_txn_boundary"] = M.fork_count(a, b) == 1

    def pair(name, sc):
        d = work / name
        build_branches(sc, d)
        shutil.copyfile(d / "a.dobim", d / "ours.dobim")
        shutil.copyfile(d / "b.dobim", d / "other.dobim")
        return d

    # (2) 같은 상대 재병합: 거짓 충돌 없음 / 취소한 병합은 다시 들여옴
    d = pair("remerge", {"a": [("wall.set", {"id": F.W2, "name": "A-only"})], "b": [("wall.set", {"id": F.W1, "thickness": 250})]})
    s = _sess(d / "ours.dobim")
    try:
        s.run("merge.apply", {"other": "other.dobim"})
        sb = _sess(d / "b.dobim", "bob")
        sb.run("wall.set", {"id": F.W1, "thickness": 300})
        sb.close()
        shutil.copyfile(d / "b.dobim", d / "other.dobim")
        pv = s.run("merge.preview", {"other": "other.dobim"})
        out["remerge_no_false_conflict"] = bool(pv["ok"] and pv["result"]["conflict_count"] == 0)
        ap = s.run("merge.apply", {"other": "other.dobim"})
        out["remerge_applies"] = bool(ap["ok"] and s.eng.st.el[F.W1]["params"]["thickness"] == 300.0)
        out["remerge_idempotent"] = s.run("merge.apply", {"other": "other.dobim"})["warnings"] == ["merge.apply: 변경 없음"]
    finally:
        s.close()
    d = pair("undone", {"a": [("wall.set", {"id": F.W2, "name": "A-only"})], "b": [("wall.set", {"id": F.W1, "thickness": 250})]})
    s = _sess(d / "ours.dobim")
    try:
        s.run("merge.apply", {"other": "other.dobim"})
        s.run("history.undo", {})
        ap = s.run("merge.apply", {"other": "other.dobim"})
        out["undone_merge_reimported"] = bool(ap["ok"] and s.eng.st.el[F.W1]["params"]["thickness"] == 250.0)
    finally:
        s.close()

    # (3) 배치: 다른 변경 뒤의 병합은 USAGE, 상태는 그대로
    d = pair("batch", {"a": [("wall.set", {"id": F.W2, "name": "A-only"})], "b": [("wall.set", {"id": F.W1, "thickness": 250})]})
    s = _sess(d / "ours.dobim")
    try:
        h0 = hashes3(s.eng)
        r = s.batch([{"command": "wall.set", "args": {"id": F.W3, "name": "pre"}}, {"command": "merge.apply", "args": {"other": "other.dobim"}}])
        r2 = s.batch([{"command": "merge.apply", "args": {"other": "other.dobim"}}, {"command": "merge.apply", "args": {"other": "other.dobim"}}])
        out["batch_merge_after_change_is_usage"] = bool(not r["ok"] and r["error"]["code"] == "USAGE" and not r2["ok"]
                                                        and r2["error"]["code"] == "USAGE" and hashes3(s.eng) == h0)
    finally:
        s.close()

    # (4) 조작된 상대 로그는 정의된 오류로 거절하고 아무것도 바꾸지 않는다
    d = pair("tamper", {"a": [("wall.set", {"id": F.W1, "thickness": 250})], "b": [("wall.set", {"id": F.W2, "name": "zz"})]})
    c = sqlite3.connect(str(d / "other.dobim"))
    seq = c.execute("select max(seq) from op_log").fetchone()[0]
    c.execute("insert into op_log(seq,txn_id,actor,base_rev,ts,op,args,inverse) values(?,?,?,?,?,?,?,?)",
              (seq + 1, f"t{seq + 1}", "evil", seq, "2026-01-01T00:00:00", "wall.set", "{}", json.dumps([["pset", F.W1, "DoBIM_Intent", "k", "evil", None]])))
    c.commit()
    c.close()
    s = _sess(d / "ours.dobim")
    try:
        h0, rev0 = hashes3(s.eng), s.eng.rev
        r = s.run("merge.apply", {"other": "other.dobim"})
        out["tampered_log_rejected"] = bool(not r["ok"] and r["error"]["code"] == "IO_ERROR" and hashes3(s.eng) == h0 and s.eng.rev == rev0
                                            and "DoBIM_Intent" not in str(s.eng.ifc.get_psets(F.W1)))
    finally:
        s.close()

    # (5) 상대를 읽어도 상대 폴더에 아무것도 남지 않는다
    d = pair("sidecar", {"a": [("wall.set", {"id": F.W2, "name": "A-only"})], "b": [("wall.set", {"id": F.W1, "thickness": 250})]})
    od = d / "theirs"
    od.mkdir()
    shutil.copyfile(d / "b.dobim", od / "o.dobim")
    before = sorted(p.name for p in od.iterdir())
    s = _sess(d / "ours.dobim")
    try:
        r1 = s.run("merge.preview", {"other": "theirs/o.dobim"})
        r2 = s.run("merge.apply", {"other": "theirs/o.dobim"})
        out["no_sidecar_left_in_other_dir"] = bool(r1["ok"] and r2["ok"] and sorted(p.name for p in od.iterdir()) == before)
    finally:
        s.close()

    # (6) 상대가 속성을 지운 것은 삭제 대 수정이 아니다
    d = pair("delmod_prop", {"a": [("element.delete", {"id": F.D1})],
                             "b": [("pset.delete", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating"})]})
    s = _sess(d / "ours.dobim")
    try:
        pv = s.run("merge.preview", {"other": "other.dobim"})
        out["prop_delete_vs_element_delete_not_conflict"] = bool(pv["ok"] and pv["result"]["conflict_count"] == 0)
    finally:
        s.close()

    # (7) 같은 자리 벽 중복이 아주 많아도 위반 수가 선형이다
    n = 300
    u = {("el", F.LV1, "@"): M.canon({"kind": "level", "ifc_class": "IfcBuildingStorey", "origin": "authored", "family_id": "level.basic"}),
         ("el", F.LV1, "p", "name"): M.canon("L"), ("el", F.LV1, "p", "elevation"): M.canon(0.0)}
    for i in range(n):
        e = "1%021d" % i
        u[("el", e, "@")] = M.canon({"kind": "wall", "ifc_class": "IfcWall", "origin": "authored", "family_id": "wall.basic"})
        for k, v in (("name", "w"), ("axis", [0.0, 0.0, 6000.0, 0.0]), ("thickness", 200.0), ("height", 3000.0)):
            u[("el", e, "p", k)] = M.canon(v)
        u[("rel", "on_level", e, F.LV1)] = "1"
    out["many_duplicate_walls_linear"] = len([v for v in M.violations(M.View(u)) if v["rule"] == "DUPLICATE_WALL"]) == n
    return out
