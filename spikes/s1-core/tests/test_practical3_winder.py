# SPDX-License-Identifier: MPL-2.0
"""21단계(실무 모델링 3) 돌음(와인더) 계단: turn.winders 로 층참 대신 쐐기 디딤판. 배치(순수 함수)·검사·커맨드·수량,
IFC(IfcStairFlight WINDER)·가져오기 왕복, 평면·단면·입면, 난간, 증분 == 전체 재생성, 결정성. 합성 데이터만 쓴다(실제 도면 수치 없음)."""
import sys

import ifcopenshell
import pytest

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import kinds as K

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

# 합성 예: U턴 '직선 5단 + 돌음 3단 + 직선 3단' = 디딤판 11, 챌판 12. 디딤 270, 챌판 245(오름 2940)
RUN = [1000, 1000, 2350, 1000]   # +x 1350mm = 디딤판 5 x 270 (아래 단 묶음 챌판 6)
TURN_U = {"type": "U", "side": "left", "risers_a": 6, "winders": 3}
TURN_L = {"type": "L", "side": "right", "risers_a": 6, "winders": 3}
ARGS = {"id": F.ST1, "level": F.LV1, "run": RUN, "width": 1000, "height": 2940, "risers": 12}


def mk_stair(sess, turn=TURN_U, **kw):
    r = sess.run("stair.create", {**ARGS, "turn": turn, **kw})
    assert r["ok"], r
    return r["result"]["id"]


def mk(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


def fills_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_fills, vid)


def params(turn):
    t = {"landing": None, "gap": 100.0 if turn["type"] == "U" else 0.0, **turn}
    return {"name": "ST", "run": [float(v) for v in RUN], "width": 1000.0, "height": 2940.0, "risers": 12, "base_offset": 0.0, "turn": t}


def _area(poly):
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly))) / 2.0


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


# ---------------------------------------------------------------- 배치(순수 함수)
@pytest.mark.parametrize("turn", [TURN_U, TURN_L])
def test_돌음_배치는_단수와_높이를_나누고_쐐기가_회전부를_빈틈없이_채운다(turn):
    p = params(turn)
    lay = K.stair_layout(p)
    fa, fb = lay["flights"]
    wd = lay["winders"]
    rise = 2940 / 12
    assert lay["landing"] is None and len(wd["wedges"]) == 3
    assert fa["n"] == 6 and fb["n"] == 12 - 6 - 3 + 1 == 4                       # 디딤판: 5 + 3 + 3 = 11 = 챌판 12 - 1
    assert (fa["n"] - 1) + 3 + (fb["n"] - 1) == p["risers"] - 1
    assert [round(w["z"], 6) for w in wd["wedges"]] == [round(k * rise, 6) for k in (6, 7, 8)]
    assert abs(fb["z0"] - 8 * rise) < 1e-9 and abs(fb["z0"] + fb["n"] * rise - 2940) < 1e-9   # 위 단 묶음 끝 = 위층 바닥
    assert all(abs(w["zb"] - (6 * rise - K.STAIR_LANDING_T)) < 1e-9 for w in wd["wedges"])
    assert abs(fb["z0"] - fb["skirt"] - wd["wedges"][0]["zb"]) < 1e-9          # 위 단 묶음 밑 = 쐐기 밑
    assert abs(fb["td"] - 270.0) < 1e-9 and abs(fb["length"] - 3 * 270.0) < 1e-9
    assert abs(sum(_area(w["poly"]) for w in wd["wedges"]) - abs(_area(wd["region"]))) < 1e-6   # 쐐기 넓이 합 = 회전부 넓이
    assert all(_area(w["poly"]) > 0 for w in wd["wedges"])                     # 반시계


@pytest.mark.parametrize("turn", [TURN_U, TURN_L])
def test_보행선은_회전_중심_둘레_원호이고_같은_각_분할이_같은_디딤_길이다(turn):
    lay = K.stair_layout(params(turn))
    fa, fb = lay["flights"]
    wd = lay["winders"]
    a_end = K.winder_walk_point(wd, 0.0)
    b_start = K.winder_walk_point(wd, wd["sweep"])
    assert _dist(a_end, ((fa["run"][2], fa["run"][3]))) < 1e-9                  # 아래 단 묶음 걸음선 끝에서 시작
    assert _dist(b_start, (fb["run"][0], fb["run"][1])) < 1e-9                  # 위 단 묶음 걸음선 시작에서 끝
    pts = [K.winder_walk_point(wd, wd["sweep"] * j / 3) for j in range(4)]
    chords = [_dist(pts[j], pts[j + 1]) for j in range(3)]
    assert max(chords) - min(chords) < 1e-9                                    # 보행선 위 디딤 길이가 같다
    r = 500.0 if turn["type"] == "L" else 550.0
    assert abs(wd["radius"] - r) < 1e-12 and abs(wd["tread"] - r * 3.141592653589793 * wd["sweep"] / 180.0 / 3) < 1e-9


def test_winders_가_없으면_배치에_돌음_키가_없다():
    p = params({"type": "U", "side": "left", "risers_a": 6})
    lay = K.stair_layout(p)
    assert "winders" not in lay and lay["landing"] is not None and lay["flights"][1]["n"] == 6


# ---------------------------------------------------------------- 커맨드·검사·수량
def test_돌음_계단_생성은_winders를_저장하고_수량을_낸다(base):
    mk_stair(base)
    g = base.run("element.get", {"id": F.ST1})["result"]
    assert g["params"]["turn"] == {"type": "U", "side": "left", "risers_a": 6, "landing": None, "gap": 100.0, "winders": 3}
    d = g["derived"]
    assert d["flight_risers"] == [6, 4] and d["winder_treads"] == 3 and d["straight_treads"] == 8 and d["treads"] == 11
    assert abs(d["tread_depth"] - 270.0) < 1e-9 and abs(d["riser_height"] - 245.0) < 1e-9
    assert base.run("stair.set", {"id": F.ST1, "turn": {"type": "U", "risers_a": 6}})["ok"]          # winders 를 빼면 층참으로
    assert "winders" not in base.run("element.get", {"id": F.ST1})["result"]["params"]["turn"]
    assert "winder_treads" not in base.run("element.get", {"id": F.ST1})["result"]["derived"]


@pytest.mark.parametrize("turn", [
    {"type": "U", "risers_a": 6, "winders": 1},                      # 1 은 층참과 같다
    {"type": "U", "risers_a": 6, "winders": 13},
    {"type": "U", "risers_a": 9, "winders": 3},                      # 위 단 묶음 챌판 1
    {"type": "L", "risers_a": 6, "winders": 3, "landing": 1200},     # L 회전부는 폭 x 폭
    {"type": "U", "risers_a": 6, "winders": 3, "gap": 1200},         # 간격이 폭보다 크다
])
def test_잘못된_돌음은_거절한다(base, turn):
    r = base.run("stair.create", {**ARGS, "turn": turn})
    assert not r["ok"] and r["error"]["code"] in ("CONSTRAINT", "SCHEMA_INVALID"), r
    assert "stair" not in base.run("model.stats", {})["result"]["counts"]


def test_챌판을_줄여_돌음이_넘치면_거절하고_실행_취소로_돌아온다(base):
    h0 = base.eng.state_hash()
    mk_stair(base)
    assert not base.run("stair.set", {"id": F.ST1, "risers": 9})["ok"]           # 위 단 묶음 챌판 9 - 6 - 3 + 1 = 1
    assert base.run("stair.set", {"id": F.ST1, "risers": 10})["ok"]              # 위 단 묶음 챌판 2
    assert base.run("history.undo", {})["ok"] and base.run("history.undo", {})["ok"]
    assert base.eng.state_hash() == h0


# ---------------------------------------------------------------- IFC
@pytest.mark.parametrize("turn,ptype", [(TURN_L, "QUARTER_WINDING_STAIR"), (TURN_U, "HALF_WINDING_STAIR")])
def test_IFC는_IfcStair_아래_단_묶음_둘과_돌음_IfcStairFlight_WINDER를_집합한다(base, tmp_path, turn, ptype):
    mk_stair(base, turn=turn)
    out = tmp_path / "s.ifc"
    base.eng.flush_ifc()
    base.eng.ifc.write(str(out))
    f = ifcopenshell.open(str(out))
    st = f.by_guid(F.ST1)
    assert st.is_a("IfcStair") and st.PredefinedType == ptype and st.Representation is None
    kids = [k for r in st.IsDecomposedBy for k in r.RelatedObjects]
    assert sorted(k.is_a() for k in kids) == ["IfcStairFlight"] * 3                # 층참 IfcSlab 없음
    w = [k for k in kids if k.PredefinedType == "WINDER"]
    assert len(w) == 1 and w[0].NumberOfTreads == 3 and w[0].NumberOfRisers == 2
    assert len(w[0].Representation.Representations[0].Items) == 3                  # 쐐기마다 수직 압출 솔리드
    st_ = sorted((k for k in kids if k.PredefinedType == "STRAIGHT"), key=lambda k: k.Name)
    assert [k.NumberOfRisers for k in st_] == [6, 4]
    assert sum(k.NumberOfRisers for k in kids) == 12
    assert base.run("model.validate", {})["ok"]


def test_층참과_돌음을_오가면_부속이_바뀐다(base, tmp_path):
    mk_stair(base, turn={"type": "U", "risers_a": 6})
    assert base.run("stair.set", {"id": F.ST1, "turn": TURN_U})["ok"]
    base.eng.flush_ifc()
    base.eng.ifc.write(str(tmp_path / "s.ifc"))
    f = ifcopenshell.open(str(tmp_path / "s.ifc"))
    assert not [s for s in f.by_type("IfcSlab") if s.PredefinedType == "LANDING"]
    assert [k.PredefinedType for k in f.by_type("IfcStairFlight")].count("WINDER") == 1
    assert base.run("stair.set", {"id": F.ST1, "clear_turn": True})["ok"]
    base.eng.flush_ifc()
    base.eng.ifc.write(str(tmp_path / "t.ifc"))
    f = ifcopenshell.open(str(tmp_path / "t.ifc"))
    assert not f.by_type("IfcStairFlight") and f.by_guid(F.ST1).Representation is not None


@pytest.mark.parametrize("turn", [TURN_U, TURN_L])
def test_돌음_계단을_내보내고_가져오면_의도대로_되살아나고_해시_3종이_같다(tmp_path, turn):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("stair.create", {**ARGS, "turn": turn})["ok"]
        h = b.hashes()
        b.export("s.ifc")
        r = b.import_ifc("s.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert b.origins("imp.dobim") == {"authored": 8, "foreign": 0, "detached": 0}   # 부속(단 묶음·돌음)은 요소로 세지 않는다
        g = b.run("element.get", {"id": F.ST1}, project="imp.dobim")["result"]
        assert g["origin"] == "authored" and g["params"]["turn"]["winders"] == 3
        hi = b.hashes("imp.dobim")
        assert {k: v for k, v in hi.items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
        assert b.run("stair.set", {"id": F.ST1, "width": 900}, project="imp.dobim")["ok"]
        assert b.run("model.validate", {}, project="imp.dobim")["ok"]
    finally:
        b.close()


def test_외부에서_돌음_쐐기를_바꾸면_분리된다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        assert b.run("stair.create", {**ARGS, "turn": TURN_U})["ok"]
        b.export("s.ifc")
        f = ifcopenshell.open(str(tmp_path / "s.ifc"))
        w = [k for k in f.by_type("IfcStairFlight") if k.PredefinedType == "WINDER"][0]
        w.Representation.Representations[0].Items[1].Depth = 999.0
        f.write(str(tmp_path / "t.ifc"))
        assert b.import_ifc("t.ifc", "imp.dobim")["ok"]
        assert b.origins("imp.dobim")["detached"] == 1
    finally:
        b.close()


# ---------------------------------------------------------------- 도면
def _wedge_edges(lay):
    out = []
    for w in lay["winders"]["wedges"]:
        poly = w["poly"]
        out += [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]
    return out


def _covered(lines, cls, a, b):
    for x in lines:
        if x[0] != cls:
            continue
        p, q = (x[1], x[2]), (x[3], x[4])
        d = _dist(p, q)
        if d < 1e-9:
            continue
        ok = True
        for c in (a, b):
            cr = ((q[0] - p[0]) * (c[1] - p[1]) - (q[1] - p[1]) * (c[0] - p[0])) / d
            t = ((c[0] - p[0]) * (q[0] - p[0]) + (c[1] - p[1]) * (q[1] - p[1])) / (d * d)
            ok = ok and abs(cr) < 1e-3 and -1e-6 <= t <= 1 + 1e-6
        if ok:
            return True
    return False


def test_평면은_절단_높이에_따라_쐐기_윤곽과_보행선과_절단선을_그린다(base):
    mk_stair(base)
    lay = K.stair_layout(params(TURN_U))
    top = lines_of(base, mk(base, name="P", type="plan", level=F.LV1, cut_height=3500))     # 계단 위: 전부, 절단선 없음
    assert all(_covered(top, "proj", a, b) for a, b in _wedge_edges(lay))
    assert not [x for x in top if x[0] == "cut" and 2350 - 1 <= min(x[1], x[3]) and max(x[1], x[3]) <= 3350 + 1]
    assert [x for x in top if x[0] == "symbol" and min(x[1], x[3]) > 2350 + 1]               # 회전부 안 보행선 원호
    mid = lines_of(base, mk(base, name="P2", type="plan", level=F.LV1, cut_height=1700))     # 둘째 쐐기(1715) 아래: 첫 쐐기만
    w0, w1 = lay["winders"]["wedges"][:2]
    assert _covered(mid, "proj", w0["poly"][1], w0["poly"][2]) or _covered(mid, "proj", w0["poly"][0], w0["poly"][1])
    cuts = [x for x in mid if x[0] == "cut" and all(2350 - 1 < v < 3350 + 1 for v in (x[1], x[3])) and all(500 - 1 < v < 2600 + 1 for v in (x[2], x[4]))]
    assert len(cuts) == 1                                                                   # 회전부 안 절단선 하나
    ub = lay["flights"][1]["run"]
    assert not _covered(mid, "proj", (ub[0] - 500, ub[1] + 270), (ub[0] + 500, ub[1] + 270))  # 위 단 묶음은 그리지 않는다
    low = lines_of(base, mk(base, name="P3", type="plan", level=F.LV1, cut_height=1000))     # 아래 단 묶음 안에서 절단
    assert not [x for x in low if x[0] in ("proj", "symbol") and max(x[1], x[3]) > 2350 + 1 and all(500 - 1 < v < 2600 + 1 for v in (x[2], x[4]))]


def test_단면은_쐐기와_두_단_묶음을_절단한다(base):
    mk_stair(base)
    sec = mk(base, name="S", type="section", p1=[0, 1000], p2=[5000, 1000])                # 아래 단 묶음 걸음선 위: s = x
    f = fills_of(base, sec)
    rise = 245.0
    zb = 6 * rise - K.STAIR_LANDING_T
    wedge = [x for x in f if abs(min(p[1] for p in x) - zb) < 1e-6 and max(p[1] for p in x) >= 6 * rise - 1e-6]
    assert wedge and max(p[0] for x in wedge for p in x) > 3350 - 1e-6                     # 회전부 먼 끝(x = 2350 + 1000)까지
    treads = [x for x in f if len(x) == 4 and min(p[1] for p in x) == 0.0 and all(1000 - 1e-6 <= p[0] <= 2350 + 1e-6 for p in x)]
    assert len(treads) == 5


def test_입면에_쐐기_윗면_높이의_모서리가_나온다(base):
    mk_stair(base, run=[1000, -3000, 2350, -3000])
    ln = lines_of(base, mk(base, name="E", type="elevation", dir="S"))
    for k in (6, 7, 8):
        assert any(x[0] == "proj" and abs(x[2] - k * 245.0) < 1e-6 and abs(x[4] - k * 245.0) < 1e-6 for x in ln), k


def test_난간_자동_생성은_돌음_바깥_둘레에_오르는_난간을_둔다(base):
    mk_stair(base)
    r = base.run("railing.from_stair", {"stair": F.ST1})
    assert r["ok"], r
    ids = r["result"]["created"]
    rails = [base.run("element.get", {"id": i})["result"]["params"] for i in ids]
    w = [p for p in rails if p.get("src_tag") == "winder"]
    assert len(w) == 1 and abs(w[0]["base_offset"] - 6 * 245.0) < 1e-6 and abs(w[0]["rise"] - 3 * 245.0) < 1e-6
    assert len(w[0]["path"]) == 4
    assert base.run("stair.move", {"id": F.ST1, "dx": 100, "dy": 0})["ok"]                   # 난간이 따라간다
    p2 = base.run("element.get", {"id": [i for i, p in zip(ids, rails) if p.get("src_tag") == "winder"][0]})["result"]["params"]
    assert abs(p2["path"][0][0] - w[0]["path"][0][0] - 100) < 1e-6


def test_돌음_계단_증분이_전체_재생성과_같다(base):
    views = {"p": mk(base, name="P", type="plan", level=F.LV1, cut_height=1700), "p2": mk(base, name="P2", type="plan", level=F.LV1, cut_height=3500),
             "s": mk(base, name="S", type="section", p1=[0, 1000], p2=[5000, 1000]), "e": mk(base, name="E", type="elevation", dir="S"),
             "e2": mk(base, name="E2", type="elevation", dir="E")}
    before = {k: lines_of(base, v) for k, v in views.items()}

    def check():
        for k, v in views.items():
            assert lines_of(base, v) == s3lib.full_lines(base.eng, v), k
            assert fills_of(base, v) == base.eng.g.q(D.view_fills, v)

    mk_stair(base)
    check()
    assert base.run("stair.set", {"id": F.ST1, "turn": TURN_L})["ok"]
    check()
    assert base.run("stair.set", {"id": F.ST1, "turn": {"type": "U", "risers_a": 5, "winders": 4, "gap": 0}})["ok"]
    check()
    assert base.run("stair.move", {"id": F.ST1, "dx": 300, "dy": 200})["ok"]
    check()
    assert base.run("history.undo", {})["ok"]
    check()
    assert base.run("stair.delete", {"id": F.ST1})["ok"]
    for k, v in views.items():
        assert lines_of(base, v) == before[k], k
    check()


def test_돌음_계단_도면과_해시는_결정적이다(tmp_path):
    def build(name):
        s = s3lib.kit.Session(tmp_path / name, guid=s3lib.GUID)
        for n, a in F.SCRIPT:
            assert s.run(n, a)["ok"]
        mk_stair(s)
        vs = [mk(s, name="P", type="plan", level=F.LV1, cut_height=1700), mk(s, name="S", type="section", p1=[0, 1000], p2=[5000, 1000]),
              mk(s, name="E", type="elevation", dir="E")]
        out = ([lines_of(s, v) for v in vs], [fills_of(s, v) for v in vs], s.eng.hashes())
        s.close()
        return out
    a, b = build("a.dobim"), build("b.dobim")
    assert a == b
