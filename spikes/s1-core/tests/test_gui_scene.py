# SPDX-License-Identifier: MPL-2.0
"""GUI B1: view.scene·view.pick·geom.snap(코어 조회 커맨드). 화면과 도면이 같은 원천인지(view_lines 와 덮는 구간이 같은지), 증분 = 전체,
쪽 나누기·응답 상한, 집기·스냅 후보를 본다."""
import base64
import json
import sys
from collections import defaultdict

import pytest

from conftest import ROOT
from dobim_core import commands_gui as CG
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import limits
from dobim_core import scene as SC
import parity as P

sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402

WIN = "0" * 21 + "w"
COL = "0" * 21 + "c"


def ok(r):
    assert r["ok"], r.get("error")
    return r["result"]


@pytest.fixture
def rich(base):
    """여러 종류가 섞인 모델: 기준 프로젝트 + 뷰 3개(평면·단면·입면) + 슬래브·계단·난간·승강기·솔리드·통심선·공간·벽 + 창·기둥 패밀리 + 2D 작도·치수."""
    for name, args in (P._V1, P._V2, P._W4, P._S1, P._ST1, P._RL1, P._EL1, P._SO1, P._GR1, P._SP1, P._DR1, P._DR2, P._A1):
        ok(base.run(name, args))
    ok(base.run("view.create", {"id": "4" + "0" * 20 + "3", "name": "남측 입면", "type": "elevation", "dir": "S"}))
    ok(base.run("view.create", {"id": "4" + "0" * 20 + "4", "name": "부분", "type": "plan", "level": F.LV1, "scale": 20, "crop": [0, 0, 3000, 3000], "dims": True}))
    ok(base.run("family.place", {"id": WIN, "family": "window.framed", "host": F.W2, "offset": 2000}))
    ok(base.run("family.place", {"id": COL, "family": "column.section", "type_values": {"section": "round"}, "level": F.LV1, "position": [1000, 1000]}))
    return base


VIEWS = (F.V1, F.V2, "4" + "0" * 20 + "3", "4" + "0" * 20 + "4")


def cover(lines):
    """등급·직선별로 덮는 구간의 합집합(merge_lines 와 같은 키). 화면(요소별)과 도면(병합)이 같은 선을 그리는지 비교한다."""
    acc = defaultdict(list)
    for ln in lines:
        k = SC._key(ln)
        if k is not None:
            acc[(ln[0],) + k[0]].append((k[4], k[5]))
    return {key: [(round(a, 2), round(b, 2)) for a, b in SC._union(ivs)] for key, ivs in acc.items()}


def same_cover(a, b, tol=0.05):
    """덮는 구간이 같다(끝점 차이 tol mm 이하). 잘라내기 경계에서 병합 전후 자르기 순서 때문에 EPS(0.01mm) 수준 차이가 날 수 있다."""
    assert a.keys() == b.keys()
    for k in a:
        assert len(a[k]) == len(b[k]), k
        for (a0, a1), (b0, b1) in zip(a[k], b[k]):
            assert abs(a0 - b0) <= tol and abs(a1 - b1) <= tol, (k, a[k], b[k])


def scene_lines(sc):
    return [ln for t in sc for ln in t[2]]


@pytest.mark.parametrize("vi", range(len(VIEWS)))
def test_요소별_선분이_도면_선분과_같은_곳을_덮는다(rich, vi):
    v = VIEWS[vi]
    rich.eng.sync()
    g = rich.eng.g
    sc = g.q(SC.view_scene, v)
    assert sc
    same_cover(cover(scene_lines(sc)), cover(g.q(D.view_lines, v)))
    # 채움·문자도 같은 원천(순서만 다를 수 있다)
    assert sorted(p for t in sc for p in t[3]) == sorted(g.q(D.view_fills, v))
    texts = sorted(x for t in sc for x in t[4])
    assert texts == sorted(g.q(D.view_labels, v))


def test_요소_id와_종류가_붙는다(rich):
    rich.eng.sync()
    sc = rich.eng.g.q(SC.view_scene, F.V1)
    by = {t[1]: t for t in sc}
    for e in (F.W1, F.W2, F.W3, F.D1, F.D2, WIN, COL, F.ST1, F.RL1, F.EL1, F.SO1, F.SP1, F.DR1, F.DR2, F.A1):
        assert e in by, e
    assert by[F.D1][0] == "door" and by[F.D1][2] and all(ln[0] == "symbol" for ln in by[F.D1][2])   # 문 기호(문짝·호)는 문의 것
    assert by[WIN][0] == "finst" and by[WIN][2]                                                    # 창 기호는 창의 것
    assert not any(ln in by[F.W1][2] for ln in by[F.D1][2])                                         # 벽에서는 빠진다
    assert by[F.DR1][0] == "draft" and by[F.DR1][5] and by[F.A1][0] == "dim"
    assert by[F.SP1][0] == "space" and by[F.SP1][4]
    assert [(t[0], t[1]) for t in sc] == sorted((t[0], t[1]) for t in sc)


def test_증분과_전체_계산이_같다(rich):
    rich.eng.sync()
    g = rich.eng.g
    for v in VIEWS:
        g.q(CG.scene_rows, v, 1)
    for name, args in (("wall.move", {"id": F.W1, "dx": 0, "dy": -50}), ("door.set", {"id": F.D1, "offset": 2000}),
                       ("draft.move", {"ids": [F.DR1], "dx": 100}), ("family.set", {"id": WIN, "offset": 2500}),
                       ("element.delete", {"id": F.RL1}), ("wall.create", {"level": F.LV1, "axis": [0, 6000, 6000, 6000], "thickness": 150, "height": 3000})):
        ok(rich.run(name, args))
        rich.eng.sync()
        ref = s3lib.RefGraph(s3lib._Inputs(rich.eng))
        for v in VIEWS:
            assert g.q(SC.view_scene, v) == ref.q(SC.view_scene, v), (name, v)
            assert g.q(CG.scene_rows, v, 1) == ref.q(CG.scene_rows, v, 1), (name, v)
            assert g.q(CG.scene_digest, v) == ref.q(CG.scene_digest, v)


def test_바뀐_요소만_다시_계산한다(rich):
    rich.eng.sync()
    g = rich.eng.g
    g.q(SC.view_scene, F.V1)
    keep = g.nodes[(SC._el_parts_q, F.V1, "slab", F.S1)]
    ok(rich.run("draft.move", {"ids": [F.DR1], "dx": 100}))
    rich.eng.sync()
    g.q(SC.view_scene, F.V1)
    assert g.nodes[(SC._el_parts_q, F.V1, "slab", F.S1)] is keep and keep.state == D.CLEAN


def pages(sess, args):
    rows, cur, n = [], None, 0
    while True:
        env = sess.run("view.scene", {**args, **({"cursor": cur} if cur else {})})
        assert env["ok"], env
        size = len(json.dumps(env, ensure_ascii=False).encode("utf-8"))
        if len(env["result"]["items"]) > 1:
            assert size <= limits.RESPONSE_CAP_BYTES + 2 and limits.est_tokens(env) <= limits.RESPONSE_CAP_TOKENS
        rows += env["result"]["items"]
        n += 1
        cur = env["page"]["next_cursor"]
        if cur is None:
            return rows, n, env


def test_쪽_나누기로_전체를_모은다(rich):
    rows, n, last = pages(rich, {"id": F.V1, "limit": 7})
    rich.eng.sync()
    assert n > 1 and rows == list(rich.eng.g.q(CG.scene_rows, F.V1, 1))
    s = last["result"]["summary"]
    assert s["elements"] == len({r["id"] for r in rows}) and s["segments"] == sum(len(t[2]) for t in rich.eng.g.q(SC.view_scene, F.V1))


def test_큰_요소는_part로_나눈다(rich, monkeypatch):
    monkeypatch.setattr(CG, "ROW_BYTES", 200)
    rich.eng.sync()
    rows = rich.eng.g.q(CG.scene_rows, F.V1, 2)
    parted = [r for r in rows if "part" in r]
    assert parted and all(r["part"][1] > 1 for r in parted)
    d1 = [r for r in rows if r["id"] == F.D1]
    assert [r["part"][0] for r in d1] == list(range(1, len(d1) + 1))
    assert sum(len(r.get("lines", [])) for r in d1) == len({t[1]: t for t in rich.eng.g.q(SC.view_scene, F.V1)}[F.D1][2])


def test_거르기_타일_ids_종류_자릿수(rich):
    rows = ok(rich.run("view.scene", {"id": F.V1, "ids": [F.D1, F.DR1]}))["items"]
    assert {r["id"] for r in rows} == {F.D1, F.DR1}
    rows = ok(rich.run("view.scene", {"id": F.V1, "kinds": ["wall"]}))["items"]
    assert {r["kind"] for r in rows} == {"wall"}
    rows, _, _ = pages(rich, {"id": F.V1, "bbox": [5500, 3500, 6500, 4500]})   # 북동 모서리 타일
    ids = {r["id"] for r in rows}
    assert F.W2 in ids and F.W3 in ids and F.D1 not in ids
    r0 = ok(rich.run("view.scene", {"id": F.V1, "ids": [F.D1], "decimals": 0}))["items"][0]
    assert all(isinstance(c, int) for ln in r0["lines"] for c in ln[1:])
    r3 = ok(rich.run("view.scene", {"id": F.V1, "ids": [F.D1], "decimals": 3}))["items"][0]
    assert any(isinstance(c, float) and c != int(c) for ln in r3["lines"] for c in ln[1:])


def test_조회는_저장소를_바꾸지_않는다(rich):
    before = rich.eng.hashes()
    ok(rich.run("view.scene", {"id": F.V1}))
    ok(rich.run("view.pick", {"id": F.V1, "x": 3000, "y": 0}))
    ok(rich.run("geom.snap", {"view": F.V1, "x": 3000, "y": 0, "from": [0, 0]}))
    assert rich.eng.hashes() == before


def test_없는_뷰와_잘못된_커서(rich):
    r = rich.run("view.scene", {"id": "9" * 22})
    assert not r["ok"] and r["error"]["code"] == "NOT_FOUND"
    r = rich.run("view.scene", {"id": F.W1})
    assert not r["ok"]
    r = rich.run("view.scene", {"id": F.V1, "cursor": base64.urlsafe_b64encode(b'"x"').decode()})
    assert not r["ok"] and r["error"]["code"] == "SCHEMA_INVALID"
    r = rich.run("geom.snap", {"view": F.V1, "x": 0, "y": 0, "kinds": ["nearest"]})
    assert not r["ok"] and r["error"]["code"] == "SCHEMA_INVALID"


def test_뷰를_지우면_장면_노드도_치운다(rich):
    rich.eng.sync()
    rich.eng.g.q(CG.scene_rows, F.V1, 1)
    ok(rich.run("view.delete", {"id": F.V1}))
    rich.eng.sync()
    assert not [k for k in rich.eng.g.nodes if k[0] in (SC.view_scene, SC._el_parts_q, CG.scene_rows, CG.scene_digest) and k[1] == F.V1]


# ================================================================ 집기
def test_집기(rich):
    hits = ok(rich.run("view.pick", {"id": F.V1, "x": 3000, "y": 0}))["items"]
    assert hits[0]["id"] == F.W1 and hits[0]["hit"] == "fill" and hits[0]["dist"] == 0
    # 문 호 위의 점(문 D1: 경첩 1050,100, 폭 900 -> 호 반지름 900)
    hits = ok(rich.run("view.pick", {"id": F.V1, "x": 1050 + 900 * 0.7071, "y": 100 + 900 * 0.7071, "tol": 20}))["items"]
    assert hits[0]["id"] == F.D1 and hits[0]["kind"] == "door"
    hits = ok(rich.run("view.pick", {"id": F.V1, "x": 2000, "y": -1500, "tol": 10}))["items"]
    assert [h["id"] for h in hits] == [F.DR1] and hits[0]["kind"] == "draft"
    assert ok(rich.run("view.pick", {"id": F.V1, "x": 3000, "y": -3000, "tol": 10}))["items"] == []
    hits = ok(rich.run("view.pick", {"id": F.V1, "x": 3000, "y": 100, "limit": 1}))["items"]
    assert len(hits) == 1


def test_집기_순서는_거리_다음_절단선(rich):
    rich.eng.sync()
    sc = rich.eng.g.q(SC.view_scene, F.V1)
    hits = SC.pick(sc, 6000, 0, 300, 50)
    assert [h[0] for h in hits] == sorted(h[0] for h in hits)


# ================================================================ 스냅
def test_스냅_끝점_중점_교점(rich):
    r = ok(rich.run("geom.snap", {"view": F.V1, "x": 8, "y": -1495, "tol": 50, "kinds": ["end"]}))
    assert (r["best"]["x"], r["best"]["y"]) == (0.0, -1500.0) and r["best"]["ids"] == [F.DR1]
    r = ok(rich.run("geom.snap", {"view": F.V1, "x": 3010, "y": -1490, "tol": 50, "kinds": ["mid"]}))
    assert (r["best"]["x"], r["best"]["y"]) == (3000.0, -1500.0)
    r = ok(rich.run("geom.snap", {"view": F.V1, "x": 4005, "y": -1505, "tol": 50, "kinds": ["int"]}))   # DR1(가로) x DR2(세로 x=4000)
    assert (r["best"]["x"], r["best"]["y"]) == (4000.0, -1500.0) and set(r["best"]["ids"]) == {F.DR1, F.DR2}


def test_스냅_축선_수선_직교(rich):
    r = ok(rich.run("geom.snap", {"view": F.V1, "x": 5990, "y": 15, "tol": 50, "kinds": ["axis"]}))
    assert (r["best"]["x"], r["best"]["y"]) == (6000.0, 0.0) and set(r["best"]["ids"]) >= {F.W1, F.W2}
    r = ok(rich.run("geom.snap", {"view": F.V1, "x": 2510, "y": -1480, "tol": 50, "kinds": ["perp"], "from": [2500, -3000]}))
    assert (r["best"]["x"], r["best"]["y"]) == (2500.0, -1500.0)
    r = ok(rich.run("geom.snap", {"view": F.V1, "x": 3010, "y": 1900, "tol": 50, "kinds": ["ortho"], "from": [0, 1880]}))
    assert (r["best"]["x"], r["best"]["y"]) == (3010.0, 1880.0) and r["best"]["kind"] == "ortho"
    r = ok(rich.run("geom.snap", {"view": F.V1, "x": 3010, "y": 1900, "tol": 50, "kinds": ["perp", "ortho"]}))   # from 이 없으면 둘 다 없다
    assert r["best"] is None and r["items"] == []


def test_스냅_기본은_가까운_순(rich):
    r = ok(rich.run("geom.snap", {"view": F.V1, "x": 6020, "y": 30}))
    ds = [i["dist"] for i in r["items"]]
    assert ds == sorted(ds) and r["best"] == r["items"][0] and r["tol"] == 300.0
