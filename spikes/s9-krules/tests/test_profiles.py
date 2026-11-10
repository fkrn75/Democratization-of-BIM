"""납품처별 프로파일(조달청 기본 / 세움터 초안) 시험.

(a) 세움터식 합성 모델이 seumteo 에서 통과하고 procurement 에서는 기존대로 판정된다.
(b) 기본 프로파일 결과는 프로파일 도입 전과 같다(저장소의 결과 파일과 바이트 단위 비교).
(c) 세움터 규칙의 진짜 위반이 잡힌다.
(d) 알 수 없는 프로파일 이름은 오류 JSON 이다.
"""
import json
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import ifcopenshell.api.geometry  # noqa: E402
import ifcopenshell.api.pset  # noqa: E402
import ifcopenshell.api.root  # noqa: E402
import ifcopenshell.api.aggregate  # noqa: E402

import fixtures  # noqa: E402
from krrules import catalog, codechecks, profiles, run, seumteo  # noqa: E402

SEU_NAME = "OO프로젝트_본관_BA_ALL.ifc"


def _write(f, tmp_path, name=SEU_NAME):
    p = tmp_path / name
    f.write(str(p))
    return p


def _checks(f, tmp_path, name=SEU_NAME, profile="seumteo", **kw):
    p = _write(f, tmp_path, name)
    res = run.run_file(p, profile=profile, **kw)
    return {c["id"]: c for c in res["checks"]}, res


@pytest.fixture()
def seu():
    return fixtures.build_seumteo()


def _bad(checks):
    return {k: (v["status"], [e.get("reason") for e in v["examples"][:1]]) for k, v in checks.items() if v["status"] in ("fail", "warn", "error")}


def _space(f, name):
    return next(s for s in f.by_type("IfcSpace") if s.Name == name or s.LongName == name)


# ---------------------------------------------------------------- 구조·메타

def test_every_check_has_exactly_one_scope():
    assert set(catalog.CHECK_SCOPE) == set(catalog.CHECKS)
    assert set(catalog.CHECK_SCOPE.values()) == {"common", "procurement"}
    counts = profiles.scope_counts()
    assert counts == {"common": 12, "procurement": 25, "seumteo": 15}
    assert sum(counts.values()) == len(catalog.CHECKS) + len(catalog.SEUMTEO_CHECKS) == 52


def test_profile_tables_cover_every_procurement_rule():
    proc = {c for c, s in catalog.CHECK_SCOPE.items() if s == "procurement"}
    assert proc == set(profiles.SKIP_REASONS) | set(profiles.REPLACED_BY)       # 빠진 규칙이 없다
    assert not set(profiles.SKIP_REASONS) & set(profiles.REPLACED_BY)           # 둘 다에 있지 않다
    assert set(profiles.REPLACED_BY.values()) <= set(catalog.SEUMTEO_CHECKS)
    # 세움터 전용 검사 함수와 카탈로그가 같은 목록이다(폴더 검사는 따로)
    assert set(seumteo.SEU_CHECKS) | {seumteo.PACKAGE_CHECK[0]} == set(catalog.SEUMTEO_CHECKS)
    # 대상만 달리 돌리는 검사는 공통 소속이고 코드 검사 레지스트리에 있다
    assert all(catalog.CHECK_SCOPE[c] == "common" and c in codechecks.CODE_CHECKS for c in seumteo.ADAPTED)
    for cid, (mode, sev, reqs, olds) in catalog.SEUMTEO_CHECKS.items():
        assert cid.startswith("KR-SEU-") and sev in ("필수", "권고", "참고")
        assert all(o in catalog.CHECKS for o in olds)


def test_requirements_json_carries_profile_scopes():
    data = json.loads((ROOT / "requirements.json").read_text(encoding="utf-8"))
    assert {c["id"]: c["profile_scope"] for c in data["checks"]} == catalog.CHECK_SCOPE
    assert [c["id"] for c in data["seumteo_checks"]] == list(catalog.SEUMTEO_CHECKS)
    assert data["_meta"]["profiles"]["default"] == "procurement"
    assert profiles.DRAFT_WARNING in data["_meta"]["profiles"]["note"]


# ---------------------------------------------------------------- (a) 세움터식 모델

def test_seumteo_model_passes_under_seumteo_profile(seu, tmp_path):
    checks, res = _checks(seu, tmp_path, deliverable=True)
    assert not _bad(checks), _bad(checks)
    assert res["profile_id"] == "seumteo" and list(res)[:2] == ["profile_id", "draft_warning"]   # 경고가 맨 위
    assert res["draft_warning"] == "초안: 세움터 BIM 설계기준서 v1.0(안) 2021-12, 공식 배포본 미확보, 실제 세움터식 내보내기 미시험"
    assert res["scope_counts"] == {"common": 12, "procurement": 25, "seumteo": 15}
    # 파일명·AIS_ 분류코드(Pset 이름 불문)·면적 객체
    assert checks["KR-SEU-01"]["status"] == "pass"
    assert checks["KR-SEU-05"]["found_via"] == {"속성 AIS_분류코드(Pset 불문)": 4}
    assert checks["KR-SEU-06"]["status"] == "pass" and checks["KR-SEU-06"]["applicable"] == 4
    for k in ("KR-SEU-09", "KR-SEU-10", "KR-SEU-11"):
        assert checks[k]["status"] == "pass" and checks[k]["applicable"] == 1, k
    # 면적 객체 IfcSpace 는 실 공간 검사에서 빠진다(실 4개만 대상)
    assert checks["KR-SEU-04"]["applicable"] == 4
    assert checks["KR-CODE-10"]["status"] == "pass" and "제외" in checks["KR-CODE-10"]["note"]
    assert checks["KR-CODE-10"]["profile_adapted"] is True
    # 시설개요는 필수 아님: 위반이 아니라 정보
    assert checks["KR-SEU-12"]["status"] == "info"
    # 소속 표식과 해당 없음 사유
    assert {c["scope"] for c in checks.values()} == {"common", "procurement", "seumteo"}
    assert checks["KR-CODE-02"]["status"] == "na" and checks["KR-CODE-02"]["replaced_by"] == "KR-SEU-01"
    assert checks["KR-IDS-04"]["status"] == "na" and "실ID" in checks["KR-IDS-04"]["note"] and "replaced_by" not in checks["KR-IDS-04"]
    assert res["summary"]["total"] == len(catalog.CHECKS) - 1 + len(seumteo.SEU_CHECKS)   # 폴더 검사(--package)만 빠짐


def test_seumteo_model_under_procurement_is_judged_as_before(seu, tmp_path):
    checks, res = _checks(seu, tmp_path, profile="procurement", deliverable=True)
    assert "profile_id" not in res and "scope" not in checks["KR-CODE-10"]          # 기본 프로파일은 표식 키를 더하지 않음
    assert res["profile"] == run.PROFILE
    assert checks["KR-CODE-02"]["status"] == "fail"       # 세움터 파일명은 조달청 정규식 위반
    assert checks["KR-IDS-05"]["status"] == "fail"        # 분류 참조·공간분류코드 속성 없음(AIS_분류코드는 조달청이 모름)
    assert checks["KR-CODE-09"]["status"] == "fail"
    assert checks["KR-CODE-10"]["status"] == "fail"       # 면적 객체와 실 공간이 겹침으로 잡힘
    assert checks["KR-IDS-03"]["status"] == "fail"        # 면적 객체(IfcSpace)에 LongName 이 없음
    assert checks["KR-IDS-12"]["status"] == "fail" and checks["KR-IDS-17"]["status"] == "fail"
    assert checks["KR-CODE-08"]["status"] == "fail"       # 방 번호는 실ID 형식 아님
    assert checks["KR-IDS-04"]["status"] == "pass"        # Name 값을 보조로 인정
    assert not any(k.startswith("KR-SEU") for k in checks)


# ---------------------------------------------------------------- (b) 기본 결과 불변

def _dump(res):
    return json.dumps(res, ensure_ascii=False, indent=2) + "\n"


def test_default_profile_results_match_committed_snapshots(tmp_path):
    ra01 = ROOT.parent / "s0-baseline" / "RA-01.ifc"
    snap = {"RA-01.json": {}, "RA-01.energy.json": {"energy": True}, "RA-01.deliverable.json": {"deliverable": True}}
    for name, kw in snap.items():
        want = (ROOT / "results" / name).read_text(encoding="utf-8")
        assert _dump(run.run_file(ra01, **kw)) == want, name
        assert _dump(run.run_file(ra01, profile="procurement", **kw)) == want, name     # 명시해도 같다
    # 합성 양성 대조군도 같다(해시만 비움: make_results 와 같은 처리)
    p = tmp_path / "00_본관_BA.ifc"
    fixtures.build_compliant().write(str(p))
    res = run.run_file(p, deliverable=True, energy=True)
    res["sha256"] = None
    assert _dump(res) == (ROOT / "results" / "compliant-fixture.json").read_text(encoding="utf-8")


def test_default_cli_output_has_no_profile_keys(tmp_path):
    out = tmp_path / "x.json"
    ra01 = ROOT.parent / "s0-baseline" / "RA-01.ifc"
    assert run.main([str(ra01), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == (ROOT / "results" / "RA-01.json").read_text(encoding="utf-8")
    out2 = tmp_path / "y.json"
    assert run.main([str(ra01), "--profile", "procurement", "--out", str(out2)]) == 0
    assert out2.read_text(encoding="utf-8") == out.read_text(encoding="utf-8")


def test_seumteo_cli_and_committed_results(tmp_path):
    out = tmp_path / "s.json"
    ra01 = ROOT.parent / "s0-baseline" / "RA-01.ifc"
    assert run.main([str(ra01), "--profile", "seumteo", "--out", str(out)]) == 0
    committed = (ROOT / "results" / "RA-01.seumteo.json").read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == committed
    assert json.loads(committed)["draft_warning"] == profiles.DRAFT_WARNING


# ---------------------------------------------------------------- (c) 세움터 규칙의 진짜 위반

@pytest.mark.parametrize("name,good", [
    ("OO프로젝트_본관_BA_ALL.ifc", True), ("OO프로젝트_본관_BG_ALL.ifc", True), ("OO프로젝트_101동_BS_ALL.ifc", True),
    ("OO프로젝트_ALL_BC_ALL.ifc", True), ("OO프로젝트_본관_BA_기둥.ifc", True), ("공공주택_단위세대_BA_59A.ifc", True),
    ("00_본관_BA.ifc", False),               # 조달청 형식은 세움터 위반
    ("OO프로젝트_본관_BC_ALL.ifc", False),   # 대지는 동명 자리가 ALL 이어야 함
    ("OO프로젝트_본관_BX_ALL.ifc", False), ("OO프로젝트_BA_ALL.ifc", False), ("OO프로젝트_본관_BA_ALL.IFC", False),
])
def test_seumteo_filename_rule(seu, tmp_path, name, good):
    checks, _ = _checks(seu, tmp_path, name=name, deliverable=True)
    assert checks["KR-SEU-01"]["status"] == ("pass" if good else "fail"), name
    # 같은 모델의 조달청 파일명 판정은 반대 방향이 아니라 따로 간다
    procurement, _ = _checks(seu, tmp_path, name=name, deliverable=True, profile="procurement")
    assert (procurement["KR-CODE-02"]["status"] == "pass") == (name == "00_본관_BA.ifc")


def test_seumteo_filename_not_judged_without_deliverable(seu, tmp_path):
    checks, _ = _checks(seu, tmp_path, name="a.ifc")
    assert checks["KR-SEU-01"]["status"] == "na"


def test_room_class_code_violations_are_caught(seu, tmp_path):
    rooms = [s for s in seu.by_type("IfcSpace") if s.LongName]
    # 룸 코드가 형식에 안 맞으면 위반, 면적 객체 코드처럼 보이는 정상 형식 오류(A1420)도 위반
    for room, bad in ((rooms[0], "99999"), (rooms[1], "A1420")):
        pset = next(r.RelatingPropertyDefinition for r in room.IsDefinedBy if r.RelatingPropertyDefinition.Name == "AIS_룸")
        ifcopenshell.api.pset.edit_pset(seu, pset=pset, properties={"AIS_분류코드": bad})
    checks, _ = _checks(seu, tmp_path)
    assert checks["KR-SEU-06"]["status"] == "fail" and checks["KR-SEU-06"]["failed"] == 2
    assert checks["KR-SEU-05"]["status"] == "pass"                 # 코드는 있고 형식이 틀림
    assert checks["KR-SEU-04"]["status"] == "pass"


def test_room_without_any_class_code_fails_present(seu, tmp_path):
    room = next(s for s in seu.by_type("IfcSpace") if s.LongName == "회의실")
    pset = next(r.RelatingPropertyDefinition for r in room.IsDefinedBy if r.RelatingPropertyDefinition.Name == "AIS_룸")
    ifcopenshell.api.pset.edit_pset(seu, pset=pset, properties={"AIS_분류코드": None})
    checks, _ = _checks(seu, tmp_path)
    assert checks["KR-SEU-05"]["status"] == "fail" and checks["KR-SEU-05"]["failed"] >= 1


def test_real_room_overlap_still_caught(seu, tmp_path):
    fixtures._place(seu, _space(seu, "104"), 4000.0, 2000.0, 0.0)  # 1F 회의실을 사무실 쪽으로 옮겨 실끼리 겹치게 한다
    checks, _ = _checks(seu, tmp_path)
    assert checks["KR-CODE-10"]["status"] == "fail"


def test_same_kind_area_overlap_is_warning_only(seu, tmp_path):
    body = [c for c in seu.by_type("IfcGeometricRepresentationSubContext")][0]
    st = next(s for s in seu.by_type("IfcBuildingStorey") if s.Name == "1F")
    fixtures._add_area_object(seu, body, st, 0.0, "용도구획-2", "A14202", {"면적": 10.0}, 2600.0)  # 용도구획끼리 겹침
    checks, _ = _checks(seu, tmp_path)
    assert checks["KR-SEU-13"]["status"] == "warn" and checks["KR-SEU-13"]["failed"] >= 1
    assert checks["KR-CODE-10"]["status"] == "pass"           # 실 공간 판정에는 영향 없음


def test_area_objects_missing_is_info_not_failure(tmp_path):
    f = fixtures.build_seumteo()
    for s in [s for s in f.by_type("IfcSpace") if not s.LongName]:
        ifcopenshell.api.root.remove_product(f, product=s)
    checks, _ = _checks(f, tmp_path)
    for k in ("KR-SEU-09", "KR-SEU-10", "KR-SEU-11"):
        assert checks[k]["status"] == "info", k
    assert not _bad(checks), _bad(checks)


def test_compression_rule(seu, tmp_path):
    # 확장자로 압축 판단
    ifczip = tmp_path / "OO프로젝트_본관_BA_ALL.ifczip"
    zf = zipfile.ZipFile(ifczip, "w")
    inner = tmp_path / "inner.ifc"
    seu.write(str(inner))
    zf.write(inner, "OO프로젝트_본관_BA_ALL.ifc")
    zf.close()
    r = seumteo.check_compression_size(None, path=str(ifczip), filename=ifczip.name)
    assert r["status"] == "warn" and "압축" in r["examples"][0]["reason"]
    # 이름은 .ifc 인데 내용이 zip 이어도 잡는다
    fake = tmp_path / "fake.ifc"
    fake.write_bytes(ifczip.read_bytes())
    assert seumteo.check_compression_size(None, path=str(fake), filename="fake.ifc")["status"] == "warn"
    # 일반 IFC 는 통과, 조달청 300MB zip 허용 문구는 세움터에서 쓰지 않는다
    assert seumteo.check_compression_size(None, path=str(inner), filename="inner.ifc")["status"] == "pass"
    assert seumteo.SIZE_LIMIT_BYTES == codechecks.SIZE_LIMIT_BYTES


def test_package_layout_seumteo(tmp_path):
    pkg = tmp_path / "납품"
    (pkg / "BIM").mkdir(parents=True)
    (pkg / "도면").mkdir()
    (pkg / "BIM" / "OO프로젝트_본관_BA_ALL.ifc").write_text("x")
    r = run.run_package(pkg, profile="seumteo")
    assert r["profile_id"] == "seumteo" and r["checks"][0]["id"] == "KR-SEU-02" and r["checks"][0]["status"] == "pass"
    (pkg / "BIM" / "00_본관_BA.ifc").write_text("x")             # 조달청 이름은 세움터에서 위반
    assert run.run_package(pkg, profile="seumteo")["checks"][0]["status"] == "fail"
    (pkg / "BIM" / "00_본관_BA.ifc").unlink()
    (pkg / "도면").rmdir()
    r = run.run_package(pkg, profile="seumteo")["checks"][0]
    assert r["status"] == "warn" and "도면" in r["examples"][0]["reason"]
    # 조달청 폴더 구성(IFC/원본/도면/BIM 보고서)은 기본 프로파일에서 그대로 위반
    assert run.run_package(pkg)["checks"][0]["status"] == "fail"


def test_seumteo_package_merges_into_file_result(seu, tmp_path):
    pkg = tmp_path / "빈폴더"
    pkg.mkdir()
    p = _write(seu, tmp_path)
    res = run.run_file(p, package=pkg, profile="seumteo")
    ids = [c["id"] for c in res["checks"]]
    assert ids.count("KR-SEU-02") == 1 and ids.count("KR-CODE-18") == 1
    assert next(c for c in res["checks"] if c["id"] == "KR-CODE-18")["status"] == "na"
    assert res["summary"]["total"] == len(catalog.CHECKS) + len(seumteo.SEU_CHECKS) + 1


def test_common_core_violations_are_still_caught(seu, tmp_path):
    """공통 코어는 세움터에서도 같은 위반을 잡는다(지하층 이름, 단위, 층 소속)."""
    st = next(s for s in seu.by_type("IfcBuildingStorey") if s.Name == "B1")
    st.Name = "저층 1"
    ps = ifcopenshell.api.pset.add_pset(seu, product=st, name="Pset_BuildingStoreyCommon")
    ifcopenshell.api.pset.edit_pset(seu, pset=ps, properties={"AboveGround": seu.createIfcLogical(False)})
    checks, _ = _checks(seu, tmp_path)
    assert checks["KR-CODE-07"]["status"] == "fail"
    assert checks["KR-SEU-15"]["candidates"][0]["observed"] == "층 2개 중 이름에 공백이 있는 층 1개"   # C-1 노트(판정 아님)
    mm, _ = _checks(_seu_in_meters(), tmp_path)
    assert mm["KR-CODE-04"]["status"] == "fail"


def _seu_in_meters():
    f = fixtures.build_seumteo()
    for u in f.by_type("IfcSIUnit"):
        if u.UnitType == "LENGTHUNIT":
            u.Prefix = None
    return f


def test_proxy_part_class_aliases(seu, tmp_path):
    st = next(s for s in seu.by_type("IfcBuildingStorey") if s.Name == "1F")
    px = ifcopenshell.api.root.create_entity(seu, ifc_class="IfcBuildingElementProxy", name="범용")
    ifcopenshell.api.aggregate.assign_object(seu, products=[px], relating_object=st)
    checks, _ = _checks(seu, tmp_path)
    assert checks["KR-SEU-07"]["status"] == "warn"              # 분류 정보 없음: 사본 불일치라 경고로 낮춤
    ps = ifcopenshell.api.pset.add_pset(seu, product=px, name="AIS_부위")
    ifcopenshell.api.pset.edit_pset(seu, pset=ps, properties={"부위분류": "333"})
    checks, _ = _checks(seu, tmp_path)
    assert checks["KR-SEU-07"]["status"] == "pass"


def test_candidate_notes_never_judge(seu, tmp_path):
    checks, _ = _checks(seu, tmp_path)
    notes = checks["KR-SEU-15"]
    assert notes["status"] == "info" and notes["failed"] == 0
    assert [c["id"] for c in notes["candidates"]] == [f"C-{i}" for i in range(1, 12)]
    c3 = next(c for c in notes["candidates"] if c["id"] == "C-3")
    assert "용도구획" in c3["observed"] and "방화구획" in c3["observed"]
    c2 = next(c for c in notes["candidates"] if c["id"] == "C-2")
    assert "형식이 맞는 값 2개" in c2["observed"]
    assert checks["KR-SEU-14"]["status"] == "info"


def test_legacy_schema_under_seumteo(tmp_path):
    from make_results import STEP_HEAD, PROJECT
    p = tmp_path / "x_본관_BA_ALL.ifc"
    p.write_text(STEP_HEAD % "IFC2X2_FINAL" + PROJECT + "ENDSEC;\nEND-ISO-10303-21;\n", encoding="utf-8")
    res = run.run_file(p, deliverable=True, profile="seumteo")
    c = {x["id"]: x for x in res["checks"]}
    assert c["KR-CODE-01"]["status"] == "fail" and res["unreadable_schema"] is True
    assert c["KR-SEU-01"]["status"] == "pass" and c["KR-SEU-03"]["status"] == "pass"
    assert c["KR-SEU-05"]["status"] == "na" and c["KR-CODE-10"]["status"] == "na"


# ---------------------------------------------------------------- (d) 알 수 없는 프로파일

@pytest.mark.parametrize("bad", ["Seumteo", "pps", "", "세움터", "procurement "])
def test_unknown_profile_raises_input_error(tmp_path, bad):
    p = _write(fixtures.build_compliant(), tmp_path, "00_본관_BA.ifc")
    with pytest.raises(run.InputError) as e:
        run.run_file(p, profile=bad)
    assert e.value.code == "UNKNOWN_PROFILE"
    with pytest.raises(run.InputError) as e:
        run.run_package(tmp_path, profile=bad)
    assert e.value.code == "UNKNOWN_PROFILE"


def test_unknown_profile_cli_writes_error_json(tmp_path, capsys):
    p = _write(fixtures.build_compliant(), tmp_path, "00_본관_BA.ifc")
    out = tmp_path / "e.json"
    assert run.main([str(p), "--profile", "nope", "--out", str(out)]) == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["status"] == "error" and data["error"]["code"] == "UNKNOWN_PROFILE" and data["profile"] == "nope"
    assert "nope" in capsys.readouterr().err
    assert "checks" not in data
    out2 = tmp_path / "e2.json"                    # 폴더 검사 경로도 같다
    assert run.main(["--package", str(tmp_path), "--profile", "nope", "--out", str(out2)]) == 2
    assert json.loads(out2.read_text(encoding="utf-8"))["error"]["code"] == "UNKNOWN_PROFILE"
    assert str(tmp_path) not in out.read_text(encoding="utf-8")   # 결과에 절대 경로를 쓰지 않는다


# ---------------------------------------------------------------- 검수 반영: 면적 객체 거짓 통과 방지

def _add_room_props(f, space, props, name="AIS_검수"):
    ps = ifcopenshell.api.pset.add_pset(f, product=space, name=name)
    ifcopenshell.api.pset.edit_pset(f, pset=ps, properties=props)


def _bare_room(f, name="실-X"):
    """이름·분류코드가 없는 실 공간 하나를 1F 에 추가(조달청식 실처럼 보이는 빈 실)."""
    body = [c for c in f.by_type("IfcGeometricRepresentationSubContext")][0]
    st = next(s for s in f.by_type("IfcBuildingStorey") if s.Name == "1F")
    sp = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSpace", name=name)
    rep = ifcopenshell.api.geometry.add_profile_representation(
        f, context=body, profile=f.createIfcRectangleProfileDef("AREA", None, None, 1000.0, 1000.0), depth=2.8)
    ifcopenshell.api.geometry.assign_representation(f, product=sp, representation=rep)
    fixtures._place(f, sp, 20000.0, 20000.0, 0.0)
    ifcopenshell.api.aggregate.assign_object(f, products=[sp], relating_object=st)
    return sp


@pytest.mark.parametrize("props", [{"방화구획여부": False}, {"AIS_바닥면적제외": False}, {"AIS_실사용": True}])
def test_marker_alone_does_not_make_an_area_object(seu, tmp_path, props):
    sp = _bare_room(seu)
    _add_room_props(seu, sp, props)
    assert seumteo.area_kind(sp) is None
    checks, _ = _checks(seu, tmp_path)
    assert checks["KR-SEU-05"]["status"] == "fail"           # 분류코드 없는 실이 검사에서 빠지지 않는다
    assert checks["KR-SEU-04"]["status"] == "pass"           # Name 보조로 인정(실명 없음이 아니라 마커 때문에 제외되지 않음)
    assert checks["KR-SEU-04"]["applicable"] == 5


def test_unnamed_room_is_judged_not_excluded(seu, tmp_path):
    sp = _bare_room(seu)
    sp.Name = None
    _add_room_props(seu, sp, {"방화구획여부": False})
    checks, _ = _checks(seu, tmp_path)
    assert checks["KR-SEU-04"]["status"] == "warn" and checks["KR-SEU-04"]["failed"] == 1


@pytest.mark.parametrize("code", ["F", "B", "S", "A99999"])
def test_room_with_area_code_typo_is_still_checked(seu, tmp_path, code):
    room = _space(seu, "104")                                # 실명이 있는 실에 면적 객체 코드를 잘못 입력
    pset = next(r.RelatingPropertyDefinition for r in room.IsDefinedBy if r.RelatingPropertyDefinition.Name == "AIS_룸")
    ifcopenshell.api.pset.edit_pset(seu, pset=pset, properties={"AIS_분류코드": code})
    assert seumteo.area_kind(room) is None
    checks, _ = _checks(seu, tmp_path)
    assert checks["KR-SEU-06"]["status"] == "fail" and checks["KR-SEU-04"]["applicable"] == 4


def test_code_alone_without_clues_beyond_identity_stays_room(seu, tmp_path):
    sp = _bare_room(seu)                                      # 코드만 있고 마커·면적 값이 없음
    _add_room_props(seu, sp, {"AIS_분류코드": "F"})
    assert seumteo.area_kind(sp) is None
    checks, _ = _checks(seu, tmp_path)
    assert checks["KR-SEU-06"]["status"] == "fail"


def test_marker_plus_code_is_area_and_exclusion_is_exposed(seu, tmp_path):
    room = _space(seu, "104")                                 # 실명 있어도 코드 F + 마커면 면적 객체(두 단서)
    _add_room_props(seu, room, {"AIS_분류코드": "F", "방화구획여부": True}, name="AIS_구획")
    # 같은 속성 이름이 두 Pset 에 있으면 먼저 나온 값이 우선이라 룸 Pset 의 코드를 지운다
    pset = next(r.RelatingPropertyDefinition for r in room.IsDefinedBy if r.RelatingPropertyDefinition.Name == "AIS_룸")
    ifcopenshell.api.pset.edit_pset(seu, pset=pset, properties={"AIS_분류코드": None})
    assert seumteo.area_kind(room) == "방화구획"
    checks, _ = _checks(seu, tmp_path)
    for k in ("KR-SEU-04", "KR-SEU-05", "KR-SEU-06", "KR-CODE-10"):
        ex = checks[k]["excluded_area_objects"]
        assert ex["count"] == 5 and len(ex["examples"]) == 5 and "global_id" in ex["examples"][0], k
        assert "제외" in checks[k]["note"]


def test_ais_value_is_stripped():
    assert seumteo.ais_value({"ais_분류코드": " B "}) == "B"


def test_size_branch_over_limit_warns(tmp_path, monkeypatch):
    p = tmp_path / "a.ifc"
    p.write_text("x")
    monkeypatch.setattr(seumteo, "SIZE_LIMIT_BYTES", 0)
    r = seumteo.check_compression_size(None, path=str(p), filename="a.ifc")
    assert r["status"] == "warn" and "용량 상한이 없" in r["examples"][0]["reason"]


@pytest.mark.parametrize("name", ["a_b_BA_ALL.ifc.ifc", "a_b_BA_ALL.ifc\n", "a_b_BA_A.LL.ifc", "a_b_BA_ALL.ifc "])
def test_seumteo_name_regex_rejects_odd_endings(name):
    assert seumteo._SEU_NAME.match(name) is None


def test_energy_flag_is_not_shown_as_judged_under_seumteo(seu, tmp_path):
    _, res = _checks(seu, tmp_path, energy=True)
    assert res["energy_mode"] is False and "energy_note" in res and res["summary"]["conditional"]["enabled"] is False


def test_requirements_json_adapted_and_req046():
    data = json.loads((ROOT / "requirements.json").read_text(encoding="utf-8"))
    assert {c["id"]: c["seumteo"] for c in data["checks"]}["KR-CODE-10"] == "대상 변경(실 공간만)"
    assert "REQ-046" in catalog.SEUMTEO_CHECKS["KR-SEU-08"][2]
