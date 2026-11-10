"""results/*.json 을 다시 만든다.

사용(스파이크 폴더에서):
  python make_results.py --building-architecture <Building-Architecture.ifc 경로>
RA-01 은 ../s0-baseline/RA-01.ifc 를 쓴다. Building-Architecture.ifc 는 공개 외래 표본(저장소에 없음)이라 경로를 받는다.
파일 이름의 .seumteo 는 세움터 프로파일(초안) 결과다(--profile seumteo, 나머지는 기본 조달청 프로파일).
파일 이름의 .energy 는 --energy(에너지 검토를 켜 조건부 규칙 KR-IDS-08/09/10 도 판정) 결과다.
양성 대조군과 망가진 입력은 합성한다(tests/fixtures.py).
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from krrules import run  # noqa: E402


def write_json(data, name):
    out = ROOT / "results" / name
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dump(res, name):
    write_json(res, name)
    print(name, res.get("schema"), res.get("summary"))


STEP_HEAD = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x'),'2;1');\nFILE_NAME('x.ifc','',(''),(''),'','','');\n"
             "FILE_SCHEMA(('%s'));\nENDSEC;\nDATA;\n")
PROJECT = "#1=IFCPROJECT('0YvctVUKr0kugbFTf53O9L',$,'P',$,$,$,$,$,$);\n"


def broken_inputs(tmp: Path):
    """망가진 입력 5종(빈 파일, 쓰레기, 끝이 잘림, IFC2X2, 일부 줄이 깨짐)의 결과를 모은다."""
    cases = {
        "empty.ifc": b"",
        "garbage.ifc": "이건 IFC 가 아님\n".encode("utf-8"),
        "truncated.ifc": (STEP_HEAD % "IFC4" + PROJECT).encode("utf-8"),
        "ifc2x2.ifc": (STEP_HEAD % "IFC2X2_FINAL" + PROJECT + "ENDSEC;\nEND-ISO-10303-21;\n").encode("utf-8"),
        "corrupt-lines.ifc": (STEP_HEAD % "IFC4" + "#1=IFCPROJECT('0YvctVUKr0kugbFTf53O9L',$,'P',$,$,$,$,$,$\n"
                              "#2=garbage((((;\nENDSEC;\nEND-ISO-10303-21;\n").encode("utf-8"),
    }
    out = {}
    for name, data in cases.items():
        p = tmp / name
        p.write_bytes(data)
        try:
            res = run.run_file(p)
            out[name] = {"exit_code": 0, "schema": res["schema"], "unreadable_schema": res["unreadable_schema"],
                         "parse_errors": res.get("parse_errors"), "summary": res["summary"],
                         "KR-CODE-01": next(c["status"] for c in res["checks"] if c["id"] == "KR-CODE-01")}
        except run.InputError as e:
            out[name] = {"exit_code": 2, "error": {"code": e.code, "message": e.message}}
    return {"note": "krrules.run 에 망가진 입력을 넣은 결과. exit_code 2 = 오류 JSON, 0 = 결과 산출(IFC2X2 는 KR-CODE-01 위반)",
            "cases": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--building-architecture")
    a = ap.parse_args()
    ra01 = ROOT.parent / "s0-baseline" / "RA-01.ifc"
    dump(run.run_file(ra01), "RA-01.json")
    dump(run.run_file(ra01, energy=True), "RA-01.energy.json")
    dump(run.run_file(ra01, deliverable=True), "RA-01.deliverable.json")
    dump(run.run_file(ra01, profile="seumteo"), "RA-01.seumteo.json")
    if a.building_architecture:
        dump(run.run_file(Path(a.building_architecture)), "Building-Architecture.json")
        dump(run.run_file(Path(a.building_architecture), energy=True), "Building-Architecture.energy.json")
    import fixtures
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        p = d / "00_본관_BA.ifc"
        fixtures.build_compliant().write(str(p))
        res = run.run_file(p, deliverable=True, energy=True)
        res["sha256"] = None   # 합성 파일은 실행마다 GlobalId·시각이 달라 해시를 기록하지 않음(결과를 바이트 재현하려고)
        dump(res, "compliant-fixture.json")
        # 세움터 프로파일(초안): 세움터식 합성 모델을 세움터 규칙으로 돌린 결과(조달청 기본 결과와 파일이 따로다)
        q = d / "OO프로젝트_본관_BA_ALL.ifc"
        fixtures.build_seumteo().write(str(q))
        res = run.run_file(q, deliverable=True, profile="seumteo")
        res["sha256"] = None
        dump(res, "seumteo-fixture.json")
        # 납품 폴더 검사: 규칙을 지키는 합성 폴더 한 곳
        pkg = d / "납품"
        for sub in ("BIM 보고서", "IFC/건물", "원본/건물", "도면"):
            (pkg / sub).mkdir(parents=True)
        for n in ("00_건물_BA.ifc", "00_건물_B.ifc"):
            (pkg / "IFC" / "건물" / n).write_text("x")
        dump(run.run_package(pkg), "package-synthetic.json")
        bi = broken_inputs(d)
    write_json(bi, "broken-inputs.json")
    print("broken-inputs.json", {k: v.get("error", {}).get("code", v.get("KR-CODE-01")) for k, v in bi["cases"].items()})


if __name__ == "__main__":
    main()
