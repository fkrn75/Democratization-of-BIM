# SPDX-License-Identifier: MPL-2.0
"""배치도 뷰(type site): 대지 경계·건물 외곽·도로·방위표·이격 치수·문자, SVG/PDF/시트, 증분 == 전체, 대지가 없으면 기존 뷰 불변, 뷰 정의의 IFC 왕복."""
import re
import sys

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import drawing_export as DX
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

LOT = [[-6000, -6000], [18000, -6000], [18000, 14000], [-6000, 14000]]
ROADS = [{"name": "남측 도로", "edge": 0, "width": 8000}, {"name": "동측 도로", "edge": 1, "width": 6000}]
V_SITE = "4" + "0" * 20 + "9"


def confirmed(sess, name, args):
    first = sess.run(name, args)
    assert first["error"]["code"] == "CONFIRM_REQUIRED", first
    return sess.run(name, args, confirm=first["error"]["details"]["confirm_token"])


def read(sess, name):
    return (sess.path.parent / name).read_bytes()


def mk_site(sess, boundary=LOT, **kw):
    r = sess.run("site.create", {"id": F.SI1, "boundary": boundary, "name": "대지", "roads": ROADS, **kw})
    assert r["ok"], r
    return F.SI1


def mk(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def site_view(sess, scale=500, name="배치도"):
    return mk(sess, name=name, type="site", scale=scale)


def refresh(sess):
    sess.eng.sync()
    sess.eng._touch_pending()


def data_of(sess, vid):
    refresh(sess)
    return {"lines": sess.eng.g.q(D.view_lines, vid), "labels": sess.eng.g.q(D.view_labels, vid), "dims": sess.eng.g.q(D.site_dims, vid),
            "fills": sess.eng.g.q(D.view_fills, vid)}


def dim_texts(dims):
    return sorted(d[5] for d in dims)


# ---------------------------------------------------------------- 내용
def test_배치도는_이격_치수와_도로_폭_문자를_모델_값으로_그린다(base):
    mk_site(base)
    d = data_of(base, site_view(base))
    # 기준 프로젝트 벽: x 0~6000(두께 200), y 0~4000. 대지 -6000..18000 x -6000..14000
    assert dim_texts(d["dims"]) == sorted(["6000", "5900", "11900", "9900", "8000", "6000"])
    texts = [x[2] for x in d["labels"]]
    assert "대지" in texts and "대지면적 480.00㎡" in texts and "N" in texts and "S=1:500" in texts
    assert "남측 도로 도로 폭 8000" in texts and "접도 길이 24000" in texts       # 남측 변(24000) 폭 8000
    assert "동측 도로 도로 폭 6000" in texts and "접도 길이 20000" in texts
    assert d["fills"] == ()


def test_대지_표고는_m_단위_문자로_나온다(base):
    mk_site(base, elevation=1500)
    assert "대지 표고 EL +1.500m" in [x[2] for x in data_of(base, site_view(base))["labels"]]


def test_건물_외곽은_합집합_바깥_선만이다(base):
    mk_site(base)
    d = data_of(base, site_view(base))
    cut = [ln for ln in d["lines"] if ln[0] == "cut"]
    # 건물 외곽 선분만 모은다(대지 경계 1점쇄선과 방위표는 건물 범위 밖에 있다)
    bld = [ln for ln in cut if -200.0 <= min(ln[1], ln[3]) and max(ln[1], ln[3]) <= 6200.0 and -200.0 <= min(ln[2], ln[4]) and max(ln[2], ln[4]) <= 4200.0]
    assert bld
    # 벽 사이 이음매(조인한 모서리 안쪽·벽 끝 맞닿음)는 선이 없다: 어떤 외곽 선분도 다른 벽 두께 띠 안쪽에 놓이지 않는다
    for x0, y0, x1, y1 in ((a[1], a[2], a[3], a[4]) for a in bld):
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        inside_w1 = -100.0 + 1e-6 < my < 100.0 - 1e-6 and 0 < mx < 6000
        inside_w2 = 5900.0 + 1e-6 < mx < 6100.0 - 1e-6 and 0 < my < 4000
        assert not (inside_w1 or inside_w2), (x0, y0, x1, y1)


def test_슬래브만_있어도_건물_외곽과_이격이_나온다(sess):
    assert sess.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})["ok"]
    assert sess.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [8000, 0], [8000, 5000], [0, 5000]]})["ok"]
    mk_site(sess, roads=[])
    d = data_of(sess, site_view(sess))
    assert dim_texts(d["dims"]) == sorted(["6000", "6000", "10000", "9000"])      # 서(6000)·남(6000)·동(18000-8000)·북(14000-5000)


def test_방위_각이_방위표_선을_돌린다(base):
    mk_site(base)
    v = site_view(base)
    l0 = data_of(base, v)["lines"]
    assert base.run("site.set", {"id": F.SI1, "north_angle": 45})["ok"]
    l1 = data_of(base, v)["lines"]
    assert l0 != l1
    assert [x for x in l0 if x[0] == "proj"] == [x for x in l1 if x[0] == "proj"]       # 도로·건물은 그대로, 방위표만 돈다
    assert base.run("site.set", {"id": F.SI1, "north_angle": 0})["ok"]
    assert data_of(base, v)["lines"] == l0


def test_대지가_없으면_배치도는_건물_외곽만_그리고_문자·치수는_없다(base):
    d = data_of(base, site_view(base))
    assert d["labels"] == () and d["dims"] == () and d["lines"] and all(ln[0] == "cut" for ln in d["lines"])


def test_대지가_있어도_다른_뷰는_바뀌지_않는다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1), "p2": mk(base, name="P2", type="plan", level=F.LV2, scale=50),
             "s": mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000]), "e": mk(base, name="E", type="elevation", dir="S")}
    before = {k: data_of(base, v) for k, v in views.items()}
    mk_site(base)
    assert base.run("view.create", {"name": "배치도", "type": "site"})["ok"]
    for k, v in views.items():
        assert data_of(base, v) == before[k], k
        assert before[k]["dims"] == ()


# ---------------------------------------------------------------- 내보내기
def test_SVG_PDF_내보내기에_배치도가_나온다(base, tmp_path):
    mk_site(base, north_angle=20)
    v = site_view(base)
    r = confirmed(base, "view.export", {"id": v, "path": "site.svg", "overwrite": True})
    assert r["ok"], r
    svg = read(base, "site.svg").decode("utf-8")
    assert "배치도  S=1:500" in svg
    assert 'class="dim"' in svg and "11900" in svg and "접도 길이 24000" in svg and "대지면적 480.00㎡" in svg
    assert re.search(r'<g id="lines-cut"', svg)
    r = confirmed(base, "view.export", {"id": v, "path": "site.pdf", "overwrite": True})
    assert r["ok"], r
    assert read(base, "site.pdf").startswith(b"%PDF")


def test_시트에_배치도를_놓을_수_있고_다른_뷰와_함께_놓인다(base):
    mk_site(base)
    v = site_view(base, scale=500)
    p = mk(base, name="1F 평면", type="plan", level=F.LV1, scale=500)
    r = confirmed(base, "sheet.export", {"views": [v, p], "path": "sheet-site.svg", "paper": "A1", "sheet_number": "A-001", "sheet_name": "배치도",
                                         "project_name": "테스트", "date": "2026-01-01", "north_angle": 20, "overwrite": True})
    assert r["ok"], r
    svg = read(base, "sheet-site.svg").decode("utf-8")
    assert f'data-view="{v}"' in svg and "접도 길이 24000" in svg and ">11900<" in svg and 'id="north-symbol"' in svg
    r = confirmed(base, "sheet.export", {"views": [v], "path": "sheet-site.pdf", "paper": "A1", "overwrite": True})
    assert r["ok"] and read(base, "sheet-site.pdf").startswith(b"%PDF")


# ---------------------------------------------------------------- 증분 == 전체
def test_배치도_증분이_전체_재생성과_같다(base):
    v = site_view(base)

    def check():
        refresh(base)
        ref = s3lib.RefGraph(s3lib._Inputs(base.eng))
        for fn in (D.view_lines, D.view_labels, D.site_dims):
            assert base.eng.g.q(fn, v) == ref.q(fn, v), fn.__name__

    check()
    mk_site(base)
    check()
    assert base.run("site.set", {"id": F.SI1, "north_angle": 33, "boundary": [[-5000, -5000], [20000, -5000], [20000, 15000], [-5000, 15000]],
                                 "roads": [{"name": "북측", "edge": 2, "width": 4000}]})["ok"]
    check()
    assert base.run("wall.move", {"id": F.W1, "dx": 0, "dy": 500})["ok"]
    check()
    assert base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[-1000, -1000], [7000, -1000], [7000, 5000], [-1000, 5000]]})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("view.set", {"id": v, "scale": 200})["ok"]
    check()
    assert base.run("site.delete", {"id": F.SI1})["ok"]
    check()


def test_뷰_종류_검사와_수정_규칙(base):
    v = site_view(base)
    assert not base.run("view.create", {"name": "x", "type": "site", "level": F.LV1})["ok"]        # 쓰지 않는 필드
    assert not base.run("view.set", {"id": v, "cut_height": 100})["ok"]
    assert base.run("view.set", {"id": v, "name": "배치도 2", "scale": 300})["ok"]
    assert base.run("view.list", {"type": "site"})["result"]["items"][0]["scale"] == 300
    assert not base.run("annot.dim.create", {"view": v, "a": f"wall:{F.W1}#axis.start", "b": f"wall:{F.W1}#axis.end"})["ok"]   # 평면 치수는 배치도에 달지 않는다


def test_배치도_뷰_정의는_IFC로_왕복한다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("site.create", {"id": F.SI1, "boundary": LOT, "roads": ROADS})["ok"]
        r = b.run("view.create", {"id": V_SITE, "name": "배치도", "type": "site", "scale": 500})
        assert r["ok"], r
        h = b.hashes()
        b.export("s.ifc")
        r = b.import_ifc("s.ifc", "imp.dobim")
        assert r["ok"], r
        items = b.run("view.list", {"type": "site"}, project="imp.dobim")["result"]["items"]
        assert [(i["id"], i["scale"]) for i in items] == [(V_SITE, 500)]
        hi = b.hashes("imp.dobim")
        assert hi["state"] == h["state"]
    finally:
        b.close()


def test_배치도_데이터는_SVG에서_문자가_없으면_바이트가_그대로다():
    base_data = {"type": "site", "name": "B", "scale": 500, "lines": (("cut", 0.0, 0.0, 5000.0, 0.0),), "fills": (), "annots": (), "datums": (("1F", 0.0),)}
    assert DX.render_svg(dict(base_data, labels=())) == DX.render_svg(base_data)
    assert b'id="datums"' not in DX.render_svg(base_data)       # 배치도는 평면처럼 레벨 기준선을 그리지 않는다
