# SPDX-License-Identifier: MPL-2.0
"""1단계 DXF 내보내기(LN-10): 시트·뷰 -> DXF R2013(AC1027, UTF-8).

합성 모델로 DXF 를 만든 뒤 시험 안의 작은 그룹 코드 읽개로 다시 읽어 구조(섹션·핸들·소유자)·층·엔티티 수·좌표·문자·결정성을 확인한다.
DXF 라이브러리를 쓰지 않으므로(코어 의존 없음) 읽개도 시험이 직접 갖는다.
"""
import pytest

from dobim_core import drawing_export as DX
from dobim_core import dxf_export as DXF
from dobim_core import fixture as F
from dobim_core import sheet as SH

pytestmark = pytest.mark.filterwarnings("ignore")

SPEC = {"project_name": "시험 주택", "sheet_name": "1층 평면도", "sheet_number": "A-101", "date": "2026-11-15"}
V3 = "4" + "0" * 20 + "3"      # 남측 입면


# ---------------------------------------------------------------- 읽개
def pairs(blob: bytes):
    lines = blob.decode("utf-8").split("\n")
    assert lines[-1] == ""
    lines = lines[:-1]
    assert len(lines) % 2 == 0
    return [(int(lines[i]), lines[i + 1]) for i in range(0, len(lines), 2)]


def parse(blob: bytes) -> dict:
    """섹션별 객체 목록: {섹션: [[(코드, 값), ...], ...]}, 헤더는 {변수: [값...]}."""
    pr = pairs(blob)
    assert pr[-1] == (0, "EOF")
    out, i = {}, 0
    while pr[i] != (0, "EOF"):
        assert pr[i] == (0, "SECTION"), pr[i]
        name = pr[i + 1][1]
        i += 2
        objs, hdr, cur = [], {}, None
        while pr[i] != (0, "ENDSEC"):
            c, v = pr[i]
            if name == "HEADER":                       # 헤더는 0 코드 없이 9(변수 이름) -> 값들
                if c == 9:
                    cur = v
                    hdr[cur] = []
                else:
                    hdr[cur].append(v)
            elif c == 0:
                objs.append([pr[i]])
            else:
                objs[-1].append(pr[i])
            i += 1
        out[name] = objs
        if name == "HEADER":
            out["_header"] = hdr
        i += 1
    return out


def val(obj, code, default=None):
    for c, v in obj:
        if c == code:
            return v
    return default


def vals(obj, code):
    return [v for c, v in obj if c == code]


def entities(doc):
    return doc["ENTITIES"]


def check_structure(blob: bytes) -> dict:
    """구조 불변식: 섹션 순서, AC1027·mm, 핸들 유일·HANDSEED 미만, 소유자(330) 가 있는 핸들, 엔티티 층이 층 표에 있음, 선 종류·글꼴 스타일 존재."""
    doc = parse(blob)
    assert [k for k in doc if not k.startswith("_")] == ["HEADER", "CLASSES", "TABLES", "BLOCKS", "ENTITIES", "OBJECTS"]
    h = doc["_header"]
    assert h["$ACADVER"] == ["AC1027"] and h["$INSUNITS"] == ["4"] and h["$MEASUREMENT"] == ["1"]
    for bad in ("$TDCREATE", "$TDUPDATE", "$FINGERPRINTGUID", "$VERSIONGUID"):
        assert bad not in h                                     # 날짜·GUID 를 쓰지 않는다(결정적)
    seed = int(h["$HANDSEED"][0], 16)
    allh = []
    for sec in ("TABLES", "BLOCKS", "ENTITIES", "OBJECTS"):
        for o in doc[sec]:
            hv = val(o, 5) or val(o, 105)
            if o[0][1] in ("ENDTAB",):
                continue
            assert hv, o[:3]
            allh.append(hv)
    assert len(allh) == len(set(allh))
    assert max(int(x, 16) for x in allh) < seed
    hs = set(allh)
    for sec in ("TABLES", "BLOCKS", "ENTITIES", "OBJECTS"):
        for o in doc[sec]:
            for c, v in o:
                if c in (330, 340, 350, 390) and v != "0":
                    assert v in hs, (o[0], c, v)
    tbl = doc["TABLES"]
    layers = {val(o, 2) for o in tbl if o[0] == (0, "LAYER")}
    ltypes = {val(o, 2) for o in tbl if o[0] == (0, "LTYPE")}
    styles = {val(o, 2): val(o, 3) for o in tbl if o[0] == (0, "STYLE")}
    assert styles[DXF.STYLE_NAME] == "malgun.ttf" and styles[DXF.STYLE_BOLD] == "malgunbd.ttf"
    for o in tbl:
        if o[0] == (0, "LAYER"):
            assert val(o, 6) in ltypes
    ms = next(val(o, 5) for o in tbl if o[0] == (0, "BLOCK_RECORD") and val(o, 2) == "*Model_Space")
    for e in entities(doc):
        assert val(e, 330) == ms and val(e, 8) in layers, e[:6]
        if val(e, 6):
            assert val(e, 6) in ltypes
        if val(e, 370):
            assert int(val(e, 370)) in DXF.LW_STD
        if e[0] == (0, "TEXT"):
            assert val(e, 7) in styles
    return doc


def by_layer(doc, layer, kind=None):
    return [e for e in entities(doc) if val(e, 8) == layer and (kind is None or e[0][1] == kind)]


def text_of(doc):
    return [val(e, 1) for e in entities(doc) if e[0] == (0, "TEXT")]


# ---------------------------------------------------------------- 커맨드
def run_export(sess, cmd, args):
    r = sess.run(cmd, args)
    assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
    r = sess.run(cmd, args, confirm=r["error"]["details"]["confirm_token"])
    assert r["ok"], r
    return r["result"]


def read(sess, name):
    return (sess.path.parent / name).read_bytes()


@pytest.fixture
def pv(base):
    assert base.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1})["ok"]
    assert base.run("view.create", {"id": V3, "name": "남측 입면", "type": "elevation", "dir": "S"})["ok"]
    return base


def test_시트_DXF_구조_층_표제란_한글(pv):
    r = run_export(pv, "sheet.export", {"views": [F.V1], "path": "s.dxf", **SPEC})
    assert r["format"] == "dxf" and r["dxf"]["version"] == "AC1027" and r["dxf"]["units"] == "mm"
    doc = check_structure(read(pv, "s.dxf"))
    lay = r["dxf"]["layers"]
    for name in ("G-BORDER", "G-TITLE", "G-CAPTION", "A-CUT"):
        assert lay.get(name), (name, lay)
    # 윤곽선: 용지 A3(420x297) 가장자리에서 10mm, y 는 위로 뒤집는다(용지 mm 1:1)
    border = by_layer(doc, "G-BORDER", "LWPOLYLINE")
    assert len(border) == 1
    xs, ys = [float(v) for v in vals(border[0], 10)], [float(v) for v in vals(border[0], 20)]
    assert (min(xs), max(xs), min(ys), max(ys)) == (10.0, 410.0, 10.0, 287.0)
    assert val(border[0], 370) is None                          # 층 굵기(0.70)와 같으면 층을 따른다
    tx = text_of(doc)
    for s in ("시험 주택", "1층 평면도", "A-101", "2026-11-15", "1F 평면", "S=1:100"):
        assert any(s in t for t in tx), s
    caps = by_layer(doc, "G-CAPTION", "TEXT")
    assert any(val(e, 1) == "1F 평면" and val(e, 7) == DXF.STYLE_BOLD for e in caps)   # 도면명은 굵은 스타일


def test_시트_DXF_엔티티_수는_그림_요소와_맞는다(pv):
    from dobim_core import commands as C
    r = run_export(pv, "sheet.export", {"views": [F.V1], "path": "s.dxf", **SPEC})
    doc = parse(read(pv, "s.dxf"))
    ctx_args = C.SheetExport.model_validate({"views": [F.V1], "path": "x.svg", **SPEC})
    sh = pv_sheet(pv, ctx_args)
    prims = [p for p in sh.prims if p[0] not in ("g", "/g")]
    n_line = sum(1 for p in prims if p[0] == "line")
    n_text = sum(1 for p in prims if p[0] == "text" and p[4].strip())
    n_poly = sum(1 for p in prims if p[0] == "poly" and p[3] and len(p[1]) >= 2)
    n_fill = sum(1 for p in prims if p[0] == "poly" and p[2] and p[2] != "#ffffff" and len(p[1]) >= 3) + \
        sum(1 for p in prims if p[0] == "circle" and p[6] and p[6] != "#ffffff")
    n_circ = sum(1 for p in prims if p[0] == "circle" and p[4])
    got = {}
    for e in entities(doc):
        got[e[0][1]] = got.get(e[0][1], 0) + 1
    assert got.get("LINE", 0) == n_line and got.get("TEXT", 0) == n_text and got.get("LWPOLYLINE", 0) == n_poly
    assert got.get("HATCH", 0) == n_fill and got.get("CIRCLE", 0) == n_circ
    assert sum(r["dxf"]["entities"].values()) == len(entities(doc))


def pv_sheet(sess, a):
    """시험용: 커맨드와 같은 경로로 시트 그림 요소를 만든다."""
    from dobim_core import commands as C
    from dobim_core.engine import Ctx
    return C.build_sheet_for(Ctx(sess.eng, sess.eng.head + 1, "t", dry_run=True), a)[0]


def test_같은_입력은_같은_DXF_바이트(pv):
    run_export(pv, "sheet.export", {"views": [F.V1, V3], "path": "a.dxf", **SPEC})
    run_export(pv, "sheet.export", {"views": [F.V1, V3], "path": "b.dxf", **SPEC})
    run_export(pv, "view.export", {"id": F.V1, "path": "va.dxf"})
    run_export(pv, "view.export", {"id": F.V1, "path": "vb.dxf"})
    assert read(pv, "a.dxf") == read(pv, "b.dxf") and read(pv, "va.dxf") == read(pv, "vb.dxf")


def test_뷰_DXF는_모델_mm_실척이다(pv):
    r = run_export(pv, "view.export", {"id": F.V1, "path": "v.dxf"})
    assert r["format"] == "dxf" and r["dxf"]["space"].startswith("model")
    doc = check_structure(read(pv, "v.dxf"))
    cut = by_layer(doc, "A-CUT", "LINE")
    assert cut
    xs = [float(v) for e in cut for v in vals(e, 10) + vals(e, 11)]
    ys = [float(v) for e in cut for v in vals(e, 20) + vals(e, 21)]
    # fixture: W1 (0,0)->(6000,0), W2 (6000,0)->(6000,4000), W3 (6000,4000)->(0,4000). 절단선은 벽 두께만큼 축선 밖으로 나간다
    assert -400 < min(xs) <= 0 and 6000 <= max(xs) < 6400
    assert -400 < min(ys) <= 0 and 4000 <= max(ys) < 4400
    # 축선 x=6000 벽(W2)의 두 면이 x=6000 을 사이에 두고 있다
    near = sorted({round(x, 3) for x in xs if 5800 < x < 6200})
    assert near[0] < 6000 < near[-1]
    # 제목 문자: 용지 5mm x 1:100 = 500mm 높이, 도면명 층
    title = DX.title_text({"type": "plan", "name": "1F 평면", "scale": 100})
    caps = [e for e in by_layer(doc, "G-CAPTION", "TEXT") if val(e, 1) == title]
    assert len(caps) == 1 and float(val(caps[0], 40)) == 500.0


def test_입면_치수는_분해한_선과_문자로_A_DIM_층(pv):
    r = run_export(pv, "sheet.export", {"views": [V3], "path": "e.dxf", "dims": ["elevation"], **SPEC})
    doc = check_structure(read(pv, "e.dxf"))
    dl, dt = by_layer(doc, "A-DIM", "LINE"), by_layer(doc, "A-DIM", "TEXT")
    assert dl and dt and r["dxf"]["layers"]["A-DIM"] == len(dl) + len(dt)
    assert not [e for e in entities(doc) if e[0][1] == "DIMENSION"]
    assert "6000" in [val(e, 1) for e in dt]                      # 전체 폭(벽 중심선 0 -> 6000)
    for e in dt:
        assert val(e, 72) == "1"                                  # 가운데 맞춤, 정렬점 = 삽입점
        assert (val(e, 11), val(e, 21)) == (val(e, 10), val(e, 20))
    # 입면 레벨 기준선: 파선 선 종류(3 1.5 용지 mm) 층
    dat = by_layer(doc, "A-DATUM", "LINE")
    assert dat and all(val(e, 6) is None for e in dat)          # 층 선 종류를 따른다
    lt = {val(o, 2): o for o in doc["TABLES"] if o[0] == (0, "LTYPE")}
    d = lt["DOBIM_D3_1P5"]
    assert [float(v) for v in vals(d, 49)] == [3.0, -1.5] and float(val(d, 40)) == 4.5


def test_숨은선은_파선_층(pv):
    r = run_export(pv, "sheet.export", {"views": [V3], "path": "h.dxf", "profile": {"hidden_lines": True}, **SPEC})
    doc = check_structure(read(pv, "h.dxf"))
    lt = {val(o, 2): o for o in doc["TABLES"] if o[0] == (0, "LTYPE")}
    ly = {val(o, 2): o for o in doc["TABLES"] if o[0] == (0, "LAYER")}
    assert val(ly["A-HIDDEN"], 6) == "DOBIM_D2P4_1P2" and "DOBIM_D2P4_1P2" in lt
    assert r["dxf"]["layers"].get("A-HIDDEN", 0) == len(by_layer(doc, "A-HIDDEN", "LINE"))


def test_색인_시트_DXF_표는_G_TABLE(pv):
    r = run_export(pv, "sheet.index", {"path": "i.dxf", "paper": "A1", "project_name": "시험", "sheet_number": "A-000", "date": "2026-11-15",
                                       "drawings": [{"number": "A-101", "title": "1층 평면도", "scale": "1:100"}], "tables": ["drawing_list", "walls", "notes"]})
    doc = check_structure(read(pv, "i.dxf"))
    tt = [val(e, 1) for e in by_layer(doc, "G-TABLE", "TEXT")]
    assert "도면목록표" in tt and "1층 평면도" in tt and "벽체 일람표" in tt
    assert r["dxf"]["layers"]["G-TABLE"] >= len(tt)


def test_저장된_시트_정의도_DXF로(pv):
    assert pv.run("sheet.define", {"id": F.SH1, "name": "평면", "views": [F.V1], **SPEC})["ok"]
    run_export(pv, "sheet.render", {"id": F.SH1, "path": "r.dxf"})
    run_export(pv, "sheet.export", {"views": [F.V1], "path": "x.dxf", **SPEC})
    assert read(pv, "r.dxf") == read(pv, "x.dxf")


def test_DWG는_거절(pv):
    assert pv.run("view.export", {"id": F.V1, "path": "v.dwg"})["error"]["code"] == "PATH_DENIED"


def test_기존_형식은_바이트가_그대로(pv):
    """DXF 분기를 더해도 SVG 는 이전과 같은 경로를 탄다(응답에 dxf 키가 없다)."""
    r = run_export(pv, "sheet.export", {"views": [F.V1], "path": "s.svg", **SPEC})
    assert "dxf" not in r


# ---------------------------------------------------------------- 그림 요소 단위(합성 시트)
def synth():
    sh = SH.Sheet("A3")
    sh.begin("view-x", **{"data-view": "x", "data-scale": 100})
    sh.line(10, 20, 110, 20, 0.50, "#000000")                    # 절단선
    sh.line(10, 30, 110, 30, 0.13, "#555555", (3.0, 1.5))         # 레벨 기준선
    sh.line(10, 40, 110, 40, 0.30, "#123456", (1.0, 0.5))         # 표에 없는 굵기·색·파선
    sh.poly([(10, 50), (60, 50), (60, 80), (10, 80)], fill="#cfcfcf")
    sh.circle(200, 100, 3.0, 0.25, DXF.TAG_COLOR, "#ffffff")
    sh.circle(210, 100, 0.5, 0.0, DXF.TAG_COLOR, DXF.TAG_COLOR)
    sh.text(50, 150, 2.5, "거실 100%^", "middle", "#000000", 30.0)
    sh.end()
    sh.begin("draft-line", **{"data-layer": "사용자층", "data-id": "d1"})
    sh.line(0, 0, 5, 5, 0.25)
    sh.end()
    sh.begin("draft-text")
    sh.text(5, 5, 3.0, "메모")
    sh.end()
    sh.begin("draft-x", **{"data-layer": 'a/b:c'})
    sh.line(1, 1, 2, 2, 0.25)
    sh.end()
    return sh


def test_합성_시트_요소별_변환():
    blob, info = DXF.render_dxf(synth())
    doc = check_structure(blob)
    es = entities(doc)
    kinds = [e[0][1] for e in es]
    assert kinds == ["LINE", "LINE", "LINE", "HATCH", "CIRCLE", "HATCH", "TEXT", "LINE", "TEXT", "LINE"]
    cut, dat, odd = es[0], es[1], es[2]
    assert val(cut, 8) == "A-CUT" and val(cut, 370) is None and (val(cut, 20), val(cut, 21)) == ("277.0", "277.0")   # y = 297 - 20
    assert val(dat, 8) == "A-DATUM" and val(dat, 6) is None and val(dat, 62) is None
    assert val(odd, 6) == "DOBIM_D1_0P5" and val(odd, 370) == "30" and val(odd, 420) == str(0x123456)
    assert val(es[3], 8) == "A-FILL" and val(es[3], 2) == "SOLID" and val(es[3], 93) == "4"
    assert val(es[4], 8) == "A-TAG" and float(val(es[4], 40)) == 3.0          # 흰 바탕 가림은 쓰지 않고 윤곽만
    assert val(es[5], 8) == "A-TAG" and val(es[5], 72) == "2"               # 채운 점 = 원호 경계 솔리드 해치
    t = es[6]
    assert val(t, 1) == "거실 100%%%^ " and val(t, 50) == "330.0" and val(t, 72) == "1" and val(t, 7) == DXF.STYLE_NAME
    assert val(es[7], 8) == "사용자층" and val(es[8], 8) == "A-DRAFT" and val(es[9], 8) == "a_b_c"
    assert info["layers"]["A-DRAFT"] == 1 and info["entities"]["HATCH"] == 2


def test_모델_역변환은_frame과_맞는다():
    data = {"type": "plan", "name": "평면", "scale": 50, "lines": [("cut", 1000.0, 2000.0, 4000.0, 2000.0), ("proj", 1000.0, 2000.0, 1000.0, 5000.0)],
            "fills": [], "datums": [], "annots": [], "labels": [(2500.0, 3500.0, "방", 2.5, "middle")]}
    blob, info = DXF.render_dxf(SH.build_view_page(data), model=DXF.view_model_xform(data), captions=(DX.title_text(data),))
    doc = check_structure(blob)
    ln = by_layer(doc, "A-CUT", "LINE")[0]
    assert [float(val(ln, c)) for c in (10, 20, 11, 21)] == [1000.0, 2000.0, 4000.0, 2000.0]
    lb = by_layer(doc, "A-TEXT", "TEXT")[0]
    assert (float(val(lb, 10)), float(val(lb, 20)), float(val(lb, 40))) == (2500.0, 3500.0, 125.0)
    assert info["extents"][0] <= 1000.0 and info["extents"][2] >= 4000.0


def test_2D_작도_요소도_DXF로_나간다(pv):
    """27단계 2D 작도(draft.create): 요소마다 draft-<모양> 그룹 + data-layer 로 그려지므로 DXF 에서는 사용자 층으로 나간다(합성 형상)."""
    def mk(args):
        r = pv.run("draft.create", {"view": F.V1, **args})
        assert r["ok"], r
    mk({"shape": "line", "pts": [[0, -1000], [6000, -1000]], "layer": "A-DRAFT-NOTE"})
    mk({"shape": "circle", "pts": [[4500, -3000]], "r": 400, "layer": "A-DRAFT-NOTE"})
    mk({"shape": "text", "pts": [[1000, 5000]], "text": "상세 참조", "size": 3.0, "align": "middle", "a0": 30})
    mk({"shape": "region", "pts": [[3000, 500], [4000, 500], [4000, 1500]], "fill": "solid", "color": "#88aaff"})
    mk({"shape": "dim", "pts": [[0, -2000], [6000, -2000]], "off": -800})
    r = run_export(pv, "view.export", {"id": F.V1, "path": "d.dxf"})
    doc = check_structure(read(pv, "d.dxf"))
    note = by_layer(doc, "A-DRAFT-NOTE")
    ln = [e for e in note if e[0] == (0, "LINE") and float(val(e, 20)) == -1000.0 and float(val(e, 21)) == -1000.0]
    assert ln and float(val(ln[0], 10)) == 0.0 and float(val(ln[0], 11)) == 6000.0      # 모델 mm 실척 그대로
    assert any(e[0] == (0, "CIRCLE") and float(val(e, 40)) == 400.0 for e in note)
    dr = by_layer(doc, "A-DRAFT")
    t = [e for e in dr if e[0] == (0, "TEXT") and val(e, 1) == "상세 참조"]
    assert t and float(val(t[0], 50)) == 30.0 and val(t[0], 72) == "1"                  # 반시계 30도, 가운데 맞춤
    assert any(e[0] == (0, "HATCH") and val(e, 420) == str(0x88AAFF) for e in dr)       # 솔리드 채움 = SOLID 해치, 색 유지
    assert any(e[0] == (0, "TEXT") and val(e, 1) == "6000" for e in dr)                  # 작도 치수 = 분해 선+문자
    assert not [e for e in entities(doc) if e[0][1] == "DIMENSION"]
    assert r["dxf"]["layers"]["A-DRAFT-NOTE"] == len(note)
    run_export(pv, "sheet.export", {"views": [F.V1], "path": "ds.dxf", **SPEC})       # 시트에서도 같은 층
    ds = check_structure(read(pv, "ds.dxf"))
    assert by_layer(ds, "A-DRAFT-NOTE") and "상세 참조" in [val(e, 1) for e in by_layer(ds, "A-DRAFT", "TEXT")]


def test_선굵기_표준값():
    assert [DXF.lineweight(w) for w in (0.09, 0.13, 0.18, 0.25, 0.35, 0.5, 0.7, 0.05, 2.0)] == [9, 13, 18, 25, 35, 50, 70, 5, 200]
