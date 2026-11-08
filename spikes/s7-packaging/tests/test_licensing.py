# SPDX-License-Identifier: MPL-2.0
"""라이선스 판정기(licensing.py)·고지 생성기(gen_notices.py)·네이티브 점검(audit_native.py)의 금지 검사 시험.

금지 검사가 '조용히 항상 통과'하지 않는다는 음성 대조가 핵심이다:
GPL 주입은 실패해야 하고, 모르는 라이선스(unknown)도 기본(strict)에서는 실패해야 하며,
금지 흔적이 든 DLL 을 만들어 넣으면 점검이 잡아야 한다.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
import audit_native  # noqa: E402
import licensing  # noqa: E402
import native_libs  # noqa: E402
import rust_notices  # noqa: E402


@pytest.mark.parametrize("expr,verdict", [
    ("MIT", "ok"),
    ("Apache-2.0 OR BSD-3-Clause", "ok"),
    ("BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0", "ok"),
    ("LGPL-3.0-or-later", "weak"),
    ("MPL-2.0", "weak"),
    ("LGPL-2.1-only WITH OCCT-exception-1.0", "weak"),
    ("GPL-3.0-only", "forbid"),
    ("GPL-2.0-or-later", "forbid"),
    ("AGPL-3.0-or-later", "forbid"),
    ("MIT AND GPL-3.0-only", "forbid"),          # AND 는 가장 위험한 쪽
    ("MIT OR GPL-3.0-only", "ok"),               # OR 는 고를 수 있는 쪽
    ("GPL-3.0-or-later OR LGPL-3.0-only", "weak"),
    ("GPL-2.0-or-later WITH Bootloader-exception", "weak"),           # 허용 목록의 예외절
    ("GPL-3.0-or-later WITH Classpath-exception-2.0", "forbid"),      # 허용 목록에 없는 예외절은 풀리지 않는다
    ("(MIT OR Apache-2.0) AND BSD-3-Clause", "ok"),
    ("Some-Weird-License", "unknown"),
    ("", "unknown"),
    ("unknown", "unknown"),
    # 엄격 모드 보강: SPDX 가 아닌 GPL 표기·자유 문장도 unknown 으로 새지 않고 forbid
    ("GPLv3", "forbid"),
    ("AGPLv3", "forbid"),
    ("GNU General Public License v3 (GPLv3)", "forbid"),
    ("GNU GPL", "forbid"),
    ("MIT AND GNU GPL", "forbid"),
    ("BSD 3-Clause; see GPL docs", "forbid"),
    # LGPL·Lesser 를 GPL 로 오인하지 않는다 (자유 문장은 해석 불가 -> unknown, forbid 아님)
    ("GNU Lesser General Public License v3 (LGPLv3)", "unknown"),
    ("LGPL-2.1", "weak"),
    ("Unicode-3.0", "ok"),
])
def test_evaluate(expr, verdict):
    assert licensing.evaluate(expr)[0] == verdict


def test_lgpl_is_not_mistaken_for_gpl():
    assert licensing.classify_id("LGPL-2.1-or-later") == "weak"
    assert licensing.classify_id("GPL-2.1-or-later") == "forbid"
    assert not licensing.text_is_gpl("GNU Lesser General Public License v2.1 or later (LGPLv2+)")
    assert licensing.text_is_gpl("GNU General Public License v2")


def test_multiple_classifiers_do_not_hide_gpl():
    """분류자가 여러 개일 때 OR 로 묶으면 GPL 이 가려진다 -> AND 로 묶어 forbid 가 드러나야 한다."""
    expr, _ = licensing.from_classifiers(["License :: OSI Approved :: MIT License",
                                          "License :: OSI Approved :: GNU General Public License v3 (GPLv3)"])
    assert licensing.evaluate(expr)[0] == "forbid"
    expr, _ = licensing.from_classifiers(["License :: OSI Approved :: MIT License", "License :: OSI Approved :: BSD License"])
    assert licensing.evaluate(expr)[0] == "ok"
    # 모르는 분류자는 조용히 버려지지 않는다
    expr, _ = licensing.from_classifiers(["License :: OSI Approved :: MIT License", "License :: Totally Unknown :: Weird License"])
    assert licensing.evaluate(expr)[0] == "unknown"
    # 라이선스가 아닌 일반 분류자는 무시
    expr, _ = licensing.from_classifiers(["License :: OSI Approved", "License :: OSI Approved :: MIT License"])
    assert expr == "MIT"


def test_rust_license_normalization():
    assert rust_notices.normalize_expr("MIT/Apache-2.0") == "MIT OR Apache-2.0"
    assert rust_notices.normalize_expr("Apache-2.0 / MIT") == "Apache-2.0 OR MIT"
    assert licensing.evaluate(rust_notices.normalize_expr("Unlicense/MIT"))[0] == "ok"


# ---- 네이티브 점검 (합성 파일로 음성 대조) ----
def _tree(tmp_path: Path, files: dict[str, bytes]) -> Path:
    root = tmp_path / "dobim"
    for rel, data in files.items():
        p = root / "_internal" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def test_audit_passes_on_clean_tree(tmp_path):
    root = _tree(tmp_path, {"ifcopenshell/ok.dll": b"MZ\x00plain library with no special strings"})
    res = audit_native.audit(root, None)
    assert res["pass"], res["problems"]


@pytest.mark.parametrize("name,payload,tag", [
    ("a.dll", b"xx GNU MP assertion failed: %s yy", "gmp"),
    ("b.dll", b"xx _mpfr_exp yy", "mpfr"),
    ("c.dll", b"xx Undecidable conversion of CGAL::Uncertain<T> yy", "cgal"),
    ("d.dll", b"xx xmlParsePI : no target name yy", "libxml2"),
    ("e.dll", b"This program is licensed under the GNU General Public License version 3", "gpl_text"),
    ("f.pyd", b"SPDX-License-Identifier: GPL-3.0-or-later", "gpl_text"),
])
def test_audit_catches_banned_traces(tmp_path, name, payload, tag):
    res = audit_native.audit(_tree(tmp_path, {"ifcopenshell/" + name: payload}), None)
    assert not res["pass"] and tag in res["banned_found"], res["problems"]


def test_audit_does_not_flag_lgpl_text(tmp_path):
    res = audit_native.audit(_tree(tmp_path, {"x.dll": b"under the terms of the GNU Lesser General Public License version 2.1"}), None)
    assert res["pass"], res["problems"]


def test_audit_catches_excluded_dll_and_unknown_build_dep(tmp_path):
    res = audit_native.audit(_tree(tmp_path, {"ifcopenshell/manifold.dll": b"MZ"}), None)
    assert not res["pass"] and res["present_excluded_files"] == ["manifold.dll"]
    res = audit_native.audit(_tree(tmp_path / "t2", {"y.dll": b"src D:/a/_deps/mystery-lib-1.2/foo.cpp"}), None)
    assert not res["pass"] and "mystery-lib-1.2" in res["build_deps_paths"]
    res = audit_native.audit(_tree(tmp_path / "t3", {"y.dll": b"src D:/a/_deps/rocksdb-9.1.0/foo.cpp rocksdb.FixedPrefix"}), None)
    assert res["pass"] and res["static_lib_versions"]["rocksdb"] == ["9.1.0"]


def test_audit_requires_static_libs_in_notices(tmp_path):
    """정적 링크가 확인됐는데 고지 JSON 에 그 구성요소가 없으면 실패한다 (CGAL/GMP 누락 사고의 재발 방지)."""
    root = _tree(tmp_path, {"y.dll": b"rocksdb.FixedPrefix"})
    nj = tmp_path / "lic.json"
    nj.write_text(json.dumps({"components": [{"name": "something else"}]}), encoding="utf-8")
    res = audit_native.audit(root, nj)
    assert not res["pass"] and res["static_libs_missing_in_notices"]
    name = next(n for i, n, *_ in native_libs.STATIC_LIBS if i == "rocksdb")
    nj.write_text(json.dumps({"components": [{"name": name}]}), encoding="utf-8")
    assert audit_native.audit(root, nj)["pass"]


def test_audit_catches_dangling_import(tmp_path):
    """제외한 DLL 을 import 하는 플러그인이 남으면 잡는다 (PE 를 직접 만들 수 없으니 실제 동결본이 있을 때만)."""
    frozen = TOOLS.parent / "build" / "dist" / "dobim"
    if not (frozen / "_internal").exists():
        pytest.skip("동결본이 없다")
    assert native_libs.dangling_imports(frozen) == {}


# ---- 고지 생성기 (동결본이 있을 때) ----
def _run(*extra):
    return subprocess.run([sys.executable, str(TOOLS / "gen_notices.py"), "--no-write", *extra],
                          capture_output=True, text=True, encoding="utf-8")


needs_frozen = pytest.mark.skipif(not (TOOLS.parent / "build" / "dist" / "dobim" / "_internal").exists(), reason="동결본이 없다")


@needs_frozen
def test_generator_clean_passes_and_gpl_injection_fails():
    clean = _run()
    assert clean.returncode == 0, clean.stdout[-800:]
    bad = _run("--no-rust", "--inject", "evil=GPL-3.0-only")
    assert bad.returncode == 1 and "evil" in bad.stdout
    ok = _run("--no-rust", "--inject", "dual=MIT OR GPL-3.0-only")
    assert ok.returncode == 0


@needs_frozen
@pytest.mark.parametrize("expr", ["GPLv3", "GNU General Public License v3", "AGPL-3.0-only", "MIT AND GPL-2.0-only"])
def test_generator_fails_on_gpl_spellings(expr):
    r = _run("--no-rust", "--inject", "evil=" + expr)
    assert r.returncode == 1 and "evil" in r.stdout, r.stdout[-600:]
    # --lenient 도 forbid 는 통과시키지 않는다
    r2 = _run("--no-rust", "--lenient", "--inject", "evil=" + expr)
    assert r2.returncode == 1


@needs_frozen
def test_generator_is_strict_about_unknown_by_default():
    r = _run("--no-rust", "--inject", "mystery=Totally-Custom-License-1.0")
    assert r.returncode == 1 and "mystery" in r.stdout
    assert _run("--no-rust", "--lenient", "--inject", "mystery=Totally-Custom-License-1.0").returncode == 0


@needs_frozen
def test_real_frozen_dir_passes_native_audit():
    res = audit_native.audit(TOOLS.parent / "build" / "dist" / "dobim", TOOLS.parent / "results" / "s7_licenses.json")
    assert res["pass"], res["problems"]
