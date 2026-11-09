# SPDX-License-Identifier: MPL-2.0
"""독립 검수(통합 브랜치)에서 나온 결함의 회귀 시험. 결함마다 재현 시나리오를 저장소 안에 고정한다 (spikes/integration/report.md "독립 검수 결함과 조치").

 1. 가져온(외래 기반) 프로젝트에서 뷰·치수·태그를 지우거나 병합이 지우면 INTERNAL(KeyError) 이던 것
 2. 병합 ID_COLLISION 의 take=both 가 params 안의 id 참조를 함께 옮기지 않던 것, 그리고 params 와 관계의 어긋남을 model.validate 가 잡는 것
"""
import json
import sqlite3

import pytest

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
