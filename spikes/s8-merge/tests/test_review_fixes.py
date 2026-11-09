# SPDX-License-Identifier: MPL-2.0
"""독립 검수에서 나온 결함의 회귀 시험: 의미 충돌 해결 반복 상한, 병합 뒤 자동 id 충돌, 배치 안 병합, 같은 상대 재병합,
상대 파일 내용 검증, 읽기 전용 열기의 사이드카, 겹친 벽 규모, 삭제 대 수정의 속성 삭제, take=both."""
import json
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

import kit
import s8lib
from dobim_core import commands, fixture as F, merge as M
from dobim_core.engine import Engine
from s8lib import I, hashes3


def make(tmp_path, sc, name="ours.dobim", other="other.dobim", a="a", b="b"):
    s8lib.build_branches(sc, tmp_path)
    shutil.copyfile(tmp_path / f"{a}.dobim", tmp_path / name)
    shutil.copyfile(tmp_path / f"{b}.dobim", tmp_path / other)
    return tmp_path


def sess(tmp_path, name="ours.dobim", create=False):
    return kit.Session(tmp_path / name, "merger", create=create)


def rows_of_file(path):
    c = sqlite3.connect(str(path))
    try:
        return c.execute(M.LOG_SQL).fetchall()
    finally:
        c.close()


def read_all(s, resolve=None):
    """merge.preview 를 쪽 나누기로 끝까지 읽는다. (첫 응답의 result, 모든 항목)"""
    items, cursor, first = [], None, None
    while True:
        a = {"other": "other.dobim", "limit": 200}
        if resolve:
            a["resolve"] = resolve
        if cursor:
            a["cursor"] = cursor
        r = s.run("merge.preview", a)
        assert r["ok"], r
        first = first or r["result"]
        items += r["result"]["items"]
        cursor = r["page"]["next_cursor"]
        if not cursor:
            return first, items


def violation_rules(eng_or_units):
    return [v["rule"] for v in M.violations(M.View(eng_or_units))]


# ---------------------------------------------------------------- 1) 의미 충돌 해결 반복 상한
@pytest.mark.parametrize("n", [3, 9, 12, 30])
def test_의미_충돌_해결이_여러_건이어도_마지막_재검사가_남는다(tmp_path, n):
    """벽 n 개를 A 가 줄이고 B 가 문을 끝으로 옮긴다: 의미 충돌 n 건. 해결 지시가 8건을 넘어도 preview 와 apply 가 같고, 위반이 남은 채 깨끗하다고 하지 않는다."""
    walls = [I("1", 300 + i) for i in range(n)]
    doors = [I("2", 300 + i) for i in range(n)]
    sc = {"base": [x for i, w in enumerate(walls) for x in (
              ("wall.create", {"id": w, "level": F.LV1, "axis": [0, 20000 + i * 1000, 6000, 20000 + i * 1000]}),
              ("door.place", {"id": doors[i], "host": w, "offset": 1500, "width": 900}))],
          "a": [("@batch", [("wall.set", {"id": w, "axis": [0, 20000 + i * 1000, 3000, 20000 + i * 1000]}) for i, w in enumerate(walls)])],
          "b": [("@batch", [("door.set", {"id": d, "offset": 2800}) for d in doors])]}
    make(tmp_path, sc)
    s = sess(tmp_path)
    try:
        whole, items = read_all(s)
        sem = [c for c in items if c["type"] == "DOOR_OUT_OF_HOST"]
        assert len(sem) == n and whole["conflict_count"] >= n
        # 일부만 해결하면 남은 충돌이 그대로 보고돼야 한다(마지막 재검사)
        part = [{"key": c["key"], "take": "ours"} for c in sem[: n - 1]]
        w1, its1 = read_all(s, part)
        assert w1["conflict_count"] >= 1 and any(c["type"] == "DOOR_OUT_OF_HOST" for c in its1)
        a1 = s.run("merge.apply", {"other": "other.dobim", "resolve": part})
        assert not a1["ok"] and a1["error"]["code"] == "MERGE_CONFLICT"
        # 전부 해결: preview 와 apply 가 일치
        rl = [{"key": c["key"], "take": "ours"} for c in sem]
        for _ in range(3):   # 해결하면 드러나는 연쇄 충돌이 있으면 이어서 해결
            p2, its2 = read_all(s, rl)
            if not its2:
                break
            rl += [{"key": c["key"], "take": "ours"} for c in its2]
        assert p2["conflict_count"] == 0
        ap = s.run("merge.apply", {"other": "other.dobim", "resolve": rl})
        assert ap["ok"], ap.get("error")
        assert ap["result"]["plan_digest"] == p2["plan_digest"]
        assert M.violations(M.View(M.fold(rows_of_file(tmp_path / "ours.dobim")))) == [] or \
            all(v["rule"] not in ("DOOR_OUT_OF_HOST", "DOOR_OUTSIDE_TRIMMED") for v in M.violations(M.View(M.fold(rows_of_file(tmp_path / "ours.dobim")))))
        assert all(s.eng.st.el[d]["params"]["offset"] == 1500.0 for d in doors)
    finally:
        s.close()


def test_반복_상한이_없어도_수렴한다(tmp_path):
    """모듈에 반복 상한 상수가 남아 있지 않다(상한 소진으로 재검사가 빠지는 길이 없다)."""
    assert not hasattr(M, "MAX_ROUNDS")


# ---------------------------------------------------------------- 2) 병합 뒤 자동 id
def test_병합_뒤_자동_id가_상대의_자동_id와_겹치지_않는다(tmp_path):
    sc = {"a": [("wall.set", {"id": F.W2, "name": "A-only"})],
          "b": [("wall.create", {"level": F.LV1, "axis": [0, 7000 + i * 500, 2000, 7000 + i * 500]}) for i in range(3)]}
    make(tmp_path, sc)
    s = sess(tmp_path)
    try:
        assert s.run("merge.apply", {"other": "other.dobim"})["ok"]
        ids = set()
        for i in range(4):
            r = s.run("wall.create", {"level": F.LV1, "axis": [0, 9000 + i * 500, 2000, 9000 + i * 500]})
            assert r["ok"], r.get("error")
            ids.add(r["result"]["id"])
        assert len(ids) == 4 and all(i in s.eng.st.el for i in ids)
        assert s.run("door.place", {"host": F.W1, "offset": 4500, "width": 700})["ok"]
        assert s.run("level.create", {"name": "9F", "elevation": 1})["ok"]
    finally:
        s.close()
    # 재오픈해도 같은 상태(로그만으로 상태를 만든다)
    e = Engine(tmp_path / "ours.dobim", "x")
    try:
        assert len(e.st.el) == 7 + 3 + 4 + 2   # 조상 7 + 상대 벽 3 + 새 벽 4 + 문 + 레벨
    finally:
        e.close()


def test_자동_id_탐색은_충돌이_없으면_기존_id를_그대로_쓴다(tmp_path):
    """골든 해시 보존: 충돌이 없는 평소의 id 는 uuid5(kind:seq:1) 첫 후보 그대로다."""
    import uuid
    import ifcopenshell.guid
    s = sess(tmp_path / "x", create=True) if (tmp_path / "x").mkdir() is None else None
    try:
        r = s.run("level.create", {"name": "1F", "elevation": 0})
        want = ifcopenshell.guid.compress(uuid.uuid5(uuid.UUID(s.eng.project_guid_uuid), f"level:{s.eng.rev}:1").hex)
        assert r["result"]["id"] == want
    finally:
        s.close()


def test_배치_안에서도_자동_id가_겹치지_않는다(tmp_path):
    make(tmp_path, {"a": [("wall.set", {"id": F.W2, "name": "A"})], "b": [("wall.set", {"id": F.W3, "name": "B"})]})
    s = sess(tmp_path)
    try:
        r = s.batch([{"command": "wall.create", "args": {"level": F.LV1, "axis": [0, 9000 + i * 300, 1000, 9000 + i * 300]}} for i in range(5)])
        assert r["ok"], r.get("error")
        ids = [x["id"] for x in r["result"]] if isinstance(r["result"], list) else None
        if ids is not None:
            assert len(set(ids)) == 5
    finally:
        s.close()


# ---------------------------------------------------------------- 3) 배치 안 병합
def test_배치에서_다른_변경_뒤나_두_번_병합은_USAGE로_막는다(tmp_path):
    make(tmp_path, {"a": [("wall.set", {"id": F.W2, "name": "A-only"})], "b": [("wall.set", {"id": F.W1, "thickness": 250})]})
    s = sess(tmp_path)
    try:
        h0, rev0 = s.eng.hashes(), s.eng.rev
        r = s.batch([{"command": "wall.set", "args": {"id": F.W3, "name": "pre"}}, {"command": "merge.apply", "args": {"other": "other.dobim"}}])
        assert not r["ok"] and r["error"]["code"] == "USAGE"
        r = s.batch([{"command": "merge.apply", "args": {"other": "other.dobim"}}, {"command": "merge.apply", "args": {"other": "other.dobim"}}])
        assert not r["ok"] and r["error"]["code"] == "USAGE"
        assert s.eng.hashes() == h0 and s.eng.rev == rev0
        # 병합이 첫 변경이면 뒤에 다른 커맨드가 와도 된다
        r = s.batch([{"command": "merge.apply", "args": {"other": "other.dobim"}}, {"command": "wall.set", "args": {"id": F.W3, "name": "post"}}])
        assert r["ok"], r.get("error")
    finally:
        s.close()


# ---------------------------------------------------------------- 4) 같은 상대 재병합
def test_같은_상대를_다시_병합해도_거짓_충돌이_없다(tmp_path):
    make(tmp_path, {"a": [("wall.set", {"id": F.W2, "name": "A-only"})], "b": [("wall.set", {"id": F.W1, "thickness": 250})]})
    s = sess(tmp_path)
    try:
        assert s.run("merge.apply", {"other": "other.dobim"})["ok"]
        # 상대가 같은 필드를 또 바꾼다(우리는 그 필드를 손대지 않았다)
        sb = kit.Session(tmp_path / "b.dobim", "bob", create=False)
        assert sb.run("wall.set", {"id": F.W1, "thickness": 300})["ok"]
        assert sb.run("wall.set", {"id": F.W3, "name": "B2"})["ok"]
        sb.close()
        shutil.copyfile(tmp_path / "b.dobim", tmp_path / "other.dobim")
        pv = s.run("merge.preview", {"other": "other.dobim"})["result"]
        assert pv["conflict_count"] == 0 and pv["base"]["kind"] == "prior_merge" and pv["relation"] == "ours_behind"
        assert s.run("merge.apply", {"other": "other.dobim"})["ok"]
        assert s.eng.st.el[F.W1]["params"]["thickness"] == 300.0 and s.eng.st.el[F.W3]["params"]["name"] == "B2"
        assert s.eng.st.el[F.W2]["params"]["name"] == "A-only"
        # 끝났으면 변경 없음
        assert s.run("merge.apply", {"other": "other.dobim"})["warnings"] == ["merge.apply: 변경 없음"]
        assert s.run("merge.preview", {"other": "other.dobim"})["result"]["relation"] == "same"
    finally:
        s.close()


def test_재병합에서도_양쪽이_다시_다르게_바꾸면_충돌이다(tmp_path):
    make(tmp_path, {"a": [("wall.set", {"id": F.W2, "name": "A-only"})], "b": [("wall.set", {"id": F.W1, "thickness": 250})]})
    s = sess(tmp_path)
    try:
        assert s.run("merge.apply", {"other": "other.dobim"})["ok"]
        assert s.run("wall.set", {"id": F.W1, "thickness": 270})["ok"]   # 우리도 같은 필드를 바꿈
        sb = kit.Session(tmp_path / "b.dobim", "bob", create=False)
        assert sb.run("wall.set", {"id": F.W1, "thickness": 300})["ok"]
        sb.close()
        shutil.copyfile(tmp_path / "b.dobim", tmp_path / "other.dobim")
        pv = s.run("merge.preview", {"other": "other.dobim"})["result"]
        assert pv["conflict_count"] == 1 and pv["items"][0]["type"] == "SAME_FIELD"
        assert pv["items"][0]["base"] == 250.0   # 기준 = 앞서 들여온 상대 값
    finally:
        s.close()


def test_취소한_병합은_다시_들여온다(tmp_path):
    make(tmp_path, {"a": [("wall.set", {"id": F.W2, "name": "A-only"})], "b": [("wall.set", {"id": F.W1, "thickness": 250})]})
    s = sess(tmp_path)
    try:
        assert s.run("merge.apply", {"other": "other.dobim"})["ok"]
        assert s.run("history.undo", {})["ok"]
        assert s.eng.st.el[F.W1]["params"]["thickness"] == 200.0
        pv = s.run("merge.preview", {"other": "other.dobim"})["result"]
        assert pv["base"]["kind"] == "fork" and pv["conflict_count"] == 0
        assert s.run("merge.apply", {"other": "other.dobim"})["ok"] and s.eng.st.el[F.W1]["params"]["thickness"] == 250.0
    finally:
        s.close()


def test_상대가_우리를_합친_뒤_다시_우리가_상대를_합친다(tmp_path):
    """양방향 왕복: B 가 A 를 합치고, B 가 더 바꾸고, A 가 B 를 합친다. 거짓 충돌이 없고 결과가 B 와 같다."""
    make(tmp_path, {"a": [("wall.set", {"id": F.W2, "name": "A-only"})], "b": [("wall.set", {"id": F.W1, "thickness": 250})]},
         name="a2.dobim", other="b2.dobim")
    sb = kit.Session(tmp_path / "b2.dobim", "bob", create=False)
    sa = kit.Session(tmp_path / "a2.dobim", "alice", create=False)
    try:
        shutil.copyfile(tmp_path / "a.dobim", tmp_path / "a_snap.dobim")
        assert sb.run("merge.apply", {"other": "a_snap.dobim"})["ok"]
        assert sb.run("wall.set", {"id": F.W1, "thickness": 300})["ok"]
        sb.close()
        sb = None
        shutil.copyfile(tmp_path / "b2.dobim", tmp_path / "b_snap.dobim")
        pv = sa.run("merge.preview", {"other": "b_snap.dobim"})["result"]
        assert pv["conflict_count"] == 0, pv["items"]
        assert sa.run("merge.apply", {"other": "b_snap.dobim"})["ok"]
        assert sa.eng.st.el[F.W1]["params"]["thickness"] == 300.0 and sa.eng.st.el[F.W2]["params"]["name"] == "A-only"
    finally:
        if sb:
            sb.close()
        sa.close()


# ---------------------------------------------------------------- 5) 상대 파일 내용 검증
def _craft(tmp_path, effects, op="wall.set", args="{}", seq_off=1):
    make(tmp_path, {"a": [("wall.set", {"id": F.W1, "thickness": 250})], "b": [("wall.set", {"id": F.W2, "name": "zz"})]})
    c = sqlite3.connect(str(tmp_path / "other.dobim"))
    seq = c.execute("select max(seq) from op_log").fetchone()[0]
    c.execute("insert into op_log(seq,txn_id,actor,base_rev,ts,op,args,inverse) values(?,?,?,?,?,?,?,?)",
              (seq + seq_off, f"t{seq + seq_off}", "evil", seq, "2026-01-01T00:00:00", op, args, effects if isinstance(effects, str) else json.dumps(effects)))
    c.commit()
    c.close()


ROW = lambda kind, params, cls="IfcWall": {"kind": kind, "ifc_class": cls, "origin": "x", "family_id": None, "params": params}  # noqa: E731
GOOD_WALL = {"name": "N", "axis": [0, 0, 1000, 0], "thickness": 200.0, "height": 3000.0}


@pytest.mark.parametrize("label,effects,expect", [
    ("bogus_kind", [["el", "Z" * 22, ROW("bogus", {}), None]], "IO_ERROR"),
    ("big_name", [["el", "Y" * 22, ROW("wall", dict(GOOD_WALL, name="N" * 2000000)), None]], "IO_ERROR"),
    ("reserved_pset", [["pset", F.W1, "DoBIM_Intent", "k", "evil", None]], "IO_ERROR"),
    ("reserved_pset_new", [["pset", F.W1, "DoBIM_Intent", "k", None, "evil"]], "IO_ERROR"),
    ("rel_unknown", [["rel", "weird", F.W1, F.W2, 1, 0]], "IO_ERROR"),
    ("nan_param", [["el", "X" * 22, ROW("wall", dict(GOOD_WALL, axis=[0, 0, float("nan"), 0])), None]], "IO_ERROR"),
    ("bad_id", [["el", "short", ROW("wall", GOOD_WALL), None]], "IO_ERROR"),
    ("unknown_effect", [["zz", 1]], "IO_ERROR"),
    ("el_new_int", [["el", "X" * 22, 5, None]], "IO_ERROR"),
    ("not_a_list", {"a": 1}, "IO_ERROR"),
    ("effect_not_list", ["x"], "IO_ERROR"),
    ("bad_json", "not json", "IO_ERROR"),
    ("no_level_wall", [["el", "Q" * 22, ROW("wall", GOOD_WALL), None]], "IO_ERROR"),   # 레벨 관계가 없는 벽: 구조 위반
    ("bad_prop_name", [["pset", F.W1, "Pset_X", "id", None, 1]], "IO_ERROR"),
])
def test_조작된_상대_로그는_정의된_오류로_거절하고_아무것도_바꾸지_않는다(tmp_path, label, effects, expect):
    _craft(tmp_path, effects)
    s = sess(tmp_path)
    try:
        h0, rev0 = s.eng.hashes(), s.eng.rev
        for cmd in ("merge.preview", "merge.apply"):
            r = s.run(cmd, {"other": "other.dobim"})
            assert not r["ok"] and r["error"]["code"] == expect, (label, cmd, r.get("error"))
        assert s.eng.hashes() == h0 and s.eng.rev == rev0
        assert s.run("wall.set", {"id": F.W3, "name": "ok-after"})["ok"]   # 엔진은 멀쩡하다
    finally:
        s.close()


def test_로그_번호가_건너뛰면_거절한다(tmp_path):
    _craft(tmp_path, [], seq_off=5)
    s = sess(tmp_path)
    try:
        r = s.run("merge.preview", {"other": "other.dobim"})
        assert not r["ok"] and r["error"]["code"] == "IO_ERROR"
    finally:
        s.close()


def test_병합_결과도_예약_속성셋을_막는다():
    """검증이 상대 로그 읽기에만 있지 않다: 합친 결과의 단위 사전도 같은 불변식으로 검사한다."""
    base = M.fold([])
    bad = {("el", F.LV1, "@"): M.canon({"kind": "level", "ifc_class": "IfcBuildingStorey", "origin": "authored", "family_id": "level.basic"}),
           ("el", F.LV1, "p", "name"): M.canon("L"), ("el", F.LV1, "p", "elevation"): M.canon(0.0),
           ("pset", F.LV1, "DoBIM_Intent", "k"): M.canon("evil")}
    assert base == {} and "DoBIM_Intent" in M.units_error(bad)
    bad2 = dict(bad)
    del bad2[("pset", F.LV1, "DoBIM_Intent", "k")]
    assert M.units_error(bad2) is None
    del bad2[("el", F.LV1, "p", "name")]
    assert M.units_error(bad2) is not None   # 파라미터가 빠진 요소


def test_불변식_상수가_커맨드의_규칙과_같다():
    assert M.RESERVED_PSET == commands.RESERVED_PSET
    assert M.NAME_MAX == 64
    from dobim_core.ifc_model import IFC_CLASS
    assert M.KIND_CLASS == IFC_CLASS
    for ex in ("Pset_DoorCommon", "FireRating", "A" * 64):
        assert M.prop_error(ex, ex, 1) is None
    assert M.prop_error("A" * 65, "x") is not None


# ---------------------------------------------------------------- 6) 상대 폴더에 사이드카를 남기지 않는다
def test_상대를_읽어도_상대_폴더에_사이드카가_생기지_않는다(tmp_path):
    make(tmp_path, {"a": [("wall.set", {"id": F.W1, "thickness": 250})], "b": [("wall.set", {"id": F.W2, "name": "zz"})]})
    other_dir = tmp_path / "theirs"
    other_dir.mkdir()
    shutil.copyfile(tmp_path / "b.dobim", other_dir / "o.dobim")
    # 모든 열린 연결이 닫혔으니 WAL 파일이 없는 상태여야 한다(복사본)
    for suffix in ("-wal", "-shm"):
        assert not (other_dir / f"o.dobim{suffix}").exists()
    before = sorted(p.name for p in other_dir.iterdir())
    st = (other_dir / "o.dobim").stat()
    s = sess(tmp_path)
    try:
        for _ in range(2):
            assert s.run("merge.preview", {"other": "theirs/o.dobim"})["ok"]
            assert s.run("merge.apply", {"other": "theirs/o.dobim"}, dry_run=True)["ok"]
        assert s.run("merge.apply", {"other": "theirs/o.dobim"})["ok"]
    finally:
        s.close()
    assert sorted(p.name for p in other_dir.iterdir()) == before
    st2 = (other_dir / "o.dobim").stat()
    assert (st.st_size, st.st_mtime_ns) == (st2.st_size, st2.st_mtime_ns)


def test_상대가_열려_있으면_WAL을_읽고_우리가_만든_사이드카만_지운다(tmp_path):
    make(tmp_path, {"a": [("wall.set", {"id": F.W1, "thickness": 250})], "b": [("wall.set", {"id": F.W2, "name": "zz"})]})
    live = kit.Session(tmp_path / "b.dobim", "bob", create=False)
    s = sess(tmp_path)
    try:
        assert live.run("wall.set", {"id": F.W3, "name": "라이브"})["ok"]
        names = sorted(p.name for p in tmp_path.iterdir())
        assert s.run("merge.apply", {"other": "b.dobim"})["ok"]
        assert s.eng.st.el[F.W3]["params"]["name"] == "라이브"
        assert set(names) <= {p.name for p in tmp_path.iterdir()}   # 상대(열려 있음)의 사이드카는 그대로
        assert live.run("wall.set", {"id": F.W3, "name": "라이브2"})["ok"]   # 상대는 계속 쓸 수 있다
    finally:
        s.close()
        live.close()


def test_비정상_종료로_WAL만_남은_상대도_읽고_새_사이드카는_지운다(tmp_path):
    make(tmp_path, {"a": [("wall.set", {"id": F.W1, "thickness": 250})], "b": [("wall.set", {"id": F.W2, "name": "zz"})]})
    # b.dobim 에 -wal 만 있고 -shm 이 없는 상태를 만든다(체크포인트 안 된 변경이 WAL 에 있다)
    src = sqlite3.connect(str(tmp_path / "b.dobim"))
    src.execute("PRAGMA wal_autocheckpoint=0")
    src.execute("update element set params=json_set(params,'$.name','wal-only') where id=?", (F.W3,))
    src.commit()
    assert (tmp_path / "b.dobim-wal").exists()
    keep_wal = (tmp_path / "b.dobim-wal").read_bytes()
    src.close()
    shutil.copyfile(tmp_path / "b.dobim", tmp_path / "c.dobim")
    (tmp_path / "c.dobim-wal").write_bytes(keep_wal)
    for suf in ("-shm",):
        assert not (tmp_path / f"c.dobim{suf}").exists()
    rows = M.load_other(tmp_path / "c.dobim")[1]
    assert rows
    assert not (tmp_path / "c.dobim-shm").exists()   # 우리가 만든 -shm 은 지웠다


# ---------------------------------------------------------------- 7) 규모·삭제 대 수정
def test_겹친_벽이_많아도_위반_수가_선형이다():
    n = 1200
    u = {("el", F.LV1, "@"): M.canon({"kind": "level", "ifc_class": "IfcBuildingStorey", "origin": "authored", "family_id": "level.basic"}),
         ("el", F.LV1, "p", "name"): M.canon("L"), ("el", F.LV1, "p", "elevation"): M.canon(0.0)}
    for i in range(n):
        e = "1%021d" % i
        u[("el", e, "@")] = M.canon({"kind": "wall", "ifc_class": "IfcWall", "origin": "authored", "family_id": "wall.basic"})
        for k, v in (("name", "w"), ("axis", [0.0, 0.0, 6000.0, 0.0]), ("thickness", 200.0), ("height", 3000.0)):
            u[("el", e, "p", k)] = M.canon(v)
        u[("rel", "on_level", e, F.LV1)] = "1"
    t = time.time()
    v = M.violations(M.View(u))
    assert time.time() - t < 20
    assert len([x for x in v if x["rule"] == "DUPLICATE_WALL"]) == n   # 쌍(72만)이 아니라 벽마다 하나
    assert M.canon(M.violations(M.View(u))) == M.canon(v)


def test_작은_겹침_묶음은_쌍으로_보고한다():
    u = {("el", F.LV1, "@"): M.canon({"kind": "level", "ifc_class": "IfcBuildingStorey", "origin": "authored", "family_id": "level.basic"}),
         ("el", F.LV1, "p", "name"): M.canon("L"), ("el", F.LV1, "p", "elevation"): M.canon(0.0)}
    for i in range(4):
        e = "1%021d" % i
        u[("el", e, "@")] = M.canon({"kind": "wall", "ifc_class": "IfcWall", "origin": "authored", "family_id": "wall.basic"})
        for k, v in (("name", "w"), ("axis", [0.0, 0.0, 6000.0, 0.0]), ("thickness", 200.0), ("height", 3000.0)):
            u[("el", e, "p", k)] = M.canon(v)
        u[("rel", "on_level", e, F.LV1)] = "1"
    assert len([x for x in M.violations(M.View(u)) if x["rule"] == "DUPLICATE_WALL"]) == 6


def test_상대가_속성을_지운_것은_삭제_대_수정이_아니다(tmp_path):
    """A 가 문 D1 을 지우고 B 가 D1 의 FireRating 속성을 지웠다: 둘 다 없애는 방향이라 충돌이 아니다. B 가 값을 바꾸면 충돌이다."""
    make(tmp_path, {"a": [("element.delete", {"id": F.D1})], "b": [("pset.delete", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating"})]})
    s = sess(tmp_path)
    try:
        pv = s.run("merge.preview", {"other": "other.dobim"})["result"]
        assert pv["conflict_count"] == 0, pv["items"]
        assert s.run("merge.apply", {"other": "other.dobim"})["ok"] and F.D1 not in s.eng.st.el
    finally:
        s.close()
    d2 = tmp_path / "v2"
    make(d2 if d2.mkdir() is None else d2, {"a": [("element.delete", {"id": F.D1})],
                                            "b": [("pset.set", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI90"})]})
    s = sess(d2)
    try:
        pv = s.run("merge.preview", {"other": "other.dobim"})["result"]
        assert [c["type"] for c in pv["items"]] == ["DELETE_MODIFY"]
    finally:
        s.close()


# ---------------------------------------------------------------- take=both (ID_COLLISION)
def test_자동_id_충돌은_take_both로_둘_다_살린다(tmp_path):
    sc = {"a": [("wall.create", {"level": F.LV1, "axis": [0, 7000, 2000, 7000]})],
          "b": [("wall.create", {"level": F.LV1, "axis": [0, 7500, 3000, 7500]})]}
    make(tmp_path, sc)
    s = sess(tmp_path)
    try:
        pv = s.run("merge.preview", {"other": "other.dobim"})["result"]
        assert [c["type"] for c in pv["items"]] == ["ID_COLLISION"] and pv["items"][0]["take"] == ["ours", "theirs", "both"]
        key = pv["items"][0]["key"]
        rl = [{"key": key, "take": "both"}]
        pv2 = s.run("merge.preview", {"other": "other.dobim", "resolve": rl})["result"]
        assert pv2["conflict_count"] == 0 and pv2["resolved"] == 1
        new_before = [w for w in s.eng.st.by_kind["wall"] if w not in (F.W1, F.W2, F.W3)]
        ap = s.run("merge.apply", {"other": "other.dobim", "resolve": rl})
        assert ap["ok"], ap.get("error")
        new = [w for w in s.eng.st.by_kind["wall"] if w not in (F.W1, F.W2, F.W3)]
        assert len(new_before) == 1 and len(new) == 2
        axes = sorted(s.eng.st.el[w]["params"]["axis"] for w in new)
        assert axes == [[0.0, 7000.0, 2000.0, 7000.0], [0.0, 7500.0, 3000.0, 7500.0]]
        assert new_before[0] in new and all(s.eng.st.out.get(("on_level", w)) == [F.LV1] for w in new)
        # 새 id 는 결정적이고 다시 병합해도 같다(멱등)
        rev = s.eng.rev
        again = s.run("merge.apply", {"other": "other.dobim", "resolve": rl})
        assert again["ok"] and s.eng.rev == rev
        # 되돌리면 병합 전
        assert s.run("history.undo", {})["ok"]
        assert [w for w in s.eng.st.by_kind["wall"] if w not in (F.W1, F.W2, F.W3)] == new_before
        assert s.eng.intent_consistency() == [] if s.run("history.redo", {})["ok"] else False
    finally:
        s.close()
    # 같은 입력이면 같은 id(별도 실행)
    d2 = tmp_path / "again"
    d2.mkdir()
    make(d2, sc)
    s2 = sess(d2)
    try:
        s2.run("merge.apply", {"other": "other.dobim", "resolve": rl})
        assert sorted(s2.eng.st.by_kind["wall"]) == sorted(
            Engine(tmp_path / "ours.dobim", "x").st.by_kind["wall"])
    finally:
        s2.close()


def test_take_both는_ID_충돌이_아닌_충돌에는_쓸_수_없다(tmp_path):
    make(tmp_path, {"a": [("wall.set", {"id": F.W1, "thickness": 250})], "b": [("wall.set", {"id": F.W1, "thickness": 300})]})
    s = sess(tmp_path)
    try:
        key = s.run("merge.preview", {"other": "other.dobim"})["result"]["items"][0]["key"]
        r = s.run("merge.apply", {"other": "other.dobim", "resolve": [{"key": key, "take": "both"}]})
        assert not r["ok"] and r["error"]["code"] == "USAGE"
    finally:
        s.close()


def test_renamed_id는_IFC_GlobalId_모양이고_결정적이다():
    a = M.renamed_id("5d0b1a50-0000-4a00-8000-0d0b1a5000ff", F.W1, 11)
    assert M.ID_RE.match(a) and a[0] in "0123" and a == M.renamed_id("5d0b1a50-0000-4a00-8000-0d0b1a5000ff", F.W1, 11)
    assert a != M.renamed_id("5d0b1a50-0000-4a00-8000-0d0b1a5000ff", F.W1, 12)
    import ifcopenshell.guid
    ifcopenshell.guid.expand(a)   # IFC 가 받는 모양


def test_독립_점검이_모두_참이다(tmp_path):
    """시나리오 틀 밖의 계약(재병합·배치·조작된 로그·사이드카·삭제 대 수정 속성·겹친 벽 규모)을 한 번에 확인한다."""
    r = s8lib.extra_checks(tmp_path)
    assert r and all(v is True for v in r.values()), r
