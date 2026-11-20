# SPDX-License-Identifier: MPL-2.0
"""법정 면적 체계(W1 연장, ARC-36·ANA-04): Revit 의 면적 계획(Area Scheme·Area Plan·Area)에 해당하는 한국 건축법 면적 산정.

area.define : 법정 면적 객체(larea)를 만들거나 바꾼다. 건축법 시행령 119조의 산입·불산입 부분을 근거 코드와 함께 다각형으로 그린다.
area.delete : 법정 면적 객체를 지운다.
area.list   : 법정 면적 객체 목록(면적·산입 면적).
area.legal  : 법정 면적표: 대지면적·층별 바닥면적·건축면적·연면적·용적률 산정용 연면적·건폐율·용적률(한도를 주면 적합 여부).
자동 판정은 벽 중심선 영역(바닥면적·건축면적)과 지하층(법 2조1항5호, 지표면 수평 가정)뿐이다. 나머지 산입·불산입은 면적 객체로 사용자가 넣고,
판정하지 못하는 것은 응답의 unverified(확인 필요)로 알린다. 근거 원문은 legal_area 모듈 머리말. 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core import legal_area as LA
from dobim_core.commands import A, Id, Name, SlabBoundary, _fresh_id, command, row
from dobim_core.errors import DobimError

LAREAS_MAX = 2000      # 법정 면적 객체 수 상한
PARKING_WORDS = ("주차",)   # 용적률 제외(4호나목) 후보로 알릴 공간 분류 낱말(제외 여부는 판정하지 않는다)


def _m2(mm2: float) -> float:
    return round(mm2 / 1.0e6, 2)


class AreaDefine(A):
    id: Id | None = Field(None, description="있는 법정 면적 객체 id 면 통째로 바꾼다. 생략하면 새로 만든다")
    name: Name
    rule: Literal[LA.RULE_CODES] = Field(description="근거 코드 = 건축법 시행령 119조 항-호-목(예: 119-1-3-나 노대). manual = 근거를 note 에 적는 사용자 조정")
    target: Literal["site", "building", "floor"] | None = Field(None, description="대지면적·건축면적·바닥면적. 근거 코드가 정하므로 manual 에만 준다")
    effect: Literal["add", "exclude", "far"] | None = Field(None, description="manual 에만: add 더함, exclude 뺌, far 용적률 산정에서만 뺌(바닥면적)")
    level: Id | None = Field(None, description="바닥면적 대상의 레벨 id(바닥면적일 때 필수)")
    boundary: SlabBoundary
    contact_length: float | None = Field(None, gt=0, le=1e6, description="노대(119-1-3-나)에만: 노대가 접한 가장 긴 외벽에 접한 길이 mm")
    note: Annotated[str, Field(max_length=LA.NOTE_MAX)] = Field("", description="근거·비고(manual 은 필수)")


@command("area.define", kind="mutation", risk="write", group="model.area",
         summary="법정 면적 객체(건축법 시행령 119조 산입·불산입 부분)를 근거 코드와 함께 만들거나 바꾼다",
         args=AreaDefine, since="0.14",
         examples=[{"id": F.LA1, "name": "발코니", "rule": "119-1-3-나", "level": F.LV1,
                    "boundary": [[0, 4000], [6000, 4000], [6000, 5500], [0, 5500]], "contact_length": 6000},
                   {"name": "옥외 주차", "rule": "manual", "target": "floor", "effect": "far", "level": F.LV1,
                    "boundary": [[0, 0], [2500, 0], [2500, 5000], [0, 5000]], "note": "4호나목 부속용도 주차(확인 필요)"}])
def area_define(ctx, a: AreaDefine):
    if a.rule == LA.MANUAL:
        if a.target is None or a.effect is None:
            raise DobimError("CONSTRAINT", "manual 은 target·effect 를 준다", path="/target")
        if not a.note.strip():
            raise DobimError("CONSTRAINT", "manual 은 근거를 note 에 적는다", path="/note")
        target, effect = a.target, a.effect
    else:
        target, effect, _ = LA.RULES[a.rule]
        if (a.target is not None and a.target != target) or a.effect is not None:
            raise DobimError("CONSTRAINT", f"근거 {a.rule} 은 대상 {target}·효과 {effect} 로 정해져 있다(target·effect 는 manual 에만)", path="/rule")
    if not LA.rule_fits(a.rule, target, effect):
        raise DobimError("CONSTRAINT", "용적률 산정 제외(far)는 바닥면적(floor) 대상에만 쓴다", path="/effect")
    if (a.level is not None) != (target == "floor"):
        raise DobimError("CONSTRAINT", "level 은 바닥면적(floor) 대상에만, 반드시 준다", path="/level")
    if (a.contact_length is not None) != (effect == "balcony"):
        raise DobimError("CONSTRAINT", "contact_length 는 노대(119-1-3-나)에만, 반드시 준다", path="/contact_length")
    if a.level is not None:
        ctx.authored(a.level, "level", what="레벨에 법정 면적 객체를 두는 것")
    params = {"name": a.name, "target": target, "rule": a.rule, "effect": effect, "boundary": C._slab_boundary(a.boundary), "note": a.note}
    if a.level is not None:
        params["level"] = a.level
    if a.contact_length is not None:
        params["contact_length"] = float(a.contact_length)
    if a.id is not None and a.id in ctx.st.el:
        if ctx.st.el[a.id]["kind"] != "larea":
            raise DobimError("CONSTRAINT", f"이미 있는 id 다(법정 면적 객체가 아니다): {a.id}", path="/id")
        ctx.put_el(a.id, dict(ctx.row(a.id), params=params))
        created = False
    else:
        if len(ctx.st.by_kind.get("larea", ())) >= LAREAS_MAX:
            raise DobimError("CONSTRAINT", f"법정 면적 객체는 {LAREAS_MAX}개까지다", path="/name")
        _fresh_id(ctx, a, "larea")
        ctx.put_el(a.id, row("larea", "", "area.legal", params))
        created = True
    return {"id": a.id, "created": created, "target": target, "effect": effect, **_counted(params)}


def _counted(p: dict) -> dict:
    """객체 하나의 면적과 산입 면적(㎡). 노대는 면적 - 접한 길이 x 1.5m, 빼는 것은 음수로."""
    ar = abs(LA.area2(p["boundary"])) / 2.0
    if p["effect"] == "balcony":
        c = LA.balcony_counted(ar, p["contact_length"])
    else:
        c = -ar if p["effect"] in ("exclude", "far") else ar
    return {"area_m2": _m2(ar), "counted_m2": _m2(c)}


class AreaDelete(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=200, description="지울 법정 면적 객체 id 목록")]


@command("area.delete", kind="mutation", risk="write", group="model.area", summary="법정 면적 객체를 지운다",
         args=AreaDelete, since="0.14", examples=[{"ids": [F.LA1]}])
def area_delete(ctx, a: AreaDelete):
    ids = list(dict.fromkeys(a.ids))
    for i in ids:
        ctx.row(i, "larea")
    for i in ids:
        ctx.put_el(i, None)
    return {"deleted": ids}


class AreaList(A):
    target: Literal["site", "building", "floor"] | None = Field(None, description="이 대상만")
    level: Id | None = Field(None, description="이 레벨의 바닥면적 객체만")
    limit: int = Field(100, ge=1, le=LAREAS_MAX, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("area.list", kind="query", risk="read", group="model.area", summary="법정 면적 객체 목록(근거·면적·산입 면적)",
         args=AreaList, idempotent=True, since="0.14", examples=[{}, {"target": "floor", "limit": 5}])
def area_list(ctx, a: AreaList):
    ids = [i for i in sorted(ctx.st.by_kind.get("larea", ()))
           if (a.target is None or ctx.st.el[i]["params"]["target"] == a.target) and (a.level is None or ctx.st.el[i]["params"].get("level") == a.level)]
    after = C._dec_cursor(a.cursor, "id")
    start = 0 if after is None else next((k for k, i in enumerate(ids) if i > after), len(ids))
    rows = []
    for i in ids[start:start + a.limit]:
        p = ctx.st.el[i]["params"]
        rows.append({"id": i, "name": p["name"], "target": p["target"], "rule": p["rule"], "effect": p["effect"], "level": p.get("level"),
                     "note": p["note"], **_counted(p)})
    items, cut = C._fit(rows, cursor_of=lambda k: C._enc_cursor(rows[k]["id"]))
    more = start + len(items) < len(ids)
    return {"items": items, "page": C._page(C._enc_cursor(items[-1]["id"]) if more else None, len(ids), cut)}


# ---------------------------------------------------------------- 법정 면적표
def _wall_polys(ctx, lv: str) -> list:
    """레벨의 저작 벽 중심선으로 닫힌 영역들(반시계 다각형). space.from_walls 와 같은 면 찾기(막다른 벽은 걷어낸다)."""
    from dobim_core import regions as RG
    walls = []
    for w in sorted(ctx.rels_in("on_level", lv)):
        r = ctx.st.el[w]
        if r["kind"] == "wall" and r["origin"] == "authored":
            x1, y1, x2, y2 = r["params"]["axis"]
            walls.append((w, (x1, y1), (x2, y2), r["params"]["thickness"]))
    return [tuple(f["center"]) for f in RG.find_faces(walls)] if walls else []


def _union_mm2(polys: list) -> float:
    """반시계 다각형들의 합집합 면적(mm2). 면적 평면(gross)과 같은 합집합 바깥선 계산을 쓴다."""
    if not polys:
        return 0.0
    from dobim_core import drawing as D
    from dobim_core import viewplans as VP
    segs = VP._union_directed(D.outline_of(tuple(polys)))
    return max(0.0, sum(x0 * y1 - x1 * y0 for x0, y0, x1, y1 in segs) / 2.0)


class AreaLegal(A):
    site: Id | None = Field(None, description="대지 id. 생략하면 저작 대지가 하나일 때 그것")
    site_area_m2: float | None = Field(None, gt=0, le=1e8, description="대지면적(제외 전) ㎡ 를 직접 준다(대지 경계 대신)")
    ground: float = Field(0.0, ge=-1e6, le=1e6, description="지표면 표고 mm(레벨 표고와 같은 기준, 수평 가정). 지하층·건축면적 판정에 쓴다")
    basement_levels: Annotated[list[Id], Field(max_length=200)] | None = Field(None, description="지하층으로 볼 레벨 id 목록. 주면 자동 판정 대신 이것을 쓴다")
    coverage_max: float | None = Field(None, gt=0, le=100, description="건폐율 한도 %(국토계획법·조례 값, 사용자가 준다)")
    far_max: float | None = Field(None, gt=0, le=10000, description="용적률 한도 %")
    cursor: str | None = Field(None, max_length=2048, description="층 표(items)의 다음 쪽: 이전 응답의 page.next_cursor")


LEVEL_COLUMNS = ["id", "name", "elevation", "height", "basement", "building", "walls_m2", "adjust_m2", "floor_m2", "far_m2"]


@command("area.legal", kind="query", risk="read", group="model.area",
         summary="법정 면적표: 대지면적·층별 바닥면적·건축면적·연면적·용적률 산정용 연면적·건폐율·용적률(건축법 시행령 119조)",
         args=AreaLegal, idempotent=True, since="0.14", examples=[{"site": F.SI1}, {"site_area_m2": 300, "coverage_max": 60, "far_max": 200}])
def area_legal(ctx, a: AreaLegal):
    """층 표(items)는 LEVEL_COLUMNS 순서의 행. basement = 지하층(null = 판정 못 함), building = 건축면적에 넣은 층.
    walls_m2 = 벽 중심선 영역, adjust_m2 = 바닥면적 객체 합(더함·노대 산입·뺌), far_m2 = 용적률 산정용(지하층 0, 용적률 제외 객체 뺌)."""
    st = ctx.st
    unverified: list = []
    used = {"119-1-2", "119-1-3", "119-1-4", "법-2-1-5"}
    # 대지면적(1호)
    site = None
    if a.site_area_m2 is not None:
        site = {"id": None, "gross_m2": round(a.site_area_m2, 2)}
    else:
        sid = a.site
        if sid is None:
            cands = sorted(i for i in st.by_kind.get("site", ()) if st.el[i]["origin"] == "authored")
            sid = cands[0] if len(cands) == 1 else None
            if len(cands) > 1:
                unverified.append({"code": "site", "message": f"대지가 {len(cands)}개라 고르지 않았다(site 로 준다)"})
        if sid is not None:
            p = ctx.row(sid, "site")["params"]
            site = {"id": sid, "gross_m2": _m2(abs(LA.area2(p["boundary"])) / 2.0)}
    lareas = [(i, st.el[i]["params"]) for i in sorted(st.by_kind.get("larea", ()))]
    by_target: dict = {"site": [], "building": [], "floor": []}
    for i, p in lareas:
        by_target[p["target"]].append((i, p))
        used.add(p["rule"])
        if p["rule"] == LA.MANUAL:
            unverified.append({"code": "manual", "message": f"사용자 조정 {p['name']}({i}): {p['note'][:80]}"})
    if site is not None:
        ex = sum(_counted(p)["counted_m2"] for _i, p in by_target["site"])
        site.update(excluded_m2=round(-ex, 2), area_m2=round(site["gross_m2"] + ex, 2))
    else:
        unverified.append({"code": "site", "message": "대지면적이 없어 건폐율·용적률을 내지 않았다(site 또는 site_area_m2)"})
    # 층(레벨): 표고 순, 높이 = 다음 레벨까지
    levels = sorted((i for i in st.by_kind.get("level", ()) if st.el[i]["origin"] == "authored"),
                    key=lambda i: (st.el[i]["params"]["elevation"], i))
    elevs = [float(st.el[i]["params"]["elevation"]) for i in levels]
    given = None if a.basement_levels is None else set(a.basement_levels)
    if given is not None:
        for b in sorted(given):
            ctx.row(b, "level")
    floor_adj: dict = {}
    far_adj: dict = {}
    for i, p in by_target["floor"]:
        lv = p["level"]
        if lv not in levels:
            ctx.warn(f"법정 면적 객체 {i} 의 레벨이 없거나 저작 레벨이 아니라 뺐다")
            continue
        c = _counted(p)["counted_m2"]
        (far_adj if p["effect"] == "far" else floor_adj)[lv] = (far_adj if p["effect"] == "far" else floor_adj).get(lv, 0.0) + c
    rows, bpolys, bl_ids = [], [], []
    gross = far = 0.0
    for k, lv in enumerate(levels):
        e = elevs[k]
        h = next((x - e for x in elevs[k + 1:] if x > e), None)
        if given is not None:
            base = lv in given
        else:
            base = LA.is_basement(e, h, a.ground)
            if base is None:
                unverified.append({"code": "법-2-1-5", "message": f"레벨 {st.el[lv]['params']['name']} 는 지표면 아래인데 위 레벨이 없어 층 높이를 몰라 지하층을 판정하지 못했다(지상으로 셈, basement_levels 로 준다)"})
        polys = _wall_polys(ctx, lv)
        walls = _m2(_union_mm2(polys))
        adj = round(floor_adj.get(lv, 0.0), 2)
        fl = round(max(0.0, walls + adj), 2)
        fr = 0.0 if base else round(max(0.0, fl + far_adj.get(lv, 0.0)), 2)
        inb = LA.counts_for_building(e, h, a.ground)
        if inb:
            bpolys += polys
            bl_ids.append(lv)
        if not polys and any(st.el[s]["kind"] == "slab" for s in ctx.rels_in("on_level", lv)):
            unverified.append({"code": "119-1-3-가", "message": f"레벨 {st.el[lv]['params']['name']} 에 슬래브는 있으나 닫힌 벽 영역이 없다(벽·기둥 구획이 없으면 area.define 119-1-3-가 로 넣는다)"})
        gross += fl
        far += fr
        rows.append([lv, st.el[lv]["params"]["name"], e, h, base, inb, walls, adj, fl, fr])
    if given is not None or any(r[4] for r in rows):
        used.add("119-1-4-가")
    # 건축면적(2호): 건축면적에 넣은 층들의 벽 중심선 영역 합집합 + 객체
    bw = _m2(_union_mm2(bpolys))
    badj = round(sum(_counted(p)["counted_m2"] for _i, p in by_target["building"]), 2)
    building = {"walls_m2": bw, "adjust_m2": badj, "area_m2": round(max(0.0, bw + badj), 2), "levels": len(bl_ids)}
    used.add("119-1-2-다-1")
    # 자동으로 보지 않는 것(확인 필요)
    park = sorted(s for s in st.by_kind.get("space", ()) if any(w in st.el[s]["params"].get("category", "") for w in PARKING_WORDS))
    if park:
        unverified.append({"code": "119-1-4-나", "message": f"분류에 주차가 든 공간 {len(park)}개: 지상층 부속용도 주차면 area.define 119-1-4-나 로 뺀다(판정 안 함)"})
    if st.by_kind.get("roof") or any(st.el[r]["params"].get("use") == "roof" for r in st.by_kind.get("slab", ())):
        unverified.append({"code": "119-1-2-가", "message": "지붕이 있다: 처마·차양 1m 이상 돌출은 자동으로 더하지 않는다(area.define 119-1-2-가)"})
    unverified.append({"code": "119-2", "message": "지표면은 수평(ground)으로 본다: 고저차 가중평균(119조2항)·옥탑 층수(9호)·다락 높이 판정은 하지 않는다"})
    result: dict = {"source": LA.SOURCE, "ground": a.ground, "site": site, "building": building,
                    "gross_floor_m2": round(gross, 2), "far_floor_m2": round(far, 2), "columns": LEVEL_COLUMNS}
    den = site["area_m2"] if site is not None else 0.0
    result["coverage_pct"] = LA.ratio_pct(building["area_m2"], den)
    result["far_pct"] = LA.ratio_pct(far, den)
    if site is not None:
        used.update(("법-55", "법-56"))
    if a.coverage_max is not None or a.far_max is not None:
        lim = {}
        if a.coverage_max is not None:
            lim["coverage_max"] = a.coverage_max
            lim["coverage_ok"] = None if result["coverage_pct"] is None else result["coverage_pct"] <= a.coverage_max
        if a.far_max is not None:
            lim["far_max"] = a.far_max
            lim["far_ok"] = None if result["far_pct"] is None else result["far_pct"] <= a.far_max
        result["limits"] = lim
    result["basis"] = {c: (LA.AUTO_BASIS.get(c) or LA.RULES[c][2]) for c in sorted(used)}
    result["unverified"] = unverified[:20]
    start = C._dec_cursor(a.cursor, "int") or 0
    window = rows[start:]
    items, cut = C._fit(window, base=result, cursor_of=lambda k: C._enc_cursor(start + k + 1))
    more = start + len(items) < len(rows)
    return {**result, "items": items, "page": C._page(C._enc_cursor(start + len(items)) if more else None, len(rows), cut)}
