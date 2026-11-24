# SPDX-License-Identifier: MPL-2.0
"""W2 열세 번째 PR(ARC-23 문 호스팅 제약 완화 + ARC-31 난간 유형).

1. 문 호스팅 제약 완화(ARC-23)
   - 얇은 벽: 패밀리 문 레시피의 호스트 제약(host_thickness >= leaf_t + 20 같은 호스트 문맥 제약)이 배치·투영에서는 경고다(상용 도구처럼 놓는다).
     레시피 검증(family.validate)은 이전처럼 엄격하다. 벽을 얇게 고쳐도 거절하지 않고 경고한다
   - 짧은 벽: 문 폭과 같거나 조금 짧은 벽(양 끝 문틀 폭 한도 kinds.DOOR_END_ALLOW = 100mm 안)에도 문을 놓는다(경고). 개구는 벽 범위로 잘라 뚫는다.
     병합 검사(merge DOOR_OUT_OF_HOST·DOOR_OUTSIDE_TRIMMED)도 같은 규칙(door_status 는 사실 표시로 그대로). 한도를 넘으면 이전처럼 거절
   - 도면·IFC(IfcOpeningElement·IfcRelVoidsElement·IfcRelFillsElement) 왕복
2. 난간 유형(ARC-31): railtype(상부 난간대·중간 난간대·발루스터·동자) — 아래 시험
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import sys

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import queries as Q

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V = F.V1
TW, SW, TW2 = ("2" + "0" * 19 + x for x in ("t1", "s1", "t2"))      # 얇은 벽·짧은 벽
DT, DS, DS2 = ("6" + "0" * 19 + x for x in ("d1", "d2", "d3"))      # 문
IW, ID1, ID2 = ("3" + "0" * 19 + x for x in ("w7", "d7", "d8"))      # IFC 왕복용(첫 글자가 유효한 GlobalId)


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def g_of(s):
    s.eng.sync()
    s.eng._touch_pending()
    return s.eng.g


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [x["message"][:160] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def same_as_full(s, views=(V,)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)
        assert g.q(D.view_fills, v) == full.q(D.view_fills, v)


@pytest.fixture
def bs(base):
    ok(base.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    return base


# ---------------------------------------------------------------- 1. 얇은 벽
def test_1_얇은_벽에_패밀리_문(bs):
    ok(bs.run("wall.create", {"id": TW, "level": F.LV1, "axis": [0, 6000, 3000, 6000], "thickness": 50, "height": 2800}))
    r = bs.run("family.place", {"id": DT, "family": "door.single", "host": TW, "offset": 1500})
    assert r["ok"], r                                                          # 이전: 벽이 문짝(40)+20 보다 얇아 거절
    assert any("레시피 권장" in w for w in r["warnings"])
    g = g_of(bs)
    spec = g.q(Q.ifc_spec, DT)
    assert not spec.get("error") and spec["cut"] and spec["body"]               # 평가 오류 상태가 아니다(형상이 있다)
    assert g.q(D.wall_openings, TW)                                             # 벽에 개구가 뚫린다
    # 두꺼운 벽은 경고가 없다(이전과 같다)
    r2 = ok(bs.run("family.place", {"family": "door.single", "host": F.W1, "offset": 4500}))
    assert r2["id"]
    assert not [w for w in bs.run("family.place", {"family": "door.single", "host": F.W3, "offset": 1200})["warnings"] if "레시피 권장" in w]
    # 벽을 더 얇게 고쳐도 거절하지 않고 경고(이전: CONSTRAINT)
    r3 = bs.run("wall.set", {"id": F.W1, "thickness": 30})
    assert r3["ok"] and any("레시피 권장" in w for w in r3["warnings"]), r3
    ok(bs.run("model.validate", {}))
    same_as_full(bs)


def test_1_레시피_검증은_엄격():
    from dobim_core.family import evaluate as EV
    from dobim_core.family import library as LIB
    r, plan = EV.load_recipe(LIB.load("door.single"))
    with pytest.raises(Exception):
        EV.evaluate(r, plan, None, None, {"host_thickness": 40.0})               # 기본(엄격): 거절
    out = EV.evaluate(r, plan, None, None, {"host_thickness": 40.0}, soft_host=True)
    assert out["host_warnings"] and out["cut"]
    assert "host_warnings" not in EV.evaluate(r, plan, None, None, {"host_thickness": 200.0}, soft_host=True)   # 지키면 결과가 이전과 같다
    assert EV.host_constraint("host_thickness >= leaf_t + 20") and not EV.host_constraint("leaf_w >= 300")


# ---------------------------------------------------------------- 1. 짧은 벽
def test_1_짧은_벽에_문(bs):
    ok(bs.run("wall.create", {"id": SW, "level": F.LV1, "axis": [0, 7000, 850, 7000], "thickness": 100, "height": 2800}))
    r = bs.run("door.place", {"id": DS, "host": SW, "offset": 425, "width": 900, "height": 2100})
    assert r["ok"] and any("끝을 25.0mm 넘는다" in w for w in r["warnings"]), r   # 벽 850 < 문 900: 양 끝 25mm
    # 같은 길이는 경고 없이(이전과 같다)
    ok(bs.run("wall.create", {"id": TW2, "level": F.LV1, "axis": [2000, 7000, 2900, 7000], "thickness": 100, "height": 2800}))
    r = ok(bs.run("door.place", {"id": DS2, "host": TW2, "offset": 450, "width": 900}))
    # 한도 초과는 거절(이전 규칙 유지)
    assert err(bs.run("door.set", {"id": DS, "width": 1100}))["code"] == "CONSTRAINT"   # 양 끝 125 > 100
    assert err(bs.run("door.set", {"id": DS2, "offset": 300}))["code"] == "CONSTRAINT"  # 한쪽 150
    ok(bs.run("door.set", {"id": DS2, "offset": 400}))                                 # 한쪽 50: 경고와 함께
    g = g_of(bs)
    ops = g.q(D.wall_openings, SW)
    start, end, _ = g.q(Q.wall_trim, SW)
    assert ops and ops[0][0] == pytest.approx(start) and ops[0][1] == pytest.approx(end)   # 개구는 벽 범위로 잘린다
    assert g.q(Q.door_status, DS) == "outside_trimmed_host"                       # 사실 표시(넘으면 표시), 병합 검사는 허용 안이라 위반 아님
    v = ok(bs.run("model.validate", {}))
    assert not [m for m in v["violations"] if "IfcGloballyUniqueId" not in m], v
    from dobim_core import merge as M
    codes = {x["rule"] for x in M.violations(M.View(M.fold(M.read_rows(bs.eng.conn))))}   # 병합 검사도 벽 끝 허용 안의 문을 위반으로 보지 않는다
    assert not codes & {"DOOR_OUT_OF_HOST", "DOOR_OUTSIDE_TRIMMED"}, codes
    assert err(bs.run("wall.set", {"id": SW, "axis": [0, 7000, 600, 7000]}))["code"] == "CONSTRAINT"   # 벽을 줄여 한도를 넘으면 거절
    same_as_full(bs)
    assert K.door_overhang(-25, 875, 0, 850) == 25 and K.door_span_ok(-100, 900, 0, 800) and not K.door_span_ok(-101, 900, 0, 800)


# ---------------------------------------------------------------- 1. IFC 왕복
def test_1_IFC_검증과_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("wall.create", {"id": IW, "level": F.LV1, "axis": [0, 9000, 850, 9000], "thickness": 60, "height": 2800}))
        ok(b.run("door.place", {"id": ID1, "host": IW, "offset": 425, "width": 900, "height": 2100}))
        ok(b.run("wall.create", {"id": TW, "level": F.LV1, "axis": [2000, 9000, 5000, 9000], "thickness": 50, "height": 2800}))
        ok(b.run("family.place", {"id": ID2, "family": "door.single", "host": TW, "offset": 1500}))
        h = b.hashes()
        out = b.export("d.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        d = f.by_guid(ID1)
        fills = [r for r in f.by_type("IfcRelFillsElement") if r.RelatedBuildingElement == d]
        assert len(fills) == 1
        op = fills[0].RelatingOpeningElement
        voids = [r for r in f.by_type("IfcRelVoidsElement") if r.RelatedOpeningElement == op]
        assert len(voids) == 1 and voids[0].RelatingBuildingElement.GlobalId == IW
        assert b.destructive("ifc.export", {"path": "d3.ifc", "schema": "IFC2X3"})["ok"]
        assert file_errors(ifcopenshell.open(str(tmp_path / "d3.ifc"))) == []
        r = b.import_ifc("d.ifc", "imp.dobim")
        assert r["ok"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("d2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
    finally:
        b.close()


# ---------------------------------------------------------------- 2. 난간 유형(ARC-31)
RT, RT2 = F.RLT1, "9" + "0" * 19 + "z9"
RA, RB = ("c" + "0" * 19 + x for x in ("a1", "a2"))
IRT, IRA = "3" + "0" * 19 + "t9", "3" + "0" * 19 + "r9"
BAL = {"name": "발루스터 1200", "height": 1200, "top": {"w": 60, "d": 40}, "rails": [{"z": 150, "w": 40, "d": 30}],
       "baluster": {"spacing": 110, "w": 20, "bottom": 150}, "post": {"spacing": 1500, "w": 60}}


def test_2_난간_유형_형상():
    p = {"name": "r", "path": [[0.0, 0.0], [3000.0, 0.0], [3000.0, 1000.0]], "height": 1100.0, "base_offset": 0.0, "rise": 0.0}
    assert "members" not in K.railing_geom(p)                                    # 유형 없는 난간은 이전과 같다
    q = dict(p, rltype=RT, parts={"top": {"w": 50.0, "d": 50.0}, "rails": [{"z": 500.0, "w": 30.0, "d": 30.0}], "post": {"spacing": 1200.0, "w": 60.0},
                                  "baluster": {"spacing": 120.0, "w": 20.0, "bottom": 100.0}})
    assert K.row_params_error("railing", q) is None
    g = K.railing_geom(q)
    kinds = [m[0] for m in g["members"]]
    assert kinds.count("top") == 1 and kinds.count("rail") == 1
    assert kinds.count("post") == 3 + 1 + 1                                       # 3000 은 3칸(1000), 1000 은 1칸: 꼭짓점 3 + 사이 2
    assert kinds.count("baluster") == 3 * 8 + 1 * 8                               # 칸 1000 을 120 이하로 9 등분, 양 끝(동자) 빼고 8
    assert kinds.count("post") + kinds.count("baluster") == K.rail_member_count(q["path"], q["parts"])
    b = next(m for m in g["members"] if m[0] == "baluster")
    assert b[1][0][2] == pytest.approx(100) and b[1][1][2] == pytest.approx(1050)   # 아래 끝 bottom, 위 끝 = 상부 난간대 아랫면(1100 - 50)
    xs = sorted(m[1][0][0] for m in g["members"] if m[0] == "baluster" and m[1][0][1] == 0 and m[1][0][0] < 1000)
    assert xs[0] == pytest.approx(1000 / 9) and xs[1] - xs[0] == pytest.approx(1000 / 9)   # 같은 간격(상한 이하)
    top = next(m for m in g["members"] if m[0] == "top")
    assert top[1][0][2] == pytest.approx(1075)                                    # 상부 난간대 중심 = 높이 - 춤/2
    # 거절: 중간 난간대가 상부 난간대와 겹친다, 발루스터 간격이 너무 좁다, 부재가 너무 많다
    bad = dict(q, parts=dict(q["parts"], rails=[{"z": 1070.0, "w": 30.0, "d": 30.0}]))
    assert K.row_params_error("railing", bad)
    assert K.row_params_error("railing", dict(q, parts=dict(q["parts"], baluster={"spacing": 10.0, "w": 5.0, "bottom": 0.0})))
    assert K.row_params_error("railing", dict(q, path=[[0.0, 0.0], [5e6, 0.0]], parts=dict(q["parts"], baluster={"spacing": 50.0, "w": 5.0, "bottom": 0.0})))


def test_2_난간_유형_명령(bs):
    ok(bs.run("railtype.create", dict(BAL, id=RT)))
    assert err(bs.run("railtype.create", dict(BAL, id=RT2)))["code"] == "CONSTRAINT"               # 같은 이름
    assert err(bs.run("railtype.create", {"name": "x", "height": 1000, "rails": [{"z": 990}]}))["code"] == "CONSTRAINT"
    ok(bs.run("railing.create", {"id": RA, "level": F.LV1, "path": [[0, -2000], [4500, -2000]], "type": RT}))
    p = ok(bs.run("element.get", {"id": RA}))["params"]
    assert p["height"] == 1200 and p["rltype"] == RT and p["parts"]["baluster"]["spacing"] == 110
    assert err(bs.run("railing.create", {"level": F.LV1, "path": [[0, 0], [1, 0]], "type": RT, "height": 900}))["code"] == "CONSTRAINT"
    assert err(bs.run("railing.set", {"id": RA, "height": 900}))["code"] == "CONSTRAINT"           # 높이는 유형이 정한다
    ok(bs.run("railing.set", {"id": RA, "path": [[0, -2000], [6000, -2000]]}))                       # 경로는 바꾼다(유형 유지)
    assert "parts" in ok(bs.run("element.get", {"id": RA}))["params"]
    ok(bs.run("railing.create", {"id": RB, "level": F.LV1, "path": [[0, -3000], [3000, -3000]]}))
    r = ok(bs.run("railing.type.set", {"ids": [RB], "type": RT}))
    assert r["changed"] == 1
    lst = ok(bs.run("railtype.list", {}))
    assert lst["items"][0]["railings"] == 2 and lst["untyped_railings"] == 0
    v = ok(bs.run("model.validate", {}))
    assert not [m for m in v["violations"] if "IfcGloballyUniqueId" not in m], v   # of_type 관계·params.rltype 이 맞는다
    g = g_of(bs)
    before = len(g.q(D.railing_lines, V, RA))
    assert before > 2                                                                                 # 평면: 난간선 + 동자 사각형
    r = ok(bs.run("railtype.set", {"id": RT, "height": 1100, "post": {"spacing": 1000, "w": 50}}))     # 유형 편집 -> 인스턴스 반영
    assert r["railings"] == 2
    assert ok(bs.run("element.get", {"id": RB}))["params"]["height"] == 1100
    assert len(g_of(bs).q(D.railing_lines, V, RA)) > before                                           # 동자가 늘었다
    st = g_of(bs).q(Q.model_stats)
    assert st["railtype"] == 1
    ok(bs.run("railtype.set", {"id": RT, "clear": ["baluster"]}))
    assert "baluster" not in ok(bs.run("element.get", {"id": RA}))["params"]["parts"]
    same_as_full(bs)
    d = ok(bs.run("railtype.duplicate", {"id": RT, "name": "복제", "new_id": RT2}))
    assert d["id"] == RT2
    assert err(bs.run("element.delete", {"id": RT}))["code"] == "CONSTRAINT"
    r = ok(bs.run("railtype.delete", {"id": RT, "detach": True}))
    assert r["detached"] == 2
    p = ok(bs.run("element.get", {"id": RA}))["params"]
    assert "rltype" not in p and "parts" not in p and p["height"] == 1100
    ok(bs.run("history.undo", {}))
    assert ok(bs.run("element.get", {"id": RA}))["params"]["rltype"] == RT
    ok(bs.run("railing.type.set", {"ids": [RA], "type": None}))
    assert "parts" not in ok(bs.run("element.get", {"id": RA}))["params"]
    same_as_full(bs)


def test_2_계단_슬래브_난간에_유형(bs):
    ok(bs.run("railtype.create", dict(BAL, id=RT)))
    ok(bs.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 1000, 3500], "width": 1200, "height": 3000, "risers": 18}))
    r = ok(bs.run("railing.from_stair", {"stair": F.ST1, "type": RT}))
    for rid in r["created"]:
        p = ok(bs.run("element.get", {"id": rid}))["params"]
        assert p["rltype"] == RT and p["height"] == 1200 and p["rise"] > 0
    ok(bs.run("stair.set", {"id": F.ST1, "width": 1000}))                                              # 원본을 따라가도 유형은 남는다
    assert ok(bs.run("element.get", {"id": r["created"][0]}))["params"]["rltype"] == RT
    g = g_of(bs)
    ok(bs.run("view.create", {"id": F.V2, "name": "S", "type": "section", "p1": [0, 2000], "p2": [3000, 2000]}))
    sec = g_of(bs).q(D.railing_lines, F.V2, r["created"][0])
    assert len(sec) > 10                                                                              # 단면: 발루스터·난간대
    assert g is not None
    same_as_full(bs, (V, F.V2))


def test_2_IFC_검증과_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("railtype.create", dict(BAL, id=IRT)))
        ok(b.run("railing.create", {"id": IRA, "level": F.LV1, "path": [[0, -2000], [3000, -2000], [3000, -500]], "type": IRT}))
        h = b.hashes()
        out = b.export("r.ifc")
        f = ifcopenshell.open(str(out))
        assert file_errors(f) == []
        e = f.by_guid(IRA)
        kids = [k for r in e.IsDecomposedBy for k in r.RelatedObjects]
        assert sorted((k.is_a(), k.PredefinedType, k.ObjectType) for k in kids) == [("IfcMember", "POST", None), ("IfcMember", "USERDEFINED", "BALUSTER"),
                                                                                    ("IfcMember", "USERDEFINED", "RAIL")]
        t = [r.RelatingType for r in e.IsTypedBy] if hasattr(e, "IsTypedBy") else []
        assert t and t[0].is_a("IfcRailingType") and t[0].Name == "발루스터 1200" and t[0].PredefinedType == "BALUSTRADE"
        assert b.destructive("ifc.export", {"path": "r3.ifc", "schema": "IFC2X3"})["ok"]
        f3 = ifcopenshell.open(str(tmp_path / "r3.ifc"))
        assert file_errors(f3) == [] and f3.by_type("IfcRailingType")
        r = b.import_ifc("r.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("r2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        ok(b.run("railtype.set", {"id": IRT, "clear": ["baluster"]}))
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
    finally:
        b.close()
