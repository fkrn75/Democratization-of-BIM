# SPDX-License-Identifier: MPL-2.0
"""dobim_render (16단계): 뷰·시트를 PNG 로. 순수 파이썬 래스터(raster.py)의 결정성·크기 상한·영역 잘라내기·빈 뷰·오류, 화소 단위 내용
(벽 절단선이 검게, 한글 문자 화소 존재), 레지스트리 커맨드(view.image·sheet.image·.png 내보내기), MCP ImageContent, CLI PNG 파일 쓰기."""
import asyncio
import base64
import json
import struct
import subprocess
import sys
import zlib

import pytest

import kit
from conftest import ROOT
from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core import pdf_font as PF
from dobim_core import raster as RA
from dobim_core import sheet as SH
from test_protocol import ENV, _client, cli

needs_font = pytest.mark.skipif(PF.find_font_path() is None, reason="한글 글꼴(Noto Sans KR·나눔고딕)이 없다")


def decode(png: bytes):
    """PNG(RGB 8비트, 필터 0) -> (너비, 높이, 행 목록(bytes)). 이 래스터가 쓰는 형식만 읽는다."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", 0
    while pos < len(png):
        n = struct.unpack(">I", png[pos:pos + 4])[0]
        tag, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + n]
        assert zlib.crc32(tag + data) & 0xFFFFFFFF == struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])[0]
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", data[:10])
            assert (depth, ctype) == (8, 2)
        elif tag == b"IDAT":
            idat += data
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = w * 3 + 1
    rows = []
    for y in range(h):
        assert raw[y * stride] == 0
        rows.append(raw[y * stride + 1:(y + 1) * stride])
    return w, h, rows


def px(rows, x, y):
    return tuple(rows[y][x * 3:x * 3 + 3])


def dark(rows, x, y, r=1):
    """(x,y) 둘레 r 화소 안에 어두운 화소(밝기 < 100)가 있는가."""
    return any(sum(px(rows, i, j)) < 300 for j in range(max(0, y - r), min(len(rows), y + r + 1)) for i in range(max(0, x - r), min(len(rows[0]) // 3, x + r + 1)))


def ink_count(rows, x0, y0, x1, y1):
    return sum(1 for y in range(y0, y1) for x in range(x0, x1) if sum(px(rows, x, y)) < 600)


def build(ws):
    """기준 프로젝트 + 평면 뷰 + 공간(사무실) + 저장 시트. 닫아서 다른 프로세스(CLI·MCP)가 열 수 있게 한다."""
    s = kit.Session(ws / "t.dobim")
    for name, args in F.SCRIPT:
        assert s.run(name, args)["ok"]
    for name, args in (("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}),
                       ("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "name": "사무실", "category": "사무실"}),
                       ("sheet.define", {"id": F.SH1, "name": "A-101 1층 평면", "views": [F.V1], "paper": "A3", "project_name": "예시 프로젝트", "sheet_number": "A-101", "date": "2026-01-01"})):
        assert s.run(name, args)["ok"], name
    return s


@pytest.fixture
def proj(tmp_path):
    s = build(tmp_path)
    yield s
    s.close()


def image(s, cmd, **args):
    att = []
    from dobim_core import dispatch
    env = dispatch.run(s.env, cmd, args, project=s.path.name, attach=att)
    return env, (att[0] if att else None)


def page_of(s, vid):
    s.eng.sync()
    s.eng._touch_pending()

    class Ctx:   # _render_view 가 쓰는 만큼만: 엔진 읽기 문맥
        pass
    from dobim_core.engine import Ctx as EC
    ctx = EC(s.eng, s.eng.head + 1, s.eng.actor, True)
    return SH.build_view_page(C._render_view(ctx, vid))


# ---------------------------------------------------------------- 래스터 자체
def test_같은_입력은_같은_PNG_바이트다(proj):
    e1, p1 = image(proj, "view.image", id=F.V1, max_px=900)
    e2, p2 = image(proj, "view.image", id=F.V1, max_px=900)
    assert e1["ok"] and p1 == p2 and p1[:8] == b"\x89PNG\r\n\x1a\n"
    assert e1["result"]["sha256"] == e2["result"]["sha256"]
    s1, q1 = image(proj, "sheet.image", id=F.SH1, max_px=900)
    s2, q2 = image(proj, "sheet.image", id=F.SH1, max_px=900)
    assert q1 == q2
    other = kit.Session(proj.path.parent / "u.dobim", create=True)   # 다른 프로젝트 파일에서 같은 입력을 다시 만들어도 같다
    try:
        for name, args in F.SCRIPT:
            other.run(name, args)
        for name, args in (("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}),
                           ("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "name": "사무실", "category": "사무실"})):
            other.run(name, args)
        _, p3 = image(other, "view.image", id=F.V1, max_px=900)
    finally:
        other.close()
    assert p3 == p1


def test_크기_상한과_영역_잘라내기(proj):
    for m in (200, 640, 1600):
        e, p = image(proj, "sheet.image", id=F.SH1, max_px=m)
        w, h, _ = decode(p)
        assert max(w, h) == m and e["result"]["width"] == w and e["result"]["height"] == h
    e, p = image(proj, "sheet.image", id=F.SH1, max_px=300)
    full_w, full_h, _ = decode(p)
    assert (full_w, full_h) == (300, 212)                                   # A3 420x297
    e, p = image(proj, "sheet.image", id=F.SH1, max_px=300, crop=[0.0, 0.0, 0.5, 0.5])
    w, h, _ = decode(p)
    assert max(w, h) == 300 and abs(w / h - 420 / 297) < 0.02              # 영역(절반)에도 상한을 쓴다: 같은 비율로 두 배 크게
    assert e["result"]["region_mm"] == pytest.approx([0.0, 0.0, 210.0, 148.5], abs=1.0)
    assert e["result"]["px_per_mm"] == pytest.approx(300 / 210, rel=1e-3)
    for bad in ({"max_px": 10}, {"max_px": 99999}, {"crop": [0.6, 0.0, 0.5, 1.0]}, {"crop": [0, 0, 1]}, {"crop": [0, 0, 1.5, 1]}):
        e, _ = image(proj, "sheet.image", id=F.SH1, **bad)
        assert not e["ok"] and e["error"]["code"] in ("SCHEMA_INVALID", "CONSTRAINT"), bad


def test_빈_뷰도_그려진다(sess):
    assert sess.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})["ok"]
    assert sess.run("view.create", {"id": F.V1, "name": "빈 평면", "type": "plan", "level": F.LV1})["ok"]
    e, p = image(sess, "view.image", id=F.V1, max_px=500)
    assert e["ok"]
    w, h, rows = decode(p)
    assert max(w, h) == 500
    pg = page_of(sess, F.V1)
    assert [p[0] for p in pg.prims] == ["text"]            # 그릴 것은 제목 줄뿐이다
    _, _, blank = decode(RA.render([], pg.w, pg.h, max_px=500)["png"])
    assert ink_count(blank, 0, 0, w, h) == 0               # 요소가 없으면 전부 흰색
    assert ink_count(rows, 0, 0, w, h) > 0                 # 제목 줄 글자는 있다
    s, q = image(sess, "sheet.image", views=[F.V1], paper="A3")
    assert s["ok"]                                          # 빈 뷰를 올린 시트(표제란만 있다)


def test_없거나_잘못된_id는_오류이고_이미지가_없다(proj):
    for cmd, args in (("view.image", {"id": "9" * 22}), ("view.image", {"id": F.W1}), ("sheet.image", {"id": "9" * 22}),
                      ("sheet.image", {"views": ["9" * 22]}), ("sheet.image", {"id": F.SH1, "views": [F.V1]}), ("sheet.image", {})):
        e, p = image(proj, cmd, **args)
        assert not e["ok"] and p is None, (cmd, args)
        assert e["error"]["code"] in ("NOT_FOUND", "SCHEMA_INVALID"), e["error"]


def test_벽_절단선_위치_화소는_검고_바탕은_희다(proj):
    pg = page_of(proj, F.V1)
    rr = RA.render(pg.prims, pg.w, pg.h, max_px=1000)
    w, h, rows = decode(rr["png"])
    k = rr["px_per_mm"]
    cuts = [p for p in pg.prims if p[0] == "line" and p[5] >= 0.5]            # 절단선(굵기 0.5mm)
    assert cuts
    hit = 0
    for _, x0, y0, x1, y1, *_ in cuts:
        cx, cy = round((x0 + x1) / 2 * k), round((y0 + y1) / 2 * k)
        assert dark(rows, cx, cy), "절단선 한가운데 화소가 칠해져야 한다"
        hit += 1
    assert hit == len(cuts)
    assert px(rows, 2, 2) == (255, 255, 255) and px(rows, w - 3, h - 3) == (255, 255, 255)
    fills = [p for p in pg.prims if p[0] == "poly" and p[2]]                  # 벽 채움(회색 #cfcfcf)
    assert fills
    xs = [q[0] for q in fills[0][1]]
    ys = [q[1] for q in fills[0][1]]
    cxp, cyp = round(sum(xs) / len(xs) * k), round(sum(ys) / len(ys) * k)
    assert px(rows, cxp, cyp) == (0xcf, 0xcf, 0xcf)


@needs_font
def test_한글_문자_화소가_있다(proj):
    pg = page_of(proj, F.V1)
    rr = RA.render(pg.prims, pg.w, pg.h, max_px=1400)
    assert rr["font"]
    w, h, rows = decode(rr["png"])
    k = rr["px_per_mm"]
    txt = [p for p in pg.prims if p[0] == "text" and p[4] == "사무실"]
    assert len(txt) == 1
    _, x, y, size, s, anchor, *_ = txt[0]
    half = len(s) * size / 2
    x0, x1, y0, y1 = int((x - half) * k) - 1, int((x + half) * k) + 1, int((y - size) * k) - 1, int(y * k) + 1
    assert ink_count(rows, x0, y0, x1, y1) > 40                                # 글자 획 화소
    blank = RA.render([p for p in pg.prims if p[0] != "text"], pg.w, pg.h, max_px=1400)
    _, _, rows2 = decode(blank["png"])
    assert ink_count(rows2, x0, y0, x1, y1) == 0                               # 문자를 빼면 그 자리는 비어 있다


def test_글꼴이_없으면_글자_칸을_그리고_경고한다(proj, monkeypatch):
    monkeypatch.setenv(PF.ENV_VAR, str(proj.path.parent / "no-such.ttf"))
    pg = page_of(proj, F.V1)
    rr = RA.render(pg.prims, pg.w, pg.h, max_px=600)
    assert rr["font"] is None and rr["text_boxes"] > 0 and rr["png"][:4] == b"\x89PNG"


def test_큰_A1_시트도_상한_안으로_줄이고_수_초_안에_그린다(proj):
    import time
    e0, _ = image(proj, "sheet.image", views=[F.V1], paper="A1", max_px=1600)     # 글꼴 부분집합을 먼저 데운다
    t = time.perf_counter()
    e, p = image(proj, "sheet.image", views=[F.V1, F.V1][:1], paper="A1", max_px=1600, project_name="속도 시험")
    dt = time.perf_counter() - t
    w, h, _ = decode(p)
    assert (w, h) == (1600, 1130) and dt < 20.0, dt


# ---------------------------------------------------------------- 레지스트리 커맨드
def test_view_image는_파일을_쓰지_않고_봉투에는_요약만_담는다(proj):
    before = sorted(x.name for x in proj.path.parent.iterdir())
    e, p = image(proj, "view.image", id=F.V1, max_px=700)
    assert e["ok"] and "_png" not in json.dumps(e) and p is not None
    r = e["result"]
    assert r["format"] == "png" and r["bytes"] == len(p) and max(r["width"], r["height"]) == 700 and len(r["sha256"]) == 64
    e2, _ = image(proj, "view.image", id=F.V1, max_px=700)                         # attach 를 주지 않으면(CLI·dobim_query) 이미지는 버린다
    from dobim_core import dispatch
    e3 = dispatch.run(proj.env, "view.image", {"id": F.V1, "max_px": 700}, project=proj.path.name)
    assert e3["ok"] and e3 == e2
    assert sorted(x.name for x in proj.path.parent.iterdir()) == before


def test_sheet_image는_저장_시트와_같은_인자가_같은_이미지를_낸다(proj):
    _, a = image(proj, "sheet.image", id=F.SH1, max_px=800)
    _, b = image(proj, "sheet.image", views=[F.V1], paper="A3", project_name="예시 프로젝트", sheet_number="A-101", date="2026-01-01", max_px=800)
    assert a == b


def test_png_내보내기는_같은_이미지이고_확인_토큰_규칙을_따른다(proj, tmp_path):
    _, want = image(proj, "view.image", id=F.V1, max_px=800)
    r = proj.run("view.export", {"id": F.V1, "path": "v.png", "max_px": 800})
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED" and r["error"]["details"]["preview"]["format"] == "png"
    tok = r["error"]["details"]["confirm_token"]
    r = proj.run("view.export", {"id": F.V1, "path": "v.png", "max_px": 800}, confirm=tok)
    assert r["ok"] and r["result"]["format"] == "png" and r["result"]["pixels"][0] <= 800
    assert (tmp_path / "v.png").read_bytes() == want
    r = proj.run("view.export", {"id": F.V1, "path": "v.png", "max_px": 800})
    assert not r["ok"] and r["error"]["code"] == "FILE_EXISTS"                    # 덮어쓰기 동의 없이는 확인 전에 거절
    # 시트: sheet.export·sheet.render 도 .png 를 받고 같은 시트 이미지를 낸다
    _, sheet_want = image(proj, "sheet.image", id=F.SH1, max_px=800)
    for cmd, args in (("sheet.render", {"id": F.SH1, "path": "s1.png", "max_px": 800}),
                      ("sheet.export", {"views": [F.V1], "paper": "A3", "project_name": "예시 프로젝트", "sheet_number": "A-101", "date": "2026-01-01", "path": "s2.png", "max_px": 800})):
        tok = proj.run(cmd, args)["error"]["details"]["confirm_token"]
        r = proj.run(cmd, args, confirm=tok)
        assert r["ok"] and r["result"]["format"] == "png", r
    assert (tmp_path / "s1.png").read_bytes() == sheet_want == (tmp_path / "s2.png").read_bytes()


def test_저장_시트_정의에는_png_인자가_들어가지_않는다(proj):
    row = proj.eng.st.el[F.SH1]["params"]
    assert "max_px" not in row and "crop" not in row


# ---------------------------------------------------------------- CLI
def test_cli는_png_파일을_쓴다(tmp_path):
    build(tmp_path).close()
    code, out, _ = cli(tmp_path, "run", "view.export", "-p", "t.dobim", f"id={F.V1}", "path=cli.png", "max_px=640")
    env = json.loads(out)
    assert code != 0 and env["error"]["code"] == "CONFIRM_REQUIRED"
    code, out, _ = cli(tmp_path, "run", "view.export", "-p", "t.dobim", f"id={F.V1}", "path=cli.png", "max_px=640", "--confirm", env["error"]["details"]["confirm_token"])
    env = json.loads(out)
    assert code == 0 and env["ok"], env
    w, h, rows = decode((tmp_path / "cli.png").read_bytes())
    assert max(w, h) == 640 and env["result"]["pixels"] == [w, h]
    code, out, _ = cli(tmp_path, "run", "view.image", "-p", "t.dobim", f"id={F.V1}", "max_px=640")      # 읽기 커맨드: 봉투 요약만(파일 없음)
    env = json.loads(out)
    assert code == 0 and env["result"]["bytes"] > 0 and not (tmp_path / "image.png").exists()


def test_다른_프로세스에서도_같은_바이트다(tmp_path):
    build(tmp_path).close()
    code, out, _ = cli(tmp_path, "run", "view.image", "-p", "t.dobim", f"id={F.V1}", "max_px=900")
    sha_cli = json.loads(out)["result"]["sha256"]
    s = kit.Session(tmp_path / "t.dobim", create=False)   # 글꼴·글리프 캐시가 빈 새 프로세스(CLI)와 데워진 이 프로세스가 같은 이미지를 낸다
    try:
        e, _ = image(s, "view.image", id=F.V1, max_px=900)
    finally:
        s.close()
    assert e["result"]["sha256"] == sha_cli


# ---------------------------------------------------------------- MCP
def test_mcp_dobim_render는_ImageContent를_돌려준다(tmp_path):
    build(tmp_path).close()

    async def go():
        async with _client(tmp_path) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            assert tools["dobim_render"].annotations.read_only_hint is True
            assert len(json.dumps(tools["dobim_render"].input_schema)) < 2500
            r = await c.call_tool("dobim_render", {"target": "view", "id": F.V1, "max_px": 700, "project": "t.dobim"})
            assert not r.is_error
            img = [x for x in r.content if x.type == "image"]
            assert len(img) == 1 and img[0].mime_type == "image/png"
            png = base64.b64decode(img[0].data)
            w, h, rows = decode(png)
            assert max(w, h) == 700
            meta = r.structured_content
            assert meta["ok"] and meta["result"]["width"] == w and "_png" not in json.dumps(meta)
            assert json.loads([x for x in r.content if x.type == "text"][0].text) == meta
            # 시트: 저장된 정의(id)·sheet.export 인자(sheet)·영역 잘라내기
            r1 = await c.call_tool("dobim_render", {"target": "sheet", "id": F.SH1, "max_px": 500, "project": "t.dobim"})
            r2 = await c.call_tool("dobim_render", {"target": "sheet", "sheet": {"views": [F.V1], "paper": "A3", "project_name": "예시 프로젝트", "sheet_number": "A-101", "date": "2026-01-01"},
                                                    "max_px": 500, "project": "t.dobim"})
            assert r1.content[0].data == r2.content[0].data
            r3 = await c.call_tool("dobim_render", {"target": "sheet", "id": F.SH1, "max_px": 500, "crop": [0, 0, 0.5, 0.5], "project": "t.dobim"})
            assert r3.content[0].data != r1.content[0].data and max(decode(base64.b64decode(r3.content[0].data))[:2]) == 500
            # 오류: 이미지가 없고 is_error
            for args in ({"target": "view", "project": "t.dobim"}, {"target": "view", "id": "9" * 22, "project": "t.dobim"},
                         {"target": "sheet", "id": "9" * 22, "project": "t.dobim"}, {"target": "bogus", "id": F.V1, "project": "t.dobim"},
                         {"target": "view", "id": F.V1, "max_px": 5, "project": "t.dobim"}):
                r = await c.call_tool("dobim_render", args)
                assert r.is_error and not [x for x in r.content if x.type == "image"], args
            # dobim_query 로 view.image 를 부르면 같은 요약만(이미지 없음): CLI 와 같은 봉투
            r = await c.call_tool("dobim_query", {"command": "view.image", "args": {"id": F.V1, "max_px": 700}, "project": "t.dobim"})
            assert not r.is_error and not [x for x in r.content if x.type == "image"] and r.structured_content["result"]["sha256"] == meta["result"]["sha256"]
    asyncio.run(go())
