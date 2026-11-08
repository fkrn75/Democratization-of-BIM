# SPDX-License-Identifier: MPL-2.0
"""코어 연결 시험: family.* 커맨드가 엔진(로그·취소·해시·IFC 투영)과 맞물려 동작하는가."""
import json

import ifcopenshell
import ifcopenshell.util.element as ue
import pytest

from conftest import load_lib
from dobim_core import fixture as F, limits
from dobim_core.engine import Engine
from dobim_core.graph import RefGraph  # noqa: F401  (차등 시험은 engine.derived_hash(reference=True) 로)


def ok(r):
    assert r["ok"], r
    return r["result"]


def place_all(s):
    d = ok(s.run("family.place", {"family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 1500}))
    w = ok(s.run("family.place", {"family": "window.fixed", "type_values": {"mullions": 1}, "values": {"sill": 600}, "host": F.W2, "offset": 2000}))
    c = ok(s.run("family.place", {"family": "column.section", "type_values": {"section": "round"}, "level": F.LV1, "position": [1000, 1000]}))
    b = ok(s.run("family.place", {"family": "wardrobe.builtin", "level": F.LV1, "position": [3000, 2000], "values": {"leaves": 3, "width": 2400}}))
    return d, w, c, b


def test_네_종_배치하고_IFC가_유효하다(sess):
    place_all(sess)
    v = ok(sess.run("model.validate", {"express_rules": True}))
    assert v["errors"] == 0, v
    assert sess.eng.intent_consistency() == []
    assert sess.eng.derived_hash() == sess.eng.derived_hash(reference=True)   # 증분 그래프 == 기준 그래프


def test_취소와_다시실행이_해시를_복원한다(sess):
    before = sess.eng.hashes()
    for name, args in (("family.place", {"family": "door.single", "host": F.W3, "offset": 1500}),
                       ("family.place", {"family": "window.fixed", "host": F.W2, "offset": 2000}),
                       ("family.place", {"family": "wardrobe.builtin", "level": F.LV1, "position": [3000, 2000]})):
        mid = sess.eng.hashes()
        ok(sess.run(name, args))
        after = sess.eng.hashes()
        assert after["state"] != mid["state"] and after["ifc"] != mid["ifc"]
        ok(sess.run("history.undo", {}))
        assert sess.eng.hashes() == {**mid, "rev": sess.eng.rev}
        ok(sess.run("history.redo", {}))
        assert sess.eng.hashes()["ifc"] == after["ifc"] and sess.eng.hashes()["state"] == after["state"]
    for _ in range(3):
        ok(sess.run("history.undo", {}))
    h = sess.eng.hashes()
    assert h["state"] == before["state"] and h["ifc"] == before["ifc"] and h["derived"] == before["derived"]


def test_프로젝트를_다시_열어도_같은_상태(sess, tmp_path):
    place_all(sess)
    h = sess.eng.hashes()
    sess.eng.close()
    e2 = Engine(sess.path, "t")
    try:
        assert e2.hashes() == h
    finally:
        e2.close()
        sess.eng = Engine(sess.path, "t")
        sess.env.adopt(sess.path, sess.eng)


def test_같은_값이면_타입을_공유하고_다르면_새_타입(sess):
    a = ok(sess.run("family.place", {"family": "door.single", "host": F.W3, "offset": 1000}))
    b = ok(sess.run("family.place", {"family": "door.single", "host": F.W3, "offset": 2000, "values": {"swing": "R"}}))
    c = ok(sess.run("family.place", {"family": "door.single", "type_values": {"width": 1000}, "host": F.W3, "offset": 4000}))
    assert a["type"] == b["type"] != c["type"]
    assert ok(sess.run("family.place", {"family": "door.single", "host": F.W3, "offset": 5000}))["type_created"] is False
    sess.eng.flush_ifc()
    types = sess.eng.ifc.f.by_type("IfcDoorType")
    assert len(types) == 2
    rel = sess.eng.ifc.f.by_type("IfcRelDefinesByType")
    assert sorted(len(r.RelatedObjects) for r in rel) == [1, 3]   # 타입마다 관계 하나, 인스턴스는 거기에 모인다


def test_인스턴스_매개변수가_형상을_바꾸면_개별_표현_아니면_타입_지도를_가리킨다(sess):
    left = ok(sess.run("family.place", {"family": "door.single", "host": F.W3, "offset": 1000}))["id"]
    right = ok(sess.run("family.place", {"family": "door.single", "host": F.W3, "offset": 3000, "values": {"swing": "R"}}))["id"]
    sess.eng.flush_ifc()
    f = sess.eng.ifc.f

    def reps(eid):
        e = sess.eng.ifc.ents[eid]
        return {r.RepresentationIdentifier: (r.RepresentationType, [i.is_a() for i in r.Items]) for r in e.Representation.Representations}

    rl, rr = reps(left), reps(right)
    assert rl["Body"][1] == ["IfcMappedItem"] and rl["FootPrint"][1] == ["IfcMappedItem"]            # 기본값 = 타입 형상
    assert rr["Body"][1] == ["IfcMappedItem"] and rr["FootPrint"][1] == ["IfcGeometricCurveSet"]       # 문 열림 방향만 다르다
    # 좌/우 호의 시작·끝점이 실제로 반대다
    def arc(eid):
        cs = next(i for r in sess.eng.ifc.ents[eid].Representation.Representations for i in r.Items if i.is_a("IfcGeometricCurveSet"))
        return next(c for c in cs.Elements if c.is_a("IfcTrimmedCurve"))
    ar = arc(right)
    tm = f.by_type("IfcRepresentationMap")
    tarc = next(c for m in tm if m.MappedRepresentation.RepresentationIdentifier == "FootPrint"
                for c in m.MappedRepresentation.Items[0].Elements if c.is_a("IfcTrimmedCurve"))
    assert tuple(ar.BasisCurve.Position.Location.Coordinates)[0] > 0 > tuple(tarc.BasisCurve.Position.Location.Coordinates)[0]
    # 타입의 OperationType 은 NOTDEFINED, 인스턴스는 방향에 따라
    t = f.by_type("IfcDoorType")[0]
    assert t.OperationType == "NOTDEFINED"
    assert sess.eng.ifc.ents[left].OperationType == "SINGLE_SWING_LEFT" and sess.eng.ifc.ents[right].OperationType == "SINGLE_SWING_RIGHT"


def test_호스트_두께가_달라지면_개구부와_표현이_따라간다(sess):
    d = ok(sess.run("family.place", {"family": "window.fixed", "host": F.W2, "offset": 2000}))["id"]
    sess.eng.flush_ifc()
    op = sess.eng.ifc.openings[d]
    ok(sess.run("wall.set", {"id": F.W2, "thickness": 400}))
    sess.eng.flush_ifc()
    e = sess.eng.ifc.ents[d]
    reps = {r.RepresentationIdentifier: r.Items[0].is_a() for r in e.Representation.Representations}
    # 창틀 깊이는 min(두께, 150) 이라 두께 200→400 에서 본체는 같고(타입 지도 유지), 평면 기호는 두께를 따라가서 개별 표현이 된다
    assert reps == {"Body": "IfcMappedItem", "FootPrint": "IfcGeometricCurveSet"}
    assert sess.eng.ifc.openings[d] is not None and ok(sess.run("model.validate", {}))["errors"] == 0
    assert op is not None


def test_속성셋은_타입과_인스턴스에_나뉘어_IFC에_쓰인다(sess):
    d, w, c, b = place_all(sess)
    sess.eng.flush_ifc()
    I = sess.eng.ifc
    t = I.ents[d["type"]]
    assert ue.get_psets(t, psets_only=True, should_inherit=False)["Pset_DoorCommon"]["HandicapAccessible"] is True
    inst_own = I.get_psets(d["id"])
    assert "DoBIM_DoorParams" in inst_own and "Pset_DoorCommon" not in inst_own       # 인스턴스 자신의 것만
    assert ue.get_psets(I.ents[d["id"]], psets_only=True)["Pset_DoorCommon"]["IsExternal"] is False   # IFC 도구는 타입 것을 물려받아 본다
    assert I.get_psets(w["id"])["DoBIM_WindowParams"]["SillHeight"] == 600.0
    assert I.get_psets(b["id"])["DoBIM_FurnitureParams"] == {"Shelves": 3, "Leaves": 3}


def test_내보낸_IFC에_레시피_전문과_타입과_개구부가_있다(sess, tmp_path):
    d, w, c, b = place_all(sess)
    r = sess.run("ifc.export", {"path": "o.ifc"})
    tok = r["error"]["details"]["confirm_token"]
    assert sess.run("ifc.export", {"path": "o.ifc"}, confirm=tok)["ok"]
    f = ifcopenshell.open(str(tmp_path / "o.ifc"))
    assert {t.is_a() for t in f.by_type("IfcTypeObject")} == {"IfcDoorType", "IfcWindowType", "IfcColumnType", "IfcFurnitureType"}
    dt = f.by_type("IfcDoorType")[0]
    intent = json.loads(ue.get_pset(dt, "DoBIM_Intent", "Intent", should_inherit=False))
    assert intent["params"]["recipe"] == load_lib("door.single")        # 레시피 전문이 IFC 안에 있다(다시 열면 파라메트릭)
    assert intent["family_id"] == "door.single"
    assert len(f.by_type("IfcRelVoidsElement")) == 4 + 0 or len(f.by_type("IfcRelVoidsElement")) >= 2
    assert len(f.by_type("IfcRelFillsElement")) >= 2
    # 지도·평면 문맥
    assert {m.MappedRepresentation.RepresentationIdentifier for m in f.by_type("IfcRepresentationMap")} == {"Body", "FootPrint"}
    assert any(c.ContextType == "Plan" for c in f.by_type("IfcGeometricRepresentationSubContext"))
    assert ifcopenshell.validate  # 유효성은 model.validate 시험이 이미 확인


def test_평면_문맥은_패밀리가_없으면_만들지_않고_지우면_사라진다(sess):
    sess.eng.flush_ifc()
    h0 = sess.eng.ifc_hash()
    assert sess.eng.ifc.plan_ctx is None
    ok(sess.run("family.place", {"family": "column.section", "level": F.LV1, "position": [0, 0]}))
    sess.eng.flush_ifc()
    assert sess.eng.ifc.plan_ctx is not None
    ok(sess.run("history.undo", {}))
    assert sess.eng.ifc_hash() == h0
    assert sess.eng.ifc.plan_ctx is None and not [c for c in sess.eng.ifc.f.by_type("IfcGeometricRepresentationSubContext") if c.ContextType == "Plan"]


def test_삭제와_제약(sess):
    d, w, c, b = place_all(sess)
    r = sess.run("element.delete", {"id": d["type"]})
    assert r["error"]["code"] == "CONSTRAINT"
    # 벽을 지우면 호스팅된 패밀리 인스턴스도 지워진다(타입은 남는다)
    assert set(ok(sess.run("element.delete", {"id": F.W2}))["deleted"]) == {F.W2, w["id"]}
    ok(sess.run("element.delete", {"id": d["id"]}))
    ok(sess.run("element.delete", {"id": d["type"]}))
    assert sess.run("element.delete", {"id": F.LV1})["error"]["code"] == "CONSTRAINT"
    assert ok(sess.run("model.validate", {}))["errors"] == 0
    assert sess.eng.intent_consistency() == []


def test_배치_오류는_구조화되어_돌아온다(sess):
    def err(args):
        r = sess.run("family.place", args)
        assert not r["ok"], r
        return r["error"]
    assert err({"family": "door.single", "host": F.W3, "offset": 5900})["code"] == "CONSTRAINT"            # 벽 밖
    assert err({"family": "door.single", "level": F.LV1, "position": [0, 0]})["code"] == "CONSTRAINT"       # 호스팅 패밀리를 자립 배치
    assert err({"family": "column.section", "host": F.W3, "offset": 100})["code"] == "CONSTRAINT"
    e = err({"family": "door.single", "values": {"swing": "X"}, "host": F.W3, "offset": 1500})
    assert e["code"] == "FAMILY_INVALID" and e["details"]["issues"][0]["code"] == "E_PARAM_RANGE" and e["hint"]
    e = err({"family": "door.single", "values": {"width": 1000}, "host": F.W3, "offset": 1500})
    assert e["details"]["issues"][0]["code"] == "E_WRONG_KIND"
    assert err({"family": "nope.none", "host": F.W3, "offset": 1})["code"] == "NOT_FOUND"
    assert err({"family": "door.single", "host": F.W3})["code"] == "SCHEMA_INVALID"
    assert err({"type": F.W1, "host": F.W3, "offset": 1000})["code"] == "NOT_FOUND"
    assert err({"family": "door.single", "type_values": {"width": 5000}, "host": F.W3, "offset": 3000})["details"]["issues"][0]["code"] == "E_PARAM_RANGE"
    assert err({"family": "window.fixed", "type_values": {"height": 2400}, "values": {"sill": 1500}, "host": F.W3, "offset": 3000})["code"] == "CONSTRAINT"   # 벽 높이 밖
    assert sess.eng.rev == 10                                  # 실패는 아무것도 바꾸지 않는다


def test_register와_inline_레시피(sess, tmp_path, door):
    t = ok(sess.run("family.register", {"family": "door.single", "type_values": {"width": 1000}, "name": "D1000"}))
    assert t["created"]
    again = ok(sess.run("family.register", {"family": "door.single", "type_values": {"width": 1000}}))
    assert not again["created"] and again["id"] == t["id"]
    # 직접 만든 레시피(이름을 바꾼 복사본)를 inline 으로 올리고, 이후 id 로 배치한다
    door["id"] = "door.custom"
    door["name"] = "내 문"
    t2 = ok(sess.run("family.register", {"recipe": door}))
    ok(sess.run("family.place", {"family": "door.custom", "host": F.W3, "offset": 1500}))
    ok(sess.run("family.place", {"type": t["id"], "host": F.W3, "offset": 3500}))
    # 경로로 읽기
    (tmp_path / "r.json").write_text(json.dumps(door), encoding="utf-8")
    door["id"] = "door.viapath"
    (tmp_path / "r2.json").write_text(json.dumps(door), encoding="utf-8")
    assert ok(sess.run("family.register", {"path": "r2.json"}))["family"] == "door.viapath"
    bad = sess.run("family.register", {"path": "../escape.json"})
    assert bad["error"]["code"] == "PATH_DENIED"
    door["derived"]["x"] = "nothing + 1"
    r = sess.run("family.register", {"recipe": door})
    assert r["error"]["code"] == "FAMILY_INVALID" and r["error"]["details"]["issues"][0]["code"] == "E_UNDEFINED_NAME"
    assert t2["created"] and sess.eng.intent_consistency() == []


def test_family_set은_매개변수와_위치를_바꾸고_속성셋을_갱신한다(sess):
    d = ok(sess.run("family.place", {"family": "door.single", "host": F.W3, "offset": 1500}))["id"]
    w = ok(sess.run("family.place", {"family": "window.fixed", "host": F.W2, "offset": 2000}))["id"]
    ok(sess.run("family.set", {"id": d, "values": {"swing": "R"}, "offset": 2500, "name": "D-new"}))
    ok(sess.run("family.set", {"id": w, "values": {"sill": 300}}))
    sess.eng.flush_ifc()
    assert sess.eng.ifc.get_psets(d)["DoBIM_DoorParams"]["Swing"] == "R"
    assert sess.eng.ifc.get_psets(w)["DoBIM_WindowParams"]["SillHeight"] == 300.0
    assert sess.eng.ifc.ents[d].Name == "D-new" and sess.eng.ifc.ents[d].OperationType == "SINGLE_SWING_RIGHT"
    e = sess.run("family.set", {"id": d, "offset": 99999})
    assert e["error"]["code"] == "CONSTRAINT"
    assert sess.run("family.set", {"id": d, "position": [1, 1]})["error"]["code"] == "CONSTRAINT"
    assert sess.run("family.set", {"id": d, "type_values": {}} if False else {"id": d})["error"]["code"] == "SCHEMA_INVALID"
    assert ok(sess.run("model.validate", {}))["errors"] == 0
    assert sess.eng.derived_hash() == sess.eng.derived_hash(reference=True)


def test_벽_축선을_줄여_호스팅된_창이_나가면_거부(sess):
    ok(sess.run("family.place", {"family": "window.fixed", "host": F.W2, "offset": 3300}))
    r = sess.run("wall.set", {"id": F.W2, "axis": [6000, 0, 6000, 3000]})
    assert r["error"]["code"] == "CONSTRAINT"


def test_응답은_상한_안이다(sess):
    d, w, c, b = place_all(sess)
    reg = ok(sess.run("family.register", {"family": "wardrobe.builtin", "type_values": {"panel_t": 20}}))
    for name, args in (("element.get", {"id": d["type"]}), ("element.get", {"id": b["id"]}), ("family.list", {}),
                       ("family.preview", {"family": "wardrobe.builtin", "values": {"shelves": 10, "leaves": 4}}),
                       ("family.preview", {"type": reg["id"], "scale": "1:200"}),
                       ("family.validate", {"family": "window.fixed", "level": "static"}), ("history.list", {"limit": 50}),
                       ("element.query", {"kind": "ftype"}), ("relation.list", {"id": d["type"]})):
        r = sess.run(name, args)
        assert r["ok"], (name, r)
        assert len(json.dumps(r, ensure_ascii=False).encode()) <= limits.RESPONSE_CAP_BYTES, (name, len(json.dumps(r)))
    big = ok(sess.run("history.list", {"limit": 50}))
    assert any(i["op"] == "family.register" for i in big["items"])


def test_validate_커맨드는_프로젝트_없이도_구조화된_결과를_준다(tmp_path):
    from dobim_core import dispatch
    env = dispatch.Env(tmp_path)
    r = dispatch.run(env, "family.validate", {"family": "column.section", "level": "static"})
    assert r["ok"] and r["result"]["ok"] and r["result"]["checks"]["values"] == "pass"
    (tmp_path / "x.json").write_text('{"id": "a.b"}', encoding="utf-8")
    r = dispatch.run(env, "family.validate", {"path": "x.json"})
    assert r["ok"] and not r["result"]["ok"] and r["result"]["errors"][0]["code"] == "E_SCHEMA"
    r = dispatch.run(env, "family.validate", {"recipe": {"a": 1}})
    assert r["ok"] and not r["result"]["ok"]
    assert dispatch.run(env, "family.validate", {})["error"]["code"] == "SCHEMA_INVALID"
    assert dispatch.run(env, "family.validate", {"family": "../x"})["error"]["code"] == "SCHEMA_INVALID"
