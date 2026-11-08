# SPDX-License-Identifier: MPL-2.0
"""S7 도구 공통: 경로, 창 없는 서브프로세스 실행, 동결본 위치."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
S7 = HERE.parent
S1 = S7.parent / "s1-core"
S1_SRC = S1 / "src"
S1_TOOLS = S1 / "tools"
FROZEN_DIR = Path(os.environ.get("DOBIM_FROZEN_DIR", S7 / "build" / "dist" / "dobim"))
EXE = FROZEN_DIR / "dobim.exe"
MCP_EXE = FROZEN_DIR / "dobim-mcp.exe"
RESULTS = S7 / "results"
CREATE_NO_WINDOW = 0x08000000   # 콘솔 창이 뜨지 않게 한다 (사용자 화면 보호)


def run_quiet(cmd: list, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("stdin", subprocess.DEVNULL)
    kw.setdefault("capture_output", True)
    kw.setdefault("timeout", 300)
    kw["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run([str(c) for c in cmd], **kw)


def dec(b: bytes) -> str:
    """서브프로세스 출력 디코딩. 파이썬은 UTF-8 이지만 윈도우 로더 오류 문구("지정된 모듈을 찾을 수 없습니다")는 시스템 코드 페이지(cp949)라
    UTF-8 로만 읽으면 깨진 문자(U+FFFD)가 JSON 에 남는다 -> UTF-8 이 안 되면 cp949 로 읽는다."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp949", "replace")


def venv_cmd() -> list:
    """기준 실행: 소스 트리를 파이썬으로 직접 실행 (동결본과 비교할 대상)."""
    return [sys.executable, "-m", "dobim_core"]


def venv_env() -> dict:
    return {**os.environ, "PYTHONPATH": str(S1_SRC), "PYTHONUTF8": "1"}


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
