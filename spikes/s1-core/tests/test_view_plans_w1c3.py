# SPDX-License-Identifier: MPL-2.0
"""W1 1-C 3차: 반사 천장 평면(VIEW-02), 범례 뷰(VIEW-09), 면적 평면(VIEW-21).
- 모드가 없는 평면은 바이트까지 그대로다(새 뷰를 만들고 되돌려도)
- 반사 천장 평면: 절단선은 평면과 같고 문·창 기호가 없다. 투영은 절단면 위(위층 슬래브 밑면·구멍, 천장선과 'CH 높이')만
- 면적 평면: gross = 벽 윤곽 합집합 바깥선의 면적, rooms = 공간별 면적과 합계. 공간 이름·면적 문자는 면적 항목이 대신한다
- 범례 뷰: 문(폭·높이별)·패밀리 유형 기호, 재료 해치 견본. 시트·PNG·DXF·PDF·GUI 장면. IFC 왕복(DoBIM_Defs)·되돌리기. 합성 데이터만 쓴다."""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

V = F.V1
W4 = "1" + "0" * 20 + "4"
BOX = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r, code="CONSTRAINT"):
    assert not r["ok"] and r["error"]["code"] == code, r
    return r["error"]


def export(s, vid, name):
    r = s.run("view.export", {"id": vid, "path": name, "overwrite": True})
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = s.run("view.export", {"id": vid, "path": name, "overwrite": True}, confirm=r["error"]["details"]["confirm_token"])
    ok(r)
    p = s.path.parent / name
    return p.read_text(encoding="utf-8") if name.endswith(".svg") else p.read_bytes()


def close_box(s):
    """기준 프로젝트의 열린 변을 닫고 모서리를 잇는다(방 하나)."""
    ok(s.run("wall.create", {"id": W4, "level": F.LV1, "axis": [0, 4000, 0, 0], "thickness": 200, "height": 3000}))
    for a, b in ((F.W2, F.W3), (F.W3, W4), (W4, F.W1)):
        ok(s.run("join.set", {"a": a, "b": b}))


def group(svg, gid):
    if f'id="{gid}"' not in svg:
        return ""
    i = svg.index(f'id="{gid}"')
    return svg[i:svg.index("</g>", i)]


def test_모드가_없는_평면은_바이트가_같고_되돌리면_돌아온다(base):
    s = base
    ok(s.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    plain = export(s, V, "a.svg")
    ok(s.run("view.rcp", {"id": F.VR1, "name": "1F 반사 천장", "level": F.LV1}))
    ok(s.run("view.area_plan", {"id": F.VA1, "name": "1F 바닥면적", "level": F.LV1, "scheme": "gross"}))
    ok(s.run("view.legend", {"id": F.VL1, "name": "창호 범례", "content": "symbols"}))
    assert export(s, V, "b.svg") == plain
    for _ in range(3):
        ok(s.run("history.undo", {}))
    assert F.VR1 not in s.eng.st.el and export(s, V, "c.svg") == plain


def test_반사_천장_평면(base):
    s = base
    ok(s.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": BOX, "thickness": 200, "openings": [[[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]]}))
    ok(s.run("slab.create", {"level": F.LV1, "boundary": BOX, "thickness": 200}))   # 발밑 슬래브: 반사 천장 평면에는 보이지 않는다
    ok(s.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": BOX, "name": "사무실", "category": "사무실", "ceiling_height": 2700}))
    ok(s.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.rcp", {"id": F.VR1, "name": "1F 반사 천장", "level": F.LV1}))
    plan, rcp = export(s, V, "p.svg"), export(s, F.VR1, "r.svg")
    assert "반사 천장 평면도" in rcp and "반사 천장" not in plan
    assert group(plan, "lines-symbol") and group(rcp, "lines-symbol").count("<line") == 2   # 평면의 문 호 대신 위층 바닥 구멍의 X(대각선 2개)
    assert "CH 2,700" in rcp and "24.00㎡" not in rcp and "FD " not in rcp and "FD " in plan
    pj = group(rcp, "lines-proj")
    assert pj.count("<line") >= 8   # 위층 슬래브 밑면 윤곽·구멍 4변 + 천장선
    lw = ok(s.run("view.linework", {"id": F.VR1}))
    assert lw["summary"]["segments"] > 0
    # 절단면 위가 비어 있으면 절단선만 남는다(천장 높이 아래로 절단면을 올리면 천장선·CH 가 빠진다)
    ok(s.run("view.set", {"id": F.VR1, "cut_height": 2800, "view_depth": 100}))
    r2 = export(s, F.VR1, "r2.svg")
    assert "CH 2,700" not in r2 and "사무실" in r2
    # 콜아웃은 반사 천장 평면 설정을 그대로 가진다
    ok(s.run("view.callout", {"id": F.VC1, "parent": F.VR1, "rect": [0, -500, 3000, 500], "name": "C1 천장 상세"}))
    assert s.eng.st.el[F.VC1]["params"]["rcp"] is True
    assert ok(s.run("view.scene", {"id": F.VR1}))   # GUI 장면도 같은 질의를 쓴다
    err(s.run("view.rcp", {"name": "x", "level": F.W1}), "NOT_FOUND")


def test_면적_평면(base):
    s = base
    close_box(s)
    ok(s.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": BOX, "name": "사무실", "category": "사무실"}))
    ok(s.run("view.area_plan", {"id": F.VA1, "name": "1F 바닥면적", "level": F.LV1, "scheme": "gross"}))
    ok(s.run("view.area_plan", {"id": F.VR1, "name": "1F 실면적", "level": F.LV1, "scheme": "rooms"}))
    g = ok(s.run("view.areas", {"id": F.VA1}))
    assert g["scheme"] == "gross" and g["items"] == [{"name": "바닥면적", "area_m2": 26.04}] and g["total_m2"] == 26.04   # 외벽 바깥면 6.2 x 4.2 m
    r = ok(s.run("view.areas", {"id": F.VR1}))
    assert r["items"] == [{"name": "사무실", "area_m2": 24.0}] and r["total_m2"] == 24.0
    gs, rs = export(s, F.VA1, "g.svg"), export(s, F.VR1, "rm.svg")
    assert "면적 평면도" in gs and "26.04㎡" in gs and group(gs, "lines-hatch").count("<line") > 5
    assert "합계 1실 24.00㎡" in rs and rs.count("사무실") == 1   # 공간 문자는 면적 항목이 대신한다(이름이 두 번 나오지 않는다)
    ok(s.run("space.set", {"id": F.SP1, "boundary": [[0, 0], [3000, 0], [3000, 4000], [0, 4000]]}))
    assert ok(s.run("view.areas", {"id": F.VR1}))["total_m2"] == 12.0   # 모델을 따라간다
    ok(s.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": F.LV1}))
    err(s.run("view.areas", {"id": V}))
    err(s.run("view.area_plan", {"name": "x", "level": F.LV1, "scheme": "net"}), "SCHEMA_INVALID")


def test_범례_뷰(base):
    s = base
    ok(s.run("family.place", {"family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 4800}))
    ok(s.run("wall.layers", {"id": F.W2, "layers": [{"material": "벽돌", "thickness": 100}, {"material": "단열재", "thickness": 100}]}))
    ok(s.run("view.legend", {"id": F.VL1, "name": "창호 범례", "content": "symbols"}))
    ok(s.run("view.legend", {"id": F.VA1, "name": "재료 범례", "content": "materials"}))
    sv = export(s, F.VL1, "l.svg")
    assert "문 900×2,100 (1)" in sv and "문 1,000×2,100 (1)" in sv and group(sv, "lines-cut") and group(sv, "lines-symbol")
    assert sv.count("<text") >= 4   # 문 2행 + 패밀리 유형 1행 + 제목
    mv = export(s, F.VA1, "m.svg")
    for m in ("Concrete", "단열재", "벽돌"):
        assert m in mv
    assert group(mv, "lines-hatch").count("<line") > 10
    for name in ("l.png", "l.dxf", "l.pdf"):
        assert len(export(s, F.VL1, name)) > 100
    sc = ok(s.run("view.scene", {"id": F.VL1}))
    assert "view" in str(sc)
    ok(s.run("sheet.define", {"id": F.SH1, "name": "범례", "views": [F.VL1, F.VA1], "paper": "A3", "sheet_number": "A-001", "date": "2026-01-01"}))
    r = s.run("sheet.render", {"id": F.SH1, "path": "sh.svg", "overwrite": True})
    if not r["ok"]:
        r = s.run("sheet.render", {"id": F.SH1, "path": "sh.svg", "overwrite": True}, confirm=r["error"]["details"]["confirm_token"])
    ok(r)
    assert "문 900×2,100 (1)" in (s.path.parent / "sh.svg").read_text(encoding="utf-8")
    # 범례 뷰에는 평면 전용 설정을 쓸 수 없다
    err(s.run("view.set", {"id": F.VL1, "crop": [0, 0, 10, 10]}))
    err(s.run("view.set", {"id": F.VL1, "cut_height": 1500}))
    ok(s.run("view.set", {"id": F.VL1, "scale": 20, "name": "창호 범례 1:20"}))
    ok(s.run("view.template.create", {"id": F.VT1, "name": "T"}))
    err(s.run("view.template.apply", {"view": F.VL1, "template": F.VT1}))
    err(s.run("view.vg.set", {"id": F.VL1, "category": "wall", "hidden": True}))
    err(s.run("view.legend", {"name": "x", "content": "colors"}), "SCHEMA_INVALID")


def test_빈_범례는_안내_문자만(sess):
    ok(sess.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0}))
    ok(sess.run("view.legend", {"id": F.VL1, "name": "빈 범례", "content": "materials"}))
    assert "범례에 넣을 항목이 모델에 없다" in export(sess, F.VL1, "e.svg")


def test_IFC_왕복은_세_뷰를_되살리고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": BOX, "name": "사무실", "category": "사무실", "ceiling_height": 2600}))
        ok(b.run("view.rcp", {"id": F.VR1, "name": "1F 반사 천장", "level": F.LV1, "cut_height": 2400}))
        ok(b.run("view.area_plan", {"id": F.VA1, "name": "1F 실면적", "level": F.LV1, "scheme": "rooms"}))
        ok(b.run("view.legend", {"id": F.VL1, "name": "재료 범례", "content": "materials"}))
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("view.areas", {"id": F.VA1}, project="imp.dobim")["result"] == b.run("view.areas", {"id": F.VA1})["result"]
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == (tmp_path / "t.ifc").read_bytes()
    finally:
        b.close()


def test_변조하거나_범위를_벗어난_정의는_되살리지_않는다():
    from dobim_core import viewdefs as VD
    eid = "9" * 22
    pv = {"name": "R", "type": "plan", "level": F.LV1, "cut_height": 2300, "view_depth": 2000, "scale": 100}
    VD.parse_def(eid, VD.def_text(eid, {"kind": "view", "family_id": "view.basic", "params": {**pv, "rcp": True}}))
    VD.parse_def(eid, VD.def_text(eid, {"kind": "view", "family_id": "view.basic", "params": {**pv, "area": "rooms"}}))
    lg = {"name": "L", "type": "legend", "legend": "symbols", "scale": 50}
    VD.parse_def(eid, VD.def_text(eid, {"kind": "view", "family_id": "view.basic", "params": lg}))
    for bad in ({**pv, "rcp": True, "area": "gross"}, {**pv, "rcp": False}, {**pv, "area": "net"}, {**lg, "legend": "colors"}, {**lg, "level": F.LV1},
                {**lg, "crop": [0, 0, 1, 1]}, {k: x for k, x in lg.items() if k != "legend"}):
        with pytest.raises(ValueError):
            VD.parse_def(eid, VD.def_text(eid, {"kind": "view", "family_id": "view.basic", "params": bad}))
