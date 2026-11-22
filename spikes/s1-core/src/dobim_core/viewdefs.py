# SPDX-License-Identifier: MPL-2.0
"""뷰·주석(치수·태그) 정의의 IFC 왕복 (ADR-0015): 정의를 IFC 프로젝트의 속성셋 한 개에 싣고, 가져올 때 검증해 되살린다.

순수 모듈이다(ifcopenshell·파일·시각·난수 없음). 뷰·치수·태그는 IFC 신원이 없는 의도 요소다(kinds.NON_IFC_KINDS: 도면 그림은 항상 의도에서 파생한다).
그래서 요소 단위 투영 클로저로 변조를 가릴 수 없다. 대신 정의 하나를 속성 하나로 싣고(이름 = 요소 id), 값에 **정의 자체의 해시**(check)를 함께 적는다.
 - 값(텍스트 JSON) = {"kind", "family_id", "params", "check"}. check = sha256(canon({id, kind, params})). id 를 넣어 다른 id 밑으로 옮겨 붙인 정의를 거른다.
 - 가져올 때 믿는 순서: 크기 상한·NaN/Infinity·거대 정수·깊은 중첩 거절 → 키 집합 → 종류·family_id → 파라미터 모양(kinds.row_params_error, 병합이 로그를 거르는
   것과 같은 검사) → 값 범위·타입(커맨드가 만들 수 있는 것만) → 참조 문법 → check 일치. 하나라도 어긋나면 ValueError 이고 호출자는 그 정의를 되살리지 않는다
   (정의는 파일에 그대로 남는다: 되살리지 않은 정의의 속성은 내보낼 때도 건드리지 않는다).
 - check 는 변조 **탐지**다(외부 도구가 값 일부만 고치면 어긋난다). 인증이 아니다: 정의 전체를 올바른 해시와 함께 다시 쓰는 쪽은 막지 못한다. 그런 정의도
   모든 검증(모양·범위·참조)을 통과해야만 되살려지고, 되살려진 뒤에는 커맨드로 만든 것과 같다(잘못된 정의가 만들 수 있는 피해 = 잘못된 도면 한 장).
참조(대상 요소가 있는지, 저작인지)는 파일 전체를 봐야 알 수 있어 가져오기(foreign.plan_import)가 판단한다. 이 모듈은 정의 하나만 본다.
"""
from __future__ import annotations

import hashlib
import json

from dobim_core import annot as AN
from dobim_core import kinds as K
from dobim_core.family.recipe import FamilyError, load_json_text

DEF_KINDS = ("view", "dim", "tag", "sheet", "grid", "dlayer", "draft", "revision", "keynote", "vtemplate", "pparam", "larea", "opening", "matdef", "walltype")   # 정의를 IFC 에 싣는 종류(kinds.NON_IFC_KINDS 와 같은 집합)
FAMILY_ID = {"view": "view.basic", "dim": "annot.dim", "tag": "annot.tag", "sheet": "sheet.basic", "grid": "grid.basic",
             "dlayer": "draft.layer", "draft": "draft.basic", "revision": "revision.basic", "keynote": "keynote.basic",
             "vtemplate": "view.template", "pparam": "project.param", "larea": "area.legal", "opening": "opening.void", "matdef": "material.def", "walltype": "wall.type"}   # 커맨드가 만드는 행의 family_id
DEFS_PSET = "DoBIM_Defs"                                                # IfcProject 의 예약 속성셋 이름
MAX_DEF_CHARS = 16 * 1024                                               # 정의 하나의 텍스트 상한(정상 정의는 1KB 안팎)
MAX_DEFS = 100_000                                                      # 한 파일에서 읽는 정의 수 상한(넘으면 나머지는 읽지 않고 경고)
MAX_VIEW = 1e7                                                          # 뷰 좌표·거리 상한(commands.VCoord 와 같다)
MAX_OFF = 1e5                                                           # 치수선·태그 간격 상한(commands.Off 와 같다)
MAX_POS = 1e9                                                           # 저장된 위치(home·at) 상한(벽·문 의도의 위치 상한과 같다)
_TAG_ANY = frozenset(f for fs in AN.TAG_ALLOWED.values() for f in fs)

assert set(DEF_KINDS) == set(K.NON_IFC_KINDS)   # 새 비 IFC 종류를 더하면 정의 왕복도 함께 정해야 한다


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def check_of(eid: str, kind: str, params) -> str:
    """정의의 변조 탐지용 해시(id 에 묶인다)."""
    return hashlib.sha256(canon({"id": eid, "kind": kind, "params": params}).encode("utf-8")).hexdigest()


def def_text(eid: str, row: dict) -> str:
    """의도 행(뷰·치수·태그) -> IFC 에 적는 텍스트(결정적 JSON). 같은 행이면 항상 같은 텍스트다."""
    kind, params = row["kind"], row["params"]
    return canon({"kind": kind, "family_id": row["family_id"], "params": params, "check": check_of(eid, kind, params)})


# ---------------------------------------------------------------- 값 검사(커맨드가 만들 수 있는 범위만)
def _in(v, hi, lo=None) -> bool:
    return K.num_ok(v) and abs(v) <= hi and (lo is None or v >= lo)


def _pt(v, hi) -> bool:
    return K._pt_ok(v) and all(abs(x) <= hi for x in v)


def _name_ok(v) -> bool:
    return not any(0xD800 <= ord(c) <= 0xDFFF for c in v)   # 짝 없는 서로게이트는 UTF-8 로 쓸 수 없다


def _strs_ok(v) -> bool:
    """중첩 값(가시성·필터) 안의 모든 글에 짝 없는 서로게이트가 없다."""
    if isinstance(v, str):
        return _name_ok(v)
    if isinstance(v, dict):
        return all(_strs_ok(k) and _strs_ok(x) for k, x in v.items())
    if isinstance(v, list):
        return all(_strs_ok(x) for x in v)
    return True


def _view_values(p: dict) -> None:
    if type(p["scale"]) is not int or not 1 <= p["scale"] <= 1000:
        raise ValueError("뷰 축척이 올바르지 않다")
    if "crop" in p and not all(_in(c, MAX_VIEW) for c in p["crop"]):   # 부위 상세 잘라내기 사각형(view.create 의 좌표 범위)
        raise ValueError("뷰 잘라내기 좌표가 범위를 벗어났다")
    if "parent" in p and "crop" not in p:   # 콜아웃 뷰(W1 1-C 2차): 잘라내기 사각형이 곧 콜아웃 범위다
        raise ValueError("콜아웃 뷰에 잘라내기가 없다")
    if "rcp" in p and "area" in p:   # 반사 천장 평면·면적 평면(W1 1-C 3차)은 함께 못 쓴다
        raise ValueError("반사 천장 평면과 면적 평면을 함께 가질 수 없다")
    t = p["type"]
    if t == "plan":
        if not _in(p["cut_height"], MAX_VIEW) or not _in(p["view_depth"], MAX_VIEW, 0):
            raise ValueError("평면 뷰의 절단 높이·깊이가 범위를 벗어났다")
    elif t == "section":
        if not (_pt(p["p1"], MAX_VIEW) and _pt(p["p2"], MAX_VIEW) and _in(p["depth"], MAX_VIEW) and p["depth"] > 0):
            raise ValueError("단면 뷰의 좌표·깊이가 범위를 벗어났다")
        dx, dy = p["p2"][0] - p["p1"][0], p["p2"][1] - p["p1"][1]
        if dx * dx + dy * dy < 1.0:
            raise ValueError("절단선 길이가 1mm 미만이다")


def _dirs_ok(v) -> bool:
    return isinstance(v, dict) and set(v) <= {"a", "b"} and all(x is None or _pt(x, 1.0 + 1e-6) for x in v.values())


def _dim_values(p: dict) -> None:
    if not _in(p["off"], MAX_OFF) or not _pt(p["home"], MAX_POS) or not _dirs_ok(p["dirs"]):
        raise ValueError("치수의 간격·위치·방향 지문이 올바르지 않다")
    ts = []
    for k in ("a", "b"):
        pr = AN.parse_ref(p[k])
        if pr is None:
            raise ValueError(f"치수 참조 {k} 의 문법이 올바르지 않다")
        ts.append(AN.role_type(pr[0], pr[2]))
    vertical = ts[0] == "z" and ts[1] == "z"
    if (ts[0] == "z") != (ts[1] == "z"):
        raise ValueError("높이 참조와 평면 참조가 섞여 있다")
    m = p.get("measure")   # W1 1-B 2차: 각도(선 둘)·반지름·지름(같은 원 하나). 선형 치수에는 원 참조가 없다
    if (m == "angle" and ts != ["line", "line"]) or (m in ("radius", "diameter") and (ts[0] not in ("circle", "arc") or ts[0] != ts[1] or p["a"] != p["b"])) \
            or (m == "arc" and (ts != ["arc", "arc"] or p["a"] != p["b"])) or (m is None and ("circle" in ts or "arc" in ts)):
        raise ValueError("치수 종류와 참조 종류가 맞지 않는다")
    if vertical != ("at" in p):
        raise ValueError("높이 치수만 at 이 있어야 한다")
    if "at" in p and not _pt(p["at"], MAX_POS):
        raise ValueError("치수 at 이 올바르지 않다")
    if "dl" in p and (vertical or not _pt(p["dl"], MAX_POS)):   # 연속·기준선 치수(W1 연장 ANN-02): 평면 치수만, 원 참조 없음(measure 없음은 kinds 가 본다)
        raise ValueError("치수 dl 이 올바르지 않다")


def _tag_values(p: dict) -> None:
    if not _in(p["off"][0], MAX_OFF) or not _in(p["off"][1], MAX_OFF) or not _pt(p["home"], MAX_POS):
        raise ValueError("태그의 간격·위치가 올바르지 않다")
    if not set(p["fields"]) <= _TAG_ANY:
        raise ValueError("태그에 없는 표시 필드가 있다")


def _grid_values(p: dict) -> None:
    if not all(_in(c, MAX_VIEW) for c in p["line"]):
        raise ValueError("통심선 좌표가 범위를 벗어났다")


def _sheet_values(p: dict) -> None:
    """시트 정의의 값: 커맨드(sheet.define)가 쓰는 같은 모델로 검사하고, 다시 쓴 값이 저장된 값과 같은(정규형) 것만 받는다."""
    from dobim_core import commands_sheet as CS   # 늦게 불러온다: 커맨드 모듈은 이 모듈을 쓰는 엔진보다 늦게 읽힌다
    try:
        d = CS.SheetDef.model_validate({k: v for k, v in p.items() if v is not None}).model_dump(mode="json")   # 값이 없는 선택 필드(null)는 생략으로 읽는다
        d = CS.stored_params(d)   # 뷰포트(W1 1-C 2차)가 없으면 키를 두지 않는 저장 형식
    except ValueError as ex:   # pydantic ValidationError 는 ValueError 다
        raise ValueError(f"시트 값이 올바르지 않다({str(ex).splitlines()[0][:60]})") from None
    if canon(d) != canon(p):
        raise ValueError("시트 정의가 정규형이 아니다")


def parse_def(eid: str, text) -> dict:
    """속성 하나(이름 = 요소 id, 값 = 텍스트) -> {"kind", "params", "family_id"}. 못 읽으면 ValueError(호출자는 이 정의를 되살리지 않는다)."""
    if not K.id_ok(eid):
        raise ValueError("정의 id 가 22자 형식이 아니다")
    if not isinstance(text, str) or len(text) > MAX_DEF_CHARS:
        raise ValueError("정의 텍스트가 없거나 너무 크다")
    try:
        d = load_json_text(text)
    except FamilyError as ex:
        raise ValueError(f"정의 JSON 을 읽을 수 없다: {ex.issues[0].message[:80]}") from None
    except (RecursionError, OverflowError):
        raise ValueError("정의 JSON 중첩이 너무 깊다") from None
    if set(d) != {"kind", "family_id", "params", "check"}:
        raise ValueError("정의 모양이 현재 스키마와 다르다")
    kind, params = d["kind"], d["params"]
    if kind not in DEF_KINDS or d["family_id"] != FAMILY_ID[kind]:
        raise ValueError("정의의 종류·family_id 가 올바르지 않다")
    if (err := K.row_params_error(kind, params)) is not None:
        raise ValueError(err)
    try:
        if not _name_ok(params["name"]) or (kind == "sheet" and not all(_name_ok(params[k]) for k in ("project_name", "sheet_name", "sheet_number", "client", "office"))):
            raise ValueError("이름에 짝 없는 서로게이트가 있다")
        if kind in ("view", "vtemplate") and not all(_strs_ok(params.get(k)) for k in ("vg", "filters")):   # 가시성·필터(W1 1-C)
            raise ValueError("가시성·필터 글에 짝 없는 서로게이트가 있다")
        if kind == "vtemplate":   # 뷰 템플릿(W1 1-C): 값 범위는 kinds 의 파라미터 검사가 커맨드 범위로 본다
            pass
        elif kind in ("revision", "keynote", "pparam", "larea", "opening", "matdef", "walltype"):   # 리비전·키노트(W1 1-B 3차)·프로젝트 매개변수(DATA-03)·법정 면적 객체(ARC-36): 값 범위는 kinds 의 파라미터 검사가 커맨드 범위로 본다
            if not all(_name_ok(params[k]) for k in ("desc", "issued_to", "issued_by", "text", "note") if k in params):
                raise ValueError("글에 짝 없는 서로게이트가 있다")
        elif kind in ("draft", "dlayer"):   # 2D 작도(27단계): 값 범위는 kinds 의 파라미터 검사(_draft_params·_dlayer_params)가 이미 커맨드 범위로 본다
            if not all(_name_ok(params[k]) for k in ("text", "layer", "group") if isinstance(params.get(k), str)):
                raise ValueError("글에 짝 없는 서로게이트가 있다")
        else:
            {"view": _view_values, "dim": _dim_values, "tag": _tag_values, "sheet": _sheet_values, "grid": _grid_values}[kind](params)
    except (KeyError, TypeError, IndexError) as ex:
        raise ValueError(f"정의 값이 올바르지 않다({type(ex).__name__})") from None
    if d["check"] != check_of(eid, kind, params):
        raise ValueError("정의가 변조됐다(check 불일치)")
    return {"kind": kind, "params": params, "family_id": d["family_id"]}


def tag_fields_ok(target_kind: str, fields) -> bool:
    """태그의 표시 필드가 대상 종류(벽·문)에 있는 것인가."""
    return target_kind in AN.TAG_FIELDS and set(fields) <= set(AN.TAG_ALLOWED[target_kind])
