# SPDX-License-Identifier: MPL-2.0
"""Tauri 셸(app/src-tauri)이 링크하는 Rust 크레이트의 라이선스 고지를 cargo metadata 에서 만든다.

범위: `cargo metadata --filter-platform x86_64-pc-windows-msvc` 의 해석 그래프에서 루트 크레이트로부터
'일반(normal)' 의존 간선만 따라 도달하는 크레이트. 빌드 스크립트·개발 의존은 설치본에 들어가지 않으므로 뺀다.
(proc-macro 크레이트는 cargo 가 일반 의존으로 표시하므로 포함된다 — 과다 포함 쪽으로 틀린다.)
판정은 licensing.py (금지 라이선스가 있으면 호출한 쪽이 실패 처리한다). 라이선스 전문은 크레이트 소스(cargo 레지스트리 캐시)의
LICENSE/COPYING/NOTICE 파일에서 읽고, 같은 전문은 한 번만 싣는다.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import S7  # noqa: E402
import licensing  # noqa: E402

TAURI = S7 / "app" / "src-tauri"
TRIPLE = "x86_64-pc-windows-msvc"
_LIC_FILE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|UNLICENSE)([-_.A-Za-z0-9]*)$", re.I)


def normalize_expr(expr: str) -> str:
    """Cargo 의 옛 표기('MIT/Apache-2.0', 'A / B')를 SPDX 식으로 고친다."""
    return re.sub(r"\s*/\s*", " OR ", (expr or "").strip())


def _read(p: Path) -> str:
    b = p.read_bytes()[:200_000]
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def load_metadata(manifest: Path = TAURI / "Cargo.toml") -> dict:
    p = subprocess.run(["cargo", "metadata", "--format-version", "1", "--locked", "--offline", "--filter-platform", TRIPLE,
                        "--manifest-path", str(manifest)], capture_output=True, text=True, encoding="utf-8", timeout=180)
    if p.returncode != 0:
        raise RuntimeError("cargo metadata 실패: " + p.stderr[-300:])
    return json.loads(p.stdout)


def reachable(meta: dict) -> list[dict]:
    pk = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    root = meta["resolve"]["root"]
    seen: set[str] = set()
    stack = [root]
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        for dep in nodes[i]["deps"]:
            if any(k["kind"] is None for k in dep["dep_kinds"]):
                stack.append(dep["pkg"])
    return [pk[i] for i in sorted(seen, key=lambda x: (pk[x]["name"], pk[x]["version"])) if i != root]


def crate_license_files(pkg: dict) -> list[tuple[str, str]]:
    d = Path(pkg["manifest_path"]).parent
    out = []
    for f in sorted(d.iterdir()) if d.is_dir() else []:
        if f.is_file() and _LIC_FILE.match(f.name):
            out.append((f.name, _read(f)))
    return out


def build(manifest: Path = TAURI / "Cargo.toml") -> dict:
    """크레이트 구성요소 목록과 판정 요약을 돌려준다. 키: crates, forbid, unknown, weak, no_text, texts, root_license"""
    meta = load_metadata(manifest)
    crates = []
    for p in reachable(meta):
        expr = normalize_expr(p.get("license") or "")
        if not expr and p.get("license_file"):
            expr = "unknown"
        verdict, chosen = licensing.evaluate(expr)
        files = crate_license_files(p)
        crates.append({"name": p["name"], "version": p["version"], "license_expression": expr or "unknown", "verdict": verdict,
                       "chosen_alternative": chosen, "repository": p.get("repository") or p.get("homepage") or "",
                       "license_files": files})
    # 같은 전문은 한 번만: 해시 -> (대표 파일 이름, 텍스트, 쓰는 크레이트들)
    texts: dict[str, dict] = {}
    for c in crates:
        for fname, text in c["license_files"]:
            h = hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8", "ignore")).hexdigest()
            t = texts.setdefault(h, {"file": fname, "text": text.strip("\n"), "crates": []})
            t["crates"].append(f"{c['name']} {c['version']}")
    return {
        "crates": crates,
        "forbid": [f"{c['name']} {c['version']}={c['license_expression']}" for c in crates if c["verdict"] == "forbid"],
        "unknown": [f"{c['name']} {c['version']}={c['license_expression']}" for c in crates if c["verdict"] == "unknown"],
        "weak": [f"{c['name']} {c['version']}={c['license_expression']}" for c in crates if c["verdict"] == "weak"],
        "no_text": [f"{c['name']} {c['version']}" for c in crates if not c["license_files"]],
        "texts": list(texts.values()),
    }


def render(r: dict) -> list[str]:
    """고지 파일에 붙일 줄 목록 (배포용 문구만)."""
    cs = r["crates"]
    L = ["", "=" * 72, f"Rust 크레이트 (Tauri 셸 실행 파일에 정적으로 링크됨, {len(cs)}개)", "=" * 72,
         "셸 실행 파일(dobim-s7-shell.exe)은 아래 크레이트를 링크한다. 이중 라이선스(OR)인 크레이트는 사용자가 허용하는 쪽을 고를 수 있다.", ""]
    for c in cs:
        L.append(f"- {c['name']} {c['version']} — {c['license_expression']}" + (f"  <{c['repository']}>" if c["repository"] else ""))
    weak = [c for c in cs if c["verdict"] == "weak"]
    if weak:
        L += ["", "[MPL-2.0 크레이트 소스 안내] 아래 크레이트는 MPL-2.0 이며 수정 없이 사용한다. 해당 버전의 소스는 crates.io 와 아래 저장소에서 받을 수 있다.",
              *[f"- {c['name']} {c['version']}  <{c['repository'] or 'https://crates.io/crates/' + c['name']}>" for c in weak]]
    if r["no_text"]:
        L += ["", "[전문 파일이 없는 크레이트] 크레이트 소스에 라이선스 파일이 없다. 위 SPDX 식과 저장소 주소를 따른다:",
              ", ".join(r["no_text"])]
    L += ["", "-" * 72, "Rust 크레이트 라이선스 전문 (같은 전문은 한 번만 싣는다)", "-" * 72]
    for t in r["texts"]:
        users = t["crates"]
        who = ", ".join(users[:6]) + (f" 외 {len(users) - 6}개" if len(users) > 6 else "")
        L += ["", f"### {t['file']}  — 사용 크레이트: {who}", t["text"]]
    return L


if __name__ == "__main__":
    r = build()
    print(json.dumps({k: (v if k in ("forbid", "unknown", "weak", "no_text") else len(v)) for k, v in r.items()}, ensure_ascii=False, indent=1))
    print("text bytes:", sum(len(t["text"]) for t in r["texts"]))
