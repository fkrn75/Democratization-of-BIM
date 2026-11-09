# SPDX-License-Identifier: MPL-2.0
"""수요 구동 증분 그래프 (ADR-0006).

- 의존성은 실행 중 읽기로 자동 기록한다.
- 입력이 바뀌면 역의존 노드를 "확인 필요"로 표시만 하고, 값은 요청될 때 계산한다.
- 결과가 같으면 changed 를 올리지 않아 전파가 멈춘다(조기 차단).
- 순환은 오류다.
"""
from __future__ import annotations

from typing import Any, Callable

CLEAN, MAYBE, NEW = 0, 1, 2


class CycleError(RuntimeError):
    pass


class Node:
    __slots__ = ("key", "fn", "args", "value", "deps", "rdeps", "state",
                 "changed", "verified", "running")

    def __init__(self, key, fn=None, args=()):
        self.key = key
        self.fn = fn
        self.args = args
        self.value = None
        self.deps: list[Node] = []
        self.rdeps: set[Node] = set()
        self.state = NEW
        self.changed = 0
        self.verified = 0
        self.running = False


class Graph:
    """메모이즈 질의 그래프. 입력 값은 inputs.load(key) 가 제공한다."""

    def __init__(self, inputs: Any):
        self.inputs = inputs
        self.nodes: dict[tuple, Node] = {}
        self.clock = 1
        self._stack: list[list[Node]] = []
        self.recomputes = 0            # 계측: 재계산 횟수
        self.on_mark: Callable[[Node], None] | None = None

    # ---- 입력
    def input(self, key: tuple) -> Any:
        node = self.nodes.get(key)
        if node is None:
            node = Node(key)
            node.value = self.inputs.load(key)
            node.state = CLEAN
            node.changed = node.verified = self.clock
            self.nodes[key] = node
        if self._stack:
            self._stack[-1].append(node)
        return node.value

    def touch(self, key: tuple) -> None:
        """입력이 바뀌었다는 알림. 값이 실제로 달라졌을 때만 역의존을 표시한다."""
        node = self.nodes.get(key)
        if node is None:
            return
        new = self.inputs.load(key)
        if new == node.value:
            return
        self.clock += 1
        node.value = new
        node.changed = self.clock
        self._mark(node)

    def drop(self, keys) -> int:
        """질의 노드를 그래프에서 치운다(뷰 삭제 뒤의 낡은 노드 정리용). 치우는 노드를 아직 읽고 있는 남은 노드가 있으면
        그 노드는 처음부터 다시 계산하게 표시한다. 입력 노드도 치울 수 있고, 다음에 읽으면 다시 만들어진다."""
        gone = [n for n in (self.nodes.pop(k, None) for k in keys) if n is not None]
        for n in gone:
            for d in n.deps:
                d.rdeps.discard(n)
        gone_ids = {id(n) for n in gone}
        for n in gone:
            for r in n.rdeps:
                if id(r) not in gone_ids:
                    r.deps = [d for d in r.deps if d is not n]
                    r.state = NEW
                    self._mark(r)
            n.rdeps = set()
        return len(gone)

    def _mark(self, node: Node) -> None:
        todo = list(node.rdeps)
        on_mark = self.on_mark
        while todo:
            n = todo.pop()
            if n.state == CLEAN:
                n.state = MAYBE
                if on_mark is not None:
                    on_mark(n)
                todo.extend(n.rdeps)

    # ---- 질의
    def q(self, fn: Callable, *args) -> Any:
        key = (fn, *args)
        node = self.nodes.get(key)
        if node is None:
            node = Node(key, fn, args)
            self.nodes[key] = node
        if node.state != CLEAN:
            self._pull(node)
        if self._stack:
            self._stack[-1].append(node)
        return node.value

    def _pull(self, node: Node) -> None:
        if node.running:
            raise CycleError(f"순환 의존: {node.key[0].__name__}{node.key[1:]}")
        if node.state == MAYBE:
            # 읽은 순서대로 확인한다. 앞선 입력이 바뀌면 뒤쪽의 낡은 의존은 건드리지 않는다.
            for dep in node.deps:
                if dep.state != CLEAN:
                    self._pull(dep)
                if dep.changed > node.verified:
                    break
            else:
                node.state = CLEAN
                node.verified = self.clock
                return
        self._recompute(node)

    def _recompute(self, node: Node) -> None:
        self.recomputes += 1
        node.running = True
        collected: list[Node] = []
        self._stack.append(collected)
        try:
            value = node.fn(self, *node.args)
        finally:
            self._stack.pop()
            node.running = False
        for d in node.deps:
            d.rdeps.discard(node)
        for d in collected:
            d.rdeps.add(node)
        node.deps = collected
        if node.state == NEW or value != node.value:
            self.clock += 1
            node.value = value
            node.changed = self.clock
        node.state = CLEAN
        node.verified = self.clock


class RefGraph:
    """캐시 없는 기준 구현. 같은 질의 함수를 매번 처음부터 계산한다.

    증분 그래프가 이 값과 다르면 무효화 누락 또는 조기 차단 오류다 (차등 시험의 기준).
    """

    def __init__(self, inputs: Any):
        self.inputs = inputs

    def input(self, key: tuple) -> Any:
        return self.inputs.load(key)

    def q(self, fn: Callable, *args) -> Any:
        return fn(self, *args)
