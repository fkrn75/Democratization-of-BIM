# SPDX-License-Identifier: MPL-2.0
"""S3 증분 벡터 도면: 뷰 커맨드, 기하 기본 사례, 증분 == 전체 재생성, undo 복원, 음성 대조, 내보내기, 순수성."""
import ast
import sys
from pathlib import Path

import pytest

from conftest import ROOT
from dobim_core import drawing as D
from dobim_core import fixture as F

S3 = ROOT.parent / "s3-drawings"
sys.path.insert(0, str(S3))
import s3lib  # noqa: E402


def _lines(sess, vid):
    sess.eng.sync()
    sess.eng._touch_pending()
    return sess.eng.g.q(D.view_lines, vid)


def _mk(sess, **kw):
    r = sess.run("view.create", kw)
    assert r["ok"], r
    return r["result"]["id"]


def _cut_boxes(lines):
    return sorted(ln for ln in lines if ln[0] == "cut")


# ---------------------------------------------------------------- 커맨드
def test_뷰_생성_검증_수정_삭제_취소(base):
    v = _mk(base, name="1F", type="plan", level=F.LV1)
    assert base.run("view.create", {"name": "1F", "type": "plan", "level": F.LV1})["error"]["code"] == "CONSTRAINT"   # 이름 중복
    assert base.run("view.create", {"name": "x", "type": "plan"})["error"]["code"] == "SCHEMA_INVALID"               # level 필요
    assert base.run("view.create", {"name": "x", "type": "elevation", "dir": "N", "level": F.LV1})["error"]["code"] == "SCHEMA_INVALID"
    assert base.run("view.create", {"name": "x", "type": "section", "p1": [0, 0], "p2": [0, 0]})["error"]["code"] == "SCHEMA_INVALID"
    assert base.run("view.create", {"name": "x", "type": "plan", "level": "9" * 22})["error"]["code"] == "NOT_FOUND"
    assert base.run("view.set", {"id": v, "dir": "N"})["error"]["code"] == "CONSTRAINT"                             # 평면에 방위는 없다
    assert base.run("view.set", {"id": v, "cut_height": 1500})["ok"]
    assert base.eng.st.el[v]["params"]["cut_height"] == 1500
    assert base.run("history.undo", {})["ok"] and base.eng.st.el[v]["params"]["cut_height"] == 1200.0
    assert base.run("view.delete", {"id": v})["ok"] and v not in base.eng.st.el
    assert base.run("history.undo", {})["ok"] and v in base.eng.st.el
    lst = base.run("view.list", {})["result"]["items"]
    assert [i["id"] for i in lst] == [v]


def test_뷰는_IFC_투영과_모델_개수를_바꾸지_않는다(base):
    before = base.eng.hashes()
    stats = base.run("model.stats", {})["result"]
    v = _mk(base, name="P", type="plan", level=F.LV1)
    after = base.eng.hashes()
    assert after["ifc"] == before["ifc"]                                  # IFC 에는 뷰가 없다
    assert after["state"] != before["state"]                              # 의도 저장소에는 있다
    assert base.run("model.stats", {})["result"]["counts"] == stats["counts"]
    assert base.run("pset.set", {"id": v, "pset": "Pset_X", "prop": "p", "value": 1})["error"]["code"] == "CONSTRAINT"
    assert base.run("element.delete", {"id": v})["ok"]
    assert base.eng.intent_consistency() == []


def test_영향_뷰_조회는_의존하는_뷰만_돌려준다(base):
    p1 = _mk(base, name="1F", type="plan", level=F.LV1)
    p2 = _mk(base, name="2F", type="plan", level=F.LV2)
    for v in (p1, p2):
        _lines(base, v)
    r = base.run("wall.move", {"id": F.W1, "dx": 300})
    assert r["ok"]
    stale = {i["id"] for i in base.run("view.affected", {})["result"]["items"]}
    assert stale == {p1}                                                  # 2F 평면은 1F 벽과 무관해 캐시가 그대로다
    dep = {i["id"] for i in base.run("view.affected", {"rev": r["rev"]})["result"]["items"]}
    assert dep == {p1}
    _lines(base, p1)
    assert base.run("view.affected", {})["result"]["items"] == []
    assert base.run("view.affected", {"rev": 9999})["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------- 기하
def test_평면_벽_하나는_사각형_윤곽이고_문이_벽을_둘로_나눈다(sess):
    sess.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    sess.run("wall.create", {"id": F.W1, "level": F.LV1, "axis": [0, 0, 6000, 0], "thickness": 200, "height": 3000})
    v = _mk(sess, name="P", type="plan", level=F.LV1)
    cut = _cut_boxes(_lines(sess, v))
    assert len(cut) == 4 and {round(c) for ln in cut for c in (ln[2], ln[4])} == {-100, 100}
    sess.run("door.place", {"id": F.D1, "host": F.W1, "offset": 3000, "width": 1000, "height": 2100})
    lines = _lines(sess, v)
    xs = sorted({round(x, 1) for ln in _cut_boxes(lines) for x in (ln[1], ln[3])})
    assert xs == [0.0, 2500.0, 3500.0, 6000.0]                            # 개구부 2500..3500 에서 벽이 끊긴다
    assert sum(1 for ln in lines if ln[0] == "symbol") == D.ARC_N + 1     # 문짝 선 1 + 호 8


def test_평면은_절단_높이가_문_위면_벽이_끊기지_않는다(sess):
    sess.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    sess.run("wall.create", {"id": F.W1, "level": F.LV1, "axis": [0, 0, 6000, 0], "thickness": 200, "height": 3000})
    sess.run("door.place", {"id": F.D1, "host": F.W1, "offset": 3000, "width": 1000, "height": 2100})
    v = _mk(sess, name="P", type="plan", level=F.LV1, cut_height=2500)
    xs = sorted({round(x, 1) for ln in _cut_boxes(_lines(sess, v)) for x in (ln[1], ln[3])})
    assert xs == [0.0, 6000.0]


def test_L_조인_모서리는_채워지고_내부_이음매_선이_없다(sess):
    sess.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    sess.run("wall.create", {"id": F.W1, "level": F.LV1, "axis": [0, 0, 6000, 0], "thickness": 200, "height": 3000})
    sess.run("wall.create", {"id": F.W2, "level": F.LV1, "axis": [6000, 0, 6000, 4000], "thickness": 200, "height": 3000})
    sess.run("join.set", {"a": F.W1, "b": F.W2})
    v = _mk(sess, name="P", type="plan", level=F.LV1)
    cut = _cut_boxes(_lines(sess, v))
    pts = {(round(ln[1]), round(ln[2])) for ln in cut} | {(round(ln[3]), round(ln[4])) for ln in cut}
    assert (6100, -100) in pts and (5900, 100) in pts                      # 바깥 모서리가 있고 안쪽 모서리가 있다
    assert len(cut) == 6                                                  # ㄴ 자 윤곽 6변
    for ln in cut:                                                        # 벽 안쪽을 가로지르는 선이 없다
        assert not (ln[1] == ln[3] == 6000 and ln[2] != ln[4])


def _single_wall_with_door(sess):
    sess.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    sess.run("wall.create", {"id": F.W1, "level": F.LV1, "axis": [0, 0, 6000, 0], "thickness": 200, "height": 3000})
    sess.run("door.place", {"id": F.D1, "host": F.W1, "offset": 3000, "width": 1000, "height": 2100})


def test_단면은_문_개구부에서_벽_아래를_비우고_인방만_남긴다(sess):
    _single_wall_with_door(sess)
    v = _mk(sess, name="S", type="section", p1=[3000, -1000], p2=[3000, 1000])    # 문 한가운데(x=3000)를 지나는 선
    lines = _lines(sess, v)
    cut = [ln for ln in lines if ln[0] == "cut"]
    zs = sorted({round(ln[2]) for ln in cut} | {round(ln[4]) for ln in cut})
    assert zs == [2100, 3000]                                                     # 문 높이 위의 인방 부분만 절단된다
    fills = sess.eng.g.q(D.view_fills, v)
    assert len(fills) == 1 and max(p[1] for p in fills[0]) == 3000 and min(p[1] for p in fills[0]) == 2100
    v2 = _mk(sess, name="S2", type="section", p1=[2000, -1000], p2=[2000, 1000])   # 문 옆 (x=2000): 벽 전체
    zs2 = sorted({round(ln[2]) for ln in _lines(sess, v2) if ln[0] == "cut"})
    assert zs2 == [0, 3000]


def test_입면은_가려진_선을_지우고_문틀을_그린다(sess):
    _single_wall_with_door(sess)
    v = _mk(sess, name="E", type="elevation", dir="N")     # 북측 입면: 벽(y=0)을 북쪽 바깥에서 본다
    lines = _lines(sess, v)
    assert all(ln[0] == "proj" for ln in lines)
    xs = sorted({abs(round(ln[1])) for ln in lines} | {abs(round(ln[3])) for ln in lines})
    assert xs == [0, 2500, 3500, 6000]                     # s = -x
    assert any(round(ln[2]) == 2100 == round(ln[4]) for ln in lines)       # 문 위 인방선
    assert not any(round(ln[2]) == 1000 for ln in lines)


def test_입면에서_앞_벽이_뒤_벽의_선을_가린다(sess):
    sess.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    sess.run("wall.create", {"id": F.W1, "level": F.LV1, "axis": [0, 0, 6000, 0], "thickness": 200, "height": 3000})            # 뒤(북)
    sess.run("wall.create", {"id": F.W2, "level": F.LV1, "axis": [1000, -4000, 3000, -4000], "thickness": 200, "height": 2000})  # 앞(남)
    v = _mk(sess, name="E", type="elevation", dir="S")     # 남쪽에서 북쪽을 본다. s = +x
    lines = _lines(sess, v)
    horiz = lambda z: sorted((round(ln[1]), round(ln[3])) for ln in lines if round(ln[2]) == z == round(ln[4]))   # noqa: E731
    assert horiz(2000) == [(1000, 3000)]                   # 앞 벽 윗변은 보인다
    assert horiz(3000) == [(0, 6000)]                      # 뒤 벽 윗변은 앞 벽보다 높아 그대로
    # 뒤 벽의 밑변(z=0)은 앞 벽이 덮은 s=1000..3000 에서 지워지고 앞 벽 밑변이 대신 보인다: 같은 구간이 한 번만 나온다
    assert horiz(0) == [(0, 1000), (1000, 3000), (3000, 6000)] or horiz(0) == [(0, 6000)]
    # 앞 벽 뒤에 숨은 뒤 벽의 세로선은 없다 (뒤 벽 끝 s=0, 6000 만)
    verticals = {round(ln[1]) for ln in lines if round(ln[1]) == round(ln[3])}
    assert verticals == {0, 1000, 3000, 6000}


# ---------------------------------------------------------------- 증분 == 전체 재생성
@pytest.fixture
def ra01(tmp_path):
    sess, ids, views = s3lib.build(tmp_path / "ra01.dobim")
    yield sess, ids, views
    sess.close()


def test_RA01_전_뷰가_만들어지고_증분이_전체_재생성과_같다(ra01):
    sess, ids, views = ra01
    assert len(views) == 7
    for k, v in views.items():
        inc = _lines(sess, v)
        assert len(inc) > 0, k
        assert inc == s3lib.full_lines(sess.eng, v), k


def test_벽_이동_뒤_증분이_전체와_같고_undo_하면_원래_선분으로_돌아간다(ra01):
    sess, ids, views = ra01
    before = {k: _lines(sess, v) for k, v in views.items()}
    walls = [ids["int-1F"][0], ids["ext-1F"][1], ids["int-2F"][1]]
    for w in walls:
        assert sess.run("wall.move", {"id": w, "dx": 400, "dy": 200})["ok"]
        for k, v in views.items():
            assert _lines(sess, v) == s3lib.full_lines(sess.eng, v), (w, k)
        assert sess.run("history.undo", {})["ok"]
        for k, v in views.items():
            assert _lines(sess, v) == before[k], (w, k)
    # 1F 벽 이동은 2F 평면 캐시를 건드리지 않는다
    _lines(sess, views["plan-2F"])
    sess.run("wall.move", {"id": ids["int-1F"][0], "dx": 100})
    state = __import__("dobim_core.drawing", fromlist=["x"]).view_cache_state(sess.eng.g, list(views.values()))
    assert state[views["plan-2F"]] == "fresh" and state[views["plan-1F"]] == "stale"


@pytest.mark.parametrize("seed", [31, 32])
def test_임의_편집열에서_모든_뷰의_증분이_전체_재생성과_같다(tmp_path, seed):
    r = s3lib.fuzz_views(tmp_path, seed, 70)
    assert r["error_count"] == 0, r["errors"]
    assert r["diff_count"] == 0, r["diffs"]
    assert r["checks"] > 100


def test_음성_대조_무효화를_빠뜨린_변이를_하네스가_잡는다(tmp_path):
    from unittest import mock
    import run_s3
    with mock.patch.object(D, "wall_solid", lambda g, w, _o=D.wall_solid: _o(run_s3._Untracked(g), w)):
        r = s3lib.fuzz_views(tmp_path, 33, 70)
    assert r["diff_count"] + r["error_count"] > 0


def test_선분_정규화와_일치율_정의():
    a = [("cut", 0.0, 0.0, 10.0, 0.0), ("cut", 0.0, 5.0, 10.0, 5.0)]
    assert s3lib.match(a, list(reversed([("cut", 10.0, 0.0, 0.0, 0.0), ("cut", 0.0, 5.0, 10.0, 5.0)])))["ratio"] == 1.0
    assert s3lib.match(a, [("cut", 0.0, 0.0, 10.0, 0.0), ("cut", 0.0, 5.5, 10.0, 5.5)])["ratio"] == 0.5
    assert s3lib.match(a, [("proj", 0.0, 0.0, 10.0, 0.0), ("cut", 0.0, 5.0, 10.0, 5.0)])["ratio"] == 0.5   # 등급이 다르면 다른 선분


# ---------------------------------------------------------------- 내보내기
def test_내보내기는_파일에_쓰고_응답에는_요약만_담는다(ra01):
    sess, ids, views = ra01
    ws = sess.path.parent
    r = sess.run("view.export", {"id": views["plan-1F"], "path": "a.svg"})
    assert r["ok"] and r["result"]["segments"] > 10 and "x0" not in str(r["result"])
    svg = (ws / "a.svg").read_text(encoding="utf-8")
    assert "<line" in svg and "평면도" in svg and "S=1:100" in svg                    # 한글 텍스트 유지
    assert sess.run("view.export", {"id": views["plan-1F"], "path": "a.svg"})["error"]["code"] == "FILE_EXISTS"
    assert sess.run("view.export", {"id": views["plan-1F"], "path": "a.svg", "overwrite": True})["ok"]
    assert sess.run("view.export", {"id": views["plan-1F"], "path": "../x.svg"})["error"]["code"] == "PATH_DENIED"
    assert sess.run("view.export", {"id": views["plan-1F"], "path": "a.txt"})["error"]["code"] == "PATH_DENIED"
    assert sess.run("view.export", {"id": views["plan-1F"], "path": "t.dobim"})["error"]["code"] == "PATH_DENIED"
    r = sess.run("view.export", {"id": views["section-BB"], "path": "b.pdf"})
    assert r["ok"] and r["result"]["non_ascii_dropped"] is True                       # PDF 는 한글을 쓰지 못한다
    pdf = (ws / "b.pdf").read_bytes()
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF") and b" re " not in pdf and b"/Image" not in pdf   # 벡터
    start = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
    assert pdf[start:start + 4] == b"xref"


def test_선분_조회는_응답_상한을_지키고_이어받을_수_있다(ra01):
    sess, ids, views = ra01
    v = views["elev-S"]
    seen, cursor = [], None
    for _ in range(20):
        r = sess.run("view.linework", {"id": v, "limit": 5, **({"cursor": cursor} if cursor else {})})
        assert r["ok"]
        seen += r["result"]["items"]
        cursor = (r["page"] or {}).get("next_cursor")
        if not cursor:
            break
    assert len(seen) == r["result"]["summary"]["segments"] == len(_lines(sess, v))


# ---------------------------------------------------------------- 순수성
def test_도면_모듈은_순수하다_시각_난수_파일_libm_없음():
    src = (ROOT / "src" / "dobim_core" / "drawing.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed = {"__future__", "hashlib", "json", "math", "dobim_core"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] not in allowed]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in allowed:
                bad.append(node.module)
            if node.module == "math":
                bad += [a.name for a in node.names if a.name != "sqrt"]       # sqrt 만 (IEEE 754 로 결정적)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("open", "print", "input", "exec", "eval"):
            bad.append(node.func.id)
    assert not bad, bad
