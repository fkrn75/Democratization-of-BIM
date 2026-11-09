# SPDX-License-Identifier: MPL-2.0
"""합격 기준: 무인(stdin 닫힘·DISPLAY 없음·최소 환경)에서 RA-01급 스크립트 완주, 종료 코드 0, stdout JSON 한 줄, 해시 일치."""
import json
import subprocess
import sys

import pytest

from conftest import ROOT, child_env


def run_ra01(ws, *, closed_stdin_posix=False, env=None):
    argv = [sys.executable, str(ROOT / "ra01_headless.py"), "--workspace", str(ws), "--python", sys.executable,
            "--env-mode", "minimal", "--timing"]
    kw = {"capture_output": True, "env": env or child_env(), "timeout": 600}
    if closed_stdin_posix and sys.platform != "win32":
        sh = " ".join(f"'{a}'" for a in argv)
        p = subprocess.run(["sh", "-c", f"exec 0<&-; exec {sh}"], **kw)   # fd 0 자체를 닫는다
    else:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, **kw)
    lines = p.stdout.decode("utf-8").splitlines()
    return p, lines


@pytest.mark.slow
def test_ra01_완주_종료코드0_stdout은_JSON한줄(tmp_path, expected):
    p, lines = run_ra01(tmp_path / "ws")
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-500:]
    assert len(lines) == 1
    out = json.loads(lines[0])
    assert out["ok"] is True and out["all_stdout_json_only"] is True and out["spec_counts_ok"] is True
    assert out["stats"] == {"door": 5, "level": 2, "wall": 14}
    assert out["steps"]["model.validate"]["errors"] == 0
    # 파괴적 커맨드의 확인 토큰 2단계가 무인에서도 동작(미리보기 종료 코드 6 -> 확인 후 0)
    assert out["steps"]["ifc.export.preview"]["exit"] == 6 and out["steps"]["ifc.export"]["exit"] == 0
    # 의미 해시 3종 + IFC 파일 정규화 해시가 기록된 기대값과 같다 (Windows·Linux 공통 값)
    assert out["hashes"] == expected["ra01"]["hashes"]
    assert out["ifc_file"]["data_sha256"] == expected["ra01"]["data_sha256"]
    assert out["ifc_file"]["globalid_sha256"] == expected["ra01"]["globalid_sha256"]
    # 모든 자식 호출의 stdout 이 JSON 한 줄이었다
    assert all(c["stdout_json_only"] for c in out["child_calls"])


@pytest.mark.slow
@pytest.mark.skipif(sys.platform == "win32", reason="fd 0 을 닫는 시험은 POSIX 전용(Windows 는 DEVNULL 로 시험)")
def test_ra01_stdin_fd를_닫아도_완주(tmp_path, expected):
    p, lines = run_ra01(tmp_path / "ws", closed_stdin_posix=True)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-500:]
    assert json.loads(lines[0])["hashes"] == expected["ra01"]["hashes"]


@pytest.mark.slow
def test_ra01_두_번_실행해도_의미해시와_정규화_파일해시가_같다(tmp_path):
    a = json.loads(run_ra01(tmp_path / "a")[1][0])
    b = json.loads(run_ra01(tmp_path / "b")[1][0])
    assert a["hashes"] == b["hashes"]
    assert a["ifc_file"]["data_sha256"] == b["ifc_file"]["data_sha256"]
    # 정직한 기록: 원시 파일 바이트는 헤더 시각·무작위 GlobalId 때문에 같지 않을 수 있다(해시 정규화의 이유)
    assert a["ifc_file"]["bytes"] > 0


def test_스크립트는_코어가_지원하는_커맨드만_쓴다():
    import ra01_headless
    from dobim_core import registry
    names = set(registry.load())
    used = {c["command"] for c in ra01_headless.build_script()}
    assert used <= names, used - names
    assert len(ra01_headless.build_script()) == 76
