# SPDX-License-Identifier: MPL-2.0
"""시트 정의(뷰 배치·표제란 인자)의 저장·왕복 시험 (BIM 지침 B-04).

 1. 커맨드: 정의·바꾸기·지우기·목록, 저장한 정의로 낸 파일 == 같은 인자로 sheet.export 한 파일(바이트까지), 뷰를 쓰는 시트가 있으면 뷰를 못 지운다
 2. 왕복: IFC 로 내보냈다 가져오면 시트가 되살고 해시 3종·두 번 내보낸 IFC·시트 산출 바이트가 같다
 3. 변조·참조: 정의 값·참조가 어긋나면 그 시트만 되살리지 않는다(가져오기는 죽지 않는다)
 4. 병합·되돌리기, 새 입력이 없으면(시트 없음) IFC 에 시트 흔적이 없다
"""
import json
import shutil
import sys
from pathlib import Path

import ifcopenshell

from test_integration_g1 import F, S, _ok
from test_integration_views_rt import (A1, V1, V2, VE, VS, _build, _def_props, _edit, _export_ifc, _get, _ids, _put, _tamper)

from dobim_core import viewdefs as VD  # noqa: E402

SH1 = F.SH1
SH2 = "9" + "0" * 20 + "2"
SHEET = {"id": SH1, "name": "A-101 1층 평면", "views": [V1, VS], "paper": "A3", "project_name": "예시 프로젝트", "sheet_name": "1층 평면도·단면",
         "sheet_number": "A-101", "date": "2026-01-02", "client": "건축주", "office": "설계사무소", "north_angle": 15.0,
         "signers": [{"role": "설계자", "name": "홍"}], "revisions": [{"rev": "1", "date": "2026-01-03", "desc": "벽 두께 조정", "view": V1}],
         "grids": [{"label": "X1", "axis": "x", "pos": 0.0}, {"label": "Y1", "axis": "y", "pos": 0.0}],
         "callouts": [{"view": V1, "rect": [0.0, 0.0, 2000.0, 2000.0], "label": "1"}], "marks": True, "tags": ["room", "wall"],
         "key_plan": V1, "key_zone": [0.0, 0.0, 3000.0, 4000.0], "line_profile": {"cut": 0.35}}


def _second(**kw) -> dict:
    """둘째 시트: 2F 평면만 올린다(키 플랜·구름·부위 상세 없음). null 은 보내지 않는다(생략이 '없음')."""
    d = {k: v for k, v in SHEET.items() if k not in ("key_plan", "key_zone")}
    d.update(id=SH2, name="둘째 시트", views=[V2], revisions=[], callouts=[])
    d.update(kw)
    return d


def _export_args(extra: dict) -> dict:
    """저장한 정의와 같은 값을 sheet.export 인자로 바꾼다."""
    a = {k: v for k, v in SHEET.items() if k not in ("id", "name")}
    a.update(extra)
    return a


def _define(b, p="src.dobim", **kw):
    return _ok(b.run("sheet.define", {**SHEET, **kw}, project=p))


def _render(b, sid, name, p="src.dobim"):
    r = b.destructive("sheet.render", {"id": sid, "path": name, "overwrite": True}, p)
    assert r["ok"], r
    return r["result"]


# ================================================================ 1. 커맨드
def test_s1_정의_바꾸기_지우기_목록(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        r = _define(b)
        assert r == {"id": SH1, "created": True}
        lst = _ok(b.run("sheet.list", {}, project="src.dobim"))
        assert lst["count"] == 1 and lst["items"][0]["missing_views"] == [] and lst["items"][0]["views"] == [V1, VS]
        r = _define(b, paper="A1", views=[VS, V1])           # 같은 id -> 통째로 바꾼다
        assert r == {"id": SH1, "created": False}
        assert _ok(b.run("sheet.list", {}, project="src.dobim"))["items"][0]["views"] == [VS, V1]
        assert _ok(b.run("element.get", {"id": SH1}, project="src.dobim"))["kind"] == "sheet"
        r = b.run("sheet.define", {**SHEET, "id": SH2}, project="src.dobim")      # 같은 이름은 거절
        assert not r["ok"] and r["error"]["code"] == "CONSTRAINT"
        _ok(b.run("sheet.define", _second(name="다른 시트"), project="src.dobim"))
        assert _ok(b.run("sheet.delete", {"id": SH2}, project="src.dobim"))["deleted"] == [SH2]
        assert _ok(b.run("model.validate", {}, project="src.dobim"))["errors"] == 0
    finally:
        b.close()


def test_s1b_잘못된_정의는_거절한다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        bad = [("없는 뷰", {"views": ["9" * 22]}), ("뷰 중복", {"views": [V1, V1]}), ("구름이 올리지 않은 뷰", {"revisions": [{"rev": "1", "date": "2026-01-03", "desc": "x", "view": V2}]}),
               ("키 플랜이 단면", {"key_plan": VS}), ("key_zone 만", {"key_plan": V1, "key_zone": [0, 0, 1], "views": [V2]}), ("용지", {"paper": "A0"})]
        for why, kw in bad:
            r = b.run("sheet.define", {**SHEET, **kw}, project="src.dobim")
            assert not r["ok"], why
        assert _ok(b.run("sheet.list", {}, project="src.dobim"))["count"] == 0
    finally:
        b.close()


def test_s1c_저장한_정의로_낸_파일은_같은_인자로_낸_시트와_바이트까지_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        _define(b)
        for ext in ("svg", "pdf"):
            direct = b.destructive("sheet.export", _export_args({"path": f"d.{ext}", "overwrite": True}), "src.dobim")
            assert direct["ok"], direct
            saved = _render(b, SH1, f"s.{ext}")
            assert (tmp_path / f"d.{ext}").read_bytes() == (tmp_path / f"s.{ext}").read_bytes()
            assert saved["views"] == direct["result"]["views"]
        _ok(b.run("sheet.define", _second(), project="src.dobim"))               # 키 플랜·key_zone·date 가 없는 정의도 낸다(저장값 null 은 생략으로)
        d2 = b.destructive("sheet.export", {k: v for k, v in _second().items() if k not in ("id", "name")} | {"path": "d2.svg", "overwrite": True}, "src.dobim")
        assert d2["ok"], d2
        _render(b, SH2, "s3.svg")
        assert (tmp_path / "d2.svg").read_bytes() == (tmp_path / "s3.svg").read_bytes()
        again = _render(b, SH1, "s2.svg")                                       # 결정성: 두 번 내도 같다
        assert (tmp_path / "s.svg").read_bytes() == (tmp_path / "s2.svg").read_bytes() and again["bytes"] > 0
    finally:
        b.close()


def test_s1d_뷰를_쓰는_시트가_있으면_뷰를_지울_수_없고_시트를_지우면_지울_수_있다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        _define(b)
        r = b.run("view.delete", {"id": VS}, project="src.dobim")
        assert not r["ok"] and r["error"]["code"] == "CONSTRAINT" and SH1 in r["error"]["message"]
        assert _ok(b.run("view.delete", {"id": VE}, project="src.dobim"))["deleted"]            # 시트가 안 쓰는 뷰는 지운다
        _ok(b.run("sheet.delete", {"id": SH1}, project="src.dobim"))
        assert _ok(b.run("view.delete", {"id": VS}, project="src.dobim"))["deleted"]
    finally:
        b.close()


def test_s1e_실행_취소와_다시_실행이_시트를_따른다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        h0 = S.trip(b.hashes("src.dobim"))
        _define(b)
        h1 = S.trip(b.hashes("src.dobim"))
        assert h1 != h0
        _ok(b.run("history.undo", {}, project="src.dobim"))
        assert S.trip(b.hashes("src.dobim")) == h0 and _ids(b, "src.dobim", "sheet") == set()
        _ok(b.run("history.redo", {}, project="src.dobim"))
        assert S.trip(b.hashes("src.dobim")) == h1
    finally:
        b.close()


# ================================================================ 2. 왕복
def test_s2_시트가_있는_프로젝트의_왕복은_해시_3종과_시트_산출이_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        _define(b)
        _ok(b.run("sheet.define", _second(name="2F 평면 시트"), project="src.dobim"))
        h0 = S.trip(b.hashes("src.dobim"))
        e1 = _export_ifc(b)
        f = ifcopenshell.open(str(e1))
        assert {SH1, SH2} <= set(_def_props(f))                                   # 정의가 프로젝트 속성셋에 실렸다
        assert not [e for e in f.by_type("IfcProduct") if e.is_a("IfcAnnotation")]
        r = b.import_ifc("e1.ifc", "imp.dobim")
        assert r["ok"], r
        res = r["result"]
        assert res["elements"] == {"authored": 20, "foreign": 0, "detached": 0} and res["warnings"] == [], res
        assert _ids(b, "imp.dobim", "sheet") == {SH1, SH2}
        assert S.trip(b.hashes("imp.dobim")) == h0
        e2 = b.export("e2.ifc", "imp.dobim")
        assert e1.read_bytes() == e2.read_bytes()
        assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
        assert b.env.engine("imp.dobim").intent_consistency() == []
        assert _ok(b.run("sheet.list", {}, project="src.dobim")) == _ok(b.run("sheet.list", {}, project="imp.dobim"))
        for ext in ("svg", "pdf"):                                               # 되살린 시트가 같은 파일을 낸다
            _render(b, SH1, f"a.{ext}", "src.dobim")
            _render(b, SH1, f"c.{ext}", "imp.dobim")
            assert (tmp_path / f"a.{ext}").read_bytes() == (tmp_path / f"c.{ext}").read_bytes()
        _define(b, p="imp.dobim", paper="A1")                                      # authored 라 편집된다
    finally:
        b.close()


def test_s2b_다시_열어도_같다(tmp_path):
    from dobim_core.engine import Engine
    b = S.Bench(tmp_path)
    try:
        _build(b)
        _define(b)
        h0 = S.trip(b.hashes("src.dobim"))
        b.export("e1.ifc", "src.dobim")
        _ok(b.import_ifc("e1.ifc", "imp.dobim"))
    finally:
        b.close()
    for p in ("src.dobim", "imp.dobim"):
        eng = Engine(tmp_path / p, "t", create=False)
        try:
            h = eng.hashes()
            assert (h["state"], h["derived"], h["ifc"]) == h0 and eng.intent_consistency() == []
        finally:
            eng.close()


def test_s2c_시트_없는_프로젝트의_IFC에는_시트_흔적이_없다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        e1 = _export_ifc(b)
        assert not [k for k in _def_props(ifcopenshell.open(str(e1))) if json.loads(_def_props(ifcopenshell.open(str(e1)))[k].NominalValue.wrappedValue)["kind"] == "sheet"]
        h = S.trip(b.hashes("src.dobim"))
        _define(b)
        _ok(b.run("sheet.delete", {"id": SH1}, project="src.dobim"))
        assert S.trip(b.hashes("src.dobim")) == h                                 # 시트를 더했다 지우면 해시가 처음과 같다
        assert _export_ifc(b, "e2.ifc").read_bytes() == e1.read_bytes()
    finally:
        b.close()


# ================================================================ 3. 변조·참조
def _sheet_only_dropped(res, why_part):
    assert any(why_part in w for w in res["warnings"]), res["warnings"]


def test_s3_정의_값이_어긋나면_그_시트만_되살리지_않는다(tmp_path):
    def build(b, p="src.dobim"):
        _build(b, p)
        _ok(b.run("sheet.define", SHEET, project=p))
        _ok(b.run("sheet.define", _second(name="둘째"), project=p))
    cases = {
        "용지 값": lambda d: d["params"].update(paper="A0"),
        "알 수 없는 키": lambda d: d["params"].update(extra=1),
        "없는 뷰": lambda d: d["params"].update(views=["9" * 22]),
        "단면을 키 플랜으로": lambda d: d["params"].update(key_plan=VS),
        "정규형 아님": lambda d: d["params"].update(north_angle=15),   # 정수로 쓰면 저장 형식(실수)과 달라 받지 않는다
    }
    for name, mut in cases.items():
        tmp = tmp_path / name.replace(" ", "_")
        tmp.mkdir()
        b, res = _tamper(tmp, lambda f, mut=mut: _edit(f, SH1, mut), build=build)
        try:
            ids = _ids(b, "imp.dobim", "sheet")
            assert ids == {SH2} and any("시트" in w or SH1[:22] in w for w in res["warnings"]), (name, res["warnings"])
            assert _ok(b.run("model.validate", {}, project="imp.dobim"))["errors"] == 0
            assert SH1 in _def_props(ifcopenshell.open(str(tmp / "t.ifc")))        # 못 읽은 정의는 파일에 그대로 남는다
        finally:
            b.close()


def test_s3b_해시가_안_맞으면_복원하지_않는다(tmp_path):
    def build(b, p="src.dobim"):
        _build(b, p)
        _ok(b.run("sheet.define", SHEET, project=p))
    b, res = _tamper(tmp_path, lambda f: _edit(f, SH1, lambda d: d["params"].update(sheet_number="A-999"), fix=False), build=build)
    try:
        assert _ids(b, "imp.dobim", "sheet") == set() and res["warnings"]
    finally:
        b.close()


def test_s3c_올린_뷰가_복원되지_않으면_시트도_복원하지_않는다(tmp_path):
    def build(b, p="src.dobim"):
        _build(b, p)
        _ok(b.run("sheet.define", SHEET, project=p))
    # 단면 뷰 VS 의 정의를 망가뜨린다(뷰가 안 살아난다): VS 를 올린 시트 전체를 되살리지 않는다
    b, res = _tamper(tmp_path, lambda f: _edit(f, VS, lambda d: d["params"].update(scale=0)), build=build)
    try:
        assert VS not in _ids(b, "imp.dobim", "view") and _ids(b, "imp.dobim", "sheet") == set()
        assert any("시트가 올린 뷰" in w for w in res["warnings"]), res["warnings"]
    finally:
        b.close()


def test_s3d_시트_정의가_가리키는_id가_다른_종류의_정의면_복원하지_않는다(tmp_path):
    def build(b, p="src.dobim"):
        _build(b, p)
        _ok(b.run("sheet.define", SHEET, project=p))
    b, res = _tamper(tmp_path, lambda f: _edit(f, SH1, lambda d: d["params"].update(views=[V1, A1], revisions=[], callouts=[])), build=build)   # A1 은 치수
    try:
        assert _ids(b, "imp.dobim", "sheet") == set()
    finally:
        b.close()


# ================================================================ 4. 병합
def test_s4_시트가_있는_두_복사본의_병합이_순차_적용과_같다(tmp_path):
    b = S.Bench(tmp_path)
    try:
        _build(b)
        b.export("e1.ifc", "src.dobim")
        _ok(b.import_ifc("e1.ifc", "anc.dobim", guid=F.GUID))
    finally:
        b.close()
    for n in ("a.dobim", "b.dobim", "ref.dobim"):
        shutil.copyfile(tmp_path / "anc.dobim", tmp_path / n)
    edit_a = [("sheet.define", SHEET)]
    edit_b = [("sheet.define", _second(name="B 시트"))]
    b = S.Bench(tmp_path)
    try:
        for n, a in edit_a:
            _ok(b.run(n, a, project="a.dobim"))
            _ok(b.run(n, a, project="ref.dobim"))
        for n, a in edit_b:
            _ok(b.run(n, a, project="b.dobim"))
            _ok(b.run(n, a, project="ref.dobim"))
        prev = b.run("merge.preview", {"other": "b.dobim"}, project="a.dobim")
        assert prev["ok"] and prev["result"]["conflict_count"] == 0, prev
        assert b.run("merge.apply", {"other": "b.dobim"}, project="a.dobim")["ok"]
        assert S.trip(b.hashes("a.dobim")) == S.trip(b.hashes("ref.dobim"))
        assert _ids(b, "a.dobim", "sheet") == {SH1, SH2}
        assert _ok(b.run("model.validate", {}, project="a.dobim"))["errors"] == 0
        e = b.export("m.ifc", "a.dobim")
        assert {SH1, SH2} <= set(_def_props(ifcopenshell.open(str(e))))
    finally:
        b.close()
