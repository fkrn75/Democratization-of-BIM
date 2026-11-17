# SPDX-License-Identifier: MPL-2.0
"""18단계: 일반 솔리드(solid: 돌출·로프트·판·스윕·메시)와 모양 개구부(사각·반원 아치·뾰족 아치·원).
순수 형상(윤곽·다면체·결정성), 커맨드·검사·실행 취소, IFC(IfcFacetedBrep·범주 클래스·벽 개구부 부속) 투영·왕복, 도면(평면 절단·입면 윤곽·가림),
입력이 없는 프로젝트의 산출 불변, 증분 == 전체 재생성."""
import sys

import ifcopenshell
import pytest

import kit
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import solids as SO

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

PRISM = {"id": F.SO1, "level": F.LV1, "category": "buttress", "form": "prism", "polygon": [[0, -900], [600, -900], [600, -300], [0, -300]], "z0": 0, "z1": 2400}
SPIRE = {"level": F.LV2, "category": "spire", "form": "loft", "sections": [{"z": 0, "polygon": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]}, {"z": 3000, "polygon": [[500, 500]]}]}
GABLE = {"level": F.LV1, "category": "wall", "form": "panel", "line": [-1000, 0, -1000, 4000], "thickness": 400,
         "profile": [[0, 0], [4000, 0], [4000, 3000], [2000, 4500], [0, 3000]],
         "openings": [{"shape": "pointed", "offset": 2000, "sill": 800, "width": 900, "height": 2600, "rise": 900, "fill": "glazing"}]}
BELT = {"level": F.LV1, "category": "covering", "form": "sweep", "path": [[0, -150], [6000, -150]], "profile": [[0, 2700], [120, 2700], [120, 2850], [0, 2850]]}
OPS = [{"shape": "pointed", "offset": 4500, "sill": 900, "width": 900, "height": 1900, "fill": "glazing"},
       {"shape": "circle", "offset": 3000, "sill": 2000, "width": 800, "height": 800}]


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


def mk_view(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def closed_ok(verts, faces):
    """모든 모서리가 반대 방향으로 정확히 두 번 쓰인다(닫힌·방향 일관 다면체)."""
    seen = {}
    for f in faces:
        for lp in f:
            for i in range(len(lp)):
                e = (lp[i], lp[(i + 1) % len(lp)])
                seen[e] = seen.get(e, 0) + 1
    return all(c == 1 and seen.get((b, a)) == 1 for (a, b), c in seen.items())


def volume(verts, faces):
    """발산 정리 부피(면을 부채꼴 삼각형으로, 구멍은 바깥 고리와 반대 방향이라 빼진다)."""
    v = 0.0
    for f in faces:
        for lp in f:
            a = verts[lp[0]]
            for i in range(1, len(lp) - 1):
                b, c = verts[lp[i]], verts[lp[i + 1]]
                v += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    return v


# ---------------------------------------------------------------- 순수 형상
def test_개구부_윤곽은_반시계이고_모양대로_꼭대기와_사각_부분이_맞다():
    for shape, w, h, rise in (("rect", 900, 2100, None), ("round", 1000, 2400, None), ("pointed", 900, 3000, None), ("pointed", 1200, 3500, 1200), ("circle", 1200, 1200, None)):
        pts, sp = SO.opening_profile(shape, w, h, rise)
        assert SO.area2(pts) > 0 and SO.poly_problem(pts) is None, shape
        assert max(z for _, z in pts) == pytest.approx(h) and min(z for _, z in pts) == pytest.approx(0.0)
        assert max(abs(u) for u, _ in pts) == pytest.approx(w / 2)
    assert SO.opening_profile("round", 1000, 2400)[1] == pytest.approx(1900.0)
    assert SO.opening_profile("pointed", 1200, 3500, 1200)[1] == pytest.approx(2300.0)
    assert SO.opening_profile("pointed", 900, 3000)[0] == SO.opening_profile("pointed", 900, 3000)[0]   # 결정적
    pts, _ = SO.opening_profile("circle", 1000, 1000)
    assert all((u * u + (z - 500) ** 2) ** 0.5 == pytest.approx(500, abs=1e-6) for u, z in pts)   # 모든 꼭짓점이 원 위


@pytest.mark.parametrize("p", [
    {"form": "prism", "polygon": [[0, 0], [1000, 0], [1000, 500], [400, 500], [400, 1500], [0, 1500]], "z0": 0, "z1": 3000},   # 오목
    {"form": "loft", "sections": [{"z": 0, "polygon": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]}, {"z": 2000, "polygon": [[200, 200], [800, 200], [800, 800], [200, 800]]}, {"z": 5000, "polygon": [[500, 500]]}]},
    {"form": "loft", "sections": [{"z": 0, "polygon": [[0, 0]]}, {"z": 1000, "polygon": [[-500, -500], [500, -500], [500, 500], [-500, 500]]}]},
    {"form": "panel", "line": [0, 0, 3000, 3000], "thickness": 300, "profile": [[0, 0], [4000, 0], [4000, 3000], [2000, 4500], [0, 3000]],
     "openings": [{"shape": "round", "offset": 1200, "sill": 500, "width": 800, "height": 2000}, {"shape": "circle", "offset": 3000, "sill": 1500, "width": 900, "height": 900}]},
    {"form": "sweep", "path": [[0, 0], [5000, 0], [5000, 4000]], "profile": [[0, 0], [200, 0], [200, 300], [0, 300]]},
    {"form": "sweep", "path": [[0, 0], [4000, 0], [4000, 4000], [0, 4000], [0, 0]], "profile": [[-100, 0], [100, 0], [100, 200], [-100, 200]]},
])
def test_다면체는_닫혀_있고_바깥을_향하며_부피가_양수다(p):
    full = {"name": "x", "category": "proxy", **p}
    assert SO.solid_problem(full) is None, SO.solid_problem(full)
    verts, faces = SO.solid_mesh(full)
    assert closed_ok(verts, faces)
    assert volume(verts, faces) > 0


def test_돌출_판_스윕_부피가_해석값과_같다():
    v, f = SO.solid_mesh({"form": "prism", "polygon": [[0, 0], [1000, 0], [1000, 2000], [0, 2000]], "z0": 0, "z1": 3000})
    assert volume(v, f) == pytest.approx(1000 * 2000 * 3000)
    p = {"form": "panel", "line": [0, 0, 0, 4000], "thickness": 500, "profile": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
         "openings": [{"shape": "rect", "offset": 2000, "sill": 1000, "width": 1000, "height": 1000}]}
    v, f = SO.solid_mesh(p)
    assert volume(v, f) == pytest.approx((4000 * 3000 - 1000 * 1000) * 500)
    v, f = SO.solid_mesh({"form": "sweep", "path": [[0, 0], [3000, 0]], "profile": [[0, 0], [100, 0], [100, 200], [0, 200]]})
    assert volume(v, f) == pytest.approx(3000 * 100 * 200)


def test_특징_모서리는_같은_평면을_나눈_대각선을_빼고_절단선은_닫힌_윤곽이다():
    v, f = SO.solid_mesh({"form": "prism", "polygon": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]], "z0": 0, "z1": 1000})
    edges, nrm = SO.feature_edges(v, f)
    assert len(edges) == 12
    segs = SO.plane_section(v, f, nrm, (0.0, 0.0, 1.0), 500.0)
    assert len(segs) == 4 and sum(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in segs) == pytest.approx(4000)


@pytest.mark.parametrize("bad,frag", [
    ({"form": "prism", "polygon": [[0, 0], [1000, 1000], [1000, 0], [0, 400]], "z0": 0, "z1": 100}, "교차"),
    ({"form": "prism", "polygon": [[0, 0], [1000, 0], [1000, 1000]], "z0": 100, "z1": 100}, "z1"),
    ({"form": "loft", "sections": [{"z": 0, "polygon": [[0, 0], [1, 0], [1, 1]]}, {"z": 100, "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}]}, "꼭짓점 수"),
    ({"form": "loft", "sections": [{"z": 0, "polygon": [[0, 0]]}, {"z": 100, "polygon": [[1, 1]]}]}, "다각형 단면"),
    ({"form": "panel", "line": [0, 0, 3000, 0], "thickness": 300, "profile": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]],
      "openings": [{"shape": "rect", "offset": 2900, "sill": 0, "width": 900, "height": 1000}]}, "밖"),
    ({"form": "panel", "line": [0, 0, 3000, 0], "thickness": 300, "profile": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]],
      "openings": [{"shape": "rect", "offset": 1000, "sill": 0, "width": 900, "height": 1000}, {"shape": "circle", "offset": 1200, "sill": 500, "width": 600, "height": 600}]}, "겹친다"),
    ({"form": "panel", "line": [0, 0, 3000, 0], "thickness": 300, "profile": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]],
      "openings": [{"shape": "circle", "offset": 1000, "sill": 0, "width": 900, "height": 1000}]}, "원"),
    ({"form": "mesh", "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], "faces": [[0, 1, 2], [0, 1, 3], [1, 2, 3], [0, 2, 9]]}, "면"),
    ({"form": "sweep", "path": [[0, 0], [1000, 0], [0, 10]], "profile": [[0, 0], [10, 0], [10, 10]]}, "꺾인다"),
    ({"form": "prism", "polygon": [[0, 0], [1000, 0], [1000, 1000]], "z0": 0, "z1": 100, "openings": []}, "이름"),
])
def test_잘못된_솔리드는_이유와_함께_거절한다(bad, frag):
    why = SO.solid_problem({"name": "x", "category": "proxy", **bad})
    assert why and frag in why, why


def test_솔리드_생성_수정_이동_삭제와_실행_취소(base):
    h0 = base.eng.state_hash()
    assert base.run("solid.create", PRISM)["ok"]
    g = base.run("element.get", {"id": F.SO1})["result"]
    assert g["kind"] == "solid" and g["ifc_class"] == "IfcColumn" and g["params"]["form"] == "prism"
    assert base.run("solid.set", {"id": F.SO1, "z1": 3000, "name": "부벽 1"})["ok"]
    p = base.run("element.get", {"id": F.SO1})["result"]["params"]
    assert p["z1"] == 3000.0 and p["name"] == "부벽 1" and p["polygon"][0] == [0.0, -900.0]
    assert base.run("solid.set", {"id": F.SO1, "category": "column"})["ok"]   # 같은 IFC 클래스(IfcColumn) 범주로는 바꿀 수 있다
    assert not base.run("solid.set", {"id": F.SO1, "category": "wall"})["ok"]   # IFC 클래스가 바뀌는 범주 변경은 거절
    assert base.run("solid.set", {"id": F.SO1, "form": "loft", "sections": SPIRE["sections"]})["ok"]   # 형태를 바꾸면 이전 형태 키는 버린다
    p = base.run("element.get", {"id": F.SO1})["result"]["params"]
    assert p["form"] == "loft" and "polygon" not in p and "z0" not in p
    assert base.run("solid.move", {"id": F.SO1, "dx": 100, "dy": 50, "dz": 10})["ok"]
    p = base.run("element.get", {"id": F.SO1})["result"]["params"]
    assert p["sections"][0]["z"] == 10.0 and p["sections"][1]["polygon"] == [[600.0, 550.0]]
    assert base.run("model.stats", {})["result"]["counts"]["solid"] == 1
    assert base.run("solid.delete", {"id": F.SO1})["ok"]
    for _ in range(6):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_잘못된_솔리드_명령은_거절한다(base):
    assert not base.run("solid.create", {**PRISM, "form": None})["ok"]
    assert not base.run("solid.create", {k: v for k, v in PRISM.items() if k != "z1"})["ok"]
    assert not base.run("solid.create", {**PRISM, "sections": SPIRE["sections"]})["ok"]   # 다른 형태의 키
    assert not base.run("solid.create", {**PRISM, "category": "nope"})["ok"]
    assert not base.run("solid.create", {**PRISM, "level": F.W1})["ok"]
    assert base.run("solid.create", PRISM)["ok"]
    assert not base.run("solid.create", PRISM)["ok"]   # 같은 id
    assert not base.run("solid.set", {"id": F.SO1, "z1": -5})["ok"]


def test_벽_모양_개구부_명령과_분할과_순면적(base):
    W = "1" + "0" * 20 + "9"
    assert base.run("wall.create", {"id": W, "level": F.LV1, "axis": [0, -3000, 6000, -3000], "thickness": 600, "height": 3000, "openings": OPS})["ok"]
    p = base.run("element.get", {"id": W})["result"]["params"]
    assert [o["shape"] for o in p["openings"]] == ["pointed", "circle"] and "rise" not in p["openings"][0] and "fill" not in p["openings"][1]
    res = base.run("schedule.rows", {"kind": "wall"})["result"]
    rows = {r[0]: dict(zip(res["columns"], r)) for r in res["items"]}
    loop_area = sum(abs(SO.area2(SO.opening_loop(o))) / 2 for o in p["openings"])
    assert rows[W]["net_area"] == pytest.approx(6000 * 3000 - loop_area)
    assert not base.run("wall.set", {"id": W, "height": 2500})["ok"]   # 개구부가 벽 위로 나간다
    assert not base.run("wall.create", {"level": F.LV1, "axis": [0, 0, 1000, 0], "openings": [OPS[0]]})["ok"]   # 벽 밖
    assert not base.run("wall.split", {"id": W, "at": 4500})["ok"]   # 개구부를 가로지른다
    assert base.run("wall.split", {"id": W, "at": 3700, "new_id": "1" + "0" * 20 + "8"})["ok"]
    a = base.run("element.get", {"id": W})["result"]["params"]
    b = base.run("element.get", {"id": "1" + "0" * 20 + "8"})["result"]["params"]
    assert [o["offset"] for o in a["openings"]] == [3000.0] and [o["offset"] for o in b["openings"]] == [800.0]
    assert base.run("wall.set", {"id": W, "openings": []})["ok"]
    assert "openings" not in base.run("element.get", {"id": W})["result"]["params"]


# ---------------------------------------------------------------- IFC
def test_IFC는_범주_클래스와_FacetedBrep와_벽_개구부를_내보낸다(base, tmp_path):
    assert base.run("solid.create", PRISM)["ok"]
    assert base.run("solid.create", {**SPIRE, "id": "f" + "0" * 20 + "2"})["ok"]
    assert base.run("solid.create", {**GABLE, "id": "f" + "0" * 20 + "3"})["ok"]
    assert base.run("solid.create", {**BELT, "id": "f" + "0" * 20 + "4"})["ok"]
    assert base.run("wall.set", {"id": F.W3, "openings": [{**OPS[0], "offset": 1500}]})["ok"]
    base.eng.flush_ifc()
    out = tmp_path / "s.ifc"
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    b = f.by_guid(F.SO1)
    assert b.is_a("IfcColumn") and b.PredefinedType == "USERDEFINED" and b.ObjectType == "BUTTRESS"
    rep = b.Representation.Representations[0]
    assert rep.RepresentationType == "Brep" and rep.Items[0].is_a("IfcFacetedBrep")
    sp = f.by_guid("f" + "0" * 20 + "2")
    assert sp.is_a("IfcRoof") and sp.ObjectType == "SPIRE"
    assert f.by_guid("f" + "0" * 20 + "3").is_a("IfcWall") and f.by_guid("f" + "0" * 20 + "4").is_a("IfcCovering")
    w = f.by_guid(F.W3)
    ops = [r.RelatedOpeningElement for r in w.HasOpenings if r.RelatedOpeningElement.Name.endswith("-opening1")]
    assert len(ops) == 1 and ops[0].Representation.Representations[0].Items[0].is_a("IfcExtrudedAreaSolid")
    assert base.run("model.validate", {})["ok"]
    assert base.run("wall.set", {"id": F.W3, "openings": []})["ok"]
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    f2 = ifcopenshell.open(str(out))   # 파일 객체를 붙잡아 둔다(엔티티보다 먼저 해제되면 접근 위반)
    w = f2.by_guid(F.W3)
    assert not [r for r in w.HasOpenings if r.RelatedOpeningElement.Name.endswith("-opening1")]   # 개구부를 떼면 부속도 사라진다


def test_솔리드와_벽_개구부를_내보내고_가져오면_저작으로_되살아나고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("solid.create", PRISM)["ok"]
        assert b.run("solid.create", {**SPIRE, "id": "f" + "0" * 20 + "2"})["ok"]
        assert b.run("solid.create", {**GABLE, "id": "f" + "0" * 20 + "3"})["ok"]
        assert b.run("solid.create", {**BELT, "id": "f" + "0" * 20 + "4"})["ok"]
        assert b.run("solid.create", {"id": "f" + "0" * 20 + "5", "level": F.LV1, "category": "terrain", "form": "mesh",
                                      "vertices": [[-3000, -3000, -300], [9000, -3000, -300], [9000, 7000, -800], [-3000, 7000, -800], [-3000, -3000, -1500], [9000, -3000, -1500], [9000, 7000, -1500], [-3000, 7000, -1500]],
                                      "faces": [[0, 1, 2, 3], [4, 7, 6, 5], [0, 4, 5, 1], [1, 5, 6, 2], [2, 6, 7, 3], [3, 7, 4, 0]]})["ok"]
        assert b.run("wall.set", {"id": F.W3, "openings": [{**OPS[0], "offset": 1500}]})["ok"]
        h = b.hashes()
        b.export("r.ifc")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        o = b.origins("imp.dobim")
        assert o["foreign"] == 0 and o["detached"] == 0, o   # 벽 개구부 부속이 외래 요소로 새지 않는다
        g = b.run("element.get", {"id": "f" + "0" * 20 + "3"}, project="imp.dobim")["result"]
        assert g["origin"] == "authored" and g["kind"] == "solid" and g["params"]["openings"][0]["rise"] == 900.0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("solid.move", {"id": F.SO1, "dx": 10}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_솔리드_형상을_외부에서_바꾸면_분리되고_클래스가_범주와_다르면_외래다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("solid.create", PRISM)["ok"]
        b.export("r.ifc")
        f = ifcopenshell.open(str(tmp_path / "r.ifc"))
        pt = f.by_guid(F.SO1).Representation.Representations[0].Items[0].Outer.CfsFaces[0].Bounds[0].Bound.Polygon[0]
        pt.Coordinates = (pt.Coordinates[0] + 50.0, pt.Coordinates[1], pt.Coordinates[2])
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
    finally:
        b.close()


# ---------------------------------------------------------------- 도면
def test_평면은_솔리드를_절단선으로_벽_개구부를_틈과_유리선으로_그린다(base):
    assert base.run("solid.create", PRISM)["ok"]
    assert base.run("wall.set", {"id": F.W3, "openings": [{"shape": "rect", "offset": 1500, "sill": 900, "width": 900, "height": 1200, "fill": "glazing"}]})["ok"]
    v = mk_view(base, name="1F", type="plan", level=F.LV1, cut_height=1200)
    ln = lines_of(base, v)
    cut = [x for x in ln if x[0] == "cut"]
    assert any(x[1:] == (0.0, -900.0, 600.0, -900.0) or x[1:] == (600.0, -900.0, 0.0, -900.0) for x in cut)   # 부벽 절단선
    assert any(x[0] == "proj" and x[2] == x[4] == 4000.0 and {x[1], x[3]} == {6000.0 - 1050.0, 6000.0 - 1950.0} for x in ln)   # 유리선(W3 는 6000 -> 0 방향)


def test_입면은_아치_윤곽과_첨탑을_그리고_부벽이_벽_선을_가린다(base):
    W = "1" + "0" * 20 + "9"
    assert base.run("wall.create", {"id": W, "level": F.LV1, "axis": [0, -3000, 6000, -3000], "thickness": 600, "height": 3000, "openings": OPS})["ok"]
    assert base.run("solid.create", {**SPIRE, "id": "f" + "0" * 20 + "2"})["ok"]
    v = mk_view(base, name="S", type="elevation", dir="S")
    ln = lines_of(base, v)
    slanted = [x for x in ln if x[0] == "proj" and x[1] != x[3] and x[2] != x[4]]
    assert any(abs(x[2] - 2800.0) < 1 or abs(x[4] - 2800.0) < 1 for x in slanted)   # 뾰족 아치 꼭대기(sill 900 + 1900)
    assert any(abs(max(x[2], x[4]) - 6300.0) < 1 for x in slanted)                  # 첨탑 정점(2F 3300 + 3000)
    # 앞에 부벽을 세우면 그 뒤 벽 모서리(x=0 수직선)의 아래쪽이 가려진다
    before = [x for x in lines_of(base, v) if x[0] == "proj" and x[1] == x[3] == 0.0]
    assert base.run("solid.create", {**PRISM, "polygon": [[-300, -4500], [300, -4500], [300, -3400], [-300, -3400]], "z0": 0, "z1": 1500})["ok"]
    after = [x for x in lines_of(base, v) if x[0] == "proj" and x[1] == x[3] == 0.0]
    assert sum(abs(x[4] - x[2]) for x in after) < sum(abs(x[4] - x[2]) for x in before) - 1000


def test_솔리드와_개구부가_없는_프로젝트의_도면은_바뀌지_않는다(base):
    v1 = mk_view(base, name="1F", type="plan", level=F.LV1, cut_height=1200)
    v2 = mk_view(base, name="S", type="elevation", dir="S")
    v3 = mk_view(base, name="X", type="section", p1=[-1000, 2000], p2=[7000, 2000])
    before = [lines_of(base, v) for v in (v1, v2, v3)]
    W = "1" + "0" * 20 + "9"
    assert base.run("wall.create", {"id": W, "level": F.LV2, "axis": [20000, 0, 26000, 0], "thickness": 600, "height": 3000, "openings": OPS})["ok"]
    assert base.run("history.undo", {})["ok"]
    assert [lines_of(base, v) for v in (v1, v2, v3)] == before
    assert all(not D.wall_shape_openings(base.eng.g, w) for w in (F.W1, F.W2, F.W3))


def test_솔리드_도면_증분이_전체_재생성과_같다(base):
    v1 = mk_view(base, name="1F", type="plan", level=F.LV1, cut_height=1200)
    v2 = mk_view(base, name="S", type="elevation", dir="S")
    v3 = mk_view(base, name="X", type="section", p1=[-2000, 2000], p2=[7000, 2000])
    for v in (v1, v2, v3):
        lines_of(base, v)
    assert base.run("solid.create", PRISM)["ok"]
    assert base.run("solid.create", {**GABLE, "id": "f" + "0" * 20 + "3"})["ok"]
    assert base.run("solid.create", {**BELT, "id": "f" + "0" * 20 + "4"})["ok"]
    assert base.run("wall.set", {"id": F.W1, "openings": [{"shape": "round", "offset": 4500, "sill": 0, "width": 900, "height": 2400}]})["ok"]
    assert base.run("solid.move", {"id": F.SO1, "dx": 200})["ok"]
    for v in (v1, v2, v3):
        inc = lines_of(base, v)
        full = s3lib.RefGraph(s3lib._Inputs(base.eng)).q(D.view_lines, v)
        assert inc == full
    sec = lines_of(base, v3)
    assert any(x[0] == "cut" for x in sec)


def test_솔리드_도면과_해시는_결정적이다(tmp_path):
    outs = []
    for name in ("a", "b"):
        s = kit.Session(tmp_path / f"{name}.dobim", guid=F.GUID)
        try:
            for c, a in F.SCRIPT:
                assert s.run(c, a)["ok"]
            assert s.run("solid.create", PRISM)["ok"]
            assert s.run("solid.create", {**GABLE, "id": "f" + "0" * 20 + "3"})["ok"]
            v = mk_view(s, name="S", type="elevation", dir="S", id="4" + "0" * 20 + "7")
            outs.append((lines_of(s, v), s.eng.state_hash()))
        finally:
            s.close()
    assert outs[0] == outs[1]


# ---------------------------------------------------------------- 재료 겉모습(18단계)
def test_재료_이름이_겉모습_표에_맞으면_IFC_재료에_표면_스타일_색이_붙고_취소하면_사라진다(base, tmp_path):
    from dobim_core import appearance as AP
    assert AP.material_rgb("적벽돌") == (168, 86, 64) and AP.material_rgb("동판(녹청)") == (96, 156, 136) and AP.material_rgb("Concrete") is None
    h0 = base.eng.state_hash()
    base.eng.flush_ifc()
    out = tmp_path / "m.ifc"
    base.eng.ifc.write(str(out))
    n0 = len(ifcopenshell.open(str(out)).by_type("IfcSurfaceStyle"))
    assert base.run("material.set", {"id": F.W2, "name": "적벽돌"})["ok"]
    assert base.run("solid.create", PRISM)["ok"]
    assert base.run("material.set", {"id": F.SO1, "name": "적벽돌"})["ok"]   # 같은 재료는 스타일도 하나
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    mats = [m for m in f.by_type("IfcMaterial") if m.Name == "적벽돌"]
    assert len(mats) == 1 and mats[0].HasRepresentation
    shade = [x for x in f.by_type("IfcSurfaceStyleShading")]
    assert len(f.by_type("IfcSurfaceStyle")) == n0 + 1 and any(round(x.SurfaceColour.Red * 255) == 168 for x in shade)
    for _ in range(3):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    f2 = ifcopenshell.open(str(out))
    assert len(f2.by_type("IfcSurfaceStyle")) == n0 and not [m for m in f2.by_type("IfcMaterial") if m.Name == "적벽돌"]
    assert not f2.by_type("IfcSurfaceStyleShading") and not f2.by_type("IfcColourRgb") and not f2.by_type("IfcMaterialDefinitionRepresentation")   # 고아 엔티티가 남지 않는다

