# SPDX-License-Identifier: MPL-2.0
"""의존성 라이선스 기록과 GPL·AGPL 검사 (ADR-0003 규칙 4).

dobim-core 가 선언한 의존성(런타임)과 개발 의존성의 전이 폐쇄를 설치된 메타데이터에서 읽는다.
LGPL 은 허용(ifcopenshell, 동적 링크 가능한 라이브러리), GPL·AGPL 은 금지.
"""
from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

RUNTIME = ["ifcopenshell", "pydantic", "mcp"]
DEV = ["pytest", "import-linter"]
FORBIDDEN = re.compile(r"(?<!L)GPL|AGPL|GNU (Affero )?General Public", re.I)


def norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def lic_of(dist: metadata.Distribution) -> str:
    md = dist.metadata
    expr = md.get("License-Expression")
    if expr:
        return expr
    lic = md.get("License")
    if lic and len(lic) < 120 and "\n" not in lic:
        return lic
    cls = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    if cls:
        return "; ".join(cls)
    return (lic or "미확인").splitlines()[0][:80]


def closure(roots: list[str], index: dict) -> dict[str, metadata.Distribution]:
    seen, todo = {}, [norm(r) for r in roots]
    while todo:
        n = todo.pop()
        if n in seen or n not in index:
            continue
        d = index[n]
        seen[n] = d
        for r in d.requires or []:
            try:
                req = Requirement(r)
            except Exception:
                continue
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            todo.append(norm(req.name))
    return seen


def scan() -> dict:
    index = {norm(d.metadata["Name"]): d for d in metadata.distributions()}
    out = {}
    for group, roots in (("runtime", RUNTIME), ("dev", DEV)):
        rows = []
        for n, d in sorted(closure(roots, index).items()):
            lic = lic_of(d)
            rows.append({"name": d.metadata["Name"], "version": d.version, "license": lic,
                         "forbidden": bool(FORBIDDEN.search(lic))})
        out[group] = rows
    out["forbidden_found"] = [r["name"] for g in ("runtime", "dev") for r in out[g] if r["forbidden"]]
    out["notes"] = ("ifcopenshell 은 LGPL-3.0 (ADR-0003: 라이브러리 파일을 교체 가능하게 동봉하고 고지). "
                    "라이선스 칸은 패키지 메타데이터 그대로이며 '미확인' 은 메타데이터에 없다는 뜻이다.")
    return out


if __name__ == "__main__":
    res = scan()
    text = json.dumps(res, ensure_ascii=True, indent=2)   # 한글은 \\uXXXX 로 써서 인코딩을 잘못 추측하는 도구에서도 깨지지 않는다
    if len(sys.argv) > 1:
        Path(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)
        with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    print(text)
    sys.exit(1 if res["forbidden_found"] else 0)
