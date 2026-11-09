# SPDX-License-Identifier: MPL-2.0
"""CLI·MCP 프로토콜 계층: stdout 위생, 종료 코드, BOM, stdin 닫힘, 공식 SDK 클라이언트."""
import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

from conftest import ROOT
from dobim_core import fixture as F
from dobim_core.errors import EXIT_CODES

ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}


def cli(ws, *args, stdin=subprocess.DEVNULL, input=None):
    p = subprocess.run([sys.executable, "-m", "dobim_core", "--workspace", str(ws), *args], capture_output=True,
                       env=ENV, stdin=stdin if input is None else None, input=input, cwd=ws, timeout=120)
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8", "replace")


@pytest.fixture
def ws(tmp_path):
    code, out, _ = cli(tmp_path, "run", "project.new", "path=p.dobim", f"guid={F.GUID}")
    assert code == 0
    return tmp_path


def test_cli_stdout은_한_줄_json이고_종료_코드는_표를_따른다(ws):
    code, out, _ = cli(ws, "run", "level.create", "-p", "p.dobim", "name=1F", "elevation=0", f"id={F.LV1}")
    assert code == 0 and len(out.splitlines()) == 1 and json.loads(out)["ok"]
    cases = [(["run", "no.such", "-p", "p.dobim"], 2), (["run", "wall.move", "-p", "p.dobim", f"id={F.W1}", "dx=1"], 5),
             (["run", "wall.create", "-p", "p.dobim", f"level={F.LV1}", "axis=[0,0,0,0]"], 1),
             (["run", "level.create", "-p", "p.dobim", "name=2F", "elevation=1", "--expected-rev", "0"], 4),
             (["run", "ifc.export", "-p", "p.dobim", "path=../a.ifc"], 6), (["run", "project.info"], 2),
             (["run", "project.info", "-p", "missing.dobim"], 5), (["bogus"], 2)]
    for args, want in cases:
        code, out, _ = cli(ws, *args)
        env = json.loads(out)
        assert code == want, (args, code, env)
        assert not env["ok"] and env["error"]["code"] in EXIT_CODES


def test_cli는_stdin이_닫혀도_묻지_않고_인자_부족은_실패한다(ws):
    code, out, _ = cli(ws, "run", "level.create", "-p", "p.dobim", "name=1F")          # elevation 누락
    assert code == 2 and json.loads(out)["error"]["path"] == "/elevation"


def test_cli_json_인자는_파일_BOM_stdin을_받는다(ws):
    f = ws / "args.json"
    f.write_bytes(b"\xef\xbb\xbf" + json.dumps({"name": "1F", "elevation": 0, "id": F.LV1}).encode())  # PowerShell 5.1 의 BOM
    code, out, _ = cli(ws, "run", "level.create", "-p", "p.dobim", "--json", f"@{f}")
    assert code == 0, out
    code, out, _ = cli(ws, "run", "level.create", "-p", "p.dobim", "--json", "-", input=json.dumps({"name": "2F", "elevation": 3300}).encode())
    assert code == 0 and json.loads(out)["result"]["id"]


def test_cli_한글과_공백_경로(tmp_path):
    ws = tmp_path / "한글 폴더 test"
    ws.mkdir()
    code, out, _ = cli(ws, "run", "project.new", "path=내 프로젝트.dobim")
    assert code == 0, out
    code, out, _ = cli(ws, "run", "level.create", "-p", "내 프로젝트.dobim", "name=1층", "elevation=0")
    assert code == 0 and json.loads(out)["ok"]
    code, out, _ = cli(ws, "run", "element.query", "-p", "내 프로젝트.dobim")
    assert json.loads(out)["result"]["items"][0]["name"] == "1층"


def test_cli_script_원자적_배치와_dry_run(ws):
    s = ws / "b.jsonl"
    s.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
        {"command": "level.create", "args": {"id": F.LV1, "name": "1F", "elevation": 0}},
        {"command": "wall.create", "args": {"id": F.W1, "level": F.LV1, "axis": [0, 0, 5000, 0]}}]), encoding="utf-8")
    code, out, _ = cli(ws, "script", str(s), "-p", "p.dobim", "--atomic", "--dry-run")
    r = json.loads(out)
    assert code == 0 and r["dry_run"] and r["affected"]["created"] == sorted([F.LV1, F.W1])
    assert json.loads(cli(ws, "run", "project.info", "-p", "p.dobim")[1])["result"]["counts"]["wall"] == 0
    code, out, _ = cli(ws, "script", str(s), "-p", "p.dobim", "--atomic")
    assert code == 0 and json.loads(cli(ws, "run", "project.info", "-p", "p.dobim")[1])["result"]["counts"]["wall"] == 1


NOISE_LAUNCHER = r"""
import dataclasses, os, sys
sys.path.insert(0, {src!r})
from dobim_core import registry
registry.load()
orig = registry.REGISTRY["project.info"]
def noisy(ctx, a):
    print("PRINT 오염")                       # 파이썬 print
    os.write(1, "C-LEVEL 오염\n".encode())     # C++ 라이브러리의 printf 대역
    sys.stdout.write("WRITE 오염\n")
    return orig.func(ctx, a)
registry.REGISTRY["project.info"] = dataclasses.replace(orig, func=noisy)
from dobim_core import cli
sys.exit(cli.main(sys.argv[1:]))
"""


def test_명령_안의_print와_저수준_stdout_쓰기가_결과_json을_오염시키지_않는다_CLI(ws):
    script = ws / "noisy_cli.py"
    script.write_text(NOISE_LAUNCHER.format(src=str(ROOT / "src")), encoding="utf-8")
    p = subprocess.run([sys.executable, str(script), "--workspace", str(ws), "run", "project.info", "-p", "p.dobim"],
                       capture_output=True, env=ENV, stdin=subprocess.DEVNULL, cwd=ws, timeout=60)
    lines = p.stdout.decode("utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["ok"]
    assert "오염" in p.stderr.decode("utf-8", "replace")      # 오염은 stderr 로 갔다


def _client(ws, script=None):
    from mcp import Client, StdioServerParameters
    args = [str(script), "--workspace", str(ws), "mcp", "serve"] if script else ["-m", "dobim_core", "--workspace", str(ws), "mcp", "serve"]
    return Client(StdioServerParameters(command=sys.executable, args=args, env=ENV, cwd=str(ws)))


def test_mcp_도구_8개_어노테이션_text와_structured_동일(ws):
    async def go():
        async with _client(ws) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            assert len(tools) == 8
            ro = {"dobim_commands_search", "dobim_command_describe", "dobim_query", "dobim_job", "dobim_render"}
            for n, t in tools.items():
                assert t.annotations is not None
                assert bool(t.annotations.read_only_hint) == (n in ro), n
            assert tools["dobim_execute_destructive"].annotations.destructive_hint is True
            for t in tools.values():                          # 메타 도구 스키마는 작다 (Codex 5,000바이트 한도 대비)
                assert len(json.dumps(t.input_schema)) < 2500, t.name
            r = await c.call_tool("dobim_query", {"command": "project.info", "project": "p.dobim"})
            assert not r.is_error and json.loads(r.content[0].text) == r.structured_content
            r = await c.call_tool("dobim_execute", {"command": "level.create", "args": {"name": "1F", "elevation": 0},
                                                    "project": "p.dobim", "dry_run": True})
            assert r.structured_content["dry_run"] and not r.is_error
            r = await c.call_tool("dobim_execute", {"command": "level.create", "args": {"name": "1F"}, "project": "p.dobim"})
            assert r.is_error and r.structured_content["error"]["path"] == "/elevation"
            r = await c.call_tool("dobim_project", {"action": "open", "path": "p.dobim"})
            h = r.structured_content["result"]["handle"]
            r = await c.call_tool("dobim_query", {"command": "model.stats", "project": h})
            assert r.structured_content["ok"]                  # 핸들로 프로젝트를 지정한다
            r = await c.call_tool("dobim_job", {"id": "x"})
            assert r.structured_content["error"]["code"] == "NOT_FOUND"
            r = await c.call_tool("dobim_render", {})
            assert r.structured_content["error"]["code"] == "UNSUPPORTED"
    asyncio.run(go())


def test_mcp_서버는_ifcopenshell_없이_시작하고_목록을_응답한다_시작시간(ws):
    async def go():
        t = time.perf_counter()
        async with _client(ws) as c:
            await c.list_tools()
            return time.perf_counter() - t
    dt = asyncio.run(go())
    assert dt < 3.0, dt                                          # 시작 3초 목표(ADR-0011). 수치는 결과 JSON 에 기록


def test_mcp_작업_폴더_밖은_거부된다(ws):
    async def go():
        async with _client(ws) as c:
            r = await c.call_tool("dobim_project", {"action": "create", "path": "../escape.dobim"})
            assert r.structured_content["error"]["code"] == "PATH_DENIED"
            r = await c.call_tool("dobim_project", {"action": "create", "path": str(ws.parent / "abs.dobim")})
            assert r.structured_content["error"]["code"] == "PATH_DENIED"
    asyncio.run(go())


def test_명령_안의_print가_MCP_스트림을_깨뜨리지_않는다(ws):
    script = ws / "noisy_mcp.py"
    script.write_text(NOISE_LAUNCHER.replace("sys.exit(cli.main(sys.argv[1:]))", "sys.exit(cli.main(sys.argv[1:]))")
                      .format(src=str(ROOT / "src")), encoding="utf-8")

    async def go():
        async with _client(ws, script) as c:
            for _ in range(3):
                r = await c.call_tool("dobim_query", {"command": "project.info", "project": "p.dobim"})
                assert r.structured_content["ok"], r
    asyncio.run(go())
