# SPDX-License-Identifier: MPL-2.0
"""응답 크기 상한과 커서 검증. 조회(목록)와 변경(affected·results) 응답이 같은 상한을 쓴다.

이 모듈은 ifcopenshell 을 import 하지 않는다(레지스트리 로드가 가벼워야 한다).

상한 근거(tools/size_check.py 가 실측해 results/s1_response_sizes.json 에 남기고,
tools/calibrate_tokens.py 가 추정식을 보정해 results/s1_token_calibration.json 에 남긴다):
- S11 기준은 도구 응답 8,000토큰이다. MCP 는 같은 봉투를 text 와 structuredContent 에 한 벌씩 싣는다.
- 상한은 두 가지를 함께 본다. (1) 봉투 JSON 의 실제 직렬화 바이트가 RESPONSE_CAP_BYTES 이하, (2) 문자 종류를 반영한 추정 토큰이
  RESPONSE_CAP_TOKENS 이하. 어느 쪽이든 먼저 닿으면 거기서 자른다. 예비 몫을 어림하지 않는다: 봉투 뼈대와 다음 쪽 커서(마지막으로
  담을 항목의 실제 커서 길이)를 포함한 실제 크기로 판정한다.
- 토큰은 바이트/3 으로 추정하면 안 된다. 무작위 영숫자(GUID)는 0.8토큰/문자이고, 무작위 한글·한자·이모지·제어문자 이름은
  바이트당 1토큰에 가깝다(한글 2.65, 한자 2.4, 이모지 3~4, 제어문자 2.7 토큰/문자). 그래서 추정식은 문자 종류별 가중치를 쓴다(est_tokens).
- 계수는 tiktoken(cl100k_base, o200k_base) 실측으로 보정했다. tiktoken 은 개발 의존으로만 두고 코어 런타임에는 넣지 않는다.
  이것은 Claude 토크나이저가 아니라 대용 측정이다(Claude 토크나이저로는 재지 못했다).
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Callable

from dobim_core.errors import DobimError

RESPONSE_CAP_BYTES = 6_000        # 봉투 한 벌의 상한(UTF-8 바이트, 실제 직렬화 크기)
RESPONSE_CAP_TOKENS = 3_950       # 봉투 한 벌의 추정 토큰 상한. text + structuredContent 두 벌이라 합계 7,900 이하. 추정식이 실제보다 작지 않게 보정돼 있어 합계 8,000 을 넘지 않는다
CURSOR_INT_MAX = 2**62
STR_MAX = 255                     # 속성 문자열 값의 입력 상한(문자 수). 근거는 commands.py 의 Text 설명
DEFAULT_CURSOR_CHARS = 760        # 커서 길이를 모르는 호출자가 가정하는 최악(실측 최대 728)
_BIG = 2**62                      # 뼈대 크기를 잴 때 쓰는 가장 긴 정수


def jsize(x: Any) -> int:
    return len(json.dumps(x, ensure_ascii=False).encode("utf-8")) + 2


# ---------------------------------------------------------------- 추정 토큰
# 문자열 안의 문자 종류별 가중치(토큰/문자). tools/calibrate_tokens.py 가 실측으로 확인한다.
#  ASCII: 같은 종류(소문자·대문자·숫자) 연속은 합쳐지고 종류가 바뀌면 끊긴다 -> 0.62*문자수 + 0.4*연속구간수
#         (무작위 영숫자 0.76, 소문자만 0.55, 문자-숫자 교대 1.0, 기호 한 글자씩 1.0 을 모두 덮는다)
#  제어문자: JSON 이 \u00XX 로 이스케이프해 문자당 2.7토큰 -> 구간 1.0 에 2.0 을 더해 3.0
#  따옴표·역슬래시: 이스케이프로 문자당 1.0 -> 0.15 를 더 얹는다
#  비 ASCII: UTF-8 바이트당 1토큰이 BPE(바이트 대체 포함)의 이론상 최악이므로 바이트 수 그대로 센다. 실측은 한글 2.65, 한자 2.4, 이모지 2.9~4.0 이다.
_RUNS = re.compile(r"[a-z]+|[A-Z]+|[0-9]+|[^a-zA-Z0-9]")
_NON_ASCII = re.compile(r"[^\x00-\x7f]+")
_CTRL = re.compile(r"[\x00-\x1f]")
W_ASCII_CHAR, W_ASCII_RUN, W_CTRL_EXTRA, W_QUOTE_EXTRA = 0.62, 0.4, 2.0, 0.15
W_STR_QUOTES, W_DICT_ENTRY, W_LIST_ENTRY, W_CONTAINER = 2.5, 3.0, 1.5, 2.0


def _ascii_tokens(s: str) -> float:
    return (W_ASCII_CHAR * len(s) + W_ASCII_RUN * len(_RUNS.findall(s)) + W_CTRL_EXTRA * len(_CTRL.findall(s))
            + W_QUOTE_EXTRA * (s.count('"') + s.count("\\")))


def _wide_weight(ch: str) -> float:
    o = ord(ch)
    return 2.0 if o < 0x800 else (3.0 if o < 0x10000 else 4.0)   # UTF-8 바이트 수


def str_tokens(s: str) -> float:
    """문자열 하나(따옴표 제외)의 추정 토큰."""
    if s.isascii():
        return _ascii_tokens(s)
    t = sum(_ascii_tokens(seg) for seg in _NON_ASCII.split(s) if seg)
    for m in _NON_ASCII.findall(s):
        t += sum(map(_wide_weight, m))
    return t


def est_tokens(obj: Any) -> float:
    """JSON 값 하나가 차지하는 추정 토큰(구조 포함). 보수적으로 어림하며, 실제 토큰 수보다 작게 나오지 않도록 보정한 값이다."""
    if obj is None or obj is True or obj is False:
        return 1.0
    if isinstance(obj, str):
        return str_tokens(obj) + W_STR_QUOTES
    if isinstance(obj, (int, float)):
        return str_tokens(repr(obj))
    if isinstance(obj, dict):
        return W_CONTAINER + sum(str_tokens(str(k)) + est_tokens(v) + W_DICT_ENTRY for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return W_CONTAINER + sum(est_tokens(x) + W_LIST_ENTRY for x in obj)
    return str_tokens(str(obj))


# ---------------------------------------------------------------- 봉투 뼈대와 목록 자르기
def empty_affected() -> dict:
    """아무것도 바뀌지 않은 affected. 호출마다 새 객체를 만든다(응답 하나를 고쳐도 다른 응답이 오염되지 않게)."""
    return {"created": [], "modified": [], "deleted": [], "counts": {"created": 0, "modified": 0, "deleted": 0}, "truncated": False}


def page(next_cursor, total, truncated) -> dict:
    return {"next_cursor": next_cursor, "total": total, "truncated": truncated}


def _skeleton(result: Any, pg: Any, affected: dict | None = None, warnings: list | None = None, dry_run: bool = False) -> dict:
    from dobim_core import dispatch   # 지연 import: dispatch 가 이 모듈을 import 한다
    return dispatch.ok_env(_BIG, result, affected=affected, warnings=warnings, page=pg, dry_run=dry_run)


def fit(rows: list, base: dict | None = None, cursor_of: Callable[[int], str] | None = None,
        extra_bytes: int = 0, extra_tokens: float = 0.0, trusted: bool = False) -> tuple[list, bool]:
    """rows 를 앞에서부터 상한 안에서 담는다. (담은 항목, 상한 때문에 잘렸는지). 최소 한 건은 담아 이어받기가 진전된다.

    크기는 실제 봉투로 잰다: 봉투 뼈대 + base(result 의 다른 키들) + 항목들 + 다음 쪽 커서(그 항목이 마지막일 때의 실제 길이).
    cursor_of(i) 는 rows[i] 가 마지막 항목일 때 낼 커서 문자열이다(없으면 최악 길이를 가정한다).
    trusted=True 는 내용을 개발자가 정한 정적 목록(커맨드 레지스트리)용이다: 입력이 섞이지 않으므로 보수적인 토큰 추정을 쓰지 않고 바이트 상한만 본다.
    (실측은 results/s1_response_sizes.json 의 commands.search 행: 합계 3,404토큰.)
    (한 건이 상한보다 큰 경우는 그 한 건을 그대로 돌려준다: 단건은 입력 길이 상한과 호출자의 분할이 책임진다.)"""
    skel = _skeleton({**(base or {}), "items": []}, page("", _BIG, True))
    used_b = jsize(skel) + extra_bytes
    used_t = est_tokens(skel) + extra_tokens
    out = []
    for i, r in enumerate(rows):
        cur = cursor_of(i) if cursor_of else "a" * DEFAULT_CURSOR_CHARS
        b, t = jsize(r) + len(cur), est_tokens(r) + W_LIST_ENTRY + str_tokens(cur)
        if out and (used_b + b > RESPONSE_CAP_BYTES or (not trusted and used_t + t > RESPONSE_CAP_TOKENS)):
            return out, True
        used_b += jsize(r)
        used_t += est_tokens(r) + W_LIST_ENTRY
        out.append(r)
        # 커서 몫은 마지막 항목 하나분만 더해 판정하므로 누적하지 않는다
    return out, False


# ---------------------------------------------------------------- 긴 문자열 표시
def clip_text(s: str) -> tuple[str, bool]:
    """STR_MAX 를 넘는 문자열을 앞부분만 남긴다(옛 로그의 값 때문에 단건 응답이 터지지 않게). (문자열, 잘렸는지)"""
    return (s, False) if len(s) <= STR_MAX else (s[:STR_MAX], True)


def clip_deep(x: Any) -> tuple[Any, int]:
    """중첩 구조의 긴 문자열을 모두 자른다. (새 구조, 자른 개수). 자르지 않은 부분은 그대로 공유한다."""
    if isinstance(x, str):
        y, c = clip_text(x)
        return y, int(c)
    if isinstance(x, dict):
        n, out = 0, {}
        for k, v in x.items():
            out[k], c = clip_deep(v)
            n += c
        return (out, n) if n else (x, 0)
    if isinstance(x, list):
        n, out = 0, []
        for v in x:
            y, c = clip_deep(v)
            out.append(y)
            n += c
        return (out, n) if n else (x, 0)
    return x, 0


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
           "relation"([종류 0~2, 방향 0~1, 상대 id]), "affected"([변경 종류 0~2, 처음 건드린 seq, id]),
           "pair"([속성셋 이름, 속성 이름])."""
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
        ok = type(v) is list and len(v) == 3 and _is_int(v[0]) and v[0] <= 2 and _is_int(v[1]) and type(v[2]) is str
    elif shape == "relation":
        ok = type(v) is list and len(v) == 3 and _is_int(v[0]) and v[0] <= 2 and _is_int(v[1]) and v[1] <= 1 and type(v[2]) is str
    elif shape == "pair":
        ok = type(v) is list and len(v) == 2 and all(type(x) is str for x in v)
    if not ok:
        raise _bad("모양이 맞지 않는다")
    return v


# ---------------------------------------------------------------- 변경 응답
MAX_WARNING_LINES = 8     # 응답 하나에 싣는 서로 다른 경고의 수
MAX_WARNING_CHARS = 240   # 경고 한 줄의 길이


def group_warnings(warnings) -> list[str]:
    """같은 경고는 한 줄로 묶어 건수를 붙이고(첫 등장 순서 유지), 줄 수와 길이를 제한한다.
    경고 문구는 행위자 이름 같은 입력이 섞일 수 있어 길이도 자른다."""
    counts: dict[str, int] = {}
    for w in warnings:
        counts[w] = counts.get(w, 0) + 1
    lines = [(w if n == 1 else f"{w} (x{n}건)") for w, n in counts.items()]
    lines = [w if len(w) <= MAX_WARNING_CHARS else w[:MAX_WARNING_CHARS - 1] + "…" for w in lines]
    if len(lines) > MAX_WARNING_LINES:
        rest = lines[MAX_WARNING_LINES - 1:]
        lines = lines[:MAX_WARNING_LINES - 1] + [f"그 밖의 경고 {len(rest)}종은 생략했다(전체 {sum(counts.values())}건)"]
    return lines


def _ranges(idx: list[int]) -> str:
    out, i = [], 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        out.append(str(idx[i]) if i == j else f"{idx[i]}~{idx[j]}")
        i = j + 1
    return ", ".join(out[:6]) + (" 등" if len(out) > 6 else "")


def fit_mutation(results, affected: dict, rev: int | None, dry_run: bool, warnings=(), must_keep: Callable[[Any], bool] | None = None,
                 how: str | None = None, extra_bytes: int = 0, extra_tokens: float = 0.0) -> tuple[Any, dict, dict | None, list[str]]:
    """변경 응답(results, affected, warnings)을 상한 안에 넣는다. 변경 자체는 이미 끝났고 응답만 줄인다.
    돌려주는 것: (results, affected, page, 경고 전체). results 가 리스트(배치)이고 잘리면 page 에 {total, shown, truncated:true, continue} 를 낸다.
    affected 는 항상 {created, modified, deleted, counts, truncated} 모양이고, 잘리면 앞부분만 남기되 counts 는 정확하다.
    경고는 같은 것끼리 묶어 상한 안에 넣는다. must_keep 이 참인 results 항목(실패 결과)은 잘려도 반드시 남긴다.
    잘린 나머지는 history.affected(rev) 로 이어받는다(변경 커맨드가 로그에 남긴 효과에서 다시 계산).
    extra_* 는 이 함수가 만들지 않는 봉투 몫(예: script 가 최상위에 한 번 더 싣는 error)이다."""
    is_list = isinstance(results, list)
    warns = group_warnings(warnings)
    counts = {k: len(affected[k]) for k in ("created", "modified", "deleted")}
    n = len(results) if is_list else 1
    full_aff = {**{k: list(affected[k]) for k in ("created", "modified", "deleted")}, "counts": counts, "truncated": False}
    full = _skeleton(results, None, full_aff, warns, dry_run)   # 다 담아도 상한 안이면 그대로 돌려준다
    if jsize(full) + extra_bytes <= RESPONSE_CAP_BYTES and est_tokens(full) + extra_tokens <= RESPONSE_CAP_TOKENS:
        return results, full_aff, None, warns
    cont = None if dry_run or rev is None else {"command": "history.affected", "args": {"rev": rev}}
    # 잘렸을 때 붙는 경고와 page 를 가장 길게 만들어 그 크기를 미리 비운다(실제는 이보다 짧다)
    worst_warn = _trunc_warning(n, n, counts, counts, dry_run, rev, how, "9" * 24 if is_list else None, is_list)
    pg_worst = {"next_cursor": None, "total": _BIG, "truncated": True, "shown": _BIG, "continue": cont} if is_list else None
    zero = {"created": [], "modified": [], "deleted": [], "counts": counts, "truncated": True}
    skel = _skeleton([] if is_list else results, pg_worst, zero, warns + [worst_warn], dry_run)
    used_b, used_t = jsize(skel) + extra_bytes, est_tokens(skel) + extra_tokens
    shown, shown_idx = results, list(range(n))
    if is_list:
        forced = [i for i, r in enumerate(results) if must_keep and must_keep(r)]
        for i in forced:
            used_b += jsize(results[i])
            used_t += est_tokens(results[i]) + W_LIST_ENTRY
        # affected 가 필요한 만큼(최대 남은 몫의 절반)을 먼저 비우고, 나머지를 results 가 쓴다. affected 가 적으면 results 가 더 많이 담긴다
        rem_b, rem_t = RESPONSE_CAP_BYTES - used_b, RESPONSE_CAP_TOKENS - used_t
        need_b = sum(jsize(x) for k in ("created", "modified", "deleted") for x in affected[k])
        need_t = sum(est_tokens(x) + W_LIST_ENTRY for k in ("created", "modified", "deleted") for x in affected[k])
        half_b, half_t = rem_b - min(need_b, rem_b // 2), rem_t - min(need_t, rem_t / 2)
        keep, ub, ut, taken = set(forced), 0, 0.0, 0
        for i, r in enumerate(results):
            if i in keep:
                continue
            b, t = jsize(r), est_tokens(r) + W_LIST_ENTRY
            if taken and (ub + b > half_b or ut + t > half_t):
                break   # 앞에서부터 이어 담는다(실패 결과처럼 강제로 남긴 항목은 따로 센다)
            keep.add(i)
            ub += b
            ut += t
            taken += 1
        shown_idx = sorted(keep)
        shown = [results[i] for i in shown_idx]
        used_b += ub
        used_t += ut
    else:
        used_b += jsize(results)
        used_t += est_tokens(results)
    out = {"created": [], "modified": [], "deleted": []}
    cut = False
    for k in ("created", "modified", "deleted"):
        for eid in affected[k]:
            b, t = jsize(eid), est_tokens(eid) + W_LIST_ENTRY
            if used_b + b > RESPONSE_CAP_BYTES or used_t + t > RESPONSE_CAP_TOKENS:
                cut = True
                break
            used_b += b
            used_t += t
            out[k].append(eid)
        if cut:
            break
    out["counts"] = counts
    out["truncated"] = cut
    page_out = None
    list_cut = is_list and len(shown_idx) < n
    if list_cut:
        page_out = {"next_cursor": None, "total": n, "truncated": True, "shown": len(shown_idx), "continue": cont}
        if cont is None:
            del page_out["continue"]
    if cut or list_cut:
        shown_c = {k: len(out[k]) for k in ("created", "modified", "deleted")}
        omitted = _ranges([i for i in range(n) if i not in set(shown_idx)]) if list_cut else ""
        warns.append(_trunc_warning(len(shown_idx), n, shown_c, counts, dry_run, rev, how, omitted if list_cut else None, is_list))
    return shown, out, page_out, warns


def _trunc_warning(shown_n: int, n: int, shown: dict, counts: dict, dry_run: bool, rev, how: str | None, omitted: str | None, is_list: bool) -> str:
    """잘림 경고. omitted 는 results(리스트)에서 생략한 항목 번호(없으면 None), is_list 는 results 가 항목 목록인지(단건 응답에는 results 문구를 쓰지 않는다)."""
    if how is None:
        how = ("dry_run 이라 로그에 남지 않아 이어받을 수 없다: 같은 입력을 dry_run 없이 실행한 뒤 history.affected 를 쓰세요" if dry_run
               else f"나머지는 history.affected(rev={rev}) 로 이어받는다" + ("(항목의 seq 는 results 의 seq 와 같다)" if is_list else ""))
    head = "응답이 크기 상한으로 잘렸다: "
    if omitted is not None:
        head += f"results {shown_n}/{n}건(생략한 index: {omitted}), "
    return (f"{head}affected 생성 {shown['created']}/{counts['created']}, 수정 {shown['modified']}/{counts['modified']}, "
            f"삭제 {shown['deleted']}/{counts['deleted']}. {how}")
