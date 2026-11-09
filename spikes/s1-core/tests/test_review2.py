# SPDX-License-Identifier: MPL-2.0
"""2차 검수 지적(N1 삭제 뒤 실패 원자성, N2 스냅숏 번호, N3 스냅숏 폴더 탈출, N4 값 검증)과 경계 골든 회귀."""
import os
import subprocess
import sys

import pytest

import golden
import kit
from dobim_core import fixture as F


def confirmed(sess, name, args):
    first = sess.run(name, args)
    assert first["error"]["code"] == "CONFIRM_REQUIRED", first
    return sess.run(name, args, confirm=first["error"]["details"]["confirm_token"])


def test_속성_쓰기가_지운_뒤_실패해도_원래_값이_남고_undo가_맞다_그리고_값_검증(tmp_path):
    assert golden.check_failure_atomicity(tmp_path) == []


def test_골든_경계_포함(tmp_path):
    assert golden.check_golden(tmp_path) == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 2**63, -2**63 - 1, "a\x00b", "\ud800"])
def test_저장_불가_속성_값은_스키마_오류로_상태를_바꾸지_않는다(base, bad):
    base.run("pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": 1})
    before = base.eng.hashes()
    r = base.run("pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": bad})
    assert r["error"]["code"] == "SCHEMA_INVALID" and r["error"]["path"].startswith("/value")
    assert base.eng.hashes() == before


def test_이름의_NUL도_거부된다(base):
    assert base.run("level.create", {"name": "a\x00b", "elevation": 0})["error"]["code"] == "SCHEMA_INVALID"
    assert base.run("material.set", {"id": F.W1, "name": "x\ud800"})["error"]["code"] == "SCHEMA_INVALID"


def test_스냅숏_폴더의_비ASCII_숫자_이름이_내보내기를_막지_않는다(base):
    ws = base.path.parent
    assert confirmed(base, "ifc.export", {"path": "o.ifc"})["ok"]
    snaps = ws / ".dobim-snapshots"
    snaps.mkdir(exist_ok=True)
    (snaps / "o.ifc.²").write_text("x", encoding="utf-8")
    (snaps / "o.ifc.٣").write_text("x", encoding="utf-8")        # 아랍-인도 숫자도 isdigit 가 참이다
    r = confirmed(base, "ifc.export", {"path": "o.ifc"})
    assert r["ok"] and r["result"]["snapshot"] == ".dobim-snapshots/o.ifc.1"
    assert [x["n"] for x in base.run("ifc.snapshots", {"path": "o.ifc"})["result"]["items"]] == [1]


def _link(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if sys.platform != "win32":
            pytest.skip("심볼릭 링크를 만들 수 없다")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_스냅숏_폴더가_작업_폴더_밖을_가리키면_거부한다(base, tmp_path_factory):
    ws = base.path.parent
    outside = tmp_path_factory.mktemp("outside")
    assert confirmed(base, "ifc.export", {"path": "j.ifc"})["ok"]
    _link(ws / ".dobim-snapshots", outside)
    before = sorted(p.name for p in outside.iterdir())
    r = base.run("ifc.export", {"path": "j.ifc"})
    assert r["error"]["code"] == "PATH_DENIED", r
    r = base.run("ifc.snapshots", {"path": "j.ifc"})
    assert r["error"]["code"] == "PATH_DENIED"
    assert sorted(p.name for p in outside.iterdir()) == before          # 밖에는 아무것도 쓰지 않았다


def test_스냅숏_폴더_이름_검사는_대소문자를_구분하지_않는다(base):
    for name in (".DOBIM-SNAPSHOTS/a.ifc", ".Dobim-Snapshots/a.ifc"):
        assert base.run("ifc.export", {"path": name})["error"]["code"] == "PATH_DENIED"
