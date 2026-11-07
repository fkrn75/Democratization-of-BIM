import json, sys
sys.path.insert(0, "phase2")
from ccad_drive import run
rc, res, so, se = run([("project_new", {}), ("types_list", {"category": "window"}), ("types_list", {"category": "door"}), ("materials_list", {})], label="p")
print([t["name"] for t in res[1]["result"]], [t["name"] for t in res[2]["result"]])
print([m if isinstance(m, str) else m.get("name") for m in res[3]["result"]])
rc, res, so, se = run([("project_new", {}), ("level_create", {"name": "RF", "elevation": 6.6}), ("level_set", {"level": "Level 1", "name": "2F", "elevation": 3.3}), ("level_set", {"level": "Ground Floor", "name": "1F"}), ("material_create", {"name": "Insulation"}), ("type_duplicate", {"type": "Masonry 25 cm", "name": "WT-EXT-320"}), ("levels_list", {})], label="p2")
print(rc, se[:300]); print([json.dumps(r["result"])[:120] for r in res])
