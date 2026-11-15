# SPDX-License-Identifier: MPL-2.0
"""평면 절단 채움이 슬래브 구멍을 비운다(열한 번째 작업): 구멍이 있는 슬래브가 절단 높이에 걸리면 채움은 경계 + 가는 틈으로 이은 구멍 고리 하나다.
구멍이 없으면 채움은 이전과 같다(바이트 불변). 증분 == 전체 재생성."""
import sys

import pytest

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402

BOUND = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
HOLE = [[2000, 1000], [3000, 1000], [3000, 3000], [2000, 3000]]
HOLE2 = [[4000, 1000], [5000, 1000], [5000, 2000], [4000, 2000]]
L_BOUND = [[0, 0], [8000, 0], [8000, 3000], [3000, 3000], [3000, 6000], [0, 6000]]   # 오목(ㄴ자)
L_HOLES = [[[500, 500], [1500, 500], [1500, 1500], [500, 1500]], [[500, 4000], [2000, 4000], [2000, 5000], [500, 5000]],
           [[4000, 800], [7000, 800], [7000, 2000], [4000, 2000]]]


def mk_slab(sess, boundary=BOUND, openings=(HOLE,), **kw):
    args = {"id": F.S1, "level": F.LV1, "boundary": boundary, "thickness": 200, "top_offset": 0, **kw}
    if openings:
        args["openings"] = [list(o) for o in openings]
    r = sess.run("slab.create", args)
    assert r["ok"], r


def mk_plan(sess, cut_height):
    r = sess.run("view.create", {"name": "P", "type": "plan", "level": F.LV1, "cut_height": cut_height})
    assert r["ok"], r
    return r["result"]["id"]


def fills_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_fills, vid)


def winding(pt, ring):
    """점 pt 의 감김수(0 이 아니면 비영 규칙으로 칠해진다)."""
    x, y = pt
    w = 0
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        if a[1] <= y < b[1] and (b[0] - a[0]) * (y - a[1]) - (x - a[0]) * (b[1] - a[1]) > 0:
            w += 1
        elif b[1] <= y < a[1] and (b[0] - a[0]) * (y - a[1]) - (x - a[0]) * (b[1] - a[1]) < 0:
            w -= 1
    return w


def inside(pt, poly):
    x, y = pt
    c = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) / (b[1] - a[1]) * (b[0] - a[0]):
            c = not c
    return c


def area2(ring):
    return sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1] for i in range(len(ring)))


def check_fill(fill, boundary, holes, step=250):
    """격자 점마다 채움 감김수가 0 이 아닌 것 == (경계 안 and 어느 구멍 안도 아님) 이다."""
    xs = [p[0] for p in boundary]
    ys = [p[1] for p in boundary]
    bad = []
    for gx in range(int(min(xs)) + step // 2, int(max(xs)), step):
        for gy in range(int(min(ys)) + step // 2, int(max(ys)), step):
            want = inside((gx, gy), boundary) and not any(inside((gx, gy), h) for h in holes)
            if (winding((gx, gy), fill) != 0) != want:
                bad.append((gx, gy))
    assert not bad, bad[:5]
    want_area = abs(area2(boundary)) - sum(abs(area2(h)) for h in holes)
    assert abs(area2(fill)) == pytest.approx(want_area)


def test_구멍이_있는_슬래브의_절단_채움은_구멍을_비운다(base):
    mk_slab(base)
    f = fills_of(base, mk_plan(base, -100))                       # 절단 높이 -100: 슬래브 두께(-200..0) 안
    assert len(f) == 1
    check_fill(f[0], [tuple(p) for p in BOUND], [[tuple(p) for p in HOLE]])


def test_구멍이_여럿이고_경계가_오목해도_채움이_맞다(base):
    mk_slab(base, boundary=L_BOUND, openings=L_HOLES)
    (f,) = fills_of(base, mk_plan(base, -100))
    check_fill(f, [tuple(p) for p in L_BOUND], [[tuple(p) for p in h] for h in L_HOLES], step=100)


def test_구멍이_없으면_채움은_이전과_같다(base):
    mk_slab(base, openings=())
    (f,) = fills_of(base, mk_plan(base, -100))
    assert f == tuple((float(x), float(y)) for x, y in BOUND)      # 경계 그대로(구멍 처리 없음)


def test_구멍_있는_슬래브_채움의_증분이_전체_재생성과_같다(base):
    vid = mk_plan(base, -100)
    mk_slab(base, openings=())
    assert fills_of(base, vid) == s3lib.RefGraph(s3lib._Inputs(base.eng)).q(D.view_fills, vid)
    for kw in ({"openings": [HOLE, HOLE2]}, {"openings": [HOLE2]}):
        assert base.run("slab.set", {"id": F.S1, **kw})["ok"]
        assert fills_of(base, vid) == s3lib.RefGraph(s3lib._Inputs(base.eng)).q(D.view_fills, vid)
    assert base.run("slab.set", {"id": F.S1, "clear_openings": True})["ok"]
    (f,) = fills_of(base, vid)
    assert f == tuple((float(x), float(y)) for x, y in BOUND)

