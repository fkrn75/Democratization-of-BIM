# SPDX-License-Identifier: MPL-2.0
"""W1 연장(ANN-02): 연속(체인)·기준선 연관 치수 annot.dim.chain.
- 벽 끝·문틀·통심선 참조를 한 방향으로 투영해 재고, 요소를 옮기면 값·보조선이 따라가며 치수선 자리는 그대로다
- 기준선은 첫 참조에서 각 참조까지, 치수선을 바깥으로 쌓는다. 단면의 높이 연속 치수
- 거절(수직 아닌 선·같은 참조·섞인 참조), 가운데 대상 삭제 = 그 칸만 고아, 다시 묶기
- 그림 항목·SVG 출력·되돌리기·증분 = 전체·IFC 왕복(DoBIM_Defs)·변조 정의 거절. 합성 데이터만 쓴다."""
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

V, V2 = F.V1, F.V2
G1, G2, G3 = "g" + "0" * 20 + "1", "g" + "0" * 20 + "2", "g" + "0" * 20 + "3"
W1S, W1E = f"wall:{F.W1}#axis.start", f"wall:{F.W1}#axis.end"
J0, J1 = f"door:{F.D1}#jamb.start", f"door:{F.D1}#jamb.end"


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def destructive(s, name, args):
    r = s.run(name, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


def build(s):
    """기준 프로젝트 + 평면 뷰 + 세로 통심선 둘(x=-1000, 8000)·사선 통심선 + 단면 뷰."""
    ok(s.run("view.create", {"id": V, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.create", {"id": V2, "name": "B-B 단면", "type": "section", "p1": [-1000, 2000], "p2": [7000, 2000]}))
    ok(s.run("grid.create", {"id": G1, "name": "1", "line": [-1000, -2000, -1000, 6000]}))
    ok(s.run("grid.create", {"id": G2, "name": "A", "line": [-2000, -2000, 8000, 8000]}))
    ok(s.run("grid.create", {"id": G3, "name": "2", "line": [8000, -2000, 8000, 6000]}))
    return s


@pytest.fixture
def vb(base):
    return build(base)


def chain(s, refs, **kw):
    return ok(s.run("annot.dim.chain", {"view": kw.pop("view", V), "refs": refs, **kw}))


def items(s):
    return {i["id"]: i for i in ok(s.run("annot.list", {"limit": 1000}))["items"]}


def draws(s, v=V):
    s.eng.sync()
    return {it[1]: it for it in s.eng.g.q(AN.view_annots, v)}


def vals(s, r):
    it = items(s)
    return [it[d["id"]].get("value") for d in r["dims"]]


# ---------------------------------------------------------------- 연속 치수
def test_연속_치수는_문을_옮기면_값과_보조선이_따라가고_치수선은_그대로다(vb):
    r = chain(vb, [W1S, J0, J1, W1E])
    assert r["mode"] == "chain" and r["along"] == [1.0, 0.0] and len(r["dims"]) == 3
    assert [d["value"] for d in r["dims"]] == [1050.0, 900.0, 4050.0] == vals(vb, r)
    it = items(vb)
    assert {it[d["id"]]["group"] for d in r["dims"]} == {r["dims"][0]["id"]} and it[r["dims"][0]["id"]]["chain"] == "chain"
    before = draws(vb)
    lines = [before[d["id"]][4] for d in r["dims"]]
    assert all(ln[1] == ln[3] == 600.0 for ln in lines)          # 한 줄(y=600)에 선다
    assert lines[0][2] == lines[1][0] and lines[1][2] == lines[2][0]   # 칸이 이어진다
    ok(vb.run("door.set", {"id": F.D1, "offset": 2500}))
    assert vals(vb, r) == [2050.0, 900.0, 3050.0]
    ok(vb.run("wall.move", {"id": F.W1, "dx": 0, "dy": -300}))     # 벽이 치수선에서 멀어져도 치수선은 그대로, 보조선이 길어진다
    after = draws(vb)
    assert vals(vb, r) == [2050.0, 900.0, 3050.0]
    assert after[r["dims"][0]["id"]][4][1] == 600.0 and after[r["dims"][0]["id"]][2][1] == -300.0
    assert after[r["dims"][1]["id"]][5] == "900"


def test_통심선_연속_치수는_자동_방향이고_통심선을_옮기면_따라간다(vb):
    r = chain(vb, [f"grid:{G1}#line", f"wall:{F.W2}#axis", f"grid:{G3}#line"], offset=-500)
    assert r["along"] == [1.0, 0.0] and [d["value"] for d in r["dims"]] == [7000.0, 2000.0]
    ok(vb.run("grid.move", {"id": G3, "dx": 500, "dy": 0}))
    ok(vb.run("wall.move", {"id": F.W2, "dx": -1000, "dy": 0}))
    assert vals(vb, r) == [6000.0, 3500.0]
    d = draws(vb)[r["dims"][0]["id"]]
    assert d[0] == "dim" and d[4][1] == d[4][3] == 1500.0           # 기준점(통심선·벽 축선 중점 y=2000)에서 오른쪽(아래) 바깥 500
    e = err(vb.run("annot.dim.chain", {"view": V, "refs": [f"grid:{G1}#line", f"grid:{G2}#line"]}))
    assert e["code"] == "CONSTRAINT" and "수직" in e["message"]


def test_기준선_치수는_첫_참조에서_재고_치수선을_쌓는다(vb):
    r = chain(vb, [W1S, J0, J1, W1E], mode="baseline", offset=-800, spacing=400)
    assert [d["value"] for d in r["dims"]] == [1050.0, 1950.0, 6000.0]
    d = draws(vb)
    ys = [d[x["id"]][4][1] for x in r["dims"]]
    assert ys == [-800.0, -1200.0, -1600.0]
    ok(vb.run("door.set", {"id": F.D1, "width": 1100}))
    assert vals(vb, r) == [950.0, 2050.0, 6000.0]


def test_단면_높이_연속_치수(vb):
    r = chain(vb, [f"level:{F.LV1}#elevation", f"wall:{F.W1}#top", f"level:{F.LV2}#elevation"], view=V2, at=[-500, 2000])
    assert [d["value"] for d in r["dims"]] == [3000.0, 300.0] and "along" not in r
    ok(vb.run("level.set", {"id": F.LV2, "elevation": 3600}))
    assert vals(vb, r) == [3000.0, 600.0]
    assert err(vb.run("annot.dim.chain", {"view": V2, "refs": [f"level:{F.LV1}#elevation", f"level:{F.LV2}#elevation"], "along": [1, 0]}))["code"] == "CONSTRAINT"


def test_거절_사례(vb):
    for refs, kw in (([W1S, W1S, W1E], {}),                                   # 같은 참조를 잇달아
                     ([W1S, f"level:{F.LV1}#elevation"], {}),                 # 평면·높이 섞임
                     ([W1S, W1E], {"at": [0, 0]}),                            # 평면에 at
                     ([W1S, J0], {"along": [0, 0]}),                          # 방향 크기 0
                     ([W1S, W1E], {"view": V2})):                             # 평면 참조를 단면에
        assert err(vb.run("annot.dim.chain", {"view": kw.pop("view", V), "refs": refs, **kw}))["code"] in ("CONSTRAINT", "SCHEMA"), refs
    assert ok(vb.run("annot.list", {}))["items"] == []                       # 실패한 호출은 아무것도 남기지 않는다


def test_가운데_대상을_지우면_그_칸만_고아이고_다시_묶을_수_있다(vb):
    r = chain(vb, [f"grid:{G1}#line", W1S, f"door:{F.D1}#center", W1E])
    assert [d["value"] for d in r["dims"]] == [1000.0, 1500.0, 4500.0]
    assert F.D1 in ok(vb.run("element.delete", {"id": F.D1}))["deleted"]
    it = items(vb)
    assert [it[d["id"]]["status"] for d in r["dims"]] == ["ok", "orphan", "orphan"]
    ok(vb.run("annot.rebind", {"id": r["dims"][1]["id"], "b": f"grid:{G3}#start"}))
    assert items(vb)[r["dims"][1]["id"]]["value"] == 8000.0              # 다시 묶어도 같은 방향(along)으로 투영해 잰다
    assert err(vb.run("annot.rebind", {"id": r["dims"][1]["id"], "b": f"grid:{G2}#line"}))["code"] == "CONSTRAINT"


# ---------------------------------------------------------------- 출력·증분
def test_SVG_출력과_되돌리면_바이트가_같다(vb):
    def exp(name):
        assert destructive(vb, "view.export", {"id": V, "path": name, "overwrite": True})["ok"]
        return (vb.path.parent / name).read_bytes()

    before = exp("a.svg")
    r = chain(vb, [W1S, J0, J1, W1E])
    svg = exp("b.svg").decode("utf-8")
    assert all(f'data-id="{d["id"]}"' in svg for d in r["dims"]) and ">4050<" in svg
    assert exp("b.pdf").startswith(b"%PDF")
    ok(vb.run("history.undo", {}))
    assert exp("c.svg") == before


def test_증분과_전체_계산이_같다(vb):
    chain(vb, [W1S, J0, J1, W1E])
    chain(vb, [f"grid:{G1}#line", f"wall:{F.W2}#axis", f"grid:{G3}#line"], mode="baseline")
    chain(vb, [f"level:{F.LV1}#elevation", f"level:{F.LV2}#elevation"], view=V2)
    vb.eng.sync()
    vb.eng.g.q(AN.annot_bundle)
    ok(vb.run("door.set", {"id": F.D1, "offset": 2000}))
    ok(vb.run("grid.move", {"id": G1, "dx": 100, "dy": 0}))
    ok(vb.run("wall.move", {"id": F.W1, "dx": 0, "dy": 50}))
    vb.eng.sync()
    inc = vb.eng.g.q(AN.annot_bundle)
    full = s3lib.RefGraph(s3lib._Inputs(vb.eng)).q(AN.annot_bundle)
    assert inc == full and all(v[1] == "ok" for v in inc["state"].values())


# ---------------------------------------------------------------- IFC 왕복
def test_IFC_왕복은_연속_기준선_치수를_되살리고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        build(b)
        ids = [d["id"] for d in ok(b.run("annot.dim.chain", {"view": V, "refs": [W1S, J0, J1, W1E]}))["dims"]]
        ids += [d["id"] for d in ok(b.run("annot.dim.chain", {"view": V, "refs": [f"grid:{G1}#line", f"grid:{G3}#line"], "mode": "baseline"}))["dims"]]
        ids += [d["id"] for d in ok(b.run("annot.dim.chain", {"view": V2, "refs": [f"level:{F.LV1}#elevation", f"level:{F.LV2}#elevation"]}))["dims"]]
        assert len(ids) == 5
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


def test_변조한_연속_치수_정의는_되살리지_않는다():
    from dobim_core import viewdefs as VD
    p = {"name": "D", "view": V, "a": W1S, "b": J0, "off": 600.0, "home": [0.0, 0.0], "dirs": {"a": None, "b": None},
         "along": [1.0, 0.0], "dl": [0.0, 600.0], "group": "5" * 22, "gk": "chain"}
    eid = "5" * 22
    VD.parse_def(eid, VD.def_text(eid, {"kind": "dim", "family_id": "annot.dim", "params": p}))
    for bad in ({"along": [2.0, 0.0]}, {"dl": None}, {"gk": "arc"}, {"group": None}, {"measure": "angle"},
                {"a": f"level:{F.LV1}#elevation", "b": f"level:{F.LV2}#elevation", "at": [0.0, 0.0]}):
        q = {k: v for k, v in {**p, **bad}.items() if v is not None}
        with pytest.raises(ValueError):
            VD.parse_def(eid, VD.def_text(eid, {"kind": "dim", "family_id": "annot.dim", "params": q}))
