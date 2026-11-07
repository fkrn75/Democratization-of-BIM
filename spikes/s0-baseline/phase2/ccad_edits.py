"""ConstructCAD(ccad, 컨테이너 격리) 로 E1~E3 수행 + 요소 스냅샷 비교. ra01.ccad 는 RA-01.ifc 가져오기 결과."""
import copy, json, sys, time
sys.path.insert(0, "phase2")
from ccad_drive import run

W = "out/p2/ccad/work/"


def snapshot(project):
    rc, res, so, se = run([("elements_list", {})], project=project, label="snap-list")
    ids = [e["id"] for e in res[0]["result"]]
    rc, res, so, se = run([("element_get", {"id": i}) for i in ids], project=project, label="snap-get")
    return {r["result"]["id"]: r["result"] for r in res}


def flat(d, p=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, p + k + "."))
        else:
            out[p + k] = json.dumps(v)
    return out


def diff(a, b):
    ch = {}
    for i in a:
        fa, fb = flat(a[i]), flat(b.get(i, {}))
        d = {k: (fa.get(k), fb.get(k)) for k in set(fa) | set(fb) if fa.get(k) != fb.get(k)}
        if d:
            ch[i] = d
    return ch


def show(title, ch, base):
    print(f"== {title}: 변경된 요소 {len(ch)}/{len(base)}")
    by = {}
    for i, d in ch.items():
        by.setdefault(base[i]["category"], []).append(i)
    print("  카테고리별:", {k: len(v) for k, v in by.items()})
    return by


base = snapshot("ra01.ccad")
json.dump(base, open("out/p2/ccad/snap_base.json", "w"), ensure_ascii=False)
wid = next(i for i, e in base.items() if e["category"] == "wall" and e["params"]["start"] == [0.0, 0.0] and e["params"]["end"] == [12.0, 0.0] and e["level"]["name"] == "1F")
print("W-E-01 =", wid)

# ---- E1
t = time.perf_counter()
rc, res, so, se = run([("elements_move", {"ids": [wid], "delta": [0, 0.6]}), ("ifc_export", {"path": "/out/e1.ifc"})], project="ra01.ccad", save_as="e1.ccad", label="E1")
print("E1 rc", rc, se[:200], round(time.perf_counter() - t, 2), "s", json.dumps(res[0]["result"])[:300] if res else so[:300])
s1 = snapshot("e1.ccad")
ch = diff(base, s1)
by = show("E1", ch, base)
for i in ch:
    print("  ", i, base[i]["category"], {k: v for k, v in list(ch[i].items())[:4]})
json.dump({str(i): d for i, d in ch.items()}, open("out/p2/ccad/e1_diff.json", "w"), ensure_ascii=False, indent=1)

# ---- E2: 문 타입 DT-01 폭 0.9 -> 1.0
rc, res, so, se = run([("type_get", {"type": "DT-01"})], project="ra01.ccad", label="E2-get")
tp = res[0]["result"]["params"]
tp2 = copy.deepcopy(tp)
for p in tp2["def"]["params"]:
    if p["name"] == "Largura":
        p["value"] = 1.0
t = time.perf_counter()
rc, res, so, se = run([("type_set", {"type": "DT-01", "params": {"def": tp2["def"]}}), ("ifc_export", {"path": "/out/e2.ifc"})], project="ra01.ccad", save_as="e2.ccad", label="E2")
print("E2 rc", rc, se[:200], round(time.perf_counter() - t, 2), "s", json.dumps(res[0]["result"])[:300] if res else so[:300])
s2 = snapshot("e2.ccad")
ch = diff(base, s2)
show("E2", ch, base)
for i in ch:
    print("  ", i, base[i]["category"], base[i]["type"]["name"], {k: v for k, v in list(ch[i].items())[:3]})

# ---- E3: 2F 층고 3.3 -> 3.6 (level_set elevation)
t = time.perf_counter()
rc, res, so, se = run([("level_set", {"level": "2F", "elevation": 3.6}), ("ifc_export", {"path": "/out/e3.ifc"})], project="ra01.ccad", save_as="e3.ccad", label="E3")
print("E3 rc", rc, se[:200], round(time.perf_counter() - t, 2), "s", json.dumps(res[0]["result"])[:300] if res else so[:300])
s3 = snapshot("e3.ccad")
ch = diff(base, s3)
by = show("E3", ch, base)
ex = {c: ch[v[0]] for c, v in by.items()}
for c, d in ex.items():
    print("  예:", c, {k: v for k, v in list(d.items())[:4]})
w1 = [e for e in s3.values() if e["category"] == "wall" and e["level"]["name"] == "1F"]
print("  1F 벽 높이 이후:", sorted({e["params"]["height"] for e in w1}), "top:", sorted({e["params"]["top"] for e in w1}))
