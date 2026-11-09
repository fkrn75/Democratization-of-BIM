# SPDX-License-Identifier: MPL-2.0
"""architecture.md 15.10절 Windows 항목 시험. 실제 클라이언트 없이 서버를 원시 JSON-RPC 로 직접 때려 측정한다.

항목: stdout 오염, 인코딩(PYTHONUTF8 유무), BOM, 한글·공백·긴 경로, 환경 변수(SystemRoot), 고아 프로세스·stdin EOF,
시작 시간, 파일 잠금(FILE_LOCKED). 코어는 고치지 않는다. 결함은 재현 방법과 함께 값으로 남긴다.
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import common as C
from dobim_core import fixture as F

PROTO = "2025-06-18"


class RawMcp:
    """최소 stdio MCP 클라이언트: 서버 stdout 의 모든 줄을 그대로 모아 JSON 여부를 검사한다."""

    def __init__(self, workspace: Path, *, env: dict | None = None, argv: list[str] | None = None):
        self.argv = argv or C.server_argv(workspace)
        t = time.perf_counter()
        self.p = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env if env is not None else {**os.environ, **C.server_env()},
                                  creationflags=0x08000000 if sys.platform == "win32" else 0)
        self.q: queue.Queue = queue.Queue()
        self.lines: list[bytes] = []
        self.err: list[bytes] = []
        threading.Thread(target=self._rd, daemon=True).start()
        threading.Thread(target=self._re, daemon=True).start()
        self.n = 0
        self.t_spawn = t

    def _rd(self):
        for line in iter(self.p.stdout.readline, b""):
            self.lines.append(line)
            self.q.put(line)
        self.q.put(None)

    def _re(self):
        for line in iter(self.p.stderr.readline, b""):
            self.err.append(line)

    def send(self, method: str, params: dict | None = None, notify: bool = False) -> int | None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        return None if notify else self.n

    def recv(self, rid: int, timeout: float = 60.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self.q.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                break
            if line is None:
                raise RuntimeError("서버가 응답 없이 종료했다 stderr=" + b"".join(self.err)[-300:].decode("utf-8", "replace"))
            try:
                m = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if m.get("id") == rid:
                return m
        raise TimeoutError(f"응답 시간 초과 id={rid}")

    def call(self, method, params=None, timeout=60.0):
        return self.recv(self.send(method, params), timeout)

    def handshake(self) -> float:
        t = time.perf_counter()
        r = self.call("initialize", {"protocolVersion": PROTO, "capabilities": {},
                                     "clientInfo": {"name": "s11-raw", "version": "0"}})
        self.send("notifications/initialized", notify=True)
        self.init_result = r["result"]
        return time.perf_counter() - t

    def tool(self, name, args, timeout=120.0) -> dict:
        r = self.call("tools/call", {"name": name, "arguments": args}, timeout)
        return r["result"]

    def env_of(self, name, args) -> dict:
        res = self.tool(name, args)
        if res.get("structuredContent"):
            return res["structuredContent"]
        text = res["content"][0]["text"]
        try:
            return json.loads(text)
        except ValueError:   # 봉투가 아닌 SDK 수준 오류 문자열
            return {"ok": False, "sdk_level_error": True, "isError": res.get("isError"), "error": {"code": "SDK_LEVEL", "message": text[:200]}}

    def close(self, wait: float = 10.0) -> float | None:
        """stdin 을 닫고 서버가 종료하기까지 걸린 초. 안 끝나면 None 후 강제 종료."""
        t = time.perf_counter()
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(timeout=wait)
            return round(time.perf_counter() - t, 3)
        except subprocess.TimeoutExpired:
            self.p.kill()
            return None

    def bad_stdout_lines(self) -> int:
        n = 0
        for l in self.lines:
            try:
                json.loads(l.decode("utf-8"))
            except ValueError:
                n += 1
        return n


def mk_ws(name: str) -> Path:
    ws = C.WORK / name
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    return ws


def procs_with(marker: str) -> list[int]:
    """명령줄에 marker 가 든 python 프로세스 PID 목록(서버가 venv 실행기 + 실제 인터프리터 2개일 수 있어 명령줄로 센다)."""
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*" + marker +
          "*' } | ForEach-Object { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True).stdout.decode("utf-8", "replace")
    return [int(x) for x in out.split() if x.isdigit()]


# ------------------------------------------------------------------ 시험들
def t_startup(n: int = 10) -> dict:
    ws = mk_ws("win-startup")
    C.build_fixture(ws)
    init, lst, first, second = [], [], [], []
    for _ in range(n):
        m = RawMcp(ws)
        init.append(m.handshake())
        t = time.perf_counter()
        m.call("tools/list", {})
        lst.append(time.perf_counter() - t)
        t = time.perf_counter()
        m.tool("dobim_query", {"command": "project.info", "project": "p.dobim"})
        first.append(time.perf_counter() - t)
        t = time.perf_counter()
        m.tool("dobim_query", {"command": "project.info", "project": "p.dobim"})
        second.append(time.perf_counter() - t)
        m.close()
    f = lambda xs: {"min": round(min(xs), 3), "median": round(statistics.median(xs), 3), "max": round(max(xs), 3)}
    return {"runs": n, "spawn_to_initialize_sec": f(init), "tools_list_sec": f(lst),
            "first_project_call_sec": f(first), "second_project_call_sec": f(second),
            "note": "spawn 부터 initialize 응답까지(콜드 import 포함). 첫 프로젝트 호출은 ifcopenshell 지연 import 를 포함한다."}


def t_stdout_purity() -> dict:
    ws = mk_ws("win-purity")
    C.build_fixture(ws)
    m = RawMcp(ws)
    m.handshake()
    for cmd, a in [("model.validate", {}), ("model.stats", {}), ("schedule.rows", {"kind": "door"})]:
        m.env_of("dobim_query", {"command": cmd, "args": a, "project": "p.dobim"})
    r1 = m.env_of("dobim_execute_destructive", {"command": "ifc.export", "args": {"path": "o.ifc"}, "project": "p.dobim"})
    m.env_of("dobim_execute_destructive", {"command": "ifc.export", "args": {"path": "o.ifc"}, "project": "p.dobim",
                                           "confirm": r1["error"]["details"]["confirm_token"]})
    out = {"normal_calls_nonjson_stdout_lines": m.bad_stdout_lines(), "stderr_bytes": len(b"".join(m.err))}
    m.close()
    # 주입 시험: fd 1 에 C 수준으로 쓰는 라이브러리를 흉내 낸다(os.write(1,..) 는 printf 와 같은 fd 로 간다)
    code = ("import os,sys,dataclasses\n"
            f"sys.path.insert(0,{str(C.SRC)!r})\n"
            "from dobim_core import registry, mcp_server\n"
            "cmds=registry.load()\n"
            "c=cmds['project.info']\n"
            "orig=c.func\n"
            "def noisy(ctx,a):\n"
            "    os.write(1,b'OCCT: stray printf from native library\\n')\n"
            "    return orig(ctx,a)\n"
            "registry.REGISTRY['project.info']=dataclasses.replace(c,func=noisy)\n"
            "sys.exit(mcp_server.serve(sys.argv[1],'mcp',False))\n")
    drv = C.WORK / "win-purity" / "_noisy_server.py"
    drv.write_text(code, encoding="utf-8")
    m2 = RawMcp(ws, argv=[str(C.PY), str(drv), str(ws)])
    m2.handshake()
    m2.tool("dobim_query", {"command": "project.info", "project": "p.dobim"})
    out["injected_stray_write_nonjson_stdout_lines"] = m2.bad_stdout_lines()
    time.sleep(0.3)
    out["injected_line_went_to_stderr"] = b"stray printf" in b"".join(m2.err)
    out["injected_note"] = ("커맨드 안에서 fd 1 로 직접 쓴 줄이 프로토콜 스트림에 그대로 섞이는지 본다. "
                            "섞이지 않고 stderr 로 가면 SDK 가 fd 1 을 보호하는 것이다(코어 코드가 아니라 mcp SDK 동작).")
    m2.close()
    return out


def t_encoding() -> dict:
    ws = mk_ws("win-enc")
    C.build_fixture(ws)
    names = {"level": "지상 1층 ✓", "door": "출입문 «정문»"}
    res = {}
    for label, env in (("PYTHONUTF8=1", {**os.environ, **C.server_env()}),
                       ("PYTHONUTF8 없음", {k: v for k, v in {**os.environ, "PYTHONPATH": str(C.SRC)}.items()
                                          if k not in ("PYTHONUTF8", "PYTHONIOENCODING")})):
        m = RawMcp(ws, env=env)
        m.handshake()
        lv = F._id("3", 50 + (label == "PYTHONUTF8 없음"))
        nm = names["level"] + ("" if label.startswith("PYTHONUTF8=") else " (B)")
        e = m.env_of("dobim_execute", {"command": "level.create", "args": {"id": lv, "name": nm, "elevation": 9900},
                                       "project": "p.dobim"})
        g = m.env_of("dobim_query", {"command": "element.get", "args": {"id": lv}, "project": "p.dobim"})
        raw = b"".join(m.lines)
        res[label] = {"create_ok": e.get("ok"), "create_error": None if e.get("ok") else {
            "code": (e.get("error") or {}).get("code"), "message": ((e.get("error") or {}).get("message") or "")[:160]},
                      "name_roundtrip": g.get("ok") and nm in json.dumps(g, ensure_ascii=False),
                      "stdout_is_valid_utf8": _utf8_ok(raw), "nonjson_lines": m.bad_stdout_lines(),
                      "ascii_escaped_in_stdout": b"\\ud" in raw or b"\\uc" in raw}
        m.close()
    # CLI 쪽 cp949 콘솔 영향: 파이프로 받는 stdout 바이트가 UTF-8 인가
    e, code = C.cli(ws, "element.query", {"kind": "level"}, project="p.dobim")
    res["cli_roundtrip_ok"] = e["ok"] and names["level"] in json.dumps(e, ensure_ascii=False)
    return res


def _utf8_ok(b: bytes) -> bool:
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def t_bom() -> dict:
    ws = mk_ws("win-bom")
    C.build_fixture(ws)
    a = ws / "args-bom.json"
    a.write_bytes(b"\xef\xbb\xbf" + json.dumps({"id": F.W1}).encode("utf-8"))
    s = ws / "script-bom.jsonl"
    s.write_bytes(b"\xef\xbb\xbf" + (json.dumps({"command": "model.stats", "args": {}}) + "\n").encode("utf-8"))
    cmd = [str(C.PY), "-m", "dobim_core", "--workspace", str(ws), "run", "element.get", "-p", "p.dobim", "--json", "@" + str(a)]
    p = subprocess.run(cmd, capture_output=True, env={**os.environ, **C.server_env()}, stdin=subprocess.DEVNULL)
    ok1 = json.loads(p.stdout.decode("utf-8")).get("ok")
    cmd = [str(C.PY), "-m", "dobim_core", "--workspace", str(ws), "script", str(s), "-p", "p.dobim"]
    p = subprocess.run(cmd, capture_output=True, env={**os.environ, **C.server_env()}, stdin=subprocess.DEVNULL)
    ok2 = json.loads(p.stdout.decode("utf-8")).get("ok")
    return {"cli_json_file_with_bom_ok": ok1, "cli_script_file_with_bom_ok": ok2}


def t_paths() -> dict:
    out = {}
    base = C.WORK / "win-path"
    shutil.rmtree(base, ignore_errors=True)
    ws = base / "한글 공백 폴더" / "프로젝트 시험 (사본)"
    ws.mkdir(parents=True)
    C.build_fixture(ws, "건물 모델.dobim")
    m = RawMcp(ws)
    m.handshake()
    r = m.env_of("dobim_query", {"command": "project.info", "project": "건물 모델.dobim"})
    e = m.env_of("dobim_execute", {"command": "wall.move", "args": {"id": F.W1, "dx": 100}, "project": "건물 모델.dobim"})
    x = m.env_of("dobim_execute_destructive", {"command": "ifc.export", "args": {"path": "출력 폴더/내 보내기.ifc"},
                                               "project": "건물 모델.dobim"})
    (ws / "출력 폴더").mkdir()
    x = m.env_of("dobim_execute_destructive", {"command": "ifc.export", "args": {"path": "출력 폴더/내 보내기.ifc"},
                                               "project": "건물 모델.dobim"})
    x2 = m.env_of("dobim_execute_destructive", {"command": "ifc.export", "args": {"path": "출력 폴더/내 보내기.ifc"},
                                                "project": "건물 모델.dobim", "confirm": x["error"]["details"]["confirm_token"]})
    out["korean_space_path"] = {"project_info_ok": r["ok"], "mutation_ok": e["ok"], "export_ok": x2["ok"],
                                "exported_file_exists": (ws / "출력 폴더" / "내 보내기.ifc").exists()}
    m.close()
    # 긴 경로: 전체 경로 길이를 늘려 가며 어디서 깨지는지 본다
    lengths = {}
    for target in (200, 259, 270, 300):
        d = C.WORK / "win-path" / "long"
        shutil.rmtree("\\\\?\\" + str(d), ignore_errors=True) if sys.platform == "win32" else None
        cur = d
        seg = "가" * 30
        try:
            while len(str(cur / "p.dobim")) < target:
                cur = cur / seg
            os.makedirs("\\\\?\\" + str(cur) if sys.platform == "win32" else cur, exist_ok=True)
            n = len(str(cur / "p.dobim"))
            m = RawMcp(cur)
            m.handshake()
            r = m.env_of("dobim_project", {"action": "create", "path": "p.dobim"})
            ok = r.get("ok")
            err = None if ok else r.get("error", {}).get("code")
            m.close()
            lengths[str(target)] = {"path_len": n, "project_create_ok": ok, "error_code": err}
        except Exception as ex:   # 서버가 죽거나 응답이 없는 경우도 결과다
            lengths[str(target)] = {"exception": f"{type(ex).__name__}: {str(ex)[:120]}"}
    out["long_paths"] = lengths
    return out


def t_env_systemroot() -> dict:
    ws = mk_ws("win-env")
    C.build_fixture(ws)
    res = {}
    for label, env in (("PYTHONPATH+PYTHONUTF8 만(SystemRoot 없음)", C.server_env()),
                       ("위 + SystemRoot", {**C.server_env(), "SystemRoot": os.environ["SystemRoot"]})):
        try:
            m = RawMcp(ws, env=env)
            m.handshake()
            r = m.env_of("dobim_query", {"command": "project.info", "project": "p.dobim"})
            res[label] = {"starts": True, "project_info_ok": r.get("ok")}
            m.close()
        except Exception as ex:
            res[label] = {"starts": False, "error": f"{type(ex).__name__}: {str(ex)[:160]}"}
    return res


def t_orphans() -> dict:
    ws = mk_ws("win-orphan")
    C.build_fixture(ws)
    marker = "win-orphan"
    out = {}
    m = RawMcp(ws)
    m.handshake()
    m.tool("dobim_query", {"command": "project.info", "project": "p.dobim"})
    out["server_processes_while_running"] = len(procs_with(marker))
    out["stdin_eof_exit_sec"] = m.close()
    out["exit_code_after_eof"] = m.p.returncode
    time.sleep(0.5)
    out["server_processes_after_eof"] = len(procs_with(marker))
    # 부모(중계기)가 강제 종료될 때 자식 서버가 남는가
    log = C.WORK / "win-orphan" / "tap.jsonl"
    argv = C.server_argv(ws, log)
    tap = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           env={**os.environ, **C.server_env()}, creationflags=0x08000000)
    tap.stdin.write((json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": PROTO, "capabilities": {}, "clientInfo": {"name": "s11", "version": "0"}}}) + "\n").encode())
    tap.stdin.flush()
    tap.stdout.readline()
    time.sleep(0.5)
    out["server_processes_before_parent_kill"] = len(procs_with(marker))
    subprocess.run(["taskkill", "/F", "/PID", str(tap.pid)], capture_output=True)
    time.sleep(3)
    left = procs_with(marker)
    out["server_processes_3s_after_parent_killed"] = len(left)
    for pid in left:   # 시험이 남긴 것만 정리한다(marker 가 이 시험 전용 폴더 이름이다)
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    out["note"] = ("서버는 자식 프로세스를 만들지 않는다. venv 의 python.exe 는 실행기(런처)와 실제 인터프리터 2개로 보일 수 있다. "
                   "부모 강제 종료 뒤 0 이면 stdin EOF 로 정리된 것이다.")
    return out


def _lock_case(share: int, label: str) -> dict:
    ws = mk_ws("win-lock-" + label)
    C.build_fixture(ws)
    target = ws / "locked.ifc"
    m = RawMcp(ws)
    m.handshake()
    a = {"command": "ifc.export", "args": {"path": "locked.ifc"}, "project": "p.dobim"}

    def export():
        t = m.env_of("dobim_execute_destructive", a)
        return m.env_of("dobim_execute_destructive", {**a, "confirm": t["error"]["details"]["confirm_token"]})
    export()
    before = target.read_bytes()
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_void_p
    h = k32.CreateFileW(str(target), 0x80000000, share, None, 3, 0x80, None)   # 읽기 열기, share: 0=공유 없음 1=읽기 공유
    res = {"share_mode": share, "opened": h not in (None, ctypes.c_void_p(-1).value)}
    try:
        r = export()
        res["export_while_locked"] = {"ok": r.get("ok"), "error_code": (r.get("error") or {}).get("code"),
                                      "sdk_level_error": bool(r.get("sdk_level_error"))}
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))
    res["original_unchanged"] = target.read_bytes() == before
    res["leftover_tmp_files"] = sorted(p.name for p in ws.glob("*.tmp"))
    res["export_after_unlock_ok"] = export().get("ok")
    m.close()
    return res


def t_file_lock() -> dict:
    return {"viewer_like_share_read": _lock_case(1, "read"), "exclusive_no_share": _lock_case(0, "excl")}


def main(out_path: str) -> int:
    res = {}
    for name, fn in (("startup", t_startup), ("stdout_purity", t_stdout_purity), ("encoding", t_encoding), ("bom", t_bom),
                     ("paths", t_paths), ("env_systemroot", t_env_systemroot), ("orphans", t_orphans),
                     ("file_lock", t_file_lock)):
        try:
            res[name] = fn()
        except Exception as ex:
            res[name] = {"exception": f"{type(ex).__name__}: {str(ex)[:300]}"}
        print(name, json.dumps(res[name], ensure_ascii=False)[:400], flush=True)
    Path(out_path).write_text(json.dumps(C.scrub(res), ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
