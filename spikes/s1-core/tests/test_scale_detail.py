# SPDX-License-Identifier: MPL-2.0
"""축척별 표현 수준(SC-09, B-17): 1:50 이하 큰 축척 평면에 마감선·문짝 두께·문턱선을 더한다. 켜지 않으면·1:100 이면 바이트가 같고, 증분 == 전체 재생성."""
import sys

import pytest

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")

SPEC = {"project_name": "시험", "sheet_name": "도면", "sheet_number": "A-101", "date": "2026-11-15"}


def run_export(sess, cmd, args):
    r = sess.run(cmd, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return sess.run(cmd, args, confirm=r["error"]["details"]["confirm_token"])


def read(sess, name):
    return (sess.path.parent / name).read_bytes()


def mkview(sess, scale):
    r = sess.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "scale": scale})
    assert r["ok"], r
    return F.V1


def detail_lines(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_detail_lines, vid)


def full_detail(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return s3lib.RefGraph(s3lib._Inputs(sess.eng)).q(D.view_detail_lines, vid)


def test_큰_축척_평면에_마감선_문짝_문턱선이_생긴다(base):
    v = mkview(base, 50)
    ln = detail_lines(base, v)
    cls = [x[0] for x in ln]
    assert cls.count("hatch") >= 5 and cls.count("symbol") == 4        # W1(재료 있음) 양면 마감선 + 문턱선 2, 문 2개의 문짝 두께 선 2줄씩
    # 마감선은 벽 면에서 FINISH_T 만큼 안쪽(W1 은 y=0 축선, 두께 200: y=±80)
    fin = [x for x in ln if x[0] == "hatch" and abs(x[2] - x[4]) < 1e-6 and abs(abs(x[2]) - 80.0) < 1e-3]
    assert len(fin) >= 3


def test_작은_축척_100은_상세가_없다(base):
    v = mkview(base, 100)
    assert detail_lines(base, v) == ()


def test_재료_없는_벽은_마감선이_없다(base):
    v = mkview(base, 50)
    # W3 에는 재료가 없다: y=4000±80 의 마감선이 없어야 한다
    assert not [x for x in detail_lines(base, v) if x[0] == "hatch" and abs(x[2] - x[4]) < 1e-6 and abs(abs(x[2]) - 3920.0) < 1e-3]
    assert base.run("material.set", {"id": F.W3, "name": "Brick"})["ok"]
    assert [x for x in detail_lines(base, v) if x[0] == "hatch" and abs(x[2] - x[4]) < 1e-6 and abs(abs(x[2]) - 3920.0) < 1e-3 or abs(x[2] - 4080.0) < 1e-3]


@pytest.mark.parametrize("ext", ["svg", "pdf"])
def test_detail을_안_켜면_시트가_바이트까지_같다(base, ext):
    v = mkview(base, 50)
    a = {"views": [v], "path": "a." + ext, **SPEC}
    run_export(base, "sheet.export", a)
    run_export(base, "sheet.export", {**a, "path": "b." + ext, "detail": False})
    assert read(base, "a." + ext) == read(base, "b." + ext)


def test_축척_100_뷰는_detail을_켜도_바이트가_같다(base):
    v = mkview(base, 100)
    a = {"views": [v], "path": "a.svg", **SPEC}
    run_export(base, "sheet.export", a)
    run_export(base, "sheet.export", {**a, "path": "b.svg", "detail": True})
    assert read(base, "a.svg") == read(base, "b.svg")


def test_큰_축척에서_detail을_켜면_선이_늘어난다(base):
    v = mkview(base, 50)
    a = {"views": [v], "path": "a.svg", **SPEC}
    run_export(base, "sheet.export", a)
    run_export(base, "sheet.export", {**a, "path": "b.svg", "detail": True})
    s0, s1 = read(base, "a.svg").decode(), read(base, "b.svg").decode()
    assert s1.count("<line") > s0.count("<line") + 8


def test_상세_선은_증분이_전체_재생성과_같다(base):
    v = mkview(base, 50)
    assert detail_lines(base, v) == full_detail(base, v)
    assert base.run("material.set", {"id": F.W3, "name": "Brick"})["ok"]
    assert detail_lines(base, v) == full_detail(base, v)
    assert base.run("wall.move", {"id": F.W1, "dx": 0, "dy": -300})["ok"]
    assert detail_lines(base, v) == full_detail(base, v)
    assert base.run("history.undo", {})["ok"]
    assert detail_lines(base, v) == full_detail(base, v)
    assert base.run("view.set", {"id": v, "scale": 100})["ok"]
    assert detail_lines(base, v) == () == full_detail(base, v)


def test_상세_선은_결정적이다(tmp_path):
    def build(name):
        s = s3lib.kit.Session(tmp_path / name, guid=s3lib.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        v = mkview(s, 50)
        out = (detail_lines(s, v), s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a[0] == b[0] and a[1]["state"] == b[1]["state"]
