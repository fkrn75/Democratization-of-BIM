"""측정 도구 음성 시험 (S0 G1): 일부러 망가뜨린 사본에서 roundtrip_check 가 정확히 잡는지 확인한다.

각 사례는 RA-01.ifc 사본을 만든 뒤 한 가지 결함을 주입하고, 기대한 지표(expected_fail)만 실패하는지 검사한다.
기대와 다르면 (잡아야 할 것을 못 잡음 / 엉뚱한 지표가 실패) 이 스크립트가 비정상 종료한다.
결과: results/negative_tests.json, 사본은 out/neg/ (gitignore 대상).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.feature
import ifcopenshell.api.geometry
import ifcopenshell.api.root
import ifcopenshell.util.element

import roundtrip_check as rc

HERE = Path(__file__).resolve().parent
REF = HERE / "RA-01.ifc"
OUTDIR = HERE / "out" / "neg"
RESULTS = HERE / "results" / "negative_tests.json"
CHECKED = ["M1", "M2", "M3", "M4", "M5", "M6", "M8", "X1"]


def by_name(f, name):
    return next(e for e in f.by_type("IfcRoot") if getattr(e, "Name", None) == name)


def set_pset(f, el, pset, prop, value):
    ps = ifcopenshell.util.element.get_pset(el, pset)
    ifcopenshell.api.run("pset.edit_pset", f, pset=f.by_id(ps["id"]), properties={prop: value})


# ---- 결함 주입 함수들 -------------------------------------------------
def n00_control(f):
    pass


def n01_remove_wall(f):
    ifcopenshell.api.run("root.remove_product", f, product=by_name(f, "W-E-02"))


def n02_door_guid(f):
    by_name(f, "D-03").GlobalId = "0000000000000000000001"


def n03_flip_isexternal_all_walls(f):
    for w in f.by_type("IfcWall"):
        cur = ifcopenshell.util.element.get_pset(w, "Pset_WallCommon", "IsExternal")
        set_pset(f, w, "Pset_WallCommon", "IsExternal", not cur)


def n03b_flip_isexternal_5_walls(f):
    for w in f.by_type("IfcWall")[:5]:
        cur = ifcopenshell.util.element.get_pset(w, "Pset_WallCommon", "IsExternal")
        set_pset(f, w, "Pset_WallCommon", "IsExternal", not cur)


def n04_remove_void_rel(f):
    f.remove(f.by_type("IfcRelVoidsElement")[0])


def n05_layer_thickness(f):
    ls = next(s for s in f.by_type("IfcMaterialLayerSet") if s.LayerSetName == "EXT-320")
    insul = next(ly for ly in ls.MaterialLayers if ly.Material.Name == "Insulation")
    insul.LayerThickness = 0.09


def _scale_column_depth(f, k):
    c = by_name(f, "C-01")
    solid = c.Representation.Representations[0].Items[0]
    solid.Depth = solid.Depth * k


def n06_column_depth_1_006(f):
    _scale_column_depth(f, 1.006)


def n06b_column_depth_1_004(f):
    _scale_column_depth(f, 1.004)


def n07_drop_intent(f):
    for w in f.by_type("IfcWall"):
        ps = ifcopenshell.util.element.get_pset(w, "DoBIM_Intent")
        ifcopenshell.api.run("pset.remove_pset", f, product=w, pset=f.by_id(ps["id"]))


def n08_dangling_profile(f):
    c = by_name(f, "C-01")
    prof = c.Representation.Representations[0].Items[0].SweptArea
    f.remove(prof)


def n09_move_wall(f):
    import numpy as np
    w = by_name(f, "W-I-03")
    m = ifcopenshell.util.placement.get_local_placement(w.ObjectPlacement)
    m[0][3] += 0.5
    ifcopenshell.api.run("geometry.edit_object_placement", f, product=w, matrix=m)


def n10_reclass(f):
    ifcopenshell.api.run("root.reassign_class", f, product=by_name(f, "C-01"), ifc_class="IfcBeam")


def n11_wrong_unit(f):
    u = next(x for x in f.by_type("IfcSIUnit") if x.UnitType == "LENGTHUNIT")
    u.Prefix = "MILLI"


import ifcopenshell.util.placement  # noqa: E402

CASES = [
    ("N00 무결함 사본(양성 대조)", n00_control, []),
    ("N01 외벽 1개 삭제(개구부 없는 벽)", n01_remove_wall, ["M1", "M2", "M5", "M6", "X1"]),
    ("N02 문 GlobalId 변경", n02_door_guid, ["M1", "M4", "M6", "X1"]),
    ("N03 전 벽 IsExternal 반전(7%)", n03_flip_isexternal_all_walls, ["M3"]),
    ("N03b 벽 5개 IsExternal 반전(2.6%, 경계 안쪽)", n03b_flip_isexternal_5_walls, []),
    ("N04 개구부 관계 1개 삭제", n04_remove_void_rel, ["M4", "M6", "M8"]),
    ("N05 단열층 두께 100->90mm", n05_layer_thickness, ["M5"]),
    ("N06 기둥 높이 +0.6%", n06_column_depth_1_006, ["M6", "X1"]),
    ("N06b 기둥 높이 +0.4% (체적 허용 안쪽, 위치는 어긋남)", n06b_column_depth_1_004, ["X1"]),
    ("N07 벽 DoBIM_Intent 전부 삭제", n07_drop_intent, []),  # M7 은 기록 지표라 값 하락만 확인
    ("N08 기둥 프로파일 삭제(스키마 파손)", n08_dangling_profile, ["M6", "M8", "X1"]),
    ("N09 내벽 0.5m 이동", n09_move_wall, ["X1"]),
    ("N10 IfcColumn -> IfcBeam 재분류", n10_reclass, ["M2"]),
    ("N11 길이 단위 m -> mm 오표기", n11_wrong_unit, ["M5", "M6", "X1"]),
]


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    ref = rc.extract(ifcopenshell.open(str(REF)))
    rv = rc.validity(ifcopenshell.open(str(REF)))
    rows, all_ok = [], True
    for i, (title, fn, expected) in enumerate(CASES):
        f = ifcopenshell.open(str(REF))
        fn(f)
        p = OUTDIR / f"neg_{i:02d}.ifc"
        f.write(str(p))
        ft = ifcopenshell.open(str(p))
        R = rc.compare(ref, rc.extract(ft), rv, rc.validity(ft))
        failed = sorted(k for k in CHECKED if R[k]["pass"] is False)
        ok = failed == sorted(expected)
        if title.startswith("N07"):  # 의도 보존율이 실제로 하락했는지
            ok = ok and R["M7"]["value"] is not None and R["M7"]["value"] < 100.0
        all_ok &= ok
        rows.append({"case": title, "expected_fail": sorted(expected), "actual_fail": failed, "ok": ok,
                     "M7": R["M7"]["value"], "M3": round(R["M3"]["value"], 2), "M6": round(R["M6"]["value"], 2),
                     "M1": round(R["M1"]["value"], 2), "m8_errors": R["M8"]["value"]})
        print(("OK  " if ok else "NG  ") + title, "| 기대", expected, "| 실제", failed)
    RESULTS.write_text(json.dumps({"all_ok": all_ok, "cases": rows}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("음성 시험 전체:", "통과" if all_ok else "불일치 있음")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
