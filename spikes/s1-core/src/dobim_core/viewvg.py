# SPDX-License-Identifier: MPL-2.0
"""뷰 가시성·그래픽 재정의(VIEW-13)·뷰 필터(VIEW-14)·뷰 템플릿(VIEW-15)을 도면에 건다 (W1 1-C).

뷰(또는 뷰가 따르는 템플릿)에 vg·filters 가 없으면 이 모듈은 아무것도 하지 않는다(view_settings 가 None): 도면은 이전과 바이트까지 같다.
있으면 평면 GUI 장면(scene.view_scene)의 요소별 분해를 그대로 쓴다. 요소마다 재정의를 정하고
  - 숨김(hidden)이면 그 요소의 선분·채움·문자를 뺀다.
  - 색·굵기·하프톤이 있으면 그 요소의 선분을 굵기·색이 박힌 그림 항목("line", 요소 id, "VG", 굵기, 색, ...)으로 옮기고, 색·하프톤이면 채움도
    ("fill", 요소 id, "VG", 색, 다각형)으로 옮긴다. 이 항목은 2D 작도 항목과 같은 모양이라 SVG·PDF·PNG·DXF·시트가 그대로 그린다.
  - 재정의가 없는 요소는 이전처럼 등급 선분으로 남는다(merge_lines 로 다시 합친다).
재정의 = 범주 재정의(vg[종류]) 위에 걸리는 필터 재정의를 덮는다. 필터는 목록의 위가 우선이다(Revit 의 필터 순서와 같다).
알려진 한계: 숨긴 요소도 다른 요소의 가림 판정에는 남는다(가림은 요소별 질의가 이미 계산했다). 단면·입면의 문 선분은 벽 선분에 섞여 있어 벽을 따른다.
모든 함수는 그래프 질의(g.q)라 바뀐 요소·뷰만 다시 계산된다. 이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

from dobim_core import annot as AN
from dobim_core import drawing as D
from dobim_core import kinds as K
from dobim_core import queries as Q
from dobim_core import scene as SC

CLS_W = {"cut": 0.50, "cutn": 0.35, "proj": 0.18, "symbol": 0.25, "hatch": 0.09}   # 등급의 기본 굵기(drawing_export.STYLE 과 같다)
CLS_C = {"symbol": "#333333", "hatch": "#555555"}                                     # 등급의 기본 색(없으면 검정)
FILL_C = "#cfcfcf"                                                                    # 절단면 채움 기본 색(drawing_export 와 같다)
LAYER = "VG"                                                                          # 재정의 항목의 층 이름(DXF 층)
_STYLE_KEYS = frozenset(("color", "cut_weight", "proj_weight", "halftone"))


def view_settings(g, v):
    """뷰에 걸리는 (vg, filters) 또는 None(아무 설정도 없다). 템플릿이 있으면 템플릿 것을 쓴다(뷰 자신의 값은 무시)."""
    row = Q.el(g, v)
    if row is None or row["kind"] != "view":
        return None
    src = row["params"]
    t = src.get("template")
    if t is not None:
        tr = Q.el(g, t)
        if tr is not None and tr["kind"] == "vtemplate":   # 지워진 템플릿(병합이 남긴 끊긴 참조)은 없는 것으로 본다
            src = tr["params"]
    vg, fl = src.get("vg"), src.get("filters")
    if not vg and not fl:
        return None
    return (vg or {}, tuple(fl or ()))


def field_value(g, kind, eid, field):
    """필터 규칙이 묻는 요소 값(글). 값이 없으면 None. 태그 필드와 같은 계산을 쓴다(annot._field)."""
    row = Q.el(g, eid)
    if row is None:
        return None
    if field.startswith("pset:"):
        ps, _, pr = field[5:].partition(".")
        v = Q.psets(g, eid).get(ps, {}).get(pr)
        return None if v is None else str(v)
    if kind in AN.TAG_ALLOWED and field in AN.TAG_ALLOWED[kind]:
        try:
            v = AN._field(g, kind, eid, row, field)
        except (AN._NonFinite, KeyError, TypeError, IndexError, ValueError):
            return None
        return None if v in (None, "-") else str(v)
    if field == "name":
        n = row["params"].get("name")
        return n if isinstance(n, str) and n else None
    if field == "ifc_class":
        return K.FIXED_IFC_CLASS.get(kind) or row.get("ifc_class") or None
    return None


def rule_ok(val, op, want) -> bool:
    """규칙 하나. 값이 없는 요소는 has·missing·ne 밖에서는 맞지 않는다. 글 비교는 대소문자를 가리지 않는다."""
    if op == "has":
        return val is not None
    if op == "missing":
        return val is None
    if val is None:
        return op in ("ne", "not_contains")
    if op in K.VG_NUM_OPS:
        a, b = K.vg_num(val), K.vg_num(want)
        if a is None or b is None:
            return False
        return {"gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}[op]
    s, w = val.casefold(), want.casefold()
    if op in ("eq", "ne"):
        a, b = K.vg_num(val), K.vg_num(want)
        same = (a == b) if a is not None and b is not None else s == w
        return same if op == "eq" else not same
    if op == "contains":
        return w in s
    if op == "not_contains":
        return w not in s
    return s.startswith(w)   # begins


def filter_hits(g, kind, eid, f) -> bool:
    return kind in f["cats"] and all(rule_ok(g.q(field_value, kind, eid, r["field"]), r["op"], r["value"]) for r in f["rules"])


def el_override(g, v, kind, eid):
    """요소 하나의 재정의(정렬된 (키, 값) 튜플) 또는 None. 범주 재정의 위에 걸리는 필터를 아래에서 위로 덮는다(위가 이긴다)."""
    s = g.q(view_settings, v)
    if s is None:
        return None
    vg, fl = s
    o = dict(vg.get(kind, {}))
    if kind in K.VG_FILTER_CATS:
        for f in reversed(fl):
            if filter_hits(g, kind, eid, f):
                o.update({k: x for k, x in f.items() if k in K.VG_OVR_KEYS})
    return tuple(sorted(o.items())) or None


def _half(c: str) -> str:
    """하프톤: 색을 흰색 쪽으로 절반 옮긴다."""
    r, gg, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
    return "#%02x%02x%02x" % ((r + 255) // 2, (gg + 255) // 2, (b + 255) // 2)


def line_style(o: dict, cls: str):
    """재정의 o 아래에서 등급 cls 선분의 (굵기 mm, 색)."""
    w = o.get("cut_weight") if cls in ("cut", "cutn") else o.get("proj_weight")
    w = CLS_W.get(cls, CLS_W["proj"]) if w is None else w
    c = o.get("color") or CLS_C.get(cls, "#000000")
    return w, (_half(c) if o.get("halftone") else c)


def annot_kind(it) -> str:
    """주석 그림 항목의 범주(scene.view_scene 과 같은 규칙)."""
    return "dim" if it[0] in ("dim", "adim") else (it[0] if it[0] == "tag" else it[2])


def vg_view(g, v, hier=False):
    """재정의를 건 뷰 그림: (선분, 채움, 문자, 재정의 항목, 숨긴 주석 범주, 개수{"hidden", "styled"}) 또는 None(설정 없음).
    hier 이면 비내력벽 절단선을 "cutn" 으로 나눈다(view_lines_hier 와 같은 규칙)."""
    s = g.q(view_settings, v)
    if s is None:
        return None
    vg = s[0]
    nb = frozenset(g.q(D.foreign_nonbearing)) if hier else frozenset()
    lines, fills, labels, items = [], [], [], []
    n_hidden = n_styled = 0
    for kind, e, ml, fl, tx, _its, _bb in g.q(SC.view_scene, v):
        if kind in K.VG_ANNO_CATS:   # 2D 작도·주석은 호출자가 원래 질의로 그리고 범주 숨김만 건다
            continue
        if kind == "view":   # 뷰 단위 그림(대지·표지판·지반선·치수 문자)
            lines.extend(ml)
            labels.extend(tx)
            continue
        oo = g.q(el_override, v, kind, e)
        o = dict(oo) if oo else {}
        if o.get("hidden"):
            n_hidden += 1
            continue
        if hier and ((kind == "wall" and g.q(D.is_nonbearing, e)) or (kind == "foreign" and e in nb)):
            ml = tuple(("cutn",) + ln[1:] if ln[0] == "cut" else ln for ln in ml)
        labels.extend(tx)
        if _STYLE_KEYS & set(o):
            n_styled += 1
            for ln in ml:
                w, c = line_style(o, ln[0])
                items.append(("line", e, LAYER, w, c, ln[1], ln[2], ln[3], ln[4]))
            if "color" in o or o.get("halftone"):
                fc = o.get("color") or FILL_C
                fc = _half(fc) if o.get("halftone") else fc
                items.extend(("fill", e, LAYER, fc, poly) for poly in fl)
            else:
                fills.extend(fl)
        else:
            lines.extend(ml)
            fills.extend(fl)
    hid = frozenset(k for k in K.VG_ANNO_CATS if vg.get(k, {}).get("hidden"))
    return (D.merge_lines(lines), tuple(fills), tuple(labels), tuple(items), hid, {"hidden": n_hidden, "styled": n_styled})


# 뷰를 지우면 뷰에 딸린 질의 노드를 치운다(drawing.forget_views 가 이 집합을 본다)
D.VIEW_SCOPED = D.VIEW_SCOPED | frozenset({view_settings, el_override, vg_view})
