# SPDX-License-Identifier: MPL-2.0
"""윤곽 합집합(drawing.outline_of)의 격자 색인판 = 예전 전수 검사판 (W1 연장 7번째 PR).

outline_of 는 면적 평면 gross(벽 바닥 윤곽 합집합)·법정 면적(벽 중심선 영역 합집합, area.legal·area.export·IFC 세움터 면적)·
배치도 건물 외곽이 함께 쓴다. 격자 색인은 후보만 줄이고 판정 식은 그대로라 결과가 바이트 단위로 같아야 한다.
 1. 여러 모양(맞닿은 벽, 닫힌 방, 섬, 기운 벽, 겹친 벽, 떨어진 건물, 아주 짧은 변)에서 선분 튜플이 완전히 같다
 2. 무작위 벽 배치 여러 벌에서 같다(씨앗 고정)
 3. 탈출 판정(_escapes_grid = _escapes)을 많은 점에서 직접 비교
 4. 합집합 면적(viewplans.union_mm2)과 바깥 선분(union_segs)이 같다
"""
import math
import random

from dobim_core import drawing as D
from dobim_core import viewplans as VP


def _ccw(poly):
    poly = [tuple(map(float, p)) for p in poly]
    if D._area2(poly) < 0:
        poly.reverse()
    return tuple(poly)


def _wall(x1, y1, x2, y2, t):
    """벽 중심선 (x1,y1)-(x2,y2), 두께 t 의 바닥 사각형(반시계)."""
    dx, dy = x2 - x1, y2 - y1
    ln = math.hypot(dx, dy)
    nx, ny = -dy / ln * t / 2.0, dx / ln * t / 2.0
    return _ccw([(x1 - nx, y1 - ny), (x2 - nx, y2 - ny), (x2 + nx, y2 + ny), (x1 + nx, y1 + ny)])


def _room(x0, y0, w, h, t=200.0):
    return [_wall(x0, y0, x0 + w, y0, t), _wall(x0 + w, y0, x0 + w, y0 + h, t),
            _wall(x0 + w, y0 + h, x0, y0 + h, t), _wall(x0, y0 + h, x0, y0, t)]


def _same(polys):
    polys = tuple(polys)
    a = D.outline_of(polys)
    b = D.outline_of(polys, brute=True)
    assert a == b
    return a


def test_shapes_same_as_brute():
    cases = [
        _room(0, 0, 10000, 8000),                                            # 닫힌 방 하나(안쪽 면은 빠진다)
        _room(0, 0, 10000, 8000) + _room(10000, 0, 6000, 8000),              # 벽을 나눠 쓰는 두 방
        _room(0, 0, 20000, 20000) + _room(8000, 8000, 3000, 3000),           # 안마당 안의 섬
        _room(0, 0, 10000, 8000) + _room(50000, 30000, 4000, 4000),          # 떨어진 두 건물
        [_wall(0, 0, 10000, 0, 200), _wall(5000, -3000, 5000, 3000, 150)],   # 열린 십자(막다른 벽)
        [_wall(0, 0, 7000, 4000, 250), _wall(7000, 4000, 1000, 9000, 250), _wall(1000, 9000, 0, 0, 250)],   # 기운 삼각형
        [_wall(0, 0, 10000, 0, 200), _wall(2000, 0, 8000, 0, 200)],          # 같은 선 위에 겹친 벽
        [_wall(0, 0, 10000, 0, 200), _wall(10000, 0, 10000.03, 0, 200)],     # 아주 짧은 벽
        [_ccw([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])],                # 다각형 하나
    ]
    for polys in cases:
        assert _same(polys)


def test_random_layouts_same_as_brute():
    rnd = random.Random(7)
    for _case in range(12):
        polys = []
        for _r in range(rnd.randint(1, 6)):        # 격자 위 방들(맞닿거나 겹친다)
            polys += _room(rnd.randint(0, 6) * 3000, rnd.randint(0, 6) * 3000, rnd.randint(1, 4) * 3000, rnd.randint(1, 4) * 3000,
                           rnd.choice((150.0, 200.0, 250.0)))
        for _w in range(rnd.randint(0, 15)):       # 아무 방향 벽
            x1, y1 = rnd.uniform(-2000, 25000), rnd.uniform(-2000, 25000)
            ang, ln = rnd.uniform(0, 2 * math.pi), rnd.uniform(500, 9000)
            polys.append(_wall(x1, y1, x1 + ln * math.cos(ang), y1 + ln * math.sin(ang), rnd.uniform(100, 300)))
        _same(polys)


def test_escape_probe_same_as_brute():
    polys = _room(0, 0, 12000, 9000) + _room(12000, 0, 5000, 9000) + [_wall(3000, 3000, 9000, 6500, 200)]
    edges = [(P[k], P[(k + 1) % len(P)]) for P in polys for k in range(len(P))]
    grid = D._Grid([(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])) for a, b in edges])
    rnd = random.Random(11)
    pts = [(rnd.uniform(-3000, 20000), rnd.uniform(-3000, 12000)) for _ in range(1500)]
    pts += [(a[0] + 0.5, a[1] - 0.5) for a, _b in edges]          # 꼭짓점 바로 곁
    pts += [(-1.0e6, 4000.0), (1.0e6, 4000.0), (6000.0, 1.0e6)]   # 격자 밖
    outside = 0
    for p in pts:
        e = D._escapes(p, edges)
        assert D._escapes_grid(p, edges, grid) == e, p
        outside += e
    assert 0 < outside < len(pts)      # 안·밖이 섞여 있어야 의미 있는 비교


def test_union_area_and_segs_same():
    faces = [_ccw([(0, 0), (10000, 0), (10000, 8000), (0, 8000)]), _ccw([(10000, 0), (16000, 0), (16000, 8000), (10000, 8000)]),
             _ccw([(3000, 8000), (7000, 8000), (7000, 12000), (3000, 12000)])]
    segs = VP.union_segs(faces)
    assert segs == VP._union_directed(D.outline_of(tuple(faces), brute=True))
    assert VP.union_mm2(faces) == 16000 * 8000 + 4000 * 4000
