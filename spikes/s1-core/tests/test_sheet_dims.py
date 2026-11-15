# SPDX-License-Identifier: MPL-2.0
"""시트 치수 자동 배치(E-04 입면 치수 사슬, L-09 복도·계단 폭): dims 입력으로만 켜고, 안 주면 시트가 바이트까지 같다."""
import re

import pytest

from dobim_core import fixture as F
from dobim_core import sheet_model as SM

pytestmark = pytest.mark.filterwarnings("ignore")

SPEC = {"project_name": "시험", "sheet_name": "도면", "sheet_number": "A-101", "date": "2026-11-15"}
V3 = "4" + "0" * 20 + "3"      # 입면 뷰 id(남측: 건물 정면, 보는 방향 북)
V4 = "4" + "0" * 20 + "4"      # 입면 뷰 id(동측)


def run_export(sess, cmd, args):
    r = sess.run(cmd, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return sess.run(cmd, args, confirm=r["error"]["details"]["confirm_token"])


def read(sess, name):
    return (sess.path.parent / name).read_bytes().decode("utf-8") if name.endswith("svg") else (sess.path.parent / name).read_bytes()


def texts(svg):
    return re.findall(r'<text[^>]*>([^<]*)</text>', svg)


@pytest.fixture
def pv(base):
    assert base.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1})["ok"]
    assert base.run("view.create", {"id": V3, "name": "남측 입면", "type": "elevation", "dir": "S"})["ok"]
    assert base.run("view.create", {"id": V4, "name": "동측 입면", "type": "elevation", "dir": "E"})["ok"]
    return base


@pytest.fixture
def rich(pv):
    """복도(좁은 방)와 거실, 계단이 있는 모델."""
    r = pv.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[100, 100], [5900, 100], [5900, 1300], [100, 1300]], "name": "복도", "category": "복도"})
    assert r["ok"], r
    assert pv.run("space.create", {"id": "8" + "0" * 20 + "2", "level": F.LV1, "boundary": [[100, 1500], [5900, 1500], [5900, 3900], [100, 3900]], "name": "거실", "category": "거실"})["ok"]
    r = pv.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000.0, 2500.0, 3500.0, 2500.0], "width": 1200.0, "height": 3300.0, "risers": 15})
    assert r["ok"], r
    return pv


def exp(sess, name, views, **kw):
    r = run_export(sess, "sheet.export", {"views": views, "path": name, "paper": "A3", **SPEC, **kw})
    assert r["ok"], r
    return read(sess, name)


@pytest.mark.parametrize("ext", ["svg", "pdf"])
def test_dims를_안_주면_바이트가_같다(rich, ext):
    vs = [F.V1, V3]
    a = exp(rich, "a." + ext, vs)
    assert exp(rich, "b." + ext, vs, dims=[]) == a


def test_입면_치수_사슬은_벽_중심선_간격과_전체_폭이다(pv):
    d = SM.elev_dims(_graph(pv), V3)
    # 남측 입면(S): 정면 벽 W1(0..6000)과 옆면 벽 W2(x=6000)·W3(0..6000, y=4000)의 끝점: 0 과 6000
    assert d["overall"][2] == "6000" and abs(d["overall"][1] - d["overall"][0] - 6000.0) < 1e-6
    assert d["chain"] == []                                            # 위치가 2개뿐이면 전체 폭만
    d = SM.elev_dims(_graph(pv), V4)                                   # 동측 입면: 길이 4000
    assert d["overall"][2] == "4000"


def _graph(sess):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g


def test_입면_치수가_시트에_그려지고_뷰_아래를_더_비운다(pv):
    s0 = exp(pv, "a.svg", [V3])
    s1 = exp(pv, "b.svg", [V3], dims=["elevation"])
    assert 'id="elevation-dims" data-count="1"' in s1 and "elevation-dims" not in s0
    assert "6000" in texts(s1)
    assert s1.count("<line") > s0.count("<line")


def test_복도_폭과_계단_폭_치수(rich):
    items = SM.plan_dims(_graph(rich), F.V1, {"corridor", "stair"})
    assert [i[4] for i in items["corridor"]] == ["1200"]               # 복도 외접 사각형의 짧은 변(y 방향 1200)
    x0, y0, x1, y1, t = items["corridor"][0]
    assert x0 == x1 and (y0, y1) == (100.0, 1300.0)
    assert [i[4] for i in items["stair"]] == ["1200"]
    s = exp(rich, "p.svg", [F.V1], dims=["corridor", "stair"])
    assert 'id="corridor-dims" data-count="1"' in s and 'id="stair-dims" data-count="1"' in s
    assert texts(s).count("1200") >= 2


def test_복도가_아닌_공간은_폭_치수가_없다(pv):
    assert pv.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[100, 100], [1300, 100], [1300, 3900], [100, 3900]], "name": "침실", "category": "침실"})["ok"]
    assert SM.plan_dims(_graph(pv), F.V1, {"corridor"}) == {"corridor": []}


def test_치수_글자가_프로파일_형식을_따른다(pv):
    s = exp(pv, "a.svg", [V3], dims=["elevation"], profile={"dim_thousands": True, "text_height": {"dimension": 3.2}})
    m = re.findall(r'<text[^>]*font-size="([\d.]+)"[^>]*>6,000</text>', s)
    assert m == ["3.2"]


def test_저장하는_시트_정의에는_dims를_넣지_않는다(pv):
    assert not pv.run("sheet.define", {"name": "S", "views": [F.V1], "dims": ["stair"]})["ok"]


def test_입면_치수는_가까운_벽_위치를_한_위치로_묶는다(pv):
    # 옆면이 보이는 내벽(x=3000)은 중심선 위치를 내고, 외벽 안쪽에서 끝나는 짧은 내벽 끝점(x=100)은 외벽 끝점(0)에 묶인다(160mm 같은 두께 간격 치수가 안 생긴다)
    assert pv.run("wall.create", {"id": "1" + "0" * 20 + "9", "level": F.LV1, "axis": [3000, 0, 3000, 4000], "thickness": 100, "height": 3000})["ok"]
    assert pv.run("wall.create", {"id": "1" + "0" * 20 + "8", "level": F.LV1, "axis": [100, 2000, 2950, 2000], "thickness": 100, "height": 3000})["ok"]
    d = SM.elev_dims(_graph(pv), V3)
    assert [c[2] for c in d["chain"]] == ["3000", "3000"] and d["overall"][2] == "6000"
