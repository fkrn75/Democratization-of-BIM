# SPDX-License-Identifier: MPL-2.0
"""부위 상세 뷰(15절): 뷰의 선택 파라미터 crop(잘라내기)·dims(계단·승강기 치수 자동 표기)·finish(입면 외부 마감),
view.create 의 around(요소 범위로 crop 정하기), 시트 방위 기본값(대지의 정북 각). 옵션이 없으면 기존 도면 불변, 증분 == 전체 재계산, IFC 왕복(해시 3종)."""
import sys

import ifcopenshell

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

RUN = [1000, 2000, 5400, 2000]      # 걸음선 4400mm, 오름 3300, 챌판 19(디딤판 18 x 244.44)
V_PLAN = "4" + "0" * 20 + "7"
V_SEC = "4" + "0" * 20 + "8"


def mk(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]


def stair(sess, **kw):
    r = sess.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": RUN, "width": 1200, "height": 3300, "risers": 19, **kw})
    assert r["ok"], r
    return F.ST1


def elev(sess, **kw):
    r = sess.run("elevator.create", {"id": F.EL1, "level": F.LV1, "shaft": [3000, 500, 4800, 2300], "rise": 6000, **kw})
    assert r["ok"], r
    return F.EL1


def refresh(sess):
    sess.eng.sync()
    sess.eng._touch_pending()


def data_of(sess, vid):
    refresh(sess)
    g = sess.eng.g
    return {"lines": g.q(D.view_lines, vid), "fills": g.q(D.view_fills, vid), "labels": g.q(D.view_labels, vid), "detail": g.q(D.view_detail_lines, vid),
            "dims": g.q(D.view_dims, vid)}


def dim_texts(d):
    return [x[5] for x in d["dims"]]


# ---------------------------------------------------------------- 검사·저장 모양
def test_옵션은_있을_때만_키가_생기고_종류에_맞아야_한다(base):
    v = mk(base, name="평면", type="plan", level=F.LV1)["id"]
    assert not {"crop", "dims", "finish"} & set(base.run("element.get", {"id": v})["result"]["params"])
    v2 = mk(base, name="평면 상세", type="plan", level=F.LV1, scale=20, crop=[0, 0, 3000, 3000], dims=True)["id"]
    p = base.run("element.get", {"id": v2})["result"]["params"]
    assert p["crop"] == [0.0, 0.0, 3000.0, 3000.0] and p["dims"] is True and K.row_params_error("view", p) is None
    assert "finish" not in p
    assert not base.run("view.create", {"name": "입면 d", "type": "elevation", "dir": "S", "dims": True})["ok"]        # dims 는 평면·단면
    assert not base.run("view.create", {"name": "평면 f", "type": "plan", "level": F.LV1, "finish": True})["ok"]       # finish 는 입면
    assert not base.run("view.create", {"name": "배치도 c", "type": "site", "crop": [0, 0, 1, 1]})["ok"]
    assert not base.run("view.create", {"name": "평면 c", "type": "plan", "level": F.LV1, "crop": [5, 0, 1, 1]})["ok"]  # x1 < x2 여야 한다
    assert not base.run("view.create", {"name": "평면 a", "type": "plan", "level": F.LV1, "crop": [0, 0, 1, 1], "around": F.ST1})["ok"]
    assert not base.run("view.create", {"name": "배치도 a", "type": "site", "around": F.ST1})["ok"]
    assert base.run("view.create", {"name": "입면 f", "type": "elevation", "dir": "S", "finish": True, "crop": [0, 0, 100, 100]})["ok"]


def test_view_set_으로_켜고_끈다(base):
    v = mk(base, name="평면", type="plan", level=F.LV1)["id"]
    h0 = base.run("model.hash", {})["result"]
    assert base.run("view.set", {"id": v, "dims": True, "crop": [0, 0, 2000, 2000]})["ok"]
    p = base.run("element.get", {"id": v})["result"]["params"]
    assert p["dims"] is True and p["crop"] == [0.0, 0.0, 2000.0, 2000.0]
    assert not base.run("view.set", {"id": v, "finish": True})["ok"]                       # 평면에는 finish 가 없다
    assert not base.run("view.set", {"id": v, "crop": [3, 3, 1, 1]})["ok"]
    assert base.run("view.set", {"id": v, "dims": False, "clear_crop": True})["ok"]
    assert not {"crop", "dims"} & set(base.run("element.get", {"id": v})["result"]["params"])
    h1 = base.run("model.hash", {})["result"]
    assert {k: h1[k] for k in ("state", "derived", "ifc")} == {k: h0[k] for k in ("state", "derived", "ifc")}     # 되돌리면 해시 3종이 처음과 같다


def test_around_는_요소_범위에_여유를_더한_crop_을_저장한다(base):
    stair(base)
    r = mk(base, name="계단 평면", type="plan", level=F.LV1, around=F.ST1, margin=500, scale=20)
    assert r["crop"] == [1000 - 500 - 0.0, 2000 - 600 - 500.0, 5400 + 500.0, 2000 + 600 + 500.0]
    p = base.run("element.get", {"id": r["id"]})["result"]["params"]
    assert p["crop"] == r["crop"] and "around" not in p and "margin" not in p                 # 요소 참조는 저장하지 않는다
    rs = mk(base, name="계단 단면", type="section", p1=[0, 2000], p2=[7000, 2000], around=F.ST1, margin=300)
    assert rs["crop"][0] == 1000 - 300 and rs["crop"][2] == 5400 + 300                        # 가로 s = x - 0
    assert rs["crop"][1] < 0 < rs["crop"][3]                                                  # 높이: 계단 아래~위(3300) 둘레
    assert not base.run("view.create", {"name": "x", "type": "plan", "level": F.LV1, "around": F.W1})["ok"]   # 계단·승강기만
    p0 = base.run("element.get", {"id": r["id"]})["result"]["params"]["crop"]
    assert base.run("stair.move", {"id": F.ST1, "dx": 1000, "dy": 0})["ok"]
    assert base.run("element.get", {"id": r["id"]})["result"]["params"]["crop"] == p0         # 이후 계단을 옮겨도 crop 은 그대로


# ---------------------------------------------------------------- 잘라내기
def test_crop_은_선과_채움을_사각형으로_자른다(base):
    stair(base)
    full = mk(base, name="평면", type="plan", level=F.LV1, scale=20)["id"]
    crop = [900.0, 1000.0, 3000.0, 3000.0]
    cut = mk(base, name="평면 상세", type="plan", level=F.LV1, scale=20, crop=crop)["id"]
    a, b = data_of(base, full), data_of(base, cut)
    assert 0 < len(b["lines"]) < len(a["lines"])
    for ln in b["lines"]:
        assert crop[0] - 1e-6 <= min(ln[1], ln[3]) and max(ln[1], ln[3]) <= crop[2] + 1e-6 and crop[1] - 1e-6 <= min(ln[2], ln[4]) and max(ln[2], ln[4]) <= crop[3] + 1e-6
    for poly in b["fills"]:
        assert all(crop[0] - 1e-6 <= p[0] <= crop[2] + 1e-6 and crop[1] - 1e-6 <= p[1] <= crop[3] + 1e-6 for p in poly)
    # 사각형 안에 통째로 든 선분은 그대로 남는다
    inside = [ln for ln in a["lines"] if crop[0] <= min(ln[1], ln[3]) and max(ln[1], ln[3]) <= crop[2] and crop[1] <= min(ln[2], ln[4]) and max(ln[2], ln[4]) <= crop[3]]
    assert inside and all(ln in b["lines"] for ln in inside)
    # 사각형 밖 문자는 빠진다: 전체 뷰에 있는 방 문자가 없다
    assert all(crop[0] <= t[0] <= crop[2] and crop[1] <= t[1] <= crop[3] for t in b["labels"])


def test_crop_은_단면의_채움과_상세선도_자른다(base):
    assert base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200})["ok"]
    crop = [500.0, -500.0, 2500.0, 3000.0]
    v = mk(base, name="단면", type="section", p1=[-500, 2000], p2=[7000, 2000], scale=20, crop=crop)["id"]
    d = data_of(base, v)
    assert d["lines"] and d["fills"]
    for poly in d["fills"]:
        assert all(crop[0] - 1e-6 <= p[0] <= crop[2] + 1e-6 and crop[1] - 1e-6 <= p[1] <= crop[3] + 1e-6 for p in poly)


# ---------------------------------------------------------------- 치수 자동 표기
def test_계단_평면_dims_는_폭_디딤판_길이_챌판_문자를_단다(base):
    stair(base)
    v = mk(base, name="계단 평면", type="plan", level=F.LV1, scale=20, around=F.ST1, dims=True)["id"]
    d = data_of(base, v)
    assert "244.4x18=4400" in dim_texts(d) and "1200" in dim_texts(d)
    assert any(t[2] == "19단 챌판 173.7 디딤 244.4" for t in d["labels"])
    ids = [x[1] for x in d["dims"]]
    assert len(set(ids)) == len(ids) and all(i.startswith("detail:") for i in ids)
    tread = next(x for x in d["dims"] if x[5].startswith("244.4x18"))
    assert abs(((tread[4][2] - tread[4][0]) ** 2 + (tread[4][3] - tread[4][1]) ** 2) ** 0.5 - 4400) < 1e-6      # 치수선 길이 = 걸음선 길이
    assert mk(base, name="계단 평면 옵션 없음", type="plan", level=F.LV1, scale=20) and data_of(base, base.run("view.list", {})["result"]["items"][-1]["id"])["dims"] == ()


def test_꺾인_계단_평면은_층참_치수를_더한다(base):
    r = base.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 3500, 1000], "width": 1200, "height": 3300, "risers": 19,
                                  "turn": {"type": "L", "risers_a": 9, "landing": 1400, "side": "left"}})
    assert r["ok"], r
    v = mk(base, name="꺾인 계단", type="plan", level=F.LV1, scale=20, dims=True, cut_height=3300)["id"]
    d = data_of(base, v)
    assert sum(1 for t in dim_texts(d) if t.startswith("층참 ")) == 2
    assert sum(1 for x in d["dims"] if ":tread:" in x[1]) == 2                                                  # 단 묶음마다 디딤 사슬


def test_계단_단면_dims_는_챌판_디딤판_오름_난간_높이를_단다(base):
    stair(base)
    assert base.run("railing.create", {"id": F.RL1, "level": F.LV1, "path": [[1000, 2600], [5400, 2600]], "height": 1100, "rise": 3300})["ok"]
    v = mk(base, name="계단 단면", type="section", p1=[0, 2000], p2=[7000, 2000], scale=20, around=F.ST1, dims=True)["id"]
    d = data_of(base, v)
    t = dim_texts(d)
    assert {"244.4x18=4400", "173.7x19=3300", "173.7", "244.4", "난간 1100"} <= set(t)
    rise = next(x for x in d["dims"] if x[5].startswith("173.7x19"))
    assert abs(abs(rise[4][3] - rise[4][1]) - 3300) < 1e-6                                                         # 오름 치수선 높이 = 3300
    # 걸음선과 나란하지 않은 단면(걸음선에 수직)은 계단 치수를 달지 않는다
    v2 = mk(base, name="직각 단면", type="section", p1=[3000, -1000], p2=[3000, 6000], scale=20, dims=True)["id"]
    assert not [x for x in data_of(base, v2)["dims"] if ":run:" in x[1] or ":riser:" in x[1]]


def test_승강기_평면_dims_는_샤프트_카_치수를_단다(base):
    elev(base, cab=[1100, 1400, 2200])
    v = mk(base, name="EV 평면", type="plan", level=F.LV1, scale=50, around=F.EL1, dims=True)["id"]
    d = data_of(base, v)
    assert sorted(dim_texts(d)) == ["샤프트 1800", "샤프트 1800", "카 1100", "카 1400"]
    cab = next(x for x in d["dims"] if x[5] == "카 1100")
    assert abs(abs(cab[4][2] - cab[4][0]) - 1100) < 1e-6 and cab[4][1] == cab[4][3]
    # 승강기가 놓인 모든 층의 평면에 단다(옵션을 켠 뷰에만)
    v2 = mk(base, name="2F EV 평면", type="plan", level=F.LV2, scale=50, dims=True)["id"]
    assert len(data_of(base, v2)["dims"]) == 4
    assert data_of(base, mk(base, name="옵션 없는 평면", type="plan", level=F.LV1, scale=50)["id"])["dims"] == ()


def test_승강기_단면_dims_는_카_오버헤드_승강로_피트를_단다(base):
    elev(base, cab=[1100, 1400, 2200], rise=4800, base_offset=-600)
    v = mk(base, name="EV 단면", type="section", p1=[2000, 1400], p2=[6000, 1400], scale=50, around=F.EL1, dims=True)["id"]
    t = dim_texts(data_of(base, v))
    assert {"카 2200", "OH 2600", "승강로 4800", "피트 600"} <= set(t)
    elev_dims = [x for x in data_of(base, v)["dims"]]
    shaft = next(x for x in elev_dims if x[5].startswith("승강로"))
    assert abs(abs(shaft[4][3] - shaft[4][1]) - 4800) < 1e-6
    base.run("elevator.set", {"id": F.EL1, "base_offset": 0})
    assert not [x for x in data_of(base, v)["dims"] if x[5].startswith("피트")]              # 피트(카 바닥이 정차층 아래) 없으면 달지 않는다


def test_crop_밖_요소는_치수를_달지_않는다(base):
    stair(base, run=[500, 3000, 2400, 3000], height=1000, risers=6)     # 계단은 crop 밖(왼쪽 위), 승강기는 안
    elev(base)
    v = mk(base, name="EV 만", type="plan", level=F.LV1, scale=50, crop=[2500, 0, 5500, 2400], dims=True)["id"]
    t = dim_texts(data_of(base, v))
    assert t and all(x.startswith(("샤프트", "카")) for x in t)


# ---------------------------------------------------------------- 입면 외부 마감 옵션
def test_입면_외부_마감_해치는_finish_옵션을_켠_뷰에만_그린다(base):
    assert base.run("pset.set", {"id": F.W1, "pset": "Pset_WallCommon", "prop": "IsExternal", "value": True})["ok"]
    assert base.run("material.set", {"id": F.W1, "name": "치장벽돌"})["ok"]
    plain = mk(base, name="입면", type="elevation", dir="S")["id"]
    fin = mk(base, name="입면 마감", type="elevation", dir="S", finish=True)["id"]
    a, b = data_of(base, plain), data_of(base, fin)
    assert not [ln for ln in a["lines"] if ln[0] == "hatch"] and "치장벽돌" not in [t[2] for t in a["labels"]]
    assert [ln for ln in b["lines"] if ln[0] == "hatch"] and "치장벽돌" in [t[2] for t in b["labels"]]
    assert base.run("view.set", {"id": fin, "finish": False})["ok"]
    assert data_of(base, fin)["lines"] == a["lines"]


# ---------------------------------------------------------------- 증분 == 전체, 불변, 왕복
def test_옵션_뷰_증분이_전체_재생성과_같다(base):
    stair(base)
    elev(base)
    vs = [mk(base, name="계단 평면", type="plan", level=F.LV1, scale=20, around=F.ST1, dims=True)["id"],
          mk(base, name="계단 단면", type="section", p1=[0, 2000], p2=[7000, 2000], scale=20, around=F.ST1, dims=True)["id"],
          mk(base, name="EV 평면", type="plan", level=F.LV1, scale=50, around=F.EL1, dims=True)["id"],
          mk(base, name="입면", type="elevation", dir="S", finish=True, crop=[0, 0, 3000, 3000])["id"]]

    def check():
        refresh(base)
        ref = s3lib.RefGraph(s3lib._Inputs(base.eng))
        for v in vs:
            for fn in (D.view_lines, D.view_labels, D.view_fills, D.view_dims, D.view_detail_lines, D.view_hidden_lines):
                assert base.eng.g.q(fn, v) == ref.q(fn, v), (fn.__name__, v)

    check()
    assert base.run("stair.set", {"id": F.ST1, "width": 1000})["ok"]
    check()
    assert base.run("elevator.move", {"id": F.EL1, "dx": 200, "dy": 0})["ok"]
    check()
    assert base.run("view.set", {"id": vs[0], "dims": False})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("stair.delete", {"id": F.ST1})["ok"]
    check()


def test_옵션을_켰다_끄면_뷰와_해시가_그대로다(base):
    stair(base)
    elev(base)
    v = mk(base, name="평면", type="plan", level=F.LV1, scale=50)["id"]
    before = data_of(base, v)
    h0 = base.run("model.hash", {})["result"]
    assert base.run("view.set", {"id": v, "dims": True, "crop": [0, 0, 3000, 3000]})["ok"]
    assert data_of(base, v) != before
    assert base.run("view.set", {"id": v, "dims": False, "clear_crop": True})["ok"]
    assert data_of(base, v) == before
    h1 = base.run("model.hash", {})["result"]
    assert {k: h1[k] for k in ("state", "derived", "ifc")} == {k: h0[k] for k in ("state", "derived", "ifc")}


def test_옵션_뷰는_IFC로_왕복한다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": RUN, "width": 1200, "height": 3300, "risers": 19})["ok"]
        assert b.run("view.create", {"id": V_PLAN, "name": "계단 평면", "type": "plan", "level": F.LV1, "scale": 20, "around": F.ST1, "dims": True})["ok"]
        assert b.run("view.create", {"id": V_SEC, "name": "입면 마감", "type": "elevation", "dir": "S", "finish": True, "crop": [0, 0, 6000, 3300]})["ok"]
        h = b.hashes()
        before = {v: b.run("element.get", {"id": v})["result"]["params"] for v in (V_PLAN, V_SEC)}
        b.export("d.ifc")
        r = b.import_ifc("d.ifc", "imp.dobim")
        assert r["ok"], r
        assert not r["result"]["warnings"]
        for v in (V_PLAN, V_SEC):
            assert b.run("element.get", {"id": v}, project="imp.dobim")["result"]["params"] == before[v]
        hi = b.hashes("imp.dobim")
        assert (hi["state"], hi["derived"], hi["ifc"]) == (h["state"], h["derived"], h["ifc"])
        assert ifcopenshell.open(str(tmp_path / "d.ifc"))
    finally:
        b.close()


def test_해시_형식은_13이다():
    from dobim_core import ifc_model
    assert ifc_model.CLOSURE_FORMAT == "13"


# ---------------------------------------------------------------- 시트 방위 기본값
def test_시트_방위_각은_생략하면_대지의_정북_각이다(base, tmp_path):
    v = mk(base, name="평면", type="plan", level=F.LV1)["id"]
    r = base.run("sheet.define", {"id": F.SH1, "name": "S0", "views": [v]})
    assert r["ok"], r
    assert base.run("element.get", {"id": F.SH1})["result"]["params"]["north_angle"] == 0.0              # 대지가 없으면 0
    assert base.run("site.create", {"id": F.SI1, "boundary": [[-6000, -6000], [18000, -6000], [18000, 14000], [-6000, 14000]], "north_angle": 25})["ok"]
    sh = "9" + "0" * 20 + "2"
    assert base.run("sheet.define", {"id": sh, "name": "S1", "views": [v]})["ok"]
    assert base.run("element.get", {"id": sh})["result"]["params"]["north_angle"] == 25.0
    sh2 = "9" + "0" * 20 + "3"
    assert base.run("sheet.define", {"id": sh2, "name": "S2", "views": [v], "north_angle": 0})["ok"]      # 명시한 값은 그대로
    assert base.run("element.get", {"id": sh2})["result"]["params"]["north_angle"] == 0.0

    def svg(**kw):
        a = {"views": [v], "path": "n.svg", "overwrite": True, **kw}
        r = base.run("sheet.export", a)
        if not r["ok"]:
            r = base.run("sheet.export", a, confirm=r["error"]["details"]["confirm_token"])
        assert r["ok"], r
        return (base.eng.workspace / "n.svg").read_text(encoding="utf-8")
    assert 'data-north-deg="25"' in svg()
    assert 'data-north-deg="0"' in svg(north_angle=0)
    assert 'data-north-deg="40"' in svg(north_angle=40)


def test_입면_재료_문자는_층_구성이_아니라_지정한_마감_재료다(base):
    assert base.run("pset.set", {"id": F.W1, "pset": "Pset_WallCommon", "prop": "IsExternal", "value": True})["ok"]
    fin = mk(base, name="입면 마감", type="elevation", dir="S", finish=True)["id"]
    assert base.run("material.set", {"id": F.W1, "name": "치장벽돌"})["ok"]
    assert [t[2] for t in data_of(base, fin)["labels"]] == ["치장벽돌"]
    assert base.run("wall.layers", {"id": F.W1, "layers": [{"material": "외장 치장벽돌", "thickness": 50}, {"material": "철근콘크리트", "thickness": 150}]})["ok"]
    assert data_of(base, fin)["labels"] == ()      # 층 구성을 주면 재료는 층 구성이 정한다: 구조체 이름(철근콘크리트)을 입면에 깔지 않는다
