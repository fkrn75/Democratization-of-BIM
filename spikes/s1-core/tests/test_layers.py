# SPDX-License-Identifier: MPL-2.0
"""벽·슬래브 층 구성(layers), 문짝 두께(leaf), 공간 천장 높이(ceiling_height) 입력과 1:50 평면·단면·입면 상세.
입력이 없으면 저장 형식·해시·산출 바이트가 그대로, IFC 왕복, 증분 == 전체 재생성."""
import sys

import ifcopenshell.util.element as ue
import pytest

from conftest import ROOT
from dobim_core import globalid as GI   # 내보낸 IFC 를 원래 요소 id 로 연다(W2: 첫 글자가 무효한 id 는 파일에서 바뀐다)
from dobim_core import dispatch
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")

SPEC = {"project_name": "시험", "sheet_name": "도면", "sheet_number": "A-101", "date": "2026-11-15"}
LY3 = [{"material": "석고보드", "thickness": 20}, {"material": "콘크리트", "thickness": 160}, {"material": "치장벽돌", "thickness": 20}]
SLAB_LY = [{"material": "마감 모르타르", "thickness": 50}, {"material": "철근콘크리트", "thickness": 150}]
SLAB_PTS = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


def ok(r):
    assert r["ok"], r
    return r["result"]


def hashes(sess):
    r = ok(sess.run("model.hash", {}))
    return tuple(r[k] for k in ("state", "derived", "ifc"))


def export_ifc(sess, name):
    r = sess.run("ifc.export", {"path": name})
    if not r["ok"]:
        r = sess.run("ifc.export", {"path": name}, confirm=r["error"]["details"]["confirm_token"])
    ok(r)
    return GI.open_exported(str(sess.path.parent / name))


def export_file(sess, cmd, args):
    r = sess.run(cmd, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    ok(sess.run(cmd, args, confirm=r["error"]["details"]["confirm_token"]))
    return (sess.path.parent / args["path"]).read_bytes()


def detail(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_detail_lines, vid)


def full_detail(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return s3lib.RefGraph(s3lib._Inputs(sess.eng)).q(D.view_detail_lines, vid)


def plan(sess, scale):
    return ok(sess.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "scale": scale}))["id"]


def section(sess, scale):
    """x=3000 을 지나는 세로 단면(남북): W1(y=0)·W3(y=4000)을 수직으로 자른다."""
    return ok(sess.run("view.create", {"id": F.V2, "name": "B-B", "type": "section", "p1": [3000, -1000], "p2": [3000, 5000], "depth": 8000, "scale": scale}))["id"]


# ---------------------------------------------------------------- 입력이 없으면 그대로
def test_층_구성을_더했다_지우면_해시가_처음과_같다(base):
    h0 = hashes(base)
    ok(base.run("wall.layers", {"id": F.W3, "layers": LY3}))
    assert hashes(base) != h0
    ok(base.run("wall.layers", {"id": F.W3, "layers": None}))
    assert hashes(base) == h0
    ok(base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": SLAB_PTS, "thickness": 200}))
    h1 = hashes(base)
    ok(base.run("slab.layers", {"id": F.S1, "layers": SLAB_LY}))
    ok(base.run("slab.layers", {"id": F.S1, "layers": None}))
    assert hashes(base) == h1


def test_문짝_두께와_천장_높이도_지우면_해시가_같다(base):
    ok(base.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": SLAB_PTS, "name": "방", "height": 2700}))
    h0 = hashes(base)
    ok(base.run("door.set", {"id": F.D1, "leaf": 45}))
    ok(base.run("space.set", {"id": F.SP1, "ceiling_height": 2400}))
    assert hashes(base) != h0
    ok(base.run("door.set", {"id": F.D1, "clear_leaf": True}))
    ok(base.run("space.set", {"id": F.SP1, "clear_ceiling": True}))
    assert hashes(base) == h0


def test_입력이_없는_벽·문의_행에는_새_키가_없다(base):
    for eid in (F.W1, F.W2, F.D1):
        p = base.eng.st.el[eid]["params"]
        assert "layers" not in p and "leaf" not in p


# ---------------------------------------------------------------- 층 구성
def test_층_구성은_두께를_정하고_재료_이름표는_가장_두꺼운_층이다(base):
    r = ok(base.run("wall.layers", {"id": F.W3, "layers": LY3}))
    assert r["thickness"] == 200
    g = ok(base.run("element.get", {"id": F.W3}))
    assert g["params"]["layers"] == [{"material": m["material"], "thickness": float(m["thickness"])} for m in LY3]
    assert g["params"]["thickness"] == 200 and g["material"] == "콘크리트"


def test_층_합이_두께를_정한다_두께_직접_변경은_거절(base):
    ok(base.run("wall.layers", {"id": F.W3, "layers": LY3}))
    r = base.run("wall.set", {"id": F.W3, "thickness": 250})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    ok(base.run("wall.set", {"id": F.W3, "thickness": 200, "name": "같은 두께는 통과"}))
    ok(base.run("wall.layers", {"id": F.W3, "layers": [{"material": "콘크리트", "thickness": 250}]}))
    assert base.eng.st.el[F.W3]["params"]["thickness"] == 250


def test_층_구성이_있는_벽의_재료는_다른_재료로_바꿀_수_없다(base):
    ok(base.run("wall.layers", {"id": F.W3, "layers": LY3}))
    r = base.run("material.set", {"id": F.W3, "name": "Brick"})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    ok(base.run("material.set", {"id": F.W3, "name": "콘크리트"}))   # 같은 이름표는 할 일 없음


def test_층_구성은_단일_재료를_대신한다_되돌리면_단일_재료가_돌아온다(base):
    assert ok(base.run("element.get", {"id": F.W1}))["material"] == "Concrete"
    h0 = hashes(base)
    ok(base.run("wall.layers", {"id": F.W1, "layers": LY3}))
    assert ok(base.run("element.get", {"id": F.W1}))["material"] == "콘크리트"
    ok(base.run("history.undo", {}))
    assert hashes(base) == h0
    assert ok(base.run("element.get", {"id": F.W1}))["material"] == "Concrete"


def test_층_수_상한과_잘못된_값은_스키마가_거절한다(base):
    bad = [{"material": "a", "thickness": 10}] * 9
    assert not base.run("wall.layers", {"id": F.W3, "layers": bad})["ok"]
    assert not base.run("wall.layers", {"id": F.W3, "layers": [{"material": "a", "thickness": 0}]})["ok"]
    assert not base.run("wall.layers", {"id": F.W3, "layers": []})["ok"]


def test_undo_redo_삭제_분할이_층_구성과_해시를_보존한다(base):
    h0 = hashes(base)
    ok(base.run("wall.layers", {"id": F.W3, "layers": LY3}))
    h1 = hashes(base)
    ok(base.run("history.undo", {}))
    assert hashes(base) == h0
    ok(base.run("history.redo", {}))
    assert hashes(base) == h1
    ok(base.run("element.delete", {"id": F.W3}))
    ok(base.run("history.undo", {}))
    assert hashes(base) == h1
    # 분할: 끝 쪽 새 벽도 같은 층 구성(복사)
    r = ok(base.run("wall.split", {"id": F.W3, "at": 1000}))
    assert base.eng.st.el[r["new"]]["params"]["layers"] == base.eng.st.el[F.W3]["params"]["layers"]
    assert detail(base, plan(base, 50)) == full_detail(base, F.V1)


def test_IFC_층_구성_지정과_왕복(base):
    ok(base.run("wall.layers", {"id": F.W3, "layers": LY3}))
    ok(base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": SLAB_PTS, "thickness": 200, "top_offset": 0}))
    ok(base.run("slab.layers", {"id": F.S1, "layers": SLAB_LY}))
    f = export_ifc(base, "a.ifc")
    u = ue.get_material(f.by_guid(F.W3), should_inherit=False)
    assert u.is_a("IfcMaterialLayerSetUsage") and u.LayerSetDirection == "AXIS2" and u.DirectionSense == "POSITIVE" and u.OffsetFromReferenceLine == -100.0
    assert [(ly.Material.Name, ly.LayerThickness) for ly in u.ForLayerSet.MaterialLayers] == [("석고보드", 20.0), ("콘크리트", 160.0), ("치장벽돌", 20.0)]
    assert u.ForLayerSet.LayerSetName == "콘크리트"
    us = ue.get_material(f.by_guid(F.S1), should_inherit=False)   # 슬래브: IFC 는 아래에서 위 순서(입력은 윗면에서 아래로)
    assert us.LayerSetDirection == "AXIS3" and us.OffsetFromReferenceLine == -200.0
    assert [(ly.Material.Name, ly.LayerThickness) for ly in us.ForLayerSet.MaterialLayers] == [("철근콘크리트", 150.0), ("마감 모르타르", 50.0)]
    h = hashes(base)
    imp = ok(dispatch.run(base.env, "ifc.import", {"source": "a.ifc", "path": "imp.dobim"}))
    assert imp["warnings"] == []
    hi = tuple(ok(dispatch.run(base.env, "model.hash", {}, project="imp.dobim"))[k] for k in ("state", "derived", "ifc"))
    assert hi == h
    g = ok(dispatch.run(base.env, "element.get", {"id": F.W3}, project="imp.dobim"))
    assert g["origin"] == "authored" and g["params"]["layers"] == base.eng.st.el[F.W3]["params"]["layers"]


def test_층_구성을_떼면_IFC에서도_사라진다(base):
    ok(base.run("wall.layers", {"id": F.W3, "layers": LY3}))
    export_ifc(base, "a.ifc")
    ok(base.run("wall.layers", {"id": F.W3, "layers": None}))
    f = export_ifc(base, "b.ifc")
    assert not f.by_type("IfcMaterialLayerSet") and not f.by_type("IfcMaterialLayer") and not f.by_type("IfcMaterialLayerSetUsage")
    assert [m.Name for m in f.by_type("IfcMaterial")] == ["Concrete"]


# ---------------------------------------------------------------- 1:50 평면 상세가 층 구성·문짝 두께를 쓴다
def _hatch_y(lines):
    return sorted({round(x[2], 3) for x in lines if x[0] == "hatch" and abs(x[2] - x[4]) < 1e-6})


def test_평면_상세가_층_경계에_선을_긋는다(base):
    v = plan(base, 50)
    assert 4080.0 not in _hatch_y(detail(base, v))      # W3(y=4000, 두께 200)는 재료가 없어 상세 선이 없다
    ok(base.run("wall.layers", {"id": F.W3, "layers": [{"material": "석고보드", "thickness": 20}, {"material": "콘크리트", "thickness": 140}, {"material": "치장벽돌", "thickness": 40}]}))
    ys = _hatch_y(detail(base, v))
    # W3 는 x 감소 방향(6000,4000 -> 0,4000)이라 지역 +v 는 -y 방향: 첫 층(석고보드)이 y=4100 쪽 면, 경계는 y=4080, y=3940
    assert 4080.0 in ys and 3940.0 in ys
    assert detail(base, v) == full_detail(base, v)


def test_층_구성_없는_재료_벽은_규약_마감선_그대로(base):
    v = plan(base, 50)
    assert {x for x in _hatch_y(detail(base, v))} >= {-80.0, 80.0}   # W1(재료 있음, 두께 200): 면에서 20 안쪽
    ok(base.run("wall.layers", {"id": F.W1, "layers": [{"material": "콘크리트", "thickness": 200}]}))   # 층 하나: 경계가 없어 마감선도 없다
    assert -80.0 not in _hatch_y(detail(base, v)) and 80.0 not in _hatch_y(detail(base, v))


def test_문짝_두께_입력이_상세를_바꾼다(base):
    v = plan(base, 50)
    sym0 = sorted(x for x in detail(base, v) if x[0] == "symbol")
    ok(base.run("door.set", {"id": F.D1, "leaf": 60}))
    sym1 = sorted(x for x in detail(base, v) if x[0] == "symbol")
    assert sym0 != sym1 and len(sym0) == len(sym1)
    # D1(W1 위, offset 1500, 폭 900): 개구부 u0 = 1050, 문짝 옆선 u = 1050 + leaf
    assert any(abs(x[1] - 1110.0) < 1e-6 and abs(x[3] - 1110.0) < 1e-6 for x in sym1)
    assert any(abs(x[1] - 1090.0) < 1e-6 and abs(x[3] - 1090.0) < 1e-6 for x in sym0)   # 입력이 없으면 규약값 40: u = 1050 + 40
    assert detail(base, v) == full_detail(base, v)


# ---------------------------------------------------------------- 1:50 단면 상세
def test_단면_상세는_벽_층_경계선과_층별_해치를_그린다(base):
    ok(base.run("wall.layers", {"id": F.W3, "layers": LY3}))
    v = section(base, 50)
    d = detail(base, v)
    vert = {round(x[1], 3) for x in d if abs(x[1] - x[3]) < 1e-6}
    # 절단면(x=3000)이 W3(y=4000)을 수직으로 자른다. 단면의 s 는 p1(y=-1000)에서 y 방향으로 잰다(보는 방향 = 선의 왼쪽): 경계가 y = 4100-20, 4100-180 에 해당
    assert len(vert) == 2
    assert any(x[0] == "hatch" and abs(x[1] - x[3]) > 1e-6 and abs(x[2] - x[4]) > 1e-6 for x in d)   # 대각 해치
    assert d == full_detail(base, v)
    # 얇은 층(20mm = 용지 0.4mm)은 해치가 없고 두꺼운 층(160mm)만 해치가 있다
    s_lo, s_hi = sorted(vert)
    assert not [x for x in d if x[0] == "hatch" and abs(x[1] - x[3]) > 1e-6 and (max(x[1], x[3]) <= s_lo + 1e-6 or min(x[1], x[3]) >= s_hi - 1e-6)]


def test_작은_축척_단면과_층_구성_없는_단면은_상세가_없다(base):
    ok(base.run("wall.layers", {"id": F.W3, "layers": LY3}))
    assert detail(base, section(base, 100)) == ()
    ok(base.run("view.set", {"id": F.V2, "scale": 50}))
    assert detail(base, F.V2) != ()
    ok(base.run("wall.layers", {"id": F.W3, "layers": None}))
    assert detail(base, F.V2) == ()


def test_슬래브_층_경계선과_해치(base):
    ok(base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, -500], [6000, -500], [6000, 4500], [0, 4500]], "thickness": 200, "top_offset": 0}))
    ok(base.run("slab.layers", {"id": F.S1, "layers": SLAB_LY}))
    v = section(base, 50)
    d = detail(base, v)
    hz = {round(x[2], 3) for x in d if abs(x[2] - x[4]) < 1e-6}
    assert -50.0 in hz                                   # 윗면(0)에서 50 아래: 모르타르/콘크리트 경계
    assert any(x[0] == "hatch" and abs(x[2] - x[4]) > 1e-6 for x in d)
    assert d == full_detail(base, v)


def test_천장_높이가_있으면_천장선을_긋는다(base):
    ok(base.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[100, 100], [5900, 100], [5900, 3900], [100, 3900]], "name": "방", "height": 2700}))
    v = section(base, 50)
    assert detail(base, v) == ()                         # 천장 높이 입력이 없으면 없다
    ok(base.run("space.set", {"id": F.SP1, "ceiling_height": 2400}))
    d = detail(base, v)
    assert len(d) == 1 and d[0][0] == "proj" and d[0][2] == d[0][4] == 2400.0
    assert abs(d[0][3] - d[0][1] - 3800.0) < 1e-6        # 절단면이 공간을 지나는 구간(y 100..3900)
    assert d == full_detail(base, v)
    r = base.run("space.set", {"id": F.SP1, "ceiling_height": 3000})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"   # 공간 높이(2700)보다 클 수 없다


def test_입면_창대는_창이_있을_때만(base):
    v = ok(base.run("view.create", {"id": F.V1, "name": "S 입면", "type": "elevation", "dir": "S", "scale": 50}))["id"]
    assert detail(base, v) == ()
    ok(base.run("family.place", {"id": "0" * 20 + "w1", "family": "window.fixed", "host": F.W1, "offset": 4000.0}))
    d = detail(base, v)
    assert len(d) == 3 and all(x[0] == "proj" for x in d)
    zs = sorted({round(x[2], 3) for x in d} | {round(x[4], 3) for x in d})
    assert zs == [860.0, 900.0]                          # 창 하단 900 에서 40 내려온 창대
    assert d == full_detail(base, v)
    ok(base.run("view.set", {"id": v, "scale": 100}))
    assert detail(base, v) == ()


@pytest.mark.parametrize("ext", ["svg", "pdf"])
def test_입력이_없으면_단면_입면_detail이_바이트까지_같다(base, ext):
    vs = section(base, 50)
    ve = ok(base.run("view.create", {"id": F.V1, "name": "S 입면", "type": "elevation", "dir": "S", "scale": 50}))["id"]
    a = {"views": [vs, ve], "path": "a." + ext, **SPEC}
    x = export_file(base, "sheet.export", a)
    y = export_file(base, "sheet.export", {**a, "path": "b." + ext, "detail": True})
    assert x == y


def test_층_구성을_넣으면_단면_시트가_달라진다(base):
    vs = section(base, 50)
    a = {"views": [vs], "path": "a.svg", **SPEC}
    x = export_file(base, "sheet.export", {**a, "detail": True})
    ok(base.run("wall.layers", {"id": F.W3, "layers": LY3}))
    y = export_file(base, "sheet.export", {**a, "path": "b.svg", "detail": True})
    z = export_file(base, "sheet.export", {**a, "path": "c.svg"})
    assert y != x and y.count(b"<line") > x.count(b"<line")
    assert z.count(b"<line") < y.count(b"<line")        # detail 을 안 켜면 상세 선이 없다
