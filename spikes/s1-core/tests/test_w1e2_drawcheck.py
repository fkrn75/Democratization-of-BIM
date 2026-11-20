# SPDX-License-Identifier: MPL-2.0
"""W1 1-E 2차: 건축법 도면 기준 대조 drawing.check(ANA-05)와 세움터 프로파일 근거 표시(DATA-09).
- 근거: 건축법 시행규칙 별표 2(허가)·별표 4의2(착공, 건축 분야) 원문. 원문에서 확인한 항목만 위반(fail)으로 세운다
- 표제란 칸·도면번호 형식은 법령 근거를 확인하지 못해(확인 필요) 경고까지만 낸다. 별표 2 는 축척을 '임의'로 두어 축척 값은 알리기만 한다
- 읽기 전용(rev 불변), 위반이 있으면 CLI 종료 코드 3
- rules.list seumteo 는 규칙마다 basis(public·draft)를 보이고, 판정 결과(runner 출력)는 바뀌지 않는다
합성 데이터만 쓴다."""
import pytest

from dobim_core import commands_drawcheck as DC
from dobim_core import dispatch
from dobim_core import errors
from dobim_core.krules import catalog, profiles

P = "a.dobim"


def ok(r):
    assert r["ok"], r
    return r["result"]


def run(env, name, args):
    return dispatch.run(env, name, args, project=P)


def check(env, **kw):
    return ok(run(env, "drawing.check", {"detail": True, **kw}))


def by_id(res):
    return {x["id"]: x for x in res["items"]}


def _ids(env):
    """kr-small 템플릿의 레벨 이름 -> id, 시트 목록."""
    lv = ok(run(env, "element.query", {"kind": "level"}))["items"]
    levels = {ok(run(env, "element.get", {"id": i["id"]}))["params"]["name"]: i["id"] for i in lv}
    sheets = ok(run(env, "sheet.list", {}))["items"]
    return levels, sheets


@pytest.fixture
def small(tmp_path):
    """kr-small 템플릿(레벨 3·평면 3·입면 남북·시트 4, 배치도·단면 없음)."""
    env = dispatch.Env(tmp_path)
    ok(dispatch.run(env, "project.new", {"path": P, "template": "kr-small"}))
    return env


def _complete(env):
    """허가·착공 기준을 모두 채운 모델: 외벽+재료, 실+반자높이, 배치도, 종·횡단면+높이 치수, 마감 표기 입면, 표제란."""
    levels, sheets = _ids(env)
    l1 = levels["1F"]
    pts = [(0, 0), (12000, 0), (12000, 8000), (0, 8000)]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        w = ok(run(env, "wall.create", {"level": l1, "axis": [x1, y1, x2, y2], "thickness": 200, "height": 3300}))["id"]
        ok(run(env, "pset.set", {"id": w, "pset": "Pset_WallCommon", "prop": "IsExternal", "value": True}))
        ok(run(env, "material.set", {"id": w, "name": "Concrete"}))
    ok(run(env, "space.create", {"level": l1, "boundary": [[0, 0], [12000, 0], [12000, 8000], [0, 8000]], "name": "사무실", "category": "사무실",
                                 "height": 3300, "ceiling_height": 2700}))
    site = ok(run(env, "view.create", {"name": "배치도", "type": "site"}))["id"]
    s1 = ok(run(env, "view.create", {"name": "A-A 단면", "type": "section", "p1": [-1000, 4000], "p2": [13000, 4000]}))["id"]
    s2 = ok(run(env, "view.create", {"name": "B-B 단면", "type": "section", "p1": [6000, -1000], "p2": [6000, 9000]}))["id"]
    ok(run(env, "annot.dim.heights", {"view": s1}))
    e1 = ok(run(env, "view.create", {"name": "동측 입면", "type": "elevation", "dir": "E", "finish": True}))["id"]
    frame = {"project_name": "예시 프로젝트", "office": "예시 설계사무소", "date": "2026-01-01", "paper": "A3"}
    for sh in sheets:
        full = ok(run(env, "element.get", {"id": sh["id"]}))["params"]
        ok(run(env, "sheet.define", {"id": sh["id"], "name": full["name"], "views": full["views"], "sheet_number": full["sheet_number"],
                                     "sheet_name": full["sheet_name"], **frame}))
    ok(run(env, "sheet.define", {"name": "A-001 배치도", "views": [site], "sheet_number": "A-001", "sheet_name": "배치도", **frame}))
    ok(run(env, "sheet.define", {"name": "A-301 단면도", "views": [s1, s2], "sheet_number": "A-301", "sheet_name": "단면도", **frame}))
    ok(run(env, "sheet.define", {"name": "A-202 동측 입면도", "views": [e1], "sheet_number": "A-202", "sheet_name": "동측 입면도", **frame}))
    return levels


# ================================================================ 근거 표
def test_규칙마다_근거_조항이_있고_법령_근거가_없는_규칙은_엔진_기준으로_표시한다():
    assert list(DC.RULES) == [f"KR-DRW-{i:02d}" for i in range(1, 14)]
    for rid, (title, basis, legal) in DC.RULES.items():
        assert title and basis and set(basis) <= {"permit", "start"}
        for b in basis.values():
            assert b.split()[0] in ("별표2", "별표4의2", "엔진")
            assert legal or b.startswith("엔진") or rid in ("KR-DRW-12", "KR-DRW-13")
    assert "개정 2021. 6. 25." in DC.SRC["별표2"] and "개정 2021. 8. 27." in DC.SRC["별표4의2"]
    assert "확인 필요" in DC.SRC["엔진"]


# ================================================================ 템플릿 그대로(배치도·단면 없음)
def test_템플릿만으로는_배치도와_종횡단면이_없어_허가_위반(small):
    res = check(small)
    rows = by_id(res)
    assert res["profile"] == "permit" and res["sheets"] == 4
    assert res["violations"] == ["KR-DRW-01", "KR-DRW-05"]
    assert rows["KR-DRW-03"]["status"] == "pass" and rows["KR-DRW-04"]["status"] == "pass"   # 1층 평면, 남·북 입면
    assert rows["KR-DRW-02"]["status"] == "na" and rows["KR-DRW-09"]["status"] == "na"
    assert rows["KR-DRW-10"]["status"] == "warn" and rows["KR-DRW-10"]["failed"] == 4         # 프로젝트명·설계사무소·작성일 비어 있음
    assert rows["KR-DRW-13"]["status"] == "info" and "건축계획서" in rows["KR-DRW-13"]["note"]
    assert "KR-DRW-06" not in rows and "KR-DRW-07" not in rows                                   # 착공 전용 규칙은 허가 프로파일에 없다


def test_조회는_모델을_바꾸지_않고_위반이면_종료_코드_3(small):
    r0 = run(small, "sheet.list", {})
    r = run(small, "drawing.check", {})
    assert r["ok"] and r["rev"] == r0["rev"]
    assert errors.exit_code_of(r) == 3
    r = run(small, "drawing.check", {"profile": "start"})
    assert errors.exit_code_of(r) == 3 and "KR-DRW-01" in r["result"]["violations"]


# ================================================================ 기준을 모두 채운 모델
def test_허가_기준을_모두_채우면_위반도_경고도_없다(small):
    _complete(small)
    res = check(small)
    rows = by_id(res)
    assert res["violations"] == [] and res["warnings"] == [], res
    assert {k: v["status"] for k, v in rows.items()} == {
        "KR-DRW-01": "pass", "KR-DRW-02": "pass", "KR-DRW-03": "pass", "KR-DRW-04": "pass", "KR-DRW-05": "pass", "KR-DRW-08": "pass",
        "KR-DRW-09": "pass", "KR-DRW-10": "pass", "KR-DRW-11": "pass", "KR-DRW-12": "pass", "KR-DRW-13": "info"}
    assert errors.exit_code_of(run(small, "drawing.check", {})) == 0


def test_착공_기준_각층_평면과_지붕평면(small):
    levels = _complete(small)
    res = check(small, profile="start")
    rows = by_id(res)
    assert res["violations"] == [] and rows["KR-DRW-06"]["applicable"] == 3 and rows["KR-DRW-07"]["status"] == "pass"
    assert "KR-DRW-03" not in rows and "도면목록표" in rows["KR-DRW-13"]["note"]
    # 레벨을 하나 더 두면 그 층 평면이 없어 위반, 지붕 요소가 그 위에 있으면 지붕평면도 위반
    l3 = ok(run(small, "level.create", {"name": "PH", "elevation": 9900}))["id"]
    ok(run(small, "roof.create", {"level": l3, "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]], "form": "gable", "pitch": 30}))
    rows = by_id(check(small, profile="start"))
    assert rows["KR-DRW-06"]["status"] == "fail" and rows["KR-DRW-06"]["examples"][0]["id"] == l3
    assert rows["KR-DRW-07"]["status"] == "fail"
    assert levels["1F"]


# ================================================================ 하나씩 어긴 경우
def test_단면이_같은_방향뿐이면_종횡단면_위반(small):
    _complete(small)
    v = ok(run(small, "view.create", {"name": "C-C 단면", "type": "section", "p1": [-1000, 2000], "p2": [13000, 2500]}))["id"]
    ok(run(small, "sheet.define", {"name": "A-302 단면도", "views": [v], "sheet_number": "A-302", "project_name": "예", "office": "예", "date": "2026-01-01"}))
    sid = next(s["id"] for s in ok(run(small, "sheet.list", {}))["items"] if s["sheet_number"] == "A-302")
    other = [s["id"] for s in ok(run(small, "sheet.list", {}))["items"] if s["sheet_number"] != "A-301"]
    rows = by_id(check(small, sheets=other))                      # 직교 단면 시트(A-301)를 빼고 본다
    assert rows["KR-DRW-05"]["status"] == "fail" and "45도" in rows["KR-DRW-05"]["examples"][0]["reason"]
    assert rows["KR-DRW-09"]["status"] == "fail"                  # 남은 단면에는 높이 치수가 없다
    assert sid in other


def test_입면이_한_방향뿐이면_위반_마감표기와_외벽재료(small):
    _complete(small)
    sheets = ok(run(small, "sheet.list", {}))["items"]
    keep = [s["id"] for s in sheets if s["sheet_number"] not in ("A-201",)]
    rows = by_id(check(small, sheets=keep))                       # 동측 입면 하나만 남는다
    assert rows["KR-DRW-04"]["status"] == "fail"
    keep2 = [s["id"] for s in sheets if s["sheet_number"] != "A-202"]
    rows = by_id(check(small, sheets=keep2))                      # 마감 표기를 켠 입면이 빠진다
    assert rows["KR-DRW-08"]["status"] == "fail" and "finish" in rows["KR-DRW-08"]["examples"][0]["reason"]
    w2 = ok(run(small, "wall.create", {"level": _ids(small)[0]["1F"], "axis": [0, 10000, 5000, 10000], "thickness": 200, "height": 3000}))["id"]
    ok(run(small, "pset.set", {"id": w2, "pset": "Pset_WallCommon", "prop": "IsExternal", "value": True}))
    rows = by_id(check(small))
    assert rows["KR-DRW-08"]["status"] == "fail" and rows["KR-DRW-08"]["examples"][0]["id"] == w2   # 재료 없는 외벽


def test_배치도와_1층_평면이_없으면_위반(small):
    _complete(small)
    sheets = ok(run(small, "sheet.list", {}))["items"]
    keep = [s["id"] for s in sheets if s["sheet_number"] not in ("A-001", "A-101")]
    rows = by_id(check(small, sheets=keep))
    assert rows["KR-DRW-01"]["status"] == "fail" and rows["KR-DRW-02"]["status"] == "na"
    assert rows["KR-DRW-03"]["status"] == "fail" and "1F" in rows["KR-DRW-03"]["examples"][0]["reason"]


def test_표제란_도면번호_축척은_경고나_정보까지만(small):
    _complete(small)
    v = ok(run(small, "view.create", {"name": "부분 평면", "type": "plan", "level": _ids(small)[0]["1F"], "scale": 75}))["id"]
    ok(run(small, "sheet.define", {"name": "중복", "views": [v], "sheet_number": "A-101"}))           # 번호 중복 + 표제란 빈칸
    ok(run(small, "sheet.define", {"name": "형식", "views": [v], "sheet_number": "건축-1", "project_name": "p", "office": "o", "date": "2026-01-01"}))
    res = check(small)
    rows = by_id(res)
    assert res["violations"] == []                                 # 엔진 기준 규칙은 위반(fail)으로 세우지 않는다
    assert set(res["warnings"]) == {"KR-DRW-10", "KR-DRW-11"}
    reasons = " ".join(e["reason"] for e in rows["KR-DRW-11"]["examples"])
    assert "같이 씀" in reasons and "형식이 아님" in reasons
    assert rows["KR-DRW-12"]["status"] == "info" and "1:75" in rows["KR-DRW-12"]["examples"][0]["reason"]
    assert errors.exit_code_of(run(small, "drawing.check", {})) == 0


def test_없는_시트는_NOT_FOUND_시트가_없으면_해당_없음(small, tmp_path):
    r = run(small, "drawing.check", {"sheets": ["0" * 22]})
    assert not r["ok"] and r["error"]["code"] == "NOT_FOUND" and r["error"]["path"] == "/sheets/0"
    env = dispatch.Env(tmp_path / "b")
    (tmp_path / "b").mkdir()
    ok(dispatch.run(env, "project.new", {"path": P}))
    res = ok(dispatch.run(env, "drawing.check", {"detail": True}, project=P))
    rows = by_id(res)
    assert res["sheets"] == 0 and rows["KR-DRW-10"]["status"] == "na" and rows["KR-DRW-03"]["status"] == "na"


def test_쪽_나눔과_상세_끄기(small):
    env = run(small, "drawing.check", {"limit": 3})
    res = ok(env)
    assert len(res["items"]) == 3 and set(res["items"][0]) == {"id", "status", "applicable", "failed"}
    nxt = ok(run(small, "drawing.check", {"limit": 50, "cursor": env["page"]["next_cursor"]}))
    assert [x["id"] for x in res["items"] + nxt["items"]] == [r for r, (_, b, _) in DC.RULES.items() if "permit" in b]


# ================================================================ 세움터 근거 표시(DATA-09)
def test_세움터_규칙_목록은_공개_확인_여부를_보인다(small):
    rows = []
    cur = None
    while True:
        env = dispatch.run(small, "rules.list", {"profile": "seumteo", **({"cursor": cur} if cur else {})})
        r = ok(env)
        rows += r["items"]
        cur = env["page"]["next_cursor"]
        if not cur:
            break
    assert "민원인 매뉴얼(BIM) Rev 1.5" in r["public_source"]
    seu = {x["id"]: x for x in rows if x["scope"] == "seumteo"}
    assert set(seu) == set(catalog.SEUMTEO_CHECKS)
    assert {k for k, v in seu.items() if v["basis"] == "public"} == set(profiles.SEU_PUBLIC) == {"KR-SEU-04", "KR-SEU-09", "KR-SEU-10", "KR-SEU-15"}
    assert all(("public_ref" in v) == (v["basis"] == "public") for v in seu.values())
    assert "AIS_피난계단" in seu["KR-SEU-15"]["public_ref"]
    proc = ok(dispatch.run(small, "rules.list", {}))
    assert "public_source" not in proc and all("basis" not in x for x in proc["items"])   # 조달청 목록은 그대로
    assert profiles.DRAFT_WARNING.startswith("초안:")                                     # 설계기준서 원본은 여전히 미확보
