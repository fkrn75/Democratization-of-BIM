# SPDX-License-Identifier: MPL-2.0
"""W2 스물일곱 번째 PR(단면·입면 뷰의 태그와 태그 후속).

1. 단면·입면 뷰의 태그: 생성·일괄 태그, 대표점 = 뷰의 (가로, 높이), 대상을 옮기면 따라감, 보이지 않는 요소(범위 밖·가려짐·잘라내기 밖)는 그리지 않음,
   겹침 회피·지시선(set)·고정 끝점, 거절 사례, 출력(SVG·PDF·시트·DXF·GUI 장면)·증분 = 전체 재계산·저장·IFC 왕복
2. 태그 후속: 절대 끝점(leader end fixed, 대상을 옮겨도 그 자리에 남는 뷰 좌표 끝점), 시트 단위 겹침 검사(annot.tag.sheet_check: 보고만)
3. 하위호환: 평면 뷰의 태그·태그가 없는 뷰의 그림·옛 행 형식은 그대로
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import sys

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import annot as AN
from dobim_core import annot_layout as LY
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

VP = F.V1                              # 평면 뷰
VA = F.V2                              # 단면 A: y=2000 에서 북쪽(+y)을 본다(W3 와 W2 가 보이고 W1 은 뒤)
VN, VS, VX = ("4" + "0" * 19 + c for c in ("e1", "e5", "e6"))   # 입면 N(남쪽을 본다: W3 가 앞), 입면 S(북쪽을 본다: W1 이 앞), 단면 X(x=1500 에서 -x 쪽을 본다)
TID = lambda n: "7" + f"{n:021d}"   # noqa: E731  태그 id 22자
BT = "9" + "0" * 19 + "c1"
BA = "9" + "0" * 19 + "n1"
SO = "f" + "0" * 20 + "9"


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


def get(s, e):
    return ok(s.run("element.get", {"id": e}))


def destructive(s, name, args):
    r = s.run(name, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


def items(s, v):
    """뷰의 태그 그림 항목 {태그 id: 항목}."""
    return {it[1]: it for it in g_of(s).q(AN.view_annots, v) if it[0] == "tag"}


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [x["message"][:160] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


@pytest.fixture
def ws(base):
    ok(base.run("view.create", {"id": VP, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(base.run("view.create", {"id": VA, "name": "A-A", "type": "section", "p1": [0, 2000], "p2": [6000, 2000], "depth": 8000}))
    ok(base.run("view.create", {"id": VN, "name": "북입면", "type": "elevation", "dir": "N"}))
    ok(base.run("view.create", {"id": VS, "name": "남입면", "type": "elevation", "dir": "S"}))
    ok(base.run("view.create", {"id": VX, "name": "X-X", "type": "section", "p1": [1500, -1000], "p2": [1500, 5000], "depth": 8000}))
    return base


def tag(s, view, target, fields=("name",), tid=None, **kw):
    a = {"view": view, "target": target, "fields": list(fields), **kw}
    if tid is not None:
        a["id"] = tid
    return ok(s.run("annot.tag.create", a))["id"]


def beam(s):
    ok(s.run("beamtype.create", {"id": BT, "name": "B1", "preset": "H-400x200x8x13"}))
    ok(s.run("beam.create", {"id": BA, "level": F.LV1, "type": BT, "start": [0, 3000], "end": [6000, 3000], "offset": 2400.0}))


# ---------------------------------------------------------------- 1. 단면·입면 뷰의 태그
def test_1_재현_평면이_아닌_뷰의_태그는_뷰_종류_안내로_거절된다(ws):
    # 이전 동작(main ad25671): 단면·입면 뷰에 annot.tag.create → CONSTRAINT "태그는 평면 뷰에 둔다". 지금은 단면·입면이 되고 대지·범례·제도 뷰만 거절된다
    ok(ws.run("view.create", {"id": F.VL1, "name": "배치", "type": "site"}))
    e = err(ws.run("annot.tag.create", {"view": F.VL1, "target": F.W3, "fields": ["name"]}))
    assert e["code"] == "CONSTRAINT" and "평면·단면·입면" in e["message"], e
    assert tag(ws, VA, F.W3)   # 단면 뷰는 된다


def test_1_단면_뷰_태그는_뷰_가로와_높이에_놓인다(ws):
    t1, t2, t3 = tag(ws, VA, F.W3, tid=TID(1)), tag(ws, VA, F.D2, ("name", "width"), tid=TID(2)), tag(ws, VA, F.W2, tid=TID(3))
    it = items(ws, VA)
    # 벽 W3: 보이는 선(절단 채움 포함)의 경계 상자 가운데 (가로 3000, 높이 1500). 문 D2: 개구부 가운데(가로 3000, 높이 1050). 간격은 뷰의 mm
    assert it[t1] == ("tag", t1, (3000.0, 1500.0), (3400.0, 1900.0), "W-000003")
    assert it[t2][2] == (3000.0, 1050.0) and it[t2][3] == (3400.0, 1450.0)
    assert it[t3][2] == (6000.0, 1500.0)                       # 단면선이 자르는 W2 는 절단 채움의 가운데
    p = get(ws, t1)["params"]
    assert p["off"] == [400.0, 400.0] and p["home"] == [3400.0, 1900.0] and "align" not in p   # 저장 형식은 평면 태그와 같은 키(뷰 종류가 좌표계를 정한다)
    # 같은 모델이라도 평면 뷰의 태그는 평면 좌표 그대로
    tp = tag(ws, VP, F.W3, tid=TID(4))
    assert items(ws, VP)[tp][2] == (3000.0, 4000.0)


def test_1_입면_뷰_태그와_앞뒤_가림(ws):
    # N 입면(남쪽을 본다)은 W3 가 앞이라 W3 의 문 D2 는 보이고 뒤쪽 벽 W1 의 문 D1 은 가려진다. S 입면은 반대
    assert tag(ws, VN, F.D2, tid=TID(1))
    assert items(ws, VN)[TID(1)][2] == (-3000.0, 1050.0)      # 입면 N 의 가로 = -x
    e = err(ws.run("annot.tag.create", {"view": VN, "target": F.D1, "fields": ["name"]}))
    assert e["code"] == "CONSTRAINT" and "보이지 않는다" in e["message"] and e["path"] == "/target"
    assert tag(ws, VS, F.D1, tid=TID(2))
    assert items(ws, VS)[TID(2)][2] == (1500.0, 1050.0)
    assert err(ws.run("annot.tag.create", {"view": VS, "target": F.D2, "fields": ["name"]}))["code"] == "CONSTRAINT"
    # 단면 A(+y 를 본다): 뒤쪽 벽 W1 과 D1 은 단면 앞 범위(near=0)에 없다
    assert err(ws.run("annot.tag.create", {"view": VA, "target": F.D1, "fields": ["name"]}))["code"] == "CONSTRAINT"
    assert err(ws.run("annot.tag.create", {"view": VA, "target": F.W1, "fields": ["name"]}))["code"] == "CONSTRAINT"
    # 단면 X(-x 를 본다)는 x=1500 의 D1 을 자르는 단면선: 문 D1 이 개구부 자리에서 보이고(가로 = y+1000) 단면선 뒤쪽의 문 D2(x=3000) 는 범위 밖
    assert tag(ws, VX, F.D1, tid=TID(3))
    assert items(ws, VX)[TID(3)][2][1] == 1050.0
    assert err(ws.run("annot.tag.create", {"view": VX, "target": F.D2, "fields": ["name"]}))["code"] == "CONSTRAINT"


def test_1_다른_요소에_가려진_문은_태그가_사라지고_다시_보이면_돌아온다(ws):
    t = tag(ws, VN, F.D2, tid=TID(1))
    before = items(ws, VN)[t]
    # D2 앞(N 입면의 관찰자 쪽 = y 가 큰 쪽)에 문 개구부를 덮는 돌출 솔리드를 세운다
    ok(ws.run("solid.create", {"id": SO, "level": F.LV1, "category": "buttress", "form": "prism",
                               "polygon": [[2200, 4200], [3800, 4200], [3800, 5200], [2200, 5200]], "z0": 0, "z1": 2400}))
    assert t not in items(ws, VN)                                        # 그리지 않는다
    li = ok(ws.run("annot.list", {"view": VN}))["items"]
    assert [x for x in li if x["id"] == t][0]["visible"] is False        # annot.list 는 보이지 않는다고 알린다(상태는 ok 그대로)
    assert [x for x in li if x["id"] == t][0]["status"] == "ok"
    ok(ws.run("history.undo", {}))
    assert items(ws, VN)[t] == before                                    # 가림이 풀리면 같은 자리로 돌아온다
    li = ok(ws.run("annot.list", {"view": VN}))["items"]
    assert "visible" not in li[0]


def test_1_범위_밖과_잘라내기_밖은_그리지_않는다(ws):
    t = tag(ws, VA, F.W3, tid=TID(1))
    td = tag(ws, VA, F.D2, tid=TID(2))
    before = dict(items(ws, VA))
    ok(ws.run("view.set", {"id": VA, "depth": 1000}))                    # W3(y=4000)가 보이는 깊이(y 2000~3000) 밖
    assert items(ws, VA) == {}
    assert {x["id"] for x in ok(ws.run("annot.list", {"view": VA}))["items"] if x.get("visible") is False} == {t, td}
    ok(ws.run("view.set", {"id": VA, "depth": 8000}))
    assert items(ws, VA) == before
    ok(ws.run("view.set", {"id": VA, "crop": [0, 0, 2000, 4000]}))       # 잘라내기 사각형 밖(가로 3000)
    assert items(ws, VA) == {}
    ok(ws.run("view.set", {"id": VA, "clear_crop": True}))
    assert items(ws, VA) == before
    # 그리지 않는 태그는 SVG 에도 없다
    ok(ws.run("view.set", {"id": VA, "depth": 1000}))
    assert destructive(ws, "view.export", {"id": VA, "path": "h.svg", "overwrite": True})["ok"]
    assert 'class="tag"' not in (ws.path.parent / "h.svg").read_text(encoding="utf-8")


def test_1_대상을_옮기면_태그가_따라간다(ws):
    t1, t2 = tag(ws, VA, F.W3, tid=TID(1)), tag(ws, VA, F.D2, tid=TID(2))
    a0 = items(ws, VA)
    ok(ws.run("wall.move", {"id": F.W3, "dx": 500, "dy": 0}))            # 단면 가로(+x) 방향으로 500
    a1 = items(ws, VA)
    for t in (t1, t2):
        assert a1[t][2] == (a0[t][2][0] + 500.0, a0[t][2][1])
        assert a1[t][3] == (a0[t][3][0] + 500.0, a0[t][3][1])             # 글 자리도 같은 만큼(간격만 저장)
    assert get(ws, t1)["params"]["off"] == [400.0, 400.0]
    ok(ws.run("door.set", {"id": F.D2, "height": 2400}))                 # 문 높이를 바꾸면 개구부 가운데 높이가 따라간다(1050 -> 1200)
    assert items(ws, VA)[t2][2][1] == 1200.0
    ok(ws.run("history.undo", {}))
    ok(ws.run("history.undo", {}))
    assert items(ws, VA) == a0


def test_1_보_슬래브_기둥_같은_다른_대상(ws):
    beam(ws)
    ok(ws.run("slab.create", {"id": F.S1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "thickness": 200}))
    tb = tag(ws, VS, BA, ("type", "size"), tid=TID(1))                   # 입면에서 보는 보: 보 축 방향 글이 아니라 지시선 가로 글
    assert "align" not in get(ws, tb)["params"]
    it = items(ws, VS)[tb]
    assert len(it) == 5 and it[4] == "B1 H-400x200" and it[2][1] == 2200.0 and (it[3][0] - it[2][0], it[3][1] - it[2][1]) == (400.0, 400.0)   # 보 가운데 높이(밑 2000 + 깊이 400 / 2)
    ts = tag(ws, VA, F.S1, ("name", "area"), tid=TID(2))
    assert items(ws, VA)[ts][2][0] == 3000.0
    # 보 태그가 달린 뒤 보를 옮기면 따라간다(보이는 선의 가운데라 가림이 바뀌면 보이는 만큼의 가운데로 옮겨 간다)
    ok(ws.run("beam.move", {"id": BA, "dx": 500, "dy": 0}))
    it2 = items(ws, VS)[tb]
    assert it2[2] != it[2] and (it2[3][0] - it2[2][0], it2[3][1] - it2[2][1]) == (400.0, 400.0)


def test_1_창과_기둥_패밀리_인스턴스(ws):
    win, col = "5" + "0" * 20 + "1", "5" + "0" * 20 + "2"
    ok(ws.run("family.place", {"id": win, "family": "window.fixed", "host": F.W3, "offset": 1500}))
    ok(ws.run("family.place", {"id": col, "family": "column.section", "type_values": {"section": "round", "width": 500, "depth": 500},
                               "level": F.LV1, "position": [3000, 2000]}))
    tw = tag(ws, VN, win, ("name",), tid=TID(1))                       # 입면 N(남쪽을 본다): W3 에 호스팅된 창은 앞에 보인다. 가로 = -x
    tc = tag(ws, VA, col, ("name",), tid=TID(2))                       # 단면 A: y=2000 단면선이 자르는 자립 기둥
    wi, ci = items(ws, VN)[tw], items(ws, VA)[tc]
    assert wi[2][0] == -4500.0 and 0.0 < wi[2][1] < 3000.0               # W3 는 (6000,4000)에서 (0,4000)으로 가는 벽이라 offset 1500 은 x=4500
    assert ci[2][0] == 3000.0 and 0.0 < ci[2][1] <= 3000.0
    assert err(ws.run("annot.tag.create", {"view": VS, "target": win, "fields": ["name"]}))["code"] == "CONSTRAINT"   # 남입면에서는 창이 뒤 벽(W3)에 가려진다


def test_1_일괄_태그는_뷰에_보이는_요소만_단다(ws):
    r = ok(ws.run("annot.tag.all", {"view": VN, "kinds": ["wall", "door", "space"], "fields": ["name"]}))
    kinds = sorted(x["kind"] for x in r["tags"])
    # N 입면: 벽 셋(뒤쪽 W1 은 테두리선이 남아 그려진다)과 앞 벽의 문 D2. 가려진 D1 과 그려지지 않는 공간은 단다
    assert kinds.count("wall") == 3 and kinds.count("door") == 1 and "space" not in kinds
    assert {x["target"] for x in r["tags"] if x["kind"] == "door"} == {F.D2}
    ws.run("history.undo", {})
    # 단면 A: W1·D1 은 범위 밖
    r2 = ok(ws.run("annot.tag.all", {"view": VA, "kinds": ["wall", "door"], "fields": ["name"]}))
    assert {x["target"] for x in r2["tags"]} == {F.W2, F.W3, F.D2}
    assert ok(ws.run("annot.tag.all", {"view": VA, "kinds": ["wall", "door"], "fields": ["name"]}))["created"] == []   # 이미 단 것은 건너뛴다
    assert err(ws.run("annot.tag.all", {"view": VA, "kinds": ["space"]}))["code"] == "CONSTRAINT"
    assert err(ws.run("annot.tag.all", {"view": VA, "kinds": ["wall"], "align": "axis"}))["code"] == "CONSTRAINT"


def test_1_거절_사례(ws):
    cases = [
        ({"view": VA, "target": F.W3, "fields": ["name"], "align": "axis"}, "CONSTRAINT"),              # 보 축 방향 글은 평면 전용(벽은 원래 안 된다)
        ({"view": VA, "target": F.W1, "fields": ["name"]}, "CONSTRAINT"),                               # 보이지 않는다
        ({"view": VA, "target": F.W3, "fields": ["nonexist"]}, "CONSTRAINT"),
        ({"view": VP, "target": F.W3, "fields": ["name"], "align": "axis"}, "CONSTRAINT"),
        ({"view": F.W1, "target": F.W3, "fields": ["name"]}, "NOT_FOUND"),
    ]
    for args, code in cases:
        assert err(ws.run("annot.tag.create", args))["code"] == code, args
    beam(ws)
    assert err(ws.run("annot.tag.create", {"view": VS, "target": BA, "fields": ["type"], "align": "axis"}))["code"] == "CONSTRAINT"
    ok(ws.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "name": "사무실", "category": "사무실"}))
    e = err(ws.run("annot.tag.create", {"view": VA, "target": F.SP1, "fields": ["name"]}))
    assert e["code"] == "CONSTRAINT" and "공간" in e["message"]
    assert ok(ws.run("annot.check", {}))["total"] == 0                                                  # 거절은 아무것도 만들지 않는다


def test_1_다시_묶기와_고아(ws):
    t = tag(ws, VA, F.W3, tid=TID(1))
    ok(ws.run("element.delete", {"id": F.W3}))
    assert ok(ws.run("annot.check", {}))["orphan"] == {"target_deleted": 1}
    assert t not in items(ws, VA) or items(ws, VA)[t][0] == "orphan"
    assert err(ws.run("annot.rebind", {"id": t, "target": F.W1}))["code"] == "CONSTRAINT"                # 단면 A 에서 안 보이는 W1 로는 못 묶는다
    ok(ws.run("annot.rebind", {"id": t, "target": F.W2}))
    assert items(ws, VA)[t][2] == (6000.0, 1500.0)


# ---------------------------------------------------------------- 1b. 겹침 회피·지시선·고정 끝점(단면·입면)
def stack(s, v, target, n):
    flds = (("name",), ("name", "level"), ("ifc_class",), ("material",), ("level",), ("name", "ifc_class"))
    return [tag(s, v, target, flds[i], tid=TID(10 + i)) for i in range(n)]


def overlaps(s, v):
    sc = float(get(s, v)["params"]["scale"])
    bx = {}
    for t, it in items(s, v).items():
        _a, lab, text, u, _p, side = LY.tag_parts(it)
        bx[t] = LY.tag_box(lab, text, u, side, sc)
    ks = sorted(bx)
    return [(a, b) for i, a in enumerate(ks) for b in ks[i + 1:]
            if LY.overlap_area(LY.grow(bx[a], LY.PAD * sc), LY.grow(bx[b], LY.PAD * sc)) > 0.0]


def test_1_겹침_회피_배치는_단면_입면에서도_같은_규약이다(ws):
    stack(ws, VA, F.W3, 5)
    assert len(overlaps(ws, VA)) >= 5                                                     # 같은 대상 태그 다섯 개는 같은 자리에 쌓인다
    r = ok(ws.run("annot.tag.arrange", {"view": VA}))
    assert r["unresolved"] == [] and len(r["moved"]) == 4 and r["kept"] == 1 and "hidden" not in r
    assert overlaps(ws, VA) == []
    pos = {t: get(ws, t)["params"]["off"] for t in items(ws, VA)}
    r2 = ok(ws.run("annot.tag.arrange", {"view": VA}))                                    # 멱등
    assert r2["moved"] == [] and {t: get(ws, t)["params"]["off"] for t in items(ws, VA)} == pos
    for t in items(ws, VA):
        assert get(ws, t)["params"]["home"][1] != 0.0                                     # home 은 뷰 좌표
    # create 의 avoid
    n = tag(ws, VA, F.W3, ("name", "material"), avoid=True, tid=TID(30)) if False else ok(ws.run("annot.tag.create", {"id": TID(30), "view": VA, "target": F.W3, "fields": ["name", "material"], "avoid": True}))["id"]
    assert overlaps(ws, VA) == [] and n in items(ws, VA)


def test_1_가려진_태그는_배치가_건너뛴다(ws):
    t1 = tag(ws, VA, F.W3, tid=TID(1))
    t2 = tag(ws, VA, F.D2, tid=TID(2))
    ok(ws.run("view.set", {"id": VA, "crop": [0, 0, 3200, 4000]}))   # W3 의 가운데(3000)는 안, D2 는 안: 둘 다 보인다
    ok(ws.run("view.set", {"id": VA, "crop": [0, 0, 2000, 4000]}))   # 둘 다 밖
    r = ok(ws.run("annot.tag.arrange", {"view": VA}))
    assert r["hidden"] == sorted([t1, t2]) and r["moved"] == [] and r["skipped"] == []


def test_1_지시선_고정_set이_단면_뷰에서_된다(ws):
    t = tag(ws, VA, F.W3, tid=TID(1))
    ok(ws.run("annot.tag.set", {"id": t, "offset": [-600, 500], "side": "end", "leader": {"on": True, "bend": [-200, 500]}}))
    it = items(ws, VA)[t]
    assert len(it) == 8 and it[6] == ((3000.0, 1500.0), (2800.0, 2000.0), (2400.0, 2000.0)) and it[7] == "end"
    p = get(ws, t)["params"]
    assert p["pin"] is True and p["home"] == [2400.0, 2000.0]
    ok(ws.run("wall.move", {"id": F.W3, "dx": 300, "dy": 0}))
    assert items(ws, VA)[t][6] == ((3300.0, 1500.0), (3100.0, 2000.0), (2700.0, 2000.0))    # 끝점·꺾임점·글이 대상과 함께
    ok(ws.run("annot.tag.set", {"id": t, "reset": True}))
    assert items(ws, VA)[t] == ("tag", t, (3300.0, 1500.0), (3700.0, 1900.0), "W-000003")


def test_2_절대_끝점은_대상을_옮겨도_그_자리에_남는다(ws):
    # 이전 동작: 자유 끝점은 대표점 기준 간격이라 대상과 함께 움직인다(85.1)
    t = tag(ws, VA, F.W3, tid=TID(1))
    ok(ws.run("annot.tag.set", {"id": t, "leader": {"on": True, "end": "free", "tip": [-500, -300]}}))
    assert items(ws, VA)[t][6][0] == (2500.0, 1200.0)
    ok(ws.run("annot.tag.set", {"id": t, "leader": {"on": True, "end": "fixed", "tip": [2500, 1200]}}))   # 뷰 좌표(가로, 높이) 절대
    assert get(ws, t)["params"]["leader"] == {"on": True, "end": "fixed", "tip": [2500.0, 1200.0]}
    assert items(ws, VA)[t][6] == ((2500.0, 1200.0), (3400.0, 1900.0))
    ok(ws.run("wall.move", {"id": F.W3, "dx": 400, "dy": 0}))
    it = items(ws, VA)[t]
    assert it[6][0] == (2500.0, 1200.0) and it[3] == (3800.0, 1900.0)                    # 끝점은 그대로, 글은 대상을 따라간다
    # 평면 뷰에서도(평면 좌표 절대)
    tp = tag(ws, VP, F.W1, tid=TID(2))
    ok(ws.run("annot.tag.set", {"id": tp, "leader": {"on": True, "end": "fixed", "tip": [1000, 700], "bend": [300, 600]}}))
    pts = items(ws, VP)[tp][6]
    assert pts[0] == (1000.0, 700.0) and pts[1] == (3300.0, 600.0)
    ok(ws.run("wall.move", {"id": F.W1, "dx": 0, "dy": 200}))
    assert items(ws, VP)[tp][6][0] == (1000.0, 700.0)
    # 거절: tip 없음, attached 에 tip, 끈 지시선에 tip
    for ld in ({"on": True, "end": "fixed"}, {"on": True, "end": "attached", "tip": [1, 1]}, {"on": False, "end": "fixed", "tip": [1, 1]}):
        assert err(ws.run("annot.tag.set", {"id": t, "leader": ld}))["code"] == "CONSTRAINT", ld
    assert err(ws.run("annot.tag.set", {"id": t, "leader": {"on": True, "end": "fixed", "tip": [1e9, 0]}}))["code"] == "SCHEMA_INVALID"


def test_2_옛_지시선_형식은_그대로_읽힌다():
    from dobim_core import kinds as K
    assert K.tag_leader_ok({"on": True}) and K.tag_leader_ok({"on": True, "end": "free", "tip": [1, 2]})
    assert K.tag_leader_ok({"end": "fixed", "tip": [123456.0, -3.0]})
    assert not K.tag_leader_ok({"end": "fixed"}) and not K.tag_leader_ok({"end": "attached", "tip": [1, 1]})
    assert not K.tag_leader_ok({"end": "free", "tip": [1e6, 0]}) and not K.tag_leader_ok({"end": "warp", "tip": [1, 1]})


# ---------------------------------------------------------------- 2. 출력·증분·왕복
def test_2_SVG_PDF_DXF_시트_GUI_장면(ws):
    t = tag(ws, VA, F.W3, tid=TID(1))
    ok(ws.run("annot.tag.set", {"id": t, "offset": [-600, 500], "side": "end", "leader": {"on": True, "bend": [-200, 500]}}))

    def exp(name, v=VA):
        assert destructive(ws, "view.export", {"id": v, "path": name, "overwrite": True})["ok"]
        return (ws.path.parent / name).read_bytes()

    svg = exp("a.svg").decode("utf-8")
    i = svg.index(f'<g class="tag" data-id="{t}"')
    blk = svg[i:svg.index("</g>", i)]
    assert blk.count("<line") == 2 and blk.count("<circle") == 1 and 'text-anchor="end"' in blk and "W-000003" in blk
    assert exp("a.pdf").startswith(b"%PDF") and "W-000003" in exp("a.dxf").decode("utf-8")
    ok(ws.run("sheet.define", {"id": F.SH1, "name": "A-101", "views": [VA], "paper": "A3", "project_name": "예시", "sheet_number": "A-101", "date": "2026-01-01"}))
    assert destructive(ws, "sheet.render", {"id": F.SH1, "path": "s.svg", "overwrite": True})["ok"]
    assert "W-000003" in (ws.path.parent / "s.svg").read_text(encoding="utf-8")
    from dobim_core import scene as SC
    sc = {(x[0], x[1]): x for x in g_of(ws).q(SC.view_scene, VA)}
    its = sc[("tag", t)][5]
    assert len(its) == 1 and SC._annot_segs(its[0]) == [(3000.0, 1500.0, 2800.0, 2000.0), (2800.0, 2000.0, 2400.0, 2000.0)]
    ok(ws.run("annot.tag.set", {"id": t, "reset": True}))


def test_2_증분_전체_계산이_같다(ws):
    beam(ws)
    t1, t2, t3 = tag(ws, VA, F.W3, tid=TID(1)), tag(ws, VN, F.D2, tid=TID(2)), tag(ws, VS, BA, ("type",), tid=TID(3))
    ok(ws.run("annot.tag.set", {"id": t1, "offset": [500, 700], "leader": {"on": True, "end": "fixed", "tip": [2000, 900], "bend": [100, 800]}}))

    def same():
        g = g_of(ws)
        full = s3lib.RefGraph(s3lib._Inputs(ws.eng))
        assert g.q(AN.annot_bundle) == full.q(AN.annot_bundle)
        for v in (VA, VN, VS, VP):
            assert g.q(AN.view_annots, v) == full.q(AN.view_annots, v)

    same()
    ok(ws.run("wall.move", {"id": F.W3, "dx": 700, "dy": 100}))
    same()
    ok(ws.run("door.set", {"id": F.D2, "height": 2400}))
    same()
    ok(ws.run("view.set", {"id": VA, "depth": 1000}))
    same()
    ok(ws.run("view.set", {"id": VA, "depth": 8000}))
    ok(ws.run("annot.tag.arrange", {"view": VN}))
    same()
    ok(ws.run("history.undo", {}))
    same()


def test_2_저장하고_다시_열기와_IFC_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("view.create", {"id": VA, "name": "A-A", "type": "section", "p1": [0, 2000], "p2": [6000, 2000], "depth": 8000}))
        ok(b.run("view.create", {"id": VN, "name": "북입면", "type": "elevation", "dir": "N"}))
        t1 = ok(b.run("annot.tag.create", {"view": VA, "target": F.W3, "fields": ["name"]}))["id"]
        t2 = ok(b.run("annot.tag.create", {"view": VN, "target": F.D2, "fields": ["name", "width"]}))["id"]
        ok(b.run("annot.tag.set", {"id": t1, "offset": [-500, 600], "side": "end", "leader": {"on": True, "end": "fixed", "tip": [2500, 1200], "bend": [-100, 600]}}))
        h = b.hashes()
        out = b.export("b.ifc")
        assert file_errors(ifcopenshell.open(str(out))) == []                                    # IFC4 검증 오류 0
        assert b.destructive("ifc.export", {"path": "b3.ifc", "schema": "IFC2X3"})["ok"]
        assert file_errors(ifcopenshell.open(str(b.ws / "b3.ifc"))) == []                        # IFC2X3 검증 오류 0
        r = b.import_ifc("b.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)                                        # 태그 정의(단면·입면 뷰 태그, 절대 끝점)까지 같다
        assert b.export("b2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        for t in (t1, t2):
            assert ok(b.run("element.get", {"id": t}, project="imp.dobim"))["params"] == ok(b.run("element.get", {"id": t}))["params"]
        assert ok(b.run("element.get", {"id": t1}, project="imp.dobim"))["params"]["leader"]["end"] == "fixed"
    finally:
        b.close()


def test_2_저장하고_다시_열면_그림_항목이_같다(tmp_path):
    s = kit.Session(tmp_path / "b.dobim")
    try:
        for name, args in F.SCRIPT:
            ok(s.run(name, args))
        ok(s.run("view.create", {"id": VA, "name": "A-A", "type": "section", "p1": [0, 2000], "p2": [6000, 2000], "depth": 8000}))
        t = tag(s, VA, F.W3, tid=TID(1))
        ok(s.run("annot.tag.set", {"id": t, "offset": [800, 500], "side": "middle", "leader": {"on": True, "bend": [300, 500]}}))
        before = (get(s, t)["params"], items(s, VA))
    finally:
        s.close()
    s = kit.Session(tmp_path / "b.dobim", create=False)
    try:
        assert (get(s, t)["params"], items(s, VA)) == before
    finally:
        s.close()


# ---------------------------------------------------------------- 3. 하위호환
def test_3_평면_태그와_태그_없는_뷰의_그림은_그대로다(ws):
    def exp(name, v):
        assert destructive(ws, "view.export", {"id": v, "path": name, "overwrite": True})["ok"]
        return (ws.path.parent / name).read_bytes()

    base = {v: exp(f"{i}.svg", v) for i, v in enumerate((VP, VA, VN, VS, VX))}
    tp = tag(ws, VP, F.W3, tid=TID(1))
    p = get(ws, tp)["params"]
    assert set(p) == {"name", "view", "target", "fields", "off", "home"}                    # 평면 태그의 저장 키는 이전과 같다
    assert items(ws, VP)[tp] == ("tag", tp, (3000.0, 4000.0), (3400.0, 4400.0), "W-000003")  # 5칸
    for v in (VA, VN, VS, VX):                                                              # 평면 뷰에 단 태그는 다른 뷰의 그림을 바꾸지 않는다
        assert exp("x.svg", v) == base[v]
    ok(ws.run("element.delete", {"id": tp}))
    assert exp("y.svg", VP) == base[VP]
    ts = tag(ws, VA, F.W3, tid=TID(2))
    assert exp("z.svg", VA) != base[VA]
    ok(ws.run("element.delete", {"id": ts}))
    assert exp("w.svg", VA) == base[VA]


# ---------------------------------------------------------------- 4. 시트 단위 겹침 검사
def sheet(s, views, sid=F.SH1, **kw):
    ok(s.run("sheet.define", {"id": sid, "name": f"S-{sid[-3:]}", "views": list(views), "paper": "A1", "project_name": "예시", "sheet_number": "A-101", "date": "2026-01-01", **kw}))
    return sid


def test_4_시트_검사는_다른_뷰_태그_겹침을_보고만_한다(ws):
    # 이전 동작: 시트에 올린 뷰들의 태그가 용지 위에서 겹쳐도 알릴 길이 없었다(한 뷰 안의 겹침만 annot.tag.arrange 가 안다)
    t1, t2 = tag(ws, VA, F.W3, tid=TID(1)), tag(ws, VN, F.W3, tid=TID(2))
    sid = sheet(ws, [VA, VN], viewports=[{"view": VA, "at": [20, 20]}, {"view": VN, "at": [420, 20]}])   # 두 뷰를 멀리 떨어뜨려 놓는다
    r0 = ok(ws.run("annot.tag.sheet_check", {"id": sid, "boxes": True}))
    assert r0["views"] == [VA, VN] and r0["tags"] == 2 and r0["count"] == 0 and r0["overlaps"] == []
    bx = {b["tag"]: b["box"] for b in r0["boxes"]}
    # 사용자가 뷰포트를 끌어 서로 겹치게 놓으면(B 를 A 위로) 두 글이 용지 위에서 겹친다: 뷰포트 위치만 옮긴다
    dx, dy = bx[t1][0] - bx[t2][0], bx[t1][1] - bx[t2][1]
    ok(ws.run("sheet.define", {"id": sid, "name": f"S-{sid[-3:]}", "views": [VA, VN], "paper": "A1", "project_name": "예시", "sheet_number": "A-101", "date": "2026-01-01",
                               "viewports": [{"view": VA, "at": [20, 20]}, {"view": VN, "at": [420 + dx, 20 + dy]}]}))
    before = (get(ws, t1)["params"], get(ws, t2)["params"])
    r = ok(ws.run("annot.tag.sheet_check", {"id": sid}))
    assert r["count"] == 1 and r["overlaps"][0]["a"]["view"] == VA and r["overlaps"][0]["b"]["view"] == VN
    assert {r["overlaps"][0]["a"]["tag"], r["overlaps"][0]["b"]["tag"]} == {t1, t2} and r["overlaps"][0]["area_mm2"] > 1.0
    assert "boxes" not in r
    assert (get(ws, t1)["params"], get(ws, t2)["params"]) == before                    # 보고만: 자리는 그대로
    # 같은 뷰 안의 태그끼리는 기본으로 보지 않는다
    tag(ws, VA, F.W3, ("level",), tid=TID(3))                   # A 안의 두 태그 t1·t3 는 같은 자리에 쌓이고, 둘 다 B 의 t2 와 겹친다
    assert ok(ws.run("annot.tag.sheet_check", {"id": sid}))["count"] == 2
    assert ok(ws.run("annot.tag.sheet_check", {"id": sid, "within": True}))["count"] == 3     # 한 뷰 안의 쌍(t1-t3)은 within 일 때만
    assert ok(ws.run("annot.tag.sheet_check", {"id": sid, "limit": 1, "within": True}))["truncated"] is True
    # 결정적: 같은 입력은 같은 응답, 뷰를 시트에서 빼면 겹침이 사라진다
    assert ok(ws.run("annot.tag.sheet_check", {"id": sid, "within": True})) == ok(ws.run("annot.tag.sheet_check", {"id": sid, "within": True}))
    sid2 = sheet(ws, [VA], sid=F.SH1[:-1] + "2")
    assert ok(ws.run("annot.tag.sheet_check", {"id": sid2}))["count"] == 0
    # 겹친 뷰포트를 다시 떨어뜨리면 겹침이 사라진다(자리를 안 바꿨으니 시트 배치만의 문제였다)
    ok(ws.run("sheet.define", {"id": sid, "name": f"S-{sid[-3:]}", "views": [VA, VN], "paper": "A1", "project_name": "예시", "sheet_number": "A-101", "date": "2026-01-01",
                               "viewports": [{"view": VA, "at": [20, 20]}, {"view": VN, "at": [420, 20]}]}))
    assert ok(ws.run("annot.tag.sheet_check", {"id": sid}))["count"] == 0


def test_4_시트_검사_거절과_평면_뷰_태그(ws):
    assert err(ws.run("annot.tag.sheet_check", {"id": F.W1}))["code"] in ("CONSTRAINT", "NOT_FOUND")
    assert err(ws.run("annot.tag.sheet_check", {"id": "9" + "0" * 21}))["code"] == "NOT_FOUND"
    assert err(ws.run("annot.tag.sheet_check", {"id": F.SH1, "limit": 0}))["code"] == "SCHEMA_INVALID"
    # 평면 뷰의 태그도 본다: 같은 평면 뷰를 둘 올릴 수는 없으니 평면 + 단면
    tp = tag(ws, VP, F.W3, tid=TID(1))
    ts = tag(ws, VA, F.W3, tid=TID(2))
    sid = sheet(ws, [VP, VA])
    r = ok(ws.run("annot.tag.sheet_check", {"id": sid, "boxes": True}))
    assert {b["tag"] for b in r["boxes"]} == {tp, ts} and r["views"] == [VP, VA]
    for b in r["boxes"]:
        x0, y0, x1, y1 = b["box"]
        assert x1 > x0 and y1 > y0 and 0 <= x0 and x1 <= 841 and 0 <= y0 and y1 <= 594      # A1 용지 안
