# SPDX-License-Identifier: MPL-2.0
"""프로젝트 템플릿(W1 1-E, DATA-12): project.new 가 새 프로젝트에 미리 채울 커맨드 목록.

템플릿은 JSON 한 벌이다: {"format": "dobim.template/1", "name", "title", "commands": [{"command", "args"}]}.
args 안에서 "$이름" 꼴 문자열(기호)은 프로젝트 UUID 와 기호 이름으로 정한 결정적 id 로 바뀐다. 그래서 같은 템플릿으로 만든
두 프로젝트의 요소 id 는 서로 겹치지 않고, 같은 guid 로 만들면 같은 id 가 나온다. 로그에는 바뀐 id 가 그대로 남아 재생이 같다.
내장 템플릿은 패키지의 templates/*.json 이다. 이 모듈은 ifcopenshell·엔진을 import 하지 않는다(GlobalId 압축은 직접 한다).
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from dobim_core.errors import DobimError

FORMAT = "dobim.template/1"
BUILTIN = ("kr-small",)          # 내장 템플릿 이름(commands 의 Literal 과 같은 집합)
MAX_COMMANDS = 500               # 템플릿 하나의 커맨드 수 상한
MAX_BYTES = 1_000_000            # 템플릿 파일 크기 상한
SYM = re.compile(r"^\$[A-Za-z0-9_]{1,40}$")
_DIR = Path(__file__).resolve().parent / "templates"


def _bad(msg: str, path: str = "/template") -> DobimError:
    return DobimError("SCHEMA_INVALID", msg, path=path)


def parse(text: str, where: str = "/template") -> dict:
    """템플릿 JSON 을 읽어 형식을 확인한다. 커맨드 이름·인자 검사는 호출자(레지스트리)가 한다."""
    try:
        d = json.loads(text)
    except ValueError as ex:
        raise _bad(f"템플릿 JSON 을 읽을 수 없다: {str(ex)[:80]}", where) from None
    if not isinstance(d, dict) or d.get("format") != FORMAT:
        raise _bad(f"템플릿 format 은 {FORMAT} 이어야 한다", where)
    cmds = d.get("commands")
    if not isinstance(cmds, list) or not 1 <= len(cmds) <= MAX_COMMANDS:
        raise _bad(f"템플릿 commands 는 1~{MAX_COMMANDS}개 목록이어야 한다", where)
    for i, c in enumerate(cmds):
        if not isinstance(c, dict) or not isinstance(c.get("command"), str) or not isinstance(c.get("args", {}), dict):
            raise _bad("템플릿 항목은 {command: 문자열, args: 객체} 여야 한다", f"{where}/commands/{i}")
    return d


def load_builtin(name: str) -> dict:
    if name not in BUILTIN:
        raise DobimError("NOT_FOUND", f"없는 내장 템플릿: {name}", path="/template")
    return parse((_DIR / f"{name}.json").read_text(encoding="utf-8"))


def load_file(p: Path) -> dict:
    if p.suffix.lower() != ".json":
        raise DobimError("PATH_DENIED", f"템플릿 파일은 .json 이어야 한다: {p.name}", path="/template_file")
    if p.stat().st_size > MAX_BYTES:
        raise _bad(f"템플릿 파일이 너무 크다(상한 {MAX_BYTES} 바이트)", "/template_file")
    return parse(p.read_text(encoding="utf-8-sig"), "/template_file")


_B64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


def compress(hexstr: str) -> str:
    """128비트 UUID(hex 32자) -> IFC GlobalId 22자(IFC 표준 압축: 앞 1자는 위 2비트, 나머지 21자는 6비트씩). ifcopenshell.guid.compress 와 같은 값."""
    n = int(hexstr, 16)
    out = []
    for _ in range(22):
        out.append(_B64[n & 63])
        n >>= 6
    return "".join(reversed(out))


def resolve(tpl: dict, project_uuid: str, compress=compress) -> list[tuple[str, dict]]:
    """기호("$이름")를 결정적 id 로 바꾼 (커맨드 이름, 인자) 목록. compress: uuid hex -> 22자 GlobalId."""
    base = uuid.UUID(project_uuid)
    ids: dict[str, str] = {}

    def sub(v):
        if isinstance(v, str) and SYM.match(v):
            if v not in ids:
                ids[v] = compress(uuid.uuid5(base, f"template:{v[1:]}").hex)
            return ids[v]
        if isinstance(v, list):
            return [sub(x) for x in v]
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        return v
    return [(c["command"], sub(c.get("args", {}))) for c in tpl["commands"]]
