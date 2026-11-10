"""krrules/catalog.py 에서 requirements.json 을 만든다(손으로 고치지 말고 catalog.py 를 고칠 것).

사용: python build_catalog.py
"""
import json
from pathlib import Path

from krrules import catalog, profiles, seumteo

ROOT = Path(__file__).resolve().parent


def main():
    sources = {d["id"]: d for d in json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))["documents"]}
    # 출처 미확인 문서에서 뽑은 요구사항은 구현 대상에서 빼야 한다
    for r in catalog.REQUIREMENTS:
        src = sources[r["source"]]
        if r["checks"] and not src["source_verified"]:
            raise SystemExit(f"{r['id']}: 출처 미확인 문서({r['source']})의 요구사항을 구현하려 함")
    checks = []
    for cid, (mode, sev) in catalog.CHECKS.items():
        checks.append({"id": cid, "mode": mode, "severity": sev, "profile_scope": catalog.CHECK_SCOPE[cid],
                       "seumteo": ("대상 변경(실 공간만)" if cid in seumteo.ADAPTED else "그대로 적용" if catalog.CHECK_SCOPE[cid] == "common" else
                                   "대체: " + profiles.REPLACED_BY[cid] if cid in profiles.REPLACED_BY else "해당 없음"),
                       "requirements": [r["id"] for r in catalog.REQUIREMENTS if cid in r["checks"]]})
    # 세움터 전용 검사(소속 seumteo). 근거는 세움터 BIM 설계기준서 v1.0(안, 2021-12)의 절·쪽이고 공식 배포본은 미확보다.
    seumteo_checks = [{"id": cid, "mode": mode, "severity": sev, "profile_scope": "seumteo", "requirements": reqs, "replaces": olds}
                      for cid, (mode, sev, reqs, olds) in catalog.SEUMTEO_CHECKS.items()]
    data = {
        "_meta": {
            "note": "요약은 원문을 줄여 옮긴 것이며 인용이 아니다. interpretation=true 는 IFC 속성 사상이 원문이 아니라 이 스파이크의 해석이라는 뜻(사람 확인 필요).",
            "profile": "조달청 v2.1 실시설계 + 국토부 시행지침(설계자·발주자 편). 계획·중간설계는 같은 구조라 미구현.",
            "profiles": {
                "default": profiles.DEFAULT_PROFILE,
                "note": "소속: common=두 프로파일 공통, procurement=조달청·국토부 전용, seumteo=세움터 초안 전용. 세움터 프로파일은 " + profiles.DRAFT_WARNING,
                "scope_counts": profiles.scope_counts(),
            },
            "counts": {
                "requirements": len(catalog.REQUIREMENTS),
                "implemented_requirements": sum(1 for r in catalog.REQUIREMENTS if r["checks"]),
                "checks": len(catalog.CHECKS),
                "ids_checks": sum(1 for m, _ in catalog.CHECKS.values() if m == "ids"),
                "code_checks": sum(1 for m, _ in catalog.CHECKS.values() if m == "code"),
            },
        },
        "requirements": catalog.REQUIREMENTS,
        "checks": checks,
        "seumteo_checks": seumteo_checks,
    }
    (ROOT / "requirements.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(data["_meta"]["counts"])


if __name__ == "__main__":
    main()
