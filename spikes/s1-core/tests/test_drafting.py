# SPDX-License-Identifier: MPL-2.0
"""27단계 2D 작도(설계: docs/2d-editing.md): 뷰 위의 2D 요소(선·폴리선·호·원·문자·치수·채움 영역)·층 스타일 커맨드,
편집(이동·복사·회전·대칭·오프셋·자르기·연장·그룹), 도면 출력(SVG·PDF·시트), 되돌리기, 증분 = 전체 계산, IFC 왕복(DoBIM_Defs).
합성 데이터만 쓴다."""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drafting as DR
from dobim_core import fixture as F
from dobim_core import viewdefs as VD

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402


def gid(c, n):
    return c + f"{n:021d}"


V = F.V1


def ok(r):
    assert r["ok"], r
    return r["result"]


def p_of(s, eid):
    return ok(s.run("element.get", {"id": eid}))["params"]


def destructive(s, name, args):
    r = s.run(name, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


@pytest.fixture
def vb(base):
    ok(base.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    return base


def line(s, n, a, b, **kw):
    return ok(s.run("draft.create", {"id": gid("b", n), "view": V, "shape": "line", "pts": [a, b], **kw}))["id"]


# ---------------------------------------------------------------- 생성·검사
def test_모든_모양을_만들고_그림_항목이_나온다(vb):
    shapes = [
        {"shape": "line", "pts": [[0, -1000], [6000, -1000]]},
        {"shape": "polyline", "pts": [[0, -2000], [1000, -2500], [2000, -2000]], "closed": False},
        {"shape": "arc", "pts": [[3000, -3000]], "r": 500, "a0": 0, "a1": 90},
        {"shape": "circle", "pts": [[4500, -3000]], "r": 400},
        {"shape": "text", "pts": [[1000, 5000]], "text": "상세 참조", "size": 3.0, "align": "middle", "a0": 30},
        {"shape": "region", "pts": [[500, 500], [500, 1500], [2000, 1500], [2000, 500]], "fill": "hatch"},   # 시계 방향으로 줘도 반시계로 저장
        {"shape": "dim", "pts": [[0, -1000], [6000, -1000]], "off": -800},
        {"shape": "region", "pts": [[3000, 500], [4000, 500], [4000, 1500]], "fill": "solid", "color": "#88AAFF"},
    ]
    ids = [ok(vb.run("draft.create", {"view": V, **sh}))["id"] for sh in shapes]
    assert p_of(vb, ids[5])["pts"][0] == [2000.0, 500.0]
    assert p_of(vb, ids[7])["color"] == "#88aaff"
    lst = ok(vb.run("draft.list", {"view": V}))
    assert len(lst["items"]) == 8
    dim = next(i for i in lst["items"] if i["shape"] == "dim")
    assert dim["value"] == 6000.0
    vb.eng.sync()
    items = vb.eng.g.q(DR.view_drafts, V)
    kinds = {it[0] for it in items}
    assert kinds == {"line", "circle", "fill", "text"}
    assert any(it[0] == "text" and it[7] == "6000" for it in items)            # 치수 값 글
    assert any(it[0] == "text" and it[7] == "상세 참조" and it[9] == 30.0 for it in items)
    # 선분 결과(view.linework)는 2D 요소와 무관하다(모델 그림만)
    lw = ok(vb.run("view.linework", {"id": V}))
    assert "draft" not in str(lw)


@pytest.mark.parametrize("args,code", [
    ({"shape": "line", "pts": [[0, 0], [0, 0]]}, "CONSTRAINT"),
    ({"shape": "text", "pts": [[0, 0]], "text": "  "}, "CONSTRAINT"),
    ({"shape": "circle", "pts": [[0, 0]]}, "CONSTRAINT"),
    ({"shape": "line", "pts": [[0, 0], [1, 1]], "fill": "solid"}, "CONSTRAINT"),
    ({"shape": "region", "pts": [[0, 0], [10, 10], [10, 0], [0, 10]]}, "CONSTRAINT"),    # 자기 교차
    ({"shape": "line", "pts": [[0, 0], [1, 1]], "layer": "A/B"}, "SCHEMA_INVALID"),
    ({"shape": "line", "pts": [[0, 0], [1, 1]], "color": "red"}, "SCHEMA_INVALID"),
    ({"shape": "arc", "pts": [[0, 0]], "r": 10, "a0": 30, "a1": 30}, "CONSTRAINT"),
])
def test_잘못된_입력은_거절한다(vb, args, code):
    r = vb.run("draft.create", {"view": V, **args})
    assert not r["ok"] and r["error"]["code"] == code, r


def test_뷰가_아닌_곳에는_못_만든다(vb):
    r = vb.run("draft.create", {"view": F.W1, "shape": "line", "pts": [[0, 0], [1, 1]]})
    assert not r["ok"] and r["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------- 편집
def test_이동_복사_회전_대칭(vb):
    a = line(vb, 1, [0, 0], [1000, 0])
    ok(vb.run("draft.move", {"ids": [a], "dx": 100, "dy": 50}))
    assert p_of(vb, a)["pts"] == [[100.0, 50.0], [1100.0, 50.0]]
    made = ok(vb.run("draft.copy", {"ids": [a], "dy": 1000, "count": 3}))["ids"]
    assert [p_of(vb, m)["pts"][0][1] for m in made] == [1050.0, 2050.0, 3050.0]
    ok(vb.run("draft.rotate", {"ids": [a], "center": [100, 50], "angle": 90}))
    assert p_of(vb, a)["pts"] == [[100.0, 50.0], [100.0, 1050.0]]
    m = ok(vb.run("draft.mirror", {"ids": [a], "axis": [[0, 0], [0, 1]], "keep": True}))["ids"][0]
    assert p_of(vb, m)["pts"] == [[-100.0, 50.0], [-100.0, 1050.0]]
    assert p_of(vb, a)["pts"] == [[100.0, 50.0], [100.0, 1050.0]]
    t = ok(vb.run("draft.create", {"view": V, "shape": "text", "pts": [[0, 0]], "text": "A", "a0": 10}))["id"]
    ok(vb.run("draft.rotate", {"ids": [t], "center": [0, 0], "angle": 20}))
    assert p_of(vb, t)["a0"] == 30.0
    arc = ok(vb.run("draft.create", {"view": V, "shape": "arc", "pts": [[0, 0]], "r": 100, "a0": 0, "a1": 90}))["id"]
    ok(vb.run("draft.mirror", {"ids": [arc], "axis": [[0, 0], [0, 1]]}))   # 세로축 대칭: 0~90 -> 90~180
    p = p_of(vb, arc)
    assert (p["a0"], p["a1"]) == (90.0, 180.0)


def test_오프셋(vb):
    a = line(vb, 1, [0, 0], [1000, 0])
    o = ok(vb.run("draft.offset", {"id": a, "distance": 200}))["id"]
    assert p_of(vb, o)["pts"] == [[0.0, 200.0], [1000.0, 200.0]]                 # 진행 방향 왼쪽이 +
    pl = ok(vb.run("draft.create", {"view": V, "shape": "polyline", "pts": [[0, 0], [1000, 0], [1000, 1000]]}))["id"]
    o = ok(vb.run("draft.offset", {"id": pl, "distance": -100}))["id"]
    assert p_of(vb, o)["pts"] == [[0.0, -100.0], [1100.0, -100.0], [1100.0, 1000.0]]
    c = ok(vb.run("draft.create", {"view": V, "shape": "circle", "pts": [[0, 0]], "r": 300}))["id"]
    assert p_of(vb, ok(vb.run("draft.offset", {"id": c, "distance": 50}))["id"])["r"] == 350.0
    r = vb.run("draft.offset", {"id": c, "distance": -400})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    t = ok(vb.run("draft.create", {"view": V, "shape": "text", "pts": [[0, 0]], "text": "A"}))["id"]
    assert not vb.run("draft.offset", {"id": t, "distance": 10})["ok"]


def test_자르기_연장(vb):
    a = line(vb, 1, [0, 0], [6000, 0])
    b = line(vb, 2, [4000, -500], [4000, 500])
    c = ok(vb.run("draft.create", {"view": V, "shape": "circle", "pts": [[9000, 0]], "r": 1000}))["id"]
    r = ok(vb.run("draft.trim", {"id": a, "boundary": b, "end": "end"}))
    assert r["end_point"] == [4000.0, 0.0]
    r = ok(vb.run("draft.extend", {"id": a, "boundary": c, "end": "end"}))
    assert r["end_point"] == [8000.0, 0.0]                                      # 원과 처음 만나는 곳
    r = vb.run("draft.extend", {"id": a, "boundary": b, "end": "start"})       # 시작 쪽 뒤로는 b 와 만나지 않는다
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    r = ok(vb.run("draft.trim", {"id": a, "boundary": b, "end": "start"}))     # 지금 a 는 0..8000: 시작 쪽을 4000 까지 자른다
    assert r["end_point"] == [4000.0, 0.0] and p_of(vb, a)["pts"] == [[4000.0, 0.0], [8000.0, 0.0]]


def test_상세_그룹과_뷰_삭제(vb):
    ids = [line(vb, n, [0, n * 100], [500, n * 100]) for n in range(1, 4)]
    ok(vb.run("draft.group", {"ids": ids[:2], "group": "상세 A"}))
    ok(vb.run("draft.move", {"view": V, "group": "상세 A", "dx": 1000}))
    assert [p_of(vb, i)["pts"][0][0] for i in ids] == [1000.0, 1000.0, 0.0]
    assert not vb.run("draft.move", {"ids": ids, "view": V, "group": "상세 A", "dx": 1})["ok"]   # 선택은 하나만
    gone = ok(vb.run("view.delete", {"id": V}))["deleted"]
    assert set(ids) <= set(gone)
    ok(vb.run("history.undo", {}))
    assert len(ok(vb.run("draft.list", {}))["items"]) == 3
    ok(vb.run("draft.delete", {"view": V, "group": "상세 A"}))
    assert [i["id"] for i in ok(vb.run("draft.list", {}))["items"]] == [ids[2]]


def test_층_스타일_bylayer(vb):
    a = line(vb, 1, [0, 0], [1000, 0], layer="A-HID")
    vb.eng.sync()
    it = vb.eng.g.q(DR.view_drafts, V)
    assert it == (("line", a, "A-HID", 0.18, "#000000", 0.0, 0.0, 1000.0, 0.0),)   # 층 스타일이 없으면 기본(fine·실선·검정)
    ok(vb.run("draft.layer.set", {"name": "A-HID", "weight": "thick", "linetype": "dashed", "color": "#123456"}))
    vb.eng.sync()
    it = vb.eng.g.q(DR.view_drafts, V)
    assert all(x[3] == 0.5 and x[4] == "#123456" for x in it) and len(it) == 3      # 파선은 마디로 나뉜다(1:100, 3mm 그리기 + 1.5mm 띄우기 = 450mm 주기)
    assert it[0][5:9] == (0.0, 0.0, 300.0, 0.0)
    ok(vb.run("draft.set", {"id": a, "weight": "thin", "linetype": "continuous"}))   # 요소 값이 층보다 먼저
    vb.eng.sync()
    assert vb.eng.g.q(DR.view_drafts, V) == (("line", a, "A-HID", 0.09, "#123456", 0.0, 0.0, 1000.0, 0.0),)
    ok(vb.run("draft.set", {"id": a, "weight": "bylayer"}))
    assert p_of(vb, a)["weight"] is None
    lst = ok(vb.run("draft.layer.list", {}))
    assert lst["items"] == [{"name": "A-HID", "elements": 1, "id": lst["items"][0]["id"], "weight": "thick", "linetype": "dashed", "color": "#123456"}]
    r = ok(vb.run("draft.layer.delete", {"name": "A-HID"}))
    assert r["elements_on_layer"] == 1


# ---------------------------------------------------------------- 출력
def test_SVG_PDF_시트에_그려지고_없으면_바이트가_같다(vb):
    def svg(name):
        assert destructive(vb, "view.export", {"id": V, "path": name, "overwrite": True})["ok"]
        return (vb.path.parent / name).read_bytes()

    before = svg("a.svg")
    t = ok(vb.run("draft.create", {"view": V, "shape": "text", "pts": [[3000, -1500]], "text": "주기 1", "layer": "A-NOTE"}))["id"]
    ok(vb.run("draft.create", {"view": V, "shape": "circle", "pts": [[3000, 6000]], "r": 500}))
    with_d = svg("b.svg").decode("utf-8")
    assert '<g id="drafts"' in with_d and f'data-id="{t}" data-layer="A-NOTE"' in with_d and "주기 1" in with_d
    assert destructive(vb, "view.export", {"id": V, "path": "b.pdf", "overwrite": True})["ok"]
    r = destructive(vb, "sheet.export", {"views": [V], "path": "s.svg", "paper": "A3", "date": "2026-01-01", "overwrite": True})
    assert r["ok"], r
    sh = (vb.path.parent / "s.svg").read_text(encoding="utf-8")
    assert 'id="draft-text" data-layer="A-NOTE"' in sh and 'id="draft-circle" data-layer="A-DRAFT"' in sh
    ok(vb.run("history.undo", {}))
    ok(vb.run("history.undo", {}))
    assert svg("c.svg") == before


def test_증분과_전체_계산이_같다(vb):
    a = line(vb, 1, [0, 0], [1000, 0], layer="L1", linetype="center")
    ok(vb.run("draft.create", {"view": V, "shape": "region", "pts": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]], "fill": "cross", "layer": "L1"}))
    vb.eng.sync()
    vb.eng.g.q(DR.view_drafts, V)
    ok(vb.run("draft.layer.set", {"name": "L1", "linetype": "hidden"}))
    ok(vb.run("draft.move", {"ids": [a], "dx": 10}))
    vb.eng.sync()
    inc = vb.eng.g.q(DR.view_drafts, V)
    full = s3lib.RefGraph(s3lib._Inputs(vb.eng)).q(DR.view_drafts, V)
    assert inc == full and inc


# ---------------------------------------------------------------- IFC 왕복
def test_IFC_왕복은_정의를_되살리고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        plain = b.export("plain.ifc").read_bytes()
        ok(b.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}))
        ok(b.run("draft.layer.set", {"name": "A-NOTE", "weight": "medium", "color": "#204080"}))
        ids = [ok(b.run("draft.create", {"view": V, "shape": "text", "pts": [[0, 0]], "text": "한글 주기", "layer": "A-NOTE", "group": "G"}))["id"],
               ok(b.run("draft.create", {"view": V, "shape": "dim", "pts": [[0, 0], [3000, 0]], "text": "3,000"}))["id"],
               ok(b.run("draft.create", {"view": V, "shape": "arc", "pts": [[0, 0]], "r": 900, "a0": 270, "a1": 45, "linetype": "dashdot"}))["id"]]
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        for i in ids:
            assert b.run("element.get", {"id": i}, project="imp.dobim")["result"]["params"] == b.run("element.get", {"id": i})["result"]["params"]
        # 되살린 프로젝트를 다시 내보내면 같은 바이트
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == (tmp_path / "t.ifc").read_bytes()
        # 2D 요소·층을 모두 지우면 IFC 는 2D 작도 전과 바이트가 같다
        ok(b.run("view.delete", {"id": V}))
        ok(b.run("draft.layer.delete", {"name": "A-NOTE"}))
        assert b.export("t3.ifc").read_bytes() == plain
    finally:
        b.close()


def test_변조된_정의는_되살리지_않는다():
    p = {"name": "DR-1", "view": V, "shape": "line", "pts": [[0.0, 0.0], [1.0, 0.0]], "closed": False, "r": 0.0, "a0": 0.0, "a1": 0.0, "text": "",
         "size": 2.5, "off": 0.0, "fill": "none", "align": "start", "layer": "A-DRAFT", "weight": None, "linetype": None, "color": None, "group": None}
    eid = gid("b", 9)
    txt = VD.def_text(eid, {"kind": "draft", "family_id": "draft.basic", "params": p})
    assert VD.parse_def(eid, txt)["params"] == p
    with pytest.raises(ValueError):
        VD.parse_def(eid, txt.replace("1.0,0.0", "2.0,0.0"))                         # check 불일치
    bad = dict(p, fill="solid")                                                       # 선에 채움(커맨드가 만들 수 없는 모양)
    with pytest.raises(ValueError):
        VD.parse_def(eid, VD.def_text(eid, {"kind": "draft", "family_id": "draft.basic", "params": bad}))
