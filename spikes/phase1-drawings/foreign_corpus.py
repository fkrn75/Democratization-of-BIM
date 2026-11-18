# SPDX-License-Identifier: MPL-2.0
"""1단계 외래 IFC 도면 코퍼스: 표본 5건의 재생성 차등 시험 도구 (설계: elements-design.md 29절, 출처표: corpus/SOURCES.md).

표본마다 (1) IFC 를 가져오고 (2) 정해진 뷰(층마다 평면, 입면 4면, 단면 2개)를 만들어 (3) 선분 해시·SVG 해시를 뽑는다.
같은 입력이면 같은 해시여야 하고(결정적 출력), 기준선(corpus/baseline.json)과 같아야 한다(재생성 차등).
수정 없는 왕복: 가져오기 -> 내보내기(a) -> a 가져오기 -> 내보내기(b) 에서 a 와 b 의 바이트 해시·모델 해시·도면 해시가 같아야 한다.

표본 위치
- 저장소 표본: corpus/ 의 buildingSMART 공개 표본(CC-BY-4.0)과 spikes/s0-baseline 의 RA-01(이 저장소에서 만든 파일, MPL-2.0).
- 로컬 전용 표본(저장소·외부로 복사하지 않는다): 환경 변수 DOBIM_REVIT_DIR 의 RA-01_IFC4RV.ifc(Revit 내보내기). 없으면 건너뛴다.
  로컬 표본의 기준선은 저장소에 두지 않는다: DOBIM_FOREIGN_LOCAL_BASELINE(JSON 경로)이 있으면 비교하고, 없으면 결정성·왕복만 본다.

사용:
  python foreign_corpus.py --out <폴더>            표본마다 해시 JSON·SVG 를 <폴더>/<표본>/ 에 쓰고 요약 JSON 을 <폴더>/summary.json 에
  python foreign_corpus.py --write-baseline        저장소 표본의 기준선 corpus/baseline.json 을 다시 쓴다(의도한 도면 변경 때만)
  python foreign_corpus.py --local-baseline <경로>  로컬 전용 표본의 기준선을 저장소 밖 <경로> 에 쓴다(DOBIM_FOREIGN_LOCAL_BASELINE 로 시험이 읽는다)
  python foreign_corpus.py --sample BA --json      표본 하나의 해시를 표준 출력에(다른 프로세스 결정성 확인용)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "spikes" / "s1-core" / "src"))

from dobim_core import dispatch  # noqa: E402
from dobim_core import drawing as D  # noqa: E402
from dobim_core import queries as Q  # noqa: E402

CORPUS = HERE / "corpus"
BASELINE = CORPUS / "baseline.json"
PROJECT = "p.dobim"


def _revit():
    d = os.environ.get("DOBIM_REVIT_DIR")
    p = Path(d) / "RA-01_IFC4RV.ifc" if d else None
    return p if p is not None and p.is_file() else None


# (표본 id, 경로를 돌려주는 함수, 저장소 포함 여부). 출처·라이선스는 corpus/SOURCES.md
SAMPLES = (
    ("BA", lambda: CORPUS / "Building-Architecture.ifc", True),
    ("BS", lambda: CORPUS / "Building-Structural.ifc", True),
    ("RA01", lambda: ROOT / "spikes" / "s0-baseline" / "RA-01.ifc", True),
    ("RA01-bonsai", lambda: ROOT / "spikes" / "s0-baseline" / "results" / "bonsai" / "RA-01.bonsai.ifc", True),
    ("RA01-revit", _revit, False),
)


def sample_path(sid: str) -> Path | None:
    for s, fn, _repo in SAMPLES:
        if s == sid:
            p = fn()
            return p if p is not None and Path(p).is_file() else None
    raise KeyError(sid)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _destructive(env, name, args):
    r = dispatch.run(env, name, args, project=PROJECT)
    if not r["ok"] and r["error"]["code"] == "CONFIRM_REQUIRED":
        r = dispatch.run(env, name, args, project=PROJECT, confirm=r["error"]["details"]["confirm_token"])
    if not r["ok"]:
        raise RuntimeError(f"{name}: {r['error']}")
    return r


def view_specs(eng) -> list[dict]:
    """표본의 정해진 뷰 목록(입력만으로 결정된다). 평면: 외래 층마다(고도·id 순) 절단 1200·보이는 깊이 1200.
    입면: N·E·S·W. 단면: heavy 범주(벽·슬래브 등) 경계 상자 중심을 지나는 x 방향·y 방향 각 1개."""
    g = eng.g
    fs = g.q(D._raw_foreign)
    storeys = sorted((fg[1], e) for e in fs if (fg := g.q(Q.fgeom, e)) is not None and fg[0] == "storey")
    out = [{"name": f"plan-{i}", "type": "plan", "level": e, "cut_height": 1200, "view_depth": 1200} for i, (_z, e) in enumerate(storeys)]
    out += [{"name": f"elev-{d}", "type": "elevation", "dir": d} for d in "NESW"]
    bbs = [i[5] for e in fs if (i := g.q(D.foreign_info, e)) is not None and i[0] == "heavy"]
    if bbs:
        x0, y0 = min(b[0] for b in bbs), min(b[1] for b in bbs)
        x1, y1 = max(b[3] for b in bbs), max(b[4] for b in bbs)
        cx, cy = round((x0 + x1) / 2, 1), round((y0 + y1) / 2, 1)
        out.append({"name": "sec-x", "type": "section", "p1": [round(x0 - 1000, 1), cy], "p2": [round(x1 + 1000, 1), cy], "depth": round(y1 - cy + 1000, 1)})
        out.append({"name": "sec-y", "type": "section", "p1": [cx, round(y0 - 1000, 1)], "p2": [cx, round(y1 + 1000, 1)], "depth": round(x1 - cx + 1000, 1)})
    return out


def render_project(env, out_dir: Path | None = None) -> dict:
    """열린 프로젝트(PROJECT)에 정해진 뷰를 만들고 뷰마다 해시를 뽑는다. 이미 같은 이름의 뷰가 있으면(왕복으로 되살아난 뷰) 그 뷰를 쓴다."""
    eng = env.engine(PROJECT)
    have = {eng.st.el[v]["params"]["name"]: v for v in eng.st.by_kind.get("view", ())}
    res = {}
    for k, spec in enumerate(view_specs(eng)):
        vid = have.get(spec["name"])
        if vid is None:
            vid = f"4F{k:020d}"
            r = dispatch.run(env, "view.create", {"id": vid, **spec}, project=PROJECT)
            if not r["ok"]:
                raise RuntimeError(f"view.create {spec['name']}: {r['error']}")
        lines = eng.g.q(D.view_lines, vid)
        svg_name = f"{spec['name']}.svg"
        _destructive(env, "view.export", {"id": vid, "path": svg_name})
        svg = (env.workspace / svg_name).read_bytes()
        if out_dir is not None:
            (out_dir / svg_name).write_bytes(svg)
        summ = D.view_summary(lines)
        res[spec["name"]] = {"lines_sha256": _sha(json.dumps(lines, separators=(",", ":")).encode("ascii")), "svg_sha256": _sha(svg),
                             "segments": summ["segments"], "by_class": summ["by_class"], "omitted": eng.g.q(D.view_omitted, vid)}
    return res


def _import(env, src: Path, name: str = "in.ifc"):
    shutil.copyfile(src, env.workspace / name)
    r = dispatch.run(env, "ifc.import", {"source": name, "path": PROJECT})
    if not r["ok"]:
        raise RuntimeError(f"ifc.import: {r['error']}")
    return r["result"]


def render_sample(src: Path, out_dir: Path | None = None) -> dict:
    """표본 하나: 가져오기 -> 뷰 해시. 작업 폴더는 임시."""
    ws = Path(tempfile.mkdtemp(prefix="dobim-foreign-"))
    env = dispatch.Env(ws, "corpus")
    try:
        info = _import(env, src)
        views = render_project(env, out_dir)
        return {"input_sha256": _sha(Path(src).read_bytes()), "elements": info["elements"], "views": views}
    finally:
        env.close()
        shutil.rmtree(ws, ignore_errors=True)


def roundtrip_sample(src: Path) -> dict:
    """수정 없는 왕복: 가져오기 -> 내보내기 a -> a 가져오기(새 작업 폴더) -> 내보내기 b. a·b 바이트 해시, 모델 해시, 도면 해시를 비교한다.
    도면 뷰는 첫 프로젝트에서 만든 뒤 내보낸다(정의가 IFC 에 실려 왕복한다: 단위가 mm 일 때). 단위가 mm 가 아니면 되살리지 않으므로 다시 만든다."""
    out = {}
    tmp = [Path(tempfile.mkdtemp(prefix="dobim-foreign-rt-")) for _ in range(2)]
    envs = [dispatch.Env(t, "corpus") for t in tmp]
    try:
        _import(envs[0], src)
        v1 = render_project(envs[0])
        _destructive(envs[0], "ifc.export", {"path": "a.ifc"})
        h1 = dispatch.run(envs[0], "model.hash", {}, project=PROJECT)["result"]
        a = (tmp[0] / "a.ifc").read_bytes()
        _import(envs[1], tmp[0] / "a.ifc", "a.ifc")
        _destructive(envs[1], "ifc.export", {"path": "b.ifc"})   # 되살아난 뷰가 그대로인 상태(아무 커맨드도 실행하지 않음)
        h2 = dispatch.run(envs[1], "model.hash", {}, project=PROJECT)["result"]
        b = (tmp[1] / "b.ifc").read_bytes()
        v2 = render_project(envs[1])
        out = {"export_a_sha256": _sha(a), "export_b_sha256": _sha(b), "exports_equal": a == b,
               "model_hash_equal": {k: h1[k] == h2[k] for k in ("state", "derived", "ifc")},
               "views_equal": v1 == v2, "views": len(v1)}
    finally:
        for e in envs:
            e.close()
        for t in tmp:
            shutil.rmtree(t, ignore_errors=True)
    return out


def _views_only(r: dict) -> dict:
    return {"input_sha256": r["input_sha256"], "views": {k: {"lines_sha256": v["lines_sha256"], "svg_sha256": v["svg_sha256"]} for k, v in r["views"].items()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="산출 폴더(표본별 SVG·해시)")
    ap.add_argument("--write-baseline", action="store_true", help="저장소 표본의 기준선 corpus/baseline.json 을 다시 쓴다")
    ap.add_argument("--local-baseline", help="로컬 전용 표본의 기준선 JSON 을 이 경로(저장소 밖)에 쓴다")
    ap.add_argument("--sample", help="표본 id 하나만")
    ap.add_argument("--json", action="store_true", help="해시 JSON 을 표준 출력으로")
    ap.add_argument("--roundtrip", action="store_true", help="수정 없는 왕복도 확인한다")
    a = ap.parse_args(argv)
    ids = [a.sample] if a.sample else [s for s, _fn, _r in SAMPLES]
    summary = {}
    for sid in ids:
        src = sample_path(sid)
        if src is None:
            summary[sid] = {"skipped": "표본 파일이 없다(로컬 전용 표본은 환경 변수로 지정)"}
            continue
        od = None
        if a.out:
            od = Path(a.out) / sid
            od.mkdir(parents=True, exist_ok=True)
        r = render_sample(src, od)
        if a.roundtrip:
            r["roundtrip"] = roundtrip_sample(src)
        if od is not None:
            (od / "hashes.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        summary[sid] = r
    if a.write_baseline:
        base = {sid: _views_only(r) for sid, r in summary.items() if "views" in r and dict((s, rp) for s, _f, rp in SAMPLES)[sid]}
        BASELINE.write_text(json.dumps(base, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    if a.local_baseline:
        base = {sid: _views_only(r) for sid, r in summary.items() if "views" in r and not dict((s, rp) for s, _f, rp in SAMPLES)[sid]}
        Path(a.local_baseline).write_text(json.dumps(base, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    if a.out:
        (Path(a.out) / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    if a.json:
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
