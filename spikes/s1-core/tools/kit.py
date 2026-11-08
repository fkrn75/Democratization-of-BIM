# SPDX-License-Identifier: MPL-2.0
"""S1 시험 도구: 임의 커맨드 생성기(시드 고정), 차등 검사, 축소 재현(ddmin).

생성기는 살아 있는 엔진 상태를 보고 다음 커맨드를 고른다. 생성되는 모든 id 는 인자에 명시해
두므로(결정적), 일부 커맨드를 지운 축소열에서도 같은 의미로 재생된다.
"""
from __future__ import annotations

import json
import math
import random
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from dobim_core import dispatch, fixture as F  # noqa: E402
from dobim_core.engine import Engine, canon  # noqa: E402

# 같은 속성에 서로 다른 타입의 값을 번갈아 쓴다(타입 고착 버그를 잡기 위해). None 은 속성 삭제(pset.delete 로 보낸다).
PSETS = [("Pset_WallCommon", "FireRating", ["EI30", "EI60", "EI90", None]),
         ("Pset_WallCommon", "IsExternal", [True, False, None, 1, "yes"]),
         ("Pset_DoorCommon", "FireRating", ["EI30", "EI60", None, 30, 0.5]),
         ("Pset_DoorCommon", "SecurityRating", [1, 2, 3, None, 2.5, True, "abc"]),
         ("Pset_Mix", "P", [1, 2.5, 0.5, True, False, "abc", 5, 1.0, 0, None, "5"]),
         ("Pset_Custom", "Weight", [10.5, 20.25, None, 10, "heavy"])]
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
                  ("door", 12), ("dset", 8), ("delete", 5), ("pset", 8), ("pdel", 4), ("mat", 4),
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
        if r.random() < 0.4:   # 비직교·소수 좌표(1/4 mm 단위라 이진 부동소수에서 정확)·아주 짧은 벽
            x, y = r.randrange(0, 80000) / 4, r.randrange(0, 48000) / 4
            dx, dy = r.randrange(-32000, 32000) / 4, r.randrange(-32000, 32000) / 4
            if r.random() < 0.1:
                dx, dy = r.choice([1.0, 1.25, -2.0]), r.choice([0.0, 0.5, -1.0])
            if dx * dx + dy * dy < 1.0:
                dx = 5.0
            return [x, y, x + dx, y + dy]
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
            if r.random() < 0.5:
                return ("wall.move", {"id": wall, "dx": r.randrange(-6, 7) * 100, "dy": r.randrange(-6, 7) * 100})
            return ("wall.move", {"id": wall, "dx": round(r.uniform(-777, 777), 3), "dy": round(r.uniform(-777, 777), 3)})
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
            length = math.sqrt((ax[2] - ax[0]) ** 2 + (ax[3] - ax[1]) ** 2)
            w = r.choice([800, 900, 1000])
            lo, hi = w // 2 + 100, int(length - w // 2)
            if hi > lo:
                off = r.randrange(lo, hi, 100) if r.random() < 0.5 else round(r.uniform(lo, hi), 3)
                return ("door.place", {"id": self._nid("2"), "host": wall, "offset": off, "width": w,
                                       "height": r.choice([2100, 2400]), "sill": r.choice([0, 0, 100])})
        if k == "dset" and door:
            f = r.choice(["width", "height", "sill", "offset", "name"])
            v = {"width": r.choice([800, 900, 1000]), "height": r.choice([2100, 2200]), "sill": r.choice([0, 50]),
                 "offset": r.choice([r.randrange(5, 15) * 100, round(r.uniform(500, 1500), 3)]), "name": f"DN{r.randrange(99)}"}[f]
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
                val = r.choice(vals)
                if val is None:   # 속성 삭제는 pset.set 의 null 이 아니라 별도 커맨드다(난수 소비는 이전과 같다)
                    return ("pset.delete", {"id": target, "pset": pset, "prop": prop})
                return ("pset.set", {"id": target, "pset": pset, "prop": prop, "value": val})
        if k == "pdel":   # 실제로 있는 속성을 지운다(pset 항목의 None 은 대부분 없는 속성이라 효과가 거의 없었다)
            target = r.choice([x for x in (wall, door) if x] or [None])
            if target:
                have = [(ps, pr) for ps, props in sorted(eng.ifc.get_psets(target).items()) for pr in sorted(props)]
                if have:
                    ps, pr = r.choice(have)
                    return ("pset.delete", {"id": target, "pset": ps, "prop": pr})
                ps, pr, _ = r.choice(PSETS)
                return ("pset.delete", {"id": target, "pset": ps, "prop": pr})
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

    def _cross(self, eng):
        """기존 벽을 가로지르는 새 벽을 만들고 조인한다(배치). 교점이 벽 중앙 근처라 조인 정리 경계(degenerate_join,
        JOIN_MAX_EXT, 문이 정리된 벽 밖으로 나감)가 실제로 실행되도록 교점 위치를 0.3mm 단위로 흔든다."""
        r = self.rng
        w = self._pick(eng, "wall")
        x1, y1, x2, y2 = eng.st.el[w]["params"]["axis"]
        L = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        c, s = (x2 - x1) / L, (y2 - y1) / L
        t = L / 2 + r.choice([-0.6, -0.3, 0.0, 0.3, 0.6, 1.0]) + r.choice([0, 0, -L / 4, L / 4 - 0.3])
        px, py = x1 + t * c, y1 + t * s
        h = r.choice([500.0, 1000.0, 3.0, 0.7])
        nid = self._nid("1")
        lv = eng.st.out.get(("on_level", w), [None])[0]
        a, b = sorted([w, nid])
        return {"op": "batch", "commands": [
            ["wall.create", {"id": nid, "level": lv, "axis": [px + h * s, py - h * c, px - h * s, py + h * c]}],
            ["join.set", {"a": a, "b": b}]]}

    def next(self, eng):
        if self.prev_undo and self.rng.random() < 0.6:   # 취소 직후에는 다시 실행·연속 취소가 자주 나와야 한다
            name = self.rng.choice(["history.redo", "history.redo", "history.undo"])
            self.prev_undo = name == "history.undo" or self.prev_undo
            return {"op": "cmd", "name": name, "args": {}}
        self.prev_undo = False
        if self.rng.random() < 0.06 and eng.st.by_kind.get("wall"):
            return self._cross(eng)
        if self.rng.random() < 0.05 and eng.st.by_kind.get("wall"):
            # 한 요소에 이름 순서와 반대로 속성셋을 여러 개 쓰고 곧바로 지운다(삭제 효과 순서의 결정성 시험)
            t = self._pick(eng, "wall")
            cmds = [["pset.set", {"id": t, "pset": n, "prop": "p", "value": self.rng.choice([1, "a", 2.5])}]
                    for n in ("Pset_Zeta", "Pset_Mid", "Pset_Alpha")]
            if self.rng.random() < 0.7:
                cmds.append(["element.delete", {"id": t}])
                if self.rng.random() < 0.6:   # 삭제 뒤 실패해 롤백되면 IFC 속성셋 삽입 순서가 재생과 달라진다
                    cmds.append(["wall.move", {"id": mkid("9", 9), "dx": 1}])
            return {"op": "batch", "commands": cmds}
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


def legacy_op(op: str, args: dict) -> tuple[str, dict]:
    """N7 이전 로그 항목을 지금 커맨드로 옮긴다: `pset.set` 의 value=null(속성 삭제) -> `pset.delete`.
    지금의 pset.set 은 null 을 받지 않으므로 옛 로그를 그대로 재생하려면 이 변환이 필요하다. 효과(역연산)는 같다."""
    if op == "pset.set" and "value" in args and args["value"] is None:
        return "pset.delete", {k: v for k, v in args.items() if k != "value"}
    # N10 이전 로그는 생략한 선택 필드를 null 로 적었다(wall.set 의 name: null 등). 지금은 명시적 null 을 거부하므로
    # 기본값이 None 인 선택 필드의 null 은 생략으로 되돌린다. 필수 필드의 null(material.set 의 name)은 뜻이 있어 둔다.
    from dobim_core import registry
    cmd = registry.load().get(op)
    if cmd is not None:
        fields = cmd.args_model.model_fields
        args = {k: v for k, v in args.items() if not (v is None and k in fields and not fields[k].is_required() and fields[k].default is None)}
    return op, args


def replay_log(rows, path, guid, *, eager_t0=False, legacy=False) -> Engine:
    """로그의 (op, args) 를 빈 프로젝트에 처음부터 다시 실행한다(전체 재생). 같은 txn 은 한 배치로 묶는다.
    legacy=True 이면 N7 이전 형식(pset.set value=null)의 항목을 legacy_op 로 옮겨 실행한다."""
    eng = Engine.create(path, "replay", guid=guid, eager_t0=eager_t0)
    i = 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1][1] == rows[i][1] and not rows[i][3].startswith("history."):
            j += 1
        group = []
        for seq, txn, actor, op, args, inv in rows[i:j + 1]:
            a = json.loads(args)
            if legacy:
                op, a = legacy_op(op, a)
            cmd = dispatch.get_command(op)
            group.append((cmd, dispatch.parse_args(cmd, a, replay=True)))   # 옛 로그의 긴 값도 재생된다(길이 상한은 새 입력에만)
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


def directed_delete_order(workdir: Path) -> list[str]:
    """삭제 효과의 결정성(10k 시드 424242 가 찾은 결함)을 우연에 맡기지 않고 직접 만든다: 속성셋을 이름 순서의 반대로 쓰고,
    삭제가 롤백되는 배치(IFC 속성셋 삽입 순서가 바뀐다)를 거친 뒤 실제로 지운다. 로그·해시가 전체 재생과 같아야 한다."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "dir-order.dobim").unlink(missing_ok=True)
    s = Session(workdir / "dir-order.dobim")
    try:
        for n, a in F.SCRIPT:
            s.run(n, a)
        for ps in ("Pset_Zeta", "Pset_Mid", "Pset_Alpha"):
            s.run("pset.set", {"id": F.W3, "pset": ps, "prop": "p", "value": 1})
        s.batch([{"command": "element.delete", "args": {"id": F.W3}}, {"command": "wall.move", "args": {"id": mkid("9", 9), "dx": 1}}])
        s.run("element.delete", {"id": F.W3})
        return full_check(s, workdir, tag="-dir")["diffs"]
    finally:
        s.close()


def coverage(eng: Engine) -> dict:
    """생성된 열이 수치 경계 경로를 실제로 밟았는지의 분포(최종 상태 + 로그 전체)."""
    from dobim_core import queries as Q
    from dobim_core.engine import invert
    st, g = eng.st, eng.g
    walls = sorted(st.by_kind.get("wall", ()))
    nonorth = trimmed = degenerate = parallel_joins = short = 0
    for w in walls:
        x1, y1, x2, y2 = st.el[w]["params"]["axis"]
        if x1 != x2 and y1 != y2:
            nonorth += 1
        L = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        short += L < 10
        a, b, status = g.q(Q.wall_trim, w)
        degenerate += status == "degenerate_join"
        trimmed += (a != 0.0 or b != L) and status == "ok"
    for (kind, src), dsts in st.out.items():
        if kind == "joins":
            parallel_joins += sum(g.q(Q.join_point, src, d) is None for d in dsts)
    doors = sorted(st.by_kind.get("door", ()))
    outside = sum(g.q(Q.door_status, d) != "ok" for d in doors)
    tchg, kinds, dels = 0, {}, 0
    for *_, inv in log_rows(eng):
        for e in invert(json.loads(inv)):
            if e[0] == "pset":
                if e[4] is not None and e[5] is not None and type(e[4]) is not type(e[5]):
                    tchg += 1
                if e[5] is not None:
                    kinds[type(e[5]).__name__] = kinds.get(type(e[5]).__name__, 0) + 1
                elif e[4] is not None:
                    dels += 1
    return {"walls": len(walls), "walls_non_orthogonal": nonorth, "walls_shorter_than_10mm": short,
            "walls_trimmed_by_join": trimmed, "walls_degenerate_join": degenerate, "joins_parallel_no_intersection": parallel_joins,
            "doors": len(doors), "doors_outside_trimmed_host": outside,
            "pset_writes_with_type_change": tchg, "pset_effective_deletes_in_log": dels, "pset_written_value_types": kinds}


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
        res.update(seed=seed, n=n, succeeded=sum(oks), failed=len(oks) - sum(oks), items=items,
                   coverage=coverage(sess.eng))
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
        # 4) 분기: undo 뒤 새 명령을 쓰면 redo 가지는 사라져야 한다. undo/redo/새 명령을 섞어 기대 해시 이력과 대조한다.
        branch_checks = 0
        gen2 = Gen(seed + 777, undo_weight=0)
        for _ in range(80):
            act = rng.choice(["undo", "undo", "redo", "redo", "new", "new"])
            d = len(eng.undo_stack)
            if act in ("undo", "redo"):
                r = sess.run("history." + act)
                can = d > 0 if act == "undo" else d < len(hist) - 1
                if can != r["ok"]:
                    viol.append(f"분기 단계 {act}: 가능 여부 {can} 인데 결과 {r.get('error', {}).get('code', 'ok')}")
                    break
            else:
                before = eng.rev
                r = sess.item(gen2.next(eng))
                if r["ok"] and eng.rev > before:
                    del hist[len(eng.undo_stack):]       # 새 변경은 그 뒤의 redo 가지를 버린다
                    hist.append(None)
                    hist[-1] = triple(eng)
                    del hist[len(eng.undo_stack) + 1:]
            now = triple(eng)
            k = len(eng.undo_stack)
            branch_checks += 1
            if k >= len(hist) or now != hist[k]:
                viol.append(f"분기 단계 {act} 뒤 해시가 기대 이력과 다르다 (깊이 {k})")
                break
        return {"seed": seed, "violations": viol, "verified_steps": steps, "branch_checks": branch_checks,
                "txns": len(hist) - 1}
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
