# SPDX-License-Identifier: MPL-2.0
"""S8 로그 기반 병합: 공통 조상에서 갈라진 두 프로젝트(.dobim)의 op_log 를 읽어 3-way 로 합친다.

이 모듈은 ifcopenshell 을 import 하지 않는다(레지스트리 로드가 가벼워야 한다). 엔진·IFC 없이 두 SQLite 파일의 로그만으로
계획(Plan)을 만든다. 계획을 상태에 적용하는 일은 commands.py 의 merge.apply 가 효과(emit)로 한다.

모델 (요약)
- 단위(unit): 병합의 최소 단위. 요소 존재/종류 ("el",id,"@"), 요소 파라미터 ("el",id,"p",키), 관계 ("rel",종류,src,dst),
  속성 ("pset",id,셋,속성), 재료 ("mat",id). 값은 정규 JSON 문자열이라 1 / 1.0 / true 가 구분된다.
- 공통 조상: 두 로그의 공통 접두(항목 전체가 같은 구간). 트랜잭션 경계로 맞춘다. 조상 상태 = 그 접두의 효과를 접은 것.
- 각 쪽의 최종 상태 = 로그 전체의 효과를 접은 것. 실행 취소·다시 실행도 로그 항목이므로 따로 다루지 않는다
  (한쪽이 취소한 것은 그 쪽의 순변화가 없는 것으로 보인다).
- 3-way: 단위마다 (조상, 우리, 상대). 한쪽만 바꿨으면 그 값, 같게 바꿨으면 그 값, 서로 다르게 바꿨으면 충돌.
  결과는 두 쪽을 바꿔 호출해도 같다(교환 법칙). 충돌은 조용히 덮지 않는다.
- 의미 충돌: 단위 병합이 깨끗해도 합친 결과가 규칙을 어길 수 있다(호스트 삭제 + 문 배치, 벽 단축 + 문 이동, 같은 자리 벽 중복).
  합친 상태를 재생성(queries 의 파생 질의 + 규칙)해서 검사한다. "병합이 새로 만든" 위반만 충돌로 본다
  (우리 쪽이나 상대 쪽 어느 한쪽이 이미 가진 위반은 병합 탓이 아니다).
- 해결은 명시적으로만: resolve=[{key, take}]. take=ours/theirs 는 그 충돌에서 해당 쪽 값을 택한다
  (의미 충돌은 반대쪽이 기여한 변경을 조상 값으로 되돌린다).
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from math import sqrt
from pathlib import Path
from typing import Any

from dobim_core import queries as Q
from dobim_core.errors import DobimError
from dobim_core.graph import RefGraph

MAX_ROUNDS = 8            # 의미 충돌 해결 반복 상한(되돌린 뒤 새 위반이 생기는 연쇄)
OPS_SHOWN = 4             # 충돌 하나에 보여 주는 양쪽 op 수
FIELDS_SHOWN = 8
KEY_MAX = 120             # 충돌 키 길이 상한(커서 128자 안에 들어가게)
EPS = 1e-6


# ---------------------------------------------------------------- 결정성 도우미
def _order(items, key=None):
    """순서가 결과에 영향을 주는 모든 곳은 이 함수로 정렬한다(변이 시험이 이 함수를 깨서 검사의 이빨을 확인한다)."""
    return sorted(items, key=key)


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(obj: Any) -> str:
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


def _ckey(key: str) -> str:
    return key if len(key) <= KEY_MAX else key[:KEY_MAX - 14] + "~" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 로그 읽기
LOG_SQL = "SELECT seq, txn_id, actor, base_rev, op, args, inverse FROM op_log ORDER BY seq"


def read_log(conn: sqlite3.Connection) -> tuple[str, list[tuple]]:
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    if meta.get("schema_version") != "1":
        raise DobimError("USAGE", "지원하지 않는 프로젝트 형식이다")
    return meta["project_uuid"], conn.execute(LOG_SQL).fetchall()


def open_other(path: str | Path) -> sqlite3.Connection:
    """상대 프로젝트를 읽기 전용으로 연다. 다른 프로세스가 쓰는 중이어도 WAL 이라 읽을 수 있다."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as ex:
        raise DobimError("IO_ERROR", f"상대 프로젝트를 열 수 없다: {type(ex).__name__}", path="/other") from ex


def load_other(path: str | Path) -> tuple[str, list[tuple]]:
    conn = open_other(path)
    try:
        conn.execute("BEGIN")
        try:
            return read_log(conn)
        finally:
            conn.execute("COMMIT")
    except sqlite3.Error as ex:
        raise DobimError("IO_ERROR", f"상대 프로젝트의 로그를 읽을 수 없다: {type(ex).__name__}", path="/other",
                         hint="프로젝트 파일(.dobim)이 맞는지, 다른 프로그램이 잠그고 있지 않은지 확인하세요") from ex
    finally:
        conn.close()


def fork_count(a: list[tuple], b: list[tuple]) -> int:
    """공통 접두 항목 수. 항목 전체(seq·txn·행위자·op·인자·역효과)가 같아야 공통이다. 시각(ts)은 보지 않는다.
    트랜잭션 중간에서 끊기면 그 트랜잭션 앞으로 물린다(일부만 같은 배치는 조상에 넣지 않는다)."""
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    while n > 0 and ((n < len(a) and a[n][1] == a[n - 1][1]) or (n < len(b) and b[n][1] == b[n - 1][1])):
        n -= 1
    return n


# ---------------------------------------------------------------- 로그 -> 단위 (접기)
def _row_units(eid: str, row: dict):
    yield ("el", eid, "@"), canon({k: row[k] for k in ("kind", "ifc_class", "origin", "family_id")})
    for k, v in row["params"].items():
        yield ("el", eid, "p", k), canon(v)


def _apply(units: dict, e: list, touch: dict | None, who: tuple) -> None:
    t = e[0]

    def mark(u):
        if touch is not None:
            touch[u] = who

    if t == "el":
        _, eid, old, new = e
        ou = dict(_row_units(eid, old)) if old is not None else {}
        nu = dict(_row_units(eid, new)) if new is not None else {}
        for u in ou:
            if u not in nu:
                units.pop(u, None)
                mark(u)
        for u, v in nu.items():
            if ou.get(u) != v:
                units[u] = v
                mark(u)
    elif t == "rel":
        _, kind, src, dst, _o, nw = e
        u = ("rel", kind, src, dst)
        if nw:
            units[u] = "1"
        else:
            units.pop(u, None)
        mark(u)
    elif t == "pset":
        _, eid, pset, prop, _o, nw = e
        u = ("pset", eid, pset, prop)
        if nw is None:
            units.pop(u, None)
        else:
            units[u] = canon(nw)
        mark(u)
    elif t == "mat":
        _, eid, _o, nw = e
        u = ("mat", eid)
        if nw is None:
            units.pop(u, None)
        else:
            units[u] = canon(nw)
        mark(u)
    else:
        raise DobimError("INTERNAL", f"알 수 없는 효과: {t}")


def fold(rows: list[tuple], units: dict | None = None, touch: dict | None = None) -> dict:
    """로그 항목들의 효과를 차례로 접어 단위 사전을 만든다. touch 가 있으면 단위마다 마지막으로 건드린 (seq, 행위자, op) 를 적는다."""
    from dobim_core.engine import invert   # 지연 import: 레지스트리 로드가 엔진(ifcopenshell)에 의존하지 않게
    units = {} if units is None else units
    for seq, _txn, actor, _b, op, _a, inv in rows:
        who = (seq, actor, op)
        for e in invert(json.loads(inv)):
            _apply(units, e, touch, who)
    return units


def elems_of(u: tuple) -> tuple:
    return (u[2], u[3]) if u[0] == "rel" else (u[1],)


def fingerprint(rows: list[tuple]) -> str:
    """로그 접두의 지문(병합 기록용)."""
    return digest([list(r) for r in rows])


# ---------------------------------------------------------------- 뷰 (단위 -> 모델)
class View:
    """단위 사전을 요소·관계 모양으로 푼 읽기 전용 모델. 파생 질의(RefGraph)의 입력 로더이기도 하다."""

    def __init__(self, units: dict):
        self.units = units
        self.rows: dict[str, dict] = {}
        rels = set()
        for u, v in units.items():
            if u[0] == "el":
                r = self.rows.setdefault(u[1], {"params": {}})
                if u[2] == "@":
                    r.update(json.loads(v))
                else:
                    r["params"][u[3]] = json.loads(v)
            elif u[0] == "rel":
                rels.add(u[1:])
        self.exists = {e for e, r in self.rows.items() if "kind" in r}
        self.orphans = ({e for e, r in self.rows.items() if "kind" not in r}
                        | {u[1] for u in units if u[0] in ("pset", "mat") and u[1] not in self.exists})
        self.rels = _order(rels)
        self.out: dict[tuple, list] = {}
        self.inn: dict[tuple, list] = {}
        for kind, s, d in self.rels:
            if s in self.exists and d in self.exists:   # 매달린 관계는 파생 질의에 보이지 않는다(따로 위반으로 잡는다)
                self.out.setdefault((kind, s), []).append(d)
                self.inn.setdefault((kind, d), []).append(s)

    def kind(self, e):
        return self.rows[e]["kind"]

    def ids(self, kind: str):
        return _order(e for e in self.exists if self.rows[e]["kind"] == kind)

    def p(self, e):
        return self.rows[e]["params"]

    # RefGraph 입력 로더
    def load(self, key):
        t = key[0]
        if t == "el":
            return self.rows.get(key[1])
        if t == "rel_out":
            return tuple(self.out.get((key[1], key[2]), ()))
        if t == "rel_in":
            return tuple(self.inn.get((key[1], key[2]), ()))
        raise KeyError(key)


# ---------------------------------------------------------------- 의미 규칙 (병합 결과의 재생성 검사)
def _v(rule, elements, reason, detail=None, focus=None, disc=""):
    return {"rule": rule, "elements": tuple(_order(set(elements))), "disc": disc, "reason": reason,
            "detail": detail or {}, "focus": focus}


def vkey(v: dict) -> tuple:
    return (v["rule"], v["elements"], v["disc"])


def semantic_key(v: dict) -> str:
    return _ckey(f"SEM:{v['rule']}:{','.join(v['elements'])}" + (f":{v['disc']}" if v["disc"] else ""))


def _pu(e, k):
    return ("el", e, "p", k)


def violations(view: View) -> list[dict]:
    out: list[dict] = []
    ex = view.exists
    # 1) 무결성: 매달린 관계, 주인 없는 단위
    for kind, s, d in view.rels:
        if s in ex and d in ex:
            continue
        code = {"hosts": "HOST_DELETED", "joins": "JOIN_TARGET_DELETED", "on_level": "LEVEL_DELETED"}.get(kind, "REL_DANGLING")
        names = {"HOST_DELETED": "호스트 벽이 지워졌는데 문이 그 벽에 놓여 있다", "JOIN_TARGET_DELETED": "조인 대상 벽이 지워졌다",
                 "LEVEL_DELETED": "벽이 놓인 레벨이 지워졌다"}
        out.append(_v(code, (s, d), names.get(code, "관계의 한쪽 끝이 없다"),
                      {"rel": [kind, s, d], "missing": [x for x in (s, d) if x not in ex]}, disc=kind))
    for e in _order(view.orphans):
        out.append(_v("ORPHAN_UNIT", (e,), "요소가 없는데 그 요소의 속성·재료·파라미터가 남아 있다"))
    hosted = {d for kind, _s, d in view.rels if kind == "hosts"}
    leveled = {w for kind, w, _d in view.rels if kind == "on_level"}
    for d in view.ids("door"):
        if d not in hosted:
            out.append(_v("HOST_MISSING", (d,), "문에 호스트 벽이 없다"))
    for w in view.ids("wall"):
        if w not in leveled:
            out.append(_v("LEVEL_MISSING", (w,), "벽에 레벨이 없다"))
    # 2) 호스팅: 문이 호스트 벽(원래 길이) 안에 있는가 (door.place/door.set 의 규칙)
    doors_on: dict[str, list] = {}
    for kind, s, d in view.rels:
        if kind == "hosts" and s in ex and d in ex and view.kind(s) == "wall" and view.kind(d) == "door":
            doors_on.setdefault(s, []).append(d)
    for w in _order(doors_on):
        ax = view.p(w).get("axis")
        if not ax or len(ax) != 4:
            continue
        length = sqrt((ax[2] - ax[0]) ** 2 + (ax[3] - ax[1]) ** 2)
        spans = []
        for d in _order(doors_on[w]):
            p = view.p(d)
            if "offset" not in p or "width" not in p:
                continue
            lo, hi = p["offset"] - p["width"] / 2, p["offset"] + p["width"] / 2
            spans.append((lo, hi, d))
            if lo < 0 or hi > length:
                out.append(_v("DOOR_OUT_OF_HOST", (w, d), f"문이 호스트 벽({length:.1f}mm) 밖으로 나간다",
                              {"wall_length": length, "door_span": [lo, hi]},
                              focus=(_pu(w, "axis"), _pu(d, "offset"), _pu(d, "width"))))
        spans.sort()
        for i, (lo, hi, d) in enumerate(spans):
            for lo2, hi2, d2 in spans[i + 1:]:
                if lo2 >= hi - EPS:
                    break
                out.append(_v("DOOR_OVERLAP", (d, d2), "같은 벽의 두 문이 겹친다", {"host": w, "spans": [[lo, hi], [lo2, hi2]]},
                              focus=(_pu(d, "offset"), _pu(d, "width"), _pu(d2, "offset"), _pu(d2, "width"))))
    # 3) 같은 레벨의 같은 자리 벽 (축선 끝점 3자리 반올림, 방향 무시)
    groups: dict[tuple, list] = {}
    for w in view.ids("wall"):
        ax = view.p(w).get("axis")
        lv = tuple(view.out.get(("on_level", w), ()))
        if ax and len(ax) == 4:
            a, b = (round(ax[0], 3), round(ax[1], 3)), (round(ax[2], 3), round(ax[3], 3))
            groups.setdefault((lv, tuple(sorted((a, b)))), []).append(w)
    for k in _order(groups):
        ws = groups[k]
        for i, w1 in enumerate(ws):
            for w2 in ws[i + 1:]:
                out.append(_v("DUPLICATE_WALL", (w1, w2), "같은 레벨의 같은 자리에 벽이 겹쳐 있다", {"axis": [list(k[1][0]), list(k[1][1])]}))
    # 4) 레벨 이름 중복 (level.create 의 규칙)
    names: dict[str, list] = {}
    for lv in view.ids("level"):
        names.setdefault(view.p(lv).get("name"), []).append(lv)
    for nm in _order(n for n in names if n is not None):
        ls = names[nm]
        for i, l1 in enumerate(ls):
            for l2 in ls[i + 1:]:
                out.append(_v("LEVEL_NAME_DUPLICATE", (l1, l2), f"같은 이름의 레벨이 둘이다: {nm}", {"name": nm},
                              focus=(_pu(l1, "name"), _pu(l2, "name"))))
    # 5) 재생성 검사: 실제 파생 질의(조인 정리, 문 상태)로 합친 모델을 다시 계산한다 (architecture 11절 4단계)
    g = RefGraph(view)
    for w in view.ids("wall"):
        if not view.out.get(("on_level", w)):
            continue
        try:
            status = g.q(Q.wall_trim, w)[2]
        except (KeyError, IndexError, ZeroDivisionError, TypeError):
            continue
        if status != "ok":
            partners = _order(set(view.out.get(("joins", w), ())) | set(view.inn.get(("joins", w), ())))
            out.append(_v("JOIN_DEGENERATE", (w, *partners), "조인 결과 벽 길이가 하한 미만이다(조인 무시)",
                          {"status": status},
                          focus=tuple(_pu(x, "axis") for x in (w, *partners)) +
                          tuple(("rel", "joins", *sorted((w, x))) for x in partners)))
    for d in view.ids("door"):
        hs = view.inn.get(("hosts", d), [])
        if len(hs) != 1:
            continue
        w = hs[0]
        try:
            status = g.q(Q.door_status, d)
        except (KeyError, IndexError, ZeroDivisionError, TypeError):
            continue
        if status != "ok":
            partners = _order(set(view.out.get(("joins", w), ())) | set(view.inn.get(("joins", w), ())))
            out.append(_v("DOOR_OUTSIDE_TRIMMED", (d, w, *partners), "조인으로 다듬어진 호스트 벽 밖으로 문이 나간다",
                          {"status": status},
                          focus=(_pu(d, "offset"), _pu(d, "width")) + tuple(_pu(x, "axis") for x in (w, *partners)) +
                          tuple(("rel", "joins", *sorted((w, x))) for x in partners)))
    out.sort(key=lambda v: (v["rule"], v["elements"], v["disc"]))
    return out


# ---------------------------------------------------------------- 계획
NO_FORCE = object()


def _force(u, vb, vo, vt, touch_o, touch_t):
    """양쪽이 서로 다르게 바꾼 단위를 자동으로 정할 수 있는가. 보수적 정책: 절대 정하지 않는다(항상 NO_FORCE → 충돌).
    변이 시험이 이 자리를 "마지막 쓴 쪽이 이김" 같은 조용한 덮어쓰기로 바꿔 검사의 이빨을 확인한다."""
    return NO_FORCE


def _ops(touch: dict, units) -> tuple[list, int]:
    seen = {}
    for u in units:
        w = touch.get(u)
        if w is not None:
            seen[w[0]] = w
    ws = [seen[s] for s in _order(seen)]
    return [{"seq": s, "op": op, "actor": ac} for s, ac, op in ws[:OPS_SHOWN]], max(0, len(ws) - OPS_SHOWN)


def _val(v):
    return None if v is None else json.loads(v)


def _label(u: tuple) -> str:
    if u[0] == "el":
        return "존재/종류" if u[2] == "@" else f"params.{u[3]}"
    if u[0] == "pset":
        return f"pset {u[2]}.{u[3]}"
    if u[0] == "mat":
        return "재료"
    return f"관계 {u[1]}"


class Plan:
    def __init__(self):
        self.fork = 0               # 공통 조상 리비전(공통 접두 항목 수)
        self.ours_rev = 0
        self.other_rev = 0
        self.merged: dict = {}
        self.conflicts: list[dict] = []     # 미해결
        self.resolved: list[str] = []
        self.unused: list[str] = []         # 어떤 충돌과도 맞지 않은 해결 지시 키
        self.semantic_checked = True        # 단위 충돌이 남았으면 의미 검사를 하지 않는다(보고가 방향에 기대지 않게)
        self.ours: dict = {}
        self.theirs: dict = {}
        self.base: dict = {}
        self.stats: dict = {}

    @property
    def clean(self) -> bool:
        return not self.conflicts

    def digest(self) -> str | None:
        return digest(sorted(self.merged.items())) if self.clean else None


def build_plan(ours_rows: list[tuple], theirs_rows: list[tuple], resolve: list[dict] | None = None) -> Plan:
    """두 로그에서 병합 계획을 만든다. 순수 함수: 파일·시각·난수를 보지 않는다."""
    res = {}
    for r in resolve or []:
        res[r["key"]] = r["take"]
    n = fork_count(ours_rows, theirs_rows)
    base = fold(ours_rows[:n])
    to, tt = {}, {}
    ours = fold(ours_rows[n:], dict(base), to)
    theirs = fold(theirs_rows[n:], dict(base), tt)
    plan = Plan()
    plan.fork, plan.ours_rev, plan.other_rev = n, len(ours_rows), len(theirs_rows)
    plan.base, plan.ours, plan.theirs = base, ours, theirs

    # ---- 1) 단위별 3-way
    merged: dict = {}
    contested: list[tuple] = []
    for u in _order(set(base) | set(ours) | set(theirs)):
        vb, vo, vt = base.get(u), ours.get(u), theirs.get(u)
        if vo == vt:
            v = vo
        elif vo == vb:
            v = vt
        elif vt == vb:
            v = vo
        else:
            forced = _force(u, vb, vo, vt, to, tt)
            if forced is NO_FORCE:
                contested.append(u)
                v = vo   # 임시값(미해결이면 적용하지 않는다)
            else:
                v = forced[0]
        if v is not None:
            merged[u] = v

    by_elem: dict[str, list] = {}
    for u in _order(set(base) | set(ours) | set(theirs)):
        for e in elems_of(u):
            by_elem.setdefault(e, []).append(u)

    def ex(units, e):
        return ("el", e, "@") in units

    def changed(side, e, rel=False):
        return [u for u in by_elem.get(e, ()) if (rel or u[0] != "rel") and side.get(u) != base.get(u)]

    groups: list[dict] = []
    in_group: set = set()
    # 요소 삭제 vs 수정
    for e in _order(by_elem):
        eb, eo, et = ex(base, e), ex(ours, e), ex(theirs, e)
        if eb and eo != et:
            deleter = "ours" if not eo else "theirs"
            other = theirs if deleter == "ours" else ours   # 지우지 않은 쪽
            mods = [u for u in changed(other, e) if u != ("el", e, "@")]
            if mods:
                fam = list(by_elem[e])
                groups.append({"type": "DELETE_MODIFY", "key": f"DELETE_MODIFY:{e}", "elements": [e], "units": fam,
                               "field": "요소", "deleter": deleter, "mods": mods,
                               "reason": f"{'우리' if deleter == 'ours' else '상대'} 쪽은 요소를 지웠고 {'상대' if deleter == 'ours' else '우리'} 쪽은 수정했다"})
                in_group.update(fam)
        elif not eb and eo and et:
            diff = [u for u in by_elem[e] if ours.get(u) != theirs.get(u)]
            if diff:
                fam = list(by_elem[e])
                groups.append({"type": "ID_COLLISION", "key": f"ID_COLLISION:{e}", "elements": [e], "units": fam, "field": "요소",
                               "reason": "양쪽이 같은 id 의 요소를 서로 다른 내용으로 새로 만들었다(id 생성이 가지별로 구분되지 않음)"})
                in_group.update(fam)
    for u in contested:
        if u in in_group:
            continue
        if u[0] == "pset":
            groups.append({"type": "PSET_SAME_PROP", "key": _ckey(f"PSET_SAME_PROP:{u[1]}:{u[2]}.{u[3]}"), "elements": [u[1]], "units": [u],
                           "field": _label(u), "reason": "같은 속성을 양쪽이 서로 다른 값으로 바꿨다"})
        elif u[0] == "mat":
            groups.append({"type": "MATERIAL_SAME", "key": f"MATERIAL_SAME:{u[1]}", "elements": [u[1]], "units": [u], "field": _label(u),
                           "reason": "같은 요소의 재료를 양쪽이 서로 다르게 지정했다"})
        else:
            groups.append({"type": "SAME_FIELD", "key": _ckey(f"SAME_FIELD:{u[1]}:{u[3] if u[2] == 'p' else '@'}"), "elements": [u[1]],
                           "units": [u], "field": _label(u), "reason": "같은 요소의 같은 필드를 양쪽이 서로 다른 값으로 바꿨다"})
    groups.sort(key=lambda g: g["key"])
    keys = [g["key"] for g in groups]
    assert len(keys) == len(set(keys)), "충돌 키가 겹친다"

    def take(units, side):
        for u in units:
            v = side.get(u)
            if v is None:
                merged.pop(u, None)
            else:
                merged[u] = v

    used = set()
    pending_unit = []
    for g in groups:
        t = res.get(g["key"])
        if t is None:
            pending_unit.append(g)
            continue
        take(g["units"], ours if t == "ours" else theirs)
        used.add(g["key"])
        plan.resolved.append(g["key"])

    # ---- 2) 의미 검사 (재생성). 병합이 새로 만든 위반만 충돌이다
    vo_keys = {vkey(v) for v in violations(View(ours))}
    vt_keys = {vkey(v) for v in violations(View(theirs))}

    def side_only(u, side):
        other = theirs if side is ours else ours
        return side.get(u) != base.get(u) and other.get(u) == base.get(u)

    def expand(us, side):
        """요소를 만들거나 지운 변경이면 그 요소의 (이 쪽만의) 변경 전체를 함께 되돌려야 반쪽짜리 요소가 남지 않는다."""
        out = set(us)
        for e in {x for u in us for x in elems_of(u)}:
            if side.get(("el", e, "@")) != base.get(("el", e, "@")):
                out.update(u for u in by_elem.get(e, ()) if side_only(u, side))
        return out

    def contrib(v, side):
        """위반 v 에 이 쪽이 기여한 변경 단위(병합 결과가 이 쪽 값을 택한 것)."""
        if v["focus"] is not None:
            cand = list(v["focus"])
        else:
            es = set(v["elements"])
            # 호스트 벽이 지워졌으면 연쇄로 지워진 문도 같은 변경의 일부다
            for k in _order(k for k in base if k[0] == "rel" and k[1] == "hosts"):
                if k[2] in es and not ex(merged, k[2]):
                    es.add(k[3])
            cand = [u for e in _order(es) for u in by_elem.get(e, ())]
        us = [u for u in cand if side_only(u, side) and merged.get(u) == side.get(u)]
        return _order(expand(us, side))

    applied_sem: set = set()
    unresolved_sem: list = []
    # 단위 충돌이 남아 있으면 의미 검사를 하지 않는다: 임시 병합 상태는 어느 쪽이 "우리"냐에 따라 달라 보고가 방향에 기대게 된다.
    # 단위 충돌을 해결한 뒤(미리보기를 resolve 와 함께 다시 읽어) 의미 충돌을 본다.
    plan.semantic_checked = not pending_unit
    for _ in range(MAX_ROUNDS if plan.semantic_checked else 0):
        new = [v for v in violations(View(merged)) if vkey(v) not in vo_keys and vkey(v) not in vt_keys]
        unresolved_sem = []
        reverted = False
        for v in new:
            k = semantic_key(v)
            t = res.get(k)
            ou, tu = contrib(v, ours), contrib(v, theirs)
            if t is not None and k not in applied_sem and ((t == "ours" and tu) or (t == "theirs" and ou)):
                for u in (tu if t == "ours" else ou):   # 반대쪽이 기여한 변경을 조상 값으로 되돌린다
                    b = base.get(u)
                    if b is None:
                        merged.pop(u, None)
                    else:
                        merged[u] = b
                applied_sem.add(k)
                used.add(k)
                plan.resolved.append(k)
                reverted = True
                break   # 되돌린 뒤 처음부터 다시 검사한다(연쇄)
            unresolved_sem.append({"v": v, "key": k, "ours_units": ou, "theirs_units": tu})
        if not reverted:
            break
    open_keys = {r["key"] for r in unresolved_sem}
    # 아무 충돌과도 맞지 않은 해결 지시는 무시한다(충돌은 키가 맞아야만 풀리므로 데이터를 덮을 수 없다). 이미 합쳐진 뒤 같은 지시로 다시
    # 실행해도 변경 없음이 되게 하려는 것이고, 호출한 쪽에는 경고로 알린다.
    plan.unused = [k for k in _order(res) if k not in used and k not in open_keys] if plan.semantic_checked else []
    plan.merged = merged

    # ---- 3) 보고서
    conflicts = []
    for g in pending_unit:
        us = g["units"]
        if g["type"] in ("DELETE_MODIFY", "ID_COLLISION"):
            ch_o = [u for u in us if ours.get(u) != base.get(u)]
            ch_t = [u for u in us if theirs.get(u) != base.get(u)]
            oo, om = _ops(to, ch_o)
            to_, tm = _ops(tt, ch_t)
            item = {"key": g["key"], "type": g["type"], "elements": g["elements"], "field": g["field"], "reason": g["reason"],
                    "ours": {"change": "deleted" if g["type"] == "DELETE_MODIFY" and g.get("deleter") == "ours" else
                             ("created" if g["type"] == "ID_COLLISION" else "modified"),
                             "fields": sorted({_label(u) for u in ch_o if u[0] != "rel"})[:FIELDS_SHOWN], "ops": oo, "ops_more": om},
                    "theirs": {"change": "deleted" if g["type"] == "DELETE_MODIFY" and g.get("deleter") == "theirs" else
                               ("created" if g["type"] == "ID_COLLISION" else "modified"),
                               "fields": sorted({_label(u) for u in ch_t if u[0] != "rel"})[:FIELDS_SHOWN], "ops": to_, "ops_more": tm},
                    "take": ["ours", "theirs"]}
        else:
            u = us[0]
            oo, om = _ops(to, us)
            to_, tm = _ops(tt, us)
            item = {"key": g["key"], "type": g["type"], "elements": g["elements"], "field": g["field"], "reason": g["reason"],
                    "base": _val(base.get(u)),
                    "ours": {"value": _val(ours.get(u)), "ops": oo, "ops_more": om},
                    "theirs": {"value": _val(theirs.get(u)), "ops": to_, "ops_more": tm},
                    "take": ["ours", "theirs"]}
        conflicts.append(item)
    for r in unresolved_sem:
        v = r["v"]
        oo, om = _ops(to, r["ours_units"])
        to_, tm = _ops(tt, r["theirs_units"])
        takes = [t for t, need in (("ours", r["theirs_units"]), ("theirs", r["ours_units"])) if need]
        conflicts.append({"key": r["key"], "type": v["rule"], "semantic": True, "elements": list(v["elements"]),
                          "field": "의미 규칙", "reason": v["reason"], "detail": v["detail"],
                          "ours": {"fields": sorted({_label(u) for u in r["ours_units"]})[:FIELDS_SHOWN], "ops": oo, "ops_more": om},
                          "theirs": {"fields": sorted({_label(u) for u in r["theirs_units"]})[:FIELDS_SHOWN], "ops": to_, "ops_more": tm},
                          "take": takes})
    conflicts.sort(key=lambda c: c["key"])
    plan.conflicts = conflicts
    plan.stats = {
        "ours_changed_units": sum(1 for u in set(base) | set(ours) if ours.get(u) != base.get(u)),
        "theirs_changed_units": sum(1 for u in set(base) | set(theirs) if theirs.get(u) != base.get(u)),
        "ours_entries": len(ours_rows) - n, "theirs_entries": len(theirs_rows) - n,
    }
    return plan


# ---------------------------------------------------------------- 계획 -> 효과 (merge.apply)
def rows_of(units: dict) -> dict:
    return {e: {"kind": r["kind"], "ifc_class": r["ifc_class"], "origin": r["origin"], "family_id": r["family_id"], "params": r["params"]}
            for e, r in View(units).rows.items() if "kind" in r}


def emit_plan(ctx, plan: Plan) -> dict:
    """ours -> merged 로 가는 효과를 ctx 로 내보낸다. 순서는 결정적이다:
    (1) 요소 생성·수정 (2) 속성·재료 (삭제될 요소의 속성 해제 포함) (3) 관계 (4) 요소 삭제."""
    ours, merged = plan.ours, plan.merged
    orows, mrows = rows_of(ours), rows_of(merged)
    cnt = {"created": 0, "modified": 0, "deleted": 0, "psets": 0, "materials": 0, "relations": 0}
    rank = {"level": 0, "wall": 1, "door": 2}   # 호스트가 먼저 생기고, 지울 때는 호스팅된 것이 먼저 사라진다(element.delete 와 같은 순서)
    key = lambda e, rows: (rank.get(rows[e]["kind"], 9), e)  # noqa: E731
    for e in _order(mrows, key=lambda e: key(e, mrows)):
        if orows.get(e) != mrows[e]:
            cnt["created" if e not in orows else "modified"] += 1
            ctx.put_el(e, mrows[e])
    for u in _order(u for u in set(ours) | set(merged) if u[0] == "pset" and ours.get(u) != merged.get(u)):
        cnt["psets"] += 1
        ctx.set_prop(u[1], u[2], u[3], _val(merged.get(u)))
    for u in _order(u for u in set(ours) | set(merged) if u[0] == "mat" and ours.get(u) != merged.get(u)):
        cnt["materials"] += 1
        ctx.set_material(u[1], _val(merged.get(u)))
    for u in _order(u for u in set(ours) | set(merged) if u[0] == "rel" and ours.get(u) != merged.get(u)):
        cnt["relations"] += 1
        ctx.rel(u[1], u[2], u[3], u in merged)
    for e in _order((e for e in orows if e not in mrows), key=lambda e: (-key(e, orows)[0], e)):
        cnt["deleted"] += 1
        ctx.put_el(e, None)
    return cnt


def conflict_error(plan: Plan, shown: int = 5) -> DobimError:
    by_type: dict[str, int] = {}
    for c in plan.conflicts:
        by_type[c["type"]] = by_type.get(c["type"], 0) + 1
    brief = [{"key": c["key"], "type": c["type"], "elements": c["elements"][:3]} for c in plan.conflicts[:shown]]
    return DobimError("MERGE_CONFLICT", f"병합 충돌 {len(plan.conflicts)}건: 자동으로 합치지 않았다", path="/other",
                      hint="merge.preview 로 전체 충돌을 보고, resolve=[{key, take: ours|theirs}] 로 명시적으로 해결하세요",
                      details={"total": len(plan.conflicts), "by_type": by_type, "conflicts": brief})
