import json, sys
sys.path.insert(0, "phase2")
from ccad_drive import run
for t in ("Masonry 25 cm", "Slab 12 cm", "Column 20 x 20 cm", "Beam 15 x 40 cm", "Door 90x210"):
    rc, res, so, se = run([("project_new", {}), ("type_get", {"type": t})], label="tg")
    print(t, json.dumps(res[1]["result"]["params"], ensure_ascii=False)[:700] if res and len(res) > 1 else (so[:300], se[:300]))
