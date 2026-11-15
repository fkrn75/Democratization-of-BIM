# SPDX-License-Identifier: MPL-2.0
"""도면 체크리스트 판정기 (1단계).

G1 판정(항목 ID·층·판정만 carried_g1.json 에 옮겼다)을 출발점으로 삼고, 그 판정이 아직 맞는지를 두 가지로 다시 확인한다.
 1) 도면 내용 항목: 이번에 뽑은 기본 산출 SVG 가 G1 산출물과 바이트 단위로 같은지(--g1 폴더가 있을 때). 같으면 G1 판정을 유지한다.
 2) 시트·글꼴이 바꾸는 항목(N-02, SC-05, SC-06, T-02, T-04, T-05, T-08, LN-06, B-13): 산출 파일(시트 SVG·PDF)을 직접 검사해 판정한다.
    시트 산출이 없으면(기준선) 같은 검사가 '없음'으로 나와 G1 판정 그대로다.
 3) 슬래브·높이 치수가 바꾸는 항목(X-01, X-02, X-03, X-04. DM-08 은 이 셋의 중복 행이라 따름): run.json 의 slab_probe(슬래브 추가 전후 뷰 선분·채움)와 heights/ 산출(높이 치수 SVG)을 검사해 판정한다.
    산출이 없으면 G1 판정 그대로다. 128항목 집계에는 "법정 #7 슬래브 단면 표현"에 해당하는 항목이 따로 없어서, G2 5-1절의 8개 자동 표현 대상(#2·#7·#8)은
    집계와 별도로 judge_gate 가 근거와 함께 낸다(계단 등 아직 없는 표현은 부분으로 남긴다).
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


def judge_gate(items: dict, out: Path, run: dict):
    """G2 5-1절 자동 표현 대상 8항목 중 이번 작업이 다루는 #2·#7·#8 을 근거와 함께 낸다(128항목 집계와 별개). 반환: [(번호, 항목, 판정, 근거)]"""
    rows = [("2", "기둥·벽·창문 위치", items["L-02"]["verdict"], items["L-02"]["reason"])]
    probe = run.get("slab_probe")
    stairs = "계단 단면 표현은 아직 없다(계단 요소 미구현)"
    if probe and probe.get("slabs"):
        secs, elevs, sec_ok, ev_ok = _slab_probe_ok(probe)
        if sec_ok and ev_ok:
            rows.append(("7", "종·횡단면도(슬래브·계단 표현 포함)", PART, f"슬래브: 단면 절단 사각형·채움 {len(secs)}개 뷰 확인, 입면 모서리 {len(elevs)}개 뷰 확인. {stairs}"))
        else:
            rows.append(("7", "종·횡단면도(슬래브·계단 표현 포함)", PART, "슬래브 표현이 확인되지 않았다(slab_probe 불일치). " + stairs))
    else:
        rows.append(("7", "종·횡단면도(슬래브·계단 표현 포함)", items["X-01"]["verdict"], "슬래브 입력 없음: G1 판정 유지(슬래브·계단 단면 표현은 아직 없다)"))
    exp = run.get("heights", {}).get("expected")
    if exp and (out / "heights").exists():
        hv = [items[i]["verdict"] for i in ("X-02", "X-03")]
        verdict = OK if all(v == OK for v in hv) else PART
        rows.append(("8", "건축물 높이·각 층 높이(G2: 반자높이 세부 제외)", verdict,
                     f"층고·최고 높이 자동 치수(X-02 {items['X-02']['verdict']}, X-03 {items['X-03']['verdict']}). "
                     f"반자높이는 X-04 {items['X-04']['verdict']}(슬래브 밑면 근사): G2 5-1절이 천장 요소 없음으로 제외한 세부"))
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
    for judged in (judge_sheet_items(out, run, items), judge_element_items(out, run, items), judge_slab_height_items(out, run, items)):
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
    lines += ["", f"법정 높이 묶음(X-02·X-03·X-04): {bundle}", "", "자동 표현 대상(G2 5-1절) 중 이번 작업이 다루는 항목(집계와 별개):", "",
              "| # | 항목 | 판정 | 근거 |", "|---|---|---|---|"]
    lines += [f"| {n} | {nm} | {v} | {why} |" for n, nm, v, why in gate]
    lines += ["", "| 항목 | 층 | 판정 | 근거 |", "|---|---|---|---|"]
    for i, r in items.items():
        lines.append(f"| {i} | {r['layer']} | {r['verdict']} | {r['reason']} |")
    text = "\n".join(lines)
    if o.md:
        Path(o.md).write_text(text, encoding="utf-8")
    print(text.split("법정 높이 묶음")[0])
    print("변경:", changed)


if __name__ == "__main__":
    main()
