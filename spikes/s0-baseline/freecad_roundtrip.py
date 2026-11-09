"""FreeCAD 1.1.4 (FreeCADCmd) 열기 -> IFC 내보내기 왕복. FreeCADCmd 안에서 실행한다 (run_freecad.ps1 참고).
환경변수: S0_IN (입력 IFC), S0_OUT (출력 IFC), S0_LOG (JSON 요약)"""
import collections
import json
import os
import time

import FreeCAD
from importers import exportIFC, importIFC

src, out, logp = os.environ["S0_IN"], os.environ["S0_OUT"], os.environ["S0_LOG"]
rec = {"input": src}
t = time.perf_counter()
doc = importIFC.open(src)
rec["import_sec"] = round(time.perf_counter() - t, 2)
objs = doc.Objects
rec["n_objects"] = len(objs)
rec["by_type"] = dict(collections.Counter(o.TypeId for o in objs))
rec["by_ifctype"] = dict(collections.Counter(getattr(o, "IfcType", "-") for o in objs))
rec["with_shape"] = sum(1 for o in objs if getattr(o, "Shape", None) is not None and not o.Shape.isNull())
t = time.perf_counter()
bim = [o for o in objs if getattr(o, "IfcType", None) not in (None, "")]
try:
    exportIFC.export(bim, out)
    rec["export_ok"] = True
except Exception as ex:
    rec["export_ok"] = False
    rec["export_error"] = repr(ex)[:400]
rec["export_sec"] = round(time.perf_counter() - t, 2)
rec["n_exported_objects"] = len(bim)
doc.saveAs(out.replace(".ifc", ".FCStd"))
json.dump(rec, open(logp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("DONE", json.dumps(rec, ensure_ascii=False)[:800])
