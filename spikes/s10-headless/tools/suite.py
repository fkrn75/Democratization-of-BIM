# SPDX-License-Identifier: MPL-2.0
"""S10 종합 측정(OS 공통): 헤드리스 완주 스크립트 + CPU 3D 렌더 시간·결정성 + GUI 패키지 검사. 결과는 stdout JSON 한 개.

Windows·Linux(Docker) 양쪽에서 같은 명령으로 돌려 compare.py 로 비교한다.
  python tools/suite.py --workspace <작업폴더> --s0 <RA-01.ifc 가 있는 폴더> [--out <PNG 저장 폴더>]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

# 설치 메타데이터에 있으면 안 되는 GUI·디스플레이·GPU 배포 이름(소문자, 정규화 전 부분 일치 아님: 전체 일치/접두)
GUI_DISTS = ("pyqt", "pyside", "wxpython", "pygame", "pyglet", "kivy", "glfw", "pyopengl", "tk", "customtkinter", "dearpygui",
             "matplotlib", "vtk", "opencv-python", "pyrender", "moderngl", "vispy", "open3d", "wgpu", "pysdl2", "toga", "flet",
             "pywebview", "pystray", "pyautogui", "mss", "pillow-gui")
GUI_SO = ("libx11", "libxcb", "libgl.", "libglx", "libegl", "libgles", "libwayland", "libxext", "libxrender", "libsm.", "libice",
          "libgtk", "libqt", "libsdl", "libglfw", "libxi.", "libxrandr", "libxcursor", "libxfixes", "libdrm", "libgbm")


def gui_scan() -> dict:
    dists = sorted({d.metadata["Name"].lower() for d in md.distributions() if d.metadata["Name"]})
    bad = [n for n in dists if any(n == g or n.startswith(g + "-") or n.startswith(g) and g in ("pyqt", "pyside") for g in GUI_DISTS)]
    out = {"installed_distributions": dists, "gui_distributions_found": bad}
    try:
        import tkinter  # noqa: F401
        out["tkinter_importable"] = True
    except Exception as ex:   # noqa: BLE001
        out["tkinter_importable"] = False
        out["tkinter_error"] = type(ex).__name__
    out["display_env"] = {k: os.environ.get(k) for k in ("DISPLAY", "WAYLAND_DISPLAY") if os.environ.get(k)}
    # 공유 라이브러리 링크(Linux): site-packages 의 모든 .so 에 ldd
    if platform.system() == "Linux":
        sp = Path(md.distribution("ifcopenshell").locate_file(""))
        libs: dict[str, set] = {}
        missing: set[str] = set()
        for so in list(sp.rglob("*.so")) + list(sp.rglob("*.so.*")):
            try:
                p = subprocess.run(["ldd", str(so)], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            except Exception:   # noqa: BLE001
                continue
            for line in p.stdout.splitlines():
                name = line.strip().split(" ")[0]
                if name and "/" not in name:
                    libs.setdefault(name, set()).add(so.parent.name + "/" + so.name.split(".")[0])
                    if "not found" in line:
                        missing.add(name)
        flagged = {n: sorted(v)[:3] for n, v in libs.items() if any(n.lower().startswith(g) for g in GUI_SO)}
        out["ldd_libs"] = sorted(libs)
        out["ldd_not_found"] = sorted(missing)   # NEEDED 인데 이 환경에 없는 라이브러리(로드 시 실패할 수 있는 모듈 확인용)
        out["ldd_gui_libs_flagged"] = flagged
    return out


def run_ra01(workspace: Path, env_mode: str) -> dict:
    argv = [sys.executable, str(ROOT / "ra01_headless.py"), "--workspace", str(workspace), "--env-mode", env_mode, "--timing"]
    t0 = time.perf_counter()
    p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=900)
    dt = time.perf_counter() - t0
    lines = p.stdout.decode("utf-8", "replace").splitlines()
    res = {"exit": p.returncode, "stdout_lines": len(lines), "seconds": round(dt, 2)}
    try:
        res["json"] = json.loads(lines[0]) if len(lines) == 1 else None
    except ValueError:
        res["json"] = None
    res["stdout_is_single_json"] = res["json"] is not None
    if res["json"] is None:
        res["stderr_tail"] = p.stderr.decode("utf-8", "replace")[-400:]
    return res


def render_case(ifc: Path, out_png: Path | None, repeats: int, width: int) -> dict:
    from dobim_render.scene import render_ifc
    runs = []
    for i in range(repeats):
        r = render_ifc(str(ifc), width=width, view="iso", threads=1)
        runs.append(r)
        if i == 0 and out_png:
            out_png.write_bytes(r.png_bytes)
    t = [r.info["seconds"]["total"] for r in runs]
    ps = {r.info["pixel_sha256"] for r in runs}
    ms = {r.info["mesh_sha256"] for r in runs}
    first = runs[0].info
    # 스레드 수가 달라도 같은 메시인지
    r4 = render_ifc(str(ifc), width=width, view="iso", threads=4)
    return {"file": ifc.name, "width": width, "repeats": repeats, "info_first": first,
            "seconds_total": {"min": min(t), "median": round(statistics.median(t), 3), "max": max(t)},
            "deterministic_repeat": len(ps) == 1 and len(ms) == 1,
            "threads4_same_mesh": r4.info["mesh_sha256"] == first["mesh_sha256"],
            "threads4_same_pixels": r4.info["pixel_sha256"] == first["pixel_sha256"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--s0", required=True, help="RA-01.ifc 가 있는 폴더(선택: Building-Architecture.ifc 도)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--env-mode", default="inherit")
    ap.add_argument("--width", type=int, default=1280)
    a = ap.parse_args()
    ws = Path(a.workspace)
    ws.mkdir(parents=True, exist_ok=True)
    outdir = Path(a.out) if a.out else None
    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)
    import ifcopenshell
    res: dict = {"platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
                              "python": platform.python_version(), "ifcopenshell": ifcopenshell.version,
                              "cpu_count": os.cpu_count()}}
    import numpy
    res["platform"]["numpy"] = numpy.__version__
    res["gui_scan"] = gui_scan()
    res["ra01"] = run_ra01(ws / "ra01", a.env_mode)
    s0 = Path(a.s0)
    if outdir and (ws / "ra01" / "ra01.ifc").exists():
        import shutil
        shutil.copy(ws / "ra01" / "ra01.ifc", outdir / "core_ra01_export.ifc")   # 플랫폼 간 파일 차이 분석용
    cases = []
    ifc_core = ws / "ra01" / "ra01.ifc"
    for label, p in (("s0_RA-01", s0 / "RA-01.ifc"), ("core_ra01", ifc_core), ("foreign_Building-Architecture", s0 / "Building-Architecture.ifc")):
        if p.exists():
            c = render_case(p, (outdir / f"{label}.png") if outdir else None, a.repeats, a.width)
            c["label"] = label
            cases.append(c)
    res["render"] = cases
    from dobim_render import raster
    from dobim_render.synthetic import cube_mesh
    v, f, fe = cube_mesh()
    res["render_synthetic_cube_pixel_sha256"] = hashlib.sha256(raster.render(v, f, fe, ["IfcWall"], width=640).color.tobytes()).hexdigest()
    if platform.system() == "Linux":   # 렌더·지오메트리까지 마친 이 프로세스가 실제로 로드한 GUI/GL 공유 라이브러리
        names = set()
        for line in Path("/proc/self/maps").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 6 and ".so" in parts[-1]:
                base = Path(parts[-1]).name.lower()
                if any(base.startswith(g) for g in GUI_SO):
                    names.add(Path(parts[-1]).name)
        res["gui_scan"]["loaded_gui_libs_after_render"] = sorted(names)
    sys.stdout.buffer.write((json.dumps(res, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
