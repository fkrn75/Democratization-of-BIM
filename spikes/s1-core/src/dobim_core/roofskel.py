# SPDX-License-Identifier: MPL-2.0
"""외곽선 지붕(roof form "footprint", W2 여덟 번째 PR, ARC-19)의 순수 형상: 가중 직선 골격(weighted straight skeleton) 파면 전파.

입력: 외곽(벽선) 꼭짓점(반시계), 변마다 물매 tan(0 = 물매를 정하지 않는 변: 박공·수직), 변마다 처마 내밀기, 처마 높이(base_offset).
변 i 의 경사면은 외곽 변 i 에서 안쪽으로 물매대로 오르는 평면 h_i(p) = z0 + k_i * d_i(p) 다(d_i = 외곽 변 i 에서 안쪽까지 거리).
처마 끝은 변마다 내민 선 L_i'(변 i 를 바깥으로 o_i 평행 이동) 이고 그 높이는 z0 - k_i o_i 다.

방법(파면 전파, 높이 h 를 시간으로 쓴다):
  * 처마 윤곽 P' = 내민 선들의 교점(볼록 꼭짓점은 늘 교점, 오목 꼭짓점은 두 처마 높이가 같으면 교점이고 다르면 골 연장선 위의 두 점과 그 사이의
    고정 연결 변: 처마 높이가 다른 두 면이 골에서 끊기지 않게 한다).
  * 변 j 의 선은 높이 h 에서 n_j . p = c_j + w_j * max(0, h - hs_j) (n = 안쪽 법선, w = 1/k, hs = 처마 높이). 물매가 없는 변(박공)·연결 변은 움직이지 않는다.
    처마 높이에 닿기 전의 경사 변도 움직이지 않는다(그 아래는 지붕이 아니다).
  * 꼭짓점 = 이웃 두 선의 교점. 사건: 변 길이가 0(변 사건: 두 꼭짓점이 합쳐진다), 오목 꼭짓점이 다른 변에 닿음(분할 사건: 고리가 둘로 나뉜다),
    경사 변이 처마 높이에 닿음(출발 사건). 같은 높이의 사건은 한꺼번에 정리한다(합치기 -> 뾰족한 0 폭 가시 자르기 -> 분할, 바뀌지 않을 때까지).
  * 변 j 의 경사면 = 그 변이 쓸고 지나간 자리. 꼭짓점 궤적(골격의 호)과 처마 선, 고리가 납작하게 사라질 때의 마지막 선(용마루)을 이어 면 고리를 만든다.
결과의 검사(면이 외곽을 빈틈없이 덮는가, 면 경계에서 높이가 이어지는가)와 용마루·추녀마루·골 분류, 처마 둘레, 물매 화살표는
roofpoly 의 마무리(_finish)를 그대로 쓴다. 어긋나면 이유 문장으로 거절한다.
같은 입력이면 같은 결과다(사칙연산·sqrt 만 쓴다, 사건 처리 순서는 고리·꼭짓점 순서로 정해진다). 이 모듈은 kinds 를 import 하지 않는다.
알고리즘은 직선 골격의 파면 전파 정의(Aichholzer-Aurenhammer 1995, 가중 골격·높이마다 속도가 바뀌는 변은 Kelly-Wonka 2011 의 절차적 돌출 설명)를
보고 새로 쓴 것이다.
"""
from __future__ import annotations

from functools import lru_cache
from math import sqrt

from dobim_core import roofpoly as RP

PTS_MAX = 128           # 외곽 꼭짓점 수 상한
FACES_MAX = 256         # 경사면 수 상한
SIDE_MIN = 1.0          # 외곽 변 최소 길이 mm(짧은 변 허용)
PAR_SIN = 1e-3          # 이웃 두 변이 한 직선으로 보이는 사잇각 sin
EVENTS_MAX = 20000      # 사건 처리 상한(넘으면 거절)
_INF = float("inf")


def _norm(dx, dy):
    ln = sqrt(dx * dx + dy * dy)
    return dx / ln, dy / ln, ln


def _isect(n0, c0, n1, c1):
    """두 직선 n0.p = c0, n1.p = c1 의 교점(평행이면 None)."""
    det = n0[0] * n1[1] - n0[1] * n1[0]
    if abs(det) < 1e-12:
        return None
    return ((c0 * n1[1] - c1 * n0[1]) / det, (n0[0] * c1 - n1[0] * c0) / det)


class _Sweep:
    """처마 윤곽 P' 위의 파면 전파. lines[j] = (nx, ny, c, w, hs)."""

    def __init__(self, pts, lines, eps):
        self.lines = lines
        self.eps = eps
        self.h = 0.0
        self.pend = None
        self.stall = False   # 사건 높이가 float 로 더 오르지 않는다(W2 열두 번째 PR, _settle 참고)
        self.ptol = 100.0 * eps   # 거의 한 점에서 만나는 선들의 어긋남 허용(반올림된 좌표가 만드는 크기, 40m 지붕이면 0.4mm)
        self.snap = RP._Snap(tol=max(RP.SNAP, eps * 4.0))
        self.arcs = []      # (in 선, out 선, 시작 점 key, 끝 점 key, in 선이 움직였나, out 선이 움직였나)
        self.bottoms = []   # (선, 시작 key, 끝 key): 선이 출발할 때 그 선 위 파면 변(면의 아랫변 = 처마 선 또는 출발 높이의 선)
        self.tops = []      # (선, 시작 key, 끝 key): 고리가 사라질 때의 마지막 선(면의 윗변)
        self.loops = []
        n = len(pts)
        loop = []
        for i in range(n):   # 꼭짓점 i 는 선 i-1 과 선 i 사이(P' 의 변 i 는 꼭짓점 i -> i+1)
            loop.append(self._vertex((i - 1) % n, i, pts[i], None))
        self.loops.append(loop)

    # ---- 선·꼭짓점
    def C(self, j, h):
        nx, ny, c, w, hs = self.lines[j]
        return c + w * (h - hs) if (w > 0.0 and h > hs) else c

    def speed(self, j, h):
        """높이 h 바로 위에서의 선 j 속도."""
        nx, ny, c, w, hs = self.lines[j]
        return w if (w > 0.0 and h >= hs - self.eps * 1e-3) else 0.0

    def _vertex(self, a, b, p, key):
        k = self.snap.key(p) if key is None else key
        return {"a": a, "b": b, "p": (p[0], p[1]), "k": k, "v": (0.0, 0.0), "m": None}

    def _dir(self, j):
        nx, ny = self.lines[j][0], self.lines[j][1]
        return (ny, -nx)   # 반시계 외곽에서 안쪽 법선의 오른쪽 = 진행 방향

    def _velocity(self, vt, h):
        la, lb = self.lines[vt["a"]], self.lines[vt["b"]]
        sa, sb = self.speed(vt["a"], h), self.speed(vt["b"], h)
        det = la[0] * lb[1] - la[1] * lb[0]
        if abs(det) < 1e-9:
            if la[0] * lb[0] + la[1] * lb[1] > 0.0:   # 같은 방향 평행(한 직선): 같은 속도면 선과 함께 수직으로 움직인다
                if abs(sa - sb) > 1e-12:
                    raise _Reject("한 직선 위에 이어진 두 변의 물매나 처마 높이가 달라 경사면을 만들 수 없다")
                return (la[0] * sa, la[1] * sa)
            return (0.0, 0.0)   # 반대 방향 평행: 0 폭 가시(정리 단계가 자른다)
        return ((sa * lb[1] - sb * la[1]) / det, (la[0] * sb - lb[0] * sa) / det)

    def _pos(self, vt, h, h0):
        """높이 h 의 꼭짓점 위치: 두 선의 교점(평행이면 h0 위치에서 속도로)."""
        la, lb = self.lines[vt["a"]], self.lines[vt["b"]]
        q = None
        det = la[0] * lb[1] - la[1] * lb[0]
        if abs(det) >= 1e-6:
            q = _isect(la, self.C(vt["a"], h), lb, self.C(vt["b"], h))
        if q is None:
            t = h - h0
            q = (vt["p"][0] + vt["v"][0] * t, vt["p"][1] + vt["v"][1] * t)
        return q

    def _reflex(self, vt) -> bool:
        ua, ub = self._dir(vt["a"]), self._dir(vt["b"])
        return ua[0] * ub[1] - ua[1] * ub[0] < -1e-12

    def _meet(self, vts, h, extra=()):
        """여러 꼭짓점이 한 점에서 만날 때의 위치(W2 아홉 번째 PR). 움직이는 꼭짓점의 위치는 사건 높이의 반올림만큼 어긋나지만
        움직이지 않는 선(박공·연결 변·아직 출발하지 않은 경사 변) 위의 점은 정확하다: 관련 선 가운데 움직이지 않는 선이 둘(평행이 아닌)이면
        그 교점, 하나면 꼭짓점 평균을 그 선 위로 내린 점, 없으면 평균. extra = 만나는 점이 놓일 선(분할 사건에서 닿은 변)."""
        px = sum(vt["p"][0] for vt in vts) / len(vts)
        py = sum(vt["p"][1] for vt in vts) / len(vts)
        fx = []
        for j in sorted({x for vt in vts for x in (vt["a"], vt["b"])} | set(extra)):
            if self.speed(j, h) <= 0.0:
                fx.append(j)
        for x in range(len(fx)):
            for y in range(x + 1, len(fx)):
                la, lb = self.lines[fx[x]], self.lines[fx[y]]
                if abs(la[0] * lb[1] - la[1] * lb[0]) < PAR_SIN:
                    continue
                q = _isect(la, self.C(fx[x], h), lb, self.C(fx[y], h))
                if q is not None and abs(q[0] - px) + abs(q[1] - py) <= 4.0 * self.ptol:
                    return q
        if fx:
            la = self.lines[fx[0]]
            f = la[0] * px + la[1] * py - self.C(fx[0], h)
            if abs(f) <= 4.0 * self.ptol:
                return (px - la[0] * f, py - la[1] * f)
        return (px, py)

    # ---- 기록
    def _end(self, vt, key):
        if vt["k"] != key:
            # 같은 높이에서 생겼다 끝난 꼭짓점(m 이 아직 없다)은 지금 높이의 선 움직임으로 본다: 만나는 점(_meet)으로 옮긴 만큼의 짧은 호도
            # 그 면의 경계가 된다(W2 아홉 번째 PR. 전에는 버려서 면이 열렸다)
            ma, mb = vt["m"] if vt["m"] is not None else (self.speed(vt["a"], self.h) > 0.0, self.speed(vt["b"], self.h) > 0.0)
            self.arcs.append((vt["a"], vt["b"], vt["k"], key, ma, mb))

    def _peak(self, loop, h):
        """고리의 선들이 (거의) 한 점에서 만나는 곳: 선마다 n.p - w h = c - w hs 를 최소 제곱으로 푼 점(움직이지 않는 선은 무게를 크게).
        모든 선이 그 점에서 ptol 안이면 그 점, 아니면(용마루처럼 선으로 사라지는 고리) None. 3x3 크라메르 공식(사칙연산만)."""
        rows = []
        for j in sorted({vt["a"] for vt in loop} | {vt["b"] for vt in loop}):
            nx, ny, c, w, hs = self.lines[j]
            if self.speed(j, h) > 0.0:
                rows.append((nx, ny, -w, c - w * hs, 1.0))
            else:
                rows.append((nx, ny, 0.0, c, 1e6))
        m = [[0.0] * 3 for _ in range(3)]
        r = [0.0] * 3
        for a0, a1, a2, bb, wt in rows:
            av = (a0, a1, a2)
            for x in range(3):
                r[x] += wt * av[x] * bb
                for y in range(3):
                    m[x][y] += wt * av[x] * av[y]

        def det3(q):
            return (q[0][0] * (q[1][1] * q[2][2] - q[1][2] * q[2][1]) - q[0][1] * (q[1][0] * q[2][2] - q[1][2] * q[2][0])
                    + q[0][2] * (q[1][0] * q[2][1] - q[1][1] * q[2][0]))
        if any(rw[2] != 0.0 for rw in rows):
            dd = det3(m)
            if abs(dd) <= 1e-12 * max(abs(m[0][0] * m[1][1] * m[2][2]), 1e-300):
                return None
            sol = []
            for x in range(3):
                q = [row[:] for row in m]
                for y in range(3):
                    q[y][x] = r[y]
                sol.append(det3(q) / dd)
        else:   # 움직이는 선이 없다(정리 단계에서는 생기지 않지만 지켜 둔다)
            return None
        p = (sol[0], sol[1])
        st = [rw for rw in rows if rw[2] == 0.0]
        if len(st) == 1:
            # 움직이지 않는 선이 하나면 그 선 위로 내린다(W2 열두 번째 PR): 거의 평행한 움직이는 선들과 풀면 무게를 크게 줘도 크라메르 풀이가
            # 점 맞춤 거리보다 크게(D 꼴 현 박공에서 수 um) 비켜나 윗변이 외곽(박공 변) 위로 보이지 않았다. 점 맞춤 거리 안이면 그대로 둔다
            a0, a1, _, bb, _ = st[0]
            f = a0 * p[0] + a1 * p[1] - bb
            if abs(f) > self.snap.tol:
                p = (p[0] - a0 * f, p[1] - a1 * f)
        for a0, a1, a2, bb, wt in rows:
            if abs(a0 * p[0] + a1 * p[1] + a2 * sol[2] - bb) > self.ptol:
                return None
        return p

    def _onto_fixed(self, loop, ps, h):
        """사라지는 고리에서 움직이는 선이 움직이지 않는 선(박공·연결 변·아직 출발하지 않은 경사 변)에 포개진 꼭짓점을 그 움직이지 않는 선들의
        교점으로 옮긴다(W2 열두 번째 PR). 고리 부채꼴의 안쪽 호만 물매일 때 안쪽 호의 파면 변은 바깥 호의 박공 변과 (반올림이 없으면 정확히)
        평행하게 다가가 한꺼번에 포개지고, 두 파면 변 사이 꼭짓점은 바깥 호 꼭짓점에 닿는다. 반올림된 좌표에서는 두 변의 방향이 조금 달라
        그 꼭짓점이 바깥 호 꼭짓점에서 (방향 차이 x 지나온 거리만큼, 수십 점 호면 ptol 을 넘게) 비켜 서서, 바깥 호 꼭짓점이 어떤 면에도
        들지 않았다(처마 둘레 높이를 정할 수 없다) 또는 그 둘레에 틈이 남았다. 포개진 선의 짝은 고리 변의 두 끝이 모두 ptol 안에 있는
        반대 방향 움직이지 않는 선이다. 옮긴 점이 고리의 움직이지 않는 꼭짓점과 같으면 늘 받고, 아니면 4 ptol 안일 때만 받는다.
        이미 ptol 안인 꼭짓점은 그대로 둔다(정확한 입력의 결과는 바뀌지 않는다)."""
        n = len(loop)
        if n < 3:
            return ps
        stay = [self.speed(j, h) <= 0.0 for j in range(len(self.lines))]
        co = {}   # 고리 변 e(꼭짓점 e -> e+1)의 선 -> 포개진 움직이지 않는 선
        for e in range(n):
            j = loop[e]["b"]
            if stay[j]:
                continue
            p, q = ps[e], ps[(e + 1) % n]
            nj = self.lines[j]
            best = None
            for f in range(n):
                g = loop[f]["b"]
                if not stay[g] or g == j:
                    continue
                lg = self.lines[g]
                if nj[0] * lg[0] + nj[1] * lg[1] >= 0.0:
                    continue
                cg = self.C(g, h)
                dm = max(abs(lg[0] * p[0] + lg[1] * p[1] - cg), abs(lg[0] * q[0] + lg[1] * q[1] - cg))
                if dm <= self.ptol and (best is None or dm < best[0]):
                    best = (dm, g)
            if best is not None:
                co[j] = best[1]
        if not co:
            return ps
        corners = [ps[i] for i in range(n) if stay[loop[i]["a"]] and stay[loop[i]["b"]]]
        out = list(ps)
        for i in range(n):
            vt = loop[i]
            if stay[vt["a"]] and stay[vt["b"]]:
                continue
            fa = vt["a"] if stay[vt["a"]] else co.get(vt["a"])
            fb = vt["b"] if stay[vt["b"]] else co.get(vt["b"])
            if fa is not None and fb is not None and fa != fb:
                la, lb = self.lines[fa], self.lines[fb]
                q = None if abs(la[0] * lb[1] - la[1] * lb[0]) < PAR_SIN else _isect(la, self.C(fa, h), lb, self.C(fb, h))
                if q is not None:
                    dq = _d(q, ps[i])
                    if dq <= self.ptol:
                        continue
                    if any(_d(q, c) <= self.snap.tol for c in corners) or dq <= 4.0 * self.ptol:
                        out[i] = q
        return out

    def _collapse(self, loop, h):
        """고리가 사라진다: 남은 변마다 윗변(끝 꼭짓점 -> 시작 꼭짓점)을 그 면에 남기고 꼭짓점의 호를 닫는다.
        넓이로 사라졌다고 본 고리가 아직 작은 크기를 가지면(거의 동시 사건: 반올림된 좌표의 거의 평행한 변들) 꼭짓점마다 다른 점으로 닫을 때
        윗변들이 그 크기만큼 구멍을 남긴다(W2 아홉 번째 PR). 그래서 ptol 안의 꼭짓점은 한 점으로 모으고(한 무리면 선들이 만나는 점 _peak),
        다른 점의 윗변 바로 옆(ptol 안)에 남은 점은 그 윗변 위로 옮겨 윗변들이 서로 맞물리게 한다. 움직이지 않는 두 선(박공·연결 변)에
        묶인 꼭짓점은 위치가 정확하므로 옮기지 않고 그 점으로 모은다."""
        n = len(loop)
        ps = [vt["p"] for vt in loop]
        fixed = [self.lines[vt["a"]][3] <= 0.0 and self.lines[vt["b"]][3] <= 0.0 for vt in loop]
        tol = self.ptol
        ps = self._onto_fixed(loop, ps, h)
        par = list(range(n))

        def find(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        ws = [{self.lines[vt["a"]][3], self.lines[vt["b"]][3]} for vt in loop]
        for i in range(n):
            for j in range(i + 1, n):
                # 이웃하지 않은 두 꼭짓점(납작한 고리의 마주 보는 두 쪽)은 네 선의 물매가 모두 같으면 4 ptol 안이어도 한 점: 거의 평행한 마주 보는
                # 변들이 용마루로 포개질 때 양쪽 꼭짓점이 (방향 차이 x 지나온 거리만큼) 용마루를 따라 엇갈려 ptol 을 넘으면 윗변 끝이 맞지 않아
                # 얇은 틈이 남았다(W2 열두 번째 PR). 물매가 섞이면 용마루가 가운데가 아니라 엇갈림이 진짜 형상일 수 있어 ptol 그대로다
                same = len(ws[i] | ws[j]) == 1 and next(iter(ws[i])) > 0.0
                lim = tol if (j == i + 1 or (i == 0 and j == n - 1) or not same) else 4.0 * tol
                if _d(ps[i], ps[j]) <= lim:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        par[max(ri, rj)] = min(ri, rj)
        grp = {}
        for i in range(n):
            grp.setdefault(find(i), []).append(i)
        rep = {}
        for g, mem in grp.items():
            fx = [ps[i] for i in mem if fixed[i]]
            src = fx if fx else [ps[i] for i in mem]
            rep[g] = (sum(p[0] for p in src) / len(src), sum(p[1] for p in src) / len(src))
        if len(grp) == 1 and n >= 3 and not any(fixed):
            pk = self._peak(loop, h)
            if pk is not None and max(_d(pk, q) for q in ps) <= tol:
                rep[0] = pk
        elif len(grp) > 2:   # 선으로 사라지는 고리: 다른 무리 둘을 잇는 윗변 바로 옆의 무리를 그 윗변 위로
            for g, mem in sorted(grp.items()):
                if any(fixed[i] for i in mem):
                    continue
                p = rep[g]
                for i in range(n):
                    g1, g2 = find(i), find((i + 1) % n)
                    if g in (g1, g2) or g1 == g2:
                        continue
                    a, c = rep[g1], rep[g2]
                    dx, dy = c[0] - a[0], c[1] - a[1]
                    l2 = dx * dx + dy * dy
                    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
                    if 0.0 < t < 1.0:
                        q = (a[0] + t * dx, a[1] + t * dy)
                        if _d(p, q) <= tol:
                            rep[g] = q
                            break
        keys = [self.snap.key(rep[find(i)]) for i in range(n)]
        for i in range(n):
            vt, nx = loop[i], loop[(i + 1) % n]
            if n >= 2 and vt["b"] == nx["a"] and keys[i] != keys[(i + 1) % n]:
                self.tops.append((vt["b"], keys[(i + 1) % n], keys[i]))
        for vt, k in zip(loop, keys):
            self._end(vt, k)

    # ---- 전파
    def run(self):
        h = min((ln[4] for ln in self.lines if ln[3] > 0.0), default=_INF)
        if h == _INF:
            raise _Reject("경사 변(물매 > 0)이 하나 이상 있어야 한다")
        self._update(h, set())
        guard = 0
        while self.loops:
            guard += 1
            if guard > EVENTS_MAX:
                raise _Reject("이 외곽에서는 경사면 계산이 끝나지 않는다(외곽을 단순하게 하라)")
            hn = self._next(h)
            if hn == _INF:
                raise _Reject("이 외곽에서는 경사면이 닫히지 않는다(물매 변이 없는 부분이 있다)")
            for lp in self.loops:
                for vt in lp:
                    vt["p"] = self._pos(vt, hn, h)
            moving = {j for j in range(len(self.lines)) if self.speed(j, h) > 0.0}
            self.stall = hn <= h
            h = hn
            self._settle(h)
            self._update(h, moving)

    def _update(self, h, before):
        """사건 정리 뒤: 속도를 다시 정하고, 궤적이 꺾이거나(이웃 변 출발) 움직임이 바뀐 꼭짓점은 호를 여기서 끊는다.
        이번에 출발한 선(before 에 없던 움직이는 선)은 지금 그 선 위의 파면 변을 면의 아랫변으로 남긴다."""
        for lp in self.loops:
            n = len(lp)
            for i in range(n):
                vt = lp[i]
                v = self._velocity(vt, h)
                m = (self.speed(vt["a"], h) > 0.0, self.speed(vt["b"], h) > 0.0)
                if vt["m"] is not None and (m != vt["m"] or abs(v[0] - vt["v"][0]) + abs(v[1] - vt["v"][1]) > 1e-9):
                    k = self.snap.key(vt["p"])
                    self._end(vt, k)
                    vt["k"] = k
                vt["v"], vt["m"] = v, m
            for i in range(n):
                v1, v2 = lp[i], lp[(i + 1) % n]
                j = v1["b"]
                if self.speed(j, h) > 0.0 and j not in before:
                    k1, k2 = self.snap.key(v1["p"]), self.snap.key(v2["p"])
                    if k1 != k2:
                        self.bottoms.append((j, k1, k2))

    def _next(self, h):
        best = _INF
        self.pend = None   # 가장 이른 사건이 분할 사건이면 (오목 꼭짓점, 닿는 변의 선): _settle 의 마지막 확인(_settle 참고)
        for lp in self.loops:
            if not any(self.lines[vt["a"]][3] > 0.0 for vt in lp):
                continue
            for ln in {vt["a"] for vt in lp}:
                w, hs = self.lines[ln][3], self.lines[ln][4]
                if w > 0.0 and hs > h + self.eps * 1e-3 and hs < best:
                    best, self.pend = hs, None
            n = len(lp)
            for i in range(n):   # 변 사건: 변 (i -> i+1) 의 길이
                v1, v2 = lp[i], lp[(i + 1) % n]
                u = self._dir(v1["b"])
                ln_ = (v2["p"][0] - v1["p"][0]) * u[0] + (v2["p"][1] - v1["p"][1]) * u[1]
                dl = (v2["v"][0] - v1["v"][0]) * u[0] + (v2["v"][1] - v1["v"][1]) * u[1]
                if dl < -1e-12 and h + max(0.0, -ln_ / dl) < best:
                    best, self.pend = h + max(0.0, -ln_ / dl), None
            for r in lp:   # 분할 사건: 오목 꼭짓점이 다른 변의 선에 닿는다
                if not self._reflex(r):
                    continue
                for i in range(n):
                    v1, v2 = lp[i], lp[(i + 1) % n]
                    e = v1["b"]
                    if r is v1 or r is v2 or e in (r["a"], r["b"]):
                        continue
                    nx, ny = self.lines[e][0], self.lines[e][1]
                    f = nx * r["p"][0] + ny * r["p"][1] - self.C(e, h)
                    df = nx * r["v"][0] + ny * r["v"][1] - self.speed(e, h)
                    if df >= -1e-12 or f < -self.eps:
                        continue
                    t = max(0.0, -f / df)
                    if h + t >= best:
                        continue
                    q = (r["p"][0] + r["v"][0] * t, r["p"][1] + r["v"][1] * t)
                    q1 = (v1["p"][0] + v1["v"][0] * t, v1["p"][1] + v1["v"][1] * t)
                    q2 = (v2["p"][0] + v2["v"][0] * t, v2["p"][1] + v2["v"][1] * t)
                    u = self._dir(e)
                    s = (q[0] - q1[0]) * u[0] + (q[1] - q1[1]) * u[1]
                    L = (q2[0] - q1[0]) * u[0] + (q2[1] - q1[1]) * u[1]
                    if -self.eps <= s <= L + self.eps:
                        best, self.pend = h + t, (r, e)
        return best

    def _settle(self, h):
        """같은 높이의 사건을 정리한다: 합치기·가시 자르기·분할을 바뀌지 않을 때까지."""
        eps = self.eps
        self.h = h
        guard = 0
        while True:
            guard += 1
            if guard > EVENTS_MAX:
                raise _Reject("이 외곽에서는 경사면 계산이 끝나지 않는다(외곽을 단순하게 하라)")
            changed = False
            for li, lp in enumerate(self.loops):
                n = len(lp)
                # 사건 높이가 더 오르지 않으면(물매가 아주 낮아 거의 평행한 선의 꼭짓점 속도가 매우 크다: 다음 사건까지 높이 차가 h 의 float
                # 해상도보다 작다) 두께(넓이 / 둘레)가 ptol 안인 고리도 사라진 것으로 본다(W2 열두 번째 PR. 전에는 같은 높이에서 끝없이 돌았다)
                a2 = abs(RP._area2([vt["p"] for vt in lp]))
                if n < 3 or a2 <= eps * _perim(lp) or (self.stall and a2 <= self.ptol * _perim(lp)):
                    self._collapse(lp, h)
                    self.loops.pop(li)
                    changed = True
                    break
                for i in range(n):   # 같은 점이 된 이웃 꼭짓점(변 사건). 변 방향으로 이미 엇갈린 짧은 변(반올림으로 사건 높이를 지나친 것)도
                    v1, v2 = lp[i], lp[(i + 1) % n]   # 합친다: 안 그러면 그 변 사건이 같은 높이에서 끝없이 다시 잡힌다(W2 아홉 번째 PR)
                    dd = _d(v1["p"], v2["p"])
                    u = self._dir(v1["b"])
                    if dd <= eps or (dd <= self.ptol and (v2["p"][0] - v1["p"][0]) * u[0] + (v2["p"][1] - v1["p"][1]) * u[1] <= 0.0):
                        k = self.snap.key(self._meet((v1, v2), h))
                        self._end(v1, k)
                        self._end(v2, k)
                        nv = self._vertex(v1["a"], v2["b"], self.snap.pts[k], k)
                        if (i + 1) % n == 0:
                            lp[:] = [nv] + lp[1:n - 1]
                        else:
                            lp[i:i + 2] = [nv]
                        changed = True
                        break
                if changed:
                    break
                for i in range(n):   # 0 폭 가시: 이웃 두 선이 반대 방향으로 포개졌다
                    vt = lp[i]
                    la, lb = self.lines[vt["a"]], self.lines[vt["b"]]
                    if la[0] * lb[0] + la[1] * lb[1] > -1.0 + 1e-9 or abs(la[0] * lb[1] - la[1] * lb[0]) > 1e-6:
                        continue
                    pa, pb = lp[i - 1], lp[(i + 1) % n]
                    if abs(la[0] * vt["p"][0] + la[1] * vt["p"][1] - self.C(vt["a"], h)) > eps or abs(lb[0] * pb["p"][0] + lb[1] * pb["p"][1] - self.C(vt["b"], h)) > eps:
                        continue
                    if abs(la[0] * pb["p"][0] + la[1] * pb["p"][1] - self.C(vt["a"], h)) > eps:
                        continue   # 두 선이 아직 떨어져 있다
                    da, db = _d(vt["p"], pa["p"]), _d(vt["p"], pb["p"])
                    kt = self.snap.key(vt["p"])
                    self._end(vt, kt)
                    if da <= db:   # 짧은 쪽(앞 변 a) 이 다 접힌다: 앞 꼭짓점이 a 대신 b 를 잇는다
                        ka = self.snap.key(pa["p"])
                        self.tops.append((vt["a"], kt, ka))
                        self.tops.append((vt["b"], ka, kt))
                        self._end(pa, ka)
                        nv = self._vertex(pa["a"], vt["b"], self.snap.pts[ka], ka)
                        j = (i - 1) % n
                        lp[j] = nv
                        lp.pop(i)
                    else:
                        kb = self.snap.key(pb["p"])
                        self.tops.append((vt["b"], kb, kt))
                        self.tops.append((vt["a"], kt, kb))
                        self._end(pb, kb)
                        nv = self._vertex(vt["a"], pb["b"], self.snap.pts[kb], kb)
                        j = (i + 1) % n
                        lp[j] = nv
                        lp.pop(i)
                    changed = True
                    break
                if changed:
                    break
                sp = self._split_one(lp, h)
                if sp is not None:
                    self.loops[li:li + 1] = sp
                    changed = True
                    break
            if not changed and self.pend is not None:
                # 이 높이를 부른 분할 사건이 eps 판정으로 일어나지 않았다: 물매가 섞인 거의 동시 사건(오목 꼭짓점이 다른 꼭짓점에 닿는다)에서
                # 위치 어긋남이 eps 를 넘으면 그 꼭짓점이 변을 그대로 지나쳐 고리가 뒤집히고 계산이 닫히지 않았다(W2 열두 번째 PR).
                # 그 오목 꼭짓점·변만 ptol 로 한 번 더 본다(다른 사건 판정은 그대로)
                r, e = self.pend
                self.pend = None
                for li, lp in enumerate(self.loops):
                    if any(vt is r for vt in lp):
                        sp = self._split_one(lp, h, self.ptol, (r, e))
                        if sp is not None:
                            self.loops[li:li + 1] = sp
                            changed = True
                        break
            if not changed:
                return

    def _split_one(self, lp, h, tol=None, only=None):
        """오목 꼭짓점 하나가 다른 변 위에 있으면 고리를 둘로 나눈다(없으면 None). tol = 판정 거리(기본 eps), only = (그 꼭짓점, 그 변의 선)만 본다."""
        eps = self.eps if tol is None else tol
        n = len(lp)
        for ri in range(n):
            r = lp[ri]
            if not self._reflex(r) or (only is not None and r is not only[0]):
                continue
            for i in range(n):
                v1, v2 = lp[i], lp[(i + 1) % n]
                e = v1["b"]
                if ri in (i, (i + 1) % n) or e in (r["a"], r["b"]) or (only is not None and e != only[1]):
                    continue
                nx, ny = self.lines[e][0], self.lines[e][1]
                if abs(nx * r["p"][0] + ny * r["p"][1] - self.C(e, h)) > eps:
                    continue
                u = self._dir(e)
                s = (r["p"][0] - v1["p"][0]) * u[0] + (r["p"][1] - v1["p"][1]) * u[1]
                L = (v2["p"][0] - v1["p"][0]) * u[0] + (v2["p"][1] - v1["p"][1]) * u[1]
                if not -eps <= s <= L + eps:
                    continue
                # 고리 순서: r, r+1, ..., i(v1), i+1(v2), ..., r-1
                seq = [lp[(ri + j) % n] for j in range(n)]
                pi = (i - ri) % n          # v1 의 seq 위치
                if _d(r["p"], v1["p"]) <= eps or _d(r["p"], v2["p"]) <= eps:   # 꼭짓점끼리 만났다
                    qi = pi if _d(r["p"], v1["p"]) <= eps else (pi + 1) % n
                    q = seq[qi]
                    k = self.snap.key(self._meet((r, q), h))
                    self._end(r, k)
                    self._end(q, k)
                    xa = self._vertex(r["a"], q["b"], self.snap.pts[k], k)
                    xb = self._vertex(q["a"], r["b"], self.snap.pts[k], k)
                    la = [xa] + seq[qi + 1:]
                    lb = [xb] + seq[1:qi]
                else:
                    k = self.snap.key(self._meet((r,), h, (e,)))
                    self._end(r, k)
                    xa = self._vertex(r["a"], e, self.snap.pts[k], k)   # r 의 앞 변 -> 닿은 변(뒤쪽 반)
                    xb = self._vertex(e, r["b"], self.snap.pts[k], k)   # 닿은 변(앞쪽 반) -> r 의 뒤 변
                    la = [xa] + seq[pi + 1:]
                    lb = [xb] + seq[1:pi + 1]
                return [x for x in (la, lb) if x]
        return None


class _Reject(Exception):
    pass


def _d(p, q):
    return sqrt((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2)


def _perim(lp):
    n = len(lp)
    return sum(_d(lp[i]["p"], lp[(i + 1) % n]["p"]) for i in range(n)) + 1.0


def outline(b, ks, os, z0=0.0):
    """처마 윤곽 P'(반시계 점들)과 변마다 (원래 외곽 변 번호 또는 -1(연결 변)) 목록, 또는 거절 이유 문장."""
    n = len(b)
    ed = RP._edges(b)
    he = [z0 - ks[i] * os[i] for i in range(n)]
    pts, src = [], []
    for i in range(n):   # 꼭짓점 i: 변 i-1 과 변 i 사이
        j = (i - 1) % n
        (p0, u0, n0, _), (p1, u1, n1, _) = ed[j], ed[i]
        c0 = n0[0] * p0[0] + n0[1] * p0[1] - os[j]
        c1 = n1[0] * p1[0] + n1[1] * p1[1] - os[i]
        convex = u0[0] * u1[1] - u0[1] * u1[0] > 0.0
        if convex or abs(he[j] - he[i]) <= 1e-6:
            q = _isect(n0, c0, n1, c1)
            pts.append(q)
            src.append(i)
            continue
        # 오목 꼭짓점의 처마 높이가 다르다: 골 연장선(두 경사면이 같은 높이인 선, 외곽 꼭짓점을 지난다) 위의 두 점과 그 사이 연결 변
        nv = (ks[j] * n0[0] - ks[i] * n1[0], ks[j] * n0[1] - ks[i] * n1[1])   # 골 선의 법선: k_j d_j = k_i d_i
        c = nv[0] * b[i][0] + nv[1] * b[i][1]
        xa = _isect(n0, c0, nv, c)
        xb = _isect(n1, c1, nv, c)
        if xa is None or xb is None:
            return "오목 꼭짓점의 골이 처마 선과 만나지 않는다"
        pts.append(xa)
        src.append(-1)      # 꼭짓점 xa 다음 변 = 연결 변
        pts.append(xb)
        src.append(i)
    # src[k] = 점 k 에서 시작하는 변의 원래 외곽 변 번호(-1 = 연결 변)
    return pts, src


@lru_cache(maxsize=256)
def _build(b, ks, os, z0):
    """형상(roofpoly._build 와 같은 꼴의 튜플) 또는 거절 이유 문장."""
    n = len(b)
    ed = RP._edges(b)
    r = outline(b, ks, os, z0)
    if isinstance(r, str):
        return r
    pts, src = r
    m = len(pts)
    if RP._area2(pts) <= 0.0 or not RP._simple(pts):
        return "처마를 내민 외곽이 꼬인다(처마 내밀기를 줄이거나 짧은 변을 늘려라)"
    for k in range(m):   # 내민 변이 거꾸로 뒤집히면 안 된다
        i = src[k]
        if i < 0:
            continue
        a, c = pts[k], pts[(k + 1) % m]
        u = ed[i][1]
        if (c[0] - a[0]) * u[0] + (c[1] - a[1]) * u[1] <= 0.0:
            return "처마를 내민 외곽이 꼬인다(처마 내밀기를 줄이거나 짧은 변을 늘려라)"
    ext = max(max(q[0] for q in pts) - min(q[0] for q in pts), max(q[1] for q in pts) - min(q[1] for q in pts))
    eps = 1e-7 * ext + 1e-6
    lines, planes, normals = [], [], []
    for k in range(m):
        i = src[k]
        a, c = pts[k], pts[(k + 1) % m]
        ux, uy, _ = _norm(c[0] - a[0], c[1] - a[1])
        nv = (-uy, ux)
        if i >= 0:
            nv = ed[i][2]   # 원래 변의 법선(내민 변과 평행)
        cc = nv[0] * a[0] + nv[1] * a[1]
        if i >= 0 and ks[i] > 0.0:
            lines.append((nv[0], nv[1], cc, 1.0 / ks[i], z0 - ks[i] * os[i]))
            planes.append(RP._plane(ed[i], ks[i], z0))
        else:
            lines.append((nv[0], nv[1], cc, 0.0, _INF))
            planes.append(None)
        normals.append(nv)
    sw = _Sweep(pts, lines, eps)
    try:
        sw.run()
    except _Reject as ex:
        return str(ex)
    snap = sw.snap
    # 면마다 방향 선분: 처마 변 + 꼭짓점 호(그 면이 앞 변이면 정방향, 뒤 변이면 역방향) + 윗변
    segs = {}
    for j, s, e in sw.bottoms:
        if planes[j] is not None:
            segs.setdefault(j, []).append((s, e))
    for a, bb, s, e, ma, mb in sw.arcs:
        if planes[a] is not None and ma:
            segs.setdefault(a, []).append((s, e))
        if planes[bb] is not None and mb:
            segs.setdefault(bb, []).append((e, s))
    for j, s, e in sw.tops:
        if planes[j] is not None:
            segs.setdefault(j, []).append((s, e))
    faces = []
    for j in sorted(segs):
        lps = _chain(segs[j], snap)
        if lps is None:
            return "이 외곽에서는 경사면을 이어 붙일 수 없다(면 안에 구멍이 생긴다: 지붕을 둘 이상으로 나눠라)"
        faces.extend((j, lp) for lp in lps)
    if not faces:
        return "경사면이 하나도 만들어지지 않는다"
    if len(faces) > FACES_MAX:
        return f"경사면이 {FACES_MAX}개를 넘는다(외곽을 단순하게 하라)"
    r = RP._finish(faces, snap, planes, normals, [tuple(q) for q in pts], max(RP.ON_TOL, snap.tol))
    if isinstance(r, str):
        return r
    return r + (tuple(src),)


def _chain(segs, snap):
    """한 면의 방향 선분들을 고리들로 잇는다(반시계, snap 번호). 이어지지 않거나 안쪽으로 감기면 None."""
    segs = [(a, b) for a, b in segs if a != b]
    # 반대 방향 짝(같은 선분을 두 번 지난 것)은 지운다
    cnt = {}
    for e in segs:
        cnt[e] = cnt.get(e, 0) + 1
    left = []
    for e in segs:
        r = (e[1], e[0])
        if cnt.get(e, 0) <= 0:
            continue
        if cnt.get(r, 0) > 0:
            cnt[e] -= 1
            cnt[r] -= 1
            continue
        cnt[e] -= 1
        left.append(e)
    outs = {}
    for a, b in left:
        outs.setdefault(a, []).append(b)
    used = set()
    loops = []
    for start in sorted(outs):
        for first in sorted(outs[start]):
            if (start, first) in used:
                continue
            ring = [start]
            prev, cur = start, first
            used.add((start, first))
            guard = 0
            while cur != start and guard < 100000:
                guard += 1
                ring.append(cur)
                cand = [nx for nx in outs.get(cur, ()) if (cur, nx) not in used]
                if not cand:
                    return None
                pa, pb = snap.pts[prev], snap.pts[cur]
                din = RP._pseudo_angle(pb[0] - pa[0], pb[1] - pa[1])

                def turn(nx, pb=pb, din=din):
                    q = snap.pts[nx]
                    return (RP._pseudo_angle(q[0] - pb[0], q[1] - pb[1]) - din + 2.0) % 4.0
                nx = min(cand, key=lambda k: (turn(k), k))
                used.add((cur, nx))
                prev, cur = cur, nx
            if cur != start:
                return None
            ring = RP._drop_collinear_keys(ring, snap)
            if len(ring) >= 3:
                a2 = RP._area2([snap.pts[k] for k in ring])
                if a2 <= 0.0:
                    if abs(a2) <= RP.AREA2_MIN * 10:
                        continue
                    return None
                loops.append(ring)
    return loops


def problem(b, ks, os) -> str | None:
    """외곽·변 물매·변 내밀기가 지붕을 만들 수 있는가(안 되면 이유 문장). b 는 반시계 [x,y] 목록, ks 는 변마다 tan(0 = 물매 없음), os 는 변마다 내밀기."""
    n = len(b)
    if not 3 <= n <= PTS_MAX:
        return f"외곽선 지붕의 외곽은 꼭짓점 3~{PTS_MAX}개여야 한다"
    if len(ks) != n or len(os) != n:
        return "변마다 물매·내밀기는 외곽의 변 수(꼭짓점 수)만큼 있어야 한다"
    if RP._area2(b) <= 0:
        return "외곽은 반시계 방향이어야 한다"
    for i in range(n):
        q, r = b[i], b[(i + 1) % n]
        ln = sqrt((r[0] - q[0]) ** 2 + (r[1] - q[1]) ** 2)
        if ln < SIDE_MIN:
            return f"외곽의 변 {i} 가 {SIDE_MIN:g}mm 보다 짧다(겹친 꼭짓점을 빼라)"
    ed = RP._edges(b)
    for i in range(n):
        u0, u1 = ed[i - 1][1], ed[i][1]
        if abs(u0[0] * u1[1] - u0[1] * u1[0]) < PAR_SIN and u0[0] * u1[0] + u0[1] * u1[1] > 0.0:
            return f"꼭짓점 {i} 의 두 변이 한 직선 위에 있다(그 꼭짓점을 빼라)"
        if abs(u0[0] * u1[1] - u0[1] * u1[0]) < PAR_SIN:
            return f"꼭짓점 {i} 에서 외곽이 되접힌다"
    if not RP._simple(b):
        return "외곽이 스스로 겹친다"
    if not any(k > 0.0 for k in ks):
        return "경사 변(물매 > 0)이 하나 이상 있어야 한다"
    for i in range(n):
        if not RP._convex_vertex(ed, i) and (ks[i - 1] <= 0.0 or ks[i] <= 0.0):
            return f"오목 꼭짓점 {i} 에 물매 없는 변(박공)이 붙어 있다(오목 꼭짓점의 두 변은 경사 변이어야 한다)"
    r = _build(tuple((float(q[0]), float(q[1])) for q in b), tuple(float(k) for k in ks), tuple(float(o) for o in os), 0.0)
    return r if isinstance(r, str) else None


def geom(b, slopes, ks, os, base_offset) -> dict:
    """roof_geom 과 같은 꼴의 dict. 더해서 "eave_src": 처마 윤곽(outline)의 변마다 원래 외곽 변 번호(-1 = 오목 꼭짓점의 연결 변)."""
    r = _build(tuple((float(q[0]), float(q[1])) for q in b), tuple(float(k) for k in ks), tuple(float(o) for o in os), float(base_offset))
    if isinstance(r, str):
        raise ValueError(r)
    face3, pieces, planes, fks, ridges, hips, valleys, perim, arrows, outl, zl, zt, src = r
    return {"faces": [list(fc) for fc in face3], "ridges": list(ridges), "hips": list(hips), "valleys": list(valleys), "perimeter": list(perim),
            "arrows": list(arrows), "outline": list(outl), "planes": list(planes), "pieces": [(list(fc), pl) for fc, pl in pieces],
            "fpitch": [float(slopes[src[j]]) for j in fks], "zlow": zl, "ztop": zt, "eave_src": list(src)}
