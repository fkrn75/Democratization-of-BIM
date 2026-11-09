import json, sys
sys.path.insert(0, "phase2")
from ccad_drive import run
rc, res, so, se = run([("view_create", {"kind": "sheet"}), ("views_list", {})], project="native.ccad", save_as="sheetprobe.ccad", label="sp")
print(rc, se[:300]); sid = res[0]["result"]; print(sid)
vid = sid["id"] if isinstance(sid, dict) else sid
rc, res, so, se = run([("view_get", {"view": vid})], project="sheetprobe.ccad", label="sg")
print(json.dumps(res[0]["result"], ensure_ascii=False)[:1500])
