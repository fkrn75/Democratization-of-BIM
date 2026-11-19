# SPDX-License-Identifier: MPL-2.0
"""W1 1-B 2차: 모델 연관 치수 확장(ANN-02)과 태그 범주 확장(ANN-06·08).
- 통심선·패밀리 인스턴스(창·기둥) 참조, 각도·반지름·지름 치수(measure). 요소를 옮기면 값·그림이 따라간다
- 창·기둥(패밀리 인스턴스)·지붕·난간 태그, 다중 범주 필드(ifc_class), 범주별 일괄 태그(annot.tag.all)
- 그림 항목·출력(SVG·PDF·DXF·시트)·증분 = 전체·IFC 왕복(DoBIM_Defs). 합성 데이터만 쓴다."""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import annot as AN
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V = F.V1
G1, G2 = "g" + "0" * 20 + "1", "g" + "0" * 20 + "2"
WIN, COL = "0" * 21 + "2", "0" * 21 + "3"


def ok(r):
    assert r["ok"], r
    return r["result"]


def destructive(s, name, args):
    r = s.run(name, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


def build(s):
    """기준 프로젝트 + 평면 뷰 + 통심선 둘(세로·사선) + W3 의 창 + 둥근 기둥."""
    ok(s.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("grid.create", {"id": G1, "name": "1", "line": [-1000, -2000, -1000, 6000]}))
    ok(s.run("grid.create", {"id": G2, "name": "A", "line": [-2000, -2000, 8000, 8000]}))
    ok(s.run("family.place", {"id": WIN, "family": "window.fixed", "host": F.W3, "offset": 1500}))
    ok(s.run("family.place", {"id": COL, "family": "column.section", "type_values": {"section": "round", "width": 500, "depth": 500},
                              "level": F.LV1, "position": [3000, 2000]}))
    return s


@pytest.fixture
def vb(base):
    return build(base)


def dim(s, **kw):
    return ok(s.run("annot.dim.create", {"view": V, **kw}))["id"]


def items(s):
    return {i["id"]: i for i in ok(s.run("annot.list", {"limit": 1000}))["items"]}


def draws(s):
    s.eng.sync()
    return {it[1]: it for it in s.eng.g.q(AN.view_annots, V)}


# ---------------------------------------------------------------- 연관 치수(ANN-02)
def test_통심선_치수는_통심선을_옮기면_따라간다(vb):
    d = dim(vb, a=f"grid:{G1}#line", b=f"wall:{F.W2}#axis")
    s = dim(vb, a=f"grid:{G1}#start", b=f"grid:{G1}#end")
    assert items(vb)[d]["value"] == 7000.0 and items(vb)[s]["value"] == 8000.0
    ok(vb.run("grid.move", {"id": G1, "dx": -500, "dy": 0}))
    assert items(vb)[d]["value"] == 7500.0
    ok(vb.run("wall.move", {"id": F.W2, "dx": 300, "dy": 0}))
    assert items(vb)[d]["value"] == 7800.0
    ok(vb.run("grid.delete", {"id": G1}))
    assert items(vb)[d]["status"] == "orphan" and items(vb)[d]["reasons"] == [{"ref": "a", "why": "target_deleted"}]


def test_각도_치수는_벽과_통심선_사이를_재고_따라간다(vb):
    a = dim(vb, a=f"wall:{F.W1}#axis", b=f"grid:{G2}#line", measure="angle", offset=1000)
    it = items(vb)[a]
    assert it["measure"] == "angle" and it["value"] == pytest.approx(45.0)
    dr = draws(vb)[a]
    assert dr[0] == "adim" and dr[4] == "45°" and len(dr[2]) == 2 + AN.ARC_N
    assert dr[2][0][:2] == (0.0, 0.0)                            # 꼭짓점 = 벽 축선(y=0)과 통심선(y=x)의 교점
    ok(vb.run("wall.set", {"id": F.W1, "axis": [0, 0, 6000, 6000 * 0.5773502691896257]}))   # 벽을 30도로 세운다
    assert items(vb)[a]["value"] == pytest.approx(15.0)
    assert draws(vb)[a][4] == "15°"
    r = vb.run("annot.dim.create", {"view": V, "a": f"wall:{F.W1}#face.left", "b": f"wall:{F.W1}#face.right", "measure": "angle"})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT" and "parallel" in r["error"]["message"]
    r = vb.run("annot.dim.create", {"view": V, "a": f"wall:{F.W1}#axis.start", "b": f"grid:{G2}#line", "measure": "angle"})
    assert not r["ok"] and "선 참조" in r["error"]["message"]


def test_창_치수는_벽을_옮기거나_창을_옮겨도_따라간다(vb):
    jw = dim(vb, a=f"finst:{WIN}#jamb.start", b=f"finst:{WIN}#jamb.end")
    to = dim(vb, a=f"wall:{F.W2}#axis", b=f"finst:{WIN}#center")
    w0 = items(vb)[jw]["value"]
    assert w0 > 0 and items(vb)[to]["value"] == 1500.0           # W3 는 (6000,4000)->(0,4000): 삽입점 x = 4500
    ok(vb.run("family.set", {"id": WIN, "offset": 2500}))
    assert items(vb)[to]["value"] == 2500.0 and items(vb)[jw]["value"] == w0
    ok(vb.run("wall.move", {"id": F.W3, "dx": 0, "dy": 500}))
    assert items(vb)[to]["value"] == 2500.0
    assert draws(vb)[to][2][1] == 4500.0                          # 보조선 시작점이 옮긴 벽(y=4500) 위에 있다
    r = vb.run("annot.dim.create", {"view": V, "a": f"finst:{COL}#jamb.start", "b": f"finst:{COL}#center"})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"


def test_둥근_기둥의_반지름_지름_치수와_고아(vb):
    r = dim(vb, a=f"finst:{COL}#circle", measure="radius")
    d = dim(vb, a=f"finst:{COL}#circle", b=f"finst:{COL}#circle", measure="diameter")
    c = dim(vb, a=f"finst:{COL}#center", b=f"wall:{F.W1}#axis")
    assert items(vb)[r]["value"] == 250.0 and items(vb)[d]["value"] == 500.0 and items(vb)[c]["value"] == 2000.0
    dr = draws(vb)
    assert dr[r][4] == "R250" and dr[d][4] == "Ø500" and dr[r][2][0][:2] == (3000.0, 2000.0)
    ok(vb.run("family.set", {"id": COL, "position": [3500, 1000]}))
    assert draws(vb)[r][2][0][:2] == (3500.0, 1000.0) and items(vb)[c]["value"] == 1000.0
    bad = vb.run("annot.dim.create", {"view": V, "a": f"finst:{COL}#circle", "b": f"wall:{F.W1}#axis"})
    assert not bad["ok"] and "measure" in bad["error"]["message"]
    bad = vb.run("annot.dim.create", {"view": V, "a": f"wall:{F.W1}#axis", "measure": "angle"})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"
    ok(vb.run("element.delete", {"id": COL}))
    assert items(vb)[r]["status"] == "orphan" and items(vb)[c]["status"] == "orphan"
    ok(vb.run("history.undo", {}))
    assert items(vb)[r]["status"] == "ok"


def test_다시_묶기와_방향_경고(vb):
    a = dim(vb, a=f"wall:{F.W1}#axis", b=f"grid:{G2}#line", measure="angle")
    ok(vb.run("grid.set", {"id": G2, "line": [8000, 8000, -2000, -2000]}))   # 통심선 방향을 뒤집으면 각도가 180 - θ
    it = items(vb)[a]
    assert it["value"] == pytest.approx(135.0) and it["warnings"] == ["direction_changed:b"]
    ok(vb.run("annot.rebind", {"id": a, "b": f"grid:{G2}#line"}))
    assert "warnings" not in items(vb)[a]
    r = dim(vb, a=f"finst:{COL}#circle", measure="radius")
    ok(vb.run("annot.rebind", {"id": r, "a": f"finst:{COL}#circle"}))
    assert items(vb)[r]["value"] == 250.0


# ---------------------------------------------------------------- 태그(ANN-06·08)
def test_창_기둥_지붕_난간_태그와_다중_범주(vb):
    ok(vb.run("railing.create", {"id": F.RL1, "level": F.LV1, "path": [[0, -500], [3000, -500]], "height": 1100}))
    t1 = ok(vb.run("annot.tag.create", {"view": V, "target": WIN, "fields": ["category", "width", "host"]}))["id"]
    t2 = ok(vb.run("annot.tag.create", {"view": V, "target": COL, "fields": ["name", "category", "width", "level"]}))["id"]
    t3 = ok(vb.run("annot.tag.create", {"view": V, "target": F.RL1, "fields": ["ifc_class", "level"]}))["id"]
    t4 = ok(vb.run("annot.tag.create", {"view": V, "target": F.W1, "fields": ["ifc_class", "material"]}))["id"]
    lst = items(vb)
    w = vb.run("element.get", {"id": WIN})["result"]["params"]["width"]
    host = vb.run("element.get", {"id": F.W3})["result"]["params"]["name"]
    assert w == 1200 and lst[t1]["text"] == f"창 1200 {host}"
    assert lst[t2]["text"] == f"C-{COL[-6:]} 기둥 - 1F" and lst[t3]["text"] == "IfcRailing 1F" and lst[t4]["text"] == "IfcWall Concrete"
    anc = {it[1]: it[2] for it in draws(vb).values() if it[0] == "tag"}
    assert anc[t2] == (3000.0, 2000.0) and anc[t3] == (1500.0, -500.0)
    ok(vb.run("annot.rebind", {"id": t4, "target": COL}))       # 다중 범주 필드만 쓰는 태그는 다른 범주로 다시 묶어도 그대로 쓴다
    assert items(vb)[t4]["text"] == "IfcColumn -"
    r = vb.run("annot.tag.create", {"view": V, "target": F.RL1, "fields": ["width"]})
    assert not r["ok"] and "ifc_class" in r["error"]["hint"]


def test_범주별_일괄_태그는_빠진_것만_단다(vb):
    one = ok(vb.run("annot.tag.create", {"view": V, "target": F.W1}))["id"]
    r = ok(vb.run("annot.tag.all", {"view": V, "kinds": ["wall", "door", "finst"], "fields": ["name", "ifc_class"]}))
    tgt = {t["target"] for t in r["tags"]}
    assert tgt == {F.W2, F.W3, F.D1, F.D2, WIN, COL} and one not in r["created"]
    again = vb.run("annot.tag.all", {"view": V, "kinds": ["wall", "door", "finst"], "fields": ["name"]})
    assert again["ok"] and again["result"]["created"] == []
    bad = vb.run("annot.tag.all", {"view": V, "kinds": ["wall", "finst"], "fields": ["thickness"]})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"
    ok(vb.run("history.undo", {}))                            # 아무것도 만들지 않은 실행은 이력이 없다: 한 번이면 일괄 태그 전체가 돌아간다
    assert set(items(vb)) == {one}


# ---------------------------------------------------------------- 출력·증분
def test_SVG_PDF_DXF_시트_출력과_되돌리면_바이트가_같다(vb):
    def exp(name):
        assert destructive(vb, "view.export", {"id": V, "path": name, "overwrite": True})["ok"]
        return (vb.path.parent / name).read_bytes()

    before_svg, before_dxf = exp("a.svg"), exp("a.dxf")
    a = dim(vb, a=f"wall:{F.W1}#axis", b=f"grid:{G2}#line", measure="angle")
    dim(vb, a=f"finst:{COL}#circle", measure="diameter")
    svg = exp("b.svg").decode("utf-8")
    assert f'<g class="dim" data-id="{a}">' in svg and ">45°<" in svg and ">Ø500<" in svg
    assert exp("b.pdf").startswith(b"%PDF")
    assert "Ø500" in exp("b.dxf").decode("utf-8")
    ok(vb.run("sheet.define", {"id": F.SH1, "name": "A-101", "views": [V], "paper": "A3", "project_name": "예시", "sheet_number": "A-101", "date": "2026-01-01"}))
    out = destructive(vb, "sheet.render", {"id": F.SH1, "path": "s.svg", "overwrite": True})
    assert out["ok"] and ">45°<" in (vb.path.parent / "s.svg").read_text(encoding="utf-8")
    for _ in range(3):
        ok(vb.run("history.undo", {}))
    assert exp("c.svg") == before_svg and exp("c.dxf") == before_dxf


def test_증분과_전체_계산이_같다(vb):
    dim(vb, a=f"wall:{F.W1}#axis", b=f"grid:{G2}#line", measure="angle")
    dim(vb, a=f"finst:{COL}#circle", measure="radius")
    dim(vb, a=f"finst:{WIN}#jamb.start", b=f"grid:{G1}#line")
    ok(vb.run("annot.tag.create", {"view": V, "target": COL, "fields": ["name", "ifc_class"]}))
    vb.eng.sync()
    vb.eng.g.q(AN.annot_bundle)
    ok(vb.run("grid.move", {"id": G2, "dx": 100, "dy": 0}))
    ok(vb.run("family.set", {"id": COL, "position": [2000, 1500]}))
    ok(vb.run("family.set", {"id": WIN, "offset": 2000}))
    vb.eng.sync()
    inc = vb.eng.g.q(AN.annot_bundle)
    full = s3lib.RefGraph(s3lib._Inputs(vb.eng)).q(AN.annot_bundle)
    assert inc == full and all(v[1] == "ok" for v in inc["state"].values())


# ---------------------------------------------------------------- IFC 왕복
def test_IFC_왕복은_새_치수와_태그를_되살리고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        build(b)
        ok(b.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200}))
        ok(b.run("railing.create", {"id": F.RL1, "level": F.LV1, "path": [[0, -500], [3000, -500]], "height": 1100}))
        args = [{"a": f"wall:{F.W1}#axis", "b": f"grid:{G2}#line", "measure": "angle", "offset": -800},
                {"a": f"finst:{COL}#circle", "measure": "radius"},
                {"a": f"finst:{WIN}#jamb.start", "b": f"finst:{WIN}#jamb.end"},
                {"a": f"grid:{G1}#line", "b": f"finst:{COL}#center"}]
        ids = [ok(b.run("annot.dim.create", {"view": V, **a}))["id"] for a in args]
        ids += ok(b.run("annot.tag.all", {"view": V, "kinds": ["slab", "finst", "railing"], "fields": ["name", "ifc_class"]}))["created"]
        assert len(ids) == 8
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        got = {i["id"]: i for i in b.run("annot.list", {"limit": 100}, project="imp.dobim")["result"]["items"]}
        src = {i["id"]: i for i in b.run("annot.list", {"limit": 100})["result"]["items"]}
        assert got == src and all(src[i]["status"] == "ok" for i in ids)
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == (tmp_path / "t.ifc").read_bytes()
    finally:
        b.close()


def test_변조한_치수_정의는_되살리지_않는다():
    from dobim_core import viewdefs as VD
    p = {"name": "D", "view": V, "a": f"wall:{F.W1}#axis", "b": f"grid:{G1}#line", "off": 600.0, "home": [0.0, 0.0],
         "dirs": {"a": None, "b": None}, "measure": "angle"}
    eid = "5" * 22
    VD.parse_def(eid, VD.def_text(eid, {"kind": "dim", "family_id": "annot.dim", "params": p}))
    for bad in ({"measure": "radius"}, {"measure": "arc"}, {"b": f"finst:{COL}#circle", "measure": None}):
        q = {k: v for k, v in {**p, **bad}.items() if v is not None}
        with pytest.raises(ValueError):
            VD.parse_def(eid, VD.def_text(eid, {"kind": "dim", "family_id": "annot.dim", "params": q}))
