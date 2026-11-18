# SPDX-License-Identifier: MPL-2.0
"""layer_c_new.py 의 묶음 지시문이 실제로 수행 가능한지 CLI 로 한 번 실행해 보는 점검(LLM 없음, 토큰 0).

지시문 순서 그대로 커맨드를 호출한다. 한 단계라도 실패하면 그 묶음 지시문은 모델이 따를 수 없는 것이라 시험 설계 오류다.
사용: python layer_c_new_ref.py
"""
from __future__ import annotations

import json
import sys

import common as C
import layer_c_new as N
from dobim_core import fixture as F

RECT = N.RECT


class Ref:
    def __init__(self, group: str):
        self.ws = C.WORK / "layerC-ref" / group
        N.prepare(group, self.ws)
        self.log: list[tuple[str, bool, str]] = []

    def run(self, name: str, args: dict, confirm: bool = False) -> dict:
        e, _ = C.cli(self.ws, name, args, project="p.dobim")
        if confirm and not e["ok"] and e["error"]["code"] == "CONFIRM_REQUIRED":
            e, _ = C.cli(self.ws, name, args, project="p.dobim", confirm=e["error"]["details"]["confirm_token"])
        self.log.append((name, e["ok"], "" if e["ok"] else json.dumps(e["error"], ensure_ascii=False)[:200]))
        return e.get("result") or {}


def g9():
    r = Ref("G9")
    r.run("join.auto", {"level": F.LV1})
    r.run("space.from_walls", {"level": F.LV1, "all": True})
    sp = r.run("space.create", {"level": F.LV1, "boundary": [[7000, 0], [9000, 0], [9000, 2000], [7000, 2000]], "name": "창고"})["id"]
    r.run("space.set", {"id": sp, "category": "창고"}); r.run("space.move", {"id": sp, "dx": 600}); r.run("space.delete", {"id": sp})
    s = r.run("slab.create", {"level": F.LV2, "boundary": RECT, "thickness": 200})["id"]
    r.run("slab.set", {"id": s, "thickness": 250})
    r.run("slab.layers", {"id": s, "layers": [{"material": "철근콘크리트", "thickness": 200}, {"material": "마감 모르타르", "thickness": 50}]})
    r.run("slab.move", {"id": s, "dx": 600}); r.run("slab.delete", {"id": s})
    r.run("wall.layers", {"id": F.W1, "layers": [{"material": "석고보드", "thickness": 20}, {"material": "콘크리트", "thickness": 160}, {"material": "치장벽돌", "thickness": 20}]})
    return r


def g10():
    r = Ref("G10")
    st = r.run("stair.create", {"level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18})["id"]
    r.run("stair.set", {"id": st, "risers": 17}); r.run("stair.move", {"id": st, "dx": 600}); r.run("railing.from_stair", {"stair": st, "side": "both"})
    r.run("stair.delete", {"id": st})
    rl = r.run("railing.create", {"level": F.LV1, "path": [[0, 0], [3000, 0]], "height": 1100})["id"]
    r.run("railing.set", {"id": rl, "height": 1200}); r.run("railing.move", {"id": rl, "dx": 600}); r.run("railing.from_slab", {"slab": F.S1})
    r.run("railing.delete", {"id": rl})
    el = r.run("elevator.create", {"level": F.LV1, "shaft": [4000, 500, 5800, 2300], "rise": 6000})["id"]
    r.run("elevator.set", {"id": el, "cab": [1400, 1600, 2300]}); r.run("elevator.cut_slabs", {"id": el}); r.run("elevator.move", {"id": el, "dx": -600})
    r.run("elevator.delete", {"id": el})
    return r


def g11():
    r = Ref("G11")
    rf = r.run("roof.create", {"level": F.LV2, "boundary": RECT, "form": "gable", "pitch": 30, "overhang": 600})["id"]
    r.run("wall.attach_top", {"id": F.W2, "targets": [rf]}); r.run("wall.detach_top", {"id": F.W2})
    r.run("wall.cut_by", {"id": F.W1, "targets": [F.SO1]})
    r.run("roof.set", {"id": rf, "pitch": 25}); r.run("roof.move", {"id": rf, "dx": 600}); r.run("roof.delete", {"id": rf})
    so = r.run("solid.create", {"level": F.LV1, "category": "buttress", "form": "prism", "polygon": [[0, -900], [600, -900], [600, -100], [0, -100]], "z0": 0, "z1": 2400})["id"]
    r.run("solid.set", {"id": so, "z1": 3000}); r.run("solid.move", {"id": so, "dx": 600}); r.run("solid.delete", {"id": so})
    return r


def g12():
    r = Ref("G12")
    si = r.run("site.create", {"boundary": [[-6000, -6000], [18000, -6000], [18000, 14000], [-6000, 14000]], "name": "대지"})["id"]
    r.run("site.set", {"id": si, "north_angle": 30}); r.run("site.delete", {"id": si})
    g = r.run("grid.create", {"name": "X1", "line": [0, -2000, 0, 12000]})["id"]
    r.run("grid.set", {"id": g, "line": [0, -3000, 0, 12000]}); r.run("grid.move", {"id": g, "dx": 300}); r.run("grid.delete", {"id": g})
    r.run("family.place_corner", {"family": "window.storefront", "walls": [F.W1, F.W2], "legs": [2500, 2000], "type_values": {"height": 2400, "bay_max": 1200}})
    return r


def g13():
    r = Ref("G13")
    r.run("drawing.profile", {}); r.run("draft.layer.set", {"name": "A-TEST", "weight": "thin", "linetype": "dashed"}); r.run("draft.layer.list", {})
    a = r.run("draft.create", {"view": F.V1, "shape": "line", "pts": [[0, -1500], [6000, -1500]]})["id"]
    b = r.run("draft.create", {"view": F.V1, "shape": "line", "pts": [[4000, -2000], [4000, -1000]]})["id"]
    c = r.run("draft.create", {"view": F.V1, "shape": "line", "pts": [[8000, -2000], [8000, -1000]]})["id"]
    r.run("draft.trim", {"id": a, "boundary": b, "end": "end"}); r.run("draft.extend", {"id": a, "boundary": c, "end": "end"})
    r.run("draft.set", {"id": a, "layer": "A-TEST", "color": "#c00000"}); r.run("draft.list", {"view": F.V1})
    r.run("draft.group", {"ids": [a, b], "group": "상세 A"}); r.run("draft.move", {"view": F.V1, "group": "상세 A", "dx": 500})
    r.run("draft.copy", {"ids": [a], "dy": 1000, "count": 2}); r.run("draft.rotate", {"ids": [b], "center": [3000, -1500], "angle": 90})
    r.run("draft.mirror", {"ids": [c], "axis": [[0, 0], [1000, 0]], "keep": True}); r.run("draft.offset", {"id": c, "distance": 300})
    r.run("draft.delete", {"ids": [c]}); r.run("draft.layer.delete", {"name": "A-TEST"})
    return r


def g14():
    r = Ref("G14")
    r.run("annot.dim.heights", {"view": F.V2})
    sh = r.run("sheet.define", {"name": "A-101 1층 평면", "views": [F.V1], "paper": "A3", "project_name": "시험 프로젝트", "sheet_number": "A-101"})["id"]
    r.run("sheet.list", {}); r.run("sheet.render", {"id": sh, "path": "sheet-saved.svg"}, confirm=True)
    r.run("sheet.export", {"views": [F.V1], "path": "sheet-direct.svg", "paper": "A3", "sheet_number": "A-102"}, confirm=True)
    r.run("sheet.index", {"path": "index.svg", "paper": "A1", "sheet_number": "A-000", "tables": ["drawing_list", "legend"]}, confirm=True)
    r.run("view.image", {"id": F.V1, "max_px": 800}); r.run("sheet.image", {"id": sh, "max_px": 800}); r.run("sheet.delete", {"id": sh})
    return r


def main() -> int:
    bad, seen = [], set()
    for fn in (g9, g10, g11, g12, g13, g14):
        r = fn()
        fails = [x for x in r.log if not x[1]]
        seen |= {n for n, ok, _ in r.log if ok}
        print(fn.__name__, len(r.log), "호출", "실패", fails, flush=True)
        bad += fails
    missing = sorted(set(N.new_commands()) - seen)
    print(json.dumps({"failures": len(bad), "commands_not_succeeded": missing}, ensure_ascii=False))
    return 1 if bad or missing else 0


if __name__ == "__main__":
    sys.exit(main())
