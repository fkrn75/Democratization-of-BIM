# SPDX-License-Identifier: MPL-2.0
"""1단계 외래 IFC 도면 경로(elements-design.md 29절): 의도 없는 외래 요소를 IFC 형상으로 그리고, 표본 5건의 재생성 차등 시험을 한다.

표본·기준선은 spikes/phase1-drawings/foreign_corpus.py 와 corpus/(출처표 SOURCES.md)에 있다.
로컬 전용 표본(Revit 내보내기)은 DOBIM_REVIT_DIR 가 없으면 건너뛴다. 그 기준선은 저장소 밖(DOBIM_FOREIGN_LOCAL_BASELINE)에 둔다.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "spikes" / "phase1-drawings"))
import foreign_corpus as FC  # noqa: E402

from dobim_core import dispatch  # noqa: E402
from dobim_core import drawing as D  # noqa: E402
from dobim_core import queries as Q  # noqa: E402
from dobim_core.engine import _Inputs  # noqa: E402
from dobim_core.graph import RefGraph  # noqa: E402

REPO = [s for s, _f, repo in FC.SAMPLES if repo]
ALL = [s for s, _f, _r in FC.SAMPLES]


def _need(sid):
    p = FC.sample_path(sid)
    if p is None:
        pytest.skip(f"표본 {sid} 이 없다(로컬 전용 표본은 DOBIM_REVIT_DIR 로 지정)")
    return p


@pytest.fixture(scope="module")
def baseline():
    return json.loads(FC.BASELINE.read_text(encoding="utf-8"))


@pytest.fixture
def ba(tmp_path):
    """BA 표본을 가져온 프로젝트와 정해진 뷰."""
    env = dispatch.Env(tmp_path, "t")
    FC._import(env, _need("BA"))
    FC.render_project(env)
    yield env
    env.close()


def test_코퍼스는_5건이고_저장소_표본의_기준선이_있다(baseline):
    assert len(FC.SAMPLES) == 5
    assert set(baseline) == set(REPO)
    assert (FC.CORPUS / "SOURCES.md").is_file()


@pytest.mark.parametrize("sid", REPO)
def test_저장소_표본_재생성은_기준선과_같다(sid, baseline):
    r = FC.render_sample(_need(sid))
    assert FC._views_only(r) == baseline[sid]
    for name, v in r["views"].items():
        assert v["segments"] > 0, name   # 외래 요소가 실제로 그려진다(빈 도면이면 해시 비교가 아무것도 검사하지 못한다)


def test_로컬_표본_재생성은_결정적이고_로컬_기준선과_같다():
    src = _need("RA01-revit")
    r1, r2 = FC.render_sample(src), FC.render_sample(src)
    assert FC._views_only(r1) == FC._views_only(r2)
    assert all(v["segments"] > 0 for v in r1["views"].values())
    lb = os.environ.get("DOBIM_FOREIGN_LOCAL_BASELINE")
    if lb and Path(lb).is_file():
        assert FC._views_only(r1) == json.loads(Path(lb).read_text(encoding="utf-8"))["RA01-revit"]


@pytest.mark.parametrize("sid", ALL)
def test_수정_없는_왕복은_바이트_모델_도면_해시가_같다(sid):
    r = FC.roundtrip_sample(_need(sid))
    assert r["exports_equal"], r
    assert all(r["model_hash_equal"].values()), r
    assert r["views_equal"] and r["views"] >= 7, r


def test_다른_프로세스에서도_같은_해시(tmp_path):
    src = _need("BA")
    here = FC._views_only(FC.render_sample(src))
    p = subprocess.run([sys.executable, str(ROOT / "spikes" / "phase1-drawings" / "foreign_corpus.py"), "--sample", "BA", "--json"],
                       capture_output=True, timeout=600, env={**os.environ, "PYTHONUTF8": "1"})
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-800:]
    there = FC._views_only(json.loads(p.stdout.decode("utf-8"))["BA"])
    assert here == there


def test_증분_그래프와_전체_계산이_같다(ba):
    eng = ba.engine(FC.PROJECT)
    ref = RefGraph(_Inputs(eng))
    views = sorted(eng.st.by_kind["view"])
    for v in views:
        assert eng.g.q(D.view_lines, v) == ref.q(D.view_lines, v), v
        assert eng.g.q(D.view_omitted, v) == ref.q(D.view_omitted, v), v


def test_외래_요소_속성_편집은_도면을_바꾸지_않는다(ba):
    eng = ba.engine(FC.PROJECT)
    views = sorted(eng.st.by_kind["view"])
    before = {v: eng.g.q(D.view_lines, v) for v in views}
    wall = next(e for e in sorted(eng.st.by_kind["foreign"]) if eng.st.el[e]["ifc_class"] == "IfcWall")
    r = dispatch.run(ba, "pset.set", {"id": wall, "pset": "Pset_WallCommon", "prop": "IsExternal", "value": True}, project=FC.PROJECT)
    assert r["ok"], r
    assert {v: eng.g.q(D.view_lines, v) for v in views} == before


def test_평면은_범주별_절단선_입면은_투영선만(ba):
    eng = ba.engine(FC.PROJECT)
    g = eng.g
    by_name = {eng.st.el[v]["params"]["name"]: v for v in eng.st.by_kind["view"]}
    plan = g.q(D.view_lines, by_name["plan-0"])
    elev = g.q(D.view_lines, by_name["elev-S"])
    assert {ln[0] for ln in plan} >= {"cut", "proj"}
    assert {ln[0] for ln in elev} == {"proj"}
    # 범주: 벽은 heavy(절단선 cut), 가구는 light(절단해도 proj), 문·창·프록시는 medium(symbol)
    cats = {eng.st.el[e]["ifc_class"]: g.q(D.foreign_info, e)[0] for e in g.q(D.all_foreign)}
    assert cats["IfcWall"] == "heavy" and cats["IfcSlab"] == "heavy" and cats["IfcFurniture"] == "light"
    assert cats["IfcBuildingElementProxy"] == "medium"
    # 공간·대지·건물·개구부는 그리지 않고, 생략으로 세지도 않는다(층은 레벨)
    drawn = set(g.q(D.all_foreign))
    for e in eng.st.by_kind["foreign"]:
        cls = eng.st.el[e]["ifc_class"]
        if cls in ("IfcSpace", "IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcSpatialZone"):
            assert e not in drawn
            assert D.foreign_omitted(g, (e,)) == 0, cls


def test_평면_뷰는_외래_층을_레벨로_쓰고_층이_아닌_외래_요소는_거절한다(ba):
    eng = ba.engine(FC.PROJECT)
    storey = next(e for e in eng.st.by_kind["foreign"] if eng.st.el[e]["ifc_class"] == "IfcBuildingStorey")
    wall = next(e for e in sorted(eng.st.by_kind["foreign"]) if eng.st.el[e]["ifc_class"] == "IfcWall")
    assert eng.g.q(Q.fgeom, storey) == ("storey", 0.0)
    r = dispatch.run(ba, "view.create", {"id": "4E" + "0" * 20, "name": "벽을 레벨로", "type": "plan", "level": wall}, project=FC.PROJECT)
    assert not r["ok"] and r["error"]["code"] == "NOT_FOUND", r
    r = dispatch.run(ba, "view.create", {"id": "4E" + "0" * 19 + "1", "name": "층 평면 2400", "type": "plan", "level": storey, "cut_height": 2400}, project=FC.PROJECT)
    assert r["ok"], r
    cfg = eng.g.q(D.view_cfg, "4E" + "0" * 19 + "1")
    assert cfg[1] == storey and cfg[2] == 2400.0


def test_mm_표본의_외래_층_평면_뷰는_왕복에서_되살아난다(ba, tmp_path):
    r = FC._destructive(ba, "ifc.export", {"path": "a.ifc"})
    assert r["ok"]
    ws2 = Path(tempfile.mkdtemp(dir=tmp_path))
    env2 = dispatch.Env(ws2, "t")
    try:
        FC._import(env2, tmp_path / "a.ifc", "a.ifc")
        e1, e2 = ba.engine(FC.PROJECT), env2.engine(FC.PROJECT)
        plans1 = {v: e1.st.el[v]["params"] for v in e1.st.by_kind["view"] if e1.st.el[v]["params"]["type"] == "plan"}
        plans2 = {v: e2.st.el[v]["params"] for v in e2.st.by_kind.get("view", ()) if e2.st.el[v]["params"]["type"] == "plan"}
        assert plans1 and plans1 == plans2
        for v in plans1:
            assert e1.g.q(D.view_lines, v) == e2.g.q(D.view_lines, v)
    finally:
        env2.close()
        shutil.rmtree(ws2, ignore_errors=True)


def test_외래_요소가_없으면_새_질의는_빈_값이다(base):
    from dobim_core import fixture as F
    assert base.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1})["ok"]
    g = base.eng.g
    assert g.q(D.scope_foreign, F.V1) == () and g.q(D._f_occ, F.V1) == ((), (), {}, ())
    assert g.q(D.view_omitted, F.V1) == {"foreign": 0, "detached": 0}


# ---------------------------------------------------------------- 2차(elements-design.md 30절): 절단면 채움·외래 비내력벽·공간 실명·외래 층 기준선
def _views(eng):
    return {eng.st.el[v]["params"]["name"]: v for v in eng.st.by_kind["view"]}


def _even_odd_area(segs):
    """절단 선분 고리들의 짝홀 넓이(바깥 - 구멍)."""
    loops = D._f_loops(segs)
    areas = [abs(D._area2(lp)) for lp in loops]
    depth = [sum(1 for j in range(len(loops)) if j != i and areas[j] > areas[i] and D._f_pip(loops[i][0], loops[j])) for i in range(len(loops))]
    return sum(a if d % 2 == 0 else -a for a, d in zip(areas, depth)) / 2.0


def _sq(x0, y0, x1, y1):
    return [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]


def test_채움_다리는_구멍을_빼고_맞닿은_몸체는_합친다():
    # 구멍 있는 사각형: 다각형 하나, 넓이 = 바깥 - 구멍
    polys = D._f_fill_polys(_sq(0, 0, 100, 100) + _sq(20, 20, 40, 40))
    assert len(polys) == 1 and abs(D._area2(polys[0]) / 2 - (10000 - 400)) < 1e-6
    # 층 구성처럼 맞닿은 두 몸체(맞닿은 변은 두 번, 한쪽 변은 T 꼭짓점으로 나뉨): 합집합 하나
    a = _sq(0, 0, 10, 100)
    b = [(10, 0, 30, 0), (30, 0, 30, 100), (30, 100, 10, 100), (10, 100, 10, 50), (10, 50, 10, 0)]
    polys = D._f_fill_polys(a + b)
    assert len(polys) == 1 and abs(D._area2(polys[0]) / 2 - 3000) < 1e-6
    # 떨어진 두 고리는 두 다각형, 닫히지 않는 사슬은 버린다
    assert len(D._f_fill_polys(_sq(0, 0, 1, 1) + _sq(5, 5, 6, 6))) == 2
    assert D._f_fill_polys([(0, 0, 1, 0), (1, 0, 1, 1)]) == ()


def test_외래_heavy_요소는_절단면이_채워진다(ba):
    eng = ba.engine(FC.PROJECT)
    g = eng.g
    vs = _views(eng)
    for name in ("plan-0", "sec-x", "sec-y"):
        v = vs[name]
        assert g.q(D.view_fills, v), name
        for e in g.q(D.scope_foreign, v):
            fills = g.q(D.foreign_fills, v, e)
            cat = g.q(D.foreign_info, e)[0]
            segs = g.q(D._f_cut, v, e)
            if cat != "heavy" or not segs:
                assert fills == (), (name, e)
                continue
            assert abs(sum(D._area2(p) for p in fills) / 2.0 - _even_odd_area(segs)) < 1.0, (name, e)
    assert all(g.q(D.view_fills, vs[f"elev-{d}"]) == () for d in "NESW")


def test_외래_벽의_LoadBearing_거짓은_선_굵기_위계에서_cutn(ba):
    eng = ba.engine(FC.PROJECT)
    g = eng.g
    plan = _views(eng)["plan-0"]
    lines, hier = g.q(D.view_lines, plan), g.q(D.view_lines_hier, plan)
    assert not any(ln[0] == "cutn" for ln in hier)
    wall = next(e for e in g.q(D.scope_foreign, plan) if eng.st.el[e]["ifc_class"] in D.F_WALLS and g.q(D._f_cut, plan, e))
    r = dispatch.run(ba, "pset.set", {"id": wall, "pset": "Pset_WallCommon", "prop": "LoadBearing", "value": False}, project=FC.PROJECT)
    assert r["ok"], r
    assert g.q(D.foreign_nonbearing) == (wall,)
    hier2 = g.q(D.view_lines_hier, plan)
    assert g.q(D.view_lines, plan) == lines                       # 기본 선분은 그대로(위계 옵션일 때만 나뉜다)
    assert any(ln[0] == "cutn" for ln in hier2)
    assert hier2 == RefGraph(_Inputs(eng)).q(D.view_lines_hier, plan)
    r = dispatch.run(ba, "pset.set", {"id": wall, "pset": "Pset_WallCommon", "prop": "LoadBearing", "value": True}, project=FC.PROJECT)
    assert r["ok"], r
    assert g.q(D.view_lines_hier, plan) == hier


def test_외래_공간은_실명과_면적을_평면에_쓴다(ba):
    eng = ba.engine(FC.PROJECT)
    g = eng.g
    spaces = sorted(e for e in eng.st.by_kind["foreign"] if eng.st.el[e]["ifc_class"] == "IfcSpace")
    assert spaces
    vals = [g.q(Q.fgeom, e) for e in spaces]
    assert all(fg[0] in ("space", "spatial") for fg in vals) and any(fg[0] == "space" for fg in vals)
    plan = _views(eng)["plan-0"]
    labels = g.q(D.view_labels, plan)
    want = []
    for e, fg in zip(spaces, vals):
        if fg[0] == "space" and e in g.q(D.scope_foreign_spaces, plan):
            ent = eng.ifc.ents[e]
            want.append((ent.LongName or ent.Name).strip())
    assert want and [t[2] for t in labels if t[3] == D.LABEL_NAME_H] == want
    assert all(t[2].endswith("㎡") for t in labels if t[3] == D.LABEL_SUB_H)
    assert all(g.q(D.view_labels, _views(eng)[f"elev-{d}"]) == () for d in "NESW")
    assert D.foreign_omitted(g, tuple(spaces)) == 0


def test_외래_층은_단면_입면의_레벨_기준선이다(ba):
    eng = ba.engine(FC.PROJECT)
    g = eng.g
    storey = next(e for e in eng.st.by_kind["foreign"] if eng.st.el[e]["ifc_class"] == "IfcBuildingStorey")
    assert g.q(D.foreign_datums) == ((eng.st.el[storey]["params"]["name"], 0.0),)
    vs = _views(eng)
    for name, has in (("elev-S", True), ("sec-x", True), ("plan-0", False)):
        FC._destructive(ba, "view.export", {"id": vs[name], "path": f"d-{name}.svg"})
        svg = (ba.workspace / f"d-{name}.svg").read_text(encoding="utf-8")
        assert ('<g id="datums"' in svg) == has, name


def test_2차_질의도_증분_그래프와_전체_계산이_같다(ba):
    eng = ba.engine(FC.PROJECT)
    ref = RefGraph(_Inputs(eng))
    for v in sorted(eng.st.by_kind["view"]):
        for fn in (D.view_fills, D.view_labels, D.view_lines_hier):
            assert eng.g.q(fn, v) == ref.q(fn, v), (fn.__name__, v)
    assert eng.g.q(D.foreign_datums) == ref.q(D.foreign_datums)


def test_외래_요소가_없으면_2차_질의도_빈_값이다(base):
    from dobim_core import fixture as F
    assert base.run("view.create", {"id": F.V1, "name": "1F", "type": "plan", "level": F.LV1})["ok"]
    g = base.eng.g
    assert g.q(D.foreign_nonbearing) == () and g.q(D.foreign_datums) == ()
    assert g.q(D.scope_foreign_spaces, F.V1) == ()
