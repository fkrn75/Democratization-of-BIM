import sys, tempfile, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
d = Path(tempfile.mkdtemp())
r = kit.property_run(seed, n, d)
items = r.pop("items")
print(r["ok"], r["diffs"])
if not r["ok"]:
    small, evals = kit.shrink(items, d, max_evals=400)
    print("축소", len(items), "->", len(small), "평가", evals)
    print(kit.describe(small))
    # 어떤 제품이 다른지 찾는다
    p = d / "dbg.dobim"
    s = kit.Session(p)
    for it in small:
        s.item(it)
    s.eng.flush_ifc()
    live = s.eng.ifc.semantic_dump()
    from dobim_core.engine import Engine
    rep = Engine(p, "re") if (len(sys.argv) > 3 and sys.argv[3] == "reopen") else kit.replay_log(kit.log_rows(s.eng), d / "dbg-rep.dobim", s.eng.project_guid_uuid)
    rep.flush_ifc()
    other = rep.ifc.semantic_dump()
    for sec, (a, b) in enumerate(zip(live, other)):
        if a != b:
            print("섹션", sec)
            for x in a:
                if x not in b:
                    print("  live only", json.dumps(x)[:3000])
            for x in b:
                if x not in a:
                    print("  rep only ", json.dumps(x)[:3000])
