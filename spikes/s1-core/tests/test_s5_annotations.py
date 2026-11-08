# SPDX-License-Identifier: MPL-2.0
"""S5 연관 주석: 참조 체계, 치수·태그 값, 고아 표시, 벽 분할 규칙, 증분 대 전체, 오라클, 임의 편집열, 음성 대조, SVG, 해시 불변."""
import ast
import json
import sys
from pathlib import Path

import pytest

from conftest import ROOT
from dobim_core import annot as AN
from dobim_core import fixture as F

S5 = ROOT.parent / "s5-annotations"
sys.path.insert(0, str(S5))
import s5lib as L  # noqa: E402

R = AN.make_ref


@pytest.fixture
def pv(base):
    """기준 프로젝트 + 1F 평면 뷰."""
    r = base.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1})
    assert r["ok"], r
    return base


def dim(sess, a, b, view=F.V1, **kw):
    r = sess.run("annot.dim.create", {"view": view, "a": a, "b": b, **kw})
    assert r["ok"], r
    return r["result"]["id"]


def state(sess, aid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(AN.annot_state, aid)


# ---------------------------------------------------------------- 참조 문법
def test_참조는_종류_id_역할_이름이고_위상_인덱스를_쓰지_않는다():
    ref = R("wall", F.W1, "trim.end")
    assert ref == f"wall:{F.W1}#trim.end" and AN.parse_ref(ref) == ("wall", F.W1, "trim.end")
    assert AN.parse_ref(f"wall:{F.W1}#face.3") is None            # 면 인덱스는 역할이 아니다
    assert AN.parse_ref(f"wall:{F.W1}#jamb.start") is None        # 다른 종류의 역할
    assert AN.parse_ref("wall:짧은id#axis") is None and AN.parse_ref("garbage") is None
    for kind, roles in AN.ROLE_TYPES.items():
        assert all(t in ("pt", "line", "z") for t in roles.values())
        assert not any(ch.isdigit() for r in roles for ch in r)


# ---------------------------------------------------------------- 치수: 생성 검사와 값
def test_치수_값은_손계산과_같다(pv):
    s = pv
    L1 = dim(s, R("wall", F.W1, "trim.start"), R("wall", F.W1, "trim.end"))                       # W1-W2 조인: 끝은 6000 에서 정리
    gap = dim(s, R("wall", F.W1, "face.left"), R("wall", F.W3, "face.left"))                      # y=+100 과 y=3900
    axes = dim(s, R("wall", F.W1, "axis"), R("wall", F.W3, "axis"))
    jamb = dim(s, R("door", F.D1, "jamb.start"), R("door", F.D1, "jamb.end"))
    pos = dim(s, R("wall", F.W1, "axis.start"), R("door", F.D1, "center"))
    pl = dim(s, R("door", F.D1, "center"), R("wall", F.W3, "face.right"))                         # 점-선
    assert state(s, L1)[2] == 6000.0 and state(s, gap)[2] == 3800.0 and state(s, axes)[2] == 4000.0
    assert state(s, jamb)[2] == 900.0 and state(s, pos)[2] == 1500.0
    assert state(s, pl)[2] == pytest.approx(4000.0 + 100.0)   # W3 오른쪽 면은 y=4100
    sec = s.run("view.create", {"name": "A", "type": "section", "p1": [0, 2000], "p2": [6000, 2000]})["result"]["id"]
    h = dim(s, R("level", F.LV1, "elevation"), R("level", F.LV2, "elevation"), view=sec, at=[-500, 0])
    dh = dim(s, R("door", F.D1, "sill"), R("door", F.D1, "head"), view=sec)
    assert state(s, h)[2] == 3300.0 and state(s, dh)[2] == 2100.0


def test_치수_생성은_잘못된_참조를_구조화_오류로_거절한다(pv):
    s = pv
    ok_a, ok_b = R("wall", F.W1, "axis.start"), R("wall", F.W1, "axis.end")
    bad = [
        ({"a": R("wall", F.W1, "axis"), "b": R("wall", F.W2, "axis")}, "CONSTRAINT"),               # 평행하지 않은 선
        ({"a": ok_a, "b": R("level", F.LV1, "elevation")}, "CONSTRAINT"),                           # 평면 + 높이 참조 혼합
        ({"a": ok_a, "b": R("wall", F.W1, "axis.mid")}, "CONSTRAINT"),                              # 없는 역할(문법은 맞아도 역할 목록에 없다)
        ({"a": ok_a, "b": R("wall", "9" * 22, "axis.end")}, "NOT_FOUND"),                          # 없는 요소
        ({"a": ok_a, "b": R("door", F.D1, "sill")}, "CONSTRAINT"),                                  # 점 + 높이 혼합
    ]
    for extra, code in bad:
        r = s.run("annot.dim.create", {"view": F.V1, **extra})
        assert not r["ok"] and r["error"]["code"] == code, (extra, r)
    assert s.run("annot.dim.create", {"view": F.V1, "a": ok_a, "b": R("wall", F.W1, "axis.end"), "at": [1, 2]})["error"]["code"] == "CONSTRAINT"
    # 높이 치수는 평면 뷰에, 평면 치수는 단면 뷰에 둘 수 없다
    assert s.run("annot.dim.create", {"view": F.V1, "a": R("level", F.LV1, "elevation"), "b": R("level", F.LV2, "elevation")})["error"]["code"] == "CONSTRAINT"
    sec = s.run("view.create", {"name": "A", "type": "section", "p1": [0, 2000], "p2": [6000, 2000]})["result"]["id"]
    assert s.run("annot.dim.create", {"view": sec, "a": ok_a, "b": ok_b})["error"]["code"] == "CONSTRAINT"
    # 다른 레벨의 벽은 이 평면 뷰에 둘 수 없다
    w2f = s.run("wall.create", {"level": F.LV2, "axis": [0, 0, 5000, 0]})["result"]["id"]
    assert s.run("annot.dim.create", {"view": F.V1, "a": ok_a, "b": R("wall", w2f, "axis.end")})["error"]["code"] == "CONSTRAINT"
    assert s.eng.st.by_kind.get("dim", set()) == set()          # 거절된 것은 아무것도 남기지 않는다


def test_주석은_값을_저장하지_않고_IFC로_투영하지_않는다(pv):
    s = pv
    before = s.eng.hashes()
    d = dim(s, R("wall", F.W1, "axis.start"), R("wall", F.W1, "axis.end"))
    t = s.run("annot.tag.create", {"view": F.V1, "target": F.D1})["result"]["id"]
    after = s.eng.hashes()
    assert after["ifc"] == before["ifc"]                                   # 투영하지 않는다: IFC 해시 불변
    assert after["state"] != before["state"] and after["derived"] != before["derived"]
    assert "value" not in s.eng.st.el[d]["params"] and "text" not in s.eng.st.el[t]["params"]   # 값은 파생
    assert s.run("pset.set", {"id": d, "pset": "Pset_X", "prop": "p", "value": 1})["error"]["code"] == "CONSTRAINT"
    assert s.run("annot.delete", {"id": d})["ok"] and s.run("annot.delete", {"id": t})["ok"]
    assert s.eng.hashes()["derived"] == before["derived"]                  # 주석이 없으면 파생 해시 키도 없다(기존 해시 그대로)
    assert s.run("annot.delete", {"id": F.W1})["error"]["code"] == "CONSTRAINT"      # 주석이 아닌 요소는 못 지운다
    assert s.run("model.stats", {})["result"]["counts"] == {"level": 2, "wall": 3, "door": 2}   # 주석은 요소 개수에 안 들어간다


# ---------------------------------------------------------------- 편집을 견딘다
def test_벽_이동_문_변경_조인_변경에서_값이_따라가고_고아가_없다(pv):
    s = pv
    a = dim(s, R("wall", F.W1, "trim.start"), R("wall", F.W1, "trim.end"))
    b = dim(s, R("wall", F.W1, "axis.start"), R("door", F.D1, "jamb.end"))
    c = dim(s, R("wall", F.W1, "face.left"), R("wall", F.W3, "face.left"))
    t = s.run("annot.tag.create", {"view": F.V1, "target": F.D1, "fields": ["name", "width"]})["result"]["id"]
    assert state(s, t)[2] == f"D-{F.D1[-6:]} 900"
    assert s.run("wall.move", {"id": F.W3, "dy": 600})["ok"]
    assert state(s, c)[2] == 4400.0                                         # 간격이 따라간다
    assert s.run("door.set", {"id": F.D1, "width": 1000, "offset": 2000})["ok"]
    assert state(s, b)[2] == 2500.0 and state(s, t)[2].endswith("1000")
    assert s.run("join.clear", {"a": F.W1, "b": F.W2})["ok"]
    assert state(s, a)[2] == 6000.0
    chk = s.run("annot.check", {})["result"]
    assert chk["ok"] == 4 and chk["orphan"] == {} and chk["invalid"] == {} and chk["dangling_view"] == 0
    assert s.eng.derived_hash() == s.eng.derived_hash(reference=True)      # 증분 == 전체 재계산


def test_대상을_지우면_고아로_표시하고_조용히_지우지_않으며_undo_로_돌아온다(pv):
    s = pv
    a = dim(s, R("wall", F.W1, "trim.start"), R("wall", F.W1, "trim.end"))
    b = dim(s, R("door", F.D1, "jamb.start"), R("door", F.D1, "jamb.end"))
    t = s.run("annot.tag.create", {"view": F.V1, "target": F.D1})["result"]["id"]
    snap = {x: state(s, x) for x in (a, b, t)}
    r = s.run("element.delete", {"id": F.W1})                                # 벽과 호스팅된 문 D1 이 같이 지워진다
    assert r["ok"] and F.D1 in r["result"]["deleted"]
    assert {a, b, t} <= s.eng.st.el.keys()                                    # 주석 자체는 남는다
    assert state(s, a)[1:] == ("orphan", (("a", "target_deleted"), ("b", "target_deleted")))
    assert state(s, b)[1] == "orphan" and state(s, t)[1:] == ("orphan", (("target", "target_deleted"),))
    chk = s.run("annot.check", {})["result"]
    assert chk["orphan"] == {"target_deleted": 3} and chk["problems"] == 3
    assert {i["id"] for i in s.run("annot.list", {"status": "orphan"})["result"]["items"]} == {a, b, t}
    assert s.run("history.undo", {})["ok"]
    assert {x: state(s, x) for x in (a, b, t)} == snap                       # 정확히 같은 값으로 복원
    assert s.run("annot.check", {})["result"]["problems"] == 0


def test_수동_복구_재결합은_고아를_새_참조로_되살린다(pv):
    s = pv
    a = dim(s, R("wall", F.W1, "axis"), R("wall", F.W3, "axis"))
    t = s.run("annot.tag.create", {"view": F.V1, "target": F.W3, "fields": ["name"]})["result"]["id"]
    assert s.run("element.delete", {"id": F.W3})["ok"]
    assert state(s, a)[1] == "orphan" and state(s, t)[1] == "orphan"
    w4 = s.run("wall.create", {"level": F.LV1, "axis": [0, 5000, 6000, 5000]})["result"]["id"]
    assert s.run("annot.rebind", {"id": a, "b": R("wall", w4, "axis")})["ok"]
    assert state(s, a)[1:] == ("ok", 5000.0)
    assert s.run("annot.rebind", {"id": t, "target": w4})["ok"] and state(s, t)[1] == "ok"
    assert s.run("annot.rebind", {"id": a, "target": w4})["error"]["code"] == "CONSTRAINT"      # 치수에 target 은 안 된다


# ---------------------------------------------------------------- 벽 분할과 참조 유지 규칙
def test_벽_분할은_치수_값을_바꾸지_않고_끝_참조는_후계를_따라간다(pv):
    s = pv
    ds = [dim(s, R("wall", F.W1, "trim.start"), R("wall", F.W1, "trim.end")),
          dim(s, R("wall", F.W1, "axis.start"), R("wall", F.W1, "axis.end")),
          dim(s, R("wall", F.W1, "face.left"), R("wall", F.W3, "face.left")),
          dim(s, R("wall", F.W1, "trim.start"), R("door", F.D1, "jamb.start"))]
    before = {x: state(s, x) for x in ds}
    r = s.run("wall.split", {"id": F.W1, "at": 3000.0, "new_id": L._i("1", 777)})
    assert r["ok"], r
    n = r["result"]["new"]
    assert s.eng.st.el[F.W1]["params"]["axis"] == [0, 0, 3000.0, 0.0] and s.eng.st.el[n]["params"]["axis"] == [3000.0, 0.0, 6000, 0]
    assert {x: state(s, x) for x in ds} == before                            # 값이 그대로
    assert s.eng.st.out[("succ", F.W1)] == [n]
    # 끝 참조는 새 벽으로 해석된다
    assert s.eng.g.q(AN.ref_resolve, R("wall", F.W1, "axis.end")) == ("pt", 6000, 0)
    assert s.eng.g.q(AN.ref_resolve, R("wall", F.W1, "axis.start")) == ("pt", 0, 0)
    # 후계 벽을 지우면 '대상이 사라졌다'가 아니라 '이어받은 벽이 사라졌다'로 고아가 된다. 자기 끝(분할점)으로 조용히 바뀌지 않는다
    assert s.run("element.delete", {"id": n})["ok"]
    assert state(s, ds[1])[1:] == ("orphan", (("b", "successor_deleted"),))
    assert state(s, ds[2])[1] == "ok" and state(s, ds[0])[1] == "orphan"
    assert s.run("history.undo", {})["ok"] and {x: state(s, x) for x in ds} == before


def test_벽_분할_규칙_문_조인_속성_그리고_거절(pv):
    s = pv
    assert s.run("pset.set", {"id": F.W1, "pset": "Pset_WallCommon", "prop": "FireRating", "value": "EI60"})["ok"]
    t_wall = s.run("wall.create", {"level": F.LV1, "axis": [3000, 0, 3000, 2000], "name": "T"})["result"]["id"]    # W1 중간(x=3000)에 닿는 T 조인 벽
    assert s.run("join.set", {"a": t_wall, "b": F.W1})["ok"]
    h0 = s.eng.state_hash()
    assert s.run("wall.split", {"id": F.W1, "at": 1500.0})["error"]["code"] == "CONSTRAINT"      # D1(1050..1950)을 가로지른다
    assert s.run("wall.split", {"id": F.W1, "at": 0.5})["error"]["code"] == "CONSTRAINT"
    assert s.run("wall.split", {"id": F.W1, "at": 5999.5})["error"]["code"] == "CONSTRAINT"
    assert s.run("wall.split", {"id": F.W1, "at": 2200.0})["error"]["code"] == "CONSTRAINT"      # T 조인 지점이 끝 조각 시작에서 800mm: 조인 정리가 달라진다
    assert s.run("wall.split", {"id": F.W1, "at": 3500.0})["error"]["code"] == "CONSTRAINT"      # 앞 조각 끝에서 500mm: 같은 이유
    assert s.eng.state_hash() == h0                                                                  # 거절된 분할은 아무것도 바꾸지 않는다
    n = s.run("wall.split", {"id": F.W1, "at": 1990.0})["result"]["new"]
    assert s.eng.ifc.get_psets(n)["Pset_WallCommon"]["FireRating"] == "EI60" and s.eng.ifc.get_material(n) == s.eng.ifc.get_material(F.W1)
    assert s.eng.st.out[("hosts", F.W1)] == [F.D1] and s.eng.st.out.get(("hosts", n), []) == []   # 문은 놓인 조각에 남는다

    def partners(w):
        return set(s.eng.st.out.get(("joins", w), ())) | set(s.eng.st.inn.get(("joins", w), ()))
    assert partners(n) == {t_wall, F.W2} and partners(F.W1) == set()                               # 조인은 조인 지점이 있는 조각이 가진다
    # 문이 끝 쪽 조각에 있으면 문 id 가 그대로이고 호스트·오프셋만 옮겨진다
    r = s.run("wall.split", {"id": F.W3, "at": 2000.0})
    n3 = r["result"]["new"]
    assert r["result"]["doors_moved"] == [F.D2] and s.eng.st.el[F.D2]["params"]["offset"] == 1000.0       # 3000 - 2000
    assert s.eng.st.inn[("hosts", F.D2)] == [n3]
    assert s.eng.g.q(AN.ref_resolve, R("door", F.D2, "center")) == ("pt", 3000.0, 4000.0)          # 세계 좌표는 그대로
    assert s.run("model.validate", {"express_rules": False})["result"]["errors"] == 0
    assert s.run("history.undo", {})["ok"] and s.run("history.undo", {})["ok"]                       # 분할 두 번 = 트랜잭션 두 개
    assert s.eng.state_hash() == h0
    assert s.eng.derived_hash() == s.eng.derived_hash(reference=True)


def test_분할_후계_사슬(pv):
    s = pv
    d = dim(s, R("wall", F.W3, "axis.start"), R("wall", F.W3, "axis.end"))
    base_v = state(s, d)[2]
    n1 = s.run("wall.split", {"id": F.W3, "at": 4000.0, "new_id": L._i("1", 801)})["result"]["new"]
    n2 = s.run("wall.split", {"id": F.W3, "at": 1500.0, "new_id": L._i("1", 802)})["result"]["new"]
    assert s.eng.st.out[("succ", F.W3)] == [n2] and s.eng.st.out[("succ", n2)] == [n1]       # W3 -> n2 -> n1
    assert state(s, d)[2] == base_v
    assert s.run("element.delete", {"id": n1})["ok"]                       # 사슬 맨 끝이 사라지면
    assert state(s, d)[1:] == ("orphan", (("b", "successor_deleted"),))


# ---------------------------------------------------------------- 태그
def test_태그_글은_속성_이름_재료를_따라가고_대상을_지우면_고아다(pv):
    s = pv
    t = s.run("annot.tag.create", {"view": F.V1, "target": F.D1, "fields": ["name", "width", "fire_rating", "material"]})["result"]["id"]
    t2 = s.run("annot.tag.create", {"view": F.V1, "target": F.D1, "fields": ["host", "level"]})["result"]["id"]
    assert state(s, t)[2] == f"D-{F.D1[-6:]} 900 EI30 -" and state(s, t2)[2] == f"W-{F.W1[-6:]} 1F"
    assert s.run("pset.set", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI90"})["ok"]
    assert s.run("material.set", {"id": F.D1, "name": "Timber"})["ok"]
    assert s.run("door.set", {"id": F.D1, "name": "문-1", "width": 950})["ok"]
    assert s.run("wall.set", {"id": F.W1, "name": "외벽"})["ok"]
    assert state(s, t)[2] == "문-1 950 EI90 Timber" and state(s, t2)[2] == "외벽 1F"
    assert s.run("annot.tag.create", {"view": F.V1, "target": F.D1, "fields": ["thickness"]})["error"]["code"] == "CONSTRAINT"   # 문에 없는 필드
    assert s.run("annot.tag.create", {"view": F.V1, "target": F.LV1})["error"]["code"] == "CONSTRAINT"                           # 레벨에는 못 단다
    assert s.run("element.delete", {"id": F.D1})["ok"] and state(s, t)[1] == "orphan" and state(s, t2)[1] == "orphan"


# ---------------------------------------------------------------- 뷰 삭제 연쇄
def test_뷰를_지우면_그_뷰의_주석도_같이_지우고_응답에_나열하며_undo_로_돌아온다(pv):
    s = pv
    a = dim(s, R("wall", F.W1, "axis.start"), R("wall", F.W1, "axis.end"))
    t = s.run("annot.tag.create", {"view": F.V1, "target": F.D1})["result"]["id"]
    h0 = s.eng.state_hash()
    r = s.run("view.delete", {"id": F.V1})
    assert r["ok"] and set(r["result"]["deleted"]) == {F.V1, a, t}
    assert not ({a, t} & s.eng.st.el.keys()) and s.run("annot.check", {})["result"]["dangling_view"] == 0
    assert s.run("history.undo", {})["ok"] and s.eng.state_hash() == h0
    r = s.run("element.delete", {"id": F.V1})                              # 일반 삭제 경로도 같다
    assert r["ok"] and set(r["result"]["deleted"]) == {F.V1, a, t}


# ---------------------------------------------------------------- 내보내기(SVG)
def test_뷰_내보내기에_주석이_그려지고_고아는_빨갛게_이유와_함께_표시된다(pv, tmp_path):
    s = pv
    dim(s, R("wall", F.W1, "trim.start"), R("wall", F.W1, "trim.end"))
    dim(s, R("wall", F.W3, "axis.start"), R("wall", F.W3, "axis.end"))
    s.run("annot.tag.create", {"view": F.V1, "target": F.D1, "fields": ["name", "width"]})
    s.run("element.delete", {"id": F.W3})                                   # W3 과 그 문 D2 삭제 -> 두 번째 치수가 고아
    ws = s.path.parent

    def export(name):
        tok = s.run("view.export", {"id": F.V1, "path": name})["error"]["details"]["confirm_token"]
        r = s.run("view.export", {"id": F.V1, "path": name}, confirm=tok)
        assert r["ok"], r
        return r["result"], (ws / name).read_text(encoding="utf-8")

    res, svg = export("a.svg")
    assert res["annotations"] == 3
    assert svg.count('class="dim"') == 1 and svg.count('class="tag"') == 1 and svg.count('class="orphan"') == 1
    assert ">6000<" in svg and f"D-{F.D1[-6:]} 900" in svg                     # 값과 태그 글
    assert "고아 치수" in svg and "대상 삭제됨" in svg                           # 한글 이유
    tok = s.run("view.export", {"id": F.V1, "path": "a.pdf"})["error"]["details"]["confirm_token"]
    r = s.run("view.export", {"id": F.V1, "path": "a.pdf"}, confirm=tok)
    assert r["ok"] and r["result"]["non_ascii_dropped"] is True              # PDF 는 한글을 물음표로 쓴다(기존 한계 그대로)


# ---------------------------------------------------------------- 오라클: RA-01 주석 70개 + 편집 시나리오
@pytest.fixture(scope="module")
def ra01(tmp_path_factory):
    work = tmp_path_factory.mktemp("s5ra01")
    sess, ids, views = L.build_ra01(work / "r.dobim")
    h = L.Harness(sess)
    made = L.place_annotations(h, ids, views)
    yield h, ids, views, made
    sess.close()


def test_RA01_주석_50개_이상_배치_기준선은_전부_정상이고_오라클과_일치(ra01):
    h, ids, views, made = ra01
    assert len(made) >= 50 and len({c for _, c in made}) >= 8
    res = h.check()
    assert res["counts"]["ok_match"] == len(made) and res["n_bad"] == 0
    assert sum(1 for a in L.annot_ids(h.eng) if h.eng.st.el[a]["kind"] == "dim") >= 50


def test_RA01_편집_시나리오_뒤_오라클_일치_고아_0_값_오류_0(ra01):
    h, ids, views, made = ra01
    base_snap = L.snapshot(h.eng)
    h.run("wall.move", {"id": ids["ext-1F"][1], "dx": 600.0, "dy": 0.0})
    assert h.check()["n_bad"] == 0
    h.run("door.set", {"id": ids["D-01"], "width": 1000.0})
    h.run("door.set", {"id": ids["D-02"], "offset": 2140.0})
    assert h.check()["n_bad"] == 0
    h.run("history.undo", {}), h.run("history.undo", {}), h.run("history.undo", {})
    assert L.snapshot(h.eng) == base_snap                                   # 정확히 복원
    # 벽 삭제 -> 정상적 고아만 생기고 오라클과 같은 수
    h.run("element.delete", {"id": ids["int-1F"][0]})
    c = h.check()
    assert c["n_bad"] == 0 and c["counts"]["orphan_expected"] > 0 and c["counts"].get("false_orphan", 0) == 0
    h.run("history.undo", {})
    assert L.snapshot(h.eng) == base_snap
    # 분할은 모든 치수 값을 그대로 둔다
    h.run("wall.split", {"id": ids["ext-1F"][0], "at": 6000.0, "new_id": L._i("1", 90001)})
    assert not [d for d in L.stable_diff(base_snap, L.snapshot(h.eng)) if h.eng.st.el[d[0]]["kind"] == "dim"]
    assert h.check()["n_bad"] == 0
    h.run("history.undo", {})
    assert L.snapshot(h.eng) == base_snap


def test_편집_하나가_다시_계산시키는_주석_노드는_일부뿐이다(ra01):
    h, ids, views, made = ra01
    m = L.edit_and_measure(h, [("wall.move", {"id": ids["ext-1F"][1], "dx": 600.0, "dy": 0.0})])
    assert m["ok"] and 0 < m["annot_recomputed"] < m["annot_nodes_total"] / 2
    h.run("history.undo", {})
    L.refresh_annotations(h.eng)


# ---------------------------------------------------------------- 임의 편집열과 음성 대조
@pytest.mark.parametrize("seed", [31, 32])
def test_임의_편집열에서_오라클_증분_전체_undo_복원이_모두_맞는다(seed, tmp_path):
    f = L.Fuzz(tmp_path, seed)
    r = f.run(200)
    f.close()
    assert r["n_failures"] == 0, r["failures"]
    st = r["stats"]
    assert st["checks"] > 200 and st["undo_checks"] > 10 and st.get("op_dim", 0) + st.get("op_tag", 0) > 8 and st["c_ok_match"] > 100 and st["full_check_ok"] == 1


@pytest.mark.parametrize("name", ["M1_분할_이력_무시", "M2_의존_기록_누락(축선 직접 읽기)", "M3_삭제된_대상의_옛_값_유지", "M5_면_왼쪽_오른쪽_뒤바뀜",
                                  "M10_고아를_0값으로_표시"])
def test_음성_대조_일부러_망가뜨리면_같은_검사가_잡는다(name, tmp_path):
    caught = 0
    for seed in (121, 122, 123):
        with L.mutation(name):
            f = L.Fuzz(tmp_path, seed)
            r = f.run(200, stop_on_fail=True)
            f.close()
        caught += r["n_failures"] > 0
        if caught:
            break
    assert caught, name


def test_변이_없으면_같은_시드가_통과한다(tmp_path):
    for seed in (121, 122):
        f = L.Fuzz(tmp_path, seed)
        assert f.run(200)["n_failures"] == 0
        f.close()


# ---------------------------------------------------------------- 레지스트리·순수성·응답 크기
def test_주석_모듈은_순수하다_시각_난수_파일_libm_없음():
    tree = ast.parse((ROOT / "src" / "dobim_core" / "annot.py").read_text(encoding="utf-8"))
    allowed = {"__future__", "math", "dobim_core"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] not in allowed]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in allowed:
                bad.append(node.module)
            if node.module == "math":
                bad += [a.name for a in node.names if a.name != "sqrt"]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("open", "print", "input", "exec", "eval"):
            bad.append(node.func.id)
    assert not bad, bad


def test_새_커맨드는_레지스트리_규약을_지키고_search_는_한_쪽에_들어간다(sess):
    from dobim_core import registry
    reg = registry.load()
    names = ["annot.dim.create", "annot.tag.create", "annot.rebind", "annot.delete", "annot.list", "annot.check", "wall.split"]
    for n in names:
        c = reg[n]
        assert c.examples and c.summary and c.args_model
    assert reg["annot.list"].risk == reg["annot.check"].risk == "read" and reg["wall.split"].risk == "write"
    r = sess.run("commands.search", {"limit": 1000})
    size = len(json.dumps(r, ensure_ascii=False).encode("utf-8"))
    assert r["page"]["truncated"] is False and len(r["result"]["items"]) == len(reg)
    assert size <= 6000 - 200, size                                          # 응답 상한(6,000B)에 200B 이상 여유
    d = sess.run("commands.describe", {"name": "annot.dim.create"})
    assert d["ok"] and "annot.refs" not in json.dumps(d)                    # 존재하지 않는 도구를 가리키지 않는다
