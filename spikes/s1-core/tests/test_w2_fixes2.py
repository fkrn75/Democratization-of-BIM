# SPDX-License-Identifier: MPL-2.0
"""W2 실사용 결함 수정 2차(성능 재확인 v2: 건물 3동·장비 6종 재모델링에서 나온 결함 8건)의 최소 재현 시험.

1. 외곽선 지붕(roofskel)이 고리 부채꼴·다점 부채꼴(반올림된 좌표의 원호 꺾은선)을 "경사면 사이에 틈"으로 거절했다.
   원인: 거의 평행한 변들이 거의 같은 높이에서 만나는 사건(반올림 때문에 동시가 아니다)을 정리할 때 (a) 넓이로 사라졌다고 본 고리를 꼭짓점마다
   다른 점으로 닫아 윗변 구멍이 남고, (b) 움직이는 꼭짓점의 위치를 움직이지 않는 선(박공·연결 변) 위의 정확한 점 대신 썼고, (c) 같은 높이에서
   생겼다 끝난 꼭짓점의 호를 버렸고, (d) 이미 엇갈린 짧은 변을 합치지 않아 같은 높이에서 끝없이 돌았다. 무작위 부채꼴·고리·원호 다각형 퍼즈 시험 포함
2. 층 구성 지붕(rooftype / roof.layers)의 경사면 IfcSlab·처마 부속에 재료 관계가 없었다 -> 층 집합을 부속에도. 처마 부속 종류별 재료(roof.eaves.set material)
3. model.stats 의 door 요약이 counts.door 와 달랐다(패밀리 문·커튼월 문 패널 빠짐) -> door 요약·counts.door = IFC 의 IfcDoor 수
4. curtain.parts 가 한 쪽에 약 36개라 원자 script 에서 다 받을 수 없었다 -> offset·compact·summary=false, 잘림 표시·다음 커서·next_offset
5. roof.eaves.set 의 edges 가 시계 방향 외곽을 뒤집어 저장한 순서라 경고 없이 엉뚱한 변에 붙었다 -> 경고·numbering=input·roof.eaves 의 input_edge
6. 독립 개구(opening.*)에 pset.set 을 거절했다 -> 속성을 의도 행에 두고 IFC IfcOpeningElement 부속에 투영(왕복)
7. family.register 가 USERDEFINED 인데 ElementType/ObjectType 이 없는 레시피를 받았다(family.validate full 은 거절) -> 같은 스키마 대조
8. railing.from_stair 로 만든 난간 id 를 원자 script 가 알 수 없어 재료를 줄 수 없었다 -> material 인자, 응답·설명 보강
"""
import copy
import math
import random
import sys

import ifcopenshell
import ifcopenshell.util.element as ue
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import fixture as F
from dobim_core import limits
from dobim_core import roofskel as RS
from dobim_core.family import library
from dobim_core.ifc_model import part_guid

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

RF = F.RF1
RT = F.RT1
CW = F.CW1
OP = F.OP1
IRT, IRF, IOP = ("3" + "0" * 19 + x for x in ("u1", "r1", "o1"))   # IFC 왕복 시험용: 첫 글자가 유효한 GlobalId


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [s["message"][:160] for s in lg.statements if s["level"] in ("error", "critical")]


def T(deg):
    return math.tan(math.radians(deg))


def arc_pt(r, deg, nd=1):
    """반지름 r, 각 deg 의 점을 nd 자리로 반올림(도면에서 읽은 좌표처럼)."""
    return [round(r * math.cos(math.radians(deg)), nd), round(r * math.sin(math.radians(deg)), nd)]


def annulus(ro, ri, a0, a1, step, nd=1):
    """고리 부채꼴(바깥 호 a0 -> a1, 안쪽 호 되돌아옴): 반시계 외곽."""
    n = round((a1 - a0) / step)
    outer = [arc_pt(ro, a0 + step * i, nd) for i in range(n + 1)]
    inner = [arc_pt(ri, a0 + step * i, nd) for i in range(n, -1, -1)]
    return outer + inner


# ---------------------------------------------------------------- 1. 외곽선 지붕: 고리 부채꼴·다점 부채꼴
@pytest.mark.parametrize("step", [10, 5, 30])
@pytest.mark.parametrize("mode", ["p0.1", "p30", "inner30"])
def test_1_고리_부채꼴을_한_장으로_받는다(step, mode):
    b = annulus(20000, 10000, 0, 90, step)
    n = len(b)
    ks = {"p0.1": [T(0.1)] * n, "p30": [T(30)] * n, "inner30": [0.0] * (n // 2) + [T(30)] * (n // 2 - 1) + [0.0]}[mode]
    assert RS.problem(b, ks, [0.0] * n) is None


def test_1_고리_부채꼴의_용마루_높이와_면_수():
    b = annulus(20000, 10000, 0, 90, 10)
    n = len(b)
    g = RS.geom(b, [30.0] * n, [T(30)] * n, [0.0] * n, 0.0)
    assert len(g["faces"]) == n   # 변마다 경사면 하나
    want = (20000 - 10000) * math.cos(math.radians(5)) / 2 * T(30)   # 바깥·안쪽 현의 가운데까지 거리 x 물매
    assert abs(g["ztop"] - want) < 0.5


def test_1_다점_부채꼴_안쪽_호만_물매():
    """안쪽 호 10변만 물매 12.7도(바깥 호·옆 변은 박공)인 22점 부채꼴: 하나로 받는다(전에는 2~4장으로 나눠야 했다)."""
    a = [-23.2 + 8.72 * i for i in range(11)]
    inner = [arc_pt(17350, x) for x in a]
    outer = [arc_pt(24250, x) for x in a]
    b = outer + inner[::-1]
    n = len(b)
    ks = [T(12.7) if i >= 11 and i < n - 1 else 0.0 for i in range(n)]   # 안쪽 호 변(꼭짓점 11 -> 21)
    assert sum(1 for k in ks if k) == 10
    assert RS.problem(b, ks, [0.0] * n) is None
    g = RS.geom(b, [math.degrees(math.atan(k)) for k in ks], ks, [0.0] * n, 0.0)
    assert len(g["faces"]) == 10 and g["ztop"] > 0


def test_1_원호_지붕을_명령으로_만든다(base):
    b = annulus(20000, 10000, 0, 90, 10)
    r = ok(base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": b, "form": "footprint", "pitch": 30, "thickness": 100}))
    assert r["id"] == RF
    v = ok(base.run("model.validate", {}))
    assert v["errors"] == 0, v["violations"]


def _fuzz_case(rng):
    """무작위 고리 부채꼴·부채꼴(중심 꼭짓점)·D 꼴(원호 + 현)·직사각형 + 반원. 좌표는 반올림(없음·1mm·0.1mm)."""
    kind = rng.choice(["ann", "pie", "dshape", "rectarc"])
    nd = rng.choice([None, 0, 1])
    rd = (lambda v: v) if nd is None else (lambda v: float(round(v, nd)) if nd else float(round(v)))
    P = lambda r, t: (rd(r * math.cos(t)), rd(r * math.sin(t)))
    a0 = rng.uniform(-math.pi, math.pi)
    nseg = rng.randint(2, 24)
    ro = rng.uniform(3000, 40000)
    inner = set()
    if kind == "ann":
        span = rng.uniform(math.radians(20), math.radians(300))
        angs = [a0 + span * i / nseg for i in range(nseg + 1)]
        ri = ro * rng.uniform(0.2, 0.85)
        b = [P(ro, a) for a in angs] + [P(ri, a) for a in angs[::-1]]
        inner = set(range(nseg + 1, 2 * nseg + 1))
    elif kind == "pie":
        span = rng.uniform(math.radians(20), math.radians(170))
        b = [P(ro, a0 + span * i / nseg) for i in range(nseg + 1)] + [(rd(0.0), rd(0.0))]
    elif kind == "dshape":
        span = rng.uniform(math.radians(20), math.radians(180))
        b = [P(ro, a0 + span * i / nseg) for i in range(nseg + 1)]
    else:
        d = rng.uniform(3000, 30000)
        b = [(rd(ro / 2), 0.0)] + [(rd(ro / 2 * math.cos(math.pi * i / nseg)), rd(d + ro / 2 * math.sin(math.pi * i / nseg))) for i in range(nseg + 1)] + [(rd(-ro / 2), 0.0)]
    area2 = sum(b[i][0] * b[(i + 1) % len(b)][1] - b[(i + 1) % len(b)][0] * b[i][1] for i in range(len(b)))
    if area2 < 0:
        b = b[::-1]
        inner = {(len(b) - 2 - i) % len(b) for i in inner}
    n = len(b)
    p = rng.choice([0.1, 5, 12.7, 30, 45, 60])
    mode = rng.choice(["all", "all", "inner", "mixed"])
    if mode == "inner" and inner:
        ks = [T(p) if i in inner else 0.0 for i in range(n)]
    elif mode == "mixed":
        ks = [T(rng.choice([p, p, 20.0])) for _ in range(n)]
    else:
        ks = [T(p)] * n
    os_ = [0.0] * n if rng.random() < 0.6 else [rng.choice([0.0, 300.0, 600.0])] * n
    return kind, b, ks, os_


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_1_무작위_원호_다각형_퍼즈(seed):
    """원호를 꺾은선으로 근사한 외곽(반올림 좌표)을 무작위로 만들어 거절이 없어야 한다. 처마를 내민 외곽이 꼬이는 것(입력 문제)과
    한 직선 위 꼭짓점(입력 검사)은 뺀다. 결과는 _finish 가 덮음·높이 이음을 검사하므로 받았으면 형상도 맞다."""
    rng = random.Random(seed)
    bad = []
    for it in range(150):
        kind, b, ks, os_ = _fuzz_case(rng)
        why = RS.problem(b, ks, os_)
        if why is not None and "꼬인다" not in why and "한 직선" not in why:
            bad.append((it, kind, len(b), why[:40]))
    assert bad == []


# ---------------------------------------------------------------- 2. 층 구성 지붕 부속의 재료
L2 = [{"material": "기와", "thickness": 50}, {"material": "합판", "thickness": 150}]
RECT = [[0, 0], [8000, 0], [8000, 5000], [0, 5000]]


def _mats_of(f, e):
    return [r.RelatingMaterial for r in f.by_type("IfcRelAssociatesMaterial") if e in r.RelatedObjects]


def test_2_층_구성_지붕의_경사면과_처마_부속이_층_집합을_갖는다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        h0 = b.hashes()
        ok(b.run("rooftype.create", {"id": IRT, "name": "기와 200", "layers": L2}))
        ok(b.run("roof.create", {"id": IRF, "level": F.LV2, "boundary": RECT, "form": "hip", "pitch": 30, "overhang": 500, "type": IRT}))
        ok(b.run("roof.eaves.set", {"id": IRF, "fascia": {"edges": "all"}, "soffit": {"edges": [0, 2], "material": "목재 반자"}}))
        h = b.hashes()
        f = ifcopenshell.open(str(b.export("l.ifc")))
        assert file_errors(f) == []
        kids = [k for r in f.by_guid(IRF).IsDecomposedBy for k in r.RelatedObjects]
        slabs = [k for k in kids if k.is_a("IfcSlab")]
        fas = [k for k in kids if k.is_a("IfcCovering") and k.ObjectType == "FASCIA"]
        sof = [k for k in kids if k.is_a("IfcCovering") and k.ObjectType == "SOFFIT"]
        assert slabs and fas and sof
        for k in slabs + fas:
            m = _mats_of(f, k)
            assert len(m) == 1 and m[0].is_a("IfcMaterialLayerSet"), k
            assert [(ly.Material.Name, ly.LayerThickness) for ly in m[0].MaterialLayers] == [("기와", 50.0), ("합판", 150.0)]
        for k in sof:   # 종류별 재료를 준 처마 반자
            m = _mats_of(f, k)
            assert len(m) == 1 and m[0].is_a("IfcMaterial") and m[0].Name == "목재 반자"
        f2 = ifcopenshell.open(str(_x3(b, tmp_path)))
        assert file_errors(f2) == []
        r = b.import_ifc("l.ifc", "imp.dobim")
        assert r["ok"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        # 층 구성을 바꾸면 부속도 새 층 집합을 따라가고, 되돌리면 같은 IFC
        ok(b.run("rooftype.set", {"id": IRT, "layers": [{"material": "금속", "thickness": 30}, {"material": "단열", "thickness": 170}]}))
        f3 = ifcopenshell.open(str(b.export("l3.ifc")))
        k3 = [k for r in f3.by_guid(IRF).IsDecomposedBy for k in r.RelatedObjects if k.is_a("IfcSlab")]
        assert [ly.Material.Name for ly in _mats_of(f3, k3[0])[0].MaterialLayers] == ["금속", "단열"]
        assert len([s for s in f3.by_type("IfcMaterialLayerSet") if s.Description == "DoBIM layers"]) == len({s.id() for s in f3.by_type("IfcMaterialLayerSet") if s.Description == "DoBIM layers"})
        assert file_errors(f3) == []
        ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h)
        for _ in range(3):
            ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h0)
    finally:
        b.close()


def _x3(b, tmp_path):
    assert b.destructive("ifc.export", {"path": "l2.ifc", "schema": "IFC2X3"})["ok"]
    return tmp_path / "l2.ifc"


def test_2_단순_재료_지붕은_이전과_같다(base):
    """층 구성 없는 지붕의 부속 재료 지정은 바뀌지 않는다(관계 하나, 단순 재료)."""
    ok(base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": RECT, "form": "gable", "pitch": 30, "overhang": 500}))
    ok(base.run("material.set", {"id": RF, "name": "기와"}))
    ok(base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": [0, 2]}}))
    base.eng.flush_ifc()
    f = base.eng.ifc.f
    rel = f.by_guid(part_guid(RF, "material"))
    assert rel.RelatingMaterial.is_a("IfcMaterial") and rel.RelatingMaterial.Name == "기와"
    assert len(rel.RelatedObjects) == len([p for p in base.eng.ifc.parts[RF].values()])


def test_2_처마_부속_재료_인자_검사(base):
    ok(base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": RECT, "form": "gable", "pitch": 30, "overhang": 500}))
    ok(base.run("roof.eaves.set", {"id": RF, "gutter": {"edges": [0], "material": "아연"}}))
    s = ok(base.run("roof.eaves", {"id": RF}))["settings"]
    assert s["gutter"]["material"] == "아연"
    ok(base.run("roof.eaves.set", {"id": RF, "gutter": {"edges": [0]}}))   # 재료를 빼면 지붕 재료를 따른다
    assert "material" not in ok(base.run("roof.eaves", {"id": RF}))["settings"]["gutter"]


# ---------------------------------------------------------------- 3. model.stats door 요약
def test_3_door_요약은_counts와_같고_IFC_IfcDoor_수와_같다(base, tmp_path):
    st0 = ok(base.run("model.stats", {}))
    assert st0["door"]["count"] == st0["counts"]["door"] and "by_source" not in st0["door"]   # 저작 문만: 응답 모양은 이전과 같다
    ok(base.run("family.place", {"family": "door.single", "host": F.W3, "offset": 1500}))
    ok(base.run("curtain.create", {"id": CW, "level": F.LV1, "axis": [0, -2000, 6000, -2000], "height": 3000,
                                   "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "count", "value": 2}}))
    keys = [x["key"] for x in ok(base.run("curtain.parts", {"id": CW, "what": "panels"}))["items"] if x["z0"] == 0][:2]
    ok(base.run("curtain.panel.set", {"id": CW, "keys": keys, "type": "door"}))
    st = ok(base.run("model.stats", {}))
    assert st["counts"]["door"] == st0["counts"]["door"] + 3
    assert st["door"]["count"] == st["counts"]["door"]
    assert st["door"]["by_source"] == {"door": st0["door"]["count"], "family": 1, "curtain_panel": 2}
    assert st["door"]["total_width"] > st0["door"]["total_width"] + 2 * 1400
    base.eng.flush_ifc()
    assert len(base.eng.ifc.f.by_type("IfcDoor")) == st["counts"]["door"]


# ---------------------------------------------------------------- 4. curtain.parts 쪽 나누기
def _big_curtain(s):
    ok(s.run("curtain.create", {"id": CW, "level": F.LV1, "axis": [0, -2000, 60000, -2000], "height": 12000,
                                "u_grid": {"rule": "spacing", "value": 500}, "v_grid": {"rule": "count", "value": 10}}))


def test_4_큰_커튼월의_패널을_전부_받는다(base):
    _big_curtain(base)
    r = base.run("curtain.parts", {"id": CW, "what": "panels", "limit": 5000})
    assert r["ok"] and r["page"]["truncated"] and r["page"]["next_cursor"]
    total = r["page"]["total"]
    n_plain = len(r["result"]["items"])
    assert total == 1200 and n_plain < total
    c = base.run("curtain.parts", {"id": CW, "what": "panels", "limit": 5000, "compact": True, "summary": False, "offset": 0})
    assert c["ok"] and "summary" not in c["result"] and c["result"]["columns"][0] == "key"
    assert len(c["result"]["items"]) >= 2 * n_plain
    assert limits.est_tokens(c) <= 2 * limits.RESPONSE_CAP_TOKENS
    keys, off, calls = [], 0, 0
    while off is not None:   # 원자 script 처럼 offset 으로 차례로
        calls += 1
        q = ok(base.run("curtain.parts", {"id": CW, "what": "panels", "limit": 5000, "compact": True, "summary": False, "offset": off}))
        keys += [row[0] for row in q["items"]]
        off = q["next_offset"]
    assert len(keys) == len(set(keys)) == total and calls <= total // (2 * n_plain) + 1
    cur, got = None, 0   # 커서로도 끝까지
    while True:
        q = base.run("curtain.parts", {"id": CW, "what": "mullions", "compact": True, "summary": False, "limit": 5000, **({"cursor": cur} if cur else {})})
        got += len(q["result"]["items"])
        cur = q["page"]["next_cursor"]
        if not cur:
            break
    assert got == q["page"]["total"]
    assert err(base.run("curtain.parts", {"id": CW, "what": "panels", "offset": 0, "cursor": c["page"]["next_cursor"]}))["code"] == "CONSTRAINT"


def test_4_기본_응답은_이전과_같다(base):
    ok(base.run("curtain.create", {"id": CW, "level": F.LV1, "axis": [0, -2000, 6000, -2000], "height": 3000,
                                   "u_grid": {"rule": "spacing", "value": 1500}, "v_grid": {"rule": "count", "value": 2}}))
    r = ok(base.run("curtain.parts", {"id": CW, "what": "mullions", "limit": 3}))
    assert set(r) == {"summary", "items"} and isinstance(r["items"][0], dict)


# ---------------------------------------------------------------- 5. 처마 부속 변 번호
CW_RECT = [[0, 0], [0, 5000], [8000, 5000], [8000, 0]]   # 시계 방향(입력 변 0 = 서쪽, 1 = 북쪽, 2 = 동쪽, 3 = 남쪽)


def test_5_시계_방향_외곽의_처마_변_번호(base):
    r = base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": CW_RECT, "form": "hip", "pitch": 30, "overhang": 500})
    assert r["ok"] and any("시계 방향" in w for w in r["warnings"])
    w = base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": [1]}})   # 저장 번호(기본): 경고한다
    assert w["ok"] and any("numbering=input" in x for x in w["warnings"])
    e = ok(base.run("roof.eaves", {"id": RF}))["edges"]
    on = [x for x in e if "fascia" in x["on"]]
    assert len(on) == 1 and on[0]["input_edge"] == 1 and on[0]["edge"] == 1   # 4각형: 저장 1 = 입력 4-2-1 = 1
    ok(base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": [0]}, "numbering": "input"}))   # 입력 변 0(서쪽)
    e = ok(base.run("roof.eaves", {"id": RF}))["edges"]
    on = [x for x in e if "fascia" in x["on"]]
    assert len(on) == 1 and on[0]["input_edge"] == 0 and {tuple(on[0]["from"]), tuple(on[0]["to"])} == {(0.0, 0.0), (0.0, 5000.0)}


def test_5_시계_방향_표시와_처마_재료가_IFC로_왕복한다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("roof.create", {"id": IRF, "level": F.LV2, "boundary": CW_RECT, "form": "hip", "pitch": 30, "overhang": 500}))
        ok(b.run("roof.eaves.set", {"id": IRF, "fascia": {"edges": [0], "material": "금속 널"}, "numbering": "input"}))
        h = b.hashes()
        out = b.export("c.ifc")
        assert file_errors(ifcopenshell.open(str(out))) == []
        r = b.import_ifc("c.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert ok(b.run("element.get", {"id": IRF}, project="imp.dobim"))["params"]["cw_input"] is True
    finally:
        b.close()


def test_5_반시계_외곽은_이전과_같다(base):
    r = ok(base.run("roof.create", {"id": RF, "level": F.LV2, "boundary": RECT, "form": "hip", "pitch": 30, "overhang": 500}))
    assert "cw_input" not in ok(base.run("element.get", {"id": RF}))["params"]
    w = base.run("roof.eaves.set", {"id": RF, "fascia": {"edges": [1]}, "numbering": "input"})
    assert w["ok"] and not w["warnings"]
    assert "input_edge" not in ok(base.run("roof.eaves", {"id": RF}))["edges"][0]
    assert r["id"] == RF


# ---------------------------------------------------------------- 6. 독립 개구의 속성셋
def test_6_독립_개구에_속성을_쓰고_IFC로_왕복한다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        h0 = b.hashes()
        ok(b.run("opening.wall", {"id": IOP, "host": F.W2, "offset": 2800, "sill": 900, "width": 800, "height": 1200}))
        ok(b.run("pset.set", {"id": IOP, "pset": "Pset_OpeningElementCommon", "prop": "Purpose", "value": "배관 관통"}))
        ok(b.run("pset.set", {"id": IOP, "pset": "Pset_OpeningElementCommon", "prop": "FireExit", "value": False}))
        g = ok(b.run("element.get", {"id": IOP}))
        assert "Purpose" in str(g) and "배관 관통" in str(g)
        h = b.hashes()
        f = ifcopenshell.open(str(b.export("o.ifc")))
        assert file_errors(f) == []
        op = f.by_guid(part_guid(F.W2, f"void:{IOP}"))
        assert op.is_a("IfcOpeningElement")
        assert ue.get_pset(op, "Pset_OpeningElementCommon")["Purpose"] == "배관 관통"
        assert b.destructive("ifc.export", {"path": "o2x3.ifc", "schema": "IFC2X3"})["ok"]
        assert file_errors(ifcopenshell.open(str(tmp_path / "o2x3.ifc"))) == []
        r = b.import_ifc("o.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("o2.ifc", "imp.dobim").read_bytes() == (tmp_path / "o.ifc").read_bytes()
        ok(b.run("pset.delete", {"id": IOP, "pset": "Pset_OpeningElementCommon", "prop": "Purpose"}, project="imp.dobim"))
        f3 = ifcopenshell.open(str(b.export("o3.ifc", "imp.dobim")))
        assert "Purpose" not in ue.get_pset(f3.by_guid(part_guid(F.W2, f"void:{IOP}")), "Pset_OpeningElementCommon")
        for _ in range(3):
            ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h0)
    finally:
        b.close()


def test_6_슬래브_수직_개구의_속성(base):
    ok(base.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200}))
    ok(base.run("opening.vertical", {"id": OP, "host": F.S1, "boundary": [[1000, 1000], [2000, 1000], [2000, 2000], [1000, 2000]]}))
    ok(base.run("pset.set", {"id": OP, "pset": "DoBIM_Note", "prop": "Count", "value": 3}))
    base.eng.flush_ifc()
    op = base.eng.ifc.f.by_guid(part_guid(F.S1, f"void:{OP}"))
    assert ue.get_pset(op, "DoBIM_Note")["Count"] == 3
    assert err(base.run("pset.set", {"id": OP, "pset": "DoBIM_Intent", "prop": "x", "value": 1}))["code"] == "CONSTRAINT"


# ---------------------------------------------------------------- 7. family.register 와 IFC 스키마
def _userdefined_recipe():
    r = copy.deepcopy(library.load("appliance.washer"))
    r["id"] = "user.washer_ud"
    r["ifc"]["predefined"] = "USERDEFINED"
    r["ifc"].get("type_attrs", {}).pop("ElementType", None)
    r["ifc"].get("attrs", {}).pop("ObjectType", None)
    return r


def test_7_USERDEFINED_인데_ElementType_없는_레시피는_register도_거절한다(base):
    v = ok(base.run("family.validate", {"recipe": _userdefined_recipe(), "level": "full"}))
    assert not v["ok"] and any(e["code"] == "E_IFC_USERDEFINED" for e in v["errors"])
    e = err(base.run("family.register", {"recipe": _userdefined_recipe()}))
    assert e["code"] == "FAMILY_INVALID" and "ElementType" in e["message"]
    good = _userdefined_recipe()
    good["ifc"].setdefault("type_attrs", {})["ElementType"] = "세탁기"
    good["ifc"].setdefault("attrs", {})["ObjectType"] = "세탁기"
    assert ok(base.run("family.register", {"recipe": good}))["created"]


# ---------------------------------------------------------------- 8. 계단 난간 id·재료
def test_8_계단_난간에_재료를_한_번에_준다(base):
    ok(base.run("stair.create", {"id": F.ST1, "level": F.LV1, "run": [1000, 1000, 5000, 1000], "width": 1200, "height": 3000, "risers": 18}))
    dry = ok(base.run("railing.from_stair", {"stair": F.ST1, "side": "both"}, dry_run=True))   # 개수·순서를 미리 본다(같은 입력이면 같다)
    ids = ["c" + "0" * 19 + "a1", "c" + "0" * 19 + "a2"]
    assert len(dry["created"]) == len(ids)
    r = ok(base.run("railing.from_stair", {"stair": F.ST1, "side": "both", "ids": ids, "material": "스테인리스"}))
    assert r["created"] == ids and r["material"] == "스테인리스"
    for rid in ids:
        assert ok(base.run("element.get", {"id": rid}))["material"] == "스테인리스"
    txt = str(ok(base.run("commands.describe", {"name": "railing.from_stair"})))
    assert "material" in txt and "created" in txt and "dry_run" in txt
    plain = ok(base.run("railing.from_slab", {"slab": F.S1})) if base.run("element.get", {"id": F.S1})["ok"] else {"created": []}
    assert "material" not in plain   # 재료를 주지 않으면 응답은 이전과 같다
