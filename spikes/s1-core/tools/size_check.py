# SPDX-License-Identifier: MPL-2.0
"""응답 크기 실측: 큰 모델에서 목록형 조회와 큰 변경(배치)의 최악 응답이 8,000토큰 안에 드는지 잰다.

- 토큰은 tiktoken(cl100k_base, o200k_base)으로 센 대용 측정이다. Claude 토크나이저로는 재지 못했다(결과 파일에 같은 말을 적는다).
  tiktoken 은 개발 의존이다(pyproject 의 [tokens]/[dev]): 코어 런타임 의존이 아니다.
- 한 응답의 크기 = text(봉투 JSON) + structuredContent(같은 봉투). 두 토크나이저 모두 8,000 이하여야 통과다.
- 모델은 토큰 효율이 가장 나쁜 쪽으로 만든다: 무작위 GlobalId(영숫자+_$), 영문과 한글 64자 이름 두 가지.
- 기본은 실제 stdio MCP 서버와 공식 SDK 클라이언트로 잰다(선 위의 text 와 structuredContent). --inprocess 는 상한 조정용 빠른 경로다.

사용: python tools/size_check.py results/s1_response_sizes.json [--inprocess] [--cap BYTES]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit  # noqa: E402
from dobim_core import dispatch, limits, mcp_server  # noqa: E402

TARGET_TOKENS = 8000
ENCODINGS = ("cl100k_base", "o200k_base")
T_REQUIRED = ["GUID", "영문", "실제 한국어", "무작위 한글", "한자", "이모지", "제어문자", "숫자-기호 교대", "문자-숫자 교대"]
N_WALL, N_DOOR, N_MOVE = 500, 250, 60


def rid(prefix: str, r: random.Random) -> str:
    return prefix + "".join(r.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$") for _ in range(21))


def build(work: Path, korean: bool) -> dict:
    """모델을 만든다. 돌려주는 값: 프로젝트 파일 이름과 시험에 쓸 id 들."""
    r = random.Random(5)
    name = f"size-{'ko' if korean else 'en'}.dobim"
    s = kit.Session(work / name)
    lv = rid("3", r)
    cmds = [{"command": "level.create", "args": {"id": lv, "name": "가" * 60 if korean else "Level-1", "elevation": 0}}]
    walls = []
    for i in range(N_WALL):
        w = rid("1", r)
        walls.append(w)
        cmds.append({"command": "wall.create", "args": {"id": w, "level": lv, "axis": [0, i * 300, 6000, i * 300],
                                                        "name": ("벽" * 63 + "x") if korean else None}})
    for j in range(N_DOOR):
        cmds.append({"command": "door.place", "args": {"id": rid("2", r), "host": walls[j], "offset": 1500, "width": 900,
                                                       "name": ("문" * 63 + "y") if korean else None}})
    assert s.batch(cmds)["ok"]
    for k in range(N_MOVE):
        assert s.run("wall.move", {"id": walls[k], "dx": 1})["ok"]
    s.close()
    return {"project": name, "level": lv, "walls": walls, "random": r}


def plans(m: dict) -> list:
    """(이름, 도구, 커맨드, 인자, 이어받기 키 추출자 또는 None)"""
    q = "dobim_query"
    return [("element.query", q, "element.query", {"limit": 1000}),
            ("schedule.rows 벽", q, "schedule.rows", {"kind": "wall", "limit": 1000}),
            ("schedule.rows 문", q, "schedule.rows", {"kind": "door", "limit": 1000}),
            ("history.list", q, "history.list", {"limit": 500}),
            ("relation.list", q, "relation.list", {"id": m["level"], "limit": 1000}),
            ("commands.search", "dobim_commands_search", None, {"limit": 1000})]


def mutation_plans(m: dict) -> list:
    r = m["random"]
    ids = [rid("1", r) for _ in range(600)]
    return [("배치 wall.create x600", {"commands": [{"command": "wall.create", "args": {"id": i, "level": m["level"],
             "axis": [0, 500000 + n * 300, 6000, 500000 + n * 300]}} for n, i in enumerate(ids)]}),
            ("배치 wall.move x400(수정 400)", {"commands": [{"command": "wall.move", "args": {"id": w, "dx": 1}} for w in m["walls"][:400]]}),
            ("history.undo(배치 600 되돌리기)", None)]


def tokens(encs: dict, text: str, sc: dict) -> dict:
    """text + structuredContent 합계. structuredContent 는 선 위에서 compact JSON 이지만 간격 있는 JSON 도 세어 큰 쪽을 쓴다."""
    sc_compact = json.dumps(sc, ensure_ascii=False, separators=(",", ":"))
    sc_spaced = json.dumps(sc, ensure_ascii=False)
    out = {}
    for n, e in encs.items():
        out[n] = len(e.encode(text)) + max(len(e.encode(sc_compact)), len(e.encode(sc_spaced)))
    return out


class InProc:
    """상한 조정용: 서버 안의 dispatch 를 직접 부른다(선 위의 text 는 mcp_server._result 와 같은 직렬화)."""

    def __init__(self, work: Path):
        self.env = dispatch.Env(work, "size")

    async def call(self, tool: str, args: dict):
        if tool == "dobim_commands_search":
            env = dispatch.run(self.env, "commands.search", {k: args.get(k) for k in ("limit", "cursor") if k in args})
        elif tool == "dobim_query":
            env = dispatch.run(self.env, args["command"], args.get("args"), project=args["project"], allowed_risks={"read"})
        elif args.get("commands"):
            env = dispatch.run_batch(self.env, args["commands"], project=args["project"], allowed_risks={"write"})
        else:
            env = dispatch.run(self.env, args["command"], args.get("args"), project=args["project"], allowed_risks={"write"})
        res = mcp_server._result(env)
        return res.content[0].text, res.structured_content

    async def close(self):
        self.env.close()


class Wire:
    """실제 stdio MCP 서버 + 공식 SDK 클라이언트."""

    def __init__(self, work: Path):
        from mcp import Client, StdioServerParameters
        self.params = StdioServerParameters(command=sys.executable, args=["-m", "dobim_core", "--workspace", str(work), "mcp", "serve"],
                                            env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")},
                                            cwd=str(work))
        self.cm = Client(self.params)

    async def __aenter__(self):
        self.c = await self.cm.__aenter__()
        return self

    async def __aexit__(self, *a):
        await self.cm.__aexit__(*a)

    async def call(self, tool: str, args: dict):
        r = await self.c.call_tool(tool, args)
        return r.content[0].text, r.structured_content


async def measure(caller, encs, m: dict, lang: str) -> list:
    rows = []
    proj = m["project"]
    for label, tool, cmd, args in plans(m):
        cursor, pages, worst, seen, total_items, trunc = None, 0, None, [], 0, 0
        while True:
            a = dict(args, **({"cursor": cursor} if cursor else {}))
            call_args = a if tool == "dobim_commands_search" else {"command": cmd, "args": a, "project": proj}
            text, sc = await caller.call(tool, call_args)
            env = sc
            assert env["ok"], (label, env)
            t = tokens(encs, text, sc)
            b = len(text.encode("utf-8")) + len(json.dumps(sc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            if worst is None or max(t.values()) > max(worst["tokens"].values()):
                worst = {"bytes_text_plus_structured": b, "text_bytes": len(text.encode("utf-8")), "tokens": t}
            pages += 1
            trunc += bool(env["page"]["truncated"])
            items = env["result"]["items"]
            total_items += len(items)
            cursor = env["page"]["next_cursor"]
            if not cursor:
                break
        rows.append({"what": label, "lang": lang, "pages_to_read_all": pages, "truncated_pages": trunc,
                     "items_total": total_items, "worst": worst})
    # 큰 변경: 응답이 상한 안에 들고 affected.counts 가 정확한지
    for label, a in mutation_plans(m)[:2]:
        text, sc = await caller.call("dobim_execute", dict(a, project=proj))
        assert sc["ok"], (label, sc.get("error"))
        t = tokens(encs, text, sc)
        aff = sc["affected"]
        rows.append({"what": label, "lang": lang, "kind": "mutation",
                     "worst": {"bytes_text_plus_structured": len(text.encode()) + len(json.dumps(sc, ensure_ascii=False, separators=(',', ':')).encode()),
                               "text_bytes": len(text.encode()), "tokens": t},
                     "affected_counts": aff["counts"], "affected_shown": {k: len(aff[k]) for k in ("created", "modified", "deleted")},
                     "affected_truncated": aff["truncated"], "results_truncated": bool(sc.get("page") and sc["page"]["truncated"])})
        # 이어받기: history.affected 로 전체를 읽어 counts 와 맞는지
        rev, cursor, got = sc["rev"], None, []
        while True:
            a2 = {"rev": rev, "limit": 1000, **({"cursor": cursor} if cursor else {})}
            _, e2 = await caller.call("dobim_query", {"command": "history.affected", "args": a2, "project": proj})
            assert e2["ok"], e2
            got += [(i["change"], i["id"]) for i in e2["result"]["items"]]
            cursor = e2["page"]["next_cursor"]
            if not cursor:
                break
        want = sum(aff["counts"].values())
        rows[-1]["history_affected_items"] = len(got)
        rows[-1]["history_affected_unique"] = len(set(got))
        rows[-1]["history_affected_matches_counts"] = len(got) == want == len(set(got))
    text, sc = await caller.call("dobim_execute", {"command": "history.undo", "args": {}, "project": proj})   # 마지막 배치(수정 400)를 되돌림
    rows.append({"what": "history.undo(배치 400)", "lang": lang, "kind": "mutation",
                 "worst": {"text_bytes": len(text.encode()), "tokens": tokens(encs, text, sc)}, "ok": sc["ok"],
                 "ops_total": sc["result"].get("ops_total"), "ops": sc["result"].get("ops")})
    return rows


async def run(out: str, inproc: bool, cap: int | None, all_classes: bool = False) -> int:
    import tiktoken
    if cap:
        limits.RESPONSE_CAP_BYTES = cap
    encs = {n: tiktoken.get_encoding(n) for n in ENCODINGS}
    work = Path(tempfile.mkdtemp(prefix="dobim-size-"))
    rows = []
    models = []
    for korean in (False, True):
        models.append(("한글" if korean else "영문", build(work, korean)))
    import token_campaign as T   # 문자 종류별 적대 모델: 이름 64자·속성값 255자를 종류별로 채운다(합격 기준의 일곱 종류 + 교대 패턴)
    rnd = random.Random(21)
    for cname, gen in T.classes(rnd).items():
        wanted = (T.REQUIRED + ("숫자-기호 교대", "문자-숫자 교대")) if all_classes else ("무작위 한글", "이모지", "제어문자")
        if cname in wanted:
            cm = T.build(work, cname, gen, rnd, n_wall=420, n_door=150, n_pset=40)
            cm["s"].close()
            models.append((cname, {"project": cm["s"].path.name, "level": cm["level"], "walls": cm["walls"], "random": rnd}))
    for lang, m in models:
        if inproc:
            c = InProc(work)
            rows += await measure(c, encs, m, lang)
            await c.close()
        else:
            async with Wire(work) as c:
                rows += await measure(c, encs, m, lang)
    worst = max(max(r["worst"]["tokens"].values()) for r in rows)
    res = {"method": "tiktoken 대용 측정(Claude 토크나이저로는 재지 못했다). 한 응답 = text + structuredContent(두 벌), 두 토크나이저 모두 8,000 이하여야 통과",
           "transport": "in-process dispatch" if inproc else "stdio MCP 서버 + 공식 SDK 클라이언트",
           "tiktoken": tiktoken.__version__, "encodings": list(ENCODINGS), "python": platform.python_version(),
           "model": {"walls": N_WALL, "doors": N_DOOR, "random_guid": True,
                     "names": ["영문", "한글 64자"] + ["문자 종류별 이름 64자·값 255자: " + ", ".join(T_REQUIRED if all_classes else ["무작위 한글", "이모지", "제어문자"])]},
           "cap": {"RESPONSE_CAP_BYTES": limits.RESPONSE_CAP_BYTES, "RESPONSE_CAP_TOKENS": limits.RESPONSE_CAP_TOKENS},
           "target_tokens": TARGET_TOKENS, "worst_tokens_any": worst, "rows": rows,
           "pass": worst <= TARGET_TOKENS and all(r.get("history_affected_matches_counts", True) for r in rows)}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"cap": res["cap"], "worst_tokens_any": worst, "pass": res["pass"]}, ensure_ascii=False))
    for r in rows:
        print(f"{r['lang']} {r['what']:28s} pages={r.get('pages_to_read_all', '-')} worst_tokens={r['worst']['tokens']}", flush=True)
    return 0 if res["pass"] else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--inprocess", action="store_true")
    ap.add_argument("--cap", type=int, default=None)
    ap.add_argument("--all-classes", action="store_true", help="문자 종류별 모델을 모두 잰다(기본은 무작위 한글·이모지·제어문자만: 시간 절약)")
    ns = ap.parse_args()
    return asyncio.run(run(ns.out, ns.inprocess, ns.cap, ns.all_classes))


if __name__ == "__main__":
    sys.exit(main())
