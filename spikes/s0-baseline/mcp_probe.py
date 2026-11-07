"""ifcmcp(stdio) 프로브: 공식 MCP Python SDK 클라이언트로 서버를 띄워 시동 시간, 도구 목록, 호출 결과를 기록한다.
사용: python mcp_probe.py OUT.json   (tools/ifc-cli/.venv 의 python 으로 실행)"""
import asyncio
import json
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
PY = HERE / "tools" / "ifc-cli" / ".venv" / "Scripts" / "python.exe"
out = {"calls": []}


async def main():
    t0 = time.perf_counter()
    params = StdioServerParameters(command=str(PY), args=["-X", "utf8", "-m", "ifcmcp"], env={"PYTHONUTF8": "1"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            out["startup_sec"] = round(time.perf_counter() - t0, 2)
            out["server"] = init.serverInfo.name, init.serverInfo.version
            out["protocol"] = init.protocolVersion
            tl = await s.list_tools()
            out["tools"] = [{"name": t.name, "annotations": t.annotations.model_dump() if t.annotations else None,
                             "schema_bytes": len(json.dumps(t.inputSchema))} for t in tl.tools]

            async def call(name, args):
                t = time.perf_counter()
                res = await s.call_tool(name, args)
                txt = "".join(c.text for c in res.content if getattr(c, "text", None))
                out["calls"].append({"tool": name, "args": args, "sec": round(time.perf_counter() - t, 2),
                                     "isError": res.isError, "has_structured": res.structuredContent is not None,
                                     "text_chars": len(txt), "text_head": txt[:300]})
                return txt
            names = {t.name for t in tl.tools}
            print("tools:", sorted(names))
            ifc = str(HERE / "RA-01.ifc")
            for n, a in (("open_model", {"path": ifc}), ("summary", {}), ("select", {"query": "IfcWall, Name=W-E-01"})):
                if n in names:
                    await call(n, a)
asyncio.run(main())
Path(sys.argv[1]).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("startup", out.get("startup_sec"), "tools", len(out["tools"]))
