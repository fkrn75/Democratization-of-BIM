# SPDX-License-Identifier: MPL-2.0
"""뷰 N개 + 치수 N개 + 태그 N개가 있는 프로젝트의 내보내기·가져오기·편집 시간(ADR-0015, 통합 보고서 11.3절).
사용: python perf_views.py N   (코어가 import 되도록 PYTHONPATH=spikes/s1-core/src)"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in ("spikes/s2-roundtrip", "spikes/s1-core/src", "spikes/s1-core/tools"):
    sys.path.insert(0, str(ROOT / p))
import scenarios as S  # noqa: E402
from dobim_core import fixture as F  # noqa: E402

N = int(sys.argv[1])
tmp = Path(tempfile.mkdtemp())
b = S.Bench(tmp)
S.build_authored(b, "src.dobim")
R = lambda k, i, r: f"{k}:{i}#{r}"   # noqa: E731


def T():
    return time.perf_counter(), time.process_time()


def dt(a):
    n = T()
    return f"벽시계 {n[0] - a[0]:.2f}s CPU {n[1] - a[1]:.2f}s"


t = T()
for k in range(N):
    v = "4" + "0" * 12 + "%09d" % k
    assert b.run("view.create", {"id": v, "name": f"v{k}", "type": "plan", "level": F.LV1}, project="src.dobim")["ok"]
    assert b.run("annot.dim.create", {"id": "5" + "0" * 12 + "%09d" % k, "view": v, "a": R("wall", F.W1, "axis.start"),
                                      "b": R("wall", F.W1, "axis.end")}, project="src.dobim")["ok"]
    assert b.run("annot.tag.create", {"id": "6" + "0" * 12 + "%09d" % k, "view": v, "target": F.D1, "fields": ["name"]}, project="src.dobim")["ok"]
print("저작", dt(t))
t = T(); h0 = b.hashes("src.dobim"); print("해시(첫 flush)", dt(t))
t = T(); b.export("e1.ifc", "src.dobim"); print("내보내기", dt(t), (tmp / "e1.ifc").stat().st_size, "바이트")
t = T(); r = b.import_ifc("e1.ifc", "imp.dobim"); print("가져오기", dt(t), r["result"]["elements"], r["result"]["warnings"][:2])
t = T(); h1 = b.hashes("imp.dobim"); print("해시(가져온 프로젝트)", dt(t))
print("해시 3종 같음:", S.trip(h0) == S.trip(h1))
t = T(); b.export("e2.ifc", "imp.dobim"); print("다시 내보내기", dt(t), "바이트 같음:", (tmp / "e1.ifc").read_bytes() == (tmp / "e2.ifc").read_bytes())
t = T(); assert b.run("view.set", {"id": "4" + "0" * 12 + "%09d" % 5, "cut_height": 999}, project="imp.dobim")["ok"]; b.hashes("imp.dobim"); print("편집+해시", dt(t))
b.close()
