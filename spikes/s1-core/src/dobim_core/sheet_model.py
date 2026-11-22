# SPDX-License-Identifier: MPL-2.0
"""시트에 얹는 모델 파생 정보: 문·창·벽체 부호와 일람표, 실번호, 단면·입면 기호, 레벨 노트, 재료표.

모델(그래프)을 읽기만 한다(쓰지 않는다). 결과는 sheet.build_sheet 가 받는 spec 의 순수 데이터(리스트·딕셔너리)다.
부호는 모델의 속성 값 묶음(종류·치수·내화·재료)을 정렬한 순서로 매겨 같은 모델이면 항상 같은 부호가 나온다(결정적).
"""
from __future__ import annotations

from math import atan2, pi, sqrt

from dobim_core import drawing as D
from dobim_core import kinds as K
from dobim_core import queries as Q

TAG_OFF = 3.6        # 부호 태그를 벽 면에서 띄우는 거리(용지 mm)
ROOM_TAG_DY = 6.2    # 실번호를 실 무게중심에서 아래로 내리는 거리(용지 mm)


def _ids(g, kind):
    out = []
    for b in range(Q.NB):
        out.extend(Q.bucket_ids(g, kind, b))
    return sorted(out)


def levels(g):
    """[(id, 이름, 높이)] 높이 오름차순."""
    out = []
    for lv in _ids(g, "level"):
        _, elev = g.q(Q.level_info, lv)
        out.append((lv, Q.el(g, lv)["params"]["name"], elev))
    return sorted(out, key=lambda t: (t[2], t[1], t[0]))


def _center(g):
    """건물 중심(벽 축선 중점의 평균). 태그를 바깥쪽·안쪽으로 보낼 방향을 정하는 데 쓴다."""
    pts = []
    for w in D.all_walls(g):
        x1, y1, x2, y2 = Q.el(g, w)["params"]["axis"]
        pts.append(((x1 + x2) / 2.0, (y1 + y2) / 2.0))
    if not pts:
        return (0.0, 0.0)
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def openings(g):
    """호스팅된 문·창 전부: [{cat, id, level, pos, normal, thick, width, height, sill, fire}]. 정렬된 id 순.
    cat: 'D'(문) / 'W'(창). 문은 door 종류와 패밀리 문, 창은 패밀리 창."""
    out = []
    for d in _ids(g, "door"):
        row = g.q(Q.door_row, d)
        host = row[2]
        x, y, c, s, _ = g.q(Q.wall_frame, host)
        if "arc" in Q.el(g, host)["params"]:   # 곡선 벽(W2 ARC-04): 문 자리의 접선 방향
            c, s = g.q(Q.axis_dir, host, row[8])
        out.append({"cat": "D", "id": d, "level": g.q(Q.wall_level, host), "wall": host, "pos": (row[9], row[10]), "dir": (c, s),
                    "thick": Q.el(g, host)["params"]["thickness"], "width": row[5], "height": row[6], "sill": row[7], "fire": row[12] or ""})
    for e in _ids(g, "finst"):
        sp = g.q(Q.ifc_spec, e)
        host = sp.get("host")
        cut = sp.get("cut")
        if not host or not cut or Q.el(g, host) is None or Q.el(g, host)["kind"] != "wall":
            continue
        x, y, c, s, _ = g.q(Q.wall_frame, host)
        off = sp["offset"]
        cat = "W" if sp.get("predefined") == "WINDOW" else "D"
        pos = (x + off * c, y + off * s)
        if "arc" in Q.el(g, host)["params"]:   # 곡선 벽(W2 ARC-04): 호 위 점·접선
            pos, (c, s) = g.q(Q.axis_pt, host, off), g.q(Q.axis_dir, host, off)
        out.append({"cat": cat, "id": e, "level": g.q(Q.wall_level, host), "wall": host, "pos": pos, "dir": (c, s),
                    "thick": Q.el(g, host)["params"]["thickness"], "width": cut["x"][1] - cut["x"][0],
                    "height": cut["z"][1] - cut["z"][0], "sill": sp["elevation"] + cut["z"][0], "fire": ""})
    return out


def opening_marks(ops):
    """(구분, 폭, 높이, 하단, 내화) 묶음을 정렬해 D1, D2 … / W1 … 부호를 매긴다. 반환: {키: 부호}"""
    keys = sorted({(o["cat"], o["width"], o["height"], o["sill"], o["fire"]) for o in ops})
    count: dict[str, int] = {}
    marks = {}
    for k in keys:
        count[k[0]] = count.get(k[0], 0) + 1
        marks[k] = f"{k[0]}{count[k[0]]}"
    return marks


def walls(g):
    """그리는 벽 전부: [{id, level, ext, thick, mat, fire, a, b}]."""
    out = []
    for w in D.all_walls(g):
        r = g.q(Q.wall_row, w)
        x1, y1, x2, y2 = Q.el(g, w)["params"]["axis"]
        out.append({"id": w, "level": g.q(Q.wall_level, w), "lname": r[2], "ext": bool(r[10]), "thick": r[6], "mat": r[11] or "", "fire": r[9] or "",
                    "a": (x1, y1), "b": (x2, y2)})
    return out


def wall_marks(ws):
    keys = sorted({(not w["ext"], w["thick"], w["mat"], w["fire"]) for w in ws})
    return {k: f"WT{i}" for i, k in enumerate(keys, 1)}


def _unit(vx, vy):
    n = sqrt(vx * vx + vy * vy) or 1.0
    return vx / n, vy / n


def plan_tags(g, view_id, kinds, scale_of_view=None):
    """평면 뷰 하나의 부호 태그: {"room": [(x,y,글)], "opening": [...], "wall": [...]} (모델 좌표). 요청한 종류만 만든다."""
    cfg = g.q(D.view_cfg, view_id)
    if cfg is None or cfg[0] != "plan" or cfg[1] is None:
        return {}
    lv, sc = cfg[1], cfg[4]
    out: dict[str, list] = {}
    cx0, cy0 = _center(g)
    if "opening" in kinds:
        ops = openings(g)
        marks = opening_marks(ops)
        items = []
        for o in ops:
            if o["level"] != lv:
                continue
            nx, ny = -o["dir"][1], o["dir"][0]
            if (o["pos"][0] - cx0) * nx + (o["pos"][1] - cy0) * ny < 0:
                nx, ny = -nx, -ny
            off = o["thick"] / 2.0 + TAG_OFF * sc
            items.append((round(o["pos"][0] + nx * off, 3), round(o["pos"][1] + ny * off, 3), marks[(o["cat"], o["width"], o["height"], o["sill"], o["fire"])]))
        out["opening"] = items
    if "wall" in kinds:
        ws = walls(g)
        marks = wall_marks(ws)
        ops = openings(g)
        items = []
        for w in ws:
            if w["level"] != lv:
                continue
            (x1, y1), (x2, y2) = w["a"], w["b"]
            length = sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            ux, uy = _unit(x2 - x1, y2 - y1)
            cuts = sorted((max(0.0, _along(w, o) - o["width"] / 2.0 - 300.0), min(length, _along(w, o) + o["width"] / 2.0 + 300.0))
                          for o in ops if o["wall"] == w["id"])
            best, cur = (0.0, 0.0), 0.0                                   # 열린 곳(개구부 사이)의 가장 긴 구간 가운데에 놓는다
            for a, b in cuts + [(length, length)]:
                if a - cur > best[1] - best[0]:
                    best = (cur, a)
                cur = max(cur, b)
            t = (best[0] + best[1]) / 2.0
            px, py = x1 + ux * t, y1 + uy * t
            nx, ny = -uy, ux
            if (cx0 - px) * nx + (cy0 - py) * ny < 0:
                nx, ny = -nx, -ny                                         # 건물 안쪽으로
            off = w["thick"] / 2.0 + TAG_OFF * sc
            items.append((round(px + nx * off, 3), round(py + ny * off, 3), marks[(not w["ext"], w["thick"], w["mat"], w["fire"])]))
        out["wall"] = items
    if "room" in kinds:
        out["room"] = room_numbers(g, view_id, sc)
    return out


# ---------------------------------------------------------------- 시트 치수(E-04 입면 치수, L-09 복도·계단 폭): 모델을 읽어 값만 만든다
def _num(v: float) -> str:
    from dobim_core import annot as AN   # 치수 문자와 같은 형식(정수면 정수, 아니면 소수 한 자리)
    return AN.fmt_num(v)


ELEV_MERGE = 300.0   # 입면 치수에서 이 간격(mm) 이내의 벽 위치는 한 위치로 묶는다


def elev_dims(g, view_id):
    """입면 뷰의 가로 치수 사슬: 벽 중심선(축선) 위치들 사이 간격과 전체 폭. {"chain": [(s0, s1, 글)], "overall": (s0, s1, 글)} 또는 None.
    보는 방향과 나란한 벽(입면에서 면이 보이는 벽)은 양 끝 위치를, 그에 수직인 벽(옆면이 보이는 벽)은 축선 위치를 낸다. ELEV_MERGE 이내는 한 위치로 본다."""
    cfg = g.q(D.view_cfg, view_id)
    if cfg is None or cfg[0] != "elev":
        return None
    rx, ry = cfg[5], cfg[6]
    xs: list[tuple[float, float]] = []                   # (위치, 무게): 중심선 > 긴 벽의 끝점 > 짧은 벽의 끝점 순으로 묶음의 대표가 된다
    for w in D.all_walls(g):
        x1, y1, x2, y2 = Q.el(g, w)["params"]["axis"]
        ln = sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        if abs(((x2 - x1) * rx + (y2 - y1) * ry) / ln) < 0.1:
            xs.append(((x1 + x2) / 2.0 * rx + (y1 + y2) / 2.0 * ry, ln + 1.0e9))   # 옆면이 보이는 벽의 중심선이 먼저(진짜 중심선), 그다음 긴 벽의 끝점
        else:
            xs += [(x1 * rx + y1 * ry, ln), (x2 * rx + y2 * ry, ln)]
    pos: list[float] = []                                # 가까운(ELEV_MERGE 이내) 위치는 한 묶음으로 보고, 무게가 가장 큰 위치를 대표로 한다(벽 두께 때문에 생기는 160·50mm 간격을 없앤다)
    cluster: list[tuple[float, float]] = []
    for x, ln in sorted(xs):
        if cluster and x - cluster[-1][0] > ELEV_MERGE:
            pos.append(round(max(cluster, key=lambda t: (t[1], -t[0]))[0], 3))
            cluster = []
        cluster.append((x, ln))
    if cluster:
        pos.append(round(max(cluster, key=lambda t: (t[1], -t[0]))[0], 3))
    if len(pos) < 2:
        return None
    chain = [(a, b, _num(b - a)) for a, b in zip(pos, pos[1:])] if len(pos) > 2 else []
    return {"chain": chain, "overall": (pos[0], pos[-1], _num(pos[-1] - pos[0]))}


def plan_dims(g, view_id, kinds):
    """평면 뷰의 폭 치수(L-09): {"corridor": [(x0, y0, x1, y1, 글)], "stair": [...]} 모델 좌표(양 끝점). 복도는 경계 외접 사각형의 짧은 변을 안목으로, 계단은 시작단의 폭을 잰다."""
    cfg = g.q(D.view_cfg, view_id)
    if cfg is None or cfg[0] != "plan" or cfg[1] is None:
        return {}
    out: dict[str, list] = {}
    if "corridor" in kinds:
        items = []
        for s in g.q(D.scope_spaces, view_id):
            poly, name, cat = g.q(D.space_info, s)
            if not K.is_corridor(name, cat):
                continue
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            cx, cy = D.polygon_centroid(poly)
            if max(xs) - min(xs) <= max(ys) - min(ys):
                items.append((round(min(xs), 3), round(cy, 3), round(max(xs), 3), round(cy, 3), _num(max(xs) - min(xs))))
            else:
                items.append((round(cx, 3), round(min(ys), 3), round(cx, 3), round(max(ys), 3), _num(max(ys) - min(ys))))
        out["corridor"] = items
    if "stair" in kinds:
        items = []
        for s in g.q(D.scope_stairs, view_id):
            info = g.q(D.stair_info, s)
            a, b = D._st_pt(info, 0.0, -info[7] / 2.0), D._st_pt(info, 0.0, info[7] / 2.0)
            items.append((round(a[0], 3), round(a[1], 3), round(b[0], 3), round(b[1], 3), _num(info[7])))
        out["stair"] = items
    return out


def _along(w, o):
    """개구부 중심이 벽 시작점에서 축선을 따라 얼마나 떨어져 있는가."""
    (x1, y1), (x2, y2) = w["a"], w["b"]
    ux, uy = _unit(x2 - x1, y2 - y1)
    return (o["pos"][0] - x1) * ux + (o["pos"][1] - y1) * uy


def room_numbers(g, view_id, scale):
    """평면 뷰의 공간에 실번호를 매긴다: 좌상단에서 시작해 시계방향. 번호 = 층 번호 x 100 + 순번. 반환: [(x, y, 글)]"""
    cfg = g.q(D.view_cfg, view_id)
    lvs = levels(g)
    idx = {lv: i + 1 for i, (lv, _, _) in enumerate(lvs)}
    spaces = [(s, D.polygon_centroid(g.q(D.space_info, s)[0])) for s in g.q(D.scope_spaces, view_id)]
    if not spaces:
        return []
    mx = sum(c[0] for _, c in spaces) / len(spaces)
    my = sum(c[1] for _, c in spaces) / len(spaces)
    ang0 = {s: atan2(c[1] - my, c[0] - mx) for s, c in spaces}
    corner = 0.75 * pi                                                   # 좌상단 방향(모델 y 가 위쪽)

    def gap(a, b):
        d = abs(a - b) % (2.0 * pi)
        return min(d, 2.0 * pi - d)
    first = min(spaces, key=lambda it: (round(gap(ang0[it[0]], corner), 9), it[0]))[0]      # 좌상단에 가장 가까운 공간이 1번

    def key(item):
        s = item[0]
        return (round((ang0[first] - ang0[s]) % (2.0 * pi), 9), s)       # 1번에서 시계방향으로 돈 각도
    out = []
    for n, (s, (cx, cy)) in enumerate(sorted(spaces, key=key), 1):
        out.append((round(cx, 3), round(cy - ROOM_TAG_DY * scale, 3), f"{idx.get(cfg[1], 0)}{n:02d}"))
    return out


def view_marks(g):
    """모델의 단면 뷰(절단선)와 입면 뷰(방향)를 평면에 그릴 기호로: (sections, elevs)."""
    secs, elevs = [], []
    for v in D.all_views(g):
        cfg = g.q(D.view_cfg, v)
        p = Q.el(g, v)["params"]
        label = (p["name"].split() or ["?"])[0][:4]
        if cfg is None or "parent" in p:   # 콜아웃 뷰(W1 1-C 2차)는 절단선·방향 기호가 아니라 부모 뷰의 콜아웃 기호로 그린다
            continue
        if cfg[0] == "sec":
            secs.append({"id": v, "label": label, "p1": tuple(p["p1"]), "p2": tuple(p["p2"]), "dir": (cfg[3], cfg[4])})
        elif cfg[0] == "elev":
            elevs.append({"id": v, "label": label, "dir": (cfg[3], cfg[4])})
    return secs, elevs


# ---------------------------------------------------------------- 색인 시트 표
def level_rows(g, gl_name=None):
    lvs = levels(g)
    if not lvs:
        return []
    ref = next((e for _, n, e in lvs if n == gl_name), lvs[0][2]) if gl_name else lvs[0][2]
    rows = []
    for _, name, elev in lvs:
        d = elev - ref
        rows.append([name, f"{elev:g}", "±0" if abs(d) < 1e-9 else f"{d:+g}", "GL 기준 레벨" if abs(d) < 1e-9 else ""])
    return rows


def opening_rows(g):
    ops = openings(g)
    marks = opening_marks(ops)
    lname = {lv: n for lv, n, _ in levels(g)}
    groups: dict[tuple, list] = {}
    for o in ops:
        groups.setdefault((o["cat"], o["width"], o["height"], o["sill"], o["fire"]), []).append(o)
    rows = []
    for k in sorted(groups, key=lambda k: marks[k].rjust(6)):
        v = groups[k]
        lv = ", ".join(sorted({lname.get(o["level"], "?") for o in v}))
        rows.append([marks[k], "문" if k[0] == "D" else "창", f"{k[1]:g}", f"{k[2]:g}", f"{k[3]:g}", k[4] or "-", str(len(v)), lv])
    return rows


def wall_rows(g):
    ws = walls(g)
    marks = wall_marks(ws)
    groups: dict[tuple, list] = {}
    for w in ws:
        groups.setdefault((not w["ext"], w["thick"], w["mat"], w["fire"]), []).append(w)
    rows = []
    for k in sorted(groups):
        v = groups[k]
        lv = ", ".join(sorted({w["lname"] for w in v}))
        rows.append([marks[k], "외벽" if not k[0] else "내벽", f"{k[1]:g}", k[2] or "(미지정)", k[3] or "-", str(len(v)), lv])
    return rows


def material_rows(g):
    """벽 재료·두께·내화 성능 묶음. 실내 마감은 모델에 입력이 없어 벽체 자체의 재료만 적는다."""
    groups: dict[tuple, list] = {}
    for w in walls(g):
        groups.setdefault(("외벽" if w["ext"] else "내벽", w["mat"] or "(미지정)", w["thick"], w["fire"]), []).append(w)
    rows = []
    for k in sorted(groups):
        v = groups[k]
        rows.append([k[0], k[1], f"{k[2]:g}", k[3] or "-", str(len(v)), ", ".join(sorted({w["lname"] for w in v}))])
    return rows
