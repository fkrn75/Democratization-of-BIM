# SPDX-License-Identifier: MPL-2.0
"""21단계(실무 모델링 3): 설비 기기·단말 범주(solid category). 합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import fixture as F
from dobim_core import solids as SO

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

BOX = {"level": F.LV1, "form": "prism", "polygon": [[0, 0], [600, 0], [600, 400], [0, 400]], "z0": 0, "z1": 900}

MEP = {
    "air_terminal": ("IfcAirTerminal", None), "fan": ("IfcFan", None), "boiler": ("IfcBoiler", None),
    "unitary_equipment": ("IfcUnitaryEquipment", None), "tank": ("IfcTank", None), "valve": ("IfcValve", None),
    "flow_meter": ("IfcFlowMeter", None), "gas_meter": ("IfcFlowMeter", "GASMETER"), "pipe_fitting": ("IfcPipeFitting", None),
    "sanitary_terminal": ("IfcSanitaryTerminal", None), "fire_terminal": ("IfcFireSuppressionTerminal", None),
    "fire_extinguisher": ("IfcFireSuppressionTerminal", "USERDEFINED"), "escape_device": ("IfcBuildingElementProxy", "USERDEFINED"),
    "electric_board": ("IfcElectricDistributionBoard", None), "control": ("IfcUnitaryControlElement", None),
}


def test_설비_범주는_IFC4_클래스와_형식으로_정해진다():
    f = ifcopenshell.file(schema="IFC4")
    for cat, (cls, pre) in MEP.items():
        c, p, _ = SO.SOLID_CATEGORIES[cat]
        assert (c, p) == (cls, pre)
        e = f.create_entity(c)        # IFC4 스키마에 있는 클래스·형식 값인가
        if p:
            e.PredefinedType = p


def test_설비_기기를_만들면_범주의_IFC_클래스로_나가고_실행취소로_돌아간다(base, tmp_path):
    h0 = base.eng.state_hash()
    ids = {}
    for cat in MEP:
        r = base.run("solid.create", {**BOX, "name": cat, "category": cat})
        assert r["ok"], r
        ids[cat] = r["result"]["id"]
        assert base.run("element.get", {"id": ids[cat]})["result"]["ifc_class"] == MEP[cat][0]
    # 범주를 바꾸면 IFC 클래스가 달라지므로 거절(기존 규칙 그대로)
    assert not base.run("solid.set", {"id": ids["valve"], "category": "boiler"})["ok"]
    base.eng.flush_ifc()
    out = tmp_path / "m.ifc"
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    assert f.by_guid(ids["gas_meter"]).PredefinedType == "GASMETER"
    ext = f.by_guid(ids["fire_extinguisher"])
    assert ext.PredefinedType == "USERDEFINED" and ext.ObjectType == "FIRE_EXTINGUISHER"
    assert f.by_guid(ids["escape_device"]).ObjectType == "ESCAPE_DEVICE"
    assert f.by_guid(ids["boiler"]).Representation.Representations[0].Items[0].is_a("IfcFacetedBrep")
    for _ in MEP:
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_관_모양_설비_단말도_부재_형태로_만든다(base):
    r = base.run("solid.create", {"level": F.LV1, "name": "v", "category": "valve", "form": "member", "anchor": "center",
                                  "start": [0, 0, 500], "end": [150, 0, 500], "section": {"shape": "circle", "width": 80, "depth": 80}})
    assert r["ok"], r
    assert base.run("element.get", {"id": r["result"]["id"]})["result"]["ifc_class"] == "IfcValve"


def test_설비_기기_IFC_가져오기_왕복_해시(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        for i, cat in enumerate(("boiler", "sanitary_terminal", "gas_meter", "fire_extinguisher")):
            assert b.run("solid.create", {**BOX, "id": "f" + "0" * 20 + str(i + 1), "name": cat, "category": cat})["ok"]
        h = b.hashes()
        b.export("r.ifc")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        o = b.origins("imp.dobim")
        assert o["foreign"] == 0 and o["detached"] == 0, o
        g = b.run("element.get", {"id": "f" + "0" * 20 + "3"}, project="imp.dobim")["result"]
        assert g["origin"] == "authored" and g["params"]["category"] == "gas_meter"
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()
