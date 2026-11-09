# SPDX-License-Identifier: MPL-2.0
"""S8 병합 시나리오. 공통 조상 = fixture.SCRIPT (레벨 2, 벽 3, 문 2, 조인 W1-W2, D1 FireRating, W1 재료).

한 시나리오 = 조상에서 갈라진 두 가지(A=alice, B=bob)가 각자 커맨드를 실행한 것. 두 가지 모두 단독으로는 성공한 커맨드만 쓴다
(합쳐서야 깨지는 것이 의미 충돌이다).

expect.clean 이면 충돌 없이 합쳐져야 하고, expect.conflicts(유형 목록, 정렬)이면 정확히 그 충돌이 보고돼야 한다.
resolve 는 충돌 해결 지정(match -> A/B 의 값을 택함). facts(eng, 변형) 은 합친 뒤 도메인 사실을 돌려준다(참이면 []).
"""
from __future__ import annotations

from s8lib import I  # noqa: E402  (경로 설정을 먼저 한다)
from dobim_core import fixture as F  # noqa: E402

W4, W5, W6 = I("1", 104), I("1", 105), I("1", 106)
D3, D4, D5 = I("2", 103), I("2", 104), I("2", 105)
L3, L4 = I("3", 103), I("3", 104)


def _bad(*checks):
    """(조건, 실패 설명) 쌍들에서 실패한 설명을 모은다."""
    return [m for ok, m in checks if not ok]


def _p(eng, e):
    return eng.st.el[e]["params"] if e in eng.st.el else None


def _ps(eng, e, ps, pr):
    return eng.ifc.get_psets(e).get(ps, {}).get(pr) if e in eng.ifc.ents else None


SCENARIOS: list[dict] = []


def sc(**kw):
    SCENARIOS.append(kw)
    return kw


# 1. 무충돌 독립 편집 (배치 포함)
sc(id="s01_independent", title="무충돌 독립 편집: 서로 다른 요소·종류를 건드리고 한쪽은 원자 배치",
   a=[("level.create", {"id": L3, "name": "3F", "elevation": 6600}),
      ("@batch", [("wall.create", {"id": W4, "level": F.LV1, "axis": [0, 8000, 6000, 8000]}),
                  ("door.place", {"id": D3, "host": W4, "offset": 1000, "width": 900})]),
      ("pset.set", {"id": D3, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI60"})],
   b=[("wall.set", {"id": F.W2, "name": "동측 벽"}), ("door.set", {"id": F.D2, "width": 1100}),
      ("material.set", {"id": F.W3, "name": "Brick"})],
   expect={"clean": True},
   facts=lambda e, v: _bad((L3 in e.st.el, "레벨 3F"), (W4 in e.st.el and D3 in e.st.el, "A 의 벽·문"),
                           (_p(e, F.W2)["name"] == "동측 벽", "B 의 이름"), (_p(e, F.D2)["width"] == 1100.0, "B 의 문 폭"),
                           (e.ifc.get_material(F.W3) == "Brick", "B 의 재료"), (_ps(e, D3, "Pset_DoorCommon", "FireRating") == "EI60", "A 의 속성")))

# 2. 같은 벽의 다른 필드
sc(id="s02_same_wall_different_fields", title="같은 벽 W1 의 다른 필드(A=두께, B=높이·이름)와 같은 요소의 다른 속성",
   a=[("wall.set", {"id": F.W1, "thickness": 250}), ("pset.set", {"id": F.W1, "pset": "Pset_WallCommon", "prop": "FireRating", "value": "EI90"})],
   b=[("wall.set", {"id": F.W1, "height": 3300}), ("wall.set", {"id": F.W1, "name": "남측 벽"}),
      ("pset.set", {"id": F.W1, "pset": "Pset_WallCommon", "prop": "IsExternal", "value": True})],
   expect={"clean": True},
   facts=lambda e, v: _bad((_p(e, F.W1)["thickness"] == 250.0, "A 두께"), (_p(e, F.W1)["height"] == 3300.0, "B 높이"),
                           (_p(e, F.W1)["name"] == "남측 벽", "B 이름"),
                           (_ps(e, F.W1, "Pset_WallCommon", "FireRating") == "EI90", "A 속성"),
                           (_ps(e, F.W1, "Pset_WallCommon", "IsExternal") is True, "B 속성")))

# 3. 같은 필드 충돌
sc(id="s03_same_field", title="같은 벽의 같은 필드(두께) 충돌 + 같은 벽의 다른 필드는 자동 병합",
   a=[("wall.set", {"id": F.W1, "thickness": 250}), ("wall.set", {"id": F.W1, "height": 3200})],
   b=[("wall.set", {"id": F.W1, "thickness": 300}), ("wall.set", {"id": F.W1, "name": "N"})],
   expect={"conflicts": ["SAME_FIELD"]},
   resolve=[{"match": {"type": "SAME_FIELD", "elements": [F.W1]}, "take": "B"}],
   facts=lambda e, v: _bad((_p(e, F.W1)["thickness"] == (300.0 if v == "spec" else 250.0), "해결된 두께"),
                           (_p(e, F.W1)["height"] == 3200.0, "A 높이 자동 병합"), (_p(e, F.W1)["name"] == "N", "B 이름 자동 병합")))

# 4. 삭제 대 수정
sc(id="s04_delete_vs_modify", title="삭제 대 수정: A 는 문 D2 를 지우고 B 는 같은 문의 폭과 속성을 바꾼다",
   a=[("element.delete", {"id": F.D2})],
   b=[("door.set", {"id": F.D2, "width": 1100}), ("pset.set", {"id": F.D2, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI30"})],
   expect={"conflicts": ["DELETE_MODIFY"]},
   resolve=[{"match": {"type": "DELETE_MODIFY", "elements": [F.D2]}, "take": "A"}],
   facts=lambda e, v: _bad(((F.D2 not in e.st.el) == (v == "spec"), "삭제 여부"),
                           (v == "spec" or (_p(e, F.D2)["width"] == 1100.0 and _ps(e, F.D2, "Pset_DoorCommon", "FireRating") == "EI30"), "B 수정 보존"),
                           (v == "spec" or e.st.inn.get(("hosts", F.D2)) == [F.W3], "호스팅 관계")))

# 5. 호스트 삭제 대 문 배치
sc(id="s05_host_deleted_vs_door_placed", title="호스트 벽 삭제(A) + 같은 벽에 새 문 배치(B): 개별 커맨드는 성공하지만 합치면 문이 갈 곳이 없다",
   a=[("element.delete", {"id": F.W3})],
   b=[("door.place", {"id": D3, "host": F.W3, "offset": 1000, "width": 900})],
   expect={"conflicts": ["HOST_DELETED"]},
   resolve=[{"match": {"type": "HOST_DELETED", "elements": [D3]}, "take": "A"}],
   facts=lambda e, v: _bad(((F.W3 not in e.st.el) == (v == "spec"), "호스트 벽 삭제 여부"), ((D3 not in e.st.el) == (v == "spec"), "새 문 존재 여부"),
                           (v == "spec" or F.D2 in e.st.el, "연쇄로 지워졌던 기존 문 D2 도 되살아남"),
                           (F.D2 not in e.st.el or v != "spec", "A 가 지운 D2 는 그대로 삭제")))

# 6. 호스트(연쇄) 삭제 대 호스팅된 문의 속성 수정
sc(id="s06_cascade_delete_vs_modify", title="벽 삭제의 연쇄로 지워진 문을 상대가 수정: 삭제 대 수정 + 호스트 삭제의 2단 충돌",
   a=[("element.delete", {"id": F.W3})],
   b=[("pset.set", {"id": F.D2, "pset": "Pset_DoorCommon", "prop": "SecurityRating", "value": 3})],
   expect={"conflicts": ["DELETE_MODIFY"]},
   resolve=[{"match": {"type": "DELETE_MODIFY", "elements": [F.D2]}, "take": "A"}, {"match": {"type": "HOST_DELETED", "elements": [F.D2]}, "take": "A"}],
   facts=lambda e, v: _bad(((F.W3 not in e.st.el) == (v == "spec"), "벽 삭제"), ((F.D2 not in e.st.el) == (v == "spec"), "문 삭제"),
                           (v == "spec" or _ps(e, F.D2, "Pset_DoorCommon", "SecurityRating") in (3, "3"), "B 속성")))

# 7. 조인 대상 삭제
sc(id="s07_join_target_deleted", title="조인 충돌: A 가 벽 W2 를 지우고, B 는 W2-W3 을 조인",
   a=[("element.delete", {"id": F.W2})],
   b=[("join.set", {"a": F.W2, "b": F.W3})],
   expect={"conflicts": ["JOIN_TARGET_DELETED"]},
   resolve=[{"match": {"type": "JOIN_TARGET_DELETED", "elements": [F.W2]}, "take": "A"}],
   facts=lambda e, v: _bad(((F.W2 not in e.st.el) == (v == "spec"), "W2 삭제"),
                           ((F.W3 in e.st.out.get(("joins", F.W2), [])) == (v != "spec"), "W2-W3 조인")))

# 8. 의미 충돌: 벽을 줄이고(A) 문을 끝 쪽으로 옮김(B)
sc(id="s08_wall_shortened_vs_door_moved", title="의미 충돌: A 가 벽 W1 을 3000 으로 줄이고 B 가 문 D1 을 2800 으로 옮김(각각은 유효)",
   a=[("wall.set", {"id": F.W1, "axis": [0, 0, 3000, 0]})],
   b=[("door.set", {"id": F.D1, "offset": 2800})],
   expect={"conflicts": ["DOOR_OUT_OF_HOST", "DOOR_OUTSIDE_TRIMMED"]},
   resolve=[{"match": {"type": "DOOR_OUT_OF_HOST"}, "take": "A"}],
   facts=lambda e, v: _bad((_p(e, F.W1)["axis"] == [0.0, 0.0, 3000.0, 0.0], "A 의 벽 단축 유지") if v == "spec" else (_p(e, F.W1)["axis"] == [0.0, 0.0, 6000.0, 0.0], "B 선택: 벽 단축 취소"),
                           (_p(e, F.D1)["offset"] == (1500.0 if v == "spec" else 2800.0), "문 위치")))

# 9. 의미 충돌: 같은 자리 벽 중복
sc(id="s09_duplicate_wall", title="의미 충돌: 두 가지가 같은 레벨의 같은 자리에 서로 다른 id 의 벽을 만든다",
   a=[("wall.create", {"id": W4, "level": F.LV1, "axis": [0, 9000, 6000, 9000], "thickness": 200})],
   b=[("wall.create", {"id": W5, "level": F.LV1, "axis": [6000, 9000, 0, 9000], "thickness": 300})],
   expect={"conflicts": ["DUPLICATE_WALL"]},
   resolve=[{"match": {"type": "DUPLICATE_WALL"}, "take": "A"}],
   facts=lambda e, v: _bad(((W4 in e.st.el) == (v == "spec"), "A 벽"), ((W5 in e.st.el) == (v != "spec"), "B 벽")))

# 10. 의미 충돌: 같은 벽의 문이 겹침
sc(id="s10_door_overlap", title="의미 충돌: 두 가지가 같은 벽의 거의 같은 자리에 문을 놓는다",
   a=[("door.place", {"id": D3, "host": F.W1, "offset": 4000, "width": 900})],
   b=[("door.place", {"id": D4, "host": F.W1, "offset": 4300, "width": 900})],
   expect={"conflicts": ["DOOR_OVERLAP"]},
   resolve=[{"match": {"type": "DOOR_OVERLAP"}, "take": "B"}],
   facts=lambda e, v: _bad(((D3 in e.st.el) == (v == "swap"), "A 문"), ((D4 in e.st.el) == (v == "spec"), "B 문")))

# 11. 의미 충돌: 레벨 이름 중복
sc(id="s11_level_name_duplicate", title="의미 충돌: 두 가지가 같은 이름(3F)의 레벨을 각각 만든다",
   a=[("level.create", {"id": L3, "name": "3F", "elevation": 6600})],
   b=[("level.create", {"id": L4, "name": "3F", "elevation": 6700})],
   expect={"conflicts": ["LEVEL_NAME_DUPLICATE"]},
   resolve=[{"match": {"type": "LEVEL_NAME_DUPLICATE"}, "take": "A"}],
   facts=lambda e, v: _bad(((L3 in e.st.el) == (v == "spec"), "A 레벨"), ((L4 in e.st.el) == (v == "swap"), "B 레벨")))

# 12. 재생성(파생 질의)으로만 드러나는 의미 충돌
sc(id="s12_regen_door_outside_trimmed", title="재생성 검사: A 가 벽 W2 를 옮겨 W1 의 조인 정리가 달라지고, B 는 문 D1 을 W1 시작 쪽으로 옮김",
   a=[("wall.move", {"id": F.W2, "dx": -5000})],
   b=[("door.set", {"id": F.D1, "offset": 500, "width": 600})],
   expect={"conflicts": ["DOOR_OUTSIDE_TRIMMED"]},
   resolve=[{"match": {"type": "DOOR_OUTSIDE_TRIMMED"}, "take": "A"}],
   facts=lambda e, v: _bad((_p(e, F.D1)["offset"] == (1500.0 if v == "spec" else 500.0), "문 위치"),
                           (_p(e, F.W2)["axis"][0] == (1000.0 if v == "spec" else 6000.0), "벽 W2 위치")))

# 13. 한쪽에 실행 취소 (순변화 없음)
sc(id="s13_undo_in_branch", title="한쪽 undo: A 가 두께를 바꿨다 되돌리고(순변화 없음), B 가 같은 필드를 바꿔도 충돌이 아니다. B 는 다시하기도 쓴다",
   a=[("wall.set", {"id": F.W1, "thickness": 250}), ("@undo",), ("wall.move", {"id": F.W2, "dx": 100})],
   b=[("wall.set", {"id": F.W1, "thickness": 300}), ("wall.set", {"id": F.W3, "height": 2800}), ("@undo",), ("@redo",)],
   expect={"clean": True},
   facts=lambda e, v: _bad((_p(e, F.W1)["thickness"] == 300.0, "B 두께"), (_p(e, F.W2)["axis"][0] == 6100.0, "A 이동"), (_p(e, F.W3)["height"] == 2800.0, "B 높이")))

# 14. 조상 쪽 트랜잭션을 한쪽이 되돌림 (재료) + 반대쪽이 바꿈
sc(id="s14_undo_of_ancestor_txn", title="조상의 마지막 트랜잭션(W1 재료 지정)을 A 가 undo, B 는 다른 재료로 바꿈: 재료 충돌",
   a=[("@undo",), ("level.create", {"id": L3, "name": "3F", "elevation": 6600})],
   b=[("material.set", {"id": F.W1, "name": "Steel"})],
   expect={"conflicts": ["MATERIAL_SAME"]},
   resolve=[{"match": {"type": "MATERIAL_SAME"}, "take": "B"}],
   facts=lambda e, v: _bad((e.ifc.get_material(F.W1) == ("Steel" if v == "spec" else None), "재료"), (L3 in e.st.el, "A 레벨")))

# 15. 속성셋 동시 편집 (다른 속성은 자동 병합, 같은 속성은 충돌)
sc(id="s15_pset_concurrent", title="속성셋 동시 편집: 다른 속성은 자동 병합, D1 FireRating 은 충돌, 한쪽이 지운 속성은 지운 것으로",
   a=[("pset.set", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI60"}),
      ("pset.set", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "SecurityRating", "value": 2}),
      ("pset.set", {"id": F.W2, "pset": "Pset_WallCommon", "prop": "FireRating", "value": "EI30"})],
   b=[("pset.set", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI90"}),
      ("pset.set", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "IsExternal", "value": True}),
      ("pset.set", {"id": F.W2, "pset": "Pset_WallCommon", "prop": "FireRating", "value": "EI30"})],
   expect={"conflicts": ["PSET_SAME_PROP"]},
   resolve=[{"match": {"type": "PSET_SAME_PROP", "elements": [F.D1]}, "take": "B"}],
   facts=lambda e, v: _bad((_ps(e, F.D1, "Pset_DoorCommon", "FireRating") == ("EI90" if v == "spec" else "EI60"), "충돌 속성"),
                           (_ps(e, F.D1, "Pset_DoorCommon", "SecurityRating") in (2, "2"), "A 속성"), (_ps(e, F.D1, "Pset_DoorCommon", "IsExternal") is True, "B 속성"),
                           (_ps(e, F.W2, "Pset_WallCommon", "FireRating") == "EI30", "같은 값으로 바꾼 속성")))

# 16. 똑같은 변경을 양쪽이 (멱등)
sc(id="s16_identical_changes", title="양쪽이 같은 변경(같은 값, 같은 id 의 같은 레벨 생성, 같은 문 삭제)을 했다: 충돌이 아니다",
   a=[("wall.set", {"id": F.W1, "thickness": 250}), ("level.create", {"id": L3, "name": "3F", "elevation": 6600}), ("element.delete", {"id": F.D2})],
   b=[("wall.set", {"id": F.W1, "thickness": 250}), ("level.create", {"id": L3, "name": "3F", "elevation": 6600}), ("element.delete", {"id": F.D2}),
      ("wall.set", {"id": F.W3, "thickness": 150})],
   expect={"clean": True},
   facts=lambda e, v: _bad((_p(e, F.W1)["thickness"] == 250.0, "두께"), (L3 in e.st.el, "레벨"), (F.D2 not in e.st.el, "삭제"), (_p(e, F.W3)["thickness"] == 150.0, "B 두께")))

# 17. 자동 id 충돌 (가지별 id 생성이 구분되지 않음)
sc(id="s17_auto_id_collision", title="id 충돌: id 를 주지 않고 각자 벽을 만들면 같은 리비전에서 같은 id 가 나온다(내용은 다름)",
   a=[("wall.create", {"level": F.LV1, "axis": [0, 7000, 2000, 7000]})],
   b=[("wall.create", {"level": F.LV1, "axis": [0, 7500, 3000, 7500]})],
   expect={"conflicts": ["ID_COLLISION"]},
   resolve=[{"match": {"type": "ID_COLLISION"}, "take": "B"}],
   facts=lambda e, v: _bad((len([w for w in e.st.by_kind["wall"] if w not in (F.W1, F.W2, F.W3)]) == 1, "새 벽 하나만 남음"),
                           (any(_p(e, w)["axis"] == ([0.0, 7500.0, 3000.0, 7500.0] if v == "spec" else [0.0, 7000.0, 2000.0, 7000.0])
                                for w in e.st.by_kind["wall"] if w not in (F.W1, F.W2, F.W3)), "선택한 쪽의 벽")))

# 18. 빠른 감기 (한쪽이 변경 없음)
sc(id="s18_fast_forward", title="한쪽(A)이 변경이 없으면 B 의 변경이 그대로 들어온다(반대 방향은 변경 없음)",
   a=[], b=[("wall.set", {"id": F.W1, "thickness": 260}), ("door.place", {"id": D3, "host": F.W2, "offset": 2000, "width": 900})],
   expect={"clean": True},
   facts=lambda e, v: _bad((_p(e, F.W1)["thickness"] == 260.0, "B 두께"), (D3 in e.st.el, "B 문")))

# 19. 삭제 대 삭제 + 한쪽이 지운 요소를 다른 쪽이 건드리지 않음
sc(id="s19_delete_untouched", title="한쪽이 지운 요소를 반대쪽이 건드리지 않았으면 조용히 삭제가 이긴다(조상 대비 변경 없음)",
   a=[("element.delete", {"id": F.W3})], b=[("wall.set", {"id": F.W1, "thickness": 260}), ("level.create", {"id": L3, "name": "3F", "elevation": 6600})],
   expect={"clean": True},
   facts=lambda e, v: _bad((F.W3 not in e.st.el and F.D2 not in e.st.el, "A 삭제와 연쇄"), (_p(e, F.W1)["thickness"] == 260.0, "B 두께")))

# 20. 여러 충돌이 한꺼번에 (부분 해결은 거부)
sc(id="s20_multiple_conflicts", title="충돌 여러 건: 같은 필드 + 삭제 대 수정 + 속성. 일부만 해결하면 거부된다",
   a=[("wall.set", {"id": F.W1, "thickness": 250}), ("element.delete", {"id": F.D2}),
      ("pset.set", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI60"})],
   b=[("wall.set", {"id": F.W1, "thickness": 300}), ("door.set", {"id": F.D2, "sill": 50}),
      ("pset.set", {"id": F.D1, "pset": "Pset_DoorCommon", "prop": "FireRating", "value": "EI90"})],
   expect={"conflicts": ["DELETE_MODIFY", "PSET_SAME_PROP", "SAME_FIELD"]},
   resolve=[{"match": {"type": "SAME_FIELD"}, "take": "A"}, {"match": {"type": "DELETE_MODIFY"}, "take": "B"},
            {"match": {"type": "PSET_SAME_PROP"}, "take": "A"}],
   facts=lambda e, v: _bad((_p(e, F.W1)["thickness"] == (250.0 if v == "spec" else 300.0), "두께"),
                           ((F.D2 in e.st.el) == (v == "spec"), "D2 존재"),
                           (_ps(e, F.D1, "Pset_DoorCommon", "FireRating") == ("EI60" if v == "spec" else "EI90"), "속성")))

# 21. 부모가 이미 가진 위반은 병합 탓이 아니다
sc(id="s21_parent_violation_not_merge_fault", title="한쪽(A)이 스스로 같은 자리 벽 중복을 만들었다: 병합이 새로 만든 위반이 아니므로 충돌로 보고하지 않는다",
   a=[("wall.create", {"id": W4, "level": F.LV1, "axis": [0, 9000, 6000, 9000]}), ("wall.create", {"id": W5, "level": F.LV1, "axis": [0, 9000, 6000, 9000]})],
   b=[("wall.set", {"id": F.W2, "name": "동측"})],
   expect={"clean": True},
   facts=lambda e, v: _bad((W4 in e.st.el and W5 in e.st.el, "A 의 두 벽 그대로"), (_p(e, F.W2)["name"] == "동측", "B 이름")))

# 22. 의미 충돌 12건: 해결 지시가 8건을 넘어도 마지막 재검사가 남아 위반이 새지 않는다(반복 상한 결함 회귀)
_MW = [I("1", 400 + i) for i in range(12)]
_MD = [I("2", 400 + i) for i in range(12)]
sc(id="s22_many_semantic_conflicts", title="의미 충돌 12건: 벽 12개를 A 가 줄이고 B 가 각 벽의 문을 끝으로 옮김. 해결 지시가 8건을 넘어도 위반이 남지 않는다",
   base=[x for i, w in enumerate(_MW) for x in (("wall.create", {"id": w, "level": F.LV1, "axis": [0, 20000 + i * 1000, 6000, 20000 + i * 1000]}),
                                                 ("door.place", {"id": _MD[i], "host": w, "offset": 1500, "width": 900}))],
   a=[("@batch", [("wall.set", {"id": w, "axis": [0, 20000 + i * 1000, 3000, 20000 + i * 1000]}) for i, w in enumerate(_MW)])],
   b=[("@batch", [("door.set", {"id": d, "offset": 2800}) for d in _MD])],
   expect={"conflicts": ["DOOR_OUT_OF_HOST"] * 12 + ["DOOR_OUTSIDE_TRIMMED"] * 12},
   resolve=[{"match": {"type": "DOOR_OUT_OF_HOST"}, "take": "A"}, {"match": {"type": "DOOR_OUTSIDE_TRIMMED"}, "take": "A"}],
   facts=lambda e, v: _bad(*[((_p(e, d)["offset"] == (1500.0 if v == "spec" else 2800.0)) and
                              (_p(e, w)["axis"] == ([0.0, 20000.0 + i * 1000, 3000.0, 20000.0 + i * 1000] if v == "spec" else [0.0, 20000.0 + i * 1000, 6000.0, 20000.0 + i * 1000]))
                              , f"문/벽 {i}") for i, (w, d) in enumerate(zip(_MW, _MD))]))

# 23. 병합 뒤 자동 id: 상대가 id 없이 만든 벽 3개와 우리 다음 자동 id 가 겹치지 않는다(사후 검사 next_auto_ids_ok 가 본다)
sc(id="s23_auto_ids_after_merge", title="상대가 id 를 주지 않고 벽 3개를 만들고 우리는 다른 곳을 고쳤다: 합친 뒤 우리 쪽 자동 id 생성이 상대 id 와 겹치지 않는다",
   a=[("wall.set", {"id": F.W2, "name": "A-only"})],
   b=[("wall.create", {"level": F.LV1, "axis": [0, 7000 + i * 500, 2000, 7000 + i * 500]}) for i in range(3)],
   expect={"clean": True},
   facts=lambda e, v: _bad((len([w for w in e.st.by_kind["wall"] if w not in (F.W1, F.W2, F.W3)]) == 3, "상대의 벽 3개"),
                           (_p(e, F.W2)["name"] == "A-only", "A 이름")))

BY_ID = {s["id"]: s for s in SCENARIOS}
assert len(BY_ID) == len(SCENARIOS)
