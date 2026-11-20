# SPDX-License-Identifier: MPL-2.0
"""W1 1-F 4차: 임의 외곽 지붕(ARC-18, roof form "poly"), 내장 패밀리 확장(FAM-13·ARC-43).
- 다각형 외곽 + 변마다 물매(0 = 박공 변). 볼록 외곽은 정확한 지붕 골격, L·T 외곽은 골(오목 꼭짓점)이 생긴다
- 직사각형에 모든 변 같은 물매면 모임 지붕(hip)과 같은 형상, 두 짧은 변이 박공이면 박공 지붕과 같은 형상
- 시계 방향 외곽·대칭은 변마다 물매가 같은 변을 따라간다. 박공 변이 오목 꼭짓점에 붙거나 외곽이 꼬이면 거절
- 도면: 지붕 평면에 골·용마루·면마다 물매 문자, 단면 절단 띠, 입면 선. 벽 상단 붙이기·일람표 면적
- 내장 패밀리 16종 추가(가구 7·위생 5·가전 2·문 1·창 1): 전부 레시피 검증(full) 통과·배치·평면 기호
- 재생·IFC 왕복 해시가 같다. 합성 데이터만 쓴다."""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core.engine import Engine
from dobim_core.family import library
from dobim_core.family.validate import validate_recipe

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

T30 = 0.5773502691896257   # tan 30도
L_OUT = [[0, 0], [9000, 0], [9000, 4000], [4000, 4000], [4000, 8000], [0, 8000]]
L_GABLE = [30, 0, 30, 30, 0, 30]   # 두 팔 끝이 박공
RF2 = "e" + "0" * 20 + "2"
FI = "0" * 21 + "1"
NEW_FAMILIES = ("appliance.fridge", "appliance.washer", "bed.double", "cabinet.base", "chair.basic", "desk.basic", "door.double", "sanitary.basin",
                "sanitary.bath", "sanitary.shower", "sanitary.sink", "sanitary.toilet", "shelf.open", "sofa.basic", "table.dining", "window.sliding")


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


def P(boundary, slopes, **kw):
    return {"name": "r", "boundary": [list(map(float, q)) for q in boundary], "form": "poly", "pitch": 30.0, "slopes": [float(v) for v in slopes],
            "overhang": 600.0, "base_offset": 0.0, "thickness": 200.0, **kw}


# ================================================================ 순수 형상
def test_직사각형_모든_변_경사면_모임_지붕과_같다():
    rect = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
    g = K.roof_geom(P(rect, [30] * 4))
    h = K.roof_geom({"name": "r", "boundary": rect, "form": "hip", "pitch": 30.0, "overhang": 600.0, "base_offset": 0.0, "thickness": 200.0})
    assert len(g["faces"]) == len(h["faces"]) == 4 and len(g["hips"]) == 4 and len(g["ridges"]) == 1 and not g["valleys"]
    assert g["ztop"] == pytest.approx(h["ztop"]) and g["zlow"] == pytest.approx(h["zlow"])
    (a, b), = g["ridges"]
    assert sorted([a[0], b[0]]) == pytest.approx([2000, 4000]) and a[1] == pytest.approx(2000)
    area = lambda gm: sum(abs(K.polygon_area2([q[:2] for q in fc])) for fc in gm["faces"])   # noqa: E731
    assert area(g) == pytest.approx(area(h))


def test_직사각형_짧은_변_박공이면_박공_지붕과_같다():
    rect = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
    g = K.roof_geom(P(rect, [30, 0, 30, 0]))
    h = K.roof_geom({"name": "r", "boundary": rect, "form": "gable", "pitch": 30.0, "overhang": 600.0, "base_offset": 0.0, "thickness": 200.0})
    assert len(g["faces"]) == 2 and not g["hips"] and len(g["ridges"]) == 1
    (a, b), = g["ridges"]
    assert sorted([a[0], b[0]]) == pytest.approx([-600, 6600])            # 용마루는 박공 쪽 처마 끝까지 간다
    assert a[2] == pytest.approx(2000 * T30) and g["ztop"] == pytest.approx(h["ztop"])
    assert len(g["perimeter"]) == len(h["perimeter"]) == 6                 # 박공 끝의 용마루 끝점이 둘레에 들어간다


def test_L_외곽은_골이_생기고_용마루가_두_팔에():
    g = K.roof_geom(P(L_OUT, [30] * 6))
    assert len(g["valleys"]) == 1 and len(g["ridges"]) == 2 and len(g["faces"]) == 6
    (a, b), = g["valleys"]
    lo, hi = sorted((a, b), key=lambda q: q[2])
    assert lo[:2] == pytest.approx((4600, 4600)) and hi[:2] == pytest.approx((2000, 2000))   # 오목 꼭짓점(처마 내민)에서 두 용마루가 만나는 점까지
    assert hi[2] == pytest.approx(2000 * T30) and lo[2] == pytest.approx(-600 * T30)
    assert g["ztop"] == pytest.approx(2000 * T30)
    # 박공 끝: 두 팔의 끝 변이 박공이면 용마루가 처마 끝까지 간다
    gg = K.roof_geom(P(L_OUT, L_GABLE))
    ends = sorted(max(q[0] for q in r) if abs(r[0][1] - r[1][1]) < 1 else max(q[1] for q in r) for r in gg["ridges"])
    assert ends == pytest.approx([8600, 9600])
    assert len(gg["valleys"]) == 1 and not gg["hips"][1:]


def test_변마다_물매가_다르면_면마다_물매_문자도_다르다():
    rect = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
    g = K.roof_geom(P(rect, [45, 0, 20, 0], overhang=0.0))
    assert sorted(g["fpitch"]) == [20.0, 45.0]
    (a, b), = g["ridges"]   # 가파른 쪽(아래 변 45도)에서 용마루가 가깝다: tan45 y = tan20 (4000 - y)
    t20 = K.roof_tans([20])[0]
    assert a[1] == pytest.approx(4000 * t20 / (1 + t20), abs=1e-6)


def test_형상은_결정적이고_캐시를_고쳐도_다음_값이_같다():
    g1 = K.roof_geom(P(L_OUT, L_GABLE))
    g1["faces"].clear()
    g2 = K.roof_geom(P(L_OUT, L_GABLE))
    assert g2["faces"] and g2 == K.roof_geom(P(L_OUT, L_GABLE))


@pytest.mark.parametrize("bnd,slopes,why", [
    (L_OUT, [30, 30, 0, 30, 30, 30], "오목 꼭짓점"),                                       # 박공 변이 오목 꼭짓점에 붙음
    ([[0, 0], [3000, 0], [6000, 0], [6000, 4000], [0, 4000]], [30] * 5, "한 직선"),         # 한 직선 위 꼭짓점
    ([[0, 0], [6000, 4000], [6000, 0], [0, 4000]], [30] * 4, "반시계"),                       # 꼬인 외곽(넓이 0)
    ([[0, 0], [6000, 0], [6000, 4000], [0, 4000]], [0] * 4, "경사 변"),                     # 경사 변 없음
    ([[0, 0], [6000, 0], [6000, 300], [0, 300]], [30] * 4, "500mm"),                        # 짧은 변
])
def test_만들_수_없는_외곽은_이유와_함께_거절(bnd, slopes, why):
    assert why in (K.row_params_error("roof", P(bnd, slopes)) or "")


# ================================================================ 커맨드
def test_다각형_지붕_만들기와_고치기(base):
    s = base
    ok(s.run("roof.create", {"id": F.RF1, "level": F.LV2, "boundary": L_OUT, "form": "poly", "pitch": 30, "overhang": 600, "slopes": L_GABLE}))
    p = s.eng.st.el[F.RF1]["params"]
    assert p["form"] == "poly" and p["slopes"] == [30.0, 0.0, 30.0, 30.0, 0.0, 30.0] and "ridge" not in p
    d = ok(s.run("element.get", {"id": F.RF1}))["derived"]
    assert d["faces"] == 4 and d["top"] == pytest.approx(3300 + 2000 * T30)
    ok(s.run("roof.set", {"id": F.RF1, "pitch": 25}))                     # pitch 만 주면 경사 변 전부
    assert s.eng.st.el[F.RF1]["params"]["slopes"] == [25.0, 0.0, 25.0, 25.0, 0.0, 25.0]
    ok(s.run("roof.set", {"id": F.RF1, "slopes": [25, 25, 25, 25, 25, 25]}))
    assert ok(s.run("element.get", {"id": F.RF1}))["derived"]["faces"] == 6
    e = err(s.run("roof.set", {"id": F.RF1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "slopes": [30, 0, 30]}))
    assert "변 수" in e["message"]
    ok(s.run("roof.set", {"id": F.RF1, "form": "hip", "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]}))   # 직사각형 형식으로 되돌리면 slopes 는 지운다
    assert "slopes" not in s.eng.st.el[F.RF1]["params"]
    ok(s.run("roof.set", {"id": F.RF1, "form": "poly"}))                  # 직사각형에서 poly 로: 모든 변 pitch
    assert s.eng.st.el[F.RF1]["params"]["slopes"] == [25.0] * 4


def test_다각형_지붕_거절(base):
    s = base
    e = err(s.run("roof.create", {"level": F.LV2, "boundary": L_OUT, "form": "hip", "pitch": 30}))
    assert "직사각형" in e["message"] and e["hint"]
    err(s.run("roof.create", {"level": F.LV2, "boundary": L_OUT[:4], "form": "gable", "pitch": 30, "slopes": [30] * 4}))
    e = err(s.run("roof.create", {"level": F.LV2, "boundary": L_OUT, "form": "poly", "pitch": 30, "slopes": [30, 30, 0, 30, 30, 30]}))
    assert "오목 꼭짓점" in e["message"]
    err(s.run("roof.create", {"level": F.LV2, "boundary": L_OUT, "form": "poly", "pitch": 30, "slopes": [30] * 5}))
    assert not s.eng.st.by_kind.get("roof")


def test_시계_방향_외곽과_대칭은_물매가_같은_변을_따라간다(base):
    s = base
    n = len(L_OUT)
    cw = L_OUT[::-1]
    cw_slopes = [L_GABLE[(n - 2 - j) % n] for j in range(n)]   # 시계 방향 외곽의 변 j = 반시계 변 n-2-j
    ok(s.run("roof.create", {"id": F.RF1, "level": F.LV2, "boundary": cw, "form": "poly", "pitch": 30, "slopes": cw_slopes}))
    p = s.eng.st.el[F.RF1]["params"]
    assert p["boundary"] == [list(map(float, q)) for q in L_OUT] and p["slopes"] == [float(v) for v in L_GABLE]
    g0 = K.roof_geom(p)
    ok(s.run("element.mirror", {"ids": [F.RF1], "axis": [10000, 0, 10000, 1000]}))
    q = s.eng.st.el[F.RF1]["params"]
    g1 = K.roof_geom(q)
    assert len(g1["ridges"]) == len(g0["ridges"]) and g1["ztop"] == pytest.approx(g0["ztop"]) and len(g1["valleys"]) == 1
    assert sorted(q["slopes"]) == sorted(p["slopes"]) and q["slopes"] != p["slopes"]


def test_다각형_지붕_도면(base):
    s = base
    ok(s.run("roof.create", {"id": F.RF1, "level": F.LV2, "boundary": L_OUT, "form": "poly", "pitch": 30, "overhang": 600, "slopes": [30, 0, 45, 30, 0, 30]}))
    ok(s.run("view.create", {"id": F.V1, "name": "지붕", "type": "plan", "level": F.LV2, "cut_height": 9000}))
    g = _g(s)
    lines = g.q(D.view_lines, F.V1)
    gm = K.roof_geom(s.eng.st.el[F.RF1]["params"])
    (va, vb), = gm["valleys"]
    want = {tuple(round(c, 3) for c in va[:2]), tuple(round(c, 3) for c in vb[:2])}
    assert any(l[0] == "proj" and {(l[1], l[2]), (l[3], l[4])} == want for l in lines)   # 골 선(도면 좌표는 0.001mm 로 맞춘다)
    assert abs(va[0] - 5039.23) < 0.01 or abs(vb[0] - 5039.23) < 0.01   # 물매가 다르면 골이 내민 외곽 모서리가 아닌 곳에서 처마와 만난다
    texts = [t[2] for t in g.q(D.view_labels, F.V1)]
    assert "물매 45°(10.0/10)" in texts and "물매 30°(5.8/10)" in texts and "용마루" in texts
    ok(s.run("view.create", {"id": F.V2, "name": "단면", "type": "section", "p1": [2000, -2000], "p2": [2000, 10000]}))
    g = _g(s)
    assert g.q(D.roof_cut_rects, F.V2, F.RF1)                                   # 절단면이 지붕을 자른 띠
    assert any(l[0] == "cut" for l in g.q(D.roof_lines, F.V2, F.RF1))
    ok(s.run("view.create", {"id": "4" + "0" * 20 + "9", "name": "남", "type": "elevation", "dir": "S"}))
    assert any(l[0] == "proj" for l in _g(s).q(D.roof_lines, "4" + "0" * 20 + "9", F.RF1))


def test_벽_상단_붙이기와_일람표_면적(base):
    s = base
    ok(s.run("roof.create", {"id": F.RF1, "level": F.LV1, "boundary": L_OUT, "form": "poly", "pitch": 30, "overhang": 0, "base_offset": 3000,
                             "slopes": [30] * 6}))
    r = ok(s.run("wall.attach_top", {"id": F.W2, "targets": [F.RF1]}))   # 가로 팔을 가로지르는 벽(x=6000, y 0~4000)
    assert r["covered"] == [[0.0, 4000.0]]
    prof = s.eng.st.el[F.W2]["params"]["top_profile"]
    assert max(z for _, z in prof) == pytest.approx(3000 + 2000 * T30 - 200, abs=1e-3)   # 용마루 아래(지붕 아랫면)
    assert prof[0][1] == pytest.approx(2800, abs=1e-3) and prof[-1][1] == pytest.approx(2800, abs=1e-3)
    gm = K.roof_geom(s.eng.st.el[F.RF1]["params"])
    plan = sum(abs(K.polygon_area2([q[:2] for q in fc])) / 2 for fc in gm["faces"])
    assert plan == pytest.approx(9000 * 4000 + 4000 * 4000)                  # 면들이 외곽을 빈틈없이 덮는다
    t = ok(s.run("schedule.table", {"category": "roofs", "fields": ["form", "area"]}))
    cells = [c for row in t["items"] for c in (row.values() if isinstance(row, dict) else row)]
    assert "poly" in cells
    assert any(isinstance(c, float) and c == pytest.approx(plan / 0.8660254037844387, rel=1e-6) for c in cells)   # 경사면 넓이 = 평면 / cos 30


# ================================================================ 내장 패밀리
def test_내장_패밀리는_23종이고_새_16종은_모두_검증을_통과한다():
    ids = library.ids()
    assert len(ids) == 23 and set(NEW_FAMILIES) <= set(ids)
    for fid in NEW_FAMILIES:
        res = validate_recipe(library.load(fid), "full")
        assert res["ok"] and not res["errors"] and not res["warnings"], (fid, res["errors"][:2], res["warnings"][:2])


def test_family_list_는_쪽을_나눠_모두_보여준다(base):
    got, cur = [], None
    while True:
        env = base.run("family.list", {"limit": 7} | ({"cursor": cur} if cur else {}))
        got += [x["id"] for x in ok(env)["items"]]
        cur = env["page"].get("next_cursor")
        if not cur:
            break
    assert got == library.ids() and all("error" not in x for x in ok(base.run("family.list", {}))["items"])


def test_새_패밀리_배치와_평면_기호(base):
    s = base
    ok(s.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    x = 500
    placed = []
    for fid in NEW_FAMILIES:
        if fid in ("door.double", "window.sliding"):
            continue
        r = ok(s.run("family.place", {"family": fid, "level": F.LV1, "position": [x, 2000]}))
        placed.append(r["id"])
        x += 400
    ok(s.run("family.place", {"family": "door.double", "host": F.W1, "offset": 1500}))
    ok(s.run("family.place", {"family": "window.sliding", "host": F.W3, "offset": 3500}))
    assert len(placed) == 14
    g = _g(s)
    for e in placed:
        assert g.q(D.fam_lines, F.V1, e), e                                    # 평면 기호(또는 절단선)가 있다
    v = ok(s.run("model.validate", {}))
    assert not [m for m in v["violations"] if "IfcGloballyUniqueId" not in m], v


# ================================================================ 재생·IFC 왕복
def _script(run):
    ok(run("roof.create", {"id": F.RF1, "level": F.LV2, "boundary": L_OUT, "form": "poly", "pitch": 30, "overhang": 600, "slopes": L_GABLE}))
    ok(run("roof.create", {"id": RF2, "level": F.LV2, "boundary": [[12000, 0], [18000, 0], [20000, 3000], [15000, 7000], [10000, 3000]], "form": "poly",
                           "pitch": 35, "overhang": 300}))
    ok(run("family.place", {"id": FI, "family": "sofa.basic", "level": F.LV1, "position": [2000, 2000], "rotation": 90}))
    ok(run("family.place", {"family": "sanitary.toilet", "level": F.LV1, "position": [4000, 2000]}))
    ok(run("family.place", {"family": "door.double", "host": F.W1, "offset": 3000}))


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


def test_IFC_지붕_형식_값():
    from dobim_core.ifc_model import _poly_roof_type
    assert _poly_roof_type([30] * 6) == "HIP_ROOF" and _poly_roof_type(L_GABLE) == "HIPPED_GABLE_ROOF" and _poly_roof_type([30, 0, 0, 0]) == "SHED_ROOF"


def test_IFC4_IFC2x3_내보내기는_스키마_검증을_통과한다(base):
    import ifcopenshell
    import ifcopenshell.validate
    s = base
    _script(s.run)
    ok(s.run("family.place", {"family": "appliance.washer", "level": F.LV1, "position": [1000, 1000]}))
    ok(s.run("family.place", {"family": "window.sliding", "host": F.W3, "offset": 3000}))
    for name, schema in (("m4.ifc", None), ("m2.ifc", "IFC2X3")):
        args = {"path": name} | ({"schema": schema} if schema else {})
        r = s.run("ifc.export", args)
        r = s.run("ifc.export", args, confirm=r["error"]["details"]["confirm_token"])
        assert r["ok"], r
        f = ifcopenshell.open(str(s.path.parent / name))
        lg = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(f, lg, express_rules=True)
        bad = [x["message"] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]]
        assert bad == [], (name, bad[:3])
        roofs = {e.Name: getattr(e, "PredefinedType", None) or getattr(e, "ShapeType", None) for e in f.by_type("IfcRoof")}   # IFC2x3 은 ShapeType
        assert sorted(roofs.values()) == ["HIPPED_GABLE_ROOF", "HIP_ROOF"]
        assert len([x for x in f.by_type("IfcSlab") if x.PredefinedType == "ROOF"]) == 4 + 5
