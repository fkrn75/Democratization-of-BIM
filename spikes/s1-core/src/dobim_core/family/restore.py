# SPDX-License-Identifier: MPL-2.0
"""가져오기의 패밀리 의도 복원 (ADR-0014): IFC 의 DoBIM_Intent 텍스트에서 패밀리 타입(ftype)·인스턴스(finst) 행을 되살린다.

순수 모듈이다(ifcopenshell·파일·시각·난수 없음). 파일에서 읽은 텍스트는 외부 입력이라 믿지 않는다:
- 의도 모양(키 집합), 파라미터 모양(kinds.row_params_error: 병합이 로그를 거르는 것과 같은 검사), 레시피(evaluate.load_recipe 의 구조 검사·정적 분석),
  레시피 해시, 타입 값·인스턴스 값이 "완전하고 검사를 통과한 사전"인지(resolve_values 와 같아야 한다), 인스턴스의 파생값(width/height)이 평가와 같은지를
  모두 맞춰 본다. 하나라도 어긋나면 ValueError 를 던지고, 호출자(foreign.plan_import)는 그 요소를 외래로 둔다(안전한 쪽).
- 평가는 S4 의 규칙(evaluate: 평가 오류·cut·elevation, 식 평가 예산)을 그대로 쓴다. 여기서 평가가 실패해도 예외는 FamilyError 이고 ValueError 로 바꿔 올린다.
- 의도 텍스트는 크기 상한이 있다(레시피 64KB + 값). 상한을 넘으면 파싱하기 전에 거절한다.
"""
from __future__ import annotations

import json

from dobim_core import kinds as K
from dobim_core.family import evaluate as FE
from dobim_core.family import spec as FS
from dobim_core.family.recipe import FamilyError, load_json_text

MAX_INTENT_CHARS = 160 * 1024   # 의도 텍스트 상한: 레시피(64KB)에 타입·인스턴스 값을 더한 정상 크기의 몇 배
MAX_MM = 1e9                    # 위치 계열 숫자(offset·elevation·rotation·position)의 상한. 벽·문 의도와 같다(제곱하는 계산이 넘치지 않게)


def load_intent(text: str) -> dict:
    """의도 텍스트를 JSON 객체로 읽는다. 크기 상한·NaN/Infinity·거대 정수·깊은 중첩을 거절한다(ValueError)."""
    if not isinstance(text, str) or len(text) > MAX_INTENT_CHARS:
        raise ValueError("의도 텍스트가 없거나 너무 크다")
    try:
        d = load_json_text(text)
    except FamilyError as ex:
        raise ValueError(f"의도 JSON 을 읽을 수 없다: {ex.issues[0].message[:80]}") from None
    return d


def claimed_kind(text: str) -> str | None:
    """의도가 스스로 밝힌 종류(없거나 읽을 수 없으면 None). 크기 상한을 넘거나 JSON 이 아니면 None."""
    try:
        d = load_intent(text)
    except ValueError:
        return None
    k = d.get("kind")
    return k if isinstance(k, str) else None


def _finite_pos(v, name) -> float | int:
    if not K.num_ok(v) or abs(v) > MAX_MM:
        raise ValueError(f"{name} 값이 범위(±1e9)를 벗어났거나 숫자가 아니다")
    return v


def parse_ftype(d: dict) -> dict:
    """타입 의도 -> {"kind", "params", "family_id", "ifc_class"}. 못 읽으면 ValueError.
    params 는 파일에 적힌 값 그대로다(JSON 이 int/float 을 보존하므로 저작 행과 같은 모양이다)."""
    if set(d) != {"kind", "params", "family_id"} or d["kind"] != "ftype":
        raise ValueError("타입 의도 모양이 현재 스키마와 다르다")
    p = d["params"]
    if (err := K.row_params_error("ftype", p)) is not None:
        raise ValueError(err)
    try:
        r, _ = FE.load_recipe(p["recipe"])
        if p["recipe_hash"] != FS.recipe_hash_of(p["recipe"]):
            raise ValueError("레시피 해시가 레시피와 다르다")
        if d["family_id"] != r.id:
            raise ValueError("패밀리 id 가 레시피의 id 와 다르다")
        tv, _ = FE.resolve_values(r, p["type_values"], None)
        if FE.canon(tv) != FE.canon(p["type_values"]):
            raise ValueError("타입 값이 완전한 검사 통과 사전이 아니다")
        FS.ftype_spec(p)   # 평가가 되는 타입인가(투영 명세를 만든다: 실패하면 FamilyError)
    except FamilyError as ex:
        raise ValueError(f"패밀리 레시피·값을 평가할 수 없다: {ex.issues[0].message[:80]}") from None
    return {"kind": "ftype", "params": p, "family_id": r.id, "ifc_class": r.ifc.type_class or (r.ifc.class_ + "Type")}


def parse_finst(d: dict) -> dict:
    """인스턴스 의도의 모양만 읽는다 -> {"kind", "params", "family_id", "type", "host"|"level"}. 타입과의 일치·평가는 check_finst 가 한다."""
    keys = set(d) - {"host", "level"}
    if keys != {"kind", "params", "family_id", "type"} or d["kind"] != "finst" or ("host" in d) == ("level" in d):
        raise ValueError("인스턴스 의도 모양이 현재 스키마와 다르다")
    p = d["params"]
    if (err := K.row_params_error("finst", p)) is not None:
        raise ValueError(err)
    if p["type"] != d["type"] or not K.id_ok(d["type"]) or not K.id_ok(d.get("host") or d.get("level")) or not isinstance(d["family_id"], str):
        raise ValueError("인스턴스의 타입·호스트·레벨 참조가 올바르지 않다")
    for k in ("rotation", "elevation", "offset"):
        if k in p:
            _finite_pos(p[k], k)
    if "position" in p:
        for v in p["position"]:
            _finite_pos(v, "position")
    out = {"kind": "finst", "params": p, "family_id": d["family_id"], "type": d["type"]}
    out["host" if "host" in d else "level"] = d.get("host") or d["level"]
    return out


def check_finst(it: dict, type_row: dict, host_params: dict | None) -> str:
    """인스턴스 의도를 그 타입(행)과 호스트 벽(params)으로 평가해 일관성을 확인한다. 돌려주는 값은 인스턴스의 IFC 클래스. 못 맞추면 ValueError.
    확인하는 것: 패밀리 id 가 타입과 같다 / 호스팅 여부가 레시피와 같다 / 인스턴스 값이 완전한 검사 통과 사전이다 / 평가가 성공한다 /
    width·height 같은 파생값이 평가(cut)와 같다(family.place 가 저장하는 모양과 같은 행이어야 한다)."""
    p = it["params"]
    tp = type_row["params"]
    try:
        r, _ = FE.load_recipe(tp["recipe"])
        if it["family_id"] != type_row["family_id"] or it["family_id"] != r.id:
            raise ValueError("인스턴스의 패밀리 id 가 타입과 다르다")
        hosted = "host" in it
        if hosted != (r.host is not None) or hosted != ("offset" in p):
            raise ValueError("호스팅 여부가 레시피·배치 필드와 다르다")
        _, iv = FE.resolve_values(r, tp["type_values"], p["values"])
        if FE.canon(iv) != FE.canon(p["values"]):
            raise ValueError("인스턴스 값이 완전한 검사 통과 사전이 아니다")
        host_ctx = None
        if hosted:
            if host_params is None:
                raise ValueError("호스트 벽 정보가 없다")
            host_ctx = {"host_thickness": host_params["thickness"], "host_height": host_params["height"]}
        ev = FS.finst_eval(tp, {"values": iv}, host_ctx)
    except FamilyError as ex:
        raise ValueError(f"인스턴스를 평가할 수 없다: {ex.issues[0].message[:80]}") from None
    want = FS.inst_params(p["name"], it["type"], iv, ev, hosted, p.get("offset"), p.get("position"), p["rotation"], p["elevation"])
    if json.dumps(want, sort_keys=True) != json.dumps(p, sort_keys=True):
        raise ValueError("인스턴스의 파생값(width·height)이 평가와 다르다")
    return r.ifc.class_
