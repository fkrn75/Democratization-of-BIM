# SPDX-License-Identifier: MPL-2.0
"""W2 여섯 번째 PR(ARC-44·ARC-12·MEP-01): 솔리드 모델링 확장.
- 회전체 revolve(단면 + 볼록도 호, 임의 축, 부분 각), 3D 스윕 sweep3d(직선·3점 호 경로, z 포함, 윤곽 또는 부재 단면, 닫힌 고리)
- 곡면 해상도 res(seg 현 수 / tol 현 높이), 없으면 기존 32각형 그대로
- 불리언 ops(빼기·합치기·교집합: BSP 다면체 불리언, 같은 평면 조각 합치기 + T 이음 닫기), solid.boolean·solid.boolean.remove
- 이동·복사·회전·대칭, 실행 취소, 도면(평면·단면·입면) 증분 = 전체 재계산, IFC4·IFC2X3 검증 오류 0, IfcBooleanResult(CSG), 왕복 해시·바이트
- 예제: 원통 호퍼 + 롤러 + 볼트 구멍 플랜지 + 손잡이(합성 데이터)."""
import sys

import pytest

import kit  # noqa: F401
from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F
from dobim_core import globalid as GI
from dobim_core import solidops as SX
from dobim_core import solids as SO

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
sys.path.insert(0, str(ROOT.parent / "s3-drawings"))
import s3lib  # noqa: E402
import scenarios as S  # noqa: E402

I2, I3, I4, I5, I6 = ("f" + "0" * 19 + c for c in ("a2", "a3", "a4", "a5", "a6"))
X1, X2, X3 = ("3" + "0" * 19 + c for c in ("d1", "d2", "d3"))   # IFC 시험용: 첫 글자가 유효한 GlobalId(내보내기가 바꾸지 않는다)
CYL = {"form": "revolve", "profile": [[0, 0], [500, 0], [500, 1000], [0, 1000]], "origin": [0, 0, 0], "axis": [0, 0, 1], "ref": [1, 0, 0], "angle": 360}
BOX = {"form": "prism", "polygon": [[-1000, -1000], [1000, -1000], [1000, 1000], [-1000, 1000]], "z0": 0, "z1": 500}
PIPE = {"form": "sweep3d", "rail": [[0, 0, 0], [2000, 0, 0], [3000, 1000, 0], [3000, 1000, 2000]],
        "via": [None, [2000 + 707.10678, 1000 - 707.10678, 0], None], "section": {"shape": "pipe", "width": 100, "depth": 100, "wall": 10}}


def ok(r):
    assert r["ok"], r
    return r["result"]


def err(r):
    assert not r["ok"], r
    return r["error"]


def sp(**kw):
    return {"name": "x", "category": "proxy", **kw}


def closed_ok(verts, faces):
    """모든 모서리가 반대 방향으로 정확히 두 번 쓰인다(닫힌·방향 일관 다면체)."""
    seen = {}
    for f in faces:
        for lp in f:
            for i in range(len(lp)):
                e = (lp[i], lp[(i + 1) % len(lp)])
                seen[e] = seen.get(e, 0) + 1
    return all(c == 1 and seen.get((b, a)) == 1 for (a, b), c in seen.items())


def ngon_area(r, n):
    return 0.5 * n * r * r * SO.sincos_deg(360.0 / n)[0]


def lines_of(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


def mk_view(sess, **kw):
    return ok(sess.run("view.create", kw))["id"]


def same_as_full(sess, views):
    sess.eng.sync()
    sess.eng._touch_pending()
    g = sess.eng.g
    full = s3lib.RefGraph(s3lib._Inputs(sess.eng))
    for v in views:
        assert g.q(D.view_lines, v) == full.q(D.view_lines, v)
        assert g.q(D.view_fills, v) == full.q(D.view_fills, v)


def _ifc_errors(ff):
    import ifcopenshell.validate as IV
    lg = IV.json_logger()
    IV.validate(ff, lg, express_rules=True)
    return [x["message"][:300] for x in lg.statements if x["level"] in ("error", "critical") and "IfcGloballyUniqueId" not in x["message"]]


# ---------------------------------------------------------------- 순수 형상
def test_결정적_각도와_해상도():
    assert SX.atan2_deg(1, 1) == pytest.approx(45.0, abs=1e-12) and SX.atan2_deg(-1, -1) == pytest.approx(-135.0, abs=1e-12)
    assert SX.atan2_deg(3, 4) == pytest.approx(36.86989764584402, abs=1e-12) and SX.atan2_deg(0, -1) == 180.0
    assert SX.full_segments(None, 500) == 32 and SX.full_segments({"seg": 12}, 500) == 12
    small, big = SX.full_segments({"tol": 0.5}, 100), SX.full_segments({"tol": 0.5}, 2000)
    assert small < big and small % 4 == 0 and big % 4 == 0
    for r, n in ((100, small), (2000, big)):   # 현 높이가 허용 오차 이하이고, 4 줄이면 넘는다
        assert r * (1 - SO.sincos_deg(180.0 / n)[1]) <= 0.5 + 1e-9
        assert n == SX.SEG_MIN or r * (1 - SO.sincos_deg(180.0 / (n - 4))[1]) > 0.5
    assert SX.arc_segments(None, 500, 90) == 8 and SX.arc_segments({"seg": 10}, 1, 30) == 1
    assert SX.res_problem({"seg": 4}) and SX.res_problem({"tol": 0}) and SX.res_problem({"seg": 8, "tol": 1}) and SX.res_problem({"seg": 8.0})


def test_res_없는_부재_원_단면은_이전과_같고_res_가_현_수를_바꾼다():
    m = {"form": "member", "start": [0, 0, 0], "end": [1000, 0, 0], "section": {"shape": "pipe", "width": 200, "depth": 200, "wall": 10}, "anchor": "center"}
    o, h = SO.member_section(m["section"], "center")
    assert o == SO._circle(100.0, 0.0, 0.0) and len(o) == 32                 # 기본 경로는 그대로(이등분 32각형)
    v0, _ = SO.solid_mesh(m)
    v1, f1 = SO.solid_mesh({**m, "res": {"seg": 64}})
    v2, _ = SO.solid_mesh({**m, "res": {"tol": 0.05}})
    assert len(v0) == 128 and len(v1) == 256 and len(v2) > len(v1) and closed_ok(v1, f1)
    assert SO.solid_problem(sp(**m, res={"seg": 64})) is None and "res" in SO.solid_problem(sp(**m, res={"seg": 7}))
    assert SO.solid_problem(sp(**BOX, res={"seg": 64})) is not None          # 원이 없는 형태에는 쓰지 않는다


def test_회전체는_닫힌_다면체이고_부피가_해석값과_같다():
    v, f = SO.solid_mesh(CYL)
    assert closed_ok(v, f) and len(f) == 34                                  # 옆 32 + 원판 2(부채 삼각형이 아니다)
    assert SX.signed_volume(v, f) == pytest.approx(ngon_area(500, 32) * 1000, rel=1e-12)
    q = {**CYL, "angle": 90}
    v, f = SO.solid_mesh(q)
    assert closed_ok(v, f) and SX.signed_volume(v, f) == pytest.approx(ngon_area(500, 32) * 1000 / 4, rel=1e-12)
    ring = {**CYL, "profile": [[300, 0], [500, 0], [500, 1000], [300, 1000]], "res": {"seg": 48}}   # 고리(원통 관): 원판 대신 구멍 있는 면
    v, f = SO.solid_mesh(ring)
    assert closed_ok(v, f) and any(len(fc) == 2 for fc in f)
    assert SX.signed_volume(v, f) == pytest.approx((ngon_area(500, 48) - ngon_area(300, 48)) * 1000, rel=1e-12)
    cone = {**CYL, "profile": [[0, 0], [400, 0], [0, 600]], "axis": [1, 0, 0], "origin": [100, 200, 300], "ref": [0, 1, 0]}   # 눕힌 원뿔
    v, f = SO.solid_mesh(cone)
    assert closed_ok(v, f) and SX.signed_volume(v, f) == pytest.approx(ngon_area(400, 32) * 600 / 3, rel=1e-9)
    xs = [p[0] for p in v]
    assert min(xs) == pytest.approx(100) and max(xs) == pytest.approx(700)
    torus = {**CYL, "profile": [[400, 0], [600, 0]], "bulges": [1, 1], "res": {"tol": 0.1}}          # 볼록도 반원 둘 = 원 단면
    assert SO.solid_problem(sp(**torus)) is None
    v, f = SO.solid_mesh(torus)
    assert closed_ok(v, f) and SX.signed_volume(v, f) == pytest.approx(2 * 3.141592653589793 ** 2 * 500 * 100 ** 2, rel=2e-3)
    a, b = SO.solid_mesh(torus), SO.solid_mesh(dict(torus))
    assert a == b                                                            # 결정적


@pytest.mark.parametrize("bad,frag", [
    ({**CYL, "profile": [[-10, 0], [500, 0], [500, 100]]}, "r 은 0 이상"),
    ({**CYL, "profile": [[0, 0], [100, 0], [100, 100], [0, 100]], "bulges": [0, 0, 0, 1]}, "축을 넘는다"),
    ({**CYL, "angle": 0}, "angle"),
    ({**CYL, "ref": [0, 0, 5]}, "나란하지"),
    ({**CYL, "axis": [0, 0, 0]}, "0 벡터"),
    ({**CYL, "bulges": [0, 0]}, "bulges"),
    ({**PIPE, "profile": [[0, 0], [10, 0], [0, 10]]}, "하나만"),
    ({**PIPE, "via": [[1000, 0, 0], None, None]}, "한 직선 위"),
    ({**PIPE, "via": [None, None, None]}, "via 에 호가"),
    ({**PIPE, "rail": [[0, 0, 0], [0, 0, 0.5]], "via": None}, "1mm"),
    ({**PIPE, "rail": [[0, 0, 0], [1000, 0, 0], [0, 10, 0]], "via": None}, "140도"),
    ({**PIPE, "rail": [[0, 0, 0], [100, 0, 0], [100, 100, 0]], "via": [[50, -50, 0], None]}, "반지름"),
    ({**BOX, "ops": [{"op": "cut", "solid": CYL}]}, "ops[0]"),
    ({**BOX, "ops": [{"op": "difference", "solid": {**CYL, "name": "a"}}]}, "name·category"),
    ({**BOX, "ops": [{"op": "intersection", "solid": {**CYL, "origin": [0, 0, 5000]}}]}, "비었다"),
])
def test_잘못된_형상은_이유와_함께_거절한다(bad, frag):
    q = {k: v for k, v in bad.items() if v is not None}
    why = SO.solid_problem(sp(**q))
    assert why and frag in why, why


def test_3D_스윕은_직선_호_오르막_경로를_따라_닫힌_다면체다():
    v, f = SO.solid_mesh(PIPE)
    assert closed_ok(v, f) and all(len(fc) == 2 for fc in f[-2:])          # 양 끝 마구리는 구멍 있는 고리
    pts, rmin = SX.rail_points(PIPE)
    assert rmin == pytest.approx(1000, rel=1e-6) and len(pts) == 4 + 7       # 90도 호 = 32/4 = 8 현
    ln = sum(SX._len(SX._sub(pts[i + 1], pts[i])) for i in range(len(pts) - 1))
    vol = SX.signed_volume(v, f)
    assert vol == pytest.approx((ngon_area(50, 32) - ngon_area(40, 32)) * ln, rel=2e-3)   # 꺾임(마이터)에서 조금 다르다
    assert max(p[2] for p in v) == pytest.approx(2000)
    ring = {"form": "sweep3d", "rail": [[0, 0, 0], [2000, 0, 500], [2000, 2000, 1000], [0, 2000, 500], [0, 0, 0]], "profile": [[-50, -50], [50, -50], [50, 50], [-50, 50]]}
    assert SO.solid_problem(sp(**ring)) is None                              # 닫힌 비평면 고리: 비틀림을 구간마다 나눠 되돌린다
    v, f = SO.solid_mesh(ring)
    assert closed_ok(v, f) and SX.signed_volume(v, f) > 0
    bent = {"form": "sweep3d", "rail": [[0, 0, 0], [1000, 0, 0], [1000, 0, 1000]], "profile": [[0, 0], [60, 0]], "bulges": [1, 1], "res": {"seg": 16}}
    v, f = SO.solid_mesh(bent)
    assert closed_ok(v, f) and SX.signed_volume(v, f) > 0


def test_불리언은_닫힌_다면체이고_부피가_정확하다():
    hole = {**CYL, "profile": [[0, -100], [300, -100], [300, 700], [0, 700]]}
    d = {**BOX, "ops": [{"op": "difference", "solid": hole}]}
    v, f = SO.solid_mesh(d)
    assert closed_ok(v, f) and len(f) == 38                                  # 위·아래(구멍 있는 면) 2 + 옆 4 + 구멍 벽 32: 같은 평면 조각이 합쳐진다
    assert sorted(len(fc) for fc in f)[-2:] == [2, 2]
    assert SX.signed_volume(v, f) == pytest.approx(2000 * 2000 * 500 - ngon_area(300, 32) * 500, rel=1e-12)
    edges, _ = SO.feature_edges(v, f)
    assert all(len(e[2]) == 2 for e in edges)                                 # T 이음이 없어 모든 모서리가 두 면 사이
    small = {"form": "prism", "polygon": [[500, 500], [1500, 500], [1500, 1500], [500, 1500]], "z0": 250, "z1": 800}
    u = SO.solid_mesh({**BOX, "ops": [{"op": "union", "solid": small}]})
    assert closed_ok(*u) and SX.signed_volume(*u) == pytest.approx(2000 * 2000 * 500 + 1000 * 1000 * 550 - 500 * 500 * 250)
    i = SO.solid_mesh({**BOX, "ops": [{"op": "intersection", "solid": small}]})
    assert closed_ok(*i) and SX.signed_volume(*i) == pytest.approx(500 * 500 * 250) and len(i[1]) == 6
    flush = SO.solid_mesh({**BOX, "ops": [{"op": "difference", "solid": {**small, "z0": 0, "z1": 500}}]})   # 면이 겹치는(같은 평면) 빼기
    assert closed_ok(*flush) and SX.signed_volume(*flush) == pytest.approx(2000 * 2000 * 500 - 500 * 500 * 500)
    chain = {**BOX, "ops": [{"op": "difference", "solid": hole}, {"op": "union", "solid": small},
                           {"op": "difference", "solid": {"form": "member", "start": [-1200, 0, 250], "end": [1200, 0, 250], "section": {"shape": "circle", "width": 100, "depth": 100}, "anchor": "center"}}]}
    v, f = SO.solid_mesh(chain)
    assert closed_ok(v, f) and SO.solid_mesh(chain) == (v, f)
    t = SO.csg_tree(chain)
    assert [o for o, _ in t[2]] == ["difference", "union", "difference"] and t[2][0][1][2] == ()


# ---------------------------------------------------------------- 커맨드
def test_만들기_고치기_불리언_명령과_실행_취소(base):
    h0 = base.eng.state_hash()
    ok(base.run("solid.create", {"id": F.SO1, "level": F.LV1, "category": "proxy", **BOX}))
    ok(base.run("solid.create", {"id": I2, "level": F.LV1, "category": "proxy", "form": "revolve", "profile": [[0, -100], [300, -100], [300, 700], [0, 700]], "origin": [0, 0, 0]}))
    p = ok(base.run("element.get", {"id": I2}))["params"]
    assert p["axis"] == [0.0, 0.0, 1.0] and p["angle"] == 360.0 and p["ref"] == [1.0, 0.0, 0.0]   # 기본값
    ok(base.run("solid.set", {"id": I2, "res": {"seg": 48}}))
    assert ok(base.run("element.get", {"id": I2}))["params"]["res"] == {"seg": 48}
    ok(base.run("solid.set", {"id": I2, "res": {}}))                                                 # 빈 객체 = 기본값
    assert "res" not in ok(base.run("element.get", {"id": I2}))["params"]
    assert err(base.run("solid.set", {"id": I2, "via": [None]}))["path"] == "/via"
    assert err(base.run("solid.create", {"level": F.LV1, **PIPE, "res": {"seg": 8, "tol": 1}}))["code"] == "SCHEMA_INVALID"
    r = ok(base.run("solid.boolean", {"id": F.SO1, "tools": [I2]}))                                    # 도구 요소를 흡수하고 지운다
    assert r == {"id": F.SO1, "ops": 1, "deleted": [I2]}
    assert err(base.run("element.get", {"id": I2}))["code"] == "NOT_FOUND"
    p = ok(base.run("element.get", {"id": F.SO1}))["params"]
    assert p["ops"][0]["op"] == "difference" and p["ops"][0]["solid"]["form"] == "revolve" and "name" not in p["ops"][0]["solid"]
    ok(base.run("solid.create", {"id": I3, "level": F.LV2, "category": "proxy", "form": "prism", "polygon": [[500, 500], [1500, 500], [1500, 1500], [500, 1500]], "z0": -3300, "z1": -2800}))
    ok(base.run("solid.boolean", {"id": F.SO1, "op": "union", "tools": [I3], "keep": True}))           # 다른 레벨 도구: 레벨 높이 차만큼 옮겨 넣는다
    assert ok(base.run("element.get", {"id": I3}))["kind"] == "solid"
    p = ok(base.run("element.get", {"id": F.SO1}))["params"]
    assert p["ops"][1]["solid"]["z0"] == 0.0 and p["ops"][1]["solid"]["z1"] == 500.0
    e = err(base.run("solid.boolean", {"id": F.SO1, "op": "intersection", "shape": {**CYL, "origin": [0, 0, 9000]}}))
    assert e["code"] == "CONSTRAINT" and "비었다" in e["message"]
    assert err(base.run("solid.boolean", {"id": F.SO1, "tools": [F.SO1]}))["code"] == "CONSTRAINT"
    assert err(base.run("solid.boolean", {"id": F.SO1}))["code"] == "SCHEMA_INVALID"
    assert err(base.run("solid.boolean", {"id": F.SO1, "shape": {"z0": 1}}))["code"] == "SCHEMA_INVALID"
    ok(base.run("solid.boolean", {"id": F.SO1, "shape": {"form": "member", "start": [-1200, 0, 250], "end": [1200, 0, 250], "section": {"shape": "circle", "width": 100, "depth": 100}, "anchor": "center", "res": {"tol": 0.5}}}))
    ok(base.run("solid.move", {"id": F.SO1, "dx": 100, "dz": 50}))                                    # 피연산자도 함께 옮긴다
    p = ok(base.run("element.get", {"id": F.SO1}))["params"]
    assert p["ops"][0]["solid"]["origin"] == [100.0, 0.0, 50.0] and p["ops"][2]["solid"]["start"] == [-1100.0, 0.0, 300.0]
    ok(base.run("solid.set", {"id": F.SO1, "z1": 600}))                                               # 형상을 고쳐도 불리언은 남는다
    assert len(ok(base.run("element.get", {"id": F.SO1}))["params"]["ops"]) == 3
    assert ok(base.run("solid.boolean.remove", {"id": F.SO1, "index": 1}))["ops"] == 2
    assert err(base.run("solid.boolean.remove", {"id": F.SO1, "index": 5}))["path"] == "/index"
    assert ok(base.run("solid.boolean.remove", {"id": F.SO1}))["ops"] == 0
    assert "ops" not in ok(base.run("element.get", {"id": F.SO1}))["params"]
    assert err(base.run("solid.boolean.remove", {"id": F.SO1}))["code"] == "CONSTRAINT"
    n = 0
    while base.eng.state_hash() != h0 and n < 20:
        ok(base.run("history.undo", {}))
        n += 1
    assert base.eng.state_hash() == h0 and n == 12                                                  # 실패한 명령은 기록하지 않는다
    for _ in range(5):
        ok(base.run("history.redo", {}))
    assert ok(base.run("element.get", {"id": F.SO1}))["params"]["ops"][0]["solid"]["form"] == "revolve"


def test_복사_회전_대칭은_새_형태와_불리언을_옮긴다(base):
    hop = {"id": F.SO1, "level": F.LV1, "category": "proxy", "form": "revolve", "profile": [[0, 0], [400, 0], [400, 600], [0, 600]], "origin": [1000, 0, 0], "angle": 120}
    ok(base.run("solid.create", hop))
    ok(base.run("solid.boolean", {"id": F.SO1, "shape": {"form": "prism", "polygon": [[1100, -50], [1300, -50], [1300, 50], [1100, 50]], "z0": -10, "z1": 700}}))
    ok(base.run("solid.create", {"id": I2, "level": F.LV1, "category": "pipe", **PIPE}))
    ok(base.run("solid.create", {"id": I3, "level": F.LV1, "category": "proxy", "form": "sweep3d", "rail": [[0, 0, 0], [1000, 0, 0], [1000, 800, 300]],
                                 "via": [None, [1400, 400, 150]], "profile": [[0, 0], [80, 0], [80, 40], [0, 60]], "bulges": [0, 0.3, 0, -0.2]}))

    def meshes():
        g = base.eng.g
        base.eng.sync()
        out = {}
        for e in (F.SO1, I2, I3):
            v, f = SO.solid_mesh(ok(base.run("element.get", {"id": e}))["params"])
            out[e] = (SX.signed_volume(v, f), SO.mesh_bbox(v))
        return g, out
    _, m0 = meshes()
    ok(base.run("element.rotate", {"ids": [F.SO1, I2, I3], "center": [0, 0], "angle": 90}))
    _, m1 = meshes()
    for e in m0:
        assert m1[e][0] == pytest.approx(m0[e][0], rel=1e-6)
        x0, y0, z0, x1, y1, z1 = m0[e][1]
        assert m1[e][1] == pytest.approx((-y1, x0, z0, -y0, x1, z1), abs=0.05)
    ok(base.run("history.undo", {}))
    ok(base.run("element.mirror", {"ids": [F.SO1, I2, I3], "axis": [0, -1, 1, -1]}))                # x 축과 나란한 y = -1 대칭
    _, m2 = meshes()
    for e in m0:
        assert m2[e][0] == pytest.approx(m0[e][0], rel=1e-6)
        x0, y0, z0, x1, y1, z1 = m0[e][1]
        assert m2[e][1] == pytest.approx((x0, -2 - y1, z0, x1, -2 - y0, z1), abs=0.05)
    r = ok(base.run("element.copy", {"ids": [F.SO1], "dx": 0, "dy": 5000}))
    assert r


# ---------------------------------------------------------------- 도면
def test_도면_평면_단면_입면과_증분이_전체와_같다(base):
    v1 = mk_view(base, name="1F", type="plan", level=F.LV1, cut_height=300)
    v2 = mk_view(base, name="S", type="elevation", dir="S")
    v3 = mk_view(base, name="X", type="section", p1=[-3000, 0], p2=[3000, 0])
    before = [lines_of(base, v) for v in (v1, v2, v3)]
    ok(base.run("solid.create", {"id": F.SO1, "level": F.LV1, "category": "proxy", **{**BOX, "polygon": [[-1000, -1000], [1000, -1000], [1000, 1000], [-1000, 1000]]}}))
    ok(base.run("solid.boolean", {"id": F.SO1, "shape": {**CYL, "profile": [[0, -100], [300, -100], [300, 700], [0, 700]]}}))
    cut = [x for x in lines_of(base, v1) if x[0] == "cut"]
    rad = [x for x in cut if abs((x[1] ** 2 + x[2] ** 2) ** 0.5 - 300) < 1e-2]   # 도면 좌표는 소수 3자리
    assert len(rad) == 32                                                      # 구멍 둘레(32현)가 절단선으로
    sec = lines_of(base, v3)
    vert = {round(x[1], 3) for x in sec if x[0] == "cut" and abs(x[1] - x[3]) < 1e-6 and abs(abs(x[4] - x[2]) - 500) < 1e-6}
    assert len(vert) == 4                                                       # 단면: 상자 양 끝 + 구멍 벽 둘(높이 500 절단선)
    ok(base.run("solid.create", {"id": I2, "level": F.LV1, "category": "pipe", **PIPE}))
    ok(base.run("solid.create", {"id": I3, "level": F.LV2, "category": "proxy", "form": "revolve", "profile": [[0, 0], [600, 0], [200, 900], [0, 900]], "origin": [4000, 2000, 0], "res": {"tol": 1}}))
    el = lines_of(base, v2)
    assert any(abs(max(x[2], x[4]) - (3300 + 900)) < 1e-6 for x in el)        # 원뿔 꼭대기(2F + 900)
    assert D._solid_vpieces(base.eng.g, v2, F.SO1) == ()                     # 빼기가 있는 솔리드는 가림 조각을 내지 않는다
    ok(base.run("solid.move", {"id": F.SO1, "dx": 300}))
    ok(base.run("solid.boolean.remove", {"id": F.SO1}))
    assert D._solid_vpieces(base.eng.g, v2, F.SO1) != ()
    ok(base.run("solid.boolean", {"id": F.SO1, "op": "union", "shape": {**CYL, "origin": [0, 0, 500]}}))
    assert D._solid_vpieces(base.eng.g, v2, F.SO1) != ()                      # 합치기만 있으면 원래 형태 조각이 그대로 맞다
    same_as_full(base, (v1, v2, v3))
    for _ in range(7):
        ok(base.run("history.undo", {}))
    assert [lines_of(base, v) for v in (v1, v2, v3)] == before
    same_as_full(base, (v1, v2, v3))


# ---------------------------------------------------------------- IFC
def _volume_occ(f, guid):
    import ifcopenshell.geom
    import ifcopenshell.util.shape as ush
    st = ifcopenshell.geom.settings()
    sh = ifcopenshell.geom.create_shape(st, f.by_guid(guid))
    return ush.get_volume(sh.geometry)


def test_IFC4_IFC2X3_불리언_CSG_와_왕복(tmp_path):
    import ifcopenshell
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        h_plain = b.hashes()
        ok(b.run("solid.create", {"id": X1, "level": F.LV1, "category": "proxy", **BOX}))
        ok(b.run("solid.boolean", {"id": X1, "shape": {**CYL, "profile": [[0, -100], [300, -100], [300, 700], [0, 700]]}}))
        ok(b.run("solid.boolean", {"id": X1, "op": "union", "shape": {"form": "prism", "polygon": [[500, 500], [1500, 500], [1500, 1500], [500, 1500]], "z0": 250, "z1": 800}}))
        ok(b.run("solid.create", {"id": X2, "level": F.LV1, "category": "pipe", **PIPE, "res": {"tol": 0.5}}))
        ok(b.run("solid.create", {"id": X3, "level": F.LV2, "category": "proxy", **CYL, "bulges": [0, 0.2, 0, 0]}))
        h = b.hashes()
        out = b.export("t.ifc")
        f = ifcopenshell.open(str(out))
        e = f.by_guid(X1)
        rep = e.Representation.Representations[0]
        assert rep.RepresentationType == "CSG" and rep.Items[0].is_a("IfcBooleanResult") and rep.Items[0].Operator == "UNION"
        assert rep.Items[0].FirstOperand.is_a("IfcBooleanResult") and rep.Items[0].FirstOperand.Operator == "DIFFERENCE"
        assert rep.Items[0].FirstOperand.FirstOperand.is_a("IfcFacetedBrep")
        assert f.by_guid(X2).is_a("IfcPipeSegment") or f.by_guid(X2).is_a("IfcFlowSegment") or f.by_guid(X2).Representation.Representations[0].RepresentationType == "Brep"
        assert _ifc_errors(f) == []
        # 정확도: IfcOpenShell(OpenCASCADE)이 IFC 의 불리언을 계산한 부피와 코어 BSP 결과 부피가 같다
        mine = SX.signed_volume(*SO.solid_mesh(ok(b.run("element.get", {"id": X1}))["params"]))
        occ = _volume_occ(f, X1)
        assert occ * 1e9 == pytest.approx(mine, rel=1e-6)
        assert b.destructive("ifc.export", {"path": "t2x3.ifc", "schema": "IFC2X3"})["ok"]
        f2 = ifcopenshell.open(str(tmp_path / "t2x3.ifc"))
        assert f2.schema == "IFC2X3" and _ifc_errors(f2) == []
        assert f2.by_guid(X1).Representation.Representations[0].Items[0].is_a("IfcBooleanResult")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        o = b.origins("imp.dobim")
        assert o["foreign"] == 0 and o["detached"] == 0, o
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        assert b.export("t3.ifc", "imp.dobim").read_bytes() == out.read_bytes()
        ok(b.run("solid.boolean.remove", {"id": X1, "index": 1}, project="imp.dobim"))                  # 가져온 프로젝트에서 고치기
        f3 = ifcopenshell.open(str(b.export("t4.ifc", "imp.dobim")))
        assert f3.by_guid(X1).Representation.Representations[0].Items[0].Operator == "DIFFERENCE" and _ifc_errors(f3) == []
        ok(b.run("solid.boolean.remove", {"id": X1}, project="imp.dobim"))
        f4 = ifcopenshell.open(str(b.export("t5.ifc", "imp.dobim")))
        rep4 = f4.by_guid(X1).Representation.Representations[0]
        assert rep4.RepresentationType == "Brep" and rep4.Items[0].is_a("IfcFacetedBrep") and _ifc_errors(f4) == []
        r2 = b.import_ifc("t2x3.ifc", "imp2.dobim")
        assert r2["ok"], r2
        assert ok(b.run("element.get", {"id": X1}, project="imp2.dobim"))["params"]["ops"][1]["op"] == "union"
        for _ in range(5):
            ok(b.run("history.undo", {}))
        assert S.trip(b.hashes()) == S.trip(h_plain)
    finally:
        b.close()


def test_다시_연_프로젝트는_같은_해시(tmp_path):
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        ok(b.run("solid.create", {"id": X1, "level": F.LV1, "category": "proxy", **BOX}))
        ok(b.run("solid.boolean", {"id": X1, "shape": CYL}))
        ok(b.run("solid.create", {"id": X2, "level": F.LV1, "category": "pipe", **PIPE}))
        h = b.hashes()
        b.close()
        b2 = S.Bench(tmp_path)
        assert S.trip(b2.hashes()) == S.trip(h)
        b2.close()
    finally:
        b.close()


# ---------------------------------------------------------------- 예제: 장비 하나(원통 호퍼 + 롤러 + 볼트 구멍 플랜지 + 손잡이)
def test_예제_장비를_새_기능으로_만든다(tmp_path):
    import ifcopenshell
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        v1 = ok(b.run("view.create", {"name": "장비", "type": "plan", "level": F.LV1, "cut_height": 2500}))["id"]
        v2 = ok(b.run("view.create", {"name": "장비 단면", "type": "section", "p1": [0, 2000], "p2": [4000, 2000]}))["id"]

        def cuts(v):
            return ok(b.run("view.linework", {"id": v, "limit": 1}))["summary"]["by_class"].get("cut", 0)
        c0 = (cuts(v1), cuts(v2))
        H = "3" + "0" * 19 + "e1"
        R = "3" + "0" * 19 + "e2"
        P = "3" + "0" * 19 + "e3"
        G = "3" + "0" * 19 + "e4"
        # 호퍼: 원뿔 + 원통 바깥 회전체(가장자리 말림은 볼록도 호)에서 안쪽 회전체를 빼 두께 5mm 껍데기
        ok(b.run("solid.create", {"id": H, "level": F.LV1, "category": "proxy", "name": "호퍼", "form": "revolve", "origin": [2000, 2000, 1500],
                                  "profile": [[0, 0], [150, 0], [600, 800], [600, 1400], [615, 1415], [0, 1415]], "bulges": [0, 0, 0, 0.4142, 0, 0], "res": {"tol": 0.5}}))
        ok(b.run("solid.boolean", {"id": H, "shape": {"form": "revolve", "origin": [2000, 2000, 1500], "profile": [[0, -10], [139.375, -10], [595, 800], [595, 1500], [0, 1500]], "res": {"tol": 0.5}}}))
        # 롤러: 축(지름 40)과 몸통(지름 200)을 단면 하나로, 수평 축
        ok(b.run("solid.create", {"id": R, "level": F.LV1, "category": "proxy", "name": "롤러", "form": "revolve", "origin": [1400, 3000, 900], "axis": [1, 0, 0],
                                  "profile": [[0, -100], [20, -100], [20, 0], [100, 0], [100, 1200], [20, 1200], [20, 1300], [0, 1300]], "res": {"tol": 0.2}}))
        # 플랜지: 판에 볼트 구멍 4개(도구 요소를 만들어 흡수 + 공극 형상)
        ok(b.run("solid.create", {"id": P, "level": F.LV1, "category": "plate", "name": "플랜지", "form": "prism",
                                  "polygon": [[1850, 1850], [2150, 1850], [2150, 2150], [1850, 2150]], "z0": 1480, "z1": 1500}))
        bolts = []
        for k, (x, y) in enumerate(((1880, 1880), (2120, 1880), (2120, 2120))):
            bid = "f" + "0" * 19 + f"b{k}"
            ok(b.run("solid.create", {"id": bid, "level": F.LV1, "category": "proxy", "form": "member", "start": [x, y, 1470], "end": [x, y, 1510],
                                      "section": {"shape": "circle", "width": 14, "depth": 14}, "anchor": "center", "res": {"seg": 16}}))
            bolts.append(bid)
        r = ok(b.run("solid.boolean", {"id": P, "tools": bolts}))
        assert r["ops"] == 3 and r["deleted"] == bolts
        ok(b.run("solid.boolean", {"id": P, "shape": {"form": "member", "start": [1880, 2120, 1470], "end": [1880, 2120, 1510],
                                                    "section": {"shape": "circle", "width": 14, "depth": 14}, "anchor": "center", "res": {"seg": 16}}}))
        ok(b.run("solid.boolean", {"id": P, "shape": {"form": "member", "start": [2000, 2000, 1470], "end": [2000, 2000, 1510],
                                                    "section": {"shape": "circle", "width": 300, "depth": 300}, "anchor": "center"}}))   # 가운데 배출구
        # 손잡이: 호퍼 옆 3D 스윕(직선 - 호 - 직선, 원 단면)
        ok(b.run("solid.create", {"id": G, "level": F.LV1, "category": "railing", "name": "손잡이", "form": "sweep3d",
                                  "rail": [[2600, 1900, 2500], [2700, 1900, 2500], [2700, 2100, 2500], [2600, 2100, 2500]], "via": [None, [2800, 2000, 2500], None],
                                  "section": {"shape": "circle", "width": 25, "depth": 25}}))
        params = {e: ok(b.run("element.get", {"id": e}))["params"] for e in (H, R, P, G)}
        vols = {e: SX.signed_volume(*SO.solid_mesh(p)) for e, p in params.items()}
        for e, p in params.items():
            assert closed_ok(*SO.solid_mesh(p)), e
        pi = 3.141592653589793
        cone = lambda r0, r1, h: pi * h * (r0 * r0 + r0 * r1 + r1 * r1) / 3   # noqa: E731
        shell = cone(150, 600, 800) + pi * 600 ** 2 * 600 - cone(145, 595, 800) - pi * 595 ** 2 * 600
        assert vols[H] == pytest.approx(shell, rel=0.05)                     # 껍데기(가장자리 말림·현 근사 포함)
        outer = SX.signed_volume(*SO.solid_mesh({k: v for k, v in params[H].items() if k != "ops"}))
        inner = SX.signed_volume(*SO.solid_mesh({"form": "revolve", "origin": [2000, 2000, 1500], "axis": [0, 0, 1], "ref": [1, 0, 0], "angle": 360,
                                                 "profile": [[0, 0], [145, 0], [595, 800], [595, 1415], [0, 1415]], "res": {"tol": 0.5}}))
        assert vols[H] == pytest.approx(outer - inner, rel=1e-6)            # 불리언 결과 = 바깥 - (바깥 높이로 자른 안쪽): 현 수가 같아 정확히
        nr = SX.full_segments({"tol": 0.2}, 100)
        assert vols[R] == pytest.approx(ngon_area(100, nr) * 1200 + 2 * ngon_area(20, nr) * 100, rel=1e-9)
        assert vols[R] == pytest.approx(pi * 100 ** 2 * 1200 + 2 * pi * 20 ** 2 * 100, rel=3e-3)   # 현 높이 0.2mm
        assert vols[P] == pytest.approx(300 * 300 * 20 - 4 * ngon_area(7, 16) * 20 - ngon_area(150, 32) * 20, rel=1e-9)
        bb = SO.mesh_bbox(SO.solid_mesh(params[G])[0])
        assert bb[3] == pytest.approx(2812.5, abs=0.5) and bb[2] == pytest.approx(2487.5, abs=1e-6)
        # 도면: 절단 높이 2500 평면에 호퍼 껍데기 두 원(현 80 + 80)과 손잡이 단면, 호퍼 가운데 단면에 껍데기·플랜지·롤러 절단선
        nh = SX.full_segments({"tol": 0.5}, 615)
        assert cuts(v1) - c0[0] >= 2 * nh and cuts(v2) - c0[1] >= 8
        out = b.export("eq.ifc")
        f = ifcopenshell.open(str(out))
        assert _ifc_errors(f) == []
        assert f.by_guid(H).Representation.Representations[0].RepresentationType == "CSG"
        assert f.by_guid(R).Representation.Representations[0].Items[0].is_a("IfcFacetedBrep")
        assert _volume_occ(f, P) * 1e9 == pytest.approx(vols[P], rel=1e-6)
        assert b.destructive("ifc.export", {"path": "eq2x3.ifc", "schema": "IFC2X3"})["ok"]
        assert _ifc_errors(ifcopenshell.open(str(tmp_path / "eq2x3.ifc"))) == []
        h = b.hashes()
        r = b.import_ifc("eq.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
        st = ok(b.run("model.stats", {}))
        assert st
    finally:
        b.close()


def test_GlobalId_은_원래_id_로_열린다(base, tmp_path):
    ok(base.run("solid.create", {"id": F.SO1, "level": F.LV1, "category": "proxy", **BOX}))
    ok(base.run("solid.boolean", {"id": F.SO1, "shape": CYL}))
    base.eng.flush_ifc()
    out = tmp_path / "s.ifc"
    base.eng.ifc.write(str(out))
    f = GI.open_exported(str(out))
    assert f.by_guid(F.SO1).Representation.Representations[0].RepresentationType == "CSG"
    assert ok(base.run("model.validate", {}))


# ---------------------------------------------------------------- 패밀리 레시피: 회전체·임의 축 회전(W2 여섯 번째 PR, FAM-10 일부)
ROLLER = {
    "schema": "dobim.family/1", "id": "equip.roller", "version": 1, "name": "롤러 장치", "description": "합성 시험용: 회전체 몸통·축, 회전체 홈 공극, 기울인 받침",
    "ifc": {"class": "IfcBuildingElementProxy", "type_class": "IfcBuildingElementProxyType", "predefined": "NOTDEFINED"},
    "params": {"dia": {"kind": "type", "default": 200, "min": 100, "max": 400}, "length": {"kind": "type", "default": 1200, "min": 600, "max": 3000},
               "tilt": {"kind": "instance", "default": 30, "unit": "deg", "min": 0, "max": 60}},
    "solids": [
        {"id": "body", "op": "revolve", "axis": "x", "origin": [0, 0, "dia"], "profile": [[0, 0], ["dia/2", 0], ["dia/2", "length"], [0, "length"]]},
        {"id": "shaft", "op": "revolve", "axis": "x", "origin": [0, 0, "dia"], "profile": [[0, -100], [20, -100], [20, "length+100"], [0, "length+100"]]},
        {"id": "groove", "op": "revolve", "axis": "x", "origin": [0, 0, "dia"], "role": "void", "cuts": ["body"],
         "profile": [["dia/2-10", "length/2-20"], ["dia/2+10", "length/2-20"], ["dia/2+10", "length/2+20"], ["dia/2-10", "length/2+20"]]},
        {"id": "arm", "op": "extrude", "plane": "xz", "profile": {"shape": "rect", "u": [-160, -120], "v": [0, "dia"]}, "range": [-60, 60],
         "rotate": {"axis": [1, 0, 0], "angle": "tilt", "origin": [-140, 0, 0]}},
        {"id": "fan", "op": "revolve", "axis": "z", "origin": ["length+200", 0, 0], "angle": 90, "profile": [[0, 0], [150, 0], [150, 40], [0, 40]],
         "rotate": {"axis": [0, 1, 0], "angle": 15}}],
    "plan_symbol": {"default": [{"type": "rect", "u": [0, "length"], "v": ["-dia/2", "dia/2"], "style": "projection"}]}}


def test_패밀리_레시피_회전체와_임의_축_회전(tmp_path):
    import copy

    import ifcopenshell
    from dobim_core.family import evaluate as FE
    from dobim_core.family.validate import validate_recipe
    rep = validate_recipe(copy.deepcopy(ROLLER), "full")
    assert rep["ok"], rep["errors"]
    assert all(v == "pass" for v in rep["checks"].values()), rep["checks"]
    r, plan = FE.load_recipe(copy.deepcopy(ROLLER))
    ev = FE.evaluate(r, plan, {}, {}, None)
    body = {b["id"]: b for b in ev["body"]}
    assert body["body"]["k"] == "revolve" and body["body"]["cuts"][0]["k"] == "revolve" and body["arm"]["rotate"]["angle"] == 30.0
    bad = copy.deepcopy(ROLLER)
    bad["solids"][0]["profile"][0] = [-5, 0]
    rb = validate_recipe(bad, "static")
    assert not rb["ok"] and any(e["code"] == "E_DEGENERATE" for e in rb["errors"])
    bad2 = copy.deepcopy(ROLLER)
    bad2["solids"][3]["rotate"]["axis"] = [0, 0, 0]
    assert any(e["code"] == "E_DEGENERATE" for e in validate_recipe(bad2, "static")["errors"])
    b = S.Bench(tmp_path)
    try:
        S.build_authored(b)
        (tmp_path / "roller.json").write_text(__import__("json").dumps(ROLLER, ensure_ascii=False), encoding="utf-8")
        t = ok(b.run("family.register", {"path": "roller.json", "type_values": {"dia": 250}}))["id"]
        ok(b.run("family.place", {"type": t, "level": F.LV1, "position": [1000, 6000], "values": {"tilt": 45}}))
        out = b.export("fam.ifc")
        f = ifcopenshell.open(str(out))
        assert _ifc_errors(f) == []
        rev = f.by_type("IfcRevolvedAreaSolid")
        assert len(rev) >= 4 and any(abs(x.Angle - 3.141592653589793 / 2) < 1e-9 for x in rev)
        assert b.destructive("ifc.export", {"path": "fam2x3.ifc", "schema": "IFC2X3"})["ok"]
        assert _ifc_errors(ifcopenshell.open(str(tmp_path / "fam2x3.ifc"))) == []
        h = b.hashes()
        r2 = b.import_ifc("fam.ifc", "imp.dobim")
        assert r2["ok"] and not r2["result"]["warnings"], r2
        assert S.trip(b.hashes("imp.dobim")) == S.trip(h)
    finally:
        b.close()


def test_패밀리_회전체_IFC_형상은_해석값과_같다():
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.shape as ush
    from dobim_core.family import ifcproj as fp
    f = ifcopenshell.file(schema="IFC4")
    st = ifcopenshell.geom.settings()
    it = {"k": "revolve", "axis": "x", "origin": [0, 0, 200], "profile": [[0, 0], [100, 0], [100, 1000], [0, 1000]], "angle": 360.0}
    pi = 3.141592653589793
    sh = ifcopenshell.geom.create_shape(st, fp.solid_entity(f, it))
    assert ush.get_volume(sh) == pytest.approx(pi * 100 ** 2 * 1000, rel=1e-4)            # 정확한 회전체(IfcRevolvedAreaSolid, 보는 도구가 삼각화)
    vs = sh.verts
    assert (min(vs[0::3]), max(vs[0::3])) == pytest.approx((0, 1000)) and (min(vs[2::3]), max(vs[2::3])) == pytest.approx((100, 300), abs=0.01)
    sh = ifcopenshell.geom.create_shape(st, fp.solid_entity(f, dict(it, angle=90.0)))
    assert ush.get_volume(sh) == pytest.approx(pi * 100 ** 2 * 1000 / 4, rel=1e-4)        # 각은 라디안으로 쓴다
    sh = ifcopenshell.geom.create_shape(st, fp.solid_entity(f, dict(it, rotate={"axis": [0, 1, 0], "angle": 90.0, "origin": [0, 0, 0]})))
    vs = sh.verts
    assert (min(vs[2::3]), max(vs[2::3])) == pytest.approx((-1000, 0), abs=1e-6)             # y 축 둘레 +90도: x 가 -z 로


def test_3D_스윕_단면_바꾸기와_회전체_축_바꾸기(base):
    ok(base.run("solid.create", {"id": F.SO1, "level": F.LV1, "category": "pipe", **PIPE}))
    ok(base.run("solid.set", {"id": F.SO1, "profile": [[-30, -30], [30, -30], [30, 30], [-30, 30]], "bulges": [0, 0.2, 0, 0.2]}))   # 부재 단면 -> 윤곽
    p = ok(base.run("element.get", {"id": F.SO1}))["params"]
    assert "section" not in p and p["bulges"] == [0.0, 0.2, 0.0, 0.2]
    ok(base.run("solid.set", {"id": F.SO1, "section": {"shape": "circle", "width": 80, "depth": 80}}))                        # 윤곽 -> 부재 단면
    p = ok(base.run("element.get", {"id": F.SO1}))["params"]
    assert "profile" not in p and "bulges" not in p and p["section"]["shape"] == "circle"
    ok(base.run("solid.set", {"id": F.SO1, "via": []}))                                                                        # 빈 목록 = 호를 모두 뗀다
    assert "via" not in ok(base.run("element.get", {"id": F.SO1}))["params"]
    ok(base.run("solid.create", {"id": I2, "level": F.LV1, "category": "proxy", **{k: v for k, v in CYL.items() if k != "ref"}, "angle": 180}))
    ok(base.run("solid.set", {"id": I2, "axis": [1, 0, 0]}))                                                                  # 축만 바꾸면 기준 방향도 다시 정한다
    p = ok(base.run("element.get", {"id": I2}))["params"]
    assert p["axis"] == [1.0, 0.0, 0.0] and p["ref"] == [0.0, 1.0, 0.0] and p["angle"] == 180.0
