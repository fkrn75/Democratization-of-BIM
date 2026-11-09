# SPDX-License-Identifier: MPL-2.0
"""시작 시간 측정: 소스 실행(venv)과 동결본(py 모드: ifcopenshell 소스를 파일로 둠)을 같은 조건에서 번갈아 잰다.

- list: ifcopenshell 을 import 하지 않는 가벼운 경로(콜드 = 복사 직후 첫 실행, 웜 = 이후 반복)
- model.hash: ifcopenshell 을 import 하는 경로. py 모드는 첫 실행에 .py 를 컴파일해 __pycache__ 를 쓰므로 콜드/웜 차이가 난다
- MCP: 프로세스 시작부터 initialize 응답까지, tools/list 까지
번갈아 재서 다른 프로세스의 부하가 양쪽에 비슷하게 걸리게 한다(이 PC 는 다른 작업이 동시에 돈다 -> 절대값보다 상대 비교를 본다).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FROZEN_DIR, RESULTS, S1_SRC, run_quiet, venv_cmd, venv_env, write_json  # noqa: E402
sys.path.insert(0, str(S1_SRC))
from dobim_core import fixture as F  # noqa: E402

REPS = int(os.environ.get("S7_TIMING_REPS", "9"))


def stat(ts):
    return {"min": round(min(ts), 3), "median": round(statistics.median(ts), 3), "max": round(max(ts), 3), "n": len(ts)}


def one(cmd, env, ws, tail):
    t0 = time.perf_counter()
    p = run_quiet([*cmd, "--workspace", ws, *tail], env=env, cwd=ws)
    return time.perf_counter() - t0, p.returncode


async def mcp_times(cmd, args, env, ws):
    from mcp import Client, StdioServerParameters
    t0 = time.perf_counter()
    async with Client(StdioServerParameters(command=str(cmd), args=[str(a) for a in args], env=env, cwd=str(ws))) as c:
        t_init = time.perf_counter() - t0
        tools = await c.list_tools()
        t_list = time.perf_counter() - t0
        return t_init, t_list, len(tools.tools)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="s7-timing-"))
    try:
        fz_dir = work / "dobim"
        shutil.copytree(FROZEN_DIR, fz_dir)
        for p in fz_dir.rglob("__pycache__"):   # 콜드 상태를 만든다: 이전 실행이 남긴 컴파일 캐시 제거
            shutil.rmtree(p, ignore_errors=True)
        sides = {"venv": (venv_cmd(), venv_env()), "frozen": ([fz_dir / "dobim.exe"], {**os.environ, "PYTHONUTF8": "1"})}
        ws = {k: work / ("ws_" + k) for k in sides}
        for k in ws:
            ws[k].mkdir()
        for k, (cmd, env) in sides.items():
            for name, args in [("project.new", {"path": "t.dobim", "guid": F.GUID}), *F.SCRIPT]:
                run_quiet([*cmd, "--workspace", ws[k], "run", name, "--json", json.dumps(args), "-p", "t.dobim"], env=env, cwd=ws[k])
        # run 위 루프가 frozen 의 콜드 캐시를 이미 데웠을 수 있어 다시 지운다
        for p in fz_dir.rglob("__pycache__"):
            shutil.rmtree(p, ignore_errors=True)
        out = {"reps": REPS}
        cold = {}
        for k, (cmd, env) in sides.items():
            cold[k] = {"list_first_run_s": round(one(cmd, env, ws[k], ["list", "--query", "wall"])[0], 3),
                       "model_hash_first_run_s": round(one(cmd, env, ws[k], ["run", "model.hash", "-p", "t.dobim"])[0], 3)}
        out["cold_first_run"] = cold
        acc = {k: {"list": [], "model_hash": []} for k in sides}
        for _ in range(REPS):
            for k, (cmd, env) in sides.items():
                acc[k]["list"].append(one(cmd, env, ws[k], ["list", "--query", "wall"])[0])
                acc[k]["model_hash"].append(one(cmd, env, ws[k], ["run", "model.hash", "-p", "t.dobim"])[0])
        out["warm"] = {k: {m: stat(v) for m, v in d.items()} for k, d in acc.items()}
        mc = {"venv": [], "frozen": []}
        for _ in range(5):
            r = asyncio.run(mcp_times(sys.executable, ["-m", "dobim_core", "--workspace", ws["venv"], "mcp", "serve"], sides["venv"][1], ws["venv"]))
            mc["venv"].append(r)
            r = asyncio.run(mcp_times(fz_dir / "dobim-mcp.exe", ["--workspace", ws["frozen"]], sides["frozen"][1], ws["frozen"]))
            mc["frozen"].append(r)
        out["mcp"] = {k: {"initialize_s": stat([x[0] for x in v]), "tools_list_done_s": stat([x[1] for x in v]), "tool_count": v[0][2]}
                      for k, v in mc.items()}
        out["note"] = "다른 에이전트 작업이 동시에 도는 PC 에서 번갈아 측정한 값이다. 절대값이 아니라 상대 비교용"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    write_json(RESULTS / "s7_timing.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
