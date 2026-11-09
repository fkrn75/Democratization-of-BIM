# SPDX-License-Identifier: MPL-2.0
"""S2 독립 검수에서 나온 결함의 회귀 시험(R1~R11). 시나리오는 spikes/s2-roundtrip/scenarios_review.py 에 있다.

공식 ifcdiff 가 필요하다: 환경변수 DOBIM_IFCDIFF_PYTHON. 없으면 이 파일은 건너뛴다(건너뛴 것은 통과가 아니다).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "spikes" / "s2-roundtrip"))
import scenarios as S  # noqa: E402
import scenarios_review as V  # noqa: E402

pytestmark = pytest.mark.skipif(not S.have_ifcdiff(), reason="ifcdiff 가 없다(DOBIM_IFCDIFF_PYTHON)")


def test_R1_수정하지_않은_저작_요소의_외부_스타일은_보존되고_형상이_바뀌면_새_솔리드로_이월된다(tmp_path):
    r = V.r1_style_preserved(tmp_path)
    assert r["elements"] == {"authored": 7, "foreign": 0, "detached": 0}
    assert r["first_flush_projected"] == 0   # 명세가 그대로인 가져온 요소는 첫 투영에서 다시 만들지 않는다
    assert r["styled_items_in_out"] == [2, 2]
    assert r["w2_style_after_export"] == ["red"] and r["d1_style_after_export"] == ["blue-door"]
    assert r["diff_import_vs_export"]["differences"] == 0
    # 의도로 형상이 실제로 바뀐 뒤에도 스타일은 새 솔리드에 있고 찌꺼기 솔리드가 없다
    a = r["after_edit"]
    assert a["w2_depth"] == 3500.0 and a["w2_style"] == ["red"] and a["d1_style"] == ["blue-door"]
    assert a["styled_items"] == 2 and a["orphan_solids"] == 0 and a["validate_errors"] == 0
    assert r["after_undo"]["w2_style"] == ["red"] and r["after_undo"]["diff_vs_import"]["differences"] == 0
    assert r["reopen_equal"] and r["reopen_first_flush_projected"] == 0


def test_R2_외부_부속이_붙은_저작_벽을_지웠다_되돌리면_해시와_부속이_복원된다(tmp_path):
    r = V.r2_delete_undo_attachments(tmp_path)
    assert r["delete_ok"] and r["hash_changed_by_delete"]
    assert r["after_delete"] == {"wall_gone": True, "dangling_empty_relations": 0, "validate_errors": 0}
    assert r["hash_restored"], "지우기 → 되돌리기 후 state/derived/ifc 해시가 처음과 같아야 한다"
    got = r["restored"]
    assert got["type"] == "TypeX" and got["qto"] == ["Qto_WallBaseQuantities"] and got["classification"] == ["Ss_25"]
    assert got["material"] == "IfcMaterialLayerSetUsage" and got["style"] == ["red"]
    assert (got["styled_items"], got["types"], got["classifications"], got["references"]) == (1, 1, 1, 1)   # 중복 없이
    assert got["validate_errors"] == 0
    # 내용 차이는 0: ifcdiff 가 센 변경은 속성셋 STEP 번호(id)만 다른 것이고, 엄격 보강(수량 세트·타입·스타일 포함)은 차이가 없다
    d = r["diff_import_vs_export"]
    assert r["diff_id_only"]["content"] == 0 and d["strict_total"] == 0 and d["ifcdiff_added"] == 0 and d["ifcdiff_deleted"] == 0
    assert r["redo_undo_hash_restored"]
    # 지운 채로 닫았다 열어도 같은 해시이고, 그 상태에서 되돌려도 부속이 돌아온다
    assert r["reopen_deleted_equal"] and r["reopen_undo_ok"] and r["reopen_undo_hash_restored"]
    rr = r["reopen_restored"]
    assert rr == {"type": "TypeX", "qto": ["Qto_WallBaseQuantities"], "classification": ["Ss_25"], "style": ["red"]}
    assert r["reopen_diff"]["strict_total"] == 0


def test_R3_사이트의_위경도_주소만_바뀐_뼈대는_뼈대로_남아_분류에_영향이_없다(tmp_path):
    r = V.r3_skeleton(tmp_path)
    p = r["props_only"]
    assert p["elements"] == {"authored": 7, "foreign": 0, "detached": 0} and p["skeleton"] and p["detached_total"] == 0
    assert p["warnings_total"] == 0 and p["wall_create_ok"]
    assert p["undo_diff_vs_import"]["differences"] == 0 and p["site_props_survive"]
    # 뼈대의 배치가 바뀌면 보수적으로 뼈대가 아니다(+ 경고)
    c = r["placement_changed"]
    assert not c["skeleton"] and c["warns_skeleton"] and c["elements"]["detached"] >= 1
    # 이름이 다르면 뼈대가 아니다
    assert r["renamed"]["skeleton"] is False


def test_R4_외부_도구가_개구부_GlobalId를_바꿔도_수정하지_않은_문은_그대로다(tmp_path):
    r = V.r4_opening_identity(tmp_path)
    assert r["elements"] == {"authored": 7, "foreign": 0, "detached": 0} and r["opening_gids_differ_from_original"]
    assert r["opening_gids_kept"] and r["diff_vs_import"]["differences"] == 0
    assert r["opening_gids_kept_after_door_edit"] and r["opening_gids_kept_after_delete_undo"]


@pytest.mark.parametrize("label", ["axis_1e200", "axis_2e9"])
def test_R5_범위를_벗어난_의도_숫자는_INTERNAL이_아니라_외래로_분류하고_경고한다(label, tmp_path):
    r = V.r5_numeric_range(tmp_path)[label]
    assert r["ok"] and r["elements"] == {"authored": 5, "foreign": 3, "detached": 0}   # 벽 W3 과 그 문 D2 가 외래로 간다
    assert r["warned"]


def test_R6_단위_바뀜은_전체_분리_외래_개구부가_더해진_벽은_분리_레벨_고도는_경고(tmp_path):
    r = V.r6_misses(tmp_path)
    u = r["unit_changed"]
    assert u["elements"]["authored"] == 0 and u["elements"]["detached"] == 7 and u["detached_total"] == 7
    assert u["warns_unit"] and u["wall_move_frozen"] == "ELEMENT_FROZEN"
    v = r["foreign_void_on_wall"]
    assert v["wall_origin"] == "detached" and v["elements"]["detached"] == 1
    lv = r["level_elevation"]
    assert lv["detached_is_level"] and lv["warns_above"]


def test_R8_문_배치_변경은_그_문만_분리하고_스타일_추가는_변조가_아니다(tmp_path):
    r = V.r8_more_tampers(tmp_path)
    d = r["door_move"]
    assert d["detached"] and d["reasons"] == [["배치"]] and d["frozen"] == "ELEMENT_FROZEN" and d["opening_foreign"]
    assert r["style_added"] == {"elements": {"authored": 7, "foreign": 0, "detached": 0}, "detached_total": 0}


def test_R9_벽을_뚫는_외래_개구부가_함께_지워지면_경고한다(tmp_path):
    r = V.r9_foreign_void_delete_warning(tmp_path)
    assert r["delete_ok"] and r["warned"]


# ---------------------------------------------------------------- R7: 엄격 보강이 스타일·타입·수량 세트까지 본다
def _two_files(tmp_path):
    b, f = V.prepare(tmp_path)
    f.write(str(tmp_path / "a.ifc"))
    return b, f


def test_R7_strict_diff는_스타일_표현구조_타입_수량세트_차이를_잡는다(tmp_path):
    import ifcopenshell
    import ifcopenshell.api.pset as api_pset
    import ifcopenshell.api.root as api_root
    import ifcopenshell.api.type as api_type
    from dobim_core import fixture as F
    sys.path.insert(0, str(ROOT / "spikes" / "s2-roundtrip" / "tools"))
    import ifcdiff_check as C
    b, f = _two_files(tmp_path)
    try:
        a = str(tmp_path / "a.ifc")
        assert C.strict_diff(a, a)["total"] == 0

        def variant(name, mutate):
            g = ifcopenshell.open(a)
            mutate(g)
            p = str(tmp_path / f"{name}.ifc")
            g.write(p)
            return C.strict_diff(a, p)

        def add_style(g):
            V.add_red_style(g, V.body_solid(g, F.W1))

        s = variant("style", add_style)
        assert s["total"] > 0 and s["other_added"] >= 1

        def add_type(g):
            t = api_root.create_entity(g, ifc_class="IfcWallType", name="T")
            api_type.assign_type(g, related_objects=[g.by_guid(F.W1)], relating_type=t)

        assert variant("type", add_type)["total"] > 0

        def lone_type(g):   # 어느 요소에도 지정되지 않은 타입 객체
            api_root.create_entity(g, ifc_class="IfcWallType", name="Lone")

        assert variant("lone", lone_type)["other_added"] >= 1

        def add_qto(g):
            q = api_pset.add_qto(g, product=g.by_guid(F.W1), name="Qto_WallBaseQuantities")
            api_pset.edit_qto(g, qto=q, properties={"Length": 1.0})

        assert variant("qto", add_qto)["total"] > 0

        def tweak_repr(g):   # 표현 구조: 돌출 깊이
            V.body_solid(g, F.W1).Depth = V.body_solid(g, F.W1).Depth + 1.0

        assert variant("repr", tweak_repr)["products_changed"] == 1

        # 같은 스타일이라도 색이 다르면 차이다
        def two(g, r):
            col = g.createIfcColourRgb(None, r, 0.0, 0.0)
            ssr = g.createIfcSurfaceStyleRendering(col, None, None, None, None, None, None, None, "FLAT")
            g.createIfcStyledItem(V.body_solid(g, F.W1), [g.createIfcSurfaceStyle("c", "BOTH", [ssr])], None)

        g1 = ifcopenshell.open(a)
        two(g1, 1.0)
        g1.write(str(tmp_path / "c1.ifc"))
        g2 = ifcopenshell.open(a)
        two(g2, 0.5)
        g2.write(str(tmp_path / "c2.ifc"))
        assert C.strict_diff(str(tmp_path / "c1.ifc"), str(tmp_path / "c2.ifc"))["total"] > 0
    finally:
        b.close()


def test_R7_코어가_직접_만든_모델의_ifc_해시는_확장_전과_같다(tmp_path):
    """semantic_dump 확장은 스타일·타입·수량 세트가 없는 모델의 해시를 바꾸지 않는다(기존 해시 계약 유지)."""
    from dobim_core import fixture as F
    from dobim_core.engine import Engine
    Engine.create(tmp_path / "p.dobim", "s2", guid=F.GUID).close()
    b = S.Bench(tmp_path)
    for name, args in F.SCRIPT:
        assert b.run(name, args)["ok"]
    import json
    prods, rels, others = b.env.engine("p.dobim").ifc.semantic_dump()
    assert prods and rels
    assert {json.loads(x)["t"] for x in others} <= {"IfcProject", "IfcMaterial"}   # 새 항목(스타일·타입·수량 세트)이 끼어들지 않는다
    b.close()


def test_R10_projection_hash는_가져올_때의_값으로_남고_편집해도_지워지지_않는다(tmp_path):
    import sqlite3
    from dobim_core import fixture as F
    b, f = V.prepare(tmp_path)
    try:
        V.write_import(b, f)
        db = tmp_path / V.P

        def hashes():
            c = sqlite3.connect(str(db))
            try:
                return dict(c.execute("SELECT id, projection_hash FROM element WHERE origin='authored'"))
            finally:
                c.close()

        h0 = hashes()
        assert len(h0) == 7 and all(h0.values())
        assert b.run("wall.move", {"id": F.W1, "dx": 300}, project=V.P)["ok"]
        b.hashes(V.P)   # 투영을 일으켜도
        assert hashes() == h0
    finally:
        b.close()
