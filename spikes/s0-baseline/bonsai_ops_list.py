"""Bonsai 가 등록한 bpy.ops.bim.* 오퍼레이터 이름 나열 + --background 에서 poll() 통과 비율(프로젝트 로드 후, 선택 없음 상태).
공개 API 인트로스펙션만 사용한다 (소스 미열람)."""
import json
import os

import bpy

bpy.ops.preferences.addon_enable(module="bl_ext.user_default.bonsai")
bpy.ops.bim.load_project(filepath=os.environ["S0_IN"], should_start_fresh_session=True)
names = sorted(n for n in dir(bpy.ops.bim) if not n.startswith("_"))
res = {"total": len(names), "poll_true": 0, "poll_false": 0, "poll_error": 0, "poll_true_names": []}
for n in names:
    try:
        ok = getattr(bpy.ops.bim, n).poll()
    except Exception:
        res["poll_error"] += 1
        continue
    if ok:
        res["poll_true"] += 1
        res["poll_true_names"].append(n)
    else:
        res["poll_false"] += 1
res["keyword_hits"] = {k: [n for n in names if k in n] for k in ("door", "window", "type", "storey", "drawing", "sheet", "schedule", "clash", "move", "resize", "param")}
res["all_names"] = names
json.dump(res, open(os.environ["S0_LOG"], "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("S0DONE", res["total"], res["poll_true"], res["poll_false"], res["poll_error"])
