# SPDX-License-Identifier: MPL-2.0
"""Windows: 설치된 ifcopenshell 의 .pyd/.dll 이 임포트하는 DLL 이름을 PE 임포트 테이블에서 읽는다(순수 파이썬, 외부 도구 없음).
Linux 의 ldd 검사에 대응한다. GUI·그래픽 DLL(user32·gdi32·opengl32 등)이 직접 링크되는지 본다. stdout JSON 한 줄."""
from __future__ import annotations

import importlib.metadata as md
import json
import struct
import sys
from pathlib import Path

GUI_DLL = ("user32.dll", "gdi32.dll", "opengl32.dll", "glu32.dll", "d3d11.dll", "d3d12.dll", "dxgi.dll", "comctl32.dll", "comdlg32.dll",
           "shell32.dll", "winmm.dll", "imm32.dll", "uxtheme.dll", "dwmapi.dll", "libgl.dll", "glfw3.dll", "sdl2.dll")


def imports_of(path: Path) -> list[str]:
    d = path.read_bytes()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", d, opt)[0]
    dd = opt + (96 if magic == 0x10B else 112)
    rva = struct.unpack_from("<I", d, dd + 8)[0]     # 임포트 디렉터리
    secs = []
    sp = opt + optsz
    for i in range(nsec):
        vsz, va, rsz, raw = struct.unpack_from("<IIII", d, sp + 40 * i + 8)
        secs.append((va, max(vsz, rsz), raw))

    def off(r):
        for va, sz, raw in secs:
            if va <= r < va + sz:
                return r - va + raw
        return None
    out = []
    o = off(rva) if rva else None
    while o is not None:
        name_rva = struct.unpack_from("<I", d, o + 12)[0]
        if name_rva == 0:
            break
        no = off(name_rva)
        out.append(d[no:d.index(b"\0", no)].decode("ascii", "replace").lower())
        o += 20
    return out


def main() -> int:
    root = Path(md.distribution("ifcopenshell").locate_file(""))
    libs: dict[str, set] = {}
    for f in list(root.rglob("*.pyd")) + list(root.rglob("*.dll")):
        if "ifcopenshell" not in f.parts and "ifcopenshell.libs" not in f.parts:
            continue
        try:
            for n in imports_of(f):
                libs.setdefault(n, set()).add(f.name)
        except Exception:   # noqa: BLE001
            continue
    flagged = {n: sorted(v)[:3] for n, v in libs.items() if n in GUI_DLL}
    non_system = sorted(n for n in libs if not n.startswith(("api-ms-", "ext-ms-")))
    print(json.dumps({"files_scanned_with_imports": sum(len(v) for v in libs.values()), "dll_names": non_system, "gui_dlls_flagged": flagged},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
