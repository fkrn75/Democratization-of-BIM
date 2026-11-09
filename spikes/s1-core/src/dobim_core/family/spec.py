# SPDX-License-Identifier: MPL-2.0
"""패밀리 요소(타입·인스턴스)의 투영 명세. 순수 함수다(queries.ifc_spec 이 부른다, ADR-0006).

행 형식 (요소 저장소)
- ftype : params = {name, recipe(레시피 전문 사본), recipe_hash, type_values(완전한 사전)}, family_id = 레시피 id
- finst : params = {name, type(ftype id), values(인스턴스 값, 완전), offset|position, rotation, elevation, width, height}
          관계: of_type(finst→ftype), hosts(wall→finst) 또는 on_level(finst→level)
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict

from dobim_core.family import evaluate as ev
from dobim_core.family.recipe import FamilyError

# 평가 결과 캐시: 정확한 LRU(가장 오래 안 쓴 것부터 버린다). 레시피 본문은 호출마다 인자로 받으므로
# 캐시가 가득 차도 조회가 실패하지 않는다(항목 수만 제한한다).
# 이전 구현은 레시피 사전 _RECIPES 가 512개에서 포화되면 새 레시피를 저장하지 않아 _eval 이 KeyError 로 영구 실패했다.
_EVAL_MAX = 1024
_EVAL: OrderedDict[tuple, dict] = OrderedDict()
_LOCK = threading.Lock()


def _eval(recipe: dict, recipe_hash: str, tv_key: str, iv_key: str, host_key: str) -> dict:
    key = (recipe_hash, tv_key, iv_key, host_key)
    with _LOCK:
        hit = _EVAL.get(key)
        if hit is not None:
            _EVAL.move_to_end(key)
            return hit
    r, plan = ev.load_recipe(recipe)
    host = json.loads(host_key)
    out = ev.evaluate(r, plan, json.loads(tv_key), json.loads(iv_key), host or None)
    with _LOCK:
        _EVAL[key] = out
        _EVAL.move_to_end(key)
        while len(_EVAL) > _EVAL_MAX:
            _EVAL.popitem(last=False)
    return out


def evaluate_cached(recipe: dict, recipe_hash: str, type_values: dict, inst_values: dict | None, host: dict | None) -> dict:
    return _eval(recipe, recipe_hash, ev.canon(type_values), ev.canon(inst_values or {}), ev.canon(host or {}))


def recipe_hash_of(recipe: dict) -> str:
    return ev.digest(recipe)


def ftype_eval(tp: dict) -> dict:
    """타입 수준 평가: 인스턴스 매개변수는 기본값, 호스트 문맥은 기본(두께 200, 높이 3000)."""
    return evaluate_cached(tp["recipe"], tp["recipe_hash"], tp["type_values"], None, None)


def ftype_spec(tp: dict) -> dict:
    e = ftype_eval(tp)
    r, _ = ev.load_recipe(tp["recipe"])
    return {"kind": "ftype", "name": tp["name"], "predefined": r.ifc.predefined, "attrs": e["type_attrs"],
            "body": e["body"], "plan": e["plan"]["default"]}


def finst_eval(tp: dict, ip: dict, host: dict | None) -> dict:
    return evaluate_cached(tp["recipe"], tp["recipe_hash"], tp["type_values"], ip["values"], host)


def finst_spec(tp: dict, ip: dict, type_id: str, host_id: str | None, level_id: str, host: dict | None,
               tolerant: bool = False) -> dict:
    """인스턴스 투영 명세. tolerant=True 면 평가 실패(FamilyError)를 던지지 않고 오류 상태 명세를 돌려준다.

    오류 상태 명세는 형상(body/plan)이 비고 개구부가 없으며 `error` 에 원인이 들어 있다. 레시피 제약을 깨는 값이 로그에
    이미 들어 있는 프로젝트(구버전이 만든 것)도 열려서 model.validate 가 보고하고, 값을 고치면 정상 상태로 돌아온다.
    """
    try:
        e = finst_eval(tp, ip, host)
        t = ftype_eval(tp)
        r, _ = ev.load_recipe(tp["recipe"])
    except FamilyError as ex:
        if not tolerant:
            raise
        first = ex.issues[0]
        try:
            predefined = ev.load_recipe(tp["recipe"])[0].ifc.predefined
        except FamilyError:
            predefined = "NOTDEFINED"
        return {"kind": "finst", "name": ip["name"], "predefined": predefined, "attrs": {}, "type": type_id,
                "host": host_id, "level": level_id, "offset": ip.get("offset"), "position": ip.get("position"),
                "rotation": ip.get("rotation", 0.0), "elevation": ip["elevation"], "cut": None,
                "wall_thickness": host["host_thickness"] if host else None, "body": [], "plan": [],
                "same_body": False, "same_plan": False,
                "error": {"code": first.code, "path": first.path, "message": first.message[:300]}}
    return {"kind": "finst", "name": ip["name"], "predefined": r.ifc.predefined, "attrs": e["attrs"], "type": type_id,
            "host": host_id, "level": level_id, "offset": ip.get("offset"), "position": ip.get("position"),
            "rotation": ip.get("rotation", 0.0), "elevation": ip["elevation"], "cut": e["cut"],
            "wall_thickness": host["host_thickness"] if host else None,
            "body": e["body"], "plan": e["plan"]["default"],
            "same_body": e["body_hash"] == t["body_hash"], "same_plan": e["plan_hash"] == t["plan_hash"]}


def inst_params(name, tid, values, ev, hosted, offset, position, rotation, elevation) -> dict:
    """인스턴스 행의 params. family.place/family.set 이 쓰는 것이 정본이고, 가져오기가 의도를 복원할 때 같은 식으로 다시 계산해 대조한다
    (width/height 는 평가한 개구부에서 나오는 파생값이라 의도 텍스트의 값이 평가와 다르면 손상으로 본다)."""
    p: dict = {"name": name, "type": tid, "values": values, "rotation": rotation, "elevation": elevation}
    if hosted:
        cut = ev["cut"]
        p["offset"] = offset
        p["width"] = (cut["x"][1] - cut["x"][0]) if cut else 0.0
        p["height"] = (cut["z"][1] - cut["z"][0]) if cut else 0.0
    else:
        p["position"] = list(position)
    return p
