"""납품처별 프로파일: 공통 코어 + 조달청(기본) + 세움터(초안, 기본 꺼짐).

- procurement(기본): 지금까지의 37개 검사 전부. 결과는 프로파일 도입 전과 바이트 단위로 같다(결과에 프로파일 표식 키를 더하지 않음).
- seumteo(초안): 공통 코어(catalog.CHECK_SCOPE 가 common) + 세움터 전용 KR-SEU-* . 조달청 전용 규칙은 해당 없음으로 표시하고
  어떤 규칙이 대신 판정하는지 적는다. 결과 맨 위에 초안 경고를 둔다.
"""
from __future__ import annotations

from . import catalog

DEFAULT_PROFILE = "procurement"
PROFILE_IDS = ("procurement", "seumteo")

SEUMTEO_LABEL = "세움터 BIM 설계기준서 v1.0(안) 초안 프로파일 + 공통 코어"
DRAFT_WARNING = "초안: 세움터 BIM 설계기준서 v1.0(안) 2021-12, 공식 배포본 미확보, 실제 세움터식 내보내기 미시험"

# 세움터 프로파일에서 조달청 전용 규칙을 돌리지 않는 이유(대조표 판정: A 일치 / B 불일치 / D 사본에 근거 없음)
SKIP_REASONS = {
    "KR-IDS-02": "세움터는 모델 구분(개요·건축·구조·대지)별로 실 공간 유무가 달라 파일 단위 존재를 요구하지 않음",
    "KR-IDS-04": "세움터 속성 규격에 실ID 가 없음(REQ-015, 사본에 근거 없음)",
    "KR-IDS-07": "세움터 부위 규격에 부재명 속성이 없음(REQ-022, 사본에 근거 없음)",
    "KR-IDS-08": "에너지 검토는 조달청·국토부 조건부 규칙이고 세움터 사본에 IsExternal 근거가 없음(REQ-030)",
    "KR-IDS-09": "에너지 검토는 조달청 조건부 규칙이고 세움터 사본에 열관류율이 없음(REQ-031)",
    "KR-IDS-10": "에너지 검토는 국토부 조건부 규칙이고 세움터 사본에 열관류율이 없음(REQ-032)",
    "KR-CODE-05": "길이 단위는 세움터가 mm 기본이라 공통 KR-CODE-04 가 판정(m 허용은 국토부 규칙)",
    "KR-CODE-08": "세움터 속성 규격에 실ID 형식 규정이 없음(REQ-016, 사본에 근거 없음)",
    "KR-CODE-12": "세움터 사본은 TM 좌표계만 언급하고 구체 투영·원점을 지정하지 않음(REQ-041·051, 근거 없음)",
    "KR-CODE-13": "세움터 공종분류는 약식 코드 목록만 있고 결합·물량 구문이 없음(REQ-024, 근거 없음)",
}
# 세움터 전용 KR-SEU-* 가 대신 판정하는 조달청 규칙(catalog.SEUMTEO_CHECKS 의 네 번째 칸에서 뒤집어 만든다)
REPLACED_BY = {old: seu for seu, (_, _, _, olds) in catalog.SEUMTEO_CHECKS.items() for old in olds}


def known(profile):
    return profile in PROFILE_IDS


def seumteo_skip_note(cid):
    """세움터 프로파일에서 조달청 전용 규칙 cid 를 해당 없음으로 둘 때 쓰는 (노트, 대체 규칙 또는 None)."""
    if cid in REPLACED_BY:
        seu = REPLACED_BY[cid]
        return f"세움터 프로파일에서는 이 규칙 대신 {seu} 가 판정함(조달청 전용 규칙)", seu
    return "세움터 프로파일 해당 없음: " + SKIP_REASONS[cid], None


def scope_counts():
    """소속별 규칙 수: {'common': n, 'procurement': n, 'seumteo': n}."""
    out = {"common": 0, "procurement": 0, "seumteo": len(catalog.SEUMTEO_CHECKS)}
    for s in catalog.CHECK_SCOPE.values():
        out[s] += 1
    return out
