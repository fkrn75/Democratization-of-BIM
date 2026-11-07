# SPDX-License-Identifier: MPL-2.0
"""4차 검수·S11 측정 회귀: 조회 경로 INTERNAL 봉투, N10(명시적 null), D1(el·mat 분기 오염), ifc.export 잠금, 응답 크기 상한."""
import asyncio
import json
import os
import sys

import pytest

import golden
import inject_grid
import kit
from conftest import ROOT
from dobim_core import commands, dispatch, fixture as F, limits, mcp_server, registry
from dobim_core.engine import canon

ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}


def _core(h):
    return {k: v for k, v in h.items() if k != "rev"}


# ---------------------------------------------------------------- 1. INTERNAL 봉투
def test_조회_배치_엔진열기의_예상밖_예외는_INTERNAL_봉투이고_내부_값이_새지_않는다(tmp_path):
    assert [x for x in golden.check_envelope_and_nulls(tmp_path) if "INTERNAL" in x or "봉투" in x or "예외" in x] == []


def test_MCP에서도_조회_중_예외가_봉투로_온다(tmp_path, capfd):
    s = kit.Session(tmp_path / "p.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    env = dispatch.Env(tmp_path, "mcp")
    env.adopt(s.path, s.eng)
    s.eng.ifc.ents.pop(F.W3)                       # 조회 중 KeyError 를 일으킨다
    srv = mcp_server.build(env)

    async def go():
        from mcp import Client
        async with Client(srv) as c:
            r = await c.call_tool("dobim_query", {"command": "element.get", "args": {"id": F.W3}, "project": "p.dobim"})
            assert r.is_error and r.structured_content["ok"] is False
            assert r.structured_content["error"]["code"] == "INTERNAL"
            assert json.loads(r.content[0].text) == r.structured_content          # text 와 structuredContent 가 같은 봉투
            assert "Traceback" not in r.content[0].text and "File " not in r.content[0].text
    asyncio.run(go())
    assert "KeyError" in capfd.readouterr().err                                   # 트레이스백은 stderr 에 남는다


# ---------------------------------------------------------------- 2. N10
def test_N10_wall_set_door_set의_명시적_null과_레지스트리_전체_점검(tmp_path):
    assert golden.check_envelope_and_nulls(tmp_path) == []


@pytest.mark.parametrize("tool_args", [
    ("wall.set", {"id": F.W1, "thickness": float("nan"), "height": 3100}),
    ("wall.set", {"id": F.W1, "thickness": float("inf")}),
    ("wall.set", {"id": F.W1, "axis": [0.0, 0.0, float("nan"), 5.0]}),
    ("door.set", {"id": F.D1, "width": 1000, "offset": float("nan")}),
    ("door.set", {"id": F.D1, "sill": float("-inf")})])
def test_N10_공식_SDK로_NaN_inf를_보내도_일부_필드만_조용히_무시되지_않는다(tmp_path, tool_args):
    path = tmp_path / "p.dobim"
    s = kit.Session(path)
    for n, a in F.SCRIPT:
        s.run(n, a)
    before = _core(s.eng.hashes())
    s.close()
    name, args = tool_args

    async def go():
        from mcp import Client, StdioServerParameters
        p = StdioServerParameters(command=sys.executable, args=["-m", "dobim_core", "--workspace", str(tmp_path), "mcp", "serve"],
                                  env=ENV, cwd=str(tmp_path))
        async with Client(p) as c:
            r = await c.call_tool("dobim_execute", {"command": name, "args": args, "project": "p.dobim"})
            assert r.is_error and r.structured_content["error"]["code"] == "SCHEMA_INVALID", r.structured_content
            h = (await c.call_tool("dobim_query", {"command": "model.hash", "project": "p.dobim"})).structured_content["result"]
            assert _core(h) == before                                         # 아무것도 바뀌지 않았다
    asyncio.run(go())


def test_N10_로그_인자에는_생략한_선택_필드의_null이_없고_옛_로그는_변환해서_재생된다(base, tmp_path):
    base.run("wall.set", {"id": F.W1, "thickness": 250})
    base.run("level.create", {"name": "ZZ", "elevation": 5})
    rows = kit.log_rows(base.eng)
    for r in rows:
        assert None not in json.loads(r[4]).values() or r[3] == "material.set", r[3:5]
    # 옛 형식 로그(생략한 필드를 null 로 적음)를 만든다: 지금의 스키마는 거부하고 legacy 변환은 같은 결과로 재생한다
    old = [(r[0], r[1], r[2], r[3], canon({**json.loads(r[4]), **({"height": None, "name": None, "axis": None} if r[3] == "wall.set" else {})}), r[5])
           for r in rows]
    with pytest.raises(Exception):
        kit.replay_log(old, tmp_path / "strict.dobim", base.eng.project_guid_uuid).close()
    rep = kit.replay_log(old, tmp_path / "legacy.dobim", base.eng.project_guid_uuid, legacy=True)
    assert (rep.state_hash(), rep.derived_hash(), rep.ifc_hash()) == (base.eng.state_hash(), base.eng.derived_hash(), base.eng.ifc_hash())
    rep.close()


# ---------------------------------------------------------------- 3. el·mat·투영 오염과 복구
def test_D1_remove_identity는_IFC_삭제가_실패하면_목록에서_먼저_빼지_않는다(tmp_path):
    assert golden.check_remove_identity_order(tmp_path) == []


def test_D1_remove_product_실패_뒤_조회와_해시가_정상이고_디스크와_같다(base, tmp_path):
    import ifcopenshell.api.root as api_root
    real = api_root.remove_product
    api_root.remove_product = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("주입"))
    try:
        r = base.run("element.delete", {"id": F.W3})
    finally:
        api_root.remove_product = real
    assert not r["ok"] and r["error"]["code"] == "INTERNAL"
    assert base.eng.tainted is None                                            # 자동으로 로그에서 다시 만들어졌다
    for cmd, a in (("element.get", {"id": F.W3}), ("model.hash", {}), ("model.validate", {}), ("schedule.rows", {"kind": "wall"})):
        assert base.run(cmd, a)["ok"], cmd
    assert base.run("element.delete", {"id": F.W3})["ok"]
    d = kit.full_check(base, tmp_path)
    assert d["ok"], d["diffs"]


def test_IFC_호출_실패_주입_격자_빠른_부분(tmp_path):
    viol, summ = inject_grid.check_inject_grid(tmp_path, quick=True)
    assert viol == [], viol[:5]
    assert summ["injected"] > 50 and summ["command_failed"] > 30


def test_IFC_호출_실패_주입_격자_전체(tmp_path):
    viol, summ = inject_grid.check_inject_grid(tmp_path)
    assert viol == [], viol[:5]
    assert summ["injected"] > 200


def test_생성기는_실제로_있는_속성을_지우는_pset_delete를_충분히_만든다(tmp_path):
    r = kit.property_run(11, 1500, tmp_path)
    assert r["ok"], r["diffs"]
    flat = [(n, a) for it in r["items"] for n, a in ([(it["name"], it["args"])] if it["op"] == "cmd" else it["commands"])]
    assert sum(1 for n, _ in flat if n == "pset.delete") >= 20
    assert r["coverage"]["pset_effective_deletes_in_log"] >= 20


# ---------------------------------------------------------------- 4. ifc.export 잠금
@pytest.mark.skipif(sys.platform != "win32", reason="Windows 파일 공유 잠금")
def test_ifc_export_restore는_공유_없이_열린_대상에서_FILE_LOCKED이고_원본_불변_tmp_없음(tmp_path):
    assert golden.check_file_lock(tmp_path) == []


# ---------------------------------------------------------------- 5. 응답 크기 상한
def test_목록형_커맨드는_응답_크기_상한을_지키고_이어받기로_빠짐_중복_없이_전체를_읽는다(tmp_path):
    assert golden.check_response_cap(tmp_path) == []


def test_상한을_작게_잡아도_commands_search와_snapshots가_같은_규칙을_따른다(base, monkeypatch):
    monkeypatch.setattr(limits, "RESPONSE_CAP_BYTES", 1400)
    names, cursor, pages = [], None, 0
    while True:
        r = base.run("commands.search", {"cursor": cursor} if cursor else {})
        assert r["ok"] and len(json.dumps(r, ensure_ascii=False).encode()) <= 1400 + 300     # 최소 한 건은 담는다
        names += [i["name"] for i in r["result"]["items"]]
        pages += 1
        cursor = r["page"]["next_cursor"]
        if not cursor:
            break
    assert pages > 2 and names == sorted(set(names)) == sorted(registry.load())
    for _ in range(3):
        first = base.run("ifc.export", {"path": "s.ifc"})
        assert base.run("ifc.export", {"path": "s.ifc"}, confirm=first["error"]["details"]["confirm_token"])["ok"]
    monkeypatch.setattr(limits, "RESPONSE_CAP_BYTES", 400)
    got, cursor = [], None
    while True:
        r = base.run("ifc.snapshots", {"path": "s.ifc", **({"cursor": cursor} if cursor else {})})
        got += [i["n"] for i in r["result"]["items"]]
        cursor = r["page"]["next_cursor"]
        if not cursor:
            break
    assert got == [1, 2]
    r = base.run("ifc.snapshots", {"path": "s.ifc", "limit": 1})
    assert r["page"]["next_cursor"] and r["page"]["truncated"] is False         # limit 로 끊은 것은 truncated 가 아니다


def test_MCP_commands_search는_limit_cursor를_받는다(tmp_path):
    env = dispatch.Env(tmp_path, "mcp")
    srv = mcp_server.build(env)

    async def go():
        from mcp import Client
        async with Client(srv) as c:
            r = await c.call_tool("dobim_commands_search", {"limit": 7})
            assert len(r.structured_content["result"]["items"]) == 7 and r.structured_content["page"]["next_cursor"]
            r2 = await c.call_tool("dobim_commands_search", {"limit": 7, "cursor": r.structured_content["page"]["next_cursor"]})
            assert r2.structured_content["result"]["items"][0]["name"] > r.structured_content["result"]["items"][-1]["name"]
    asyncio.run(go())


def test_삭제_효과_결정성을_직접_만든_순서로_확인한다(tmp_path):
    assert kit.directed_delete_order(tmp_path) == []
