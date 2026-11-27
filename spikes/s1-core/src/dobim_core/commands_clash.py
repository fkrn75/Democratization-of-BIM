# SPDX-License-Identifier: MPL-2.0
"""간섭 검토 커맨드(W2 스물여덟 번째 PR, COL-08). 두 요소 묶음 사이의 3D 겹침을 찾아 목록으로 돌려준다(Revit 의 간섭 검사).

- clash.check: 읽기 전용 검사. 두 묶음(범주·요소 종류·요소 id·레벨)을 주면 겹친 쌍을 깊이 큰 순으로 돌려준다(쌍마다 두 요소·겹침 깊이·부피·대표점, 응답 상한에 맞춰 쪽으로 나눈다).
  b 를 생략하면 a 묶음 안의 쌍을 본다. 호스팅(벽-문·창)·조인(벽-벽)·보 끝이 닿은 기둥·주 보·난간의 원천·승강기가 뚫는 슬래브처럼 설계상 의도된 접합은 기본으로 뺀다.
- clash.export: 같은 검사를 CSV(일람표와 같은 UTF-8 BOM·CRLF) 또는 JSON(검사 정의를 함께 담아 다시 돌릴 수 있다)으로 내보낸다.
- clash.compare: 이전 내보내기(JSON)와 지금의 결과를 견줘 새로 생긴·없어진·깊이가 달라진 쌍을 알려 준다(검사 정의는 파일에 든 것을 쓴다).

계산은 clash.py(순수). 이 모듈은 선택·출력·파일만 다룬다. 설계와 한계는 spikes/phase1-drawings/baseline.md 87절."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, model_validator

from dobim_core import clash as CL
from dobim_core import commands as C
from dobim_core import queries as Q
from dobim_core import schedules as S
from dobim_core.commands import A, Id, command
from dobim_core.errors import DobimError

ClashCategory = Literal[tuple(k for k in S.CATEGORIES if k != "rooms")]
ClashKind = Literal[CL.SHAPE_KINDS]
FORMAT = "dobim-clash"
FORMAT_VERSION = 1
PAIRS_MAX = 200000     # 한 번에 다루는 간섭 쌍 수 상한(그 이상은 거절: 선택을 좁힌다)


class ClashSel(A):
    forbid_null = True
    categories: list[ClashCategory] = Field(default_factory=list, max_length=len(S.CATEGORIES),
                                            description="범주(schedule.categories 와 같은 이름: walls, beams, ducts, piping, columns ...)")
    kinds: list[ClashKind] = Field(default_factory=list, max_length=len(CL.SHAPE_KINDS), description="요소 종류(wall, slab, beam, solid, finst, curtwall ...)")
    ids: list[Id] = Field(default_factory=list, max_length=5000, description="요소 id 목록")
    level: Id | None = Field(None, description="이 레벨의 요소만(범주·종류로 고른 것에 걸린다. id 로 직접 준 요소는 그대로 넣는다)")

    @model_validator(mode="after")
    def _some(self):
        if not (self.categories or self.kinds or self.ids):
            raise ValueError("categories·kinds·ids 중 하나는 있어야 한다")
        return self


class ClashDef(A):
    forbid_null = True
    a: ClashSel = Field(description="첫째 묶음")
    b: ClashSel | None = Field(None, description="둘째 묶음. 생략하면 a 묶음 안의 쌍을 본다")
    tolerance: float = Field(CL.DEFAULT_TOL, ge=0.0, le=1000.0,
                             description="허용 오차 mm: 겹침 깊이(겹친 덩어리의 최소 두께)가 이 값 이하인 쌍은 간섭이 아니다(면이 맞닿기만 한 것은 깊이 0). 기본 1mm")
    include_connected: bool = Field(False, description="true = 설계상 의도된 접합(호스팅·조인·보 끝이 닿은 기둥·주 보·난간의 원천·승강기가 뚫는 슬래브)도 센다")
    approx: bool = Field(True, description="false = 근사 형상(지붕·난간·승강기·커튼월·볼록하지 않은 일반 솔리드 등 경계 상자)의 요소는 뺀다")


class ClashCheck(ClashDef):
    limit: int = Field(100, ge=1, le=1000, description="한 쪽에 담을 쌍의 최대 개수(전체 개수는 count). 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor(깊이 큰 순 목록의 다음 쪽)")


def _all_ids(g, kind):
    out = []
    for b in range(Q.NB):
        out.extend(Q.bucket_ids(g, kind, b))
    return out


def _resolve(g, sel: ClashSel) -> list:
    """선택 묶음 -> 정렬된 요소 id. 없는 id 는 NOT_FOUND."""
    picked = set()
    for c in sel.categories:
        picked.update(S.category_ids(g, c))
    for k in sel.kinds:
        for e in _all_ids(g, k):
            row = Q.el(g, e)
            if row is not None and row["origin"] == "authored":
                picked.add(e)
    if sel.level is not None:
        picked = {e for e in picked if S._level_of(g, e, Q.el(g, e)["kind"]) == sel.level}
    for e in sel.ids:
        if Q.el(g, e) is None:
            raise DobimError("NOT_FOUND", f"없는 요소: {e}", path="/ids")
        picked.add(e)
    return sorted(picked)


def _info(g, e) -> dict:
    row = Q.el(g, e)
    p = row["params"]
    lv = None
    try:
        lid = S._level_of(g, e, row["kind"])
        lv = g.q(Q.level_info, lid)[0] if lid else None
    except (KeyError, IndexError, TypeError):
        lv = None
    return {"id": e, "kind": row["kind"], "category": S.category_of(g, e), "name": p.get("name"), "level": lv}


def _sel_dump(s: ClashSel) -> dict:
    """선택 묶음을 정의에 싣는 형태(빈 목록·없는 레벨은 뺀다)."""
    return {k: v for k, v in s.model_dump().items() if v}


def _definition(a: ClashDef) -> dict:
    return {"a": _sel_dump(a.a), **({"b": _sel_dump(a.b)} if a.b is not None else {}),
            "tolerance": a.tolerance, "include_connected": a.include_connected, "approx": a.approx}


def _run(ctx, a: ClashDef):
    """검사를 돌려 (결과 dict, 묶음 a, 묶음 b) 를 돌려준다."""
    g = ctx.g
    ids_a = _resolve(g, a.a)
    ids_b = _resolve(g, a.b) if a.b is not None else None
    r = CL.check(g, ids_a, ids_b, a.tolerance, a.include_connected, a.approx)
    if len(r["pairs"]) > PAIRS_MAX:
        raise DobimError("CONSTRAINT", f"간섭 쌍이 {PAIRS_MAX}개를 넘는다({len(r['pairs'])}): 선택을 좁히거나 허용 오차를 키우세요", path="/tolerance")
    return r, set(ids_a), set(ids_b) if ids_b is not None else None


def _orient(a, b, set_a, set_b):
    """쌍의 표시 순서: 묶음 a 의 요소가 앞(둘 다 같은 묶음이면 id 순)."""
    if set_b is not None and a not in set_a and b in set_a:
        return b, a
    return a, b


def _rows(ctx, r, set_a, set_b, pairs) -> list:
    g = ctx.g
    rows = []
    for a, b, d, exact, vol, pt in pairs:
        x, y = _orient(a, b, set_a, set_b)
        rows.append({"a": _info(g, x), "b": _info(g, y), "depth_mm": round(d, 2), "volume_mm3": round(vol, 1),
                     "point": [round(c, 1) + 0.0 for c in pt], "basis": "exact" if exact else "approx"})
    return rows

def _page_of(rows, total, start, base, key="pairs"):
    """응답 크기 상한 안에 담을 만큼만 담고 다음 쪽 커서를 붙인다(깊이 큰 순 목록의 start 번째부터)."""
    items, cut = C._fit(rows, base=base, cursor_of=lambda k: C._enc_cursor(start + k + 1))
    more = start + len(items) < total
    return {**base, key: items, "page": C._page(C._enc_cursor(start + len(items)) if more else None, total, cut)}


@command("clash.check", kind="query", risk="read", group="clash",
         summary="간섭 검토: 두 요소 묶음(범주·종류·id) 사이의 3D 겹침을 깊이 큰 순으로 알린다(모델은 바꾸지 않는다)",
         args=ClashCheck, idempotent=True, since="0.26",
         examples=[{"a": {"categories": ["walls"]}, "b": {"categories": ["doors"]}, "tolerance": 1.0},
                   {"a": {"categories": ["walls"]}, "limit": 10}])
def clash_check(ctx, a: ClashCheck):
    r, sa, sb = _run(ctx, a)
    start = C._dec_cursor(a.cursor, "int") or 0
    rows = _rows(ctx, r, sa, sb, r["pairs"][start:start + a.limit])
    if r["pairs"] and start == 0:
        ctx.warn(f"간섭 {len(r['pairs'])}쌍(깊이 {a.tolerance:g}mm 초과)")
    ns = r["no_shape"]
    base = {"count": len(r["pairs"]), "tolerance_mm": a.tolerance,
            "checked": {"candidates": r["candidates"], "no_shape": len(ns), "no_shape_ids": ns[:5]}}
    return _page_of(rows, len(r["pairs"]), start, base)

# ---------------------------------------------------------------- 내보내기
CSV_COLS = ["a_id", "a_kind", "a_category", "a_name", "a_level", "b_id", "b_kind", "b_category", "b_name", "b_level", "depth_mm", "volume_mm3", "x", "y", "z", "basis"]


def _csv_row(r) -> list:
    pt = r["point"] or [None, None, None]
    return [r["a"]["id"], r["a"]["kind"], r["a"]["category"], r["a"]["name"], r["a"]["level"],
            r["b"]["id"], r["b"]["kind"], r["b"]["category"], r["b"]["name"], r["b"]["level"], r["depth_mm"], r["volume_mm3"], *pt, r["basis"]]


class ClashExport(ClashDef):
    path: str = Field(max_length=260, description="작업 폴더 안의 .csv(UTF-8 BOM·CRLF) 또는 .json(검사 정의를 담아 clash.compare 가 다시 쓴다) 경로")
    limit: int = Field(20000, ge=1, le=PAIRS_MAX, description="내보낼 쌍의 최대 개수(깊이 큰 순)")
    overwrite: bool = Field(False, description="거짓이면 이미 있는 파일을 덮어쓰지 않는다. 참이면 덮어쓰며 덮어쓰기 전에 자동 스냅숏을 남긴다(확인 토큰 필요)")


def _target(ctx, a: ClashExport):
    from dobim_core.paths import resolve_in_workspace

    p = resolve_in_workspace(ctx.eng.workspace, a.path)
    ext = p.suffix.lower()
    if ext not in (".csv", ".json"):
        raise DobimError("PATH_DENIED", "간섭 목록은 .csv 또는 .json 으로만 내보낸다", path="/path")
    if C.SNAP_DIR.lower() in [x.lower() for x in p.parts] or p.is_dir() or ":" in p.name:
        raise DobimError("PATH_DENIED", f"쓸 수 없는 경로다: {p.name}", path="/path")
    if p.exists() and not a.overwrite:
        raise DobimError("FILE_EXISTS", f"이미 있는 파일이다: {p.name}", path="/path", hint="overwrite=true 로 덮어쓰거나 다른 이름을 쓰세요")
    return p, ext


def _export_preview(ctx, a: ClashExport):
    p, ext = _target(ctx, a)
    C._snap_dir(p, ctx.eng.workspace)
    return {"will_write": p.name, "format": ext[1:], "overwrites": p.exists(), "snapshot_before_overwrite": p.exists()}


@command("clash.export", kind="session", risk="destructive", group="clash",
         summary="간섭 검토 결과를 CSV·JSON 으로 내보내기(JSON 은 clash.compare 의 기준 파일)",
         args=ClashExport, since="0.26", preview=_export_preview,
         examples=[{"path": "clash-example.csv", "a": {"categories": ["walls"]}, "overwrite": True},
                   {"path": "clash-before.json", "a": {"categories": ["walls"]}, "overwrite": True}])
def clash_export(ctx, a: ClashExport):
    p, ext = _target(ctx, a)
    r, sa, sb = _run(ctx, a)
    shown = r["pairs"][:a.limit]
    rows = _rows(ctx, r, sa, sb, shown)
    truncated = len(r["pairs"]) > a.limit
    if truncated:
        ctx.warn(f"간섭 {len(r['pairs'])}쌍 중 {a.limit}쌍만 내보냈다(깊이 큰 순)")
    if ext == ".csv":
        blob = ("\ufeff" + S.csv_text(CSV_COLS, [_csv_row(x) for x in rows])).encode("utf-8")
    else:
        doc = {"format": FORMAT, "version": FORMAT_VERSION, "rev": ctx.eng.rev, "definition": _definition(a), "count": len(r["pairs"]),
               "truncated": truncated, "pairs": rows}
        blob = (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    snap = C._snapshot_existing(p, ctx.eng.workspace)
    try:
        C._replace(p, lambda tmp: tmp.write_bytes(blob))
    except BaseException:
        C._drop_snapshot(p, snap, ctx.eng.workspace)
        raise
    return {"path": p.name, "format": ext[1:], "rows": len(rows), "count": len(r["pairs"]), "truncated": truncated, "bytes": len(blob),
            "rev": ctx.eng.rev, "snapshot": snap}


# ---------------------------------------------------------------- 비교
class ClashCompare(A):
    forbid_null = True
    before: str = Field(max_length=260, description="이전 clash.export 의 .json 파일(작업 폴더 안)")
    definition: ClashDef | None = Field(None, description="지금 돌릴 검사 정의. 생략하면 파일에 든 정의로 다시 돌린다")
    limit: int = Field(100, ge=1, le=1000, description="한 쪽에 담을 항목의 최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor(항목 목록의 다음 쪽)")


def _load_before(ctx, a: ClashCompare):
    from dobim_core.paths import resolve_in_workspace
    p = resolve_in_workspace(ctx.eng.workspace, a.before)
    if p.suffix.lower() != ".json":
        raise DobimError("PATH_DENIED", "비교 기준은 clash.export 의 .json 파일이다", path="/before")
    if not p.is_file():
        raise DobimError("NOT_FOUND", f"파일이 없다: {p.name}", path="/before")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        ok = isinstance(doc, dict) and doc.get("format") == FORMAT and isinstance(doc.get("pairs"), list) and isinstance(doc.get("definition"), dict)
        pairs = {}
        for x in doc["pairs"] if ok else ():
            pairs[tuple(sorted((x["a"]["id"], x["b"]["id"])))] = x
    except (ValueError, KeyError, TypeError, OSError):
        ok = False
    if not ok:
        raise DobimError("CONSTRAINT", f"clash.export 가 만든 .json 이 아니다: {p.name}", path="/before")
    return p, doc, pairs


@command("clash.compare", kind="query", risk="read", group="clash",
         summary="이전 간섭 검토 결과(JSON)와 지금을 견줘 새로 생긴·없어진·깊이가 달라진 쌍을 알린다",
         args=ClashCompare, idempotent=True, since="0.26",
         examples=[{"before": "clash-before.json"}])
def clash_compare(ctx, a: ClashCompare):
    p, doc, before = _load_before(ctx, a)
    try:
        d = a.definition if a.definition is not None else ClashDef.model_validate(doc["definition"])
    except ValueError as ex:
        raise DobimError("CONSTRAINT", f"파일의 검사 정의를 읽을 수 없다: {str(ex)[:200]}", path="/before") from ex
    r, sa, sb = _run(ctx, d)
    now = {tuple(sorted((x[0], x[1]))): x for x in r["pairs"]}
    new_keys = sorted(k for k in now if k not in before)
    gone_keys = sorted(k for k in before if k not in now)
    both = sorted(k for k in now if k in before)
    changed = [k for k in both if abs(now[k][2] - float(before[k]["depth_mm"])) > 1.0]
    start = C._dec_cursor(a.cursor, "int") or 0
    total = len(new_keys) + len(gone_keys) + len(changed)
    order = [("new", k) for k in new_keys] + [("resolved", k) for k in gone_keys] + [("changed", k) for k in changed]   # 항목은 새로 생긴 -> 없어진 -> 달라진 순
    rows = []
    for kind, k in order[start:start + a.limit]:
        if kind == "new":
            rows.append({"change": "new", **_rows(ctx, r, sa, sb, [now[k]])[0]})
        elif kind == "resolved":
            rows.append({"change": "resolved", "a": before[k]["a"], "b": before[k]["b"], "depth_mm": before[k]["depth_mm"]})
        else:
            rows.append({"change": "changed", "a": before[k]["a"]["id"], "b": before[k]["b"]["id"], "before_mm": before[k]["depth_mm"], "now_mm": round(now[k][2], 2)})
    if doc.get("truncated"):
        ctx.warn("기준 파일이 잘린 목록이라(깊이 큰 순 일부) 새로 생긴 쌍에 이미 있던 작은 간섭이 섞일 수 있다")
    if new_keys and start == 0:
        ctx.warn(f"새로 생긴 간섭 {len(new_keys)}쌍")
    base = {"before": {"file": p.name, "rev": doc.get("rev"), "count": doc.get("count"), "truncated": bool(doc.get("truncated"))},
            "now": {"rev": ctx.eng.rev, "count": len(r["pairs"])},
            "new_count": len(new_keys), "resolved_count": len(gone_keys), "changed_count": len(changed), "unchanged_count": len(both) - len(changed),
            "definition_from": "args" if a.definition is not None else "file"}
    return _page_of(rows, total, start, base, key="items")