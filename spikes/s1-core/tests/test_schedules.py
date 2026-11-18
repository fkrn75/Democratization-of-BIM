# SPDX-License-Identifier: MPL-2.0
"""W1 범용 일람표: 모든 범주의 행, 필드 선택·계산 필드·필터·정렬·그룹·합계·단위, 재료 물량, CSV·시트 내보내기.
합성 데이터만 쓴다(기준 프로젝트 fixture.SCRIPT + 직사각형 슬래브·실·계단·솔리드·패밀리)."""
import csv
import io

import pytest

import kit  # noqa: F401
from dobim_core import fixture as F
from dobim_core import schedules as S


def gid(c, n):
    return c + f"{n:021d}"


def ok(sess, name, args):
    r = sess.run(name, args)
    assert r["ok"], r
    return r["result"]


def params_name(sess, eid):
    return sess.run("element.get", {"id": eid})["result"]["params"]["name"]


def run_export(sess, args):
    r = sess.run("schedule.export", args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    r = sess.run("schedule.export", args, confirm=r["error"]["details"]["confirm_token"])
    assert r["ok"], r
    return r["result"]


@pytest.fixture
def rich(base):
    """기준 프로젝트(벽 3·문 1·레벨 2)에 범주마다 요소를 하나 이상 더한다."""
    s = base
    ok(s, "slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200,
                          "openings": [[[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]]})
    ok(s, "slab.create", {"id": gid("e", 2), "level": F.LV2, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 250})
    ok(s, "space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[0, 0], [3000, 0], [3000, 4000], [0, 4000]], "name": "101", "category": "거실"})
    ok(s, "stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18})
    ok(s, "railing.create", {"id": F.RL1, "level": F.LV1, "path": [[0, 0], [3000, 0], [3000, 4000]], "height": 1100})
    ok(s, "roof.create", {"id": F.RF1, "level": F.LV2, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "form": "gable", "pitch": 30, "overhang": 0})
    ok(s, "solid.create", {"id": F.SO1, "level": F.LV1, "category": "column", "form": "prism",
                           "polygon": [[0, 0], [400, 0], [400, 500], [0, 500]], "z0": 0, "z1": 3000})
    ok(s, "solid.create", {"id": gid("d", 2), "level": F.LV1, "category": "beam", "form": "member", "start": [0, 0, 3000], "end": [4000, 0, 3000],
                           "section": {"shape": "rect", "width": 200, "depth": 500}})
    ok(s, "solid.create", {"id": gid("d", 3), "level": F.LV1, "category": "toilet", "form": "prism",
                           "polygon": [[100, 100], [500, 100], [500, 800], [100, 800]], "z0": 0, "z1": 400})
    ok(s, "solid.create", {"id": gid("d", 4), "level": F.LV1, "category": "ceiling", "form": "prism",
                           "polygon": [[0, 0], [3000, 0], [3000, 4000], [0, 4000]], "z0": 2600, "z1": 2612})
    ok(s, "elevator.create", {"id": F.EL1, "level": F.LV1, "shaft": [4000, 500, 5800, 2300], "rise": 3300})
    ok(s, "family.place", {"family": "window.fixed", "host": F.W2, "offset": 2000, "elevation": 900})
    ok(s, "family.place", {"family": "wardrobe.builtin", "level": F.LV1, "position": [2000, 3000]})
    return s


def test_범주_목록은_모든_범주와_요소_수를_준다(rich):
    r = ok(rich, "schedule.categories", {})
    by = {x["category"]: x for x in r["items"]}
    assert list(by) == list(S.CATEGORIES)
    want = {"walls": 3, "doors": 2, "windows": 1, "floors": 2, "roofs": 1, "ceilings": 1, "stairs": 1, "railings": 1,
            "columns": 1, "beams": 1, "rooms": 1, "elevators": 1, "plumbing_fixtures": 1, "furniture": 1}
    for c, n in want.items():
        assert by[c]["count"] == n, c
    assert by["piping"]["count"] == 0
    assert "area" in by["walls"]["fields"] and "volume" in by["floors"]["fields"]


def test_모든_요소는_어느_범주에든_든다(rich):
    g = rich.eng.g
    from dobim_core import queries as Q
    total = 0
    for kind in S.ROW_KINDS:
        for b in range(Q.NB):
            for e in Q.bucket_ids(g, kind, b):
                assert g.q(S.category_of, e) in S.CATEGORIES, (kind, e)
                total += 1
    assert total == sum(ok(rich, "schedule.categories", {})["items"][i]["count"] for i in range(len(S.CATEGORIES)))


def test_벽_일람표는_기존_schedule_rows_와_같은_값이다(rich):
    old = ok(rich, "schedule.rows", {"kind": "wall"})
    ci = {c: i for i, c in enumerate(old["columns"])}
    new = ok(rich, "schedule.table", {"category": "walls", "fields": ["id", "length", "thickness", "area", "volume"]})
    got = {r[0]: r for r in new["items"]}
    for r in old["items"]:
        g = got[r[ci["id"]]]
        assert g[1] == r[ci["length"]] and g[3] == r[ci["net_area"]]
        assert g[4] == pytest.approx(r[ci["net_area"]] * r[ci["thickness"]])
    summ = ok(rich, "schedule.summary", {"kind": "wall"})
    assert new["totals"]["count"] == summ["count"] and new["totals"]["length"] == pytest.approx(summ["total_length"])
    assert new["totals"]["area"] == pytest.approx(summ["total_net_area"])


def test_바닥_실_지붕_면적과_부피(rich):
    fl = ok(rich, "schedule.table", {"category": "floors", "fields": ["level", "area", "volume", "openings"], "sort": [{"field": "level"}]})
    assert fl["items"][0] == ["1F", 24e6 - 1e6, (24e6 - 1e6) * 200, 1]
    assert fl["items"][1][1] == 24e6 and fl["items"][1][2] == 24e6 * 250
    rm = ok(rich, "schedule.table", {"category": "rooms", "units": "m"})
    assert rm["columns"][:2] == ["number", "name"]
    assert rm["items"][0][0] == "101" and rm["items"][0][1] == "거실"
    assert rm["items"][0][rm["columns"].index("area")] == pytest.approx(12.0)
    rf = ok(rich, "schedule.table", {"category": "roofs", "fields": ["form", "area"]})
    # 박공 30도: 경사면 넓이 = 평면 24㎡ / cos30
    assert rf["items"][0][0] == "gable" and rf["items"][0][1] == pytest.approx(24e6 / (3 ** 0.5 / 2), rel=1e-6)


def test_솔리드와_패밀리_치수(rich):
    col = ok(rich, "schedule.table", {"category": "columns", "fields": ["ifc_class", "width", "length", "height", "volume"]})
    assert col["items"][0] == ["IfcColumn", 400.0, 500.0, 3000.0, pytest.approx(400 * 500 * 3000)]
    bm = ok(rich, "schedule.table", {"category": "beams", "fields": ["length", "width", "depth", "volume"]})
    assert bm["items"][0][:3] == [4000.0, 200, 500] and bm["items"][0][3] == pytest.approx(4000 * 200 * 500, rel=1e-6)
    ce = ok(rich, "schedule.table", {"category": "ceilings", "fields": ["area", "thickness"]})
    assert ce["items"][0][0] == pytest.approx(12e6) and ce["items"][0][1] == pytest.approx(12.0)
    wn = ok(rich, "schedule.table", {"category": "windows", "fields": ["type", "host", "ifc_class", "sill"]})
    assert wn["items"][0][1] == params_name(rich, F.W2) and wn["items"][0][2] == "IfcWindow" and wn["items"][0][3] == 900
    pf = ok(rich, "schedule.table", {"category": "plumbing_fixtures", "fields": ["type", "ifc_class"]})
    assert pf["items"][0] == ["toilet", "IfcSanitaryTerminal"]
    fu = ok(rich, "schedule.table", {"category": "furniture", "fields": ["ifc_class"]})
    assert fu["items"][0] == ["IfcFurniture"]
    st = ok(rich, "schedule.table", {"category": "stairs", "fields": ["risers", "riser_height"]})
    assert st["items"][0] == [18, pytest.approx(3000 / 18)]


def test_필터_정렬_그룹_합계_계산필드(rich):
    a = {"category": "walls", "fields": ["name", "level", "length", "len2"], "calc": [{"name": "len2", "expr": "(length + 0) * 2"}],
         "filters": [{"field": "length", "op": "ge", "value": 4000}], "sort": [{"field": "length", "desc": True}]}
    r = ok(rich, "schedule.table", a)
    lens = [x[2] for x in r["items"]]
    assert lens == sorted(lens, reverse=True) and all(v >= 4000 for v in lens)
    assert all(x[3] == x[2] * 2 for x in r["items"])
    assert r["totals"]["len2"] == pytest.approx(2 * r["totals"]["length"])
    g = ok(rich, "schedule.table", {"category": "floors", "fields": ["level", "count", "area"], "group_by": ["level"], "itemize": False})
    assert [x[0] for x in g["items"]] == ["1F", "2F"] and all(x[1] == 1 for x in g["items"])
    assert [gr["totals"]["count"] for gr in g["groups"]] == [1, 1]
    c = ok(rich, "schedule.table", {"category": "walls", "fields": ["name"], "filters": [{"field": "name", "op": "contains", "value": params_name(rich, F.W1).lower()}]})
    assert [x[0] for x in c["items"]] == [params_name(rich, F.W1)]


def test_잘못된_필드와_산식은_스키마에서_거절한다(rich):
    for bad in ({"category": "walls", "fields": ["risers"]},
                {"category": "walls", "calc": [{"name": "x", "expr": "length +"}]},
                {"category": "walls", "calc": [{"name": "x", "expr": "nope * 2"}]},
                {"category": "walls", "calc": [{"name": "area", "expr": "1"}]},
                {"category": "walls", "itemize": False}):
        r = rich.run("schedule.table", bad)
        assert not r["ok"] and r["error"]["code"] == "SCHEMA_INVALID", (bad, r)


def test_0으로_나누면_빈값이다():
    t = S.parse_expr("a / b", {"a", "b"})
    assert S.eval_expr(t, {"a": 1.0, "b": 0.0}) is None and S.eval_expr(t, {"a": 1.0, "b": None}) is None
    assert S.eval_expr(S.parse_expr("-a * (2 + 3)", {"a"}), {"a": 2}) == -10


def test_쪽_넘김(rich):
    a = {"category": "walls", "fields": ["id"], "limit": 2}
    e1 = rich.run("schedule.table", a)
    e2 = rich.run("schedule.table", {**a, "offset": int(e1["page"]["next_cursor"])})
    r1, r2 = e1["result"], e2["result"]
    assert len(r1["items"]) == 2 and len(r2["items"]) == 1 and e2["page"]["next_cursor"] is None and e1["page"]["total"] == 3
    assert len({x[0] for x in r1["items"] + r2["items"]}) == 3


def test_재료_물량은_층_구성을_층마다_나눈다(rich):
    ok(rich, "wall.layers", {"id": F.W1, "layers": [{"material": "콘크리트", "thickness": 150}, {"material": "단열재", "thickness": 50}]})
    r = ok(rich, "schedule.takeoff", {"categories": ["walls", "floors"], "units": "mm"})
    rows = [dict(zip(r["columns"], x)) for x in r["items"]]
    w1 = ok(rich, "schedule.table", {"category": "walls", "fields": ["id", "area", "volume"], "filters": [{"field": "id", "op": "eq", "value": F.W1}]})["items"][0]
    con = [x for x in rows if x["category"] == "walls" and x["material"] == "콘크리트"]
    ins = [x for x in rows if x["category"] == "walls" and x["material"] == "단열재"]
    assert con[0]["volume"] == pytest.approx(w1[2] * 150 / 200) and ins[0]["volume"] == pytest.approx(w1[2] * 50 / 200)
    assert con[0]["area"] == pytest.approx(w1[1])
    fl = [x for x in rows if x["category"] == "floors"]
    assert {x["level"] for x in fl} == {"1F", "2F"}
    assert r["totals"]["volume"] == pytest.approx(sum(x["volume"] for x in rows))


def test_CSV_내보내기(rich):
    res = run_export(rich, {"path": "walls.csv", "table": {"category": "walls", "fields": ["name", "level", "length"], "group_by": ["level"]}})
    raw = (rich.path.parent / "walls.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0] == ["name", "level", "length"]
    assert rows[-1][0].startswith("합계 (3)") and float(rows[-1][2]) > 0
    assert any(r[0].startswith("소계 1F") for r in rows)
    assert res["rows"] == 3


def test_시트_내보내기는_표와_표제란을_그린다(rich):
    res = run_export(rich, {"path": "takeoff.svg", "takeoff": {}, "project_name": "합성", "date": "2026-01-01"})
    svg = (rich.path.parent / "takeoff.svg").read_text(encoding="utf-8")
    assert 'id="schedule-1"' in svg and 'id="title-block"' in svg and "재료 물량표" in svg
    assert res["blocks"] == 1
    # 같은 입력이면 같은 바이트(결정성)
    b1 = (rich.path.parent / "takeoff.svg").read_bytes()
    run_export(rich, {"path": "takeoff.svg", "takeoff": {}, "project_name": "합성", "date": "2026-01-01", "overwrite": True})
    assert (rich.path.parent / "takeoff.svg").read_bytes() == b1


def test_긴_일람표는_단을_나눈다(base):
    for i in range(70):
        ok(base, "wall.create", {"id": gid("9", i), "level": F.LV1, "axis": [0, 10000 + 300 * i, 2000, 10000 + 300 * i], "thickness": 100, "height": 3000})
    res = run_export(base, {"path": "many.svg", "table": {"category": "walls", "fields": ["name", "length"]}, "date": "2026-01-01"})
    assert res["blocks"] >= 2
    svg = (base.path.parent / "many.svg").read_text(encoding="utf-8")
    assert 'id="schedule-2"' in svg


def test_내보내기_인자_검사(rich):
    r = rich.run("schedule.export", {"path": "x.csv"})
    assert not r["ok"] and r["error"]["code"] == "SCHEMA_INVALID"
    r = rich.run("schedule.export", {"path": "x.txt", "takeoff": {}})
    assert not r["ok"] and r["error"]["code"] == "PATH_DENIED"


def test_증분_계산은_전체_계산과_같다(rich):
    import sys
    from conftest import ROOT
    sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
    import s3lib
    a = ok(rich, "schedule.table", {"category": "walls"})
    ok(rich, "wall.set", {"id": F.W1, "height": 2800})
    b = ok(rich, "schedule.table", {"category": "walls"})
    assert a != b
    full = s3lib.RefGraph(s3lib._Inputs(rich.eng))
    for c in S.CATEGORIES:
        assert tuple(rich.eng.g.q(S.element_row, e) for e in rich.eng.g.q(S.category_ids, c)) == \
            tuple(full.q(S.element_row, e) for e in full.q(S.category_ids, c)), c
