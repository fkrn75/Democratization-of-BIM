"""왕복 실패의 클래스·Pset별 분해. 사용: python breakdown.py REF TEST"""
import collections, sys
import ifcopenshell
import roundtrip_check as rc
a, b = rc.extract(ifcopenshell.open(sys.argv[1])), rc.extract(ifcopenshell.open(sys.argv[2]))
miss = collections.Counter(a["gids"][g] for g in a["gids"] if g not in b["gids"])
print("M1 누락 클래스:", dict(miss))
tot, bad = collections.Counter(), collections.Counter()
for k, v in a["psets"].items():
    tot[k[1]] += 1
    if k not in b["psets"] or not rc._same(v, b["psets"][k]): bad[k[1]] += 1
print("M3 pset별 실패/전체:", {p: f"{bad[p]}/{tot[p]}" for p in tot})
vt, vb = collections.Counter(), collections.Counter()
for g, v in a["volume"].items():
    c = a["gids"][g]; vt[c] += 1
    t = b["volume"].get(g)
    if t is None or abs(t - v) / v > rc.VOL_TOL: vb[c] += 1
print("M6 클래스별 실패/전체:", {c: f"{vb[c]}/{vt[c]}" for c in vt})
print("M2 차이:", {k: (a['classes'].get(k, 0), b['classes'].get(k, 0)) for k in set(a['classes']) | set(b['classes']) if a['classes'].get(k, 0) != b['classes'].get(k, 0)})
print("TEST 에만 있는 클래스 GlobalId 수:", sum(1 for g in b["gids"] if g not in a["gids"]))
print("layers REF/TEST:", len(a["layers"]), len(b["layers"]), "voids", len(a["voids"]), len(b["voids"]), "fills", len(a["fills"]), len(b["fills"]), "intent", len(a["intent"]), len(b["intent"]))
