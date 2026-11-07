"""ccad mcp (컨테이너 stdio) 프로브: 공식 MCP Python SDK 클라이언트(호스트)가 docker run -i 를 서버 명령으로 띄운다.
시동 시간, 도구 수·스키마 크기·어노테이션, E1 호출 결과(CLI 와 비교)를 기록한다."""
import asyncio, json, os, sys, time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent.parent
OUT = (HERE / "out/p2/ccad/work").resolve()
args = ["run", "--rm", "-i", "--network", "none", "--cap-drop=ALL", "--security-opt", "no-new-privileges", "--memory", "2g", "--cpus", "2",
        "--pids-limit", "256", "--read-only", "--tmpfs", "/tmp:size=256m", "-v", f"{OUT}:/out", "s0-ccad", "ccad", "mcp"]
rec = {"calls": []}


async def main():
    t0 = time.perf_counter()
    async with stdio_client(StdioServerParameters(command="docker", args=args)) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            rec["startup_sec"] = round(time.perf_counter() - t0, 2)
            rec["server"] = [init.serverInfo.name, init.serverInfo.version]
            rec["protocol"] = init.protocolVersion
            tl = await s.list_tools()
            rec["n_tools"] = len(tl.tools)
            rec["tools"] = [{"name": t.name, "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else None,
                             "schema_bytes": len(json.dumps(t.inputSchema)), "desc_chars": len(t.description or "")} for t in tl.tools]
            rec["list_tools_bytes"] = len(json.dumps([t.model_dump() for t in tl.tools], default=str))
            print("tools", rec["n_tools"], rec["startup_sec"], "s")

            async def call(name, a):
                t = time.perf_counter()
                res = await s.call_tool(name, a)
                txt = "".join(c.text for c in res.content if getattr(c, "text", None))
                rec["calls"].append({"tool": name, "args": a, "sec": round(time.perf_counter() - t, 2), "isError": res.isError,
                                     "types": [c.type for c in res.content], "structured": res.structuredContent is not None, "chars": len(txt), "head": txt[:240]})
                return txt
            names = [t["name"] for t in rec["tools"]]
            print(names[:30])
            if "run" in names or any(n for n in names if n.startswith("ccad")):
                pass
            sys.stdout.flush()
            rec["names"] = names
            return names, call, s
    
async def go():
    t0 = time.perf_counter()
    async with stdio_client(StdioServerParameters(command="docker", args=args)) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            rec["startup_sec"] = round(time.perf_counter() - t0, 2)
            rec["server"] = [init.serverInfo.name, init.serverInfo.version]
            rec["protocol"] = init.protocolVersion
            tl = await s.list_tools()
            rec["n_tools"] = len(tl.tools)
            rec["tools"] = [{"name": t.name, "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else None,
                             "schema_bytes": len(json.dumps(t.inputSchema)), "desc_chars": len(t.description or "")} for t in tl.tools]
            rec["list_tools_bytes"] = len(json.dumps([t.model_dump() for t in tl.tools], default=str))
            print("startup", rec["startup_sec"], "tools", rec["n_tools"], "list bytes", rec["list_tools_bytes"])
            print([ (t["name"], t["annotations"]) for t in rec["tools"]][:12])

            async def call(name, a):
                t = time.perf_counter()
                res = await s.call_tool(name, a)
                txt = "".join(c.text for c in res.content if getattr(c, "text", None))
                rec["calls"].append({"tool": name, "args": a, "sec": round(time.perf_counter() - t, 2), "isError": res.isError,
                                     "types": [c.type for c in res.content], "structured": res.structuredContent is not None, "chars": len(txt), "head": txt[:200]})
                return txt
            names = {t["name"] for t in rec["tools"]}
            return_names = sorted(names)
            rec["names"] = return_names
            if "project_open" in names:  # 커맨드가 직접 도구로 노출되는 경우
                await call("project_open", {"path": "/out/native.ccad"})
                await call("elements_move", {"ids": [101], "delta": [0, 0.6]})
                await call("ifc_export", {"path": "/out/mcp_e1.ifc"})
            else:
                print("직접 도구 아님 -> 메타 도구 구조:", return_names)
    json.dump(rec, open(HERE / "out/p2/ccad/mcp_probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

asyncio.run(go())
