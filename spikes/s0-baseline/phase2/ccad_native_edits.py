"""네이티브 RA-01(native.ccad) 위에서 E1~E6 를 ccad CLI 만으로 수행. 컨테이너 격리. 결과 요약을 출력."""
import copy, json, sys, time
sys.path.insert(0, "phase2")
from ccad_drive import run

W = "out/p2/ccad/work/"
T = {}


def timed(label, fn):
    t = time.perf_counter()
    r = fn()
    T[label] = round(time.perf_counter() - t, 2)
    return r


def snap(project):
    rc, res, so, se = run([("elements_list", {})], project=project, label="snap-list")
    ids = [e["id"] for e in res[0]["result"]]
    rc, res, so, se = run([("element_get", {"id": i}) for i in ids], project=project, label="snap-get")
    return {r["result"]["id"]: r["result"] for r in res}


def flat(d, p=""):
    o = {}
    for k, v in d.items():
        if isinstance(v, dict):
            o.update(flat(v, p + k + "."))
        else:
            o[p + k] = v
    return o


def num_eq(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6
    except Exception:
        return json.dumps(a) == json.dumps(b)


def diff(a, b):
    ch = {}
    for i in a:
        fa, fb = flat(a[i]), flat(b.get(i, {}))
        d = {}
        for k in set(fa) | set(fb):
            x, y = fa.get(k), fb.get(k)
            if isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
                same = all(num_eq(p, q) if not isinstance(p, list) else json.dumps(p) == json.dumps(q) for p, q in zip(x, y)) or json.dumps(x) == json.dumps(y)
                if not same:
                    d[k] = (x, y)
            elif not num_eq(x, y):
                d[k] = (x, y)
        if d:
            ch[i] = d
    return ch


def summarize(title, base, ch):
    by = {}
    for i in ch:
        by.setdefault(base[i]["category"], []).append(i)
    print(f"== {title}: 실변경 {len(ch)}/{len(base)} 카테고리별 {({k: len(v) for k, v in by.items()})}")
    return by


base = snap("native.ccad")
walls = {i: e for i, e in base.items() if e["category"] == "wall"}
w1 = next(i for i, e in walls.items() if e["params"]["start"] == [0.0, 0.0] and e["params"]["end"] == [12.0, 0.0] and e["level"]["name"] == "1F")
print("W-E-01 id", w1, "원래 1F 벽 높이", sorted({e["params"].get("height") for e in walls.values() if e["level"]["name"] == "1F"}), "top", sorted({str(e["params"]["top"]) for e in walls.values()}))

# ---- E1
rc, res, so, se = run([("elements_move", {"ids": [w1], "delta": [0, 0.6]})], project="native.ccad", save_as="n_e1.ccad", label="E1")
print("E1", rc, json.dumps(res[0]["result"]) if res else se[:300])
s1 = snap("n_e1.ccad")
ch = diff(base, s1)
by = summarize("E1 벽 600mm 이동", base, ch)
for i, d in ch.items():
    print("   ", i, base[i]["category"], {k: v for k, v in list(d.items())[:3]})

# ---- E2
rc, res, so, se = run([("type_get", {"type": "DT-01"})], project="native.ccad", label="E2g")
df = copy.deepcopy(res[0]["result"]["params"]["def"])
for p in df["params"]:
    if p["name"] == "Width":
        p["value"] = 1.0
rc, res, so, se = run([("type_set", {"type": "DT-01", "params": {"def": df}})], project="native.ccad", save_as="n_e2.ccad", label="E2")
print("E2", rc, se[:300])
s2 = snap("n_e2.ccad")
ch = diff(base, s2)
by = summarize("E2 문 타입 폭 0.9->1.0", base, ch)
for i, d in list(ch.items())[:4]:
    print("   ", i, base[i]["category"], base[i]["type"]["name"], {k: v for k, v in list(d.items())[:3]})

# ---- E3
rc, res, so, se = run([("level_set", {"level": "2F", "elevation": 3.6}), ("ifc_export", {"path": "/out/n_e3.ifc"})], project="native.ccad", save_as="n_e3.ccad", label="E3")
print("E3", rc, se[:300])
s3 = snap("n_e3.ccad")
ch = diff(base, s3)
by = summarize("E3 2F 3.3->3.6", base, ch)
g1 = [e for e in s3.values() if e["category"] == "wall" and e["level"]["name"] == "1F"]
g2 = [e for e in s3.values() if e["category"] == "wall" and e["level"]["name"] == "2F"]
print("   1F 벽 높이", sorted({round(e['derived']['height'], 3) for e in g1}), "2F 벽 높이", sorted({round(e['derived']['height'], 3) for e in g2}))
for cat in ("column", "beam", "door", "window", "room", "floor"):
    ids = [i for i in ch if base[i]["category"] == cat]
    print("  ", cat, f"{len(ids)}/{sum(1 for e in base.values() if e['category']==cat)} 변경", ({k: v for k, v in list(ch[ids[0]].items())[:2]} if ids else ""))
