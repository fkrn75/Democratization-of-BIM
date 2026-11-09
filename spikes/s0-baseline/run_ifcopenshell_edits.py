"""IfcOpenShell(ifcedit/ifcquery CLI) 로 E1~E3, E6 을 수행하고 호출 기록을 남긴다.

실행: python run_ifcopenshell_edits.py   (tools/ifc-cli/.venv 의 ifcedit/ifcquery 를 서브프로세스로 호출)
산출: results/ifcopenshell/E1.ifc E2.ifc E3.ifc, calls.jsonl(명령·종료코드·소요·출력 요약)
ID 조회에만 ifcopenshell 라이브러리를 쓰고, 모든 편집은 ifcedit CLI 호출로 한다.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import ifcopenshell

HERE = Path(__file__).resolve().parent
V = HERE / "tools" / "ifc-cli" / ".venv" / "Scripts"
R = HERE / "results" / "ifcopenshell"
ENV = {**os.environ, "PYTHONUTF8": "1"}
LOG = R / "calls.jsonl"
LOG.write_text("", encoding="utf-8")


def call(exe, args, stdin=None):
    t = time.perf_counter()
    p = subprocess.run([str(V / f"{exe}.exe"), *map(str, args)], input=stdin, capture_output=True, text=True,
                       encoding="utf-8", env=ENV)
    dt = time.perf_counter() - t
    rec = {"cmd": [exe, *map(str, args)], "stdin": stdin, "rc": p.returncode, "sec": round(dt, 2),
           "stdout": p.stdout[:400], "stderr": p.stderr[:300]}
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p.returncode, p.stdout


def named(f, name):
    return next(e for e in f.by_type("IfcRoot") if getattr(e, "Name", None) == name)


src = HERE / "RA-01.ifc"
f = ifcopenshell.open(str(src))

# ---- E1: 외벽 W-E-01 을 +Y 로 600mm 이동 (자식=개구부·문·창은 같이 이동)
w = named(f, "W-E-01")
rc, _ = call("ifcedit", ["run", src, "geometry.edit_object_placement", "--product", f"#{w.id()}",
                         "--matrix", "[[1,0,0,0],[0,1,0,0.6],[0,0,1,0],[0,0,0,1]]",
                         "--should_transform_children", "true", "-o", R / "E1.ifc"])
print("E1 rc", rc)

# ---- E2: 문 타입 DT-01 폭 0.9 -> 1.0
#   (a) 타입의 DoBIM_TypeParams.OverallWidth  (IFC4 에는 타입 치수 속성이 없어 커스텀 Pset)
#   (b) 인스턴스 OverallWidth 속성  (c) 문 본체/개구부 프로파일 XDim (형상 재계산이 없어 수동 편집)
f = ifcopenshell.open(str(src))
dt01 = named(f, "DT-01")
tps = next(p for p in dt01.HasPropertySets if p.Name == "DoBIM_TypeParams")
cur = str(R / "E2.ifc")
call("ifcedit", ["run", src, "pset.edit_pset", "--pset", f"#{tps.id()}", "--properties", '{"OverallWidth": 1.0}', "-o", cur])
f = ifcopenshell.open(cur)
inst = [d for d in f.by_type("IfcDoor") if d.IsTypedBy and d.IsTypedBy[0].RelatingType.Name == "DT-01"]
call("ifcedit", ["foreach", cur, "attribute.edit_attributes", "--product", "#{id}", "--attributes", '{"OverallWidth": 1.0}'],
     stdin=json.dumps([{"id": d.id()} for d in inst]))
prof_ids = []
for d in inst:
    prof_ids.append(d.Representation.Representations[0].Items[0].SweptArea.id())
    op = d.FillsVoids[0].RelatingOpeningElement
    prof_ids.append(op.Representation.Representations[0].Items[0].SweptArea.id())
call("ifcedit", ["foreach", cur, "attribute.edit_attributes", "--product", "#{id}", "--attributes", '{"XDim": 1.0}'],
     stdin=json.dumps([{"id": i} for i in prof_ids]))
print("E2 대상 문:", [d.Name for d in inst])

# ---- E3: 2F 층고 3.3 -> 3.6 (2F 층 Elevation, 층 배치 z, 1F 벽/기둥의 높이, 1F 슬래브가 아닌 요소만)
f = ifcopenshell.open(str(src))
st2 = named(f, "2F")
cur = str(R / "E3.ifc")
# 여기서 '2층 층고'를 1F 층고(1F 바닥~2F 바닥)로 해석한다: 2F 가 0.3 올라가고 1F 벽·기둥 높이도 0.3 늘어야 한다.
call("ifcedit", ["run", src, "attribute.edit_attributes", "--product", f"#{st2.id()}", "--attributes", '{"Elevation": 3.6}', "-o", cur])
call("ifcedit", ["run", cur, "geometry.edit_object_placement", "--product", f"#{st2.id()}",
                  "--matrix", "[[1,0,0,0],[0,1,0,0],[0,0,1,3.6],[0,0,0,1]]", "--should_transform_children", "true", "-o", cur])
f = ifcopenshell.open(cur)
st1 = named(f, "1F")
gf = [e for rel in st1.ContainsElements for e in rel.RelatedElements if e.is_a() in ("IfcWall", "IfcColumn")]
solids = [e.Representation.Representations[0].Items[0].id() for e in gf]
call("ifcedit", ["foreach", cur, "attribute.edit_attributes", "--product", "#{id}", "--attributes", '{"Depth": 3.6}'],
     stdin=json.dumps([{"id": i} for i in solids]))
print("E3 1F 벽·기둥 높이 수정 개수:", len(solids))
