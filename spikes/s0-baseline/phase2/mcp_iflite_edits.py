"""ifc-lite MCP(컨테이너 stdio)로 E1~E3 수행·저장. 각 시나리오는 새 세션(원본 RA-01 로드)에서 시작한다."""
import asyncio, json, time
from pathlib import Path
import ifcopenshell
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
HERE = Path(__file__).resolve().parent.parent
OUT = (HERE / "out/p2/iflite").resolve()
f = ifcopenshell.open(str(HERE / "RA-01.ifc"))
named = lambda n: next(e for e in f.by_type("IfcRoot") if getattr(e, "Name", None) == n)
W1 = named("W-E-01").id()
DOORS = [named("D-01").id(), named("D-05").id()]
st1 = named("1F")
GF = [e for rel in st1.ContainsElements for e in rel.RelatedElements if e.is_a() in ("IfcWall", "IfcColumn")]
args = ["run", "--rm", "-i", "--network", "none", "--cap-drop=ALL", "--security-opt", "no-new-privileges", "--memory", "2g", "--cpus", "2", "--pids-limit", "256",
        "--read-only", "--tmpfs", "/tmp:size=256m", "-v", f"{HERE / 'RA-01.ifc'}:/in/RA-01.ifc:ro", "-v", f"{OUT}:/out", "s0-iflite", "ifc-lite", "mcp", "/in/RA-01.ifc"]
LOG = []


async def session(label, steps):
    async with stdio_client(StdioServerParameters(command="docker", args=args)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for tool, a in steps:
                t = time.perf_counter()
                res = await s.call_tool(tool, a)
                txt = "".join(c.text for c in res.content if getattr(c, "text", None))
                LOG.append({"scenario": label, "tool": tool, "args": json.dumps(a)[:200], "sec": round(time.perf_counter() - t, 2), "isError": res.isError, "head": txt[:260]})
                print(label, tool, "ERR" if res.isError else "ok", txt[:140].replace("\n", " "))


async def main():
    await session("E1", [("edit_element_geometry", {"operation": {"kind": "transform", "express_ids": [W1], "transform": {"kind": "move", "delta": [0, 0.6]}}}),
                         ("model_save", {"file_path": "/out/E1.mcp.ifc"})])
    await session("E2", [("edit_hosted_element", {"express_id": d, "patch": {"OverallWidth": 1.0}}) for d in DOORS] + [("model_save", {"file_path": "/out/E2.mcp.ifc"})])
    steps = [("edit_element_geometry", {"operation": {"kind": "size", "express_id": e.id(), "patch": ({"kind": "wall", "Height": 3.6} if e.is_a("IfcWall") else {"kind": "linear", "Depth": 3.6})}}) for e in GF]
    await session("E3", steps + [("model_save", {"file_path": "/out/E3.mcp.ifc"})])
    # dry-run/원자성: 겹치는 문 배치는 거부되는가
    await session("C4", [("place_door", {"host_express_id": W1, "params": {"Offset": 8.0, "Width": 0.9, "Height": 2.1}}), ("mutation_undo", {"n": 1}), ("model_save", {"file_path": "/out/C4.mcp.ifc"})])
asyncio.run(main())
json.dump(LOG, open(OUT / "mcp_edits.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
