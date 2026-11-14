# SPDX-License-Identifier: MPL-2.0
"""슬래브 종류(slab): 커맨드·검사, IFC 투영·가져오기 왕복, 모델 해시, 도면(평면 절단·단면 절단 사각형·입면 모서리), 증분 == 전체 재생성, 결정성."""
import sys

import ifcopenshell

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
# 오목(L 자) 경계: 4000x4000 정사각형에서 오른쪽 위 2000x2000 을 뺀 모양
LSHAPE = [[0, 0], [4000, 0], [4000, 2000], [2000, 2000], [2000, 4000], [0, 4000]]


def mk_slab(sess, boundary=RECT, level=F.LV2, **kw):
    r = sess.run("slab.create", {"id": F.S1, "level": level, "boundary": boundary, **kw})
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


def slab_fills(sess, vid, z0=3100.0, z1=3300.0):
    """단면 채움 중 슬래브 절단 사각형(z 범위가 슬래브 두께와 같은 것)."""
    return [f for f in fills_of(sess, vid) if {p[1] for p in f} == {z0, z1}]


# ---------------------------------------------------------------- 커맨드·검사
def test_슬래브_생성은_반시계로_저장하고_레벨에_걸린다(base):
    mk_slab(base, boundary=RECT[::-1], top_offset=0, thickness=250)           # 시계 방향으로 줘도
    r = base.run("element.get", {"id": F.S1})["result"]
    b = r["params"]["boundary"]
    assert r["kind"] == "slab" and r["ifc_class"] == "IfcSlab"
    xs = sum(b[i][0] * b[(i + 1) % 4][1] - b[(i + 1) % 4][0] * b[i][1] for i in range(4))
    assert xs > 0                                                             # 반시계
    assert r["relations"]["on_level"] == [F.LV2]
    assert r["derived"] == {"bottom": 3050.0, "top": 3300.0}
    assert base.run("model.stats", {})["result"]["counts"]["slab"] == 1


def test_잘못된_경계는_거절한다(base):
    for bad, why in (([[0, 0], [1000, 0]], "꼭짓점 2개"), ([[0, 0], [1000, 0], [2000, 0]], "면적 0"),
                     ([[0, 0], [4000, 4000], [4000, 0], [0, 4000]], "나비 모양(자기 교차)"), ([[0, 0], [0, 0], [1000, 0], [0, 1000]], "반복 꼭짓점")):
        r = base.run("slab.create", {"level": F.LV2, "boundary": bad})
        assert not r["ok"] and r["error"]["code"] in ("CONSTRAINT", "SCHEMA_INVALID"), why
    r = base.run("slab.create", {"level": F.LV2, "boundary": RECT, "thickness": 0})
    assert not r["ok"]
    r = base.run("slab.create", {"level": "9" * 22, "boundary": RECT})
    assert not r["ok"]                                                        # 없는 레벨
    assert "slab" not in base.run("model.stats", {})["result"]["counts"]


def test_슬래브_수정_이동_삭제와_실행_취소(base):
    mk_slab(base)
    h0 = base.eng.state_hash()
    assert base.run("slab.set", {"id": F.S1, "thickness": 300, "top_offset": -50.0})["ok"]
    assert base.run("element.get", {"id": F.S1})["result"]["derived"] == {"bottom": 2950.0, "top": 3250.0}
    assert base.run("slab.move", {"id": F.S1, "dx": 100.0, "dy": -200.0})["ok"]
    assert base.run("element.get", {"id": F.S1})["result"]["params"]["boundary"][0] == [100.0, -200.0]
    assert not base.run("slab.set", {"id": F.S1})["ok"]                       # 바꿀 필드 없음
    assert not base.run("slab.set", {"id": F.S1, "boundary": [[0, 0], [1, 1], [1, 0], [0, 1]]})["ok"]
    assert not base.run("slab.move", {"id": F.W1, "dx": 1})["ok"]             # 슬래브가 아닌 요소
    assert base.run("slab.delete", {"id": F.S1})["ok"]
    assert base.run("element.get", {"id": F.S1})["error"]["code"] == "NOT_FOUND"
    for _ in range(3):                                                        # 삭제·이동·수정을 되돌리면 생성 직후 상태다
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0
    assert base.run("history.undo", {})["ok"]                                 # 생성까지 되돌리면 슬래브가 없다
    assert base.run("element.get", {"id": F.S1})["error"]["code"] == "NOT_FOUND"


def test_슬래브가_있는_레벨은_지울_수_없고_element_delete도_된다(base):
    mk_slab(base)
    r = base.run("element.delete", {"id": F.LV2})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    assert base.run("element.delete", {"id": F.S1})["ok"]
    assert base.run("element.delete", {"id": F.LV2})["ok"]


def test_슬래브_높이_참조는_치수로_잴_수_있다(base):
    mk_slab(base, top_offset=0, thickness=200)
    v = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
    r = base.run("annot.dim.create", {"view": v, "a": f"slab:{F.S1}#bottom", "b": f"slab:{F.S1}#top"})
    assert r["ok"], r
    d = base.run("annot.list", {"view": v})["result"]["items"][0]
    assert d["value"] == 200.0 or d.get("text") == "200" or "200" in str(d)


# ---------------------------------------------------------------- IFC 투영·왕복
def test_IFC_투영은_IfcSlab_압출_두께와_레벨_소속을_갖는다(base, tmp_path):
    mk_slab(base, boundary=LSHAPE, top_offset=0, thickness=200)
    out = tmp_path / "s.ifc"
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    sl = f.by_guid(F.S1)
    assert sl.is_a("IfcSlab") and sl.PredefinedType == "FLOOR"
    solid = sl.Representation.Representations[0].Items[0]
    assert solid.is_a("IfcExtrudedAreaSolid") and solid.Depth == 200.0
    assert len(solid.SweptArea.OuterCurve.Points) == len(LSHAPE) + 1          # 닫힌 폴리라인
    assert solid.Position.Location.Coordinates[2] == -200.0                  # 윗면(오프셋 0)에서 아래로 두께만큼
    cont = [r for r in f.get_inverse(sl) if r.is_a("IfcRelContainedInSpatialStructure")]
    assert cont and cont[0].RelatingStructure.GlobalId == F.LV2


def test_슬래브를_내보내고_가져오면_저작_슬래브로_되살아나고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": LSHAPE, "thickness": 220, "top_offset": 10})["ok"]
        h = b.hashes()
        b.export("s.ifc")
        r = b.import_ifc("s.ifc", "imp.dobim")
        assert r["ok"], r
        assert b.origins("imp.dobim") == {"authored": 8, "foreign": 0, "detached": 0}
        g = b.run("element.get", {"id": F.S1}, project="imp.dobim")["result"]
        assert g["kind"] == "slab" and g["origin"] == "authored"
        assert g["params"]["boundary"] == [[float(x), float(y)] for x, y in LSHAPE] and g["params"]["thickness"] == 220.0
        hi = b.hashes("imp.dobim")
        assert hi["state"] == h["state"] and hi["derived"] == h["derived"]    # 의도·파생 해시가 왕복 뒤에도 같다
        # 가져온 슬래브를 고치면 저작 슬래브처럼 다시 투영된다
        assert b.run("slab.set", {"id": F.S1, "thickness": 300}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_외부에서_슬래브_형상을_바꾸면_분리되고_도면은_그리지_않는다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": RECT})["ok"]
        b.export("s.ifc")
        f = ifcopenshell.open(str(tmp_path / "s.ifc"))
        f.by_guid(F.S1).Representation.Representations[0].Items[0].Depth = 999.0
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
        assert not b.run("slab.set", {"id": F.S1, "thickness": 300}, project="imp.dobim")["ok"]   # 동결
    finally:
        b.close()


# ---------------------------------------------------------------- 도면
def test_단면은_슬래브를_절단_사각형_채움과_윤곽으로_그린다(base):
    mk_slab(base, top_offset=0, thickness=200)                                # 2F(3300) 윗면, 3100..3300
    sec = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])   # 보는 방향 -x, s = y + 2000
    assert ((2000.0, 3100.0), (6000.0, 3100.0), (6000.0, 3300.0), (2000.0, 3300.0)) in fills_of(base, sec)
    assert len(slab_fills(base, sec)) == 1
    ln = lines_of(base, sec)
    cuts = [x for x in ln if x[0] == "cut"]
    assert ("cut", 2000.0, 3100.0, 6000.0, 3100.0) in cuts and ("cut", 2000.0, 3300.0, 6000.0, 3300.0) in cuts
    assert ("cut", 2000.0, 3100.0, 2000.0, 3300.0) in cuts and ("cut", 6000.0, 3100.0, 6000.0, 3300.0) in cuts
    other = mk(base, name="S2", type="section", p1=[7000, -1000], p2=[7000, 1000])   # 슬래브 밖을 지나는 단면: 절단 없음
    assert slab_fills(base, other) == []


def test_오목_경계는_단면마다_교차_구간이_다르다(base):
    mk_slab(base, boundary=LSHAPE, thickness=200)
    arm = mk(base, name="S", type="section", p1=[-1000, 3000], p2=[5000, 3000])      # y=3000: x 0..2000 만 슬래브(L 자의 위쪽 팔), s = x + 1000
    f = slab_fills(base, arm)
    assert f == [((1000.0, 3100.0), (3000.0, 3100.0), (3000.0, 3300.0), (1000.0, 3300.0))]
    low = mk(base, name="S2", type="section", p1=[-1000, 1000], p2=[5000, 1000])     # y=1000: x 0..4000
    assert slab_fills(base, low) == [((1000.0, 3100.0), (5000.0, 3100.0), (5000.0, 3300.0), (1000.0, 3300.0))]
    # 오목 모서리 안쪽을 가로지르는 단면: x=3000 은 y 0..2000 만(구간 하나, 폭 2000)
    notch = mk(base, name="S3", type="section", p1=[3000, -1000], p2=[3000, 5000])
    assert slab_fills(base, notch) == [((1000.0, 3100.0), (3000.0, 3100.0), (3000.0, 3300.0), (1000.0, 3300.0))]
    # U 자 경계: 한 단면이 슬래브를 두 번 자른다(구간 둘)
    assert base.run("slab.set", {"id": F.S1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [4000, 4000], [4000, 1000], [2000, 1000], [2000, 4000], [0, 4000]]})["ok"]
    cross = mk(base, name="S4", type="section", p1=[-1000, 3000], p2=[7000, 3000])
    assert len(slab_fills(base, cross)) == 2


def test_벽_위에_앉은_슬래브의_이음매_선이_없다(base):
    # 2F 슬래브(3100..3300)를 1F 벽 W1·W3(0..3000)에 얹힌 것이 아니라 벽 위(3000..3300 이 비는 곳)와 맞닿게 한다
    assert base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[-100, -100], [6100, -100], [6100, 4100], [-100, 4100]],
                                    "thickness": 200, "top_offset": 3200})["ok"]    # 윗면 3200, 밑면 3000: 벽 윗면과 맞닿는다
    sec = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 6000])
    ln = lines_of(base, sec)
    # 벽 윗선(z=3000)과 슬래브 아랫선(z=3000)이 맞닿는 구간은 한 겹으로만 남는다: 같은 직선 위 cut 선이 겹치지 않는다
    seams = [x for x in ln if x[0] == "cut" and x[2] == x[4] == 3000.0]
    for a in seams:
        for b in seams:
            if a is not b:
                assert a[3] <= b[1] or b[3] <= a[1], (a, b)


def test_입면은_슬래브_옆면_모서리를_보인다(base):
    assert base.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": [[-500, -500], [6500, -500], [6500, 4500], [-500, 4500]],
                                    "thickness": 200})["ok"]
    el = mk(base, name="E", type="elevation", dir="S")
    ln = lines_of(base, el)
    # 슬래브가 벽 앞으로 나와 있으므로 윗·아랫면 선과 양 끝 수직 모서리가 보인다
    assert ("proj", -500.0, 3300.0, 6500.0, 3300.0) in ln or any(x[2] == x[4] == 3300.0 and x[1] <= -500.0 and x[3] >= 6500.0 for x in ln)
    assert any(x[2] == x[4] == 3100.0 for x in ln)
    assert any(x[1] == x[3] and abs(x[2] - 3100.0) < 1e-6 and abs(x[4] - 3300.0) < 1e-6 for x in ln)


def test_평면은_절단_높이가_슬래브_두께_안일_때만_윤곽과_채움을_그린다(base):
    mk_slab(base, level=F.LV1, boundary=RECT, thickness=300, top_offset=0)                 # -300..0
    p0 = mk(base, name="P0", type="plan", level=F.LV1, cut_height=-100)                    # 두께 안
    assert RECT_FILL(base, p0)
    p1 = mk(base, name="P1", type="plan", level=F.LV1, cut_height=1200)                    # 두께 밖: 그리지 않는다
    assert not RECT_FILL(base, p1)
    base_plan_lines = lines_of(base, p1)
    assert base.run("slab.delete", {"id": F.S1})["ok"]
    assert lines_of(base, p1) == base_plan_lines                                           # 슬래브가 평면 선분을 바꾸지 않았다


def RECT_FILL(sess, vid):
    want = tuple((float(x), float(y)) for x, y in RECT)
    return any(set(f) == set(want) for f in fills_of(sess, vid))


def test_슬래브가_없으면_도면이_바뀌지_않는다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1), "s": mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000]),
             "e": mk(base, name="E", type="elevation", dir="S")}
    before = {k: lines_of(base, v) for k, v in views.items()}
    mk_slab(base)
    assert base.run("slab.delete", {"id": F.S1})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k


def test_슬래브_증분이_전체_재생성과_같다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1, cut_height=-100), "p2": mk(base, name="P2", type="plan", level=F.LV2),
             "s": mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000]),
             "s2": mk(base, name="S2", type="section", p1=[-1000, 1000], p2=[7000, 1000]), "e": mk(base, name="E", type="elevation", dir="S"),
             "e2": mk(base, name="E2", type="elevation", dir="E")}
    before = {k: lines_of(base, v) for k, v in views.items()}

    def check():
        for k, v in views.items():
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k
            assert fills_of(base, v) == base.eng.g.q(D.view_fills, v)

    mk_slab(base, boundary=LSHAPE, thickness=200)
    check()
    assert base.run("slab.set", {"id": F.S1, "boundary": [[-300, -300], [6300, -300], [6300, 4300], [-300, 4300]], "thickness": 260})["ok"]
    check()
    assert base.run("slab.move", {"id": F.S1, "dx": 500, "dy": 100})["ok"]
    check()
    assert base.run("level.create", {"name": "R", "elevation": 7000})["ok"]
    assert base.run("slab.set", {"id": F.S1, "top_offset": 150})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("slab.delete", {"id": F.S1})["ok"]
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("slab.delete", {"id": F.S1})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k                              # 지우면 슬래브가 없던 때의 선분으로 돌아간다
    check()


def test_슬래브_도면과_해시는_결정적이다(tmp_path):
    def build(name):
        s = s3lib.kit.Session(tmp_path / name, guid=s3lib.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        assert s.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": LSHAPE, "thickness": 200})["ok"]
        vs = [mk(s, name="S", type="section", p1=[1000, -1000], p2=[1000, 5000]), mk(s, name="E", type="elevation", dir="S")]
        out = ([lines_of(s, v) for v in vs], [fills_of(s, v) for v in vs], s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a[0] == b[0] and a[1] == b[1] and a[2]["state"] == b[2]["state"] and a[2]["derived"] == b[2]["derived"] and a[2]["ifc"] == b[2]["ifc"]


def test_삼각형_분할은_오목_경계의_면적을_보존한다(base):
    mk_slab(base, boundary=LSHAPE)
    base.eng.sync()
    base.eng._touch_pending()
    tris = base.eng.g.q(D.slab_tris, F.S1)
    area = sum(abs(D._area2(t)) for t in tris) / 2
    assert abs(area - 12_000_000.0) < 1e-6                                     # 16m² - 4m²
    assert all(D._area2(t) > 0 for t in tris)                                  # 모두 반시계 볼록 조각
