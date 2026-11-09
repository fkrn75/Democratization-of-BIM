# SPDX-License-Identifier: MPL-2.0
"""동결본 MCP stdio 서버 진입점 (dobim-mcp.exe). `dobim.exe mcp serve` 와 같은 서버를 인자 없이 띄운다.

--workspace / --actor / --allow-code 는 그대로 넘길 수 있다(전역 옵션이라 mcp serve 앞에 둔다).
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from dobim_core.cli import main
    glob, rest = [], list(sys.argv[1:])
    # 전역 옵션(--workspace X, --actor X)을 앞으로, 나머지(--allow-code)는 mcp serve 뒤로
    while rest:
        a = rest.pop(0)
        if a in ("--workspace", "--actor") and rest:
            glob += [a, rest.pop(0)]
        elif a.startswith(("--workspace=", "--actor=")):
            glob.append(a)
        else:
            tail = [a] + rest
            rest = []
            break
    else:
        tail = []
    sys.exit(main(glob + ["mcp", "serve"] + tail))
