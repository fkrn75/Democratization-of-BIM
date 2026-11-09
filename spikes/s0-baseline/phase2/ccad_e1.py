import json, sys
sys.path.insert(0, "phase2")
from ccad_drive import run
rc, res, so, se = run([("element_get", {"id": 92}), ("type_get", {"type": "DT-01"}), ("element_get", {"id": 108})], project="ra01.ccad", label="inspect")
print(rc, se[:300])
for r in res: print(json.dumps(r["result"], ensure_ascii=False)[:1500]); print()
