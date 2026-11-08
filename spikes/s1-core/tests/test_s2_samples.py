# SPDX-License-Identifier: MPL-2.0
"""S2: 외래 표본(T1~T3). Building-Architecture.ifc(공개 표본)와 Revit 내보내기 표본(비공개, 로컬)을 통과시킨다.

환경변수:
- DOBIM_IFCDIFF_PYTHON  ifcdiff 가 설치된 파이썬
- DOBIM_BA_IFC          Building-Architecture.ifc (기본: spikes/s0-baseline/out/samples/)
- DOBIM_REVIT_DIR       RA-01_IFC4RV.ifc / RA-01_IFC4DTV.ifc / RA-01_IFC2x3.ifc 가 있는 폴더(저장소에 없는 비공개 표본). 없으면 T3 은 건너뛴다.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "spikes" / "s2-roundtrip"))
import scenarios as S  # noqa: E402

pytestmark = pytest.mark.skipif(not S.have_ifcdiff(), reason="ifcdiff 가 없다(DOBIM_IFCDIFF_PYTHON)")

SAMPLES = [pytest.param(S.ba_path, id="BA"),
           pytest.param(lambda: S.revit_path("IFC4RV"), id="revit_ifc4rv"),
           pytest.param(lambda: S.revit_path("IFC4DTV"), id="revit_ifc4dtv")]


def _need(getter):
    p = getter()
    if p is None:
        pytest.skip("표본 파일이 없다(DOBIM_BA_IFC / DOBIM_REVIT_DIR)")
    return p


@pytest.mark.parametrize("getter", SAMPLES)
def test_T1_T3_가져오기_내보내기는_ifcdiff_차이가_0이고_전_요소가_foreign(getter, tmp_path):
    r = S.t1_passthrough(_need(getter), tmp_path)
    assert r["all_foreign"] and r["elements"]["foreign"] == r["products_in"] and r["products_in"] == r["products_out"]
    assert r["diff"]["ifcdiff_total"] == 0 and r["diff"]["strict_total"] == 0, r["diff"]
    assert r["hash_reopen_equal"] and r["hash_cross_process_equal"]
    assert r["intent_consistency_bad"] == [] and r["undo_nothing"]
    m = r["roundtrip_check"]
    # M1~M7, X1: 모두 통과. M8(유효성)은 원본 자신의 오류 수와 같아야 한다(가져오기가 오류를 늘리지 않는다): 아래에서 따로 본다
    for k in ("M1", "M2", "M3", "M4", "M5", "M6", "X1"):
        assert m[k]["pass"], (k, m[k])
    assert m["M8"]["value"] == m["X2"]["value"], m


@pytest.mark.parametrize("getter", SAMPLES)
def test_T2_T3_속성_편집_1건의_차이는_그_요소의_그_속성뿐(getter, tmp_path):
    r = S.t2_property_edit(_need(getter), tmp_path)
    d = r["diff"]
    assert r["changed_is_target"] and r["props_changed"] == 1
    assert d["ifcdiff_changed"] == 1 and d["ifcdiff_added"] == 0 and d["ifcdiff_deleted"] == 0
    assert d["ifcdiff_changed_kinds"] == {"properties_changed": 1} and d["strict_products_changed"] == 0
    assert r["products_in"] == r["products_out"]
    assert r["after_undo_diff"]["differences"] == 0
    assert r["delete_changed_is_target"]
    m = r["roundtrip_check"]
    for k in ("M1", "M2", "M4", "M5", "M6", "X1"):
        assert m[k]["pass"], (k, m[k])
    # M3(표준 Pset 값 보존율)는 편집한 값 1건만큼 100% 밑이다. 그보다 많이 어긋나면 안 된다
    if r["ref_pset_values"]:
        assert m["M3"]["value"] >= 100.0 * (1 - 1.0 / r["ref_pset_values"]) - 0.01, m["M3"]


def test_T3_IFC2x3_는_정의된_오류로_거절하고_프로젝트를_남기지_않는다(tmp_path):
    p = S.revit_path("IFC2x3")
    if p is None:
        pytest.skip("Revit 표본이 없다(DOBIM_REVIT_DIR)")
    r = S.reject_checks(tmp_path, p)
    assert r["ifc2x3"] == "UNSUPPORTED_SCHEMA" and r["ifc2x3_project_left_behind"] is False


def test_외래_파일에_저작_요소를_더하면_원본_요소는_그대로이고_mm_파일만_된다(tmp_path):
    """BA(mm): 레벨·벽·문을 더해도 내보낸 파일에서 바뀐 것은 새 요소와 건물(새 레벨의 집합 소속)뿐이고 유효하다. Revit(m): UNSUPPORTED."""
    ba = _need(S.ba_path)
    b = S.Bench(tmp_path / "ba")
    b.put(ba, "in.ifc")
    assert b.import_ifc("in.ifc")["ok"]
    h0 = S.trip(b.hashes())
    lv, w, d = "3" + f"{9:021d}", "1" + f"{9:021d}", "2" + f"{9:021d}"
    assert b.run("level.create", {"id": lv, "name": "S2-레벨", "elevation": 12000})["ok"]
    assert b.run("wall.create", {"id": w, "level": lv, "axis": [0, 0, 4000, 0]})["ok"]
    assert b.run("door.place", {"id": d, "host": w, "offset": 2000})["ok"]
    h1 = S.trip(b.hashes())
    assert h1 != h0 and b.run("model.validate", {})["result"]["errors"] == 0
    out = b.export("o.ifc")
    ids = S.ifdiff(b.ws / "in.ifc", out, ids=True)["changed_ids"]
    new = {lv, w, d}
    changed_old = [i for i in ids if i not in new]
    assert new <= set(ids) and len(changed_old) == 1   # 바뀐 기존 요소는 새 레벨을 집합으로 받은 건물 하나
    b.env.close()
    assert S.trip(S.Bench(tmp_path / "ba").hashes()) == h1
    rv = S.revit_path("IFC4RV")
    if rv is not None:
        b2 = S.Bench(tmp_path / "rv")
        b2.put(rv, "in.ifc")
        assert b2.import_ifc("in.ifc")["ok"]
        r = b2.run("level.create", {"name": "X", "elevation": 0})
        assert not r["ok"] and r["error"]["code"] == "UNSUPPORTED"
        b2.close()
