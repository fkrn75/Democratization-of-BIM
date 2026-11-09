# SPDX-License-Identifier: MPL-2.0
"""설치 메타데이터 트리에 GUI·디스플레이 패키지가 없는가 (architecture.md 15.9) — 현재 시험 환경 대상."""
import suite


def test_설치된_배포_중_GUI_패키지가_없다():
    r = suite.gui_scan()
    assert r["gui_distributions_found"] == [], r["gui_distributions_found"]


def test_코어_의존성_닫힘에_GUI가_없다():
    """dobim-core 의 requires 를 재귀로 따라가며 GUI 배포 이름이 없는지."""
    import importlib.metadata as md
    import re
    seen, todo = set(), ["dobim-core"]
    while todo:
        n = todo.pop().lower().replace("_", "-")
        if n in seen:
            continue
        try:
            reqs = md.requires(n) or []
        except md.PackageNotFoundError:
            continue
        seen.add(n)
        for r in reqs:
            if "extra ==" in r:
                continue
            todo.append(re.split(r"[ ;<>=!~\[(]", r, 1)[0])
    assert not (seen & set(suite.GUI_DISTS)), seen & set(suite.GUI_DISTS)
    assert {"ifcopenshell", "pydantic", "mcp"} <= seen
