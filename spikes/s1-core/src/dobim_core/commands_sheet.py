# SPDX-License-Identifier: MPL-2.0
"""도면 색인 시트(sheet.index)와 도면 작성기준 프로파일(drawing.profile).

sheet.index  : 뷰 없이 도면목록표·범례·주기·레벨 노트·창호 일람표·벽체 일람표·재료표를 한 장에 놓는 시트를 SVG·PDF·PNG·DXF 로 내보낸다.
drawing.profile : 이 엔진이 실제로 쓰는 도면 작성 기준(용지·배치·방위·레벨·표제란·축척·선 굵기·선 겹침·해치·문자·치수·기호)을 값으로 돌려준다.
                  BIM 수행계획서에 그대로 옮겨 적을 수 있는 기준표다(엔진 상수에서 읽으므로 코드와 어긋나지 않는다).
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, create_model, model_validator

from dobim_core import commands as C
from dobim_core import drawing as D
from dobim_core import drawing_export as DX
from dobim_core import fixture as F
from dobim_core.commands import A, Id, Name, Num, Text, command
from dobim_core.errors import DobimError

TABLES = ("drawing_list", "legend", "notes", "levels", "openings", "walls", "materials")


class IndexDrawing(A):
    number: Name = Field(description="도면번호(예: A-101)")
    title: Text = Field(description="도면명")
    scale: Name = Field("", description="축척 표기(예: 1:100, 도면참조). 비우면 '-'")
    note: Text = Field("", description="비고")


class SheetIndex(C.SheetFrame):
    path: str = Field(max_length=260, description="작업 폴더 안의 .svg·.pdf·.png·.dxf 경로(DXF 는 R2013 UTF-8, 용지 mm 1:1, 범주별 층)")
    paper: Literal["A1", "A3"] = Field("A1", description="용지(가로 방향). 표가 많으면 A1")
    project_name: Text = Field("", description="표제란 프로젝트명")
    sheet_name: Text = Field("", description="표제란 도면명(비우면 '도면목록·범례·일람표')")
    sheet_number: Text = Field("A-000", description="표제란 도면번호")
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="작성일 YYYY-MM-DD(생략하면 오늘 날짜)")
    drawings: list[IndexDrawing] = Field(default_factory=list, max_length=60, description="도면목록표의 행. 도면번호 첫 글자(A 건축 등)가 공종이 된다. 비우면 도면목록표를 만들지 않는다")
    tables: list[Literal["drawing_list", "legend", "notes", "levels", "openings", "walls", "materials"]] = Field(
        default_factory=lambda: list(TABLES), max_length=7, description="넣을 표(기본: 모두). 모델에서 뽑는 표: levels 레벨 노트, openings 창호 일람표, walls 벽체 일람표, materials 재료표")
    gl_level: Text = Field("", description="GL(지반) 기준으로 삼을 레벨 이름(비우면 가장 낮은 레벨)")
    notes: list[Text] = Field(default_factory=list, max_length=12, description="주기에 더할 줄")
    overwrite: bool = Field(False, description="거짓이면 이미 있는 파일을 덮어쓰지 않는다. 참이면 덮어쓰며 덮어쓰기 전에 자동 스냅숏을 남긴다(확인 토큰 필요)")
    max_px: C.PngMaxPx = 1600
    crop: C.PngCrop | None = None


def _index_preview(ctx, a: SheetIndex):
    p, ext = C._view_export_target(ctx, a)
    C._snap_dir(p, ctx.eng.workspace)
    return {"will_write": p.name, "format": ext[1:], "paper": a.paper, "tables": list(a.tables), "overwrites": p.exists(), "snapshot_before_overwrite": p.exists()}


@command("sheet.index", kind="session", risk="destructive", group="view",
         summary="색인 시트(도면목록·범례·일람표) 내보내기",
         args=SheetIndex, since="0.3", preview=_index_preview,
         examples=[{"path": "index-example.svg", "paper": "A1", "project_name": "예시 프로젝트", "sheet_number": "A-000", "overwrite": True,
                    "drawings": [{"number": "A-101", "title": "1층 평면도", "scale": "1:100"}], "tables": ["drawing_list", "legend", "notes"]}])
def sheet_index(ctx, a: SheetIndex):
    """뷰 없는 색인 시트: 도면목록표·범례·주기와 모델에서 뽑은 레벨 노트·창호 일람표·벽체 일람표·재료표."""
    import datetime

    from dobim_core import pdf_font as PF
    from dobim_core import sheet as SH
    from dobim_core import sheet_extras as SE
    from dobim_core import sheet_model as SM
    p, ext = C._view_export_target(ctx, a)
    want = set(a.tables)
    g = ctx.g
    spec = {"paper": a.paper, "project": a.project_name or "(프로젝트명 없음)", "title": a.sheet_name or "도면목록·범례·일람표",
            "number": a.sheet_number, "date": a.date or datetime.date.today().isoformat(), "north_deg": 0.0}
    spec.update(SE.frame_spec(a))
    tables: dict = {}
    if "drawing_list" in want and a.drawings:
        tables["drawing_list"] = [[d.number, d.title, SE.DISCIPLINES.get(d.number.split("-")[0], "-"), d.scale or "-", d.note] for d in a.drawings]
    if "levels" in want:
        tables["levels"] = SM.level_rows(g, a.gl_level or None)
    if "openings" in want:
        tables["openings"] = SM.opening_rows(g)
    if "walls" in want:
        tables["walls"] = SM.wall_rows(g)
    if "materials" in want:
        tables["materials"] = SM.material_rows(g)
    if "legend" in want:
        tables["legend"] = True
    if "notes" in want:
        tables["notes"] = ["치수는 mm 단위이며 단위 표기는 생략한다.",
                           "축척은 도면마다 적고, 축척이 다른 도면이 한 장에 있으면 표제란에는 '도면참조'로 적는다.",
                           "레벨은 FL(마감 바닥) 기준이며 GL(지반)과의 관계는 레벨 노트를 따른다.",
                           "투상법은 제3각법(정투상)을 따른다.",
                           "문(D)·창(W)·벽체(WT) 부호는 각 일람표를 따른다."] + list(a.notes)
    try:
        sh = SH.build_index_sheet(spec, tables)
    except SH.SheetError as ex:
        raise DobimError("CONSTRAINT", str(ex), path="/tables")
    dropped, font_name, px, dxf = False, None, None, None
    if ext == ".svg":
        blob = SH.render_sheet_svg(sh, spec)
    elif ext == ".dxf":
        from dobim_core import dxf_export as DXF
        blob, dxf = DXF.render_dxf(sh, style=SH._style(spec))
    elif ext == ".png":
        rr = C.png_of(ctx, sh, a)
        blob, px, font_name = rr["png"], [rr["width"], rr["height"]], rr["font"]
    else:
        font = PF.load_pdf_font()
        blob, dropped = SH.render_sheet_pdf(sh, spec, font)
        font_name = font.name if font else None
        if font is None:
            ctx.warn("한글 글꼴(Noto Sans KR·나눔고딕)을 찾지 못해 PDF 의 한글이 '?' 로 나왔다. 글꼴을 설치하거나 환경 변수 DOBIM_PDF_FONT 에 .ttf 경로를 주세요")
        elif font.missing:
            ctx.warn(f"글꼴에 없는 글자 {len(font.missing)}개는 빈칸으로 나왔다: {''.join(font.missing[:20])}")
    snap = C._snapshot_existing(p, ctx.eng.workspace)
    try:
        C._replace(p, lambda tmp: tmp.write_bytes(blob))
    except BaseException:
        C._drop_snapshot(p, snap, ctx.eng.workspace)
        raise
    out = {"path": p.name, "bytes": len(blob), "format": ext[1:], "paper": a.paper, "sheet_number": a.sheet_number,
           "number_check": SE.number_check(a.sheet_number, p.stem), "tables": sorted(tables), "rows": {k: len(v) for k, v in tables.items() if isinstance(v, list)},
           "font_embedded": font_name, "non_ascii_dropped": dropped, "rev": ctx.eng.rev, "snapshot": snap}
    if px is not None:
        out["pixels"] = px
    if dxf is not None:
        out["dxf"] = dxf
    return out


# ---------------------------------------------------------------- 도면 작성기준 프로파일
class DrawingProfile(A):
    profile: C.SheetProfile | None = Field(None, description="별도 작성기준(문자 높이·치수 형식·기호 크기). 주면 그 값을 얹은 유효 기준을 돌려준다(sheet.export 의 profile 과 같은 입력)")


def build_profile(applied: dict | None = None) -> dict:
    """엔진 상수에서 읽은 도면 작성 기준. 키는 BIM 시행지침의 도면 기준 항목(도면 크기, 배치·방향, 방위·레벨, 표제란, 축척, 선 굵기, 선·해칭, 문자·폰트, 치수·지시선, 기호)을 따른다."""
    from dobim_core import sheet as SH
    uses = {"cut": "절단선: 평면·단면에서 잘린 벽·기둥·슬래브·계단의 윤곽", "proj": "보이는 선: 투영·입면 윤곽, 보이는 모서리",
            "symbol": "기호선: 창·문·계단·방화벽 기호, 방화 표기", "hatch": "해치: 입면 외벽 마감 재료 표시(45도 가는선)"}
    weights = {k: {"width_mm": v[0], "color": v[1], "use": uses[k]} for k, v in DX.STYLE.items()}
    out = _base_profile(SH, uses, weights)
    if applied:
        # 별도 작성기준을 얹은 유효 값: 문자 높이·치수 형식·기호 크기만 바뀐다(주지 않은 값은 기본 그대로)
        th = applied.get("text_height") or {}
        hm = out["text_font"]["heights_mm"]
        for k, name in (("dimension", "dimension"), ("datum", "datum"), ("caption", "view_caption"), ("room_name", "room_name"), ("room_area", "room_area"),
                        ("fire_mark", "fire_mark"), ("tag", "tag")):
            if k in th:
                hm[name] = th[k]
        out["dimension_leader"]["number_format"] = {"decimals": applied.get("dim_decimals"), "thousands_separator": bool(applied.get("dim_thousands")),
                                                    "unit": "mm, 단위 생략"}
        if "symbol_scale" in applied:
            out["symbols_scale"] = applied["symbol_scale"]
            out["text_font"]["symbol_text_heights_mm"] = {k: round(v * applied["symbol_scale"], 3) for k, v in out["text_font"]["symbol_text_heights_mm"].items()}
        if applied.get("line_hierarchy"):
            st = DX.STYLE
            out["line_hierarchy"] = {
                "tiers": [{"rank": 1, "use": "구조체·주요 단면선(절단선)", "width_mm": st["cut"][0]},
                          {"rank": 2, "use": "창호·기호선, 치수선", "width_mm": SH.HIER_DIM_W[0], "symbol_mm": st["symbol"][0]},
                          {"rank": 3, "use": "마감선·입면선(보이는 선), 숨은 선, 치수 보조선", "width_mm": st["proj"][0], "extension_mm": SH.HIER_DIM_W[1]},
                          {"rank": 4, "use": "가는선(레벨 기준선)·해칭", "width_mm": DX.DATUM_W, "hatch_mm": st["hatch"][0]}],
                "text": "문자는 선 굵기가 아니라 용도별 높이(text_font.heights_mm)로 위계를 둔다",
                "cut_nonbearing": {"width_mm": SH.NONBEARING_CUT_W, "use": "Pset_WallCommon.LoadBearing 이 명시적으로 거짓인 벽(비내력벽)의 절단선. 내력벽·값이 없는 벽은 절단선 굵기 그대로",
                                   "class": "cutn"},
                "limit": "LoadBearing 값이 없는 벽은 구분하지 않고 내력벽처럼 절단선으로 그린다. 입면에는 절단선이 없다"}
        if applied.get("hidden_lines"):
            out["line_types"]["hidden"] = (f"숨은 선: 앞의 벽·기둥·슬래브·계단에 가려진 모서리·개구부 윤곽(파선 {SH.HIDDEN_DASH[0]:g}/{SH.HIDDEN_DASH[1]:g}mm, "
                                           "보이는 선과 같은 굵기, 단면·입면만)")
        if applied.get("projection"):
            out["projection"] = {"method": "제3각법(정투상)", "symbol": "표제란 투상법 칸의 제3각법 기호(원뿔대 정면·측면)"}
        out["applied_profile"] = applied
    return out


def _base_profile(SH, uses, weights) -> dict:
    return {
        "version": 1,
        "paper_size": {"sizes_mm": {k: list(v) for k, v in SH.PAPERS.items()}, "orientation": "가로", "border_offset_mm": SH.OUTER, "drawing_area_padding_mm": SH.PAD,
                       "title_block_width_mm": SH.TB_W},
        "layout_orientation": {"plan_up": "평면은 도면 위쪽이 정북(방위표 각도로 보정)", "section_elevation_down": "단면·입면은 중력 방향을 아래로",
                               "view_flow": "왼쪽 위에서 오른쪽, 아래 줄 순으로 놓는다", "view_gap_mm": SH.GAP, "caption": "뷰 아래에 도면명과 S=1:N"},
        "north_level": {"north": "방위표(원 + 화살표 + N), 표제란 아래", "level_datum": "단면·입면의 레벨 기준선(파선)과 '이름 +표고' 문자",
                        "level_unit": "표고·레벨 차이는 mm", "fl_gl": "FL(마감 바닥) 기준, GL 관계는 색인 시트 레벨 노트"},
        "title_block": {"fields": ["프로젝트명", "건축주", "설계사무소", "도면명", "도면번호", "축척", "작성일", "개정차수", "서명·날인", "개정란(차수·일자·내용)", "키 플랜", "축척 막대", "방위표"],
                        "drawing_number": "공종 문자-3자리 일련번호(예: A-101), 파일명과 일치"},
        "scale": {"series": [1, 2, 5, 10, 20, 50, 100, 200, 300], "default": 100, "display": "뷰마다 S=1:N, 표제란 1:N 또는 도면참조", "scale_bar": "표제란에 축척 막대",
                  "model_scale": "모델은 1:1, 도면에서 축척 지정"},
        "line_weights": weights,
        "line_types": {"solid": "보이는 선·절단선", "dashed": "방화벽 중심선·레벨 기준선", "dash_dot": "통심선·단면 절단선·부위 상세 표시", "dimension": f"치수선 0.13mm, 색 {DX.ANNOT_COLOR}"},
        "line_overlap": {"priority": dict(D.PRIORITY), "rule": "같은 직선 위에서 겹치거나 이어진 선분은 하나로 병합하고, 절단선이 덮는 부분은 아래 등급의 선을 지운다. "
                                                           "그리는 순서는 해치·보이는 선·기호선·절단선(굵은 선이 위)", "draw_order": list(DX.LINE_ORDER),
                         "corner_fill": "L 조인 모서리는 코어 벽 형상(조인한 벽 중 id 가 작은 쪽이 상대 벽 반 두께 연장)으로 닫고 IFC 형상과 같다" if D.CORNER_FILL else "없음"},
        "hatch": {"types": [{"id": "ext-finish-45", "angle_deg": 45, "step_mm": D.HATCH_STEP, "line_mm": DX.STYLE["hatch"][0], "use": "입면 외벽 마감 재료(재료 이름 문자 병기)"},
                            {"id": "section-fill", "fill": "#cfcfcf", "use": "절단면 채움(벽·기둥·슬래브·계단)"}]},
        "text_font": {"font": SH.FONT, "pdf": "OFL 고딕 부분집합 내장", "heights_mm": {"room_name": D.LABEL_NAME_H, "room_area": D.LABEL_SUB_H, "fire_mark": D.LABEL_FIRE_H,
                                                                                     "dimension": 2.5, "datum": 2.6, "view_caption": 4.0, "sheet_title": 5.0},
                      "symbol_text_heights_mm": {"grid": 2.3, "section_mark": 1.9, "detail_callout": 2.0, "tag": 1.9}, "direction": "가로쓰기"},
        "dimension_leader": {"style": "연관 치수(선·보조선·45도 눈금·치수 문자)", "unit": "mm, 단위 생략", "ordering": "건물에서 먼 쪽으로 열을 달리해 배치", "leader": "태그 지시선(점 + 선)"},
        "symbols": [{"id": "door", "plan": "열린 문짝 선 + 1/4 호(문에만)", "label": "D<n>"}, {"id": "window", "plan": "개구부 마구리·벽 면 선·유리 중심선(호 없음)", "label": "W<n>"},
                    {"id": "column", "plan": "절단 채움 + 대각선"}, {"id": "stair", "plan": "단 선·걸음선 화살표·절단선"}, {"id": "fire_wall", "plan": "중심선 파선 + 'FW 등급'"},
                    {"id": "fire_door", "plan": "'FD 등급' 문자"}, {"id": "space", "plan": "실명 + 면적(㎡) + 실번호(좌상단부터 시계방향)"},
                    {"id": "wall_mark", "plan": "원 안에 WT<n>"}, {"id": "grid", "plan": "통심선(1점쇄선) + 원 안 번호"}, {"id": "section_mark", "plan": "절단선 양 끝 원 + 보는 방향 화살표"},
                    {"id": "elevation_mark", "plan": "원 + 삼각형(보는 방향)"}, {"id": "detail_callout", "plan": "1점쇄선 사각형 + 부호 원"}, {"id": "revision_cloud", "plan": "구름 선 + 차수 삼각형"}],
    }


@command("drawing.profile", kind="query", risk="read", group="view",
         summary="도면 작성기준 프로파일 조회",
         args=DrawingProfile, idempotent=True, examples=[{}])
def drawing_profile(ctx, a: DrawingProfile):
    """이 엔진의 도면 작성 기준 값(용지·배치·방위·레벨·표제란·축척·선 굵기·선 겹침·해치·문자·치수·기호). profile 을 주면 별도 작성기준을 얹은 유효 값."""
    from dobim_core import sheet_extras as SE
    return build_profile(SE.profile_spec(a).get("profile"))


# ================================================================ 시트 정의의 저장·왕복 (B-04)
# 시트(뷰 배치·표제란 인자)는 뷰와 같은 비 IFC 의도 요소(kind sheet)로 모델에 저장한다. 정의는 DoBIM_Defs 속성으로 IFC 에 실려 왕복한다(viewdefs).
# 시트 파일(SVG·PDF)은 정의에서 다시 만드는 파생물이다. sheet.export 의 인자와 같은 값을 저장하므로 저장한 정의로 낸 파일은 같은 인자로 낸 것과 바이트까지 같다.
def _sheet_def_check(self):
    """시트 정의가 스스로 맞아야 하는 것(커맨드와 가져오기가 함께 쓴다): 뷰는 한 번씩, 개정 구름·부위 상세는 올린 뷰만 가리킨다."""
    if len(set(self.views)) != len(self.views):
        raise ValueError("같은 뷰를 두 번 올릴 수 없다")
    on = set(self.views)
    if any(r.view and r.view not in on for r in self.revisions):
        raise ValueError("개정 구름을 둘 뷰가 시트에 없다")
    if any(c.view not in on for c in self.callouts):
        raise ValueError("부위 상세 표시를 둘 뷰가 시트에 없다")
    if self.key_zone is not None and self.key_plan is None:
        raise ValueError("key_zone 은 key_plan 과 함께 준다")
    return self


NOT_STORED = ("path", "overwrite", "profile", "detail", "dims", "max_px", "crop")   # 파일을 낼 때만 받는 입력(저장하는 시트 정의에는 넣지 않는다: 저장된 정의의 해시·형식은 그대로)
_DEF_FIELDS = {n: (f.annotation, f) for n, f in C.SheetExport.model_fields.items() if n not in NOT_STORED}
SheetDef = create_model(
    "SheetDef", __base__=A, __validators__={"_chk": model_validator(mode="after")(_sheet_def_check)},
    name=(Name, Field(description="시트 이름(시트끼리 겹치지 않는다)")), **_DEF_FIELDS)


class SheetDefine(SheetDef):
    id: Id | None = Field(None, description="시트 id. 이미 있는 시트 id 면 그 정의를 통째로 바꾸고, 생략하면 새로 만든다")


def _sheet_name_free(ctx, name, except_id=None):
    for s in ctx.st.by_kind.get("sheet", ()):
        if s != except_id and ctx.st.el[s]["params"]["name"] == name:
            raise DobimError("CONSTRAINT", f"같은 이름의 시트가 있다: {name}", path="/name")


def _check_sheet_views(ctx, params) -> None:
    for v in params["views"]:
        ctx.row(v, "view")
    if params["key_plan"] is not None:
        if ctx.row(params["key_plan"], "view")["params"]["type"] != "plan":
            raise DobimError("CONSTRAINT", "키 플랜은 평면 뷰여야 한다", path="/key_plan")


@command("sheet.define", kind="mutation", risk="write", group="view",
         summary="시트 정의(뷰 배치·표제란 인자)를 모델에 저장",
         args=SheetDefine, since="0.4",
         examples=[{"id": F.SH1, "name": "A-101 1층 평면", "views": [F.V1], "paper": "A3", "project_name": "예시 프로젝트", "sheet_number": "A-101", "date": "2026-01-01"}])
def sheet_define(ctx, a: SheetDefine):
    """시트를 저장한다. 저장한 정의는 모델과 함께 .dobim·IFC 로 왕복하고 병합·실행 취소를 따른다. 파일로 내는 것은 sheet.render 다."""
    sid = a.id
    if sid is not None and sid in ctx.st.el:
        r = ctx.row(sid, "sheet")
    else:
        r = None
        a.id = sid = C._fresh_id(ctx, a, "sheet")
    params = a.model_dump(mode="json", exclude={"id"})
    if params.get("north_angle") is None:   # 생략한 방위 각은 지금 대지의 정북 각(없으면 0)으로 정해 저장한다(저장 형식은 그대로 숫자)
        params["north_angle"] = C.site_north(ctx)
    _sheet_name_free(ctx, a.name, sid if r else None)
    _check_sheet_views(ctx, params)
    ctx.put_el(sid, C.row("sheet", "", "sheet.basic", params) if r is None else dict(r, params=params))
    return {"id": sid, "created": r is None}


@command("sheet.delete", kind="mutation", risk="write", group="view", summary="시트 정의를 지운다(뷰는 그대로)",
         args=C.IdArg, since="0.4", examples=[{"id": F.SH1}])
def sheet_delete(ctx, a: C.IdArg):
    ctx.row(a.id, "sheet")
    ctx.put_el(a.id, None)
    return {"deleted": [a.id]}


class SheetList(A):
    pass


def sheets_using(st, vid) -> list[str]:
    """뷰 vid 를 올린 시트 id(정렬)."""
    return sorted(s for s in st.by_kind.get("sheet", ()) if vid in st.el[s]["params"]["views"] or st.el[s]["params"]["key_plan"] == vid)


@command("sheet.list", kind="query", risk="read", group="view", summary="저장된 시트 정의 목록",
         args=SheetList, idempotent=True, examples=[{}])
def sheet_list(ctx, a: SheetList):
    st = ctx.st
    items = []
    for s in sorted(st.by_kind.get("sheet", ())):
        p = st.el[s]["params"]
        used = list(p["views"]) + ([p["key_plan"]] if p["key_plan"] else [])
        items.append({"id": s, "name": p["name"], "sheet_number": p["sheet_number"], "paper": p["paper"], "views": list(p["views"]),
                      "missing_views": sorted({v for v in used if v not in st.el or st.el[v]["kind"] != "view"})})
    return {"items": items, "count": len(items)}


class SheetRender(A):
    id: Id = Field(description="저장된 시트 정의의 id")
    path: str = Field(max_length=260, description="작업 폴더 안의 .svg·.pdf·.png·.dxf 경로(DXF 는 R2013 UTF-8, 용지 mm 1:1, 범주별 층)")
    overwrite: bool = Field(False, description="거짓이면 이미 있는 파일을 덮어쓰지 않는다. 참이면 덮어쓰며 덮어쓰기 전에 자동 스냅숏을 남긴다(확인 토큰 필요)")
    max_px: C.PngMaxPx = 1600
    crop: C.PngCrop | None = None


def _export_args(ctx, a: SheetRender):
    """저장된 정의 -> sheet.export 인자(같은 값이라 같은 파일이 나온다). 뷰가 사라졌으면 CONSTRAINT."""
    p = {k: v for k, v in ctx.row(a.id, "sheet")["params"].items() if k != "name" and v is not None}   # 없는 선택 값(null)은 생략으로 낸다(sheet.export 가 null 을 받지 않는다)
    missing = [v for v in [*p["views"], *([p["key_plan"]] if p.get("key_plan") else [])] if v not in ctx.st.el or ctx.st.el[v]["kind"] != "view"]
    if missing:
        raise DobimError("CONSTRAINT", f"시트가 올린 뷰가 없다: {', '.join(sorted(set(missing)))}", path="/id", hint="sheet.define 으로 시트를 고치세요")
    return C.SheetExport.model_validate({**p, "path": a.path, "overwrite": a.overwrite, "max_px": a.max_px, **({"crop": a.crop} if a.crop is not None else {})})


def _render_preview(ctx, a: SheetRender):
    return C._sheet_preview(ctx, _export_args(ctx, a))


@command("sheet.render", kind="session", risk="destructive", group="view",
         summary="저장된 시트 정의로 SVG·PDF·PNG·DXF 내보내기",
         args=SheetRender, since="0.4", preview=_render_preview,
         examples=[{"id": F.SH1, "path": "sheet-saved-example.svg", "overwrite": True}])
def sheet_render(ctx, a: SheetRender):
    """저장된 시트 정의를 sheet.export 와 같은 방식으로 파일에 낸다."""
    return C.sheet_export(ctx, _export_args(ctx, a))


# ================================================================ 이미지로 보기 (16단계 dobim_render): 파일을 쓰지 않는 읽기 커맨드
# view.image·sheet.image 는 PNG 를 만들어 결과의 비공개 키(_png)에 싣는다. dispatch 가 그 키를 봉투 밖으로 빼므로 봉투에는 크기·해시 같은 요약만 남는다
# (CLI·dobim_query 는 요약만, MCP dobim_render 는 같은 PNG 를 ImageContent 로 돌려준다). 파일로 받으려면 view.export·sheet.export·sheet.render 에 .png 경로를 준다.
class ViewImage(A):
    id: Id = Field(description="이미지로 볼 뷰 id")
    max_px: C.PngMaxPx = 1600
    crop: C.PngCrop | None = None


def _image_result(ctx, rr, extra: dict) -> dict:
    import hashlib
    png = rr["png"]
    return {**extra, "format": "png", "width": rr["width"], "height": rr["height"], "bytes": len(png), "sha256": hashlib.sha256(png).hexdigest(),
            "px_per_mm": round(rr["px_per_mm"], 4), "region_mm": [round(v, 2) for v in rr["region_mm"]], "font": rr["font"], "_png": png}


@command("view.image", kind="query", risk="read", group="view",
         summary="뷰를 PNG 이미지로 본다(파일 안 씀)",
         args=ViewImage, idempotent=True, examples=[{"id": F.V1, "max_px": 800}])
def view_image(ctx, a: ViewImage):
    """뷰 한 장을 PNG 로 그린다. 같은 입력이면 같은 바이트다. 응답 봉투에는 크기·해시만 담고 이미지는 MCP dobim_render 가 돌려준다."""
    from dobim_core import sheet as SH
    r = ctx.row(a.id, "view")
    rr = C.png_of(ctx, SH.build_view_page(C._render_view(ctx, a.id)), a)
    return _image_result(ctx, rr, {"id": a.id, "name": r["params"]["name"], "type": r["params"]["type"]})


_IMG_SKIP = {"path", "overwrite", "views"}
_IMG_FIELDS = {n: (f.annotation, f) for n, f in C.SheetExport.model_fields.items() if n not in _IMG_SKIP}
SheetImageBase = create_model(
    "SheetImageBase", __base__=A,
    id=(Id | None, Field(None, description="저장된 시트 정의의 id(sheet.define). 주면 sheet.export 인자(views 등)는 쓰지 않는다")),
    views=(list[Id] | None, Field(None, min_length=1, max_length=12, description="id 없이 시트를 바로 그릴 때 올릴 뷰 id 목록(sheet.export 와 같은 인자)")),
    **_IMG_FIELDS)


class SheetImage(SheetImageBase):
    @model_validator(mode="after")
    def _one_source(self):
        if (self.id is None) == (self.views is None):
            raise ValueError("id(저장된 시트) 또는 views(sheet.export 인자) 중 하나만 준다")
        return self


@command("sheet.image", kind="query", risk="read", group="view",
         summary="시트를 PNG 이미지로 본다(파일 안 씀)",
         args=SheetImage, idempotent=True, examples=[{"views": [F.V1], "paper": "A3", "project_name": "예시 프로젝트", "sheet_number": "A-101", "max_px": 800}])
def sheet_image(ctx, a: SheetImage):
    """시트를 PNG 로 그린다. id 를 주면 저장된 정의(sheet.render 와 같은 값), 아니면 sheet.export 와 같은 인자로 그린다. 큰 시트도 max_px 안으로 줄인다."""
    if a.id is not None:
        exp = _export_args(ctx, SheetRender(id=a.id, path="image.png"))
        exp = C.SheetExport.model_validate({**exp.model_dump(mode="json", exclude={"max_px", "crop"}, exclude_none=True), "max_px": a.max_px, **({"crop": a.crop} if a.crop is not None else {})})
    else:
        exp = C.SheetExport.model_validate({**a.model_dump(mode="json", exclude={"id"}, exclude_none=True), "path": "image.png"})
    for v in exp.views:
        ctx.row(v, "view")
    sh, _spec = C.build_sheet_for(ctx, exp)
    rr = C.png_of(ctx, sh, exp)
    return _image_result(ctx, rr, {"id": a.id, "paper": exp.paper, "sheet_number": exp.sheet_number, "views": sh.placed})
