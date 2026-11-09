"""ccad mcp(컨테이너 stdio)로 E1~E6 를 수행해 C2(MCP만으로 수행한 비율)를 실측한다. 결과 값은 CLI 결과와 맞춰 본다."""
import asyncio, copy, json, time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
HERE = Path(__file__).resolve().parent.parent
OUT = (HERE / "out/p2/ccad/work").resolve()
args = ["run", "--rm", "-i", "--network", "none", "--cap-drop=ALL", "--security-opt", "no-new-privileges", "--memory", "2g", "--cpus", "2", "--pids-limit", "256",
        "--read-only", "--tmpfs", "/tmp:size=256m", "-v", f"{OUT}:/out", "s0-ccad", "ccad", "mcp"]
R = {}


async def main():
    async with stdio_client(StdioServerParameters(command="docker", args=args)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(n, a):
                res = await s.call_tool(n, a)
                txt = "".join(c.text for c in res.content if getattr(c, "text", None))
                if res.isError:
                    raise RuntimeError(n + ": " + txt[:200])
                return res.structuredContent or json.loads(txt)
            # E1
            await call("project_open", {"path": "/out/native.ccad"})
            ids = [e["id"] for e in (await call("elements_list", {}))["result"]] if False else None
            await call("elements_move", {"ids": [101], "delta": [0, 0.6]})
            R["E1"] = (await call("element_get", {"id": 101}))["params"]["start"]
            # E2
            await call("project_open", {"path": "/out/native.ccad"})
            tp = (await call("type_get", {"type": "DT-01"}))["params"]
            d = copy.deepcopy(tp["def"])
            for p in d["params"]:
                if p["name"] == "Width":
                    p["value"] = 1.0
            await call("type_set", {"type": "DT-01", "params": {"def": d}})
            sch = await call("schedule_table", {"view": "Door Schedule"})
            R["E2/E6"] = [row["cells"][2] for row in sch["rows"]]
            # E3
            await call("project_open", {"path": "/out/native.ccad"})
            await call("level_set", {"level": "2F", "elevation": 3.6})
            R["E3"] = round((await call("element_get", {"id": 101}))["derived"]["height"], 3)
            # E4
            await call("project_open", {"path": "/out/n_views.ccad"})
            vl = await call("views_list", {})
            vl = vl if isinstance(vl, list) else vl.get("result", vl)
            p1 = next(v["id"] for v in vl if v["category"] == "floor_plan" and v["name"] == "Ground Floor")
            await call("view_export", {"view": p1, "path": "/out/mcp_plan_before.svg", "format": "svg"})
            await call("elements_move", {"ids": [101], "delta": [0, 0.6]})
            await call("view_export", {"view": p1, "path": "/out/mcp_plan_after.svg", "format": "svg"})
            # E5
            await call("project_open", {"path": "/out/n_sheet2.ccad"})
            vl = await call("views_list", {})
            vl = vl if isinstance(vl, list) else vl.get("result", vl)
            sid = next(v["id"] for v in vl if v["name"] == "S-101")
            await call("sheet_export", {"sheet": sid, "path": "/out/mcp_S101.pdf", "format": "pdf"})
asyncio.run(main())
a = (OUT / "mcp_plan_before.svg").read_text(encoding="utf-8"); b = (OUT / "mcp_plan_after.svg").read_text(encoding="utf-8")
R["E4_svg_changed"] = a != b
R["E5_pdf_bytes"] = (OUT / "mcp_S101.pdf").stat().st_size
print(json.dumps(R, ensure_ascii=False))
