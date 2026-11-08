# SPDX-License-Identifier: MPL-2.0
"""6차 검수 후속(응답 크기·잘림·오류 분류·SQL 쓰기 실패) 검사 모음. 위반 문자열 목록을 돌려준다(빈 목록 = 통과).

golden.py 의 손계산 검사와 별개로 둔다: 변이 시험은 골든을 끈 상태로도 이 검사를 돌려, 이 항목의 변이가 골든 함수 밖에서도 잡히는지 보인다.
tests/test_review6.py 와 tools/run_mutants.py 가 같은 함수를 부른다.
"""
from __future__ import annotations

import errno
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit  # noqa: E402
from dobim_core import dispatch, fixture as F, limits  # noqa: E402


def _size(env: dict) -> int:
    return len(json.dumps(env, ensure_ascii=False).encode("utf-8"))


def _mk(i: int, p: str = "1") -> str:
    return p + f"{i:021d}"


def _sess(work: Path, tag: str) -> kit.Session:
    return kit.Session(Path(tempfile.mkdtemp(dir=Path(work), prefix=tag + "-")) / "p.dobim")


def _walls(n: int, level: str, start: int = 1, **extra) -> list[dict]:
    return [{"command": "wall.create", "args": {"id": _mk(start + i), "level": level, "axis": [0, i * 10, 6000, i * 10], **extra}} for i in range(n)]


def _confirmed(s, name: str, args: dict) -> dict:
    r = s.run(name, args)
    if r["ok"] or r["error"]["code"] != "CONFIRM_REQUIRED":
        return r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


# ---------------------------------------------------------------- 1. 공유 가변 기본값
def check_shared_state(work: Path) -> list[str]:
    """응답 하나의 affected 를 고쳐도 이후 응답이 오염되지 않는다(공유 가변 객체 금지)."""
    v = []
    s = _sess(work, "shared")
    for n, a in F.SCRIPT:
        s.run(n, a)
    a = s.run("project.info")
    a["affected"]["created"].append("X")
    a["affected"]["counts"]["created"] = 99
    a["affected"]["truncated"] = True
    a["warnings"].append("w")
    for r in (s.run("project.info"), s.run("model.stats"), s.run("element.get", {"id": F.W1})):
        if r["affected"] != limits.empty_affected():
            v.append(f"앞 응답을 고쳤더니 다음 응답의 affected 가 오염됐다: {r['affected']}")
        if r["warnings"] and "w" in r["warnings"]:
            v.append("warnings 가 응답 사이에 공유된다")
    if limits.empty_affected() is limits.empty_affected() or limits.empty_affected()["counts"] is limits.empty_affected()["counts"]:
        v.append("empty_affected() 가 호출마다 새 객체를 돌려주지 않는다")
    d1, d2 = s.run("commands.describe", {"name": "wall.move"}), s.run("commands.describe", {"name": "wall.move"})
    d1["result"]["examples"][0]["id"] = "mutated"
    if d2["result"]["examples"][0]["id"] == "mutated" or s.run("commands.describe", {"name": "wall.move"})["result"]["examples"][0]["id"] == "mutated":
        v.append("commands.describe 의 예시가 레지스트리와 공유된다")
    s.close()
    return v


# ---------------------------------------------------------------- 2. 경고 상한
def check_warning_cap(work: Path) -> list[str]:
    """변경 없는 명령 배치도 경고까지 포함해 상한 안이고, 같은 경고는 건수로 묶인다."""
    v = []
    s = _sess(work, "warn")
    for n, a in F.SCRIPT:
        s.run(n, a)
    noop = [{"command": "wall.move", "args": {"id": F.W1, "dx": 0}} for _ in range(1000)]
    for label, kw in (("실행", {}), ("dry_run", {"dry_run": True})):
        r = s.batch(noop, **kw)
        if not r["ok"]:
            v.append(f"변경 없는 배치({label})가 실패: {r['error']}")
            continue
        if _size(r) > limits.RESPONSE_CAP_BYTES:
            v.append(f"변경 없는 배치 1000건({label}) 응답이 {_size(r)}바이트로 상한 {limits.RESPONSE_CAP_BYTES} 을 넘는다")
        w = [x for x in r["warnings"] if "변경 없음" in x]
        if len(w) != 1 or "1000" not in w[0]:
            v.append(f"같은 경고가 한 줄로 묶이지 않았다({label}): {r['warnings'][:3]}")
        if len(r["warnings"]) > limits.MAX_WARNING_LINES + 1:
            v.append(f"경고 줄 수가 상한을 넘는다({label}): {len(r['warnings'])}")
    # 행위자 이름이 긴 되돌리기 경고도 한 줄 길이 상한을 지킨다
    s.eng.actor = s.env.actor = "행" * 3000
    s.run("wall.move", {"id": F.W2, "dx": 5})
    s.eng.actor = s.env.actor = "t"
    u = s.run("history.undo")
    long_w = [w for w in u["warnings"] if "다른 행위자" in w]
    if not long_w or any(len(w) > limits.MAX_WARNING_CHARS for w in u["warnings"]) or _size(u) > limits.RESPONSE_CAP_BYTES:
        v.append(f"긴 행위자 이름의 경고가 길이 상한을 넘는다: {[len(w) for w in u['warnings']]}")
    s.close()
    return v


# ---------------------------------------------------------------- 3·4. 엄밀한 상한과 토큰(문자 종류별)
REQUIRED_CLASSES = ["GUID", "영문", "실제 한국어", "무작위 한글", "한자", "이모지", "제어문자"]


def check_token_classes(work: Path, selected: list[str] | None = None) -> list[str]:
    """이름 64자·속성값 255자를 문자 종류별로 채운 모델의 모든 목록형 조회와 큰 변경 응답이 (1) 실제 직렬화 바이트로 상한 안이고
    (2) tiktoken cl100k·o200k 로 text + structuredContent 합계 8,000토큰 이내다. tiktoken 이 없으면 바이트 상한만 본다."""
    import token_campaign as T
    try:
        encs = T.encoders()
    except ImportError:
        encs = {}
    return T.run(Path(tempfile.mkdtemp(dir=Path(work), prefix="camp-")), selected or REQUIRED_CLASSES, encs)[1]


# ---------------------------------------------------------------- 5. 잘린 배치 results
def check_batch_results(work: Path) -> list[str]:
    """잘린 배치 results 의 항목마다 명령 번호(index)와 로그 번호(seq)가 있고, page 가 이어 읽는 방법을 알려 주며,
    history.affected 로 이어받으면 모든 명령의 결과(id)를 명령 순서대로 얻는다."""
    v = []
    s = _sess(work, "bres")
    s.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    n = 300
    ids = [_mk(i + 1) for i in range(n)]
    # 중간에 변경 없는 명령을 끼워 index 와 seq 가 어긋나는 경우도 본다
    cmds = []
    for i, c in enumerate(_walls(n, F.LV1)):
        cmds.append(c)
        if i in (3, 4, 100):
            cmds.append({"command": "wall.move", "args": {"id": ids[0], "dx": 0}})
    r = s.batch(cmds)
    if not r["ok"]:
        return [f"배치 실패: {r['error']}"]
    res, pg = r["result"], r["page"]
    if not pg or pg.get("truncated") is not True or pg.get("total") != len(cmds) or pg.get("shown") != len(res):
        v.append(f"잘린 배치의 page 가 틀렸다: {pg}")
    if pg and pg.get("continue") != {"command": "history.affected", "args": {"rev": r["rev"]}}:
        v.append(f"page.continue 가 이어 읽는 방법을 알려 주지 않는다: {pg.get('continue')}")
    if [x.get("index") for x in res] != list(range(len(res))):
        v.append(f"results 항목에 명령 번호(index)가 없거나 어긋난다: {[x.get('index') for x in res][:6]}")
    if any("seq" not in x for x in res):
        v.append("results 항목에 seq 가 없다")
    noop_idx = {4, 6, 103}   # 끼워 넣은 변경 없는 명령의 index (벽 i=3, 4, 100 뒤)
    for x in res:
        if (x["index"] in noop_idx) != (x["seq"] is None):
            v.append(f"변경 없는 명령의 seq 표시가 틀렸다: {x}")
            break
    # 이어받기: 만들어진 벽 전체가 명령 순서대로 나오고 seq 가 results 와 맞는다
    got, cursor = [], None
    while True:
        q = s.run("history.affected", dict({"rev": r["rev"], "limit": 1000}, **({"cursor": cursor} if cursor else {})))
        if not q["ok"]:
            v.append(f"history.affected 실패: {q['error']}")
            break
        if _size(q) > limits.RESPONSE_CAP_BYTES:
            v.append("history.affected 응답이 상한을 넘는다")
        got += [(i["change"], i["id"], i["seq"]) for i in q["result"]["items"]]
        cursor = q["page"]["next_cursor"]
        if not cursor:
            break
    created = [(i, sq) for c, i, sq in got if c == "created"]
    if [i for i, _ in created] != ids:
        v.append(f"history.affected 로 이어받은 생성 id 가 명령 순서의 전체와 다르다({len(created)}건)")
    seq_of = dict(created)
    for x in res:
        if "axis" not in x and x.get("id") in seq_of and seq_of[x["id"]] != x["seq"]:   # axis 가 있는 것은 wall.move 의 결과
            v.append(f"results 의 seq 와 history.affected 의 seq 가 다르다: {x}")
            break
    # 실패한 배치는 results 가 없다(원자적): 오류 봉투
    bad = s.batch(_walls(5, F.LV1, 5000) + [{"command": "wall.move", "args": {"id": "9" * 22, "dx": 1}}])
    if bad["ok"]:
        v.append("실패해야 할 배치가 성공했다")
    s.close()
    return v


# ---------------------------------------------------------------- 6. undo/redo ops 요약
def check_undo_ops(work: Path) -> list[str]:
    v = []
    s = _sess(work, "uops")
    s.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    r = s.batch(_walls(3, F.LV1) + [{"command": "wall.move", "args": {"id": _mk(1), "dx": 5}}, {"command": "wall.move", "args": {"id": _mk(2), "dx": 5}}])
    assert r["ok"], r
    for label in ("undo", "redo"):
        u = s.run("history." + label)
        want = [{"op": "wall.create", "count": 3}, {"op": "wall.move", "count": 2}]
        if not u["ok"] or u["result"]["ops"] != want or u["result"]["ops_total"] != 5 or "truncated" in u["result"]:
            v.append(f"{label} 의 ops 가 커맨드별 건수 요약이 아니다: {u.get('error') or u['result']}")
    big = s.batch(_walls(600, F.LV1, 1000))
    for label in ("undo", "redo"):
        u = s.run("history." + label)
        if not u["ok"]:
            v.append(f"큰 배치 {label} 실패: {u['error']}")
            continue
        if u["result"]["ops"] != [{"op": "wall.create", "count": 600}] or u["result"]["ops_total"] != 600:
            v.append(f"큰 배치 {label} 의 ops 요약이 틀렸다: {u['result']['ops']}")
        if _size(u) > limits.RESPONSE_CAP_BYTES:
            v.append(f"큰 배치 {label} 응답이 상한을 넘는다: {_size(u)}")
        for w in u["warnings"]:
            if "results" in w:
                v.append(f"단건 응답의 경고에 results 건수 문구가 있다: {w[:80]}")
        if u["page"] is not None:
            v.append(f"단건 응답에 page 가 있다: {u['page']}")
    assert big["ok"]
    s.close()
    return v


# ---------------------------------------------------------------- 7. 비원자 script
def check_script_cap(work: Path) -> list[str]:
    """비원자 script 응답도 상한 안이고 잘림을 표시하며, 중간 실패 항목은 잘려 사라지지 않는다."""
    v = []
    s = _sess(work, "scr")
    s.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})

    def items(n, start, fail_at=None):
        out = [{"command": c["command"], "args": c["args"]} for c in _walls(n, F.LV1, start)]
        if fail_at is not None:
            out.insert(fail_at, {"command": "wall.move", "args": {"id": "9" * 22, "dx": 1}})
        return out
    r = dispatch.run_script(s.env, items(150, 2000), project=s.path.name)
    if not r["ok"] or _size(r) > limits.RESPONSE_CAP_BYTES:
        v.append(f"성공한 script 150건: ok={r['ok']} {_size(r)}바이트(상한 {limits.RESPONSE_CAP_BYTES})")
    pg = r.get("page")
    if not pg or pg["truncated"] is not True or pg["total"] != 150 or pg["shown"] >= 150:
        v.append(f"성공한 script 의 잘림 표시가 틀렸다: {pg}")
    if r["affected"]["counts"]["created"] != 150:
        v.append(f"script 의 합친 affected 건수가 틀렸다: {r['affected']['counts']}")
    # 중간 실패
    r = dispatch.run_script(s.env, items(150, 3000, fail_at=100), project=s.path.name)
    if r["ok"] or r["error"]["code"] != "NOT_FOUND":
        v.append(f"중간 실패가 오류 봉투가 아니다: ok={r['ok']} {r.get('error')}")
    res = r.get("result") or []
    fails = [x for x in res if not x["ok"]]
    if len(fails) != 1 or fails[0]["index"] != 100 or fails[0]["error"]["code"] != "NOT_FOUND":
        v.append(f"중간 실패 항목이 응답에서 사라졌다: {[x['index'] for x in fails]}")
    if _size(r) > limits.RESPONSE_CAP_BYTES:
        v.append(f"중간 실패 script 응답이 상한을 넘는다: {_size(r)}")
    if not r.get("page") or r["page"]["truncated"] is not True or r["page"]["total"] != 101:
        v.append(f"중간 실패 script 의 page 가 틀렸다: {r.get('page')}")
    if not any("생략한 index" in w and "history" in w for w in r["warnings"]):
        v.append("생략한 항목과 이어 읽는 방법을 알리는 경고가 없다")
    if r["affected"]["counts"]["created"] != 100:
        v.append(f"실패 전에 커밋된 100건이 affected 에 없다: {r['affected']['counts']}")
    # 첫 항목 실패, 객체 아닌 항목
    r = dispatch.run_script(s.env, items(3, 4000, fail_at=0), project=s.path.name)
    if r["ok"] or [x["index"] for x in r["result"]] != [0]:
        v.append(f"첫 항목 실패 처리가 틀렸다: {r.get('result')}")
    r = dispatch.run_script(s.env, [1, "x"], project=s.path.name)
    if r["ok"] or r["error"]["code"] != "SCHEMA_INVALID":
        v.append(f"객체가 아닌 script 항목: {r.get('error')}")
    s.close()
    return v


# ---------------------------------------------------------------- 8. 큰 단건
def check_big_single(work: Path) -> list[str]:
    """입력 문자열 길이 상한(SCHEMA_INVALID), 속성이 많은 element.get 의 쪽 나누기, 관계가 많은 element.get 의 상한,
    옛 로그의 긴 값(재오픈·재생·조회)이 깨지지 않고 잘림이 표시되는지."""
    v = []
    s = _sess(work, "big")
    for n, a in F.SCRIPT:
        s.run(n, a)
    big = "가" * (limits.STR_MAX + 1)
    for name, args in (("pset.set", {"id": F.W1, "pset": "Pset_Big", "prop": "Note", "value": big}),
                       ("commands.search", {"query": "x" * 300}), ("commands.describe", {"name": "w" * 70})):
        r = s.run(name, args)
        if r["ok"] or r["error"]["code"] != "SCHEMA_INVALID":
            v.append(f"{name} 입력 길이 상한이 SCHEMA_INVALID 가 아니다: {r.get('error', {}).get('code', 'ok')}")
    if not s.run("pset.set", {"id": F.W1, "pset": "Pset_Big", "prop": "Note", "value": "가" * limits.STR_MAX})["ok"]:
        v.append("상한 길이(255자) 값이 거부됐다")
    r = s.run("x" * 40000)
    if _size(r) > limits.RESPONSE_CAP_BYTES:
        v.append(f"긴 커맨드 이름의 오류 응답이 상한을 넘는다: {_size(r)}")
    # 속성이 아주 많은 요소: 쪽으로 나눠 읽고 truncated 가 정직하다
    wall = F.W2
    for i in range(80):
        assert s.run("pset.set", {"id": wall, "pset": f"Pset_{i:03d}", "prop": "P", "value": "\x01" * limits.STR_MAX})["ok"]
    seen, cursor, pages, flags = {}, None, 0, []
    while True:
        r = s.run("element.get", dict({"id": wall, "limit": 1000}, **({"cursor": cursor} if cursor else {})))
        if not r["ok"]:
            v.append(f"element.get 실패: {r['error']}")
            break
        pages += 1
        if _size(r) > limits.RESPONSE_CAP_BYTES:
            v.append(f"element.get {pages}쪽이 {_size(r)}바이트로 상한을 넘는다")
        for ps, props in r["result"]["psets"].items():
            for pr, val in props.items():
                seen[(ps, pr)] = val
        flags.append((r["page"]["truncated"], r["page"]["next_cursor"] is not None))
        cursor = r["page"]["next_cursor"]
        if not cursor or pages > 100:
            break
    if pages < 2:
        v.append(f"속성이 많은 element.get 이 쪽으로 나뉘지 않았다(쪽 {pages})")
    if any(t and c is False for t, c in flags[:-1]) or not all(t for t, _ in flags[:-1]):
        v.append(f"element.get 의 truncated/next_cursor 표시가 이상하다: {flags[:4]}")
    if len(seen) != 80 or any(len(x) != limits.STR_MAX for x in seen.values()):
        v.append(f"element.get 쪽 이어받기로 속성 80개를 다 얻지 못했다: {len(seen)}개")
    # 관계가 많은 레벨
    big_lv = _sess(work, "bigrel")
    big_lv.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    big_lv.batch(_walls(300, F.LV1))
    r = big_lv.run("element.get", {"id": F.LV1})
    res = r["result"]
    shown = sum(len(x) for x in res["relations"].values())
    if _size(r) > limits.RESPONSE_CAP_BYTES or shown != 20 or res["relations_total"] != 300 or r["page"]["truncated"] is not True:
        v.append(f"관계가 많은 element.get 이 잘리고도 정직하게 표시하지 않았다: 보임 {shown}, 총 {res['relations_total']}, 크기 {_size(r)}, page {r['page']}")
    if not any("relation.list" in w for w in r["warnings"]):
        v.append("잘린 관계를 이어 읽는 방법(relation.list)을 알리는 경고가 없다")
    big_lv.close()
    # 옛 로그의 긴 값: 상한이 생기기 전에 쓴 값이 로그에 있다(재생 경로로 만든다)
    from dobim_core.engine import Engine
    old = "옛" * 40000
    cmd = dispatch.get_command("pset.set")
    args = {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": old}   # 일람표에도 실리는 속성
    try:
        dispatch.parse_args(cmd, args)
        v.append("새 입력의 40,000자 값이 거부되지 않았다")
    except Exception as ex:   # noqa: BLE001
        if getattr(ex, "code", "") != "SCHEMA_INVALID":
            v.append(f"새 입력의 긴 값이 SCHEMA_INVALID 가 아니다: {type(ex).__name__}")
    s.eng.mutate([(cmd, dispatch.parse_args(cmd, args, replay=True))])
    for name, a2 in (("history.list", {"limit": 5}), ("element.get", {"id": F.D1}), ("schedule.rows", {"kind": "door", "limit": 5})):
        r = s.run(name, a2)
        if not r["ok"]:
            v.append(f"옛 긴 값이 있는 {name} 이 실패했다: {r['error']}")
            continue
        if _size(r) > limits.RESPONSE_CAP_BYTES:
            v.append(f"옛 긴 값이 있는 {name} 응답이 {_size(r)}바이트로 상한을 넘는다")
        if r["page"]["truncated"] is not True:
            v.append(f"옛 긴 값을 잘랐는데 {name} 의 page.truncated 가 {r['page']['truncated']} 다(거짓 보고)")
        if not any("옛 " in w for w in r["warnings"]):
            v.append(f"옛 긴 값을 잘랐는데 {name} 에 경고가 없다")
    d = kit.full_check(s, Path(tempfile.mkdtemp(dir=Path(work), prefix="oldrep-")), tag="-old")
    if not d["ok"]:
        v.append(f"옛 긴 값이 있는 로그의 재생·재오픈 검사 실패: {d['diffs'][:2]}")
    eng2 = Engine(s.path, "reopen")
    try:
        if eng2.ifc.get_psets(F.D1).get("Pset_DoorCommon", {}).get("FireRating") != old:
            v.append("재오픈한 엔진이 옛 긴 값을 그대로 갖고 있지 않다")
    finally:
        eng2.close()
    s.close()
    return v


# ---------------------------------------------------------------- 10. 파일 입출력 오류
def check_io_errors(work: Path) -> list[str]:
    """ifc.export 의 실패 처리: 라이브러리 예외에서도 스냅숏이 쌓이지 않고, OSError 는 원인별 코드로 나뉘며,
    다른 프로세스가 .tmp 를 잡아도 INTERNAL 이 아니다."""
    from unittest import mock
    from dobim_core.ifc_model import IfcModel
    v = []
    s = _sess(work, "io")
    for n, a in F.SCRIPT:
        s.run(n, a)
    ws = s.path.parent
    if not _confirmed(s, "ifc.export", {"path": "o.ifc"})["ok"]:
        return ["처음 내보내기가 실패했다"]

    def snaps():
        d = ws / ".dobim-snapshots"
        return sorted(x.name for x in d.iterdir()) if d.is_dir() else []

    def tmps():
        return sorted(x.name for x in ws.glob("*.tmp"))
    base = snaps()

    def failing(exc):
        def write(self, path):
            Path(path).write_bytes(b"half")
            raise exc
        return mock.patch.object(IfcModel, "write", write)
    cases = [("라이브러리 예외", RuntimeError("주입"), "INTERNAL"),
             ("디스크 가득(ENOSPC)", OSError(errno.ENOSPC, "No space left on device"), "DISK_FULL"),
             ("장치 오류(EIO)", OSError(errno.EIO, "I/O error"), "IO_ERROR"),
             ("경로 없음(ENOENT)", FileNotFoundError(errno.ENOENT, "no such file"), "NOT_FOUND")]
    for label, exc, want in cases:
        with failing(exc):
            r = _confirmed(s, "ifc.export", {"path": "o.ifc"})
        code = None if r["ok"] else r["error"]["code"]
        if code != want:
            v.append(f"{label} 가 {want} 가 아니라 {code} 로 보고됐다")
        if snaps() != base:
            v.append(f"{label} 뒤에 스냅숏이 쌓였다: {len(snaps())}개(기준 {len(base)})")
        if tmps():
            v.append(f"{label} 뒤에 .tmp 가 남았다: {tmps()}")
    if sys.platform == "win32":
        v += _lock_cases(s, ws, snaps, tmps, base)
    ok = _confirmed(s, "ifc.export", {"path": "o.ifc"})
    if not ok["ok"]:
        v.append(f"주입을 걷은 뒤 내보내기가 실패했다: {ok['error']}")
    s.close()
    return v


def _lock_cases(s, ws: Path, snaps, tmps, base) -> list[str]:
    """윈도 전용: 같은 프로세스 안에서 CreateFileW 로 파일을 공유 없이 잡아 다른 프로그램의 잠금을 흉내 낸다."""
    import ctypes
    from ctypes import wintypes
    v = []
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    invalid = wintypes.HANDLE(-1).value

    def hold(path: Path, share: int, create: bool):
        h = k.CreateFileW(str(path), 0xC0000000, share, None, 1 if create else 3, 0x80, None)
        if h in (invalid, None):
            raise OSError(f"잡지 못했다: {ctypes.get_last_error()}")
        return h
    # 다른 프로세스가 .tmp 를 잡고 있다(이전 실패의 잔해): INTERNAL 이 아니라 FILE_LOCKED
    tmp = ws / "o.ifc.tmp"
    h = hold(tmp, 0, True)
    try:
        r = _confirmed(s, "ifc.export", {"path": "o.ifc"})
    finally:
        k.CloseHandle(h)
    code = None if r["ok"] else r["error"]["code"]
    if code != "FILE_LOCKED":
        v.append(f".tmp 를 다른 프로세스가 잡았을 때 {code} 로 보고됐다(FILE_LOCKED 여야 한다)")
    if snaps() != base:
        v.append("잠긴 .tmp 에서 실패했는데 스냅숏이 쌓였다")
    tmp.unlink(missing_ok=True)
    # 대상 파일을 읽기·쓰기 공유로 잡았지만 삭제 공유는 없다: 이름 바꾸기가 접근 거부(WinError 5)로 실패해도 FILE_LOCKED
    h = hold(ws / "o.ifc", 3, False)
    try:
        r = _confirmed(s, "ifc.export", {"path": "o.ifc"})
    finally:
        k.CloseHandle(h)
    code = None if r["ok"] else r["error"]["code"]
    if code != "FILE_LOCKED":
        v.append(f"대상이 잠겼을 때 {code} 로 보고됐다(FILE_LOCKED 여야 한다)")
    if snaps() != base or tmps():
        v.append("잠긴 대상에서 실패했는데 스냅숏·.tmp 가 남았다")
    return v


ALL = [("공유 가변 객체", check_shared_state), ("경고 상한", check_warning_cap), ("배치 results 잘림", check_batch_results),
       ("undo/redo ops 요약", check_undo_ops), ("비원자 script", check_script_cap), ("큰 단건", check_big_single),
       ("파일 입출력 오류", check_io_errors)]


def run_all(work: Path, with_tokens: bool = True) -> list[str]:
    out = []
    for label, fn in ALL:
        out += [f"[{label}] {x}" for x in fn(work)]
    if with_tokens:
        out += [f"[토큰 캠페인] {x}" for x in check_token_classes(work)]
    return out


if __name__ == "__main__":
    w = Path(tempfile.mkdtemp())
    res = run_all(w)
    print("\n".join(res) or "프로토콜 검사 통과")
    sys.exit(1 if res else 0)
