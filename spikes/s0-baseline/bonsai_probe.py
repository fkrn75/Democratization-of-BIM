"""Bonsai(Blender 4.5 --background) 탐침: 확장 활성화 -> RA-01 열기 -> IFC 저장. 각 단계의 성공/예외를 JSON 으로 남긴다.
실행: blender -b --factory-startup --python bonsai_probe.py   (BLENDER_USER_RESOURCES 는 격리 폴더, S0_IN/S0_OUT/S0_LOG 환경변수)
Bonsai 소스는 읽지 않고 공개 오퍼레이터 이름만 호출한다 (ADR-0003)."""
import json
import os
import time
import traceback

import bpy

rec = {"steps": [], "blender": bpy.app.version_string, "background": bpy.app.background}


def step(name, fn):
    t = time.perf_counter()
    try:
        r = fn()
        rec["steps"].append({"step": name, "ok": True, "sec": round(time.perf_counter() - t, 2), "ret": str(r)[:200]})
        return r
    except Exception as ex:
        rec["steps"].append({"step": name, "ok": False, "sec": round(time.perf_counter() - t, 2),
                             "err": repr(ex)[:400], "tb": traceback.format_exc()[-600:]})


step("enable_addon", lambda: bpy.ops.preferences.addon_enable(module="bl_ext.user_default.bonsai"))
rec["addon_enabled"] = "bl_ext.user_default.bonsai" in bpy.context.preferences.addons
step("import_bonsai_tool", lambda: __import__("bonsai.tool"))
src, out = os.environ["S0_IN"], os.environ["S0_OUT"]
step("load_project", lambda: bpy.ops.bim.load_project(filepath=src, should_start_fresh_session=True))
rec["n_objects_after_load"] = len(bpy.data.objects)
rec["n_meshes_with_verts"] = sum(1 for o in bpy.data.objects if o.type == "MESH" and len(o.data.vertices))
step("save_project", lambda: bpy.ops.bim.save_project(filepath=out, should_save_as=True))
rec["out_exists"] = os.path.exists(out)
json.dump(rec, open(os.environ["S0_LOG"], "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("S0DONE", json.dumps(rec, ensure_ascii=False)[:1500])
