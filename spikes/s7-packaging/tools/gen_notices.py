# SPDX-License-Identifier: MPL-2.0
"""동결본에 실제로 들어간 구성요소를 모아 THIRD-PARTY-NOTICES 를 만들고, 금지·미확인 라이선스가 있으면 실패한다.

수집 방법 (동결 결과물이 정본이다 — 설치된 패키지 전체가 아니라 *실제로 포함된 것*):
  1. 파이썬 모듈: PyInstaller 가 남긴 PYZ-*.toc 의 모듈 이름 -> 최상위 패키지 -> 배포(distribution)
  2. 동결 폴더(_internal)의 파일(dll, pyd, 데이터): 빌드 venv 의 RECORD 와 대조해 소유 배포를 찾는다
  3. 어느 배포에도 속하지 않는 파일: 런타임 패턴 표(CPython, OpenSSL, UCRT 등)로 귀속, 그래도 없으면 '귀속 불명'(실패)
  4. 휠 안에 묻어 들어온 네이티브 라이브러리(OCCT, GEOS, OpenBLAS 등): 패턴 표로 별도 구성요소로 올린다
  5. 네이티브 파일 *내용*의 정적 링크 흔적(native_libs.STATIC_LIBS): RocksDB·PROJ·Eigen 등 이름표에 없는 라이브러리를 증거 기반으로 올린다
  6. Tauri 셸의 Rust 크레이트(rust_notices.py, cargo metadata 기반)
판정은 licensing.py. 기본은 엄격하다: 금지(forbid)·미확인(unknown)·귀속 불명 파일이 하나라도 있으면 종료 코드 1.
--lenient 는 unknown·귀속 불명을 목록으로만 남기고 통과시킨다(탐색용). forbid 는 --lenient 로도 통과하지 못한다.
배포용 고지(THIRD-PARTY-NOTICES.txt)에는 내부 메모를 넣지 않는다: 내부 메모(법무 확인 대상 등)는 결과 JSON 의 internal_note 에만 남긴다.

사용: python gen_notices.py [--inject 이름=SPDX식 ...] [--lenient] [--no-rust] [--no-write]   (--inject 는 금지 검사가 실제로 실패하는지 보는 음성 대조)
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
import native_libs  # noqa: E402
import rust_notices  # noqa: E402

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
# 형식: (정규식, 구성요소 이름, SPDX 식, 공개 비고(배포용 고지에 그대로 실린다), 라이선스 전문을 찾을 로컬 참고 파일 키)
# 내부 메모(법무 확인 대상 등)는 INTERNAL_NOTES 에 따로 둔다 — 배포용 고지에는 넣지 않는다.
# 제외한 DLL(CGAL 커널·svgfill·manifold)의 규칙은 없다: 동결본에 다시 나타나면 '귀속 불명'으로 실패한다(audit_native.py 도 잡는다).
NATIVE_RULES = [
    (r"^(python3(12)?\.dll|base_library\.zip|pyexpat\.pyd|unicodedata\.pyd|_(asyncio|bz2|ctypes|decimal|hashlib|lzma|multiprocessing|"
     r"overlapped|queue|socket|sqlite3|ssl|uuid|wmi|zoneinfo|elementtree)\.pyd|select\.pyd)$",
     "CPython 3.12 런타임", "PSF-2.0", "표준 파이썬 배포본. zlib·bzip2·liblzma·expat·mpdecimal 은 아래 별도 항목", "cpython"),
    (r"^(libcrypto-3|libssl-3)\.dll$", "OpenSSL 3.x", "Apache-2.0", "파이썬 배포본에 동봉된 OpenSSL", "openssl"),
    (r"^libffi-8\.dll$", "libffi", "MIT", "파이썬 배포본에 동봉", "libffi"),
    (r"^sqlite3\.dll$", "SQLite", "blessing", "퍼블릭 도메인(저작권 포기). 고지 의무 없음", None),
    (r"^(msvcp140|vcruntime140(_1)?)(-[0-9a-f]+)?\.dll$", "Microsoft Visual C++ 런타임", "LicenseRef-MS-Redistributable",
     "마이크로소프트 재배포 가능 파일. Visual C++ Redistributable 의 재배포 조항을 따른다(오픈소스 라이선스가 아님)", None),
    (r"^(ucrtbase\.dll|api-ms-win-[a-z0-9\-]+\.dll)$", "Microsoft Universal CRT", "LicenseRef-MS-Redistributable",
     "Windows 유니버설 CRT 재배포 파일. 재배포 조항을 따른다(오픈소스 라이선스가 아님)", None),
    (r"^dobim(-mcp)?\.exe$", "PyInstaller 부트로더", "GPL-2.0-or-later WITH Bootloader-exception",
     "실행 파일 앞부분의 부트로더. PyInstaller 의 부트로더 예외가 어떤 라이선스의 프로그램에도 쓰도록 허용한다", "pyinstaller"),
    # 휠 안의 네이티브 (경로가 소유 배포에 속하므로 '구성요소 추가'로 처리한다: 소유 배포 뒤에 별도 항목)
    (r"^ifcopenshell/TK[A-Za-z0-9]+\.dll$", "Open CASCADE Technology (OCCT, ifcopenshell 휠에 동봉)", "LGPL-2.1-only WITH OCCT-exception-1.0",
     "LGPL-2.1 + OCCT 예외. 별도 DLL(TK*.dll)로 있어 교체할 수 있다", "LGPL-2.1"),
    (r"^ifcopenshell/boost_[a-z_]+.*\.dll$", "Boost (ifcopenshell 휠에 동봉)", "BSL-1.0", "Boost Software License", None),
    (r"^numpy\.libs/libscipy_openblas64_.*\.dll$", "OpenBLAS (numpy 휠에 동봉)", "BSD-3-Clause",
     "numpy 휠에 동봉. 전문은 numpy 항목의 LICENSE.txt 에 있다", None),
    (r"^numpy\.libs/msvcp140.*\.dll$", "Microsoft Visual C++ 런타임 (numpy 휠에 동봉)", "LicenseRef-MS-Redistributable", "재배포 가능 파일", None),
    (r"^shapely\.libs/geos.*\.dll$", "GEOS (shapely 휠에 동봉)", "LGPL-2.1-only",
     "LGPL-2.1. 별도 DLL 이라 교체할 수 있다(절차는 LIBRARY-REPLACEMENT.md)", "geos"),
    (r"^shapely\.libs/msvcp140.*\.dll$", "Microsoft Visual C++ 런타임 (shapely 휠에 동봉)", "LicenseRef-MS-Redistributable", "재배포 가능 파일", None),
]

# 배포용 고지에 넣지 않는 내부 메모 (결과 JSON 의 internal_note 에만 남는다)
INTERNAL_NOTES = {
    "PyInstaller 부트로더": "GPL 부트로더 + 예외절. 예외절 적용 범위는 법무 확인 대상",
    "Open CASCADE Technology (OCCT, ifcopenshell 휠에 동봉)": "ADR-0003 의 휠 고지 항목. 버전은 ifcopenshell 휠 빌드 기준이라 문자열로는 확인되지 않았다",
    "Microsoft Visual C++ 런타임": "재배포 조항 준수 여부는 법무 확인 대상",
    "Microsoft Universal CRT": "재배포 조항 준수 여부는 법무 확인 대상",
}
# 라이선스 전문이 다른 구성요소의 파일에 이미 있는 경우 (구성요소 이름 -> 그 구성요소의 소유 배포 이름)
TEXT_IN = {"OpenBLAS (numpy 휠에 동봉)": "numpy", "GCC 런타임 라이브러리 (libgfortran, OpenBLAS 에 정적 링크)": "numpy"}
# 소스·상류 주소 (python 배포는 메타데이터에서 읽고, 네이티브는 여기서)
SOURCE_URLS = {
    "Open CASCADE Technology (OCCT, ifcopenshell 휠에 동봉)": "https://github.com/Open-Cascade-SAS/OCCT",
    "GEOS (shapely 휠에 동봉)": "https://github.com/libgeos/geos",
    "PyInstaller 부트로더": "https://github.com/pyinstaller/pyinstaller",
    "ifcopenshell": "https://github.com/IfcOpenShell/IfcOpenShell",
    "OpenSSL 3.x": "https://www.openssl.org", "libffi": "https://github.com/libffi/libffi", "CPython 3.12 런타임": "https://www.python.org",
    "OpenBLAS (numpy 휠에 동봉)": "https://github.com/OpenMathLib/OpenBLAS", "Boost (ifcopenshell 휠에 동봉)": "https://www.boost.org",
}

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
    "LGPL-2.1": ("shapely 휠 LICENSE_GEOS 안의 LGPL-2.1 표준 전문(OCCT 예외 문구는 포함하지 않는다)",
                 [("site", "shapely-2.2.0.dist-info/licenses/LICENSE_GEOS#GNU LESSER GENERAL PUBLIC LICENSE")]),
    "MPL-2.0": ("이 저장소 LICENSE 의 MPL-2.0 전문(표준 전문, 고유 저작권 줄 없음)", [("abs", str(S7.parent.parent / "LICENSE"))]),
    "Apache-2.0": ("cryptography 배포의 Apache-2.0 전문(표준 전문)", [("site", "cryptography-50.0.2.dist-info/licenses/LICENSE.APACHE")]),
}
# 정적 링크 구성요소(native_libs.STATIC_LIBS id) -> 일반 전문 키: 표준 전문이 같은 라이선스(MPL-2.0, Apache-2.0)만 붙인다
STATIC_REFKEY = {"eigen": "MPL-2.0", "rocksdb": "Apache-2.0"}
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
        rel, _, marker = rel.partition("#")   # '#표지'가 있으면 그 표지부터 끝까지만 쓴다(파일 머리의 다른 안내 제외)
        if kind == "abs":
            p = Path(rel)
        elif kind == "site":
            p = Path(any_dist.locate_file(rel))
        else:
            p = Path(sys.base_prefix) / rel
        if p.is_file():
            t = read_text(p)
            if marker and marker in t:
                t = t[t.index(marker):]
            return desc, t
    return None


def dist_license(d: metadata.Distribution) -> tuple[str, str]:
    md = d.metadata
    name = norm_name(md["Name"])
    if name in DIST_OVERRIDES:
        return DIST_OVERRIDES[name][0], "override: " + DIST_OVERRIDES[name][1]
    expr = md.get("License-Expression")
    if expr:
        return expr, "metadata License-Expression"
    # 분류자가 여러 개면 AND 로 묶는다(OR 로 묶으면 GPL 이 가려진다). 이중 라이선스는 DIST_OVERRIDES 로 명시한다
    expr2, how2 = licensing.from_classifiers([c for c in md.get_all("Classifier") or [] if c.startswith("License ::")])
    if expr2:
        return expr2, how2
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


def dist_url(d: metadata.Distribution) -> str:
    """배포 메타데이터에서 소스·홈 주소를 읽는다 (고지의 '소스 위치' 안내용)."""
    md = d.metadata
    urls = {}
    for pu in md.get_all("Project-URL") or []:
        k, _, v = pu.partition(",")
        urls[k.strip().lower()] = v.strip()
    for k in ("source", "source code", "repository", "homepage", "home"):
        if urls.get(k):
            return urls[k]
    return md.get("Home-page") or ""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject", action="append", default=[], help="이름=SPDX식: 가짜 구성요소를 넣어 금지·미확인 검사 자체를 시험")
    ap.add_argument("--lenient", action="store_true", help="unknown·귀속 불명 파일이 있어도 통과(탐색용). forbid 는 이 옵션으로도 실패한다")
    ap.add_argument("--no-rust", action="store_true", help="Rust 크레이트 고지 생성을 건너뛴다(cargo 가 없을 때)")
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
                                   "kind": kind, "evidence": [], "license_files": [], "public_note": "", "url": "", "internal_note": ""})
        if version and not c["version"]:
            c["version"] = version
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
            c["url"] = dist_url(d)
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
            c = comp("native-" + cname, cname, "", expr, "파일 이름 패턴 표(수동 귀속)", "native")
            c["evidence"].append(rel)
            c["refkey"] = refkey
            c["public_note"] = c["public_note"] or note
            continue
        if owner:
            d = dists.get(owner)
            if d is not None:
                expr, how = dist_license(d)
                c = comp(owner, d.metadata["Name"], d.version, expr, how, "python-package")
                c["evidence"].append(rel)
                c["url"] = c["url"] or dist_url(d)
                continue
        if rel.endswith((".py", ".pyc")):
            continue
        unattributed.append(rel)

    # 2b) 네이티브 파일 내용의 정적 링크 흔적 -> 구성요소 (이름표에 없는 RocksDB·PROJ·Eigen 등을 증거로 올린다)
    scan = native_libs.scan(FROZEN_DIR, rel_base=INTERNAL)
    for lid, cname, expr, _needles, note, url in native_libs.STATIC_LIBS:
        files = scan["static"].get(lid)
        if not files:
            continue
        ver = ", ".join(scan["versions"].get(lid, []))
        c = comp("native-" + cname, cname, ver, expr, "정적 링크 흔적 탐지(native_libs.py)", "native")
        c["evidence"] += [x for x in files if x not in c["evidence"]]
        c["public_note"] = c["public_note"] or note
        c["url"] = c["url"] or url
        c["refkey"] = c.get("refkey") or STATIC_REFKEY.get(lid)
    for key, c in comps.items():
        if c["kind"] == "native":
            c["url"] = c["url"] or SOURCE_URLS.get(c["name"], "")
            c["internal_note"] = INTERNAL_NOTES.get(c["name"], "")
            if c["name"] in TEXT_IN:
                c["text_in"] = TEXT_IN[c["name"]]
    for k in ("ifcopenshell",):
        if k in comps:
            comps[k]["url"] = SOURCE_URLS[k]
    if "native-GEOS (shapely 휠에 동봉)" in comps:
        try:
            import shapely
            comps["native-GEOS (shapely 휠에 동봉)"]["version"] = ".".join(map(str, shapely.geos_version))
        except Exception:   # shapely 를 못 불러와도 고지는 만든다(버전 칸만 빈다)
            pass

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
                    c["license_files"].append((f"[일반 전문] {rk}", got[1]))
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
    rust = None
    if not a.no_rust:
        rust = rust_notices.build()
    forbidden = [c for c in rows if c["verdict"] == "forbid"]
    unknown = [c for c in rows if c["verdict"] == "unknown"]
    weak = [c for c in rows if c["verdict"] == "weak"]
    no_text = [c["name"] for c in rows if c["kind"] in ("python-package",) and not c["license_files"]
               and c["verdict"] != "forbid"]

    # 6) 고지 파일 (배포용 — 내부 메모·판정 라벨을 넣지 않는다)
    src_needed = [c for c in rows if c["verdict"] == "weak" and c["kind"] != "project"
                  and "Redistributable" not in c["license_expression"] and any(t in c["license_expression"] for t in ("GPL", "MPL"))]
    lines = ["THIRD-PARTY NOTICES — Democratization of BIM (DoBIM)", "=" * 72,
             "이 파일은 빌드 과정(tools/gen_notices.py)에서 동결 결과물을 분석해 자동으로 만든다.",
             "DoBIM 코어 자체는 MPL-2.0 이다. 아래는 함께 배포되는 제3자 구성요소와 그 라이선스다.", "",
             "[교체 안내] LGPL 구성요소(ifcopenshell, Open CASCADE, GEOS)는 설치 폴더의 _internal 안에 실행 파일과 분리된 파일로 있다.",
             "사용자는 같은 이름의 호환 빌드로 바꿔 쓸 수 있다. 절차는 설치 폴더의 LIBRARY-REPLACEMENT.md 를 본다.", "",
             "[소스 제공 안내] 소스 공개 의무가 있는 구성요소(LGPL·MPL 등)는 수정 없이 상류 프로젝트의 배포본을 그대로 사용한다.",
             "해당 버전의 소스는 아래 '소스 위치'의 상류 프로젝트에서 받을 수 있다.", ""]
    lines += ["소스 위치", "-" * 72]
    for c in src_needed:
        lines.append(f"- {c['name']} {c['version']}".rstrip() + f" — {c['license_expression']}" + (f"  <{c['url']}>" if c["url"] else ""))
    lines += ["", "요약", "-" * 72]
    for c in rows:
        lines.append(f"- {c['name']} {c['version']}".rstrip() + f" — {c['license_expression']}")
    lines += ["", "=" * 72, "상세 (구성요소별 라이선스 전문)", "=" * 72]
    for c in rows:
        lines += ["", "-" * 72, f"{c['name']} {c['version']}".rstrip(), f"라이선스: {c['license_expression']}"]
        if c["public_note"]:
            lines.append(f"비고: {c['public_note']}")
        if c["url"]:
            lines.append(f"상류: {c['url']}")
        lines.append(f"포함된 파일/모듈 수: {c['evidence_count']}")
        if c.get("text_in"):
            lines.append(f"라이선스 전문: '{c['text_in']}' 구성요소의 라이선스 파일에 포함되어 있다.")
        elif c.get("reference_keys") and not [r for r, _ in c["license_files"] if not r.startswith("[일반 전문]")]:
            lines.append("※ 이 구성요소의 배포물에는 라이선스 전문 파일이 없어 같은 라이선스의 일반 전문을 붙였다. "
                         "구성요소 고유의 저작권 표기는 위 상류 프로젝트의 소스에서 확인한다.")
        elif c["kind"] == "native" and not c["license_files"] and c["license_expression"] not in ("blessing",) \
                and "Redistributable" not in c["license_expression"] and c["url"]:
            lines.append("라이선스 전문은 위 상류 프로젝트의 소스에 있다.")
        for rel, text in c["license_files"]:
            lines += ["", f"### {rel}", text.strip("\n")]
    if rust is not None:
        lines += rust_notices.render(rust)
    text = "\n".join(lines) + "\n"
    if not a.no_write:
        OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
        OUT_TXT.write_text(text, encoding="utf-8", newline="\n")

    missing, reference_only = [], []
    for c in rows:
        real = [r for r, _ in c["license_files"] if not r.startswith("[일반 전문]")]
        if c["kind"] in ("python-package", "native") and not real and c["verdict"] != "forbid" and c["license_expression"] != "blessing" \
                and not c.get("text_in"):
            entry = {"name": c["name"], "version": c["version"], "license_expression": c["license_expression"],
                     "reason": "배포물/동결본에 라이선스 전문이 없다", "upstream": c["url"]}
            if c.get("reference_keys"):
                entry["reason"] += " -> 같은 라이선스의 일반 전문으로 대체(해당 버전 원문과 저작권 줄로 교체 필요)"
                reference_only.append(entry)
            else:
                missing.append(entry)
    ref_notes = sorted({k for c in rows for k in c.get("reference_keys", [])})
    fail_reasons = []
    if forbidden:
        fail_reasons.append("금지 라이선스: " + ", ".join(c["name"] for c in forbidden))
    if not a.lenient:
        if unknown:
            fail_reasons.append("미확인 라이선스: " + ", ".join(f"{c['name']}={c['license_expression']}" for c in unknown))
        if unattributed:
            fail_reasons.append(f"귀속 불명 파일 {len(unattributed)}개: {unattributed[:3]}")
    if scan["banned"]:
        fail_reasons.append("금지 정적 링크 흔적: " + ", ".join(f"{k}({len(v)}개 파일)" for k, v in scan["banned"].items()))
    if rust is not None:
        if rust["forbid"]:
            fail_reasons.append("Rust 크레이트 금지 라이선스: " + ", ".join(rust["forbid"]))
        if rust["unknown"] and not a.lenient:
            fail_reasons.append("Rust 크레이트 미확인 라이선스: " + ", ".join(rust["unknown"]))
    res = {
        "mode": "lenient" if a.lenient else "strict",
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
        "static_link_scan": {"files_scanned": scan["files_scanned"], "found": {k: len(v) for k, v in scan["static"].items()},
                             "versions": scan["versions"], "banned_found": scan["banned"]},
        "rust_crates": None if rust is None else {
            "count": len(rust["crates"]), "forbid": rust["forbid"], "unknown": rust["unknown"], "mpl_2_0_crates": rust["weak"],
            "without_license_file": rust["no_text"], "unique_license_texts": len(rust["texts"])},
        "notices_bytes": len(text.encode("utf-8")),
        "injected": a.inject,
        "fail_reasons": fail_reasons,
    }
    res["pass"] = not fail_reasons
    if not a.no_write and not a.inject:
        write_json(RESULTS / "s7_licenses.json", res)
    brief = {k: res[k] for k in ("mode", "counts", "forbidden", "pass")}
    brief["unknown"] = [u["name"] + "=" + u["license_expression"] for u in res["unknown_or_unclear"]]
    brief["missing_license_text"] = [m["name"] for m in missing]
    brief["reference_text_only"] = [m["name"] for m in reference_only]
    brief["unattributed_files"] = len(unattributed)
    brief["fail_reasons"] = fail_reasons
    if rust is not None:
        brief["rust"] = res["rust_crates"]["count"], len(rust["weak"])
    print(__import__("json").dumps(brief, ensure_ascii=False, indent=1))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
