"""검수 반영 시험: 조건부(에너지) 규칙 분리, IDS 오탐 줄이기, 망가진 입력, 폴더 검사 CLI, 출처 표기.

test_krrules.py 의 양성 대조군(규칙을 모두 지키는 합성 모델)에 변이를 가해 확인한다.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import ifcopenshell  # noqa: E402
import ifcopenshell.api.pset  # noqa: E402
import ifcopenshell.util.element as uel  # noqa: E402

import fixtures  # noqa: E402
from krrules import catalog, codechecks, run  # noqa: E402


@pytest.fixture()
def base():
    return fixtures.build_compliant()


def _run(f, tmp_path, name="00_본관_BA.ifc", **kw):
    p = tmp_path / name
    f.write(str(p))
    res = run.run_file(p, **kw)
    return {c["id"]: c for c in res["checks"]}, res


# ---------------------------------------------------------------- 에너지 검토 조건부 규칙

def _break_energy_props(f):
    wall = f.by_type("IfcWall")[0]
    wp = f.by_id(uel.get_psets(wall, psets_only=True)["Pset_WallCommon"]["id"])
    ifcopenshell.api.pset.edit_pset(f, pset=wp, properties={"IsExternal": None, "ThermalTransmittance": None})


def test_energy_rules_are_off_by_default_and_separated(base, tmp_path):
    _break_energy_props(base)
    checks, res = _run(base, tmp_path)   # --energy 없음
    for cid in ("KR-IDS-08", "KR-IDS-09", "KR-IDS-10"):
        assert checks[cid]["status"] == "na" and checks[cid]["conditional_off"], cid
        assert checks[cid]["severity"] == "조건부"
    s = res["summary"]
    assert s.get("fail", 0) == 0 and s["conditional"] == {"enabled": False, "na": 3}


def test_energy_rules_counted_apart_from_failures(base, tmp_path):
    _break_energy_props(base)
    checks, res = _run(base, tmp_path, energy=True)
    assert checks["KR-IDS-08"]["status"] == "fail"
    s = res["summary"]
    # 조건부 위반은 일반 fail 집계에 섞이지 않고 conditional 에만 센다
    assert s.get("fail", 0) == 0 and s["conditional"]["enabled"] is True and s["conditional"]["fail"] >= 1


def test_energy_flag_via_cli(base, tmp_path):
    p = tmp_path / "00_본관_BA.ifc"
    base.write(str(p))
    out = tmp_path / "r.json"
    assert run.main([str(p), "--energy", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["energy_mode"] is True


# ---------------------------------------------------------------- IDS 오탐 줄이기(dataType·Pset 이름)

def _props(f, name):
    return [p for p in f.by_type("IfcPropertySingleValue") if p.Name == name]


def test_logical_and_real_value_types_are_accepted(base, tmp_path):
    # 내보내기 도구에 따라 IsExternal 이 IfcLogical, 열관류율이 IfcReal 로 나온다. 값이 있으면 위반이 아니다
    for p in _props(base, "IsExternal"):
        p.NominalValue = base.createIfcLogical(True)
    for p in _props(base, "ThermalTransmittance"):
        p.NominalValue = base.createIfcReal(0.2)
    checks, _ = _run(base, tmp_path, energy=True)
    for cid in ("KR-IDS-08", "KR-IDS-09", "KR-IDS-10"):
        assert checks[cid]["status"] == "pass", (cid, checks[cid]["examples"])
        assert checks[cid]["applicable"] >= 1


def test_value_in_custom_pset_is_tolerated(base, tmp_path):
    # Pset_*Common 이 아닌 사용자 정의 Pset 에 값이 있어도 통과(실행기가 재판정)
    for w in base.by_type("IfcWall"):
        base.by_id(uel.get_psets(w, psets_only=True)["Pset_WallCommon"]["id"]).Name = "회사_외피"
    checks, _ = _run(base, tmp_path, energy=True)
    for cid in ("KR-IDS-08", "KR-IDS-09", "KR-IDS-10"):
        assert checks[cid]["status"] == "pass", (cid, checks[cid]["examples"])
    assert checks["KR-IDS-08"]["tolerated"] == 2
    assert checks["KR-IDS-09"]["applicable"] == 4          # 벽 2개 + 문 2개가 모두 대상


def test_custom_pset_wall_missing_thermal_is_still_caught(base, tmp_path):
    # 사용자 정의 Pset 의 IsExternal=TRUE 벽은 대상에 들어오고, 열관류율이 없으면 위반
    wall = base.by_type("IfcWall")[0]
    ps = base.by_id(uel.get_psets(wall, psets_only=True)["Pset_WallCommon"]["id"])
    ps.Name = "회사_외피"
    ifcopenshell.api.pset.edit_pset(base, pset=ps, properties={"ThermalTransmittance": None})
    checks, _ = _run(base, tmp_path, energy=True)
    assert checks["KR-IDS-09"]["status"] == "fail" and checks["KR-IDS-09"]["failed"] == 1
    assert checks["KR-IDS-08"]["status"] == "pass"


def test_multi_facet_failures_are_merged_in_examples(base, tmp_path):
    site = base.by_type("IfcSite")[0]
    site.RefLatitude = None
    site.RefLongitude = None
    checks, _ = _run(base, tmp_path)
    ex = checks["KR-IDS-11"]["examples"]
    assert checks["KR-IDS-11"]["failed"] == 1 and len(ex) == 1     # 같은 대지가 두 번 나오지 않음
    assert "RefLatitude" in ex[0]["reason"] and "RefLongitude" in ex[0]["reason"]


def test_result_json_is_byte_reproducible_across_hash_seeds(tmp_path):
    ra01 = ROOT.parent / "s0-baseline" / "RA-01.ifc"
    outs = []
    for seed in ("1", "2"):
        out = tmp_path / f"r{seed}.json"
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8")
        subprocess.run([sys.executable, "-m", "krrules.run", str(ra01), "--energy", "--out", str(out)],
                       cwd=ROOT, env=env, check=True, capture_output=True)
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]


# ---------------------------------------------------------------- 망가진 입력

STEP_HEAD = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x'),'2;1');\nFILE_NAME('x.ifc','',(''),(''),'','','');\n"
             "FILE_SCHEMA(('%s'));\nENDSEC;\nDATA;\n")
PROJECT = "#1=IFCPROJECT('0YvctVUKr0kugbFTf53O9L',$,'P',$,$,$,$,$,$);\n"


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return p


@pytest.mark.parametrize("name,content,code", [
    ("empty.ifc", b"", "EMPTY_FILE"),
    ("garbage.ifc", "이건 IFC 가 아님\n".encode("utf-8"), "NOT_STEP"),
    ("cut.ifc", STEP_HEAD % "IFC4" + PROJECT, "TRUNCATED"),
    ("nohdr.ifc", "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n", "NO_FILE_SCHEMA"),
    ("bad.ifczip", b"PK not really a zip", "OPEN_FAILED"),
])
def test_unreadable_inputs_give_error_json_and_nonzero_exit(tmp_path, name, content, code):
    p = _write(tmp_path, name, content)
    out = tmp_path / "e.json"
    assert run.main([str(p), "--out", str(out)]) == 2
    text = out.read_text(encoding="utf-8")
    res = json.loads(text)
    assert res["status"] == "error" and res["error"]["code"] == code and res["file"] == name
    assert str(tmp_path) not in text      # 절대 경로를 쓰지 않음


def test_missing_file_is_input_error(tmp_path):
    out = tmp_path / "e.json"
    assert run.main([str(tmp_path / "없음.ifc"), "--out", str(out)]) == 2
    assert json.loads(out.read_text(encoding="utf-8"))["error"]["code"] == "NOT_FOUND"


def test_ifc2x2_is_reported_as_kr_code_01_violation(tmp_path):
    body = STEP_HEAD % "IFC2X2_FINAL" + PROJECT + "ENDSEC;\nEND-ISO-10303-21;\n"
    p = _write(tmp_path, "old.ifc", body)
    out = tmp_path / "r.json"
    assert run.main([str(p), "--out", str(out)]) == 0    # 결과를 만들었으므로 0, 위반은 JSON 에
    res = json.loads(out.read_text(encoding="utf-8"))
    c = {x["id"]: x for x in res["checks"]}
    assert res["schema"] == "IFC2X2_FINAL" and res["unreadable_schema"] is True
    assert c["KR-CODE-01"]["status"] == "fail" and "IFC2X2_FINAL" in c["KR-CODE-01"]["examples"][0]["reason"]
    assert c["KR-CODE-04"]["status"] == "na" and "열 수 없어" in c["KR-CODE-04"]["note"]
    assert c["KR-IDS-01"]["status"] == "na"
    assert res["summary"]["fail"] == 1


def test_corrupt_data_lines_are_recorded_not_fatal(tmp_path):
    body = (STEP_HEAD % "IFC4" + "#1=IFCPROJECT('0YvctVUKr0kugbFTf53O9L',$,'P',$,$,$,$,$,$\n#2=garbage((((;\n"
            "ENDSEC;\nEND-ISO-10303-21;\n")
    res = run.run_file(_write(tmp_path, "c.ifc", body))
    assert res["parse_errors"]["count"] >= 1 and str(tmp_path) not in json.dumps(res, ensure_ascii=False)


def test_one_crashing_check_becomes_error_status(base, tmp_path, monkeypatch):
    def boom(f, **_):
        raise ValueError("의도한 오류")
    monkeypatch.setitem(codechecks.CODE_CHECKS, "KR-CODE-06", boom)
    checks, res = _run(base, tmp_path)
    assert checks["KR-CODE-06"]["status"] == "error" and "ValueError" in checks["KR-CODE-06"]["note"]
    assert checks["KR-CODE-07"]["status"] != "error"        # 다른 검사는 계속 돈다
    assert res["summary"]["error"] == 1


# ---------------------------------------------------------------- 납품 폴더(CLI)

def test_package_check_via_cli(tmp_path):
    good = tmp_path / "납품"
    for d in ("BIM 보고서", "IFC/건물", "원본/건물", "도면"):
        (good / d).mkdir(parents=True)
    for n in ("00_건물_BA.ifc", "00_건물_B.ifc"):
        (good / "IFC" / "건물" / n).write_text("x")
    out = tmp_path / "p.json"
    assert run.main(["--package", str(good), "--out", str(out)]) == 0
    res = json.loads(out.read_text(encoding="utf-8"))
    assert res["package"] == "납품" and res["checks"][0]["id"] == "KR-CODE-18" and res["checks"][0]["status"] == "pass"
    assert run.main(["--package", str(tmp_path / "없는폴더"), "--out", str(out)]) == 2


def test_package_check_merges_into_file_result(base, tmp_path):
    pkg = tmp_path / "빈폴더"
    pkg.mkdir()
    p = tmp_path / "00_본관_BA.ifc"
    base.write(str(p))
    res = run.run_file(p, package=pkg)
    ids = [c["id"] for c in res["checks"]]
    assert ids.count("KR-CODE-18") == 1 and res["summary"]["total"] == 37 == len(catalog.CHECKS)
    assert set(ids) == set(catalog.CHECKS)       # 37개 검사 전부 CLI 로 실행 가능


# ---------------------------------------------------------------- 출처 표기

def test_source_clause_labels():
    req = {r["id"]: r for r in catalog.REQUIREMENTS}
    assert req["REQ-010"]["clause"] == "5.2.2(3)다, 5.2.2(4)나" and req["REQ-010"]["page"] == "84-85"
    assert req["REQ-028"]["proxy_indicator"] is True and "대리 지표" in req["REQ-028"]["summary"]
    assert req["REQ-028"]["interpretation"] is True
    # 대리 지표는 이 하나뿐이어야 한다(다른 요구사항에 조용히 붙지 않게)
    assert [k for k, v in req.items() if v["proxy_indicator"]] == ["REQ-028"]
    assert "4.x" not in (ROOT / "krrules" / "codechecks.py").read_text(encoding="utf-8")


def test_no_internal_work_phrases_in_public_files():
    for name in ("sources.json", "requirements.json"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for phrase in ("이전 작업자", "자동 심사", "이 PC"):
            assert phrase not in text, (name, phrase)
