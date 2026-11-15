# SPDX-License-Identifier: MPL-2.0
"""작성기준 프로파일 확장(B-09): 문자 높이·치수 형식(소수·천단위 쉼표)·기호 크기. 주지 않으면 시트가 바이트까지 같다."""
import re

import pytest

from dobim_core import fixture as F

pytestmark = pytest.mark.filterwarnings("ignore")

SPEC = {"project_name": "시험", "sheet_name": "도면", "sheet_number": "A-101", "date": "2026-11-15"}


def run_export(sess, cmd, args):
    r = sess.run(cmd, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return sess.run(cmd, args, confirm=r["error"]["details"]["confirm_token"])


def read(sess, name):
    return (sess.path.parent / name).read_bytes()


@pytest.fixture
def pv(base):
    assert base.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1})["ok"]
    assert base.run("annot.dim.create", {"view": F.V1, "a": f"wall:{F.W1}#trim.start", "b": f"wall:{F.W1}#trim.end"})["ok"]
    assert base.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[100, 100], [2900, 100], [2900, 3900], [100, 3900]], "name": "거실", "category": "거실"})["ok"]
    return base


def out(sess, name, **kw):
    r = run_export(sess, "sheet.export", {"views": [F.V1], "path": name, **SPEC, **kw})
    assert r["ok"], r
    return read(sess, name).decode("utf-8")


def texts(svg):
    return re.findall(r'<text[^>]*font-size="([\d.]+)"[^>]*>([^<]*)</text>', svg)


def test_프로파일을_안_주거나_비워도_바이트가_같다(pv):
    a = out(pv, "a.svg")
    assert out(pv, "b.svg", profile={}) == a
    assert out(pv, "c.svg", profile={"symbol_scale": 1.0}) == a          # 배율 1 은 값이 그대로다


def test_치수_천단위_쉼표와_소수_자릿수(pv):
    base = out(pv, "a.svg")
    assert any(t == "6000" for _, t in texts(base))
    s = out(pv, "b.svg", profile={"dim_thousands": True})
    assert any(t == "6,000" for _, t in texts(s)) and not any(t == "6000" for _, t in texts(s))
    s = out(pv, "c.svg", profile={"dim_thousands": True, "dim_decimals": 1})
    assert any(t == "6,000.0" for _, t in texts(s))
    s = out(pv, "d.svg", profile={"dim_decimals": 2})
    assert any(t == "6000.00" for _, t in texts(s))


def test_문자_높이가_바뀐다(pv):
    s = out(pv, "a.svg", profile={"text_height": {"dimension": 3.5, "room_name": 3.0, "room_area": 2.4, "caption": 5.0}})
    ts = texts(s)
    assert any(h == "3.5" and t == "6000" for h, t in ts)
    assert any(h == "3" and t == "거실" for h, t in ts) and any(h == "2.4" and t.endswith("㎡") for h, t in ts)
    assert any(h == "5" and t == "1F 평면" for h, t in ts)
    base = texts(out(pv, "b.svg"))
    assert any(h == "2.5" and t == "6000" for h, t in base)


def test_기호_크기_배율(pv):
    kw = {"grids": [{"label": "X1", "axis": "x", "pos": 0}], "tags": ["wall"]}
    r1 = re.findall(r'<circle[^>]*r="([\d.]+)"', out(pv, "a.svg", **kw))
    r2 = re.findall(r'<circle[^>]*r="([\d.]+)"', out(pv, "b.svg", profile={"symbol_scale": 2.0}, **kw))
    assert r1 and len(r1) == len(r2) and sorted(map(float, r2))[:5] == [4.8, 4.8, 4.8, 6.6, 6.6] and sorted(map(float, r1))[:5] == [2.4, 2.4, 2.4, 3.3, 3.3]   # 10 은 방위표(배율 밖)


def test_프로파일은_저장하는_시트_정의에_넣지_않는다(pv):
    r = pv.run("sheet.define", {"name": "S", "views": [F.V1], "profile": {"symbol_scale": 2.0}})
    assert not r["ok"]


def test_drawing_profile_유효_기준(pv):
    r0 = pv.run("drawing.profile", {})["result"]
    assert "applied_profile" not in r0 and r0["text_font"]["heights_mm"]["dimension"] == 2.5
    r = pv.run("drawing.profile", {"profile": {"text_height": {"dimension": 3.0}, "dim_thousands": True, "dim_decimals": 0}})["result"]
    assert r["text_font"]["heights_mm"]["dimension"] == 3.0 and r["dimension_leader"]["number_format"]["thousands_separator"] is True
    assert r["applied_profile"]["dim_decimals"] == 0
    r1 = pv.run("drawing.profile", {})["result"]
    assert r1 == r0                                                   # 주지 않으면 이전과 같은 값


def test_기호_안_문자_높이가_프로파일에_값으로_있고_배율을_따른다(pv):
    r0 = pv.run("drawing.profile", {})["result"]["text_font"]["symbol_text_heights_mm"]
    assert r0 == {"grid": 2.3, "section_mark": 1.9, "detail_callout": 2.0, "tag": 1.9}
    r = pv.run("drawing.profile", {"profile": {"symbol_scale": 2.0}})["result"]["text_font"]["symbol_text_heights_mm"]
    assert r == {"grid": 4.6, "section_mark": 3.8, "detail_callout": 4.0, "tag": 3.8}
