"""FreeCAD(FreeCADCmd) 헤드리스 저작 탐침: Arch API 로 벽+문+창+층을 만들고 IFC 로 내보낸 뒤 E1/E3 유사 편집을 관찰한다.
IFC 가져오기가 번들 ifcopenshell 불일치로 실패하므로 (freecad_roundtrip.py 참고) RA-01 을 열 수 없어 대체한 시험이다.
환경변수: S0_OUTDIR (출력 폴더), S0_LOG (JSON)"""
import json
import os
import time

import Arch
import Draft
import FreeCAD
from FreeCAD import Placement, Vector
from importers import exportIFC

outdir = os.environ["S0_OUTDIR"]
rec = {}
doc = FreeCAD.newDocument("probe")
# 벽: Draft 선으로부터 (길이 6m, 두께 0.3, 높이 3.3)
line = Draft.makeLine(Vector(0, 0, 0), Vector(6000, 0, 0))
doc.recompute()
wall = Arch.makeWall(line, width=300, height=3300, align="Center")
wall.Label = "W-E-01"
doc.recompute()
win = Arch.makeWindowPreset("Open 1-pane", width=1200, height=1200, h1=50, h2=50, h3=50, w1=100, w2=50, o1=0, o2=50,
                            placement=Placement(Vector(3000, 0, 900), FreeCAD.Rotation(Vector(1, 0, 0), 90)))
win.Label = "WIN-01"
win.Hosts = [wall]
doc.recompute()
level = Arch.makeFloor([wall, win])
level.Label = "1F"
level.Height = 3300
doc.recompute()
rec["wall_volume_with_window"] = round(wall.Shape.Volume / 1e9, 6)
rec["wall_volume_expected_no_window"] = round(6000 * 300 * 3300 / 1e9, 6)
t = time.perf_counter()
exportIFC.export([level, wall, win], os.path.join(outdir, "probe0.ifc"))
rec["export_sec"] = round(time.perf_counter() - t, 2)

# E1: 벽을 +Y 600mm 이동. 창이 따라오는가?
w0, win0 = wall.Shape.BoundBox.Center, win.Shape.BoundBox.Center
wall.Placement.Base = wall.Placement.Base + Vector(0, 600, 0)
doc.recompute()
rec["E1_wall_moved_mm"] = round((wall.Shape.BoundBox.Center - w0).y, 1)
rec["E1_window_moved_mm"] = round((win.Shape.BoundBox.Center - win0).y, 1)
rec["E1_wall_volume_after"] = round(wall.Shape.Volume / 1e9, 6)
# E3: 층고 3300 -> 3600. 벽 높이가 따라오는가?
level.Height = 3600
doc.recompute()
rec["E3_wall_height_after_level_change_mm"] = round(wall.Shape.BoundBox.ZLength, 1)
exportIFC.export([level, wall, win], os.path.join(outdir, "probe1.ifc"))
json.dump(rec, open(os.environ["S0_LOG"], "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("DONE", rec)
