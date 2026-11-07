import sys
sys.path.insert(0, "phase2")
from ccad_drive import run
rc, res, so, se = run([("elements_move", {"ids": [101], "delta": [0, 0.6]}), ("ifc_export", {"path": "/out/cli_e1.ifc"})], project="native.ccad", label="cli-e1-cmp")
print(rc)
