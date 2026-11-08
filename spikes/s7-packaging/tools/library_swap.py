# SPDX-License-Identifier: MPL-2.0
"""S7 합격 기준 3: LGPL 라이브러리(ifcopenshell) 교체 시험 (ADR-0003 규칙 2).

설치된 onedir 폴더의 파일만 바꿔서(재빌드 없이) 다른 빌드의 ifcopenshell 로 동작하는지 본다.
  E1 코드 수정 가능성: _internal/ifcopenshell/guid.py 에 표식 한 줄을 덧붙이면 실제로 실행되는가
     (PYZ 안에 묶이면 파일을 바꿔도 반영되지 않는다 -> LGPL 의무 미충족)
  E2 휠 통째 교체: 같은 버전 재설치본(0.9.0.post1), 다른 패치(0.9.0), 이전 마이너(0.8.5)의 ifcopenshell/ 폴더로 바꾼 뒤
     기준 프로젝트의 의미 해시 3종과 model.validate 가 같은가
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
from common import FROZEN_DIR, RESULTS, S1, S1_SRC, run_quiet, write_json  # noqa: E402
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
        return json.loads(p.stdout.decode("utf-8")), p.stderr.decode("utf-8", "replace"), p.returncode
    except ValueError:
        return {"ok": False, "raw": p.stdout.decode("utf-8", "replace")[:300]}, p.stderr.decode("utf-8", "replace")[-600:], p.returncode


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


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else FROZEN_DIR
    outname = sys.argv[2] if len(sys.argv) > 2 else "s7_library_swap.json"
    work = Path(tempfile.mkdtemp(prefix="s7-swap-"))
    out: dict = {"source_dir_is_py_mode": (src / "_internal" / "ifcopenshell" / "guid.py").exists()}
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

        same = [x for x in e2 if x["wheel"].startswith("ifcopenshell-0.9.0.post1")]
        out["pass"] = bool(e1["pass"] and same and same[0]["run_ok"] and same[0]["hash_equal_to_baseline"])
        out["summary"] = {
            "py_modification_effective": e1["pass"],
            "same_version_reinstall_ok": bool(same and same[0].get("hash_equal_to_baseline")),
            "other_versions": {x["wheel"]: ("해시 동일" if x.get("hash_equal_to_baseline") else ("실행 실패" if not x["run_ok"] else "해시 다름")) for x in e2},
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
    write_json(RESULTS / outname, out)
    print(json.dumps(out.get("summary", out), ensure_ascii=False, indent=1), "pass=", out.get("pass"))
    return 0 if out.get("pass") else 1


if __name__ == "__main__":
    sys.exit(main())
