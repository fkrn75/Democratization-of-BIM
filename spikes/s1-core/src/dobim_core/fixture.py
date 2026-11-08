# SPDX-License-Identifier: MPL-2.0
"""패리티 시험과 예시가 공유하는 고정 id 와 기준 프로젝트 스크립트.

예시(examples)는 이 id 를 참조한다. 시험 하네스는 이 스크립트로 같은 프로젝트를 만든 뒤
예시를 CLI·MCP 양쪽에 실행하고 결과를 비교한다.
"""


def _id(prefix: str, n: int) -> str:
    return prefix + f"{n:021d}"


LV1, LV2 = _id("3", 1), _id("3", 2)
W1, W2, W3 = _id("1", 1), _id("1", 2), _id("1", 3)
D1, D2 = _id("2", 1), _id("2", 2)
V1 = _id("4", 1)   # 뷰 예시용 고정 id (SCRIPT 에는 넣지 않는다: 기존 기준 해시를 바꾸지 않으려고)
GUID = "5d0b1a50-0000-4a00-8000-0d0b1a5000f1"

SCRIPT: list[tuple[str, dict]] = [
    ("level.create", {"id": LV1, "name": "1F", "elevation": 0}),
    ("level.create", {"id": LV2, "name": "2F", "elevation": 3300}),
    ("wall.create", {"id": W1, "level": LV1, "axis": [0, 0, 6000, 0], "thickness": 200, "height": 3000}),
    ("wall.create", {"id": W2, "level": LV1, "axis": [6000, 0, 6000, 4000], "thickness": 200, "height": 3000}),
    ("wall.create", {"id": W3, "level": LV1, "axis": [6000, 4000, 0, 4000], "thickness": 200, "height": 3000}),
    ("join.set", {"a": W1, "b": W2}),
    ("door.place", {"id": D1, "host": W1, "offset": 1500, "width": 900, "height": 2100}),
    ("door.place", {"id": D2, "host": W3, "offset": 3000, "width": 1000, "height": 2100}),
    ("pset.set", {"id": D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI30"}),
    ("material.set", {"id": W1, "name": "Concrete"}),
]
