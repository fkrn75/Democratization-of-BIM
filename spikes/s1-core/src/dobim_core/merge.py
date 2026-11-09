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

import hashlib
import json
import os
import re
import sqlite3
from math import isfinite, sqrt
from pathlib import Path
from typing import Any

from dobim_core import queries as Q
from dobim_core.errors import DobimError
from dobim_core.graph import RefGraph
from dobim_core.kinds import (ELEMENT_KINDS, NAME_MAX, NON_IFC_KINDS, REL_KINDS, class_error, id_ok as _id_ok, invert,
                              num_ok as _num, row_params_error, text_ok as _text_ok)

DUP_PAIRS_MAX_GROUP = 40  # 같은 자리 벽 중복: 이 수까지는 쌍(n^2/2)으로 보고하고, 더 큰 묶음은 벽마다 하나씩(n)으로 보고한다(겹친 벽 1200개가 쌍 72만 개가 되는 것을 막는다)
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


# ---------------------------------------------------------------- 불변식 (commands.py 의 입력 검사와 같은 규칙. 같은지는 시험이 대조한다)
# 요소 종류·관계 종류·효과 태그의 목록과 파라미터 검사는 kinds.py 가 정본이다(여기서 따로 갖지 않는다).
PROP_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
REL_KINDS_NAMES = tuple(REL_KINDS)
RESERVED_PSET = "DoBIM_Intent"   # 투영이 소유한 예약 속성셋: pset.set/pset.delete 가 거부하므로 로그에 있을 수 없다
GENESIS_OP = "ifc.import"        # 가져온 시작 상태를 담는 로그 항목(seq 0). engine.GENESIS_OP 와 같아야 한다(시험이 대조한다)


def row_error(r) -> str | None:
    """요소 행이 커맨드가 만들 수 있는 모양인가(종류·IFC 클래스·출처·파라미터 이름과 값). 아니면 이유."""
    if not isinstance(r, dict) or set(r) != {"kind", "ifc_class", "origin", "family_id", "params"}:
        return "요소 행의 모양이 다르다"
    kind = r["kind"]
    if kind not in ELEMENT_KINDS:
        return f"알 수 없는 요소 종류·IFC 클래스: {str(kind)[:20]}"
    if (m := class_error(kind, r["ifc_class"])):
        return m
    if r["origin"] not in ("authored", "foreign", "detached") or (r["origin"] == "foreign") != (kind == "foreign"):
        return "origin 이 올바르지 않다"
    if not (r["family_id"] is None or _text_ok(r["family_id"], 0, 64)):
        return "family_id 가 올바르지 않다"
    return row_params_error(kind, r["params"])


def prop_error(pset, prop, value=None) -> str | None:
    """속성셋 이름·속성 이름·값(None = 없음)이 pset.set 이 받는 모양인가."""
    if not isinstance(pset, str) or not PROP_RE.match(pset):
        return "속성셋 이름이 올바르지 않다"
    if pset == RESERVED_PSET:
        return f"{RESERVED_PSET} 는 투영이 소유한 예약 속성셋이다"
    if not isinstance(prop, str) or not PROP_RE.match(prop) or prop == "id":
        return "속성 이름이 올바르지 않다"
    if value is not None and not (type(value) is bool or _num(value) or _text_ok(value)):
        return "속성 값이 올바르지 않다"
    return None


def effect_error(e) -> str | None:
    """로그 효과 하나의 모양과 값. 병합이 상대 파일을 믿지 않고 먼저 거르는 검사다(커맨드 검증을 우회한 효과를 막는다)."""
    if not isinstance(e, list) or not e:
        return "효과가 목록이 아니다"
    t = e[0]
    if t == "el":
        if len(e) != 4 or not _id_ok(e[1]) or (e[2] is None and e[3] is None):
            return "el 효과의 모양이 다르다"
        for r in (e[2], e[3]):
            if r is not None and (m := row_error(r)):
                return m
        return None
    if t == "rel":
        if len(e) != 6 or e[1] not in REL_KINDS_NAMES or not _id_ok(e[2]) or not _id_ok(e[3]) \
                or any(type(x) is not int or x not in (0, 1) for x in (e[4], e[5])):
            return "rel 효과의 모양이 다르다(관계 종류·id·0/1)"
        return None
    if t == "pset":
        if len(e) != 6 or not _id_ok(e[1]):
            return "pset 효과의 모양이 다르다"
        return prop_error(e[2], e[3], e[4]) or prop_error(e[2], e[3], e[5])
    if t == "mat":
        if len(e) != 4 or not _id_ok(e[1]) or any(x is not None and not _text_ok(x, 1, NAME_MAX) for x in (e[2], e[3])):
            return "mat 효과의 모양이 다르다"
        return None
    if t == "att":   # 외래 부속 스냅숏(S2): IFC 쪽 부속일 뿐 병합 단위가 아니다. 모양만 본다
        if len(e) != 4 or not _id_ok(e[1]) or any(x is not None and not isinstance(x, dict) for x in (e[2], e[3])):
            return "att 효과의 모양이 다르다"
        return None
    return f"알 수 없는 효과: {str(t)[:20]}"


def rows_error(rows: list[tuple]) -> str | None:
    """로그 전체가 엔진이 쓰는 모양인가: seq 는 1부터 이어지고, 인자는 JSON 객체, 역효과는 올바른 효과 목록."""
    for i, r in enumerate(rows):
        if len(r) != 7 or type(r[0]) is not int or r[0] != i + 1 or type(r[3]) is not int \
                or not all(isinstance(x, str) for x in (r[1], r[2], r[4], r[5], r[6])):
            return f"로그 항목 {i + 1} 의 열이 올바르지 않다"
        try:
            args, inv = json.loads(r[5]), json.loads(r[6])
        except ValueError:
            return f"로그 항목 {i + 1} 의 JSON 이 깨졌다"
        if not isinstance(args, dict) or not isinstance(inv, list):
            return f"로그 항목 {i + 1} 의 인자·역효과 모양이 다르다"
        for e in inv:
            if m := effect_error(e):
                return f"로그 항목 {i + 1}: {m}"
    return None


# ---------------------------------------------------------------- 로그 읽기
LOG_SQL = "SELECT seq, txn_id, actor, base_rev, op, args, inverse FROM op_log WHERE seq > 0 ORDER BY seq"
GENESIS_SQL = "SELECT seq, txn_id, actor, base_rev, op, args, inverse FROM op_log WHERE seq = 0"


class LogRows(list):
    """로그 항목(seq 1부터)의 목록. 가져온 프로젝트의 시작 상태 기록(seq 0, GENESIS_OP)은 목록에 섞지 않고 .genesis 에 따로 둔다:
    리비전·공통 접두 길이·되돌리기 계산이 모두 seq 1 부터 세기 때문이다. 접기(fold)만 이 기록부터 시작한다. 슬라이스는 일반 목록이 된다."""
    genesis: tuple | None = None


def read_rows(conn: sqlite3.Connection) -> LogRows:
    rows = LogRows(conn.execute(LOG_SQL).fetchall())
    g = conn.execute(GENESIS_SQL).fetchall()
    rows.genesis = g[0] if g else None
    return rows


def genesis_of(rows) -> tuple | None:
    return getattr(rows, "genesis", None)


def genesis_error(g) -> str | None:
    """시작 상태 기록: 요소 생성과 관계 맺기만 담는다(가져오기가 만든 상태)."""
    if len(g) != 7 or g[0] != 0 or g[4] != GENESIS_OP or not all(isinstance(x, str) for x in (g[1], g[2], g[4], g[5], g[6])):
        return "시작 상태 기록의 열이 올바르지 않다"
    try:
        inv = json.loads(g[6])
    except ValueError:
        return "시작 상태 기록의 JSON 이 깨졌다"
    if not isinstance(inv, list):
        return "시작 상태 기록의 모양이 다르다"
    for e in inv:   # 저장된 것은 역효과다: 뒤집으면 생성(el)·맺기(rel)여야 한다
        if (m := effect_error(e)):
            return f"시작 상태 기록: {m}"
        if e[0] == "el" and not (e[2] is not None and e[3] is None):
            return "시작 상태 기록에 요소 생성이 아닌 효과가 있다"
        if e[0] == "rel" and not (e[4] == 1 and e[5] == 0):
            return "시작 상태 기록에 관계 맺기가 아닌 효과가 있다"
        if e[0] not in ("el", "rel"):
            return "시작 상태 기록에 요소·관계 외의 효과가 있다"
    return None


def read_log(conn: sqlite3.Connection) -> tuple[str, LogRows]:
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    if meta.get("schema_version") != "1":
        raise DobimError("USAGE", "지원하지 않는 프로젝트 형식이다")
    if not isinstance(meta.get("project_uuid"), str):
        raise DobimError("IO_ERROR", "상대 프로젝트에 project_uuid 가 없다", path="/other")
    rows = read_rows(conn)
    has_base = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='foreign_base'").fetchone() is not None         and conn.execute("SELECT 1 FROM foreign_base").fetchone() is not None
    rows.has_base = has_base
    return meta["project_uuid"], rows


def open_other(path: str | Path) -> sqlite3.Connection:
    """상대 프로젝트를 읽기 전용으로 연다. 다른 프로세스가 쓰는 중이어도 WAL 이라 읽을 수 있다.
    WAL 사이드카(-wal, -shm)가 하나도 없으면 아무도 열고 있지 않다는 뜻이므로 immutable=1 로 연다: 읽기 전용 연결도 -shm 을 만들려
    해서 상대 폴더에 사이드카가 생기고 남는 일을 막는다. 사이드카가 있으면(쓰는 중이거나 비정상 종료) WAL 내용을 읽어야 하므로 mode=ro 로 열고,
    우리가 새로 만든 사이드카만 닫은 뒤 지운다(close_other)."""
    p = Path(path).resolve()
    sidecars = [str(p) + "-wal", str(p) + "-shm"]
    existed = [os.path.exists(x) for x in sidecars]
    uri = p.as_uri() + ("?mode=ro" if any(existed) else "?mode=ro&immutable=1")
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as ex:
        raise DobimError("IO_ERROR", f"상대 프로젝트를 열 수 없다: {type(ex).__name__}", path="/other") from ex
    _CREATED[id(conn)] = [x for x, was in zip(sidecars, existed) if not was]
    return conn


_CREATED: dict[int, list[str]] = {}   # 연결 -> 이 연결을 열 때 없던 사이드카 경로


def close_other(conn: sqlite3.Connection) -> None:
    created = _CREATED.pop(id(conn), [])
    conn.close()
    for x in created:   # 우리가 만든 사이드카만. 그 사이 다른 프로세스가 열었으면 지워지지 않거나(Windows) 그쪽 몫이므로 실패해도 넘어간다
        try:
            os.remove(x)
        except OSError:
            pass


def load_other(path: str | Path) -> tuple[str, LogRows]:
    conn = open_other(path)
    try:
        conn.execute("BEGIN")
        try:
            uuid_, rows = read_log(conn)
        finally:
            conn.execute("COMMIT")
    except sqlite3.Error as ex:
        raise DobimError("IO_ERROR", f"상대 프로젝트의 로그를 읽을 수 없다: {type(ex).__name__}", path="/other",
                         hint="프로젝트 파일(.dobim)이 맞는지, 다른 프로그램이 잠그고 있지 않은지 확인하세요") from ex
    finally:
        close_other(conn)
    if (m := rows_error(rows)) is not None or (genesis_of(rows) is not None and (m := genesis_error(genesis_of(rows))) is not None):
        raise DobimError("IO_ERROR", f"상대 프로젝트의 로그가 올바르지 않다: {m}", path="/other",
                         hint="프로젝트 파일이 손상됐거나 손으로 고친 것일 수 있습니다. 병합하지 않았습니다")
    return uuid_, rows


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


def _apply(units: dict, e: list, touch: dict | None, who: tuple, seed: dict | None = None) -> None:
    t = e[0]
    if t == "att":
        return   # 외래 부속 스냅숏: IFC 쪽 부속이라 병합 단위가 아니다

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
        if seed is not None and _o is not None and u not in units:
            seed.setdefault(u, canon(_o))
        if nw is None:
            units.pop(u, None)
        else:
            units[u] = canon(nw)
        mark(u)
    elif t == "mat":
        _, eid, _o, nw = e
        u = ("mat", eid)
        if seed is not None and _o is not None and u not in units:
            seed.setdefault(u, canon(_o))
        if nw is None:
            units.pop(u, None)
        else:
            units[u] = canon(nw)
        mark(u)
    else:
        raise DobimError("INTERNAL", f"알 수 없는 효과: {t}")


def fold(rows: list[tuple], units: dict | None = None, touch: dict | None = None, seed: dict | None = None) -> dict:
    """로그 항목들의 효과를 차례로 접어 단위 사전을 만든다. touch 가 있으면 단위마다 마지막으로 건드린 (seq, 행위자, op) 를 적는다.
    seed 가 있으면, 접는 중 처음 만난 속성·재료 효과의 이전 값이 단위 사전에 없을 때 그 값을 seed 에 적는다: 가져온 IFC 가 원래 갖고 있던 값
    (로그에 없는 시작 상태)이다. 이 값이 조상에 없으면 한쪽의 삭제가 다른 쪽과 같아 보여 사라진다(S8 × S2)."""
    units = {} if units is None else units
    for seq, _txn, actor, _b, op, _a, inv in rows:
        who = (seq, actor, op)
        for e in invert(json.loads(inv)):
            _apply(units, e, touch, who, seed)
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
    # 1) 무결성: 매달린 관계, 주인 없는 단위. 관계 종류별 규칙 이름·문구는 kinds.REL_KINDS 가 정한다
    for kind, s, d in view.rels:
        rk = REL_KINDS.get(kind)
        if s in ex and d in ex:
            if rk is not None and not (view.kind(s) in rk.src and view.kind(d) in rk.dst):
                out.append(_v("REL_ENDS", (s, d), "관계 양 끝의 요소 종류가 맞지 않는다", {"rel": [kind, s, d]}, disc=kind))
            continue
        if rk is not None and rk.dangling_ok:
            continue   # 분할 이력(succ)은 끝 요소가 지워져도 남는다
        code, msg = (rk.violation, rk.message) if rk is not None else ("REL_DANGLING", "관계의 한쪽 끝이 없다")
        out.append(_v(code, (s, d), msg, {"rel": [kind, s, d], "missing": [x for x in (s, d) if x not in ex]}, disc=kind))
    for e in _order(view.orphans):
        out.append(_v("ORPHAN_UNIT", (e,), "요소가 없는데 그 요소의 속성·재료·파라미터가 남아 있다"))
    hosted = {d for kind, _s, d in view.rels if kind == "hosts"}
    leveled = {w for kind, w, _d in view.rels if kind == "on_level"}
    typed = {f for kind, f, _d in view.rels if kind == "of_type"}
    annotated = {a for kind, a, _d in view.rels if kind == "annot_of"}
    for d in view.ids("door"):
        if d not in hosted:
            out.append(_v("HOST_MISSING", (d,), "문에 호스트 벽이 없다"))
    for w in view.ids("wall"):
        if w not in leveled:
            out.append(_v("LEVEL_MISSING", (w,), "벽에 레벨이 없다"))
    for f in view.ids("finst"):
        if f not in typed:
            out.append(_v("TYPE_MISSING", (f,), "패밀리 인스턴스에 타입이 없다"))
        if f not in hosted and f not in leveled:
            out.append(_v("LEVEL_MISSING", (f,), "호스트 없는 패밀리 인스턴스에 레벨이 없다"))
    for k in ("dim", "tag"):
        for a in view.ids(k):
            if a not in annotated:
                out.append(_v("VIEW_MISSING", (a,), "주석에 뷰가 없다"))
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
        axis = [list(k[1][0]), list(k[1][1])]
        if len(ws) > DUP_PAIRS_MAX_GROUP:
            # 겹친 벽이 아주 많으면 쌍(n^2/2)이 메모리를 먹는다(1200개 -> 72만 쌍). 이 묶음은 벽마다 하나씩(n)으로 보고한다.
            # "병합이 새로 만든 위반" 판정도 같은 단위로 한다: 어느 쪽에도 이미 겹쳐 있던 벽이면 새 위반이 아니다.
            for w in ws:
                out.append(_v("DUPLICATE_WALL", (w,), "같은 레벨의 같은 자리에 벽이 겹쳐 있다(겹친 벽이 많아 벽별로 보고)",
                              {"axis": axis, "group_size": len(ws)}, disc="many"))
            continue
        for i, w1 in enumerate(ws):
            for w2 in ws[i + 1:]:
                out.append(_v("DUPLICATE_WALL", (w1, w2), "같은 레벨의 같은 자리에 벽이 겹쳐 있다", {"axis": axis}))
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
    # 6) 호스팅된 패밀리 인스턴스(S4): 합친 호스트 벽에서 다시 평가한 개구부가 벽 안에 들어가는가(wall.set·wall.split 이 지키는 규칙과 같다)
    for f in view.ids("finst"):
        hs = view.inn.get(("hosts", f), [])
        if len(hs) != 1 or hs[0] not in ex or view.kind(hs[0]) != "wall":
            continue
        w = hs[0]
        try:
            spec = g.q(Q.ifc_spec, f)
            ax = view.p(w)["axis"]
            length = sqrt((ax[2] - ax[0]) ** 2 + (ax[3] - ax[1]) ** 2)
        except (KeyError, IndexError, ZeroDivisionError, TypeError, ValueError):
            continue
        focus = tuple(_pu(w, k) for k in ("axis", "thickness", "height")) + tuple(_pu(f, k) for k in view.p(f))
        if spec.get("error"):
            out.append(_v("FINST_INVALID", (f, w), "호스트 벽에서 패밀리 인스턴스를 평가할 수 없다", {"error": spec["error"]["code"]}, focus=focus))
            continue
        cut = spec.get("cut")
        if cut and (spec["offset"] + cut["x"][0] < -EPS or spec["offset"] + cut["x"][1] > length + EPS
                    or spec["elevation"] + cut["z"][0] < -EPS or spec["elevation"] + cut["z"][1] > view.p(w)["height"] + EPS):
            out.append(_v("FINST_OUT_OF_HOST", (f, w), f"패밀리 인스턴스 개구부가 호스트 벽({length:.1f}mm) 밖으로 나간다",
                          {"wall_length": length}, focus=focus))
    # 7) 평면 뷰의 레벨, 뷰 이름 (view.create·view.set 의 규칙)
    names_v: dict = {}
    for v in view.ids("view"):
        p = view.p(v)
        if p.get("type") == "plan" and p.get("level") not in ex:
            out.append(_v("VIEW_LEVEL_DELETED", (v,), "평면 뷰가 가리키는 레벨이 지워졌다", {"level": p.get("level")},
                          focus=(_pu(v, "level"), ("el", p.get("level"), "@"))))
        names_v.setdefault(p.get("name"), []).append(v)
    for nm in _order(n for n in names_v if n is not None):
        for i, v1 in enumerate(names_v[nm]):
            for v2 in names_v[nm][i + 1:]:
                out.append(_v("VIEW_NAME_DUPLICATE", (v1, v2), f"같은 이름의 뷰가 둘이다: {nm}", {"name": nm},
                              focus=(_pu(v1, "name"), _pu(v2, "name"))))
    out.sort(key=lambda v: (v["rule"], v["elements"], v["disc"]))
    return out


# ---------------------------------------------------------------- 계획
NO_FORCE = object()


def _rounds(n_resolve: int) -> int:
    """의미 충돌 해결 반복 횟수. 되돌리는 바퀴는 해결 키 수(n_resolve)를 넘을 수 없고 그 뒤 한 바퀴는 반드시 되돌림 없이 끝나므로
    n_resolve + 1 이면 충분하다(+2 는 여유). 상한 때문에 최종 재검사가 빠지는 일이 없다. 변이 시험이 이 값을 8 로 낮춰 검사의 이빨을 확인한다."""
    return n_resolve + 2


def check_batch_position(ctx) -> None:
    """merge.apply 는 배치(script --atomic)의 첫 변경이어야 한다: 앞 커맨드가 이미 상태를 바꿨다면(seq 가 head+1 보다 크다)
    로그만 접어서는 지금 상태를 알 수 없다. 뒤에 다른 커맨드가 오는 것은 괜찮다. 병합 둘을 한 배치에 넣는 것도 같은 이유로 거절한다."""
    if ctx.seq != ctx.eng.head + 1:
        raise DobimError("USAGE", "merge.apply 는 배치에서 다른 변경 뒤에 올 수 없다(앞 커맨드가 바꾼 상태를 로그로 접을 수 없다)",
                         hint="merge.apply 를 먼저 단독으로 실행하거나 배치의 맨 앞에 두세요(배치에 병합은 한 번만)")


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
        self.base_kind = "fork"             # 3-way 의 기준: "fork"(공통 접두) | "prior_merge"(앞선 병합이 들여온 상대/우리 로그 접두)
        self.base_rev = 0                   # prior_merge 일 때 기준이 된 접두 길이
        self.new_ours = 0                   # 기준 이후 우리 쪽 새 항목 수(관계 판정용)
        self.new_theirs = 0
        self.other_fp = ""                  # 상대 로그 전체의 지문(merge.apply 가 로그에 기록)
        self.renamed: dict = {}             # take=both 로 새 id 를 받은 상대 요소 {원래 id: 새 id}

    @property
    def clean(self) -> bool:
        return not self.conflicts

    def digest(self) -> str | None:
        return digest(sorted(self.merged.items())) if self.clean else None


def live_txns(rows: list[tuple]) -> set:
    """로그를 끝까지 읽었을 때 취소되지 않고 살아 있는 트랜잭션들(Engine._note_entry 와 같은 규칙)."""
    undo: list = []
    redo: list = []
    for r in rows:
        txn, op = r[1], r[4]
        if op == "history.undo":
            if undo:
                redo.append(undo.pop())
        elif op == "history.redo":
            if redo:
                undo.append(redo.pop())
        else:
            if not undo or undo[-1] != txn:
                undo.append(txn)
            redo.clear()
    return set(undo)


def _merge_records(rows: list[tuple], start: int):
    """rows[start:] 의 살아 있는 merge.apply 항목: (위치, 합친 상대 로그 길이 k, 그 접두의 지문). 지문은 merge.apply 가 기록한다."""
    live = live_txns(rows)
    out = []
    for i in range(start, len(rows)):
        r = rows[i]
        if r[4] != "merge.apply" or r[1] not in live:
            continue
        try:
            a = json.loads(r[5])
        except ValueError:
            continue
        k, fp = a.get("other_rev"), a.get("other_fp")
        if type(k) is int and isinstance(fp, str):
            out.append((i, k, fp))
    return sorted(out, key=lambda x: -x[1])


def prior_base(ours_rows: list[tuple], theirs_rows: list[tuple], n: int):
    """앞선 병합이 있었으면 공통 조상을 그 병합이 들여온 접두까지 끌어올린다(같은 상대를 다시 병합할 때 거짓 충돌을 막는다).
    - 우리가 상대(지금과 같은 로그)를 합친 기록이면 기준 = 상대 로그의 앞 k 항목: 우리는 그 시점까지의 상대 변경을 이미 갖고 있다.
    - 상대가 우리 로그를 합친 기록이면 기준 = 우리 로그의 앞 k 항목.
    기록의 지문(합친 접두의 해시)이 지금 로그의 접두와 같아야 같은 상대로 본다. 취소된 병합은 쓰지 않는다(다시 들여와야 하므로).
    양쪽이 서로를 합친 뒤 갈라진 경우(교차 병합): 한쪽이 다른 쪽의 후손이면 후손을 쓰고, 아니면 보수적으로 공통 접두(n)를 쓴다.
    반환: None | {rows, kind, rev, new_ours, new_theirs}."""
    a = b = None
    for i, k, fp in _merge_records(ours_rows, n):
        if n < k <= len(theirs_rows) and fingerprint(theirs_rows[:k]) == fp:
            a = (i, k)
            break
    for j, k, fp in _merge_records(theirs_rows, n):
        if n < k <= len(ours_rows) and fingerprint(ours_rows[:k]) == fp:
            b = (j, k)
            break
    pick = None
    if a and b:
        if b[0] < a[1]:       # 상대의 병합 항목이 우리가 합친 상대 접두 안에 있다: a 가 b 의 후손
            pick = "a"
        elif a[0] < b[1]:     # 우리의 병합 항목이 상대가 합친 우리 접두 안에 있다: b 가 a 의 후손
            pick = "b"
    elif a:
        pick = "a"
    elif b:
        pick = "b"
    if pick == "a":
        return {"rows": theirs_rows[:a[1]], "kind": "prior_merge", "rev": a[1],
                "new_ours": len(ours_rows) - (a[0] + 1), "new_theirs": len(theirs_rows) - a[1]}
    if pick == "b":
        return {"rows": ours_rows[:b[1]], "kind": "prior_merge", "rev": b[1],
                "new_ours": len(ours_rows) - b[1], "new_theirs": len(theirs_rows) - (b[0] + 1)}
    return None


# 병합 결과·상대 쪽에 있을 수 없는 구조 위반(커맨드는 항상 지킨다). 이게 보이면 파일이 손으로 고쳐졌거나 손상된 것이다
INTEGRITY_RULES = ("HOST_DELETED", "JOIN_TARGET_DELETED", "LEVEL_DELETED", "REL_DANGLING", "ORPHAN_UNIT", "HOST_MISSING", "LEVEL_MISSING",
                   "VIEW_DELETED", "TYPE_DELETED", "REL_ENDS", "TYPE_MISSING", "VIEW_MISSING")


def units_error(units: dict) -> str | None:
    """단위 사전이 커맨드가 만들 수 있는 모양인가: 요소 행(파라미터 완전성 포함), 속성(예약 속성셋 금지), 재료, 관계 종류."""
    rows_all = rows_of(units)
    for e, r in rows_all.items():
        if m := row_error(r):
            return f"{e}: {m}"
    for u, v in units.items():
        if u[0] in ("pset", "mat") and u[1] in rows_all and rows_all[u[1]]["kind"] in NON_IFC_KINDS:
            return f"{u[1]}: 뷰·주석에는 속성셋·재료가 없다"
        if u[0] == "pset" and (m := prop_error(u[2], u[3], json.loads(v))):
            return f"{u[1]} {u[2]}.{u[3]}: {m}"
        if u[0] == "mat" and not _text_ok(json.loads(v), 1, NAME_MAX):
            return f"{u[1]} 재료 이름이 올바르지 않다"
        if u[0] == "rel" and (u[1] not in REL_KINDS or not _id_ok(u[2]) or not _id_ok(u[3])):
            return f"관계 {str(u[1])[:20]} 이 올바르지 않다"
    return None


B64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


def renamed_id(project_uuid: str, eid: str, created_seq: int) -> str:
    """ID_COLLISION 에서 take=both 로 상대 요소를 살릴 때 쓰는 새 id: (프로젝트, 원래 id, 상대가 그 요소를 만든 seq) 의 결정적 함수.
    ifcopenshell 없이 IFC GlobalId(22자, 128비트 압축)와 같은 모양으로 만든다. 같은 입력이면 항상 같아서 병합을 다시 해도 같은 id 다."""
    n = int.from_bytes(hashlib.sha256(f"merge-rename:{project_uuid}:{eid}:{created_seq}".encode("utf-8")).digest()[:16], "big")
    out = []
    for _ in range(22):
        out.append(B64[n % 64])
        n //= 64
    return "".join(reversed(out))


def _rename_unit(u: tuple, m: dict) -> tuple:
    if u[0] == "rel":
        return (u[0], u[1], m.get(u[2], u[2]), m.get(u[3], u[3]))
    return (u[0], m.get(u[1], u[1])) + tuple(u[2:])


def _created_seq(rows: list[tuple], eid: str) -> int:
    """rows 중 요소 eid 를 처음 만든(없던 것이 생긴) 로그 항목의 seq."""
    for r in rows:
        for e in invert(json.loads(r[6])):
            if e[0] == "el" and e[1] == eid and e[2] is None:
                return r[0]
    return 0


def id_collisions(base: dict, ours: dict, theirs: dict) -> list[str]:
    """양쪽이 조상에 없던 같은 id 의 요소를 만들었고 내용이 다른 id 들(정렬)."""
    cand = {u[1] for u in ours if u[0] == "el" and u[2] == "@" and u not in base} & \
           {u[1] for u in theirs if u[0] == "el" and u[2] == "@" and u not in base}
    diff = set()
    for u in set(ours) | set(theirs):
        if ours.get(u) != theirs.get(u):
            diff.update(e for e in elems_of(u) if e in cand)
    return _order(diff)


def build_plan(ours_rows: list[tuple], theirs_rows: list[tuple], resolve: list[dict] | None = None, project_uuid: str = "") -> Plan:
    """두 로그에서 병합 계획을 만든다. 순수 함수: 파일·시각·난수를 보지 않는다."""
    res = {}
    for r in resolve or []:
        res[r["key"]] = r["take"]
    go, gt = genesis_of(ours_rows), genesis_of(theirs_rows)
    if go != gt:   # 가져온 프로젝트는 같은 가져오기에서 갈라진 것끼리만 병합한다(시작 상태가 다르면 조상이 없다)
        raise DobimError("CONSTRAINT", "같은 가져오기(시작 상태)에서 갈라진 파일이 아니다", path="/other",
                         hint="가져온 IFC 가 다르거나 한쪽이 가져오지 않은 프로젝트입니다")
    gen = [go] if go is not None else []   # 접기는 시작 상태 기록(seq 0)부터 한다
    n = fork_count(ours_rows, theirs_rows)
    fork_units = fold(gen + list(ours_rows[:n]))
    pb = prior_base(ours_rows, theirs_rows, n)
    base = fork_units if pb is None else fold(gen + list(pb["rows"]))
    # 가져온 IFC 가 원래 갖고 있던 속성·재료(로그에 없는 시작 상태)를 한쪽이 바꾸거나 지웠다면, 그 이전 값을 조상에 넣는다.
    # 안 그러면 한쪽의 삭제가 "조상에도 없고 상대에도 없는 값"으로 보여 병합에서 조용히 사라진다.
    seed: dict = {}
    fold(list(ours_rows[n:]), dict(fork_units), None, seed)
    fold(list(theirs_rows[n:]), dict(fork_units), None, seed)
    if seed:
        fork_units = {**seed, **fork_units}
        base = {**seed, **base}
    to, tt = {}, {}
    ours = fold(ours_rows[n:], dict(fork_units), to)
    theirs = fold(theirs_rows[n:], dict(fork_units), tt)
    # take=both: 같은 id 를 서로 다르게 만든 충돌에서 상대 요소를 새 id 로 옮겨 둘 다 살린다(우리 쪽 요소는 그대로).
    # 상대 쪽 단위(요소·관계·속성·재료)의 id 를 모두 새 id 로 바꾼 뒤 보통의 3-way 를 하므로 충돌이 사라진다.
    renamed: dict[str, str] = {}
    puid = project_uuid
    for e in id_collisions(base, ours, theirs):
        if res.get(f"ID_COLLISION:{e}") == "both":
            renamed[e] = renamed_id(puid, e, _created_seq(theirs_rows[n:], e))
    if renamed:
        theirs = {_rename_unit(u, renamed): v for u, v in theirs.items()}
        tt = {_rename_unit(u, renamed): w for u, w in tt.items()}
    plan = Plan()
    plan.renamed = renamed
    plan.fork, plan.ours_rev, plan.other_rev = n, len(ours_rows), len(theirs_rows)
    plan.base, plan.ours, plan.theirs = base, ours, theirs
    plan.new_ours, plan.new_theirs = len(ours_rows) - n, len(theirs_rows) - n
    plan.other_fp = fingerprint(theirs_rows)
    if pb is not None:
        plan.base_kind, plan.base_rev, plan.new_ours, plan.new_theirs = pb["kind"], pb["rev"], pb["new_ours"], pb["new_theirs"]

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
            # 지우지 않은 쪽이 속성·재료·관계를 "지운" 변경은 요소 삭제와 같은 방향이라 수정으로 세지 않는다(값이 남아 있는 변경만 수정)
            mods = [u for u in changed(other, e) if u != ("el", e, "@") and other.get(u) is not None]
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
    for e in _order(renamed):
        used.add(f"ID_COLLISION:{e}")
        plan.resolved.append(f"ID_COLLISION:{e}")
    pending_unit = []
    for g in groups:
        t = res.get(g["key"])
        if t is None:
            pending_unit.append(g)
            continue
        if t == "both":
            raise DobimError("USAGE", f'take="both" 는 ID_COLLISION 충돌에만 쓸 수 있다: {g["key"]}', path="/resolve",
                             hint="ID_COLLISION 이 아닌 충돌은 ours 또는 theirs 를 고르세요")
        take(g["units"], ours if t == "ours" else theirs)
        used.add(g["key"])
        plan.resolved.append(g["key"])

    # ---- 2) 의미 검사 (재생성). 병합이 새로 만든 위반만 충돌이다
    v_theirs = violations(View(theirs))
    broken = [v for v in v_theirs if v["rule"] in INTEGRITY_RULES]
    if broken:
        raise DobimError("IO_ERROR", f"상대 프로젝트의 상태가 일관되지 않다({broken[0]['rule']}: {broken[0]['reason']})", path="/other",
                         hint="커맨드로는 만들 수 없는 상태입니다. 프로젝트 파일이 손상됐거나 손으로 고친 것일 수 있습니다. 병합하지 않았습니다")
    vo_keys = {vkey(v) for v in violations(View(ours))}
    vt_keys = {vkey(v) for v in v_theirs}

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
    # 반복 상한은 두지 않는다. 수렴 논증: 한 바퀴가 값을 되돌리면 그 바퀴에서 쓴 해결 키 하나가 applied_sem 에 들어가고
    # (k not in applied_sem 조건), 한 키는 한 번만 쓰이므로 되돌리는 바퀴는 len(res) 번을 넘을 수 없다.
    # 그 다음 바퀴는 반드시 되돌림 없이 끝나므로 마지막 바퀴가 항상 "최종 재검사"가 된다(상한 소진으로 검사가 빠지는 일이 없다).
    # 반복 횟수 = 되돌린 횟수 + 1 이라 동시에 선언 한도(resolve ≤ 200건) 안에서 유한하다.
    for _round in range(_rounds(len(res)) if plan.semantic_checked else 0):
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
    else:   # 도달할 수 없다(위 수렴 논증). 도달하면 마지막 재검사 없이 깨끗하다고 판정하게 되므로 조용히 넘기지 않는다
        if plan.semantic_checked:
            raise DobimError("INTERNAL", "의미 충돌 해결이 수렴하지 않았다")
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
                    "take": ["ours", "theirs"] + (["both"] if g["type"] == "ID_COLLISION" else [])}
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
    if not conflicts and (m := units_error(merged)) is not None:   # 커맨드와 같은 불변식: 결과가 규칙을 어기면 합치지 않는다
        raise DobimError("CONSTRAINT", f"병합 결과가 커맨드 불변식을 어긴다: {m}", path="/other",
                         hint="상대 프로젝트의 로그가 손으로 고쳐졌을 수 있습니다. 병합하지 않았습니다")
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
    # 호스트·타입·뷰가 먼저 생기고, 지울 때는 의존하는 것이 먼저 사라진다(element.delete 와 같은 순서). 순서는 kinds.ELEMENT_KINDS 가 정한다
    key = lambda e, rows: (ELEMENT_KINDS[rows[e]["kind"]].order, e)  # noqa: E731
    for e in _order((e for e in orows if e not in mrows), key=lambda e: (-key(e, orows)[0], e)):
        ctx.attach(e)   # 가져온 요소를 지울 때 외래 부속(타입·수량 세트·스타일 등)을 효과로 남긴다: 실행 취소가 되살린다(element.delete 와 같이 맨 먼저)
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
