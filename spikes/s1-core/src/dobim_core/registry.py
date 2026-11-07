# SPDX-License-Identifier: MPL-2.0
"""커맨드 레지스트리. CLI, MCP, 패리티 시험, 문서가 모두 여기서 나온다 (15.1절).

이 모듈과 commands.py 는 ifcopenshell 을 import 하지 않는다. `dobim list`,
MCP tools/list 가 무거운 라이브러리 없이 응답해야 하기 때문이다 (15.10절).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

KINDS = {"query", "mutation", "session", "job"}
RISKS = {"read", "write", "destructive", "code_exec"}


@dataclass(frozen=True)
class Command:
    name: str
    kind: str
    risk: str
    group: str
    summary: str
    args_model: type[BaseModel]
    func: Callable[..., Any]
    examples: tuple[dict, ...]
    idempotent: bool = False
    long_running: bool = False
    since: str = "0.0"
    needs_project: bool = True
    preview: Callable[..., Any] | None = None   # destructive 커맨드의 미리보기 (확인 토큰 1회차)

    def describe(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "risk": self.risk, "group": self.group,
            "summary": self.summary, "idempotent": self.idempotent,
            "long_running": self.long_running, "since": self.since,
            "needs_project": self.needs_project, "has_preview": self.preview is not None,
            "input_schema": self.args_model.model_json_schema(),
            "examples": list(self.examples),
        }

    def brief(self) -> dict:
        return {"name": self.name, "kind": self.kind, "risk": self.risk,
                "group": self.group, "summary": self.summary}


REGISTRY: dict[str, Command] = {}


def command(name: str, *, kind: str, risk: str, group: str, summary: str, args: type[BaseModel],
            examples: list[dict], **kw: Any) -> Callable:
    """커맨드를 등록하는 데코레이터. 등록 시점에 메타데이터를 검사한다."""
    if kind not in KINDS or risk not in RISKS:
        raise ValueError(f"{name}: kind/risk 값 오류")
    if not examples:
        raise ValueError(f"{name}: 예시가 없으면 패리티 시험을 할 수 없다")

    def deco(fn: Callable) -> Callable:
        if name in REGISTRY:
            raise ValueError(f"중복 커맨드: {name}")
        REGISTRY[name] = Command(name=name, kind=kind, risk=risk, group=group, summary=summary,
                                 args_model=args, func=fn, examples=tuple(examples), **kw)
        return fn

    return deco


def load() -> dict[str, Command]:
    """커맨드 모듈을 읽어 레지스트리를 채운다."""
    from dobim_core import commands  # noqa: F401
    return REGISTRY


def search(query: str = "", group: str | None = None, risk: str | None = None) -> list[dict]:
    q = query.lower().split()
    out = []
    for c in sorted(load().values(), key=lambda c: c.name):
        if group and not c.group.startswith(group):
            continue
        if risk and c.risk != risk:
            continue
        hay = f"{c.name} {c.group} {c.summary}".lower()
        if all(t in hay for t in q):
            out.append(c.brief())
    return out
