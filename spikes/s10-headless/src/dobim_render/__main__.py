# SPDX-License-Identifier: MPL-2.0
"""python -m dobim_render in.ifc out.png [--width 1280] [--view iso]  — stdout 은 JSON 한 줄(렌더 정보)."""
from __future__ import annotations

import argparse
import json
import sys

from dobim_render.scene import render_ifc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dobim_render")
    ap.add_argument("ifc")
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--view", default="iso")
    ap.add_argument("--threads", type=int, default=1)
    a = ap.parse_args(argv)
    r = render_ifc(a.ifc, a.width, a.view, a.threads)
    with open(a.out, "wb") as fh:
        fh.write(r.png_bytes)
    sys.stdout.buffer.write((json.dumps(r.info, sort_keys=True) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
