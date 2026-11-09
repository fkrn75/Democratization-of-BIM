# SPDX-License-Identifier: MPL-2.0
"""합격 기준 4: 레지스트리의 모든 커맨드가 CLI 와 MCP 에서 같은 결과를 내는가 (패리티 C).

방법: 같은 기준 프로젝트(fixture.SCRIPT)의 복사본 두 개에 각 커맨드의 examples 를 한쪽은 CLI 서브프로세스로,
다른 쪽은 공식 SDK 클라이언트로 stdio MCP 서버에 실행한다. 결과 봉투(시각·토큰 제외)와 실행 뒤 모델 해시를 비교한다.
추가로 오류 사례(스키마·없는 대상·리비전 충돌·경로 거부·확인 토큰)도 양쪽에서 비교한다.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kit  # noqa: E402
from dobim_core import fixture as F, registry  # noqa: E402
from dobim_core.errors import exit_code_of  # noqa: E402
from dobim_core.engine import Engine  # noqa: E402

SRC = str(HERE.parent / "src")
# 예시 실행 전에 필요한 준비 (양쪽에 같은 경로로 실행한다)
_EXP = ("ifc.export", {"path": "export-example.ifc"}, True)   # True = 파괴적 커맨드의 확인 토큰 2단계
_V1 = ("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200})   # S3: 뷰가 있어야 하는 예시의 준비
_A1 = ("annot.dim.create", {"id": F.A1, "view": F.V1, "a": f"wall:{F.W1}#trim.start", "b": f"wall:{F.W1}#trim.end"})   # S5: 주석이 있어야 하는 예시의 준비
# family.set 예시는 이미 놓인 인스턴스를 고친다: 같은 id 로 family.place 예시(첫 번째)를 먼저 실행한다
_FAMILY_PRE = ("family.place", {"id": "0" * 21 + "1", "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 1500})
PRE = {"history.redo": [("history.undo", {})], "ifc.restore": [_EXP, _EXP], "ifc.snapshots": [_EXP, _EXP],
       "view.set": [_V1], "view.delete": [_V1], "view.list": [_V1], "view.linework": [_V1], "view.export": [_V1], "view.affected": [_V1],
       "annot.dim.create": [_V1], "annot.tag.create": [_V1], "annot.rebind": [_V1, _A1], "annot.delete": [_V1, _A1],
       "annot.list": [_V1, _A1], "annot.check": [_V1, _A1],
       "family.set": [_FAMILY_PRE]}
ERROR_CASES = [
    ("없는 커맨드", "no.such.command", {}, {}),
    ("스키마 오류(타입)", "wall.move", {"id": F.W1, "dx": "abc"}, {}),
    ("스키마 오류(필수 누락)", "wall.create", {"level": F.LV1}, {}),
    ("스키마 오류(추가 필드)", "wall.move", {"id": F.W1, "dz": 1}, {}),
    ("대상 없음", "wall.move", {"id": "9" * 22, "dx": 1}, {}),
    ("제약 위반", "door.place", {"host": F.W2, "offset": 9_000_000, "width": 900}, {}),
    ("리비전 충돌", "wall.move", {"id": F.W1, "dx": 1}, {"expected_rev": 1}),
    ("경로 거부", "ifc.export", {"path": "../escape.ifc"}, {}),
    ("확인 토큰 없음", "ifc.export", {"path": "x.ifc"}, {}),
    ("확인 토큰 위조", "ifc.export", {"path": "x.ifc"}, {"confirm": "9999999999.deadbeefdeadbeefdeadbeef"}),
    ("내보내기 확장자 거부", "ifc.export", {"path": "x.txt"}, {}),
    ("내보내기 .dobim 거부", "ifc.export", {"path": "ex.dobim"}, {}),
    ("내보내기 -wal 거부", "ifc.export", {"path": "ex.dobim-wal"}, {}),
    # NaN·inf 는 표준 JSON 에 없다: 공식 SDK 클라이언트가 null 로 바꿔 보내므로 양쪽에 같은 입력(null)을 줄 수 없다.
    # 그래서 null 이 속성 삭제가 아니라 스키마 오류인지(N7)를 여기서 비교하고, SDK 로 NaN/inf 를 실제로 보내 속성이 남는지는
    # tests/test_review3.py 가 확인한다. 삭제는 별도 커맨드 pset.delete 이며 모든 커맨드의 예시로 양쪽에서 실행된다.
    ("속성값 null(삭제 표기 아님)", "pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": None}, {}),
    ("속성값 null, 이미 있는 속성", "pset.set", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": None}, {}),
    ("속성 이름 id 거부(쓰기)", "pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "id", "value": 1}, {}),
    ("속성 이름 id 거부(삭제)", "pset.delete", {"id": F.W1, "pset": "Pset_Z", "prop": "id"}, {}),
    ("속성 삭제(있는 속성)", "pset.delete", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating"}, {}),
    ("속성 삭제(없는 속성)", "pset.delete", {"id": F.W1, "pset": "Pset_Z", "prop": "P"}, {}),
    ("속성 삭제(예약 속성셋)", "pset.delete", {"id": F.W1, "pset": "DoBIM_Intent", "prop": "Intent"}, {}),
    ("wall.set 숫자 필드 명시적 null", "wall.set", {"id": F.W1, "thickness": None, "height": 3100}, {}),
    ("door.set 숫자 필드 명시적 null", "door.set", {"id": F.D1, "width": 1000, "offset": None}, {}),
    ("wall.set 이름 명시적 null", "wall.set", {"id": F.W1, "name": None, "thickness": 250}, {}),
    ("목록 limit 최대", "element.query", {"limit": 1000}, {}),
    ("목록 이어받기 커서", "element.query", {"limit": 2, "cursor": "IjIwMDAwMDAwMDAwMDAwMDAwMDAwMDEi"}, {}),
    ("잘못된 커서", "schedule.rows", {"kind": "door", "cursor": "###"}, {}),
    ("commands.search 쪽 나누기", "commands.search", {"limit": 3}, {}),
    ("history.list 쪽 나누기", "history.list", {"limit": 3}, {}),
    ("위조 커서(음수)", "schedule.rows", {"kind": "door", "cursor": "LTE="}, {}),
    ("위조 커서(타입 불일치)", "relation.list", {"id": F.LV1, "cursor": "ImFiYyI="}, {}),
    ("위조 커서(history.list 에 문자열)", "history.list", {"cursor": "ImFiYyI="}, {}),
    ("끝을 지난 키셋 커서", "schedule.rows", {"kind": "door", "cursor": "WyJ6enp6IiwgInp6enoiLCAiOTk5OTk5OTk5OTk5OTk5OTk5OTk5OSJd"}, {}),
    ("history.affected 현재 리비전", "history.affected", {}, {}),
    ("history.affected 없는 리비전", "history.affected", {"rev": 9999}, {}),
    ("속성값 int64 초과", "pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": 2**63}, {}),
    ("속성값 NUL", "pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": "a\x00b"}, {}),
    ("dry-run", "wall.move", {"id": F.W1, "dx": 700}, {"dry_run": True}),
    ("되돌릴 것 없음", "history.redo", {}, {}),
    ("주석: 없는 뷰", "annot.dim.create", {"view": "9" * 22, "a": f"wall:{F.W1}#axis.start", "b": f"wall:{F.W1}#axis.end"}, {}),
    ("주석: 없는 역할", "annot.dim.create", {"view": F.V1, "a": f"wall:{F.W1}#axis.mid", "b": f"wall:{F.W1}#axis.end"}, {}),
    ("주석: 참조 문법 오류", "annot.dim.create", {"view": F.V1, "a": "wall:short#axis", "b": f"wall:{F.W1}#axis.end"}, {}),
    ("주석: 주석이 아닌 요소 삭제", "annot.delete", {"id": F.W1}, {}),
    ("벽 분할: 문을 가로지름", "wall.split", {"id": F.W1, "at": 1500}, {}),
    ("벽 분할: 끝 조각이 너무 짧음", "wall.split", {"id": F.W1, "at": 5999.5}, {}),
    ("병합 상대 파일 없음", "merge.preview", {"other": "no-such.dobim"}, {}),
    ("병합 상대 경로 거부", "merge.apply", {"other": "../escape.dobim"}, {}),
    ("병합 해결 키 잘못(무시됨)", "merge.apply", {"other": "other.dobim", "resolve": [{"key": "SAME_FIELD:x", "take": "ours"}]}, {}),
    ("병합 상대 리비전 고정 불일치", "merge.apply", {"other": "other.dobim", "other_rev": 1}, {}),
    ("병합 dry-run", "merge.apply", {"other": "other.dobim"}, {"dry_run": True}),
]


def norm(env: dict) -> dict:
    """시각·토큰처럼 호출마다 달라지는 값만 가린다."""
    env = json.loads(json.dumps(env))
    d = env.get("error", {}).get("details", {})
    if "confirm_token" in d:
        d["confirm_token"] = "<token>"
    return env


class CliSide:
    def __init__(self, ws: Path):
        self.ws = ws
        self.env = {**os.environ, "PYTHONPATH": SRC, "PYTHONUTF8": "1"}

    def call(self, name, args, project=None, expected_rev=None, dry_run=False, confirm=None) -> tuple[dict, int]:
        cmd = [sys.executable, "-m", "dobim_core", "--workspace", str(self.ws), "--actor", "cli", "run", name,
               "--json", json.dumps(args)]
        if project:
            cmd += ["-p", project]
        if expected_rev is not None:
            cmd += ["--expected-rev", str(expected_rev)]
        if dry_run:
            cmd.append("--dry-run")
        if confirm:
            cmd += ["--confirm", confirm]
        p = subprocess.run(cmd, capture_output=True, env=self.env, stdin=subprocess.DEVNULL, cwd=self.ws, timeout=120)
        out = p.stdout.decode("utf-8")
        try:
            env = json.loads(out)
        except ValueError:
            raise SystemExit(f"CLI stdout 이 JSON 이 아니다 ({name}): {out[:200]!r} / stderr {p.stderr.decode('utf-8', 'replace')[:300]}")
        if len(out.strip().splitlines()) != 1:
            raise SystemExit(f"CLI stdout 이 한 줄 JSON 이 아니다 ({name})")
        return env, p.returncode


class McpSide:
    def __init__(self, ws: Path):
        from mcp import Client, StdioServerParameters
        self.ws = ws
        self.params = StdioServerParameters(
            command=sys.executable, args=["-m", "dobim_core", "--workspace", str(ws), "--actor", "cli", "mcp", "serve"],
            env={**os.environ, "PYTHONPATH": SRC, "PYTHONUTF8": "1"})
        self._Client = Client
        self.client = None

    async def __aenter__(self):
        self.cm = self._Client(self.params)
        self.client = await self.cm.__aenter__()
        return self

    async def __aexit__(self, *a):
        await self.cm.__aexit__(*a)

    async def call_batch(self, commands, project) -> tuple[dict, bool]:
        r = await self.client.call_tool("dobim_execute", {"commands": commands, "project": project})
        assert r.structured_content == json.loads(r.content[0].text), "text 와 structuredContent 가 다르다"
        return r.structured_content, bool(r.is_error)

    async def call(self, name, args, project=None, expected_rev=None, dry_run=False, confirm=None) -> tuple[dict, bool]:
        cmd = registry.load().get(name)
        risk = cmd.risk if cmd else "write"
        a = {"command": name, "args": args}
        if project:
            a["project"] = project
        if risk == "read":
            tool = "dobim_query"
        elif risk == "write":
            tool = "dobim_execute"
            if expected_rev is not None:
                a["expected_rev"] = expected_rev
            if dry_run:
                a["dry_run"] = True
        else:
            tool = "dobim_execute_destructive"
            if confirm:
                a["confirm"] = confirm
        # 위험 등급과 도구가 맞지 않는 사례(read 커맨드에 dry_run 등)는 CLI 와 같은 결과가 나도록 execute 로 보낸다
        if cmd is None or (risk == "read" and (expected_rev is not None or dry_run)):
            tool = "dobim_execute"
            a.pop("project", None) if cmd is None else None
            if expected_rev is not None:
                a["expected_rev"] = expected_rev
            if dry_run:
                a["dry_run"] = True
        r = await self.client.call_tool(tool, a)
        env = r.structured_content
        text_env = json.loads(r.content[0].text)
        assert env == text_env, "text 와 structuredContent 가 다르다"
        return env, bool(r.is_error)


def _two_step(call, name, args, project, **kw):
    """파괴적 커맨드: 1회차는 확인 토큰을 받고 2회차에 실행."""
    first = call(name, args, project=project, **kw)
    return first


async def run_parity(work: Path | None = None) -> dict:
    work = Path(work or tempfile.mkdtemp(prefix="dobim-parity-"))
    wa, wb = work / "cli", work / "mcp"
    for w in (wa, wb):
        shutil.rmtree(w, ignore_errors=True)
        w.mkdir(parents=True)
    base = work / "fixture.dobim"
    base.unlink(missing_ok=True)
    s = kit.Session(base)
    for name, args in F.SCRIPT:
        assert s.run(name, args)["ok"]
    base_hashes = s.eng.hashes()
    s.eng.ifc.write(str(work / "import-example.ifc"))   # ifc.import 예시의 입력(기준 프로젝트를 내보낸 IFC): 양쪽 작업 폴더에 같은 파일을 둔다
    s.close()
    # 병합 예시(S8)의 상대 프로젝트: 기준 프로젝트에서 갈라져 몇 가지 바꾼 복사본. 양쪽 작업 폴더에 같은 파일을 둔다(읽기 전용으로만 쓰인다)
    other_src = work / "other_src.dobim"
    other_src.unlink(missing_ok=True)
    shutil.copy(base, other_src)
    so = kit.Session(other_src, "other", create=False)
    for name, args in (("wall.set", {"id": F.W2, "name": "병합 예시 벽"}), ("pset.set", {"id": F.D2, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI60"})):
        assert so.run(name, args)["ok"]
    so.close()
    for w in (wa, wb):
        shutil.copy(other_src, w / "other.dobim")
    for w in (wa, wb):
        shutil.copy(work / "import-example.ifc", w / "import-example.ifc")
    cli = CliSide(wa)
    results, mismatches = [], []
    cmds = registry.load()

    async with McpSide(wb) as mcp:
        tools = await mcp.client.list_tools()
        tool_names = sorted(t.name for t in tools.tools)

        async def both(name, args, label, use_pre=False, **kw):
            """같은 입력을 CLI 와 MCP 에 한 번씩 실행하고 비교한다."""
            needs = name in cmds and cmds[name].needs_project
            pa = pb = None
            if needs:
                pa, pb = f"{label}.dobim", f"{label}.dobim"
                shutil.copy(base, wa / pa)
                shutil.copy(base, wb / pb)
                for pre in (PRE.get(name, []) if use_pre else []):
                    pre_name, pre_args = pre[0], pre[1]
                    e1, _ = cli.call(pre_name, pre_args, project=pa)
                    e2, _ = await mcp.call(pre_name, pre_args, project=pb)
                    if len(pre) > 2:
                        e1, _ = cli.call(pre_name, pre_args, project=pa, confirm=e1["error"]["details"]["confirm_token"])
                        e2, _ = await mcp.call(pre_name, pre_args, project=pb, confirm=e2["error"]["details"]["confirm_token"])
                    assert norm(e1) == norm(e2) and e1["ok"], (pre_name, e1, e2)
            e1, code = cli.call(name, args, project=pa, **kw)
            e2, is_err = await mcp.call(name, args, project=pb, **kw)
            # 파괴적 커맨드는 1회차 응답의 토큰으로 2회차를 실행한다
            second = None
            if name in cmds and cmds[name].risk == "destructive" and not e1["ok"] and e1["error"]["code"] == "CONFIRM_REQUIRED" \
                    and kw.get("confirm") is None:
                t1 = e1["error"]["details"]["confirm_token"]
                t2 = e2["error"]["details"]["confirm_token"]
                f1, code2 = cli.call(name, args, project=pa, confirm=t1)
                f2, is_err2 = await mcp.call(name, args, project=pb, confirm=t2)
                second = {"cli": norm(f1), "mcp": norm(f2), "cli_exit": code2, "mcp_is_error": is_err2,
                          "both_ok": f1["ok"] and f2["ok"]}
                if norm(f1) != norm(f2) or code2 != exit_code_of(f1) or is_err2 != (not f2["ok"]):
                    mismatches.append(f"{label}: 2회차 불일치")
            same = norm(e1) == norm(e2)
            code_ok = code == exit_code_of(e1) and is_err == (not e2["ok"])
            h1 = h2 = None
            if needs:
                x1, _ = cli.call("model.hash", {}, project=pa)
                x2, _ = await mcp.call("model.hash", {}, project=pb)
                h1, h2 = x1["result"], x2["result"]
            rec = {"label": label, "command": name, "same_envelope": same, "exit_code": code, "exit_code_matches_envelope": code_ok,
                   "ok": e1["ok"], "state_hash_equal": h1 == h2, "second_step": second}
            results.append(rec)
            if not (same and code_ok and h1 == h2):
                mismatches.append(f"{label}: 봉투 {same}, 종료코드 {code_ok}, 해시 {h1 == h2}\n  CLI={json.dumps(norm(e1), ensure_ascii=False)[:400]}\n  MCP={json.dumps(norm(e2), ensure_ascii=False)[:400]}")
            return e1, e2

        # 1) 모든 커맨드의 모든 예시
        executed = set()
        for name in sorted(cmds):
            for i, ex in enumerate(cmds[name].examples):
                await both(name, ex, f"ex-{name.replace('.', '_')}-{i}", use_pre=True)
                executed.add(name)
        # 2) 스키마 설명 패리티: 모든 커맨드
        for name in sorted(cmds):
            e1, _ = cli.call("commands.describe", {"name": name})
            e2, _ = await mcp.call("commands.describe", {"name": name})
            if norm(e1) != norm(e2):
                mismatches.append(f"describe {name} 불일치")
        e1, _ = cli.call("commands.search", {"query": ""})
        e2, _ = await mcp.call("commands.search", {"query": ""})
        search_same = norm(e1) == norm(e2) and {i["name"] for i in e1["result"]["items"]} == set(cmds)
        if not search_same:
            mismatches.append("commands.search 불일치")
        # 3) 오류 사례
        for i, (label, name, args, kw) in enumerate(ERROR_CASES):
            await both(name, args, f"err-{i}", **kw)
        # 3b) 큰 배치(응답 상한으로 affected·results 가 잘림): CLI script --atomic 과 MCP commands 가 같은 봉투여야 한다
        big = [{"command": "wall.create", "args": {"id": "1" + f"{i + 100:021d}", "level": F.LV1, "axis": [0, 10000 + i * 10, 6000, 10000 + i * 10]}}
               for i in range(300)]
        for w in (wa, wb):
            shutil.copy(base, w / "bigbatch.dobim")
        (wa / "_big.jsonl").write_text(chr(10).join(json.dumps(c) for c in big), encoding="utf-8")
        pr = subprocess.run([sys.executable, "-m", "dobim_core", "--workspace", str(wa), "--actor", "cli", "script", str(wa / "_big.jsonl"),
                             "-p", "bigbatch.dobim", "--atomic"], capture_output=True, env=cli.env, stdin=subprocess.DEVNULL, cwd=wa, timeout=300)
        e1 = json.loads(pr.stdout.decode("utf-8"))
        e2, _ = await mcp.call_batch(big, "bigbatch.dobim")
        h1, _ = cli.call("model.hash", {}, project="bigbatch.dobim")
        h2, _ = await mcp.call("model.hash", {}, project="bigbatch.dobim")
        big_ok = (norm(e1) == norm(e2) and e1["ok"] and e1["affected"]["truncated"] is True and e1["affected"]["counts"]["created"] == 300
                  and h1["result"] == h2["result"])
        if not big_ok:
            mismatches.append(f"큰 배치 불일치: 봉투 {norm(e1) == norm(e2)}, truncated {e1.get('affected', {}).get('truncated')}, 해시 {h1['result'] == h2['result']}")
        results.append({"label": "big-batch", "command": "batch(wall.create x300)", "same_envelope": norm(e1) == norm(e2), "exit_code": pr.returncode,
                        "exit_code_matches_envelope": pr.returncode == exit_code_of(e1), "ok": e1["ok"], "state_hash_equal": h1["result"] == h2["result"],
                        "second_step": None})
        # 4) 위험 등급 도구 불일치는 양쪽에서 거부되어야 한다
        r = await mcp.client.call_tool("dobim_query", {"command": "wall.move", "args": {"id": F.W1}, "project": "x"})
        risk_ok = r.is_error and r.structured_content["error"]["code"] == "RISK_MISMATCH"
        r = await mcp.client.call_tool("dobim_execute", {"command": "ifc.export", "args": {"path": "a.ifc"}, "project": "x"})
        risk_ok = risk_ok and r.is_error and r.structured_content["error"]["code"] == "RISK_MISMATCH"

    # 음성 대조: MCP 쪽 프로젝트만 몰래 바꾸면 하네스가 불일치를 잡아내야 한다(하네스 자체의 증명)
    async with McpSide(wb) as mcp2:
        shutil.copy(base, wa / "ctl.dobim")
        shutil.copy(base, wb / "ctl.dobim")
        await mcp2.call("wall.move", {"id": F.W1, "dx": 1}, project="ctl.dobim")
        x1, _ = cli.call("model.hash", {}, project="ctl.dobim")
        x2, _ = await mcp2.call("model.hash", {}, project="ctl.dobim")
        control_detected = x1["result"] != x2["result"]
        y1, _ = cli.call("project.info", {}, project="ctl.dobim")
        y2, _ = await mcp2.call("project.info", {}, project="ctl.dobim")
        control_detected = control_detected and norm(y1) != norm(y2)

    out = {"negative_control_detects_divergence": control_detected, "commands_in_registry": len(cmds), "commands_executed_with_examples": len(executed),
           "coverage_percent": round(100 * len(executed) / len(cmds), 1),
           "example_runs": sum(1 for r in results if r["label"].startswith("ex-")),
           "error_case_runs": sum(1 for r in results if r["label"].startswith("err-")),
           "mcp_tools": tool_names, "describe_parity_all_commands": not any("describe" in m for m in mismatches),
           "search_parity": search_same, "risk_gating_enforced": bool(risk_ok),
           "mismatches": mismatches, "parity_C_percent": 0.0, "results": results, "fixture_hashes": base_hashes}
    good = sum(1 for r in results if r["same_envelope"] and r["exit_code_matches_envelope"] and r["state_hash_equal"])
    out["parity_C_percent"] = round(100 * good / len(results), 1)
    out["pass"] = control_detected and (not mismatches) and out["coverage_percent"] == 100.0 and bool(risk_ok) and len(tool_names) == 8
    return out


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    res = asyncio.run(run_parity())
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(text, encoding="utf-8")
    print(text if not res["pass"] else json.dumps({k: v for k, v in res.items() if k != "results"}, ensure_ascii=False, indent=2))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
