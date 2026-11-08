# SPDX-License-Identifier: MPL-2.0
"""동결본에 실제로 들어간 구성요소를 모아 THIRD-PARTY-NOTICES 를 만들고, 금지 라이선스가 있으면 실패한다.

수집 방법 (동결 결과물이 정본이다 — 설치된 패키지 전체가 아니라 *실제로 포함된 것*):
  1. 파이썬 모듈: PyInstaller 가 남긴 PYZ-*.toc 의 모듈 이름 -> 최상위 패키지 -> 배포(distribution)
  2. 동결 폴더(_internal)의 파일(dll, pyd, 데이터): 빌드 venv 의 RECORD 와 대조해 소유 배포를 찾는다
  3. 어느 배포에도 속하지 않는 파일: 런타임 패턴 표(CPython, OpenSSL, UCRT 등)로 귀속, 그래도 없으면 '귀속 불명'
  4. 휠 안에 묻어 들어온 네이티브 라이브러리(OCCT, GEOS, OpenBLAS 등): 패턴 표로 별도 구성요소로 올린다
판정은 licensing.py. 금지(forbid) 가 하나라도 있으면 종료 코드 1. unknown 은 목록으로 남기고 --strict 일 때만 실패.

사용: python gen_notices.py [--inject 이름=SPDX식 ...] [--strict]   (--inject 는 금지 검사가 실제로 실패하는지 보는 음성 대조)
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from importlib import metadata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FROZEN_DIR, RESULTS, S7, write_json  # noqa: E402
import licensing  # noqa: E402

WORK = S7 / "build" / "work" / "dobim"
INTERNAL = FROZEN_DIR / "_internal"
OUT_TXT = S7 / "results" / "THIRD-PARTY-NOTICES.txt"
LICENSE_FILE = re.compile(r"(^|/)(LICEN[CS]E[^/]*|COPYING[^/]*|NOTICE[^/]*|AUTHORS[^/]*|COPYRIGHT[^/]*)$", re.I)

# 배포 이름 -> SPDX 식 보정. 메타데이터가 없거나 모호할 때만 쓰고, 근거를 남긴다.
DIST_OVERRIDES = {
    "pywin32": ("PSF-2.0", "pywin32 프로젝트 문서의 라이선스 표기(PSF). 메타데이터에 식이 없다"),
    "pywin32-ctypes": ("BSD-3-Clause", "프로젝트 LICENSE.txt"),
}

# 어느 배포에도 속하지 않는 런타임 파일 / 휠에 묻어 들어온 네이티브 라이브러리 (파일 이름 패턴 -> 구성요소)
# 형식: (정규식, 구성요소 이름, SPDX 식, 근거·비고, 라이선스 전문을 찾을 로컬 참고 파일 키)
NATIVE_RULES = [
    (r"^(python3(12)?\.dll|base_library\.zip|pyexpat\.pyd|unicodedata\.pyd|_(asyncio|bz2|ctypes|decimal|hashlib|lzma|multiprocessing|"
     r"overlapped|queue|socket|sqlite3|ssl|uuid|wmi|zoneinfo|elementtree)\.pyd|select\.pyd)$",
     "CPython 3.12 런타임", "PSF-2.0", "표준 파이썬 배포본. pyexpat 은 expat(MIT)을 포함", "cpython"),
    (r"^(libcrypto-3|libssl-3)\.dll$", "OpenSSL 3.x", "Apache-2.0", "파이썬 배포본에 동봉된 OpenSSL. 저작권 문구는 해당 배포본 기준", "openssl"),
    (r"^libffi-8\.dll$", "libffi", "MIT", "파이썬 배포본에 동봉", "libffi"),
    (r"^sqlite3\.dll$", "SQLite", "blessing", "퍼블릭 도메인(저작권 포기). 고지 의무 없음", None),
    (r"^(msvcp140|vcruntime140(_1)?)(-[0-9a-f]+)?\.dll$", "Microsoft Visual C++ 런타임", "LicenseRef-MS-Redistributable",
     "마이크로소프트 재배포 가능 파일. Visual Studio/VC++ Redistributable 의 재배포 조항을 따른다(비 OSS)", None),
    (r"^(ucrtbase\.dll|api-ms-win-[a-z0-9\-]+\.dll)$", "Microsoft Universal CRT", "LicenseRef-MS-Redistributable",
     "Windows 유니버설 CRT 재배포 파일(파이썬 배포본이 동봉). 재배포 조항을 따른다(비 OSS)", None),
    (r"^dobim(-mcp)?\.exe$", "PyInstaller 부트로더", "GPL-2.0-or-later WITH Bootloader-exception",
     "실행 파일 앞부분의 부트로더. PyInstaller 의 예외절이 어떤 라이선스의 앱에도 쓰도록 허용한다(법무 확인 대상으로 남긴다)", "pyinstaller"),
    # 휠 안의 네이티브 (경로가 소유 배포에 속하므로 '구성요소 추가'로 처리한다: 소유 배포 뒤에 별도 항목)
    (r"^ifcopenshell/TK[A-Za-z0-9]+\.dll$", "Open CASCADE Technology (OCCT, ifcopenshell 휠에 동봉)", "LGPL-2.1-only WITH OCCT-exception-1.0",
     "OCCT 7.x 는 LGPL-2.1 + OCCT 예외. ADR-0003 의 휠 고지 항목", None),
    (r"^ifcopenshell/boost_[a-z_]+.*\.dll$", "Boost (ifcopenshell 휠에 동봉)", "BSL-1.0", "Boost Software License", None),
    (r"^ifcopenshell/manifold\.dll$", "Manifold (ifcopenshell 휠에 동봉)", "Apache-2.0", "Manifold 형상 라이브러리", None),
    (r"^ifcopenshell/ifcopenshell_geometry_kernel_cgal[a-z]*\.dll$", "CGAL 기반 지오메트리 커널 (ifcopenshell 휠에 동봉)",
     "LicenseRef-CGAL-UNVERIFIED",
     "CGAL 은 패키지별로 LGPL-3.0+ 또는 GPL-3.0+ 이다. 이 DLL 이 GPL 패키지(Nef 다면체 등)에 링크됐는지 확인되지 않았다 -> 법무 확인 필요. "
     "코어는 지오메트리 커널을 쓰지 않으므로 동결본에서 제거하는 완화책을 시험했다(slim 시험 결과 참고)", None),
    (r"^ifcopenshell/ifcopenshell_geometry_svgfill\.dll$", "svgfill (ifcopenshell 휠에 동봉)", "LicenseRef-CGAL-UNVERIFIED",
     "svgfill 은 CGAL 을 쓴다. 위와 같은 사유로 라이선스 확인 필요", None),
    (r"^numpy\.libs/libscipy_openblas64_.*\.dll$", "OpenBLAS (numpy 휠에 동봉)", "BSD-3-Clause", "numpy 의 LICENSES_bundled 에 근거", None),
    (r"^numpy\.libs/msvcp140.*\.dll$", "Microsoft Visual C++ 런타임 (numpy 휠에 동봉)", "LicenseRef-MS-Redistributable", "재배포 가능 파일", None),
    (r"^shapely\.libs/geos.*\.dll$", "GEOS (shapely 휠에 동봉)", "LGPL-2.1-only", "shapely 의 LICENSE_GEOS. LGPL 이므로 교체 가능해야 한다 "
     "(별도 DLL 이라 파일 교체로 충족, 교체 시험은 library_swap 참고)", "geos"),
    (r"^shapely\.libs/msvcp140.*\.dll$", "Microsoft Visual C++ 런타임 (shapely 휠에 동봉)", "LicenseRef-MS-Redistributable", "재배포 가능 파일", None),
]

# 라이선스 전문을 동결본이나 휠에서 못 찾을 때 쓸 참고 사본 (이 PC 의 다른 배포본에서). 키 -> (설명, 후보 경로 목록)
# 경로 종류: site = 빌드 venv site-packages 기준, abs = 절대 경로, base = sys.base_prefix 기준.
# 참고 사본은 같은 라이선스의 일반 전문일 뿐 해당 버전의 저작권 줄과 같다는 보증이 없다 -> 결과에 reference_text_only 로 남긴다.
_GIT = r"C:\Program Files\Git"
REF_TEXTS = {
    "LGPL-3.0": ("setuptools 번들(autocommand)의 LGPL-3.0 전문", [("site", "setuptools/_vendor/autocommand-2.2.2.dist-info/LICENSE")]),
    "GPL-3.0": ("Git for Windows 의 GPL-3.0 전문(COPYING3)", [("abs", _GIT + r"\mingw64\share\licenses\gcc-libs\COPYING3")]),
    "cpython": ("파이썬 설치본의 LICENSE.txt", [("base", "LICENSE.txt")]),
    "openssl": ("Git for Windows 동봉 OpenSSL 의 라이선스 문서", [("abs", _GIT + r"\usr\share\licenses\libopenssl\LICENSE.txt"),
                                                              ("abs", _GIT + r"\mingw64\share\licenses\openssl\LICENSE.txt")]),
    "libffi": ("Git for Windows 동봉 libffi 의 LICENSE", [("abs", _GIT + r"\usr\share\licenses\libffi\LICENSE"),
                                                      ("abs", _GIT + r"\mingw64\share\licenses\libffi\LICENSE")]),
    "pyinstaller": ("PyInstaller 배포의 COPYING.txt(부트로더 예외 포함)", [("site", "pyinstaller-6.22.3.dist-info/licenses/COPYING.txt")]),
    "geos": ("shapely 휠의 LICENSE_GEOS", [("site", "shapely-2.2.0.dist-info/licenses/LICENSE_GEOS")]),
}
# 배포 이름 -> 덧붙일 참고 사본 키 (휠이 라이선스 파일을 안 싣는 경우)
EXACT_REFS = {"geos", "pyinstaller"}   # 실제로 같은 휠/배포가 싣는 원문(참고 사본이 아님)
DIST_REFS = {"ifcopenshell": ["LGPL-3.0", "GPL-3.0"]}
# 모듈 이름 -> 배포 (packages_distributions 가 못 찾는 .pth 경로 모듈)
MODULE_DIST = {"win32con": "pywin32", "win32evtlogutil": "pywin32", "winerror": "pywin32", "pywin32_system32": "pywin32",
               "pywintypes": "pywin32", "win32": "pywin32", "win32com": "pywin32"}


def norm_name(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def read_text(p: Path, limit=200_000) -> str:
    try:
        b = p.read_bytes()[:limit]
    except OSError:
        return ""
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def find_ref(key: str, any_dist) -> tuple[str, str] | None:
    desc, srcs = REF_TEXTS[key]
    for kind, rel in srcs:
        if kind == "abs":
            p = Path(rel)
        elif kind == "site":
            p = Path(any_dist.locate_file(rel))
        else:
            p = Path(sys.base_prefix) / rel
        if p.is_file():
            return desc, read_text(p)
    return None


def dist_license(d: metadata.Distribution) -> tuple[str, str]:
    md = d.metadata
    name = norm_name(md["Name"])
    if name in DIST_OVERRIDES:
        return DIST_OVERRIDES[name][0], "override: " + DIST_OVERRIDES[name][1]
    expr = md.get("License-Expression")
    if expr:
        return expr, "metadata License-Expression"
    cls = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    mapped = [licensing.CLASSIFIERS.get(c) for c in cls]
    mapped = [m for m in mapped if m]
    if mapped:
        return " OR ".join(sorted(set(mapped))) if len(set(mapped)) > 1 else mapped[0], "metadata classifier"
    lic = md.get("License")
    if lic and len(lic) < 100 and "\n" not in lic:
        return lic, "metadata License 필드"
    return "unknown", "메타데이터에 라이선스 정보 없음"


def dist_license_files(d: metadata.Distribution) -> list[tuple[str, str]]:
    out = []
    for f in d.files or []:
        s = str(f).replace("\\", "/")
        if LICENSE_FILE.search(s) and "/tests/" not in s and ".py" not in s[-4:]:
            p = Path(d.locate_file(f))
            if p.is_file():
                out.append((s, read_text(p)))
    return sorted(out)


def frozen_modules() -> set[str]:
    tops = set()
    for toc in sorted(WORK.glob("PYZ-*.toc")):
        data = ast.literal_eval(toc.read_text(encoding="utf-8"))
        for name, _path, _ty in data[1]:
            tops.add(name.split(".")[0])
    return tops


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="append", default=[], help="이름=SPDX식: 가짜 구성요소를 넣어 금지 검사 자체를 시험")
    ap.add_argument("--strict", action="store_true", help="unknown 이 있어도 실패")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()

    dists = {norm_name(d.metadata["Name"]): d for d in metadata.distributions()}
    pkg2dist = metadata.packages_distributions()
    record: dict[str, str] = {}   # site-packages 상대 경로(소문자) -> 배포 이름
    for n, d in dists.items():
        for f in d.files or []:
            record[str(f).replace("\\", "/").lower()] = n

    comps: dict[str, dict] = {}

    def comp(key, name, version, expr, how, kind):
        c = comps.setdefault(key, {"name": name, "version": version, "license_expression": expr, "license_source": how,
                                   "kind": kind, "evidence": [], "license_files": []})
        return c

    # 1) 모듈 -> 배포
    stdlib = set(sys.stdlib_module_names)
    for top in sorted(frozen_modules()):
        if top in stdlib or top == "dobim_core":
            continue
        ds = pkg2dist.get(top) or ([MODULE_DIST[top]] if top in MODULE_DIST else None)
        if not ds:
            comp("unattributed-module-" + top, top, "", "unknown", "어느 배포에도 속하지 않는 모듈", "python-package")["evidence"].append(f"module:{top}")
            continue
        for dn in ds:
            d = dists[norm_name(dn)]
            expr, how = dist_license(d)
            c = comp(norm_name(dn), d.metadata["Name"], d.version, expr, how, "python-package")
            c["evidence"].append(f"module:{top}")
    comp("dobim-core", "dobim-core (이 프로젝트)", "0.0.1", "MPL-2.0", "pyproject.toml", "project")["evidence"].append("module:dobim_core")

    # 2) 동결 폴더 파일 -> 배포 / 네이티브 구성요소
    unattributed = []
    rules = [(re.compile(r, re.I), *rest) for r, *rest in NATIVE_RULES]
    n_files = 0
    for f in sorted([p for p in INTERNAL.rglob("*") if p.is_file()] + [p for p in FROZEN_DIR.glob("*") if p.is_file()]):
        rel = f.relative_to(INTERNAL).as_posix() if INTERNAL in f.parents else f.name
        n_files += 1
        if ".dist-info/" in rel:
            continue
        owner = record.get(rel.lower())
        # 휠 안 네이티브(OCCT, GEOS 등)와 런타임 파일은 소유 배포가 있어도 별도 구성요소로 올린다
        hit = next((r for r in rules if r[0].match(rel)), None)
        if hit:
            _rx, cname, expr, note, refkey = hit
            c = comp("native-" + cname, cname, "", expr, "패턴 표(수동 귀속): " + note, "native")
            c["evidence"].append(rel)
            c["refkey"] = refkey
            continue
        if owner:
            d = dists.get(owner)
            if d is not None:
                expr, how = dist_license(d)
                c = comp(owner, d.metadata["Name"], d.version, expr, how, "python-package")
                c["evidence"].append(rel)
                continue
        if rel.endswith((".py", ".pyc")):
            continue
        unattributed.append(rel)
    # 데이터 파일 중 소유가 확인된 .py 는 위 모듈로 이미 잡힌다

    # 3) 라이선스 전문 수집
    for key, c in comps.items():
        if c["kind"] == "python-package":
            d = dists.get(key)
            if d:
                c["license_files"] = dist_license_files(d)

    anyd = next(iter(dists.values()))
    for key, c in comps.items():
        refs = DIST_REFS.get(key, []) if c["kind"] == "python-package" else ([c["refkey"]] if c.get("refkey") in REF_TEXTS else [])
        for rk in refs:
            got = find_ref(rk, anyd)
            if got:
                if rk in EXACT_REFS:
                    c["license_files"].append((got[0], got[1]))
                else:
                    c["license_files"].append((f"[참고 사본] {rk}: {got[0]}", got[1]))
                    c.setdefault("reference_keys", []).append(rk)

    # 4) 음성 대조용 주입
    for inj in a.inject:
        n, _, expr = inj.partition("=")
        comp("injected-" + n, n, "0", expr, "--inject", "injected")["evidence"].append("injected")

    # 5) 판정
    rows = []
    for key, c in sorted(comps.items(), key=lambda kv: kv[1]["name"].lower()):
        verdict, chosen = licensing.evaluate(c["license_expression"])
        c["verdict"], c["chosen_alternative"] = verdict, chosen
        c["evidence_count"] = len(c["evidence"])
        rows.append(c)
    forbidden = [c for c in rows if c["verdict"] == "forbid"]
    unknown = [c for c in rows if c["verdict"] == "unknown"]
    weak = [c for c in rows if c["verdict"] == "weak"]
    no_text = [c["name"] for c in rows if c["kind"] in ("python-package",) and not c["license_files"]
               and c["verdict"] != "forbid"]

    # 6) 고지 파일
    lines = ["THIRD-PARTY NOTICES — Democratization of BIM (spike S7 동결본)", "=" * 72,
             "이 파일은 tools/gen_notices.py 가 동결 결과물에서 자동으로 만들었다. 직접 고치지 말 것.",
             "DoBIM 코어 자체는 MPL-2.0. 아래는 함께 배포되는 제3자 구성요소와 그 라이선스다.", "",
             "[LGPL 구성요소 교체 안내] LGPL 구성요소(ifcopenshell, OCCT, GEOS)의 파일은 설치 폴더의 _internal 안에 별도 파일로 있다.",
             "사용자는 같은 이름의 호환 빌드로 교체해 쓸 수 있다. 절차는 docs/library-swap.md 를 본다.", "",
             "요약", "-" * 72]
    for c in rows:
        lines.append(f"- {c['name']} {c['version']}".rstrip() + f" — {c['license_expression']}  [{c['verdict']}]")
    lines += ["", "=" * 72, "상세 (구성요소별 라이선스 전문)", "=" * 72]
    for c in rows:
        lines += ["", "-" * 72, f"{c['name']} {c['version']}".rstrip(), f"라이선스: {c['license_expression']}   (근거: {c['license_source']})",
                  f"판정: {c['verdict']} ({c['chosen_alternative']})", f"포함된 파일/모듈 수: {c['evidence_count']}"]
        if c.get("reference_keys") and not [r for r, _ in c["license_files"] if not r.startswith("[참고 사본]")]:
            lines.append("※ 이 구성요소의 자체 라이선스 전문이 배포물에 없어 같은 라이선스의 참고 사본을 붙였다. 해당 버전의 원문으로 교체해야 한다.")
        for rel, text in c["license_files"]:
            lines += ["", f"### {rel}", text.strip("\n")]
    text = "\n".join(lines) + "\n"
    if not a.no_write:
        OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
        OUT_TXT.write_text(text, encoding="utf-8", newline="\n")

    missing, reference_only = [], []
    for c in rows:
        real = [r for r, _ in c["license_files"] if not r.startswith("[참고 사본]")]
        if c["kind"] in ("python-package", "native") and not real and c["verdict"] != "forbid" and c["license_expression"] != "blessing":
            entry = {"name": c["name"], "version": c["version"], "license_expression": c["license_expression"],
                     "reason": "배포물/동결본에 라이선스 전문이 없다"}
            if c.get("reference_keys"):
                entry["reason"] += " -> 같은 라이선스의 참고 사본으로 대체(해당 버전 원문으로 교체 필요)"
                reference_only.append(entry)
            else:
                missing.append(entry)
    ref_notes = sorted({k for c in rows for k in c.get("reference_keys", [])})
    res = {
        "components": [{k: v for k, v in c.items() if k not in ("license_files", "evidence", "refkey")}
                       | {"license_file_names": [r for r, _ in c["license_files"]], "evidence_sample": sorted(c["evidence"])[:3]} for c in rows],
        "counts": {"components": len(rows), "files_in_frozen_dir": n_files, "forbid": len(forbidden), "unknown": len(unknown),
                   "weak_copyleft_or_redistributable": len(weak), "ok": len(rows) - len(forbidden) - len(unknown) - len(weak)},
        "forbidden": [c["name"] for c in forbidden],
        "unknown_or_unclear": [{"name": c["name"], "license_expression": c["license_expression"], "note": c["license_source"]} for c in unknown],
        "weak_obligations": [{"name": c["name"], "license_expression": c["license_expression"]} for c in weak],
        "missing_license_text": missing,
        "reference_text_only": reference_only,
        "unattributed_files": unattributed,
        "reference_texts_appended": ref_notes,
        "notices_bytes": len(text.encode("utf-8")),
        "injected": a.inject,
    }
    res["pass"] = not forbidden and (not unknown or not a.strict)
    if not a.no_write and not a.inject:
        write_json(RESULTS / "s7_licenses.json", res)
    brief = {k: res[k] for k in ("counts", "forbidden", "pass")}
    brief["unknown"] = [u["name"] + "=" + u["license_expression"] for u in res["unknown_or_unclear"]]
    brief["missing_license_text"] = [m["name"] for m in missing]
    brief["reference_text_only"] = [m["name"] for m in reference_only]
    brief["unattributed_files"] = len(unattributed)
    print(__import__("json").dumps(brief, ensure_ascii=False, indent=1))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
