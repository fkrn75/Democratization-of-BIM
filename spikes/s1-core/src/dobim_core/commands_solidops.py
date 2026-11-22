# SPDX-License-Identifier: MPL-2.0
"""솔리드 불리언(W2 여섯 번째 PR, ARC-12·ARC-44): 솔리드에서 다른 솔리드·공극(void) 형상을 빼기, 합치기, 교집합.

- 연산은 대상 솔리드 행의 params["ops"] 목록에 차례로 쌓는다: [{"op": "difference"|"union"|"intersection", "solid": 형상}].
  형상은 대상과 같은 레벨 좌표(z 는 대상 레벨 기준)의 형태 사전(name·category 없음, res·ops 중첩 가능)이다.
  다른 요소를 가리키지 않으므로 도구 요소를 지워도·옮겨도 대상은 그대로이고, 증분 도면 = 전체 재계산이 요소 하나 안에서 닫힌다.
- solid.boolean 은 도구 솔리드 요소(tools)의 현재 형상을 복사해 넣고(다른 레벨이면 레벨 높이 차만큼 옮긴다) 기본으로 도구 요소를 지운다
  (keep=true 면 남긴다). shape 를 주면 요소 없이 그 형상(공극)을 바로 쓴다.
- 형상 계산은 solids.solid_mesh(solidops.boolean_mesh, BSP 다면체 불리언). IFC 는 IfcBooleanResult 사슬(표현 종류 CSG)이다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core.commands import A, Id, SolidGeom, command
from dobim_core.errors import DobimError

BoolOp = Literal["difference", "union", "intersection"]


class SolidShape(SolidGeom):
    """불리언 피연산자(공극) 형상: solid.create 의 형상 필드와 같다(form 필수)."""


class SolidBoolean(A):
    id: Id = Field(description="결과를 담을 대상 솔리드")
    op: BoolOp = Field("difference", description="difference 빼기(공극), union 합치기, intersection 교집합")
    tools: Annotated[list[Id], Field(min_length=1, max_length=16)] | None = Field(None, description="도구 솔리드 요소들(형상을 복사한다). 차례로 같은 연산을 적용")
    shape: SolidShape | None = Field(None, description="도구 대신 바로 쓸 형상(공극 형상): solid.create 의 형태·형상 필드(z 는 대상 레벨 기준)")
    keep: bool = Field(False, description="tools 요소를 지우지 않고 남긴다(기본은 대상에 흡수하고 지운다)")

    @model_validator(mode="after")
    def _one(self):
        if (self.tools is None) == (self.shape is None):
            raise ValueError("tools 와 shape 중 하나만 준다")
        if self.shape is not None and self.shape.form is None:
            raise ValueError("shape.form 이 필요하다")
        return self


def _level_z(ctx, eid) -> float:
    lv = ctx.rels_out("on_level", eid)
    return float(ctx.st.el[lv[0]]["params"]["elevation"]) if lv else 0.0


def _operand_of(ctx, tid: str, target: str) -> dict:
    r = ctx.authored(tid, "solid")
    if tid == target:
        raise DobimError("CONSTRAINT", "대상 자신을 도구로 쓸 수 없다", path="/tools")
    q = {k: v for k, v in r["params"].items() if k not in ("name", "category", "grid")}
    dz = _level_z(ctx, tid) - _level_z(ctx, target)
    return C._solid_moved(q, 0.0, 0.0, dz) if dz else q


@command("solid.boolean", kind="mutation", risk="write", group="model.solid",
         summary="솔리드 불리언: 대상에서 도구 솔리드·공극 형상을 빼거나(difference) 합치거나(union) 교집합(intersection). IFC 는 IfcBooleanResult(CSG)",
         args=SolidBoolean, since="0.19",
         examples=[{"id": F.SO1, "op": "difference", "shape": {"form": "member", "start": [300, -1200, 1200], "end": [300, 200, 1200],
                                                               "section": {"shape": "circle", "width": 200, "depth": 200}, "anchor": "center"}}])
def solid_boolean(ctx, a: SolidBoolean):
    r = ctx.authored(a.id, "solid")
    p = dict(r["params"])
    if a.shape is not None:
        shape = C._solid_checked({"name": "op", "category": "proxy", **C._solid_geom(a.shape, None)})
        operands = [{k: v for k, v in shape.items() if k not in ("name", "category")}]
        tools = []
    else:
        tools = list(dict.fromkeys(a.tools))
        operands = [_operand_of(ctx, t, a.id) for t in tools]
    p["ops"] = list(p.get("ops", ())) + [{"op": a.op, "solid": q} for q in operands]
    p = C._solid_checked(p)
    ctx.put_el(a.id, dict(r, params=p))
    deleted = []
    if not a.keep:
        for t in tools:
            C._delete_one(ctx, t)
            deleted.append(t)
    return {"id": a.id, "ops": len(p["ops"]), "deleted": deleted}


class SolidBooleanRemove(A):
    id: Id
    index: Annotated[int, Field(ge=0, le=63)] | None = Field(None, description="뗄 연산 번호(0부터). 생략하면 모두 뗀다")


@command("solid.boolean.remove", kind="mutation", risk="write", group="model.solid", summary="솔리드의 불리언 연산을 뗀다(번호 하나 또는 모두). 떼어 낸 도구 형상은 요소로 되살리지 않는다",
         args=SolidBooleanRemove, since="0.19", examples=[{"id": F.SO1}])
def solid_boolean_remove(ctx, a: SolidBooleanRemove):
    r = ctx.authored(a.id, "solid")
    p = dict(r["params"])
    ops = list(p.get("ops", ()))
    if not ops:
        raise DobimError("CONSTRAINT", "이 솔리드에는 불리언 연산이 없다", path="/id")
    if a.index is None:
        ops = []
    elif a.index >= len(ops):
        raise DobimError("CONSTRAINT", f"연산 번호는 0~{len(ops) - 1} 이다", path="/index")
    else:
        ops.pop(a.index)
    if ops:
        p["ops"] = ops
    else:
        p.pop("ops", None)
    p = C._solid_checked(p)
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "ops": len(ops)}
