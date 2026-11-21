# SPDX-License-Identifier: MPL-2.0
"""W1 연장 VIEW-07: 제도 뷰(view.drafting, 뷰 종류 drafting).
- 모델을 자르지 않고 2D 작도(draft.*)만 그린다: 모델 선·레벨 기준선이 없고 작도 선·문자·채움·치수만
- 시트에 올리고 뷰포트 번호·제목을 단다(SVG·PDF·PNG·DXF), GUI 장면(view.scene)에 작도 항목이 나온다
- 다른 뷰의 작도를 draft.copy to_view 로 옮겨 온다
- 모델 치수·높이 치수·가시성·템플릿·평면 전용 설정은 거절, 표고·좌표 기호는 글을 직접 받는다
- IFC 왕복(DoBIM_Defs)·되돌리기, 변조 정의 거절. 합성 데이터만 쓴다."""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

VD = F.VD1


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r, code="CONSTRAINT"):
    assert not r["ok"] and r["error"]["code"] == code, r
    return r["error"]


def _confirm(s, name, args):
    r = s.run(name, args)
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = s.run(name, args, confirm=r["error"]["details"]["confirm_token"])
    return ok(r)


def export(s, vid, name):
    _confirm(s, "view.export", {"id": vid, "path": name, "overwrite": True})
    p = s.path.parent / name
    return p.read_text(encoding="utf-8") if name.endswith(".svg") else p.read_bytes()


def detail(s):
    """창호 상세 하나: 윤곽 폴리선, 채움 영역, 문자, 치수."""
    ok(s.run("view.drafting", {"id": VD, "name": "D-01 창호 상세", "scale": 10}))
    ids = [ok(s.run("draft.create", {"view": VD, "shape": "polyline", "pts": [[0, 0], [600, 0], [600, 200], [0, 200]], "closed": True}))["id"],
           ok(s.run("draft.create", {"view": VD, "shape": "region", "pts": [[0, 0], [600, 0], [600, 50], [0, 50]], "fill": "hatch"}))["id"],
           ok(s.run("draft.create", {"view": VD, "shape": "text", "pts": [[300, 300]], "text": "창틀 상세"}))["id"],
           ok(s.run("draft.create", {"view": VD, "shape": "dim", "pts": [[0, 0], [600, 0]], "off": -150}))["id"]]
    return ids


def test_제도_뷰는_2D_작도만_그린다(base):
    s = base
    ids = detail(s)
    v = ok(s.run("view.list", {"type": "drafting"}))["items"]
    assert [x["id"] for x in v] == [VD] and v[0]["type"] == "drafting" and v[0]["scale"] == 10 and set(v[0]) == {"id", "name", "type", "scale", "cache"}
    lw = ok(s.run("view.linework", {"id": VD}))
    assert lw["items"] == [] and lw["type"] == "drafting"   # 모델 선이 없다(기준 프로젝트에 벽이 있어도)
    sv = export(s, VD, "d.svg")
    assert "창틀 상세" in sv and "600" in sv and "상세도" in sv and "S=1:10" in sv
    assert "1F" not in sv   # 레벨 기준선을 긋지 않는다
    for name in ("d.pdf", "d.png", "d.dxf"):
        assert len(export(s, VD, name)) > 100
    sc = ok(s.run("view.scene", {"id": VD}))
    assert sc["type"] == "drafting" and sorted(it["id"] for it in sc["items"]) == sorted(ids) and {it["kind"] for it in sc["items"]} == {"draft"}
    assert len(ok(s.run("draft.list", {"view": VD}))["items"]) == 4


def test_빈_제도_뷰도_내보낸다(base):
    ok(base.run("view.drafting", {"id": VD, "name": "빈 상세"}))
    sv = export(base, VD, "e.svg")
    assert "빈 상세" in sv and "S=1:20" in sv   # 축척 기본값 1:20


def test_다른_뷰의_작도를_옮겨_오고_시트에_올린다(base):
    s = base
    ok(s.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1}))
    src = ok(s.run("draft.create", {"view": F.V1, "shape": "text", "pts": [[100, 100]], "text": "줄눈 상세"}))["id"]
    ok(s.run("view.drafting", {"id": VD, "name": "D-02 줄눈", "scale": 5}))
    made = ok(s.run("draft.copy", {"ids": [src], "to_view": VD}))["ids"]
    assert ok(s.run("draft.list", {"view": VD}))["items"][0]["id"] == made[0]
    ok(s.run("sheet.define", {"id": F.SH1, "name": "상세", "views": [F.V1, VD], "paper": "A3", "sheet_number": "A-501", "date": "2026-01-01"}))
    ok(s.run("sheet.viewport.set", {"id": F.SH1, "view": VD, "at": [200, 60], "number": "3", "title": "줄눈 상세"}))
    refs = ok(s.run("view.refs", {"id": VD}))
    assert "A-501" in str(refs)
    _confirm(s, "sheet.render", {"id": F.SH1, "path": "sh.svg", "overwrite": True})
    sh = (s.path.parent / "sh.svg").read_text(encoding="utf-8")
    assert sh.count("줄눈 상세") >= 2   # 뷰포트 제목 + 작도 문자
    _confirm(s, "sheet.render", {"id": F.SH1, "path": "sh.pdf", "overwrite": True})
    assert (s.path.parent / "sh.pdf").stat().st_size > 1000
    # 시트가 쓰는 제도 뷰는 지울 수 없다(다른 뷰와 같은 규칙)
    err(s.run("view.delete", {"id": VD}))


def test_모델_전용_설정과_주석은_거절한다(base):
    s = base
    ok(s.run("view.drafting", {"id": VD, "name": "D-03"}))
    err(s.run("view.set", {"id": VD, "cut_height": 1500}))
    err(s.run("view.set", {"id": VD, "crop": [0, 0, 10, 10]}))
    ok(s.run("view.set", {"id": VD, "scale": 5, "name": "D-03 1:5"}))
    err(s.run("annot.dim.create", {"view": VD, "a": f"wall:{F.W1}#axis", "b": f"wall:{F.W2}#axis"}))
    err(s.run("annot.dim.create", {"view": VD, "a": f"level:{F.LV1}#elevation", "b": f"level:{F.LV2}#elevation", "at": [0, 0]}))
    err(s.run("annot.dim.heights", {"view": VD}))
    err(s.run("annot.tag.create", {"view": VD, "target": F.W1}))
    err(s.run("view.vg.set", {"id": VD, "category": "wall", "hidden": True}))
    ok(s.run("view.template.create", {"id": F.VT1, "name": "T"}))
    err(s.run("view.template.apply", {"view": VD, "template": F.VT1}))
    err(s.run("view.callout", {"parent": VD, "rect": [0, 0, 100, 100], "name": "C"}))
    for shape in ("spot_elev", "spot_coord"):   # 모델 좌표가 없어 값을 잴 수 없다: 글을 직접 받는다
        err(s.run("draft.create", {"view": VD, "shape": shape, "pts": [[0, 0], [300, 300]]}))
        ok(s.run("draft.create", {"view": VD, "shape": shape, "pts": [[0, 0], [300, 300]], "text": "EL+3,300"}))
    err(s.run("draft.create", {"view": VD, "shape": "spot_slope", "pts": [[0, 0], [300, -10]]}))
    err(s.run("view.drafting", {"name": "D-03 1:5"}))   # 뷰 이름은 겹칠 수 없다
    err(s.run("view.drafting", {"name": "x", "scale": 0}), "SCHEMA_INVALID")


def test_되돌리면_돌아오고_다시_하면_같다(base):
    s = base
    before = export(s, F.V1, "p0.svg") if ok(s.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1})) else None
    detail(s)
    a = export(s, VD, "a.svg")
    for _ in range(5):
        ok(s.run("history.undo", {}))
    assert ok(s.run("view.list", {"type": "drafting"}))["items"] == []
    assert export(s, F.V1, "p1.svg") == before   # 다른 뷰는 그대로
    for _ in range(5):
        ok(s.run("history.redo", {}))
    assert export(s, VD, "b.svg") == a


def test_IFC_왕복은_제도_뷰와_작도를_되살리고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("view.drafting", {"id": VD, "name": "D-01 창호 상세", "scale": 10}))
        ok(b.run("draft.create", {"view": VD, "shape": "text", "pts": [[0, 0]], "text": "상세"}))
        ok(b.run("draft.create", {"view": VD, "shape": "line", "pts": [[0, 0], [500, 0]], "linetype": "dashed"}))
        ok(b.run("sheet.define", {"id": F.SH1, "name": "상세", "views": [VD], "sheet_number": "A-501", "date": "2026-01-01"}))
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert len(b.run("draft.list", {"view": VD}, project="imp.dobim")["result"]["items"]) == 2
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == (tmp_path / "t.ifc").read_bytes()
    finally:
        b.close()


def test_변조한_제도_뷰_정의는_되살리지_않는다():
    from dobim_core import viewdefs as VDF
    eid = "9" * 22
    good = {"name": "D", "type": "drafting", "scale": 10}
    VDF.parse_def(eid, VDF.def_text(eid, {"kind": "view", "family_id": "view.basic", "params": good}))
    for bad in ({**good, "level": F.LV1}, {**good, "crop": [0, 0, 1, 1]}, {**good, "vg": {}}, {**good, "scale": 0}, {**good, "legend": "symbols"},
                {k: x for k, x in good.items() if k != "scale"}):
        with pytest.raises(ValueError):
            VDF.parse_def(eid, VDF.def_text(eid, {"kind": "view", "family_id": "view.basic", "params": bad}))
