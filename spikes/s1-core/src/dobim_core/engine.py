# SPDX-License-Identifier: MPL-2.0
"""프로젝트 엔진: 의도 저장소(SQLite WAL) + IFC 쪽 상태 + 증분 그래프 + 커맨드 로그.

상태 변경의 관문은 `_apply_effect` 하나다. 커맨드는 효과(effect)를 내보내기만 하고,
효과는 [종류, 대상, 이전 값, 새 값] 이므로 실행 취소는 로그에 적힌 효과를 뒤집어 적용하는 것이다.

효과 형식 (JSON):
    ["el",   id, old_row|null, new_row|null]
    ["rel",  kind, src, dst, old(0|1), new(0|1)]
    ["pset", id, pset, prop, old|null, new|null]
    ["mat",  id, old_name|null, new_name|null]
    ["att",  id, old_blob|null, new_blob|null]    외래 부속 스냅숏(S2): 지우기 직전에 old 를 적고, 실행 취소가 new 로 되살린다
"""
from __future__ import annotations

import bisect
import hashlib
import json
import secrets
import sqlite3
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

import ifcopenshell.guid

from dobim_core import queries as Q
from dobim_core.errors import DobimError, internal_error
from dobim_core.graph import Graph, RefGraph
from dobim_core.ifc_model import IfcModel
from dobim_core.registry import Command

SCHEMA_VERSION = "1"
DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE element (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, ifc_class TEXT NOT NULL, origin TEXT NOT NULL,
  family_id TEXT, params TEXT NOT NULL, projection_hash TEXT,
  workset_id TEXT, phase_created TEXT, phase_demolished TEXT, design_option_id TEXT);
CREATE TABLE relation (kind TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL, data TEXT,
  PRIMARY KEY (kind, src, dst));
CREATE TABLE op_log (
  seq INTEGER PRIMARY KEY, txn_id TEXT NOT NULL, actor TEXT NOT NULL, base_rev INTEGER NOT NULL,
  ts TEXT NOT NULL, op TEXT NOT NULL, args TEXT NOT NULL, inverse TEXT NOT NULL);
CREATE INDEX op_log_txn ON op_log (txn_id);
CREATE TABLE foreign_base (id INTEGER PRIMARY KEY CHECK (id = 1), schema TEXT NOT NULL, size INTEGER NOT NULL,
  sha256 TEXT NOT NULL, data BLOB NOT NULL);
CREATE TABLE import_spec (id TEXT PRIMARY KEY, spec_hash TEXT NOT NULL);
"""


# ---------------------------------------------------------------- 효과
def invert_effect(e: list) -> list:
    t = e[0]
    if t == "el" or t == "mat" or t == "att":
        return [t, e[1], e[3], e[2]]
    return [t, e[1], e[2], e[3], e[5], e[4]]


def invert(effects: list) -> list:
    return [invert_effect(e) for e in reversed(effects)]


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def log_args(model) -> str:
    """로그에 남길 인자(JSON). 생략된 선택 필드(기본값 None 이고 값도 None)는 null 로 적지 않고 뺀다: 명시적 null 은
    입력에서 거부되므로, 로그의 null 을 그대로 다시 실행하면 재생이 스키마 검사에 걸린다."""
    d = model.model_dump(mode="json")
    for name, f in type(model).model_fields.items():
        if d.get(name) is None and not f.is_required() and f.default is None:
            d.pop(name, None)
    return canon(d)


def digest(obj: Any) -> str:
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 메모리 상태
class State:
    """의도 저장소의 메모리 사본. 행은 불변으로 취급한다(바꿀 때는 새 dict 로 교체)."""

    def __init__(self):
        self.el: dict[str, dict] = {}
        self.out: dict[tuple, list] = {}
        self.inn: dict[tuple, list] = {}
        self.bucket: dict[tuple, list] = {}
        self.count: dict[str, int] = {}
        self.by_kind: dict[str, set] = {}
        self.origins: dict[str, int] = {}   # authored / foreign / detached 별 요소 수

    def apply_el(self, eid, old, new):
        if old is not None:
            self.origins[old["origin"]] -= 1
        if new is not None:
            self.origins[new["origin"]] = self.origins.get(new["origin"], 0) + 1
        if old is None:
            kind = new["kind"]
            bisect.insort(self.bucket.setdefault((kind, Q.bucket_of(eid)), []), eid)
            self.count[kind] = self.count.get(kind, 0) + 1
            self.by_kind.setdefault(kind, set()).add(eid)
            self.el[eid] = new
        elif new is None:
            kind = old["kind"]
            lst = self.bucket[(kind, Q.bucket_of(eid))]
            del lst[bisect.bisect_left(lst, eid)]
            self.count[kind] -= 1
            self.by_kind[kind].discard(eid)
            del self.el[eid]
        else:
            self.el[eid] = new

    def apply_rel(self, kind, src, dst, present):
        for table, key, other in ((self.out, (kind, src), dst), (self.inn, (kind, dst), src)):
            lst = table.setdefault(key, [])
            i = bisect.bisect_left(lst, other)
            if present:
                if i == len(lst) or lst[i] != other:
                    lst.insert(i, other)
            elif i < len(lst) and lst[i] == other:
                del lst[i]

    def relations(self):
        return sorted((k, s, d) for (k, s), dsts in self.out.items() for d in dsts)


class _Inputs:
    """그래프 입력의 로더. 키별로 불변 값(튜플·행)을 돌려준다."""

    def __init__(self, eng: "Engine"):
        self.eng = eng

    def load(self, key):
        st = self.eng.st
        t = key[0]
        if t == "el":
            return st.el.get(key[1])
        if t == "bucket":
            return tuple(st.bucket.get((key[1], key[2]), ()))
        if t == "count":
            return st.count.get(key[1], 0)
        if t == "rel_out":
            return tuple(st.out.get((key[1], key[2]), ()))
        if t == "rel_in":
            return tuple(st.inn.get((key[1], key[2]), ()))
        ifc = self.eng.ifc
        if t == "pset":
            return ifc.get_psets(key[1]) if key[1] in ifc.ents else {}
        if t == "mat":
            return ifc.get_material(key[1]) if key[1] in ifc.ents else None
        raise KeyError(key)


class Ctx:
    """커맨드가 보는 실행 문맥. 읽기는 메모리 상태, 쓰기는 효과 내보내기뿐이다."""

    def __init__(self, eng: "Engine", seq: int, actor: str, dry_run: bool):
        self.eng = eng
        self.seq = seq
        self.actor = actor
        self.dry_run = dry_run
        self.effects: list = []
        self.warnings: list[str] = []
        self._n = 0

    # ---- 읽기
    @property
    def st(self) -> State:
        return self.eng.st

    @property
    def g(self):
        return self.eng.g

    def row(self, eid: str, kind: str | None = None) -> dict:
        r = self.eng.st.el.get(eid)
        if r is None or (kind is not None and r["kind"] != kind):
            raise DobimError("NOT_FOUND", f"{kind or '요소'} 를 찾을 수 없다: {eid}", path="/id")
        return r

    def authored(self, eid: str, kind: str | None = None, *, what: str = "형상") -> dict:
        """저작 요소만 돌려준다. 외래(foreign)·분리됨(detached) 요소의 형상·배치·재료·삭제는 거절한다(ADR-0005): IFC 가 형상의 정본이고
        코어는 그 형상을 다시 쓰지 않는다. 속성(pset.set/delete)만 편집할 수 있다."""
        r = self.eng.st.el.get(eid)
        if r is not None and r["origin"] != "authored":
            why = "분리됨(외부 도구가 형상을 바꿔 동결됐다)" if r["origin"] == "detached" else "외래 요소(IFC 가 정본이다)"
            raise DobimError("ELEMENT_FROZEN", f"{why}라 {what}을 바꿀 수 없다: {eid}", path="/id",
                             hint="속성은 pset.set / pset.delete 로 편집할 수 있다. 형상은 원본 도구에서 고쳐 다시 가져오세요")
        return self.row(eid, kind)

    def rels_out(self, kind, src) -> list:
        return list(self.eng.st.out.get((kind, src), ()))

    def rels_in(self, kind, dst) -> list:
        return list(self.eng.st.inn.get((kind, dst), ()))

    def psets(self, eid) -> dict:
        return self.eng.ifc.get_psets(eid)

    def material(self, eid):
        return self.eng.ifc.get_material(eid)

    def complex_material(self, eid) -> bool:
        return self.eng.has_base and self.eng.ifc.has_complex_material(eid)

    def foreign_voids(self, eid) -> list:
        return self.eng.ifc.foreign_voids(eid) if self.eng.has_base else []

    def new_id(self, kind: str) -> str:
        """결정적 GlobalId. 같은 프로젝트에서 같은 순번이면 같은 값이 나온다."""
        self._n += 1
        u = uuid.uuid5(uuid.UUID(self.eng.project_guid_uuid), f"{kind}:{self.seq}:{self._n}")
        return ifcopenshell.guid.compress(u.hex)

    # ---- 쓰기 (효과)
    def emit(self, eff: list) -> None:
        self.eng._apply_effect(eff, persist=not self.dry_run)
        self.effects.append(eff)

    def put_el(self, eid: str, new: dict | None) -> None:
        old = self.eng.st.el.get(eid)
        if old == new:
            return
        self.emit(["el", eid, old, new])

    def rel(self, kind: str, src: str, dst: str, present: bool) -> bool:
        cur = dst in self.eng.st.out.get((kind, src), ())
        if cur == present:
            return False
        self.emit(["rel", kind, src, dst, int(cur), int(present)])
        return True

    def set_prop(self, eid: str, pset: str, prop: str, value) -> bool:
        old = self.eng.ifc.get_psets(eid).get(pset, {}).get(prop)
        if old == value and type(old) is type(value):
            return False
        self.emit(["pset", eid, pset, prop, old, value])
        return True

    def set_material(self, eid: str, name) -> bool:
        old = self.eng.ifc.get_material(eid)
        if old == name:
            return False
        self.emit(["mat", eid, old, name])
        return True

    def attach(self, eid: str) -> None:
        """지울 요소의 외래 부속을 스냅숏 효과로 남긴다(외래 기반 프로젝트만). 없으면 아무것도 하지 않는다."""
        if not self.eng.has_base:
            return
        blob = self.eng.ifc.capture_attachments(eid)
        if blob:
            self.emit(["att", eid, blob, None])

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    # ---- 이력
    def peek_undo(self) -> str:
        if not self.eng.undo_stack:
            raise DobimError("NOTHING_TO_UNDO", "되돌릴 항목이 없다")
        return self.eng.undo_stack[-1]

    def peek_redo(self) -> str:
        if not self.eng.redo_stack:
            raise DobimError("NOTHING_TO_REDO", "다시 실행할 항목이 없다")
        return self.eng.redo_stack[-1]

    def txn_entries(self, txn: str) -> list[tuple]:
        return self.eng.txn_entries(txn)


def affected_of(effects: list) -> dict:
    created, deleted, modified = set(), set(), set()
    for e in effects:
        if e[0] == "el":
            if e[2] is None:
                created.add(e[1])
            elif e[3] is None:
                deleted.add(e[1])
            else:
                modified.add(e[1])
        elif e[0] == "rel":
            modified.update((e[2], e[3]))
        else:
            modified.add(e[1])
    both = created & deleted  # 같은 실행 안에서 만들고 지운 요소는 흔적을 남기지 않는다
    created -= both
    deleted -= both
    modified -= created | deleted | both
    return {"created": sorted(created), "modified": sorted(modified), "deleted": sorted(deleted)}


class Engine:
    def __init__(self, path: str | Path, actor: str = "py", *, create: bool = False, guid: str | None = None,
                 busy_ms: int = 5000, synchronous: str = "NORMAL", eager_t0: bool = True, seed=None):
        self.path = str(path)
        self.actor = actor
        self.eager_t0 = eager_t0
        self.workspace = Path.cwd()
        self.timing: dict[str, float] = {}
        self._tainted: str | None = None   # IFC 투영이 로그와 어긋났을 수 있는 이유(복구되면 None)
        self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=busy_ms / 1000.0)
        self.conn.execute(f"PRAGMA busy_timeout={int(busy_ms)}")
        self.conn.execute("PRAGMA foreign_keys=OFF")
        try:
            if create:
                self._create_schema(guid, seed)
            self.conn.execute(f"PRAGMA synchronous={synchronous}")
            self._load()
        except BaseException:
            self.conn.close()   # 만들다 실패한 연결을 쥐고 있으면 호출자가 파일을 치우지 못한다(윈도)
            raise

    # ------------------------------------------------------------ 생성·열기
    @classmethod
    def create(cls, path, actor="py", guid=None, **kw) -> "Engine":
        p = Path(path)
        if p.exists():
            raise DobimError("FILE_EXISTS", f"이미 있는 파일이다: {p.name}", path="/path")
        return cls(p, actor, create=True, guid=guid, **kw)

    @classmethod
    def create_imported(cls, path, data: bytes, plan, actor="py", guid=None, **kw) -> "Engine":
        """외래 IFC(data)를 기반으로 새 프로젝트를 만든다. plan 은 foreign.plan_import 의 분류 결과다."""
        p = Path(path)
        if p.exists():
            raise DobimError("FILE_EXISTS", f"이미 있는 파일이다: {p.name}", path="/path")
        try:
            return cls(p, actor, create=True, guid=guid, seed=(data, plan), **kw)
        except BaseException:
            for suffix in ("", "-wal", "-shm", "-journal"):   # 반쯤 만든 프로젝트 파일을 남기지 않는다
                try:
                    Path(str(p) + suffix).unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def _seed_import(self, data: bytes, plan) -> None:
        """생성 트랜잭션 안에서 외래 기반 원본과 분류 결과를 적는다. 가져오기는 로그 항목이 아니다: 프로젝트의 시작 상태(rev 0)다.
        element.projection_hash 는 가져오는 순간의 클로저 해시이고 이후 바뀌지 않는다(변조 판정은 가져오는 순간에만 한다)."""
        c = self.conn
        c.execute("INSERT INTO foreign_base (id, schema, size, sha256, data) VALUES (1,?,?,?,?)",
                  (plan.schema, len(data), hashlib.sha256(data).hexdigest(), zlib.compress(data, 6)))
        c.executemany("INSERT INTO element (id, kind, ifc_class, origin, family_id, params, projection_hash) VALUES (?,?,?,?,?,?,?)",
                      [(r["id"], r["kind"], r["ifc_class"], r["origin"], r["family_id"], canon(r["params"]), r.get("projection_hash"))
                       for r in plan.rows])
        c.executemany("INSERT INTO relation (kind, src, dst) VALUES (?,?,?)", plan.rels)
        # 저작 요소의 가져올 때 투영 명세 해시: 명세가 그대로면 첫 투영에서 들어온 엔티티를 그대로 둔다(스타일 등 외래 부속 보존)
        c.executemany("INSERT INTO import_spec (id, spec_hash) VALUES (?,?)",
                      [(r["id"], r["spec_hash"]) for r in plan.rows if r.get("spec_hash")])

    def _create_schema(self, guid, seed=None):
        c = self.conn
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("BEGIN IMMEDIATE")
        for stmt in DDL.split(";\n"):
            if stmt.strip():
                c.execute(stmt)
        u = uuid.UUID(guid) if guid else uuid.uuid4()
        c.executemany("INSERT INTO meta VALUES (?,?)", [
            ("schema_version", SCHEMA_VERSION), ("project_uuid", str(u)),
            ("confirm_secret", secrets.token_hex(16))])
        if seed is not None:
            try:
                self._seed_import(*seed)
            except BaseException:
                c.execute("ROLLBACK")
                raise
        c.execute("COMMIT")

    def _load(self):
        c = self.conn
        try:
            c.execute("BEGIN")
            meta = dict(c.execute("SELECT key, value FROM meta"))
            if meta.get("schema_version") != SCHEMA_VERSION:
                raise DobimError("USAGE", "지원하지 않는 프로젝트 형식이다")
            self.project_guid_uuid = meta["project_uuid"]
            self.confirm_secret = meta["confirm_secret"]
            self.st = State()
            for eid, kind, ifc_class, origin, fam, params in c.execute(
                    "SELECT id, kind, ifc_class, origin, family_id, params FROM element ORDER BY id"):
                self.st.apply_el(eid, None, {"kind": kind, "ifc_class": ifc_class, "origin": origin,
                                             "family_id": fam, "params": json.loads(params)})
            for kind, src, dst in c.execute("SELECT kind, src, dst FROM relation"):
                self.st.apply_rel(kind, src, dst, True)
            log = c.execute("SELECT seq, txn_id, op, inverse FROM op_log ORDER BY seq").fetchall()
            base = None
            self._import_spec: dict[str, str] = {}
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='foreign_base'").fetchone():
                row = c.execute("SELECT data FROM foreign_base WHERE id=1").fetchone()
                base = zlib.decompress(row[0]) if row else None
                if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_spec'").fetchone():
                    self._import_spec = dict(c.execute("SELECT id, spec_hash FROM import_spec"))
            c.execute("COMMIT")
        except Exception:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        self.head = log[-1][0] if log else 0
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        for seq, txn, op, _ in log:
            self._note_entry(op, txn)
        self.has_base = base is not None
        self.ifc = IfcModel(self.project_guid_uuid, base)
        self._rebuild_ifc(log)
        self._proj_stamp: dict[str, int] = {}
        self._pending: set = set()
        self.g = Graph(_Inputs(self))

    def _rebuild_ifc(self, log):
        """IFC 쪽 정본(속성셋·재료)은 로그의 효과를 순서대로 접어 최종값만 한 번에 만든다."""
        psets: dict[tuple, Any] = {}
        mats: dict[str, Any] = {}
        for _, _, _, inv in log:
            for e in invert(json.loads(inv)):
                if e[0] == "pset":
                    psets[(e[1], e[2], e[3])] = e[5]
                elif e[0] == "mat":
                    mats[e[1]] = e[3]
        base = self.ifc.base_mode
        for eid in sorted(self.st.el):
            self.ifc.create_identity(eid, self.st.el[eid]["kind"])
        if base:
            self.ifc.prune()
        for (eid, pset, prop), v in sorted(psets.items(), key=lambda kv: kv[0]):
            # 외래 기반에는 가져온 속성이 이미 있으므로 삭제(None)도 적용한다. 빈 파일에서 시작하는 일반 프로젝트는 건너뛴다
            if (v is not None or base) and eid in self.ifc.ents:
                self.ifc.set_prop(eid, pset, prop, v)
        for eid, name in sorted(mats.items()):
            if (name is not None or base) and eid in self.ifc.ents:
                self.ifc.set_material(eid, name)

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------ 이력 스택
    def _note_entry(self, op: str, txn: str) -> None:
        if op == "history.undo":
            self.redo_stack.append(self.undo_stack.pop())
        elif op == "history.redo":
            self.undo_stack.append(self.redo_stack.pop())
        else:
            if not self.undo_stack or self.undo_stack[-1] != txn:
                self.undo_stack.append(txn)
            self.redo_stack.clear()

    def txn_entries(self, txn: str) -> list[tuple]:
        return self.conn.execute(
            "SELECT seq, actor, op, args, inverse FROM op_log WHERE txn_id=? ORDER BY seq", (txn,)).fetchall()

    # ------------------------------------------------------------ 효과 적용 (유일한 상태 변경 관문)
    def _apply_effect(self, e: list, persist: bool) -> None:
        t = e[0]
        p = self._pending
        if t == "el":
            _, eid, old, new = e
            self.st.apply_el(eid, old, new)
            p.add(("el", eid))
            try:   # IFC 쪽이 실패하면 메모리 상태도 되돌리고, IFC 는 어떤 상태인지 모르니 오염으로 표시한다
                if old is None:
                    self.ifc.create_identity(eid, new["kind"])
                elif new is None:
                    self.ifc.remove_identity(eid)
                    self._proj_stamp.pop(eid, None)
            except BaseException as ex:
                self.st.apply_el(eid, new, old)
                self._taint(f"요소 {eid}", ex)
                raise
            if old is None or new is None:
                kind = (new or old)["kind"]
                p.add(("bucket", kind, Q.bucket_of(eid)))
                p.add(("count", kind))
            if persist:
                if new is None:
                    self._sql(f"요소 {eid}", "DELETE FROM element WHERE id=?", (eid,))
                else:
                    self._sql(f"요소 {eid}",
                              "INSERT INTO element (id, kind, ifc_class, origin, family_id, params) VALUES (?,?,?,?,?,?) "
                              "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, ifc_class=excluded.ifc_class, origin=excluded.origin, "
                              "family_id=excluded.family_id, params=excluded.params",   # projection_hash 는 건드리지 않는다
                              (eid, new["kind"], new["ifc_class"], new["origin"], new["family_id"], canon(new["params"])))
        elif t == "rel":
            _, kind, src, dst, _, new = e
            self.st.apply_rel(kind, src, dst, bool(new))
            p.add(("rel_out", kind, src))
            p.add(("rel_in", kind, dst))
            if persist:
                if new:
                    self._sql(f"관계 {kind}", "INSERT OR IGNORE INTO relation (kind, src, dst) VALUES (?,?,?)", (kind, src, dst))
                else:
                    self._sql(f"관계 {kind}", "DELETE FROM relation WHERE kind=? AND src=? AND dst=?", (kind, src, dst))
        elif t == "pset":
            try:   # 실패한 효과는 effects 에 들어가지 않아 롤백되지 않는다: 오염으로 표시해 로그에서 다시 만들게 한다
                self.ifc.set_prop(e[1], e[2], e[3], e[5])
            except BaseException as ex:
                self._taint(f"속성 {e[2]}/{e[3]}", ex)
                raise
            p.add(("pset", e[1]))
        elif t == "mat":
            try:
                self.ifc.set_material(e[1], e[3])
            except BaseException as ex:
                self._taint(f"재료 {e[1]}", ex)
                raise
            p.add(("mat", e[1]))
        elif t == "att":
            # new 가 있으면 되살리고(실행 취소), 없으면(지우기 쪽) 할 일이 없다: 부속은 요소 제거 때 함께 사라지고 old 가 로그에 남는다
            if e[3] is not None:
                try:
                    self.ifc.restore_attachments(e[1], e[3])
                except BaseException as ex:
                    self._taint(f"부속 {e[1]}", ex)
                    raise
        else:
            raise DobimError("INTERNAL", f"알 수 없는 효과: {t}")

    def _sql(self, what: str, sql: str, params: tuple) -> None:
        """효과의 SQL 쓰기. 메모리 상태와 IFC 는 이미 바뀐 뒤라, 쓰기가 실패하면(디스크 오류 등) 메모리가 디스크와 어긋난다.
        실패한 효과는 effects 에 들어가지 않아 롤백되지 않으므로 IFC 실패와 같게 오염으로 표시한다: 호출 쪽 실패 경로가
        트랜잭션을 롤백한 뒤 로그(디스크)에서 상태와 투영을 다시 만든다."""
        try:
            self.conn.execute(sql, params)
        except BaseException as ex:
            self._taint(f"SQL 쓰기 {what}", ex)
            raise

    def _taint(self, what: str, ex: BaseException) -> None:
        """IFC 호출이 예외를 던졌다: 호출이 어디까지 적용됐는지 알 수 없다(고아 재료·중복 재료·반쯤 지워진 요소).
        그래서 제자리 복원을 믿지 않고 오염으로 표시한다. 호출 쪽 실패 경로가 로그에서 상태와 투영을 다시 만든다.
        DobimError 는 IFC 를 건드리기 전의 검증 실패이므로 오염이 아니다."""
        if isinstance(ex, DobimError):
            return
        self._tainted = self._tainted or f"{what}: IFC 호출 실패({type(ex).__name__})"

    @property
    def tainted(self) -> str | None:
        """IFC 투영이 로그와 어긋났을 수 있는 이유. 정상이면 None. 다음 호출 때 자동으로 복구를 시도한다."""
        return self._tainted or getattr(getattr(self, "ifc", None), "tainted", None)

    def _heal(self) -> None:
        """오염된 상태를 디스크의 로그에서 다시 만든다(프로젝트를 다시 여는 것과 같다). 못 하면 ENGINE_TAINTED.
        트랜잭션 밖에서만 부른다."""
        reason = self.tainted
        if reason is None:
            return
        self._tainted = reason   # 다시 만드는 도중 실패해도 오염 표시가 남아야 한다
        try:
            self._load()
        except BaseException as ex:
            if isinstance(ex, KeyboardInterrupt):
                raise
            internal_error(ex)   # 트레이스백은 stderr 로만 남긴다
            raise DobimError("ENGINE_TAINTED", f"IFC 투영이 오염됐고 로그에서 다시 만들지 못했다: {reason} / 복구 실패: "
                             f"{type(ex).__name__}", hint="원인을 없앤 뒤 다시 시도하면 자동으로 복구를 시도한다. "
                             "프로젝트 파일(.dobim)의 로그는 온전하다: 다시 열면 복구된다") from ex
        self._tainted = None

    def _touch_pending(self) -> None:
        keys, self._pending = self._pending, set()
        for k in keys:
            self.g.touch(k)

    # ------------------------------------------------------------ 따라잡기 (다른 프로세스의 쓰기)
    def sync(self) -> int:
        """다른 프로세스가 로그에 추가한 항목을 메모리 상태에 반영한다 (15.6절 4항)."""
        if not self.conn.in_transaction:
            self._heal()   # 이전 실패로 오염됐으면 먼저 로그에서 다시 만든다(못 하면 ENGINE_TAINTED)
        rows = self.conn.execute(
            "SELECT seq, txn_id, op, inverse FROM op_log WHERE seq > ? ORDER BY seq", (self.head,)).fetchall()
        try:
            for seq, txn, op, inv in rows:
                for e in invert(json.loads(inv)):
                    self._apply_effect(e, persist=False)
                self._note_entry(op, txn)
                self.head = seq
        except BaseException:
            if self.tainted and not self.conn.in_transaction:
                self._heal()   # 다시 만들었으면 디스크 기준으로 최신이다
                return len(rows)
            raise   # 트랜잭션 안이면 mutate 의 실패 경로가 롤백한 뒤 복구한다
        if rows:
            self._touch_pending()
        return len(rows)

    @property
    def rev(self) -> int:
        return self.head

    # ------------------------------------------------------------ 실행
    def _t0(self) -> None:
        self._touch_pending()
        if self.eager_t0:
            self.g.q(Q.t0_state)

    def read(self, cmd: Command, model) -> tuple[Any, list[str]]:
        """조회 커맨드. 다른 프로세스의 쓰기를 먼저 따라잡고 스냅숏 위에서 계산한다."""
        self.sync()
        self._touch_pending()
        ctx = Ctx(self, self.head + 1, self.actor, True)
        result = cmd.func(ctx, model)
        return result, ctx.warnings

    def mutate(self, cmds: list[tuple[Command, Any]], *, expected_rev: int | None = None,
               dry_run: bool = False, label: str | None = None) -> dict:
        """변경 커맨드 1개 이상을 하나의 트랜잭션으로 실행한다. 실패하면 전체를 되돌린다."""
        t_start = time.perf_counter()
        c = self.conn
        self._heal()   # 이전 실패로 오염됐으면 쓰기 전에 로그에서 다시 만든다(못 하면 ENGINE_TAINTED 로 거부)
        if not dry_run:
            try:
                c.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as ex:
                raise DobimError("LOCKED", f"프로젝트가 잠겨 있다: {ex}", hint="잠시 뒤 다시 시도하세요") from ex
        applied: list = []
        try:
            self.sync()
            t_sync = time.perf_counter()
            if expected_rev is not None and expected_rev != self.head:
                raise DobimError("REV_CONFLICT", f"리비전이 어긋났다: 기대 {expected_rev}, 현재 {self.head}",
                                 path="/expected_rev", details={"expected": expected_rev, "actual": self.head})
            base = self.head
            txn = f"t{base + 1}"
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            entries, results, warnings, seqs = [], [], [], []
            for cmd, model in cmds:
                if cmd.name.startswith("history.") and len(cmds) > 1:
                    raise DobimError("USAGE", "history.* 는 배치에 섞을 수 없다")
                seq = base + len(entries) + 1
                ctx = Ctx(self, seq, self.actor, dry_run)
                try:
                    res = cmd.func(ctx, model)
                finally:
                    applied.extend(ctx.effects)  # 중간에 실패해도 적용된 효과는 되돌려야 한다
                results.append(res)
                warnings.extend(ctx.warnings)
                if ctx.effects:
                    entries.append((seq, cmd.name, log_args(model), invert(ctx.effects)))
                    seqs.append(seq)
                else:
                    seqs.append(None)
                    warnings.append(f"{cmd.name}: 변경 없음")
            t_apply = time.perf_counter()
            self._t0()
            t_t0 = time.perf_counter()
            aff = affected_of(applied)
            if dry_run:
                done, applied[:] = list(applied), []   # 되돌리는 도중 실패해도 실패 경로가 같은 효과를 또 되돌리지 않게
                self._rollback(done)
                return {"rev": self.head, "results": results, "seqs": seqs, "affected": aff, "warnings": warnings,
                        "entries": len(entries), "dry_run": True}
            for seq, op, args, inv in entries:
                c.execute("INSERT INTO op_log (seq, txn_id, actor, base_rev, ts, op, args, inverse) "
                          "VALUES (?,?,?,?,?,?,?,?)", (seq, txn, self.actor, base, ts, op, args, canon(inv)))
            c.execute("COMMIT")
            for seq, op, _, _ in entries:
                self._note_entry(op, txn)
                self.head = seq
            t_end = time.perf_counter()
            self.timing = {"sync_ms": (t_sync - t_start) * 1e3, "apply_ms": (t_apply - t_sync) * 1e3,
                           "t0_ms": (t_t0 - t_apply) * 1e3, "commit_ms": (t_end - t_t0) * 1e3,
                           "total_ms": (t_end - t_start) * 1e3}
            return {"rev": self.head, "results": results, "seqs": seqs, "affected": aff, "warnings": warnings,
                    "entries": len(entries), "dry_run": False}
        except BaseException as ex:
            if not dry_run and c.in_transaction:
                c.execute("ROLLBACK")
            if applied and not self.tainted:   # 오염이면 되돌리기를 건너뛴다: 어긋난 IFC 에 더 쓰지 않고 바로 다시 만든다
                try:
                    self._rollback(applied)
                except BaseException:
                    pass   # 삼키는 것이 아니다: _rollback 이 이미 오염으로 표시했고, 바로 아래에서 로그로 다시 만든다
            if self.tainted:
                try:
                    self._heal()   # 복원 실패로 IFC 가 로그와 어긋났다: 로그에서 다시 만든다
                except DobimError as healing_failed:
                    raise healing_failed from ex   # 다시 만들 수 없으면 이후 쓰기는 ENGINE_TAINTED 로 거부된다
            if isinstance(ex, (DobimError, KeyboardInterrupt)):
                raise
            if isinstance(ex, sqlite3.OperationalError) and "locked" in str(ex):
                raise DobimError("LOCKED", f"프로젝트가 잠겨 있다: {ex}") from ex
            raise internal_error(ex) from ex

    def _rollback(self, applied: list) -> None:
        """적용한 효과를 거꾸로 되돌린다. 도중에 실패하면 오염으로 표시하고 예외를 올린다."""
        try:
            for e in invert(applied):
                self._apply_effect(e, persist=False)
            self._touch_pending()
        except BaseException as rb:
            self._tainted = self._tainted or f"롤백 실패({type(rb).__name__})"
            raise

    # ------------------------------------------------------------ IFC 투영 (T1 이후, 요청 시)
    def flush_ifc(self) -> int:
        """바뀐 요소만 IFC 로 다시 투영한다. 스펙 노드의 변경 스탬프가 같으면 건너뛴다(조기 차단)."""
        self.sync()
        self._touch_pending()
        n = 0
        for kind in ("level", "wall", "door"):
            for eid in sorted(self.st.by_kind.get(kind, ())):
                if self.st.el[eid]["origin"] != "authored":
                    continue   # 외래·분리 요소의 형상은 IFC 가 정본이다: 투영하지 않는다(동결)
                spec = self.g.q(Q.ifc_spec, eid)
                stamp = self.g.nodes[(Q.ifc_spec, eid)].changed
                if self._proj_stamp.get(eid) != stamp:
                    # 가져온 뒤 이 세션에서 한 번도 투영하지 않았고 명세가 가져올 때와 같으면, 들어온 엔티티가 이미 그 투영이다(가져오기가
                    # 클로저 해시로 확인했다). 다시 투영하면 외부 도구가 붙인 스타일·개구부 신원처럼 의도 밖의 부분이 사라지므로 그대로 둔다.
                    if (self.ifc.base_mode and eid not in self._proj_stamp and eid in self.ifc.base_ents
                            and self._import_spec.get(eid) == digest(spec)):
                        self._proj_stamp[eid] = stamp
                        continue
                    try:
                        self.ifc.project(eid, spec)
                    except BaseException as ex:
                        # 투영이 중간에 멈추면 고아 개구부·반쯤 놓인 배치가 남을 수 있다: 오염으로 표시하고 다시 만든다
                        self._taint(f"투영 {eid}", ex)
                        try:
                            self._heal()
                        except DobimError as healing_failed:
                            raise healing_failed from ex
                        raise
                    self._proj_stamp[eid] = stamp
                    n += 1
        return n

    # ------------------------------------------------------------ 해시 (정규화된 의미 해시)
    def state_dump(self) -> dict:
        self.sync()
        ids = sorted(self.st.el)
        return {
            "elements": [[i, self.st.el[i]] for i in ids],
            "relations": self.st.relations(),
            "psets": {i: p for i in ids if (p := self.ifc.get_psets(i))},
            "materials": {i: m for i in ids if (m := self.ifc.get_material(i)) is not None},
        }

    def state_hash(self) -> str:
        return digest(self.state_dump())

    def derived_hash(self, reference: bool = False) -> str:
        self.sync()
        self._touch_pending()
        g = RefGraph(_Inputs(self)) if reference else self.g
        return digest(Q.derived_bundle(g, sorted(self.st.el)))

    def ifc_hash(self) -> str:
        self.flush_ifc()
        return self.ifc.semantic_hash()

    def hashes(self) -> dict:
        return {"rev": self.head, "state": self.state_hash(), "derived": self.derived_hash(),
                "ifc": self.ifc_hash()}

    def intent_consistency(self) -> list[str]:
        """IFC 에 복제된 DoBIM_Intent 가 의도 저장소와 일치하는지 (S0 발견 8: 낡은 의도 방지)."""
        self.flush_ifc()
        bad = []
        texts = self.ifc.intent_texts()
        for eid in sorted(self.st.el):
            if self.st.el[eid]["origin"] == "foreign":
                continue   # 의도가 없는 요소다
            want = canon(self.g.q(Q.intent_of, eid))
            got = texts.get(eid)
            if self.has_base:   # 외부 도구가 쓴 의도 텍스트는 모양이 달라도 뜻이 같으면 일치로 본다
                try:
                    got = canon(json.loads(got))
                except (TypeError, ValueError):
                    pass
            if got != want:
                bad.append(eid)
        return bad
