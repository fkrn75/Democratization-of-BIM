# SPDX-License-Identifier: MPL-2.0
"""S3 공용 도구: 코어 위의 RA-01 모델, 뷰 묶음, 선분 일치율, 변이(음성 대조).

RA-01 에서 코어가 표현하는 것만 옮겼다: 레벨 2, 벽 14(외벽 8 + 내벽 6), 문 5, 조인.
코어에 없어서 뺀 것: 창 8, 슬래브 3, 기둥 12, 보 8, 계단 1, 공간 6, 벽 재료 층, 문 종류(타입).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "s1-core"
for p in (CORE / "src", CORE / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import kit  # noqa: E402
from dobim_core import drawing as D  # noqa: E402
from dobim_core.engine import _Inputs  # noqa: E402
from dobim_core.graph import RefGraph  # noqa: E402

LX, LY, STOREY = 12000.0, 8000.0, 3300.0
EXT_T, INT_T = 320.0, 100.0
HI = EXT_T / 2
GUID = "5d0b1a50-0000-4a00-8000-0d0b1a5000b3"


def _i(prefix: str, n: int) -> str:
    return prefix + f"{n:021d}"


def ra01_commands() -> tuple[list[tuple[str, dict]], dict]:
    """(커맨드 목록, 이름 -> id). 모든 id 를 명시해 어느 PC 에서든 같은 모델이 나온다."""
    cmds, ids = [], {}
    for k, (name, elev) in enumerate((("1F", 0.0), ("2F", STOREY)), 1):
        ids[name] = _i("3", k)
        cmds.append(("level.create", {"id": ids[name], "name": name, "elevation": elev}))
    n = 0
    ext = (((0, 0), (LX, 0)), ((LX, 0), (LX, LY)), ((LX, LY), (0, LY)), ((0, LY), (0, 0)))
    inter = (((4000, HI), (4000, LY - HI)), ((4000 + INT_T / 2, 4000), (LX - HI, 4000)), ((8000, HI), (8000, 2000)))
    for lv in ("1F", "2F"):
        base = [None] * 4
        for i, (a, b) in enumerate(ext):
            n += 1
            wid = _i("1", n)
            ids[f"W-E-{n:02d}"] = wid
            base[i] = wid
            cmds.append(("wall.create", {"id": wid, "level": ids[lv], "axis": [*a, *b], "thickness": EXT_T, "height": STOREY,
                                         "name": f"W-E-{n:02d}"}))
        inn = []
        for j, (a, b) in enumerate(inter, 1):
            n += 1
            wid = _i("1", n)
            ids[f"W-I-{lv}-{j}"] = wid
            inn.append(wid)
            cmds.append(("wall.create", {"id": wid, "level": ids[lv], "axis": [*a, *b], "thickness": INT_T, "height": STOREY,
                                         "name": f"W-I-{lv}-{j}"}))
        for i in range(4):
            cmds.append(("join.set", {"a": base[i], "b": base[(i + 1) % 4]}))
        for a, b in ((inn[0], base[0]), (inn[0], base[2]), (inn[1], inn[0]), (inn[1], base[1]), (inn[2], base[0])):
            cmds.append(("join.set", {"a": a, "b": b}))
        ids[f"ext-{lv}"], ids[f"int-{lv}"] = base, inn
    d = 0
    for host, off, wd in ((ids["ext-1F"][0], 8000, 900), (ids["int-1F"][0], 1840, 800), (ids["int-1F"][1], 2950, 800),
                          (ids["int-2F"][0], 1840, 800), (ids["ext-2F"][0], 8000, 900)):
        d += 1
        did = _i("2", d)
        ids[f"D-{d:02d}"] = did
        cmds.append(("door.place", {"id": did, "host": host, "offset": float(off), "width": float(wd), "height": 2100.0,
                                    "name": f"D-{d:02d}"}))
    return cmds, ids


def view_commands(ids: dict) -> tuple[list[tuple[str, dict]], dict]:
    vs, cmds = {}, []

    def add(key, **kw):
        vid = _i("4", len(vs) + 1)
        vs[key] = vid
        cmds.append(("view.create", {"id": vid, **kw}))

    add("plan-1F", name="1F 평면", type="plan", level=ids["1F"], cut_height=1200.0)
    add("plan-2F", name="2F 평면", type="plan", level=ids["2F"], cut_height=1200.0)
    add("section-BB", name="B-B 단면", type="section", p1=[-1000.0, 1900.0], p2=[13000.0, 1900.0])
    for d in ("N", "E", "S", "W"):
        add(f"elev-{d}", name=f"{ {'N': '북', 'E': '동', 'S': '남', 'W': '서'}[d] }측 입면", type="elevation", dir=d)
    return cmds, vs


def build(path: Path, with_views: bool = True):
    """RA-01(코어 범위) 프로젝트를 만들어 (세션, ids, views) 를 돌려준다."""
    sess = kit.Session(Path(path), guid=GUID)
    cmds, ids = ra01_commands()
    r = sess.batch([{"command": n, "args": a} for n, a in cmds])
    assert r["ok"], r
    views = {}
    if with_views:
        vc, views = view_commands(ids)
        r = sess.batch([{"command": n, "args": a} for n, a in vc])
        assert r["ok"], r
    return sess, ids, views


# ---------------------------------------------------------------- 선분 비교
TOL = 0.01   # mm. 끝점을 이 격자로 반올림해 같은 선분인지 본다 (10 마이크로미터)


def norm_segs(lines, tol: float = TOL):
    """정규화: 등급 유지, 끝점을 tol 격자로 반올림, 끝점 순서 고정, 길이 0 제거. 다중집합(Counter)으로 비교한다."""
    from collections import Counter
    out = Counter()
    for cls, x0, y0, x1, y1 in lines:
        a, b = (round(x0 / tol), round(y0 / tol)), (round(x1 / tol), round(y1 / tol))
        if a == b:
            continue
        out[(cls, *(min(a, b) + max(a, b)))] += 1
    return out


def match(a, b, tol: float = TOL) -> dict:
    """선분 일치: 다중집합 교집합 / 더 큰 쪽 개수, 그리고 길이 가중 일치율."""
    na, nb = norm_segs(a, tol), norm_segs(b, tol)
    inter = na & nb
    cnt_a, cnt_b, cnt_i = sum(na.values()), sum(nb.values()), sum(inter.values())

    def length(c):
        return sum(((k[3] - k[1]) ** 2 + (k[4] - k[2]) ** 2) ** 0.5 * n for k, n in c.items()) * tol

    la, lb, li = length(na), length(nb), length(inter)
    return {"count_a": cnt_a, "count_b": cnt_b, "common": cnt_i,
            "ratio": 1.0 if max(cnt_a, cnt_b) == 0 else cnt_i / max(cnt_a, cnt_b),
            "length_ratio": 1.0 if max(la, lb) == 0 else li / max(la, lb)}


def full_lines(eng, vid):
    """전체 재생성: 캐시 없는 기준 그래프로 같은 질의 함수를 처음부터 계산한다."""
    eng.sync()
    eng._touch_pending()
    return RefGraph(_Inputs(eng)).q(D.view_lines, vid)


def fresh_graph_lines(eng, vid):
    """새 증분 그래프(빈 캐시)에서 계산: 캐시가 비어 있을 때의 비용 측정용."""
    from dobim_core.graph import Graph
    eng.sync()
    eng._touch_pending()
    return Graph(_Inputs(eng)).q(D.view_lines, vid)


# ---------------------------------------------------------------- 임의 편집열 속의 증분 = 전체 재생성
def fuzz_views(work: Path, seed: int, n: int, check_every: int = 1, view_every: int = 12, on_diff=None) -> dict:
    """S1 의 임의 커맨드 생성기로 모델을 마구 바꾸면서, 매 검사 시점에 모든 뷰의 증분 선분을 전체 재생성과 비교한다.

    뷰: 입면 4 + 단면 2(고정) + 레벨이 생길 때마다 그 레벨의 평면. 생성기의 undo/redo 가 뷰 생성도 되돌릴 수 있다.
    반환: 검사 횟수, 불일치(diffs), 예외, 일치율 최솟값, 선분 수 통계.
    """
    import random
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    sess = kit.Session(work / f"fuzz-views-{seed}.dobim", guid=GUID)
    eng = sess.eng
    gen = kit.Gen(seed)
    rng = random.Random(seed ^ 0x5EED)
    made = {"plan": set()}
    vid_n = [0]

    def add_view(**kw):
        vid_n[0] += 1
        kw.setdefault("id", _i("4", seed * 1000 + vid_n[0]))
        r = sess.run("view.create", kw)
        return kw["id"] if r["ok"] else None

    fixed = [add_view(name=f"E-{d}", type="elevation", dir=d) for d in "NESW"]
    for k in range(2):
        x, y = rng.randrange(0, 12) * 1000.0, rng.randrange(0, 8) * 1000.0
        dx, dy = (rng.choice([1, 0, 1, 1]) , rng.choice([0, 1, 1, -1]))
        add_view(name=f"S-{k}", type="section", p1=[x - 1000 * dx, y - 1000 * dy], p2=[x + 9000 * dx + 1000 * (1 - abs(dx)), y + 9000 * dy])
    checks, diffs, errors, min_ratio, max_lines, total_lines = 0, [], [], 1.0, 0, 0
    for step in range(n):
        it = gen.next(eng)
        sess.item(it)
        if step % view_every == 0:
            for lv in sorted(eng.st.by_kind.get("level", ())):
                if lv not in made["plan"]:
                    made["plan"].add(lv)
                    add_view(name=f"P-{lv[-6:]}", type="plan", level=lv, cut_height=rng.choice([1200.0, 1200.0, 500.0, 2500.0]))
        if step % 9 == 4 and eng.st.by_kind.get("view"):          # 뷰 설정 편집도 섞는다(절단 높이·절단선·방위)
            vv = rng.choice(sorted(eng.st.by_kind["view"]))
            t = eng.st.el[vv]["params"]["type"]
            if t == "plan":
                sess.run("view.set", {"id": vv, "cut_height": rng.choice([300.0, 1200.0, 2000.0, 3000.0])})
            elif t == "section":
                sess.run("view.set", rng.choice([{"id": vv, "flip": rng.choice([True, False])}, {"id": vv, "depth": rng.choice([2000.0, 20000.0])}]))
            else:
                sess.run("view.set", {"id": vv, "dir": rng.choice(list("NESW"))})
        if step % check_every:
            continue
        for v in sorted(eng.st.by_kind.get("view", ())):
            try:
                inc = eng.g.q(D.view_lines, v)
                ref = full_lines(eng, v)
            except Exception as ex:   # 기하 코드가 어떤 입력에서도 죽지 않아야 한다
                errors.append({"step": step, "view": v, "error": f"{type(ex).__name__}: {ex}"[:200]})
                continue
            checks += 1
            m = match(inc, ref)
            min_ratio = min(min_ratio, m["ratio"])
            max_lines = max(max_lines, len(inc))
            total_lines += len(inc)
            if inc != ref:
                diffs.append({"step": step, "view": v, "ratio": m["ratio"], "n_inc": len(inc), "n_ref": len(ref)})
                if on_diff:
                    on_diff(step, v, inc, ref)
    sess.close()
    return {"seed": seed, "steps": n, "checks": checks, "diffs": diffs[:20], "diff_count": len(diffs), "errors": errors[:20],
            "error_count": len(errors), "min_match_ratio": min_ratio, "max_segments_in_a_view": max_lines,
            "mean_segments": round(total_lines / max(checks, 1), 1)}

# ---------------------------------------------------------------- 영향 뷰 조회(view.affected)의 누락 검사
def fuzz_affected(work: Path, seed: int, n: int) -> dict:
    """임의 편집열에서 view.affected(rev=R) 가 R 로 선분이 실제로 바뀐 뷰를 하나도 빠뜨리지 않는지 본다.

    기준(오라클): 변경 직전에 모든 뷰를 증분으로 계산해 둔 선분(prev)과, 변경 뒤 캐시 없는 전체 재생성 선분(full_lines)을 비교해
    달라진 뷰 집합(changed)을 구한다. view.affected(rev) 의 결과(dep)가 changed 를 모두 포함해야 한다(누락 0).
    과포함 = dep - changed 도 센다(보수적으로 알리는 정도). 뷰 생성·수정·삭제와 undo/redo 도 같은 방식으로 본다.
    """
    import json
    import random
    from dobim_core import engine as E
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    sess = kit.Session(work / f"fuzz-aff-{seed}.dobim", guid=GUID)
    eng = sess.eng
    gen = kit.Gen(seed)
    rng = random.Random(seed ^ 0xAFF)
    made, vid_n = set(), [0]
    stat = {"ops": 0, "checked": 0, "changed_views": 0, "reported_views": 0, "over_reported": 0}
    missed, missed_keys, errors = [], [], []

    def live():
        return sorted(eng.st.by_kind.get("view", ()))

    def snapshot():
        eng.sync()
        eng._touch_pending()
        return {v: eng.g.q(D.view_lines, v) for v in live()}

    def observe(prev, resp, label):
        if not resp.get("ok") or resp.get("rev") is None:
            return
        stat["ops"] += 1
        aff = sess.run("view.affected", {"rev": resp["rev"]})
        if not aff["ok"]:
            errors.append({"op": label, "rev": resp["rev"], "error": aff["error"]["code"]})
            return
        cache = {i["id"] for i in sess.run("view.affected", {})["result"]["items"]}
        dep = {i["id"] for i in aff["result"]["items"]}
        now = live()
        changed = {v for v in now if v not in prev or full_lines(eng, v) != prev[v]}
        stat["checked"] += 1
        # 키 따라가기만(낡은 뷰 포함 없이): 모든 뷰가 최신이던 직후라 의존 목록이 정확하므로 이것만으로도 누락이 없어야 한다
        eng.sync()
        eng._touch_pending()
        txn = eng.conn.execute("SELECT txn_id FROM op_log WHERE seq=?", (resp["rev"],)).fetchone()[0]
        keys = E.input_keys_of([x for en in eng.txn_entries(txn) for x in E.invert(json.loads(en[4]))])
        walk = set(D.views_depending_on(eng.g, keys, now, include_not_fresh=False))
        if changed - walk:
            missed_keys.append({"op": label, "rev": resp["rev"], "missed": sorted(v[-6:] for v in changed - walk)})
        stat["changed_views"] += len(changed)
        stat["reported_views"] += len(dep)
        stat["over_reported"] += len(dep - changed)
        if changed - dep:
            missed.append({"op": label, "rev": resp["rev"], "missed": sorted(v[-6:] for v in changed - dep)})
        if not cache <= dep:
            errors.append({"op": label, "rev": resp["rev"], "error": "cache 모드의 뷰가 dependency 모드에 없다"})
        if not dep <= set(now):
            errors.append({"op": label, "rev": resp["rev"], "error": "삭제된 뷰가 결과에 있다"})

    def vcreate(**kw):
        vid_n[0] += 1
        kw.setdefault("id", _i("4", seed * 1000 + vid_n[0]))
        prev = snapshot()
        r = sess.run("view.create", kw)
        observe(prev, r, "view.create")

    for d in "NESW":
        vcreate(name=f"E-{d}", type="elevation", dir=d)
    vcreate(name="S-0", type="section", p1=[0.0, 1000.0], p2=[9000.0, 1000.0])
    for step in range(n):
        prev = snapshot()
        it = gen.next(eng)
        r = sess.item(it)
        observe(prev, r, it.get("name") or "batch")
        if step % 10 == 0:
            for lv in sorted(eng.st.by_kind.get("level", ())):
                if lv not in made:
                    made.add(lv)
                    vcreate(name=f"P-{lv[-6:]}", type="plan", level=lv, cut_height=rng.choice([1200.0, 500.0, 2500.0]))
        if step % 7 == 3 and live():
            vv = rng.choice(live())
            t = eng.st.el[vv]["params"]["type"]
            args = {"id": vv, **({"cut_height": rng.choice([300.0, 1200.0, 3000.0])} if t == "plan" else
                                 {"depth": rng.choice([2000.0, 20000.0])} if t == "section" else {"dir": rng.choice(list("NESW"))})}
            prev = snapshot()
            observe(prev, sess.run("view.set", args), "view.set")
        if step % 23 == 11 and live():
            prev = snapshot()
            observe(prev, sess.run("view.delete", {"id": rng.choice(live())}), "view.delete")
    sess.close()
    return {"seed": seed, "steps": n, **stat, "missed_count": len(missed), "missed": missed[:10],
            "missed_key_walk_count": len(missed_keys), "missed_key_walk": missed_keys[:10], "error_count": len(errors),
            "errors": errors[:10], "precision": round(1 - stat["over_reported"] / max(stat["reported_views"], 1), 3)}

