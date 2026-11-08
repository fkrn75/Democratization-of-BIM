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


def all_conflicts(sess: kit.Session, resolve: list | None = None) -> tuple[dict, list]:
    """merge.preview 를 limit=1 쪽 나누기로 끝까지 읽고, limit=200 한 번 읽은 것과 같은지도 돌려준다."""
    args = {"other": "other.dobim", "limit": 200}
    if resolve:
        args["resolve"] = resolve
    whole = sess.run("merge.preview", args)
    assert whole["ok"], whole
    items, cursor, pages = [], None, 0
    while True:
        a = dict(args, limit=1)
        if cursor:
            a["cursor"] = cursor
        r = sess.run("merge.preview", a)
        assert r["ok"], r
        items += r["result"]["items"]
        pages += 1
        cursor = r["page"]["next_cursor"]
        if not cursor:
            break
        assert pages <= 500
    assert [c["key"] for c in items] == [c["key"] for c in whole["result"]["items"]], "쪽 나누기 결과가 한 번에 읽은 것과 다르다"
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


def post_checks(work: Path, name: str, h_before: dict, h_after: dict, rev_after: int, resolve: list | None = None, changed: bool = True) -> dict:
    """합친 프로젝트에 대한 사후 검사. 모두 참이어야 한다."""
    path = work / name
    out = {}
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
