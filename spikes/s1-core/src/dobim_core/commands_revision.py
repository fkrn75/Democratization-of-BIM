# SPDX-License-Identifier: MPL-2.0
"""리비전(ANN-17·DOC-15)과 키노트(ANN-09) (W1 1-B 3차).

revision.*  : 프로젝트 리비전(순번·표기·일자·내용·발행처·발행자·발행 상태)과 그 리비전을 싣는 시트. 시트 개정란은 리비전을 싣는 시트(sheets)와,
              올린 뷰에 같은 표기의 리비전 구름(draft cloud 의 text)이 있는 시트에 리비전을 적는다(Revit 의 시트 리비전 표와 같은 규칙).
              revision.issue 로 발행(시트 발행)하면 그 리비전은 고치거나 지울 수 없다(발행을 풀면 다시 고칠 수 있다). revision.schedule 은 리비전 일정표다.
keynote.*   : 키노트 표(키 -> 설명)와 요소의 키노트(속성셋 DoBIM_Keynote.Key, 패밀리 인스턴스는 없으면 타입의 키노트). 태그 필드 keynote 로 평면에 달고,
              keynote.legend 가 쓰인 키노트의 범례(키·설명·태그 수)를 낸다. sheet.index 의 표 revisions·keynotes 가 같은 행을 시트에 그린다.
리비전·키노트는 비 IFC 의도 요소라 정의가 DoBIM_Defs 로 IFC 를 왕복하고, 실행 취소·병합을 그대로 탄다. 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field

from dobim_core import annot as AN
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core.commands import A, Id, IdArg, Text, _det_id, _fresh_id, command, row
from dobim_core.errors import DobimError

Date = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")]
Mark = Annotated[str, Field(min_length=1, max_length=8, pattern=r"^\S(.*\S)?$", description="리비전 표기(예: 1, A). 앞뒤 공백 없이 1~8자")]
Desc = Annotated[Text, Field(max_length=255)]
Who = Annotated[Text, Field(max_length=K.NAME_MAX)]


def _key_ok(v: str) -> str:
    if not K.keynote_key_ok(v):
        raise ValueError("키노트 키는 공백 없는 1~16자")
    return v


Key = Annotated[str, Field(min_length=1, max_length=K.KEYNOTE_KEY_MAX, description="키노트 키(예: 03 30 00, A-12 처럼 공백 없이: 03.30.00)"), AfterValidator(_key_ok)]
SheetIds = Annotated[list[Id], Field(max_length=K.REVISION_SHEETS_MAX, description="이 리비전을 개정란에 싣는 시트 id(구름이 없어도 싣는다)")]


# ================================================================ 리비전
def _revs(st):
    return [(i, st.el[i]["params"]) for i in sorted(st.by_kind.get("revision", ()))]


def _mark_free(ctx, mark, me=None):
    for i, p in _revs(ctx.st):
        if i != me and p["name"] == mark:
            raise DobimError("CONSTRAINT", f"같은 표기의 리비전이 있다: {mark}", path="/mark")


def _check_sheets(ctx, sheets) -> list:
    if len(set(sheets)) != len(sheets):
        raise DobimError("CONSTRAINT", "같은 시트를 두 번 실을 수 없다", path="/sheets")
    for s in sheets:
        ctx.row(s, "sheet")
    return list(sheets)


def _unissued(ctx, rid, what):
    r = ctx.row(rid, "revision")
    if r["params"]["issued"]:
        raise DobimError("CONSTRAINT", f"발행된 리비전은 {what} 수 없다: {r['params']['name']}", path="/id",
                         hint="revision.issue 에 issued=false 를 주어 발행을 풀면 다시 고칠 수 있다")
    return r


def revision_sheets(st) -> dict:
    """리비전 id -> {시트 id: 근거("listed" 직접 싣기 | "cloud" 올린 뷰의 같은 표기 구름)}. 리비전이 없으면 빈 사전(다른 것을 읽지 않는다)."""
    revs = _revs(st)
    if not revs:
        return {}
    by_mark = {p["name"]: i for i, p in revs}
    out = {i: {s: "listed" for s in p["sheets"] if st.el.get(s, {}).get("kind") == "sheet"} for i, p in revs}
    on: dict = {}   # 뷰 -> 그 뷰를 올린 시트
    for s in st.by_kind.get("sheet", ()):
        for v in st.el[s]["params"]["views"]:
            on.setdefault(v, []).append(s)
    for d in st.by_kind.get("draft", ()):
        p = st.el[d]["params"]
        if p["shape"] == "cloud" and p["text"] in by_mark:
            for s in on.get(p["view"], ()):
                out[by_mark[p["text"]]].setdefault(s, "cloud")
    return out


def sheet_revision_rows(st, sheet_id, view_ids) -> list:
    """시트 개정란에 더할 모델 리비전(순번 순) [{"rev","date","desc","view"}]. sheet_id 가 None(저장 안 된 시트)이면 올린 뷰의 구름만 본다.
    모델에 리비전이 없으면 빈 목록이라 시트는 이전과 바이트까지 같다."""
    revs = _revs(st)
    if not revs:
        return []
    marks = {p["name"] for _i, p in revs}
    views = set(view_ids)
    clouded = {st.el[d]["params"]["text"] for d in st.by_kind.get("draft", ())
               if st.el[d]["params"]["shape"] == "cloud" and st.el[d]["params"]["view"] in views and st.el[d]["params"]["text"] in marks}
    rows = [p for _i, p in revs if p["name"] in clouded or (sheet_id is not None and sheet_id in p["sheets"])]
    return [{"rev": p["name"], "date": p["date"], "desc": p["desc"], "view": None} for p in sorted(rows, key=lambda p: p["seq"])]


def schedule_rows(st) -> list:
    """리비전 일정표의 행(순번 순): [표기, 일자, 내용, 발행, 발행처, 발행자, 시트 번호들]. sheet.index 의 revisions 표가 쓴다."""
    on = revision_sheets(st)
    out = []
    for i, p in sorted(_revs(st), key=lambda x: x[1]["seq"]):
        nums = sorted(st.el[s]["params"]["sheet_number"] or st.el[s]["params"]["name"] for s in on.get(i, {}))
        out.append([p["name"], p["date"], p["desc"], "발행" if p["issued"] else "-", p["issued_to"] or "-", p["issued_by"] or "-", ", ".join(nums) or "-"])
    return out


class RevisionCreate(A):
    id: Id | None = None
    mark: Mark | None = Field(None, description="리비전 표기. 생략하면 순번(1, 2, ...)")
    date: Date = Field(description="리비전 일자 YYYY-MM-DD")
    desc: Desc = Field("", description="리비전 내용")
    issued_to: Who = Field("", description="발행처(받는 곳)")
    issued_by: Who = Field("", description="발행자")
    sheets: SheetIds = Field(default_factory=list)


@command("revision.create", kind="mutation", risk="write", group="view.revision",
         summary="프로젝트 리비전을 만든다(순번은 마지막 다음). 시트 개정란·리비전 일정표에 오른다",
         args=RevisionCreate, since="0.9",
         examples=[{"id": F.RV1, "date": "2026-02-01", "desc": "1층 평면 문 위치 변경", "issued_to": "건축주", "issued_by": "설계팀"}])
def revision_create(ctx, a: RevisionCreate):
    revs = _revs(ctx.st)
    if len(revs) >= K.REVISIONS_MAX:
        raise DobimError("CONSTRAINT", f"리비전은 {K.REVISIONS_MAX}개까지다", path="/")
    seq = max((p["seq"] for _i, p in revs), default=0) + 1
    if seq > K.REVISION_SEQ_MAX:
        raise DobimError("CONSTRAINT", f"리비전 순번은 {K.REVISION_SEQ_MAX} 까지다", path="/")
    mark = a.mark if a.mark is not None else str(seq)
    _fresh_id(ctx, a, "revision")
    _mark_free(ctx, mark)
    p = {"name": mark, "seq": seq, "date": a.date, "desc": a.desc, "issued_to": a.issued_to, "issued_by": a.issued_by, "issued": False,
         "sheets": _check_sheets(ctx, a.sheets)}
    ctx.put_el(a.id, row("revision", "", "revision.basic", p))
    return {"id": a.id, "seq": seq, "mark": mark}


class RevisionSet(A):
    forbid_null = True
    id: Id
    mark: Mark | None = None
    date: Date | None = None
    desc: Desc | None = None
    issued_to: Who | None = None
    issued_by: Who | None = None
    sheets: SheetIds | None = None


@command("revision.set", kind="mutation", risk="write", group="view.revision",
         summary="발행 전 리비전의 표기·일자·내용·발행처·발행자·싣는 시트를 고친다",
         args=RevisionSet, since="0.9", examples=[{"id": F.RV1, "desc": "1층 평면 문 위치·폭 변경"}])
def revision_set(ctx, a: RevisionSet):
    r = _unissued(ctx, a.id, "고칠")
    p = dict(r["params"])
    if a.mark is not None:
        _mark_free(ctx, a.mark, a.id)
        p["name"] = a.mark
    for k in ("date", "desc", "issued_to", "issued_by"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    if a.sheets is not None:
        p["sheets"] = _check_sheets(ctx, a.sheets)
    ctx.put_el(a.id, dict(r, params=p))
    return {"id": a.id}


class RevisionIssue(A):
    id: Id
    issued: bool = Field(True, description="참이면 발행(시트 발행), 거짓이면 발행을 푼다")
    date: Date | None = Field(None, description="발행 일자(주면 리비전 일자를 이 날로 바꾼다)")
    issued_to: Who | None = Field(None, description="발행처")
    issued_by: Who | None = Field(None, description="발행자")


@command("revision.issue", kind="mutation", risk="write", group="view.revision",
         summary="리비전을 발행(시트 발행)하거나 발행을 푼다. 발행된 리비전은 고치거나 지울 수 없다",
         args=RevisionIssue, since="0.9", examples=[{"id": F.RV1, "issued_to": "건축주"}])
def revision_issue(ctx, a: RevisionIssue):
    r = ctx.row(a.id, "revision")
    p = dict(r["params"], issued=a.issued)
    if a.issued and r["params"]["issued"]:
        raise DobimError("CONSTRAINT", f"이미 발행된 리비전이다: {p['name']}", path="/id")
    for k in ("date", "issued_to", "issued_by"):
        if getattr(a, k) is not None:
            p[k] = getattr(a, k)
    ctx.put_el(a.id, dict(r, params=p))
    on = revision_sheets(ctx.st).get(a.id, {})
    return {"id": a.id, "issued": a.issued, "sheets": sorted(on)}


@command("revision.delete", kind="mutation", risk="write", group="view.revision",
         summary="발행 전 리비전을 지운다(같은 표기의 구름은 남는다)", args=IdArg,
         since="0.9", examples=[{"id": F.RV1}])
def revision_delete(ctx, a: IdArg):
    _unissued(ctx, a.id, "지울")
    ctx.put_el(a.id, None)
    return {"deleted": [a.id]}


class RevisionSchedule(A):
    pass


@command("revision.schedule", kind="query", risk="read", group="view.revision",
         summary="리비전 일정표: 리비전마다 순번·표기·일자·내용·발행 상태와 실린 시트, 시트마다 현재 리비전",
         args=RevisionSchedule, idempotent=True, examples=[{}])
def revision_schedule(ctx, a: RevisionSchedule):
    st = ctx.st
    on = revision_sheets(st)
    items = []
    for i, p in sorted(_revs(st), key=lambda x: x[1]["seq"]):
        items.append({"id": i, "seq": p["seq"], "mark": p["name"], "date": p["date"], "desc": p["desc"], "issued": p["issued"],
                      "issued_to": p["issued_to"], "issued_by": p["issued_by"],
                      "sheets": [{"id": s, "sheet_number": st.el[s]["params"]["sheet_number"], "via": via} for s, via in sorted(on.get(i, {}).items())]})
    per: dict = {}
    for it in items:
        for s in it["sheets"]:
            per.setdefault(s["id"], []).append(it["mark"])
    sheets = [{"id": s, "sheet_number": st.el[s]["params"]["sheet_number"], "revisions": marks, "current": marks[-1]} for s, marks in sorted(per.items())]
    return {"items": items, "count": len(items), "sheets": sheets}


def drop_sheet(ctx, sid) -> list:
    """시트를 지울 때 그 시트를 싣는 리비전에서 빼낸다(지운 시트를 가리키는 참조를 남기지 않는다). 고친 리비전 id."""
    out = []
    for i, p in _revs(ctx.st):
        if sid in p["sheets"]:
            ctx.put_el(i, dict(ctx.st.el[i], params=dict(p, sheets=[s for s in p["sheets"] if s != sid])))
            out.append(i)
    return out


# ================================================================ 키노트
def _keynote_id(ctx, key):
    """키의 키노트 요소 id(없으면 None). 가져온 프로젝트의 키노트는 결정적 id 가 아닐 수 있어 키로 찾는다."""
    return next((i for i in sorted(ctx.st.by_kind.get("keynote", ())) if ctx.st.el[i]["params"]["name"] == key), None)


def keynote_table(st) -> dict:
    return {st.el[i]["params"]["name"]: st.el[i]["params"]["text"] for i in st.by_kind.get("keynote", ())}


def keynote_usage(ctx, views=None) -> dict:
    """키노트 태그(필드 keynote)가 보이는 키 -> 태그 수. views 를 주면 그 뷰의 태그만 센다."""
    st, g = ctx.st, ctx.g
    out: dict = {}
    for t in st.by_kind.get("tag", ()):
        p = st.el[t]["params"]
        if "keynote" not in p["fields"] or (views is not None and p["view"] not in views):
            continue
        tgt = st.el.get(p["target"])
        if tgt is None:
            continue
        k = g.q(AN.keynote_of, tgt["kind"], p["target"])
        if k is not None:
            out[k] = out.get(k, 0) + 1
    return out


def legend_rows(ctx, views=None) -> list:
    """키노트 범례 행(키 순): [키, 설명, 태그 수]. 표에 없는 키는 설명 '(키노트 표에 없음)'."""
    table = keynote_table(ctx.st)
    return [[k, table.get(k, "(키노트 표에 없음)"), str(n)] for k, n in sorted(keynote_usage(ctx, views).items())]


class KeynoteSet(A):
    key: Key
    text: Annotated[Text, Field(max_length=K.KEYNOTE_TEXT_MAX)] = Field(description="키노트 설명(예: 콘크리트 벽 THK200 노출 마감)")


@command("keynote.set", kind="mutation", risk="write", group="view.keynote",
         summary="키노트 표의 한 줄(키 -> 설명)을 만들거나 설명을 바꾼다",
         args=KeynoteSet, since="0.9", examples=[{"key": "03.30.00", "text": "현장타설 콘크리트"}])
def keynote_set(ctx, a: KeynoteSet):
    kid = _keynote_id(ctx, a.key)
    if kid is None:
        if len(ctx.st.by_kind.get("keynote", ())) >= K.KEYNOTES_MAX:
            raise DobimError("CONSTRAINT", f"키노트 표는 {K.KEYNOTES_MAX}줄까지다", path="/key")
        kid = _det_id("keynote", a.key)
        if kid in ctx.st.el:
            raise DobimError("CONSTRAINT", f"이미 있는 id 다: {kid}", path="/key")
        if ctx.external_def(kid):
            ctx.warn("가져온 파일에 이 id 의 키노트 정의가 되살려지지 않은 채 남아 있다: 새 정의가 그 속성을 덮어쓴다")
        ctx.put_el(kid, row("keynote", "", "keynote.basic", {"name": a.key, "text": a.text}))
        return {"id": kid, "created": True}
    r = ctx.row(kid)
    ctx.put_el(kid, dict(r, params=dict(r["params"], text=a.text)))
    return {"id": kid, "created": False}


class KeynoteDelete(A):
    key: Key


@command("keynote.delete", kind="mutation", risk="write", group="view.keynote",
         summary="키노트 표의 한 줄을 지운다(요소에 단 키는 남고 범례에 '(키노트 표에 없음)'으로 나온다)",
         args=KeynoteDelete, since="0.9", examples=[{"key": "03.30.00"}])
def keynote_delete(ctx, a: KeynoteDelete):
    kid = _keynote_id(ctx, a.key)
    if kid is None:
        raise DobimError("NOT_FOUND", f"키노트가 없다: {a.key}", path="/key")
    ctx.put_el(kid, None)
    return {"deleted": [kid]}


class KeynoteAssign(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=1000, description="키노트를 달 요소(벽·문·슬래브·패밀리 인스턴스·패밀리 타입 등 IFC 요소)")]
    key: Key | None = Field(description="키노트 표의 키. null 이면 요소의 키노트를 뗀다")


@command("keynote.assign", kind="mutation", risk="write", group="view.keynote",
         summary="요소에 키노트를 단다(속성셋 DoBIM_Keynote.Key). 패밀리 타입에 달면 키가 없는 인스턴스가 따른다",
         args=KeynoteAssign, since="0.9", examples=[{"ids": [F.W1, F.W2], "key": "03.30.00"}])
def keynote_assign(ctx, a: KeynoteAssign):
    if len(set(a.ids)) != len(a.ids):
        raise DobimError("CONSTRAINT", "같은 요소를 두 번 줄 수 없다", path="/ids")
    if a.key is not None and _keynote_id(ctx, a.key) is None:
        raise DobimError("NOT_FOUND", f"키노트 표에 없는 키다: {a.key}", path="/key", hint="keynote.set 으로 먼저 표에 넣으세요")
    for i, eid in enumerate(a.ids):
        if ctx.row(eid)["kind"] in K.NON_IFC_KINDS:
            raise DobimError("CONSTRAINT", "키노트는 IFC 요소에만 단다(뷰·주석·시트 제외)", path=f"/ids/{i}")
    changed = [eid for eid in a.ids if ctx.set_prop(eid, AN.KEYNOTE_PSET, AN.KEYNOTE_PROP, a.key)]
    return {"changed": changed, "key": a.key}


class KeynoteLegend(A):
    view: Id | None = Field(None, description="이 뷰의 키노트 태그만 센다")
    sheet: Id | None = Field(None, description="이 시트에 올린 뷰의 키노트 태그만 센다(view 와 함께 주지 않는다)")


@command("keynote.legend", kind="query", risk="read", group="view.keynote",
         summary="키노트 표와 범례: 표 전체(키·설명)와, 키노트 태그가 보이는 키의 범례(키·설명·태그 수)",
         args=KeynoteLegend, idempotent=True, examples=[{}])
def keynote_legend(ctx, a: KeynoteLegend):
    if a.view is not None and a.sheet is not None:
        raise DobimError("CONSTRAINT", "view 와 sheet 중 하나만 준다", path="/sheet")
    views = None
    if a.view is not None:
        ctx.row(a.view, "view")
        views = {a.view}
    elif a.sheet is not None:
        views = set(ctx.row(a.sheet, "sheet")["params"]["views"])
    table = keynote_table(ctx.st)
    return {"table": [{"key": k, "text": table[k]} for k in sorted(table)],
            "legend": [{"key": k, "text": t if k in table else None, "tags": int(n)} for k, t, n in legend_rows(ctx, views)]}
