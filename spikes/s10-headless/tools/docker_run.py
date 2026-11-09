# SPDX-License-Identifier: MPL-2.0
"""Docker 로 깨끗한 Linux 환경 측정: 이미지 빌드(네트워크 사용) -> 실행(--network none, DISPLAY 없음, stdin 닫힘).
만든 이미지는 끝나면 지운다(--keep 으로 유지). 다른 컨테이너·이미지는 건드리지 않는다.
  python tools/docker_run.py --dockerfile docker/Dockerfile --tag dobim-s10-clean:tmp --out results/linux_debian.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def sh(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dockerfile", default="docker/Dockerfile")
    ap.add_argument("--tag", default="dobim-s10-clean:tmp")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ba", default=None, help="Building-Architecture.ifc 경로(선택)")
    ap.add_argument("--keep-ifc", default=None, help="내보낸 IFC 를 이 경로로 복사(분석용)")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="dobim-s10-ctx-"))
    ctx = tmp / "ctx"
    outdir = tmp / "out"
    outdir.mkdir()
    try:
        r = sh([sys.executable, str(HERE / "make_docker_ctx.py"), str(ctx)] + ([a.ba] if a.ba else []))
        shutil.copy(ROOT / a.dockerfile, ctx / "Dockerfile")
        b = sh(["docker", "build", "-q", "-t", a.tag, str(ctx)])
        if b.returncode != 0:
            print(b.stderr[-800:], file=sys.stderr)
            return 1
        res: dict = {"image": a.tag, "dockerfile": a.dockerfile}
        info = sh(["docker", "run", "--rm", "--network", "none", a.tag, "sh", "-c",
                   "head -2 /etc/os-release; python3 --version; dpkg -l | wc -l; "
                   "dpkg -l | awk '{print $2}' | grep -Ei 'libx11|libxcb|libgl|libegl|mesa|wayland|libxext|libsm[^a]|libice|gtk|qt5|qt6|libsdl|libdrm|tk8|tcl8|xorg|libxrender' || echo '(GUI/GL 패키지 없음)'; "
                   "echo \"DISPLAY=[$DISPLAY] WAYLAND_DISPLAY=[$WAYLAND_DISPLAY]\"; id -un; "
                   "python3 -c \"import socket; socket.create_connection(('1.1.1.1',53),timeout=3)\" 2>&1 | tail -1"])
        res["env_probe"] = info.stdout.strip().splitlines()
        # 본 측정: 네트워크 없음, 루트 파일시스템 읽기 전용, stdin 닫힘(fd 0 을 닫고 실행)
        mount = f"{outdir.as_posix()}:/out"
        cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/work:exec,mode=1777", "--tmpfs", "/tmp",
               "-v", mount, a.tag, "sh", "-c",
               "exec 0<&-; python3 /opt/dobim/s10/tools/suite.py --workspace /work/ws --s0 /opt/dobim/s0 --out /out"]
        p = sh(cmd, timeout=1800)
        res["suite_exit"] = p.returncode
        lines = p.stdout.splitlines()
        res["suite_stdout_lines"] = len(lines)
        try:
            res["suite"] = json.loads(lines[0]) if len(lines) == 1 else None
        except ValueError:
            res["suite"] = None
        if res["suite"] is None:
            res["stderr_tail"] = p.stderr[-800:]
        png_dir = Path(a.out).parent / "png"
        png_dir.mkdir(parents=True, exist_ok=True)
        if a.keep_ifc:
            for f in outdir.glob("*.ifc"):
                shutil.copy(f, a.keep_ifc)
        for f in outdir.glob("*.png"):
            shutil.copy(f, png_dir / f"{Path(a.out).stem}_{f.name}")
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8", newline="\n")
        return 0 if p.returncode == 0 and res["suite"] else 1
    finally:
        if not a.keep:
            sh(["docker", "rmi", a.tag])
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
