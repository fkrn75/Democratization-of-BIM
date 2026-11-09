"""왕복 비교 도구 (S0): 기준 IFC 와 시험 IFC 를 비교해 지표 M1~M8 을 계산한다.

사용법:
    python roundtrip_check.py REF.ifc TEST.ifc [--golden RA-01.golden.json] [--json out.json]
                              [--no-express] [--quiet]
    종료 코드: 모든 지표 통과 0, 하나라도 실패 1, 입력 오류 2.

지표 정의 (docs/roadmap.md 3절 + 아래 해석):
    M1 요소 GlobalId 보존율  : REF 의 IfcProduct(공간 구조·개구부 포함) 중 TEST 에 같은 GlobalId 가 있는 비율. 합격 99% 이상
    M2 클래스별 개수 일치     : IfcProduct 클래스별 개수 사전이 완전히 같다. 합격 100%
    M3 표준 Pset 값 보존율   : REF 의 (GlobalId, Pset, 속성) 중 이름이 Pset_ 로 시작하는 것의 값이 TEST 와 같은 비율
                               (부동소수는 상대오차 1e-6). 합격 95% 이상
    M4 개구부 관계 보존       : REF 의 IfcRelVoidsElement (벽,개구부) 쌍과 IfcRelFillsElement (개구부,채움) 쌍이
                               TEST 에 모두 있다. 합격 100%
    M5 재료 층 구성 보존      : REF 에서 층 재료(LayerSet/Usage)를 가진 요소의 (재료명, 두께 m) 목록이 같다. 합격 100%
    M6 요소별 체적 오차       : 지오메트리 반복기로 계산한 체적의 상대오차 0.5% 이내인 요소 비율. 합격 100%
                               (REF 에서 형상이 있는 요소는 TEST 에도 형상이 있어야 함)
    M7 DoBIM_Intent 보존율   : DoBIM_Intent.Intent 문자열이 글자 그대로 같은 비율. 기록만 (합격 판정 없음)
    M8 유효성 오류            : TEST 에 ifcopenshell.validate (express 규칙 포함) 오류 0건. 합격 0건
    M9 공개 검증 서비스       : 이 도구 범위 밖 (미시험)
보조 지표 (G0 규칙에는 없음, 도구가 위치 오류를 못 잡는 구멍을 막기 위해 추가):
    X1 위치 편차             : 요소 세계좌표 bbox 중심의 편차가 1mm 이내인 요소 비율. 합격 100%
    X2 REF 자체 유효성        : REF 의 M8 오류 수 (참고)
--golden 을 주면 REF 의 지오메트리 체적을 골든의 해석적 체적과 대조한다 (G1: 측정 도구 자체 검증).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.shape
import ifcopenshell.util.unit
import ifcopenshell.validate

VOL_TOL = 0.005       # M6 상대 허용오차
POS_TOL = 1e-3        # X1 허용오차 (m)
FLOAT_RTOL = 1e-6


# ------------------------------------------------------------------ 추출
def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or (isinstance(a, bool) and isinstance(b, bool) and a == b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=FLOAT_RTOL, abs_tol=1e-9)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def extract(f: ifcopenshell.file, geometry: bool = True) -> dict:
    prods = {e.GlobalId: e for e in f.by_type("IfcProduct") if getattr(e, "GlobalId", None)}
    out: dict = {"classes": {}, "gids": {}, "psets": {}, "intent": {}, "layers": {},
                 "voids": set(), "fills": set(), "volume": {}, "center": {}}
    for gid, e in prods.items():
        out["gids"][gid] = e.is_a()
        out["classes"][e.is_a()] = out["classes"].get(e.is_a(), 0) + 1
    # Pset / 의도
    for gid, e in prods.items():
        try:
            ps = ifcopenshell.util.element.get_psets(e, psets_only=True)
        except Exception:
            ps = {}
        for pn, props in ps.items():
            for k, v in props.items():
                if k == "id":
                    continue
                if pn.startswith("Pset_"):
                    out["psets"][(gid, pn, k)] = v
                if pn == "DoBIM_Intent" and k == "Intent":
                    out["intent"][gid] = v
    # 개구부 관계
    for r in f.by_type("IfcRelVoidsElement"):
        out["voids"].add((r.RelatingBuildingElement.GlobalId, r.RelatedOpeningElement.GlobalId))
    for r in f.by_type("IfcRelFillsElement"):
        out["fills"].add((r.RelatingOpeningElement.GlobalId, r.RelatedBuildingElement.GlobalId))
    # 재료 층
    scale = ifcopenshell.util.unit.calculate_unit_scale(f)
    for gid, e in prods.items():
        try:
            m = ifcopenshell.util.element.get_material(e, should_skip_usage=False)
        except Exception:
            m = None
        if m is None:
            continue
        s = m.ForLayerSet if m.is_a("IfcMaterialLayerSetUsage") else m
        if s is not None and s.is_a("IfcMaterialLayerSet"):
            out["layers"][gid] = [(ly.Material.Name if ly.Material else None,
                                   round((ly.LayerThickness or 0.0) * scale, 9)) for ly in s.MaterialLayers]
    if geometry:
        _geometry(f, prods, out)
    return out


def _geometry(f, prods, out) -> None:
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    inc = [e for e in prods.values() if not e.is_a("IfcOpeningElement")
           and (e.is_a("IfcElement") or e.is_a("IfcSpace"))]
    if not inc:
        return
    it = ifcopenshell.geom.iterator(settings, f, max(1, (os.cpu_count() or 2) // 2), include=inc)
    if not it.initialize():
        return
    while True:
        sh = it.get()
        try:
            gid = f.by_id(sh.id).GlobalId
            g = sh.geometry
            v = ifcopenshell.util.shape.get_volume(g)
            vs = g.verts
            xs, ys, zs = vs[0::3], vs[1::3], vs[2::3]
            if len(xs):
                out["volume"][gid] = v
                out["center"][gid] = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
        except Exception:
            pass
        if not it.next():
            break


def validity(f: ifcopenshell.file, express: bool = True) -> dict:
    lg = ifcopenshell.validate.json_logger()
    try:
        ifcopenshell.validate.validate(f, lg, express_rules=express)
    except Exception as ex:  # 검증기 자체가 죽으면 그것도 오류로 센다
        return {"errors": 1, "warnings": 0, "samples": [f"검증기 예외: {ex!r}"]}
    errs = [s for s in lg.statements if s["level"] in ("error", "critical")]
    warns = [s for s in lg.statements if s["level"] == "warning"]
    return {"errors": len(errs), "warnings": len(warns),
            "samples": [s["message"][:200] for s in errs[:8]]}


# ------------------------------------------------------------------ 비교
def compare(ref: dict, test: dict, ref_valid: dict | None, test_valid: dict, golden: dict | None = None) -> dict:
    R: dict = {}

    # M1
    miss = [g for g in ref["gids"] if g not in test["gids"]]
    n = len(ref["gids"])
    R["M1"] = {"value": 100.0 * (n - len(miss)) / n if n else 100.0, "threshold": ">=99", "unit": "%",
               "pass": (100.0 * (n - len(miss)) / n if n else 100.0) >= 99.0,
               "detail": [f"누락 {g} ({ref['gids'][g]})" for g in miss[:10]]}
    # M2
    rc, tc = ref["classes"], test["classes"]
    diff = {k: (rc.get(k, 0), tc.get(k, 0)) for k in sorted(set(rc) | set(tc)) if rc.get(k, 0) != tc.get(k, 0)}
    same_cls = sum(1 for k in rc if rc[k] == tc.get(k, 0))
    R["M2"] = {"value": 100.0 * same_cls / len(rc) if rc else 100.0, "threshold": "=100", "unit": "% (클래스)",
               "pass": not diff, "detail": [f"{k}: REF {a} / TEST {b}" for k, (a, b) in diff.items()]}
    # M3
    tot = len(ref["psets"])
    bad = [k for k, v in ref["psets"].items() if k not in test["psets"] or not _same(v, test["psets"][k])]
    val = 100.0 * (tot - len(bad)) / tot if tot else 100.0
    R["M3"] = {"value": val, "threshold": ">=95", "unit": "%", "total": tot, "pass": val >= 95.0,
               "detail": [f"{k} REF={ref['psets'][k]!r} TEST={test['psets'].get(k, '<없음>')!r}" for k in bad[:10]]}
    # M4
    pairs = [("void", p) for p in sorted(ref["voids"])] + [("fill", p) for p in sorted(ref["fills"])]
    lost = [(k, p) for k, p in pairs if p not in (test["voids"] if k == "void" else test["fills"])]
    val = 100.0 * (len(pairs) - len(lost)) / len(pairs) if pairs else 100.0
    R["M4"] = {"value": val, "threshold": "=100", "unit": "%", "total": len(pairs), "pass": not lost,
               "detail": [f"{k} 쌍 누락 {p}" for k, p in lost[:10]]}
    # M5
    lay = ref["layers"]
    badl = [g for g, v in lay.items() if g not in test["layers"] or not _same(
        [list(x) for x in v], [list(x) for x in test["layers"][g]])]
    val = 100.0 * (len(lay) - len(badl)) / len(lay) if lay else 100.0
    R["M5"] = {"value": val, "threshold": "=100", "unit": "%", "total": len(lay), "pass": not badl,
               "detail": [f"{g}: REF={lay[g]} TEST={test['layers'].get(g, '<없음>')}" for g in badl[:6]]}
    # M6
    vol = ref["volume"]
    errs, badv = [], []
    for g, v in vol.items():
        tv = test["volume"].get(g)
        if tv is None:
            badv.append((g, v, None, None))
            continue
        e = abs(tv - v) / abs(v) if abs(v) > 1e-12 else abs(tv - v)
        errs.append(e)
        if e > VOL_TOL:
            badv.append((g, v, tv, e))
    val = 100.0 * (len(vol) - len(badv)) / len(vol) if vol else 100.0
    R["M6"] = {"value": val, "threshold": "=100 (오차<=0.5%)", "unit": "% (요소)", "total": len(vol),
               "max_rel_err": max(errs) if errs else None, "pass": not badv and bool(vol),
               "detail": [f"{g} REF={a:.6f} TEST={b} err={e}" for g, a, b, e in badv[:8]]}
    # M7
    it = ref["intent"]
    badi = [g for g, s in it.items() if test["intent"].get(g) != s]
    val = 100.0 * (len(it) - len(badi)) / len(it) if it else None
    R["M7"] = {"value": val, "threshold": "기록만", "unit": "%", "total": len(it), "pass": None,
               "detail": [f"{g} 의도 소실/변경" for g in badi[:5]]}
    # M8
    R["M8"] = {"value": test_valid["errors"], "threshold": "=0", "unit": "건", "pass": test_valid["errors"] == 0,
               "warnings": test_valid["warnings"], "detail": test_valid["samples"]}
    # X1
    cen = ref["center"]
    badp = []
    for g, c in cen.items():
        t = test["center"].get(g)
        d = math.dist(c, t) if t else None
        if d is None or d > POS_TOL:
            badp.append((g, d))
    val = 100.0 * (len(cen) - len(badp)) / len(cen) if cen else 100.0
    R["X1"] = {"value": val, "threshold": "=100 (1mm)", "unit": "% (요소)", "total": len(cen), "pass": not badp,
               "detail": [f"{g} 편차 {d}" for g, d in badp[:8]]}
    if ref_valid is not None:
        R["X2"] = {"value": ref_valid["errors"], "threshold": "참고", "unit": "건", "pass": None, "detail": ref_valid["samples"]}
    # G1: 골든 해석적 체적 대조
    if golden:
        gv = {g: e["volume_analytic"] for g, e in golden["elements"].items() if e.get("volume_analytic") is not None}
        bad = [(g, gv[g], ref["volume"].get(g)) for g in gv
               if ref["volume"].get(g) is None or abs(ref["volume"][g] - gv[g]) / gv[g] > 1e-6]
        R["G1"] = {"value": 100.0 * (len(gv) - len(bad)) / len(gv) if gv else None, "threshold": "=100 (1e-6)",
                   "unit": "% (요소)", "total": len(gv), "pass": not bad and bool(gv),
                   "detail": [f"{golden['elements'][g]['name']} 해석={a:.6f} 측정={b}" for g, a, b in bad[:8]]}
    return R


def run_compare(ref_path, test_path, golden_path=None, express=True) -> dict:
    fr = ifcopenshell.open(str(ref_path))
    ft = ifcopenshell.open(str(test_path)) if str(test_path) != str(ref_path) else fr
    ref = extract(fr)
    test = ref if ft is fr else extract(ft)
    rv = validity(fr, express)
    tv = rv if ft is fr else validity(ft, express)
    golden = json.loads(Path(golden_path).read_text(encoding="utf-8")) if golden_path else None
    R = compare(ref, test, rv, tv, golden)
    R["_meta"] = {"ref": str(ref_path), "test": str(test_path), "schema_ref": fr.schema, "schema_test": ft.schema,
                  "n_products_ref": len(ref["gids"]), "n_products_test": len(test["gids"])}
    return R


def verdict(R: dict) -> bool:
    return all(v["pass"] for k, v in R.items() if not k.startswith("_") and v.get("pass") is not None)


def format_table(R: dict) -> str:
    rows = ["| 지표 | 값 | 기준 | 판정 |", "|---|---|---|---|"]
    for k in sorted(k for k in R if not k.startswith("_")):
        v = R[k]
        val = v["value"]
        sv = "-" if val is None else (f"{val:.3f}" if isinstance(val, float) else str(val))
        p = v["pass"]
        rows.append(f"| {k} | {sv} {v['unit']} | {v['threshold']} | {'기록' if p is None else ('통과' if p else '실패')} |")
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="IFC 왕복 비교 (M1~M8)")
    ap.add_argument("ref")
    ap.add_argument("test")
    ap.add_argument("--golden")
    ap.add_argument("--json")
    ap.add_argument("--no-express", action="store_true", help="EXPRESS WHERE 규칙 검사 생략")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    for p in (a.ref, a.test):
        if not Path(p).is_file():
            print(f"파일 없음: {p}", file=sys.stderr)
            return 2
    R = run_compare(a.ref, a.test, a.golden, not a.no_express)
    ok = verdict(R)
    if a.json:
        Path(a.json).write_text(json.dumps(R, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8", newline="\n")
    if not a.quiet:
        print(format_table(R))
        for k in sorted(k for k in R if not k.startswith("_")):
            for d in R[k].get("detail", [])[:6]:
                print(f"  [{k}] {d}")
        print("전체:", "통과" if ok else "실패")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
