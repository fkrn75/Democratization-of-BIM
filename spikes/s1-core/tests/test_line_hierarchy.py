# SPDX-License-Identifier: MPL-2.0
"""선 위계(LN-03·LN-04)·제3각법(T-11): 숨은 선, 치수선을 포함한 굵기 위계, 투상법 기호. 모두 sheet.export profile 입력이며 주지 않으면 시트가 바이트까지 같다."""
import re

import pytest

from conftest import ROOT  # noqa: F401
from dobim_core import drawing as D
from dobim_core import fixture as F

pytestmark = pytest.mark.filterwarnings("ignore")

SPEC = {"project_name": "시험", "sheet_name": "도면", "sheet_number": "A-101", "date": "2026-11-15"}
VE = "4" + "0" * 20 + "7"


def run_export(sess, cmd, args):
    r = sess.run(cmd, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return sess.run(cmd, args, confirm=r["error"]["details"]["confirm_token"])


def read(sess, name):
    return (sess.path.parent / name).read_bytes()


@pytest.fixture
def pv(base):
    assert base.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1})["ok"]
    assert base.run("view.create", {"id": VE, "name": "남측 입면", "type": "elevation", "dir": "S"})["ok"]
    assert base.run("annot.dim.create", {"view": F.V1, "a": f"wall:{F.W1}#trim.start", "b": f"wall:{F.W1}#trim.end"})["ok"]
    return base


def out(sess, name, views=None, ext="svg", **kw):
    r = run_export(sess, "sheet.export", {"views": views or [F.V1], "path": f"{name}.{ext}", "overwrite": True, **SPEC, **kw})
    assert r["ok"], r
    return read(sess, f"{name}.{ext}")


def svg(sess, name, **kw):
    return out(sess, name, **kw).decode("utf-8")


# ---------------------------------------------------------------- 기본은 바이트 불변
def test_새_프로파일_입력을_안_주거나_꺼도_시트가_바이트까지_같다(pv):
    for views in ([F.V1], [VE], [F.V1, VE]):
        a = out(pv, "a", views=views)
        assert out(pv, "b", views=views, profile={}) == a
        assert out(pv, "c", views=views, profile={"hidden_lines": False, "line_hierarchy": False}) == a
        assert out(pv, "d", views=views, ext="pdf", profile={"hidden_lines": False}) == out(pv, "e", views=views, ext="pdf")
    assert b"hidden-lines" not in a and b"projection-symbol" not in a


# ---------------------------------------------------------------- LN-03 숨은 선
def hidden_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_hidden_lines, vid)


def test_입면에_앞_벽에_가려진_문_윤곽이_숨은_선으로_나온다(pv):
    hid = hidden_of(pv, VE)
    # 남측 입면: 남쪽 벽(W1)이 북쪽 벽(W3)의 문(D2: x 2500~3500, 높이 2100)을 가린다
    assert hid and all(h[0] == "hid" for h in hid)
    segs = {(round(h[1]), round(h[2]), round(h[3]), round(h[4])) for h in hid}
    assert (2500, 0, 2500, 2100) in segs and (3500, 0, 3500, 2100) in segs      # 문설주 두 개(인방은 띠 경계 위라 기존 가림 판정이 보이는 선으로 둔다)
    # 앞 벽의 실선과 겹치는 부분은 뺀다(실선이 우선): 같은 직선 위의 보이는 선과 겹치는 숨은 선이 없다
    pv.eng.sync()
    vis = pv.eng.g.q(D.view_lines, VE)
    for h in hid:
        for ln in vis:
            same_line = abs((h[3] - h[1]) * (ln[2] - h[2]) - (h[4] - h[2]) * (ln[1] - h[1])) < 1e-6 and abs((h[3] - h[1]) * (ln[4] - h[2]) - (h[4] - h[2]) * (ln[3] - h[1])) < 1e-6
            if same_line:
                a0, a1 = sorted((h[1] + h[2] * 1e-6, h[3] + h[4] * 1e-6))
                b0, b1 = sorted((ln[1] + ln[2] * 1e-6, ln[3] + ln[4] * 1e-6))
                assert min(a1, b1) - max(a0, b0) < 1e-3, (h, ln)


def test_평면과_배치도에는_숨은_선이_없다(pv):
    assert hidden_of(pv, F.V1) == ()


def test_숨은_선은_시트에서_파선으로_그려진다(pv):
    s = svg(pv, "h", views=[VE], profile={"hidden_lines": True})
    m = re.search(r'<g id="hidden-lines" data-count="(\d+)">(.*?)</g>', s, re.S)
    assert m and int(m.group(1)) == len(hidden_of(pv, VE))
    lines = re.findall(r"<line [^>]*/>", m.group(2))
    assert len(lines) == int(m.group(1)) and all('stroke-dasharray="2.4 1.2"' in ln and 'stroke-width="0.18"' in ln for ln in lines)
    # 보이는 선(실선)은 그대로다: 숨은 선을 뺀 나머지 그림이 기본 시트와 같다
    base = svg(pv, "b", views=[VE])
    assert m.group(0) not in base and s.replace(m.group(0) + chr(10), "") == base
    pdf = out(pv, "h", views=[VE], ext="pdf", profile={"hidden_lines": True})
    assert pdf != out(pv, "b", views=[VE], ext="pdf") and b"[" in pdf


def test_숨은_선_증분이_전체_재생성과_같다(pv):
    import sys
    sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
    import s3lib

    def check():
        pv.eng.sync()
        pv.eng._touch_pending()
        ref = s3lib.RefGraph(s3lib._Inputs(pv.eng))
        assert pv.eng.g.q(D.view_hidden_lines, VE) == ref.q(D.view_hidden_lines, VE)

    check()
    assert pv.run("door.place", {"host": F.W3, "offset": 5000, "width": 700, "height": 2000})["ok"]
    check()
    assert pv.run("wall.move", {"id": F.W1, "dx": 0, "dy": 300})["ok"]
    check()
    assert pv.run("history.undo", {})["ok"]
    check()


# ---------------------------------------------------------------- LN-04 굵기 위계
def stroke_widths(s, color):
    return sorted({float(w) for c, w in re.findall(r'stroke="(#[0-9a-f]{6})" stroke-width="([\d.]+)"', s) if c == color})


def test_위계를_켜면_치수선이_보이는_선보다_굵고_창호_기호선과_같다(pv):
    d0 = stroke_widths(svg(pv, "a"), "#1a4fa0")
    d1 = stroke_widths(svg(pv, "b", profile={"line_hierarchy": True}), "#1a4fa0")
    assert 0.13 in d0 and 0.13 not in d1                      # 기본: 치수선 0.13(보이는 선 0.18 보다 가늘다)
    assert d1 == [0.18, 0.25]                                 # 위계: 보조선 0.18 = 보이는 선, 치수선·눈금 0.25 = 기호선
    r = pv.run("drawing.profile", {"profile": {"line_hierarchy": True}})["result"]
    widths = [t["width_mm"] for t in r["line_hierarchy"]["tiers"]]
    assert widths == sorted(widths, reverse=True) and len(set(widths)) == 4          # 층마다 굵기가 다르고 위로 갈수록 굵다
    assert widths[0] == r["line_weights"]["cut"]["width_mm"] and widths[1] >= r["line_weights"]["proj"]["width_mm"]
    assert "line_hierarchy" not in pv.run("drawing.profile", {})["result"]


def test_위계는_시트_치수_사슬에도_적용된다(pv):
    s0 = svg(pv, "a", views=[VE], dims=["elevation"])
    s1 = svg(pv, "b", views=[VE], dims=["elevation"], profile={"line_hierarchy": True})
    assert 0.13 in stroke_widths(s0, "#1a4fa0") and 0.13 not in stroke_widths(s1, "#1a4fa0")


# ---------------------------------------------------------------- T-11 제3각법
def test_제3각법_기호가_표제란에_들어간다(pv):
    s = svg(pv, "p", profile={"projection": "third"})
    assert 'id="projection-symbol" data-method="third-angle"' in s and ">제3각법<" in s and ">투상법<" in s
    assert s.index('id="projection-symbol"') > s.index('id="title-block"')
    base = svg(pv, "b")
    assert 'id="projection-symbol"' not in base and ">제3각법<" not in base
    pdf = out(pv, "p", ext="pdf", profile={"projection": "third"})
    assert pdf.startswith(b"%PDF")
    r = pv.run("drawing.profile", {"profile": {"projection": "third"}})["result"]
    assert "제3각법" in r["projection"]["method"]


def test_프로파일_입력은_시트_정의에_저장되지_않는다(pv):
    r = pv.run("sheet.define", {"name": "S", "views": [F.V1], "profile": {"hidden_lines": True}})
    assert not r["ok"]
