# SPDX-License-Identifier: MPL-2.0
"""S1 시험 도구: 임의 커맨드 생성기(시드 고정), 차등 검사, 축소 재현(ddmin).

생성기는 살아 있는 엔진 상태를 보고 다음 커맨드를 고른다. 생성되는 모든 id 는 인자에 명시해
두므로(결정적), 일부 커맨드를 지운 축소열에서도 같은 의미로 재생된다.
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from dobim_core import dispatch, fixture as F  # noqa: E402
from dobim_core.engine import Engine, canon  # noqa: E402

PSETS = [("Pset_WallCommon", "FireRating", ["EI30", "EI60", "EI90", None]),
         ("Pset_WallCommon", "IsExternal", [True, False, None]),
         ("Pset_DoorCommon", "FireRating", ["EI30", "EI60", None]),
         ("Pset_DoorCommon", "SecurityRating", [1, 2, 3, None]),
         ("Pset_Custom", "Weight", [10.5, 20.25, None])]
MATERIALS = ["Concrete", "Steel", "Gypsum", "Timber", None]


def mkid(prefix: str, n: int) -> str:
    return prefix + f"{n:021d}"


class Session:
    """엔진 하나와 그 위의 dispatch 경로. 시험에서 CLI·MCP 와 같은 검증 경로를 탄다."""

    def __init__(self, path: Path, actor="t", *, create=True, guid=F.GUID, **kw):
        self.path = Path(path)
        self.eng = Engine.create(self.path, actor, guid=guid, **kw) if create else Engine(self.path, actor, **kw)
        self.env = dispatch.Env(self.path.parent, actor)
        self.env.adopt(self.path, self.eng)

    def run(self, name, args=None, **kw):
        return dispatch.run(self.env, name, args, project=self.path.name, **kw)

    def batch(self, commands, **kw):
        return dispatch.run_batch(self.env, commands, project=self.path.name, **kw)

    def item(self, it: dict):
        if it["op"] == "batch":
            return self.batch([{"command": n, "args": a} for n, a in it["commands"]], expected_rev=it.get("expected_rev"))
        return self.run(it["name"], it["args"])

    def close(self):
        self.eng.close()


# ================================================================ 생성기
class Gen:
    """살아 있는 상태에서 다음 시험 항목을 고른다. 같은 시드와 같은 결과 이력이면 같은 열이 나온다."""

    def __init__(self, seed: int, *, undo_weight=6, invalid_weight=3, batch_weight=4):
        self.rng = random.Random(seed)
        self.seed = seed
        self.prev_undo = False
        self.n = {"1": 0, "2": 0, "3": 0}
        self.w = [("level", 2), ("wall", 20), ("move", 15), ("wset", 8), ("join", 10), ("unjoin", 3),
                  ("door", 12), ("dset", 8), ("delete", 5), ("pset", 8), ("mat", 4),
                  ("undo", undo_weight), ("redo", undo_weight / 2), ("invalid", invalid_weight),
                  ("batch", batch_weight)]

    def _nid(self, prefix):
        self.n[prefix] += 1
        return mkid(prefix, self.n[prefix] + self.seed * 100000)

    def _pick(self, eng, kind):
        ids = sorted(eng.st.by_kind.get(kind, ()))
        return self.rng.choice(ids) if ids else None

    def _axis(self):
        r = self.rng
        x, y = r.randrange(0, 20) * 500, r.randrange(0, 12) * 500
        if r.random() < 0.5:
            return [x, y, x + r.randrange(2, 14) * 500, y]
        return [x, y, x, y + r.randrange(2, 14) * 500]

    def _one(self, eng, force=None):
        r = self.rng
        kinds, weights = zip(*self.w)
        k = force or r.choices(kinds, weights)[0]
        if not eng.st.by_kind.get("level") and k not in ("undo", "redo", "invalid"):
            k = "level"
        if k == "level":
            i = self._nid("3")
            return ("level.create", {"id": i, "name": f"L{i[-6:]}", "elevation": r.randrange(0, 6) * 3300})
        if k == "wall":
            return ("wall.create", {"id": self._nid("1"), "level": self._pick(eng, "level"), "axis": self._axis(),
                                    "thickness": r.choice([100, 150, 200, 250]), "height": r.choice([2700, 3000, 3300])})
        wall, door = self._pick(eng, "wall"), self._pick(eng, "door")
        if k == "move" and wall:
            return ("wall.move", {"id": wall, "dx": r.randrange(-6, 7) * 100, "dy": r.randrange(-6, 7) * 100})
        if k == "wset" and wall:
            f = r.choice(["thickness", "height", "name", "axis"])
            v = {"thickness": r.choice([100, 200, 300]), "height": r.choice([2800, 3200]), "name": f"N{r.randrange(99)}",
                 "axis": self._axis()}[f]
            return ("wall.set", {"id": wall, f: v})
        if k in ("join", "unjoin") and wall:
            lv = eng.st.out.get(("on_level", wall), [None])[0]
            same = [w for w in eng.st.by_kind["wall"] if w != wall and eng.st.out.get(("on_level", w), [None])[0] == lv]
            if same:
                return ("join.set" if k == "join" else "join.clear", {"a": wall, "b": r.choice(sorted(same))})
        if k == "door" and wall:
            ax = eng.st.el[wall]["params"]["axis"]
            length = max(abs(ax[2] - ax[0]), abs(ax[3] - ax[1]))  # 축 정렬 벽만 만든다
            w = r.choice([800, 900, 1000])
            lo, hi = w // 2 + 100, int(length - w // 2)
            if hi > lo:
                return ("door.place", {"id": self._nid("2"), "host": wall, "offset": r.randrange(lo, hi, 100), "width": w,
                                       "height": r.choice([2100, 2400]), "sill": r.choice([0, 0, 100])})
        if k == "dset" and door:
            f = r.choice(["width", "height", "sill", "offset", "name"])
            v = {"width": r.choice([800, 900, 1000]), "height": r.choice([2100, 2200]), "sill": r.choice([0, 50]),
                 "offset": r.randrange(5, 15) * 100, "name": f"DN{r.randrange(99)}"}[f]
            return ("door.set", {"id": door, f: v})
        if k == "delete":
            pool = [x for x in (wall, door) if x]
            if pool and r.random() < 0.9:
                return ("element.delete", {"id": r.choice(pool)})
            lv = self._pick(eng, "level")
            if lv:
                return ("element.delete", {"id": lv})
        if k == "pset":
            target = r.choice([x for x in (wall, door) if x] or [None])
            if target:
                pset, prop, vals = r.choice(PSETS)
                return ("pset.set", {"id": target, "pset": pset, "prop": prop, "value": r.choice(vals)})
        if k == "mat":
            target = r.choice([x for x in (wall, door) if x] or [None])
            if target:
                return ("material.set", {"id": target, "name": r.choice(MATERIALS)})
        if k == "undo":
            return ("history.undo", {})
        if k == "redo":
            return ("history.redo", {})
        # 실패해야 하는 입력: 없는 id, 범위 밖 문, 같은 벽 조인 등
        bad = r.choice(["ghost", "far_door", "self_join", "neg"])
        if bad == "ghost":
            return ("wall.move", {"id": mkid("9", r.randrange(99)), "dx": 1})
        if bad == "far_door" and wall:
            return ("door.place", {"host": wall, "offset": 9_000_000, "width": 900})
        if bad == "self_join" and wall:
            return ("join.set", {"a": wall, "b": wall})
        return ("wall.set", {"id": wall or mkid("9", 1), "thickness": -5})

    def next(self, eng):
        if self.prev_undo and self.rng.random() < 0.6:   # 취소 직후에는 다시 실행·연속 취소가 자주 나와야 한다
            name = self.rng.choice(["history.redo", "history.redo", "history.undo"])
            self.prev_undo = name == "history.undo" or self.prev_undo
            return {"op": "cmd", "name": name, "args": {}}
        self.prev_undo = False
        if self.rng.random() < self.w[-1][1] / 100 and eng.st.by_kind.get("wall"):
            n = self.rng.randrange(2, 4)
            cmds = [self._one(eng, force=self.rng.choice(["move", "wset", "door", "pset", "wall"])) for _ in range(n)]
            if self.rng.random() < 0.3:
                cmds.append(("wall.move", {"id": mkid("9", 7), "dx": 1}))  # 마지막이 실패하는 배치: 전체 롤백
            return {"op": "batch", "commands": [[a, b] for a, b in cmds if not a.startswith("history.")]}
        name, args = self._one(eng)
        self.prev_undo = name == "history.undo"
        return {"op": "cmd", "name": name, "args": args}


def run_sequence(sess: Session, items, on_step=None):
    """항목을 순서대로 실행한다. 반환: 항목별 성공 여부."""
    oks = []
    for i, it in enumerate(items):
        r = sess.item(it)
        oks.append(r["ok"])
        if on_step:
            on_step(i, it, r)
    return oks


def generate_and_run(sess: Session, seed: int, n: int, on_step=None, **gen_kw):
    """생성기와 실행을 한 몸으로 돌린다(생성이 현재 상태에 의존). 항목 목록과 성공 여부를 돌려준다."""
    g = Gen(seed, **gen_kw)
    items, oks = [], []
    for i in range(n):
        it = g.next(sess.eng)
        r = sess.item(it)
        items.append(it)
        oks.append(r["ok"])
        if on_step:
            on_step(i, it, r)
    return items, oks


# ================================================================ 차등 검사
def log_rows(eng: Engine):
    return eng.conn.execute("SELECT seq, txn_id, actor, op, args, inverse FROM op_log ORDER BY seq").fetchall()


def replay_log(rows, path, guid, *, eager_t0=False) -> Engine:
    """로그의 (op, args) 를 빈 프로젝트에 처음부터 다시 실행한다(전체 재생). 같은 txn 은 한 배치로 묶는다."""
    eng = Engine.create(path, "replay", guid=guid, eager_t0=eager_t0)
    i = 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1][1] == rows[i][1] and not rows[i][3].startswith("history."):
            j += 1
        group = []
        for seq, txn, actor, op, args, inv in rows[i:j + 1]:
            cmd = dispatch.get_command(op)
            group.append((cmd, dispatch.parse_args(cmd, json.loads(args))))
        eng.mutate(group)
        i = j + 1
    return eng


def compare_engines(live: Engine, other: Engine, label: str) -> list[str]:
    """두 엔진의 로그 항목(op·args·inverse)과 세 해시를 비교한다. 다른 항목 설명 목록을 돌려준다."""
    diffs = []
    a = [(r[3], r[4], r[5]) for r in log_rows(live)]
    b = [(r[3], r[4], r[5]) for r in log_rows(other)]
    if a != b:
        k = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        diffs.append(f"{label}: 로그가 다르다 (seq {k + 1}, {len(a)} vs {len(b)}항목)")
    for name, fa, fb in (("state", live.state_hash, other.state_hash), ("derived", live.derived_hash, other.derived_hash),
                         ("ifc", live.ifc_hash, other.ifc_hash)):
        if fa() != fb():
            diffs.append(f"{label}: {name} 해시가 다르다")
    return diffs


def full_check(sess: Session, workdir: Path, *, validate=False, tag="") -> dict:
    """증분 엔진을 세 가지 기준과 비교한다.
    1. 기준 그래프(캐시 없음) = 무효화 누락·조기 차단 오류를 잡는다.
    2. 로그 전체 재생 = 비결정·역연산·효과 오류를 잡는다.
    3. 파일을 새로 연 엔진 = 저장·IFC 재구성 오류를 잡는다.
    """
    eng = sess.eng
    t0 = time.perf_counter()
    diffs = []
    live = {"state": eng.state_hash(), "derived": eng.derived_hash(), "ifc": eng.ifc_hash()}
    if eng.derived_hash(reference=True) != live["derived"]:
        diffs.append("증분 그래프 != 기준 그래프(캐시 없음)")
    bad = eng.intent_consistency()
    if bad:
        diffs.append(f"DoBIM_Intent 불일치 {len(bad)}건")
    rep_path = Path(workdir) / f"replay{tag}.dobim"
    rep_path.unlink(missing_ok=True)
    rep = replay_log(log_rows(eng), rep_path, eng.project_guid_uuid)
    diffs += compare_engines(eng, rep, "전체 재생")
    rep.close()
    reopen = Engine(sess.path, "reopen")
    diffs += compare_engines(eng, reopen, "새로 열기")
    if validate:
        v = eng.ifc.validate(express=True)
        if v["errors"]:
            diffs.append(f"IFC 유효성 오류 {v['errors']}건: {v['samples'][:2]}")
    reopen.close()
    return {"ok": not diffs, "diffs": diffs, "hashes": live, "rev": eng.rev, "sec": round(time.perf_counter() - t0, 2)}


def property_run(seed: int, n: int, workdir: Path, *, checkpoint_every=0, validate=False, **gen_kw) -> dict:
    """한 시드에 대해 n 개 항목을 실행하고 차등 검사를 한다. 실패하면 items 도 돌려준다."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / f"fuzz-{seed}.dobim"
    path.unlink(missing_ok=True)
    sess = Session(path)
    cps = []
    try:
        def step(i, it, r):
            if checkpoint_every and (i + 1) % checkpoint_every == 0:
                sess.eng.sync()
                sess.eng.flush_ifc()   # 증분 투영을 중간중간 실행해 둔다(마지막에 한 번에 투영하면 증분 오류가 가려진다)
                cps.append((i + 1, sess.eng.derived_hash() == sess.eng.derived_hash(reference=True)))

        items, oks = generate_and_run(sess, seed, n, on_step=step, **gen_kw)
        res = full_check(sess, workdir, validate=validate, tag=f"-{seed}")
        bad_cp = [c for c, ok in cps if not ok]
        if bad_cp:
            res["ok"] = False
            res["diffs"].append(f"중간 점검에서 증분 != 기준: 항목 {bad_cp[:5]}")
        res.update(seed=seed, n=n, succeeded=sum(oks), failed=len(oks) - sum(oks), items=items)
        return res
    finally:
        sess.close()


# ================================================================ 축소 재현
def fails(items, workdir: Path, *, validate=False) -> bool:
    """items 를 빈 프로젝트에 실행해 차등 검사가 어긋나면 True. 예외도 실패로 센다."""
    path = Path(workdir) / "shrink.dobim"
    path.unlink(missing_ok=True)
    sess = Session(path)
    try:
        for it in items:
            sess.item(it)
        r = full_check(sess, workdir, validate=validate, tag="-shrink")
        return not r["ok"]
    except Exception:
        return True
    finally:
        sess.close()


def shrink(items, workdir: Path, *, max_evals=300, validate=False) -> tuple[list, int]:
    """ddmin: 실패를 유지하는 최소에 가까운 부분열을 찾는다. (축소열, 평가 횟수) 를 돌려준다."""
    evals = 0
    cur = list(items)
    chunk = max(1, len(cur) // 2)
    while chunk >= 1 and evals < max_evals:
        i, removed_any = 0, False
        while i < len(cur) and evals < max_evals:
            cand = cur[:i] + cur[i + chunk:]
            evals += 1
            if cand and fails(cand, workdir, validate=validate):
                cur, removed_any = cand, True
            else:
                i += chunk
        if not removed_any:
            chunk //= 2
    return cur, evals


def describe(items) -> str:
    out = []
    for it in items:
        if it["op"] == "batch":
            out.append("batch[" + "; ".join(f"{a} {json.dumps(b, ensure_ascii=False)}" for a, b in it["commands"]) + "]")
        else:
            out.append(f"{it['name']} {json.dumps(it['args'], ensure_ascii=False)}")
    return "\n".join(out)


# ================================================================ 개별 검사 (변이 시험이 재사용한다)
def triple(eng: Engine) -> tuple:
    return (eng.state_hash(), eng.derived_hash(), eng.ifc_hash())


def undo_property(seed: int, n: int, workdir: Path) -> dict:
    """합격 기준 2: 실행 취소·다시 실행 뒤 해시(상태·파생값·IFC)가 정확히 복원되는가. 위반 목록을 돌려준다.
    실패한 커맨드(원자적 롤백)도 해시를 바꾸지 않아야 한다."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / f"undo-{seed}.dobim"
    path.unlink(missing_ok=True)
    sess = Session(path)
    eng = sess.eng
    viol, steps = [], 0
    try:
        gen = Gen(seed, undo_weight=0)
        hist = [triple(eng)]                       # hist[k] = 변경 트랜잭션 k 개가 적용된 상태의 해시
        for i in range(n):
            it = gen.next(eng)
            before_rev = eng.rev
            r = sess.item(it)
            if not r["ok"]:
                steps += 1
                if triple(eng) != hist[-1]:
                    viol.append(f"항목 {i} 실패(원자적 롤백) 뒤 해시가 바뀜: {it}")
                continue
            if eng.rev > before_rev:
                hist.append(triple(eng))
        depth = len(eng.undo_stack)
        if depth != len(hist) - 1:
            viol.append(f"스택 깊이 {depth} != 기록 {len(hist) - 1}")
        rng = random.Random(seed ^ 0x5EED)
        # 1) 끝까지 되돌린다 2) 끝까지 다시 한다 3) 무작위로 오간다
        plan = ["undo"] * depth + ["redo"] * depth + [rng.choice(["undo", "redo"]) for _ in range(60)]
        for act in plan:
            r = sess.run("history." + act)
            if not r["ok"]:
                if r["error"]["code"] not in ("NOTHING_TO_UNDO", "NOTHING_TO_REDO"):
                    viol.append(f"{act} 실패: {r['error']}")
                continue
            steps += 1
            k = len(eng.undo_stack)
            now = triple(eng)
            if now != hist[k]:
                which = [nm for nm, x, y in zip(("상태", "파생값", "IFC"), now, hist[k]) if x != y]
                viol.append(f"{act} 뒤 해시({'·'.join(which)})가 깊이 {k} 의 기록과 다르다")
                break
        return {"seed": seed, "violations": viol, "verified_steps": steps, "txns": len(hist) - 1}
    finally:
        sess.close()


def concurrency_property(workdir: Path) -> list[str]:
    """한 파일을 연 엔진 두 개(프로세스 두 개의 대역)가 번갈아 쓸 때: 낡은 expected_rev 는 거부되고,
    따라잡기를 거친 쓰기는 로그와 해시가 하나로 수렴해야 한다."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / "conc.dobim"
    path.unlink(missing_ok=True)
    viol = []
    a = Session(path, actor="A")
    b = Session(path, actor="B", create=False)
    try:
        a.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
        a.run("wall.create", {"id": F.W1, "level": F.LV1, "axis": [0, 0, 5000, 0]})
        # B 는 아직 모른다. 낡은 expected_rev(0)로 쓰면 거부되어야 한다.
        r = b.run("wall.move", {"id": F.W1, "dx": 100}, expected_rev=0)
        if r["ok"] or r["error"]["code"] != "REV_CONFLICT":
            viol.append(f"낡은 expected_rev 가 거부되지 않음: {r.get('error')}")
        # expected_rev 없이 쓰면 따라잡은 뒤 적용되어야 한다 (A 의 벽을 B 가 움직인다)
        r = b.run("wall.move", {"id": F.W1, "dx": 100})
        if not r["ok"]:
            viol.append(f"B 가 따라잡지 못함: {r.get('error')}")
        r = a.run("wall.move", {"id": F.W1, "dy": 50}, expected_rev=b.eng.rev)
        if not r["ok"]:
            viol.append(f"A 의 최신 expected_rev 쓰기 실패: {r.get('error')}")
        r = b.run("history.undo")  # B 가 A 의 변경을 되돌림: 경고가 있어야 한다
        if not r["ok"] or not r["warnings"]:
            viol.append(f"교차 실행 취소 경고 없음: {r}")
        a.eng.sync()
        c = Engine(path, "C")
        t = [triple(x) for x in (a.eng, b.eng, c)]
        if not (t[0] == t[1] == t[2]):
            viol.append("엔진 A, B, 새로 연 C 의 해시가 다르다")
        c.close()
        return viol
    finally:
        a.close()
        b.close()
