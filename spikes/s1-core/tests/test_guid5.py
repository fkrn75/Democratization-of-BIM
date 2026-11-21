"""부속 GlobalId 빠른 계산(_guid5)이 원래 식 ifcopenshell.guid.compress(uuid.uuid5(...).hex) 와 같은지."""
import random
import uuid

import ifcopenshell.guid

from dobim_core import ifc_model as IM


def _ref(name: str) -> str:
    return ifcopenshell.guid.compress(uuid.uuid5(IM._NS, name).hex)


def test_guid5_matches_reference():
    rnd = random.Random(20261122)
    names = [f"part:{ifcopenshell.guid.new()}:{lab}" for lab in ("opening:0", "opening:63", "parapet", "landing", "face:7", "xopening:12")]
    names += ["", "part::", "part:벽-1:opening:3", "opening:\U0001F600", "x" * 1000]
    names += ["".join(chr(rnd.randrange(32, 0x3000)) for _ in range(rnd.randrange(0, 40))) for _ in range(20000)]
    for n in names:
        assert IM._guid5(n) == _ref(n), n


def test_part_guid_shape():
    g = IM.part_guid("eid", "opening:0")
    assert len(g) == 22 and g == _ref("part:eid:opening:0")
    assert ifcopenshell.guid.expand(g) == uuid.uuid5(IM._NS, "part:eid:opening:0").hex
