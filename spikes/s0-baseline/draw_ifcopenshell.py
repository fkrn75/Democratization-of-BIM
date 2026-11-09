"""ifcopenshell.draw 로 평면/단면/입면 SVG 를 만든다 (E4/E5 관찰용).

`ifcquery plot` 은 0.9.0.post1 에서 cells=True 경로가 AttributeError 로 죽어서 (RA-01·외래 표본 모두 재현),
라이브러리를 직접 호출하고 cells=False 로 우회한다. 사용: python draw_ifcopenshell.py IN.ifc OUTDIR PREFIX
"""
import re
import sys
import time
from pathlib import Path

import ifcopenshell
import ifcopenshell.draw as d

src, outdir, prefix = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
outdir.mkdir(parents=True, exist_ok=True)
f = ifcopenshell.open(str(src))
for view in ("floorplan", "section", "elevation"):
    s = d.draw_settings(auto_floorplan=view == "floorplan", auto_section=view == "section",
                        auto_elevation=view == "elevation", width=420, height=297, scale=1 / 100,
                        cells=False, include_projection=True, css="")
    t = time.perf_counter()
    try:
        svg = d.main(s, files=[f])
    except Exception as ex:
        print(prefix, view, "실패", repr(ex)[:200])
        continue
    dt = time.perf_counter() - t
    out = outdir / f"{prefix}_{view}.svg"
    out.write_bytes(svg)
    txt = svg.decode("utf-8")
    print(prefix, view, f"{dt:.2f}s", f"{len(svg)}B", "path", len(re.findall(r"<path", txt)), "text", len(re.findall(r"<text", txt)))
