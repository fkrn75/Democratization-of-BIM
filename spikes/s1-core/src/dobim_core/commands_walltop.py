# SPDX-License-Identifier: MPL-2.0
"""벽 상단 붙이기 커맨드(21단계, 설계: phase1-drawings/elements-design.md 23.2).

wall.attach_top  벽 축선 위에서 대상(경사 지붕·솔리드(경사 판 등)·슬래브)의 아랫면(또는 윗면) 높이를 재서 벽 상단 윤곽(top_profile)을 채운다.
wall.detach_top  상단 윤곽을 뗀다(상단이 height 로 수평).
붙이기는 그 순간의 대상 형상을 윤곽 값으로 옮겨 적는다(스냅숏). 대상이 바뀌어도 벽은 따라가지 않는다: 다시 붙이면 새 형상을 따른다(설계 메모 23.2 의 의존 판단).
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from dobim_core import fixture as F
from dobim_core import queries as Q
from dobim_core import walltop as WT
from dobim_core.commands import A, Id, _hosted_under_top, _wall_checked, command
from dobim_core.errors import DobimError

ATTACH_KINDS = ("roof", "solid", "slab")   # 붙일 수 있는 대상 종류
ATTACH_TARGETS_MAX = 16


class WallAttachTop(A):
    id: Id
    targets: Annotated[list[Id], Field(min_length=1, max_length=ATTACH_TARGETS_MAX, description="붙일 대상 id 목록: 경사 지붕(roof)·솔리드(경사 판 plate 등)·슬래브")]
    face: Literal["bottom", "top"] = Field("bottom", description="bottom 대상의 아랫면(기본, 상용 도구의 지붕 아래 붙이기), top 윗면")


@command("wall.attach_top", kind="mutation", risk="write", group="model.wall",
         summary="벽 상단을 지붕·경사 판·슬래브에 붙인다: 축선 위에서 대상 면의 높이로 상단 윤곽(top_profile)을 채운다(대상 밖 구간은 height 유지, 붙인 순간의 값)",
         args=WallAttachTop, since="0.7", examples=[{"id": F.W2, "targets": [F.RF1]}])
def wall_attach_top(ctx, a: WallAttachTop):
    r = ctx.authored(a.id, "wall", what="상단 붙이기")
    p = dict(r["params"])
    wall_elev = ctx.g.q(Q.level_info, ctx.rels_out("on_level", a.id)[0])[1]
    faces = []
    for i, t in enumerate(dict.fromkeys(a.targets)):
        tr = ctx.row(t)
        if tr["kind"] not in ATTACH_KINDS:
            raise DobimError("CONSTRAINT", f"벽 상단을 붙일 수 없는 종류다({tr['kind']}): {t}", path=f"/targets/{i}",
                             hint="경사 지붕(roof)·솔리드·슬래브에 붙일 수 있다")
        if tr["origin"] != "authored":
            raise DobimError("CONSTRAINT", f"저작 요소가 아니라(외래·분리됨) 형상을 읽을 수 없다: {t}", path=f"/targets/{i}")
        dz = ctx.g.q(Q.level_info, ctx.rels_out("on_level", t)[0])[1] - wall_elev
        faces += WT.target_faces(tr["kind"], tr["params"], a.face, dz)
    prof, covered = WT.attach_profile(p["axis"], p["height"], faces, a.face)
    if prof is None:
        raise DobimError("CONSTRAINT", "벽 축선이 대상 면 아래(위)를 지나지 않는다", path="/targets",
                         hint="평면에서 벽 축선이 대상 외곽 안을 지나야 한다")
    why = WT.profile_problem(prof, sum((p["axis"][k + 2] - p["axis"][k]) ** 2 for k in (0, 1)) ** 0.5)
    if why:
        raise DobimError("CONSTRAINT", f"붙인 상단 윤곽이 올바르지 않다: {why}", path="/targets",
                         hint="대상 면이 벽 레벨보다 낮으면 붙일 수 없다")
    p["top_profile"] = prof
    _wall_checked(p)
    _hosted_under_top(ctx, a.id, p)
    changed = p != r["params"]
    if changed:
        ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "changed": changed, "points": len(prof), "covered": covered,
            "top_min": WT.zmin(prof), "top_max": WT.zmax(prof)}


class WallDetachTop(A):
    id: Id


@command("wall.detach_top", kind="mutation", risk="write", group="model.wall", summary="벽 상단 윤곽을 뗀다(상단이 height 로 수평). 윤곽이 없으면 바꾸지 않는다",
         args=WallDetachTop, since="0.7", examples=[{"id": F.W1}])
def wall_detach_top(ctx, a: WallDetachTop):
    r = ctx.authored(a.id, "wall", what="상단 떼기")
    if "top_profile" not in r["params"]:
        return {"id": a.id, "changed": False}
    p = {k: v for k, v in r["params"].items() if k != "top_profile"}
    _wall_checked(p)   # 개구부가 height 위로 나가면 뗄 수 없다
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "changed": True}
