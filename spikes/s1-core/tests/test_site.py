# SPDX-License-Identifier: MPL-2.0
"""대지 종류(site): 커맨드·검사, IFC 투영(뼈대 대지 아래 하위 IfcSite)·가져오기 왕복, 병합 검사, 대지가 없으면 모델·IFC 바이트 불변."""
import sys

import ifcopenshell

from conftest import ROOT
from dobim_core import fixture as F
from dobim_core import kinds as K

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import scenarios as S  # noqa: E402

LOT = [[-6000, -6000], [18000, -6000], [18000, 14000], [-6000, 14000]]
ROADS = [{"name": "남측 도로", "edge": 0, "width": 8000}, {"name": "동측 도로", "edge": 1, "width": 6000}]


def mk_site(sess, boundary=LOT, **kw):
    r = sess.run("site.create", {"id": F.SI1, "boundary": boundary, "name": "대지", **kw})
    assert r["ok"], r
    return r["result"]["id"]


# ---------------------------------------------------------------- 커맨드·검사
def test_대지_생성은_반시계로_저장하고_면적을_준다(base):
    mk_site(base, boundary=LOT[::-1], north_angle=30, elevation=1500)
    r = base.run("element.get", {"id": F.SI1})["result"]
    assert r["kind"] == "site" and r["ifc_class"] == "IfcSite"
    b = r["params"]["boundary"]
    assert sum(b[i][0] * b[(i + 1) % 4][1] - b[(i + 1) % 4][0] * b[i][1] for i in range(4)) > 0
    assert r["derived"]["area_m2"] == 24.0 * 20.0 and r["params"]["north_angle"] == 30 and r["params"]["elevation"] == 1500
    assert base.run("model.stats", {})["result"]["counts"]["site"] == 1


def test_시계_방향으로_준_경계는_도로_변_번호를_입력_순서대로_옮긴다(base):
    # 입력 경계(시계): 변 0 = (-6000,14000)->(18000,14000) 은 북쪽 변이다
    cw = [[-6000, 14000], [18000, 14000], [18000, -6000], [-6000, -6000]]
    mk_site(base, boundary=cw, roads=[{"name": "북측", "edge": 0, "width": 5000}])
    p = base.run("element.get", {"id": F.SI1})["result"]["params"]
    b, e = p["boundary"], p["roads"][0]["edge"]
    a, c = b[e], b[(e + 1) % 4]
    assert a[1] == 14000 and c[1] == 14000        # 저장한 경계에서도 그 도로는 북쪽 변에 붙어 있다


def test_잘못된_대지는_거절한다(base):
    for bad in ({"boundary": [[0, 0], [1000, 0]]}, {"boundary": [[0, 0], [4000, 4000], [4000, 0], [0, 4000]]},
                {"roads": [{"name": "a", "edge": 4, "width": 1000}]}, {"roads": [{"name": "a", "edge": 0, "width": 0}]},
                {"roads": [{"name": "a", "edge": 0, "width": 1000}, {"name": "b", "edge": 0, "width": 2000}]}, {"north_angle": 400}):
        r = base.run("site.create", {"boundary": LOT, **bad})
        assert not r["ok"], bad
    assert "site" not in base.run("model.stats", {})["result"]["counts"]


def test_대지_수정_삭제와_실행_취소(base):
    mk_site(base, roads=ROADS, elevation=900)
    h0 = base.eng.state_hash()
    assert base.run("site.set", {"id": F.SI1, "north_angle": -15, "roads": [{"name": "북측", "edge": 3, "width": 4000}]})["ok"]
    assert base.run("site.set", {"id": F.SI1, "clear_elevation": True})["ok"]
    assert base.run("element.get", {"id": F.SI1})["result"]["params"]["elevation"] is None
    assert not base.run("site.set", {"id": F.SI1})["ok"]
    assert not base.run("site.set", {"id": F.SI1, "elevation": 1, "clear_elevation": True})["ok"]
    assert base.run("site.set", {"id": F.SI1, "boundary": [[0, 0], [5000, 0], [5000, 5000]]})["ok"] is False   # 도로 변 번호가 새 경계를 넘는다
    assert base.run("history.undo", {})["ok"] and base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0
    assert base.run("site.delete", {"id": F.SI1})["ok"] and base.run("history.undo", {})["ok"]


def test_종류_검사는_도로_변_번호와_표고를_본다():
    ok = {"name": "대지", "boundary": [list(map(float, q)) for q in LOT], "north_angle": 0.0, "roads": [], "elevation": None}
    assert K.row_params_error("site", ok) is None
    assert K.row_params_error("site", dict(ok, roads=[{"name": "r", "edge": 9, "width": 1.0}])) is not None
    assert K.row_params_error("site", dict(ok, elevation="x")) is not None


# ---------------------------------------------------------------- IFC
def test_IFC_투영은_뼈대_대지_아래_하위_IfcSite와_경계_폴리라인을_갖는다(base, tmp_path):
    mk_site(base, roads=ROADS, elevation=1200)
    out = tmp_path / "s.ifc"
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    st = f.by_guid(F.SI1)
    assert st.is_a("IfcSite") and st.Name == "대지" and st.RefElevation == 1200.0
    rep = st.Representation.Representations[0]
    assert rep.RepresentationType == "GeometricCurveSet" and len(rep.Items[0].Elements[0].Points) == len(LOT) + 1
    agg = [r for r in f.get_inverse(st) if r.is_a("IfcRelAggregates") and st in r.RelatedObjects]
    assert agg and agg[0].RelatingObject.Name == "Site" and agg[0].RelatingObject.GlobalId != F.SI1
    assert len(f.by_type("IfcSite")) == 2


def test_대지를_내보내고_가져오면_저작_대지로_되살아나고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("site.create", {"id": F.SI1, "boundary": LOT, "name": "대지", "north_angle": 20, "roads": ROADS, "elevation": 500})["ok"]
        h = b.hashes()
        b.export("s.ifc")
        r = b.import_ifc("s.ifc", "imp.dobim")
        assert r["ok"], r
        assert r["result"]["skeleton"] is True
        assert b.origins("imp.dobim") == {"authored": 8, "foreign": 0, "detached": 0}
        g = b.run("element.get", {"id": F.SI1}, project="imp.dobim")["result"]
        assert g["kind"] == "site" and g["origin"] == "authored" and g["params"]["roads"] == [dict(r_, width=float(r_["width"])) for r_ in ROADS]
        assert g["params"]["north_angle"] == 20 and g["params"]["elevation"] == 500
        hi = b.hashes("imp.dobim")
        assert hi["state"] == h["state"] and hi["derived"] == h["derived"]
        assert b.run("site.set", {"id": F.SI1, "name": "본 대지"}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_외부에서_대지_경계를_바꾸면_분리된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("site.create", {"id": F.SI1, "boundary": LOT})["ok"]
        b.export("s.ifc")
        f = ifcopenshell.open(str(tmp_path / "s.ifc"))
        f.by_guid(F.SI1).Representation.Representations[0].Items[0].Elements[0].Points[0].Coordinates = (-9999.0, -6000.0)
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
    finally:
        b.close()


def test_대지가_없으면_IFC와_해시가_그대로다(base, tmp_path):
    base.eng.flush_ifc()
    base.eng.ifc.write(str(tmp_path / "a.ifc"))
    h0 = base.eng.state_hash()
    mk_site(base)
    assert base.run("site.delete", {"id": F.SI1})["ok"]
    base.eng.flush_ifc()
    base.eng.ifc.write(str(tmp_path / "b.ifc"))
    assert base.eng.state_hash() == h0
    assert len(ifcopenshell.open(str(tmp_path / "b.ifc")).by_type("IfcSite")) == 1
