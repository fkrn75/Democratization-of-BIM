# SPDX-License-Identifier: MPL-2.0
"""프로젝트 매개변수(W1 연장, DATA-03): Revit 의 프로젝트 매개변수처럼 속성셋.속성 하나를 값 형식·묶을 요소 종류(범주)와 함께 정의한다.

param.define : 정의를 만들거나 바꾼다(키 = 속성셋.속성). 값은 요소의 속성셋에 그대로 둔다(pset.set·pset.set_many 로 쓴다).
param.delete : 정의를 지운다(요소에 쓴 값은 남는다).
param.list   : 정의 목록과 묶인 요소 수·값을 채운 수·빈 수·형식이 어긋난 수.
정의가 있으면 pset.set·pset.set_many 가 그 속성을 쓸 때 묶은 종류·형식을 검사한다. 정의는 비 IFC 의도 요소라 DoBIM_Defs 로 IFC 를 왕복하고
실행 취소·병합을 그대로 탄다. 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator

from dobim_core import commands as C
from dobim_core import kinds as K
from dobim_core.commands import RESERVED_PSET, A, PropName, PsetName, _det_id, command, row
from dobim_core.errors import DobimError

SAMPLE_IDS = 10   # param.list 가 보여 주는 빈 요소·어긋난 요소 id 수


def _pid(ctx, pset: str, name: str) -> str | None:
    return next((i for i in sorted(ctx.st.by_kind.get("pparam", ())) if ctx.st.el[i]["params"]["pset"] == pset and ctx.st.el[i]["params"]["name"] == name), None)


class ParamDefine(A):
    pset: PsetName = Field(description="속성셋 이름(예: Pset_Project, 회사_공통)")
    name: PropName
    type: Literal["text", "number", "integer", "bool"] = Field(description="값 형식: text 문자열, number 수, integer 정수, bool 참·거짓")
    kinds: Annotated[list[str], Field(min_length=1, max_length=20, description="묶을 요소 종류(범주): wall·door·slab·space·finst 등 속성셋을 가지는 종류")]
    desc: Annotated[str, Field(max_length=K.PPARAM_DESC_MAX)] = Field("", description="설명")

    @field_validator("kinds")
    @classmethod
    def _kinds(cls, v):
        bad = [k for k in v if k not in K.pparam_kinds()]
        if bad:
            raise ValueError(f"묶을 수 없는 종류: {', '.join(bad)} (가능: {', '.join(K.pparam_kinds())})")
        return sorted(set(v))


def _usage(ctx, p: dict) -> dict:
    """정의 p 에 묶인 요소 수와 값 상태. 외래 요소는 세지 않는다(값을 쓰지 않는다)."""
    st, n, filled, empty, bad = ctx.st, 0, 0, [], []
    for k in p["kinds"]:
        for e in sorted(st.by_kind.get(k, ())):
            if st.el[e]["origin"] != "authored":
                continue
            n += 1
            v = ctx.psets(e).get(p["pset"], {}).get(p["name"])
            if v is None:
                empty.append(e)
            elif not K.pparam_value_ok(p["type"], v):
                bad.append(e)
            else:
                filled += 1
    return {"bound": n, "filled": filled, "empty": len(empty), "mismatched": len(bad),
            "empty_ids": empty[:SAMPLE_IDS], "mismatched_ids": bad[:SAMPLE_IDS]}


@command("param.define", kind="mutation", risk="write", group="model.pset",
         summary="프로젝트 매개변수 정의(속성셋.속성 + 형식 + 묶을 종류)를 만들거나 바꾼다",
         args=ParamDefine, since="0.13",
         examples=[{"pset": "Pset_ProjectCommon", "name": "Zone", "type": "text", "kinds": ["wall", "door"], "desc": "공구 구분"}])
def param_define(ctx, a: ParamDefine):
    if a.pset == RESERVED_PSET:
        raise DobimError("CONSTRAINT", f"{RESERVED_PSET} 는 투영이 소유한 예약 속성셋이다", path="/pset")
    params = {"name": a.name, "pset": a.pset, "type": a.type, "kinds": list(a.kinds), "desc": a.desc}
    pid = _pid(ctx, a.pset, a.name)
    created = pid is None
    if created:
        if len(ctx.st.by_kind.get("pparam", ())) >= K.PPARAMS_MAX:
            raise DobimError("CONSTRAINT", f"프로젝트 매개변수는 {K.PPARAMS_MAX}개까지다", path="/name")
        pid = _det_id("pparam", a.pset, a.name)
        if pid in ctx.st.el:
            raise DobimError("CONSTRAINT", f"이미 있는 id 다: {pid}", path="/name")
        if ctx.external_def(pid):
            ctx.warn("가져온 파일에 이 id 의 프로젝트 매개변수 정의가 되살려지지 않은 채 남아 있다: 새 정의가 그 속성을 덮어쓴다")
        ctx.put_el(pid, row("pparam", "", "project.param", params))
    else:
        ctx.put_el(pid, dict(ctx.row(pid), params=params))
    u = _usage(ctx, params)
    if u["mismatched"]:
        ctx.warn(f"이미 쓴 값 {u['mismatched']}개가 형식 {a.type} 와 맞지 않는다(param.list 의 mismatched_ids)")
    return {"id": pid, "created": created, "bound": u["bound"], "filled": u["filled"], "mismatched": u["mismatched"]}


class ParamKey(A):
    pset: PsetName
    name: PropName


@command("param.delete", kind="mutation", risk="write", group="model.pset",
         summary="프로젝트 매개변수 정의를 지운다(요소에 쓴 값은 남는다)",
         args=ParamKey, since="0.13", examples=[{"pset": "Pset_ProjectCommon", "name": "Zone"}])
def param_delete(ctx, a: ParamKey):
    pid = _pid(ctx, a.pset, a.name)
    if pid is None:
        raise DobimError("NOT_FOUND", f"프로젝트 매개변수가 없다: {a.pset}.{a.name}", path="/name")
    ctx.put_el(pid, None)
    return {"deleted": [pid]}


class ParamList(A):
    limit: int = Field(100, ge=1, le=K.PPARAMS_MAX, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("param.list", kind="query", risk="read", group="model.pset",
         summary="프로젝트 매개변수 목록(묶인 요소 수·채운 수·빈 수·형식 어긋남)",
         args=ParamList, idempotent=True, since="0.13", examples=[{}, {"limit": 5}])
def param_list(ctx, a: ParamList):
    ids = sorted(ctx.st.by_kind.get("pparam", ()))
    after = C._dec_cursor(a.cursor, "id")
    start = 0 if after is None else next((k for k, i in enumerate(ids) if i > after), len(ids))
    window = ids[start:start + a.limit]
    rows = []
    for i in window:
        p = ctx.st.el[i]["params"]
        rows.append({"id": i, "pset": p["pset"], "name": p["name"], "type": p["type"], "kinds": list(p["kinds"]), "desc": p["desc"], **_usage(ctx, p)})
    items, cut = C._fit(rows, cursor_of=lambda k: C._enc_cursor(rows[k]["id"]))
    more = start + len(items) < len(ids)
    return {"items": items, "page": C._page(C._enc_cursor(items[-1]["id"]) if more else None, len(ids), cut)}
