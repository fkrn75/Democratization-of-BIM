# SPDX-License-Identifier: MPL-2.0
"""S11 시험 도구 공통부: 경로, 서버 실행 명령, 실제 클라이언트(claude, codex) 비대화 실행기, 출력 해석기.

원본 대화 기록(stream-json 등)에는 사용자 경로·계정 정보가 섞일 수 있어 저장소 밖(S11_RAW)에만 쓴다.
저장소에 남기는 것은 값만 뽑은 요약(summarize_* 의 반환)이다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
S1 = REPO / "spikes" / "s1-core"
SRC = S1 / "src"
S1_TOOLS = S1 / "tools"
PY = S1 / ".venv" / "Scripts" / "python.exe"          # 코어 의존성이 든 venv (PATH 의 python 은 Store stub)
TAP = HERE / "stdio_tap.py"
SAMPLE_IFC = REPO / "spikes" / "s0-baseline" / "RA-01.ifc"

RAW = Path(os.environ.get("S11_RAW") or Path(tempfile.gettempdir()) / "s11-raw")
WORK = Path(os.environ.get("S11_WORK") or Path(tempfile.gettempdir()) / "s11-work")
RAW.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)

# npm codex 의 네이티브 실행 파일(codex.cmd 가 node 래퍼로 띄우는 것). 배치 파일 인자 인용 문제를 피하려고 직접 실행한다
CODEX_NPM = str(next(iter((Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@openai" / "codex").glob(
    "node_modules/@openai/codex-win32-x64/vendor/*/bin/codex.exe")), "codex"))
CODEX_DESKTOP_GLOB = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"

DOBIM_TOOLS = ["dobim_commands_search", "dobim_command_describe", "dobim_query", "dobim_execute",
               "dobim_execute_destructive", "dobim_job", "dobim_render", "dobim_project"]
SEARCH_TOOLS = {"dobim_commands_search", "dobim_command_describe"}

sys.path.insert(0, str(S1 / "src"))
sys.path.insert(0, str(S1_TOOLS))


def codex_desktop_exe() -> str | None:
    for p in sorted(CODEX_DESKTOP_GLOB.glob("*/codex.exe")):
        return str(p)
    return None


def server_env() -> dict:
    return {"PYTHONPATH": str(SRC), "PYTHONUTF8": "1"}


def server_argv(workspace: Path, tap_log: Path | None = None) -> list[str]:
    """서버 실행 인자 배열. 인자는 셸 문자열이 아니라 배열로 전달한다(15.10)."""
    core = [str(PY), "-m", "dobim_core", "--workspace", str(workspace), "mcp", "serve"]
    if tap_log is None:
        return core
    return [str(PY), str(TAP), "--log", str(tap_log), "--", *core]


# ---------------------------------------------------------------- CLI (기준 실행)
def cli(workspace: Path, name: str, args: dict | None = None, project: str | None = None, **flags) -> tuple[dict, int]:
    """CLI 서브프로세스로 커맨드를 실행한다(결정적 기준·상태 판정용)."""
    cmd = [str(PY), "-m", "dobim_core", "--workspace", str(workspace), "--actor", "cli", "run", name,
           "--json", json.dumps(args or {})]
    if project:
        cmd += ["-p", project]
    if flags.get("dry_run"):
        cmd.append("--dry-run")
    if flags.get("confirm"):
        cmd += ["--confirm", flags["confirm"]]
    p = subprocess.run(cmd, capture_output=True, env={**os.environ, **server_env()}, stdin=subprocess.DEVNULL,
                       cwd=workspace, timeout=180)
    try:
        return json.loads(p.stdout.decode("utf-8")), p.returncode
    except ValueError:
        raise RuntimeError(f"CLI 출력이 JSON 이 아니다: {p.stdout[:200]!r} {p.stderr[-300:]!r}")


def build_fixture(workspace: Path, name: str = "p.dobim") -> None:
    """기준 프로젝트(fixture.SCRIPT)를 CLI 로 만든다."""
    from dobim_core import fixture as F
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / name).unlink(missing_ok=True)
    e, _ = cli(workspace, "project.new", {"path": name, "guid": F.GUID})
    assert e["ok"], e
    for n, a in F.SCRIPT:
        e, _ = cli(workspace, n, a, project=name)
        assert e["ok"], (n, e)


# ---------------------------------------------------------------- 클라이언트 실행
def claude_cfg(workspace: Path, tap_log: Path | None, path: Path) -> Path:
    cfg = {"mcpServers": {"dobim": {"command": server_argv(workspace, tap_log)[0],
                                    "args": server_argv(workspace, tap_log)[1:], "env": server_env()}}}
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return path


def run_claude(prompt: str, workspace: Path, tag: str, *, model: str = "sonnet", timeout: int = 600,
               tap: bool = True, extra: list[str] | None = None) -> dict:
    """실제 claude 를 비대화로 실행한다. 원본은 RAW/<tag>.* 에 둔다."""
    tap_log = RAW / f"{tag}.tap.jsonl" if tap else None
    if tap_log:
        tap_log.unlink(missing_ok=True)
    cfg = claude_cfg(workspace, tap_log, RAW / f"{tag}.mcp.json")
    allowed = [f"mcp__dobim__{t}" for t in DOBIM_TOOLS]
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--model", model,
           "--strict-mcp-config", "--mcp-config", str(cfg), "--allowedTools", *allowed,
           "--tools", "", "--setting-sources", "project", "--no-session-persistence", *(extra or []), "--", prompt]
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, cwd=workspace, stdin=subprocess.DEVNULL, timeout=timeout)
    wall = time.time() - t
    (RAW / f"{tag}.out.jsonl").write_bytes(p.stdout)
    (RAW / f"{tag}.err.txt").write_bytes(p.stderr)
    return {"tag": tag, "client": "claude", "exit": p.returncode, "wall_s": round(wall, 1), "model": model,
            "raw": RAW / f"{tag}.out.jsonl", "tap": tap_log}


def toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)   # JSON 문자열은 TOML 기본 문자열과 호환된다(\\ 이스케이프 포함)


def codex_overrides(workspace: Path, tap_log: Path | None, *, with_systemroot: bool = True, approval: str | None = "approve",
                    startup: int | None = None, tool_timeout: int | None = None) -> list[str]:
    argv = server_argv(workspace, tap_log)
    env = dict(server_env())
    if with_systemroot:
        env["SystemRoot"] = os.environ.get("SystemRoot", r"C:\Windows")
    ov = [f"mcp_servers.dobim.command={toml_str(argv[0])}",
          "mcp_servers.dobim.args=[" + ",".join(toml_str(a) for a in argv[1:]) + "]",
          "mcp_servers.dobim.env={" + ",".join(f"{k}={toml_str(v)}" for k, v in env.items()) + "}"]
    if approval:
        ov.append(f"mcp_servers.dobim.default_tools_approval_mode={toml_str(approval)}")
    if startup:
        ov.append(f"mcp_servers.dobim.startup_timeout_sec={startup}")
    if tool_timeout:
        ov.append(f"mcp_servers.dobim.tool_timeout_sec={tool_timeout}")
    return ov


def run_codex(prompt: str, workspace: Path, tag: str, *, exe: str, timeout: int = 600, tap: bool = True,
              model: str | None = None, ignore_user_config: bool = True, sandbox: str = "workspace-write",
              with_systemroot: bool = True, approval: str | None = "approve", extra: list[str] | None = None) -> dict:
    tap_log = RAW / f"{tag}.tap.jsonl" if tap else None
    if tap_log:
        tap_log.unlink(missing_ok=True)
    cmd = [exe, "exec", "--json", "--skip-git-repo-check", "-s", sandbox, "-C", str(workspace)]
    if ignore_user_config:
        cmd.append("--ignore-user-config")
    if model:
        cmd += ["-m", model]
    for o in codex_overrides(workspace, tap_log, with_systemroot=with_systemroot, approval=approval):
        cmd += ["-c", o]
    cmd += extra or []
    cmd += ["--", prompt]
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, cwd=workspace, stdin=subprocess.DEVNULL, timeout=timeout)
    wall = time.time() - t
    (RAW / f"{tag}.out.jsonl").write_bytes(p.stdout)
    (RAW / f"{tag}.err.txt").write_bytes(p.stderr)
    return {"tag": tag, "client": "codex", "exit": p.returncode, "wall_s": round(wall, 1), "exe": exe,
            "raw": RAW / f"{tag}.out.jsonl", "tap": tap_log}


# ---------------------------------------------------------------- 해석: 클라이언트 출력 → 정규화된 도구 호출 목록
def _jl(path: Path) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _env_of(text: str | None):
    if not text:
        return None
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) and "ok" in v else None
    except ValueError:
        return None


def parse_claude(path: Path) -> dict:
    """claude stream-json 에서 dobim 도구 호출·결과·사용량·최종 결과를 뽑는다."""
    ev = _jl(path)
    calls, pending = [], {}
    usage, result, init = {}, None, None
    for e in ev:
        t = e.get("type")
        if t == "system" and e.get("subtype") == "init":
            init = e
        elif t == "assistant":
            for b in e.get("message", {}).get("content", []) or []:
                if b.get("type") == "tool_use":
                    pending[b["id"]] = {"tool": b["name"].removeprefix("mcp__dobim__"), "input": b.get("input") or {}}
        elif t == "user":
            c = e.get("message", {}).get("content")
            if isinstance(c, list):
                for b in c:
                    if b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                        txt = b.get("content")
                        if isinstance(txt, list):
                            txt = "".join(x.get("text", "") for x in txt if isinstance(x, dict))
                        rec = pending.pop(b["tool_use_id"])
                        rec.update(result_text=txt or "", is_error=bool(b.get("is_error")), envelope=_env_of(txt))
                        calls.append(rec)
        elif t == "result":
            result = e
    for rec in pending.values():          # 결과를 못 받은 호출
        rec.update(result_text="", is_error=True, envelope=None, no_result=True)
        calls.append(rec)
    if result:
        usage = {"usage": result.get("usage"), "total_cost_usd": result.get("total_cost_usd"),
                 "modelUsage": result.get("modelUsage"), "num_turns": result.get("num_turns"),
                 "duration_ms": result.get("duration_ms"), "is_error": result.get("is_error"),
                 "subtype": result.get("subtype")}
    tools = None
    mcp = None
    if init:
        tools = [x for x in init.get("tools", []) if "dobim" in x]
        mcp = init.get("mcp_servers")
    return {"calls": calls, "usage": usage, "final_text": (result or {}).get("result", ""), "init_tools": tools,
            "init_mcp": mcp, "model": (init or {}).get("model"), "version": (init or {}).get("claude_code_version")}


def parse_codex(path: Path) -> dict:
    """codex exec --json 에서 MCP 도구 호출·결과·사용량을 뽑는다. 이벤트 형식은 버전마다 다를 수 있어 방어적으로 읽는다."""
    ev = _jl(path)
    calls, usage, final, errors = [], {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                                       "reasoning_output_tokens": 0}, "", []
    turns = 0
    item_types: dict = {}
    for e in ev:
        t = e.get("type")
        it = e.get("item") or {}
        if t == "item.completed":
            item_types[it.get("type")] = item_types.get(it.get("type"), 0) + 1
        if t == "item.completed" and it.get("type") == "mcp_tool_call":
            res = it.get("result") or {}
            content = res.get("content") or []
            txt = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            calls.append({"tool": it.get("tool"), "server": it.get("server"), "input": it.get("arguments") or {},
                          "result_text": txt, "is_error": it.get("status") == "failed" or bool(it.get("error")),
                          "envelope": _env_of(txt), "has_structured": res.get("structured_content") is not None,
                          "error": it.get("error"), "status": it.get("status")})
        elif t == "item.completed" and it.get("type") == "agent_message":
            final = it.get("text", "")
        elif t == "turn.completed":
            turns += 1
            for k in usage:
                usage[k] += (e.get("usage") or {}).get(k, 0) or 0
        elif t in ("error", "turn.failed"):
            errors.append(e.get("message") or json.dumps(e)[:300])
    return {"calls": calls, "usage": usage, "final_text": final, "errors": errors, "turns": turns, "item_types": item_types}


def parse_tap(path: Path | None) -> dict:
    """중계기 기록에서 프로토콜 사실을 뽑는다."""
    if not path or not Path(path).exists():
        return {}
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    info: dict = {"messages": len(rows)}
    req_t: dict = {}
    lat = []
    nonjson = 0
    for r in rows:
        k = r["k"]
        if k == "c2s":
            try:
                m = json.loads(r["raw"])
            except ValueError:
                continue
            if m.get("method") == "initialize":
                p = m.get("params", {})
                info["initialize"] = {"protocolVersion": p.get("protocolVersion"), "clientInfo": p.get("clientInfo"),
                                      "capabilities": sorted((p.get("capabilities") or {}).keys())}
                info["t_initialize_sent"] = r["t"]
            if "id" in m and m.get("method"):
                req_t[m["id"]] = (r["t"], m["method"], (m.get("params") or {}).get("name"),
                                  ((m.get("params") or {}).get("arguments") or {}).get("command"))
        elif k == "s2c":
            if not r.get("json"):
                nonjson += 1
                continue
            m = json.loads(r["raw"])
            if "result" in m and "protocolVersion" in (m["result"] or {}):
                info["negotiated"] = m["result"]["protocolVersion"]
                info["t_initialize_done"] = r["t"]
                info["server_capabilities"] = sorted((m["result"].get("capabilities") or {}).keys())
            if "id" in m and m["id"] in req_t:
                t0, method, name, cmd = req_t[m["id"]]
                res = m.get("result") or {}
                lat.append({"method": method, "tool": name, "command": cmd, "ms": round((r["t"] - t0) * 1000, 1),
                            "bytes": r["nbytes"], "is_error": bool(res.get("isError"))})
        elif k == "first_stdout":
            info["t_first_stdout"] = r["t"]
        elif k == "spawn":
            info["t_spawn"] = r["t"]
        elif k == "child_exit":
            info["child_exit_t"] = r["t"]
            info["child_exit_code"] = r["code"]
        elif k == "client_stdin_eof":
            info["client_stdin_eof_t"] = r["t"]
    info["non_json_stdout_lines"] = nonjson
    info["latencies"] = lat
    return info


def latency_summary(lat: list[dict]) -> dict:
    """tools/call 응답 시간 요약(ms): 개수, 중앙값, 최댓값과 가장 느린 호출, initialize 시간."""
    import statistics
    calls = [x for x in lat if x["method"] == "tools/call"]
    init = [x for x in lat if x["method"] == "initialize"]
    out = {"tool_calls": len(calls), "initialize_ms": init[0]["ms"] if init else None}
    if calls:
        worst = max(calls, key=lambda x: x["ms"])
        out.update(median_ms=round(statistics.median(x["ms"] for x in calls), 1), max_ms=worst["ms"],
                   slowest_tool=worst["tool"], max_response_wire_bytes=max(x["bytes"] for x in calls))
    return out


def est_tokens(nbytes_or_text) -> int:
    """응답 크기에서 토큰을 추정한다. 한글·기호가 섞인 JSON 을 보수적으로 잡아 UTF-8 바이트 / 3 (정밀 토크나이저 없음)."""
    n = len(nbytes_or_text.encode("utf-8")) if isinstance(nbytes_or_text, str) else int(nbytes_or_text)
    return (n + 2) // 3


def scrub(obj):
    """결과 JSON 에서 사용자 경로·이름이 새지 않게 문자열을 치환한다."""
    s = json.dumps(obj, ensure_ascii=False)
    for pat, rep in ((re.escape(str(Path.home())).replace("\\\\", r"[\\/]{1,2}"), "<HOME>"),
                     (r"C:[\\/]{1,2}Users[\\/]{1,2}[^\\/\"]+", "<HOME>")):
        s = re.sub(pat, rep, s, flags=re.I)
    return json.loads(s)
