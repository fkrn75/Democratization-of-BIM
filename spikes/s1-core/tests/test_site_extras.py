# SPDX-License-Identifier: MPL-2.0
"""대지의 선택 속성(14절): 지반 표고점·조경/공개공지 영역·주차 계획·간판, 대지 단면 뷰(section 의 with_site).
배치도의 영역 해치·면적·주차면·동선, 입면의 간판 사각형, 대지 단면의 지반선·GL·대지 경계, 증분 == 전체, IFC 왕복(해시 3종), 입력이 없으면 기존 도면 불변."""
import sys


from conftest import ROOT
from dobim_core import globalid as GI   # 내보낸 IFC 를 원래 요소 id 로 연다(W2: 첫 글자가 무효한 id 는 파일에서 바뀐다)
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

LOT = [[-6000, -6000], [18000, -6000], [18000, 14000], [-6000, 14000]]
ZONES = [{"use": "landscape", "name": "전정", "boundary": [[-5000, 6000], [-1000, 6000], [-1000, 10000], [-5000, 10000]]},
         {"use": "open_space", "name": "", "boundary": [[8000, 6000], [14000, 6000], [14000, 12000], [8000, 12000]]}]
PARKING = {"bays": [{"origin": [8000, -4000], "angle": 0, "count": 4, "w": 2500, "d": 5000},
                    {"origin": [8000, -4000 + 5000 + 6000], "angle": 0, "count": 1, "w": 3500, "d": 5000, "accessible": True}],
           "aisles": [{"path": [[8000, 1500], [18000, 1500]], "width": 6000}],
           "routes": [{"path": [[17000, -6000], [17000, 1500], [9000, 1500]]}]}
SIGNS = [{"name": "S1", "kind": "sign", "face": "S", "center": [3000, 0], "z": 2200, "w": 2400, "h": 600, "text": "카페"},
         {"name": "N1", "kind": "address", "face": "N", "center": [3000, 4000], "z": 1500, "w": 300, "h": 200, "text": "12"}]
TERRAIN = [[-6000, -6000, -600], [18000, -6000, -600], [18000, 14000, 400], [-6000, 14000, 400], [3000, 2000, 0]]
V_SITE = "4" + "0" * 20 + "9"
V_SEC = "4" + "0" * 20 + "8"


def mk_site(sess, **kw):
    r = sess.run("site.create", {"id": F.SI1, "boundary": LOT, "name": "대지", **kw})
    assert r["ok"], r
    return F.SI1


def mk(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def refresh(sess):
    sess.eng.sync()
    sess.eng._touch_pending()


def data_of(sess, vid):
    refresh(sess)
    return {"lines": sess.eng.g.q(D.view_lines, vid), "labels": sess.eng.g.q(D.view_labels, vid)}


def texts(d):
    return [x[2] for x in d["labels"]]


# ---------------------------------------------------------------- 검사
def test_대지_선택_속성은_저장_모양으로_정규화된다(base):
    mk_site(base, zones=[{"use": "landscape", "name": "시계", "boundary": ZONES[0]["boundary"][::-1]}], parking=PARKING, signs=SIGNS, terrain=TERRAIN)
    p = base.run("element.get", {"id": F.SI1})["result"]["params"]
    b = p["zones"][0]["boundary"]
    assert sum(b[i][0] * b[(i + 1) % 4][1] - b[(i + 1) % 4][0] * b[i][1] for i in range(4)) > 0     # 반시계로 저장
    assert p["parking"]["bays"][0]["count"] == 4 and "accessible" not in p["parking"]["bays"][0] and p["parking"]["bays"][1]["accessible"] is True
    assert all(isinstance(v, float) for q in p["terrain"] for v in q)
    assert K.row_params_error("site", p) is None


def test_대지_밖_영역과_주차_시작점과_잘못된_값은_거절한다(base):
    bad = [{"use": "landscape", "name": "x", "boundary": [[17000, 0], [19000, 0], [19000, 2000], [17000, 2000]]}]
    r = base.run("site.create", {"id": F.SI1, "boundary": LOT, "zones": bad})
    assert not r["ok"] and "대지 경계 밖" in r["error"]["message"]
    r = base.run("site.create", {"id": F.SI1, "boundary": LOT, "parking": {"bays": [{"origin": [30000, 0], "count": 2}]}})
    assert not r["ok"] and "경계 밖" in r["error"]["message"]
    assert not base.run("site.create", {"id": F.SI1, "boundary": LOT, "parking": {}})["ok"]                              # 빈 계획
    assert not base.run("site.create", {"id": F.SI1, "boundary": LOT, "signs": [dict(SIGNS[0], face="X")]})["ok"]       # 방위가 아님
    assert not base.run("site.create", {"id": F.SI1, "boundary": LOT, "signs": [dict(SIGNS[0], w=0)]})["ok"]
    assert not base.run("site.create", {"id": F.SI1, "boundary": LOT, "terrain": [[1, 2]]})["ok"]
    assert not base.run("site.create", {"id": F.SI1, "boundary": LOT, "parking": {"bays": [{"origin": [0, 0], "count": 0}]}})["ok"]


def test_site_set_은_통째로_바꾸고_clear_로_지운다(base):
    mk_site(base)
    assert base.run("site.set", {"id": F.SI1, "zones": ZONES, "signs": SIGNS})["ok"]
    p = base.run("element.get", {"id": F.SI1})["result"]["params"]
    assert len(p["zones"]) == 2 and len(p["signs"]) == 2
    assert not base.run("site.set", {"id": F.SI1, "zones": ZONES, "clear": ["zones"]})["ok"]            # 지우면서 주면 모순
    assert base.run("site.set", {"id": F.SI1, "clear": ["zones", "signs"]})["ok"]
    assert set(base.run("element.get", {"id": F.SI1})["result"]["params"]) == {"name", "boundary", "north_angle", "roads", "elevation"}
    # 경계를 줄여 영역이 밖으로 나가면 거절한다
    assert base.run("site.set", {"id": F.SI1, "zones": ZONES})["ok"]
    r = base.run("site.set", {"id": F.SI1, "boundary": [[-6000, -6000], [0, -6000], [0, 0], [-6000, 0]]})
    assert not r["ok"]


def test_파생값은_선택_속성이_있을_때만_더해진다(base):
    mk_site(base)
    assert set(base.run("element.get", {"id": F.SI1})["result"]["derived"]) == {"area_m2", "roads"}
    assert base.run("site.set", {"id": F.SI1, "zones": ZONES, "parking": PARKING, "signs": SIGNS, "terrain": TERRAIN})["ok"]
    d = base.run("element.get", {"id": F.SI1})["result"]["derived"]
    assert d["landscape_m2"] == 16.0 and d["open_space_m2"] == 36.0 and d["parking_bays"] == 5 and d["parking_accessible"] == 1 and d["signs"] == 2 and d["terrain_points"] == 5


# ---------------------------------------------------------------- P-06 조경·공개공지 / P-05 주차
def test_배치도에_영역_해치와_면적_합계_문자를_그린다(base):
    mk_site(base, zones=ZONES)
    d = data_of(base, mk(base, name="배치도", type="site", scale=200))
    t = texts(d)
    assert "전정 16.00㎡" in t and "공개공지 36.00㎡" in t                      # 영역 이름이 없으면 용도 이름
    assert "조경 면적 합계 16.00㎡(대지면적의 3.3%)" in t and "공개공지 면적 합계 36.00㎡(대지면적의 7.5%)" in t
    hatch = [ln for ln in d["lines"] if ln[0] == "hatch"]
    assert hatch
    for _c, x0, y0, x1, y1 in hatch:                                           # 모든 해치 선은 어느 영역 안쪽에 있다
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        assert any(z["boundary"][0][0] - 1 <= mx <= z["boundary"][2][0] + 1 and z["boundary"][0][1] - 1 <= my <= z["boundary"][2][1] + 1 for z in ZONES)
    land = [ln for ln in hatch if ln[1] < 0]
    assert all(abs((ln[4] - ln[2]) - (ln[3] - ln[1])) < 1e-6 for ln in land)     # 조경 = 45도(기울기 +1)
    ope = [ln for ln in hatch if ln[1] > 0]
    assert all(abs((ln[4] - ln[2]) + (ln[3] - ln[1])) < 1e-6 for ln in ope)      # 공개공지 = 135도(기울기 -1)
    # 간격 3.5mm x 축척 200 = 700mm 로 영역 폭 4000mm 에서 선이 5~7개
    assert 5 <= len(land) <= 8


def test_배치도에_주차면_차로_동선과_대수를_그린다(base):
    mk_site(base, parking=PARKING)
    d = data_of(base, mk(base, name="배치도", type="site", scale=200))
    t = texts(d)
    assert "주차 4대" in t and "장애인 1대" in t and "주차 합계 5대(장애인 전용 1대)" in t
    proj = {(round(a, 1), round(b, 1), round(c, 1), round(e, 1)) for k, a, b, c, e in d["lines"] if k == "proj"}
    assert (8000.0, -4000.0, 18000.0, -4000.0) in proj and (8000.0, 1000.0, 18000.0, 1000.0) in proj        # 4칸(폭 2500)이 이어진 아래·위 변(깊이 5000), 선은 합쳐진다
    assert (8000.0, -4000.0, 8000.0, 1000.0) in proj and (8000.0, 7000.0, 11500.0, 7000.0) in proj          # 장애인 칸(폭 3500)
    assert (8000.0, 4500.0, 18000.0, 4500.0) in proj and (8000.0, -1500.0, 18000.0, -1500.0) in proj        # 차로 폭 6000 의 양쪽 선
    sym = [ln for ln in d["lines"] if ln[0] == "symbol" and ln[1] != ln[3] or ln[0] == "symbol" and ln[2] != ln[4]]
    assert any(abs(ln[1] - 17000.0) < 1e-6 and abs(ln[3] - 17000.0) < 1e-6 for ln in sym)                  # 동선 첫 구간(세로)
    head = [ln for ln in sym if abs(ln[1] - 9000.0) < 1e-6 and abs(ln[2] - 1500.0) < 1e-6 and 9000.0 < ln[3] < 10000.0]   # 끝점(9000, 1500)에서 뒤로 뻗는 화살표 머리 둘
    assert len(head) == 2 and abs(head[0][4] + head[1][4] - 3000.0) < 1e-6


def test_주차면_배열은_각도를_따라_회전한다(base):
    mk_site(base, parking={"bays": [{"origin": [0, 0], "angle": 90, "count": 2, "w": 2500, "d": 5000}]})
    refresh(base)
    bays = D.parking_bays(base.eng.st.el[F.SI1]["params"]["parking"])
    assert len(bays) == 2
    (p0, p1, p2, p3) = bays[0][2]
    assert abs(p1[0]) < 1e-6 and abs(p1[1] - 2500) < 1e-6 and abs(p3[0] + 5000) < 1e-6         # 배열은 +y 로, 깊이는 그 왼쪽(-x)
    assert D.parking_counts(base.eng.st.el[F.SI1]["params"]["parking"]) == (2, 0)


# ---------------------------------------------------------------- E-03 간판
def test_입면에_간판_사각형과_크기_문자를_그린다(base):
    mk_site(base, signs=SIGNS)
    ds = data_of(base, mk(base, name="남측 입면", type="elevation", dir="S", scale=50))
    dn = data_of(base, mk(base, name="북측 입면", type="elevation", dir="N", scale=50))
    de = data_of(base, mk(base, name="동측 입면", type="elevation", dir="E", scale=50))
    assert "간판 S1 2400x600" in texts(ds) and "카페" in texts(ds) and not any("N1" in x for x in texts(ds))
    assert "건물번호판 N1 300x200" in texts(dn) and "12" in texts(dn) and not any("S1" in x for x in texts(dn))
    assert not any("S1" in x or "N1" in x for x in texts(de))                                    # 다른 면 입면에는 없다
    # 남측 입면은 바깥(남쪽)에서 북쪽을 본다: 가로축 s 는 +x 쪽이 오른쪽이 아니라 방향 규약을 따른다. 폭 2400, 높이 600, 아래 2200
    sym = [ln for ln in ds["lines"] if ln[0] == "symbol" and abs(ln[2] - 2200.0) < 1e-6 and abs(ln[4] - 2200.0) < 1e-6 and abs(abs(ln[3] - ln[1]) - 2400.0) < 1e-6]
    assert len(sym) == 1
    top = [ln for ln in ds["lines"] if ln[0] == "symbol" and abs(ln[2] - 2800.0) < 1e-6 and abs(ln[4] - 2800.0) < 1e-6]
    assert len(top) == 1


# ---------------------------------------------------------------- P-03 대지 단면
def test_대지_단면은_지반선_GL_대지_경계를_그린다(base):
    mk_site(base, terrain=TERRAIN, elevation=12500)
    v = mk(base, name="대지 단면", type="section", p1=[3000, -8000], p2=[3000, 16000], with_site=True, scale=200)
    d = data_of(base, v)
    cut_ground = [ln for ln in d["lines"] if ln[0] == "cut" and abs(ln[1] - ln[3]) > 1e-6 and ln[2] < 1000 and ln[4] < 1000 and ln[2] > -1000 and ln[4] > -1000]
    assert cut_ground
    refresh(base)
    pts = base.eng.st.el[F.SI1]["params"]["terrain"]
    assert D.terrain_z(pts, 3000.0, 2000.0) == 0.0                                         # 표고점 위에서는 그 값
    zs = [D.terrain_z(pts, 3000.0, y) for y in (-6000.0, 14000.0)]
    assert zs[0] < 0 < zs[1] or zs[0] < zs[1]
    t = texts(d)
    assert t.count("대지 경계") == 2 and any(x.startswith("GL ") and "EL +" in x for x in t)
    syms = [ln for ln in d["lines"] if ln[0] == "symbol" and abs(ln[1] - ln[3]) < 1e-6 and abs(ln[4] - ln[2]) == 4500.0]
    assert len(syms) == 2                                                                  # 경계 두 곳의 세로 표시(아래 1500 + 위 3000)
    assert [ln for ln in d["lines"] if ln[0] == "hatch"]                                   # 지반 아래 사선 눈금
    # 일반 단면(with_site 없음)에는 지반선도 문자도 없다
    v2 = mk(base, name="일반 단면", type="section", p1=[3000, -8000], p2=[3000, 16000], scale=200)
    d2 = data_of(base, v2)
    assert "대지 경계" not in texts(d2) and not any(ln[0] == "hatch" for ln in d2["lines"])


def test_대지_단면은_절단선이_대지를_일부만_지나면_구간만_그린다(base):
    mk_site(base, terrain=[[0, 0, 100]])
    v = mk(base, name="대지 단면", type="section", p1=[3000, 0], p2=[3000, 20000], with_site=True)     # 절단선 시작은 대지 안, 끝은 밖
    d = data_of(base, v)
    assert texts(d).count("대지 경계") == 1


def test_표고점이_없으면_지반은_모델_0_높이_수평선이다(base):
    mk_site(base)
    v = mk(base, name="대지 단면", type="section", p1=[3000, -8000], p2=[3000, 16000], with_site=True)
    d = data_of(base, v)
    ground = [ln for ln in d["lines"] if ln[0] == "cut" and abs(ln[2]) < 1e-9 and abs(ln[4]) < 1e-9]
    assert ground and "GL +0" in texts(d)


def test_with_site_는_뷰_설정으로_켜고_끈다(base):
    v = mk(base, name="단면", type="section", p1=[3000, -8000], p2=[3000, 16000])
    assert "with_site" not in base.run("element.get", {"id": v})["result"]["params"]
    assert base.run("view.set", {"id": v, "with_site": True})["ok"]
    assert base.run("element.get", {"id": v})["result"]["params"]["with_site"] is True
    assert base.run("view.set", {"id": v, "with_site": False})["ok"]
    assert "with_site" not in base.run("element.get", {"id": v})["result"]["params"]
    assert not base.run("view.create", {"name": "입면", "type": "elevation", "dir": "S", "with_site": True})["ok"]   # 단면 뷰 전용


# ---------------------------------------------------------------- 불변·증분·왕복
def test_선택_속성이_없으면_뷰와_해시가_그대로다(base):
    mk_site(base)
    vs, ve, vc = (mk(base, name="배치도", type="site", scale=200), mk(base, name="남", type="elevation", dir="S"),
                  mk(base, name="단면", type="section", p1=[3000, -8000], p2=[3000, 16000]))
    before = [data_of(base, v) for v in (vs, ve, vc)]
    h0 = base.run("model.hash", {})["result"]
    assert base.run("site.set", {"id": F.SI1, "zones": ZONES, "parking": PARKING, "signs": SIGNS, "terrain": TERRAIN})["ok"]
    assert base.run("model.hash", {})["result"]["state"] != h0["state"]
    assert base.run("site.set", {"id": F.SI1, "clear": ["zones", "parking", "signs", "terrain"]})["ok"]
    assert [data_of(base, v) for v in (vs, ve, vc)] == before
    h1 = base.run("model.hash", {})["result"]
    assert {k: h1[k] for k in ("state", "derived", "ifc")} == {k: h0[k] for k in ("state", "derived", "ifc")}     # 되돌리면 해시 3종이 처음과 같다


def test_선택_속성_뷰_증분이_전체_재생성과_같다(base):
    vs = mk(base, name="배치도", type="site", scale=200)
    ve = mk(base, name="남", type="elevation", dir="S", scale=50)
    vc = mk(base, name="대지 단면", type="section", p1=[3000, -8000], p2=[3000, 16000], with_site=True, scale=200)

    def check():
        refresh(base)
        ref = s3lib.RefGraph(s3lib._Inputs(base.eng))
        for v, fns in ((vs, (D.view_lines, D.view_labels)), (ve, (D.view_lines, D.view_labels)), (vc, (D.view_lines, D.view_labels))):
            for fn in fns:
                assert base.eng.g.q(fn, v) == ref.q(fn, v), (fn.__name__, v)

    check()
    mk_site(base)
    check()
    assert base.run("site.set", {"id": F.SI1, "zones": ZONES, "parking": PARKING, "signs": SIGNS, "terrain": TERRAIN})["ok"]
    check()
    assert base.run("site.set", {"id": F.SI1, "terrain": [[0, 0, 500]], "signs": SIGNS[:1]})["ok"]
    check()
    assert base.run("wall.move", {"id": F.W1, "dx": 0, "dy": 500})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("site.set", {"id": F.SI1, "clear": ["zones"]})["ok"]
    check()
    assert base.run("view.set", {"id": vc, "with_site": False})["ok"]
    check()
    assert base.run("site.delete", {"id": F.SI1})["ok"]
    check()


def test_선택_속성과_대지_단면은_IFC로_왕복한다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("site.create", {"id": F.SI1, "boundary": LOT, "name": "대지", "zones": ZONES, "parking": PARKING, "signs": SIGNS, "terrain": TERRAIN, "elevation": 100})["ok"]
        assert b.run("view.create", {"id": V_SEC, "name": "대지 단면", "type": "section", "p1": [3000, -8000], "p2": [3000, 16000], "with_site": True})["ok"]
        h = b.hashes()
        before = b.run("element.get", {"id": F.SI1})["result"]["params"]
        b.export("s.ifc")
        f = GI.open_exported(str(tmp_path / "s.ifc"))
        assert f.by_guid(F.SI1).Name == "대지"
        r = b.import_ifc("s.ifc", "imp.dobim")
        assert r["ok"], r
        assert not r["result"]["warnings"]
        assert b.run("element.get", {"id": F.SI1}, project="imp.dobim")["result"]["params"] == before
        assert b.run("element.get", {"id": F.SI1}, project="imp.dobim")["result"]["origin"] == "authored"
        assert b.run("element.get", {"id": V_SEC}, project="imp.dobim")["result"]["params"]["with_site"] is True
        hi = b.hashes("imp.dobim")
        assert (hi["state"], hi["derived"], hi["ifc"]) == (h["state"], h["derived"], h["ifc"])
    finally:
        b.close()


def test_해시_형식은_12_이상이다():
    from dobim_core import ifc_model
    assert int(ifc_model.CLOSURE_FORMAT) >= 12     # 12 에서 대지 선택 속성이 생겼다(13: 뷰의 선택 옵션)


def test_배치도에_지반_표고점_높이_문자를_그린다(base):
    mk_site(base, terrain=TERRAIN)
    d = data_of(base, mk(base, name="배치도", type="site", scale=200))
    t = texts(d)
    assert t.count("지반 -600") == 2 and "지반 +400" in t and "지반 +0" in t
    cross = [ln for ln in d["lines"] if ln[0] == "symbol" and abs(ln[1] - 3000.0) < 1e-6 and abs(ln[3] - 3000.0) < 1e-6 and abs(ln[2] - 2000.0) < 300]
    assert cross                                                                             # (3000, 2000) 점의 십자(세로 선)
