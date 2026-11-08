"""results/*.json 을 다시 만든다.

사용(스파이크 폴더에서):
  python make_results.py --building-architecture <Building-Architecture.ifc 경로>
RA-01 은 ../s0-baseline/RA-01.ifc 를 쓴다. Building-Architecture.ifc 는 공개 외래 표본(저장소에 없음)이라 경로를 받는다.
양성 대조군은 tests/fixtures.py 로 합성한다.
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


def dump(res, name):
    out = ROOT / "results" / name
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(name, res["schema"], res["summary"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--building-architecture")
    a = ap.parse_args()
    dump(run.run_file(ROOT.parent / "s0-baseline" / "RA-01.ifc"), "RA-01.json")
    dump(run.run_file(ROOT.parent / "s0-baseline" / "RA-01.ifc", deliverable=True), "RA-01.deliverable.json")
    if a.building_architecture:
        dump(run.run_file(Path(a.building_architecture)), "Building-Architecture.json")
    import fixtures
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "00_본관_BA.ifc"
        fixtures.build_compliant().write(str(p))
        dump(run.run_file(p, deliverable=True), "compliant-fixture.json")


if __name__ == "__main__":
    main()
