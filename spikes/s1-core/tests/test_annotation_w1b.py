# SPDX-License-Identifier: MPL-2.0
"""W1 1-B 주석·치수 확장(2D 작도 모양 추가): 각도·반지름·지름·호 길이·연속·기준선 치수(ANN-02), 표고·좌표·경사 기호(ANN-04·03),
마스킹 영역(ANN-13), 리비전 구름(ANN-17 일부). 값·그림 항목·검사·편집(회전·대칭)·출력(SVG·PDF·DXF)·증분 = 전체·IFC 왕복.
합성 데이터만 쓴다."""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drafting as DR
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V, VS = F.V1, F.V2


def ok(r):
    assert r["ok"], r
    return r["result"]


def destructive(s, name, args):
    r = s.run(name, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


@pytest.fixture
def vb(base):
    ok(base.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(base.run("view.create", {"id": VS, "name": "A-A", "type": "section", "p1": [0, 2000], "p2": [6000, 2000], "depth": 8000}))
    return base


def mk(s, view=V, **kw):
    return ok(s.run("draft.create", {"view": view, **kw}))["id"]


def item(s, eid):
    return next(i for i in ok(s.run("draft.list", {"limit": 1000}))["items"] if i["id"] == eid)


def texts(s, eid):
    s.eng.sync()
    return [it[7] for it in s.eng.g.q(DR.draft_items, eid) if it[0] == "text"]


# ---------------------------------------------------------------- 치수 종류(ANN-02)
def test_각도_반지름_지름_호길이_치수의_값과_글(vb):
    ang = mk(vb, shape="dim_angle", pts=[[0, 0], [1000, 0], [0, 1000]], r=800)
    rad = mk(vb, shape="dim_radius", pts=[[3000, 3000]], r=500, a0=45)
    dia = mk(vb, shape="dim_diameter", pts=[[3000, 3000]], r=500, a0=0)
    arc = mk(vb, shape="dim_arc", pts=[[0, 0]], r=1000, a0=0, a1=90)
    assert item(vb, ang)["value"] == 90.0 and texts(vb, ang) == ["90°"]
    assert item(vb, rad)["value"] == 500.0 and texts(vb, rad) == ["R500"]
    assert item(vb, dia)["value"] == 1000.0 and texts(vb, dia) == ["Ø1000"]
    assert item(vb, arc)["value"] == pytest.approx(1570.796, abs=1e-3) and texts(vb, arc) == ["1571"]
    assert ok(vb.run("element.get", {"id": arc}))["params"]["off"] == 600.0      # 호 길이 치수 간격 기본값
    # 반시계로 잰다: 순서를 바꾸면 270도
    assert item(vb, mk(vb, shape="dim_angle", pts=[[0, 0], [0, 1000], [1000, 0]], r=800))["value"] == 270.0
    # 값 대신 쓸 글
    assert texts(vb, mk(vb, shape="dim_radius", pts=[[0, 0]], r=300, text="R=300(내)")) == ["R=300(내)"]
    vb.eng.sync()
    kinds = {it[0] for e in (ang, rad, dia, arc) for it in vb.eng.g.q(DR.draft_items, e)}
    assert kinds == {"line", "text"}


def test_연속_기준선_치수(vb):
    ch = mk(vb, shape="dim_chain", pts=[[0, 0], [1000, 300], [3000, 0], [3000, 900]], a0=0, off=-800)
    assert item(vb, ch)["values"] == [1000.0, 2000.0]                                 # 같은 위치의 점은 한 눈금
    assert texts(vb, ch) == ["1000", "2000"]
    bs = mk(vb, shape="dim_base", pts=[[0, 0], [3000, 0], [1000, 0]], a0=0)
    assert item(vb, bs)["values"] == [1000.0, 3000.0] and texts(vb, bs) == ["1000", "3000"]
    vb.eng.sync()
    rows = sorted({it[6] for it in vb.eng.g.q(DR.draft_items, bs) if it[0] == "line" and it[6] == it[8] and it[5] != it[7]})
    assert rows == [600.0, 1300.0]                                                     # 기준선 줄은 용지 7mm(1:100 = 700) 간격으로 쌓인다
    vt = mk(vb, shape="dim_chain", pts=[[0, 0], [0, 2500]], a0=90)                     # 세로 측정
    assert item(vb, vt)["values"] == [2500.0]


# ---------------------------------------------------------------- 표고·좌표·경사(ANN-04·03)
def test_표고_좌표_경사(vb):
    e1 = mk(vb, view=VS, shape="spot_elev", pts=[[1000, 3000], [1500, 3500]])
    assert texts(vb, e1) == ["EL+3,000"]                                               # 단면: 점의 높이
    e2 = mk(vb, shape="spot_elev", pts=[[1000, 1000], [1500, 1500]])
    assert texts(vb, e2) == ["EL+0"]                                                   # 평면: 그 뷰 레벨(1F = 0)
    c = mk(vb, shape="spot_coord", pts=[[1200, -3400], [2000, -2000]])
    assert texts(vb, c) == ["N -3,400", "E 1,200"]
    sl = mk(vb, view=VS, shape="spot_slope", pts=[[0, 3000], [5000, 2900]])
    assert texts(vb, sl) == ["2.0%"]
    r = vb.run("draft.create", {"view": V, "shape": "spot_slope", "pts": [[0, 0], [1000, 0]]})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT" and r["error"]["path"] == "/text"   # 평면은 높이를 몰라 글로 받는다
    assert texts(vb, mk(vb, shape="spot_slope", pts=[[0, 0], [1000, 0]], text="i=1/50")) == ["i=1/50"]


# ---------------------------------------------------------------- 마스킹·구름(ANN-13·17)
def test_마스킹은_다른_2D_요소보다_먼저_그린다(vb):
    t = mk(vb, shape="text", pts=[[500, 500]], text="가린 곳 위 글")
    m = mk(vb, shape="mask", pts=[[0, 0], [0, 1000], [2000, 1000], [2000, 0]])        # 시계로 줘도 반시계로 저장
    assert ok(vb.run("element.get", {"id": m}))["params"]["pts"][0] == [2000.0, 0.0]
    vb.eng.sync()
    items = vb.eng.g.q(DR.view_drafts, V)
    assert items[0][:4] == ("fill", m, "A-DRAFT", "#ffffff") and len(vb.eng.g.q(DR.draft_items, m)) == 1   # 경계선 없음
    assert items[-1][1] == t
    ok(vb.run("draft.set", {"id": m, "closed": True}))
    vb.eng.sync()
    assert sum(1 for it in vb.eng.g.q(DR.draft_items, m) if it[0] == "line") == 4


def test_리비전_구름과_번호(vb):
    c = mk(vb, shape="cloud", pts=[[0, 0], [4000, 0], [4000, 2000], [0, 2000]], text="2")
    vb.eng.sync()
    its = vb.eng.g.q(DR.draft_items, c)
    assert texts(vb, c) == ["2"]
    xs = [v for it in its if it[0] == "line" for v in (it[5], it[7])]
    ys = [v for it in its if it[0] == "line" for v in (it[6], it[8])]
    assert min(ys) < -1.0 and max(xs) > 4001.0                                         # 호가 바깥으로 부푼다
    dense = mk(vb, shape="cloud", pts=[[0, 0], [1e6, 0], [1e6, 1e6]], r=1)             # 아주 작은 호: 개수 상한
    vb.eng.sync()
    assert len(vb.eng.g.q(DR.draft_items, dense)) < DR.CLOUD_MAX_BUMPS * 40


# ---------------------------------------------------------------- 검사
@pytest.mark.parametrize("args", [
    {"shape": "dim_angle", "pts": [[0, 0], [1, 0], [2, 0]], "r": 10},                  # 두 방향이 같다
    {"shape": "dim_angle", "pts": [[0, 0], [0, 0], [0, 1]], "r": 10},
    {"shape": "dim_angle", "pts": [[0, 0], [1, 0], [0, 1]]},                            # r 없음
    {"shape": "dim_radius", "pts": [[0, 0]], "r": 10, "a1": 5},
    {"shape": "dim_arc", "pts": [[0, 0]], "r": 100, "a0": 10, "a1": 10},
    {"shape": "dim_arc", "pts": [[0, 0]], "r": 100, "a0": 0, "a1": 90, "off": -200},   # 치수 호 반지름 0 이하
    {"shape": "dim_chain", "pts": [[0, 0], [0, 1000]], "a0": 0},                        # 측정 방향으로 같은 위치
    {"shape": "dim_chain", "pts": [[0, 0], [1000, 0]], "text": "x"},
    {"shape": "spot_slope", "pts": [[0, 0], [0, 0]], "text": "i"},
    {"shape": "mask", "pts": [[0, 0], [10, 10], [10, 0], [0, 10]]},                    # 자기 교차
    {"shape": "mask", "pts": [[0, 0], [10, 0], [10, 10]], "fill": "solid"},
    {"shape": "cloud", "pts": [[0, 0], [10, 0], [10, 10]], "closed": True},
    {"shape": "spot_elev", "pts": [[0, 0], [1, 1]], "r": 5},
])
def test_잘못된_입력은_거절한다(vb, args):
    r = vb.run("draft.create", {"view": V, **args})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT", r


# ---------------------------------------------------------------- 편집
def test_회전_대칭에도_값이_그대로다(vb):
    ch = mk(vb, shape="dim_chain", pts=[[0, 0], [1000, 0], [3000, 0]], a0=0, off=500)
    an = mk(vb, shape="dim_angle", pts=[[0, 0], [1000, 0], [0, 1000]], r=800)
    rd = mk(vb, shape="dim_radius", pts=[[0, 0]], r=300, a0=10)
    ar = mk(vb, shape="dim_arc", pts=[[0, 0]], r=1000, a0=0, a1=60)
    ok(vb.run("draft.rotate", {"ids": [ch, an, rd, ar], "center": [0, 0], "angle": 30}))
    assert ok(vb.run("element.get", {"id": ch}))["params"]["a0"] == 30.0
    assert ok(vb.run("element.get", {"id": rd}))["params"]["a0"] == 40.0
    assert (item(vb, ch)["values"], item(vb, an)["value"]) == ([1000.0, 2000.0], 90.0)
    ok(vb.run("draft.mirror", {"ids": [ch, an, rd, ar], "axis": [[0, 0], [0, 1]]}))
    assert item(vb, an)["value"] == pytest.approx(90.0) and item(vb, ch)["values"] == [1000.0, 2000.0]
    assert item(vb, ar)["value"] == pytest.approx(1047.198, abs=1e-3)
    p = ok(vb.run("element.get", {"id": ch}))["params"]
    assert p["off"] == -500.0 and p["a0"] == 150.0                                     # 대칭으로 왼쪽이 오른쪽이 된다


# ---------------------------------------------------------------- 출력
def test_SVG_PDF_DXF_출력과_되돌리면_바이트가_같다(vb):
    def exp(name):
        assert destructive(vb, "view.export", {"id": V, "path": name, "overwrite": True})["ok"]
        return (vb.path.parent / name).read_bytes()

    before_svg, before_dxf = exp("a.svg"), exp("a.dxf")
    assert b"16777215" not in before_dxf
    m = mk(vb, shape="mask", pts=[[1000, -500], [3000, -500], [3000, 500], [1000, 500]])
    mk(vb, shape="dim_chain", pts=[[0, 0], [1500, 0], [6000, 0]], a0=0, off=-1200)
    svg = exp("b.svg").decode("utf-8")
    assert f'<g class="draft" data-id="{m}"' in svg and 'fill="#ffffff"' in svg and ">4500<" in svg
    assert svg.index(f'data-id="{m}"') < svg.index(">4500<")
    assert exp("b.pdf").startswith(b"%PDF")
    dxf = exp("b.dxf").decode("utf-8")
    assert "HATCH" in dxf and "\n420\n16777215\n" in dxf                              # 마스킹 = 흰 SOLID 해치
    ok(vb.run("history.undo", {}))
    ok(vb.run("history.undo", {}))
    assert exp("c.svg") == before_svg and exp("c.dxf") == before_dxf


def test_증분과_전체_계산이_같다(vb):
    ch = mk(vb, shape="dim_chain", pts=[[0, 0], [1000, 0]], a0=0, layer="L1")
    mk(vb, shape="mask", pts=[[0, 0], [1000, 0], [1000, 1000]])
    mk(vb, shape="spot_elev", pts=[[0, 0], [100, 100]])
    vb.eng.sync()
    vb.eng.g.q(DR.view_drafts, V)
    ok(vb.run("draft.layer.set", {"name": "L1", "weight": "thick"}))
    ok(vb.run("draft.move", {"ids": [ch], "dx": 10}))
    vb.eng.sync()
    inc = vb.eng.g.q(DR.view_drafts, V)
    full = s3lib.RefGraph(s3lib._Inputs(vb.eng)).q(DR.view_drafts, V)
    assert inc == full and any(it[0] == "text" and it[7] == "EL+0" for it in inc)


# ---------------------------------------------------------------- IFC 왕복
def test_IFC_왕복은_새_모양을_되살리고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}))
        args = [{"shape": "dim_angle", "pts": [[0, 0], [1000, 0], [0, 1000]], "r": 800},
                {"shape": "dim_diameter", "pts": [[0, 0]], "r": 400, "a0": 30},
                {"shape": "dim_arc", "pts": [[0, 0]], "r": 900, "a0": 270, "a1": 45, "off": 300},
                {"shape": "dim_base", "pts": [[0, 0], [2000, 0], [5000, 100]], "a0": 0, "off": -900},
                {"shape": "spot_coord", "pts": [[100, 200], [900, 900]], "text": "기준점"},
                {"shape": "mask", "pts": [[0, 0], [1000, 0], [1000, 1000]], "closed": True},
                {"shape": "cloud", "pts": [[0, 0], [3000, 0], [3000, 2000]], "text": "1"}]
        ids = [ok(b.run("draft.create", {"view": V, **a}))["id"] for a in args]
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        for i in ids:
            assert b.run("element.get", {"id": i}, project="imp.dobim")["result"]["params"] == b.run("element.get", {"id": i})["result"]["params"]
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == (tmp_path / "t.ifc").read_bytes()
    finally:
        b.close()


# ---------------------------------------------------------------- 태그 범주 확장(ANN-06·08)
def test_슬래브_공간_계단_태그와_재료_태그(vb):
    sl = ok(vb.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200}))["id"]
    sp = ok(vb.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[0, 0], [3000, 0], [3000, 2000], [0, 2000]], "name": "사무실", "category": "업무"}))["id"]
    st = ok(vb.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18}))["id"]
    t1 = ok(vb.run("annot.tag.create", {"view": V, "target": sl, "fields": ["thickness", "area", "level"]}))["id"]
    t2 = ok(vb.run("annot.tag.create", {"view": V, "target": sp, "fields": ["name", "category", "area"]}))["id"]
    t3 = ok(vb.run("annot.tag.create", {"view": V, "target": st, "fields": ["risers", "width"]}))["id"]
    t4 = ok(vb.run("annot.tag.create", {"view": V, "target": sl, "fields": ["material"]}))["id"]
    lst = {i["id"]: i for i in ok(vb.run("annot.list", {}))["items"]}
    assert lst[t1]["text"] == "200 24.00㎡ 1F" and lst[t2]["text"] == "사무실 업무 6.00㎡" and lst[t3]["text"] == "18 1200"
    assert lst[t4]["status"] == "ok"
    r = vb.run("annot.tag.create", {"view": V, "target": st, "fields": ["area"]})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT" and "risers" in r["error"]["hint"]
    vb.eng.sync()
    from dobim_core import annot as AN
    anc = {it[1]: it[2] for it in vb.eng.g.q(AN.view_annots, V) if it[0] == "tag"}
    assert anc[t2] == (1500.0, 1000.0) and anc[t3] == (3000.0, 1000.0)
    ok(vb.run("element.delete", {"id": sp}))
    assert next(i for i in ok(vb.run("annot.list", {}))["items"] if i["id"] == t2)["status"] == "orphan"
