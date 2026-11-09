# SPDX-License-Identifier: MPL-2.0
"""Docker 빌드 컨텍스트를 만든다: 저장소 사본에서 필요한 파일만(.venv·결과·캐시 제외)."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
S10 = HERE.parent
S1 = S10.parent / "s1-core"
S0 = S10.parent / "s0-baseline"


def main(dest: str, ba: str | None) -> None:
    d = Path(dest)
    shutil.rmtree(d, ignore_errors=True)
    ign = shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", "results", "tests", ".pytest_cache", "*.egg-info")
    shutil.copytree(S1, d / "s1-core", ignore=ign)
    shutil.copytree(S10, d / "s10", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "results", "tests", "docker", ".pytest_cache"))
    (d / "s0").mkdir()
    shutil.copy(S0 / "RA-01.ifc", d / "s0" / "RA-01.ifc")
    if ba and Path(ba).exists():
        shutil.copy(ba, d / "s0" / "Building-Architecture.ifc")
    shutil.copy(S10 / "docker" / "Dockerfile", d / "Dockerfile")
    print(f"컨텍스트 생성: {sum(1 for _ in d.rglob('*') if _.is_file())} 파일")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
