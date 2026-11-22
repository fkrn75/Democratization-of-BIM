# SPDX-License-Identifier: MPL-2.0
"""W2 세 번째 PR ARC-34: 독립 개구 객체(수직 개구·샤프트 개구·벽 개구).
- 호스트 params 는 그대로, 도면(슬래브 구멍·벽 모양 개구부·평면 기호)과 IFC(호스트마다 IfcOpeningElement + IfcRelVoidsElement)가 뺀다
- 샤프트는 레벨 범위의 슬래브를 한 번에(나중에 만든 슬래브도) 관통, 평면은 경계 + X
- 벽 개구는 분할·대칭·트림과 함께 움직이고, 곡선 벽은 사각만
- 되돌리기·다시 하기, 증분 = 전체 재계산, IFC4·IFC2X3 검증, 왕복 해시. 합성 데이터만 쓴다."""
import math
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import queries as Q
from dobim_core.ifc_model import part_guid

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

S2 = "6" + "0" * 20 + "2"
V, VS, V2F = F.V1, F.V2, "4" + "0" * 20 + "7"
SQ = [[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]
SH = [[4000, 500], [5000, 500], [5000, 1500], [4000, 1500]]
BOX = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def g_of(s):
    s.eng.sync()
    s.eng._touch_pending()
    return s.eng.g


def params(s, eid):
    return ok(s.run("element.get", {"id": eid}))["params"]


def setup(s):
    ok(s.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": BOX, "thickness": 200}))
    ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.create", {"id": V2F, "name": "2F", "type": "plan", "level": F.LV2, "cut_height": 1200}))
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [-1000, 1500], "p2": [7000, 1500]}))
    return s


@pytest.fixture
def bs(base):
    return setup(base)


def same_as_full(s, views=(V, V2F, VS)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)


def test_수직_개구는_슬래브_구멍이고_평면에_경계를_그린다(bs):
    before = g_of(bs).q(D.slab_cut_rects, VS, F.S1)
    r = ok(bs.run("opening.vertical", {"host": F.S1, "boundary": SQ}))
    o = r["id"]
    assert r["area_m2"] == 1.0 and "openings" not in params(bs, F.S1)                # 슬래브 params 는 그대로
    g = g_of(bs)
    assert len(g.q(D.slab_info, F.S1)[3]) == 1
    after = g.q(D.slab_cut_rects, VS, F.S1)
    assert len(after) == len(before) + 1                                               # 단면: 구멍 자리에서 절단 사각형이 나뉜다
    pl = g.q(D.plan_opening_lines, V)
    assert len(pl) == 4 and all(ln[0] == "proj" for ln in pl) and not g.q(D.plan_opening_lines, V2F)
    assert err(bs.run("opening.vertical", {"host": F.S1, "boundary": [[1500, 1500], [2500, 1500], [2500, 2500], [1500, 2500]]}))["code"] == "CONSTRAINT"   # 겹친다
    assert err(bs.run("opening.vertical", {"host": F.S1, "boundary": [[5500, 3500], [6500, 3500], [6500, 4500], [5500, 4500]]}))["code"] == "CONSTRAINT"   # 경계 밖
    assert err(bs.run("opening.vertical", {"host": F.W1, "boundary": SQ}))["code"] in ("CONSTRAINT", "NOT_FOUND")
    same_as_full(bs)
    ok(bs.run("history.undo", {}))
    assert not g_of(bs).q(D.slab_info, F.S1)[3]
    ok(bs.run("history.redo", {}))
    ok(bs.run("opening.delete", {"ids": [o]}))
    assert not g_of(bs).q(D.slab_info, F.S1)[3]


def test_샤프트는_레벨_범위의_슬래브를_한_번에_관통한다(bs):
    r = ok(bs.run("opening.shaft", {"level": F.LV1, "boundary": SH, "height": 6600, "base_offset": -300}))
    assert r["slabs"] == [F.S1] and r["bottom"] == -300 and r["top"] == 6300
    ok(bs.run("slab.create", {"id": S2, "level": F.LV2, "boundary": BOX, "thickness": 200}))   # 나중에 만든 위층 슬래브도 관통한다
    g = g_of(bs)
    assert [v[0] for v in g.q(Q.slab_voids, S2)] == [r["id"]] and [v[0] for v in g.q(Q.slab_voids, F.S1)] == [r["id"]]
    for v in (V, V2F):                                                                 # 두 층 평면 모두 경계 + X
        pl = g.q(D.plan_opening_lines, v)
        assert len(pl) == 6
    ok(bs.run("opening.shaft", {"id": "9" + "0" * 19 + "22", "level": F.LV2, "boundary": [[500, 3000], [1000, 3000], [1000, 3500], [500, 3500]], "height": 1000}))
    g = g_of(bs)
    assert len(g.q(Q.slab_voids, S2)) == 2 and len(g.q(Q.slab_voids, F.S1)) == 1         # 2F 에서 시작한 짧은 샤프트는 1F 슬래브를 뚫지 않는다
    assert err(bs.run("opening.vertical", {"host": S2, "boundary": [[4500, 1000], [5500, 1000], [5500, 2000], [4500, 2000]]}))["code"] == "CONSTRAINT"   # 샤프트와 겹친다
    same_as_full(bs)


def test_벽_개구_사각과_원(bs):
    def net(w):
        return {r[0]: r for r in ok(bs.run("schedule.rows", {"kind": "wall"}))["items"]}[w][7]
    n0 = net(F.W2)
    a = ok(bs.run("opening.wall", {"host": F.W2, "offset": 2800, "sill": 900, "width": 800, "height": 1200}))["id"]
    assert net(F.W2) == pytest.approx(n0 - 800 * 1200)                                  # 일람표 순 면적에서 뺀다
    c = ok(bs.run("opening.wall", {"host": F.W1, "shape": "circle", "offset": 4500, "sill": 1500, "width": 600, "height": 600}))["id"]
    assert "openings" not in params(bs, F.W2)
    g = g_of(bs)
    assert any(o[:2] == pytest.approx((2400, 3200)) and o[2:] == pytest.approx((900, 2100)) for o in g.q(D.wall_openings, F.W2))
    shapes = g.q(D.wall_shape_openings, F.W1)
    assert len(shapes) == 1 and shapes[0][0] is None and len(shapes[0][1]) > 8       # 원: 사각 부분 없이 윤곽만(입면에 그린다)
    assert err(bs.run("opening.wall", {"host": F.W2, "offset": 2800, "sill": 1500, "width": 800, "height": 600}))["code"] == "CONSTRAINT"   # 겹친다
    assert err(bs.run("opening.wall", {"host": F.W1, "offset": 1500, "sill": 900, "width": 600, "height": 600}))["code"] == "CONSTRAINT"   # 문과 겹친다
    assert err(bs.run("opening.wall", {"host": F.W2, "offset": 3900, "sill": 900, "width": 800, "height": 600}))["code"] == "CONSTRAINT"   # 벽 밖
    ok(bs.run("wall.split", {"id": F.W2, "at": 2000, "new_id": "1" + "0" * 19 + "b2"}))   # 분할: 놓인 쪽 새 벽으로 옮겨 단다
    p = params(bs, a)
    assert p["host"] == "1" + "0" * 19 + "b2" and p["offset"] == 800
    ok(bs.run("history.undo", {}))
    assert err(bs.run("wall.split", {"id": F.W2, "at": 2800}))["code"] == "CONSTRAINT"   # 개구를 가로지른다
    ok(bs.run("element.mirror", {"ids": [F.W2], "axis": [0, 2000, 1000, 2000]}))         # 대칭: u -> L - u
    assert params(bs, a)["offset"] == 1200
    same_as_full(bs)
    assert g_of(bs).q(D.wall_shape_openings, F.W1)[0][1] == shapes[0][1]
    r = bs.run("element.delete", {"id": F.W2})                                           # 호스트를 지우면 개구는 고아로 남는다(그리지 않는다)
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = bs.run("element.delete", {"id": F.W2}, confirm=r["error"]["details"]["confirm_token"])
    assert r["ok"], r
    assert params(bs, a)["host"] == F.W2 and params(bs, c)["host"] == F.W1
    same_as_full(bs)


def test_곡선_벽_개구는_사각만(bs):
    q = ok(bs.run("wall.arc", {"level": F.LV1, "points": [[0, 4000], [-2000, 2000], [0, 0]]}))["id"]
    ok(bs.run("opening.wall", {"host": q, "offset": math.pi * 1000, "sill": 900, "width": 1000, "height": 1000}))
    assert err(bs.run("opening.wall", {"host": q, "shape": "circle", "offset": 1000, "sill": 900, "width": 600, "height": 600}))["code"] == "CONSTRAINT"
    g = g_of(bs)
    assert any(o[:2] == pytest.approx((math.pi * 1000 - 500, math.pi * 1000 + 500)) for o in g.q(D.wall_openings, q))
    ok(bs.run("wall.offset", {"ids": [q], "distance": 500, "keep": False}))               # 동심 간격띄우기: 같은 각도
    oid = sorted(bs.eng.st.by_kind["opening"])[0]
    assert params(bs, oid)["offset"] == pytest.approx(math.pi * 750)
    same_as_full(bs)


def _ifc_errors(ff):
    import ifcopenshell.validate as IV
    lg = IV.json_logger()
    IV.validate(ff, lg, express_rules=True)
    return [x["message"] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def test_독립_개구의_IFC_와_왕복(tmp_path):
    import ifcopenshell
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": BOX, "thickness": 200}))
        ok(b.run("slab.create", {"id": S2, "level": F.LV2, "boundary": BOX, "thickness": 200}))
        v = ok(b.run("opening.vertical", {"host": F.S1, "boundary": SQ}))["id"]
        sh = ok(b.run("opening.shaft", {"level": F.LV1, "boundary": SH, "height": 6600}))["id"]
        w = ok(b.run("opening.wall", {"host": F.W2, "offset": 2800, "sill": 900, "width": 800, "height": 1200}))["id"]
        q = ok(b.run("wall.arc", {"level": F.LV1, "points": [[0, -6000], [-2000, -8000], [0, -10000]]}))["id"]
        aw = ok(b.run("opening.wall", {"host": q, "offset": math.pi * 1000, "sill": 900, "width": 1000, "height": 1000}))["id"]
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        nm = {e: ok(b.run("element.get", {"id": e}))["params"]["name"] for e in (F.S1, S2, F.W2, q)}
        voids = {r.RelatedOpeningElement.GlobalId: r.RelatingBuildingElement.Name for r in f.by_type("IfcRelVoidsElement")}
        assert voids[part_guid(F.S1, f"void:{v}")] == nm[F.S1]
        assert voids[part_guid(F.S1, f"void:{sh}")] == nm[F.S1] and voids[part_guid(S2, f"void:{sh}")] == nm[S2]
        assert voids[part_guid(F.W2, f"void:{w}")] == nm[F.W2] and voids[part_guid(q, f"void:{aw}")] == nm[q]
        assert _ifc_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t3.ifc", "schema": "IFC2X3"})["ok"]
        assert _ifc_errors(ifcopenshell.open(str(tmp_path / "t3.ifc"))) == []
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: x for k, x in b.hashes("imp.dobim").items() if k != "rev"} == {k: x for k, x in h.items() if k != "rev"}
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        ok(b.run("opening.delete", {"ids": [sh]}))                                        # 지우면 두 슬래브의 개구 부속이 모두 빠진다
        f = ifcopenshell.open(str(b.export("t4.ifc")))
        assert not any(o.GlobalId in (part_guid(F.S1, f"void:{sh}"), part_guid(S2, f"void:{sh}")) for o in f.by_type("IfcOpeningElement"))
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)                                          # 되돌리면 의미 해시(state·derived·ifc)가 같다
    finally:
        b.close()
