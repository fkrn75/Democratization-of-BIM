"""ifcmcp 로 E1(벽 이동)을 수행하고 CLI 결과와 비교, plot/validate 호출 결과를 기록한다. 사용: python mcp_edit.py"""
import asyncio
import json
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
PY = HERE / "tools" / "ifc-cli" / ".venv" / "Scripts" / "python.exe"
R = HERE / "results" / "ifcopenshell"
log = []


async def main():
    p = StdioServerParameters(command=str(PY), args=["-X", "utf8", "-m", "ifcmcp"], env={"PYTHONUTF8": "1"})
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(name, args):
                t = time.perf_counter()
                res = await s.call_tool(name, args)
                kinds = [c.type for c in res.content]
                txt = "".join(c.text for c in res.content if getattr(c, "text", None))
                log.append({"tool": name, "args": args, "sec": round(time.perf_counter() - t, 2), "isError": res.isError,
                            "content_types": kinds, "text_head": txt[:260]})
                return txt
            await call("ifc_load", {"path": str(HERE / "RA-01.ifc")})
            await call("ifc_select", {"query": "IfcWall, Name=W-E-01"})
            await call("ifc_edit", {"function_path": "geometry.edit_object_placement",
                                    "params": json.dumps({"product": "#104", "matrix": [[1, 0, 0, 0], [0, 1, 0, 0.6], [0, 0, 1, 0], [0, 0, 0, 1]],
                                                          "should_transform_children": "true"})})
            await call("ifc_validate", {"express_rules": True})
            await call("ifc_save", {"path": str(R / "E1_mcp.ifc")})
            await call("ifc_plot", {"view": "floorplan"})
            await call("ifc_render", {"view": "iso"})
asyncio.run(main())
(R / "mcp_edit.json").write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
for l in log:
    print(l["tool"], l["sec"], "err" if l["isError"] else "ok", l["content_types"], l["text_head"][:110].replace("\n", " "))
