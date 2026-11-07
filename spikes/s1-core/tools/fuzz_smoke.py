import sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
d = Path(tempfile.mkdtemp())
t = time.time()
r = kit.property_run(seed, n, d, checkpoint_every=25, validate=True)
items = r.pop("items")
print({k: v for k, v in r.items()}, round(time.time() - t, 1))
if not r["ok"]:
    print(kit.describe(items[:40]))
