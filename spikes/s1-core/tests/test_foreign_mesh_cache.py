# SPDX-License-Identifier: MPL-2.0
"""외래 형상 저장 캐시(ADR-0017, 프로젝트 형식 2): 가져올 때 외래 요소의 정리 메시를 한 번 계산해 프로젝트 파일(foreign_mesh)에 담고,
열 때 키가 같고 바이트가 온전하면 삼각형화 없이 쓴다. 캐시는 파생물이라 없거나·낡았거나·깨졌어도 다시 계산하고 결과는 같다.
- 저장한 메시로 그린 도면 = 캐시를 지우고 다시 계산해 그린 도면(선·SVG 해시, 요소마다 형상 값)
- 깨진 바이트·다른 키(정리 규칙 판)는 쓰지 않고 다시 계산해 새로 저장한다
- 형식 1 파일(테이블 없음)도 열리고, 첫 형상 요청 뒤 캐시를 저장하며 형식 번호를 2 로 올린다
- 잠긴 파일에서는 저장을 기다리지 않고 건너뛴다(값은 같다)"""
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from dobim_core import dispatch
from dobim_core import foreign_geom as FG
from dobim_core import queries as Q
from dobim_core.engine import SCHEMA_VERSION

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "phase1-drawings"))
import foreign_corpus as FC  # noqa: E402


def _imported(ws: Path, sid: str) -> Path:
    src = FC.sample_path(sid)
    if src is None:
        pytest.skip(f"표본 {sid} 이 없다")
    ws.mkdir(parents=True, exist_ok=True)
    env = dispatch.Env(ws, "t")
    try:
        FC._import(env, src)
    finally:
        env.close()
    return ws / FC.PROJECT


def _copy(p: Path, ws: Path) -> Path:
    ws.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(p, ws / FC.PROJECT)
    return ws / FC.PROJECT


def _row(p: Path):
    con = sqlite3.connect(p)
    try:
        return con.execute("SELECT cache_key, ids, counts, verts, tris, digest FROM foreign_mesh WHERE id=1").fetchone()
    finally:
        con.close()


def _sql(p: Path, *stmts):
    con = sqlite3.connect(p, isolation_level=None)
    try:
        for s in stmts:
            con.execute(*s) if isinstance(s, tuple) else con.execute(s)
    finally:
        con.close()


def _render(p: Path):
    """프로젝트 하나의 정해진 뷰(foreign_corpus.view_specs) 해시와 외래 요소마다 형상 입력 값, 형상을 얻은 곳."""
    for f in p.parent.glob("*.svg"):   # 같은 작업 폴더에서 다시 그릴 때 내보내기가 덮어쓰기를 거절하지 않게
        f.unlink()
    env = dispatch.Env(p.parent, "t")
    try:
        views = FC.render_project(env)
        eng = env.engine(FC.PROJECT)
        geo = {e: eng.g.q(Q.fgeom, e) for e in sorted(eng.st.by_kind.get("foreign", ()))}
        return views, geo, eng.fmesh_source, eng.schema_version
    finally:
        env.close()


@pytest.mark.parametrize("sid,prefetch_min", [("BA", None), ("RA01", None), ("BS", 1), ("RA01-bonsai", 1)])
def test_저장한_메시로_그린_도면은_다시_계산한_도면과_같다(sid, prefetch_min, tmp_path, monkeypatch):
    if prefetch_min is not None:   # 작은 표본에서 미리 계산(iterator) 길로 계산·저장하게 문턱을 낮춘다(문턱은 캐시 키에 든다)
        monkeypatch.setattr(FG, "PREFETCH_MIN", prefetch_min)
    p = _imported(tmp_path / "a", sid)
    row = _row(p)
    assert row is not None, "가져오기가 외래 형상 캐시를 저장하지 않았다"
    con = sqlite3.connect(p)
    sha = con.execute("SELECT sha256 FROM foreign_base").fetchone()[0]
    con.close()
    assert row[0] == FG.cache_key(sha)
    stored = FG.unpack(*row[1:])
    assert stored is not None and len(stored) > 0
    v1, g1, src1, ver1 = _render(p)
    assert (src1, ver1) == ("stored", SCHEMA_VERSION)
    q = _copy(p, tmp_path / "b")
    _sql(q, "DELETE FROM foreign_mesh")
    v2, g2, src2, _ = _render(q)
    assert src2 == "computed"
    assert v1 == v2
    assert g1 == g2
    assert any(v is not None and v[0] == "mesh" for v in g1.values())
    assert _row(q) == row, "다시 계산해 저장한 캐시 바이트가 가져올 때와 다르다(결정적이어야 한다)"
    if sid in ("BA", "RA01") and prefetch_min is None:   # 저장소 표본 기준선(캐시 이전 값)과도 같다
        import json
        base = json.loads(FC.BASELINE.read_text(encoding="utf-8"))[sid]["views"]
        assert {k: {"lines_sha256": v["lines_sha256"], "svg_sha256": v["svg_sha256"]} for k, v in v1.items()} == base


def test_깨지거나_키가_다른_캐시는_쓰지_않고_다시_계산해_저장한다(tmp_path, monkeypatch):
    p = _imported(tmp_path / "a", "RA01")
    row = _row(p)
    v0, g0, src0, _ = _render(p)
    assert src0 == "stored"
    # 1) 꼭짓점 바이트 하나를 바꿨다: 다이제스트가 맞지 않는다
    bad = bytearray(row[3])
    bad[len(bad) // 2] ^= 0xFF
    assert FG.unpack(row[1], row[2], bytes(bad), row[4], row[5]) is None
    q = _copy(p, tmp_path / "b")
    _sql(q, ("UPDATE foreign_mesh SET verts=?", (bytes(bad),)))
    v1, g1, src1, _ = _render(q)
    assert (src1, v1, g1) == ("computed", v0, g0)
    assert _row(q) == row   # 다시 저장했다
    # 2) 다이제스트까지 맞춘 잘린 바이트: 길이가 맞지 않는다
    cols = (row[1], row[2], __import__("zlib").compress(b"\0" * 24), row[4])
    assert FG.unpack(*cols, FG._digest(cols)) is None
    # 3) 정리 규칙 판이 바뀌었다(키가 다르다): 다시 계산하고 새 키로 저장한다
    monkeypatch.setattr(FG, "MESH_FORMAT", "test-next")
    r = _copy(p, tmp_path / "c")
    v2, g2, src2, _ = _render(r)
    assert (src2, v2, g2) == ("computed", v0, g0)
    assert _row(r)[0] == FG.cache_key(__import__("json").loads(row[0])["base_sha256"]) != row[0]
    assert _row(r)[1:] == row[1:]


def test_형식_1_파일도_열리고_첫_형상_요청_뒤_캐시를_저장하며_형식을_올린다(tmp_path):
    p = _imported(tmp_path / "a", "BA")
    v0, g0, _, _ = _render(p)
    q = _copy(p, tmp_path / "b")
    _sql(q, "DROP TABLE foreign_mesh", "UPDATE meta SET value='1' WHERE key='schema_version'")   # 이 변경 전에 만든 파일과 같은 구조
    env = dispatch.Env(q.parent, "t")
    try:
        r = dispatch.run(env, "project.info", {}, project=FC.PROJECT)
        assert r["ok"] and r["result"]["schema_version"] == "1"
    finally:
        env.close()
    v1, g1, src1, ver1 = _render(q)
    assert (src1, ver1, v1, g1) == ("computed", "2", v0, g0)
    assert _row(q) == _row(p)
    v2, _g2, src2, _ = _render(q)
    assert (src2, v2) == ("stored", v0)


def test_잠긴_파일에서는_저장을_건너뛰고_값은_같다(tmp_path):
    p = _imported(tmp_path / "a", "RA01")
    v0, g0, _, _ = _render(p)
    q = _copy(p, tmp_path / "b")
    _sql(q, "DELETE FROM foreign_mesh")
    lock = sqlite3.connect(q, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")   # 다른 프로세스가 쓰는 중
    try:
        t0 = time.perf_counter()
        v1, g1, src1, _ = _render(q)
        assert time.perf_counter() - t0 < 60
        assert (src1, v1, g1) == ("computed", v0, g0)
    finally:
        lock.execute("ROLLBACK")
        lock.close()
    assert _row(q) is None   # 잠겨 있어 쓰지 않았다(다음에 열 때 다시 계산·저장한다)


def test_pack_unpack_왕복(tmp_path):
    meshes = {"b": (((0.0, 1.5, -2.25), (1e-2, 3.0, 4.0), (5.0, 6.0, 7.0)), ((0, 1, 2),)), "a": None,
              "c": (((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), ((0, 1, 2), (0, 2, 3)))}
    st = FG.unpack(*FG.pack(meshes))
    assert st is not None and len(st) == 3 and "a" in st and "z" not in st
    assert {k: st.get(k) for k in meshes} == meshes
    empty = FG.unpack(*FG.pack({}))
    assert empty is not None and len(empty) == 0
    cols = FG.pack(meshes)
    assert FG.unpack(*cols[:4], "0" * 64) is None
