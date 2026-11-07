# SPDX-License-Identifier: MPL-2.0
"""합격 기준 5: CLI 와 MCP 프로세스가 번갈아 쓰고 서로의 변경을 실행 취소해도 해시가 일치하는가.

한 프로젝트 파일을 CLI 서브프로세스(호출마다 새로 시작)와 상주 stdio MCP 서버 프로세스가 번갈아 쓴다.
같은 열을 프로세스 밖의 기준 엔진(oracle, 별도 파일)에도 적용해 단계마다 해시를 비교한다.
마지막에 진짜 동시 쓰기 경합(서브프로세스 여러 개 동시 실행)과 expected_rev 경합도 시험한다.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kit  # noqa: E402
import parity  # noqa: E402
from dobim_core import fixture as F  # noqa: E402
from dobim_core.engine import Engine  # noqa: E402


def trip(h: dict) -> tuple:
    return (h["state"], h["derived"], h["ifc"])


async def run(steps: int, seed: int, work: Path, cli_hash_every: int) -> dict:
    ws = work / "ws"
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    cli = parity.CliSide(ws)
    out = {"seed": seed, "steps": 0, "cli_writes": 0, "mcp_writes": 0, "undo_redo_steps": 0, "cross_actor_undos": 0,
           "hash_checks": 0, "hash_mismatches": [], "step_failures": []}
    e, code = cli.call("project.new", {"path": "shared.dobim", "guid": F.GUID})
    assert e["ok"], e
    oracle_sess = kit.Session(work / "oracle.dobim")
    oracle = oracle_sess.eng
    gen = kit.Gen(seed, undo_weight=14, invalid_weight=3, batch_weight=6)
    actors = []

    from mcp import Client, StdioServerParameters
    import os
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "dobim_core", "--workspace", str(ws), "--actor", "mcp", "mcp", "serve"],
                                   env={**os.environ, "PYTHONPATH": parity.SRC, "PYTHONUTF8": "1"})

    async def mcp_item(client, it):
        if it["op"] == "batch":
            r = await client.call_tool("dobim_execute", {"commands": [{"command": a, "args": b} for a, b in it["commands"]],
                                                         "project": "shared.dobim"})
        else:
            name = it["name"]
            tool = "dobim_execute"
            r = await client.call_tool(tool, {"command": name, "args": it["args"], "project": "shared.dobim"})
        return r.structured_content

    def cli_item(it):
        if it["op"] == "batch":
            script = ws / "batch.jsonl"
            script.write_text("\n".join(json.dumps({"command": a, "args": b}) for a, b in it["commands"]), encoding="utf-8")
            import subprocess
            p = subprocess.run([sys.executable, "-m", "dobim_core", "--workspace", str(ws), "--actor", "cli", "script",
                                str(script), "-p", "shared.dobim", "--atomic"], capture_output=True, env=cli.env,
                               stdin=subprocess.DEVNULL, timeout=120)
            return json.loads(p.stdout.decode("utf-8"))
        env, _ = cli.call(it["name"], it["args"], project="shared.dobim")
        return env

    async with Client(params) as mcp:
        async def mcp_hash():
            r = await mcp.call_tool("dobim_query", {"command": "model.hash", "project": "shared.dobim"})
            return r.structured_content["result"]

        for i in range(steps):
            it = gen.next(oracle)
            actor = "cli" if i % 2 == 0 else "mcp"
            before_rev = oracle.rev
            ro = oracle_sess.item(it)
            r = cli_item(it) if actor == "cli" else await mcp_item(mcp, it)
            out["steps"] += 1
            if r["ok"] != ro["ok"] or (r["ok"] and r["rev"] != ro["rev"]):
                out["step_failures"].append({"step": i, "actor": actor, "item": it, "actual": r.get("error") or r["rev"], "oracle": ro.get("error") or ro["rev"]})
                if len(out["step_failures"]) > 3:
                    break
                continue
            if r["ok"] and r["rev"] > before_rev:
                out["cli_writes" if actor == "cli" else "mcp_writes"] += 1
            if it["op"] == "cmd" and it["name"] in ("history.undo", "history.redo") and r["ok"]:
                out["undo_redo_steps"] += 1
                if r["warnings"] and any("다른 행위자" in w for w in r["warnings"]):
                    out["cross_actor_undos"] += 1
            # 해시 비교: MCP(따라잡기 거친 상주 프로세스)는 매 단계, CLI(새로 연 프로세스)는 일부 단계
            exp = trip(oracle.hashes())
            h = await mcp_hash()
            out["hash_checks"] += 1
            if trip(h) != exp:
                out["hash_mismatches"].append({"step": i, "who": "mcp", "item": it})
            if cli_hash_every and i % cli_hash_every == 0:
                hc, _ = cli.call("model.hash", {}, project="shared.dobim")
                out["hash_checks"] += 1
                if trip(hc["result"]) != exp:
                    out["hash_mismatches"].append({"step": i, "who": "cli", "item": it})

        # 교차 실행 취소 시나리오(명시): CLI 가 3번 쓰고 MCP 가 3번 되돌린 뒤 CLI 가 다시 실행
        base = trip(oracle.hashes())
        ids = [kit.mkid("1", 900001 + k) for k in range(3)]
        lv = sorted(oracle.st.by_kind["level"])[0] if oracle.st.by_kind.get("level") else None
        if lv is None:
            r, _ = cli.call("level.create", {"id": kit.mkid("3", 999999), "name": "XL", "elevation": 0}, project="shared.dobim")
            oracle_sess.run("level.create", {"id": kit.mkid("3", 999999), "name": "XL", "elevation": 0})
            lv = kit.mkid("3", 999999)
            base = trip(oracle.hashes())
        mids = []
        for k, wid in enumerate(ids):
            args = {"id": wid, "level": lv, "axis": [k * 1000, 20000, k * 1000 + 800, 20000]}
            mids.append(cli.call("wall.create", args, project="shared.dobim")[0])
            oracle_sess.run("wall.create", args)
        after_writes = trip(oracle.hashes())
        for _ in range(3):
            r = (await mcp.call_tool("dobim_execute", {"command": "history.undo", "project": "shared.dobim"})).structured_content
            oracle_sess.run("history.undo")
            out["undo_redo_steps"] += 1
            out["cross_actor_undos"] += 1 if r["warnings"] else 0
        h = await mcp_hash()
        hc = cli.call("model.hash", {}, project="shared.dobim")[0]["result"]
        out["explicit_cross_undo"] = {"mcp_undid_cli_writes": trip(h) == base and trip(hc) == base,
                                       "differs_from_after_writes": base != after_writes}
        for _ in range(3):
            r = cli.call("history.redo", {}, project="shared.dobim")[0]
            oracle_sess.run("history.redo")
        hm = await mcp_hash()
        out["explicit_cross_undo"]["cli_redo_restores"] = trip(hm) == after_writes

    # 사후 검증: 파일을 새로 연 엔진 = 로그 전체 재생 = 기준 엔진
    final = Engine(ws / "shared.dobim", "verify")
    rep = kit.replay_log(kit.log_rows(final), work / "replay-final.dobim", final.project_guid_uuid)
    out["final"] = {"rev": final.rev, "reopen_equals_replay": not kit.compare_engines(final, rep, "x"),
                    "equals_oracle": trip(final.hashes()) == trip(oracle.hashes()),
                    "actors_in_log": sorted({r[2] for r in kit.log_rows(final)})}
    rep.close()
    final.close()
    oracle_sess.close()
    return out


def race(ws: Path, rounds_free: int, rounds_rev: int) -> dict:
    """진짜 동시 쓰기: 서브프로세스 여러 개를 한꺼번에 시작한다."""
    import subprocess
    cli = parity.CliSide(ws)
    path = ws / "shared.dobim"
    eng = Engine(path, "race")
    lv = sorted(eng.st.by_kind["level"])[0]
    walls = []
    for k in range(6):
        wid = kit.mkid("1", 800001 + k)
        e, _ = cli.call("wall.create", {"id": wid, "level": lv, "axis": [k * 1000, 30000, k * 1000 + 800, 30000]},
                        project="shared.dobim")
        assert e["ok"], e
        walls.append(wid)
    start_rev = Engine(path, "r").rev
    results = {"free": [], "rev": []}
    lock = threading.Lock()

    def worker(idx, barrier, kw, bucket):
        args = {"id": walls[idx % len(walls)], "dx": 10 * (idx + 1)}
        barrier.wait()
        e, code = cli.call("wall.move", args, project="shared.dobim", **kw)
        with lock:
            bucket.append((e["ok"], e.get("error", {}).get("code"), code))

    for _ in range(rounds_free):
        b = threading.Barrier(4)
        ts = [threading.Thread(target=worker, args=(i, b, {}, results["free"])) for i in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    rev_checks = []
    for _ in range(rounds_rev):
        cur = Engine(path, "r")
        rv = cur.rev
        cur.close()
        local = []
        b = threading.Barrier(3)
        ts = [threading.Thread(target=worker, args=(i, b, {"expected_rev": rv}, local)) for i in range(3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        wins = sum(1 for ok, _, _ in local if ok)
        conflicts = sum(1 for ok, c, _ in local if not ok and c == "REV_CONFLICT")
        locked = sum(1 for ok, c, _ in local if not ok and c == "LOCKED")
        rev_checks.append({"winners": wins, "rev_conflict": conflicts, "locked": locked,
                           "exactly_one_winner": wins == 1 and wins + conflicts + locked == 3})
    final = Engine(path, "v")
    rep = kit.replay_log(kit.log_rows(final), ws.parent / "replay-race.dobim", final.project_guid_uuid)
    free_ok = sum(1 for ok, _, _ in results["free"] if ok)
    free_locked = sum(1 for ok, c, _ in results["free"] if not ok and c == "LOCKED")
    res = {"free_writes_attempted": len(results["free"]), "free_ok": free_ok, "free_locked": free_locked,
           "free_other_errors": len(results["free"]) - free_ok - free_locked,
           "log_growth": final.rev - start_rev,
           "rev_rounds": rev_checks, "all_rev_rounds_exactly_one_winner": all(r["exactly_one_winner"] for r in rev_checks),
           "reopen_equals_replay_after_race": not kit.compare_engines(final, rep, "race")}
    # 로그 증가분 = 성공한 쓰기 수(자유 쓰기 + 경합 우승자)
    res["log_growth_equals_successful_writes"] = res["log_growth"] == free_ok + sum(r["winners"] for r in rev_checks)
    rep.close()
    final.close()
    return res


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else None
    steps = 80
    work = Path(tempfile.mkdtemp(prefix="dobim-cross-"))
    res = asyncio.run(run(steps, 20261107, work, cli_hash_every=8))
    res["race"] = race(work / "ws", rounds_free=3, rounds_rev=4)
    res["pass"] = (not res["hash_mismatches"] and not res["step_failures"] and res["final"]["reopen_equals_replay"]
                   and res["final"]["equals_oracle"] and res["cross_actor_undos"] > 0
                   and all(res["explicit_cross_undo"].values())
                   and res["race"]["all_rev_rounds_exactly_one_winner"] and res["race"]["free_other_errors"] == 0
                   and res["race"]["log_growth_equals_successful_writes"] and res["race"]["reopen_equals_replay_after_race"])
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(text, encoding="utf-8")
    print(text)
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
