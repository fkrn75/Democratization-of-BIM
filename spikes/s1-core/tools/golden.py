# SPDX-License-Identifier: MPL-2.0
"""골든 값 시험: 질의 함수와 독립적으로 손으로 계산한 기대값.

증분 그래프와 기준 그래프는 같은 질의 함수를 공유하므로 질의의 의미 오류(면적 공식, 경계 비교 등)는
차등 시험으로 잡히지 않는다. 여기의 기대값은 코드를 보지 않고 계산한 숫자이며 계산 근거를 주석에 적었다.
위반 문자열 목록을 돌려준다(빈 목록 = 통과).
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import kit
from dobim_core import fixture as F, registry

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

    # ---- 6. 정확한 경계 (손 계산)
    # (a) MIN_TRIMMED: 길이 100 벽, 수직 벽이 x=49.5 와 x=50.5 에서 교차 -> 시작 49.5, 끝 50.5, 차 정확히 1.0.
    #     차가 1.0 미만일 때만 퇴화이므로 정상(ok)이고 길이 1.0.
    s = kit.Session(work / "g6.dobim")
    s.run("level.create", {"id": LV, "name": "L", "elevation": 0})
    s.run("wall.create", {"id": A, "level": LV, "axis": [0, 0, 100, 0]})
    s.run("wall.create", {"id": X1, "level": LV, "axis": [49.5, -500, 49.5, 500]})
    s.run("wall.create", {"id": X2, "level": LV, "axis": [50.5, -500, 50.5, 500]})
    s.run("join.set", {"a": A, "b": X1})
    s.run("join.set", {"a": A, "b": X2})
    expect("MIN_TRIMMED 경계 trim", s.eng.g.q(Q.wall_trim, A), (49.5, 50.5, "ok"))
    s.close()
    # (b) JOIN_MAX_EXT: 길이 6000 벽. 교차가 시작에서 정확히 1000 이면 정리되고(<=), 1000.5 이면 무시된다.
    for nm, cx, want in (("정확히 1000", 1000.0, (1000.0, 6000.0, "ok")), ("1000.5", 1000.5, (0.0, 6000.0, "ok"))):
        s = kit.Session(work / f"g7-{cx}.dobim")
        s.run("level.create", {"id": LV, "name": "L", "elevation": 0})
        s.run("wall.create", {"id": A, "level": LV, "axis": [0, 0, 6000, 0]})
        s.run("wall.create", {"id": X1, "level": LV, "axis": [cx, -500, cx, 500]})
        s.run("join.set", {"a": A, "b": X1})
        expect(f"JOIN_MAX_EXT 경계({nm})", s.eng.g.q(Q.wall_trim, A), want)
        s.close()
    # (c) 중간 동률: 길이 100 벽, 교차 t=50 은 |t| <= |t-L| 이라 시작 쪽으로 배정 -> (50, 100). 끝 쪽이면 (0, 50) 이 된다.
    s = kit.Session(work / "g8.dobim")
    s.run("level.create", {"id": LV, "name": "L", "elevation": 0})
    s.run("wall.create", {"id": A, "level": LV, "axis": [0, 0, 100, 0]})
    s.run("wall.create", {"id": X1, "level": LV, "axis": [50, -500, 50, 500]})
    s.run("join.set", {"a": A, "b": X1})
    expect("중간 동률 trim", s.eng.g.q(Q.wall_trim, A), (50.0, 100.0, "ok"))
    s.close()
    return v


def check_failure_atomicity(work: Path) -> list[str]:
    """속성 쓰기가 '지운 뒤' 실패하는 경로(N1)와 값 검증(N4)을 시험한다. 위반 목록을 돌려준다."""
    import ifcopenshell.api.pset as api_pset
    import dobim_core.ifc_model as im
    v = []
    work = Path(tempfile.mkdtemp(dir=Path(work), prefix="atom-"))
    s = kit.Session(work / "a1.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    eng = s.eng
    prop = lambda: eng.ifc.get_psets(F.W1).get("Pset_Z", {}).get("P")   # noqa: E731
    s.run("pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": 1})
    before = eng.hashes()

    # 지운 직후의 쓰기에서 실패시킨다(edit_pset 이 None 으로 지운 다음, 값을 쓰는 첫 호출만 예외).
    real, st = api_pset.edit_pset, {"removed": False, "failed": False}

    def flaky(f, pset, properties=None, **kw):
        if properties and None in properties.values():
            st["removed"] = True
        elif st["removed"] and not st["failed"]:
            st["failed"] = True
            raise RuntimeError("지운 뒤 실패(시험용)")
        return real(f, pset=pset, properties=properties, **kw)

    api_pset.edit_pset = flaky
    try:
        r = s.run("pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": 2.5})
    finally:
        api_pset.edit_pset = real
    if r["ok"]:
        v.append("지운 뒤 실패를 주입했는데 성공으로 끝남(주입이 동작하지 않음)")
    if prop() != 1 or type(prop()) is not int:
        v.append(f"삭제 뒤 실패하자 속성이 사라지거나 바뀜: {prop()!r}")
    if eng.hashes() != before:
        v.append("삭제 뒤 실패한 쓰기가 해시를 바꿈")
    s.run("pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": 7})
    s.run("history.undo")
    if prop() != 1 or type(prop()) is not int:
        v.append(f"뒤이은 쓰기를 undo 했더니 {prop()!r} (1 이어야 함)")
    d = kit.full_check(s, work, tag="-atom")
    if not d["ok"]:
        v.append(f"차등 검사 실패: {d['diffs'][:2]}")
    s.close()

    # N4: 저장할 수 없는 값은 스키마에서 거부된다(상태 불변)
    s = kit.Session(work / "a2.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    s.run("pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": 1})
    before = s.eng.hashes()
    for bad in (float("nan"), float("inf"), float("-inf"), 2**63, -2**63 - 1, "a\x00b", "\ud800", "x\udfffy"):
        r = s.run("pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": bad})
        if r["ok"] or r["error"]["code"] != "SCHEMA_INVALID":
            v.append(f"저장 불가 값 {bad!r} 이 SCHEMA_INVALID 로 거부되지 않음: {r.get('error', {}).get('code', 'ok')}")
    for good in (2**63 - 1, -2**63, 1e308, "한글"):
        r = s.run("pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": "P", "value": good})
        if not r["ok"] or prop_of(s) != good:
            v.append(f"허용 경계값 {good!r} 이 거부되거나 다르게 저장됨")
    bad_name = s.run("level.create", {"name": "a\x00b", "elevation": 0})
    if bad_name["ok"]:
        v.append("이름의 NUL 이 허용됨")
    s.close()
    return v


def prop_of(s):
    return s.eng.ifc.get_psets(F.W1).get("Pset_Z", {}).get("P")


LAST_OUTCOMES: dict = {}   # check_restore_failure 가 마지막으로 센 결과 분포(보고용)


def _core(h: dict) -> dict:
    return {k: x for k, x in h.items() if k != "rev"}


def check_delete_and_names(work: Path) -> list[str]:
    """N7: value=null 은 속성 삭제가 아니라 스키마 오류이고 삭제는 pset.delete 로만 한다(삭제 효과의 undo·재생·해시는 그대로).
    N9: 속성 이름 "id" 는 스키마에서 거부하고, 변형("ID", "Id")은 왕복·삭제가 된다. 위반 목록을 돌려준다."""
    v = []
    work = Path(tempfile.mkdtemp(dir=Path(work), prefix="del-"))
    s = kit.Session(work / "d1.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    eng = s.eng
    never = _core(eng.hashes())                                  # 속성을 한 번도 쓰지 않은 상태의 해시
    ARG = {"id": F.W1, "pset": "Pset_Z", "prop": "P"}
    s.run("pset.set", {**ARG, "value": 1})
    with_p = _core(eng.hashes())

    # N7: null 은 어떤 경로로도 삭제가 되지 않는다(공식 SDK 가 NaN/inf 를 null 로 바꿔 보내므로 NaN 과 구분할 수 없다)
    r = s.run("pset.set", {**ARG, "value": None})
    if r["ok"] or r["error"]["code"] != "SCHEMA_INVALID" or not r["error"]["path"].startswith("/value"):
        v.append(f"value=null 이 SCHEMA_INVALID 로 거부되지 않음: {r.get('error', r)}")
    if prop_of(s) != 1 or _core(eng.hashes()) != with_p:
        v.append("value=null 이 속성을 지우거나 상태를 바꿨다")
    b = s.batch([{"command": "pset.set", "args": {**ARG, "value": None}}])
    if b["ok"] or prop_of(s) != 1:
        v.append("배치 안의 value=null 이 속성을 지웠다")
    if "value" not in registry.load()["pset.set"].args_model.model_json_schema()["required"]:
        v.append("pset.set 의 value 가 필수가 아니다")

    # 삭제: pset.delete. 해시는 '한 번도 쓰지 않은 상태'와 같아야 하고(빈 속성셋이 남지 않는다), undo 와 redo 가 맞아야 한다
    r = s.run("pset.delete", ARG)
    if not r["ok"] or r["result"] != {"changed": True} or prop_of(s) is not None:
        v.append(f"pset.delete 가 속성을 지우지 못함: {r}")
    if "Pset_Z" in eng.ifc.get_psets(F.W1) or _core(eng.hashes()) != never:
        v.append("삭제 뒤 해시가 '쓴 적 없는 상태'와 다르다(빈 속성셋이 남았거나 상태가 다름)")
    if s.run("history.undo")["ok"] is not True or prop_of(s) != 1 or type(prop_of(s)) is not int or _core(eng.hashes()) != with_p:
        v.append("삭제를 undo 했더니 값(타입 포함)이나 해시가 복원되지 않음")
    if s.run("history.redo")["ok"] is not True or prop_of(s) is not None or _core(eng.hashes()) != never:
        v.append("삭제를 redo 했더니 지워지지 않음")
    rev = eng.rev
    r = s.run("pset.delete", ARG)                                # 이미 없다: 변경 없음, 로그 불변
    if not r["ok"] or r["result"] != {"changed": False} or eng.rev != rev or not any("변경 없음" in w for w in r["warnings"]):
        v.append(f"없는 속성 삭제가 '변경 없음'이 아님: {r}")
    if s.run("pset.delete", {**ARG, "pset": "DoBIM_Intent", "prop": "Intent"})["error"]["code"] != "CONSTRAINT":
        v.append("예약 속성셋 DoBIM_Intent 의 삭제가 거부되지 않음")
    if s.run("pset.delete", {**ARG, "id": "9" * 22})["error"]["code"] != "NOT_FOUND":
        v.append("없는 요소의 속성 삭제가 NOT_FOUND 가 아님")
    d = kit.full_check(s, work, tag="-del")
    if not d["ok"]:
        v.append(f"삭제 뒤 차등 검사 실패: {d['diffs'][:2]}")
    s.close()

    # N9: "id" 는 거부, 변형은 왕복·삭제 가능
    s = kit.Session(work / "d2.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    eng = s.eng
    base_h = _core(eng.hashes())
    for cmd, extra in (("pset.set", {"value": 5}), ("pset.delete", {})):
        r = s.run(cmd, {"id": F.W1, "pset": "Pset_Z", "prop": "id", **extra})
        if r["ok"] or r["error"]["code"] != "SCHEMA_INVALID" or r["error"]["path"] != "/prop":
            v.append(f'{cmd} 가 속성 이름 "id" 를 SCHEMA_INVALID(/prop)로 거부하지 않음: {r.get("error", r)}')
    if _core(eng.hashes()) != base_h:
        v.append('"id" 거부가 상태를 바꿨다')
    for name in ("ID", "Id", "iD", "id2", "Pid"):
        r = s.run("pset.set", {"id": F.W1, "pset": "Pset_Z", "prop": name, "value": 7})
        seen = s.run("element.get", {"id": F.W1})["result"]["psets"].get("Pset_Z", {}).get(name)
        if not r["ok"] or seen != 7:
            v.append(f'속성 이름 "{name}" 이 왕복되지 않음(쓰기 {r["ok"]}, 조회 {seen!r})')
        r = s.run("pset.delete", {"id": F.W1, "pset": "Pset_Z", "prop": name})
        if not r["ok"] or "Pset_Z" in s.run("element.get", {"id": F.W1})["result"]["psets"]:
            v.append(f'속성 이름 "{name}" 을 지우지 못함')
    if _core(eng.hashes()) != base_h:
        v.append("변형 이름을 쓰고 지운 뒤 해시가 처음과 다르다")
    d = kit.full_check(s, work, tag="-ids")
    if not d["ok"]:
        v.append(f"이름 변형 뒤 차등 검사 실패: {d['diffs'][:2]}")
    s.close()
    return v


def check_restore_failure(work: Path) -> list[str]:
    """N8: 쓰기가 실패한 뒤 복원마저 실패해도 속성이 조용히 사라지지 않는다.
    edit_pset 이 k 번째 호출부터 w 번 연속(None=영구) 실패하도록 주입하고 타입이 바뀌는 쓰기(1 -> 2.5)를 실행한다.
    어느 경우에도 (a) 오염 표시가 없는 엔진은 로그에서 만든 새 엔진과 같고 (b) 오염된 엔진은 정상 응답이 아니라
    ENGINE_TAINTED 로 거부하며 (c) 주입을 걷으면 자동으로 복구돼 로그와 같아져야 한다. 위반 목록을 돌려준다."""
    import ifcopenshell.api.pset as api_pset
    v = []
    work = Path(tempfile.mkdtemp(dir=Path(work), prefix="rst-"))
    real = api_pset.edit_pset
    ARG = {"id": F.W1, "pset": "Pset_Z", "prop": "P"}
    outcomes = {"성공": 0, "실패_정상": 0, "실패_오염_거부": 0, "실패_오염_복구": 0}
    for k in (1, 2, 3, 4):
        for w in (1, 2, 3, 5, None):
            tag = f"k{k}w{w}"
            s = kit.Session(work / f"{tag}.dobim")
            for n, a in F.SCRIPT:
                s.run(n, a)
            s.run("pset.set", {**ARG, "value": 1})
            eng = s.eng
            calls = [0]

            def flaky(f, pset, properties=None, **kw):
                calls[0] += 1
                if calls[0] >= k and (w is None or calls[0] < k + w):
                    raise RuntimeError(f"주입 실패 #{calls[0]}")
                return real(f, pset=pset, properties=properties, **kw)

            api_pset.edit_pset = flaky
            try:
                r = s.run("pset.set", {**ARG, "value": 2.5})
                want = 2.5 if r["ok"] else 1
                if not r["ok"] and eng.tainted is None:
                    outcomes["실패_정상"] += 1
                elif not r["ok"]:
                    outcomes["실패_오염_거부"] += 1
                else:
                    outcomes["성공"] += 1
                # 오염 표시가 없다면 조회·쓰기는 정상이고 값이 맞아야 한다. 오염이면 ENGINE_TAINTED 로 거부하거나(복구 실패) 복구한 뒤 응답한다.
                for probe in (("element.get", {"id": F.W1}), ("wall.move", {"id": F.W2, "dx": 0}), ("project.info", {})):
                    was_tainted = eng.tainted is not None
                    q = s.run(*probe)
                    if q["ok"]:
                        if eng.tainted is not None:
                            v.append(f"{tag}: 오염 상태인데 {probe[0]} 가 정상 응답함")
                        if probe[0] == "element.get":
                            got = q["result"]["psets"].get("Pset_Z", {}).get("P")
                            if got != want or type(got) is not type(want):
                                v.append(f"{tag}: 조회한 값 {got!r} (기대 {want!r}): 속성이 조용히 사라지거나 바뀜")
                        if was_tainted:
                            outcomes["실패_오염_복구"] += 1
                    elif q["error"]["code"] != "ENGINE_TAINTED":
                        v.append(f"{tag}: 오염된 엔진의 {probe[0]} 가 ENGINE_TAINTED 가 아닌 {q['error']['code']} 로 실패")
                    elif eng.tainted is None:
                        v.append(f"{tag}: ENGINE_TAINTED 를 돌려줬는데 오염 표시가 없다")
            finally:
                api_pset.edit_pset = real
            # 주입을 걷으면 다음 호출에서 자동 복구되고, 로그·재생·새로 열기와 일치해야 한다
            r2 = s.run("project.info")
            if not r2["ok"] or eng.tainted is not None:
                v.append(f"{tag}: 주입을 걷어도 복구되지 않음: {r2.get('error', r2)}")
                s.close()
                continue
            got = eng.ifc.get_psets(F.W1).get("Pset_Z", {}).get("P")
            if got != want or type(got) is not type(want):
                v.append(f"{tag}: 복구 뒤 값 {got!r} (기대 {want!r})")
            d = kit.full_check(s, work, tag=f"-{tag}")
            if not d["ok"]:
                v.append(f"{tag}: 복구 뒤 차등 검사 실패: {d['diffs'][:2]}")
            if not s.run("pset.set", {**ARG, "value": "after"})["ok"]:
                v.append(f"{tag}: 복구 뒤 쓰기가 실패함")
            s.close()
    if not outcomes["실패_오염_거부"] + outcomes["실패_오염_복구"]:
        v.append("주입이 복원 실패 경로를 한 번도 밟지 못했다(시험 무효)")
    LAST_OUTCOMES.update(outcomes)

    # 재료 효과도 같은 경로: 모든 assign_material 이 실패하는 동안의 재료 교체
    import dobim_core.ifc_model as im
    real_m = im.api_material.assign_material
    s = kit.Session(work / "mat.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    eng = s.eng
    im.api_material.assign_material = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("주입 실패(재료)"))
    try:
        r = s.run("material.set", {"id": F.W1, "name": "Steel"})
        if r["ok"]:
            v.append("재료 주입 실패가 실패로 보고되지 않음")
        q = s.run("element.get", {"id": F.W1})
        if q["ok"] and (eng.tainted is not None or q["result"]["material"] != "Concrete"):
            v.append(f"재료 복원 실패 뒤 조회가 틀린 응답을 줌: {q['result']['material']!r}, 오염 {eng.tainted}")
        if not q["ok"] and q["error"]["code"] != "ENGINE_TAINTED":
            v.append(f"재료 복원 실패 뒤 조회가 ENGINE_TAINTED 가 아님: {q['error']['code']}")
    finally:
        im.api_material.assign_material = real_m
    q = s.run("element.get", {"id": F.W1})
    if not q["ok"] or q["result"]["material"] != "Concrete" or eng.tainted is not None:
        v.append(f"재료: 주입을 걷은 뒤 복구되지 않음: {q.get('error', q.get('result', {}).get('material'))}")
    d = kit.full_check(s, work, tag="-mat")
    if not d["ok"]:
        v.append(f"재료: 복구 뒤 차등 검사 실패: {d['diffs'][:2]}")
    s.close()
    return v


def check_envelope_and_nulls(work: Path) -> list[str]:
    """(1) 예상 밖 예외는 조회·배치·엔진 열기 어디서든 INTERNAL 봉투가 된다(메시지에 경로·내부 값 없음).
    (2) N10: wall.set/door.set 의 명시적 null(= SDK 가 NaN/inf 를 바꿔 보낸 값)은 SCHEMA_INVALID 이고 아무것도 바꾸지 않는다.
    (3) 로그 인자에는 생략한 선택 필드의 null 이 없다. 위반 목록을 돌려준다."""
    import json as _json
    from unittest import mock
    from dobim_core import commands, dispatch
    v = []
    work = Path(tempfile.mkdtemp(dir=Path(work), prefix="nul-"))
    s = kit.Session(work / "n1.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    eng = s.eng
    # ---- (1) 조회 경로의 비 DobimError
    eng.ifc.ents.pop(F.W3)                                   # 조회 중 KeyError 를 일으키는 상태
    r = s.run("element.get", {"id": F.W3})
    if r["ok"] or r["error"]["code"] != "INTERNAL":
        v.append(f"조회 중 KeyError 가 INTERNAL 봉투가 아님: {r}")
    elif F.W3 in r["error"]["message"] or "Traceback" in r["error"]["message"] or "\\" in r["error"]["message"]:
        v.append(f"INTERNAL 메시지에 내부 값이나 스택이 들어 있다: {r['error']['message']}")
    s.close()
    secret = "C:\\secret\\place"
    env = dispatch.Env(work)
    with mock.patch.object(dispatch, "get_command", side_effect=RuntimeError(secret)):
        for label, call in (("run", lambda: dispatch.run(env, "project.info", {}, project="n1.dobim")),
                            ("run_batch", lambda: dispatch.run_batch(env, [{"command": "wall.move", "args": {}}], project="n1.dobim"))):
            try:
                r = call()
            except BaseException as ex:
                v.append(f"{label}: 봉투 없는 예외 {type(ex).__name__}")
                continue
            if r["ok"] or r["error"]["code"] != "INTERNAL" or "secret" in r["error"]["message"]:
                v.append(f"{label}: INTERNAL 봉투가 아니거나 예외 문장이 샜다: {r.get('error')}")
    (work / "garbage.dobim").write_bytes(b"this is not a sqlite database" * 20)
    try:
        r = dispatch.run(env, "project.info", {}, project="garbage.dobim")
        if r["ok"] or r["error"]["code"] != "INTERNAL":
            v.append(f"깨진 프로젝트 파일 열기가 INTERNAL 이 아님: {r}")
    except BaseException as ex:
        v.append(f"깨진 프로젝트 파일 열기가 봉투 없는 예외: {type(ex).__name__}")
    env.close()

    # ---- (2) 명시적 null
    s = kit.Session(work / "n2.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    eng = s.eng
    before = eng.hashes()
    cases = [("wall.set", {"id": F.W1, "thickness": None, "height": 3100}, "/thickness"),
             ("wall.set", {"id": F.W1, "thickness": 250, "height": None}, "/height"),
             ("wall.set", {"id": F.W1, "axis": None, "thickness": 250}, "/axis"),
             ("wall.set", {"id": F.W1, "name": None, "thickness": 250}, "/name"),
             ("wall.set", {"id": F.W1, "thickness": None}, "/thickness"),
             ("door.set", {"id": F.D1, "width": 1000, "offset": None}, "/offset"),
             ("door.set", {"id": F.D1, "width": 1000, "sill": None}, "/sill"),
             ("door.set", {"id": F.D1, "width": 1000, "name": None}, "/name")]
    for name, args, path in cases:
        r = s.run(name, args)
        if r["ok"] or r["error"]["code"] != "SCHEMA_INVALID" or r["error"]["path"] != path:
            v.append(f"{name} {args} 의 명시적 null 이 {path} SCHEMA_INVALID 로 거부되지 않음: {r.get('error', 'ok')}")
        b = s.batch([{"command": name, "args": args}])
        if b["ok"]:
            v.append(f"배치 안의 {name} 명시적 null 이 받아들여졌다")
    if eng.hashes() != before:
        v.append("null 거부가 상태를 바꿨다")
    r = s.run("wall.set", {"id": F.W1, "thickness": 250})   # 생략은 여전히 '바꾸지 않음'
    if not r["ok"] or eng.st.el[F.W1]["params"]["thickness"] != 250 or eng.st.el[F.W1]["params"]["height"] != 3000:
        v.append(f"생략한 필드를 바꾸지 않는 동작이 깨졌다: {r}")
    # 레지스트리 전체: 숫자 선택 필드(`X | None`)는 어떤 커맨드든 명시적 null 을 거부한다
    for cname, c in sorted(registry.load().items()):
        for fname, fi in c.args_model.model_fields.items():
            if commands._optional_numeric(fi.annotation) or (getattr(c.args_model, "forbid_null", False) and type(None) in __import__("typing").get_args(fi.annotation)):
                ok = True
                try:
                    c.args_model.model_validate({**{k: x for k, x in (c.examples[0] or {}).items()}, fname: None})
                except Exception:
                    ok = False
                if ok:
                    v.append(f"{cname}.{fname}: 명시적 null 을 받아들임")
    # ---- (3) 로그 인자
    s.run("wall.set", {"id": F.W1, "height": 3200})
    s.run("level.create", {"name": "ZZ", "elevation": 99})
    last = [_json.loads(r[4]) for r in kit.log_rows(eng)][-2:]
    if any(x is None for a in last for x in a.values()):
        v.append(f"로그 인자에 null 이 남았다: {last}")
    d = kit.full_check(s, work, tag="-nul")
    if not d["ok"]:
        v.append(f"차등 검사 실패(null 거부 뒤): {d['diffs'][:2]}")
    s.close()
    return v


def check_remove_identity_order(work: Path) -> list[str]:
    """D1: remove_identity 는 IFC 삭제가 실패하면 엔티티 목록에서 빼지 않는다(순서: 지운 뒤에 뺀다). 개구부도 마찬가지."""
    import ifcopenshell.api.feature as api_feature
    import ifcopenshell.api.root as api_root
    v = []
    work = Path(tempfile.mkdtemp(dir=Path(work), prefix="rid-"))
    s = kit.Session(work / "r1.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    s.eng.hashes()
    ifc = s.eng.ifc
    real = api_root.remove_product

    def boom(*a, **k):
        raise RuntimeError("주입")

    api_root.remove_product = boom
    try:
        try:
            ifc.remove_identity(F.W3)
            v.append("remove_product 실패가 전파되지 않았다")
        except RuntimeError:
            pass
    finally:
        api_root.remove_product = real
    if F.W3 not in ifc.ents:
        v.append("remove_product 가 실패했는데 엔티티 목록에서 먼저 빠졌다")
    real_f = api_feature.remove_feature
    api_feature.remove_feature = boom
    try:
        try:
            ifc.remove_identity(F.D1)
        except RuntimeError:
            pass
    finally:
        api_feature.remove_feature = real_f
    if F.D1 not in ifc.ents or F.D1 not in ifc.openings:
        v.append("remove_feature 가 실패했는데 문·개구부 기록이 먼저 빠졌다")
    s.close()
    return v


def check_response_cap(work: Path, cap: int | None = None) -> list[str]:
    """목록형 커맨드의 응답 크기 상한: 봉투 전체가 상한 이하이고, 잘리면 truncated 와 next_cursor 가 있으며,
    이어받기로 전체를 읽으면 기준(상한 없이 읽은 결과)과 빠짐·중복 없이 같다."""
    import json as _json
    from unittest import mock
    from dobim_core import commands, limits
    from dobim_core import queries as Q
    v = []
    work = Path(tempfile.mkdtemp(dir=Path(work), prefix="cap-"))
    s = kit.Session(work / "c1.dobim")
    s.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    cmds = [{"command": "wall.create", "args": {"id": kit.mkid("1", i), "level": F.LV1, "axis": [0, i * 300, 6000, i * 300],
                                                "name": f"긴 벽 이름 {i:04d} " + "가" * 40}} for i in range(1, 281)]
    assert s.batch(cmds)["ok"]
    cap = cap or limits.RESPONSE_CAP_BYTES
    expect_ids = sorted(s.eng.st.el)
    log_seqs = [r[0] for r in s.eng.conn.execute("SELECT seq FROM op_log ORDER BY seq DESC").fetchall()]
    sched = [list(r) for r in s.eng.g.q(Q.schedule_sorted, "wall")]
    plans = [("element.query", {"limit": 1000}, lambda it: it["id"], expect_ids),
             ("schedule.rows", {"kind": "wall", "limit": 1000}, lambda it: it[0], [r[0] for r in sched]),
             ("history.list", {"limit": 500}, lambda it: it["seq"], log_seqs),
             ("relation.list", {"id": F.LV1, "limit": 1000}, lambda it: it["src"], None)]
    for name, base, key, expect in plans:
        keys, pages, truncs, sizes, cursor = [], 0, [], [], None
        while True:
            args = dict(base, **({"cursor": cursor} if cursor else {}))
            r = s.run(name, args)
            if not r["ok"]:
                v.append(f"{name}: 실패 {r['error']['code']}")
                break
            pages += 1
            sizes.append(len(_json.dumps(r, ensure_ascii=False).encode("utf-8")))
            truncs.append(r["page"]["truncated"])
            keys += [key(it) for it in r["result"]["items"]]
            cursor = r["page"]["next_cursor"]
            if r["page"]["truncated"] and cursor is None:
                v.append(f"{name}: truncated 인데 next_cursor 가 없다")
            if cursor is None or pages > 400:
                break
        if expect is None:
            expect = list(s.eng.st.inn.get(("on_level", F.LV1), ()))
        if pages < 2:
            v.append(f"{name}: 상한이 적용되지 않았다(쪽 수 {pages})")
        if max(sizes) > cap:
            v.append(f"{name}: 응답 {max(sizes)}바이트가 상한 {cap} 를 넘는다")
        if not any(truncs[:-1]) or truncs[-1]:
            v.append(f"{name}: truncated 표시가 이상하다 {truncs[:3]}…{truncs[-2:]}")
        if keys != expect:
            v.append(f"{name}: 이어받기 결과가 기준과 다르다(빠짐 {len(set(expect) - set(keys))}, 중복 {len(keys) - len(set(keys))}, "
                     f"순서 {'같음' if sorted(keys) == sorted(expect) else '다름'})")
        if len(keys) != len(set(keys)):
            v.append(f"{name}: 중복 항목")
    s.close()
    return v


def check_file_lock(work: Path) -> list[str]:
    """ifc.export/ifc.restore 가 공유 없이 열린 대상을 만나면 FILE_LOCKED 봉투이고, 원본 불변·.tmp 잔재 없음·스냅숏 증가 없음.
    잠금을 풀면 다시 성공한다. Windows 전용(그 밖에서는 시험하지 않고 빈 목록)."""
    import ctypes
    if sys.platform != "win32":
        return []
    v = []
    work = Path(tempfile.mkdtemp(dir=Path(work), prefix="lock-"))
    s = kit.Session(work / "k1.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)

    def confirmed(name, args):
        first = s.run(name, args)
        if first["ok"] or first["error"]["code"] != "CONFIRM_REQUIRED":
            return first
        return s.run(name, args, confirm=first["error"]["details"]["confirm_token"])

    target = work / "locked.ifc"
    if not confirmed("ifc.export", {"path": "locked.ifc"})["ok"] or not confirmed("ifc.export", {"path": "locked.ifc"})["ok"]:
        return ["준비 내보내기 실패"]
    before = target.read_bytes()
    snaps = sorted(p.name for p in (work / ".dobim-snapshots").iterdir())
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_void_p
    for share, label in ((0, "공유 없음"), (1, "읽기 공유")):
        h = k32.CreateFileW(str(target), 0x80000000, share, None, 3, 0x80, None)   # 읽기로 열기
        try:
            for name, args in (("ifc.export", {"path": "locked.ifc"}), ("ifc.restore", {"path": "locked.ifc", "snapshot": 1})):
                r = confirmed(name, args)
                if r["ok"] or r["error"]["code"] != "FILE_LOCKED":
                    v.append(f"{label}: 열린 파일에 {name} 가 FILE_LOCKED 가 아님: {r.get('error', 'ok')}")
        finally:
            k32.CloseHandle(ctypes.c_void_p(h))
        if target.read_bytes() != before:
            v.append(f"{label}: 잠금 중 실패했는데 원본이 바뀌었다")
        if sorted(work.glob("*.tmp")):
            v.append(f"{label}: .tmp 잔재 {[p.name for p in work.glob('*.tmp')]}")
        if sorted(p.name for p in (work / ".dobim-snapshots").iterdir()) != snaps:
            v.append(f"{label}: 실패했는데 스냅숏이 늘었다")
    if not confirmed("ifc.export", {"path": "locked.ifc"})["ok"]:
        v.append("잠금을 푼 뒤 내보내기가 성공하지 못함")
    s.close()
    return v


def _mk(i, p="1"):
    return p + f"{i:021d}"


def _paged(s, name, base, key, after_first=None):
    """이어받기로 끝까지 읽는다. 첫 쪽을 읽은 직후 after_first(첫 쪽 항목) 를 실행한다. (읽은 키 목록, 쪽 수)"""
    keys, pages, cursor = [], 0, None
    while True:
        r = s.run(name, dict(base, **({"cursor": cursor} if cursor else {})))
        if not r["ok"]:
            raise AssertionError(f"{name}: {r['error']['code']} {r['error'].get('message')}")
        its = r["result"]["items"]
        keys += [key(i) for i in its]
        pages += 1
        if pages == 1 and after_first:
            after_first(its)
        cursor = r["page"]["next_cursor"]
        if cursor is None:
            return keys, pages
        if not its or pages > 3000:
            raise AssertionError(f"{name}: 빈 쪽에서 next_cursor 를 돌려주거나 끝나지 않는다")


def check_keyset_paging(work: Path) -> list[str]:
    """A: 이어받는 동안 모델이 바뀌어도 키셋 커서는 빠짐·중복이 없다.
    의미(정의): 시작과 끝에 모두 있고 정렬 키(이름·레벨)가 바뀌지 않은 항목은 정확히 한 번 나온다. 삭제된 항목은 0~1번,
    새로 삽입된 항목은 읽은 위치 뒤에 놓일 때만 1번(앞이면 0번), 이름이 바뀌어 정렬 키가 달라진 항목은 새 키 위치로 옮겨 가므로 0~2번
    (읽은 뒤 뒤쪽으로 옮겨 가면 다시 나오고, 읽기 전 앞쪽으로 옮겨 가면 빠질 수 있다). 커서로 쓴 행이 삭제돼도 동작한다."""
    v = []
    work = Path(tempfile.mkdtemp(dir=Path(work), prefix="ks-"))
    N = 40

    def build(tag):
        s = kit.Session(work / f"{tag}.dobim")
        s.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
        cmds = [{"command": "wall.create", "args": {"id": _mk(i), "level": F.LV1, "axis": [0, i * 300, 6000, i * 300],
                                                    "name": f"W{i:03d}"}} for i in range(1, N + 1)]
        cmds += [{"command": "door.place", "args": {"id": _mk(i, "2"), "host": _mk(i), "offset": 1500, "width": 900, "name": f"D{i:03d}"}}
                 for i in range(1, N // 2 + 1)]
        assert s.batch(cmds)["ok"]
        return s

    def new_wall(s, nm, i=900):
        s.run("wall.create", {"id": _mk(i), "level": F.LV1, "axis": [0, 99999 + i, 6000, 99999 + i], "name": nm})

    for kind in ("wall", "door"):
        if kind == "wall":
            ins = lambda s, nm: s.run("wall.create", {"id": _mk(900), "level": F.LV1, "axis": [0, 99999, 6000, 99999], "name": nm})   # noqa: E731
            ren = lambda s, i, nm: s.run("wall.set", {"id": i, "name": nm})                                                             # noqa: E731
        else:
            ins = lambda s, nm: s.run("door.place", {"id": _mk(900, "2"), "host": _mk(N), "offset": 3000, "width": 900, "name": nm})   # noqa: E731
            ren = lambda s, i, nm: s.run("door.set", {"id": i, "name": nm})                                                             # noqa: E731
        last = _mk(N // 2, "2") if kind == "door" else _mk(N)
        # (이름, 첫 쪽 뒤에 하는 변경(s, 첫 쪽 항목), 이름이 바뀌는 id 를 고르는 함수 또는 None)
        scen = [
            ("읽은 행 삭제", lambda s, its: s.run("element.delete", {"id": its[1][0]}), None),
            ("커서로 쓴 행(마지막 읽은 행) 삭제", lambda s, its: s.run("element.delete", {"id": its[-1][0]}), None),
            ("읽지 않은 행 삭제", lambda s, its: s.run("element.delete", {"id": last}), None),
            ("앞쪽에 새 행 삽입", lambda s, its: ins(s, "A-front"), None),
            ("뒤쪽에 새 행 삽입", lambda s, its: ins(s, "Z-back"), None),
            ("읽은 행의 이름을 뒤로 보냄", lambda s, its: ren(s, its[0][0], "zzz-moved"), lambda its: its[0][0]),
            ("읽지 않은 행의 이름을 앞으로 보냄", lambda s, its: ren(s, last, "A-moved"), lambda its: last),
        ]
        for label, mut, moved_of in scen:
            tag = f"{kind}-{abs(hash(label)) % 10 ** 6}"
            s = build(tag)
            first_page = []
            base = {"kind": kind, "limit": 7}
            start_keys, _ = _paged(s, "schedule.rows", dict(base, limit=1000), lambda r: r[0])

            def after(its, s=s, mut=mut, first_page=first_page):
                first_page.extend(its)
                mut(s, its)
            keys, pages = _paged(s, "schedule.rows", base, lambda r: r[0], after_first=after)
            end_keys, _ = _paged(s, "schedule.rows", dict(base, limit=1000), lambda r: r[0])
            moved = moved_of(first_page) if moved_of else None
            cnt = {k: keys.count(k) for k in set(keys)}
            if pages < 3:
                v.append(f"{label}/{kind}: 쪽이 너무 적다({pages})")
            for k in start_keys:
                if k not in end_keys:
                    if cnt.get(k, 0) > 1:
                        v.append(f"{label}/{kind}: 삭제된 {k[-4:]} 가 {cnt[k]}번")
                elif k == moved:
                    if cnt.get(k, 0) > 2:
                        v.append(f"{label}/{kind}: 이름이 바뀐 {k[-4:]} 가 {cnt[k]}번")
                elif cnt.get(k, 0) != 1:
                    v.append(f"{label}/{kind}: 시작·끝에 모두 있는 {k[-4:]} 가 {cnt.get(k, 0)}번 나왔다")
            for k, c in cnt.items():
                if k not in start_keys and c > 1:
                    v.append(f"{label}/{kind}: 삽입된 {k[-4:]} 가 {c}번")
            s.close()

    # relation.list (레벨에 걸린 벽 40개): 같은 규칙. 관계는 이름이 없으니 삭제·삽입만
    for label, mut in (("읽은 관계 삭제", lambda s, its: s.run("element.delete", {"id": its[1]["src"]})),
                       ("커서 관계 삭제", lambda s, its: s.run("element.delete", {"id": its[-1]["src"]})),
                       ("앞쪽 id 새 벽", lambda s, its: s.run("wall.create", {"id": "1" + "0" * 20 + "1", "level": F.LV1, "axis": [0, 1, 6000, 1]})),
                       ("뒤쪽 id 새 벽", lambda s, its: s.run("wall.create", {"id": "1" + "z" * 21, "level": F.LV1, "axis": [0, 2, 6000, 2]}))):
        s = build(f"rel-{label[:3]}")
        base = {"id": F.LV1, "limit": 7}
        start, _ = _paged(s, "relation.list", dict(base, limit=1000), lambda i: i["src"])
        keys, pages = _paged(s, "relation.list", base, lambda i: i["src"], after_first=lambda its, s=s, mut=mut: mut(s, its))
        end, _ = _paged(s, "relation.list", dict(base, limit=1000), lambda i: i["src"])
        for k in set(start) & set(end):
            if keys.count(k) != 1:
                v.append(f"relation.list/{label}: 시작·끝에 모두 있는 {k[-4:]} 가 {keys.count(k)}번")
        if len(keys) != len(set(keys)):
            v.append(f"relation.list/{label}: 중복")
        s.close()
    return v


def check_cursor_and_batch_input(work: Path) -> list[str]:
    """C: 위조·타입 불일치 커서는 모든 목록형 커맨드에서 SCHEMA_INVALID(/cursor)이고, 끝을 지난 올바른 모양의 커서는 빈 쪽이며
    next_cursor 가 없다. 배치 항목이 객체가 아니거나 command 가 문자열이 아니어도 INTERNAL 이 아니라 SCHEMA_INVALID 다."""
    import base64
    import json as _json
    v = []
    work = Path(tempfile.mkdtemp(dir=Path(work), prefix="cur-"))
    s = kit.Session(work / "c1.dobim")
    for n, a in F.SCRIPT:
        s.run(n, a)
    enc = lambda x: base64.urlsafe_b64encode(_json.dumps(x).encode()).decode()   # noqa: E731
    lists = [("schedule.rows", {"kind": "door", "limit": 1}), ("relation.list", {"id": F.LV1, "limit": 1}),
             ("element.query", {"limit": 1}), ("history.list", {"limit": 1}), ("history.affected", {"limit": 1}),
             ("commands.search", {"limit": 1}), ("ifc.snapshots", {"path": "x.ifc", "limit": 1})]
    forged = [("음수", enc(-3)), ("실수", enc(1.5)), ("문자열", enc("abc")), ("정수", enc(5)), ("리스트", enc([1])), ("null", enc(None)),
              ("거대 정수", enc(10 ** 30)), ("불리언", enc(True)), ("객체", enc({"a": 1})), ("깨진 base64", "###"), ("빈 문자열", ""),
              ("세 문자열 리스트", enc(["a", "b", "c"])), ("관계 모양 아님", enc([9, 9, "x"]))]
    for name, base in lists:
        for lab, cur in forged:
            # 그 커맨드의 올바른 모양인 커서는 제외(예: 세 문자열 리스트는 일람표에서는 올바른 모양)
            ok_shape = {("schedule.rows", "세 문자열 리스트"), ("element.query", "문자열"), ("commands.search", "문자열"),
                        ("history.list", "정수"), ("ifc.snapshots", "정수"), ("history.affected", "리스트")}
            if (name, lab) in ok_shape:
                continue
            r = s.run(name, dict(base, cursor=cur))
            if r["ok"] or r["error"]["code"] != "SCHEMA_INVALID" or r["error"]["path"] != "/cursor":
                v.append(f"{name} 위조 커서({lab}) 가 SCHEMA_INVALID(/cursor) 가 아님: {r.get('error', {}).get('code', 'ok')}")
    # 올바른 모양이지만 끝을 지난 커서: 빈 쪽, next_cursor 없음
    past = [("schedule.rows", {"kind": "door", "limit": 1}, enc(["zzzz", "zzzz", "9" * 22])),
            ("relation.list", {"id": F.LV1, "limit": 1}, enc([2, 1, "z" * 22])),
            ("element.query", {"limit": 1}, enc("z" * 22)), ("history.list", {"limit": 1}, enc(0)),
            ("history.affected", {"limit": 1}, enc([2, 10 ** 6, "z" * 22])), ("commands.search", {"limit": 1}, enc("zzzz")),
            ("ifc.snapshots", {"path": "x.ifc", "limit": 1}, enc(10 ** 6))]
    for name, base, cur in past:
        r = s.run(name, dict(base, cursor=cur))
        if not r["ok"] or r["result"]["items"] or r["page"]["next_cursor"] is not None:
            v.append(f"{name}: 끝을 지난 커서가 빈 쪽·next_cursor 없음이 아님: {r.get('error', r.get('page'))}")
    # 배치 항목 입력
    for lab, cmds in (("None 항목", [None]), ("문자열 항목", ["x"]), ("command 숫자", [{"command": 5}]), ("name 숫자", [{"name": 7}]),
                      ("commands 가 dict", {"a": 1}), ("commands 가 문자열", "abc"), ("args 리스트", [{"command": "wall.move", "args": [1]}])):
        r = s.batch(cmds)
        if r["ok"] or r["error"]["code"] != "SCHEMA_INVALID":
            v.append(f"배치 입력({lab}) 이 SCHEMA_INVALID 가 아님: {r.get('error', {}).get('code', 'ok')}")
    s.close()
    return v


def check_mutation_cap(work: Path) -> list[str]:
    """E: 큰 변경(배치) 응답도 상한 안에 든다. 변경은 그대로 끝나고, affected 는 앞부분과 정확한 건수와 truncated 를 돌려주며,
    history.affected 로 이어받으면 전체가 빠짐·중복 없이 나온다. undo/redo 는 같은 해시로 돌아온다."""
    import json as _json
    from dobim_core import limits
    v = []
    work = Path(tempfile.mkdtemp(dir=Path(work), prefix="mcap-"))
    s = kit.Session(work / "m1.dobim")
    s.run("level.create", {"id": F.LV1, "name": "1F", "elevation": 0})
    n = 600
    ids = [_mk(i + 1) for i in range(n)]
    eng = s.eng
    r = s.batch([{"command": "wall.create", "args": {"id": i, "level": F.LV1, "axis": [0, k * 10, 6000, k * 10]}} for k, i in enumerate(ids)])
    size = len(_json.dumps(r, ensure_ascii=False).encode("utf-8"))
    if not r["ok"]:
        return [f"큰 배치가 실패함: {r['error']}"]
    if size > limits.RESPONSE_CAP_BYTES:
        v.append(f"큰 배치 응답이 상한을 넘는다: {size} > {limits.RESPONSE_CAP_BYTES}")
    if len(eng.st.by_kind.get("wall", ())) != n:
        v.append("변경 자체가 끝나지 않았다")
    aff = r["affected"]
    if aff["counts"] != {"created": n, "modified": 1, "deleted": 0} or aff["truncated"] is not True or not 0 < len(aff["created"]) < n:
        v.append(f"affected 의 앞부분·정확한 건수·truncated 가 틀렸다: {aff['counts']} truncated={aff['truncated']} 앞부분 {len(aff['created'])}")
    if not r["page"] or r["page"]["total"] != n or r["page"]["truncated"] is not True or len(r["result"]) >= n:
        v.append(f"배치 results 잘림 표시가 틀렸다: {r['page']}")
    if not any("history.affected" in w for w in r["warnings"]):
        v.append("잘렸는데 이어받는 방법을 알리는 경고가 없다")
    if aff["created"] != ids[:len(aff["created"])] and aff["created"] != sorted(ids)[:len(aff["created"])]:
        v.append("affected 앞부분이 정렬된 id 의 앞부분이 아니다")
    # 이어받기
    rev, cursor, got = r["rev"], None, []
    while True:
        q = s.run("history.affected", dict({"rev": rev, "limit": 1000}, **({"cursor": cursor} if cursor else {})))
        if not q["ok"]:
            v.append(f"history.affected 실패: {q['error']}")
            break
        if len(_json.dumps(q, ensure_ascii=False).encode("utf-8")) > limits.RESPONSE_CAP_BYTES:
            v.append("history.affected 응답이 상한을 넘는다")
        got += [(i["change"], i["id"]) for i in q["result"]["items"]]
        cursor = q["page"]["next_cursor"]
        if cursor is None:
            break
    if sorted(got) != sorted([("created", i) for i in ids] + [("modified", F.LV1)]):
        v.append(f"history.affected 이어받기가 생성 id 전체와 다르다(받은 {len(got)}, 중복 {len(got) - len(set(got))})")
    # dry_run 도 상한 안
    r2 = s.batch([{"command": "wall.move", "args": {"id": i, "dx": 1}} for i in ids], dry_run=True)
    if not r2["ok"] or len(_json.dumps(r2, ensure_ascii=False).encode("utf-8")) > limits.RESPONSE_CAP_BYTES or r2["affected"]["counts"]["modified"] != n:
        v.append("dry_run 큰 배치 응답이 상한 안에 들지 않거나 건수가 틀렸다")
    # undo / redo 동작은 같다: 해시 복원, ops 는 잘려도 ops_total 이 정확하다
    h_after = {k: x for k, x in eng.hashes().items() if k != "rev"}
    u = s.run("history.undo")
    if (not u["ok"] or u["result"]["ops_total"] != n or u["result"]["ops"] != [{"op": "wall.create", "count": n}]
            or len(_json.dumps(u, ensure_ascii=False).encode("utf-8")) > limits.RESPONSE_CAP_BYTES):
        v.append(f"큰 배치의 undo 응답이 틀렸다: {u.get('error') or {k: x for k, x in u['result'].items() if k != 'ops'}}")
    if eng.st.by_kind.get("wall"):
        v.append("undo 가 600개 벽을 되돌리지 못했다")
    rr = s.run("history.redo")
    if not rr["ok"] or {k: x for k, x in eng.hashes().items() if k != "rev"} != h_after:
        v.append("redo 뒤 해시가 큰 배치 직후와 다르다")
    # 수정 600건: counts 정확
    r3 = s.batch([{"command": "wall.move", "args": {"id": i, "dx": 1}} for i in ids])
    if not r3["ok"] or r3["affected"]["counts"]["modified"] != n or r3["affected"]["truncated"] is not True:
        v.append(f"수정 600건 응답의 counts/truncated 가 틀렸다: {r3['affected']['counts']}")
    # 작은 변경은 모양이 같고 truncated 가 false
    r4 = s.run("wall.move", {"id": ids[0], "dx": 1})
    if r4["affected"]["truncated"] is not False or r4["affected"]["modified"] != [ids[0]] or r4["affected"]["counts"]["modified"] != 1:
        v.append(f"작은 변경의 affected 모양이 틀렸다: {r4['affected']}")
    d = kit.full_check(s, work, tag="-mcap")
    if not d["ok"]:
        v.append(f"큰 배치 뒤 차등 검사 실패: {d['diffs'][:2]}")
    s.close()
    return v


def check_token_budget(work: Path) -> list[str]:
    """D: 실측 토크나이저(tiktoken cl100k·o200k, 대용 측정)로 text + structuredContent 합계가 8,000 이하. tiktoken 이 없으면 시험하지 않는다."""
    try:
        import tiktoken  # noqa: F401
    except ImportError:
        return []
    import asyncio
    import size_check
    work = Path(tempfile.mkdtemp(dir=Path(work), prefix="tok-"))
    encs = {n: __import__("tiktoken").get_encoding(n) for n in size_check.ENCODINGS}
    v = []
    for korean in (False, True):
        m = size_check.build(work, korean)
        c = size_check.InProc(work)
        rows = asyncio.run(size_check.measure(c, encs, m, "한글" if korean else "영문"))
        asyncio.run(c.close())
        for r in rows:
            t = max(r["worst"]["tokens"].values())
            if t > size_check.TARGET_TOKENS:
                v.append(f"{r['lang']} {r['what']}: 최악 {t}토큰 > {size_check.TARGET_TOKENS} {r['worst']['tokens']}")
            if r.get("history_affected_matches_counts") is False:
                v.append(f"{r['what']}: history.affected 이어받기가 counts 와 다르다")
    return v


if __name__ == "__main__":
    import sys
    import tempfile
    w = Path(tempfile.mkdtemp())
    res = check_golden(w) + check_failure_atomicity(w) + check_delete_and_names(w) + check_restore_failure(w) + check_envelope_and_nulls(w) + check_remove_identity_order(w) + check_response_cap(w) + check_file_lock(w) + check_keyset_paging(w) + check_cursor_and_batch_input(w) + check_mutation_cap(w) + check_token_budget(w)
    print("\n".join(res) or "골든 값 통과")
    sys.exit(1 if res else 0)
