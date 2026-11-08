"""한국 납품 규칙 시범 구현 시험.

1) 양성 대조군(규칙을 모두 지키는 합성 모델)은 어떤 검사도 위반하지 않아야 한다.
2) 규칙을 하나씩 깨는 변이는 해당 검사만 위반으로 잡혀야 한다(음성 대조).
3) 요구사항 목록·출처 목록·IDS 파일이 서로 어긋나지 않아야 한다.
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import ifcopenshell  # noqa: E402
import ifcopenshell.api.feature  # noqa: E402
import ifcopenshell.api.georeference  # noqa: E402
import ifcopenshell.api.pset  # noqa: E402
import ifcopenshell.api.root  # noqa: E402
import ifcopenshell.api.spatial  # noqa: E402
import ifcopenshell.util.element as uel  # noqa: E402

import fixtures  # noqa: E402
from krrules import catalog, codechecks, ids_build, run  # noqa: E402


def _run(f, tmp_path, name="00_본관_BA.ifc", **kw):
    p = tmp_path / name
    f.write(str(p))
    res = run.run_file(p, **kw)
    return {c["id"]: c for c in res["checks"]}, res


@pytest.fixture()
def base(tmp_path):
    f = fixtures.build_compliant()
    return f


# ---------------------------------------------------------------- 양성 대조군

def test_compliant_model_has_no_failures(base, tmp_path):
    checks, res = _run(base, tmp_path, deliverable=True)
    bad = {k: (v["status"], v["examples"][:1]) for k, v in checks.items() if v["status"] in ("fail", "warn")}
    assert not bad, bad
    # 범용객체가 없으니 KR-IDS-06 만 해당 없음이어야 한다
    assert {k for k, v in checks.items() if v["status"] == "na"} == {"KR-IDS-06"}
    assert res["summary"]["total"] == 36


def test_sample_ra01_has_known_findings():
    ra01 = ROOT.parent / "s0-baseline" / "RA-01.ifc"
    res = run.run_file(ra01)
    c = {x["id"]: x for x in res["checks"]}
    # RA-01 은 미터 단위 IFC4 이고 좌표·분류·공사코드가 없다(자체 생성 표본이라 한국 규칙을 몰랐음)
    assert c["KR-CODE-04"]["status"] == "fail"       # mm 가 아님
    assert c["KR-CODE-05"]["status"] == "pass"       # m 는 시행지침상 허용
    assert c["KR-CODE-06"]["status"] == "pass"       # 모든 객체가 층에 속함
    assert c["KR-CODE-14"]["status"] == "pass"       # 문·창 13개가 모두 벽에 속함
    assert c["KR-IDS-05"]["status"] == "fail"        # 공간 분류 없음
    assert c["KR-CODE-19"]["status"] == "fail"       # 공사코드 없음
    assert c["KR-IDS-02"]["status"] == "pass"


# ---------------------------------------------------------------- 변이(음성 대조)

def _failed(checks):
    return {k for k, v in checks.items() if v["status"] in ("fail", "warn")}


def test_unit_variants(tmp_path):
    for unit, expect_mm_fail, expect_m_fail in (("METERS", True, False), ("CENTIMETERS", True, True)):
        f = fixtures.build_compliant(unit)
        checks, _ = _run(f, tmp_path)
        assert (checks["KR-CODE-04"]["status"] == "fail") == expect_mm_fail, unit
        assert (checks["KR-CODE-05"]["status"] == "fail") == expect_m_fail, unit


def test_space_without_classification(base, tmp_path):
    sp = base.by_type("IfcSpace")[0]
    for rel in list(sp.HasAssociations):
        if rel.is_a("IfcRelAssociatesClassification"):
            base.remove(rel)
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-IDS-05", "KR-CODE-09"}


def test_bad_space_id(base, tmp_path):
    base.by_type("IfcSpace")[0].Name = "SP-101"
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-CODE-08"}


def test_missing_is_external_and_thermal(base, tmp_path):
    wall = base.by_type("IfcWall")[0]
    ps = next(p for p in uel.get_psets(wall, psets_only=True).items() if p[0] == "Pset_WallCommon")
    pset = base.by_id(ps[1]["id"])
    ifcopenshell.api.pset.edit_pset(base, pset=pset, properties={"IsExternal": None})
    door = base.by_type("IfcDoor")[0]
    dp = base.by_id(uel.get_psets(door, psets_only=True)["Pset_DoorCommon"]["id"])
    ifcopenshell.api.pset.edit_pset(base, pset=dp, properties={"ThermalTransmittance": None})
    checks, _ = _run(base, tmp_path)
    assert {"KR-IDS-08", "KR-IDS-09", "KR-IDS-10"} <= _failed(checks)  # 7종 IsExternal / 6종·5종 열관류율


def test_basement_name(base, tmp_path):
    st = next(s for s in base.by_type("IfcBuildingStorey") if s.Elevation < 0)
    st.Name = "Lower"
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-CODE-07"}


def test_detached_from_storey(base, tmp_path):
    wall = base.by_type("IfcWall")[0]
    ifcopenshell.api.spatial.unassign_container(base, products=[wall])
    checks, _ = _run(base, tmp_path)
    assert "KR-CODE-06" in _failed(checks)
    assert checks["KR-CODE-06"]["failed"] == 1


def test_door_without_host_wall(base, tmp_path):
    door = base.by_type("IfcDoor")[0]
    for rel in list(door.FillsVoids):
        base.remove(rel)
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-CODE-14"}


def test_duplicate_object(base, tmp_path):
    import ifcopenshell.api.root as r
    wall = base.by_type("IfcWall")[0]
    dup = r.copy_class(base, product=wall)
    dup.Representation = wall.Representation      # copy_class 는 형상을 복사하지 않으므로 같은 형상·위치를 공유시킴
    dup.ObjectPlacement = wall.ObjectPlacement
    checks, _ = _run(base, tmp_path)
    assert "KR-CODE-17" in _failed(checks)


def test_overlapping_spaces(base, tmp_path):
    import numpy as np
    import ifcopenshell.api.geometry as g
    s0, s1 = base.by_type("IfcSpace")
    m = np.eye(4)
    m[:3, 3] = (2500.0, 2000.0, 0.0)
    for s in (s0, s1):
        g.edit_object_placement(base, product=s, matrix=m, is_si=False)
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-CODE-10"}


def test_workcode_syntax_and_presence(base, tmp_path):
    wall = base.by_type("IfcWall")[0]
    ps = base.by_id(uel.get_psets(wall, psets_only=True)["PPS_Info"]["id"])
    ifcopenshell.api.pset.edit_pset(base, pset=ps, properties={"조달청표준공사코드": "콘크리트"})
    door = base.by_type("IfcDoor")[0]
    dps = base.by_id(uel.get_psets(door, psets_only=True)["PPS_Info"]["id"])
    ifcopenshell.api.pset.remove_pset(base, product=door, pset=dps)
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-CODE-13", "KR-CODE-19"}


def test_site_latlon_missing(base, tmp_path):
    site = base.by_type("IfcSite")[0]
    site.RefLatitude = None
    checks, _ = _run(base, tmp_path)
    assert _failed(checks) == {"KR-IDS-11"}


def test_no_georeference(base, tmp_path):
    ifcopenshell.api.georeference.remove_georeferencing(base)
    checks, _ = _run(base, tmp_path)
    assert {"KR-CODE-11", "KR-CODE-12"} <= _failed(checks)


def test_non_korean_crs_is_warning_only(base, tmp_path):
    crs = base.by_type("IfcProjectedCRS")[0]
    crs.Name = "EPSG:32760"
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-12"]["status"] == "warn"


def test_filename_rules(base, tmp_path):
    checks, _ = _run(base, tmp_path, name="RA-01.ifc", deliverable=True)
    assert checks["KR-CODE-02"]["status"] == "fail" and checks["KR-CODE-03"]["status"] == "pass"
    checks, _ = _run(base, tmp_path, name="00_본관 BA.ifc", deliverable=True)
    assert checks["KR-CODE-02"]["status"] == "fail" and checks["KR-CODE-03"]["status"] == "fail"
    checks, _ = _run(base, tmp_path, name="RA-01.ifc", deliverable=False)  # 납품 후보가 아니면 판정하지 않음
    assert checks["KR-CODE-02"]["status"] == "na"
    checks, _ = _run(base, tmp_path, name="RA-01.ifc", deliverable=True, as_name="00_본관_B.ifc")
    assert checks["KR-CODE-02"]["status"] == "pass"


def test_two_buildings_warn(base, tmp_path):
    import ifcopenshell.api.aggregate as agg
    b2 = ifcopenshell.api.root.create_entity(base, ifc_class="IfcBuilding", name="별관")
    agg.assign_object(base, products=[b2], relating_object=base.by_type("IfcSite")[0])
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-15"]["status"] == "warn"


def test_filesize_warning(base, tmp_path, monkeypatch):
    monkeypatch.setattr(codechecks, "SIZE_LIMIT_BYTES", 10)
    checks, _ = _run(base, tmp_path)
    assert checks["KR-CODE-16"]["status"] == "warn"


# ---------------------------------------------------------------- 납품 폴더

def test_package_layout(tmp_path):
    good = tmp_path / "good"
    for d in ("BIM 보고서", "IFC/건물", "원본/건물", "도면"):
        (good / d).mkdir(parents=True)
    for n in ("00_건물_BA.ifc", "00_건물_BS.ifc", "00_건물_B.ifc"):
        (good / "IFC" / "건물" / n).write_text("x")
    assert codechecks.check_package_layout(good)["status"] == "pass"
    bad = tmp_path / "bad"
    (bad / "IFC").mkdir(parents=True)
    (bad / "IFC" / "model.ifc").write_text("x")
    (bad / "IFC" / "00_건물_BA.ifc").write_text("x")
    r = codechecks.check_package_layout(bad)
    assert r["status"] == "fail"
    reasons = " ".join(e["reason"] for e in r["examples"])
    assert "원본" in reasons or r["failed"] >= 4


# ---------------------------------------------------------------- 목록 일관성

def test_committed_ids_matches_builder(tmp_path):
    out = tmp_path / "x.ids"
    ids_build.write(out)
    assert out.read_bytes() == (ROOT / "ids" / "kr_delivery_construction.ids").read_bytes()


def test_requirements_json_matches_catalog():
    data = json.loads((ROOT / "requirements.json").read_text(encoding="utf-8"))
    assert data["_meta"]["counts"]["requirements"] == len(catalog.REQUIREMENTS) >= 30
    assert data["_meta"]["counts"]["checks"] == len(catalog.CHECKS) >= 20
    ids_in_reqs = {c for r in catalog.REQUIREMENTS for c in r["checks"]}
    assert ids_in_reqs <= set(catalog.CHECKS), ids_in_reqs - set(catalog.CHECKS)
    assert set(catalog.CHECKS) <= ids_in_reqs, set(catalog.CHECKS) - ids_in_reqs  # 요구사항 없는 검사 금지
    # 파일 단위 검사 레지스트리와 일치(폴더 구성 검사 KR-CODE-18 만 따로)
    code_ids = {k for k, (m, _) in catalog.CHECKS.items() if m == "code"}
    assert code_ids - set(codechecks.CODE_CHECKS) == {"KR-CODE-18"}


def test_only_verified_sources_are_implemented():
    src = {d["id"]: d for d in json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))["documents"]}
    for r in catalog.REQUIREMENTS:
        assert r["source"] in src, r["id"]
        if r["checks"]:
            assert src[r["source"]]["source_verified"], r["id"]
    for d in src.values():
        if not d["source_verified"]:
            assert not d["used_for_implementation"], d["id"]


def test_no_absolute_paths_in_deliverables():
    for name in ("sources.json", "requirements.json"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "C:\\" not in text and "C:/" not in text and "/Users/" not in text, name
