# SPDX-License-Identifier: MPL-2.0
"""탐색 시험: 커맨드 이름을 주지 않은 과제 20개와 자동 판정기.

판정은 모델의 자기 보고가 아니라 실행이 끝난 뒤 CLI 로 프로젝트 상태를 조회해 한다.
조회형 과제는 '답을 모델 안의 속성(Pset_Report)으로 기록'하게 해서 상태로 판정한다.
요소는 이름으로 부른다(W-000001 등). 과제 문구에는 커맨드 이름·도구 이름을 쓰지 않는다.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import common as C
from dobim_core import fixture as F

PREAMBLE = ("작업 폴더에 건축 모델 프로젝트 파일 p.dobim 이 있다. 요소는 이름으로 부른다: 벽 W-000001, W-000002, W-000003, "
            "문 D-000001(W-000001에 호스팅), D-000002(W-000003에 호스팅), 레벨 1F, 2F. 길이 단위는 mm 이다. "
            "연결된 BIM 도구(dobim MCP 서버)만 사용해서 아래 과제를 끝내라. 과제를 끝내면 한 줄로만 보고하라.\n\n과제: ")


def q(ws: Path, name, args=None, project="p.dobim"):
    e, _ = C.cli(ws, name, args or {}, project=project)
    return e


def ex(ws, i):
    e = q(ws, "element.get", {"id": i})
    return e["result"] if e["ok"] else None


def setup_base(ws: Path):
    C.build_fixture(ws)


def setup_moved(ws: Path):          # 이동 후 되돌림 직전 상태
    setup_base(ws)
    assert q(ws, "wall.move", {"id": F.W1, "dx": 100})["ok"]


def setup_undone(ws: Path):         # 이동 후 되돌린 상태
    setup_moved(ws)
    assert q(ws, "history.undo")["ok"]


def _export(ws: Path, path: str):
    e = q(ws, "ifc.export", {"path": path})
    t = e["error"]["details"]["confirm_token"]
    e, _ = C.cli(ws, "ifc.export", {"path": path}, project="p.dobim", confirm=t)
    assert e["ok"], e


def setup_exported(ws: Path):       # out.ifc 가 이미 있고 모델이 그 뒤에 바뀐 상태
    setup_base(ws)
    _export(ws, "out.ifc")
    (ws / "pre-export.bak").write_bytes((ws / "out.ifc").read_bytes())
    assert q(ws, "wall.move", {"id": F.W2, "dy": 50})["ok"]


def setup_exported_twice(ws: Path):  # 스냅숏 1(=첫 내보내기)이 있는 상태
    setup_exported(ws)
    _export(ws, "out.ifc")
    (ws / "first-export.bak").write_bytes((ws / ".dobim-snapshots" / "out.ifc.1").read_bytes())


def setup_empty(ws: Path):
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)


def pset(ws, i, p, k):
    r = ex(ws, i)
    return None if r is None else (r.get("psets", {}).get(p, {}) or {}).get(k)


def axis(ws, i):
    r = ex(ws, i)
    return None if r is None else r["params"]["axis"]


def near(a, b, t=1e-6):
    return a is not None and b is not None and len(a) == len(b) and all(abs(x - y) <= t for x, y in zip(a, b))


def exported_ok(ws, name, walls):
    f = ws / name
    if not f.exists():
        return False
    import ifcopenshell
    m = ifcopenshell.open(str(f))
    return len(m.by_type("IfcWall")) == walls


# (id, 문구, 준비, 판정). 판정은 (ws) -> bool.
TASKS = [
    ("T01", "벽 W-000001 을 가로 방향(+x)으로 500 만큼 옮겨라.", setup_base,
     lambda ws: near(axis(ws, F.W1), [500, 0, 6500, 0])),
    ("T02", "벽 W-000002 의 두께를 300 으로 바꿔라.", setup_base,
     lambda ws: (ex(ws, F.W2) or {}).get("params", {}).get("thickness") == 300),
    ("T03", "벽 W-000003 을 모델에서 없애라.", setup_base,
     lambda ws: ex(ws, F.W3) is None and ex(ws, F.D2) is None),
    ("T04", "벽 W-000001 과 W-000002 가 서로 연결(접합)되어 있다. 이 연결을 풀어라.", setup_base,
     lambda ws: not (ex(ws, F.W1) or {}).get("relations", {}).get("joins")),
    ("T05", "벽 W-000002 와 W-000003 의 끝을 서로 이어 붙여라(접합).", setup_base,
     lambda ws: F.W3 in ((ex(ws, F.W2) or {}).get("relations", {}).get("joins") or [])),
    ("T06", "문 D-000001 의 폭을 1000 으로 바꿔라.", setup_base,
     lambda ws: (ex(ws, F.D1) or {}).get("params", {}).get("width") == 1000),
    ("T07", "벽 W-000001 의 시작점에서 4000 떨어진 곳에 폭 900, 높이 2100 인 새 문을 달아라.", setup_base,
     lambda ws: any((ex(ws, i["id"]) or {}).get("params", {}).get("offset") == 4000
                    for i in q(ws, "element.query", {"kind": "door"})["result"]["items"])),
    ("T08", "'3F' 라는 이름의 새 층을 높이 6600 에 만들어라.", setup_base,
     lambda ws: any(i["name"] == "3F" for i in q(ws, "element.query", {"kind": "level"})["result"]["items"])),
    ("T09", "벽 W-000001 의 재료를 Brick 으로 지정해라.", setup_base,
     lambda ws: (ex(ws, F.W1) or {}).get("material") == "Brick"),
    ("T10", "문 D-000001 의 내화 등급(Pset_DoorCommon 의 FireRating)을 EI60 으로 바꿔라.", setup_base,
     lambda ws: pset(ws, F.D1, "Pset_DoorCommon", "FireRating") == "EI60"),
    ("T11", "문 D-000001 의 Pset_DoorCommon 속성 FireRating 을 완전히 지워라(값을 빈 문자열로 두는 것이 아니라 속성 자체를 제거).",
     setup_base, lambda ws: pset(ws, F.D1, "Pset_DoorCommon", "FireRating") is None and ex(ws, F.D1) is not None),
    ("T12", "방금 한 모델 변경(벽 이동)이 잘못이었다. 마지막 변경을 취소해서 원래대로 돌려놓아라.", setup_moved,
     lambda ws: near(axis(ws, F.W1), [0, 0, 6000, 0])),
    ("T13", "벽 이동을 취소했었는데 마음이 바뀌었다. 취소했던 변경을 다시 적용해라.", setup_undone,
     lambda ws: near(axis(ws, F.W1), [100, 0, 6100, 0])),
    ("T14", "현재 모델을 IFC 파일 out.ifc 로 내보내라.", setup_base,
     lambda ws: exported_ok(ws, "out.ifc", 3)),
    ("T15", "out.ifc 가 이미 있다. 현재 모델로 out.ifc 를 덮어써라(이전 파일은 잃지 않게).", setup_exported,
     lambda ws: (ws / ".dobim-snapshots" / "out.ifc.1").exists()
     and (ws / ".dobim-snapshots" / "out.ifc.1").read_bytes() == (ws / "pre-export.bak").read_bytes()
     and (ws / "out.ifc").read_bytes() != (ws / "pre-export.bak").read_bytes()),
    ("T16", "out.ifc 를 두 번 내보냈다. 처음 내보냈던 내용으로 out.ifc 를 되돌려라.", setup_exported_twice,
     lambda ws: (ws / "out.ifc").read_bytes() == (ws / "first-export.bak").read_bytes()),
    ("T17", "모델에 문이 몇 개 있는지 세어서, 그 숫자를 벽 W-000001 의 사용자 속성 Pset_Report 의 DoorCount 로 기록해라.", setup_base,
     lambda ws: pset(ws, F.W1, "Pset_Report", "DoorCount") == 2),
    ("T18", "모든 벽의 길이 합계(mm)를 구해서 벽 W-000001 의 사용자 속성 Pset_Report 의 TotalWallLength 로 기록해라.", setup_base,
     lambda ws: pset(ws, F.W1, "Pset_Report", "TotalWallLength") in (16000, 16000.0)),
    ("T19", "벽 W-000001 과 W-000002 를 둘 다 +x 방향으로 100 옮겨라. 둘 중 하나라도 실패하면 둘 다 반영되지 않게, 한 번에 처리하고 "
            "나중에 한 번에 취소할 수 있게 해라.", setup_base,
     lambda ws: near(axis(ws, F.W1), [100, 0, 6100, 0]) and near(axis(ws, F.W2), [6100, 0, 6100, 4000])
     and _one_txn(ws)),
    ("T20", "작업 폴더에 new.dobim 이라는 새 프로젝트를 만들고, 거기에 '1F'(높이 0) 층과 (0,0)-(5000,0) 축선의 벽 하나(두께 200, 높이 3000)를 만들어라.",
     setup_empty,
     lambda ws: (ws / "new.dobim").exists() and len(q(ws, "element.query", {"kind": "wall"}, "new.dobim")["result"]["items"]) == 1
     and any(i["name"] == "1F" for i in q(ws, "element.query", {"kind": "level"}, "new.dobim")["result"]["items"])),
]


def _one_txn(ws) -> bool:
    """두 이동이 같은 트랜잭션인가: 마지막 로그 항목 하나를 되돌리면 두 벽이 모두 원위치여야 한다."""
    c = ws.parent / (ws.name + "-undo-check")
    shutil.rmtree(c, ignore_errors=True)
    shutil.copytree(ws, c)
    if not q(c, "history.undo")["ok"]:
        return False
    return near(axis(c, F.W1), [0, 0, 6000, 0]) and near(axis(c, F.W2), [6000, 0, 6000, 4000])


def prompt_of(t) -> str:
    return PREAMBLE + t[1]


def judge(t, ws: Path) -> bool:
    try:
        return bool(t[3](ws))
    except Exception:
        return False


if __name__ == "__main__":
    # 자가 검증: 준비 직후에는 판정이 모두 실패해야 한다(판정기가 항상 참이 아님을 증명)
    import sys
    bad = []
    for t in TASKS:
        ws = C.WORK / "explore-selftest" / t[0]
        t[2](ws)
        if t[0] in ("T12", "T13"):
            pass
        if judge(t, ws):
            bad.append(t[0])
    print(json.dumps({"always_true_judges": bad}))
    sys.exit(1 if bad else 0)
