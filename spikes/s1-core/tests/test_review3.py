# SPDX-License-Identifier: MPL-2.0
"""3차 검수 지적 회귀: N7(value=null 이 속성 삭제), N8(복원 중 실패를 삼킴), N9(속성 이름 "id")."""
import asyncio
import json
import os
import sqlite3
import sys

import pytest

import golden
import kit
from conftest import ROOT
from dobim_core import dispatch, fixture as F, registry
from dobim_core.engine import canon
from dobim_core.errors import DobimError

ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}
ARG = {"id": F.W1, "pset": "Pset_Z", "prop": "P"}


def _core(h):
    return {k: v for k, v in h.items() if k != "rev"}


def test_N7_N9_삭제는_pset_delete_뿐이고_id_이름은_거부된다(tmp_path):
    assert golden.check_delete_and_names(tmp_path) == []


def test_N8_복원_실패를_삼키지_않고_로그에서_다시_만들거나_명시_오류로_거부한다(tmp_path):
    assert golden.check_restore_failure(tmp_path) == []
    o = golden.LAST_OUTCOMES                       # 주입이 실제로 오염 경로를 밟았다(시험이 무효가 아니다)
    assert o["실패_오염_거부"] > 0 and o["실패_정상"] > 0 and o["성공"] > 0, o


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), float("-inf")])
def test_N7_null과_NaN은_이미_있는_속성을_지우지_못한다_CLI_경로(base, bad):
    base.run("pset.set", {**ARG, "value": 1})
    before = base.eng.hashes()
    r = base.run("pset.set", {**ARG, "value": bad})
    assert not r["ok"] and r["error"]["code"] == "SCHEMA_INVALID" and r["error"]["path"].startswith("/value")
    assert base.eng.hashes() == before and base.eng.ifc.get_psets(F.W1)["Pset_Z"]["P"] == 1
    # 배치 안에서도 마찬가지로 전체가 거부된다
    r = base.batch([{"command": "wall.move", "args": {"id": F.W1, "dx": 5}}, {"command": "pset.set", "args": {**ARG, "value": bad}}])
    assert not r["ok"] and base.eng.hashes() == before


def test_N9_id_이름은_스키마가_거부하고_조회와_삭제가_안_되는_속성이_생기지_않는다(base):
    before = base.eng.hashes()
    for cmd, extra in (("pset.set", {"value": 3}), ("pset.delete", {})):
        r = base.run(cmd, {"id": F.W1, "pset": "Pset_Z", "prop": "id", **extra})
        assert r["error"]["code"] == "SCHEMA_INVALID" and r["error"]["path"] == "/prop", r
    assert base.eng.hashes() == before
    # pset 이름이 "id" 인 것은 충돌이 없다(속성 맵의 키가 아니다): 왕복과 삭제가 된다
    assert base.run("pset.set", {"id": F.W1, "pset": "id", "prop": "P", "value": 1})["ok"]
    assert base.run("element.get", {"id": F.W1})["result"]["psets"]["id"] == {"P": 1}
    assert base.run("pset.delete", {"id": F.W1, "pset": "id", "prop": "P"})["ok"]
    assert base.eng.hashes() == before | {"rev": base.eng.rev}


def test_스키마_설명에_value는_필수이고_null이_없다_그리고_삭제_커맨드가_검색된다(tmp_path):
    env = dispatch.Env(tmp_path)
    d = dispatch.run(env, "commands.describe", {"name": "pset.set"})["result"]
    sch = d["input_schema"]
    assert "value" in sch["required"] and all(x.get("type") != "null" for x in sch["properties"]["value"]["anyOf"])
    assert "pset.delete" in {i["name"] for i in dispatch.run(env, "commands.search", {"query": "delete"})["result"]["items"]}
    assert registry.load()["pset.delete"].risk == "write"


def test_생성기는_속성_삭제를_pset_delete로_보내고_null_pset_set은_만들지_않는다(tmp_path):
    r = kit.property_run(11, 600, tmp_path)
    assert r["ok"], r["diffs"]
    flat = [(n, a) for it in r["items"] for n, a in ([(it["name"], it["args"])] if it["op"] == "cmd" else it["commands"])]
    assert any(n == "pset.delete" for n, _ in flat)
    assert not any(n == "pset.set" and a.get("value") is None for n, a in flat)


# ---------------------------------------------------------------- 옛 로그(pset.set value=null) 호환
def test_N7_이전_형식의_로그도_열리고_undo_redo_재생이_같다(tmp_path):
    path = tmp_path / "old.dobim"
    s = kit.Session(path)
    for n, a in F.SCRIPT:
        s.run(n, a)
    s.run("pset.set", {**ARG, "value": 1})
    after_set = _core(s.eng.hashes())
    assert s.run("pset.delete", ARG)["ok"]
    after_delete = _core(s.eng.hashes())
    seq = s.eng.conn.execute("SELECT seq FROM op_log WHERE op='pset.delete'").fetchone()[0]
    s.close()
    # 이전 코드가 남겼을 형태로 로그를 바꾼다: op=pset.set, args 에 value=null. 역연산(inverse) 효과는 그대로다.
    c = sqlite3.connect(path)
    args = json.loads(c.execute("SELECT args FROM op_log WHERE seq=?", (seq,)).fetchone()[0])
    c.execute("UPDATE op_log SET op='pset.set', args=? WHERE seq=?", (canon({**args, "value": None}), seq))
    c.commit()
    c.close()

    s2 = kit.Session(path, create=False)                      # 다시 열기는 역연산 효과로 상태를 만든다: args 를 해석하지 않는다
    eng = s2.eng
    assert _core(eng.hashes()) == after_delete and eng.ifc.get_psets(F.W1).get("Pset_Z") is None
    assert s2.run("history.undo")["ok"] and _core(eng.hashes()) == after_set and eng.ifc.get_psets(F.W1)["Pset_Z"]["P"] == 1
    assert s2.run("history.redo")["ok"] and _core(eng.hashes()) == after_delete
    rows = kit.log_rows(eng)
    with pytest.raises(DobimError) as ei:                     # 지금의 pset.set 은 옛 항목을 그대로 받지 않는다(정책)
        dispatch.parse_args(registry.load()["pset.set"], json.loads(rows[seq - 1][4]))
    assert ei.value.code == "SCHEMA_INVALID"
    with pytest.raises(DobimError):
        kit.replay_log(rows, tmp_path / "strict.dobim", eng.project_guid_uuid).close()
    rep = kit.replay_log(rows, tmp_path / "legacy.dobim", eng.project_guid_uuid, legacy=True)   # 변환해 재생하면 같다
    assert [r[5] for r in kit.log_rows(rep)] == [r[5] for r in rows]                              # 역연산 효과가 같다
    assert (rep.state_hash(), rep.derived_hash(), rep.ifc_hash()) == (eng.state_hash(), eng.derived_hash(), eng.ifc_hash())
    rep.close()
    # 로그 행 비교(kit.full_check 의 전체 재생)는 op 이름이 달라 쓰지 않고, 위처럼 해시와 역연산 효과를 비교한다
    s2.close()


# ---------------------------------------------------------------- dry-run 롤백 중 복원 실패
def test_N8_dry_run_되돌리기가_실패해도_조용히_넘어가지_않고_주입을_걷으면_복구된다(base, tmp_path):
    import ifcopenshell.api.pset as api_pset
    eng = base.eng
    base.run("pset.set", {**ARG, "value": 1})
    before = _core(eng.hashes())
    real, calls = api_pset.edit_pset, [0]

    def flaky(f, pset, properties=None, **kw):
        calls[0] += 1
        if calls[0] >= 3:                                   # 1-2: 쓰기(지우고 쓰기), 3 이후: 되돌리기부터 영구 실패
            raise RuntimeError("주입 실패")
        return real(f, pset=pset, properties=properties, **kw)

    api_pset.edit_pset = flaky
    try:
        r = base.run("pset.set", {**ARG, "value": 2.5}, dry_run=True)
        assert not r["ok"] and r["error"]["code"] in ("ENGINE_TAINTED", "INTERNAL"), r
        assert eng.tainted is not None
        r = base.run("element.get", {"id": F.W1})
        assert not r["ok"] and r["error"]["code"] == "ENGINE_TAINTED"      # 틀린 값을 정상 응답으로 내주지 않는다
    finally:
        api_pset.edit_pset = real
    r = base.run("element.get", {"id": F.W1})
    assert r["ok"] and r["result"]["psets"]["Pset_Z"]["P"] == 1 and eng.tainted is None
    assert _core(eng.hashes()) == before
    d = kit.full_check(base, tmp_path)
    assert d["ok"], d["diffs"]


# ---------------------------------------------------------------- 공식 SDK 클라이언트
def _client(ws):
    from mcp import Client, StdioServerParameters
    return Client(StdioServerParameters(command=sys.executable, args=["-m", "dobim_core", "--workspace", str(ws), "mcp", "serve"],
                                        env=ENV, cwd=str(ws)))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None])
def test_N7_공식_SDK로_NaN_inf를_보내도_속성이_남는다(tmp_path, bad):
    path = tmp_path / "p.dobim"
    s = kit.Session(path)
    for n, a in F.SCRIPT:
        s.run(n, a)
    s.run("pset.set", {**ARG, "value": 1})
    before = _core(s.eng.hashes())
    s.close()

    async def go():
        async with _client(tmp_path) as c:
            # 사실 확인: SDK 는 NaN/inf 를 표준 JSON 에 없는 값이라 null 로 바꿔 보낸다 -> 서버는 NaN 과 null 을 구분할 수 없다
            r = await c.call_tool("dobim_execute", {"command": "pset.set", "args": {**ARG, "value": bad}, "project": "p.dobim"})
            assert r.is_error and r.structured_content["error"]["code"] == "SCHEMA_INVALID", r.structured_content
            assert r.structured_content["error"]["path"].startswith("/value")
            r = await c.call_tool("dobim_execute", {"commands": [{"command": "pset.set", "args": {**ARG, "value": bad}}], "project": "p.dobim"})
            assert r.is_error and r.structured_content["error"]["code"] == "SCHEMA_INVALID"
            r = await c.call_tool("dobim_query", {"command": "element.get", "args": {"id": F.W1}, "project": "p.dobim"})
            assert r.structured_content["result"]["psets"]["Pset_Z"]["P"] == 1          # 속성이 남아 있다
            h = (await c.call_tool("dobim_query", {"command": "model.hash", "project": "p.dobim"})).structured_content["result"]
            assert _core(h) == before
            # 양성 대조: 명시적 삭제는 pset.delete 로 된다
            r = await c.call_tool("dobim_execute", {"command": "pset.delete", "args": ARG, "project": "p.dobim"})
            assert r.structured_content["ok"] and r.structured_content["result"] == {"changed": True}
            r = await c.call_tool("dobim_query", {"command": "element.get", "args": {"id": F.W1}, "project": "p.dobim"})
            assert "Pset_Z" not in r.structured_content["result"]["psets"]
    asyncio.run(go())
