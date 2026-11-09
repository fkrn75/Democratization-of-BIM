# SPDX-License-Identifier: MPL-2.0
"""층 A 프로토콜: 공식 SDK 클라이언트(mcp 2.3.0)로 stdio 서버를 호출해 모든 커맨드의 examples 를 CLI·MCP 로 실행·비교한다.

s1-core 의 tools/parity.py 를 그대로 재사용한다(복제하지 않는다). 여기서는 실행하고 값만 뽑아 저장한다.
추가로 tools/mcp_probe.py 로 협상 버전·시작·호출 지연을 잰다.
"""
import asyncio
import json
import sys
from pathlib import Path

import common as C  # noqa: F401  (sys.path 에 s1-core/tools 를 넣는다)
import parity
import mcp_probe


def main(out_dir: str) -> int:
    out = Path(out_dir)
    full = asyncio.run(parity.run_parity())
    summary = {k: v for k, v in full.items() if k not in ("results", "fixture_hashes")}
    summary["per_command"] = sorted({r["command"] for r in full["results"] if r["label"].startswith("ex-")})
    summary["failed_runs"] = [r["label"] for r in full["results"]
                              if not (r["same_envelope"] and r["exit_code_matches_envelope"] and r["state_hash_equal"])]
    (out / "s11_layer_a_parity.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    probe = out / "s11_layer_a_probe.json"
    asyncio.run(mcp_probe.main(str(probe)))
    print(json.dumps({k: summary[k] for k in ("pass", "commands_in_registry", "commands_executed_with_examples",
                                              "example_runs", "error_case_runs", "parity_C_percent", "mismatches")},
                     ensure_ascii=False))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
