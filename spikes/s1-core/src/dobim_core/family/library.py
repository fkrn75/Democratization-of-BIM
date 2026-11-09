# SPDX-License-Identifier: MPL-2.0
"""기본 패밀리 라이브러리: 이 폴더의 library/*.json. 파일을 읽으므로 queries(순수)에서 부르지 않는다."""
from __future__ import annotations

import json
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent / "library"


def ids() -> list[str]:
    return sorted(p.stem for p in LIB_DIR.glob("*.json"))


def load(family_id: str) -> dict | None:
    p = LIB_DIR / f"{family_id}.json"
    if not p.is_file() or p.parent != LIB_DIR:
        return None
    return json.loads(p.read_text(encoding="utf-8"))
