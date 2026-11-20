# SPDX-License-Identifier: MPL-2.0
"""W1 1-F 3차: T 조인(ARC-03), 천장(ARC-21), 경사로(ARC-32), 패밀리 유형 바꾸기(UI-04).
- T 조인: 몸통 벽은 자르지 않고(줄기 끝이 몸통 끝 1000mm 안이어도), 줄기 끝은 몸통 면에서 멈춘다. 평면은 몸통 면 선을 줄기 폭만큼 끊고 줄기 끝선은 없다
- join.auto tee 는 T 를 찾아 같은 표식을 단다. join.clear 는 표식을 뗀다. 분할하면 끝 쪽 표식은 새 벽이 가진다
- 천장: 공간 경계·천장 높이로 수평 판(IfcCovering CEILING), 밑면 = 천장 높이. 격자는 반사 천장 평면에만 긋는다
- 경사로: 걸음선·오름·참으로 옆 윤곽 판(IfcRamp). 1/12 보다 급하면 경고, 1/3 보다 급하면 거절. 평면에 오름 화살표
- 유형 바꾸기: 같은 패밀리의 타입으로 바꾸고 다시 평가한다(타입 관계도 바뀐다)
- 재생·IFC 왕복 해시가 같다. 합성 데이터만 쓴다."""
import sys

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import queries as Q
from dobim_core.engine import Engine

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

W5, W6 = "1" + "0" * 20 + "5", "1" + "0" * 20 + "6"
FI = "0" * 21 + "1"


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r, code="CONSTRAINT"):
    assert not r["ok"] and r["error"]["code"] == code, r
    return r["error"]


def _g(s):
    s.eng.sync()
    s.eng._touch_pending()
    return s.eng.g


def _tee_model(s):
    ok(s.run("wall.create", {"id": W5, "level": F.LV1, "axis": [4500, 0, 4500, 3000], "thickness": 200, "height": 3000}))
    return ok(s.run("join.tee", {"stem": W5, "host": F.W1}))


# ================================================================ T 조인
def test_T_조인은_몸통을_자르지_않고_줄기는_면에서_멈춘다(base):
    s = base
    r = _tee_model(s)
    assert r["end"] == "start" and s.eng.st.el[W5]["params"]["tee"] == ["start"]
    g = _g(s)
    assert g.q(Q.wall_trim, F.W1)[:2] == (0.0, 6000.0)
    assert g.q(Q.wall_trim, W5)[:2] == (0.0, 3000.0)        # 논리 길이는 축선 교점 기준 그대로
    assert g.q(Q.wall_extent, W5) == (100.0, 3000.0)         # 형상은 몸통 면(반 두께 100)에서 멈춘다
    assert g.q(Q.ifc_spec, W5)["footprint"][0] == (100.0, -100.0)
    ok(s.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    lines = _g(s).q(D.view_lines, F.V1)
    face = sorted((min(l[1], l[3]), max(l[1], l[3])) for l in lines if l[0] == "cut" and l[2] == l[4] == 100.0)
    assert (1950.0, 4400.0) in face and any(a == 4600.0 for a, _ in face)    # 몸통 안쪽 면 선이 줄기 폭(4400~4600)만큼 끊긴다
    assert not [l for l in lines if l[0] == "cut" and l[2] == l[4] == 100.0 and min(l[1], l[3]) < 4500 < max(l[1], l[3])]
    assert ok(s.run("model.validate", {}))["errors"] == 0


def test_몸통_끝_가까이의_T_도_몸통을_자르지_않는다(base):
    s = base
    ok(s.run("wall.create", {"id": W6, "level": F.LV1, "axis": [5200, 3000, 5200, 4000], "thickness": 100, "height": 3000}))
    ok(s.run("join.set", {"a": W6, "b": F.W3}))                              # 표식 없는 조인: 예전처럼 몸통이 잘린다
    assert _g(s).q(Q.wall_trim, F.W3)[0] == 800.0
    ok(s.run("join.clear", {"a": W6, "b": F.W3}))
    r = ok(s.run("join.auto", {"level": F.LV1, "tee": True}))
    assert [W6, F.W3] in r["tees"]
    g = _g(s)
    assert g.q(Q.wall_trim, F.W3)[:2] == (0.0, 6000.0) and g.q(Q.wall_extent, W6) == (0.0, 900.0)
    ok(s.run("join.clear", {"a": W6, "b": F.W3}))
    assert "tee" not in s.eng.st.el[W6]["params"]
    assert "tees" not in ok(s.run("join.auto", {"level": F.LV1}))           # tee 를 주지 않으면 예전과 같다(T 는 건드리지 않는다)


def test_T_조인_거절과_분할(base):
    s = base
    err(s.run("join.tee", {"stem": F.W3, "host": F.W1}))                     # 나란한 벽
    ok(s.run("wall.create", {"id": W6, "level": F.LV1, "axis": [3000, -3000, 3000, -2000], "thickness": 200, "height": 3000}))
    err(s.run("join.tee", {"stem": W6, "host": F.W1}))                       # 줄기 끝이 몸통에서 2000mm
    _tee_model(s)
    r = ok(s.run("wall.split", {"id": W5, "at": 1500}))
    assert s.eng.st.el[W5]["params"]["tee"] == ["start"] and "tee" not in s.eng.st.el[r["new"]]["params"]
    assert ok(s.run("model.validate", {}))["errors"] == 0


# ================================================================ 천장
def test_천장은_공간에서_만들고_격자는_반사_천장_평면에만(base):
    s = base
    ok(s.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "name": "사무실", "category": "사무실",
                              "ceiling_height": 2500}))
    r = ok(s.run("ceiling.create", {"id": F.SO1, "space": F.SP1, "grid": [600, 600]}))
    p = s.eng.st.el[F.SO1]["params"]
    assert r["height"] == 2500 and p["base"][2] == 2515.0 and p["grid"]["origin"] == [3000.0, 2000.0]
    assert s.eng.st.el[F.SO1]["ifc_class"] == "IfcCovering"
    ok(s.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.rcp", {"id": F.VR1, "name": "1F 반사 천장", "level": F.LV1}))
    g = _g(s)
    hat = [l for l in g.q(D.view_lines, F.VR1) if l[0] == "hatch"]
    assert len(hat) == 9 + 7                                                  # x 3000±600k(600~5400, 9줄), y 2000±600k(200~3800, 7줄)
    assert not [l for l in g.q(D.view_lines, F.V1) if l[0] == "hatch"]        # 평면에는 없다(절단면 위)
    ok(s.run("ceiling.set", {"id": F.SO1, "height": 2700, "grid_origin": [0, 0]}))
    p = s.eng.st.el[F.SO1]["params"]
    assert p["base"][2] == 2715.0 and p["grid"] == {"size": [600.0, 600.0], "origin": [0.0, 0.0]}
    ok(s.run("ceiling.set", {"id": F.SO1, "clear_grid": True}))
    assert not [l for l in _g(s).q(D.view_lines, F.VR1) if l[0] == "hatch"]   # 격자 없는 천장판은 반사 천장 평면에 그리지 않는다(22단계 솔리드 천장과 같다)


def test_천장_거절(base):
    s = base
    ok(s.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "name": "실", "category": "사무실"}))
    err(s.run("ceiling.create", {"space": F.SP1}))                           # 천장 높이 없음
    err(s.run("ceiling.create", {"level": F.LV1, "boundary": [[0, 0], [10, 0], [0, 10]]}))   # height 없음
    assert s.run("ceiling.create", {"space": F.SP1, "level": F.LV1, "height": 2400})["error"]["code"] == "SCHEMA_INVALID"
    ok(s.run("solid.create", {"id": F.SO1, "level": F.LV1, "category": "beam", "form": "prism", "polygon": [[0, 0], [100, 0], [100, 100]], "z0": 0, "z1": 100}))
    err(s.run("ceiling.set", {"id": F.SO1, "height": 2400}))


# ================================================================ 경사로
def test_경사로는_IfcRamp_판이고_평면에_오름_화살표(base):
    s = base
    r = s.run("ramp.create", {"level": F.LV1, "run": [0, -3000, 6000, -3000], "rise": 450, "width": 1500, "landing_top": 1500})
    assert r["ok"] and r["warnings"] == [] and r["result"]["slope_pct"] == 7.5
    row = s.eng.st.el[r["result"]["id"]]
    assert row["ifc_class"] == "IfcRamp" and row["params"]["line"] == [0.0, -3000.0, 7500.0, -3000.0]
    assert row["params"]["profile"] == [[0.0, -150.0], [6000.0, 300.0], [7500.0, 300.0], [7500.0, 450.0], [6000.0, 450.0], [0.0, 0.0]]
    ok(s.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    sym = [l for l in _g(s).q(D.view_lines, F.V1) if l[0] == "symbol" and l[2] < -1000]
    assert ("symbol", 375.0, -3000.0, 7125.0, -3000.0) in sym and len(sym) == 3    # 낮은 끝 -> 높은 끝, 화살촉 두 줄
    assert any(l[0] == "proj" and l[2] == l[4] == -3750.0 for l in _g(s).q(D.view_lines, F.V1))   # 경사로 옆선(보이는 깊이 아래여도 그린다)
    w = s.run("ramp.create", {"level": F.LV1, "run": [0, -6000, 3000, -6000], "rise": 450})["warnings"]
    assert w and "1/12" in w[0]
    err(s.run("ramp.create", {"level": F.LV1, "run": [0, -9000, 1000, -9000], "rise": 500}))
    assert ok(s.run("model.validate", {}))["errors"] == 0


# ================================================================ 패밀리 유형 바꾸기
def test_패밀리_유형_바꾸기(base):
    s = base
    t0 = ok(s.run("family.place", {"id": FI, "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 1500}))["type"]
    r = ok(s.run("family.set_type", {"id": FI, "type_values": {"width": 1000}}))
    p = s.eng.st.el[FI]["params"]
    assert r["from"] == t0 and p["type"] == r["type"] and p["width"] == 1000.0 and p["values"]["swing"] == "R"
    assert ok(s.run("relation.list", {"id": FI})) and r["type"] in repr(ok(s.run("relation.list", {"id": FI}))) and t0 not in repr(ok(s.run("relation.list", {"id": FI})))
    r2 = ok(s.run("family.set_type", {"id": FI, "type": t0}))                 # 이미 있는 타입으로 되돌린다
    assert r2["type"] == t0 and not r2["type_created"] and s.eng.st.el[FI]["params"]["width"] == 900.0
    err(s.run("family.set_type", {"id": FI, "type": t0}))                     # 같은 타입
    col = ok(s.run("family.place", {"family": "column.section", "type_values": {"section": "round"}, "level": F.LV1, "position": [1000, 1000]}))
    err(s.run("family.set_type", {"id": FI, "type": col["type"]}))            # 다른 패밀리
    err(s.run("family.set_type", {"id": FI, "type_values": {"width": 9000}}), "FAMILY_INVALID")   # 레시피 범위 밖
    assert ok(s.run("model.validate", {}))["errors"] == 0


# ================================================================ 재생·IFC 왕복
def _script(run):
    ok(run("wall.create", {"id": W5, "level": F.LV1, "axis": [4500, 0, 4500, 3000], "thickness": 200, "height": 3000}))
    ok(run("join.tee", {"stem": W5, "host": F.W1}))
    ok(run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[0, 0], [4400, 0], [4400, 3900], [0, 3900]], "name": "실", "category": "사무실",
                            "ceiling_height": 2400}))
    ok(run("ceiling.create", {"space": F.SP1, "grid": [600, 1200]}))
    ok(run("ramp.create", {"level": F.LV1, "run": [0, -3000, 6000, -3000], "rise": 450, "landing_bottom": 1200, "landing_top": 1500}))
    ok(run("family.place", {"id": FI, "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 1500}))
    ok(run("family.set_type", {"id": FI, "type_values": {"width": 1000}}))


def test_새_요소는_다시_열어도_같다(tmp_path):
    s = kit.Session(tmp_path / "t.dobim")
    for n, a in F.SCRIPT:
        ok(s.run(n, a))
    _script(s.run)
    h = s.eng.hashes()
    assert s.eng.intent_consistency() == []
    s.close()
    e = Engine(tmp_path / "t.dobim", "t", create=False)
    try:
        h2 = e.hashes()
        assert (h2["state"], h2["derived"], h2["ifc"]) == (h["state"], h["derived"], h["ifc"])
    finally:
        e.close()


def test_IFC_왕복은_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        _script(b.run)
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()
