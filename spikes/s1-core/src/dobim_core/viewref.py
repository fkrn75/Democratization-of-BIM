# SPDX-License-Identifier: MPL-2.0
"""뷰포트·뷰 참조·콜아웃 뷰의 순수 계산 (W1 1-C 2차: DOC-02 뷰포트 배치·뷰 제목, VIEW-25 단면 머리 ↔ 시트 번호, VIEW-06 콜아웃 뷰).

뷰포트 = 시트 정의(sheet)의 선택 키 viewports: [{view, at, number, title}]. at 은 뷰 틀의 왼쪽 위(용지 mm), number 는 상세 번호, title 은 시트 위 제목.
뷰 참조 = 상세 번호를 받은 뷰가 어느 시트에 있는가. marks 를 켠 시트의 단면 절단선·입면 방향·콜아웃 기호 머리에 '번호 / 시트 번호'를 적는다.
  참조는 저장된 시트 정의(sheet.define)에서 읽고, 지금 내는 시트의 뷰포트가 같은 뷰를 번호와 함께 놓으면 그것이 이긴다.
  번호가 없는 뷰는 참조가 없다: 기호는 이전처럼 뷰 이름 첫 낱말을 적는다(이전 시트 바이트 그대로).
콜아웃 뷰 = 부모 뷰와 같은 종류의 뷰에 parent(부모 뷰 id)와 crop(콜아웃 범위)을 둔 것. marks 를 켠 시트에서 부모 뷰 위에 1점쇄선 사각형과 머리로 그린다.
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from dobim_core.errors import DobimError


def _g(vp, k):
    return vp.get(k) if isinstance(vp, dict) else getattr(vp, k)


def viewports_error(views, viewports) -> str | None:
    """뷰포트 목록이 시트 스스로 맞아야 하는 것: 올린 뷰만, 뷰마다 한 번, 상세 번호는 시트 안에서 겹치지 않는다. 맞으면 None."""
    if not viewports:
        return None
    seen, nums = set(), set()
    for vp in viewports:
        v, n = _g(vp, "view"), _g(vp, "number")
        if v not in views:
            return "뷰포트의 뷰가 시트에 없다(views 에 있는 뷰여야 한다)"
        if v in seen:
            return "같은 뷰의 뷰포트를 두 번 줄 수 없다"
        seen.add(v)
        if n is not None:
            if n in nums:
                return f"상세 번호가 시트 안에서 겹친다: {n}"
            nums.add(n)
    return None


def numbered(st, except_sid=None) -> dict:
    """저장된 시트에서 상세 번호를 받은 뷰: {뷰 id: (상세 번호, 시트 번호, 시트 id)}. 시트 id 순으로 처음 것."""
    out: dict = {}
    for sid in sorted(st.by_kind.get("sheet", ())):
        if sid == except_sid:
            continue
        p = st.el[sid]["params"]
        for vp in p.get("viewports") or ():
            if vp.get("number") is not None:
                out.setdefault(vp["view"], (vp["number"], p["sheet_number"], sid))
    return out


def check_numbered_once(st, sid, params) -> None:
    """한 뷰에 상세 번호를 주는 시트는 하나다(참조가 한 곳을 가리키도록, Revit 처럼)."""
    other = numbered(st, sid)
    for vp in params.get("viewports") or ():
        if vp.get("number") is not None and vp["view"] in other:
            raise DobimError("CONSTRAINT", f"이 뷰는 다른 시트({other[vp['view']][2]})에 상세 번호 {other[vp['view']][0]} 로 놓여 있다", path="/viewports",
                             hint="한 뷰의 상세 번호는 한 시트에만 준다. 다른 시트에서 번호를 빼거나 번호 없이 놓으세요")


def ref_map(st, sheet_id, sheet_number, viewports) -> dict:
    """뷰 참조 {뷰 id: (상세 번호, 시트 번호)}: 저장된 다른 시트 + 지금 내는 시트(sheet_number·viewports)."""
    refs = {v: (n, s) for v, (n, s, _sid) in numbered(st, sheet_id).items()}
    for vp in viewports or ():
        if _g(vp, "number") is not None:
            refs[_g(vp, "view")] = (_g(vp, "number"), sheet_number)
    return refs


def label_of(name: str) -> str:
    """기호 머리에 적는 뷰 이름 첫 낱말(단면 기호와 같은 규칙)."""
    return (name.split() or ["?"])[0][:4]


def callout_views(st, parent=None) -> list[str]:
    """콜아웃 뷰 id(정렬). parent 를 주면 그 뷰의 콜아웃만."""
    return sorted(v for v in st.by_kind.get("view", ()) if "parent" in st.el[v]["params"] and (parent is None or st.el[v]["params"]["parent"] == parent))


def callout_items(st, on_views, refs) -> list[dict]:
    """시트에 올린 부모 뷰 위에 그릴 콜아웃 기호: [{view(부모), rect, label, ref?}]."""
    out = []
    for v in callout_views(st):
        p = st.el[v]["params"]
        if p["parent"] in on_views and "crop" in p:
            it = {"view": p["parent"], "rect": list(p["crop"]), "label": label_of(p["name"]), "id": v}
            if v in refs:
                it["ref"] = refs[v]
            out.append(it)
    return out


def sheet_spec(ctx, a, sheet_id, spec: dict) -> None:
    """시트 spec 에 뷰포트·뷰 참조·콜아웃 기호를 더한다. 뷰포트도 marks 도 없으면 spec 은 그대로다(이전 시트 바이트 불변)."""
    if a.viewports:
        spec["viewports"] = {vp.view: {"at": list(vp.at) if vp.at else None, "number": vp.number, "title": vp.title} for vp in a.viewports}
    if a.marks:
        refs = ref_map(ctx.st, sheet_id, a.sheet_number, a.viewports)
        for key in ("sections", "elevs"):
            for m in spec.get(key) or ():
                if m.get("id") in refs:
                    m["ref"] = refs[m["id"]]
        vc = callout_items(ctx.st, set(a.views), refs)
        if vc:
            spec["vcallouts"] = vc


def view_refs(st, vid) -> dict:
    """뷰 하나의 참조 정보: 올린 시트들, 상세 번호 참조, 부모·콜아웃 뷰."""
    p = st.el[vid]["params"]
    sheets = []
    for sid in sorted(st.by_kind.get("sheet", ())):
        sp = st.el[sid]["params"]
        if vid in sp["views"]:
            vp = next((x for x in sp.get("viewports") or () if x["view"] == vid), None)
            sheets.append({"sheet": sid, "sheet_name": sp["name"], "sheet_number": sp["sheet_number"], "number": vp.get("number") if vp else None,
                           "at": vp.get("at") if vp else None, "title": vp.get("title") if vp else None})
    ref = numbered(st).get(vid)
    out = {"id": vid, "name": p["name"], "type": p["type"], "sheets": sheets, "ref": {"number": ref[0], "sheet_number": ref[1], "sheet": ref[2]} if ref else None,
           "callouts": callout_views(st, vid)}
    if "parent" in p:
        out["parent"] = p["parent"]
        out["rect"] = list(p["crop"]) if "crop" in p else None
    return out
