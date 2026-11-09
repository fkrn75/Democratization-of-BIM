"""C4(구조화 결과·오류, 트랜잭션·원자성, undo) + C3 CPU 3D 렌더 시간 측정."""
import hashlib, json, sys, time
from pathlib import Path
sys.path.insert(0, "phase2")
from ccad_drive import run
W = Path("out/p2/ccad/work")
sha = lambda f: hashlib.sha256((W / f).read_bytes()).hexdigest()[:12]
# 1) 오류 구조: 없는 요소
rc, res, so, se = run([("element_get", {"id": 999999})], project="native.ccad", label="c4-err")
print("없는 id: rc", rc, "stdout:", so[:200].replace("\n", " "), "| stderr:", se[:200].replace("\n", " "))
# 2) 스키마 위반
rc, res, so, se = run([("elements_move", {"ids": "x", "delta": [0, 1]})], project="native.ccad", label="c4-schema")
print("스키마 위반: rc", rc, se[:250].replace("\n", " "))
# 3) 원자성: [유효 이동, 유효하지 않은 편집] + --save-as 는 오류 시 저장되는가
before = sha("native.ccad")
rc, res, so, se = run([("elements_move", {"ids": [101], "delta": [0, 0.6]}), ("element_get", {"id": 999999})], project="native.ccad", save_as="c4_atomic.ccad", label="c4-atomic")
print("원자성: rc", rc, "c4_atomic.ccad 생성?", (W / "c4_atomic.ccad").exists(), "원본 해시 불변?", before == sha("native.ccad"))
# 4) 같은 세션 undo/redo
rc, res, so, se = run([("elements_move", {"ids": [101], "delta": [0, 0.6]}), ("element_get", {"id": 101}), ("undo", {}), ("element_get", {"id": 101})], project="native.ccad", label="c4-undo")
print("undo: 이동 후 start", res[1]["result"]["params"]["start"], "-> undo 후", res[3]["result"]["params"]["start"])
# 5) 모델을 무효로 만드는 편집 거부(문서: refused if it would leave the model invalid)
rc, res, so, se = run([("wall_create", {"start": [0, 0], "end": [0, 0], "level": "1F"})], project="native.ccad", label="c4-invalid")
print("길이 0 벽: rc", rc, (so or se)[:250].replace("\n", " "))
# 6) CPU 3D 렌더
t = time.perf_counter()
rc, res, so, se = run([("view_export", {"view": "{3D}", "path": "/out/3d.png", "format": "png", "dpi": 96})], project="native.ccad", label="c3-3d")
print("3D PNG: rc", rc, round(time.perf_counter() - t, 2), "s", se[:200], (W / "3d.png").stat().st_size if (W / "3d.png").exists() else "없음")
