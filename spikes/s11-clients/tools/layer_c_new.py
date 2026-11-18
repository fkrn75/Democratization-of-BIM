# SPDX-License-Identifier: MPL-2.0
"""층 C 재측정(C-1 마감): 54개 측정(aef8de2) 이후 새로 생긴 커맨드 71개만 묶음별로 실제 클라이언트에 시킨다.

판정 방식은 layer_c.py 와 같다(클라이언트 출력에서 뽑은 도구 호출과 서버 응답 봉투로 판정, 모델 자기 보고 아님).
새 커맨드 목록은 레지스트리 차이(`new_commands()`)로 정한다: 54개 측정 때의 레지스트리 스냅숏(results/s11_c1close_registry_54.json)에 없는 이름.

사용: python layer_c_new.py <claude|codex-desktop|codex-npm> <out.json> [그룹...]
      python layer_c_new.py selftest        (LLM 없이 준비·커맨드 목록 덮음만 점검)
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import common as C
import layer_c as L
from dobim_core import fixture as F
from dobim_core import registry

HERE = Path(__file__).resolve().parent
REG54 = HERE.parent / "results" / "s11_c1close_registry_54.json"

COMMON = L.COMMON

# 사각형 외곽(6000 x 4000)
RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]

GROUPS = {
    "G9": {
        "title": "조인·공간·슬래브·벽 층",
        "setup": "box",
        "commands": ["join.auto", "space.from_walls", "space.create", "space.set", "space.move", "space.delete",
                     "slab.create", "slab.set", "slab.layers", "slab.move", "slab.delete", "wall.layers"],
        "prompt": COMMON + (
            "작업 폴더의 p.dobim 은 1층(1F)에 벽 4개(W-000001, W-000002, W-000003, 그리고 마지막 벽 (0,4000)-(0,0))가 사각형으로 둘러싼 모델이다. "
            "층은 1F(고도 0)와 2F(고도 3300)다. 아래를 순서대로 하라(괄호는 커맨드 이름).\n"
            "1. 1F 벽의 모서리를 자동으로 이어 붙이기(join.auto)\n"
            "2. 1F 벽으로 닫힌 영역을 찾아 공간을 자동 생성(space.from_walls)\n"
            "3. 공간을 직접 만들기: 1F, 경계 (7000,0),(9000,0),(9000,2000),(7000,2000), 이름 '창고'(space.create). "
            "그 공간의 분류를 '창고'로 변경(space.set), +x 600 이동(space.move), 삭제(space.delete)\n"
            "4. 슬래브: 2F 레벨에 경계 (0,0),(6000,0),(6000,4000),(0,4000), 두께 200(slab.create). 두께를 250 으로 변경(slab.set), "
            "두께 층 구성을 철근콘크리트 200 + 마감 모르타르 50 으로 지정(slab.layers), +x 600 이동(slab.move), 삭제(slab.delete)\n"
            "5. 벽 W-000001 마감 층 구성을 석고보드 20, 콘크리트 160, 치장벽돌 20 으로 지정(wall.layers)"),
    },
    "G10": {
        "title": "계단·난간·승강기",
        "setup": "stairslab",
        "commands": ["stair.create", "stair.set", "stair.move", "stair.delete",
                     "railing.create", "railing.set", "railing.move", "railing.delete", "railing.from_slab", "railing.from_stair",
                     "elevator.create", "elevator.set", "elevator.move", "elevator.cut_slabs", "elevator.delete"],
        "prompt": COMMON + (
            "작업 폴더의 p.dobim 은 벽 3개, 문 2개, 층 1F(고도 0)·2F(고도 3300)와 2F 층의 슬래브 하나(바닥 6000x4000)가 든 모델이다. "
            "아래를 순서대로 하라(괄호는 커맨드 이름). 새로 만든 요소는 응답에 돌아온 id 를 이어서 써라.\n"
            "1. 계단: 1F, 걸음선 (1000,1000)-(5000,1000), 폭 1200, 높이 3000, 챌판 18개(stair.create). 챌판을 17개로 변경(stair.set), "
            "+x 600 이동(stair.move), 그 계단 양옆에 난간 자동 생성(railing.from_stair), 계단 삭제(stair.delete)\n"
            "2. 난간: 1F, 난간선 (0,0)-(3000,0), 높이 1100(railing.create). 높이를 1200 으로 변경(railing.set), +x 600 이동(railing.move). "
            "2F 슬래브의 바깥 가장자리에 난간 자동 생성(railing.from_slab). 직접 만든 난간 삭제(railing.delete)\n"
            "3. 승강기: 1F, 샤프트 사각형 (4000,500)-(5800,2300), 높이 6000(elevator.create). 카 크기를 1400x1600x2300 으로 변경(elevator.set), "
            "2F 슬래브에 샤프트 모양 구멍 내기(elevator.cut_slabs), -x 600 이동(elevator.move: dx -600), 승강기 삭제(elevator.delete)"),
    },
    "G11": {
        "title": "지붕·솔리드·벽 상단/잘라내기",
        "setup": "beam",
        "commands": ["roof.create", "roof.set", "roof.move", "roof.delete", "solid.create", "solid.set", "solid.move", "solid.delete",
                     "wall.attach_top", "wall.detach_top", "wall.cut_by"],
        "prompt": COMMON + (
            "작업 폴더의 p.dobim 은 1F 벽 3개(W-000001..3), 문 2개, 층 1F(고도 0)·2F(고도 3300), 그리고 벽 W-000001 문 위쪽의 인방(보) 솔리드 하나가 든 모델이다. "
            "아래를 순서대로 하라(괄호는 커맨드 이름). 새로 만든 요소는 응답에 돌아온 id 를 이어서 써라.\n"
            "1. 지붕: 2F 층에 외곽 (0,0),(6000,0),(6000,4000),(0,4000), 박공(gable), 물매 30도, 처마 내밀기 600(roof.create)\n"
            "2. 벽 W-000002 상단을 그 지붕에 붙이기(wall.attach_top), 다시 떼기(wall.detach_top)\n"
            "3. 벽 W-000001 에서 인방이 차지한 부피를 빼기(wall.cut_by)\n"
            "4. 지붕 물매를 25도로 변경(roof.set), +x 600 이동(roof.move), 지붕 삭제(roof.delete)\n"
            "5. 솔리드: 1F, 형태 prism, 범주 buttress, 평면 다각형 (0,-900),(600,-900),(600,-100),(0,-100), 높이 z0 0 ~ z1 2400(solid.create). "
            "z1 을 3000 으로 변경(solid.set), +x 600 이동(solid.move), 삭제(solid.delete). 인방 솔리드는 건드리지 마라"),
    },
    "G12": {
        "title": "대지·통심선·전개 창호",
        "setup": "fixture",
        "commands": ["site.create", "site.set", "site.delete", "grid.create", "grid.set", "grid.move", "grid.delete", "family.place_corner"],
        "prompt": COMMON + (
            "작업 폴더의 p.dobim 은 벽 3개(W-000001..3), 문 2개, 층 1F·2F 가 든 모델이다. 아래를 순서대로 하라(괄호는 커맨드 이름). "
            "새로 만든 요소는 응답에 돌아온 id 를 이어서 써라.\n"
            "1. 대지: 경계 (-6000,-6000),(18000,-6000),(18000,14000),(-6000,14000), 이름 '대지'(site.create). 방위를 30도로 변경(site.set), 삭제(site.delete)\n"
            "2. 통심선: 이름 X1, 선분 (0,-2000)-(0,12000)(grid.create). 선분을 (0,-3000)-(0,12000) 으로 변경(grid.set), +x 300 이동(grid.move), 삭제(grid.delete)\n"
            "3. 직각으로 이어지는 벽 W-000001 과 W-000002 에 걸쳐 상점 앞 창(window.storefront 패밀리)을 모서리를 돌아 한 단위로 배치"
            "(family.place_corner): 두 다리 길이 2500, 2000, 창 높이 2400, 칸 최대 폭 1200"),
    },
    "G13": {
        "title": "2D 작도",
        "setup": "planview",
        "commands": ["drawing.profile", "draft.layer.set", "draft.layer.list", "draft.layer.delete", "draft.create", "draft.set", "draft.list",
                     "draft.group", "draft.move", "draft.copy", "draft.rotate", "draft.mirror", "draft.offset", "draft.trim", "draft.extend",
                     "draft.delete"],
        "prompt": COMMON + (
            "작업 폴더의 p.dobim 은 벽 3개, 문 2개, 층 1F·2F 와 평면 뷰 '1F 평면'이 든 모델이다. 2D 작도 요소는 뷰 좌표(mm)를 쓴다. "
            "아래를 순서대로 하라(괄호는 커맨드 이름). 새로 만든 요소는 응답에 돌아온 id 를 이어서 써라.\n"
            "1. 도면 작성기준 프로파일 조회(drawing.profile)\n"
            "2. 작도 층 스타일 'A-TEST' 를 선굵기 thin, 선종류 dashed 로 만들기(draft.layer.set), 작도 층 목록 조회(draft.layer.list)\n"
            "3. 평면 뷰에 직선 3개를 만들기(draft.create): 선 A (0,-1500)-(6000,-1500), 선 B (4000,-2000)-(4000,-1000), 선 C (8000,-2000)-(8000,-1000)\n"
            "4. 선 A 의 끝(end)을 선 B 와 만나는 곳에서 자르기(draft.trim), 이어서 선 A 의 같은 끝을 선 C 와 만나는 곳까지 늘이기(draft.extend)\n"
            "5. 선 A 를 층 'A-TEST' 로 옮기고 색을 #c00000 으로 변경(draft.set), 평면 뷰의 작도 요소 목록 조회(draft.list)\n"
            "6. 선 A 와 B 를 '상세 A' 그룹으로 묶기(draft.group), 그 그룹을 dx 500 이동(draft.move), 선 A 를 dy 1000, 개수 2 로 복사(draft.copy)\n"
            "7. 선 B 를 점 (3000,-1500) 중심으로 90도 회전(draft.rotate), 선 C 를 축 (0,0)-(1000,0) 에 대해 원본을 두고 대칭 복사(draft.mirror), "
            "선 C 를 300 만큼 평행 복사(draft.offset)\n"
            "8. 선 C 를 삭제(draft.delete), 작도 층 'A-TEST' 스타일 삭제(draft.layer.delete)"),
    },
    "G14": {
        "title": "시트·이미지·높이 치수",
        "setup": "views",
        "commands": ["annot.dim.heights", "sheet.define", "sheet.list", "sheet.render", "sheet.export", "sheet.index",
                     "view.image", "sheet.image", "sheet.delete"],
        "prompt": COMMON + (
            "작업 폴더의 p.dobim 은 벽 3개, 문 2개, 층 1F·2F, 평면 뷰 '1F 평면'과 단면 뷰 'B-B 단면'이 든 모델이다. "
            "아래를 순서대로 하라(괄호는 커맨드 이름). 파일을 내보내는 커맨드는 되돌릴 수 없어 확인 절차가 있다: 미리보기 결과의 토큰으로 확정하라.\n"
            "1. 단면 뷰에 높이 치수(층고·최고 높이) 자동 배치(annot.dim.heights)\n"
            "2. 시트 정의를 모델에 저장: 이름 'A-101 1층 평면', 평면 뷰 한 장, 용지 A3, 프로젝트명 '시험 프로젝트', 시트 번호 A-101(sheet.define). "
            "저장된 시트 정의 목록 조회(sheet.list)\n"
            "3. 저장한 시트 정의로 sheet-saved.svg 내보내기(sheet.render)\n"
            "4. 저장하지 않고 평면 뷰로 시트를 바로 구성해 용지 A3, 시트 번호 A-102 로 sheet-direct.svg 내보내기(sheet.export)\n"
            "5. 도면목록·범례가 든 색인 시트를 용지 A1, 시트 번호 A-000 으로 index.svg 로 내보내기(sheet.index)\n"
            "6. 평면 뷰를 이미지로 보기(view.image), 저장한 시트를 이미지로 보기(sheet.image). 이미지를 돌려주는 도구가 있으면 그것을 써라\n"
            "7. 저장한 시트 정의 삭제(sheet.delete)"),
    },
}


# ---------------------------------------------------------------- 레지스트리 차이
def new_commands() -> list[str]:
    """54개 측정 이후 새 커맨드 = 현재 레지스트리 - 54개 때 스냅숏."""
    old = set(json.loads(REG54.read_text(encoding="utf-8")))
    return sorted(set(registry.load()) - old)


# ---------------------------------------------------------------- 준비(CLI)
def _do(ws: Path, name: str, args: dict) -> None:
    e, _ = C.cli(ws, name, args, project="p.dobim")
    assert e["ok"], (name, e)


def prepare(group: str, ws: Path) -> None:
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    C.build_fixture(ws)
    setup = GROUPS[group]["setup"]
    if setup == "box":      # 열린 변을 닫아 방 하나(parity 의 _W4)
        _do(ws, "wall.create", {"id": "1" + "0" * 20 + "4", "level": F.LV1, "axis": [0, 4000, 0, 0], "thickness": 200, "height": 3000})
    elif setup == "stairslab":   # 2F 슬래브(승강기 구멍·난간 자동 대상)
        _do(ws, "slab.create", {"id": F.S1, "level": F.LV2, "boundary": RECT, "thickness": 200})
    elif setup == "beam":   # W1 문 위 인방(wall.cut_by 대상)
        _do(ws, "solid.create", {"id": F.SO1, "level": F.LV1, "category": "beam", "form": "member", "start": [1000, 0, 2400],
                                 "end": [2000, 0, 2400], "section": {"shape": "rect", "width": 200, "depth": 300}, "name": "인방 B1"})
    elif setup in ("planview", "views"):
        _do(ws, "view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200})
        if setup == "views":
            _do(ws, "view.create", {"id": F.V2, "name": "B-B 단면", "type": "section", "p1": [-1000, 2000], "p2": [7000, 2000]})


def post_state(group: str, ws: Path) -> dict:
    out = {}
    e, _ = C.cli(ws, "project.info", {}, project="p.dobim")
    out["rev"] = e["result"]["rev"] if e.get("ok") else None
    out["counts"] = e["result"]["counts"] if e.get("ok") else None
    if group == "G14":
        out["files"] = {n: (ws / n).exists() for n in ("sheet-saved.svg", "sheet-direct.svg", "index.svg")}
    out["escape_file_outside_workspace"] = (ws.parent / "escape.ifc").exists()
    return out


# layer_c.run_group 은 모듈 전역 GROUPS·prepare·post_state 를 쓴다: 새 묶음으로 바꿔 끼운다
def _patch() -> None:
    L.GROUPS.clear()
    L.GROUPS.update(GROUPS)
    L.prepare = prepare
    L.post_state = post_state


def main(client: str, out: str, groups: list[str]) -> int:
    _patch()
    results = {}
    for g in groups or list(GROUPS):
        results[g] = L.run_group(client, g)
        print(g, json.dumps({k: v for k, v in results[g].items() if k in ("exit", "wall_s", "tool_calls")}, ensure_ascii=False), flush=True)
        Path(out).write_text(json.dumps(C.scrub(results), ensure_ascii=False, indent=2), encoding="utf-8")
    new = set(new_commands())
    covered = {n for g in results.values() for n, v in g["commands"].items() if v["pass"]}
    succ = {n for g in results.values() for n, v in g["commands"].items() if v["succeeded"]}
    summary = {"client": client, "commands_new_total": len(new), "commands_passed": len(covered & new),
               "commands_with_success": len(succ & new), "not_passed": sorted(new - covered), "no_success": sorted(new - succ)}
    results["_summary"] = summary
    Path(out).write_text(json.dumps(C.scrub(results), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def selftest() -> int:
    """LLM 없이: 묶음 커맨드가 새 71개를 정확히 덮는지, 준비가 되는지 점검한다."""
    new = set(new_commands())
    listed = [c for g in GROUPS.values() for c in g["commands"]]
    dup = sorted({c for c in listed if listed.count(c) > 1})
    res = {"new_total": len(new), "listed": len(listed), "duplicates": dup, "missing": sorted(new - set(listed)),
           "extra": sorted(set(listed) - new), "prepared": []}
    for g in GROUPS:
        ws = C.WORK / "layerC-selftest" / g
        prepare(g, ws)
        res["prepared"].append(g)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if not (dup or res["missing"] or res["extra"]) and len(listed) == len(new) else 1


if __name__ == "__main__":
    if sys.argv[1] == "selftest":
        sys.exit(selftest())
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
