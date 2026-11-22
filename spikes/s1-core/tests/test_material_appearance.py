# SPDX-License-Identifier: MPL-2.0
"""재료 외관(W2 다섯 번째 PR, DATA-05·VIS-07): 재료 이름 하나의 표면 색·투명도·광택 정의, 기본 재료 라이브러리, IFC 표면 스타일(IFC4·IFC2x3),
가져오기 왕복, 실행 취소, 요소별 외관 조회(3D 장면), 정의하지 않은 프로젝트의 IFC 불변, 도면 불변."""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import appearance as AP
from dobim_core import drawing as D
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402,F401
import scenarios as S  # noqa: E402

CWI = "3" + "0" * 19 + "c1"   # 첫 글자가 유효한 GlobalId(내보내기가 바꾸지 않는다)
CW = {"id": CWI, "level": F.LV1, "axis": [0, -2000, 6000, -2000], "height": 3000, "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "count", "value": 2}}


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def _ifc_errors(ff):
    import ifcopenshell.validate as IV
    lg = IV.json_logger()
    IV.validate(ff, lg, express_rules=True)
    return [x["message"][:300] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]]


def _renderings(f, name):
    """이름 name 의 IfcMaterial 에 붙은 표면 스타일 항목들."""
    out = []
    for m in f.by_type("IfcMaterial"):
        if m.Name != name:
            continue
        for rep in m.HasRepresentation or ():
            for r in rep.Representations:
                for it in r.Items:
                    for st in it.Styles or ():
                        inner = st.Styles if st.is_a("IfcPresentationStyleAssignment") else (st,)
                        for s2 in inner:
                            out.extend(s2.Styles)
    return out


# ---------------------------------------------------------------- 라이브러리
def test_기본_재료_라이브러리는_18단계_색과_같고_유리는_반투명이다():
    for keys, rgb in AP.MATERIAL_COLORS:
        assert AP.library_appearance(keys[0])["color"] == list(rgb)
    g = AP.library_appearance("외부 유리")
    assert g["transparency"] > 0.3 and g["shininess"] > 0.5
    for nm in ("철근콘크리트", "Concrete", "알루미늄 멀리언", "H형 강재", "석고보드", "목재 루버", "화강석"):
        assert AP.library_appearance(nm) is not None, nm
    assert AP.library_appearance("철근콘크리트")["color"] == AP.library_appearance("콘크리트")["color"]   # 콘크리트가 철보다 먼저
    assert AP.library_appearance("알 수 없는 재료") is None and AP.material_rgb("Concrete") is None   # 18단계 IFC 기본 표는 그대로


# ---------------------------------------------------------------- 커맨드
def test_정의_만들기_고치기_지우기와_실행_취소(base):
    h0 = base.eng.state_hash()
    r = ok(base.run("material.define", {"name": "Concrete"}))
    assert r["created"] and r["appearance"] == {"color": [190, 190, 186], "transparency": 0.0, "shininess": 0.05} and r["used_by"] >= 1
    r2 = ok(base.run("material.define", {"name": "Concrete", "transparency": 0.25}))   # 준 값만 바꾼다
    assert not r2["created"] and r2["changed"] and r2["appearance"]["color"] == [190, 190, 186] and r2["appearance"]["transparency"] == 0.25
    assert not ok(base.run("material.define", {"name": "Concrete", "transparency": 0.25}))["changed"]
    e = err(base.run("material.define", {"name": "새 재료"}))   # 라이브러리에 없으면 색이 필요하다
    assert e["code"] == "CONSTRAINT" and e["path"] == "/color"
    assert err(base.run("material.define", {"name": "x", "color": [0, 0, 256]}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("material.define", {"name": "x", "color": [0, 0, 0], "transparency": 1.5}))["code"] == "SCHEMA_INVALID"
    ok(base.run("material.define", {"name": "외벽 도장", "color": [200, 80, 60], "shininess": 0.3}))
    lst = {x["name"]: x for x in ok(base.run("material.list", {}))["items"]}
    assert lst["Concrete"]["source"] == "defined" and lst["외벽 도장"]["used_by"] == 0 and lst["외벽 도장"]["appearance"]["color"] == [200, 80, 60]
    ok(base.run("material.set", {"id": F.W2, "name": "적벽돌"}))
    br = {x["name"]: x for x in ok(base.run("material.list", {}))["items"]}["적벽돌"]
    assert br["source"] == "default" and br["id"] is None and br["appearance"]["color"] == [168, 86, 64]   # 정의 없는 재료: 18단계 기본 색
    assert ok(base.run("material.library", {"name": "외부 유리"}))["appearance"]["transparency"] > 0
    assert len(ok(base.run("material.library", {}))["items"]) == len(AP.library_rows())
    assert err(base.run("material.delete", {"name": "없는 재료"}))["code"] == "NOT_FOUND"
    ok(base.run("material.delete", {"name": "외벽 도장"}))
    n = 0
    while base.eng.state_hash() != h0 and n < 10:   # 정의 2·고치기 1·재료 지정 1·지우기 1 = 5번(바뀐 것 없는 정의는 기록하지 않는다)
        ok(base.run("history.undo", {}))
        n += 1
    assert base.eng.state_hash() == h0 and n == 5
    ok(base.run("history.redo", {}))
    assert ok(base.run("material.list", {}))["items"][0]["source"] == "defined"


def test_요소별_외관_조회는_층_구성과_커튼월_부속까지_준다(base):
    ok(base.run("curtain.create", CW))
    ok(base.run("material.define", {"name": "유리"}))
    ok(base.run("wall.layers", {"id": F.W2, "layers": [{"material": "석고보드", "thickness": 20}, {"material": "콘크리트", "thickness": 160}, {"material": "석고보드", "thickness": 20}]}))
    ok(base.run("material.define", {"name": "콘크리트", "color": [100, 100, 100]}))
    items = {x["id"]: x for x in ok(base.run("material.appearance", {"ids": [F.W1, F.W2, CWI]}))["items"]}
    assert items[F.W1]["material"] == "Concrete" and items[F.W1]["appearance"] is None and items[F.W1]["source"] is None
    assert items[F.W2]["material"] == "콘크리트" and items[F.W2]["appearance"]["color"] == [100, 100, 100]
    assert [x["appearance"] is not None for x in items[F.W2]["layers"]] == [False, True, False]
    p = items[CWI]["parts"]
    assert p["glass"]["appearance"]["transparency"] == AP.library_appearance("유리")["transparency"] and p["mullion"]["appearance"] is None
    pp = ok(base.run("param.define", {"pset": "Pset_X", "name": "Zone", "type": "text", "kinds": ["wall"]}))["id"]
    assert err(base.run("material.appearance", {"ids": [pp]}))["code"] == "CONSTRAINT"


def test_외관_정의는_도면을_바꾸지_않는다(base):
    def lines():
        g = base.eng.g
        return [g.q(D.view_lines, v) for v in (F.V1, F.V2)]
    before = lines()
    ok(base.run("material.define", {"name": "Concrete", "color": [10, 20, 30], "transparency": 0.5}))
    ok(base.run("material.define", {"name": "Timber", "transparency": 0.2}))
    assert lines() == before


# ---------------------------------------------------------------- IFC
def test_IFC4_IFC2x3_표면_스타일과_왕복(tmp_path):
    import ifcopenshell
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        plain = b.export("plain.ifc").read_bytes()
        h_plain = b.hashes()
        ok(b.run("curtain.create", CW))
        h_cw = b.hashes()
        cw_bytes = b.export("cw.ifc").read_bytes()
        ok(b.run("material.define", {"name": "유리"}))                                        # 커튼월 패널(유리) 재료
        ok(b.run("material.define", {"name": "Concrete", "transparency": 0.25, "shininess": 0}))   # 벽 W1 재료
        ok(b.run("material.define", {"name": "Timber", "color": [90, 60, 30]}))               # 18단계 기본 색이 있던 재료: 정의가 덮는다
        ok(b.run("material.define", {"name": "쓰지 않는 재료", "color": [1, 2, 3]}))           # IFC 재료가 없다: 정의만 왕복한다
        h = b.hashes()
        assert S.trip(h) != S.trip(h_cw)
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        g = _renderings(f, "유리")
        assert len(g) == 1 and g[0].is_a("IfcSurfaceStyleRendering") and abs(g[0].Transparency - 0.6) < 1e-9
        assert abs(g[0].SpecularColour.wrappedValue - 0.9) < 1e-9 and round(g[0].SurfaceColour.Blue * 255) == 212
        c = _renderings(f, "Concrete")
        assert len(c) == 1 and abs(c[0].Transparency - 0.25) < 1e-9 and c[0].SpecularColour is None
        t = _renderings(f, "Timber")
        assert len(t) == 1 and t[0].is_a("IfcSurfaceStyleRendering") and round(t[0].SurfaceColour.Red * 255) == 90
        assert not [m for m in f.by_type("IfcMaterial") if m.Name == "쓰지 않는 재료"]
        assert _ifc_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t2x3.ifc", "schema": "IFC2X3"})["ok"]
        f2 = ifcopenshell.open(str(tmp_path / "t2x3.ifc"))
        assert f2.schema == "IFC2X3" and _ifc_errors(f2) == []
        g2 = _renderings(f2, "유리")
        assert len(g2) == 1 and abs(g2[0].Transparency - 0.6) < 1e-9
        # 가져오기: 정의가 되살아나고 해시·바이트가 같다
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("t3.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        lst = {x["name"]: x for x in ok(b.run("material.list", {}, project="imp.dobim"))["items"]}
        assert lst["유리"]["source"] == "defined" and lst["쓰지 않는 재료"]["appearance"]["color"] == [1, 2, 3]
        ok(b.run("material.define", {"name": "유리", "transparency": 0.3}, project="imp.dobim"))   # 가져온 프로젝트에서 고치기
        f3 = ifcopenshell.open(str(b.export("t4.ifc", "imp.dobim")))
        g3 = _renderings(f3, "유리")
        assert len(g3) == 1 and abs(g3[0].Transparency - 0.3) < 1e-9 and _ifc_errors(f3) == []
        ok(b.run("history.undo", {}, project="imp.dobim"))
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)   # 되돌리면 의미 해시가 같다(엔티티 번호는 새로 매겨진다)
        # IFC2x3 파일 가져오기도 정의를 되살린다
        r2 = b.import_ifc("t2x3.ifc", "imp2.dobim")
        assert r2["ok"], r2
        lst2 = {x["name"]: x for x in ok(b.run("material.list", {}, project="imp2.dobim"))["items"]}
        assert lst2["유리"]["source"] == "defined" and lst2["Concrete"]["appearance"]["transparency"] == 0.25
        ok(b.run("material.define", {"name": "Concrete", "transparency": 0.5}, project="imp2.dobim"))   # IFC2x3 스타일 묶음도 코어 것으로 알아본다
        # 실행 취소하면 외관을 쓰지 않은 상태의 해시·바이트로 돌아간다
        for _ in range(4):
            ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h_cw)
        assert b.export("cw2.ifc").stat().st_size > 0 and len(cw_bytes) > 0
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h_plain) and len(plain) > 0
    finally:
        b.close()


def test_지붕_경사면_부속도_요소_재료의_외관을_따른다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("roof.create", {"id": F.RF1, "level": F.LV2, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "form": "gable", "pitch": 30, "overhang": 600}))
        ok(b.run("material.set", {"id": F.RF1, "name": "슬레이트"}))
        ok(b.run("material.define", {"name": "슬레이트", "color": [10, 20, 30], "transparency": 0.1}))
        from dobim_core import globalid as GI   # 첫 글자가 무효한 id 는 내보낸 파일에서 바뀐다: 원래 id 로 되돌려 연다
        f = GI.open_exported(str(b.export("r.ifc")))
        roof = f.by_guid(F.RF1)
        kids = [k for r in roof.IsDecomposedBy for k in r.RelatedObjects]
        assert kids
        for k in kids:
            m = [r.RelatingMaterial for r in k.HasAssociations if r.is_a("IfcRelAssociatesMaterial")]
            assert len(m) == 1 and m[0].Name == "슬레이트"
        g = _renderings(f, "슬레이트")
        assert len(g) == 1 and round(g[0].SurfaceColour.Green * 255) == 20 and abs(g[0].Transparency - 0.1) < 1e-9 and _ifc_errors(f) == []
    finally:
        b.close()


def test_다시_연_프로젝트는_같은_IFC_해시(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("material.set", {"id": F.W2, "name": "유리 블록"}))
        ok(b.run("material.define", {"name": "유리 블록", "transparency": 0.4}))
        ok(b.run("material.define", {"name": "Concrete", "color": [9, 9, 9]}))
        ok(b.run("wall.layers", {"id": F.W1, "layers": [{"material": "단열재", "thickness": 50}, {"material": "Concrete", "thickness": 150}]}))
        ok(b.run("material.define", {"name": "단열재"}))
        h = b.hashes()
        b.close()
        b2 = S.Bench(tmp_path)
        assert S.trip(b2.hashes()) == S.trip(h)
        b2.close()
    finally:
        b.close()


def test_가져온_파일의_다른_도구_스타일은_덮어쓰지_않는다(tmp_path):
    import ifcopenshell
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("material.set", {"id": F.W2, "name": "유리"}))
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        mat = next(m for m in f.by_type("IfcMaterial") if m.Name == "유리")
        st = mat.HasRepresentation[0].Representations[0].Items[0].Styles[0]
        st.Styles = list(st.Styles) + [f.createIfcSurfaceStyleLighting(f.createIfcColourRgb(None, 1, 1, 1), f.createIfcColourRgb(None, 1, 1, 1),
                                                                        f.createIfcColourRgb(None, 1, 1, 1), f.createIfcColourRgb(None, 1, 1, 1))]   # 다른 도구가 더한 스타일
        f.write(str(tmp_path / "x.ifc"))
        r = b.import_ifc("x.ifc", "imp.dobim")
        assert r["ok"], r
        e = err(b.run("material.define", {"name": "유리"}, project="imp.dobim"))
        assert e["code"] == "CONSTRAINT" and e["path"] == "/name"
        ok(b.run("material.define", {"name": "Concrete", "transparency": 0.1}, project="imp.dobim"))   # 스타일이 없던 재료는 된다
    finally:
        b.close()


@pytest.mark.parametrize("n", [1])
def test_정의_수_상한과_이름_검사(base, n):
    from dobim_core import kinds as K
    assert K.row_params_error("matdef", {"name": "a", "color": [0, 0, 0], "transparency": 0.0, "shininess": 0.0}) is None
    assert K.row_params_error("matdef", {"name": "a", "color": [0, 0, 0], "transparency": 1, "shininess": 0.0}) is not None   # 정수 투명도는 저장 형식이 아니다
    assert K.row_params_error("matdef", {"name": "a", "color": [0, 0, True], "transparency": 0.0, "shininess": 0.0}) is not None
    assert K.MATDEFS_MAX >= 100 * n
