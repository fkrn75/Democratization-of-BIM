import sys
sys.path.insert(0, "phase2")
from ccad_drive import run
rc, res, so, se = run([("views_list", {})], project="native.ccad", label="vl")
print([(v["category"], v["name"], v["id"]) for v in res[0]["result"]])
