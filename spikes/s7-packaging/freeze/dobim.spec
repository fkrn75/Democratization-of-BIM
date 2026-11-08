# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: MPL-2.0
# PyInstaller onedir 명세. UPX 금지(DLL 손상·백신 오탐, ADR-0003 의 교체 가능성도 해친다).
# 두 실행 파일(dobim.exe, dobim-mcp.exe)이 하나의 _internal 폴더를 공유한다.
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

HERE = os.path.dirname(os.path.abspath(SPEC))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "s1-core", "src"))

datas, binaries, hidden = [], [], []
# ifcopenshell: api/util 이 하위 모듈을 이름으로 동적 import 하고, 스키마 데이터와 플러그인 DLL(TK*.dll 등)이 필요하다
d, b, h = collect_all("ifcopenshell", include_py_files=False)   # .py 는 아래 module_collection_mode 로 따로 싣는다
datas += d; binaries += b; hidden += h
# mcp.cli 는 typer(선택 의존)가 필요해 import 시 종료하므로 제외한다. 코어는 mcp.server 만 쓴다
hidden += collect_submodules("mcp", filter=lambda n: not n.startswith("mcp.cli"))
hidden += collect_submodules("mcp_types")
datas += collect_data_files("mcp") + collect_data_files("mcp_types")
# ifcopenshell.validate(express_rules=True) 의 규칙 실행기(express/rule_executor.py)가 함수 안에서 `from _pytest import assertion` 을 한다.
# 정적 분석에 안 잡히고, 빠지면 검증이 예외를 삼켜 errors=1 ("No module named _pytest")로 조용히 실패한다 (S0 발견, S7 에서 재확인).
hidden += ["_pytest.assertion", "_pytest.assertion.rewrite", "_pytest.assertion.util", "pluggy", "iniconfig"]
hidden += collect_submodules("dobim_core")
hidden += collect_submodules("pydantic") + ["pydantic_core"]

a_common = dict(pathex=[SRC], binaries=binaries, datas=datas, hiddenimports=hidden, hookspath=[], runtime_hooks=[],
                excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "setuptools", "pkg_resources", "_distutils_hack",
                          "mcp.cli", "typer", "ifcopenshell.simple_spf.test_parser"],
                noarchive=False, optimize=0,
                # LGPL 라이브러리(ifcopenshell)는 PYZ(실행 파일 안 압축 묶음) 밖에 .py 파일로 둔다.
                # PYZ 안에 있으면 사용자가 파일을 바꿔도 PyInstaller 로더가 PYZ 쪽을 먼저 쓰므로 '교체 가능' 의무를 못 채운다(S7 시험으로 확인).
                module_collection_mode={"ifcopenshell": "py"})
a1 = Analysis([os.path.join(HERE, "entry_dobim.py")], **a_common)
a2 = Analysis([os.path.join(HERE, "entry_mcp.py")], **a_common)
MERGE((a1, "dobim", "dobim"), (a2, "dobim-mcp", "dobim-mcp"))

pyz1 = PYZ(a1.pure)
pyz2 = PYZ(a2.pure)
exe1 = EXE(pyz1, a1.scripts, [], exclude_binaries=True, name="dobim", console=True, upx=False)
exe2 = EXE(pyz2, a2.scripts, [], exclude_binaries=True, name="dobim-mcp", console=True, upx=False)
coll = COLLECT(exe1, a1.binaries, a1.datas, exe2, a2.binaries, a2.datas, strip=False, upx=False, name="dobim")
