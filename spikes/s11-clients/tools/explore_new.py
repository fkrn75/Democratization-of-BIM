# SPDX-License-Identifier: MPL-2.0
"""탐색 시험(C-1 마감): explore.py 와 같은 실행기로 새 커맨드 과제(explore_tasks_new.py)를 돌린다.

사용: python explore_new.py <claude|codex-desktop|codex-npm> <out.json> [--only N01,N02] [--workers N]
"""
from __future__ import annotations

import sys

import explore
import explore_tasks_new as EN

explore.E = EN          # run_one 이 E.TASKS·E.prompt_of·E.judge 를 쓴다

if __name__ == "__main__":
    sys.exit(explore.main())
