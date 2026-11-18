# SPDX-License-Identifier: MPL-2.0
"""20단계(실무 모델링 2): 호스팅 패밀리 뒤집기(rotation 180), 미닫이문 패밀리(door.sliding), 기존 요소 편집(경계·구멍·형상 교체, id 유지).
합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

DOOR = "0" * 21 + "7"
SLD = "0" * 21 + "8"


def _plan_syms(base, wall):
    base.eng.sync()
    base.eng._touch_pending()
    return base.eng.g.q(D.hosted_fam, wall)


def _arc(fam):
    return next(it for it in fam[6] if it[0] == "arc")


def test_호스팅_문을_뒤집으면_평면_기호가_반대쪽으로_열리고_개구부는_그대로다(base):
    h0 = base.eng.state_hash()
    r = base.run("family.place", {"id": DOOR, "family": "door.single", "values": {"swing": "L"}, "host": F.W3, "offset": 1500})
    assert r["ok"], r
    f0 = _plan_syms(base, F.W3)[0]
    assert base.run("family.set", {"id": DOOR, "rotation": 180})["ok"]
    f1 = _plan_syms(base, F.W3)[0]
    assert f1[:6] == f0[:6]                      # 개구부 범위·높이 그대로
    a0, a1 = _arc(f0), _arc(f1)
    assert a1[1] == (-a0[1][0], -a0[1][1]) and a1[3] == a0[3] + 180.0
    assert max(v for it in f1[6] if it[0] == "line" for v in (it[1][1], it[2][1])) <= 1e-9 or \
        min(v for it in f1[6] if it[0] == "line" for v in (it[1][1], it[2][1])) < 0   # 문짝 선이 -v 쪽
    for bad in (90, 45):
        r = base.run("family.set", {"id": DOOR, "rotation": bad})
        assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    assert base.run("family.set", {"id": DOOR, "rotation": 0})["ok"]
    assert _plan_syms(base, F.W3)[0] == f0
    for _ in range(3):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_뒤집은_문은_IFC_배치_방향이_반대이고_왕복하면_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        r = b.run("family.place", {"id": DOOR, "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 1500, "rotation": 180})
        assert r["ok"], r
        r = b.run("family.place", {"id": SLD, "family": "door.sliding", "type_values": {"width": 1600, "panels": 2}, "host": F.W3, "offset": 3500})
        assert r["ok"], r
        h = b.hashes()
        out = b.export("r.ifc")
        f = ifcopenshell.open(str(out) if out else str(tmp_path / "r.ifc"))
        d = f.by_guid(DOOR)
        assert tuple(d.ObjectPlacement.RelativePlacement.RefDirection.DirectionRatios)[:2] == (-1.0, 0.0)
        s = f.by_guid(SLD)
        assert s.is_a("IfcDoor") and s.OperationType == "DOUBLE_DOOR_SLIDING" and s.OverallWidth == 1600
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim")["foreign"] == 0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


def test_미닫이문은_한_짝_방향과_두_짝을_갖고_벽보다_넓으면_거절한다(base):
    r = base.run("family.place", {"id": SLD, "family": "door.sliding", "type_values": {"width": 2200}, "values": {"slide": "R"}, "host": F.W3, "offset": 2000})
    assert r["ok"], r
    g = base.run("element.get", {"id": SLD})["result"]
    assert g["ifc_class"] == "IfcDoor" and g["params"]["width"] == 2200.0
    syms = _plan_syms(base, F.W3)[0][6]
    assert sum(1 for it in syms if it[0] == "line") == 2      # 화살표(몸통 + 머리)
    assert not base.run("family.place", {"family": "door.sliding", "type_values": {"width": 9000}, "host": F.W3, "offset": 2600})["ok"]   # 상한 8000(24단계 version 2)


def test_기존_요소_편집은_id를_유지한다_슬래브_경계와_구멍_솔리드_형상_교체(base):
    sq = [[0, 0], [6000, 0], [6000, 5000], [0, 5000]]
    r = base.run("slab.create", {"level": F.LV2, "boundary": sq, "thickness": 200})
    assert r["ok"], r
    sid = r["result"]["id"]
    lsh = [[0, 0], [6000, 0], [6000, 3000], [3000, 3000], [3000, 5000], [0, 5000]]
    hole = [[500, 500], [1500, 500], [1500, 1500], [500, 1500]]
    assert base.run("slab.set", {"id": sid, "boundary": lsh, "openings": [hole]})["ok"]
    p = base.run("element.get", {"id": sid})["result"]["params"]
    assert len(p["boundary"]) == 6 and len(p["openings"]) == 1
    assert base.run("slab.set", {"id": sid, "clear_openings": True})["ok"]
    assert "openings" not in base.run("element.get", {"id": sid})["result"]["params"]
    r = base.run("solid.create", {"level": F.LV2, "category": "roof", "form": "prism", "polygon": sq, "z0": 0, "z1": 300})
    assert r["ok"], r
    oid = r["result"]["id"]
    assert base.run("solid.set", {"id": oid, "form": "plate", "polygon": lsh, "thickness": 300, "base": [0, 0, 0], "pitch": 20, "dir": 90,
                                  "holes": [hole]})["ok"]
    g = base.run("element.get", {"id": oid})["result"]
    assert g["params"]["form"] == "plate" and g["ifc_class"] == "IfcRoof"


def test_문이_벽_길이를_다_차지해도_평면에_문_기호를_그린다(base):
    r = base.run("wall.create", {"level": F.LV1, "axis": [20000, 0, 20900, 0], "thickness": 150, "height": 2800})
    assert r["ok"], r
    w = r["result"]["id"]
    assert base.run("family.place", {"id": DOOR, "family": "door.single", "type_values": {"width": 900}, "host": w, "offset": 450, "rotation": 180})["ok"]
    vid = base.run("view.create", {"name": "P", "type": "plan", "level": F.LV1, "cut_height": 1200})["result"]["id"]
    base.eng.sync()
    base.eng._touch_pending()
    segs = base.eng.g.q(D.view_lines, vid)
    near = [s for s in segs if s[0] == "symbol" and 19900 <= s[1] <= 21000 and -1000 <= s[2] <= 1000]
    assert near and min(min(s[2], s[4]) for s in near) < -100     # 뒤집은 문짝은 -v(축선 오른쪽 = -y) 쪽으로 열린다
