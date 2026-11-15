# SPDX-License-Identifier: MPL-2.0
"""sheet.export·sheet.index 의 선택 입력(표제란 확장·개정·통심선·기호·태그·키 플랜·선 굵기 프로파일)을 시트 spec 으로 옮긴다.

입력이 하나도 없으면 빈 딕셔너리를 돌려준다(그러면 시트는 이전과 바이트까지 같다). 모델은 읽기만 한다.
"""
from __future__ import annotations

import re

from dobim_core import commands as C
from dobim_core import sheet_model as SM
from dobim_core.errors import DobimError

NUMBER_RE = re.compile(r"^[A-Z]{1,2}-\d{3}(-\d{1,2})?$")      # 도면번호 체계: 공종 문자-3자리 일련번호(예: A-101)
DISCIPLINES = {"A": "건축", "S": "구조", "M": "기계", "E": "전기", "P": "배관", "C": "토목", "L": "조경"}


def number_check(number: str, path_stem: str) -> dict:
    """도면번호 체계(형식)와 파일명 일치를 검사한다. 응답에 그대로 실린다."""
    return {"format_ok": bool(NUMBER_RE.match(number)), "file_match": path_stem == number,
            "discipline": DISCIPLINES.get(number.split("-")[0], "") if NUMBER_RE.match(number) else ""}


def frame_spec(a) -> dict:
    """표제란·개정·선 굵기: 모델을 보지 않는 입력."""
    out: dict = {}
    if a.client:
        out["client"] = a.client
    if a.office:
        out["office"] = a.office
    if a.signers:
        out["signers"] = [{"role": s.role, "name": s.name} for s in a.signers]
    if a.revisions:
        out["revisions"] = [{"rev": r.rev, "date": r.date, "desc": r.desc, "view": r.view} for r in a.revisions]
    if a.line_profile:
        out["line_profile"] = dict(a.line_profile)
    return out


def profile_spec(a) -> dict:
    """별도 작성기준 프로파일(문자 높이·치수 형식·기호 크기). 값이 하나도 없으면 빈 딕셔너리(시트는 이전과 같다)."""
    pf = getattr(a, "profile", None)
    if pf is None:
        return {}
    out: dict = {}
    if pf.text_height:
        out["text_height"] = dict(pf.text_height)
    if pf.dim_decimals is not None:
        out["dim_decimals"] = pf.dim_decimals
    if pf.dim_thousands is not None:
        out["dim_thousands"] = pf.dim_thousands
    if pf.symbol_scale is not None:
        out["symbol_scale"] = pf.symbol_scale
    if pf.hidden_lines:
        out["hidden_lines"] = True
    if pf.line_hierarchy:
        out["line_hierarchy"] = True
    if pf.projection:
        out["projection"] = pf.projection
    return {"profile": out} if out else {}


def view_extras(ctx, a, datas) -> dict:
    """뷰에 얹는 입력(통심선·단면 기호·부위 상세·태그·키 플랜)."""
    out: dict = {}
    ids = {d["id"] for d in datas}
    for r in a.revisions:
        if r.view and r.view not in ids:
            raise DobimError("CONSTRAINT", "개정 구름을 둘 뷰가 시트에 없다", path="/revisions", hint="revisions[].view 는 views 에 있는 뷰여야 한다")
    if a.grids:
        out["grids"] = [{"label": g.label, "axis": g.axis, "pos": g.pos} for g in a.grids]
    if a.callouts:
        for c in a.callouts:
            if c.view not in ids:
                raise DobimError("CONSTRAINT", "부위 상세 표시를 둘 뷰가 시트에 없다", path="/callouts", hint="callouts[].view 는 views 에 있는 뷰여야 한다")
        out["callouts"] = [{"view": c.view, "rect": list(c.rect), "label": c.label} for c in a.callouts]
    if a.marks:
        out["sections"], out["elevs"] = SM.view_marks(ctx.g)
    if a.tags:
        tg = {}
        for d in datas:
            if d["type"] == "plan":
                tg[d["id"]] = SM.plan_tags(ctx.g, d["id"], set(a.tags))
        out["tags"] = tg
    if a.dims:
        dm = {}
        for d in datas:
            if d["type"] == "plan":
                dm[d["id"]] = SM.plan_dims(ctx.g, d["id"], set(a.dims))
            elif d["type"] == "elevation" and "elevation" in a.dims:
                dm[d["id"]] = {"elevation": SM.elev_dims(ctx.g, d["id"])}
        out["dims"] = dm
    if a.key_plan:
        r = ctx.row(a.key_plan, "view")
        if r["params"]["type"] != "plan":
            raise DobimError("CONSTRAINT", "키 플랜은 평면 뷰여야 한다", path="/key_plan")
        out["key_plan"] = {"data": C._render_view(ctx, a.key_plan), "zone": list(a.key_zone) if a.key_zone else None}
    return out
