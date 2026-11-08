# SPDX-License-Identifier: MPL-2.0
"""S8 판정기: 여러 번(프로세스별) 실행한 시나리오 결과에서 합격 기준을 검사한다.

runs = [ {시나리오 id: run_scenario 결과}, ... ]  (각 항목은 서로 다른 프로세스·해시 시드·순서·시각에서 만든 것)
check(runs) 는 실패 설명 목록을 돌려준다. 비어 있어야 합격이다. 변이 시험은 이 판정기가 변이를 잡는지(비어 있지 않은지) 본다.
분류 접두: [결정성] [정확성] [대칭] [사후] [기대]
"""
from __future__ import annotations

import json

from scenarios import BY_ID


def _j(x) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


def hashes_of(flow: dict, variant: str | None):
    if variant is None:
        return (flow.get("apply") or {}).get("hashes")
    return (((flow.get("variants") or {}).get(variant) or {}).get("apply") or {}).get("hashes")


def check(runs: list[dict], ids: list[str] | None = None) -> list[str]:
    fails: list[str] = []
    ids = ids or [i for i in sorted(runs[0]) if not i.startswith("_")]
    for sid in ids:
        sc = BY_ID[sid]
        per = [r[sid] for r in runs if sid in r]
        if len(per) != len(runs):
            fails.append(f"[결정성] {sid}: 일부 실행에 결과가 없다")
            continue
        crashed = [p["crash"] for p in per if p.get("crash")]
        if crashed:
            fails.append(f"[정확성] {sid}: 실행이 예외로 끝났다 {crashed[0]}")
            continue
        # 1) 결정성: 실행(프로세스·시드·순서·시각)이 달라도 방향별 결과가 바이트 단위로 같다
        for d in per[0]["flows"]:
            ref = _j(per[0]["flows"][d])
            for k, p in enumerate(per[1:], 1):
                if _j(p["flows"][d]) != ref:
                    fails.append(f"[결정성] {sid} {d}: 실행 0 과 실행 {k} 의 결과가 다르다")
                    break
        if _j(per[0].get("oracle")) != _j(per[-1].get("oracle")):
            fails.append(f"[결정성] {sid}: 재실행 오라클 결과가 실행마다 다르다")
        r0 = per[0]
        fa, fb = r0["flows"]["A<-B"], r0["flows"]["B<-A"]
        # 2) 대칭: 방향을 바꿔도 같은 충돌 집합, 같은 결과 해시
        ka = [(c["key"], c["type"], c["elements"]) for c in fa["preview"]["conflicts"]]
        kb = [(c["key"], c["type"], c["elements"]) for c in fb["preview"]["conflicts"]]
        if ka != kb:
            fails.append(f"[대칭] {sid}: 방향에 따라 충돌 집합이 다르다")
        if fa["preview"]["plan_digest"] != fb["preview"]["plan_digest"]:
            fails.append(f"[대칭] {sid}: 계획 지문이 방향에 따라 다르다")
        for var in (None, "spec", "swap"):
            ha, hb = hashes_of(fa, var), hashes_of(fb, var)
            if (ha is None) != (hb is None):
                fails.append(f"[대칭] {sid} {var}: 한 방향만 합쳐진다")
            elif ha is not None and ha != hb:
                fails.append(f"[대칭] {sid} {var}: 방향에 따라 결과 해시(상태/파생/IFC)가 다르다")
        # 3) 기대: 충돌 유무·유형
        exp = sc["expect"]
        for d, f in r0["flows"].items():
            types = sorted(c["type"] for c in f["preview"]["conflicts"])
            if exp.get("clean"):
                if types or not f["apply"]["ok"]:
                    fails.append(f"[기대] {sid} {d}: 깨끗하게 합쳐져야 한다(충돌 {types}, 적용 {f['apply'].get('code')})")
            else:
                if types != sorted(exp["conflicts"]):
                    fails.append(f"[기대] {sid} {d}: 충돌 유형 {types} != 기대 {sorted(exp['conflicts'])}")
                if f["apply"]["ok"]:
                    fails.append(f"[기대] {sid} {d}: 충돌이 있는데 조용히 합쳐졌다")
                elif f["apply"].get("code") != "MERGE_CONFLICT" or not f["apply"].get("state_untouched"):
                    fails.append(f"[기대] {sid} {d}: 충돌 거부가 상태를 건드렸거나 오류 코드가 다르다 {f['apply']}")
                for c in f["preview"]["conflicts"]:
                    if not (c["ours_ops"] or c["theirs_ops"]) :
                        fails.append(f"[기대] {sid} {d}: 충돌 {c['key']} 에 양쪽 op 귀속이 없다")
                    if not c["take"]:
                        fails.append(f"[기대] {sid} {d}: 충돌 {c['key']} 에 해결 선택지가 없다")
            # 4) 정확성/사후
            if f["apply"]["ok"]:
                bad = [k for k, v in (f.get("post") or {}).items() if v is not True]
                if bad:
                    fails.append(f"[사후] {sid} {d}: {bad}")
                if f.get("facts"):
                    fails.append(f"[정확성] {sid} {d}: 도메인 사실 위반 {f['facts']}")
            for vn, v in (f.get("variants") or {}).items():
                if not v["apply"]["ok"]:
                    fails.append(f"[정확성] {sid} {d} {vn}: 해결 지시를 줘도 합쳐지지 않는다 {v['apply']}")
                    continue
                bad = [k for k, x in (v.get("post") or {}).items() if x is not True]
                if bad:
                    fails.append(f"[사후] {sid} {d} {vn}: {bad}")
                if v.get("facts"):
                    fails.append(f"[정확성] {sid} {d} {vn}: 도메인 사실 위반 {v['facts']}")
        if sid == "s18_fast_forward":
            if (fa["preview"]["relation"], fb["preview"]["relation"]) != ("ours_behind", "ours_ahead"):
                fails.append(f"[기대] {sid}: 빠른 감기 관계가 틀렸다")
    # 시나리오 밖의 독립 점검(재병합·배치·조작된 로그·사이드카 등): 모든 실행에서 참이고 실행 사이에 같아야 한다
    ex = [r.get("_extra") for r in runs if "_extra" in r]
    if not ex:
        pass   # 시나리오 결과만 넘긴 호출(시나리오 하나를 한 프로세스에서 돌린 시험)은 독립 점검을 따로 한다
    elif len(ex) != len(runs):
        fails.append("[정확성] _extra: 독립 점검 결과가 없다")
    else:
        for k, e in enumerate(ex):
            if e.get("crash"):
                fails.append(f"[정확성] _extra: 독립 점검이 예외로 끝났다 {e['crash']}")
            else:
                bad = [n for n, v in e.items() if v is not True]
                if bad:
                    fails.append(f"[정확성] _extra 실행 {k}: 거짓인 점검 {bad}")
        if len({_j(e) for e in ex}) > 1:
            fails.append("[결정성] _extra: 독립 점검 결과가 실행마다 다르다")
    return fails
