"""E4(평면·단면 + E1 후 갱신), E5(A1 시트 PDF, 한글), E6(일람표 + E2 후) — ccad CLI 만 사용, 컨테이너 격리."""
import json, re, sys, time
sys.path.insert(0, "phase2")
from ccad_drive import run

W = "out/p2/ccad/work/"
t = time.perf_counter()
rc, res, so, se = run([("view_create", {"kind": "section", "start": [6, -1], "end": [6, 9], "depth": 10, "name": "SEC-1"}),
                       ("view_create", {"kind": "elevation", "direction": "south", "name": "ELEV-S"})],
                      project="native.ccad", save_as="n_views.ccad", label="views")
print("뷰 생성", rc, se[:200], res and [r["result"] for r in res])

rc, res, so, se = run([("views_list", {})], project="n_views.ccad", label="vl")
VL = res[0]["result"]
P1 = next(v["id"] for v in VL if v["category"] == "floor_plan" and v["name"] == "Ground Floor")
P2 = next(v["id"] for v in VL if v["category"] == "floor_plan" and v["name"] == "Level 1")
SEC = next(v["id"] for v in VL if v["name"] == "SEC-1")


def export(project, tag):
    cmds = [("view_export", {"view": P1, "path": f"/out/{tag}_plan.svg", "format": "svg"}),
            ("view_export", {"view": "SEC-1", "path": f"/out/{tag}_section.svg", "format": "svg"})]
    t0 = time.perf_counter()
    rc, res, so, se = run(cmds, project=project, label="export-" + tag)
    print(tag, "rc", rc, "경과", round(time.perf_counter() - t0, 2), "s", se[:200] if rc else "")

export("n_views.ccad", "before")
# E1 적용 후 같은 뷰 재출력 (뷰 재생성 명령 없이)
rc, res, so, se = run([("elements_move", {"ids": [101], "delta": [0, 0.6]})], project="n_views.ccad", save_as="n_views_e1.ccad", label="E1b")
export("n_views_e1.ccad", "after")

def stats(p):
    s = open(W + p, encoding="utf-8").read()
    return {"bytes": len(s), "path": len(re.findall(r"<path", s)), "line": len(re.findall(r"<line", s)), "polyline": len(re.findall(r"<poly", s)), "text": len(re.findall(r"<text", s)), "image": len(re.findall(r"<image", s))}

for n in ("before_plan", "after_plan", "before_section", "after_section"):
    print(n, stats(n + ".svg"))
for k in ("plan", "section"):
    a = open(W + f"before_{k}.svg", encoding="utf-8").read(); b = open(W + f"after_{k}.svg", encoding="utf-8").read()
    print(k, "전후 SVG 동일?", a == b)

# ---- E5: A1 시트 + 한글
rc, res, so, se = run([("project_info_set", {"name": "RA-01 소형 사무소", "author": "홍길동", "number": "한글-01"}),
                       ("view_create", {"kind": "sheet", "name": "S-101"}),
                       ("views_list", {})], project="n_views.ccad", save_as="n_sheet.ccad", label="sheet1")
print("sheet", rc, se[:300])
sid = next(v["id"] for v in res[2]["result"] if v["category"] == "sheet" and v["name"] == "S-101")
rc, res, so, se = run([("view_set", {"view": sid, "params": {"size": "A1"}}),
                       ("sheet_place_view", {"sheet": sid, "view": P2, "center": [250, 300]}),
                       ("sheet_place_view", {"sheet": sid, "view": SEC, "center": [600, 300]}),
                       ("text_create", {"view": P2, "position": [6, -2], "text": "1층 평면도 한글 시험"}),
                       ("sheet_export", {"sheet": sid, "path": "/out/S101.pdf", "format": "pdf"}),
                       ("sheet_export", {"sheet": sid, "path": "/out/S101.svg", "format": "svg"}),
                       ("sheet_export", {"sheet": sid, "path": "/out/S101.dxf", "format": "dxf"}),
                       ("view_get", {"view": sid})], project="n_sheet.ccad", save_as="n_sheet2.ccad", label="sheet2")
print("sheet2", rc, se[:400], so[-300:] if rc else "")
if res: print(json.dumps(res[-1]["result"], ensure_ascii=False)[:300])

# ---- E6
rc, res, so, se = run([("schedule_table", {"view": "Door Schedule"}), ("schedule_export_csv", {"view": "Door Schedule", "path": "/out/doors_before.csv"})], project="native.ccad", label="E6a")
print("E6 전", rc, se[:200]); print(json.dumps(res[0]["result"], ensure_ascii=False)[:700] if res else so[:300])
rc, res, so, se = run([("schedule_table", {"view": "Door Schedule"}), ("schedule_export_csv", {"view": "Door Schedule", "path": "/out/doors_after.csv"})], project="n_e2.ccad", label="E6b")
print("E6 E2후", rc, json.dumps(res[0]["result"], ensure_ascii=False)[:700] if res else so[:300])
