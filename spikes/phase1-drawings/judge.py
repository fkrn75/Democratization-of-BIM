# SPDX-License-Identifier: MPL-2.0
"""도면 체크리스트 판정기 (1단계).

G1 판정(항목 ID·층·판정만 carried_g1.json 에 옮겼다)을 출발점으로 삼고, 그 판정이 아직 맞는지를 두 가지로 다시 확인한다.
 1) 도면 내용 항목: 이번에 뽑은 기본 산출 SVG 가 G1 산출물과 바이트 단위로 같은지(--g1 폴더가 있을 때). 같으면 G1 판정을 유지한다.
 2) 시트·글꼴이 바꾸는 항목(N-02, SC-05, SC-06, T-02, T-04, T-05, T-08, LN-06, B-13): 산출 파일(시트 SVG·PDF)을 직접 검사해 판정한다.
    시트 산출이 없으면(기준선) 같은 검사가 '없음'으로 나와 G1 판정 그대로다.
 3) 슬래브·높이 치수가 바꾸는 항목(X-01, X-02, X-03, X-04. DM-08 은 이 셋의 중복 행이라 따름): run.json 의 slab_probe(슬래브 추가 전후 뷰 선분·채움)와 heights/ 산출(높이 치수 SVG)을 검사해 판정한다.
    산출이 없으면 G1 판정 그대로다. 128항목 집계에는 "법정 #7 슬래브 단면 표현"에 해당하는 항목이 따로 없어서, G2 5-1절의 8개 자동 표현 대상(#2·#7·#8)은
    집계와 별도로 judge_gate 가 근거와 함께 낸다(계단 등 아직 없는 표현은 부분으로 남긴다).
 4) 계단·공간·방화·외부 마감이 바꾸는 항목(L-03 방화구획·방화문, L-04 복도·계단, E-02 외부마감재료. FR-01·FR-02·VT-01·MT-02 는 이 셋의 중복 행): run.json 의 stair_probe·
    space_probe·fire_probe·finish_probe(각 입력을 더하기 전후의 뷰 선분·문자)와 base/ 산출 SVG(문자 층 labels, 해치 선 lines-hatch)를 함께 검사해 판정한다.
    산출이 없으면 G1 판정 그대로다. 법정 #7 의 계단 단면 표현은 stair_probe 의 단면 뷰 채움 증가로 확인한다.
 5) 여덟 번째 작업(대지·배치도·숨은 선·선 굵기 위계·제3각법): run.json 의 after8 와 sheets8/(A-006 배치도, A-007 입면, A-000b 도면목록)을 직접 검사해 법정 P-01·P-02·P-04,
    D-01·N-06·N-08·LN-03·LN-04·T-11·B-05 를 판정한다(judge_after8_items). 이격은 내보낸 IFC 에서 엔진과 독립으로 다시 잰 값과 맞춘다.
 6) 아홉 번째 작업(벽·슬래브 층 구성·문짝 두께·천장 높이·1:50 단면·입면 상세·비내력벽 절단선): run.json 의 after9 와 sheets9/(A-008 평면, A-009 종단면, A-010 입면)을 직접 검사해
    SC-09·B-17·X-04·LN-04 를 판정한다(judge_after9_items). 층 구성은 내보낸 IFC 에서 엔진과 독립으로 다시 잰 형상 두께와 맞춘다.
집계 규칙은 G1 과 같다: 중복 행은 뺀다, 해당없음은 분모에서 뺀다, 법정 16 = 별표 2 고유 항목(높이 3개 행은 한 묶음 판정).

사용: <python> judge.py --out <산출 폴더> [--g1 <G1 산출 폴더>] [--md <출력.md>]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
OK, PART, NO, NA = "충족", "부분", "미충족", "해당없음"


def load_carried():
    rows = json.loads((HERE / "carried_g1.json").read_text(encoding="utf-8"))
    return {r["id"]: dict(r, reason="G1 판정 유지") for r in rows}


def digest_same(out: Path, g1: Path | None):
    """기본 산출 SVG 가 G1 산출물과 같은가: (같은 수, 비교한 수)"""
    if g1 is None or not g1.exists():
        return None
    same = tot = 0
    for sub in ("base", "annot", "fam", "xsec"):
        for f in sorted((out / sub).glob("*.svg")) if (out / sub).exists() else []:
            g = g1 / sub / f.name
            if g.exists():
                tot += 1
                same += hashlib.sha256(g.read_bytes()).digest() == hashlib.sha256(f.read_bytes()).digest()
    return same, tot


# ---------------------------------------------------------------- 산출 검사
def sheet_files(out: Path):
    d = out / "sheets"
    return sorted(d.glob("*.svg")) if d.exists() else []


def pdf_has_korean_font(pdf: bytes) -> bool:
    """PDF 에 글꼴(FontFile2)이 내장돼 있고 그 안에 한글 음절 글리프가 있는가."""
    m = re.search(rb"/FontFile2 (\d+) 0 R", pdf)
    if not m:
        return False
    obj = re.search(rb"\n" + m.group(1) + rb" 0 obj\n.*?stream\n(.*?)\nendstream", pdf, re.S)
    if not obj:
        return False
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(io.BytesIO(zlib.decompress(obj.group(1))))
        return any(0xAC00 <= cp <= 0xD7A3 for cp in font.getBestCmap())
    except Exception:
        return False


def judge_sheet_items(out: Path, run: dict, base: dict):
    """시트 산출을 직접 보고 시트가 바꾸는 항목을 판정한다. 반환: {id: (판정, 근거)}"""
    res: dict[str, tuple[str, str]] = {}
    svgs = sheet_files(out)
    txt = {f.name: f.read_text(encoding="utf-8") for f in svgs}
    plan_ids = {v for k, v in run.get("views", {}).items() if k.startswith("plan")}
    has = lambda s, gid: f'id="{gid}"' in s   # noqa: E731

    north = [n for n, s in txt.items() if has(s, "north-symbol") and any(f'data-view="{p}"' in s for p in plan_ids)]
    res["N-02"] = (OK, f"시트 {len(north)}장에 방위표(north-symbol)와 평면 뷰 동시 배치") if north else (NO, "평면 SVG 에 방위 기호 요소 0개")
    tb = {n: s for n, s in txt.items() if has(s, "title-block")}
    scl = [n for n, s in tb.items() if re.search(r">(1:\d+|도면참조)<", s)]
    res["SC-05"] = (OK, f"표제란 축척 칸 + 축척 막대(scale-bar) 시트 {len(scl)}장") if scl else (PART, "축척은 도면 하단 제목 글에만, 표제란 없음")
    mixed = [n for n, s in tb.items() if len(set(re.findall(r'data-view="[^"]*" data-scale="(\d+)"', s))) > 1]
    if mixed:
        ok_cap = all(len(re.findall(r"S=1:\d+", txt[n])) >= len(re.findall(r'data-view="', txt[n])) and ">도면참조<" in txt[n] for n in mixed)
        res["SC-06"] = (OK, "축척이 다른 뷰 시트: 뷰마다 S=1:N 표기 + 표제란 '도면참조'") if ok_cap else (PART, "혼합 축척 시트의 표기 일부 누락")
    else:
        res["SC-06"] = (NA, "시트 조합 기능 없음(1뷰=1파일)" if not svgs else "혼합 축척 시트 산출 없음")
    if tb:
        n0 = next(iter(tb))
        s = tb[n0]
        have = {k: (k in s) for k in ("프로젝트명", "도면명", "도면번호", "축척", "작성일")}
        res["T-02"] = (PART, "표제란(프로젝트·도면명·도면번호·축척·작성일·개정란) 있음, 건축주·설계사무소 영역 없음") if all(have.values()) else (NO, f"표제란 칸 누락 {[k for k, v in have.items() if not v]}")
        res["T-04"] = (PART, "도면번호 칸에 호출자가 준 번호를 넣음. 분류체계·파일명 일치 검증은 없음")
        res["T-08"] = (PART, "개정란(차수·일자·내용) 빈 표만. 구름표시·개정 입력 수단 없음") if has(s, "revision-table") else (NO, "개정란 없음")
    else:
        res["T-02"], res["T-04"], res["T-08"] = [(base[i]["verdict"], "시트 산출 없음") for i in ("T-02", "T-04", "T-08")]
    paper = []
    for n, s in txt.items():
        m = re.search(r'width="([\d.]+)mm" height="([\d.]+)mm"', s)
        if m and has(s, "sheet-border") and (round(float(m.group(1))), round(float(m.group(2)))) in ((841, 594), (420, 297)):
            paper.append(n)
    res["T-05"] = (OK, f"A1·A3 용지 + 윤곽선 + 표제란 시트 {len(paper)}장") if paper else (NO, "A계열 용지·윤곽선 없음(내용에 맞춘 임의 크기)")
    pdfs = sorted((out / "sheets").glob("*.pdf")) if (out / "sheets").exists() else []
    good = [p.name for p in pdfs if pdf_has_korean_font(p.read_bytes())]
    if good:
        res["LN-06"] = (OK, f"PDF {len(good)}장에 고딕 글꼴(OFL) 부분집합 내장, 한글 글리프 확인")
        res["B-13"] = (OK, "sheet.export 의 PDF: 벡터, 한글 정상(글꼴 내장)")
    else:
        res["LN-06"] = (PART, "PDF 는 Helvetica 비내장, 한글 '?'. SVG 는 한글 글꼴 계열 지정")
        res["B-13"] = (PART, "PDF 출력 O 이나 한글이 '?' 로 깨짐")
    return res


def _svg_group_count(svg: str, gid: str, tag: str) -> int:
    """SVG 의 <g id=gid> 그룹 안 tag 요소 개수(없으면 0)."""
    m = re.search(rf'<g id="{gid}"[^>]*>(.*?)</g>', svg, re.S)
    return len(re.findall(rf"<{tag}[ >/]", m.group(1))) if m else 0


def judge_element_items(out: Path, run: dict, base: dict):
    """창·기둥 표현 항목(L-02, B-02)을 패밀리 배치 산출(fam/)과 기본 산출(base/)을 비교해 판정한다. 반환: {id: (판정, 근거)}

    창: 평면에서 문 호가 아닌 창 기호 — 창 하나가 더하는 symbol 선은 유리 중심선 1개뿐이어야 한다(예전: 문짝 1 + 호 8).
    기둥: 평면 절단 채움 다각형이 base 보다 늘고, 기둥 기호(대각선 2)만 더해진다. 배치가 실패했으면 판정하지 않는다."""
    res: dict[str, tuple[str, str]] = {}
    fam_p, base_p = out / "fam" / "plan-1F.svg", out / "base" / "plan-1F.svg"
    if not fam_p.exists() or not base_p.exists():
        return res
    fs, bs = fam_p.read_text(encoding="utf-8"), base_p.read_text(encoding="utf-8")
    fam_log = run.get("family", [])
    windows = sum(1 for e in fam_log if "window" in e and e["ok"])
    columns = sum(1 for e in fam_log if "column" in e and e["ok"])
    added_sym = _svg_group_count(fs, "lines-symbol", "line") - _svg_group_count(bs, "lines-symbol", "line")
    added_fill = _svg_group_count(fs, "fills", "polygon") - _svg_group_count(bs, "fills", "polygon")
    win_ok = windows > 0 and added_sym <= windows + 2 * columns       # 창마다 중심선 1, 기둥마다 대각선 2 까지만(호가 있으면 창마다 +9)
    col_ok = columns > 0 and added_fill >= columns
    if windows and columns:
        if win_ok and col_ok:
            res["L-02"] = (OK, f"벽 + 창 {windows}(창 기호: 벽 두께를 가로지르는 선, 호 없음) + 기둥 {columns}(절단 채움·대각선) 평면 표시. 추가 symbol 선 {added_sym}, 추가 채움 {added_fill}")
        else:
            res["L-02"] = (PART, f"창 호 없음={win_ok}, 기둥 채움={col_ok}(추가 symbol 선 {added_sym}, 추가 채움 {added_fill})")
        res["B-02"] = (PART, "창 호 오표시·기둥 미도시는 해소(L-02 근거). 남은 불일치: L 조인 모서리는 표시용 채움(S3 보고서 7절 2)") if win_ok and col_ok else (base["B-02"]["verdict"], "G1 판정 유지")
    return res



def _dim_texts(svg: str) -> list[str]:
    """뷰 SVG 의 치수(<g class="dim">) 글자들."""
    return re.findall(r'<g class="dim"[^>]*>.*?<text[^>]*>([^<]*)</text>', svg, re.S)


def _fmt(v: float) -> str:
    r = round(v)
    return str(int(r)) if abs(v - r) < 0.05 else f"{v:.1f}"


def _slab_probe_ok(probe: dict):
    """slab_probe 에서 (단면 뷰들, 입면 뷰들, 단면 절단 채움이 슬래브 절단 사각형 수만큼 늘었는가, 입면 선분이 늘었는가)."""
    secs = {k: v for k, v in probe["views"].items() if k.startswith("section") or k.startswith("xsec")}
    elevs = {k: v for k, v in probe["views"].items() if k.startswith("elev")}
    sec_ok = bool(secs) and all(v["fills_after"] - v["fills_before"] == v["slab_cut_rects"] >= 1 for v in secs.values())
    ev_ok = bool(elevs) and all(v["segments_after"] > v["segments_before"] for v in elevs.values())
    return secs, elevs, sec_ok, ev_ok


def judge_slab_height_items(out: Path, run: dict, base: dict):
    """슬래브 단면 표현과 높이 치수 항목을 판정한다. 반환: {id: (판정, 근거)}

    슬래브: run.json 의 slab_probe(슬래브를 더하기 전후의 뷰 채움·선분 수)에서 단면의 절단 채움이 슬래브 절단 사각형 수만큼 늘었는지 본다.
    높이: heights/<뷰>.svg 의 치수 글자에 층고·반자높이·최고높이 수치가 모두 있는지 본다(기대값은 run.json 의 heights.expected).
    반자높이는 천장 요소가 없어 슬래브 밑면까지의 안목 높이로 근사하므로 X-04 는 부분을 넘지 않는다."""
    res: dict[str, tuple[str, str]] = {}
    probe = run.get("slab_probe")
    if probe and probe.get("slabs"):
        secs, _, sec_ok, _ = _slab_probe_ok(probe)
        if sec_ok:
            detail = ", ".join(f"{k} 채움 +{v['fills_after'] - v['fills_before']}" for k, v in sorted(secs.items()))
            res["X-01"] = (OK, f"종단·횡단 단면에 슬래브 절단 사각형(절단선·채움, 벽 절단과 합집합 윤곽)이 표시된다: 슬래브 {len(probe['slabs'])}개, {detail}")
    hs = out / "heights"
    exp = run.get("heights", {}).get("expected")
    if exp and hs.exists():
        txt = {f.stem: _dim_texts(f.read_text(encoding="utf-8")) for f in sorted(hs.glob("*.svg"))}
        sec_names = [k for k in txt if k.startswith("section") or k.startswith("xsec")]
        floor = [_fmt(v) for v in exp["floor"]]
        clear = [_fmt(v) for v in exp["clear"]]
        overall = _fmt(exp["overall"]) if exp.get("overall") is not None else None
        has_all = lambda names, vals: bool(names) and all(all(v in txt[n] for v in vals) for n in names)   # noqa: E731
        f_ok = bool(floor) and has_all(sec_names, floor)
        o_ok = overall is not None and has_all(list(txt), [overall])
        c_ok = bool(clear) and has_all(sec_names, clear)
        res["X-03"] = (OK, f"단면 {len(sec_names)}장에 층고 치수 자동 배치({'·'.join(floor)})") if f_ok else (base["X-03"]["verdict"], "G1 판정 유지")
        res["X-02"] = (OK, f"단면·입면 {len(txt)}장에 최고 높이 치수 자동 배치({overall})") if o_ok else (base["X-02"]["verdict"], "G1 판정 유지")
        if c_ok:
            res["X-04"] = (PART, f"반자높이를 슬래브 밑면 기준 안목 높이({'·'.join(clear)})로 자동 표기. 천장 요소가 없어 천장 마감 높이는 아님")
    return res


def _svg_texts(path: Path, gid: str) -> list[str]:
    """SVG 의 <g id=gid> 그룹 안 <text> 글자들(없으면 빈 목록)."""
    if not path.exists():
        return []
    m = re.search(rf'<g id="{gid}"[^>]*>(.*?)</g>', path.read_text(encoding="utf-8"), re.S)
    return re.findall(r"<text[^>]*>([^<]*)</text>", m.group(1)) if m else []


def _svg_lines(path: Path, gid: str) -> int:
    return _svg_group_count(path.read_text(encoding="utf-8"), gid, "line") if path.exists() else 0


def stair_ok(run: dict):
    """(계단 평면 표시, 계단 단면 표시, 근거 문장). stair_probe 에서 평면 선분이 늘고 단면 채움이 계단 절단 사각형 수만큼 늘었는가."""
    p = run.get("stair_probe")
    if not p:
        return False, False, "계단 입력 없음"
    pv = p["views"].get("plan-1F")
    plan_ok = bool(pv) and pv["after"]["segments"] > pv["before"]["segments"] and pv["after"]["by_class"].get("symbol", 0) > pv["before"]["by_class"].get("symbol", 0)
    secs = {k: v for k, v in p["views"].items() if k.startswith("section") or k.startswith("xsec")}
    cut = {k: v for k, v in secs.items() if v["stair_cut_rects"] >= 1}
    sec_ok = bool(cut) and all(v["after"]["fills"] - v["before"]["fills"] == v["stair_cut_rects"] for v in cut.values())
    cut_txt = ", ".join("%s 채움 +%d" % (k, v["stair_cut_rects"]) for k, v in sorted(cut.items())) or "계단을 자르는 단면 없음"
    sym0, sym1 = (pv["before"]["by_class"].get("symbol", 0), pv["after"]["by_class"].get("symbol", 0)) if pv else (0, 0)
    why = (f"평면 1F: 선분 {pv['before']['segments']}->{pv['after']['segments']}(symbol {sym0}->{sym1}), 단면 {cut_txt}") if pv else "평면 1F 뷰 없음"
    return plan_ok, sec_ok, why


def judge_stair_space_items(out: Path, run: dict, base: dict):
    """L-04(복도·계단), L-03(방화구획·방화문), E-02(외부마감재료)를 판정한다. 반환: {id: (판정, 근거)}

    L-04: 계단이 평면에 표시되고(stair_probe) 복도로 분류된 공간의 실명 문자가 평면 SVG 의 문자 층에 있으면 충족. 하나만 되면 부분.
    L-03: FireRating 이 있는 벽의 중심선 파선(symbol 선 증가)과 'FW' 문자, 문의 'FD' 문자가 평면 SVG 에 있으면 충족(방화구획 면적 판정은 범위 밖).
    E-02: 입면 SVG 에 해치 선 그룹과 재료 이름 문자가 있으면 충족."""
    res: dict[str, tuple[str, str]] = {}
    plan = out / "base" / "plan-1F.svg"
    texts = _svg_texts(plan, "labels")
    plan_ok, _, sw = stair_ok(run)
    sp = run.get("space_probe")
    corridors = [s["name"] for s in run.get("spaces", []) if s["category"] in ("복도", "corridor", "hallway") and s["level"] == "1F"]
    corr_ok = bool(sp) and bool(corridors) and all(n in texts for n in corridors)
    if run.get("stair_probe") or sp:
        why = f"계단: {sw}. 공간 {len(run.get('spaces', []))}개 실명·면적 문자(평면 1F 문자 {len(texts)}개), 복도 문자 {'있음' if corr_ok else '없음'}({'·'.join(corridors) or '복도 분류 공간 없음'})"
        res["L-04"] = (OK if plan_ok and corr_ok else (PART if plan_ok or corr_ok else NO), why)
    fp = run.get("fire_probe")
    if fp:
        pv = fp["views"]["plan-1F"]
        added_sym = pv["after"]["by_class"].get("symbol", 0) - pv["before"]["by_class"].get("symbol", 0)
        fw = sum(1 for t in texts if t.startswith("FW "))
        fd = sum(1 for t in texts if t.startswith("FD "))
        ok_ = added_sym > 0 and fw >= fp["fire_walls"] and fd >= fp["fire_doors"]
        res["L-03"] = ((OK if ok_ else PART), f"평면 1F: 방화벽 중심선 파선(symbol 선 +{added_sym}), FW 문자 {fw}/{fp['fire_walls']}, 방화문 FD 문자 {fd}/{fp['fire_doors']}. 방화구획 면적 판정은 범위 밖")
    fin = run.get("finish_probe")
    if fin:
        elevs = sorted(k for k in fin["views"] if k.startswith("elev"))
        hatched = [k for k in elevs if fin["views"][k]["after"]["by_class"].get("hatch", 0) > 0]
        mats = set(fin["materials"])
        shown = {m for k in elevs for m in _svg_texts(out / "base" / f"{k}.svg", "labels") if m in mats}
        svg_h = [k for k in elevs if _svg_lines(out / "base" / f"{k}.svg", "lines-hatch") > 0]
        ok_ = len(hatched) == len(elevs) > 0 and shown == mats and len(svg_h) == len(elevs)
        res["E-02"] = ((OK if ok_ else PART), f"입면 {len(hatched)}/{len(elevs)}개에 외벽 해치(hatch 선 {sum(fin['views'][k]['after']['by_class'].get('hatch', 0) for k in elevs)}개), 재료 문자 {sorted(shown)}")
    # 열세 번째 작업부터 외부 마감 해치·재료 문자는 뷰의 finish 옵션을 켠 입면에만 그린다: 기본 입면은 해치가 없고 finish 입면(sheets13/elevfin-*)에만 있다
    fc = (run.get("after13") or {}).get("finish_check")
    if fc:
        sd = out / "sheets13"
        elevs = sorted(k for k in fc["views"] if k.startswith("elevfin"))
        plain = sorted(k for k in fc["views"] if k.startswith("elev-"))
        mats = set(fc["materials"])
        hatched = [k for k in elevs if fc["views"][k]["hatch"] > 0]
        shown = {m for k in elevs for m in _svg_texts(sd / f"{k}.svg", "labels") if m in mats}
        svg_h = [k for k in elevs if _svg_lines(sd / f"{k}.svg", "lines-hatch") > 0]
        plain_clean = bool(plain) and all(fc["views"][k]["hatch"] == 0 and not fc["views"][k]["labels"] for k in plain)
        ok_ = len(hatched) == len(elevs) == 4 and shown == mats and len(svg_h) == 4 and plain_clean
        res["E-02"] = ((OK if ok_ else PART), f"finish 옵션 입면 {len(hatched)}/{len(elevs)}개에 외벽 해치(hatch 선 {sum(fc['views'][k]['hatch'] for k in elevs)}개)와 마감 재료 문자 {sorted(shown)}. "
                                              f"옵션이 없는 기본 입면 {len(plain)}개는 해치·재료 문자가 없다({'확인' if plain_clean else '불일치'}: 벽 전체에 해치·구조체 이름을 깔지 않는다)")
    return res


def _group(svg: str, gid: str):
    """<g id=gid ...> 한 그룹(중첩 없는 그룹)의 (속성 문자열, 안쪽)."""
    m = re.search(rf'<g id="{gid}"([^>]*)>(.*?)</g>', svg, re.S)
    return (m.group(1), m.group(2)) if m else None


def _gtexts(svg: str, gid: str) -> list[str]:
    """같은 id 의 그룹이 여러 개(뷰마다)일 수 있다: 모두 모아 글자를 낸다."""
    out: list[str] = []
    for m in re.finditer(rf'<g id="{gid}"[^>]*>(.*?)</g>', svg, re.S):
        out += re.findall(r"<text[^>]*>([^<]*)</text>", m.group(1))
    return out


def _gsum(svg: str, gid: str, attr: str = "data-count") -> int:
    return sum(int(x) for x in re.findall(rf'<g id="{gid}"[^>]*{attr}="(\d+)"', svg))


def _gattr(svg: str, gid: str, attr: str):
    g = _group(svg, gid)
    m = re.search(rf'{attr}="([^"]*)"', g[0]) if g else None
    return m.group(1) if m else None


def judge_sheet2_items(out: Path, run: dict, base: dict):
    """다섯 번째 작업 항목(색인 시트·확장 시트·작성기준 프로파일)을 산출 파일에서 직접 판정한다. 산출이 없으면 이전 판정 그대로(빈 결과).
    항목 정의대로 도면에 실제로 보일 때만 충족으로 둔다: 입력이 없어 못 하는 부분이 있으면 부분으로 남기고 이유를 적는다. 반환: {id: (판정, 근거)}"""
    res: dict[str, tuple[str, str]] = {}
    sd = out / "sheets"
    f2, f0, f3, fa3 = sd / "A-002.svg", sd / "A-000.svg", sd / "A-003.svg", sd / "A-101.svg"
    if not (f2.exists() and f0.exists()):
        return res
    s2, s0 = f2.read_text(encoding="utf-8"), f0.read_text(encoding="utf-8")
    exports = [e for e in run.get("exports", []) if e.get("number") and e["ext"] == "svg"]
    # ---- 표제란·도면목록·서명·개정
    tb2, tb0 = _group(s2, "title-block"), _group(s0, "title-block")
    need = ("프로젝트명", "건축주", "설계사무소", "도면명", "도면번호", "축척", "작성일")
    if tb2 and tb0 and all(k in s2 and k in s0 for k in need):
        res["T-02"] = (OK, "표제란에 프로젝트명·건축주·설계사무소·도면명·도면번호·축척·작성일·개정 영역(확장 시트 A-002, 색인 시트 A-000)")
    revs_n = int(_gattr(s2, "revision-table", "data-count") or 0)
    if "개정차수" in s2 and revs_n >= 1 and all(k in s2 for k in ("도면명", "도면번호", "축척", "작성일")):
        res["T-03"] = (OK, f"표제란에 도면명·도면번호·축척·작성일·개정차수 칸, 개정 {revs_n}건 기재")
    cloud = re.search(r'<g id="revision-cloud"[^>]*>\s*<polygon points="([^"]+)"', s2)
    if revs_n >= 1 and cloud and len(cloud.group(1).split()) > 40:
        res["T-08"] = (OK, f"개정란에 차수·일자·내용 {revs_n}건 + 개정한 뷰를 둘러싼 구름 표시(점 {len(cloud.group(1).split())}개)와 차수 삼각형")
    sig_n = int(_gattr(s2, "signature-block", "data-count") or 0)
    sg = _gtexts(s2, "signature-block")
    if sig_n >= 2 and "(서명)" in sg and "(인)" in sg:
        res["T-06"] = (OK, f"표제란에 서명·날인 영역({sig_n}줄: 역할·성명·서명·인). 서명·날인 자체는 사람이 한다(영역만 제공)")
    listed = run.get("drawing_list", [])
    dl_rows = int(_gattr(s0, "drawing-list", "data-rows") or 0)
    numbers = {e["number"] for e in exports}
    dl_texts = _gtexts(s0, "drawing-list")
    if listed and dl_rows == len(listed) and {d["number"] for d in listed} == numbers and all(d["number"] in dl_texts for d in listed) and "건축" in dl_texts:
        res["T-01"] = (OK, f"색인 시트(A-000)에 도면목록표 {dl_rows}행(도면번호·도면명·공종·축척), 공종 '건축', 목록이 낸 시트 {len(numbers)}장과 일치. "
                           "색인 시트가 프로젝트·건축주·설계사무소를 적은 표지 역할")
    checks = [e["number_check"] for e in exports if e.get("number_check")]
    if checks and all(c["format_ok"] and c["file_match"] for c in checks) and res.get("T-01", (None,))[0] == OK:
        res["T-04"] = (OK, f"도면번호 {len(checks)}장 모두 '공종문자-3자리' 체계에 맞고 파일명과 일치, 도면목록표와 일치")
    # ---- 범례·키 플랜·기호
    lg = _gtexts(s0, "legend")
    nt = _gtexts(s0, "notes")
    if lg and nt and all(any(k in t for t in lg) for k in ("1점쇄선", "파선", "해치", "FW", "FD")):
        res["T-09"] = (OK, f"색인 시트에 범례(선 종류·해치·기호·약어 {len(lg)}줄)와 주기 {_gattr(s0, 'notes', 'data-lines')}줄")
        dashed = "stroke-dasharray" in s2 and _group(s2, "grid-lines") is not None
        res["LN-02"] = (OK, "범례가 실선·파선·1점쇄선의 용도를 밝히고, 도면에는 실선·파선(레벨 기준선)·1점쇄선(통심선·절단선)이 실제로 쓰였다") if dashed else (PART, "범례는 있으나 도면에 선 종류가 다 쓰이지 않았다")
    kp = _group(s2, "key-plan")
    if kp and len(re.findall(r'stroke="#c62828"', kp[1])) >= 5:
        res["T-10"] = (OK, f"표제란 오른쪽 위 키 플랜(평면 축소) + 해당 영역 해칭(영역 {_gattr(s2, 'key-plan', 'data-zone')})")
    marks = {g: int(_gattr(s2, g, "data-count") or 0) for g in ("section-markers", "elevation-markers", "detail-callouts")}
    if all(v >= 1 for v in marks.values()):
        res["T-12"] = (OK, f"평면에 단면 절단선 기호 {marks['section-markers']}개, 입면 방향 기호 {marks['elevation-markers']}개, 부위 상세 표시(1점쇄선 + 부호) {marks['detail-callouts']}개")
    rooms = _gsum(s2, "room-numbers")
    if rooms:
        res["T-13"] = (PART, f"실번호 {rooms}개(좌상단부터 시계방향)와 실 중앙의 실명은 있다. 좁은 실의 인출선은 없다")
    # ---- 창호·벽체·재료 부호와 일람표
    def qty(gid, col, ncols):
        t = _gtexts(s0, gid)
        body = t[ncols + 1:]                       # 제목 1 + 머리글 ncols 개 이후가 행
        return [body[i:i + ncols] for i in range(0, len(body), ncols)]
    op_rows = qty("opening-schedule", 8, 8)
    wl_rows = qty("wall-schedule", 7, 7)
    op_marks = {r[0] for r in op_rows}
    op_qty = sum(int(r[6]) for r in op_rows if len(r) == 8)
    wl_marks = {r[0] for r in wl_rows}
    wl_qty = sum(int(r[5]) for r in wl_rows if len(r) == 7)
    op_tags = _gtexts(s2, "opening-tags")
    wl_tags = _gtexts(s2, "wall-tags")
    if op_rows:
        res["OP-03"] = (PART, f"창호 일람표 {len(op_rows)}행(부호·구분·폭·높이·하단높이·내화·수량·설치 층). 창호 상세 도면은 없다")
    if op_rows and op_tags and len(op_tags) == op_qty and set(op_tags) <= op_marks:
        res["OP-04"] = (OK, f"평면에 문·창 부호 {len(op_tags)}개({'·'.join(sorted(set(op_tags)))}), 일람표 수량 합 {op_qty}과 일치하고 부호가 일람표에 있다")
    if wl_rows and wl_tags and len(wl_tags) == wl_qty and set(wl_tags) <= wl_marks:
        res["OP-05"] = (OK, f"평면의 모든 벽 {len(wl_tags)}개에 벽체 부호({'·'.join(sorted(set(wl_tags)))}), 벽체 일람표 수량 합 {wl_qty}과 일치")
    mt = int(_gattr(s0, "material-schedule", "data-rows") or 0)
    if mt:
        res["MT-01"] = (PART, f"재료표 {mt}행에 재료(품명)·규격(두께)·내화 성능. 질감·색상·성능 세부는 모델에 입력이 없다")
        res["MT-03"] = (PART, f"외벽·내벽 마감 재료표 {mt}행. 실내 마감(바닥·벽·천장)은 모델에 입력이 없어 표에 없다")
        res["MT-04"] = (PART, "재료명(모델 입력 그대로)과 규격(두께 mm) 병기. 시방서 용어와의 일치는 검증할 수 없다")
    lv = int(_gattr(s0, "level-note", "data-rows") or 0)
    if lv and "±0" in _gtexts(s0, "level-note"):
        res["N-05"] = (PART, f"색인 시트 레벨 노트에 층별 FL과 GL 대비 값 {lv}행. 평면도 위 노트가 아니고 GL 은 입력 레벨(기본 최저 레벨)이다")
    grids = run.get("grids", [])
    gl = _group(s2, "grid-lines")
    if gl and int(_gattr(s2, "grid-lines", "data-count") or 0) == len(grids) and set(g["label"] for g in grids) <= set(_gtexts(s2, "grid-lines")):
        res["N-09"] = (OK, f"평면에 통심선(1점쇄선) {len(grids)}개와 양 끝 그리드 기호(원 + 번호: {'·'.join(g['label'] for g in grids)})")
    # ---- 실의 용도·면적(이미 그리던 것)
    plan = out / "base" / "plan-1F.svg"
    sp1 = [x for x in run.get("spaces", []) if x["level"] == "1F"]
    if sp1 and plan.exists():
        t = _svg_texts(plan, "labels")
        names_ok = all(x["name"] in t for x in sp1)
        areas = [x for x in t if x.endswith("㎡")]
        if names_ok and len(areas) >= len(sp1):
            res["L-08"] = (OK, f"평면 1F 공간 {len(sp1)}개 모두 실명(거실·침실 등 용도)과 면적(㎡) 문자 {len(areas)}개")
    # ---- BIM 지침: 작성기준 프로파일
    pf = out / "profile.json"
    if pf.exists():
        prof = json.loads(pf.read_text(encoding="utf-8"))
        ov = prof.get("line_overlap", {})
        if ov.get("priority") and ov.get("rule") and prof.get("line_weights"):
            res["B-06"] = (OK, f"작성기준 프로파일(drawing.profile)이 선 클래스 우선순위 {ov['priority']}와 겹침 규칙(병합·절단선 우선·그리는 순서 {ov.get('draw_order')})을 명기")
        sy = {x["id"] for x in prof.get("symbols", [])}
        if {"door", "window", "column", "stair", "fire_wall", "fire_door", "space", "grid", "section_mark"} <= sy:
            res["B-07"] = (OK, f"프로파일에 평면 심볼 {len(sy)}종(문·창·기둥·계단·방화·공간·통심선·단면 기호 등) 정의")
        keys = ("paper_size", "layout_orientation", "north_level", "title_block", "scale", "line_weights", "hatch", "text_font", "dimension_leader", "symbols")
        if all(k in prof for k in keys):
            res["B-08"] = (OK, "프로파일이 도면 크기·배치·방향·방위·레벨·표제란·축척·선 굵기·선·해칭·문자·폰트·치수·지시선·기호 10개 기준 항목을 값으로 명기")
    if f3.exists() and fa3.exists():
        c3 = len(re.findall(r'stroke-width="0.35"', f3.read_text(encoding="utf-8")))
        ca = len(re.findall(r'stroke-width="0.35"', fa3.read_text(encoding="utf-8")))
        if c3 > ca + 5:
            res["B-09"] = (PART, f"별도 작성기준을 선 굵기 프로파일로 받아 적용할 수 있다(A-003: 절단선 0.35mm 선 {c3}개, 기본 시트 {ca}개). 문자·기호·치수 기준은 입력 경로가 없다")
    return res


def _sizes(svg: str, text: str) -> set:
    """SVG 에서 이 글자를 그린 <text> 의 글자 높이(font-size) 집합."""
    return set(re.findall(rf'<text[^>]*font-size="([\d.]+)"[^>]*>{re.escape(text)}</text>', svg))


def judge_after7_items(out: Path, run: dict, base: dict):
    """일곱 번째 작업 항목을 sheets7/ 산출(A-004: 1:50 평면 + 프로파일 + 폭 치수, A-005: 입면 치수)과 같은 인자의 기본 시트(-default) 대비로 판정한다.
    B-09(작성기준 프로파일: 문자 높이·치수 형식·기호 크기), LN-05(문자 높이), DM-10(천단위 쉼표), SC-09(축척별 상세), E-04(입면 치수), L-09(복도·계단 폭)."""
    res: dict[str, tuple[str, str]] = {}
    a7, sd = run.get("after7"), out / "sheets7"
    names = ("A-004.svg", "A-004-default.svg", "A-005.svg", "A-005-default.svg")
    if not a7 or not all((sd / n).exists() for n in names) or not (out / "profile-applied.json").exists():
        return res
    s4, s4d, s5, s5d = ((sd / n).read_text(encoding="utf-8") for n in names)
    eff = json.loads((out / "profile-applied.json").read_text(encoding="utf-8"))
    th = a7["profile"]["text_height"]
    k = a7["profile"]["symbol_scale"]
    # 문자 높이: 같은 글자를 기본 시트는 기본 높이로, 프로파일 시트는 지정한 높이로 그린다
    txt = {"dimension": "12,000", "room_name": "거실", "caption": "1층 평면도 1:50"}
    txt_default = {"dimension": "12000", "room_name": "거실", "caption": "1층 평면도 1:50"}
    got = {c: (_sizes(s4, txt[c]), _sizes(s4d, txt_default[c])) for c in txt}
    heights_ok = all(f"{th[c]:g}" in got[c][0] and f"{th[c]:g}" not in got[c][1] for c in txt)
    area_ok = any(f"{th['room_area']:g}" in _sizes(s4, t) for t in re.findall(r"<text[^>]*>([^<]*㎡)</text>", s4))
    r_new = {f"{3.3 * k:g}"}
    circ = set(re.findall(r'<circle[^>]*r="([\d.]+)"', s4)) >= r_new and not (set(re.findall(r'<circle[^>]*r="([\d.]+)"', s4d)) & r_new)
    fmt_ok = eff["dimension_leader"]["number_format"]["thousands_separator"] is True and eff["text_font"]["heights_mm"]["dimension"] == th["dimension"]
    if heights_ok and area_ok and circ and fmt_ok and a7["empty_args_equal_default"]:
        res["B-09"] = (OK, f"별도 작성기준(profile)을 sheet.export·drawing.profile 입력으로 받아 시트에 적용: 문자 높이(치수 {th['dimension']}·실명 {th['room_name']}·면적 {th['room_area']}·도면명 {th['caption']}mm), "
                           f"치수 형식(천단위 쉼표), 기호 크기(배율 {k}: 그리드 원 반지름 3.3→{3.3 * k:g}). 선 굵기 프로파일(A-003)과 함께 선·문자·치수·기호 4가지를 외부 기준으로 받는다. 주지 않거나 비워 주면 기본 시트와 바이트까지 같다")
        res["LN-05"] = (OK, "drawing.profile 이 문자 높이를 용도별로 값으로 정하고(실명·면적·치수·레벨·도면명·방화 표기, 기호 안 문자) 시트에 그 높이로 그린다: 지정 높이가 시트에 실제로 쓰임(A-004). 기본값은 기존 체계 그대로")
    if "12,000" in s4 and "12,000" in s5 and "12000" in s4d and "12,000" not in s4d:
        res["DM-10"] = (OK, "작성기준 프로파일 dim_thousands 를 켜면 네 자리 이상 치수 문자가 세 자리마다 쉼표(예: 12,000)로 그려진다(A-004·A-005). 기본은 쉼표 없음(기존 도면 바이트 불변)")
    dl = a7["detail_lines"]
    if dl["added"] >= 20 and s4.count("<line") >= s4d.count("<line") + dl["added"] - 2:
        res["SC-09"] = (PART, f"1:50 평면에서 표현 수준을 높인다(A-004): 재료가 있는 벽의 마감선·문짝 두께·문턱선 {dl['added']}개 선을 더한다(1:100 은 그대로). "
                              "천장선(천장 요소 없음)·창 프레임(창 상세 없음)·단면·입면 상세는 아직 없다. 마감 두께 20·문짝 두께 40mm 는 모델에 마감 층이 없어 규약값")
    pe = a7["elev_dims"]
    want = sum(len(pe[k_]["chain"]) + 1 for k_ in ("elev-S", "elev-E"))
    if _gsum(s5, "elevation-dims") == want and "elevation-dims" not in s5d and all(pe[k_]["overall"][2] in ("12000", "8000") for k_ in ("elev-S", "elev-E")):
        res["E-04"] = (OK, f"입면 2개(A-005)에 벽 중심선 위치 사슬과 전체 폭 치수 {want}개(남측 전체 {pe['elev-S']['overall'][2]}·동측 {pe['elev-E']['overall'][2]}mm). dims=['elevation'] 로 켠다")
    pd = a7["plan_dims"]
    if _gsum(s4, "corridor-dims") == len(pd["corridor"]) >= 1 and _gsum(s4, "stair-dims") == len(pd["stair"]) >= 1 and "corridor-dims" not in s4d:
        c, st = pd["corridor"][0][4], pd["stair"][0][4]
        res["L-09"] = (OK, f"평면(A-004)에 복도 폭 {c}mm(안목)와 계단 폭 {st}mm 치수. 복도·계단 위치는 평면에 그려진 공간·계단 그대로다. dims=['corridor','stair'] 로 켠다")
    return res


def _texts(svg: str) -> list[str]:
    return re.findall(r"<text[^>]*>([^<]*)</text>", svg)


def judge_after8_items(out: Path, run: dict, base: dict):
    """여덟 번째 작업 항목을 sheets8/ 산출(A-006 배치도, A-007 입면, A-000b 도면목록)과 같은 인자의 기본 시트(-default)·내보낸 IFC 독립 검산 대비로 판정한다.
    배치도: 법정 P-01(축척·방위)·P-02(접한 도로 길이·너비)·P-04(이격거리), 제도·관행 D-01(배치도 도서)·N-06(표고 단위)·N-08(고저차), BIM B-05.
    선 위계: LN-03(숨은 선)·LN-04(굵기 위계)·T-11(제3각법). 근거 없는 충족은 만들지 않는다: 산출이 없으면 빈 결과(이전 판정 그대로)."""
    res: dict[str, tuple[str, str]] = {}
    a8, sd = run.get("after8"), out / "sheets8"
    if not a8 or not (sd / "A-007.svg").exists() or not (sd / "A-007-default.svg").exists():
        return res
    s7, s7d = (sd / "A-007.svg").read_text(encoding="utf-8"), (sd / "A-007-default.svg").read_text(encoding="utf-8")
    eff = json.loads((out / "profile-applied8.json").read_text(encoding="utf-8")) if (out / "profile-applied8.json").exists() else {}
    # ---- 숨은 선 LN-03: 단면·입면의 가려진 모서리가 파선 그룹으로 나오고, 기본 시트에는 없고, 꺼 둔 값은 기본과 바이트가 같다
    n_hid = _gsum(s7, "hidden-lines")
    want = a8["hidden"]["elev-S"] + a8["hidden"]["elev-N"]
    body = "".join(m.group(1) for m in re.finditer(r'<g id="hidden-lines"[^>]*>(.*?)</g>', s7, re.S))
    dashed = len(re.findall(r'<line [^>]*stroke-dasharray="2.4 1.2"', body))
    if n_hid == want >= 1 and dashed == n_hid and "hidden-lines" not in s7d and a8["empty_args_equal_default"] and a8["hidden"]["plan-1F"] == 0:
        res["LN-03"] = (OK, f"입면 2개(A-007)에 앞의 벽에 가려진 모서리·개구부 윤곽 {n_hid}개를 파선(2.4/1.2mm, 보이는 선과 같은 굵기)으로 그린다. 보이는 선과 겹치는 부분은 뺀다(실선 우선). "
                            "profile.hidden_lines 로 켜며 기본·꺼 둔 값은 기본 시트와 바이트까지 같다. 한계: 단면·입면만(평면의 절단면 위 요소 파선은 없다)")
    # ---- 굵기 위계 LN-04: 치수선·보조선이 위계 값으로 바뀐다(기본 0.13 < 보이는 선 0.18 -> 치수선 0.25 = 기호선, 보조선 0.18 = 보이는 선). 비내력벽은 구분 속성이 없다
    wd = lambda svg: sorted({float(w) for c, w in re.findall(r'stroke="(#[0-9a-f]{6})" stroke-width="([\d.]+)"', svg) if c == "#1a4fa0"})   # noqa: E731
    tiers = (eff.get("line_hierarchy") or {}).get("tiers", [])
    tw = [t["width_mm"] for t in tiers]
    if tiers and tw == sorted(tw, reverse=True) and len(set(tw)) == len(tw) and wd(s7) == [0.18, 0.25] and 0.13 in wd(s7d):
        res["LN-04"] = (PART, f"선 굵기 위계를 값으로 정하고(drawing.profile line_hierarchy: 절단선 {tw[0]:g} > 창호·기호선·치수선 {tw[1]:g} > 보이는 선·보조선 {tw[2]:g} > 가는선·해칭 {tw[3]:g}mm) "
                             f"시트가 그대로 그린다(A-007 치수선 {wd(s7d)} -> {wd(s7)}). 기본은 치수선이 보이는 선보다 가늘다. 비내력벽을 구분하는 속성이 없어 모든 벽의 절단선이 같은 굵기라 부분")
    # ---- 배치도·대지
    st = a8.get("site")
    if st and all((sd / n).exists() for n in ("A-006.svg", "A-006-default.svg", "A-000b.svg")):
        s6, s6d, s0 = ((sd / n).read_text(encoding="utf-8") for n in ("A-006.svg", "A-006-default.svg", "A-000b.svg"))
        vt = _gtexts(s6, f"view-{st['view']}")
        t0 = _texts(s0)
        sc = f"S=1:{st['scale']}"
        deg = _gattr(s6, "north-symbol", "data-north-deg")
        if sc in vt and "N" in vt and deg is not None and float(deg) == st["north_angle"]:
            res["P-01"] = (OK, f"배치도 시트(A-006)에 축척({sc}: 뷰 문자·도면명·표제란 축척 칸·축척 막대)과 방위(뷰 안 방위표 N, 정북 각 {st['north_angle']:g}도가 표제란 방위표 data-north-deg 와 같다)가 표시된다. N-01 은 같은 항목")
        rd = st["roads"]
        if rd and all(f"접도 길이 {int(r['length'])}" in vt and f"{r['name']} 폭 {int(r['width'])}" in vt for r in rd):
            res["P-02"] = (OK, "접한 도로 " + ", ".join(f"{r['name']}(접도 길이 {int(r['length'])}·폭 {int(r['width'])}mm)" for r in rd) + "를 배치도에 띠로 그리고 이름·폭·접도 길이 문자와 폭 치수를 단다. 길이는 대지 경계 변 길이, 폭은 입력값")
        mm = sorted(round(x["mm"]) for x in st["setbacks"])
        ifc = sorted(round(x) for x in a8["ifc_check"]["setbacks_mm"] if x is not None)
        if len(mm) == len(st["boundary"]) and mm == ifc and all(str(v) in vt for v in mm):
            res["P-04"] = (OK, f"대지 경계 {len(mm)}개 변마다 건물까지 이격 치수({', '.join(str(v) for v in mm)}mm)를 배치도에 단다. 내보낸 IFC 의 대지 경계 폴리라인과 벽·슬래브 형상 정점에서 다시 잰 값과 같다(엔진 코드와 독립 검산). "
                             "건축선은 입력 개념이 없어 대지 경계선 기준이다")
        if "projection-symbol" not in s6d and 'data-method="third-angle"' in s6 and "제3각법" in _texts(s6):
            res["T-11"] = (OK, "profile.projection='third' 를 주면 표제란에 투상법 칸(글 '제3각법' + 원뿔대 정면·측면 기호, data-method=third-angle)이 들어간다(A-006). 주지 않으면 칸이 없다(기본 시트와 바이트 동일)")
        if "A-006" in t0 and "배치도" in t0:
            res["D-01"] = (OK, "배치도 도서(A-006)를 내고 도면목록표(A-000b)에 배치도 행(번호 A-006·축척 1:200)을 적는다")
        el = f"대지 표고 EL {st['elevation'] / 1000:+.3f}m"
        if el in vt and "FL(mm)" in t0:
            res["N-06"] = (OK, f"대지 표고는 m 단위(배치도 '{el}')로, 건물 레벨은 mm 단위(레벨 노트 FL(mm) 열)로 표기한다")
            res["N-08"] = (PART, f"배치도에 대지 표고 한 값('{el}')만 있다. 대지의 고저차(경사·등고선·여러 점 표고)는 입력이 없어 표시하지 못한다")
        rt = a8["roundtrip"]
        if all(rt["hash_equal"]) and rt["origin"] == "authored" and rt["views"] == [st["view"]] and not rt["warnings"] and a8["ifc_check"]["parent"] == "Site":
            res["B-05"] = (PART, "배치도(A-006)가 더해져 배치·평면·입면·단면 4종을 BIM 도면(모델 파생)으로 낸다. 대지는 IfcSite 로 왕복한다(해시 3종 동일, 경고 0). 수직동선 상세 도면(계단 상세 등)은 아직 없다")
    return res


def judge_after9_items(out: Path, run: dict, base: dict):
    """아홉 번째 작업 항목을 sheets9/ 산출(A-008 1:50 평면, A-009 1:50 종단면, A-010 1:50 입면)과 같은 인자의 기본 시트(-default)·내보낸 IFC 독립 검산 대비로 판정한다.
    SC-09(축척별 표현 상세)·B-17(1/50 수준) = 평면 층 경계선·문짝 두께 + 단면 벽·슬래브 층 경계선·층별 해치·천장선 + 입면 창대,
    X-04(반자높이) = 천장 높이 입력 기준 치수, LN-04(굵기 위계) = 비내력벽 절단선. 근거가 하나라도 없으면 판정을 만들지 않는다(이전 판정 그대로)."""
    res: dict[str, tuple[str, str]] = {}
    a9, sd = run.get("after9"), out / "sheets9"
    names = ("A-008", "A-008-default", "A-009", "A-009-default", "A-010", "A-010-default")
    if not a9 or not all((sd / f"{n}.svg").exists() for n in names):
        return res
    s8, s8d, s9, s9d, s10, s10d = ((sd / f"{n}.svg").read_text(encoding="utf-8") for n in names)
    dl, ck, rt = a9["detail_lines"], a9["ifc_check"], a9["roundtrip"]
    nl = lambda svg: svg.count("<line")   # noqa: E731
    add = {k: sum(v["added"].values()) for k, v in dl.items()}
    plan_ok = add["plan"] >= 10 and nl(s8) >= nl(s8d) + add["plan"] - 2
    sec_ok = (dl["section"]["added"].get("hatch", 0) >= 20 and dl["section"]["added"].get("proj", 0) == len(a9["ceiling_lines"]) >= 1
              and nl(s9) >= nl(s9d) + add["section"] - 2)
    elev_ok = add["elev_e"] + add["elev_n"] >= 1 and nl(s10) >= nl(s10d) + add["elev_e"] + add["elev_n"] - 2
    same = all(v["empty_args_equal_default"] for v in a9["sheets"].values())
    ifc_ok = ck["wall_thickness_equal"] and ck["offset_is_first_face"] and ck["slab_thickness_equal"] and ck["layer_sets"] >= 1
    inp = a9["inputs"]
    rt_ok = (all(rt["hash_equal"]) and not rt["warnings"] and rt["wall_layers_back"] and rt["wall_origin"] == "authored"
             and rt["door_leaf_back"] == inp["door_leaf"]["D-01"] and rt["ceiling_back"] == inp["ceiling_height"])
    if plan_ok and sec_ok and elev_ok and same and ifc_ok and rt_ok:
        hatch_n = dl["section"]["added"].get("hatch", 0)
        why = (f"1:50 상세를 모델 입력으로 그린다. 평면(A-008): 층 구성이 있는 벽 {inp['ext_walls'] + inp['int_walls']}개의 층 경계선과 문짝 두께(입력 {inp['door_leaf']['D-01']:g}mm) 선 {add['plan']}개. "
               f"단면(A-009): 벽·슬래브({inp['slabs']}개)의 층 경계선과 층별 절단 해치, 천장 높이 {inp['ceiling_height']:g}mm 의 천장선 {len(a9['ceiling_lines'])}개(해치·경계선 {hatch_n}개). "
               f"입면(A-010): 창대 {add['elev_e'] + add['elev_n']}개(창 프레임 폭·유리·멀리언은 창 패밀리 형상이라 기본 입면이 이미 그린다). "
               f"입력은 IFC 층 구성(IfcMaterialLayerSetUsage)으로 왕복하고(해시 3종 동일, 경고 0) 내보낸 IFC 형상 두께와 층 두께 합이 벽 {len(ck['walls'])}개·슬래브 {len(ck['slabs'])}개 모두 같다(엔진 코드와 독립 검산). "
               "입력이 없거나 1:100 이거나 detail 을 안 켜면 기본 시트와 바이트까지 같다. 한계: 층 해치 무늬는 재료 이름으로 정해지는 3종(해칭 유형 규정 LN-07 은 아님)이고 용지 위 1.5mm 미만의 얇은 층은 경계선만 긋는다")
        res["SC-09"] = (OK, why)
        res["B-17"] = (OK, "조달 도면의 1/50 수준 표현: 큰 축척(1:50) 평면·단면·입면이 층 구성(마감 층 포함)·문짝 두께·슬래브 두께 층·천장선·창대를 그린다(SC-09 근거 참조). 작은 축척 도면은 그대로")
    heights = a9["heights"]["dims"]
    clear = [d for d in heights if d["kind"] == "clear"]
    t9 = _texts(s9)
    if clear and all(d["b"].startswith("space:") for d in clear) and all(_fmt(d["value"]) in t9 for d in clear) and all(abs(d["value"] - inp["ceiling_height"]) < 1e-6 for d in clear):
        res["X-04"] = (OK, f"반자높이를 공간의 천장 마감 높이 입력(space.ceiling_height {inp['ceiling_height']:g}mm)까지의 치수({'·'.join(_fmt(d['value']) for d in clear)})로 자동 표기한다(A-009, 참조 space:id#ceiling). "
                           "천장 입력이 없는 레벨은 이전처럼 슬래브 밑면 근사. 한 레벨에 천장 높이가 다른 공간이 여럿이면 가장 낮은 천장을 쓴다")
    cc = a9["cut_classes"]["plan"]
    n_nb = cc["hier"].get("cutn", 0)
    if n_nb >= 1 and _gsum(s8, "cut-nonbearing") == n_nb and "cut-nonbearing" not in s8d and a9["sheets"]["A-008"]["empty_args_equal_default"] and cc["plain"].get("cutn", 0) == 0:
        res["LN-04"] = (OK, f"선 굵기 위계(profile.line_hierarchy)에서 Pset_WallCommon.LoadBearing 이 명시적으로 거짓인 벽({inp['load_bearing']['false']}개)의 절단선 {n_nb}개를 한 단계 가는 0.35mm 로 그린다"
                            f"(내력벽 {inp['load_bearing']['true']}개·값 없는 벽은 절단선 0.50). 절단선 0.50 > 비내력벽 절단선 0.35 > 창호·기호선·치수선 0.25 > 보이는 선·보조선 0.18 > 가는선·해칭 0.09. "
                            "위계를 켜지 않으면 기본 시트와 바이트까지 같다(A-008). 한계: LoadBearing 값이 없는 벽은 구분하지 않는다")
    return res


def judge_after11_items(out: Path, run: dict, base: dict):
    """열한 번째 작업(승강기·지붕): sheets11/ 의 평면 1F·2F SVG 에서 승강기 표시(EV 문자, 샤프트·카 윤곽)를 직접 확인해 L-05(법정)·VT-02 를 판정한다.
    내보낸 IFC 의 IfcTransportElement(ELEVATOR)와 해시 3종 왕복 일치도 근거로 요구한다. 산출이 없으면 빈 결과."""
    res: dict[str, tuple[str, str]] = {}
    a = run.get("after11")
    sd = out / "sheets11"
    if not a or not (sd / "plan-1F.svg").exists():
        return res
    texts = {k: _texts((sd / f"{k}.svg").read_text(encoding="utf-8")) for k in ("plan-1F", "plan-2F")}
    rt, ifc = a["roundtrip"], a["ifc_check"]
    if (all("EV" in t for t in texts.values()) and a["plan-1F"]["ev_labels"] == 1 and a["plan-2F"]["ev_labels"] == 1 and all(rt["hash_equal"]) and not rt["warnings"]
            and ifc["transport"] == [["IfcTransportElement", "ELEVATOR"]] and a["elevator"]["cut_slabs"]):
        res["L-05"] = (OK, "승강기(EV-1, 샤프트 안쪽 1800x1800 + 카)가 1층·2층 평면에 샤프트·카 윤곽과 대각 X, 'EV' 문자로 표시된다(지나는 모든 층). "
                           "2층 슬래브의 샤프트 구멍을 자동으로 내고 IFC 는 IfcTransportElement(ELEVATOR)로 나가 왕복한다(해시 3종 동일, 경고 0)")
    return res


def judge_after12_items(out: Path, run: dict, base: dict):
    """열두 번째 작업(대지 선택 속성): sheets12/ 의 배치도·입면·대지 단면 산출을 직접 읽어 법정 P-03(대지 종·횡단면)·P-05(주차동선·옥외주차)·P-06(공개공지·조경)·E-03(간판·번호판)과
    제도·관행 N-08(배치도 고저차)을 판정한다. 근거: 산출 SVG 의 문자·선 그룹, 내보낸 IFC 의 DoBIM_Intent 를 엔진 코드와 독립으로 다시 잰 값, 지반선 높이 독립 재계산, 왕복 해시.
    산출이 없으면 빈 결과."""
    res: dict[str, tuple[str, str]] = {}
    a = run.get("after12")
    sd = out / "sheets12"
    if not a or not (sd / "site-plan.svg").exists():
        return res
    rd = lambda n: (sd / f"{n}.svg").read_text(encoding="utf-8")   # noqa: E731
    sp, es, en, sl, st = (rd(n) for n in ("site-plan", "elev-S", "elev-N", "site-section-long", "site-section-trans"))
    ts, tsp, tes, ten, tsl, tst = (_texts(x) for x in (rd("site-plan"), sp, es, en, sl, st))
    inp, ifc, rt, cc = a["inputs"], a["ifc_check"], a["roundtrip"], a["clear_check"]
    nlines = lambda svg, gid: len(re.findall(r"<line ", "".join(m.group(1) for m in re.finditer(rf'<g id="{gid}"[^>]*>(.*?)</g>', svg, re.S))))   # noqa: E731
    exact = all(rt["hash_equal"]) and not rt["warnings"] and rt["site_params_equal"] and cc["views_equal_plain"] and all(cc["hash_equal_before"])
    zones = inp["zones"]
    zt = [f"{z['name'] or ('조경' if z['use'] == 'landscape' else '공개공지')} {z['area_m2']:.2f}㎡" for z in zones]
    if (all(t in tsp for t in zt) and any(x.startswith("조경 면적 합계") for x in tsp) and any(x.startswith("공개공지 면적 합계") for x in tsp) and nlines(sp, "lines-hatch") >= 8
            and [round(z["area_m2"], 4) for z in zones] == ifc["zones_m2"] and exact):
        res["P-06"] = (OK, f"배치도에 조경 '{zones[0]['name']}'({zones[0]['area_m2']:.0f}㎡)·공개공지({zones[1]['area_m2']:.0f}㎡) 영역을 해치(조경 45도·공개공지 135도 {nlines(sp, 'lines-hatch')}선)와 면적 문자로 그리고 "
                         "종류별 면적 합계·대지면적 대비 %를 단다. 내보낸 IFC 의 의도에서 신발끈 공식으로 다시 잰 면적과 같다(엔진 코드와 독립). 새 입력을 지우면 뷰·해시가 이전과 같고 왕복 해시 3종이 같다. "
                         "한계: 영역끼리·건물과 겹침은 검사하지 않는다")
    tot = sum(b["count"] for b in inp["bays"])
    acc = sum(b["count"] for b in inp["bays"] if b["accessible"])
    sym = nlines(sp, "lines-symbol")
    if (f"주차 합계 {tot}대(장애인 전용 {acc}대)" in tsp and "주차 4대" in tsp and f"장애인 {acc}대" in tsp and ifc["bays"] == tot and ifc["accessible"] == acc and sym >= 6 and exact):
        res["P-05"] = (OK, f"배치도에 주차면 배열(일반 4대·장애인 {acc}대)의 규격 사각형(2500x5000·3500x5000), 차로(폭 2000) 양쪽 선, 진출입 동선 화살표(진입 가로대·화살표 머리)를 그리고 "
                           f"배열별 대수와 '주차 합계 {tot}대(장애인 전용 {acc}대)' 문자를 단다. 대수는 내보낸 IFC 의 의도에서 다시 센 값과 같다. 한계: 법정 설치 대수 산정·구획 규격·경사로는 다루지 않는다")
    sg = inp["signs"]
    s1 = next(x for x in sg if x["face"] == "S")
    n1 = next(x for x in sg if x["face"] == "N")
    if (f"간판 {s1['name']} {s1['w']:.0f}x{s1['h']:.0f}" in tes and "RA-01 상가" in tes and f"건물번호판 {n1['name']} {n1['w']:.0f}x{n1['h']:.0f}" in ten and "12-3" in ten
            and not any("간판 S1" in x for x in ten) and ifc["signs"] == len(sg) and exact):
        res["E-03"] = (OK, f"남측 입면에 간판 {s1['name']}(폭 {s1['w']:.0f} x 높이 {s1['h']:.0f}mm, 아래 모서리 +{s1['z']:.0f})을, 북측 입면에 건물번호판 {n1['name']}({n1['w']:.0f}x{n1['h']:.0f}mm, +{n1['z']:.0f})을 "
                           "사각형과 크기·문구 문자로 표시한다. 다른 면 입면에는 나오지 않는다. 입력이 없으면 입면은 바이트 그대로이고 왕복 해시 3종이 같다. 한계: 평면·배치도에는 안 그리고 벽 두께·돌출·조명은 없다")
    sc = a["section_check"]
    both = [rd("site-section-long"), rd("site-section-trans")]
    if (sc["max_abs_err_mm"] is not None and sc["max_abs_err_mm"] < 0.01 and sc["ground_points"] >= 20 and tsl.count("대지 경계") == 2 and tst.count("대지 경계") == 2 and sc["gl"].startswith("GL ")
            and all(nlines(x, "lines-hatch") >= 10 and nlines(x, "lines-cut") >= 30 for x in both) and (sd / "A-013.svg").exists() and rt["section_with_site"] is True and exact):
        res["P-03"] = (OK, f"대지 종단면(절단선 길이 23000)·횡단면(26000) 두 방향에 건물 단면과 함께 지반선(표고점 {inp['terrain_points']}개 보간, 점 {sc['ground_points']}개)·지반 아래 사선 눈금·GL 수평선('{sc['gl']}')·"
                           "대지 경계 세로 표시와 '대지 경계' 문자(경계 두 곳)를 그린다. 지반선 높이는 거리 제곱 역가중으로 다시 계산한 값과 최대 오차 "
                           f"{sc['max_abs_err_mm']:.4f}mm 로 같다(독립 계산). 시트 A-013 에 두 단면을 함께 올린다. 한계: 지반은 표고점 보간이라 등고선·TIN 이 아니고 지하층·토공은 없다")
    n_spot = sum(1 for x in tsp if x.startswith("지반 "))
    if n_spot == inp["terrain_points"] and a["derived"].get("terrain_points") == inp["terrain_points"] and exact:
        res["N-08"] = (OK, f"배치도에 지반 표고점 {n_spot}개의 높이(모델 기준 mm)를 문자로 표시하고, 대지 단면에서 지반선으로 고저차를 보인다(최저 -800·최고 +600). 대지 표고 EL 한 값 표기(8절)에 더한 것")
    return res


def judge_after13_items(out: Path, run: dict, base: dict):
    """열세 번째 작업(수직동선 상세·승강기 치수): sheets13/ 의 상세 뷰 SVG 를 직접 읽어 제도·관행 L-10(승강기 위치·치수)·VT-03(수직동선 상세)과 BIM B-05(수직동선 상세 도면)를 판정한다.
    기대 치수 문자는 내보낸 IFC 의 DoBIM_Intent(계단 걸음선·폭·챌판·높이, 승강기 샤프트·카·높이)에서 엔진 코드와 독립으로 다시 계산해 SVG 의 치수 글자와 맞춘다.
    산출이 없으면 빈 결과(이전 판정 그대로)."""
    res: dict[str, tuple[str, str]] = {}
    a = run.get("after13")
    sd = out / "sheets13"
    need = ("stair-plan", "stair-section", "ev-plan-1F", "ev-plan-2F", "ev-section", "plan-1F-dims")
    if not a or not all((sd / f"{n}.svg").exists() for n in need):
        return res
    rd = lambda n: (sd / f"{n}.svg").read_text(encoding="utf-8")   # noqa: E731
    tx = {n: _texts(rd(n)) for n in need}
    ist, iev = a["ifc_intent"]["stair"], a["ifc_intent"]["elevator"]
    x1, y1, x2, y2 = ist["run"]
    length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    n = ist["risers"]
    td, rs = length / (n - 1), ist["height"] / n
    sx1, sy1, sx2, sy2 = iev["shaft"]
    cw, cd, _ch = iev["cab"]
    z0, z1, cab_top = iev["base_offset"], iev["base_offset"] + iev["rise"], iev["base_offset"] + iev["cab"][2]
    rt = a["roundtrip"]
    exact = all(rt["hash_equal"]) and not rt["warnings"] and all(rt["view_params_equal"].values())
    off = a["option_off"]
    # ---- L-10: 승강기 평면의 샤프트·카 치수
    want = [f"샤프트 {_fmt(sx2 - sx1)}", f"샤프트 {_fmt(sy2 - sy1)}", f"카 {_fmt(cw)}", f"카 {_fmt(cd)}"]
    t1 = tx["plan-1F-dims"]
    t_ev = tx["ev-plan-1F"]
    if (all(t1.count(w) >= (2 if w in want[:2] and want[0] == want[1] else 1) for w in set(want)) and "EV" in t1 and all(w in t_ev for w in want) and "EV" in tx["ev-plan-2F"]
            and all(w in tx["ev-plan-2F"] for w in want) and exact and off["lines_equal"] and off["labels_equal"] and off["dims"] == 0):
        res["L-10"] = (OK, f"승강기 평면에 샤프트 안쪽 {_fmt(sx2 - sx1)}x{_fmt(sy2 - sy1)}·카 {_fmt(cw)}x{_fmt(cd)} 치수를 표기한다(1:100 1층 평면 dims 옵션, 1:50 1F·2F 상세 평면). "
                           "치수 글자는 내보낸 IFC 의 의도에서 다시 계산한 값과 같다. 옵션을 끄면 뷰가 일반 평면과 선·문자까지 같다(기존 승강기 도면 바이트 불변: 새 뷰 옵션으로 켠다). "
                           "IFC 왕복 해시 3종 동일(경고 0, 옵션 뷰 파라미터 동일). 한계: 문 위치·도어 방향·기계실·피트 표기 없음")
    # ---- B-05: 계단·승강기 상세 도면
    run_txt = f"{_fmt(td)}x{n - 1}={_fmt(length)}"
    rise_txt = f"{_fmt(rs)}x{n}={_fmt(ist['height'])}"
    sp, ss, es = tx["stair-plan"], tx["stair-section"], tx["ev-section"]
    stair_ok_ = (run_txt in sp and _fmt(ist["width"]) in sp and f"{n}단 챌판 {_fmt(rs)} 디딤 {_fmt(td)}" in sp
                 and {run_txt, rise_txt, _fmt(rs), _fmt(td)} <= set(ss) and any(t.startswith("난간 ") for t in ss))
    ev_ok_ = (f"카 {_fmt(cab_top - z0)}" in es and f"OH {_fmt(z1 - cab_top)}" in es and f"승강로 {_fmt(z1 - z0)}" in es)
    segs = {(e["view"], e["ext"]): e["segments"] for e in a["exports"] if "view" in e}
    small = segs.get(("stair-plan", "svg"), 1 << 30) < segs.get(("plan-1F", "svg"), 0)
    sheets = all((sd / f"{s}.svg").exists() for s in ("A-014", "A-015"))
    scales = [rd(n).count("S=1:20") + rd(n).count("S=1:50") for n in ("A-014", "A-015")] if sheets else [0]
    if stair_ok_ and ev_ok_ and small and sheets and all(scales) and exact:
        res["B-05"] = (OK, f"수직동선 상세 도면이 있다. 계단 상세(A-014): 평면 1:20(걸음선 {_fmt(length)}={n - 1}x{_fmt(td)}·폭 {_fmt(ist['width'])} 치수·'{n}단 챌판 {_fmt(rs)} 디딤 {_fmt(td)}')과 단면 1:20(첫 챌판 {_fmt(rs)}·디딤 {_fmt(td)}·"
                           f"오름 {rise_txt}·디딤 사슬 {run_txt}·난간 높이). 승강기 상세(A-015): 1F·2F 평면 1:50(샤프트·카 폭·깊이)과 단면 1:50(카 {_fmt(cab_top - z0)}·오버헤드 OH {_fmt(z1 - cab_top)}·승강로 {_fmt(z1 - z0)}). "
                           "치수 글자는 내보낸 IFC 의 의도에서 다시 계산한 값과 같고, 상세는 요소 범위로 잘라낸 부위 뷰다(crop). IFC 왕복 해시 3종 동일. 한계: 코어(계단실+승강기) 통합 상세·주차경사로·피트(샤프트 바닥이 카 바닥) 별도 표기는 없다")
    # ---- VT-03: 중복 행(계단의 평면·단면, 코어, 주차경사로 상세): 계단 상세는 있고 코어·주차경사로는 없다
    if "B-05" in res:
        res["VT-03"] = (PART, "계단의 평면·단면 상세와 승강기 상세 평면·단면이 있다(A-014·A-015). 코어(계단실·승강기 통합 상세)와 주차경사로 상세는 없다")
    return res


def judge_bim_items(out: Path, run: dict, base: dict):
    """여섯 번째 작업 항목(B-02 추출 형상, B-04 뷰 원본 저장)을 run.json 의 probe 로 판정한다."""
    return judge_corner(run) | judge_sheet_def(run)


def judge_corner(run: dict):
    """B-02 추출 형상 변경 금지: 도면이 그리는 모든 벽의 범위가 내보낸 IFC 파일의 실제 형상과 같고(0.01mm 이내), 예전에 도면만 채우던 L 조인 모서리가
    IFC 형상에도 들어 있을 때만 충족이다. 도면 내용 = BIM 데이터(IFC)의 정의를 따른다."""
    pr = run.get("corner_probe")
    if not pr:
        return {}
    if pr["max_diff_mm"] < 0.01 and pr["extended_walls"] > 0:
        return {"B-02": (OK, f"벽 {pr['walls']}개 모두 도면이 그리는 범위와 내보낸 IFC 파일의 실제 형상이 같다(최대 차이 {pr['max_diff_mm']}mm). "
                             f"L 조인 모서리를 연장한 벽 {pr['extended_walls']}개는 IFC 형상에도 연장이 들어 있다(코어 조인 형상). 논리 길이·치수 참조·일람표 길이는 그대로")}
    return {"B-02": (PART, f"도면과 IFC 형상이 일부 다르다(최대 차이 {pr['max_diff_mm']}mm, 연장 벽 {pr['extended_walls']}개)")}


def judge_sheet_def(run: dict):
    """B-04 도면 뷰의 원본 저장: 시트 정의(뷰 배치·표제란 인자)를 모델에 저장하고(sheet.define), 저장한 정의로 낸 파일이 직접 낸 시트와 바이트까지 같고,
    IFC 로 내보냈다 가져와도 정의가 되살고(해시 3종 같음) 되살린 정의로 낸 파일도 같을 때만 충족이다. 뷰·주석 정의의 IFC 왕복은 이전부터 있었다(ADR-0015)."""
    pr = run.get("sheet_def_probe")
    if not pr:
        return {}
    n = len(pr["sheets"])
    ok_all = (n >= 3 and all(pr["render_equals_export"].values()) and all(pr["hash_equal"]) and pr["restored"] == pr["sheets"] and not pr["import_warnings"]
              and all(pr["roundtrip_render_equals_export"].values()) and pr["missing_views"] == 0)
    if ok_all:
        return {"B-04": (OK, f"시트 정의 {n}개를 모델에 저장(sheet.define): 저장한 정의로 낸 SVG·PDF {len(pr['render_equals_export'])}개가 sheet.export 산출과 바이트까지 같다. "
                             f"IFC 로 내보냈다 가져오면 정의 {len(pr['restored'])}개가 되살고 해시 3종이 같으며, 되살린 정의로 낸 파일도 같다(경고 0). 뷰·주석 정의는 이전부터 IFC 로 왕복한다")}
    return {"B-04": (PART, f"시트 정의 저장은 있으나 검증이 일부 어긋났다: {json.dumps(pr, ensure_ascii=False)[:300]}")}


def judge_gate(items: dict, out: Path, run: dict):
    """G2 5-1절 자동 표현 대상 8항목 중 이번 작업이 다루는 #2·#7·#8 을 근거와 함께 낸다(128항목 집계와 별개). 반환: [(번호, 항목, 판정, 근거)]"""
    rows = [("2", "기둥·벽·창문 위치", items["L-02"]["verdict"], items["L-02"]["reason"])]
    probe = run.get("slab_probe")
    _, stair_sec, stair_why = stair_ok(run)
    stairs = f"계단 단면 표현 확인({stair_why})" if stair_sec else "계단 단면 표현은 확인되지 않았다(stair_probe 없음·불일치)"
    if probe and probe.get("slabs"):
        secs, elevs, sec_ok, ev_ok = _slab_probe_ok(probe)
        if sec_ok and ev_ok:
            rows.append(("7", "종·횡단면도(슬래브·계단 표현 포함)", OK if stair_sec else PART,
                         f"슬래브: 단면 절단 사각형·채움 {len(secs)}개 뷰 확인, 입면 모서리 {len(elevs)}개 뷰 확인. {stairs}"))
        else:
            rows.append(("7", "종·횡단면도(슬래브·계단 표현 포함)", PART, "슬래브 표현이 확인되지 않았다(slab_probe 불일치). " + stairs))
    else:
        rows.append(("7", "종·횡단면도(슬래브·계단 표현 포함)", items["X-01"]["verdict"], "슬래브 입력 없음: G1 판정 유지(슬래브·계단 단면 표현은 아직 없다)"))
    for n, nm, i in (("3", "방화구획 및 방화문의 위치", "L-03"), ("4", "복도 및 계단의 위치", "L-04"), ("6", "외부마감재료", "E-02")):
        rows.append((n, nm, items[i]["verdict"], items[i]["reason"]))
    rows.sort(key=lambda r: int(r[0]))
    exp = run.get("heights", {}).get("expected")
    if exp and (out / "heights").exists():
        hv = [items[i]["verdict"] for i in ("X-02", "X-03")]
        verdict = OK if all(v == OK for v in hv) else PART
        rows.append(("8", "건축물 높이·각 층 높이(G2: 반자높이 세부 제외)", verdict,
                     f"층고·최고 높이 자동 치수(X-02 {items['X-02']['verdict']}, X-03 {items['X-03']['verdict']}). "
                     f"반자높이는 X-04 {items['X-04']['verdict']}({'천장 마감 높이 입력 기준' if items['X-04']['verdict'] == OK else '슬래브 밑면 근사'}): G2 5-1절이 천장 요소 없음으로 제외한 세부"))
    else:
        rows.append(("8", "건축물 높이·각 층 높이(G2: 반자높이 세부 제외)", PART, "높이 자동 치수 산출 없음"))
    return rows


# ---------------------------------------------------------------- 집계
def tally(items: dict):
    """G1 집계 규칙. 반환: {층: (충족, 부분, 미충족, 해당없음)}"""
    legal_ids = [i for i, r in items.items() if r["layer"] == "법정" and i not in ("X-02", "X-03", "X-04")]
    h = [items[i]["verdict"] for i in ("X-02", "X-03", "X-04")]
    bundle = OK if all(v == OK for v in h) else (NO if all(v == NO for v in h) else PART)
    legal = [items[i]["verdict"] for i in legal_ids] + [bundle]
    other = lambda layer: [r["verdict"] for r in items.values() if r["layer"] == layer]   # noqa: E731
    cnt = lambda vs: tuple(vs.count(k) for k in (OK, PART, NO, NA))   # noqa: E731
    return {"법정(별표 2 고유 16)": cnt(legal), "제도·관행": cnt(other("제도·관행")), "BIM 지침": cnt(other("BIM"))}, bundle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--g1")
    ap.add_argument("--md")
    o = ap.parse_args()
    out = Path(o.out)
    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    items = load_carried()
    dg = digest_same(out, Path(o.g1) if o.g1 else None)
    changed = []
    for judged in (judge_sheet_items(out, run, items), judge_element_items(out, run, items), judge_slab_height_items(out, run, items),
                   judge_stair_space_items(out, run, items), judge_sheet2_items(out, run, items), judge_bim_items(out, run, items), judge_after7_items(out, run, items), judge_after8_items(out, run, items), judge_after9_items(out, run, items),
                   judge_after11_items(out, run, items), judge_after12_items(out, run, items), judge_after13_items(out, run, items)):
        for i, (v, why) in judged.items():
            if items[i]["verdict"] != v:
                changed.append((i, items[i]["verdict"], v))
            items[i] = dict(items[i], verdict=v, reason=why)
    t, bundle = tally(items)
    gate = judge_gate(items, out, run)
    lines = []
    if dg:
        lines.append(f"- 기본 산출 SVG 와 G1 산출물 동일: {dg[0]}/{dg[1]}")
    lines += ["", "| 층 | 충족 | 부분 | 미충족 | 해당없음 | 충족률(해당없음 제외) | 충족+부분 |", "|---|---|---|---|---|---|---|"]
    for k, (a, b, c, d) in t.items():
        den = a + b + c
        lines.append(f"| {k} | {a} | {b} | {c} | {d} | {a}/{den} ({100 * a // den}%) | {a + b}/{den} ({100 * (a + b) // den}%) |")
    full = {"1": items["L-01"]["verdict"], "5": items["E-01"]["verdict"]} | {n: v for n, _, v, _ in gate}     # #1·#5 는 G1 판정(충족 유지)
    n_ok = sum(1 for v in full.values() if v == OK)
    lines += ["", f"법정 높이 묶음(X-02·X-03·X-04): {bundle}", "", f"G2 자동 표현 대상 법정 8항목 충족: {n_ok}/8(#1·#5 는 G1 판정 유지, 나머지는 아래 표)", "",
              "자동 표현 대상(G2 5-1절) 중 이번 작업이 다루는 항목(집계와 별개):", "", "| # | 항목 | 판정 | 근거 |", "|---|---|---|---|"]
    lines += [f"| {n} | {nm} | {v} | {why} |" for n, nm, v, why in gate]
    lines += ["", "| 항목 | 층 | 판정 | 근거 |", "|---|---|---|---|"]
    for i, r in items.items():
        lines.append(f"| {i} | {r['layer']} | {r['verdict']} | {r['reason']} |")
    text = "\n".join(lines)
    if o.md:
        Path(o.md).write_text(text, encoding="utf-8")
    print(text.split("법정 높이 묶음")[0])
    print(next(ln for ln in lines if ln.startswith("G2 자동 표현")))
    print("변경:", changed)


if __name__ == "__main__":
    main()
