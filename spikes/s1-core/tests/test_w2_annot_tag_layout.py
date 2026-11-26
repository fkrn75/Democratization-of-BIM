# SPDX-License-Identifier: MPL-2.0
"""W2 스물여섯 번째 PR(주석 태그 겹침 회피와 지시선).

1. 태그 지시선(annot.tag.set): 켜기·끄기, 끝점 부착·자유, 꺾임점, 글 맞춤, 대상을 옮기면 따라감, 고정(pin), 거절 사례, 이전 태그 모양 불변
2. 출력(SVG·PDF·시트·DXF·GUI 장면)·증분 = 전체 재계산·저장·IFC 왕복(태그 정의)
3. 겹침 회피 배치(annot.tag.arrange, create·all 의 avoid): 재현(겹침) -> 배치 후 겹침 0, 결정적·멱등, 고정 태그 보존, 치수·문자 피하기, 보 태그 자동 지시선
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import re
import sys
from math import cos, pi, sin

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import annot as AN
from dobim_core import annot_layout as LY
from dobim_core import drawing_export as DX
from dobim_core import fixture as F

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V = F.V1
BT = "9" + "0" * 19 + "c1"
BA, BB, BCC, BD = ("9" + "0" * 19 + x for x in ("n1", "n2", "n3", "n4"))
TID = lambda n: "7" + f"{n:021d}"   # noqa: E731  태그 id 22자(결정적으로 정렬되는 고정 값)
IBT, IBM = ("3" + "0" * 19 + x for x in ("c7", "n7"))


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


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [x["message"][:160] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def items(s, v=V):
    """뷰의 태그 그림 항목 {태그 id: 항목}."""
    return {it[1]: it for it in g_of(s).q(AN.view_annots, v) if it[0] == "tag"}


@pytest.fixture
def ws(base):
    ok(base.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    return base


def tag(s, target, fields=("name",), tid=None, **kw):
    a = {"view": V, "target": target, "fields": list(fields), **kw}
    if tid is not None:
        a["id"] = tid
    return ok(s.run("annot.tag.create", a))["id"]


def beam_type(s):
    ok(s.run("beamtype.create", {"id": BT, "name": "B1", "preset": "H-400x200x8x13"}))


def beam(s, bid, start, end):
    return ok(s.run("beam.create", {"id": bid, "level": F.LV1, "type": BT, "start": list(start), "end": list(end), "offset": 2400.0}))


def boxes_of(s, v=V, only=None):
    """뷰의 태그 글 상자들 {id: 상자}(글 맞춤·눕힌 글 반영)."""
    sc = float(get(s, v)["params"]["scale"])
    out = {}
    for t, it in items(s, v).items():
        if only is None or t in only:
            _anc, lab, text, u, _pts, side = LY.tag_parts(it)
            out[t] = LY.tag_box(lab, text, u, side, sc)
    return out


def overlaps(s, ids=None, v=V):
    """겹친 태그 쌍(여유 포함)."""
    sc = float(get(s, v)["params"]["scale"])
    bx = boxes_of(s, v, ids)
    ks = sorted(bx)
    return [(a, b) for i, a in enumerate(ks) for b in ks[i + 1:]
            if LY.overlap_area(LY.grow(bx[a], LY.PAD * sc), LY.grow(bx[b], LY.PAD * sc)) > 0.0]


# ---------------------------------------------------------------- 0. 순수 함수
def test_0_글_폭과_회전각은_내보내기와_같은_규칙이다():
    for s_ in ("W-000001", "보 B1 H-400x200", "", "ABC가나다"):
        assert LY.label_width(s_, 2.5) == DX.label_width(s_, 2.5)
    for k in range(24):
        a = k * pi / 12.0
        u = (cos(a), sin(a))
        assert LY.text_angle(u) == DX.adim_text_angle(u)
        t, n = LY.text_frame(u)
        assert abs(t[0] * n[0] + t[1] * n[1]) < 1e-12 and abs(t[0] ** 2 + t[1] ** 2 - 1.0) < 1e-12
        assert n[1] >= -1e-12   # 글 위쪽은 아래로 향하지 않는다(글이 거꾸로 서지 않는 쪽)


def test_0_상자_선분_판정과_후보는_결정적이다():
    # 가로 글: 글 자리에서 오른쪽으로(start), 왼쪽으로(end), 가운데(middle)
    w = LY.label_width("ABCD", 2.5) * 100
    b = LY.tag_box((1000.0, 2000.0), "ABCD", None, "start", 100.0)
    assert b == (1060.0, 1950.0, 1060.0 + w, 2200.0)
    assert LY.tag_box((1000.0, 2000.0), "ABCD", None, "end", 100.0)[2] == 940.0
    mb = LY.tag_box((1000.0, 2000.0), "ABCD", None, "middle", 100.0)
    assert mb[0] == pytest.approx(1000.0 - w / 2.0) and mb[2] == pytest.approx(1000.0 + w / 2.0)
    # 눕힌 글(보 방향 90도): 글 폭이 세로로 선다
    vb = LY.tag_box((0.0, 0.0), "ABCD", (0.0, 1.0), "start", 100.0)
    assert vb[3] - vb[1] == pytest.approx(w) and vb[2] - vb[0] == pytest.approx(250.0)
    # 선분-상자
    box = (0.0, 0.0, 10.0, 10.0)
    assert LY.seg_hits_box((-5.0, 5.0, 15.0, 5.0), box) and not LY.seg_hits_box((-5.0, 11.0, 15.0, 11.0), box)
    assert not LY.seg_hits_box((-5.0, 0.0, 15.0, 0.0), box)   # 경계만 스치는 선은 지나가는 것이 아니다
    assert LY.seg_hits_box((5.0, 5.0, 6.0, 6.0), box)         # 안에 든 선분
    # 후보: 지금 자리가 맨 앞, 중복 없음, 같은 입력 -> 같은 출력
    tg = {"id": "x", "anc": (0.0, 0.0), "text": "AB", "dir": None, "off": (400.0, 400.0), "side": "start"}
    c1, c2 = LY.candidates(tg, 100.0), LY.candidates(tg, 100.0)
    assert c1 == c2 and c1[0] == ((400.0, 400.0), "start") and len(c1) == len(set(c1)) == 1 + 5 * 7
    assert all(sd == "end" for o, sd in c1[1:] if o[0] < 0) and all(sd == "start" for o, sd in c1[1:] if o[0] > 0)
    assert all(not (o[0] == 0 and o[1] < 0) for o, _ in c1)   # 남쪽(지시선이 글을 가로지른다)은 없다
    ax = dict(tg, dir=(1.0, 0.0), off=(0.0, 0.0))
    ca = LY.candidates(ax, 100.0)
    assert ca[0] == ((0.0, 0.0), "start") and len(ca) == len(set(ca)) == 1 + 14 + 5 * 3
    assert all(o[1] == 0.0 for o, _ in ca[1:15]) and all(o[1] > 0 for o, _ in ca[15:])   # 가로 보: 먼저 보를 따라 미끄러지고, 그다음 글 위쪽으로 든다
    assert not LY.lead_on((1.0, 0.0), (1914.0, 0.0)) and LY.lead_on((1.0, 0.0), (0.0, 500.0)) and LY.lead_on(None, (0.0, 0.0))


# ---------------------------------------------------------------- 1. 태그 지시선
def test_1_이전_태그는_저장_형식과_그림_항목이_그대로다(ws):
    wt = tag(ws, F.W1, tid=TID(1))
    p = get(ws, wt)["params"]
    assert set(p) == {"name", "view", "target", "fields", "off", "home"} and p["off"] == [400.0, 400.0]   # 새 키(leader·side·pin)가 없다
    assert items(ws)[wt] == ("tag", wt, (3000.0, 0.0), (3400.0, 400.0), "W-000001")                       # 5칸 그대로
    it = [x for x in ok(ws.run("annot.list", {}))["items"] if x["id"] == wt][0]
    assert "layout" not in it
    beam_type(ws)
    beam(ws, BA, (0, -1000), (6000, -1000))
    bt = tag(ws, BA, ("type",), tid=TID(2))
    assert items(ws)[bt] == ("tag", bt, (3000.0, -1000.0), (3000.0, -1000.0), "B1", (1.0, 0.0))            # 6칸 그대로
    assert set(get(ws, bt)["params"]) == {"name", "view", "target", "fields", "off", "home", "align"}


def test_1_지시선_켜기_끄기_끝점_부착_자유_꺾임점(ws):
    wt = tag(ws, F.W1, tid=TID(1))
    anc, lab = (3000.0, 0.0), (3400.0, 400.0)
    # 끄기: 선·점 없이 글만(글 자리는 그대로). 고정(pin)이 켜진다
    r = ok(ws.run("annot.tag.set", {"id": wt, "leader": {"on": False}}))
    assert r["leader"] == {"on": False} and r["pin"] is True
    assert items(ws)[wt] == ("tag", wt, anc, lab, "W-000001", None, (), "start")
    # 기본과 같은 설정(켜짐·부착·꺾임 없음)은 키를 두지 않는다 -> 이전 모양(5칸)으로 돌아온다
    ok(ws.run("annot.tag.set", {"id": wt, "leader": {"on": True}}))
    assert "leader" not in get(ws, wt)["params"] and items(ws)[wt] == ("tag", wt, anc, lab, "W-000001")
    # 꺾임점: 끝점(부착) -> 꺾임점 -> 글 자리
    ok(ws.run("annot.tag.set", {"id": wt, "leader": {"on": True, "bend": [200, 600]}}))
    assert items(ws)[wt][6] == (anc, (3200.0, 600.0), lab)
    # 자유 끝점: 끝점 = 대표점 + tip
    ok(ws.run("annot.tag.set", {"id": wt, "leader": {"on": True, "end": "free", "tip": [-500, 300]}}))
    assert items(ws)[wt][6] == ((2500.0, 300.0), lab)
    ok(ws.run("annot.tag.set", {"id": wt, "leader": {"on": True, "end": "free", "tip": [-500, 300], "bend": [200, 600]}}))
    assert items(ws)[wt][6] == ((2500.0, 300.0), (3200.0, 600.0), lab)
    assert get(ws, wt)["params"]["leader"] == {"on": True, "end": "free", "tip": [-500.0, 300.0], "bend": [200.0, 600.0]}
    # 글 자리와 맞춤: 가운데·왼쪽 맞춤
    ok(ws.run("annot.tag.set", {"id": wt, "offset": [0, 800], "side": "middle", "leader": {"on": True}}))
    assert items(ws)[wt] == ("tag", wt, anc, (3000.0, 800.0), "W-000001", None, (anc, (3000.0, 800.0)), "middle")
    ok(ws.run("annot.tag.set", {"id": wt, "offset": [-300, 500], "side": "end"}))
    assert items(ws)[wt][7] == "end" and items(ws)[wt][3] == (2700.0, 500.0)
    ok(ws.run("annot.tag.set", {"id": wt, "side": "start"}))
    assert "side" not in get(ws, wt)["params"]
    # 목록에는 쓴 설정만 layout 으로 나온다
    lay = [x for x in ok(ws.run("annot.list", {}))["items"] if x["id"] == wt][0]["layout"]
    assert lay == {"off": [-300.0, 500.0], "pin": True}                                           # 마지막 설정이 기본 지시선이라 leader 키는 없다
    ok(ws.run("annot.tag.set", {"id": wt, "leader": {"on": True, "end": "free", "tip": [1, 2]}}))
    lay = [x for x in ok(ws.run("annot.list", {}))["items"] if x["id"] == wt][0]["layout"]
    assert lay["leader"] == {"on": True, "end": "free", "tip": [1.0, 2.0]}


def test_1_지시선은_대상을_따라간다_부착도_자유도(ws):
    beam_type(ws)
    beam(ws, BA, (0, -1000), (6000, -1000))
    t = tag(ws, BA, ("type",), tid=TID(1), align="leader", offset=[500, 700])
    ok(ws.run("annot.tag.set", {"id": t, "leader": {"on": True, "end": "free", "tip": [-1000, 200], "bend": [300, 900]}}))
    before = items(ws)[t]
    ok(ws.run("beam.move", {"id": BA, "dx": 1500, "dy": 800}))
    after = items(ws)[t]
    # 기준점·글 자리·끝점·꺾임점이 모두 같은 만큼 옮겨진다(대표점 기준 간격이라 모양이 그대로)
    d = (after[2][0] - before[2][0], after[2][1] - before[2][1])
    assert d == (1500.0, 800.0)
    assert after[3] == (before[3][0] + d[0], before[3][1] + d[1])
    assert after[6] == tuple((p[0] + d[0], p[1] + d[1]) for p in before[6])
    ok(ws.run("history.undo", {}))
    assert items(ws)[t] == before
    # 대상을 지우면 고아(조용히 지우지 않는다) 이고 지시선 설정은 되돌림으로 되살아난다
    ok(ws.run("element.delete", {"id": BA}))
    assert g_of(ws).q(AN.annot_state, t)[:2] == ("tag", "orphan")
    assert err(ws.run("annot.tag.set", {"id": t, "offset": [1, 1]}))["code"] == "CONSTRAINT"      # 고아는 먼저 다시 묶는다
    ok(ws.run("history.undo", {}))
    assert items(ws)[t] == before


def test_1_보_태그는_옮기면_지시선이_켜진다(ws):
    beam_type(ws)
    beam(ws, BA, (0, -1000), (6000, -1000))
    t = tag(ws, BA, ("type", "size"), tid=TID(1))
    ok(ws.run("annot.tag.set", {"id": t, "offset": [0, 600], "leader": {"on": True}}))
    it = items(ws)[t]
    assert it == ("tag", t, (3000.0, -1000.0), (3000.0, -400.0), "B1 H-400x200", (1.0, 0.0), ((3000.0, -1000.0), (3000.0, -400.0)), "start")
    assert get(ws, t)["params"]["leader"] == {"on": True}                      # 보 축 방향 태그의 기본은 꺼짐이라 키가 생긴다
    ok(ws.run("annot.tag.set", {"id": t, "leader": {"on": False}}))
    assert "leader" not in get(ws, t)["params"] and len(items(ws)[t]) == 6                  # 기본(꺼짐)과 같으면 키를 지운다
    # 보 축 방향 글은 가운데 맞춤만
    assert err(ws.run("annot.tag.set", {"id": t, "side": "end"}))["code"] == "CONSTRAINT"


def test_1_고정과_풀기_되돌리기(ws):
    wt = tag(ws, F.W1, tid=TID(1))
    assert err(ws.run("annot.tag.set", {"id": wt}))["code"] == "CONSTRAINT"                      # 바꿀 필드 없음
    ok(ws.run("annot.tag.set", {"id": wt, "offset": [700, 300]}))                                # 손으로 옮기면 고정이 기본
    assert get(ws, wt)["params"]["pin"] is True and get(ws, wt)["params"]["off"] == [700.0, 300.0]
    assert get(ws, wt)["params"]["home"] == [3700.0, 300.0]
    ok(ws.run("annot.tag.set", {"id": wt, "pin": False}))
    assert "pin" not in get(ws, wt)["params"] and get(ws, wt)["params"]["off"] == [700.0, 300.0]   # 풀기만: 자리는 그대로
    ok(ws.run("annot.tag.set", {"id": wt, "offset": [900, 100], "pin": False}))                  # 옮기면서 고정하지 않기
    assert "pin" not in get(ws, wt)["params"]
    ok(ws.run("annot.tag.set", {"id": wt, "side": "end", "leader": {"on": False}}))
    ok(ws.run("annot.tag.set", {"id": wt, "reset": True}))                                       # 전부 기본으로
    assert set(get(ws, wt)["params"]) == {"name", "view", "target", "fields", "off", "home"} and get(ws, wt)["params"]["off"] == [400.0, 400.0]
    assert items(ws)[wt] == ("tag", wt, (3000.0, 0.0), (3400.0, 400.0), "W-000001")
    ok(ws.run("history.undo", {}))
    assert get(ws, wt)["params"]["side"] == "end"


def test_1_거절_사례(ws):
    wt = tag(ws, F.W1, tid=TID(1))
    beam_type(ws)
    beam(ws, BA, (0, -1000), (6000, -1000))
    bt = tag(ws, BA, ("type",), tid=TID(2))
    cases = [
        ({"id": F.W1, "offset": [1, 1]}, "CONSTRAINT"),                                            # 태그가 아님
        ({"id": wt, "leader": {"on": True, "end": "free"}}, "CONSTRAINT"),                         # 자유 끝점에 tip 없음
        ({"id": wt, "leader": {"on": True, "tip": [1, 1]}}, "CONSTRAINT"),                         # 부착인데 tip
        ({"id": wt, "leader": {"on": False, "bend": [1, 1]}}, "CONSTRAINT"),                       # 끈 지시선에 꺾임점
        ({"id": bt, "side": "middle"}, "CONSTRAINT"),                                              # 눕힌 글에 맞춤
        ({"id": wt, "reset": True, "offset": [1, 1]}, "CONSTRAINT"),                               # reset 은 단독
        ({"id": wt, "offset": [1, 2, 3]}, "SCHEMA_INVALID"),
        ({"id": wt, "offset": [1e9, 0]}, "SCHEMA_INVALID"),
        ({"id": wt, "side": "up"}, "SCHEMA_INVALID"),
        ({"id": wt, "leader": {"on": True, "end": "tangent"}}, "SCHEMA_INVALID"),
        ({"id": wt, "pin": None}, "SCHEMA_INVALID"),                                               # 명시적 null 은 거절
    ]
    for args, code in cases:
        assert err(ws.run("annot.tag.set", args))["code"] == code, args
    assert err(ws.run("annot.tag.arrange", {"view": V, "ids": ["x"]}))["code"] == "SCHEMA_INVALID"
    assert err(ws.run("annot.tag.arrange", {"view": F.W1}))["code"] == "NOT_FOUND"
    assert get(ws, wt)["params"]["off"] == [400.0, 400.0]                                           # 거절은 아무것도 바꾸지 않는다


# ---------------------------------------------------------------- 2. 출력·증분·왕복
def test_2_SVG_PDF_DXF_시트_GUI_장면(ws):
    wt = tag(ws, F.W1, tid=TID(1))
    plain = tag(ws, F.W2, tid=TID(2))

    def exp(name):
        assert destructive(ws, "view.export", {"id": V, "path": name, "overwrite": True})["ok"]
        return (ws.path.parent / name).read_bytes()

    base_svg = exp("a.svg").decode("utf-8")

    def blk(doc, t):
        i = doc.index(f'<g class="tag" data-id="{t}"')
        return doc[i:doc.index("</g>", i)]

    ok(ws.run("annot.tag.set", {"id": wt, "offset": [-600, 500], "side": "end", "leader": {"on": True, "bend": [-200, 500], "end": "free", "tip": [-900, 100]}}))
    svg = exp("b.svg").decode("utf-8")
    b = blk(svg, wt)
    assert b.count("<line") == 2 and b.count("<circle") == 1 and 'text-anchor="end"' in b and "W-000001" in b    # 꺾은선 두 선분, 시작점에 점, 오른쪽 맞춤 글
    assert re.sub(r"-?\d+\.\d+", "N", blk(svg, plain)) == re.sub(r"-?\d+\.\d+", "N", blk(base_svg, plain))      # 옵션 없는 태그 모양 그대로
    ok(ws.run("annot.tag.set", {"id": wt, "leader": {"on": False}, "side": "middle"}))
    b2 = blk(exp("c.svg").decode("utf-8"), wt)
    assert "<line" not in b2 and "<circle" not in b2 and 'text-anchor="middle"' in b2
    ok(ws.run("annot.tag.set", {"id": wt, "side": "end", "leader": {"on": True, "bend": [-200, 500]}}))
    pdf = exp("b.pdf")
    assert pdf.startswith(b"%PDF") and b" Td " in pdf
    assert "W-000001" in exp("b.dxf").decode("utf-8")
    ok(ws.run("sheet.define", {"id": F.SH1, "name": "A-101", "views": [V], "paper": "A3", "project_name": "예시", "sheet_number": "A-101", "date": "2026-01-01"}))
    assert destructive(ws, "sheet.render", {"id": F.SH1, "path": "s.svg", "overwrite": True})["ok"]
    assert "W-000001" in (ws.path.parent / "s.svg").read_text(encoding="utf-8")
    # GUI 장면: 꺾은선의 선분이 집기 대상이고 경계 점에 꺾임점이 든다
    from dobim_core import scene as SC
    sc = {(x[0], x[1]): x for x in g_of(ws).q(SC.view_scene, V)}
    its = sc[("tag", wt)][5]
    assert len(its) == 1 and len(its[0]) == 8
    assert SC._annot_segs(its[0]) == [(3000.0, 0.0, 2800.0, 500.0), (2800.0, 500.0, 2400.0, 500.0)]
    pts = SC._annot_points(its[0])
    assert (2800.0, 500.0) in pts and (2400.0, 500.0) in pts
    for _ in range(3):   # 시트 정의 + 설정 셋: 옛 도면과 바이트 같게 돌아온다
        ok(ws.run("history.undo", {}))
    ok(ws.run("annot.tag.set", {"id": wt, "reset": True}))
    assert exp("d.svg").decode("utf-8") == base_svg


def test_2_증분_전체_계산이_같다(ws):
    beam_type(ws)
    beam(ws, BA, (0, -1000), (6000, -1000))
    t1, t2 = tag(ws, F.W1, tid=TID(1)), tag(ws, BA, ("type",), tid=TID(2))
    ok(ws.run("annot.tag.set", {"id": t1, "offset": [500, 700], "leader": {"on": True, "end": "free", "tip": [-300, 100], "bend": [100, 800]}}))
    ok(ws.run("annot.tag.set", {"id": t2, "offset": [0, 700], "leader": {"on": True}}))

    def same():
        g = g_of(ws)
        full = s3lib.RefGraph(s3lib._Inputs(ws.eng))
        assert g.q(AN.annot_bundle) == full.q(AN.annot_bundle)
        assert g.q(AN.view_annots, V) == full.q(AN.view_annots, V)

    same()
    ok(ws.run("beam.move", {"id": BA, "dx": 700, "dy": 100}))
    same()
    ok(ws.run("wall.move", {"id": F.W1, "dx": 100, "dy": 0}))
    same()
    ok(ws.run("annot.tag.arrange", {"view": V}))
    same()
    ok(ws.run("history.undo", {}))
    same()


def test_2_저장하고_다시_열기와_IFC_왕복(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
        ok(b.run("beamtype.create", {"id": IBT, "name": "B1", "preset": "H-400x200x8x13"}))
        ok(b.run("beam.create", {"id": IBM, "level": F.LV1, "type": IBT, "start": [0, -3000], "end": [6000, -3000], "offset": 2400}))
        t1 = ok(b.run("annot.tag.create", {"view": V, "target": F.W1, "fields": ["name"]}))["id"]
        t2 = ok(b.run("annot.tag.create", {"view": V, "target": IBM, "fields": ["type", "size"]}))["id"]
        ok(b.run("annot.tag.set", {"id": t1, "offset": [-500, 600], "side": "end", "leader": {"on": True, "end": "free", "tip": [-200, 100], "bend": [-100, 600]}}))
        ok(b.run("annot.tag.set", {"id": t2, "offset": [0, 700], "leader": {"on": True}}))
        h = b.hashes()
        out = b.export("b.ifc")
        assert file_errors(ifcopenshell.open(str(out))) == []                                    # IFC4 검증 오류 0(태그 정의는 DoBIM_Defs 속성)
        assert b.destructive("ifc.export", {"path": "b3.ifc", "schema": "IFC2X3"})["ok"]
        assert file_errors(ifcopenshell.open(str(b.ws / "b3.ifc"))) == []                        # IFC2X3 검증 오류 0
        r = b.import_ifc("b.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)                                        # 태그 정의(leader·side·pin 포함)까지 같다
        assert b.export("b2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        for t in (t1, t2):
            assert ok(b.run("element.get", {"id": t}, project="imp.dobim"))["params"] == ok(b.run("element.get", {"id": t}))["params"]
        p1 = ok(b.run("element.get", {"id": t1}, project="imp.dobim"))["params"]
        assert p1["leader"]["end"] == "free" and p1["side"] == "end" and p1["pin"] is True
    finally:
        b.close()


def test_2_저장하고_다시_열면_그림_항목이_같다(tmp_path):
    s = kit.Session(tmp_path / "b.dobim")
    try:
        for name, args in F.SCRIPT:
            ok(s.run(name, args))
        ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1}))
        t = tag(s, F.W1, tid=TID(1))
        ok(s.run("annot.tag.set", {"id": t, "offset": [800, 500], "side": "middle", "leader": {"on": True, "bend": [300, 500]}}))
        before = (get(s, t)["params"], items(s))
    finally:
        s.close()
    s = kit.Session(tmp_path / "b.dobim", create=False)
    try:
        assert (get(s, t)["params"], items(s)) == before
    finally:
        s.close()


# ---------------------------------------------------------------- 3. 겹침 회피 배치
def stack(s, n=4, target=F.W1):
    """같은 대상에 필드가 다른 태그 n 개: 모두 같은 기본 자리라 글이 겹친다."""
    flds = (("name",), ("name", "level"), ("ifc_class",), ("material",), ("level",), ("name", "ifc_class"))
    return [tag(s, target, flds[i], tid=TID(10 + i)) for i in range(n)]


def test_3_재현_기본_자리는_겹치고_배치하면_겹치지_않는다(ws):
    ids = stack(ws, 5)
    assert len(overlaps(ws)) >= 5                                                                   # 이전 동작: 같은 자리에 그대로 쌓인다
    r = ok(ws.run("annot.tag.arrange", {"view": V}))
    assert r["unresolved"] == [] and r["pinned"] == 0 and len(r["moved"]) == 4 and r["kept"] == 1   # 하나는 기본 자리에 두고 네 개가 옮겨진다
    assert overlaps(ws) == []
    # 글 상자 사이에 지시선이 지나가지도 않는다(지시선이 다른 글 상자를 가로지르지 않는다)
    sc = float(get(ws, V)["params"]["scale"])
    bx = boxes_of(ws)
    for t, it in items(ws).items():
        _a, _l, _x, _u, pts, _s = LY.tag_parts(it)
        for a, b in zip(pts, pts[1:]):
            for t2, b2 in bx.items():
                if t2 != t:
                    assert not LY.seg_hits_box(tuple(a) + tuple(b), LY.grow(b2, LY.PAD * sc * 0.5)), (t, t2)
    # 같은 대상에 쌓았던 다섯 개: 기준점은 같고 자리만 다르다
    assert len({items(ws)[t][3] for t in ids}) == 5 and len({items(ws)[t][2] for t in ids}) == 1


def test_3_결정적이고_멱등이다_만든_순서와_무관(tmp_path):
    def build(order, name):
        s = kit.Session(tmp_path / name)
        for n_, a_ in F.SCRIPT:
            ok(s.run(n_, a_))
        ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1}))
        for i in order:
            for tgt in (F.W1, F.W2):
                flds = (("name",), ("name", "level"), ("ifc_class",), ("material",))[i]
                tag(s, tgt, flds, tid=TID(10 * (1 if tgt == F.W1 else 2) + i))
        ok(s.run("annot.tag.arrange", {"view": V}))
        return s

    s1, s2 = build([0, 1, 2, 3], "a.dobim"), build([3, 1, 0, 2], "b.dobim")
    try:
        rows = lambda s: {t: (get(s, t)["params"]["off"], get(s, t)["params"].get("side")) for t in items(s)}   # noqa: E731
        assert rows(s1) == rows(s2) and len(rows(s1)) == 8
        assert overlaps(s1) == [] and items(s1) == items(s2)
        # 멱등: 다시 배치해도 아무것도 옮기지 않고 상태가 같다
        before = get(s1, TID(10))["params"], items(s1)
        r = ok(s1.run("annot.tag.arrange", {"view": V}))
        assert r["moved"] == [] and r["kept"] == 8 and (get(s1, TID(10))["params"], items(s1)) == before
    finally:
        s1.close()
        s2.close()


def test_3_고정_태그는_덮어쓰지_않고_장애물이_된다(ws):
    ids = stack(ws, 4)
    ok(ws.run("annot.tag.set", {"id": ids[0], "offset": [900, 0]}))                                  # 손으로 옮긴 태그
    pinned_before = get(ws, ids[0])["params"]
    ok(ws.run("annot.tag.set", {"id": ids[1], "offset": [400, 400]}))                                # 기본 자리에 손으로 고정
    r = ok(ws.run("annot.tag.arrange", {"view": V}))
    assert r["pinned"] == 2 and {m["id"] for m in r["moved"]} <= set(ids[2:])
    assert get(ws, ids[0])["params"] == pinned_before and get(ws, ids[1])["params"]["off"] == [400.0, 400.0]
    assert overlaps(ws) == []                                                                       # 고정 태그를 피해서 놓았다
    # ids 로 일부만 배치: 나머지는 움직이지 않는 장애물
    ok(ws.run("annot.tag.set", {"id": ids[3], "pin": False, "offset": [400, 400]}))
    ok(ws.run("annot.tag.set", {"id": ids[3], "pin": False}))
    r2 = ok(ws.run("annot.tag.arrange", {"view": V, "ids": [ids[3]]}))
    assert {m["id"] for m in r2["moved"]} <= {ids[3]} and overlaps(ws) == []
    # 풀어 주면 다음 배치에서 움직일 수 있다
    ok(ws.run("annot.tag.set", {"id": ids[1], "pin": False}))
    assert "pin" not in get(ws, ids[1])["params"]


def test_3_치수_글_치수선과_문자_주석을_피한다(ws):
    wt = tag(ws, F.W1, tid=TID(1))
    # 기본 글 자리(3400, 400)에 문자 주석을 놓는다
    ok(ws.run("draft.create", {"view": V, "shape": "text", "pts": [[3500, 500]], "text": "상세 참조", "size": 3.0, "align": "start"}))
    sc = float(get(ws, V)["params"]["scale"])
    from dobim_core import drafting as DRF
    draft_boxes = lambda: [LY.text_box_at((it[4], it[5]), it[7], it[6], it[8], -it[9], sc) for it in g_of(ws).q(DRF.view_drafts, V) if it[0] == "text"]   # noqa: E731
    box = lambda: boxes_of(ws)[wt]                                                                  # noqa: E731
    assert LY.overlap_area(box(), draft_boxes()[0]) > 0.0                                          # 이전 동작: 문자 위에 그대로 얹힌다
    r = ok(ws.run("annot.tag.arrange", {"view": V}))
    assert [m["id"] for m in r["moved"]] == [wt] and r["unresolved"] == []
    assert LY.overlap_area(LY.grow(box(), LY.PAD * sc), LY.grow(draft_boxes()[0], 0.0)) == 0.0
    # 치수: 벽 W2 의 길이 치수 글·치수선 위를 지나는 자리를 피한다
    w2t = tag(ws, F.W2, tid=TID(2), offset=[-200, 1000])
    d = ok(ws.run("annot.dim.create", {"view": V, "a": f"wall:{F.W2}#trim.start", "b": f"wall:{F.W2}#trim.end", "offset": -600}))["id"]
    dim = [it for it in g_of(ws).q(AN.view_annots, V) if it[0] == "dim" and it[1] == d][0]
    dbox = LY.tag_box(tuple(dim[6]), dim[5], tuple(dim[7]), "start", sc)
    r = ok(ws.run("annot.tag.arrange", {"view": V}))
    assert r["unresolved"] == []
    b2 = boxes_of(ws)[w2t]
    assert LY.overlap_area(LY.grow(b2, LY.PAD * sc), LY.grow(dbox, LY.PAD * sc)) == 0.0
    for e in (dim[2], dim[3], dim[4]):
        assert not LY.seg_hits_box(tuple(e), LY.grow(b2, LY.PAD * sc))                              # 치수선·보조선이 글 상자를 지나지 않는다


def test_3_보_태그는_겹치면_위로_올리고_지시선이_켜진다(ws):
    beam_type(ws)
    # 간격 300 mm 로 나란한 보 다섯 개: 가운데 글(높이 250)끼리 겹친다
    bs = [beam(ws, "9" + "0" * 19 + f"m{i}", (0, 300 * i), (6000, 300 * i)) and ("9" + "0" * 19 + f"m{i}") for i in range(5)]
    tg = [tag(ws, b, ("type", "size"), tid=TID(20 + i)) for i, b in enumerate(bs)]
    assert len(overlaps(ws)) >= 4                                                                   # 이전 동작: 같은 높이 간격이라 글이 서로 덮는다
    r = ok(ws.run("annot.tag.arrange", {"view": V}))
    assert r["unresolved"] == [] and overlaps(ws) == []
    moved = {m["id"]: m for m in r["moved"]}
    assert set(moved) == {tg[1], tg[3]}                                                              # 이웃과 겹치는 태그만 옮겨진다(먼저 놓는 태그와 겹치지 않는 것은 그대로)
    for t in tg:
        p = get(ws, t)["params"]
        if t in moved:   # 보(길이 6000)를 따라 미끄러질 자리(글 폭 1740 -> 한쪽 2130 이내)가 모자라 위로 든다: 지시선이 켜진다. 안 옮겨진 태그는 지시선이 없다
            assert p["off"][1] > 0 and p["leader"]["on"] is True and moved[t]["leader"] is True
        else:
            assert "leader" not in p and len(items(ws)[t]) == 6                                      # 모양은 이전 보 태그와 같다(6칸)
        assert items(ws)[t][5] == (1.0, 0.0)                                                         # 글은 보를 따라 눕는다
    # 고정한 태그는 손대지 않는다
    ok(ws.run("annot.tag.set", {"id": tg[2], "reset": True}))
    ok(ws.run("annot.tag.set", {"id": tg[2], "pin": True}))
    ok(ws.run("annot.tag.arrange", {"view": V}))
    assert get(ws, tg[2])["params"]["off"] == [0.0, 0.0] and get(ws, tg[2])["params"]["pin"] is True


def test_3_치수선이_보_가운데_줄을_막으면_위로_들고_자유_끝점_지시선이_켜진다(ws):
    beam_type(ws)
    beam(ws, BA, (0, -1000), (6000, -1000))
    t = tag(ws, BA, ("type", "size"), tid=TID(40))
    # 벽 W1 의 길이 치수(치수선 y = -850)가 보 가운데 줄(글 상자 y -970..-720)을 가로지른다: 보를 따라 미끄러지는 자리가 모두 막힌다
    d = ok(ws.run("annot.dim.create", {"view": V, "a": f"wall:{F.W1}#trim.start", "b": f"wall:{F.W1}#trim.end", "offset": -850}))["id"]
    sc = float(get(ws, V)["params"]["scale"])
    dim = [it for it in g_of(ws).q(AN.view_annots, V) if it[0] == "dim" and it[1] == d][0]
    assert LY.seg_hits_box(tuple(dim[4]), boxes_of(ws)[t])                                         # 이전 동작: 치수선이 태그 글을 가로지른다
    r = ok(ws.run("annot.tag.arrange", {"view": V}))
    assert r["unresolved"] == [] and [m["id"] for m in r["moved"]] == [t] and r["moved"][0]["leader"] is True
    p, it = get(ws, t)["params"], items(ws)[t]
    # 글은 치수선 위로 들렸고 지시선은 글 바로 밑의 보 위 점(자유 끝점)에서 곧게 올라간다(치수 글 상자를 지나지 않는다)
    assert p["off"][1] > 0 and p["leader"]["end"] == "free" and len(it) == 8 and it[5] == (1.0, 0.0)
    assert it[6] == ((it[3][0], it[2][1]), it[3]) and it[6][0][0] != it[2][0]
    box = boxes_of(ws)[t]
    dbox = LY.tag_box(tuple(dim[6]), dim[5], tuple(dim[7]), "start", sc)
    assert LY.overlap_area(LY.grow(box, LY.PAD * sc), LY.grow(dbox, LY.PAD * sc)) == 0.0
    for e in (dim[2], dim[3], dim[4]):
        assert not LY.seg_hits_box(tuple(e), LY.grow(box, LY.PAD * sc))                            # 치수선·보조선이 글 상자를 지나지 않는다
    assert not LY.seg_hits_box(tuple(it[6][0]) + tuple(it[6][1]), LY.grow(dbox, LY.PAD * sc * 0.5))
    # 다시 배치해도 그대로(멱등). 대상 보를 옮기면 글·지시선이 함께 따라간다
    assert ok(ws.run("annot.tag.arrange", {"view": V}))["moved"] == []
    ok(ws.run("beam.move", {"id": BA, "dx": 1000, "dy": 500}))
    a, b = it, items(ws)[t]
    assert b[3] == (a[3][0] + 1000.0, a[3][1] + 500.0) and b[6] == tuple((q[0] + 1000.0, q[1] + 500.0) for q in a[6])


def test_3_보_하나에_태그가_여럿이면_보를_따라_미끄러진다(ws):
    beam_type(ws)
    beam(ws, BA, (0, 0), (6000, 0))
    flds = [("type",), ("width",), ("depth",), ("length",), ("offset",)]
    tg = [tag(ws, BA, f, tid=TID(40 + i)) for i, f in enumerate(flds)]
    assert len(overlaps(ws)) >= 8
    r = ok(ws.run("annot.tag.arrange", {"view": V}))
    assert r["unresolved"] == [] and overlaps(ws) == [] and len(r["moved"]) == 4
    half = 3000.0                                                                                    # 글 중심이 보(길이 6000) 밖으로 나가지 않는다
    assert all(abs(get(ws, t)["params"]["off"][0]) <= half for t in tg)
    for t in tg:   # 보 위에서 미끄러졌을 뿐이라 지시선 없이 이전 보 태그 모양(6칸)이다
        p, it = get(ws, t)["params"], items(ws)[t]
        assert p["off"][1] == 0.0 and "leader" not in p and len(it) == 6 and it[5] == (1.0, 0.0)
    assert ok(ws.run("annot.tag.arrange", {"view": V}))["moved"] == []
    # 한 번 옮겨진 태그는 자리가 비어도 지금 자리를 먼저 본다(옮김을 최소로)
    ok(ws.run("annot.delete", {"id": tg[0]}))
    assert ok(ws.run("annot.tag.arrange", {"view": V}))["moved"] == []


def test_3_고아_태그는_건너뛰고_겹침을_해결_못하면_알린다(ws):
    beam_type(ws)
    beam(ws, BA, (0, -1000), (6000, -1000))
    ob = tag(ws, BA, ("type",), tid=TID(1))
    ids = stack(ws, 6)
    ok(ws.run("element.delete", {"id": BA}))
    r = ok(ws.run("annot.tag.arrange", {"view": V}))
    assert r["skipped"] == [ob] and any("고아" in w for w in ws.run("annot.tag.arrange", {"view": V})["warnings"])
    # 후보 자리를 모두 막는 고정 태그 더미: 겹침을 못 피한 태그를 알리고 가장 덜 겹치는 자리에 놓는다(결정적)
    from dobim_core import annot_layout as L
    tgs = [{"id": f"t{i}", "anc": (0.0, 0.0), "text": "ABCDEFGH", "dir": None, "off": (400.0, 400.0), "side": "start"} for i in range(3)]
    wall = [(-1e5, -1e5, 1e5, 1e5)]   # 모든 자리를 덮는 큰 상자
    res, bad = L.arrange(tgs, wall, [], 100.0)
    res2, bad2 = L.arrange(list(reversed(tgs)), wall, [], 100.0)
    assert bad == bad2 == ["t0", "t1", "t2"] and res == res2
    assert res["t0"][0] == (400.0, 400.0) and len({res[t] for t in res}) == 3                       # 먼저 놓는 태그는 점수가 같으면 앞선 후보(지금 자리), 나머지는 서로 피한다
    assert len(ids) == 6


def test_3_avoid_옵션_create와_all(ws):
    first = tag(ws, F.W1, tid=TID(1))
    r = ok(ws.run("annot.tag.create", {"id": TID(2), "view": V, "target": F.W1, "fields": ["name", "level"], "avoid": True}))
    assert r["placed"]["moved"] is True and r["placed"]["unresolved"] is False
    assert overlaps(ws) == [] and items(ws)[first][3] == (3400.0, 400.0)                           # 기존 태그는 그대로
    assert "pin" not in get(ws, TID(2))["params"]                                                    # 자동 배치한 태그는 고정이 아니다
    # avoid 가 없으면 이전 동작(겹침)
    ok(ws.run("annot.tag.create", {"id": TID(3), "view": V, "target": F.W1, "fields": ["ifc_class"]}))
    assert overlaps(ws) != []
    ok(ws.run("history.undo", {}))
    # 일괄 태그 + avoid: 새 태그끼리·기존 태그와 겹치지 않는다
    for tgt in (F.W3, F.D2):
        tag(ws, tgt, ("name",), tid=TID(30 if tgt == F.W3 else 31))
    ok(ws.run("annot.delete", {"id": TID(31)}))
    r2 = ok(ws.run("annot.tag.all", {"view": V, "kinds": ["wall", "door"], "fields": ["name"], "avoid": True}))
    assert r2["arranged"]["unresolved"] == [] and overlaps(ws) == []
    # 이전 호출(avoid 없음)의 응답에는 새 키가 없다
    ok(ws.run("annot.delete", {"id": r2["created"][0]}))
    assert "arranged" not in ok(ws.run("annot.tag.all", {"view": V, "kinds": ["wall", "door"], "fields": ["name"]}))
    assert err(ws.run("annot.tag.create", {"view": V, "target": F.W1, "fields": ["name"], "avoid": 3}))["code"] == "SCHEMA_INVALID"


def test_3_되돌리기와_증분_전체_계산(ws):
    ids = stack(ws, 4)
    h0 = (get(ws, ids[1])["params"], items(ws))
    ok(ws.run("annot.tag.arrange", {"view": V}))
    assert overlaps(ws) == []
    g = g_of(ws)
    full = s3lib.RefGraph(s3lib._Inputs(ws.eng))
    assert g.q(AN.annot_bundle) == full.q(AN.annot_bundle)
    ok(ws.run("history.undo", {}))
    assert (get(ws, ids[1])["params"], items(ws)) == h0 and overlaps(ws) != []
    ok(ws.run("history.redo", {}))
    assert overlaps(ws) == []
    # 대상을 옮기면 배치한 태그가 같은 간격으로 따라간다(다시 배치하기 전까지)
    t = ids[2]
    off = get(ws, t)["params"]["off"]
    ok(ws.run("wall.move", {"id": F.W1, "dx": 0, "dy": 300}))
    assert get(ws, t)["params"]["off"] == off


def test_3_배치는_평면_뷰만이고_태그가_없으면_아무것도_안_한다(ws):
    r = ok(ws.run("annot.tag.arrange", {"view": V}))
    assert r == {"view": V, "moved": [], "kept": 0, "pinned": 0, "unresolved": [], "skipped": []}
    ok(ws.run("view.create", {"id": F.V2, "name": "입면", "type": "elevation", "dir": "S"}))
    assert err(ws.run("annot.tag.arrange", {"view": F.V2}))["code"] == "CONSTRAINT"
