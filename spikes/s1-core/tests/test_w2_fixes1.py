# SPDX-License-Identifier: MPL-2.0
"""W2 실사용 결함 수정 1차(도면→3D 실사용 시험에서 나온 결함 6건)의 최소 재현 시험.

1. 난간 IFC 표현 유형: IfcSweptDiskSolid 몸체는 'AdvancedSweptSolid' 여야 IFC4 규칙(IfcShapeRepresentationTypes)을 통과한다
2. 사용자 id 의 첫 글자가 4 이상(IFC GlobalId 는 첫 글자 0~3)이어도 내보낸 IFC·model.validate 가 유효하고, 다시 가져오면 원래 id 로 돌아온다
3. CLI script 는 기본이 원자적(전부 성공 또는 전부 되돌림)이다. 옛 동작(항목마다 커밋)은 --per-item
4. model.stats 는 패밀리로 놓은 문(IfcDoor 레시피)도 문으로 센다
5. family.register 는 family.validate(static) 가 오류를 내는 레시피를 거절한다(극값 표본 실패 포함)
6. 계단 조각(단 묶음·참)은 계단의 재료를 따르고, 승강기 카는 승강기의 재료를 가진다
"""
import copy
import json
import os
import subprocess
import sys

import ifcopenshell
import ifcopenshell.util.element as ue
import ifcopenshell.validate
import pytest

import kit
from conftest import ROOT
from dobim_core import fixture as F
from dobim_core.family import library

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}
BAD_W = "W" + "0" * 20 + "1"     # 첫 글자 W: IFC GlobalId 로는 무효(첫 글자는 0~3)
RUN = [1000, 1000, 4000, 1000]
TURN_U = {"type": "U", "side": "left", "risers_a": 9}


def ok(r):
    assert r["ok"], r
    return r["result"]


def file_errors(path) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(ifcopenshell.open(str(path)), lg, express_rules=True)
    return [s["message"][:160] for s in lg.statements if s["level"] in ("error", "critical")]


# ---------------------------------------------------------------- 1. 난간 표현 유형
def test_1_난간_IFC는_표현_유형_규칙을_통과한다(base, tmp_path):
    ok(base.run("railing.create", {"id": F.RL1.replace("c", "0", 1), "level": F.LV1, "path": [[0, 0], [3000, 0], [3000, 2000]], "height": 1100}))
    v = ok(base.run("model.validate", {}))
    assert v["errors"] == 0, v["violations"]
    base.eng.flush_ifc()
    rep = base.eng.ifc.ents[F.RL1.replace("c", "0", 1)].Representation.Representations[0]
    assert rep.RepresentationType == "AdvancedSweptSolid" and all(i.is_a("IfcSweptDiskSolid") for i in rep.Items)


def test_1_옛_표현_유형으로_내보낸_난간도_저작으로_가져온다(tmp_path):
    """고치기 전에 내보낸 파일('SweptSolid')을 가져와도 난간이 분리(동결)되지 않는다."""
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        rid = "0" + F.RL1[1:]
        assert b.run("railing.create", {"id": rid, "level": F.LV1, "path": [[0, 0], [3000, 0]], "height": 1000})["ok"]
        b.export("r.ifc")
        p = tmp_path / "r.ifc"
        txt = p.read_text(encoding="utf-8")
        assert "'AdvancedSweptSolid'" in txt
        p.write_text(txt.replace("'AdvancedSweptSolid'", "'SweptSolid'"), encoding="utf-8")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"], r
        assert b.run("element.get", {"id": rid}, project="imp.dobim")["result"]["origin"] == "authored"
        v = b.run("model.validate", {}, project="imp.dobim")["result"]
        assert v["errors"] == 0, v["violations"]
    finally:
        b.close()


# ---------------------------------------------------------------- 2. 사용자 id 의 GlobalId 첫 글자
def test_2_첫_글자가_4_이상인_id도_IFC_검증을_통과하고_왕복하면_id가_그대로다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("wall.create", {"id": BAD_W, "level": F.LV1, "axis": [0, 6000, 4000, 6000]})["ok"]
        assert b.run("material.set", {"id": BAD_W, "name": "Concrete"})["ok"]
        v = b.run("model.validate", {})["result"]
        assert v["errors"] == 0, v["violations"]
        h = b.hashes()
        b.export("g.ifc")
        assert file_errors(tmp_path / "g.ifc") == []
        f = ifcopenshell.open(str(tmp_path / "g.ifc"))
        assert all(e.GlobalId[0] in "0123" for e in f.by_type("IfcRoot"))
        r = b.import_ifc("g.ifc", "imp.dobim")
        assert r["ok"], r
        assert b.origins("imp.dobim") == {"authored": 8, "foreign": 0, "detached": 0}
        g = b.run("element.get", {"id": BAD_W}, project="imp.dobim")["result"]
        assert g["origin"] == "authored" and g["kind"] == "wall"
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("model.validate", {}, project="imp.dobim")["result"]["errors"] == 0
        b.export("g2.ifc", project="imp.dobim")   # 다시 내보내도 대응표가 하나뿐이고 같은 바이트다
        assert (tmp_path / "g2.ifc").read_bytes().count(b"DoBIM_IdMap") == 1
    finally:
        b.close()


def test_2_유효한_id만_있으면_대응표를_붙이지_않는다(base, tmp_path):
    r = base.run("ifc.export", {"path": "v.ifc"})
    if not r["ok"]:
        r = base.run("ifc.export", {"path": "v.ifc"}, confirm=r["error"]["details"]["confirm_token"])
    assert r["ok"], r
    assert b"DoBIM_IdMap" not in (base.path.parent / "v.ifc").read_bytes()


def test_2_IFC2x3_내보내기도_유효한_GlobalId(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("wall.create", {"id": BAD_W, "level": F.LV1, "axis": [0, 6000, 4000, 6000]})["ok"]
        r = b.destructive("ifc.export", {"path": "g3.ifc", "schema": "IFC2X3"})
        assert r["ok"], r
        f = ifcopenshell.open(str(tmp_path / "g3.ifc"))
        assert all(e.GlobalId[0] in "0123" for e in f.by_type("IfcRoot"))
        r = b.import_ifc("g3.ifc", "imp3.dobim")
        assert r["ok"], r
        assert b.run("element.get", {"id": BAD_W}, project="imp3.dobim")["ok"]
    finally:
        b.close()


# ---------------------------------------------------------------- 3. script 원자성
def _cli(ws, *args):
    p = subprocess.run([sys.executable, "-m", "dobim_core", "--workspace", str(ws), *args], capture_output=True, env=ENV,
                       stdin=subprocess.DEVNULL, cwd=ws, timeout=120)
    return p.returncode, json.loads(p.stdout.decode("utf-8"))


@pytest.fixture
def ws(tmp_path):
    code, _ = _cli(tmp_path, "run", "project.new", "path=p.dobim", f"guid={F.GUID}")
    assert code == 0
    return tmp_path


def _walls(ws):
    return _cli(ws, "run", "project.info", "-p", "p.dobim")[1]["result"]["counts"].get("wall", 0)


def test_3_script는_기본이_원자적이라_중간_실패면_아무것도_남지_않는다(ws):
    lines = [{"command": "level.create", "args": {"id": F.LV1, "name": "1F", "elevation": 0}}]
    lines += [{"command": "wall.create", "args": {"level": F.LV1, "axis": [0, i * 100, 3000, i * 100]}} for i in range(5)]
    lines += [{"command": "wall.move", "args": {"id": "1" + "9" * 21, "dx": 1}}]   # 없는 벽: 7번째에서 실패
    (ws / "s.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    rev0 = _cli(ws, "run", "project.info", "-p", "p.dobim")[1]["rev"]
    code, out = _cli(ws, "script", "s.jsonl", "-p", "p.dobim")
    assert code != 0 and not out["ok"] and out["error"]["code"] == "NOT_FOUND" and "/6" in (out["error"].get("path") or "")
    assert _walls(ws) == 0 and _cli(ws, "run", "project.info", "-p", "p.dobim")[1]["rev"] == rev0
    code, out = _cli(ws, "script", "s.jsonl", "-p", "p.dobim", "--per-item")   # 옛 동작: 앞 항목은 따로 커밋된다
    assert code != 0 and [x["index"] for x in out["result"] if not x["ok"]] == [6]
    assert _walls(ws) == 5


def test_3_기본_script에_조회를_섞으면_per_item을_안내한다(ws):
    (ws / "q.jsonl").write_text(json.dumps({"command": "model.stats", "args": {}}), encoding="utf-8")
    code, out = _cli(ws, "script", "q.jsonl", "-p", "p.dobim")
    assert code != 0 and out["error"]["code"] == "USAGE" and "--per-item" in out["error"]["hint"]
    code, out = _cli(ws, "script", "q.jsonl", "-p", "p.dobim", "--per-item")
    assert code == 0 and out["ok"]


# ---------------------------------------------------------------- 4. model.stats 와 패밀리 문
def test_4_model_stats는_패밀리로_놓은_문도_센다(base):
    n0 = ok(base.run("model.stats", {}))["counts"]["door"]
    ok(base.run("family.place", {"family": "door.single", "host": F.W3, "offset": 1500}))
    ok(base.run("family.place", {"family": "window.fixed", "host": F.W3, "offset": 3500}))
    c = ok(base.run("model.stats", {}))["counts"]
    assert c["door"] == n0 + 1 and c["window"] == 1 and c["finst"] == 2


# ---------------------------------------------------------------- 5. family.validate == family.register
def _bad_recipe():
    r = copy.deepcopy(library.load("appliance.washer"))
    r["id"] = "user.washer_wide_door"
    r["params"]["door_r"]["max"] = 400   # 문 반지름 최대 400: 극값 표본에서 제약(door_r*2 < width - 60)이 깨진다
    return r


def test_5_validate가_실패하는_레시피는_register도_거절한다(base):
    v = ok(base.run("family.validate", {"recipe": _bad_recipe(), "level": "static"}))
    assert not v["ok"] and v["checks"]["values"] == "fail"
    r = base.run("family.register", {"recipe": _bad_recipe()})
    assert not r["ok"] and r["error"]["code"] == "FAMILY_INVALID", r
    assert r["error"]["details"]["issues"][0]["code"] == v["errors"][0]["code"]
    good = copy.deepcopy(_bad_recipe())
    good["params"]["door_r"]["max"] = 240
    assert ok(base.run("family.validate", {"recipe": good, "level": "static"}))["ok"]
    assert ok(base.run("family.register", {"recipe": good}))["created"]


# ---------------------------------------------------------------- 6. 재료 전파
def test_6_꺾인_계단의_단_묶음과_참은_계단의_재료를_따른다(base):
    sid = "0" + F.ST1[1:]
    ok(base.run("stair.create", {"id": sid, "level": F.LV1, "run": RUN, "width": 1200, "height": 3000, "risers": 18, "turn": TURN_U}))
    ok(base.run("material.set", {"id": sid, "name": "Concrete"}))
    base.eng.flush_ifc()
    parts = list(base.eng.ifc.parts[sid].values())
    assert len(parts) == 3 and all(ue.get_material(p, should_inherit=False).Name == "Concrete" for p in parts)
    ok(base.run("material.set", {"id": sid, "name": "Steel"}))
    ok(base.run("stair.set", {"id": sid, "width": 1100}))   # 다시 투영해도 따른다
    base.eng.flush_ifc()
    parts = list(base.eng.ifc.parts[sid].values())
    assert all(ue.get_material(p, should_inherit=False).Name == "Steel" for p in parts)
    ok(base.run("material.set", {"id": sid, "name": None}))
    base.eng.flush_ifc()
    assert all(ue.get_material(p, should_inherit=False) is None for p in base.eng.ifc.parts[sid].values())
    assert ok(base.run("model.validate", {}))["errors"] == 0
    h = base.eng.hashes()
    ok(base.run("material.set", {"id": sid, "name": "Brick"}))
    ok(base.run("history.undo", {}))   # 되돌리면 IFC 해시까지 같다
    assert base.eng.hashes()["ifc"] == h["ifc"]


def test_6_재료가_있는_꺾인_계단을_왕복해도_저작이다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        sid = "0" + F.ST1[1:]
        assert b.run("stair.create", {"id": sid, "level": F.LV1, "run": RUN, "width": 1200, "height": 3000, "risers": 18, "turn": TURN_U})["ok"]
        assert b.run("material.set", {"id": sid, "name": "Concrete"})["ok"]
        h = b.hashes()
        b.export("s.ifc")
        f = ifcopenshell.open(str(tmp_path / "s.ifc"))
        assert all(ue.get_material(p, should_inherit=False).Name == "Concrete" for p in f.by_type("IfcStairFlight"))
        r = b.import_ifc("s.ifc", "imp.dobim")
        assert r["ok"], r
        assert b.run("element.get", {"id": sid}, project="imp.dobim")["result"]["origin"] == "authored"
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


def test_6_승강기_카는_승강기의_재료를_가진다(base):
    eid = "0" + F.EL1[1:]
    ok(base.run("elevator.create", {"id": eid, "level": F.LV1, "shaft": [4000, 500, 5800, 2300], "rise": 6000}))
    ok(base.run("material.set", {"id": eid, "name": "Steel"}))
    ok(base.run("elevator.set", {"id": eid, "cab": [1100, 1300, 2200]}))
    base.eng.flush_ifc()
    car = base.eng.ifc.ents[eid]
    assert car.is_a("IfcTransportElement") and car.Representation is not None
    assert ue.get_material(car, should_inherit=False).Name == "Steel"
