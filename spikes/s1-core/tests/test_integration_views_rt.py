# SPDX-License-Identifier: MPL-2.0
"""뷰·치수·태그 정의의 IFC 왕복 시험 (ADR-0015): 뷰·주석이 있는 저작 프로젝트를 IFC 로 내보냈다 다시 가져오면 전부 복원되고 해시 3종이 같다.

배경: 통합 보고서 10.4절. 뷰(S3)·치수·태그(S5)는 IFC 신원이 없는 의도 요소라 예전에는 내보낸 IFC 에 정의가 없어 왕복하면 사라졌다.
지금은 정의를 프로젝트 속성셋 DoBIM_Defs(속성 하나 = 정의 하나, 이름 = 요소 id, 값에 변조 탐지 해시)에 싣고, 가져오기가 검증해 되살린다.

 1. 수정 없는 왕복: 전부 복원(authored), 해시 3종 같음, 두 번 내보낸 IFC 같음, 도면 산출(view.linework·SVG) 같음, 다시 열어도 같음
 2. 왕복 뒤 같은 편집열(뷰·주석 생성·수정·삭제, 참조 요소 삭제, 되돌리기·다시하기)이 저작 프로젝트와 같은 해시
 3. 병합: 가져온 뷰·주석이 있는 두 복사본의 병합이 순차 적용과 같다
 4. 변조: 정의 값·해시·참조·id 를 바꾸면 그 정의(와 그것에 기대는 주석)만 복원하지 않고, 가져오기는 죽지 않고, 되살리지 않은 정의는 파일에 그대로 남는다
 5. 옛 프로젝트·옛 파일 호환, 뷰·주석이 없는 프로젝트의 IFC 는 그대로
 6. 규모: 뷰·주석이 많아도 가져오기·내보내기가 선형
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

import ifcopenshell
import ifcopenshell.guid
import ifcopenshell.util.element as ue

from test_integration_g1 import F, S, Engine, _ok

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "s2-roundtrip" / "tools"))
import ifcdiff_check  # noqa: E402

from dobim_core import viewdefs as VD  # noqa: E402

V1 = F.V1                    # 1F 평면 뷰
V2 = "4" + "0" * 20 + "2"     # 2F 평면 뷰(레벨 LV2 에 벽이 없다)
VS = "4" + "0" * 20 + "3"     # 단면 뷰
VE = "4" + "0" * 20 + "4"     # 입면 뷰
A1 = F.A1                    # 1F 평면 치수(벽 W1 의 양 끝)
A2 = "5" + "0" * 20 + "2"      # 단면 높이 치수(레벨 LV1 ~ LV2)
A3 = "5" + "0" * 20 + "3"      # 1F 평면 치수(벽 W2 의 양 끝)
T1 = "5" + "0" * 20 + "4"      # 1F 태그(문 D1)
T2 = "5" + "0" * 20 + "5"      # 1F 태그(벽 W2)
FD = "0" * 21 + "1"           # 패밀리 문(호스트 W3)
ALL_DEFS = {V1, V2, VS, VE, A1, A2, A3, T1, T2}
R = lambda k, i, r: f"{k}:{i}#{r}"   # noqa: E731


def _build(b: S.Bench, p: str = "src.dobim") -> None:
    S.build_authored(b, p)
    r = lambda n, a: _ok(b.run(n, a, project=p))   # noqa: E731
    r("family.place", {"id": FD, "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 4500})
    r("view.create", {"id": V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200, "scale": 50})
    r("view.create", {"id": V2, "name": "2F 평면", "type": "plan", "level": F.LV2})
    r("view.create", {"id": VS, "name": "A-A 단면", "type": "section", "p1": [0, 2000], "p2": [6000, 2000], "depth": 8000, "flip": True})
    r("view.create", {"id": VE, "name": "남측 입면", "type": "elevation", "dir": "S", "scale": 200})
    r("annot.dim.create", {"id": A1, "view": V1, "a": R("wall", F.W1, "trim.start"), "b": R("wall", F.W1, "trim.end"), "offset": 750})
    r("annot.dim.create", {"id": A2, "view": VS, "a": R("level", F.LV1, "elevation"), "b": R("level", F.LV2, "elevation"), "at": [100, 200]})
    r("annot.dim.create", {"id": A3, "view": V1, "a": R("wall", F.W2, "axis.start"), "b": R("wall", F.W2, "axis.end")})
    r("annot.tag.create", {"id": T1, "view": V1, "target": F.D1, "fields": ["name", "width"]})
    r("annot.tag.create", {"id": T2, "view": V1, "target": F.W2, "fields": ["name", "thickness", "length"], "offset": [250, -300]})


def _origins(b: S.Bench, p: str) -> dict:
    out = {}
    for origin in ("detached", "foreign"):
        for i in b.ids_of(origin, p):
            out[i] = origin
    return out


def _ids(b: S.Bench, p: str, *kinds) -> set:
    out = set()
    for k in kinds:
        out |= {i["id"] for i in _ok(b.run("element.query", {"kind": k, "limit": 1000}, project=p))["items"]}
    return out


def _defs_pset(f):
    return ue.get_pset(f.by_type("IfcProject")[0], "DoBIM_Defs", should_inherit=False)


def _def_props(f) -> dict:
    """프로젝트 속성셋 DoBIM_Defs 의 {이름: 속성 엔티티}."""
    ps = f.by_id(_defs_pset(f)["id"])
    return {p.Name: p for p in ps.HasProperties}


def _get(f, eid) -> dict:
    return json.loads(_def_props(f)[eid].NominalValue.wrappedValue)


def _put(f, eid, d, *, fix=True):
    """정의 하나를 다시 쓴다(fix 면 변조 탐지 해시를 새 내용에 맞춘다: 해시까지 맞춘 위조)."""
    if fix:
        d["check"] = VD.check_of(eid, d["kind"], d["params"])
    _def_props(f)[eid].NominalValue = f.createIfcText(json.dumps(d))


def _edit(f, eid, fn, *, fix=True):
    d = _get(f, eid)
    fn(d)
    _put(f, eid, d, fix=fix)


def _export_ifc(b, name="e1.ifc", project="src.dobim") -> Path:
    return b.export(name, project)


def _tamper(tmp_path, mutate, *, build=_build):
    """저작 프로젝트를 내보낸 IFC 를 mutate(f) 로 바꿔 가져온다. 반환: (Bench, 가져오기 결과)."""
    b = S.Bench(tmp_path)
    build(b)
    b.export("e1.ifc", "src.dobim")
    f = ifcopenshell.open(str(tmp_path / "e1.ifc"))
    mutate(f)
    f.write(str(tmp_path / "t.ifc"))
    r = b.import_ifc("t.ifc", "imp.dobim")
    assert r["ok"], r
    return b, r["result"]


# ================================================================ 1. 수정 없는 왕복
def test_v1_뷰_주석_저작_프로젝트의_왕복은_전부_authored이고_해시_3종이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        h0 = S.trip(b.hashes("src.dobim"))
        e1 = _export_ifc(b)
        f = ifcopenshell.open(str(e1))
        assert set(_def_props(f)) == ALL_DEFS                                   # 정의 9개가 프로젝트 속성셋에 실렸다
        assert not [e for e in f.by_type("IfcProduct") if e.is_a("IfcAnnotation")]   # IfcProduct 는 늘지 않는다
        r = b.import_ifc("e1.ifc", "imp.dobim")
        assert r["ok"], r
        res = r["result"]
        assert res["elements"] == {"authored": 18, "foreign": 0, "detached": 0}, res["elements"]   # 레벨 2 + 벽 3 + 문 2 + 패밀리 타입 1 + 인스턴스 1 + 뷰 4 + 치수 3 + 태그 2
        assert res["detached"] == [] and res["warnings"] == []
        assert _ids(b, "imp.dobim", "view") == {V1, V2, VS, VE}
        assert _ids(b, "imp.dobim", "dim") == {A1, A2, A3} and _ids(b, "imp.dobim", "tag") == {T1, T2}
        assert S.trip(b.hashes("imp.dobim")) == h0                              # state·derived·ifc 모두 같다
        e2 = b.export("e2.ifc", "imp.dobim")
        assert e1.read_bytes() == e2.read_bytes()
        assert ifcdiff_check.strict_diff(str(e1), str(e2))["total"] == 0
        if S.have_ifcdiff():
            d = S.ifdiff(e1, e2)
            assert d["ifcdiff_total"] == 0 and d["strict_total"] == 0, d
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
        assert b.env.engine("imp.dobim").intent_consistency() == []
        assert _ok(b.run("annot.check", {}, project="imp.dobim"))["problems"] == 0
        # authored 이므로 편집된다
        _ok(b.run("view.set", {"id": V1, "cut_height": 1500}, project="imp.dobim"))
        _ok(b.run("annot.delete", {"id": A1}, project="imp.dobim"))
    finally:
        b.close()


def test_v1b_복원된_뷰의_도면_산출이_원본과_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        b.export("e1.ifc", "src.dobim")
        _ok(b.import_ifc("e1.ifc", "imp.dobim"))
        for v in (V1, V2, VS, VE):
            a = _ok(b.run("view.linework", {"id": v, "limit": 1000}, project="src.dobim"))
            c = _ok(b.run("view.linework", {"id": v, "limit": 1000}, project="imp.dobim"))
            assert a == c, v
            assert a["summary"]["digest"] == c["summary"]["digest"]
            assert _ok(b.run("view.list", {}, project="src.dobim")) == _ok(b.run("view.list", {}, project="imp.dobim"))
            ea = b.destructive("view.export", {"id": v, "path": f"a-{v}.svg"}, "src.dobim")
            ec = b.destructive("view.export", {"id": v, "path": f"c-{v}.svg"}, "imp.dobim")
            assert ea["ok"] and ec["ok"]
            assert (tmp_path / f"a-{v}.svg").read_bytes() == (tmp_path / f"c-{v}.svg").read_bytes()
            assert ea["result"]["digest"] == ec["result"]["digest"] and ea["result"]["annotations"] == ec["result"]["annotations"]
        la = _ok(b.run("annot.list", {"limit": 100}, project="src.dobim"))
        lc = _ok(b.run("annot.list", {"limit": 100}, project="imp.dobim"))
        assert la["items"] == lc["items"]
    finally:
        b.close()


def test_v1c_다시_열어도_같고_재오픈한_저작_프로젝트도_정의를_내보낸다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        h0 = S.trip(b.hashes("src.dobim"))
        b.export("e0.ifc", "src.dobim")
    finally:
        b.close()
    eng = Engine(tmp_path / "src.dobim", "t", create=False)   # 새 엔진(로그에서 IFC 를 다시 만든다)
    try:
        h = eng.hashes()
        assert (h["state"], h["derived"], h["ifc"]) == h0
        assert eng.intent_consistency() == []
    finally:
        eng.close()
    b = S.Bench(tmp_path)
    try:
        b.export("e1.ifc", "src.dobim")
        _ok(b.import_ifc("e1.ifc", "imp.dobim"))
        assert S.trip(b.hashes("imp.dobim")) == h0
        # 세션이 달라도 정의 속성셋과 관계의 GlobalId 가 같다(프로젝트 UUID 에서 파생: 무작위면 ifcdiff 가 바뀐 것으로 센다)
        gid = lambda path: (lambda f: (f.by_id(_defs_pset(f)["id"]).GlobalId,   # noqa: E731
                                       [r.GlobalId for r in f.by_type("IfcRelDefinesByProperties") if r.RelatedObjects[0].is_a("IfcProject")]))(ifcopenshell.open(str(path)))
        assert gid(tmp_path / "e0.ifc") == gid(tmp_path / "e1.ifc")
    finally:
        b.close()
    eng = Engine(tmp_path / "imp.dobim", "t", create=False)   # 가져온 프로젝트를 다시 열어도 같다
    try:
        h = eng.hashes()
        assert (h["state"], h["derived"], h["ifc"]) == h0 and eng.intent_consistency() == []
    finally:
        eng.close()


def test_v1d_고아_주석도_왕복한다(tmp_path):
    """참조 요소를 지운 뒤(고아 치수·태그)의 프로젝트도 같은 상태로 되살아난다: 파일에 없는 대상은 저작 상태의 고아와 같다."""
    b = S.Bench(tmp_path)
    try:
        _build(b)
        _ok(b.run("element.delete", {"id": F.D1}, project="src.dobim"))      # T1 의 대상
        _ok(b.run("element.delete", {"id": F.W2}, project="src.dobim"))      # A3·T2 의 대상, A2 는 레벨이라 그대로
        before = _ok(b.run("annot.check", {}, project="src.dobim"))
        assert before["orphan"], before
        h0 = S.trip(b.hashes("src.dobim"))
        b.export("e1.ifc", "src.dobim")
        r = b.import_ifc("e1.ifc", "imp.dobim")
        assert r["ok"] and r["result"]["warnings"] == [], r
        assert S.trip(b.hashes("imp.dobim")) == h0
        assert _ok(b.run("annot.check", {}, project="imp.dobim")) == before
        assert _ids(b, "imp.dobim", "dim", "tag") == {A1, A2, A3, T1, T2}
        _ok(b.run("annot.rebind", {"id": T1, "target": F.W1, "fields": ["name"]}, project="imp.dobim"))   # 고아를 다시 묶을 수 있다
    finally:
        b.close()


# ================================================================ 2. 왕복 뒤 같은 편집열은 저작 프로젝트와 같은 해시
def test_v2_왕복_뒤_같은_편집은_저작_프로젝트와_해시가_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        b.export("e1.ifc", "src.dobim")
        guid = b.env.engine("src.dobim").project_guid_uuid
        _ok(b.import_ifc("e1.ifc", "imp.dobim", guid=guid))
    finally:
        b.close()
    shutil.copyfile(tmp_path / "src.dobim", tmp_path / "ref.dobim")
    N1, N2, N3 = "4" + "0" * 20 + "9", "5" + "0" * 20 + "9", "5" + "0" * 20 + "8"
    b = S.Bench(tmp_path)
    try:
        def both(name, args):
            ra, rb = b.run(name, args, project="ref.dobim"), b.run(name, args, project="imp.dobim")
            assert ra["ok"] and rb["ok"], (name, ra.get("error"), rb.get("error"))
            assert S.trip(b.hashes("ref.dobim")) == S.trip(b.hashes("imp.dobim")), name

        both("view.set", {"id": V1, "cut_height": 1500, "scale": 100})
        both("view.set", {"id": VS, "p2": [6000, 3000], "flip": False})
        both("view.create", {"id": N1, "name": "새 평면", "type": "plan", "level": F.LV1})
        both("annot.dim.create", {"id": N2, "view": N1, "a": R("wall", F.W3, "axis.start"), "b": R("wall", F.W3, "axis.end")})
        both("annot.tag.create", {"id": N3, "view": N1, "target": F.W1})
        both("wall.move", {"id": F.W1, "dx": 100})                                   # 치수·태그가 따라간다
        both("wall.set", {"id": F.W2, "thickness": 250})
        both("wall.split", {"id": F.W2, "at": 1500, "new_id": "3" + "0" * 21})       # 분할 이력으로 끝 참조(A3)가 이어진다
        both("annot.rebind", {"id": A1, "b": R("wall", F.W1, "axis.end")})
        both("element.delete", {"id": F.D1})                                          # T1 이 고아가 된다
        both("history.undo", {})
        both("history.undo", {})
        both("history.undo", {})
        both("history.redo", {})
        both("annot.delete", {"id": A3})
        both("view.delete", {"id": V1})                                               # 뷰와 딸린 주석이 함께 지워진다
        both("history.undo", {})
        both("view.delete", {"id": N1})
        both("history.undo", {})
        both("history.redo", {})
        both("element.delete", {"id": F.W1})                                          # 벽 삭제: 주석 고아
        both("history.undo", {})
        for pr in ("ref.dobim", "imp.dobim"):
            assert _ok(b.run("model.validate", {}, project=pr))["errors"] == 0
            assert b.env.engine(pr).intent_consistency() == []
        e_ref, e_imp = b.export("eref.ifc", "ref.dobim"), b.export("eimp.ifc", "imp.dobim")
        assert ifcdiff_check.strict_diff(str(e_ref), str(e_imp))["total"] == 0
    finally:
        b.close()
    ref, imp = (Engine(tmp_path / n, "t", create=False) for n in ("ref.dobim", "imp.dobim"))   # 다시 열어도 같다
    try:
        hr, hi = ref.hashes(), imp.hashes()
        assert (hr["state"], hr["derived"], hr["ifc"]) == (hi["state"], hi["derived"], hi["ifc"])
        assert ref.intent_consistency() == [] and imp.intent_consistency() == []
    finally:
        ref.close()
        imp.close()


def test_v2b_가져온_뒤_지운_뷰는_다시_내보낼_때_사라지고_재가져오기가_같다(tmp_path):
    """가져온 정의를 지우면 속성도 지운다(마지막 정의면 속성셋도 사라져 정의 없는 프로젝트와 같은 IFC). 재가져오기가 같은 상태다."""
    b = S.Bench(tmp_path)
    try:
        _build(b)
        b.export("e1.ifc", "src.dobim")
        _ok(b.import_ifc("e1.ifc", "imp.dobim", guid=F.GUID))
        for v in (V1, V2, VS, VE):
            _ok(b.run("view.delete", {"id": v}, project="imp.dobim"))
        assert _ids(b, "imp.dobim", "view", "dim", "tag") == set()
        e2 = b.export("e2.ifc", "imp.dobim")
        assert _defs_pset(ifcopenshell.open(str(e2))) is None
        # 처음부터 뷰가 없던 프로젝트의 내보내기와 의미가 같다(속성셋이 남지 않았다)
        S.build_authored(b, "plain.dobim")
        _ok(b.run("family.place", {"id": FD, "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 4500}, project="plain.dobim"))
        e3 = b.export("e3.ifc", "plain.dobim")
        assert ifcdiff_check.strict_diff(str(e2), str(e3))["total"] == 0
        assert S.trip(b.hashes("imp.dobim")) == S.trip(b.hashes("plain.dobim"))
        _ok(b.import_ifc("e2.ifc", "again.dobim", guid=F.GUID))
        assert S.trip(b.hashes("again.dobim")) == S.trip(b.hashes("imp.dobim"))
    finally:
        b.close()


# ================================================================ 3. 병합
def test_v3_뷰_주석이_있는_가져온_프로젝트의_두_복사본이_병합되고_결과가_순차_적용과_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        b.export("e1.ifc", "src.dobim")
        _ok(b.import_ifc("e1.ifc", "anc.dobim", guid=F.GUID))
    finally:
        b.close()
    for n in ("a.dobim", "b.dobim", "ref.dobim"):
        shutil.copyfile(tmp_path / "anc.dobim", tmp_path / n)
    NA, NB, DA, TB = "4" + "0" * 20 + "a", "4" + "0" * 20 + "b", "5" + "0" * 20 + "a", "5" + "0" * 20 + "b"
    edit_a = [("view.create", {"id": NA, "name": "A 가지 평면", "type": "plan", "level": F.LV1}),
              ("annot.dim.create", {"id": DA, "view": NA, "a": R("wall", F.W1, "axis.start"), "b": R("wall", F.W1, "axis.end")}),
              ("view.set", {"id": V1, "cut_height": 900})]
    edit_b = [("view.create", {"id": NB, "name": "B 가지 입면", "type": "elevation", "dir": "N"}),
              ("annot.tag.create", {"id": TB, "view": V1, "target": F.W3}),
              ("annot.delete", {"id": A3})]
    b = S.Bench(tmp_path)
    try:
        for n, a in edit_a:
            _ok(b.run(n, a, project="a.dobim"))
            _ok(b.run(n, a, project="ref.dobim"))
        for n, a in edit_b:
            _ok(b.run(n, a, project="b.dobim"))
            _ok(b.run(n, a, project="ref.dobim"))
        prev = b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim")
        assert prev["ok"] and prev["result"]["conflict_count"] == 0, prev
        r = b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")
        assert r["ok"], r
        assert S.trip(b.hashes("a.dobim")) == S.trip(b.hashes("ref.dobim"))
        assert _ok(b.run("model.validate", {}, project="a.dobim"))["errors"] == 0
        assert b.env.engine("a.dobim").intent_consistency() == []
        e = b.export("m.ifc", "a.dobim")
        assert set(_def_props(ifcopenshell.open(str(e)))) == (ALL_DEFS | {NA, NB, DA, TB}) - {A3}   # 병합 결과의 정의가 IFC 에 실린다
        _ok(b.run("history.undo", {}, project="a.dobim"))
        assert NB not in _ids(b, "a.dobim", "view")
    finally:
        b.close()


# ================================================================ 4. 변조
def _move_level(f):
    loc = f.by_guid(F.LV1).ObjectPlacement.RelativePlacement.Location
    loc.Coordinates = (loc.Coordinates[0], loc.Coordinates[1], loc.Coordinates[2] + 100.0)
    f.by_guid(F.LV1).Elevation = f.by_guid(F.LV1).Elevation + 100.0


def _move_wall(f, gid=F.W1, dx=300.0):
    loc = f.by_guid(gid).ObjectPlacement.RelativePlacement.Location
    loc.Coordinates = (loc.Coordinates[0] + dx, loc.Coordinates[1], loc.Coordinates[2])


def _pset_text(f, eid, text):
    _def_props(f)[eid].NominalValue = f.createIfcText(text)


def _intent_pset_dup(f):
    """같은 이름의 속성셋을 하나 더 만든다(모호). add_pset 은 같은 이름이면 기존 것을 돌려주므로 엔티티를 직접 만든다."""
    proj = f.by_type("IfcProject")[0]
    ps = f.create_entity("IfcPropertySet", GlobalId=ifcopenshell.guid.new(), Name="DoBIM_Defs",
                         HasProperties=[f.create_entity("IfcPropertySingleValue", Name="Z" * 22, NominalValue=f.createIfcText("{}"))])
    f.create_entity("IfcRelDefinesByProperties", GlobalId=ifcopenshell.guid.new(), RelatedObjects=[proj], RelatingPropertyDefinition=ps)


def _dup_prop(f):
    ps = f.by_id(_defs_pset(f)["id"])
    p = _def_props(f)[V2]
    q = f.create_entity("IfcPropertySingleValue", Name=V2, NominalValue=f.createIfcText(p.NominalValue.wrappedValue))
    ps.HasProperties = list(ps.HasProperties) + [q]


def _add_def(f, eid, text):
    ps = f.by_id(_defs_pset(f)["id"])
    q = f.create_entity("IfcPropertySingleValue", Name=eid, NominalValue=f.createIfcText(text))
    ps.HasProperties = list(ps.HasProperties) + [q]


def _clone(f, src, new, fn=None, *, fix=True):
    d = _get(f, src)
    if fn:
        fn(d)
    if fix:
        d["check"] = VD.check_of(new, d["kind"], d["params"])
    _add_def(f, new, json.dumps(d))


def _ref_swap_nofix(f):
    _edit(f, A1, lambda d: d["params"].update(b=R("wall", F.W3, "trim.end")), fix=False)


def _nan_forged(f):
    """해시는 cut_height 0(정수)에 맞추고 텍스트는 NaN 으로 바꾼다(NaN 을 0 으로 읽는 구현이면 해시까지 맞아 복원돼 버린다)."""
    _edit(f, V1, lambda d: d["params"].update(cut_height=0))
    t = _def_props(f)[V1].NominalValue.wrappedValue
    assert '"cut_height": 0,' in t
    _pset_text(f, V1, t.replace('"cut_height": 0,', '"cut_height": NaN,'))


def _padded(f):
    """JSON 으로 멀쩡하고 해시도 맞지만 텍스트 크기가 상한을 넘는다(공백 2만 자)."""
    _pset_text(f, VE, _def_props(f)[VE].NominalValue.wrappedValue + " " * 20_000)


def _bad_ref_kind(f):   # 'wall:' 접두사인데 id 는 문이다
    _edit(f, A1, lambda d: d["params"].update(b=R("wall", F.D1, "trim.end")))


# (이름, 변조, 되살려지지 않아야 하는 id 집합). 그 밖의 정의는 모두 복원돼야 한다.
CASES = [
    ("없음", lambda f: None, set()),
    # --- 정의 값 변조(해시를 안 고침: 변조 탐지)
    ("치수_위치만_바꿈", lambda f: _edit(f, A1, lambda d: d["params"].update(home=[1.0, 2.0]), fix=False), {A1}),
    ("태그_위치만_바꿈", lambda f: _edit(f, T1, lambda d: d["params"].update(off=[9.0, 9.0]), fix=False), {T1}),
    ("뷰_정의만_바꿈", lambda f: _edit(f, V1, lambda d: d["params"].update(cut_height=5000.0), fix=False), {V1, A1, A3, T1, T2}),
    ("참조_id_바꿔치기_해시_그대로", _ref_swap_nofix, {A1}),
    ("해시를_지움", lambda f: _edit(f, A1, lambda d: d.update(check="0" * 64), fix=False), {A1}),
    # --- 모양·범위 위반(해시까지 맞춘 위조도 검증에서 걸린다)
    ("JSON_아님", lambda f: _pset_text(f, A1, "{not json"), {A1}),
    ("NaN", lambda f: _pset_text(f, V1, json.dumps(_get(f, V1)).replace('"cut_height": 1200.0', '"cut_height": NaN')), {V1, A1, A3, T1, T2}),
    ("거대_정수", lambda f: _pset_text(f, V2, json.dumps(_get(f, V2)).replace('"cut_height": 1200.0', '"cut_height": ' + "9" * 5000)), {V2}),
    ("거대_텍스트", lambda f: _pset_text(f, VE, "x" * 400_000), {VE}),
    ("깊은_중첩", lambda f: _pset_text(f, VE, "[" * 200_000 + "]" * 200_000), {VE}),
    ("키_누락", lambda f: _edit(f, VE, lambda d: d.pop("family_id")), {VE}),
    ("키_추가", lambda f: _edit(f, VE, lambda d: d.update(extra=1)), {VE}),
    ("family_id_틀림", lambda f: _edit(f, VE, lambda d: d.update(family_id="annot.dim")), {VE}),
    ("종류_바꿈", lambda f: _edit(f, VE, lambda d: d.update(kind="dim")), {VE}),
    ("거대_절단높이", lambda f: _edit(f, V2, lambda d: d["params"].update(cut_height=1e300)), {V2}),
    ("범위_밖_절단높이", lambda f: _edit(f, V2, lambda d: d["params"].update(cut_height=1e9)), {V2}),
    ("축척_0", lambda f: _edit(f, VE, lambda d: d["params"].update(scale=0)), {VE}),
    ("축척_실수", lambda f: _edit(f, VE, lambda d: d["params"].update(scale=100.5)), {VE}),
    ("방위_틀림", lambda f: _edit(f, VE, lambda d: d["params"].update(dir="X")), {VE}),
    ("절단선_길이_0", lambda f: _edit(f, VS, lambda d: d["params"].update(p2=d["params"]["p1"])), {VS, A2}),
    ("이름_너무_김", lambda f: _edit(f, VE, lambda d: d["params"].update(name="가" * 65)), {VE}),
    ("이름에_NUL", lambda f: _edit(f, VE, lambda d: d["params"].update(name="a\x00b")), {VE}),
    ("이름에_짝_없는_서로게이트", lambda f: _edit(f, VE, lambda d: d["params"].update(name="a\ud800b")), {VE}),
    ("평면뷰의_레벨이_벽_id", lambda f: _edit(f, V2, lambda d: d["params"].update(level=F.W1)), {V2}),
    ("참조_문법_틀림", lambda f: _edit(f, A1, lambda d: d["params"].update(a="wall:abc#trim.start")), {A1}),
    ("없는_참조_역할", lambda f: _edit(f, A1, lambda d: d["params"].update(a=R("wall", F.W1, "nope"))), {A1}),
    ("거대_치수선_간격", lambda f: _edit(f, A1, lambda d: d["params"].update(off=1e300)), {A1}),
    ("치수_home_범위_밖", lambda f: _edit(f, A1, lambda d: d["params"].update(home=[1e12, 0.0])), {A1}),
    ("태그_필드_없음", lambda f: _edit(f, T1, lambda d: d["params"].update(fields=["thickness"])), {T1}),
    ("태그_필드_5개", lambda f: _edit(f, T1, lambda d: d["params"].update(fields=["name"] * 5)), {T1}),
    ("높이치수에_at_없음", lambda f: _edit(f, A2, lambda d: d["params"].pop("at")), {A2}),
    ("평면치수에_at", lambda f: _edit(f, A1, lambda d: d["params"].update(at=[0.0, 0.0])), {A1}),
    ("치수가_단면뷰에", lambda f: _edit(f, A1, lambda d: d["params"].update(view=VS)), {A1}),
    ("태그가_단면뷰에", lambda f: _edit(f, T1, lambda d: d["params"].update(view=VS)), {T1}),
    ("지문_모양_틀림", lambda f: _edit(f, A1, lambda d: d["params"].update(dirs={"a": [5.0, 5.0], "b": None})), {A1}),
    # --- 참조 무결성
    ("참조_종류_틀림", _bad_ref_kind, {A1}),
    ("없는_뷰를_가리키는_주석", lambda f: _edit(f, A1, lambda d: d["params"].update(view="Z" * 22)), {A1}),
    ("없는_태그_대상은_고아로_복원", lambda f: _edit(f, T2, lambda d: d["params"].update(target="Z" * 22)), set()),
    ("없는_치수_대상은_고아로_복원", lambda f: _edit(f, A3, lambda d: d["params"].update(b=R("wall", "Y" * 22, "axis.end"))), set()),
    ("없는_레벨의_평면뷰는_고아_뷰로_복원", lambda f: _edit(f, V2, lambda d: d["params"].update(level="X" * 22)), set()),
    ("참조_바꿔치기_해시도_맞춤", lambda f: _edit(f, A1, lambda d: d["params"].update(b=R("wall", F.W3, "trim.end"))), set()),   # 다른 저작 벽: 정의 전체를 맞게 다시 쓴 위조는 막지 못한다(ADR-0015)
    # --- 참조 대상이 같은 파일의 다른 정의 id 인 위조(해시를 맞춰도 종류 검사가 막아야 한다: 정의 id 는 요소 종류가 아니다)
    ("태그_대상이_뷰_id", lambda f: _edit(f, T1, lambda d: d["params"].update(target=V1)), {T1}),
    ("태그_대상이_치수_id", lambda f: _edit(f, T1, lambda d: d["params"].update(target=A1)), {T1}),
    ("태그_대상이_다른_태그_id", lambda f: _edit(f, T1, lambda d: d["params"].update(target=T2)), {T1}),
    ("태그_대상이_자기_id", lambda f: _edit(f, T2, lambda d: d["params"].update(target=T2)), {T2}),
    ("치수_참조가_뷰_id", lambda f: _edit(f, A1, lambda d: d["params"].update(a=R("wall", V1, "axis.start"))), {A1}),
    ("치수_참조가_태그_id를_레벨로", lambda f: _edit(f, A2, lambda d: d["params"].update(a=R("level", T1, "elevation"))), {A2}),
    ("치수_참조가_태그_id를_벽으로", lambda f: _edit(f, A3, lambda d: d["params"].update(a=R("wall", T1, "axis.start"))), {A3}),
    ("치수_자기_참조", lambda f: _edit(f, A1, lambda d: d["params"].update(b=R("wall", A1, "axis.end"))), {A1}),
    ("평면뷰의_레벨이_다른_뷰_id", lambda f: _edit(f, V1, lambda d: d["params"].update(level=V2)), {V1, A1, A3, T1, T2}),
    ("평면뷰의_레벨이_치수_id", lambda f: _edit(f, V1, lambda d: d["params"].update(level=A1)), {V1, A1, A3, T1, T2}),
    ("평면뷰의_레벨이_자기_id", lambda f: _edit(f, V1, lambda d: d["params"].update(level=V1)), {V1, A1, A3, T1, T2}),
    # --- id 중복
    # --- 이름 중복
    ("같은_이름_뷰_둘", lambda f: _clone(f, V2, "4" + "0" * 20 + "7"), {V2, "4" + "0" * 20 + "7"}),
    # --- 해시를 맞춘 위조(검증 단계 자체가 막아야 한다)
    ("NaN_해시는_맞춤", _nan_forged, {V1, A1, A3, T1, T2}),
    ("크기_상한_넘는_멀쩡한_정의", _padded, {VE}),
    ("다른_레벨의_벽을_재는_치수", lambda f: _clone(f, A1, "6" + "0" * 20 + "4", lambda d: d["params"].update(view=V2)), {"6" + "0" * 20 + "4"}),
    ("다른_레벨의_문에_다는_태그", lambda f: _clone(f, T1, "6" + "0" * 20 + "5", lambda d: d["params"].update(view=V2)), {"6" + "0" * 20 + "5"}),
    ("다른_id_밑에_옮겨_붙인_정의", lambda f: _clone(f, A3, "6" + "0" * 20 + "3", None, fix=False), {"6" + "0" * 20 + "3"}),   # 해시가 id 에 묶여 있다
]


@pytest.mark.parametrize("name,mutate,skipped", CASES, ids=[f"case{i:02d}_{c[0]}" for i, c in enumerate(CASES)])   # 번호 접두사: pytest 가 한글 id 를 이스케이프해 -k 로 못 고르므로
def test_v4_변조된_정의만_되살리지_않고_가져오기는_죽지_않는다(tmp_path, name, mutate, skipped):
    b, res = _tamper(tmp_path, mutate)
    try:
        got = _ids(b, "imp.dobim", "view", "dim", "tag")
        want = ALL_DEFS - skipped
        assert got == want, (name, sorted(got ^ want))
        assert not (skipped & got), (name, skipped & got)
        # 오류로 죽지 않고, 복원된 것의 판정은 모두 authored 이며, 다시 열어도 같고, 계속 편집된다
        assert res["elements"]["detached"] == 0 and res["elements"]["foreign"] == 0
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0 or name == "없는_레벨의_평면뷰는_고아_뷰로_복원"
        assert b.env.engine("imp.dobim").intent_consistency() == []
        if skipped:
            assert any("뷰·주석 정의를 되살리지 않는다" in w for w in res["warnings"]), res["warnings"]
        else:
            assert res["warnings"] == [], res["warnings"]
        _ok(b.run("view.create", {"name": "복원 뒤 새 뷰", "type": "elevation", "dir": "E"}, project="imp.dobim"))
        h = S.trip(b.hashes("imp.dobim"))
        # 되살리지 않은 정의는 파일에 그대로 남는다(내보낼 때 지우지 않는다)
        e = b.export("e2.ifc", "imp.dobim")
        props = _def_props(ifcopenshell.open(str(e)))
        for eid in skipped:
            assert eid in props, (name, eid)
    finally:
        b.close()
    eng = Engine(tmp_path / "imp.dobim", "t", create=False)
    try:
        hh = eng.hashes()
        assert (hh["state"], hh["derived"], hh["ifc"]) == h
    finally:
        eng.close()


def test_v4b_같은_이름_속성이_둘이면_그_id_만_빠지고_속성셋이_둘이면_하나도_읽지_않는다(tmp_path):
    b, res = _tamper(tmp_path / "a", _dup_prop)
    try:
        assert _ids(b, "imp.dobim", "view", "dim", "tag") == ALL_DEFS - {V2}
        assert any("둘 이상" in w for w in res["warnings"])
    finally:
        b.close()
    b, res = _tamper(tmp_path / "b", _intent_pset_dup)   # 빈 속성셋이 하나 더 있어도 모호하다
    try:
        assert _ids(b, "imp.dobim", "view", "dim", "tag") == set()
        assert any("DoBIM_Defs" in w for w in res["warnings"])
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
    finally:
        b.close()


def test_v4c_정의_id가_타입_id와_겹쳐도_가져오기는_죽지_않고_그_정의만_빠진다(tmp_path):
    holder = {}

    def mut(f):
        tid = ifcopenshell.util.element.get_type(f.by_guid(FD)).GlobalId
        holder["t"] = tid
        _clone(f, V2, tid, lambda d: d["params"].update(name="타입 id 복제"))
    b, res = _tamper(tmp_path, mut)
    try:
        assert _ids(b, "imp.dobim", "view", "dim", "tag") == ALL_DEFS
        assert any("겹친다" in w for w in res["warnings"])
        assert holder["t"] in _def_props(ifcopenshell.open(str(b.export("e2.ifc", "imp.dobim"))))   # 파일에 남는다
    finally:
        b.close()


def test_v4d_정의_id가_벽_id와_겹치면_그_정의만_빠지고_벽은_저작으로_남는다(tmp_path):
    b, res = _tamper(tmp_path, lambda f: _clone(f, V2, F.W1, lambda d: d["params"].update(name="벽 id 복제")))
    try:
        assert _ids(b, "imp.dobim", "view", "dim", "tag") == ALL_DEFS
        assert F.W1 not in _origins(b, "imp.dobim")
        assert _ok(b.run("element.get", {"id": F.W1}, project="imp.dobim"))["kind"] == "wall"
    finally:
        b.close()


def test_v4e_참조_요소가_분리되면_그_요소를_재는_치수와_다는_태그만_복원하지_않는다(tmp_path):
    """벽 W1 의 배치를 바꿔 W1 이 분리되면 W1 의 양 끝을 재는 치수 A1 은 낡은 값을 재게 되므로 복원하지 않는다. 문 D1 은 벽을 따라가 저작이다."""
    tag_w1 = "6" + "0" * 20 + "6"

    def mut(f):
        _clone(f, T2, tag_w1, lambda d: d["params"].update(target=F.W1))   # 벽 W1 에 다는 태그(W1 은 같은 레벨이라 레벨 검사는 통과한다)
        _move_wall(f)
    b, res = _tamper(tmp_path, mut)
    try:
        org = _origins(b, "imp.dobim")
        assert org.get(F.W1) == "detached"
        assert _ids(b, "imp.dobim", "view", "dim", "tag") == ALL_DEFS - {A1}          # A1 과 W1 의 태그가 빠진다
        assert any("치수 참조" in w for w in res["warnings"]) and any("태그 대상" in w for w in res["warnings"])
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
        assert A1 in _def_props(ifcopenshell.open(str(b.export("e2.ifc", "imp.dobim"))))
    finally:
        b.close()


def test_v4f_뷰가_가리키는_레벨이_분리되면_그_뷰와_딸린_주석과_그_레벨을_재는_치수를_복원하지_않는다(tmp_path):
    b, res = _tamper(tmp_path, _move_level)
    try:
        assert _origins(b, "imp.dobim").get(F.LV1) == "detached"
        got = _ids(b, "imp.dobim", "view", "dim", "tag")
        # 1F 평면 뷰 V1 이 빠지고 그 뷰의 주석(A1·A3·T1·T2)도 함께 빠진다. 높이 치수 A2 는 분리된 레벨 LV1 의 고도를 재므로 빠진다
        assert got == {V2, VS, VE}, got
        assert any("레벨" in w for w in res["warnings"])
        props = _def_props(ifcopenshell.open(str(b.export("e2.ifc", "imp.dobim"))))
        assert {V1, A1, A2, A3, T1, T2} <= set(props)                     # 정의는 파일에 남는다
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
    finally:
        b.close()


def test_v4h_길이_단위가_밀리미터가_아니면_정의도_되살리지_않는다(tmp_path):
    def mut(f):
        for u in f.by_type("IfcSIUnit"):
            if u.UnitType == "LENGTHUNIT":
                u.Prefix = None
    b, res = _tamper(tmp_path, mut)
    try:
        assert _ids(b, "imp.dobim", "view", "dim", "tag") == set()
        assert any("단위" in w and "정의" in w for w in res["warnings"])
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
    finally:
        b.close()


def test_v4i_외래_요소를_재는_치수와_태그는_복원하지_않는다(tmp_path):
    """정의가 가리키는 대상이 파일에는 있지만 코어가 저작으로 알아보지 못한 외래 벽이면 낡은 값이 아니라 아예 형상을 모른다: 복원하지 않는다."""
    import ifcopenshell.api.root as api_root
    import ifcopenshell.api.spatial as api_spatial
    fw = "F" * 22

    def mut(f):
        w = api_root.create_entity(f, ifc_class="IfcWall", name="외래벽")
        w.GlobalId = fw
        api_spatial.assign_container(f, products=[w], relating_structure=f.by_guid(F.LV1))
        _clone(f, A1, "6" + "0" * 20 + "1", lambda d: d["params"].update(a=R("wall", fw, "axis.start"), b=R("wall", fw, "axis.end")))
        _clone(f, T2, "6" + "0" * 20 + "2", lambda d: d["params"].update(target=fw, fields=["name"]))
    b, res = _tamper(tmp_path, mut)
    try:
        assert _origins(b, "imp.dobim").get(fw) == "foreign"
        assert _ids(b, "imp.dobim", "view", "dim", "tag") == ALL_DEFS
        assert sum("뷰·주석 정의를 되살리지 않는다" in w for w in res["warnings"]) == 2
    finally:
        b.close()


def test_v4j_파일에서_참조_요소가_사라지면_고아_주석으로_복원한다(tmp_path):
    """코어가 아닌 도구가 문 D1(과 그 개구부)을 지웠다: 태그 T1 의 대상이 없다. 저작 상태에서 대상을 지운 뒤와 같은 고아로 되살아난다."""
    import ifcopenshell.api.feature as api_feature
    import ifcopenshell.api.root as api_root

    def mut(f):
        e = f.by_guid(F.D1)
        op = [r.RelatingOpeningElement for r in f.get_inverse(e) if r.is_a("IfcRelFillsElement")][0]
        api_root.remove_product(f, product=e)
        api_feature.remove_feature(f, feature=op)
    b, res = _tamper(tmp_path, mut)
    try:
        assert _ids(b, "imp.dobim", "view", "dim", "tag") == ALL_DEFS and res["warnings"] == [], res["warnings"]
        chk = _ok(b.run("annot.check", {}, project="imp.dobim"))
        assert chk["orphan"] == {"target_deleted": 1}, chk
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
    finally:
        b.close()


def test_v4k_정의_개수_상한을_넘으면_나머지는_읽지_않고_경고한다(tmp_path, monkeypatch):
    monkeypatch.setattr(VD, "MAX_DEFS", 4)
    b, res = _tamper(tmp_path, lambda f: None)
    try:
        got = _ids(b, "imp.dobim", "view", "dim", "tag")
        assert len(got) <= 4 and any("넘어" in w for w in res["warnings"]), (got, res["warnings"])
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
        assert set(_def_props(ifcopenshell.open(str(b.export("e2.ifc", "imp.dobim"))))) == ALL_DEFS   # 읽지 않은 정의는 파일에 남는다
    finally:
        b.close()


# ================================================================ 5. 호환
def test_v5_뷰_주석이_없는_프로젝트의_IFC는_바뀌지_않는다(tmp_path):
    """뷰를 만들었다 지워도(되돌려도) IFC 는 뷰가 없던 때와 바이트까지 같다: 속성셋을 남기지 않는다."""
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "a.dobim")
        S.build_authored(b, "b.dobim")
        b.export("a.ifc", "a.dobim")
        h = S.trip(b.hashes("a.dobim"))
        _ok(b.run("view.create", {"id": V1, "name": "v", "type": "plan", "level": F.LV1}, project="b.dobim"))
        assert S.trip(b.hashes("b.dobim")) != h                                   # 뷰가 있으면 IFC 가 바뀐다
        _ok(b.run("annot.dim.create", {"id": A1, "view": V1, "a": R("wall", F.W1, "axis.start"), "b": R("wall", F.W1, "axis.end")}, project="b.dobim"))
        _ok(b.run("view.delete", {"id": V1}, project="b.dobim"))
        assert S.trip(b.hashes("b.dobim")) == h                                   # 지우면 원래대로
        _ok(b.run("history.undo", {}, project="b.dobim"))                         # 삭제를 되돌림: 뷰와 치수가 돌아온다
        assert S.trip(b.hashes("b.dobim")) != h
        _ok(b.run("history.undo", {}, project="b.dobim"))                         # 치수 생성을 되돌림: 뷰만 남는다
        assert S.trip(b.hashes("b.dobim")) != h
        _ok(b.run("history.undo", {}, project="b.dobim"))                         # 뷰 생성까지 되돌림
        assert S.trip(b.hashes("b.dobim")) == h
        e = b.export("b.ifc", "b.dobim")
        f = ifcopenshell.open(str(e))
        assert _defs_pset(f) is None
    finally:
        b.close()
    ga, gb = ifcopenshell.open(str(tmp_path / "a.ifc")), ifcopenshell.open(str(tmp_path / "b.ifc"))
    assert ifcdiff_check.strict_diff(str(tmp_path / "a.ifc"), str(tmp_path / "b.ifc"))["total"] == 0
    assert len(ga.by_type("IfcPropertySet")) == len(gb.by_type("IfcPropertySet"))


def test_v5b_옛_IFC_속성셋_없음를_가져온_뒤_뷰를_더하면_내보내고_다시_가져와_같다(tmp_path):
    """이 변경 전에 내보낸 파일(정의 속성셋이 없다)을 가져온 프로젝트: 뷰를 새로 만들면 정의가 실리고, 재가져오기가 같은 상태다."""
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "src.dobim")
        b.export("old.ifc", "src.dobim")
        assert _defs_pset(ifcopenshell.open(str(tmp_path / "old.ifc"))) is None
        _ok(b.import_ifc("old.ifc", "imp.dobim", guid=F.GUID))
        for n, a in [("view.create", {"id": V1, "name": "1F", "type": "plan", "level": F.LV1}),
                     ("annot.dim.create", {"id": A1, "view": V1, "a": R("wall", F.W1, "axis.start"), "b": R("wall", F.W1, "axis.end")}),
                     ("annot.tag.create", {"id": T1, "view": V1, "target": F.D1})]:
            _ok(b.run(n, a, project="imp.dobim"))
        h = S.trip(b.hashes("imp.dobim"))
        e = b.export("new.ifc", "imp.dobim")
        assert set(_def_props(ifcopenshell.open(str(e)))) == {V1, A1, T1}
        r = b.import_ifc("new.ifc", "again.dobim", guid=F.GUID)
        assert r["ok"] and r["result"]["elements"]["foreign"] == 0 and r["result"]["warnings"] == []
        assert S.trip(b.hashes("again.dobim")) == h
    finally:
        b.close()
    eng = Engine(tmp_path / "imp.dobim", "t", create=False)   # 정의 없는 파일에서 가져온 프로젝트를 다시 열어도(새 엔진) 같다
    try:
        hh = eng.hashes()
        assert (hh["state"], hh["derived"], hh["ifc"]) == h and eng.intent_consistency() == []
    finally:
        eng.close()


def test_v5c_이_변경_전에_만든_프로젝트는_그대로_열리고_뷰_정의를_내보낸다(tmp_path):
    """뷰·주석이 있는 옛 프로젝트(.dobim 에는 정의 속성이 따로 없다: 정의는 항상 의도에서 파생한다)는 열고 내보내면 정의가 실린다.
    해시 3종은 같은 로그에서 다시 만든 것과 같다."""
    b = S.Bench(tmp_path)
    try:
        _build(b)
        h = S.trip(b.hashes("src.dobim"))
    finally:
        b.close()
    con = __import__("sqlite3").connect(tmp_path / "src.dobim")   # 프로젝트 파일에 정의용 테이블·메타가 없다(옛 파일과 같은 구조)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert names == {"meta", "element", "relation", "op_log", "foreign_base", "import_spec"}
    b = S.Bench(tmp_path)
    try:
        assert S.trip(b.hashes("src.dobim")) == h
        assert set(_def_props(ifcopenshell.open(str(b.export("e1.ifc", "src.dobim"))))) == ALL_DEFS
    finally:
        b.close()


def test_v5d_되살리지_않은_외부_속성과_손상_정의는_가져온_뒤_내보내도_남는다(tmp_path):
    def mut(f):
        _add_def(f, "외부도구가-넣은-속성", "그냥 글자")          # 22자 id 가 아닌 속성
        _add_def(f, "Q" * 22, "{broken")                        # 손상 정의
    b, res = _tamper(tmp_path, mut)
    try:
        assert _ids(b, "imp.dobim", "view", "dim", "tag") == ALL_DEFS
        _ok(b.run("view.delete", {"id": V2}, project="imp.dobim"))      # 지운 것은 사라지고
        e = b.export("e2.ifc", "imp.dobim")
        props = _def_props(ifcopenshell.open(str(e)))
        assert "외부도구가-넣은-속성" in props and "Q" * 22 in props      # 남의 것은 남는다
        assert V2 not in props
        assert props["Q" * 22].NominalValue.wrappedValue == "{broken"
    finally:
        b.close()


def test_v5e_정의_속성이_IfcLabel이거나_숫자형이어도_죽지_않는다(tmp_path):
    def mut(f):
        p = _def_props(f)[V2]
        p.NominalValue = f.createIfcLabel(p.NominalValue.wrappedValue)       # 문자열 형식만 다르다: 읽힌다
        _def_props(f)[VE].NominalValue = f.createIfcReal(1.5)                # 문자열이 아니다
        _def_props(f)[VS].NominalValue = None
    b, res = _tamper(tmp_path, mut)
    try:
        assert _ids(b, "imp.dobim", "view") == {V1, V2}
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
    finally:
        b.close()


# ================================================================ 6. 규모
def _import_work_views(tmp_path, n: int) -> dict:
    """뷰 n개와 주석 n개가 있는 프로젝트를 가져올 때 ifcopenshell 에서 쓰는 일의 양."""
    from ifcopenshell.entity_instance import entity_instance_mixin as M
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "src.dobim")
        for k in range(n):
            vid = "4" + "0" * 12 + "%09d" % k
            _ok(b.run("view.create", {"id": vid, "name": f"v{k}", "type": "plan", "level": F.LV1}, project="src.dobim"))
            _ok(b.run("annot.dim.create", {"id": "5" + "0" * 12 + "%09d" % k, "view": vid, "a": R("wall", F.W1, "axis.start"),
                                           "b": R("wall", F.W1, "axis.end")}, project="src.dobim"))
        b.export("e1.ifc", "src.dobim")
        work = {"eq": 0, "inv": 0}
        orig_eq, orig_inv = M.is_entity, ifcopenshell.file.get_inverse

        def eq(self, *a, **k):
            work["eq"] += 1
            return orig_eq(self, *a, **k)

        def inv(self, inst, *a, **k):
            r = orig_inv(self, inst, *a, **k)
            work["inv"] += len(r)
            return r
        M.is_entity, ifcopenshell.file.get_inverse = eq, inv
        try:
            res = _ok(b.import_ifc("e1.ifc", "imp.dobim"))
        finally:
            M.is_entity, ifcopenshell.file.get_inverse = orig_eq, orig_inv
        assert res["elements"]["authored"] >= 2 * n and res["warnings"] == []
        return work
    finally:
        b.close()


def test_v6_뷰_주석이_많아도_가져오기_일의_양이_선형이다(tmp_path):
    small = _import_work_views(tmp_path / "a", 40)
    big = _import_work_views(tmp_path / "b", 80)
    for k in ("eq", "inv"):
        base = max(small[k], 1)
        assert big[k] / base < 2.6, (k, small, big)


def test_v6b_뷰_주석이_많아도_내보내기와_편집이_선형이다(tmp_path):
    """정의 속성을 하나 바꿀 때 전체를 다시 쓰지 않는다: 바뀐 정의만 쓴다."""
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "src.dobim")
        for k in range(30):
            _ok(b.run("view.create", {"id": "4" + "0" * 12 + "%09d" % k, "name": f"v{k}", "type": "plan", "level": F.LV1}, project="src.dobim"))
        eng = b.env.engine("src.dobim")
        eng.flush_ifc()
        import dobim_core.ifc_model as IM
        written = []
        orig = IM.api_pset.edit_pset

        def spy(f, pset, properties):   # 속성셋에 실제로 쓰는 속성 수
            written.append(len(properties))
            return orig(f, pset=pset, properties=properties)
        IM.api_pset.edit_pset = spy
        try:
            _ok(b.run("view.set", {"id": "4" + "0" * 12 + "%09d" % 3, "cut_height": 1111}, project="src.dobim"))
            eng.flush_ifc()
            eng.flush_ifc()   # 바뀐 게 없으면 아무것도 쓰지 않는다
        finally:
            IM.api_pset.edit_pset = orig
        assert written == [1], written
    finally:
        b.close()


# ================================================================ 7. 검수 보강: 정의 id 를 가리키는 참조·덮어쓰기·요소 보호
@pytest.mark.parametrize("name", ["tag_view", "dim_view", "dim_self", "plan_level_view", "plan_level_dim", "tag_tag"])
def test_v4l_정의_id를_가리키는_위조는_복원되지_않아_도면과_해시가_죽지_않는다(tmp_path, name):
    muts = {
        "tag_view": lambda f: _edit(f, T1, lambda d: d["params"].update(target=V1)),
        "dim_view": lambda f: _edit(f, A1, lambda d: d["params"].update(a=R("wall", V1, "axis.start"))),
        "dim_self": lambda f: _edit(f, A1, lambda d: d["params"].update(b=R("wall", A1, "axis.end"))),
        "plan_level_view": lambda f: _edit(f, V1, lambda d: d["params"].update(level=V2)),
        "plan_level_dim": lambda f: _edit(f, V1, lambda d: d["params"].update(level=A1)),
        "tag_tag": lambda f: _edit(f, T1, lambda d: d["params"].update(target=T2)),
    }
    b, res = _tamper(tmp_path, muts[name])
    try:
        # INTERNAL 오류 0: 가져온 직후 해시·검사·도면·내보내기가 결정적으로 끝난다
        for call, args in (("model.hash", {}), ("annot.check", {}), ("model.validate", {}), ("view.list", {}),
                           ("view.linework", {"id": V2})):
            r = b.run(call, args, project="imp.dobim")
            assert r["ok"], (name, call, r.get("error"))
        b.export("o2.ifc", "imp.dobim")
        assert any("뷰·주석 정의를 되살리지 않는다" in w for w in res["warnings"]), res["warnings"]
    finally:
        b.close()
    eng = Engine(tmp_path / "imp.dobim", "t", create=False)   # 상태가 SQLite 에 저장돼 있으니 다시 열어도 같아야 한다
    try:
        assert eng.hashes()["state"]
    finally:
        eng.close()


def test_v4m_저장된_상태에_잘못된_참조가_이미_있어도_도면과_주석_해석은_터지지_않는다(tmp_path):
    """이 수정 전 버전이 만든 프로젝트 방어: 종류가 어긋난 참조를 직접 심어도 결정적으로 고아·빈 뷰가 된다(INTERNAL 0)."""
    from dobim_core import annot as AN
    from dobim_core import drawing as DR
    from dobim_core.engine import Ctx

    b = S.Bench(tmp_path)
    try:
        _build(b)
        eng = b.env.engine("src.dobim")
        st = eng.st
        a1, v1, t1 = st.el[A1], st.el[V1], st.el[T1]
        st.apply_el(A1, a1, dict(a1, params=dict(a1["params"], a=R("wall", V1, "axis.start"))))       # 벽 참조가 뷰를 가리킨다
        st.apply_el(T1, t1, dict(t1, params=dict(t1["params"], target=V1)))                           # 태그 대상이 뷰다
        st.apply_el(V1, v1, dict(v1, params=dict(v1["params"], level=A3)))                            # 평면 뷰의 레벨이 치수다
        g = Ctx(eng, eng.head + 1, "t", True).fresh()
        assert g.q(AN.dim_flags, A1) == ()                              # KeyError 'axis' 이던 곳
        assert g.q(AN.dim_value, A1)[0] == "orphan"
        assert g.q(AN.tag_text, T1)[0] == "orphan"
        assert g.q(DR.view_cfg, V1)[1] is None                         # 레벨이 아니면 고아 뷰(KeyError 'elevation' 이던 곳)
        AN.annot_bundle(g)
    finally:
        b.close()


def test_v5f_되살리지_않은_정의와_같은_id로_뷰를_만들면_경고하고_덮어쓰며_어떤_되돌리기_경로에서도_원본이_돌아온다(tmp_path):
    """결정(ADR-0015): 거절하지 않고 덮어쓰되 경고한다. 새 정의가 사라지면(삭제·되돌리기) 파일의 외부 정의가 돌아온다."""
    b, _res = _tamper(tmp_path, lambda f: _edit(f, V2, lambda d: d["params"].update(name="zz"), fix=False))
    try:
        assert V2 not in _ids(b, "imp.dobim", "view")
        h0 = S.trip(b.hashes("imp.dobim"))
        e0 = b.export("x0.ifc", "imp.dobim")
        orig = _def_props(ifcopenshell.open(str(e0)))[V2].NominalValue.wrappedValue
        r = b.run("view.create", {"id": V2, "name": "new2", "type": "plan", "level": F.LV2}, project="imp.dobim")
        assert r["ok"] and any("덮어쓴다" in w for w in r["warnings"]), r
        new_text = _def_props(ifcopenshell.open(str(b.export("x1.ifc", "imp.dobim"))))[V2].NominalValue.wrappedValue
        assert new_text != orig and '"new2"' in new_text
        # 지우면 원본이 돌아온다
        _ok(b.run("element.delete", {"id": V2}, project="imp.dobim"))
        assert _def_props(ifcopenshell.open(str(b.export("x2.ifc", "imp.dobim"))))[V2].NominalValue.wrappedValue == orig
        # 되돌리기 두 번 = 처음과 같다(해시 3종·내보낸 바이트)
        _ok(b.run("history.undo", {}, project="imp.dobim"))
        assert '"new2"' in _def_props(ifcopenshell.open(str(b.export("x3.ifc", "imp.dobim"))))[V2].NominalValue.wrappedValue
        _ok(b.run("history.undo", {}, project="imp.dobim"))
        assert S.trip(b.hashes("imp.dobim")) == h0
        assert b.export("x4.ifc", "imp.dobim").read_bytes() == e0.read_bytes()
        # 다시하기도 같은 길이다
        _ok(b.run("history.redo", {}, project="imp.dobim"))
        x5 = b.export("x5.ifc", "imp.dobim").read_bytes()
        assert '"new2"' in _def_props(ifcopenshell.open(str(tmp_path / "x5.ifc")))[V2].NominalValue.wrappedValue
        h1 = S.trip(b.hashes("imp.dobim"))
    finally:
        b.close()
    eng = Engine(tmp_path / "imp.dobim", "t", create=False)   # 재오픈해도 같다(로그에서 다시 만든 IFC)
    try:
        hh = eng.hashes()
        assert (hh["state"], hh["derived"], hh["ifc"]) == h1
    finally:
        eng.close()
    b2 = S.Bench(tmp_path)
    try:
        assert b2.export("x6.ifc", "imp.dobim").read_bytes() == x5
    finally:
        b2.close()


def test_v5g_값_타입이_다른_외부_속성도_덮어쓴_뒤_되돌리면_원본으로_돌아온다(tmp_path):
    def mut(f):
        _def_props(f)[VE].NominalValue = f.createIfcReal(1.5)    # 문자열이 아닌 외부 속성
    b, _res = _tamper(tmp_path, mut)
    try:
        assert VE not in _ids(b, "imp.dobim", "view")
        e0 = b.export("x0.ifc", "imp.dobim")
        _ok(b.run("view.create", {"id": VE, "name": "새 입면", "type": "elevation", "dir": "N"}, project="imp.dobim"))
        _ok(b.run("history.undo", {}, project="imp.dobim"))
        e1 = b.export("x1.ifc", "imp.dobim")
        v = _def_props(ifcopenshell.open(str(e1)))[VE].NominalValue
        assert v.is_a("IfcReal") and v.wrappedValue == 1.5
        assert e0.read_bytes() == e1.read_bytes()
    finally:
        b.close()


_ZERO = ("products_added", "products_deleted", "products_changed", "relationships_removed", "other_added", "other_removed")


def test_v7_뷰_주석_편집은_정의_속성셋_밖의_IFC를_바꾸지_않는다(tmp_path):
    """요소 보호: 뷰·치수·태그를 만들고 고치는 어떤 편집도 제품·타입·관계·재료·단위를 바꾸지 않는다(strict ifcdiff).
    정의가 모두 없어지면 속성셋도 사라져 편집 전과 IFC 가 완전히 같다."""
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b, "src.dobim")
        r = lambda n, a: _ok(b.run(n, a, project="src.dobim"))   # noqa: E731
        r("family.place", {"id": FD, "family": "door.single", "values": {"swing": "R"}, "host": F.W3, "offset": 4500})
        a = b.export("a.ifc", "src.dobim")
        r("view.create", {"id": V1, "name": "1F", "type": "plan", "level": F.LV1})
        r("view.create", {"id": VS, "name": "A-A", "type": "section", "p1": [0, 2000], "p2": [6000, 2000], "depth": 8000})
        r("annot.dim.create", {"id": A1, "view": V1, "a": R("wall", F.W1, "trim.start"), "b": R("wall", F.W1, "trim.end")})
        r("annot.dim.create", {"id": A2, "view": VS, "a": R("level", F.LV1, "elevation"), "b": R("level", F.LV2, "elevation")})
        r("annot.tag.create", {"id": T1, "view": V1, "target": F.D1})
        for i in range(3):
            r("view.set", {"id": V1, "cut_height": 1000 + i * 100})
        c = b.export("c.ifc", "src.dobim")
        d = ifcdiff_check.strict_diff(str(a), str(c))
        # 속성셋을 프로젝트에 거는 관계 객체 하나만 늘어난다: 제품·타입·재료·단위·그 밖의 객체는 그대로
        assert {k: d[k] for k in _ZERO} == dict.fromkeys(_ZERO, 0), d
        assert d["relationships_added"] == 1, d
        for eid in (T1, A1, A2, VS, V1):   # 정의를 모두 지우면 속성셋도 사라져 처음 IFC 와 같다
            r("element.delete", {"id": eid})
        c3 = b.export("c3.ifc", "src.dobim")
        assert ifcdiff_check.strict_diff(str(a), str(c3))["total"] == 0
    finally:
        b.close()
