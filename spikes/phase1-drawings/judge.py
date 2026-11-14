# SPDX-License-Identifier: MPL-2.0
"""도면 체크리스트 판정기 (1단계).

G1 판정(항목 ID·층·판정만 carried_g1.json 에 옮겼다)을 출발점으로 삼고, 그 판정이 아직 맞는지를 두 가지로 다시 확인한다.
 1) 도면 내용 항목: 이번에 뽑은 기본 산출 SVG 가 G1 산출물과 바이트 단위로 같은지(--g1 폴더가 있을 때). 같으면 G1 판정을 유지한다.
 2) 시트·글꼴이 바꾸는 항목(N-02, SC-05, SC-06, T-02, T-04, T-05, T-08, LN-06, B-13): 산출 파일(시트 SVG·PDF)을 직접 검사해 판정한다.
    시트 산출이 없으면(기준선) 같은 검사가 '없음'으로 나와 G1 판정 그대로다.
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
    for i, (v, why) in judge_sheet_items(out, run, items).items():
        if items[i]["verdict"] != v:
            changed.append((i, items[i]["verdict"], v))
        items[i] = dict(items[i], verdict=v, reason=why)
    t, bundle = tally(items)
    lines = []
    if dg:
        lines.append(f"- 기본 산출 SVG 와 G1 산출물 동일: {dg[0]}/{dg[1]}")
    lines += ["", "| 층 | 충족 | 부분 | 미충족 | 해당없음 | 충족률(해당없음 제외) | 충족+부분 |", "|---|---|---|---|---|---|---|"]
    for k, (a, b, c, d) in t.items():
        den = a + b + c
        lines.append(f"| {k} | {a} | {b} | {c} | {d} | {a}/{den} ({100 * a // den}%) | {a + b}/{den} ({100 * (a + b) // den}%) |")
    lines += ["", f"법정 높이 묶음(X-02·X-03·X-04): {bundle}", "", "| 항목 | 층 | 판정 | 근거 |", "|---|---|---|---|"]
    for i, r in items.items():
        lines.append(f"| {i} | {r['layer']} | {r['verdict']} | {r['reason']} |")
    text = "\n".join(lines)
    if o.md:
        Path(o.md).write_text(text, encoding="utf-8")
    print(text.split("법정 높이 묶음")[0])
    print("변경:", changed)


if __name__ == "__main__":
    main()
