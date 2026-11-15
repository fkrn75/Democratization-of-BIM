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
        f = ifcopenshell.open(str(work / "site.ifc"))
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
    f = ifcopenshell.open(str(work / "after9.ifc"))
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
    f = ifcopenshell.open(str(work / "after11.ifc"))
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
             {"name": "N1", "kind": "address", "face": "N", "center": [6000.0, 8160.0], "z": 1500.0, "w": 400.0, "h": 250.0, "text": "12-3"}]
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
    f = ifcopenshell.open(str(work / "after12.ifc"))
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
    ap.add_argument("--no-site", action="store_true")
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
    (out / "run.json").write_text(json.dumps({"views": views, "ids": ids, **log}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
