# SPDX-License-Identifier: MPL-2.0
"""stdio MCP 서버 앞에 끼우는 투명 중계기(측정 전용).

클라이언트(Claude·Codex)가 서버를 직접 띄우는 대신 이 중계기를 띄우게 하고, 중계기가 진짜 서버를 자식으로 띄운다.
오가는 JSON-RPC 줄을 그대로 전달하면서 시각과 함께 기록한다. 코어는 전혀 고치지 않는다.
기록으로 알 수 있는 것: 협상된 프로토콜 버전, 클라이언트 이름·버전·기능, 서버 시작 시간, 호출별 응답 시간·크기,
stdout 오염(JSON 이 아닌 줄), 클라이언트가 stdin 을 닫았을 때 서버가 종료하는지.

사용: python stdio_tap.py --log <기록.jsonl> -- <서버 명령...>
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time


def main() -> int:
    argv = sys.argv[1:]
    log_path = None
    if "--log" in argv:
        i = argv.index("--log")
        log_path = argv[i + 1]
        del argv[i:i + 2]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        sys.stderr.write("서버 명령이 없다\n")
        return 2

    t0 = time.perf_counter()
    lock = threading.Lock()
    log = open(log_path, "a", encoding="utf-8", newline="\n") if log_path else None

    def rec(kind: str, **kw) -> None:
        if log is None:
            return
        row = {"t": round(time.perf_counter() - t0, 4), "k": kind, **kw}
        with lock:
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()

    flags = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=flags)
    rec("spawn", pid=child.pid, tap_pid=__import__("os").getpid())
    out = sys.stdout.buffer

    def pump_in() -> None:
        try:
            for line in iter(sys.stdin.buffer.readline, b""):
                rec("c2s", raw=line.decode("utf-8", "replace").rstrip("\n"))
                child.stdin.write(line)
                child.stdin.flush()
        except (OSError, ValueError):
            pass
        rec("client_stdin_eof")
        try:
            child.stdin.close()
        except OSError:
            pass

    def pump_out() -> None:
        first = True
        for line in iter(child.stdout.readline, b""):
            if first:
                rec("first_stdout")
                first = False
            text = line.decode("utf-8", "replace").rstrip("\n")
            try:
                json.loads(text)
                valid = True
            except ValueError:
                valid = False
            rec("s2c", raw=text, json=valid, nbytes=len(line))
            out.write(line)
            out.flush()

    def pump_err() -> None:
        for line in iter(child.stderr.readline, b""):
            rec("stderr", raw=line.decode("utf-8", "replace").rstrip("\n")[:2000])

    threads = [threading.Thread(target=f, daemon=True) for f in (pump_in, pump_out, pump_err)]
    for t in threads:
        t.start()
    code = child.wait()
    rec("child_exit", code=code)
    for t in threads[1:]:
        t.join(timeout=2)
    if log:
        log.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
