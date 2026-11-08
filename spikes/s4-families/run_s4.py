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


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    lib = library()
    write("s4_library.json", lib)
    mu = mutants()
    write("s4_mutants.json", mu)
    it = integration()
    write("s4_integration.json", it)
    print(json.dumps({"library_all_ok": lib["all_ok"], "mutants": f"{mu['caught']}/{mu['total']}", "integration": {k: it[k] for k in (
        "model_validate_errors", "undo_restores_hashes", "redo_restores_hashes", "reopen_equal", "derived_incremental_equals_reference")}},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
