# SPDX-License-Identifier: MPL-2.0
"""열다섯 번째 작업: 난간 연동(railing.from_stair·from_slab 의 출처 추적), space.from_walls 섬 구멍(닫힌 내부 벽 고리·기둥), 슬래브 구멍을 통한 단면·입면 가림 판정.
연동은 14단계 승강기 구멍 소유 추적과 같은 원칙이다: 사용자가 고친 난간은 건드리지 않고 lost, 원본 삭제는 독립 난간으로 남기고, 한 커맨드 효과라 실행 취소가 함께 되돌린다."""
import random
import sys

import pytest

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import regions as RG

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

BOUND = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]
HOLE = [[2000, 1000], [3000, 1000], [3000, 3000], [2000, 3000]]


def get(sess, eid, **kw):
    r = sess.run("element.get", {"id": eid}, **kw)
    assert r["ok"], r
    return r["result"]


def mk_stair(sess, **kw):
    a = {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18, **kw}
    assert sess.run("stair.create", a)["ok"]
    return F.ST1


# ---------------------------------------------------------------- 난간 연동: 계단
def test_계단에서_만든_난간은_출처를_기록한다(base):
    mk_stair(base)
    ids = base.run("railing.from_stair", {"stair": F.ST1, "inset": 80})["result"]["created"]
    ps = [get(base, i)["params"] for i in ids]
    assert {p["src"] for p in ps} == {F.ST1} and sorted(p["src_tag"] for p in ps) == ["f0L", "f0R"] and all(p["src_inset"] == 80.0 for p in ps)
    plain = base.run("railing.create", {"id": F.RL1, "level": F.LV1, "path": [[0, 0], [3000, 0]]})
    assert plain["ok"] and "src" not in get(base, F.RL1)["params"]          # 손으로 만든 난간에는 출처가 없다


def test_계단을_옮기거나_바꾸면_난간이_따라가고_실행_취소가_함께_되돌린다(base):
    mk_stair(base)
    ids = base.run("railing.from_stair", {"stair": F.ST1})["result"]["created"]
    h0 = base.eng.state_hash()
    r = base.run("stair.move", {"id": F.ST1, "dx": 500, "dy": 300})
    assert r["ok"] and sorted(r["result"]["railings"]["moved"]) == sorted(ids) and r["result"]["railings"]["lost"] == []
    got = sorted(get(base, i)["params"]["path"][0] for i in ids)
    assert got == [[1500.0, 750.0], [1500.0, 1850.0]]                        # 옆면 y = 1300 ± 550
    # 새 계단에서 처음부터 만든 난간과 같은 위치(같은 순수 함수)
    base.run("history.undo", {})
    assert base.eng.state_hash() == h0                                       # 계단과 난간이 함께 되돌아간다
    r2 = base.run("stair.set", {"id": F.ST1, "risers": 16, "height": 2800})
    assert r2["ok"] and len(r2["result"]["railings"]["moved"]) == 2
    p = get(base, ids[0])["params"]
    assert p["rise"] == pytest.approx(15 * 2800 / 16) and p["base_offset"] == pytest.approx(2800 / 16)
    n = base.run("stair.set", {"id": F.ST1, "name": "새 이름"})               # 난간 자리가 안 바뀌는 변경은 난간을 건드리지 않는다
    assert n["ok"] and "railings" not in n["result"]


def test_사용자가_고친_난간은_따라가지_않고_lost로_알린다(base):
    mk_stair(base)
    a, b = base.run("railing.from_stair", {"stair": F.ST1})["result"]["created"]
    assert base.run("railing.move", {"id": a, "dx": 40, "dy": 0})["ok"]      # 손으로 고쳤다
    before = get(base, a)["params"]
    r = base.run("stair.move", {"id": F.ST1, "dx": 500, "dy": 0})
    assert r["ok"] and r["result"]["railings"]["moved"] == [b]
    assert [x["id"] for x in r["result"]["railings"]["lost"]] == [a]
    assert get(base, a)["params"] == before                                  # 건드리지 않았다
    # 연동을 끊으면(clear_source) 더는 lost 로도 알리지 않는다
    assert base.run("railing.set", {"id": a, "clear_source": True})["ok"]
    assert "src" not in get(base, a)["params"]
    r = base.run("stair.move", {"id": F.ST1, "dx": 100, "dy": 0})
    assert "lost" not in r["result"]["railings"] or r["result"]["railings"]["lost"] == []


def test_높이는_사용자_것이라_계단을_옮겨도_그대로다(base):
    mk_stair(base)
    ids = base.run("railing.from_stair", {"stair": F.ST1})["result"]["created"]
    assert base.run("railing.set", {"id": ids[0], "height": 900})["ok"]
    r = base.run("stair.move", {"id": F.ST1, "dx": 300, "dy": 0})
    assert sorted(r["result"]["railings"]["moved"]) == sorted(ids) and get(base, ids[0])["params"]["height"] == 900.0


def test_새_계단에_그_난간_자리가_없으면_lost(base):
    mk_stair(base, run=[1000, 1000, 4000, 1000], turn={"type": "L", "side": "left", "risers_a": 9})
    ids = base.run("railing.from_stair", {"stair": F.ST1})["result"]["created"]
    assert len(ids) == 5
    r = base.run("stair.set", {"id": F.ST1, "clear_turn": True})
    assert r["ok"]
    lost = {x["id"] for x in r["result"]["railings"]["lost"]}
    assert len(lost) >= 3 and lost <= set(ids)                               # 층참·둘째 단 묶음 난간은 갈 곳이 없다


def test_계단을_지우면_난간은_독립_난간으로_남는다(base):
    mk_stair(base)
    ids = base.run("railing.from_stair", {"stair": F.ST1})["result"]["created"]
    h0 = base.eng.state_hash()
    r = base.run("stair.delete", {"id": F.ST1})
    assert r["ok"] and sorted(r["result"]["railings_released"]) == sorted(ids)
    assert all("src" not in get(base, i)["params"] for i in ids) and base.run("model.validate", {})["ok"]
    base.run("history.undo", {})
    assert base.eng.state_hash() == h0
    r = base.run("element.delete", {"id": F.ST1})                            # 일반 삭제 경로도 같다
    assert r["ok"] and sorted(r["result"]["railings_released"]) == sorted(ids)


# ---------------------------------------------------------------- 난간 연동: 슬래브
def mk_slab(sess, **kw):
    assert sess.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": BOUND, "thickness": 200, **kw})["ok"]


def test_슬래브_가장자리_난간은_슬래브를_옮기거나_경계를_바꾸면_따라간다(base):
    mk_slab(base, openings=[HOLE])
    outer = base.run("railing.from_slab", {"slab": F.S1})["result"]["created"][0]
    part = base.run("railing.from_slab", {"slab": F.S1, "edges": [0, 1]})["result"]["created"][0]
    hole = base.run("railing.from_slab", {"slab": F.S1, "opening": 0})["result"]["created"][0]
    assert [get(base, i)["params"]["src_tag"] for i in (outer, part, hole)] == ["outer:all", "outer:0+2", "o0:all"]
    r = base.run("slab.move", {"id": F.S1, "dx": 1000, "dy": 500})
    assert r["ok"] and sorted(r["result"]["railings"]["moved"]) == sorted([outer, part, hole]) and r["result"]["railings"]["lost"] == []
    assert get(base, part)["params"]["path"] == [[1000.0, 500.0], [7000.0, 500.0], [7000.0, 4500.0]]
    r = base.run("slab.set", {"id": F.S1, "boundary": [[1000, 500], [7000, 500], [7000, 4500], [1000, 4500], [1000, 2000]], "top_offset": 20})
    assert r["ok"]
    got = r["result"]["railings"]
    assert part in got["moved"]                                              # 같은 변 번호(0·1)의 난간선이 새 경계로 옮겨졌다
    assert get(base, part)["params"]["base_offset"] == 20.0 and get(base, part)["params"]["path"][-1] == [7000.0, 4500.0]
    # 구멍을 지우면 구멍 둘레 난간은 갈 곳이 없다
    r = base.run("slab.set", {"id": F.S1, "clear_openings": True})
    assert hole in {x["id"] for x in r["result"]["railings"]["lost"]}


def test_슬래브를_지우면_난간이_남고_구멍_난간은_승강기_구멍_이동을_따라간다(base):
    mk_slab(base, top_offset=3000)    # 샤프트(카 바닥 0 ~ 3000)가 지나는 높이의 슬래브
    assert base.run("elevator.create", {"id": F.EL1, "level": F.LV1, "shaft": [4000, 500, 5800, 2300], "rise": 3000})["ok"]
    assert base.run("elevator.cut_slabs", {"id": F.EL1})["ok"]
    rid = base.run("railing.from_slab", {"slab": F.S1, "opening": 0})["result"]["created"][0]
    r = base.run("elevator.move", {"id": F.EL1, "dx": -600, "dy": 200})
    assert r["ok"] and r["result"]["holes"]["railings"][0]["moved"] == [rid]
    assert get(base, rid)["params"]["path"][0] == [3400.0, 700.0]
    d = base.run("slab.delete", {"id": F.S1})
    assert d["ok"] and d["result"]["railings_released"] == [rid]
    assert "src" not in get(base, rid)["params"]


def test_난간_출처_표식은_검사된다(base):
    mk_stair(base)
    ok = {"name": "r", "path": [[0, 0], [1000, 0]], "height": 1100.0, "base_offset": 0.0, "rise": 0.0}
    from dobim_core import kinds as K
    assert K.row_params_error("railing", {**ok, "src": F.ST1, "src_tag": "f0L"}) is None
    assert K.row_params_error("railing", {**ok, "src": F.ST1}) is not None                  # src_tag 와 함께여야 한다
    assert K.row_params_error("railing", {**ok, "src_inset": 5.0}) is not None              # src 없이 src_inset 만은 안 된다
    from dobim_core.kinds import remap_params
    assert remap_params("railing", {**ok, "src": "a" * 22, "src_tag": "x"}, {"a" * 22: "b" * 22})["src"] == "b" * 22


def test_난간_연동_증분이_전체_재생성과_같다(base):
    def mk(**kw):
        r = base.run("view.create", kw)
        assert r["ok"], r
        return r["result"]["id"]
    views = {"p": mk(name="P", type="plan", level=F.LV1, cut_height=1200), "e": mk(name="E", type="elevation", dir="S"),
             "s": mk(name="S", type="section", p1=[3000, -1000], p2=[3000, 3000])}

    def lines(v):
        base.eng.sync()
        base.eng._touch_pending()
        return base.eng.g.q(D.view_lines, v)

    def check():
        for k, v in views.items():
            assert lines(v) == s3lib.full_lines(base.eng, v), k

    mk_stair(base)
    mk_slab(base)
    base.run("railing.from_stair", {"stair": F.ST1})
    base.run("railing.from_slab", {"slab": F.S1, "edges": [0, 1]})
    check()
    assert base.run("stair.move", {"id": F.ST1, "dx": 400, "dy": 200})["ok"]
    check()
    assert base.run("slab.move", {"id": F.S1, "dx": -300, "dy": 100})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()


def test_연동_난간_도면과_해시는_결정적이다(tmp_path):
    import kit

    def build(name):
        s = kit.Session(tmp_path / name, guid=F.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        mk_stair(s)
        s.run("railing.from_stair", {"stair": F.ST1})
        s.run("stair.move", {"id": F.ST1, "dx": 700, "dy": 0})
        out = s.eng.hashes()
        s.close()
        return out
    assert build("a.dobim") == build("b.dobim")


def test_연동_난간을_내보내고_가져오면_출처가_되살고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18})["ok"]
        assert b.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": BOUND, "thickness": 200})["ok"]
        ids = b.run("railing.from_stair", {"stair": F.ST1})["result"]["created"] + b.run("railing.from_slab", {"slab": F.S1, "edges": [0]})["result"]["created"]
        h = b.hashes()
        b.export("r.ifc")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        for i in ids:
            assert b.run("element.get", {"id": i}, project="imp.dobim")["result"]["params"] == b.run("element.get", {"id": i})["result"]["params"]
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("stair.move", {"id": F.ST1, "dx": 200, "dy": 0}, project="imp.dobim")["result"]["railings"]["moved"]   # 가져온 뒤에도 따라간다
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


# ---------------------------------------------------------------- space.from_walls 섬 구멍
W = [("1" + "0" * 20 + str(i)) for i in range(4, 10)]


def box(prefix, x0, y0, x1, y1, th=200):
    p = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [(f"{prefix}{i}", p[i], p[(i + 1) % 4], th) for i in range(4)]


def island_walls(sess, x0=2000, y0=1000, x1=3000, y1=2000, th=100, first=5):
    """기준 프로젝트의 열린 변을 닫고(x=0) 방 안에 따로 떨어진 닫힌 벽 고리(섬)를 만든다."""
    assert sess.run("wall.create", {"id": "1" + "0" * 20 + "4", "level": F.LV1, "axis": [0, 4000, 0, 0], "thickness": 200, "height": 3000})["ok"]
    p = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for i in range(4):
        a, b = p[i], p[(i + 1) % 4]
        assert sess.run("wall.create", {"id": "1" + "0" * 20 + str(first + i), "level": F.LV1, "axis": [a[0], a[1], b[0], b[1]], "thickness": th, "height": 3000})["ok"]


def test_regions_섬은_방의_구멍으로_나온다():
    walls = box("a", 0, 0, 8000, 6000) + box("b", 3000, 2000, 5000, 4000)
    fs = RG.find_faces(walls, islands=True)
    assert len(fs) == 2 and len(fs[0]["holes"]) == 1 and fs[1]["holes"] == []
    assert RG.inner_polygon(fs[0]["holes"][0], outer=True) == [(2900.0, 1900.0), (5100.0, 1900.0), (5100.0, 4100.0), (2900.0, 4100.0)]
    assert all("holes" not in f and "comp" not in f for f in RG.find_faces(walls))      # 옵션을 안 켜면 이전과 같은 결과


def test_regions_섬_안의_섬은_가장_안쪽_방의_구멍이다():
    walls = box("a", 0, 0, 8000, 6000) + box("b", 3000, 2000, 5000, 4000) + box("c", 3500, 2500, 4500, 3500)
    fs = RG.find_faces(walls, islands=True)
    holes = {f["center"][0]: len(f["holes"]) for f in fs}
    assert holes == {(0.0, 0.0): 1, (3000.0, 2000.0): 1, (3500.0, 2500.0): 0}


def test_regions_섬_결과는_벽_순서와_무관하다():
    walls = box("a", 0, 0, 8000, 6000) + box("b", 3000, 2000, 5000, 4000) + box("c", 6000, 1000, 7000, 2000)
    ref = RG.find_faces(walls, islands=True)
    for seed in range(6):
        w = list(walls)
        random.Random(seed).shuffle(w)
        got = RG.find_faces(w, islands=True)
        assert [f["center"] for f in got] == [f["center"] for f in ref]
        assert [[h["center"] for h in f["holes"]] for f in got] == [[h["center"] for h in f["holes"]] for f in ref]


def test_섬이_있는_방은_구멍을_빼고_섬_안쪽은_따로_공간이_된다(base):
    island_walls(base)
    r = base.run("space.from_walls", {"level": F.LV1, "all": True, "min_area_m2": 0.1})
    assert r["ok"] and len(r["result"]["created"]) == 2
    outer, inner = (get(base, i) for i in r["result"]["created"])
    assert outer["params"]["holes"] == [[[1950.0, 950.0], [3050.0, 950.0], [3050.0, 2050.0], [1950.0, 2050.0]]]   # 섬 벽의 바깥 면
    assert outer["derived"]["hole_area_m2"] == pytest.approx(1.21) and outer["derived"]["area_m2"] == pytest.approx(5800 * 3800 / 1e6 - 1.21)
    assert "holes" not in inner["params"] and inner["derived"]["area_m2"] == pytest.approx(0.81)
    again = base.run("space.from_walls", {"level": F.LV1, "all": True, "min_area_m2": 0.1})                    # 다시 실행해도 중복이 없다
    assert again["result"]["created"] == [] and len(again["result"]["skipped"]) == 2


def test_islands_거짓이면_이전처럼_섬을_무시한다(base):
    island_walls(base)
    r = base.run("space.from_walls", {"level": F.LV1, "points": [[500, 500]], "islands": False})
    assert "holes" not in get(base, r["result"]["created"][0])["params"]
    r = base.run("space.from_walls", {"level": F.LV1, "points": [[5000, 3000]], "edge": "center"})
    sp = get(base, r["result"]["created"][0])
    assert sp["params"]["holes"] == [[[2000.0, 1000.0], [3000.0, 1000.0], [3000.0, 2000.0], [2000.0, 2000.0]]]   # center: 벽 중심선 고리


def test_기둥도_구멍으로_뺀다(base):
    island_walls(base, first=5)
    assert base.run("family.place", {"family": "column.section", "type_values": {"section": "round"}, "level": F.LV1, "position": [4500, 3000]})["ok"]
    sp = get(base, base.run("space.from_walls", {"level": F.LV1, "points": [[500, 500]]})["result"]["created"][0])
    assert len(sp["params"]["holes"]) == 2                                   # 섬 + 기둥
    col = sp["params"]["holes"][1]
    xs, ys = [p[0] for p in col], [p[1] for p in col]
    assert min(xs) < 4500 < max(xs) and min(ys) < 3000 < max(ys)


def test_섬_구멍_있는_공간의_검사와_이동과_면적_문자(base):
    assert base.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": BOUND, "name": "홀", "holes": [HOLE]})["ok"]
    assert get(base, F.SP1)["derived"]["area_m2"] == pytest.approx(24 - 2)
    assert not base.run("space.set", {"id": F.SP1, "holes": [[[-100, 0], [500, 0], [500, 500], [-100, 500]]]})["ok"]        # 경계 밖
    assert not base.run("space.set", {"id": F.SP1, "holes": [HOLE, [[2500, 1500], [3500, 1500], [3500, 2500], [2500, 2500]]]})["ok"]   # 서로 겹친다
    assert base.run("space.move", {"id": F.SP1, "dx": 100, "dy": 0})["ok"] and get(base, F.SP1)["params"]["holes"][0][0] == [2100.0, 1000.0]
    v = base.run("view.create", {"name": "P", "type": "plan", "level": F.LV1})["result"]["id"]
    base.eng.sync()
    base.eng._touch_pending()
    labels = base.eng.g.q(D.view_labels, v)
    assert any(t[2] == "22.00㎡" for t in labels)                            # 면적 문자는 구멍을 뺀 값
    nm = next(t for t in labels if t[2] == "홀")
    assert not (2100 <= nm[0] <= 3100 and 1000 <= nm[1] <= 3000)             # 이름 문자는 구멍 밖에 놓인다
    assert base.run("space.set", {"id": F.SP1, "clear_holes": True})["ok"] and "holes" not in get(base, F.SP1)["params"]


def test_섬_구멍_공간을_내보내고_가져오면_되살고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": BOUND, "name": "홀", "holes": [HOLE]})["ok"]
        h = b.hashes()
        b.export("r.ifc")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim")["foreign"] == 0 and b.origins("imp.dobim")["detached"] == 0
        assert b.run("element.get", {"id": F.SP1}, project="imp.dobim")["result"]["params"]["holes"] == [HOLE_F]
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


HOLE_F = [[float(x), float(y)] for x, y in HOLE]


def test_섬이_없으면_공간_행과_해시가_그대로다(base):
    h0 = base.eng.state_hash()
    assert base.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": BOUND, "name": "홀", "holes": [HOLE]})["ok"]
    assert base.run("space.set", {"id": F.SP1, "clear_holes": True})["ok"]
    base.run("space.delete", {"id": F.SP1})
    assert base.eng.state_hash() == h0


# ---------------------------------------------------------------- 단면·입면 가림 판정에 슬래브 구멍
def test_사다리꼴_분해는_구멍을_뺀_면적과_같다():
    poly = ((0.0, 0.0), (6000.0, 0.0), (6000.0, 4000.0), (0.0, 4000.0))
    holes = (((2000.0, 1000.0), (3000.0, 1000.0), (3000.0, 3000.0), (2000.0, 3000.0)), ((4000.0, 500.0), (5000.0, 1500.0), (4000.0, 2000.0)))
    pieces = D._trapezoids(poly, holes)
    area = sum(abs(D._area2(p)) for p in pieces) / 2.0
    # 이웃 띠가 SEAM 만큼 겹치므로 면적은 구멍을 뺀 값보다 겹침 폭 x 높이 만큼만 크다
    expect = 6000 * 4000 - 2000 * 1000 - 0.5 * abs(D._area2(holes[1]))
    assert expect <= area <= expect + 4000 * D.SEAM * 8
    assert D._trapezoids(poly, holes) == pieces                                # 결정적
    cx, cy = 2500.0, 2000.0                                                    # 구멍 한가운데는 어느 조각에도 없다
    assert not any(D._in_tri((cx, cy), *[q for q in p[:3]]) for p in pieces if len(p) == 3)
    assert all(not _inside(p, (cx, cy)) for p in pieces)


def test_사다리꼴_분해는_오목_경계와_비스듬한_구멍에서도_면적이_맞는다():
    poly = ((0.0, 0.0), (6000.0, 0.0), (6000.0, 2000.0), (3000.0, 2000.0), (3000.0, 5000.0), (0.0, 5000.0))   # L 자(오목)
    holes = (((500.0, 500.0), (1500.0, 700.0), (1300.0, 1700.0), (400.0, 1500.0)), ((4000.0, 500.0), (5000.0, 500.0), (5000.0, 1500.0), (4000.0, 1500.0)))
    area = lambda p: abs(D._area2(p)) / 2.0   # noqa: E731
    pieces = D._trapezoids(poly, holes)
    expect = area(poly) - sum(area(h) for h in holes)
    assert expect <= sum(area(p) for p in pieces) <= expect + 5000 * D.SEAM * len(pieces)
    assert all(len(p) in (3, 4) and D._area2(p) > 0 for p in pieces)           # 볼록 조각(반시계)
    assert not any(_inside(p, c) for p in pieces for c in ((900.0, 1100.0), (4500.0, 1000.0)))   # 구멍 한가운데는 비어 있다


def _inside(poly, pt):
    n = len(poly)
    return all((poly[(i + 1) % n][0] - poly[i][0]) * (pt[1] - poly[i][1]) - (poly[(i + 1) % n][1] - poly[i][1]) * (pt[0] - poly[i][0]) > 0 for i in range(n))


def _vertical_at(lines, s, tol=1e-3):
    return sorted((round(min(ln[2], ln[4]), 1), round(max(ln[2], ln[4]), 1)) for ln in lines if abs(ln[1] - s) < tol and abs(ln[3] - s) < tol)


def test_단면_절단면이_구멍을_지나면_구멍_속_선이_슬래브에_가리지_않는다(base):
    base.run("wall.create", {"id": W[5], "level": F.LV1, "axis": [2200, 2400, 2800, 2400], "thickness": 100, "height": 3000})
    # 슬래브 윗면 3000, 두께 200(밑 2800): 벽의 위쪽 200mm 가 슬래브 두께와 같은 높이다. 절단선(y=2000)은 구멍(y 1000~3000) 안이다
    assert base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": BOUND, "thickness": 200, "top_offset": 3000, "openings": [HOLE]})["ok"]
    v = base.run("view.create", {"name": "S", "type": "section", "p1": [-1000, 2000], "p2": [7000, 2000]})["result"]["id"]
    base.eng.sync()
    base.eng._touch_pending()
    ls = base.eng.g.q(D.view_lines, v)
    assert (0.0, 3000.0) in _vertical_at(ls, 3200.0)                           # 구멍을 통해 벽 모서리가 윗끝까지 보인다
    # 같은 모델에서 구멍만 없애면 슬래브 두께 높이(2800~3000)는 가려진다(이전 동작과 같다)
    assert base.run("slab.set", {"id": F.S1, "clear_openings": True})["ok"]
    base.eng.sync()
    base.eng._touch_pending()
    assert _vertical_at(base.eng.g.q(D.view_lines, v), 3200.0) == [(0.0, 2800.0)]


def test_슬래브_구멍_가림_증분이_전체_재생성과_같다(base):
    base.run("wall.create", {"id": W[5], "level": F.LV1, "axis": [2200, 2400, 2800, 2400], "thickness": 100, "height": 3000})
    views = {}
    for k, kw in {"s": {"type": "section", "p1": [-1000, 2000], "p2": [7000, 2000]}, "e": {"type": "elevation", "dir": "S"}, "e2": {"type": "elevation", "dir": "E"}}.items():
        views[k] = base.run("view.create", {"name": k, **kw})["result"]["id"]

    def check():
        for k, v in views.items():
            base.eng.sync()
            base.eng._touch_pending()
            assert base.eng.g.q(D.view_lines, v) == s3lib.full_lines(base.eng, v), k

    assert base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": BOUND, "thickness": 200, "top_offset": 3000, "openings": [HOLE]})["ok"]
    check()
    assert base.run("slab.set", {"id": F.S1, "openings": [HOLE, [[4000, 500], [5000, 500], [5000, 1500], [4000, 1500]]]})["ok"]
    check()
    assert base.run("slab.move", {"id": F.S1, "dx": 300, "dy": -200})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()


def test_구멍_없는_슬래브의_가림_조각은_이전과_같다(base):
    """구멍이 없으면 이전 삼각분할을 그대로 쓴다(도면 바이트 불변)."""
    assert base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": BOUND, "thickness": 200})["ok"]
    base.eng.sync()
    base.eng._touch_pending()
    tris = base.eng.g.q(D.slab_tris, F.S1)
    assert len(tris) == 2 and all(len(t) == 3 for t in tris)
