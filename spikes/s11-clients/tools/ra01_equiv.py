# SPDX-License-Identifier: MPL-2.0
"""C-1 잔여: RA-01 결정적 시나리오의 결과 동등 시험(LLM 없음).

같은 커맨드 순서(s10 의 build_script: RA-01 에서 코어가 표현하는 범위, 레벨 2·벽 14·문 5)를
(A) CLI 서브프로세스(직접 호출)와 (B) 공식 SDK 클라이언트로 stdio MCP 서버(MCP 경유)에 각각 실행해 비교한다.
비교 항목: 단계별 결과 봉투(확인 토큰만 가림), 최종 model.hash(state·derived·ifc), 내보낸 IFC 의 의미 비교(s0 roundtrip_check,
차이 0), 두 IFC 의 DATA 구역 해시(무작위 GlobalId·SET 순서 정규화 후).
모델 시험 없이 서버 직접 경로만 쓰므로 토큰을 쓰지 않는다.

사용: python ra01_equiv.py <out.json>
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import common as C
from dobim_core import registry

# s10 의 RA-01 스크립트를 그대로 재사용한다(복제하지 않는다)
_spec = importlib.util.spec_from_file_location("ra01_headless", C.REPO / "spikes" / "s10-headless" / "ra01_headless.py")
RA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RA)

PROJ = "ra01.dobim"
IFC = "ra01.ifc"


def steps() -> list[dict]:
    """고정된 단계 열. kind: new(프로젝트 생성) / batch(원자 일괄) / cmd(단일 커맨드). confirm=TOKEN 은 앞 단계의 확인 토큰."""
    batch = RA.build_script()
    return [
        {"kind": "new", "args": {"path": PROJ, "guid": RA.GUID}},
        {"kind": "batch", "commands": batch},
        {"kind": "cmd", "name": "model.stats", "args": {}},
        {"kind": "cmd", "name": "model.validate", "args": {"express_rules": True}},
        {"kind": "cmd", "name": "ifc.export", "args": {"path": IFC}},                       # 미리보기: 토큰 발급
        {"kind": "cmd", "name": "ifc.export", "args": {"path": IFC}, "confirm": "TOKEN"},   # 실행
        {"kind": "cmd", "name": "ifc.export", "args": {"path": "../escape.ifc"}},           # 작업 폴더 밖: PATH_DENIED
        {"kind": "cmd", "name": "schedule.summary", "args": {"kind": "door"}},
        {"kind": "cmd", "name": "element.query", "args": {"limit": 50}},
        {"kind": "cmd", "name": "history.list", "args": {"limit": 5}},
        {"kind": "cmd", "name": "model.hash", "args": {}},
    ]


# 전송 경로 자체가 만드는 정당한 차이(비교에서 가리고 결과에 목록으로 남긴다)
ALLOWED = ["project.new 결과의 handle(MCP 세션만 핸들을 발급)", "history.list 항목의 actor(CLI 는 cli, MCP 서버는 mcp)",
           "확인 토큰 값(호출마다 다름)", "ifc.export 의 bytes(헤더 시각 + MCP 경로에서만 속성 엔티티 번호 순서가 다름: 직접 2회끼리는 DATA 구역이 같고 MCP 와는 다르다. 의미·model.hash.ifc 는 같음)"]


def norm(env: dict) -> dict:
    """호출마다·경로마다 달라지는 값만 가린다(ALLOWED 참조)."""
    env = json.loads(json.dumps(env))
    d = (env.get("error") or {}).get("details") or {}
    if "confirm_token" in d:
        d["confirm_token"] = "<token>"
    r = env.get("result")
    if isinstance(r, dict):
        r.pop("handle", None)
        if "bytes" in r and "path" in r:
            r["bytes"] = "<n>"
        for it in r.get("items") or []:
            if isinstance(it, dict) and "actor" in it:
                it["actor"] = "<actor>"
    return env


def prepare(ws: Path) -> None:
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)


def run_direct(ws: Path) -> list[dict]:
    """(A) 직접 호출: CLI 서브프로세스."""
    out, token = [], None
    for s in steps():
        if s["kind"] == "new":
            e, _ = C.cli(ws, "project.new", s["args"])
        elif s["kind"] == "batch":
            f = ws / "_batch.jsonl"
            f.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in s["commands"]), encoding="utf-8", newline="\n")
            p = subprocess.run([str(C.PY), "-m", "dobim_core", "--workspace", str(ws), "--actor", "cli", "script", str(f), "-p", PROJ, "--atomic"],
                               capture_output=True, env={**os.environ, **C.server_env()}, stdin=subprocess.DEVNULL, cwd=ws, timeout=180)
            e = json.loads(p.stdout.decode("utf-8"))
            f.unlink()
        else:
            conf = token if s.get("confirm") == "TOKEN" else None
            e, _ = C.cli(ws, s["name"], s["args"], project=PROJ, confirm=conf)
            token = (e.get("error") or {}).get("details", {}).get("confirm_token", token)
        out.append(e)
    return out


async def run_mcp(ws: Path) -> list[dict]:
    """(B) MCP 경유: 공식 SDK 클라이언트 -> stdio 서버. LLM 은 없다."""
    from mcp import Client, StdioServerParameters
    params = StdioServerParameters(command=str(C.PY), args=["-m", "dobim_core", "--workspace", str(ws), "--actor", "cli", "mcp", "serve"],
                                   env={**os.environ, **C.server_env()})
    out, token = [], None
    async with Client(params) as cl:
        for s in steps():
            if s["kind"] == "new":
                r = await cl.call_tool("dobim_project", {"action": "create", **s["args"]})
            elif s["kind"] == "batch":
                r = await cl.call_tool("dobim_execute", {"project": PROJ, "commands": s["commands"]})
            else:
                risk = registry.load()[s["name"]].risk
                tool = {"read": "dobim_query", "write": "dobim_execute"}.get(risk, "dobim_execute_destructive")
                a = {"project": PROJ, "command": s["name"], "args": s["args"]}
                if s.get("confirm") == "TOKEN":
                    a["confirm"] = token
                r = await cl.call_tool(tool, a)
            e = r.structured_content
            assert e == json.loads(r.content[0].text), "text 와 structuredContent 가 다르다"
            token = (e.get("error") or {}).get("details", {}).get("confirm_token", token)
            out.append(e)
    return out


def main(out: str) -> int:
    import equiv
    base = C.WORK / "ra01-equiv"
    wa, wb = base / "direct" / "ws", base / "mcp" / "ws"
    prepare(wa)
    prepare(wb)
    ea = run_direct(wa)
    eb = asyncio.run(run_mcp(wb))
    wc = base / "direct2" / "ws"   # 대조군: 직접 호출을 한 번 더 돌려 바이트 차이가 MCP 때문이 아님을 보인다
    prepare(wc)
    run_direct(wc)
    st = steps()
    per_step, mism = [], 0
    for i, (s, a, b) in enumerate(zip(st, ea, eb), 1):
        same = norm(a) == norm(b)
        mism += not same
        label = s["kind"] if s["kind"] != "cmd" else s["name"]
        per_step.append({"i": i, "step": label, "ok_direct": a["ok"], "ok_mcp": b["ok"],
                         "code": None if a["ok"] else a["error"]["code"], "same_envelope": same})
    hashes = {k: (e[-1]["result"] if e[-1]["ok"] else None) for k, e in (("direct", ea), ("mcp", eb))}
    res = {"steps": len(st), "ra01_commands_in_batch": len(st[1]["commands"]), "per_step": per_step, "envelope_mismatches": mism,
           "hash_direct": hashes["direct"], "hash_mcp": hashes["mcp"],
           "hash_equal": hashes["direct"] == hashes["mcp"] and hashes["direct"] is not None,
           "escape_file_created_outside": any((w.parent / "escape.ifc").exists() for w in (wa, wb))}
    fa, fb = wa / IFC, wb / IFC
    res["ifc_semantic"] = equiv.semantic_diff(fa, fb) if fa.exists() and fb.exists() else {"identical": False, "reason": "파일 없음"}
    res["allowed_differences"] = ALLOWED
    if fa.exists() and fb.exists():
        res["ifc_data_sha256_equal_direct_vs_mcp"] = RA.sha256_data_section(fa) == RA.sha256_data_section(fb)
        res["ifc_data_sha256_equal_direct_vs_direct2"] = RA.sha256_data_section(fa) == RA.sha256_data_section(wc / IFC)
        res["ifc_semantic_direct_vs_direct2"] = equiv.semantic_diff(fa, wc / IFC)["identical"]
    # 참고: 원본 RA-01.ifc 와의 의미 비교는 코어 범위(벽·문만)라 같지 않다. 값은 기록만 한다
    if C.SAMPLE_IFC.exists() and fa.exists():
        d = equiv.semantic_diff(C.SAMPLE_IFC, fa)
        res["reference_vs_original_ra01"] = {"identical": d["identical"], "differing_parts": d["differing_parts"],
                                             "note": "코어는 벽·문·레벨·속성·재료만 표현(창·슬래브·기둥·계단·공간 제외)이라 원본과 다른 것이 정상"}
    res["pass"] = bool(mism == 0 and res["hash_equal"] and res["ifc_semantic"]["identical"] and not res["escape_file_created_outside"])
    Path(out).write_text(json.dumps(C.scrub(res), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("steps", "envelope_mismatches", "hash_equal", "pass")}, ensure_ascii=False))
    for p in per_step:
        if not p["same_envelope"]:
            print("불일치:", p)
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
