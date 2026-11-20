# SPDX-License-Identifier: MPL-2.0
"""일괄 PDF(DOC-14, sheet.render_set): 저장된 시트 여러 장을 한 PDF 로.

 1. 쪽 수·순서·용지·책갈피가 ids 와 같고, 쪽마다 그림 명령이 같은 시트를 sheet.render 로 한 장 낸 PDF 와 같다(글꼴 GID 를 뺀 나머지)
 2. 인쇄 설정은 모든 쪽에 같이 걸린다
 3. 거절: 같은 시트 두 번, .pdf 아닌 경로, 없는 시트, 빈 목록, 덮어쓰기 동의 없음
"""
import re
import zlib

from test_integration_g1 import S, _ok
from test_integration_views_rt import _build
from test_sheet_def import SH1, SH2, SHEET, _define, _render, _second


def _streams(pdf: bytes) -> list[str]:
    """쪽 콘텐츠 스트림(풀어서) 목록: 객체 번호 순서 = 쪽 순서."""
    out = []
    for m in re.finditer(rb"<< /Length (\d+) /Filter /FlateDecode >>\nstream\n", pdf):
        raw = zlib.decompress(pdf[m.end():m.end() + int(m.group(1))])
        if raw.startswith(b"q 1 J 1 j"):
            out.append(raw.decode("ascii"))
    return out


def _no_glyphs(stream: str) -> list[str]:
    """글꼴 부분집합이 다르면 GID 가 달라진다: 글 문자열(<...> Tj)만 비우고 나머지 그림 명령을 비교한다."""
    return [re.sub(r"<[0-9A-F]*> Tj", "<> Tj", ln) for ln in stream.splitlines()]


def _titles(pdf: bytes) -> list[str]:
    return [bytes.fromhex(t.decode()).decode("utf-16-be") for t in re.findall(rb"/Title <FEFF([0-9A-F]+)> /Parent 4 0 R", pdf)]


def _two(b):
    _build(b)
    _define(b)
    _ok(b.run("sheet.define", _second(paper="A1", sheet_number="A-102", sheet_name="2층 평면도"), project="src.dobim"))


def test_여러_시트를_한_PDF_로_낸다(tmp_path):
    b = S.Bench(tmp_path)
    _two(b)
    pv = b.run("sheet.render_set", {"ids": [SH2, SH1], "path": "set.pdf", "overwrite": True}, project="src.dobim")
    assert pv["error"]["code"] == "CONFIRM_REQUIRED"
    r = b.destructive("sheet.render_set", {"ids": [SH2, SH1], "path": "set.pdf", "overwrite": True}, "src.dobim")
    assert r["ok"], r
    res = r["result"]
    assert [x["id"] for x in res["pages"]] == [SH2, SH1] and [x["paper"] for x in res["pages"]] == ["A1", "A3"]
    assert [x["sheet_number"] for x in res["pages"]] == ["A-102", "A-101"]
    pdf = (b.ws / "set.pdf").read_bytes()
    assert len(pdf) == res["bytes"] and pdf.startswith(b"%PDF-1.5") and pdf.rstrip().endswith(b"%%EOF")
    assert b"/Count 2 >>" in pdf and len(re.findall(rb"/Type /Page /Parent", pdf)) == 2
    boxes = re.findall(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf)
    assert [round(float(w) * 25.4 / 72) for w, _ in boxes] == [841, 420]          # A1 가로, A3 가로(쪽마다 자기 용지)
    assert b"/Outlines 4 0 R" in pdf and len(re.findall(rb"/Dest \[\d+ 0 R /Fit\]", pdf)) == 2
    assert _titles(pdf) == ["A-102 2층 평면도", f"A-101 {SHEET['sheet_name']}"]
    # 쪽마다 sheet.render 로 한 장씩 낸 PDF 와 그림 명령이 같다
    _render(b, SH2, "one2.pdf")
    _render(b, SH1, "one1.pdf")
    pages = _streams(pdf)
    assert len(pages) == 2
    for page, name in zip(pages, ("one2.pdf", "one1.pdf")):
        one = _streams((b.ws / name).read_bytes())
        assert len(one) == 1 and _no_glyphs(page) == _no_glyphs(one[0]), name
    # 같은 입력이면 같은 파일(결정성)
    r2 = b.destructive("sheet.render_set", {"ids": [SH2, SH1], "path": "set-b.pdf", "overwrite": True}, "src.dobim")
    assert r2["ok"] and (b.ws / "set-b.pdf").read_bytes() == pdf


def test_인쇄_설정은_모든_쪽에_걸린다(tmp_path):
    b = S.Bench(tmp_path)
    _two(b)
    r = b.destructive("sheet.render_set", {"ids": [SH1, SH2], "path": "p.pdf", "overwrite": True, "print": {"paper": "A4", "margin": 5}}, "src.dobim")
    assert r["ok"], r
    assert [x["paper"] for x in r["result"]["pages"]] == ["A4", "A4"]
    boxes = re.findall(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", (b.ws / "p.pdf").read_bytes())
    assert [round(float(w) * 25.4 / 72) for w, _ in boxes] == [297, 297]
    assert any("축척 맞춤" in w for w in r.get("warnings", [])), r.get("warnings")


def test_거절(tmp_path):
    b = S.Bench(tmp_path)
    _build(b)
    _define(b)
    run = lambda a: b.run("sheet.render_set", a, project="src.dobim")   # noqa: E731
    assert run({"ids": [SH1, SH1], "path": "x.pdf", "overwrite": True})["error"]["code"] == "CONSTRAINT"
    assert run({"ids": [SH1], "path": "x.svg", "overwrite": True})["error"]["code"] == "PATH_DENIED"
    assert run({"ids": [SH2], "path": "x.pdf", "overwrite": True})["error"]["code"] == "NOT_FOUND"
    assert run({"ids": [], "path": "x.pdf", "overwrite": True})["error"]["code"] == "SCHEMA_INVALID"
    assert b.destructive("sheet.render_set", {"ids": [SH1], "path": "x.pdf", "overwrite": True}, "src.dobim")["ok"]
    assert run({"ids": [SH1], "path": "x.pdf"})["error"]["code"] == "FILE_EXISTS"
