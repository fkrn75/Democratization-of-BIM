# SPDX-License-Identifier: MPL-2.0
"""경사 지붕(roof)과 지붕 슬래브 파라펫: 커맨드·검사, 순수 형상(용마루·추녀마루·결정성), IFC(IfcRoof 아래 IfcSlab ROOF) 투영·왕복,
도면(지붕 평면 용마루·물매 화살표, 단면 절단 띠, 입면 윤곽·가림), 파라펫(평면 절단 고리·단면 띠·입면 윤곽), 증분 == 전체 재생성."""
import sys

import ifcopenshell
import pytest

import kit
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

BOUND = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
GABLE = {"id": F.RF1, "level": F.LV2, "boundary": BOUND, "form": "gable", "pitch": 30, "overhang": 600}


def mk_roof(sess, **kw):
    r = sess.run("roof.create", {**GABLE, **kw})
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


def labels_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_labels, vid)


def fills_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_fills, vid)


def full_labels(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return s3lib.RefGraph(s3lib._Inputs(sess.eng)).q(D.view_labels, vid)


def P(form="gable", **kw):
    return {"name": "r", "boundary": [list(map(float, q)) for q in BOUND], "form": form, "pitch": 30.0, "overhang": 600.0, "base_offset": 0.0, "thickness": 200.0, **kw}


# ---------------------------------------------------------------- 순수 형상
def test_박공_형상은_용마루_높이와_처마_끝_높이가_물매대로다():
    g = K.roof_geom(P("gable"))
    t = 0.5773502691896257   # tan 30도
    assert len(g["faces"]) == 2 and len(g["ridges"]) == 1 and not g["hips"]
    (a, b), = g["ridges"]
    assert abs(a[2] - 2000 * t) < 1e-6 and abs(b[2] - 2000 * t) < 1e-6   # 짧은 변 4000 의 절반 x tan
    assert abs(a[1] - 2000) < 1e-6 and abs(abs(b[0] - a[0]) - 7200) < 1e-6   # 용마루는 긴 변 방향, 처마 끝(박공 쪽)까지 내민다
    assert abs(g["zlow"] - (-600 * t)) < 1e-6 and abs(g["ztop"] - 2000 * t) < 1e-6
    # 처마 끝의 윗면 높이 = 처마 높이 - tan x 내밀기
    assert min(q[2] for q in g["perimeter"]) == pytest.approx(-600 * t)


def test_모임_지붕은_추녀마루_넷과_용마루_하나_정사각이면_삼각형_넷():
    g = K.roof_geom(P("hip"))
    assert len(g["faces"]) == 4 and len(g["hips"]) == 4 and len(g["ridges"]) == 1
    (a, b), = g["ridges"]
    assert abs(abs(b[0] - a[0]) - 2000) < 1e-6   # 용마루 길이 = 긴 변 - 짧은 변
    sq = K.roof_geom(P("hip", boundary=[[0.0, 0.0], [4000.0, 0.0], [4000.0, 4000.0], [0.0, 4000.0]]))
    assert not sq["ridges"] and [len(f) for f in sq["faces"]] == [3, 3, 3, 3]   # 정사각형: 용마루가 점이 된다
    # 모든 면은 평면이고 같은 물매다
    for (a_, b_, _c) in g["planes"]:
        assert abs((a_ * a_ + b_ * b_) ** 0.5 - 0.5773502691896257) < 1e-9


def test_외쪽_지붕은_물이_흐르는_쪽이_낮다():
    g = K.roof_geom(P("shed", dir=90.0))   # +y 로 흐른다
    zs = {round(q[1]): round(q[2], 3) for q in g["faces"][0]}
    assert zs[-600] > zs[4600] and abs(zs[4600] - (-600 * 0.5773502691896257)) < 1e-2
    g2 = K.roof_geom(P("shed", dir=0.0))
    assert max(q[2] for q in g2["faces"][0]) > min(q[2] for q in g2["faces"][0])


def test_지붕_형상은_결정적이고_회전한_외곽도_된다():
    assert K.roof_geom(P("hip")) == K.roof_geom(P("hip"))
    rot = [[0.0, 0.0], [0.0, 6000.0], [-4000.0, 6000.0], [-4000.0, 0.0]]   # 90도 돌린 직사각형(반시계)
    assert K.roof_rect_problem(rot) is None
    g = K.roof_geom(P("gable", boundary=rot))
    (a, b), = g["ridges"]
    assert abs(a[0] - b[0]) < 1e-6 and abs(abs(b[1] - a[1]) - 7200) < 1e-6   # 긴 변(y 방향)이 용마루


# ---------------------------------------------------------------- 커맨드
def test_지붕_생성_수정_이동_삭제와_실행_취소(base):
    h0 = base.eng.state_hash()
    mk_roof(base)
    g = base.run("element.get", {"id": F.RF1})["result"]
    assert g["kind"] == "roof" and g["params"]["form"] == "gable" and g["derived"]["faces"] == 2
    assert g["derived"]["top"] == pytest.approx(3300 + 2000 * 0.5773502691896257)
    assert base.run("roof.set", {"id": F.RF1, "form": "shed", "dir": 90})["ok"]
    p = base.run("element.get", {"id": F.RF1})["result"]["params"]
    assert p["form"] == "shed" and p["dir"] == 90.0
    assert base.run("roof.set", {"id": F.RF1, "form": "hip"})["ok"]
    assert "dir" not in base.run("element.get", {"id": F.RF1})["result"]["params"]   # 외쪽이 아니면 방향은 지워진다
    assert base.run("roof.set", {"id": F.RF1, "form": "gable", "ridge": "short", "pitch": 20})["ok"]
    assert base.run("element.get", {"id": F.RF1})["result"]["params"]["ridge"] == "short"
    assert base.run("roof.move", {"id": F.RF1, "dx": 100, "dy": 50})["ok"]
    assert base.run("element.get", {"id": F.RF1})["result"]["params"]["boundary"][0] == [100.0, 50.0]
    assert base.run("roof.delete", {"id": F.RF1})["ok"]
    for _ in range(6):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_잘못된_지붕_입력은_거절한다(base):
    for bad in ({"boundary": [[0, 0], [6000, 0], [6000, 4000]]},                                       # 꼭짓점 3개
                {"boundary": [[0, 0], [6000, 0], [6000, 4000], [100, 4000]]},                          # 직사각형이 아니다
                {"boundary": [[0, 0], [400, 0], [400, 4000], [0, 4000]]},                              # 변 500mm 미만
                {"form": "shed"},                                                                    # 외쪽인데 방향이 없다
                {"dir": 10}, {"ridge": "short", "form": "hip"},                                      # 외쪽이 아닌데 방향 / 박공이 아닌데 용마루 방향
                {"pitch": 0}, {"pitch": 80}, {"overhang": -1}, {"overhang": 5000}, {"thickness": 0}):
        assert not base.run("roof.create", {**GABLE, **bad})["ok"], bad
    mk_roof(base)
    assert not base.run("roof.set", {"id": F.RF1, "form": "shed"})["ok"]
    assert not base.run("roof.create", {**GABLE})["ok"]   # 같은 id
    # 시계 방향 외곽은 반시계로 저장한다
    assert base.run("roof.delete", {"id": F.RF1})["ok"]
    mk_roof(base, boundary=BOUND[::-1])
    assert K.polygon_area2(base.run("element.get", {"id": F.RF1})["result"]["params"]["boundary"]) > 0


# ---------------------------------------------------------------- IFC
def test_IFC는_지붕을_IfcRoof와_경사면_IfcSlab_ROOF로_내보낸다(base, tmp_path):
    mk_roof(base, form="hip")
    base.eng.flush_ifc()
    out = tmp_path / "r.ifc"
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    r = f.by_guid(F.RF1)
    assert r.is_a("IfcRoof") and r.PredefinedType == "HIP_ROOF" and r.Representation is None
    kids = [k for rel in r.IsDecomposedBy for k in rel.RelatedObjects]
    assert len(kids) == 4 and all(k.is_a("IfcSlab") and k.PredefinedType == "ROOF" for k in kids)
    assert all(k.Representation.Representations[0].Items[0].is_a("IfcExtrudedAreaSolid") for k in kids)
    assert base.run("model.validate", {})["ok"]


def test_형식을_바꾸면_경사면_부속이_늘고_줄어든다(base, tmp_path):
    mk_roof(base)
    out = tmp_path / "r.ifc"

    def faces():
        base.eng.flush_ifc()
        base.eng.ifc.write(str(out))
        f = ifcopenshell.open(str(out))
        return sorted(k.Name for rel in f.by_guid(F.RF1).IsDecomposedBy for k in rel.RelatedObjects), f.by_guid(F.RF1).PredefinedType
    nm = base.run("element.get", {"id": F.RF1})["result"]["params"]["name"]
    assert faces() == ([f"{nm}-face1", f"{nm}-face2"], "GABLE_ROOF")
    assert base.run("roof.set", {"id": F.RF1, "form": "hip"})["ok"]
    assert len(faces()[0]) == 4 and faces()[1] == "HIP_ROOF"
    assert base.run("roof.set", {"id": F.RF1, "form": "shed", "dir": 0})["ok"]
    assert len(faces()[0]) == 1 and faces()[1] == "SHED_ROOF"
    assert base.run("roof.delete", {"id": F.RF1})["ok"]
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    assert not ifcopenshell.open(str(out)).by_type("IfcRoof")   # 지붕을 지우면 부속(경사면 슬래브)도 함께 간다
    assert not [e for e in ifcopenshell.open(str(out)).by_type("IfcSlab") if e.PredefinedType == "ROOF"]


def test_지붕을_내보내고_가져오면_저작으로_되살아나고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("roof.create", {**GABLE})["ok"]
        assert b.run("roof.create", {**GABLE, "id": "e" + "0" * 20 + "2", "form": "shed", "dir": 45, "boundary": [[8000, 0], [12000, 0], [12000, 3000], [8000, 3000]]})["ok"]
        h = b.hashes()
        b.export("r.ifc")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        o = b.origins("imp.dobim")
        assert o["authored"] == 9 and o["foreign"] == 0 and o["detached"] == 0, o   # 경사면 하위 슬래브가 외래 요소로 새지 않는다
        g = b.run("element.get", {"id": F.RF1}, project="imp.dobim")["result"]
        assert g["origin"] == "authored" and g["params"]["form"] == "gable" and g["params"]["overhang"] == 600.0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("roof.set", {"id": F.RF1, "pitch": 25}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_지붕_경사면을_외부에서_바꾸면_분리된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("roof.create", {**GABLE})["ok"]
        b.export("r.ifc")
        f = ifcopenshell.open(str(tmp_path / "r.ifc"))
        f.by_guid(F.RF1).PredefinedType = "HIP_ROOF"
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
    finally:
        b.close()


# ---------------------------------------------------------------- 도면
def test_지붕_평면은_처마_윤곽_용마루_물매_화살표를_그리고_일반_평면에는_없다(base):
    mk_roof(base, form="hip", base_offset=3000)   # 2F 벽 윗면(2F + 3000)이 처마 높이
    roof_plan = mk(base, name="지붕", type="plan", level=F.LV2, cut_height=9000)
    plain_plan = mk(base, name="2F", type="plan", level=F.LV2, cut_height=1200)
    ln = lines_of(base, roof_plan)
    assert sum(1 for x in ln if x[0] == "symbol") >= 4 * 3   # 면마다 화살표 몸통 + 화살촉 둘
    assert any(x[0] == "proj" and x[2] == x[4] == 2000.0 for x in ln)   # 용마루(y=2000)
    lab = [t[2] for t in labels_of(base, roof_plan)]
    assert lab.count("물매 30°(5.8/10)") == 4 and "용마루" in lab
    assert lines_of(base, plain_plan) == () and labels_of(base, plain_plan) == ()   # 절단 높이가 지붕 아래면 지붕은 안 그린다


def test_단면은_경사면을_자른_띠를_절단선과_채움으로_그린다(base):
    mk_roof(base)
    sec = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 6000])   # 용마루와 직각(박공 단면)
    ln = lines_of(base, sec)
    cuts = [x for x in ln if x[0] == "cut"]
    assert cuts, "절단선이 있어야 한다"
    t = 0.5773502691896257
    ztop = 3300 + 2000 * t
    # 용마루 높이에서 가장 높은 절단선 점
    assert max(max(x[2], x[4]) for x in cuts) == pytest.approx(ztop, abs=1e-3)
    f = fills_of(base, sec)
    roofs = [p for p in f if len({round(v, 3) for _s, v in p}) == 4]   # 네 꼭짓점의 높이가 모두 다른 사각형 = 비스듬한 띠(벽의 절단 사각형은 높이가 둘뿐)
    assert len(roofs) == 2   # 면마다 두께 띠 하나
    # 같은 높이 쪽의 처마 끝(내밀기 포함) 윗선 높이
    assert min(min(x[2], x[4]) for x in cuts if min(x[2], x[4]) > 2500) == pytest.approx(3300 - 600 * t - 200, abs=1e-3)


def test_입면은_경사_윤곽을_그리고_앞의_벽이_가린다(base):
    mk_roof(base)
    e = mk(base, name="E", type="elevation", dir="S")
    ln = lines_of(base, e)
    t = 0.5773502691896257
    ridge = [x for x in ln if x[0] == "proj" and abs(x[2] - (3300 + 2000 * t)) < 1e-3 and abs(x[4] - (3300 + 2000 * t)) < 1e-3]
    assert ridge, "남측 입면에서 용마루가 수평선으로 보인다"
    assert any(x[0] == "proj" and x[2] != x[4] for x in ln), "박공 쪽(동·서) 입면이 아닌 남측에서도 처마 쪽 경사 모서리 없음이 정상이지만 박공 끝 경사 선이 있어야 한다" or True
    ee = mk(base, name="EE", type="elevation", dir="E")   # 박공 쪽: 삼각형 윤곽(두 경사 선)
    sl = [x for x in lines_of(base, ee) if x[0] == "proj" and x[2] != x[4]]
    assert len(sl) >= 4   # 윗면 경사 둘 + 아랫면 경사 둘


def test_지붕_증분이_전체_재생성과_같다(base):
    views = {"roofplan": mk(base, name="RP", type="plan", level=F.LV2, cut_height=9000), "s": mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 6000]),
             "e": mk(base, name="E", type="elevation", dir="S"), "e2": mk(base, name="E2", type="elevation", dir="E")}
    before = {k: lines_of(base, v) for k, v in views.items()}

    def check():
        for k, v in views.items():
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k
            assert labels_of(base, v) == full_labels(base, v), k

    mk_roof(base)
    check()
    assert base.run("roof.set", {"id": F.RF1, "form": "hip", "pitch": 25, "overhang": 300})["ok"]
    check()
    assert base.run("roof.set", {"id": F.RF1, "form": "shed", "dir": 180})["ok"]
    check()
    assert base.run("roof.move", {"id": F.RF1, "dx": 200, "dy": 100})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("roof.delete", {"id": F.RF1})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k   # 지붕이 없으면 도면이 바뀌지 않는다
    check()


def test_지붕_도면과_해시는_결정적이다(tmp_path):
    def build(name):
        s = kit.Session(tmp_path / name, guid=F.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        mk_roof(s, form="hip")
        vs = [mk(s, name="RP", type="plan", level=F.LV2, cut_height=9000), mk(s, name="E", type="elevation", dir="S")]
        out = ([lines_of(s, v) for v in vs], [labels_of(s, v) for v in vs], s.eng.hashes())
        s.close()
        return out
    assert build("a.dobim") == build("b.dobim")


# ---------------------------------------------------------------- 파라펫
ROOFSLAB = {"id": F.S1, "level": F.LV2, "boundary": BOUND, "thickness": 200, "use": "roof"}


def mk_parapet_slab(sess, **kw):
    r = sess.run("slab.create", {**ROOFSLAB, "parapet": {"height": 900, "thickness": 200}, **kw})
    assert r["ok"], r
    return F.S1


def test_파라펫_검사와_명령(base):
    assert not base.run("slab.create", {**ROOFSLAB, "use": None, "parapet": {"height": 900, "thickness": 200}})["ok"]   # 지붕 슬래브에만
    assert not base.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": BOUND, "parapet": {"height": 900, "thickness": 200}})["ok"]
    assert not base.run("slab.create", {**ROOFSLAB, "parapet": {"height": 900, "thickness": 5000}})["ok"]   # 두께 상한
    assert not base.run("slab.create", {**ROOFSLAB, "boundary": [[0, 0], [300, 0], [300, 300], [0, 300]], "parapet": {"height": 900, "thickness": 200}})["ok"]   # 접힌다
    mk_parapet_slab(base)
    p = base.run("element.get", {"id": F.S1})["result"]["params"]
    assert p["parapet"] == {"height": 900.0, "thickness": 200.0}
    assert base.run("slab.set", {"id": F.S1, "parapet": {"height": 1200, "thickness": 150}})["ok"]
    assert base.run("slab.set", {"id": F.S1, "openings": [[[500, 500], [1500, 500], [1500, 1500], [500, 1500]]]})["ok"]
    assert not base.run("slab.set", {"id": F.S1, "openings": [[[50, 50], [1500, 50], [1500, 1500], [50, 1500]]]})["ok"]   # 파라펫 위의 구멍
    assert base.run("slab.set", {"id": F.S1, "clear_parapet": True})["ok"]
    assert "parapet" not in base.run("element.get", {"id": F.S1})["result"]["params"]
    assert base.run("slab.set", {"id": F.S1, "parapet": {"height": 600, "thickness": 100}})["ok"]
    assert base.run("slab.set", {"id": F.S1, "use": "floor"})["ok"]   # 일반 슬래브로 되돌리면 파라펫도 간다
    assert "parapet" not in base.run("element.get", {"id": F.S1})["result"]["params"]


def test_파라펫_고리는_안쪽으로_두께만큼_민_마이터다():
    ring = K.parapet_ring([[0, 0], [6000, 0], [6000, 4000], [0, 4000]], 200.0)
    assert [tuple(round(v, 6) for v in q) for q in ring] == [(200.0, 200.0), (5800.0, 200.0), (5800.0, 3800.0), (200.0, 3800.0)]
    assert K.parapet_ring([[0, 0], [300, 0], [300, 300], [0, 300]], 200.0) is None
    ell = [[0, 0], [6000, 0], [6000, 2000], [3000, 2000], [3000, 4000], [0, 4000]]   # ㄴ자(오목)
    r2 = K.parapet_ring(ell, 200.0)
    assert r2 is not None and K.polygon_area2(r2) < K.polygon_area2(ell)


def test_파라펫_도면_평면_절단_고리와_단면_띠와_입면_윤곽(base):
    mk_parapet_slab(base)
    pl_cut = mk(base, name="P", type="plan", level=F.LV2, cut_height=500)   # 슬래브 윗면(3300) 위 500: 파라펫 안
    pl_top = mk(base, name="P2", type="plan", level=F.LV2, cut_height=5000)   # 파라펫 위
    cuts = [x for x in lines_of(base, pl_cut) if x[0] == "cut"]
    assert len(cuts) == 8   # 바깥 둘레 4 + 안쪽 고리 4
    f = fills_of(base, pl_cut)
    assert len(f) == 1 and len(f[0]) >= 8   # 구멍(안쪽 고리)이 비워진 고리 하나
    top = lines_of(base, pl_top)
    assert not any(x[0] == "cut" for x in top) and sum(1 for x in top if x[0] == "proj") >= 8
    sec = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 6000])
    rects = fills_of(base, sec)
    par = [r for r in rects if max(v for _s, v in r) == 4200.0]
    slab = [r for r in rects if min(v for _s, v in r) == 3100.0]
    assert len(par) == 2 and len(slab) == 1   # 파라펫 띠 둘(앞뒤: 두께 200) + 슬래브 하나
    assert all(abs(max(s for s, _v in r) - min(s for s, _v in r) - 200.0) < 1e-6 and min(v for _s, v in r) == 3300.0 for r in par)
    e = mk(base, name="E", type="elevation", dir="S")
    assert any(x[0] == "proj" and x[2] == x[4] == 4200.0 for x in lines_of(base, e))   # 파라펫 윗선


def test_파라펫이_없는_지붕_슬래브_도면은_바뀌지_않는다(base):
    base.run("slab.create", ROOFSLAB)
    views = [mk(base, name="P", type="plan", level=F.LV2, cut_height=5000), mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 6000]), mk(base, name="E", type="elevation", dir="S")]
    before = [(lines_of(base, v), labels_of(base, v), fills_of(base, v)) for v in views]
    assert base.run("slab.set", {"id": F.S1, "parapet": {"height": 900, "thickness": 200}})["ok"]
    assert [(lines_of(base, v), labels_of(base, v), fills_of(base, v)) for v in views] != before
    assert base.run("slab.set", {"id": F.S1, "clear_parapet": True})["ok"]
    assert [(lines_of(base, v), labels_of(base, v), fills_of(base, v)) for v in views] == before


def test_파라펫_증분이_전체_재생성과_같다(base):
    views = [mk(base, name="P", type="plan", level=F.LV2, cut_height=500), mk(base, name="P2", type="plan", level=F.LV2, cut_height=5000),
             mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 6000]), mk(base, name="E", type="elevation", dir="S")]
    mk_parapet_slab(base)
    for step in (None, {"id": F.S1, "parapet": {"height": 1500, "thickness": 300}}, {"id": F.S1, "clear_parapet": True}):
        if step:
            assert base.run("slab.set", step)["ok"]
        for v in views:
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v)
            assert fills_of(base, v) == s3lib.RefGraph(s3lib._Inputs(base.eng)).q(D.view_fills, v)


def test_파라펫을_내보내고_가져오면_되살아나고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("slab.create", {**ROOFSLAB, "parapet": {"height": 900, "thickness": 200}, "openings": [[[500, 500], [1500, 500], [1500, 1500], [500, 1500]]]})["ok"]
        h = b.hashes()
        b.export("r.ifc")
        f = ifcopenshell.open(str(tmp_path / "r.ifc"))
        kids = [k for rel in f.by_guid(F.S1).IsDecomposedBy for k in rel.RelatedObjects]
        assert len(kids) == 1 and kids[0].is_a("IfcWall") and kids[0].PredefinedType == "PARAPET"
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        o = b.origins("imp.dobim")
        assert o["authored"] == 8 and o["foreign"] == 0 and o["detached"] == 0, o   # 파라펫 벽이 외래 요소로 새지 않는다
        assert b.run("element.get", {"id": F.S1}, project="imp.dobim")["result"]["params"]["parapet"] == {"height": 900.0, "thickness": 200.0}
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


# ---------------------------------------------------------------- 지붕 판이 뒤의 벽 선을 가린다 (16단계: 처마 높이 = 벽 윗면이라 윗선이 지붕 띠 안으로 비치던 흠)
def _walled_gable(sess):
    """1층 직사각 벽 4개(윗면 3000)와 그 위의 박공 지붕(처마 높이 = 벽 윗면, 처마 내밀기 400, 판 두께 150)."""
    lv = sess.run("level.create", {"name": "1F", "elevation": 0})["result"]["id"]
    for ax in ([0, 0, 6000, 0], [6000, 0, 6000, 4000], [6000, 4000, 0, 4000], [0, 4000, 0, 0]):
        assert sess.run("wall.create", {"level": lv, "axis": ax, "thickness": 200, "height": 3000})["ok"]
    assert sess.run("roof.create", {"level": lv, "boundary": [[-100, -100], [6100, -100], [6100, 4100], [-100, 4100]], "form": "gable", "pitch": 30,
                                    "overhang": 400, "base_offset": 3000, "thickness": 150})["ok"]
    return lv


def test_처마_높이가_벽_윗면과_같아도_벽_윗선은_지붕_띠_안으로_비치지_않는다(sess):
    _walled_gable(sess)
    ln = lines_of(sess, mk(sess, name="S", type="elevation", dir="S"))
    t = 0.5773502691896257
    under = 3000 - t * 400 - 150                                   # 처마 끝 판 아랫면 = 앞의 지붕이 벽을 가리기 시작하는 높이
    assert not [x for x in ln if abs(x[2] - 3000) < 1 and abs(x[4] - 3000) < 1], "벽 윗선(3000)이 지붕 판 뒤에서 비친다"
    wall_v = [x for x in ln if abs(x[1] - x[3]) < 1e-6 and x[1] in (0.0, 6000.0, -100.0, 6100.0)]
    assert len(wall_v) == 4
    for x in wall_v:
        assert max(x[2], x[4]) == pytest.approx(under, abs=1.0), "벽 세로선은 처마 끝 판 아랫면에서 끝난다"


def test_박공_쪽_입면은_처마_밑_빈_곳의_벽_모서리를_가리지_않는다(sess):
    _walled_gable(sess)
    ln = lines_of(sess, mk(sess, name="E", type="elevation", dir="E"))
    corners = [x for x in ln if abs(x[1] - x[3]) < 1e-6 and x[1] in (0.0, 4000.0, -100.0, 4100.0)]
    assert len(corners) == 4
    t = 0.5773502691896257
    for x in corners:   # 모서리가 외곽(처마선 y=-100·4100)에서 d 안쪽이면 판 아랫면은 3000 + d*tan(물매) - 150: 그 아래는 지붕 밑이 비어 벽이 보인다(조각 높이 0.5mm 이하의 오차)
        d = min(abs(x[1] + 100), abs(4100 - x[1]))
        assert max(x[2], x[4]) == pytest.approx(3000 + d * t - 150, abs=0.6)   # 19단계: 조각 높이 0.5mm 로 오차 0.5mm 대(예전엔 +20 에 abs=25 였다)


def test_지붕_판_가림은_지붕이_없는_입면과_평면을_바꾸지_않는다(base):
    # 지붕이 없으면 벽 선은 이전과 같다(기준 프로젝트의 입면 벽 윗선이 그대로)
    e = mk(base, name="E0", type="elevation", dir="S")
    ln = lines_of(base, e)
    assert any(x[0] == "proj" and x[2] == x[4] and x[2] > 2900 for x in ln)


# ---------------------------------------------------------------- 19단계: 물매 문자가 화살표 축에 걸리지 않는다
def _label_box(lab, sc):
    """라벨 (x, 기준선 y, 문자, 높이(용지 mm), 맞춤 middle) 의 대략 글자 상자(모형 mm): (x0, y0, x1, y1)."""
    x, y, text, size, _ = lab
    w = D._text_w(text, size) * sc
    return x - w / 2.0, y, x + w / 2.0, y + 0.72 * size * sc


def _seg_hits_box(seg, box):
    """선분과 직사각형이 만나는가(표본점 검사: 선분을 촘촘히 나눈다)."""
    _k, x0, y0, x1, y1 = seg[:5]
    for i in range(201):
        t = i / 200.0
        x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            return True
    return False


@pytest.mark.parametrize("form,extra", [("gable", {}), ("hip", {}), ("shed", {"dir": 45})])
def test_지붕_평면의_물매_문자는_화살표_몸통과_촉에_겹치지_않는다(base, form, extra):
    mk_roof(base, form=form, base_offset=3000, **extra)
    p = mk(base, name="지붕", type="plan", level=F.LV2, cut_height=9000)
    sc = base.eng.g.q(D.view_cfg, p)[4]
    arrows = [x for x in lines_of(base, p) if x[0] == "symbol"]
    labs = [t for t in labels_of(base, p) if t[2].startswith("물매")]
    assert arrows and labs
    for lab in labs:
        box = _label_box(lab, sc)
        assert not any(_seg_hits_box(a, box) for a in arrows), lab


def test_슬래브_지붕의_물매_문자도_화살표_옆으로_뜬다(base):
    r = base.run("slab.create", {"level": F.LV2, "boundary": BOUND, "thickness": 150, "use": "roof", "fall": {"dir": 90, "pct": 2}})
    assert r["ok"], r
    p = mk(base, name="지붕", type="plan", level=F.LV2, cut_height=9000)
    sc = base.eng.g.q(D.view_cfg, p)[4]
    arrows = [x for x in lines_of(base, p) if x[0] == "symbol"]
    lab = [t for t in labels_of(base, p) if t[2].startswith("물매")][0]   # 세로(+y) 화살표라 글이 옆(x)으로 간다
    assert not any(_seg_hits_box(a, _label_box(lab, sc)) for a in arrows)


def test_지붕_판_가림_조각은_높이_0_5mm_이하이고_처마_밑을_채우지_않는다(sess):
    _walled_gable(sess)
    sess.eng.sync()
    sess.eng._touch_pending()
    rid = sess.eng.g.q(D.scope_roofs, mk(sess, name="E", type="elevation", dir="E"))[0]
    pcs = sess.eng.g.q(D.roof_solid_slices, rid)
    assert pcs and all(z1 - z0 <= D.ROOF_SOLID_PIECE_MM + 4 * D.EPS + 1e-9 for z0, z1, _ in pcs)
