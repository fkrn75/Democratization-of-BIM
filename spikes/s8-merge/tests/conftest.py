# SPDX-License-Identifier: MPL-2.0
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # s8lib 가 s1-core 의 src·tools 경로도 넣는다
import s8lib  # noqa: E402,F401
