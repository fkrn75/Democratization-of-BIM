# SPDX-License-Identifier: MPL-2.0
"""시트 확장(제도·관행·BIM 지침 층): 표제란 건축주·서명·개정 구름, 통심선, 단면·입면 기호, 부위 상세, 부호 태그, 키 플랜,
색인 시트(도면목록·범례·일람표), 도면 작성기준 프로파일. 기능 입력이 없으면 기존 시트가 바이트까지 같다는 것도 고정한다."""
import hashlib
import re

import pytest

from dobim_core import fixture as F
from dobim_core import sheet_model as SM

pytestmark = pytest.mark.filterwarnings("ignore")

SPEC = {"project_name": "시험 주택", "sheet_name": "도면", "sheet_number": "A-101", "date": "2026-11-15"}
V3 = "4" + "0" * 20 + "3"      # 입면 뷰 id

# 이 단계 이전(c35a797) 코드로 같은 입력을 내보낸 파일의 sha256 (새 기능 입력이 없으면 시트가 바뀌지 않는다는 고정값)
GOLDEN = {"svg": "318ec933e827ab8a091a01471729a246df621d7616d6697665fa054b42d773b7",
          "pdf": "0ada830946e645d2130e1564aad978cefe771b0e46937550c8570fd6c8ae5d77"}


def run_export(sess, cmd, args):
    r = sess.run(cmd, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    return sess.run(cmd, args, confirm=r["error"]["details"]["confirm_token"])


def read(sess, name):
    return (sess.path.parent / name).read_bytes()


@pytest.fixture
def pv(base):
    assert base.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1})["ok"]
    assert base.run("view.create", {"id": F.V2, "name": "B-B 단면", "type": "section", "p1": [-1000, 2000], "p2": [7000, 2000]})["ok"]
    assert base.run("view.create", {"id": V3, "name": "남측 입면", "type": "elevation", "dir": "S"})["ok"]
    return base


@pytest.fixture
def rich(pv):
    """공간 2개와 창 1개가 있는 모델."""
    assert pv.run("space.create", {"id": F.SP1, "level": F.LV1, "boundary": [[100, 100], [2900, 100], [2900, 3900], [100, 3900]], "name": "거실", "category": "거실"})["ok"]
    assert pv.run("space.create", {"id": "8" + "0" * 20 + "2", "level": F.LV1, "boundary": [[3100, 100], [5900, 100], [5900, 3900], [3100, 3900]], "name": "침실", "category": "침실"})["ok"]
    assert pv.run("family.place", {"id": "0" * 20 + "w1", "family": "window.fixed", "host": F.W2, "offset": 2000.0})["ok"]
    return pv


# ---------------------------------------------------------------- 바이트 불변
@pytest.mark.parametrize("ext", ["svg", "pdf"])
def test_새_입력이_없으면_시트가_이전과_바이트까지_같다(pv, ext):
    args = {"views": [F.V1, F.V2], "path": "g." + ext, "paper": "A3", "project_name": "시험", "sheet_name": "골든", "sheet_number": "A-101", "date": "2026-11-15"}
    assert run_export(pv, "sheet.export", args)["ok"]
    assert hashlib.sha256(read(pv, "g." + ext)).hexdigest() == GOLDEN[ext]


# ---------------------------------------------------------------- 표제란 확장
def test_건축주_설계사무소_서명_개정차수(pv):
    r = run_export(pv, "sheet.export", {"views": [F.V1], "path": "s.svg", "client": "홍길동", "office": "도비엠 건축",
                                        "signers": [{"role": "설계자", "name": "김설계"}, {"role": "관계전문기술자", "name": ""}],
                                        "revisions": [{"rev": "1", "date": "2026-11-10", "desc": "창 위치 변경"}, {"rev": "2", "date": "2026-11-15", "desc": "방화문 추가"}],
                                        **SPEC})
    assert r["ok"], r
    s = read(pv, "s.svg").decode("utf-8")
    for gid in ("client-office", "signature-block", "revision-table"):
        assert f'id="{gid}"' in s, gid
    for text in (">홍길동<", ">도비엠 건축<", ">김설계<", ">(서명)<", ">(인)<", "개정차수", ">창 위치 변경<", ">방화문 추가<", ">2026-11-10<"):
        assert text in s, text
    assert 'id="signature-block" data-count="2"' in s


def test_개정_구름은_뷰를_둘러싼_닫힌_선이다(pv):
    r = run_export(pv, "sheet.export", {"views": [F.V1], "path": "c.svg", "revisions": [{"rev": "A", "date": "2026-11-15", "desc": "구름", "view": F.V1}], **SPEC})
    assert r["ok"], r
    s = read(pv, "c.svg").decode("utf-8")
    m = re.search(r'<g id="revision-cloud" data-rev="A" data-view="[^"]+">\n<polygon points="([^"]+)" fill="none" stroke="#c62828"', s)
    assert m, "구름 다각형이 없다"
    assert len(m.group(1).split()) > 40                       # 호를 이은 점이 많다


def test_개정_구름의_뷰는_시트에_있어야_한다(pv):
    r = run_export(pv, "sheet.export", {"views": [F.V1], "path": "c.svg", "revisions": [{"rev": "A", "date": "2026-11-15", "desc": "x", "view": F.V2}], **SPEC})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"


def test_도면번호_체계와_파일명_검사(pv):
    r = run_export(pv, "sheet.export", {"views": [F.V1], "path": "A-101.svg", **SPEC})
    assert r["result"]["number_check"] == {"format_ok": True, "file_match": True, "discipline": "건축"}
    r = run_export(pv, "sheet.export", {"views": [F.V1], "path": "x.svg", **{**SPEC, "sheet_number": "도면1"}})
    assert r["result"]["number_check"]["format_ok"] is False and r["result"]["number_check"]["file_match"] is False


def test_선_굵기_프로파일이_절단선_굵기를_바꾼다(pv):
    a = {"views": [F.V1], "path": "p0.svg", **SPEC}
    run_export(pv, "sheet.export", a)
    run_export(pv, "sheet.export", {**a, "path": "p1.svg", "line_profile": {"cut": 0.65}})
    s0, s1 = read(pv, "p0.svg").decode(), read(pv, "p1.svg").decode()
    assert 'stroke-width="0.65"' not in s0 and 'stroke-width="0.65"' in s1
    assert "0.5" in s0                                          # 기본 절단선 굵기


# ---------------------------------------------------------------- 뷰 위 기호
def test_통심선_단면기호_입면기호_상세표시_키플랜(rich):
    r = run_export(rich, "sheet.export", {
        "views": [F.V1, F.V2, V3], "path": "x.svg", "paper": "A1", **SPEC,
        "grids": [{"label": "X1", "axis": "x", "pos": 0}, {"label": "X2", "axis": "x", "pos": 6000}, {"label": "Y1", "axis": "y", "pos": 0}],
        "marks": True, "callouts": [{"view": F.V1, "rect": [0, 0, 2000, 2000], "label": "1"}],
        "tags": ["room", "opening", "wall"], "key_plan": F.V1, "key_zone": [0, 0, 3000, 4000]})
    assert r["ok"], r
    s = read(rich, "x.svg").decode("utf-8")
    for gid in ("grid-lines", "section-markers", "elevation-markers", "detail-callouts", "room-numbers", "opening-tags", "wall-tags", "key-plan"):
        assert f'id="{gid}"' in s, gid
    assert 'id="grid-lines" data-count="3"' in s and 'data-zone="0,0,3000,4000"' in s
    for t in (">X1<", ">Y1<", ">B-B<", ">101<", ">102<", ">W1<", ">WT1<", ">키 플랜<"):
        assert t in s, t


def test_키플랜_통심선은_평면_뷰에만_그린다(pv):
    r = run_export(pv, "sheet.export", {"views": [F.V2], "path": "n.svg", **SPEC, "grids": [{"label": "X1", "axis": "x", "pos": 0}], "marks": True, "tags": ["room"]})
    assert r["ok"], r
    s = read(pv, "n.svg").decode("utf-8")
    assert 'id="grid-lines"' not in s and 'id="section-markers"' not in s and 'id="room-numbers"' not in s


def test_확장을_모두_쓴_시트는_결정적이다(rich):
    a = {"views": [F.V1, F.V2, V3], "paper": "A1", **SPEC, "client": "갑", "office": "을", "signers": [{"role": "설계자", "name": "병"}],
         "revisions": [{"rev": "1", "date": "2026-11-15", "desc": "x", "view": F.V1}], "grids": [{"label": "X1", "axis": "x", "pos": 0}],
         "marks": True, "tags": ["room", "opening", "wall"], "key_plan": F.V1}
    run_export(rich, "sheet.export", {**a, "path": "d1.svg"})
    run_export(rich, "sheet.export", {**a, "path": "d2.svg"})
    run_export(rich, "sheet.export", {**a, "path": "d1.pdf"})
    run_export(rich, "sheet.export", {**a, "path": "d2.pdf"})
    assert read(rich, "d1.svg") == read(rich, "d2.svg") and read(rich, "d1.pdf") == read(rich, "d2.pdf")


def test_A3에_확장을_다_넣어도_들어간다(rich):
    r = run_export(rich, "sheet.export", {"views": [F.V1], "path": "a3.svg", "paper": "A3", **SPEC, "client": "갑", "office": "을", "signers": [{"role": "설계자", "name": "병"}],
                                          "revisions": [{"rev": "1", "date": "2026-11-15", "desc": "x"}], "key_plan": F.V1})
    assert r["ok"], r
    assert 'id="revision-table"' in read(rich, "a3.svg").decode("utf-8")


# ---------------------------------------------------------------- 부호·실번호
def test_실번호는_좌상단부터_시계방향이다(rich):
    g = rich.eng.g
    rich.eng.sync()
    tags = SM.room_numbers(rich.eng.g, F.V1, 100.0)
    # 두 공간: 거실(왼쪽), 침실(오른쪽). 좌상단에서 시계방향이면 왼쪽(거실)이 101, 오른쪽이 102
    assert [t[2] for t in tags] == ["101", "102"] and tags[0][0] < tags[1][0]


def test_부호는_속성_묶음마다_하나이고_결정적이다(rich):
    rich.eng.sync()
    g = rich.eng.g
    ops = SM.openings(g)
    marks = SM.opening_marks(ops)
    assert set(marks.values()) >= {"D1", "W1"} or set(marks.values()) >= {"W1"}
    assert len(set(marks.values())) == len(marks)
    assert SM.opening_marks(SM.openings(g)) == marks
    wm = SM.wall_marks(SM.walls(g))
    assert sorted(wm.values())[0] == "WT1"


# ---------------------------------------------------------------- 색인 시트
def test_색인_시트_표_전부(rich):
    r = run_export(rich, "sheet.index", {"path": "i.svg", "paper": "A1", "project_name": "시험", "sheet_number": "A-000", "date": "2026-11-15",
                                         "drawings": [{"number": "A-101", "title": "1층 평면도", "scale": "1:100"}, {"number": "A-201", "title": "입면도", "scale": "1:100"}],
                                         "client": "갑", "office": "을"})
    assert r["ok"], r
    assert r["result"]["rows"]["drawing_list"] == 2 and r["result"]["number_check"]["format_ok"]
    s = read(rich, "i.svg").decode("utf-8")
    for gid in ("drawing-list", "legend", "notes", "level-note", "opening-schedule", "wall-schedule", "material-schedule", "title-block"):
        assert f'id="{gid}"' in s, gid
    for t in (">A-101<", ">1층 평면도<", ">건축<", ">범례<", ">주기<", ">1F<", ">±0<", ">W1<", ">WT1<", "해치", "1점쇄선"):
        assert t in s, t
    assert 'id="north-symbol"' not in s and 'id="scale-bar"' not in s     # 뷰가 없으니 방위표·축척 막대는 없다


def test_색인_시트_PDF_한글(rich):
    r = run_export(rich, "sheet.index", {"path": "i.pdf", "paper": "A1", "drawings": [{"number": "A-101", "title": "평면도"}]})
    assert r["ok"], r
    assert read(rich, "i.pdf").startswith(b"%PDF")


def test_색인_시트는_결정적이다(rich):
    a = {"paper": "A1", "drawings": [{"number": "A-101", "title": "평면도"}], "date": "2026-11-15"}
    run_export(rich, "sheet.index", {**a, "path": "a.svg"})
    run_export(rich, "sheet.index", {**a, "path": "b.svg"})
    assert read(rich, "a.svg") == read(rich, "b.svg")


def test_색인_시트_행이_너무_많으면_알린다(rich):
    many = [{"number": f"A-{100 + i}", "title": "도면"} for i in range(60)]
    r = run_export(rich, "sheet.index", {"path": "m.svg", "paper": "A3", "drawings": many, "tables": ["drawing_list"]})
    assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"


def test_색인_시트_GL_기준_레벨(rich):
    run_export(rich, "sheet.index", {"path": "g.svg", "tables": ["levels"], "gl_level": "없는이름"})
    assert "±0" in read(rich, "g.svg").decode("utf-8")


# ---------------------------------------------------------------- 프로파일
def test_도면_작성기준_프로파일은_BIM_지침_기준_항목을_모두_담는다(rich):
    r = rich.run("drawing.profile", {})
    assert r["ok"], r
    p = r["result"]
    for k in ("paper_size", "layout_orientation", "north_level", "title_block", "scale", "line_weights", "hatch", "text_font", "dimension_leader", "symbols", "line_overlap"):
        assert k in p, k
    assert p["line_weights"]["cut"]["width_mm"] == 0.5 and p["line_overlap"]["priority"]["cut"] > p["line_overlap"]["priority"]["proj"]
    assert {s["id"] for s in p["symbols"]} >= {"door", "window", "column", "stair", "fire_wall", "grid", "section_mark"}
    assert rich.run("drawing.profile", {})["result"] == p        # 결정적
