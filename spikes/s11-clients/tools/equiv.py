# SPDX-License-Identifier: MPL-2.0
"""결과 동등 + Windows 시나리오: 같은 결정적 스크립트를 CLI, Claude 경유, Codex 경유로 돌려 결과를 비교한다.

작업 폴더는 한글·공백이 든 경로다(15.10절). 스크립트에는 모델 조작, dry-run 후 실행, undo·redo, 확인 토큰 절차,
IFC 내보내기·덮어쓰기(스냅숏), 작업 폴더 밖 쓰기 시도(PATH_DENIED)가 들어 있다.
비교: 최종 model.hash, 내보낸 IFC 의 의미 비교(s0 roundtrip_check 재사용: 차이 0), 첫 내보내기 스냅숏의 의미 비교.

사용: python equiv.py <claude|codex-desktop|codex-npm|cli> <out.json>
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import common as C
from dobim_core import fixture as F

PROJ = "건물 모델.dobim"
IFC = "출력/건물 모델.ifc"
BATCH = [{"command": n, "args": a} for n, a in F.SCRIPT]

# (도구, 인자) 목록. 확인 토큰 단계는 TOKEN 표시로 앞 단계 응답의 토큰을 넘긴다.
STEPS = [
    ("dobim_project", {"action": "create", "path": PROJ, "guid": F.GUID}),
    ("dobim_execute", {"project": PROJ, "commands": BATCH}),
    ("dobim_execute", {"project": PROJ, "command": "wall.move", "args": {"id": F.W2, "dx": -500}, "dry_run": True}),
    ("dobim_execute", {"project": PROJ, "command": "wall.move", "args": {"id": F.W2, "dx": -500}}),
    ("dobim_execute", {"project": PROJ, "command": "history.undo", "args": {}}),
    ("dobim_execute", {"project": PROJ, "command": "history.redo", "args": {}}),
    ("dobim_execute", {"project": PROJ, "command": "door.set", "args": {"id": F.D2, "width": 1100}}),
    ("dobim_execute_destructive", {"project": PROJ, "command": "ifc.export", "args": {"path": IFC}}),
    ("dobim_execute_destructive", {"project": PROJ, "command": "ifc.export", "args": {"path": IFC}, "confirm": "TOKEN"}),
    ("dobim_execute_destructive", {"project": PROJ, "command": "ifc.export", "args": {"path": "../escape.ifc"}}),
    ("dobim_execute", {"project": PROJ, "command": "wall.move", "args": {"id": F.W1, "dx": 250}}),
    ("dobim_execute_destructive", {"project": PROJ, "command": "ifc.export", "args": {"path": IFC}}),
    ("dobim_execute_destructive", {"project": PROJ, "command": "ifc.export", "args": {"path": IFC}, "confirm": "TOKEN"}),
    ("dobim_query", {"project": PROJ, "command": "ifc.snapshots", "args": {"path": IFC}}),
    ("dobim_query", {"project": PROJ, "command": "model.hash", "args": {}}),
]


def prompt() -> str:
    lines = [f"{i}. 도구 {t}  인자 {json.dumps(a, ensure_ascii=False)}" for i, (t, a) in enumerate(STEPS, 1)]
    return ("연결된 BIM 도구(dobim MCP 서버)만 사용해서 아래 단계를 순서대로 실행하라. 도구 이름과 인자는 한 글자도 바꾸지 말고 그대로 쓴다. "
            "단 인자의 \"confirm\": \"TOKEN\" 은 바로 앞 단계 응답의 error.details.confirm_token 값으로 바꿔서 쓴다. "
            "오류 응답이 나와도(작업 폴더 밖 쓰기 거부 등) 정상 흐름이니 다음 단계로 계속 진행하라. 다른 조회 호출은 하지 마라. "
            "마지막에 각 단계의 결과가 ok 인지 오류 코드인지만 한 줄씩 보고하라.\n\n" + "\n".join(lines))


def prepare(ws: Path) -> None:
    shutil.rmtree("\\\\?\\" + str(ws) if os.name == "nt" else ws, ignore_errors=True)
    (ws / "출력").mkdir(parents=True)


def run_cli_baseline(ws: Path) -> list[dict]:
    """같은 단계를 CLI 서브프로세스로 실행한다(기준)."""
    token = None
    log = []
    for tool, a in STEPS:
        a = dict(a)
        if tool == "dobim_project":
            e, _ = C.cli(ws, "project.new", {"path": a["path"], "guid": a["guid"]})
        elif "commands" in a:
            f = ws / "_batch.jsonl"
            f.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in a["commands"]), encoding="utf-8")
            import subprocess
            p = subprocess.run([str(C.PY), "-m", "dobim_core", "--workspace", str(ws), "script", str(f), "-p", a["project"], "--atomic"],
                               capture_output=True, env={**os.environ, **C.server_env()}, stdin=subprocess.DEVNULL)
            e = json.loads(p.stdout.decode("utf-8"))
            f.unlink()
        else:
            conf = a.get("confirm")
            if conf == "TOKEN":
                conf = token
            e, _ = C.cli(ws, a["command"], a.get("args", {}), project=a["project"], dry_run=a.get("dry_run", False), confirm=conf)
            token = (e.get("error") or {}).get("details", {}).get("confirm_token", token)
        log.append({"tool": tool, "ok": e["ok"], "code": None if e["ok"] else e["error"]["code"]})
    return log


def semantic_diff(ref: Path, test: Path) -> dict:
    """s0 의 roundtrip_check 를 재사용해 두 IFC 의 의미 차이를 센다."""
    sys.path.insert(0, str(C.REPO / "spikes" / "s0-baseline"))
    import roundtrip_check as R
    import ifcopenshell
    a, b = R.extract(ifcopenshell.open(str(ref))), R.extract(ifcopenshell.open(str(test)))
    diffs = []
    for k in ("classes", "gids", "psets", "intent", "layers", "voids", "fills"):
        if a[k] != b[k]:
            diffs.append(k)
    for k in ("volume", "center"):
        if set(a[k]) != set(b[k]) or any(not R._same(a[k][g], b[k][g]) for g in a[k]):
            diffs.append(k)
    return {"identical": not diffs, "differing_parts": diffs, "elements": len(a["gids"])}


def final_state(ws: Path) -> dict:
    h, _ = C.cli(ws, "model.hash", {}, project=PROJ)
    i, _ = C.cli(ws, "project.info", {}, project=PROJ)
    return {"hash": h["result"] if h["ok"] else None, "rev": i["result"]["rev"] if i["ok"] else None,
            "counts": i["result"]["counts"] if i["ok"] else None}


def main(client: str, out: str) -> int:
    base = C.WORK / "equiv" / "cli" / "한글 공백 폴더" / "프로젝트 시험 (사본)"
    prepare(base)
    base_log = run_cli_baseline(base)
    base_state = final_state(base)
    res = {"steps": len(STEPS), "baseline_cli": {"log": base_log, "state": base_state}}
    if client != "cli":
        ws = C.WORK / "equiv" / client / "한글 공백 폴더" / "프로젝트 시험 (사본)"
        prepare(ws)
        tag = f"equiv-{client}"
        if client == "claude":
            r = C.run_claude(prompt(), ws, tag)
            p = C.parse_claude(r["raw"])
        else:
            r = C.run_codex(prompt(), ws, tag, exe=C.codex_desktop_exe() if client == "codex-desktop" else C.CODEX_NPM)
            p = C.parse_codex(r["raw"])
        calls = p["calls"]
        seq = [(c["tool"], (c["envelope"] or {}).get("ok"), ((c["envelope"] or {}).get("error") or {}).get("code")) for c in calls]
        expect = [(b["tool"], b["ok"], b["code"]) for b in base_log]
        st = final_state(ws)
        res["client"] = {"exit": r["exit"], "wall_s": r["wall_s"], "tool_calls": len(calls), "usage": p["usage"],
                         "call_sequence_equals_script": seq == expect, "state": st,
                         "other_item_types": p.get("item_types"),
                         "extra_or_missing_calls": len(calls) - len(STEPS),
                         "hash_equal_to_cli": st["hash"] == base_state["hash"] and st["hash"] is not None,
                         "escape_file_created_outside": (ws.parent / "escape.ifc").exists()}
        if seq != expect:
            res["client"]["first_divergence_index"] = next((i for i, (x, y) in enumerate(zip(seq, expect)) if x != y), min(len(seq), len(expect)))
        f_cli, f_cl = base / IFC, ws / IFC
        res["client"]["final_ifc_semantic"] = semantic_diff(f_cli, f_cl) if f_cl.exists() else {"identical": False, "reason": "파일 없음"}
        s_cli = base / "출력" / ".dobim-snapshots" / "건물 모델.ifc.1"
        s_cl = ws / "출력" / ".dobim-snapshots" / "건물 모델.ifc.1"
        res["client"]["snapshot1_semantic"] = semantic_diff(s_cli, s_cl) if s_cl.exists() else {"identical": False, "reason": "파일 없음"}
        tap = C.parse_tap(r["tap"])
        res["client"]["protocol"] = {k: tap.get(k) for k in ("initialize", "negotiated", "non_json_stdout_lines")}
        res["client"]["latency"] = C.latency_summary(tap.get("latencies", []))
        res["client"]["server_start_sec"] = tap.get("t_first_stdout")
    Path(out).write_text(json.dumps(C.scrub(res), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(C.scrub(res)["client"] if "client" in res else res["baseline_cli"], ensure_ascii=False)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
