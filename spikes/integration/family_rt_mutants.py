# SPDX-License-Identifier: MPL-2.0
"""패밀리 의도 왕복(ADR-0014)의 변이 확인: 핵심 조치를 하나씩 되돌리고 새 시험(tests/test_integration_family_rt.py)이 실패하는지 본다.

사용: DOBIM_IFCDIFF_PYTHON=<ifcdiff 가 있는 python> python family_rt_mutants.py [이름 조각 ...] [--out results/family_rt_mutants.json]
소스 파일을 잠깐 바꾸고 항상 원래대로 복원한다(다른 프로세스가 같은 작업트리를 쓰는 동안 돌리지 마라).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "s1-core"
SRC = ROOT / "src" / "dobim_core"
PY = sys.executable
T = "tests/test_integration_family_rt.py"
env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
M = [  # (이름, 파일, 옛, 새, -k 식)
    ("M01 패밀리 종류 인식 끔", "family/restore.py", "    return k if isinstance(k, str) else None", "    return None", "f1_패밀리"),
    ("M02 인스턴스 클로저의 타입 지정 제거", "ifc_model.py", "        if typed:\n", "        if False:\n", "타입_지정_관계_제거"),
    ("M03 타입 클로저에 속성셋 포함", "ifc_model.py", 'if k not in ("GlobalId", "OwnerHistory", "HasPropertySets")}}', 'if k not in ("GlobalId", "OwnerHistory")}}', "f1_패밀리"),
    ("M04 타입 분리 전파 제거", "foreign.py", 'rows[r["params"]["type"]]["origin"] != "authored"', "False", "f3c"),
    ("M05 파생값(width/height) 대조 제거", "family/restore.py", 'if json.dumps(want, sort_keys=True) != json.dumps(p, sort_keys=True):', "if False:", "width_mismatch"),
    ("M06 시작 상태 타입 가지치기 제거", "engine.py", "self.ifc.prune(types)", "self.ifc.prune(set())", "f2b"),
    ("M07 가져온 패밀리 투영 상태 이어받기 제거", "ifc_model.py", "                self._adopt_family_state(eid, kind, e)\n", "", "f2_왕복 or f2d"),
    ("M08 개구부 GlobalId 유지 제거", "family/ifcproj.py", "model._opening_gid.get(eid) or ", "", "f2d"),
    ("M09 타입 지정 관계 GlobalId 유지 제거", "family/ifcproj.py", 'gid = model._typerel_gid.get(type_eid) or model.derived_guid_for(f"typerel:{type_eid}")', "gid = ifcopenshell.guid.new()", "f2d"),
    ("M10 가져온 프로젝트의 타입 재사용 제거", "commands_family.py", "if tid not in ctx.st.el and ctx.eng.has_base:", "if False:", "f2c"),
    ("M11 개구부 소유(인스턴스) 제거", "foreign.py", 'r["kind"] in ("door", "finst"):', 'r["kind"] == "door":', "f1_패밀리"),
    ("M12 평면 문맥 이어받기 제거", "ifc_model.py", 'c.ContextType == "Plan" and c.ContextIdentifier == "FootPrint" and c.TargetView == "PLAN_VIEW"', "False", "f2_왕복 or f2d"),
    ("M13 레시피 해시 대조 제거", "family/restore.py", 'if p["recipe_hash"] != FS.recipe_hash_of(p["recipe"]):', "if False:", "damage_hash or hash"),
    ("M14 타입 패밀리 id 대조 제거", "family/restore.py", 'if d["family_id"] != r.id:', "if False:", "type_wrong_family"),
    ("M15 타입 값 완전성 대조 제거", "family/restore.py", "if FE.canon(tv) != FE.canon(p[\"type_values\"]):", "if False:", "type_values"),
    ("M16 인스턴스 값 완전성 대조 제거", "family/restore.py", 'if FE.canon(iv) != FE.canon(p["values"]):', "if False:", "unknown_value or value_range or values_incomplete"),
    ("M17 타입 부속 스냅숏의 타입 예외 제거", "ifc_model.py", "if not e.is_a(\"IfcTypeObject\") and e.Representation is not None:", "if e.Representation is not None:", "f2b"),
    ("M18 위치 범위 제한 제거", "family/restore.py", "if not K.num_ok(v) or abs(v) > MAX_MM:", "if not K.num_ok(v):", "huge_offset"),
    ("M19 호스팅 여부 대조 제거", "family/restore.py", 'if hosted != (r.host is not None) or hosted != ("offset" in p):', "if False:", "host_to_level"),
    ("M20 의도 크기 상한 제거", "family/restore.py", "len(text) > MAX_INTENT_CHARS", "False", "huge_text or f4c"),
]

out = None
args = sys.argv[1:]
if "--out" in args:
    i = args.index("--out")
    out = Path(args[i + 1])
    del args[i:i + 2]
only = args
res = []
for name, rel, old, new, k in M:
    if only and not any(o in name for o in only):
        continue
    if k is None:
        res.append((name, "건너뜀(전용 시험 없음)")); continue
    p = SRC / rel
    orig = p.read_text(encoding="utf-8")
    if old not in orig:
        res.append((name, "패턴 없음")); continue
    p.write_text(orig.replace(old, new, 1), encoding="utf-8", newline="")
    try:
        t0 = time.time()
        r = subprocess.run([PY, "-m", "pytest", T, "-q", "-x", "-p", "no:cacheprovider", "-k", k], capture_output=True, text=True, env=env,
                           cwd=str(ROOT), encoding="utf-8", timeout=900, stdin=subprocess.DEVNULL)
        tail = [l for l in r.stdout.splitlines() if l.startswith("FAILED") or " passed" in l or " failed" in l][-1:]
        res.append((name, ("실패(이빨 있음)" if r.returncode != 0 else "통과(이빨 없음!)"), f"{time.time() - t0:.0f}s", tail))
    finally:
        p.write_text(orig, encoding="utf-8", newline="")
    print(res[-1], flush=True)
print("DONE", flush=True)
if out is not None:
    rows = [{"name": r[0], "result": r[1]} for r in res]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"mutants": rows, "killed": sum(1 for r in rows if r["result"].startswith("실패")), "total": len(rows)}, ensure_ascii=False, indent=2), encoding="utf-8")
