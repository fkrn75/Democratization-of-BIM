"""ConstructCAD(ccad CLI, 컨테이너 격리)만으로 RA-01 사양을 네이티브 모델링한다 (roadmap 135행 '전 과정 CLI').
산출: out/p2/ccad/work/native.ccad, native.ifc. 호출 수와 소요를 출력한다."""
import copy, json, sys, time
sys.path.insert(0, "phase2")
from ccad_drive import run

EXT_T, INT_T, H = 0.32, 0.10, 3.3
hi = EXT_T / 2
t0 = time.perf_counter()
calls = 0


def go(cmds, **kw):
    global calls
    rc, res, so, se = run(cmds, **kw)
    calls += len(cmds)
    if rc != 0 or res is None:
        print("실패", rc, so[-600:], se[-400:])
        sys.exit(1)
    return res


# ---- 1단계: 레벨, 재료, 타입
r = go([("project_new", {}), ("level_create", {"name": "RF", "elevation": 6.6}),
        ("level_set", {"level": "Level 1", "name": "2F", "elevation": 3.3}), ("level_set", {"level": "Ground Floor", "name": "1F"}),
        ("material_create", {"name": "Insulation"}),
        ("type_duplicate", {"type": "Masonry 25 cm", "name": "WT-EXT-320"}),
        ("type_duplicate", {"type": "Gypsum Partition 10 cm", "name": "WT-INT-100"}),
        ("type_duplicate", {"type": "Door 90x210", "name": "DT-01"}), ("type_duplicate", {"type": "Door 80x210", "name": "DT-02"}),
        ("type_duplicate", {"type": "Window 120x120", "name": "WT-01"}), ("type_duplicate", {"type": "Window 120x120", "name": "WT-02"}),
        ("type_duplicate", {"type": "Column 20 x 20 cm", "name": "COL-400"}),
        ("type_duplicate", {"type": "Beam 15 x 40 cm", "name": "BM-300x400"}),
        ("type_duplicate", {"type": "Slab 12 cm", "name": "SL-200"}),
        ("type_get", {"type": "WT-02"}), ("type_get", {"type": "WT-INT-100"}), ("type_get", {"type": "Slab 12 cm"})], save_as="native.ccad", label="n1")
wt02 = r[-3]["result"]["params"]
widths = [p for p in wt02["def"]["params"]]
print("WT-02 params:", [(p["name"], p["value"]) for p in widths])
d = copy.deepcopy(wt02["def"])
for p in d["params"]:
    if p["name"] == "Width":
        p["value"] = 1.8
    if p["name"] == "Height":
        p["value"] = 1.2
intl = r[-2]["result"]["params"]["layers"]
print("INT layers", intl)
r = go([("type_set", {"type": "WT-EXT-320", "params": {"layers": [
            {"function": "Finish", "material": "Plaster", "thickness": 0.02},
            {"function": "Structure", "material": "Insulation", "thickness": 0.10},
            {"function": "Structure", "material": "Concrete", "thickness": 0.20}]}}),
        ("type_set", {"type": "WT-INT-100", "params": {"layers": [{"function": intl[0]["function"], "material": "Gypsum", "thickness": INT_T}]}}),
        ("type_set", {"type": "WT-02", "params": {"def": d}}),
        ("type_set", {"type": "COL-400", "params": {"section": {"Rectangular": {"depth": 0.4, "width": 0.4}}}}),
        ("type_set", {"type": "BM-300x400", "params": {"width": 0.3}}),
        ("type_set", {"type": "SL-200", "params": {"layers": [{"function": "Structure", "material": "Concrete", "thickness": 0.2}]}})], project="native.ccad", save=True, label="n2")

# ---- 2단계: 벽
cmds = []
for lv, top in (("1F", "2F"), ("2F", "RF")):
    for a, b in (((0, 0), (12, 0)), ((12, 0), (12, 8)), ((12, 8), (0, 8)), ((0, 8), (0, 0))):
        cmds.append(("wall_create", {"start": list(a), "end": list(b), "level": lv, "top_level": top, "type": "WT-EXT-320"}))
    for a, b in (((4.0, hi), (4.0, 8 - hi)), ((4.05, 4.0), (12 - hi, 4.0)), ((8.0, hi), (8.0, 2.0))):
        cmds.append(("wall_create", {"start": list(a), "end": list(b), "level": lv, "top_level": top, "type": "WT-INT-100"}))
r = go(cmds, project="native.ccad", save=True, label="n3-walls")
wid = [x["result"]["id"] for x in r]
print("wall ids", wid)
# wid 순서: 1F ext(4) int(3), 2F ext(4) int(3)
E1F, I1F = wid[0:4], wid[4:7]
E2F, I2F = wid[7:11], wid[11:14]
# 이 모델에서 1F 벽 7개, 2F 벽 7개 순서로 만들었으므로 위처럼 나눈다 (wid[0:7], wid[7:14])
ext1, int1, ext2, int2 = wid[0:4], wid[4:7], wid[7:11], wid[11:14]

# ---- 3단계: 문·창 (offset 은 호스트 벽 시작점에서의 거리)
c = [("door_create", {"wall": ext1[0], "offset": 8.0, "type": "DT-01"}),
     ("door_create", {"wall": int1[0], "offset": 1.84, "type": "DT-02"}),
     ("door_create", {"wall": int1[1], "offset": 2.95, "type": "DT-02"}),
     ("door_create", {"wall": int2[0], "offset": 1.84, "type": "DT-02"}),
     ("door_create", {"wall": ext2[0], "offset": 8.0, "type": "DT-01"})]
for south, north in ((ext1[0], ext1[2]), (ext2[0], ext2[2])):
    for u in (2.5, 5.0):
        c.append(("window_create", {"wall": south, "offset": u, "sill": 0.9, "type": "WT-01"}))
    for u in (3.0, 9.0):
        c.append(("window_create", {"wall": north, "offset": u, "sill": 0.9, "type": "WT-02"}))
go(c, project="native.ccad", save=True, label="n4-openings")

# ---- 4단계: 슬래브, 기둥, 보, 계단, 공간
c = [("floor_create", {"level": "1F", "boundary": [[-hi, -hi], [12 + hi, -hi], [12 + hi, 8 + hi], [-hi, 8 + hi]], "offset": 0.0, "type": "SL-200"}),
     ("floor_create", {"level": "2F", "boundary": [[hi, hi], [12 - hi, hi], [12 - hi, 8 - hi], [hi, 8 - hi]], "offset": 0.0, "type": "SL-200"}),
     ("floor_create", {"level": "RF", "boundary": [[hi, hi], [12 - hi, hi], [12 - hi, 8 - hi], [hi, 8 - hi]], "offset": 0.0})]
for lv in ("1F", "2F"):
    for x in (hi, 6.0, 12 - hi):
        for y in (hi, 8 - hi):
            c.append(("column_create", {"position": [x, y], "level": lv, "type": "COL-400"}))
for a, b in (((hi, hi), (6, hi)), ((6, hi), (12 - hi, hi)), ((hi, 8 - hi), (6, 8 - hi)), ((6, 8 - hi), (12 - hi, 8 - hi)),
             ((12 - hi, hi), (12 - hi, 4)), ((12 - hi, 4), (12 - hi, 8 - hi)), ((hi, hi), (hi, 4)), ((hi, 4), (hi, 8 - hi))):
    c.append(("beam_create", {"start": list(a), "end": list(b), "level": "2F", "top_offset": 0.0, "type": "BM-300x400"}))
c.append(("stair_create", {"start": [1.6, 1.0], "direction": 90, "level": "1F", "top_level": "2F"}))
for lv, pts in (("1F", ([2, 4], [8, 2], [8, 6])), ("2F", ([2, 4], [8, 2], [8, 6]))):
    for p in pts:
        c.append(("room_create", {"point": p, "level": lv}))
c.append(("ifc_export", {"path": "/out/native.ifc"}))
r = go(c, project="native.ccad", save=True, label="n5-rest")
print("전체 명령 수", calls, "경과 초", round(time.perf_counter() - t0, 1))
