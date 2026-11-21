# SPDX-License-Identifier: MPL-2.0
"""세움터 면적 객체 IFC 내보내기(W1 연장 6번째 PR, ARC-36 마무리): ifc.export seumteo_areas.

모델은 test_legal_area_plan 과 같다(지하 1층 12x10m, 1층·2층 10x8m, 옥상 레벨, 대지 300㎡, 법정 면적 객체 6개) + 1층 실 공간 하나.
 1. 용도구획(A)·건축면적(B)·대지면적(S)이 IfcSpace + AIS_ 속성으로 나가고, 값이 area.legal 과 같다(제외 플래그·노대 산입 면적 포함)
 2. 내보낸 파일을 krules 세움터 프로파일로 다시 보면 KR-SEU-09·10·11 통과, 실 공간 검사는 면적 객체를 빼고 본다
 3. 옵션을 끄면 정본 투영 그대로(사본에만 더하므로 앞뒤 내보내기 바이트가 같다), 같은 모델이면 같은 파일, IFC2X3 도 유효
 4. 인자 오류(코드 형식·없는 레벨)는 파일·스냅숏을 남기지 않는다
"""
import sys

import ifcopenshell
import ifcopenshell.util.element as ue
import ifcopenshell.validate

from conftest import ROOT
import test_legal_area_plan as T

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402
from dobim_core.krules import runner as run  # noqa: E402

ok, err = T.ok, T.err
CODE = "A00000"   # 시험용 자리표시 값(형식만 맞춘 값이며 실제 용도 코드가 아니다)
CODE2 = "A00001"
ROOM = "8" + f"{1:021d}"


def _bench(tmp_path):
    from dobim_core.engine import Engine
    b = S.Bench(tmp_path)
    Engine.create(b.ws / "p.dobim", "s2", guid=T.GUID).close()
    T._model(b.run)
    ids = T._objects(b.run)
    ok(b.run("space.create", {"id": ROOM, "level": T.L1, "boundary": [[5000, 0], [10000, 0], [10000, 4000], [5000, 4000]], "name": "사무실"}))
    return b, ids


def _areas(f):
    """{이름: (ObjectType, DoBIM_AIS, DoBIM_SeumteoNote)} 면적 객체만."""
    out = {}
    for s in f.by_type("IfcSpace"):
        ps = ue.get_psets(s)
        if "DoBIM_AIS" in ps:
            out[s.Name] = (s.ObjectType, ps["DoBIM_AIS"], ps["DoBIM_SeumteoNote"])
    return out


def _errors(f):
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    # 시험 모델의 id 는 사람이 읽기 쉬운 자리표시 값이라 GlobalId base64 검사는 뺀다(면적 객체 GlobalId 는 파생 값이라 유효)
    return [s["message"][:200] for s in lg.statements if s["level"] in ("error", "critical") and "GloballyUniqueId" not in s["message"]]


def test_면적_객체_값과_krules_자체_검사(tmp_path):
    b, ids = _bench(tmp_path)
    try:
        legal = ok(b.run("area.legal", {}))
        r = ok(b.destructive("ifc.export", {"path": "s.ifc", "seumteo_areas": {"use_code": CODE, "level_use_codes": [{"level": T.L2, "code": CODE2}]}}))
        seu = r["seumteo_areas"]
        assert seu["objects"] == {"A": 7, "B": 1, "S": 1}   # 벽 영역 3(지하·1층·2층) + 바닥면적 대상 객체 4, 옥상 레벨은 벽 없음
        assert seu["building_m2"] == legal["building"]["area_m2"] == 70.0 and seu["site_m2"] == legal["site"]["area_m2"] == 280.0
        assert seu["use_codes"] == {"B1": CODE, "1F": CODE, "2F": CODE2, "RF": CODE}
        assert seu["self_check"] == {"KR-SEU-09": "pass", "KR-SEU-10": "pass", "KR-SEU-11": "pass", "KR-SEU-13": "pass"}
        assert {u["code"] for u in seu["unverified"]} >= {"AIS_분류코드", "IFC 위치", "제외 플래그", "방화구획"}
        f = ifcopenshell.open(str(b.ws / "s.ifc"))
        assert not _errors(f)
        ar = _areas(f)
        # 건축면적·대지면적: 코드·면적 값(area.legal), 제외 플래그 없음, LongName 없음(krules 가정 2 의 정체 없음 조건)
        t, ais, note = ar["건축면적"]
        assert t == "AREA_건축면적" and ais["AIS_분류코드"] == "B" and ais["면적"] == 70.0 and "AIS_연면적제외" not in ais and note["근거"] == "119-1-2"
        t, ais, note = ar["대지면적"]
        assert t == "AREA_대지면적" and ais["AIS_분류코드"] == "S" and ais["면적"] == 280.0 and note["원본"] == T.SI
        # 용도구획: 층 벽 영역(지하층은 용적률 제외), 법정 면적 객체(효과 -> 제외 플래그, 노대는 산입 면적)
        flags = ("AIS_연면적제외", "AIS_용적률제외", "AIS_바닥면적제외")
        fl = {n: tuple(v[1][k] for k in flags) for n, v in ar.items() if v[0] == "AREA_용도구획"}
        assert fl == {"B1 용도구획 1": (False, True, False), "1F 용도구획 1": (False, False, False), "2F 용도구획 1": (False, False, False),
                      "주차": (False, True, False), "추가": (False, False, False), "발코니": (False, False, False), "계단탑": (True, True, True)}
        assert ar["B1 용도구획 1"][1]["면적"] == 120.0 and ar["1F 용도구획 1"][1]["면적"] == 80.0
        assert ar["발코니"][1]["면적"] == 3.0 and ar["계단탑"][1]["면적"] == 4.0 and ar["주차"][1]["면적"] == 20.0
        assert ar["2F 용도구획 1"][1]["AIS_분류코드"] == CODE2 and ar["발코니"][1]["AIS_분류코드"] == CODE2 and ar["주차"][1]["AIS_분류코드"] == CODE
        assert ar["발코니"][2]["근거"] == "119-1-3-나" and ar["발코니"][2]["원본"] == ids["bal"] and "확인 필요" in ar["발코니"][2]["확인필요"]
        # 합산(seumteo_plan 머리말의 셈): 연면적·용적률 산정용 연면적이 area.legal 과 같다
        A = [(v[1], v[2]["겹침"]) for v in ar.values() if v[0] == "AREA_용도구획"]
        assert sorted(n for n, v in ar.items() if v[0] == "AREA_용도구획" and v[2]["겹침"]) == ["계단탑", "주차"]
        gross = sum(a["면적"] for a, o in A if not o) - sum(a["면적"] for a, o in A if o and a["AIS_바닥면적제외"])
        far = sum(a["면적"] for a, o in A if not o and not a["AIS_용적률제외"]) - sum(a["면적"] for a, o in A if o and a["AIS_용적률제외"])
        assert round(gross, 2) == legal["gross_floor_m2"] == 280.0 and round(far, 2) == legal["far_floor_m2"] == 140.0
        # 배치: 용도구획은 그 층 아래, 건축면적은 건물, 대지면적은 대지 아래
        par = {s.Name: s.Decomposes[0].RelatingObject for s in f.by_type("IfcSpace") if s.Name in ar}
        assert par["2F 용도구획 1"].is_a("IfcBuildingStorey") and par["2F 용도구획 1"].Name == "2F" and par["발코니"].Name == "2F"
        assert par["건축면적"].is_a("IfcBuilding") and par["대지면적"].is_a("IfcSite")
        # krules 세움터 프로파일 전체: 면적 객체 검사 통과, 실 공간 검사는 면적 객체 9개를 빼고 실 1개만 본다
        p = b.ws / "OO_본관_BA_ALL.ifc"
        p.write_bytes((b.ws / "s.ifc").read_bytes())
        res = {c["id"]: c for c in run.run_file(p, profile="seumteo")["checks"]}
        assert all(res[c]["status"] == "pass" for c in ("KR-SEU-09", "KR-SEU-10", "KR-SEU-11"))
        assert res["KR-SEU-04"]["excluded_area_objects"]["count"] == 9 and res["KR-SEU-04"]["applicable"] == 1
        assert res["KR-SEU-13"]["status"] in ("pass", "na")
        # 조달청 프로파일로 보면 면적 객체도 공간이라 결과가 달라질 수 있다: 세움터 제출 전용 옵션임을 확인만 한다
        assert run.run_file(p, profile="procurement")["checks"]
    finally:
        b.close()


def test_옵션을_끄면_정본_그대로_같은_모델이면_같은_파일(tmp_path):
    b, _ = _bench(tmp_path)
    try:
        ok(b.destructive("ifc.export", {"path": "p1.ifc"}))
        r1 = ok(b.destructive("ifc.export", {"path": "s1.ifc", "seumteo_areas": {"use_code": CODE}}))
        r2 = ok(b.destructive("ifc.export", {"path": "s2.ifc", "seumteo_areas": {"use_code": CODE}}))
        ok(b.destructive("ifc.export", {"path": "p2.ifc"}))
        p1, p2 = (b.ws / "p1.ifc").read_bytes(), (b.ws / "p2.ifc").read_bytes()
        assert p1 == p2 and b"DoBIM_AIS" not in p1   # 사본에만 더했다: 정본 투영·기본 출력은 그대로
        assert (b.ws / "s1.ifc").read_bytes() == (b.ws / "s2.ifc").read_bytes()   # GlobalId 는 파생 값(같은 모델이면 같은 파일)
        assert r1["seumteo_areas"] == r2["seumteo_areas"]
        plain = ok(b.destructive("ifc.export", {"path": "p3.ifc"}))
        assert "seumteo_areas" not in plain and set(plain) == {"path", "bytes", "rev", "snapshot"}   # 응답도 이전과 같다
        # IFC2X3: 면적 객체가 함께 내려가고 유효하며 자체 검사 통과
        r = ok(b.destructive("ifc.export", {"path": "s3.ifc", "schema": "IFC2X3", "seumteo_areas": {"use_code": CODE, "site_area_m2": 250}}))
        assert r["schema"] == "IFC2X3" and r["seumteo_areas"]["self_check"]["KR-SEU-11"] == "pass" and r["seumteo_areas"]["site_m2"] == 230.0
        f = ifcopenshell.open(str(b.ws / "s3.ifc"))
        assert f.schema == "IFC2X3" and not _errors(f)
        ar = _areas(f)
        assert ar["대지면적"][1]["면적"] == 230.0 and ar["건축면적"][1]["AIS_분류코드"] == "B"
        s = next(x for x in f.by_type("IfcSpace") if x.Name == "대지면적")
        assert s.Representation is None and s.InteriorOrExteriorSpace == "NOTDEFINED"   # site_area_m2 로 주면 형상 없음
    finally:
        b.close()


def test_인자_오류는_파일을_남기지_않는다(tmp_path):
    b, _ = _bench(tmp_path)
    try:
        ok(b.destructive("ifc.export", {"path": "x.ifc"}))
        before = (b.ws / "x.ifc").read_bytes()
        for bad in ({"use_code": "A1234"}, {"use_code": "B00000"}, {}, {"use_code": CODE, "level_use_codes": [{"level": T.L1, "code": "12345"}]}):
            assert err(b.run("ifc.export", {"path": "x.ifc", "seumteo_areas": bad})) == "SCHEMA_INVALID"
        r = b.run("ifc.export", {"path": "x.ifc", "seumteo_areas": {"use_code": CODE, "level_use_codes": [{"level": T.SI, "code": CODE}]}})
        if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
            r = b.run("ifc.export", {"path": "x.ifc", "seumteo_areas": {"use_code": CODE, "level_use_codes": [{"level": T.SI, "code": CODE}]}},
                      confirm=r["error"]["details"]["confirm_token"])
        assert err(r) in ("NOT_FOUND", "CONSTRAINT")
        assert (b.ws / "x.ifc").read_bytes() == before
        snaps = b.ws / ".dobim-snapshots"
        assert not snaps.exists() or not [x for x in snaps.rglob("*") if x.is_file()]   # 실패한 내보내기는 스냅숏을 남기지 않는다
    finally:
        b.close()
