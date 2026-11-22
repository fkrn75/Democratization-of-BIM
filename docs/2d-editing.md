# 2D 편집 설계 — 2D 작도 커맨드(A)와 평면 GUI 편집기(B)

- 일자: 2026-11-18
- 상태: A 는 구현(브랜치 `phase1/drafting-2d`, 27단계). B 는 B0(패리티 G 검사기·조작 기록 재생기)·B1(코어 조회 `view.scene`·`view.pick`·`geom.snap`) 구현(브랜치 `phase1/gui-b0-b1`, 2.8절), B2 이후 미착수. B 의 기술 결정은 [ADR-0016](adr/0016-plan-editor-gui.md)
- 관련: ADR-0004(플랫폼·스택), ADR-0011(모든 기능은 커맨드, 패리티 G·C·A, 헤드리스), ADR-0015(뷰·주석 정의 왕복), 로드맵 2a·2b, `spikes/s6-viewer/report.md`

사용자 요청은 "도빔에 2D 편집 기능도 넣어줘"이고 범위는 둘 다다. (A) 도면·뷰 위의 CAD식 2D 작도를 CLI·MCP 커맨드로 먼저 만들고, (B) 그 위에 평면에서 벽·문·창을 그리고 옮기면 모델이 바뀌는 GUI 편집기를 둔다(로드맵 2b 의 "2D 평면 편집"을 앞당김).

## 1. A — 2D 작도 커맨드 (구현됨)

### 1.1 요소 종류

| 종류 | 무엇 | IFC | 관계 |
|---|---|---|---|
| `draft` | 뷰에 속한 2D 요소 하나. 모양 `shape` = line·polyline·arc·circle·text·region·dim, W1 1-B 에서 dim_angle·dim_radius·dim_diameter·dim_arc·dim_chain·dim_base·spot_elev·spot_coord·spot_slope·mask·cloud 추가 | 비 IFC 의도 요소(`NON_IFC_KINDS`), 정의는 `DoBIM_Defs` | `annot_of` → 뷰(뷰를 지우면 함께 지워지고 되돌리면 함께 돌아온다) |
| `dlayer` | 층 스타일(이름·선굵기·선종류·색) | 같음 | 없음(요소가 층 이름으로 가리킨다) |

`draft` 파라미터는 모양과 관계없이 키 집합이 고정이다(병합·가져오기 검사가 키 집합으로 모양을 거른다):
`name, view, shape, pts, closed, r, a0, a1, text, size, off, fill, align, layer, weight, linetype, color, group`.

- 좌표 `pts` 는 **그 뷰의 2D 좌표**다(평면 x,y / 단면·입면 s,z mm, `view_lines` 와 같은 공간). 모델 요소와 연관되지 않는다(연관 치수·태그는 S5 `annot.*`).
- 모양별 값: 선·치수 점 2개, 폴리선 2~256개(`closed`), 영역 3~256개(자기 교차 금지, 반시계로 저장, `fill` none/solid/hatch/cross), 호·원 중심 1개 + `r`(호는 `a0`→`a1` 반시계 도), 문자 기준점 1개 + `text`·`size`(용지 mm)·`align`·회전 `a0`, 치수 `off`(치수선 간격, 진행 방향 왼쪽 +)·`text`(값 대신 쓸 글).
- W1 1-B 모양(키 집합은 그대로, 필드 뜻만 모양별): 각도 치수 `dim_angle` 점 3개(꼭짓점·첫 방향·둘째 방향, 반시계) + `r`(치수 호 반지름), 반지름·지름 `dim_radius`·`dim_diameter` 중심 + `r` + `a0`(치수선 방향), 호 길이 `dim_arc` 중심 + `r` + `a0`→`a1` + `off`(치수 호 간격, 바깥 +), 연속·기준선 `dim_chain`·`dim_base` 점 2~256개 + `a0`(측정 방향) + `off`(첫 점에서 치수선까지, 왼쪽 +, 기준선은 용지 7mm 간격으로 쌓는다), 표고·좌표 `spot_elev`·`spot_coord` 점 2개(재는 점·글 위치), 경사 `spot_slope` 점 2개(높은 쪽 -> 낮은 쪽), 마스킹 `mask` 경계 3~256개(`closed` 면 경계선을 그린다), 리비전 구름 `cloud` 경계 3~256개 + `r`(호 크기, 0 이면 용지 8mm) + `text`(리비전 번호). 값은 그릴 때 잰다: 표고는 단면·입면에서 점의 높이, 평면에서 그 뷰 레벨 높이(표면 표본 아님), 배치도는 `text` 필수. 경사는 단면·입면에서만 재고 평면·배치도는 `text` 필수. 상세는 spikes/phase1-drawings/baseline.md 34절.
- 스타일: `weight` thin 0.09·fine 0.18·medium 0.25·thick 0.5mm(기존 해치·투영·기호·절단 선 굵기 체계), `linetype` continuous·dashed·hidden·center·dashdot·dotted, `color` "#rrggbb". 셋 다 null 이면 층 스타일을 따른다(bylayer). 층 스타일이 없으면 fine·실선·검정.
- 상세 그룹: `group` 이름. 같은 뷰의 같은 이름이 한 묶음이고, 편집 커맨드는 `ids` 대신 `view + group` 으로 묶음 전체를 고를 수 있다.
- 층 이름은 DXF 층 이름으로 그대로 나가므로 DXF 금지 문자(`<>/\":;?*|=` 백틱)·제어 문자·앞뒤 공백을 막는다.

### 1.2 커맨드(15개, 모두 레지스트리 → CLI·MCP 자동 생성)

| 커맨드 | 하는 일 |
|---|---|
| `draft.create` / `draft.set` | 생성 / 점·모양별 값·스타일·이름 변경(`bylayer` 로 층 스타일 복귀) |
| `draft.delete` / `draft.move` / `draft.copy` / `draft.rotate` / `draft.mirror` | 여러 요소(ids 또는 view+group)에. 복사는 배열(`count`)·다른 뷰(`to_view`)·새 그룹(`as_group`), 대칭은 `keep` 이면 원본 유지 |
| `draft.offset` | 선·폴리선·영역(꺾인 곳은 이웃 변 연장 교점)·호·원(반지름 ±) 평행 복사본 |
| `draft.trim` / `draft.extend` | 선·열린 폴리선의 한쪽 끝을 경계(선·폴리선·영역·치수·원·호)와 만나는 곳까지 자르기/늘이기 |
| `draft.group` | 상세 그룹 묶기·풀기(한 뷰 안) |
| `draft.list` | 목록(뷰·층·그룹 거르기, 쪽 나누기, 치수 값) |
| `draft.layer.set` / `draft.layer.delete` / `draft.layer.list` | 층 스타일. id 는 층 이름에서 결정적으로 만든다 |

모든 변경 커맨드는 `write` 등급(되돌리기로 복구되므로 확인 토큰 없음)이고, 커맨드 로그·되돌리기·다시하기·병합(`merge.py` 의 뷰 없는 요소 검사 포함)을 그대로 탄다.

사용 예(CLI):

```
dobim run draft.layer.set -p p.dobim --json '{"name":"A-NOTE","weight":"medium","color":"#204080"}'
dobim run draft.create -p p.dobim --json '{"view":"<평면 뷰>","shape":"text","pts":[[3000,-1500]],"text":"주기 1","layer":"A-NOTE"}'
dobim run draft.create -p p.dobim --json '{"view":"<평면 뷰>","shape":"region","pts":[[0,0],[1500,0],[1500,900],[0,900]],"fill":"hatch"}'
dobim run draft.copy -p p.dobim --json '{"view":"<평면 뷰>","group":"상세 A","dx":3000,"count":2}'
```

### 1.3 도면 출력

- 질의 `drafting.view_drafts(뷰)` 가 그림 항목(선·원·채움·문자, 뷰 좌표 + 용지 mm 굵기·색·층)을 낸다. 선종류는 **경로를 따라 미리 마디로 나눈 실선 조각**으로 낸다: SVG·PDF·PNG·DXF 어느 렌더러도 선종류를 몰라도 같은 모양이 나온다. 해치는 45도(교차는 45·135도) 2.5mm 간격 선분, 치수는 보조선·치수선·45도 눈금·값 글로 분해한다. 마스킹 영역은 흰 채움 항목으로 내고 2D 요소 중 가장 먼저 그린다(모델 선만 가린다). DXF 는 이것을 흰 SOLID 해치(트루컬러 16777215)로 쓴다.
- `view.export`(SVG·PDF·PNG)와 `sheet.export`·`sheet.render`·`view.image`·`sheet.image` 에 그려진다. 뷰 데이터에 `drafts` 키는 **2D 요소가 있을 때만** 생기므로 2D 요소가 없는 뷰·시트의 출력 바이트는 그대로다(시험으로 확인).
- 시트(그리고 DXF)는 요소마다 그룹 `draft-<모양>` 에 `data-layer`(층 이름)·`data-id` 를 붙인다. DXF 내보내기(dobim-dxf, 별도 PR)는 시트 그림 요소를 읽어 `data-layer` 를 DXF 층으로 쓰므로 2D 요소가 자동으로 층별로 나간다(실선 원은 CIRCLE, 채움은 SOLID 해치, 나머지는 선·문자).
- `view.linework`(모델 선분·디지스트)는 바뀌지 않는다: 2D 요소는 모델 그림이 아니다.
- 뷰의 잘라내기(crop)는 2D 요소에 적용하지 않는다(알려진 한계).

### 1.4 IFC 왕복

ADR-0015 의 그릇을 그대로 쓴다(`viewdefs.DEF_KINDS` 에 `dlayer`·`draft` 추가, family_id `draft.layer`·`draft.basic`). 가져오기 규칙: 층 스타일은 이름이 겹치면 둘 다 되살리지 않고, 2D 요소는 속한 뷰가 되살려진 뷰여야 한다. 그 밖(크기·변조 check·모양 검사)은 기존 정의와 같다. 2D 요소가 없는 프로젝트의 IFC 는 바이트까지 그대로다(정의가 없으면 속성셋을 만들지 않는다). 시험: 왕복 뒤 해시 3종·파라미터 같음, 다시 내보낸 IFC 바이트 같음, 모두 지우면 2D 작도 전 IFC 와 바이트 같음, 변조 정의 거절.

### 1.5 A 의 남은 범위(후속 PR 후보)

- 시트 소속 2D 요소(용지 좌표): `sheet.export` 가 시트 정의 id 를 모르는 경로(뷰 목록 인자)라 소속 판정·그리기 경로를 따로 정해야 한다.
- 뷰 잘라내기 적용, 폴리선 중간 구간 자르기(지금은 끝만), 원·호 자르기, 모서리 다듬기(fillet·chamfer), 스냅·객체 추적 질의(`geom.snap`), 블록(심볼 정의·삽입), 문자 스타일(글꼴·굵게).
- DXF 의 진짜 선종류(LTYPE) 내보내기: 지금은 마디로 나눈 선분이 나간다.

## 2. B — 평면 GUI 편집기 (설계)

### 2.1 원칙(ADR-0011 의 패리티 G)

1. **GUI 는 커맨드만 호출한다.** 화면의 모든 확정 동작(그리기·옮기기·지우기·속성 바꾸기)은 레지스트리 커맨드 한 번(또는 트랜잭션 묶음)이다. GUI 는 코어 내부를 import 하지 못하고(import-linter), 프로젝트 저장소에 직접 쓰지 않는다.
2. **GUI 전용 기능 0개.** GUI 가 필요한 조회(화면에 그릴 선분, 집기, 스냅)도 먼저 코어 조회 커맨드로 만든다. 그래서 에이전트도 같은 것을 쓴다.
3. **제외 목록은 `gui_exempt.yaml` 하나뿐.** 순수 화면 조작(카메라 이동·확대, 호버, 끌기 미리보기, 임시 선택, 패널 배치, 테마, 단축키)만 들어가고 항목마다 이유를 적는다.
4. **조작 기록 = 커맨드 목록.** GUI 세션은 실행한 커맨드를 순서대로 기록(매크로)한다. 같은 기록을 헤드리스 CLI 로 재생하면 같은 `model.hash` 가 나와야 한다.

### 2.2 구조

```
 Tauri 2 창 (Windows, WebView2)
 ├─ Svelte 5 UI: 도구 막대·속성 패널·층/뷰 목록 ─────────┐
 ├─ 평면 캔버스: PixiJS v8 (WebGL2, 필요 시 Canvas)      │ 모든 확정 동작 = invoke(command, args)
 │    그리는 것 = view.scene 결과(요소 id 가 붙은 선분)  │
 └─ 커맨드 클라이언트(얇은 어댑터) ─────────────────────┘
          │ stdio JSON 줄(같은 dispatch 봉투: ok/result/error/rev)
 dobim 코어 사이드카 (Python, dobim-core 그대로: CLI·MCP 와 같은 dispatch.run)
```

- 사이드카는 이미 있는 `dispatch.run` 을 그대로 부르는 줄 단위 JSON 서버(`dobim gui-serve`)다. MCP 와 같은 봉투·오류 코드·rev 를 쓰므로 GUI 쪽에 새 프로토콜이 없다. 에이전트가 같은 프로젝트를 동시에 쓰는 경우는 아키텍처 15.6 의 `dobim daemon` 이 직렬화한다(2b 선택 범위).
- 평면 캔버스는 코어가 계산한 선분을 그대로 그린다(GUI 가 형상을 다시 계산하지 않는다 = 화면과 도면 출력이 같은 원천). 바뀐 요소만 다시 받는다: 변경 응답의 `rev` → `view.affected` → 영향받은 뷰의 `view.scene` 증분.
- 3D 뷰는 이 범위 밖이다. 붙일 때는 S6 권고(ifc-lite WebGPU, 폴백 three.js WebGL2)를 따른다.

### 2.3 GUI 를 위해 코어에 더할 조회 커맨드(GUI 전용 아님: 에이전트도 쓴다)

| 커맨드 | 하는 일 | 왜 필요 |
|---|---|---|
| `view.scene` | 뷰의 그림을 **요소 id 가 붙은** 선분·채움·문자로(지금 `view.linework` 는 병합된 선분이라 어느 요소의 선인지 모른다). 2D 작도 항목 포함. 쪽 나누기 | 화면 그리기·선택 강조 |
| `view.pick` | 뷰 좌표 한 점(+허용 거리)에 걸리는 요소 id 목록(가까운 순) | 클릭 선택. 에이전트도 "이 위치의 벽" 질의에 쓴다 |
| `geom.snap` | 점 → 가장 가까운 끝점·중점·교점·축선·격자 후보(종류·좌표) | 그리기 스냅(아키텍처 15.2 표의 `geom.snap`) |
| `selection.save` / `session.selection` | 선택 집합 저장 / GUI 가 붙어 있을 때 현재 선택 읽기(없으면 `NO_GUI_ATTACHED`) | 15.2 표 그대로 |

### 2.4 최소 범위(MVP)

| 화면 동작 | 커맨드 |
|---|---|
| 평면 뷰 열기·표시 | `view.list`, `view.scene` |
| 이동·확대·호버 | 제외(`gui_exempt.yaml`: 저장·출력 무관) |
| 클릭·상자 선택 | `view.pick`(임시 선택은 제외, 저장은 `selection.save`) |
| 벽 그리기(두 점, 연속) | `wall.create`(연속 그리기는 한 트랜잭션 묶음), 끝 이어짐은 `join.auto` |
| 벽 옮기기·끝점 끌기 | `wall.move`, `wall.set`(axis) |
| 문·창 배치·옮기기 | `door.place`·`door.set`, `family.place`·`family.set`(창은 패밀리) |
| 2D 작도 도구 | `draft.*` 전부(선·폴리선·호·원·문자·치수·영역, 이동·복사·회전·대칭·오프셋·자르기·연장, 층 패널) |
| 지우기·되돌리기 | `element.delete`·`draft.delete`, `history.undo`·`history.redo` |
| 속성 패널 | `element.get`, `*.set` |

끌기 중의 미리보기(고무줄 선)는 화면에서만 그리고 놓을 때 커맨드 하나를 낸다(미리보기는 제외 목록). 스냅 후보 계산은 `geom.snap` 조회로 하되 마우스 이동마다 부르지 않도록 화면 쪽에서 디바운스한다(조회라 저장소를 건드리지 않는다).

### 2.5 패리티 G 자동 검사

1. **정적 매핑 검사(빌드 실패 조건).** GUI 의 모든 메뉴·도구·단축 동작은 `gui/actions.json` 에 선언된다: `{"id", "command"}` 또는 `{"id", "exempt"}`. 검사기(`tools/parity_g.py`)는 (a) `command` 가 레지스트리에 있는지, (b) `exempt` id 가 `gui_exempt.yaml` 에 이유와 함께 있는지, (c) 코드에서 커맨드 클라이언트를 거치지 않고 사이드카를 부르는 곳이 없는지(정적 검색) 본다.
2. **제외 동작 무해성 검사.** 헤드리스 GUI 시험 하네스가 제외 동작(확대·호버·임시 선택 등)을 모두 실행한 뒤 프로젝트의 `rev`·해시 3종이 그대로인지 본다. 하나라도 바뀌면 실패.
3. **조작 기록 재생 해시 시험.** GUI 시험 시나리오(벽 그리기 → 문 배치 → 2D 주기 → 되돌리기 …)가 남긴 조작 기록(커맨드 목록)을 새 프로젝트에 CLI 로 재생해 `model.hash` 가 GUI 세션의 최종 해시와 같은지 본다. 커맨드 레지스트리 하나를 쓰므로 정의상 같아야 하고, 다르면 GUI 가 커맨드 밖에서 상태를 바꾼 것이다.

`gui_exempt.yaml` 모양(초안):

```yaml
- id: canvas.pan
  reason: 화면 이동. 저장·출력 무관(아키텍처 15.2 카메라 조작)
- id: canvas.zoom
  reason: 화면 확대. 저장·출력 무관
- id: canvas.hover
  reason: 강조 표시만
- id: canvas.drag_preview
  reason: 끌기 중 고무줄 미리보기. 놓을 때 확정 커맨드를 낸다
- id: selection.temp
  reason: 임시 선택. 저장은 selection.save
- id: ui.panel_layout
  reason: 패널 배치·테마·창 크기
```

### 2.6 시험 방법(브라우저·화면 제어 도구 없이)

- 코어 쪽(`view.scene`·`view.pick`·`geom.snap`·사이드카 프로토콜)은 지금처럼 pytest 와 패리티 C 로 본다.
- GUI 쪽 단위 시험은 vitest(MIT)로: 도구 상태 기계(점 두 번 → `wall.create` 인자), 조작 기록 형식, 액션 매핑. 캔버스 그리기는 PixiJS 를 가짜 렌더러로 바꿔 장면 그래프만 비교한다.
- 실제 창(Tauri·WebView2) 안의 종단 시험은 창이 필요하므로 이 단계에서 하지 않는다(S6 와 같은 한계). 필요하면 지휘자가 사용자에게 화면 시험 방법을 따로 정한다.

### 2.7 단계별 PR 분할안

| PR | 내용 | 코드 위치 | 완료 기준 |
|---|---|---|---|
| B0 | ADR-0016 확정, `gui_exempt.yaml`·`gui/actions.json` 형식, 패리티 G 검사기·조작 기록 재생기(GUI 없이 커맨드 목록 파일만) | `spikes/s1-core/tools/parity_g.py`, `tools/replay.py` | 가짜 actions.json 으로 검사기 시험, 재생 해시 시험 |
| B1 | 코어 조회 `view.scene`·`view.pick`·`geom.snap`(+ `selection.save`/`session.selection` 은 B3) | `dobim_core` | pytest·패리티 C·증분=전체, 큰 뷰 응답 쪽 나누기 |
| B2 | 앱 셸: Tauri 2 + Svelte 5 + PixiJS, 사이드카(`dobim gui-serve`), 평면 뷰 읽기 전용 표시·이동·확대 | `spikes/s12-gui/`(새 스파이크, 앱 패키지는 코어에 의존만) | vitest, import-linter(코어가 GUI 를 모름), 설치 크기 기록 |
| B3 | 선택(`view.pick`)·벽 그리기/옮기기·문·창 배치, 스냅, 조작 기록 | 같음 | 재생 해시 시험, 패리티 G 검사 통과 |
| B4 | 2D 작도 도구 전부·층 패널(`draft.*`) | 같음 | 같음 |
| B5(선택) | 시트 편집 화면, 3D 뷰(ifc-lite) | 같음 | 로드맵 2b |

B0·B1 은 GUI 없이 코어만 바꾸므로 지금의 시험 체계로 끝까지 검증된다. B2 부터 Node·Rust 빌드 체인이 들어온다.

### 2.8 B0·B1 구현 메모(2026-11-19, 브랜치 `phase1/gui-b0-b1`)

**파일**

| 파일 | 내용 |
|---|---|
| `spikes/s12-gui/gui/actions.json` | 형식 `dobim.gui.actions/1`. 동작마다 `command`(하나)·`commands`(여럿)·`exempt: true` 중 정확히 하나. `client_files` = 사이드카를 불러도 되는 커맨드 클라이언트 파일. 지금은 2.4절 MVP 동작 35개(커맨드 29, 제외 6) |
| `spikes/s12-gui/gui_exempt.yaml` | 제외 6개(2.5절 초안 그대로) + 선택 `queries`(그 동작이 부르는 조회). YAML 의존을 들이지 않으려고 작은 부분집합만 받는다(목록 안 `key: value`, 값은 문자열·`[a, b]`) |
| `spikes/s1-core/tools/parity_g.py` | 정적 검사. 실패 코드 `E_FORMAT`·`E_UNKNOWN_COMMAND`(없는 커맨드)·`E_EXEMPT_MISSING`(매핑 누락)·`E_NO_REASON`·`E_EXEMPT_WRITES`(제외 항목의 `queries` 가 조회가 아니거나 제외 id 가 쓰기 커맨드 이름)·`E_BYPASS`(`--src` 소스에서 클라이언트 밖 `invoke(`·`gui-serve`·`dispatch.run`·`sidecar(` 호출). 통과 0 / 실패 1 |
| `spikes/s1-core/tools/replay.py` | 조작 기록 `dobim.gui.record/1`(project.guid, steps, final = model.hash 4개)을 새 프로젝트에 **CLI 서브프로세스**로 재생해 rev·state·derived·ifc 를 비교한다. 단계 = `command`+`args` / `batch`(원자 묶음) / `exempt`(+`query`). 이어진 단계는 `dobim script --per-item` 한 번으로, 묶음은 `script --atomic`, 파괴적 커맨드는 확인 토큰 2단계로. 제외 단계의 조회는 앞뒤 해시가 같아야 한다(제외 동작 무해성, `R_EXEMPT`). `--actions` 를 주면 단계의 동작 ↔ 커맨드 매핑도 본다(`R_ACTION`). `Recorder` 는 같은 기록을 만드는 기록기(GUI 커맨드 클라이언트가 할 일을 dispatch 경로로 흉내) |
| `src/dobim_core/scene.py`, `commands_gui.py` | B1 조회 |

자동 id 는 프로젝트 UUID 와 로그 순번에서 결정적으로 나오므로(`engine.new_id`) 기록에 id 가 없어도 같은 guid 로 만든 새 프로젝트에서 같은 id 가 나온다. 실패한 커맨드는 상태를 바꾸지 않으므로 기록하지 않는다.

**`view.scene`** — `view_lines` 가 쓰는 요소별 질의(`plan_wall_lines`·`fam_lines`·`slab_lines` …)를 요소마다 따로 모아 요소 id 를 붙인다. 화면과 도면이 같은 원천이 되도록 요소 안은 `merge_lines` 로 정규화하고, 요소 사이에는 어느 요소의 절단선이 덮는 다른 요소의 낮은 등급 선을 지운다(`view_lines` 의 병합 규칙). 잘라내기(crop)도 같은 함수다. 그래서 등급·직선별로 덮는 구간이 `view_lines` 와 같다(시험: 평면·단면·입면·잘라낸 상세 평면, 끝점 차 0.05mm 이하). 다른 점은 요소 사이 같은 등급의 맞닿은 선을 잇지 않는다는 것뿐이다. 문·창(호스팅 패밀리)의 평면 기호는 벽 선분에서 빼서 그 요소의 것으로 돌린다(같은 함수로 다시 만든 선분과 정확히 같은 것만 뺀다). 단면·입면의 개구부 선은 벽의 것으로 남는다. 2D 작도(`draft`)·주석(`dim`·`tag`)은 그림 항목 그대로, 뷰가 계산해 붙이는 것(대지 뷰 전체·표지판·지반선·계단/승강기 치수)은 종류 `view`, id = 뷰 id 로 담는다.
응답 행 = `{id, kind, bb, lines?, fills?, texts?, items?, part?}`(행 하나 약 1.5KB, 선분이 많은 요소는 `part: [i, n]` 로 나눈다). 거르기: `bbox`(타일, 경계 상자가 겹치는 요소 통째로)·`ids`(바뀐 요소만)·`kinds`, `decimals`(기본 1). `summary` 는 거르기 전 전체 장면의 요소·선분 수·경계 상자·digest(쪽들이 같은 장면인지 맞춰 보기). 장면·행·요약은 그래프 노드라 바뀐 요소만 다시 계산된다(증분 = 전체 시험), 뷰를 지우면 `forget_views` 가 치운다.

**`view.pick`** — 점에서 허용 거리(기본 용지 3mm = 3 x 축척) 안의 요소를 가까운 순으로(같으면 절단선 > 채움 안 > 나머지 > 문자). 채움 다각형 안이면 거리 0. **`geom.snap`** — 끝점·중점·교점(근처 선분 200개 안에서)·벽 축선 끝·중점(평면)·수선의 발(`from` 필요)·직교(`from` 을 지나는 가로·세로) 후보를 가까운 순으로, `best` 는 첫 후보.

**측정**(합성 격자 평면, `tools/bench_gui_scene.py`) 결과는 `spikes/phase1-drawings/baseline.md` 32절.
