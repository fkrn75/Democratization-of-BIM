# SPDX-License-Identifier: MPL-2.0
"""Tauri 빌드 산출물(인스톨러)의 크기·해시를 기록하고, 인스톨러를 *실행하지 않고* 내용 배치를 검사한다.

검사 방법: NSIS 스크립트(installer.nsi)가 설치 폴더에 놓을 파일 목록을 읽어
  - 사이드카 dobim.exe, dobim-mcp.exe 가 설치 폴더 루트에 놓이는가
  - _internal 의 파일 수가 동결본과 같은가, 라이선스 고지가 포함되는가
를 본다. 이어서 같은 배치를 임시 폴더에 복사로 재현해 거기서 dobim.exe(콘솔, 창 없음)를 실행한다.
(진짜 설치 시험 — 레지스트리·바로가기·제거 — 은 사람과 깨끗한 VM 이 필요한 '미확인' 항목이다.)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FROZEN_DIR, RESULTS, S7, run_quiet, write_json  # noqa: E402

TAURI = S7 / "app" / "src-tauri"
REL = TAURI / "target" / "release"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    out: dict = {"installers": []}
    for p in sorted(list((REL / "bundle").rglob("*.exe")) + list((REL / "bundle").rglob("*.msi"))):
        out["installers"].append({"name": p.name, "kind": p.suffix.lstrip("."), "bytes": p.stat().st_size,
                                  "mib": round(p.stat().st_size / 2**20, 1), "sha256": sha(p)})
    shell = REL / "dobim-s7-shell.exe"
    if shell.exists():
        out["shell_exe"] = {"bytes": shell.stat().st_size, "sha256": sha(shell)}
    nsi = next(iter((REL / "nsis").rglob("installer.nsi")), None)
    frozen_files = sum(1 for f in (FROZEN_DIR / "_internal").rglob("*") if f.is_file())
    out["frozen_internal_files"] = frozen_files
    if nsi:
        txt = nsi.read_text(encoding="utf-8", errors="replace")
        files = re.findall(r'^\s*File\s+(?:/a\s+)?"/oname=([^"]+)"\s+"([^"]+)"', txt, re.M)
        names = [o for o, _src in files if not o.startswith("$TEMP")]
        out["nsis_script"] = {
            "file_commands": len(files),
            "has_dobim_exe": any(n.lower() == "dobim.exe" for n in names),
            "has_dobim_mcp_exe": any(n.lower() == "dobim-mcp.exe" for n in names),
            "internal_entries": sum(1 for n in names if n.replace("/", "\\").lower().startswith("_internal\\")),
            "has_third_party_notices": any("THIRD-PARTY-NOTICES" in n for n in names),
            "has_library_replacement_doc": any("LIBRARY-REPLACEMENT" in n for n in names),
            "install_mode_line": next((ln.strip() for ln in txt.splitlines() if "INSTALLMODE" in ln.upper() and "!define" in ln), None),
        }
        out["nsis_script"]["internal_matches_frozen"] = out["nsis_script"]["internal_entries"] == frozen_files
    # 배치 재현: 설치 폴더 구조를 복사로 만들고 사이드카를 창 없이 실행
    work = Path(tempfile.mkdtemp(prefix="s7-layout-"))
    try:
        inst = work / "install"
        inst.mkdir()
        bins = TAURI / "binaries"
        shutil.copy2(bins / "dobim-x86_64-pc-windows-msvc.exe", inst / "dobim.exe")
        shutil.copy2(bins / "dobim-mcp-x86_64-pc-windows-msvc.exe", inst / "dobim-mcp.exe")
        shutil.copytree(TAURI / "resources" / "_internal", inst / "_internal")
        for extra in ("THIRD-PARTY-NOTICES.txt", "LIBRARY-REPLACEMENT.md"):
            if (TAURI / "resources" / extra).exists():
                shutil.copy2(TAURI / "resources" / extra, inst / extra)
        ws = work / "ws"
        ws.mkdir()
        p = run_quiet([inst / "dobim.exe", "--workspace", ws, "list", "--query", "wall"], cwd=ws)
        d = json.loads(p.stdout.decode("utf-8")) if p.stdout else {}
        out["layout_emulation"] = {"dobim_list_ok": bool(d.get("ok")), "exit_code": p.returncode,
                                   "items": len((d.get("result") or {}).get("items", [])),
                                   "same_files_as_frozen": sum(1 for f in (inst / "_internal").rglob("*") if f.is_file()) == frozen_files,
                                   "note": "복사로 재현한 설치 폴더에서의 실행이다. 진짜 설치(레지스트리·바로가기·제거)는 아니다"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    write_json(RESULTS / "s7_tauri_build.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
