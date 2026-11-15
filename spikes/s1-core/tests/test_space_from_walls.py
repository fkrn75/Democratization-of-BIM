# SPDX-License-Identifier: MPL-2.0
"""space.from_walls: 레벨의 벽으로 닫힌 영역을 찾아 공간을 만든다(점 지정·전체, 안쪽 면·중심선), 결정성, 건너뛰기, 거절."""
import random

import kit
import pytest

from dobim_core import fixture as F
from dobim_core import regions as RG

W4 = "1" + "0" * 20 + "4"
W5 = "1" + "0" * 20 + "5"
W6 = "1" + "0" * 20 + "6"


def close_room(sess):
    """기준 프로젝트의 열린 변(x=0)을 닫아 6000 x 4000 방 하나(벽 두께 200)를 만든다."""
    assert sess.run("wall.create", {"id": W4, "level": F.LV1, "axis": [0, 4000, 0, 0], "thickness": 200, "height": 3000})["ok"]


def partition(sess):
    """x=3000 칸막이 벽(T 접합 둘)으로 방을 둘로 나눈다."""
    assert sess.run("wall.create", {"id": W5, "level": F.LV1, "axis": [3000, 0, 3000, 4000], "thickness": 100, "height": 3000})["ok"]


def spaces(sess):
    return sess.run("element.query", {"kind": "space", "limit": 100})["result"]


def space_polys(sess, ids):
    return [sess.run("element.get", {"id": i})["result"]["params"]["boundary"] for i in ids]


# ---------------------------------------------------------------- 순수 기하
def test_벽_중심선_사각형은_방_하나이고_가지는_걷어낸다():
    walls = [("a", (0, 0), (4000, 0), 200), ("b", (4000, 0), (4000, 3000), 200), ("c", (4000, 3000), (0, 3000), 200), ("d", (0, 3000), (0, 0), 200),
             ("tail", (4000, 1500), (6000, 1500), 200)]       # 바깥으로 삐져나간 막다른 벽
    faces = RG.find_faces(walls)
    assert len(faces) == 1 and faces[0]["center"][0] == (0.0, 0.0)
    assert RG._area2(faces[0]["center"]) / 2 == 4000 * 3000
    inner = RG.inner_polygon(faces[0])
    assert inner == [(100.0, 100.0), (3900.0, 100.0), (3900.0, 2900.0), (100.0, 2900.0)]


def test_열린_벽은_방이_아니다():
    assert RG.find_faces([("a", (0, 0), (4000, 0), 200), ("b", (4000, 0), (4000, 3000), 200), ("c", (4000, 3000), (0, 3000), 200)]) == []


def test_입력_순서가_달라도_같은_방이다():
    walls = [("a", (0, 0), (6000, 0), 200), ("b", (6000, 0), (6000, 4000), 200), ("c", (6000, 4000), (0, 4000), 200), ("d", (0, 4000), (0, 0), 200),
             ("m", (3000, 0), (3000, 4000), 100), ("n", (0, 2000), (3000, 2000), 100)]
    ref = RG.find_faces(walls)
    assert len(ref) == 3
    for seed in range(5):
        w = walls[:]
        random.Random(seed).shuffle(w)
        assert RG.find_faces(w) == ref


# ---------------------------------------------------------------- 커맨드
def test_점_지정은_점을_둘러싼_영역에_안쪽_면_공간을_만든다(base):
    close_room(base)
    r = base.run("space.from_walls", {"level": F.LV1, "points": [[3000, 2000]], "category": "사무실"})
    assert r["ok"], r
    ids = r["result"]["created"]
    assert len(ids) == 1 and r["result"]["regions"] == 1
    g = base.run("element.get", {"id": ids[0]})["result"]
    assert g["kind"] == "space" and g["origin"] == "authored" and g["relations"]["on_level"] == [F.LV1] and g["params"]["category"] == "사무실"
    assert sorted(g["params"]["boundary"]) == [[100.0, 100.0], [100.0, 3900.0], [5900.0, 100.0], [5900.0, 3900.0]]
    assert g["derived"]["area_m2"] == pytest.approx(5.8 * 3.8)
    c = base.run("space.from_walls", {"level": F.LV1, "points": [[3000, 2000]], "edge": "center"})   # 같은 영역을 중심선으로: 경계가 다르므로 새 공간
    assert c["ok"] and len(c["result"]["created"]) == 1
    assert base.run("element.get", {"id": c["result"]["created"][0]})["result"]["derived"]["area_m2"] == pytest.approx(24.0)


def test_점이_영역_밖이거나_벽이_열려_있으면_거절한다(base):
    r = base.run("space.from_walls", {"level": F.LV1, "points": [[3000, 2000]]})              # 기준 프로젝트의 벽은 한쪽이 열려 있다
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
    close_room(base)
    r = base.run("space.from_walls", {"level": F.LV1, "points": [[9000, 2000]]})
    assert not r["ok"]
    assert "space" not in base.run("model.stats", {})["result"]["counts"]


def test_두_점이_서로_다른_영역이면_공간_둘_같은_영역이면_하나(base):
    close_room(base)
    partition(base)
    r = base.run("space.from_walls", {"level": F.LV1, "points": [[1000, 1000], [5000, 3000], [1500, 3500]]})
    assert r["ok"]
    assert len(r["result"]["created"]) == 2                                                  # 첫째·셋째 점은 같은 방
    polys = sorted(sorted(p) for p in space_polys(base, r["result"]["created"]))
    assert polys[0] == [[100.0, 100.0], [100.0, 3900.0], [2950.0, 100.0], [2950.0, 3900.0]]  # 칸막이 두께 100 -> 중심 3000 에서 50
    assert polys[1] == [[3050.0, 100.0], [3050.0, 3900.0], [5900.0, 100.0], [5900.0, 3900.0]]


def test_전체_일괄은_모든_방을_만들고_다시_하면_건너뛴다(base):
    close_room(base)
    partition(base)
    r = base.run("space.from_walls", {"level": F.LV1, "all": True})
    assert r["ok"] and len(r["result"]["created"]) == 2 and r["result"]["skipped"] == []
    again = base.run("space.from_walls", {"level": F.LV1, "all": True})
    assert again["ok"] and again["result"]["created"] == [] and len(again["result"]["skipped"]) == 2
    big = base.run("space.from_walls", {"level": F.LV1, "all": True, "min_area_m2": 100})   # 너무 작은 영역은 건너뛴다
    assert big["ok"] and big["result"]["regions"] == 0


def test_같은_입력은_같은_id와_순서를_낸다(tmp_path):

    def build(name):
        s = kit.Session(tmp_path / name, guid=F.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        close_room(s)
        partition(s)
        r = s.run("space.from_walls", {"level": F.LV1, "all": True})
        out = (r["result"], s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a[0] == b[0] and a[1] == b[1]


def test_인자는_points와_all_중_정확히_하나이고_실행_취소로_되돌린다(base):
    close_room(base)
    assert not base.run("space.from_walls", {"level": F.LV1})["ok"]
    assert not base.run("space.from_walls", {"level": F.LV1, "points": [[1, 1]], "all": True})["ok"]
    h0 = base.eng.state_hash()
    assert base.run("space.from_walls", {"level": F.LV1, "all": True})["ok"]
    assert base.eng.state_hash() != h0
    assert base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0
