# SPDX-License-Identifier: MPL-2.0
"""5차 검수 회귀: A 키셋 커서, C 위조 커서·배치 항목 입력, D 실측 토큰 상한, E 변경 응답 상한."""
import asyncio
import json
import os
import subprocess
import sys

import pytest

import golden
import kit
from conftest import ROOT
from dobim_core import dispatch, fixture as F, limits

ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}


def test_A_읽는_동안_행이_삭제_삽입_이름변경돼도_키셋_커서는_빠짐_중복이_없다(tmp_path):
    assert golden.check_keyset_paging(tmp_path) == []


def test_C_위조_타입불일치_커서는_SCHEMA_INVALID이고_끝을_지난_커서는_빈_쪽이다_배치_입력도(tmp_path):
    assert golden.check_cursor_and_batch_input(tmp_path) == []


def test_E_큰_변경_응답도_상한_안이고_history_affected로_전체를_이어받는다(tmp_path):
    assert golden.check_mutation_cap(tmp_path) == []


def test_D_실측_토크나이저로_text_structured_합계가_8000토큰_이내(tmp_path):
    pytest.importorskip("tiktoken")
    assert golden.check_token_budget(tmp_path) == []


def test_D_size_check는_실제_MCP_선_위에서_같은_기준으로_통과한다(tmp_path):
    pytest.importorskip("tiktoken")
    out = tmp_path / "sizes.json"
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "size_check.py"), str(out)], capture_output=True, env=ENV,
                       stdin=subprocess.DEVNULL, cwd=tmp_path, timeout=600)
    assert p.returncode == 0, p.stdout.decode("utf-8", "replace")[-600:] + p.stderr.decode("utf-8", "replace")[-300:]
    res = json.loads(out.read_text(encoding="utf-8"))
    assert res["pass"] and res["worst_tokens_any"] <= 8000 and "stdio MCP" in res["transport"]
    assert "Claude 토크나이저로는 재지 못했다" in res["method"]


def test_E_MCP_선_위에서_배치_600건_응답이_상한_안이고_text와_structured가_같다(tmp_path):
    s = kit.Session(tmp_path / "p.dobim")
    s.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    s.close()
    cmds = [{"command": "wall.create", "args": {"level": F.LV1, "axis": [0, i * 10, 6000, i * 10]}} for i in range(600)]

    async def go():
        from mcp import Client, StdioServerParameters
        p = StdioServerParameters(command=sys.executable, args=["-m", "dobim_core", "--workspace", str(tmp_path), "mcp", "serve"],
                                  env=ENV, cwd=str(tmp_path))
        async with Client(p) as c:
            r = await c.call_tool("dobim_execute", {"commands": cmds, "project": "p.dobim"})
            assert not r.is_error and json.loads(r.content[0].text) == r.structured_content
            assert len(r.content[0].text.encode("utf-8")) <= limits.RESPONSE_CAP_BYTES
            env = r.structured_content
            assert env["affected"]["counts"]["created"] == 600 and env["affected"]["truncated"] is True
            q = await c.call_tool("dobim_query", {"command": "history.affected", "args": {"rev": env["rev"], "limit": 1000}, "project": "p.dobim"})
            assert q.structured_content["ok"] and q.structured_content["result"]["counts"]["created"] == 600
    asyncio.run(go())


def test_history_affected_없는_리비전과_기본값(base):
    r = base.run("history.affected", {"rev": 9999})
    assert not r["ok"] and r["error"]["code"] == "NOT_FOUND"
    r = base.run("history.affected", {})                       # 현재 리비전의 변경
    assert r["ok"] and r["result"]["rev"] == base.eng.rev and r["result"]["items"]
    assert base.run("history.affected", {"rev": None})["error"]["code"] == "SCHEMA_INVALID"      # 명시적 null 은 거부


def test_작은_변경의_affected_모양은_counts와_truncated를_포함하고_질의의_기본값도_같다(base):
    r = base.run("wall.move", {"id": F.W1, "dx": 5})
    assert r["affected"]["modified"] == [F.W1] and r["affected"]["counts"] == {"created": 0, "modified": 1, "deleted": 0}
    assert r["affected"]["truncated"] is False and r["page"] is None
    q = base.run("project.info")
    assert q["affected"]["counts"] == {"created": 0, "modified": 0, "deleted": 0} and q["affected"]["truncated"] is False


def test_cross_process_도구는_시드_인자를_받는다():
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "cross_process.py"), "--help"], capture_output=True, env=ENV,
                       stdin=subprocess.DEVNULL, timeout=60)
    out = p.stdout.decode("utf-8", "replace")
    assert p.returncode == 0 and "--seed" in out and "--steps" in out
