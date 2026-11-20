# SPDX-License-Identifier: MPL-2.0
"""W1 1-F 5차: 내장 패밀리 30종(FAM-13), 사용자 레시피 폴더 불러오기(FAM-01), 인쇄 여백·축척 맞춤(IO-17).
- 새 7종(문 3·창 3·설비 1)은 레시피 검증(full) 오류·경고 0, 배치·평면 기호·IFC4/IFC2x3 스키마 검증, 기존 23종은 그대로
- family.load_dir: id 마다 version 하나(지정 또는 가장 높은 것), 잘못된 레시피는 파일마다 이유와 함께 거절, 기본 라이브러리와 같은 id 규칙
- sheet.render print: 인쇄 용지·여백·축척 맞춤, 주지 않으면 이전과 같은 파일
합성 데이터만 쓴다."""
import json
import re
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core.engine import Engine
from dobim_core.family import library
from dobim_core.family import spec as FS
from dobim_core.family.validate import validate_recipe

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

NEW7 = ("door.folding", "door.glass", "door.fire", "window.casement", "window.awning", "window.hung", "lighting.ceiling")
OLD23 = ("appliance.fridge", "appliance.washer", "bed.double", "cabinet.base", "chair.basic", "column.section", "desk.basic", "door.double",
         "door.single", "door.sliding", "sanitary.basin", "sanitary.bath", "sanitary.shower", "sanitary.sink", "sanitary.toilet", "shelf.open",
         "sofa.basic", "table.dining", "wardrobe.builtin", "window.fixed", "window.framed", "window.sliding", "window.storefront")
FI = "0" * 21 + "7"


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r, code="CONSTRAINT"):
    assert not r["ok"] and r["error"]["code"] == code, r
    return r["error"]


def _g(s):
    s.eng.sync()
    s.eng._touch_pending()
    return s.eng.g


def _export(s, name, args):
    a = {"path": name} | args
    r = s.run("ifc.export", a)
    r = s.run("ifc.export", a, confirm=r["error"]["details"]["confirm_token"])
    assert r["ok"], r
    return r


def _destructive(s, name, args):
    r = s.run(name, args)
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = s.run(name, args, confirm=r["error"]["details"]["confirm_token"])
    return r


# ================================================================ FAM-13 내장 패밀리 30종
def test_내장_패밀리는_30종이고_새_7종은_검증_오류_경고가_없다():
    ids = library.ids()
    assert len(ids) == 30 and set(ids) == set(OLD23) | set(NEW7)
    for fid in NEW7:
        res = validate_recipe(library.load(fid), "full")
        assert res["ok"] and not res["errors"] and not res["warnings"], (fid, res["errors"][:2], res["warnings"][:2])
    classes = {fid: library.load(fid)["ifc"]["class"] for fid in NEW7}
    assert classes == {"door.folding": "IfcDoor", "door.glass": "IfcDoor", "door.fire": "IfcDoor", "window.casement": "IfcWindow",
                       "window.awning": "IfcWindow", "window.hung": "IfcWindow", "lighting.ceiling": "IfcLightFixture"}


def test_새_패밀리_배치와_평면_기호(base):
    s = base
    ok(s.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    # 접이문은 문짝 장수(2~6)마다 평가된다(반복 압출·지그재그 기호)
    for n in range(2, 7):
        ok(s.run("family.place", {"family": "door.folding", "type_values": {"panels": n, "width": min(1200 + 400 * (n - 2), 2800)}, "host": F.W2, "offset": 2000}))
        ok(s.run("history.undo", {}))
    lines0 = len(_g(s).q(D.view_lines, F.V1))
    placed = {}
    for fid, args in (("door.folding", {"host": F.W1, "offset": 1500}), ("door.glass", {"host": F.W3, "offset": 1500, "values": {"swing": "R"}}),
                      ("door.fire", {"host": F.W1, "offset": 4200}), ("window.casement", {"host": F.W2, "offset": 1000}),
                      ("window.awning", {"host": F.W2, "offset": 2500, "values": {"sill": 1200}}), ("window.hung", {"host": F.W3, "offset": 3500})):
        placed[fid] = (ok(s.run("family.place", {"family": fid} | args))["id"], args["host"])
    light = ok(s.run("family.place", {"family": "lighting.ceiling", "level": F.LV1, "position": [2000, 2000], "elevation": 2400}))["id"]
    g = _g(s)
    for fid, (e, w) in placed.items():   # 문·창 평면 기호(문틀 절단·열림 호·지그재그·창짝 선)는 호스트 벽 선에 들어간다
        plans = [f[6] for f in g.q(D.hosted_fam, w) if f[4] == pytest.approx(s.eng.st.el[e]["params"]["offset"])]
        assert len(plans) == 1 and plans[0], fid
    kinds = {fid: {it[0] for it in [f for f in g.q(D.hosted_fam, w) if f[4] == pytest.approx(s.eng.st.el[e]["params"]["offset"])][0][6]}
             for fid, (e, w) in placed.items()}
    assert "poly" in kinds["door.folding"] and "arc" in kinds["door.glass"] and "arc" in kinds["window.casement"]
    assert len(g.q(D.view_lines, F.V1)) > lines0
    v = ok(s.run("model.validate", {}))
    assert not [m for m in v["violations"] if "IfcGloballyUniqueId" not in m], v
    p = s.eng.st.el[light]["params"]
    assert p["elevation"] == 2400.0


def test_방화문_속성과_IFC_값(base):
    s = base
    r = ok(s.run("family.place", {"family": "door.fire", "type_values": {"fire_rating": "EI90", "vision": True}, "host": F.W1, "offset": 3000}))
    assert s.eng.st.el[r["type"]]["ifc_class"] == "IfcDoorType"
    got = ok(s.run("element.get", {"id": r["type"]}))
    ps = got.get("psets") or got.get("properties")
    assert ps["Pset_DoorCommon"]["FireRating"] == "EI90" and ps["Pset_DoorCommon"]["SelfClosing"] is True and ps["Pset_DoorCommon"]["FireExit"] is True


def test_새_패밀리는_IFC4_IFC2x3_스키마_검증을_통과한다(base):
    import ifcopenshell
    import ifcopenshell.validate
    s = base
    ok(s.run("family.place", {"family": "door.folding", "host": F.W1, "offset": 1500, "values": {"fold": "R"}}))
    ok(s.run("family.place", {"family": "window.casement", "type_values": {"hinge": "R"}, "host": F.W2, "offset": 1000}))
    ok(s.run("family.place", {"family": "window.hung", "host": F.W3, "offset": 3500}))
    ok(s.run("family.place", {"family": "window.awning", "host": F.W2, "offset": 2500}))
    ok(s.run("family.place", {"family": "door.glass", "host": F.W3, "offset": 1500}))
    ok(s.run("family.place", {"family": "lighting.ceiling", "type_values": {"shape": "round"}, "level": F.LV1, "position": [2000, 2000], "elevation": 2400}))
    for name, schema in (("m4.ifc", None), ("m2.ifc", "IFC2X3")):
        _export(s, name, {} if schema is None else {"schema": schema})
        f = ifcopenshell.open(str(s.path.parent / name))
        lg = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(f, lg, express_rules=True)
        bad = [x["message"] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]]
        assert bad == [], (name, bad[:3])
        if schema is None:
            assert "FOLDING_TO_RIGHT" in {d.OperationType for d in f.by_type("IfcDoor")}
            assert len(f.by_type("IfcLightFixture")) == 1 and f.by_type("IfcLightFixtureType")[0].PredefinedType == "POINTSOURCE"
            ops = sorted(p.OperationType for p in f.by_type("IfcWindowPanelProperties"))
            assert {"SIDEHUNGRIGHTHAND", "SLIDINGVERTICAL", "TOPHUNG"} <= set(ops)
        else:
            assert any(e.ObjectType and "IfcLightFixture" in e.ObjectType for e in f.by_type("IfcFlowTerminal"))


# ================================================================ FAM-01 사용자 레시피 폴더
def _recipe(base_id="chair.basic", **kw):
    d = json.loads(json.dumps(library.load(base_id)))
    d.update(kw)
    return d


def _write(folder, name, data):
    folder.mkdir(exist_ok=True)
    p = folder / name
    if isinstance(data, (bytes, bytearray)):
        p.write_bytes(data)
    else:
        p.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def uf(base):
    d = base.path.parent / "fam"
    _write(d, "stool-1.json", _recipe(id="user.stool", version=1, name="스툴 1"))
    _write(d, "stool-3.json", _recipe(id="user.stool", version=3, name="스툴 3"))
    _write(d, "stool-2.json", _recipe(id="user.stool", version=2, name="스툴 2"))
    _write(d, "bench.json", _recipe("sofa.basic", id="user.bench", version=1, name="벤치"))
    return base, d


def test_load_dir_은_id_마다_가장_높은_version_을_올린다(uf):
    s, _d = uf
    r = ok(s.run("family.load_dir", {"path": "fam"}))
    assert r["rejected"] == []
    by = {x["id"]: x for x in r["loaded"]}
    assert set(by) == {"user.stool", "user.bench"}
    assert by["user.stool"]["version"] == 3 and by["user.stool"]["file"] == "stool-3.json" and by["user.stool"]["versions"] == [1, 2, 3]
    assert "versions" not in by["user.bench"] and all(x["created"] for x in r["loaded"])
    t = s.eng.st.el[by["user.stool"]["type"]]
    assert t["kind"] == "ftype" and t["family_id"] == "user.stool" and t["params"]["recipe"]["version"] == 3
    p = ok(s.run("family.place", {"family": "user.stool", "level": F.LV1, "position": [1000, 1000]}))
    assert p["type"] == by["user.stool"]["type"] and p["type_created"] is False      # 배치는 불러온 타입을 쓴다
    again = ok(s.run("family.load_dir", {"path": "fam"}))                            # 다시 불러오면 같은 타입(만들지 않음)
    assert [x["type"] for x in again["loaded"]] == [x["type"] for x in r["loaded"]] and not any(x["created"] for x in again["loaded"])


def test_load_dir_versions_로_version_을_고른다(uf):
    s, _d = uf
    r = ok(s.run("family.load_dir", {"path": "fam", "versions": {"user.stool": 2}}))
    st = next(x for x in r["loaded"] if x["id"] == "user.stool")
    assert st["version"] == 2 and st["file"] == "stool-2.json"
    r = ok(s.run("family.load_dir", {"path": "fam", "versions": {"user.stool": 9, "user.none": 1}}))
    rej = {(x["id"], x["code"]) for x in r["rejected"]}
    assert rej == {("user.stool", "version_not_found"), ("user.none", "version_not_found")}
    assert [x["id"] for x in r["loaded"]] == ["user.bench"]


def test_load_dir_은_잘못된_레시피를_이유와_함께_거절하고_나머지는_올린다(uf):
    s, d = uf
    _write(d, "broken.json", "{not json")
    _write(d, "latin.json", b"\xff\xfe\x00bad")
    _write(d, "schema.json", _recipe(id="user.bad", version=1, solids=[]))
    _write(d, "big.json", json.dumps(_recipe(id="user.big", version=1)) + " " * (70 * 1024))
    _write(d, "note.txt", "레시피 아님")
    r = ok(s.run("family.load_dir", {"path": "fam"}))
    rej = {x["file"]: x for x in r["rejected"]}
    assert set(rej) == {"broken.json", "latin.json", "schema.json", "big.json"}
    assert rej["broken.json"]["code"] == "invalid" and "JSON" in rej["broken.json"]["reason"]
    assert rej["latin.json"]["code"] == "invalid" and "UTF-8" in rej["latin.json"]["reason"]
    assert rej["schema.json"]["code"] == "invalid" and "/solids" in rej["schema.json"]["reason"]
    assert rej["big.json"]["code"] == "too_large"
    assert {x["id"] for x in r["loaded"]} == {"user.stool", "user.bench"}


def test_load_dir_같은_version_에_내용이_다르면_모호해_거절하고_같은_내용은_하나로_본다(uf):
    s, d = uf
    _write(d, "stool-3b.json", _recipe(id="user.stool", version=3, name="스툴 3 다른 내용"))
    _write(d, "bench-copy.json", _recipe("sofa.basic", id="user.bench", version=1, name="벤치"))   # 같은 내용의 복사본
    r = ok(s.run("family.load_dir", {"path": "fam"}))
    assert [(x["id"], x["code"], x["version"]) for x in r["rejected"]] == [("user.stool", "ambiguous_version", 3)]
    assert "stool-3.json" in r["rejected"][0]["reason"] and "stool-3b.json" in r["rejected"][0]["reason"]
    assert [x["id"] for x in r["loaded"]] == ["user.bench"]
    r = ok(s.run("family.load_dir", {"path": "fam", "versions": {"user.stool": 2}}))   # 모호하지 않은 version 을 고르면 올라간다
    assert {x["id"]: x["version"] for x in r["loaded"]} == {"user.bench": 1, "user.stool": 2}


def test_load_dir_기본_라이브러리와_같은_id_규칙(uf):
    s, d = uf
    _write(d, "chair-same.json", library.load("chair.basic"))                                 # 내용까지 같다: 그대로 올린다
    _write(d, "door-mine.json", _recipe("door.single", name="우리 문"))                        # 같은 id·같은 version, 내용이 다르다
    r = ok(s.run("family.load_dir", {"path": "fam"}))
    assert [(x["id"], x["code"]) for x in r["rejected"]] == [("door.single", "builtin_id")]
    chair = next(x for x in r["loaded"] if x["id"] == "chair.basic")
    assert "override" not in chair
    assert s.eng.st.el[chair["type"]]["params"]["recipe_hash"] == FS.recipe_hash_of(library.load("chair.basic"))
    r = s.run("family.load_dir", {"path": "fam", "builtin": "override"})
    door = next(x for x in ok(r)["loaded"] if x["id"] == "door.single")
    assert door["override"] is True and any("기본 라이브러리의 door.single" in w for w in r["warnings"])
    p = ok(s.run("family.place", {"family": "door.single", "host": F.W1, "offset": 4500}))   # 이 프로젝트에 올린 쪽이 우선
    assert p["type"] == door["type"]


def test_load_dir_경로_검사와_상한(base):
    s = base
    err(s.run("family.load_dir", {"path": "../outside"}), "PATH_DENIED")
    err(s.run("family.load_dir", {"path": "nope"}), "NOT_FOUND")
    _write(s.path.parent / "f1", "a.json", _recipe(id="user.a", version=1))
    err(s.run("family.load_dir", {"path": "f1/a.json"}))                                   # 폴더가 아니다
    d = s.path.parent / "many"
    for i in range(65):
        _write(d, f"r{i:02d}.json", "{}")
    e = err(s.run("family.load_dir", {"path": "many"}))
    assert "64" in e["message"]
    empty = s.path.parent / "empty"
    empty.mkdir()
    r = s.run("family.load_dir", {"path": "empty"})
    assert ok(r) == {"loaded": [], "rejected": []} and r["warnings"]


def test_load_dir_되돌리기와_dry_run(uf):
    s, _d = uf
    n0 = len(s.eng.st.by_kind.get("ftype", ()))
    r = s.run("family.load_dir", {"path": "fam"}, dry_run=True)
    assert r["ok"] and r.get("dry_run") and len(s.eng.st.by_kind.get("ftype", ())) == n0
    ok(s.run("family.load_dir", {"path": "fam"}))
    assert len(s.eng.st.by_kind.get("ftype", ())) == n0 + 2
    ok(s.run("history.undo", {}))
    assert len(s.eng.st.by_kind.get("ftype", ())) == n0


def _script(run, ws):
    _write(ws / "fam", "stool-2.json", _recipe(id="user.stool", version=2, name="스툴"))
    _write(ws / "fam", "stool-1.json", _recipe(id="user.stool", version=1, name="스툴 옛"))
    ok(run("family.load_dir", {"path": "fam"}))
    ok(run("family.place", {"id": FI, "family": "user.stool", "level": F.LV1, "position": [1500, 1500]}))
    ok(run("family.place", {"family": "door.fire", "host": F.W1, "offset": 3000}))
    ok(run("family.place", {"family": "window.hung", "host": F.W2, "offset": 1500}))
    ok(run("family.place", {"family": "lighting.ceiling", "level": F.LV1, "position": [3000, 2000], "elevation": 2400}))


def test_새_요소는_다시_열어도_같다(tmp_path):
    s = kit.Session(tmp_path / "t.dobim")
    for n, a in F.SCRIPT:
        ok(s.run(n, a))
    _script(s.run, tmp_path)
    h = s.eng.hashes()
    assert s.eng.intent_consistency() == []
    s.close()
    e = Engine(tmp_path / "t.dobim", "t", create=False)
    try:
        h2 = e.hashes()
        assert (h2["state"], h2["derived"], h2["ifc"]) == (h["state"], h["derived"], h["ifc"])
    finally:
        e.close()


def test_IFC_왕복은_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        _script(b.run, tmp_path)
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


# ================================================================ IO-17 인쇄 여백·축척 맞춤(sheet.render print)
@pytest.fixture
def sheet(base):
    s = base
    ok(s.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("sheet.define", {"id": F.SH1, "name": "A-101", "views": [F.V1], "paper": "A3", "project_name": "예시", "sheet_number": "A-101",
                              "date": "2026-01-01"}))
    return s


def _render(s, path, pr=None, **kw):
    return _destructive(s, "sheet.render", {"id": F.SH1, "path": path, "overwrite": True, **kw} | ({"print": pr} if pr is not None else {}))


def _svg_size(p):
    t = p.read_text(encoding="utf-8")
    m = re.search(r'width="([\d.]+)mm" height="([\d.]+)mm"', t)
    return float(m.group(1)), float(m.group(2)), t


def test_인쇄_설정이_없으면_시트_파일은_이전과_같다(sheet):
    s = sheet
    assert "print" not in ok(_render(s, "a.svg"))
    r = ok(_render(s, "b.svg", {"paper": "A3", "margin": 0, "fit": False}))
    assert r["print"] == {"paper": "A3", "margin": 0.0, "fit": False, "zoom": 100.0, "origin": [0.0, 0.0]}
    a, b = (s.path.parent / "a.svg").read_text(encoding="utf-8"), (s.path.parent / "b.svg").read_text(encoding="utf-8")
    body = lambda t: [ln for ln in t.splitlines() if not ln.startswith(("<g id=\"print\"", "</g>"))]   # noqa: E731
    assert body(a) == body(b)                                                       # 100%·여백 0 이면 그림은 같고 그룹 표지만 더해진다


def test_축척_맞춤은_인쇄_용지_여백_안에_시트를_가운데_맞춘다(sheet):
    s = sheet
    r = _render(s, "a4.svg", {"paper": "A4", "margin": 5})
    pr = ok(r)["print"]
    k = min((297 - 10) / 420, (210 - 10) / 297)
    assert pr["paper"] == "A4" and pr["fit"] is True and pr["zoom"] == pytest.approx(k * 100, abs=1e-3)
    assert pr["origin"][0] == pytest.approx(5 + (287 - k * 420) / 2, abs=1e-3) and pr["origin"][1] == pytest.approx(5.0, abs=1e-3)
    assert any("축척 맞춤" in w for w in r["warnings"])
    w, h, t = _svg_size(s.path.parent / "a4.svg")
    assert (w, h) == (297.0, 210.0) and 'data-sheet-paper="A3"' in t
    v = ok(r)["views"][0]
    assert v["x"] >= 5 and v["x"] + v["w"] <= 292 and v["y"] >= 5 and v["y"] + v["h"] <= 205
    # 큰 용지로 확대도 한다(A3 -> A1 세로), PDF·PNG 도 인쇄 용지 크기다
    r = ok(_render(s, "a1.pdf", {"paper": "A1P", "margin": 10}))
    assert r["print"]["zoom"] > 100 and r["paper"] == "A3"
    pdf = (s.path.parent / "a1.pdf").read_bytes()
    mb = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf)
    assert float(mb.group(1)) == pytest.approx(594 * 72 / 25.4, abs=0.01) and float(mb.group(2)) == pytest.approx(841 * 72 / 25.4, abs=0.01)
    r = ok(_render(s, "p.png", {"paper": "A4P"}, max_px=600))
    assert r["pixels"][1] > r["pixels"][0]                                         # 세로 용지


def test_실제_크기_인쇄는_여백_안에_들어가지_않으면_거절한다(sheet):
    s = sheet
    e = err(_render(s, "x.svg", {"margin": 5, "fit": False}))
    assert e["path"] == "/print" and "fit" in e["message"]
    r = ok(_render(s, "x.svg", {"paper": "A2", "margin": 20, "fit": False}))
    assert r["print"]["zoom"] == 100.0 and r["print"]["origin"] == [round((594 - 420) / 2, 3), round((420 - 297) / 2, 3)]
    e = err(_render(s, "x.dxf", {"paper": "A4"}))
    assert e["path"] == "/print"
    err(s.run("sheet.render", {"id": F.SH1, "path": "x.svg", "print": {"margin": 60}}), "SCHEMA_INVALID")


def test_인쇄_설정은_시트_정의에_저장하지_않고_sheet_export_image_인자는_그대로다(sheet):
    s = sheet
    before = json.dumps(s.eng.st.el[F.SH1]["params"], sort_keys=True)
    ok(_render(s, "r.svg", {"paper": "A4"}))
    assert json.dumps(s.eng.st.el[F.SH1]["params"], sort_keys=True) == before
    err(s.run("sheet.define", {"id": F.SH1, "name": "A-101", "views": [F.V1], "print": {"paper": "A4"}}), "SCHEMA_INVALID")
    err(s.run("sheet.export", {"views": [F.V1], "path": "e.svg", "print": {"paper": "A4"}}), "SCHEMA_INVALID")
    err(s.run("sheet.image", {"id": F.SH1, "print": {"paper": "A4"}}), "SCHEMA_INVALID")
