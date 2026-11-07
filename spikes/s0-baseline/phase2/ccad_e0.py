import json, sys
sys.path.insert(0, "phase2")
from ccad_drive import run
rc, res, so, se = run([("ifc_import", {"path": "/in/RA-01.ifc"}), ("project_save", {"path": "/out/ra01.ccad"})], label="import+save")
print(rc, se[:200])
rc, res, so, se = run([("elements_list", {}), ("levels_list", {}), ("views_list", {}), ("types_list", {})], project="ra01.ccad", label="inventory")
print(rc, se[:300])
for r in res:
    v = r["result"]
    print(r["command"], type(v).__name__, len(v) if hasattr(v, "__len__") else "")
json.dump(res, open("out/p2/ccad/inventory.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
