# SPDX-License-Identifier: MPL-2.0
"""자동 id(Ctx.new_id): 충돌이 없으면 첫 후보(골든 id 불변), 이미 있는 id 와 겹치면 결정적으로 다음 후보로 넘어간다.
병합이 상대의 자동 id 요소를 들여온 뒤에도 우리 다음 자동 id 가 CONSTRAINT 로 막히지 않게 하는 장치다(S8 검수 지적)."""
import uuid

import ifcopenshell.guid

from dobim_core import fixture as F
from dobim_core.engine import Ctx


def cand(eng, kind, seq, n):
    return ifcopenshell.guid.compress(uuid.uuid5(uuid.UUID(eng.project_guid_uuid), f"{kind}:{seq}:{n}").hex)


def test_충돌이_없으면_첫_후보를_그대로_쓴다(base):
    eng = base.eng
    seq = eng.head + 1
    r = base.run("wall.create", {"level": F.LV1, "axis": [0, 9000, 2000, 9000]})
    assert r["ok"] and r["result"]["id"] == cand(eng, "wall", seq, 1)


def test_점유된_후보는_건너뛰고_결정적이다(base):
    """다음 자동 id 의 첫 후보를 (병합이 들여온 요소처럼) 미리 점유해 두면 두 번째 후보를 쓴다."""
    eng = base.eng
    seq = eng.head + 1
    first = cand(eng, "wall", seq, 1)
    assert base.run("wall.create", {"id": first, "level": F.LV1, "axis": [0, 7000, 2000, 7000]})["ok"]
    # 지금 head 는 seq 다. 같은 seq 의 첫 후보가 이미 있는 상태를 Ctx 로 직접 만든다
    c = Ctx(eng, seq, "t", True)
    got = c.new_id("wall")
    assert got == cand(eng, "wall", seq, 2) and got not in eng.st.el
    nxt = c.new_id("wall")   # 같은 실행에서 다시 부르면 이미 내준 id 와 겹치지 않는다
    assert nxt not in (first, got)
    assert Ctx(eng, seq, "t", True).new_id("wall") == got   # 새 Ctx 도 같은 값(상태에만 의존)


def test_점유된_자동_id가_있어도_자동_생성이_계속_성공한다(base):
    eng = base.eng
    # 앞으로 몇 개 seq 의 첫 후보를 모두 점유한다(상대가 같은 seq 에 자동 생성한 요소를 들여온 상황)
    held = [cand(eng, "wall", eng.head + 1 + i, 1) for i in range(1, 4)]
    for i, h in enumerate(held):
        assert base.run("wall.create", {"id": h, "level": F.LV1, "axis": [0, 6000 + i * 100, 2000, 6000 + i * 100]})["ok"]
    for i in range(4):
        r = base.run("wall.create", {"level": F.LV1, "axis": [0, 5000 + i * 100, 2000, 5000 + i * 100]})
        assert r["ok"], r.get("error")
