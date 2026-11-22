# SPDX-License-Identifier: MPL-2.0
"""재료 외관(W2 다섯 번째 PR, DATA-05·VIS-07): Revit 의 재료 외관(Appearance·Shading)처럼 재료 이름 하나에 표면 색·투명도·광택을 정한다.

material.define     : 정의를 만들거나 고친다(키 = 재료 이름). 처음 만들 때 색을 주지 않으면 기본 재료 라이브러리(appearance.library_appearance)
                      값으로 시작하고, 이미 있으면 준 값만 바꾼다.
material.delete     : 정의를 지운다(요소의 재료 이름은 그대로, 외관은 기본값으로 돌아간다).
material.list       : 프로젝트의 재료(요소가 쓰는 재료 + 정의된 재료)와 외관·출처·사용 요소 수.
material.library    : 기본 재료 라이브러리 표(낱말·색·투명도·광택).
material.appearance : 요소별 실제 외관(3D 장면·GUI 가 칠할 색): 재료, 층 구성의 층별, 커튼월 부속 종류별.

요소에는 여전히 재료 이름만 둔다(material.set·material.set_many·층 구성). 정의는 비 IFC 의도 요소(matdef)라 DoBIM_Defs 로 IFC 를 왕복하고
실행 취소·병합을 그대로 탄다. IFC 투영은 그 이름의 IfcMaterial 에 IfcSurfaceStyleRendering(색·투명도·광택)을 IfcMaterialDefinitionRepresentation
으로 붙인다(엔진 flush 가 값이 바뀐 재료만 다시 붙인다). 정의가 없는 재료의 IFC 는 18단계 그대로다(기존 프로젝트의 IFC 바이트 불변).
도면(평면·단면·입면 2D)은 외관을 쓰지 않는다. 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, StrictInt

from dobim_core import appearance as AP
from dobim_core import commands as C
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core.commands import A, Id, Name, _det_id, command, row
from dobim_core.errors import DobimError

IDS_MAX = 500
Rgb = Annotated[list[Annotated[StrictInt, Field(ge=0, le=255)]], Field(min_length=3, max_length=3, description="표면 색 [r, g, b] 0~255")]
Unit = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
DIGITS = 4   # 투명도·광택 저장 자릿수(같은 값이면 같은 IFC 가 나오게)


def _mid(ctx, name: str) -> str | None:
    return next((i for i in sorted(ctx.st.by_kind.get("matdef", ())) if ctx.st.el[i]["params"]["name"] == name), None)


def _pub(p: dict) -> dict:
    return {"color": list(p["color"]), "transparency": p["transparency"], "shininess": p["shininess"]}


def _usage(ctx) -> dict:
    """재료 이름 -> 그 재료를 쓰는 요소 수(단일 재료, 층 구성의 층, 커튼월 부속 종류). 외래 요소의 재료도 센다(IFC 에 있다)."""
    st, n = ctx.st, {}
    for k, v in K.ELEMENT_KINDS.items():
        if not v.ifc:
            continue
        for e in st.by_kind.get(k, ()):
            p = st.el[e]["params"]
            if k in ("wall", "slab") and "layers" in p:
                names = {x["material"] for x in p["layers"]}
            elif k == "curtwall":
                names = set(ctx.eng.ifc.CW_MATERIALS.values())
            else:
                m = ctx.material(e)
                names = {m} if isinstance(m, str) else set()
            for nm in names:
                n[nm] = n.get(nm, 0) + 1
    return n


def effective(ctx, name):
    """재료 이름 -> (외관 dict 또는 None, 출처). 출처: defined(외관 정의) · ifc(가져온 파일 재료의 다른 스타일) · default(18단계 기본 색) · None."""
    if not isinstance(name, str):
        return None, None
    mid = _mid(ctx, name)
    if mid is not None:
        return _pub(ctx.st.el[mid]["params"]), "defined"
    ifc = ctx.eng.ifc
    if ifc.base_mode and name in ifc._mats:   # 가져온 파일의 재료: 그 파일이 붙인 스타일이 코어의 기본과 다르면 그것을 보인다
        sig = ifc.style_sig(ifc._mats[name])
        if sig not in (None, "other") and sig != ifc.style_want(name):
            return ifc.appearance_of(name), "ifc"
    rgb = AP.material_rgb(name)
    if rgb is not None:
        return {"color": list(rgb), "transparency": 0.0, "shininess": 0.0}, "default"
    return None, None


# ---------------------------------------------------------------- 정의 만들기·고치기·지우기
class MatDefine(A):
    name: Name = Field(description="재료 이름(material.set 의 이름과 같다)")
    color: Rgb | None = Field(None, description="표면 색. 처음 만들 때 생략하면 기본 재료 라이브러리 색(라이브러리에 없는 재료는 꼭 준다)")
    transparency: Unit | None = Field(None, description="투명도 0(불투명)~1(완전 투명). 생략하면 기존 값 또는 라이브러리 값(없으면 0)")
    shininess: Unit | None = Field(None, description="광택(반사 정도) 0~1. 생략하면 기존 값 또는 라이브러리 값(없으면 0)")


@command("material.define", kind="mutation", risk="write", group="model.pset",
         summary="재료 외관(표면 색·투명도·광택)을 만들거나 고친다(처음이면 기본 재료 라이브러리 값으로 시작)",
         args=MatDefine, since="0.18",
         examples=[{"name": "Concrete"}, {"name": "유리", "transparency": 0.7}, {"name": "외벽 도장", "color": [200, 80, 60], "shininess": 0.3}])
def material_define(ctx, a: MatDefine):
    mid = _mid(ctx, a.name)
    created = mid is None
    if created:
        base = AP.library_appearance(a.name)
        if base is None:
            if a.color is None:
                raise DobimError("CONSTRAINT", f"기본 재료 라이브러리에 없는 재료다: {a.name}. color 를 주세요", path="/color",
                                 hint="material.library 로 라이브러리 낱말을 보세요")
            base = {"color": list(a.color), "transparency": 0.0, "shininess": 0.0}
    else:
        base = _pub(ctx.st.el[mid]["params"])
    params = {"name": a.name, "color": list(a.color) if a.color is not None else list(base["color"]),
              "transparency": round(float(a.transparency if a.transparency is not None else base["transparency"]), DIGITS),
              "shininess": round(float(a.shininess if a.shininess is not None else base["shininess"]), DIGITS)}
    if not ctx.eng.ifc.appearance_owned(a.name):
        raise DobimError("CONSTRAINT", f"가져온 파일의 재료 {a.name} 는 다른 도구가 붙인 표면 스타일을 가진다: 외관 정의로 덮어쓰지 않는다", path="/name",
                         hint="다른 이름의 재료를 정의하고 material.set 으로 바꿔 지정하세요")
    if created:
        if len(ctx.st.by_kind.get("matdef", ())) >= K.MATDEFS_MAX:
            raise DobimError("CONSTRAINT", f"재료 외관 정의는 {K.MATDEFS_MAX}개까지다", path="/name")
        mid = _det_id("matdef", a.name)
        if mid in ctx.st.el:
            raise DobimError("CONSTRAINT", f"이미 있는 id 다: {mid}", path="/name")
        if ctx.external_def(mid):
            ctx.warn("가져온 파일에 이 id 의 재료 외관 정의가 되살려지지 않은 채 남아 있다: 새 정의가 그 속성을 덮어쓴다")
        ctx.put_el(mid, row("matdef", "", "material.def", params))
        changed = True
    else:
        changed = params != ctx.st.el[mid]["params"]
        if changed:
            ctx.put_el(mid, dict(ctx.row(mid), params=params))
    return {"id": mid, "created": created, "changed": changed, "name": a.name, "appearance": _pub(params), "used_by": _usage(ctx).get(a.name, 0)}


class MatName(A):
    name: Name


@command("material.delete", kind="mutation", risk="write", group="model.pset",
         summary="재료 외관 정의를 지운다(요소의 재료 이름은 남고 외관은 기본값으로 돌아간다)",
         args=MatName, since="0.18", examples=[{"name": "Concrete"}])
def material_delete(ctx, a: MatName):
    mid = _mid(ctx, a.name)
    if mid is None:
        raise DobimError("NOT_FOUND", f"재료 외관 정의가 없다: {a.name}", path="/name")
    ctx.put_el(mid, None)
    return {"deleted": [mid]}


# ---------------------------------------------------------------- 조회
class MatList(A):
    limit: int = Field(100, ge=1, le=K.MATDEFS_MAX, description="최대 개수. 응답 크기 상한에 걸리면 이보다 적게 돌려주고 page.truncated 로 알린다")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


@command("material.list", kind="query", risk="read", group="model.pset",
         summary="프로젝트 재료 목록(외관·출처·정의 여부·쓰는 요소 수)",
         args=MatList, idempotent=True, since="0.18", examples=[{}, {"limit": 5}])
def material_list(ctx, a: MatList):
    used = _usage(ctx)
    names = sorted(set(used) | {ctx.st.el[i]["params"]["name"] for i in ctx.st.by_kind.get("matdef", ())})
    after = C._dec_cursor(a.cursor, "id")
    start = 0 if after is None else next((k for k, n in enumerate(names) if n > after), len(names))
    rows = []
    for nm in names[start:start + a.limit]:
        ap, src = effective(ctx, nm)
        rows.append({"name": nm, "id": _mid(ctx, nm), "appearance": ap, "source": src, "library": AP.library_appearance(nm), "used_by": used.get(nm, 0)})
    items, cut = C._fit(rows, cursor_of=lambda k: C._enc_cursor(rows[k]["name"]))
    more = start + len(items) < len(names)
    return {"items": items, "page": C._page(C._enc_cursor(items[-1]["name"]) if more else None, len(names), cut)}


class MatLibrary(A):
    name: Name | None = Field(None, description="이 재료 이름이 라이브러리의 어느 값에 맞는지만 본다")


@command("material.library", kind="query", risk="read", group="model.pset",
         summary="기본 재료 라이브러리(낱말·색·투명도·광택). name 을 주면 그 이름에 맞는 값",
         args=MatLibrary, idempotent=True, since="0.18", examples=[{}, {"name": "외부 유리"}])
def material_library(ctx, a: MatLibrary):
    if a.name is not None:
        return {"name": a.name, "appearance": AP.library_appearance(a.name)}
    return {"items": AP.library_rows()}


class MatAppearance(A):
    ids: Annotated[list[Id], Field(min_length=1, max_length=IDS_MAX, description="요소 id 들")]


@command("material.appearance", kind="query", risk="read", group="model.pset",
         summary="요소별 실제 외관(3D 장면·GUI 가 칠할 색·투명도·광택): 재료, 층 구성의 층별, 커튼월 부속 종류별",
         args=MatAppearance, idempotent=True, since="0.18", examples=[{"ids": [F.W1, F.W2]}])
def material_appearance(ctx, a: MatAppearance):
    out = []
    for eid in a.ids:
        r = ctx.row(eid)
        if r["kind"] in K.NON_IFC_KINDS:
            raise DobimError("CONSTRAINT", f"재료를 가질 수 없는 요소다: {eid}", path="/ids")
        m = ctx.material(eid)
        ap, src = effective(ctx, m)
        it = {"id": eid, "kind": r["kind"], "material": m, "appearance": ap, "source": src}
        if r["kind"] in ("wall", "slab") and "layers" in r["params"]:
            it["layers"] = [{"material": x["material"], "thickness": x["thickness"], "appearance": effective(ctx, x["material"])[0]} for x in r["params"]["layers"]]
        elif r["kind"] == "curtwall":   # 커튼월 부속(W2 ARC-22)은 종류별 재료를 따른다
            it["parts"] = {c: {"material": n, "appearance": effective(ctx, n)[0]} for c, n in sorted(ctx.eng.ifc.CW_MATERIALS.items())}
        out.append(it)
    return {"items": out}
