# SPDX-License-Identifier: MPL-2.0
"""S10: 코어 CLI 만으로 RA-01급 모델을 만들고 검증·해시·IFC 내보내기까지 하는 결정적 무인 스크립트.

- 코어의 `python -m dobim_core`(또는 설치된 `dobim`) 를 자식 프로세스로 연속 호출한다(stdin=DEVNULL).
- 이 스크립트 자신의 stdout 은 마지막에 쓰는 JSON 한 개뿐이다. 진행·진단은 stderr 에 쓴다.
- 종료 코드 0 = 모든 단계 성공 + 자체 점검 통과. 아니면 비 0.
- 자식 환경변수는 --env-mode 로 정한다: inherit(그대로) | minimal(OS 필수 변수만).

RA-01 에서 코어가 표현하는 범위(S1 커맨드 31개): 레벨, 벽, 벽 조인, 문, 속성셋, 재료.
범위 밖(코어에 커맨드가 없다): 슬래브·기둥·보·계단·공간·창·타입(IfcWallType 등)·레이어 재료·개구부 형상.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

GUID = "5d0b1a50-0000-4a00-8000-0d0b1a5000a1"   # 프로젝트 UUID 고정 -> 결정적 id·해시
LX, LY = 12000.0, 8000.0                         # RA-01 평면 12m x 8m (mm)
STOREY_H = 3300.0
EXT_T, INT_T = 320.0, 100.0


def mkid(prefix: str, n: int) -> str:
    """22자 GlobalId. 접두 1자(종류 표시) + 21자리 숫자."""
    return prefix + f"{n:021d}"


def build_script() -> list[dict]:
    """RA-01 사양에서 코어가 지원하는 부분을 커맨드 열로 만든다. 순서·id 가 고정이라 결정적이다."""
    cmds: list[dict] = []
    lv = {"1F": mkid("3", 1), "2F": mkid("3", 2)}
    cmds.append({"command": "level.create", "args": {"id": lv["1F"], "name": "1F", "elevation": 0}})
    cmds.append({"command": "level.create", "args": {"id": lv["2F"], "name": "2F", "elevation": STOREY_H}})

    hi = EXT_T / 2
    walls: dict[str, tuple[str, float]] = {}   # 이름 -> (id, 축선 길이)
    n = 0

    def wall(name, level, axis, t, ext):
        nonlocal n
        n += 1
        wid = mkid("1", n)
        x1, y1, x2, y2 = axis
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        walls[name] = (wid, length)
        cmds.append({"command": "wall.create", "args": {"id": wid, "level": lv[level], "axis": list(axis),
                                                        "thickness": t, "height": STOREY_H, "name": name}})
        cmds.append({"command": "pset.set", "args": {"id": wid, "pset": "Pset_WallCommon", "prop": "IsExternal", "value": ext}})
        cmds.append({"command": "pset.set", "args": {"id": wid, "pset": "Pset_WallCommon", "prop": "FireRating",
                                                     "value": "120min" if ext else "60min"}})
        cmds.append({"command": "material.set", "args": {"id": wid, "name": "Concrete" if ext else "Gypsum"}})

    k = 0
    for sn in ("1F", "2F"):
        for a, b in (((0, 0), (LX, 0)), ((LX, 0), (LX, LY)), ((LX, LY), (0, LY)), ((0, LY), (0, 0))):
            k += 1
            wall(f"W-E-{k:02d}", sn, (a[0], a[1], b[0], b[1]), EXT_T, True)
    k = 0
    for sn in ("1F", "2F"):
        for a, b in (((4000, hi), (4000, LY - hi)), ((4000 + INT_T / 2, 4000), (LX - hi, 4000)), ((8000, hi), (8000, 2000))):
            k += 1
            wall(f"W-I-{k:02d}", sn, (a[0], a[1], b[0], b[1]), INT_T, False)

    # 외벽 모서리 조인(층당 4쌍): 같은 레벨 안에서 이웃한 외벽끼리
    for base in (1, 5):
        for i in range(4):
            a = f"W-E-{base + i:02d}"
            b = f"W-E-{base + (i + 1) % 4:02d}"
            cmds.append({"command": "join.set", "args": {"a": walls[a][0], "b": walls[b][0]}})

    # 문 5: 외부 2, 내부 3 (s0 build_ra01.py 와 같은 호스트·위치)
    doors = [("D-01", "W-E-01", 8000, 900, "60min"), ("D-02", "W-I-01", 1840, 800, "30min"),
             ("D-03", "W-I-02", 2950, 800, "30min"), ("D-04", "W-I-04", 1840, 800, "30min"),
             ("D-05", "W-E-05", 8000, 900, "60min")]
    for i, (nm, host, off, w, fire) in enumerate(doors, 1):
        did = mkid("2", i)
        cmds.append({"command": "door.place", "args": {"id": did, "host": walls[host][0], "offset": off,
                                                        "width": w, "height": 2100, "name": nm}})
        cmds.append({"command": "pset.set", "args": {"id": did, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": fire}})
    return cmds


EXPECT = {"levels": 2, "walls": 14, "doors": 5}


class Runner:
    def __init__(self, python: str, workspace: Path, env: dict, timeout: float):
        self.python, self.ws, self.env, self.timeout = python, workspace, env, timeout
        self.calls: list[dict] = []

    def cli(self, *args: str) -> tuple[dict, int]:
        argv = [self.python, "-m", "dobim_core", "--workspace", str(self.ws), "--actor", "ra01", *args]
        t0 = time.perf_counter()
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, env=self.env, timeout=self.timeout,
                           cwd=str(self.ws))
        dt = time.perf_counter() - t0
        raw = p.stdout
        try:
            lines = raw.decode("utf-8").splitlines()
            # stdout 이 JSON 한 줄뿐이어야 한다(합격 기준)
            env = json.loads(lines[0]) if len(lines) == 1 else None
        except (UnicodeDecodeError, ValueError, IndexError):
            env = None
        self.calls.append({"argv": [a for a in args if not a.startswith(str(self.ws))][:3], "exit": p.returncode,
                           "stdout_json_only": env is not None, "stdout_bytes": len(raw), "stderr_bytes": len(p.stderr),
                           "seconds": round(dt, 3)})
        if env is None:
            raise RuntimeError(f"stdout 이 JSON 한 줄이 아니다: {raw[:200]!r} stderr={p.stderr[-300:]!r}")
        return env, p.returncode


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha256_text_lf(p: Path) -> str:
    """줄 끝(CRLF/LF) 차이를 지운 해시. 플랫폼 간 바이트 비교용 보조."""
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


_SET_LINE = re.compile(rb"^#\d+=IFC(?:UNITASSIGNMENT|RELASSOCIATESMATERIAL|RELCONTAINEDINSPATIALSTRUCTURE)\(.*$", re.M)
_REFS = re.compile(rb"\((#\d+(?:,#\d+)+)\)")
_RANDOM_GUID = re.compile(rb"^(#\d+=(?:IFCREL\w+|IFCPROPERTYSET|IFCELEMENTQUANTITY)\(')[0-9A-Za-z_$]{22}'", re.M)


def _sort_ref_lists(raw: bytes) -> bytes:
    """집합 의미인 참조 목록 (#1,#2,...) 을 정렬한다(IFC 의 SET: 원소 순서는 의미가 없다)."""
    def fix_refs(m):
        return b"(" + b",".join(sorted(m.group(1).split(b","), key=lambda r: int(r[1:]))) + b")"
    return _SET_LINE.sub(lambda m: _REFS.sub(fix_refs, m.group(0)), raw)


def sha256_data_section(p: Path) -> str:
    """STEP 의 DATA 구역만 해시한다(줄 끝 LF 정규화).
    코어 내보내기는 (1) 관계·속성셋 엔티티의 GlobalId 를 무작위로 만들고 (2) SET 원소를 플랫폼마다 다른 순서로 쓰며
    (3) 헤더에 작성 시각을 넣어 파일 바이트가 매번 다르다. 이 세 가지를 지운 뒤 해시한다: 나머지 바이트(실수 표기·엔티티 번호)는 같아야 한다."""
    raw = p.read_bytes().replace(b"\r\n", b"\n")
    i = raw.index(b"\nDATA;")
    body = _RANDOM_GUID.sub(rb"\1X'", raw[i:])
    return hashlib.sha256(_sort_ref_lists(body)).hexdigest()


def ifc_file_semantic(p: Path) -> dict:
    """내보낸 .ifc 를 다시 읽어 센 값(코어와 독립 경로): 클래스별 개수 + GlobalId 집합 해시."""
    import ifcopenshell
    f = ifcopenshell.open(str(p))
    counts: dict[str, int] = {}
    gids = []
    for e in f.by_type("IfcRoot"):
        counts[e.is_a()] = counts.get(e.is_a(), 0) + 1
        if e.is_a("IfcProduct") or e.is_a("IfcProject"):   # 관계·속성셋 GlobalId 는 무작위라 뺀다
            gids.append(e.GlobalId)
    return {"schema": f.schema, "counts": dict(sorted(counts.items())),
            "globalid_sha256": hashlib.sha256("\n".join(sorted(gids)).encode()).hexdigest(),
            "ifcopenshell": ifcopenshell.version}


def make_env(mode: str) -> dict:
    if mode == "inherit":
        env = dict(os.environ)
        env.pop("DISPLAY", None)           # 디스플레이 없음 보장
        env.pop("WAYLAND_DISPLAY", None)
        return env
    # minimal: 인터프리터가 돌 최소 변수만. 사용자 프로필·PATH 없음
    keep = ("SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PYTHONPATH")
    env = {k: v for k, v in os.environ.items() if k in keep}
    return env


def run(args) -> dict:
    ws = Path(args.workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    for stale in ("ra01.dobim", "ra01.dobim-wal", "ra01.dobim-shm", "ra01.ifc"):
        if (ws / stale).exists():
            (ws / stale).unlink()
    env = make_env(args.env_mode)
    r = Runner(args.python, ws, env, args.timeout)
    script = build_script()
    (ws / "ra01.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False, sort_keys=True) for c in script) + "\n",
                                   encoding="utf-8", newline="\n")
    out: dict = {"steps": {}}

    e, code = r.cli("run", "project.new", "path=ra01.dobim", f"guid={GUID}")
    assert e["ok"] and code == 0, e
    out["steps"]["project.new"] = {"exit": code, "rev": e["rev"]}

    e, code = r.cli("script", "ra01.jsonl", "-p", "ra01.dobim", "--atomic")
    assert e["ok"] and code == 0, e
    out["steps"]["script"] = {"exit": code, "rev": e["rev"], "commands": len(script)}

    e, code = r.cli("run", "model.stats", "-p", "ra01.dobim")
    assert e["ok"] and code == 0, e
    counts = e["result"]["counts"]
    out["stats"] = counts
    out["steps"]["model.stats"] = {"exit": code}

    e, code = r.cli("run", "model.validate", "-p", "ra01.dobim", "--json", '{"express_rules": true}')
    out["steps"]["model.validate"] = {"exit": code, "errors": e["result"]["errors"], "warnings": e["result"]["warnings"]}
    assert e["ok"] and code == 0 and e["result"]["errors"] == 0, e

    # 파괴적 커맨드(ifc.export)는 2단계: 미리보기에서 확인 토큰을 받고 --confirm 으로 다시 호출한다(무인에서도 토큰 흐름 유지)
    e, code = r.cli("run", "ifc.export", "-p", "ra01.dobim", "path=ra01.ifc")
    assert (not e["ok"]) and code == 6 and e["error"]["code"] == "CONFIRM_REQUIRED", e
    token = e["error"]["details"]["confirm_token"]
    out["steps"]["ifc.export.preview"] = {"exit": code}
    e, code = r.cli("run", "ifc.export", "-p", "ra01.dobim", "--confirm", token, "path=ra01.ifc")
    assert e["ok"] and code == 0, e
    out["steps"]["ifc.export"] = {"exit": code, "bytes": e["result"]["bytes"]}

    e, code = r.cli("run", "model.hash", "-p", "ra01.dobim")
    assert e["ok"] and code == 0, e
    h = e["result"]
    out["hashes"] = {"state": h["state"], "derived": h["derived"], "ifc": h["ifc"], "rev": h["rev"]}
    out["steps"]["model.hash"] = {"exit": code}

    ifc = ws / "ra01.ifc"
    out["ifc_file"] = {"sha256": sha256_file(ifc), "sha256_lf": sha256_text_lf(ifc), "data_sha256": sha256_data_section(ifc), "bytes": ifc.stat().st_size,
                       **ifc_file_semantic(ifc)}

    # 자체 점검: 사양 수량
    want = EXPECT
    ok_counts = (counts.get("level") == want["levels"] and counts.get("wall") == want["walls"] and counts.get("door") == want["doors"])
    out["spec_counts_ok"] = bool(ok_counts)
    out["child_calls"] = r.calls
    out["all_stdout_json_only"] = all(c["stdout_json_only"] for c in r.calls)
    out["ok"] = bool(ok_counts and out["all_stdout_json_only"] and all(c["exit"] in (0, 6) for c in r.calls))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="RA-01급 헤드리스 완주 스크립트")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--env-mode", choices=["inherit", "minimal"], default="inherit")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--timing", action="store_true", help="벽시계 시간을 결과에 넣는다(해시 비교에서는 제외)")
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    try:
        out = run(args)
        code = 0 if out["ok"] else 1
    except Exception as ex:   # noqa: BLE001
        print(f"실패: {ex!r}", file=sys.stderr)
        out, code = {"ok": False, "error": repr(ex)[:300]}, 1
    out["platform"] = {"system": platform.system(), "python": platform.python_version(), "machine": platform.machine()}
    if args.timing:
        out["wall_seconds"] = round(time.perf_counter() - t0, 3)
    # 콘솔 코드페이지(cp949 등)와 무관하게 UTF-8 바이트로 쓴다(실패 경로의 OS 메시지에 한글이 섞일 수 있다)
    sys.stdout.buffer.write((json.dumps(out, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
