# SPDX-License-Identifier: MPL-2.0
"""패리티 C 전체(S1 tools/parity.py)를 동결본으로 돌린다.

S1 의 시험 코드는 건드리지 않고, 서브프로세스 호출만 동결 실행 파일로 바꿔 끼운다:
  CLI 쪽  [python, -m, dobim_core, ...]  ->  [dobim.exe, ...]
  MCP 쪽  python -m dobim_core mcp serve ->  dobim-mcp.exe (전역 옵션은 그대로)
모든 커맨드의 모든 예시, 오류 사례, 큰 배치, 위험 등급 거부가 동결본에서 CLI 와 MCP 가 같은 결과를 내야 한다.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess as _sp
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CREATE_NO_WINDOW, EXE, MCP_EXE, RESULTS, S1_SRC, S1_TOOLS, write_json  # noqa: E402
sys.path.insert(0, str(S1_TOOLS))
sys.path.insert(0, str(S1_SRC))
import parity  # noqa: E402


class _Sub:
    """parity.subprocess 대용: -m dobim_core 호출을 동결 exe 로 바꾸고 창이 뜨지 않게 한다."""
    PIPE, DEVNULL, CalledProcessError = _sp.PIPE, _sp.DEVNULL, _sp.CalledProcessError

    @staticmethod
    def run(cmd, **kw):
        if list(cmd[1:3]) == ["-m", "dobim_core"]:
            cmd = [str(EXE), *cmd[3:]]
        kw["creationflags"] = CREATE_NO_WINDOW
        kw["env"] = {k: v for k, v in (kw.get("env") or os.environ).items() if k != "PYTHONPATH"}   # 소스 트리를 보지 못하게 한다
        return _sp.run(cmd, **kw)


parity.subprocess = _Sub

_orig_init = parity.McpSide.__init__


def _init(self, ws):
    _orig_init(self, ws)
    from mcp import StdioServerParameters
    env = {k: v for k, v in self.params.env.items() if k != "PYTHONPATH"}
    self.params = StdioServerParameters(command=str(MCP_EXE), args=["--workspace", str(ws), "--actor", "cli"], env=env)


parity.McpSide.__init__ = _init


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="s7-parity-"))
    res = asyncio.run(parity.run_parity(work))
    res["executed_with"] = "동결본 dobim.exe(CLI) / dobim-mcp.exe(MCP). PYTHONPATH 제거(소스 트리 접근 없음)"
    slim = {k: v for k, v in res.items() if k != "results"}
    slim["result_count"] = len(res["results"])
    write_json(RESULTS / "s7_parity_frozen.json", slim)
    print(json.dumps({k: slim[k] for k in ("pass", "parity_C_percent", "coverage_percent", "example_runs", "error_case_runs",
                                           "negative_control_detects_divergence", "risk_gating_enforced")}, ensure_ascii=False))
    if res["mismatches"]:
        print("\n".join(res["mismatches"][:10]))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
