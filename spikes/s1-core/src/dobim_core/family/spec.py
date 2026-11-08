# SPDX-License-Identifier: MPL-2.0
"""패밀리 요소(타입·인스턴스)의 투영 명세. 순수 함수다(queries.ifc_spec 이 부른다, ADR-0006).

행 형식 (요소 저장소)
- ftype : params = {name, recipe(레시피 전문 사본), recipe_hash, type_values(완전한 사전)}, family_id = 레시피 id
- finst : params = {name, type(ftype id), values(인스턴스 값, 완전), offset|position, rotation, elevation, width, height}
          관계: of_type(finst→ftype), hosts(wall→finst) 또는 on_level(finst→level)
"""
from __future__ import annotations

from functools import lru_cache

from dobim_core.family import evaluate as ev

_RECIPES: dict[str, dict] = {}


def remember(recipe_hash: str, recipe: dict) -> None:
    if len(_RECIPES) < 512:
        _RECIPES.setdefault(recipe_hash, recipe)


@lru_cache(maxsize=1024)
def _eval(recipe_hash: str, tv_key: str, iv_key: str, host_key: str) -> dict:
    import json
    r, plan = ev.load_recipe(_RECIPES[recipe_hash])
    host = json.loads(host_key)
    return ev.evaluate(r, plan, json.loads(tv_key), json.loads(iv_key), host or None)


def evaluate_cached(recipe: dict, recipe_hash: str, type_values: dict, inst_values: dict | None, host: dict | None) -> dict:
    remember(recipe_hash, recipe)
    return _eval(recipe_hash, ev.canon(type_values), ev.canon(inst_values or {}), ev.canon(host or {}))


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


def finst_spec(tp: dict, ip: dict, type_id: str, host_id: str | None, level_id: str, host: dict | None) -> dict:
    e = finst_eval(tp, ip, host)
    t = ftype_eval(tp)
    r, _ = ev.load_recipe(tp["recipe"])
    return {"kind": "finst", "name": ip["name"], "predefined": r.ifc.predefined, "attrs": e["attrs"], "type": type_id,
            "host": host_id, "level": level_id, "offset": ip.get("offset"), "position": ip.get("position"),
            "rotation": ip.get("rotation", 0.0), "elevation": ip["elevation"], "cut": e["cut"],
            "wall_thickness": host["host_thickness"] if host else None,
            "body": e["body"], "plan": e["plan"]["default"],
            "same_body": e["body_hash"] == t["body_hash"], "same_plan": e["plan_hash"] == t["plan_hash"]}
