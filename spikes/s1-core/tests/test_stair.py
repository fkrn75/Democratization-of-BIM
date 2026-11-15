# SPDX-License-Identifier: MPL-2.0
"""계단 종류(stair): 커맨드·검사, IFC 투영·가져오기 왕복, 도면(평면 기호·절단선, 단면 절단 채움, 입면 윤곽), 증분 == 전체 재생성, 결정성."""
import sys

import ifcopenshell

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

RUN = [1000, 1000, 5000, 1000]   # +x 방향 4000mm, 폭 1200(y 400..1600), 오름 3000, 챌판 18 -> 디딤판 17, 깊이 4000/17, 챌판 높이 3000/18


def mk_stair(sess, **kw):
    args = {"id": F.ST1, "level": F.LV1, "run": RUN, "width": 1200, "height": 3000, "risers": 18, **kw}
    r = sess.run("stair.create", args)
    assert r["ok"], r
    return r["result"]["id"]


def mk(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


def fills_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_fills, vid)


# ---------------------------------------------------------------- 커맨드·검사
def test_계단_생성은_레벨에_걸리고_파생값을_준다(base):
    mk_stair(base)
    r = base.run("element.get", {"id": F.ST1})["result"]
    assert r["kind"] == "stair" and r["ifc_class"] == "IfcStair" and r["relations"]["on_level"] == [F.LV1]
    assert r["params"]["risers"] == 18 and r["derived"]["bottom"] == 0.0 and r["derived"]["top"] == 3000.0
    assert abs(r["derived"]["tread_depth"] - 4000 / 17) < 1e-9 and abs(r["derived"]["riser_height"] - 3000 / 18) < 1e-9
    assert base.run("model.stats", {})["result"]["counts"]["stair"] == 1


def test_잘못된_계단은_거절한다(base):
    for bad in ({"run": [0, 0, 0.2, 0]}, {"risers": 1}, {"risers": 101}, {"width": 0}, {"height": -1}, {"level": "9" * 22}):
        r = base.run("stair.create", {"level": F.LV1, "run": RUN, **bad})
        assert not r["ok"], bad
    assert "stair" not in base.run("model.stats", {})["result"]["counts"]


def test_계단_수정_이동_삭제와_실행_취소(base):
    mk_stair(base)
    h0 = base.eng.state_hash()
    assert base.run("stair.set", {"id": F.ST1, "risers": 17, "height": 2890})["ok"]
    assert base.run("element.get", {"id": F.ST1})["result"]["derived"]["top"] == 2890.0
    assert base.run("stair.move", {"id": F.ST1, "dx": 100.0, "dy": -50.0})["ok"]
    assert base.run("element.get", {"id": F.ST1})["result"]["params"]["run"] == [1100.0, 950.0, 5100.0, 950.0]
    assert not base.run("stair.set", {"id": F.ST1})["ok"]                                 # 바꿀 필드 없음
    assert base.run("history.undo", {})["ok"] and base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0
    assert base.run("stair.delete", {"id": F.ST1})["ok"] and base.run("history.undo", {})["ok"]
    assert base.run("element.delete", {"id": F.LV1})["ok"] is False                       # 계단이 있는 레벨은 지울 수 없다


# ---------------------------------------------------------------- IFC 투영·왕복
def test_IFC_투영은_IfcStair_톱니_옆모습_압출을_갖는다(base, tmp_path):
    mk_stair(base)
    out = tmp_path / "s.ifc"
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    st = f.by_guid(F.ST1)
    assert st.is_a("IfcStair") and st.PredefinedType == "STRAIGHT_RUN_STAIR"
    solid = st.Representation.Representations[0].Items[0]
    assert solid.is_a("IfcExtrudedAreaSolid") and solid.Depth == 1200.0
    pts = solid.SweptArea.OuterCurve.Points
    assert len(pts) == 1 + 18 + 17 + 1 + 1                                                       # 시작·챌판 위 꼭짓점 18·디딤 끝 17·아래 끝·닫힘
    assert max(p.Coordinates[1] for p in pts) == 3000.0
    assert tuple(solid.Position.Axis.DirectionRatios) == (0.0, -1.0, 0.0)                    # 걸음선 오른쪽 법선
    assert tuple(solid.Position.Location.Coordinates) == (1000.0, 1600.0, 0.0)               # 걸음선 시작에서 왼쪽으로 폭의 절반
    cont = [r for r in f.get_inverse(st) if r.is_a("IfcRelContainedInSpatialStructure")]
    assert cont and cont[0].RelatingStructure.GlobalId == F.LV1


def test_계단을_내보내고_가져오면_저작_계단으로_되살아나고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": [0, 0, 3000, 1000], "width": 900, "height": 2900, "risers": 16, "base_offset": 20})["ok"]
        h = b.hashes()
        b.export("s.ifc")
        r = b.import_ifc("s.ifc", "imp.dobim")
        assert r["ok"], r
        assert b.origins("imp.dobim") == {"authored": 8, "foreign": 0, "detached": 0}
        g = b.run("element.get", {"id": F.ST1}, project="imp.dobim")["result"]
        assert g["kind"] == "stair" and g["origin"] == "authored" and g["params"]["risers"] == 16
        assert g["params"]["run"] == [0.0, 0.0, 3000.0, 1000.0] and g["params"]["base_offset"] == 20.0
        hi = b.hashes("imp.dobim")
        assert hi["state"] == h["state"] and hi["derived"] == h["derived"]
        assert b.run("stair.set", {"id": F.ST1, "width": 1000}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_외부에서_계단_형상을_바꾸면_분리된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": RUN})["ok"]
        b.export("s.ifc")
        f = ifcopenshell.open(str(tmp_path / "s.ifc"))
        f.by_guid(F.ST1).Representation.Representations[0].Items[0].Depth = 999.0
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
        assert not b.run("stair.set", {"id": F.ST1, "width": 1000}, project="imp.dobim")["ok"]
    finally:
        b.close()


# ---------------------------------------------------------------- 도면
def test_평면은_윤곽_디딤판_선_화살표와_절단선을_그린다(base):
    mk_stair(base)
    p = mk(base, name="P", type="plan", level=F.LV1, cut_height=1200)
    ln = lines_of(base, p)
    td = 4000 / 17
    tmax = 7 * td                                                                              # 1200 / (3000/18) = 7.2 -> 디딤 7 번째 단
    assert ("proj", 1000.0, 400.0, round(1000 + tmax, 3), 400.0) in ln and ("proj", 1000.0, 1600.0, round(1000 + tmax, 3), 1600.0) in ln
    assert ("proj", 1000.0, 400.0, 1000.0, 1600.0) in ln                                       # 첫 챌판 쪽 끝선
    treads = [x for x in ln if x[0] == "proj" and x[1] == x[3] and x[2] == 400.0 and x[4] == 1600.0]
    assert len(treads) == 1 + 6                                                                # 첫 끝선 + 절단 위치 아래의 디딤판 사이 선 6개
    assert any(x[0] == "cut" for x in ln)                                                      # 절단선
    assert any(x[0] == "symbol" and x[2] == x[4] == 1000.0 for x in ln)                        # 걸음선
    assert sum(1 for x in ln if x[0] == "symbol" and 1000.0 <= x[1] <= 5000.0 and 400.0 <= x[2] <= 1600.0 and 1000.0 <= x[3] <= 5000.0 and 400.0 <= x[4] <= 1600.0) >= 3   # 걸음선 + 화살촉 둘
    assert not [f for f in fills_of(base, p) if all(1000.0 <= q[0] <= 5000.0 and 400.0 <= q[1] <= 1600.0 for q in f)]   # 계단 자리에는 채움이 없다
    hi = mk(base, name="P2", type="plan", level=F.LV1, cut_height=3500)                        # 절단 높이가 계단 위: 전체, 절단선 없음
    ln2 = lines_of(base, hi)
    assert not any(x[0] == "cut" for x in ln2)
    assert ("proj", 5000.0, 400.0, 5000.0, 1600.0) in ln2
    low = mk(base, name="P3", type="plan", level=F.LV1, cut_height=-100)                        # 계단 밑: 그리지 않는다
    assert not any(x[0] == "symbol" for x in lines_of(base, low))
    other = mk(base, name="P4", type="plan", level=F.LV2)                                       # 다른 레벨의 평면에는 나오지 않는다
    assert not any(x[0] == "symbol" for x in lines_of(base, other))


def test_단면은_걸음선과_평행하면_톱니_채움을_그린다(base):
    mk_stair(base)
    sec = mk(base, name="S", type="section", p1=[0, 1000], p2=[6000, 1000])                    # 걸음선 위를 지난다: s = x
    f = [x for x in fills_of(base, sec) if len(x) == 4 and min(p[0] for p in x) >= 1000.0 - 1e-6 and max(p[0] for p in x) <= 5000.0 + 1e-6 and max(p[1] for p in x) <= 3000.0 + 1e-6 and min(p[1] for p in x) == 0.0]
    assert len(f) == 17                                                                         # 디딤판마다 하나
    assert abs(max(p[1] for x in f for p in x) - (3000.0 - 3000 / 18)) < 1e-2                                # 마지막 디딤판 윗면
    cuts = [x for x in lines_of(base, sec) if x[0] == "cut" and x[2] == x[4] and x[1] != x[3] and 0 < x[2] < 3000]
    assert len(cuts) >= 16                                                                      # 디딤판 윗선이 합집합 윤곽으로 남는다
    # 걸음선과 직각인 단면은 한 덩어리 사각형(단면 평면 s = y)
    perp = mk(base, name="S2", type="section", p1=[3000, 0], p2=[3000, 2000])
    pf = [x for x in fills_of(base, perp) if {p[0] for p in x} == {400.0, 1600.0}]
    assert len(pf) == 1 and min(p[1] for p in pf[0]) == 0.0
    miss = mk(base, name="S3", type="section", p1=[7000, 0], p2=[7000, 2000])                # 계단 밖
    assert not [x for x in fills_of(base, miss) if max(p[1] for p in x) < 3000.0 and min(p[1] for p in x) == 0.0]


def test_입면은_옆모습_윤곽과_디딤판_모서리를_보인다(base):
    mk_stair(base, run=[1000, -2000, 5000, -2000])                                              # 벽 앞(남쪽 -y)에 선 계단
    el = mk(base, name="E", type="elevation", dir="S")
    ln = lines_of(base, el)
    assert any(x[0] == "proj" and x[2] == x[4] == 3000.0 for x in ln) or any(x[0] == "proj" and x[2] == x[4] == 3000.0 - 3000 / 18 for x in ln)
    assert sum(1 for x in ln if x[0] == "proj" and x[1] == x[3]) >= 10                          # 챌판 수직선들


def test_계단이_없으면_도면이_바뀌지_않는다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1), "s": mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000]),
             "e": mk(base, name="E", type="elevation", dir="S")}
    before = {k: (lines_of(base, v), fills_of(base, v)) for k, v in views.items()}
    mk_stair(base)
    assert base.run("stair.delete", {"id": F.ST1})["ok"]
    for k, v in views.items():
        assert (lines_of(base, v), fills_of(base, v)) == before[k], k


def test_계단_증분이_전체_재생성과_같다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1), "p2": mk(base, name="P2", type="plan", level=F.LV2),
             "s": mk(base, name="S", type="section", p1=[0, 1000], p2=[6000, 1000]), "s2": mk(base, name="S2", type="section", p1=[3000, -1000], p2=[3000, 3000]),
             "e": mk(base, name="E", type="elevation", dir="S"), "e2": mk(base, name="E2", type="elevation", dir="E")}
    before = {k: lines_of(base, v) for k, v in views.items()}

    def check():
        for k, v in views.items():
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k
            assert fills_of(base, v) == base.eng.g.q(D.view_fills, v)

    mk_stair(base)
    check()
    assert base.run("stair.set", {"id": F.ST1, "risers": 12, "width": 1000})["ok"]
    check()
    assert base.run("stair.move", {"id": F.ST1, "dx": 300, "dy": 200})["ok"]
    check()
    assert base.run("stair.set", {"id": F.ST1, "run": [500, 500, 500, 4500]})["ok"]           # 방향 바꿈(+y)
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("stair.delete", {"id": F.ST1})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k
    check()


def test_계단_도면과_해시는_결정적이다(tmp_path):
    def build(name):
        s = s3lib.kit.Session(tmp_path / name, guid=s3lib.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        assert s.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": [500, 500, 500, 3500]})["ok"]
        vs = [mk(s, name="P", type="plan", level=F.LV1), mk(s, name="S", type="section", p1=[0, 1000], p2=[2000, 1000]), mk(s, name="E", type="elevation", dir="E")]
        out = ([lines_of(s, v) for v in vs], [fills_of(s, v) for v in vs], s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a[0] == b[0] and a[1] == b[1] and a[2]["state"] == b[2]["state"] and a[2]["derived"] == b[2]["derived"] and a[2]["ifc"] == b[2]["ifc"]
