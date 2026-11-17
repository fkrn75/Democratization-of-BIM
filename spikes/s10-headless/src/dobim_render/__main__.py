# SPDX-License-Identifier: MPL-2.0
"""python -m dobim_render in.ifc out.png [--width 1280] [--view iso] [--only mep] [--hide ...] [--ghost] [--tie-eps 1e-6]
— stdout 은 JSON 한 줄(렌더 정보)."""
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
    # 21단계: 범주 필터(쉼표 목록: IFC 클래스 또는 묶음 structure|mep|arch, 상위 클래스도 맞음)와 고스트, 같은 평면 겹침 잡음 억제
    ap.add_argument("--only", default="", help="보일 범주: 예) structure, mep, IfcBeam,IfcColumn")
    ap.add_argument("--hide", default="", help="숨길 범주(같은 형식)")
    ap.add_argument("--ghost", action="store_true", help="숨긴 요소를 반투명 회색으로 겹쳐 그린다")
    ap.add_argument("--tie-eps", type=float, default=0.0, help="깊이 차이가 이 값(m) 이하면 먼저 그린 면을 둔다(권장 1e-6)")
    a = ap.parse_args(argv)
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]   # noqa: E731
    r = render_ifc(a.ifc, a.width, a.view, a.threads, only=split(a.only), hide=split(a.hide), ghost=a.ghost, tie_eps=a.tie_eps)
    with open(a.out, "wb") as fh:
        fh.write(r.png_bytes)
    sys.stdout.buffer.write((json.dumps(r.info, sort_keys=True) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
