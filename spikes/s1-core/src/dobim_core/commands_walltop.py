# SPDX-License-Identifier: MPL-2.0
"""벽 상단 붙이기 커맨드(21단계, 설계: phase1-drawings/elements-design.md 23.2).

wall.attach_top  벽 축선 위에서 대상(경사 지붕·솔리드(경사 판 등)·슬래브)의 아랫면(또는 윗면) 높이를 재서 벽 상단 윤곽(top_profile)을 채운다.
wall.detach_top  상단 윤곽을 뗀다(상단이 height 로 수평).
wall.cut_by      보·인방 같은 부재가 벽과 겹친 부피를 벽 잘라내기(cuts)로 적는다(23단계, 설계 메모 25.1). 붙이기와 같은 스냅숏이다.
붙이기는 그 순간의 대상 형상을 윤곽 값으로 옮겨 적는다(스냅숏). 대상이 바뀌어도 벽은 따라가지 않는다: 다시 붙이면 새 형상을 따른다(설계 메모 23.2 의 의존 판단).
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from dobim_core import fixture as F
from dobim_core import queries as Q
from dobim_core import solids as SO
from dobim_core import wallcut as WC
from dobim_core import walltop as WT
from dobim_core.commands import A, Id, _hosted_under_top, _wall_checked, command, wall_len
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
    if "arc" in p:   # 곡선 벽(W2 ARC-04): 조각 현마다 재서 호 위 거리 u 의 윤곽으로 잇는다
        from dobim_core import arcgeom as AG
        prof, covered = WT.attach_profile_arc(AG.info(p["axis"], p["arc"]), WT.base(p) + p["height"], faces, a.face)
    else:
        prof, covered = WT.attach_profile(p["axis"], WT.base(p) + p["height"], faces, a.face)   # 바닥 오프셋(22단계): 덮이지 않은 구간 = 레벨 기준 윗면
    if prof is None:
        raise DobimError("CONSTRAINT", "벽 축선이 대상 면 아래(위)를 지나지 않는다", path="/targets",
                         hint="평면에서 벽 축선이 대상 외곽 안을 지나야 한다")
    why = WT.profile_problem(prof, wall_len(p))
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


CUT_TARGETS_MAX = 64


class WallCutBy(A):
    id: Id
    targets: Annotated[list[Id], Field(min_length=1, max_length=CUT_TARGETS_MAX, description=(
        "벽을 자를 부재 id 목록(솔리드: 보·인방(member)·기둥·바닥 마감 판 등). 벽 축에 나란한 직육면체여야 한다. 벽과 겹치지 않는 대상은 건너뛴다"))]


@command("wall.cut_by", kind="mutation", risk="write", group="model.wall",
         summary="벽에서 보·인방 같은 부재가 차지한 부피를 뺀다(형상 결합에서 구조 부재가 벽을 자르는 것): 겹친 상자를 벽 잘라내기(cuts)로 적는다(그 순간의 값, 기존 cuts 를 바꾼다)",
         args=WallCutBy, since="0.8", examples=[{"id": F.W1, "targets": [F.SO1]}])
def wall_cut_by(ctx, a: WallCutBy):
    r = ctx.authored(a.id, "wall", what="잘라내기")
    p = dict(r["params"])
    wall_elev = ctx.g.q(Q.level_info, ctx.rels_out("on_level", a.id)[0])[1]
    start, end = ctx.g.q(Q.wall_extent, a.id)
    half = p["thickness"] / 2.0
    wbox = (start, end, -half, half, WT.base(p), WT.top_rel(p))
    boxes, used, skipped = [], [], []
    for i, t in enumerate(dict.fromkeys(a.targets)):
        tr = ctx.row(t)
        if tr["kind"] != "solid":
            raise DobimError("CONSTRAINT", f"벽을 자를 수 없는 종류다({tr['kind']}): {t}", path=f"/targets/{i}",
                             hint="솔리드(보·인방 member, 기둥·마감 prism/plate 등)만 벽을 자른다")
        if tr["origin"] != "authored":
            raise DobimError("CONSTRAINT", f"저작 요소가 아니라(외래·분리됨) 형상을 읽을 수 없다: {t}", path=f"/targets/{i}")
        dz = ctx.g.q(Q.level_info, ctx.rels_out("on_level", t)[0])[1] - wall_elev
        verts, faces = SO.solid_mesh(tr["params"])
        pts = WC.to_local(p["axis"], verts, dz)
        if WC.overlap(WC.bbox(pts), wbox) is None:
            skipped.append(t)
            continue
        box = WC.aligned_box(pts, faces)
        if box is None:
            raise DobimError("CONSTRAINT", f"대상이 벽 축에 나란한 직육면체가 아니다: {t}", path=f"/targets/{i}",
                             hint="벽과 나란하거나 직각인 사각 단면 보·인방, 축 정렬 상자 솔리드만 벽을 자른다")
        o = WC.overlap(box, wbox)
        if o is None:
            skipped.append(t)
            continue
        boxes.append([float(x) for x in o])
        used.append(t)
    if not boxes:
        raise DobimError("CONSTRAINT", "벽과 겹치는 대상이 없다", path="/targets", hint="벽 형상(조인 범위·두께·높이)과 부피가 겹치는 부재를 주세요")
    p["cuts"] = boxes
    _wall_checked(p)
    changed = p != r["params"]
    if changed:
        ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id, "changed": changed, "cut": used, "skipped": skipped, "cut_volume_m3": WC.union_volume(boxes) / 1e9}


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
