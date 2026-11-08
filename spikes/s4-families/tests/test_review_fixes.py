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


# ---------------------------------------------------------------- 5. NaN/Infinity·거대 정수·깊은 중첩은 구조화 오류
def test_json_텍스트의_NaN_Infinity는_E_JSON(tmp_path):
    from dobim_core.family.recipe import FamilyError, load_json_text
    for tok in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(FamilyError) as ei:
            load_json_text('{"a": %s}' % tok)
        assert ei.value.issues[0].code == "E_JSON"


@pytest.mark.parametrize("mut", [
    lambda r: r["params"]["width"].update(default=float("nan")),
    lambda r: r["derived"].update(leaf_w=float("inf")),
    lambda r: r["params"]["width"].update(max=float("inf")),
    lambda r: r["params"]["width"].update(min=float("-inf")),
    lambda r: r["params"]["width"].update(max=10 ** 400),
])
def test_inline_레시피의_비정상_숫자는_FAMILY_INVALID(sess, door, mut):
    mut(door)
    r = sess.run("family.register", {"recipe": door})
    assert not r["ok"] and r["error"]["code"] == "FAMILY_INVALID", r
    assert r["error"]["details"]["issues"][0]["code"] in ("E_SCHEMA", "E_LIMIT")
    v = ok(sess.run("family.validate", {"recipe": door, "level": "static"}))
    assert not v["ok"] and v["checks"]["schema"] == "fail" and v["errors"][0]["code"] in ("E_SCHEMA", "E_LIMIT")
    assert sess.eng.rev == 10


def test_파일_레시피의_NaN과_깊은_중첩은_INTERNAL이_아니다(sess, tmp_path):
    (tmp_path / "nan.json").write_text('{"schema": "dobim.family/1", "x": NaN}', encoding="utf-8")
    (tmp_path / "deep.json").write_text("[" * 5000 + "]" * 5000, encoding="utf-8")
    (tmp_path / "deep2.json").write_text('{"a":' * 5000 + "1" + "}" * 5000, encoding="utf-8")
    (tmp_path / "big.json").write_text('{"x": 1' + "0" * 5000 + "}", encoding="utf-8")   # 파이썬 정수 변환 상한(4300자리) 초과
    for name in ("nan.json", "deep.json", "deep2.json", "big.json"):
        for cmd in ("family.register", "family.validate"):
            r = sess.run(cmd, {"path": name})
            code = r["error"]["code"] if not r["ok"] else r["result"]["errors"][0]["code"]
            assert code != "INTERNAL" and (r["ok"] is True or r["error"]["code"] == "FAMILY_INVALID"), (name, cmd, r)
            assert not r["ok"] or r["result"]["ok"] is False


def test_인라인_깊은_중첩도_구조화_오류(sess, door):
    node = door
    for _ in range(3000):
        node["x"] = {}
        node = node["x"]
    r = sess.run("family.register", {"recipe": door})
    assert r["error"]["code"] == "FAMILY_INVALID" and r["error"]["details"]["issues"][0]["code"] == "E_LIMIT"


def test_거대_매개변수_값은_범위_오류(sess):
    r = sess.run("family.place", {"family": "door.single", "type_values": {"width": 10 ** 18}, "host": F.W3, "offset": 3000})
    assert r["error"]["code"] == "FAMILY_INVALID" and r["error"]["details"]["issues"][0]["code"] == "E_PARAM_RANGE"


# ---------------------------------------------------------------- 6. 같은 id 의 여러 레시피
def _variant(door, version, name):
    r = copy.deepcopy(door)
    r["id"] = "door.multi"
    r["version"] = version
    r["name"] = name
    return r


def test_같은_id_여러_version이면_가장_높은_version으로_배치하고_등록때_경고(sess, door):
    ok(sess.run("family.register", {"recipe": _variant(door, 2, "v2 문")}))
    r1 = sess.run("family.register", {"recipe": _variant(door, 1, "v1 문")})
    assert ok(r1)["created"] and any("door.multi" in w and "version" in w for w in r1["warnings"])
    p = ok(sess.run("family.place", {"family": "door.multi", "host": F.W3, "offset": 1500}))
    assert sess.eng.st.el[p["type"]]["params"]["recipe"]["version"] == 2
    # 같은 레시피를 다시 올리면(created=False) 경고 없이 같은 타입
    again = sess.run("family.register", {"recipe": _variant(door, 2, "v2 문")})
    assert ok(again)["created"] is False and not any("door.multi" in w for w in again["warnings"])


def test_같은_id_같은_version인데_내용이_다르면_배치를_거절(sess, door):
    ok(sess.run("family.register", {"recipe": _variant(door, 3, "A")}))
    ok(sess.run("family.register", {"recipe": _variant(door, 3, "B")}))
    r = sess.run("family.place", {"family": "door.multi", "host": F.W3, "offset": 1500})
    assert r["error"]["code"] == "CONSTRAINT" and "모호" in r["error"]["message"] and r["error"]["hint"]
    # 타입 id 로는 지정할 수 있다
    t = [i for i in sess.eng.st.by_kind["ftype"] if sess.eng.st.el[i]["params"]["recipe"]["name"] == "A"][0]
    ok(sess.run("family.place", {"type": t, "host": F.W3, "offset": 1500}))


def test_라이브러리와_같은_id의_다른_내용을_올리면_경고(sess, door):
    door["name"] = "내 문"
    r = sess.run("family.register", {"recipe": door})
    assert ok(r)["created"] and any("기본 라이브러리" in w for w in r["warnings"])
    clean = sess.run("family.register", {"family": "window.fixed"})
    assert ok(clean)["created"] and not any("기본 라이브러리" in w for w in clean["warnings"])


# ---------------------------------------------------------------- 7. 예시·죽은 코드
def test_family_set_예시가_성공한다(sess):
    from dobim_core import registry
    cmds = registry.load()
    for name in ("family.place", "family.set"):          # 패리티 하네스와 같은 순서(이름순)로 예시를 실행
        for ex in cmds[name].examples:
            r = sess.run(name, ex)
            assert r["ok"], (name, ex, r)
    assert ok(sess.run("model.validate", {}))["errors"] == 0


def test_죽은_코드_정리():
    from dobim_core import commands_family
    assert not hasattr(commands_family, "_check_values_keys")
