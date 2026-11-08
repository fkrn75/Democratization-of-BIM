# SPDX-License-Identifier: MPL-2.0
import importlib.util
import json

import kit
from dobim_core import fixture as F, queries as Q
from dobim_core.engine import Engine, affected_of, invert


def test_기준_프로젝트의_일람표와_파생값(base):
    eng = base.eng
    doors = {r[0]: r for r in eng.g.q(Q.schedule_sorted, "door")}
    assert doors[F.D1][9:12] == (1500.0, 0.0, 0.0)            # 세계 좌표
    assert doors[F.D1][12] == "EI30"                           # IFC 정본의 속성셋 값
    walls = {r[0]: r for r in eng.g.q(Q.schedule_sorted, "wall")}
    assert walls[F.W1][11] == "Concrete"
    start, end, status = eng.g.q(Q.wall_trim, F.W1)            # W1-W2 조인: W1 의 끝이 W2 축선(x=6000)에 정리된다
    assert status == "ok" and end == 6000.0


def test_같은_입력은_같은_해시를_낸다_결정성(tmp_path):
    hs = []
    for i in range(2):
        s = kit.Session(tmp_path / f"d{i}.dobim")
        for name, args in F.SCRIPT:
            s.run(name, args)
        hs.append(s.eng.hashes())
        s.close()
    assert hs[0] == hs[1]


def test_id_는_프로젝트와_순번에서_결정적으로_나온다(tmp_path):
    ids = []
    for i in range(2):
        s = kit.Session(tmp_path / f"i{i}.dobim")
        s.run("level.create", {"name": "1F", "elevation": 0})
        ids.append(sorted(s.eng.st.el))
        s.close()
    assert ids[0] == ids[1] and len(ids[0][0]) == 22


def _core(h):
    return {k: v for k, v in h.items() if k != "rev"}


def test_undo_redo_해시_복원(base):
    eng = base.eng
    h0 = eng.hashes()
    assert base.run("wall.move", {"id": F.W1, "dx": 600})["ok"]
    h1 = eng.hashes()
    assert h1["state"] != h0["state"] and h1["derived"] != h0["derived"] and h1["ifc"] != h0["ifc"]
    assert base.run("history.undo")["ok"]
    assert _core(eng.hashes()) == _core(h0)
    assert base.run("history.redo")["ok"]
    assert _core(eng.hashes()) == _core(h1)
    base.run("history.undo")
    base.run("wall.move", {"id": F.W1, "dx": 1})            # 새 변경은 redo 스택을 비운다
    assert base.run("history.redo")["error"]["code"] == "NOTHING_TO_REDO"


def test_dry_run은_아무것도_바꾸지_않고_변경_내용을_보여준다(base):
    eng = base.eng
    before = eng.hashes()
    r = base.run("wall.move", {"id": F.W1, "dx": 600}, dry_run=True)
    assert r["ok"] and r["dry_run"] and r["rev"] == before["rev"]
    assert r["affected"]["modified"] == [F.W1]
    assert eng.hashes() == before


def test_배치는_원자적이다_마지막이_실패하면_전부_되돌린다(base):
    eng = base.eng
    before = eng.hashes()
    r = base.batch([{"command": "wall.move", "args": {"id": F.W1, "dx": 100}},
                    {"command": "pset.set", "args": {"id": F.W1, "pset": "Pset_WallCommon", "prop": "FireRating", "value": "EI90"}},
                    {"command": "wall.move", "args": {"id": "9" * 22, "dx": 1}}])
    assert not r["ok"] and r["error"]["code"] == "NOT_FOUND"
    assert eng.hashes() == before
    r = base.batch([{"command": "wall.move", "args": {"id": F.W1, "dx": 100}},
                    {"command": "wall.move", "args": {"id": F.W2, "dx": 100}}])
    assert r["ok"]                                            # 성공한 배치는 하나의 트랜잭션: 한 번에 되돌려진다
    u = base.run("history.undo")["result"]
    assert u["ops"] == [{"op": "wall.move", "count": 2}] and u["ops_total"] == 2
    assert eng.state_hash() == before["state"]


def test_배치_스키마_오류는_위치를_가리킨다(base):
    r = base.batch([{"command": "wall.move", "args": {"id": F.W1, "dx": 1}},
                    {"command": "wall.move", "args": {"id": F.W1, "dx": "x"}}])
    assert r["error"]["code"] == "SCHEMA_INVALID" and r["error"]["path"] == "/commands/1/dx"


def test_expected_rev_충돌(base):
    rev = base.eng.rev
    assert base.run("wall.move", {"id": F.W1, "dx": 1}, expected_rev=rev)["ok"]
    r = base.run("wall.move", {"id": F.W1, "dx": 1}, expected_rev=rev)
    assert r["error"]["code"] == "REV_CONFLICT" and r["error"]["retryable"] and r["error"]["details"]["actual"] == rev + 1


def test_제약_위반과_변경없음(base):
    assert base.run("door.place", {"host": F.W2, "offset": 100, "width": 900})["error"]["code"] == "CONSTRAINT"
    assert base.run("element.delete", {"id": F.LV1})["error"]["code"] == "CONSTRAINT"        # 벽이 있는 레벨
    assert base.run("pset.set", {"id": F.W1, "pset": "DoBIM_Intent", "prop": "x", "value": 1})["error"]["code"] == "CONSTRAINT"
    r = base.run("join.set", {"a": F.W1, "b": F.W2})                                             # 이미 조인됨
    assert r["ok"] and r["rev"] == base.eng.rev and any("변경 없음" in w for w in r["warnings"])
    assert base.run("wall.set", {"id": F.W1, "axis": [0, 0, 100, 0]})["error"]["code"] == "CONSTRAINT"   # 호스팅된 문이 벽 밖


def test_벽을_지우면_호스팅된_문도_지워지고_취소하면_속성셋까지_돌아온다(base):
    eng = base.eng
    h0 = eng.hashes()
    r = base.run("element.delete", {"id": F.W1})
    assert r["ok"] and set(r["result"]["deleted"]) == {F.W1, F.D1}
    assert F.D1 not in eng.st.el and F.D1 not in eng.ifc.ents
    assert base.run("history.undo")["ok"]
    assert eng.ifc.get_psets(F.D1) == {"Pset_DoorCommon": {"FireRating": "EI30"}}
    assert eng.ifc.get_material(F.W1) == "Concrete"
    assert _core(eng.hashes()) == _core(h0)


def test_ifc_투영은_유효하고_의도가_일관된다_M8(base):
    eng = base.eng
    eng.flush_ifc()
    assert eng.ifc.validate(express=True)["errors"] == 0
    assert eng.intent_consistency() == []
    # S0 발견 8(낮은 수준 편집은 의도를 낡게 함)에 대한 답: 투영이 매번 의도를 다시 쓴다
    base.run("wall.move", {"id": F.W1, "dx": 600})
    base.run("wall.set", {"id": F.W2, "thickness": 300})
    assert eng.intent_consistency() == []
    assert eng.ifc.validate(express=True)["errors"] == 0
    assert json.loads(eng.ifc.intent_texts()[F.W1])["params"]["axis"][0] == 600.0


def test_ifc_내보내기와_s0_왕복_검사기_M1_M8_X1(base, tmp_path):
    spec = importlib.util.spec_from_file_location("rt", kit.HERE.parent.parent / "s0-baseline" / "roundtrip_check.py")
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    base.eng.workspace = tmp_path

    def export(name):
        first = base.run("ifc.export", {"path": name})
        assert first["error"]["code"] == "CONFIRM_REQUIRED"
        assert base.run("ifc.export", {"path": name}, confirm=first["error"]["details"]["confirm_token"])["ok"]

    export("a.ifc")
    base.run("wall.move", {"id": F.W3, "dx": 800})
    export("b.ifc")
    same = rt.run_compare(str(tmp_path / "a.ifc"), str(tmp_path / "a.ifc"))
    assert same["M8"]["pass"] and same["M1"]["pass"] and same["M4"]["pass"], rt.format_table(same)
    moved = rt.run_compare(str(tmp_path / "a.ifc"), str(tmp_path / "b.ifc"))
    assert moved["M8"]["pass"] and moved["M1"]["pass"] and moved["M2"]["pass"]
    assert not moved["X1"]["pass"]                      # 위치 지표는 이동을 잡아낸다 (S0 발견 6)


def test_확인_토큰은_인자와_리비전에_묶인다(base, tmp_path):
    base.eng.workspace = tmp_path
    tok = base.run("ifc.export", {"path": "x.ifc"})["error"]["details"]["confirm_token"]
    assert base.run("ifc.export", {"path": "y.ifc"}, confirm=tok)["error"]["code"] == "CONFIRM_INVALID"     # 다른 인자
    base.run("wall.move", {"id": F.W1, "dx": 1})
    assert base.run("ifc.export", {"path": "x.ifc"}, confirm=tok)["error"]["code"] == "CONFIRM_INVALID"     # 다른 리비전
    assert base.run("ifc.export", {"path": "../x.ifc"})["error"]["code"] == "PATH_DENIED"


def test_파일을_다시_열어도_같은_상태(base):
    h = base.eng.hashes()
    e2 = Engine(base.path, "again")
    assert e2.hashes() == h and e2.undo_stack == base.eng.undo_stack
    e2.close()


def test_효과_뒤집기와_변경_요약():
    eff = [["el", "a", None, {"kind": "wall"}], ["rel", "hosts", "a", "b", 0, 1], ["pset", "a", "P", "p", None, 1]]
    assert invert(invert(eff)) == eff
    assert affected_of(eff)["created"] == ["a"]
