# SPDX-License-Identifier: MPL-2.0
"""W2 ARC-04 곡선 벽 편집(W2 세 번째 PR): 분할·동심 간격띄우기·트림/연장·모서리·T 조인 줄기.
- 분할: at = 호 위 거리, 두 조각은 같은 원 위의 호, 문은 놓인 쪽으로(offset - at), 조인은 교점 쪽 조각, 이력(succ)
- 간격띄우기: 반지름 -/+ distance 동심 호(복사·이동), 호스팅 요소는 같은 각도에 남는다
- 트림/연장: 곡선 벽 끝을 직선·곡선 경계까지 호를 따라 늘이거나 자르고, 직선 벽 끝을 곡선 벽(원)까지 늘인다
- T 조인 줄기: 곡선 벽 끝이 직선·곡선 몸통 면에서 멈춘다
- 되돌리기·증분 = 전체 재계산, IFC 왕복 해시. 합성 데이터만 쓴다."""
import math
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import arcgeom as AG
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import queries as Q

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

AW = "1" + "0" * 20 + "9"      # 반원 앱스 곡선 벽: (0,4000) -> (-2000,2000) -> (0,0), 중심 (0,2000) 반지름 2000(반시계)
NW = "1" + "0" * 19 + "a9"     # 분할로 생기는 끝 쪽 벽
AD = "2" + "0" * 20 + "9"
V, VS = F.V1, F.V2
PTS = [[0, 4000], [-2000, 2000], [0, 0]]
L = 2000 * math.pi


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def g_of(s):
    s.eng.sync()
    s.eng._touch_pending()
    return s.eng.g


def params(s, eid):
    return ok(s.run("element.get", {"id": eid}))["params"]


def apse(s):
    ok(s.run("wall.arc", {"id": AW, "level": F.LV1, "points": PTS, "thickness": 200, "height": 3000}))
    ok(s.run("join.set", {"a": F.W3, "b": AW}))
    ok(s.run("join.set", {"a": F.W1, "b": AW}))
    ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [-3000, 2000], "p2": [7000, 2000]}))
    return s


@pytest.fixture
def ab(base):
    return apse(base)


def same_as_full(s):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in (V, VS):
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)


def on_circle(pt, c, r, tol=1e-3):
    return abs(math.hypot(pt[0] - c[0], pt[1] - c[1]) - r) < tol


# ---------------------------------------------------------------- 분할
def test_곡선_벽_분할은_호_위_거리로_나누고_문과_조인을_나눠_가진다(ab):
    ok(ab.run("door.place", {"id": AD, "host": AW, "offset": 4500, "width": 900, "height": 2100}))
    assert err(ab.run("wall.split", {"id": AW, "at": 4300}))["code"] == "CONSTRAINT"          # 문을 가로지른다
    assert err(ab.run("wall.split", {"id": AW, "at": 0.5}))["code"] == "CONSTRAINT"
    r = ok(ab.run("wall.split", {"id": AW, "at": L / 2, "new_id": NW}))
    assert r["split_at"] == pytest.approx([-2000, 2000]) and r["doors_moved"] == [AD]
    pw, pn = params(ab, AW), params(ab, NW)
    assert pw["axis"][:2] == [0, 4000] and pn["axis"][2:] == [0, 0]
    for p in (pw, pn):                                                                # 같은 원 위의 사분원 둘
        ai = AG.info(p["axis"], p["arc"])
        assert ai[:3] == pytest.approx((0, 2000, 2000), abs=1e-3) and AG.length(ai) == pytest.approx(L / 2)
    assert params(ab, AD)["offset"] == pytest.approx(4500 - L / 2)
    g = g_of(ab)
    assert g.q(Q.wall_joins, AW) == (F.W3,) and g.q(Q.wall_joins, NW) == (F.W1,)
    assert g.q(Q.wall_trim, AW)[2] == "ok" and g.q(Q.wall_trim, NW)[2] == "ok"
    assert on_circle(g.q(Q.door_world, AD)[:2], (0, 2000), 2000)
    assert g.q(Q.rel_out, "succ", AW) == (NW,)
    same_as_full(ab)
    ok(ab.run("history.undo", {}))
    assert params(ab, AW)["arc"] == [-2000.0, 2000.0] and params(ab, AD)["offset"] == 4500


# ---------------------------------------------------------------- 간격띄우기
def test_곡선_벽_간격띄우기는_동심_호다(ab):
    ok(ab.run("door.place", {"id": AD, "host": AW, "offset": L / 2, "width": 900, "height": 2100}))
    r = ok(ab.run("wall.offset", {"ids": [AW], "distance": 500}))                    # 반시계 호의 왼쪽 = 중심 쪽: 반지름 1500
    nw, nd = r["ids"]
    p = params(ab, nw)
    ai = AG.info(p["axis"], p["arc"])
    assert ai[:3] == pytest.approx((0, 2000, 1500), abs=1e-3) and p["axis"] == [0.0, 3500.0, 0.0, 500.0]
    assert params(ab, nd)["offset"] == pytest.approx(1500 * math.pi / 2)              # 같은 각도(호 가운데)
    ok(ab.run("wall.offset", {"ids": [AW], "distance": -600, "keep": False}))         # 이동: 반지름 2600, 문도 같은 각도
    p = params(ab, AW)
    assert AG.info(p["axis"], p["arc"])[2] == pytest.approx(2600) and params(ab, AD)["offset"] == pytest.approx(1300 * math.pi)
    assert err(ab.run("wall.offset", {"ids": [nw], "distance": 1450}))["code"] == "CONSTRAINT"   # 반지름 50 < 두께/2
    same_as_full(ab)


# ---------------------------------------------------------------- 트림/연장·모서리
def test_곡선_벽_끝을_직선_벽까지_늘이고_직선_벽을_곡선_벽까지_늘인다(base):
    q = ok(base.run("wall.arc", {"level": F.LV1, "center": [3000, 0], "radius": 2500, "start_angle": 30, "end_angle": 150}))["id"]
    r = ok(base.run("wall.extend_to", {"id": q, "to": F.W1, "end": "start"}))       # 시작(30도)을 호를 따라 y=0 까지(0도)
    assert r["point"] == pytest.approx([5500, 0]) and r["changed"]
    p = params(base, q)
    ai = AG.info(p["axis"], p["arc"])
    assert ai[:3] == pytest.approx((3000, 0, 2500), abs=1e-3) and AG.length(ai) == pytest.approx(2500 * math.pi * 150 / 180)
    s = ok(base.run("wall.create", {"level": F.LV1, "axis": [3000, 500, 3000, 1500], "thickness": 200}))["id"]
    r = ok(base.run("wall.extend_to", {"id": s, "to": q}))                           # 직선 벽 끝을 곡선 벽 원(위쪽 교점)까지
    assert r["end"] == "end" and r["point"] == pytest.approx([3000, 2500])
    assert err(base.run("wall.extend_to", {"id": q, "to": F.W2}))["code"] == "CONSTRAINT"   # x=6000 직선과 원(x 는 500~5500)이 만나지 않는다


def test_곡선_벽_트림과_모서리(base):
    q = ok(base.run("wall.arc", {"level": F.LV1, "center": [3000, 0], "radius": 2500, "start_angle": 10, "end_angle": 150}))["id"]
    assert err(base.run("wall.extend_to", {"id": q, "to": F.W2}))["code"] == "CONSTRAINT"    # x=6000 직선과 원이 만나지 않는다
    s = ok(base.run("wall.create", {"level": F.LV1, "axis": [4000, 1000, 4000, 3000], "thickness": 200}))["id"]
    r = ok(base.run("wall.extend_to", {"id": q, "to": s, "end": "start"}))            # 시작(10도)을 x=4000 까지 자른다
    assert r["point"][0] == pytest.approx(4000) and on_circle(r["point"], (3000, 0), 2500)
    p = params(base, q)
    assert AG.length(AG.info(p["axis"], p["arc"])) < 2500 * math.pi * 140 / 180
    t = ok(base.run("wall.create", {"level": F.LV1, "axis": [-1000, 2000, 0, 2000], "thickness": 200}))["id"]
    r = ok(base.run("wall.corner", {"a": q, "b": t}))                                 # 끝(150도)을 y=2000 직선과의 교점(1500, 2000)까지 자르고, 직선 벽도 늘인다
    assert r["joined"] and r["ends"] == {q: "end", t: "end"} and on_circle(r["point"], (3000, 0), 2500)
    g = g_of(base)
    assert g.q(Q.wall_trim, q)[2] == "ok" and g.q(Q.wall_trim, t)[2] == "ok"
    ok(base.run("history.undo", {}))
    assert params(base, t)["axis"] == [-1000.0, 2000.0, 0.0, 2000.0]


def test_곡선_벽끼리_모서리(base):
    a = ok(base.run("wall.arc", {"level": F.LV1, "center": [0, 0], "radius": 3000, "start_angle": 0, "end_angle": 80}))["id"]
    b = ok(base.run("wall.arc", {"level": F.LV1, "center": [4000, 0], "radius": 3000, "start_angle": 100, "end_angle": 170}))["id"]
    r = ok(base.run("wall.corner", {"a": a, "b": b}))
    assert on_circle(r["point"], (0, 0), 3000) and on_circle(r["point"], (4000, 0), 3000) and r["point"][1] > 0
    assert r["point"][0] == pytest.approx(2000)
    g = g_of(base)
    assert g.q(Q.wall_trim, a)[2] == "ok" and g.q(Q.wall_trim, b)[2] == "ok"


# ---------------------------------------------------------------- T 조인 줄기
def test_곡선_벽이_T_조인_줄기가_된다(base):
    ok(base.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    q = ok(base.run("wall.arc", {"level": F.LV1, "center": [5000, 0], "radius": 2000, "start_angle": 180, "end_angle": 120, "thickness": 200}))["id"]   # 시작 (3000,0)이 W1 가운데, 접선은 W1 에 수직
    r = ok(base.run("join.tee", {"stem": q, "host": F.W1}))
    assert r["end"] == "start" and params(base, q)["tee"] == ["start"]
    g = g_of(base)
    assert g.q(Q.wall_trim, F.W1)[:2] == pytest.approx((0, 6000))                       # 몸통은 자르지 않는다
    st, en = g.q(Q.wall_extent, q)
    assert st == pytest.approx(100, abs=1)                                              # 줄기는 몸통 반 두께(접선 수직)만큼 물러난다
    lines = g.q(D.view_lines, V)
    assert lines
    ok(base.run("join.clear", {"a": q, "b": F.W1}))
    assert "tee" not in params(base, q)
    ok(base.run("history.undo", {}))
    assert params(base, q)["tee"] == ["start"]
    r = ok(base.run("wall.split", {"id": q, "at": 1000}))                               # 줄기 시작 끝은 원래 벽이 가진다
    assert params(base, q)["tee"] == ["start"] and "tee" not in params(base, r["new"])
    full = s3lib.RefGraph(s3lib._Inputs(base.eng))
    assert g_of(base).q(D.view_lines, V) == full.q(D.view_lines, V)


# ---------------------------------------------------------------- IFC 왕복
def test_편집한_곡선_벽의_IFC_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("wall.arc", {"id": AW, "level": F.LV1, "points": [[0, -6000], [-2000, -8000], [0, -10000]], "thickness": 200, "height": 3000}))
        ok(b.run("wall.split", {"id": AW, "at": 2000, "new_id": NW}))
        ok(b.run("wall.offset", {"ids": [NW], "distance": -500}))
        s = ok(b.run("wall.create", {"level": F.LV1, "axis": [-5000, -8000, -4000, -8000], "thickness": 200}))["id"]
        ok(b.run("wall.extend_to", {"id": s, "to": AW}))
        q = ok(b.run("wall.arc", {"level": F.LV1, "center": [-1500, -8000], "radius": 2000, "start_angle": 180, "end_angle": 120, "thickness": 200}))["id"]
        ok(b.run("join.tee", {"stem": q, "host": s}))
        h = b.hashes()
        out = b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        h2 = b.hashes("imp.dobim")
        assert (h2["derived"], h2["ifc"]) == (h["derived"], h["ifc"])            # 분할 이력(succ)은 직선 벽처럼 IFC 에 싣지 않아 state 는 다르다
        for e in (AW, NW, s, q):
            assert ok(b.run("element.get", {"id": e}, project="imp.dobim"))["params"] == ok(b.run("element.get", {"id": e}))["params"]
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
    finally:
        b.close()


# ---------------------------------------------------------------- 상단 붙이기
ROOF = {"id": F.RF1, "level": F.LV2, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "form": "gable", "pitch": 30, "overhang": 600}
TAN30 = math.tan(math.radians(30))


def roof_z(y):
    return 3100.0 + (2000.0 - abs(y - 2000.0)) * TAN30      # 박공 지붕 아랫면(1F 기준): 용마루 y = 2000


def test_곡선_벽_상단을_박공_지붕에_붙인다(base):
    from dobim_core import walltop as WT
    ok(base.run("roof.create", ROOF))
    q = ok(base.run("wall.arc", {"level": F.LV1, "center": [3000, 2000], "radius": 1500, "start_angle": 200, "end_angle": 340}))["id"]
    r = ok(base.run("wall.attach_top", {"id": q, "targets": [F.RF1]}))
    p = params(base, q)
    ai = AG.info(p["axis"], p["arc"])
    n = AG.length(ai)
    assert len(r["covered"]) == 1 and r["covered"][0][1] == pytest.approx(n, abs=1e-3) and p["top_profile"][-1][0] == pytest.approx(n)
    for u in (0.0, n / 3, n / 2, n):                                                 # 조각 경계 사이 선형(현과 호 차 2mm 이하)
        x, y = AG.point(ai, u)
        assert WT.eval_at(p["top_profile"], u) == pytest.approx(roof_z(y), abs=3.0)
    E = "4" + "0" * 20 + "3"
    ok(base.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 3500}))
    ok(base.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [0, 1000], "p2": [6000, 1000]}))
    ok(base.run("view.create", {"id": E, "name": "남측", "type": "elevation", "dir": "S"}))
    g = g_of(base)
    cut, proj = g.q(D.plan_pieces, V, q)
    assert cut and proj                                                              # 절단 높이 3500: 용마루 쪽은 자르고 처마 쪽은 투영
    rects = g.q(D.vcut_rects, VS, q)
    assert len(rects) >= 2 and all(roof_z(850) < max(z for _, z in rc) < roof_z(1150) for rc in rects)   # 비스듬히 자른 조각들: 윗변은 그 자리 지붕 높이
    tops = [ln for ln in g.q(D.vedges, E, q) if abs(ln[0][2] - ln[1][2]) > 1e-6 and abs(ln[0][0] - ln[1][0]) > 1e-6]
    assert tops and max(max(a[2], b[2]) for a, b in tops) == pytest.approx(max(z for _, z in p["top_profile"]))   # 기울어진 윗변
    full = s3lib.RefGraph(s3lib._Inputs(base.eng))
    for vv in (V, VS, E):
        assert g.q(D.view_lines, vv) and g.q(D.view_lines, vv) == full.q(D.view_lines, vv)
    assert err(base.run("door.place", {"host": q, "offset": n / 2, "width": 600, "height": 3500}))["code"] == "CONSTRAINT"   # 호 가운데 윤곽(약 3389) 위로 나간다
    ok(base.run("wall.detach_top", {"id": q}))
    assert "top_profile" not in params(base, q)


def _ifc_errors(ff):
    import ifcopenshell.validate as IV
    lg = IV.json_logger()
    IV.validate(ff, lg, express_rules=True)
    return [x["message"] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def _volume(f, eid):
    import ifcopenshell.geom as geom
    import numpy as np
    sh = geom.create_shape(geom.settings(), f.by_guid(eid))
    vs = np.array(sh.geometry.verts).reshape(-1, 3)
    fs = np.array(sh.geometry.faces).reshape(-1, 3)
    return abs(sum(np.dot(vs[a], np.cross(vs[b], vs[c])) for a, b, c in fs)) / 6.0 * 1e9


def test_상단_윤곽_곡선_벽과_사각_개구부의_IFC(tmp_path):
    import ifcopenshell
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("wall.arc", {"id": AW, "level": F.LV1, "points": [[0, -6000], [-2000, -8000], [0, -10000]], "thickness": 200, "height": 3000}))
        ok(b.run("wall.set", {"id": AW, "top_profile": [[0, 3000], [L / 2, 4000], [L, 3000]]}))
        ok(b.run("wall.arc", {"id": NW, "level": F.LV1, "center": [-6000, -8000], "radius": 2000, "start_angle": 90, "end_angle": 270}))
        ok(b.run("wall.set", {"id": NW, "openings": [{"shape": "rect", "offset": L / 2, "sill": 900, "width": 1200, "height": 1200}]}))
        assert err(b.run("wall.set", {"id": NW, "openings": [{"shape": "round", "offset": L / 2, "sill": 900, "width": 1200, "height": 1600}]}))["code"] in ("CONSTRAINT", "SCHEMA_INVALID")
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        w = f.by_guid(AW)
        assert w.Representation.Representations[0].RepresentationType == "Brep"
        ring = math.pi * (2100 ** 2 - 1900 ** 2) / 2
        assert _volume(f, AW) == pytest.approx(ring * 3500, rel=0.01)               # 평균 높이 3500(호 위 거리로 선형)
        assert _volume(f, NW) == pytest.approx(ring * 3000 - 1200 * 1200 * 200, rel=0.01)   # 사각 개구부를 뺀다
        ops = [r.RelatedOpeningElement for r in f.by_type("IfcRelVoidsElement") if r.RelatingBuildingElement == f.by_guid(NW)]
        assert len(ops) == 1
        assert _ifc_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"]
        assert _ifc_errors(ifcopenshell.open(str(tmp_path / "t3.ifc"))) == []
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        ok(b.run("wall.detach_top", {"id": AW}))                                    # 떼면 돌출로 되돌아간다
        f = ifcopenshell.open(str(b.export("t4.ifc")))
        assert f.by_guid(AW).Representation.Representations[0].RepresentationType == "SweptSolid"
    finally:
        b.close()


# ---------------------------------------------------------------- 곡선 벽 사각 모양 개구부
def test_곡선_벽_사각_모양_개구부는_호_위_거리와_높이_범위다(ab):
    ok(ab.run("wall.set", {"id": AW, "openings": [{"shape": "rect", "offset": L / 2, "sill": 900, "width": 1200, "height": 1200}]}))
    assert err(ab.run("wall.set", {"id": AW, "openings": [{"shape": "rect", "offset": 100, "sill": 900, "width": 1200, "height": 1200}]}))["code"] == "CONSTRAINT"
    g = g_of(ab)
    assert any(o[:2] == pytest.approx((L / 2 - 600, L / 2 + 600)) and o[2:] == pytest.approx((900, 2100)) for o in g.q(D.wall_openings, AW))
    cut = [ln for ln in g.q(D.plan_wall_lines, V, AW) if ln[0] == "cut"]
    jambs = [ln for ln in cut if all(abs(math.hypot(x, y - 2000) - rr) > 0.5 for x, y in ((ln[1], ln[2]), (ln[3], ln[4])) for rr in (2100, 1900)) is False
             and abs(math.hypot(ln[1], ln[2] - 2000) - math.hypot(ln[3], ln[4] - 2000)) > 150]
    assert len(jambs) >= 2                                                            # 절단 높이 1200: 개구부 양 끝 두께 방향 선
    ok(ab.run("wall.split", {"id": AW, "at": 1000, "new_id": NW}))                     # 분할하면 개구부는 놓인 쪽(끝 쪽) 벽으로
    assert params(ab, NW)["openings"][0]["offset"] == pytest.approx(L / 2 - 1000) and "openings" not in params(ab, AW)
    ok(ab.run("history.undo", {}))
    ok(ab.run("element.mirror", {"ids": [AW], "axis": [0, 0, 0, 1000]}))               # 대칭은 u -> L - u (가운데라 그대로)
    assert params(ab, AW)["openings"][0]["offset"] == pytest.approx(L / 2)
    same_as_full(ab)


def test_직선_벽_대칭은_모양_개구부_위치를_뒤집는다(base):
    """첫 PR(곡선 벽)이 xform.wall 의 대칭 조건을 곡선 벽 조건으로 바꿔 직선 벽 대칭이 개구부·상단 윤곽을 뒤집지 않던 결함(재현 -> 수정)."""
    ok(base.run("wall.set", {"id": F.W2, "openings": [{"shape": "rect", "offset": 1000, "sill": 900, "width": 600, "height": 1000}],
                             "top_profile": [[0, 3000], [4000, 3600]]}))
    ok(base.run("element.mirror", {"ids": [F.W2], "axis": [0, 2000, 1000, 2000]}))
    p = params(base, F.W2)
    assert p["openings"][0]["offset"] == 3000.0 and p["top_profile"] == [[0.0, 3600.0], [4000.0, 3000.0]]
