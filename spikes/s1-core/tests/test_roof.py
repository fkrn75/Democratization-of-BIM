# SPDX-License-Identifier: MPL-2.0
"""지붕 슬래브(slab use=roof) 와 지붕 평면: 커맨드·검사, IFC PredefinedType ROOF 투영·왕복(물매·배수구는 의도로), 지붕 평면(윤곽·물매 화살표·물매 문자·배수구),
지붕이 아닌 슬래브의 도면 불변, 증분 == 전체 재생성, 결정성."""
import sys

import ifcopenshell
import pytest

import kit
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

BOUND = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
HOLE = [[2000, 1000], [3000, 1000], [3000, 3000], [2000, 3000]]
ROOF = {"use": "roof", "fall": {"dir": 180, "pct": 2}, "drains": [[500, 2000]]}


def mk_roof(sess, **kw):
    args = {"id": F.S1, "level": F.LV2, "boundary": BOUND, "thickness": 200, **ROOF, **kw}
    r = sess.run("slab.create", args)
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


def has_seg(ln, cls, x0, y0, x1, y1, tol=0.01):
    return any(s[0] == cls and all(abs(a - b) <= tol for a, b in zip(s[1:], (x0, y0, x1, y1))) for s in ln)


# ---------------------------------------------------------------- 커맨드·검사
def test_지붕_슬래브_생성_수정_이동과_실행_취소(base):
    h0 = base.eng.state_hash()
    mk_roof(base)
    g = base.run("element.get", {"id": F.S1})["result"]
    assert g["params"]["use"] == "roof" and g["params"]["fall"] == {"dir": 180.0, "pct": 2.0} and g["params"]["drains"] == [[500.0, 2000.0]]
    assert g["derived"]["roof"] is True and g["derived"]["drains"] == 1
    assert base.run("slab.set", {"id": F.S1, "fall": {"dir": 90, "pct": 1.5}, "drains": [[500, 2000], [5500, 2000]]})["ok"]
    assert base.run("element.get", {"id": F.S1})["result"]["params"]["fall"] == {"dir": 90.0, "pct": 1.5}
    assert base.run("slab.move", {"id": F.S1, "dx": 100, "dy": 50})["ok"]
    assert base.run("element.get", {"id": F.S1})["result"]["params"]["drains"] == [[600.0, 2050.0], [5600.0, 2050.0]]   # 배수구도 따라 움직인다
    assert base.run("slab.set", {"id": F.S1, "clear_fall": True, "clear_drains": True})["ok"]
    p = base.run("element.get", {"id": F.S1})["result"]["params"]
    assert p["use"] == "roof" and "fall" not in p and "drains" not in p
    assert base.run("slab.set", {"id": F.S1, "use": "floor"})["ok"]
    assert "use" not in base.run("element.get", {"id": F.S1})["result"]["params"] and "roof" not in base.run("element.get", {"id": F.S1})["result"]["derived"]
    for _ in range(5):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_잘못된_지붕_입력은_거절한다(base):
    plain = {"id": F.S1, "level": F.LV2, "boundary": BOUND}
    for bad in ({"fall": {"dir": 0, "pct": 2}}, {"drains": [[500, 2000]]},                              # 지붕이 아닌데 물매·배수구
                {**ROOF, "fall": {"dir": 0, "pct": 0}}, {**ROOF, "fall": {"dir": 0, "pct": 101}},       # 물매 범위
                {**ROOF, "drains": [[7000, 2000]]}, {**ROOF, "drains": [[0, 2000]]},                    # 슬래브 밖·경계 위
                {**ROOF, "openings": [HOLE], "drains": [[2500, 2000]]},                                  # 구멍 안
                {**ROOF, "drains": []}, {"use": "floor"}):
        assert not base.run("slab.create", {**plain, **bad})["ok"], bad
    assert base.run("slab.create", plain)["ok"]
    assert not base.run("slab.set", {"id": F.S1, "fall": {"dir": 0, "pct": 2}})["ok"]                   # 일반 슬래브에는 물매를 둘 수 없다
    assert base.run("slab.set", {"id": F.S1, "use": "roof", "fall": {"dir": 0, "pct": 2}})["ok"]
    assert not base.run("slab.set", {"id": F.S1, "drains": [[9000, 9000]]})["ok"]
    assert not base.run("slab.set", {"id": F.S1, "use": "floor", "drains": [[500, 2000]]})["ok"]


# ---------------------------------------------------------------- IFC
def test_IFC는_지붕_슬래브를_ROOF로_내보낸다(base, tmp_path):
    mk_roof(base)
    assert base.run("slab.create", {"id": F.S1[:-1] + "9", "level": F.LV1, "boundary": BOUND})["ok"]
    base.eng.flush_ifc()
    out = tmp_path / "r.ifc"
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    assert f.by_guid(F.S1).is_a("IfcSlab") and f.by_guid(F.S1).PredefinedType == "ROOF"
    assert f.by_guid(F.S1[:-1] + "9").PredefinedType == "FLOOR"                                         # 일반 슬래브는 그대로 FLOOR
    assert base.run("model.validate", {})["ok"]


def test_지붕_슬래브를_내보내고_가져오면_물매와_배수구까지_되살아나고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": BOUND, **ROOF})["ok"]
        h = b.hashes()
        b.export("r.ifc")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"], r
        assert b.origins("imp.dobim") == {"authored": 8, "foreign": 0, "detached": 0}
        g = b.run("element.get", {"id": F.S1}, project="imp.dobim")["result"]
        assert g["origin"] == "authored" and g["params"]["use"] == "roof" and g["params"]["fall"] == {"dir": 180.0, "pct": 2.0} and g["params"]["drains"] == [[500.0, 2000.0]]
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("slab.set", {"id": F.S1, "fall": {"dir": 0, "pct": 3}}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_외부에서_PredefinedType을_바꾸면_분리된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": BOUND, **ROOF})["ok"]
        b.export("r.ifc")
        f = ifcopenshell.open(str(tmp_path / "r.ifc"))
        f.by_guid(F.S1).PredefinedType = "FLOOR"
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
    finally:
        b.close()


# ---------------------------------------------------------------- 지붕 평면
def test_지붕_평면은_윤곽_물매_화살표_물매_문자_배수구를_그린다(base):
    mk_roof(base)
    p = mk(base, name="RF", type="plan", level=F.LV2, cut_height=1200)                                  # 절단 높이 4500 > 지붕 윗면 3300
    ln = lines_of(base, p)
    for a, b in zip(BOUND, BOUND[1:] + BOUND[:1]):                                                      # 윤곽 4변
        x0, y0, x1, y1 = (*sorted([a, b])[0], *sorted([a, b])[1])
        assert has_seg(ln, "proj", x0, y0, x1, y1), (a, b)
    assert has_seg(ln, "symbol", 2400.0, 2000.0, 3600.0, 2000.0)                                       # 중심에서 -x 쪽으로 흐르는 화살표(길이 12 용지 mm = 1200)
    arms = [x for x in ln if x[0] == "symbol" and ((abs(x[1] - 2400) < 0.01 and abs(x[2] - 2000) < 0.01 and abs(x[4] - 2000) > 1) or (abs(x[3] - 2400) < 0.01 and abs(x[4] - 2000) < 0.01 and abs(x[2] - 2000) > 1))]
    assert len(arms) == 2   # 화살촉 두 선(화살 끝에서 뒤로 +-20도)
    assert has_seg(ln, "symbol", 350.0, 2000.0, 650.0, 2000.0) and has_seg(ln, "symbol", 500.0, 1850.0, 500.0, 2150.0)   # 배수구 십자
    lab = [t for t in labels_of(base, p) if t[2].startswith("물매")]
    assert lab == [(3000.0, 1820.0, "물매 2%", D.LABEL_SUB_H, "middle")]


def test_절단_높이가_지붕_안이면_지붕_표시_없이_평소처럼_절단선만_그린다(base):
    mk_roof(base)
    p = mk(base, name="RF", type="plan", level=F.LV2, cut_height=-100)                                  # 절단 높이 3200: 두께 3100..3300 안
    ln = lines_of(base, p)
    assert any(s[0] == "cut" for s in ln) and not any(s[0] == "symbol" for s in ln) and not labels_of(base, p)


def test_구멍이_있는_지붕은_구멍_표시도_함께_그린다(base):
    mk_roof(base, openings=[HOLE])
    p = mk(base, name="RF", type="plan", level=F.LV2, cut_height=1200)
    ln = lines_of(base, p)
    assert has_seg(ln, "proj", 2000.0, 1000.0, 3000.0, 1000.0) and has_seg(ln, "symbol", 2000.0, 1000.0, 3000.0, 3000.0)   # 구멍 윤곽·X
    assert has_seg(ln, "proj", 0.0, 0.0, 6000.0, 0.0)


def test_지붕이_아닌_슬래브의_도면은_바뀌지_않는다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV2, cut_height=1200), "s": mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000]),
             "e": mk(base, name="E", type="elevation", dir="S")}
    assert base.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": BOUND, "thickness": 200})["ok"]
    assert lines_of(base, views["p"]) == () and labels_of(base, views["p"]) == ()                      # 평면은 벽이 윤곽을 준다: 일반 슬래브는 그리지 않는다(예전과 같다)
    before = {k: lines_of(base, v) for k, v in views.items()}
    assert base.run("slab.set", {"id": F.S1, "use": "roof", "fall": {"dir": 0, "pct": 2}})["ok"]
    after = {k: lines_of(base, v) for k, v in views.items()}
    assert after["s"] == before["s"] and after["e"] == before["e"]                                      # 단면·입면은 같은 슬래브 모양이다
    assert after["p"] != before["p"]
    assert base.run("slab.set", {"id": F.S1, "use": "floor"})["ok"]
    assert {k: lines_of(base, v) for k, v in views.items()} == before


def test_지붕_증분이_전체_재생성과_같다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV2, cut_height=1200), "p0": mk(base, name="P0", type="plan", level=F.LV2, cut_height=-100),
             "p1": mk(base, name="P1", type="plan", level=F.LV1), "s": mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000]),
             "e": mk(base, name="E", type="elevation", dir="S")}
    before = {k: lines_of(base, v) for k, v in views.items()}

    def check():
        for k, v in views.items():
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k
            assert labels_of(base, v) == full_labels(base, v), k

    mk_roof(base)
    check()
    assert base.run("slab.set", {"id": F.S1, "fall": {"dir": 45, "pct": 5}, "drains": [[500, 2000], [5000, 3000]]})["ok"]
    check()
    assert base.run("slab.set", {"id": F.S1, "openings": [HOLE]})["ok"]
    check()
    assert base.run("slab.move", {"id": F.S1, "dx": 500, "dy": 300})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("slab.delete", {"id": F.S1})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k
    check()


def test_지붕_도면과_해시는_결정적이다(tmp_path):
    def build(name):
        s = kit.Session(tmp_path / name, guid=F.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        mk_roof(s)
        vs = [mk(s, name="RF", type="plan", level=F.LV2, cut_height=1200)]
        out = ([lines_of(s, v) for v in vs], [labels_of(s, v) for v in vs], s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a == b
