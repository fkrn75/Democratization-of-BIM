# SPDX-License-Identifier: MPL-2.0
"""승강기(elevator): 커맨드·검사, 샤프트 슬래브 구멍 자동 생성, IFC(IfcTransportElement ELEVATOR, 카 상자) 투영·왕복, 도면(평면 샤프트·카·X·EV, 단면·입면 상자), 증분 == 전체 재생성, 결정성."""
import sys

import ifcopenshell
import ifcopenshell.util.element as ue
import pytest

import kit
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import globalid as GI
from dobim_core import kinds as K

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

SHAFT = [4000, 500, 5800, 2300]     # 1800 x 1800 (벽 W2 안쪽 면 x=5900 안)
BOUND = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


def mk_ev(sess, **kw):
    args = {"id": F.EL1, "level": F.LV1, "shaft": SHAFT, "rise": 6000, **kw}
    r = sess.run("elevator.create", args)
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


def labels_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_labels, vid)


def full_labels(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return s3lib.RefGraph(s3lib._Inputs(sess.eng)).q(D.view_labels, vid)


def mk_slab(sess, sid, level, **kw):
    r = sess.run("slab.create", {"id": sid, "level": level, "boundary": BOUND, "thickness": 200, **kw})
    assert r["ok"], r
    return sid


S_LV1, S_LV2 = "6" + "0" * 20 + "1", "6" + "0" * 20 + "2"


# ---------------------------------------------------------------- 순수 기하
def test_승강기_형상은_카를_샤프트_가운데에_놓는다():
    g = K.elevator_geom({"shaft": [4000.0, 500.0, 5800.0, 2300.0], "cab": [1100.0, 1400.0, 2200.0], "base_offset": 50.0, "rise": 6000.0})
    assert g["shaft"] == (4000.0, 500.0, 5800.0, 2300.0) and g["cab"] == (4350.0, 700.0, 5450.0, 2100.0)
    assert (g["z0"], g["z1"], g["cab_z1"]) == (50.0, 6050.0, 2250.0)


# ---------------------------------------------------------------- 커맨드·검사
def test_승강기_생성_수정_이동_삭제와_실행_취소(base):
    h0 = base.eng.state_hash()
    assert mk_ev(base) == F.EL1
    g = base.run("element.get", {"id": F.EL1})["result"]
    assert g["kind"] == "elevator" and g["ifc_class"] == "IfcTransportElement" and g["relations"]["on_level"] == [F.LV1]
    assert g["params"]["cab"] == [1100.0, 1400.0, 2200.0]                            # 기본 카
    assert g["derived"] == {"bottom": 0.0, "top": 6000.0, "shaft_area_m2": pytest.approx(3.24), "cab_area_m2": pytest.approx(1.54)}
    assert base.run("model.stats", {})["result"]["counts"]["elevator"] == 1
    assert base.run("elevator.set", {"id": F.EL1, "cab": [1400, 1600, 2300], "base_offset": 100})["ok"]
    assert base.run("element.get", {"id": F.EL1})["result"]["derived"]["bottom"] == 100.0
    assert base.run("elevator.move", {"id": F.EL1, "dx": -200, "dy": 100})["ok"]
    assert base.run("element.get", {"id": F.EL1})["result"]["params"]["shaft"] == [3800.0, 600.0, 5600.0, 2400.0]
    assert not base.run("elevator.set", {"id": F.EL1})["ok"]
    assert base.run("element.delete", {"id": F.LV1})["ok"] is False                   # 승강기가 있는 레벨은 지울 수 없다
    assert base.run("elevator.delete", {"id": F.EL1})["ok"]
    assert "elevator" not in base.run("model.stats", {})["result"]["counts"]
    for _ in range(4):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_위_정차층으로_높이를_정하고_두_모서리는_정렬해_저장한다(base):
    r = base.run("elevator.create", {"id": F.EL1, "level": F.LV1, "shaft": [5800, 2300, 4000, 500], "to_level": F.LV2, "overrun": 1200})
    assert r["ok"] and r["result"]["rise"] == 4500.0                                  # 3300 + 1200
    assert base.run("element.get", {"id": F.EL1})["result"]["params"]["shaft"] == [4000.0, 500.0, 5800.0, 2300.0]


def test_잘못된_승강기는_거절한다(base):
    bad = ({"shaft": [0, 0, 500, 2000]}, {"shaft": [0, 0, 1000, 2000], "cab": [1100, 1400, 2200]}, {"cab": [1100, 1400, 7000]}, {"cab": [0, 1400, 2200]},
           {"level": "9" * 22}, {"rise": None}, {"to_level": F.LV2}, {"to_level": F.LV1}, {"rise": None, "to_level": F.LV1}, {"overrun": 100})
    for kw in bad:
        args = {"level": F.LV1, "shaft": SHAFT, "rise": 6000, **kw}
        args = {k: v for k, v in args.items() if v is not None}
        assert not base.run("elevator.create", args)["ok"], kw
    assert "elevator" not in base.run("model.stats", {})["result"]["counts"]


def test_더했다_지우면_해시가_처음과_같다(base):
    h0 = base.eng.state_hash()
    mk_ev(base)
    assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


# ---------------------------------------------------------------- 슬래브 구멍 자동 생성
def test_샤프트가_지나는_슬래브에만_구멍을_낸다(base):
    mk_slab(base, S_LV1, F.LV1)                                                       # 윗면 z=0: 카가 서는 바닥(샤프트 바닥과 같은 높이)이라 건드리지 않는다
    mk_slab(base, S_LV2, F.LV2)                                                       # 윗면 z=3300: 샤프트를 지난다
    mk_ev(base)
    r = base.run("elevator.cut_slabs", {"id": F.EL1})
    assert r["ok"] and r["result"] == {"cut": [S_LV2], "already": [], "skipped": []}
    assert "openings" not in base.run("element.get", {"id": S_LV1})["result"]["params"]
    assert base.run("element.get", {"id": S_LV2})["result"]["params"]["openings"] == [[[4000.0, 500.0], [5800.0, 500.0], [5800.0, 2300.0], [4000.0, 2300.0]]]
    again = base.run("elevator.cut_slabs", {"id": F.EL1})
    assert again["ok"] and again["result"] == {"cut": [], "already": [S_LV2], "skipped": []}   # 두 번째는 건너뛴다(중복 없음)
    assert base.run("history.undo", {})["ok"] is True


def test_여유와_겹침_경계_밖은_건너뛰며_이유를_알린다(base):
    mk_slab(base, S_LV2, F.LV2, openings=[[[5000, 1000], [5500, 1000], [5500, 1500], [5000, 1500]]])    # 샤프트와 겹치는 구멍
    mk_ev(base)
    r = base.run("elevator.cut_slabs", {"id": F.EL1})["result"]
    assert r["cut"] == [] and len(r["skipped"]) == 1 and "겹친다" in r["skipped"][0]["reason"]
    assert base.run("elevator.set", {"id": F.EL1, "shaft": [5000, 500, 7000, 2300]})["ok"]     # 슬래브 경계 밖으로 나간다
    r2 = base.run("elevator.cut_slabs", {"id": F.EL1})["result"]
    assert r2["cut"] == [] and "경계" in r2["skipped"][0]["reason"]
    assert base.run("elevator.set", {"id": F.EL1, "shaft": SHAFT, "rise": 3000})["ok"]          # 높이가 짧아 슬래브(z=3300)에 닿지 않는다
    r3 = base.run("elevator.cut_slabs", {"id": F.EL1})
    assert r3["ok"] and r3["result"]["cut"] == [] and r3["warnings"]


def test_구멍_여유를_사방으로_넓혀_낸다(base):
    mk_slab(base, S_LV2, F.LV2)
    mk_ev(base)
    assert base.run("elevator.cut_slabs", {"id": F.EL1, "clearance": 50})["ok"]
    assert base.run("element.get", {"id": S_LV2})["result"]["params"]["openings"][0][0] == [3950.0, 450.0]


# ---------------------------------------------------------------- IFC
def test_IFC는_IfcTransportElement_카_상자를_담는다(base, tmp_path):
    mk_ev(base)
    base.eng.flush_ifc()
    out = tmp_path / "e.ifc"
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    ev = f.by_guid(F.EL1)
    assert ev.is_a("IfcTransportElement") and ev.PredefinedType == "ELEVATOR"
    (solid,) = ev.Representation.Representations[0].Items
    assert solid.is_a("IfcExtrudedAreaSolid") and solid.Depth == 2200.0
    pts = sorted(tuple(p.Coordinates) for p in solid.SweptArea.OuterCurve.Points)
    assert pts[0] == (4350.0, 700.0) and pts[-1] == (5450.0, 2100.0)
    assert [r.RelatingStructure.GlobalId for r in ev.ContainedInStructure] == [F.LV1]
    assert ue.get_pset(ev, "DoBIM_Intent") is not None
    assert base.run("model.validate", {})["ok"]


def test_승강기를_내보내고_가져오면_저작으로_되살아나고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("elevator.create", {"id": F.EL1, "level": F.LV1, "shaft": SHAFT, "to_level": F.LV2, "cab": [1200, 1500, 2300], "base_offset": 30})["ok"]
        h = b.hashes()
        b.export("e.ifc")
        r = b.import_ifc("e.ifc", "imp.dobim")
        assert r["ok"], r
        assert b.origins("imp.dobim") == {"authored": 8, "foreign": 0, "detached": 0}
        g = b.run("element.get", {"id": F.EL1}, project="imp.dobim")["result"]
        assert g["kind"] == "elevator" and g["origin"] == "authored" and g["params"]["cab"] == [1200.0, 1500.0, 2300.0] and g["params"]["rise"] == 3270.0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("elevator.set", {"id": F.EL1, "cab": [1100, 1400, 2200]}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_외부에서_카_형상을_바꾸면_분리된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("elevator.create", {"id": F.EL1, "level": F.LV1, "shaft": SHAFT, "rise": 6000})["ok"]
        b.export("e.ifc")
        f = GI.open_exported(tmp_path / "e.ifc")   # 첫 글자가 무효한 id(F.EL1)는 내보낸 파일에서 바뀌어 있다(W2): 원래 id 로 되돌려 연다
        f.by_guid(F.EL1).Representation.Representations[0].Items[0].Depth = 2500.0
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
    finally:
        b.close()


# ---------------------------------------------------------------- 도면
def test_평면은_샤프트_카_X_EV를_그리고_지나는_모든_층에_나온다(base):
    mk_ev(base)
    p1 = mk(base, name="P1", type="plan", level=F.LV1, cut_height=1200)
    ln = lines_of(base, p1)
    assert ("proj", 4000.0, 500.0, 5800.0, 500.0) in ln and ("proj", 4000.0, 500.0, 4000.0, 2300.0) in ln     # 샤프트 윤곽
    assert ("proj", 4350.0, 700.0, 5450.0, 700.0) in ln and ("proj", 5450.0, 700.0, 5450.0, 2100.0) in ln     # 카 윤곽
    assert ("symbol", 4350.0, 700.0, 5450.0, 2100.0) in ln and ("symbol", 4350.0, 2100.0, 5450.0, 700.0) in ln   # 대각 X
    ev = [t for t in labels_of(base, p1) if t[2] == "EV"]
    assert len(ev) == 1 and ev[0][0] == 4900.0 and ev[0][1] > 1400.0 and ev[0][3:] == (D.LABEL_SUB_H, "middle")   # 카 가운데 위쪽
    p2 = mk(base, name="P2", type="plan", level=F.LV2, cut_height=1200)                   # 2층(절단 높이 4500 < 6000): 샤프트가 지난다
    assert ("symbol", 4350.0, 700.0, 5450.0, 2100.0) in lines_of(base, p2)
    top = mk(base, name="P3", type="plan", level=F.LV2, cut_height=3000)                  # 절단 높이 6300 > 샤프트 위: 나오지 않는다
    assert not any(x[1] in (4000.0, 4350.0) for x in lines_of(base, top)) and not any(t[2] == "EV" for t in labels_of(base, top))


def test_입면과_단면은_샤프트_상자_모서리를_그린다(base):
    mk_ev(base, shaft=[500, -3000, 2300, -1200], rise=6000)                               # 건물 밖 앞(남쪽 -y): 벽에 가려지지 않는다
    el = mk(base, name="E", type="elevation", dir="S")
    ln = lines_of(base, el)
    assert any(x[0] == "proj" and x[2] == x[4] == 6000.0 for x in ln)                     # 샤프트 윗선
    assert any(x[0] == "proj" and x[1] == x[3] == 500.0 and x[2] == 0.0 and x[4] == 6000.0 for x in ln)   # 수직 모서리
    sec = mk(base, name="S", type="section", p1=[-1000, -2000], p2=[3000, -2000])
    assert any(x[0] == "proj" and x[2] == x[4] == 2200.0 for x in lines_of(base, sec))   # 카 천장선


def test_승강기가_없으면_도면이_바뀌지_않는다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1), "s": mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000]),
             "e": mk(base, name="E", type="elevation", dir="S")}
    before = {k: (lines_of(base, v), labels_of(base, v)) for k, v in views.items()}
    mk_ev(base)
    assert base.run("elevator.delete", {"id": F.EL1})["ok"]
    for k, v in views.items():
        assert (lines_of(base, v), labels_of(base, v)) == before[k], k


def test_승강기_증분이_전체_재생성과_같다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1, cut_height=1200), "p2": mk(base, name="P2", type="plan", level=F.LV2),
             "s": mk(base, name="S", type="section", p1=[4900, -1000], p2=[4900, 3000]), "e": mk(base, name="E", type="elevation", dir="S"),
             "e2": mk(base, name="E2", type="elevation", dir="E")}
    before = {k: lines_of(base, v) for k, v in views.items()}

    def check():
        for k, v in views.items():
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k
            assert labels_of(base, v) == full_labels(base, v), k

    mk_ev(base)
    check()
    mk_slab(base, S_LV2, F.LV2)
    assert base.run("elevator.cut_slabs", {"id": F.EL1})["ok"]
    check()
    assert base.run("elevator.set", {"id": F.EL1, "cab": [1300, 1500, 2300], "rise": 4000})["ok"]
    check()
    assert base.run("elevator.move", {"id": F.EL1, "dx": -300, "dy": -200})["ok"]   # 낸 슬래브 구멍이 따라 옮겨진다(경계 안에 머무는 이동)
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("elevator.delete", {"id": F.EL1})["ok"]
    assert base.run("slab.delete", {"id": S_LV2})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k
    check()


def test_승강기_도면과_해시는_결정적이다(tmp_path):
    def build(name):
        s = kit.Session(tmp_path / name, guid=F.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        mk_ev(s)
        mk_slab(s, S_LV2, F.LV2)
        assert s.run("elevator.cut_slabs", {"id": F.EL1})["ok"]
        vs = [mk(s, name="P", type="plan", level=F.LV1, cut_height=1200), mk(s, name="E", type="elevation", dir="S")]
        out = ([lines_of(s, v) for v in vs], [labels_of(s, v) for v in vs], s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a == b
