# SPDX-License-Identifier: MPL-2.0
"""S8 합격 기준: 시나리오(20종)마다 결정적 결과·충돌 감지·대칭·사후 검사. 한 프로세스에서 한 번씩 돌린 결과를 판정기에 건다.
프로세스·시드·순서를 바꿔 반복하는 결정성 시험은 test_determinism.py 가 한다."""
import json

import pytest

import s8check
import s8lib
from scenarios import SCENARIOS, BY_ID

_cache: dict = {}


def result(tmp_path_factory, sid):
    if sid not in _cache:
        _cache[sid] = s8lib.run_scenario(BY_ID[sid], tmp_path_factory.mktemp(sid))
    return _cache[sid]


def test_시나리오는_10종_이상이고_유형을_고루_덮는다():
    assert len(SCENARIOS) >= 10
    types = {t for s in SCENARIOS for t in s["expect"].get("conflicts", [])}
    assert types >= {"SAME_FIELD", "DELETE_MODIFY", "HOST_DELETED", "JOIN_TARGET_DELETED", "DOOR_OUT_OF_HOST", "DOOR_OUTSIDE_TRIMMED",
                     "DUPLICATE_WALL", "DOOR_OVERLAP", "LEVEL_NAME_DUPLICATE", "PSET_SAME_PROP", "MATERIAL_SAME", "ID_COLLISION"}
    assert sum(1 for s in SCENARIOS if s["expect"].get("clean")) >= 4


@pytest.mark.parametrize("sid", [s["id"] for s in SCENARIOS])
def test_시나리오(tmp_path_factory, sid):
    r = result(tmp_path_factory, sid)
    fails = s8check.check([{sid: r}], [sid])
    assert fails == [], json.dumps(fails, ensure_ascii=False, indent=1)


def test_충돌_보고는_요소_id와_양쪽_op와_사유를_담는다(tmp_path_factory):
    r = result(tmp_path_factory, "s05_host_deleted_vs_door_placed")
    c = r["flows"]["A<-B"]["preview"]["conflicts"][0]
    assert c["type"] == "HOST_DELETED" and set(c["elements"]) == {s8lib.F.W3, "2" + "0" * 18 + "103"}
    assert ("element.delete" in [o[1] for o in c["ours_ops"]]) and ("door.place" in [o[1] for o in c["theirs_ops"]])
    assert c["take"] == ["ours", "theirs"]
    # 반대 방향에서는 같은 충돌의 양쪽이 서로 뒤바뀌어 보인다
    d = r["flows"]["B<-A"]["preview"]["conflicts"][0]
    assert d["key"] == c["key"] and d["ours_ops"] == c["theirs_ops"] and d["theirs_ops"] == c["ours_ops"]
