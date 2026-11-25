# SPDX-License-Identifier: MPL-2.0
"""범용 일람표 커맨드(W1: DOC-07 모든 범주·DOC-08 필터/정렬/그룹/합계·DOC-09 계산 필드·DOC-10 재료 물량·DOC-13/IO-12 CSV).

schedule.categories : 범주 목록·요소 수·쓸 수 있는 필드
schedule.table      : 범주 하나의 일람표(필드 선택·계산 필드·필터·정렬·그룹·합계·단위)
schedule.takeoff    : 재료 물량(범주·레벨·재료별 면적·부피, 층 구성은 층마다)
schedule.export     : 일람표·재료 물량을 CSV 또는 일람표 시트(SVG·PDF·PNG)로 내보낸다

기존 schedule.rows·schedule.summary(문·벽)는 그대로다(응답·T0 상태 불변). 행 계산은 schedules.py(순수 질의)가 한다.
시트 그리기는 sheet.py 의 Sheet·표제란과 sheet_tables.draw_table 을 그대로 쓰고 공용 도면 파일은 고치지 않는다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from dobim_core import commands as C
from dobim_core import schedules as S
from dobim_core.commands import A, Name, Text, command
from dobim_core.errors import DobimError

Category = Literal["walls", "doors", "windows", "floors", "roofs", "ceilings", "finishes", "stairs", "railings", "columns", "beams",
                   "rooms", "elevators", "plumbing_fixtures", "furniture", "piping", "ducts", "mechanical", "electrical",
                   "fire_protection", "generic"]
FieldName = Name


class CalcField(A):
    name: Name = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", description="계산 필드 이름(영문·숫자·_). 기존 필드 이름과 겹치면 안 된다")
    expr: Text = Field(description="산식: 수·필드 이름·+ - * /·괄호. 예: 'area * 1.1', 'width * height / 1000000'. 빈 칸·0 나누기는 빈 값")


class Filter(A):
    field: FieldName
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "contains", "empty", "not_empty"] = "eq"
    value: str | float | bool | None = Field(None, description="비교 값(empty·not_empty 는 쓰지 않는다). 수 필드는 수로 비교, 나머지는 문자열로 비교")


class SortKey(A):
    field: FieldName
    desc: bool = False


class TableSpec(A):
    category: Category = Field(description="일람표 범주(schedule.categories 가 목록·요소 수를 준다)")
    fields: list[FieldName] | None = Field(None, max_length=30, description="열(순서대로). 비우면 범주 기본 필드. 계산 필드 이름도 쓸 수 있다")
    calc: list[CalcField] = Field(default_factory=list, max_length=10, description="계산 필드(앞의 계산 필드를 뒤에서 쓸 수 있다)")
    filters: list[Filter] = Field(default_factory=list, max_length=10, description="모두 만족하는 행만(AND)")
    sort: list[SortKey] = Field(default_factory=list, max_length=5, description="정렬 키(그룹 필드 다음). 마지막 키는 늘 id")
    group_by: list[FieldName] = Field(default_factory=list, max_length=3, description="그룹 필드. 그룹마다 개수·가산 필드 합계")
    itemize: bool = Field(True, description="거짓이면 그룹 행만(그룹 안 값이 모두 같은 칸은 그 값, 가산 칸은 합). group_by 가 있어야 한다")
    units: Literal["mm", "m"] = Field("mm", description="m 이면 길이 m·면적 m²·부피 m³")

    @model_validator(mode="after")
    def _check(self):
        known = set(S.fields_of(self.category))
        for c in self.calc:
            if c.name in known:
                raise ValueError(f"계산 필드 이름이 기존 필드와 겹친다: {c.name}")
            try:
                S.parse_expr(c.expr, known)
            except S.ExprError as ex:
                raise ValueError(f"계산 필드 {c.name}: {ex}")
            known.add(c.name)
        used = list(self.fields or ()) + [f.field for f in self.filters] + [s.field for s in self.sort] + list(self.group_by)
        bad = sorted({f for f in used if f not in known})
        if bad:
            raise ValueError(f"{self.category} 범주에 없는 필드다: {', '.join(bad)} (쓸 수 있는 필드: {', '.join(S.fields_of(self.category))})")
        if not self.itemize and not self.group_by:
            raise ValueError("itemize=false 는 group_by 가 있어야 한다")
        return self


def columns_of(a: TableSpec) -> list:
    return list(a.fields) if a.fields else list(S.default_fields(a.category))


def compute(ctx, a: TableSpec) -> tuple[list, dict]:
    """(열, build_table 결과). 행 = 요소 행 + 계산 필드(단위를 바꾼 뒤 계산한다: 산식은 보이는 단위의 값을 쓴다)."""
    g = ctx.g
    trees = [(c.name, S.parse_expr(c.expr, set(S.fields_of(a.category)) | {x.name for x in a.calc})) for c in a.calc]
    rows = []
    for e in g.q(S.category_ids, a.category):
        r = S.convert_units(g.q(S.element_row, e), a.units, S.FIELD_UNIT)
        r = dict(r)
        for name, t in trees:
            r[name] = S.eval_expr(t, r)
        rows.append(r)
    cols = columns_of(a)
    additive = set(S.ADDITIVE) | {name for name, _ in trees}   # 계산 필드도 합계를 낸다(수일 때)
    return cols, S.build_table(rows, cols, [f.model_dump() for f in a.filters], [s.model_dump() for s in a.sort], list(a.group_by), a.itemize, additive)


def display_lines(cols: list, t: dict, itemize: bool, group_by: list) -> list:
    """표를 줄 목록으로: 데이터 줄, (그룹·항목별이면) 그룹 소계 줄, 끝에 합계 줄. 소계·합계 줄은 첫 칸에 이름표를 둔다."""
    def total_line(label, tot):
        line = [tot.get(c) for c in cols]   # 합계가 없는 칸(치수·문자)은 빈칸
        return [label] + line[1:]
    out = []
    if group_by and itemize:
        for gr in t["groups"]:
            out.extend(t["rows"][gr["first"]:gr["last"] + 1])
            key = " / ".join("" if v is None else str(v) for v in gr["key"].values())
            out.append(total_line(f"소계 {key} ({gr['totals']['count']})", gr["totals"]))
    else:
        out.extend(t["rows"])
    out.append(total_line(f"합계 ({t['totals']['count']})", t["totals"]))
    return out


# ---------------------------------------------------------------- schedule.categories
class ScheduleCategories(A):
    pass


@command("schedule.categories", kind="query", risk="read", group="schedule", summary="일람표 범주·필드 목록",
         args=ScheduleCategories, idempotent=True, since="0.9", examples=[{}])
def schedule_categories(ctx, a: ScheduleCategories):
    counts = ctx.g.q(S.category_counts)
    return {"items": [{"category": c, "label": S.CATEGORIES[c], "count": counts.get(c, 0), "fields": list(S.fields_of(c)),
                       "default_fields": list(S.default_fields(c))} for c in S.CATEGORIES],
            "additive": list(S.ADDITIVE)}


# ---------------------------------------------------------------- schedule.table
class ScheduleTable(TableSpec):
    limit: int = Field(200, ge=1, le=1000, description="최대 행 수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    offset: int = Field(0, ge=0, le=1_000_000, description="건너뛸 행 수(쪽 넘김: 이전 응답의 page.next_cursor 를 정수로 준다)")


def _clip_cells(ctx, items):
    from dobim_core import limits
    clipped = 0
    for row in items:
        for i, v in enumerate(row):
            if isinstance(v, str):
                row[i], was = limits.clip_text(v)
                clipped += was
    if clipped:
        ctx.warn(f"길이 상한을 넘는 문자열 {clipped}개는 앞부분만 보인다")


@command("schedule.table", kind="query", risk="read", group="schedule", summary="범주별 일람표(필드·필터·정렬·그룹·합계·계산)",
         args=ScheduleTable, idempotent=True, since="0.9",
         examples=[{"category": "walls", "fields": ["name", "level", "length", "area", "area_x"], "calc": [{"name": "area_x", "expr": "area * 1.1"}],
                    "filters": [{"field": "length", "op": "gt", "value": 1000}], "sort": [{"field": "length", "desc": True}],
                    "group_by": ["level"], "units": "m"}])
def schedule_table(ctx, a: ScheduleTable):
    cols, t = compute(ctx, a)
    window = [list(r) for r in t["rows"][a.offset:a.offset + a.limit]]
    _clip_cells(ctx, window)
    base = {"category": a.category, "columns": cols, "units": a.units, "totals": t["totals"], "groups": t["groups"]}
    items, cut = C._fit(window, base=base, cursor_of=lambda i: str(a.offset + i + 1))
    end = a.offset + len(items)
    nxt = str(end) if end < len(t["rows"]) else None
    return {**base, "items": items, "page": C._page(nxt, len(t["rows"]), cut)}


# ---------------------------------------------------------------- schedule.takeoff
TAKEOFF_CATS = ("walls", "floors", "roofs", "ceilings", "finishes")


class TakeoffSpec(A):
    categories: list[Literal["walls", "floors", "roofs", "ceilings", "finishes", "beams"]] = Field(
        default_factory=lambda: list(TAKEOFF_CATS), min_length=1, max_length=6, description="재료 물량을 낼 범주(기본: 벽·바닥·지붕·천장·마감). beams(보, W2 열다섯 번째 PR)는 부피만이고 기본에 들지 않는다")
    by_level: bool = Field(True, description="참이면 레벨별로 나눈다")
    units: Literal["mm", "m"] = Field("m", description="m 이면 면적 m²·부피 m³(기본)")


TAKEOFF_COLS = ["category", "level", "material", "count", "area", "volume"]


def takeoff_rows(ctx, a: TakeoffSpec) -> list:
    rows = S.takeoff(ctx.g, tuple(dict.fromkeys(a.categories)), a.by_level)
    return [S.convert_units(r, a.units, {"area": "area", "volume": "vol"}) for r in rows]


@command("schedule.takeoff", kind="query", risk="read", group="schedule", summary="재료 물량(범주·레벨·재료별 면적·부피)",
         args=TakeoffSpec, idempotent=True, since="0.9", examples=[{"categories": ["walls", "floors"], "by_level": True}])
def schedule_takeoff(ctx, a: TakeoffSpec):
    rows = takeoff_rows(ctx, a)
    total = {"area": sum(r["area"] for r in rows if r["area"] is not None), "volume": sum(r["volume"] for r in rows)}
    items = [[r[c] for c in TAKEOFF_COLS] for r in rows]
    _clip_cells(ctx, items)
    fitted, cut = C._fit(items, base={"columns": TAKEOFF_COLS, "units": a.units, "totals": total})
    return {"columns": TAKEOFF_COLS, "units": a.units, "items": fitted, "totals": total, "page": C._page(None, len(items), cut)}


# ---------------------------------------------------------------- schedule.export
class ScheduleExport(A):
    path: str = Field(max_length=260, description="작업 폴더 안의 .csv(표 데이터, UTF-8 BOM·CRLF) 또는 .svg·.pdf·.png(일람표 시트) 경로")
    table: TableSpec | None = Field(None, description="내보낼 일람표(table·takeoff 중 하나만)")
    takeoff: TakeoffSpec | None = Field(None, description="내보낼 재료 물량(table·takeoff 중 하나만)")
    title: Text = Field("", description="표 제목(비우면 '<범주> 일람표' 또는 '재료 물량표')")
    paper: C.Paper = Field("A3", description="시트 용지 A0~A4(끝에 P 면 세로). 행이 많으면 큰 용지")
    project_name: Text = Field("", description="표제란 프로젝트명")
    sheet_number: Text = Field("A-600", description="표제란 도면번호")
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="작성일 YYYY-MM-DD(생략하면 오늘 날짜)")
    overwrite: bool = Field(False, description="거짓이면 이미 있는 파일을 덮어쓰지 않는다. 참이면 덮어쓰며 덮어쓰기 전에 자동 스냅숏을 남긴다(확인 토큰 필요)")
    max_px: C.PngMaxPx = 1600
    crop: C.PngCrop | None = None

    @model_validator(mode="after")
    def _one(self):
        if (self.table is None) == (self.takeoff is None):
            raise ValueError("table 과 takeoff 중 하나만 준다")
        return self


def _target(ctx, a: ScheduleExport):
    """.csv 는 여기서, 시트 형식은 view.export 와 같은 검사(작업 폴더·스냅숏 폴더·덮어쓰기 동의)."""
    from dobim_core.paths import resolve_in_workspace
    p = resolve_in_workspace(ctx.eng.workspace, a.path)
    ext = p.suffix.lower()
    if ext != ".csv":
        return C._view_export_target(ctx, a)
    if C.SNAP_DIR.lower() in [x.lower() for x in p.parts] or p.is_dir() or ":" in p.name:
        raise DobimError("PATH_DENIED", f"쓸 수 없는 경로다: {p.name}", path="/path")
    if p.exists() and not a.overwrite:
        raise DobimError("FILE_EXISTS", f"이미 있는 파일이다: {p.name}", path="/path", hint="overwrite=true 로 덮어쓰거나 다른 이름을 쓰세요")
    return p, ext


def _export_preview(ctx, a: ScheduleExport):
    p, ext = _target(ctx, a)
    C._snap_dir(p, ctx.eng.workspace)
    return {"will_write": p.name, "format": ext[1:], "overwrites": p.exists(), "snapshot_before_overwrite": p.exists()}


def _fmt(v, units) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = f"{v:.3f}" if units == "m" else f"{v:.1f}"
        s = s.rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(v)


def _col_width(head: str, cells: list) -> float:
    est = max([S_len(head)] + [S_len(c) for c in cells[:200]])
    return min(60.0, max(14.0, est * 2.6 + 3.0))


def S_len(s: str) -> float:
    return sum(1.0 if ord(c) >= 0x2E80 else 0.58 for c in s)


def build_schedule_sheet(spec: dict, title: str, header: list, lines: list):
    """일람표 시트: 표가 한 단에 안 들어가면 머리글을 반복해 오른쪽 단으로 나눈다(Revit 의 일람표 나누기). 용지를 넘으면 SheetError."""
    from dobim_core import sheet as SH
    from dobim_core import sheet_tables as ST
    sh = SH.Sheet(spec["paper"])
    sh.begin("sheet-border")
    sh.rect(SH.OUTER, SH.OUTER, sh.w - 2 * SH.OUTER, sh.h - 2 * SH.OUTER, 0.7)
    sh.end()
    ax0, ay0 = SH.OUTER + SH.PAD, SH.OUTER + SH.PAD
    ax1, ay1 = sh.w - SH.OUTER - SH.TB_W - SH.PAD, sh.h - SH.OUTER - SH.PAD
    cols = [(h, _col_width(h, [r[i] for r in lines])) for i, h in enumerate(header)]
    tw = sum(c[1] for c in cols)
    per = int((ay1 - ay0 - 12.0) // 5.6) - 1   # 한 단의 행 수(제목·머리글·아래 여유를 뺀다)
    if per < 1 or tw > ax1 - ax0:
        raise SH.SheetError(f"일람표가 {sh.paper} 용지에 들어가지 않는다(표 폭 {tw:.0f}mm). 열을 줄이거나 A1 을 쓰세요")
    chunks = [lines[i:i + per] for i in range(0, len(lines), per)] or [[]]
    x = ax0
    for k, ch in enumerate(chunks):
        if x + tw > ax1 + 1e-6:
            raise SH.SheetError(f"일람표 행 {len(lines)}줄이 {sh.paper} 용지 한 장에 다 들어가지 않는다. 필터·그룹으로 줄이거나 A1 을 쓰세요")
        ST.draw_table(sh, x, ay0, title if k == 0 else f"{title} (계속 {k + 1})", cols, ch, f"schedule-{k + 1}")
        x += tw + SH.GAP
    sh.begin("title-block")
    SH.title_block(sh, spec, [])
    sh.end()
    return sh, len(chunks)


@command("schedule.export", kind="session", risk="destructive", group="schedule",
         summary="일람표·재료 물량을 CSV·시트로 내보내기", args=ScheduleExport, since="0.9", preview=_export_preview,
         examples=[{"path": "schedule-walls.csv", "table": {"category": "walls", "group_by": ["level"]}, "overwrite": True},
                   {"path": "schedule-takeoff.svg", "takeoff": {"categories": ["walls"]}, "project_name": "예시 프로젝트", "date": "2026-01-01", "overwrite": True}])
def schedule_export(ctx, a: ScheduleExport):
    import datetime

    from dobim_core import pdf_font as PF
    from dobim_core import sheet as SH
    p, ext = _target(ctx, a)
    if a.table is not None:
        cols, t = compute(ctx, a.table)
        raw = display_lines(cols, t, a.table.itemize, list(a.table.group_by))
        header, units = cols, a.table.units
        title = a.title or f"{S.CATEGORIES[a.table.category]} 일람표"
        nrows = len(t["rows"])
    else:
        rows = takeoff_rows(ctx, a.takeoff)
        header, units = TAKEOFF_COLS, a.takeoff.units
        raw = [[r[c] for c in TAKEOFF_COLS] for r in rows]
        tot = ["합계", None, None, sum(r["count"] for r in rows), sum(r["area"] for r in rows if r["area"] is not None), sum(r["volume"] for r in rows)]
        raw.append(tot)
        title = a.title or "재료 물량표"
        nrows = len(rows)
    out = {"path": p.name, "format": ext[1:], "rows": nrows, "columns": header}
    px, font_name, dropped = None, None, False
    if ext == ".csv":
        blob = ("﻿" + S.csv_text(header, raw)).encode("utf-8")
    else:
        lines = [[_fmt(v, units) for v in r] for r in raw]
        spec = {"paper": a.paper, "project": a.project_name or "(프로젝트명 없음)", "title": title, "number": a.sheet_number,
                "date": a.date or datetime.date.today().isoformat(), "north_deg": 0.0}
        try:
            sh, nblocks = build_schedule_sheet(spec, title, header, lines)
        except SH.SheetError as ex:
            raise DobimError("CONSTRAINT", str(ex), path="/paper")
        out["blocks"] = nblocks
        if ext == ".svg":
            blob = SH.render_sheet_svg(sh, spec)
        elif ext == ".png":
            rr = C.png_of(ctx, sh, a)
            blob, px, font_name = rr["png"], [rr["width"], rr["height"]], rr["font"]
        else:
            font = PF.load_pdf_font()
            blob, dropped = SH.render_sheet_pdf(sh, spec, font)
            font_name = font.name if font else None
            if font is None:
                ctx.warn("한글 글꼴(Noto Sans KR·나눔고딕)을 찾지 못해 PDF 의 한글이 '?' 로 나왔다. 글꼴을 설치하거나 환경 변수 DOBIM_PDF_FONT 에 .ttf 경로를 주세요")
        out.update(paper=a.paper, font_embedded=font_name, non_ascii_dropped=dropped)
    snap = C._snapshot_existing(p, ctx.eng.workspace)
    try:
        C._replace(p, lambda tmp: tmp.write_bytes(blob))
    except BaseException:
        C._drop_snapshot(p, snap, ctx.eng.workspace)
        raise
    out.update(bytes=len(blob), rev=ctx.eng.rev, snapshot=snap)
    if px is not None:
        out["pixels"] = px
    return out
