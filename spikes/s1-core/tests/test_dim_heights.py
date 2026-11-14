# SPDX-License-Identifier: MPL-2.0
"""높이 치수 자동 배치(annot.dim.heights): 층고·반자높이(슬래브 밑면 근사)·최고 높이, 재실행 대체, 연관성, 결정성, 위치."""
from dobim_core import annot as AN
from dobim_core import drawing as D
from dobim_core import fixture as F

SL_2F, SL_ROOF = "6" + "0" * 20 + "2", "6" + "0" * 20 + "3"


def mk(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def add_slabs(sess):
    """2F 바닥 슬래브(3100..3300)와 지붕 슬래브(윗면 6600 = 2F + 3300, 3 개 층 높이 시험용)."""
    pts = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
    assert sess.run("slab.create", {"id": SL_2F, "level": F.LV2, "boundary": pts, "thickness": 200})["ok"]
    assert sess.run("slab.create", {"id": SL_ROOF, "level": F.LV2, "boundary": pts, "thickness": 200, "top_offset": 3300})["ok"]


def heights(sess, view, **kw):
    r = sess.run("annot.dim.heights", {"view": view, **kw})
    assert r["ok"], r
    return r["result"]


def by_kind(res):
    out = {}
    for d in res["dims"]:
        out.setdefault(d["kind"], []).append(d)
    return out


def test_단면에_층고_반자높이_최고높이_치수가_자동으로_생긴다(base):
    add_slabs(base)
    v = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
    res = heights(base, v)
    k = by_kind(res)
    assert [d["value"] for d in k["floor"]] == [3300.0]                                  # 1F -> 2F
    assert [d["value"] for d in k["clear"]] == [3100.0, 3100.0]                          # 1F: 0 -> 2F 슬래브 밑면 3100, 2F: 3300 -> 지붕 슬래브 밑면 6400
    assert [d["value"] for d in k["overall"]] == [6600.0]                                # 지붕 슬래브 윗면(벽 최고 3000 + 3300 = 6300 보다 높다)
    assert k["overall"][0]["b"] == f"slab:{SL_ROOF}#top" and k["floor"][0]["name"] == "H-층고-1F"
    items = base.run("annot.list", {"view": v})["result"]["items"]
    assert len(items) == 4 and all(i["status"] == "ok" and i["kind"] == "dim" for i in items)
    assert sorted(i["value"] for i in items) == [3100.0, 3100.0, 3300.0, 6600.0]
    assert base.run("annot.check", {})["result"]["problems"] == 0


def test_종류를_골라_만들고_평면_뷰는_거절한다(base):
    add_slabs(base)
    v = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
    res = heights(base, v, kinds=["overall"])
    assert [d["kind"] for d in res["dims"]] == ["overall"]
    plan = mk(base, name="P", type="plan", level=F.LV1)
    r = base.run("annot.dim.heights", {"view": plan})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    r = base.run("annot.dim.heights", {"view": F.W1})
    assert not r["ok"]                                                                   # 뷰가 아니다
    r = base.run("annot.dim.heights", {"view": v, "kinds": []})
    assert not r["ok"]                                                                   # 종류가 비었다


def test_슬래브_없이도_층고와_벽_기준_최고높이를_만든다(base):
    v = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
    k = by_kind(heights(base, v))
    assert [d["value"] for d in k["floor"]] == [3300.0] and "clear" not in k              # 슬래브가 없으면 반자(안목) 치수는 없다
    assert k["overall"][0]["value"] == 3000.0 and k["overall"][0]["b"] == f"wall:{F.W1}#top"   # 벽 3 개 모두 3000: 같은 높이면 id 가 작은 벽


def test_다시_실행하면_이전_자동_치수를_지우고_같은_id로_다시_만든다(base):
    add_slabs(base)
    v = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
    r1 = heights(base, v)
    manual = base.run("annot.dim.create", {"view": v, "a": f"level:{F.LV1}#elevation", "b": f"level:{F.LV2}#elevation", "name": "내 치수", "at": [0, 0]})
    assert manual["ok"]
    r2 = heights(base, v)
    assert sorted(r2["removed"]) == sorted(r1["created"]) and r2["created"] == r1["created"]   # 같은 id
    names = sorted(i["name"] for i in base.run("annot.list", {"view": v})["result"]["items"])
    assert "내 치수" in names and len(names) == 5                                          # 손으로 만든 치수는 건드리지 않는다
    assert base.run("history.undo", {})["ok"]                                              # 한 번의 실행 = 한 번의 되돌리기
    assert len(base.run("annot.list", {"view": v})["result"]["items"]) == 5
    assert base.run("history.undo", {})["ok"] and base.run("history.undo", {})["ok"]       # 수동 치수, 첫 자동 치수까지
    assert base.run("annot.list", {"view": v})["result"]["items"] == []


def test_치수는_연관돼서_슬래브_두께_레벨_높이_변경을_따라간다(base):
    add_slabs(base)
    v = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
    heights(base, v)
    val = lambda name: next(i["value"] for i in base.run("annot.list", {"view": v})["result"]["items"] if i["name"] == name)   # noqa: E731
    assert base.run("slab.set", {"id": SL_2F, "thickness": 300})["ok"]                     # 밑면 3000
    assert val("H-반자-1F") == 3000.0 and val("H-층고-1F") == 3300.0
    assert base.run("slab.set", {"id": SL_ROOF, "top_offset": 3600.0})["ok"]               # 지붕 윗면 6900
    assert val("H-최고높이") == 6900.0
    assert base.run("slab.delete", {"id": SL_2F})["ok"]                                    # 대상이 사라지면 고아로 표시한다(조용히 지우지 않는다)
    st = {i["name"]: i["status"] for i in base.run("annot.list", {"view": v})["result"]["items"]}
    assert st["H-반자-1F"] == "orphan" and st["H-층고-1F"] == "ok"
    # 다시 실행하면 남은 슬래브 기준으로 새로 만든다: 1F 위 가장 낮은 슬래브 밑면은 지붕 슬래브(6600 + 300 - 200 ...)
    res = heights(base, v)
    assert all(i["status"] == "ok" for i in base.run("annot.list", {"view": v})["result"]["items"])
    assert [d["name"] for d in by_kind(res)["clear"]] == ["H-반자-1F", "H-반자-2F"]


def test_치수선은_뷰_그림의_왼쪽_바깥에_자동으로_놓인다(base):
    add_slabs(base)
    for kw in (dict(name="S", type="section", p1=[3000, -2000], p2=[3000, 2000]), dict(name="E", type="elevation", dir="S")):
        v = mk(base, **kw)
        res = heights(base, v)
        base.eng.sync()
        base.eng._touch_pending()
        g = base.eng.g
        xmin = min(min(ln[1], ln[3]) for ln in g.q(D.view_lines, v))
        for d in res["dims"]:
            item = g.q(AN.annot_draw, d["id"])
            assert item[0] == "dim", item
            assert item[4][0] < xmin - 400.0 and item[4][2] < xmin - 400.0, (d["name"], item[4], xmin)   # 치수선이 그림 왼쪽 밖
        offs = sorted(g.q(AN.annot_draw, d["id"])[4][0] for d in res["dims"])
        assert offs[0] < offs[-1]                                                         # 층고·반자·전체가 서로 다른 열


def test_at을_주면_그_위치를_쓴다(base):
    v = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
    res = heights(base, v, at=[-5000.0, 0.0], kinds=["floor"])
    p = base.run("element.get", {"id": res["dims"][0]["id"]})["result"]["params"]
    assert p["at"] == [-5000.0, 0.0]


def test_자동_치수_id와_결과는_결정적이다(tmp_path):
    import sys
    from conftest import ROOT
    sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
    import s3lib

    def build(name):
        s = s3lib.kit.Session(tmp_path / name, guid=s3lib.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        add_slabs(s)
        v = mk(s, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
        res = heights(s, v)
        s.eng.sync()
        s.eng._touch_pending()
        draw = [s.eng.g.q(AN.annot_draw, d["id"]) for d in res["dims"]]
        out = (res, draw, s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a[0] == b[0] and a[1] == b[1] and a[2]["state"] == b[2]["state"] and a[2]["derived"] == b[2]["derived"]


def test_증분이_전체_재생성과_같다(base):
    import sys
    from conftest import ROOT
    sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
    import s3lib
    from dobim_core.graph import RefGraph
    from dobim_core.engine import _Inputs
    add_slabs(base)
    v = mk(base, name="S", type="section", p1=[3000, -2000], p2=[3000, 2000])
    e = mk(base, name="E", type="elevation", dir="E")
    heights(base, v)
    heights(base, e)

    def check():
        base.eng.sync()
        base.eng._touch_pending()
        assert base.eng.g.q(AN.annot_bundle) == RefGraph(_Inputs(base.eng)).q(AN.annot_bundle)

    check()
    assert base.run("slab.set", {"id": SL_ROOF, "thickness": 400})["ok"]
    check()
    assert base.run("level.create", {"name": "3F", "elevation": 6600})["ok"]
    heights(base, v)
    check()
    assert s3lib is not None
