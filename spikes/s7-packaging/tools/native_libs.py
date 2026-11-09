# SPDX-License-Identifier: MPL-2.0
"""동결본의 네이티브 파일(.dll .pyd .exe)에서 *정적으로 링크된* 라이브러리의 흔적을 찾는 공용 모듈.

왜 필요한가: 휠에 묻어 온 DLL 은 어떤 라이브러리를 안에 정적으로 품고 있는지 메타데이터에 없다.
파일 이름 표(gen_notices.NATIVE_RULES)만으로는 ifcopenshell_geometry_kernel_cgal.dll 안의 GMP/MPFR(LGPL)이나
svgfill 안의 libxml2 같은 것이 고지에서 빠진다(독립 검수가 발견한 결함). 그래서 파일 *내용*의 문자열 흔적으로 판단한다.

세 가지 목록:
  STATIC_LIBS  흔적이 나오면 '고지 대상 구성요소'로 올린다 (gen_notices 가 사용). 이름·라이선스·상류 주소를 여기서 한 곳에 둔다.
  BANNED_SIGS  이 동결본에 있어서는 안 되는 흔적(제외했거나, 고지 표에 없는 라이브러리, GPL 문구). 하나라도 나오면 audit_native 가 실패한다.
  KNOWN_DEPS   빌드 경로 흔적(_deps/<이름>/)으로 드러나는 FetchContent 의존성 중 허용하는 이름. 그 밖의 이름은 실패한다(미확인 라이브러리).
탐지는 바이트열 리터럴 포함 검사(`in`)라 빠르고 결정적이다. 한계: 문자열이 제거된(strip) 바이너리나 흔적 없는 헤더 전용 라이브러리는 못 잡는다
-> 헤더 라이브러리는 이름 장식(@Eigen@@, @boost@@ 등)과 정적 라이브러리는 고유 오류 문자열로 잡았고, 목록에 없는 새 흔적은 사람이 보강해야 한다.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

NATIVE_SUFFIX = (".dll", ".pyd", ".exe")

# (id, 구성요소 이름, SPDX 식, 흔적(하나라도 있으면 해당), 공개 비고, 상류 주소)
# 이름은 gen_notices.NATIVE_RULES 의 구성요소 이름과 같으면 하나로 합쳐진다.
STATIC_LIBS = [
    ("rocksdb", "RocksDB (ifcopenshell 에 정적 링크)", "Apache-2.0 OR GPL-2.0-only",
     [b"rocksdb.FixedPrefix", b"rocksdb.CappedPrefix"],
     "RocksDB 는 Apache-2.0 과 GPL-2.0 중 선택하는 이중 라이선스이며 Apache-2.0 으로 사용한다", "https://github.com/facebook/rocksdb"),
    ("proj", "PROJ (ifcopenshell_geometry_glb.dll 에 정적 링크)", "MIT",
     [b"proj_create_geographic_crs", b"proj_nlohmann"], "좌표계 변환 라이브러리", "https://github.com/OSGeo/PROJ"),
    ("sqlite", "SQLite", "blessing", [b"SQLite format 3\x00"], "퍼블릭 도메인(저작권 포기). 고지 의무 없음", "https://sqlite.org"),
    ("nlohmann", "nlohmann/json (헤더 라이브러리, 정적 포함)", "MIT", [b"nlohmann@@"], "JSON 헤더 라이브러리",
     "https://github.com/nlohmann/json"),
    ("eigen", "Eigen (헤더 라이브러리, ifcopenshell 에 정적 포함)", "MPL-2.0", [b"@Eigen@@"],
     "선형대수 헤더 라이브러리. MPL-2.0 은 파일 단위 카피레프트이며 소스는 상류에서 받을 수 있다", "https://gitlab.com/libeigen/eigen"),
    ("boost", "Boost (ifcopenshell 휠에 동봉)", "BSL-1.0", [b"@boost@@"], "Boost Software License", "https://www.boost.org"),
    ("libgfortran", "GCC 런타임 라이브러리 (libgfortran, OpenBLAS 에 정적 링크)", "GPL-3.0-or-later WITH GCC-exception-3.1",
     [b"libgfortran/runtime", b"libgfortran/io"],
     "GCC 런타임 라이브러리 예외가 적용된다(컴파일러가 만든 결과물에 런타임을 링크한 경우 독점·다른 라이선스 배포를 허용). 전문은 numpy 항목의 LICENSE.txt 에 있다",
     "https://gcc.gnu.org/git/?p=gcc.git;a=tree;f=libgfortran"),
    # CPython 배포본이 품은 라이브러리 (python312.dll, pyd)
    ("expat", "Expat (CPython pyexpat 에 정적 링크)", "MIT", [b"expat_CAPI"], "XML 파서", "https://libexpat.github.io"),
    ("mpdecimal", "mpdecimal (CPython _decimal 에 정적 링크)", "BSD-2-Clause", [b"dec_mpd_"], "십진 연산 라이브러리",
     "https://www.bytereef.org/mpdecimal"),
    ("zlib", "zlib (CPython 에 정적 링크)", "Zlib", [b"zlib.error"], "압축 라이브러리", "https://zlib.net"),
    ("bzip2", "bzip2 (CPython _bz2 에 정적 링크)", "bzip2-1.0.6", [b"libbzip2"], "압축 라이브러리", "https://sourceware.org/bzip2"),
    ("liblzma", "liblzma / XZ Utils (CPython _lzma 에 정적 링크)", "LicenseRef-public-domain", [b"liblzma"],
     "XZ Utils 의 liblzma 는 퍼블릭 도메인(0BSD)", "https://tukaani.org/xz"),
]

# 이 동결본에서는 나오면 안 되는 흔적. (id, 설명, 흔적)
BANNED_SIGS = [
    ("gmp", "GMP/MPIR (LGPL 또는 GPL, 고지 표에 없다)",
     [b"GNU MP assertion failed", b"GNU MP: Cannot allocate", b"__gmpz_", b"__gmpn_", b"__gmpq_", b"MPIR: "]),
    ("mpfr", "MPFR (LGPL)", [b"_mpfr_exp", b"mpfr_set_prec", b"mpfr_init2", b"MPFR_"]),
    ("cgal", "CGAL (패키지별 LGPL 또는 GPL, GPL 링크 여부 미확인)", [b"CGAL::", b"@CGAL@@", b"CGAL_"]),
    ("libxml2", "libxml2 (MIT, 고지 표에 없는 정적 링크)", [b"xmlParsePI", b"xmlParserMaxDepth", b"libxml2"]),
    ("gpl_text", "GPL 문구 (LGPL 은 해당 없음)",
     [b"GNU General Public License", b"GNU Affero General Public", b"GPL-2.0", b"GPL-3.0", b"GPLv2", b"GPLv3", b"AGPL-"]),
]

# 빌드 경로 흔적 _deps/<이름>[-버전]/ 의 허용 이름 (FetchContent 로 받은 의존성). 키는 '-버전'을 뗀 이름
KNOWN_DEPS = {"occt_git": "Open CASCADE Technology (OCCT)", "rocksdb": "RocksDB", "boost": "Boost"}

# 라이브러리 버전을 파일 안 경로 흔적에서 읽는 정규식 (id -> 바이트 정규식, 첫 그룹이 버전)
VERSION_HINTS = {"rocksdb": rb"_deps[\\/]rocksdb-([0-9][0-9.]*)", "boost": rb"_deps[\\/]boost-([0-9][0-9.]*)",
                 "proj": rb"proj-([0-9]+\.[0-9]+\.[0-9]+)"}

# 동결 폴더에 빠져 있으면 안 되는 제외 목록의 파일 이름 (freeze/dobim.spec 의 EXCLUDE_NATIVE 와 같아야 한다)
EXCLUDED_FILES = {
    "ifcopenshell_geometry_kernel_cgal.dll", "ifcopenshell_geometry_kernel_cgalsimple.dll", "ifcopenshell_geometry_svgfill.dll",
    "manifold.dll", "ifcopenshell_geometry_kernel_manifold.dll",
}

_DEPS_RX = re.compile(rb"_deps[\\/]([A-Za-z0-9_\-.]+)[\\/]")
# 빌드 루트 흔적: 'D:/a/IfcOpenShell...' 처럼 소스 경로가 박힌 접두(참고 정보용)
_BUILDROOT_RX = re.compile(rb"[A-Za-z]:[\\/](?:a|b|w|src|build|vcpkg)[\\/][A-Za-z0-9_.\-]{1,40}")


def native_files(root: Path) -> list[Path]:
    return sorted(p for p in Path(root).rglob("*") if p.is_file() and p.suffix.lower() in NATIVE_SUFFIX)


def _hits(data: bytes, needles: list[bytes]) -> bool:
    return any(n in data for n in needles)


def scan(root: Path, rel_base: Path | None = None) -> dict:
    """root 아래 네이티브 파일을 훑어 흔적을 모은다. 반환: 정적 라이브러리/금지/_deps/빌드루트/파일 수.

    static: id -> 파일 상대경로 집합, versions: id -> 버전 문자열 목록,  banned: id -> 파일 집합,  deps: 이름 -> 파일 집합,  buildroots: 접두 -> 파일 수
    """
    root = Path(root).resolve()
    base = Path(rel_base).resolve() if rel_base else root
    static = {i: set() for i, *_ in STATIC_LIBS}
    versions: dict[str, set] = {}
    banned = {i: set() for i, *_ in BANNED_SIGS}
    deps: dict[str, set] = {}
    buildroots: dict[str, int] = {}
    files = native_files(root)
    for p in files:
        data = p.read_bytes()
        rel = (p.relative_to(base) if base in p.parents else p.relative_to(root)).as_posix()
        for lid, _name, _expr, needles, _note, _url in STATIC_LIBS:
            if _hits(data, needles):
                static[lid].add(rel)
                vr = VERSION_HINTS.get(lid)
                if vr:
                    m = re.search(vr, data)
                    if m:
                        versions.setdefault(lid, set()).add(m.group(1).decode("ascii", "ignore").rstrip("."))
        for bid, _why, needles in BANNED_SIGS:
            if _hits(data, needles):
                banned[bid].add(rel)
        for m in _DEPS_RX.finditer(data):
            deps.setdefault(m.group(1).decode("ascii", "ignore"), set()).add(rel)
        for m in _BUILDROOT_RX.finditer(data):
            k = m.group(0).decode("ascii", "ignore").replace("\\", "/")
            buildroots[k] = buildroots.get(k, 0) + 1
    return {"files_scanned": len(files),
            "static": {k: sorted(v) for k, v in static.items() if v},
            "banned": {k: sorted(v) for k, v in banned.items() if v},
            "deps": {k: sorted(v) for k, v in deps.items()},
            "versions": {k: sorted(v) for k, v in versions.items()},
            "buildroots": dict(sorted(buildroots.items(), key=lambda kv: -kv[1])[:12])}


def pe_imports(path: Path) -> set[str]:
    """PE 파일이 (지연 포함) import 하는 DLL 이름(소문자). pefile 이 없으면 빈 집합."""
    import pefile
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                           pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
    names = set()
    for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
        for e in getattr(pe, attr, []):
            names.add(e.dll.decode("ascii", "ignore").lower())
    pe.close()
    return names


def dangling_imports(root: Path) -> dict[str, list[str]]:
    """동결 폴더 안 파일이 import 하지만 폴더에도 없고 Windows 시스템 폴더에도 없는 DLL (제외로 끊긴 의존 포함)."""
    root = Path(root)
    shipped = {p.name.lower() for p in native_files(root)}
    sysdir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    out: dict[str, list[str]] = {}
    for p in native_files(root):
        try:
            imps = pe_imports(p)
        except Exception:   # 손상·비 PE 는 건너뛴다(파싱 실패는 별도 신호가 아님)
            continue
        for n in imps:
            if n in shipped or n.startswith("api-ms-win-") or n.startswith("ext-ms-win-"):
                continue
            if (sysdir / n).exists():
                continue
            out.setdefault(n, []).append(p.relative_to(root).as_posix())
    return {k: sorted(v) for k, v in sorted(out.items())}
