# SPDX-License-Identifier: MPL-2.0
"""merge.preview / merge.apply 의 계약: 입력 검사, 보수적 정책, 응답 상한, 이력·실행 취소, CLI·MCP 경로."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import kit
import s8lib
from dobim_core import fixture as F, limits, merge as M, registry
from dobim_core.errors import EXIT_CODES, exit_code_of
from s8lib import I
from scenarios import BY_ID


def branches(tmp_path, sid):
    s8lib.build_branches(BY_ID[sid], tmp_path)
    shutil.copyfile(tmp_path / "a.dobim", tmp_path / "ours.dobim")
    shutil.copyfile(tmp_path / "b.dobim", tmp_path / "other.dobim")
    return tmp_path


def sess(tmp_path, name="ours.dobim"):
    return kit.Session(tmp_path / name, "merger", create=False)


def test_레지스트리와_위험_등급():
    cmds = registry.load()
    assert cmds["merge.preview"].kind == "query" and cmds["merge.preview"].risk == "read" and cmds["merge.preview"].idempotent
    assert cmds["merge.apply"].kind == "mutation" and cmds["merge.apply"].risk == "write"
    for n in ("merge.preview", "merge.apply"):
        assert cmds[n].examples
        for ex in cmds[n].examples:
            cmds[n].args_model.model_validate(ex)
        json.dumps(cmds[n].describe())
    assert EXIT_CODES["MERGE_CONFLICT"] == 1


def test_일부만_해결하면_거부되고_남은_충돌을_알린다(tmp_path):
    branches(tmp_path, "s20_multiple_conflicts")
    s = sess(tmp_path)
    try:
        pv = s.run("merge.preview", {"other": "other.dobim"})["result"]
        assert pv["conflict_count"] == 3 and pv["plan_digest"] is None
        first = pv["items"][0]
        h0, rev0 = s.eng.hashes(), s.eng.rev
        r = s.run("merge.apply", {"other": "other.dobim", "resolve": [{"key": first["key"], "take": "ours"}]})
        assert not r["ok"] and r["error"]["code"] == "MERGE_CONFLICT" and r["error"]["details"]["total"] == 2
        assert s.eng.hashes() == h0 and s.eng.rev == rev0 and exit_code_of(r) == 1
        assert limits.jsize(r) <= limits.RESPONSE_CAP_BYTES
        # 전부 해결하면 합쳐진다
        rl = [{"key": c["key"], "take": "theirs"} for c in pv["items"]]
        r = s.run("merge.apply", {"other": "other.dobim", "resolve": rl})
        assert r["ok"] and r["result"]["resolved"]["count"] == 3
    finally:
        s.close()


def test_맞지_않는_해결_키는_무시하고_경고한다(tmp_path):
    branches(tmp_path, "s01_independent")
    s = sess(tmp_path)
    try:
        r = s.run("merge.apply", {"other": "other.dobim", "resolve": [{"key": "SAME_FIELD:nope", "take": "theirs"}]})
        assert r["ok"] and any("무시" in w for w in r["warnings"])
        assert s.run("merge.apply", {"other": "other.dobim"})["warnings"] == ["merge.apply: 변경 없음"]
    finally:
        s.close()


def test_상대_리비전_고정과_낡은_expected_rev(tmp_path):
    branches(tmp_path, "s01_independent")
    s = sess(tmp_path)
    try:
        pv = s.run("merge.preview", {"other": "other.dobim"})["result"]
        r = s.run("merge.apply", {"other": "other.dobim", "other_rev": pv["other_rev"] - 1})
        assert not r["ok"] and r["error"]["code"] == "REV_CONFLICT" and r["error"]["retryable"]
        r = s.run("merge.apply", {"other": "other.dobim", "other_rev": pv["other_rev"]}, expected_rev=s.eng.rev + 5)
        assert not r["ok"] and r["error"]["code"] == "REV_CONFLICT"
        r = s.run("merge.apply", {"other": "other.dobim", "other_rev": pv["other_rev"]}, expected_rev=s.eng.rev)
        assert r["ok"]
        # 로그에는 실제로 합친 상대 리비전이 남는다
        h = s.run("history.list", {"limit": 1})["result"]["items"][0]
        assert h["op"] == "merge.apply" and h["args"]["other_rev"] == pv["other_rev"] and h["args"]["other"] == "other.dobim"
    finally:
        s.close()


@pytest.mark.parametrize("case", ["missing", "outside", "self", "garbage", "other_project"])
def test_입력_검사(tmp_path, case):
    branches(tmp_path, "s01_independent")
    if case == "garbage":
        (tmp_path / "junk.dobim").write_bytes(b"not a database" * 100)
    if case == "other_project":
        kit.Session(tmp_path / "foreign.dobim", "x", guid="5d0b1a50-0000-4a00-8000-0d0b1a5000ff").close()
    other = {"missing": "nope.dobim", "outside": "../x.dobim", "self": "ours.dobim", "garbage": "junk.dobim",
             "other_project": "foreign.dobim"}[case]
    code = {"missing": "NOT_FOUND", "outside": "PATH_DENIED", "self": "CONSTRAINT", "garbage": "IO_ERROR", "other_project": "CONSTRAINT"}[case]
    s = sess(tmp_path)
    try:
        for cmd in ("merge.preview", "merge.apply"):
            h0, rev0 = s.eng.hashes(), s.eng.rev
            r = s.run(cmd, {"other": other})
            assert not r["ok"] and r["error"]["code"] == code, (cmd, r)
            assert s.eng.hashes() == h0 and s.eng.rev == rev0
    finally:
        s.close()


def test_dry_run은_바꾸지_않고_영향만_보여_준다(tmp_path):
    branches(tmp_path, "s01_independent")
    s = sess(tmp_path)
    try:
        h0, rev0 = s.eng.hashes(), s.eng.rev
        r = s.run("merge.apply", {"other": "other.dobim"}, dry_run=True)
        assert r["ok"] and r["dry_run"] and r["affected"]["counts"]["modified"] >= 1
        assert s.eng.hashes() == h0 and s.eng.rev == rev0
        assert s.run("merge.apply", {"other": "other.dobim"})["ok"]
    finally:
        s.close()


def test_병합은_로그_항목_하나이고_영향_요소가_조회된다(tmp_path):
    branches(tmp_path, "s01_independent")
    s = sess(tmp_path)
    try:
        rev0 = s.eng.rev
        r = s.run("merge.apply", {"other": "other.dobim"})
        assert r["ok"] and r["rev"] == rev0 + 1
        aff = s.run("history.affected", {"rev": r["rev"], "limit": 50})["result"]
        assert {i["id"] for i in aff["items"]} >= {F.W2, F.D2}
        h_merged = s.eng.hashes()
        assert s.run("history.undo", {})["ok"] and s.eng.rev == rev0 + 2
        assert s.run("history.redo", {})["ok"]
        assert {k: s.eng.hashes()[k] for k in ("state", "derived", "ifc")} == {k: h_merged[k] for k in ("state", "derived", "ifc")}
    finally:
        s.close()


def test_상대_프로젝트가_열려_쓰는_중이어도_읽는다(tmp_path):
    """상대 파일을 다른 세션이 열어 둔 채(WAL 에 쓰는 중) 읽기 전용으로 합친다. 상대 파일은 바뀌지 않는다."""
    branches(tmp_path, "s01_independent")
    live = kit.Session(tmp_path / "b.dobim", "bob", create=False)
    s = sess(tmp_path)
    try:
        assert live.run("wall.set", {"id": F.W3, "name": "라이브"})["ok"]   # WAL 에 아직 체크포인트되지 않았을 수 있다
        before = (tmp_path / "b.dobim").read_bytes()
        r = s.run("merge.apply", {"other": "b.dobim"})
        assert r["ok"]
        assert s.eng.st.el[F.W3]["params"]["name"] == "라이브"
        assert (tmp_path / "b.dobim").read_bytes() == before   # 상대 파일 본체는 그대로
    finally:
        s.close()
        live.close()


def _conflict_pair(tmp_path, n):
    """같은 벽 n 개의 두께를 두 가지가 서로 다르게 바꾼 n 건 충돌."""
    base = [("wall.create", {"id": I("1", 1000 + i), "level": F.LV1, "axis": [0, 20000 + i * 500, 4000, 20000 + i * 500]}) for i in range(n)]
    sc = {"id": "big", "base": base,
          "a": [("@batch", [("wall.set", {"id": I("1", 1000 + i), "thickness": 250}) for i in range(n)])],
          "b": [("@batch", [("wall.set", {"id": I("1", 1000 + i), "thickness": 300}) for i in range(n)])]}
    s8lib.build_branches(sc, tmp_path)
    shutil.copyfile(tmp_path / "a.dobim", tmp_path / "ours.dobim")
    shutil.copyfile(tmp_path / "b.dobim", tmp_path / "other.dobim")


def test_충돌이_많아도_응답이_상한_안이고_쪽으로_끝까지_읽는다(tmp_path):
    _conflict_pair(tmp_path, 300)
    s = sess(tmp_path)
    try:
        whole = s.run("merge.preview", {"other": "other.dobim", "limit": 200})
        assert whole["ok"] and whole["result"]["conflict_count"] == 300
        assert limits.jsize(whole) <= limits.RESPONSE_CAP_BYTES and limits.est_tokens(whole) <= limits.RESPONSE_CAP_TOKENS
        assert whole["page"]["truncated"] and whole["page"]["next_cursor"]
        keys, cursor = [], None
        while True:
            a = {"other": "other.dobim", "limit": 200}
            if cursor:
                a["cursor"] = cursor
            r = s.run("merge.preview", a)
            assert limits.jsize(r) <= limits.RESPONSE_CAP_BYTES
            keys += [c["key"] for c in r["result"]["items"]]
            cursor = r["page"]["next_cursor"]
            if not cursor:
                break
        assert len(keys) == 300 == len(set(keys)) and keys == sorted(keys)
        ap = s.run("merge.apply", {"other": "other.dobim"})
        assert not ap["ok"] and ap["error"]["code"] == "MERGE_CONFLICT" and ap["error"]["details"]["total"] == 300
        assert limits.jsize(ap) <= limits.RESPONSE_CAP_BYTES and limits.est_tokens(ap) <= limits.RESPONSE_CAP_TOKENS
        # resolve 상한(200)을 넘는 지시는 거절되고, 200 건만 해결하면 100 건이 남는다
        r = s.run("merge.apply", {"other": "other.dobim", "resolve": [{"key": k, "take": "theirs"} for k in keys[:200]]})
        assert not r["ok"] and r["error"]["details"]["total"] == 100
        too_many = s.run("merge.apply", {"other": "other.dobim", "resolve": [{"key": k, "take": "theirs"} for k in keys]})
        assert not too_many["ok"] and too_many["error"]["code"] == "SCHEMA_INVALID"
    finally:
        s.close()


def test_접은_상태는_엔진_상태와_같다(tmp_path):
    """로그를 접어 만든 단위 사전이 엔진의 실제 상태(요소·관계·속성·재료)와 같다: 병합 계획의 전제."""
    for sid in ("s01_independent", "s13_undo_in_branch", "s14_undo_of_ancestor_txn", "s05_host_deleted_vs_door_placed"):
        d = tmp_path / sid
        s8lib.build_branches(BY_ID[sid], d)
        for n in "ab":
            c = sqlite3.connect(str(d / f"{n}.dobim"))
            rows = c.execute(M.LOG_SQL).fetchall()
            c.close()
            units = M.fold(rows)
            e = s8lib.Engine(d / f"{n}.dobim", "x")
            try:
                assert M.rows_of(units) == e.st.el
                assert sorted(u[1:] for u in units if u[0] == "rel") == e.st.relations()
                ps = {(u[1], u[2], u[3]): json.loads(v) for u, v in units.items() if u[0] == "pset"}
                live = {(i, a, b): v for i in e.st.el for a, props in e.ifc.get_psets(i).items() for b, v in props.items()}
                assert {k: str(v) for k, v in ps.items()} == {k: str(v) for k, v in live.items()}
                mats = {u[1]: json.loads(v) for u, v in units.items() if u[0] == "mat"}
                assert mats == {i: m for i in e.st.el if (m := e.ifc.get_material(i)) is not None}
            finally:
                e.close()


def test_공통_조상은_트랜잭션_경계로_물린다():
    mk = lambda seq, txn, op: (seq, txn, "x", seq - 1, op, "{}", "[]")  # noqa: E731
    a = [mk(1, "t1", "p"), mk(2, "t2", "q"), mk(3, "t2", "r1")]
    b = [mk(1, "t1", "p"), mk(2, "t2", "q"), mk(3, "t2", "r2")]
    assert M.fork_count(a, b) == 1   # t2 는 일부만 같아 조상에 넣지 않는다
    assert M.fork_count(a, a) == 3 and M.fork_count(a, a[:1]) == 1 and M.fork_count(a, []) == 0


def test_CLI_경로가_같은_봉투를_낸다(tmp_path):
    branches(tmp_path, "s03_same_field")
    env = {**os.environ, "PYTHONPATH": str(Path(s8lib.CORE) / "src"), "PYTHONUTF8": "1"}

    def cli(*args):
        p = subprocess.run([sys.executable, "-m", "dobim_core", "--workspace", str(tmp_path), "--actor", "cli", *args],
                           capture_output=True, env=env, stdin=subprocess.DEVNULL, cwd=tmp_path, timeout=120)
        return json.loads(p.stdout.decode("utf-8")), p.returncode

    shutil.copyfile(tmp_path / "ours.dobim", tmp_path / "cli.dobim")
    pv, rc = cli("run", "merge.preview", "-p", "cli.dobim", "--json", '{"other":"other.dobim"}')
    s = sess(tmp_path)
    try:
        inproc = s.run("merge.preview", {"other": "other.dobim"})
    finally:
        s.close()
    assert rc == 0 and pv == inproc
    ap, rc = cli("run", "merge.apply", "-p", "cli.dobim", "--json", '{"other":"other.dobim"}')
    assert rc == 1 and ap["error"]["code"] == "MERGE_CONFLICT"
    key = pv["result"]["items"][0]["key"]
    ap, rc = cli("run", "merge.apply", "-p", "cli.dobim", "--json", json.dumps({"other": "other.dobim", "resolve": [{"key": key, "take": "theirs"}]}))
    assert rc == 0 and ap["ok"]


@pytest.mark.slow
def test_MCP_경로가_같은_봉투를_낸다(tmp_path):
    import asyncio
    from parity import McpSide
    branches(tmp_path, "s03_same_field")
    s = sess(tmp_path)
    try:
        inproc = s.run("merge.preview", {"other": "other.dobim"})
    finally:
        s.close()
    shutil.copyfile(tmp_path / "a.dobim", tmp_path / "mcp.dobim")

    async def go():
        async with McpSide(tmp_path) as m:
            e1, err1 = await m.call("merge.preview", {"other": "other.dobim"}, project="mcp.dobim")
            e2, err2 = await m.call("merge.apply", {"other": "other.dobim"}, project="mcp.dobim")
            r1 = await m.client.call_tool("dobim_execute", {"command": "merge.preview", "args": {"other": "other.dobim"}, "project": "mcp.dobim"})
            r2 = await m.client.call_tool("dobim_query", {"command": "merge.apply", "args": {"other": "other.dobim"}, "project": "mcp.dobim"})
            return e1, err1, e2, err2, r1.structured_content["error"]["code"], r2.structured_content["error"]["code"]
    e1, err1, e2, err2, risk1, risk2 = asyncio.run(go())
    assert e1 == inproc and not err1
    assert err2 and e2["error"]["code"] == "MERGE_CONFLICT"
    assert (risk1, risk2) == ("RISK_MISMATCH", "RISK_MISMATCH")   # preview 는 query 도구, apply 는 execute 도구로만
