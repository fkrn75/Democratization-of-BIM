import asyncio, json
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
HERE = Path(__file__).resolve().parent.parent
OUT = (HERE / "out/p2/iflite").resolve()
args = ["run", "--rm", "-i", "--network", "none", "--cap-drop=ALL", "--memory", "2g", "--read-only", "--tmpfs", "/tmp:size=256m", "-v", f"{HERE / 'RA-01.ifc'}:/in/RA-01.ifc:ro", "-v", f"{OUT}:/out", "s0-iflite", "ifc-lite", "mcp", "/in/RA-01.ifc"]
async def go():
    async with stdio_client(StdioServerParameters(command="docker", args=args)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for t in (await s.list_tools()).tools:
                if t.name == "edit_element_geometry":
                    print(json.dumps(t.inputSchema)[:3500])
asyncio.run(go())
