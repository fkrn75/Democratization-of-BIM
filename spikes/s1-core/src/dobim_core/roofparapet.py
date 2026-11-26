# SPDX-License-Identifier: MPL-2.0
"""평지붕 파라펫(W2 스물두 번째 PR, ARC-19·ARC-20 후속)의 순수 형상.

지붕 params.parapet = {"height": 지붕 윗면 위로 올라온 높이 mm, "thickness": 두께 mm(처마 윤곽에서 안쪽으로), "edges": [외곽 변 번호, ...](오름차순·겹침 없음)}.
평지붕 형상(roofflat)이 돌려주는 처마 윤곽(outline, 반시계)의 변(현)마다, 그 현이 속한 외곽 변(eave_src)이 edges 에 있으면 파라펫 조각 하나를 만든다
(원호 변은 그 호의 모든 현에 조각). 조각 = 윤곽 변을 바깥 면으로 두께만큼 안쪽으로 민 평행사변이 아닌 사다리꼴 기둥이다.

  * 이웃한 현도 파라펫이면 두 안쪽 선의 교점(마이터)으로 맞물린다: 두 조각이 같은 모서리 면을 공유하고 겹치지 않는다
  * 이웃한 현이 파라펫이 아니면(일부 변만 고른 경우) 변에 수직으로 끝낸다(끝 면이 보인다)
  * 조각의 바닥은 지붕 윗면(평면 z = a x + b y + c), 윗면은 바닥보다 height 위다(경사 윗면이면 같은 높이로 따라간다)

조각 = (이름표 "parapet:<외곽 변>:<윤곽 변>", "parapet", 평면 사각형 4점(반시계: 안쪽 시작 -> 바깥 시작 -> 바깥 끝 -> 안쪽 끝), 바닥 z 4개, 윗면 z 4개)로
처마 부속 조각(roofeaves)과 같은 꼴이라 도면(모서리·단면·평면 윤곽)과 IFC(닫힌 육면체)가 같은 함수를 쓴다.
이 모듈은 kinds 를 import 하지 않는다(kinds 가 roof_geom 과 이 모듈을 쓴다). libm 을 쓰지 않는다(sqrt 와 사칙연산뿐이라 결정적이다).
"""
from __future__ import annotations

from math import sqrt

PARAPET_H_MAX = 3000.0     # 파라펫 높이 상한 mm(지붕 슬래브 파라펫과 같다)
PARAPET_T_MAX = 1000.0     # 파라펫 두께 상한 mm(지붕 슬래브 파라펫과 같다)
PIECES_MAX = 512           # 지붕 하나의 파라펫 조각 수 상한(호를 펼친 현 수 이하)
TOL = 1e-6                 # 겹침·교차 판정 허용(mm, 면적은 mm²)


def _unit(dx, dy):
    ln = sqrt(dx * dx + dy * dy)
    return (dx / ln, dy / ln) if ln > 0.0 else (0.0, 0.0)


def _area2(poly):
    n = len(poly)
    return sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))


def _orient(p, q, r):
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _proper(a, b, c, d) -> bool:
    """선분 ab 와 cd 가 서로의 안쪽에서 엇갈리는가(끝점 접촉·공선 겹침은 아니다)."""
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    return ((o1 > TOL and o2 < -TOL) or (o1 < -TOL and o2 > TOL)) and ((o3 > TOL and o4 < -TOL) or (o3 < -TOL and o4 > TOL))


def _locate(pt, poly) -> int:
    """점이 다각형 안(1)·경계 위(0, 허용 TOL)·밖(-1)."""
    x, y = pt
    n = len(poly)
    inside = False
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ln = sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
        if ln > 0.0 and abs(_orient(a, b, pt)) <= TOL * ln and min(a[0], b[0]) - TOL <= x <= max(a[0], b[0]) + TOL and min(a[1], b[1]) - TOL <= y <= max(a[1], b[1]) + TOL:
            return 0
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) / (b[1] - a[1]) * (b[0] - a[0]):
            inside = not inside
    return 1 if inside else -1


def _bbox(poly):
    xs, ys = [q[0] for q in poly], [q[1] for q in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(p, q) -> bool:
    """볼록 사각형 p, q 의 안쪽이 겹치는가(경계 접촉·공유 모서리는 겹침이 아니다)."""
    a, b = _bbox(p), _bbox(q)
    if a[0] >= b[2] - TOL or b[0] >= a[2] - TOL or a[1] >= b[3] - TOL or b[1] >= a[3] - TOL:
        return False
    for i in range(4):
        for j in range(4):
            if _proper(p[i], p[(i + 1) % 4], q[j], q[(j + 1) % 4]):
                return True
    # 한 사각형이 다른 사각형을 통째로 품은 경우(서로 엇갈리는 변이 없다): 무게중심이 안쪽이면 겹친다
    cp = (sum(x[0] for x in p) / 4.0, sum(x[1] for x in p) / 4.0)
    cq = (sum(x[0] for x in q) / 4.0, sum(x[1] for x in q) / 4.0)
    return _locate(cp, q) == 1 or _locate(cq, p) == 1


def plan_quads(outline, src, edges, t):
    """(조각 평면 사각형들 {윤곽 변 k: [안쪽 시작, 바깥 시작, 바깥 끝, 안쪽 끝]}) 또는 이유 문장. outline 은 반시계 처마 윤곽, src[k] = 윤곽 변 k 의 외곽 변 번호."""
    m = len(outline)
    es = set(edges)
    sel = [src[k] in es for k in range(m)]
    nrm = []
    for k in range(m):
        a, b = outline[k], outline[(k + 1) % m]
        u = _unit(b[0] - a[0], b[1] - a[1])
        nrm.append((-u[1], u[0]))   # 안쪽(왼쪽) 법선
    quads = {}
    for k in range(m):
        if not sel[k]:
            continue
        a, b = outline[k], outline[(k + 1) % m]
        p, n_ = (k - 1) % m, (k + 1) % m

        def inner(at, other, here=nrm[k]):
            if not sel[other]:   # 이웃이 파라펫이 아니면 변에 수직으로 끝낸다. 그 끝점이 지붕 밖이면(이웃 변과 예각을 이루거나 호의 현이 접선에서 기운 경우)
                foot = (at[0] + t * here[0], at[1] + t * here[1])   # 안쪽 선이 이웃 윤곽 변의 직선과 만나는 점에서 끝낸다(끝 면이 이웃 변을 따른다)
                if _locate(foot, outline) >= 0:
                    return foot
                c, d = outline[other], outline[(other + 1) % m]
                u = _unit(b[0] - a[0], b[1] - a[1])
                v = (d[0] - c[0], d[1] - c[1])
                det = u[0] * v[1] - u[1] * v[0]
                if abs(det) < 1e-9:
                    return foot
                s = ((c[0] - foot[0]) * v[1] - (c[1] - foot[1]) * v[0]) / det
                return (foot[0] + s * u[0], foot[1] + s * u[1])
            o = nrm[other]
            den = 1.0 + o[0] * here[0] + o[1] * here[1]
            if den < 1e-6:
                return None   # 거의 180도로 되꺾이는 꼭짓점: 마이터가 무한히 길다
            return (at[0] + t * (o[0] + here[0]) / den, at[1] + t * (o[1] + here[1]) / den)
        ma, mb = inner(a, p), inner(b, n_)
        if ma is None or mb is None:
            return f"윤곽 변 {k} 근처에서 두께로 안쪽 모서리를 만들 수 없다(되꺾이는 꼭짓점)"
        if (b[0] - a[0]) * (mb[0] - ma[0]) + (b[1] - a[1]) * (mb[1] - ma[1]) <= 0.0:
            return f"두께가 윤곽 변 {k} 에 비해 너무 두꺼워 파라펫이 접힌다"
        q = [ma, a, b, mb]
        if _area2(q) <= TOL:
            return f"윤곽 변 {k} 의 파라펫 조각 넓이가 0 이다"
        quads[k] = q
    return quads


def problem(outline, src, edges, t, drains=()) -> str | None:
    """파라펫이 쓸 수 있는가(안 되면 이유 문장): 안쪽 모서리가 만들어지고, 조각이 처마 윤곽 안에 들며 서로 겹치지 않고, 배수구를 덮지 않는다."""
    r = plan_quads(outline, src, edges, t)
    if isinstance(r, str):
        return r
    if len(r) > PIECES_MAX:
        return f"파라펫 조각이 {PIECES_MAX}개를 넘는다"
    if not r:
        return "고른 변에 파라펫 조각이 없다"
    m = len(outline)
    ks = sorted(r)
    for k in ks:
        q = r[k]
        for pt in (q[0], q[3]):   # 안쪽 두 점은 윤곽 안(또는 경계 위)에 있어야 한다
            if _locate(pt, outline) < 0:
                return f"파라펫이 윤곽 변 {k} 에서 지붕 밖으로 나온다(두께가 지붕에 비해 크거나 모서리가 뾰족하다)"
        for e in range(m):   # 안쪽 변이 처마 윤곽 변과 엇갈리지 않는다
            if e != k and _proper(q[3], q[0], outline[e], outline[(e + 1) % m]):
                return f"파라펫 안쪽 변이 윤곽 변 {e} 와 엇갈린다(두께가 지붕에 비해 너무 두껍다)"
    for i, k in enumerate(ks):   # 서로 겹치지 않는다
        for k2 in ks[i + 1:]:
            if _overlap(r[k], r[k2]):
                return f"파라펫 조각(윤곽 변 {k}, {k2})이 겹친다(두께가 지붕에 비해 너무 두껍다)"
    for i, d in enumerate(drains):
        if any(_locate(d, r[k]) >= 0 for k in ks):
            return f"{i + 1}번 배수구가 파라펫 위에 있다"
    return None


def pieces(gm, par) -> list:
    """파라펫 조각들 [(이름표, "parapet", 평면 사각형 4점, 바닥 z 4, 윗면 z 4)]. gm = roof_geom(평지붕, 레벨 기준 z), par = params.parapet. 윤곽 변 순서(결정적)."""
    out = gm["outline"]
    src = gm["eave_src"]
    r = plan_quads([tuple(q) for q in out], src, par["edges"], float(par["thickness"]))
    if isinstance(r, str):
        raise ValueError(r)
    a, b, c = gm["planes"][0]
    h = float(par["height"])
    res = []
    for k in sorted(r):
        q = tuple((float(x), float(y)) for x, y in r[k])
        zb = tuple(a * x + b * y + c for x, y in q)
        res.append((f"parapet:{src[k]}:{k}", "parapet", q, zb, tuple(z + h for z in zb)))
    return res


def summary(pcs) -> dict:
    """조각 수·바깥 면 길이 합(mm)·부피 합(mm³)·바깥 면 넓이 합(mm², 높이 x 길이). 조각이 없으면 빈 dict."""
    if not pcs:
        return {}
    ln = vol = 0.0
    for _lb, _kd, q, zb, zt in pcs:
        d = sqrt((q[2][0] - q[1][0]) ** 2 + (q[2][1] - q[1][1]) ** 2)
        ln += d
        # 윗면·바닥 사이 높이는 네 점 모두 같다(같은 height). 바닥 평면 위 넓이 x 높이가 부피(경사 윗면도 수직 기둥이라 같다)
        vol += abs(_area2(list(q))) / 2.0 * (zt[0] - zb[0])
    h = pcs[0][4][0] - pcs[0][3][0]
    return {"pieces": len(pcs), "length": round(ln, 3), "height": round(h, 3), "volume": round(vol, 3), "area": round(ln * h, 3)}
