# SPDX-License-Identifier: MPL-2.0
"""토큰 적대 캠페인: 이름·속성값을 문자 종류별(GUID, 영문, 실제 한국어, 무작위 한글, 한자, 이모지, 제어문자 등)로 채운 모델에서
모든 목록형 조회와 큰 변경 응답을 끝까지 읽어, 한 응답의 text + structuredContent 합계 토큰(tiktoken cl100k·o200k 대용 측정)과
실제 직렬화 바이트를 잰다. 응답 크기 상한(limits)이 문자 종류에 상관없이 지켜지는지의 증거이자 추정식 보정의 입력이다.

tiktoken 은 개발 의존이다(코어 런타임에는 없다). Claude 토크나이저로는 재지 못했다(대용 측정).
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit  # noqa: E402
from dobim_core import dispatch, limits  # noqa: E402

ENCODINGS = ("cl100k_base", "o200k_base")
REQUIRED = ("GUID", "영문", "실제 한국어", "무작위 한글", "한자", "이모지", "제어문자")   # 합격 기준의 일곱 종류
TARGET_TOKENS = 8000
_AN = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
_ASC = "!#$%&()*+,-./:;<=>?@[]^_{|}~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_BS = chr(92)
_KO_WORDS = "서울특별시 강남구 테헤란로 건축물 방화문 외벽 마감재료 지하주차장 계단실 승강기 기계실 옥상 출입구 내화구조 "


def classes(r: random.Random) -> dict:
    """문자 종류별 문자열 생성기(길이 n). 합격 기준의 일곱 종류 + 조임용 변종."""
    def rr(a, b):
        return lambda n: "".join(chr(r.randint(a, b)) for _ in range(n))
    return {
        "GUID": lambda n: "".join(r.choice(_AN) for _ in range(n)),
        "영문": lambda n: ("Exterior Wall Type A level door fire rating " * 10)[:n],
        "실제 한국어": lambda n: (_KO_WORDS * 10)[:n],
        "무작위 한글": rr(0xAC00, 0xD7A3),
        "한자": rr(0x4E00, 0x9FFF),
        "이모지": rr(0x1F300, 0x1F64F),
        "제어문자": lambda n: "".join(chr(r.randint(1, 31)) for _ in range(n)),
        "ASCII 기호": lambda n: "".join(r.choice(_ASC) for _ in range(n)),
        "따옴표 역슬래시": lambda n: (('"' + _BS) * n)[:n],
        "문자-숫자 교대": lambda n: "".join(r.choice("abcdefghijklmnopqrstuvwxyz") + r.choice("0123456789") for _ in range(n))[:n],
        "숫자-기호 교대": lambda n: "".join(r.choice("0123456789") + r.choice(".-_/") for _ in range(n))[:n],
        "무작위 소수(좌표)": lambda n: "".join(r.choice(_AN) for _ in range(n)),   # 이름은 GUID, 축선 좌표를 자릿수 많은 무작위 소수로 채운다
        "무작위 BMP": rr(0x80, 0xD7FF),
        "무작위 비BMP": rr(0x10000, 0x1FFFF),
    }


def rid(prefix: str, r: random.Random) -> str:
    return prefix + "".join(r.choice(_AN) for _ in range(21))


def encoders():
    import tiktoken
    return {n: tiktoken.get_encoding(n) for n in ENCODINGS}


def total_tokens(encs: dict, env: dict) -> dict:
    """text(간격 있는 JSON) + structuredContent(선 위에서는 compact, 간격 있는 쪽도 세어 큰 값). 토크나이저별 합계."""
    text = json.dumps(env, ensure_ascii=False)
    compact = json.dumps(env, ensure_ascii=False, separators=(",", ":"))
    return {n: len(e.encode(text)) + max(len(e.encode(compact)), len(e.encode(text))) for n, e in encs.items()}


def build(work: Path, tag: str, gen, r: random.Random, n_wall=300, n_door=150, n_pset=60) -> dict:
    floats = tag.startswith("무작위 소수")
    s = kit.Session(Path(work) / f"camp-{abs(hash(tag))}.dobim")
    lv = rid("3", r)
    cmds = [{"command": "level.create", "args": {"id": lv, "name": gen(64), "elevation": 0}}]
    walls = []
    for i in range(n_wall):
        w = rid("1", r)
        walls.append(w)
        cmds.append({"command": "wall.create", "args": {"id": w, "level": lv, "axis": [r.uniform(0, 1e5), r.uniform(0, 1e5), r.uniform(0, 1e5), r.uniform(0, 1e5)] if floats else [0, i * 300, 6000, i * 300],
                                                        "name": gen(64)}})
    for j in range(n_door):
        cmds.append({"command": "door.place", "args": {"id": rid("2", r), "host": walls[j], "offset": 1500, "width": 900, "name": gen(64)}})
    if floats:   # 호스트 벽이 문 폭보다 짧을 수 있어 문은 건너뛴다
        cmds = [c for c in cmds if c["command"] != "door.place"]
    assert s.batch(cmds)["ok"], tag
    for i in range(n_pset):   # 일람표에 실리는 방화등급(255자) + 한 요소의 속성 수십 개(element.get 쪽 나누기)
        assert s.run("pset.set", {"id": walls[i], "pset": "Pset_WallCommon", "prop": "FireRating", "value": gen(255)})["ok"]
        assert s.run("material.set", {"id": walls[i], "name": gen(64)})["ok"]
        assert s.run("pset.set", {"id": walls[0], "pset": f"Pset_{i}", "prop": "P", "value": gen(255)})["ok"]
    return {"s": s, "level": lv, "walls": walls}


def drain(s, name: str, base: dict, label: str):
    """커서를 끝까지 따라가며 (라벨, 봉투) 를 낸다."""
    cur = None
    while True:
        env = s.run(name, dict(base, **({"cursor": cur} if cur else {})))
        yield label, env
        if not env["ok"]:
            return
        cur = env["page"]["next_cursor"] if env.get("page") else None
        if not cur:
            return


def responses(m: dict, gen, r: random.Random):
    """한 모델에서 시험할 모든 응답을 차례로 낸다: (라벨, 봉투)."""
    s, lv, walls = m["s"], m["level"], m["walls"]
    rev0 = s.eng.rev
    for name, base in (("element.query", {"limit": 1000}), ("schedule.rows", {"kind": "wall", "limit": 1000}),
                       ("schedule.rows", {"kind": "door", "limit": 1000}), ("history.list", {"limit": 500}),
                       ("relation.list", {"id": lv, "limit": 1000}), ("commands.search", {"limit": 1000}),
                       ("element.get", {"id": lv, "limit": 1000}), ("element.get", {"id": walls[0], "limit": 1000}),
                       ("element.get", {"id": walls[1], "limit": 1000})):
        yield from drain(s, name, base, f"{name}{base.get('kind', '')}")
    big = [{"command": "wall.create", "args": {"level": lv, "axis": [0, i * 10 + 99000, 6000, i * 10 + 99000], "name": gen(64)}} for i in range(400)]
    yield "배치 dry_run", s.batch(big, dry_run=True)
    env = s.batch(big)
    yield "배치 400", env
    yield from drain(s, "history.affected", {"rev": env["rev"], "limit": 1000}, "history.affected")
    yield "undo", s.run("history.undo")
    yield "redo", s.run("history.redo")
    yield "변경 없는 배치 1000", s.batch([{"command": "wall.move", "args": {"id": walls[2], "dx": 0}} for _ in range(1000)])
    yield "요소 삭제(속성 많은 벽)", s.run("element.delete", {"id": walls[0]})
    yield "undo(삭제)", s.run("history.undo")
    items = [{"command": "wall.create", "args": {"level": lv, "axis": [0, i * 10 + 199000, 6000, i * 10 + 199000], "name": gen(64)}} for i in range(120)]
    items.insert(80, {"command": "wall.move", "args": {"id": "9" * 22, "dx": 1}})   # 중간 실패
    yield "script 중간 실패", dispatch.run_script(s.env, items, project=s.path.name)
    assert s.eng.rev >= rev0


def run(work: Path, selected: list[str] | None = None, encs: dict | None = None, seed: int = 11) -> tuple[list[dict], list[str]]:
    """(행 목록, 위반 목록). 행: 종류·라벨·바이트·토큰·추정. 위반: 8,000토큰 초과 또는 바이트 상한 초과."""
    encs = encoders() if encs is None else encs   # {} 는 토큰을 재지 않고 바이트 상한만 본다(tiktoken 이 없을 때)
    r = random.Random(seed)
    rows, viol = [], []
    for cname, gen in classes(r).items():
        if selected and cname not in selected:
            continue
        m = build(work, cname, gen, r)
        for label, env in responses(m, gen, r):
            if not env.get("ok") and label != "script 중간 실패":
                viol.append(f"{cname}/{label}: 실패 {env.get('error')}")
                continue
            text = json.dumps(env, ensure_ascii=False)
            nb, tk = len(text.encode("utf-8")), (total_tokens(encs, env) if encs else {})
            est = 2 * limits.est_tokens(env)
            row = {"class": cname, "what": label, "bytes": nb, "tokens": tk, "est_x2": round(est), "ratio": round(max(tk.values()) / est, 3) if tk else None}
            if tk and max(tk.values()) > TARGET_TOKENS:
                viol.append(f"{cname}/{label}: {max(tk.values())}토큰 > {TARGET_TOKENS} {tk} ({nb}B)")
            if nb > limits.RESPONSE_CAP_BYTES:
                viol.append(f"{cname}/{label}: {nb}바이트 > 상한 {limits.RESPONSE_CAP_BYTES}")
            rows.append(row)
        m["s"].close()
    return rows, viol
