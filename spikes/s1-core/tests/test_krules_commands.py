# SPDX-License-Identifier: MPL-2.0
"""한국 납품 규칙 레지스트리 커맨드(rules.list, rules.check) 시험.

판정 자체는 test_krules_*.py(S9 시험 201개를 옮긴 것)가 본다. 여기서는 커맨드 층: 결과가 실행기와 같은지, 응답 크기 상한·쪽 나눔,
위반이면 CLI 종료 코드 3, 사용자 IDS·납품 폴더·오류 봉투를 본다.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dobim_core import dispatch, limits, registry
from dobim_core.errors import exit_code_of
from dobim_core.krules import runner

SPIKES = Path(__file__).resolve().parent.parent.parent
RA01 = SPIKES / "s0-baseline" / "RA-01.ifc"
S9_RESULTS = SPIKES / "s9-krules" / "results"


@pytest.fixture
def ws(tmp_path):
    shutil.copyfile(RA01, tmp_path / "RA-01.ifc")
    return tmp_path


def _run(ws, name, args):
    env = dispatch.Env(ws)
    r = dispatch.run(env, name, args)
    assert len(json.dumps(r, ensure_ascii=False).encode("utf-8")) <= limits.RESPONSE_CAP_BYTES
    return r


def _all_items(ws, name, args):
    """쪽을 끝까지 넘겨 모든 행을 모은다."""
    rows, cur = [], None
    for _ in range(50):
        r = _run(ws, name, {**args, **({"cursor": cur} if cur else {})})
        assert r["ok"], r
        rows += r["result"]["items"]
        cur = r["page"]["next_cursor"]
        if not cur:
            return r, rows
    raise AssertionError("쪽 넘김이 끝나지 않음")


def test_커맨드가_등록돼_있고_모델_없이_읽기만_한다():
    reg = registry.load()
    for name in ("rules.list", "rules.check"):
        c = reg[name]
        assert c.kind == "query" and c.risk == "read" and not c.needs_project and c.idempotent and c.group == "rules"


def test_규칙_목록은_37개이고_세움터는_세움터_규칙_15개를_더한다(ws):
    r, rows = _all_items(ws, "rules.list", {})
    assert len(rows) == 37 and r["result"]["counts"] == {"ids": 18, "code": 19}
    assert sum(1 for x in rows if x["mode"] == "ids") == 18 and all(x["title"] for x in rows)
    r, rows = _all_items(ws, "rules.list", {"profile": "seumteo"})
    seu = [x for x in rows if x["scope"] == "seumteo"]
    assert len(seu) == 15 and "draft_warning" in r["result"]
    judged = [x for x in rows if x["in_profile"]]
    assert len(judged) == 12 + 15   # 공통 12 + 세움터 15
    assert {x["id"] for x in rows if not x["in_profile"]} == {x["id"] for x in rows if x["scope"] == "procurement"}


@pytest.mark.parametrize("profile,committed", [("procurement", "RA-01.json"), ("seumteo", "RA-01.seumteo.json")])
def test_검사_결과는_S9_기준_결과와_같다(ws, profile, committed):
    want = json.loads((S9_RESULTS / committed).read_text(encoding="utf-8"))
    r, rows = _all_items(ws, "rules.check", {"path": "RA-01.ifc", "profile": profile})
    res = r["result"]
    assert res["summary"] == want["summary"] and res["rules"] == len(want["checks"])
    assert [(x["id"], x["status"], x["applicable"], x["failed"]) for x in rows] == \
        [(c["id"], c["status"], c["applicable"], c["failed"]) for c in want["checks"]]
    assert res["violations"] == [c["id"] for c in want["checks"] if c["status"] == "fail"]
    assert exit_code_of(r) == 3   # 위반이 있으면 CLI 종료 코드 3
    assert ("draft_warning" in res) == (profile == "seumteo")


def test_자세히_보기는_사례를_잘라_싣고_쪽을_나눈다(ws):
    want = {c["id"]: c for c in json.loads((S9_RESULTS / "RA-01.energy.json").read_text(encoding="utf-8"))["checks"]}
    r, rows = _all_items(ws, "rules.check", {"path": "RA-01.ifc", "energy": True, "detail": True})
    assert r["result"]["energy_mode"] is True and len(rows) == len(want)
    for x in rows:
        w = want[x["id"]]
        assert x["title"] == w["title"] and x["severity"] == w["severity"] and len(x["examples"]) == min(3, len(w["examples"]))
        for ex, wex in zip(x["examples"], w["examples"]):
            assert ex["reason"] == limits.clip_chars(wex["reason"], 160)


def test_규칙을_골라_보면_위반과_종료_코드도_그_규칙_기준이다(ws):
    r = _run(ws, "rules.check", {"path": "RA-01.ifc", "rules": ["KR-IDS-01", "KR-CODE-0"]})
    assert r["ok"] is False and r["error"]["code"] == "NOT_FOUND" and r["error"]["path"] == "/rules/1"   # 접두는 '-' 로 끝낸다
    r = _run(ws, "rules.check", {"path": "RA-01.ifc", "rules": ["KR-IDS-01", "KR-IDS-02"]})
    assert [x["id"] for x in r["result"]["items"]] == ["KR-IDS-01", "KR-IDS-02"]
    assert r["result"]["violations"] == [] and exit_code_of(r) == 0
    r, rows = _all_items(ws, "rules.check", {"path": "RA-01.ifc", "rules": ["KR-CODE-"]})
    assert rows and all(x["id"].startswith("KR-CODE-") for x in rows) and set(r["result"]["violations"]) <= {x["id"] for x in rows}


def test_사용자_IDS_와_지원하지_않는_IDS(ws):
    shutil.copyfile(runner.IDS_PATH, ws / "mine.ids")
    r = _run(ws, "rules.check", {"path": "RA-01.ifc", "ids": "mine.ids", "rules": ["KR-IDS-"]})
    assert r["ok"], r
    d = _run(ws, "rules.check", {"path": "RA-01.ifc", "rules": ["KR-IDS-"]})
    # 사용자 IDS 는 한국어 사용자 속성 OR 판정을 쓰지 않는다: 엄격 슬롯 결과(KR-IDS-13 사업명은 IfcProject.Name 만)
    assert {x["id"] for x in r["result"]["items"]} == {x["id"] for x in d["result"]["items"]}
    text = runner.IDS_PATH.read_text(encoding="utf-8").replace(
        "<requirements />", '<requirements><partOf relation="IFCRELAGGREGATES"><entity><name><simpleValue>IFCBUILDING</simpleValue></name></entity></partOf></requirements>', 1)
    (ws / "partof.ids").write_text(text, encoding="utf-8")
    r = _run(ws, "rules.check", {"path": "RA-01.ifc", "ids": "partof.ids"})
    assert r["error"]["code"] == "UNSUPPORTED" and r["error"]["path"] == "/ids"
    (ws / "bad.ids").write_text("<ids>", encoding="utf-8")
    assert _run(ws, "rules.check", {"path": "RA-01.ifc", "ids": "bad.ids"})["error"]["code"] == "IMPORT_INVALID"
    assert _run(ws, "rules.check", {"path": "RA-01.ifc", "ids": "mine.ids", "profile": "seumteo"})["error"]["code"] == "USAGE"


def test_납품_폴더만_검사(ws):
    pkg = ws / "납품"
    (pkg / "IFC").mkdir(parents=True)
    r = _run(ws, "rules.check", {"package": "납품"})
    assert r["ok"] and r["result"]["file"] is None and r["result"]["package"] == "납품"
    assert [x["id"] for x in r["result"]["items"]] == ["KR-CODE-18"]
    r = _run(ws, "rules.check", {"package": "납품", "profile": "seumteo"})
    assert [x["id"] for x in r["result"]["items"]] == ["KR-SEU-02"]


def test_오류_봉투(ws):
    assert _run(ws, "rules.check", {})["error"]["code"] == "USAGE"
    assert _run(ws, "rules.check", {"path": "없음.ifc"})["error"]["code"] == "NOT_FOUND"
    assert _run(ws, "rules.check", {"path": "../밖.ifc"})["error"]["code"] == "PATH_DENIED"
    (ws / "x.txt").write_text("x", encoding="utf-8")
    assert _run(ws, "rules.check", {"path": "x.txt"})["error"]["code"] == "PATH_DENIED"
    (ws / "빈.ifc").write_bytes(b"")
    e = _run(ws, "rules.check", {"path": "빈.ifc"})["error"]
    assert e["code"] == "IMPORT_INVALID" and e["details"]["input_error"] == "EMPTY_FILE"
    assert _run(ws, "rules.check", {"path": "RA-01.ifc", "profile": "seoul"})["error"]["code"] == "SCHEMA_INVALID"
    assert _run(ws, "rules.check", {"path": "RA-01.ifc", "cursor": limits.enc_cursor(999)})["error"]["code"] == "SCHEMA_INVALID"
