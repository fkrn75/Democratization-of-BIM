# SPDX-License-Identifier: MPL-2.0
"""Windows '깨끗한 환경' 근사 실행기 (Windows Sandbox 는 쓰지 않는다).

새 venv 의 인터프리터로 ra01_headless.py 를 실행하되,
- 환경변수를 SystemRoot 와 임시 프로필 폴더(USERPROFILE·APPDATA·LOCALAPPDATA·TEMP·TMP)로만 구성하고
  (PATH·PYTHON*·DISPLAY 등 사용자 환경 제거),
- stdin 을 닫고(DEVNULL), 콘솔 창 없이(CREATE_NO_WINDOW) 띄우고,
- 부모 환경의 사용자 설치 site-packages 가 끼어들지 않도록 인터프리터에 -I(격리 모드)를 준다.
한계: 같은 OS 설치·같은 사용자 계정·같은 디스크이며, 레지스트리·시스템 PATH·백신은 그대로다.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv-python", required=True)
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--out", default=None, help="자식 stdout(JSON)을 이 파일에 저장")
    ap.add_argument("--suite-s0", default=None, help="주면 ra01 대신 suite.py(렌더·GUI 검사 포함)를 돌린다. RA-01.ifc 가 있는 폴더")
    ap.add_argument("--keep", action="store_true", help="임시 프로필 폴더를 남긴다")
    a = ap.parse_args()
    prof = Path(tempfile.mkdtemp(prefix="dobim-s10-profile-"))
    for sub in ("AppData/Roaming", "AppData/Local", "Temp"):
        (prof / sub).mkdir(parents=True, exist_ok=True)
    ws = Path(a.workspace) if a.workspace else prof / "ws"
    env = {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "USERPROFILE": str(prof),
        "APPDATA": str(prof / "AppData/Roaming"),
        "LOCALAPPDATA": str(prof / "AppData/Local"),
        "TEMP": str(prof / "Temp"),
        "TMP": str(prof / "Temp"),
    }
    if a.suite_s0:
        argv = [a.venv_python, "-I", str(HERE / "suite.py"), "--workspace", str(ws), "--s0", a.suite_s0, "--env-mode", "minimal"]
    else:
        argv = [a.venv_python, "-I", str(HERE.parent / "ra01_headless.py"), "--workspace", str(ws),
                "--python", a.venv_python, "--env-mode", "minimal", "--timing"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, env=env, creationflags=flags, timeout=600)
    text = p.stdout.decode("utf-8", "replace")
    lines = text.splitlines()
    info = {"exit": p.returncode, "stdout_lines": len(lines), "stdout_is_single_json": False, "env_keys": sorted(env)}
    try:
        if len(lines) == 1:
            json.loads(lines[0])
            info["stdout_is_single_json"] = True
    except ValueError:
        pass
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    if not a.keep:
        import shutil
        shutil.rmtree(prof, ignore_errors=True)
    sys.stderr.write(p.stderr.decode("utf-8", "replace")[-600:])
    sys.stdout.write(json.dumps(info, sort_keys=True) + "\n")
    return 0 if p.returncode == 0 and info["stdout_is_single_json"] else 1


if __name__ == "__main__":
    sys.exit(main())
