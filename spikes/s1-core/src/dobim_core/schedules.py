# SPDX-License-Identifier: MPL-2.0
"""범용 일람표(W1: DOC-07~10·13). 모든 요소 범주의 행·필드 선택·필터·정렬·그룹·합계·계산 필드·재료 물량.

순수 파생 질의다(queries.py 와 같은 규칙): 시각·난수·파일 접근 없음, libm 함수(sin·cos·hypot) 없이 sqrt 와 사칙연산만.
요소 하나의 행(element_row)은 그래프 질의라 요소가 바뀌면 그 행만 다시 계산한다.
단위는 내부적으로 mm·mm²·mm³ 이고, 표를 만들 때 units="m" 이면 m·m²·m³ 로 바꾼다.

범주는 Revit 일람표 범주에 맞춘 이름이다. 요소 종류(kind)와 IFC 분류(솔리드 category, 패밀리 레시피 ifc.class)로 정한다:
지금 모델에 만들 수 있는 모든 요소가 어느 한 범주에 든다(외래 IFC 요소 foreign 과 대지 site 는 일람표 범주가 아니다).
"""
from __future__ import annotations

from math import sqrt

from dobim_core import beams as BM
from dobim_core import kinds as K
from dobim_core import queries as Q
from dobim_core import solids as SO

# ---------------------------------------------------------------- 범주
# 범주 -> 한글 이름. 순서가 응답·문서의 순서다
CATEGORIES = {
    "walls": "벽", "doors": "문", "windows": "창", "floors": "바닥(슬래브)", "roofs": "지붕", "ceilings": "천장",
    "finishes": "마감(바닥 마감·외장·단열)", "stairs": "계단", "railings": "난간", "columns": "기둥", "beams": "보",
    "rooms": "실(공간)", "elevators": "승강기", "plumbing_fixtures": "위생기구", "furniture": "가구",
    "piping": "배관", "ducts": "덕트·공조 단말", "mechanical": "기계 설비", "electrical": "전기·조명·감지기",
    "fire_protection": "소방", "generic": "일반 모델",
}

# 솔리드 category -> 일람표 범주
_SOLID_CAT = {
    "wall": "walls", "door": "doors", "window": "windows", "slab": "floors", "roof": "roofs", "spire": "roofs",
    "ceiling": "ceilings", "flooring": "finishes", "cladding": "finishes", "insulation": "finishes", "covering": "finishes",
    "stair": "stairs", "railing": "railings", "column": "columns", "buttress": "columns", "beam": "beams",
    "pipe": "piping", "pipe_fitting": "piping", "valve": "piping", "flow_meter": "piping", "gas_meter": "piping",
    "duct": "ducts", "air_terminal": "ducts", "fan": "mechanical", "boiler": "mechanical", "unitary_equipment": "mechanical", "tank": "mechanical",
    "electric_board": "electrical", "control": "electrical", "light_fixture": "electrical", "exit_sign": "electrical",
    "cable_tray": "electrical", "alarm_panel": "electrical", "smoke_detector": "electrical", "heat_detector": "electrical",
    "fire_terminal": "fire_protection", "fire_extinguisher": "fire_protection",
    "member": "generic", "plate": "generic", "proxy": "generic", "footing": "generic", "terrain": "generic", "escape_device": "generic",
}
for _c in SO.SANITARY_CATEGORIES:
    _SOLID_CAT[_c] = "plumbing_fixtures"

# 패밀리 레시피 ifc.class -> 일람표 범주(없는 클래스는 generic)
_FAMILY_CAT = {"IfcDoor": "doors", "IfcWindow": "windows", "IfcColumn": "columns", "IfcBeam": "beams",
               "IfcFurniture": "furniture", "IfcFurnishingElement": "furniture", "IfcSanitaryTerminal": "plumbing_fixtures",
               "IfcStair": "stairs", "IfcRailing": "railings", "IfcSlab": "floors", "IfcWall": "walls", "IfcCovering": "finishes"}

# 일람표 범주가 있는 요소 종류(행을 만드는 종류)
ROW_KINDS = ("wall", "door", "slab", "roof", "stair", "railing", "space", "elevator", "solid", "finst", "beam")   # 구조 보: W2 열다섯 번째 PR

# ---------------------------------------------------------------- 필드
COMMON_FIELDS = ("id", "kind", "category", "ifc_class", "name", "type", "level", "elevation", "material", "fire_rating", "is_external", "count")
# 필드 -> 단위 종류(None 은 단위 없음). units="m" 일 때 바꾼다
FIELD_UNIT = {"elevation": "len", "length": "len", "width": "len", "height": "len", "thickness": "len", "perimeter": "len",
              "sill": "len", "offset": "len", "top_offset": "len", "riser_height": "len", "tread": "len", "ceiling_height": "len",
              "rise": "len", "depth": "len", "area": "area", "gross_area": "area", "volume": "vol"}
# 합계를 내는 필드(가산량). 폭·높이 같은 치수는 더하지 않는다
ADDITIVE = ("count", "length", "perimeter", "area", "gross_area", "volume")
# 범주마다 쓸 수 있는 고유 필드(공통 필드 뒤에 붙는다)
CATEGORY_FIELDS = {
    "walls": ("length", "height", "thickness", "area", "volume", "doors", "status"),
    "doors": ("host", "width", "height", "sill", "offset", "area"),
    "windows": ("host", "width", "height", "sill", "offset", "area"),
    "floors": ("thickness", "top_offset", "area", "perimeter", "volume", "openings"),
    "roofs": ("form", "pitch", "thickness", "area", "volume"),
    "ceilings": ("thickness", "area", "volume", "width", "length", "height"),
    "finishes": ("thickness", "area", "volume", "width", "length", "height"),
    "stairs": ("width", "height", "risers", "riser_height", "tread", "length"),
    "railings": ("height", "length", "rise"),
    "columns": ("width", "length", "height", "volume"),
    "beams": ("width", "depth", "length", "volume"),
    "rooms": ("number", "area", "perimeter", "height", "ceiling_height", "volume"),
    "elevators": ("width", "length", "rise"),
    "plumbing_fixtures": ("width", "length", "height", "volume"),
    "furniture": ("width", "length", "height", "volume"),
    "piping": ("width", "length", "height", "volume"),
    "ducts": ("width", "length", "height", "volume"),
    "mechanical": ("width", "length", "height", "volume"),
    "electrical": ("width", "length", "height", "volume"),
    "fire_protection": ("width", "length", "height", "volume"),
    "generic": ("width", "length", "height", "area", "volume"),
}
# 기본 필드(fields 를 주지 않을 때)
DEFAULT_FIELDS = {
    "walls": ("name", "level", "length", "height", "thickness", "area", "volume", "material"),
    "doors": ("name", "level", "type", "width", "height", "sill", "host", "fire_rating"),
    "windows": ("name", "level", "type", "width", "height", "sill", "host"),
    "floors": ("name", "level", "thickness", "area", "volume", "material"),
    "roofs": ("name", "level", "form", "pitch", "thickness", "area", "volume"),
    "rooms": ("number", "name", "level", "area", "perimeter", "height", "volume"),
    "stairs": ("name", "level", "width", "height", "risers", "riser_height", "tread"),
    "railings": ("name", "level", "height", "length"),
}


def fields_of(category: str) -> tuple:
    return COMMON_FIELDS + CATEGORY_FIELDS[category]


def default_fields(category: str) -> tuple:
    return DEFAULT_FIELDS.get(category, ("name", "level", "type") + CATEGORY_FIELDS[category])


# ---------------------------------------------------------------- 기하 도우미(순수)
def _poly_area(pts) -> float:
    return abs(SO.area2([tuple(q[:2]) for q in pts])) / 2.0


def _perimeter(pts) -> float:
    n = len(pts)
    s = 0.0
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        s += sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
    return s


def _polyline_len(pts) -> float:
    return sum(sqrt((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) for i in range(len(pts) - 1))


def mesh_measure(verts, faces) -> dict:
    """닫힌 다면체의 (부피, 윗면 투영 넓이, 상자 크기). 면은 고리(바깥 + 구멍) 목록이고 고리는 꼭짓점 번호다.
    부피는 발산 정리(원점 사면체 부호 부피의 합), 윗면 넓이는 위를 보는 면(법선 z>0)의 평면 투영 넓이 합이다."""
    vol, top = 0.0, 0.0
    for f in faces:
        axs = []
        for lp in f:
            ax = 0.0
            if len(lp) >= 3:
                p0 = verts[lp[0]]
                for i in range(1, len(lp) - 1):
                    p1, p2 = verts[lp[i]], verts[lp[i + 1]]
                    vol += (p0[0] * (p1[1] * p2[2] - p1[2] * p2[1]) - p0[1] * (p1[0] * p2[2] - p1[2] * p2[0])
                            + p0[2] * (p1[0] * p2[1] - p1[1] * p2[0])) / 6.0
                    ax += ((p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])) / 2.0
            axs.append(ax)
        if axs and axs[0] > 0.0:   # 위를 보는 면(바깥 고리 반시계). 구멍 고리는 반대로 돌아 음수라 바깥 넓이에서 빠진다
            top += sum(axs)
    xs = [v[0] for v in verts] or [0.0]
    ys = [v[1] for v in verts] or [0.0]
    zs = [v[2] for v in verts] or [0.0]
    return {"volume": abs(vol), "top_area": top, "dx": max(xs) - min(xs), "dy": max(ys) - min(ys), "dz": max(zs) - min(zs)}


# ---------------------------------------------------------------- 범주 판정
def _recipe_class(g, f) -> str | None:
    tp = Q.el(g, Q.finst_type(g, f))["params"]
    return (tp.get("recipe") or {}).get("ifc", {}).get("class")


def category_of(g, e) -> str | None:
    """요소의 일람표 범주(없으면 None)."""
    row = Q.el(g, e)
    if row is None:
        return None
    kind, p = row["kind"], row["params"]
    if kind == "wall":
        return "walls"
    if kind == "door":
        return "doors"
    if kind == "slab":
        return "roofs" if p.get("use") == "roof" else "floors"
    if kind == "roof":
        return "roofs"
    if kind == "stair":
        return "stairs"
    if kind == "railing":
        return "railings"
    if kind == "space":
        return "rooms"
    if kind == "elevator":
        return "elevators"
    if kind == "solid":
        return _SOLID_CAT.get(p["category"], "generic")
    if kind == "beam":   # 구조 보(W2 열다섯 번째 PR)
        return "beams"
    if kind == "finst":
        return _FAMILY_CAT.get(g.q(_recipe_class, e), "generic")
    return None


def _level_of(g, e, kind):
    if kind == "wall":
        return g.q(Q.wall_level, e)
    if kind == "door":
        return g.q(Q.wall_level, g.q(Q.door_host, e))
    if kind == "finst":
        return g.q(Q.finst_level, e)
    r = Q.rel_out(g, "on_level", e)
    return r[0] if r else None


def _pset_any(g, e, prop):
    """Pset_*Common 중 prop 값(처음 찾은 것). 일람표의 내화 등급·외부 여부 칸."""
    ps = Q.psets(g, e) or {}
    for name in sorted(ps):
        if name.endswith("Common") and prop in ps[name]:
            return ps[name][prop]
    return None


# ---------------------------------------------------------------- 요소 행
def element_row(g, e) -> dict:
    """요소 하나의 일람표 행(필드 -> 값, mm 단위). 범주가 없는 요소는 빈 dict."""
    cat = g.q(category_of, e)
    if cat is None:
        return {}
    row = Q.el(g, e)
    kind, p = row["kind"], row["params"]
    lv = g.q(_level_of, e, kind)
    lname, lelev = g.q(Q.level_info, lv) if lv is not None and Q.el(g, lv) is not None else (None, None)
    out = {"id": e, "kind": kind, "category": cat, "ifc_class": K.FIXED_IFC_CLASS.get(kind), "name": p.get("name"), "type": None,
           "level": lname, "elevation": lelev, "material": Q.material(g, e), "fire_rating": _pset_any(g, e, "FireRating"),
           "is_external": _pset_any(g, e, "IsExternal"), "count": 1}
    if kind == "wall":
        r = g.q(Q.wall_row, e)
        out.update(length=r[4], height=r[5], thickness=r[6], area=r[7], volume=r[7] * r[6], doors=r[8], status=r[12])
    elif kind == "door":
        r = g.q(Q.door_row, e)
        out.update(host=Q.el(g, r[2])["params"]["name"], width=r[5], height=r[6], sill=r[7], offset=r[8], area=r[5] * r[6])
    elif kind == "slab":
        area = _poly_area(p["boundary"]) - sum(_poly_area(o) for o in p.get("openings", ()))
        out.update(thickness=p["thickness"], top_offset=p["top_offset"], area=area, perimeter=_perimeter(p["boundary"]),
                   volume=area * p["thickness"], openings=len(p.get("openings", ())), ifc_class="IfcSlab")
        if lelev is not None:
            out["elevation"] = lelev + p["top_offset"]
    elif kind == "roof":
        gm = K.roof_geom(p)
        area = 0.0
        for fc, (a, b, _c) in zip(gm["faces"], gm["planes"]):   # 경사면 넓이 = 평면 투영 넓이 x sqrt(1 + 기울기²)
            area += _poly_area(fc) * sqrt(1.0 + a * a + b * b)
        out.update(form=p["form"], pitch=p["pitch"], thickness=p["thickness"], area=area, volume=area * p["thickness"])
    elif kind == "stair":
        lay = K.stair_layout(p)
        fl = lay["flights"]
        out.update(width=p["width"], height=p["height"], risers=p["risers"], riser_height=lay["rise"],
                   tread=fl[0]["td"] if fl else None, length=sum(f["length"] for f in fl))
    elif kind == "railing":
        out.update(height=p["height"], length=_polyline_len(p["path"]), rise=p["rise"])
    elif kind == "space":
        area = _poly_area(p["boundary"]) - sum(_poly_area(o) for o in p.get("holes", ()))
        out.update(number=p.get("name"), type=p.get("category") or None, area=area, perimeter=_perimeter(p["boundary"]), height=p["height"],
                   ceiling_height=p.get("ceiling_height"), volume=area * p.get("ceiling_height", p["height"]))
        out["name"] = p.get("category") or p.get("name")   # 실 일람표: 이름 칸은 실 용도, 번호 칸은 실 이름(번호)
    elif kind == "elevator":
        s = p["shaft"]
        out.update(width=abs(s[2] - s[0]), length=abs(s[3] - s[1]), rise=p["rise"])
    elif kind == "solid":
        spec = g.q(Q.ifc_spec, e)
        m = mesh_measure(spec["verts"], spec["faces"])
        out.update(type=p["category"], ifc_class=spec["ifc_class"], volume=m["volume"], height=m["dz"])
        if p["form"] == "member":
            a0, a1 = p["start"], p["end"]
            out.update(length=sqrt(sum((a1[i] - a0[i]) ** 2 for i in range(3))), width=p["section"].get("width", p["section"].get("diameter")),
                       depth=p["section"].get("depth", p["section"].get("diameter")))
        else:
            out.update(width=min(m["dx"], m["dy"]), length=max(m["dx"], m["dy"]))
        if cat in ("ceilings", "finishes", "generic", "floors", "roofs", "walls"):
            out["area"] = m["top_area"]
            if "thickness" in p:
                out["thickness"] = p["thickness"]
            elif m["top_area"] > 0:
                out["thickness"] = m["volume"] / m["top_area"]
    elif kind == "beam":   # 구조 보(W2 열다섯 번째 PR): 길이는 기둥 면 자르기 뒤의 길이, 체적 = 단면적 x 그 길이
        _ln, cl, _a, vol = g.q(Q.beam_measure, e)
        bt = p.get("btype")
        out.update(type=Q.el(g, bt)["params"]["name"] if bt and Q.el(g, bt) is not None else BM.section_label(p["section"]),
                   width=p["section"]["width"], depth=p["section"]["depth"], length=cl, volume=vol)
        if lelev is not None:
            out["elevation"] = lelev + p["offset"]
    elif kind == "finst":
        spec = g.q(Q.ifc_spec, e)
        out["ifc_class"] = g.q(_recipe_class, e)
        out["type"] = Q.el(g, Q.finst_type(g, e))["params"]["name"]
        if "offset" in p:   # 호스팅(문·창): 폭·높이는 인스턴스 개구부 값
            h = g.q(Q.finst_host, e)
            out.update(host=Q.el(g, h)["params"]["name"] if h else None, width=p["width"], height=p["height"], sill=p["elevation"],
                       offset=p["offset"], area=p["width"] * p["height"])
        else:
            tv = Q.el(g, Q.finst_type(g, e))["params"].get("type_values", {})
            vals = {**tv, **p.get("values", {})}
            for k in ("width", "length", "height", "depth"):
                v = vals.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out[k] = float(v)
        if spec.get("error"):
            out["status"] = "error"
    return out


def category_ids(g, category: str) -> tuple:
    """범주에 드는 요소 id(정렬)."""
    out = []
    for kind in ROW_KINDS:
        for b in range(Q.NB):
            for e in Q.bucket_ids(g, kind, b):
                if g.q(category_of, e) == category:
                    out.append(e)
    return tuple(sorted(out))


def category_counts(g) -> dict:
    """범주별 요소 수(0 인 범주는 빼고)."""
    n: dict = {}
    for kind in ROW_KINDS:
        for b in range(Q.NB):
            for e in Q.bucket_ids(g, kind, b):
                c = g.q(category_of, e)
                if c is not None:
                    n[c] = n.get(c, 0) + 1
    return {c: n[c] for c in CATEGORIES if c in n}


# ---------------------------------------------------------------- 계산 필드(간단한 산식)
class ExprError(ValueError):
    pass


def _tokens(s: str) -> list:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
        elif c in "+-*/()":
            out.append(c)
            i += 1
        elif c.isdigit() or c == ".":
            j = i
            while j < len(s) and (s[j].isdigit() or s[j] == "."):
                j += 1
            try:
                out.append(float(s[i:j]))
            except ValueError:
                raise ExprError(f"숫자가 올바르지 않다: {s[i:j]}")
            i = j
        elif c.isalpha() or c == "_":
            j = i
            while j < len(s) and (s[j].isalnum() or s[j] == "_"):
                j += 1
            out.append(("f", s[i:j]))
            i = j
        else:
            raise ExprError(f"산식에 쓸 수 없는 글자다: {c}")
    return out


def parse_expr(s: str, known) -> tuple:
    """산식을 트리로 읽는다: 수·필드 이름·+ - * /·괄호·단항 -. 필드는 known 에 있어야 한다. 트리: ("n", v) | ("f", 이름) | (연산, a, b) | ("neg", a)."""
    toks = _tokens(s)
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        t = peek()
        pos[0] += 1
        return t

    def atom():
        t = take()
        if t is None:
            raise ExprError("산식이 덜 끝났다")
        if t == "(":
            v = add()
            if take() != ")":
                raise ExprError("괄호가 닫히지 않았다")
            return v
        if t == "-":
            return ("neg", atom())
        if isinstance(t, float):
            return ("n", t)
        if isinstance(t, tuple):
            if t[1] not in known:
                raise ExprError(f"없는 필드다: {t[1]}")
            return t
        raise ExprError(f"여기에 올 수 없다: {t}")

    def mul():
        v = atom()
        while peek() in ("*", "/"):
            op = take()
            v = (op, v, atom())
        return v

    def add():
        v = mul()
        while peek() in ("+", "-"):
            op = take()
            v = (op, v, mul())
        return v

    tree = add()
    if pos[0] != len(toks):
        raise ExprError(f"산식 끝에 남는 것이 있다: {toks[pos[0]]}")
    return tree


def eval_expr(tree, row: dict):
    """산식 값. 필드가 비었거나 수가 아니거나 0 으로 나누면 None."""
    op = tree[0]
    if op == "n":
        return tree[1]
    if op == "f":
        v = row.get(tree[1])
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    if op == "neg":
        v = eval_expr(tree[1], row)
        return None if v is None else -v
    a, b = eval_expr(tree[1], row), eval_expr(tree[2], row)
    if a is None or b is None:
        return None
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    return None if b == 0 else a / b


def expr_fields(tree) -> set:
    if tree[0] == "f":
        return {tree[1]}
    if tree[0] == "n":
        return set()
    return set().union(*(expr_fields(t) for t in tree[1:]))


# ---------------------------------------------------------------- 표 만들기(행 dict 목록 -> 표)
def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def convert_units(row: dict, units: str, unit_of: dict) -> dict:
    if units == "mm":
        return row
    out = dict(row)
    for k, u in unit_of.items():
        v = out.get(k)
        if _num(v):
            out[k] = v / (1000.0 if u == "len" else 1e6 if u == "area" else 1e9)
    return out


def _match(v, op, x) -> bool:
    if op == "empty":
        return v is None or v == ""
    if op == "not_empty":
        return not (v is None or v == "")
    if v is None:
        return False
    if op == "contains":
        return str(x).lower() in str(v).lower()
    if _num(v) and _num(x):
        a, b = float(v), float(x)
    else:
        a, b = str(v), str(x)
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    try:
        return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]
    except TypeError:
        return False


def _sort_key(v):
    """비어 있는 값은 맨 뒤, 수는 수끼리, 나머지는 문자열로 비교한다(형이 섞여도 전순서)."""
    if v is None:
        return (2, 0.0, "")
    if _num(v):
        return (0, float(v), "")
    return (1, 0.0, str(v))


def build_table(rows: list, columns: list, filters: list, sort: list, group_by: list, itemize: bool, additive: set) -> dict:
    """행 dict 목록 -> {"rows": [[값...]], "groups": [...], "totals": {...}}. 필터(모두 만족) → 정렬(그룹 필드가 먼저) → 그룹·합계.
    rows 는 이미 계산 필드·단위가 들어간 dict 다. 정렬의 마지막 키는 id 라 순서가 결정적이다."""
    kept = [r for r in rows if all(_match(r.get(f["field"]), f["op"], f.get("value")) for f in filters)]
    keys = [(g, False) for g in group_by] + [(s["field"], s.get("desc", False)) for s in sort] + [("id", False)]
    for fld, desc in reversed(keys):   # 안정 정렬을 뒤 키부터: 결과는 앞 키가 주 키
        kept.sort(key=lambda r: _sort_key(r.get(fld)), reverse=desc)
    sums = [c for c in columns if c in additive]

    def total(rs):
        t = {"count": len(rs)}
        for c in sums:
            if c == "count":
                continue
            vals = [r.get(c) for r in rs if _num(r.get(c))]
            t[c] = sum(vals) if vals else None
        return t
    groups = []
    if group_by:
        cur, start = None, 0
        for i, r in enumerate(kept + [None]):
            k = None if r is None else tuple(r.get(g) for g in group_by)
            if i == 0:
                cur = k
                continue
            if r is None or k != cur:
                rs = kept[start:i]
                groups.append({"key": dict(zip(group_by, cur)), "first": start, "last": i - 1, "totals": total(rs)})
                cur, start = k, i
    if group_by and not itemize:   # 그룹 행만: 그룹 안에서 값이 모두 같은 칸은 그 값, 가산 칸은 합, 나머지는 빈칸
        out_rows = []
        for gr in groups:
            rs = kept[gr["first"]:gr["last"] + 1]
            line = []
            for c in columns:
                if c in additive:
                    line.append(len(rs) if c == "count" else gr["totals"].get(c))
                else:
                    vals = {repr(r.get(c)) for r in rs}
                    line.append(rs[0].get(c) if len(vals) == 1 else None)
            out_rows.append(line)
    else:
        out_rows = [[r.get(c) for c in columns] for r in kept]
    return {"rows": out_rows, "groups": groups, "totals": total(kept), "matched": len(kept)}


# ---------------------------------------------------------------- 재료 물량(DOC-10)
TAKEOFF_KINDS = ("wall", "slab", "roof", "solid", "beam")   # 구조 보(W2 열다섯 번째 PR): 부피만


def takeoff_parts(g, e) -> tuple:
    """요소 하나의 재료 물량 조각 ((범주, 재료, 면적 mm², 부피 mm³), ...). 층 구성이 있는 벽·슬래브는 층마다 한 조각(면적은 같고
    부피는 층 두께 비율)이다. 지붕은 경사면 넓이, 솔리드는 마감·판 계열이면 윗면 넓이, 아니면 면적 없이 부피만."""
    r = g.q(element_row, e)
    if not r:
        return ()
    p = Q.el(g, e)["params"]
    area, vol = r.get("area"), r.get("volume")
    if r["kind"] in ("wall", "slab", "roof") and "layers" in p:   # 지붕 층 구성: W2 여덟 번째 PR
        t = p["thickness"]
        return tuple((r["category"], x["material"] or "(미지정)", area, (vol or 0.0) * x["thickness"] / t if t else 0.0) for x in p["layers"])
    return ((r["category"], r.get("material") or "(미지정)", area, vol),)


def takeoff(g, categories, by_level: bool) -> list:
    """재료 물량 표 행: (범주, [레벨], 재료) 묶음마다 {"category","level","material","count","area","volume"}. 정렬 = 묶음 키."""
    groups: dict = {}
    for kind in TAKEOFF_KINDS:
        for b in range(Q.NB):
            for e in Q.bucket_ids(g, kind, b):
                r = g.q(element_row, e)
                if not r or r["category"] not in categories:
                    continue
                for cat, mat, area, vol in g.q(takeoff_parts, e):
                    key = (cat, r["level"] if by_level else None, mat)
                    acc = groups.setdefault(key, {"ids": set(), "area": 0.0, "volume": 0.0, "has_area": False})
                    acc["ids"].add(e)
                    if area is not None:
                        acc["area"] += area
                        acc["has_area"] = True
                    acc["volume"] += vol or 0.0
    out = []
    for key in sorted(groups, key=lambda k: (list(CATEGORIES).index(k[0]), _sort_key(k[1]), k[2])):
        acc = groups[key]
        out.append({"category": key[0], "level": key[1], "material": key[2], "count": len(acc["ids"]),
                    "area": acc["area"] if acc["has_area"] else None, "volume": acc["volume"]})
    return out


# ---------------------------------------------------------------- CSV
def csv_text(header: list, rows: list) -> str:
    """RFC 4180 CSV(줄끝 CRLF). 수는 소수 6자리 이하로 끝의 0 을 지운다. 엑셀이 한글을 읽도록 호출자가 UTF-8 BOM 을 붙인다."""
    def cell(v):
        if v is None:
            return ""
        if isinstance(v, bool):
            s = "TRUE" if v else "FALSE"
        elif _num(v):
            s = f"{float(v):.6f}".rstrip("0").rstrip(".") if not isinstance(v, int) else str(v)
            if s in ("-0", ""):
                s = "0"
        else:
            s = str(v)
        if any(c in s for c in ',"\r\n'):
            s = '"' + s.replace('"', '""') + '"'
        return s
    return "".join(",".join(cell(v) for v in line) + "\r\n" for line in [header] + rows)
