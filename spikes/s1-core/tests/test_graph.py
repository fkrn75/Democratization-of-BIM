# SPDX-License-Identifier: MPL-2.0
import pytest

from dobim_core import queries as Q
from dobim_core.graph import CycleError, Graph, RefGraph


class Inp:
    def __init__(self):
        self.v = {"a": 1, "b": 2}

    def load(self, key):
        return self.v[key[1]]


def sum_ab(g):
    return g.input(("x", "a")) + g.input(("x", "b"))


def parity(g):
    return g.q(sum_ab) % 2


def top(g):
    return g.q(parity) * 10


def test_의존성은_읽기로_자동_기록되고_값은_요청될_때만_계산된다():
    inp = Inp()
    g = Graph(inp)
    assert g.recomputes == 0
    assert g.q(top) == 10
    assert g.recomputes == 3
    inp.v["a"] = 2
    g.touch(("x", "a"))
    assert g.recomputes == 3          # 표시만 하고 계산은 미룬다
    assert g.q(top) == 0
    assert g.recomputes == 6


def test_결과가_같으면_전파를_멈춘다_조기_차단():
    inp = Inp()
    g = Graph(inp)
    g.q(top)
    before = g.recomputes
    inp.v["a"] = 3                     # 합은 달라지지만 홀짝은 같다
    g.touch(("x", "a"))
    g.q(top)
    assert g.recomputes - before == 2  # sum_ab, parity 만 다시. top 은 건너뛴다


def test_순환은_오류():
    def f(g):
        return g.q(h)

    def h(g):
        return g.q(f)

    with pytest.raises(CycleError):
        Graph(Inp()).q(f)


def test_기준_그래프는_캐시가_없다():
    inp = Inp()
    r = RefGraph(inp)
    assert r.q(top) == 10
    inp.v["a"] = 2
    assert r.q(top) == 0


def test_벽을_평행이동해도_조인이_그대로면_윤곽은_바뀌지_않는다(base):
    """문 투영 명세는 호스트 벽의 국소 좌표라 벽이 움직여도 변하지 않는다."""
    from dobim_core import fixture as F
    eng = base.eng
    eng.g.q(Q.t0_state)
    spec_door = eng.g.q(Q.ifc_spec, F.D2)
    node = eng.g.nodes[(Q.ifc_spec, F.D2)]
    stamp = node.changed
    assert base.run("wall.move", {"id": F.W3, "dx": 500, "dy": 0})["ok"]
    assert eng.g.q(Q.ifc_spec, F.D2) == spec_door and node.changed == stamp   # 전파 차단
    # 반면 일람표의 세계 좌표는 문을 따라가야 한다
    rows = {r[0]: r for r in eng.g.q(Q.schedule_sorted, "door")}
    assert rows[F.D2][9] == 3500.0
