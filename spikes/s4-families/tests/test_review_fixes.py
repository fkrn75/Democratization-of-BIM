# SPDX-License-Identifier: MPL-2.0
"""독립 검수에서 나온 결함의 회귀 시험(검수 재현 스크립트를 시험으로 옮겼다)."""
import copy
import json
import sqlite3

import pytest

from conftest import load_lib
from dobim_core import fixture as F
from dobim_core.engine import Engine
from dobim_core.family import spec as FS


def ok(r):
    assert r["ok"], r
    return r["result"]


def reopen(sess):
    """세션의 엔진을 닫고 같은 파일을 다시 연다(재오픈 시험)."""
    sess.eng.close()
    sess.eng = Engine(sess.path, "t")
    sess.env.adopt(sess.path, sess.eng)


# ---------------------------------------------------------------- 1. 호스트 형상 변경과 호스팅된 인스턴스 제약
def test_wall_set_두께가_인스턴스_제약을_깨면_거절하고_상태를_안_바꾼다(sess):
    d = ok(sess.run("family.place", {"family": "door.single", "host": F.W3, "offset": 1500}))["id"]
    rev, h = sess.eng.rev, sess.eng.hashes()
    r = sess.run("wall.set", {"id": F.W3, "thickness": 50})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT" and r["error"]["path"] == "/thickness"
    assert d in r["error"]["message"] and r["error"]["details"]["issue"]["code"] == "E_CONSTRAINT"
    assert sess.eng.rev == rev and sess.eng.hashes() == {**h, "rev": rev}
    # 경계: 문짝 두께 + 20 이상이면 허용
    ok(sess.run("wall.set", {"id": F.W3, "thickness": 120}))
    assert ok(sess.run("model.validate", {}))["errors"] == 0
    reopen(sess)
    assert ok(sess.run("model.validate", {}))["errors"] == 0


def test_wall_set_높이가_개구부보다_낮아지면_거절(sess):
    ok(sess.run("family.place", {"family": "door.single", "host": F.W3, "offset": 1500}))
    r = sess.run("wall.set", {"id": F.W3, "height": 2000})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT" and r["error"]["path"] == "/height"
    ok(sess.run("wall.set", {"id": F.W3, "height": 2400}))     # 문 높이 2100 은 들어가는 높이


def test_wall_set_축선이_창_개구부를_벗어나면_거절하고_안전하면_허용(sess):
    ok(sess.run("family.place", {"family": "window.fixed", "host": F.W2, "offset": 3300}))
    r = sess.run("wall.set", {"id": F.W2, "axis": [6000, 0, 6000, 3000]})
    assert r["error"]["code"] == "CONSTRAINT" and r["error"]["path"] == "/axis"
    ok(sess.run("wall.set", {"id": F.W2, "axis": [6000, 0, 6000, 5000]}))
    assert sess.eng.derived_hash() == sess.eng.derived_hash(reference=True)


def test_wall_set_이_여러_호스팅_인스턴스_중_하나만_깨도_거절(sess):
    ok(sess.run("family.place", {"family": "window.fixed", "host": F.W3, "offset": 4500}))
    ok(sess.run("family.place", {"family": "door.single", "host": F.W3, "offset": 1500}))
    assert sess.run("wall.set", {"id": F.W3, "thickness": 50})["error"]["code"] == "CONSTRAINT"


def test_이미_제약을_깬_프로젝트도_열리고_보고하고_고치면_복구된다(sess):
    """구버전이 만든 프로젝트(로그에 이미 깨진 벽 두께가 있음)를 시뮬레이션한다: 열려야 하고, model.validate 가 오류로 보고하며,
    값을 고치면 정상으로 돌아온다. 이전에는 Engine(path) 자체가 FamilyError 로 실패해 프로젝트를 영구히 못 열었다."""
    d = ok(sess.run("family.place", {"family": "door.single", "host": F.W3, "offset": 1500}))["id"]
    sess.eng.close()
    con = sqlite3.connect(sess.path)
    row = con.execute("SELECT params FROM element WHERE id=?", (F.W3,)).fetchone()
    p = json.loads(row[0])
    p["thickness"] = 50.0
    con.execute("UPDATE element SET params=? WHERE id=?", (json.dumps(p), F.W3))
    con.commit()
    con.close()
    sess.eng = Engine(sess.path, "t")          # 예전에는 여기서 예외
    sess.env.adopt(sess.path, sess.eng)
    sess.eng.hashes()                           # 해시·flush 도 예외 없이
    v = ok(sess.run("model.validate", {}))
    assert v["errors"] >= 1 and any(d in x and "E_CONSTRAINT" in x for x in v["violations"])
    ok(sess.run("element.query", {"limit": 5}))
    ok(sess.run("wall.set", {"id": F.W3, "thickness": 250}))    # 고치면 복구
    assert ok(sess.run("model.validate", {}))["errors"] == 0
    assert sess.eng.intent_consistency() == []
    assert sess.eng.derived_hash() == sess.eng.derived_hash(reference=True)


# ---------------------------------------------------------------- 2. 평가 캐시가 포화돼도 영구 실패하지 않는다
def test_레시피_캐시가_가득_차도_새_레시피를_등록하고_평가한다(sess, monkeypatch):
    monkeypatch.setattr(FS, "_EVAL_MAX", 4)
    for i in range(12):        # 상한을 여러 번 넘긴다: 예전에는 512개째부터 KeyError → INTERNAL
        r = copy.deepcopy(load_lib("door.single"))
        r["id"] = f"door.v{i}"
        r["name"] = f"변형 {i}"
        ok(sess.run("family.register", {"recipe": r}))
    ok(sess.run("family.place", {"family": "door.v3", "host": F.W3, "offset": 1500}))
    assert ok(sess.run("model.validate", {}))["errors"] == 0
    assert len(FS._EVAL) <= 4
    reopen(sess)
    assert ok(sess.run("model.validate", {}))["errors"] == 0
    assert sess.eng.hashes() == sess.eng.hashes()


def test_family_validate_가_평가_캐시_슬롯을_소모하지_않는다(sess):
    n = len(FS._EVAL)
    for i in range(6):
        r = copy.deepcopy(load_lib("door.single"))
        r["id"] = f"door.tmp{i}"
        ok(sess.run("family.validate", {"recipe": r, "level": "static"}))
    assert len(FS._EVAL) == n          # validate 는 요소 저장소 평가 캐시에 쓰지 않는다
    r = copy.deepcopy(load_lib("door.single"))
    r["id"] = "door.after"
    ok(sess.run("family.register", {"recipe": r}))
