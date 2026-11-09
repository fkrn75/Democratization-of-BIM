# SPDX-License-Identifier: MPL-2.0
"""뷰·주석 정의 왕복(ADR-0015)의 변이 확인: 핵심 조치를 하나씩 되돌리고 새 시험(tests/test_integration_views_rt.py)이 실패하는지 본다.

사용: DOBIM_IFCDIFF_PYTHON=<ifcdiff 가 있는 python> python views_rt_mutants.py [이름 조각 ...] [--out results/views_rt_mutants.json]
작업트리의 소스를 바꾸지 않는다: 임시 폴더에 필요한 부분(s1-core, s2-roundtrip, s0-baseline 의 .py)을 복사해 그 복사본에서 변이시킨다
(다른 프로세스가 같은 작업트리를 쓰는 동안에도 돌릴 수 있다).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
T = "tests/test_integration_views_rt.py"
M = [  # (이름, 파일, 옛, 새, -k 식)
    ("M01 변조 탐지 해시 대조 제거", "viewdefs.py", 'if d["check"] != check_of(eid, kind, params):', "if False:", "위치만 or 정의만 or 바꿔치기_해시 or 해시를_지움"),
    ("M02 파라미터 모양 검사 제거", "viewdefs.py", "if (err := K.row_params_error(kind, params)) is not None:", "if False:", "키_누락 or 키_추가 or 이름_너무_김 or 이름에_NUL"),
    ("M03 값 범위 검사 제거", "viewdefs.py", '        {"view": _view_values, "dim": _dim_values, "tag": _tag_values}[kind](params)', "        pass", "거대_절단 or 범위_밖 or 축척 or 방위 or 절단선"),
    ("M04 정의 크기 상한 제거", "viewdefs.py", "len(text) > MAX_DEF_CHARS", "False", "거대_텍스트"),
    ("M05 NaN 거절 제거", "family/recipe.py", "raise _NonFinite(name)", "return 0", "NaN"),
    ("M06 정의 id 충돌 검사 제거", "foreign.py", "if eid in taken or eid in rows:", "if False:", "v4c or v4d"),
    ("M07 같은 이름 뷰 검사 제거", "foreign.py", "if len(ids) > 1:   # 뷰 이름은", "if False:   # 뷰 이름은", "같은_이름_뷰"),
    ("M08 평면 뷰 레벨 저작 확인 제거", "foreign.py", 'if st is not None and st != ("level", "authored"):', "if False:", "v4f or 레벨이_벽"),
    ("M09 치수 참조 저작 확인 제거", "foreign.py", 'if st is not None and st != (rk, "authored"):', "if False:", "v4e or v4i or 참조_종류_틀림"),
    ("M10 태그 대상 저작 확인 제거", "foreign.py", 'elif st is not None and (st[1] != "authored" or not VD.tag_fields_ok(st[0], p["fields"])):', "elif False:", "v4i"),
    ("M11 뷰 없는 주석 복원 거절 제거", "foreign.py", "        if v is None:\n", "        if False:\n", "없는_뷰를_가리키는 or v4f"),
    ("M12 치수·뷰 종류 일치 확인 제거", "foreign.py", 'if ("at" in p) == (vt == "plan"):', "if False:", "치수가_단면뷰에"),
    ("M13 같은 이름 속성 처리 제거", "foreign.py", "        if name in out or name in dup:", "        if False:", "v4b"),
    ("M14 속성셋 모호 거절 제거", "foreign.py", "    if len(psets) > 1:", "    if False:", "v4b"),
    ("M15 길이 단위 확인 제거", "foreign.py", "    if unit_ok:\n        _restore_defs", "    if True:\n        _restore_defs", "v4h"),
    ("M16 가져온 정의 소유 이어받기 제거", "engine.py", "            self.ifc.adopt_defs(owned)", "            pass", "v2b or v1_ or v2_"),
    ("M17 소유하지 않은 속성 보존 제거", "ifc_model.py", "        removed = [i for i in self._defs_text if i not in desired]", "        removed = [p.Name for p in (self._defs_pset(False).HasProperties if self._defs_pset(False) else ()) if p.Name not in desired]", "v5d or v4i or v4f"),
    ("M18 빈 속성셋 정리 제거", "ifc_model.py", '            if not [p for p in ps.HasProperties or () if p.is_a("IfcProperty")]:', "            if False:", "v2b or v5_"),
    ("M19 정의 갱신(flush) 제거", "engine.py", "        self._flush_defs()\n        return n", "        return n", "v1_ or v5_"),
    ("M20 바뀐 정의만 쓰기 제거", "ifc_model.py", "        changed = {i: t for i, t in desired.items() if self._defs_text.get(i) != t}", "        changed = dict(desired)", "v6b"),
    ("M21 속성셋 GlobalId 파생 제거", "ifc_model.py", 'ps.GlobalId = derived_guid(self.project_guid, "defs:pset")', "pass", "v1c"),
    ("M22 annot_of 관계 복원 제거", "foreign.py", '            rels.add(("annot_of", eid, d["params"]["view"]))', "            pass", "v1_ or v3"),
    ("M23 서로게이트 거절 제거", "viewdefs.py", "        if not _name_ok(params[\"name\"]):", "        if False:", "서로게이트"),
    ("M24 방향 지문 모양 검사 제거", "viewdefs.py", "or not _dirs_ok(p[\"dirs\"]):", "or False:", "지문_모양"),
    ("M25 정의 개수 상한 제거", "foreign.py", "if len(out) > VD.MAX_DEFS:", "if False:", None),
    ("M26 id 에 묶인 해시 제거", "viewdefs.py", 'return hashlib.sha256(canon({"id": eid, "kind": kind, "params": params}).encode("utf-8")).hexdigest()',
     'return hashlib.sha256(canon({"kind": kind, "params": params}).encode("utf-8")).hexdigest()', "v4c or v4d or 같은_이름_뷰"),
]


def main():
    out = None
    args = sys.argv[1:]
    if "--out" in args:
        i = args.index("--out")
        out = Path(args[i + 1])
        del args[i:i + 2]
    only = args
    tmp = Path(tempfile.mkdtemp(prefix="dobim-mut-"))
    try:
        for rel in ("spikes/s1-core/src", "spikes/s1-core/tests", "spikes/s1-core/tools", "spikes/s2-roundtrip"):
            shutil.copytree(REPO / rel, tmp / rel, ignore=shutil.ignore_patterns("__pycache__", "results", ".pytest_cache"))
        for f in (REPO / "spikes/s0-baseline").glob("*.py"):
            (tmp / "spikes/s0-baseline").mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, tmp / "spikes/s0-baseline" / f.name)
        shutil.copy2(REPO / "spikes/s1-core/pyproject.toml", tmp / "spikes/s1-core/pyproject.toml")
        root = tmp / "spikes/s1-core"
        src = root / "src/dobim_core"
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root / "src"), "PYTHONIOENCODING": "utf-8"}
        res = []
        for name, rel, old, new, k in M:
            if only and not any(o in name for o in only):
                continue
            if k is None:
                res.append((name, "건너뜀(전용 시험 없음)")); continue
            p = src / rel
            orig = p.read_text(encoding="utf-8")
            if old not in orig:
                res.append((name, "패턴 없음")); print(res[-1], flush=True); continue
            p.write_text(orig.replace(old, new, 1), encoding="utf-8", newline="")
            try:
                t0 = time.time()
                r = subprocess.run([PY, "-m", "pytest", T, "-q", "-x", "-p", "no:cacheprovider", "-k", k], capture_output=True, text=True, env=env,
                                   cwd=str(root), encoding="utf-8", timeout=1500, stdin=subprocess.DEVNULL)
                tail = [l for l in r.stdout.splitlines() if l.startswith("FAILED") or " passed" in l or " failed" in l][-1:]
                res.append((name, ("실패(이빨 있음)" if r.returncode != 0 else "통과(이빨 없음!)"), f"{time.time() - t0:.0f}s", tail))
            finally:
                p.write_text(orig, encoding="utf-8", newline="")
            print(res[-1], flush=True)
        print("DONE", flush=True)
        if out is not None:
            rows = [{"name": r[0], "result": r[1]} for r in res]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"mutants": rows, "killed": sum(1 for r in rows if r["result"].startswith("실패")), "total": len(rows)},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
