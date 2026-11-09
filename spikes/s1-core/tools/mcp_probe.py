# SPDX-License-Identifier: MPL-2.0
"""공식 SDK 클라이언트로 stdio MCP 서버를 측정한다: 협상된 프로토콜 버전, 시작·목록 지연, 호출 지연, 응답 크기.
Claude·Codex 실제 클라이언트 호출은 S11 범위라 여기서는 하지 않는다."""
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parity  # noqa: E402
import kit  # noqa: E402
from dobim_core import fixture as F  # noqa: E402


async def main(out_path):
    from mcp import Client, StdioServerParameters
    ws = Path(tempfile.mkdtemp(prefix="dobim-mcp-"))
    s = kit.Session(ws / "p.dobim")
    for name, args in F.SCRIPT:
        s.run(name, args)
    s.close()
    params = StdioServerParameters(command=sys.executable, args=["-m", "dobim_core", "--workspace", str(ws), "mcp", "serve"],
                                   env={**os.environ, "PYTHONPATH": parity.SRC, "PYTHONUTF8": "1"})
    res = {}
    t0 = time.perf_counter()
    async with Client(params) as c:
        res["connect_and_initialize_sec"] = round(time.perf_counter() - t0, 3)
        res["negotiated_protocol_version"] = c.protocol_version
        res["server_info"] = c.server_info.name if c.server_info else None
        t = time.perf_counter()
        tools = await c.list_tools()
        res["tools_list_sec"] = round(time.perf_counter() - t, 3)
        res["tool_count"] = len(tools.tools)
        res["tool_schema_bytes"] = {t_.name: len(json.dumps(t_.input_schema)) for t_ in tools.tools}
        calls = [("dobim_commands_search", {"query": "wall"}), ("dobim_command_describe", {"name": "wall.create"}),
                 ("dobim_query", {"command": "project.info", "project": "p.dobim"}),
                 ("dobim_query", {"command": "schedule.rows", "args": {"kind": "door"}, "project": "p.dobim"}),
                 ("dobim_query", {"command": "model.hash", "project": "p.dobim"}),
                 ("dobim_execute", {"command": "wall.move", "args": {"id": F.W1, "dx": 100}, "project": "p.dobim"}),
                 ("dobim_execute", {"command": "history.undo", "project": "p.dobim"}),
                 ("dobim_query", {"command": "model.validate", "project": "p.dobim"})]
        rows = []
        for tool, a in calls:
            ts = []
            size = 0
            for _ in range(5):
                t = time.perf_counter()
                r = await c.call_tool(tool, a)
                ts.append((time.perf_counter() - t) * 1000)
                size = len(r.content[0].text.encode("utf-8"))
                assert not r.is_error, r.content[0].text[:200]
            rows.append({"tool": tool, "command": a.get("command") or a.get("name") or a.get("query"),
                         "first_ms": round(ts[0], 1), "median_ms": round(statistics.median(ts), 1), "response_bytes": size})
        res["calls"] = rows
        res["max_response_bytes"] = max(r["response_bytes"] for r in rows)
    res["note"] = "tools/list 와 project.info 는 첫 호출에서 ifcopenshell 을 불러오므로 first_ms 가 크다(지연 import)."
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
