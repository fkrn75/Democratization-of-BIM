# SPDX-License-Identifier: MPL-2.0
"""탐색 판정기의 양성 대조: 정답 동작을 CLI 로 직접 수행하면 판정기가 참을 내는지 확인한다(판정기가 항상 거짓이 아님을 증명).
음성 대조(준비 직후엔 모두 거짓)는 explore_tasks.py 의 자가 검증이 한다."""
import json
import sys
from pathlib import Path

import common as C
import explore_tasks as E
from dobim_core import fixture as F

P = "p.dobim"


def run(ws, n, a=None, project=P, confirm=None):
    e, _ = C.cli(ws, n, a or {}, project=project, confirm=confirm)
    return e


def export(ws, path):
    e = run(ws, "ifc.export", {"path": path})
    t = e["error"]["details"]["confirm_token"]
    return run(ws, "ifc.export", {"path": path}, confirm=t)


ORACLE = {
    "T01": lambda ws: run(ws, "wall.move", {"id": F.W1, "dx": 500}),
    "T02": lambda ws: run(ws, "wall.set", {"id": F.W2, "thickness": 300}),
    "T03": lambda ws: run(ws, "element.delete", {"id": F.W3}),
    "T04": lambda ws: run(ws, "join.clear", {"a": F.W1, "b": F.W2}),
    "T05": lambda ws: run(ws, "join.set", {"a": F.W2, "b": F.W3}),
    "T06": lambda ws: run(ws, "door.set", {"id": F.D1, "width": 1000}),
    "T07": lambda ws: run(ws, "door.place", {"host": F.W1, "offset": 4000, "width": 900, "height": 2100}),
    "T08": lambda ws: run(ws, "level.create", {"name": "3F", "elevation": 6600}),
    "T09": lambda ws: run(ws, "material.set", {"id": F.W1, "name": "Brick"}),
    "T10": lambda ws: run(ws, "pset.set", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI60"}),
    "T11": lambda ws: run(ws, "pset.delete", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating"}),
    "T12": lambda ws: run(ws, "history.undo"),
    "T13": lambda ws: run(ws, "history.redo"),
    "T14": lambda ws: export(ws, "out.ifc"),
    "T15": lambda ws: export(ws, "out.ifc"),
    "T16": lambda ws: (lambda e: run(ws, "ifc.restore", {"path": "out.ifc", "snapshot": 1}, confirm=e["error"]["details"]["confirm_token"]))(
        run(ws, "ifc.restore", {"path": "out.ifc", "snapshot": 1})),
    "T17": lambda ws: run(ws, "pset.set", {"id": F.W1, "pset": "Pset_Report", "prop": "DoorCount", "value": 2}),
    "T18": lambda ws: run(ws, "pset.set", {"id": F.W1, "pset": "Pset_Report", "prop": "TotalWallLength", "value": 16000}),
    "T19": lambda ws: C.cli(ws, "history.list", {}, project=P) and __import__("subprocess").run(
        [str(C.PY), "-m", "dobim_core", "--workspace", str(ws), "script", str(_batch(ws)), "-p", P, "--atomic"],
        capture_output=True, env={**__import__("os").environ, **C.server_env()}, stdin=__import__("subprocess").DEVNULL),
    "T20": lambda ws: (run(ws, "project.new", {"path": "new.dobim"}, project=None),
                       run(ws, "level.create", {"name": "1F", "elevation": 0}, project="new.dobim"),
                       run(ws, "wall.create", {"level": run(ws, "element.query", {"kind": "level"}, project="new.dobim")["result"]["items"][0]["id"],
                                               "axis": [0, 0, 5000, 0], "thickness": 200, "height": 3000}, project="new.dobim")),
}


def _batch(ws: Path) -> Path:
    f = ws / "_b.jsonl"
    f.write_text("\n".join(json.dumps(c) for c in [{"command": "wall.move", "args": {"id": F.W1, "dx": 100}},
                                                  {"command": "wall.move", "args": {"id": F.W2, "dx": 100}}]), encoding="utf-8")
    return f


def main(out: str) -> int:
    res = {}
    for t in E.TASKS:
        ws = C.WORK / "explore-controls" / t[0]
        t[2](ws)
        ORACLE[t[0]](ws)
        res[t[0]] = E.judge(t, ws)
    wrong = [k for k, v in res.items() if not v]
    Path(out).write_text(json.dumps({"positive_control": res, "judge_false_despite_correct_action": wrong}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(json.dumps({"judge_false_despite_correct_action": wrong}))
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
