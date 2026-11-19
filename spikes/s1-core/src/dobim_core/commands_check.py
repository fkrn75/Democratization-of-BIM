# SPDX-License-Identifier: MPL-2.0
"""모델 품질 점검(W1 1-E, ANA-06): model.check.

model.validate 는 IFC 투영의 유효성을, annot.check 는 주석을 본다. model.check 는 모델링 실수를 찾는다(읽기 전용, 아무것도 바꾸지 않는다).
  short_wall   축 길이가 tol 보다 짧은 벽
  overlap_wall 같은 레벨에서 같은 선 위에 놓여 tol 넘게 겹친 두 벽
  duplicate    같은 종류·같은 레벨(문은 같은 호스트)에 이름만 빼고 매개변수가 똑같은 요소 묶음
  orphan       호스트 벽이 없는 문, 레벨 관계가 없는 요소
  off_level    바닥이 위 레벨 높이 이상이거나 벽 전체가 자기 레벨 아래에 있는 벽(다른 층에 놓인 셈)
  space_open   공간 경계 변 가운데 같은 레벨 벽(축선에서 두께/2 + tol 안, 평행)이 덮지 않는 구간이 tol 보다 긴 것
이 모듈은 ifcopenshell 을 import 하지 않는다.
"""
from __future__ import annotations

import json
from math import atan2, cos, floor, hypot, pi, sin
from typing import Literal

from pydantic import Field

from dobim_core import fixture as F
from dobim_core import limits
from dobim_core import walltop as WT
from dobim_core.commands import A, Id, command
from dobim_core.errors import DobimError

CHECKS = ("short_wall", "overlap_wall", "duplicate", "orphan", "off_level", "space_open")
Check = Literal["short_wall", "overlap_wall", "duplicate", "orphan", "off_level", "space_open"]
DUP_KINDS = ("wall", "slab", "door", "space", "stair", "railing", "roof", "solid", "finst")   # 중복을 보는 형상 요소
LEVEL_KINDS = ("wall", "slab", "stair", "space", "railing", "roof", "solid")                # 레벨 관계가 꼭 있어야 하는 요소
ANG_TOL = 0.5 * pi / 180.0      # 평행 판정 각(0.5도)
ANG_STEP = 0.002                # 겹침 후보를 묶는 각 칸(라디안)
MSG_CHARS = 120


class ModelCheck(A):
    checks: list[Check] | None = Field(None, min_length=1, max_length=len(CHECKS), description="볼 항목(생략하면 전부)")
    level: Id | None = Field(None, description="이 레벨의 요소만 본다")
    tol: float = Field(10.0, ge=0.1, le=1000.0, description="길이 허용치 mm(짧은 벽·겹침·열린 경계)")
    limit: int = Field(100, ge=1, le=1000, description="최대 개수(응답 크기 상한에 걸리면 page.truncated)")
    cursor: str | None = Field(None, max_length=2048, description="이전 응답의 page.next_cursor")


def _lv(ctx, e):
    r = ctx.rels_out("on_level", e)
    return r[0] if r else None


def _seg(p):
    x0, y0, x1, y1 = p["axis"]
    return float(x0), float(y0), float(x1), float(y1)


def _short(ctx, walls, tol):
    out = []
    for w in walls:
        x0, y0, x1, y1 = _seg(ctx.st.el[w]["params"])
        n = hypot(x1 - x0, y1 - y0)
        if n < tol:
            out.append(([w], f"벽 길이 {n:.1f}mm < {tol:g}mm"))
    return out


def _line(seg):
    """선의 방향각 [0, pi), 단위 방향, 원점에서의 수직 거리, 선 위 구간."""
    x0, y0, x1, y1 = seg
    t = atan2(y1 - y0, x1 - x0) % pi
    ux, uy = cos(t), sin(t)
    c = -uy * x0 + ux * y0
    s0, s1 = ux * x0 + uy * y0, ux * x1 + uy * y1
    return t, ux, uy, c, min(s0, s1), max(s0, s1)


def _overlap(ctx, walls, tol):
    """같은 레벨에서 평행하고 수직 거리가 tol 안이며 tol 넘게 겹친 벽 쌍. 각·거리 칸으로 묶어 이웃 칸만 비교한다."""
    st = ctx.st
    nang = int(pi / ANG_STEP) + 1
    by: dict = {}
    info = {}
    for w in walls:
        lv = _lv(ctx, w)
        t, ux, uy, c, a0, a1 = info[w] = _line(_seg(st.el[w]["params"]))
        by.setdefault((lv, int(t / ANG_STEP) % nang, floor(c / tol)), []).append(w)
    out, seen = [], set()
    for w in walls:
        lv = _lv(ctx, w)
        t, ux, uy, c, a0, a1 = info[w]
        ka, kc = int(t / ANG_STEP) % nang, floor(c / tol)
        for da in (-1, 0, 1):
            for dc in (-1, 0, 1):
                for v in by.get((lv, (ka + da) % nang, kc + dc), ()):
                    if v <= w or (w, v) in seen:
                        continue
                    t2 = info[v][0]
                    if min(abs(t - t2), pi - abs(t - t2)) > ANG_TOL:
                        continue
                    x0, y0, x1, y1 = _seg(st.el[v]["params"])   # v 의 두 끝을 w 의 선에 투영
                    d0, d1 = -uy * x0 + ux * y0 - c, -uy * x1 + ux * y1 - c
                    if max(abs(d0), abs(d1)) > tol:
                        continue
                    b0, b1 = sorted((ux * x0 + uy * y0, ux * x1 + uy * y1))
                    ov = min(a1, b1) - max(a0, b0)
                    if ov > tol:
                        seen.add((w, v))
                        out.append(([w, v], f"같은 선 위에서 {ov:.0f}mm 겹친다"))
    return out


def _dup(ctx, kinds_ids):
    st = ctx.st
    groups: dict = {}
    for k, ids in kinds_ids.items():
        for e in ids:
            p = {x: y for x, y in st.el[e]["params"].items() if x != "name"}
            host = ctx.rels_in("hosts", e) if k in ("door", "finst") else []
            key = (k, _lv(ctx, e), tuple(host), json.dumps(p, sort_keys=True, ensure_ascii=False))
            groups.setdefault(key, []).append(e)
    return [(sorted(g), f"{key[0]} {len(g)}개가 이름 말고 같다") for key, g in groups.items() if len(g) > 1]


def _orphan(ctx, kinds_ids):
    out = []
    for d in kinds_ids.get("door", ()):
        if not ctx.rels_in("hosts", d):
            out.append(([d], "호스트 벽이 없는 문"))
    for k in LEVEL_KINDS:
        for e in kinds_ids.get(k, ()):
            if _lv(ctx, e) is None:
                out.append(([e], f"레벨 관계가 없는 {k}"))
    return out


def _off_level(ctx, walls):
    st = ctx.st
    elev = sorted((st.el[lv]["params"]["elevation"], lv) for lv in st.by_kind.get("level", ()))
    out = []
    for w in walls:
        lv = _lv(ctx, w)
        if lv is None:
            continue
        p = st.el[w]["params"]
        z0 = st.el[lv]["params"]["elevation"]
        b = WT.base(p)
        above = [z for z, x in elev if z > z0]
        if above and z0 + b >= above[0]:
            out.append(([w], f"벽 바닥 {z0 + b:.0f} 이 위 레벨({above[0]:.0f}) 이상"))
        elif b + p["height"] <= 0:
            out.append(([w], "벽 전체가 자기 레벨 아래에 있다"))
    return out


def _space_open(ctx, spaces, walls, tol):
    """공간 경계 변마다 같은 레벨 벽이 덮는 구간을 모아 덮이지 않은 가장 긴 틈을 본다."""
    st = ctx.st
    by_lv: dict = {}
    for w in walls:
        by_lv.setdefault(_lv(ctx, w), []).append(w)
    out = []
    for s in spaces:
        cand = by_lv.get(_lv(ctx, s), [])
        pts = st.el[s]["params"]["boundary"]
        worst = 0.0
        for i in range(len(pts)):
            (ax, ay), (bx, by_) = pts[i][:2], pts[(i + 1) % len(pts)][:2]
            n = hypot(bx - ax, by_ - ay)
            if n <= tol:
                continue
            ux, uy = (bx - ax) / n, (by_ - ay) / n
            cover = []
            for w in cand:
                x0, y0, x1, y1 = _seg(st.el[w]["params"])
                lw = hypot(x1 - x0, y1 - y0)
                if lw <= 0 or abs(ux * (y1 - y0) - uy * (x1 - x0)) / lw > sin(ANG_TOL):
                    continue
                reach = st.el[w]["params"]["thickness"] / 2.0 + tol
                if max(abs(-uy * (x0 - ax) + ux * (y0 - ay)), abs(-uy * (x1 - ax) + ux * (y1 - ay))) > reach:
                    continue
                s0, s1 = sorted((ux * (x0 - ax) + uy * (y0 - ay), ux * (x1 - ax) + uy * (y1 - ay)))
                cover.append((s0 - tol, s1 + tol))
            cover.sort()
            pos, gap = 0.0, 0.0
            for s0, s1 in cover:
                if s0 > pos:
                    gap = max(gap, min(s0, n) - pos)
                pos = max(pos, s1)
                if pos >= n:
                    break
            gap = max(gap, n - pos)
            worst = max(worst, gap)
        if worst > tol:
            out.append(([s], f"경계가 벽으로 닫히지 않았다(가장 긴 틈 {worst:.0f}mm)"))
    return out


@command("model.check", kind="query", risk="read", group="model", summary="모델 품질 점검: 짧은 벽·겹친 벽·중복·고아·레벨 밖·열린 공간 경계",
         args=ModelCheck, since="0.12", idempotent=True, examples=[{}, {"checks": ["overlap_wall", "duplicate"], "level": F.LV1}])
def model_check(ctx, a: ModelCheck):
    st = ctx.st
    if a.level is not None:
        ctx.row(a.level, "level")
    want = set(a.checks or CHECKS)
    kinds_ids = {}
    for k in DUP_KINDS:
        ids = sorted(st.by_kind.get(k, ()))
        if a.level is not None:
            ids = [e for e in ids if _lv(ctx, e) == a.level or (k == "door" and any(_lv(ctx, h) == a.level for h in ctx.rels_in("hosts", e)))]
        kinds_ids[k] = ids
    walls = kinds_ids["wall"]
    found = {
        "short_wall": lambda: _short(ctx, walls, a.tol),
        "overlap_wall": lambda: _overlap(ctx, walls, a.tol),
        "duplicate": lambda: _dup(ctx, kinds_ids),
        "orphan": lambda: _orphan(ctx, kinds_ids),
        "off_level": lambda: _off_level(ctx, walls),
        "space_open": lambda: _space_open(ctx, kinds_ids["space"], walls, a.tol),
    }
    rows, counts = [], {}
    for c in CHECKS:
        if c not in want:
            continue
        got = sorted(found[c](), key=lambda x: x[0])
        counts[c] = len(got)
        rows += [{"check": c, "ids": ids[:20], **({"n": len(ids)} if len(ids) > 20 else {}), "msg": limits.clip_chars(m, MSG_CHARS)} for ids, m in got]
    start = limits.dec_cursor(a.cursor, "int") or 0
    if start > len(rows):
        raise DobimError("SCHEMA_INVALID", "커서가 목록 범위를 벗어났다", path="/cursor")
    base = {"counts": counts, "problems": len(rows)}
    chunk = rows[start:start + a.limit]
    items, cut = limits.fit(chunk, base, cursor_of=lambda i: limits.enc_cursor(start + i + 1))
    nxt = start + len(items)
    return {**base, "items": items, "page": limits.page(limits.enc_cursor(nxt) if nxt < len(rows) else None, len(rows), cut)}
