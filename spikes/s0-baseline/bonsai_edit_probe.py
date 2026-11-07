"""Bonsai(--background) 편집·도면 탐침: E1(오브젝트 이동->저장 시 IFC 반영), 도면 생성(E4), 시트/PDF(E5) 시도를 기록한다.
공개 오퍼레이터만 호출한다 (소스 미열람). 환경변수: S0_IN, S0_OUTDIR, S0_LOG"""
import json
import os
import time
import traceback

import bpy

out = os.environ["S0_OUTDIR"]
rec = {"steps": []}


def step(name, fn):
    t = time.perf_counter()
    try:
        r = fn()
        rec["steps"].append({"step": name, "ok": True, "sec": round(time.perf_counter() - t, 2), "ret": str(r)[:160]})
        return r
    except Exception as ex:
        rec["steps"].append({"step": name, "ok": False, "sec": round(time.perf_counter() - t, 2),
                             "err": repr(ex)[:300], "tb": traceback.format_exc()[-500:]})


def log():
    json.dump(rec, open(os.environ["S0_LOG"], "w", encoding="utf-8"), indent=1, ensure_ascii=False)


step("enable_addon", lambda: bpy.ops.preferences.addon_enable(module="bl_ext.user_default.bonsai"))
step("load_project", lambda: bpy.ops.bim.load_project(filepath=os.environ["S0_IN"], should_start_fresh_session=True))
objs = {o.name: o for o in bpy.data.objects}
wall = next((o for n, o in objs.items() if n.endswith("/W-E-01")), None)
rec["wall_object"] = wall.name if wall else None

# E1: 벽 오브젝트를 +Y 0.6m 이동하고 저장 -> IFC 에 반영되는가, 문/창은 따라가는가
if wall:
    wall.location.y += 0.6
    bpy.context.view_layer.update()
step("save_E1", lambda: bpy.ops.bim.save_project(filepath=os.path.join(out, "E1.bonsai.ifc"), should_save_as=True))

# 도면 생성 시도 (평면)
step("add_drawing", lambda: bpy.ops.bim.add_drawing())
import bonsai.tool as tool
ifc = tool.Ifc.get()
drs = [a for a in ifc.by_type("IfcAnnotation") if a.ObjectType == "DRAWING"]
rec["drawings_in_ifc"] = [d.Name for d in drs]
if drs:
    step("activate_drawing", lambda: bpy.ops.bim.activate_drawing(drawing=drs[0].id()))
step("create_drawing", lambda: bpy.ops.bim.create_drawing())
rec["drawing_files"] = sorted(str(p) for p in __import__("pathlib").Path(out).rglob("*.svg"))
log()
# 시트
step("add_sheet", lambda: bpy.ops.bim.add_sheet())
step("create_sheets", lambda: bpy.ops.bim.create_sheets())
step("save_after_drawing", lambda: bpy.ops.bim.save_project(filepath=os.path.join(out, "after_drawing.bonsai.ifc"), should_save_as=True))
rec["out_files"] = sorted(os.listdir(out))
log()
print("S0DONE", [(s["step"], s["ok"]) for s in rec["steps"]])
