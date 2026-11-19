# SPDX-License-Identifier: MPL-2.0
"""W1 1-E: 프로젝트 템플릿(DATA-12), 인쇄 용지 A0~A4·세로(IO-17), 모델 품질 점검 model.check(ANA-06).
- 템플릿은 한 트랜잭션이라 한 번 되돌리면 빈 프로젝트가 되고, 기호 id 는 프로젝트 UUID 로 정해져 재생·IFC 왕복이 같다
- 템플릿이 실패하면 프로젝트 파일을 남기지 않는다. 조회·세션 커맨드는 템플릿에 넣을 수 없다
- 용지 크기는 SVG 의 mm 크기로 확인한다. 작은 용지에 표제란 칸이 넘치면 겹쳐 그리지 않고 거절한다
- model.check 는 짧은 벽·겹친 벽·중복·고아·레벨 밖·열린 공간 경계를 찾고 아무것도 바꾸지 않는다
합성 데이터만 쓴다."""
import json
import re
import sys
import uuid

import kit
from conftest import ROOT
from dobim_core import commands_check as CK
from dobim_core import dispatch
from dobim_core import fixture as F
from dobim_core import kinds as K
from dobim_core import project_template as PT
from dobim_core import sheet as SH

sys.path.insert(0, str(ROOT.parent / "s2-roundtrip"))
import scenarios as S  # noqa: E402

G1 = "5d0b1a50-0000-4a00-8000-0d0b1a5000e1"
G2 = "5d0b1a50-0000-4a00-8000-0d0b1a5000e2"
W4, W5, W6, W7 = ("0" * 19 + s for s in ("e4a", "e5a", "e6a", "e7a"))


def ok(r):
    assert r["ok"], r
    return r["result"]


def destructive(s, name, args):
    """확인 토큰이 필요한 내보내기: 토큰을 받아 다시 부른다(실패는 첫 응답 그대로)."""
    r = s.run(name, args)
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = s.run(name, args, confirm=r["error"]["details"]["confirm_token"])
    return r


def err(r, code="CONSTRAINT"):
    assert not r["ok"] and r["error"]["code"] == code, r
    return r["error"]


# ================================================================ 템플릿(DATA-12)
def test_내장_템플릿으로_새_프로젝트(tmp_path):
    env = dispatch.Env(tmp_path)
    r = ok(dispatch.run(env, "project.new", {"path": "a.dobim", "guid": G1, "template": "kr-small"}))
    assert r["template"]["created"] == {"keynote": 6, "level": 3, "sheet": 4, "view": 6, "vtemplate": 2}
    lv = ok(dispatch.run(env, "element.query", {"kind": "level"}, project="a.dobim"))
    ids = sorted(i["id"] for i in lv["items"])
    want = sorted(PT.compress(uuid.uuid5(uuid.UUID(G1), f"template:{s}").hex) for s in ("L1", "L2", "LR"))
    assert ids == want                                                   # 기호 id = 프로젝트 UUID 로 정한 결정적 id
    vt = ok(dispatch.run(env, "view.template.list", {}, project="a.dobim"))
    assert sorted(len(t["views"]) for t in vt["items"]) == [2, 3]          # 평면 3장·입면 2장이 템플릿을 따른다
    ok(dispatch.run(env, "project.new", {"path": "b.dobim", "guid": G2, "template": "kr-small"}))
    lv2 = ok(dispatch.run(env, "element.query", {"kind": "level"}, project="b.dobim"))
    assert not set(ids) & {i["id"] for i in lv2["items"]}                # 다른 프로젝트와 id 가 겹치지 않는다
    ok(dispatch.run(env, "history.undo", {}, project="a.dobim"))         # 한 트랜잭션: 한 번 되돌리면 빈 프로젝트
    assert ok(dispatch.run(env, "element.query", {}, project="a.dobim"))["items"] == []
    env.close()


def test_템플릿_재생과_IFC_왕복(tmp_path):
    env = dispatch.Env(tmp_path)
    ok(dispatch.run(env, "project.new", {"path": "p.dobim", "guid": G1, "template": "kr-small"}))
    env.close()
    s = kit.Session(tmp_path / "p.dobim", create=False)
    try:
        ok(s.run("wall.create", {"id": W4, "level": ok(s.run("element.query", {"kind": "level"}))["items"][0]["id"], "axis": [0, 0, 5000, 0], "thickness": 200, "height": 3000}))
        res = kit.full_check(s, tmp_path)
        assert res["ok"], res["diffs"]
    finally:
        s.close()
    b = S.Bench(tmp_path)
    try:
        h = b.hashes()
        b.export("t.ifc")
        r = b.import_ifc("t.ifc", "imp.dobim")
        assert r["ok"] and not r["result"]["warnings"], r
        assert {k: v for k, v in b.hashes("imp.dobim").items() if k != "rev"} == {k: v for k, v in h.items() if k != "rev"}
    finally:
        b.close()


def test_사용자_템플릿_파일과_거절(tmp_path):
    env = dispatch.Env(tmp_path)
    tpl = {"format": PT.FORMAT, "name": "mine", "commands": [
        {"command": "level.create", "args": {"id": "$A", "name": "B1", "elevation": -3000}},
        {"command": "view.create", "args": {"id": "$V", "name": "B1 평면", "type": "plan", "level": "$A"}},
        {"command": "sheet.define", "args": {"name": "S-1", "views": ["$V"], "paper": "A4P"}}]}
    (tmp_path / "mine.json").write_text(json.dumps(tpl, ensure_ascii=False), encoding="utf-8")
    r = ok(dispatch.run(env, "project.new", {"path": "m.dobim", "template_file": "mine.json"}))
    assert r["template"] == {"name": "mine.json", "commands": 3, "created": {"level": 1, "sheet": 1, "view": 1}}
    assert ok(dispatch.run(env, "sheet.list", {}, project="m.dobim"))["items"][0]["paper"] == "A4P"

    def bad(t, code, path_part):
        (tmp_path / "bad.json").write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
        e = err(dispatch.run(env, "project.new", {"path": "x.dobim", "template_file": "bad.json"}), code)
        assert path_part in e["path"], e
        assert not (tmp_path / "x.dobim").exists()                       # 실패하면 파일을 남기지 않는다
    bad({"format": "x", "commands": []}, "SCHEMA_INVALID", "/template_file")
    bad({**tpl, "commands": [{"command": "view.list", "args": {}}]}, "USAGE", "/template/commands/0")
    bad({**tpl, "commands": [{"command": "level.creat", "args": {}}]}, "UNKNOWN_COMMAND", "/template/commands/0")
    bad({**tpl, "commands": [{"command": "level.create", "args": {"name": "1F"}}]}, "SCHEMA_INVALID", "/template/commands/0/elevation")
    # 기호는 영문·숫자·_ 만: 그 밖은 바뀌지 않아 id 꼴 검사에 걸린다
    bad({**tpl, "commands": [{"command": "level.create", "args": {"name": "1F", "elevation": 0}},
                              {"command": "view.create", "args": {"name": "v", "type": "plan", "level": "$없음"}}]}, "SCHEMA_INVALID", "/template/commands/1")
    # 인자는 맞지만 실행이 실패(없는 레벨): 트랜잭션 전체를 버리고 파일도 지운다
    (tmp_path / "bad.json").write_text(json.dumps({**tpl, "commands": [{"command": "wall.create", "args": {"level": "$L", "axis": [0, 0, 1000, 0], "thickness": 200, "height": 3000}}]}), encoding="utf-8")
    err(dispatch.run(env, "project.new", {"path": "x.dobim", "template_file": "bad.json"}), "NOT_FOUND")
    assert not (tmp_path / "x.dobim").exists()
    err(dispatch.run(env, "project.new", {"path": "x.dobim", "template": "kr-small", "template_file": "mine.json"}), "SCHEMA_INVALID")
    err(dispatch.run(env, "project.new", {"path": "m.dobim", "template": "kr-small"}), "FILE_EXISTS")
    env.close()


def test_GlobalId_압축은_ifcopenshell_과_같다():
    import ifcopenshell.guid as G
    for h in ("0" * 32, "f" * 32, uuid.uuid5(uuid.UUID(G1), "x").hex, "0123456789abcdef0123456789abcdef"):
        assert PT.compress(h) == G.compress(h)


# ================================================================ 용지(IO-17)
def test_용지_이름은_한_집합():
    assert tuple(SH.PAPERS) == K.PAPER_NAMES
    assert SH.PAPERS["A0"] == (1189.0, 841.0) and SH.PAPERS["A4P"] == (210.0, 297.0) and SH.PAPERS["A2"] == (594.0, 420.0)
    assert set(SH.STANDARD_PAPERS) <= set(SH.PAPERS)
    assert K.row_params_error("sheet", {"name": "s", "views": [F.V1], "paper": "B4"}) is not None


def _svg_mm(blob: bytes):
    m = re.search(rb'width="([0-9.]+)mm" height="([0-9.]+)mm"', blob)
    return float(m.group(1)), float(m.group(2))


def test_용지별_시트_내보내기(base, tmp_path):
    s = base
    ok(s.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200, "scale": 200}))
    for paper in ("A0", "A2", "A4", "A3P", "A4P"):
        r = destructive(s, "sheet.export", {"views": [F.V1], "path": f"s-{paper}.svg", "paper": paper, "date": "2026-01-01"})
        assert r["ok"], r
        assert _svg_mm((tmp_path / f"s-{paper}.svg").read_bytes()) == SH.PAPERS[paper]
    ok(s.run("sheet.define", {"id": F.SH1, "name": "A4 세로", "views": [F.V1], "paper": "A4P", "date": "2026-01-01"}))
    assert ok(s.run("sheet.list", {}))["items"][0]["paper"] == "A4P"
    img = ok(s.run("sheet.image", {"id": F.SH1, "max_px": 300}))
    assert img["paper"] == "A4P"
    # 1:100 평면(6m x 4m + 여백)은 A4 세로 도면 영역(폭 89mm)에 안 들어간다: 겹쳐 그리지 않고 거절
    ok(s.run("view.set", {"id": F.V1, "scale": 50}))
    e = destructive(s, "sheet.export", {"views": [F.V1], "path": "s-x.svg", "paper": "A4P", "date": "2026-01-01"})
    assert not e["ok"] and "A4P" in e["error"]["message"], e


def test_작은_용지에_표제란이_넘치면_거절(base):
    s = base
    ok(s.run("view.create", {"id": F.V1, "name": "1F 평면", "type": "plan", "level": F.LV1, "cut_height": 1200, "scale": 200}))
    signers = [{"role": f"역할{i}", "name": f"이름{i}"} for i in range(6)]
    e = destructive(s, "sheet.export", {"views": [F.V1], "path": "t.svg", "paper": "A4", "client": "건축주", "office": "사무소", "signers": signers, "date": "2026-01-01"})
    assert not e["ok"] and "표제란" in e["error"]["message"], e
    assert destructive(s, "sheet.export", {"views": [F.V1], "path": "t.svg", "paper": "A4", "date": "2026-01-01"})["ok"]


# ================================================================ model.check(ANA-06)
def _probs(s, **kw):
    r = ok(s.run("model.check", kw))
    return r, {c: [tuple(i["ids"]) for i in r["items"] if i["check"] == c] for c in r["counts"]}


def test_깨끗한_기준_모델은_문제가_없다(base):
    r, _ = _probs(base)
    assert r["problems"] == 0 and set(r["counts"]) == set(CK.CHECKS)


def test_모델_점검이_찾는_것(base):
    s = base
    rev = ok(s.run("project.info", {}))["rev"]
    ok(s.run("wall.create", {"id": W4, "level": F.LV1, "axis": [2000, 0, 8000, 0], "thickness": 200, "height": 3000}))   # W1 과 4000 겹침
    ok(s.run("wall.create", {"id": W5, "level": F.LV1, "axis": [6000, 0, 6000, 4000], "thickness": 200, "height": 3000}))  # W2 와 똑같다(중복이자 겹침)
    ok(s.run("wall.create", {"id": W6, "level": F.LV1, "axis": [9000, 9000, 9005, 9000], "thickness": 100, "height": 3000}))  # 5mm 짧은 벽
    ok(s.run("wall.create", {"id": W7, "level": F.LV1, "axis": [9000, 0, 9000, 3000], "thickness": 200, "height": 2000, "base_offset": 3500}))  # 2F(3300) 위
    ok(s.run("space.create", {"id": F.SP1, "level": F.LV1, "name": "거실", "boundary": [[100, 100], [5900, 100], [5900, 3900], [100, 3900]], "height": 2800}))
    rev2 = ok(s.run("project.info", {}))["rev"]
    r, p = _probs(s)
    assert ok(s.run("project.info", {}))["rev"] == rev2 != rev               # 읽기 전용
    assert (W6,) in p["short_wall"]
    assert sorted([F.W1, W4]) in [sorted(x) for x in p["overlap_wall"]] and sorted([F.W2, W5]) in [sorted(x) for x in p["overlap_wall"]]
    assert p["duplicate"] == [tuple(sorted([F.W2, W5]))]
    assert p["off_level"] == [(W7,)]
    assert p["space_open"] == [(F.SP1,)]                                      # 서쪽(x=0) 벽이 없다
    assert p["orphan"] == []
    gap = next(i for i in r["items"] if i["check"] == "space_open")["msg"]
    assert "3800mm" in gap, gap
    # 서쪽 벽을 그으면 닫힌다
    ok(s.run("wall.create", {"level": F.LV1, "axis": [0, 4000, 0, 0], "thickness": 200, "height": 3000}))
    assert _probs(s, checks=["space_open"])[1] == {"space_open": []}
    # 거르기·쪽 나누기
    assert set(_probs(s, checks=["short_wall", "duplicate"])[0]["counts"]) == {"short_wall", "duplicate"}
    assert _probs(s, level=F.LV2)[0]["problems"] == 0
    e1 = s.run("model.check", {"limit": 2})
    assert len(e1["result"]["items"]) == 2 and e1["page"]["next_cursor"]
    e2 = s.run("model.check", {"limit": 100, "cursor": e1["page"]["next_cursor"]})
    assert len(e1["result"]["items"]) + len(e2["result"]["items"]) == e1["result"]["problems"] and e2["page"]["next_cursor"] is None
    assert not s.run("model.check", {"level": F.W1})["ok"]                   # 레벨이 아닌 id


def test_큰_모델에서도_겹침_점검은_칸으로_묶는다(sess):
    s = sess
    ok(s.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0}))
    cmds = [{"command": "wall.create", "args": {"level": F.LV1, "axis": [0, i * 500, 3000, i * 500], "thickness": 100, "height": 3000}} for i in range(400)]
    cmds.append({"command": "wall.create", "args": {"level": F.LV1, "axis": [1000, 500 * 7 + 3, 5000, 500 * 7 + 3], "thickness": 100, "height": 3000}})
    ok(s.batch(cmds))
    r, p = _probs(s, checks=["overlap_wall"])
    assert r["counts"] == {"overlap_wall": 1} and len(p["overlap_wall"][0]) == 2
