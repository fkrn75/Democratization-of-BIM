# SPDX-License-Identifier: MPL-2.0
"""LN-04 비내력벽 구분: Pset_WallCommon.LoadBearing 이 명시적으로 거짓인 벽의 절단선은 선 굵기 위계(profile.line_hierarchy)를 켤 때만 한 단계 가늘다.
켜지 않으면 시트 SVG·PDF 가 바이트까지 같고, 증분 == 전체 재생성."""
import sys

import pytest

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")

SPEC = {"project_name": "시험", "sheet_name": "도면", "sheet_number": "A-101", "date": "2026-11-15"}


def ok(r):
    assert r["ok"], r
    return r["result"]


def export_file(sess, args):
    r = sess.run("sheet.export", args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    ok(sess.run("sheet.export", args, confirm=r["error"]["details"]["confirm_token"]))
    return (sess.path.parent / args["path"]).read_bytes()


def plan(sess):
    return ok(sess.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1, "scale": 100}))["id"]


def lines(sess, fn, v):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(fn, v)


def full(sess, fn, v):
    sess.eng.sync()
    sess.eng._touch_pending()
    return s3lib.RefGraph(s3lib._Inputs(sess.eng)).q(fn, v)


def test_LoadBearing_값이_없으면_위계_선분이_기본과_같다(base):
    v = plan(base)
    assert lines(base, D.view_lines_hier, v) == lines(base, D.view_lines, v)


def test_LoadBearing_거짓인_벽만_가는_절단선_등급이_된다(base):
    v = plan(base)
    cut0 = [x for x in lines(base, D.view_lines, v) if x[0] == "cut"]
    ok(base.run("pset.set", {"id": F.W3, "pset": "Pset_WallCommon", "prop": "LoadBearing", "value": False}))
    ok(base.run("pset.set", {"id": F.W1, "pset": "Pset_WallCommon", "prop": "LoadBearing", "value": True}))
    hier = lines(base, D.view_lines_hier, v)
    cn = [x for x in hier if x[0] == "cutn"]
    assert cn and all(abs(x[2] - 4000.0) < 200.0 or abs(x[4] - 4000.0) < 200.0 for x in cn)   # W3(y=4000) 둘레의 선만
    # 비내력벽 선은 cut 에서 빠진다(같은 선이 두 등급에 겹쳐 나오지 않는다)
    assert len([x for x in hier if x[0] == "cut"]) < len(cut0) + len(cn)
    assert not [x for x in hier if x[0] == "cut" and any(x[1:] == y[1:] for y in cn)]
    assert hier == full(base, D.view_lines_hier, v)
    # 내력(참)·값 없음은 그대로 cut
    assert any(x[0] == "cut" for x in hier)
    ok(base.run("pset.set", {"id": F.W3, "pset": "Pset_WallCommon", "prop": "LoadBearing", "value": True}))
    assert lines(base, D.view_lines_hier, v) == lines(base, D.view_lines, v)


def test_단면에서도_비내력벽의_절단선이_갈린다(base):
    ok(base.run("pset.set", {"id": F.W3, "pset": "Pset_WallCommon", "prop": "LoadBearing", "value": False}))
    vs = ok(base.run("view.create", {"id": F.V2, "name": "B-B", "type": "section", "p1": [3000, -1000], "p2": [3000, 5000], "depth": 8000, "scale": 100}))["id"]
    hier = lines(base, D.view_lines_hier, vs)
    assert [x for x in hier if x[0] == "cutn"] and [x for x in hier if x[0] == "cut"]
    assert hier == full(base, D.view_lines_hier, vs)


@pytest.mark.parametrize("ext", ["svg", "pdf"])
def test_위계를_안_켜면_LoadBearing이_있어도_바이트가_같다(base, ext):
    v = plan(base)
    a = {"views": [v], "path": "a." + ext, **SPEC}
    x = export_file(base, a)
    ok(base.run("pset.set", {"id": F.W3, "pset": "Pset_WallCommon", "prop": "LoadBearing", "value": False}))
    y = export_file(base, {**a, "path": "b." + ext})
    z = export_file(base, {**a, "path": "c." + ext, "profile": {"line_hierarchy": False}})
    assert x == y == z


def test_위계를_켜면_비내력벽_절단선이_따로_그려진다(base):
    v = plan(base)
    a = {"views": [v], "path": "a.svg", "profile": {"line_hierarchy": True}, **SPEC}
    x = export_file(base, a)
    assert b"cut-nonbearing" not in x                      # LoadBearing 값이 없으면 그룹이 없다
    ok(base.run("pset.set", {"id": F.W3, "pset": "Pset_WallCommon", "prop": "LoadBearing", "value": False}))
    y = export_file(base, {**a, "path": "b.svg"})
    assert b'id="cut-nonbearing"' in y and b'data-count="8"' in y   # W3 둘레 절단선 8개(문 개구부로 끊긴 외곽)
