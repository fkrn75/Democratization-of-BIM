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

from dobim_core import annot as AN
from dobim_core import drawing as D
from dobim_core import queries as Q
from dobim_core import viewdefs as VD
from dobim_core.errors import DobimError, internal_error
from dobim_core.graph import Graph, RefGraph
from dobim_core.ifc_model import CLOSURE_FORMAT, IfcModel
from dobim_core import kinds as K
from dobim_core.kinds import EFFECTS, ELEMENT_KINDS, PROJECTED_KINDS, REL_KINDS
from dobim_core.registry import Command

SCHEMA_VERSION = "1"
GENESIS_OP = "ifc.import"   # 가져온 시작 상태를 담는 로그 항목(seq 0). 되돌리기 대상이 아니고 리비전(rev)에 세지 않는다
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
    """효과를 뒤집는다. 어느 태그가 값 교환형인지는 kinds.EFFECTS 가 정한다(병합·키 계산과 같은 표).
    엔진 쪽 이름으로 한 겹 감싼다: 변이 시험(tools/run_mutants.py)이 이 이름을 바꿔치기해 되돌리기 검사의 이빨을 확인한다."""
    return K.invert_effect(e)


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


def genesis_effects(rows: list[dict], rels: list) -> list:
    """가져오기가 만든 시작 상태를 효과로 쓴다(요소 생성 + 관계 맺기). 병합이 로그만 접어서 시작 상태를 알 수 있게 하는 기록이다.
    projection_hash·spec_hash 는 IFC 쪽 판정 부산물이라 담지 않는다(origin 은 담는다: 동결 여부는 의미다)."""
    out = [["el", r["id"], None, {"kind": r["kind"], "ifc_class": r["ifc_class"], "origin": r["origin"],
                                  "family_id": r["family_id"], "params": r["params"]}]
           for r in sorted(rows, key=lambda r: (ELEMENT_KINDS[r["kind"]].order, r["id"]))]
    out += [["rel", k, s, d, 0, 1] for k, s, d in sorted(rels)]
    return out


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
        self._issued: set[str] = set()   # 이번 실행이 내준 id(아직 효과로 내보내기 전인 것도 겹치지 않게)

    # ---- 읽기
    def fresh(self) -> RefGraph:
        """캐시 없는 기준 그래프. 지금 메모리 상태를 바로 읽으므로(같은 배치에서 앞 커맨드의 효과도 보인다) 변경 커맨드가
        검증용으로 파생값을 계산할 때 쓴다. 증분 그래프는 배치가 끝나야 갱신되어 낡은 값을 줄 수 있다."""
        return RefGraph(_Inputs(self.eng))

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
        if r is not None:
            self._frozen_check(eid, r, what)
        return self.row(eid, kind)

    @staticmethod
    def _frozen_check(eid: str, row: dict, what: str = "형상") -> None:
        """저작 확인의 단일 관문. 커맨드가 부르는 authored() 와, 효과를 내보내는 쓰기(put_el·rel·set_material)가 같은 검사를 거친다:
        커맨드 하나가 확인을 빠뜨려도(S4 의 family.place, S8 의 merge.apply 가 그랬다) 동결된 요소는 바뀌지 않는다."""
        if row["origin"] != "authored":
            why = "분리됨(외부 도구가 형상을 바꿔 동결됐다)" if row["origin"] == "detached" else "외래 요소(IFC 가 정본이다)"
            raise DobimError("ELEMENT_FROZEN", f"{why}라 {what}을 바꿀 수 없다: {eid}", path="/id",
                             hint="속성은 pset.set / pset.delete 로 편집할 수 있다. 형상은 원본 도구에서 고쳐 다시 가져오세요")

    def rels_out(self, kind, src) -> list:
        return list(self.eng.st.out.get((kind, src), ()))

    def rels_in(self, kind, dst) -> list:
        return list(self.eng.st.inn.get((kind, dst), ()))

    def _in_ifc(self, eid) -> bool:
        """IFC 신원이 있어야 하는 요소인가(뷰 같은 비 IFC 요소가 아닌가). ents 에 있는지가 아니라 종류로 판단한다:
        있어야 할 신원이 없는 것은 숨기지 말고 오류로 드러나야 한다."""
        row = self.eng.st.el.get(eid)
        return row is None or row["kind"] not in Q.NON_IFC_KINDS

    def psets(self, eid) -> dict:
        return self.eng.ifc.get_psets(eid) if self._in_ifc(eid) else {}

    def material(self, eid):
        return self.eng.ifc.get_material(eid) if self._in_ifc(eid) else None

    def det_id(self, label: str) -> str:
        """라벨에서 결정적으로 얻는 GlobalId(같은 프로젝트·같은 라벨이면 같은 값). 내용 주소 id 에 쓴다."""
        return ifcopenshell.guid.compress(uuid.uuid5(uuid.UUID(self.eng.project_guid_uuid), label).hex)

    def complex_material(self, eid) -> bool:
        return self.eng.has_base and self._in_ifc(eid) and self.eng.ifc.has_complex_material(eid)

    def foreign_voids(self, eid) -> list:
        return self.eng.ifc.foreign_voids(eid) if self.eng.has_base else []

    def foreign_typed(self, eid) -> list:
        return self.eng.ifc.foreign_typed(eid) if self.eng.has_base else []

    def new_id(self, kind: str) -> str:
        """결정적 GlobalId. 같은 프로젝트에서 같은 순번이면 같은 값이 나온다.
        이미 있는 요소의 id 나 이번 실행이 이미 내준 id 와 겹치면 순번(n)을 올려 다음 후보를 쓴다(결정적 탐색).
        충돌이 없는 평소에는 첫 후보를 그대로 쓰므로 기존 id(골든 해시)는 바뀌지 않는다. 병합이 상대의 자동 id 요소를 들여온 뒤에도
        우리 다음 자동 id 가 그와 겹쳐 계속 CONSTRAINT 가 나는 일을 막는다. 탐색 결과는 현재 상태에만 의존하므로 재생에 영향이 없다
        (재생은 로그에 적힌 id 를 쓰고 new_id 를 부르지 않는다)."""
        base = uuid.UUID(self.eng.project_guid_uuid)
        while True:
            self._n += 1
            gid = ifcopenshell.guid.compress(uuid.uuid5(base, f"{kind}:{self.seq}:{self._n}").hex)
            if gid not in self.eng.st.el and gid not in self._issued:
                self._issued.add(gid)
                return gid

    # ---- 쓰기 (효과)
    def emit(self, eff: list) -> None:
        self.eng._apply_effect(eff, persist=not self.dry_run)
        self.effects.append(eff)

    def put_el(self, eid: str, new: dict | None) -> None:
        old = self.eng.st.el.get(eid)
        if old == new:
            return
        # 동결된 요소는 바꾸거나 지울 수 없고, 저작이 아닌 요소는 커맨드로 만들 수 없다(외래·분리 요소는 가져오기만 만든다)
        for r in (old, new):
            if r is not None:
                self._frozen_check(eid, r, "형상·삭제" if r is old else "생성")
        self.emit(["el", eid, old, new])

    def rel(self, kind: str, src: str, dst: str, present: bool) -> bool:
        cur = dst in self.eng.st.out.get((kind, src), ())
        if cur == present:
            return False
        if present:   # 관계를 새로 맺을 때 동결된 요소가 끼면 거절한다(끊는 것은 허용: 저작 문을 지우면 분리된 호스트와의 관계도 끊긴다)
            rk = REL_KINDS.get(kind)
            for end, eid in (("src", src), ("dst", dst)):
                row = self.eng.st.el.get(eid)
                if rk is not None and end in rk.guard and row is not None:
                    self._frozen_check(eid, row, f"관계 {kind} 의 연결")
        self.emit(["rel", kind, src, dst, int(cur), int(present)])
        return True

    def set_prop(self, eid: str, pset: str, prop: str, value) -> bool:
        if not self._in_ifc(eid):
            raise DobimError("CONSTRAINT", f"속성을 가질 수 없는 요소다: {eid}", path="/id")
        old = self.eng.ifc.get_psets(eid).get(pset, {}).get(prop)
        if old == value and type(old) is type(value):
            return False
        self.emit(["pset", eid, pset, prop, old, value])
        return True

    def set_material(self, eid: str, name) -> bool:
        if not self._in_ifc(eid):
            if name is None:
                return False
            raise DobimError("CONSTRAINT", f"재료를 가질 수 없는 요소다: {eid}", path="/id")
        old = self.eng.ifc.get_material(eid)
        if old == name:
            return False
        self._frozen_check(eid, self.row(eid), "재료")   # 재료는 형상과 같이 동결된다(속성셋만 편집할 수 있다)
        self.emit(["mat", eid, old, name])
        return True

    def attach(self, eid: str) -> None:
        """지울 요소의 외래 부속을 스냅숏 효과로 남긴다(외래 기반 프로젝트만). 없으면 아무것도 하지 않는다."""
        if not self.eng.has_base or not self._in_ifc(eid):   # 뷰·치수·태그 같은 비 IFC 요소는 IFC 신원이 없어 부속도 없다(종류 레지스트리로 판단)
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


def input_keys_of(effects: list) -> set:
    """효과들이 건드리는 그래프 입력 키. `_apply_effect` 가 pending 에 넣는 키와 같아야 한다(시험이 대조한다).
    view.affected 가 요소 id 만이 아니라 버킷·개수·관계 키까지 따라가게 하려고 있다: 새 요소를 만들면 그 요소의 노드는 아직 없고,
    그 요소를 처음 읽게 될 뷰는 버킷(`wall` 목록)이나 관계(`on_level`)를 이미 읽고 있기 때문이다."""
    keys = set()
    for e in effects:
        keys |= EFFECTS[e[0]].keys(e)   # 태그별 키 계산은 kinds.EFFECTS 한 곳에 있다(att 처럼 키가 없는 태그도 거기서 정해진다)
    return keys


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
        """생성 트랜잭션 안에서 외래 기반 원본과 분류 결과를 적는다. 가져온 상태는 프로젝트의 시작 상태(rev 0)다.
        element/relation 테이블에 풀어 두는 것과 함께, 같은 상태를 로그 seq 0 항목(GENESIS_OP)으로도 적는다: 병합은 로그만 접어 상태를
        알기 때문에(S8) 시작 상태가 로그에 없으면 가져온 프로젝트의 요소가 "출처 없는 속성"으로 보여 병합할 수 없었다.
        seq 0 항목은 리비전에 세지 않고 되돌리기 스택에도 들어가지 않는다. 같은 파일을 가져온 복사본끼리는 이 항목이 같아 공통 조상이 된다.
        element.projection_hash 는 가져오는 순간의 클로저 해시이고 이후 바뀌지 않는다(변조 판정은 가져오는 순간에만 한다)."""
        c = self.conn
        c.execute("INSERT INTO meta VALUES ('closure_format', ?)", (CLOSURE_FORMAT,))
        sha = hashlib.sha256(data).hexdigest()
        eff = genesis_effects(plan.rows, plan.rels)
        c.execute("INSERT INTO op_log (seq, txn_id, actor, base_rev, ts, op, args, inverse) VALUES (0,'t0',?,0,?,?,?,?)",
                  (self.actor, time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), GENESIS_OP,
                   canon({"schema": plan.schema, "sha256": sha, "size": len(data)}), canon(invert(eff))))
        c.execute("INSERT INTO foreign_base (id, schema, size, sha256, data) VALUES (1,?,?,?,?)",
                  (plan.schema, len(data), sha, zlib.compress(data, 6)))
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
        self.rejudged: list[str] = []   # 해시 형식이 바뀌어 다시 판정하며 동결된 요소 id
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        for seq, txn, op, _ in log:
            if op != GENESIS_OP:   # 시작 상태 기록은 되돌리기 대상이 아니다
                self._note_entry(op, txn)
        self.has_base = base is not None
        if base is not None and meta.get("closure_format") != CLOSURE_FORMAT:
            self._rejudge_import(base)
        self.ifc = IfcModel(self.project_guid_uuid, base)
        self._rebuild_ifc(log)
        self._proj_stamp: dict[str, int] = {}
        self._pending: set = set()
        self._defs_cache: dict[str, tuple] = {}   # 뷰·주석 id -> (행 객체, IFC 에 적을 정의 텍스트). 행은 불변이라 같은 객체면 텍스트도 같다
        self._dead_views: set = set()   # 지워진 뷰: 그래프에 남은 그 뷰의 노드를 다음 갱신 때 정리한다
        self.g = Graph(_Inputs(self))

    def _rejudge_import(self, data: bytes) -> None:
        """해시 형식(CLOSURE_FORMAT)이 바뀌었으면 가져온 프로젝트를 다시 판정한다. 가져올 때 저장한 element.projection_hash 는 그때 형식의
        해시라서(예: S4 가 IfcTypeObject 를 참조로 접도록 정규화를 바꿨다) 새 형식으로 비교하면 모두 어긋난다.
        원본 바이트(foreign_base)로 가져오기와 같은 판정(plan_import)을 현재 형식으로 다시 하고 해시를 갱신한다.
        판정이 저작 → 분리로 바뀐 요소는 동결한다(안전한 쪽). 분리 → 저작으로는 되살리지 않는다(이미 외부 형상을 쥔 채 편집이 쌓였을 수 있다).
        동결로 바뀌면 시작 상태 기록(seq 0)도 같이 고쳐 로그와 상태가 어긋나지 않게 한다. 형식 번호는 같은 트랜잭션에서 올린다."""
        from dobim_core.foreign import plan_import
        plan = plan_import(data)
        by_id = {r["id"]: r for r in plan.rows}
        c = self.conn
        try:
            c.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as ex:
            raise DobimError("LOCKED", f"프로젝트가 잠겨 있다: {ex}", hint="잠시 뒤 다시 시도하세요") from ex
        try:
            cur = c.execute("SELECT value FROM meta WHERE key='closure_format'").fetchone()
            if cur is not None and cur[0] == CLOSURE_FORMAT:   # 다른 프로세스가 먼저 다시 판정했다: 그 결과(동결·명세 해시)를 메모리로 가져온다
                self._reload_judgement()
                c.execute("COMMIT")
                return
            frozen = []
            for eid, r in sorted(by_id.items()):
                row = self.st.el.get(eid)
                if row is None:
                    continue   # 가져온 뒤 지워진 요소
                c.execute("UPDATE element SET projection_hash=? WHERE id=?", (r.get("projection_hash"), eid))
                if row["origin"] == "authored" and r["origin"] == "detached":
                    frozen.append(eid)
                    c.execute("UPDATE element SET origin='detached' WHERE id=?", (eid,))
                    c.execute("DELETE FROM import_spec WHERE id=?", (eid,))
                    self.st.apply_el(eid, row, dict(row, origin="detached"))
                elif row["origin"] == "authored" and r.get("spec_hash"):
                    c.execute("INSERT OR REPLACE INTO import_spec (id, spec_hash) VALUES (?,?)", (eid, r["spec_hash"]))
            g0 = c.execute("SELECT inverse FROM op_log WHERE seq=0").fetchone() if frozen else None
            if g0 is not None:   # 옛 형식 프로젝트(시작 상태 기록이 없다)는 고칠 기록이 없다: 요소 테이블만 동결한다
                inv = json.loads(g0[0])
                eff = invert(inv)
                for e in eff:
                    if e[0] == "el" and e[1] in frozen:
                        e[3] = dict(e[3], origin="detached")
                c.execute("UPDATE op_log SET inverse=? WHERE seq=0", (canon(invert(eff)),))
            c.execute("INSERT OR REPLACE INTO meta VALUES ('closure_format', ?)", (CLOSURE_FORMAT,))
            self._reload_judgement()   # 메모리의 명세 해시도 방금 고친 값으로(열기 전에 읽어 둔 값은 낡았다: 첫 세션과 재오픈 세션이 다르게 투영한다)
            c.execute("COMMIT")
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        self.rejudged = frozen

    def _reload_judgement(self) -> None:
        """DB 에 적힌 가져온 요소의 판정(출처)과 투영 명세 해시를 메모리 상태로 다시 읽는다(다시 판정한 뒤)."""
        c = self.conn
        for eid, origin in c.execute("SELECT id, origin FROM element").fetchall():
            row = self.st.el.get(eid)
            if row is not None and row["origin"] != origin:
                self.st.apply_el(eid, row, dict(row, origin=origin))
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='import_spec'").fetchone():
            self._import_spec = dict(c.execute("SELECT id, spec_hash FROM import_spec"))

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
            if self.st.el[eid]["kind"] not in Q.NON_IFC_KINDS:
                self.ifc.create_identity(eid, self.st.el[eid]["kind"], self.st.el[eid]["ifc_class"])
        if base:
            # 시작 상태 기록(seq 0)에 패밀리 타입으로 들어 있던 id: 지금 요소가 아니면 가져온 뒤 지워진 타입이다(제품과 달리 타입은 색인으로 알 수 없다)
            gen = [row for row in log if row[0] == 0 and row[2] == GENESIS_OP]
            types = {e[1] for e in invert(json.loads(gen[0][3])) if e[0] == "el" and e[3] is not None and e[3]["kind"] == "ftype"} if gen else set()
            self.ifc.prune(types)
            # 시작 상태 기록(seq 0)에 정의(뷰·치수·태그)로 들어 있던 id: 그 속성은 이 프로젝트가 소유한다(지금 요소가 아니면 가져온 뒤 지워졌으니 내보낼 때 지운다).
            # 되살리지 못한 정의(손상·참조 불일치)는 여기 없으니 파일에 그대로 남는다(ADR-0015)
            owned = {e[1] for e in invert(json.loads(gen[0][3])) if e[0] == "el" and e[3] is not None and e[3]["kind"] in VD.DEF_KINDS} if gen else set()
            self.ifc.adopt_defs(owned)
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
        if t not in EFFECTS:
            raise DobimError("INTERNAL", f"알 수 없는 효과: {t}")
        # 건드린 그래프 입력 키는 input_keys_of 와 같은 표(kinds.EFFECTS)에서 얻는다: 따로 적으면 태그마다 어긋난다(att 가 그랬다)
        self._pending |= EFFECTS[t].keys(e)
        if t == "el":
            _, eid, old, new = e
            self.st.apply_el(eid, old, new)
            if new is None and old is not None and old["kind"] == "view":
                self._dead_views.add(eid)
            try:   # IFC 쪽이 실패하면 메모리 상태도 되돌리고, IFC 는 어떤 상태인지 모르니 오염으로 표시한다
                if (new or old)["kind"] in Q.NON_IFC_KINDS:
                    pass   # 뷰 정의 같은 비 IFC 요소는 신원이 없다
                elif old is None:
                    self.ifc.create_identity(eid, new["kind"], new["ifc_class"])
                elif new is None:
                    for d in self.ifc.remove_identity(eid):   # 벽과 함께 개구부를 잃은 문은 다시 투영해야 한다
                        self._proj_stamp.pop(d, None)
                    self._proj_stamp.pop(eid, None)
            except BaseException as ex:
                self.st.apply_el(eid, new, old)
                self._taint(f"요소 {eid}", ex)
                raise
            if persist:
                if new is None:
                    self._sql(f"요소 {eid}", "DELETE FROM element WHERE id=?", (eid,))
                else:
                    self._sql(f"요소 {eid}",
                              "INSERT OR REPLACE INTO element (id, kind, ifc_class, origin, family_id, params, projection_hash) "
                              "VALUES (?,?,?,?,?,?, (SELECT projection_hash FROM element WHERE id=?))",   # 가져올 때의 해시는 유지한다
                              (eid, new["kind"], new["ifc_class"], new["origin"], new["family_id"], canon(new["params"]), eid))
        elif t == "rel":
            _, kind, src, dst, _, new = e
            self.st.apply_rel(kind, src, dst, bool(new))
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
        elif t == "mat":
            try:
                self.ifc.set_material(e[1], e[3])
            except BaseException as ex:
                self._taint(f"재료 {e[1]}", ex)
                raise
        elif t == "att":
            # new 가 있으면 되살리고(실행 취소), 없으면(지우기 쪽) 할 일이 없다: 부속은 요소 제거 때 함께 사라지고 old 가 로그에 남는다
            if e[3] is not None:
                try:
                    self.ifc.restore_attachments(e[1], e[3])
                except BaseException as ex:
                    self._taint(f"부속 {e[1]}", ex)
                    raise

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
        if self._dead_views:   # 삭제·생성 취소된 뷰의 낡은 노드를 치운다(되살리면 처음부터 다시 계산한다)
            dead, self._dead_views = self._dead_views, set()
            D.forget_views(self.g, {v for v in dead if v not in self.st.el})

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
        for kind in PROJECTED_KINDS:   # 투영 순서는 kinds.py 가 정한다(타입이 인스턴스보다 먼저: 인스턴스가 타입의 표현 지도를 가리킨다)
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
        self._flush_defs()
        return n

    def _flush_defs(self) -> None:
        """뷰·치수·태그의 정의를 IFC 프로젝트 속성셋에 맞춘다(ADR-0015). 정의가 없으면 아무것도 하지 않는다(IFC 가 그대로다).
        행은 불변이라 같은 객체면 텍스트를 다시 만들지 않는다. 바뀐 정의의 속성만 쓴다."""
        old, cache = self._defs_cache, {}
        for kind in VD.DEF_KINDS:
            for eid in self.st.by_kind.get(kind, ()):
                row = self.st.el[eid]
                hit = old.get(eid)
                cache[eid] = hit if hit is not None and hit[0] is row else (row, VD.def_text(eid, row))
        self._defs_cache = cache
        try:
            self.ifc.sync_defs({eid: t for eid, (_, t) in cache.items()})
        except BaseException as ex:
            self._taint("정의 속성", ex)
            try:
                self._heal()
            except DobimError as healing_failed:
                raise healing_failed from ex
            raise

    # ------------------------------------------------------------ 해시 (정규화된 의미 해시)
    def state_dump(self) -> dict:
        self.sync()
        ids = sorted(self.st.el)
        return {
            "elements": [[i, self.st.el[i]] for i in ids],
            "relations": self.st.relations(),
            "psets": {i: p for i in ids if self.st.el[i]["kind"] not in Q.NON_IFC_KINDS and (p := self.ifc.get_psets(i))},
            "materials": {i: m for i in ids if self.st.el[i]["kind"] not in Q.NON_IFC_KINDS and (m := self.ifc.get_material(i)) is not None},
        }

    def state_hash(self) -> str:
        return digest(self.state_dump())

    def derived_hash(self, reference: bool = False) -> str:
        self.sync()
        self._touch_pending()
        g = RefGraph(_Inputs(self)) if reference else self.g
        bundle = Q.derived_bundle(g, sorted(self.st.el))
        if self.st.by_kind.get("dim") or self.st.by_kind.get("tag"):   # 주석이 없으면 키를 더하지 않아 기존 파생 해시가 그대로다
            bundle["annot"] = AN.annot_bundle(g)
        return digest(bundle)

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
            if self.st.el[eid]["kind"] in Q.NON_IFC_KINDS or self.st.el[eid]["origin"] == "foreign":
                continue   # 요소 단위 IFC 투영이 없는 요소(뷰·주석: 정의는 아래에서 따로 본다)와 의도가 없는 외래 요소
            want = canon(self.g.q(Q.intent_of, eid))
            got = texts.get(eid)
            if self.has_base:   # 외부 도구가 쓴 의도 텍스트는 모양이 달라도 뜻이 같으면 일치로 본다
                try:
                    got = canon(json.loads(got))
                except (TypeError, ValueError):
                    pass
            if got != want:
                bad.append(eid)
        want_defs = {eid: t for eid, (_, t) in self._defs_cache.items()}   # flush_ifc 가 방금 맞춘 정의 텍스트: IFC 에 실려 있는 것과 같아야 한다
        got_defs = self.ifc.defs_texts()
        bad += [eid for eid in sorted(want_defs) if got_defs.get(eid) != want_defs[eid]]
        return bad
