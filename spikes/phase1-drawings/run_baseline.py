# SPDX-License-Identifier: MPL-2.0
"""1단계 도면 엔진 기준선·개선 후 산출 (RA-01 코어 범위 입력 + 주석 + 패밀리 배치 + 종단면).

G1 때와 같은 방식으로 도면을 다시 뽑는다(s3lib.build 의 RA-01 코어 범위 모델). 산출 파일은 저장소 밖 폴더(--out)에 둔다.

사용:  PYTHONPATH=<worktree>/spikes/s1-core/src  <python> run_baseline.py --out <폴더> [--sheets] [--no-slabs]
  기본          : base/(기본 7뷰) annot/(치수·태그 포함 7뷰) fam/(창·기둥 배치 + 전체높이 치수) xsec/(종단면)  각 SVG·PDF
  --sheets      : 위에 더해 sheets/ 아래에 시트(A3·A1) SVG·PDF(한글 포함)를 뽑는다
  --no-slabs    : 입력에서 슬래브 3개를 뺀다(이전 단계 산출과 같은 입력으로 되돌려 바이트 비교할 때)
  --no-heights  : 높이 치수 자동 배치(heights/)를 건너뛴다
  --no-stairs / --no-spaces / --no-fire / --no-finish : 계단·공간·방화 등급·외벽 마감 입력을 뺀다(네 번째 작업 이전 입력)
  --no-sheetdef : 여섯 번째 작업(시트 정의 저장·왕복 probe)을 건너뛴다
  --no-sheet2   : 다섯 번째 작업(색인 시트·통심선·기호·태그·키 플랜·개정·서명·선 굵기 프로파일·작성기준 프로파일)을 건너뛴다

다섯 번째 작업 산출(--sheets): sheets/A-000(색인: 도면목록·범례·주기·레벨 노트·창호/벽체 일람표·재료표), sheets/A-002(통심선·단면·입면 기호·부위 상세·부호 태그·키 플랜·
  건축주·서명·개정 구름), sheets/A-003(별도 작성기준 선 굵기 프로파일 적용), profile.json(drawing.profile).

높이 치수(두 번째 작업): 단면·입면 6뷰(단면 2·입면 4)에 annot.dim.heights 로 층고·반자높이(슬래브 밑면 기준 안목)·최고 높이를 자동 배치하고
  heights/ 에 낸다. 같은 뷰에 손으로 만든 높이 치수(annot/ 용 level 사이 치수)는 지우고 자동 치수로 바꾼 뒤 시트를 뽑는다.

입력 변경(세 번째 작업): RA-01 코어 범위 모델에 슬래브 3개를 더한다(RA-01 에는 슬래브 3개가 있다. g2-gate 5-1절). 기본 켜짐.
  S-GND  1F 바닥 슬래브(1F 윗면 0, 두께 200, 외벽 바깥면까지)   S-2F  2F 바닥 슬래브(2F 윗면, 두께 200, 외벽 축선 안쪽)
  S-ROOF 지붕 슬래브(2F 벽 윗면 6600 = 2F + 3300, 두께 200, 외벽 바깥면까지)

입력 변경(네 번째 작업, 모두 기본 켜짐): RA-01 에 계단 1개, 공간 6개, 방화 등급, 외벽 재료를 더한다(g2-gate 5-1절 RA-01 사양).
  계단 ST-1F   1F 오른쪽 위 방(주방)의 직선 계단: 걸음선 (5000,5000)->(9400,5000), 폭 1200, 오름 3300(1F->2F), 챌판 19
  공간 6       1F 거실·복도(분류 복도)·침실1·침실2·주방, 2F 침실3 (벽 안쪽 면 기준 경계)
  방화         1F 내벽 W-I-1F-1(구획 벽)에 Pset_WallCommon.FireRating EI60, 문 D-02(그 벽에 호스팅) EI60, 현관문 D-01 EI30
  외벽 재료    외벽 8개에 Pset_WallCommon.IsExternal 참 + 재료(1F 치장벽돌, 2F 스터코)
  종단 계단 단면 뷰 section-CC (y=5000, 걸음선 위를 지난다)를 따로 만들어 stair/ 에 낸다(base/·annot/ 의 뷰 목록은 그대로)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s3-drawings"))
sys.path.insert(0, str(HERE.parent / "s1-core" / "tools"))

import s3lib  # noqa: E402
from dobim_core import drawing as D  # noqa: E402


def ok(r):
    assert r.get("ok"), r
    return r["result"]


def export(sess, name, args, out_path: Path):
    """확인 토큰 2단계 호출로 내보낸다. 응답 result 를 돌려준다."""
    a = {**args, "path": out_path.name, "overwrite": True}
    r = sess.run(name, a)
    if r.get("ok"):   # 덮어쓸 파일이 없으면 확인 절차 없이 쓴다
        return r["result"]
    tok = r["error"]["details"]["confirm_token"]
    return ok(sess.run(name, a, confirm=tok))


def ifc_out(sess, name):
    """IFC 를 내보낸다(ifc.export 는 overwrite 인자가 없고 기존 파일이면 확인 토큰이 필요하다)."""
    r = sess.run("ifc.export", {"path": name})
    if not r.get("ok"):
        r = sess.run("ifc.export", {"path": name}, confirm=r["error"]["details"]["confirm_token"])
    return ok(r)


def cornerprobe(sess, log, work):
    """여섯 번째 작업(B-02): 모든 벽에서 도면이 그리는 벽 범위(drawing.wall_range)와 내보낸 IFC 파일의 실제 형상(축선 방향 범위)이 같은지 잰다.
    IFC 는 파일을 열어 형상을 직접 만든 정점으로 읽는다(엔진 코드와 독립). 연장된 끝(L 조인 모서리)의 길이도 기록한다. 결과는 run.json 의 corner_probe."""
    from math import sqrt

    import ifcopenshell
    import ifcopenshell.geom
    from dobim_core import queries as Q
    ifc_out(sess, "corner.ifc")
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    f = ifcopenshell.open(str(work / "corner.ifc"))
    st = ifcopenshell.geom.settings()
    st.set("use-world-coords", True)
    rows = []
    for w in sorted(sess.eng.st.by_kind.get("wall", ())):
        x1, y1, x2, y2 = sess.eng.st.el[w]["params"]["axis"]
        ln = sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        c, s_ = (x2 - x1) / ln, (y2 - y1) / ln
        v = ifcopenshell.geom.create_shape(st, f.by_guid(w)).geometry.verts
        ts = [((v[i] * 1000.0 - x1) * c + (v[i + 1] * 1000.0 - y1) * s_) for i in range(0, len(v), 3)]
        lo, hi = min(ts), max(ts)
        rng = g.q(D.wall_range, w)
        trim = g.q(Q.wall_trim, w)
        rows.append({"id": w, "ifc": [round(lo, 3), round(hi, 3)], "drawing": [round(rng[0], 3), round(rng[1], 3)], "trim": [round(trim[0], 3), round(trim[1], 3)],
                     "diff_mm": round(max(abs(lo - rng[0]), abs(hi - rng[1])), 4), "extended_mm": round((trim[0] - rng[0]) + (rng[1] - trim[1]), 3)})
    log["corner_probe"] = {"walls": len(rows), "extended_walls": sum(1 for r in rows if r["extended_mm"] > 0), "max_diff_mm": max(r["diff_mm"] for r in rows),
                           "legacy_notch_walls": sum(1 for r in rows if r["extended_mm"] > 0 and abs(r["ifc"][0] - r["trim"][0]) < 1e-3 and abs(r["ifc"][1] - r["trim"][1]) < 1e-3),
                           "rows": rows}


def sheetdef(sess, saved, log, out, work, sheets_dir):
    """여섯 번째 작업(B-04): 낸 시트의 인자를 sheet.define 으로 모델에 저장하고, 저장한 정의로 다시 내 파일이 sheet.export 산출과 바이트까지 같은지,
    IFC 로 내보냈다 가져와도 시트가 되살고 해시 3종·시트 산출이 같은지 확인한다. 결과는 run.json 의 sheet_def_probe 로 남는다(판정기의 근거)."""
    from dobim_core import dispatch
    names = sorted(saved)
    for nm in names:
        ok(sess.run("sheet.define", {"name": nm, **{k: v for k, v in saved[nm].items() if k not in ("path", "overwrite")}}))
    lst = ok(sess.run("sheet.list", {}))
    same = {}
    for nm in names:
        sid = next(i["id"] for i in lst["items"] if i["name"] == nm)
        for ext in ("svg", "pdf"):
            export(sess, "sheet.render", {"id": sid}, work / f"def-{nm}.{ext}")
            same[f"{nm}.{ext}"] = (work / f"def-{nm}.{ext}").read_bytes() == (sheets_dir / f"{nm}.{ext}").read_bytes()
    h0 = ok(sess.run("model.hash", {}))
    ifc_out(sess, "sheetdef.ifc")
    imp = ok(dispatch.run(sess.env, "ifc.import", {"source": "sheetdef.ifc", "path": "sheetdef-imp.dobim"}))
    h1 = ok(dispatch.run(sess.env, "model.hash", {}, project="sheetdef-imp.dobim"))
    lst2 = ok(dispatch.run(sess.env, "sheet.list", {}, project="sheetdef-imp.dobim"))
    rt_same = {}
    for nm in names:
        sid = next(i["id"] for i in lst2["items"] if i["name"] == nm)
        for ext in ("svg", "pdf"):
            a = {"id": sid, "path": f"imp-{nm}.{ext}", "overwrite": True}
            r1 = dispatch.run(sess.env, "sheet.render", a, project="sheetdef-imp.dobim")
            tok = r1["error"]["details"]["confirm_token"]
            ok(dispatch.run(sess.env, "sheet.render", a, project="sheetdef-imp.dobim", confirm=tok))
            rt_same[f"{nm}.{ext}"] = (work / f"imp-{nm}.{ext}").read_bytes() == (sheets_dir / f"{nm}.{ext}").read_bytes()
    log["sheet_def_probe"] = {"sheets": names, "views_per_sheet": {i["name"]: len(i["views"]) for i in lst["items"]}, "missing_views": sum(len(i["missing_views"]) for i in lst["items"]),
                              "render_equals_export": same, "import_elements": imp["elements"], "import_warnings": imp["warnings"], "restored": sorted(i["name"] for i in lst2["items"]),
                              "hash_equal": [h0[k] == h1[k] for k in ("state", "derived", "ifc")], "roundtrip_render_equals_export": rt_same}


def sheet2(sess, ids, views, log, out, work, sheets_dir, exp_views, sheet, spec):
    """다섯 번째 작업 산출. 색인 시트, 확장 시트(통심선·기호·태그·키 플랜·개정·서명), 선 굵기 프로파일 시트, 작성기준 프로파일. 낸 시트 인자를 돌려준다."""
    saved = {}

    def sheet_n(nm, a):
        """파일 이름이 도면번호와 같게 내보낸다(T-04 파일명 일치)."""
        saved[nm] = {**spec, **a}
        for ext in ("svg", "pdf"):
            r_ = export(sess, "sheet.export", {**spec, **a}, work / f"{nm}.{ext}")
            (sheets_dir / f"{nm}.{ext}").write_bytes((work / f"{nm}.{ext}").read_bytes())
            log["exports"].append({"dir": "sheets", "sheet": nm, "ext": ext, "bytes": r_["bytes"], "font_embedded": r_["font_embedded"],
                                   "non_ascii_dropped": r_["non_ascii_dropped"], "views": len(r_["views"]), "number_check": r_["number_check"], "number": r_["sheet_number"]})

    lx, ly = s3lib.LX, s3lib.LY
    grids = [{"label": f"X{i}", "axis": "x", "pos": x} for i, x in enumerate((0.0, 4000.0, 8000.0, lx), 1)] + \
            [{"label": f"Y{i}", "axis": "y", "pos": y} for i, y in enumerate((0.0, 4000.0, ly), 1)]
    log["grids"] = grids
    signers = [{"role": "설계자", "name": "홍길동"}, {"role": "관계전문기술자", "name": ""}]
    revs = [{"rev": "1", "date": "2026-11-10", "desc": "창 위치 조정"}, {"rev": "2", "date": "2026-11-15", "desc": "2층 평면 개정", "view": views["plan-2F"]}]
    full = {"views": [views["plan-1F"], views["plan-2F"], views["section-BB"], views["elev-S"]], "paper": "A1", "sheet_name": "평면도·단면도·입면도(통심선·기호)",
            "sheet_number": "A-002", "client": "예시 건축주", "office": "도비엠 설계", "signers": signers, "revisions": revs, "grids": grids, "marks": True,
            "tags": ["room", "opening", "wall"], "key_plan": views["plan-1F"],
            "callouts": [{"view": views["plan-1F"], "rect": [4000.0, 4000.0, lx, ly], "label": "1"}]}
    sheet_n("A-002", full)
    # 별도 작성기준 선 굵기 프로파일: 같은 평면을 더 가는 선으로(절단선 0.35·보이는 선 0.13)
    sheet_n("A-003", {"views": [views["plan-1F"]], "paper": "A3", "sheet_name": "1층 평면도(선 굵기 프로파일)", "sheet_number": "A-003", "line_profile": {"cut": 0.35, "proj": 0.13}})
    # 색인 시트: 도면목록표는 위에서 낸 시트 번호를 그대로 적는다
    listing = [{"number": "A-000", "title": "도면목록·범례·일람표", "scale": "-"}, {"number": "A-001", "title": "평면도·단면도·입면도", "scale": "1:100"},
               {"number": "A-002", "title": "평면도·단면도·입면도(통심선·기호)", "scale": "1:100"}, {"number": "A-003", "title": "1층 평면도(선 굵기 프로파일)", "scale": "1:100"},
               {"number": "A-101", "title": "1·2층 평면도", "scale": "1:100"}, {"number": "A-201", "title": "혼합 축척 예", "scale": "도면참조"}]
    log["drawing_list"] = listing
    idx = {"paper": "A1", "sheet_name": "도면목록·범례·일람표", "sheet_number": "A-000", "client": "예시 건축주", "office": "도비엠 설계", "signers": signers,
           "revisions": [revs[0]], "drawings": listing, "project_name": spec["project_name"], "date": spec["date"]}
    for ext in ("svg", "pdf"):
        r_ = export(sess, "sheet.index", idx, work / f"A-000.{ext}")
        (sheets_dir / f"A-000.{ext}").write_bytes((work / f"A-000.{ext}").read_bytes())
        log["exports"].append({"dir": "sheets", "sheet": "A-000", "ext": ext, "bytes": r_["bytes"], "font_embedded": r_["font_embedded"],
                               "non_ascii_dropped": r_["non_ascii_dropped"], "rows": r_["rows"], "number_check": r_["number_check"], "number": r_["sheet_number"]})
    prof = ok(sess.run("drawing.profile", {}))
    (out / "profile.json").write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheets", action="store_true")
    ap.add_argument("--no-slabs", action="store_true")
    ap.add_argument("--no-heights", action="store_true")
    ap.add_argument("--no-sheet2", action="store_true")
    ap.add_argument("--no-sheetdef", action="store_true")
    for f in ("stairs", "spaces", "fire", "finish"):
        ap.add_argument(f"--no-{f}", action="store_true")
    o = ap.parse_args()
    out = Path(o.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir(exist_ok=True)
    proj = work / "ra01.dobim"
    if proj.exists():
        proj.unlink()
    sess, ids, views = s3lib.build(proj)
    log = {"exports": [], "slabs": []}
    # 종단면은 뷰 묶음(7뷰)과 따로 만들어 두었다가 나중에 views 에 넣는다(base/·annot/ 의 뷰 목록은 그대로)
    vc0 = ok(sess.run("view.create", {"id": "4" + "0" * 20 + "9", "name": "A-A 종단면", "type": "section", "p1": [2000.0, -1000.0], "p2": [2000.0, 9000.0]}))
    probe_views = {**views, "xsec-AA": vc0["id"]}

    def measure():
        sess.eng.sync()
        sess.eng._touch_pending()
        return {k: {"segments": len(sess.eng.g.q(D.view_lines, v)), "fills": len(sess.eng.g.q(D.view_fills, v))} for k, v in probe_views.items()}

    before = measure()
    if not o.no_slabs:
        # 입력 변경: 슬래브 3개(RA-01 슬래브 수). 외벽 두께 320: 바깥면 = 축선 바깥 160
        lx, ly, ho = s3lib.LX, s3lib.LY, s3lib.EXT_T / 2
        outer = [[-ho, -ho], [lx + ho, -ho], [lx + ho, ly + ho], [-ho, ly + ho]]
        axis = [[0.0, 0.0], [lx, 0.0], [lx, ly], [0.0, ly]]
        for k, (nm, lv, b, off) in enumerate((("S-GND", "1F", outer, 0.0), ("S-2F", "2F", axis, 0.0), ("S-ROOF", "2F", outer, s3lib.STOREY)), 1):
            sid = "6" + f"{k:021d}"
            ids[nm] = sid
            ok(sess.run("slab.create", {"id": sid, "level": ids[lv], "boundary": b, "thickness": 200.0, "top_offset": off, "name": nm}))
            log["slabs"].append({"name": nm, "id": sid, "level": lv, "top_offset": off})
        after = measure()
        cuts = {k: sum(len(sess.eng.g.q(D.slab_cut_rects, probe_views[k], sl["id"])) for sl in log["slabs"]) for k in probe_views}
        log["slab_probe"] = {"slabs": [sl["name"] for sl in log["slabs"]],
                             "views": {k: {"segments_before": before[k]["segments"], "segments_after": after[k]["segments"], "fills_before": before[k]["fills"],
                                           "fills_after": after[k]["fills"], "slab_cut_rects": cuts[k]} for k in probe_views}}

    # ---- 네 번째 작업 입력: 계단·공간·방화 등급·외벽 재료. 각 묶음을 더하기 전후의 뷰 선분·채움·문자를 재서 판정기의 근거로 쓴다

    def measure2():
        sess.eng.sync()
        sess.eng._touch_pending()
        out_ = {}
        for k, v in probe_views.items():
            ln = sess.eng.g.q(D.view_lines, v)
            lb = sess.eng.g.q(D.view_labels, v)
            by = {}
            for x in ln:
                by[x[0]] = by.get(x[0], 0) + 1
            out_[k] = {"segments": len(ln), "fills": len(sess.eng.g.q(D.view_fills, v)), "by_class": by, "labels": [t[2] for t in lb]}
        return out_

    def probe(name, extra):
        log[name] = {"views": {k: {"before": b4[k], "after": af[k], **extra.get(k, {})} for k in probe_views}, **extra.get("_", {})}

    if not o.no_stairs:
        # 계단 단면 뷰(걸음선 위 y=5000)는 계단을 더할 때만 만든다(자동 id 가 쓰는 순번이 바뀌므로 --no-stairs 입력은 이전 산출과 바이트 같게 둔다)
        vc1 = ok(sess.run("view.create", {"id": "4" + "0" * 20 + "8", "name": "C-C 계단 단면", "type": "section", "p1": [-1000.0, 5000.0], "p2": [13000.0, 5000.0]}))
        probe_views["section-CC"] = vc1["id"]
        b4 = measure2()
        sid = "7" + f"{1:021d}"
        ids["ST-1F"] = sid
        ok(sess.run("stair.create", {"id": sid, "level": ids["1F"], "run": [5000.0, 5000.0, 9400.0, 5000.0], "width": 1200.0, "height": s3lib.STOREY,
                                     "risers": 19, "name": "ST-1F"}))
        af = measure2()
        probe("stair_probe", {k: {"stair_cut_rects": len(sess.eng.g.q(D.stair_cut_rects, probe_views[k], sid))} for k in probe_views} | {"_": {"stairs": ["ST-1F"]}})
    if not o.no_spaces:
        b4 = measure2()
        hi = s3lib.EXT_T / 2
        inner = s3lib.INT_T / 2
        lx, ly = s3lib.LX, s3lib.LY
        rooms = (("거실", "거실", "1F", [[hi, hi], [2700.0, hi], [2700.0, ly - hi], [hi, ly - hi]]),
                 ("복도", "복도", "1F", [[2700.0, hi], [4000.0 - inner, hi], [4000.0 - inner, ly - hi], [2700.0, ly - hi]]),
                 ("침실1", "침실", "1F", [[4000.0 + inner, hi], [8000.0 - inner, hi], [8000.0 - inner, 4000.0 - inner], [4000.0 + inner, 4000.0 - inner]]),
                 ("침실2", "침실", "1F", [[8000.0 + inner, hi], [lx - hi, hi], [lx - hi, 4000.0 - inner], [8000.0 + inner, 4000.0 - inner]]),
                 ("주방", "주방", "1F", [[4000.0 + inner, 4000.0 + inner], [lx - hi, 4000.0 + inner], [lx - hi, ly - hi], [4000.0 + inner, ly - hi]]),
                 ("침실3", "침실", "2F", [[hi, hi], [lx - hi, hi], [lx - hi, ly - hi], [hi, ly - hi]]))
        log["spaces"] = []
        for k, (nm, cat, lv, b) in enumerate(rooms, 1):
            spid = "8" + f"{k:021d}"
            ids[f"SP-{nm}"] = spid
            ok(sess.run("space.create", {"id": spid, "level": ids[lv], "boundary": b, "name": nm, "category": cat, "height": 2700.0}))
            log["spaces"].append({"name": nm, "category": cat, "level": lv, "id": spid})
        af = measure2()
        probe("space_probe", {"_": {"spaces": [r_["name"] for r_ in log["spaces"]]}})
    if not o.no_fire:
        b4 = measure2()
        ok(sess.run("pset.set", {"id": ids["int-1F"][0], "pset": "Pset_WallCommon", "prop": "FireRating", "value": "EI60"}))
        ok(sess.run("pset.set", {"id": ids["D-02"], "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI60"}))
        ok(sess.run("pset.set", {"id": ids["D-01"], "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI30"}))
        af = measure2()
        probe("fire_probe", {"_": {"fire_walls": 1, "fire_doors": 2}})
    if not o.no_finish:
        b4 = measure2()
        for lv, mat in (("1F", "치장벽돌"), ("2F", "스터코")):
            for w in ids[f"ext-{lv}"]:
                ok(sess.run("pset.set", {"id": w, "pset": "Pset_WallCommon", "prop": "IsExternal", "value": True}))
                ok(sess.run("material.set", {"id": w, "name": mat}))
        af = measure2()
        probe("finish_probe", {"_": {"external_walls": 8, "materials": ["치장벽돌", "스터코"]}})

    def exp_views(sub, keys):
        d = out / sub
        d.mkdir(exist_ok=True)
        for k in keys:
            for ext in ("svg", "pdf"):
                # 워크스페이스는 work/ 이므로 상대 경로가 아니라 한 단계 위로 쓸 수 없다 -> 작업 폴더에 쓰고 옮긴다
                r = export(sess, "view.export", {"id": views[k]}, work / f"{sub}_{k}.{ext}")
                (d / f"{k}.{ext}").write_bytes((work / f"{sub}_{k}.{ext}").read_bytes())
                log["exports"].append({"dir": sub, "view": k, "ext": ext, **{x: r[x] for x in ("bytes", "segments", "fills", "annotations", "non_ascii_dropped")}})

    keys = list(views)
    exp_views("base", keys)
    # 주석: 평면 치수 3개·태그 4개, 단면·입면 높이 치수
    w1, w2 = ids["ext-1F"][0], ids["ext-1F"][1]
    dim = lambda view, a, b, **kw: ok(sess.run("annot.dim.create", {"view": views[view], "a": a, "b": b, **kw}))   # noqa: E731
    manual_heights = []
    dim("plan-1F", f"wall:{w1}#trim.start", f"wall:{w1}#trim.end")
    dim("plan-1F", f"wall:{w2}#trim.start", f"wall:{w2}#trim.end", offset=900.0)
    for k in ("section-BB", "elev-S"):
        manual_heights.append(dim(k, f"level:{ids['1F']}#elevation", f"level:{ids['2F']}#elevation", at=[-600.0, 0.0])["id"])
    for t in (ids["D-01"], ids["D-02"], ids["D-03"], ids["ext-1F"][0]):
        ok(sess.run("annot.tag.create", {"view": views["plan-1F"], "target": t, "fields": ["name"]}))
    exp_views("annot", keys)
    # 패밀리 배치(창 2·기둥 1)와 종단면 시도: 실패해도 기록만 한다
    for i, (host, off) in enumerate(((ids["ext-1F"][1], 2000.0), (ids["ext-1F"][2], 4000.0)), 1):
        r = sess.run("family.place", {"id": "0" * 20 + f"w{i}", "family": "window.fixed", "host": host, "offset": off})
        log.setdefault("family", []).append({"window": i, "ok": bool(r.get("ok")), "err": (r.get("error") or {}).get("message")})
    r = sess.run("family.place", {"id": "0" * 20 + "c1", "family": "column.section", "type_values": {"section": "round"}, "level": ids["1F"], "position": [4000.0, 4000.0]})
    log["family"].append({"column": 1, "ok": bool(r.get("ok")), "err": (r.get("error") or {}).get("message")})
    exp_views("fam", ["plan-1F", "section-BB"])
    views["xsec-AA"] = vc0["id"]
    exp_views("xsec", ["xsec-AA"])
    if not o.no_stairs:
        views["section-CC"] = vc1["id"]
        exp_views("stair", ["section-CC"])
        del views["section-CC"]

    plans = [views["plan-1F"], views["plan-2F"]]
    elevs = [views[f"elev-{x}"] for x in "NESW"]
    spec = {"project_name": "RA-01 예시 주택", "date": "2026-11-15"}
    sheets_dir = out / "sheets"
    if o.sheets:
        sheets_dir.mkdir(exist_ok=True)

    saved: dict = {}

    def sheet(nm, a, extra=None):
        if nm != "A-201":   # 혼합 축척 시트는 뷰 축척을 잠깐 바꿔 뽑으므로 정의로 다시 내면 달라진다: 저장 대상이 아니다
            saved[nm] = {**spec, **a}
        for ext in ("svg", "pdf"):
            r = export(sess, "sheet.export", {**spec, **a}, work / f"{nm}.{ext}")
            (sheets_dir / f"{nm}.{ext}").write_bytes((work / f"{nm}.{ext}").read_bytes())
            log["exports"].append({"dir": "sheets", "sheet": nm, "ext": ext, "bytes": r["bytes"], "font_embedded": r["font_embedded"],
                                   "non_ascii_dropped": r["non_ascii_dropped"], "views": len(r["views"]), "number_check": r.get("number_check"), "number": r["sheet_number"]})

    if o.sheets:
        sheet("A-101", {"views": plans, "paper": "A3", "sheet_name": "1·2층 평면도", "sheet_number": "A-101"})
        # 혼합 축척 시트: 북측 입면만 1:50 으로 바꿔 한 장에 올린다(표제란 '도면참조', 뷰별 S=1:N). 높이 치수가 붙기 전에 뽑고 축척은 되돌린다
        ok(sess.run("view.set", {"id": views["elev-N"], "scale": 50}))
        sheet("A-201", {"views": [views["plan-1F"], views["elev-N"]], "paper": "A3", "sheet_name": "혼합 축척 예", "sheet_number": "A-201", "north_angle": 30.0})
        ok(sess.run("view.set", {"id": views["elev-N"], "scale": 100}))

    if not o.no_heights:
        # 높이 치수 자동 배치: 손으로 만든 층 사이 치수(위 annot/ 용)는 자동 치수가 대신하므로 지운다
        for d_id in manual_heights:
            ok(sess.run("annot.delete", {"id": d_id}))
        d = out / "heights"
        d.mkdir(exist_ok=True)
        hv = ["section-BB", "xsec-AA", "elev-N", "elev-E", "elev-S", "elev-W"]
        res = {k: ok(sess.run("annot.dim.heights", {"view": views[k]})) for k in hv}
        exp_views("heights", hv)
        dims = res["section-BB"]["dims"]
        by = lambda kind: [x["value"] for x in dims if x["kind"] == kind]   # noqa: E731
        log["heights"] = {"views": hv, "dims": {k: [{x: y[x] for x in ("kind", "name", "value")} for y in r["dims"]] for k, r in res.items()},
                          "expected": {"floor": by("floor"), "clear": by("clear"), "overall": (by("overall") or [None])[0]}}

    if o.sheets:
        sheet("A-001", {"views": plans + [views["section-BB"], views["xsec-AA"]] + elevs, "paper": "A1", "sheet_name": "평면도·단면도·입면도", "sheet_number": "A-001"})
    if o.sheets and not o.no_sheet2:
        saved.update(sheet2(sess, ids, views, log, out, work, sheets_dir, exp_views, sheet, spec))
        if not o.no_sheetdef:
            sheetdef(sess, saved, log, out, work, sheets_dir)
            cornerprobe(sess, log, work)
    (out / "run.json").write_text(json.dumps({"views": views, "ids": ids, **log}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
