# SPDX-License-Identifier: MPL-2.0
"""프로젝트 매개변수(W1 연장, DATA-03): param.define·param.delete·param.list 와 pset.set·pset.set_many 의 종류·형식 검사.

 1. 정의 -> 목록(묶인 수·빈 수) -> 값 쓰기(맞는 형식만, 묶은 종류만) -> 채운 수, 실행 취소
 2. 정의를 바꾸면 이미 쓴 값 중 형식이 어긋난 것을 알린다. 지워도 값은 남는다
 3. IFC 왕복: 정의가 되살고 해시·목록이 같다
 4. 정의가 없으면 이전과 같다(검사 없음)
"""
import sys

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

ZONE = {"pset": "Pset_ProjectCommon", "name": "Zone", "type": "text", "kinds": ["wall", "door"], "desc": "공구 구분"}


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]["code"]


def _item(b, project="p.dobim"):
    items = ok(b.run("param.list", {}, project=project))["items"]
    assert len(items) == 1
    return items[0]


def test_정의_목록_값_쓰기_검사_실행_취소(tmp_path):
    b = S.Bench(tmp_path)
    S.build_authored(b)
    r = ok(b.run("param.define", ZONE))
    assert r["created"] and r["bound"] >= 3 and r["filled"] == 0
    it = _item(b)
    assert it["kinds"] == ["door", "wall"] and it["empty"] == it["bound"] and it["mismatched"] == 0
    # 형식이 다르면 거절, 묶지 않은 종류(레벨)도 거절
    assert err(b.run("pset.set", {"id": F.W1, "pset": "Pset_ProjectCommon", "prop": "Zone", "value": 3})) == "CONSTRAINT"
    assert err(b.run("pset.set", {"id": F.LV1, "pset": "Pset_ProjectCommon", "prop": "Zone", "value": "A"})) == "CONSTRAINT"
    ok(b.run("pset.set", {"id": F.W1, "pset": "Pset_ProjectCommon", "prop": "Zone", "value": "A"}))
    # 여러 요소에 한 번에: 묶은 종류면 통과, 섞이면 전체 실패
    ok(b.run("pset.set_many", {"where": {"kind": "door"}, "pset": "Pset_ProjectCommon", "prop": "Zone", "value": "B"}))
    assert err(b.run("pset.set_many", {"ids": [F.W2, F.LV1], "pset": "Pset_ProjectCommon", "prop": "Zone", "value": "C"})) == "CONSTRAINT"
    it = _item(b)
    assert it["filled"] >= 2 and it["mismatched"] == 0
    assert it["filled"] + it["empty"] == it["bound"]
    # 다른 속성셋·속성은 검사하지 않는다(이전과 같다)
    ok(b.run("pset.set", {"id": F.LV1, "pset": "Pset_ProjectCommon", "prop": "Other", "value": 3}))
    # 실행 취소로 정의 전까지 되돌리면 목록이 빈다
    for _ in range(4):
        ok(b.run("history.undo", {}))
    assert ok(b.run("param.list", {}))["items"] == []


def test_정의를_바꾸면_어긋난_값을_알리고_지워도_값은_남는다(tmp_path):
    b = S.Bench(tmp_path)
    S.build_authored(b)
    ok(b.run("param.define", ZONE))
    ok(b.run("pset.set", {"id": F.W1, "pset": "Pset_ProjectCommon", "prop": "Zone", "value": "A"}))
    r = b.run("param.define", dict(ZONE, type="integer"))
    assert r["ok"] and not r["result"]["created"] and r["result"]["mismatched"] == 1
    assert any("형식" in w for w in r["warnings"])
    assert _item(b)["mismatched_ids"] == [F.W1]
    ok(b.run("pset.set", {"id": F.W1, "pset": "Pset_ProjectCommon", "prop": "Zone", "value": 2}))
    assert err(b.run("param.define", dict(ZONE, kinds=["level", "view"]))) == "SCHEMA_INVALID"
    assert err(b.run("param.define", dict(ZONE, pset="DoBIM_Intent"))) == "CONSTRAINT"
    ok(b.run("param.delete", {"pset": "Pset_ProjectCommon", "name": "Zone"}))
    assert err(b.run("param.delete", {"pset": "Pset_ProjectCommon", "name": "Zone"})) == "NOT_FOUND"
    got = ok(b.run("element.get", {"id": F.W1}))
    assert "Zone" in str(got)                                  # 값은 남는다
    ok(b.run("pset.set", {"id": F.W1, "pset": "Pset_ProjectCommon", "prop": "Zone", "value": "자유"}))   # 정의가 없으면 검사도 없다


def test_IFC_왕복은_프로젝트_매개변수를_되살리고_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    S.build_authored(b)
    ok(b.run("param.define", ZONE))
    ok(b.run("param.define", {"pset": "Company_Common", "name": "Cost", "type": "number", "kinds": ["slab"]}))
    ok(b.run("pset.set", {"id": F.W1, "pset": "Pset_ProjectCommon", "prop": "Zone", "value": "A"}))
    h = b.hashes()
    b.export("t.ifc")
    r = b.import_ifc("t.ifc", "imp.dobim")
    assert r["ok"] and not r["result"]["warnings"], r
    assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    assert b.run("param.list", {}, project="imp.dobim")["result"] == b.run("param.list", {})["result"]
    assert err(b.run("pset.set", {"id": F.W1, "pset": "Pset_ProjectCommon", "prop": "Zone", "value": 1}, project="imp.dobim")) == "CONSTRAINT"
    assert b.export("t2.ifc", "imp.dobim").read_bytes() == (tmp_path / "t.ifc").read_bytes()


def test_목록_쪽_나눔(tmp_path):
    b = S.Bench(tmp_path)
    S.build_authored(b)
    for i in range(12):
        ok(b.run("param.define", {"pset": "Pset_ProjectCommon", "name": f"P{i:02d}", "type": "bool", "kinds": ["wall", "door", "slab"], "desc": "가" * 200}))
    names, cur, pages = [], None, 0
    while True:
        e = b.run("param.list", {"limit": 100, **({"cursor": cur} if cur else {})})
        r = ok(e)
        names += [x["name"] for x in r["items"]]
        pages += 1
        cur = e["page"]["next_cursor"]
        if not cur:
            break
    assert sorted(names) == [f"P{i:02d}" for i in range(12)] and len(names) == 12 and pages > 1
