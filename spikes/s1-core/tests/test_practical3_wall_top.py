# SPDX-License-Identifier: MPL-2.0
"""21단계(실무 모델링 3) 23.2: 벽 상단 윤곽(top_profile)과 벽 상단 붙이기(wall.attach_top / wall.detach_top).
합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import walltop as WT

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

ROOF = {"id": F.RF1, "level": F.LV2, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "form": "gable", "pitch": 30, "overhang": 600}
GABLE = [[0.0, 3100.0], [2000.0, 4254.701], [4000.0, 3100.0]]   # W2(x=6000, y 0~4000)를 박공 지붕 아랫면에 붙인 윤곽(레벨 1F 기준)
PLATE = "f" + "0" * 20 + "7"
W5 = "1" + "0" * 20 + "5"


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


def fills_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_fills, vid)


def view(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def params(sess, eid):
    return sess.run("element.get", {"id": eid})["result"]["params"]


def test_박공_지붕_아랫면에_붙이면_박공_윤곽이_되고_취소하면_그대로다(base):
    h0 = base.eng.state_hash()
    assert base.run("roof.create", ROOF)["ok"]
    r = base.run("wall.attach_top", {"id": F.W2, "targets": [F.RF1]})
    assert r["ok"], r
    assert r["result"]["covered"] == [[0.0, 4000.0]] and r["result"]["points"] == 3
    assert params(base, F.W2)["top_profile"] == GABLE
    g = base.run("element.get", {"id": F.W2})["result"]["derived"]
    area = (3100.0 + 4254.701) / 2.0 * 4000.0
    assert g["top_min"] == 3100.0 and g["top_max"] == 4254.701 and g["gross_area_m2"] == pytest.approx(area / 1e6) and g["volume_m3"] == pytest.approx(area * 200 / 1e9)
    rows = {x[0]: x for x in base.run("schedule.rows", {"kind": "wall"})["result"]["items"]}
    assert rows[F.W2][5] == 4254.701 and rows[F.W2][7] == pytest.approx(area)
    assert rows[F.W3][5] == 3000.0                                      # 윤곽 없는 벽은 그대로
    # 다시 붙여도 같다(바뀌지 않음). 윗면에 붙이면 지붕 두께만큼 높다
    assert base.run("wall.attach_top", {"id": F.W2, "targets": [F.RF1]})["result"]["changed"] is False
    top = base.run("wall.attach_top", {"id": F.W2, "targets": [F.RF1], "face": "top"})["result"]
    assert top["top_min"] == pytest.approx(3300.0) and top["top_max"] == pytest.approx(4454.701)
    assert base.run("wall.detach_top", {"id": F.W2})["result"]["changed"] is True
    assert "top_profile" not in params(base, F.W2)
    assert base.run("wall.detach_top", {"id": F.W2})["result"]["changed"] is False
    for _ in range(4):
        assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


def test_윤곽_검사와_편집(base):
    assert "top_profile" not in params(base, F.W1)                     # 입력이 없으면 행에 키가 없다
    bad = [
        [[0, 3000], [5000, 3000]],                                     # 축 길이(6000)에서 끝나지 않음
        [[0, 3000], [3000, 0], [6000, 3000]],                          # z <= 0
        [[0, 3000], [4000, 3500], [3000, 3600], [6000, 3000]],         # s 가 줄어든다
        [[0, 3000], [0, 3500], [6000, 3000]],                          # 첫 구간이 수직 단
        [[0, 3000], [3000, 3000], [3000, 3500], [3000, 3200], [6000, 3000]],   # 같은 s 세 점
    ]
    for tp in bad:
        r = base.run("wall.set", {"id": F.W3, "top_profile": tp})
        assert not r["ok"] and r["error"]["code"] == "CONSTRAINT", (tp, r)
    # 단이 있는 윤곽: 3000 에서 3000 -> 3600 으로 오른다
    step = [[0, 3000], [3000, 3000], [3000, 3600], [6000, 3600]]
    assert base.run("wall.set", {"id": F.W3, "top_profile": step})["ok"]
    assert base.run("element.get", {"id": F.W3})["result"]["derived"]["gross_area_m2"] == pytest.approx((3000 * 3000 + 3000 * 3600) / 1e6)
    # 호스팅 문(D2: offset 3000, 높이 2100)보다 낮은 윤곽은 거절, 문을 윤곽 위로 높이면 거절
    r = base.run("wall.set", {"id": F.W3, "top_profile": [[0, 2000], [6000, 2000]]})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    r = base.run("door.set", {"id": F.D2, "offset": 1000, "height": 3100})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    assert base.run("door.set", {"id": F.D2, "offset": 4500, "height": 3100})["ok"]   # 높은 쪽(3600)이면 된다
    # 모양 개구부가 윤곽 위로 나가면 거절
    r = base.run("wall.set", {"id": F.W3, "openings": [{"shape": "round", "offset": 1000, "sill": 1000, "width": 800, "height": 2500}]})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    assert base.run("wall.set", {"id": F.W3, "openings": [{"shape": "round", "offset": 1000, "sill": 800, "width": 800, "height": 1800}]})["ok"]
    # 축 길이를 바꾸면 윤곽이 맞지 않아 거절(평행 이동은 된다)
    r = base.run("wall.set", {"id": F.W3, "axis": [6000, 4000, 1000, 4000]})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    assert base.run("wall.move", {"id": F.W3, "dy": 100})["ok"]
    assert params(base, F.W3)["top_profile"] == [[float(a), float(b)] for a, b in step]
    # 빈 목록으로 뗀다
    assert base.run("wall.set", {"id": F.W3, "top_profile": []})["ok"]
    assert "top_profile" not in params(base, F.W3)


def test_만들때_윤곽과_분할(base):
    tp = [[0, 2800], [2500, 3800], [5000, 2800]]
    r = base.run("wall.create", {"id": W5, "level": F.LV1, "axis": [0, -2000, 5000, -2000], "thickness": 200, "height": 2800, "top_profile": tp})
    assert r["ok"], r
    r = base.run("wall.split", {"id": W5, "at": 1000})
    assert r["ok"], r
    assert params(base, W5)["top_profile"] == [[0.0, 2800.0], [1000.0, 3200.0]]
    assert params(base, r["result"]["new"])["top_profile"] == [[0.0, 3200.0], [1500.0, 3800.0], [4000.0, 2800.0]]


def test_경사_판_일부만_덮으면_밖은_height_이고_경계는_수직_단이다(base):
    # 판: x 2000~8000 위(벽 W1 의 2000~6000 구간만 덮는다), 윗면 x 방향으로 올라가는 경사(물은 -x 로 흐른다)
    plate = {"id": PLATE, "level": F.LV1, "category": "roof", "form": "plate", "polygon": [[2000, -1000], [8000, -1000], [8000, 1000], [2000, 1000]],
             "thickness": 200, "base": [2000, 0, 3500], "pitch": 10, "dir": 180}
    r = base.run("solid.create", plate)
    assert r["ok"], r
    r = base.run("wall.attach_top", {"id": F.W1, "targets": [PLATE]})
    assert r["ok"], r
    assert r["result"]["covered"] == [[2000.0, 6000.0]]
    tp = params(base, F.W1)["top_profile"]
    assert tp[0] == [0.0, 3000.0] and tp[1] == [2000.0, 3000.0] and tp[2] == [2000.0, 3300.0]   # 판 밖은 height(3000), 경계에서 수직 단
    assert tp[-1][0] == 6000.0 and tp[-1][1] == pytest.approx(3300.0 + 4000.0 * 0.17632698, abs=0.01)
    # 축선이 판 아래를 지나지 않으면 거절, 붙일 수 없는 종류도 거절
    r = base.run("wall.attach_top", {"id": F.W3, "targets": [PLATE]})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    r = base.run("wall.attach_top", {"id": F.W2, "targets": [F.W1]})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"


def test_슬래브_아랫면에_붙인다(base):
    r = base.run("slab.create", {"id": F.S1, "level": F.LV2, "boundary": [[-1000, -1000], [7000, -1000], [7000, 5000], [-1000, 5000]], "thickness": 250})
    assert r["ok"], r
    r = base.run("wall.attach_top", {"id": F.W3, "targets": [F.S1]})
    assert r["ok"], r
    assert params(base, F.W3)["top_profile"] == [[0.0, 3050.0], [6000.0, 3050.0]]   # 2F 3300 - 두께 250


def test_순수_함수(base):
    tp = [[0, 3000], [2000, 3000], [2000, 4000], [4000, 3000]]
    assert WT.eval_at(tp, 2000, True) == 4000 and WT.eval_at(tp, 2000, False) == 3000
    assert WT.clip(tp, -200, 4300) == [(-200.0, 3000.0), (0.0, 3000.0), (2000.0, 3000.0), (2000.0, 4000.0), (4000.0, 3000.0), (4300.0, 3000.0)]
    assert WT.ivs_between(WT.clip(tp, 0, 4000), 3500, 1e9) == [(2000.0, 3000.0)]
    assert WT.area(tp, 0, 4000) == 2000 * 3000 + 2000 * 3500


def test_IFC_몸체는_수직_윤곽_돌출이고_왕복하면_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("roof.create", ROOF)["ok"]
        assert b.run("wall.attach_top", {"id": F.W2, "targets": [F.RF1]})["ok"]
        r = b.run("wall.layers", {"id": F.W2, "layers": [{"material": "석고보드", "thickness": 20}, {"material": "콘크리트", "thickness": 180}]})
        assert r["ok"], r
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        w = f.by_guid(F.W2)
        rep = w.Representation.Representations[0]
        assert rep.RepresentationType == "SweptSolid"
        sol = rep.Items[0]
        assert sol.is_a("IfcExtrudedAreaSolid") and sol.Depth == 200.0
        pts = [tuple(p.Coordinates) for p in sol.SweptArea.OuterCurve.Points]
        zs = sorted({round(z, 3) for _, z in pts})
        assert zs == [0.0, 3100.0, 4254.701]
        assert any(r_.is_a("IfcMaterialLayerSetUsage") for r_ in [x.RelatingMaterial for x in w.HasAssociations if x.is_a("IfcRelAssociatesMaterial")])
        w1 = f.by_guid(F.W1)                                           # 윤곽 없는 벽은 바닥 윤곽 압출 그대로
        assert tuple(w1.Representation.Representations[0].Items[0].ExtrudedDirection.DirectionRatios) == (0.0, 0.0, 1.0)
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim")["foreign"] == 0 and b.origins("imp.dobim")["detached"] == 0
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("element.get", {"id": F.W2}, project="imp.dobim")["result"]["params"]["top_profile"] == GABLE
    finally:
        b.close()


def test_평면은_윤곽보다_높게_자르면_그_구간을_투영으로_그린다(base):
    assert base.run("roof.create", ROOF)["ok"]
    assert base.run("wall.attach_top", {"id": F.W2, "targets": [F.RF1]})["ok"]
    v = view(base, name="P", type="plan", level=F.LV1, cut_height=3700, view_depth=3700)
    segs = lines_of(base, v)
    cuts = [s for s in segs if s[0] == "cut" and abs(s[1] - s[3]) < 1e-6 and 5850 < s[1] < 6150]   # W2 면의 세로 절단선
    ys = sorted({round(y, 1) for s in cuts for y in (s[2], s[4])})
    assert cuts and min(ys) > 900 and max(ys) < 3100                   # 윤곽 > 3700 인 가운데 구간만 절단(약 1040 ~ 2960)
    proj = [s for s in segs if s[0] == "proj" and abs(s[1] - s[3]) < 1e-6 and 5850 < s[1] < 6150]
    assert proj and min(min(s[2], s[4]) for s in proj) < 100           # 낮은 양 끝은 투영선
    fills = fills_of(base, v)
    assert any(all(5850 < p[0] < 6150 for p in q) for q in fills)


def test_단면과_입면은_기울어진_윗변을_그린다(base):
    assert base.run("roof.create", ROOF)["ok"]
    assert base.run("wall.attach_top", {"id": F.W2, "targets": [F.RF1]})["ok"]
    sec = view(base, name="X", type="section", p1=[6050, -1000], p2=[6050, 5000], flip=True)     # 벽 두께 안을 지나는 벽 방향 단면
    segs = lines_of(base, sec)
    slanted_cut = [s for s in segs if s[0] == "cut" and abs(s[1] - s[3]) > 100 and abs(s[2] - s[4]) > 100]
    assert len(slanted_cut) >= 2                                        # 박공 두 경사 윗변
    assert any(abs(z - 4254.701) < 0.01 for s in slanted_cut for z in (s[2], s[4]))   # 벽 윗변 꼭대기(지붕 절단선은 더 높다)
    fills = fills_of(base, sec)
    assert any(len(q) >= 3 and max(p[1] for p in q) > 4000 for q in fills)
    assert base.run("roof.delete", {"id": F.RF1})["ok"]                 # 붙인 윤곽은 그 순간의 값이다: 지붕을 지워도 벽은 그대로(입면에 지붕선이 겹치지 않게)
    assert params(base, F.W2)["top_profile"] == GABLE
    ele = view(base, name="E", type="elevation", dir="E")
    segs = lines_of(base, ele)
    slanted = [s for s in segs if s[0] == "proj" and abs(s[1] - s[3]) > 100 and abs(s[2] - s[4]) > 100]
    assert slanted and max(max(s[2], s[4]) for s in slanted) == pytest.approx(4254.701, abs=0.01)
    across = view(base, name="Y", type="section", p1=[5000, 1000], p2=[7000, 1000])     # 벽을 가로지르는 단면: 절단 높이 = 그 자리 윤곽
    rects = fills_of(base, across)
    z_at = 3100.0 + (4254.701 - 3100.0) * 1000.0 / 2000.0
    assert any(max(p[1] for p in q) == pytest.approx(z_at, abs=0.01) for q in rects)


def test_증분_도면이_전체_계산과_같다(base):
    assert base.run("roof.create", ROOF)["ok"]
    vs = [view(base, name="P", type="plan", level=F.LV1, cut_height=3700, view_depth=3700),
          view(base, name="E", type="elevation", dir="E"),
          view(base, name="X", type="section", p1=[6050, -1000], p2=[6050, 5000], flip=True)]
    for v in vs:
        lines_of(base, v)
    assert base.run("wall.attach_top", {"id": F.W2, "targets": [F.RF1]})["ok"]
    assert base.run("wall.set", {"id": F.W2, "openings": [{"shape": "pointed", "offset": 2000, "sill": 1000, "width": 800, "height": 2400, "fill": "glazing"}]})["ok"]
    for v in vs:
        inc = lines_of(base, v)
        full = s3lib.RefGraph(s3lib._Inputs(base.eng)).q(D.view_lines, v)
        assert inc == full
