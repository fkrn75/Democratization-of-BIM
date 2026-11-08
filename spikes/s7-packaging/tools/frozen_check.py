# SPDX-License-Identifier: MPL-2.0
"""S7 합격 기준 1: 동결본(dobim.exe, dobim-mcp.exe)이 소스 실행(venv)과 같은 결과를 내는가.

A. 기준 프로젝트(fixture.SCRIPT, RA-01 급 결정적 스크립트)를 커맨드마다 새 CLI 프로세스로 실행 -> 의미 해시 3종 비교
   (venv 실행, 동결 실행, S1 이 기록해 둔 값 셋이 모두 같아야 한다)
B. 무작위 열(시드 고정)을 생성기로 만들어 oracle(프로세스 안 엔진)에 적용하고, 같은 열을
   venv MCP 서버와 동결 MCP 서버(dobim-mcp.exe)에 재생 -> 항목별 ok 와 최종 해시 3종 비교
C. model.validate(express), ifc.export 가 동결본에서도 같은 결과인가
D. 시작 시간과 크기 측정
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (EXE, MCP_EXE, FROZEN_DIR, RESULTS, S1, S1_SRC, S1_TOOLS, run_quiet, venv_cmd, venv_env,  # noqa: E402
                    write_json)
sys.path.insert(0, str(S1_TOOLS))
sys.path.insert(0, str(S1_SRC))
import kit  # noqa: E402
from dobim_core import fixture as F, registry  # noqa: E402


class Side:
    """CLI 한 쪽(venv 또는 동결본). 호출마다 새 프로세스."""

    def __init__(self, name, base_cmd, env, ws: Path):
        self.name, self.base, self.env, self.ws = name, base_cmd, env, ws
        ws.mkdir(parents=True, exist_ok=True)

    def run(self, name, args, project=None, extra=()):
        cmd = [*self.base, "--workspace", self.ws, "--actor", "t", "run", name, "--json", json.dumps(args)]
        if project:
            cmd += ["-p", project]
        cmd += list(extra)
        p = run_quiet(cmd, env=self.env, cwd=self.ws)
        return json.loads(p.stdout.decode("utf-8")), p.returncode

    def hash(self, project):
        e, _ = self.run("model.hash", {}, project=project)
        return e["result"]


def triple(h):
    return {k: h[k] for k in ("state", "derived", "ifc")}


def sides(work: Path):
    return (Side("venv", venv_cmd(), venv_env(), work / "venv"),
            Side("frozen", [EXE], {**os.environ, "PYTHONUTF8": "1"}, work / "frozen"))


def part_a(work: Path) -> dict:
    v, fz = sides(work / "a")
    s1_ref = json.loads((S1 / "results" / "s1_parity.json").read_text(encoding="utf-8"))["fixture_hashes"]
    out = {"cli_process_launches_per_side": len(F.SCRIPT) + 2}
    for sd in (v, fz):
        e, _ = sd.run("project.new", {"path": "fx.dobim", "guid": F.GUID})
        assert e["ok"], e
        for name, args in F.SCRIPT:
            e, _ = sd.run(name, args, project="fx.dobim")
            assert e["ok"], (sd.name, name, e)
    hv, hf = v.hash("fx.dobim"), fz.hash("fx.dobim")
    out.update({"venv": triple(hv), "frozen": triple(hf), "s1_recorded": {k: s1_ref[k] for k in ("state", "derived", "ifc")}})
    out["equal_venv_frozen"] = triple(hv) == triple(hf)
    out["equal_s1_recorded"] = triple(hf) == out["s1_recorded"]
    out["pass"] = out["equal_venv_frozen"] and out["equal_s1_recorded"]
    return out


class Mcp:
    def __init__(self, name, command, args, env, ws: Path):
        from mcp import Client, StdioServerParameters
        ws.mkdir(parents=True, exist_ok=True)
        self.name, self.ws = name, ws
        self.params = StdioServerParameters(command=str(command), args=[str(a) for a in args], env=env, cwd=str(ws))
        self._Client = Client

    async def __aenter__(self):
        t0 = time.perf_counter()
        self.cm = self._Client(self.params)
        self.c = await self.cm.__aenter__()
        self.init_sec = round(time.perf_counter() - t0, 3)
        return self

    async def __aexit__(self, *a):
        await self.cm.__aexit__(*a)

    async def item(self, it: dict, project: str) -> dict:
        if it["op"] == "batch":
            r = await self.c.call_tool("dobim_execute", {"commands": [{"command": a, "args": b} for a, b in it["commands"]],
                                                         "project": project})
        else:
            risk = registry.load()[it["name"]].risk
            tool = {"read": "dobim_query", "write": "dobim_execute"}.get(risk, "dobim_execute_destructive")
            r = await self.c.call_tool(tool, {"command": it["name"], "args": it["args"], "project": project})
        return r.structured_content

    async def hash(self, project):
        r = await self.c.call_tool("dobim_query", {"command": "model.hash", "project": project})
        return r.structured_content["result"]


async def part_b(work: Path, seeds, n) -> dict:
    env_v, env_f = venv_env(), {**os.environ, "PYTHONUTF8": "1"}
    runs = []
    for seed in seeds:
        proj = f"seed{seed}.dobim"
        (work / "b").mkdir(parents=True, exist_ok=True)
        sess = kit.Session(work / "b" / f"oracle{seed}.dobim")
        items, oks = kit.generate_and_run(sess, seed, n)
        ho = triple(sess.eng.hashes())
        sess.close()
        rec = {"seed": seed, "items": len(items), "ok_items": sum(oks), "failed_items": len(oks) - sum(oks),
               "batches": sum(1 for i in items if i["op"] == "batch"),
               "undo_redo": sum(1 for i in items if i["op"] == "cmd" and i["name"].startswith("history."))}
        for name, cmd, args, env in (("venv", venv_cmd()[0], ["-m", "dobim_core", "--workspace", None, "mcp", "serve"], env_v),
                                     ("frozen", MCP_EXE, ["--workspace", None], env_f)):
            ws = work / "b" / f"{name}{seed}"
            args = [str(ws) if a is None else a for a in args]
            async with Mcp(name, cmd, args, env, ws) as m:
                # 프로젝트는 MCP 에 project.new 커맨드로 만든다
                r = await m.c.call_tool("dobim_execute", {"command": "project.new", "args": {"path": proj, "guid": F.GUID}})
                assert r.structured_content["ok"], r.structured_content
                t0 = time.time()
                got = [bool((await m.item(it, proj))["ok"]) for it in items]
                rec[name] = {"hash": triple(await m.hash(proj)), "ok_flags_equal_oracle": got == oks,
                             "seconds": round(time.time() - t0, 1)}
        rec["oracle_hash"] = ho
        rec["pass"] = (rec["venv"]["hash"] == rec["frozen"]["hash"] == ho and rec["frozen"]["ok_flags_equal_oracle"]
                       and rec["venv"]["ok_flags_equal_oracle"])
        runs.append(rec)
    return {"runs": runs, "pass": all(r["pass"] for r in runs)}


def part_c(work: Path) -> dict:
    v, fz = sides(work / "c")
    out = {}
    for sd in (v, fz):
        sd.run("project.new", {"path": "c.dobim", "guid": F.GUID})
        for name, args in F.SCRIPT:
            sd.run(name, args, project="c.dobim")
        val, _ = sd.run("model.validate", {"express_rules": True}, project="c.dobim")
        exp, _ = sd.run("ifc.export", {"path": "out.ifc"}, project="c.dobim")
        tok = exp.get("error", {}).get("details", {}).get("confirm_token")
        exp2 = sd.run("ifc.export", {"path": "out.ifc"}, project="c.dobim", extra=["--confirm", tok])[0] if tok else exp
        data = (sd.ws / "out.ifc").read_bytes() if (sd.ws / "out.ifc").exists() else b""
        out[sd.name] = {"validate": val, "export_ok": exp2.get("ok"), "ifc_bytes": len(data), "ifc_head": data[:14].decode("ascii", "replace")}

    def body(p):
        txt = Path(p).read_text(encoding="utf-8", errors="replace").splitlines()
        # 관계·속성셋의 GUID 는 호출마다 새로 만들어지므로(의미 해시는 GUID 를 제외한다) 22자 GUID 자리를 가리고 비교한다
        return [re.sub(r"'[0-9A-Za-z_$]{22}'", "'<guid>'", ln) for ln in txt if not ln.startswith("FILE_NAME")]

    out["validate_equal"] = out["venv"]["validate"] == out["frozen"]["validate"]
    out["export_body_equal"] = body(v.ws / "out.ifc") == body(fz.ws / "out.ifc")
    for k in ("venv", "frozen"):
        out[k]["validate_ok"] = out[k]["validate"].get("ok")
        out[k]["validate"] = {"ok": out[k]["validate"].get("ok"), "result_head": json.dumps(out[k]["validate"].get("result"), ensure_ascii=False)[:300]}
    out["pass"] = bool(out["validate_equal"] and out["export_body_equal"] and out["frozen"]["validate_ok"] and out["frozen"]["export_ok"])
    return out


def part_e(work: Path) -> dict:
    """E. 레지스트리의 모든 커맨드 예시를 venv CLI 와 동결 CLI 에 실행해 결과 봉투를 비교한다 (S1 패리티 C 의 PRE·확인 토큰 절차를 따른다)."""
    import parity
    v, fz = sides(work / "e")
    base = work / "e" / "base.dobim"
    s = kit.Session(base)
    for name, args in F.SCRIPT:
        assert s.run(name, args)["ok"]
    s.close()
    cmds = registry.load()
    rows, bad = [], []
    for name in sorted(cmds):
        for i, ex in enumerate(cmds[name].examples):
            proj = f"{name.replace('.', '_')}_{i}.dobim"
            envs = []
            for sd in (v, fz):
                if cmds[name].needs_project:
                    shutil.copy(base, sd.ws / proj)
                use = proj if cmds[name].needs_project else None
                for pre in parity.PRE.get(name, []):
                    e1, _ = sd.run(pre[0], pre[1], project=use)
                    if len(pre) > 2:
                        sd.run(pre[0], pre[1], project=use, extra=["--confirm", e1["error"]["details"]["confirm_token"]])
                e, code = sd.run(name, ex, project=use)
                second = None
                if cmds[name].risk == "destructive" and not e["ok"] and e["error"]["code"] == "CONFIRM_REQUIRED":
                    second = sd.run(name, ex, project=use, extra=["--confirm", e["error"]["details"]["confirm_token"]])
                h = sd.hash(use) if use else None
                envs.append((parity.norm(e), code, parity.norm(second[0]) if second else None, h and triple(h)))
            same = envs[0] == envs[1]
            rows.append({"command": name, "example": i, "same": same, "ok": envs[1][0]["ok"]})
            if not same:
                bad.append(f"{name}#{i}")
    return {"example_runs": len(rows), "commands_covered": len({r['command'] for r in rows}), "commands_in_registry": len(cmds),
            "mismatches": bad, "pass": not bad and len({r['command'] for r in rows}) == len(cmds)}


def timing(work: Path, reps=10) -> dict:
    v, fz = sides(work / "t")
    res = {}
    for sd in (v, fz):
        sd.run("project.new", {"path": "t.dobim", "guid": F.GUID})
        for name, args in F.SCRIPT:
            sd.run(name, args, project="t.dobim")
    for sd in (v, fz):
        r = {}
        for label, tail in (("list_s", ["list", "--query", "wall"]), ("model_hash_s", ["run", "model.hash", "-p", "t.dobim"])):
            ts = []
            for _ in range(reps):
                t0 = time.perf_counter()
                run_quiet([*sd.base, "--workspace", sd.ws, *tail], env=sd.env, cwd=sd.ws)
                ts.append(time.perf_counter() - t0)
            r[label] = {"min": round(min(ts), 3), "median": round(statistics.median(ts), 3), "max": round(max(ts), 3)}
        res[sd.name] = r
    return res


def tree_size(p: Path) -> dict:
    files = [f for f in p.rglob("*") if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    return {"files": len(files), "bytes": total, "mib": round(total / 2**20, 1)}


def main():
    work = Path(tempfile.mkdtemp(prefix="s7-frozen-"))
    try:
        out = {"frozen_dir_size": tree_size(FROZEN_DIR),
               "ifcopenshell_dir_size": tree_size(FROZEN_DIR / "_internal" / "ifcopenshell")}
        out["A_fixture_hashes"] = part_a(work)
        print("A", out["A_fixture_hashes"]["pass"], flush=True)
        out["C_validate_export"] = part_c(work)
        print("C", out["C_validate_export"]["pass"], flush=True)
        out["E_all_command_examples_venv_vs_frozen"] = part_e(work)
        print("E", out["E_all_command_examples_venv_vs_frozen"], flush=True)
        out["D_timing"] = timing(work)
        print("D", out["D_timing"], flush=True)
        seeds = [int(x) for x in os.environ.get("S7_SEEDS", "1,2,3").split(",")]
        out["B_generated_sequences_via_mcp"] = asyncio.run(part_b(work, seeds, int(os.environ.get("S7_N", "300"))))
        print("B", out["B_generated_sequences_via_mcp"]["pass"], flush=True)
        out["pass"] = all(out[k]["pass"] for k in ("A_fixture_hashes", "B_generated_sequences_via_mcp", "C_validate_export",
                                                      "E_all_command_examples_venv_vs_frozen"))
        write_json(RESULTS / "s7_frozen_check.json", out)
        print(json.dumps({"pass": out["pass"]}))
        return 0 if out["pass"] else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
