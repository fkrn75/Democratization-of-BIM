# SPDX-License-Identifier: MPL-2.0
"""W1 IFC2x3 TC1 내보내기·읽기: 스키마 검증, 다시 읽은 요소 수 일치, 클래스 대체·손실 경고, IFC4 출력 바이트 불변.
합성 데이터만 쓴다(기준 프로젝트 fixture.SCRIPT + 범주별 요소)."""
import collections

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from dobim_core import fixture as F
from dobim_core import globalid as GI
from dobim_core import ifc2x3 as X


def gid(c, n):
    return c + f"{n:021d}"


def ok(sess, name, args):
    r = sess.run(name, args)
    assert r["ok"], r
    return r["result"]


def export(sess, args):
    r = sess.run("ifc.export", args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    r = sess.run("ifc.export", args, confirm=r["error"]["details"]["confirm_token"])
    assert r["ok"], r
    return r


@pytest.fixture
def rich(base):
    s = base
    ok(s, "slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200,
                          "openings": [[[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]]})
    ok(s, "space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[0, 0], [3000, 0], [3000, 4000], [0, 4000]], "name": "101", "category": "거실"})
    ok(s, "stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18})
    ok(s, "railing.create", {"id": F.RL1, "level": F.LV1, "path": [[0, 0], [3000, 0], [3000, 4000]], "height": 1100})
    ok(s, "roof.create", {"id": F.RF1, "level": F.LV2, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "form": "gable", "pitch": 30, "overhang": 0})
    ok(s, "elevator.create", {"id": F.EL1, "level": F.LV1, "shaft": [4000, 500, 5800, 2300], "rise": 3300})
    ok(s, "site.create", {"id": F.SI1, "boundary": [[-6000, -6000], [18000, -6000], [18000, 14000], [-6000, 14000]], "name": "대지"})
    for i, cat in enumerate(("toilet", "air_terminal", "smoke_detector", "terrain", "column", "pipe")):
        if cat == "pipe":
            ok(s, "solid.create", {"id": gid("f", i + 11), "level": F.LV1, "category": cat, "form": "member", "start": [0, 0, 2500], "end": [2000, 0, 2500],
                                   "section": {"shape": "rect", "width": 100, "depth": 100}})
        else:
            ok(s, "solid.create", {"id": gid("f", i + 11), "level": F.LV1, "category": cat, "form": "prism",
                                   "polygon": [[0, 0], [300, 0], [300, 300], [0, 300]], "z0": 0, "z1": 400})
    ok(s, "family.place", {"family": "window.framed", "host": F.W2, "offset": 2000, "elevation": 900})
    ok(s, "family.place", {"family": "wardrobe.builtin", "level": F.LV1, "position": [2000, 3000]})
    return s


def _errors(f, express):
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=express)
    # 기준 프로젝트의 시험 id(예: 8000...01)는 128비트 범위를 넘는 22자라 GlobalId 규칙에 걸린다: IFC4 에서도 같다(내보내기와 무관)
    return [s["message"] for s in lg.statements if s["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in s["message"]]


def test_IFC2x3_내보내기는_스키마_검증을_통과하고_요소_수가_같다(rich):
    r4 = export(rich, {"path": "m4.ifc"})
    r2 = export(rich, {"path": "m2.ifc", "schema": "IFC2X3"})
    ws = rich.path.parent
    f4, f2 = ifcopenshell.open(str(ws / "m4.ifc")), ifcopenshell.open(str(ws / "m2.ifc"))
    assert f4.schema == "IFC4" and f2.schema == "IFC2X3"
    assert _errors(f2, False) == [] and _errors(f2, True) == []
    g4 = collections.Counter(e.GlobalId for e in f4.by_type("IfcProduct"))
    g2 = collections.Counter(e.GlobalId for e in f2.by_type("IfcProduct"))
    assert g4 == g2 and len(g4) > 20
    assert len(f4.by_type("IfcPropertySet")) == len(f2.by_type("IfcPropertySet"))
    assert len(f2.by_type("IfcOwnerHistory")) == 1 and all(e.OwnerHistory is not None for e in f2.by_type("IfcRoot"))
    assert "schema" not in r4["result"] and r2["result"]["schema"] == "IFC2X3"
    assert any("IFC2x3" in w for w in r2["warnings"])


def test_IFC4_에_없는_클래스는_대체하고_원래_분류를_ObjectType_에_남긴다(rich):
    r = export(rich, {"path": "m2.ifc", "schema": "IFC2X3"})["result"]
    f2 = GI.open_exported(rich.path.parent / "m2.ifc")   # 첫 글자가 무효한 id 는 파일에서 바뀐다(W2): 원래 id 로 되돌려 연다
    by = {e.GlobalId: e for e in f2.by_type("IfcProduct")}
    assert by[gid("f", 11)].is_a() == "IfcFlowTerminal" and by[gid("f", 11)].ObjectType == "IfcSanitaryTerminal.TOILETPAN"
    assert by[gid("f", 12)].is_a() == "IfcFlowTerminal" and "IfcAirTerminal" in by[gid("f", 12)].ObjectType
    assert by[gid("f", 13)].is_a() == "IfcDistributionControlElement" and "SMOKESENSOR" in by[gid("f", 13)].ObjectType
    assert by[gid("f", 14)].is_a() == "IfcBuildingElementProxy"
    assert by[gid("f", 15)].is_a() == "IfcColumn"
    assert by[gid("f", 16)].is_a() == "IfcFlowSegment"
    assert len(f2.by_type("IfcWindowStyle")) == 1 and len(f2.by_type("IfcFurnishingElement")) >= 1
    kinds = {(x["class"], x["loss"]) for x in r["losses"]}
    assert ("IfcSanitaryTerminal", "클래스 -> IfcFlowTerminal") in kinds
    assert ("IfcWindowType", "클래스 -> IfcWindowStyle") in kinds
    assert r["losses_total"] == len(r["losses"])


def test_IFC4_출력은_바이트_불변(rich):
    export(rich, {"path": "a.ifc"})
    b1 = (rich.path.parent / "a.ifc").read_bytes()
    export(rich, {"path": "b.ifc", "schema": "IFC2X3"})
    export(rich, {"path": "c.ifc"})
    assert (rich.path.parent / "c.ifc").read_bytes() == b1


def test_IFC2x3_는_같은_모델이면_같은_내용이다(rich):
    export(rich, {"path": "x1.ifc", "schema": "IFC2X3"})
    export(rich, {"path": "x2.ifc", "schema": "IFC2X3"})
    body = [(rich.path.parent / n).read_text(encoding="utf-8").split("DATA;", 1)[1] for n in ("x1.ifc", "x2.ifc")]
    assert body[0] == body[1]


def test_투영은_IFC4_전용_형상을_쓰지_않는다(rich):
    rich.eng.flush_ifc()
    assert X.ifc4_only_geometry(rich.eng.ifc.f) == []


def test_IFC2x3_를_다시_가져온다(rich, tmp_path):
    export(rich, {"path": "m2.ifc", "schema": "IFC2X3"})
    export(rich, {"path": "m4.ifc"})
    r = rich.run("ifc.import", {"source": "m2.ifc", "path": "back.dobim", "guid": "5d0b1a50-0000-4a00-8000-0d0b1a5000f9"})
    assert r["ok"], r
    r4 = rich.run("ifc.import", {"source": "m4.ifc", "path": "back4.dobim", "guid": "5d0b1a50-0000-4a00-8000-0d0b1a5000fa"})
    assert r4["ok"], r4
    res = r["result"]
    assert res["schema"] == "IFC2X3" and r4["result"]["schema"] == "IFC4"
    assert any("IFC2x3" in w for w in res["warnings"])
    # 다시 읽은 요소 수 = IFC4 로 가져온 요소 수(분류는 클래스 대체로 authored 가 detached·foreign 으로 바뀔 수 있다)
    assert sum(res["elements"].values()) == sum(r4["result"]["elements"].values())
    f0 = ifcopenshell.open(str(rich.path.parent / "m2.ifc"))
    up, warns = X.to_ifc4(f0)
    assert warns == [] and up.schema == "IFC4"
    assert {e.GlobalId for e in up.by_type("IfcProduct")} == {e.GlobalId for e in f0.by_type("IfcProduct")}
