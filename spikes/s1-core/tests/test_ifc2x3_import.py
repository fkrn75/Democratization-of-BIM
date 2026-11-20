# SPDX-License-Identifier: MPL-2.0
"""W1 1-F 5차(IO-03): DoBIM 이 내보내지 않은 IFC2x3 원본 가져오기 표본 시험.

표본은 tests/ifc2x3_samples.py 가 직접 만든다(외부 파일·현장 자료 없음): IfcWallStandardCase + 재료 층 사용, IfcDoorStyle·IfcWindowStyle,
개구부 채움, IfcFlowTerminal + IfcSanitaryTerminalType, IfcFurnishingElement, 공간, 층 2개, IfcOwnerHistory.
- 표본 자체가 IFC2x3 스키마 검증(EXPRESS 규칙 포함)을 통과하고, IFC4 로 올릴 때 버리는 개체가 없다
- 가져오면 모든 IfcProduct 가 외래 요소가 되고 GlobalId·클래스·속성셋이 남는다
- 길이 단위 mm 표본과 m 표본은 같은 도면(선분·SVG 해시)이 나온다
- 1층 평면에 벽 절단선·문·창 기호·공간 실명, 2층 평면의 비내력벽은 cutn
- 같은 파일을 두 번 가져오면 해시가 같고, IFC4 로 내보내 다시 가져와도 도면이 같다"""
import collections
import shutil
import sys
import tempfile
from pathlib import Path

import ifcopenshell
import ifcopenshell.validate
import pytest

import ifc2x3_samples as S
from dobim_core import dispatch
from dobim_core import drawing as D
from dobim_core import ifc2x3 as X

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "spikes" / "phase1-drawings"))
import foreign_corpus as FC  # noqa: E402

CLASSES = {"IfcWallStandardCase": 5, "IfcBuildingStorey": 2, "IfcOpeningElement": 2, "IfcSpace": 1, "IfcSite": 1, "IfcBuilding": 1, "IfcSlab": 1,
           "IfcDoor": 1, "IfcWindow": 1, "IfcColumn": 1, "IfcFlowTerminal": 1, "IfcFurnishingElement": 1}


def _imp(ws: Path, unit: str, name: str = "s.ifc"):
    gids = S.write(ws / name, unit)
    env = dispatch.Env(ws, "t")
    r = dispatch.run(env, "ifc.import", {"source": name, "path": FC.PROJECT})
    assert r["ok"], r
    return env, r["result"], gids


@pytest.fixture
def mm(tmp_path):
    env, res, gids = _imp(tmp_path, "mm")
    yield env, res, gids
    env.close()


def _by_name(eng):
    return {eng.st.el[e]["params"].get("name"): e for e in eng.st.by_kind["foreign"]}


def test_표본은_IFC2x3_스키마_검증을_통과하고_IFC4_로_빠짐없이_올라간다(tmp_path):
    for unit in ("mm", "m"):
        S.write(tmp_path / f"{unit}.ifc", unit)
        f = ifcopenshell.open(str(tmp_path / f"{unit}.ifc"))
        assert f.schema == "IFC2X3"
        lg = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(f, lg, express_rules=True)
        assert [x["message"] for x in lg.statements if x["level"] in ("error", "critical")] == []
        assert {e.is_a() for e in f.by_type("IfcTypeObject")} == {"IfcDoorStyle", "IfcWindowStyle", "IfcSanitaryTerminalType"}
        up, warns = X.to_ifc4(f)
        assert warns == [] and up.schema == "IFC4"
        assert sorted(e.GlobalId for e in up.by_type("IfcProduct")) == sorted(e.GlobalId for e in f.by_type("IfcProduct"))


def test_가져오면_모든_제품이_외래_요소가_되고_클래스와_속성이_남는다(mm):
    env, res, gids = mm
    assert res["schema"] == "IFC2X3" and res["elements"] == {"authored": 0, "foreign": len(gids), "detached": 0}
    assert any("IFC2x3" in w for w in res["warnings"])
    eng = env.engine(FC.PROJECT)
    assert set(eng.st.by_kind["foreign"]) == set(gids)
    assert dict(collections.Counter(eng.st.el[e]["ifc_class"] for e in eng.st.by_kind["foreign"])) == CLASSES
    by = _by_name(eng)
    r = dispatch.run(env, "element.get", {"id": by["벽 남"]}, project=FC.PROJECT)
    assert r["ok"], r
    props = r["result"].get("psets") or r["result"].get("properties") or {}
    assert props.get("Pset_WallCommon", {}).get("Reference") == "W200" and props["Pset_WallCommon"]["LoadBearing"] is True
    r = dispatch.run(env, "element.get", {"id": by["2층 벽"]}, project=FC.PROJECT)
    assert (r["result"].get("psets") or r["result"].get("properties"))["Pset_WallCommon"]["LoadBearing"] is False


def test_길이_단위가_mm_이든_m_이든_도면이_같다(tmp_path):
    out = {}
    for unit in ("mm", "m"):
        ws = tmp_path / unit
        ws.mkdir()
        env, _res, _g = _imp(ws, unit)
        try:
            out[unit] = FC.render_project(env)
            eng = env.engine(FC.PROJECT)
            wall = _by_name(eng)["벽 남"]
            bb = eng.g.q(D.foreign_info, wall)[5]
            assert bb[3] - bb[0] == pytest.approx(6000, abs=1e-3) and bb[5] - bb[2] == pytest.approx(3000, abs=1e-3)   # 길이는 mm 로 읽는다
        finally:
            env.close()
    assert out["mm"] == out["m"]
    assert set(out["mm"]) == {"plan-0", "plan-1", "elev-N", "elev-E", "elev-S", "elev-W", "sec-x", "sec-y"}


def test_1층_평면은_벽_절단선_문창_기호_실명_2층_비내력벽은_cutn(mm):
    env, _res, _g = mm
    views = FC.render_project(env)
    p0, p1 = views["plan-0"], views["plan-1"]
    assert p0["by_class"]["cut"] > 0 and p0["by_class"].get("symbol", 0) > 0 and p0["fills"] > 0 and p0["omitted"] == {"foreign": 0, "detached": 0}
    assert p0["labels"] >= 1   # 공간 101 의 실명
    eng = env.engine(FC.PROJECT)
    cats = {eng.st.el[e]["ifc_class"]: eng.g.q(D.foreign_info, e)[0] for e in eng.g.q(D.all_foreign)}
    assert cats["IfcWallStandardCase"] == "heavy" and cats["IfcSlab"] == "heavy" and cats["IfcDoor"] == "medium" and cats["IfcWindow"] == "medium"
    assert p1["cutn"] > 0 and p1["by_class"]["cut"] == p1["cutn"]   # 2층에는 LoadBearing 거짓 벽 하나뿐


def test_같은_파일을_두_번_가져오면_해시가_같다(tmp_path):
    hs = []
    S.write(tmp_path / "s.ifc", "m")
    for i in range(2):
        ws = tmp_path / f"w{i}"
        ws.mkdir()
        shutil.copyfile(tmp_path / "s.ifc", ws / "s.ifc")
        env = dispatch.Env(ws, "t")
        try:
            assert dispatch.run(env, "ifc.import", {"source": "s.ifc", "path": FC.PROJECT, "guid": "5d0b1a50-0000-4a00-8000-0d0b1a5000fb"})["ok"]
            h = env.engine(FC.PROJECT).hashes()
            hs.append((h["state"], h["derived"], h["ifc"]))
        finally:
            env.close()
    assert hs[0] == hs[1]


def test_IFC4_로_내보내_다시_가져와도_도면이_같다(mm, tmp_path):
    env, _res, _g = mm
    v1 = FC.render_project(env)
    r = FC._destructive(env, "ifc.export", {"path": "a.ifc"})
    assert r["ok"], r
    ws2 = Path(tempfile.mkdtemp(dir=tmp_path))
    env2 = dispatch.Env(ws2, "t")
    try:
        FC._import(env2, env.workspace / "a.ifc", "a.ifc")
        v2 = {k: v for k, v in FC.render_project(env2).items()}
        assert {k: v["lines_sha256"] for k, v in v1.items()} == {k: v["lines_sha256"] for k, v in v2.items()}
    finally:
        env2.close()
