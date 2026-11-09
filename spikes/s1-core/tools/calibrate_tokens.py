# SPDX-License-Identifier: MPL-2.0
"""토큰 추정식(limits.est_tokens)의 보정 근거를 실측으로 남긴다.

1) 문자 종류별 미시 실측: 길이 8·22·64·255 의 문자열(JSON 직렬화)을 문자 종류별로 만들어 tiktoken(cl100k_base, o200k_base) 실측 토큰과
   추정식을 비교한다. 추정은 모든 종류·길이에서 실측 이상이어야 한다(실측/추정 비율의 최댓값이 1 이하).
2) 응답 단위 거시 실측: tools/token_campaign.py 가 문자 종류별 모델의 모든 목록형 조회·큰 변경 응답을 끝까지 읽어, 응답마다
   text + structuredContent 합계 토큰(대용 측정)과 2 x 추정 토큰의 비율, 실제 바이트, 8,000토큰 초과 여부를 잰다.

tiktoken 은 개발 의존이다(코어 런타임에는 없다). Claude 토크나이저로는 재지 못했다(대용 측정).
사용: python tools/calibrate_tokens.py results/s1_token_calibration.json
"""
from __future__ import annotations

import json
import platform
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import token_campaign as T  # noqa: E402
from dobim_core import limits  # noqa: E402


def micro(encs: dict) -> dict:
    r = random.Random(7)
    rows = {}
    for cname, gen in T.classes(r).items():
        worst = {"ratio": 0.0, "n": 0}
        for n in (8, 22, 64, 255):
            for _ in range(60):
                s = gen(n)
                est = limits.est_tokens(s)
                for e in encs.values():
                    ratio = len(e.encode(json.dumps(s, ensure_ascii=False))) / est
                    if ratio > worst["ratio"]:
                        worst = {"ratio": round(ratio, 3), "n": n}
        rows[cname] = worst
    return rows


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else None
    import tiktoken
    encs = T.encoders()
    t0 = time.time()
    mi = micro(encs)
    rows, viol = T.run(Path(tempfile.mkdtemp(prefix="dobim-calib-")), None, encs)
    by_class = {}
    for row in rows:
        d = by_class.setdefault(row["class"], {"worst_tokens": 0, "worst_what": "", "worst_bytes": 0, "max_actual_over_est": 0.0, "responses": 0})
        d["responses"] += 1
        tk = max(row["tokens"].values())
        if tk > d["worst_tokens"]:
            d["worst_tokens"], d["worst_what"] = tk, row["what"]
        d["worst_bytes"] = max(d["worst_bytes"], row["bytes"])
        d["max_actual_over_est"] = max(d["max_actual_over_est"], row["ratio"])
    res = {
        "method": "tiktoken 대용 측정(Claude 토크나이저로는 재지 못했다). 한 응답 = text + structuredContent(두 벌). 추정은 모든 문자 종류에서 실측 이상이어야 한다",
        "tiktoken": tiktoken.__version__, "python": platform.python_version(),
        "limits": {"RESPONSE_CAP_BYTES": limits.RESPONSE_CAP_BYTES, "RESPONSE_CAP_TOKENS": limits.RESPONSE_CAP_TOKENS, "STR_MAX": limits.STR_MAX,
                   "weights": {"ascii_char": limits.W_ASCII_CHAR, "ascii_run": limits.W_ASCII_RUN, "ctrl_extra": limits.W_CTRL_EXTRA,
                               "quote_extra": limits.W_QUOTE_EXTRA, "non_ascii": "UTF-8 바이트 수", "str_quotes": limits.W_STR_QUOTES,
                               "dict_entry": limits.W_DICT_ENTRY, "list_entry": limits.W_LIST_ENTRY, "container": limits.W_CONTAINER}},
        "micro_worst_actual_over_est_by_class": mi,
        "micro_pass": all(v["ratio"] <= 1.0 for v in mi.values()),
        "campaign_by_class": by_class,
        "campaign_worst_tokens_any": max(d["worst_tokens"] for d in by_class.values()),
        "campaign_max_actual_over_est": max(d["max_actual_over_est"] for d in by_class.values()),
        "campaign_worst_bytes_any": max(d["worst_bytes"] for d in by_class.values()),
        "violations": viol[:30], "target_tokens": T.TARGET_TOKENS, "elapsed_sec": round(time.time() - t0, 1),
    }
    res["pass"] = res["micro_pass"] and not viol
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("campaign_by_class", "micro_worst_actual_over_est_by_class")}, ensure_ascii=False, indent=1))
    for k, v in mi.items():
        print(f"micro {k:16s} 최대 실측/추정 {v['ratio']} (n={v['n']})")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
