# SPDX-License-Identifier: MPL-2.0
"""S2: 저작 왕복(T4), 변조(T5), 의존 요소(T5b), 거절 동작, 규칙 단위 시험.

공식 ifcdiff 가 필요하다: 환경변수 DOBIM_IFCDIFF_PYTHON 에 ifcdiff 가 설치된 파이썬을 지정한다(spikes/s2-roundtrip/results/report.md 참고).
없으면 이 파일은 건너뛴다(건너뛴 것은 통과가 아니다: 결과 파일은 시험을 돌린 환경에서 만든다).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "spikes" / "s2-roundtrip"))
import scenarios as S  # noqa: E402

from dobim_core import fixture as F  # noqa: E402

pytestmark = pytest.mark.skipif(not S.have_ifcdiff(), reason="ifcdiff 가 없다(DOBIM_IFCDIFF_PYTHON)")


def test_T4_저작_왕복은_전_요소가_authored_이고_해시가_같다(tmp_path):
    r = S.t4_authored_roundtrip(tmp_path)
    assert r["elements"] == {"authored": 7, "foreign": 0, "detached": 0}
    assert r["all_authored"] and r["detached_total"] == 0 and r["skeleton"]
    assert r["state_hash_equal"] and r["derived_hash_equal"] and r["ifc_hash_equal"]
    assert r["diff_first_vs_second_export"]["differences"] == 0
    assert r["intent_consistency_bad"] == [] and r["validate_errors"] == 0
    # 가져온 프로젝트에서 계속 저작해도 취소·재실행·해시가 깨지지 않는다
    assert r["authored_edit_ok"] and r["hash_changes_on_edit"] and r["undo_restores_hash"]
    assert r["authored_delete_ok"] and r["undo_delete_restores_hash"]
    assert r["delete_diff"]["deleted"] >= 1 and r["delete_diff"]["added"] == 0
    assert r["reopen_equal"] and r["cross_process_equal"]


def test_T5_변조한_벽_둘만_분리되고_속성만_바뀐_벽은_저작으로_남는다(tmp_path):
    r = S.t5_tamper(tmp_path)
    assert r["elements"] == {"authored": 8, "foreign": 0, "detached": 2}
    assert r["detached_ids_are_the_two"] and r["warns_name_detached"] == 2
    assert r["detached_reasons"]["move"] == ["배치"] and r["detached_reasons"]["shape"] == ["속성 Representation"]
    assert r["prop_only_stays_authored"] and r["prop_only_value_visible"]
    # 변조가 실제로 있었음을 도구가 본다(음성 대조): 형상 2건 + 속성 1건
    assert r["tamper_vs_original_diff"]["ifcdiff_changed"] == 3
    assert r["tamper_vs_original_diff"]["ifcdiff_changed_kinds"] == {"geometry_changed": 2, "properties_changed": 1}
    # 분리된 벽의 형상 커맨드는 정의된 오류, 속성 편집은 허용
    assert r["frozen_rejected_all"], r["frozen_errors"]
    assert r["detached_pset_edit_ok"] and r["authored_move_ok"]
    # 내보내기: 변조 형상 보존 + 저작 요소만 갱신(이동한 벽 1, 속성을 더한 분리 벽 1)
    assert r["tampered_geometry_preserved"] and r["export_diff_changed_ids_ok"]
    assert r["export_vs_tampered_diff"]["ifcdiff_added"] == 0 and r["export_vs_tampered_diff"]["ifcdiff_deleted"] == 0
    assert r["untouched_export_diff"]["differences"] == 0
    assert r["intent_consistency_bad"] == [] and r["reopen_equal"] and r["cross_process_equal"]


def test_T5b_호스트가_움직여도_문은_저작으로_남고_개구부를_바꾸면_문이_분리된다(tmp_path):
    r = S.t5b_dependents(tmp_path)
    assert r["exactly_expected"] and r["host_wall_detached"] and r["door_on_moved_host_authored"]
    assert r["door_with_changed_opening_detached"]


def test_거절_동작(tmp_path):
    r = S.reject_checks(tmp_path)
    assert r["garbage"] == "IMPORT_INVALID" and r["exists"] == "FILE_EXISTS"
    assert r["not_ifc_extension"] == "PATH_DENIED" and r["outside_workspace"] == "PATH_DENIED"


def test_가져온_뒤_실패하면_프로젝트_파일을_남기지_않는다(tmp_path):
    b = S.Bench(tmp_path)
    (tmp_path / "bad.ifc").write_bytes(b"not an ifc")
    assert not b.import_ifc("bad.ifc", "p.dobim")["ok"]
    assert not (tmp_path / "p.dobim").exists()
    b.close()


def test_외래_요소_규칙_삭제는_거절하고_속성만_편집된다(tmp_path):
    """foreign 요소: 삭제·형상·재료는 ELEMENT_FROZEN, 속성은 편집·취소·재실행 가능."""
    ba = S.ba_path()
    if ba is None:
        pytest.skip("Building-Architecture.ifc 표본이 없다")
    b = S.Bench(tmp_path)
    b.put(ba, "in.ifc")
    assert b.import_ifc("in.ifc")["ok"]
    foreign = b.ids_of("foreign")
    assert len(foreign) == 20
    eid = foreign[0]
    for name, args in (("element.delete", {"id": eid}), ("wall.move", {"id": eid, "dx": 1}), ("material.set", {"id": eid, "name": "X"}),
                       ("wall.set", {"id": eid, "height": 100})):
        e = b.run(name, args)
        assert not e["ok"] and e["error"]["code"] == "ELEMENT_FROZEN", (name, e)
    h0 = S.trip(b.hashes())
    r = b.run("pset.set", {"id": eid, "pset": "Pset_S2", "prop": "Note", "value": "메모"})
    assert r["ok"] and r["result"]["changed"]
    h1 = S.trip(b.hashes())
    assert h1 != h0
    assert b.run("history.undo", {})["ok"] and S.trip(b.hashes()) == h0
    assert b.run("history.redo", {})["ok"] and S.trip(b.hashes()) == h1
    # 재오픈해도 편집이 남는다
    b.env.close()
    b2 = S.Bench(tmp_path)
    assert S.trip(b2.hashes()) == h1
    assert b2.run("element.get", {"id": eid})["result"]["psets"]["Pset_S2"]["Note"] == "메모"
    # 속성 삭제도 재오픈 뒤 유지된다(가져온 속성의 삭제는 로그 재생이 적용해야 한다)
    got = b2.run("element.get", {"id": eid})["result"]["psets"]
    ps = next((p for p in sorted(got) if p != "Pset_S2"), None)
    if ps:
        pr = sorted(got[ps])[0]
        assert b2.run("pset.delete", {"id": eid, "pset": ps, "prop": pr})["result"]["changed"]
        h2 = S.trip(b2.hashes())
        b2.env.close()
        b3 = S.Bench(tmp_path)
        assert S.trip(b3.hashes()) == h2 and pr not in b3.run("element.get", {"id": eid})["result"]["psets"].get(ps, {})
        b3.close()
    b2.close()


def test_모델_도구는_원본을_외래_기반으로_보존한다(tmp_path):
    """프로젝트 파일 안에 원본 바이트가 들어 있고(스냅숏·복원과 무관하게) 프로젝트 파일만으로 다시 내보낼 수 있다."""
    import sqlite3
    import zlib
    b = S.Bench(tmp_path)
    S.build_authored(b, "src.dobim")
    src = b.export("e1.ifc", "src.dobim")
    assert b.import_ifc("e1.ifc", "dst.dobim")["ok"]
    b.env.close()
    (tmp_path / "e1.ifc").rename(tmp_path / "gone.ifc")   # 원본 파일이 없어도
    c = sqlite3.connect(tmp_path / "dst.dobim")
    n, data = c.execute("SELECT size, data FROM foreign_base WHERE id=1").fetchone()
    assert len(zlib.decompress(data)) == n == src.parent.joinpath("gone.ifc").stat().st_size
    ph = [r[0] for r in c.execute("SELECT projection_hash FROM element")]
    assert len(ph) == 7 and all(isinstance(h, str) and len(h) == 64 for h in ph)   # projection_hash 를 실제로 채운다
    c.close()
    b2 = S.Bench(tmp_path)
    out = b2.export("again.ifc", "dst.dobim")
    assert S.ifdiff(tmp_path / "gone.ifc", out)["differences"] == 0
    b2.close()


def test_표본이_아닌_일반_프로젝트는_영향이_없다(tmp_path):
    """가져오기 없이 만든 S1 프로젝트: origins 키가 없고 외래 기반 표가 비어 있다."""
    import sqlite3
    b = S.Bench(tmp_path)
    S.build_authored(b, "src.dobim")
    info = b.run("project.info", {}, project="src.dobim")["result"]
    assert "origins" not in info and info["counts"] == {"level": 2, "wall": 3, "door": 2}
    c = sqlite3.connect(tmp_path / "src.dobim")
    assert c.execute("SELECT COUNT(*) FROM foreign_base").fetchone()[0] == 0
    c.close()
    b.close()


def test_오류_코드는_종료_코드_표에_있다():
    from dobim_core.errors import EXIT_CODES
    assert EXIT_CODES["ELEMENT_FROZEN"] == 1 and EXIT_CODES["IMPORT_INVALID"] == 1 and EXIT_CODES["UNSUPPORTED_SCHEMA"] == 2


# ---------------------------------------------------------------- 변조 종류별 분류 (탐지 기준 시험)
def _t_name(f, w):
    w.Name = "바뀐 이름"


def _t_profile(f, w):
    for rep in w.Representation.Representations:
        for it in rep.Items:
            if it.is_a("IfcExtrudedAreaSolid"):
                pts = it.SweptArea.OuterCurve.Points
                pts[1].Coordinates = (pts[1].Coordinates[0] + 40.0, pts[1].Coordinates[1])


def _t_container(f, w):
    import ifcopenshell.api.spatial as sp
    sp.assign_container(f, products=[w], relating_structure=f.by_guid(F.LV1))


def _t_material(f, w):
    import ifcopenshell.api.material as mt
    mt.assign_material(f, products=[w], material=mt.add_material(f, name="ExternalSteel"))


def _t_pset(f, w):
    import ifcopenshell.api.pset as ps
    ps.edit_pset(f, pset=ps.add_pset(f, product=w, name="Pset_Ext"), properties={"A": 1})


def _t_no_intent(f, w):
    import ifcopenshell.api.pset as ps
    import ifcopenshell.util.element as ue
    ps.remove_pset(f, product=w, pset=f.by_id(ue.get_pset(w, "DoBIM_Intent")["id"]))


def _t_bad_intent(f, w):
    import ifcopenshell.util.element as ue
    f.by_id(ue.get_pset(w, "DoBIM_Intent")["id"]).HasProperties[0].NominalValue = f.createIfcText("{not json")


def _t_none(f, w):
    pass


@pytest.mark.parametrize("fn,expected", [
    (_t_name, "detached"), (_t_profile, "detached"), (_t_container, "detached"),
    (_t_material, "authored"), (_t_pset, "authored"), (_t_none, "authored"),
    (_t_no_intent, "foreign"), (_t_bad_intent, "foreign")], ids=lambda x: getattr(x, "__name__", x))
def test_변조_종류별_분류(tmp_path, fn, expected):
    import ifcopenshell
    b = S.Bench(tmp_path)
    S.build_authored(b, "src.dobim", extra_walls=3)
    e1 = b.export("e1.ifc", "src.dobim")
    f = ifcopenshell.open(str(e1))
    fn(f, f.by_guid(S.W_MOVE))
    f.write(str(tmp_path / "t.ifc"))
    r = b.import_ifc("t.ifc", "t.dobim")
    assert r["ok"], r
    got = b.run("element.get", {"id": S.W_MOVE}, project="t.dobim")["result"]["origin"]
    assert got == expected
    counts = r["result"]["elements"]
    assert counts["authored"] + counts["detached"] + counts["foreign"] == 10
    if fn is _t_bad_intent:
        assert any("해석할 수 없어" in w for w in r["result"]["warnings"])
    if expected == "authored":   # 다른 요소는 그대로 저작
        assert counts == {"authored": 10, "foreign": 0, "detached": 0}
    b.close()


def test_스냅숏과_복원은_가져온_프로젝트에서도_된다(tmp_path):
    b = S.Bench(tmp_path)
    S.build_authored(b, "src.dobim")
    e1 = b.export("e1.ifc", "src.dobim")
    assert b.import_ifc("e1.ifc", "p.dobim")["ok"]
    first = b.export("out.ifc", "p.dobim")
    assert b.run("pset.set", {"id": F.W1, "pset": "Pset_X", "prop": "N", "value": 1}, project="p.dobim")["ok"]
    b.export("out.ifc", "p.dobim")           # 덮어쓰기: 첫 파일이 스냅숏 1 로 남는다
    snaps = b.run("ifc.snapshots", {"path": "out.ifc"}, project="p.dobim")["result"]["items"]
    assert [s["n"] for s in snaps] == [1]
    assert b.destructive("ifc.restore", {"path": "out.ifc", "snapshot": 1}, "p.dobim")["ok"]
    assert S.ifdiff(e1, tmp_path / "out.ifc")["differences"] == 0 and first.exists()
    b.close()
