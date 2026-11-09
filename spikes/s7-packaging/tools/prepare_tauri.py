# SPDX-License-Identifier: MPL-2.0
"""동결본을 Tauri 프로젝트(app/src-tauri)에 배치한다.

  dobim.exe      -> binaries/dobim-<triple>.exe        (externalBin: 설치 폴더 루트에 triple 없이 놓인다)
  dobim-mcp.exe  -> binaries/dobim-mcp-<triple>.exe
  _internal/     -> resources/_internal/                (resources {"resources/": ""} 로 설치 폴더 루트에 그대로 놓인다)
  THIRD-PARTY-NOTICES.txt -> resources/
  docs/installed/*.md (교체 절차 LIBRARY-REPLACEMENT.md) -> resources/   (설치 폴더 루트에 함께 설치된다)
PyInstaller 6 onedir 는 실행 파일 옆의 _internal 을 찾으므로 이 배치가 곧 설치 폴더 배치다.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FROZEN_DIR, RESULTS, S7  # noqa: E402

TRIPLE = "x86_64-pc-windows-msvc"
TAURI = S7 / "app" / "src-tauri"


def main() -> int:
    bins, res = TAURI / "binaries", TAURI / "resources"
    for d in (bins, res):
        for p in d.iterdir():
            if p.name == ".gitkeep":
                continue
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    shutil.copy2(FROZEN_DIR / "dobim.exe", bins / f"dobim-{TRIPLE}.exe")
    shutil.copy2(FROZEN_DIR / "dobim-mcp.exe", bins / f"dobim-mcp-{TRIPLE}.exe")
    shutil.copytree(FROZEN_DIR / "_internal", res / "_internal")
    notices = RESULTS / "THIRD-PARTY-NOTICES.txt"
    if notices.exists():
        shutil.copy2(notices, res / "THIRD-PARTY-NOTICES.txt")
    for md in sorted((S7 / "docs" / "installed").glob("*.md")):
        shutil.copy2(md, res / md.name)
    n = sum(1 for _ in res.rglob("*") if _.is_file())
    print(f"배치 완료: binaries 2개, resources 파일 {n}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
