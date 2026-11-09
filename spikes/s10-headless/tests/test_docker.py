# SPDX-License-Identifier: MPL-2.0
"""깨끗한 Linux(Docker): 이미지 빌드는 네트워크가 필요하다. 실행은 --network none. 기본 실행에서는 `-m "not docker"` 로 제외한다."""
import json
import shutil
import subprocess
import sys

import pytest

from conftest import ROOT, S0

pytestmark = [pytest.mark.docker, pytest.mark.slow]


def docker_up() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, timeout=30).returncode == 0
    except Exception:   # noqa: BLE001
        return False


@pytest.mark.skipif(not docker_up(), reason="Docker 데몬이 없다")
@pytest.mark.parametrize("dockerfile,tag", [("docker/Dockerfile", "dobim-s10-pytest-debian:tmp"),
                                            ("docker/Dockerfile.ubuntu", "dobim-s10-pytest-ubuntu:tmp")])
def test_깨끗한_리눅스에서_완주하고_해시가_기록값과_같다(tmp_path, expected, dockerfile, tag):
    out = tmp_path / "linux.json"
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "docker_run.py"), "--dockerfile", dockerfile, "--tag", tag,
                        "--out", str(out)], capture_output=True, text=True, timeout=1800)
    assert p.returncode == 0, p.stderr[-600:]
    res = json.loads(out.read_text(encoding="utf-8"))
    s = res["suite"]
    assert res["suite_exit"] == 0 and res["suite_stdout_lines"] == 1
    probe = "\n".join(res["env_probe"])
    assert "(GUI/GL" in probe and "DISPLAY=[]" in probe and "unreachable" in probe   # GUI 패키지 없음·DISPLAY 없음·네트워크 없음
    assert s["gui_scan"]["gui_distributions_found"] == []
    r = s["ra01"]["json"]
    assert s["ra01"]["exit"] == 0 and r["ok"] and r["all_stdout_json_only"]
    assert r["hashes"] == expected["ra01"]["hashes"]
    assert r["ifc_file"]["data_sha256"] == expected["ra01"]["data_sha256"]
    ra = next(c for c in s["render"] if c["label"] == "s0_RA-01")
    assert ra["seconds_total"]["max"] < 10.0 and ra["deterministic_repeat"]
    assert ra["info_first"]["pixel_sha256"] == expected["render"]["s0_ra01"]["pixel_sha256"]
