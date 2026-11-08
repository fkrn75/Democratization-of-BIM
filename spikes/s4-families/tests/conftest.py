# SPDX-License-Identifier: MPL-2.0
import copy
import json
import sys
from pathlib import Path

import pytest

S4 = Path(__file__).resolve().parent.parent
CORE = S4.parent / "s1-core"
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(CORE / "tools"))

LIB = CORE / "src" / "dobim_core" / "family" / "library"
LIBRARY_IDS = ["door.single", "window.fixed", "column.section", "wardrobe.builtin"]


def load_lib(fid: str) -> dict:
    return json.loads((LIB / f"{fid}.json").read_text(encoding="utf-8"))


@pytest.fixture
def door():
    return copy.deepcopy(load_lib("door.single"))


@pytest.fixture
def window():
    return copy.deepcopy(load_lib("window.fixed"))


@pytest.fixture
def wardrobe():
    return copy.deepcopy(load_lib("wardrobe.builtin"))


@pytest.fixture
def sess(tmp_path):
    import kit
    from dobim_core import fixture as F
    s = kit.Session(tmp_path / "t.dobim")
    for name, args in F.SCRIPT:
        assert s.run(name, args)["ok"]
    yield s
    s.close()
