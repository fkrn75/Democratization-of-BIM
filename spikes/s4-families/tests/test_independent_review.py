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


# ---------------------------------------------------------------- 3. 시간·자원 상한
BASE_RECIPE = {"schema": "dobim.family/1", "id": "x.t", "version": 1, "name": "t", "ifc": {"class": "IfcFurniture"},
               "params": {"a": {"kind": "type", "default": 500, "min": 100, "max": 900}}, "solids": [],
               "plan_symbol": {"default": [{"type": "line", "from": [0, 0], "to": [1, 1]}]}}
BOX = {"id": "box", "op": "extrude", "plane": "xy", "profile": {"shape": "rect", "u": [0, "a"], "v": [0, "a"]}, "range": [0, "a"]}


def _recipe(solids, **kw):
    r = copy.deepcopy(BASE_RECIPE)
    r["solids"] = solids
    r.update(kw)
    return r


def _void(vid, u, v, rng=(-10, 2000), **kw):
    return {"id": vid, "role": "void", "cuts": ["box"], "op": "extrude", "plane": "xy",
            "profile": {"shape": "rect", "u": u, "v": v}, "range": list(rng), **kw}


def test_solid당_공극_수_상한(sess):
    from dobim_core.family.evaluate import MAX_CUTS_PER_SOLID
    many = _void("v", ["k*10", "k*10+5"], [0, 5], repeat={"var": "k", "count": MAX_CUTS_PER_SOLID + 1})
    r = _recipe([BOX, many])
    t0 = __import__("time").perf_counter()
    res = ok(sess.run("family.validate", {"recipe": r, "level": "static"}))
    assert not res["ok"] and any(e["code"] == "E_LIMIT" and "공극" in e["message"] for e in res["errors"])
    assert __import__("time").perf_counter() - t0 < 5
    ok_n = _recipe([BOX, _void("v", ["k*10", "k*10+5"], [0, 5], repeat={"var": "k", "count": MAX_CUTS_PER_SOLID})])
    assert ok(sess.run("family.validate", {"recipe": ok_n, "level": "static"}))["ok"]


def _heavy_recipe():
    import math
    expr = "+".join(["1"] * 33)
    pts = [[f"{round(100 * math.cos(2 * math.pi * i / 32))}+0*({expr})", f"{round(100 * math.sin(2 * math.pi * i / 32))}+0*({expr})"] for i in range(32)]
    sol = [{"id": f"s{i}", "op": "extrude", "plane": "xy", "repeat": {"var": "k", "count": 50},
            "profile": {"shape": "polygon", "points": pts}, "range": [f"0*({expr})", f"10+0*({expr})"]} for i in range(8)]
    return _recipe(sol)


def test_식_평가_예산이_폭주하는_레시피를_빠르게_거절한다(sess):
    """검수 재현: 42KB 레시피는 평가 1회에 1.7초, 표본 120개면 3분 넘게 걸렸다. 이제 첫 평가에서 E_LIMIT."""
    import time
    r = _heavy_recipe()
    t0 = time.perf_counter()
    res = ok(sess.run("family.validate", {"recipe": r, "level": "full"}))
    dt = time.perf_counter() - t0
    assert not res["ok"] and any(e["code"] == "E_LIMIT" and "예산" in e["message"] for e in res["errors"]), res["errors"][:2]
    assert dt < 10, dt
    reg = sess.run("family.register", {"recipe": r})
    assert reg["error"]["code"] == "FAMILY_INVALID" and reg["error"]["details"]["issues"][0]["code"] == "E_LIMIT"


def test_검증_전체_시간_예산(door):
    import time
    from dobim_core.family.validate import validate_recipe
    t0 = time.perf_counter()
    res = validate_recipe(door, "full", time_budget=0.0)
    assert not res["ok"] and res["errors"][-1]["code"] == "E_LIMIT" and "시간 예산" in res["errors"][-1]["message"]
    assert time.perf_counter() - t0 < 3
    # 표본 평가는 통과하지만 IFC 투영·메시 단계에서 예산이 바닥나는 경우도 멈춘다
    t0 = time.perf_counter()
    res = validate_recipe(door, "full", time_budget=1.0)
    assert not res["ok"] and any(e["code"] == "E_LIMIT" for e in res["errors"]), res["checks"]
    assert time.perf_counter() - t0 < 20
    assert validate_recipe(door, "static")["ok"]      # 기본 예산 안에서는 정상


# ---------------------------------------------------------------- 4. 공극이 solid 와 안 만나거나 전부 지우는 경우
def _codes(r, level="full"):
    from dobim_core.family.validate import validate_recipe
    res = validate_recipe(r, level)
    return res, sorted({e["code"] for e in res["errors"]})


def test_공극이_solid를_빗나가면_정적_단계에서_E_VOID_MISS():
    res, codes = _codes(_recipe([BOX, _void("v", [5000, 6000], [5000, 6000])]), "static")
    assert "E_VOID_MISS" in codes and res["checks"]["geometry"] == "fail"
    e = next(e for e in res["errors"] if e["code"] == "E_VOID_MISS")
    assert e["path"] == "/solids/1" and e["hint"]


def test_공극이_solid를_전부_지우면_E_SHAPE_VOLUME():
    res, codes = _codes(_recipe([BOX, _void("v", [-10, 2000], [-10, 2000])]))
    assert "E_SHAPE_VOLUME" in codes and res["checks"]["geometry_shape"] == "fail", res["errors"]
    assert "부피가 줄지 않는다" in next(e for e in res["errors"] if e["code"] == "E_SHAPE_VOLUME")["message"]


def test_정상_공극과_일부만_빗나가는_반복_공극은_통과():
    half = _recipe([BOX, _void("v", ["a/2", 2000], [-10, 2000])])             # 절반만 절단
    res, codes = _codes(half)
    assert res["ok"], res["errors"]
    holes = _recipe([BOX, _void("v", ["k*300", "k*300+50"], [100, 150], repeat={"var": "k", "count": 4})])   # a=100 에서는 k>=1 이 빗나간다
    res, codes = _codes(holes, "static")
    assert "E_VOID_MISS" not in codes and res["ok"], res["errors"]


# ---------------------------------------------------------------- 7. SPEC 에 적은 식 동작(반올림·거듭제곱)이 실제와 같다
def test_round는_은행가_반올림이고_정수_거듭제곱은_곱셈과_같다():
    from dobim_core.family import expr as E

    def ev(src, **env):
        return E.evaluate(E.compile_expr(src), env)
    assert [ev("round(x)", x=v) for v in (0.5, 1.5, 2.5, -0.5, -1.5)] == [0, 2, 2, 0, -2]
    assert ev("round(x, 2)", x=1.005) == 1.0
    x = 1.1
    assert ev("x**3", x=x) == x * x * x and ev("x**-2", x=x) == 1.0 / (x * x) and ev("x**0", x=x) == 1.0
    assert ev("2**8") == 256 and isinstance(ev("2**8"), int)
    assert ev("x**8", x=1.5) == 1.5 ** 8
    assert ev("x**0.5", x=4.0) == 2.0          # 비정수 지수는 libm pow


def test_family_place_예시는_SPEC_의_FAMILY_INVALID_형식을_따른다(sess):
    r = sess.run("family.place", {"family": "door.single", "values": {"swing": "X"}, "host": F.W3, "offset": 1500})
    e = r["error"]
    assert e["code"] == "FAMILY_INVALID" and e["path"] and e["hint"]
    assert e["details"]["total"] >= 1 and set(e["details"]["issues"][0]) >= {"level", "code", "path", "message"}


def test_패리티_하네스의_family_set_준비_단계는_place_예시와_같다():
    import parity
    from dobim_core import registry
    pre_name, pre_args = parity.PRE["family.set"][0]
    assert pre_name == "family.place" and pre_args == registry.load()["family.place"].examples[0]
    assert registry.load()["family.set"].examples[0]["id"] == pre_args["id"]
