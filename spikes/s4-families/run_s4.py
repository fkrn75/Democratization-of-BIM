# SPDX-License-Identifier: MPL-2.0
"""S4 측정 스크립트: results/ 에 재현 가능한 JSON 을 남긴다.

  <venv python> run_s4.py            # library + mutants + integration 을 모두 측정
출력: results/s4_library.json, results/s4_mutants.json, results/s4_integration.json
(패리티는 s1-core/tools/parity.py 를 따로 돌린다: results/s4_parity.json)
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "s1-core"
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(CORE / "tools"))
sys.path.insert(0, str(HERE / "tests"))

OUT = HERE / "results"


def write(name: str, obj) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def library() -> dict:
    from dobim_core.family.validate import validate_recipe
    from conftest import LIBRARY_IDS, load_lib
    rows = []
    for fid in LIBRARY_IDS:
        d = load_lib(fid)
        r = validate_recipe(d, "full")
        rows.append({"family": fid, "ok": r["ok"], "checks": r["checks"], "errors": r["errors"], "warnings": r["warnings"], "stats": r["stats"],
                     "recipe_bytes": len(json.dumps(d, ensure_ascii=False).encode("utf-8"))})
    return {"all_ok": all(r["ok"] for r in rows), "families": rows}


def mutants() -> dict:
    from dobim_core.family.validate import validate_recipe
    from test_validator import DOOR_MUTANTS
    from conftest import load_lib
    rows, caught = [], 0
    for name, (fn, code, level, path) in sorted(DOOR_MUTANTS.items()):
        d = copy.deepcopy(load_lib("door.single"))
        fn(d)
        r = validate_recipe(d, level)
        codes = sorted({e["code"] for e in r["errors"]})
        hit = (not r["ok"]) and code in codes
        caught += hit
        first = next((e for e in r["errors"] if e["code"] == code), None)
        rows.append({"mutant": name, "expected": code, "level": level, "caught": hit, "codes": codes,
                     "has_hint": bool(first and first.get("hint")), "path": first["path"] if first else None})
    return {"total": len(rows), "caught": caught, "rate": round(caught / len(rows), 3),
            "with_hint": sum(r["has_hint"] for r in rows), "mutants": rows}


def integration() -> dict:
    import kit
    import ifcopenshell
    import ifcopenshell.util.element as ue
    from dobim_core import fixture as F
    from dobim_core.family import library as L
    out: dict = {}
    d = Path(tempfile.mkdtemp(prefix="s4-int-"))
    s = kit.Session(d / "t.dobim")
    for n, a in F.SCRIPT:
        assert s.run(n, a)["ok"]
    base = s.eng.hashes()
    steps = [("family.place", {"family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 1500}),
             ("family.place", {"family": "door.single", "host": F.W3, "offset": 4500}),
             ("family.place", {"family": "window.fixed", "type_values": {"mullions": 1}, "values": {"sill": 600}, "host": F.W2, "offset": 2000}),
             ("family.place", {"family": "column.section", "type_values": {"section": "round"}, "level": F.LV1, "position": [1000, 1000]}),
             ("family.place", {"family": "column.section", "type_values": {"section": "chamfered"}, "level": F.LV1, "position": [2500, 1000]}),
             ("family.place", {"family": "wardrobe.builtin", "level": F.LV1, "position": [3000, 2500], "values": {"leaves": 3, "width": 2400}})]
    timings = []
    for n, a in steps:
        t = time.perf_counter()
        r = s.run(n, a)
        assert r["ok"], r
        timings.append(round((time.perf_counter() - t) * 1000, 1))
    out["place_ms"] = timings
    t = time.perf_counter()
    h = s.eng.hashes()
    out["flush_and_hash_ms"] = round((time.perf_counter() - t) * 1000, 1)
    v = s.run("model.validate", {"express_rules": True})["result"]
    out["model_validate_errors"] = v["errors"]
    out["intent_consistency_bad"] = s.eng.intent_consistency()
    out["derived_incremental_equals_reference"] = s.eng.derived_hash() == s.eng.derived_hash(reference=True)
    # 취소 6번 → 처음 해시
    for _ in steps:
        assert s.run("history.undo", {})["ok"]
    now = s.eng.hashes()
    out["undo_restores_hashes"] = {k: now[k] == base[k] for k in ("state", "derived", "ifc")}
    for _ in steps:
        assert s.run("history.redo", {})["ok"]
    after = s.eng.hashes()
    out["redo_restores_hashes"] = {k: after[k] == h[k] for k in ("state", "derived", "ifc")}
    # 다시 열기(로그 재생)
    s.eng.close()
    from dobim_core.engine import Engine
    e2 = Engine(s.path, "t")
    out["reopen_equal"] = e2.hashes() == after | {"rev": e2.rev}
    e2.flush_ifc()
    f = e2.ifc.f
    out["ifc_types"] = sorted(t.is_a() for t in f.by_type("IfcTypeObject"))
    out["ifc_type_counts"] = {c: len(f.by_type(c)) for c in ("IfcDoorType", "IfcWindowType", "IfcColumnType", "IfcFurnitureType", "IfcDoor", "IfcWindow", "IfcColumn", "IfcFurniture",
                                                             "IfcOpeningElement", "IfcRepresentationMap", "IfcMappedItem", "IfcRelDefinesByType")}
    p = d / "o.ifc"
    e2.ifc.write(str(p))
    out["ifc_file_bytes"] = p.stat().st_size
    g = ifcopenshell.open(str(p))
    intents = {t.Name: len(ue.get_pset(t, "DoBIM_Intent", "Intent", should_inherit=False)) for t in g.by_type("IfcTypeObject")}
    out["recipe_intent_bytes_in_ifc_per_type"] = intents
    out["recipe_intent_total_bytes"] = sum(intents.values())
    out["ifc_without_intent_bytes_estimate"] = out["ifc_file_bytes"] - out["recipe_intent_total_bytes"]
    out["recipe_roundtrip_equals_library"] = {
        t.Name: json.loads(ue.get_pset(t, "DoBIM_Intent", "Intent", should_inherit=False))["params"]["recipe"] == L.load(json.loads(ue.get_pset(t, "DoBIM_Intent", "Intent", should_inherit=False))["family_id"])
        for t in g.by_type("IfcTypeObject")}
    e2.close()
    return out


def review_fixes() -> dict:
    """독립 검수에서 나온 결함의 재현 시나리오를 다시 돌려 결과 코드와 소요 시간을 기록한다(수정 전에는 INTERNAL/78초/재오픈 불능)."""
    import math
    import sqlite3
    import kit
    from dobim_core import fixture as F
    from dobim_core.engine import Engine
    from dobim_core.family import spec as FS
    from dobim_core.family.validate import validate_recipe
    from conftest import load_lib
    out: dict = {}
    d = Path(tempfile.mkdtemp(prefix="s4-rev-"))
    s = kit.Session(d / "t.dobim")
    for n, a in F.SCRIPT:
        assert s.run(n, a)["ok"]

    def code(r):
        return r["error"]["code"] if not r["ok"] else "ok"

    # 1) wall.set 이 호스팅된 문의 제약을 깨는 경우
    s.run("family.place", {"family": "door.single", "host": F.W3, "offset": 1500})
    rev = s.eng.rev
    out["wall_set_thickness_50"] = {"result": code(s.run("wall.set", {"id": F.W3, "thickness": 50})), "rev_unchanged": s.eng.rev == rev}
    out["wall_set_height_2000"] = code(s.run("wall.set", {"id": F.W3, "height": 2000}))
    out["wall_set_thickness_120_allowed"] = code(s.run("wall.set", {"id": F.W3, "thickness": 120}))
    # 이미 깨진 값이 로그에 있는 프로젝트(구버전 시뮬레이션)가 열리는가
    s.eng.close()
    con = sqlite3.connect(s.path)
    p = json.loads(con.execute("SELECT params FROM element WHERE id=?", (F.W3,)).fetchone()[0])
    p["thickness"] = 50.0
    con.execute("UPDATE element SET params=? WHERE id=?", (json.dumps(p), F.W3))
    con.commit()
    con.close()
    try:
        s.eng = Engine(s.path, "t")
        s.env.adopt(s.path, s.eng)
        v = s.run("model.validate", {})["result"]
        out["legacy_broken_project"] = {"opens": True, "validate_errors": v["errors"], "reported": bool(v["violations"])}
        s.run("wall.set", {"id": F.W3, "thickness": 250})
        out["legacy_broken_project"]["after_repair_errors"] = s.run("model.validate", {})["result"]["errors"]
    except Exception as ex:   # noqa: BLE001
        out["legacy_broken_project"] = {"opens": False, "error": type(ex).__name__}
    # 2) 평가 캐시 포화
    FS._EVAL_MAX = 4
    codes = []
    for i in range(12):
        r = copy.deepcopy(load_lib("door.single"))
        r["id"] = f"door.v{i}"
        codes.append(code(s.run("family.register", {"recipe": r})))
    out["cache_saturation"] = {"registers_ok": codes.count("ok"), "of": len(codes), "validate_errors": s.run("model.validate", {})["result"]["errors"]}
    FS._EVAL_MAX = 1024
    # 5) 비정상 JSON
    bad = {}
    for label, mut in (("nan_default", lambda r: r["params"]["width"].update(default=float("nan"))),
                       ("inf_max", lambda r: r["params"]["width"].update(max=float("inf"))),
                       ("max_1e400_int", lambda r: r["params"]["width"].update(max=10 ** 400))):
        r = copy.deepcopy(load_lib("door.single"))
        mut(r)
        bad[label] = code(s.run("family.register", {"recipe": r}))
    (d / "deep.json").write_text("[" * 5000 + "]" * 5000, encoding="utf-8")
    bad["deep_5000_file"] = code(s.run("family.register", {"path": "deep.json"}))
    out["malformed_json"] = bad
    # 3) 자원 상한
    box = {"id": "box", "op": "extrude", "plane": "xy", "profile": {"shape": "rect", "u": [0, "a"], "v": [0, "a"]}, "range": [0, "a"]}
    base = {"schema": "dobim.family/1", "id": "x.t", "version": 1, "name": "t", "ifc": {"class": "IfcFurniture"},
            "params": {"a": {"kind": "type", "default": 500, "min": 100, "max": 900}}, "solids": [],
            "plan_symbol": {"default": [{"type": "line", "from": [0, 0], "to": [1, 1]}]}}

    def recipe(solids):
        r = copy.deepcopy(base)
        r["solids"] = solids
        return r

    def void(vid, u, v, **kw):
        return {"id": vid, "role": "void", "cuts": ["box"], "op": "extrude", "plane": "xy", "profile": {"shape": "rect", "u": u, "v": v}, "range": [-10, 2000], **kw}

    def timed(r, level="full"):
        t = time.perf_counter()
        res = validate_recipe(r, level)
        return {"ok": res["ok"], "codes": sorted({e["code"] for e in res["errors"]}), "seconds": round(time.perf_counter() - t, 2)}

    expr = "+".join(["1"] * 33)
    pts = [[f"{round(100 * math.cos(2 * math.pi * i / 32))}+0*({expr})", f"{round(100 * math.sin(2 * math.pi * i / 32))}+0*({expr})"] for i in range(32)]
    heavy = recipe([{"id": f"s{i}", "op": "extrude", "plane": "xy", "repeat": {"var": "k", "count": 50}, "profile": {"shape": "polygon", "points": pts},
                     "range": [f"0*({expr})", f"10+0*({expr})"]} for i in range(8)])
    out["limits"] = {
        "heavy_42KB_recipe": {**timed(heavy), "recipe_bytes": len(json.dumps(heavy))},
        "17_voids_on_one_solid": timed(recipe([box, void("v", ["k*10", "k*10+5"], [0, 5], repeat={"var": "k", "count": 17})]), "static"),
        "16_voids_on_one_solid": timed(recipe([box, void("v", ["k*10", "k*10+5"], [0, 5], repeat={"var": "k", "count": 16})]), "static"),
        "time_budget_0s": {"codes": sorted({e["code"] for e in validate_recipe(load_lib("door.single"), "full", time_budget=0.0)["errors"]})},
    }
    out["void_checks"] = {"void_eats_all": timed(recipe([box, void("v", [-10, 2000], [-10, 2000])])),
                          "void_misses": timed(recipe([box, void("v", [5000, 6000], [5000, 6000])])),
                          "void_half_ok": timed(recipe([box, void("v", ["a/2", 2000], [-10, 2000])]))}
    # 6) 다중 version
    r1, r2 = copy.deepcopy(load_lib("door.single")), copy.deepcopy(load_lib("door.single"))
    for r, ver in ((r1, 2), (r2, 1)):
        r["id"], r["version"], r["name"] = "door.multi", ver, f"v{ver}"
    s.run("family.register", {"recipe": r1})
    reg = s.run("family.register", {"recipe": r2})
    pl = s.run("family.place", {"family": "door.multi", "host": F.W3, "offset": 4000})
    out["multi_version"] = {"register_warned": any("door.multi" in w for w in reg["warnings"]),
                            "placed_version": s.eng.st.el[pl["result"]["type"]]["params"]["recipe"]["version"]}
    s.close()
    return out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    lib = library()
    write("s4_library.json", lib)
    mu = mutants()
    write("s4_mutants.json", mu)
    it = integration()
    write("s4_integration.json", it)
    rf = review_fixes()
    write("s4_review_fixes.json", rf)
    print(json.dumps({"library_all_ok": lib["all_ok"], "mutants": f"{mu['caught']}/{mu['total']}", "integration": {k: it[k] for k in (
        "model_validate_errors", "undo_restores_hashes", "redo_restores_hashes", "reopen_equal", "derived_incremental_equals_reference")},
                      "review_fixes": {k: rf[k] for k in ("wall_set_thickness_50", "legacy_broken_project", "cache_saturation", "malformed_json", "multi_version")}},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
