# SPDX-License-Identifier: MPL-2.0
"""과업 ⑤(5만 요소 성능): 이웃 판정 격자 색인과 깊이 컬링이 예전 전수 판정과 같은 값을 내는지.

합성 격자 모델(tools/bench_50k.py, 약 1,000 요소)에서
  - plan_nbrs·vnbrs = 뷰 안 모든 벽을 훑는 예전 판정(순서까지)
  - _visible_culled = 벽 자기·이웃 전체 + 그 밖의 가림 조각으로 부른 visible_parts
  - vnbrs_front 로 뺀 이웃은 실제로 어떤 선분도 가리지 않는다(위 비교에 포함)
"""
import pytest

import bench_50k as B
from dobim_core import drawing as D


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    import kit
    p = tmp_path_factory.mktemp("perf") / "s.dobim"
    B.build(1000, p)
    s = kit.Session(p, create=False)
    yield s
    s.close()


def _brute_nbrs(g, bbfn, v, w):
    me = g.q(bbfn, v, w)
    if me is None:
        return ()
    out = []
    for o in g.q(D.scope_walls, v):
        if o == w:
            continue
        bb = g.q(bbfn, v, o)
        if bb is not None and bb[0] <= me[2] + D.NBR_PAD and me[0] <= bb[2] + D.NBR_PAD \
                and bb[1] <= me[3] + D.NBR_PAD and me[1] <= bb[3] + D.NBR_PAD:
            out.append(o)
    return tuple(out)


@pytest.mark.parametrize("view,nb,bb", [("plan", "plan_nbrs", "plan_bbox"), ("section", "vnbrs", "vbbox"), ("elevation", "vnbrs", "vbbox")])
def test_색인_이웃은_전수_판정과_같다(synth, view, nb, bb):
    g, v = synth.eng.g, B.VIEWS[view]
    walls = g.q(D.scope_walls, v)
    assert len(walls) > 400
    for w in walls:
        assert g.q(getattr(D, nb), v, w) == _brute_nbrs(g, getattr(D, bb), v, w)


@pytest.mark.parametrize("view", ["section", "elevation"])
def test_깊이_컬링은_전체_가림_판정과_같다(synth, view):
    g, v = synth.eng.g, B.VIEWS[view]
    others = list(D._other_occ_boxes(g, v))
    checked = hidden = 0
    for w in g.q(D.scope_walls, v):
        occ = D._wall_occluders(g, v, w) + [b[5] for b in others]
        boxed = sorted(D._occ_boxed(occ), key=D._W_KEY)
        for e0, e1 in g.q(D.vedges, v, w):
            slanted = abs(e1[2] - e0[2]) > 1e-9 and (abs(e1[0] - e0[0]) > 1e-9 or abs(e1[1] - e0[1]) > 1e-9)
            if slanted:
                continue
            ref = D.visible_parts(e0, e1, occ)
            assert D._visible_culled(e0, e1, boxed) == ref
            checked += 1
            hidden += not ref
    assert checked > 1000 and hidden > 100   # 가려진 선과 보이는 선이 모두 있다


def test_색인_이웃은_벽_이동을_따라간다(synth):
    """벽을 옮기면 색인이 다시 계산돼 이웃이 전수 판정과 계속 같다(증분 그래프)."""
    g, v = synth.eng.g, B.V_PLAN
    walls = g.q(D.scope_walls, v)
    w = walls[len(walls) // 2]
    r = synth.run("wall.move", {"id": w, "dx": 2500.0, "dy": 1300.0})
    assert r["ok"], r
    for x in g.q(D.scope_walls, v):
        assert g.q(D.plan_nbrs, v, x) == _brute_nbrs(g, D.plan_bbox, v, x)
    assert synth.run("history.undo", {})["ok"]
