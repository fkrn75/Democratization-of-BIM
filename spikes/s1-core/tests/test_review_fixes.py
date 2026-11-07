# SPDX-License-Identifier: MPL-2.0
"""독립 검수 지적 사항(D1 값 타입, 골든 값, redo 분기, D5 내보내기 대상, 스냅숏, 효과 뒤 예외)의 회귀 시험."""
import dataclasses

import pytest

import golden
import kit
from dobim_core import fixture as F, registry
from dobim_core.errors import DobimError


def _core(h):
    return {k: v for k, v in h.items() if k != "rev"}


def confirmed(sess, name, args):
    first = sess.run(name, args)
    assert first["error"]["code"] == "CONFIRM_REQUIRED", first
    return sess.run(name, args, confirm=first["error"]["details"]["confirm_token"])


def test_골든_값(tmp_path):
    assert golden.check_golden(tmp_path) == []


@pytest.mark.parametrize("seq", [[1, 2.5], [10, 0.5], [True, 5], [5, "abc"], ["abc", 5], [2.5, 1], [1, True], [1, 1.0]])
def test_속성_값은_타입까지_입력_그대로_저장되고_undo_재생_다시열기가_일치한다(base, tmp_path, seq):
    eng = base.eng
    for v in seq:
        assert base.run("pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": v})["ok"]
        got = eng.ifc.get_psets(F.W1)["Pset_Z"]["P"]
        assert got == v and type(got) is type(v)
    d = kit.full_check(base, tmp_path)
    assert d["ok"], d["diffs"]
    base.run("history.undo")
    got = eng.ifc.get_psets(F.W1)["Pset_Z"]["P"]
    assert got == seq[-2] and type(got) is type(seq[-2])


def test_redo_가지는_새_변경으로_버려지고_해시로_확인된다(base):
    eng = base.eng
    base.run("wall.move", {"id": F.W1, "dx": 100})
    h_a = eng.hashes()
    base.run("wall.move", {"id": F.W2, "dx": 100})
    base.run("history.undo")
    assert _core(eng.hashes()) == _core(h_a)
    base.run("wall.move", {"id": F.W3, "dy": 50})                    # 새 가지
    h_b = eng.hashes()
    r = base.run("history.redo")
    assert r["error"]["code"] == "NOTHING_TO_REDO" and _core(eng.hashes()) == _core(h_b)
    base.run("history.undo")
    assert _core(eng.hashes()) == _core(h_a)                         # 새 가지를 되돌리면 분기점
    assert base.run("history.redo")["ok"] and _core(eng.hashes()) == _core(h_b)


@pytest.mark.parametrize("seed", range(1, 5))
def test_undo_redo_분기_이력(seed, tmp_path):
    r = kit.undo_property(seed, 100, tmp_path)
    assert r["violations"] == [] and r["branch_checks"] > 50


@pytest.mark.parametrize("bad", ["x.txt", "x", "x.IFC.bak", "p.dobim", "t.dobim", "t.dobim-wal", "t.dobim-shm", ".dobim-snapshots/a.ifc",
                                 "sub", "x.ifc.tmp"])
def test_내보내기는_ifc_이외_대상을_거부한다(base, bad):
    (base.path.parent / "sub").mkdir(exist_ok=True)
    before = (base.path.parent / "t.dobim").stat().st_size
    r = base.run("ifc.export", {"path": bad})
    assert r["error"]["code"] == "PATH_DENIED", (bad, r)
    r = base.run("ifc.export", {"path": bad}, confirm="9999999999.deadbeefdeadbeefdeadbeef")
    assert not r["ok"]
    assert (base.path.parent / "t.dobim").stat().st_size == before


def test_덮어쓰기_전에_스냅숏을_남기고_되돌릴_수_있다(base):
    out = base.path.parent / "m.ifc"
    out.write_bytes("ISO-10303-21; 사용자가 만든 원본\n".encode())
    original = out.read_bytes()
    r = confirmed(base, "ifc.export", {"path": "m.ifc"})
    assert r["ok"] and r["result"]["snapshot"] == ".dobim-snapshots/m.ifc.1"
    assert out.read_bytes() != original and (base.path.parent / ".dobim-snapshots" / "m.ifc.1").read_bytes() == original
    first_export = out.read_bytes()
    base.run("wall.move", {"id": F.W1, "dx": 5})
    assert confirmed(base, "ifc.export", {"path": "m.ifc"})["result"]["snapshot"].endswith("m.ifc.2")   # 이전 스냅숏을 덮지 않는다
    snaps = base.run("ifc.snapshots", {"path": "m.ifc"})["result"]["items"]
    assert [x["n"] for x in snaps] == [1, 2]
    assert confirmed(base, "ifc.restore", {"path": "m.ifc", "snapshot": 1})["ok"]
    assert out.read_bytes() == original                                   # 첫 덮어쓰기를 되돌렸다
    assert confirmed(base, "ifc.restore", {"path": "m.ifc", "snapshot": 2})["ok"]
    assert out.read_bytes() == first_export                               # 복원 자체도 스냅숏이 남아 되돌릴 수 있다
    assert base.run("ifc.restore", {"path": "m.ifc", "snapshot": 99})["error"]["code"] in ("NOT_FOUND", "CONFIRM_REQUIRED")


def test_새_파일_내보내기는_스냅숏이_없다(base):
    r = confirmed(base, "ifc.export", {"path": "new.ifc"})
    assert r["ok"] and r["result"]["snapshot"] is None


def test_효과를_낸_뒤_예외가_나는_단일_커맨드는_상태를_바꾸지_않는다(base, monkeypatch):
    eng = base.eng
    before = eng.hashes()
    cmd = registry.load()["wall.move"]

    def boom(ctx, a):
        ctx.put_el(a.id, dict(ctx.row(a.id), params=dict(ctx.row(a.id)["params"], thickness=777.0)))
        ctx.set_prop(a.id, "Pset_Boom", "p", 1)
        ctx.rel("joins", F.W2, F.W3, True)
        raise RuntimeError("효과 뒤 예외")

    monkeypatch.setitem(registry.REGISTRY, "wall.move", dataclasses.replace(cmd, func=boom))
    r = base.run("wall.move", {"id": F.W1, "dx": 1})
    assert not r["ok"] and r["error"]["code"] == "INTERNAL"
    assert eng.hashes() == before and not eng.conn.in_transaction
    assert base.run("model.stats")["ok"]
    d = kit.full_check(base, base.path.parent)
    assert d["ok"], d["diffs"]


def test_IFC_쪽_적용이_중간에_실패해도_메모리_상태가_되돌려진다(base, monkeypatch):
    eng = base.eng
    before = eng.hashes()

    def fail(self, eid, kind):
        raise RuntimeError("IFC 실패")

    monkeypatch.setattr(type(eng.ifc), "create_identity", fail)
    r = base.run("level.create", {"name": "ZZ", "elevation": 1})
    assert not r["ok"]
    monkeypatch.undo()
    assert eng.hashes() == before


def test_속성셋_적용이_실패해도_빈_속성셋을_남기지_않는다(base, monkeypatch):
    eng = base.eng
    before = eng.state_hash()
    import dobim_core.ifc_model as im
    real = im.api_pset.edit_pset

    def fail(*a, **k):
        raise RuntimeError("edit_pset 실패")

    monkeypatch.setattr(im.api_pset, "edit_pset", fail)
    r = base.run("pset.set", {"id": F.W1, "pset": "Pset_New", "prop": "x", "value": 1})
    assert not r["ok"]
    monkeypatch.setattr(im.api_pset, "edit_pset", real)
    assert eng.state_hash() == before
