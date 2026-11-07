# SPDX-License-Identifier: MPL-2.0
"""응답 크기 상한과 커서 검증. 조회(목록)와 변경(affected·results) 응답이 같은 상한을 쓴다.

이 모듈은 ifcopenshell 을 import 하지 않는다(레지스트리 로드가 가벼워야 한다).

상한 근거(tools/size_check.py 가 실측해 results/s1_response_sizes.json 에 남긴다):
- S11 기준은 도구 응답 8,000토큰이다. MCP 는 같은 봉투를 text 와 structuredContent 에 한 벌씩 싣는다.
- 토큰은 바이트/3 으로 추정하면 안 된다. GUID 같은 무작위 영숫자 문자열은 토큰 효율이 나빠 실측이 추정보다 훨씬 크다.
  그래서 tiktoken(cl100k_base, o200k_base)으로 text + structuredContent 합계를 재어 두 토크나이저 모두 8,000 이내가
  되는 바이트 상한을 정했다. 이것은 Claude 토크나이저가 아니라 대용 측정이다(Claude 토크나이저로는 재지 못했다).
- tiktoken 은 개발 의존으로만 두고 코어 런타임에는 넣지 않는다. 코어는 바이트 상한만 안다.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from dobim_core.errors import DobimError

RESPONSE_CAP_BYTES = 6_000        # 봉투 한 벌의 상한(UTF-8 바이트). 값의 근거는 위 설명과 results/s1_response_sizes.json
ENVELOPE_RESERVE_BYTES = 900      # 봉투의 나머지(약 330~510바이트)와 다음 쪽 커서(일람표는 이름이 길면 600바이트 가까이) 몫
CURSOR_INT_MAX = 2**62


def jsize(x: Any) -> int:
    return len(json.dumps(x, ensure_ascii=False).encode("utf-8")) + 2


def fit(rows: list, base: Any = None, reserve: int | None = None) -> tuple[list, bool]:
    """rows 를 앞에서부터 상한 안에서 담는다. (담은 항목, 상한 때문에 잘렸는지). 최소 한 건은 담아 이어받기가 진전된다.
    (한 건이 상한보다 큰 경우는 그 한 건을 그대로 돌려주고 truncated 도 false 다: 단건 제한은 이 모듈의 범위 밖이다.)"""
    used = (ENVELOPE_RESERVE_BYTES if reserve is None else reserve) + (jsize(base) if base else 0)
    out = []
    for r in rows:
        sz = jsize(r)
        if out and used + sz > RESPONSE_CAP_BYTES:
            return out, True
        used += sz
        out.append(r)
    return out, False


def page(next_cursor, total, truncated) -> dict:
    return {"next_cursor": next_cursor, "total": total, "truncated": truncated}


# ---------------------------------------------------------------- 커서
def enc_cursor(v) -> str:
    return base64.urlsafe_b64encode(json.dumps(v, ensure_ascii=False).encode("utf-8")).decode()


def _bad(why: str) -> DobimError:
    return DobimError("SCHEMA_INVALID", f"잘못된 커서다({why})", path="/cursor", hint="이전 응답의 page.next_cursor 를 그대로 넘기세요")


def _is_int(x) -> bool:
    return type(x) is int and 0 <= x <= CURSOR_INT_MAX


def dec_cursor(c: str | None, shape: str):
    """커서를 풀고 모양을 검사한다. 모양이 틀리거나 위조된 커서는 SCHEMA_INVALID 다.
    shape: "id"(22자 GlobalId 문자열), "name"(문자열), "int"(0 이상 정수), "schedule"([레벨, 이름, id] 문자열 셋),
           "relation"([종류 0~2, 방향 0~1, 상대 id]), "affected"([변경 종류 0~2, id])."""
    if c is None:
        return None
    try:
        v = json.loads(base64.urlsafe_b64decode(c.encode("ascii")).decode("utf-8"))
    except Exception:
        raise _bad("해석할 수 없다") from None
    ok = False
    if shape == "id":
        ok = type(v) is str and len(v) == 22
    elif shape == "name":
        ok = type(v) is str and 0 < len(v) <= 128
    elif shape == "int":
        ok = _is_int(v)
    elif shape == "schedule":
        ok = type(v) is list and len(v) == 3 and all(type(x) is str for x in v)
    elif shape == "affected":
        ok = type(v) is list and len(v) == 2 and _is_int(v[0]) and v[0] <= 2 and type(v[1]) is str
    elif shape == "relation":
        ok = type(v) is list and len(v) == 3 and _is_int(v[0]) and v[0] <= 2 and _is_int(v[1]) and v[1] <= 1 and type(v[2]) is str
    if not ok:
        raise _bad("모양이 맞지 않는다")
    return v


# ---------------------------------------------------------------- 변경 응답
EMPTY_AFFECTED = {"created": [], "modified": [], "deleted": [], "counts": {"created": 0, "modified": 0, "deleted": 0}, "truncated": False}


def fit_mutation(results, affected: dict, rev: int | None, dry_run: bool) -> tuple[Any, dict, dict | None, list[str]]:
    """변경 응답(results 와 affected)을 상한 안에 넣는다. 변경 자체는 이미 끝났고 응답만 줄인다.
    돌려주는 것: (results, affected, page, 경고). results 가 리스트(배치)이고 잘리면 page 에 {total, truncated:true} 를 낸다.
    affected 는 항상 {created, modified, deleted, counts, truncated} 모양이고, 잘리면 앞부분만 남기되 counts 는 정확하다.
    잘린 나머지는 history.affected(rev) 로 이어받는다(변경 커맨드가 로그에 남긴 효과에서 다시 계산)."""
    used = ENVELOPE_RESERVE_BYTES
    page_out = None
    shown_results = results
    if isinstance(results, list):
        half = (RESPONSE_CAP_BYTES - used) // 2
        shown_results, u = [], 0
        for r in results:
            sz = jsize(r)
            if shown_results and u + sz > half:
                break
            shown_results.append(r)
            u += sz
        used += u
        if len(shown_results) < len(results):
            page_out = page(None, len(results), True)
    else:
        used += jsize(results)
    counts = {k: len(affected[k]) for k in ("created", "modified", "deleted")}
    out = {"created": [], "modified": [], "deleted": []}
    cut = False
    for k in ("created", "modified", "deleted"):
        for eid in affected[k]:
            sz = jsize(eid)
            if used + sz > RESPONSE_CAP_BYTES and (out["created"] or out["modified"] or out["deleted"]):
                cut = True
                break
            used += sz
            out[k].append(eid)
        if cut:
            break
    out["counts"] = counts
    out["truncated"] = cut
    warns = []
    if cut or page_out:
        shown = {k: len(out[k]) for k in ("created", "modified", "deleted")}
        how = ("dry_run 이라 로그에 남지 않아 이어받을 수 없다: 같은 입력을 dry_run 없이 실행한 뒤 history.affected 를 쓰세요" if dry_run
               else f"나머지는 history.affected(rev={rev}) 로 이어받는다")
        n = len(results) if isinstance(results, list) else 1
        warns.append(f"응답이 크기 상한으로 잘렸다: results {len(shown_results) if isinstance(results, list) else 1}/{n}건, "
                     f"affected 생성 {shown['created']}/{counts['created']}, 수정 {shown['modified']}/{counts['modified']}, "
                     f"삭제 {shown['deleted']}/{counts['deleted']}. {how}")
    return shown_results, out, page_out, warns
