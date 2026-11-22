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
  --no-after7   : 일곱 번째 작업(작성기준 프로파일·1:50 상세·시트 치수 A-004·A-005)을 건너뛴다
  --no-after8   : 여덟 번째 작업(대지·배치도 A-006, 숨은 선·선 굵기 위계·제3각법 A-007, 도면목록 A-000b)을 통째로 건너뛴다
  --no-site     : 여덟 번째 작업 중 대지 입력과 배치도(A-006·도면목록의 배치도 행)만 뺀다(숨은 선·위계·제3각법은 그대로)
  --no-after9   : 아홉 번째 작업(벽·슬래브 층 구성, 문짝 두께, 천장 높이, 1:50 단면·입면 상세, 비내력벽 절단선 A-008~A-010)을 건너뛴다
  --no-after11  : 열한 번째 작업(승강기·지붕 입력과 승강기 표시 평면)을 건너뛴다
  --no-after12  : 열두 번째 작업(대지 단면·조경·주차·간판 입력과 산출)을 건너뛴다
  --no-after13  : 열세 번째 작업(계단·승강기 상세 뷰, 승강기 평면 치수, 문·창을 지나는 단면, 방위 기본값)을 건너뛴다
  --no-after14  : 열네 번째 작업(경사 지붕·파라펫·승강기 피트/기계실/승강장 문/슬래브 구멍 연동, A-018·A-019)을 건너뛴다
  --no-after15  : 열다섯 번째 작업(건물 위의 박공 지붕·섬 구멍 공간·난간 연동·슬래브 구멍 가림, A-020)을 건너뛴다
  --no-png      : 열여섯 번째 작업(시트 몇 장의 PNG: png/A-001·A-006·A-007·A-010·A-018·A-020)을 건너뛴다(--sheets 일 때만 만든다)
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
from dobim_core import globalid as GI  # noqa: E402


def ok(r):
    assert r.get("ok"), r
    return r["result"]


PNG_SHEETS = ("A-001", "A-006", "A-007", "A-010", "A-018", "A-020")   # 열여섯 번째 작업: 이 시트는 SVG·PDF 와 함께 PNG 도 낸다(dobim_render 가 눈으로 볼 수 있게)
PNG_STATE: dict = {"dir": None, "done": set(), "log": []}


def _export(sess, name, a):
    r = sess.run(name, a)
    if r.get("ok"):   # 덮어쓸 파일이 없으면 확인 절차 없이 쓴다
        return r["result"]
    tok = r["error"]["details"]["confirm_token"]
    return ok(sess.run(name, a, confirm=tok))


def export(sess, name, args, out_path: Path):
    """확인 토큰 2단계 호출로 내보낸다. 응답 result 를 돌려준다."""
    a = {**args, "path": out_path.name, "overwrite": True}
    res = _export(sess, name, a)
    num = args.get("sheet_number")
    if PNG_STATE["dir"] is not None and name == "sheet.export" and out_path.suffix == ".svg" and num in PNG_SHEETS and num not in PNG_STATE["done"]:
        import time
        PNG_STATE["done"].add(num)
        pa = {**args, "path": out_path.stem + ".png", "overwrite": True, "max_px": 2400}
        t = time.perf_counter()
        pr = _export(sess, name, pa)
        dt = time.perf_counter() - t
        (PNG_STATE["dir"] / f"{num}.png").write_bytes((out_path.parent / pa["path"]).read_bytes())
        PNG_STATE["log"].append({"sheet": num, "paper": args.get("paper"), "pixels": pr["pixels"], "bytes": pr["bytes"], "seconds": round(dt, 2)})
    return res


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
    f = GI.open_exported(work / "corner.ifc")   # 첫 글자가 무효한 id 는 내보낸 파일에서 바뀌어 있다(W2): 원래 id 로 되돌려 찾는다
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


def after7(sess, ids, views, log, out, work, spec):
    """일곱 번째 작업: 작성기준 프로파일(문자 높이·치수 형식·기호 크기, B-09), 1:50 평면 상세(SC-09·B-17), 시트 치수(입면 E-04·복도/계단 폭 L-09).
    산출은 sheets7/ 에 둔다(기존 sheets/ 의 도면목록·번호 검사와 따로). 같은 인자에서 새 입력만 뺀 기본 시트를 함께 내어 차이를 재고, run.json 의 after7 로 남긴다."""
    sd = out / "sheets7"
    sd.mkdir(exist_ok=True)
    v50 = ok(sess.run("view.create", {"id": "4" + "0" * 19 + "a1", "name": "1층 평면도 1:50", "type": "plan", "level": ids["1F"], "scale": 50}))["id"]
    w1, w2 = ids["ext-1F"][0], ids["ext-1F"][1]
    for a, b, kw in ((f"wall:{w1}#trim.start", f"wall:{w1}#trim.end", {}), (f"wall:{w2}#trim.start", f"wall:{w2}#trim.end", {"offset": 900.0})):
        ok(sess.run("annot.dim.create", {"view": v50, "a": a, "b": b, **kw}))
    lx, ly = s3lib.LX, s3lib.LY
    grids = [{"label": f"X{i}", "axis": "x", "pos": x} for i, x in enumerate((0.0, 4000.0, 8000.0, lx), 1)] + [{"label": f"Y{i}", "axis": "y", "pos": y} for i, y in enumerate((0.0, 4000.0, ly), 1)]
    prof = {"text_height": {"dimension": 3.0, "room_name": 3.0, "room_area": 2.4, "caption": 5.0, "tag": 2.2}, "dim_thousands": True, "symbol_scale": 1.2}
    base_a = {**spec, "paper": "A1", "tags": ["room", "opening"], "grids": grids}
    new_a = {"detail": True, "profile": prof, "dims": ["corridor", "stair"]}
    probe = {"profile": prof, "view": v50, "sheets": {}}

    def one(nm, args, exts=("svg", "pdf")):
        for ext in exts:
            r = export(sess, "sheet.export", args, work / f"{nm}.{ext}")
            (sd / f"{nm}.{ext}").write_bytes((work / f"{nm}.{ext}").read_bytes())
            probe.setdefault("exports", []).append({"dir": "sheets7", "sheet": nm, "ext": ext, "bytes": r["bytes"], "font_embedded": r["font_embedded"], "non_ascii_dropped": r["non_ascii_dropped"],
                                   "views": len(r["views"]), "number_check": r["number_check"], "number": r["sheet_number"]})

    a4 = {**base_a, "views": [v50], "sheet_name": "1층 평면도 1:50(상세·작성기준·폭 치수)", "sheet_number": "A-004", **new_a}
    one("A-004", a4)
    one("A-004-default", {**base_a, "views": [v50], "sheet_name": "1층 평면도 1:50(기본 표현)", "sheet_number": "A-004"}, ("svg",))
    # 새 입력을 주지 않은 같은 호출: 빈 값을 줘도 기본과 바이트가 같다(바이트 불변 고정)
    one("A-004-empty", {**base_a, "views": [v50], "sheet_name": "1층 평면도 1:50(기본 표현)", "sheet_number": "A-004", "detail": False, "dims": [], "profile": {}}, ("svg",))
    probe["empty_args_equal_default"] = (sd / "A-004-empty.svg").read_bytes() == (sd / "A-004-default.svg").read_bytes()
    elev = [views["elev-S"], views["elev-E"]]
    a5 = {**spec, "views": elev, "paper": "A1", "sheet_name": "입면도(벽 중심선 치수)", "sheet_number": "A-005", "dims": ["elevation"], "profile": {"dim_thousands": True, "text_height": {"dimension": 3.0}}}
    one("A-005", a5)
    one("A-005-default", {**spec, "views": elev, "paper": "A1", "sheet_name": "입면도(벽 중심선 치수)", "sheet_number": "A-005"}, ("svg",))
    # 모델에서 뽑은 값(시트가 그리는 치수): 재현·판정용
    from dobim_core import sheet_model as SM
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    probe["plan_dims"] = {k: [list(x) for x in v] for k, v in SM.plan_dims(g, v50, {"corridor", "stair"}).items()}
    probe["elev_dims"] = {k: {"chain": [list(x) for x in (d or {}).get("chain", [])], "overall": list((d or {}).get("overall") or [])} for k, d in
                          ((k, SM.elev_dims(g, views[k])) for k in ("elev-N", "elev-E", "elev-S", "elev-W"))}
    probe["detail_lines"] = {"before": len(sess.eng.g.q(D.view_lines, v50)), "added": len(sess.eng.g.q(D.view_detail_lines, v50))}
    eff = ok(sess.run("drawing.profile", {"profile": prof}))
    (out / "profile-applied.json").write_text(json.dumps(eff, ensure_ascii=False, indent=1), encoding="utf-8")
    log["after7"] = probe


def after8(sess, ids, views, log, out, work, spec, site=True):
    """여덟 번째 작업: 대지·배치도(B-05, 법정 P-01·P-02·P-04), 숨은 선·선 굵기 위계·제3각법(LN-03·LN-04·T-11).
    산출은 sheets8/ 와 site/ 에 둔다. 같은 인자에서 새 입력(profile 의 hidden_lines·line_hierarchy·projection)만 뺀 기본 시트를 함께 내어 차이를 재고,
    run.json 의 after8 로 남긴다. 대지·배치도 뷰는 모든 기존 산출을 뽑은 뒤에 더하므로 기존 파일은 바이트가 그대로다."""
    from math import sqrt

    from dobim_core import dispatch
    sd = out / "sheets8"
    sd.mkdir(exist_ok=True)
    probe: dict = {"sheets": {}, "exports": []}
    lx, ly = s3lib.LX, s3lib.LY
    new_prof = {"hidden_lines": True, "line_hierarchy": True, "projection": "third"}

    def one(nm, args, exts=("svg", "pdf"), cmd="sheet.export"):
        for ext in exts:
            r = export(sess, cmd, args, work / f"{nm}.{ext}")
            (sd / f"{nm}.{ext}").write_bytes((work / f"{nm}.{ext}").read_bytes())
            probe["exports"].append({"dir": "sheets8", "sheet": nm, "ext": ext, "bytes": r["bytes"], "font_embedded": r["font_embedded"], "non_ascii_dropped": r["non_ascii_dropped"],
                                     "number_check": r.get("number_check"), "number": r.get("sheet_number")})

    north = 15.0
    if site:
        lot = [[-6000.0, -8000.0], [lx + 6000.0, -8000.0], [lx + 6000.0, ly + 5000.0], [-6000.0, ly + 5000.0]]
        roads = [{"name": "남측 도로", "edge": 0, "width": 8000.0}, {"name": "동측 도로", "edge": 1, "width": 6000.0}]
        sid = "a" + f"{1:021d}"
        ids["SITE"] = sid
        ok(sess.run("site.create", {"id": sid, "boundary": lot, "name": "RA-01 대지", "north_angle": north, "roads": roads, "elevation": 12500.0}))
        vsite = ok(sess.run("view.create", {"id": "4" + "0" * 19 + "b1", "name": "배치도", "type": "site", "scale": 200}))["id"]
        sess.eng.sync()
        sess.eng._touch_pending()
        g = sess.eng.g
        sb = [{"edge": i, "mm": round(d, 3), "foot": [round(x, 3) for x in foot], "vertex": [round(x, 3) for x in vert]} for i, d, foot, vert in g.q(D.site_setbacks, sid)]
        edges = []
        for i in range(len(lot)):
            a, b = lot[i], lot[(i + 1) % len(lot)]
            edges.append(round(sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2), 3))
        probe["site"] = {"id": sid, "view": vsite, "boundary": lot, "north_angle": north, "elevation": 12500.0, "scale": 200,
                         "roads": [{"name": r["name"], "edge": r["edge"], "width": r["width"], "length": edges[r["edge"]]} for r in roads],
                         "setbacks": sb, "edge_lengths": edges, "area_m2": (lot[2][0] - lot[0][0]) * (lot[2][1] - lot[0][1]) / 1e6,
                         "lines": len(g.q(D.view_lines, vsite)), "labels": [t[2] for t in g.q(D.view_labels, vsite)], "dims": [d[5] for d in g.q(D.site_dims, vsite)]}
        sdir = out / "site"
        sdir.mkdir(exist_ok=True)
        for ext in ("svg", "pdf"):
            r = export(sess, "view.export", {"id": vsite}, work / f"site_view.{ext}")
            (sdir / f"배치도.{ext}").write_bytes((work / f"site_view.{ext}").read_bytes())
            probe["exports"].append({"dir": "site", "view": "배치도", "ext": ext, "bytes": r["bytes"], "segments": r["segments"], "labels": r["labels"], "annotations": r["annotations"]})
        # 배치도 시트: 1:200 배치도 한 장(A1). 투상법·선 굵기 위계는 새 입력(profile)이다
        a6 = {**spec, "views": [vsite], "paper": "A1", "sheet_name": "배치도", "sheet_number": "A-006", "north_angle": north, "dims": []}
        one("A-006", {**a6, "profile": {"projection": "third", "line_hierarchy": True}})
        one("A-006-default", a6, ("svg",))
        # 독립 검산: 내보낸 IFC 파일을 열어(엔진 코드와 독립) 대지 경계 폴리라인과 건물 형상 정점에서 변마다 이격을 다시 잰다
        import ifcopenshell
        import ifcopenshell.geom
        ifc_out(sess, "site.ifc")
        f = GI.open_exported(work / "site.ifc")   # 첫 글자가 무효한 id 는 내보낸 파일에서 바뀌어 있다(W2): 원래 id 로 되돌려 찾는다
        st_ = f.by_guid(sid)
        pts = [tuple(p.Coordinates) for p in st_.Representation.Representations[0].Items[0].Elements[0].Points][:-1]
        gs = ifcopenshell.geom.settings()
        gs.set("use-world-coords", True)
        verts = set()
        for cls in ("IfcWall", "IfcSlab"):
            for e in f.by_type(cls):
                v = ifcopenshell.geom.create_shape(gs, e).geometry.verts
                verts |= {(round(v[i] * 1000.0, 2), round(v[i + 1] * 1000.0, 2)) for i in range(0, len(v), 3)}
        ifc_sb = []
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            ln = sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
            ux, uy = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
            best = None
            for vx, vy in verts:
                t = (vx - a[0]) * ux + (vy - a[1]) * uy
                if 0.0 <= t <= ln:
                    d = abs((vx - a[0]) * -uy + (vy - a[1]) * ux)
                    best = d if best is None or d < best else best
            ifc_sb.append(None if best is None else round(best, 2))
        probe["ifc_check"] = {"site_name": st_.Name, "ref_elevation": st_.RefElevation, "boundary_points": [list(p) for p in pts], "setbacks_mm": ifc_sb,
                              "parent": f.by_guid(sid).Decomposes[0].RelatingObject.Name}
        # IFC 왕복: 가져와도 대지·배치도 뷰가 되살고 해시 3종이 같다
        h0 = ok(sess.run("model.hash", {}))
        imp = ok(dispatch.run(sess.env, "ifc.import", {"source": "site.ifc", "path": "site-imp.dobim"}))
        h1 = ok(dispatch.run(sess.env, "model.hash", {}, project="site-imp.dobim"))
        g1 = ok(dispatch.run(sess.env, "element.get", {"id": sid}, project="site-imp.dobim"))
        v1 = ok(dispatch.run(sess.env, "view.list", {"type": "site"}, project="site-imp.dobim"))
        probe["roundtrip"] = {"warnings": imp["warnings"], "skeleton": imp["skeleton"], "elements": imp["elements"], "hash_equal": [h0[k] == h1[k] for k in ("state", "derived", "ifc")],
                              "origin": g1["origin"], "kind": g1["kind"], "views": [i["id"] for i in v1["items"]]}
    # 입면 시트: 숨은 선·선 굵기 위계·제3각법(profile). 같은 인자의 기본 시트와 비교하고, 꺼 둔 값(false)을 줘도 기본과 바이트가 같다
    elev = [views["elev-S"], views["elev-N"]]
    a7 = {**spec, "views": elev, "paper": "A1", "sheet_name": "입면도(숨은 선·선 굵기 위계)", "sheet_number": "A-007", "dims": ["elevation"]}
    one("A-007", {**a7, "profile": new_prof})
    one("A-007-default", a7, ("svg",))
    one("A-007-empty", {**a7, "profile": {"hidden_lines": False, "line_hierarchy": False}}, ("svg",))
    probe["empty_args_equal_default"] = (sd / "A-007-empty.svg").read_bytes() == (sd / "A-007-default.svg").read_bytes()
    sess.eng.sync()
    sess.eng._touch_pending()
    probe["hidden"] = {k: len(sess.eng.g.q(D.view_hidden_lines, views[k])) for k in ("elev-S", "elev-N", "elev-E", "elev-W", "plan-1F", "section-BB")}
    # 도면목록(색인 시트): 배치도(A-006)와 입면 시트(A-007)를 적어 D-01 을 확인한다
    listing = [{"number": "A-000", "title": "도면목록·범례·일람표", "scale": "-"}, {"number": "A-001", "title": "평면도·단면도·입면도", "scale": "1:100"}]
    if site:
        listing.append({"number": "A-006", "title": "배치도", "scale": "1:200"})
    listing.append({"number": "A-007", "title": "입면도(숨은 선·선 굵기 위계)", "scale": "1:100"})
    probe["drawing_list"] = listing
    idx = {"paper": "A1", "sheet_name": "도면목록(배치도 포함)", "sheet_number": "A-000b", "drawings": listing, "tables": ["drawing_list", "levels"],
           "project_name": spec["project_name"], "date": spec["date"]}
    for ext in ("svg", "pdf"):
        r = export(sess, "sheet.index", idx, work / f"A-000b.{ext}")
        (sd / f"A-000b.{ext}").write_bytes((work / f"A-000b.{ext}").read_bytes())
        probe["exports"].append({"dir": "sheets8", "sheet": "A-000b", "ext": ext, "bytes": r["bytes"], "font_embedded": r["font_embedded"], "non_ascii_dropped": r["non_ascii_dropped"]})
    eff = ok(sess.run("drawing.profile", {"profile": new_prof}))
    (out / "profile-applied8.json").write_text(json.dumps(eff, ensure_ascii=False, indent=1), encoding="utf-8")
    log["after8"] = probe


def after9(sess, ids, views, log, out, work, spec):
    """아홉 번째 작업: 벽·슬래브 층 구성(wall.layers·slab.layers), 문짝 두께(door.set leaf), 공간 천장 높이(space.set ceiling_height), 비내력벽(LoadBearing)을 입력으로 더하고
    1:50 평면·단면·입면 상세(detail)·비내력벽 절단선(line_hierarchy)·천장 기준 반자높이 치수(annot.dim.heights)를 낸다. 산출은 sheets9/ 에 둔다.
    이 입력은 모든 기존 산출을 뽑은 뒤에 더하므로 기존 파일은 바이트가 그대로다. 같은 인자에서 새 입력(detail·profile)만 뺀 기본 시트를 함께 내고,
    run.json 의 after9 로 남긴다(판정기의 근거)."""
    from math import sqrt

    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element as ue
    from dobim_core import dispatch
    sd = out / "sheets9"
    sd.mkdir(exist_ok=True)
    probe: dict = {"exports": [], "sheets": {}}
    ly = s3lib.LY
    ext_ly = [("외장 치장벽돌", 50.0), ("단열재", 50.0), ("철근콘크리트", 200.0), ("내부 석고보드", 20.0)]     # 320 = 외벽 두께. 외벽 축선은 반시계라 첫 층이 바깥
    int_ly = [("석고보드", 20.0), ("경량철골 스터드·단열", 60.0), ("석고보드", 20.0)]                       # 100 = 칸막이벽 두께
    slab_ly = [("마감 모르타르", 50.0), ("철근콘크리트", 150.0)]                                          # 200 = 슬래브 두께, 윗면에서 아래로
    as_in = lambda ls: [{"material": m, "thickness": t} for m, t in ls]   # noqa: E731
    walls = {"ext": [w for lv in ("1F", "2F") for w in ids[f"ext-{lv}"]], "int": [w for lv in ("1F", "2F") for w in ids[f"int-{lv}"]]}
    for w in walls["ext"]:
        ok(sess.run("wall.layers", {"id": w, "layers": as_in(ext_ly)}))
        ok(sess.run("pset.set", {"id": w, "pset": "Pset_WallCommon", "prop": "LoadBearing", "value": True}))
    for w in walls["int"]:
        ok(sess.run("wall.layers", {"id": w, "layers": as_in(int_ly)}))
        ok(sess.run("pset.set", {"id": w, "pset": "Pset_WallCommon", "prop": "LoadBearing", "value": False}))
    slabs = [ids[k] for k in ("S-GND", "S-2F", "S-ROOF") if k in ids]
    for sid in slabs:
        ok(sess.run("slab.layers", {"id": sid, "layers": as_in(slab_ly)}))
    ok(sess.run("door.set", {"id": ids["D-01"], "leaf": 45.0}))
    spaces = [ids[k] for k in ids if k.startswith("SP-")]
    for sp in spaces:
        ok(sess.run("space.set", {"id": sp, "ceiling_height": 2400.0}))
    probe["inputs"] = {"ext_walls": len(walls["ext"]), "int_walls": len(walls["int"]), "ext_layers": ext_ly, "int_layers": int_ly, "slabs": len(slabs), "slab_layers": slab_ly,
                       "door_leaf": {"D-01": 45.0}, "spaces_with_ceiling": len(spaces), "ceiling_height": 2400.0, "space_height": 2700.0,
                       "load_bearing": {"true": len(walls["ext"]), "false": len(walls["int"])}}
    # ---- 뷰(1:50): 1층 평면, 단면(x=2000 을 지나는 남북 종단: 남측 외벽·거실·슬래브를 자른다), 동·북측 입면(창이 있다)
    vid = lambda k: "4" + "0" * 19 + k   # noqa: E731
    v_plan = ok(sess.run("view.create", {"id": vid("c1"), "name": "1층 평면도 1:50(층 구성)", "type": "plan", "level": ids["1F"], "scale": 50}))["id"]
    v_sec = ok(sess.run("view.create", {"id": vid("c2"), "name": "C-C 종단면 1:50", "type": "section", "p1": [2000.0, -1000.0], "p2": [2000.0, ly + 1000.0], "scale": 50}))["id"]
    v_e = ok(sess.run("view.create", {"id": vid("c3"), "name": "동측 입면 1:50", "type": "elevation", "dir": "E", "scale": 50}))["id"]
    v_n = ok(sess.run("view.create", {"id": vid("c4"), "name": "북측 입면 1:50", "type": "elevation", "dir": "N", "scale": 50}))["id"]
    heights = ok(sess.run("annot.dim.heights", {"view": v_sec}))
    probe["views"] = {"plan": v_plan, "section": v_sec, "elev_e": v_e, "elev_n": v_n}
    probe["heights"] = {"dims": [{k: d[k] for k in ("kind", "name", "value", "a", "b")} for d in heights["dims"]]}

    def one(nm, args, exts=("svg", "pdf")):
        for ext in exts:
            r = export(sess, "sheet.export", args, work / f"{nm}.{ext}")
            (sd / f"{nm}.{ext}").write_bytes((work / f"{nm}.{ext}").read_bytes())
            probe["exports"].append({"dir": "sheets9", "sheet": nm, "ext": ext, "bytes": r["bytes"], "font_embedded": r["font_embedded"], "non_ascii_dropped": r["non_ascii_dropped"],
                                     "number_check": r.get("number_check"), "number": r.get("sheet_number")})

    new_a = {"detail": True, "profile": {"line_hierarchy": True}}
    sheets = (("A-008", v_plan, "1층 평면도 1:50(층 구성·문짝 두께·비내력벽)", ["room", "opening"]), ("A-009", v_sec, "C-C 종단면 1:50(벽·슬래브 층·천장선)", []))
    for nm, v, title, tags in sheets:
        base_a = {**spec, "views": [v], "paper": "A1", "sheet_name": title, "sheet_number": nm, "tags": tags}
        one(nm, {**base_a, **new_a})
        one(f"{nm}-default", base_a, ("svg",))
        one(f"{nm}-empty", {**base_a, "detail": False, "profile": {"line_hierarchy": False}}, ("svg",))
        probe["sheets"][nm] = {"view": v, "empty_args_equal_default": (sd / f"{nm}-empty.svg").read_bytes() == (sd / f"{nm}-default.svg").read_bytes()}
    base_e = {**spec, "views": [v_e, v_n], "paper": "A1", "sheet_name": "동·북측 입면도 1:50(창대)", "sheet_number": "A-010"}
    one("A-010", {**base_e, "detail": True})
    one("A-010-default", base_e, ("svg",))
    one("A-010-empty", {**base_e, "detail": False}, ("svg",))
    probe["sheets"]["A-010"] = {"views": [v_e, v_n], "empty_args_equal_default": (sd / "A-010-empty.svg").read_bytes() == (sd / "A-010-default.svg").read_bytes()}
    # ---- 뷰별 상세 선·절단선 수(재현·판정용)
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    by = lambda ls: {c: sum(1 for x in ls if x[0] == c) for c in sorted({x[0] for x in ls})}   # noqa: E731
    probe["detail_lines"] = {k: {"before": len(g.q(D.view_lines, v)), "added": by(g.q(D.view_detail_lines, v))} for k, v in
                             (("plan", v_plan), ("section", v_sec), ("elev_e", v_e), ("elev_n", v_n))}
    probe["cut_classes"] = {k: {"hier": by(g.q(D.view_lines_hier, v)), "plain": by(g.q(D.view_lines, v))} for k, v in (("plan", v_plan), ("section", v_sec))}
    probe["ceiling_lines"] = [list(x) for sp in spaces for x in g.q(D.space_ceiling_lines, v_sec, sp)]
    # ---- 독립 검산: 내보낸 IFC 를 열어(엔진 코드와 독립) 층 구성 두께 합이 형상의 실제 두께와 같은지, 슬래브 층이 형상 두께와 같은지
    ifc_out(sess, "after9.ifc")
    f = GI.open_exported(work / "after9.ifc")   # 첫 글자가 무효한 id 는 내보낸 파일에서 바뀌어 있다(W2): 원래 id 로 되돌려 찾는다
    gs = ifcopenshell.geom.settings()
    gs.set("use-world-coords", True)
    rows = []
    for w in walls["ext"] + walls["int"]:
        x1, y1, x2, y2 = sess.eng.st.el[w]["params"]["axis"]
        ln = sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        c, s_ = (x2 - x1) / ln, (y2 - y1) / ln
        v = ifcopenshell.geom.create_shape(gs, f.by_guid(w)).geometry.verts
        ns = [(-(v[i] * 1000.0 - x1) * s_ + (v[i + 1] * 1000.0 - y1) * c) for i in range(0, len(v), 3)]
        u = ue.get_material(f.by_guid(w), should_inherit=False)
        lays = [(lr.Material.Name, lr.LayerThickness) for lr in u.ForLayerSet.MaterialLayers]
        rows.append({"wall": w, "geom_thickness": round(max(ns) - min(ns), 3), "layer_sum": sum(t for _, t in lays), "layers": [list(x) for x in lays],
                     "offset": u.OffsetFromReferenceLine, "first_layer_face": round(min(ns), 3), "usage": u.is_a()})
    probe["ifc_check"] = {"walls": rows, "wall_thickness_equal": all(abs(r["geom_thickness"] - r["layer_sum"]) < 0.01 for r in rows),
                          "offset_is_first_face": all(abs(r["offset"] - r["first_layer_face"]) < 0.01 for r in rows)}
    srows = []
    for sid in slabs:
        v = ifcopenshell.geom.create_shape(gs, f.by_guid(sid)).geometry.verts
        zs = [v[i + 2] * 1000.0 for i in range(0, len(v), 3)]
        u = ue.get_material(f.by_guid(sid), should_inherit=False)
        lays = [(lr.Material.Name, lr.LayerThickness) for lr in u.ForLayerSet.MaterialLayers]
        srows.append({"slab": sid, "geom_thickness": round(max(zs) - min(zs), 3), "layer_sum": sum(t for _, t in lays), "ifc_layers_bottom_to_top": [list(x) for x in lays]})
    probe["ifc_check"]["slabs"] = srows
    probe["ifc_check"]["slab_thickness_equal"] = all(abs(r["geom_thickness"] - r["layer_sum"]) < 0.01 for r in srows)
    probe["ifc_check"]["layer_sets"] = len(f.by_type("IfcMaterialLayerSet"))
    # ---- IFC 왕복: 가져와도 층 구성·문짝 두께·천장 높이가 되살고 해시 3종이 같다
    h0 = ok(sess.run("model.hash", {}))
    imp = ok(dispatch.run(sess.env, "ifc.import", {"source": "after9.ifc", "path": "after9-imp.dobim"}))
    h1 = ok(dispatch.run(sess.env, "model.hash", {}, project="after9-imp.dobim"))
    wg = ok(dispatch.run(sess.env, "element.get", {"id": walls["ext"][0]}, project="after9-imp.dobim"))
    dg = ok(dispatch.run(sess.env, "element.get", {"id": ids["D-01"]}, project="after9-imp.dobim"))
    sg = ok(dispatch.run(sess.env, "element.get", {"id": spaces[0]}, project="after9-imp.dobim"))
    probe["roundtrip"] = {"warnings": imp["warnings"], "elements": imp["elements"], "hash_equal": [h0[k] == h1[k] for k in ("state", "derived", "ifc")],
                          "wall_layers_back": wg["params"].get("layers") == as_in(ext_ly), "wall_origin": wg["origin"], "door_leaf_back": dg["params"].get("leaf"),
                          "ceiling_back": sg["params"].get("ceiling_height")}
    log["after9"] = probe


def after11(sess, ids, views, log, out, work, spec):
    """열한 번째 작업(승강기·지붕, 코어는 main 의 elevator·roof 종류): 승강기(1F 왼쪽 방 모서리, 1F->2F + 오버런 1500)와 슬래브 구멍, 지붕 슬래브(S-ROOF 를 roof 로, 물매·배수구)를
    입력으로 더하고 평면 1F·2F 에 승강기 표시(EV 문자·샤프트·카)를 낸다(L-05 승강기 위치). 산출은 sheets11/ 에 둔다.
    이 입력은 모든 기존 산출을 뽑은 뒤에 더하므로 기존 파일은 바이트가 그대로다."""
    from dobim_core import dispatch
    sd = out / "sheets11"
    sd.mkdir(exist_ok=True)
    probe: dict = {"exports": []}
    eid = "d" + f"{1:021d}"
    ids["EV-1"] = eid
    ok(sess.run("elevator.create", {"id": eid, "level": ids["1F"], "shaft": [600.0, 5600.0, 2400.0, 7400.0], "to_level": ids["2F"], "overrun": 1500.0, "name": "EV-1"}))
    cut = ok(sess.run("elevator.cut_slabs", {"id": eid}))
    if "S-ROOF" in ids:
        ok(sess.run("slab.set", {"id": ids["S-ROOF"], "use": "roof", "fall": {"dir": 90.0, "pct": 2.0}, "drains": [[6000.0, 4000.0]]}))
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    probe["elevator"] = {"id": eid, "shaft": [600.0, 5600.0, 2400.0, 7400.0], "cut_slabs": cut["cut"], "skipped": cut["skipped"]}
    for nm, vk in (("plan-1F", "plan-1F"), ("plan-2F", "plan-2F")):
        v = views[vk]
        labels = [t[2] for t in g.q(D.view_labels, v)]
        r = None
        for ext in ("svg", "pdf"):
            r = export(sess, "view.export", {"id": v}, work / f"ev_{nm}.{ext}")
            (sd / f"{nm}.{ext}").write_bytes((work / f"ev_{nm}.{ext}").read_bytes())
            probe["exports"].append({"dir": "sheets11", "view": nm, "ext": ext, "bytes": r["bytes"], "segments": r["segments"], "labels": r["labels"]})
        probe[nm] = {"view": v, "ev_labels": labels.count("EV"), "lines": len(g.q(D.view_lines, v))}
    ifc_out(sess, "after11.ifc")
    import ifcopenshell
    f = GI.open_exported(work / "after11.ifc")   # 첫 글자가 무효한 id 는 내보낸 파일에서 바뀌어 있다(W2): 원래 id 로 되돌려 찾는다
    probe["ifc_check"] = {"transport": [(e.is_a(), e.PredefinedType) for e in f.by_type("IfcTransportElement")], "roof_slabs": [e.GlobalId for e in f.by_type("IfcSlab") if e.PredefinedType == "ROOF"]}
    h0 = ok(sess.run("model.hash", {}))
    imp = ok(dispatch.run(sess.env, "ifc.import", {"source": "after11.ifc", "path": "after11-imp.dobim"}))
    h1 = ok(dispatch.run(sess.env, "model.hash", {}, project="after11-imp.dobim"))
    probe["roundtrip"] = {"warnings": imp["warnings"], "hash_equal": [h0[k] == h1[k] for k in ("state", "derived", "ifc")]}
    log["after11"] = probe


def after12(sess, ids, views, log, out, work, spec):
    """열두 번째 작업: 대지의 선택 속성(지반 표고점·조경/공개공지 영역·주차 계획·간판)을 입력으로 더하고 배치도(P-05·P-06)·입면(E-03)·대지 단면 종·횡(P-03)을 낸다.
    산출은 sheets12/ 에 둔다. 모든 기존 산출을 뽑은 뒤에 더하므로 기존 파일은 바이트가 그대로다. 독립 검산: 내보낸 IFC 의 DoBIM_Intent 를 읽어(엔진 코드와 독립)
    영역 면적을 신발끈 공식으로 다시 재고, 대지 단면 지반선의 높이를 거리 제곱 역가중 평균으로 다시 계산해 엔진 값과 맞춘다. 같은 모델에서 새 입력을 지우면 뷰가 이전과 같다."""
    import ifcopenshell

    from dobim_core import dispatch
    sd = out / "sheets12"
    sd.mkdir(exist_ok=True)
    probe: dict = {"exports": []}
    sid = ids["SITE"]
    terrain = [[-6000.0, -8000.0, -800.0], [18000.0, -8000.0, -800.0], [18000.0, 13000.0, 600.0], [-6000.0, 13000.0, 600.0], [6000.0, 4000.0, -100.0]]
    zones = [{"use": "landscape", "name": "전정", "boundary": [[-5000.0, 6000.0], [-1000.0, 6000.0], [-1000.0, 12000.0], [-5000.0, 12000.0]]},
             {"use": "open_space", "name": "", "boundary": [[12500.0, 6500.0], [17500.0, 6500.0], [17500.0, 12500.0], [12500.0, 12500.0]]}]
    parking = {"bays": [{"origin": [0.0, -7800.0], "angle": 0.0, "count": 4, "w": 2500.0, "d": 5000.0},
                        {"origin": [10000.0, -7800.0], "angle": 0.0, "count": 1, "w": 3500.0, "d": 5000.0, "accessible": True}],
               "aisles": [{"path": [[-5000.0, -1800.0], [17000.0, -1800.0]], "width": 2000.0}],
               "routes": [{"path": [[17000.0, -8000.0], [17000.0, -1800.0], [0.0, -1800.0]]}]}
    signs = [{"name": "S1", "kind": "sign", "face": "S", "center": [3000.0, -160.0], "z": 2500.0, "w": 2400.0, "h": 700.0, "text": "RA-01 상가"},
             {"name": "N1", "kind": "address", "face": "N", "center": [3500.0, 8160.0], "z": 1500.0, "w": 400.0, "h": 250.0, "text": "12-3"}]   # 열네 번째 작업: 북측 창(x 7440~8600)의 모서리와 겹치던 번호판 문자를 창에서 뗐다(x 6000 -> 3500)
    vsite = ok(sess.run("view.list", {"type": "site"}))["items"][0]["id"]
    # 새 입력 전의 뷰 데이터(입력을 지운 뒤 되돌아오는지 보는 기준)
    sess.eng.sync()
    sess.eng._touch_pending()
    plain = {k: (len(sess.eng.g.q(D.view_lines, v)), len(sess.eng.g.q(D.view_labels, v))) for k, v in (("site", vsite), ("elev-S", views["elev-S"]), ("elev-N", views["elev-N"]))}
    h_before = ok(sess.run("model.hash", {}))
    ok(sess.run("site.set", {"id": sid, "terrain": terrain, "zones": zones, "parking": parking, "signs": signs}))
    vid = lambda k: "4" + "0" * 19 + k   # noqa: E731
    v_long = ok(sess.run("view.create", {"id": vid("d1"), "name": "대지 종단면", "type": "section", "p1": [3000.0, -9000.0], "p2": [3000.0, 14000.0], "scale": 200, "with_site": True}))["id"]
    v_trans = ok(sess.run("view.create", {"id": vid("d2"), "name": "대지 횡단면", "type": "section", "p1": [-7000.0, 3000.0], "p2": [19000.0, 3000.0], "scale": 200, "with_site": True}))["id"]
    pairs = (("site-plan", vsite), ("site-section-long", v_long), ("site-section-trans", v_trans), ("elev-S", views["elev-S"]), ("elev-N", views["elev-N"]))
    for nm, v in pairs:
        for ext in ("svg", "pdf"):
            r = export(sess, "view.export", {"id": v}, work / f"s12_{nm}.{ext}")
            (sd / f"{nm}.{ext}").write_bytes((work / f"s12_{nm}.{ext}").read_bytes())
            probe["exports"].append({"dir": "sheets12", "view": nm, "ext": ext, "bytes": r["bytes"], "segments": r["segments"], "labels": r["labels"]})
    for nm, ks, title in (("A-012", [vsite], "배치도(조경·공개공지·주차 동선)"), ("A-013", [v_long, v_trans], "대지 종·횡단면도")):
        a = {**spec, "views": ks, "paper": "A1", "sheet_name": title, "sheet_number": nm, "north_angle": 15.0, "dims": []}
        for ext in ("svg", "pdf"):
            r = export(sess, "sheet.export", a, work / f"{nm}.{ext}")
            (sd / f"{nm}.{ext}").write_bytes((work / f"{nm}.{ext}").read_bytes())
            probe["exports"].append({"dir": "sheets12", "sheet": nm, "ext": ext, "bytes": r["bytes"], "font_embedded": r["font_embedded"], "non_ascii_dropped": r["non_ascii_dropped"]})
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    probe["views"] = {"site": vsite, "long": v_long, "trans": v_trans, "elev_s": views["elev-S"], "elev_n": views["elev-N"]}
    probe["plain_before"] = plain
    probe["derived"] = ok(sess.run("element.get", {"id": sid}))["derived"]
    probe["inputs"] = {"zones": [{"use": z["use"], "name": z["name"], "area_m2": abs(sum(z["boundary"][i][0] * z["boundary"][(i + 1) % 4][1] - z["boundary"][(i + 1) % 4][0] * z["boundary"][i][1]
                                                                                       for i in range(4))) / 2e6} for z in zones],
                       "bays": [{"count": b["count"], "accessible": bool(b.get("accessible"))} for b in parking["bays"]], "signs": [{k: sg[k] for k in ("name", "kind", "face", "w", "h", "z")} for sg in signs],
                       "terrain_points": len(terrain)}
    # ---- 독립 검산 1: 내보낸 IFC 의 DoBIM_Intent 를 열어 영역 면적·주차 대수·간판 수를 다시 센다
    ifc_out(sess, "after12.ifc")
    f = GI.open_exported(work / "after12.ifc")   # 첫 글자가 무효한 id 는 내보낸 파일에서 바뀌어 있다(W2): 원래 id 로 되돌려 찾는다
    import ifcopenshell.util.element as ue
    it = json.loads(ue.get_pset(f.by_guid(sid), "DoBIM_Intent")["Intent"])
    ip = it["params"]
    shoelace = lambda b: abs(sum(b[i][0] * b[(i + 1) % len(b)][1] - b[(i + 1) % len(b)][0] * b[i][1] for i in range(len(b)))) / 2e6   # noqa: E731
    probe["ifc_check"] = {"zones_m2": [round(shoelace(z["boundary"]), 4) for z in ip["zones"]], "bays": sum(b["count"] for b in ip["parking"]["bays"]),
                          "accessible": sum(b["count"] for b in ip["parking"]["bays"] if b.get("accessible")), "signs": len(ip["signs"]), "terrain": len(ip["terrain"])}

    # ---- 독립 검산 2: 대지 단면 지반선의 높이를 다시 계산한다(절단선이 +y 방향이라 가로 s = y + 9000)
    def idw(x, y):
        num = den = 0.0
        for px, py, pz in terrain:
            d2 = (px - x) ** 2 + (py - y) ** 2
            if d2 < 1e-6:
                return pz
            num += pz / d2
            den += 1.0 / d2
        return num / den

    segs = g.q(D.site_section_lines, v_long)
    errs = [abs(z - idw(3000.0, s_ - 9000.0)) for c, s0, z0, s1, z1 in segs if c == "cut" for s_, z in ((s0, z0), (s1, z1))]
    probe["section_check"] = {"ground_points": len(errs), "max_abs_err_mm": max(errs) if errs else None, "gl": g.q(D.site_section_labels, v_long)[0][2]}
    # ---- IFC 왕복: 선택 속성과 대지 단면 뷰가 되살고 해시 3종이 같다
    h0 = ok(sess.run("model.hash", {}))
    imp = ok(dispatch.run(sess.env, "ifc.import", {"source": "after12.ifc", "path": "after12-imp.dobim"}))
    h1 = ok(dispatch.run(sess.env, "model.hash", {}, project="after12-imp.dobim"))
    sg = ok(dispatch.run(sess.env, "element.get", {"id": sid}, project="after12-imp.dobim"))
    vg = ok(dispatch.run(sess.env, "element.get", {"id": v_long}, project="after12-imp.dobim"))
    probe["roundtrip"] = {"warnings": imp["warnings"], "hash_equal": [h0[k] == h1[k] for k in ("state", "derived", "ifc")], "site_params_equal": sg["params"] == ok(sess.run("element.get", {"id": sid}))["params"],
                          "section_with_site": vg["params"].get("with_site")}
    # ---- 새 입력을 지우면 뷰가 입력 전과 같고 해시 3종이 되돌아온다(복원 시험: 같은 입력을 다시 넣어 이후 산출은 그대로 둔다)
    saved = ok(sess.run("element.get", {"id": sid}))["params"]
    ok(sess.run("site.set", {"id": sid, "clear": ["terrain", "zones", "parking", "signs"]}))
    ok(sess.run("view.delete", {"id": v_long}))
    ok(sess.run("view.delete", {"id": v_trans}))
    sess.eng.sync()
    sess.eng._touch_pending()
    after_clear = {k: (len(sess.eng.g.q(D.view_lines, v)), len(sess.eng.g.q(D.view_labels, v))) for k, v in (("site", vsite), ("elev-S", views["elev-S"]), ("elev-N", views["elev-N"]))}
    h2 = ok(sess.run("model.hash", {}))
    probe["clear_check"] = {"views_equal_plain": after_clear == plain, "hash_equal_before": [h2[k] == h_before[k] for k in ("state", "derived", "ifc")]}
    ok(sess.run("site.set", {"id": sid, "terrain": saved["terrain"], "zones": saved["zones"], "parking": saved["parking"], "signs": saved["signs"]}))
    ok(sess.run("view.create", {"id": v_long, "name": "대지 종단면", "type": "section", "p1": [3000.0, -9000.0], "p2": [3000.0, 14000.0], "scale": 200, "with_site": True}))
    ok(sess.run("view.create", {"id": v_trans, "name": "대지 횡단면", "type": "section", "p1": [-7000.0, 3000.0], "p2": [19000.0, 3000.0], "scale": 200, "with_site": True}))
    log["after12"] = probe


def after13(sess, ids, views, log, out, work, spec):
    """열세 번째 작업: 수직동선 상세(B-05)·승강기 치수(L-10)·도면 품질 개선(입면 마감 옵션·시트 방위 기본값·문/창을 지나는 단면). 산출은 sheets13/ 에 둔다.
    모든 기존 산출을 뽑은 뒤에 더한다(난간은 이 단계에서 계단 양옆에 더해 단면 난간 높이 치수를 만든다).
    - 계단 상세: 평면 1:20(crop = 계단 범위 + 여유, dims)·단면 1:20(걸음선을 지나는 절단선, 단높이·디딤·오름·난간 치수)
    - 승강기 상세: 1F·2F 평면 1:50(샤프트·카 치수)·단면 1:50(카 높이·오버헤드·승강로 높이)과 일반 1:100 1층 평면(dims 옵션)
    - 입면: 기본(해치 없음)과 finish 옵션(외부 마감 해치·재료 문자) 비교
    - 단면 D-D: 절단선이 문·창을 지난다(개구부가 보인다). 문·창 통과 여부는 모델의 벽 축선·개구부 범위에서 다시 센다
    독립 검산: 내보낸 IFC 의 DoBIM_Intent 에서 계단(걸음선·폭·챌판)·승강기(샤프트·카)를 다시 읽어 판정이 치수 문자를 직접 계산한다."""
    import ifcopenshell
    import ifcopenshell.util.element as ue

    from dobim_core import dispatch
    from dobim_core import queries as Q
    sd = out / "sheets13"
    sd.mkdir(exist_ok=True)
    probe: dict = {"exports": []}
    sid, eid = ids["ST-1F"], ids["EV-1"]
    vid = lambda k: "4" + "0" * 19 + k   # noqa: E731
    rails = ok(sess.run("railing.from_stair", {"stair": sid, "side": "both"}))
    probe["railings"] = rails
    mk = lambda k, a: ok(sess.run("view.create", {"id": vid(k), **a}))   # noqa: E731
    v = {}
    v["stair-plan"] = mk("g1", {"name": "계단 상세 평면", "type": "plan", "level": ids["1F"], "scale": 20, "around": sid, "margin": 800.0, "dims": True})
    v["stair-section"] = mk("g2", {"name": "계단 상세 단면", "type": "section", "p1": [4000.0, 5000.0], "p2": [10500.0, 5000.0], "scale": 20, "around": sid, "margin": 800.0, "dims": True})
    v["ev-plan-1F"] = mk("g3", {"name": "승강기 상세 1F 평면", "type": "plan", "level": ids["1F"], "scale": 50, "around": eid, "margin": 1500.0, "dims": True})
    v["ev-plan-2F"] = mk("g4", {"name": "승강기 상세 2F 평면", "type": "plan", "level": ids["2F"], "scale": 50, "around": eid, "margin": 1500.0, "dims": True})
    v["ev-section"] = mk("g5", {"name": "승강기 상세 단면", "type": "section", "p1": [-500.0, 6500.0], "p2": [3500.0, 6500.0], "scale": 50, "around": eid, "margin": 1500.0, "dims": True})
    v["plan-1F-dims"] = mk("g6", {"name": "1F 평면 승강기·계단 치수", "type": "plan", "level": ids["1F"], "scale": 100, "dims": True})
    v["section-DD"] = mk("g7", {"name": "D-D 단면(문·창 통과)", "type": "section", "p1": [8300.0, -1000.0], "p2": [8300.0, 9000.0], "scale": 100})
    v["elev-S-finish"] = mk("g8", {"name": "남측 입면 마감", "type": "elevation", "dir": "S", "finish": True})
    ids13 = {k: x["id"] for k, x in v.items()}
    probe["views"] = ids13
    probe["crops"] = {k: v[k]["crop"] for k in ("stair-plan", "stair-section", "ev-plan-1F", "ev-plan-2F", "ev-section")}
    pairs = list(ids13.items()) + [("elev-S", views["elev-S"]), ("plan-1F", views["plan-1F"])]
    for nm, vv in pairs:
        for ext in ("svg", "pdf"):
            r = export(sess, "view.export", {"id": vv}, work / f"s13_{nm}.{ext}")
            (sd / f"{nm}.{ext}").write_bytes((work / f"s13_{nm}.{ext}").read_bytes())
            probe["exports"].append({"dir": "sheets13", "view": nm, "ext": ext, "bytes": r["bytes"], "segments": r["segments"], "labels": r["labels"], "annotations": r["annotations"]})
    for nm, ks, title, paper, extra in (
            ("A-014", [ids13["stair-plan"], ids13["stair-section"]], "계단 상세도", "A1", {"north_angle": 15.0}),
            ("A-015", [ids13["ev-plan-1F"], ids13["ev-plan-2F"], ids13["ev-section"]], "승강기 상세도", "A1", {"north_angle": 15.0}),
            ("A-016", [ids13["plan-1F-dims"]], "1층 평면도(승강기·계단 치수)", "A1", {}),                  # 방위 각을 주지 않는다: 대지의 정북 각이 기본이다
            ("A-017", [ids13["section-DD"], views["section-BB"]], "단면도(문·창을 지나는 절단선)", "A3", {})):
        a = {**spec, "views": ks, "paper": paper, "sheet_name": title, "sheet_number": nm, "dims": [], **extra}
        for ext in ("svg", "pdf"):
            r = export(sess, "sheet.export", a, work / f"{nm}.{ext}")
            (sd / f"{nm}.{ext}").write_bytes((work / f"{nm}.{ext}").read_bytes())
            probe["exports"].append({"dir": "sheets13", "sheet": nm, "ext": ext, "bytes": r["bytes"], "font_embedded": r["font_embedded"], "non_ascii_dropped": r["non_ascii_dropped"]})
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    # ---- 엔진 값: 뷰마다 치수 문자, 입면 마감 비교
    probe["dims"] = {k: [d[5] for d in g.q(D.view_dims, x)] for k, x in ids13.items()}
    probe["finish"] = {k: {"hatch": sum(1 for ln in g.q(D.view_lines, vv) if ln[0] == "hatch"), "labels": [t[2] for t in g.q(D.view_labels, vv) if t[2] in ("치장벽돌", "스터코", "철근콘크리트")]}
                       for k, vv in (("elev-S", views["elev-S"]), ("elev-S-finish", ids13["elev-S-finish"]))}

    # ---- (c) 절단선이 지나는 문·창: 모델의 벽 축선과 개구부 범위에서 센다(그리기 코드와 독립)
    def crossing(x_cut, y_lo, y_hi):
        cr = {"door": 0, "window": 0}
        for w in g.q(D.all_walls):
            x, y, c, sn, length = g.q(Q.wall_frame, w)
            if abs(c) < 1e-9:
                continue
            u = (x_cut - x) / c
            if not (0.0 <= u <= length and y_lo <= y + sn * u <= y_hi):
                continue
            cr["door"] += sum(1 for op in g.q(D.wall_door_openings, w) if op[0] - 1e-6 <= u <= op[1] + 1e-6)
            cr["window"] += sum(1 for fm in g.q(D.hosted_fam, w) if fm[0] - 1e-6 <= u <= fm[1] + 1e-6)
        return cr
    probe["section_dd_openings"] = crossing(8300.0, -1000.0, 9000.0)
    probe["section_aa_openings"] = crossing(2000.0, -1000.0, 9000.0)
    # ---- 독립 검산: 내보낸 IFC 의 의도에서 계단·승강기 값을 다시 읽는다
    ifc_out(sess, "after13.ifc")
    f = GI.open_exported(work / "after13.ifc")   # 첫 글자가 무효한 id 는 내보낸 파일에서 바뀌어 있다(W2): 원래 id 로 되돌려 찾는다
    ist = json.loads(ue.get_pset(f.by_guid(sid), "DoBIM_Intent")["Intent"])["params"]
    iev = json.loads(ue.get_pset(f.by_guid(eid), "DoBIM_Intent")["Intent"])["params"]
    probe["ifc_intent"] = {"stair": {k: ist[k] for k in ("run", "width", "risers", "height")}, "elevator": {k: iev[k] for k in ("shaft", "cab", "base_offset", "rise")}}
    probe["ifc_check"] = {"railings": len(f.by_type("IfcRailing")), "stairs": len(f.by_type("IfcStair")), "transport": len(f.by_type("IfcTransportElement"))}
    probe["north"] = {"site": ok(sess.run("element.get", {"id": ids["SITE"]}))["params"]["north_angle"]}
    # ---- IFC 왕복: 옵션 뷰(crop·dims·finish)가 되살고 해시 3종이 같다
    h0 = ok(sess.run("model.hash", {}))
    imp = ok(dispatch.run(sess.env, "ifc.import", {"source": "after13.ifc", "path": "after13-imp.dobim"}))
    h1 = ok(dispatch.run(sess.env, "model.hash", {}, project="after13-imp.dobim"))
    same = {}
    for k, vv in ids13.items():
        a0 = ok(sess.run("element.get", {"id": vv}))["params"]
        a1 = ok(dispatch.run(sess.env, "element.get", {"id": vv}, project="after13-imp.dobim"))["params"]
        same[k] = a0 == a1
    probe["roundtrip"] = {"warnings": imp["warnings"], "hash_equal": [h0[k] == h1[k] for k in ("state", "derived", "ifc")], "view_params_equal": same}
    # ---- 옵션을 끄면 뷰가 옵션 없는 뷰와 같다(바이트 불변의 근거): 1:100 1F 평면에서 dims 를 끄면 일반 1F 평면(같은 레벨·절단 높이·축척)과 선·문자가 같다
    ok(sess.run("view.set", {"id": ids13["plan-1F-dims"], "dims": False}))
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    probe["option_off"] = {"lines_equal": g.q(D.view_lines, ids13["plan-1F-dims"]) == g.q(D.view_lines, views["plan-1F"]), "labels_equal": g.q(D.view_labels, ids13["plan-1F-dims"]) == g.q(D.view_labels, views["plan-1F"]),
                           "dims": len(g.q(D.view_dims, ids13["plan-1F-dims"]))}
    ok(sess.run("view.set", {"id": ids13["plan-1F-dims"], "dims": True}))
    # ---- 입면 외부 마감(E-02): finish 옵션은 마감 재료를 지정한 벽에만 해치·문자를 그린다. 외벽은 9단계에서 층 구성을 받아 지정 재료가 지워졌으므로(층 구성이 재료를 정한다)
    # 이 모델 변경(맨 끝이라 앞 산출에 영향 없음)으로 층 구성을 떼고 4단계와 같은 재료(1F 치장벽돌, 2F 스터코)를 다시 지정한 뒤 finish 입면 4개와 기본 입면 4개를 잰다
    for lv, mat in (("1F", "치장벽돌"), ("2F", "스터코")):
        for w in ids[f"ext-{lv}"]:
            ok(sess.run("wall.layers", {"id": w, "layers": None}))
            ok(sess.run("material.set", {"id": w, "name": mat}))
    fin_views = {}
    for i_, d in enumerate("NESW", 1):
        fin_views[f"elevfin-{d}"] = ok(sess.run("view.create", {"id": vid(f"h{i_}"), "name": f"{ {'N': '북', 'E': '동', 'S': '남', 'W': '서'}[d] }측 입면 마감(E-02)", "type": "elevation", "dir": d, "finish": True}))["id"]
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    mats = ["치장벽돌", "스터코"]
    chk = {}
    for k, vv in [(f"elev-{d}", views[f"elev-{d}"]) for d in "NESW"] + list(fin_views.items()):
        ln = g.q(D.view_lines, vv)
        chk[k] = {"hatch": sum(1 for x in ln if x[0] == "hatch"), "labels": sorted({t[2] for t in g.q(D.view_labels, vv) if t[2] in mats})}
    for k, vv in fin_views.items():
        for ext in ("svg", "pdf"):
            r = export(sess, "view.export", {"id": vv}, work / f"s13_{k}.{ext}")
            (sd / f"{k}.{ext}").write_bytes((work / f"s13_{k}.{ext}").read_bytes())
            probe["exports"].append({"dir": "sheets13", "view": k, "ext": ext, "bytes": r["bytes"], "segments": r["segments"], "labels": r["labels"]})
    probe["finish_check"] = {"materials": mats, "views": chk}
    log["after13"] = probe


def after14(sess, ids, views, log, out, work, spec):
    """열네 번째 작업: 경사 지붕(박공 주차 캐노피·외쪽 현관 캐노피·모임 정자)과 평지붕 슬래브(S-ROOF)의 파라펫, 승강기 보강(피트·기계실·층별 승강장 문·슬래브 구멍 연동 이동)을
    입력으로 더하고 지붕 평면·단면·입면(A-018)과 승강기 상세(A-019)를 낸다. 산출은 sheets14/ 에 둔다. 모든 기존 산출을 뽑은 뒤 맨 끝에 더한다(자동 id 가 밀리지 않게 id 는 모두 명시한다).
    독립 검산(엔진 코드·그리기 코드를 쓰지 않는다): 내보낸 IFC 의 DoBIM_Intent 를 읽어 math.tan 으로 용마루 높이·처마 끝 높이를 다시 계산해 단면·입면 선의 높이와 맞추고,
    파라펫 윗면 높이(슬래브 윗면 + 높이), 승강기를 옮긴 뒤의 슬래브 구멍(= 옮긴 샤프트 사각형)을 IFC 의도에서 다시 읽어 맞춘다."""
    import math

    import ifcopenshell
    import ifcopenshell.util.element as ue

    from dobim_core import dispatch
    sd = out / "sheets14"
    sd.mkdir(exist_ok=True)
    probe: dict = {"exports": []}
    vid = lambda k: "4" + "0" * 19 + k   # noqa: E731
    rid = lambda n: "e" + f"{n:021d}"   # noqa: E731
    two, eid = ids["2F"], ids["EV-1"]
    roofs = {"RF-PARK": (1, {"level": two, "boundary": [[0.0, -7800.0], [10000.0, -7800.0], [10000.0, -2800.0], [0.0, -2800.0]], "form": "gable", "pitch": 8.0, "overhang": 300.0, "base_offset": -500.0, "thickness": 150.0, "name": "RF-PARK"}),
             "RF-ENTRY": (2, {"level": two, "boundary": [[2400.0, -2000.0], [5600.0, -2000.0], [5600.0, -200.0], [2400.0, -200.0]], "form": "shed", "dir": 270.0, "pitch": 10.0, "overhang": 0.0, "base_offset": -500.0, "thickness": 150.0, "name": "RF-ENTRY"}),
             "RF-GAZEBO": (3, {"level": two, "boundary": [[13500.0, 8000.0], [17000.0, 8000.0], [17000.0, 10500.0], [13500.0, 10500.0]], "form": "hip", "pitch": 30.0, "overhang": 400.0, "base_offset": -900.0, "thickness": 150.0, "name": "RF-GAZEBO"})}
    for nm, (n, a) in roofs.items():
        ids[nm] = rid(n)
        ok(sess.run("roof.create", {"id": ids[nm], **a}))
    # 평지붕 슬래브 둘레의 파라펫(높이 900·두께 200)
    par = {"height": 900.0, "thickness": 200.0}
    ok(sess.run("slab.set", {"id": ids["S-ROOF"], "parapet": par}))
    # 승강기 보강: 샤프트가 지붕 윗면까지(rise 6600), 피트 1200, 기계실 2400(지붕 위), 승강장 문(S 면, 폭 900·높이 2100, 1F·2F). 지붕 슬래브에도 구멍을 내고(승강기가 소유) 옮긴다
    ok(sess.run("elevator.set", {"id": eid, "rise": 6600.0, "pit": 1200.0, "machine_height": 2400.0, "doors": {"face": "S", "w": 900.0, "h": 2100.0, "levels": [ids["1F"], ids["2F"]]}}))
    cut2 = ok(sess.run("elevator.cut_slabs", {"id": eid}))
    owned0 = ok(sess.run("element.get", {"id": eid}))["params"]["hole_slabs"]
    mv = ok(sess.run("elevator.move", {"id": eid, "dx": 300.0, "dy": -200.0}))
    shaft = ok(sess.run("element.get", {"id": eid}))["params"]["shaft"]
    probe["elevator"] = {"cut_again": cut2, "owned": owned0, "move": mv, "shaft": shaft}
    mk = lambda k, a: ok(sess.run("view.create", {"id": vid(k), **a}))   # noqa: E731
    v = {}
    v["roof-plan"] = mk("i1", {"name": "지붕 평면도", "type": "plan", "level": two, "cut_height": 12000.0, "scale": 100})
    v["section-EE"] = mk("i2", {"name": "E-E 단면(지붕·파라펫)", "type": "section", "p1": [3000.0, -9000.0], "p2": [3000.0, 9000.0], "scale": 100})
    v["elev-S-roof"] = mk("i3", {"name": "남측 입면(지붕)", "type": "elevation", "dir": "S"})
    v["elev-E-roof"] = mk("i4", {"name": "동측 입면(지붕)", "type": "elevation", "dir": "E"})
    v["ev-plan-1F"] = mk("i5", {"name": "승강기 1F 평면(승강장 문)", "type": "plan", "level": ids["1F"], "scale": 50, "around": eid, "margin": 1500.0, "dims": True})
    v["ev-plan-pit"] = mk("i6", {"name": "승강기 피트 평면", "type": "plan", "level": ids["1F"], "cut_height": -600.0, "scale": 50, "around": eid, "margin": 1500.0, "dims": True})
    v["ev-plan-machine"] = mk("i7", {"name": "승강기 기계실 평면", "type": "plan", "level": two, "cut_height": 4000.0, "scale": 50, "around": eid, "margin": 1500.0, "dims": True})
    v["ev-section-door"] = mk("i8", {"name": "승강기 단면(승강장 문·피트·기계실)", "type": "section", "p1": [-500.0, 6300.0], "p2": [3500.0, 6300.0], "flip": True, "scale": 50, "around": eid, "margin": 1500.0, "dims": True})
    ids14 = {k: x["id"] for k, x in v.items()}
    probe["views"] = ids14
    for nm, vv in ids14.items():
        for ext in ("svg", "pdf"):
            r = export(sess, "view.export", {"id": vv}, work / f"s14_{nm}.{ext}")
            (sd / f"{nm}.{ext}").write_bytes((work / f"s14_{nm}.{ext}").read_bytes())
            probe["exports"].append({"dir": "sheets14", "view": nm, "ext": ext, "bytes": r["bytes"], "segments": r["segments"], "labels": r["labels"], "annotations": r["annotations"]})
    for nm, ks, title, paper in (("A-018", [ids14["roof-plan"], ids14["section-EE"], ids14["elev-S-roof"], ids14["elev-E-roof"]], "지붕 평면도·단면도·입면도(경사 지붕·파라펫)", "A1"),
                                 ("A-019", [ids14["ev-plan-1F"], ids14["ev-plan-pit"], ids14["ev-plan-machine"], ids14["ev-section-door"]], "승강기 상세도(승강장 문·피트·기계실)", "A1")):
        a = {**spec, "views": ks, "paper": paper, "sheet_name": title, "sheet_number": nm, "dims": []}
        for ext in ("svg", "pdf"):
            r = export(sess, "sheet.export", a, work / f"{nm}.{ext}")
            (sd / f"{nm}.{ext}").write_bytes((work / f"{nm}.{ext}").read_bytes())
            probe["exports"].append({"dir": "sheets14", "sheet": nm, "ext": ext, "bytes": r["bytes"], "font_embedded": r["font_embedded"], "non_ascii_dropped": r["non_ascii_dropped"]})
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    probe["dims"] = {k: [d[5] for d in g.q(D.view_dims, x)] for k, x in ids14.items() if k.startswith("ev-")}
    probe["labels"] = {k: [t[2] for t in g.q(D.view_labels, x)] for k, x in ids14.items()}
    probe["counts"] = {k: {"lines": len(g.q(D.view_lines, x)), "fills": len(g.q(D.view_fills, x))} for k, x in ids14.items()}
    probe["z_sets"] = {k: sorted({round(z, 3) for ln in g.q(D.view_lines, ids14[k]) for z in (ln[2], ln[4])}) for k in ("section-EE", "elev-S-roof", "elev-E-roof")}
    # ---- 독립 검산: 내보낸 IFC 의 의도에서 지붕·파라펫·승강기를 다시 읽는다(math.tan, 엔진 코드 없음)
    ifc_out(sess, "after14.ifc")
    f = GI.open_exported(work / "after14.ifc")   # 첫 글자가 무효한 id 는 내보낸 파일에서 바뀌어 있다(W2): 원래 id 로 되돌려 찾는다
    intent = lambda gid: json.loads(ue.get_pset(f.by_guid(gid), "DoBIM_Intent")["Intent"])["params"]   # noqa: E731
    lv2 = ok(sess.run("element.get", {"id": two}))["params"]["elevation"]
    exp = {}
    for nm in roofs:
        ip = intent(ids[nm])
        b = ip["boundary"]
        sx, sy = abs(b[1][0] - b[0][0]) + abs(b[1][1] - b[0][1]), abs(b[2][0] - b[1][0]) + abs(b[2][1] - b[1][1])   # 축에 나란한 직사각형: 두 변 길이
        t = math.tan(math.radians(ip["pitch"]))
        short = min(sx, sy)
        z_eave = lv2 + ip["base_offset"]
        e = {"form": ip["form"], "pitch": ip["pitch"], "eave_z": z_eave, "eave_end_z": z_eave - t * ip["overhang"], "thickness": ip["thickness"]}
        if ip["form"] in ("gable", "hip"):
            e["ridge_z"] = z_eave + t * short / 2.0
        else:
            n = (math.cos(math.radians(ip["dir"])), math.sin(math.radians(ip["dir"])))
            half = (sx / 2.0 * abs(n[0]) + sy / 2.0 * abs(n[1]))
            e["high_z"] = z_eave + t * 2 * half   # 물이 흐르는 쪽 처마의 반대쪽 끝(가장 높은 모서리)의 높이
        exp[nm] = e
    probe["roof_expected"] = exp
    probe["roof_ifc"] = {nm: {"class": f.by_guid(ids[nm]).is_a(), "predefined": f.by_guid(ids[nm]).PredefinedType,
                              "faces": [(k.is_a(), k.PredefinedType) for rel in f.by_guid(ids[nm]).IsDecomposedBy for k in rel.RelatedObjects]} for nm in roofs}
    sp = intent(ids["S-ROOF"])
    ztop = lv2 + sp["top_offset"]
    probe["parapet"] = {"intent": sp["parapet"], "top_z": ztop + sp["parapet"]["height"],
                        "ifc": [(k.is_a(), k.PredefinedType) for rel in f.by_guid(ids["S-ROOF"]).IsDecomposedBy for k in rel.RelatedObjects]}
    iev = intent(eid)
    probe["ifc_elevator"] = {k: iev[k] for k in ("shaft", "pit", "machine", "doors", "stops", "rise", "hole_slabs", "hole_clearance")}
    holes = {}
    c_, sh = iev["hole_clearance"], iev["shaft"]
    want = sorted([[sh[0] - c_, sh[1] - c_], [sh[2] + c_, sh[1] - c_], [sh[2] + c_, sh[3] + c_], [sh[0] - c_, sh[3] + c_]])
    for sid in iev["hole_slabs"]:
        ops = intent(sid).get("openings", [])
        holes[sid] = {"count": len(ops), "match_shaft": any(sorted(o) == want for o in ops)}
    probe["ifc_holes"] = holes
    probe["ifc_check"] = {"roofs": len(f.by_type("IfcRoof")), "roof_slabs": len([e for e in f.by_type("IfcSlab") if e.PredefinedType == "ROOF"]), "parapet_walls": len([e for e in f.by_type("IfcWall") if e.PredefinedType == "PARAPET"])}
    # ---- IFC 왕복: 지붕·파라펫·승강기 선택 입력이 되살고 해시 3종이 같다
    h0 = ok(sess.run("model.hash", {}))
    imp = ok(dispatch.run(sess.env, "ifc.import", {"source": "after14.ifc", "path": "after14-imp.dobim"}))
    h1 = ok(dispatch.run(sess.env, "model.hash", {}, project="after14-imp.dobim"))
    same = {}
    for k in [*roofs.keys(), "S-ROOF", "EV-1"]:
        a0 = ok(sess.run("element.get", {"id": ids[k]}))["params"]
        a1 = ok(dispatch.run(sess.env, "element.get", {"id": ids[k]}, project="after14-imp.dobim"))["params"]
        same[k] = a0 == a1
    probe["roundtrip"] = {"warnings": imp["warnings"], "hash_equal": [h0[k] == h1[k] for k in ("state", "derived", "ifc")], "params_equal": same}
    # ---- 선택 입력을 지우면 승강기 도면에서 문·피트·기계실 표기가 사라진다(복원 시험: 같은 입력을 다시 넣는다)
    saved_ev = ok(sess.run("element.get", {"id": eid}))["params"]
    on = {"ev_plan_1F_symbols": len([ln for ln in g.q(D.view_lines, ids14["ev-plan-1F"]) if ln[0] == "symbol"])}
    ok(sess.run("elevator.set", {"id": eid, "clear_pit": True, "clear_machine": True, "clear_doors": True}))
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    off = {"ev_plan_1F_symbols": len([ln for ln in g.q(D.view_lines, ids14["ev-plan-1F"]) if ln[0] == "symbol"])}
    probe["option_off"] = {"on": on, "off": off}
    ok(sess.run("elevator.set", {"id": eid, "pit": saved_ev["pit"], "machine_height": saved_ev["machine"]["height"], "doors": {**saved_ev["doors"], "levels": saved_ev["stops"]}}))
    log["after14"] = probe


def after15(sess, ids, views, log, out, work, spec):
    """열다섯 번째 작업: (1) 실제 건물 위에 얹은 박공 지붕(별채: 벽 4개·문·섬 코어·기둥 위의 RF-ANNEX), (2) space.from_walls 의 섬 구멍(방 안의 닫힌 내부 벽 고리와 기둥을 구멍으로 뺀 면적),
    (3) 난간 연동(railing.from_stair·from_slab 이 만든 난간이 계단·승강기가 소유한 슬래브 구멍을 따라간다: 옮기고 되돌리기·사용자가 고친 난간의 lost·실행 취소), (4) 슬래브 구멍을 통한 단면 가림 판정(B5)을 입력으로 더한다.
    산출은 sheets15/ 에 둔다. 모든 기존 산출을 뽑은 뒤 맨 끝에 더한다(자동 id 가 밀리지 않게 id 는 모두 명시한다. 공간 자동 생성 id 만 자동이다: 이 단계의 마지막 입력).
    독립 검산(엔진·그리기 코드를 쓰지 않는다): 내보낸 IFC 의 의도에서 math.tan 으로 용마루·처마 끝 높이를, IFC 몸체(IfcArbitraryProfileDefWithVoids)의 폴리라인에서 신발끈 공식으로 공간 면적을,
    벽 축선·두께에서 손으로 섬 바깥 면 좌표를, 계단 걸음선·폭에서 손으로 난간 옆선 좌표를 다시 계산해 맞춘다."""
    import math

    import ifcopenshell
    import ifcopenshell.util.element as ue

    from dobim_core import dispatch
    sd = out / "sheets15"
    sd.mkdir(exist_ok=True)
    probe: dict = {"exports": []}
    one = ids["1F"]
    wid = lambda k: "1" + "0" * 19 + k   # noqa: E731
    vid = lambda k: "4" + "0" * 19 + k   # noqa: E731
    get = lambda i: ok(sess.run("element.get", {"id": i}))   # noqa: E731
    # ---- 별채: 1F 에 벽 4개(두께 200, 높이 3000)·남측 벽의 문·방 안의 섬(닫힌 벽 고리 1000 x 1000, 두께 100)·기둥, 그 위에 박공 지붕(처마 선 = 벽 바깥면, 처마 높이 = 벽 윗면 3000)
    ax = [(13000.0, 500.0), (17500.0, 500.0), (17500.0, 4500.0), (13000.0, 4500.0)]
    for i in range(4):
        ids[f"AN-W{i + 1}"] = wid(f"f{i + 1}")
        ok(sess.run("wall.create", {"id": ids[f"AN-W{i + 1}"], "level": one, "axis": [*ax[i], *ax[(i + 1) % 4]], "thickness": 200.0, "height": 3000.0, "name": f"AN-W{i + 1}"}))
    for i in range(4):
        ok(sess.run("join.set", {"a": ids[f"AN-W{i + 1}"], "b": ids[f"AN-W{(i + 1) % 4 + 1}"]}))
    ids["AN-D1"] = "2" + "0" * 19 + "f1"
    ok(sess.run("door.place", {"id": ids["AN-D1"], "host": ids["AN-W1"], "offset": 1800.0, "width": 900.0, "height": 2100.0, "name": "AN-D1"}))
    core = [(15000.0, 2000.0), (16000.0, 2000.0), (16000.0, 3000.0), (15000.0, 3000.0)]
    for i in range(4):
        ids[f"AN-C{i + 1}"] = wid(f"g{i + 1}")
        ok(sess.run("wall.create", {"id": ids[f"AN-C{i + 1}"], "level": one, "axis": [*core[i], *core[(i + 1) % 4]], "thickness": 100.0, "height": 3000.0, "name": f"AN-C{i + 1}"}))
    for i in range(4):
        ok(sess.run("join.set", {"a": ids[f"AN-C{i + 1}"], "b": ids[f"AN-C{(i + 1) % 4 + 1}"]}))
    ids["AN-COL"] = "0" * 20 + "c2"
    ok(sess.run("family.place", {"id": ids["AN-COL"], "family": "column.section", "type_values": {"section": "round"}, "level": one, "position": [14000.0, 3500.0]}))
    ids["RF-ANNEX"] = "e" + f"{4:021d}"
    ok(sess.run("roof.create", {"id": ids["RF-ANNEX"], "level": one, "boundary": [[12900.0, 400.0], [17600.0, 400.0], [17600.0, 4600.0], [12900.0, 4600.0]], "form": "gable", "pitch": 30.0,
                                "overhang": 400.0, "base_offset": 3000.0, "thickness": 150.0, "name": "RF-ANNEX"}))
    mk = lambda k, a: ok(sess.run("view.create", {"id": vid(k), **a}))   # noqa: E731
    box = [12000.0, 0.0, 18500.0, 5200.0]
    v = {}
    v["annex-plan"] = mk("j1", {"name": "별채 평면도", "type": "plan", "level": one, "cut_height": 1200.0, "scale": 50, "crop": box})
    v["annex-roof"] = mk("j2", {"name": "별채 지붕 평면도", "type": "plan", "level": one, "cut_height": 6000.0, "scale": 50, "crop": box})
    v["annex-section"] = mk("j3", {"name": "F-F 단면(별채)", "type": "section", "p1": [15250.0, -1000.0], "p2": [15250.0, 6000.0], "depth": 2800.0, "scale": 50, "crop": [1000.0, -500.0, 6000.0, 5000.0]})
    v["annex-elev"] = mk("j4", {"name": "남측 입면(별채)", "type": "elevation", "dir": "S", "scale": 50, "crop": [12000.0, -500.0, 18500.0, 5000.0]})
    ids15 = {k: x["id"] for k, x in v.items()}
    probe["views"] = ids15
    for nm, vv in ids15.items():
        for ext in ("svg", "pdf"):
            r = export(sess, "view.export", {"id": vv}, work / f"s15_{nm}.{ext}")
            (sd / f"{nm}.{ext}").write_bytes((work / f"s15_{nm}.{ext}").read_bytes())
            probe["exports"].append({"dir": "sheets15", "view": nm, "ext": ext, "bytes": r["bytes"], "segments": r["segments"], "labels": r["labels"], "annotations": r["annotations"]})
    # 방 안의 섬을 구멍으로 빼는 공간 자동 생성(점 지정): 별채 방(섬·기둥이 구멍)과 섬 안쪽(설비실). 자동 id 를 쓰는 이 단계의 유일한 입력
    sp = ok(sess.run("space.from_walls", {"level": one, "points": [[14000.0, 1500.0], [15500.0, 2500.0]], "category": "별채"}))
    rooms = sp["created"]
    ok(sess.run("space.set", {"id": rooms[0], "name": "별채 홀"}))
    ok(sess.run("space.set", {"id": rooms[1], "name": "설비실", "category": "설비"}))
    ids["AN-SP1"], ids["AN-SP2"] = rooms
    probe["spaces"] = {"created": rooms, "skipped": sp["skipped"], "hall": get(rooms[0]), "core": get(rooms[1])}
    # 시트 A-020: 별채(평면·지붕 평면·단면·입면)
    a = {**spec, "views": [ids15["annex-plan"], ids15["annex-roof"], ids15["annex-section"], ids15["annex-elev"]], "paper": "A1", "sheet_name": "별채 평면도·지붕 평면도·단면도·입면도", "sheet_number": "A-020", "dims": []}
    for ext in ("svg", "pdf"):
        r = export(sess, "sheet.export", a, work / f"A-020.{ext}")
        (sd / f"A-020.{ext}").write_bytes((work / f"A-020.{ext}").read_bytes())
        probe["exports"].append({"dir": "sheets15", "sheet": "A-020", "ext": ext, "bytes": r["bytes"], "font_embedded": r["font_embedded"], "non_ascii_dropped": r["non_ascii_dropped"]})
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    probe["labels"] = {k: [t[2] for t in g.q(D.view_labels, x)] for k, x in ids15.items()}
    probe["counts"] = {k: {"lines": len(g.q(D.view_lines, x)), "fills": len(g.q(D.view_fills, x))} for k, x in ids15.items()}
    probe["z_sets"] = {k: sorted({round(z, 3) for ln in g.q(D.view_lines, ids15[k]) for z in (ln[2], ln[4])}) for k in ("annex-section", "annex-elev")}
    # ---- 난간 연동(모델 변경은 산출을 뽑은 뒤): 계단 ST-1F 옆 난간과 2층 슬래브의 승강기 구멍 둘레 난간이 원본을 따라간다
    st, s2f, eid = ids["ST-1F"], ids["S-2F"], ids["EV-1"]
    rl = ok(sess.run("railing.from_stair", {"stair": st, "inset": 50.0}))["created"]
    shaft = get(eid)["params"]["shaft"]
    rect = sorted([[shaft[0], shaft[1]], [shaft[2], shaft[1]], [shaft[2], shaft[3]], [shaft[0], shaft[3]]])
    ops = get(s2f)["params"].get("openings", [])
    idx = next((i for i, o in enumerate(ops) if sorted(o) == rect), None)
    hole_rl = ok(sess.run("railing.from_slab", {"slab": s2f, "opening": idx}))["created"][0] if idx is not None else None
    run = get(st)["params"]["run"]
    width = get(st)["params"]["width"]
    side = lambda dx, dy, sgn: [[run[0] + dx, run[1] + dy + sgn * (width / 2.0 - 50.0)], [run[2] + dx, run[3] + dy + sgn * (width / 2.0 - 50.0)]]   # noqa: E731  계단 걸음선에서 손으로 낸 옆선
    pth = lambda i: get(i)["params"]["path"]   # noqa: E731
    left, right = (rl[0], rl[1]) if get(rl[0])["params"]["src_tag"] == "f0L" else (rl[1], rl[0])
    lk: dict = {"stair_rails": rl, "hole_rail": hole_rl, "opening_index": idx, "tags": {i: get(i)["params"]["src_tag"] for i in rl},
                "initial_ok": pth(left) == side(0, 0, 1) and pth(right) == side(0, 0, -1)}
    h0 = ok(sess.run("model.hash", {}))["state"]
    mv = ok(sess.run("stair.move", {"id": st, "dx": 400.0, "dy": 0.0}))
    lk["move"] = mv["railings"]
    lk["moved_ok"] = pth(left) == side(400, 0, 1) and pth(right) == side(400, 0, -1)
    ok(sess.run("history.undo", {}))
    lk["undo_state_equal"] = ok(sess.run("model.hash", {}))["state"] == h0
    ok(sess.run("stair.move", {"id": st, "dx": 400.0, "dy": 0.0}))
    ok(sess.run("railing.move", {"id": right, "dx": 0.0, "dy": 25.0}))   # 사용자가 오른쪽 난간을 손으로 고쳤다
    hand = pth(right)
    back = ok(sess.run("stair.move", {"id": st, "dx": -400.0, "dy": 0.0}))
    lk["back"] = back["railings"]
    lk["hand_edit_kept"] = pth(right) == hand and pth(left) == side(0, 0, 1)
    ok(sess.run("railing.set", {"id": right, "clear_source": True}))
    lk["released_after_clear"] = "src" not in get(right)["params"]
    if hole_rl is not None:
        ring = get(s2f)["params"]["openings"][idx]
        lk["hole_rail_initial"] = pth(hole_rl) == [*ring, ring[0]]
        em = ok(sess.run("elevator.move", {"id": eid, "dx": 100.0, "dy": 100.0}))
        lk["hole_move"] = em["holes"].get("railings")
        ring2 = get(s2f)["params"]["openings"][idx]
        lk["hole_rail_followed"] = pth(hole_rl) == [*ring2, ring2[0]] and ring2 != ring
        ok(sess.run("elevator.move", {"id": eid, "dx": -100.0, "dy": -100.0}))
        lk["hole_rail_restored"] = pth(hole_rl) == [*ring, ring[0]]
    probe["linkage"] = lk
    # ---- B5: 슬래브 구멍을 통한 가림 판정 — 2층 슬래브(S-2F)의 가림 조각에 승강기 구멍이 비어 있다(독립 검산: 신발끈 면적, 구멍 한가운데가 어느 조각에도 없다)
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    tris = g.q(D.slab_tris, s2f)
    sp2 = get(s2f)["params"]
    ar = lambda poly: abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))) / 2.0   # noqa: E731
    want = ar(sp2["boundary"]) - sum(ar(o) for o in sp2.get("openings", []))
    have = sum(ar(t) for t in tris)
    cx = [(sum(p[0] for p in o) / len(o), sum(p[1] for p in o) / len(o)) for o in sp2.get("openings", [])]
    inside = lambda poly, pt: all((poly[(i + 1) % len(poly)][0] - poly[i][0]) * (pt[1] - poly[i][1]) - (poly[(i + 1) % len(poly)][1] - poly[i][1]) * (pt[0] - poly[i][0]) > 0 for i in range(len(poly)))   # noqa: E731
    probe["occlusion"] = {"slab": "S-2F", "openings": len(sp2.get("openings", [])), "pieces": len(tris), "area_want": want, "area_have": have,
                          "hole_center_covered": any(inside(list(t), c) for t in tris for c in cx)}
    # ---- 독립 검산: 내보낸 IFC 의 의도·몸체에서 다시 읽는다
    ifc_out(sess, "after15.ifc")
    f = GI.open_exported(work / "after15.ifc")   # 첫 글자가 무효한 id 는 내보낸 파일에서 바뀌어 있다(W2): 원래 id 로 되돌려 찾는다
    intent = lambda gid: json.loads(ue.get_pset(f.by_guid(gid), "DoBIM_Intent")["Intent"])["params"]   # noqa: E731
    ip = intent(ids["RF-ANNEX"])
    t = math.tan(math.radians(ip["pitch"]))
    short = min(abs(ip["boundary"][1][0] - ip["boundary"][0][0]), abs(ip["boundary"][2][1] - ip["boundary"][1][1]))
    z_eave = ip["base_offset"]
    probe["roof_expected"] = {"ridge_z": z_eave + t * short / 2.0, "eave_end_z": z_eave - t * ip["overhang"], "eave_z": z_eave}
    probe["roof_ifc"] = {"class": f.by_guid(ids["RF-ANNEX"]).is_a(), "predefined": f.by_guid(ids["RF-ANNEX"]).PredefinedType,
                         "faces": [(k.is_a(), k.PredefinedType) for rel in f.by_guid(ids["RF-ANNEX"]).IsDecomposedBy for k in rel.RelatedObjects]}
    spe = f.by_guid(rooms[0])
    prof = spe.Representation.Representations[0].Items[0].SweptArea
    pl = lambda c: [(p.Coordinates[0], p.Coordinates[1]) for p in c.Points][:-1]   # noqa: E731
    outer_ifc = pl(prof.OuterCurve)
    inner_ifc = [pl(c) for c in prof.InnerCurves] if prof.is_a("IfcArbitraryProfileDefWithVoids") else []
    probe["space_ifc"] = {"profile": prof.is_a(), "outer_area_m2": ar(outer_ifc) / 1e6, "inner_areas_m2": [ar(c) / 1e6 for c in inner_ifc],
                          "net_area_m2": (ar(outer_ifc) - sum(ar(c) for c in inner_ifc)) / 1e6,
                          "intent_holes": intent(rooms[0]).get("holes"), "hand_island": [[14950.0, 1950.0], [16050.0, 1950.0], [16050.0, 3050.0], [14950.0, 3050.0]]}
    probe["space_ifc"]["core_profile"] = f.by_guid(rooms[1]).Representation.Representations[0].Items[0].SweptArea.is_a()
    probe["rail_ifc"] = {i: {k: intent(i).get(k) for k in ("src", "src_tag", "src_inset")} for i in (left, hole_rl) if i}   # 연동이 남은 난간(끊은 난간 right 는 출처가 없어야 한다)
    probe["rail_ifc_cleared"] = {k: intent(right).get(k) for k in ("src", "src_tag", "src_inset")}
    # ---- IFC 왕복: 별채·섬 구멍 공간·연동 난간이 되살고 해시 3종이 같다
    h_before = ok(sess.run("model.hash", {}))
    imp = ok(dispatch.run(sess.env, "ifc.import", {"source": "after15.ifc", "path": "after15-imp.dobim"}))
    h_after = ok(dispatch.run(sess.env, "model.hash", {}, project="after15-imp.dobim"))
    same = {}
    for k in ("RF-ANNEX", "AN-SP1", "AN-SP2", "AN-COL"):
        a0 = get(ids[k])["params"]
        a1 = ok(dispatch.run(sess.env, "element.get", {"id": ids[k]}, project="after15-imp.dobim"))["params"]
        same[k] = a0 == a1
    for i in [*rl, hole_rl]:
        if i is not None:
            same[f"rail:{i[-6:]}"] = get(i)["params"] == ok(dispatch.run(sess.env, "element.get", {"id": i}, project="after15-imp.dobim"))["params"]
    probe["roundtrip"] = {"warnings": imp["warnings"], "hash_equal": [h_before[k] == h_after[k] for k in ("state", "derived", "ifc")], "params_equal": same}
    log["after15"] = probe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheets", action="store_true")
    ap.add_argument("--no-slabs", action="store_true")
    ap.add_argument("--no-heights", action="store_true")
    ap.add_argument("--no-sheet2", action="store_true")
    ap.add_argument("--no-sheetdef", action="store_true")
    ap.add_argument("--no-after7", action="store_true")
    ap.add_argument("--no-after8", action="store_true")
    ap.add_argument("--no-after9", action="store_true")
    ap.add_argument("--no-after11", action="store_true")
    ap.add_argument("--no-after12", action="store_true")
    ap.add_argument("--no-after13", action="store_true")
    ap.add_argument("--no-after14", action="store_true")
    ap.add_argument("--no-after15", action="store_true")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--no-site", action="store_true")
    for f in ("stairs", "spaces", "fire", "finish"):
        ap.add_argument(f"--no-{f}", action="store_true")
    o = ap.parse_args()
    out = Path(o.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir(exist_ok=True)
    if o.sheets and not o.no_png:
        PNG_STATE["dir"] = out / "png"
        PNG_STATE["dir"].mkdir(exist_ok=True)
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
    if o.sheets and not o.no_sheet2 and not o.no_after7:
        after7(sess, ids, views, log, out, work, spec)
    if o.sheets and not o.no_sheet2 and not o.no_after8:
        after8(sess, ids, views, log, out, work, spec, site=not o.no_site)
    if o.sheets and not o.no_sheet2 and not o.no_after9:
        after9(sess, ids, views, log, out, work, spec)
    if o.sheets and not o.no_sheet2 and not o.no_after11:
        after11(sess, ids, views, log, out, work, spec)
    if o.sheets and not o.no_sheet2 and not o.no_after8 and not o.no_site and not o.no_after12:
        after12(sess, ids, views, log, out, work, spec)
    if o.sheets and not o.no_sheet2 and not o.no_after8 and not o.no_site and not o.no_after11 and not o.no_after13:
        after13(sess, ids, views, log, out, work, spec)
    if o.sheets and not o.no_sheet2 and not o.no_after8 and not o.no_site and not o.no_after11 and not o.no_after13 and not o.no_after14:
        after14(sess, ids, views, log, out, work, spec)
    if o.sheets and not o.no_sheet2 and not o.no_after8 and not o.no_site and not o.no_after11 and not o.no_after13 and not o.no_after14 and not o.no_after15 and ids.get("ST-1F") and ids.get("S-2F"):
        after15(sess, ids, views, log, out, work, spec)
    if PNG_STATE["log"]:
        log["png"] = PNG_STATE["log"]
    (out / "run.json").write_text(json.dumps({"views": views, "ids": ids, **log}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
