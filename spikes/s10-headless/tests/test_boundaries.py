# SPDX-License-Identifier: MPL-2.0
"""의존 방향(import-linter) 음성 시험: 역방향 의존을 넣은 '시험 커밋'을 린트가 잡아내는가.
저장소에는 아무것도 커밋하지 않는다. 임시 폴더에 사본을 만들고 임시 git 저장소에서 기준 커밋 + 위반 커밋을 만든다."""
import os
import shutil
import subprocess
import sys

import pytest

from conftest import ROOT, S1

CODE = "import sys; from importlinter.cli import lint_imports_command; sys.exit(lint_imports_command())"
IGN = shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", "results", "tests", ".pytest_cache", "docker", "*.egg-info")

pytest.importorskip("importlinter")


def git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)


def lint(work):
    env = {**os.environ, "PYTHONUTF8": "1",
           "PYTHONPATH": os.pathsep.join([str(work / "s1-core" / "src"), str(work / "s10" / "src")])}
    return subprocess.run([sys.executable, "-c", CODE], cwd=work / "s10", env=env, capture_output=True, timeout=180)


@pytest.fixture
def repo(tmp_path):
    w = tmp_path / "repo"
    shutil.copytree(S1, w / "s1-core", ignore=IGN)
    shutil.copytree(ROOT, w / "s10", ignore=IGN)
    git(w, "init", "-q")
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "기준")
    return w


def test_저장소_현재_상태는_계약을_모두_지킨다():
    # 사본이 아니라 실제 트리에서 실행
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": os.pathsep.join([str(S1 / "src"), str(ROOT / "src")])}
    p = subprocess.run([sys.executable, "-c", CODE], cwd=ROOT, env=env, capture_output=True, timeout=180)
    assert p.returncode == 0, p.stdout.decode("utf-8", "replace")[-800:]


CASES = [
    ("코어 엔진이 렌더 패키지를 import", "s1-core/src/dobim_core/engine.py", "\nimport dobim_render\n"),
    ("코어가 렌더 scene 을 from-import", "s1-core/src/dobim_core/cli.py", "\nfrom dobim_render import scene\n"),
    ("코어 순수 질의가 GUI(tkinter)", "s1-core/src/dobim_core/queries.py", "\nimport tkinter\n"),
    ("코어가 PyQt6", "s1-core/src/dobim_core/dispatch.py", "\nimport PyQt6.QtWidgets\n"),
    ("코어가 GPU 라이브러리(wgpu)", "s1-core/src/dobim_core/graph.py", "\nimport wgpu\n"),
    ("코어가 numpy 를 직접 import", "s1-core/src/dobim_core/limits.py", "\nimport numpy\n"),
    ("렌더가 코어 엔진을 import", "s10/src/dobim_render/mesh.py", "\nfrom dobim_core import engine\n"),
    ("렌더 래스터가 ifcopenshell", "s10/src/dobim_render/raster.py", "\nimport ifcopenshell\n"),
    ("렌더 PNG 가 mesh 모듈", "s10/src/dobim_render/png.py", "\nfrom dobim_render import mesh\n"),
    ("렌더가 GUI(PyQt6)", "s10/src/dobim_render/scene.py", "\nimport PyQt6\n"),
    ("렌더가 MCP 계층", "s10/src/dobim_render/__main__.py", "\nimport mcp\n"),
]


@pytest.mark.parametrize("label,path,line", CASES, ids=[c[0] for c in CASES])
def test_역방향_의존_시험_커밋을_린트가_잡아낸다(repo, label, path, line):
    assert lint(repo).returncode == 0                      # 기준 커밋은 통과
    f = repo / path
    f.write_text(f.read_text(encoding="utf-8") + line, encoding="utf-8")
    git(repo, "commit", "-q", "-am", f"시험 커밋: {label}")
    p = lint(repo)
    assert p.returncode != 0, f"{label}: 린트가 통과했다"
    git(repo, "reset", "-q", "--hard", "HEAD~1")           # 시험 커밋 되돌림 -> 다시 통과
    assert lint(repo).returncode == 0


def test_간접_의존도_잡는다(repo):
    """코어 commands.py 가 새 도우미 모듈을 import 하고, 그 도우미가 렌더를 import (두 단계)."""
    core = repo / "s1-core" / "src" / "dobim_core"
    (core / "helper_tmp.py").write_text("import dobim_render\n", encoding="utf-8")
    cmd = core / "commands.py"
    cmd.write_text(cmd.read_text(encoding="utf-8") + "\nfrom dobim_core import helper_tmp\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "시험 커밋: 간접 의존")
    assert lint(repo).returncode != 0
