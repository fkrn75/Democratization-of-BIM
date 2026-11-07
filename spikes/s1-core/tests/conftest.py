# SPDX-License-Identifier: MPL-2.0
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import kit  # noqa: E402
from dobim_core import fixture as F  # noqa: E402


@pytest.fixture
def sess(tmp_path):
    s = kit.Session(tmp_path / "t.dobim")
    yield s
    s.close()


@pytest.fixture
def base(sess):
    """기준 프로젝트(fixture.SCRIPT)가 만들어진 세션."""
    for name, args in F.SCRIPT:
        assert sess.run(name, args)["ok"]
    return sess
