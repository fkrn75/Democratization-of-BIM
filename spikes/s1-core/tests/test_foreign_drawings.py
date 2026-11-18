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
