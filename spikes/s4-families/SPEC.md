# 패밀리 레시피 사양 (dobim.family/1)

이 문서만 읽고 새 패밀리(문, 창, 기둥, 가구 등)를 JSON 레시피로 쓸 수 있도록 쓴 사양이다. 모르는 것은 추측하지 말고 검증기를 돌려서 확인한다. 검증기의 오류에는 경로(path)와 이유(message)와 고치는 법(hint)이 들어 있다.

## 0. 작업 순서

1. 레시피를 `*.json` 으로 쓴다(UTF-8, 주석·후행 쉼표 금지).
2. 검증기를 돌린다. 출력은 JSON이고 `ok` 가 `true` 여야 하며, `errors` 가 비어야 한다.

```
<프로젝트 venv의 python> spikes/s4-families/validate.py 내레시피.json            # 전체 검증(약 5초)
<프로젝트 venv의 python> spikes/s4-families/validate.py a.json b.json c.json     # 여러 개: passed/total 요약
<프로젝트 venv의 python> spikes/s4-families/validate.py 내레시피.json --level static   # 빠른 검사(1초 미만)
```

3. `errors[].path`(JSON 포인터) 위치를 `message` 와 `hint` 대로 고치고 다시 돌린다. 모두 통과할 때까지 반복한다.
4. 예시는 같은 폴더 구조의 라이브러리 4종이다. 표현법이 막히면 가장 비슷한 것을 열어 보라.
   `spikes/s1-core/src/dobim_core/family/library/` 의 `door.single.json`(호스팅·스윕·평면 기호), `window.fixed.json`(공극·반복),
   `column.section.json`(표시 조건·다각형), `wardrobe.builtin.json`(인스턴스 매개변수·반복·공극).

## 1. 큰 그림

- 레시피는 **텍스트 선언**이다. 코드는 없다. 값은 숫자와 식(§4)으로만 계산한다.
- **타입 매개변수**(`kind: type`)가 같은 인스턴스는 IFC 의 `IfcXxxType` 하나를 공유한다. **인스턴스 매개변수**(`kind: instance`)는 배치마다 다르다. 폭·높이처럼 "규격"은 type, 열림 방향·하단 높이·배치별 치수는 instance.
- 형상은 **돌출(extrude)** 과 **스윕(sweep)** 으로 만들고, **공극(void)** 으로 깎고, **표시 조건(when)** 으로 켜고 끄고, **반복(repeat)** 으로 늘어놓는다.
- **평면 기호**(2D 선·호·원)는 도면용이고 3D 절단이 아니다. 축척별로 따로 쓸 수 있다.
- **호스트**(벽)에 놓는 패밀리는 `host` 블록을 쓰고 벽에 개구부가 뚫린다. 자립형(기둥·가구)은 `host` 를 쓰지 않는다.
- 최종 산출물은 IFC4: 타입은 `IfcXxxType` + 표현 지도, 인스턴스는 `IfcXxx` + 타입 관계 + (호스팅이면) 개구부. 속성셋은 IFC 가 정본이다.

## 2. 최상위 구조

| 키 | 필수 | 설명 |
|---|---|---|
| `schema` | 예 | 항상 `"dobim.family/1"` |
| `id` | 예 | `분류.이름` 형식 소문자(`door.sliding`). 점 1개 이상, 문자·숫자·밑줄 |
| `version` | 예 | 정수 ≥ 1 |
| `name` | 예 | 표시 이름(64자 이하, 한글 가능) |
| `description` | 아니오 | 400자 이하 |
| `ifc` | 예 | §9 |
| `host` | 아니오 | §7. 없으면 자립형 |
| `params` | 예 | §3 (1~64개) |
| `derived` | 아니오 | §5 파생값 |
| `refs` | 아니오 | §5 참조 평면 |
| `constraints` | 아니오 | §5 제약 |
| `solids` | 예 | §6 (1~64개) |
| `plan_symbol` | 예 | §8. `default` 키 필수 |
| `psets` | 아니오 | §10 |

알 수 없는 키는 오류다(철자 주의). 레시피 전체는 64KB 이하.

## 3. 매개변수 `params`

키는 이름(`^[a-z][a-z0-9_]{0,31}$`), 값은 다음 객체.

| 필드 | 설명 |
|---|---|
| `kind` | `"type"` 또는 `"instance"` (필수) |
| `type` | `number`(기본) / `int` / `bool` / `enum` / `text` |
| `unit` | `mm` / `deg` / `none`. number 의 기본은 `mm`, 나머지는 `none` (길이는 항상 mm, 각도는 deg) |
| `default` | 기본값(필수). type 에 맞는 값 |
| `min`, `max` | number/int 의 범위. **가능하면 항상 쓴다** — 검증기가 범위 극단에서 형상이 깨지는지 시험한다 |
| `values` | enum 의 허용 문자열 목록(필수, 32개 이하, 중복 없음). default 는 이 중 하나 |
| `description` | 200자 이하 |

예: `"width": {"kind":"type","default":900,"min":600,"max":1500}`,
`"swing": {"kind":"instance","type":"enum","values":["L","R"],"default":"L"}`,
`"panes": {"kind":"type","type":"int","unit":"none","default":2,"min":1,"max":4}`.

이름은 `params`/`derived`/`refs` 가 한 이름공간을 쓴다. 겹치면 오류. 예약 이름: `host_thickness`, `host_height`, `true`, `false`, 함수 이름(§4).

## 4. 식 언어

숫자가 들어가는 거의 모든 칸(`default` 제외)에는 **숫자 또는 식 문자열**을 쓴다. `900` 도 `"width/2"` 도 된다.

- 문법: 숫자, 문자열(`'L'` 또는 `"L"`), `true`/`false`, 이름, `+ - * / // % **`, 단항 `-`, 비교 `== != < <= > >=`(연쇄 가능 `1 < x <= 3`), `and or not`, 조건식 `a if 조건 else b`, 괄호.
- 함수(위치 인자만): `min(a,b,..)`, `max(a,b,..)`, `abs(x)`, `round(x)`/`round(x,n)`, `floor(x)`, `ceil(x)`, `sqrt(x)`, `clamp(x, 하한, 상한)`.
- **없는 것**: 삼각함수, 지수/로그, 리스트·딕셔너리·인덱싱, 속성 접근(`.`), 람다, 대입. 이런 것을 쓰면 오류. 각도가 필요하면 매개변수(deg)로 받는다.
- 타입 규칙: 산술은 숫자끼리. `and/or/not` 과 조건식의 조건은 불리언(비교 결과 또는 bool 매개변수). 문자열은 `==`, `!=` 로만 비교(enum 판별용). 숫자와 문자열을 섞어 비교하면 오류.
- 한계: 식 300자, 노드 120개, 거듭제곱 지수 -8..8. 0 으로 나누기·음수 제곱근은 오류(그 값이 나오는 매개변수 범위를 `min`/`max` 로 막는다).
- **사용할 수 있는 이름**: 매개변수, 파생값, 참조 평면, (호스팅이면) `host_thickness`(벽 두께 mm), `host_height`(벽 높이 mm), (반복 안에서) 반복 변수.
- 속성셋·IFC 속성의 값 칸(§9, §10)은 **리터럴 또는 `{"expr": "식"}`** 이다. 거기서 일반 문자열 `"EI30"` 은 식이 아니라 문자열 값이다.

## 5. 파생값, 참조 평면, 제약

```json
"derived": { "leaf_w": "width - 2*frame_w", "hinge_x": "(-width/2) if swing == 'L' else (width/2)" },
"refs": { "left": {"axis": "x", "at": "-width/2"}, "head": {"axis": "z", "at": "height"} },
"constraints": [ {"expr": "leaf_w >= 300", "message": "문짝 폭이 300mm 미만이다"} ]
```

- `derived`: 이름 → 식. 서로 참조할 수 있지만 **순환하면 오류**(`E_CYCLE`). 평가 순서는 자동.
- `refs`: 이름 있는 참조 평면. `axis`(`x`/`y`/`z`)는 어느 축의 위치인지 문서화하는 표시이고 `at` 식으로 위치를 정한다. 사용법은 파생값과 같다(이름으로 부른다).
- `constraints`: `expr` 이 **항상 참**이어야 하는 불변식. 검증기가 매개변수 범위의 극단 조합에서 시험한다. 범위를 좁히거나 식을 고쳐서 항상 참이 되게 한다.
- **수준**: 인스턴스 매개변수, `host_thickness`/`host_height`, 그리고 그것들에 (간접으로라도) 의존하는 파생값·참조는 "인스턴스 수준"이고 나머지는 "타입 수준"이다. 타입 속성(`ifc.type_attrs`)과 `on: "type"` 속성셋은 **타입 수준 이름만** 참조할 수 있다(`E_TYPE_REFERENCES_INSTANCE`).

## 6. 형상 `solids`

로컬 좌표계: **x = 호스트 축선 방향(문 폭 방향), y = 호스트 두께 방향(벽 중심선이 0), z = 위쪽**, mm.
호스팅 패밀리의 원점: x = 개구부 중심(배치 offset), y = 벽 중심선, z = 배치 높이(보통 벽 바닥). 자립형의 원점: 평면 위치(footprint 중심), z = 레벨 바닥(배치 elevation).

공통 필드: `id`(`^[a-z][a-z0-9_]{0,31}$`, 고유), `op`, 선택: `when`(표시 조건 식 또는 bool), `repeat`, `role`, `cuts`.

### 6.1 돌출 `extrude`

```json
{"id": "leaf", "op": "extrude", "plane": "xz",
 "profile": {"shape": "rect", "u": ["-w/2", "w/2"], "v": [0, "h"]},
 "range": ["-t/2", "t/2"]}
```

| plane | 프로파일 좌표 (u, v) | 돌출축(range 의 축) |
|---|---|---|
| `xy` | (x, y) | z — 바닥 평면 단면을 위로 |
| `xz` | (x, z) | y — 입면 단면을 벽 두께 방향으로 |
| `yz` | (y, z) | x |

- `range` = `[시작, 끝]` 돌출축의 **절대 좌표**(시작 < 끝). 예: `xz` 에서 `[-20, 20]` 은 y -20..20.
- 프로파일: `{"shape":"rect","u":[u0,u1],"v":[v0,v1]}`, `{"shape":"circle","center":[u,v],"r":반지름}`, `{"shape":"polygon","points":[[u,v],...]}`(3~32점, 넓이 0 금지).
- 치수가 0 이하(퇴화)이거나 좌표가 ±100m 를 넘으면 오류.

### 6.2 스윕 `sweep` (틀·손잡이처럼 경로를 따라가는 단면)

```json
{"id": "frame", "op": "sweep", "normal": "y", "closed": false,
 "path": [[-400,0,0],[-400,0,2050],[400,0,2050],[400,0,0]],
 "profile": {"shape": "rect", "w": 50, "h": "host_thickness"}}
```

- `path`: 2~16개의 3D 점 `[x,y,z]` 연속 직선 구간. `closed: true` 면 마지막 점에서 첫 점으로 닫는다.
- 단면: `rect` 는 `w`(경로에 수직인 면 안의 폭)와 `h`(`normal` 축 방향 두께), `circle` 은 `r`. 단면 중심이 경로 위에 놓인다.
- `normal`: 단면의 `h` 방향 축(`x`/`y`/`z`, 기본 `y`). 경로 구간이 이 축과 평행하면 오류(`E_SWEEP_NORMAL`). 벽 면 안에 그린 틀은 `normal: "y"`.
- 꺾이는 점에서는 단면 반폭만큼 구간이 서로 겹쳐 모서리를 메운다(마이터 없음). 열린 경로의 두 끝은 그대로.

### 6.3 공극 `role: "void"`

`"role": "void", "cuts": ["대상id", ...]` — 대상 solid 에서 이 형상을 **빼낸다**. 대상은 `role: solid`(기본)인 형상의 id. 반복 형상은 기본 id 로 지정하면 모든 반복에 적용된다. 공극 자체는 따로 보이지 않는다.

### 6.4 표시 조건과 반복

- `"when": "section == 'round'"` — 식이 거짓이면 이 형상(공극·평면 기호 요소 포함)은 없다. bool 리터럴도 된다.
- `"repeat": {"var": "i", "count": "shelves"}` — `count`(0..50 정수)번 만들고 각 번에서 `i`(0부터)를 식에 쓸 수 있다. 결과 id 는 `shelf[0]`, `shelf[1]`…. 형상 총 400개 이하.

## 7. 호스트 `host`

```json
"host": { "kinds": ["wall"], "cut": { "x": ["-width/2", "width/2"], "z": [0, "height"] } }
```

- `kinds`: 지금은 `["wall"]` 만. 
- `cut`: 벽에 뚫을 개구부의 로컬 사각형(`x` 범위, `z` 범위). 생략하면 개구부 없음(벽걸이 가구 등). 개구부는 벽 두께 전체를 관통한다.
- `host` 를 쓰면 `host_thickness`, `host_height` 이름을 쓸 수 있고, 배치 때 `host`+`offset` 을 준다. `host` 가 없으면 `level`+`position` 을 준다.

## 8. 평면 기호 `plan_symbol`

```json
"plan_symbol": {
  "default": [ {"type":"rect","u":[-450,450],"v":[-100,100],"style":"cut"},
               {"type":"arc","center":[-400,0],"radius":800,"start":0,"end":90} ],
  "1:200":   [ {"type":"arc","center":[-400,0],"radius":800,"start":0,"end":90} ]
}
```

- 키: `default`(필수) 와 `1:50`, `1:100`, `1:200` 같은 축척. 뷰어는 축척에 맞는 키가 없으면 `default` 를 쓴다. IFC 에는 `default` 만 들어가고 나머지는 `DoBIM_Intent` 의 레시피 전문에 남는다.
- 요소: `line`(`from`,`to`), `arc`(`center`,`radius`,`start`,`end` — 각도 deg, **반시계가 +**, `start < end ≤ start+360`), `circle`(`center`,`radius`), `rect`(`u`,`v`), `polyline`(`points`,`closed`). 각 요소에 `when`(표시 조건)과 `style`(`cut`/`projection`/`hidden`, 기본 projection) 가능.
- 좌표는 로컬 평면 (x, y). 문이 +y 쪽으로 열린다고 약속하고 쓰면 된다. 요소 총 200개 이하.
- 열림 방향처럼 인스턴스마다 다른 기호는 `when` 이나 식으로 뒤집는다(예: `"start": "0 if swing == 'L' else 90"`).

## 9. IFC 투영 `ifc`

```json
"ifc": { "class": "IfcDoor", "type_class": "IfcDoorType", "predefined": "DOOR",
         "type_attrs": { "OperationType": "NOTDEFINED" },
         "attrs": { "OverallWidth": {"expr": "width"}, "OperationType": {"expr": "'SLIDING_TO_LEFT' if slide == 'L' else 'SLIDING_TO_RIGHT'"} } }
```

- `class`: IFC4 요소 클래스(`IfcDoor`, `IfcWindow`, `IfcColumn`, `IfcFurniture`, `IfcBeam`, `IfcRailing`...). `type_class` 생략하면 `class`+`Type`.
- `predefined`: 타입의 `PredefinedType` 열거값(인스턴스에도 같은 값이 들어간다). 클래스마다 허용값이 다르다. 틀리면 검증기가 허용값을 알려 준다. 자주 쓰는 값:
  - IfcDoorType: `DOOR GATE TRAPDOOR USERDEFINED NOTDEFINED`
  - IfcWindowType: `WINDOW SKYLIGHT LIGHTDOME USERDEFINED NOTDEFINED`
  - IfcColumnType: `COLUMN PILASTER USERDEFINED NOTDEFINED`
  - IfcFurnitureType: `CHAIR TABLE DESK BED FILECABINET SHELF SOFA USERDEFINED NOTDEFINED`
- `USERDEFINED` 를 쓰면 `type_attrs` 에 `"ElementType": "이름"`, `attrs` 에 `"ObjectType": "이름"` 이 필요하다.
- `type_attrs`: 타입 객체의 속성(타입 수준 값만 참조). `attrs`: 인스턴스 객체의 속성(인스턴스 값 참조 가능).
  - 값은 리터럴 또는 `{"expr": ...}`. 열거 속성은 열거값 문자열, 실수는 숫자, 불리언은 true/false.
  - 문·창 자주 쓰는 속성: IfcDoor `OverallWidth OverallHeight OperationType`, IfcDoorType `OperationType ParameterTakesPrecedence`, IfcWindow `OverallWidth OverallHeight PartitioningType`, IfcWindowType `PartitioningType ParameterTakesPrecedence`.
  - `IfcDoorTypeOperationEnum`: `SINGLE_SWING_LEFT SINGLE_SWING_RIGHT DOUBLE_DOOR_SINGLE_SWING DOUBLE_DOOR_SINGLE_SWING_OPPOSITE_LEFT DOUBLE_DOOR_SINGLE_SWING_OPPOSITE_RIGHT DOUBLE_SWING_LEFT DOUBLE_SWING_RIGHT DOUBLE_DOOR_DOUBLE_SWING SLIDING_TO_LEFT SLIDING_TO_RIGHT DOUBLE_DOOR_SLIDING FOLDING_TO_LEFT FOLDING_TO_RIGHT DOUBLE_DOOR_FOLDING REVOLVING ROLLINGUP SWING_FIXED_LEFT SWING_FIXED_RIGHT USERDEFINED NOTDEFINED`
  - `IfcWindowTypePartitioningEnum`: `SINGLE_PANEL DOUBLE_PANEL_VERTICAL DOUBLE_PANEL_HORIZONTAL TRIPLE_PANEL_VERTICAL TRIPLE_PANEL_BOTTOM TRIPLE_PANEL_TOP TRIPLE_PANEL_LEFT TRIPLE_PANEL_RIGHT TRIPLE_PANEL_HORIZONTAL USERDEFINED NOTDEFINED`
- `Name`, `GlobalId`, 배치·표현, `PredefinedType` 은 코어가 정하므로 attrs 에 쓰면 오류.

## 10. 속성셋 `psets`

```json
"psets": {
  "Pset_DoorCommon": { "on": "type", "props": { "IsExternal": {"expr": "is_external"}, "FireRating": "EI30" } },
  "DoBIM_DoorParams": { "on": "instance", "props": { "Swing": {"expr": "swing"} } }
}
```

- `on`: `"type"`(타입 객체에 기록, 타입 수준 값만 참조) 또는 `"instance"`.
- `Pset_`/`Qto_` 로 시작하는 이름은 **buildingSMART 표준**이다. 존재하는 표준 속성셋이어야 하고, 이 패밀리의 `ifc.class` 에 적용되는 것이어야 하며, 속성 이름과 값 종류(불리언/문자/숫자)와 열거값이 표준 템플릿과 맞아야 한다. 표준이 아닌 것은 **다른 접두사**(`DoBIM_...`, 회사 접두사)를 쓴다.
- 자주 쓰는 표준 속성(이름: 값 종류):
  - `Pset_DoorCommon`: Reference(문자) Status(열거 NEW EXISTING DEMOLISH TEMPORARY OTHER NOTKNOWN UNSET) FireRating AcousticRating SecurityRating DurabilityRating HygrothermalRating WaterTightnessRating MechanicalLoadRating WindLoadRating(문자) Infiltration ThermalTransmittance GlazingAreaFraction(숫자) IsExternal HandicapAccessible FireExit HasDrive SelfClosing SmokeStop(불리언)
  - `Pset_WindowCommon`: Reference Status AcousticRating FireRating SecurityRating WaterTightnessRating MechanicalLoadRating WindLoadRating(문자/열거) Infiltration ThermalTransmittance GlazingAreaFraction(숫자) IsExternal HasSillExternal HasSillInternal HasDrive SmokeStop FireExit(불리언)
  - `Pset_ColumnCommon`: Reference Status FireRating(문자) Slope Roll ThermalTransmittance(숫자) IsExternal LoadBearing(불리언)
  - `Pset_FurnitureTypeCommon`: Reference Status Style MainColor(문자) NominalHeight NominalLength NominalDepth(숫자) IsBuiltIn(불리언)
  - 그 밖의 속성셋·속성은 검증기 오류 메시지의 "표준 속성" 목록으로 확인한다.
- 값에는 리터럴(`true`, `1.5`, `"EI30"`)이나 `{"expr": "식"}`. 문자열은 255자 이하.

## 11. 검증기가 보는 것 (`checks`)

| 검사 | 내용 | 실패 시 주로 나오는 코드 |
|---|---|---|
| `schema` | JSON 구조, 키, 타입, 매개변수 기본값·범위 | `E_JSON` `E_SCHEMA` |
| `references` | 식 구문, 정의되지 않은 이름, 순환, 이름 중복, 타입/인스턴스 수준 위반, 호스트 문맥, solid id 중복, `cuts` 대상 | `E_EXPR_*` `E_UNDEFINED_NAME` `E_CYCLE` `E_NAME_*` `E_TYPE_REFERENCES_INSTANCE` `E_HOST_CONTEXT` `E_CUT_TARGET` `E_PLAN_*` |
| `values` | 기본값·각 매개변수 최솟값/최댓값·모든 최소/모든 최대·쌍별 극단·각 열거값·호스트 두께 100/300/600 에서 값 검사·식 평가·제약 | `E_PARAM_*` `E_CONSTRAINT` `E_EXPR_MATH` `E_EXPR_TYPE` |
| `geometry` | 같은 표본에서 형상 해석: 퇴화(0 이하 치수·역전된 범위), 좌표 범위, 반복 횟수, 스윕 방향 | `E_DEGENERATE` `E_GEOMETRY_RANGE` `E_REPEAT_RANGE` `E_SWEEP_NORMAL` `E_LIMIT` |
| `ifc_schema` | 클래스·타입 클래스·PredefinedType·속성 이름/종류/열거값(식 결과도 표본마다) | `E_IFC_CLASS` `E_IFC_ENUM` `E_IFC_ATTR_*` `E_IFC_USERDEFINED` |
| `psets_standard` | `Pset_`/`Qto_` 표준 대조 | `E_PSET_NOT_STANDARD` `E_PSET_APPLICABLE` `E_PSET_PROP_UNKNOWN` `E_PSET_VALUE_TYPE` `E_PSET_ENUM` |
| `ifc_projection` | 실제 코어 투영으로 IFC 를 만들고 `ifcopenshell.validate`(express 규칙) 오류 0 | `E_IFC_BUILD` `E_IFC_INVALID` |
| `geometry_shape` | 인스턴스마다 실제 메시를 만들어 비어 있지 않고 납작하지 않고 부피 > 0 | `E_SHAPE_*` |
| `psets_values` | IFC 에서 다시 읽은 속성셋 값이 레시피가 평가한 값과 같다 | `E_PSET_MISMATCH` |

경고(`warnings`, 통과를 막지 않음): `W_UNUSED`(안 쓰는 매개변수·파생값), `W_SOLID_NEVER_VISIBLE`(어떤 표본에서도 안 만들어지는 형상 — `when` 오타나 항상 거짓 조건을 의심).

표본 검사는 증명이 아니다. 극단 조합 전부를 보지는 않으므로, 제약(`constraints`)과 범위(`min`/`max`)를 정직하게 써야 한다.

## 12. 코어 커맨드

레시피를 프로젝트에 올리고 배치하는 커맨드(CLI `run`, MCP `dobim_execute`/`dobim_query`):

- `family.list` — 기본 라이브러리 목록
- `family.validate` `{family|path|recipe, level}` — 위 검증기(프로젝트 불필요)
- `family.register` `{family|path|recipe, type_values, name}` — 타입 만들기(같은 레시피·값이면 같은 타입 id)
- `family.place` `{family 또는 type, type_values, values, host+offset 또는 level+position, rotation, elevation, name}` — 인스턴스 배치(타입이 없으면 자동 생성). 호스팅이면 벽에 개구부.
- `family.set` `{id, values, offset|position, rotation, elevation, name}` — 인스턴스 수정
- `family.preview` `{family 또는 type, type_values, values, scale}` — 평가 결과(개구부·형상 목록·평면 기호 도형·속성셋)를 요소 없이 확인

## 13. 흔한 실수

1. 입면(`xz`) 프로파일의 `v` 는 z 다. 두께 방향은 `range`(y)다.
2. `range` 를 `[두께]` 처럼 길이로 쓰지 말고 `[시작, 끝]` 절대 좌표로 쓴다. 중심 기준이면 `["-t/2", "t/2"]`.
3. 타입 속성셋에서 인스턴스 매개변수를 참조 → `E_TYPE_REFERENCES_INSTANCE`. `on: "instance"` 로 옮긴다.
4. 열거 매개변수는 문자열이다: `swing == 'L'`. 따옴표를 빼면 정의되지 않은 이름 오류.
5. 정수 매개변수가 필요한 반복 횟수는 `type: "int"` 와 `min`/`max` 로 막는다(`repeat.count` 는 0..50 정수여야 한다).
6. 구멍(공극)을 뚫는 형상은 대상보다 두께 방향으로 넉넉히 길게(`range` 를 크게) 잡는다.
7. 문처럼 벽에 들어가는 패밀리의 두께는 `host_thickness` 를 쓴다. 벽이 얇은 경우 제약 `host_thickness >= ...` 로 막는다.
8. `Pset_` 접두사는 표준 전용이다. 자기 속성은 `DoBIM_` 같은 접두사로.
9. `USERDEFINED` 를 쓰면 `ElementType`(타입)과 `ObjectType`(인스턴스)를 같이 쓴다.
10. 평면 기호의 호 `start`/`end` 는 도(deg)이고 `end` 가 더 커야 한다. 반시계 방향.

## 14. 최소 완전 예시 (자립형 선반)

```json
{
  "schema": "dobim.family/1", "id": "furniture.shelf_unit", "version": 1, "name": "선반장",
  "ifc": { "class": "IfcFurniture", "type_class": "IfcFurnitureType", "predefined": "SHELF" },
  "params": {
    "board_t": {"kind": "type", "default": 18, "min": 12, "max": 30},
    "width":   {"kind": "instance", "default": 800, "min": 400, "max": 1500},
    "height":  {"kind": "instance", "default": 1800, "min": 600, "max": 2400},
    "depth":   {"kind": "instance", "default": 300, "min": 200, "max": 600},
    "tiers":   {"kind": "instance", "type": "int", "unit": "none", "default": 4, "min": 1, "max": 10}
  },
  "constraints": [ {"expr": "height > board_t*(tiers + 1) + 100", "message": "단 사이 간격이 100mm 이하다"} ],
  "solids": [
    {"id": "side_l", "op": "extrude", "plane": "yz", "profile": {"shape": "rect", "u": ["-depth/2", "depth/2"], "v": [0, "height"]}, "range": ["-width/2", "-width/2 + board_t"]},
    {"id": "side_r", "op": "extrude", "plane": "yz", "profile": {"shape": "rect", "u": ["-depth/2", "depth/2"], "v": [0, "height"]}, "range": ["width/2 - board_t", "width/2"]},
    {"id": "board", "op": "extrude", "plane": "xy", "repeat": {"var": "i", "count": "tiers + 1"},
     "profile": {"shape": "rect", "u": ["-width/2 + board_t", "width/2 - board_t"], "v": ["-depth/2", "depth/2"]},
     "range": ["i*(height - board_t)/tiers", "i*(height - board_t)/tiers + board_t"]}
  ],
  "plan_symbol": { "default": [ {"type": "rect", "u": ["-width/2", "width/2"], "v": ["-depth/2", "depth/2"], "style": "cut"} ] },
  "psets": { "Pset_FurnitureTypeCommon": {"on": "type", "props": {"IsBuiltIn": false}} }
}
```

(`repeat.count` 에도 식을 쓸 수 있다. 위 예시는 `tiers + 1` 장의 판을 만든다.)
