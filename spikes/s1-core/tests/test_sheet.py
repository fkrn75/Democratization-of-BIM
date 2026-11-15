# SPDX-License-Identifier: MPL-2.0
"""1단계 시트·한글 PDF 글꼴: 배치, 표제란, 방위표·축척 막대, 혼합 축척, PDF 글꼴 내장, 글꼴 없을 때 대체, 오류 경로."""
import io
import re
import zlib

import pytest

from dobim_core import fixture as F
from dobim_core import pdf_font as PF
from dobim_core import sheet as SH

pytestmark = pytest.mark.filterwarnings("ignore")

SPEC = {"project_name": "시험 주택", "sheet_name": "1층 평면도", "sheet_number": "A-101", "date": "2026-11-15"}


def export(sess, args, **kw):
    """확인 토큰 2단계로 sheet.export 를 실행한다."""
    r = sess.run("sheet.export", args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return sess.run("sheet.export", args, confirm=r["error"]["details"]["confirm_token"], **kw)


@pytest.fixture
def pv(base):
    assert base.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1})["ok"]
    return base


def read(sess, name):
    return (sess.path.parent / name).read_bytes()


def test_A3_SVG_표제란_방위표_축척막대(pv):
    r = export(pv, {"views": [F.V1], "path": "s.svg", **SPEC})
    assert r["ok"], r
    assert r["result"]["paper"] == "A3" and r["result"]["views"][0]["view"] == F.V1
    s = read(pv, "s.svg").decode("utf-8")
    assert 'width="420mm" height="297mm"' in s
    for gid in ("sheet-border", "title-block", "north-symbol", "scale-bar", "revision-table"):
        assert f'id="{gid}"' in s, gid
    for text in ("시험 주택", "1층 평면도", "A-101", "2026-11-15", ">1:100<", "S=1:100"):
        assert text in s, text
    assert 'data-north-deg="0"' in s


def test_A1_용지와_방위각(pv):
    r = export(pv, {"views": [F.V1], "path": "s.svg", "paper": "A1", "north_angle": 45.0, **SPEC})
    assert r["ok"], r
    s = read(pv, "s.svg").decode("utf-8")
    assert 'width="841mm" height="594mm"' in s and 'data-north-deg="45"' in s


def test_같은_입력은_같은_파일이다(pv):
    a = export(pv, {"views": [F.V1], "path": "a.svg", **SPEC})
    b = export(pv, {"views": [F.V1], "path": "b.svg", **SPEC})
    assert a["ok"] and b["ok"] and read(pv, "a.svg") == read(pv, "b.svg")


def test_날짜를_생략하면_오늘_날짜(pv):
    import datetime
    r = export(pv, {"views": [F.V1], "path": "s.svg", "project_name": "p"})
    assert r["ok"], r
    assert datetime.date.today().isoformat() in read(pv, "s.svg").decode("utf-8")


def test_혼합_축척은_도면참조와_뷰별_축척(pv):
    assert pv.run("view.create", {"id": "4" + "0" * 20 + "2", "name": "남측 입면", "type": "elevation", "dir": "S", "scale": 50})["ok"]
    r = export(pv, {"views": [F.V1, "4" + "0" * 20 + "2"], "path": "s.svg", **SPEC})
    assert r["ok"], r
    s = read(pv, "s.svg").decode("utf-8")
    assert ">도면참조<" in s and "S=1:100" in s and "S=1:50" in s
    placed = r["result"]["views"]
    assert placed[0]["scale"] == 100 and placed[1]["scale"] == 50
    a, b = placed
    assert not (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]), "뷰가 겹친다"


def test_뷰가_용지에_안_들어가면_고치는_방법을_알린다(pv):
    assert pv.run("view.set", {"id": F.V1, "scale": 1})["ok"]
    r = export(pv, {"views": [F.V1], "path": "s.svg", **SPEC})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT" and "축척" in r["error"]["message"]


def test_오류_경로(pv):
    assert pv.run("sheet.export", {"views": ["9" * 22], "path": "s.svg"})["error"]["code"] in ("NOT_FOUND", "CONSTRAINT")
    assert pv.run("sheet.export", {"views": [F.V1], "path": "s.png"})["error"]["code"] == "PATH_DENIED"
    assert pv.run("sheet.export", {"views": [], "path": "s.svg"})["error"]["code"] == "SCHEMA_INVALID"
    assert pv.run("sheet.export", {"views": [F.V1], "path": "s.svg", "paper": "A4"})["error"]["code"] == "SCHEMA_INVALID"
    assert pv.run("sheet.export", {"views": [F.V1], "path": "s.svg", "date": "2026/11/15"})["error"]["code"] == "SCHEMA_INVALID"
    r = export(pv, {"views": [F.V1, F.V1], "path": "s.svg"})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    assert export(pv, {"views": [F.V1], "path": "s.svg"})["ok"]
    r = pv.run("sheet.export", {"views": [F.V1], "path": "s.svg"})        # 덮어쓰기 동의 없이 이미 있는 파일
    assert r["error"]["code"] == "FILE_EXISTS"


# ---------------------------------------------------------------- PDF 글꼴
def _fontfile(pdf: bytes) -> bytes:
    ref = re.search(rb"/FontFile2 (\d+) 0 R", pdf).group(1)
    stream = re.search(rb"\n" + ref + rb" 0 obj\n.*?stream\n(.*?)\nendstream", pdf, re.S).group(1)
    return zlib.decompress(stream)


needs_font = pytest.mark.skipif(PF.load_pdf_font() is None, reason="이 PC 에 OFL 한글 글꼴(Noto Sans KR·나눔고딕)이 없다")


@needs_font
def test_PDF_한글_글꼴_내장(pv):
    r = export(pv, {"views": [F.V1], "path": "s.pdf", **SPEC})
    assert r["ok"], r
    assert r["result"]["non_ascii_dropped"] is False and r["result"]["font_embedded"]
    pdf = read(pv, "s.pdf")
    assert pdf.startswith(b"%PDF") and b"/Identity-H" in pdf and b"/ToUnicode" in pdf and b"/Helvetica" not in pdf
    from fontTools.ttLib import TTFont
    cmap = TTFont(io.BytesIO(_fontfile(pdf))).getBestCmap()
    for ch in "시험주택평면도":
        assert ord(ch) in cmap, ch                      # 쓴 한글이 부분집합에 들어 있다
    assert len(pdf) < 400_000                           # 부분집합이라 글꼴 전체(수 MB)를 싣지 않는다
    assert b"/MediaBox [0 0 1190.55 841.89]" in pdf     # A3


@needs_font
def test_PDF_글자_복사용_ToUnicode에_한글이_있다(pv):
    r = export(pv, {"views": [F.V1], "path": "s.pdf", **SPEC})
    assert r["ok"]
    pdf = read(pv, "s.pdf")
    ref = re.search(rb"/ToUnicode (\d+) 0 R", pdf).group(1)
    cm = re.search(rb"\n" + ref + rb" 0 obj\n.*?stream\n(.*?)\nendstream", pdf, re.S).group(1)      # ToUnicode 는 압축하지 않는다
    assert f"<{ord('시'):04X}>".encode() in cm            # 매핑 대상 유니코드에 한글 '시'


@needs_font
def test_PDF_두_번_뽑아도_같다(pv):
    export(pv, {"views": [F.V1], "path": "a.pdf", **SPEC})
    export(pv, {"views": [F.V1], "path": "b.pdf", **SPEC})
    assert read(pv, "a.pdf") == read(pv, "b.pdf")


def test_글꼴이_없으면_Helvetica로_내려가고_알린다(pv, monkeypatch):
    monkeypatch.setattr(PF, "find_font_path", lambda: None)
    r = export(pv, {"views": [F.V1], "path": "s.pdf", **SPEC})
    assert r["ok"], r
    assert r["result"]["font_embedded"] is None and r["result"]["non_ascii_dropped"] is True
    assert any("글꼴" in w for w in r.get("warnings", [])), r
    pdf = read(pv, "s.pdf")
    assert b"/Helvetica" in pdf and b"/FontFile2" not in pdf


def test_환경변수_글꼴이_없는_파일이면_글꼴_없음으로_본다(monkeypatch, tmp_path):
    monkeypatch.setenv(PF.ENV_VAR, str(tmp_path / "없음.ttf"))
    assert PF.find_font_path() is None and PF.load_pdf_font() is None


def test_내장금지_글꼴은_쓰지_않는다(monkeypatch, tmp_path):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A"])
    fb.setupCharacterMap({65: "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0)), pen.lineTo((0, 500)), pen.lineTo((500, 500)), pen.closePath()
    g = pen.glyph()
    fb.setupGlyf({".notdef": g, "A": g})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "A": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    fb.setupOS2(fsType=0x0002)
    fb.setupPost()
    p = tmp_path / "restricted.ttf"
    fb.save(str(p))
    monkeypatch.setenv(PF.ENV_VAR, str(p))
    assert PF.load_pdf_font() is None
    fb.setupOS2(fsType=0)
    fb.save(str(p))
    PF._CACHE.clear()
    assert PF.load_pdf_font() is not None


# ---------------------------------------------------------------- 순수 함수
def test_축척_막대는_용지_안에_든다():
    for scale in (1, 20, 50, 100, 200, 500, 1000):
        m, mm = SH._nice_bar(scale)
        assert 0 < mm <= 70.0 and abs(mm - m * 1000 / scale) < 1e-9


def test_글_줄바꿈은_줄수를_넘기면_줄임표():
    lines = SH.wrap("가" * 100, 4.0, 40.0, 2)
    assert len(lines) == 2 and lines[-1].endswith("…")
    assert SH.wrap("짧다", 4.0, 40.0, 2) == ["짧다"]
