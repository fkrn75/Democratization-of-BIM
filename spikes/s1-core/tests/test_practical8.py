# SPDX-License-Identifier: MPL-2.0
"""26단계(실무 모델링 8): also_cuts 맞닿음 허용·함께 자른 개구부의 벽 수량 빼기, window.framed 가로대·단×칸별 열림 방식,
옆 경첩 프로젝트(project_left/right), window.storefront 칸별 열림·낮은 높이·세로대 폭 목록(cols),
평행하게 어긋난 다음 구간을 전개 창호 사슬에 잇기(step). 합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell
import ifcopenshell.util.element  # noqa: F401
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import queries as Q

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402


def gid(c, n):
    return c + f"{n:021d}"


def params(sess, eid, project=None):
    kw = {"project": project} if project else {}
    return sess.run("element.get", {"id": eid}, **kw)["result"]["params"]


def derived(sess, eid):
    return sess.run("element.get", {"id": eid})["result"].get("derived") or {}


def props(sess, eid):
    return sess.run("element.get", {"id": eid})["result"].get("psets") or {}


def walls(sess, axes, level=F.LV1, height=3000, start=10):
    ids = []
    for i, ax in enumerate(axes):
        w = gid("1", start + i)
        r = sess.run("wall.create", {"id": w, "level": level, "axis": ax, "thickness": 200, "height": height})
        assert r["ok"], r
        ids.append(w)
    return ids


def openings(sess, w):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.wall_openings, w)


def net_area(sess, w):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(Q.wall_row, w)[7]


def body_ids(sess, tid):
    pv = sess.run("family.preview", {"type": tid})["result"]
    return [b["id"] for b in pv["body"]], pv


def _panels(f, guid):
    t = ifcopenshell.util.element.get_type(f.by_guid(guid))
    return t, [(p.OperationType, p.PanelPosition) for p in (t.HasPropertySets or ()) if p.is_a("IfcWindowPanelProperties")]


WF = {"family": "window.framed", "type_values": {"width": 1200}}


# ---------------------------------------------------------------- 1. also_cuts: 이웃 벽 끝에 맞닿음
def test_창_끝이_같은_선_이웃_벽_끝에_맞닿으면_also_cuts_로_받는다(base):
    # 호스트 0~3000, 같은 선 이웃 벽 3060~6000(사이 60 = 틀 자리 틈). 창 2160~3060 은 끝이 이웃 벽 시작에 정확히 맞닿는다
    a, b = walls(base, [[0, 9000, 3000, 9000], [3060, 9000, 6000, 9000]], start=60)
    tv = {"family": "window.framed", "type_values": {"width": 900}}
    bad = base.run("family.place", {**tv, "host": a, "offset": 2610})
    assert not bad["ok"] and bad["error"]["details"]["unserved"] == ["end"] and bad["error"]["details"]["overflow"]["end"] == pytest.approx(59.5)
    i = gid("b", 1)
    r = base.run("family.place", {**tv, "id": i, "host": a, "offset": 2610, "also_cuts": [b]})
    assert r["ok"], r
    assert openings(base, b) == ()                                                   # 맞닿은 벽에는 빈 개구부가 생기지 않는다
    assert openings(base, a) == ((2160.0, 3000.0, 900.0, 2100.0),)
    # 허용 오차(0.5mm) 안의 틈도 맞닿음이다. 그보다 떨어지면 거절
    assert base.run("family.set", {"id": i, "offset": 2609.7})["ok"]
    bad = base.run("family.set", {"id": i, "offset": 2605})
    assert not bad["ok"] and bad["error"]["path"] == "/also_cuts/0"
    # 맞닿은 쪽이 넘친 쪽이 아니면 거절: 시작 쪽은 이어지는 벽이 받지만, 위층 벽은 창 위 끝(= 호스트 위 끝)에 맞닿기만 한다
    a2, c2 = walls(base, [[0, 12000, 3000, 12000], [-3000, 12000, 0, 12000]], height=3300, start=62)
    up = walls(base, [[-3000, 12000, 3000, 12000]], level=F.LV2, start=64)[0]
    win = {"family": "window.framed", "type_values": {"width": 900}, "values": {"sill": 2100}, "host": a2, "offset": 300}   # -150~750, 2100~3300
    bad = base.run("family.place", {**win, "also_cuts": [c2, up]})
    assert not bad["ok"] and bad["error"]["path"] == "/also_cuts/1" and bad["error"]["details"]["touch"] == "top"
    assert base.run("family.place", {**win, "also_cuts": [c2]})["ok"]


def test_맞닿은_also_cuts_는_IFC_에_빈_개구부를_만들지_않고_왕복한다(tmp_path):
    b_ = S.Bench(tmp_path)
    try:
        S.build_authored(b_)
        a, b = walls(b_, [[0, 9000, 3000, 9000], [3060, 9000, 6000, 9000]], start=60)
        i = gid("b", 2)
        assert b_.run("family.place", {"family": "window.framed", "type_values": {"width": 900}, "id": i, "host": a, "offset": 2610,
                                       "also_cuts": [b]})["ok"]
        h = b_.hashes()
        f = ifcopenshell.open(str(b_.export("t.ifc")))
        assert not [r for r in f.by_type("IfcRelVoidsElement") if r.RelatingBuildingElement.GlobalId == b]
        r = b_.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        hi = b_.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert params(b_, i, "imp.dobim")["also_cuts"] == [b]
    finally:
        b_.close()


# ---------------------------------------------------------------- 2. also_cuts 개구부를 벽 수량에서 빼기
def test_함께_자른_개구부는_두_벽의_면적_부피에서_나뉘어_빠진다(base):
    a, b = walls(base, [[0, 9000, 3000, 9000], [3000, 9000, 6000, 9000]], start=60)
    gross = 3000.0 * 3000.0
    assert net_area(base, a) == gross and net_area(base, b) == gross
    assert "xcut_area_m2" not in derived(base, a)                                    # also_cuts 가 없으면 응답은 그대로
    i = gid("b", 3)
    assert base.run("family.place", {**WF, "id": i, "host": a, "offset": 2800, "also_cuts": [b]})["ok"]   # 2200~3400, 900~2100
    assert net_area(base, a) == pytest.approx(gross - 800 * 1200)                    # 호스트: 자기 범위(~3000)만
    assert net_area(base, b) == pytest.approx(gross - 400 * 1200)                    # 이웃 벽: 넘친 400 만큼
    db = derived(base, b)
    assert db["xcut_openings"] == 1 and db["xcut_area_m2"] == pytest.approx(0.48)
    assert db["xcut_volume_m3"] == pytest.approx(0.48 * 0.2) and db["net_volume_m3"] == pytest.approx((gross - 480000) * 200 / 1e9)
    assert derived(base, a)["net_area_m2"] == pytest.approx((gross - 960000) / 1e6)
    # 쌓인 벽(위층): 위 벽은 창 위 부분만, 아래 벽은 자기 높이까지만
    lo, hi = walls(base, [[0, 8000, 6000, 8000]], start=70)[0], walls(base, [[0, 8000, 6000, 8000]], level=F.LV2, start=71)[0]
    j = gid("b", 4)
    assert base.run("family.place", {"family": "window.framed", "type_values": {"width": 1200, "height": 1500}, "values": {"sill": 2500},
                                     "id": j, "host": lo, "offset": 3000, "also_cuts": [hi]})["ok"]
    big = 6000.0 * 3000.0
    assert net_area(base, lo) == pytest.approx(big - 1200 * 500) and net_area(base, hi) == pytest.approx(big - 1200 * 700)
    # 떼면 이전 값(호스트는 창 전체 넓이)으로 돌아간다: also_cuts 없는 창은 예전처럼 폭 x 높이를 뺀다
    assert base.run("family.set", {"id": i, "offset": 2000, "also_cuts": []})["ok"]
    assert net_area(base, a) == pytest.approx(gross - 1200 * 1200) and net_area(base, b) == gross
    assert "xcut_area_m2" not in derived(base, b)


# ---------------------------------------------------------------- 3·4. window.framed 가로대·단x칸별 열림·옆 경첩 프로젝트
def test_framed_가로대와_단별_열림은_타입_값으로_풀리고_형상이_생긴다(base):
    # 아래 단 프로젝트(위 경첩) + 가로대 위 고정(한 칸)
    r = base.run("family.place", {"family": "window.framed", "type_values": {"width": 1500, "height": 1500}, "transoms": [600],
                                  "bays": [["project_out"], ["fixed"]], "host": F.W3, "offset": 3000})
    assert r["ok"], r
    tid = r["result"]["type"]
    tv = params(base, tid)["type_values"]
    assert tv["transom"] == 600 and tv["bay1"] == "project_out" and tv["bay1_2"] == "fixed"
    tps = props(base, tid)
    if tps:
        assert tps["DoBIM_WindowFrame"]["TransomCount"] == 1
    ids, _ = body_ids(base, tid)
    assert "tbar" in ids and "bay_sash1" in ids and "bay_glass1" in ids and "bay_glass1_2" in ids
    assert "glass" not in ids and "sash" not in ids and "bay_sash1_2" not in ids
    # 평평한 목록은 아래 단이다
    r = base.run("family.place", {"family": "window.framed", "type_values": {"width": 2700, "height": 1500, "mullions": 2},
                                  "bays": ["fixed", "fixed", "project_left"], "host": F.W1, "offset": 3000})
    assert r["ok"], r
    assert params(base, r["result"]["type"])["type_values"]["bay3"] == "project_left"
    # 단·칸이 창보다 많거나, 없는 칸, 여닫이 가로대, 함께 주기는 거절
    for args, path in (({"bays": [["fixed"], ["project_out"]]}, "/bays"),
                       ({"type_values": {"mullions": 2}, "bays": [["fixed", "fixed", "fixed", "fixed"]]}, "/bays/0/3"),
                       ({"type_values": {"bay1": "fixed"}, "bays": [["fixed"]]}, "/bays")):
        bad = base.run("family.place", {"family": "window.framed", "host": F.W2, "offset": 2000, **args})
        assert not bad["ok"] and bad["error"]["path"] == path, (args, bad)
    bad = base.run("family.place", {"family": "window.framed", "type_values": {"operation": "casement_in", "height": 1500}, "transoms": [600],
                                    "host": F.W2, "offset": 2000})
    assert not bad["ok"] and bad["error"]["code"] == "FAMILY_INVALID"
    bad = base.run("family.place", {"family": "window.framed", "type_values": {"height": 1500}, "transoms": [1300], "host": F.W2, "offset": 2000})
    assert not bad["ok"] and bad["error"]["path"] == "/transoms"                     # 위 끝에서 300 안: 가로대가 되지 않는다


def test_framed_단x칸_IFC_패널과_옆_경첩은_왕복한다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        i1, i2, i3 = gid("b", 11), gid("b", 12), gid("b", 13)
        r = b.run("family.place", {"id": i1, "family": "window.framed", "type_values": {"width": 2700, "height": 2100, "mullions": 2},
                                   "transoms": [1500], "bays": [["fixed", "fixed", "fixed"], ["project_left", "fixed", "project_right"]],
                                   "host": F.W3, "offset": 1700})
        assert r["ok"], r
        r = b.run("family.place", {"id": i2, "family": "window.framed", "type_values": {"width": 1550, "height": 1500}, "transoms": [600],
                                   "bays": [["project_out"], ["fixed"]], "host": F.W1, "offset": 4200})
        assert r["ok"], r
        r = b.run("family.place", {"id": i3, "family": "window.framed", "type_values": {"width": 900, "operation": "project_right"},
                                   "host": F.W2, "offset": 2000})
        assert r["ok"], r
        h = b.hashes()
        f = ifcopenshell.open(str(b.export("t.ifc")))
        t, pn = _panels(f, i1)
        assert t.PartitioningType == "USERDEFINED"
        assert pn == [("FIXEDCASEMENT", "LEFT"), ("FIXEDCASEMENT", "MIDDLE"), ("FIXEDCASEMENT", "RIGHT"),
                      ("SIDEHUNGLEFTHAND", "LEFT"), ("FIXEDCASEMENT", "MIDDLE"), ("SIDEHUNGRIGHTHAND", "RIGHT")]
        t, pn = _panels(f, i2)
        assert t.PartitioningType == "DOUBLE_PANEL_HORIZONTAL" and pn == [("TOPHUNG", "BOTTOM"), ("FIXEDCASEMENT", "TOP")]
        t, pn = _panels(f, i3)
        assert t.PartitioningType == "SINGLE_PANEL" and pn == [("SIDEHUNGRIGHTHAND", "MIDDLE")]
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


def test_옆_경첩_칸은_평면에_젖힘_선이_생기고_증분이_전체와_같다(base):
    v = base.run("view.create", {"name": "P", "type": "plan", "level": F.LV1})["result"]["id"]
    base.eng.sync()
    base.eng._touch_pending()
    before = base.eng.g.q(D.view_lines, v)
    r = base.run("family.place", {"family": "window.framed", "type_values": {"width": 2700, "height": 2100, "mullions": 2}, "transoms": [1500],
                                  "bays": [["fixed", "fixed", "fixed"], ["project_left", "fixed", "project_right"]], "host": F.W3, "offset": 1700})
    assert r["ok"], r
    ids, _ = body_ids(base, r["result"]["type"])
    assert "bay_sash1_2" in ids and "bay_sash3_2" in ids and "bay_sash2_2" not in ids and "bay_sash1" not in ids
    assert "tbar" in ids and sum(x.startswith("bay_glass") for x in ids) == 6
    base.eng.sync()
    base.eng._touch_pending()
    new = [s for s in base.eng.g.q(D.view_lines, v) if s not in before]
    tilted = [s for s in new if abs(s[1] - s[3]) > 1e-6 and abs(s[2] - s[4]) > 1e-6]
    assert len(tilted) == 2                                                          # 왼 경첩 칸·오른 경첩 칸 하나씩
    assert base.eng.g.q(D.view_lines, v) == s3lib.RefGraph(s3lib._Inputs(base.eng)).q(D.view_lines, v)


# ---------------------------------------------------------------- 5·6. window.storefront 칸별 열림·낮은 높이·세로대 폭 목록
def _sf_body(sess, tid, values=None):
    from dobim_core.family import evaluate as FE
    tp = params(sess, tid)
    rec = sess.eng.st.el[tid]["params"]["recipe"]
    r, plan = FE.load_recipe(rec)
    return FE.evaluate(r, plan, tp["type_values"], values or {}, None)


def test_storefront_는_낮은_높이와_칸별_열림을_받는다(base):
    # 높이 1500(예전 하한 2000 아래), 가로대 600, 아래 단 프로젝트 한 칸
    r = base.run("family.place", {"family": "window.storefront", "type_values": {"width": 1550, "height": 1500, "bay_max": 1550},
                                  "transoms": [600], "bays": [["project_out"], ["fixed"]], "values": {"sill": 900}, "host": F.W3, "offset": 3000})
    assert r["ok"], r
    tv = params(base, r["result"]["type"])["type_values"]
    assert tv["bay_row"] == 1 and tv["bay1"] == "project_out"
    ids = [b["id"] for b in _sf_body(base, r["result"]["type"])["body"]]
    assert "psash1[0]" in ids
    # 2단(가로대 900~2400)의 오른쪽 칸만 옆 경첩
    for w in (F.W1,):
        assert base.run("wall.set", {"id": w, "height": 3600})["ok"]
    r = base.run("family.place", {"family": "window.storefront", "type_values": {"width": 2700, "height": 3300, "bay_max": 900},
                                  "transoms": [900, 2400], "bays": [["fixed"], ["fixed", "fixed", "project_right"]], "host": F.W1, "offset": 3000})
    assert r["ok"], r
    tv = params(base, r["result"]["type"])["type_values"]
    assert tv["bay_row"] == 2 and tv["bay3"] == "project_right" and tv["bay1"] == "fixed"
    ev = _sf_body(base, r["result"]["type"])
    sash = [b for b in ev["body"] if b["id"].startswith("psash1")]
    assert [b["id"] for b in sash] == ["psash1[2]"] and sash[0]["profile"]["v"][0] == pytest.approx(900)
    assert ("SIDEHUNGRIGHTHAND", "MIDDLE") in [tuple(p) for p in ev["panels"]]
    # 문이 있는 낮은 창호, 두 단에 열리는 칸, 없는 칸은 거절
    bad = base.run("family.place", {"family": "window.storefront", "type_values": {"width": 2000, "height": 1800, "door_w": 900}, "host": F.W2, "offset": 2000})
    assert not bad["ok"] and bad["error"]["path"] == "/type_values/door_w"
    bad = base.run("family.place", {"family": "window.storefront", "type_values": {"width": 2000, "height": 2400}, "transoms": [1200],
                                    "bays": [["project_out"], ["project_out"]], "host": F.W2, "offset": 2000})
    assert not bad["ok"] and bad["error"]["path"] == "/bays"
    bad = base.run("family.place", {"family": "window.storefront", "type_values": {"width": 1200, "height": 2400, "bay_max": 1500},
                                    "bays": [["fixed", "project_out"]], "host": F.W2, "offset": 2000})
    assert not bad["ok"] and bad["error"]["path"] == "/bays" and bad["error"]["details"]["bays"] == 1


def test_storefront_세로대는_폭_목록으로_놓인다(base):
    tv = {"width": 2700, "height": 2400, "frame_w": 60}
    r = base.run("family.place", {"family": "window.storefront", "type_values": tv, "cols": [900, 900], "host": F.W3, "offset": 3000})
    assert r["ok"], r
    tid = r["result"]["type"]
    assert params(base, tid)["type_values"]["col2"] == 900
    ev = _sf_body(base, tid)
    mul = sorted(b["profile"]["u"] for b in ev["body"] if b["id"].startswith("mull1"))
    assert mul == [[-420.0, -360.0], [480.0, 540.0]]                                  # 틀 안쪽 -1290 에서 900·1800, 남은 780 이 셋째 칸
    assert ev["psets"]["instance"]["DoBIM_WindowParams"]["Columns"] == 3
    # 합이 딱 맞으면 남는 칸이 없다
    r = base.run("family.place", {"family": "window.storefront", "type_values": tv, "cols": [1290, 1290], "host": F.W1, "offset": 3000})
    assert r["ok"], r
    assert len([b for b in _sf_body(base, r["result"]["type"])["body"] if b["id"].startswith("mull1")]) == 1
    # 구간보다 길거나, 남은 칸이 너무 좁거나, 칸이 너무 좁으면 거절
    for cols in ([2000, 1000], [2500], [100, 1000]):
        bad = base.run("family.place", {"family": "window.storefront", "type_values": tv, "cols": cols, "host": F.W2, "offset": 2000})
        assert not bad["ok"] and bad["error"]["path"] == "/cols", (cols, bad)
    bad = base.run("family.place", {"family": "window.framed", "cols": [600], "host": F.W2, "offset": 2000})
    assert not bad["ok"] and bad["error"]["details"]["max"] == 0


# ---------------------------------------------------------------- 7. 평행하게 어긋난 다음 구간(단차)
SF = {"height": 2400, "bay_max": 1200}


def test_평행하게_어긋난_다음_구간은_단차로_사슬에_잇는다(base):
    a, b, c = walls(base, [[0, 10000, 3000, 10000], [3000, 10075, 6000, 10075], [6000, 10075, 6000, 13000]], start=80)
    r = base.run("family.place_corner", {"family": "window.storefront", "walls": [a, b, c], "legs": [2000, 3000, 1500], "type_values": SF})
    assert r["ok"], r
    assert r["result"]["joints"] == ["step", "corner"] and r["result"]["corners"][0] == [3000.0, 10000.0]
    pa, pb, pc = (params(base, i) for i in r["result"]["ids"])
    assert pa["offset"] == pytest.approx(2000) and pa["values"]["end_r"] == "jamb"   # 앞 다리 끝 = 단차 세로대(문틀)
    assert pb["offset"] == pytest.approx(1500) and pb["values"]["end_l"] == "corner" and pb["values"]["end_r"] == "corner"
    assert pc["values"]["end_l"] == "corner"
    assert base.run("history.undo", {})["ok"]
    # 너무 많이 어긋나거나(700) 끝이 떨어지면 거절
    d, e = walls(base, [[0, 20000, 3000, 20000], [3000, 20700, 6000, 20700]], start=90)
    bad = base.run("family.place_corner", {"family": "window.storefront", "walls": [d, e], "legs": [2000, 2000], "type_values": SF})
    assert not bad["ok"] and bad["error"]["details"]["offset"] == pytest.approx(700)
    g, h = walls(base, [[0, 30000, 3000, 30000], [3500, 30075, 6000, 30075]], start=95)
    bad = base.run("family.place_corner", {"family": "window.storefront", "walls": [g, h], "legs": [2000, 2000], "type_values": SF})
    assert not bad["ok"] and bad["error"]["details"]["gap"] == pytest.approx(500)
