# SPDX-License-Identifier: MPL-2.0
"""W1 1-C 2차: 뷰포트 배치·뷰 제목(DOC-02), 단면 머리 ↔ 시트 번호(VIEW-25), 콜아웃 뷰(VIEW-06).
- 뷰포트·번호가 없는 시트는 바이트까지 그대로, 뷰포트를 빼면 원래 바이트로 돌아온다
- 뷰포트 at 은 그 자리에 놓이고(응답 x·y 와 같은 좌표), 번호가 있으면 번호 원 제목, 단면·입면·콜아웃 기호 머리에 '번호 / 시트 번호'
- 콜아웃 뷰는 부모와 같은 종류의 잘라낸 뷰이고 부모 위에 기호로 그린다. IFC 왕복(DoBIM_Defs)·되돌리기. 합성 데이터만 쓴다."""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

V = F.V1
SEC = F.V2
SH2 = "9" + "0" * 19 + "77"


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r, code="CONSTRAINT"):
    assert not r["ok"] and r["error"]["code"] == code, r
    return r["error"]


def destructive(s, name, args):
    r = s.run(name, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


def build(s):
    ok(s.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.create", {"id": SEC, "name": "A-A 단면", "type": "section", "p1": [-1000, 2000], "p2": [7000, 2000]}))
    ok(s.run("sheet.define", {"id": F.SH1, "name": "평면", "views": [V], "paper": "A3", "sheet_number": "A-101", "date": "2026-01-01", "marks": True}))
    ok(s.run("sheet.define", {"id": SH2, "name": "단면", "views": [SEC], "paper": "A3", "sheet_number": "A-301", "date": "2026-01-01"}))
    return s


def render(s, sid=F.SH1, name="a.svg"):
    r = ok(destructive(s, "sheet.render", {"id": sid, "path": name, "overwrite": True}))
    return (s.path.parent / name).read_text(encoding="utf-8"), r


def group(svg, gid):
    i = svg.index(f'id="{gid}')
    return svg[i:svg.index("</g>", i)]


def test_뷰포트가_없으면_바이트가_같고_빼면_돌아온다(base):
    s = build(base)
    plain, r0 = render(s)
    params = s.eng.st.el[F.SH1]["params"]
    assert "viewports" not in params and "view-title" not in plain
    ok(s.run("sheet.viewport.set", {"id": F.SH1, "view": V, "at": [40, 40], "number": "1"}))
    assert render(s)[0] != plain
    ok(s.run("sheet.viewport.set", {"id": F.SH1, "view": V, "clear": True}))
    assert s.eng.st.el[F.SH1]["params"] == params and render(s)[0] == plain
    ok(s.run("sheet.viewport.set", {"id": F.SH1, "view": V, "title": "다른 제목"}))
    ok(s.run("history.undo", {}))
    assert render(s)[0] == plain
    # 번호가 없는 단면은 참조가 없다: marks 기호는 이전과 같다
    assert "A-301" not in plain


def test_뷰포트_위치_번호_제목(base):
    s = build(base)
    ok(s.run("sheet.viewport.set", {"id": F.SH1, "view": V, "at": [40.5, 35], "number": "1", "title": "1층 평면도"}))
    svg, r = render(s)
    pl = r["views"][0]
    assert (pl["x"], pl["y"], pl["number"]) == (40.5, 35.0, "1")
    t = group(svg, "view-title")
    assert 'data-number="1"' in t and "1층 평면도" in t and "S=1:100" in t
    # 제목만 주면 원래 모양의 제목에 글만 바뀐다
    ok(s.run("sheet.viewport.set", {"id": F.SH1, "view": V, "title": "평면도"}))
    svg2, r2 = render(s)
    assert "view-title" not in svg2 and "평면도" in svg2 and r2["views"][0]["x"] != 40.5
    # sheet.export 도 같은 인자를 받는다(저장 안 한 시트)
    r3 = ok(destructive(s, "sheet.export", {"views": [V, SEC], "path": "e.svg", "paper": "A3", "sheet_number": "A-102", "overwrite": True,
                                          "viewports": [{"view": SEC, "at": [30, 30], "number": "2"}]}))
    sec = next(q for q in r3["views"] if q["view"] == SEC)
    assert (sec["x"], sec["y"]) == (30.0, 30.0)


def test_뷰포트_입력_오류(base):
    s = build(base)
    err(s.run("sheet.viewport.set", {"id": F.SH1, "view": SEC, "number": "1"}))   # 시트에 없는 뷰
    r = s.run("sheet.define", {"id": F.SH1, "name": "평면", "views": [V, SEC], "paper": "A3", "sheet_number": "A-101", "date": "2026-01-01",
                               "viewports": [{"view": V, "number": "1"}, {"view": SEC, "number": "1"}]})
    assert not r["ok"]   # 시트 안 번호 겹침
    r = s.run("sheet.define", {"id": F.SH1, "name": "평면", "views": [V], "paper": "A3", "sheet_number": "A-101", "date": "2026-01-01",
                               "viewports": [{"view": SEC}]})
    assert not r["ok"]   # 올리지 않은 뷰
    ok(s.run("sheet.viewport.set", {"id": SH2, "view": SEC, "number": "3"}))
    ok(s.run("sheet.define", {"id": F.SH1, "name": "평면", "views": [V, SEC], "paper": "A3", "sheet_number": "A-101", "date": "2026-01-01"}))
    err(s.run("sheet.viewport.set", {"id": F.SH1, "view": SEC, "number": "4"}))   # 한 뷰의 번호는 한 시트에만
    ok(s.run("sheet.viewport.set", {"id": F.SH1, "view": SEC, "at": [20, 20]}))   # 번호 없이 놓기는 된다
    ok(s.run("sheet.viewport.set", {"id": F.SH1, "view": V, "at": [380, 250]}))
    err(destructive(s, "sheet.render", {"id": F.SH1, "path": "x.svg", "overwrite": True}))   # 도면 영역 밖
    assert not s.run("sheet.viewport.set", {"id": F.SH1, "view": V, "clear": True, "number": "1"})["ok"]


def test_단면_머리에_상세_번호와_시트_번호(base):
    s = build(base)
    plain, _ = render(s)
    ok(s.run("view.create", {"name": "N 입면", "type": "elevation", "dir": "N"}))
    ok(s.run("sheet.viewport.set", {"id": SH2, "view": SEC, "number": "3"}))
    svg, _ = render(s)
    m = group(svg, "section-markers")
    assert m.count(">3<") == 2 and m.count(">A-301<") == 2
    assert "A-301" not in group(svg, "elevation-markers")
    refs = ok(s.run("view.refs", {"id": SEC}))["items"][0]
    assert refs["ref"] == {"number": "3", "sheet_number": "A-301", "sheet": SH2} and refs["sheets"][0]["number"] == "3"
    # 지금 내는 시트가 같은 뷰를 번호와 함께 놓으면 그 시트 번호가 이긴다(저장 안 한 sheet.export)
    ok(destructive(s, "sheet.export", {"views": [V], "path": "e.svg", "paper": "A3", "sheet_number": "A-999", "marks": True, "overwrite": True}))
    assert "A-301" in (s.path.parent / "e.svg").read_text(encoding="utf-8")
    # 번호를 빼면 기호는 원래대로
    ok(s.run("sheet.viewport.set", {"id": SH2, "view": SEC, "clear": True}))
    assert "A-301" not in render(s)[0]
    ok(s.run("history.undo", {}))
    ok(s.run("sheet.delete", {"id": SH2}))
    assert "A-301" not in render(s)[0]


def test_콜아웃_뷰(base):
    s = build(base)
    plain, _ = render(s)
    r = ok(s.run("view.callout", {"id": F.VC1, "parent": V, "rect": [-500, -800, 2500, 800], "name": "C1 벽 상세", "scale": 20}))
    assert r == {"id": F.VC1, "parent": V, "type": "plan"}
    p = s.eng.st.el[F.VC1]["params"]
    assert p["level"] == F.LV1 and p["crop"] == [-500, -800, 2500, 800] and p["parent"] == V and p["scale"] == 20
    ok(destructive(s, "view.export", {"id": F.VC1, "path": "c.svg", "overwrite": True}))
    svg, _ = render(s)
    c = group(svg, "detail-callouts")
    assert ">C1<" in c and 'data-count="1"' in c
    assert "section-markers" not in svg or svg.count("section-markers") == plain.count("section-markers")   # 콜아웃은 절단선이 아니다
    # 콜아웃 뷰를 다른 시트에 번호와 놓으면 머리에 참조
    ok(s.run("sheet.define", {"id": SH2, "name": "단면", "views": [SEC, F.VC1], "paper": "A3", "sheet_number": "A-501", "date": "2026-01-01",
                              "viewports": [{"view": F.VC1, "number": "5"}]}))
    c = group(render(s)[0], "detail-callouts")
    assert ">5<" in c and ">A-501<" in c
    assert ok(s.run("view.refs", {"id": V}))["items"][0]["callouts"] == [F.VC1]
    # 범위는 crop 으로 바꾸고, 없애거나 부모를 먼저 지울 수는 없다
    ok(s.run("view.set", {"id": F.VC1, "crop": [0, -800, 3000, 800]}))
    err(s.run("view.set", {"id": F.VC1, "clear_crop": True}))
    err(s.run("view.delete", {"id": V}))
    err(s.run("view.callout", {"parent": F.VC1, "rect": [0, 0, 10, 10], "name": "C2"}))
    site = ok(s.run("view.create", {"name": "배치", "type": "site"}))["id"]
    err(s.run("view.callout", {"parent": site, "rect": [0, 0, 10, 10], "name": "C3"}))
    sc = ok(s.run("view.callout", {"parent": SEC, "rect": [0, 0, 3000, 3000], "name": "C4 단면 상세"}))
    assert sc["type"] == "section" and s.eng.st.el[sc["id"]]["params"]["p1"] == [-1000, 2000]
    assert not s.run("view.callout", {"parent": V, "rect": [10, 0, 0, 10], "name": "C5"})["ok"]


def test_IFC_왕복은_뷰포트와_콜아웃을_되살리고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        build(b)
        ok(b.run("view.callout", {"id": F.VC1, "parent": V, "rect": [-500, -800, 2500, 800], "name": "C1 벽 상세"}))
        ok(b.run("sheet.viewport.set", {"id": SH2, "view": SEC, "at": [30, 30], "number": "3", "title": "단면도"}))
        ok(b.run("sheet.define", {"id": SH2, "name": "단면", "views": [SEC, F.VC1], "paper": "A3", "sheet_number": "A-301", "date": "2026-01-01",
                                  "viewports": [{"view": SEC, "at": [30, 30], "number": "3", "title": "단면도"}, {"view": F.VC1, "number": "4"}]}))
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("view.refs", {}, project="imp.dobim")["result"] == b.run("view.refs", {})["result"]
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == (tmp_path / "t.ifc").read_bytes()
    finally:
        b.close()


def test_변조하거나_범위를_벗어난_정의는_되살리지_않는다():
    from dobim_core import viewdefs as VD
    eid = "9" * 22
    base_v = {"name": "C1", "type": "plan", "level": F.LV1, "cut_height": 1200, "view_depth": 1200, "scale": 20}
    VD.parse_def(eid, VD.def_text(eid, {"kind": "view", "family_id": "view.basic", "params": {**base_v, "crop": [0, 0, 1, 1], "parent": V}}))
    for bad in ({"parent": V}, {"parent": "x", "crop": [0, 0, 1, 1]}):
        with pytest.raises(ValueError):
            VD.parse_def(eid, VD.def_text(eid, {"kind": "view", "family_id": "view.basic", "params": {**base_v, **bad}}))
    sheet = {"name": "s", "views": [V], "paper": "A3", "project_name": "", "sheet_name": "", "sheet_number": "A-1", "date": None, "north_angle": 0.0,
             "client": "", "office": "", "signers": [], "revisions": [], "grids": [], "callouts": [], "marks": False, "tags": [], "key_plan": None,
             "key_zone": None, "line_profile": None}
    vp = {"view": V, "at": [10.0, 10.0], "number": "1"}
    VD.parse_def(eid, VD.def_text(eid, {"kind": "sheet", "family_id": "sheet.basic", "params": sheet}))
    VD.parse_def(eid, VD.def_text(eid, {"kind": "sheet", "family_id": "sheet.basic", "params": {**sheet, "viewports": [vp]}}))
    for bad in ([], [{**vp, "view": F.V2}], [vp, vp], [{**vp, "at": [10, 10]}], [{**vp, "at": [-1.0, 10.0]}], [{**vp, "title": None}], None):
        with pytest.raises(ValueError):
            VD.parse_def(eid, VD.def_text(eid, {"kind": "sheet", "family_id": "sheet.basic", "params": {**sheet, "viewports": bad}}))
