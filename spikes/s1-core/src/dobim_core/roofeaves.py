# SPDX-License-Identifier: MPL-2.0
"""처마 부속(W2 여덟 번째 PR, ARC-20)의 순수 형상: 지붕 변을 따라가는 fascia(처마 널)·gutter(홈통)·soffit(처마 반자).

지붕 params.eaves = {종류: {"edges": [외곽 변 번호, ...], 치수}}. 외곽 변 i 의 처마 선은 처마 윤곽(roof_geom 의 outline) 가운데 그 변을 바깥으로
내민 선 위에 있는 변들이다(외곽선 지붕의 오목 꼭짓점 연결 변은 어느 변의 처마도 아니다). 처마 선의 윗면 높이는 지붕 둘레(perimeter)를 따른다.

  * fascia: 처마 선 바깥에 붙는 널(폭 width, 지붕 윗면 모서리에서 아래로 depth). 박공 변(경사 처마)이면 경사를 따라간다
  * gutter: fascia(있으면) 바깥의 상자 홈통(폭 width, 깊이 depth). 윗면 = 처마 윗면 - GUTTER_DROP. 수평 처마 조각에만 단다
  * soffit: 벽선(외곽 변)과 처마 선 사이의 수평 판(두께 thickness). 윗면 = 처마 끝 지붕 아랫면(처마 높이 - 지붕 두께). 수평 처마이고 내밀기가 있는 변만
모서리: 이웃한 처마 선(처마 윤곽에서 이어진 변) 둘 다 같은 종류를 가지면 바깥 모서리를 두 내민 선의 교점으로 잇는다(연귀). 아니면 직각으로 끝낸다.

조각 = (이름표, 종류, 평면 사각형 4점(반시계), 바닥 z 4개, 윗면 z 4개). 사각형의 대응 꼭짓점 위로 수직 모서리가 서는 볼록 육면체다
(윗면·바닥은 처마 선 방향으로만 기울어 평면이다). 도면(모서리·단면)과 IFC(IfcFacetedBrep)가 같은 값을 쓴다.
이 모듈은 kinds 를 import 하지 않는다(kinds 가 roof_geom 을 준다).
"""
from __future__ import annotations

from math import sqrt

GUTTER_DROP = 30.0     # 홈통 윗면이 처마 윗면보다 내려가는 거리 mm
LINE_TOL = 0.5         # 점이 처마 선 위에 있다고 보는 거리 mm
FLAT_TOL = 1.0         # 수평 처마로 보는 높이 차 mm
PIECES_MAX = 2000      # 지붕 하나의 처마 부속 조각 수 상한


def _unit(dx, dy):
    ln = sqrt(dx * dx + dy * dy)
    return (dx / ln, dy / ln) if ln > 0.0 else (0.0, 0.0)


def _isect(p, u, q, v):
    """직선 p + s u 와 q + t v 의 교점(평행이면 None)."""
    det = u[0] * v[1] - u[1] * v[0]
    if abs(det) < 1e-9:
        return None
    s = ((q[0] - p[0]) * v[1] - (q[1] - p[1]) * v[0]) / det
    return (p[0] + s * u[0], p[1] + s * u[1])


def _area2(poly):
    n = len(poly)
    return sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))


def eave_runs(boundary, overhangs, outline):
    """외곽 변마다 처마 윤곽 위의 처마 변 번호들: {외곽 변 i: [윤곽 변 k, ...]}. 윤곽 변 k 의 두 끝이 변 i 를 o_i 만큼 바깥으로 내민 선 위에 있다."""
    n, m = len(boundary), len(outline)
    out = {}
    for i in range(n):
        a, b = boundary[i], boundary[(i + 1) % n]
        u = _unit(b[0] - a[0], b[1] - a[1])
        nv = (-u[1], u[0])   # 안쪽 법선
        c = nv[0] * a[0] + nv[1] * a[1] - overhangs[i]
        for k in range(m):
            p, q = outline[k], outline[(k + 1) % m]
            if (abs(nv[0] * p[0] + nv[1] * p[1] - c) <= LINE_TOL and abs(nv[0] * q[0] + nv[1] * q[1] - c) <= LINE_TOL
                    and (q[0] - p[0]) * u[0] + (q[1] - p[1]) * u[1] > LINE_TOL):
                out.setdefault(i, []).append(k)
    return out


def _top_line(gm, p, q):
    """윤곽 변 p -> q 위의 둘레 점들 [(t, (x, y, z))] (t 오름차순, 양 끝 포함)."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    l2 = dx * dx + dy * dy
    pts = []
    for r in gm["perimeter"]:
        t = ((r[0] - p[0]) * dx + (r[1] - p[1]) * dy) / l2
        if -1e-9 <= t <= 1.0 + 1e-9:
            cx, cy = p[0] + t * dx - r[0], p[1] + t * dy - r[1]
            if cx * cx + cy * cy <= LINE_TOL * LINE_TOL:
                pts.append((min(1.0, max(0.0, t)), (r[0], r[1], r[2])))
    pts.sort()
    out = []
    for t, r in pts:
        if out and t - out[-1][0] <= 1e-9:
            continue
        out.append((t, r))
    return out


def pieces(p, gm, overhangs):
    """처마 부속 조각들(정해진 순서) [(이름표, 종류, 평면 사각형, 바닥 z 4, 윗면 z 4)]. p 는 지붕 params(eaves·thickness·boundary), gm 은 roof_geom."""
    ev = p.get("eaves")
    if not ev:
        return []
    b = [tuple(q) for q in p["boundary"]]
    outl = [tuple(q) for q in gm["outline"]]
    m = len(outl)
    runs = eave_runs(b, overhangs, outl)
    owner = {k: i for i, ks in runs.items() for k in ks}   # 윤곽 변 -> 외곽 변
    out = []
    t_roof = float(p["thickness"])
    for kind in ("fascia", "gutter", "soffit"):
        d = ev.get(kind)
        if d is None:
            continue
        sel = set(d["edges"])
        has = {k for k, i in owner.items() if i in sel}   # 이 종류를 단 윤곽 변
        f_has = {k for k, i in owner.items() if i in set(ev.get("fascia", {}).get("edges", ()))}
        for k in sorted(has):
            i = owner[k]
            p0, p1 = outl[k], outl[(k + 1) % m]
            u = _unit(p1[0] - p0[0], p1[1] - p0[1])
            nv = (u[1], -u[0])   # 바깥 법선
            line = _top_line(gm, p0, p1)
            if len(line) < 2:
                continue
            if kind == "soffit":
                zs = [r[2] for _, r in line]
                if max(zs) - min(zs) > FLAT_TOL or overhangs[i] <= LINE_TOL:
                    continue
                a, c = b[i], b[(i + 1) % len(b)]
                quad = [p0, p1, (c[0], c[1]), (a[0], a[1])]
                if _area2(quad) < 0:
                    quad.reverse()
                zt = min(zs) - t_roof
                zb = zt - float(d["thickness"])
                out.append((f"soffit:{i}:{k}", kind, tuple(quad), (zb,) * 4, (zt,) * 4))
                continue
            w = float(d["width"])
            off = float(ev["fascia"]["width"]) if kind == "gutter" and k in f_has else 0.0
            prv, nxt = (k - 1) % m, (k + 1) % m

            def corner(at, other_k, inner_off, outer_off, start, p0=p0, p1=p1, u=u, nv=nv):
                """처마 선 끝(at)의 (안쪽, 바깥) 두 점. 이웃 윤곽 변도 같은 종류면 연귀(내민 선끼리 교점), 아니면 직각."""
                base_in = (at[0] + nv[0] * inner_off, at[1] + nv[1] * inner_off)
                base_out = (at[0] + nv[0] * outer_off, at[1] + nv[1] * outer_off)
                if other_k not in has:
                    return base_in, base_out
                q0, q1 = outl[other_k], outl[(other_k + 1) % m]
                uo = _unit(q1[0] - q0[0], q1[1] - q0[1])
                no = (uo[1], -uo[0])
                o_off = (float(ev["fascia"]["width"]) if kind == "gutter" and other_k in f_has else 0.0)
                ri = _isect(base_in, u, (q0[0] + no[0] * o_off, q0[1] + no[1] * o_off), uo)
                ro = _isect(base_out, u, (q0[0] + no[0] * (o_off + w), q0[1] + no[1] * (o_off + w)), uo)
                turn = (uo[0] * u[1] - uo[1] * u[0]) if start else (u[0] * uo[1] - u[1] * uo[0])
                if ri is None or ro is None or turn <= 0.0:
                    return base_in, base_out   # 오목하게 꺾인 이웃은 연귀하지 않는다(안쪽에서 겹친다)
                return ri, ro
            s_in, s_out = corner(p0, prv, off, off + w, True)
            e_in, e_out = corner(p1, nxt, off, off + w, False)
            for j in range(len(line) - 1):
                (ta, ra), (tb, rb) = line[j], line[j + 1]
                if kind == "gutter" and abs(ra[2] - rb[2]) > FLAT_TOL:
                    continue   # 홈통은 수평 처마에만
                ia = s_in if j == 0 else (ra[0] + nv[0] * off, ra[1] + nv[1] * off)
                oa = s_out if j == 0 else (ra[0] + nv[0] * (off + w), ra[1] + nv[1] * (off + w))
                ib = e_in if j == len(line) - 2 else (rb[0] + nv[0] * off, rb[1] + nv[1] * off)
                ob = e_out if j == len(line) - 2 else (rb[0] + nv[0] * (off + w), rb[1] + nv[1] * (off + w))
                quad = [ia, oa, ob, ib]   # 반시계(바깥 법선이 진행 방향 오른쪽): 안a -> 밖a -> 밖b -> 안b
                za, zb_ = ra[2], rb[2]
                if kind == "gutter":
                    za, zb_ = za - GUTTER_DROP, zb_ - GUTTER_DROP
                dep = float(d["depth"])
                zt = (za, za, zb_, zb_)
                if _area2(quad) <= 1e-6:
                    continue   # 연귀가 뒤집힐 만큼 짧은 조각
                out.append((f"{kind}:{i}:{k}:{j}", kind, tuple(quad), tuple(z - dep for z in zt), zt))
                if len(out) > PIECES_MAX:
                    raise ValueError(f"처마 부속 조각이 {PIECES_MAX}개를 넘는다")
    return out


def piece_mesh(pc):
    """조각 -> (꼭짓점 8개 [(x,y,z)], 면들 [[(번호...)]]) : 바깥을 향하는 볼록 육면체(바닥 4, 윗면 4)."""
    _, _, quad, zb, zt = pc
    verts = [(q[0], q[1], z) for q, z in zip(quad, zb)] + [(q[0], q[1], z) for q, z in zip(quad, zt)]
    faces = [[(3, 2, 1, 0)], [(4, 5, 6, 7)]]
    for i in range(4):
        j = (i + 1) % 4
        faces.append([(i, j, 4 + j, 4 + i)])
    return verts, faces


def piece_edges(pc):
    """조각의 12 모서리 [((x,y,z), (x,y,z))]."""
    verts, _ = piece_mesh(pc)
    out = []
    for i in range(4):
        j = (i + 1) % 4
        out += [(verts[i], verts[j]), (verts[4 + i], verts[4 + j]), (verts[i], verts[4 + i])]
    return out


def summary(pcs) -> dict:
    """종류마다 조각 수와 길이 합(mm, 처마 선 방향)."""
    out = {}
    for _lb, kind, quad, _zb, zt in pcs:
        d = out.setdefault(kind, {"pieces": 0, "length": 0.0})
        d["pieces"] += 1
        if kind == "soffit":
            d["length"] += sqrt((quad[1][0] - quad[0][0]) ** 2 + (quad[1][1] - quad[0][1]) ** 2)
        else:
            d["length"] += sqrt((quad[3][0] - quad[0][0]) ** 2 + (quad[3][1] - quad[0][1]) ** 2 + (zt[3] - zt[0]) ** 2)   # 안쪽(처마 선) 길이
    for d in out.values():
        d["length"] = round(d["length"], 3)
    return out
