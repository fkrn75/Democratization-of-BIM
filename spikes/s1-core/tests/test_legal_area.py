# SPDX-License-Identifier: MPL-2.0
"""법정 면적 체계(W1 연장, ARC-36·ANA-04): area.define·area.delete·area.list·area.legal.

모델: 지하 1층(B1, -3,300) 12x10m, 1층·2층 10x8m(벽 중심선), 옥상 레벨(6,600, 벽 없음), 대지 20x15m = 300㎡.
 1. 자동 산정: 바닥면적 = 벽 중심선 영역, 지하층 자동 판정, 건축면적 = 지표 1m 위 층 합집합, 연면적·용적률 산정용 연면적·건폐율·용적률
 2. 면적 객체: 노대(면적 - 접한 길이 x 1.5m), 바닥면적 불산입, 용적률만 제외, 건축면적 불산입, 대지면적 제외, manual(확인 필요)
 3. 거절: 근거와 대상·효과 불일치, 레벨 누락, 노대 길이 누락, manual note 없음
 4. IFC 왕복: 면적 객체가 되살고 면적표가 같다. 실행 취소
 5. 한도·지하층 직접 지정·대지 없음·층 표 쪽 나눔
"""
import sys

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import legal_area as LA

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

GUID = "5d0b1a50-0000-4a00-8000-0d0b1a5000a1"
B1, L1, L2, RF, SI = ("3" + f"{n:021d}" for n in (11, 12, 13, 14, 15))


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]["code"]


def _box(b, lv, w, h, tag):
    pts = [(0, 0), (w, 0), (w, h), (0, h)]
    for k in range(4):
        (x0, y0), (x1, y1) = pts[k], pts[(k + 1) % 4]
        ok(b.run("wall.create", {"id": f"{tag}{k + 1:021d}", "level": lv, "axis": [x0, y0, x1, y1], "thickness": 200, "height": 3000}))


def _build(tmp_path, site=True):
    from dobim_core.engine import Engine
    b = S.Bench(tmp_path)
    Engine.create(b.ws / "p.dobim", "s2", guid=GUID).close()
    for i, nm, el in ((B1, "B1", -3300), (L1, "1F", 0), (L2, "2F", 3300), (RF, "RF", 6600)):
        ok(b.run("level.create", {"id": i, "name": nm, "elevation": el}))
    _box(b, B1, 12000, 10000, "5")
    _box(b, L1, 10000, 8000, "6")
    _box(b, L2, 10000, 8000, "7")
    if site:
        ok(b.run("site.create", {"id": SI, "boundary": [[-5000, -5000], [15000, -5000], [15000, 10000], [-5000, 10000]], "name": "대지"}))
    return b


def _rows(r):
    cols = r["columns"]
    return {row[1]: dict(zip(cols, row)) for row in r["items"]}


def test_자동_산정_바닥_건축_연면적_건폐율_용적률(tmp_path):
    b = _build(tmp_path)
    r = ok(b.run("area.legal", {}))
    rows = _rows(r)
    assert rows["B1"]["basement"] is True and rows["B1"]["floor_m2"] == 120.0 and rows["B1"]["far_m2"] == 0.0
    assert rows["1F"]["basement"] is False and rows["1F"]["floor_m2"] == 80.0 and rows["1F"]["far_m2"] == 80.0
    assert rows["2F"]["floor_m2"] == 80.0 and rows["RF"]["floor_m2"] == 0.0 and rows["RF"]["height"] is None
    assert r["site"] == {"id": SI, "gross_m2": 300.0, "excluded_m2": 0.0, "area_m2": 300.0}
    assert r["gross_floor_m2"] == 280.0 and r["far_floor_m2"] == 160.0
    # 지하 1층(윗면 = 지표면, 1m 이하)은 건축면적에 넣지 않는다: 1·2층 합집합 80㎡
    assert rows["B1"]["building"] is False and r["building"]["area_m2"] == 80.0
    assert r["coverage_pct"] == round(80 / 300 * 100, 2) and r["far_pct"] == round(160 / 300 * 100, 2)
    assert "119-1-4-가" in r["basis"] and "법-55" in r["basis"] and r["source"] == LA.SOURCE
    assert any(u["code"] == "119-2" for u in r["unverified"])


def test_면적_객체_산입_불산입(tmp_path):
    b = _build(tmp_path)
    # 노대 6m x 2m = 12㎡, 접한 길이 6m -> 12 - 9 = 3㎡ 산입
    r = ok(b.run("area.define", {"name": "발코니", "rule": "119-1-3-나", "level": L2, "boundary": [[0, 8000], [6000, 8000], [6000, 10000], [0, 10000]], "contact_length": 6000}))
    assert r["created"] and r["target"] == "floor" and r["effect"] == "balcony" and r["area_m2"] == 12.0 and r["counted_m2"] == 3.0
    bal = r["id"]
    ok(b.run("area.define", {"name": "계단탑", "rule": "119-1-3-라", "level": L2, "boundary": [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]}))   # -4㎡
    ok(b.run("area.define", {"name": "주차", "rule": "119-1-4-나", "level": L1, "boundary": [[0, 0], [5000, 0], [5000, 4000], [0, 4000]]}))   # 용적률만 -20㎡
    ok(b.run("area.define", {"name": "통로", "rule": "119-1-2-다", "boundary": [[0, 0], [2000, 0], [2000, 5000], [0, 5000]]}))   # 건축면적 -10㎡
    ok(b.run("area.define", {"name": "도로 부분", "rule": "119-1-1-나", "boundary": [[-5000, -5000], [15000, -5000], [15000, -4000], [-5000, -4000]]}))   # 대지 -20㎡
    ok(b.run("area.define", {"name": "추가", "rule": "manual", "target": "floor", "effect": "add", "level": L1, "boundary": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]], "note": "시험용"}))
    r = ok(b.run("area.legal", {}))
    rows = _rows(r)
    assert rows["2F"]["adjust_m2"] == -1.0 and rows["2F"]["floor_m2"] == 79.0 and rows["2F"]["far_m2"] == 79.0
    assert rows["1F"]["floor_m2"] == 81.0 and rows["1F"]["far_m2"] == 61.0
    assert r["building"] == {"walls_m2": 80.0, "adjust_m2": -10.0, "area_m2": 70.0, "levels": 3}
    assert r["site"]["area_m2"] == 280.0 and r["gross_floor_m2"] == 280.0 and r["far_floor_m2"] == 140.0
    assert any(u["code"] == "manual" for u in r["unverified"]) and "119-1-3-나" in r["basis"]
    lst = ok(b.run("area.list", {"target": "floor"}))["items"]
    assert len(lst) == 4 and {x["rule"] for x in lst} == {"119-1-3-나", "119-1-3-라", "119-1-4-나", "manual"}
    # 바꾸기·지우기·실행 취소
    r2 = ok(b.run("area.define", {"id": bal, "name": "발코니", "rule": "119-1-3-나", "level": L2, "boundary": [[0, 8000], [6000, 8000], [6000, 10000], [0, 10000]], "contact_length": 8000}))
    assert not r2["created"] and r2["counted_m2"] == 0.0
    ok(b.run("area.delete", {"ids": [bal]}))
    assert len(ok(b.run("area.list", {}))["items"]) == 5
    ok(b.run("history.undo", {}))
    ok(b.run("history.undo", {}))
    assert _rows(ok(b.run("area.legal", {})))["2F"]["floor_m2"] == 79.0


def test_거절(tmp_path):
    b = _build(tmp_path)
    box = [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]
    assert err(b.run("area.define", {"name": "x", "rule": "119-1-3-라", "boundary": box})) == "CONSTRAINT"                         # 바닥면적인데 레벨 없음
    assert err(b.run("area.define", {"name": "x", "rule": "119-1-2-다", "level": L1, "boundary": box})) == "CONSTRAINT"            # 건축면적인데 레벨
    assert err(b.run("area.define", {"name": "x", "rule": "119-1-3-나", "level": L1, "boundary": box})) == "CONSTRAINT"            # 노대 길이 없음
    assert err(b.run("area.define", {"name": "x", "rule": "119-1-3-라", "level": L1, "boundary": box, "effect": "add"})) == "CONSTRAINT"   # 근거가 정한 효과
    assert err(b.run("area.define", {"name": "x", "rule": "manual", "target": "floor", "effect": "add", "level": L1, "boundary": box})) == "CONSTRAINT"   # note 없음
    assert err(b.run("area.define", {"name": "x", "rule": "manual", "target": "site", "effect": "far", "boundary": box, "note": "n"})) == "CONSTRAINT"   # far 는 바닥면적만
    assert err(b.run("area.define", {"name": "x", "rule": "119-9-9", "boundary": box})) == "SCHEMA_INVALID"
    assert err(b.run("area.define", {"id": L1, "name": "x", "rule": "119-1-2-다", "boundary": box})) == "CONSTRAINT"               # 다른 종류의 id
    assert err(b.run("area.delete", {"ids": [L1]})) == "NOT_FOUND"
    assert ok(b.run("area.list", {}))["items"] == []


def test_IFC_왕복은_면적_객체를_되살리고_면적표가_같다(tmp_path):
    b = _build(tmp_path)
    ok(b.run("area.define", {"name": "발코니", "rule": "119-1-3-나", "level": L2, "boundary": [[0, 8000], [6000, 8000], [6000, 10000], [0, 10000]], "contact_length": 6000}))
    ok(b.run("area.define", {"name": "통로", "rule": "119-1-2-다", "boundary": [[0, 0], [2000, 0], [2000, 5000], [0, 5000]], "note": "보행통로"}))
    h = b.hashes()
    b.export("t.ifc")
    r = b.import_ifc("t.ifc", "imp.dobim")
    assert r["ok"] and not r["result"]["warnings"], r
    assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    assert b.run("area.legal", {}, project="imp.dobim")["result"] == b.run("area.legal", {})["result"]
    assert b.run("area.list", {}, project="imp.dobim")["result"] == b.run("area.list", {})["result"]
    assert b.export("t2.ifc", "imp.dobim").read_bytes() == (tmp_path / "t.ifc").read_bytes()


def test_한도_지하층_지정_대지_없음_쪽_나눔(tmp_path):
    b = _build(tmp_path, site=False)
    r = ok(b.run("area.legal", {}))
    assert r["site"] is None and r["coverage_pct"] is None and any(u["code"] == "site" for u in r["unverified"])
    r = ok(b.run("area.legal", {"site_area_m2": 200, "coverage_max": 40, "far_max": 70, "basement_levels": []}))
    assert _rows(r)["B1"]["basement"] is False and r["far_floor_m2"] == 280.0
    assert r["limits"] == {"coverage_max": 40, "coverage_ok": True, "far_max": 70, "far_ok": False}
    assert err(b.run("area.legal", {"basement_levels": [SI]})) == "NOT_FOUND"
    # 층이 많으면 층 표를 쪽으로 나눈다(합계는 쪽마다 같다)
    for k in range(60):
        ok(b.run("level.create", {"name": f"X{k:02d}-" + "가" * 40, "elevation": 10000 + 3000 * k}))
    names, cur, pages, tot = [], None, 0, set()
    while True:
        e = b.run("area.legal", {**({"cursor": cur} if cur else {})})
        r = ok(e)
        names += [x[1] for x in r["items"]]
        tot.add(r["gross_floor_m2"])
        pages += 1
        cur = e["page"]["next_cursor"]
        if not cur:
            break
    assert pages > 1 and len(names) == 64 and len(set(names)) == 64 and tot == {280.0}


def test_근거표는_대상과_효과가_맞다():
    for code, (t, e, text) in LA.RULES.items():
        assert text
        if code != LA.MANUAL:
            assert code.startswith("119-1-") and t in LA.TARGETS and e in LA.EFFECTS and LA.rule_fits(code, t, e)
    assert LA.is_basement(-3300, 3300, 0) is True and LA.is_basement(-1000, 3300, 0) is False and LA.is_basement(-1000, None, 0) is None
    assert LA.counts_for_building(-3300, 3300, 0) is False and LA.counts_for_building(-3300, 4400, 0) is True
    assert LA.balcony_counted(12e6, 6000) == 3e6 and LA.balcony_counted(5e6, 6000) == 0.0
