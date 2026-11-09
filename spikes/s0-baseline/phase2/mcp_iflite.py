"""ifc-lite mcp(컨테이너 stdio) 프로브: 도구 수·어노테이션·스키마 크기, 시동, 몇 가지 호출 결과를 기록한다."""
import asyncio, json, time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
HERE = Path(__file__).resolve().parent.parent
OUT = (HERE / "out/p2/iflite").resolve()
args = ["run", "--rm", "-i", "--network", "none", "--cap-drop=ALL", "--security-opt", "no-new-privileges", "--memory", "2g", "--cpus", "2", "--pids-limit", "256",
        "--read-only", "--tmpfs", "/tmp:size=256m", "-v", f"{HERE / 'RA-01.ifc'}:/in/RA-01.ifc:ro", "-v", f"{OUT}:/out", "s0-iflite", "ifc-lite", "mcp", "/in/RA-01.ifc"]
rec = {"calls": []}

async def go():
    t0 = time.perf_counter()
    async with stdio_client(StdioServerParameters(command="docker", args=args)) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            rec["startup_sec"] = round(time.perf_counter() - t0, 2)
            rec["server"] = [init.serverInfo.name, init.serverInfo.version]
            tl = await s.list_tools()
            rec["n_tools"] = len(tl.tools)
            rec["tools"] = [{"name": t.name, "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else None, "schema_bytes": len(json.dumps(t.inputSchema))} for t in tl.tools]
            rec["list_tools_bytes"] = len(json.dumps([t.model_dump() for t in tl.tools], default=str))
            print("startup", rec["startup_sec"], "tools", rec["n_tools"], "bytes", rec["list_tools_bytes"])
            print([t["name"] for t in rec["tools"]])
            ro = [t["name"] for t in rec["tools"] if (t["annotations"] or {}).get("readOnlyHint")]
            print("readOnlyHint 도구:", len(ro))
            async def call(n, a):
                t = time.perf_counter()
                res = await s.call_tool(n, a)
                txt = "".join(c.text for c in res.content if getattr(c, "text", None))
                rec["calls"].append({"tool": n, "args": a, "sec": round(time.perf_counter() - t, 2), "isError": res.isError, "chars": len(txt), "head": txt[:200]})
                print(n, res.isError, txt[:160].replace("\n", " "))
            names = {t["name"] for t in rec["tools"]}
            for n, a in (("ifc_summary", {}), ("list_models", {}), ("query_entities", {"type": "IfcWall", "limit": 2})):
                if n in names:
                    await call(n, a)
asyncio.run(go())
json.dump(rec, open(OUT / "mcp_probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
