# SPDX-License-Identifier: MPL-2.0
"""S2 시나리오 T1~T5: 외래 IFC 를 무손실로 통과시키고 의도를 복원할 수 있는가 (docs/roadmap.md S2).

각 함수는 측정값 사전을 돌려준다(경로·해시·GlobalId 를 넣지 않는다). 시험(tests/test_s2_*.py)과 결과 생성기(run_s2.py)가 같이 쓴다.
공식 ifcdiff 는 환경변수 DOBIM_IFCDIFF_PYTHON 의 파이썬으로 부른다(tools/ifcdiff_check.py).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, str(ROOT / "spikes" / "s1-core" / "src"))
sys.path.insert(0, str(ROOT / "spikes" / "s1-core" / "tools"))
sys.path.insert(0, str(ROOT / "spikes" / "s0-baseline"))

import ifcopenshell  # noqa: E402
import ifcopenshell.api.pset as api_pset  # noqa: E402
import ifcopenshell.util.element as ue  # noqa: E402

import ifcdiff_check  # noqa: E402
import roundtrip_check  # noqa: E402
from dobim_core import dispatch, fixture as F  # noqa: E402

BA_DEFAULT = ROOT / "spikes" / "s0-baseline" / "out" / "samples" / "Building-Architecture.ifc"


def ba_path() -> Path | None:
    p = Path(os.environ.get("DOBIM_BA_IFC", BA_DEFAULT))
    return p if p.is_file() else None


def revit_path(variant: str) -> Path | None:
    """Revit 내보내기 표본(비공개, 로컬). variant = IFC4RV | IFC4DTV | IFC2x3."""
    d = os.environ.get("DOBIM_REVIT_DIR")
    p = Path(d) / f"RA-01_{variant}.ifc" if d else None
    return p if p and p.is_file() else None


def have_ifcdiff() -> bool:
    py = os.environ.get("DOBIM_IFCDIFF_PYTHON") or sys.executable
    return subprocess.run([py, "-c", "import ifcdiff"], capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


# ---------------------------------------------------------------- 작업대
class Bench:
    """작업 폴더 하나와 그 위의 dispatch 경로(CLI·MCP 와 같은 검증 경로)."""

    def __init__(self, ws: Path, actor: str = "s2"):
        self.ws = Path(ws)
        self.ws.mkdir(parents=True, exist_ok=True)
        self.env = dispatch.Env(self.ws, actor)

    def run(self, name, args=None, project="p.dobim", **kw):
        return dispatch.run(self.env, name, args, project=project, **kw)

    def destructive(self, name, args, project="p.dobim"):
        r = self.run(name, args, project=project)
        assert not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED", r
        return self.run(name, args, project=project, confirm=r["error"]["details"]["confirm_token"])

    def export(self, out: str, project="p.dobim") -> Path:
        r = self.destructive("ifc.export", {"path": out}, project)
        assert r["ok"], r
        return self.ws / out

    def put(self, src: Path, name: str) -> Path:
        dst = self.ws / name
        shutil.copyfile(src, dst)
        return dst

    def import_ifc(self, source: str, project="p.dobim", guid=None):
        args = {"source": source, "path": project}
        if guid:
            args["guid"] = guid
        return dispatch.run(self.env, "ifc.import", args)

    def hashes(self, project="p.dobim") -> dict:
        r = self.run("model.hash", {}, project=project)
        assert r["ok"], r
        return r["result"]

    def origins(self, project="p.dobim") -> dict:
        r = self.run("project.info", {}, project=project)
        assert r["ok"], r
        return r["result"].get("origins", {"authored": sum(r["result"]["counts"].values()), "foreign": 0, "detached": 0})

    def ids_of(self, origin: str, project="p.dobim") -> list[str]:
        out, cursor = [], None
        while True:
            a = {"origin": origin, "limit": 1000}
            if cursor:
                a["cursor"] = cursor
            r = self.run("element.query", a, project=project)
            assert r["ok"], r
            out += [i["id"] for i in r["result"]["items"]]
            cursor = (r["page"] or {}).get("next_cursor")
            if not cursor:
                return out

    def close(self):
        self.env.close()


def cli_hashes(ws: Path, project="p.dobim") -> dict:
    """다른 프로세스(CLI)가 같은 프로젝트에서 계산한 해시 3종."""
    p = subprocess.run([sys.executable, "-m", "dobim_core", "--workspace", str(ws), "--actor", "cli", "run", "model.hash", "-p", project],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=300, cwd=ws,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "spikes" / "s1-core" / "src"), "PYTHONUTF8": "1"})
    env = json.loads(p.stdout.decode("utf-8"))
    assert env["ok"], env
    return env["result"]


def trip(h: dict) -> tuple:
    return (h["state"], h["derived"], h["ifc"])


def m_summary(ref: Path, test: Path) -> dict:
    """S0 왕복 비교 도구(M1~M8)의 지표별 값·판정. 세부 목록(GlobalId)은 싣지 않는다."""
    R = roundtrip_check.run_compare(ref, test)
    out = {k: {"value": (round(v["value"], 3) if isinstance(v["value"], float) else v["value"]), "pass": v["pass"]}
           for k, v in sorted(R.items()) if not k.startswith("_")}
    out["verdict"] = roundtrip_check.verdict(R)
    return out


def product_count(path: Path) -> int:
    return len(ifcopenshell.open(str(path)).by_type("IfcProduct"))


# ---------------------------------------------------------------- T1 / T2 / T3: 외래 표본
def t1_passthrough(src: Path, work: Path) -> dict:
    """가져오기 → 내보내기. 수정하지 않은 요소의 의미 동일(ifcdiff 차이 0)과 M1~M8, 전 요소 foreign."""
    b = Bench(work)
    try:
        b.put(src, "in.ifc")
        imp = b.import_ifc("in.ifc")
        assert imp["ok"], imp
        r = imp["result"]
        h_before = b.hashes()
        out = b.export("out.ifc")
        diff = ifdiff(b.ws / "in.ifc", out)
        res = {"products_in": product_count(b.ws / "in.ifc"), "products_out": product_count(out),
               "elements": r["elements"], "all_foreign": r["elements"]["authored"] == 0 and r["elements"]["detached"] == 0,
               "skeleton": r["skeleton"], "warnings_total": r["warnings_total"], "diff": diff, "roundtrip_check": m_summary(b.ws / "in.ifc", out)}
        # 같은 프로젝트를 다시 열어도(새 엔진) 해시가 같고, 다른 프로세스(CLI)도 같은 값을 낸다
        b.env.close()
        b2 = Bench(work)
        res["hash_reopen_equal"] = trip(b2.hashes()) == trip(h_before)
        res["hash_cross_process_equal"] = trip(cli_hashes(b2.ws)) == trip(h_before)
        res["intent_consistency_bad"] = b2.env.engine("p.dobim").intent_consistency()
        res["undo_nothing"] = b2.run("history.undo", {})["error"]["code"] == "NOTHING_TO_UNDO"
        b2.close()
        return res
    finally:
        b.close()


def ifdiff(old: Path, new: Path, ids: bool = False) -> dict:
    d = ifcdiff_check.check(str(old), str(new), ids=ids)
    out = {}
    if ids:   # 공개해도 되는 합성 모델(fixture)에서만 쓴다
        out["changed_ids"] = sorted(set(d["ifcdiff"]["ids"]["changed"]) | set(d["ifcdiff"]["ids"]["added"]) | set(d["ifcdiff"]["ids"]["deleted"])
                                    | set(d["strict"]["ids"]["changed"]) | set(d["strict"]["ids"]["added"]) | set(d["strict"]["ids"]["deleted"]))
    return {**out, "ifcdiff_total": d["ifcdiff"]["total"], "ifcdiff_added": d["ifcdiff"]["added"], "ifcdiff_deleted": d["ifcdiff"]["deleted"],
            "ifcdiff_changed": d["ifcdiff"]["changed"], "ifcdiff_changed_kinds": d["ifcdiff"]["changed_kinds"],
            "ifcdiff_version": d["ifcdiff"]["ifcdiff_version"], "strict_total": d["strict"]["total"],
            "strict_products_changed": d["strict"]["products_changed"], "strict_relationships_changed":
                d["strict"]["relationships_added"] + d["strict"]["relationships_removed"], "differences": d["differences"]}


def _first_editable(b: Bench) -> tuple[str, str, str, object]:
    """속성이 있는 첫 외래 요소의 (id, 속성셋, 속성, 값): 문자열·불리언 속성을 우선한다."""
    for eid in b.ids_of("foreign"):
        r = b.run("element.get", {"id": eid})["result"]
        for ps, props in sorted(r["psets"].items()):
            for pr, v in sorted(props.items()):
                if isinstance(v, (str, bool)) and not ps.startswith("Qto"):
                    return eid, ps, pr, v
    raise AssertionError("속성이 있는 외래 요소가 없다")


def _prop_snapshot(path: Path) -> dict:
    """파일 안 모든 요소의 (GlobalId, 속성셋, 속성) -> 값. 형 상속 없이 요소에 직접 달린 것만."""
    f = ifcopenshell.open(str(path))
    out = {}
    for p in f.by_type("IfcProduct"):
        for ps, props in ue.get_psets(p, psets_only=True, should_inherit=False).items():
            for k, v in props.items():
                if k != "id":
                    out[(p.GlobalId, ps, k)] = v
    return out


def t2_property_edit(src: Path, work: Path) -> dict:
    """외래 요소 속성 1건 편집 → 내보내기 → 차이는 그 요소의 그 속성뿐. 편집 취소 후에는 차이 0."""
    b = Bench(work)
    try:
        b.put(src, "in.ifc")
        assert b.import_ifc("in.ifc")["ok"]
        eid, ps, pr, old = _first_editable(b)
        new = (not old) if isinstance(old, bool) else str(old) + "-S2"
        r = b.run("pset.set", {"id": eid, "pset": ps, "prop": pr, "value": new})
        assert r["ok"] and r["result"]["changed"], r
        out = b.export("out.ifc")
        diff = ifdiff(b.ws / "in.ifc", out)
        a, c = _prop_snapshot(b.ws / "in.ifc"), _prop_snapshot(out)
        changed = sorted(k for k in a.keys() | c.keys() if a.get(k) != c.get(k))
        ref_psets = len(roundtrip_check.extract(ifcopenshell.open(str(b.ws / "in.ifc")), geometry=False)["psets"])
        res = {"diff": diff, "ref_pset_values": ref_psets, "props_changed": len(changed), "changed_is_target": changed == [(eid, ps, pr)] and c.get((eid, ps, pr)) == new,
               "products_in": product_count(b.ws / "in.ifc"), "products_out": product_count(out),
               "roundtrip_check": m_summary(b.ws / "in.ifc", out)}
        # 취소하면 원본과 같아진다(해시도 처음으로)
        u = b.run("history.undo", {})
        assert u["ok"], u
        out2 = b.export("out2.ifc")
        res["after_undo_diff"] = ifdiff(b.ws / "in.ifc", out2)
        # 지우기: 속성 하나만 없어진다
        d = b.run("pset.delete", {"id": eid, "pset": ps, "prop": pr})
        assert d["ok"] and d["result"]["changed"], d
        out3 = b.export("out3.ifc")
        c3 = _prop_snapshot(out3)
        gone = sorted(k for k in a.keys() | c3.keys() if a.get(k) != c3.get(k))
        res["delete_changed_is_target"] = gone == [(eid, ps, pr)] and (eid, ps, pr) not in c3
        return res
    finally:
        b.close()


# ---------------------------------------------------------------- T4: 저작 왕복
def build_authored(b: Bench, project="p.dobim", extra_walls: int = 0) -> None:
    """기준 fixture 로 프로젝트를 만든다. extra_walls > 0 이면 조인·문이 없는 독립 벽을 더한다(T5 용)."""
    from dobim_core.engine import Engine
    Engine.create(b.ws / project, "s2", guid=F.GUID).close()
    for name, args in F.SCRIPT:
        r = b.run(name, args, project=project)
        assert r["ok"], r
    for i in range(extra_walls):
        r = b.run("wall.create", {"id": "4" + f"{i + 1:021d}", "level": F.LV2, "axis": [0, 1000 * (i + 1), 5000, 1000 * (i + 1)],
                                  "thickness": 200, "height": 3000}, project=project)
        assert r["ok"], r
    b.run("pset.set", {"id": F.W2, "pset": "Pset_WallCommon", "prop": "FireRating", "value": "EI60"}, project=project)
    b.run("material.set", {"id": F.D2, "name": "Timber"}, project=project)


def t4_authored_roundtrip(work: Path) -> dict:
    b = Bench(work)
    try:
        build_authored(b, "src.dobim")
        h0 = b.hashes("src.dobim")
        e1 = b.export("e1.ifc", "src.dobim")
        imp = b.import_ifc("e1.ifc", "dst.dobim")
        assert imp["ok"], imp
        r = imp["result"]
        h1 = b.hashes("dst.dobim")
        e2 = b.export("e2.ifc", "dst.dobim")
        res = {"elements": r["elements"], "all_authored": r["elements"]["foreign"] == 0 and r["elements"]["detached"] == 0,
               "detached_total": r["detached_total"], "skeleton": r["skeleton"],
               "state_hash_equal": h1["state"] == h0["state"], "derived_hash_equal": h1["derived"] == h0["derived"],
               "ifc_hash_equal": h1["ifc"] == h0["ifc"], "diff_first_vs_second_export": ifdiff(e1, e2),
               "intent_consistency_bad": b.env.engine("dst.dobim").intent_consistency(),
               "validate_errors": b.run("model.validate", {}, project="dst.dobim")["result"]["errors"]}
        # 가져온 프로젝트에서 계속 저작한다: 이동 → 되돌리기 → 해시 복원, 삭제 → 되돌리기
        mv = b.run("wall.move", {"id": F.W1, "dx": 700, "dy": 0}, project="dst.dobim")
        res["authored_edit_ok"] = mv["ok"]
        res["hash_changes_on_edit"] = b.hashes("dst.dobim")["state"] != h1["state"]
        b.run("history.undo", {}, project="dst.dobim")
        res["undo_restores_hash"] = trip(b.hashes("dst.dobim")) == trip(h1)
        dl = b.run("element.delete", {"id": F.D1}, project="dst.dobim")
        res["authored_delete_ok"] = dl["ok"]
        e3 = b.export("e3.ifc", "dst.dobim")
        # 문 하나가 지워졌으니 원본에서 개구부+문 두 제품이 사라진 차이만 있다
        d3 = ifdiff(e1, e3)
        res["delete_diff"] = {"deleted": d3["ifcdiff_deleted"], "added": d3["ifcdiff_added"]}
        b.run("history.undo", {}, project="dst.dobim")
        res["undo_delete_restores_hash"] = b.hashes("dst.dobim")["state"] == h1["state"]
        # 다시 연 엔진·다른 프로세스에서도 같은 해시
        h_now = b.hashes("dst.dobim")
        b.env.close()
        b2 = Bench(work)
        res["reopen_equal"] = trip(b2.hashes("dst.dobim")) == trip(h_now)
        res["cross_process_equal"] = trip(cli_hashes(b2.ws, "dst.dobim")) == trip(h_now)
        b2.close()
        return res
    finally:
        b.close()


# ---------------------------------------------------------------- T5: 변조
W_MOVE, W_SHAPE, W_PROP = "4" + f"{1:021d}", "4" + f"{2:021d}", "4" + f"{3:021d}"


def tamper(src: Path, dst: Path) -> None:
    """외부 도구처럼 ifcopenshell 로 직접 고친다: 벽 하나는 배치 이동, 다른 벽은 형상 표현(돌출 깊이), 또 다른 벽은 속성만."""
    f = ifcopenshell.open(str(src))
    w = f.by_guid(W_MOVE)
    loc = w.ObjectPlacement.RelativePlacement.Location
    loc.Coordinates = (loc.Coordinates[0] + 500.0, loc.Coordinates[1], loc.Coordinates[2])
    w = f.by_guid(W_SHAPE)
    for rep in w.Representation.Representations:
        for it in rep.Items:
            if it.is_a("IfcExtrudedAreaSolid"):
                it.Depth = it.Depth + 300.0
    w = f.by_guid(W_PROP)
    pset = api_pset.add_pset(f, product=w, name="Pset_WallCommon")
    api_pset.edit_pset(f, pset=pset, properties={"FireRating": "EI120", "Reference": "외부도구"})
    f.write(str(dst))


def t5_tamper(work: Path) -> dict:
    b = Bench(work)
    try:
        build_authored(b, "src.dobim", extra_walls=3)
        b.export("e1.ifc", "src.dobim")
        tamper(b.ws / "e1.ifc", b.ws / "tampered.ifc")
        imp = b.import_ifc("tampered.ifc", "t.dobim")
        assert imp["ok"], imp
        r = imp["result"]
        detached = sorted(b.ids_of("detached", "t.dobim"))
        res = {"elements": r["elements"], "detached_ids_are_the_two": detached == sorted([W_MOVE, W_SHAPE]),
               "detached_reasons": {d["id"]: d["reasons"] for d in r["detached"]},
               "warnings_total": r["warnings_total"], "warns_name_detached": sum(1 for w in r["warnings"] if "분리됨" in w),
               "tamper_vs_original_diff": ifdiff(b.ws / "e1.ifc", b.ws / "tampered.ifc")}
        res["detached_reasons"] = {("move" if k == W_MOVE else "shape"): v for k, v in res["detached_reasons"].items()}
        # 속성만 바뀐 벽은 authored 로 남고, 바뀐 속성이 그대로 읽힌다(속성은 IFC 가 정본이라 가져오기가 곧 반영)
        g = b.run("element.get", {"id": W_PROP}, project="t.dobim")["result"]
        res["prop_only_stays_authored"] = g["origin"] == "authored" and W_PROP not in detached
        res["prop_only_value_visible"] = g["psets"].get("Pset_WallCommon", {}).get("FireRating") == "EI120"
        # 분리된 벽의 형상 커맨드는 정의된 오류로 거절되고, 속성 편집은 허용된다
        frozen = {}
        for name, args in (("wall.move", {"id": W_MOVE, "dx": 100}), ("wall.set", {"id": W_SHAPE, "height": 3500}),
                           ("join.set", {"a": W_MOVE, "b": W_SHAPE}), ("element.delete", {"id": W_MOVE}),
                           ("material.set", {"id": W_SHAPE, "name": "Steel"}), ("door.place", {"host": W_MOVE, "offset": 1500})):
            e = b.run(name, args, project="t.dobim")
            frozen[name] = (e["error"]["code"] if not e["ok"] else "ok")
        res["frozen_errors"] = frozen
        res["frozen_rejected_all"] = all(v == "ELEMENT_FROZEN" for v in frozen.values())
        ps = b.run("pset.set", {"id": W_MOVE, "pset": "Pset_Review", "prop": "Note", "value": "검토"}, project="t.dobim")
        res["detached_pset_edit_ok"] = ps["ok"]
        # 저작 요소는 계속 편집된다. 내보내기는 변조 형상을 그대로 보존하고 저작 요소만 갱신한다
        mv = b.run("wall.move", {"id": W_PROP, "dx": 250, "dy": 0}, project="t.dobim")
        res["authored_move_ok"] = mv["ok"]
        out = b.export("out.ifc", "t.dobim")
        d = ifdiff(b.ws / "tampered.ifc", out, ids=True)
        # 기대 차이: 이동한 벽 1(배치) + 속성을 더한 분리 벽 1(속성). 변조된 두 벽의 형상은 보존된다
        res["export_vs_tampered_diff"] = d
        res["export_diff_changed_ids_ok"] = d["changed_ids"] == sorted([W_PROP, W_MOVE])
        f_t, f_o = ifcopenshell.open(str(b.ws / "tampered.ifc")), ifcopenshell.open(str(out))
        keep = []
        for g_ in (W_MOVE, W_SHAPE):
            a_, c_ = f_t.by_guid(g_), f_o.by_guid(g_)
            keep.append(_geom_sig(a_) == _geom_sig(c_))
        res["tampered_geometry_preserved"] = all(keep)
        # 변경 없이 내보낸 것은 변조 파일과 차이 0 (저작 요소 재투영이 멱등)
        b.run("history.undo", {}, project="t.dobim")   # 방금 pset 편집이 아니라 마지막 변경(wall.move)을 취소
        b.run("history.undo", {}, project="t.dobim")   # 분리 벽의 속성 편집 취소
        out2 = b.export("out2.ifc", "t.dobim")
        res["untouched_export_diff"] = ifdiff(b.ws / "tampered.ifc", out2)
        res["intent_consistency_bad"] = b.env.engine("t.dobim").intent_consistency()
        # 해시 3종: 다시 연 엔진·다른 프로세스도 같다
        h = b.hashes("t.dobim")
        b.env.close()
        b2 = Bench(work)
        res["reopen_equal"] = trip(b2.hashes("t.dobim")) == trip(h)
        res["cross_process_equal"] = trip(cli_hashes(b2.ws, "t.dobim")) == trip(h)
        b2.close()
        return res
    finally:
        b.close()


def _geom_sig(e) -> str:
    """제품의 배치·표현을 문자열로(보존 확인용 정규화)."""
    from dobim_core.ifc_model import Closure
    c = Closure(e.file, set())
    return json.dumps({"p": c.place(e.ObjectPlacement), "r": c.shape(e)["attrs"].get("Representation")}, sort_keys=True)


def t5b_dependents(work: Path) -> dict:
    """의존 요소: 호스트 벽의 배치만 바뀌면 문은 authored 로 남고(문의 배치는 호스트 기준 상대), 개구부를 바꾸면 문이 분리된다."""
    b = Bench(work)
    try:
        build_authored(b, "src.dobim")
        b.export("e1.ifc", "src.dobim")
        f = ifcopenshell.open(str(b.ws / "e1.ifc"))
        w = f.by_guid(F.W3)
        loc = w.ObjectPlacement.RelativePlacement.Location
        loc.Coordinates = (loc.Coordinates[0], loc.Coordinates[1] + 400.0, loc.Coordinates[2])
        d = f.by_guid(F.D1)
        for r in f.get_inverse(d):
            if r.is_a("IfcRelFillsElement"):
                op = r.RelatingOpeningElement
                for rep in op.Representation.Representations:
                    for it in rep.Items:
                        if it.is_a("IfcExtrudedAreaSolid"):
                            it.Depth = it.Depth + 50.0
        f.write(str(b.ws / "t.ifc"))
        imp = b.import_ifc("t.ifc", "t.dobim")
        assert imp["ok"], imp
        det = sorted(b.ids_of("detached", "t.dobim"))
        return {"detached": len(det), "host_wall_detached": F.W3 in det, "door_on_moved_host_authored": F.D2 not in det,
                "door_with_changed_opening_detached": F.D1 in det, "exactly_expected": det == sorted([F.W3, F.D1]),
                "elements": imp["result"]["elements"]}
    finally:
        b.close()


# ---------------------------------------------------------------- 거절 동작
def reject_checks(work: Path, x23: Path | None = None) -> dict:
    b = Bench(work)
    try:
        out = {}
        if x23 is not None:
            b.put(x23, "x23.ifc")
            r = b.import_ifc("x23.ifc", "x.dobim")
            out["ifc2x3"] = r["error"]["code"] if not r["ok"] else "accepted"
            out["ifc2x3_project_left_behind"] = (b.ws / "x.dobim").exists()
        (b.ws / "bad.ifc").write_bytes(b"ISO-10303-21; not really\n")
        r = b.import_ifc("bad.ifc", "y.dobim")
        out["garbage"] = r["error"]["code"] if not r["ok"] else "accepted"
        (b.ws / "z.dobim").write_bytes(b"x")
        r = b.import_ifc("bad.ifc", "z.dobim")
        out["exists"] = r["error"]["code"] if not r["ok"] else "accepted"
        (b.ws / "n.txt").write_text("x")
        r = b.import_ifc("n.txt", "w.dobim")
        out["not_ifc_extension"] = r["error"]["code"] if not r["ok"] else "accepted"
        r = b.import_ifc("../escape.ifc", "v.dobim")
        out["outside_workspace"] = r["error"]["code"] if not r["ok"] else "accepted"
        return out
    finally:
        b.close()
