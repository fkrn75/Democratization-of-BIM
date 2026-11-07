# SPDX-License-Identifier: MPL-2.0
"""응답 크기 측정: 큰 모델(벽 600, 문 300)에서 목록형 커맨드의 최악 응답이 상한 안에 드는지, 이어받기로 전체를 읽는 데
쪽이 몇 개 드는지 잰다. 토큰은 UTF-8 바이트/3 으로 추정한 값이다(정밀 토크나이저가 없어 보수적으로 잡았고 추정임을 밝힌다).
같은 봉투가 MCP 의 text 와 structuredContent 에 한 벌씩 실려 선 위의 바이트는 약 2배다.

사용: python tools/size_check.py results/s1_response_sizes.json
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit  # noqa: E402
from dobim_core import commands, fixture as F  # noqa: E402

N_WALL, N_DOOR = 600, 300
TARGET_TOKENS = 8000


def build(path: Path) -> kit.Session:
    s = kit.Session(path)
    cmds = [{"command": "level.create", "args": {"id": F.LV1, "name": "1F", "elevation": 0}}]
    walls = []
    for i in range(N_WALL):
        wid = "1" + f"{i + 1:021d}"
        walls.append(wid)
        cmds.append({"command": "wall.create", "args": {"id": wid, "level": F.LV1, "axis": [0, i * 300, 6000, i * 300],
                                                        "thickness": 200, "height": 3000}})
    for j in range(N_DOOR):
        cmds.append({"command": "door.place", "args": {"id": "2" + f"{j + 1:021d}", "host": walls[j], "offset": 1500,
                                                       "width": 900, "height": 2100}})
    assert s.batch(cmds)["ok"]
    for k in range(40):   # 이력이 길어지도록 개별 변경을 더한다
        assert s.run("wall.move", {"id": walls[k], "dx": 10})["ok"]
    return s


def size_of(env: dict) -> dict:
    text = json.dumps(env, ensure_ascii=False, allow_nan=False)
    b = len(text.encode("utf-8"))
    return {"text_bytes": b, "est_tokens_text": round(b / 3), "est_tokens_text_plus_structured": round(2 * b / 3)}


def main(out: str) -> int:
    work = Path(tempfile.mkdtemp(prefix="dobim-size-"))
    s = build(work / "big.dobim")
    rows = []
    walls_level = F.LV1
    plans = [("element.query", {"limit": 1000}, "id"), ("element.query", {}, None),
             ("schedule.rows", {"kind": "wall", "limit": 1000}, 0), ("schedule.rows", {"kind": "door", "limit": 1000}, 0),
             ("history.list", {"limit": 500}, "seq"), ("history.list", {}, None),
             ("relation.list", {"id": walls_level, "limit": 1000}, "src"), ("commands.search", {"limit": 1000}, "name")]
    for name, base, key in plans:
        cursor, pages, total_items, sizes, trunc_pages = None, 0, 0, [], 0
        seen = []
        while True:
            r = s.run(name, dict(base, **({"cursor": cursor} if cursor else {})))
            assert r["ok"], r
            pages += 1
            sizes.append(size_of(r))
            trunc_pages += bool(r["page"]["truncated"])
            items = r["result"]["items"]
            total_items += len(items)
            if key is not None:
                seen += [it[key] if isinstance(key, str) else it[key] for it in items]
            cursor = r["page"]["next_cursor"]
            if not cursor:
                break
        worst = max(sizes, key=lambda z: z["text_bytes"])
        rows.append({"command": name, "args": base, "pages_to_read_all": pages, "truncated_pages": trunc_pages,
                     "items_total": total_items, "page_total_field": r["page"]["total"], "no_gap_no_dup": (len(seen) == len(set(seen)) == total_items) if key is not None else None,
                     "worst_page": worst, "within_8000_tokens_text": worst["est_tokens_text"] <= TARGET_TOKENS,
                     "within_8000_tokens_text_plus_structured": worst["est_tokens_text_plus_structured"] <= TARGET_TOKENS})
        print(name, base, pages, worst, flush=True)
    res = {"model": {"walls": N_WALL, "doors": N_DOOR, "log_entries": s.eng.rev},
           "cap": {"RESPONSE_CAP_BYTES": commands.RESPONSE_CAP_BYTES, "ENVELOPE_RESERVE_BYTES": commands.ENVELOPE_RESERVE_BYTES},
           "token_estimate": "UTF-8 바이트 / 3 (추정)", "target_tokens": TARGET_TOKENS, "rows": rows,
           "all_within_target": all(r["within_8000_tokens_text_plus_structured"] for r in rows),
           "all_no_gap_no_dup": all(r["no_gap_no_dup"] in (None, True) for r in rows)}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    s.close()
    return 0 if res["all_within_target"] and res["all_no_gap_no_dup"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
