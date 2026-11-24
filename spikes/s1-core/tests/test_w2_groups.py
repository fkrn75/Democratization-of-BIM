# SPDX-License-Identifier: MPL-2.0
"""W2 열한 번째 PR(ARC-46·COL-11 + ARC-22 잔여): 모델 그룹·조립품·커튼월 칸 합치기.

1. 모델 그룹: 정의(mgtype) + 배치(mgroup) 여러 개. 배치 놓기(이동·회전·대칭·다른 레벨), 제자리 이동·회전·대칭, 한 배치를 고쳐 같은 정의의 다른 배치에
   반영(mgroup.sync: 부속 번호가 같은 요소를 배치 틀로 옮겨 다시 쓴다, id 유지), 부속 빼기(excluded)·더하기, 풀기, 되돌리기. IFC 는 배치마다 IfcGroup
2. 조립품: 이름·유형 이름 + 부속. 조립품 단위 일람표(부속 수·범주별 수·길이·면적·부피 합, 유형별 합계). IFC 는 IfcElementAssembly + IfcRelAggregates
3. 커튼월 칸 합치기: 두 모서리 칸 사이를 패널 하나로(대표 키 = 시작 칸, 안쪽 멀리언 조각 없음), 격자 재배치·상단 붙이기·경사 커튼·곡선 축(현),
   IFC IfcPlate 하나
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import sys

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import commands_group as CG
from dobim_core import curtain as CW
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import xform as X
from dobim_core.ifc_model import part_guid

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V = F.V1
CW1, RF = F.CW1, F.RF1
ICW = "3" + "0" * 19 + "m1"   # IFC 왕복 시험용: 첫 글자가 유효한 GlobalId
GA, GB, GC, GD = ("9" + "0" * 19 + x for x in ("a1", "b1", "c1", "d1"))
RECT = [[0, 0], [6000, 0], [6000, 4000], [0, 4000]]


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def hs(s):
    return {k: v for k, v in s.eng.hashes().items() if k != "rev"}


def el(s, eid):
    return s.eng.st.el.get(eid)


def prm(s, eid):
    return s.eng.st.el[eid]["params"]


def members(s, gid) -> dict:
    p = prm(s, gid)
    return dict(zip(p["keys"], p["members"]))


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [x["message"][:160] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def same_as_full(s, views):
    s.eng.sync()
    s.eng._touch_pending()
    g = s.eng.g
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)
        assert g.q(D.view_fills, v) == full.q(D.view_fills, v)


def expect_axis(s, src_gid, dst_gid, key):
    """원본 배치의 번호 key 부속을 두 배치 틀 사이 변환으로 옮긴 벽 축(동기화 결과가 같아야 한다)."""
    xf = CG.between(CG._frame(prm(s, src_gid)), CG._frame(prm(s, dst_gid)))
    return X.params("wall", prm(s, members(s, src_gid)[key]), xf)["axis"]


# ---------------------------------------------------------------- 1. 모델 그룹
def test_1_그룹_배치와_동기화(base):
    s = base
    r = ok(s.run("mgroup.create", {"id": GA, "ids": [F.W1, F.W2], "name": "세대 A", "origin": [0, 0]}))
    assert r["members"] == [F.W1, F.W2, F.D1] and r["name"] == "세대 A"   # 벽에 놓인 문이 함께 든다
    assert members(s, GA) == {0: F.W1, 1: F.W2, 2: F.D1} and prm(s, GA)["level"] == F.LV1
    assert err(s.run("mgroup.create", {"ids": [F.W1]}))["code"] == "CONSTRAINT"              # 한 요소는 한 그룹 배치에만
    assert err(s.run("mgroup.create", {"ids": [F.W3], "name": "세대 A"}))["code"] == "CONSTRAINT"   # 정의 이름은 유일
    # 배치 놓기: 이동, 회전 90도, 대칭, 다른 레벨
    ok(s.run("mgroup.place", {"id": GA, "at": [0, 10000], "new_id": GB}))
    ok(s.run("mgroup.place", {"id": GA, "at": [20000, 0], "angle": 90, "new_id": GC}))
    ok(s.run("mgroup.place", {"id": GA, "at": [0, -10000], "mirror": True, "new_id": GD, "level": F.LV2}))
    mb, mc, md = members(s, GB), members(s, GC), members(s, GD)
    assert prm(s, mb[0])["axis"] == [0, 10000, 6000, 10000]
    assert prm(s, mc[0])["axis"] == [20000, 0, 20000, 6000] and prm(s, mc[1])["axis"] == [20000, 6000, 16000, 6000]
    assert prm(s, md[0])["axis"] == [6000, -10000, 0, -10000]           # 대칭: 축이 뒤집혀 왼쪽 면이 그대로 왼쪽
    assert prm(s, md[2])["offset"] == 6000 - prm(s, F.D1)["offset"]   # 문(offset = 가운데)은 대칭 자리
    assert s.eng.st.out[("on_level", md[0])] == [F.LV2] and prm(s, GD)["level"] == F.LV2 and prm(s, GD)["mirror"] is True
    assert s.eng.st.inn[("hosts", md[2])] == [md[0]] and s.eng.st.inn[("hosts", mc[2])] == [mc[0]]
    assert ("joins", min(mc[0], mc[1]), max(mc[0], mc[1])) in s.eng.st.relations()   # 그룹 안 조인도 복사
    assert {x["type"] for x in ok(s.run("mgroup.list", {}))["types"]} == {prm(s, GA)["type"]}
    # 원본 배치를 고치고 동기화: 다른 배치의 같은 번호 부속이 같은 id 로 바뀐다
    ok(s.run("wall.set", {"id": F.W1, "thickness": 300, "height": 3200}))
    ok(s.run("door.set", {"id": F.D1, "width": 1000}))
    ok(s.run("pset.set", {"id": F.W2, "pset": "Pset_WallCommon", "prop": "FireRating", "value": "EI90"}))
    ok(s.run("material.set", {"id": F.W2, "name": "벽돌"}))
    before = hs(s)
    r = ok(s.run("mgroup.sync", {"id": GA}))
    assert sorted(r["instances"]) == sorted([GB, GC, GD]) and r["updated"] == 9 and r["created"] == []
    for g in (GB, GC, GD):
        m = members(s, g)
        assert prm(s, m[0])["thickness"] == 300 and prm(s, m[0])["height"] == 3200 and prm(s, m[2])["width"] == 1000
        assert prm(s, m[0])["axis"] == expect_axis(s, GA, g, 0)
        assert s.eng.st.el[m[1]] is not None
    assert members(s, GB) == mb and members(s, GC) == mc                 # id 유지
    assert prm(s, md[2])["offset"] == 6000 - prm(s, F.D1)["offset"]
    ok(s.run("history.undo", {}))
    assert hs(s) == before
    ok(s.run("history.redo", {}))
    # 부속 빼기: 지우고 번호를 excluded 로, 동기화가 다시 만들지 않는다
    r = ok(s.run("mgroup.exclude", {"id": GB, "members": [mb[1]]}))
    assert r["excluded"] == [1] and el(s, mb[1]) is None
    # 부속 더하기(W3 + 문 D2) 후 동기화: 다른 배치에 새 부속이 생긴다
    r = ok(s.run("mgroup.add", {"id": GA, "ids": [F.W3]}))
    assert r["added"] == [F.W3, F.D2]
    r = ok(s.run("mgroup.sync", {"id": GA}))
    assert len(r["created"]) == 6 and el(s, mb[1]) is None and 1 not in members(s, GB)
    for g in (GB, GC, GD):
        m = members(s, g)
        assert set(m) >= {3, 4} and prm(s, m[3])["axis"] == expect_axis(s, GA, g, 3) and s.eng.st.inn[("hosts", m[4])] == [m[3]]
    # 제자리 이동·회전·대칭: 틀이 함께 바뀌어 다음 동기화가 부속을 제자리에 둔다
    ok(s.run("mgroup.move", {"id": GB, "dx": 500, "dy": 0}))
    assert prm(s, GB)["at"] == [500, 10000] and prm(s, members(s, GB)[0])["axis"] == [500, 10000, 6500, 10000]
    ok(s.run("mgroup.rotate", {"id": GC, "center": [20000, 0], "angle": -90}))
    assert prm(s, GC)["angle"] == 0 and prm(s, members(s, GC)[0])["axis"] == [20000, 0, 26000, 0]
    ok(s.run("mgroup.mirror", {"id": GD, "axis": [0, -5000, 10, -5000]}))
    assert "mirror" not in prm(s, GD) and prm(s, GD)["at"] == [0, 0]
    snap = {g: {k: prm(s, m)["axis"] for k, m in members(s, g).items() if el(s, m)["kind"] == "wall"} for g in (GB, GC, GD)}
    ok(s.run("mgroup.sync", {"id": GA}))
    assert {g: {k: prm(s, m)["axis"] for k, m in members(s, g).items() if el(s, m)["kind"] == "wall"} for g in (GB, GC, GD)} == snap
    ok(s.run("mgroup.mirror", {"id": GA, "axis": [-100, 0, -100, 10], "keep": True}))   # 대칭 배치 하나 더
    assert len(ok(s.run("mgroup.list", {}))["types"][0]["instances"]) == 5
    # 밖에서 지운 부속은 뺀 것으로 적는다. 풀기: 부속은 남고 마지막 배치면 정의도 지운다
    ok(s.run("element.delete", {"id": members(s, GC)[3]}))
    assert set(prm(s, GC)["excluded"]) == {3, 4}
    t = prm(s, GA)["type"]
    assert err(s.run("element.delete", {"id": t}))["code"] == "CONSTRAINT"
    for g in sorted(s.eng.st.by_kind["mgroup"]):
        r = ok(s.run("mgroup.ungroup", {"id": g}))
    assert r["type_deleted"] and el(s, t) is None and el(s, F.W1) is not None
    assert not s.eng.intent_consistency()


def test_1_그룹_틀_변환():
    for src, dst in ((((0, 0), 0, False), ((100, 50), 30, False)), (((10, 0), 45, False), ((0, 0), 10, True)), (((5, 5), 90, True), ((1, 2), 270, True))):
        xf = CG.between(src, dst)
        back = CG.between(dst, src)
        for pt in ((0.0, 0.0), (1000.0, 0.0), (300.0, -700.0)):
            q = xf.pt(*pt)
            assert all(abs(a - b) < 1e-5 for a, b in zip(back.pt(*q), pt))
        # 정의 원점이 배치 기준점으로 간다
        assert all(abs(a - b) < 1e-6 for a, b in zip(xf.pt(*src[0]), dst[0]))


# ---------------------------------------------------------------- 2. 조립품
def test_2_조립품과_일람표(base):
    s = base
    ok(s.run("assembly.create", {"id": F.AS1, "ids": [F.W1, F.D1], "name": "현관 벽체", "type": "벽체 유닛"}))
    assert err(s.run("assembly.create", {"ids": [F.W1]}))["code"] == "CONSTRAINT"   # 한 요소는 조립품 하나에만
    assert err(s.run("assembly.create", {"ids": [V]}))["code"] in ("CONSTRAINT", "NOT_FOUND")
    ok(s.run("assembly.create", {"ids": [F.W2], "type": "벽체 유닛"}))
    ok(s.run("assembly.create", {"ids": [F.W3, F.D2], "name": "북측"}))
    rows = ok(s.run("assembly.list", {"units": "mm"}))["rows"]
    a1 = next(r for r in rows if r["id"] == F.AS1)
    assert a1["count"] == 2 and a1["categories"] == {"doors": 1, "walls": 1} and a1["type"] == "벽체 유닛"
    assert abs(a1["length"] - 6000) < 1e-6 and a1["volume"] > 0
    t = {r["type"]: r for r in ok(s.run("assembly.list", {"group_by": "type"}))["rows"]}
    assert t["벽체 유닛"]["assemblies"] == 2 and t["벽체 유닛"]["count"] == 3 and t["북측"]["count"] == 2
    ok(s.run("assembly.set", {"id": F.AS1, "remove": [F.D1], "type": "벽체 유닛 B"}))
    assert prm(s, F.AS1) == {"name": "현관 벽체", "type": "벽체 유닛 B", "members": [F.W1]}
    assert err(s.run("assembly.set", {"id": F.AS1, "remove": [F.W1]}))["code"] == "CONSTRAINT"
    ok(s.run("element.delete", {"id": F.W1}))     # 마지막 부속을 지우면 조립품도 없어진다
    assert el(s, F.AS1) is None
    ok(s.run("history.undo", {}))
    assert prm(s, F.AS1)["members"] == [F.W1]


# ---------------------------------------------------------------- IFC(1·2)
def test_12_IFC_검증과_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("mgroup.create", {"id": GA, "ids": [F.W1, F.W2], "name": "세대 A"}))
        ok(b.run("mgroup.place", {"id": GA, "at": [0, 10000], "new_id": GB}))
        ok(b.run("assembly.create", {"id": F.AS1, "ids": [F.W3, F.D2], "name": "북측 벽체", "type": "벽체 유닛"}))
        h = b.hashes()
        out = b.export("g.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        gs = [g for g in f.by_type("IfcGroup") if g.is_a() == "IfcGroup"]
        assert sorted(g.GlobalId for g in gs) == sorted([part_guid(GA, "mgroup"), part_guid(GB, "mgroup")])
        ga = f.by_guid(part_guid(GA, "mgroup"))
        assert ga.ObjectType == "세대 A" and [x.GlobalId for x in ga.IsGroupedBy[0].RelatedObjects] == [F.W1, F.W2, F.D1]
        asm = f.by_guid(part_guid(F.AS1, "assembly"))
        assert asm.is_a("IfcElementAssembly") and asm.ObjectType == "벽체 유닛" and asm.PredefinedType == "USERDEFINED"
        assert [x.GlobalId for x in asm.IsDecomposedBy[0].RelatedObjects] == [F.W3, F.D2]
        assert asm.ContainedInStructure[0].RelatingStructure.GlobalId == F.LV1
        assert b.destructive("ifc.export", {"path": "g3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "g3.ifc"))
        assert file_errors(f3) == [] and len(f3.by_type("IfcElementAssembly")) == 1
        r = b.import_ifc("g.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("g2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        # 가져온 파일에서 고치기: 조립품을 지우면 IfcElementAssembly 도 없어진다(가지치기가 남기지 않는다)
        ok(b.run("element.delete", {"id": F.AS1}, project="imp.dobim"))
        f4 = ifcopenshell.open(str(b.export("g4.ifc", "imp.dobim")))
        assert not f4.by_type("IfcElementAssembly") and file_errors(f4) == []
        ok(b.run("mgroup.ungroup", {"id": GB}))
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()


# ---------------------------------------------------------------- 3. 커튼월 칸 합치기
def _cw(**kw):
    p = {"name": "a", "axis": [0, 0, 6000, 0], "height": 3000, "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "count", "value": 2},
         "types": {"k": {"width": 50, "depth": 150}}, "mullion": {"interior": "k", "border": "k"}, "join": "vertical", "panel_thickness": 24}
    p.update(kw)
    return p


def test_3_칸_합치기_배치():
    p = _cw()
    a = CW.layout(p)
    b = CW.layout(dict(p, merges=[["P0:0", "P1500:1500"]]))
    assert len(a["panels"]) == 8 and len(b["panels"]) == 5 and len(b["mullions"]) == len(a["mullions"]) - 4
    keys = {x[0] for x in b["panels"]}
    assert "P0:0" in keys and not keys & {"P0:1500", "P1500:0", "P1500:1500"}   # 대표 키 = 시작 칸
    m = next(x for x in b["panels"] if x[0] == "P0:0")
    assert m[3:5] == (0.0, 3000) and m[5] == (True, True, True, True)
    area = lambda lay, ks: sum(CW.panel_area(lay, x) for x in lay["panels"] if x[0] in ks)   # noqa: E731
    assert abs(CW.panel_area(b, m) - area(a, {"P0:0", "P0:1500", "P1500:0", "P1500:1500"})) < 1e-6
    assert [x for x in b["panels"] if x[0] != "P0:0"] == [x for x in a["panels"] if x[0] not in {"P0:0", "P0:1500", "P1500:0", "P1500:1500"}]
    assert not {"V1500:0", "V1500:1500", "H1500:0", "H1500:1500"} & CW.mullion_keys(b)
    # 격자 재배치: 영역 안에 선을 더해도 영역은 그대로(새 선 조각 없음), 모서리 칸의 선을 빼면 합치기가 떨어진다
    c = CW.layout(dict(p, merges=[["P0:0", "P1500:1500"]], u_add=[750]))
    assert len(c["panels"]) == 5 and not any(k.startswith("V750") for k in CW.mullion_keys(c))
    assert CW.prune(dict(p, merges=[["P0:0", "P1500:1500"]], u_remove=[1500])).get("merges") is None
    with pytest.raises(ValueError):
        CW.layout(dict(p, merges=[["P0:0", "P1500:0"], ["P1500:0", "P3000:1500"]]))   # 겹침
    # 가로 연속(join horizontal)·외곽 없음도 같은 규칙
    h = CW.layout(_cw(join="horizontal", mullion={"interior": "k"}, merges=[["P1500:0", "P3000:0"]]))
    assert len(h["panels"]) == 7
    # 곡선 축: 영역 양 끝 격자선 점을 잇는 현 하나, 너무 넓으면 거절
    arc = _cw(axis=[0, 0, 6000, 0], arc=[3000, 1500], u_grid={"rule": "max_spacing", "value": 600})
    la = CW.layout(arc)
    u0, u2 = CW.fmt(la["us"][0]), CW.fmt(la["us"][1])
    lm = CW.layout(dict(arc, merges=[[f"P{u0}:0", f"P{u2}:0"]]))
    pm = next(x for x in lm["panels"] if x[0] == f"P{u0}:0")
    assert pm[2][0] == CW.quad(la["pts"][0], la["pts"][2], 12.0)[0]
    assert CW.problem(dict(arc, merges=[[f"P{u0}:0", f"P{CW.fmt(la['us'][-2])}:0"]])) is not None


def test_3_칸_합치기_상단_경사():
    # 상단 윤곽: 합친 패널도 윤곽에서 잘리고 넓이 = 칸마다 자른 넓이의 합
    prof = [[0, 1000], [6000, 3000]]
    p = _cw(top_profile=prof)
    a = CW.layout(p)
    b = CW.layout(dict(p, merges=[["P0:0", "P1500:1500"]]))
    cells = {"P0:0", "P0:1500", "P1500:0", "P1500:1500"}
    assert "P0:0" in b["shapes"]
    assert abs(CW.panel_area(b, next(x for x in b["panels"] if x[0] == "P0:0")) - sum(CW.panel_area(a, x) for x in a["panels"] if x[0] in cells)) < 1e-3
    # 경사 커튼: 합친 칸 = 영역 ∩ 외곽
    sp = {"name": "s", "form": "sloped", "axis": [0, 0, 6000, 0], "slope": 20, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 3000]],
          "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "spacing", "value": 1500},
          "types": {"k": {"width": 50, "depth": 150}}, "mullion": {"interior": "k", "border": "k"}, "join": "vertical", "panel_thickness": 24}
    sa = CW.layout(sp)
    sb = CW.layout(dict(sp, merges=[["P0:0", "P1500:1500"]]))
    assert len(sb["panels"]) == len(sa["panels"]) - 3
    assert abs(sum(CW.panel_area(sb, x) for x in sb["panels"]) - sum(CW.panel_area(sa, x) for x in sa["panels"])) < 1e-3
    assert not {"V1500:0", "V1500:1500", "H1500:0", "H1500:1500"} & CW.mullion_keys(sb)


def test_3_칸_합치기_명령과_도면(base):
    s = base
    VE = "4" + "0" * 20 + "8"
    ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.create", {"id": F.V2, "name": "S", "type": "section", "p1": [1000, -5000], "p2": [1000, 6000]}))
    ok(s.run("view.create", {"id": VE, "name": "남측", "type": "elevation", "dir": "S"}))
    for v in (V, F.V2, VE):
        s.eng.sync()
        s.eng._touch_pending()
        s.eng.g.q(D.view_lines, v)
    ok(s.run("curtain.create", {"id": CW1, "level": F.LV1, "axis": [0, -2000, 6000, -2000], "height": 3000,
                                "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "count", "value": 2}}))
    before = hs(s)
    r = ok(s.run("curtain.merge", {"id": CW1, "keys": ["P1500:1500", "P0:0"]}))   # 순서는 상관없다
    assert r["key"] == "P0:0" and r["cells"] == 4 and r["panels"] == 5 and r["merged"] == 1
    assert prm(s, CW1)["merges"] == [["P0:0", "P1500:1500"]]
    assert err(s.run("curtain.merge", {"id": CW1, "keys": ["P1500:0", "P3000:0"]}))["code"] == "CONSTRAINT"   # 겹침
    ok(s.run("curtain.panel.set", {"id": CW1, "keys": ["P0:0"], "type": "opaque"}))
    s.eng.flush_ifc()
    f = s.eng.ifc.f
    assert f.by_guid(part_guid(CW1, "p:P0:0")).is_a("IfcPlate")
    for k in ("P1500:0", "P0:1500", "P1500:1500"):
        with pytest.raises(RuntimeError):
            f.by_guid(part_guid(CW1, f"p:{k}"))
    same_as_full(s, (V, F.V2, VE))   # 증분 도면 = 전체 재계산(평면·단면·입면)
    ev = s.eng.g.q(D.view_lines, VE)
    # 격자선을 빼서 모서리 칸이 없어지면 합치기가 떨어진다
    ok(s.run("curtain.grid", {"id": CW1, "dir": "u", "op": "remove", "at": 1500}))
    assert "merges" not in prm(s, CW1)
    ok(s.run("history.undo", {}))
    ok(s.run("curtain.unmerge", {"id": CW1, "key": "P1500:1500"}))
    assert "merges" not in prm(s, CW1)
    ok(s.run("curtain.panel.set", {"id": CW1, "keys": ["P0:0"], "type": "glass"}))
    assert hs(s) == before
    assert s.eng.g.q(D.view_lines, VE) != ev   # 합친 칸의 입면 선이 다르다(사이 멀리언이 없다)
    same_as_full(s, (V, F.V2, VE))


def test_3_칸_합치기_IFC_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("curtain.create", {"id": ICW, "level": F.LV1, "axis": [0, -2000, 6000, -2000], "height": 3000,
                                    "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "count", "value": 2}}))
        ok(b.run("curtain.merge", {"id": ICW, "keys": ["P0:0", "P1500:1500"]}))
        h = b.hashes()
        out = b.export("m.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        plates = [k for r in f.by_guid(ICW).IsDecomposedBy for k in r.RelatedObjects if k.is_a("IfcPlate")]
        assert len(plates) == 5
        assert b.destructive("ifc.export", {"path": "m3.ifc", "schema": "IFC2X3"})["ok"]
        assert file_errors(ifcopenshell.open(str(tmp_path / "m3.ifc"))) == []
        r = b.import_ifc("m.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("m2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
    finally:
        b.close()
