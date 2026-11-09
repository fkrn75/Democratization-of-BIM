# SPDX-License-Identifier: MPL-2.0
"""S7 합격 기준 3: LGPL 라이브러리(ifcopenshell) 교체 시험 (ADR-0003 규칙 2).

설치된 onedir 폴더의 파일만 바꿔서(재빌드 없이) 다른 빌드의 ifcopenshell 로 동작하는지 본다.
  E1 코드 수정 가능성: _internal/ifcopenshell/guid.py 에 표식 한 줄을 덧붙이면 실제로 실행되는가
     (PYZ 안에 묶이면 파일을 바꿔도 반영되지 않는다 -> LGPL 의무 미충족)
  E2 휠 통째 교체: 같은 버전 재설치본(0.9.0.post1), 다른 패치(0.9.0), 이전 마이너(0.8.5)의 ifcopenshell/ 폴더로 바꾼 뒤
     기준 프로젝트의 의미 해시 3종과 model.validate 가 같은가
  E3 GEOS(LGPL, shapely 휠 동봉) 교체: 코어는 shapely 를 import 하지 않아 dobim.exe 로는 GEOS 를 부를 수 없다. 그래서 같은 버전 휠 파일(동결본 DLL 과
     해시 동일 확인)을 임시 폴더에 두고 shapely 휠을 다른 버전으로 *파일만* 바꾼 뒤, 격리 인터프리터(-I -S, 파이썬 3.12)로 그 폴더만 경로에 올려
     shapely.geos_version 과 연산 결과를 확인한다. '파일 교체가 로드에 반영되는가'만 보며 동결 인터프리터 안의 교체는 시험하지 못했다(JSON 의 scope).
인자: 동결 폴더(기본 build/dist/dobim)  결과 JSON 이름
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FROZEN_DIR, RESULTS, S1, S1_SRC, dec, run_quiet, write_json  # noqa: E402
sys.path.insert(0, str(S1_SRC))
from dobim_core import fixture as F  # noqa: E402

WHEELS = Path(os.environ.get("S7_WHEELS", Path.home() / "dobim-s7-wheels"))
MARK = "S7-SWAP-MARKER"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def call(exe: Path, ws: Path, name, args, project=None):
    cmd = [exe, "--workspace", ws, "--actor", "swap", "run", name, "--json", json.dumps(args)]
    if project:
        cmd += ["-p", project]
    p = run_quiet(cmd, cwd=ws, env={**os.environ, "PYTHONUTF8": "1"})
    try:
        return json.loads(dec(p.stdout)), dec(p.stderr), p.returncode
    except ValueError:
        return {"ok": False, "raw": dec(p.stdout)[:300]}, dec(p.stderr)[-600:], p.returncode


def fixture_run(exe: Path, work: Path) -> dict:
    """기준 프로젝트를 이 exe 로 처음부터 만들어 해시·검증을 돌려준다. 실패하면 이유를 돌려준다."""
    ws = work / "ws"
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    e, err, rc = call(exe, ws, "project.new", {"path": "f.dobim", "guid": F.GUID})
    if not e.get("ok"):
        return {"ok": False, "stage": "project.new", "stderr_tail": err[-400:], "rc": rc}
    for name, args in F.SCRIPT:
        e, err, rc = call(exe, ws, name, args, "f.dobim")
        if not e.get("ok"):
            return {"ok": False, "stage": name, "stderr_tail": err[-400:], "error": e.get("error", e)}
    h, err, rc = call(exe, ws, "model.hash", {}, "f.dobim")
    v, _, _ = call(exe, ws, "model.validate", {"express_rules": True}, "f.dobim")
    if not h.get("ok"):
        return {"ok": False, "stage": "model.hash", "stderr_tail": err[-400:]}
    r = h["result"]
    return {"ok": True, "hash": {k: r[k] for k in ("state", "derived", "ifc")}, "validate_ok": v.get("ok"),
            "validate": v.get("result")}


def ifc_version(exe: Path, work: Path) -> str:
    """동결본이 실제로 불러온 ifcopenshell 의 버전(해당 폴더의 version 문자열 파일에서)."""
    p = exe.parent / "_internal" / "ifcopenshell" / "__init__.py"
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.startswith("version") or "__version__" in ln:
            return ln.strip()[:80]
    return "?"


GEOS_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import shapely
from shapely.geometry import Point, Polygon
a = Point(0, 0).buffer(10, quad_segs=16).area
b = Polygon([(0, 0), (4, 0), (4, 3)]).intersection(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])).area
print(json.dumps({"shapely": shapely.__version__, "geos": list(shapely.geos_version), "buffer_area": round(a, 9), "intersection_area": b,
                  "shapely_file_under_stage": sys.argv[1].lower() in shapely.__file__.lower()}))
"""


def geos_swap(src: Path, work: Path) -> dict:
    """E3: shapely 휠(GEOS 동봉)을 파일 교체만으로 바꿔도 로드되는가. 범위 한계는 머리말 참고.

    동결 폴더에는 shapely/numpy 의 .py 가 없다(PYZ 안). 그래서 *같은 버전의 휠 파일*을 빌드 venv 에서 가져와 쓰고,
    그 DLL·pyd 가 동결 폴더의 것과 바이트까지 같은지를 해시로 확인한다(같은 파일 위에서 시험했다는 증거).
    """
    import hashlib
    from importlib import metadata
    wheels = sorted(WHEELS.glob("shapely-*.whl"))
    internal = src / "_internal"
    if not wheels:
        return {"skipped": "S7_WHEELS 폴더에 shapely 휠이 없다(pip download --no-deps --only-binary=:all: --python-version 3.12 --platform win_amd64 shapely==2.1.2)"}
    if not (internal / "Shapely.libs").exists():
        return {"skipped": "동결 폴더에 shapely 가 없다"}
    sp = Path(metadata.distribution("shapely").locate_file(""))
    h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()   # noqa: E731
    frozen_dlls = {f.name: h(f) for f in (internal / "Shapely.libs").glob("*.dll")}
    venv_dlls = {f.name: h(f) for f in (sp / "shapely.libs").glob("*.dll")}
    same_files = frozen_dlls == venv_dlls and bool(frozen_dlls)

    def stage(tag: str) -> Path:
        d = work / ("geos_" + tag)
        d.mkdir(parents=True)
        for name in ("shapely", "shapely.libs", "numpy", "numpy.libs"):
            shutil.copytree(sp / name, d / name)
        return d

    def probe(d: Path) -> dict:
        p = run_quiet([sys.executable, "-I", "-S", "-c", GEOS_PROBE, str(d)], cwd=work)
        try:
            return json.loads(dec(p.stdout).strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"error": dec(p.stderr)[-400:], "rc": p.returncode}

    res = {"scope": "빌드 venv 의 shapely 2.2.0·numpy 파일(동결 폴더의 GEOS DLL 과 바이트 동일: 아래 확인)을 임시 폴더에 복사하고, "
                    "격리 파이썬 3.12(-I -S)로 그 폴더만 경로에 올려 shapely 휠을 파일만 바꿔 본다. "
                    "동결 인터프리터 안에서의 교체는 시험하지 못했다: 코어가 shapely 를 부르지 않아 dobim.exe 로 GEOS 를 부를 수 없고, "
                    "동결본에서 shapely 의 .py 는 PYZ 안에 있어 휠 폴더 교체와 PYZ 의 옛 .py 가 어긋날 수 있다.",
           "frozen_geos_dlls_identical_to_venv": same_files, "baseline": probe(stage("base"))}
    runs = []
    for wheel in wheels:
        d = stage(wheel.name.split("-")[1])
        before = sorted(f.name for f in (d / "shapely.libs").glob("*.dll"))
        shutil.rmtree(d / "shapely")
        shutil.rmtree(d / "shapely.libs")
        with zipfile.ZipFile(wheel) as z:
            for n in z.namelist():
                if n.endswith("/") or not (n.startswith("shapely/") or n.lower().startswith("shapely.libs/")):
                    continue
                t = d / n
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_bytes(z.read(n))
        libs = d / "shapely.libs"
        runs.append({"wheel": wheel.name, "geos_dlls_before": before,
                     "geos_dlls_after": sorted(f.name for f in libs.glob("*.dll")) if libs.exists() else [], "probe": probe(d)})
    res["swaps"] = runs
    base = res["baseline"]
    ok_runs = [r for r in runs if "error" not in r["probe"]]
    res["summary"] = {
        "frozen_geos_dlls_identical_to_venv": same_files,
        "baseline_geos": base.get("geos"),
        "swapped_geos_versions": {r["wheel"]: r["probe"].get("geos", "로드 실패") for r in runs},
        "loaded_other_geos_by_file_swap": any(r["probe"].get("geos") != base.get("geos") for r in ok_runs),
        "buffer_and_intersection_same_as_baseline": all(r["probe"].get("buffer_area") == base.get("buffer_area")
                                                        and r["probe"].get("intersection_area") == base.get("intersection_area") for r in ok_runs),
        "pass": bool(ok_runs) and len(ok_runs) == len(runs) and "error" not in base and same_files,
    }
    return res


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else FROZEN_DIR
    outname = sys.argv[2] if len(sys.argv) > 2 else "s7_library_swap.json"
    work = Path(tempfile.mkdtemp(prefix="s7-swap-"))
    # guid.py 파일이 *있는 것*과 *그 파일이 실제로 쓰이는 것*은 다르다: PYZ 모드 빌드에도 파일이 데이터로 따라 들어올 수 있다.
    # 실제로 파일이 우선인지는 E1 결과(effective_loader)로 판정한다.
    out: dict = {"guid_py_file_present_in_source_dir": (src / "_internal" / "ifcopenshell" / "guid.py").exists()}
    try:
        def fresh(tag: str) -> Path:
            d = work / tag
            shutil.copytree(src, d)
            return d / "dobim.exe"

        base_exe = fresh("base")
        base = fixture_run(base_exe, work / "w_base")
        out["baseline"] = base
        assert base["ok"], base

        # E1: .py 수정이 반영되는가
        exe = fresh("e1")
        guid_py = exe.parent / "_internal" / "ifcopenshell" / "guid.py"
        e1 = {"guid_py_exists_as_file": guid_py.exists()}
        if guid_py.exists():
            guid_py.write_text(guid_py.read_text(encoding="utf-8") + f"\nimport sys as _s\n_s.stderr.write('{MARK}\\n')\n", encoding="utf-8")
            ws = work / "w_e1"
            ws.mkdir()
            call(exe, ws, "project.new", {"path": "f.dobim", "guid": F.GUID})
            _, err, _ = call(exe, ws, "model.hash", {}, "f.dobim")
            e1["marker_seen_on_stderr"] = MARK in err
        else:
            e1["marker_seen_on_stderr"] = False
        e1["pass"] = bool(e1["marker_seen_on_stderr"])
        out["E1_python_source_modification_takes_effect"] = e1
        out["effective_loader"] = ("파일 우선: .py 수정이 반영된다(module_collection_mode=py)" if e1["pass"]
                                   else "PYZ 우선: 파일이 있어도 수정이 반영되지 않는다 -> LGPL 교체 가능 의무 미충족")

        # E2: 휠 통째 교체
        e2 = []
        for wheel in sorted(WHEELS.glob("ifcopenshell-*.whl")):
            ver = wheel.name.split("-")[1]
            exe = fresh("e2_" + ver)
            dst = exe.parent / "_internal" / "ifcopenshell"
            old_files = sum(1 for _ in dst.rglob("*") if _.is_file())
            shutil.rmtree(dst)
            with zipfile.ZipFile(wheel) as z:
                names = [n for n in z.namelist() if n.startswith("ifcopenshell/") and not n.endswith("/")]
                for n in names:
                    t = exe.parent / "_internal" / n
                    t.parent.mkdir(parents=True, exist_ok=True)
                    t.write_bytes(z.read(n))
            rec = {"wheel": wheel.name, "files_before": old_files, "files_after": sum(1 for _ in dst.rglob("*") if _.is_file())}
            r = fixture_run(exe, work / ("w_e2_" + ver))
            rec["run_ok"] = r["ok"]
            # 실제로 로드된 ifcopenshell 버전을 확인한다: guid.py 끝에 버전 출력 한 줄을 덧붙여 stderr 로 받는다
            gp = dst / "guid.py"
            # ifcopenshell 의 초기화 중에 guid 가 import 되므로 종료 시점(atexit)에 버전을 읽는다
            probe = ["", "import atexit as _a, sys as _s",
                     "_a.register(lambda: _s.stderr.write('LOADED-VERSION=' + str(_s.modules['ifcopenshell'].version) + chr(10)))", ""]
            gp.write_text(gp.read_text(encoding="utf-8") + chr(10).join(probe), encoding="utf-8")
            _, err, _ = call(exe, work / ("w_e2_" + ver) / "ws", "model.hash", {}, "f.dobim")
            loaded = [ln.split("=", 1)[1].strip() for ln in err.splitlines() if ln.startswith("LOADED-VERSION=")]
            rec["loaded_version"] = loaded[0] if loaded else None
            if r["ok"]:
                rec["hash_equal_to_baseline"] = r["hash"] == base["hash"]
                rec["validate_ok"] = r["validate_ok"]
                rec["hash"] = r["hash"]
            else:
                rec["failure"] = {k: r.get(k) for k in ("stage", "stderr_tail", "error")}
            e2.append(rec)
        out["E2_wheel_swap"] = e2

        e3 = geos_swap(src, work)
        out["E3_geos_swap"] = e3

        same = [x for x in e2 if x["wheel"].startswith("ifcopenshell-0.9.0.post1")]
        out["pass"] = bool(e1["pass"] and same and same[0]["run_ok"] and same[0]["hash_equal_to_baseline"])
        out["summary"] = {
            "py_modification_effective": e1["pass"],
            "same_version_reinstall_ok": bool(same and same[0].get("hash_equal_to_baseline")),
            "geos_swap": e3.get("summary", e3.get("skipped")),
            "other_versions": {x["wheel"]: ("해시 동일" if x.get("hash_equal_to_baseline") else ("실행 실패" if not x["run_ok"] else "해시 다름")) for x in e2},
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
    write_json(RESULTS / outname, out)
    print(json.dumps(out.get("summary", out), ensure_ascii=False, indent=1), "pass=", out.get("pass"))
    return 0 if out.get("pass") else 1


if __name__ == "__main__":
    sys.exit(main())
