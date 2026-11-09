# SPDX-License-Identifier: MPL-2.0
"""작업 폴더(workspace) 경로 검사 (15.8절). 실제 경로로 해석한 뒤 폴더 밖이면 PATH_DENIED."""
from __future__ import annotations

import os
from pathlib import Path

from dobim_core.errors import DobimError

_DEVICE = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def resolve_in_workspace(workspace: Path, raw: str, *, must_exist: bool = False) -> Path:
    if not raw or "\0" in raw:
        raise DobimError("PATH_DENIED", "경로가 비어 있거나 잘못됐다", path="/path")
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise DobimError("PATH_DENIED", "UNC 경로는 허용하지 않는다", path="/path")
    p = Path(raw)
    if not p.is_absolute():
        p = workspace / p
    if any(part.split(".")[0].upper() in _DEVICE for part in p.parts[1:]):
        raise DobimError("PATH_DENIED", "장치 이름은 허용하지 않는다", path="/path")
    real = Path(os.path.realpath(p))   # .., 심볼릭 링크, 대소문자를 해석한다
    root = Path(os.path.realpath(workspace))
    try:
        real.relative_to(root)
    except ValueError:
        raise DobimError("PATH_DENIED", "작업 폴더 밖이라 거부했다", path="/path",
                         hint="--workspace 또는 --allow-path 로 서버 시작 시 지정하세요") from None
    if must_exist and not real.exists():
        raise DobimError("NOT_FOUND", f"파일이 없다: {real.name}", path="/path")
    return real
