# SPDX-License-Identifier: MPL-2.0
"""W1 1-C: 뷰 가시성·그래픽 재정의(VIEW-13)·뷰 필터(VIEW-14)·뷰 템플릿(VIEW-15).
- 설정이 없는 뷰의 도면은 바이트까지 그대로, 설정을 지우면 원래 바이트로 돌아온다
- 범주 숨기기(모델·주석)·색·선 굵기·하프톤, 조건 필터(위가 우선), 템플릿(뷰는 템플릿을 따르고 떼거나 지우면 설정을 복사)
- SVG·PDF·PNG·DXF·시트가 재정의를 그린다. IFC 왕복(DoBIM_Defs)·되돌리기. 합성 데이터만 쓴다."""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

V = F.V1
T = F.VT1


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
    ok(s.run("annot.dim.create", {"view": V, "a": f"wall:{F.W1}#trim.start", "b": f"wall:{F.W1}#trim.end"}))
    return s


def svg(s, name="v.svg", vid=V):
    ok(destructive(s, "view.export", {"id": vid, "path": name, "overwrite": True}))
    return (s.path.parent / name).read_text(encoding="utf-8")


def test_설정이_없으면_바이트가_같고_지우면_돌아온다(base):
    s = build(base)
    plain = svg(s)
    params = s.eng.st.el[V]["params"]
    ok(s.run("view.vg.set", {"id": V, "category": "wall", "color": "#C00000"}))
    ok(s.run("view.filter.set", {"id": V, "name": "f", "cats": ["wall"], "rules": [], "halftone": True}))
    assert svg(s) != plain
    ok(s.run("view.vg.set", {"id": V, "category": "wall", "clear": True}))
    ok(s.run("view.filter.delete", {"id": V, "name": "f"}))
    assert s.eng.st.el[V]["params"] == params and svg(s) == plain
    ok(s.run("view.vg.set", {"id": V, "category": "door", "hidden": True}))
    ok(s.run("history.undo", {}))
    assert svg(s) == plain


def test_범주_숨기기와_색_굵기_하프톤(base):
    s = build(base)
    plain = svg(s)
    assert 'id="annots"' in plain or "<g" in plain
    ok(s.run("view.vg.set", {"id": V, "category": "wall", "hidden": True}))
    ok(s.run("view.vg.set", {"id": V, "category": "dim", "hidden": True}))
    hid = svg(s)
    assert len(hid) < len(plain) // 2
    g = ok(s.run("view.vg.get", {"id": V}))
    assert g["source"] == "view" and g["elements"]["hidden"] == 3 and g["vg"]["dim"] == {"hidden": True}
    err(s.run("view.vg.set", {"id": V, "category": "dim", "color": "#ff0000"}))
    ok(s.run("view.vg.set", {"id": V, "category": "wall", "hidden": False, "color": "#C00000", "cut_weight": 0.7}))
    col = svg(s)
    assert 'stroke="#c00000" stroke-width="0.7"' in col and 'fill="#c00000"' in col
    ok(s.run("view.vg.set", {"id": V, "category": "wall", "halftone": True, "unset": ["color"]}))
    half = svg(s)
    assert 'stroke="#7f7f7f" stroke-width="0.7"' in half and 'fill="#e7e7e7"' in half
    assert ok(s.run("view.vg.get", {"id": V}))["vg"]["wall"] == {"cut_weight": 0.7, "halftone": True}


def test_필터는_규칙에_맞는_요소만_위가_우선(base):
    s = build(base)
    ok(s.run("wall.create", {"id": "1" + "0" * 20 + "9", "level": F.LV1, "axis": [0, 4000, 0, 0], "thickness": 100, "height": 3000}))
    ok(s.run("view.filter.set", {"id": V, "name": "두꺼운 벽", "cats": ["wall"], "rules": [{"field": "thickness", "op": "ge", "value": "200"}],
                                 "color": "#0050c0"}))
    assert ok(s.run("view.vg.get", {"id": V}))["elements"] == {"hidden": 0, "styled": 3}
    out = svg(s)
    assert out.count('data-id="1' + "0" * 20 + "9") == 0 and 'stroke="#0050c0"' in out
    r = ok(s.run("view.filter.set", {"id": V, "name": "얇은 벽", "cats": ["wall"], "rules": [{"field": "thickness", "op": "lt", "value": "150"}],
                                     "hidden": True, "index": 0}))
    assert r["index"] == 0
    assert ok(s.run("view.vg.get", {"id": V}))["elements"] == {"hidden": 1, "styled": 3}
    ok(s.run("view.filter.set", {"id": V, "name": "1번 벽", "cats": ["wall"], "rules": [{"field": "name", "op": "missing", "value": ""}], "color": "#00aa00", "index": 0}))
    assert 'stroke="#00aa00"' not in svg(s)   # 이름 없는 벽은 없다
    ok(s.run("pset.set", {"id": F.W1, "pset": "Pset_WallCommon", "prop": "FireRating", "value": "1시간"}))
    ok(s.run("view.filter.set", {"id": V, "name": "방화", "cats": ["wall"], "rules": [{"field": "pset:Pset_WallCommon.FireRating", "op": "contains", "value": "시간"}],
                                 "color": "#ff0000", "index": 0}))
    out = svg(s)
    assert 'stroke="#ff0000"' in out and 'stroke="#0050c0"' in out   # 위 필터가 W1, 아래 필터가 나머지 두꺼운 벽
    for bad in ({"rules": [{"field": "thickness", "op": "gt", "value": "두꺼움"}]}, {"rules": [{"field": "nope", "op": "eq", "value": "1"}]},
                {"rules": [{"field": "name", "op": "has", "value": "x"}]}, {"color": None, "cats": ["wall"]}, {"cats": ["dim"]}):
        args = {"id": V, "name": "x", "cats": ["wall"], "rules": [], "color": "#000000", **bad}
        args = {k: v for k, v in args.items() if v is not None}
        assert s.run("view.filter.set", args)["error"]["code"] == "SCHEMA_INVALID"
    err(s.run("view.filter.delete", {"id": V, "name": "없음"}), "NOT_FOUND")


def test_템플릿은_뷰_설정을_정하고_떼거나_지우면_복사한다(base):
    s = build(base)
    plain = svg(s)
    ok(s.run("view.vg.set", {"id": V, "category": "wall", "color": "#123456"}))
    ok(s.run("view.template.create", {"id": T, "name": "평면 기본", "from_view": V, "scale": 50}))
    t2 = ok(s.run("view.template.create", {"name": "빈 템플릿"}))["id"]
    err(s.run("view.template.create", {"name": "평면 기본"}))
    assert ok(s.run("view.vg.get", {"id": T}))["vg"] == {"wall": {"color": "#123456"}}
    ok(s.run("view.vg.set", {"id": V, "category": "wall", "clear": True}))
    ok(s.run("view.template.apply", {"view": V, "template": T}))
    assert s.eng.st.el[V]["params"]["scale"] == 50
    err(s.run("view.vg.set", {"id": V, "category": "wall", "hidden": True}))
    ok(s.run("view.vg.set", {"id": T, "category": "dim", "hidden": True}))
    g = ok(s.run("view.vg.get", {"id": V}))
    assert g["source"] == "template" and g["template"] == T and set(g["vg"]) == {"wall", "dim"}
    out = svg(s)
    assert 'stroke="#123456"' in out
    assert ok(s.run("view.template.set", {"id": T, "scale": 100}))["views"] == [V]
    assert s.eng.st.el[V]["params"]["scale"] == 100
    lst = ok(s.run("view.template.list", {}))["items"]
    assert [(i["name"], i["views"]) for i in lst] == sorted([("평면 기본", [V]), ("빈 템플릿", [])], key=lambda x: [i["id"] for i in lst].index(T if x[0] == "평면 기본" else t2))
    ok(s.run("view.template.apply", {"view": V, "template": t2}))   # 빈 템플릿: 설정 없음 = 원래 도면
    assert svg(s) == plain
    ok(s.run("view.template.apply", {"view": V, "template": T}))
    ok(s.run("view.template.delete", {"id": T}))
    p = s.eng.st.el[V]["params"]
    assert "template" not in p and p["vg"] == {"wall": {"color": "#123456"}, "dim": {"hidden": True}}
    ok(s.run("history.undo", {}))
    ok(s.run("view.template.apply", {"view": V, "template": None}))
    assert "template" not in s.eng.st.el[V]["params"] and s.eng.st.el[V]["params"]["vg"]["dim"] == {"hidden": True}
    err(s.run("view.template.apply", {"view": V, "template": None}))


def test_모든_형식과_시트가_재정의를_그린다(base):
    s = build(base)
    ok(s.run("view.vg.set", {"id": V, "category": "wall", "color": "#c00000"}))
    ok(s.run("draft.create", {"view": V, "shape": "mask", "pts": [[0, -500], [1000, -500], [1000, 500], [0, 500]]}))
    for ext in ("pdf", "png", "dxf"):
        r = ok(destructive(s, "view.export", {"id": V, "path": f"v.{ext}", "overwrite": True}))
        assert (s.path.parent / f"v.{ext}").stat().st_size > 0, r
    assert "VG" in (s.path.parent / "v.dxf").read_text(encoding="utf-8", errors="replace")
    ok(s.run("sheet.define", {"id": F.SH1, "name": "A-101", "views": [V], "paper": "A3", "project_name": "예시", "sheet_number": "A-101", "date": "2026-01-01"}))
    ok(destructive(s, "sheet.render", {"id": F.SH1, "path": "a.svg", "overwrite": True}))
    assert "#c00000" in (s.path.parent / "a.svg").read_text(encoding="utf-8")
    ok(s.run("view.vg.set", {"id": V, "category": "draft", "hidden": True}))
    assert 'id="drafts"' in svg(s)   # 재정의 항목은 남고 2D 요소(마스킹)만 빠진다
    ok(s.run("view.vg.set", {"id": V, "category": "wall", "clear": True}))
    assert 'id="drafts"' not in svg(s)


def test_IFC_왕복은_템플릿과_필터를_되살리고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        build(b)
        ok(b.run("view.template.create", {"id": T, "name": "평면 기본", "scale": 50}))
        ok(b.run("view.filter.set", {"id": T, "name": "두꺼운 벽", "cats": ["wall", "finst"], "rules": [{"field": "thickness", "op": "ge", "value": "200"}],
                                     "color": "#0050c0", "proj_weight": 0.25}))
        ok(b.run("view.vg.set", {"id": T, "category": "tag", "hidden": True}))
        ok(b.run("view.template.apply", {"view": V, "template": T}))
        v2 = ok(b.run("view.create", {"name": "B-B", "type": "section", "p1": [-1000, 2000], "p2": [7000, 2000]}))["id"]
        ok(b.run("view.vg.set", {"id": v2, "category": "slab", "halftone": True}))
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("view.vg.get", {"id": V}, project="imp.dobim")["result"] == b.run("view.vg.get", {"id": V})["result"]
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == (tmp_path / "t.ifc").read_bytes()
    finally:
        b.close()


def test_변조하거나_범위를_벗어난_정의는_되살리지_않는다():
    from dobim_core import viewdefs as VD
    eid = "9" * 22
    base_v = {"name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200, "view_depth": 1200, "scale": 100}
    good = {"vg": {"wall": {"color": "#c00000"}}, "filters": [{"name": "f", "cats": ["wall"], "rules": [], "hidden": True}], "template": T}
    VD.parse_def(eid, VD.def_text(eid, {"kind": "view", "family_id": "view.basic", "params": {**base_v, **good}}))
    VD.parse_def(eid, VD.def_text(eid, {"kind": "vtemplate", "family_id": "view.template", "params": {"name": "t", "scale": 50, **{k: good[k] for k in ("vg", "filters")}}}))
    for bad in ({"vg": {}}, {"vg": {"wall": {}}}, {"vg": {"dim": {"color": "#000000"}}}, {"vg": {"wall": {"color": "#C00000"}}}, {"vg": {"nope": {"hidden": True}}},
                {"vg": {"wall": {"cut_weight": 5.0}}}, {"vg": {"wall": {"hidden": False}}}, {"filters": []},
                {"filters": [{"name": "f", "cats": ["wall"], "rules": []}]}, {"filters": [{"name": "f", "cats": ["grid"], "rules": [], "hidden": True}]},
                {"filters": [{"name": "f", "cats": ["wall"], "rules": [{"field": "name", "op": "gt", "value": "x"}], "hidden": True}]},
                {"filters": [{"name": "f", "cats": ["wall"], "rules": [], "hidden": True}] * 2}, {"template": "x"}):
        with pytest.raises(ValueError):
            VD.parse_def(eid, VD.def_text(eid, {"kind": "view", "family_id": "view.basic", "params": {**base_v, **bad}}))
    for bad in ({"scale": 0}, {"scale": 50.0}, {"template": T}, {"vg": {"wall": {"hidden": True}}, "x": 1}):
        with pytest.raises(ValueError):
            VD.parse_def(eid, VD.def_text(eid, {"kind": "vtemplate", "family_id": "view.template", "params": {"name": "t", **bad}}))
