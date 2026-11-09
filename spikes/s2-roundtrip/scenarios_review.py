# SPDX-License-Identifier: MPL-2.0
"""S2 독립 검수에서 나온 결함의 재현·회귀 시나리오 (R1~R11). 각 함수는 측정값 사전을 돌려준다(경로·GlobalId 를 넣지 않는다).

시험(tests/test_s2_review.py)과 결과 생성기(run_s2.py → results/s2_review_fixes.json)가 같이 쓴다.
외부 도구의 변조는 ifcopenshell 로 직접 파일을 고쳐 흉내 낸다(실제 도구와 같은 효과: 엔티티를 더하거나 값을 바꾼다).
"""
from __future__ import annotations

import json
from pathlib import Path

import ifcopenshell
import ifcopenshell.api.classification as api_class
import ifcopenshell.api.feature as api_feature
import ifcopenshell.api.material as api_material
import ifcopenshell.api.pset as api_pset
import ifcopenshell.api.root as api_root
import ifcopenshell.api.type as api_type
import ifcopenshell.util.classification as uclass
import ifcopenshell.util.element as ue

import scenarios as S
from dobim_core import fixture as F

P = "t.dobim"


def prepare(work: Path):
    """기준 프로젝트를 저작해 내보낸다. (작업대, 내보낸 파일을 연 ifcopenshell 파일)를 돌려준다. 호출 쪽이 고쳐서 write_import 로 가져온다."""
    b = S.Bench(work)
    S.build_authored(b, "src.dobim")
    e1 = b.export("e1.ifc", "src.dobim")
    return b, ifcopenshell.open(str(e1))


def write_import(b: S.Bench, f, name="t.ifc", project=P) -> dict:
    f.write(str(b.ws / name))
    r = b.import_ifc(name, project)
    return r


def body_solid(f, gid):
    for rep in f.by_guid(gid).Representation.Representations:
        for it in rep.Items:
            if it.is_a("IfcExtrudedAreaSolid"):
                return it


def add_red_style(f, item, name="red"):
    col = f.createIfcColourRgb(None, 1.0, 0.0, 0.0)
    ssr = f.createIfcSurfaceStyleRendering(col, None, None, None, None, None, None, None, "FLAT")
    ss = f.createIfcSurfaceStyle(name, "BOTH", [ssr])
    return f.createIfcStyledItem(item, [ss], None)


def styled_by(f, gid) -> list[str]:
    """요소 몸체 솔리드에 입혀진 스타일 이름들."""
    out = []
    for rep in f.by_guid(gid).Representation.Representations:
        for it in rep.Items:
            for st in it.StyledByItem or ():
                out += [s.Name for s in st.Styles]
    return out


def orphan_solids(f) -> int:
    """어느 표현에도 속하지 않은 돌출 솔리드(스타일 등이 붙들어 둔 찌꺼기) 수."""
    used = {it.id() for r in f.by_type("IfcShapeRepresentation") for it in r.Items}
    return sum(1 for s in f.by_type("IfcExtrudedAreaSolid") if s.id() not in used)


# ---------------------------------------------------------------- R1: 스타일 보존
def r1_style_preserved(work: Path) -> dict:
    """수정하지 않은 저작 요소의 외부 스타일은 가져오기·내보내기에서 그대로이고(들어온 엔티티를 다시 투영하지 않음),
    의도로 형상이 바뀌어 다시 투영돼도 새 솔리드로 옮겨진다."""
    b, f = prepare(work)
    try:
        add_red_style(f, body_solid(f, F.W2))
        add_red_style(f, body_solid(f, F.D1), "blue-door")
        r = write_import(b, f)["result"]
        eng = b.env.engine(P)
        res = {"elements": r["elements"], "first_flush_projected": eng.flush_ifc()}
        out = b.export("o.ifc", P)
        g = ifcopenshell.open(str(out))
        res["styled_items_in_out"] = [len(f.by_type("IfcStyledItem")), len(g.by_type("IfcStyledItem"))]
        res["w2_style_after_export"] = styled_by(g, F.W2)
        res["d1_style_after_export"] = styled_by(g, F.D1)
        res["diff_import_vs_export"] = S.ifdiff(b.ws / "t.ifc", out)
        # 형상이 실제로 바뀐다: 높이 변경 → 다시 투영 → 스타일은 새 솔리드로 이월(규칙)
        assert b.run("wall.set", {"id": F.W2, "height": 3500}, project=P)["ok"]
        assert b.run("door.set", {"id": F.D1, "width": 1000}, project=P)["ok"]
        out2 = b.export("o2.ifc", P)
        g2 = ifcopenshell.open(str(out2))
        res["after_edit"] = {"w2_style": styled_by(g2, F.W2), "d1_style": styled_by(g2, F.D1),
                             "styled_items": len(g2.by_type("IfcStyledItem")), "orphan_solids": orphan_solids(g2),
                             "w2_depth": body_solid(g2, F.W2).Depth, "validate_errors": b.run("model.validate", {}, project=P)["result"]["errors"]}
        # 취소해서 원래 형상으로 돌아와도 스타일이 남는다
        assert b.run("history.undo", {}, project=P)["ok"] and b.run("history.undo", {}, project=P)["ok"]
        g3 = ifcopenshell.open(str(b.export("o3.ifc", P)))
        res["after_undo"] = {"w2_style": styled_by(g3, F.W2), "d1_style": styled_by(g3, F.D1), "diff_vs_import": S.ifdiff(b.ws / "t.ifc", b.ws / "o3.ifc")}
        # 다시 연 엔진도 같다
        h = b.hashes(P)
        b.env.close()
        b2 = S.Bench(work)
        res["reopen_equal"] = S.trip(b2.hashes(P)) == S.trip(h)
        res["reopen_first_flush_projected"] = b2.env.engine(P).flush_ifc()
        b2.close()
        return res
    finally:
        b.close()


# ---------------------------------------------------------------- R2: 외부 부속이 붙은 요소의 지우기 → 취소
def r2_delete_undo_attachments(work: Path) -> dict:
    """타입 지정·수량 세트·분류·복합 재료·몸체 스타일이 붙은 저작 벽을 지웠다 되돌리면 해시와 내보낸 파일이 처음과 같다. 다시 연 뒤 되돌려도 같다."""
    b, f = prepare(work)
    try:
        w = f.by_guid(F.W3)
        t = api_root.create_entity(f, ifc_class="IfcWallType", name="TypeX")
        api_type.assign_type(f, related_objects=[w], relating_type=t)
        q = api_pset.add_qto(f, product=w, name="Qto_WallBaseQuantities")
        api_pset.edit_qto(f, qto=q, properties={"Length": 6000.0})
        cl = api_class.add_classification(f, classification="Uniclass")
        api_class.add_reference(f, products=[w], identification="Ss_25", name="Walls", classification=cl)
        ls = api_material.add_material_set(f, name="LS", set_type="IfcMaterialLayerSet")
        api_material.add_layer(f, layer_set=ls, material=api_material.add_material(f, name="Brick"))
        api_material.assign_material(f, products=[w], type="IfcMaterialLayerSetUsage", material=ls)
        add_red_style(f, body_solid(f, F.W3))
        res = {"elements": write_import(b, f)["result"]["elements"]}
        h0 = S.trip(b.hashes(P))
        d = b.run("element.delete", {"id": F.W3}, project=P)
        res["delete_ok"] = d["ok"]
        res["hash_changed_by_delete"] = S.trip(b.hashes(P)) != h0
        e_del = b.export("e_del.ifc", P)
        res["after_delete"] = {"wall_gone": not any(x.GlobalId == F.W3 for x in ifcopenshell.open(str(e_del)).by_type("IfcWall")),
                               "dangling_empty_relations": _empty_relations(ifcopenshell.open(str(e_del))),
                               "validate_errors": b.run("model.validate", {}, project=P)["result"]["errors"]}
        assert b.run("history.undo", {}, project=P)["ok"]
        res["hash_restored"] = S.trip(b.hashes(P)) == h0
        out = b.export("o.ifc", P)
        g = ifcopenshell.open(str(out))
        gw = g.by_guid(F.W3)
        res["restored"] = {"type": ue.get_type(gw).Name if ue.get_type(gw) else None,
                           "qto": sorted(ue.get_psets(gw, qtos_only=True)), "classification": _classification_ids(gw),
                           "material": type(ue.get_material(gw)).__name__ if ue.get_material(gw) is None else ue.get_material(gw).is_a(),
                           "style": styled_by(g, F.W3), "styled_items": len(g.by_type("IfcStyledItem")),
                           "types": len(g.by_type("IfcWallType")), "classifications": len(g.by_type("IfcClassification")),
                           "references": len(g.by_type("IfcClassificationReference")), "validate_errors": b.run("model.validate", {}, project=P)["result"]["errors"]}
        res["diff_import_vs_export"] = S.ifdiff(b.ws / "t.ifc", out)
        res["diff_id_only"] = id_only_changes(b.ws / "t.ifc", out)
        # redo → undo 를 한 번 더
        assert b.run("history.redo", {}, project=P)["ok"] and b.run("history.undo", {}, project=P)["ok"]
        res["redo_undo_hash_restored"] = S.trip(b.hashes(P)) == h0
        # 지운 채로 닫았다 열어서 되돌린다
        assert b.run("history.redo", {}, project=P)["ok"]
        h_del = S.trip(b.hashes(P))
        b.env.close()
        b2 = S.Bench(work)
        res["reopen_deleted_equal"] = S.trip(b2.hashes(P)) == h_del
        u = b2.run("history.undo", {}, project=P)
        res["reopen_undo_ok"] = u["ok"]
        res["reopen_undo_hash_restored"] = u["ok"] and S.trip(b2.hashes(P)) == h0
        out2 = b2.export("o2.ifc", P)
        g2 = ifcopenshell.open(str(out2))
        gw2 = g2.by_guid(F.W3)
        res["reopen_restored"] = {"type": ue.get_type(gw2).Name if ue.get_type(gw2) else None, "qto": sorted(ue.get_psets(gw2, qtos_only=True)),
                                  "classification": _classification_ids(gw2), "style": styled_by(g2, F.W3)}
        res["reopen_diff"] = S.ifdiff(b.ws / "t.ifc", out2)
        b2.close()
        return res
    finally:
        b.close()


def _empty_relations(f) -> int:
    n = 0
    for r in f.by_type("IfcRelationship"):
        for k, v in r.get_info(recursive=False).items():
            if k.startswith("Related") and isinstance(v, tuple) and not v:
                n += 1
    return n


def _classification_ids(w) -> list[str]:
    return sorted(r.Identification for r in uclass.get_references(w))


def id_only_changes(old: Path, new: Path) -> dict:
    """ifcdiff 는 속성셋의 STEP 번호(id)가 달라져도 properties_changed 로 센다. 번호만 다른 변경과 내용이 다른 변경을 나눠 센다."""
    return S.ifcdiff_check.id_only_summary(str(old), str(new))


# ---------------------------------------------------------------- R3: 뼈대의 비형상 속성
def r3_skeleton(work: Path) -> dict:
    out = {}
    # (a) 위경도·주소·설명만 바뀐 뼈대: 뼈대로 남고 요소 분류에 영향이 없다
    b, f = prepare(work / "a")
    try:
        site = f.by_type("IfcSite")[0]
        site.RefLatitude = (37, 33, 0, 0)
        site.RefLongitude = (126, 58, 0, 0)
        site.RefElevation = 38.0
        site.Description = "서울 사이트"
        site.SiteAddress = f.createIfcPostalAddress(None, "대지", None, None, ["세종대로 1"], None, "서울", None, "03100", "KR")
        f.by_type("IfcBuilding")[0].LongName = "시험동"
        r = write_import(b, f)["result"]
        out["props_only"] = {"elements": r["elements"], "skeleton": r["skeleton"], "detached_total": r["detached_total"], "warnings_total": r["warnings_total"]}
        wc = b.run("wall.create", {"level": F.LV1, "axis": [0, 0, 1000, 0]}, project=P)
        out["props_only"]["wall_create_ok"] = wc["ok"]
        o = b.export("o.ifc", P)
        out["props_only"]["diff_vs_import"] = S.ifdiff(b.ws / "t.ifc", o, ids=True)["changed_ids"] != []   # 새 벽·건물이 바뀐다
        assert b.run("history.undo", {}, project=P)["ok"]
        o2 = b.export("o2.ifc", P)
        out["props_only"]["undo_diff_vs_import"] = S.ifdiff(b.ws / "t.ifc", o2)
        out["props_only"]["site_props_survive"] = ifcopenshell.open(str(o2)).by_type("IfcSite")[0].RefLatitude == (37, 33, 0, 0)
    finally:
        b.close()
    # (b) 뼈대의 배치가 바뀌면(형상 변경) 보수적으로 뼈대로 보지 않고 경고한다
    b, f = prepare(work / "b")
    try:
        site = f.by_type("IfcSite")[0]
        site.ObjectPlacement.RelativePlacement.Location.Coordinates = (1000.0, 2000.0, 0.0)
        r = write_import(b, f)["result"]
        out["placement_changed"] = {"elements": r["elements"], "skeleton": r["skeleton"],
                                    "warns_skeleton": any("뼈대" in w for w in r["warnings"])}
    finally:
        b.close()
    # (c) 이름이 다르면 뼈대가 아니다(기존 동작 유지)
    b, f = prepare(work / "c")
    try:
        f.by_type("IfcSite")[0].Name = "Seoul"
        r = write_import(b, f)["result"]
        out["renamed"] = {"skeleton": r["skeleton"]}
    finally:
        b.close()
    return out


# ---------------------------------------------------------------- R4: 개구부 GlobalId
def r4_opening_identity(work: Path) -> dict:
    b, f = prepare(work)
    try:
        before = sorted(o.GlobalId for o in f.by_type("IfcOpeningElement"))
        for op in f.by_type("IfcOpeningElement"):
            op.GlobalId = ifcopenshell.guid.new()
        changed = sorted(o.GlobalId for o in f.by_type("IfcOpeningElement"))
        r = write_import(b, f)["result"]
        out_ = b.export("o.ifc", P)
        g = ifcopenshell.open(str(out_))
        res = {"elements": r["elements"], "opening_gids_kept": sorted(o.GlobalId for o in g.by_type("IfcOpeningElement")) == changed,
               "opening_gids_differ_from_original": changed != before, "diff_vs_import": S.ifdiff(b.ws / "t.ifc", out_)}
        # 문을 고쳐도 외부가 붙인 개구부 GlobalId 가 유지된다
        assert b.run("door.set", {"id": F.D1, "width": 1000}, project=P)["ok"]
        g2 = ifcopenshell.open(str(b.export("o2.ifc", P)))
        res["opening_gids_kept_after_door_edit"] = sorted(o.GlobalId for o in g2.by_type("IfcOpeningElement")) == changed
        # 문을 지웠다 되돌려도(개구부를 다시 만든다) 같은 GlobalId
        assert b.run("element.delete", {"id": F.D1}, project=P)["ok"] and b.run("history.undo", {}, project=P)["ok"]
        g3 = ifcopenshell.open(str(b.export("o3.ifc", P)))
        res["opening_gids_kept_after_delete_undo"] = sorted(o.GlobalId for o in g3.by_type("IfcOpeningElement")) == changed
        return res
    finally:
        b.close()


# ---------------------------------------------------------------- R5: 숫자 범위
def r5_numeric_range(work: Path) -> dict:
    out = {}
    for label, val in (("axis_1e200", 1e200), ("axis_2e9", 2e9)):
        b, f = prepare(work / label)
        try:
            w = f.by_guid(F.W3)
            ps = f.by_id(ue.get_pset(w, "DoBIM_Intent")["id"])
            d = json.loads(ps.HasProperties[0].NominalValue.wrappedValue)
            d["params"]["axis"] = [0.0, 0.0, val, 0.0]
            ps.HasProperties[0].NominalValue = f.createIfcText(json.dumps(d))
            r = write_import(b, f)
            out[label] = {"ok": r["ok"], "code": None if r["ok"] else r["error"]["code"],
                          "elements": r["result"]["elements"] if r["ok"] else None,
                          "warned": r["ok"] and any("해석할 수 없어" in w for w in r["result"]["warnings"])}
        finally:
            b.close()
    return out


# ---------------------------------------------------------------- R6: 미탐 셋 (단위·벽의 외래 개구부·레벨 고도)
def r6_misses(work: Path) -> dict:
    out = {}
    b, f = prepare(work / "unit")
    try:
        for u in f.by_type("IfcSIUnit"):
            if u.UnitType == "LENGTHUNIT":
                u.Prefix = None
        r = write_import(b, f)["result"]
        out["unit_changed"] = {"elements": r["elements"], "all_reasons": sorted({x for d in r["detached"] for x in d["reasons"]}),
                               "warns_unit": any("단위" in w for w in r["warnings"]), "detached_total": r["detached_total"]}
        out["unit_changed"]["wall_move_frozen"] = b.run("wall.move", {"id": F.W1, "dx": 1}, project=P)["error"]["code"]
    finally:
        b.close()
    b, f = prepare(work / "void")
    try:
        w = f.by_guid(F.W3)
        op = api_root.create_entity(f, ifc_class="IfcOpeningElement")
        api_feature.add_feature(f, feature=op, element=w)
        r = write_import(b, f)["result"]
        out["foreign_void_on_wall"] = {"elements": r["elements"], "detached": [d["id"] == F.W3 and d["reasons"] for d in r["detached"]],
                                       "wall_origin": b.run("element.get", {"id": F.W3}, project=P)["result"]["origin"]}
    finally:
        b.close()
    b, f = prepare(work / "level")
    try:
        lv = f.by_guid(F.LV1)
        lv.ObjectPlacement.RelativePlacement.Location.Coordinates = (0.0, 0.0, 500.0)
        lv.Elevation = 500.0
        r = write_import(b, f)["result"]
        out["level_elevation"] = {"elements": r["elements"], "detached_is_level": [d["id"] for d in r["detached"]] == [F.LV1],
                                  "warns_above": any("레벨" in w and "저작 요소" in w for w in r["warnings"])}
    finally:
        b.close()
    return out


# ---------------------------------------------------------------- R8: 변조 시험 보강
def r8_more_tampers(work: Path) -> dict:
    out = {}
    # 문 배치 변경 → 그 문만 분리
    b, f = prepare(work / "door")
    try:
        d = f.by_guid(F.D1)
        loc = d.ObjectPlacement.RelativePlacement.Location
        loc.Coordinates = (loc.Coordinates[0] + 200.0, loc.Coordinates[1], loc.Coordinates[2])
        r = write_import(b, f)["result"]
        out["door_move"] = {"elements": r["elements"], "detached": sorted(x["id"] for x in r["detached"]) == [F.D1],
                            "reasons": [x["reasons"] for x in r["detached"]]}
        out["door_move"]["frozen"] = b.run("door.set", {"id": F.D1, "width": 800}, project=P)["error"]["code"]
        # 분리된 문의 개구부는 외래 요소로 남는다(속성 편집 가능)
        out["door_move"]["opening_foreign"] = r["elements"]["foreign"] == 1
    finally:
        b.close()
    # 스타일 추가는 변조가 아니다(형상 클로저에 없다)
    b, f = prepare(work / "style")
    try:
        add_red_style(f, body_solid(f, F.W1))
        r = write_import(b, f)["result"]
        out["style_added"] = {"elements": r["elements"], "detached_total": r["detached_total"]}
    finally:
        b.close()
    # 사이트 변조(배치)는 R3 에서. 레벨 변조는 R6 에서.
    return out


# ---------------------------------------------------------------- R9: 벽을 뚫는 외래 개구부와 지우기 경고
def r9_foreign_void_delete_warning(work: Path) -> dict:
    """외래 개구부가 있는 벽은 가져올 때 분리되므로(R6) 지울 수 없다. 방어적 경고는 가져온 뒤 외래 개구부가 생긴 경우(엔진 안에서 직접
    붙여 흉내)에 나온다."""
    b, f = prepare(work)
    try:
        write_import(b, f)
        eng = b.env.engine(P)
        eng.flush_ifc()
        w = eng.ifc.ents[F.W3]
        op = api_root.create_entity(eng.ifc.f, ifc_class="IfcOpeningElement")
        api_feature.add_feature(eng.ifc.f, feature=op, element=w)
        d = b.run("element.delete", {"id": F.W3}, project=P)
        return {"delete_ok": d["ok"], "warned": any("외래 개구부" in w for w in d["warnings"])}
    finally:
        b.close()
