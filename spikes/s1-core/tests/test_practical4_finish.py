# SPDX-License-Identifier: MPL-2.0
"""22단계(실무 모델링 4) 24.2: 마감(천장·바닥·외장·단열)과 소방 전기(감지기·수신반·조명·유도등) 범주. 합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import fixture as F
from dobim_core import solids as SO

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

BOX = {"level": F.LV1, "form": "prism", "polygon": [[0, 0], [600, 0], [600, 400], [0, 400]], "z0": 2300, "z1": 2400}
# 구멍 있는 수평 판(pitch 0): 천장(윗면 2310, 두께 10) 가운데에 점검구 자리 구멍
CEIL = {"level": F.LV1, "form": "plate", "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]], "holes": [[[1000, 1000], [1600, 1000], [1600, 1600], [1000, 1600]]],
        "thickness": 10, "base": [0, 0, 2310], "pitch": 0, "dir": 0}

NEW = {
    "ceiling": ("IfcCovering", "CEILING"), "flooring": ("IfcCovering", "FLOORING"), "cladding": ("IfcCovering", "CLADDING"),
    "insulation": ("IfcCovering", "INSULATION"), "smoke_detector": ("IfcSensor", "SMOKESENSOR"), "heat_detector": ("IfcSensor", "HEATSENSOR"),
    "alarm_panel": ("IfcUnitaryControlElement", "ALARMPANEL"), "light_fixture": ("IfcLightFixture", None), "exit_sign": ("IfcLightFixture", "SECURITYLIGHTING"),
}


def test_새_범주는_IFC4_클래스와_형식으로_정해진다():
    f = ifcopenshell.file(schema="IFC4")
    for cat, (cls, pre) in NEW.items():
        c, p, _ = SO.SOLID_CATEGORIES[cat]
        assert (c, p) == (cls, pre)
        e = f.create_entity(c)        # IFC4 스키마에 있는 클래스·형식 값인가
        if p:
            e.PredefinedType = p
    assert SO.SOLID_CATEGORIES["covering"] == ("IfcCovering", "MOLDING", None)   # 기존 범주는 그대로


def test_마감_소방_요소를_만들면_범주의_클래스로_나가고_실행취소로_돌아간다(base, tmp_path):
    h0 = base.eng.state_hash()
    ids = {}
    for cat in NEW:
        args = {**CEIL, "name": cat, "category": cat} if cat in ("ceiling", "flooring") else {**BOX, "name": cat, "category": cat}
        r = base.run("solid.create", args)
        assert r["ok"], r
        ids[cat] = r["result"]["id"]
        assert base.run("element.get", {"id": ids[cat]})["result"]["ifc_class"] == NEW[cat][0]
    assert not base.run("solid.set", {"id": ids["ceiling"], "category": "smoke_detector"})["ok"]   # 클래스가 바뀌는 범주 변경은 거절
    base.eng.flush_ifc()
    out = tmp_path / "m.ifc"
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    for cat, (cls, pre) in NEW.items():
        e = f.by_guid(ids[cat])
        assert e.is_a(cls) and (pre is None or e.PredefinedType == pre)
    zs = sorted({round(p.Coordinates[2], 3) for p in f.by_guid(ids["ceiling"]).Representation.Representations[0].Items[0].Outer.CfsFaces[0].Bounds[0].Bound.Polygon})
    assert zs == [2310.0]
    for _ in NEW:
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_왕복하면_범주와_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        for cat in NEW:
            args = {**CEIL, "name": cat, "category": cat} if cat in ("ceiling", "flooring") else {**BOX, "name": cat, "category": cat}
            assert b.run("solid.create", args)["ok"]
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim")["foreign"] == 0 and b.origins("imp.dobim")["detached"] == 0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


def test_렌더는_새_클래스를_설비_묶음으로_본다(tmp_path):
    sys.path.insert(0, str(ROOT.parent / "s10-headless" / "src"))
    from dobim_render import raster
    assert "IfcSensor" in raster.PALETTE and "IfcLightFixture" in raster.PALETTE
    schema = ifcopenshell.schema_by_name("IFC4")
    for cls in ("IfcSensor", "IfcLightFixture", "IfcUnitaryControlElement"):
        d = schema.declaration_by_name(cls)
        chain = []
        while d is not None:
            chain.append(d.name())
            d = d.supertype()
        assert "IfcDistributionElement" in chain
