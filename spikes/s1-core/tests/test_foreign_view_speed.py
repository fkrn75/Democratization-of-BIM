"""외래 단면·입면 첫 뷰 단축(IO-04, w1x-12)이 값을 바꾸지 않는지: 삼각형 전용 모서리 뽑기 = 일반 모서리 뽑기,
클래스 기억 판정 = 매번 판정, 합친 후보 캐시를 쓴 _f_hidden = 캐시 없이 처음부터 본 _f_hidden."""
import random

from dobim_core import drawing as D
from dobim_core import solids as SO


def _box(x0, y0, z0, dx, dy, dz):
    v = [(x0 + i * dx, y0 + j * dy, z0 + k * dz) for i in (0, 1) for j in (0, 1) for k in (0, 1)]
    q = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = [t for a, b, c, d in q for t in ((a, b, c), (a, c, d))]
    return v, tris


def test_삼각형_전용_모서리는_일반_함수와_같다():
    rng = random.Random(7)
    cases = [_box(0, 0, 0, 1000, 200, 3000), _box(-0.0, 0.0, -0.0, 0.5, 0.5, 0.5)]
    for _ in range(30):   # 아무렇게나 흔든 메시(퇴화 면·0 좌표 포함)
        v, tris = _box(rng.uniform(-5e4, 5e4), rng.uniform(-5e4, 5e4), 0.0, rng.uniform(1, 9e3), rng.uniform(1, 9e3), rng.uniform(1, 9e3))
        v = [tuple(c + rng.choice((0.0, -0.0, rng.uniform(-50, 50))) for c in p) for p in v]
        tris = tris + [(0, 0, 1)]
        rng.shuffle(tris)
        cases.append((v, tris))
    for v, tris in cases:
        got = SO.feature_edges_tris(v, tris)
        want = SO.feature_edges(v, tuple((t,) for t in tris))
        assert repr(got) == repr(want)   # -0.0 같은 부호까지 같다


def test_삼각형이_아닌_면이_섞이면_일반_함수로_넘긴다():
    v, _ = _box(0, 0, 0, 1, 1, 1)
    tris = [(0, 1, 3, 2), (4, 6, 7)]
    assert SO.feature_edges_tris(v, tris) == SO.feature_edges(v, tuple((t,) for t in tris))


def test_클래스_기억_판정은_매번_판정과_같다():
    import ifcopenshell
    from dobim_core import foreign_geom as FG
    f = ifcopenshell.file(schema="IFC4")
    for cls in ("IfcWall", "IfcDoor", "IfcOpeningElement", "IfcVirtualElement", "IfcBuildingStorey", "IfcSpace", "IfcSite",
                "IfcFurnishingElement", "IfcStairFlight"):
        for _ in range(2):
            e = f.create_entity(cls)
            assert FG.category(e) == FG._category(e)
            want = ("storey" if e.is_a("IfcBuildingStorey") else "space" if e.is_a("IfcSpace")
                    else "spatial" if e.is_a("IfcSpatialElement") else None)
            assert FG._klass(e) == want


def _occ(rng, n):
    polys, boxes, bins = [], [], {}
    for i in range(n):
        a, b, d = rng.uniform(0, 9000), rng.uniform(0, 9000), rng.uniform(0, 5000)
        p2 = [(a, b), (a + rng.uniform(100, 4000), b), (a, b + rng.uniform(100, 4000))]
        plane = (rng.uniform(-0.1, 0.1), rng.uniform(-0.1, 0.1), d)
        polys.append((tuple(p2), plane))
        boxes.append((min(q[0] for q in p2), max(q[0] for q in p2), min(q[1] for q in p2), max(q[1] for q in p2), d - 500))
        bx = boxes[-1]
        for k in range(int(bx[0] // D.F_BIN), int(bx[1] // D.F_BIN) + 1):
            for m in range(int(bx[2] // D.F_BIN), int(bx[3] // D.F_BIN) + 1):
                bins.setdefault((k, m), []).append((bx[4], i))
    return (tuple(polys), tuple(boxes), {k: tuple(sorted(x)) for k, x in bins.items()}, (), {})


def test_합친_후보_캐시를_쓴_가림은_처음부터_본_것과_같다():
    rng = random.Random(3)
    occ = _occ(rng, 300)
    segs = [((rng.uniform(0, 9000), rng.uniform(0, 9000), rng.uniform(0, 8000)),
             (rng.uniform(0, 9000), rng.uniform(0, 9000), rng.uniform(0, 8000))) for _ in range(400)]
    first = [D._f_hidden(occ, A, B) for A, B in segs]
    again = [D._f_hidden(occ, A, B) for A, B in reversed(segs)][::-1]   # 캐시가 찬 뒤 다른 순서로
    cold = []
    for A, B in segs:   # 매번 새 색인 객체(캐시를 처음부터)
        o = occ[:2] + (dict(occ[2]),) + occ[3:]
        cold.append(D._f_hidden(o, A, B))
    assert first == again == cold
    assert any(first)


def test_한꺼번에_투영은_점마다_투영과_같다():
    rng = random.Random(11)
    pts = [(rng.uniform(-9e4, 9e4), rng.uniform(-9e4, 9e4), rng.uniform(-3e3, 6e4)) for _ in range(200)] + [(0.0, -0.0, -0.0)]
    cfgs = [("plan", "L", 1200.0, 3000.0, 100), ("sec", 1000.0, -2000.0, 0.6, 0.8, 0.8, -0.6, -500.0, 9000.0, True),
            ("elev", -0.0, 0.0, 0.0, 1.0, 1.0, 0.0, -1e9, 1e9, False)]
    for cfg in cfgs:
        assert repr(D._f_pts(cfg, pts)) == repr([D._f_pt(cfg, p) for p in pts])
