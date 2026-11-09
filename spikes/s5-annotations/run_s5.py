# SPDX-License-Identifier: MPL-2.0
"""S5 측정 실행기.

    python run_s5.py scenarios   RA-01 에 주석 71개를 두고 편집 시나리오 E1~E8 (results/s5_scenarios.json, out/*.svg)
    python run_s5.py fuzz        임의 편집열 시드 여러 개 (results/s5_fuzz.json)
    python run_s5.py mutants     변이 15종(음성 대조) (results/s5_mutants.json)
    python run_s5.py all
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import s5lib as L
from dobim_core import annot as AN

HERE = Path(__file__).resolve().parent
RES, OUT = HERE / "results", HERE / "out"


def dump(name, obj):
    RES.mkdir(exist_ok=True)
    (RES / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def unexpected(c: dict) -> int:
    """합격 기준의 '고아': 대상이 살아 있는데 끊긴 것 + 이유가 틀린 것 + 대상이 없는데 값이 나오는 것 + 그 밖의 불일치 + 뷰 없는 주석."""
    return sum(c.get(k, 0) for k in ("false_orphan", "reason_mismatch", "silent_alive", "other_mismatch", "dangling_view", "flag_mismatch"))


def step(h, label, steps, notes=""):
    snap0 = L.snapshot(h.eng)
    m = L.edit_and_measure(h, steps)
    chk = h.check()
    ac = h.run("annot.check", {"limit": 1000})["result"]
    snap1 = L.snapshot(h.eng)
    c = chk["counts"]
    orphans_cmd = sum(ac["orphan"].values())
    return {"label": label, "steps": [f"{n} {json.dumps(a, ensure_ascii=False)[:90]}" for n, a in steps], "edit_ok": m["ok"], "notes": notes,
            "annots": c.get("total", 0), "ok_match": c.get("ok_match", 0), "orphan_expected": c.get("orphan_expected", 0),
            "orphans_by_reason": ac["orphan"], "unexpected_orphans": unexpected(c), "value_errors": c.get("value_error", 0),
            "annot_check_cmd_orphans": orphans_cmd, "annot_check_matches_oracle": orphans_cmd == c.get("orphan_expected", 0) + c.get("false_orphan", 0)
            + c.get("reason_mismatch", 0), "changed_by_edit": len(L.stable_diff(snap0, snap1)),
            "recompute": {k: m[k] for k in ("annot_nodes_total", "annot_recomputed", "other_recomputed", "edit_ms", "refresh_ms")},
            "bad": chk["bad"]}


def scenarios():
    work = Path(tempfile.mkdtemp(prefix="s5-"))
    sess, ids, views = L.build_ra01(work / "ra01.dobim")
    h = L.Harness(sess)
    eng = sess.eng
    R = AN.make_ref
    out = {"model": "RA-01 코어 범위(레벨 2, 벽 14, 문 5, 조인, 뷰 7)", "scenarios": []}

    # ---- E0: 배치
    hash_before = eng.hashes()
    made = L.place_annotations(h, ids, views)
    hash_after = eng.hashes()
    cats: dict[str, int] = {}
    for _, cat in made:
        cats[cat] = cats.get(cat, 0) + 1
    base = h.check()
    kinds = {k: len(eng.st.by_kind.get(k, ())) for k in ("dim", "tag")}
    out["placement"] = {"total": len(made), "dims": kinds["dim"], "tags": kinds["tag"], "by_category": cats,
                        "ifc_hash_unchanged": hash_before["ifc"] == hash_after["ifc"],
                        "state_hash_changed": hash_before["state"] != hash_after["state"],
                        "derived_hash_changed": hash_before["derived"] != hash_after["derived"],
                        "baseline": base["counts"], "baseline_unexpected": unexpected(base["counts"])}
    base_snap = L.snapshot(eng)
    pre = {"all": base_snap}

    def add(r):
        out["scenarios"].append(r)
        return r

    ew, ed, iw = ids["ext-1F"], None, ids["int-1F"]
    # ---- E1: 외벽 600mm 이동 (+ undo/redo 복원)
    r = add(step(h, "E1 외벽(1F 동쪽) 600mm 이동", [("wall.move", {"id": ew[1], "dx": 600.0, "dy": 0.0})]))
    after_e1 = L.snapshot(eng)
    h.run("history.undo", {})
    r["undo_restores_exactly"] = L.snapshot(eng) == base_snap
    h.run("history.redo", {})
    r["redo_restores_exactly"] = L.snapshot(eng) == after_e1
    r["check_after_redo"] = unexpected(h.check()["counts"]) == 0

    # ---- E2: 문 폭 900 -> 1000, 문 이동, 문 이름 변경
    r = add(step(h, "E2 문 폭 900→1000, 문 이동, 문턱 높이·이름 변경",
                 [("door.set", {"id": ids["D-01"], "width": 1000.0}), ("door.set", {"id": ids["D-02"], "offset": 2140.0}),
                  ("door.set", {"id": ids["D-03"], "sill": 100.0, "name": "D-03A"})]))
    after_e2 = L.snapshot(eng)

    # ---- E3: 벽 삭제(문 연쇄) -> 조인 변경 -> undo 전부
    r3a = add(step(h, "E3a 내부벽 삭제(호스팅된 문 D-02 연쇄 삭제)", [("element.delete", {"id": iw[0]})]))
    r3b = add(step(h, "E3b 조인 해제(내부벽3-남쪽 외벽 T 조인: 내부벽3 길이가 달라진다)", [("join.clear", {"a": iw[2], "b": ew[0]})]))
    r3c = add(step(h, "E3c 조인 해제(내부벽2-동쪽 외벽)", [("join.clear", {"a": iw[1], "b": ew[1]})]))
    undone = [h.run("history.undo", {})["ok"] for _ in range(3)]
    snap = L.snapshot(eng)
    r3d = {"label": "E3d undo ×3 → E2 직후 상태 복원", "undo_ok": undone, "restores_exactly": snap == after_e2,
           "unexpected_orphans": unexpected(h.check()["counts"]), "value_errors": h.check()["counts"].get("value_error", 0)}
    add(r3d)

    # ---- E4: 벽 분할(안정 참조 규칙) -> 이중 분할 -> 후계 삭제 -> undo
    w0 = ew[0]
    pre_split = L.snapshot(eng)
    r4a = add(step(h, "E4a 1F 남쪽 외벽을 x=6000 에서 분할(문 D-01 이 끝 쪽 벽으로 이동)", [("wall.split", {"id": w0, "at": 6000.0, "new_id": L._i("1", 90001)})],
                   "분할은 형상을 바꾸지 않으므로 모든 치수 값이 그대로여야 한다"))
    post_split = L.snapshot(eng)
    dims_changed = [d for d in L.stable_diff(pre_split, post_split) if eng.st.el[d[0]]["kind"] == "dim"]
    r4a["dim_values_changed_by_split"] = len(dims_changed)
    r4a["tags_changed_by_split"] = len([d for d in L.stable_diff(pre_split, post_split) if eng.st.el[d[0]]["kind"] == "tag"])
    r4a["door_D01_host_after"] = eng.st.inn[("hosts", ids["D-01"])][0] == L._i("1", 90001)
    r4b = add(step(h, "E4b 같은 벽을 x=2500 에서 한 번 더 분할(후계 사슬 W→N2→N1)", [("wall.split", {"id": w0, "at": 2500.0, "new_id": L._i("1", 90002)})]))
    r4b["dim_values_changed_by_split"] = len([d for d in L.stable_diff(post_split, L.snapshot(eng)) if eng.st.el[d[0]]["kind"] == "dim"])
    snap_e4b = L.snapshot(eng)
    r4c = add(step(h, "E4c 사슬 끝 벽 N1 삭제(문 D-01 연쇄 삭제)", [("element.delete", {"id": L._i("1", 90001)})],
                   "W 의 끝 참조는 successor_deleted, 문 D-01 참조는 target_deleted 로 고아"))
    h.run("history.undo", {})
    add({"label": "E4d N1 삭제 undo → 고아 복귀", "restores_exactly": L.snapshot(eng) == snap_e4b,
         "unexpected_orphans": unexpected(h.check()["counts"]), "value_errors": h.check()["counts"].get("value_error", 0)})

    # ---- E5: 고아 수동 복구(annot.rebind)
    r5a = add(step(h, "E5a 내부벽3 삭제(그 벽을 가리키는 치수·태그가 고아)", [("element.delete", {"id": iw[2]})]))
    orphan_dims = [a for a, st_ in L.snapshot(eng).items() if st_[1] == "orphan"]
    cmd = {"orphan_before": len(orphan_dims)}
    fixed = 0
    for a in orphan_dims:
        p = eng.st.el[a]["params"]
        if eng.st.el[a]["kind"] == "dim":           # 지워진 벽을 가리키는 참조만 같은 역할의 서쪽 외벽(평행·같은 레벨) 참조로 바꾼다
            sub = lambda ref: ref.replace(iw[2], ew[3])
            if h.run("annot.rebind", {"id": a, "a": sub(p["a"]), "b": sub(p["b"])})["ok"]:
                fixed += 1
        # 태그는 일부러 복구하지 않는다: 복구하지 않은 고아는 계속 목록에 남아야 한다
    chk = h.check()
    cmd.update({"rebound_ok": fixed, "orphan_after": len([1 for s in L.snapshot(eng).values() if s[1] == "orphan"]),
                "unexpected_orphans": unexpected(chk["counts"]), "value_errors": chk["counts"].get("value_error", 0)})
    r5a["manual_recovery"] = cmd
    # 복구하지 못한 것은 계속 고아로 남는다(조용히 사라지지 않음)
    r5a["remaining_orphans_listed_by_annot_check"] = h.run("annot.check", {"limit": 1000})["result"]["problems"]

    # ---- E6: 축선 방향 반전(한계 사례: 역할 이름이 방향에 의존)
    pre = L.snapshot(eng)
    r6 = add(step(h, "E6 2F 남쪽 외벽 축선 방향 반전(wall.set axis)", [("wall.set", {"id": ids["ext-2F"][0], "axis": [12000.0, 0.0, 0.0, 0.0]})],
                  "참조는 끊기지 않고 오라클과도 일치하지만, 역할 이름(시작·끝·왼쪽·오른쪽)이 가리키는 물리적 위치는 뒤바뀐다(한계)"))
    r6["dims_whose_value_changed"] = len([d for d in L.stable_diff(pre, L.snapshot(eng)) if eng.st.el[d[0]]["kind"] == "dim"])
    ac6 = h.run("annot.check", {"limit": 1000})["result"]
    r6["direction_warnings"] = ac6["warnings"].get("direction_changed", 0)                    # 값이 조용히 바뀌지 않고 경고로 알려진다
    r6["warned_items_listed"] = len([i for i in ac6["items"] if i.get("warnings")])
    h.run("history.undo", {})
    r6["undo_clears_warnings"] = h.run("annot.check", {"limit": 1000})["result"]["warnings"].get("direction_changed", 0) == 0
    h.run("history.redo", {})

    # ---- E6b: 분할 뒤 자기 끝 참조(own.end)와 원래 끝 참조(axis.end)의 값 차이
    wsrc = ids["ext-2F"][2]
    dpair = [h.run("annot.dim.create", {"view": views["plan-2F"], "a": R("wall", wsrc, "axis.start"), "b": R("wall", wsrc, b_role), "offset": 600.0})["result"]["id"]
             for b_role in ("axis.end", "own.end")]
    ax = eng.st.el[wsrc]["params"]["axis"]
    full = ((ax[2] - ax[0]) ** 2 + (ax[3] - ax[1]) ** 2) ** 0.5
    nsplit = h.run("wall.split", {"id": wsrc, "at": round(full / 2, 3), "new_id": L._i("1", 90003)})
    vals = [L.snapshot(eng)[d][2] for d in dpair]
    add({"label": "E6b 벽 분할 뒤 axis.end(원래 끝)는 전체 길이, own.end(자기 끝)는 조각 길이", "split_ok": nsplit["ok"], "full_length": full,
         "dim_axis_end": vals[0], "dim_own_end": vals[1], "axis_end_unchanged": abs(vals[0] - full) < 1e-6, "own_end_is_half": abs(vals[1] - full / 2) < 1e-3,
         "unexpected_orphans": unexpected(h.check()["counts"])})
    h.run("history.undo", {})
    h.run("annot.delete", {"id": dpair[0]})
    h.run("annot.delete", {"id": dpair[1]})

    # ---- E7: 뷰 삭제 연쇄
    n_before = len(L.annot_ids(eng))
    vdel = h.run("view.delete", {"id": views["plan-2F"]})
    gone = vdel["result"]["deleted"]
    c7 = h.check()["counts"]
    h.run("history.undo", {})
    r7 = {"label": "E7 2F 평면 뷰 삭제 → 그 뷰의 주석 연쇄 삭제(응답에 나열) → undo", "deleted_listed": len(gone) - 1,
          "annots_before": n_before, "annots_after_delete": c7.get("total", 0), "dangling_view": c7.get("dangling_view", 0),
          "annots_after_undo": len(L.annot_ids(eng)), "undo_restores_count": len(L.annot_ids(eng)) == n_before,
          "unexpected_orphans": unexpected(h.check()["counts"])}
    add(r7)

    # ---- E8: SVG 내보내기(한글 포함, 고아 표시)
    OUT.mkdir(exist_ok=True)
    svgs = {}
    for key in ("plan-1F", "plan-2F", "section-BB", "elev-S"):
        name = f"s5-{key}.svg"
        first = h.sess.run("view.export", {"id": views[key], "path": name})
        tok = first.get("error", {}).get("details", {}).get("confirm_token")
        r_ = h.sess.run("view.export", {"id": views[key], "path": name}, confirm=tok)
        text = (work / name).read_text(encoding="utf-8") if r_["ok"] else ""
        (OUT / name).write_text(text, encoding="utf-8")
        svgs[key] = {"ok": r_["ok"], "annotations": (r_.get("result") or {}).get("annotations"), "bytes": (r_.get("result") or {}).get("bytes"),
                     "dim_groups": text.count('class="dim"'), "tag_groups": text.count('class="tag"'), "orphan_groups": text.count('class="orphan"'),
                     "has_korean_text": any("가" <= ch <= "힣" for ch in text)}
    out["export"] = svgs
    # ---- E9: 분할로 문이 옮겨진 뒤 IFC 투영(model.hash) -> undo -> model.hash (검수가 찾은 INTERNAL 결함의 직접 재현) + 이력 id 재사용 거절
    h.run("model.hash", {})
    nw = {"ok": False, "result": {"doors_moved": []}}
    split_args = None
    for d_ in sorted(eng.st.by_kind.get("door", ())):                  # 문이 끝 쪽 조각으로 옮겨지는 분할을 찾는다
        hw = eng.st.inn[("hosts", d_)][0]
        dp = eng.st.el[d_]["params"]
        for gap in (100.0, 300.0, 600.0):
            at_ = round(dp["offset"] - dp["width"] / 2 - gap, 3)
            nw = h.run("wall.split", {"id": hw, "at": at_, "new_id": L._i("1", 90004)})
            if nw["ok"]:
                split_args = (hw, at_)
                break
        if nw["ok"] and nw["result"]["doors_moved"]:
            break
        if nw["ok"]:
            h.run("history.undo", {})
            nw = {"ok": False, "result": {"doors_moved": []}}
    ok_flush1 = h.run("model.hash", {})["ok"]
    moved = nw["ok"] and bool(nw["result"]["doors_moved"])
    ua = h.run("history.undo", {})
    ok_flush2 = h.run("model.hash", {})
    from dobim_core.engine import Engine
    ref = Engine(sess.path, "ifc-ref")
    same = ref.ifc_hash() == eng.ifc_hash()
    ref.close()
    h.run("history.redo", {})
    ok_flush3 = h.run("model.hash", {})["ok"]
    ref = Engine(sess.path, "ifc-ref")
    same2 = ref.ifc_hash() == eng.ifc_hash()
    ref.close()
    h.run("history.undo", {})
    # 이력 id 재사용: 분할 -> 후계 삭제 -> 같은 id 로 다시 분할(예전에는 succ(X->X) 순환으로 annot.check·model.hash 가 INTERNAL)
    h.run("wall.split", {"id": split_args[0], "at": split_args[1], "new_id": L._i("1", 90005)})
    h.run("element.delete", {"id": L._i("1", 90005)})
    reuse = h.run("wall.split", {"id": split_args[0], "at": split_args[1], "new_id": L._i("1", 90005)})
    after_reuse = [h.run(c, {})["ok"] for c in ("annot.check", "annot.list", "model.hash")]
    recreate = h.run("wall.create", {"id": L._i("1", 90005), "level": eng.st.out[("on_level", split_args[0])][0], "axis": [0.0, 0.0, 1000.0, 0.0]})
    add({"label": "E9 분할(문 이동) → IFC 투영 → undo → 투영 → redo → 투영", "door_moved_to_new_wall": moved, "flush_after_split": ok_flush1,
         "undo_ok": ua["ok"], "flush_after_undo": ok_flush2["ok"], "ifc_equals_reopened_after_undo": same, "flush_after_redo": ok_flush3,
         "ifc_equals_reopened_after_redo": same2, "reused_succ_id_rejected": (not reuse["ok"]) and reuse["error"]["code"] == "CONSTRAINT",
         "recreate_with_history_id_rejected": (not recreate["ok"]) and recreate["error"]["code"] == "CONSTRAINT",
         "annot_check_list_hash_ok_after_reuse_attempt": all(after_reuse),
         "succ_cycle_free": L.Fuzz.succ_cycles(type("X", (), {"eng": eng})()) == 0})
    # 요약
    sc = [s for s in out["scenarios"] if "unexpected_orphans" in s]
    out["summary"] = {"annotations": len(made), "scenarios": len(out["scenarios"]),
                      "total_unexpected_orphans": sum(s["unexpected_orphans"] for s in sc),
                      "total_value_errors": sum(s.get("value_errors", 0) for s in sc),
                      "expected_orphans_seen": sum(s.get("orphan_expected", 0) for s in sc)}
    dump("s5_scenarios.json", out)
    sess.close()
    return out


def fuzz(seeds=(11, 12, 13, 14, 15, 16), n=400):
    work = Path(tempfile.mkdtemp(prefix="s5f-"))
    runs = []
    for sd in seeds:
        f = L.Fuzz(work, sd)
        runs.append(f.run(n))
        f.close()
    tot = {}
    for r in runs:
        for k, v in r["stats"].items():
            if isinstance(v, (int, float)):
                tot[k] = tot.get(k, 0) + v
    out = {"seeds": list(seeds), "steps_per_seed": n, "runs": runs, "totals": tot,
           "failures": sum(r["n_failures"] for r in runs)}
    dump("s5_fuzz.json", out)
    return out


def mutants(seeds=(21, 22, 23, 24, 25, 26), n=300, cap=None):
    work = Path(tempfile.mkdtemp(prefix="s5m-"))
    rows = []
    # 변이 없는 기준
    clean = []
    for sd in seeds:
        f = L.Fuzz(work, sd)
        r = f.run(n)
        clean.append(r["n_failures"])
        f.close()
    for name in L.MUTANTS:
        caught_at, detail = None, None
        t0 = time.perf_counter()
        for sd in seeds:
            with L.mutation(name):
                f = L.Fuzz(work, sd + 100)
                r = f.run(n, stop_on_fail=True)
                f.close()
            if r["n_failures"]:
                caught_at = (sd + 100, r["stats"]["steps"])
                detail = r["failures"][0]
                detail = {k: v for k, v in detail.items() if k != "bad"} | {"first_bad": [str(b)[:150] for b in detail.get("bad", [])[:1]]}
                break
        rows.append({"mutant": name, "caught": caught_at is not None, "seed_steps": caught_at, "how": detail, "sec": round(time.perf_counter() - t0, 1)})
    out = {"clean_failures_per_seed": clean, "mutants": rows, "caught": sum(r["caught"] for r in rows), "total": len(rows)}
    dump("s5_mutants.json", out)
    return out


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("scenarios", "all"):
        o = scenarios()
        print(json.dumps(o["summary"], ensure_ascii=False), json.dumps(o["placement"]["baseline"]))
    if what in ("fuzz", "all"):
        o = fuzz()
        print("fuzz", o["totals"], "failures", o["failures"])
    if what in ("mutants", "all"):
        o = mutants()
        print("mutants", o["caught"], "/", o["total"], o["clean_failures_per_seed"])
