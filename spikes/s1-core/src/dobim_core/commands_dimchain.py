# SPDX-License-Identifier: MPL-2.0
"""연속(체인)·기준선 연관 치수 (W1 연장, ANN-02).

참조 여러 개를 한 번에 재는 치수 묶음을 만든다. 묶음의 한 칸은 보통 연관 치수(dim, 참조 둘)라서
참조 해석·고아·방향 경고·IFC 왕복(DoBIM_Defs)·annot.rebind·annot.delete 를 그대로 쓴다. 칸마다 params.group(묶음 id = 첫 칸 id)과
params.gk(chain | baseline)를 둔다.

평면 치수: 모든 칸이 같은 방향(along)으로 투영해 잰다. 치수선은 묶음을 만들 때 정한 고정 직선(params.dl 을 지나는 along 방향) 위에 놓인다.
    요소가 움직이면 값과 보조선이 따라가고 치수선 자리는 그대로다(Revit 의 연속 치수와 같다). 선 참조(벽 면·축선, 통심선)는 재는 방향에 수직이어야 한다.
    연속 = 이웃한 참조 사이, 기준선 = 첫 참조에서 각 참조까지(치수선을 spacing 씩 바깥으로 쌓는다).
높이 치수(단면·입면): 같은 at·간격의 높이 치수 칸들이라 치수선이 한 줄에 선다(기준선은 간격을 쌓는다).

한계: 묶음은 칸들의 모음이다. 가운데 참조의 대상을 지우면 그 참조를 쓰는 칸만 고아가 된다(Revit 처럼 이웃 칸을 합치지 않는다).
호 길이 치수는 annot.dim.create measure=arc 로 곡선 벽(W2 ARC-04)의 호 역할(wall:<id>#arc·arc.left·arc.right)을 잰다(묶음에는 넣지 않는다).
"""
from __future__ import annotations

from math import isfinite, sqrt
from types import SimpleNamespace
from typing import Annotated, Literal

from pydantic import Field

from dobim_core import annot as AN
from dobim_core import fixture as F
from dobim_core.commands import (A, Id, Name, Off, Point2, RefStr, _dim_checked, _dim_home, _dir_fp, _fresh_id, _new_annot,
                                 _ref_parts, command)
from dobim_core.errors import DobimError

MAX_REFS = 64   # 한 묶음의 참조 수 상한(칸은 63개)


class DimChain(A):
    view: Id = Field(description="치수를 그릴 뷰. 평면 참조는 평면 뷰, 높이 참조(level·slab·space·wall base/top·door sill/head)는 단면·입면 뷰")
    refs: Annotated[list[RefStr], Field(min_length=2, max_length=MAX_REFS, description="잴 참조를 순서대로(2~64개). 점(벽 끝·문틀·창 개구부 끝·기둥 중심·통심선 끝)과 선(벽 면·축선, 통심선)을 섞을 수 있다")]
    mode: Literal["chain", "baseline"] = Field("chain", description="chain = 이웃한 참조 사이(연속 치수), baseline = 첫 참조에서 각 참조까지(기준선 치수)")
    along: Point2 | None = Field(None, description="평면: 재는 방향 [ux,uy](크기는 무시). 생략하면 첫 선 참조에 수직, 선 참조가 없으면 첫 참조에서 끝 참조 쪽")
    offset: Off = Field(600.0, description="평면: 가장 바깥 참조에서 치수선까지 mm(재는 방향 왼쪽이 +, 음수면 오른쪽 바깥). 높이: 높이 치수의 간격")
    spacing: float = Field(500.0, ge=100.0, le=5000.0, allow_inf_nan=False, description="기준선 치수에서 치수선을 바깥으로 쌓는 간격 mm")
    at: Point2 | None = Field(None, description="높이 치수: 치수선을 그릴 평면 위치 [x,y](생략하면 [0,0])")
    name: Name | None = Field(None, description="칸 이름의 머리(칸 이름 = 머리-1, 머리-2 …). 생략하면 DIM-<id 끝 6자리>")


def _auto_along(ctx, rs, given):
    """재는 방향(단위 벡터)과 치수선의 법선(왼쪽)."""
    if given is not None:
        ux, uy = given
        why = "along 의 크기가 0 이다"
    else:
        line = next((r for r in rs if r[0] == "line"), None)
        a0, a1 = (rs[0][1], rs[0][2]), (rs[-1][1], rs[-1][2])
        if line is not None:   # 선 참조에 수직, 첫 참조에서 끝 참조 쪽을 +
            ux, uy = -line[4], line[3]
            if (a1[0] - a0[0]) * ux + (a1[1] - a0[1]) * uy < 0:
                ux, uy = -ux, -uy
        else:
            ux, uy = a1[0] - a0[0], a1[1] - a0[1]
        why = "첫 참조와 끝 참조가 같은 자리라 재는 방향을 정할 수 없다"
    ln = sqrt(ux * ux + uy * uy)
    if not isfinite(ln) or ln < 1e-6:
        raise DobimError("CONSTRAINT", why, path="/along", hint="along 으로 재는 방향을 주세요")
    return [ux / ln, uy / ln]


@command("annot.dim.chain", kind="mutation", risk="write", group="annot",
         summary="연속(체인)·기준선 연관 치수 묶음 생성. 요소를 옮기면 각 칸의 값과 보조선이 따라간다",
         args=DimChain, since="0.2",
         examples=[{"view": F.V1, "refs": [f"wall:{F.W1}#trim.start", f"door:{F.D1}#jamb.start", f"door:{F.D1}#jamb.end", f"wall:{F.W1}#trim.end"]},
                   {"view": F.V1, "refs": [f"wall:{F.W1}#trim.start", f"door:{F.D1}#center", f"wall:{F.W1}#trim.end"], "mode": "baseline", "offset": -800}])
def annot_dim_chain(ctx, a: DimChain):
    for i, r in enumerate(a.refs):
        _ref_parts(ctx, r, f"/refs/{i}")
    pairs = list(zip(a.refs, a.refs[1:])) if a.mode == "chain" else [(a.refs[0], r) for r in a.refs[1:]]
    for i, (ra, rb) in enumerate(pairs):
        if ra == rb:
            raise DobimError("CONSTRAINT", "같은 참조를 잇달아 잴 수 없다", path=f"/refs/{i + 1}")
    g = ctx.fresh()
    rs = [g.q(AN.ref_resolve, r) for r in a.refs]
    for i, r in enumerate(rs):
        if r[0] == "orphan":
            raise DobimError("CONSTRAINT", f"참조를 풀 수 없다: {r[1]}", path=f"/refs/{i}")
    vertical = rs[0][0] == "z"
    if vertical and a.along is not None:
        raise DobimError("CONSTRAINT", "along 은 평면 치수에만 쓴다", path="/along")
    if not vertical and a.at is not None:
        raise DobimError("CONSTRAINT", "at 은 높이 치수에만 쓴다", path="/at")
    side = 1.0 if a.offset >= 0 else -1.0
    plan = None
    if not vertical:
        if any(r[0] not in ("pt", "line") for r in rs):
            raise DobimError("CONSTRAINT", "연속·기준선 치수는 점·선 참조만 잰다(원·높이 참조는 섞을 수 없다)", path="/refs")
        u = _auto_along(ctx, rs, a.along)
        n = (-u[1], u[0])
        ss = [r[1] * n[0] + r[2] * n[1] for r in rs]
        s_line = (max(ss) if side > 0 else min(ss)) + a.offset   # 가장 바깥 참조에서 offset 만큼 떨어진 치수선
        t0 = rs[0][1] * u[0] + rs[0][2] * u[1]
        plan = (u, n, t0, s_line)
    checked = [_dim_checked(ctx, a.view, ra, rb, a.at if vertical else None, None, None if vertical else plan[0]) for ra, rb in pairs]   # 모두 검사한 뒤 만든다
    out, gid = [], None
    for i, ((ra, rb), (_vt, res_a, res_b, g2)) in enumerate(zip(pairs, checked)):
        step = side * a.spacing * i if a.mode == "baseline" else 0.0
        sa = SimpleNamespace(id=None, view=a.view)
        _fresh_id(ctx, sa, "dim")
        gid = gid or sa.id
        params = {"name": f"{a.name}-{i + 1}" if a.name else f"DIM-{sa.id[-6:]}", "view": a.view, "a": ra, "b": rb,
                  "off": a.offset + step, "home": [0.0, 0.0],
                  "dirs": {"a": _dir_fp(ctx, ra), "b": _dir_fp(ctx, rb)}, "group": gid, "gk": a.mode}
        if vertical:
            params["at"] = list(a.at or [0.0, 0.0])
        else:
            u, n, t0, s_line = plan
            s = s_line + step
            params["along"] = list(u)
            params["dl"] = [round(t0 * u[0] + s * n[0], 3), round(t0 * u[1] + s * n[1], 3)]
        params["home"] = _dim_home(g2, params, res_a, res_b)
        _new_annot(ctx, sa, "dim", params)
        st = AN.measure_along(res_a, res_b, params["along"]) if not vertical else AN.measure(res_a, res_b)
        if st[0] == "ok" and st[1] < 0.5:
            ctx.warn(f"{i + 1}번째 칸의 두 참조가 재는 방향으로 거의 같은 자리다(값 {st[1]:.3f}mm)")
        out.append({"id": sa.id, "a": ra, "b": rb, "value": st[1] if st[0] == "ok" else None})
    res = {"group": gid, "mode": a.mode, "view": a.view, "dims": out}
    if plan is not None:
        res["along"] = [round(v, 6) for v in plan[0]]
    return res
