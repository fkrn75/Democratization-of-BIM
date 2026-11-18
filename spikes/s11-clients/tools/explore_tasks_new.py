# SPDX-License-Identifier: MPL-2.0
"""탐색 시험(C-1 마감): 54개 측정 이후 새 커맨드를 쓰는 과제 15개와 자동 판정기.

explore_tasks.py 와 같은 규칙이다: 과제 문구에 커맨드 이름·도구 이름을 쓰지 않고, 판정은 실행이 끝난 뒤 CLI 로 상태를 조회해 한다.
요소는 이름으로 부른다. 새로 만든 요소를 가리킬 때는 종류와 위치로 설명한다.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import common as C
import explore_tasks as E
from dobim_core import fixture as F
from explore_tasks import PREAMBLE, ex, near, q

RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


def _mk(ws: Path, name: str, args: dict) -> dict:
    e = q(ws, name, args)
    assert e["ok"], (name, e)
    return e["result"]


def items(ws: Path, kind: str, project: str = "p.dobim") -> list[dict]:
    e = q(ws, "element.query", {"kind": kind}, project)
    return e["result"]["items"] if e["ok"] else []


def params(ws: Path, kind: str) -> list[dict]:
    return [(ex(ws, i["id"]) or {}).get("params", {}) for i in items(ws, kind)]


def setup_base(ws):
    C.build_fixture(ws)


def setup_box(ws):
    setup_base(ws)
    _mk(ws, "wall.create", {"id": "1" + "0" * 20 + "4", "level": F.LV1, "axis": [0, 4000, 0, 0], "thickness": 200, "height": 3000})


def setup_stair(ws):
    setup_base(ws)
    _mk(ws, "stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18})


def setup_roof(ws):
    setup_base(ws)
    _mk(ws, "roof.create", {"id": F.RF1, "level": F.LV2, "boundary": RECT, "form": "gable", "pitch": 30, "overhang": 600})


def setup_plan(ws):
    setup_base(ws)
    _mk(ws, "view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200})


def setup_section(ws):
    setup_roof(ws)
    _mk(ws, "view.create", {"id": F.V2, "name": "B-B 단면", "type": "section", "p1": [-1000, 2000], "p2": [7000, 2000]})
    _mk(ws, "wall.attach_top", {"id": F.W2, "targets": [F.RF1]})


def setup_beam(ws):
    setup_base(ws)
    _mk(ws, "solid.create", {"id": F.SO1, "level": F.LV1, "category": "beam", "form": "member", "start": [1000, 0, 2400],
                             "end": [2000, 0, 2400], "section": {"shape": "rect", "width": 200, "depth": 300}, "name": "인방 B1"})


def _one(ws, kind):
    return params(ws, kind)[0] if len(items(ws, kind)) == 1 else None


# 과제: (id, 문구, 준비, 판정)
TASKS = [
    ("N01", "1F 층에 바닥 슬래브를 하나 만들어라. 외곽은 (0,0), (6000,0), (6000,4000), (0,4000), 두께 200.", setup_base,
     lambda ws: (_one(ws, "slab") or {}).get("thickness") == 200 and len((_one(ws, "slab") or {}).get("boundary", [])) == 4),
    ("N02", "1F 에 계단을 하나 만들어라. 걸음선은 (1000,1000)에서 (5000,1000), 폭 1200, 오르는 높이 3000, 챌판(단) 18개.", setup_base,
     lambda ws: (_one(ws, "stair") or {}).get("risers") == 18 and (_one(ws, "stair") or {}).get("width") == 1200),
    ("N03", "1F 에 벽 4개로 완전히 둘러싸인 방이 하나 있다. 이 방을 모델의 공간(방)으로 등록해라.", setup_box,
     lambda ws: len(items(ws, "space")) == 1),
    ("N04", "1F 의 모든 벽 모서리가 서로 이어지도록(접합) 한꺼번에 처리해라. 이미 이어진 곳은 그대로 둔다.", setup_box,
     lambda ws: all(len((ex(ws, i["id"]) or {}).get("relations", {}).get("joins") or []) >= 2 for i in items(ws, "wall"))),
    ("N05", "2F 층 위에 박공지붕을 하나 올려라. 외곽은 (0,0), (6000,0), (6000,4000), (0,4000), 물매는 30도, 처마는 600 내밀어라.", setup_base,
     lambda ws: (_one(ws, "roof") or {}).get("form") == "gable" and (_one(ws, "roof") or {}).get("pitch") == 30
     and (_one(ws, "roof") or {}).get("overhang") == 600),
    ("N06", "1F 에 계단이 하나 있다. 계단 양옆에 난간을 만들어라.", setup_stair,
     lambda ws: len(items(ws, "railing")) >= 2),
    ("N07", "통심선(그리드선) X1 을 (0,-2000)에서 (0,12000)까지 만들어라.", setup_base,
     lambda ws: any(p.get("name") == "X1" and near(p.get("line"), [0, -2000, 0, 12000]) for p in params(ws, "grid"))),
    ("N08", "대지를 등록해라. 이름은 '시험대지', 경계는 (-6000,-6000), (18000,-6000), (18000,14000), (-6000,14000).", setup_base,
     lambda ws: any(p.get("name") == "시험대지" and len(p.get("boundary", [])) == 4 for p in params(ws, "site"))),
    ("N09", "벽 W-000001 을 바깥쪽부터 석고보드 20, 콘크리트 160, 치장벽돌 20 순서의 마감 층 구성으로 만들어라.", setup_base,
     lambda ws: [(l["material"], l["thickness"]) for l in (ex(ws, F.W1) or {}).get("params", {}).get("layers", [])]
     == [("석고보드", 20), ("콘크리트", 160), ("치장벽돌", 20)]),
    ("N10", "1F 에 승강기를 하나 설치해라. 승강로(샤프트) 안쪽 사각형은 (4000,500)-(5800,2300), 높이 6000.", setup_base,
     lambda ws: (_one(ws, "elevator") or {}).get("rise") == 6000),
    ("N11", "'1F 평면' 뷰에 (0,-1500)에서 (6000,-1500)까지 2D 직선을 하나 그려라(모델 요소가 아니라 뷰 위에 그리는 작도 선).", setup_plan,
     lambda ws: sum(1 for p in params(ws, "draft") if p.get("shape") == "line" and near(p.get("pts", [[]])[0] + p.get("pts", [[], []])[1], [0, -1500, 6000, -1500])) == 1),
    ("N12", "'1F 평면' 뷰 한 장으로 이루어진 도면 시트를 모델에 저장해 둬라. 용지 A3, 시트 번호 A-101, 이름 'A-101 1층 평면'.", setup_plan,
     lambda ws: any(i["name"] == "A-101 1층 평면" for i in items(ws, "sheet"))),
    ("N13", "'B-B 단면' 뷰에 높이 치수(층고와 최고 높이)를 자동으로 넣어라.", setup_section,
     lambda ws: len(q(ws, "annot.list", {"view": F.V2})["result"]["items"]) >= 2),
    ("N14", "벽 W-000002 의 윗면이 2F 위 지붕 면을 따라가도록 맞춰라.", setup_roof,
     lambda ws: bool((ex(ws, F.W2) or {}).get("params", {}).get("top_profile"))),
    ("N15", "'1F 평면' 뷰를 용지 A3 시트에 올려 sheet-out.svg 파일로 내보내라.", setup_plan,
     lambda ws: (ws / "sheet-out.svg").exists() and (ws / "sheet-out.svg").stat().st_size > 1000),
]


def prompt_of(t) -> str:
    return PREAMBLE + t[1]


def judge(t, ws: Path) -> bool:
    try:
        return bool(t[3](ws))
    except Exception:
        return False


if __name__ == "__main__":
    # 자가 검증: 준비 직후에는 판정이 모두 실패해야 한다(판정기가 항상 참이 아님을 증명)
    bad, errs = [], []
    for t in TASKS:
        ws = C.WORK / "explore-new-selftest" / t[0]
        shutil.rmtree(ws, ignore_errors=True)
        try:
            t[2](ws)
        except Exception as ex_:
            errs.append((t[0], str(ex_)[:200]))
            continue
        if judge(t, ws):
            bad.append(t[0])
    print(json.dumps({"always_true_judges": bad, "setup_errors": errs}, ensure_ascii=False))
    sys.exit(1 if bad or errs else 0)
