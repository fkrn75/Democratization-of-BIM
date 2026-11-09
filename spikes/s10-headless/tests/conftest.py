# SPDX-License-Identifier: MPL-2.0
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
S1 = ROOT.parent / "s1-core"
S0 = ROOT.parent / "s0-baseline"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(S1 / "src"))


def child_env(extra: dict | None = None) -> dict:
    """자식 프로세스 환경: 최소 변수 + 코어 소스 경로(설치하지 않은 상태에서도 돌게)."""
    keep = ("SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PATH")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["PYTHONPATH"] = os.pathsep.join([str(S1 / "src"), str(ROOT / "src")])
    env.update(extra or {})
    return env


@pytest.fixture(scope="session")
def expected() -> dict:
    return json.loads((ROOT / "results" / "expected.json").read_text(encoding="utf-8"))
