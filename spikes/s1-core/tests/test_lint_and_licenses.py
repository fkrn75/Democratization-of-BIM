# SPDX-License-Identifier: MPL-2.0
"""의존 방향(import-linter)과 의존성 라이선스. 린트 자체도 위반 시험 커밋으로 증명한다."""
import shutil
import subprocess
import sys

from conftest import ROOT
import license_scan


def lint(cwd):
    exe = ROOT / ".venv" / "Scripts" / "lint-imports.exe"
    cmd = [str(exe)] if exe.exists() else [sys.executable, "-m", "importlinter"]
    return subprocess.run(cmd, capture_output=True, cwd=cwd, env={**__import__("os").environ, "PYTHONPATH": "src"}, timeout=120)


def test_의존_방향_계약이_지켜진다():
    p = lint(ROOT)
    assert p.returncode == 0, p.stdout.decode("utf-8", "replace")[-800:]


def test_린트는_역방향_의존을_넣은_시험_커밋을_잡아낸다(tmp_path):
    work = tmp_path / "core"
    shutil.copytree(ROOT / "src", work / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "pyproject.toml", work / "pyproject.toml")
    for victim, line in (("queries.py", "import tkinter\n"),                              # GUI 의존
                         ("graph.py", "from dobim_core import engine\n"),                  # 순수 질의가 엔진을 안다
                         ("commands.py", "import ifcopenshell\n"),                         # 커맨드가 IFC 를 직접 만진다
                         ("mcp_server.py", "from dobim_core import cli\n")):               # 서버가 CLI 를 안다
        f = work / "src" / "dobim_core" / victim
        original = f.read_text(encoding="utf-8")
        f.write_text(original + "\n" + line, encoding="utf-8")
        p = lint(work)
        assert p.returncode != 0, f"{victim} 에 {line.strip()} 를 넣었는데 린트가 통과했다"
        f.write_text(original, encoding="utf-8")
    assert lint(work).returncode == 0


def test_의존성에_gpl_agpl이_없다():
    res = license_scan.scan()
    assert res["forbidden_found"] == []
    names = {r["name"].lower() for r in res["runtime"]}
    assert {"ifcopenshell", "pydantic", "mcp"} <= names
    lic = {r["name"].lower(): r["license"] for r in res["runtime"]}
    assert "LGPL" in lic["ifcopenshell"]            # LGPL 은 허용(ADR-0003), 동봉 시 교체 가능성·고지 의무가 따른다
