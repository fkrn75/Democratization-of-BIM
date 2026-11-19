# SPDX-License-Identifier: MPL-2.0
"""W1 1-B 3차: 리비전(ANN-17)·리비전 일정과 시트 발행(DOC-15)·키노트(ANN-09).
- 프로젝트 리비전: 시트 개정란은 리비전을 싣는 시트와 올린 뷰에 같은 표기의 구름이 있는 시트에 리비전을 적는다. 발행하면 고칠 수 없다
- 리비전 일정표(revision.schedule·sheet.index revisions), 시트를 지우면 리비전에서 빠진다
- 키노트 표·요소 키노트(타입 상속)·키노트 태그·범례(keynote.legend·sheet.index keynotes)
- IFC 왕복(DoBIM_Defs·속성셋)·되돌리면 바이트가 같다. 합성 데이터만 쓴다."""
import sys

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

V = F.V1
V2 = "4" + "0" * 20 + "9"
COL = "0" * 21 + "3"


def ok(r):
    assert r["ok"], r
    return r["result"]


def destructive(s, name, args):
    r = s.run(name, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


def build(s):
    ok(s.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.create", {"id": V2, "name": "2F 평면", "type": "plan", "level": F.LV2, "cut_height": 1200}))
    ok(s.run("sheet.define", {"id": F.SH1, "name": "A-101", "views": [V], "paper": "A3", "project_name": "예시", "sheet_number": "A-101", "date": "2026-01-01"}))
    return s


def svg_of(s, sid, name):
    assert destructive(s, "sheet.render", {"id": sid, "path": name, "overwrite": True})["ok"]
    return (s.path.parent / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 리비전(ANN-17·DOC-15)
def test_리비전은_싣는_시트와_구름이_있는_시트의_개정란에_오른다(base):
    s = build(base)
    sh2 = ok(s.run("sheet.define", {"name": "A-102", "views": [V2], "paper": "A3", "sheet_number": "A-102", "date": "2026-01-01"}))["id"]
    before = svg_of(s, F.SH1, "a.svg")
    r1 = ok(s.run("revision.create", {"date": "2026-02-01", "desc": "문 위치 변경", "sheets": [F.SH1]}))
    r2 = ok(s.run("revision.create", {"mark": "B", "date": "2026-03-01", "desc": "2층 벽 변경"}))
    assert (r1["seq"], r1["mark"], r2["seq"], r2["mark"]) == (1, "1", 2, "B")
    one = svg_of(s, F.SH1, "b.svg")
    assert one != before and 'data-count="1"' in one and ">2026-02-01<" in one and ">2026-03-01<" not in one
    ok(s.run("draft.create", {"view": V2, "shape": "cloud", "pts": [[0, 0], [2000, 0], [2000, 1000]], "text": "B"}))   # 2층 뷰의 B 구름
    two = svg_of(s, sh2, "c.svg")
    assert ">2026-03-01<" in two and ">2026-02-01<" not in two
    sc = ok(s.run("revision.schedule", {}))
    assert [(i["mark"], [(x["id"], x["via"]) for x in i["sheets"]]) for i in sc["items"]] == [("1", [(F.SH1, "listed")]), ("B", [(sh2, "cloud")])]
    assert {x["id"]: x["current"] for x in sc["sheets"]} == {F.SH1: "1", sh2: "B"}
    bad = s.run("revision.create", {"mark": "B", "date": "2026-03-02"})
    assert not bad["ok"] and bad["error"]["code"] == "CONSTRAINT"
    for _ in range(3):
        ok(s.run("history.undo", {}))
    assert svg_of(s, F.SH1, "d.svg") == before           # 리비전이 없으면 시트는 이전과 바이트까지 같다


def test_발행한_리비전은_고치거나_지울_수_없고_발행을_풀면_된다(base):
    s = build(base)
    rid = ok(s.run("revision.create", {"id": F.RV1, "date": "2026-02-01", "desc": "초안", "sheets": [F.SH1]}))["id"]
    out = ok(s.run("revision.issue", {"id": rid, "issued_to": "건축주", "issued_by": "설계팀", "date": "2026-02-05"}))
    assert out == {"id": rid, "issued": True, "sheets": [F.SH1]}
    for name, args in (("revision.set", {"id": rid, "desc": "x"}), ("revision.delete", {"id": rid}), ("revision.issue", {"id": rid})):
        r = s.run(name, args)
        assert not r["ok"] and r["error"]["code"] == "CONSTRAINT", (name, r)
    it = ok(s.run("revision.schedule", {}))["items"][0]
    assert (it["issued"], it["issued_to"], it["issued_by"], it["date"]) == (True, "건축주", "설계팀", "2026-02-05")
    ok(s.run("revision.issue", {"id": rid, "issued": False}))
    ok(s.run("revision.set", {"id": rid, "desc": "고침"}))
    assert ok(s.run("revision.schedule", {}))["items"][0]["desc"] == "고침"


def test_시트를_지우면_리비전에서_빠지고_되돌리면_돌아온다(base):
    s = build(base)
    rid = ok(s.run("revision.create", {"date": "2026-02-01", "sheets": [F.SH1]}))["id"]
    out = ok(s.run("sheet.delete", {"id": F.SH1}))
    assert out["revisions_updated"] == [rid]
    assert ok(s.run("element.get", {"id": rid}))["params"]["sheets"] == []
    ok(s.run("history.undo", {}))
    assert ok(s.run("element.get", {"id": rid}))["params"]["sheets"] == [F.SH1]
    bad = s.run("revision.create", {"date": "2026-02-01", "sheets": [V]})
    assert not bad["ok"]


def test_색인_시트의_리비전_일정표와_키노트_범례(base):
    s = build(base)
    args = {"path": "i.svg", "paper": "A1", "date": "2026-01-01", "overwrite": True}
    before = destructive(s, "sheet.index", args)
    assert "revisions" not in before["result"]["tables"] and "keynotes" not in before["result"]["tables"]
    plain = (s.path.parent / "i.svg").read_bytes()
    ok(s.run("revision.create", {"date": "2026-02-01", "desc": "문 위치 변경", "sheets": [F.SH1], "issued_to": "건축주"}))
    ok(s.run("keynote.set", {"key": "03.30.00", "text": "현장타설 콘크리트"}))
    ok(s.run("keynote.assign", {"ids": [F.W1], "key": "03.30.00"}))
    ok(s.run("annot.tag.create", {"view": V, "target": F.W1, "fields": ["keynote"]}))
    out = destructive(s, "sheet.index", args)["result"]
    assert out["rows"]["revisions"] == 1 and out["rows"]["keynotes"] == 1
    svg = (s.path.parent / "i.svg").read_text(encoding="utf-8")
    assert 'id="revision-schedule"' in svg and ">건축주<" in svg and ">A-101<" in svg
    assert 'id="keynote-legend"' in svg and ">현장타설 콘크리트<" in svg
    for _ in range(4):
        ok(s.run("history.undo", {}))
    destructive(s, "sheet.index", args)
    assert (s.path.parent / "i.svg").read_bytes() == plain


# ---------------------------------------------------------------- 키노트(ANN-09)
def test_키노트_태그와_타입_상속과_범례(base):
    s = build(base)
    ok(s.run("family.place", {"id": COL, "family": "column.section", "type_values": {"section": "round", "width": 500, "depth": 500},
                              "level": F.LV1, "position": [3000, 2000]}))
    ok(s.run("keynote.set", {"key": "03.30.00", "text": "현장타설 콘크리트"}))
    ok(s.run("keynote.set", {"key": "05.12.00", "text": "구조용 강재 기둥"}))
    assert ok(s.run("keynote.set", {"key": "05.12.00", "text": "강재 기둥"}))["created"] is False
    bad = s.run("keynote.assign", {"ids": [F.W1], "key": "99"})
    assert not bad["ok"] and bad["error"]["code"] == "NOT_FOUND"
    assert not s.run("keynote.assign", {"ids": [V], "key": "03.30.00"})["ok"]
    ok(s.run("keynote.assign", {"ids": [F.W1, F.W2], "key": "03.30.00"}))
    ftype = ok(s.run("element.get", {"id": COL}))["relations"]["of_type"][0]
    ok(s.run("keynote.assign", {"ids": [ftype], "key": "05.12.00"}))       # 타입에 달면 인스턴스가 따른다
    tags = [ok(s.run("annot.tag.create", {"view": V, "target": t, "fields": ["keynote"]}))["id"] for t in (F.W1, F.W2, COL, F.W3)]
    txt = {i["id"]: i["text"] for i in ok(s.run("annot.list", {"limit": 100}))["items"]}
    assert [txt[t] for t in tags] == ["03.30.00", "03.30.00", "05.12.00", "-"]
    lg = ok(s.run("keynote.legend", {"sheet": F.SH1}))
    assert lg["legend"] == [{"key": "03.30.00", "text": "현장타설 콘크리트", "tags": 2}, {"key": "05.12.00", "text": "강재 기둥", "tags": 1}]
    assert [r["key"] for r in lg["table"]] == ["03.30.00", "05.12.00"]
    assert ok(s.run("keynote.legend", {"view": V2}))["legend"] == []
    ok(s.run("keynote.assign", {"ids": [COL], "key": "03.30.00"}))       # 인스턴스 값이 타입보다 앞선다
    ok(s.run("keynote.delete", {"key": "05.12.00"}))
    txt = {i["id"]: i["text"] for i in ok(s.run("annot.list", {"limit": 100}))["items"]}
    assert txt[tags[2]] == "03.30.00"
    ok(s.run("keynote.assign", {"ids": [F.W1], "key": None}))
    assert {i["id"]: i["text"] for i in ok(s.run("annot.list", {"limit": 100}))["items"]}[tags[0]] == "-"


# ---------------------------------------------------------------- IFC 왕복
def test_IFC_왕복은_리비전과_키노트를_되살리고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        build(b)
        ok(b.run("revision.create", {"id": F.RV1, "date": "2026-02-01", "desc": "문 위치 변경", "sheets": [F.SH1]}))
        ok(b.run("revision.issue", {"id": F.RV1, "issued_to": "건축주"}))
        ok(b.run("revision.create", {"mark": "B", "date": "2026-03-01"}))
        ok(b.run("draft.create", {"view": V, "shape": "cloud", "pts": [[0, 0], [2000, 0], [2000, 1000]], "text": "B"}))
        ok(b.run("keynote.set", {"key": "03.30.00", "text": "현장타설 콘크리트"}))
        ok(b.run("keynote.assign", {"ids": [F.W1], "key": "03.30.00"}))
        ok(b.run("annot.tag.create", {"view": V, "target": F.W1, "fields": ["keynote", "name"]}))
        ok(b.run("site.create", {"id": F.SI1, "boundary": [[-6000, -6000], [18000, -6000], [18000, 14000], [-6000, 14000]], "north_angle": 30,
                                 "survey": [200000000, 450000000.5]}))
        ok(b.run("draft.create", {"view": V, "shape": "spot_coord", "pts": [[1200, -3400], [2000, -2000]]}))
        ok(b.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200, "top_offset": 50}))
        ok(b.run("draft.create", {"view": V, "shape": "spot_elev", "pts": [[1000, 1000], [1500, 1500]]}))
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("revision.schedule", {}, project="imp.dobim")["result"] == b.run("revision.schedule", {})["result"]
        assert b.run("keynote.legend", {}, project="imp.dobim")["result"] == b.run("keynote.legend", {})["result"]
        assert b.run("draft.list", {"limit": 100}, project="imp.dobim")["result"] == b.run("draft.list", {"limit": 100})["result"]
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == (tmp_path / "t.ifc").read_bytes()
    finally:
        b.close()


def test_변조하거나_겹친_리비전_정의는_되살리지_않는다():
    import pytest

    from dobim_core import viewdefs as VD
    p = {"name": "1", "seq": 1, "date": "2026-02-01", "desc": "", "issued_to": "", "issued_by": "", "issued": False, "sheets": []}
    eid = "9" * 22
    VD.parse_def(eid, VD.def_text(eid, {"kind": "revision", "family_id": "revision.basic", "params": p}))
    for bad in ({"seq": 0}, {"seq": 1.0}, {"date": "2026/02/01"}, {"name": " 1"}, {"issued": 1}, {"sheets": [V, V]}, {"extra": 1}):
        with pytest.raises(ValueError):
            VD.parse_def(eid, VD.def_text(eid, {"kind": "revision", "family_id": "revision.basic", "params": {**p, **bad}}))
    with pytest.raises(ValueError):
        VD.parse_def(eid, VD.def_text(eid, {"kind": "keynote", "family_id": "keynote.basic", "params": {"name": "a b", "text": ""}}))


# ---------------------------------------------------------------- 표면 표고·측량 좌표(ANN-04)
def _texts(s, eid):
    from dobim_core import drafting as DR
    s.eng.sync()
    return [it[7] for it in s.eng.g.q(DR.draft_items, eid) if it[0] == "text"]


def test_평면_표고는_슬래브_지붕_윗면을_재고_따라간다(base):
    s = build(base)
    e = ok(s.run("draft.create", {"view": V, "shape": "spot_elev", "pts": [[1000, 1000], [1500, 1500]]}))["id"]
    out = ok(s.run("draft.create", {"view": V, "shape": "spot_elev", "pts": [[9000, 9000], [9500, 9500]]}))["id"]
    assert _texts(s, e) == ["EL+0"]                                              # 덮는 면이 없으면 레벨 높이
    ok(s.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200, "top_offset": 50}))
    assert _texts(s, e) == ["EL+50"] and _texts(s, out) == ["EL+0"]
    ok(s.run("slab.set", {"id": F.S1, "top_offset": -30}))
    assert _texts(s, e) == ["EL-30"]
    ok(s.run("slab.set", {"id": F.S1, "openings": [[[500, 500], [1500, 500], [1500, 1500], [500, 1500]]]}))
    assert _texts(s, e) == ["EL+0"]                                              # 구멍 안은 슬래브가 덮지 않는다
    ok(s.run("slab.move", {"id": F.S1, "dx": 2000, "dy": 0}))
    assert _texts(s, e) == ["EL+0"]
    ok(s.run("roof.create", {"id": F.RF1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "form": "shed", "dir": 0,
                             "pitch": 5, "overhang": 0, "base_offset": 600}))
    z = float(_texts(s, e)[0][2:].replace(",", ""))
    assert 600 < z < 1200                                                     # 외쪽 지붕 면(물이 +x 로 흐른다): 처마(600)보다 높고 절단면(1200) 아래
    for _ in range(5):
        ok(s.run("history.undo", {}))
    assert _texts(s, e) == ["EL+0"]


def test_측량_기준점이_있으면_좌표_기호는_측량_좌표(base):
    s = build(base)
    c = ok(s.run("draft.create", {"view": V, "shape": "spot_coord", "pts": [[1200, -3400], [2000, -2000]]}))["id"]
    assert _texts(s, c) == ["N -3,400", "E 1,200"]
    ok(s.run("site.create", {"id": F.SI1, "boundary": [[-6000, -6000], [18000, -6000], [18000, 14000], [-6000, 14000]], "north_angle": 0}))
    assert _texts(s, c) == ["N -3,400", "E 1,200"]                            # 측량 기준점이 없으면 프로젝트 원점 기준
    ok(s.run("site.set", {"id": F.SI1, "survey": [200000000, 450000000]}))
    assert _texts(s, c) == ["N 449,996,600", "E 200,001,200"]
    ok(s.run("site.set", {"id": F.SI1, "north_angle": 90}))                   # 정북이 도면 오른쪽: 도면 +x 가 N, -y 가 E
    assert _texts(s, c) == ["N 450,001,200", "E 200,003,400"]
    ok(s.run("site.set", {"id": F.SI1, "clear": ["survey"]}))
    assert _texts(s, c) == ["N -3,400", "E 1,200"]
    assert not s.run("site.set", {"id": F.SI1, "survey": [1, 2, 3]})["ok"]
