# SPDX-License-Identifier: MPL-2.0
"""난간(railing): 커맨드·검사, 계단·슬래브 가장자리 자동 생성, IFC(IfcRailing, 손잡이·기둥 IfcSweptDiskSolid) 투영·왕복, 도면(평면·입면), 증분 == 전체 재생성, 결정성."""
import sys

import ifcopenshell
import pytest

import kit
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

PATH = [[0, 0], [3000, 0], [3000, 2000]]


def mk_rail(sess, **kw):
    args = {"id": F.RL1, "level": F.LV1, "path": PATH, "height": 1100, **kw}
    r = sess.run("railing.create", args)
    assert r["ok"], r
    return r["result"]["id"]


def mk(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


# ---------------------------------------------------------------- 순수 기하
def test_난간_형상은_기둥_간격_상한_이하로_등분하고_경사는_길이에_비례한다():
    g = K.railing_geom({"path": [[0, 0], [3000, 0]], "height": 1100.0, "base_offset": 100.0, "rise": 600.0})
    assert g["surface"] == [(0.0, 0.0, 100.0), (3000.0, 0.0, 700.0)] and g["top"][1] == (3000.0, 0.0, 1800.0)
    assert len(g["posts"]) == 2 + 2                                       # 3000 / 1200 -> 3 등분: 양 끝 + 사이 둘
    mid = sorted(g["posts"])[1]
    assert mid[0] == (1000.0, 0.0, 300.0) and mid[1] == (1000.0, 0.0, 1400.0)


# ---------------------------------------------------------------- 커맨드·검사
def test_난간_생성_수정_이동_삭제와_실행_취소(base):
    h0 = base.eng.state_hash()
    mk_rail(base)
    g = base.run("element.get", {"id": F.RL1})["result"]
    assert g["kind"] == "railing" and g["ifc_class"] == "IfcRailing" and g["relations"]["on_level"] == [F.LV1]
    assert g["derived"]["length"] == pytest.approx(5000.0) and g["derived"]["start_top"] == 1100.0
    assert base.run("model.stats", {})["result"]["counts"]["railing"] == 1
    assert base.run("railing.set", {"id": F.RL1, "height": 900, "rise": 300})["ok"]
    assert base.run("element.get", {"id": F.RL1})["result"]["derived"]["end_top"] == 1200.0
    assert base.run("railing.move", {"id": F.RL1, "dx": 100, "dy": 50})["ok"]
    assert base.run("element.get", {"id": F.RL1})["result"]["params"]["path"][0] == [100.0, 50.0]
    assert not base.run("railing.set", {"id": F.RL1})["ok"]
    assert base.run("element.delete", {"id": F.LV1})["ok"] is False        # 난간이 있는 레벨은 지울 수 없다
    assert base.run("railing.delete", {"id": F.RL1})["ok"]
    assert "railing" not in base.run("model.stats", {})["result"]["counts"]
    for _ in range(4):                                                        # 삭제·이동·수정·생성 네 변경을 모두 되돌린다
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_잘못된_난간은_거절한다(base):
    for bad in ({"path": [[0, 0]]}, {"path": [[0, 0], [0.2, 0]]}, {"height": 0}, {"level": "9" * 22}):
        r = base.run("railing.create", {"level": F.LV1, "path": PATH, **bad})
        assert not r["ok"], bad
    assert "railing" not in base.run("model.stats", {})["result"]["counts"]


def test_더했다_지우면_해시가_처음과_같다(base):
    h0 = base.eng.state_hash()
    mk_rail(base)
    assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


# ---------------------------------------------------------------- 자동 생성
def test_계단_옆면에_난간을_만든다(base):
    assert base.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18})["ok"]
    r = base.run("railing.from_stair", {"stair": F.ST1})
    assert r["ok"] and len(r["result"]["created"]) == 2
    ps = [base.run("element.get", {"id": i})["result"]["params"] for i in r["result"]["created"]]
    assert sorted(p["path"][0][1] for p in ps) == [450.0, 1550.0]            # 폭 1200 에서 50 들인 양 옆(y=1000 ± 550)
    assert all(p["rise"] == pytest.approx(17 * 3000 / 18) and p["base_offset"] == pytest.approx(3000 / 18) for p in ps)
    one = base.run("railing.from_stair", {"stair": F.ST1, "side": "left", "height": 900})
    assert len(one["result"]["created"]) == 1


def test_꺾인_계단은_단_묶음마다_옆면과_층참_가장자리에_난간을_만든다(base):
    assert base.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 4000, 1000], "width": 1200, "height": 3000, "risers": 18,
                                      "turn": {"type": "L", "side": "left", "risers_a": 9}})["ok"]
    r = base.run("railing.from_stair", {"stair": F.ST1})
    assert r["ok"] and len(r["result"]["created"]) == 2 * 2 + 1            # 단 묶음 둘 x 양쪽 + 층참 (ㄱ자 두 변)
    last = base.run("element.get", {"id": r["result"]["created"][-1]})["result"]["params"]
    assert last["rise"] == 0.0 and last["base_offset"] == pytest.approx(1500.0) and len(last["path"]) == 3
    u = base.run("stair.set", {"id": F.ST1, "turn": {"type": "U", "risers_a": 9}})
    assert u["ok"]
    r2 = base.run("railing.from_stair", {"stair": F.ST1, "landing": True})
    assert len(base.run("element.get", {"id": r2["result"]["created"][-1]})["result"]["params"]["path"]) == 4   # U 형 층참은 세 변(꼭짓점 4개)


def test_슬래브_가장자리와_구멍에_난간을_만든다(base):
    assert base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]],
                                    "openings": [[[2000, 1000], [3000, 1000], [3000, 3000], [2000, 3000]]]})["ok"]
    r = base.run("railing.from_slab", {"slab": F.S1})
    assert r["ok"] and len(r["result"]["created"]) == 1
    p = base.run("element.get", {"id": r["result"]["created"][0]})["result"]["params"]
    assert len(p["path"]) == 5 and p["path"][0] == p["path"][-1]            # 닫힌 한 줄
    two = base.run("railing.from_slab", {"slab": F.S1, "edges": [0, 1, 3]})  # 변 3, 0, 1 이 이어진다
    assert two["ok"] and len(two["result"]["created"]) == 1
    p2 = base.run("element.get", {"id": two["result"]["created"][0]})["result"]["params"]
    assert p2["path"] == [[0.0, 4000.0], [0.0, 0.0], [6000.0, 0.0], [6000.0, 4000.0]]
    hole = base.run("railing.from_slab", {"slab": F.S1, "opening": 0})
    assert hole["ok"] and len(base.run("element.get", {"id": hole["result"]["created"][0]})["result"]["params"]["path"]) == 5
    assert not base.run("railing.from_slab", {"slab": F.S1, "opening": 3})["ok"]
    assert not base.run("railing.from_slab", {"slab": F.S1, "edges": [9]})["ok"]


# ---------------------------------------------------------------- IFC
def test_IFC는_IfcRailing_손잡이와_기둥을_담는다(base, tmp_path):
    mk_rail(base)
    base.eng.flush_ifc()
    out = tmp_path / "r.ifc"
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    rl = f.by_guid(F.RL1)
    assert rl.is_a("IfcRailing") and rl.PredefinedType == "GUARDRAIL"
    items = rl.Representation.Representations[0].Items
    assert all(i.is_a("IfcSweptDiskSolid") for i in items)
    assert len(items) == 1 + len(K.railing_geom({"path": PATH, "height": 1100.0, "base_offset": 0.0, "rise": 0.0})["posts"])
    assert [r.RelatingStructure.GlobalId for r in rl.ContainedInStructure] == [F.LV1]
    assert base.run("model.validate", {})["ok"]


def test_난간을_내보내고_가져오면_저작으로_되살아나고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("railing.create", {"id": F.RL1, "level": F.LV1, "path": PATH, "height": 1000, "base_offset": 50, "rise": 400})["ok"]
        h = b.hashes()
        b.export("r.ifc")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"], r
        assert b.origins("imp.dobim") == {"authored": 8, "foreign": 0, "detached": 0}
        g = b.run("element.get", {"id": F.RL1}, project="imp.dobim")["result"]
        assert g["kind"] == "railing" and g["origin"] == "authored" and g["params"]["rise"] == 400.0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("railing.set", {"id": F.RL1, "height": 1200}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_외부에서_난간_형상을_바꾸면_분리된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("railing.create", {"id": F.RL1, "level": F.LV1, "path": PATH})["ok"]
        b.export("r.ifc")
        f = ifcopenshell.open(str(tmp_path / "r.ifc"))
        f.by_guid(F.RL1).Representation.Representations[0].Items[0].Radius = 99.0
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
    finally:
        b.close()


# ---------------------------------------------------------------- 도면
def test_평면은_난간선을_그리고_경사_난간은_절단_높이에서_끊는다(base):
    mk_rail(base)
    p = mk(base, name="P", type="plan", level=F.LV1, cut_height=1200)
    ln = lines_of(base, p)
    assert ("proj", 0.0, 0.0, 3000.0, 0.0) in ln and ("proj", 3000.0, 0.0, 3000.0, 2000.0) in ln
    assert base.run("railing.set", {"id": F.RL1, "path": [[0, 0], [4000, 0]], "rise": 2400})["ok"]    # 바닥 마감이 z = 0..2400 으로 오른다
    ln2 = lines_of(base, p)
    assert ("proj", 0.0, 0.0, 2000.0, 0.0) in ln2                            # z=1200 인 x=2000 까지만
    low = mk(base, name="P2", type="plan", level=F.LV1, cut_height=-100)
    assert not [x for x in lines_of(base, low) if x[0] == "proj" and x[2] == x[4] == 0.0 and x[1] == 0.0]


def test_입면은_손잡이선과_기둥을_그린다(base):
    mk_rail(base, path=[[500, -2000], [4500, -2000]])                        # 벽 앞(남쪽 -y)
    el = mk(base, name="E", type="elevation", dir="S")
    ln = lines_of(base, el)
    assert any(x[0] == "proj" and x[2] == x[4] == 1100.0 for x in ln)       # 손잡이 윗선
    assert sum(1 for x in ln if x[0] == "proj" and x[1] == x[3] and x[2] == 0.0 and x[4] == 1100.0) >= 4     # 기둥


def test_난간이_없으면_도면이_바뀌지_않는다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1), "s": mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000]),
             "e": mk(base, name="E", type="elevation", dir="S")}
    before = {k: lines_of(base, v) for k, v in views.items()}
    mk_rail(base)
    assert base.run("railing.delete", {"id": F.RL1})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k


def test_난간_증분이_전체_재생성과_같다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1, cut_height=1200), "p2": mk(base, name="P2", type="plan", level=F.LV2),
             "s": mk(base, name="S", type="section", p1=[1500, -1000], p2=[1500, 3000]), "e": mk(base, name="E", type="elevation", dir="S"),
             "e2": mk(base, name="E2", type="elevation", dir="E")}
    before = {k: lines_of(base, v) for k, v in views.items()}

    def check():
        for k, v in views.items():
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k

    mk_rail(base)
    check()
    assert base.run("railing.set", {"id": F.RL1, "height": 900, "rise": 500})["ok"]
    check()
    assert base.run("railing.move", {"id": F.RL1, "dx": 300, "dy": -2500})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("railing.delete", {"id": F.RL1})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k
    check()


def test_난간_도면과_해시는_결정적이다(tmp_path):
    def build(name):
        s = kit.Session(tmp_path / name, guid=F.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        mk_rail(s)
        vs = [mk(s, name="P", type="plan", level=F.LV1, cut_height=1200), mk(s, name="E", type="elevation", dir="S")]
        out = ([lines_of(s, v) for v in vs], s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a == b
