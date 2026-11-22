# SPDX-License-Identifier: MPL-2.0
"""벽 유형(ARC-11)·벽 위치선·스택 벽(ARC-09) (W2 일곱 번째 PR).

- 순수 계산: 위치선 거리(중심선·코어 중심·마감면·코어 면, 코어 기본값), 스택 구간 높이
- 벽 유형: 만들기·고치기·복제·지우기(쓰는 벽이 있으면 거절·detach)·일람표, 유형을 고치면 그 유형의 벽만 따라가고 위치선은 제자리
- 벽에 유형 입히기·떼기, 유형 있는 벽의 층 구성·두께 직접 편집 거절, 분할이 유형을 잇는다
- 위치선: wall.create·wall.arc 의 location_line, wall.location.set(몸체 고정·선 고정), wall.location 조회, 곡선 벽
- 스택 벽: 부분 벽 생성·고치기·지우기(부분 벽과 문 함께)·부분 벽 직접 편집 거절·문 놓기, 평면은 절단 높이 구간, 단면·입면은 구간마다
- 실행 취소·다시 실행, 도면 증분 = 전체 재계산, model.validate
- IFC: IfcWallType + IfcRelDefinesByType + 유형 IfcMaterialLayerSet, 스택 벽 IfcRelAggregates(부분 벽은 층에 담지 않는다),
  IFC4·IFC2X3 검증 오류 0, 가져오기 왕복 해시·바이트, 가져온 프로젝트에서 고치기·되돌리기, 다시 연 프로젝트 해시
"""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import walltypes as WTY

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V, VS, VE = F.V1, F.V2, "4" + "0" * 20 + "8"
T1, T2, T3 = F.WT1, F.WT2, "9" + "0" * 19 + "26"
SW = F.SW1
# IFC 시험용: 첫 글자가 유효한 GlobalId(내보내기가 바꾸지 않는다)
IT1, IT2, ISW, IP1, IP2 = ("3" + "0" * 19 + x for x in ("t1", "t2", "s1", "p1", "p2"))
L4 = [{"material": "치장벽돌", "thickness": 90}, {"material": "단열재", "thickness": 50}, {"material": "철근콘크리트", "thickness": 200}, {"material": "석고보드", "thickness": 10}]


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


def params(s, eid):
    return ok(s.run("element.get", {"id": eid}))["params"]


def same_as_full(s, views=(V, VS, VE)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)
        assert g.q(D.view_fills, v) == full.q(D.view_fills, v)


def views(s):
    ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(s.run("view.create", {"id": VS, "name": "S", "type": "section", "p1": [3000, -6000], "p2": [3000, 12000]}))
    ok(s.run("view.create", {"id": VE, "name": "남측", "type": "elevation", "dir": "S"}))


def types(s, t1=T1, t2=T2):
    ok(s.run("walltype.create", {"id": t1, "name": "외벽", "layers": L4, "core": [2, 3], "function": "exterior"}))
    ok(s.run("walltype.create", {"id": t2, "name": "칸막이", "thickness": 100, "height": 2700}))


# ---------------------------------------------------------------- 순수 계산
def test_위치선_거리와_코어_기본값():
    p = {"thickness": 350.0, "layers": [dict(x, thickness=float(x["thickness"])) for x in L4], "core": [2, 3]}
    want = {"wall_center": 0.0, "finish_ext": -175.0, "finish_int": 175.0, "core_ext": -35.0, "core_int": 165.0, "core_center": 65.0}
    for line, d in want.items():
        assert WTY.loc_offset(p, line) == pytest.approx(d), line
    q = dict(p)
    q.pop("core")
    assert WTY.core_of(q) == (2, 3) and WTY.loc_offset(q, "core_center") == pytest.approx(65.0)   # 기본 코어 = 가장 두꺼운 층
    plain = {"thickness": 200.0}
    assert WTY.loc_offset(plain, "core_center") == 0.0 and WTY.loc_offset(plain, "core_ext") == -100.0 and WTY.loc_offset(plain, "core_int") == 100.0
    assert WTY.loc_offset(dict(plain, loc="finish_int")) == 100.0   # 행의 loc 를 따른다


def test_스택_구간_높이():
    segs = [{"type": T1, "height": 1000.0, "offset": 0.0}, {"type": T2, "height": None, "offset": 0.0}]
    assert WTY.stack_heights(segs, 3000.0) == [1000.0, 2000.0]
    for bad, total in (([dict(segs[0]), dict(segs[0])], 3000.0), ([dict(segs[1]), dict(segs[1])], 3000.0), (segs, 1000.5)):
        with pytest.raises(ValueError):
            WTY.stack_heights(bad, total)
    sp = {"name": "s", "axis": [0.0, 0.0, 1.0, 0.0], "height": 3000.0, "line": "wall_center", "segments": segs, "parts": [IP1, IP2]}
    assert K.row_params_error("stackwall", sp) is None
    assert K.row_params_error("stackwall", dict(sp, parts=[IP1])) is not None
    assert K.row_params_error("stackwall", dict(sp, height=900.0)) is not None
    assert K.row_params_error("walltype", {"name": "a", "thickness": 100.0, "core": [0, 1]}) is not None   # 층 구성 없이 코어 없음
    assert K.row_params_error("wall", {"name": "w", "axis": [0, 0, 1, 0], "thickness": 1.0, "height": 1.0, "loc": "wall_center"}) is not None   # 중심선은 키 없음


# ---------------------------------------------------------------- 벽 유형
def test_유형_만들기_고치기_복제_지우기와_실행_취소(base):
    h0 = base.eng.state_hash()
    types(base)
    assert err(base.run("walltype.create", {"name": "외벽", "thickness": 100}))["path"] == "/name"            # 이름 중복
    assert err(base.run("walltype.create", {"name": "x", "layers": L4, "thickness": 300}))["path"] == "/thickness"   # 층 합과 다름
    assert err(base.run("walltype.create", {"name": "x", "thickness": 100, "core": [0, 1]}))["path"] == "/core"
    assert err(base.run("walltype.create", {"name": "x", "layers": L4, "core": [2, 5]}))["path"] == "/core"
    assert err(base.run("walltype.create", {"name": "x"}))["code"] == "CONSTRAINT"
    lst = ok(base.run("walltype.list", {}))
    assert [x["name"] for x in lst["items"]] == ["외벽", "칸막이"] and lst["untyped_walls"] == 3
    assert lst["items"][0]["thickness"] == 350.0 and lst["items"][0]["core"] == [2, 3] and lst["items"][1]["height"] == 2700.0
    env = base.run("walltype.list", {"limit": 1})
    assert len(ok(env)["items"]) == 1 and env["page"]["next_cursor"]
    rest = ok(base.run("walltype.list", {"cursor": env["page"]["next_cursor"]}))
    assert [x["name"] for x in rest["items"]] == ["칸막이"]
    d = ok(base.run("walltype.duplicate", {"id": T1, "name": "외벽 2"}))
    assert params(base, d["id"]) == dict(params(base, T1), name="외벽 2")
    ok(base.run("wall.type.set", {"ids": [F.W1], "type": T1}))
    e = err(base.run("walltype.delete", {"id": T1}))
    assert e["code"] == "CONSTRAINT" and e["details"]["walls"] == [F.W1]
    assert err(base.run("element.delete", {"id": T1}))["code"] == "CONSTRAINT"
    r = ok(base.run("walltype.delete", {"id": T1, "detach": True}))
    assert r["detached"] == 1 and "wtype" not in params(base, F.W1) and params(base, F.W1)["layers"][0]["material"] == "치장벽돌"   # 층 구성은 벽에 남는다
    assert ok(base.run("model.validate", {}))["errors"] == 0
    for _ in range(4):
        ok(base.run("history.undo", {}))
    assert base.eng.state_hash() != h0
    ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h0
    for _ in range(5):
        ok(base.run("history.redo", {}))
    assert "wtype" not in params(base, F.W1) and ok(base.run("model.stats", {}))["counts"]["walltype"] == 2


def test_유형_있는_벽_생성과_위치선(base):
    types(base)
    w = ok(base.run("wall.create", {"level": F.LV1, "axis": [0, 10000, 6000, 10000], "type": T1, "location_line": "finish_ext"}))["id"]
    p = params(base, w)
    assert p["axis"] == [0.0, 10175.0, 6000.0, 10175.0] and p["thickness"] == 350.0 and p["wtype"] == T1 and p["loc"] == "finish_ext" and p["core"] == [2, 3]
    assert [x["material"] for x in p["layers"]] == [x["material"] for x in L4] and p["height"] == 3000.0
    loc = ok(base.run("wall.location", {"ids": [w]}))["items"][0]
    assert loc["axis"] == [0.0, 10000.0, 6000.0, 10000.0] and loc["offset"] == -175.0 and loc["type"] == T1
    assert ok(base.run("relation.list", {"id": w}))   # 관계 조회가 터지지 않는다
    w2 = ok(base.run("wall.create", {"level": F.LV1, "axis": [0, 12000, 6000, 12000], "type": T2}))["id"]
    assert params(base, w2)["height"] == 2700.0 and params(base, w2)["axis"][1] == 12000.0   # 기본 높이·중심선
    assert err(base.run("wall.create", {"level": F.LV1, "axis": [0, 14000, 6000, 14000], "type": T2, "thickness": 150}))["path"] == "/thickness"
    w3 = ok(base.run("wall.create", {"level": F.LV1, "axis": [0, 16000, 6000, 16000], "thickness": 300, "location_line": "finish_int"}))["id"]
    assert params(base, w3)["axis"][1] == 15850.0 and "wtype" not in params(base, w3)
    assert err(base.run("wall.layers", {"id": w, "layers": [{"material": "a", "thickness": 100}]}))["code"] == "CONSTRAINT"
    assert err(base.run("wall.set", {"id": w2, "thickness": 150}))["path"] == "/thickness"
    ok(base.run("wall.set", {"id": w2, "height": 2600}))   # 유형과 무관한 값은 그대로 고친다
    assert ok(base.run("model.validate", {}))["errors"] == 0


def test_유형을_고치면_그_유형의_벽만_따라가고_위치선은_제자리(base):
    views(base)
    types(base)
    a = ok(base.run("wall.create", {"level": F.LV1, "axis": [0, 10000, 6000, 10000], "type": T1, "location_line": "finish_ext"}))["id"]
    b = ok(base.run("wall.create", {"level": F.LV1, "axis": [8000, 0, 8000, 6000], "type": T1, "location_line": "core_center"}))["id"]
    c = ok(base.run("wall.create", {"level": F.LV1, "axis": [0, 12000, 6000, 12000], "type": T2}))["id"]
    before_c, before_w1 = params(base, c), params(base, F.W1)
    loc_b = ok(base.run("wall.location", {"ids": [b]}))["items"][0]["axis"]
    same_as_full(base)
    h = base.eng.state_hash()
    r = ok(base.run("walltype.set", {"id": T1, "layers": [{"material": "철근콘크리트", "thickness": 250}, {"material": "석고보드", "thickness": 12}]}))
    assert r["walls"] == 2
    pa = params(base, a)
    assert pa["thickness"] == 262.0 and pa["axis"][1] == 10131.0 and "core" not in pa   # 바깥 마감면 y=10000 그대로
    assert ok(base.run("wall.location", {"ids": [b]}))["items"][0]["axis"] == loc_b       # 코어 중심선 그대로
    assert params(base, c) == before_c and params(base, F.W1) == before_w1
    same_as_full(base)
    assert ok(base.run("walltype.set", {"id": T1, "name": "외벽 RC"}))["walls"] == 0     # 이름만 바꾸면 벽은 그대로
    assert not ok(base.run("walltype.set", {"id": T1, "name": "외벽 RC"}))["changed"]
    ok(base.run("history.undo", {}))
    ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == h and params(base, a)["axis"][1] == 10175.0
    same_as_full(base)
    ok(base.run("history.redo", {}))
    assert params(base, a)["axis"][1] == 10131.0
    e = err(base.run("walltype.set", {"id": T2, "thickness": 50, "layers": L4}))   # 스키마: 함께 줄 수 있으나 합과 다르면 거절
    assert e["code"] == "CONSTRAINT"


def test_벽에_유형_입히기와_떼기_분할(base):
    types(base)
    r = ok(base.run("wall.type.set", {"ids": [F.W1, F.W3], "type": T1}))
    assert r["changed"] == 2
    p1 = params(base, F.W1)
    assert p1["thickness"] == 350.0 and p1["axis"] == [0.0, 0.0, 6000.0, 0.0] and p1["wtype"] == T1   # 중심선 위치선이라 축 그대로
    assert ok(base.run("element.get", {"id": F.W1})).get("material") == "철근콘크리트"   # 층 구성(가장 두꺼운 층)이 단일 재료를 대신한다
    assert sorted(ok(base.run("walltype.list", {}))["items"][0:1][0] and [x["walls"] for x in ok(base.run("walltype.list", {}))["items"]]) == [0, 2]
    sp = ok(base.run("wall.split", {"id": F.W3, "at": 1500}))
    n = sp.get("new") or sp.get("id") or [v for v in sp.values() if isinstance(v, str) and v != F.W3][0]
    assert params(base, n)["wtype"] == T1 and ok(base.run("model.validate", {}))["errors"] == 0
    assert [x["walls"] for x in ok(base.run("walltype.list", {}))["items"]][0] == 3
    ok(base.run("wall.type.set", {"ids": [F.W1], "type": None}))
    assert "wtype" not in params(base, F.W1) and params(base, F.W1)["thickness"] == 350.0
    ok(base.run("wall.layers", {"id": F.W1, "layers": None}))   # 뗀 뒤에는 층 구성을 직접 고친다
    assert ok(base.run("model.validate", {}))["errors"] == 0
    assert err(base.run("wall.type.set", {"ids": [F.W1], "type": "9" * 22}))["code"] == "NOT_FOUND"
    assert err(base.run("wall.type.set", {"ids": [F.D1], "type": T1}))["code"] == "NOT_FOUND"


def test_위치선_지정_몸체_고정과_선_고정(base):
    types(base)
    ok(base.run("wall.type.set", {"ids": [F.W1], "type": T1}))
    ok(base.run("wall.location.set", {"ids": [F.W1], "line": "finish_ext"}))                 # 몸체 그대로(Revit 과 같다)
    assert params(base, F.W1)["axis"] == [0.0, 0.0, 6000.0, 0.0]
    assert ok(base.run("wall.location", {"ids": [F.W1]}))["items"][0]["axis"] == [0.0, -175.0, 6000.0, -175.0]
    ok(base.run("wall.location.set", {"ids": [F.W1], "line": "core_ext", "keep": "line"}))    # 선 그대로: 코어 바깥 면이 y=-175 에
    assert ok(base.run("wall.location", {"ids": [F.W1]}))["items"][0]["axis"] == [0.0, -175.0, 6000.0, -175.0]
    assert params(base, F.W1)["axis"] == [0.0, -140.0, 6000.0, -140.0]
    ok(base.run("wall.location.set", {"ids": [F.W1], "line": "wall_center"}))
    assert "loc" not in params(base, F.W1)
    assert ok(base.run("model.validate", {}))["errors"] == 0


def test_곡선_벽의_유형과_위치선(base):
    types(base)
    r = ok(base.run("wall.arc", {"level": F.LV1, "points": [[0, 8000], [3000, 11000], [6000, 8000]], "type": T1, "location_line": "finish_ext"}))
    w = r["id"]
    p = params(base, w)
    assert "arc" in p and p["wtype"] == T1
    loc = ok(base.run("wall.location", {"ids": [w]}))["items"][0]
    assert loc["axis"] == pytest.approx([0.0, 8000.0, 6000.0, 8000.0], abs=1e-5) and loc["arc"] == pytest.approx([3000.0, 11000.0], abs=1e-5)
    ok(base.run("door.place", {"host": w, "offset": 2000, "width": 900, "height": 2100}))
    ok(base.run("walltype.set", {"id": T1, "layers": [{"material": "철근콘크리트", "thickness": 250}]}))
    loc2 = ok(base.run("wall.location", {"ids": [w]}))["items"][0]
    assert loc2["axis"] == pytest.approx(loc["axis"], abs=1e-5) and loc2["arc"] == pytest.approx(loc["arc"], abs=1e-5)   # 바깥 마감 호 제자리
    assert ok(base.run("model.validate", {}))["errors"] == 0


# ---------------------------------------------------------------- 스택 벽
def stack(s, **kw):
    a = {"id": SW, "level": F.LV1, "axis": [0, -3000, 6000, -3000], "height": 3000, "line": "finish_ext",
         "segments": [{"type": T1, "height": 1000}, {"type": T2}]}
    a.update(kw)
    return ok(s.run("stackwall.create", a))


def _ys(lines, cls=None):
    return {round(ln[2], 3) for ln in lines if abs(ln[2] - ln[4]) < 1e-9 and (cls is None or ln[0] == cls)}


def test_스택_벽_부분_벽과_도면(base):
    views(base)
    types(base)
    h0 = base.eng.state_hash()
    r = stack(base)
    lo, hi = r["parts"]
    pl, ph = params(base, lo), params(base, hi)
    assert pl["stack"] == SW and pl["wtype"] == T1 and pl["height"] == 1000.0 and "base_offset" not in pl and pl["axis"][1] == -2825.0
    assert ph["wtype"] == T2 and ph["height"] == 2000.0 and ph["base_offset"] == 1000.0 and ph["axis"][1] == -2950.0
    st = ok(base.run("model.stats", {}))["counts"]
    assert st["stackwall"] == 1 and st["wall"] == 5
    g = g_of(base)
    plan = g.q(D.view_lines, V)
    cut = _ys(plan, "cut")
    assert -3000.0 in cut and -2900.0 in cut and -2650.0 not in cut                    # 절단 높이 1200: 위 구간(칸막이 100)만 자른다
    assert -2650.0 in _ys(plan)                                                         # 아래 구간(350) 안쪽 면은 투영선
    sec = g.q(D.view_lines, VS)
    assert sec and any(ln[0] == "cut" for ln in sec)
    same_as_full(base)
    # 부분 벽은 직접 고치지 않는다(스택 벽 커맨드로), 문은 놓을 수 있다
    for name, args in (("wall.move", {"id": hi, "dx": 10}), ("element.delete", {"id": hi}), ("wall.set", {"id": hi, "height": 100}),
                       ("wall.type.set", {"ids": [hi], "type": T1}), ("wall.location.set", {"ids": [hi], "line": "wall_center"}),
                       ("element.copy", {"ids": [hi], "dy": -500}), ("element.rotate", {"ids": [hi], "center": [0, 0], "angle": 90})):
        r2 = base.run(name, args)
        assert not r2["ok"] and r2["error"]["code"] in ("CONSTRAINT", "SCHEMA_INVALID", "NOT_FOUND"), (name, r2)
    d = ok(base.run("door.place", {"host": hi, "offset": 3000, "width": 900, "height": 1500, "sill": 1100}))["id"]
    # 고치기: 구간 높이·유형·offset, 구간 수
    ok(base.run("stackwall.set", {"id": SW, "segments": [{"type": T1, "height": 800, "offset": 0}, {"type": T2, "offset": -20}]}))
    pl, ph = params(base, lo), params(base, hi)
    assert pl["height"] == 800.0 and ph["base_offset"] == 800.0 and ph["height"] == 2200.0 and ph["axis"][1] == -2970.0
    e = err(base.run("stackwall.set", {"id": SW, "segments": [{"type": T1, "height": 2000}, {"type": T2}]}))   # 문이 새 구간 밖
    assert e["code"] == "CONSTRAINT"
    r3 = ok(base.run("stackwall.set", {"id": SW, "segments": [{"type": T1, "height": 800}, {"type": T2, "height": 1800}, {"type": T1}]}))
    assert len(r3["parts"]) == 3 and r3["parts"][:2] == [lo, hi]
    top = r3["parts"][2]
    assert params(base, top)["base_offset"] == 2600.0 and params(base, top)["height"] == 400.0
    same_as_full(base)
    ok(base.run("walltype.set", {"id": T1, "layers": [{"material": "철근콘크리트", "thickness": 200}]}))   # 유형 고치기가 부분 벽에도: 정렬선 제자리
    assert params(base, lo)["axis"][1] == -2900.0 and params(base, top)["axis"][1] == -2900.0
    assert err(base.run("walltype.delete", {"id": T1, "detach": True}))["code"] == "CONSTRAINT"   # 스택 벽이 쓴다
    assert ok(base.run("model.validate", {}))["errors"] == 0
    # 지우기: 부분 벽과 문까지, 되돌리면 그대로
    hb = base.eng.state_hash()
    gone = ok(base.run("element.delete", {"id": SW}))["deleted"]
    assert set(gone) == {SW, lo, hi, top, d}
    assert ok(base.run("model.stats", {}))["counts"]["wall"] == 3
    ok(base.run("history.undo", {}))
    assert base.eng.state_hash() == hb
    same_as_full(base)
    for _ in range(5):   # 유형 고치기·스택 고치기 2·문·스택 만들기
        ok(base.run("history.undo", {}))
    assert ok(base.run("model.stats", {}))["counts"].get("stackwall") is None
    assert base.eng.state_hash() == h0
    same_as_full(base)


def test_곡선_스택_벽(base):
    types(base)
    r = ok(base.run("stackwall.create", {"id": SW, "level": F.LV1, "axis": [0, -3000, 6000, -3000], "arc_through": [3000, -5000], "height": 3000,
                                          "line": "core_center", "segments": [{"type": T1, "height": 1200}, {"type": T2}]}))
    for w in r["parts"]:
        assert "arc" in params(base, w)
        loc = ok(base.run("wall.location", {"ids": [w]}))["items"][0]
        assert loc["axis"] == pytest.approx([0.0, -3000.0, 6000.0, -3000.0], abs=1e-5)   # 정렬선(코어 중심) 호가 같다
    assert ok(base.run("model.validate", {}))["errors"] == 0


# ---------------------------------------------------------------- IFC
def _ifc_errors(ff):
    import ifcopenshell.validate as IV
    lg = IV.json_logger()
    IV.validate(ff, lg, express_rules=True)
    return [x["message"][:300] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]]


def _build_ifc_case(b, project="p.dobim"):
    S.build_authored(b, project)
    ok(b.run("walltype.create", {"id": IT1, "name": "외벽", "layers": L4, "core": [2, 3], "function": "exterior"}, project=project))
    ok(b.run("walltype.create", {"id": IT2, "name": "칸막이", "thickness": 100}, project=project))
    ok(b.run("walltype.create", {"name": "쓰지 않는 유형", "layers": [{"material": "목재", "thickness": 30}]}, project=project))
    ok(b.run("wall.type.set", {"ids": [F.W1, F.W2], "type": IT1}, project=project))
    ok(b.run("wall.location.set", {"ids": [F.W2], "line": "finish_ext"}, project=project))
    ok(b.run("stackwall.create", {"id": ISW, "level": F.LV1, "axis": [0, -3000, 6000, -3000], "height": 3000, "line": "finish_ext",
                                  "segments": [{"type": IT1, "height": 1000}, {"type": IT2}], "part_ids": [IP1, IP2]}, project=project))


def test_IFC_벽_유형과_스택_벽_검증과_왕복(tmp_path):
    import ifcopenshell
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        h_plain = b.hashes()
        plain = b.export("plain.ifc").read_bytes()
        ok(b.run("walltype.create", {"id": IT1, "name": "외벽", "layers": L4, "core": [2, 3], "function": "exterior"}))
        ok(b.run("walltype.create", {"id": IT2, "name": "칸막이", "thickness": 100}))
        ok(b.run("walltype.create", {"name": "쓰지 않는 유형", "layers": [{"material": "목재", "thickness": 30}]}))
        ok(b.run("wall.type.set", {"ids": [F.W1, F.W2], "type": IT1}))
        ok(b.run("wall.location.set", {"ids": [F.W2], "line": "finish_ext"}))
        ok(b.run("stackwall.create", {"id": ISW, "level": F.LV1, "axis": [0, -3000, 6000, -3000], "height": 3000, "line": "finish_ext",
                                      "segments": [{"type": IT1, "height": 1000}, {"type": IT2}], "part_ids": [IP1, IP2]}))
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        wt = {t.Name: t for t in f.by_type("IfcWallType")}
        assert set(wt) == {"외벽", "칸막이", "쓰지 않는 유형"} and all(t.PredefinedType == "STANDARD" for t in wt.values()) and wt["외벽"].ElementType == "exterior"
        rel = [r for r in f.by_type("IfcRelDefinesByType") if r.RelatingType == wt["외벽"]]
        assert len(rel) == 1 and sorted(o.GlobalId for o in rel[0].RelatedObjects) == sorted([F.W1, F.W2, IP1])
        assert not [r for r in f.by_type("IfcRelDefinesByType") if r.RelatingType == wt["쓰지 않는 유형"]]
        ls = [r.RelatingMaterial for r in f.by_type("IfcRelAssociatesMaterial") if wt["외벽"] in r.RelatedObjects]
        assert len(ls) == 1 and ls[0].is_a("IfcMaterialLayerSet") and [(ly.Material.Name, ly.LayerThickness) for ly in ls[0].MaterialLayers] == [
            (x["material"], float(x["thickness"])) for x in L4]
        sw = f.by_guid(ISW)
        assert sw.is_a("IfcWall") and sw.Representation is None and [k.GlobalId for r in sw.IsDecomposedBy for k in r.RelatedObjects] == [IP1, IP2]
        for pid in (IP1, IP2):   # 부분 벽은 층에 담지 않는다(전체만 담긴다)
            assert not f.by_guid(pid).ContainedInStructure and f.by_guid(pid).Representation is not None
        assert sw.ContainedInStructure and f.by_guid(F.W1).ContainedInStructure
        assert _ifc_errors(f) == []
        assert b.destructive("ifc.export", {"path": "t2x3.ifc", "schema": "IFC2X3"})["ok"]
        f2 = ifcopenshell.open(str(tmp_path / "t2x3.ifc"))
        assert f2.schema == "IFC2X3" and _ifc_errors(f2) == [] and len(f2.by_type("IfcWallType")) == 3
        # 가져오기: 유형·스택 벽이 되살아나고 해시·바이트가 같다
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("t3.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        lst = ok(b.run("walltype.list", {}, project="imp.dobim"))
        assert {x["name"]: x["walls"] for x in lst["items"]} == {"외벽": 3, "칸막이": 1, "쓰지 않는 유형": 0}
        # 가져온 프로젝트에서 고치고 되돌리기
        ok(b.run("walltype.set", {"id": IT1, "layers": [{"material": "철근콘크리트", "thickness": 250}]}, project="imp.dobim"))
        f3 = ifcopenshell.open(str(b.export("t4.ifc", "imp.dobim")))
        t3 = next(t for t in f3.by_type("IfcWallType") if t.Name == "외벽")
        ls3 = [r.RelatingMaterial for r in f3.by_type("IfcRelAssociatesMaterial") if t3 in r.RelatedObjects]
        assert [ly.LayerThickness for ly in ls3[0].MaterialLayers] == [250.0] and _ifc_errors(f3) == []
        ok(b.run("history.undo", {}, project="imp.dobim"))
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        ok(b.run("element.delete", {"id": ISW}, project="imp.dobim"))
        ok(b.run("walltype.delete", {"id": IT2}, project="imp.dobim"))
        h_del = b.hashes("imp.dobim")
        f4 = ifcopenshell.open(str(b.export("t5.ifc", "imp.dobim")))
        assert {t.Name for t in f4.by_type("IfcWallType")} == {"외벽", "쓰지 않는 유형"} and _ifc_errors(f4) == []
        b2 = S.Bench(tmp_path)   # 가져온 뒤 지운 유형: 다시 열어도 같은 IFC
        try:
            assert S.trip(b2.hashes("imp.dobim")) == S.trip(h_del)
        finally:
            b2.close()
        # IFC2X3 파일 가져오기
        r2 = b.import_ifc("t2x3.ifc", "imp2.dobim")
        assert r2["ok"], r2
        assert len(ok(b.run("walltype.list", {}, project="imp2.dobim"))["items"]) == 3
        # 원래 프로젝트를 되돌리면 유형을 쓰지 않은 상태의 해시·바이트
        for _ in range(6):
            ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h_plain) and plain   # 되돌리면 의미 해시가 같다(엔티티 번호는 새로 매겨진다)
    finally:
        b.close()


def test_다시_연_프로젝트는_같은_IFC_해시(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build_ifc_case(b)
        ok(b.run("walltype.set", {"id": IT1, "layers": [{"material": "철근콘크리트", "thickness": 240}, {"material": "석고보드", "thickness": 10}]}))
        h = b.hashes()
        b.close()
        b2 = S.Bench(tmp_path)
        assert S.trip(b2.hashes()) == S.trip(h)
        b2.close()
    finally:
        b.close()


def test_유형을_쓰지_않으면_응답이_그대로다(base):
    st = ok(base.run("model.stats", {}))["counts"]
    assert "walltype" not in st and "stackwall" not in st
    w = ok(base.run("wall.create", {"level": F.LV1, "axis": [0, 9000, 5000, 9000]}))
    assert set(w) == {"id"} and set(params(base, w["id"])) == {"name", "axis", "thickness", "height"}
