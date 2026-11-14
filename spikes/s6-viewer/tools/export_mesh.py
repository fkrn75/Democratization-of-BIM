"""IFC -> 직접 구현용 바이너리 메쉬 내보내기(ifcopenshell 테셀레이션, 월드 좌표).
삼각형마다 정점 3개를 펼친(non-indexed) 위치+플랫 법선 버퍼와 요소별 [start,count] 범위 JSON 생성."""
import sys, json, multiprocessing
import numpy as np, ifcopenshell, ifcopenshell.geom as geom
src, out = sys.argv[1], sys.argv[2]
f = ifcopenshell.open(src)
s = geom.settings(); s.set("use-world-coords", True)
it = geom.iterator(s, f, multiprocessing.cpu_count())
pos, nor, rng, types = [], [], [], []
start = 0
if it.initialize():
    while True:
        sh = it.get(); g = sh.geometry
        v = np.array(g.verts, dtype=np.float32).reshape(-1, 3)
        t = np.array(g.faces, dtype=np.int64).reshape(-1, 3)
        tri = v[t]                                   # (T,3,3)
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        pos.append(tri.reshape(-1, 3)); nor.append(np.repeat(n, 3, axis=0).astype(np.float32))
        cnt = t.shape[0] * 3
        rng.append([sh.id, start, cnt]); types.append(f.by_id(sh.id).is_a()); start += cnt
        if not it.next(): break
P = np.concatenate(pos).astype(np.float32); N = np.concatenate(nor).astype(np.float32)
open(out + ".pos", "wb").write(P.tobytes()); open(out + ".nor", "wb").write(N.tobytes())
json.dump({"elements": len(rng), "vertices": int(P.shape[0]), "ranges": rng, "types": types}, open(out + ".json", "w"))
print("elements", len(rng), "vertices", P.shape[0])
