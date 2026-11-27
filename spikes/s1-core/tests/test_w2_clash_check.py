# SPDX-License-Identifier: MPL-2.0
"""W2 스물여덟 번째 PR(간섭 검토 COL-08).

1. 순수 기하(clash.py): 볼록 조각의 겹침 깊이(분리축 정리)·겹친 부피·도심, 면 접촉은 간섭이 아님, 볼록하지 않은 메시, 넓은 단계 격자
2. clash.check: 벽·슬래브·보·솔리드 부재 등 요소 형상(개구부 반영)과 두 묶음 선택, 허용 오차, 의도된 접합 제외·포함, 근사 형상 표기, 결정적 순서·limit, 읽기 전용
3. clash.export(CSV·JSON)·clash.compare(새로·없어진·달라진 쌍), 거절 사례
합성 데이터만 쓴다(재현 입력은 일반화한 좌표)."""
import json
import time

import pytest

import kit  # noqa: F401
from dobim_core import clash as CL
from dobim_core import fixture as F

BT = "9" + "0" * 19 + "c1"
BA, BB = "9" + "0" * 19 + "n1", "9" + "0" * 19 + "n2"
sid = lambda n: "f" + f"{n:021d}"   # noqa: E731  솔리드 id 22자


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def g_of(s):
    s.eng.sync()
    s.eng._touch_pending()
    return s.eng.g


def duct(s, n, start, end, w=300, d=200, category="duct", shape="rect"):
    sec = {"shape": shape, "width": w, "depth": d}
    return ok(s.run("solid.create", {"id": sid(n), "level": F.LV1, "category": category, "form": "member", "start": start, "end": end, "section": sec, "anchor": "center"}))["id"]


def check(s, a, b=None, **kw):
    args = {"a": a, **({"b": b} if b is not None else {}), **kw}
    r = s.run("clash.check", args)
    return {**ok(r), "page": r["page"]}                                       # 쪽 번호표는 봉투의 page 로 올라온다


def pair_ids(res):
    return [(x["a"]["id"], x["b"]["id"]) for x in res["pairs"]]


def destructive(s, name, args):
    r = s.run(name, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


# ================================================================ 1. 순수 기하
def box(x0, y0, z0, x1, y1, z1):
    return CL.box_cx((x0, y0, z0), (x1, y1, z1))


def test_상자_겹침_깊이는_최소_이동_거리이고_면_접촉은_0():
    a = box(0, 0, 0, 10, 10, 10)
    assert CL.depth(a, box(8, 2, 2, 20, 8, 8)) == pytest.approx(2.0)          # x 로 2 만 빼면 풀린다
    assert CL.depth(a, box(5, 5, 5, 6, 6, 6)) == pytest.approx(5.0)           # 안에 든 작은 상자: 가장 가까운 면 밖으로 완전히 빼는 거리(10 - 5)
    assert CL.depth(a, box(10, 0, 0, 20, 10, 10)) == 0.0                      # 면 접촉
    assert CL.depth(a, box(10, 10, 10, 20, 20, 20)) == 0.0                    # 모서리 접촉
    assert CL.depth(a, box(11, 0, 0, 20, 10, 10)) == 0.0                      # 분리
    assert CL.depth(a, box(9.9999999, 0, 0, 20, 10, 10)) == 0.0               # 1e-6 mm 이하는 접촉


def test_기울어진_조각의_깊이_부피_도심은_해석값과_같다():
    cube = box(0, 0, 0, 10, 10, 10)
    # 마름모(꼭짓점 (9,5)(13,9)(17,5)(13,1))의 한 꼭짓점이 상자 면 x=10 을 1mm 파고든다
    dia = CL.prism_cx([(9, 5), (13, 1), (17, 5), (13, 9)], 0, 10)
    assert CL.depth(cube, dia) == pytest.approx(1.0)
    vol, c = CL.overlap(cube, dia)
    assert vol == pytest.approx(10.0)                                         # 밑변 2 · 높이 1 의 삼각형 x 높이 10
    assert c == pytest.approx((9 + 2 / 3, 5.0, 5.0))
    v2, c2 = CL.overlap(dia, cube)                                            # 순서를 바꿔도 같다
    assert v2 == pytest.approx(10.0) and c2 == pytest.approx(c)
    assert CL.overlap(cube, box(20, 0, 0, 30, 10, 10)) == (0.0, None)


def test_볼록하지_않은_메시는_볼록_조각이_아니다():
    v = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (1, 2, 0), (0, 2, 0)]      # L 자 단면
    verts = v + [(x, y, 1) for x, y, _ in v]
    loops = [tuple(range(6))[::-1], tuple(range(6, 12))] + [(i, (i + 1) % 6, 6 + (i + 1) % 6, 6 + i) for i in range(6)]
    assert CL.Cx.mesh(verts, loops) is None
    cube = CL.Cx.mesh([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
                      [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
    assert cube is not None and cube.vol == pytest.approx(1.0)


def test_넓은_단계_격자는_이웃만_후보로_삼는다():
    n = 40
    boxes = [(f"{i:04d}", (i * 1000.0, 0.0, 0.0), (i * 1000.0 + 600.0, 600.0, 600.0)) for i in range(n)]
    assert CL.candidate_pairs(boxes, boxes, 1.0, True) == []                    # 서로 떨어진 40개: 후보가 없다(모든 쌍 검사 아님)
    boxes[10] = ("0010", (9500.0, 0.0, 0.0), (10100.0, 600.0, 600.0))           # 9번(9000~9600)·11번(11000~) 중 9번과 겹친다
    assert CL.candidate_pairs(boxes, boxes, 1.0, True) == [("0009", "0010")]
    assert CL.candidate_pairs(boxes[:20], boxes[20:], 1.0, False) == []
    big = ("big", (-1e6, -1e6, -1e6), (1e6, 1e6, 1e6))                           # 아주 큰 요소는 격자에 넣지 않고 따로 본다
    assert len(CL.candidate_pairs([big], boxes, 1.0, False)) == n


# ================================================================ 2. clash.check
def test_덕트가_벽을_관통하면_깊이_부피_대표점을_알린다(base):
    d = duct(base, 1, [3000, -1000, 2500], [3000, 1000, 2500])
    r = check(base, {"categories": ["ducts"]}, {"categories": ["walls"]})
    assert r["count"] == 1 and r["page"] == {"next_cursor": None, "total": 1, "truncated": False} and r["tolerance_mm"] == 1.0
    row = r["pairs"][0]
    assert row["a"]["id"] == d and row["b"]["id"] == F.W1 and row["a"]["category"] == "ducts" and row["b"]["category"] == "walls"   # 묶음 a 의 요소가 앞
    assert row["depth_mm"] == 200.0                                             # 겹친 덩어리(300 x 200 x 200)의 최소 두께: 벽 두께이자 덕트 높이
    assert row["volume_mm3"] == pytest.approx(300 * 200 * 200)                  # 덕트 폭 x 벽 두께 x 덕트 높이
    assert row["point"] == pytest.approx([3000.0, 0.0, 2500.0], abs=0.1) and row["basis"] == "exact"
    assert r["checked"]["no_shape"] == 0 and r["checked"]["candidates"] == 1


def test_문_개구부로_지나가는_덕트는_벽과_간섭이_아니고_문과는_간섭이다(base):
    d = duct(base, 2, [1500, -1000, 1000], [1500, 1000, 1000])                  # 문 D1(x 1050~1950, 높이 2100)의 개구부 안을 지난다
    assert check(base, {"ids": [d]}, {"ids": [F.W1]})["count"] == 0
    r = check(base, {"ids": [d]}, {"ids": [F.D1]})
    assert r["count"] == 1 and r["pairs"][0]["b"]["kind"] == "door" and r["pairs"][0]["volume_mm3"] == pytest.approx(300 * 200 * 200)
    top = duct(base, 3, [1500, -1000, 2200], [1500, 1000, 2200], w=300, d=200)  # 문 위 인방(z 2100~)을 지난다: 벽과 간섭, 문과는 면 접촉뿐
    r = check(base, {"ids": [top]}, {"ids": [F.W1, F.D1]})
    assert [x["b"]["id"] for x in r["pairs"]] == [F.W1]


def test_조각_수는_개구부를_반영한다(base):
    g = g_of(base)
    sh = CL.build_shape(g, F.W1)
    assert len(sh.pieces) == 3 and sh.exact                                     # 문 왼쪽·문 오른쪽·문 위 띠
    assert sum(p.vol for p in sh.pieces) == pytest.approx(6100 * 200 * 3000 - 900 * 200 * 2100)
    assert len(CL.build_shape(g, F.W2).pieces) == 1


def test_면_접촉은_간섭이_아니고_허용_오차가_얕은_겹침을_거른다(base):
    duct(base, 4, [3000, -1000, 3100], [3000, 1000, 3100])                      # 아랫면 z 3000 = 벽 윗면: 맞닿기만 한다
    assert check(base, {"kinds": ["solid"]}, {"kinds": ["wall"]})["count"] == 0
    s2 = duct(base, 5, [3000, -1000, 3099.5], [3000, 1000, 3099.5])             # 0.5mm 파고든다
    assert check(base, {"ids": [s2]}, {"ids": [F.W1]})["count"] == 0            # 기본 허용 오차 1mm 이하
    r = check(base, {"ids": [s2]}, {"ids": [F.W1]}, tolerance=0.1)
    assert r["count"] == 1 and r["pairs"][0]["depth_mm"] == 0.5
    assert check(base, {"ids": [s2]}, {"ids": [F.W1]}, tolerance=0.5)["count"] == 0   # 깊이가 허용 오차 이하면 간섭이 아니다
    assert check(base, {"ids": [s2]}, {"ids": [F.W1]}, tolerance=0.0)["count"] == 1


def test_묶음_선택_범주_종류_id_레벨과_b_생략(base):
    d1 = duct(base, 6, [3000, -1000, 2500], [3000, 1000, 2500])
    d2 = duct(base, 7, [3100, -1000, 2500], [3100, 1000, 2500])                 # d1 과 겹치고 벽도 관통
    r = check(base, {"ids": [d1, d2]})                                          # b 생략: 묶음 안의 쌍
    assert pair_ids(r) == [(d1, d2)] and r["pairs"][0]["depth_mm"] == 200.0     # 겹친 덩어리 200(x) x 2000(y) x 200(z)
    assert check(base, {"kinds": ["solid"]})["count"] == 1
    assert check(base, {"categories": ["ducts"], "level": F.LV1})["count"] == 1
    assert check(base, {"categories": ["ducts"], "level": F.LV2})["count"] == 0   # 다른 레벨
    both = check(base, {"categories": ["ducts", "walls"]})                       # 묶음 안에서 덕트-벽·덕트-덕트
    assert both["count"] == 4 and {frozenset((x["a"]["id"], x["b"]["id"])) for x in both["pairs"]} == {
        frozenset((d1, d2)), frozenset((d1, F.W1)), frozenset((d2, F.W1)), frozenset((F.W2, F.W3))}
    assert check(base, {"ids": [d1]}, {"ids": [d1]})["count"] == 0              # 자기 자신과는 짝짓지 않는다
    sw = check(base, {"categories": ["walls"]}, {"categories": ["ducts"]})       # a 와 b 를 바꾸면 표시 순서도 바뀐다
    assert all(x["a"]["category"] == "walls" and x["b"]["category"] == "ducts" for x in sw["pairs"])


def test_조인된_벽의_모서리_겹침은_기본으로_뺀다(base):
    r = check(base, {"categories": ["walls"]})
    assert pair_ids(r) == [(F.W2, F.W3)]                                        # W1-W2 는 join.set 한 조인이라 설계 의도
    r2 = check(base, {"categories": ["walls"]}, include_connected=True)
    assert {frozenset(p) for p in pair_ids(r2)} == {frozenset((F.W1, F.W2)), frozenset((F.W2, F.W3))}
    row = [x for x in r2["pairs"] if F.W1 in (x["a"]["id"], x["b"]["id"])][0]
    assert row["depth_mm"] == 100.0 and row["volume_mm3"] == pytest.approx(200 * 100 * 3000, rel=1e-6)   # 모서리 겹침 200 x 100 x 3000
    g = g_of(base)
    assert (F.W1, F.D1) in CL.linked_pairs(g, [F.W1, F.D1, F.W2])               # 호스팅
    assert (F.W1, F.W2) in CL.linked_pairs(g, [F.W1, F.W2])                     # 조인


def test_슬래브_구멍과_관통_배관(base):
    ok(base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200,
                                "openings": [[[2000, 1000], [3000, 1000], [3000, 2000], [2000, 2000]]]}))
    through = duct(base, 8, [4500, 2500, -1000], [4500, 2500, 1000], w=100, d=100, category="pipe", shape="circle")
    hole = duct(base, 9, [2500, 1500, -1000], [2500, 1500, 1000], w=100, d=100, category="pipe", shape="circle")
    r = check(base, {"categories": ["piping"]}, {"categories": ["floors"]})
    assert pair_ids(r) == [(through, F.S1)]                                    # 구멍으로 지나는 배관은 슬래브와 간섭이 아니다
    row = r["pairs"][0]
    assert 99.0 < row["depth_mm"] < 100.1 and row["point"] == pytest.approx([4500.0, 2500.0, -100.0], abs=0.1)   # 겹친 덩어리(지름 100 의 원기둥)의 최소 두께
    assert row["volume_mm3"] == pytest.approx(3.14159 * 50 * 50 * 200, rel=0.03)  # 원(32각형) 단면 x 슬래브 두께
    assert hole not in [x["a"]["id"] for x in r["pairs"]]


def test_보끼리_끝_접합은_맞닿기만_해서_간섭이_아니다(base):
    ok(base.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    ok(base.run("beam.create", {"id": BA, "level": F.LV1, "type": BT, "start": [100, 1000], "end": [5500, 1000], "offset": 2800}))
    ok(base.run("beam.create", {"id": BB, "level": F.LV1, "type": BT, "start": [3000, 1000], "end": [3000, 3000], "offset": 2800}))   # 끝이 주 보 안에 든다 -> 면까지 잘린다
    g = g_of(base)
    assert (BA, BB) in CL.linked_pairs(g, [BA, BB])
    assert check(base, {"categories": ["beams"]}, include_connected=True)["count"] == 0
    assert len(CL.build_shape(g, BA).pieces) == 1
    wall = check(base, {"categories": ["beams"]}, {"categories": ["walls"]})
    assert wall["count"] == 0                                                   # 보는 벽(y=0~)과 떨어져 있다


def test_근사_형상은_basis_approx_로_표기하고_approx_false_는_뺀다(base):
    ok(base.run("wall.arc", {"id": "1" + "0" * 20 + "9", "level": F.LV1, "points": [[0, 8000], [-1200, 7600], [0, 7200]], "thickness": 200, "height": 3000}))
    aw = "1" + "0" * 20 + "9"
    d = duct(base, 10, [-1200, 7000, 2500], [-1200, 8200, 2500])               # 호의 한가운데(x=-1200)를 가로지른다
    r = check(base, {"ids": [d]}, {"ids": [aw]})
    assert r["count"] == 1 and r["pairs"][0]["basis"] == "approx"
    assert check(base, {"ids": [d]}, {"ids": [aw]}, approx=False)["count"] == 0
    ok(base.run("roof.create", {"id": F.RF1, "level": F.LV2, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "form": "gable", "pitch": 30, "overhang": 600}))
    sh = CL.build_shape(g_of(base), F.RF1)
    assert sh is not None and not sh.exact
    assert CL.build_shape(g_of(base), F.W1).exact


def test_계단_난간_승강기_형상(base):
    ok(base.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18}))
    ok(base.run("elevator.create", {"id": F.EL1, "level": F.LV1, "shaft": [4000, 500, 5800, 2300], "rise": 6000}))
    ok(base.run("railing.create", {"id": F.RL1, "level": F.LV1, "path": [[0, 0], [3000, 0]], "height": 1100}))
    g = g_of(base)
    st, el, rl = CL.build_shape(g, F.ST1), CL.build_shape(g, F.EL1), CL.build_shape(g, F.RL1)
    assert st.exact and len(st.pieces) == 17 and not el.exact and not rl.exact   # 계단은 디딤판마다 정확한 조각
    r = check(base, {"kinds": ["stair"]}, {"kinds": ["elevator"]})              # 계단(x 1000~5000)이 승강기 구덩이(x 4000~)에 들어간다
    assert r["count"] == 1 and r["pairs"][0]["basis"] == "approx"
    d = duct(base, 11, [2000, 400, 500], [2000, 1600, 500])                     # 디딤판을 파고드는 덕트
    assert check(base, {"ids": [d]}, {"kinds": ["stair"]})["count"] == 1


def test_순서는_깊이가_큰_것부터이고_결정적이며_limit는_개수를_자른다(base):
    ids = [duct(base, 20 + i, [500 + 700 * i, -1000, 2500], [500 + 700 * i, 1000, 2500], w=60 + 30 * i, d=200) for i in range(5)]
    r1 = check(base, {"ids": ids}, {"ids": [F.W1]}, limit=100)
    r2 = check(base, {"ids": ids}, {"ids": [F.W1]}, limit=100)
    assert r1 == r2 and r1["count"] == 5
    depths = [x["depth_mm"] for x in r1["pairs"]]
    assert depths == sorted(depths, reverse=True) and len(set(depths)) == 5
    cut = check(base, {"ids": ids}, {"ids": [F.W1]}, limit=2)
    assert cut["count"] == 5 and cut["pairs"] == r1["pairs"][:2] and cut["page"]["next_cursor"] and not cut["page"]["truncated"]
    rest = check(base, {"ids": ids}, {"ids": [F.W1]}, limit=100, cursor=cut["page"]["next_cursor"])                  # 다음 쪽
    assert rest["pairs"] == r1["pairs"][2:] and rest["page"]["next_cursor"] is None


def test_검사는_모델을_바꾸지_않는다(base):
    duct(base, 30, [3000, -1000, 2500], [3000, 1000, 2500])
    rev = base.eng.rev
    n = ok(base.run("history.list", {"limit": 500}))
    ok(base.run("clash.check", {"a": {"categories": ["ducts"]}, "b": {"categories": ["walls"]}}))
    assert base.eng.rev == rev and ok(base.run("history.list", {"limit": 500})) == n


def test_입력_거절(base):
    assert err(base.run("clash.check", {"a": {}}))["code"] == "SCHEMA_INVALID"                     # 선택이 비었다
    assert err(base.run("clash.check", {"a": {"categories": ["rooms"]}}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("clash.check", {"a": {"categories": ["walls"]}, "tolerance": -1}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("clash.check", {"a": {"categories": ["walls"]}, "limit": 0}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("clash.check", {"a": {"categories": ["walls"]}, "b": None}))["code"] == "SCHEMA_INVALID"    # 명시적 null 은 거절
    assert err(base.run("clash.check", {"a": {"ids": ["9" * 22]}}))["code"] == "NOT_FOUND"
    assert err(base.run("clash.check", {"a": {"kinds": ["view"]}}))["code"] == "SCHEMA_INVALID"
    nos = check(base, {"ids": [F.LV1, F.W1]})                                   # 형상이 없는 요소(레벨)는 건너뛰고 알린다
    assert nos["checked"]["no_shape"] == 1 and nos["checked"]["no_shape_ids"] == [F.LV1]


def test_레지스트리_예시가_모두_돈다(base):
    from dobim_core import registry
    reg = registry.load()
    for name in ("clash.check", "clash.export", "clash.compare"):
        c = reg[name]
        assert c.risk == ("destructive" if name == "clash.export" else "read") and c.examples
    for ex in reg["clash.check"].examples:
        assert ok(base.run("clash.check", ex))["count"] >= 0


# ================================================================ 3. 내보내기·비교
def test_CSV_내보내기(base, tmp_path):
    duct(base, 40, [3000, -1000, 2500], [3000, 1000, 2500])
    args = {"path": "clash.csv", "a": {"categories": ["ducts", "walls"]}}
    r = destructive(base, "clash.export", args)
    out = ok(r)
    assert out["format"] == "csv" and out["rows"] == out["count"] == 2 and not out["truncated"]
    raw = (tmp_path / "clash.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    lines = raw.decode("utf-8-sig").split("\r\n")
    assert lines[0] == "a_id,a_kind,a_category,a_name,a_level,b_id,b_kind,b_category,b_name,b_level,depth_mm,volume_mm3,x,y,z,basis"
    cols = lines[1].split(",")
    assert cols[0] == F.W1 and cols[5] == sid(40) and cols[10] == "200" and cols[11] == "12000000" and cols[-1] == "exact"   # 같은 묶음 안의 쌍은 id 순
    assert len([x for x in lines if x]) == 3
    assert err(base.run("clash.export", args))["code"] == "FILE_EXISTS"            # 이미 있는 파일은 덮어쓰기 동의 없이는 거절
    ow = ok(destructive(base, "clash.export", {**args, "overwrite": True}))
    assert ow["snapshot"] and (tmp_path / ".dobim-snapshots").is_dir()


def test_JSON_내보내기는_정의를_담고_거절_사례를_지킨다(base, tmp_path):
    duct(base, 41, [3000, -1000, 2500], [3000, 1000, 2500])
    a = {"categories": ["ducts"]}
    out = ok(destructive(base, "clash.export", {"path": "c.json", "a": a, "b": {"categories": ["walls"]}, "tolerance": 2.5, "limit": 10}))
    doc = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert doc["format"] == "dobim-clash" and doc["version"] == 1 and doc["count"] == 1 and not doc["truncated"]
    assert doc["definition"] == {"a": a, "b": {"categories": ["walls"]}, "tolerance": 2.5, "include_connected": False, "approx": True}
    assert doc["pairs"][0]["depth_mm"] == 200.0 and out["bytes"] == (tmp_path / "c.json").stat().st_size
    for bad in ("x.txt", "../x.csv", ".dobim-snapshots/x.csv", "c:/x.csv"):
        r = base.run("clash.export", {"path": bad, "a": a})
        assert not r["ok"] and r["error"]["code"] in ("PATH_DENIED", "SCHEMA_INVALID"), (bad, r)
    cut = ok(destructive(base, "clash.export", {"path": "t.json", "a": {"categories": ["walls"]}, "include_connected": True, "limit": 1}))
    assert cut["count"] == 2 and cut["rows"] == 1 and cut["truncated"]


def test_비교는_새로_생긴_없어진_달라진_쌍을_알린다(base, tmp_path):
    d1 = duct(base, 50, [3000, -1000, 2500], [3000, 1000, 2500])
    a = {"categories": ["ducts", "walls"]}
    ok(destructive(base, "clash.export", {"path": "before.json", "a": a}))
    same = ok(base.run("clash.compare", {"before": "before.json"}))
    assert same["new_count"] == same["resolved_count"] == same["changed_count"] == 0 and same["unchanged_count"] == 2 and same["items"] == []
    assert same["definition_from"] == "file" and same["before"] == {"file": "before.json", "rev": same["before"]["rev"], "count": 2, "truncated": False}
    d2 = duct(base, 51, [4500, -1000, 2500], [4500, 1000, 2500])                  # 새로 생긴 간섭
    ok(base.run("wall.move", {"id": F.W3, "dx": 0, "dy": 2000}))                    # W2-W3 모서리 겹침이 사라진다
    ok(base.run("solid.set", {"id": d1, "section": {"shape": "rect", "width": 150, "depth": 200}}))   # 깊이 200 -> 150 으로 달라진다
    r = ok(base.run("clash.compare", {"before": "before.json"}))
    assert r["new_count"] == 1 and r["resolved_count"] == 1 and r["changed_count"] == 1
    new, gone, chg = r["items"]                                                   # 항목 순서: 새로 생긴 -> 없어진 -> 달라진
    assert new["change"] == "new" and (new["a"]["id"], new["b"]["id"]) == (F.W1, d2) and new["point"][0] == pytest.approx(4500.0, abs=0.1)
    assert gone["change"] == "resolved" and {gone["a"]["id"], gone["b"]["id"]} == {F.W2, F.W3}
    assert chg == {"change": "changed", "a": F.W1, "b": d1, "before_mm": 200.0, "now_mm": 150.0}
    over = ok(base.run("clash.compare", {"before": "before.json", "definition": {"a": {"categories": ["walls"]}}}))   # 정의를 바꿔 견준다
    assert over["definition_from"] == "args" and over["now"]["count"] == 0 and over["resolved_count"] == 2 and len(over["items"]) == 2
    cur = ok(base.run("clash.check", {"a": a}))
    assert r["now"]["count"] == cur["count"] == 2


def test_비교_파일_거절(base, tmp_path):
    assert err(base.run("clash.compare", {"before": "none.json"}))["code"] == "NOT_FOUND"
    assert err(base.run("clash.compare", {"before": "x.csv"}))["code"] == "PATH_DENIED"
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert err(base.run("clash.compare", {"before": "bad.json"}))["code"] == "CONSTRAINT"
    (tmp_path / "other.json").write_text(json.dumps({"format": "other", "pairs": [], "definition": {}}), encoding="utf-8")
    assert err(base.run("clash.compare", {"before": "other.json"}))["code"] == "CONSTRAINT"
    (tmp_path / "baddef.json").write_text(json.dumps({"format": "dobim-clash", "pairs": [], "definition": {"a": {}}}), encoding="utf-8")
    assert err(base.run("clash.compare", {"before": "baddef.json"}))["code"] == "CONSTRAINT"
    assert err(base.run("clash.compare", {"before": "../x.json"}))["code"] == "PATH_DENIED"


def test_요소를_옮기면_다시_검사한_결과가_따라간다(base):
    d = duct(base, 60, [3000, -1000, 2500], [3000, 1000, 2500])
    assert check(base, {"ids": [d]}, {"ids": [F.W1]})["count"] == 1
    ok(base.run("wall.move", {"id": F.W1, "dx": 0, "dy": -3000}))
    assert check(base, {"ids": [d]}, {"ids": [F.W1]})["count"] == 0


def test_요소가_많아도_후보는_이웃만이다(sess):
    assert sess.run(*F.SCRIPT[0])["ok"]
    cmds = []
    n = 24
    for i in range(n):
        for j in range(n):
            cmds.append({"command": "solid.create", "args": {"id": sid(1000 + i * n + j), "level": F.LV1, "category": "duct", "form": "prism",
                                                              "polygon": [[i * 1000, j * 1000], [i * 1000 + 600, j * 1000], [i * 1000 + 600, j * 1000 + 600], [i * 1000, j * 1000 + 600]],
                                                              "z0": 0, "z1": 500}})
    assert sess.batch(cmds)["ok"]
    r = check(sess, {"kinds": ["solid"]})
    assert r["count"] == 0 and r["checked"]["candidates"] == 0                  # 576개인데 후보 쌍은 0(모든 쌍 576 x 575 / 2 = 165600 을 보지 않는다)
    ok(sess.run("solid.create", {"id": sid(5000), "level": F.LV1, "category": "duct", "form": "prism",
                                 "polygon": [[550, 0], [1150, 0], [1150, 600], [550, 600]], "z0": 0, "z1": 500}))   # 이웃 둘과 50mm·150mm 겹친다
    t0 = time.perf_counter()
    r = check(sess, {"kinds": ["solid"]})
    assert time.perf_counter() - t0 < 30
    assert r["checked"]["candidates"] == 2 and [x["depth_mm"] for x in r["pairs"]] == [150.0, 50.0]


def test_조각을_나누어도_깊이_부피_대표점은_같다():
    one = CL.Shape([CL.box_cx((0, 0, 0), (100, 10, 10))])
    two = CL.Shape([CL.box_cx((0, 0, 0), (40, 10, 10)), CL.box_cx((40, 0, 0), (100, 10, 10))])    # 같은 상자를 둘로 쪼갠 것
    other = CL.Shape([CL.box_cx((30, -5, 2), (60, 20, 8))])
    a, b = CL.pair_overlap(one, other), CL.pair_overlap(two, other)
    assert a[0] == pytest.approx(b[0]) and a[1] == pytest.approx(b[1]) and a[2] == pytest.approx(b[2])
    assert a[0] == pytest.approx(30 * 10 * 6) and a[1] == pytest.approx(6.0) and a[2] == pytest.approx((45.0, 5.0, 5.0))

def test_응답은_크기_상한에_맞춰_쪽으로_나뉜다(base):
    from dobim_core import limits
    for i in range(40):
        duct(base, 100 + i, [2000 + 60 * i, -1000, 2500], [2000 + 60 * i, 1000, 2500], w=50, d=200)
    seen, cursor, pages = [], None, 0
    while True:
        r = base.run("clash.check", {"a": {"categories": ["ducts"]}, "b": {"categories": ["walls"]}, "limit": 1000, **({"cursor": cursor} if cursor else {})})
        res = {**ok(r), "page": r["page"]}
        assert len(json.dumps(r, ensure_ascii=False).encode("utf-8")) <= limits.RESPONSE_CAP_BYTES and limits.est_tokens(r) <= limits.RESPONSE_CAP_TOKENS
        seen += [x["a"]["id"] for x in res["pairs"]]
        pages += 1
        assert res["page"]["total"] == res["count"] == 40
        cursor = res["page"]["next_cursor"]
        if cursor is None:
            break
        assert res["page"]["truncated"]                                           # 상한 때문에 잘렸다
    assert pages > 1 and len(seen) == 40 and len(set(seen)) == 40                    # 빠짐·겹침 없이 모두 나온다
    assert err(base.run("clash.check", {"a": {"categories": ["ducts"]}, "cursor": "###"}))["code"] == "SCHEMA_INVALID"
    big = ok(destructive(base, "clash.export", {"path": "all.json", "a": {"categories": ["ducts"]}, "b": {"categories": ["walls"]}}))   # 내보내기는 쪽으로 나누지 않고 파일에 다 쓴다
    assert big["rows"] == big["count"] == 40
    cr = base.run("clash.compare", {"before": "all.json", "limit": 1000})
    cmp = ok(cr)
    assert cmp["unchanged_count"] == 40 and cmp["items"] == [] and cr["page"]["next_cursor"] is None