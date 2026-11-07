# SPDX-License-Identifier: MPL-2.0
"""골든 값 시험: 질의 함수와 독립적으로 손으로 계산한 기대값.

증분 그래프와 기준 그래프는 같은 질의 함수를 공유하므로 질의의 의미 오류(면적 공식, 경계 비교 등)는
차등 시험으로 잡히지 않는다. 여기의 기대값은 코드를 보지 않고 계산한 숫자이며 계산 근거를 주석에 적었다.
위반 문자열 목록을 돌려준다(빈 목록 = 통과).
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import kit
from dobim_core import fixture as F

LV, A, B, C, X1, X2 = (kit.mkid("3", 1), kit.mkid("1", 1), kit.mkid("1", 2), kit.mkid("1", 3),
                       kit.mkid("1", 4), kit.mkid("1", 5))
D = [kit.mkid("2", i) for i in range(1, 6)]


def near(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


def check_golden(work: Path) -> list[str]:
    from dobim_core import queries as Q
    v = []
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=work, prefix="golden-"))   # 호출마다 새 폴더(이전 호출의 파일이 남지 않게)

    def expect(name, got, want, tol=1e-6):
        ok = (near(got, want, tol) if isinstance(want, float) else got == want)
        if not ok:
            v.append(f"{name}: 기대 {want!r}, 실제 {got!r}")

    def sched(s, kind, eid):
        return next(r for r in s.eng.g.q(Q.schedule_sorted, kind) if r[0] == eid)

    # ---- 1. 기준 프로젝트 (fixture.SCRIPT)
    # W1 (0,0)-(6000,0), W2 (6000,0)-(6000,4000), W3 (6000,4000)-(0,4000), 두께 200, 높이 3000. W1-W2 조인.
    # W1: 교점 (6000,0) 은 끝점이라 정리 후에도 길이 6000.  W2: 교점이 시작점이라 길이 4000.  W3: 조인 없음 6000.
    # 순면적 = 길이*높이 - sum(문 너비*높이).
    #   W1 = 6000*3000 - 900*2100  = 18,000,000 - 1,890,000 = 16,110,000
    #   W2 = 4000*3000             = 12,000,000
    #   W3 = 6000*3000 - 1000*2100 = 18,000,000 - 2,100,000 = 15,900,000
    s = kit.Session(work / "g1.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    w1, w2, w3 = (sched(s, "wall", x) for x in (F.W1, F.W2, F.W3))
    for nm, r, length, area, doors in (("W1", w1, 6000.0, 16_110_000.0, 1), ("W2", w2, 4000.0, 12_000_000.0, 0),
                                       ("W3", w3, 6000.0, 15_900_000.0, 1)):
        expect(f"{nm} 길이", r[4], length)
        expect(f"{nm} 순면적", r[7], area)
        expect(f"{nm} 문 수", r[8], doors)
        expect(f"{nm} 상태", r[12], "ok")
    expect("W1 재료", w1[11], "Concrete")
    sw, sd = s.eng.g.q(Q.schedule_summary, "wall"), s.eng.g.q(Q.schedule_summary, "door")
    expect("벽 합계 길이", sw["total_length"], 16000.0)
    expect("벽 합계 순면적", sw["total_net_area"], 44_010_000.0)       # 16,110,000 + 12,000,000 + 15,900,000
    expect("벽 개수", sw["count"], 3)
    expect("문 합계 너비", sd["total_width"], 1900.0)
    d1, d2 = sched(s, "door", F.D1), sched(s, "door", F.D2)
    # 문 세계 좌표 = 호스트 시작점 + 방향*offset + (0,0,레벨 고도+문턱). D1: (0+1500, 0, 0). D2: W3 방향 (-1,0): (6000-3000, 4000, 0)
    expect("D1 x", d1[9], 1500.0), expect("D1 y", d1[10], 0.0), expect("D1 z", d1[11], 0.0)
    expect("D2 x", d2[9], 3000.0), expect("D2 y", d2[10], 4000.0)
    expect("D1 방화등급", d1[12], "EI30")
    expect("D1 상태", d1[15], "ok")
    # 이동: W3 를 (+500,0) 옮기면 D2 는 (6500-3000, 4000) = 3500
    s.run("wall.move", {"id": F.W3, "dx": 500, "dy": 0})
    expect("이동 뒤 D2 x", sched(s, "door", F.D2)[9], 3500.0)
    # 문 높이 바꾸기: D1 높이 2400 -> W1 순면적 = 18,000,000 - 900*2400 = 15,840,000
    s.run("door.set", {"id": F.D1, "height": 2400})
    expect("문 높이 변경 뒤 W1 순면적", sched(s, "wall", F.W1)[7], 15_840_000.0)

    # IFC 투영 골든: 개수와 좌표
    ifc = s.eng
    ifc.flush_ifc()
    f = ifc.ifc.f
    for cls, n in (("IfcBuildingStorey", 2), ("IfcWall", 3), ("IfcDoor", 2), ("IfcOpeningElement", 2),
                   ("IfcRelVoidsElement", 2), ("IfcRelFillsElement", 2)):
        expect(f"IFC {cls} 개수", len(f.by_type(cls)), n)
    dw = f.by_guid(F.D1)
    expect("IFC 문 폭", dw.OverallWidth, 900.0), expect("IFC 문 높이", dw.OverallHeight, 2400.0)
    expect("IFC 문 배치 x(호스트 국소)", dw.ObjectPlacement.RelativePlacement.Location.Coordinates[0], 1500.0)
    wl = f.by_guid(F.W1)
    solid = wl.Representation.Representations[0].Items[0]
    expect("IFC 벽 압출 깊이", solid.Depth, 3000.0)
    pts = [tuple(p.Coordinates) for p in solid.SweptArea.OuterCurve.Points]
    expect("IFC 벽 윤곽", pts, [(0.0, -100.0), (6000.0, -100.0), (6000.0, 100.0), (0.0, 100.0), (0.0, -100.0)])
    s.close()

    # ---- 2. 경계: 문이 정리된 호스트 시작선에 정확히 닿으면 ok, 0.1mm 벗어나면 outside
    # A (0,0)-(6000,0), X1 이 x=450 에서 수직 교차 -> A 의 정리 시작 t=450 (450 <= 6000-450 이므로 시작 쪽), 길이 6000-450=5550.
    # 문 폭 900, 중심 offset 900 -> 왼쪽 끝 900-450 = 450 = 시작선(경계 포함이라 ok). offset 899.9 -> 449.9 < 450 이라 outside.
    s = kit.Session(work / "g2.dobim")
    s.run("level.create", {"id": LV, "name": "L", "elevation": 0})
    s.run("wall.create", {"id": A, "level": LV, "axis": [0, 0, 6000, 0]})
    s.run("wall.create", {"id": X1, "level": LV, "axis": [450, -1000, 450, 1000]})
    s.run("join.set", {"a": A, "b": X1})
    s.run("door.place", {"id": D[0], "host": A, "offset": 900, "width": 900})
    s.run("door.place", {"id": D[1], "host": A, "offset": 899.9, "width": 900})
    s.run("door.place", {"id": D[2], "host": A, "offset": 5550, "width": 900})        # 오른쪽 끝 5550+450=6000 = 끝선(ok)
    ra = sched(s, "wall", A)
    expect("A 정리 길이", ra[4], 5550.0)
    expect("A 순면적", ra[7], 5550.0 * 3000 - 3 * 900 * 2100.0)                         # 16,650,000 - 5,670,000 = 10,980,000
    expect("경계 문 상태(정확히 닿음)", sched(s, "door", D[0])[15], "ok")
    expect("경계 문 상태(0.1mm 벗어남)", sched(s, "door", D[1])[15], "outside_trimmed_host")
    expect("끝선 문 상태", sched(s, "door", D[2])[15], "ok")
    s.close()

    # ---- 3. 퇴화 조인: 길이 100 벽을 x=50.4, x=49.8 에서 수직 벽 둘이 교차 -> 시작 49.8, 끝 50.4, 차 0.6 < 1 -> 원래 길이로 돌아가고 상태 표시
    s = kit.Session(work / "g3.dobim")
    s.run("level.create", {"id": LV, "name": "L", "elevation": 0})
    s.run("wall.create", {"id": A, "level": LV, "axis": [0, 0, 100, 0]})
    s.run("wall.create", {"id": X1, "level": LV, "axis": [50.4, -500, 50.4, 500]})
    s.run("wall.create", {"id": X2, "level": LV, "axis": [49.8, -500, 49.8, 500]})
    s.run("join.set", {"a": A, "b": X1})
    s.run("join.set", {"a": A, "b": X2})
    ra = sched(s, "wall", A)
    expect("퇴화 조인 상태", ra[12], "degenerate_join")
    expect("퇴화 조인 길이(원래대로)", ra[4], 100.0)
    s.run("wall.move", {"id": X2, "dx": -10})                                            # 49.8 -> 39.8: 차 10.6 >= 1 -> 정상 정리
    ra = sched(s, "wall", A)
    expect("퇴화 해소 상태", ra[12], "ok")
    expect("퇴화 해소 길이", ra[4], 50.4 - 39.8)
    s.close()

    # ---- 4. 비직교 벽: (0,0)-(3,4) 길이 5, 높이 3000 -> 면적 15,000. 문 폭 2, 중심 2.5 -> 세계 (2.5*0.6, 2.5*0.8) = (1.5, 2.0)
    s = kit.Session(work / "g4.dobim")
    s.run("level.create", {"id": LV, "name": "L", "elevation": 1000})
    s.run("wall.create", {"id": A, "level": LV, "axis": [0, 0, 3, 4], "height": 3000})
    s.run("door.place", {"id": D[0], "host": A, "offset": 2.5, "width": 2, "height": 1, "sill": 10})
    ra, rd = sched(s, "wall", A), sched(s, "door", D[0])
    expect("비직교 길이", ra[4], 5.0), expect("비직교 순면적", ra[7], 15_000.0 - 2.0)
    expect("비직교 문 x", rd[9], 1.5, 1e-9), expect("비직교 문 y", rd[10], 2.0, 1e-9)
    expect("문 z = 레벨 고도 + 문턱", rd[11], 1010.0)
    s.close()

    # ---- 5. 속성 값 타입은 입력 그대로 저장된다 (읽은 값의 값과 타입, undo·다시 열기 포함)
    s = kit.Session(work / "g5.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    seq = [1, 1.0, True, 2.5, 0.5, 5, "abc", 5, 1.0, 1, False, 0, "5"]   # 1 -> 1.0 -> True 처럼 값은 같고 타입만 다른 이웃 포함
    prev = []
    for val in seq:
        s.run("pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": val})
        got = s.eng.ifc.get_psets(F.W1).get("Pset_Z", {}).get("P")
        if got != val or type(got) is not type(val):
            v.append(f"속성 {val!r} 을 썼더니 {got!r}({type(got).__name__}) 로 저장됨")
        prev.append(val)
    for want in reversed(prev[:-1]):
        s.run("history.undo")
        got = s.eng.ifc.get_psets(F.W1).get("Pset_Z", {}).get("P")
        if got != want or type(got) is not type(want):
            v.append(f"undo 뒤 {got!r} != {want!r}")
    e2 = kit.Engine(s.path, "re")
    got = e2.ifc.get_psets(F.W1).get("Pset_Z", {}).get("P")
    if got != prev[0] or type(got) is not type(prev[0]):
        v.append(f"다시 연 엔진의 값 {got!r} != {prev[0]!r}")
    e2.close()
    s.close()
    return v


if __name__ == "__main__":
    import sys
    import tempfile
    res = check_golden(Path(tempfile.mkdtemp()))
    print("\n".join(res) or "골든 값 통과")
    sys.exit(1 if res else 0)
