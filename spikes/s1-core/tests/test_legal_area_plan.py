# SPDX-License-Identifier: MPL-2.0
"""법정 면적 체계 나머지(W1 연장 5번째 PR, ARC-36): 법정 면적 평면(view.area_plan scheme legal)과 면적표 CSV(area.export).

모델(test_legal_area 와 같은 합성 모델): 지하 1층 12x10m, 1층·2층 10x8m(벽 중심선), 옥상 레벨, 대지 20x15m = 300㎡.
 1. 법정 면적 평면: 벽 중심선 바닥면적 경계 + 그 층 법정 면적 객체(이름·근거·산입 면적, 해치), 층 합계 = area.legal 층 표
 2. 모델을 따라간다(객체 바꾸기·지우기·실행 취소), 증분 그림 = 새로 연 그림(바이트), 다른 면적 평면·일반 평면은 객체가 생겨도 바이트 그대로
 3. 시트·PNG·PDF·DXF·GUI 장면, IFC 왕복(뷰 정의·면적 값)
 4. area.export: all·summary·levels·objects CSV 값 = area.legal, 확인 토큰·덮어쓰기·확장자 거절
"""
import csv
import io
import sys

import kit
from conftest import ROOT
from dobim_core import viewplans as VP

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

GUID = "5d0b1a50-0000-4a00-8000-0d0b1a5000a2"
B1, L1, L2, RF, SI = ("3" + f"{n:021d}" for n in (11, 12, 13, 14, 15))
VA, VG, VP1 = ("9" + f"{n:021d}" for n in (51, 52, 53))


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]["code"]


def _box(run, lv, w, h, tag):
    pts = [(0, 0), (w, 0), (w, h), (0, h)]
    for k in range(4):
        (x0, y0), (x1, y1) = pts[k], pts[(k + 1) % 4]
        ok(run("wall.create", {"id": f"{tag}{k + 1:021d}", "level": lv, "axis": [x0, y0, x1, y1], "thickness": 200, "height": 3000}))


def _model(run):
    for i, nm, el in ((B1, "B1", -3300), (L1, "1F", 0), (L2, "2F", 3300), (RF, "RF", 6600)):
        ok(run("level.create", {"id": i, "name": nm, "elevation": el}))
    _box(run, B1, 12000, 10000, "5")
    _box(run, L1, 10000, 8000, "6")
    _box(run, L2, 10000, 8000, "7")
    ok(run("site.create", {"id": SI, "boundary": [[-5000, -5000], [15000, -5000], [15000, 10000], [-5000, 10000]], "name": "대지"}))


def _objects(run):
    """1층: 주차(용적률만 -20㎡)·추가(manual +1㎡), 2층: 노대(12㎡ -> 3㎡)·계단탑(-4㎡), 건축면적 통로(-10㎡)·대지 도로(-20㎡)."""
    ids = {}
    ids["park"] = ok(run("area.define", {"name": "주차", "rule": "119-1-4-나", "level": L1, "boundary": [[0, 0], [5000, 0], [5000, 4000], [0, 4000]]}))["id"]
    ids["add"] = ok(run("area.define", {"name": "추가", "rule": "manual", "target": "floor", "effect": "add", "level": L1,
                                        "boundary": [[6000, 6000], [7000, 6000], [7000, 7000], [6000, 7000]], "note": "시험용"}))["id"]
    ids["bal"] = ok(run("area.define", {"name": "발코니", "rule": "119-1-3-나", "level": L2, "boundary": [[0, 8000], [6000, 8000], [6000, 10000], [0, 10000]],
                                        "contact_length": 6000}))["id"]
    ids["stair"] = ok(run("area.define", {"name": "계단탑", "rule": "119-1-3-라", "level": L2, "boundary": [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]}))["id"]
    ok(run("area.define", {"name": "통로", "rule": "119-1-2-다", "boundary": [[0, 0], [2000, 0], [2000, 5000], [0, 5000]]}))
    ok(run("area.define", {"name": "도로 부분", "rule": "119-1-1-나", "boundary": [[-5000, -5000], [15000, -5000], [15000, -4000], [-5000, -4000]]}))
    return ids


def _export(run, vid, path):
    r = run("view.export", {"id": vid, "path": path, "overwrite": True})
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = run("view.export", {"id": vid, "path": path, "overwrite": True}, confirm=r["error"]["details"]["confirm_token"])
    return ok(r)


def _svg(s, vid, name):
    _export(s.run, vid, name)
    return (s.path.parent / name).read_text(encoding="utf-8")


def _group(svg, gid):
    if f'id="{gid}"' not in svg:
        return ""
    i = svg.index(f'id="{gid}"')
    return svg[i:svg.index("</g>", i)]


def _legal_rows(r):
    return {row[1]: dict(zip(r["columns"], row)) for row in r["items"]}


def _session(tmp_path, name="p.dobim"):
    s = kit.Session(tmp_path / name, guid=GUID)
    _model(s.run)
    return s


def test_법정_면적_평면은_벽_중심선_경계와_면적_객체를_그리고_층_합계가_면적표와_같다(tmp_path):
    s = _session(tmp_path)
    try:
        ids = _objects(s.run)
        ok(s.run("view.area_plan", {"id": VA, "name": "1F 법정 바닥면적", "level": L1, "scheme": "legal"}))
        ok(s.run("view.area_plan", {"id": VP1, "name": "2F 법정 바닥면적", "level": L2, "scheme": "legal"}))
        rows = _legal_rows(ok(s.run("area.legal", {})))
        a1, a2 = ok(s.run("view.areas", {"id": VA})), ok(s.run("view.areas", {"id": VP1}))
        assert a1["scheme"] == "legal" and a1["walls_m2"] == rows["1F"]["walls_m2"] == 80.0
        assert a1["total_m2"] == rows["1F"]["floor_m2"] == 81.0 and a1["adjust_m2"] == rows["1F"]["adjust_m2"] and a1["far_excluded_m2"] == -20.0
        assert a2["total_m2"] == rows["2F"]["floor_m2"] == 79.0 and a2["far_excluded_m2"] == 0.0
        assert [(x["name"], x["rule"], x["effect"], x["area_m2"], x["counted_m2"]) for x in a1["items"]] == [
            ("바닥면적(벽 중심선)", "119-1-3", "walls", 80.0, 80.0), ("주차", "119-1-4-나", "far", 20.0, -20.0), ("추가", "manual", "add", 1.0, 1.0)]
        assert a1["items"][0]["id"] is None and a1["items"][1]["id"] == ids["park"]
        assert sorted(x["counted_m2"] for x in a2["items"]) == [-4.0, 3.0, 80.0]   # 노대 12 - 6 x 1.5 = 3㎡(항목은 벽 영역 다음 id 순)
        sv = _svg(s, VA, "l1.svg")
        assert "면적 평면도" in sv and "바닥면적(벽 중심선)" in sv and "80.00㎡" in sv
        assert "119-1-4-나 용적률 제외 -20.00㎡" in sv and "manual 산입 +1.00㎡" in sv and "바닥면적 81.00㎡ (용적률 산정 제외 20.00㎡)" in sv
        assert "통로" not in sv and "도로 부분" not in sv   # 대지·건축면적 대상(레벨 없음)은 그리지 않는다
        assert _group(sv, "lines-symbol").count("<line") >= 8 and _group(sv, "lines-hatch").count("<line") > 5
        sv2 = _svg(s, VP1, "l2.svg")
        assert "119-1-3-나 노대 산입 +3.00㎡" in sv2 and "119-1-3-라 불산입 -4.00㎡" in sv2 and "바닥면적 79.00㎡" in sv2 and "용적률 산정" not in sv2
        # 벽 중심선 경계는 벽 축 위(바깥면 기준인 gross 와 다르다)
        it = s.eng.g.q(VP.legal_items, VA)[0]
        xs = sorted({round(p[0]) for e in it[4] for p in e})
        assert xs == [0, 10000]
        # 벽이 없는 층(옥상)은 항목 없음
        ok(s.run("view.area_plan", {"id": VG, "name": "RF 법정", "level": RF, "scheme": "legal"}))
        assert ok(s.run("view.areas", {"id": VG}))["items"] == [] and "㎡" not in _svg(s, VG, "rf.svg")
    finally:
        s.close()


def test_모델을_따라가고_증분_그림이_새로_연_그림과_같다(tmp_path):
    s = _session(tmp_path)
    try:
        ok(s.run("view.area_plan", {"id": VA, "name": "1F 법정", "level": L1, "scheme": "legal"}))
        ok(s.run("view.area_plan", {"id": VG, "name": "1F 바닥면적", "level": L1, "scheme": "gross"}))
        ok(s.run("view.create", {"id": VP1, "name": "1F 평면", "type": "plan", "level": L1, "cut_height": 1200}))
        gross0, plan0, legal0 = _svg(s, VG, "g0.svg"), _svg(s, VP1, "p0.svg"), _svg(s, VA, "a0.svg")
        assert "바닥면적 80.00㎡" in legal0 and "119-1" not in legal0
        ids = _objects(s.run)
        assert _svg(s, VG, "g1.svg") == gross0 and _svg(s, VP1, "p1.svg") == plan0   # 다른 면적 평면·일반 평면은 객체가 생겨도 그대로
        a = _svg(s, VA, "a1.svg")
        assert "주차" in a and a != legal0
        ok(s.run("area.define", {"id": ids["park"], "name": "주차장", "rule": "119-1-4-나", "level": L1, "boundary": [[0, 0], [5000, 0], [5000, 2000], [0, 2000]]}))
        a2 = _svg(s, VA, "a2.svg")
        assert "주차장" in a2 and "-10.00㎡" in a2 and ok(s.run("view.areas", {"id": VA}))["far_excluded_m2"] == -10.0
        ok(s.run("wall.move", {"id": "6" + f"{2:021d}", "dx": 2000}))   # 1층 동쪽 벽을 떼어 놓으면 닫힌 벽 영역이 없어진다(막다른 가지는 걷는다)
        assert ok(s.run("view.areas", {"id": VA}))["walls_m2"] == 0.0 and "바닥면적(벽 중심선)" not in _svg(s, VA, "mv.svg")
        ok(s.run("history.undo", {}))
        assert ok(s.run("view.areas", {"id": VA}))["walls_m2"] == 80.0
        ok(s.run("area.delete", {"ids": [ids["add"]]}))
        a3 = _svg(s, VA, "a3.svg")
        assert "추가" not in a3 and ok(s.run("view.areas", {"id": VA}))["total_m2"] == 80.0
        ok(s.run("history.undo", {}))
        assert _svg(s, VA, "a4.svg") == a2   # 되돌리면 바이트가 돌아온다
        s.close()
        s2 = kit.Session(tmp_path / "p.dobim", create=False)
        try:
            assert _svg(s2, VA, "a5.svg") == a2   # 증분 = 새로 연 그림
        finally:
            s2.close()
    finally:
        try:
            s.close()
        except Exception:
            pass


def test_시트_내보내기_장면_IFC_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        from dobim_core.engine import Engine
        Engine.create(b.ws / "p.dobim", "s2", guid=GUID).close()
        _model(b.run)
        _objects(b.run)
        ok(b.run("view.area_plan", {"id": VA, "name": "2F 법정", "level": L2, "scheme": "legal", "scale": 200}))
        for name in ("a.png", "a.pdf", "a.dxf"):
            _export(b.run, VA, name)
            assert (b.ws / name).stat().st_size > 100
        assert "발코니" in (b.ws / "a.dxf").read_text(encoding="utf-8", errors="replace")
        sc = ok(b.run("view.scene", {"id": VA}))
        assert "발코니" in str(sc) and "119-1-3-나 노대 산입 +3.00㎡" in str(sc)
        ok(b.run("sheet.define", {"id": "9" + f"{54:021d}", "name": "면적 산정도", "views": [VA], "paper": "A3", "sheet_number": "A-010", "date": "2026-01-01"}))
        ok(b.destructive("sheet.render", {"id": "9" + f"{54:021d}", "path": "sh.svg", "overwrite": True}))
        assert "바닥면적 79.00㎡" in (b.ws / "sh.svg").read_text(encoding="utf-8")
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("view.areas", {"id": VA}, project="imp.dobim")["result"] == b.run("view.areas", {"id": VA})["result"]
        assert b.export("t2.ifc", "imp.dobim").read_bytes() == (tmp_path / "t.ifc").read_bytes()
        assert err(b.run("view.area_plan", {"name": "x", "level": L1, "scheme": "net"})) == "SCHEMA_INVALID"
    finally:
        b.close()


def _csv(path):
    raw = path.read_bytes()
    assert raw.startswith("﻿".encode("utf-8")) and b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))


def test_면적표_CSV(tmp_path):
    b = S.Bench(tmp_path)
    try:
        from dobim_core.engine import Engine
        Engine.create(b.ws / "p.dobim", "s2", guid=GUID).close()
        _model(b.run)
        _objects(b.run)
        legal = ok(b.run("area.legal", {"coverage_max": 60, "far_max": 40}))
        r = b.run("area.export", {"path": "lv.csv", "table": "levels"})
        assert err(r) == "CONFIRM_REQUIRED" and not (b.ws / "lv.csv").exists()
        out = ok(b.destructive("area.export", {"path": "lv.csv", "table": "levels"}))
        assert out["levels"] == 4 and out["objects"] == 6 and out["gross_floor_m2"] == legal["gross_floor_m2"] == 280.0 and out["snapshot"] is None
        rows = _csv(b.ws / "lv.csv")
        assert rows[0] == ["레벨", "표고_mm", "층높이_mm", "지하층", "건축면적_산입", "벽중심선_m2", "조정_m2", "바닥면적_m2", "용적률산정_m2"]
        by = {x[0]: x for x in rows[1:]}
        assert by["1F"][7] == "81" and by["1F"][8] == "61" and by["B1"][3] == "TRUE" and by["2F"][7] == "79" and by["RF"][2] == ""
        assert by["합계"][7] == "280" and by["합계"][8] == "140"
        # 요약: 값이 area.legal 과 같다(한도 판정 포함)
        ok(b.destructive("area.export", {"path": "sm.csv", "table": "summary", "coverage_max": 60, "far_max": 40}))
        sm = {x[0]: x for x in _csv(b.ws / "sm.csv")[1:]}
        assert sm["대지면적"][1] == "280" and sm["건축면적"][1] == "70" and sm["연면적"][1] == "280" and sm["용적률 산정용 연면적"][1] == "140"
        assert float(sm["건폐율"][1]) == legal["coverage_pct"] and sm["건폐율 한도"][3] == "적합" and sm["용적률 한도"][3] == "부적합"
        assert sm["확인 필요"][3] in {u["code"] for u in legal["unverified"]} and "건축법 시행령 제119조" in sm["근거 법령"][1]
        # 면적 객체
        ok(b.destructive("area.export", {"path": "ob.csv", "table": "objects"}))
        ob = _csv(b.ws / "ob.csv")
        assert ob[0][:4] == ["id", "이름", "대상", "근거"] and len(ob) == 7
        bal = next(x for x in ob if x[1] == "발코니")
        assert bal[2] == "바닥면적" and bal[5] == "2F" and bal[6] == "12" and bal[7] == "3"
        assert next(x for x in ob if x[1] == "도로 부분")[2:4] == ["대지면적", "119-1-1-나"]
        # all: 세 표를 머리 줄과 빈 줄로 나눈다
        ok(b.destructive("area.export", {"path": "all.csv", "site": SI}))
        al = _csv(b.ws / "all.csv")
        heads = [i for i, x in enumerate(al) if x and x[0].startswith("[")]
        assert [al[i][0] for i in heads] == ["[요약]", "[층별 바닥면적]", "[법정 면적 객체]"] and al[heads[1] - 1] == [] and al[heads[2] + 1][0] == "id"
        # 덮어쓰기: overwrite 없으면 거절, 있으면 스냅숏
        assert err(b.run("area.export", {"path": "all.csv"})) == "FILE_EXISTS"
        r = ok(b.destructive("area.export", {"path": "all.csv", "overwrite": True}))
        assert r["snapshot"]
        assert err(b.run("area.export", {"path": "x.txt"})) == "SCHEMA_INVALID"
        assert err(b.run("area.export", {"path": "../x.csv"})) in ("PATH_DENIED", "PATH_OUTSIDE_WORKSPACE")
    finally:
        b.close()
