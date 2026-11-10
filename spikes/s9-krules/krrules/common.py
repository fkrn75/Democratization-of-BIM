"""검사 결과 조립과 속성 탐색 공용 도구(codechecks.py 와 slots.py 가 함께 쓴다).

조달청 부속서-8 사용자 가이드(Revit·ArchiCAD)는 실ID·부재명·사업정보 등을 소프트웨어의 사용자 정의 속성으로
넣게 하고, IFC 에서 어느 Pset 에 나오는지는 도구가 정한다. 그래서 표준 IFC 슬롯 하나만 정답으로 보지 않고
"표준 슬롯 또는 한국어 사용자 속성" 중 하나라도 있으면 인정하는 탐색 함수를 여기에 모은다.
"""
from __future__ import annotations

import re
import unicodedata

import ifcopenshell.util.element as uel

MAX_EXAMPLES = 5


def _res(status, applicable=0, failed=0, examples=None, note="", found_via=None):
    r = {"status": status, "applicable": applicable, "failed": failed,
         "examples": (examples or [])[:MAX_EXAMPLES], "note": note}
    if found_via:  # 값을 어느 위치에서 찾았는지(발주처 확인용): {위치 설명: 개수}
        r["found_via"] = dict(sorted(found_via.items()))
    return r


def _ex(e, reason):
    return {"class": e.is_a(), "name": getattr(e, "Name", None),
            "global_id": getattr(e, "GlobalId", None), "reason": reason}


# 입력 안 한 것과 같은 자리표시 문자열(공백·제로폭 문자를 걷어낸 뒤 소문자 비교)
_PLACEHOLDERS = {"", "0", "-", "--", "---", "_", ".", "?", "없음", "해당없음", "해당사항없음", "미정", "미입력", "n/a", "na", "none",
                 "null", "nil", "tbd", "unknown"}
_NUM = re.compile(r"(?:(?<!\w)-)?\d[\d,]*(?:\.\d+)?")  # 앞의 '-' 는 단어 문자 바로 뒤가 아닐 때만 음수 부호로 본다


def _squash(s):
    """제로폭·특수 공백·제어 문자(유니코드 범주 Z*, C*)와 일반 공백을 모두 제거한 문자열."""
    return "".join(ch for ch in s if not unicodedata.category(ch).startswith(("Z", "C")) and not ch.isspace())


def ok(v):
    """값이 '있다'고 볼 수 있는지. None·빈/자리표시 문자열('0', '-', '없음', 'N/A', UNKNOWN, 제로폭 공백만 등)과 숫자 0 은
    없는 것으로 본다. 불리언은 값이다(AboveGround=FALSE 등)."""
    if v is None:
        return False
    if isinstance(v, bool):
        return True
    if isinstance(v, str):
        return _squash(v).lower() not in _PLACEHOLDERS
    if isinstance(v, (list, tuple)):
        return any(ok(x) for x in v)
    if isinstance(v, (int, float)):
        return v != 0
    return True


def ok_number(v):
    """수치 값(면적·층수)이 0 보다 큰지. 문자열은 자리표시를 거부하고 안에 든 첫 숫자가 0 보다 커야 한다('지하4층, 지상10층' 은 4)."""
    if isinstance(v, bool) or v is None:
        return False
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        if not ok(v):
            return False
        m = _NUM.search(v)
        try:
            return bool(m) and float(m.group().replace(",", "")) > 0
        except ValueError:
            return False
    return False


def ok_part_class(v):
    """부위분류 속성 값: 자리표시를 거부하고 영문·숫자·한글이 하나라도 있어야 한다(원문에 형식 규정이 없어 이 정도만 본다)."""
    return isinstance(v, str) and ok(v) and any(ch.isalnum() for ch in v)


def norm(s):
    """속성 이름 비교용 정규화: 공백 제거 + 소문자."""
    return "".join(str(s).split()).lower()


def psets(e):
    """객체(유형 객체 속성 포함)의 {Pset 이름: {속성: 값}}. 읽기 실패는 빈 dict."""
    try:
        return uel.get_psets(e)
    except Exception:
        return {}


def find_prop(e, names, pset_re=None, check=None):
    """Pset 이름 불문(또는 pset_re 일치)으로 names 중 값이 유효한(check, 기본 ok) 첫 속성을 찾는다. (Pset, 속성, 값) 또는 None."""
    check = check or ok
    import re
    wanted = [norm(n) for n in names]
    rx = re.compile(pset_re) if pset_re else None
    for pname, props in psets(e).items():
        if rx and not rx.match(pname):
            continue
        for k, v in props.items():
            if k != "id" and norm(k) in wanted and check(v):
                return pname, k, v
    return None


def find_pset_dict(f, name):
    """프로젝트·대지에 붙은 이름이 name 인 속성세트(IFC2X3 ePSet_* 용). 없으면 None."""
    objs = list(f.by_type("IfcProject")) + list(f.by_type("IfcSite"))
    for o in objs:
        d = psets(o).get(name)
        if d and any(k != "id" for k in d):  # 'id' 키만 있는 빈 속성세트는 선언으로 보지 않음
            return d
    return None


def find_user_prop(f, e, names, check=None):
    """e 자신 -> 프로젝트 순으로 names 속성을 찾는다(가이드는 프로젝트 정보에 입력하게 함). 다른 건물·대지의 값은 보지 않는다."""
    seen = set()
    for o in [e] + list(f.by_type("IfcProject")):
        if o.id() in seen:
            continue
        seen.add(o.id())
        hit = find_prop(o, names, check=check)
        if hit:
            return hit
    return None


def class_refs(e):
    """객체(와 유형 객체)에 연결된 분류 참조 목록(IfcClassificationReference). 값이 빈 참조는 제외."""
    out = []
    objs = [e]
    try:
        t = uel.get_type(e)
        if t is not None and t is not e:
            objs.append(t)
    except Exception:
        pass
    for o in objs:
        for rel in getattr(o, "HasAssociations", None) or []:
            if rel.is_a("IfcRelAssociatesClassification"):
                ref = rel.RelatingClassification
                code = getattr(ref, "Identification", None) or getattr(ref, "ItemReference", None)
                if ok(code):  # 코드가 비었거나 자리표시면 분류한 것이 아님
                    out.append(ref)
    return out


def class_codes(e):
    """분류 참조의 코드 문자열 목록(Identification, 없으면 ItemReference)."""
    return [getattr(r, "Identification", None) or getattr(r, "ItemReference", None) for r in class_refs(e)]


def space_real_id(s):
    """실ID 값과 찾은 위치. 이름이 '실ID' 인 속성을 우선하고, 없으면 IfcSpace.Name 을 보조로 인정한다.
    (조달청 가이드: 실ID 는 실번호와 별개의 사용자 속성. IFC 관례상 Name 은 방 번호 자리)"""
    hit = find_prop(s, ["실ID"])
    if hit:
        return str(hit[2]), "속성 실ID"
    if ok(s.Name):
        return s.Name, "Name 값(보조, 실번호일 수 있음)"
    return None, None
