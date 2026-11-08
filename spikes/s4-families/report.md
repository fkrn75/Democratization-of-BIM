# S4 보고서: 선언형 레시피로 패밀리를 표현하고 에이전트가 생성할 수 있는가

질문(docs/roadmap.md S4): 선언형 레시피로 패밀리를 표현하는가? 에이전트가 생성할 수 있는가?
합격 기준: 문, 창, 기둥, 붙박이장 4종의 공식·평면 기호·IFC 타입 투영이 동작. 사양 문서만으로 에이전트가 새 패밀리 3종 생성, 검증기 통과율 2/3 이상.

**결론: 합격.** 구속 해석기 없이 4종을 모두 표현했고(ADR-0008의 뒤집는 조건 "2종 이상 표현 불가"에 해당하지 않음), 사양 문서만 받은 별도 에이전트가 새 패밀리 3종을 첫 시도에 3/3 통과시켰다.

## 1. 합격 기준별 판정

| 기준 | 판정 | 근거 |
|---|---|---|
| 문·창·기둥·붙박이장 공식 | 통과 | 4종 모두 파생값 DAG, 제약, 표시 조건(when), 반복(repeat), 공극(void), 스윕/압출로 표현 |
| 평면 기호 | 통과 | 선·호·원·사각형·폴리라인, 축척별(default, 1:200), 열림 방향(L/R) 뒤집힘. IFC에는 Plan/FootPrint 문맥의 `IfcGeometricCurveSet` |
| IFC 타입 투영 | 통과 | `IfcDoorType`/`IfcWindowType`/`IfcColumnType`/`IfcFurnitureType` + `IfcRepresentationMap`(본체·평면) + `IfcRelDefinesByType` + 표준 Pset(타입/인스턴스 구분), 호스팅은 `IfcOpeningElement`. 4종 배치 후 express 포함 `ifcopenshell.validate` 오류 0 |
| 에이전트가 사양만으로 새 패밀리 3종 생성, 통과율 2/3 이상 | 통과(3/3) | 아래 2절 |

## 2. 에이전트 생성 시험

구현과 무관한 별도 에이전트에게 `SPEC.md`, 라이브러리 예시 4종, 검증 명령만 주고 구현·시험 코드 열람을 금지한 채 3종을 만들게 했다. 결과물은 `agent_out/`.

| 패밀리 | 첫 시도 | 비고 |
|---|---|---|
| 미닫이문(`door.sliding.json`) | 통과 | 문짝 2장을 서로 다른 레일에, 인스턴스 매개변수로 방향 전환, OperationType DOUBLE_DOOR_SLIDING |
| 고정창 + 하부 환기창(`window.fixed_vent.json`) | 통과 | 비율 매개변수(0.3~0.6), PartitioningType DOUBLE_PANEL_HORIZONTAL |
| 원형 기둥(`column.round.json`) | 통과 | 지름·높이, 선택적 주두 |

- 첫 시도 3/3 통과, 수정 라운드 불필요. 지휘자가 같은 명령으로 다시 돌려 3/3을 확인했다.
- 한계: 한 번도 실패하지 않아 검증기 오류 메시지·힌트가 수정에 도움이 되는지는 이 시험으로 판단할 수 없다. 표본은 에이전트 1회, 3종이다.
- 에이전트가 낸 SPEC 개선 제안: 미닫이(두 레일) 예시 추가, 검증기가 호스트 두께 100/300/600mm를 모두 시험한다는 점 명시, 비율 매개변수의 `unit: none` 명시, 의도적으로 틀린 레시피와 오류 메시지 모음 첨부, 예약 이름 목록과 `sweep`의 `rect` 방향 설명.

## 3. 측정 (`results/*.json`, 재현 `run_s4.py`)

- 라이브러리 4종: 전체 검증 4/4, 9개 단계 모두 pass, 인스턴스당 실제 메시 생성.
- 변이 검출: 문 레시피를 고장 낸 38종을 38/38 검출(기대 오류 코드까지 일치). 힌트가 붙은 오류 31/38(82%).
- 코어 통합: 4종 배치 후 `model.validate` 오류 0. 취소 6회 후 state/derived/ifc 해시가 처음과 같고 다시실행도 같다. 프로젝트 재오픈(로그 재생) 해시 같음, 증분 그래프 == 기준 그래프, `DoBIM_Intent` 불일치 0.
- 타입 공유: 같은 레시피·타입 값이면 같은 타입 id(내용 주소). 문 3개(타입 값 2종)는 `IfcDoorType` 2개.
- 개별 표현: 문 열림 방향만 다르면 본체는 타입 지도, 평면 기호만 개별 표현.
- 지연: 배치 요소당 5~12ms, 6개 배치 후 flush+해시 116ms.
- IFC 크기: 파일 68.9KB 중 레시피 사본(`DoBIM_Intent`)이 19.2KB(28%). IFC를 다시 열면 레시피가 복원된다. 라이브러리 30종이면 약 100~150KB 추가로 추정.

## 4. 시험과 계약

- S4 시험 120개 통과(식 언어, 변이, 라이브러리 전 단계, 코어 통합 16개, 독립 검수 회귀 30개).
- 기존 S1 시험(`-m "not slow"`): 검수 반영 후 293개 모두 통과(느린 시험 `-m slow` 는 생략).
- 패리티 C: 검수 반영 후 재측정 **100%**(레지스트리 37개, 커버리지 100%, 예시 42회·오류 사례 38회 불일치 0, `results/s4_parity.json`). 수정 전에는 98.8%였다(`family.validate` 응답의 실행 시간, `commands.search` 잘림). `family.set` 예시는 이제 성공(종료 코드 0)한다: 예시는 항목마다 기준 프로젝트 복사본에서 따로 실행되므로 `tools/parity.py` 의 `PRE` 에 `family.place` 예시를 준비 단계로 추가했다.
- 주의: `commands.search`는 37개가 한 쪽(5,897바이트, 상한 6,000, 여유 103바이트)에 겨우 들어간다. 커맨드를 더 추가하면 잘린다(잘리면 `page.truncated` 와 커서로 이어 받는다).

## 5. 한계·미확인

1. 변이 시험은 문 레시피 위주다(창·붙박이장 4건, 기둥은 라이브러리 통과만).
2. 표본 검사는 증명이 아니다. 3개 이상 매개변수가 동시에 극단일 때만 깨지는 제약은 놓칠 수 있다.
3. IFC로 못 담는 것: 평면 기호의 선 스타일과 default 외 축척은 `DoBIM_Intent`에만 남는다. 속성은 측정형 타입이 아니라 `IfcReal`/`IfcLabel`/`IfcBoolean`.
4. 공극이 있는 형상은 `IfcBooleanResult`를 `IfcCsgSolid`로 감싼 표현이다. 다른 IFC 뷰어 호환은 미확인.
5. 스윕은 직선 구간 압출의 합이다. 곡선 경로, revolve, 일반 불리언, 중첩 패밀리, 재료, MEP 커넥터는 없다. 호스트는 직선 벽뿐이다.
6. 코어 미비: 조인으로 줄어든 구간 밖 인스턴스 상태 표시 없음, 일람표·수량이 패밀리 인스턴스를 포함하지 않음. (`wall.set` 의 호스팅 적합성은 6절에서 고쳤다)
7. 레시피 전문을 타입마다 복사해 보관한다. 검증기의 시간 상한(static 10초/full 45초)은 협력적 검사라 OCC 불리언 한 번이 오래 걸리는 경우는 공극 수 상한(16)으로만 막는다. 상한을 넘는 정상 레시피는 거절될 수 있다(근거: 라이브러리 최대 공극 4, 식 노드 2천).
8. S1 코어 변경 지점: `ifc_model`(타입 관계·평면 문맥, `should_inherit=False`, 타입 객체를 의미 해시에 포함), `engine`, `queries`, `commands`(`history.list` 큰 인자 생략 등), `errors`(`FAMILY_INVALID`), import-linter 계약에 `commands_family` 추가. 기존 요소의 해시 값은 바뀌지 않는다.

## 6. 독립 검수 반영 (조건부 판정 → 결함 수정)

별도 검수자가 25개 재현 스크립트로 결함을 찾았고(합격 판정은 유지, 조건부), 아래를 고쳐 회귀 시험을 붙였다(`tests/test_independent_review.py` 30개, 재현 수치 `results/s4_review_fixes.json`; 문서 반영은 `SPEC.md` 4·6.3·11·12·12.1절).

| # | 결함 | 조치 |
|---|---|---|
| 1 | `wall.set` 두께·높이가 호스팅 인스턴스 제약을 깨도 ok 로 커밋 → flush 에서 `FamilyError`(INTERNAL), 재오픈 실패 | `wall.set` 이 호스팅된 `finst` 를 새 값으로 미리 재평가해 제약 위반·개구부 이탈(길이·높이, 비대칭 cut 포함)을 `CONSTRAINT`(`details.instance`, `details.issue`)로 거절한다. 이미 깨진 값이 로그에 있는 프로젝트도 열리게 `ifc_spec` 가 평가 실패를 오류 상태(형상 없음)로 바꾸고 `model.validate` 가 errors 로 보고한다. 벽 값을 고치면 복구. 호스트 형상을 바꾸는 커맨드는 `wall.set` 뿐이다 |
| 2 | `_RECIPES` 512개 포화 → `KeyError` 영구 INTERNAL | 평가 결과 캐시를 정확한 LRU 로 바꾸고 레시피 본문을 호출 인자로 받는다. `family.validate` 는 캐시를 쓰지 않는다 |
| 3 | 1.4KB 레시피(공극 50개) full 78초, 42KB 식 평가 1.7초×표본 120 | solid당 공극 16개, 평가당 식 노드 20만 개, 검증 전체 시간 예산(static 10초/full 45초) → 구조화 오류 `E_LIMIT`. 42KB 레시피는 첫 평가에서 거절 |
| 4 | 공극이 solid 를 전부 지우거나 빗나가도 통과(IfcOpenShell 이 빈 불리언을 원본으로 되돌림) | 경계 상자 검사 `E_VOID_MISS`(static 단계), 절단 전후 부피 비교 `E_SHAPE_VOLUME`(full) |
| 5 | NaN/Infinity, `max=10**400`, 깊이 5000 JSON 이 INTERNAL | `parse_constant` 거절, 반복문 검사(숫자 유한·±1e15·깊이 24·값 4만 개), `RecursionError` 처리 → `FAMILY_INVALID`/`E_JSON`/`E_LIMIT` |
| 6 | 같은 id 여러 version 에서 `sorted(tid)` 첫 번째 선택 | 가장 높은 version 선택, 같은 version·다른 내용이면 모호로 거절, `family.register` 가 같은 id 의 다른 레시피에 경고 |
| 7 | `family.set` 예시가 항상 NOT_FOUND, SPEC 누락, 죽은 코드 | 예시가 성공하도록(place 예시가 만든 인스턴스를 고침), SPEC 에 `FAMILY_INVALID`·`details.issues` 형식·은행가 반올림·`**` 플랫폼 차이·자원 상한 추가, 정수 지수 거듭제곱은 곱셈으로 계산해 플랫폼 간 같게 함, `_check_values_keys` 삭제 |

남은 한계: 시간 예산은 단계 사이에서 확인하므로 OCC 불리언 한 번 자체는 중단하지 못한다(공극 수 상한이 대신 막는다). 부피 감소 검사는 solid 마다 첫 인스턴스에서만 본다. `E_VOID_MISS` 는 경계 상자(보수적)라 대상과 상자만 겹치고 실제로는 안 닿는 공극은 `E_SHAPE_VOLUME` 에서만 잡힌다.

## 7. 재현

```
<python> spikes/s4-families/validate.py <recipe.json>...      # 전체 검증, stdout JSON {passed,total,results[]}
<python> spikes/s4-families/validate.py <recipe.json> --level static
<python> spikes/s4-families/run_s4.py
cd spikes/s1-core && <python> tools/parity.py ../s4-families/results/s4_parity.json
```
