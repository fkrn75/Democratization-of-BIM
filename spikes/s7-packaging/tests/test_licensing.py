# SPDX-License-Identifier: MPL-2.0
"""라이선스 판정기(licensing.py)와 고지 생성기의 금지 검사 시험.

금지 검사가 '조용히 항상 통과'하지 않는다는 음성 대조가 핵심이다.
"""
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
import licensing  # noqa: E402


@pytest.mark.parametrize("expr,verdict", [
    ("MIT", "ok"),
    ("Apache-2.0 OR BSD-3-Clause", "ok"),
    ("BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0", "ok"),
    ("LGPL-3.0-or-later", "weak"),
    ("MPL-2.0", "weak"),
    ("LGPL-2.1-only WITH OCCT-exception-1.0", "weak"),
    ("GPL-3.0-only", "forbid"),
    ("GPL-2.0-or-later", "forbid"),
    ("AGPL-3.0-or-later", "forbid"),
    ("MIT AND GPL-3.0-only", "forbid"),          # AND 는 가장 위험한 쪽
    ("MIT OR GPL-3.0-only", "ok"),               # OR 는 고를 수 있는 쪽
    ("GPL-3.0-or-later OR LGPL-3.0-only", "weak"),
    ("GPL-2.0-or-later WITH Bootloader-exception", "weak"),           # 허용 목록의 예외절
    ("GPL-3.0-or-later WITH Classpath-exception-2.0", "forbid"),      # 허용 목록에 없는 예외절은 풀리지 않는다
    ("(MIT OR Apache-2.0) AND BSD-3-Clause", "ok"),
    ("Some-Weird-License", "unknown"),
    ("", "unknown"),
    ("unknown", "unknown"),
])
def test_evaluate(expr, verdict):
    assert licensing.evaluate(expr)[0] == verdict


def test_lgpl_is_not_mistaken_for_gpl():
    assert licensing.classify_id("LGPL-2.1-or-later") == "weak"
    assert licensing.classify_id("GPL-2.1-or-later") == "forbid"


def _run(*extra):
    return subprocess.run([sys.executable, str(TOOLS / "gen_notices.py"), "--no-write", *extra],
                          capture_output=True, text=True, encoding="utf-8")


@pytest.mark.skipif(not (TOOLS.parent / "build" / "dist" / "dobim" / "_internal").exists(), reason="동결본이 없다")
def test_generator_fails_on_injected_gpl_and_passes_clean():
    clean = _run()
    assert clean.returncode == 0, clean.stdout[-600:]
    bad = _run("--inject", "evil=GPL-3.0-only")
    assert bad.returncode == 1 and "evil" in bad.stdout
    ok = _run("--inject", "dual=MIT OR GPL-3.0-only")
    assert ok.returncode == 0
