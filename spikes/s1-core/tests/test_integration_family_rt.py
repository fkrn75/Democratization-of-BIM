# SPDX-License-Identifier: MPL-2.0
"""패밀리 의도 왕복 시험 (ADR-0014): 패밀리 인스턴스가 있는 저작 프로젝트를 IFC 로 내보냈다 다시 가져오면 전부 authored 이고 해시 3종이 같다.

배경: 통합 보고서 8절 항목 7. 예전에는 패밀리 인스턴스·타입의 DoBIM_Intent 가 해석되지 않아 인스턴스와 개구부가 외래가 되고
호스트 벽이 '벽에 뚫린 개구부'로 분리(동결)됐다. 지금은 가져오기가 타입(ftype)과 인스턴스(finst)의 의도를 복원하고, 레시피를 S4 의 규칙으로
평가해 일관될 때만 후보로 삼은 뒤 의도를 다시 투영해 들어온 형상과 요소 클로저 해시로 대조한다.

 1. 수정 없는 왕복: 전부 authored, 해시 3종 같음, 두 번 내보낸 IFC 차이 0, 다시 열어도 같음
 2. 왕복 뒤 편집: 같은 커맨드열을 저작 프로젝트와 가져온 프로젝트에 적용하면 해시 3종이 같다(편집·되돌리기·다시하기·타입 삭제 후 재오픈 포함)
 3. 변조: 외부에서 인스턴스·타입·개구부를 바꾸면 그 요소(와 영향받는 호스트)만 detached/foreign 이 된다
 4. 손상·거대 의도: 읽을 수 없으면 오류 없이 외래로 떨어진다
 5. 옛 프로젝트 호환: 이 변경 전에 가져온 프로젝트(인스턴스가 외래)가 그대로 열린다
"""
import json
import sys
from pathlib import Path

import pytest

import ifcopenshell
import ifcopenshell.api.feature as api_feature
import ifcopenshell.api.pset as api_pset
import ifcopenshell.api.root as api_root
import ifcopenshell.util.element as ue

from test_integration_g1 import F, S, Engine, _ok

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "s2-roundtrip" / "tools"))
import ifcdiff_check  # noqa: E402

D = "0" * 21 + "1"      # 문 패밀리 인스턴스(호스트 W3)
WND = "0" * 21 + "2"    # 고정창 패밀리 인스턴스(호스트 W2)
COL = "0" * 21 + "3"    # 기둥 패밀리 인스턴스(자립형, 레벨 LV1)


def _build(b: S.Bench, p: str = "src.dobim") -> None:
    S.build_authored(b, p)
    _ok(b.run("family.place", {"id": D, "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 4500}, project=p))
    _ok(b.run("family.place", {"id": WND, "family": "window.fixed", "values": {}, "host": F.W2, "offset": 2000}, project=p))
    _ok(b.run("family.place", {"id": COL, "family": "column.section", "values": {}, "level": F.LV1, "position": [1000, 1000]}, project=p))


def _type_of(f, gid):
    e = f.by_guid(gid)
    return [r.RelatingType for r in f.get_inverse(e) if r.is_a("IfcRelDefinesByType")][0]


def _opening_of(f, gid):
    e = f.by_guid(gid)
    return [r.RelatingOpeningElement for r in f.get_inverse(e) if r.is_a("IfcRelFillsElement")][0]


def _set_intent(f, e, text):
    ps = f.by_id(ue.get_pset(e, "DoBIM_Intent", should_inherit=False)["id"])
    api_pset.edit_pset(f, pset=ps, properties={"Intent": text})


def _intent(f, e) -> dict:
    return json.loads(ue.get_pset(e, "DoBIM_Intent", should_inherit=False)["Intent"])


def _mv(e, dx):
    loc = e.ObjectPlacement.RelativePlacement.Location
    loc.Coordinates = (loc.Coordinates[0] + dx, loc.Coordinates[1], loc.Coordinates[2])


def _origins(b: S.Bench, p: str) -> dict:
    """origin 이 authored 가 아닌 요소: {id: origin}."""
    out = {}
    for origin in ("detached", "foreign"):
        for i in b.ids_of(origin, p):
            out[i] = origin
    return out


# ================================================================ 1. 수정 없는 왕복
def test_f1_패밀리_저작_프로젝트의_왕복은_전부_authored이고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        h0 = S.trip(b.hashes("src.dobim"))
        e1 = b.export("e1.ifc", "src.dobim")
        r = b.import_ifc("e1.ifc", "imp.dobim")
        assert r["ok"], r
        res = r["result"]
        assert res["elements"] == {"authored": 13, "foreign": 0, "detached": 0}, res["elements"]   # 레벨 2 + 벽 3 + 문 2 + 타입 3 + 인스턴스 3
        assert res["detached"] == [] and res["warnings"] == []
        kinds = {i["kind"] for i in _ok(b.run("element.query", {"limit": 100}, project="imp.dobim"))["items"]}
        assert {"ftype", "finst"} <= kinds
        assert S.trip(b.hashes("imp.dobim")) == h0                        # state·derived·ifc 모두 같다
        # 두 번 내보낸 IFC 가 같다(수정 없는 가져오기 → 내보내기)
        e2 = b.export("e2.ifc", "imp.dobim")
        assert e1.read_bytes() == e2.read_bytes()
        assert ifcdiff_check.strict_diff(str(e1), str(e2))["total"] == 0
        if S.have_ifcdiff():
            d = S.ifdiff(e1, e2)
            assert d["ifcdiff_total"] == 0 and d["strict_total"] == 0, d
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
        assert b.env.engine("imp.dobim").intent_consistency() == []
    finally:
        b.close()
    # 다시 열어도(새 엔진) 같다
    eng = Engine(tmp_path / "imp.dobim", "t", create=False)
    try:
        h = eng.hashes()
        assert (h["state"], h["derived"], h["ifc"]) == h0
    finally:
        eng.close()


def test_f1b_자립형_인스턴스와_호스팅_인스턴스의_관계가_복원된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        b.export("e1.ifc", "src.dobim")
        _ok(b.import_ifc("e1.ifc", "imp.dobim"))
        g = lambda i: _ok(b.run("element.get", {"id": i}, project="imp.dobim"))   # noqa: E731
        d, w, c = g(D), g(WND), g(COL)
        assert d["origin"] == w["origin"] == c["origin"] == "authored"
        rel = lambda r, k: sorted(x for x in r["relations"].get(k, []))             # noqa: E731
        assert F.W3 in str(d["relations"]) and F.W2 in str(w["relations"]) and F.LV1 in str(c["relations"])
        assert d["family_id"] == "door.single" and w["family_id"] == "window.fixed" and c["family_id"] == "column.section"
        assert rel(d, "of_type") and rel(w, "of_type") and rel(c, "of_type")
    finally:
        b.close()


# ================================================================ 2. 왕복 뒤 편집은 저작 프로젝트와 같은 결과
def test_f2_왕복_뒤_같은_편집은_저작_프로젝트와_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        b.export("e1.ifc", "src.dobim")
        guid = b.env.engine("src.dobim").project_guid_uuid
        _ok(b.import_ifc("e1.ifc", "imp.dobim", guid=guid))   # 같은 프로젝트 UUID: 결정적으로 파생되는 id·GlobalId 가 같다
    finally:
        b.close()
    import shutil
    shutil.copyfile(tmp_path / "src.dobim", tmp_path / "ref.dobim")
    b = S.Bench(tmp_path)
    try:
        def both(name, args):
            ra, rb = b.run(name, args, project="ref.dobim"), b.run(name, args, project="imp.dobim")
            assert ra["ok"] and rb["ok"], (name, ra.get("error"), rb.get("error"))
            assert S.trip(b.hashes("ref.dobim")) == S.trip(b.hashes("imp.dobim")), name

        both("family.set", {"id": D, "values": {"swing": "L"}, "offset": 4000})   # 호스팅 인스턴스의 개구부·표현이 다시 투영된다
        both("family.set", {"id": D, "name": "현관문"})
        both("family.set", {"id": COL, "position": [2000, 1500], "rotation": 30})
        both("family.place", {"id": "0" * 21 + "4", "family": "door.single", "values": {}, "host": F.W1, "offset": 2500})   # 기존 타입을 재사용
        both("family.place", {"id": "0" * 21 + "5", "family": "column.section", "values": {}, "level": F.LV1, "position": [3000, 1000]})   # 평면 기호 문맥을 이어받는다
        both("family.register", {"family": "door.single", "type_values": {"width": 1200}, "name": "D1200"})
        both("pset.set", {"id": D, "pset": "Pset_X", "prop": "Note", "value": "a"})
        both("wall.set", {"id": F.W3, "thickness": 250})                          # 호스트 벽 치수: 인스턴스의 개구부 깊이가 따라간다
        both("wall.move", {"id": F.W3, "dx": 100})
        both("wall.split", {"id": F.W2, "at": 3000, "new_id": "3" + "0" * 21})   # 창이 놓인 벽을 나눈다
        both("element.delete", {"id": COL})
        both("history.undo", {})
        both("history.undo", {})
        both("history.undo", {})
        both("element.delete", {"id": WND})
        both("history.undo", {})
        both("history.redo", {})
        both("history.undo", {})
        for pr in ("ref.dobim", "imp.dobim"):
            assert _ok(b.run("model.validate", {}, project=pr))["errors"] == 0
            assert b.env.engine(pr).intent_consistency() == []
        e_ref, e_imp = b.export("eref.ifc", "ref.dobim"), b.export("eimp.ifc", "imp.dobim")
        assert ifcdiff_check.strict_diff(str(e_ref), str(e_imp))["total"] == 0
    finally:
        b.close()
    ref, imp = (Engine(tmp_path / n, "t", create=False) for n in ("ref.dobim", "imp.dobim"))   # 다시 열어도 같다
    try:
        hr, hi = ref.hashes(), imp.hashes()
        assert (hr["state"], hr["derived"], hr["ifc"]) == (hi["state"], hi["derived"], hi["ifc"])
    finally:
        ref.close()
        imp.close()


def test_f2b_가져온_타입을_지우고_다시_열어도_저작_프로젝트와_같다(tmp_path):
    """시작 상태에 있던 패밀리 타입을 지우면 다시 열 때 작업 사본에서도 없어야 한다(가지치기). 지운 타입을 되돌리면 되살아난다."""
    b = S.Bench(tmp_path)
    try:
        _build(b)
        b.export("e1.ifc", "src.dobim")
        guid = b.env.engine("src.dobim").project_guid_uuid
        _ok(b.import_ifc("e1.ifc", "imp.dobim", guid=guid))
    finally:
        b.close()
    import shutil
    shutil.copyfile(tmp_path / "src.dobim", tmp_path / "ref.dobim")
    b = S.Bench(tmp_path)
    try:
        ctype = _ok(b.run("element.get", {"id": COL}, project="imp.dobim"))["params"]["type"]
        for pr in ("ref.dobim", "imp.dobim"):
            _ok(b.run("element.delete", {"id": COL}, project=pr))
            _ok(b.run("element.delete", {"id": ctype}, project=pr))
        assert S.trip(b.hashes("ref.dobim")) == S.trip(b.hashes("imp.dobim"))
        want = S.trip(b.hashes("imp.dobim"))
    finally:
        b.close()
    eng = Engine(tmp_path / "imp.dobim", "t", create=False)   # 재오픈: 가져온 파일에 있던 타입이 작업 사본에서 지워진다
    try:
        h = eng.hashes()
        assert (h["state"], h["derived"], h["ifc"]) == want
        assert not [t for t in eng.ifc.f.by_type("IfcColumnType")]
    finally:
        eng.close()
    b = S.Bench(tmp_path)
    try:
        for pr in ("ref.dobim", "imp.dobim"):
            _ok(b.run("history.undo", {}, project=pr))
            _ok(b.run("history.undo", {}, project=pr))
        assert S.trip(b.hashes("ref.dobim")) == S.trip(b.hashes("imp.dobim"))
    finally:
        b.close()


def test_f2c_다른_UUID로_가져와도_family_place가_같은_타입을_다시_만들지_않는다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        b.export("e1.ifc", "src.dobim")
        _ok(b.import_ifc("e1.ifc", "imp.dobim"))                       # 무작위 UUID
        types = lambda: sorted(i["id"] for i in _ok(b.run("element.query", {"kind": "ftype", "limit": 50}, project="imp.dobim"))["items"])   # noqa: E731
        before = types()
        r = _ok(b.run("family.place", {"id": "0" * 21 + "4", "family": "door.single", "values": {"swing": "R"}, "host": F.W1, "offset": 2500}, project="imp.dobim"))
        assert r["type_created"] is False and types() == before          # 문 타입이 이미 있다
        r = _ok(b.run("family.register", {"family": "door.single", "type_values": {}}, project="imp.dobim"))
        assert r["created"] is False and types() == before
        r = _ok(b.run("family.register", {"family": "door.single", "type_values": {"width": 1234}}, project="imp.dobim"))   # 값이 다르면 새 타입
        assert r["created"] is True and len(types()) == len(before) + 1
    finally:
        b.close()


def test_f2d_가져온_개구부와_타입_지정_관계의_GlobalId는_편집_뒤에도_유지된다(tmp_path):
    """다른 UUID 로 가져와도 인스턴스를 고쳐 다시 투영할 때 파일에 있던 개구부 GlobalId 를 바꾸지 않는다(외부 도구가 개구부를 추적한다).
    지운 인스턴스를 되돌려도 같다. 타입 지정 관계의 GlobalId 는 타입 id 에서 결정적으로 파생해 다른 세션에서 내보내도 같다."""
    b = S.Bench(tmp_path)
    try:
        _build(b)
        e1 = b.export("e1.ifc", "src.dobim")
        f = ifcopenshell.open(str(e1))
        gid_open = {i: _opening_of(f, i).GlobalId for i in (D, WND)}
        rels = {r.GlobalId for r in f.by_type("IfcRelDefinesByType")}
        _ok(b.import_ifc("e1.ifc", "imp.dobim"))                              # 무작위 UUID
        _ok(b.run("family.set", {"id": D, "values": {"swing": "L"}, "offset": 4000}, project="imp.dobim"))
        _ok(b.run("element.delete", {"id": WND}, project="imp.dobim"))
        _ok(b.run("history.undo", {}, project="imp.dobim"))
        e2 = b.export("e2.ifc", "imp.dobim")
        f2 = ifcopenshell.open(str(e2))
        assert {i: _opening_of(f2, i).GlobalId for i in (D, WND)} == gid_open
        assert {r.GlobalId for r in f2.by_type("IfcRelDefinesByType")} == rels   # 관계도 하나도 늘지 않고 같은 GlobalId
        assert len(f2.by_type("IfcRelDefinesByType")) == len(f.by_type("IfcRelDefinesByType"))
        assert len(f2.by_type("IfcGeometricRepresentationSubContext")) == len(f.by_type("IfcGeometricRepresentationSubContext"))
    finally:
        b.close()
    # 같은 저작 프로젝트를 다른 세션에서 두 번 내보내면 타입 지정 관계의 GlobalId 가 같다(무작위가 아니다)
    b = S.Bench(tmp_path)
    try:
        e3 = b.export("e3.ifc", "src.dobim")
    finally:
        b.close()
    assert {r.GlobalId for r in ifcopenshell.open(str(e3)).by_type("IfcRelDefinesByType")} == rels


# ================================================================ 3. 변조
def _tamper_case(tmp_path, mutate):
    """저작 프로젝트(패밀리 포함)를 내보낸 IFC 를 mutate(f) 로 바꿔 가져온다. 반환: (Bench, 가져오기 결과, {요소: origin}, 파일 안 id 정보)."""
    b = S.Bench(tmp_path)
    _build(b)
    b.export("e1.ifc", "src.dobim")
    f = ifcopenshell.open(str(tmp_path / "e1.ifc"))
    info = {"door_type": _type_of(f, D).GlobalId, "door_opening": _opening_of(f, D).GlobalId, "win_opening": _opening_of(f, WND).GlobalId}
    mutate(f, info)
    f.write(str(tmp_path / "t.ifc"))
    r = b.import_ifc("t.ifc", "imp.dobim")
    assert r["ok"], r
    return b, r["result"], _origins(b, "imp.dobim"), info


def _move_inst(f, info): _mv(f.by_guid(D), 300.0)
def _move_opening(f, info): _mv(_opening_of(f, D), 300.0)
def _opening_shape(f, info):
    solid = _opening_of(f, D).Representation.Representations[0].Items[0]
    solid.Depth = solid.Depth * 2
def _window_move(f, info): _mv(f.by_guid(WND), 100.0)
def _column_move(f, info): _mv(f.by_guid(COL), 100.0)
def _rename_type(f, info): _type_of(f, D).Name = "바뀐 타입"


def _type_geometry(f, info):
    it = _type_of(f, D).RepresentationMaps[0].MappedRepresentation.Items[0]
    while it.is_a("IfcCsgSolid") or it.is_a("IfcBooleanResult"):
        it = it.TreeRootExpression if it.is_a("IfcCsgSolid") else it.FirstOperand
    it.Depth = it.Depth + 50.0


def _inst_intent_offset(f, info):
    d = _intent(f, f.by_guid(D))
    d["params"]["offset"] = 4600.0
    _set_intent(f, f.by_guid(D), json.dumps(d, sort_keys=True))


def _remove_type_rel(f, info):
    for r in [r for r in f.get_inverse(_type_of(f, D)) if r.is_a("IfcRelDefinesByType")]:
        f.remove(r)


def _delete_inst_and_opening(f, info):
    api_feature.remove_feature(f, feature=_opening_of(f, D))
    api_root.remove_product(f, product=f.by_guid(D))


def _delete_inst_keep_opening(f, info): api_root.remove_product(f, product=f.by_guid(D))
def _move_host(f, info): _mv(f.by_guid(F.W3), 200.0)


# (이름, 변조, 분리된 요소, 외래가 된 요소 키): 요소는 id 또는 info 의 키
TAMPER = [
    ("인스턴스_이동", _move_inst, {D}, {"door_opening"}),
    ("개구부_이동", _move_opening, {D}, {"door_opening"}),
    ("개구부_형상", _opening_shape, {D}, {"door_opening"}),
    ("창_이동", _window_move, {WND}, {"win_opening"}),
    ("기둥_이동", _column_move, {COL}, set()),
    ("타입_이름", _rename_type, {"door_type", D}, {"door_opening"}),
    ("타입_형상", _type_geometry, {"door_type", D}, {"door_opening"}),
    ("인스턴스_의도_offset_불일치", _inst_intent_offset, {D}, {"door_opening"}),
    ("타입_지정_관계_제거", _remove_type_rel, {D}, {"door_opening"}),
    ("인스턴스와_개구부_삭제", _delete_inst_and_opening, set(), set()),
    ("인스턴스만_삭제_개구부_남음", _delete_inst_keep_opening, {F.W3}, {"door_opening"}),
    ("호스트_벽_이동", _move_host, {F.W3}, set()),
]


@pytest.mark.parametrize("name,mutate,detached,foreign", TAMPER, ids=[t[0] for t in TAMPER])
def test_f3_변조는_그_요소와_영향받는_호스트만_분리한다(tmp_path, name, mutate, detached, foreign):
    b, res, got, info = _tamper_case(tmp_path, mutate)
    try:
        want = {(info.get(k, k)): "detached" for k in detached} | {info.get(k, k): "foreign" for k in foreign}
        assert got == want, (got, want)
        # 영향받지 않은 요소는 그대로 저작이다: 편집이 된다
        untouched = F.W1 if F.W1 not in got else F.LV1
        assert _ok(b.run("wall.move", {"id": F.W1, "dx": 10}, project="imp.dobim")) if untouched == F.W1 else True
        # 분리된 요소는 형상을 못 고치고, 속성은 고칠 수 있다
        for g, origin in got.items():
            if origin == "detached" and g == D:
                assert b.run("family.set", {"id": D, "offset": 4000}, project="imp.dobim")["error"]["code"] == "ELEMENT_FROZEN"
                assert _ok(b.run("pset.set", {"id": D, "pset": "Pset_S2", "prop": "Note", "value": "x"}, project="imp.dobim"))
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
        h = S.trip(b.hashes("imp.dobim"))
    finally:
        b.close()
    eng = Engine(tmp_path / "imp.dobim", "t", create=False)   # 다시 열어도 같다(재판정 없이)
    try:
        hh = eng.hashes()
        assert (hh["state"], hh["derived"], hh["ifc"]) == h and eng.rejudged == []
    finally:
        eng.close()


def test_f3b_분리된_타입의_인스턴스도_함께_분리된다_이유가_남는다(tmp_path):
    b, res, got, info = _tamper_case(tmp_path, _type_geometry)
    try:
        reasons = {d["id"]: d["reasons"] for d in res["detached"]}
        assert info["door_type"] in reasons and any("속성" in x for x in reasons[info["door_type"]]), reasons
        assert D in reasons                                            # 표현 지도를 매핑한 인스턴스라 형상도 달라 보이거나, 타입 분리로 함께 분리된다
        assert F.W3 not in reasons                                     # 호스트 벽은 영향받지 않는다
        # 창·기둥의 타입은 그대로 저작이다
        assert WND not in got and COL not in got
    finally:
        b.close()


def test_f3c_타입의_표현만_같고_인스턴스가_자기_표현을_가진_경우도_타입_분리를_따른다(tmp_path):
    """타입 이름만 바뀌면(형상은 같다) 타입은 분리되고, 인스턴스는 자기 형상이 같아도 낡은 타입을 가리키므로 함께 분리된다."""
    b, res, got, info = _tamper_case(tmp_path, _rename_type)
    try:
        reasons = {d["id"]: d["reasons"] for d in res["detached"]}
        assert reasons[D] == ["패밀리 타입이 분리됨"], reasons
    finally:
        b.close()


# ================================================================ 4. 손상·거대 의도는 외래로 안전하게 떨어진다
def _damage_garbage(f, info): _set_intent(f, f.by_guid(D), "{not json")
def _damage_garbage_type(f, info): _set_intent(f, _type_of(f, D), "{not json")


def _damage_hash(f, info):
    t = _type_of(f, D)
    d = _intent(f, t)
    d["params"]["recipe_hash"] = "0" * 64
    _set_intent(f, t, json.dumps(d))


def _edit_inst(f, fn):
    e = f.by_guid(D)
    d = _intent(f, e)
    fn(d)
    _set_intent(f, e, json.dumps(d))


def _damage_huge_offset(f, info): _edit_inst(f, lambda d: d["params"].update(offset=1e300))
def _damage_huge_text(f, info): _set_intent(f, f.by_guid(D), "x" * 400_000)
def _damage_huge_value(f, info): _edit_inst(f, lambda d: d["params"]["values"].update(swing="R" * 100_000))
def _damage_bigint(f, info): _set_intent(f, f.by_guid(D), json.dumps(_intent(f, f.by_guid(D))).replace('"offset": 4500.0', '"offset": ' + "9" * 5000))
def _damage_missing_key(f, info): _edit_inst(f, lambda d: d.pop("family_id"))
def _damage_extra_key(f, info): _edit_inst(f, lambda d: d.update(extra=1))
def _damage_unknown_type(f, info): _edit_inst(f, lambda d: (d.update(type="X" * 22), d["params"].update(type="X" * 22)))
def _damage_width_mismatch(f, info): _edit_inst(f, lambda d: d["params"].update(width=d["params"]["width"] + 1.0))
def _damage_value_range(f, info): _edit_inst(f, lambda d: d["params"]["values"].update(leaf_t=1e9))
def _damage_unknown_value(f, info): _edit_inst(f, lambda d: d["params"]["values"].update(nope=1))
def _damage_host_to_level(f, info): _edit_inst(f, lambda d: (d.pop("host"), d.update(level=F.LV1)))
def _damage_nan(f, info): _set_intent(f, f.by_guid(D), json.dumps(_intent(f, f.by_guid(D))).replace('"offset": 4500.0', '"offset": NaN'))


def _damage_recipe_bomb(f, info):
    t = _type_of(f, D)
    _set_intent(f, t, "[" * 200_000)


def _damage_type_wrong_family(f, info):
    t = _type_of(f, D)
    d = _intent(f, t)
    d["family_id"] = "window.fixed"
    _set_intent(f, t, json.dumps(d))


def _damage_type_values(f, info):
    t = _type_of(f, D)
    d = _intent(f, t)
    d["params"]["type_values"] = {}
    _set_intent(f, t, json.dumps(d))


DAMAGE = [_damage_garbage, _damage_hash, _damage_huge_offset, _damage_huge_text, _damage_huge_value, _damage_bigint,
          _damage_missing_key, _damage_extra_key, _damage_unknown_type, _damage_width_mismatch, _damage_value_range,
          _damage_unknown_value, _damage_host_to_level, _damage_nan, _damage_garbage_type, _damage_recipe_bomb,
          _damage_type_wrong_family, _damage_type_values]


@pytest.mark.parametrize("mutate", DAMAGE, ids=[m.__name__[8:] for m in DAMAGE])
def test_f4_읽을_수_없는_의도는_인스턴스를_외래로_두고_호스트_벽만_분리한다(tmp_path, mutate):
    b, res, got, info = _tamper_case(tmp_path, mutate)
    try:
        assert got.get(D) == "foreign" and got.get(info["door_opening"]) == "foreign", got   # 인스턴스와 개구부는 외래
        assert got.get(F.W3) == "detached", got                                               # 개구부가 더해진 호스트 벽은 분리
        assert all(o == "foreign" or i == F.W3 for i, o in got.items())                       # 그 밖의 요소는 영향이 없다
        assert [w for w in res["warnings"] if "해석할 수 없어" in w or "외래로 둔다" in w], res["warnings"]
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
        # 다른 패밀리(창·기둥)는 그대로 저작이고 편집된다
        assert _ok(b.run("family.set", {"id": COL, "position": [1500, 1500]}, project="imp.dobim"))
        assert b.run("family.set", {"id": D, "offset": 4000}, project="imp.dobim")["error"]["code"] == "ELEMENT_FROZEN"
        S.trip(b.hashes("imp.dobim"))
    finally:
        b.close()


def test_f4b_의도가_멀쩡해도_타입의_형상이_다르면_타입만_분리한다(tmp_path):
    """타입 의도의 type_values 만 바꾸면(형상은 그대로) 타입은 분리되고, 인스턴스는 파생값이 평가와 달라 읽을 수 없으니 외래다."""
    b, res, got, info = _tamper_case(tmp_path, lambda f, info: _edit_type_values(f))
    try:
        assert got.get(info["door_type"]) == "detached"
        assert got.get(D) in ("foreign", "detached")
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
    finally:
        b.close()


def _edit_type_values(f):
    t = f.by_guid(next(r.RelatingType.GlobalId for r in f.get_inverse(f.by_guid(D)) if r.is_a("IfcRelDefinesByType")))
    d = _intent(f, t)
    d["params"]["type_values"]["width"] = d["params"]["type_values"]["width"] + 100.0
    _set_intent(f, t, json.dumps(d, sort_keys=True))


# ================================================================ 5. 옛 프로젝트 호환
def test_f5_이_변경_전에_가져온_프로젝트는_그대로_열리고_내보낸_IFC가_원본과_같다(tmp_path, monkeypatch):
    """옛 가져오기는 패밀리 의도를 해석하지 못해 인스턴스·개구부가 외래, 호스트 벽이 분리, 타입은 요소가 아니었다. 그 판정을 재현해 만든
    프로젝트(claimed_kind 를 끈다)를 지금 코드로 열어도 오류 없이 같은 판정으로 열리고, 편집·내보내기가 된다."""
    from dobim_core.family import restore as FR
    b = S.Bench(tmp_path)
    try:
        _build(b)
        e1 = b.export("e1.ifc", "src.dobim")
        monkeypatch.setattr(FR, "claimed_kind", lambda text: None)       # 옛 가져오기: 패밀리 종류를 알아보지 못한다
        r = b.import_ifc("e1.ifc", "old.dobim")
        monkeypatch.undo()
        assert r["ok"] and r["result"]["elements"] == {"authored": 5, "foreign": 5, "detached": 2}, r["result"]["elements"]
        old = S.trip(b.hashes("old.dobim"))
    finally:
        b.close()
    eng = Engine(tmp_path / "old.dobim", "t", create=False)              # 새 코드로 다시 연다
    try:
        h = eng.hashes()
        assert (h["state"], h["derived"], h["ifc"]) == old and eng.rejudged == []
    finally:
        eng.close()
    b = S.Bench(tmp_path)
    try:
        got = _origins(b, "old.dobim")
        assert got[F.W3] == got[F.W2] == "detached" and got[D] == got[WND] == got[COL] == "foreign"   # 창·문이 놓인 벽 둘이 분리
        assert not [i for i in _ok(b.run("element.query", {"kind": "ftype", "limit": 5}, project="old.dobim"))["items"]]
        assert _ok(b.run("wall.move", {"id": F.W1, "dx": 10}, project="old.dobim"))
        _ok(b.run("history.undo", {}, project="old.dobim"))
        assert _ok(b.run("pset.set", {"id": D, "pset": "Pset_S2", "prop": "Note", "value": "x"}, project="old.dobim"))
        _ok(b.run("history.undo", {}, project="old.dobim"))
        e3 = b.export("e3.ifc", "old.dobim")
        assert ifcdiff_check.strict_diff(str(e1), str(e3))["total"] == 0   # 수정 없는 내보내기는 원본과 의미가 같다
    finally:
        b.close()


# ================================================================ 6. 병합
def test_f6_가져온_패밀리_프로젝트의_두_복사본이_병합되고_결과가_순차_적용과_같다(tmp_path):
    """시작 상태 기록(seq 0)에 ftype/finst 행이 들어가므로 병합이 이를 검사(row_error)하고 접어야 한다."""
    import shutil
    b = S.Bench(tmp_path)
    try:
        _build(b)
        b.export("e1.ifc", "src.dobim")
        _ok(b.import_ifc("e1.ifc", "anc.dobim", guid=F.GUID))
    finally:
        b.close()
    for n in ("a.dobim", "b.dobim", "ref.dobim"):
        shutil.copyfile(tmp_path / "anc.dobim", tmp_path / n)
    new = "0" * 21 + "4"
    edit_a = [("family.set", {"id": D, "values": {"swing": "L"}, "offset": 4000})]
    edit_b = [("family.place", {"id": new, "family": "door.single", "values": {}, "host": F.W1, "offset": 2500}),
              ("family.set", {"id": COL, "position": [2000, 1500]})]
    b = S.Bench(tmp_path)
    try:
        for n, a in edit_a:
            _ok(b.run(n, a, project="a.dobim"))
            _ok(b.run(n, a, project="ref.dobim"))
        for n, a in edit_b:
            _ok(b.run(n, a, project="b.dobim"))
            _ok(b.run(n, a, project="ref.dobim"))
        prev = b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim")
        assert prev["ok"] and prev["result"]["conflict_count"] == 0, prev
        r = b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")
        assert r["ok"], r
        assert S.trip(b.hashes("a.dobim")) == S.trip(b.hashes("ref.dobim"))
        assert _ok(b.run("model.validate", {}, project="a.dobim"))["errors"] == 0
        _ok(b.run("history.undo", {}, project="a.dobim"))
        assert new not in {i["id"] for i in _ok(b.run("element.query", {"kind": "finst", "limit": 50}, project="a.dobim"))["items"]}
    finally:
        b.close()
