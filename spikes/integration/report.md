# 통합 보고서: 스파이크 코어 병합과 선행 리팩터링 (spike/integration)

질문(docs/integration-g1.md): S2·S3·S4·S5·S8의 코어 변경을 한 브랜치로 합치고, git이 잡지 못하는 의미 충돌 6건을 고쳐 G1에 넘길 수 있는가?

**결론: 합쳤고, 의미 충돌 6건을 시험으로 고정한 뒤 모두 고쳤다.** 재측정 수치는 이전 스파이크 보고와 같다(5절). 문서에 없던 상호작용 6건도 더 찾아 고쳤다(4절).

## 1. 무엇을 합쳤나

| 순서 | 브랜치 | 방법 | 텍스트 충돌 |
|---|---|---|---|
| 기반 | spike/s5-annotations (s3 포함) | `git worktree add -b spike/integration` | — |
| 1 | spike/s8-merge | `merge --no-ff` | `parity.py` 1(오류 사례 목록: 양쪽 유지) |
| 2 | spike/s4-families | `merge --no-ff` | `commands.py` 3, `engine.py` 2, `errors.py` 1, `ifc_model.py` 1, `parity.py` 1 |
| 3 | spike/s2-roundtrip | `merge --no-ff` | `commands.py` 2, `engine.py` 2, `ifc_model.py` 6, `parity.py` 1 |

S7·S9·S10은 코어를 고치지 않아 범위 밖이다. 충돌 해결의 요점은 다음과 같다.

- **commands.py**: 삭제의 관계 목록에 S5 `annot_of`와 S4 `of_type`를 둘 다 넣었다. S2의 `attach`와 `complex_material`을 `_delete_one`에 유지했다. `model.validate`는 S3의 뷰 레벨 검사와 S4의 패밀리 평가 오류를 둘 다 센다.
- **engine.py**: 신원 만들기에 S4의 `ifc_class` 인자, S3의 "비 IFC 요소는 신원 없음", S2의 `prune`을 모두 넣었다.
- **ifc_model.py**: 상태 필드를 한 `__init__`으로 합쳤다. `remove_identity`는 S3의 개구부 잃은 문 목록, S4의 패밀리 투영 해제, S2의 `_remove_product`를 한 흐름으로 합쳤다. 의미 덤프는 S2의 `make_norm`을 정본으로 삼고 S4의 `IfcTypeObject` 참조 접기를 거기에 넣었다.
- **parity.py**: 각 스파이크의 준비 단계(`_V1`, `_A1`, `_FAMILY_PRE`)와 오류 사례, 입력 파일 준비를 모두 유지했다.

병합 직후 비-slow 시험 중 S5 한 건만 실패했다(`commands.search`가 6,000B 상한에서 한 쪽에 안 들어감). 쪽을 넘겨 모아 대조하도록 고쳤다.

## 2. 의미 충돌 6건: 원인·조치·시험

시험은 `spikes/s1-core/tests/test_integration_g1.py`에 있다. 수정 전 8개가 모두 실패했고, 지금은 14개 모두 통과한다.

| # | 짝 | 원인 | 수정 전 실패 | 조치 | 시험 |
|---|---|---|---|---|---|
| 1 | S8×S2 | S2는 가져온 상태를 `element` 테이블에만 풀어 두고(로그 밖), S8은 로그만 접는다 | `IO_ERROR`(ORPHAN_UNIT) | 시작 상태를 로그 seq 0 항목(`ifc.import`)으로도 기록. 가져온 IFC가 원래 갖던 속성·재료는 조상 시드로 넣음 | `test_1`, `test_1b`, `test_9` |
| 2 | S8×S3·S4 | `merge.py`가 level·wall·door만 앎, `of_type`·`att` 모름. 병합의 `put_el`이 `ELEMENT_FROZEN` 우회 | `IO_ERROR`(알 수 없는 IFC 클래스 view), 동결 위반 대신 `IO_ERROR` | `kinds.py`로 일원화, 쓰기 관문에서 저작 확인 | `test_2`, `test_2b`, `test_2c` |
| 3 | S3×S4 | 도면이 `hosts` 자식을 전부 `sill` 있는 문으로 가정 | `INTERNAL`(KeyError) | 문은 `offset±width/2`·`sill`, 패밀리 인스턴스는 평가된 `cut`·`elevation` | `test_3` |
| 4 | S2×S3 | 레벨 관계에 외래 요소가 섞여 KeyError, detached는 낡은 파라미터로 그려짐 | `INTERNAL`(KeyError) | 저작 벽만 그림. 외래·분리는 `omitted` 개수와 경고로 알림 | `test_4` |
| 5 | S2×S4 | `family.place`가 저작 확인을 안 거침. `make_norm` 변경으로 `projection_hash` 형식이 달라짐 | detached 벽에 배치 성공, 낡은 해시 그대로 | 쓰기 관문 + `CLOSURE_FORMAT` 번호와 열 때 재판정 | `test_5`, `test_5b`, `test_9b` |
| 6 | S2×S3 | `input_keys_of`는 `att`에 키를 만들고 `_apply_effect`는 안 넣음 | `pending=∅` vs `{("att",id)}` | 둘이 같은 표(`EFFECTS[태그].keys`)를 씀, `att`는 키 없음 | `test_6` |

## 3. 리팩터링

1. **레지스트리(`kinds.py`, ADR-0013)**: ifcopenshell을 import하지 않는다. 요소 9종, 관계 6종, 효과 5종을 정의한다. 엔진·IFC 모델·질의·병합·가져오기·커맨드가 여기서 파생한다.
   - `succ`만 끝 요소가 지워져도 남는 것으로 표시했다. 한 번 쓴 id는 다시 만들 수 없다는 S5 규칙 때문이다.
2. **시작 상태 로그 기록**:
   - seq 0 항목은 리비전에 세지 않고 되돌리기 대상이 아니다.
   - 서로 다른 가져오기에서 갈라진 파일의 병합은 `CONSTRAINT`로 거절한다.
   - 접기가 가져온 IFC 값을 조상 시드로 넣어, 한쪽의 삭제가 사라지거나 수정 대 삭제 충돌을 놓치는 일을 막는다. 시드를 끄면 `test_1b`가 실패하는 것을 확인했다.
3. **저작 확인 단일 관문**:
   - `Ctx._frozen_check` 하나를 `authored()`, `put_el`, 새 관계, `set_material`이 같이 쓴다.
   - 관계는 맺을 때만 검사한다. 저작 문을 지울 때 분리된 호스트와의 관계를 끊어야 하기 때문이다.
4. **해시 형식 번호**:
   - `CLOSURE_FORMAT`은 "1"이 S2, "2"가 통합이다.
   - 열 때 번호가 다르면 원본 바이트로 재판정해 `projection_hash`를 갱신한다.
   - 저작→분리는 동결하고 시작 상태 기록도 고친다. 분리→저작으로는 되살리지 않는다.
5. **병합 확장**:
   - 요소 9종, 관계 6종, `att` 효과를 다룬다.
   - 새 의미 규칙: `REL_ENDS`, `VIEW_DELETED`, `TYPE_DELETED`, `TYPE_MISSING`, `VIEW_MISSING`, `FINST_INVALID`, `FINST_OUT_OF_HOST`, `VIEW_LEVEL_DELETED`, 뷰 이름 중복.
   - 병합이 지우는 가져온 요소의 외래 부속을 `att`로 먼저 남긴다.
6. **패리티·린트**:
   - 패리티 도구가 `commands.search`를 쪽 단위로 모은다.
   - ifcopenshell 직접 사용 금지 계약에 `merge`, `kinds`, `limits`, 패밀리 순수 모듈을 넣었다.
   - 허용 목록은 engine, ifc_model, foreign(외래 IFC를 읽는 가져오기 계획), family.ifcproj, family.validate(IFC4 스키마의 클래스 이름 확인)다.
7. **도면**: `is_drawn_wall`, `view_omitted`, `hosted_opening`을 더했다.

## 4. 합치면서 더 찾은 상호작용

| 짝 | 증상 | 조치 | 시험 |
|---|---|---|---|
| S5×S2 | `wall.split`이 저작 확인을 안 거침 | `authored`로 변경 | `test_7` |
| S5×S4 | `wall.split`이 패밀리 인스턴스를 `offset±width/2`로 봄 | 평가한 `cut` 범위 사용 | `test_7` |
| S2×S5 | 가져온 프로젝트에서 분할 투영 뒤 undo하면 문의 개구부가 IFC에서 사라지고 해시 불일치 | 개구부를 잃은 문을 `base_ents`에서 뺌 | `test_7` |
| S5×S2 | 주석이 분리 벽을 대상으로 만들어져 낡은 형상을 쟀음 | 주석 대상도 저작 확인 | `test_5` |
| S8×S5 | 분할 이력 `succ`가 끝 요소가 지워진 채 남는 것을 "매달린 관계"로 오판할 수 있음 | `succ`는 `dangling_ok` | `test_8` |
| S8×S2 | 가져온 IFC 속성의 삭제가 병합에서 사라짐 | 조상 시드 | `test_1b` |

## 5. 검증과 재측정

Windows 11, Python 3.12, ifcopenshell 0.9.0.post1, 한 대에서 쟀다. 비교 도구는 `spikes/integration/compare.py`, 원자료는 `spikes/integration/results/`.

| 항목 | 이전 보고 | 통합본 |
|---|---|---|
| 코어 비-slow | 병합 전 측정 254 통과/38 건너뜀(docs/integration-g1.md 3절) | 345개 중 340 통과, 5 건너뜀(Revit 환경변수 없음), 실패 0. `test_protocol` 한 건이 동시 부하로 시간 초과(3.08s>3.0s)했고 단독 실행에서 통과 |
| slow | — | 코어 7/7, S8 22/22 |
| S4 spike / S8 spike(비-slow) | 120 / — | 120/120, 118/118 |
| 패리티 C | 100%(45개) | 100%(54개, 불일치 0, 음성 대조가 불일치를 잡음) |
| `commands.search` | 한 쪽 34개 | 2쪽(5,959B + 1,553B) |
| lint | 5계약 | 5계약 유지 |
| 응답 크기 최악 | 6,718토큰 | 6,718토큰(상한 8,000) |
| 변이 | S1 60/60, S3 11, S4 38, S5 15, S8 20 | 모두 같음(S1 골든 끈 상태 48, 이전 47) |
| S2 T1~T5 | 차이 0, 속성 1건 등 | 같음(Revit 전용 검사는 실행 안 됨) |
| S3 | 노드 574, 이동당 평균 157.6 | 594, 158.6(벽마다 `is_drawn_wall` 노드 1개 증가). SVG 파일마다 +40B는 S5부터 |
| S5 시나리오·퍼즈 | — | 차이 0 |
| S8 시나리오 | 23개 통과 | 23개 통과. 차이는 IFC 해시 76건뿐(S2의 개구부 GlobalId 규칙이 합쳐진 결과, state·derived 동일) |
| 픽스처 해시 | state `c85d…`, derived `47c2…` | 같음. ifc는 S2의 `6ea6…` |

시험 변경 기록(약화 아님):
- S5의 `commands.search` 시험은 "한 쪽에 전부 + 200B 여유"에서 "쪽을 넘겨 모아 레지스트리와 대조, 쪽마다 6,000B 이하"로 바꿨다. 커맨드가 54개로 늘어 한 쪽에 들어가지 않기 때문이다.
- S8 변이 M7은 `fold`의 새 인자 `seed`를 그대로 넘기게 고쳤다. 변이의 의도(이력 항목 무시)는 같고 실패 59건도 이전과 같다.

## 6. 확인하지 못한 것

- Revit 표본(비공개)은 환경변수가 없어 건너뛰었다(시험 5개).
- Claude 토크나이저로는 응답 크기를 재지 못했다(tiktoken 대용).
- 재측정 중 다른 프로세스가 동시에 돌아 시간 값은 부하의 영향을 받았다.

## 7. 한계

- 통합 전에 가져온 프로젝트에는 시작 상태 기록이 없다. 병합하려면 `ifc.import`로 다시 가져와야 하고, 병합이 `CONSTRAINT`로 알린다.
- 패밀리·뷰·주석은 가져오기에서 의도를 복원하지 않아 외래로 분류된다.
- 병합의 패밀리 규칙은 개구부가 호스트 벽 안에 들어가는지와 평가 오류만 본다.
- 분리를 저작으로 되돌리는 경로는 없다.
- 단일 환경에서만 쟀다.
- 의미 충돌은 코드를 읽고 재현한 것이며, 모든 짝을 체계적으로 탐색한 것은 아니다.

## 8. 독립 검수 결함과 조치

통합본을 코드와 시나리오 재현으로 따로 검수한 결과(조건부 통과)를 고쳤다. 회귀 시험은 `spikes/s1-core/tests/test_integration_review.py`(`test_r*`)와 `test_integration_g1.py::test_6`이다. 조치를 되돌려 시험이 실패하는지(이빨)는 항목마다 확인했다.

| # | 결함 | 원인 | 조치 | 시험 |
|---|---|---|---|---|
| 1 | 가져온(외래 기반) 프로젝트에서 치수·태그·주석 있는 뷰를 지우거나(`element.delete`, `view.delete`, `annot.delete`) 병합이 지우면 INTERNAL(KeyError) | `_delete_one`·`emit_plan`이 `Ctx.attach`·`complex_material`을 IFC 신원 없는 요소에도 불러 `ifc.ents[eid]`를 조회 | 두 함수가 `_in_ifc`(종류 레지스트리 `NON_IFC_KINDS`)로 신원 여부를 먼저 판단 | `test_r1`(7조합, 삭제 후 undo로 해시 복원), `test_r1b`(병합이 뷰·주석 삭제) |
| 1+ | (검수 중 발견) 가져온 프로젝트에서 패밀리 인스턴스를 지웠다 되돌리면 IFC에 같은 `IfcRelDefinesByType`가 하나 더 생김(IFC 해시 불일치) | 지우기 전 부속 스냅숏이 투영 소유 타입 관계를 외래 부속으로 떴다가 되돌릴 때 투영과 함께 중복 생성 | `capture_attachments`가 인스턴스의 투영 소유 타입 관계는 뜨지 않음 | `test_r1`의 `element.delete`/`finst` 조합 |
| 2 | 병합 `ID_COLLISION`의 `take=both`가 params 안의 id 참조(`view.level`, 치수·태그의 `view`·참조·`target`)를 옮기지 않아 `annot_of` 관계와 params가 다른 대상을 가리킴(`model.validate`도 못 잡음) | 재번호가 단위 키만 바꾸고 파라미터 값은 그대로 둠 | `kinds.ELEMENT_KINDS[종류].refs`(파라미터 이름, 형태 id/ref, 짝이 되는 관계)를 표에 두고 `merge.rename_units`가 이 표에서 파생해 값 안의 id와 `종류:id#역할` 문자열도 옮김. `model.validate`는 같은 표로 params와 관계(`annot_of`, `of_type`)가 어긋나면 오류로 보고 | `test_r2`(치수·태그/뷰), `test_r2b`(레벨/뷰), `test_r2c`(벽/참조 문자열), `test_r2d`(어긋남 검출), `test_r2e`(표가 실제 커맨드 결과와 일치) |
| 3 | 옛 형식 DB(seq 0·`closure_format` 없음)를 열 때 재판정이 저작→분리로 동결하면 `_rejudge_import`가 `op_log` seq=0을 `fetchone()[0]`으로 읽어 TypeError→INTERNAL | 시작 상태 기록이 없는 경우를 가정하지 않음 | 기록이 없으면 요소 테이블만 동결하고 없던 기록을 새로 만들지 않음 | `test_r3`, `test_r3b`(옛 형식 병합은 `CONSTRAINT`) |
| 3+ | 검수자의 짐작: 재판정 뒤 `_import_spec` 메모리 값이 낡아 첫 세션과 재오픈 세션이 같은 요소를 다르게 투영할 수 있다 | 사실이다. 열기 전에 읽은 명세 해시를 재판정이 DB에서 고친 뒤에도 갱신하지 않음(다른 프로세스가 먼저 재판정한 경우의 출처 변경도 같음) | `_reload_judgement`로 메모리의 출처·명세 해시를 DB와 맞춤 | `test_r3c`. 다만 우리 표본에서는 재투영이 멱등이라(스타일·수량 세트를 붙인 벽으로도) IFC 해시 차이는 관측하지 못했다. 메모리 값 불일치만 시험으로 고정 |
| 4 | 재판정으로 동결된 요소의 동결 전 편집을 `history.undo`하면 저작으로 되살아남 | 되돌리기가 효과를 `_apply_effect`로 직접 적용해 동결 검사를 거치지 않음 | `history.undo/redo`가 효과를 하나라도 적용하기 전에 `el`·`mat` 효과의 대상이 지금 동결돼 있으면 `ELEMENT_FROZEN`으로 거절(아무것도 남기지 않음). 거절하는 쪽을 골랐다: 되돌리기가 동결 요소의 의도 값을 조용히 바꾸지 않는다. 대가로 그 트랜잭션이 스택 맨 위인 동안은 더 아래로 되돌릴 수 없다 | `test_r4` |
| 5 | 같은 IFC를 두 사람이 따로 가져오면 시작 상태 기록 비교에 actor가 들어가 `CONSTRAINT`로 병합 거절 | 기록 전체(행위자 포함)를 비교 | 비교를 연산 이름과 인자(원본 바이트 sha256·크기·스키마)로 한정(`merge.genesis_key`). 프로젝트 식별자(`project_uuid`)가 다르면 별도 규칙으로 여전히 거절 | `test_r5`, `test_r5b`(다른 IFC는 여전히 거절) |
| 6a | `Ctx.rel`의 저작 확인(guard)을 꺼도 통합 시험이 모두 통과. 위조 로그(분리 벽에 문 호스팅)는 `merge.preview`가 충돌 0이라 하고 적용에서야 거절 | 커맨드의 앞단 확인이 관문을 가림. 미리보기는 적용의 쓰기 관문 규칙을 몰랐음 | `merge.frozen_hits`가 적용과 같은 규칙(동결 요소의 생성·수정·삭제, `REL_KINDS.guard` 관계 맺기, 재료)을 계획 단계에서 보고 `ELEMENT_FROZEN`으로 미리 알림 | `test_r6`, `test_r6b`(위조 로그), `test_r6c`(정상 병합은 안 걸림), `test_r6d`(관문을 직접 부름. `Ctx.rel`의 guard, `REL_KINDS.guard` 검사, 재료 확인을 각각 끄면 실패하는 것을 확인) |
| 6b | `test_6`이 `EFFECTS` 표를 바꿔도(`att` 키 복원) 통과 | 같은 표에서 나온 두 계산을 서로 비교만 함 | 표와 독립인 기대 키(버킷은 `crc32 % 64`를 시험에서 직접 계산)를 고정. `att`에 키를 넣으면 실패하는 것을 확인 | `test_integration_g1.py::test_6` |
| 7 | 패밀리 인스턴스가 있는 저작 프로젝트를 IFC로 내보냈다 다시 가져오면 호스트 벽이 분리(동결)됨 | 아래 한계 | 고치지 못했다. 현재 동작을 시험으로 고정 | `test_r7` |

항목 7의 정확한 원인: 내보낸 패밀리 인스턴스(`IfcDoor`·`IfcWindow`)의 `DoBIM_Intent`는 문·벽·레벨의 의도 모양이 아니라서 `foreign._parse_intent`가 거절하고, 인스턴스와 그 개구부(`IfcOpeningElement`)가 외래 요소가 된다. 호스트 벽의 투영 클로저에는 그 개구부(`voids`)가 들어 있는데, 가져오기가 벽의 의도만으로 다시 투영한 벽에는 개구부가 없어 클로저 해시가 달라지고, 사유 "벽에 뚫린 개구부"로 호스트 벽이 분리됨이 된다. 안전한 쪽의 판정이지만 저작 프로젝트가 왕복 뒤 호스트 벽의 형상을 고칠 수 없다(속성은 가능). 고치려면 가져오기가 패밀리 타입·인스턴스의 의도(레시피·값·호스트·오프셋)를 복원하고 그 개구부를 재투영 비교에 넣어야 한다. 이는 ADR-0013의 "패밀리·뷰·주석은 의도를 복원하지 않는다" 한계와 같은 작업이며 이번에 하지 않았다.

검수 중 확인한 것(결함 아님): 같은 IFC라도 `guid`를 따로 정하지 않고 가져오면 `project_uuid`가 달라 병합이 거절된다(의도된 규칙). 되돌리기 거절(항목 4)은 재판정이 판정을 뒤집는 드문 경로에서만 나타난다.

수치(같은 한 대, 다른 프로세스와 겹쳐 돈 시간은 참고용): 코어 비-slow 369개(이전 345 + 검수 회귀 24) 중 364 통과, 5 건너뜀(Revit 환경변수 없음), 실패 0. 패리티 C 100%(불일치 0, 음성 대조가 불일치를 잡음). `commands.search`는 쪽마다 6,000B 이하(시험 통과). import-linter 5계약 유지. 검수 재현 스크립트 전부 재실행: 결함 재현 항목(`sc2`·`sc2b`·`sc4`·`sc5`·`sc6`·`sc7`·`sc12`·`legacy_flip`)은 모두 해소됐고, 회귀 점검 항목(`sc1` 되돌리기 끝까지 후 재오픈 해시, `sc3`, `sc9`, `sc10`, `sc11`, 병합 퍼즈 가져온 기반·저작 기반 각 25시드: INTERNAL·IO_ERROR 0, 열어 다시 계산한 해시 일치, undo 일치)는 이전과 같다. `sc8`(항목 7)은 위의 한계대로 호스트 벽이 분리로 동결된다.

## 9. 한계에 더해진 것

- 패밀리 인스턴스가 있는 저작 프로젝트의 IFC 왕복은 호스트 벽을 분리(동결)로 만든다(8절 항목 7). 가져오기가 패밀리 타입·인스턴스의 의도를 복원해야 풀린다.
- 재판정이 판정을 뒤집어 요소를 동결하면 그 전의 편집은 `history.undo/redo`로 되돌릴 수 없다(`ELEMENT_FROZEN`). 그 트랜잭션이 스택 맨 위인 동안은 더 아래로 되돌릴 수 없고, 되돌리려면 원본 도구에서 고쳐 다시 가져와야 한다.
- 병합 미리보기의 동결 예고는 `ELEMENT_FROZEN` 오류로 끝난다(해결 가능한 충돌 목록이 아니다). 커맨드로는 만들 수 없는 로그에만 해당한다.
