# SPDX-License-Identifier: MPL-2.0
"""W2 열두 번째 PR: 외곽선 지붕(roofskel) 의 촘촘한 고리 부채꼴 거절(67.6 의 남은 결함)의 재현 시험.

67절(PR #84)에서 퇴화 사건 4가지를 고친 뒤에도 무작위 원호 다각형 가운데 점 36개 이상의 고리 부채꼴 일부를 거절했다.
원인은 모두 "사라지는 고리"(_collapse)에서 반올림 누적 어긋남이 ptol(= 100 eps) 을 넘은 것이다:
  (a) 안쪽 호만 물매: 안쪽 호 파면 변이 바깥 호 박공 변과 거의 평행하게 다가가 포개질 때, 두 파면 변 사이 꼭짓점이 바깥 호 꼭짓점에서
      (방향 차이 x 지나온 거리만큼) 비켜 서서 바깥 호 꼭짓점이 어느 면에도 들지 않았다("처마 둘레의 높이를 정할 수 없다") 또는 틈이 남았다.
      -> 포개진 움직이는 선을 그 박공 선으로 바꿔 두 박공 선의 교점(_onto_fixed)으로 옮긴다.
  (b) 모든 변 물매: 마주 보는 거의 평행한 변들이 용마루로 포개질 때 양쪽 꼭짓점이 용마루를 따라 엇갈려 윗변 끝이 맞지 않았다("틈").
      -> 납작한 고리의 이웃하지 않은 두 꼭짓점은 4 ptol 안이면 한 점으로 모은다.
  (c) D 꼴(현이 박공): 세 선이 만나는 점(_peak)의 최소 제곱 풀이가 박공 선에서 점 맞춤 거리보다 비켜나 윗변이 외곽 위로 보이지 않았다("틈").
      -> 움직이지 않는 선이 하나면 그 선 위로 내린다.
"""
import math
import random

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import roofskel as RS

T = lambda d: math.tan(math.radians(d))  # noqa: E731
_SRC = (ROOT / "tests" / "test_w2_fixes2.py").read_text(encoding="utf-8")
_ns = {"math": math, "T": T}
exec(_SRC[_SRC.index("def _fuzz_case"):_SRC.index('@pytest.mark.parametrize("seed", [1, 2, 3])')], _ns)
_fuzz_case = _ns["_fuzz_case"]   # 67절 퍼즈 생성기(같은 seed 면 같은 외곽)


def _gen_w2i(rng):
    """67절을 고칠 때 쓴 다른 퍼즈 생성기(고리·부채꼴·D 꼴·반원 끝 직사각형, 물매 모드 all/inner/outer/mixed). 재현용으로 그대로 옮긴다."""
    kind = rng.choice(["ann", "pie", "dshape", "rectarc"])
    dec = rng.choice([None, 0, 1])
    rd = (lambda v: v) if dec is None else (lambda v: round(v, dec) if dec else float(round(v)))
    P = lambda r, t: (rd(r * math.cos(t)), rd(r * math.sin(t)))  # noqa: E731
    a0 = rng.uniform(-math.pi, math.pi)
    span = rng.uniform(math.radians(20), math.radians(300) if kind != "dshape" else math.radians(180))
    nseg = rng.randint(2, 24)
    ro = rng.uniform(3000, 40000)
    angs = [a0 + span * i / nseg for i in range(nseg + 1)]
    if kind == "ann":
        ri = ro * rng.uniform(0.2, 0.85)
        b = [P(ro, a) for a in angs] + [P(ri, a) for a in angs[::-1]]
        inner = set(range(nseg + 1, 2 * nseg + 1))
        outer = set(range(0, nseg))
    elif kind == "pie":
        span = min(span, math.radians(170))
        angs = [a0 + span * i / nseg for i in range(nseg + 1)]
        b = [P(ro, a) for a in angs] + [(rd(0.0), rd(0.0))]
        inner, outer = set(), set(range(0, nseg))
    elif kind == "dshape":
        b = [P(ro, a) for a in angs]
        inner, outer = set(), set(range(0, nseg))
    else:
        w = ro
        d = rng.uniform(3000, 30000)
        arc = [(w / 2 * math.cos(math.pi * i / nseg), d + w / 2 * math.sin(math.pi * i / nseg)) for i in range(nseg + 1)]
        b = [(rd(w / 2), rd(0.0))] + [(rd(x), rd(y)) for x, y in arc] + [(rd(-w / 2), rd(0.0))]
        inner, outer = set(), set(range(1, nseg + 1))
    if sum(b[i][0] * b[(i + 1) % len(b)][1] - b[(i + 1) % len(b)][0] * b[i][1] for i in range(len(b))) < 0:
        b = b[::-1]
        n = len(b)
        inner = {(n - 2 - i) % n for i in inner}
        outer = {(n - 2 - i) % n for i in outer}
    n = len(b)
    mode = rng.choice(["all", "all", "inner", "outer", "mixed"])
    p = rng.choice([0.1, 5, 12.7, 30, 45, 60])
    if mode == "inner" and inner:
        ks = [T(p) if i in inner else 0.0 for i in range(n)]
    elif mode == "outer":
        ks = [T(p) if i in outer else 0.0 for i in range(n)]
    elif mode == "mixed":
        ks = [T(rng.choice([p, p, 20.0])) for i in range(n)]
    else:
        ks = [T(p)] * n
    os_ = [0.0] * n if rng.random() < 0.6 else [rng.choice([0.0, 300.0, 600.0])] * n
    return kind, b, ks, os_


def _nth(gen, seed, it):
    rng = random.Random(seed)
    for _ in range(it):
        gen(rng)
    return gen(rng)


def _ring_case(rng):
    """촘촘한 고리 부채꼴(점 36~72): 안쪽 호만 물매(절반)·모든 변 물매, 반올림 없음·1 mm·0.1 mm, 처마 내밀기 0·300·600."""
    nseg = rng.randint(17, 35)
    nd = rng.choice([None, 0, 1])
    rd = (lambda v: v) if nd is None else (lambda v: float(round(v, nd)) if nd else float(round(v)))
    a0 = rng.uniform(-math.pi, math.pi)
    span = rng.uniform(math.radians(20), math.radians(300))
    ro = rng.uniform(3000, 40000)
    ri = ro * rng.uniform(0.2, 0.85)
    angs = [a0 + span * i / nseg for i in range(nseg + 1)]
    b = [(rd(ro * math.cos(a)), rd(ro * math.sin(a))) for a in angs] + [(rd(ri * math.cos(a)), rd(ri * math.sin(a))) for a in angs[::-1]]
    n = len(b)
    p = rng.choice([0.1, 5, 12.7, 30, 45, 60])
    if rng.random() < 0.5:
        ks = [T(p) if nseg + 1 <= i <= 2 * nseg else 0.0 for i in range(n)]
    else:
        ks = [T(p)] * n
    os_ = [0.0] * n if rng.random() < 0.7 else [rng.choice([300.0, 600.0])] * n
    return b, ks, os_


# 고치기 전 거절하던 사건(67절 생성기 seed 4~143 x 150 = 21,000 과 다른 생성기 seed 2~8 x 3,000 = 21,000 에서 찾은 것)
# 67절 생성기: 고리 44·48·36·46점, 그리고 물매가 섞인 18점 고리(오목 꼭짓점이 다른 꼭짓점을 지나쳐 계산이 닫히지 않았다)
CASES_FIX2 = [(65, 116), (80, 82), (90, 105), (95, 3), (104, 67)]
# 다른 생성기: D 꼴 23점, 고리 28·44·36·46점, 물매 0.1도·처마 600 의 10점 고리(같은 높이에서 끝없이 돌았다),
# 그리고 물매가 섞인 48점 고리(원래 받던 것: 마주 보는 꼭짓점 모으기를 물매가 같은 선으로만 한정한 근거)
CASES_W2I = [(2, 1132), (3, 2066), (3, 2457), (4, 1138), (6, 944), (6, 1293), (5, 1490)]


@pytest.mark.parametrize("seed,it", CASES_FIX2)
def test_고리_부채꼴_재현_67절_생성기(seed, it):
    kind, b, ks, os_ = _nth(_fuzz_case, seed, it)
    RS._build.cache_clear()
    assert RS.problem(b, ks, os_) is None


@pytest.mark.parametrize("seed,it", CASES_W2I)
def test_고리_부채꼴_재현_다른_생성기(seed, it):
    kind, b, ks, os_ = _nth(_gen_w2i, seed, it)
    RS._build.cache_clear()
    assert RS.problem(b, ks, os_) is None


def test_안쪽_호만_물매_고리의_바깥_꼭짓점이_면에_든다():
    """(a) 의 결과: 바깥 호 꼭짓점(박공 두 변 사이)이 모두 어떤 면의 꼭짓점이고, 면 수는 물매 변 수다."""
    kind, b, ks, os_ = _nth(_fuzz_case, 90, 105)
    g = RS.geom(b, [math.degrees(math.atan(k)) for k in ks], ks, os_, 0.0)
    assert len(g["faces"]) == sum(1 for k in ks if k)
    pts = {(round(q[0], 6), round(q[1], 6)) for fc in g["faces"] for q in fc}
    outer = [q for i, q in enumerate(b) if ks[i - 1] == 0.0 and ks[i] == 0.0]
    assert outer and all((round(q[0], 6), round(q[1], 6)) in pts for q in outer)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_촘촘한_고리_부채꼴_퍼즈(seed):
    """점 36~72 고리 부채꼴 60개씩: 입력 문제(처마 내밀기로 꼬임·한 직선 위 꼭짓점) 밖의 거절이 없어야 한다."""
    rng = random.Random(seed)
    bad = []
    for it in range(60):
        b, ks, os_ = _ring_case(rng)
        RS._build.cache_clear()
        why = RS.problem(b, ks, os_)
        if why is not None and "꼬인다" not in why and "한 직선" not in why:
            bad.append((it, len(b), why[:40]))
    assert bad == []
