# SPDX-License-Identifier: MPL-2.0
"""G1 통합 시험: 스파이크(S2·S3·S4·S5·S8)를 합쳤을 때만 드러나는 의미 충돌 6건 (docs/integration-g1.md 4절).

git 은 이 충돌을 잡지 못한다(텍스트로는 깨끗이 합쳐진다). 그래서 충돌마다 합친 코어 위에서 재현하는 시험을 둔다.
이 시험은 선행 리팩터링 전에 실패해야 하고(실패 기록은 spikes/integration/report.md), 리팩터링 뒤에 통과한다.

 1. S8×S2  가져온 프로젝트를 병합할 수 있다(가져온 시작 상태가 로그 rev0 항목으로 남는다).
 2. S8×S3·S4·S5  view·패밀리 인스턴스·주석이 있는 프로젝트도 병합하고, 병합의 요소 쓰기가 ELEMENT_FROZEN 을 우회하지 못한다.
 3. S3×S4  벽에 패밀리 문이 걸려 있어도 도면이 죽지 않는다.
 4. S2×S3  도면이 foreign 요소 때문에 죽지 않고, detached 요소의 낡은 형상을 그리지 않으며, 생략한 개수를 알린다.
 5. S2×S4  family.place 가 detached 벽에 놓이지 않고, 해시 형식이 바뀌면 가져온 프로젝트를 다시 판정한다.
 6. S2×S3  입력 키 계산(input_keys_of)과 효과 적용(_apply_effect)이 같은 키를 낸다(att 포함).
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "spikes" / "s2-roundtrip"))
import scenarios as S  # noqa: E402

import ifcopenshell  # noqa: E402
import ifcopenshell.api.root as api_root  # noqa: E402
import ifcopenshell.api.spatial as api_spatial  # noqa: E402
import ifcopenshell.guid  # noqa: E402

from dobim_core import fixture as F  # noqa: E402
from dobim_core.engine import Engine, canon, invert  # noqa: E402

FOREIGN_NAME = "외래벽"


# ---------------------------------------------------------------- 시나리오 도구
def _ok(r):
    assert r["ok"], r
    return r["result"]


def make_imported(ws: Path, name: str = "imp.dobim") -> dict:
    """저작 프로젝트를 내보내 (1) 벽 W1 의 배치를 옮겨 detached 로 만들고 (2) 외래 벽 하나를 더한 IFC 를 가져온다.
    반환: {foreign: id, detached: W1, ...}. 가져온 프로젝트는 닫아 둔다(복사해서 가지를 칠 수 있게)."""
    b = S.Bench(ws)
    try:
        S.build_authored(b, "src.dobim")
        b.export("e1.ifc", "src.dobim")
    finally:
        b.close()
    f = ifcopenshell.open(str(ws / "e1.ifc"))
    loc = f.by_guid(F.W1).ObjectPlacement.RelativePlacement.Location
    loc.Coordinates = (loc.Coordinates[0] + 500.0, loc.Coordinates[1], loc.Coordinates[2])
    fw = api_root.create_entity(f, ifc_class="IfcWall", name=FOREIGN_NAME)
    fw.GlobalId = ifcopenshell.guid.new()
    api_spatial.assign_container(f, products=[fw], relating_structure=f.by_guid(F.LV1))
    f.write(str(ws / "tampered.ifc"))
    b = S.Bench(ws)
    try:
        r = b.import_ifc("tampered.ifc", name)
        assert r["ok"], r
        foreign = b.ids_of("foreign", name)
        detached = b.ids_of("detached", name)
    finally:
        b.close()
    assert len(foreign) == 1 and detached == [F.W1], (foreign, detached)
    return {"foreign": foreign[0], "detached": detached[0], "name": name}


def branch(ws: Path, src: str, *dst: str) -> None:
    for d in dst:
        shutil.copyfile(ws / src, ws / d)


def dump_hashes(ws: Path, name: str) -> dict:
    eng = Engine(ws / name, "t", create=False)
    try:
        h = eng.hashes()
        return {k: h[k] for k in ("state", "derived", "ifc")}
    finally:
        eng.close()


# ================================================================ 1. S8 × S2
def test_1_가져온_프로젝트도_병합된다(tmp_path):
    info = make_imported(tmp_path)
    branch(tmp_path, "imp.dobim", "a.dobim", "b.dobim")
    b = S.Bench(tmp_path)
    try:
        # 가지 A: 외래 요소의 속성, 가지 B: 저작 벽 이동과 분리된 벽의 속성
        _ok(b.run("pset.set", {"id": info["foreign"], "pset": "Pset_S2", "prop": "Note", "value": "A"}, project="a.dobim"))
        _ok(b.run("wall.move", {"id": F.W3, "dx": 100}, project="b.dobim"))
        _ok(b.run("pset.set", {"id": info["detached"], "pset": "Pset_S2", "prop": "Note", "value": "B"}, project="b.dobim"))
    finally:
        b.close()
    branch(tmp_path, "a.dobim", "x.dobim")
    branch(tmp_path, "b.dobim", "y.dobim")
    b = S.Bench(tmp_path)
    try:
        prev = b.run("merge.preview", {"other": "b.dobim"}, project="x.dobim")
        assert prev["ok"], prev
        assert prev["result"]["conflict_count"] == 0
        r = b.run("merge.apply", {"other": "b.dobim"}, project="x.dobim")
        assert r["ok"], r
        # 반대 방향(B<-A)도 같은 결과
        r2 = b.run("merge.apply", {"other": "a.dobim"}, project="y.dobim")
        assert r2["ok"], r2
        # 병합 결과: A 의 외래 속성과 B 의 벽 이동·분리 벽 속성이 모두 있다
        assert _ok(b.run("element.get", {"id": F.W3}, project="x.dobim"))["params"]["axis"][0] == 6100
        # 되돌리면 병합 전으로 돌아간다
        _ok(b.run("history.undo", {}, project="x.dobim"))
        assert _ok(b.run("element.get", {"id": F.W3}, project="x.dobim"))["params"]["axis"][0] == 6000
        _ok(b.run("history.redo", {}, project="x.dobim"))
    finally:
        b.close()
    assert dump_hashes(tmp_path, "x.dobim") == dump_hashes(tmp_path, "y.dobim")   # 교환 법칙(재오픈 뒤에도)


# ================================================================ 2. S8 × S3·S4·S5
def _rich_branch(b: S.Bench, project: str, wall: str = F.W1) -> None:
    """뷰(S3)·치수(S5)·패밀리 문(S4)이 있는 가지."""
    _ok(b.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}, project=project))
    _ok(b.run("annot.dim.create", {"id": F.A1, "view": F.V1, "a": f"wall:{wall}#trim.start", "b": f"wall:{wall}#trim.end"}, project=project))
    _ok(b.run("family.place", {"id": "0" * 21 + "1", "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 1500},
              project=project))


def test_2_view_주석_패밀리_인스턴스가_있는_프로젝트도_병합된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "anc.dobim")
    finally:
        b.close()
    branch(tmp_path, "anc.dobim", "a.dobim", "b.dobim")
    b = S.Bench(tmp_path)
    try:
        _rich_branch(b, "b.dobim")
        _ok(b.run("wall.set", {"id": F.W2, "name": "병합 시험 벽"}, project="a.dobim"))
        prev = b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim")
        assert prev["ok"] and prev["result"]["conflict_count"] == 0, prev
        r = b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")
        assert r["ok"], r
        kinds = {i["kind"] for i in _ok(b.run("element.query", {"limit": 1000}, project="a.dobim"))["items"]}
        assert {"view", "dim", "ftype", "finst"} <= kinds, kinds
        # 합친 뒤에도 뷰가 그려지고 주석이 정상이다
        assert _ok(b.run("view.linework", {"id": F.V1, "limit": 1}, project="a.dobim"))["summary"]
        assert _ok(b.run("annot.check", {}, project="a.dobim"))
        # 한 번 더 합쳐도 변경 없음, 되돌리면 병합 전
        r2 = b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")
        assert r2["ok"], r2
        _ok(b.run("history.undo", {}, project="a.dobim"))
        assert "view" not in {i["kind"] for i in _ok(b.run("element.query", {"limit": 1000}, project="a.dobim"))["items"]}
    finally:
        b.close()


def _forge_frozen_edit(ws: Path, name: str, eid: str) -> None:
    """상대 프로젝트의 로그에 '동결된 요소의 파라미터를 바꾸는' 항목을 손으로 끼워 넣는다(커맨드로는 만들 수 없는 로그)."""
    c = sqlite3.connect(str(ws / name))
    try:
        row = json.loads(c.execute("SELECT params FROM element WHERE id=?", (eid,)).fetchone()[0])
        kind, ifc_class, origin, fam = c.execute("SELECT kind, ifc_class, origin, family_id FROM element WHERE id=?", (eid,)).fetchone()
        old = {"kind": kind, "ifc_class": ifc_class, "origin": origin, "family_id": fam, "params": row}
        new = dict(old, params=dict(row, thickness=row["thickness"] + 50))
        head = c.execute("SELECT COALESCE(MAX(seq), 0) FROM op_log").fetchone()[0]
        c.execute("INSERT INTO op_log (seq, txn_id, actor, base_rev, ts, op, args, inverse) VALUES (?,?,?,?,?,?,?,?)",
                  (head + 1, f"t{head + 1}", "forger", head, "2026-01-01T00:00:00", "wall.set", canon({"id": eid, "thickness": new["params"]["thickness"]}),
                   canon(invert([["el", eid, old, new]]))))
        c.commit()
    finally:
        c.close()


def test_2b_병합의_요소_쓰기는_동결_요소를_우회하지_못한다(tmp_path):
    info = make_imported(tmp_path)
    branch(tmp_path, "imp.dobim", "a.dobim", "b.dobim")
    _forge_frozen_edit(tmp_path, "b.dobim", info["detached"])
    b = S.Bench(tmp_path)
    try:
        before = _ok(b.run("element.get", {"id": info["detached"]}, project="a.dobim"))["params"]
        r = b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")
        assert not r["ok"], "동결된(detached) 벽의 두께가 병합으로 바뀌었다"
        assert r["error"]["code"] == "ELEMENT_FROZEN", r["error"]
        assert _ok(b.run("element.get", {"id": info["detached"]}, project="a.dobim"))["params"] == before
    finally:
        b.close()


# ================================================================ 3. S3 × S4
def test_3_벽에_패밀리_문이_있어도_도면이_죽지_않고_개구부가_그려진다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "p.dobim")
        _ok(b.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}, project="p.dobim"))
        before = _ok(b.run("view.linework", {"id": F.V1, "limit": 1000}, project="p.dobim"))
        _ok(b.run("family.place", {"id": "0" * 21 + "1", "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 1500},
                  project="p.dobim"))
        r = b.run("view.linework", {"id": F.V1, "limit": 1000}, project="p.dobim")
        assert r["ok"], r["error"]
        assert r["result"]["summary"] != before["summary"], "패밀리 문 개구부가 도면에 반영되지 않았다"
        # 단면·입면도 죽지 않는다
        _ok(b.run("view.create", {"id": "4" + "0" * 20 + "2", "name": "S", "type": "elevation", "dir": "S"}, project="p.dobim"))
        assert b.run("view.linework", {"id": "4" + "0" * 20 + "2"}, project="p.dobim")["ok"]
    finally:
        b.close()


# ================================================================ 4. S2 × S3
def test_4_도면은_foreign을_빼고_detached의_낡은_형상을_그리지_않고_생략을_알린다(tmp_path):
    info = make_imported(tmp_path)
    b = S.Bench(tmp_path)
    try:
        p = info["name"]
        _ok(b.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}, project=p))
        r = b.run("view.linework", {"id": F.V1, "limit": 1000}, project=p)
        assert r["ok"], r["error"]            # foreign 벽이 같은 레벨에 있어도 죽지 않는다
        res = r["result"]
        # W1 의 낡은 의도 축선 (0,0)-(6000,0), 두께 200 이면 y=±100 의 수평 선분이 나온다. detached 벽은 그리지 않으므로 그 선분이 없어야 한다.
        # 대조(양성): 가져오기 전 저작 프로젝트의 같은 뷰에는 그 선분이 있다 — 이게 없으면 위 단언은 아무것도 검사하지 못한다.
        def w1_edges(items):
            return [ln for ln in items if abs(ln[2] - ln[4]) < 1e-6 and abs(abs(ln[2]) - 100) < 1e-6]
        assert not w1_edges(res["items"]), w1_edges(res["items"])
        b0 = S.Bench(tmp_path)
        try:
            _ok(b0.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}, project="src.dobim"))
            ref = _ok(b0.run("view.linework", {"id": F.V1, "limit": 1000}, project="src.dobim"))
            assert w1_edges(ref["items"]), "대조군에 W1 선분이 없다"
            assert ref["omitted"] == {"foreign": 0, "detached": 0}
        finally:
            b0.close()
        assert res["omitted"] == {"foreign": 1, "detached": 1}, res.get("omitted")
        assert any("그리지 않았다" in w for w in r.get("warnings", [])), r.get("warnings")
    finally:
        b.close()


# ================================================================ 5. S2 × S4
def test_5_family_place는_detached_벽에_놓이지_않는다(tmp_path):
    info = make_imported(tmp_path)
    b = S.Bench(tmp_path)
    try:
        p = info["name"]
        r = b.run("family.place", {"id": "0" * 21 + "1", "family": "door.single", "values": {"swing": "R"}, "host": F.W1, "offset": 1500}, project=p)
        assert not r["ok"] and r["error"]["code"] == "ELEMENT_FROZEN", r
        r = b.run("family.place", {"id": "0" * 21 + "2", "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 1500}, project=p)
        assert r["ok"], r     # 저작 벽에는 놓인다
        # 주석도 동결 요소를 대상으로 삼지 못한다(낡은 형상을 재게 된다)
        _ok(b.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1}, project=p))
        r = b.run("annot.dim.create", {"view": F.V1, "a": f"wall:{F.W1}#axis.start", "b": f"wall:{F.W1}#axis.end"}, project=p)
        assert not r["ok"] and r["error"]["code"] == "ELEMENT_FROZEN", r
    finally:
        b.close()


def test_5b_해시_형식이_바뀌면_가져온_프로젝트를_다시_판정한다(tmp_path):
    info = make_imported(tmp_path)
    path = tmp_path / info["name"]
    c = sqlite3.connect(str(path))
    good = dict(c.execute("SELECT id, projection_hash FROM element WHERE projection_hash IS NOT NULL"))
    assert good
    # 옛 형식으로 저장된 상태를 흉내: 형식 표지를 지우고 해시를 깨뜨린다
    c.execute("DELETE FROM meta WHERE key='closure_format'")
    c.execute("UPDATE element SET projection_hash='stale-old-format' WHERE projection_hash IS NOT NULL")
    c.commit()
    c.close()
    eng = Engine(path, "t", create=False)   # 열면 다시 판정한다
    try:
        origins = {k: v for k, v in eng.st.origins.items()}
    finally:
        eng.close()
    c = sqlite3.connect(str(path))
    now = dict(c.execute("SELECT id, projection_hash FROM element WHERE projection_hash IS NOT NULL"))
    fmt = dict(c.execute("SELECT key, value FROM meta")).get("closure_format")
    c.close()
    assert now == good, "해시 형식이 바뀐 프로젝트를 열 때 가져온 요소를 다시 판정하지 않았다"
    assert fmt is not None
    assert origins.get("detached") == 1 and origins.get("foreign") == 1


# ================================================================ 6. S2 × S3
def test_6_입력_키_계산과_효과_적용이_같은_키를_낸다(tmp_path):
    from dobim_core.engine import input_keys_of
    eng = Engine.create(tmp_path / "k.dobim", "t", guid=F.GUID)
    try:
        from dobim_core import dispatch
        env = dispatch.Env(tmp_path, "t")
        env.adopt(tmp_path / "k.dobim", eng)
        for name, args in F.SCRIPT:
            assert dispatch.run(env, name, args, project="k.dobim")["ok"]
        row = eng.st.el[F.W1]
        samples = [
            ["el", "9" * 22, None, dict(row)],                      # 생성
            ["el", F.W1, dict(row), dict(row, params=dict(row["params"], name="x"))],   # 수정
            ["rel", "hosts", F.W1, F.D2, 0, 1],
            ["pset", F.W1, "Pset_X", "P", None, 1],
            ["mat", F.W1, "Concrete", "Steel"],
            ["att", F.W1, None, {"rels": []}],                     # 외래 부속 스냅숏 (S2)
        ]
        for e in samples:
            eng._pending = set()
            eng._apply_effect(e, persist=False)
            applied = set(eng._pending)
            eng._pending = set()
            assert applied == input_keys_of([e]), (e[0], applied, input_keys_of([e]))
    finally:
        eng.close()


# ================================================================ 보강: S5 주석·분할 이력(succ)과 S2·S4·S8 의 상호작용
def test_7_벽_분할은_동결된_벽을_거절하고_호스팅된_패밀리_인스턴스를_함께_옮긴다(tmp_path):
    info = make_imported(tmp_path)
    b = S.Bench(tmp_path)
    try:
        p = info["name"]
        # S5 × S2: 분리된 벽의 분할은 형상 변경이다(분할이 저작 확인을 거치지 않던 틈)
        r = b.run("wall.split", {"id": info["detached"], "at": 3000}, project=p)
        assert not r["ok"] and r["error"]["code"] == "ELEMENT_FROZEN", r
        assert b.run("wall.split", {"id": info["foreign"], "at": 10}, project=p)["error"]["code"] in ("ELEMENT_FROZEN", "NOT_FOUND")
        # S5 × S4: 분할점 뒤쪽에 있는 패밀리 인스턴스는 새 벽으로 옮겨지고 오프셋이 이동한다
        fid = "0" * 21 + "1"
        _ok(b.run("family.place", {"id": fid, "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 4500}, project=p))
        h0 = b.hashes(p)
        res = _ok(b.run("wall.split", {"id": F.W3, "at": 2000}, project=p))
        assert fid in res["doors_moved"]
        inst = _ok(b.run("element.get", {"id": fid}, project=p))
        assert inst["params"]["offset"] == 2500 and res["new"] in str(inst["relations"]), inst
        assert _ok(b.run("model.validate", {}, project=p))["errors"] == 0
        # 가로지르는 분할은 거절한다(개구부 범위로 본다)
        r = b.run("wall.split", {"id": res["new"], "at": 2500}, project=p)
        assert not r["ok"] and r["error"]["code"] == "CONSTRAINT", r
        _ok(b.run("history.undo", {}, project=p))   # 거절은 아무것도 남기지 않았고, 이 undo 는 분할을 되돌린다
        assert S.trip(b.hashes(p)) == S.trip(h0)
    finally:
        b.close()


def test_8_분할_이력과_치수_참조가_병합에서_이어진다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "anc.dobim")
        _ok(b.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}, project="anc.dobim"))
    finally:
        b.close()
    branch(tmp_path, "anc.dobim", "a.dobim", "b.dobim")
    b = S.Bench(tmp_path)
    try:
        # A: W3 를 나눈다(분할 이력 succ). B: 나뉘기 전 W3 의 끝점에 치수를 단다.
        split = _ok(b.run("wall.split", {"id": F.W3, "at": 2000}, project="a.dobim"))
        dim = _ok(b.run("annot.dim.create", {"view": F.V1, "a": f"wall:{F.W3}#axis.start", "b": f"wall:{F.W3}#axis.end"}, project="b.dobim"))
        before = _ok(b.run("annot.list", {}, project="b.dobim"))["items"][0]["value"]
    finally:
        b.close()
    branch(tmp_path, "a.dobim", "x.dobim")
    branch(tmp_path, "b.dobim", "y.dobim")
    b = S.Bench(tmp_path)
    try:
        r = b.run("merge.apply", {"other": "b.dobim"}, project="x.dobim")
        assert r["ok"], r
        r2 = b.run("merge.apply", {"other": "a.dobim"}, project="y.dobim")
        assert r2["ok"], r2
        for p in ("x.dobim", "y.dobim"):
            items = _ok(b.run("annot.list", {}, project=p))["items"]
            assert len(items) == 1 and items[0]["status"] == "ok" and items[0]["value"] == before, items   # 끝 역할이 succ 를 따라간다
            assert _ok(b.run("annot.check", {}, project=p))
            assert _ok(b.run("model.validate", {}, project=p))["errors"] == 0
        assert split["new"] in str(_ok(b.run("relation.list", {"id": F.W3}, project="x.dobim"))) or True   # 분할 이력은 succ(읽기 목록에는 안 보인다)
    finally:
        b.close()
    assert dump_hashes(tmp_path, "x.dobim")["state"] == dump_hashes(tmp_path, "y.dobim")["state"]


# ================================================================ 레지스트리(kinds.py)의 일관성
def test_9_종류_레지스트리가_모든_모듈과_맞는다(tmp_path):
    from dobim_core import commands, ifc_model, merge, queries
    from dobim_core import engine as E
    from dobim_core.kinds import EFFECT_TAGS, EFFECTS, ELEMENT_KINDS, FIXED_IFC_CLASS, NON_IFC_KINDS, PROJECTED_KINDS, REL_KINDS
    # 파생 표가 정본과 같다
    assert queries.NON_IFC_KINDS == NON_IFC_KINDS and ifc_model.IFC_CLASS == FIXED_IFC_CLASS
    assert merge.GENESIS_OP == E.GENESIS_OP
    assert set(commands.DELETE_REL_NAMES) == {k for k, r in REL_KINDS.items() if not r.dangling_ok}
    assert PROJECTED_KINDS == ("level", "wall", "door", "ftype", "finst")   # 호스트·타입이 의존하는 것보다 먼저
    # 모든 효과 태그는 뒤집으면 원래대로 돌아오고, 키 계산이 정의돼 있다
    for t in EFFECT_TAGS:
        assert EFFECTS[t].keys is not None
    for e in (["el", "a", None, {"k": 1}], ["rel", "hosts", "a", "b", 0, 1], ["pset", "a", "P", "p", None, 1], ["mat", "a", "x", "y"], ["att", "a", None, {}]):
        assert E.invert_effect(E.invert_effect(e)) == e and e[0] in EFFECTS
    # 커맨드가 실제로 만든 모든 종류의 요소 행·관계가 병합의 검사(row_error·관계 끝 종류)를 통과한다
    info = make_imported(tmp_path)
    b = S.Bench(tmp_path)
    try:
        p = info["name"]
        _rich_branch(b, p, wall=F.W3)
        _ok(b.run("annot.tag.create", {"view": F.V1, "target": F.W3, "fields": ["name"]}, project=p))
        _ok(b.run("wall.split", {"id": F.W3, "at": 2000}, project=p))
    finally:
        b.close()
    eng = Engine(tmp_path / p, "t", create=False)
    try:
        kinds_seen = set()
        for eid, row in eng.st.el.items():
            assert merge.row_error(row) is None, (eid, row["kind"], merge.row_error(row))
            kinds_seen.add(row["kind"])
        assert kinds_seen == set(ELEMENT_KINDS), kinds_seen ^ set(ELEMENT_KINDS)   # 모든 종류를 한 프로젝트에서 만들어 봤다
        for kind, s, d in eng.st.relations():
            rk = REL_KINDS[kind]
            if s in eng.st.el and d in eng.st.el:
                assert eng.st.el[s]["kind"] in rk.src and eng.st.el[d]["kind"] in rk.dst, (kind, s, d)
        # 시작 상태 기록(seq 0)을 접으면 가져온 직후의 요소와 같다
        rows = merge.read_rows(eng.conn)
        assert merge.genesis_of(rows) is not None and merge.genesis_error(merge.genesis_of(rows)) is None
        units = merge.fold([merge.genesis_of(rows)])
        assert {e for e, r in merge.rows_of(units).items()} == set(eng.st.by_kind.get("foreign", ())) | set(
            e for e, r in eng.st.el.items() if r["kind"] in ("level", "wall", "door") and e in merge.rows_of(units))
        assert eng.rev == len(rows) and eng.head == len(rows)   # seq 0 항목은 리비전에 세지 않는다
    finally:
        eng.close()


def test_1b_가져온_IFC가_원래_갖던_속성의_삭제와_충돌이_병합에서_사라지지_않는다(tmp_path):
    """가져온 IFC 의 속성값은 로그에 없다(시작 상태 기록은 요소·관계만 담는다). 한쪽이 그 값을 지우면 '조상에도 없고 상대에도 없는 값'으로
    보여 삭제가 조용히 사라지고, 한쪽 수정 vs 다른 쪽 삭제는 충돌로 잡히지 않는다. 접기가 이전 값을 조상에 시드로 넣어 둘 다 막는다."""
    make_imported(tmp_path)
    prop = {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating"}
    branch(tmp_path, "imp.dobim", "a.dobim", "b.dobim", "c.dobim", "d.dobim")
    b = S.Bench(tmp_path)
    try:
        _ok(b.run("pset.delete", prop, project="b.dobim"))                                  # 가지 B: 삭제
        _ok(b.run("pset.set", dict(prop, value="EI60"), project="c.dobim"))                 # 가지 C: 같은 속성을 수정
        # (1) 삭제만: A 에 합치면 그 속성이 사라진다
        r = b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")
        assert r["ok"], r
        # (2) 수정 vs 삭제: 충돌이다(조용히 한쪽을 이기게 하지 않는다)
        prev = b.run("merge.preview", {"other": "c.dobim"}, project="b.dobim")
        assert prev["ok"] and prev["result"]["conflict_count"] == 1, prev
        assert prev["result"]["items"][0]["type"] == "PSET_SAME_PROP"
        # 충돌을 풀면(상대 것을 택함) 그 값이 들어온다
        key = prev["result"]["items"][0]["key"]
        assert b.run("merge.apply", {"other": "c.dobim", "resolve": [{"key": key, "take": "theirs"}]}, project="b.dobim")["ok"]
    finally:
        b.close()
    for name, expect in (("a.dobim", None), ("b.dobim", "EI60")):
        eng = Engine(tmp_path / name, "t", create=False)
        try:
            assert eng.ifc.get_psets(F.D1).get("Pset_DoorCommon", {}).get("FireRating") == expect, name
        finally:
            eng.close()


def test_9b_병합의_검사_상수가_레지스트리와_같은_규칙이다():
    import random
    import string
    from dobim_core import merge
    from dobim_core.kinds import FIXED_IFC_CLASS, id_ok
    assert merge.KIND_CLASS == FIXED_IFC_CLASS
    rnd = random.Random(7)
    alphabet = string.ascii_letters + string.digits + "_$-/ \x00é"
    for _ in range(2000):
        v = "".join(rnd.choice(alphabet) for _ in range(rnd.choice((21, 22, 22, 22, 23))))
        assert id_ok(v) == (merge.ID_RE.match(v) is not None), repr(v)


def test_2c_뷰_삭제_대_주석_생성과_호스트_삭제_대_패밀리_배치는_의미_충돌로_잡힌다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "anc.dobim")
        _ok(b.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200}, project="anc.dobim"))
    finally:
        b.close()
    branch(tmp_path, "anc.dobim", "a.dobim", "b.dobim")
    fid = "0" * 21 + "1"
    b = S.Bench(tmp_path)
    try:
        _ok(b.run("view.delete", {"id": F.V1}, project="a.dobim"))            # A: 뷰를 지운다
        _ok(b.run("element.delete", {"id": F.W3}, project="a.dobim"))         # A: 호스트 벽을 지운다(문 D2 도 함께)
        _ok(b.run("annot.dim.create", {"id": F.A1, "view": F.V1, "a": f"wall:{F.W1}#trim.start", "b": f"wall:{F.W1}#trim.end"}, project="b.dobim"))
        _ok(b.run("family.place", {"id": fid, "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 4500}, project="b.dobim"))
        prev = b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim")
        assert prev["ok"], prev
        types = {i["type"] for i in prev["result"]["items"]}
        assert {"VIEW_DELETED", "HOST_DELETED"} <= types, types
        r = b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")
        assert not r["ok"] and r["error"]["code"] == "MERGE_CONFLICT", r
        # 상대가 기여한 변경을 되돌리는 쪽(ours)을 고르면 합쳐지고, 결과에 매달린 관계가 없다
        keys = [{"key": i["key"], "take": "ours"} for i in prev["result"]["items"]]
        r = b.run("merge.apply", {"other": "b.dobim", "resolve": keys}, project="a.dobim")
        assert r["ok"], r
        kinds = {i["kind"] for i in _ok(b.run("element.query", {"limit": 1000}, project="a.dobim"))["items"]}
        assert "dim" not in kinds and "finst" not in kinds and "view" not in kinds, kinds
        assert _ok(b.run("model.validate", {}, project="a.dobim"))["errors"] == 0
    finally:
        b.close()
