# SPDX-License-Identifier: MPL-2.0
"""L 조인 모서리의 코어 형상 (BIM 지침 B-02: 도면 내용은 BIM 데이터와 같아야 한다).

예전에는 L 조인 모서리를 도면만 표시용으로 채웠고(drawing.wall_range) IFC 벽 윤곽에는 반 두께 노치가 남았다. 지금은 코어가 id 가 작은 벽을 상대 벽 반 두께만큼 연장하고
(queries.wall_extent), IFC 투영(wall_footprint)과 도면(wall_range)이 같은 값을 쓴다.

 1. 도면 벽 범위 == IFC 윤곽(투영 명세) == 내보낸 IFC 파일의 실제 형상(독립 확인)
 2. 바뀌는 범위: L 조인(id 작은 벽의 조인 쪽 끝)뿐이다. 조인 없는 벽·T 조인·얕은 각은 예전 IFC 형상 그대로, 논리 길이(wall_trim·일람표 길이)는 그대로다
 3. 편집(조인 변경·두께·이동·되돌리기) 뒤 증분 상태 == 로그에서 다시 만든 전체 상태
 4. 도면(평면 선분·채움)은 예전과 같다: 도면은 이미 같은 범위를 그리고 있었다
"""
from math import sqrt

import ifcopenshell
import ifcopenshell.geom

from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import queries as Q
from dobim_core.engine import Engine


def _g(sess):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g


def _spec_fp(sess, w):
    return _g(sess).q(Q.ifc_spec, w)["footprint"]


def _ifc_range(tmp_path, sess, w):
    """내보낸 IFC 파일에서 벽 w 의 실제 형상을 읽어 벽 축선 방향 범위(mm)를 구한다(엔진 코드와 독립)."""
    name = f"e-{w[-4:]}-{len(list(tmp_path.glob('e-*.ifc')))}.ifc"               # 호출마다 새 파일(덮어쓰기 확인 절차를 피한다)
    r = sess.run("ifc.export", {"path": name})
    if not r["ok"]:
        r = sess.run("ifc.export", {"path": name}, confirm=r["error"]["details"]["confirm_token"])
    assert r["ok"], r
    f = ifcopenshell.open(str(tmp_path / name))
    st = ifcopenshell.geom.settings()
    st.set("use-world-coords", True)
    shape = ifcopenshell.geom.create_shape(st, f.by_guid(w))
    v = shape.geometry.verts
    pts = [(v[i] * 1000.0, v[i + 1] * 1000.0) for i in range(0, len(v), 3)]
    x1, y1, x2, y2 = sess.eng.st.el[w]["params"]["axis"]
    ln = sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    c, s = (x2 - x1) / ln, (y2 - y1) / ln
    ts = [(px - x1) * c + (py - y1) * s for px, py in pts]
    return min(ts), max(ts)


def test_c1_L_조인에서_도면_범위_투영_명세_IFC_파일_형상이_같다(base, tmp_path):
    g = _g(base)
    rng = g.q(D.wall_range, F.W1)
    trim = g.q(Q.wall_trim, F.W1)
    assert rng[0] == trim[0] and rng[1] == trim[1] + 100.0        # 상대 벽 W2 두께 200 의 반(수직이라 sin=1)만큼 조인 끝이 나간다
    fp = _spec_fp(base, F.W1)
    assert (fp[0][0], fp[1][0]) == rng and fp[0][1] == -100.0     # 투영 명세 윤곽 == 도면 범위
    lo, hi = _ifc_range(tmp_path, base, F.W1)
    assert abs(lo - rng[0]) < 1e-3 and abs(hi - rng[1]) < 1e-3, (lo, hi, rng)   # 내보낸 IFC 의 실제 형상도 같다
    # 상대 벽(id 큰 쪽)은 연장하지 않는다: 코어 형상 그대로
    assert g.q(D.wall_range, F.W2) == g.q(Q.wall_trim, F.W2)[:2]
    lo2, hi2 = _ifc_range(tmp_path, base, F.W2)
    t2 = g.q(Q.wall_trim, F.W2)
    assert abs(lo2 - t2[0]) < 1e-3 and abs(hi2 - t2[1]) < 1e-3


def test_c2_조인_없는_벽과_T_조인은_예전_형상이고_논리_길이는_그대로다(base):
    g = _g(base)
    assert g.q(Q.wall_extent, F.W3) == g.q(Q.wall_trim, F.W3)[:2]             # 조인 없는 벽
    assert _spec_fp(base, F.W3) == ((0.0, -100.0), (6000.0, -100.0), (6000.0, 100.0), (0.0, 100.0))
    assert g.q(Q.wall_length, F.W1) == 6000.0                                   # 논리 길이(일람표·치수 참조)는 축선 교점 기준 그대로
    # T 조인: W3 끝을 W2 중간에 닿게 한 새 벽은 연장이 없다
    t = "1" + "0" * 20 + "9"
    assert base.run("wall.create", {"id": t, "level": F.LV1, "axis": [3000, 2000, 6000, 2000], "thickness": 200, "height": 3000})["ok"]
    assert base.run("join.set", {"a": t, "b": F.W2})["ok"]
    g = _g(base)
    assert g.q(Q.wall_extent, t) == g.q(Q.wall_trim, t)[:2] and g.q(Q.wall_extent, F.W2) == g.q(Q.wall_trim, F.W2)[:2]
    # 얕은 각(sin < 0.2): 연장하지 않는다
    u, v = "1" + "0" * 20 + "a", "1" + "0" * 20 + "b"
    assert base.run("wall.create", {"id": u, "level": F.LV1, "axis": [0, 8000, 6000, 8000], "thickness": 200, "height": 3000})["ok"]
    assert base.run("wall.create", {"id": v, "level": F.LV1, "axis": [6000, 8000, 12000, 8600], "thickness": 200, "height": 3000})["ok"]
    assert base.run("join.set", {"a": u, "b": v})["ok"]
    g = _g(base)
    assert g.q(Q.wall_extent, u) == g.q(Q.wall_trim, u)[:2]


def test_c3_두께를_바꿔도_증분과_전체가_같고_연장이_따라간다(base, tmp_path):
    assert base.run("wall.set", {"id": F.W2, "thickness": 400})["ok"]
    assert _g(base).q(D.wall_range, F.W1)[1] == 6000.0 + 200.0            # 상대 두께 400 -> 200 연장
    assert _spec_fp(base, F.W1)[1][0] == 6200.0                           # IFC 윤곽도 따라간다(캐시가 낡지 않는다)
    h = base.eng.hashes()
    base.eng.close()
    eng = Engine(tmp_path / "t.dobim", "t", create=False)                 # 로그에서 다시 만든 전체 상태
    try:
        h2 = eng.hashes()
        assert (h2["state"], h2["derived"], h2["ifc"]) == (h["state"], h["derived"], h["ifc"])
        assert eng.intent_consistency() == []
    finally:
        eng.close()


def test_c4_조인을_풀면_연장이_사라지고_되돌리면_돌아온다(base):
    h0 = base.eng.hashes()
    assert _g(base).q(D.wall_range, F.W1)[1] == 6100.0
    assert base.run("join.clear", {"a": F.W1, "b": F.W2})["ok"]
    assert _g(base).q(D.wall_range, F.W1)[1] == 6000.0 and _spec_fp(base, F.W1)[1][0] == 6000.0
    assert base.run("history.undo", {})["ok"]
    assert _g(base).q(D.wall_range, F.W1)[1] == 6100.0
    h1 = base.eng.hashes()
    assert (h1["state"], h1["derived"], h1["ifc"]) == (h0["state"], h0["derived"], h0["ifc"])
