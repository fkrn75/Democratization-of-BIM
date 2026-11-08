# SPDX-License-Identifier: MPL-2.0
"""6차 검수 후속 회귀: 공유 가변 객체, 경고 상한, 엄밀한 응답 상한(커서·제어문자), 문자 종류별 토큰, 잘린 배치 results,
undo/redo 요약, 비원자 script 상한, 큰 단건, effect SQL 쓰기 실패, 파일 입출력 오류 분류."""
import errno
import json
import os
import sqlite3
import subprocess
import sys

import pytest

import kit
import protocol_checks as P
import token_campaign as T
from conftest import ROOT
from dobim_core import dispatch, fixture as F, limits
from dobim_core.errors import EXIT_CODES, RETRYABLE

ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}


def test_1_응답_하나의_affected를_고쳐도_다음_응답이_오염되지_않는다(tmp_path):
    assert P.check_shared_state(tmp_path) == []


def test_1_기본_봉투에_공유_객체가_없다(base):
    a, b = base.run("project.info"), base.run("project.info")
    assert a["affected"] == b["affected"] == limits.empty_affected()
    assert a["affected"] is not b["affected"] and a["affected"]["counts"] is not b["affected"]["counts"]
    assert a["affected"]["created"] is not b["affected"]["created"] and a["warnings"] is not b["warnings"]


def test_2_변경_없는_배치_1000건도_경고까지_상한_안이고_같은_경고는_묶인다(tmp_path):
    assert P.check_warning_cap(tmp_path) == []


def test_2_group_warnings는_순서를_지키고_건수를_붙이고_줄_수와_길이를_제한한다():
    out = limits.group_warnings(["a: 변경 없음"] * 1000 + ["b"] + ["a: 변경 없음"] * 3)
    assert out == ["a: 변경 없음 (x1003건)", "b"]
    many = limits.group_warnings([f"w{i}" for i in range(50)])
    assert len(many) == limits.MAX_WARNING_LINES and "생략" in many[-1]
    assert all(len(w) <= limits.MAX_WARNING_CHARS for w in limits.group_warnings(["가" * 1000]))


@pytest.mark.parametrize("cls", ["이모지", "제어문자", "따옴표 역슬래시", "무작위 한글"])
def test_3_커서와_뼈대를_포함한_실제_직렬화_크기가_상한_이하다(tmp_path, cls):
    rows, viol = T.run(tmp_path, [cls], encs={})   # 바이트 상한만(토큰은 아래 시험)
    assert [x for x in viol if "바이트" in x] == []
    assert rows and max(r["bytes"] for r in rows) <= limits.RESPONSE_CAP_BYTES


def test_3_커서가_아주_길어도_다음_쪽_커서까지_상한_안이다(tmp_path):
    """레벨 이름과 벽 이름이 모두 이모지 64자면 일람표 커서가 약 700바이트다. 예비 몫에 기대지 않고 실제 길이로 잰다."""
    import random
    r = random.Random(1)
    gen = T.classes(r)["이모지"]
    s = kit.Session(tmp_path / "p.dobim")
    s.run("level.create", {"id": F.LV1, "name": gen(64), "elevation": 0})
    assert s.batch([{"command": "wall.create", "args": {"id": kit.mkid("1", i), "level": F.LV1, "axis": [0, i * 10, 6000, i * 10], "name": gen(64)}}
                    for i in range(1, 80)])["ok"]
    cursor, worst, longest = None, 0, 0
    while True:
        env = s.run("schedule.rows", dict({"kind": "wall", "limit": 1000}, **({"cursor": cursor} if cursor else {})))
        worst = max(worst, len(json.dumps(env, ensure_ascii=False).encode("utf-8")))
        cursor = env["page"]["next_cursor"]
        longest = max(longest, len(cursor or ""))
        if not cursor:
            break
    assert longest > 500 and worst <= limits.RESPONSE_CAP_BYTES, (longest, worst)
    s.close()


def test_4_문자_종류별_이름과_값에서_text_structured_합계가_cl100k_o200k_모두_8000_이내(tmp_path):
    pytest.importorskip("tiktoken")
    assert P.check_token_classes(tmp_path, P.REQUIRED_CLASSES + ["숫자-기호 교대", "문자-숫자 교대", "무작위 소수(좌표)"]) == []


def test_4_추정_토큰은_실측보다_작지_않다(tmp_path):
    """문자열 하나(JSON 으로 직렬화)의 추정 토큰이 모든 문자 종류와 길이에서 실측 이상이다. 응답 상한의 안전 근거다."""
    pytest.importorskip("tiktoken")
    import random
    encs = T.encoders()
    r = random.Random(7)
    for cname, gen in T.classes(r).items():
        for n in (8, 22, 64, 255):
            for _ in range(5):
                s = gen(n)
                est = limits.est_tokens(s)
                for name, e in encs.items():
                    assert len(e.encode(json.dumps(s, ensure_ascii=False))) <= est, (cname, n, name)


def test_4_토큰_추정은_런타임에_tiktoken을_쓰지_않는다():
    code = "import sys; import dobim_core.limits, dobim_core.commands, dobim_core.dispatch; assert 'tiktoken' not in sys.modules; print('ok')"
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, env=ENV, stdin=subprocess.DEVNULL, timeout=120)
    assert p.returncode == 0 and b"ok" in p.stdout, p.stderr.decode("utf-8", "replace")[-400:]


def test_5_잘린_배치_results는_명령_번호와_seq를_갖고_history_affected로_이어_읽는다(tmp_path):
    assert P.check_batch_results(tmp_path) == []


def test_5_배치_results_항목_모양(base):
    r = base.batch([{"command": "wall.move", "args": {"id": F.W1, "dx": 0}}, {"command": "wall.move", "args": {"id": F.W1, "dx": 5}}])
    assert [x["index"] for x in r["result"]] == [0, 1]
    assert r["result"][0]["seq"] is None and r["result"][1]["seq"] == r["rev"]      # 변경 없는 명령은 seq 가 없다
    assert r["page"] is None and any("변경 없음" in w for w in r["warnings"])


def test_5_history_affected_항목은_seq를_갖고_명령_순서로_나온다(base):
    ids = [kit.mkid("1", 900 + i) for i in range(5)]
    r = base.batch([{"command": "wall.create", "args": {"id": i, "level": F.LV1, "axis": [0, 100 * k, 3000, 100 * k]}} for k, i in enumerate(reversed(ids))])
    q = base.run("history.affected", {"rev": r["rev"]})
    created = [x for x in q["result"]["items"] if x["change"] == "created"]
    assert [x["id"] for x in created] == list(reversed(ids)) and [x["seq"] for x in created] == sorted(x["seq"] for x in created)


def test_6_undo_redo_ops는_커맨드별_건수_요약이다(tmp_path):
    assert P.check_undo_ops(tmp_path) == []


def test_7_비원자_script_응답도_상한_안이고_중간_실패는_잘리지_않는다(tmp_path):
    assert P.check_script_cap(tmp_path) == []


def test_7_CLI_script_150건은_상한_안이고_중간_실패는_종료코드와_함께_보인다(tmp_path):
    s = kit.Session(tmp_path / "p.dobim")
    s.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    s.close()
    lines = [json.dumps({"command": "wall.create", "args": {"level": F.LV1, "axis": [0, i * 10, 6000, i * 10]}}) for i in range(150)]
    (tmp_path / "ok.jsonl").write_text("\n".join(lines), encoding="utf-8")
    bad = lines[:100] + [json.dumps({"command": "wall.move", "args": {"id": "9" * 22, "dx": 1}})] + lines[100:]
    (tmp_path / "bad.jsonl").write_text("\n".join(bad), encoding="utf-8")

    def cli(name):
        p = subprocess.run([sys.executable, "-m", "dobim_core", "--workspace", str(tmp_path), "script", "-p", "p.dobim", str(tmp_path / name)],
                           capture_output=True, env=ENV, stdin=subprocess.DEVNULL, cwd=tmp_path, timeout=300)
        return p.returncode, p.stdout, json.loads(p.stdout)
    rc, raw, out = cli("ok.jsonl")
    assert rc == 0 and out["ok"] and len(raw) <= limits.RESPONSE_CAP_BYTES + 1 and out["page"]["truncated"] and out["page"]["total"] == 150
    assert out["affected"]["counts"]["created"] == 150
    rc, raw, out = cli("bad.jsonl")
    assert rc == EXIT_CODES["NOT_FOUND"] and not out["ok"] and out["error"]["code"] == "NOT_FOUND" and len(raw) <= limits.RESPONSE_CAP_BYTES + 1
    assert [x["index"] for x in out["result"] if not x["ok"]] == [100] and out["affected"]["counts"]["created"] == 100


def test_8_입력_길이_상한_element_get_쪽_나누기_옛_로그의_긴_값(tmp_path):
    assert P.check_big_single(tmp_path) == []


def test_8_속성_문자열_상한은_스키마에_드러난다(base):
    schema = base.run("commands.describe", {"name": "pset.set"})["result"]["input_schema"]
    assert "maxLength" in json.dumps(schema) and str(limits.STR_MAX) in json.dumps(schema)


def test_9_effect_SQL_쓰기가_중간에_실패해도_라이브와_디스크가_같다(tmp_path):
    pats = ["INSERT OR REPLACE INTO element", "INSERT OR IGNORE INTO relation", "DELETE FROM element", "DELETE FROM relation", "INSERT INTO op_log"]
    for k, pat in enumerate(pats):
        s = kit.Session(tmp_path / f"s{k}.dobim")
        for n, a in F.SCRIPT:
            s.run(n, a)
        s.eng.hashes()
        real = s.eng.conn

        class Flaky:
            armed = True

            def __getattr__(self, name):
                return getattr(real, name)

            def execute(self, sql, *a):
                if Flaky.armed and sql.startswith(pat):
                    Flaky.armed = False
                    raise sqlite3.OperationalError("disk I/O error")
                return real.execute(sql, *a)
        s.eng.conn = Flaky()
        if pat.startswith("DELETE FROM element") or pat.startswith("DELETE FROM relation"):
            r = s.run("element.delete", {"id": F.D2})
        elif "relation" in pat:
            r = s.run("wall.create", {"id": kit.mkid("1", 99), "level": F.LV1, "axis": [0, 5000, 100, 5000]})
        else:
            r = s.run("wall.move", {"id": F.W1, "dx": 7})
        assert not r["ok"], pat
        s.eng.conn = real
        live = s.eng.hashes()
        assert s.eng.tainted is None, pat
        d = kit.Session(s.path, create=False)
        assert {k_: v for k_, v in live.items()} == d.eng.hashes(), pat
        assert s.eng.head == d.eng.head and s.eng.undo_stack == d.eng.undo_stack, pat
        d.close()
        assert s.run("wall.move", {"id": F.W1, "dx": 1})["ok"]      # 복구된 엔진이 계속 쓰인다
        s.close()


def test_9_주입_격자의_SQL_쓰기_지점(tmp_path):
    import inject_grid as G
    n, bad = 0, []
    for scen in ("set_typechange", "wall_create", "elem_delete_wall", "batch_move_set_del_mat", "undo_elem_delete"):
        counts = G.count_calls(tmp_path, scen)
        for tgt in G.SQL_TARGETS:
            for mode in ("once", "persist"):
                if counts.get(tgt[1], 0) >= 1:
                    n += 1
                    bad += G.run_case(tmp_path, scen, tgt, mode, 1, n)["viol"]
    assert n >= 20 and bad == []


def test_10_파일_입출력_오류는_원인별_코드로_나뉘고_스냅숏이_쌓이지_않는다(tmp_path):
    assert P.check_io_errors(tmp_path) == []


def test_10_OSError_분류표(tmp_path):
    from dobim_core import commands
    p = tmp_path / "o.ifc"
    p.write_bytes(b"x")

    def code(ex):
        return commands._io_error(p, ex).code
    err = OSError
    win = lambda n: type("W", (OSError,), {})(errno.EACCES, "x", None, n)   # noqa: E731  winerror 를 받는 OSError
    if sys.platform == "win32":
        assert code(win(32)) == "FILE_LOCKED" and code(win(33)) == "FILE_LOCKED"
        assert code(win(112)) == "DISK_FULL" and code(win(39)) == "DISK_FULL"
    assert code(err(errno.ENOSPC, "x")) == "DISK_FULL"
    assert code(err(errno.EIO, "x")) == "IO_ERROR"
    assert code(FileNotFoundError(errno.ENOENT, "x")) == "NOT_FOUND"
    assert code(PermissionError(errno.EACCES, "x")) == "FILE_LOCKED"                 # 쓸 수 있는데 접근 거부 = 다른 프로그램이 열고 있다
    os.chmod(p, 0o444)
    try:
        assert code(PermissionError(errno.EACCES, "x")) == "PATH_DENIED"             # 읽기 전용 속성
    finally:
        os.chmod(p, 0o666)
    for c in ("DISK_FULL", "IO_ERROR"):
        assert EXIT_CODES[c] == 1 and c not in RETRYABLE
    assert "FILE_LOCKED" in RETRYABLE


def test_오류_메시지는_입력이_길어도_상한_안이다(base):
    r = base.run("x" * 100_000)
    assert not r["ok"] and len(json.dumps(r, ensure_ascii=False)) < 3000
