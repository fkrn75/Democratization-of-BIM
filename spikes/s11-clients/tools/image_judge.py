# SPDX-License-Identifier: MPL-2.0
"""C-1 마감: 이미지 판정. 실제 클라이언트가 dobim_render 로 받은 PNG 를 눈으로 보고 답하는가.

도면에서만 알 수 있는 값을 모델이 이미지를 보고 읽어 모델 속성(Pset_Report)으로 기록하게 하고, 실행 뒤 CLI 로 상태를 조회해 판정한다.
조회 커맨드로 답을 얻는 우회를 막으려고 지시문에서 요소·시트 조회 커맨드를 금지하고, 호출 기록에 그런 호출이 있으면 실패로 센다.
도구 결과에 이미지 블록이 실제로 실렸는지도 원본 대화 기록에서 센다.

과제
- I1: 평면 뷰 이미지에서 문 기호 개수(모델에는 문 3개: 지시문에 개수를 주지 않는다)
- I2: 저장된 시트 이미지의 표제란에서 도면번호·프로젝트명 읽기(저장된 정의에만 있고 지시문에 값이 없다)

사용: python image_judge.py <claude|codex-desktop|codex-npm> <out.json> [--only I1,I2]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import common as C
from dobim_core import fixture as F
from explore_tasks import ex, pset, q

PREAMBLE = ("작업 폴더에 건축 모델 프로젝트 파일 p.dobim 이 있다. 벽 W-000001(id 1000000000000000000001)이 있다. 연결된 BIM 도구(dobim MCP 서버)만 사용해서 아래 과제를 끝내라. "
            "답은 반드시 도구로 돌려받은 이미지를 눈으로 보고 정하라. 요소·시트·일람표를 조회하는 커맨드(element.*, schedule.*, model.*, sheet.list 등)로 답을 구하는 것은 금지다"
            "(뷰 목록 조회, 도구·커맨드 검색은 허용). 값을 모델에 적는 데는 속성 쓰기 커맨드를 쓴다. 과제를 끝내면 한 줄로만 보고하라.\n\n과제: ")

FORBIDDEN_PREFIX = ("element.", "schedule.", "model.", "sheet.list", "relation.", "view.linework", "history.")


def setup_i1(ws: Path):
    C.build_fixture(ws)
    e, _ = C.cli(ws, "door.place", {"id": "2" + "0" * 20 + "3", "host": F.W2, "offset": 1500, "width": 900, "height": 2100}, project="p.dobim")
    assert e["ok"], e
    e, _ = C.cli(ws, "view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}, project="p.dobim")
    assert e["ok"], e


def setup_i2(ws: Path):
    setup_i1(ws)
    e, _ = C.cli(ws, "sheet.define", {"id": F.SH1, "name": "검수 시트", "views": [F.V1], "paper": "A3", "project_name": "검수 청사",
                                       "sheet_name": "1층 평면도", "sheet_number": "C1-734", "date": "2026-11-19"}, project="p.dobim")
    assert e["ok"], e


TASKS = [
    ("I1", "'1F 평면' 뷰를 이미지로 그려 보고, 도면에 보이는 문 기호(열림 호가 붙은 문)가 모두 몇 개인지 세어 벽 W-000001 의 사용자 속성 Pset_Report 의 "
           "ImageDoorCount 로 기록해라(정수).", setup_i1,
     lambda ws: pset(ws, F.W1, "Pset_Report", "ImageDoorCount") in (3, 3.0, "3")),
    ("I2", "모델에 저장된 시트(이름 '검수 시트', 시트 id 9000000000000000000001)를 이미지로 그려 보고, 표제란에 적힌 도면번호와 프로젝트명을 읽어 벽 W-000001 의 사용자 속성 Pset_Report 의 "
           "SheetNumber, ProjectName 으로 각각 기록해라. 표제란 글자가 작아 읽기 어려우면 이미지를 확대해(자르기 인자) 다시 봐도 된다.", setup_i2,
     lambda ws: pset(ws, F.W1, "Pset_Report", "SheetNumber") == "C1-734" and pset(ws, F.W1, "Pset_Report", "ProjectName") == "검수 청사"),
]


def count_image_blocks(raw: Path) -> int:
    """원본 대화 기록에서 이미지 블록 개수(claude: tool_result 안 image, codex: 결과 content 의 image). 값만 센다."""
    n = 0
    try:
        for line in raw.read_text(encoding="utf-8", errors="replace").splitlines():
            n += len(re.findall(r'"type":\s*"image"', line))
    except OSError:
        return -1
    return n


def run_one(client: str, t, model: str) -> dict:
    ws = C.WORK / f"image-{client}" / t[0]
    shutil.rmtree(ws, ignore_errors=True)
    t[2](ws)
    tag = f"image-{client}-{t[0]}"
    prompt = PREAMBLE + t[1]
    if client == "claude":
        r = C.run_claude(prompt, ws, tag, model=model)
        p = C.parse_claude(r["raw"])
    else:
        r = C.run_codex(prompt, ws, tag, exe=C.codex_desktop_exe() if client == "codex-desktop" else C.CODEX_NPM)
        p = C.parse_codex(r["raw"])
    from layer_c import invocations
    inv = invocations(p["calls"])
    try:
        ok_state = bool(t[3](ws))
    except Exception:
        ok_state = False
    renders = [c for c in p["calls"] if c["tool"] == "dobim_render"]
    renders_ok = [c for c in renders if (c.get("envelope") or {}).get("ok")]
    forbidden = [i["command"] for i in inv if i["command"] and i["command"].startswith(FORBIDDEN_PREFIX)]
    images = count_image_blocks(r["raw"])
    return {"task": t[0], "state_correct": ok_state, "render_calls": len(renders), "render_ok": len(renders_ok),
            "render_args": [c["input"] for c in renders], "image_blocks_in_transcript": images, "forbidden_calls": forbidden,
            "success": bool(ok_state and renders_ok and not forbidden), "exit": r["exit"], "wall_s": r["wall_s"],
            "tool_calls": len(p["calls"]), "commands_used": sorted({i["command"] for i in inv}), "usage": p["usage"],
            "error_responses": [i["outcome"] for i in inv if i["outcome"] != "ok"],
            "result_max_bytes": max([len(c["result_text"].encode("utf-8")) for c in p["calls"]] or [0])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("client")
    ap.add_argument("out")
    ap.add_argument("--only", default="")
    ap.add_argument("--model", default="sonnet")
    a = ap.parse_args()
    rows = [run_one(a.client, t, a.model) for t in TASKS if not a.only or t[0] in a.only.split(",")]
    summary = {"client": a.client, "tasks": len(rows), "success": sum(r["success"] for r in rows), "failed": [r["task"] for r in rows if not r["success"]]}
    Path(a.out).write_text(json.dumps(C.scrub({"summary": summary, "rows": rows}), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def selftest() -> int:
    """LLM 없이: 준비 직후 판정이 모두 거짓이어야 한다(항상 참인 판정기가 아님)."""
    bad = []
    for t in TASKS:
        ws = C.WORK / "image-selftest" / t[0]
        shutil.rmtree(ws, ignore_errors=True)
        t[2](ws)
        if t[3](ws):
            bad.append(t[0])
    print(json.dumps({"always_true_judges": bad}))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1] == "selftest" else main())
