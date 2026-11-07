"""ConstructCAD(컨테이너 안 ccad) 호출 도우미. 각 호출은 격리 컨테이너를 새로 띄우고 stdout JSON 을 파싱해 기록한다.
호출 기록은 out 폴더의 calls.jsonl 에 남는다. 사용: from ccad_drive import run"""
import json, subprocess, time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "out" / "p2" / "ccad" / "work"
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "calls.jsonl"


def run(cmds, project=None, save_as=None, save=False, label=""):
    """cmds: [(command, args)]. project/save_as 는 /out 안의 파일명."""
    opts = []
    if project:
        opts += ["-p", f"/out/{project}"]
    if save:
        opts += ["--save"]
    if save_as:
        opts += ["--save-as", f"/out/{save_as}"]
    payload = json.dumps([{"command": c, "args": a} for c, a in cmds])
    t = time.perf_counter()
    p = subprocess.run(["C:/Program Files/Git/bin/bash.exe", "phase2/run_ccad.sh", "out/p2/ccad/work", "ccad", *opts, "script", "-"],
                       input=payload, capture_output=True, text=True, encoding="utf-8", cwd=HERE)
    dt = time.perf_counter() - t
    try:
        res = json.loads(p.stdout)
    except Exception:
        res = None
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"label": label, "opts": opts, "cmds": [c for c, _ in cmds], "rc": p.returncode, "sec": round(dt, 2),
                             "stdout_head": p.stdout[:300], "stderr": p.stderr[:300]}, ensure_ascii=False) + "\n")
    return p.returncode, res, p.stdout, p.stderr
