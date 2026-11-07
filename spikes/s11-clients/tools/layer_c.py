# SPDX-License-Identifier: MPL-2.0
"""층 C 전수 호출: 실제 클라이언트가 현재 코어의 모든 커맨드(30개)를 묶음별로 실행한다.

판정은 클라이언트 출력에서 뽑은 도구 호출과 서버 응답 봉투로 한다(모델의 자기 보고 아님).
커맨드 통과 = 호출됐고 관찰된 결과가 모두 성공이거나 정의된 오류 코드(errors.EXIT_CODES)다. '성공 호출 있음'은 따로 센다.

사용: python layer_c.py <claude|codex-desktop|codex-npm> <out.json> [그룹...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import common as C
from dobim_core import registry
from dobim_core.errors import EXIT_CODES

COMMON = ("연결된 BIM 도구(dobim MCP 서버)만 사용하라. 커맨드 인자는 도구로 스키마를 조회해 맞춰라. "
          "오류가 나면 포기하지 말고 오류 코드를 읽고 한 번 고쳐 다시 시도하라. 마지막에 커맨드별로 '성공' 또는 오류 코드를 "
          "한 줄씩 보고하라.\n\n")

GROUPS = {
    "G1": {
        "title": "프로젝트·모델링",
        "setup": "empty",
        "commands": ["project.new", "project.info", "commands.search", "commands.describe", "level.create", "wall.create",
                     "wall.move", "wall.set", "join.set", "join.clear", "door.place", "door.set", "pset.set", "pset.delete",
                     "material.set", "element.delete"],
        "prompt": COMMON + (
            "작업 폴더에 g1.dobim 새 프로젝트를 만든 뒤 아래 커맨드를 각각 최소 한 번 실제로 호출하라(괄호는 커맨드 이름).\n"
            "1. 새 프로젝트 만들기(project.new), 프로젝트 정보 조회(project.info)\n"
            "2. 커맨드 검색(commands.search)으로 'wall' 을 찾고, 커맨드 설명(commands.describe)으로 wall.create 를 조회\n"
            "3. 층 '1F' 고도 0 (level.create), 벽 3개를 (0,0)-(6000,0), (6000,0)-(6000,4000), (6000,4000)-(0,4000) 축선으로, "
            "두께 200, 높이 3000 (wall.create)\n"
            "4. 첫 벽을 +x 100 이동(wall.move), 첫 벽 두께를 250 으로 변경(wall.set)\n"
            "5. 첫째·둘째 벽 접합(join.set) 후 접합 해제(join.clear)\n"
            "6. 첫 벽에 offset 1500, 폭 900, 높이 2100 문 배치(door.place), 그 문 폭을 1000 으로 변경(door.set)\n"
            "7. 문에 Pset_DoorCommon.FireRating=EI30 쓰기(pset.set) 후 그 속성 삭제(pset.delete)\n"
            "8. 첫 벽 재료를 Concrete 로 지정(material.set)\n"
            "9. 셋째 벽 삭제(element.delete)"),
    },
    "G2": {
        "title": "조회·검사",
        "setup": "fixture",
        "commands": ["element.get", "element.query", "relation.list", "model.stats", "model.hash", "model.validate",
                     "schedule.rows", "schedule.summary", "history.list", "project.info"],
        "prompt": COMMON + (
            "작업 폴더의 p.dobim 은 벽 3개(W-000001..3), 문 2개(D-000001..2), 레벨 2개(1F, 2F)가 든 모델이다. 읽기만 하라. "
            "아래 커맨드를 각각 최소 한 번 호출하라(괄호는 커맨드 이름).\n"
            "요소 목록 조회(element.query), 벽 하나의 상세(element.get), 그 벽의 관계 목록(relation.list), 모델 통계(model.stats), "
            "모델 해시(model.hash), 모델 검증(model.validate), 문 일람표 행(schedule.rows), 문 일람표 합계(schedule.summary), "
            "최근 이력 5개(history.list), 프로젝트 정보(project.info)"),
    },
    "G3": {
        "title": "이력·내보내기(파괴적)",
        "setup": "fixture",
        "commands": ["wall.move", "history.undo", "history.redo", "ifc.export", "ifc.snapshots", "ifc.restore"],
        "prompt": COMMON + (
            "작업 폴더의 p.dobim 모델로 아래를 순서대로 하라(괄호는 커맨드 이름).\n"
            "1. 벽 W-000001 을 +x 100 이동(wall.move)\n"
            "2. 방금 변경을 되돌리기(history.undo), 다시 적용(history.redo)\n"
            "3. 현재 모델을 out.ifc 로 내보내기(ifc.export). 되돌릴 수 없는 커맨드라 확인 절차가 있다. 미리보기 결과의 토큰으로 확정하라\n"
            "4. 벽 W-000002 를 +y 50 이동한 뒤 out.ifc 로 다시 내보내 덮어쓰기(ifc.export, 확인 절차 포함)\n"
            "5. out.ifc 의 스냅숏 목록 조회(ifc.snapshots)\n"
            "6. 스냅숏 1번으로 out.ifc 를 되돌리기(ifc.restore, 확인 절차 포함)"),
    },
    "G4": {
        "title": "오류 코드·안전 장치",
        "setup": "fixture",
        "commands": [],
        "prompt": COMMON + (
            "작업 폴더의 p.dobim 모델에서 아래 상황을 일부러 만들어 서버가 어떤 오류 코드로 응답하는지 확인하라. 각 항목은 한 번만 시도하고 "
            "결과 코드를 보고하라.\n"
            "a. 작업 폴더 밖 경로 ../escape.ifc 로 IFC 내보내기 시도\n"
            "b. 존재하지 않는 커맨드 no.such.command 실행 시도\n"
            "c. wall.move 에 dx 를 숫자가 아닌 'abc' 로 줘서 실행\n"
            "d. 쓰기 커맨드 wall.move 를 읽기 전용 실행 도구로 호출\n"
            "e. 벽 W-000001 을 +x 100 이동하되 expected_rev 를 1 로 지정\n"
            "f. 벽 W-000001 을 +x 100 이동을 dry_run 으로 실행(모델은 바뀌지 않아야 한다)\n"
            "g. 존재하지 않는 id(9가 22개)의 벽을 이동\n"
            "h. 작업(job) 조회 도구로 id 'nope' 조회\n"
            "i. 렌더 도구 호출"),
    },
}


def invocations(calls: list[dict]) -> list[dict]:
    """도구 호출을 커맨드 단위로 펼친다: {command, tool, outcome}. outcome 은 'ok' 또는 오류 코드."""
    out = []

    def outcome(c):
        e = c.get("envelope")
        if e is None:
            return "NO_ENVELOPE"
        return "ok" if e.get("ok") else (e.get("error") or {}).get("code", "UNKNOWN")

    for c in calls:
        t, a = c["tool"], c.get("input") or {}
        if t == "dobim_commands_search":
            out.append({"command": "commands.search", "tool": t, "outcome": outcome(c)})
        elif t == "dobim_command_describe":
            out.append({"command": "commands.describe", "tool": t, "outcome": outcome(c)})
        elif t == "dobim_project":
            act = a.get("action")
            cmd = {"create": "project.new", "open": "project.info", "info": "project.info"}.get(act)
            if cmd:
                out.append({"command": cmd, "tool": f"{t}:{act}", "outcome": outcome(c)})
        elif t in ("dobim_query", "dobim_execute", "dobim_execute_destructive"):
            batch = a.get("commands")
            if batch:
                for it in batch:
                    out.append({"command": it.get("command") or it.get("name"), "tool": t + ":batch", "outcome": outcome(c)})
            elif a.get("command"):
                out.append({"command": a["command"], "tool": t, "outcome": outcome(c), "dry_run": bool(a.get("dry_run"))})
        elif t in ("dobim_job", "dobim_render"):
            out.append({"command": t, "tool": t, "outcome": outcome(c)})
    return out


def judge_group(group: str, calls: list[dict]) -> dict:
    inv = invocations(calls)
    res = {}
    for name in GROUPS[group]["commands"]:
        mine = [i for i in inv if i["command"] == name]
        outs = [i["outcome"] for i in mine]
        res[name] = {"invoked": bool(mine), "outcomes": sorted(set(outs)), "succeeded": "ok" in outs,
                     "all_defined": bool(mine) and all(o == "ok" or o in EXIT_CODES for o in outs),
                     "pass": bool(mine) and all(o == "ok" or o in EXIT_CODES for o in outs)}
    return res


def judge_g4(calls: list[dict]) -> dict:
    """G4: 서버 응답의 오류 코드가 기대와 맞는지 도구 호출로 판정한다."""
    inv = invocations(calls)
    want = {
        "a_path_denied": lambda i: i["command"] == "ifc.export" and i["outcome"] == "PATH_DENIED",
        "b_unknown_command": lambda i: i["command"] == "no.such.command" and i["outcome"] == "UNKNOWN_COMMAND",
        "c_schema_invalid": lambda i: i["command"] == "wall.move" and i["outcome"] == "SCHEMA_INVALID",
        "d_risk_mismatch": lambda i: i["command"] == "wall.move" and i["tool"] == "dobim_query" and i["outcome"] == "RISK_MISMATCH",
        "e_rev_conflict": lambda i: i["command"] == "wall.move" and i["outcome"] == "REV_CONFLICT",
        "f_dry_run_ok": lambda i: i["command"] == "wall.move" and i.get("dry_run") and i["outcome"] == "ok",
        "g_not_found": lambda i: i["command"] == "wall.move" and i["outcome"] in ("NOT_FOUND", "CONSTRAINT"),
        "h_job": lambda i: i["command"] == "dobim_job" and i["outcome"] == "NOT_FOUND",
        "i_render": lambda i: i["command"] == "dobim_render" and i["outcome"] == "UNSUPPORTED",
    }
    return {k: any(f(i) for i in inv) for k, f in want.items()}


def prepare(group: str, ws: Path) -> None:
    import shutil
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    if GROUPS[group]["setup"] == "fixture":
        C.build_fixture(ws)


def post_state(group: str, ws: Path) -> dict:
    """실행 뒤 상태를 CLI 로 조회해 모델 쪽 부작용을 확인한다."""
    out = {}
    if group == "G1":
        e, _ = C.cli(ws, "project.info", {}, project="g1.dobim") if (ws / "g1.dobim").exists() else ({"ok": False}, 1)
        out["g1_exists"] = (ws / "g1.dobim").exists()
        if e.get("ok"):
            out["counts"] = e["result"]["counts"]
        v, _ = C.cli(ws, "model.validate", {}, project="g1.dobim") if (ws / "g1.dobim").exists() else ({"ok": False}, 1)
        out["valid"] = v.get("ok")
    elif group in ("G2", "G3", "G4"):
        e, _ = C.cli(ws, "project.info", {}, project="p.dobim")
        out["rev"] = e["result"]["rev"] if e.get("ok") else None
        out["counts"] = e["result"]["counts"] if e.get("ok") else None
        if group == "G3":
            out["out_ifc_exists"] = (ws / "out.ifc").exists()
            out["snapshots"] = len(list((ws / ".dobim-snapshots").glob("out.ifc.*"))) if (ws / ".dobim-snapshots").exists() else 0
        if group == "G4":
            out["escape_file_outside_workspace"] = (ws.parent / "escape.ifc").exists()
    return out


def run_group(client: str, group: str, *, model: str = "sonnet") -> dict:
    ws = C.WORK / f"layerC-{client}-{group}"
    prepare(group, ws)
    tag = f"layerC-{client}-{group}"
    prompt = GROUPS[group]["prompt"]
    if client == "claude":
        r = C.run_claude(prompt, ws, tag, model=model)
        p = C.parse_claude(r["raw"])
    else:
        exe = C.codex_desktop_exe() if client == "codex-desktop" else C.CODEX_NPM
        r = C.run_codex(prompt, ws, tag, exe=exe)
        p = C.parse_codex(r["raw"])
    tap = C.parse_tap(r["tap"])
    rec = {"group": group, "title": GROUPS[group]["title"], "exit": r["exit"], "wall_s": r["wall_s"],
           "tool_calls": len(p["calls"]), "search_calls": sum(1 for c in p["calls"] if c["tool"] in C.SEARCH_TOOLS),
           "usage": p["usage"], "post_state": post_state(group, ws),
           "other_item_types": p.get("item_types"), "errors": p.get("errors"),
           "tool_result_bytes": [len(c["result_text"].encode("utf-8")) for c in p["calls"]],
           "protocol": {k: tap.get(k) for k in ("initialize", "negotiated", "non_json_stdout_lines")},
           "latency": C.latency_summary(tap.get("latencies", [])), "server_start_sec": tap.get("t_first_stdout")}
    if group == "G4":
        rec["g4"] = judge_g4(p["calls"])
        rec["commands"] = {}
    else:
        rec["commands"] = judge_group(group, p["calls"])
    rec["final_text_chars"] = len(p["final_text"])
    return rec


def main(client: str, out: str, groups: list[str]) -> int:
    results = {}
    for g in groups or list(GROUPS):
        results[g] = run_group(client, g)
        print(g, json.dumps({k: v for k, v in results[g].items() if k in ("exit", "wall_s", "tool_calls", "g4")}, ensure_ascii=False), flush=True)
        Path(out).write_text(json.dumps(C.scrub(results), ensure_ascii=False, indent=2), encoding="utf-8")
    allc = set(registry.load())
    covered = {n for g in results.values() for n, v in g["commands"].items() if v["pass"]}
    succ = {n for g in results.values() for n, v in g["commands"].items() if v["succeeded"]}
    summary = {"client": client, "commands_total": len(allc), "commands_passed": len(covered & allc),
               "commands_with_success": len(succ & allc), "not_passed": sorted(allc - covered), "no_success": sorted(allc - succ)}
    results["_summary"] = summary
    Path(out).write_text(json.dumps(C.scrub(results), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
