"""FreeCAD TechDraw 헤드리스 탐침(E4/E5 관찰): 벽 평면 뷰를 만들어 SVG/DXF 로 쓰고, 벽 이동 후 뷰가 자동 갱신되는지 본다.
환경변수: S0_OUTDIR, S0_LOG"""
import json
import os
import re
import shutil
import time

import Arch
import Draft
import FreeCAD
import TechDraw
from FreeCAD import Vector

out, rec = os.environ["S0_OUTDIR"], {}
doc = FreeCAD.newDocument("td")
line = Draft.makeLine(Vector(0, 0, 0), Vector(6000, 0, 0))
wall = Arch.makeWall(line, width=300, height=3300, align="Center")
doc.recompute()
fc = FreeCAD.getHomePath()
tmpl = None
for root, _, files in os.walk(os.path.join(fc, "data", "Mod", "TechDraw", "Templates", "ISO")):
    for f in files:
        if f == "A1_Landscape_blank.svg" or (f.startswith("A1_") and tmpl is None):
            tmpl = os.path.join(root, f)
rec["template"] = tmpl
page = doc.addObject("TechDraw::DrawPage", "Page")
t = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
t.Template = tmpl
page.Template = t
view = doc.addObject("TechDraw::DrawViewPart", "View")
page.addView(view)
view.Source = [wall]
view.Direction = Vector(0, 0, 1)   # 평면
view.Scale = 0.01
t0 = time.perf_counter()
doc.recompute()
rec["recompute_sec"] = round(time.perf_counter() - t0, 2)
def dump(tag):
    path = os.path.join(out, f"td_{tag}.dxf")
    TechDraw.writeDXFPage(page, path)
    txt = open(path, encoding="utf-8", errors="replace").read()
    return txt, len(re.findall(r"(?m)^(?:LINE|LWPOLYLINE|POLYLINE)\s*$", txt))

try:
    d0, n0 = dump("before")
    rec["dxf_before_bytes"], rec["dxf_before_entities"] = len(d0), n0
except Exception as ex:
    rec["dxf_error"] = repr(ex)[:300]
    d0 = None
rec["has_PageResult_in_console"] = hasattr(page, "PageResult")
rec["edges_before"] = len(view.getVisibleEdges()) if hasattr(view, "getVisibleEdges") else None
line.End = Vector(8000, 0, 0)   # 이동은 뷰가 bbox 중심으로 정규화해 변화가 안 보이므로 형상을 바꾼다 (벽 길이 6m->8m)
t0 = time.perf_counter()
doc.recompute()
rec["E4_recompute_after_move_sec"] = round(time.perf_counter() - t0, 2)
if d0 is not None:
    d1, n1 = dump("after")
    rec["dxf_after_entities"] = n1
    rec["E4_dxf_changed_after_wall_move"] = d1 != d0
json.dump(rec, open(os.environ["S0_LOG"], "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("DONE", rec)
