# SPDX-License-Identifier: MPL-2.0
"""독립 검수(통합 브랜치)에서 나온 결함의 회귀 시험. 결함마다 재현 시나리오를 저장소 안에 고정한다 (spikes/integration/report.md "독립 검수 결함과 조치").

 1. 가져온(외래 기반) 프로젝트에서 뷰·치수·태그를 지우거나 병합이 지우면 INTERNAL(KeyError) 이던 것
 2. 병합 ID_COLLISION 의 take=both 가 params 안의 id 참조를 함께 옮기지 않던 것, 그리고 params 와 관계의 어긋남을 model.validate 가 잡는 것
"""
import json
import sqlite3

import pytest

import ifcopenshell

from test_integration_g1 import F, S, Engine, _ok, branch, dump_hashes, make_imported

FID = "0" * 21 + "1"


# ================================================================ 1. 비 IFC 요소 삭제 (가져온 프로젝트)
def _annotated(b: S.Bench, p: str) -> dict:
    """가져온 프로젝트에 뷰 + 치수 + 태그 + 패밀리 문을 만든다. 반환: 각 id."""
    _ok(b.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}, project=p))
    _ok(b.run("annot.dim.create", {"id": F.A1, "view": F.V1, "a": f"wall:{F.W3}#axis.start", "b": f"wall:{F.W3}#axis.end"}, project=p))
    _ok(b.run("annot.tag.create", {"view": F.V1, "target": F.W3, "fields": ["name"]}, project=p))
    _ok(b.run("family.place", {"id": FID, "family": "door.single", "values": {}, "host": F.W3, "offset": 4500}, project=p))
    tag = [i["id"] for i in _ok(b.run("element.query", {"kind": "tag", "limit": 5}, project=p))["items"]][0]
    return {"view": F.V1, "dim": F.A1, "tag": tag, "finst": FID}


@pytest.mark.parametrize("cmd,which", [
    ("element.delete", "dim"), ("element.delete", "tag"), ("element.delete", "view"), ("element.delete", "finst"),
    ("view.delete", "view"), ("annot.delete", "dim"), ("annot.delete", "tag"),
])
def test_r1_가져온_프로젝트에서_뷰_주석_삭제가_죽지_않고_되돌려진다(tmp_path, cmd, which):
    info = make_imported(tmp_path)
    b = S.Bench(tmp_path)
    try:
        p = info["name"]
        ids = _annotated(b, p)
        before = S.trip(b.hashes(p))
        r = b.run(cmd, {"id": ids[which]}, project=p)
        assert r["ok"], (cmd, which, r.get("error"))
        gone = set(r["result"]["deleted"])
        assert ids[which] in gone
        if which == "view":   # 뷰를 지우면 속한 주석도 같은 트랜잭션에서 지워진다
            assert {ids["dim"], ids["tag"]} <= gone
        assert _ok(b.run("model.validate", {}, project=p))["errors"] == 0
        assert S.trip(b.hashes(p)) != before
        _ok(b.run("history.undo", {}, project=p))   # 실행 취소가 모두 되살린다
        assert S.trip(b.hashes(p)) == before
    finally:
        b.close()


def test_r1b_병합이_가져온_프로젝트의_뷰와_주석을_지워도_죽지_않는다(tmp_path):
    info = make_imported(tmp_path)
    b = S.Bench(tmp_path)
    try:
        _annotated(b, info["name"])
    finally:
        b.close()
    branch(tmp_path, info["name"], "a.dobim", "b.dobim")
    b = S.Bench(tmp_path)
    try:
        # B: 뷰(와 주석)를 지운다. A: 다른 벽의 속성만 바꾼다. A <- B 병합이 뷰·치수·태그를 지운다
        _ok(b.run("view.delete", {"id": F.V1}, project="b.dobim"))
        _ok(b.run("pset.set", {"id": info["foreign"], "pset": "Pset_S2", "prop": "Note", "value": "A"}, project="a.dobim"))
        r = b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")
        assert r["ok"], r.get("error")
        kinds = {i["kind"] for i in _ok(b.run("element.query", {"limit": 1000}, project="a.dobim"))["items"]}
        assert not ({"view", "dim", "tag"} & kinds), kinds
        assert _ok(b.run("model.validate", {}, project="a.dobim"))["errors"] == 0
        # 되돌리면 병합 전(뷰·주석이 있는 상태)
        _ok(b.run("history.undo", {}, project="a.dobim"))
        assert {"view", "dim", "tag"} <= {i["kind"] for i in _ok(b.run("element.query", {"limit": 1000}, project="a.dobim"))["items"]}
    finally:
        b.close()


# ================================================================ 2. ID_COLLISION take=both 와 params 안의 id 참조
def _anc(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "anc.dobim")
    finally:
        b.close()
    branch(tmp_path, "anc.dobim", "a.dobim", "b.dobim")


def _both_apply(b: S.Bench, ws) -> None:
    prev = _ok(b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim"))
    keys = [{"key": i["key"], "take": "both"} for i in prev["items"] if i["type"] == "ID_COLLISION"]
    assert keys, prev
    _ok(b.run("merge.apply", {"other": "b.dobim", "resolve": keys}, project="a.dobim"))


def _names(b: S.Bench) -> dict:
    return {i["id"]: i.get("name") for i in _ok(b.run("element.query", {"limit": 1000}, project="a.dobim"))["items"]}


def test_r2_뷰_id_충돌을_둘_다_살리면_치수의_params_view가_관계와_같은_뷰를_가리킨다(tmp_path):
    _anc(tmp_path)
    b = S.Bench(tmp_path)
    try:
        _ok(b.run("view.create", {"name": "A plan", "type": "plan", "level": F.LV1, "cut_height": 1200}, project="a.dobim"))
        v = _ok(b.run("view.create", {"name": "B plan", "type": "plan", "level": F.LV1, "cut_height": 900}, project="b.dobim"))
        _ok(b.run("annot.dim.create", {"view": v["id"], "a": f"wall:{F.W1}#trim.start", "b": f"wall:{F.W1}#trim.end"}, project="b.dobim"))
        _ok(b.run("annot.tag.create", {"view": v["id"], "target": F.W1, "fields": ["name"]}, project="b.dobim"))
        _both_apply(b, tmp_path)
        nm = _names(b)
        e = Engine(tmp_path / "a.dobim", "x", create=False)
        try:
            n_annot = 0
            for i, r in e.st.el.items():
                if r["kind"] in ("dim", "tag"):
                    n_annot += 1
                    # 상대(B)의 주석은 상대의 뷰(B plan)에 달린다: 파라미터도 관계도 같은 대상
                    assert nm[r["params"]["view"]] == "B plan", (r["kind"], nm[r["params"]["view"]])
                    assert [nm[d] for d in e.st.out.get(("annot_of", i), [])] == ["B plan"]
            assert n_annot == 2
        finally:
            e.close()
        assert _ok(b.run("model.validate", {}, project="a.dobim"))["errors"] == 0
        assert _ok(b.run("annot.check", {}, project="a.dobim"))["ok"] == 2
    finally:
        b.close()


def test_r2b_레벨_id_충돌을_둘_다_살리면_상대_뷰의_params_level이_상대_레벨을_가리킨다(tmp_path):
    _anc(tmp_path)
    b = S.Bench(tmp_path)
    try:
        _ok(b.run("level.create", {"name": "L-ours", "elevation": 6000}, project="a.dobim"))
        lv = _ok(b.run("level.create", {"name": "L-theirs", "elevation": 9000}, project="b.dobim"))
        _ok(b.run("view.create", {"name": "B plan", "type": "plan", "level": lv["id"]}, project="b.dobim"))
        _both_apply(b, tmp_path)
        nm = _names(b)
        e = Engine(tmp_path / "a.dobim", "x", create=False)
        try:
            views = [r for r in e.st.el.values() if r["kind"] == "view"]
            assert len(views) == 1 and nm[views[0]["params"]["level"]] == "L-theirs"
        finally:
            e.close()
        assert _ok(b.run("model.validate", {}, project="a.dobim"))["errors"] == 0
    finally:
        b.close()


def test_r2c_벽_id_충돌을_둘_다_살리면_상대_치수의_참조_문자열도_옮겨진다(tmp_path):
    """주석 참조 문자열("wall:<id>#역할")과 태그의 target 도 새 id 로 옮겨진다(id 가 문자열 안에 들어 있는 경우)."""
    _anc(tmp_path)
    b = S.Bench(tmp_path)
    try:
        w_a = _ok(b.run("wall.create", {"name": "WA", "level": F.LV1, "axis": [0, 3000, 2000, 3000], "thickness": 200, "height": 3000}, project="a.dobim"))
        w_b = _ok(b.run("wall.create", {"name": "WB", "level": F.LV1, "axis": [0, 5000, 2000, 5000], "thickness": 200, "height": 3000}, project="b.dobim"))
        if w_a["id"] != w_b["id"]:
            pytest.skip("자동 id 가 달라 충돌하지 않는다")
        v = _ok(b.run("view.create", {"name": "B plan", "type": "plan", "level": F.LV1}, project="b.dobim"))
        _ok(b.run("annot.dim.create", {"view": v["id"], "a": f"wall:{w_b['id']}#axis.start", "b": f"wall:{w_b['id']}#axis.end"}, project="b.dobim"))
        _ok(b.run("annot.tag.create", {"view": v["id"], "target": w_b["id"], "fields": ["name"]}, project="b.dobim"))
        _both_apply(b, tmp_path)
        nm = _names(b)
        e = Engine(tmp_path / "a.dobim", "x", create=False)
        try:
            for r in e.st.el.values():
                if r["kind"] == "dim":
                    assert all(nm[x.split(":", 1)[1].split("#")[0]] == "WB" for x in (r["params"]["a"], r["params"]["b"]))
                if r["kind"] == "tag":
                    assert nm[r["params"]["target"]] == "WB"
        finally:
            e.close()
        assert _ok(b.run("annot.check", {}, project="a.dobim"))["ok"] == 2
    finally:
        b.close()


def test_r2d_params_참조와_관계가_어긋나면_model_validate가_오류로_잡는다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "p.dobim")
        _ok(b.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1}, project="p.dobim"))
        v2 = _ok(b.run("view.create", {"name": "2F", "type": "plan", "level": F.LV1}, project="p.dobim"))["id"]
        _ok(b.run("annot.dim.create", {"id": F.A1, "view": F.V1, "a": f"wall:{F.W1}#trim.start", "b": f"wall:{F.W1}#trim.end"}, project="p.dobim"))
        assert _ok(b.run("model.validate", {}, project="p.dobim"))["errors"] == 0
    finally:
        b.close()
    # 상태 테이블에서 치수의 params.view 만 다른 뷰로 바꾼다(관계는 그대로): 옛 병합이 만들던 어긋남
    c = sqlite3.connect(str(tmp_path / "p.dobim"))
    p = json.loads(c.execute("SELECT params FROM element WHERE id=?", (F.A1,)).fetchone()[0])
    p["view"] = v2
    c.execute("UPDATE element SET params=? WHERE id=?", (json.dumps(p, ensure_ascii=False, sort_keys=True, separators=(",", ":")), F.A1))
    c.commit()
    c.close()
    b = S.Bench(tmp_path)
    try:
        r = _ok(b.run("model.validate", {}, project="p.dobim"))
        assert r["errors"] >= 1 and any("annot_of" in v for v in r["violations"]), r
    finally:
        b.close()


def test_r2e_파라미터_참조_표가_커맨드가_만든_모든_요소에서_관계와_맞는다(tmp_path):
    """kinds 의 refs 표(관계 짝이 있는 항목)가 실제 커맨드 결과와 일치한다: 치수·태그의 view ↔ annot_of, 인스턴스 type ↔ of_type."""
    info = make_imported(tmp_path)
    b = S.Bench(tmp_path)
    try:
        ids = _annotated(b, info["name"])
        assert _ok(b.run("model.validate", {}, project=info["name"]))["errors"] == 0
        assert ids
    finally:
        b.close()
    from dobim_core.kinds import ELEMENT_KINDS, param_ref_targets
    assert {k for k, v in ELEMENT_KINDS.items() if any(r[2] for r in v.refs)} == {"dim", "tag", "finst"}
    e = Engine(tmp_path / info["name"], "x", create=False)
    try:
        seen = set()
        for eid, r in e.st.el.items():
            for name, rel, target in param_ref_targets(r["kind"], r["params"]):
                seen.add((r["kind"], name, rel))
                assert e.st.out.get((rel, eid)) == [target], (eid, name, rel)
        assert seen == {("dim", "view", "annot_of"), ("tag", "view", "annot_of"), ("finst", "type", "of_type")}
    finally:
        e.close()


# ================================================================ 3. 옛 형식 DB 의 다시 판정
def _to_legacy(path, flip: str | None = None) -> None:
    """가져온 프로젝트를 옛 형식(시작 상태 기록 seq 0 과 형식 표지가 없다)으로 되돌린다. flip 이 있으면 그 요소를 '옛 판정은 저작으로 봤다'로 만든다."""
    c = sqlite3.connect(str(path))
    c.execute("DELETE FROM op_log WHERE seq=0")
    c.execute("DELETE FROM meta WHERE key='closure_format'")
    if flip:
        c.execute("UPDATE element SET origin='authored' WHERE id=?", (flip,))
    c.commit()
    c.close()


def test_r3_옛_형식_DB에서_다시_판정이_저작을_분리로_동결해도_열린다(tmp_path):
    info = make_imported(tmp_path)
    path = tmp_path / info["name"]
    _to_legacy(path, flip=info["detached"])
    eng = Engine(path, "t", create=False)   # 예전에는 op_log seq=0 을 fetchone()[0] 으로 읽다 TypeError -> INTERNAL
    try:
        assert eng.rejudged == [info["detached"]]
        assert eng.st.el[info["detached"]]["origin"] == "detached"
    finally:
        eng.close()
    c = sqlite3.connect(str(path))
    assert c.execute("SELECT origin FROM element WHERE id=?", (info["detached"],)).fetchone()[0] == "detached"
    assert c.execute("SELECT COUNT(*) FROM op_log WHERE seq=0").fetchone()[0] == 0   # 없던 시작 상태 기록을 새로 만들지 않는다
    assert dict(c.execute("SELECT key, value FROM meta")).get("closure_format")
    c.close()
    b = S.Bench(tmp_path)   # 디스패치 경로로도 열리고, 동결이 적용된다
    try:
        r = b.run("wall.move", {"id": info["detached"], "dx": 1}, project=info["name"])
        assert not r["ok"] and r["error"]["code"] == "ELEMENT_FROZEN", r
        assert _ok(b.run("wall.move", {"id": F.W3, "dx": 10}, project=info["name"]))
    finally:
        b.close()


def test_r3b_옛_형식_DB는_병합할_수_없다고_알린다(tmp_path):
    info = make_imported(tmp_path)
    branch(tmp_path, info["name"], "a.dobim", "b.dobim")
    _to_legacy(tmp_path / "a.dobim")
    b = S.Bench(tmp_path)
    try:
        r = b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim")
        assert not r["ok"] and r["error"]["code"] == "CONSTRAINT", r
    finally:
        b.close()


# ================================================================ 보강: 다시 판정 뒤 메모리의 투영 명세 해시
def test_r3c_다시_판정한_뒤_메모리의_import_spec이_DB와_같다(tmp_path):
    """열기 전에 읽어 둔 명세 해시가 다시 판정이 DB 를 고친 뒤에도 낡은 채로 남으면, 첫 세션과 재오픈 세션이 같은 요소를 다르게 투영한다."""
    info = make_imported(tmp_path)
    path = tmp_path / info["name"]
    c = sqlite3.connect(str(path))
    good = dict(c.execute("SELECT id, spec_hash FROM import_spec"))
    assert good
    c.execute("DELETE FROM meta WHERE key='closure_format'")
    c.execute("UPDATE import_spec SET spec_hash='old-format'")
    c.execute("UPDATE element SET projection_hash='old-format' WHERE projection_hash IS NOT NULL")
    c.commit()
    c.close()
    first = Engine(path, "t", create=False)
    try:
        assert first._import_spec == good, "다시 판정 뒤에도 메모리의 명세 해시가 낡았다"
        h1 = first.hashes()
    finally:
        first.close()
    second = Engine(path, "t", create=False)
    try:
        assert second._import_spec == good
        assert second.hashes() == h1
    finally:
        second.close()


# ================================================================ 4. 다시 판정으로 동결된 요소의 동결 전 편집은 되돌리기·다시 실행으로 되살아나지 않는다
def test_r4_다시_판정으로_동결된_요소의_동결_전_편집을_되돌려도_저작으로_되살아나지_않는다(tmp_path, monkeypatch):
    import dobim_core.foreign as FO
    info = make_imported(tmp_path)
    p = info["name"]
    b = S.Bench(tmp_path)
    try:
        _ok(b.run("wall.move", {"id": F.W3, "dx": 100}, project=p))   # 동결 전 편집
    finally:
        b.close()
    c = sqlite3.connect(str(tmp_path / p))
    c.execute("DELETE FROM meta WHERE key='closure_format'")   # 다음에 열 때 다시 판정한다
    c.commit()
    c.close()
    orig = FO.plan_import

    def flipped(data):   # 새 형식의 판정은 W3 를 분리로 본다(판정이 뒤집히는 것을 흉내)
        pl = orig(data)
        for r in pl.rows:
            if r["id"] == F.W3:
                r["origin"] = "detached"
        return pl
    monkeypatch.setattr(FO, "plan_import", flipped)
    b = S.Bench(tmp_path)
    try:
        before = _ok(b.run("element.get", {"id": F.W3}, project=p))
        assert before["origin"] == "detached"
        r = b.run("history.undo", {}, project=p)
        assert not r["ok"] and r["error"]["code"] == "ELEMENT_FROZEN", r
        after = _ok(b.run("element.get", {"id": F.W3}, project=p))
        assert after["origin"] == "detached" and after["params"] == before["params"]   # 거절은 아무것도 바꾸지 않았다
        # 정상 흐름의 되돌리기는 그대로 된다: 저작 벽의 편집(스택 맨 위가 막혀 있지 않은 새 트랜잭션)
        _ok(b.run("wall.move", {"id": F.W2, "dx": 5}, project=p))
        _ok(b.run("history.undo", {}, project=p))
        _ok(b.run("history.redo", {}, project=p))
    finally:
        b.close()


# ================================================================ 5. 시작 상태 기록 비교에는 행위자가 들어가지 않는다

def test_r5_같은_IFC를_두_사람이_따로_가져와도_병합된다(tmp_path):
    make_imported(tmp_path, "tmp.dobim")
    guid = "5d0b1a50-0000-4a00-8000-0d0b1a5000f3"   # 같은 프로젝트 식별자(project_uuid)를 쓴 복사본끼리만 병합한다(별도 규칙)
    for actor, pn in (("alice", "a.dobim"), ("bob", "b.dobim")):
        b = S.Bench(tmp_path, actor=actor)
        try:
            assert b.import_ifc("tampered.ifc", pn, guid=guid)["ok"]
        finally:
            b.close()
    b = S.Bench(tmp_path, actor="alice")
    try:
        _ok(b.run("wall.move", {"id": F.W3, "dx": 100}, project="a.dobim"))
    finally:
        b.close()
    b = S.Bench(tmp_path, actor="bob")
    try:
        _ok(b.run("wall.set", {"id": F.W2, "name": "bob"}, project="b.dobim"))
    finally:
        b.close()
    b = S.Bench(tmp_path, actor="alice")
    try:
        prev = b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim")
        assert prev["ok"] and prev["result"]["conflict_count"] == 0, prev
        assert b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")["ok"]
        g = _ok(b.run("element.get", {"id": F.W2}, project="a.dobim"))
        assert g["params"]["name"] == "bob"
    finally:
        b.close()


def test_r5b_다른_IFC에서_가져온_프로젝트는_여전히_병합을_거절한다(tmp_path):
    """행위자를 비교에서 뺀 뒤에도, 원본 바이트가 다르면 같은 시작 상태가 아니다."""
    make_imported(tmp_path, "tmp.dobim")
    f = ifcopenshell.open(str(tmp_path / "tampered.ifc"))
    f.by_guid(F.W3).Name = "다른 이름"
    f.write(str(tmp_path / "tampered2.ifc"))
    for src, pn in (("tampered.ifc", "a.dobim"), ("tampered2.ifc", "b.dobim")):
        b = S.Bench(tmp_path)
        try:
            assert b.import_ifc(src, pn)["ok"]
        finally:
            b.close()
    b = S.Bench(tmp_path)
    try:
        r = b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim")
        assert not r["ok"] and r["error"]["code"] == "CONSTRAINT", r
    finally:
        b.close()


# ================================================================ 6. 시험의 이빨: 위조 로그와 쓰기 관문
def _forge_entry(ws, name: str, op: str, effects: list) -> None:
    """상대 프로젝트의 로그 끝에 커맨드로는 만들 수 없는 항목(효과 목록)을 손으로 끼워 넣는다."""
    from dobim_core.engine import canon, invert
    c = sqlite3.connect(str(ws / name))
    try:
        head = c.execute("SELECT COALESCE(MAX(seq), 0) FROM op_log").fetchone()[0]
        c.execute("INSERT INTO op_log (seq, txn_id, actor, base_rev, ts, op, args, inverse) VALUES (?,?,?,?,?,?,?,?)",
                  (head + 1, f"t{head + 1}", "forger", head, "2026-01-01T00:00:00", op, canon({"id": effects[0][1]}), canon(invert(effects))))
        c.commit()
    finally:
        c.close()


D9 = "9" * 22
_FORGED_DOOR = {"kind": "door", "ifc_class": "IfcDoor", "origin": "authored", "family_id": "door.single",
                "params": {"name": "forged", "width": 900.0, "height": 2100.0, "sill": 0.0, "offset": 1000.0}}


def test_r6_분리_벽에_문을_호스팅하는_위조_로그는_병합_미리보기와_적용_모두_거절한다(tmp_path):
    info = make_imported(tmp_path)
    branch(tmp_path, info["name"], "a.dobim", "b.dobim")
    _forge_entry(tmp_path, "b.dobim", "door.place", [["el", D9, None, _FORGED_DOOR], ["rel", "hosts", info["detached"], D9, 0, 1]])
    b = S.Bench(tmp_path)
    try:
        prev = b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim")   # 예전에는 "충돌 0" 으로 보였다
        assert not prev["ok"] and prev["error"]["code"] == "ELEMENT_FROZEN", prev
        assert info["detached"] in prev["error"]["details"]["elements"]
        r = b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")
        assert not r["ok"] and r["error"]["code"] == "ELEMENT_FROZEN", r
        assert D9 not in {i["id"] for i in _ok(b.run("element.query", {"limit": 1000}, project="a.dobim"))["items"]}   # 아무것도 들어오지 않았다
    finally:
        b.close()


def test_r6b_외래_요소를_만드는_위조_로그도_미리보기가_알린다(tmp_path):
    info = make_imported(tmp_path)
    branch(tmp_path, info["name"], "a.dobim", "b.dobim")
    row = {"kind": "foreign", "ifc_class": "IfcWall", "origin": "foreign", "family_id": None, "params": {"name": "forged"}}
    _forge_entry(tmp_path, "b.dobim", "wall.create", [["el", D9, None, row]])
    b = S.Bench(tmp_path)
    try:
        prev = b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim")
        assert not prev["ok"] and prev["error"]["code"] == "ELEMENT_FROZEN", prev
    finally:
        b.close()


def test_r6c_정상_병합은_동결_예고에_걸리지_않는다(tmp_path):
    """분리된 벽의 속성 편집, 저작 문을 지워 분리된 호스트와의 관계를 끊는 병합은 정상이다(끊는 것·속성은 막지 않는다)."""
    info = make_imported(tmp_path)
    branch(tmp_path, info["name"], "a.dobim", "b.dobim")
    b = S.Bench(tmp_path)
    try:
        _ok(b.run("pset.set", {"id": info["detached"], "pset": "Pset_S2", "prop": "Note", "value": "B"}, project="b.dobim"))
        _ok(b.run("element.delete", {"id": F.D1}, project="b.dobim"))   # D1 은 분리된 W1 에 호스팅된 저작 문
        prev = b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim")
        assert prev["ok"] and prev["result"]["conflict_count"] == 0, prev
        assert b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")["ok"]
    finally:
        b.close()


def test_r6d_쓰기_관문이_동결_요소를_스스로_막는다(tmp_path):
    """Ctx.put_el·rel·set_material 의 저작 확인은 커맨드가 authored() 를 빠뜨려도 동결 요소를 지킨다: 커맨드를 거치지 않고 관문을 직접 부른다.
    (커맨드 쪽 앞단 확인만으로는 관문 검사가 꺼져도 다른 시험이 모두 통과해 관문이 검증되지 않았다)"""
    from dobim_core.engine import Ctx
    from dobim_core.errors import DobimError
    info = make_imported(tmp_path)
    eng = Engine(tmp_path / info["name"], "t", create=False)
    try:
        ctx = Ctx(eng, eng.head + 1, "t", dry_run=True)
        w1, d2 = info["detached"], F.D2
        row = eng.st.el[w1]
        assert row["origin"] == "detached" and eng.st.el[d2]["origin"] == "authored"

        def frozen(fn):
            with pytest.raises(DobimError) as ei:
                fn()
            assert ei.value.code == "ELEMENT_FROZEN"
        frozen(lambda: ctx.rel("hosts", w1, d2, True))                       # 분리된 벽에 문을 호스팅
        frozen(lambda: ctx.put_el(w1, dict(row, params=dict(row["params"], thickness=999.0))))   # 분리된 벽의 수정
        frozen(lambda: ctx.put_el(w1, None))                                 # 삭제
        frozen(lambda: ctx.put_el(D9, dict(_FORGED_DOOR, origin="detached")))   # 동결 요소를 새로 만들기
        frozen(lambda: ctx.set_material(w1, "Steel"))                        # 재료(W1 의 지금 재료는 Concrete)
        # 끊는 것과 저작 요소는 막지 않는다
        assert ctx.rel("hosts", w1, F.D1, False) is True
        assert not ctx.effects or ctx.effects[-1][0] == "rel"
    finally:
        eng.close()


# ================================================================ 7. 알려진 한계: 패밀리 인스턴스가 있는 저작 프로젝트의 IFC 왕복
def test_r7_패밀리_인스턴스의_호스트_벽은_왕복_뒤_분리로_동결된다_알려진_한계(tmp_path):
    """현재 동작을 고정한다(고친 것이 아니다). 패밀리 인스턴스(finst)가 놓인 저작 프로젝트를 IFC 로 내보냈다 다시 가져오면:
      - finst 의 DoBIM_Intent 는 문·벽·레벨의 의도 모양이 아니라서 해석되지 않고(foreign.KIND_OF 가 IfcDoor 를 문으로 보지만 의도 모양이 다르다)
        인스턴스와 그 개구부는 외래 요소가 된다.
      - 호스트 벽의 투영 클로저에는 그 개구부(voids)가 들어 있는데, 가져오기가 의도로 다시 투영하는 벽에는 개구부가 없어 해시가 다르다.
        그래서 호스트 벽은 '벽에 뚫린 개구부'를 이유로 분리됨(동결)이 된다. 안전한 쪽의 판정이지만 저작 프로젝트가 왕복 뒤 호스트 벽을 못 고친다.
    고치려면 가져오기가 패밀리 타입·인스턴스의 의도를 복원해 개구부까지 의도 재투영 비교에 넣어야 한다(ADR-0013 알려진 한계, 별도 작업)."""
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "src.dobim")
        _ok(b.run("family.place", {"id": FID, "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 4500}, project="src.dobim"))
        b.export("fam.ifc", "src.dobim")
        r = b.import_ifc("fam.ifc", "imp.dobim")
        assert r["ok"], r
        det = {d["id"]: d["reasons"] for d in r["result"]["detached"]}
        assert det == {F.W3: ["벽에 뚫린 개구부"]}, det           # 호스트 벽만, 그 이유로
        assert b.ids_of("detached", "imp.dobim") == [F.W3]
        foreign = {i["id"]: i for i in _ok(b.run("element.query", {"origin": "foreign", "limit": 50}, project="imp.dobim"))["items"]}
        assert FID in foreign and len(foreign) == 2   # 인스턴스(IfcDoor)와 그 개구부(IfcOpeningElement)
        # 패밀리가 없는 벽은 그대로 저작이다
        assert _ok(b.run("wall.move", {"id": F.W1, "dx": 10}, project="imp.dobim"))
        # 동결의 결과: 호스트 벽의 형상은 고칠 수 없고 속성은 고칠 수 있다
        assert b.run("wall.move", {"id": F.W3, "dx": 10}, project="imp.dobim")["error"]["code"] == "ELEMENT_FROZEN"
        assert _ok(b.run("pset.set", {"id": F.W3, "pset": "Pset_S2", "prop": "Note", "value": "x"}, project="imp.dobim"))
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
    finally:
        b.close()
