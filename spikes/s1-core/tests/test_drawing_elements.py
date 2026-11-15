# SPDX-License-Identifier: MPL-2.0
"""1단계 도면 요소 표현: 창(평면·단면·입면에서 문 호 없음), 패밀리 기둥(평면 절단 채움·단면·입면), 증분 == 전체 재생성, 결정성."""
import sys

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402

WND, COL = "0" * 21 + "7", "0" * 21 + "8"


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


def fills_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_fills, vid)


def mk(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def place_window(sess, offset=3000):
    r = sess.run("family.place", {"id": WND, "family": "window.fixed", "host": F.W1, "offset": offset})
    assert r["ok"], r


def place_column(sess, pos=(2000.0, 2000.0), **tv):
    r = sess.run("family.place", {"id": COL, "family": "column.section", "type_values": tv, "level": F.LV1, "position": list(pos)})
    assert r["ok"], r


def symbols(lines):
    return [ln for ln in lines if ln[0] == "symbol"]


# ---------------------------------------------------------------- 창
def test_창은_평면에서_문_호_없이_벽_두께를_가로지르는_기호로_그려진다(base):
    v = mk(base, name="P", type="plan", level=F.LV1)       # 절단 높이 1200: 창 개구부(900..2100) 안
    before = lines_of(base, v)
    place_window(base)                                       # W1(y=0, 두께 200) offset 3000, 폭 1200 -> 2400..3600
    after = lines_of(base, v)
    # 호·문짝은 없다: 문 호 기호(symbol)가 창 때문에 늘어나는 것은 유리 중심선 1개뿐이다(예전에는 문짝 1 + 호 8 이 더 생겼다)
    assert len(symbols(after)) == len(symbols(before)) + 1
    assert ("symbol", 2400.0, 0.0, 3600.0, 0.0) in after      # 유리 중심선: 벽 두께의 가운데를 개구부 폭만큼
    cuts = [ln for ln in after if ln[0] == "cut"]
    assert ("cut", 2400.0, -100.0, 2400.0, 100.0) in cuts and ("cut", 3600.0, -100.0, 3600.0, 100.0) in cuts   # 개구부 마구리
    # 개구부 구간의 두 벽 면 선이 끊기지 않는다(창이 벽 면을 닫는다)
    assert any(ln[2] == ln[4] == -100.0 and ln[1] <= 2400.0 and ln[3] >= 3600.0 for ln in cuts)
    assert any(ln[2] == ln[4] == 100.0 and ln[1] <= 2400.0 and ln[3] >= 3600.0 for ln in cuts)


def test_절단_높이가_창_밖이면_창_기호를_그리지_않는다(base):
    place_window(base)
    low = mk(base, name="P-low", type="plan", level=F.LV1, cut_height=500)     # 창턱(900) 아래: 벽이 이어진다
    ln = lines_of(base, low)
    assert ("symbol", 2400.0, 0.0, 3600.0, 0.0) not in ln
    xs = sorted({x for s in ln if s[0] == "cut" and s[2] == s[4] == -100.0 for x in (s[1], s[3])})
    assert xs[0] == 0.0 and xs[-1] == 6100.0                                   # 벽 아랫면 선이 한 줄로 이어진다


def test_창은_입면과_단면에서_창틀과_유리_선이_보인다(base):
    place_window(base)
    el = mk(base, name="E", type="elevation", dir="S")
    ln = lines_of(base, el)
    assert ("proj", 2460.0, 960.0, 3540.0, 960.0) in ln and ("proj", 2460.0, 2040.0, 3540.0, 2040.0) in ln    # 안쪽 틀(frame_w 60)
    assert ("proj", 2400.0, 900.0, 3600.0, 900.0) in ln and ("proj", 2400.0, 2100.0, 3600.0, 2100.0) in ln    # 개구부 둘레
    sec = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
    sl = lines_of(base, sec)
    assert any(x[0] == "proj" and x[1] == x[3] and x[2] == 960.0 and x[4] == 2040.0 for x in sl)             # 유리 모서리(수직)
    assert any(x[0] == "proj" and x[2] == x[4] == 960.0 for x in sl)                                         # 창틀 아랫면


def test_창_추가_삭제는_다른_뷰를_건드리지_않고_원래_선분으로_돌아간다(base):
    p2 = mk(base, name="P2", type="plan", level=F.LV2)
    p1 = mk(base, name="P1", type="plan", level=F.LV1)
    b1, b2 = lines_of(base, p1), lines_of(base, p2)
    place_window(base)
    assert lines_of(base, p1) != b1 and lines_of(base, p2) == b2
    assert base.run("element.delete", {"id": WND})["ok"]
    assert lines_of(base, p1) == b1


# ---------------------------------------------------------------- 기둥(패밀리 column.section)
def test_기둥은_평면에서_절단_채움과_대각선_기호로_그려진다(base):
    p1 = mk(base, name="P1", type="plan", level=F.LV1)
    p2 = mk(base, name="P2", type="plan", level=F.LV2)
    b1, b2 = lines_of(base, p1), lines_of(base, p2)
    place_column(base, section="rect", width=400, depth=400)
    ln = lines_of(base, p1)
    new = [x for x in ln if x not in b1]
    assert {("cut", 1800.0, 1800.0, 2200.0, 1800.0), ("cut", 2200.0, 1800.0, 2200.0, 2200.0)} <= set(new)      # 절단 윤곽
    assert len([x for x in new if x[0] == "symbol"]) == 2                                                        # 대각선 두 개
    assert ((1800.0, 1800.0), (2200.0, 1800.0), (2200.0, 2200.0), (1800.0, 2200.0)) in fills_of(base, p1)      # 절단 채움
    assert lines_of(base, p2) == b2 and fills_of(base, p2) == ()                                                 # 다른 층 평면은 그대로
    high = mk(base, name="P-hi", type="plan", level=F.LV1, cut_height=3500)                                      # 기둥 높이(3000) 위를 자른다
    assert not any(len(poly) == 4 and poly[0] == (1800.0, 1800.0) for poly in fills_of(base, high))


def test_원형_기둥은_다각형_근사로_채워지고_결정적이다(base):
    place_column(base, section="round", width=400, depth=400)
    p1 = mk(base, name="P1", type="plan", level=F.LV1)
    f = [poly for poly in fills_of(base, p1) if len(poly) == D.CIRC_N]
    assert len(f) == 1
    xs = [p[0] for p in f[0]]
    assert abs(min(xs) - 1800.0) < 1e-3 and abs(max(xs) - 2200.0) < 1e-3                                        # 반지름 200 원
    assert lines_of(base, p1) == lines_of(base, p1)
    assert base.run("family.set", {"id": COL, "rotation": 45})["ok"]                                            # 회전해도 평가가 돈다
    assert len([poly for poly in fills_of(base, p1) if len(poly) == D.CIRC_N]) == 1


def test_기둥은_단면과_입면에서_절단_채움과_가림을_갖는다(base):
    place_column(base, section="rect", width=400, depth=400)
    sec = mk(base, name="S", type="section", p1=[2000, -2000], p2=[2000, 2000])
    assert ((3800.0, 0.0), (4200.0, 0.0), (4200.0, 3000.0), (3800.0, 3000.0)) in fills_of(base, sec)           # 단면 절단 채움
    el = mk(base, name="E", type="elevation", dir="S")
    ln = lines_of(base, el)
    # 정면 벽 W1 이 가리고, 문 D1 개구부(x 1050..1950, 높이 2100)로 기둥 왼쪽 모서리만 보인다
    assert ("proj", 1800.0, 0.0, 1800.0, 2100.0) in ln
    assert not any(x[0] == "proj" and x[1] == x[3] and 1950.0 < x[1] <= 2200.0 and x[2] < 2100.0 for x in ln)   # 가려진 기둥 오른쪽 모서리


def test_기둥_증분이_전체_재생성과_같고_삭제하면_돌아간다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1), "s": mk(base, name="S", type="section", p1=[2000, -2000], p2=[2000, 2000]),
             "e": mk(base, name="E", type="elevation", dir="S")}
    before = {k: lines_of(base, v) for k, v in views.items()}
    place_window(base)
    place_column(base, section="chamfered")
    for k, v in views.items():
        assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k
    assert base.run("family.set", {"id": COL, "position": [3000, 1000], "rotation": 30})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k
    assert base.run("element.delete", {"id": COL})["ok"] and base.run("element.delete", {"id": WND})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k


def test_사인_코사인은_libm_없이_정확하다():
    import math
    for a in (0, 30, 45, 90, 135, 180, 270, 360, -45, 721, 1.5):
        sn, cs = D._sincos_deg(a)
        assert abs(sn - math.sin(math.radians(a))) < 1e-12 and abs(cs - math.cos(math.radians(a))) < 1e-12, a
