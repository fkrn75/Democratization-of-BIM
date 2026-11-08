# SPDX-License-Identifier: MPL-2.0
"""식 언어와 검증기 시험. 음성 시험은 '고장 낸 레시피를 검증기가 잡는가'를 센다(변이 검출률)."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import LIBRARY_IDS, S4, load_lib
from dobim_core.family import expr as E
from dobim_core.family.evaluate import load_recipe
from dobim_core.family.recipe import FamilyError
from dobim_core.family.validate import validate_recipe


# ---------------------------------------------------------------- 식 언어
def ev(src, **env):
    return E.evaluate(E.compile_expr(src), env)


def test_산술_조건_함수():
    assert ev("a*2 + b/4", a=3, b=8) == 8
    assert ev("1 if x > 2 else 0", x=3) == 1
    assert ev("'L' if s == 'L' else 'R'", s="R") == "R"
    assert ev("clamp(x, 1, 5) + min(2, 3) + abs(-1) + ceil(1.2) + floor(1.8) + round(2.5678, 2) + sqrt(16)", x=9) == 5 + 2 + 1 + 2 + 1 + 2.57 + 4
    assert ev("a and not b or c", a=True, b=True, c=True) is True
    assert ev("1 < x <= 3", x=3) is True


@pytest.mark.parametrize("src", [
    "__import__('os').system('x')", "().__class__", "width.real", "x[0]", "[i for i in range(3)]", "lambda: 1",
    "open('f')", "eval('1')", "exec('1')", "(yield)", "f'{x}'", "x := 1", "a if b else c if d else __builtins__",
    "min(*a)", "min(a=1)", "x.y", "{1: 2}", "[1, 2]", "1 + 2j", "None", "b'x'", "min", "_x + 1",
])
def test_허용하지_않는_문법은_컴파일에서_거절한다(src):
    with pytest.raises(E.ExprError):
        E.compile_expr(src)


def test_자원_상한과_수학_오류():
    with pytest.raises(E.ExprError) as ex:
        E.compile_expr("1+" * 200 + "1")
    assert ex.value.code == "E_EXPR_LIMIT"
    for src, code in (("1/0", "E_EXPR_MATH"), ("2**100", "E_EXPR_LIMIT"), ("sqrt(-1)", "E_EXPR_MATH"), ("(-8)**0.5", "E_EXPR_MATH"),
                      ("0**-1", "E_EXPR_MATH"), ("1e308*1e10", "E_EXPR_MATH"), ("'a' + 1", "E_EXPR_TYPE"), ("not 1", "E_EXPR_TYPE"),
                      ("1 < 'a'", "E_EXPR_TYPE"), ("y", "E_EXPR_UNDEFINED"), ("clamp(1, 5, 2)", "E_EXPR_MATH"), ("1 if 2 else 3", "E_EXPR_TYPE")):
        with pytest.raises(E.ExprError) as ex:
            ev(src)
        assert ex.value.code == code, src


def test_식_평가는_eval을_쓰지_않는다():
    src = (Path(E.__file__)).read_text(encoding="utf-8")
    import ast
    names = {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)}
    assert not names & {"eval", "exec", "compile", "__import__", "getattr", "open"}


# ---------------------------------------------------------------- 라이브러리 4종은 전 단계 통과
@pytest.mark.parametrize("fid", LIBRARY_IDS)
def test_라이브러리_레시피는_전_단계_통과(fid):
    r = validate_recipe(load_lib(fid), "full")
    assert r["ok"], r["errors"]
    assert all(v == "pass" for v in r["checks"].values()), r["checks"]
    assert r["stats"]["ifc_validate_errors"] == 0
    assert r["stats"]["meshes_ok"] >= 5


# ---------------------------------------------------------------- 음성 시험(변이 검출)
def _m(fn, code, level="static", path=None):
    return (fn, code, level, path)


def _del(d, *keys):
    for k in keys[:-1]:
        d = d[k]
    del d[keys[-1]]


def _set(d, keys, v):
    for k in keys[:-1]:
        d = d[k]
    d[keys[-1]] = v


DOOR_MUTANTS = {
    "알 수 없는 최상위 키": _m(lambda d: d.update(foo=1), "E_SCHEMA", path="/foo"),
    "ifc 블록 없음": _m(lambda d: _del(d, "ifc"), "E_SCHEMA", path="/ifc"),
    "기본값이 범위 밖": _m(lambda d: _set(d, ("params", "width", "default"), 5000), "E_SCHEMA", path="/params/width"),
    "min > max": _m(lambda d: _set(d, ("params", "width", "min"), 2000), "E_SCHEMA"),
    "열거 기본값이 목록에 없음": _m(lambda d: _set(d, ("params", "swing", "default"), "X"), "E_SCHEMA"),
    "미정의 이름": _m(lambda d: d["derived"].update(bad="width + nothing"), "E_UNDEFINED_NAME", path="/derived/bad"),
    "파생값 순환": _m(lambda d: d["derived"].update(a="b + 1", b="a + 1"), "E_CYCLE"),
    "자기 참조": _m(lambda d: d["derived"].update(a="a + 1"), "E_CYCLE"),
    "코드 실행 시도": _m(lambda d: d["derived"].update(bad="__import__('os')"), "E_EXPR_FUNC"),
    "속성 접근": _m(lambda d: d["derived"].update(bad="width.real"), "E_EXPR_FORBIDDEN"),
    "식 구문 오류": _m(lambda d: d["derived"].update(bad="width +"), "E_EXPR_SYNTAX"),
    "0 으로 나눔": _m(lambda d: d["derived"].update(bad="1/(width - width)"), "E_EXPR_MATH"),
    "타입 불일치 식": _m(lambda d: d["derived"].update(bad="width + 'a'"), "E_EXPR_TYPE"),
    "타입 속성셋이 인스턴스 참조": _m(lambda d: _set(d, ("psets", "Pset_DoorCommon", "props", "IsExternal"), {"expr": "swing == 'L'"}),
                           "E_TYPE_REFERENCES_INSTANCE"),
    "호스트 없이 호스트 문맥": _m(lambda d: _set(d, ("host",), None), "E_HOST_CONTEXT"),
    "이름 중복": _m(lambda d: d["derived"].update(width="1"), "E_NAME_DUPLICATE"),
    "예약 이름": _m(lambda d: d["derived"].update(host_thickness="1"), "E_NAME_RESERVED"),
    "퇴화 돌출 범위": _m(lambda d: _set(d, ("solids", 1, "range"), ["leaf_t", 0]), "E_DEGENERATE"),
    "퇴화 프로파일": _m(lambda d: _set(d, ("solids", 1, "profile", "u"), [10, 10]), "E_DEGENERATE"),
    "좌표 단위 실수": _m(lambda d: _set(d, ("solids", 1, "profile", "u"), [0, 1e9]), "E_GEOMETRY_RANGE"),
    "범위 안 제약 위반": _m(lambda d: _set(d, ("params", "frame_w", "max"), 400), "E_CONSTRAINT"),
    "스윕 법선 평행": _m(lambda d: _set(d, ("solids", 0, "path"), [[0, 0, 0], [0, 100, 0]]), "E_SWEEP_NORMAL"),
    "평면 기호 default 없음": _m(lambda d: d["plan_symbol"].pop("default"), "E_PLAN_DEFAULT"),
    "평면 기호 축척 형식": _m(lambda d: d["plan_symbol"].update({"2:1": []}), "E_PLAN_SCALE"),
    "퇴화 호": _m(lambda d: _set(d, ("plan_symbol", "default", 3, "end"), -5), "E_DEGENERATE"),
    "solid id 중복": _m(lambda d: _set(d, ("solids", 1, "id"), "frame"), "E_SOLID_ID_DUPLICATE"),
    "IFC 클래스 없음": _m(lambda d: _set(d, ("ifc", "class"), "IfcDoorz"), "E_IFC_CLASS", "full"),
    "IFC 요소가 아닌 클래스": _m(lambda d: _set(d, ("ifc", "class"), "IfcPerson"), "E_IFC_CLASS", "full"),
    "PredefinedType 열거 오류": _m(lambda d: _set(d, ("ifc", "predefined"), "PORTAL"), "E_IFC_ENUM", "full"),
    "IFC 속성 이름 오류": _m(lambda d: _set(d, ("ifc", "attrs", "OverallWidht"), {"expr": "width"}), "E_IFC_ATTR_UNKNOWN", "full"),
    "식이 낸 IFC 열거값 오류": _m(lambda d: _set(d, ("ifc", "attrs", "OperationType"), {"expr": "'SWINGY'"}), "E_IFC_ENUM", "full"),
    "IFC 속성 종류 오류": _m(lambda d: _set(d, ("ifc", "attrs", "OverallWidth"), "wide"), "E_IFC_ATTR_TYPE", "full"),
    "금지된 IFC 속성": _m(lambda d: _set(d, ("ifc", "attrs", "GlobalId"), "abc"), "E_IFC_ATTR_FORBIDDEN", "full"),
    "표준이 아닌 Pset_": _m(lambda d: d["psets"].update(Pset_MyStuff={"on": "type", "props": {"A": 1}}), "E_PSET_NOT_STANDARD", "full"),
    "표준 속성 이름 오류": _m(lambda d: _set(d, ("psets", "Pset_DoorCommon", "props", "FireRatng"), "EI30"), "E_PSET_PROP_UNKNOWN", "full"),
    "표준 속성 값 종류 오류": _m(lambda d: _set(d, ("psets", "Pset_DoorCommon", "props", "IsExternal"), "예"), "E_PSET_VALUE_TYPE", "full"),
    "표준 속성 열거값 오류": _m(lambda d: _set(d, ("psets", "Pset_DoorCommon", "props", "Status"), "BOGUS"), "E_PSET_ENUM", "full"),
    "다른 클래스의 표준 Pset": _m(lambda d: d["psets"].update(Pset_WindowCommon={"on": "type", "props": {"IsExternal": True}}), "E_PSET_APPLICABLE", "full"),
}


@pytest.mark.parametrize("name", sorted(DOOR_MUTANTS))
def test_고장난_문_레시피를_검증기가_잡는다(door, name):
    fn, code, level, path = DOOR_MUTANTS[name]
    fn(door)
    r = validate_recipe(door, level)
    assert not r["ok"], f"{name}: 통과해 버렸다"
    codes = {e["code"] for e in r["errors"]}
    assert code in codes, f"{name}: 기대 {code}, 실제 {codes}"
    hit = [e for e in r["errors"] if e["code"] == code]
    assert all("path" in e and e["message"] for e in hit)
    if path:
        assert any(e["path"].startswith(path) for e in hit), (path, [e["path"] for e in hit])


def test_힌트가_있는_오류가_대부분이다(door):
    """에이전트가 고칠 수 있도록 오류에 힌트가 붙는지(전체 변이의 80% 이상)."""
    with_hint = total = 0
    for name, (fn, code, level, path) in DOOR_MUTANTS.items():
        d = copy.deepcopy(door)
        fn(d)
        for e in validate_recipe(d, level)["errors"]:
            if e["code"] == code:
                total += 1
                with_hint += bool(e.get("hint"))
                break
    assert with_hint / total >= 0.8, (with_hint, total)


def test_창_레시피_변이():
    w = load_lib("window.fixed")
    w["solids"][1]["cuts"] = ["nothing"]
    assert "E_CUT_TARGET" in {e["code"] for e in validate_recipe(w, "static")["errors"]}
    w = load_lib("window.fixed")
    w["params"]["mullions"]["max"] = 100
    assert "E_REPEAT_RANGE" in {e["code"] for e in validate_recipe(w, "static")["errors"]}
    w = load_lib("window.fixed")
    w["solids"][0]["when"] = "false"
    r = validate_recipe(w, "static")
    assert "W_SOLID_NEVER_VISIBLE" in {e["code"] for e in r["warnings"]}


def test_붙박이장_USERDEFINED는_ElementType이_필요하다(wardrobe):
    del wardrobe["ifc"]["type_attrs"]["ElementType"]
    r = validate_recipe(wardrobe, "full")
    assert "E_IFC_USERDEFINED" in {e["code"] for e in r["errors"]}


def test_값_검사는_구조화된_오류를_낸다(door):
    r, plan = load_recipe(door)
    from dobim_core.family.evaluate import evaluate
    with pytest.raises(FamilyError) as ex:
        evaluate(r, plan, {"width": 99999}, None)
    i = ex.value.issues[0]
    assert i.code == "E_PARAM_RANGE" and i.path == "/type_values/width" and i.hint
    with pytest.raises(FamilyError) as ex:
        evaluate(r, plan, {"swing": "L"}, None)
    assert ex.value.issues[0].code == "E_WRONG_KIND"
    with pytest.raises(FamilyError) as ex:
        evaluate(r, plan, {}, {"wdith": 1})
    assert ex.value.issues[0].code == "E_UNKNOWN_PARAM" and "width" in (ex.value.issues[0].hint or "")


def test_JSON_구문_오류는_줄과_열을_알려_준다(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"id": "x.y",\n "a": 1,}', encoding="utf-8")
    out = subprocess.run([sys.executable, str(S4 / "validate.py"), str(p)], capture_output=True, timeout=60)
    res = json.loads(out.stdout.decode("utf-8"))
    assert out.returncode == 1 and res["results"][0]["errors"][0]["code"] == "E_JSON"
    assert "줄" in res["results"][0]["errors"][0]["message"]


def test_한_줄_명령은_JSON을_내고_종료코드를_준다():
    lib = Path(load_lib.__globals__["LIB"])
    out = subprocess.run([sys.executable, str(S4 / "validate.py"), str(lib / "column.section.json"), "--level", "static"], capture_output=True, timeout=120)
    res = json.loads(out.stdout.decode("utf-8"))
    assert out.returncode == 0 and res["passed"] == 1 and res["total"] == 1
