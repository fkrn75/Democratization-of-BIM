# SPDX-License-Identifier: MPL-2.0
"""동결 폴더의 네이티브 파일을 전수 점검해 고지 누락·금지 흔적·끊긴 의존을 찾는다 (독립 검수 결함 1의 재현·재발 방지 도구).

점검 항목 (하나라도 어기면 종료 코드 1):
  1. 제외 목록(CGAL 커널·svgfill·manifold)의 DLL 이 폴더에 없다
  2. 금지 흔적이 없다: GMP/MPFR/CGAL/libxml2 정적 링크 흔적, GPL 문구 (native_libs.BANNED_SIGS)
  3. 빌드 경로 흔적 _deps/<이름>/ 이 모두 허용 이름이다 (모르는 FetchContent 의존성은 실패)
  4. 폴더 안 파일이 import 하는데 폴더에도 시스템에도 없는 DLL 이 없다 (제외로 의존이 끊긴 플러그인 방지)
  5. 정적 링크 흔적으로 찾은 라이브러리가 모두 고지(results/s7_licenses.json 의 구성요소)에 올라 있다 (--no-notices-check 로 끔)
결과: results/s7_native_audit.json.  사용: python audit_native.py [--dir 동결폴더]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FROZEN_DIR, RESULTS, write_json  # noqa: E402
import native_libs as NL  # noqa: E402


def audit(root: Path, notices_json: Path | None) -> dict:
    root = Path(root)
    internal = root / "_internal"
    sc = NL.scan(root, rel_base=internal if internal.exists() else root)
    problems: list[str] = []

    present_excluded = sorted(p.name for p in NL.native_files(root) if p.name.lower() in NL.EXCLUDED_FILES)
    if present_excluded:
        problems.append(f"제외 대상 DLL 이 폴더에 있다: {present_excluded}")

    why = {bid: w for bid, w, _ in NL.BANNED_SIGS}
    for bid, files in sc["banned"].items():
        problems.append(f"금지 흔적 [{bid}] {why[bid]}: {files[:4]}{' …' if len(files) > 4 else ''}")

    unknown_deps = {k: v for k, v in sc["deps"].items() if re.sub(r"-[0-9][0-9.]*$", "", k) not in NL.KNOWN_DEPS}
    for k, v in unknown_deps.items():
        problems.append(f"허용 목록에 없는 빌드 의존성 경로 _deps/{k}/: {v[:3]}")

    dangling = NL.dangling_imports(root)
    for dll, importers in dangling.items():
        problems.append(f"끊긴 import {dll} <- {importers[:3]}")

    notices_missing: list[str] = []
    if notices_json is not None:
        if not notices_json.exists():
            problems.append(f"고지 JSON 이 없다({notices_json.name}) — gen_notices.py 를 먼저 실행")
        else:
            names = {c["name"] for c in json.loads(notices_json.read_text(encoding="utf-8"))["components"]}
            by_id = {i: n for i, n, *_ in NL.STATIC_LIBS}
            for lid in sc["static"]:
                if by_id[lid] not in names:
                    notices_missing.append(by_id[lid])
            if notices_missing:
                problems.append(f"정적 링크가 확인됐지만 고지에 없는 구성요소: {notices_missing}")

    return {"dir_is_slim": not present_excluded, "files_scanned": sc["files_scanned"],
            "static_libs_found": {k: {"component": next(n for i, n, *_ in NL.STATIC_LIBS if i == k), "files": len(v), "sample": v[:3]}
                                  for k, v in sc["static"].items()},
            "static_lib_versions": sc["versions"], "banned_found": sc["banned"], "build_deps_paths": sc["deps"], "build_roots": sc["buildroots"],
            "present_excluded_files": present_excluded, "dangling_imports": dangling,
            "static_libs_missing_in_notices": notices_missing,
            "limits": "문자열 흔적 기반이다. 심볼·문자열이 제거된 바이너리나 흔적 없는 헤더 라이브러리는 못 잡는다(native_libs.py 머리말).",
            "problems": problems, "pass": not problems}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=FROZEN_DIR)
    ap.add_argument("--no-notices-check", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    res = audit(a.dir, None if a.no_notices_check else RESULTS / "s7_licenses.json")
    if not a.no_write:
        write_json(RESULTS / "s7_native_audit.json", res)
    print(json.dumps({k: res[k] for k in ("files_scanned", "dir_is_slim", "problems", "pass")}, ensure_ascii=False, indent=1))
    print("정적 링크로 확인된 구성요소:", ", ".join(sorted(v["component"] for v in res["static_libs_found"].values())))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
