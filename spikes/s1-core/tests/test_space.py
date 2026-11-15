# SPDX-License-Identifier: MPL-2.0
"""공간 종류(space): 커맨드·검사, 복도 판정, IFC 투영(집합 관계)·가져오기 왕복, 평면 문자(실명·면적), 내보내기 문자 층, 증분 == 전체 재생성."""
import sys

import ifcopenshell

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import drawing_export as DX
from dobim_core import fixture as F
from dobim_core.kinds import is_corridor

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
LSHAPE = [[0, 0], [4000, 0], [4000, 2000], [2000, 2000], [2000, 4000], [0, 4000]]


def mk_space(sess, boundary=RECT, level=F.LV1, **kw):
    r = sess.run("space.create", {"id": F.SP1, "level": level, "boundary": boundary, "name": "사무실", "category": "사무실", **kw})
    assert r["ok"], r
    return r["result"]["id"]


def mk(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def labels_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_labels, vid)


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


# ---------------------------------------------------------------- 커맨드·복도 판정
def test_공간_생성은_반시계로_저장하고_면적과_복도_여부를_준다(base):
    mk_space(base, boundary=RECT[::-1], category="복도", name="1층 홀")
    r = base.run("element.get", {"id": F.SP1})["result"]
    assert r["kind"] == "space" and r["ifc_class"] == "IfcSpace" and r["relations"]["on_level"] == [F.LV1]
    b = r["params"]["boundary"]
    assert sum(b[i][0] * b[(i + 1) % 4][1] - b[(i + 1) % 4][0] * b[i][1] for i in range(4)) > 0
    assert r["derived"] == {"area_m2": 24.0, "corridor": True}
    assert base.run("model.stats", {})["result"]["counts"]["space"] == 1


def test_복도_판정은_분류나_실명이_있을_때만이다():
    assert is_corridor("A", "복도") and is_corridor("A", "Corridor") and is_corridor("A", " hallway ")
    assert is_corridor("2층 복도", "") and is_corridor("Main Corridor", "")
    assert not is_corridor("SP-abc123", "") and not is_corridor("사무실", "사무실") and not is_corridor("", None)


def test_잘못된_공간은_거절한다(base):
    for bad in ({"boundary": [[0, 0], [1000, 0]]}, {"boundary": [[0, 0], [4000, 4000], [4000, 0], [0, 4000]]}, {"height": 0}, {"level": "9" * 22}):
        r = base.run("space.create", {"level": F.LV1, "boundary": RECT, **bad})
        assert not r["ok"], bad
    assert "space" not in base.run("model.stats", {})["result"]["counts"]


def test_공간_수정_이동_삭제와_실행_취소(base):
    mk_space(base)
    h0 = base.eng.state_hash()
    assert base.run("space.set", {"id": F.SP1, "category": "복도", "height": 3000})["ok"]
    assert base.run("element.get", {"id": F.SP1})["result"]["derived"]["corridor"] is True
    assert base.run("space.move", {"id": F.SP1, "dx": 500, "dy": 100})["ok"]
    assert not base.run("space.set", {"id": F.SP1})["ok"]
    assert base.run("history.undo", {})["ok"] and base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0
    assert not base.run("element.delete", {"id": F.LV1})["ok"]                              # 공간이 있는 레벨은 지울 수 없다
    assert base.run("space.delete", {"id": F.SP1})["ok"] and base.run("history.undo", {})["ok"]


# ---------------------------------------------------------------- IFC
def test_IFC_투영은_IfcSpace_압출과_층_집합_관계를_갖는다(base, tmp_path):
    mk_space(base, boundary=LSHAPE, height=2800, category="복도")
    out = tmp_path / "s.ifc"
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    sp = f.by_guid(F.SP1)
    assert sp.is_a("IfcSpace") and sp.PredefinedType == "INTERNAL" and sp.Name == "사무실" and sp.ObjectType == "복도"
    solid = sp.Representation.Representations[0].Items[0]
    assert solid.is_a("IfcExtrudedAreaSolid") and solid.Depth == 2800.0 and len(solid.SweptArea.OuterCurve.Points) == len(LSHAPE) + 1
    agg = [r for r in f.get_inverse(sp) if r.is_a("IfcRelAggregates")]
    assert agg and agg[0].RelatingObject.GlobalId == F.LV1
    assert not [r for r in f.get_inverse(sp) if r.is_a("IfcRelContainedInSpatialStructure")]


def test_공간을_내보내고_가져오면_저작_공간으로_되살아나고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("space.create", {"id": F.SP1, "level": F.LV2, "boundary": LSHAPE, "name": "복도", "category": "복도", "height": 2600})["ok"]
        h = b.hashes()
        b.export("s.ifc")
        r = b.import_ifc("s.ifc", "imp.dobim")
        assert r["ok"], r
        assert b.origins("imp.dobim") == {"authored": 8, "foreign": 0, "detached": 0}
        g = b.run("element.get", {"id": F.SP1}, project="imp.dobim")["result"]
        assert g["kind"] == "space" and g["origin"] == "authored" and g["params"]["category"] == "복도" and g["relations"]["on_level"] == [F.LV2]
        hi = b.hashes("imp.dobim")
        assert hi["state"] == h["state"] and hi["derived"] == h["derived"]
        assert b.run("space.set", {"id": F.SP1, "name": "중앙 복도"}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_외부에서_공간_형상을_바꾸면_분리된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": RECT})["ok"]
        b.export("s.ifc")
        f = ifcopenshell.open(str(tmp_path / "s.ifc"))
        f.by_guid(F.SP1).Representation.Representations[0].Items[0].Depth = 999.0
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
    finally:
        b.close()


# ---------------------------------------------------------------- 도면 문자 층
def test_평면은_실명과_면적_문자를_무게중심에_놓고_선은_만들지_않는다(base):
    p = mk(base, name="P", type="plan", level=F.LV1)
    lines0 = lines_of(base, p)
    lb0 = labels_of(base, p)                                                                # 기준 프로젝트의 방화문(FD) 문자만 있다
    assert all(x[2].startswith("FD") for x in lb0)
    mk_space(base)
    lb = labels_of(base, p)
    assert lb == ((3000.0, 2090.0, "사무실", 2.5, "middle"), (3000.0, 1800.0, "24.00㎡", 2.0, "middle")) + lb0   # 축척 1:100: 위 0.9mm·아래 2.0mm 용지 간격
    assert lines_of(base, p) == lines0                                                      # 선분은 그대로
    other = mk(base, name="P2", type="plan", level=F.LV2)
    assert labels_of(base, other) == ()                                                     # 다른 레벨
    sec = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
    assert labels_of(base, sec) == ()                                                       # 단면·입면은 그리지 않는다


def test_오목_공간의_면적과_무게중심(base):
    mk_space(base, boundary=LSHAPE)
    p = mk(base, name="P", type="plan", level=F.LV1)
    lb = labels_of(base, p)
    assert lb[1][2] == "12.00㎡" and lb[0][2] == "사무실"
    assert 1000.0 < lb[0][0] < 2000.0 and 1000.0 < lb[0][1] - 90.0 < 2000.0              # L 자의 무게중심은 정사각형 중심(2000,2000)에서 아래 왼쪽으로 쏠린다


def test_문자_층은_SVG와_PDF에_나오고_없으면_바이트가_같다():
    base_data = {"type": "plan", "name": "P", "scale": 100, "lines": (("cut", 0.0, 0.0, 5000.0, 0.0),), "fills": (), "annots": (), "datums": ()}
    with_labels = dict(base_data, labels=((2500.0, 500.0, "사무실", 2.5, "middle"), (2500.0, 200.0, "24.00㎡", 2.0, "middle")))
    svg = DX.render_svg(with_labels).decode("utf-8")
    assert '<g id="labels"' in svg and "사무실" in svg and "24.00㎡" in svg
    assert DX.render_svg(dict(base_data, labels=())) == DX.render_svg(base_data)               # 문자가 없으면 키가 있어도 바이트가 같다
    pdf, _ = DX.render_pdf(with_labels)
    assert pdf.startswith(b"%PDF") and len(pdf) > len(DX.render_pdf(base_data)[0])


def test_공간_증분이_전체_재생성과_같다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1), "p2": mk(base, name="P2", type="plan", level=F.LV2, scale=50),
             "s": mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])}

    def check():
        for k, v in views.items():
            ref = s3lib.RefGraph(s3lib._Inputs(base.eng))
            assert labels_of(base, v) == ref.q(D.view_labels, v), k
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k

    check()
    mk_space(base)
    check()
    assert base.run("space.set", {"id": F.SP1, "name": "회의실", "boundary": LSHAPE})["ok"]
    check()
    assert base.run("space.move", {"id": F.SP1, "dx": 300, "dy": 0})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("space.delete", {"id": F.SP1})["ok"]
    assert labels_of(base, views["p"]) == ()
    check()


def test_공간이_없으면_도면이_바뀌지_않는다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1), "e": mk(base, name="E", type="elevation", dir="S")}
    before = {k: (lines_of(base, v), labels_of(base, v)) for k, v in views.items()}
    mk_space(base)
    assert base.run("space.delete", {"id": F.SP1})["ok"]
    for k, v in views.items():
        assert (lines_of(base, v), labels_of(base, v)) == before[k], k
