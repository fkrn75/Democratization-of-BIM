# SPDX-License-Identifier: MPL-2.0
"""W2 스물한 번째 PR(구조 보 4부: 보 태그, 원호 통심선 일괄 배치, 반사 천장 평면의 보).

1. 보 태그(annot.tag.create·all): 유형 이름·단면 표기 같은 필드, 보 가운데(곡선 보는 호 가운데)·보 축 방향·거꾸로 서지 않는 글, 켜기·끄기, 이전 태그 그대로
2. 태그 출력: SVG·PDF·시트·GUI 장면, 증분 = 전체 재계산, 저장·IFC 왕복(태그 정의)
3. 원호 통심선 일괄 배치(beam.grid): 호를 따라 곡선 보, 방사선·직선 통심선과의 교점 분할, 반원 초과 호, 기둥 칸, 되돌리기
4. 반사 천장 평면의 보(밑면 윤곽 = 투영선, 절단면에 걸친 보 = 절단선·채움, 범위 밖은 그리지 않음), 일반 평면은 그대로
합성 데이터만 쓴다(실사용 재현 입력은 일반화한 좌표)."""
import re
import sys
from math import atan2, cos, degrees, pi, sin, sqrt, tan

import ifcopenshell
import ifcopenshell.validate
import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import annot as AN
from dobim_core import beams as BM
from dobim_core import drawing as D
from dobim_core import drawing_export as DX
from dobim_core import fixture as F
from dobim_core import queries as Q

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

V, VR = F.V1, F.VR1
BT, BS, BC = ("9" + "0" * 19 + x for x in ("c1", "c2", "c3"))                              # 보 유형
BA, BB, BCC, BD, BE, BF = ("9" + "0" * 19 + x for x in ("n1", "n2", "n3", "n4", "n5", "n6"))   # 보
C1, C2, C3 = ("0" * 20 + x for x in ("d1", "d2", "d3"))                                   # 기둥
G1, G2, G3, G4 = ("g" + "0" * 19 + x for x in ("a1", "a2", "a3", "a4"))                   # 통심선
IBT, IBM, IBN = ("3" + "0" * 19 + x for x in ("c7", "n7", "n8"))                          # IFC 왕복용(첫 글자가 유효한 GlobalId)
SEC = {"shape": "rect", "width": 400.0, "depth": 600.0}


def ok(r):
    assert r["ok"], r
    return r["result"]


def file_errors(f) -> list:
    lg = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, lg, express_rules=True)
    return [x["message"][:160] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]
            and "IfcSurfaceStyle" not in x["message"]]


def err(r):
    assert not r["ok"], r
    return r["error"]


def g_of(s):
    s.eng.sync()
    s.eng._touch_pending()
    return s.eng.g


def get(s, e):
    return ok(s.run("element.get", {"id": e}))


def column(s, cid, pos, w=500):
    ok(s.run("family.place", {"id": cid, "family": "column.section", "type_values": {"section": "rect", "width": w, "depth": w},
                              "values": {"height": 3300}, "level": F.LV1, "position": list(pos)}))


def tags(s, v=V):
    """뷰의 태그 그림 항목 {태그 id: 항목}."""
    return {it[1]: it for it in g_of(s).q(AN.view_annots, v) if it[0] == "tag"}


def text_of(s, t):
    st = g_of(s).q(AN.annot_state, t)
    assert st[:2] == ("tag", "ok"), st
    return st[2]


def destructive(s, name, args):
    r = s.run(name, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return s.run(name, args, confirm=r["error"]["details"]["confirm_token"])


@pytest.fixture
def bs(base):
    ok(base.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
    ok(base.run("view.rcp", {"id": VR, "name": "1F 반사 천장 평면", "level": F.LV1, "cut_height": 2000, "view_depth": 1500}))
    return base


def h_type(s, name="B1", preset="H-400x200x8x13"):
    ok(s.run("beamtype.create", {"id": BT, "name": name, "preset": preset}))


def beam(s, bid, start, end, level=F.LV1, offset=2400.0, **kw):
    return ok(s.run("beam.create", {"id": bid, "level": level, "type": kw.pop("type", BT), "start": list(start), "end": list(end), "offset": offset, **kw}))


# ---------------------------------------------------------------- 1. 보 태그
def test_1_보_태그_표기_필드(bs):
    h_type(bs)
    beam(bs, BA, (0, -1000), (6000, -1000))
    t = ok(bs.run("annot.tag.create", {"view": V, "target": BA, "fields": ["type", "size"]}))["id"]
    assert text_of(bs, t) == "B1 H-400x200"                                                    # 유형 이름 + 약식 단면
    one = {f: ok(bs.run("annot.tag.create", {"view": V, "target": BA, "fields": [f]}))["id"] for f in
           ("name", "type", "section", "size", "width", "depth", "length", "offset", "level", "material", "ifc_class")}
    got = {f: text_of(bs, i) for f, i in one.items()}
    assert got["section"] == "H-400x200x8x13" and got["width"] == "200" and got["depth"] == "400"
    assert got["length"] == "6000" and got["offset"] == "2400" and got["level"] == "1F" and got["ifc_class"] == "IfcBeam"
    assert got["name"] == get(bs, BA)["params"]["name"] and got["material"] == "-"
    r = err(bs.run("annot.tag.create", {"view": V, "target": BA, "fields": ["thickness"]}))
    assert r["code"] == "CONSTRAINT" and "size" in r["hint"]
    # 사각 단면·유형을 뗀 보·재료
    ok(bs.run("beamtype.create", {"id": BS, "name": "G1", "width": 300, "depth": 600}))
    beam(bs, BB, (0, -3000), (4000, -3000), type=BS)
    t2 = ok(bs.run("annot.tag.create", {"view": V, "target": BB, "fields": ["type", "size", "section"]}))["id"]
    assert text_of(bs, t2) == "G1 R-300x600 R-300x600"
    ok(bs.run("beam.type.set", {"ids": [BB], "type": None}))
    assert text_of(bs, t2) == "- R-300x600 R-300x600"                                           # 태그는 유형이 떨어져도 값을 따라간다
    ok(bs.run("material.set", {"id": BB, "name": "Steel"}))
    t3 = ok(bs.run("annot.tag.create", {"view": V, "target": BB, "fields": ["material"]}))["id"]
    assert text_of(bs, t3) == "Steel"
    # 유형 이름·단면을 고치면 태그가 따라간다(파생값)
    ok(bs.run("beamtype.set", {"id": BT, "name": "B2", "preset": "H-300x150x6.5x9"}))
    assert text_of(bs, t) == "B2 H-300x150"
    ok(bs.run("history.undo", {}))
    assert text_of(bs, t) == "B1 H-400x200"


def test_1_보_태그_자리와_방향(bs):
    h_type(bs)
    # 보 가운데에 얹고 보 진행 방향으로 눕는다
    beam(bs, BA, (0, -1000), (6000, -1000))
    t = ok(bs.run("annot.tag.create", {"view": V, "target": BA, "fields": ["type", "size"]}))["id"]
    p = get(bs, t)["params"]
    assert p["align"] == "axis" and p["off"] == [0.0, 0.0] and p["home"] == [3000.0, -1000.0]    # 보는 축 방향 태그가 기본, 간격 없음
    it = tags(bs)[t]
    assert it == ("tag", t, (3000.0, -1000.0), (3000.0, -1000.0), "B1 H-400x200", (1.0, 0.0))
    assert DX.adim_text_angle(it[5]) == 0.0
    # 거꾸로 만든 보(끝 -> 시작): 방향 벡터는 보 방향이고 글은 거꾸로 서지 않는다
    beam(bs, BB, (6000, -2000), (0, -2000))
    t2 = ok(bs.run("annot.tag.create", {"view": V, "target": BB, "fields": ["type"]}))["id"]
    assert tags(bs)[t2][5] == (-1.0, 0.0) and DX.adim_text_angle(tags(bs)[t2][5]) == 0.0
    # 모든 방향에서 글 회전각이 (-90, 90] 안
    for k in range(24):
        a = k * pi / 12.0
        ang = DX.adim_text_angle((cos(a), sin(a)))
        assert -90.0 < ang <= 90.0, (k, ang)
    # 사선 보와 세로 보
    beam(bs, BCC, (500, 0), (500, 3000))
    beam(bs, BD, (1000, 1000), (1000 + 4000 * cos(pi / 6), 1000 + 4000 * sin(pi / 6)))
    t3, t4 = (ok(bs.run("annot.tag.create", {"view": V, "target": b, "fields": ["name"]}))["id"] for b in (BCC, BD))
    assert tags(bs)[t3][2] == (500.0, 1500.0) and tags(bs)[t3][5] == (0.0, 1.0) and DX.adim_text_angle(tags(bs)[t3][5]) == 90.0
    u = tags(bs)[t4][5]
    assert u == (pytest.approx(cos(pi / 6), abs=1e-6), pytest.approx(0.5, abs=1e-6)) and DX.adim_text_angle(u) == pytest.approx(-30.0, abs=1e-4)


def test_1_보_태그_자른_보와_곡선_보_경사_보(bs):
    h_type(bs)
    column(bs, C1, (0, 0))
    beam(bs, BA, (0, 0), (6000, 0))                                                            # 시작이 기둥 안: 250 줄어든다
    assert g_of(bs).q(Q.beam_cut, BA)[:2] == (250.0, 0.0)
    t = ok(bs.run("annot.tag.create", {"view": V, "target": BA, "fields": ["length"]}))["id"]
    assert tags(bs)[t][2] == (3125.0, 0.0) and text_of(bs, t) == "5750"                         # 자른 뒤 축의 가운데
    ok(bs.run("family.set", {"id": C1, "position": [-500, 0]}))
    assert g_of(bs).q(Q.beam_cut, BA)[0] == 0.0 and tags(bs)[t][2] == (3000.0, 0.0)             # 기둥을 옮기면 태그 자리가 따라간다
    # 곡선 보: 호 가운데
    beam(bs, BB, (0, -5000), (10000, -5000), bulge=0.5)                                         # 반지름 6250, 아래로 부푼다(진행 방향 오른쪽)
    t2 = ok(bs.run("annot.tag.create", {"view": V, "target": BB, "fields": ["size"]}))["id"]
    it = tags(bs)[t2]
    assert it[2] == (pytest.approx(5000.0, abs=1e-2), pytest.approx(-7500.0, abs=1e-2)) and it[5] == (pytest.approx(1.0, abs=1e-6), pytest.approx(0.0, abs=1e-6))
    ln = BM.arc_length(get(bs, BB)["params"])
    t3 = ok(bs.run("annot.tag.create", {"view": V, "target": BB, "fields": ["length"]}))["id"]
    assert text_of(bs, t3) == AN.fmt_num(ln)                                                    # 곡선 보 길이 = 호 길이
    # 경사 보: 평면에 투영한 축 가운데
    beam(bs, BCC, (0, -9000), (4000, -9000), offset=2000, end_offset=3000)
    t4 = ok(bs.run("annot.tag.create", {"view": V, "target": BCC, "fields": ["length"]}))["id"]
    assert tags(bs)[t4][2] == (2000.0, -9000.0) and text_of(bs, t4) == AN.fmt_num(sqrt(4000.0 ** 2 + 1000.0 ** 2))


def test_1_보_태그_켜기_끄기와_이전_태그_그대로(bs):
    h_type(bs)
    beam(bs, BA, (0, -1000), (6000, -1000))
    beam(bs, BB, (0, -3000), (4000, -3000))
    # 켜기: 일괄 태그, 다시 켜도 겹치지 않는다
    r = ok(bs.run("annot.tag.all", {"view": V, "kinds": ["beam"], "fields": ["type", "size"]}))
    assert sorted(t["target"] for t in r["tags"]) == sorted([BA, BB]) and len(r["created"]) == 2
    assert all(get(bs, t)["params"]["align"] == "axis" for t in r["created"])
    assert ok(bs.run("annot.tag.all", {"view": V, "kinds": ["beam"], "fields": ["type"]}))["created"] == []
    # 끄기: 태그를 지우거나 뷰에서 tag 범주를 숨긴다
    ok(bs.run("annot.delete", {"id": r["created"][0]}))
    assert len(tags(bs)) == 1
    # 표기 형식: 보 필드가 아닌 것은 거절, 여러 범주에 쓰는 필드는 허용
    assert err(bs.run("annot.tag.all", {"view": V, "kinds": ["wall", "beam"], "fields": ["size"]}))["code"] == "CONSTRAINT"
    mixed = ok(bs.run("annot.tag.all", {"view": V, "kinds": ["wall", "beam"], "fields": ["name", "level"]}))
    assert any(x["kind"] == "wall" for x in mixed["tags"]) and any(x["kind"] == "beam" for x in mixed["tags"])
    # 지시선 모양(leader): 이전 태그와 같다(align 키 없음, 간격 [400, 400], 항목 5칸)
    ok(bs.run("annot.tag.create", {"id": "7" + "0" * 20 + "1", "view": V, "target": BB, "fields": ["name"], "align": "leader"}))
    p = get(bs, "7" + "0" * 20 + "1")["params"]
    assert "align" not in p and p["off"] == [400.0, 400.0] and p["home"] == [2400.0, -2600.0]
    assert len(tags(bs)["7" + "0" * 20 + "1"]) == 5
    # 벽 태그는 이전과 같다: align 키·여섯째 값이 없다
    w = ok(bs.run("annot.tag.create", {"view": V, "target": F.W1, "fields": ["name"]}))["id"]
    pw = get(bs, w)["params"]
    assert set(pw) == {"name", "view", "target", "fields", "off", "home"} and pw["off"] == [400.0, 400.0] and len(tags(bs)[w]) == 5
    # 축 방향 태그는 보에만, 대상이 다른 레벨이면 거절
    assert err(bs.run("annot.tag.create", {"view": V, "target": F.W1, "fields": ["name"], "align": "axis"}))["code"] == "CONSTRAINT"
    assert err(bs.run("annot.tag.all", {"view": V, "kinds": ["wall"], "fields": ["name"], "align": "axis"}))["code"] == "CONSTRAINT"
    ok(bs.run("beamtype.create", {"id": BC, "name": "G9", "width": 300, "depth": 600}))
    beam(bs, BD, (0, 0), (3000, 0), level=F.LV2, type=BC)
    assert err(bs.run("annot.tag.create", {"view": V, "target": BD, "fields": ["name"]}))["code"] == "CONSTRAINT"
    # 뷰 가시성(범주 tag 숨기기)으로도 끈다: 내보낸 도면에서 태그가 빠지고 다시 켜면 돌아온다
    def n_tags():
        assert destructive(bs, "view.export", {"id": V, "path": "v.svg", "overwrite": True})["ok"]
        return (bs.path.parent / "v.svg").read_text(encoding="utf-8").count('<g class="tag"')

    n0 = n_tags()
    assert n0 >= 3
    ok(bs.run("view.vg.set", {"id": V, "category": "tag", "hidden": True}))
    assert n_tags() == 0
    ok(bs.run("view.vg.set", {"id": V, "category": "tag", "hidden": False}))
    assert n_tags() == n0


def test_1_보_태그_옮기기_지우기_고아_되돌리기(bs):
    h_type(bs)
    beam(bs, BA, (0, -1000), (6000, -1000))
    t = ok(bs.run("annot.tag.create", {"view": V, "target": BA, "fields": ["type", "size"]}))["id"]
    ok(bs.run("beam.move", {"id": BA, "dx": 1000, "dy": 500}))
    assert tags(bs)[t][2] == (4000.0, -500.0) and tags(bs)[t][3] == (4000.0, -500.0)            # 보를 옮기면 태그가 따라간다
    ok(bs.run("beam.set", {"id": BA, "end": [8000, -500]}))
    assert tags(bs)[t][2] == (4500.0, -500.0)
    ok(bs.run("element.delete", {"id": BA}))                                                   # 지우면 고아(조용히 지우지 않는다)
    assert g_of(bs).q(AN.annot_state, t)[:3] == ("tag", "orphan", (("target", "target_deleted"),))
    assert tags(bs) == {} and [x[0] for x in g_of(bs).q(AN.view_annots, V)] == ["orphan"]
    ok(bs.run("history.undo", {}))
    assert text_of(bs, t) == "B1 H-400x200"
    # 다시 묶기: 다른 보로
    beam(bs, BB, (0, -3000), (2000, -3000))
    ok(bs.run("annot.rebind", {"id": t, "target": BB}))
    assert tags(bs)[t][2] == (1000.0, -3000.0)
    wall = err(bs.run("annot.rebind", {"id": t, "target": F.W1}))                              # 축 방향 태그를 벽으로 옮길 수 없다
    assert wall["code"] == "CONSTRAINT"


def test_2_증분_전체_계산이_같다(bs):
    h_type(bs)
    column(bs, C1, (0, 0))
    beam(bs, BA, (0, 0), (6000, 0))
    beam(bs, BB, (0, -5000), (10000, -5000), bulge=-0.4)
    for b in (BA, BB):
        ok(bs.run("annot.tag.create", {"view": V, "target": b, "fields": ["type", "size", "length"]}))
    ok(bs.run("annot.tag.create", {"view": VR, "target": BA, "fields": ["name"]}))

    def same():
        g = g_of(bs)
        full = s3lib.RefGraph(s3lib._Inputs(bs.eng))
        assert g.q(AN.annot_bundle) == full.q(AN.annot_bundle)
        for v in (V, VR):
            assert g.q(AN.view_annots, v) == full.q(AN.view_annots, v)

    same()
    ok(bs.run("family.set", {"id": C1, "position": [-700, 0]}))
    same()
    ok(bs.run("beam.set", {"id": BB, "bulge": 0.3}))
    same()
    ok(bs.run("beamtype.set", {"id": BT, "preset": "H-300x150x6.5x9"}))
    same()
    ok(bs.run("element.delete", {"id": BA}))
    same()
    ok(bs.run("history.undo", {}))
    same()


# ---------------------------------------------------------------- 2. 태그 출력·왕복
def test_2_SVG_PDF_DXF_시트_GUI_장면(bs):
    h_type(bs)
    beam(bs, BA, (0, -1000), (6000, -1000))
    beam(bs, BB, (500, 0), (500, 3000))
    beam(bs, BCC, (0, -5000), (10000, -5000), bulge=0.5)
    wt = ok(bs.run("annot.tag.create", {"view": V, "target": F.W1, "fields": ["name"]}))["id"]

    def exp(name):
        assert destructive(bs, "view.export", {"id": V, "path": name, "overwrite": True})["ok"]
        return (bs.path.parent / name).read_bytes()

    base_svg = exp("a.svg").decode("utf-8")
    t1, t2, t3 = (ok(bs.run("annot.tag.create", {"view": V, "target": b, "fields": ["type", "size"]}))["id"] for b in (BA, BB, BCC))
    svg = exp("b.svg").decode("utf-8")
    assert f'<g class="tag" data-id="{wt}"' in base_svg and f'<line' in base_svg                   # 이전 태그: 지시선 + 가로 글
    for t, ang in ((t1, "0.00"), (t2, "90.00"), (t3, "0.00")):
        i = svg.index(f'<g class="tag" data-id="{t}"')
        blk = svg[i:svg.index("</g>", i)]
        assert f"rotate({ang})" in blk and 'text-anchor="middle"' in blk and "B1 H-400x200" in blk
        assert "<line" not in blk and "<circle" not in blk                                        # 지시선·점이 없다
    # 벽 태그 모양은 이전과 같다(용지 범위가 달라져 좌표 수치만 다르다): 지시선·점·가로 글
    def shape(doc, t):
        i = doc.index(f'<g class="tag" data-id="{t}"')
        return re.sub(r"-?\d+\.\d+", "N", doc[i:doc.index("</g>", i)])

    assert shape(svg, wt) == shape(base_svg, wt) and "<circle" in shape(svg, wt) and "<text x=" in shape(svg, wt)
    pdf = exp("b.pdf")
    assert pdf.startswith(b"%PDF") and b" Tm " in pdf
    dxf = exp("b.dxf").decode("utf-8")
    assert "H-400x200" in dxf
    ok(bs.run("sheet.define", {"id": F.SH1, "name": "A-101", "views": [V], "paper": "A3", "project_name": "예시", "sheet_number": "A-101", "date": "2026-01-01"}))
    out = destructive(bs, "sheet.render", {"id": F.SH1, "path": "s.svg", "overwrite": True})
    assert out["ok"] and "H-400x200" in (bs.path.parent / "s.svg").read_text(encoding="utf-8")
    # GUI 장면: 축 방향 태그는 지시선이 없고 글 자리 한 점이다(집기·경계 상자)
    sc = {(x[0], x[1]): x for x in g_of(bs).q(__import__("dobim_core.scene", fromlist=["x"]).view_scene, V)}
    assert sc[("tag", t1)][5][0][5] == (1.0, 0.0) and sc[("tag", t1)][6] is not None
    assert sc[("tag", wt)][5][0][:2] == ("tag", wt)
    # 되돌리면 이전 출력과 바이트 같다
    for _ in range(4):                                                                           # 시트 정의 + 태그 셋
        ok(bs.run("history.undo", {}))
    assert exp("c.svg").decode("utf-8") == base_svg


def test_2_저장하고_다시_열기와_IFC_왕복(tmp_path):
    s = kit.Session(tmp_path / "b.dobim")
    try:
        for name, args in F.SCRIPT:
            ok(s.run(name, args))
        ok(s.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
        ok(s.run("view.rcp", {"id": VR, "name": "1F 반사 천장 평면", "level": F.LV1, "cut_height": 2000, "view_depth": 1500}))
        h_type(s)
        beam(s, BA, (0, -1000), (6000, -1000))
        beam(s, BB, (0, -5000), (10000, -5000), bulge=0.5)
        ta = ok(s.run("annot.tag.create", {"view": V, "target": BA, "fields": ["type", "size"]}))["id"]
        tb = ok(s.run("annot.tag.create", {"view": VR, "target": BB, "fields": ["name", "length"], "offset": [100, 200]}))["id"]
        key = lambda s_: (get(s_, ta)["params"], get(s_, tb)["params"], g_of(s_).q(AN.view_annots, V), g_of(s_).q(AN.view_annots, VR))
        before = key(s)
        assert before[0]["align"] == "axis" and before[1]["off"] == [100.0, 200.0]
    finally:
        s.close()
    s = kit.Session(tmp_path / "b.dobim", create=False)
    try:
        assert key(s) == before
    finally:
        s.close()


def test_2_IFC_왕복에서_태그_정의가_살아난다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("view.create", {"id": V, "name": "1F", "type": "plan", "level": F.LV1, "cut_height": 1200}))
        ok(b.run("beamtype.create", {"id": IBT, "name": "B1", "preset": "H-400x200x8x13"}))
        ok(b.run("beam.create", {"id": IBM, "level": F.LV1, "type": IBT, "start": [0, -3000], "end": [6000, -3000], "offset": 2400}))
        ok(b.run("beam.create", {"id": IBN, "level": F.LV1, "type": IBT, "start": [0, -9000], "end": [6000, -9000], "offset": 2400, "bulge": 0.4}))
        t1 = ok(b.run("annot.tag.create", {"view": V, "target": IBM, "fields": ["type", "size"]}))["id"]
        t2 = ok(b.run("annot.tag.create", {"view": V, "target": IBN, "fields": ["section"], "align": "leader"}))["id"]
        h = b.hashes()
        out = b.export("b.ifc")
        assert file_errors(ifcopenshell.open(str(out))) == []                                    # IFC4 검증 오류 0(태그 정의는 DoBIM_Defs 속성)
        assert b.destructive("ifc.export", {"path": "b3.ifc", "schema": "IFC2X3"})["ok"]
        assert file_errors(ifcopenshell.open(str(b.ws / "b3.ifc"))) == []                        # IFC2X3 검증 오류 0
        r = b.import_ifc("b.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)                                        # 태그 정의(align 포함)까지 같다
        assert b.export("b2.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        p1 = ok(b.run("element.get", {"id": t1}, project="imp.dobim"))["params"]
        p2 = ok(b.run("element.get", {"id": t2}, project="imp.dobim"))["params"]
        assert p1["align"] == "axis" and "align" not in p2
    finally:
        b.close()


# ---------------------------------------------------------------- 3. 원호 통심선 일괄 배치
def arc_grid(s, name, r, a0, a1, center=(0, 0)):
    return ok(s.run("grid.create_arc", {"name": name, "center": list(center), "radius": r, "start_angle": a0, "end_angle": a1}))["id"]


def radials(s, angles, r0=2000, r1=8000):
    return ok(s.run("grid.radial", {"center": [0, 0], "angles": list(angles), "r0": r0, "r1": r1, "names": [f"R{i + 1}" for i in range(len(angles))]}))["ids"]


def beam_rows(s):
    return {b: get(s, b)["params"] for b in sorted(g_of(s).q(Q.beam_ids))}


def ang_of(pt):
    return degrees(atan2(pt[1], pt[0]))


def same_as_full(s, views=(V, VR)):
    g = g_of(s)
    full = s3lib.RefGraph(s3lib._Inputs(s.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)
        assert g.q(D.view_fills, v) == full.q(D.view_fills, v)


def test_3_원호_통심선_일괄_배치(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    arc_grid(bs, "A", 6000, 0, 90)
    arc_grid(bs, "B", 4000, 0, 90)
    radials(bs, (0, 30, 60, 90))
    r = ok(bs.run("beam.grid", {"level": F.LV2, "type": BT, "between": "intersections"}))
    rows = beam_rows(bs)
    assert r["count"] == 10 and len(rows) == 10
    curved = [p for p in rows.values() if "bulge" in p]
    straight = [p for p in rows.values() if "bulge" not in p]
    assert len(curved) == 6 and len(straight) == 4
    bu = tan(pi / 24.0)                                                                       # 30도 칸: tan(중심각 / 4)
    for p in curved:                                                                          # 호를 따라 놓인 곡선 보: 같은 원(중심 원점)·같은 볼록도(반시계 +)
        ai = BM.arc_info(p)
        assert p["bulge"] == pytest.approx(bu, abs=1e-8) and ai[4] == 1.0 and (ai[0], ai[1]) == (pytest.approx(0.0, abs=1e-3), pytest.approx(0.0, abs=1e-3))
        assert ai[2] == pytest.approx(6000.0, abs=1e-3) or ai[2] == pytest.approx(4000.0, abs=1e-3)
        assert ai[5] == pytest.approx(pi / 6.0, abs=1e-8)
        assert sqrt(p["start"][0] ** 2 + p["start"][1] ** 2) == pytest.approx(ai[2], abs=1e-3)
    assert sorted(round(ang_of(p["start"])) for p in curved if BM.arc_info(p)[2] > 5000) == [0, 30, 60]
    assert sorted(round(ang_of(p["end"])) for p in curved if BM.arc_info(p)[2] > 5000) == [30, 60, 90]
    for p in straight:                                                                        # 방사선: 안쪽 호 ~ 바깥 호 사이 한 칸
        assert sorted([round(sqrt(p[k][0] ** 2 + p[k][1] ** 2), 3) for k in ("start", "end")]) == [4000.0, 6000.0]
    # 곡선 보 수량 = 호 길이(원 위에 놓였다)
    cb = next(b for b, p in rows.items() if "bulge" in p and BM.arc_info(p)[2] > 5000)
    item = next(x for x in ok(bs.run("beam.list", {}))["items"] if x["id"] == cb)
    assert item["length"] == pytest.approx(6000.0 * pi / 6.0, rel=1e-6) and item["sweep_deg"] == pytest.approx(30.0, abs=1e-3)
    # 다시 실행해도 겹치지 않는다(곡선 보 포함). 아무것도 만들지 않은 실행은 이력이 없어 되돌리면 처음 일괄 배치가 한 번에 사라진다
    assert ok(bs.run("beam.grid", {"level": F.LV2, "type": BT, "between": "intersections"}))["count"] == 0
    same_as_full(bs)
    ok(bs.run("history.undo", {}))
    assert beam_rows(bs) == {}
    # 같은 호 칸에 반대 방향으로 놓은 보가 이미 있으면 건너뛴다(끝 순서를 바꾸면 볼록도 부호가 바뀐다)
    ok(bs.run("beam.create", {"level": F.LV2, "type": BT, "start": rows[cb]["end"], "end": rows[cb]["start"], "bulge": -rows[cb]["bulge"]}))
    r = ok(bs.run("beam.grid", {"level": F.LV2, "type": BT, "between": "intersections"}))
    assert r["count"] == 9 and len(beam_rows(bs)) == 10
    same_as_full(bs)


def test_3_직선과_호가_섞인_격자와_이전_동작(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    arc_grid(bs, "A", 6000, 0, 90)
    ok(bs.run("grid.create", {"name": "X", "line": [3000, -1000, 3000, 8000]}))
    ok(bs.run("grid.create", {"name": "Y", "line": [-1000, 3000, 8000, 3000]}))
    # include_arcs false: 이전과 같다(원호 통심선은 건너뛰고 경고, X·Y 는 한 점에서 만날 뿐이라 놓을 칸이 없다)
    r = bs.run("beam.grid", {"level": F.LV2, "type": BT, "between": "intersections", "include_arcs": False})
    assert r["ok"] and r["result"]["count"] == 0 and any("원호" in w for w in r["warnings"])
    # 기본: 호 위 교점 둘(X·Y 와 만나는 곳)이 곡선 보 한 칸, X·Y 도 호와의 교점에서 나뉘어 한 칸씩
    on = ok(bs.run("beam.grid", {"level": F.LV2, "type": BT, "between": "intersections"}))
    rows = beam_rows(bs)
    assert on["count"] == 3 and len(rows) == 3
    cv = [p for p in rows.values() if "bulge" in p]
    assert len(cv) == 1 and round(ang_of(cv[0]["start"])) == 30 and round(ang_of(cv[0]["end"])) == 60
    ai = BM.arc_info(cv[0])
    assert ai[2] == pytest.approx(6000.0, abs=1e-3) and ai[5] == pytest.approx(pi / 6.0, abs=1e-8)
    st = sorted((tuple(p["start"]), tuple(p["end"])) for p in rows.values() if "bulge" not in p)
    x_beam = next(sorted([a, b]) for a, b in st if a[0] == b[0] == 3000.0)
    y_beam = next(sorted([a, b]) for a, b in st if a[1] == b[1] == 3000.0)
    assert x_beam == [(3000.0, 3000.0), (3000.0, pytest.approx(6000.0 * sin(pi / 3.0), abs=1e-3))]
    assert y_beam == [(3000.0, 3000.0), (pytest.approx(6000.0 * cos(pi / 6.0), abs=1e-3), 3000.0)]


def test_3_시계_호와_반원_초과_호(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    arc_grid(bs, "A", 5000, 90, 0)                                                            # 시계 방향 호
    radials(bs, (0, 45, 90), 2000, 7000)
    r = ok(bs.run("beam.grid", {"level": F.LV2, "type": BT, "between": "intersections"}))
    rows = beam_rows(bs)
    cv = [p for p in rows.values() if "bulge" in p]
    assert r["count"] == 2 and len(cv) == 2
    for p in cv:
        ai = BM.arc_info(p)
        assert ai[4] == -1.0 and p["bulge"] == pytest.approx(-tan(pi / 16.0), abs=1e-8) and ai[2] == pytest.approx(5000.0, abs=1e-3)   # 진행 방향 = 통심선 방향(90도 -> 0도)
        assert (ai[0], ai[1]) == (pytest.approx(0.0, abs=1e-3), pytest.approx(0.0, abs=1e-3))
    assert sorted(round(ang_of(p["start"])) for p in cv) == [45, 90]


def test_3_반원을_넘는_호_칸은_건너뛰고_알린다(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    big = arc_grid(bs, "C", 3000, 0, 270, center=(20000, 0))                                  # 중심각 270도
    ok(bs.run("grid.create", {"name": "H0", "line": [21000, 0, 30000, 0]}))                    # 0도 교점 (23000, 0)
    ok(bs.run("grid.create", {"name": "H1", "line": [20000, -3000, 20000, -9000]}))            # 270도 교점 (20000, -3000)
    r = bs.run("beam.grid", {"level": F.LV2, "type": BT, "between": "intersections", "grids": [big]})
    assert r["ok"] and r["result"]["count"] == 0 and any("180도" in w for w in r["warnings"])    # 한 보(bulge <= 1, 반원까지)로 놓을 수 없다
    ok(bs.run("grid.create", {"name": "H2", "line": [17000, 0, 14000, 0]}))                    # 180도 교점 (17000, 0)
    r = ok(bs.run("beam.grid", {"level": F.LV2, "type": BT, "between": "intersections", "grids": [big]}))
    rows = beam_rows(bs)
    assert r["count"] == 2 and sorted(round(BM.arc_info(p)[5] * 180.0 / pi) for p in rows.values()) == [90, 180]                      # 0~180 반원(bulge 1) + 180~270
    assert sorted(p["bulge"] for p in rows.values())[1] == pytest.approx(1.0, abs=1e-8)


def test_3_원호_통심선_기둥_칸(bs):
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    arc_grid(bs, "A", 6000, 0, 90)
    radials(bs, (0, 30, 60, 90))
    for cid, ang in ((C1, 0), (C2, 30)):
        column(bs, cid, (6000 * cos(ang * pi / 180.0), 6000 * sin(ang * pi / 180.0)))
    r = ok(bs.run("beam.grid", {"level": F.LV2, "type": BT}))                                 # 기본 columns: 두 교점 모두에 기둥이 있는 칸만
    assert r["count"] == 1
    b = r["ids"][0]
    p = get(bs, b)["params"]
    assert "bulge" in p and round(ang_of(p["start"])) == 0 and round(ang_of(p["end"])) == 30
    c = g_of(bs).q(Q.beam_cut, b)
    assert (c[2], c[3]) == (C1, C2) and c[0] > 100 and c[1] > 100                              # 곡선 보 끝이 기둥 면까지 잘린다(기존 규칙)
    column(bs, C3, (6000 * cos(pi / 3), 6000 * sin(pi / 3)))
    r = ok(bs.run("beam.grid", {"level": F.LV2, "type": BT}))
    assert r["count"] == 1 and round(ang_of(get(bs, r["ids"][0])["params"]["start"])) == 30  # 30 -> 60 칸
    assert len(beam_rows(bs)) == 2
    same_as_full(bs)


# ---------------------------------------------------------------- 4. 반사 천장 평면의 보
def rcp_setup(s):
    """반사 천장 평면(1F, 절단면 2000, 보이는 깊이 1500: 밑면이 (2000, 3500] 인 보가 보인다)에 걸리는 보 넷과 안 걸리는 둘."""
    ok(s.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    beam(s, BA, (0, -1000), (6000, -1000), level=F.LV2, offset=0.0)       # 2F 보: 밑면 2600 -> 위에서 본 평면 외곽(투영선)
    beam(s, BB, (0, -3000), (4000, -3000), offset=2300.0)                  # 1F 보 1600 ~ 2300: 절단면 2000 에 걸린다
    beam(s, BCC, (0, -5000), (4000, -5000), offset=1500.0)                  # 800 ~ 1500: 절단면 아래(위를 보는 뷰에서는 안 보인다)
    beam(s, BD, (0, -7000), (4000, -7000), level=F.LV2, offset=1500.0)    # 3 800 ~ 4 500: 보이는 깊이(3500) 밖


def test_4_반사_천장_평면의_보(bs):
    g0 = g_of(bs)
    base_lines, base_fills = g0.q(D.view_lines, VR), g0.q(D.view_fills, VR)
    rcp_setup(bs)
    g = g_of(bs)
    assert g.q(D.scope_beams, VR) == (BA, BB)                                  # 밑면이 보이는 깊이 안인 보 + 절단면에 걸친 보. 레벨은 따지지 않는다
    # 밑면 윤곽: 투영선 실선 네 변(평면 외곽, 파선이 아니다)
    ln = g.q(D.beam_lines, VR, BA)
    assert len(ln) == 4 and all(x[0] == "proj" for x in ln)
    assert sorted({round(sqrt((x[3] - x[1]) ** 2 + (x[4] - x[2]) ** 2), 3) for x in ln}) == [400.0, 6000.0]
    assert sorted({x[1] for x in ln} | {x[3] for x in ln}) == [0.0, 6000.0] and sorted({x[2] for x in ln} | {x[4] for x in ln}) == [-1200.0, -800.0]
    assert g.q(D.beam_fills, VR, BA) == ()                                      # 절단되지 않았다: 채움 없음
    # 절단면에 걸친 보: 평면과 같은 절단선 + 절단 채움(폭 400 x 길이 4000)
    cl = g.q(D.beam_lines, VR, BB)
    assert len(cl) == 4 and all(x[0] == "cut" for x in cl)
    fl = g.q(D.beam_fills, VR, BB)
    assert len(fl) == 1 and abs(D._area2(list(fl[0]))) / 2.0 == pytest.approx(400.0 * 4000.0)
    # 절단면 아래·보이는 깊이 밖의 보는 그리지 않는다
    assert BCC not in g.q(D.scope_beams, VR) and BD not in g.q(D.scope_beams, VR) and g.q(D.beam_lines, VR, BCC) == ()
    vl = g.q(D.view_lines, VR)
    norm = lambda segs: {(x[0],) + tuple(sorted([(x[1], x[2]), (x[3], x[4])])) for x in segs}   # 선분 방향은 정규화해서 견준다
    assert norm(x for x in vl if x not in base_lines) == norm(ln) | norm(cl)
    assert g.q(D.view_fills, VR) == base_fills + fl
    # 일반 평면은 그대로: 절단면(1200) 위의 보는 숨은선 파선(마디 2mm x 축척 100)
    pl = g.q(D.beam_lines, V, BA)
    assert pl and all(x[0] == "proj" for x in pl) and max(sqrt((x[3] - x[1]) ** 2 + (x[4] - x[2]) ** 2) for x in pl) == pytest.approx(D.HIDDEN_DASH * 100)
    assert g.q(D.scope_beams, V) == (BA, BB, BCC)                                # 1F 평면(이전 그대로): 다음 레벨 높이 아래에 걸친 보까지(BD 는 밑면이 3300 위)
    same_as_full(bs)
    # 보를 옮겨 범위 밖으로 -> 사라지고 되돌리면 돌아온다
    ok(bs.run("beam.set", {"id": BA, "offset": 1500}))
    assert g_of(bs).q(D.scope_beams, VR) == (BB,)
    same_as_full(bs)
    ok(bs.run("history.undo", {}))
    assert g_of(bs).q(D.scope_beams, VR) == (BA, BB)
    # 보이는 깊이를 0 으로: 절단면에 걸친 보만(밑면이 절단면 위 0 mm 안인 보는 없다)
    ok(bs.run("view.set", {"id": VR, "view_depth": 0}))
    assert g_of(bs).q(D.scope_beams, VR) == (BB,)
    same_as_full(bs)


def test_4_반사_천장_평면_곡선_보와_경사_보(bs):
    rcp_setup(bs)
    beam(bs, BE, (0, -10000), (6000, -10000), level=F.LV2, offset=0.0, bulge=0.5)            # 곡선 보(밑면 2600): 반지름 3750, 진행 방향 오른쪽(아래)으로 부푼다
    beam(bs, BF, (0, -13000), (4000, -13000), offset=2000.0, end_offset=2800.0)               # 경사 보: 윗면 2000 ~ 2800, 절단면에 걸친다
    g = g_of(bs)
    sc = g.q(D.scope_beams, VR)
    assert BE in sc and BF in sc
    cl = g.q(D.beam_lines, VR, BE)
    assert len(cl) > 40 and all(x[0] == "proj" for x in cl)                                   # 호를 나눈 윤곽 다각형(바깥·안쪽 가장자리 + 양 끝)
    ai = BM.arc_info(get(bs, BE)["params"])
    for x in cl:                                                                               # 모든 점이 호 둘레(반지름 ± 보 폭 / 2)에 있다
        for px, py in ((x[1], x[2]), (x[3], x[4])):
            assert abs(sqrt((px - ai[0]) ** 2 + (py - ai[1]) ** 2) - ai[2]) <= 201.0               # 마이터 늘림(1/cos(반각) = 1.0008)까지
    sl = g.q(D.beam_lines, VR, BF)
    assert sl and all(x[0] == "cut" for x in sl) and len(g.q(D.beam_fills, VR, BF)) == 1
    same_as_full(bs)


def test_4_반사_천장_평면의_보_태그(bs):
    rcp_setup(bs)
    # 반사 천장 평면은 레벨이 달라도 뷰가 그리는 보면 태그를 단다(2F 보가 1F 천장 밑에 보인다)
    r = ok(bs.run("annot.tag.all", {"view": VR, "kinds": ["beam"], "fields": ["type", "size"]}))
    assert sorted(t["target"] for t in r["tags"]) == [BA, BB]
    assert err(bs.run("annot.tag.create", {"view": VR, "target": BCC, "fields": ["name"]}))["code"] == "CONSTRAINT"   # 안 그려지는 보는 못 단다
    assert err(bs.run("annot.tag.create", {"view": V, "target": BA, "fields": ["name"]}))["code"] == "CONSTRAINT"     # 일반 평면은 같은 레벨만(이전과 같다)
    r1 = ok(bs.run("annot.tag.all", {"view": V, "kinds": ["beam"], "fields": ["type"]}))
    assert sorted(t["target"] for t in r1["tags"]) == [BB, BCC]
    it = {x[1]: x for x in g_of(bs).q(AN.view_annots, VR) if x[0] == "tag"}
    ta = next(t["id"] for t in r["tags"] if t["target"] == BA)
    assert it[ta][2] == (3000.0, -1000.0) and it[ta][4] == "G1 R-400x700" and it[ta][5] == (1.0, 0.0)
    same_as_full(bs)
    # 반사 천장 평면 태그의 저장·IFC 왕복(태그 정의)은 test_2 가 본다
    sc = {(x[0], x[1]): x for x in g_of(bs).q(__import__("dobim_core.scene", fromlist=["x"]).view_scene, VR)}
    assert ("beam", BA) in sc and sc[("beam", BA)][2] and ("beam", BB) in sc and sc[("beam", BB)][3]   # GUI 장면: 선분과 절단 채움


def test_4_보가_없거나_범위_밖이면_반사_천장_평면은_그대로(bs):
    g0 = g_of(bs)
    lines0, fills0 = g0.q(D.view_lines, VR), g0.q(D.view_fills, VR)
    ok(bs.run("beamtype.create", {"id": BT, "name": "G1", "width": 400, "depth": 700}))
    beam(bs, BCC, (0, -5000), (4000, -5000), offset=1500.0)                                   # 절단면 아래
    beam(bs, BD, (0, -7000), (4000, -7000), level=F.LV2, offset=1500.0)                       # 보이는 깊이 밖
    g = g_of(bs)
    assert g.q(D.view_lines, VR) == lines0 and g.q(D.view_fills, VR) == fills0 and g.q(D.scope_beams, VR) == ()
    same_as_full(bs)
