# SPDX-License-Identifier: MPL-2.0
"""1단계 도면 엔진 기준선·개선 후 산출 (RA-01 코어 범위 입력 + 주석 + 패밀리 배치 + 종단면).

G1 때와 같은 방식으로 도면을 다시 뽑는다(s3lib.build 의 RA-01 코어 범위 모델). 산출 파일은 저장소 밖 폴더(--out)에 둔다.

사용:  PYTHONPATH=<worktree>/spikes/s1-core/src  <python> run_baseline.py --out <폴더> [--sheets]
  기본          : base/(기본 7뷰) annot/(치수·태그 포함 7뷰) fam/(창·기둥 배치 + 전체높이 치수) xsec/(종단면)  각 SVG·PDF
  --sheets      : 위에 더해 sheets/ 아래에 시트(A3·A1) SVG·PDF(한글 포함)를 뽑는다
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s3-drawings"))
sys.path.insert(0, str(HERE.parent / "s1-core" / "tools"))

import s3lib  # noqa: E402


def ok(r):
    assert r.get("ok"), r
    return r["result"]


def export(sess, name, args, out_path: Path):
    """확인 토큰 2단계 호출로 내보낸다. 응답 result 를 돌려준다."""
    a = {**args, "path": out_path.name, "overwrite": True}
    r = sess.run(name, a)
    tok = r["error"]["details"]["confirm_token"]
    return ok(sess.run(name, a, confirm=tok))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheets", action="store_true")
    o = ap.parse_args()
    out = Path(o.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir(exist_ok=True)
    proj = work / "ra01.dobim"
    if proj.exists():
        proj.unlink()
    sess, ids, views = s3lib.build(proj)
    log = {"exports": []}

    def exp_views(sub, keys):
        d = out / sub
        d.mkdir(exist_ok=True)
        for k in keys:
            for ext in ("svg", "pdf"):
                # 워크스페이스는 work/ 이므로 상대 경로가 아니라 한 단계 위로 쓸 수 없다 -> 작업 폴더에 쓰고 옮긴다
                r = export(sess, "view.export", {"id": views[k]}, work / f"{sub}_{k}.{ext}")
                (d / f"{k}.{ext}").write_bytes((work / f"{sub}_{k}.{ext}").read_bytes())
                log["exports"].append({"dir": sub, "view": k, "ext": ext, **{x: r[x] for x in ("bytes", "segments", "fills", "annotations", "non_ascii_dropped")}})

    keys = list(views)
    exp_views("base", keys)
    # 주석: 평면 치수 3개·태그 4개, 단면·입면 높이 치수
    w1, w2 = ids["ext-1F"][0], ids["ext-1F"][1]
    dim = lambda view, a, b, **kw: ok(sess.run("annot.dim.create", {"view": views[view], "a": a, "b": b, **kw}))   # noqa: E731
    dim("plan-1F", f"wall:{w1}#trim.start", f"wall:{w1}#trim.end")
    dim("plan-1F", f"wall:{w2}#trim.start", f"wall:{w2}#trim.end", offset=900.0)
    for k in ("section-BB", "elev-S"):
        dim(k, f"level:{ids['1F']}#elevation", f"level:{ids['2F']}#elevation", at=[-600.0, 0.0])
    for t in (ids["D-01"], ids["D-02"], ids["D-03"], ids["ext-1F"][0]):
        ok(sess.run("annot.tag.create", {"view": views["plan-1F"], "target": t, "fields": ["name"]}))
    exp_views("annot", keys)
    # 패밀리 배치(창 2·기둥 1)와 종단면 시도: 실패해도 기록만 한다
    for i, (host, off) in enumerate(((ids["ext-1F"][1], 2000.0), (ids["ext-1F"][2], 4000.0)), 1):
        r = sess.run("family.place", {"id": "0" * 20 + f"w{i}", "family": "window.fixed", "host": host, "offset": off})
        log.setdefault("family", []).append({"window": i, "ok": bool(r.get("ok")), "err": (r.get("error") or {}).get("message")})
    r = sess.run("family.place", {"id": "0" * 20 + "c1", "family": "column.section", "type_values": {"section": "round"}, "level": ids["1F"], "position": [4000.0, 4000.0]})
    log["family"].append({"column": 1, "ok": bool(r.get("ok")), "err": (r.get("error") or {}).get("message")})
    exp_views("fam", ["plan-1F", "section-BB"])
    vc = ok(sess.run("view.create", {"id": "4" + "0" * 20 + "9", "name": "A-A 종단면", "type": "section", "p1": [2000.0, -1000.0], "p2": [2000.0, 9000.0]}))
    views["xsec-AA"] = vc["id"]
    exp_views("xsec", ["xsec-AA"])

    if o.sheets:
        d = out / "sheets"
        d.mkdir(exist_ok=True)
        plans = [views["plan-1F"], views["plan-2F"]]
        elevs = [views[f"elev-{x}"] for x in "NESW"]
        spec = {"project_name": "RA-01 예시 주택", "date": "2026-11-15"}
        for nm, a in (("A3-plans", {"views": plans, "paper": "A3", "sheet_name": "1·2층 평면도", "sheet_number": "A-101"}),
                      ("A1-all", {"views": plans + [views["section-BB"], views["xsec-AA"]] + elevs, "paper": "A1", "sheet_name": "평면도·단면도·입면도", "sheet_number": "A-001"})):
            for ext in ("svg", "pdf"):
                r = export(sess, "sheet.export", {**spec, **a}, work / f"sheet_{nm}.{ext}")
                (d / f"{nm}.{ext}").write_bytes((work / f"sheet_{nm}.{ext}").read_bytes())
                log["exports"].append({"dir": "sheets", "sheet": nm, "ext": ext, "bytes": r["bytes"], "font_embedded": r["font_embedded"],
                                       "non_ascii_dropped": r["non_ascii_dropped"], "views": len(r["views"])})
        # 혼합 축척 시트: 북측 입면만 1:50 으로 바꿔 한 장에 올린다(표제란 '도면참조', 뷰별 S=1:N)
        ok(sess.run("view.set", {"id": views["elev-N"], "scale": 50}))
        for ext in ("svg", "pdf"):
            r = export(sess, "sheet.export", {**spec, "views": [views["plan-1F"], views["elev-N"]], "paper": "A3", "sheet_name": "혼합 축척 예", "sheet_number": "A-201", "north_angle": 30.0},
                       work / f"sheet_A3-mixed.{ext}")
            (d / f"A3-mixed.{ext}").write_bytes((work / f"sheet_A3-mixed.{ext}").read_bytes())
            log["exports"].append({"dir": "sheets", "sheet": "A3-mixed", "ext": ext, "bytes": r["bytes"], "font_embedded": r["font_embedded"],
                                   "non_ascii_dropped": r["non_ascii_dropped"], "views": len(r["views"])})
    (out / "run.json").write_text(json.dumps({"views": views, "ids": ids, **log}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
