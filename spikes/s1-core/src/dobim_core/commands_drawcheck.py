# SPDX-License-Identifier: MPL-2.0
"""건축법 도면 기준 대조(W1 1-E, ANA-05): drawing.check.

저장된 시트 정의(sheet.define)와 그 시트에 올린 뷰, 모델을 읽어 법정 설계도서 기준 가운데 기계로 판정할 수 있는 항목만 본다(읽기 전용).
근거는 국가법령정보센터(law.go.kr)에서 받은 별표 원문이다(2026-11-20 확인, 파일에 적힌 개정일).
  permit : 건축법 시행규칙 [별표 2] 건축허가신청에 필요한 설계도서(제6조제1항 관련) <개정 2021. 6. 25.>
  start  : 건축법 시행규칙 [별표 4의2] 착공신고에 필요한 설계도서(제14조제1항 관련) <개정 2021. 8. 27.> 중 1. 건축 분야
원문에서 확인한 것만 위반(fail)으로 세운다. 별표 2 는 모든 도서의 축척을 "임의"로 두므로 축척 값 자체는 위반으로 보지 않는다.
표제란 칸·도면번호 형식은 법령 원문에서 근거 조항을 찾지 못했다(확인 필요): 이 엔진의 도면 작성기준(drawing.profile)으로만 보고 경고(warn)까지만 낸다.
도면이 아닌 도서(건축계획서·구조계산서·시방서)와 조건부 도서(구조도·소방설비도 등), 색인 시트(sheet.index, 저장된 시트가 아님)로 내는 표는
판정하지 않고 KR-DRW-13 에 이름만 알린다.
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

import re
from math import atan2, pi
from typing import Literal

from pydantic import Field

from dobim_core import limits
from dobim_core.commands import A, Id, command
from dobim_core.errors import DobimError

SRC = {"별표2": "건축법 시행규칙 [별표 2] 건축허가신청에 필요한 설계도서(제6조제1항 관련) <개정 2021. 6. 25.>",
       "별표4의2": "건축법 시행규칙 [별표 4의2] 착공신고에 필요한 설계도서(제14조제1항 관련) <개정 2021. 8. 27.> 1. 건축",
       "엔진": "법령 근거 확인 필요: 이 엔진의 도면 작성기준(drawing.profile)"}
PROFILE_LABEL = {"permit": "건축허가 설계도서(건축법 시행규칙 별표 2)", "start": "착공신고 설계도서 건축 분야(건축법 시행규칙 별표 4의2)"}

# id -> (제목, {프로파일: 근거 조항}, 법령 근거 여부). 근거가 엔진 기준뿐인 규칙은 fail 대신 warn 까지만 낸다.
RULES = {
    "KR-DRW-01": ("배치도가 시트에 있음", {"permit": "별표2 배치도", "start": "별표4의2 1.바 배치도"}, True),
    "KR-DRW-02": ("배치도 축척 및 방위 표시", {"permit": "별표2 배치도 1(축척 및 방위)", "start": "별표4의2 1.바(축척 및 방위)"}, True),
    "KR-DRW-03": ("1층 평면도가 시트에 있음", {"permit": "별표2 평면도 1(1층 및 기준층 평면도)"}, True),
    "KR-DRW-04": ("입면도 2면 이상", {"permit": "별표2 입면도 1(2면 이상의 입면계획)", "start": "별표4의2 1.자 입면도(2면 이상)"}, True),
    "KR-DRW-05": ("종·횡단면도", {"permit": "별표2 단면도 1(종·횡단면도)", "start": "별표4의2 1.차 단면도(종ㆍ횡단면도)"}, True),
    "KR-DRW-06": ("각층 평면도가 시트에 있음", {"start": "별표4의2 1.아 각층 및 지붕평면도"}, True),
    "KR-DRW-07": ("지붕평면도가 시트에 있음", {"start": "별표4의2 1.아 각층 및 지붕평면도"}, True),
    "KR-DRW-08": ("외벽 마감재료 정보", {"permit": "별표2 입면도 2(외부마감재료)", "start": "별표4의2 1.자 1)(외벽마감재료)"}, True),
    "KR-DRW-09": ("단면도 높이·각층 높이·반자높이 치수", {"permit": "별표2 단면도 2(건축물의 높이, 각층의 높이 및 반자높이)", "start": "별표4의2 1.차 1)(반자높이)"}, True),
    "KR-DRW-10": ("표제란 필수 칸", {"permit": "엔진 표제란", "start": "엔진 표제란"}, False),
    "KR-DRW-11": ("도면번호 형식·중복", {"permit": "엔진 도면번호", "start": "엔진 도면번호"}, False),
    "KR-DRW-12": ("뷰 축척 표기", {"permit": "별표2 도서의 축척(임의)", "start": "엔진 축척"}, False),
    "KR-DRW-13": ("기계 판정하지 않는 도서", {"permit": "별표2", "start": "별표4의2 1. 건축"}, False),
}
# KR-DRW-13 이 이름만 알리는 도서(원문 순서). 판정하지 않는 이유가 서로 달라 묶음별로 적는다.
NOT_JUDGED = {
    "permit": [("도면이 아닌 도서", ["건축계획서", "구조계산서"]),
               ("조건부 도서(대상 건축물만)", ["구조도", "소방설비도"]),
               ("내용 판정 안 함", ["배치도 2~6(도로·대지 종횡단·이격거리·주차·공개공지 조경)", "평면도 2~5(기둥·벽·창, 방화구획·방화문, 복도·계단, 승강기 위치)",
                                "입면도 3(간판·건물번호판)", "기준층 평면도(어느 층이 기준층인지는 설계자 판단)"])],
    "start": [("색인 시트(sheet.index)로 내는 표", ["도면목록표", "마감재료표", "창호도(창호일람표)"]),
              ("도면이 아닌 도서", ["개요서", "구적도", "시방서"]),
              ("내용 판정 안 함", ["안내도", "주차계획도", "수직동선상세도", "부분상세도", "건축설비도", "방화구획상세도", "외벽 마감재료 단면상세도"])],
}
SCALE_SERIES = (1, 2, 5, 10, 20, 50, 100, 200, 300, 500, 600, 1000, 1200)   # 흔히 쓰는 축척(벗어나면 info 로 알리기만 한다: 별표 2 는 축척 임의)
NUMBER_RE = re.compile(r"^[A-Z]{1,3}-\d{3}$")   # 도면번호: 공종 문자-3자리 일련번호(drawing.profile title_block.drawing_number)
MAX_EX = 5
TEXT_CHARS = 160
SHOWN_EXAMPLES = 3

Profile = Literal["permit", "start"]


class DrawingCheck(A):
    profile: Profile = Field("permit", description="permit=건축허가(별표 2), start=착공신고 건축 분야(별표 4의2)")
    sheets: list[Id] | None = Field(None, min_length=1, max_length=200, description="볼 시트 id(생략하면 저장된 시트 전부)")
    detail: bool = Field(False, description="참이면 규칙마다 제목·근거 조항·노트·사례(앞 3건)")
    limit: int = Field(100, ge=1, le=200, description="최대 개수(응답 크기 상한에 걸리면 page.truncated)")
    cursor: str | None = Field(None, max_length=64, description="이전 응답의 page.next_cursor")


def _res(status, applicable=0, failed=0, examples=(), note=""):
    return {"status": status, "applicable": applicable, "failed": failed, "examples": list(examples)[:MAX_EX], "note": note}


def _ex(eid, reason):
    return {"id": eid, "reason": reason}


# ---------------------------------------------------------------- 모델 읽기
class _Data:
    """판정에 쓰는 값을 한 번만 읽어 둔다: 볼 시트, 시트에 올린 뷰(종류별), 레벨, 지붕."""

    def __init__(self, ctx, sheet_ids):
        st = ctx.st
        self.ctx, self.st = ctx, st
        self.sheets = sheet_ids
        on = []
        for s in sheet_ids:
            for v in st.el[s]["params"]["views"]:
                if v in st.el and st.el[v]["kind"] == "view" and v not in on:
                    on.append(v)
        self.placed = on                                       # 시트에 올린 뷰(시트 순서 -> 뷰 순서, 한 번씩)
        self.vp = {v: st.el[v]["params"] for v in on}
        self.levels = sorted(((lv, st.el[lv]["params"]["name"], float(st.el[lv]["params"]["elevation"])) for lv in st.by_kind.get("level", ())),
                             key=lambda t: (t[2], t[1], t[0]))

    def of_type(self, t):
        return [v for v in self.placed if self.vp[v]["type"] == t]

    def floor_plans(self):
        """시트에 올린 바닥 평면(반사 천장·면적 평면 제외)의 레벨 -> 뷰 목록."""
        out = {}
        for v in self.of_type("plan"):
            p = self.vp[v]
            if p.get("rcp") or p.get("area"):
                continue
            out.setdefault(p["level"], []).append(v)
        return out


# ---------------------------------------------------------------- 필수 도면
def _site_present(d):
    vs = d.of_type("site")
    if vs:
        return _res("pass", 1, 0, note=f"배치도 뷰 {len(vs)}개")
    return _res("fail", 1, 1, [_ex(None, "시트에 올린 배치도(site) 뷰가 없음")], "view.create type=site 로 배치도를 만들어 시트에 올리세요")


def _site_scale_north(d):
    vs = d.of_type("site")
    if not vs:
        return _res("na", note="배치도 뷰 없음(KR-DRW-01)")
    bad = [_ex(v, "축척 값이 없음") for v in vs if not isinstance(d.vp[v].get("scale"), (int, float)) or d.vp[v]["scale"] <= 0]
    note = "축척: 뷰 아래 S=1:N 표기와 축척 막대, 방위: 시트 표제란 아래 방위표(시트의 north_angle)를 이 엔진이 늘 그린다"
    return _res("fail" if bad else "pass", len(vs), len(bad), bad, note)


def _ground_level(d):
    """1층: 높이 0 이상인 레벨 중 가장 낮은 레벨(지하층 제외). 없으면 가장 낮은 레벨."""
    up = [t for t in d.levels if t[2] >= 0]
    return (up or d.levels or [None])[0]


def _first_floor_plan(d):
    g = _ground_level(d)
    if g is None:
        return _res("na", note="레벨 없음")
    plans = d.floor_plans()
    if g[0] in plans:
        return _res("pass", 1, 0, note=f"1층({g[1]}) 평면 뷰 {len(plans[g[0]])}개. 기준층은 판정 안 함(KR-DRW-13)")
    return _res("fail", 1, 1, [_ex(g[0], f"1층 레벨 '{g[1]}' 의 평면도가 시트에 없음")], "1층 = 높이 0 이상인 레벨 중 가장 낮은 레벨")


def _elevations(d):
    vs = d.of_type("elevation")
    dirs = sorted({d.vp[v]["dir"] for v in vs})
    if len(dirs) >= 2:
        return _res("pass", 1, 0, note=f"입면 방향 {', '.join(dirs)}")
    return _res("fail", 1, 1, [_ex(None, f"서로 다른 방향의 입면도가 {len(dirs)}면뿐(2면 이상 필요)")], "같은 방향 입면은 한 면으로 센다")


def _sections(d):
    vs = d.of_type("section")
    angs = []
    for v in vs:
        (x1, y1), (x2, y2) = d.vp[v]["p1"], d.vp[v]["p2"]
        angs.append(atan2(y2 - y1, x2 - x1) % pi)
    cross = any(min(abs(a - b), pi - abs(a - b)) >= pi / 4 for i, a in enumerate(angs) for b in angs[i + 1:])
    if cross:
        return _res("pass", 1, 0, note=f"단면 뷰 {len(vs)}개, 절단선 방향이 45도 이상 다른 쌍 있음")
    why = "단면도가 없음" if not vs else "절단선 방향이 모두 같은 쪽(45도 미만 차이)이라 종·횡 한 벌이 되지 않음"
    return _res("fail", 1, 1, [_ex(None, why)], "종·횡 = 절단선 방향이 45도 이상 다른 단면 두 장")


def _every_floor_plan(d):
    if not d.levels:
        return _res("na", note="레벨 없음")
    plans = d.floor_plans()
    miss = [_ex(lv, f"레벨 '{n}' 의 평면도가 시트에 없음") for lv, n, _ in d.levels if lv not in plans]
    return _res("fail" if miss else "pass", len(d.levels), len(miss), miss, "반사 천장·면적 평면은 바닥 평면으로 세지 않음")


def _roof_plan(d):
    if not d.levels:
        return _res("na", note="레벨 없음")
    roofs = sorted(d.st.by_kind.get("roof", ()))
    elev = {lv: e for lv, _, e in d.levels}
    if roofs:   # 지붕 요소가 놓인 가장 높은 레벨 이상
        top = max(elev.get(next(iter(d.ctx.rels_out("on_level", r)), None), float("-inf")) for r in roofs)
        how = "지붕 요소가 놓인 레벨 이상"
    else:       # 지붕 요소가 없으면(평지붕을 슬래브로 만든 경우 등) 가장 높은 레벨을 지붕 층으로 본다
        top = d.levels[-1][2]
        how = "지붕 요소가 없어 가장 높은 레벨"
    if any(elev.get(lv, float("-inf")) >= top for lv in d.floor_plans()):
        return _res("pass", 1, 0, note=f"{how}의 평면이 시트에 있음")
    return _res("fail", 1, 1, [_ex(None, f"{how}인 레벨의 평면도가 시트에 없음")], "지붕평면 = " + how + " 레벨의 바닥 평면")


# ---------------------------------------------------------------- 모델 정보(도면에 적을 근거)
def _exterior_finish(d):
    from dobim_core import sheet_model as SM
    ext = [w for w in SM.walls(d.ctx.g) if w["ext"]]
    if not ext:
        return _res("na", note="외벽(IsExternal 참) 없음")
    bad = []
    if not any(d.vp[v].get("finish") for v in d.of_type("elevation")):
        bad.append(_ex(None, "시트에 올린 입면도 중 외부 마감 표기(view.create finish=true)를 켠 것이 없음"))
    bad += [_ex(w["id"], "외벽에 재료가 없음") for w in ext if not w["mat"]]
    return _res("fail" if bad else "pass", len(ext) + 1, len(bad), bad,
                "입면도의 외벽 마감재료 해치·문자는 finish 를 켠 입면에서 외벽(IsExternal 참) 재료로 그린다")


def _dim_kind(a: str, b: str):
    """높이 치수 하나의 종류: 한쪽이 레벨이고 다른 쪽이 레벨이면 층고(floor), 천장·슬래브 밑면이면 반자(clear), 윗면이면 높이(overall)."""
    for x, y in ((a, b), (b, a)):
        if x.startswith("level:"):
            if y.startswith("level:"):
                return "floor"
            if y.endswith("#ceiling") or (y.startswith("slab:") and y.endswith("#bottom")):
                return "clear"
            if y.endswith("#top"):
                return "overall"
    return None


HEIGHT_LABEL = {"overall": "건축물의 높이", "floor": "각층의 높이(층고)", "clear": "반자높이"}


def _section_heights(d):
    vs = d.of_type("section")
    if not vs:
        return _res("na", note="단면도 없음(KR-DRW-05)")
    st = d.st
    have = set()
    for v in vs:
        for an in d.ctx.rels_in("annot_of", v):
            r = st.el[an]
            if r["kind"] == "dim":
                k = _dim_kind(r["params"]["a"], r["params"]["b"])
                if k:
                    have.add(k)
    need = ["overall", "clear"] + (["floor"] if len({e for _, _, e in d.levels}) >= 2 else [])
    miss = [k for k in ("overall", "floor", "clear") if k in need and k not in have]
    if not miss:
        return _res("pass", len(need), 0, note=f"시트에 올린 단면 {len(vs)}개의 높이 치수에서 찾음")
    return _res("fail", len(need), len(miss), [_ex(None, f"단면도에 {HEIGHT_LABEL[k]} 치수가 없음") for k in miss],
                "annot.dim.heights 로 단면에 층고·반자높이·최고 높이 치수를 자동 배치할 수 있음(레벨에서 잰 높이 치수를 종류별로 셈)")


# ---------------------------------------------------------------- 표제란·도면번호·축척(엔진 기준)
def _title_block(d):
    if not d.sheets:
        return _res("na", note="볼 시트 없음")
    bad = []
    for s in d.sheets:
        p = d.st.el[s]["params"]
        miss = [lab for k, lab in (("project_name", "프로젝트명"), ("office", "설계사무소")) if not (p.get(k) or "").strip()]
        if not p.get("date"):
            miss.append("작성일(없으면 낼 때마다 오늘 날짜)")
        if miss:
            bad.append(_ex(s, f"시트 '{p['name']}': {', '.join(miss)} 없음"))
    note = "엔진 표제란 칸 중 비우면 채워 넣지 않는 것만 봄(도면명은 첫 뷰 이름, 도면번호는 늘 있음). 서명·날인 칸 근거는 확인 필요"
    return _res("warn" if bad else "pass", len(d.sheets), len(bad), bad, note)


def _numbers(d):
    if not d.sheets:
        return _res("na", note="볼 시트 없음")
    seen, bad = {}, []
    for s in d.st.by_kind.get("sheet", ()):
        seen.setdefault(d.st.el[s]["params"]["sheet_number"], []).append(s)
    for s in d.sheets:
        n = d.st.el[s]["params"]["sheet_number"]
        if not NUMBER_RE.match(n):
            bad.append(_ex(s, f"도면번호 '{n}' 이 공종 문자-3자리 일련번호(예: A-101) 형식이 아님"))
        elif len(seen[n]) > 1:
            bad.append(_ex(s, f"도면번호 '{n}' 를 시트 {len(seen[n])}장이 같이 씀"))
    return _res("warn" if bad else "pass", len(d.sheets), len(bad), bad, "중복은 저장된 시트 전체에서 셈")


def _scales(d):
    vs = [v for v in d.placed if d.vp[v]["type"] != "legend"]
    if not vs:
        return _res("na", note="시트에 올린 뷰 없음")
    odd = [_ex(v, f"뷰 '{d.vp[v]['name']}' 축척 1:{d.vp[v]['scale']:g} 는 흔히 쓰는 축척이 아님") for v in vs if d.vp[v]["scale"] not in SCALE_SERIES]
    note = "별표 2 는 도서의 축척을 '임의'로 둠: 뷰마다 S=1:N 표기가 있는지만 판정하고 드문 축척은 알리기만 함"
    return _res("info" if odd else "pass", len(vs), len(odd), odd, note)


def _not_judged(profile):
    def run(d):
        parts = [f"{k}: {', '.join(v)}" for k, v in NOT_JUDGED[profile]]
        return _res("info", 0, 0, note="; ".join(parts))
    return run


CHECKS = {"KR-DRW-01": _site_present, "KR-DRW-02": _site_scale_north, "KR-DRW-03": _first_floor_plan, "KR-DRW-04": _elevations,
          "KR-DRW-05": _sections, "KR-DRW-06": _every_floor_plan, "KR-DRW-07": _roof_plan, "KR-DRW-08": _exterior_finish,
          "KR-DRW-09": _section_heights, "KR-DRW-10": _title_block, "KR-DRW-11": _numbers, "KR-DRW-12": _scales}


def run_checks(ctx, profile: str, sheet_ids: list[str]) -> list[dict]:
    """프로파일에 속한 규칙을 순서대로 돌린 결과 행 전부(제목·근거·노트·사례 포함)."""
    d = _Data(ctx, sheet_ids)
    out = []
    for rid, (title, basis, legal) in RULES.items():
        if profile not in basis:
            continue
        fn = CHECKS.get(rid) or _not_judged(profile)
        r = fn(d)
        if not legal and r["status"] == "fail":   # 법령 근거가 확인되지 않은 규칙은 위반으로 세우지 않는다
            r["status"] = "warn"
        b = basis[profile]
        src = SRC["엔진"] if b.startswith("엔진") else SRC["별표2"] if b.startswith("별표2") else SRC["별표4의2"]
        out.append({"id": rid, "title": title, "basis": b, "source": src, "legal": legal, **r})
    return out


def _clip(s):
    return limits.clip_chars(s, TEXT_CHARS) if isinstance(s, str) else s


def _row(c: dict, detail: bool) -> dict:
    r = {"id": c["id"], "status": c["status"], "applicable": c["applicable"], "failed": c["failed"]}
    if detail:
        r.update(title=c["title"], basis=c["basis"], legal=c["legal"], note=_clip(c["note"]),
                 examples=[{k: _clip(v) for k, v in ex.items() if v is not None} for ex in c["examples"][:SHOWN_EXAMPLES]])
    return r


@command("drawing.check", kind="query", risk="read", group="view",
         summary="건축법 도면 기준 대조(별표 2 허가·별표 4의2 착공): 필수 도면·축척·방위·표제란. 위반이면 CLI 종료 코드 3",
         args=DrawingCheck, since="0.12", idempotent=True, examples=[{}, {"profile": "start", "detail": True, "limit": 3}])
def drawing_check(ctx, a: DrawingCheck):
    st = ctx.st
    if a.sheets is not None:
        for i, s in enumerate(a.sheets):
            if s not in st.el or st.el[s]["kind"] != "sheet":
                raise DobimError("NOT_FOUND", f"시트가 없다: {s}", path=f"/sheets/{i}", hint="sheet.list 로 시트 id 를 확인하세요")
        sids = list(dict.fromkeys(a.sheets))
    else:
        sids = sorted(st.by_kind.get("sheet", ()))
    rows = run_checks(ctx, a.profile, sids)
    summary = {}
    for c in rows:
        summary[c["status"]] = summary.get(c["status"], 0) + 1
    base = {"profile": a.profile, "profile_label": PROFILE_LABEL[a.profile], "sheets": len(sids), "rules": len(rows), "summary": summary,
            "violations": [c["id"] for c in rows if c["status"] == "fail"], "warnings": [c["id"] for c in rows if c["status"] == "warn"],
            "sources": sorted({c["source"] for c in rows})}
    shown = [_row(c, a.detail) for c in rows]
    start = limits.dec_cursor(a.cursor, "int") or 0
    if start > len(shown):
        raise DobimError("SCHEMA_INVALID", "커서가 목록 범위를 벗어났다", path="/cursor")
    chunk = shown[start:start + a.limit]
    items, cut = limits.fit(chunk, base, cursor_of=lambda i: limits.enc_cursor(start + i + 1))
    nxt = start + len(items)
    return {**base, "items": items, "page": limits.page(limits.enc_cursor(nxt) if nxt < len(shown) else None, len(shown), cut)}

