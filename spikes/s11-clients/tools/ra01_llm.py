# SPDX-License-Identifier: MPL-2.0
"""C-1 마감: RA-01 결정적 시나리오의 LLM 경유 결과 동등(Claude Code CLI, Codex).

ra01_equiv.py 의 11단계(레벨 2·벽 14·문 5, 일괄 76커맨드 포함)를 실제 클라이언트가 도구 호출로 수행하게 하고,
같은 단계를 CLI 로 직접 실행한 기준과 비교한다. 비교: 호출 순서(도구·성공/오류 코드), 최종 model.hash(state·derived·ifc·rev),
내보낸 IFC 의 의미 비교(s0 roundtrip_check, 차이 0), 작업 폴더 밖 쓰기 없음.
(ra01_equiv.py 는 같은 단계를 LLM 없이 공식 SDK 클라이언트로 돌린 서버 직접 경로 시험이다.)

사용: python ra01_llm.py <claude|codex-desktop|codex-npm|cli> <out.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import common as C
import equiv
import ra01_equiv as R
from dobim_core import registry


def tool_steps() -> list[tuple[str, dict]]:
    """ra01_equiv.steps() 를 도구 호출 목록으로 바꾼다(도구 이름, 인자). 확인 토큰은 'TOKEN' 표시."""
    out = []
    reg = registry.load()
    for s in R.steps():
        if s["kind"] == "new":
            out.append(("dobim_project", {"action": "create", **s["args"]}))
        elif s["kind"] == "batch":
            out.append(("dobim_execute", {"project": R.PROJ, "commands": s["commands"]}))
        else:
            tool = {"read": "dobim_query", "write": "dobim_execute"}.get(reg[s["name"]].risk, "dobim_execute_destructive")
            a = {"project": R.PROJ, "command": s["name"], "args": s["args"]}
            if s.get("confirm") == "TOKEN":
                a["confirm"] = "TOKEN"
            out.append((tool, a))
    return out


def prompt() -> str:
    lines = [f"{i}. 도구 {t}  인자 {json.dumps(a, ensure_ascii=False, separators=(',', ':'))}" for i, (t, a) in enumerate(tool_steps(), 1)]
    return ("연결된 BIM 도구(dobim MCP 서버)만 사용해서 아래 단계를 순서대로 실행하라. 도구 이름과 인자는 한 글자도 바꾸지 말고 그대로 쓴다"
            "(commands 목록의 순서·값도 그대로). 단 인자의 \"confirm\":\"TOKEN\" 은 바로 앞 단계 응답의 error.details.confirm_token 값으로 바꿔서 쓴다. "
            "오류 응답이 나와도(작업 폴더 밖 쓰기 거부 등) 정상 흐름이니 다음 단계로 계속 진행하라. 다른 조회 호출은 하지 마라. "
            "마지막에 각 단계의 결과가 ok 인지 오류 코드인지만 한 줄씩 보고하라.\n\n" + "\n".join(lines))


def expected_seq(base_log: list[dict]) -> list[tuple]:
    reg = registry.load()
    seq = []
    for (tool, _a), e in zip(tool_steps(), base_log):
        seq.append((tool, e["ok"], None if e["ok"] else e["error"]["code"]))
    return seq


def final_hash(ws: Path):
    h, _ = C.cli(ws, "model.hash", {}, project=R.PROJ)
    i, _ = C.cli(ws, "project.info", {}, project=R.PROJ)
    return {"hash": h["result"] if h["ok"] else None, "rev": i["result"]["rev"] if i["ok"] else None,
            "counts": i["result"]["counts"] if i["ok"] else None}


def main(client: str, out: str) -> int:
    base = C.WORK / "ra01-llm" / "cli"
    R.prepare(base)
    ea = R.run_direct(base)
    base_state = final_hash(base)
    res = {"steps": len(tool_steps()), "ra01_commands_in_batch": len(R.steps()[1]["commands"]), "prompt_chars": len(prompt()),
           "baseline_cli": {"ok_per_step": [e["ok"] for e in ea], "state": base_state}}
    if client != "cli":
        ws = C.WORK / "ra01-llm" / client
        R.prepare(ws)
        tag = f"ra01llm-{client}"
        if client == "claude":
            r = C.run_claude(prompt(), ws, tag)
            p = C.parse_claude(r["raw"])
        else:
            r = C.run_codex(prompt(), ws, tag, exe=C.codex_desktop_exe() if client == "codex-desktop" else C.CODEX_NPM)
            p = C.parse_codex(r["raw"])
        calls = p["calls"]
        seq = [(c["tool"], (c["envelope"] or {}).get("ok"), ((c["envelope"] or {}).get("error") or {}).get("code")) for c in calls]
        expect = expected_seq(ea)
        st = final_hash(ws)
        cl = {"exit": r["exit"], "wall_s": r["wall_s"], "tool_calls": len(calls), "usage": p["usage"], "other_item_types": p.get("item_types"),
              "call_sequence_equals_script": seq == expect, "extra_or_missing_calls": len(calls) - len(expect), "state": st,
              "hash_equal_to_cli": st["hash"] is not None and st["hash"] == base_state["hash"],
              "escape_file_created_outside": (ws.parent / "escape.ifc").exists(), "final_text_chars": len(p["final_text"])}
        if seq != expect:
            cl["first_divergence_index"] = next((i for i, (x, y) in enumerate(zip(seq, expect)) if x != y), min(len(seq), len(expect)))
            cl["sequence_actual"] = [list(x) for x in seq]
        # 일괄 인자가 기준과 글자 그대로인지(모델이 76커맨드를 옮겨 쓰다 바꾸지 않았는지)
        want_batch = tool_steps()[1][1]["commands"]
        got_batch = next((c["input"].get("commands") for c in calls if c["tool"] == "dobim_execute" and c["input"].get("commands")), None)
        cl["batch_args_identical"] = got_batch == want_batch
        f_cli, f_cl = base / R.IFC, ws / R.IFC
        cl["final_ifc_semantic"] = equiv.semantic_diff(f_cli, f_cl) if f_cl.exists() else {"identical": False, "reason": "파일 없음"}
        tap = C.parse_tap(r["tap"])
        cl["protocol"] = {k: tap.get(k) for k in ("initialize", "negotiated", "non_json_stdout_lines")}
        cl["latency"] = C.latency_summary(tap.get("latencies", []))
        res["client"] = cl
        res["pass"] = bool(cl["call_sequence_equals_script"] and cl["hash_equal_to_cli"] and cl["final_ifc_semantic"]["identical"]
                           and not cl["escape_file_created_outside"] and r["exit"] == 0)
    Path(out).write_text(json.dumps(C.scrub(res), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(C.scrub(res).get("client", res["baseline_cli"]), ensure_ascii=False)[:1800])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
