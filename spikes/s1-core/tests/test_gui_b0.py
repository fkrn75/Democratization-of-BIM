# SPDX-License-Identifier: MPL-2.0
"""GUI B0: 패리티 G 정적 검사기(tools/parity_g.py)와 조작 기록 재생기(tools/replay.py). GUI 없이 커맨드 목록 파일만으로 본다."""
import copy
import json
from pathlib import Path

import pytest

import parity_g as PG
import replay as RP
from dobim_core import fixture as F

GUI = Path(PG.DEFAULT_ACTIONS).parent.parent
REAL_ACTIONS = json.loads(Path(PG.DEFAULT_ACTIONS).read_text(encoding="utf-8"))
REAL_EXEMPT = PG.load_exempt_yaml(Path(PG.DEFAULT_EXEMPT).read_text(encoding="utf-8"))
GUID = "5d0b1a50-0000-4a00-8000-0d0b1a50a0b0"


def fake(actions, exempt=None):
    return PG.check({"format": PG.FORMAT, "client_files": ["src/lib/commands.ts"], "actions": actions},
                    REAL_EXEMPT if exempt is None else exempt)


def codes(res):
    return sorted({e["code"] for e in res["errors"]})


# ================================================================ 패리티 G 정적 검사
def test_저장소의_actions_json은_통과한다():
    res = PG.check(REAL_ACTIONS, REAL_EXEMPT)
    assert res["pass"], res["errors"]
    assert res["counts"]["exempt_actions"] == res["counts"]["exempt_entries"] == 6
    assert not res["warnings"]


def test_명령줄_종료코드(tmp_path, capsys):
    assert PG.main([]) == 0
    bad = tmp_path / "a.json"
    bad.write_text(json.dumps({"format": PG.FORMAT, "actions": [{"id": "x.y", "command": "no.such"}]}), encoding="utf-8")
    assert PG.main(["--actions", str(bad)]) == 1
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert PG.main(["--actions", str(tmp_path / "broken.json")]) == 1


def test_없는_커맨드를_가리키면_실패():
    res = fake([{"id": "wall.draw", "command": "wall.craete"}, {"id": "a.b", "commands": ["view.scene", "no.such"]}])
    assert not res["pass"] and codes(res) == ["E_UNKNOWN_COMMAND"] and len(res["errors"]) == 2


def test_매핑이_없거나_둘이면_실패():
    res = fake([{"id": "wall.draw"}, {"id": "wall.move", "command": "wall.move", "exempt": True}, {"id": "a.b", "commands": []}])
    assert codes(res) == ["E_FORMAT"] and len(res["errors"]) == 3


def test_형식_오류():
    assert codes(fake([{"id": "Bad Id", "command": "wall.move"}])) == ["E_FORMAT"]
    assert codes(fake([{"id": "a.b", "command": "wall.move"}, {"id": "a.b", "command": "wall.set"}])) == ["E_FORMAT"]
    assert codes(fake([{"id": "a.b", "command": "wall.move", "shortcut": "W"}])) == ["E_FORMAT"]
    assert codes(PG.check({"format": "x", "actions": [{"id": "a.b", "command": "wall.move"}]}, [])) == ["E_FORMAT"]
    assert codes(fake([{"id": "canvas.pan", "exempt": "yes"}])) == ["E_FORMAT"]


def test_제외인데_yaml에_없으면_실패():
    res = fake([{"id": "canvas.rotate", "exempt": True}])
    assert codes(res) == ["E_EXEMPT_MISSING"]
    assert {w["id"] for w in res["warnings"]} == {e["id"] for e in REAL_EXEMPT}   # 실제 yaml 항목은 이 가짜 목록에서 안 쓰인다(경고만)


def test_이유가_없으면_실패():
    ex = [{"id": "canvas.pan", "reason": "  "}, {"id": "canvas.zoom"}]
    res = fake([{"id": "canvas.pan", "exempt": True}, {"id": "canvas.zoom", "exempt": True}], ex)
    assert codes(res) == ["E_NO_REASON"] and len(res["errors"]) == 2


def test_제외_동작이_저장소를_건드리면_실패():
    # 조회가 아닌 커맨드를 부르는 제외 항목
    ex = [{"id": "canvas.hover", "reason": "강조", "queries": ["view.pick", "wall.move"]}]
    res = fake([{"id": "canvas.hover", "exempt": True}], ex)
    assert codes(res) == ["E_EXEMPT_WRITES"] and "wall.move" in res["errors"][0]["message"]
    # 커맨드 이름을 제외로 숨기기
    ex = [{"id": "wall.create", "reason": "숨김"}]
    assert codes(fake([{"id": "wall.create", "exempt": True}], ex)) == ["E_EXEMPT_WRITES"]
    # 없는 조회
    ex = [{"id": "canvas.hover", "reason": "강조", "queries": ["view.peek"]}]
    assert codes(fake([{"id": "canvas.hover", "exempt": True}], ex)) == ["E_UNKNOWN_COMMAND"]


def test_yaml_부분집합_해석():
    items = PG.load_exempt_yaml("# 주석\n- id: a.b\n  reason: 이유 # 꼬리 주석\n  queries: [view.pick, geom.snap]\n- id: 'c.d'\n  reason: \"x\"\n")
    assert items == [{"id": "a.b", "reason": "이유", "queries": ["view.pick", "geom.snap"]}, {"id": "c.d", "reason": "x"}]
    for bad in ("- id: a\n\treason: x\n", "id: a.b\n", "- id: a.b\n  sub:\n    k: v\n", "- id: a.b\n  id: c.d\n", "- id a.b\n", "- id: a.b\n  queries: [x\n"):
        with pytest.raises(PG.YamlError):
            PG.load_exempt_yaml(bad)


def test_커맨드_클라이언트_밖_직접_호출을_찾는다(tmp_path):
    src = tmp_path / "app"
    (src / "src" / "lib").mkdir(parents=True)
    (src / "src" / "lib" / "commands.ts").write_text("export const run = (c, a) => invoke('dobim', {c, a});\n", encoding="utf-8")
    (src / "src" / "Canvas.svelte").write_text("<script>\nimport {run} from './lib/commands';\nrun('view.scene', {});\n</script>\n", encoding="utf-8")
    res = PG.check({**REAL_ACTIONS}, REAL_EXEMPT, src=src)
    assert res["pass"] and res["counts"]["scanned_files"] == 2
    (src / "src" / "Bad.ts").write_text("const x = 1;\nawait invoke('dobim', {command: 'wall.create'});\n", encoding="utf-8")
    (src / "src" / "Side.rs").write_text("let c = Command::new_sidecar(\"dobim\");\nshell.sidecar(\"gui-serve\");\n", encoding="utf-8")
    res = PG.check({**REAL_ACTIONS}, REAL_EXEMPT, src=src)
    assert codes(res) == ["E_BYPASS"]
    assert {(e["id"], e["message"].split("번째")[0]) for e in res["errors"]} >= {("src/Bad.ts", "2"), ("src/Side.rs", "2")}


# ================================================================ 조작 기록 재생
def scenario(rec):
    """GUI 세션 흉내: 레벨·뷰 → 벽(두 점·연속 묶음) → 문 → 2D 선 → 화면 조작(제외) → 옮기기 → 되돌리기."""
    assert rec.do("level.add", "level.create", {"id": F.LV1, "name": "1F", "elevation": 0})["ok"]
    assert rec.do("view.new", "view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200})["ok"]
    rec.exempt("canvas.zoom")
    rec.exempt("canvas.pan")
    w1 = rec.do("wall.draw", "wall.create", {"level": F.LV1, "axis": [0, 0, 6000, 0], "thickness": 200, "height": 3000})
    assert w1["ok"]
    r = rec.exempt("canvas.drag_preview", ("geom.snap", {"view": F.V1, "x": 6030, "y": 20}))
    assert r["ok"] and r["result"]["best"]["x"] == 6000.0
    assert rec.batch("wall.draw_chain", [("wall.create", {"level": F.LV1, "axis": [6000, 0, 6000, 4000], "thickness": 200, "height": 3000}),
                                         ("wall.create", {"level": F.LV1, "axis": [6000, 4000, 0, 4000], "thickness": 200, "height": 3000}),
                                         ("join.auto", {"level": F.LV1})])["ok"]
    hit = rec.exempt("canvas.hover", ("view.pick", {"id": F.V1, "x": 3000, "y": 0}))
    wid = hit["result"]["items"][0]["id"]
    assert rec.do("door.place", "door.place", {"host": wid, "offset": 1500, "width": 900, "height": 2100})["ok"]
    assert rec.do("draft.line", "draft.create", {"view": F.V1, "shape": "line", "pts": [[0, -1500], [6000, -1500]]})["ok"]
    rec.exempt("selection.temp", ("view.scene", {"id": F.V1, "bbox": [0, -2000, 6000, 0]}))
    assert rec.do("wall.move", "wall.move", {"id": wid, "dx": 0, "dy": -100})["ok"]
    assert rec.do("props.show", "element.get", {"id": wid})["ok"]
    assert rec.do("edit.undo", "history.undo", {})["ok"]
    assert rec.do("wall.move", "wall.move", {"id": wid, "dx": 0, "dy": -50})["ok"]
    assert not rec.do("wall.move", "wall.move", {"id": "9" * 22, "dx": 1})["ok"]   # 실패한 커맨드는 기록되지 않는다
    rec.exempt("ui.panel_layout")
    return wid


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    ws = tmp_path_factory.mktemp("gui")
    rec = RP.Recorder(ws, GUID)
    try:
        scenario(rec)
        return rec.record()
    finally:
        rec.close()


def test_기록_형식(recorded):
    assert RP._check_format(recorded) is None
    assert [s["action"] for s in recorded["steps"]].count("wall.move") == 2
    assert all(set(s) <= {"action", "exempt", "query"} for s in recorded["steps"] if "exempt" in s)
    json.dumps(recorded)


def test_재생_해시가_같다(recorded, tmp_path):
    """GUI 세션(기록기, 같은 dispatch 경로)의 최종 model.hash 를 헤드리스 CLI 서브프로세스 재생이 그대로 낸다. actions.json 매핑도 맞는다."""
    res = RP.replay(recorded, tmp_path, RP._load_actions(PG.DEFAULT_ACTIONS))
    assert res["pass"], res["errors"]
    assert res["replayed"] == {k: recorded["final"][k] for k in RP.HASH_KEYS}
    assert res["cli_calls"] < 25   # 이어진 단계는 script 한 번으로 묶는다


def test_기록을_바꾸면_해시_불일치(recorded, tmp_path):
    bad = copy.deepcopy(recorded)
    step = next(s for s in bad["steps"] if s.get("command") == "draft.create")
    step["args"]["pts"][1][0] = 5000   # GUI 가 커맨드 밖에서 상태를 바꾼 것과 같은 결과
    res = RP.replay(bad, tmp_path)
    assert not res["pass"] and [e["code"] for e in res["errors"]] == ["R_HASH"]
    assert "state" in res["errors"][0]["message"]


def test_제외_동작이_변경_커맨드를_부르면_실패(tmp_path):
    rec = {"format": RP.FORMAT, "project": {"guid": GUID},
           "steps": [{"action": "level.add", "command": "level.create", "args": {"id": F.LV1, "name": "1F", "elevation": 0}},
                     {"action": "canvas.hover", "exempt": True, "query": {"command": "level.set", "args": {"id": F.LV1, "elevation": 100}}}],
           "final": {"rev": 0, "state": "", "derived": "", "ifc": ""}}
    res = RP.replay(rec, tmp_path)
    assert "R_EXEMPT" in [e["code"] for e in res["errors"]]


def test_actions_json_밖의_커맨드는_실패(recorded):
    acts = RP._load_actions(PG.DEFAULT_ACTIONS)
    acts["wall.move"] = {"commands": ["wall.set"]}   # 동작 매핑과 다른 커맨드를 냈다
    acts.pop("draft.line")
    bad = copy.deepcopy(recorded)
    bad["final"] = {"rev": -1, "state": "", "derived": "", "ifc": ""}   # 재생까지 가지 않아도 매핑 오류는 먼저 모인다
    bad["steps"] = [next(s for s in bad["steps"] if s["action"] == "wall.move"), next(s for s in bad["steps"] if s["action"] == "draft.line"),
                    {"action": "canvas.zoom", "command": "view.scene", "args": {"id": F.V1}}]   # 마지막: 제외 동작인데 커맨드를 냈다
    errs = RP.check_actions(bad, acts)
    assert [e["step"] for e in errs] == [0, 1, 2] and {e["code"] for e in errs} == {"R_ACTION"}
    assert RP.check_actions(recorded, RP._load_actions(PG.DEFAULT_ACTIONS)) == []


def test_형식_오류는_재생하지_않는다(tmp_path):
    for rec in ({}, {"format": RP.FORMAT, "project": {}, "steps": [], "final": {}},
                {"format": RP.FORMAT, "project": {"guid": GUID}, "steps": [{"action": "a.b", "exempt": True, "command": "wall.move"}],
                 "final": {"rev": 0, "state": "", "derived": "", "ifc": ""}}):
        res = RP.replay(rec, tmp_path)
        assert not res["pass"] and res["errors"][0]["code"] == "R_FORMAT" and res["cli_calls"] == 0


def test_명령줄_재생(recorded, tmp_path):
    f = tmp_path / "rec.json"
    f.write_text(json.dumps(recorded, ensure_ascii=False), encoding="utf-8")
    assert RP.main([str(f), "--actions", str(PG.DEFAULT_ACTIONS), "--keep", str(tmp_path / "ws")]) == 0
