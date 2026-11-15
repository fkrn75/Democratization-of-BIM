# SPDX-License-Identifier: MPL-2.0
"""방화(#3: 방화벽 중심선 파선·FW/FD 문자)와 외부 마감(#6: 입면 해치·재료 문자): 새 종류 없이 기존 속성(FireRating·IsExternal·재료)만 읽는다."""
import sys

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402


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


def pset(sess, eid, pset_, prop, value):
    r = sess.run("pset.set", {"id": eid, "pset": pset_, "prop": prop, "value": value})
    assert r["ok"], r


def hatch(sess, vid):
    return [x for x in lines_of(sess, vid) if x[0] == "hatch"]


def external(sess, wall, material="Concrete"):
    pset(sess, wall, "Pset_WallCommon", "IsExternal", True)
    if material:
        assert sess.run("material.set", {"id": wall, "name": material})["ok"]


# ---------------------------------------------------------------- 방화 (평면)
def test_방화등급이_있는_벽은_중심선_파선과_FW_문자를_갖는다(base):
    p = mk(base, name="P", type="plan", level=F.LV1)                                       # 1:100
    before_l, before_t = lines_of(base, p), labels_of(base, p)
    assert [t[2] for t in before_t] == ["FD EI30"]                                         # 기준 모델의 방화문(D1)만
    pset(base, F.W1, "Pset_WallCommon", "FireRating", "EI60")
    added = set(lines_of(base, p)) - set(before_l)
    assert added and all(x[0] == "symbol" and x[2] == x[4] == 0.0 for x in added)          # W1 의 중심선(y=0) 위
    assert sorted(x[3] - x[1] for x in added)[-1] == 400.0                                  # 마디 4mm x 축척 100
    assert all(0.0 <= x[1] and x[3] <= 6000.0 for x in added)
    texts = [t[2] for t in labels_of(base, p)]
    assert "FW EI60" in texts and "FD EI30" in texts
    fw = [t for t in labels_of(base, p) if t[2] == "FW EI60"][0]
    assert fw[0] == 3000.0 and fw[1] == 100.0 + 160.0                                       # 벽 중간, 벽 두께 절반 + 1.6mm 바깥(위쪽)
    fine = mk(base, name="P2", type="plan", level=F.LV1, scale=50)
    assert max(x[3] - x[1] for x in lines_of(base, fine) if x[0] == "symbol" and x[2] == x[4] == 0.0 and x[1] >= 0) == 200.0   # 축척이 작아지면 마디도 줄어든다
    assert base.run("pset.delete", {"id": F.W1, "pset": "Pset_WallCommon", "prop": "FireRating"})["ok"]
    assert lines_of(base, p) == before_l and labels_of(base, p) == before_t                 # 속성을 지우면 원래 도면


def test_방화문_문자는_문_옆에_놓이고_등급이_없으면_없다(base):
    p = mk(base, name="P", type="plan", level=F.LV1)
    t = labels_of(base, p)
    assert len(t) == 1 and t[0][2] == "FD EI30" and abs(t[0][0] - 1500.0) < 1e-6            # D1 은 W1 위 오프셋 1500
    pset(base, F.D2, "Pset_DoorCommon", "FireRating", "EI90")
    assert sorted(x[2] for x in labels_of(base, p)) == ["FD EI30", "FD EI90"]
    assert base.run("pset.delete", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating"})["ok"]
    assert [x[2] for x in labels_of(base, p)] == ["FD EI90"]


# ---------------------------------------------------------------- 외부 마감 (입면)
def test_외부_벽에_재료가_있으면_입면에_해치와_재료_문자를_그린다(base):
    e = mk(base, name="E", type="elevation", dir="S", finish=True)
    plain = mk(base, name="E 기본", type="elevation", dir="S")                          # finish 옵션이 없는 입면은 해치도 재료 문자도 그리지 않는다
    assert hatch(base, e) == [] and labels_of(base, e) == ()                               # 속성이 없으면 해치도 문자도 없다
    external(base, F.W1)
    assert hatch(base, plain) == [] and labels_of(base, plain) == ()
    h = hatch(base, e)
    assert len(h) > 20
    assert all(abs((x[3] - x[1]) - (x[4] - x[2])) < 0.01 or abs((x[3] - x[1]) + (x[4] - x[2])) < 0.01 for x in h)   # 45도
    assert all(-100.01 <= x[1] and x[3] <= 6100.01 and -0.01 <= x[2] and x[4] <= 3000.01 for x in h)   # 모서리 조인 보정으로 벽 끝이 반 두께 더 나간다
    # 문 개구부(x 1050..1950, z 0..2100) 안에는 해치가 없다
    for x in h:
        mx, mz = (x[1] + x[3]) / 2.0, (x[2] + x[4]) / 2.0
        assert not (1050.0 + 1 < mx < 1950.0 - 1 and mz < 2100.0 - 1), x
    lab = labels_of(base, e)
    assert len(lab) == 1 and lab[0][2] == "Concrete" and lab[0][1] == 2550.0 and lab[0][0] == 3050.0   # 보이는 해치가 가장 많은 조각(문 위 띠)의 해치 범위 가운데
    ln = lines_of(base, e)
    assert any(x[0] == "proj" for x in ln)                                                  # 기존 윤곽선은 그대로


def test_재료가_없거나_외부가_아니면_해치가_없다(base):
    e = mk(base, name="E", type="elevation", dir="S", finish=True)
    pset(base, F.W3, "Pset_WallCommon", "IsExternal", True)                                 # 외부지만 재료 없음
    pset(base, F.W2, "Pset_WallCommon", "IsExternal", False)
    assert hatch(base, e) == []
    pset(base, F.W1, "Pset_WallCommon", "IsExternal", False)                              # 재료는 있지만 외부 아님
    assert hatch(base, e) == [] and labels_of(base, e) == ()
    sec = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
    external(base, F.W1)
    assert hatch(base, sec) == []                                                           # 단면은 대상 아님
    assert hatch(base, mk(base, name="P", type="plan", level=F.LV1)) == []


def test_앞벽에_가려진_뒷벽의_해치는_개구부로만_보인다(base):
    e = mk(base, name="E", type="elevation", dir="S", finish=True)
    external(base, F.W3, "Brick")                                                           # 뒤쪽(y=4000) 벽만 외부 마감
    h = hatch(base, e)
    assert h, "문 개구부로 뒷벽이 보여야 한다"
    for x in h:
        assert 1050.0 - 0.01 <= x[1] and x[3] <= 1950.0 + 0.01 and x[2] <= 2100.0 + 0.01 and x[4] <= 2100.0 + 0.01, x
    assert [t[2] for t in labels_of(base, e)] == ["Brick"]


def test_방화와_마감_도면은_증분이_전체_재생성과_같다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1), "e": mk(base, name="E", type="elevation", dir="S", finish=True), "e2": mk(base, name="E2", type="elevation", dir="N", finish=True)}

    def check():
        for k, v in views.items():
            ref = s3lib.RefGraph(s3lib._Inputs(base.eng))
            assert labels_of(base, v) == ref.q(D.view_labels, v), k
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k

    check()
    pset(base, F.W1, "Pset_WallCommon", "FireRating", "EI60")
    check()
    external(base, F.W1)
    check()
    external(base, F.W3, "Brick")
    check()
    assert base.run("wall.move", {"id": F.W1, "dx": 0, "dy": -300})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("material.set", {"id": F.W1, "name": "Stone"})["ok"]
    check()
    assert any(t[2] == "Stone" for t in labels_of(base, views["e"]))


def test_방화와_마감_도면은_결정적이다(tmp_path):
    def build(name):
        s = s3lib.kit.Session(tmp_path / name, guid=s3lib.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        pset(s, F.W1, "Pset_WallCommon", "FireRating", "EI60")
        external(s, F.W1)
        external(s, F.W2, "Brick")
        vs = [mk(s, name="P", type="plan", level=F.LV1), mk(s, name="E", type="elevation", dir="S", finish=True), mk(s, name="E2", type="elevation", dir="E", finish=True)]
        out = ([lines_of(s, v) for v in vs], [labels_of(s, v) for v in vs], s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a[0] == b[0] and a[1] == b[1] and a[2]["state"] == b[2]["state"]
