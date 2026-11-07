# 04. BimCraft 심층 조사 (소스 열람 기준)

- 기준일 2026-11-07 / 대상: https://github.com/getcadcraft/bimcraft (HEAD, 2026-11-02 push)
- 방법: `gh api`로 트리 전체(5,889개 파일)와 핵심 소스 20여 개를 읽음. **클론·실행·쓰기 동작 없음.** 따라서 "구현됨"은 *소스에 코드와 테스트가 있다*는 뜻이지 *돌려서 동작을 확인했다*는 뜻이 아님.
- 판정 용어: 구현됨(소스+테스트 확인) / 부분 / 데모 / 주장만(README·문서에만 있고 소스 못 찾음).
- 법률 자문 아님.

---

## 0. 한 줄 결론

**소스는 "README 주장만"이 아니라 실체가 있다. 다만 (a) 공개 저장소는 비공개 개발본의 일회성 스냅샷 미러라 외부인이 재현·기여하기 어렵고, (b) 3.5주 만에 30만 줄급이 생긴 AI 주도 개발 정황이 뚜렷하며, (c) CI가 현재 빨간불이고 문서·서버·일부 모듈이 저장소에 없다.** 설계 아이디어(IFC를 정본으로 쓰는 패밀리·호스팅·재도출 구조)는 가져올 가치가 크고, 코드 자체의 장기 의존은 위험하다.

---

## 1. 저장소 실측

| 항목 | 값 | 근거 |
|---|---|---|
| URL / star / fork / open issue | getcadcraft/bimcraft / 189 / 40 / 7 | `gh api repos/getcadcraft/bimcraft` |
| 생성일 | **2026-10-13** (조사일 기준 25일 전) | created_at |
| 커밋 수·기간 | **30개**, 2026-10-13 ~ 2026-11-02. 커밋 메시지는 "CadCraft Sync from development", "bimcraft 0.8" 등. 마지막 큰 푸시 "bimcraft 0.8"은 **4,326개 파일, +1,196,763줄**이 한 커밋 | commits API, Actions run 메타 |
| 기여자 | 4 계정: CadCraft 12, renanem 8, ddgconsultant 7, yisraelk 3 | contributors API. `CadCraft`=조직 계정이 동기화 커밋, 나머지는 외부 PR |
| 릴리스 | v0.4.0(10-13), v0.6.0(10-19), v0.7.0(10-25), v0.8.0(11-01), v0.8.1(11-02) → 약 5일마다 | releases API |
| 소유 | The Data Design Group, Inc. (NOTICE). "CadCraft 가족 8개 앱" 중 하나, 데스크톱 쉘은 별도 repo getcadcraft/studio(19★, Apache-2.0 표기) | NOTICE, CONTRIBUTING.md |
| 코드 규모 | 트리 blob 합 약 309MB / 5,889파일. `app/` 1,906파일(번역·도움말 JSON/JS가 대부분), 비번역 `app/*.js` 약 360개 + 하위 모듈. `tests/` 3,054파일, `fixtures/` 226파일(56MB, IFC 샘플 포함) | git tree |
| 언어 구성(바이트) | JavaScript 30.3MB, HTML 0.75MB, CSS 0.3MB, PHP 0.3MB, Python 0.28MB, Rust 20KB, C++ 10KB | languages API. JS 대부분은 번역 번들이므로 *실질 코드량*은 이보다 훨씬 작음(추정) |
| 최대 소스 | `app/bimcraft.js` 403KB, `host.js` 138KB, `model-ui.js` 125KB, `family-model.js` 65KB, 테스트 `test-bimcraft.mjs` 272KB(`ok(` 호출 568건) | tree sizes |

### 1.1 star 대비 활동 불일치와 AI 대량 작성 흔적 (판단)
- 25일에 189★/40 fork는 홍보 효과로 가능한 범위이나, **커밋 30개로 활동량을 판단할 수 없음**(스냅샷 미러). 실제 개발 이력·리뷰 과정은 비공개 저장소에 있음.
- 소스 주석에 `0.6 DC.114, session bim-families_PART_2`, `spike docs/spikes/bim-families_PART_2.md`, `(0.6 DF.16)`, `LV.18, final sweep` 같은 **에이전트 세션·태스크 ID**가 수없이 박혀 있음(family-model.js, live-client.js, model-write.js 헤더). 파일 헤더 주석이 수십 줄에 이르는 설계 서술 형식으로 일관됨. → **LLM 에이전트가 세션 단위로 대량 작성한 코드로 판단**(근거: 주석 문체와 ID 체계. 사용 도구는 확인 불가).
- `docs/` 디렉터리가 저장소에 **없음**. 그런데 소스는 `docs/STUDIO-LIVE-SPEC.md`, `docs/LIVE-PROTOCOL.md`, `docs/AGENTS-SPEC.md`, `docs/spikes/*`를 곳곳에서 참조. 설계 정본 문서가 공개 미러에서 빠져 있음.
- 증상으로 보이는 품질 문제: 이슈 #17 — `app-devices-ui.js`가 정적 import 하는 `app-devices-studio.js`가 어느 저장소에도 없어 **입력 장치 모듈 전체가 링크 실패**(다른 6개 앱도 공유). 이슈 #14 — 문서가 약속한 `@getcadcraft/mcp`가 npm 404이고 소스도 없음.
- **CI 현재 실패**: Actions 최근 push 실행 2건(2026-11-01 "bimcraft 0.8", 11-02 "Sync from development") 모두 `failure`. 로그: `test-bimcraft-fixtures.mjs` 1개 FAIL(7개 중 6개 통과). PR 실행은 `action_required`(외부 기여자 승인 대기). 즉 릴리스 태그 v0.8.1은 CI 적색 상태의 코드.

### 1.2 라이선스
- `LICENSE`: Apache-2.0 표준 원문 177줄(변형 없음). `NOTICE`: "Copyright 2026 The Data Design Group, Inc.", 상표 조항: CadCraft·앱 이름은 Apache 6조에 따라 사용권 없음, **포크는 개명 필수**(TRADEMARKS.md).
- 디렉터리별 예외: 별도 LICENSE 예외 파일은 못 찾음. 파일마다 `SPDX-License-Identifier: Apache-2.0` 헤더. 예외는 `app/usd/earcut.js`(ISC, Mapbox earcut 추종, SPDX에 병기).
- 번들 의존성(THIRD_PARTY.md 표 전수 확인): CoolProp(MIT), Delaunator(ISC), geotiff.js(MIT), laz-perf(Apache-2.0), **Manifold 3.5.4(Apache-2.0)**, noble-*(MIT), OIDN weights(Apache-2.0), PDF.js(Apache-2.0), Proj4js(MIT), RDKit(BSD-3), robust-predicates(Unlicense), three.js 계열(MIT), web-e57(Apache-2.0 AND MIT), Bootstrap(MIT), Archivo 폰트(OFL-1.1). **GPL/LGPL/MPL/AGPL 없음.**
- 테스트 전용 copyleft: `devshell/test.mjs`의 `--oracles`에서만 설치되는 web-ifc(MPL-2.0), occt-import-js(LGPL-2.1), libredwg-web(GPL-3.0). 앱에는 미포함, CI는 `--oracles`로 실행하므로 **개발·CI 환경에는 설치됨**(제품 배포물과 분리되어 있다는 주장은 PROVENANCE.md, 배포물 쪽 검증은 못 함).
- 위험 한 가지: PROVENANCE.md는 "copyleft 코드를 읽지 않았다/클린룸"이라 주장하지만, `app/ifc/api.js`는 web-ifc의 **IfcAPI와 호출 형태·값 모양(Handle, 토큰 타입 번호 5/3, `StreamAllMeshes` 등)을 그대로 맞춘 호환 계층**("switching an app is one import")임. API 모양 호환은 통상 저작권 침해로 보지 않으나(엔지니어링 판단), 흡수 시 법무가 한 번 볼 대목.
- 폐쇄 상용 제품에 코드 이식: **가능(Apache-2.0 고지+NOTICE 유지, 상표 제외)**. 이는 ConstructCAD(GPL-3.0)·Bonsai(GPL-3.0)와 결정적 차이.

---

## 2. 기능 실체 판정 (02-revit-gap-analysis.md 요구 표 기준)

번호는 02 문서 2절 표의 `#`.

| # | 기능 | 판정 | 근거 (파일/링크) | 비고 |
|---|---|---|---|---|
| 1 | 3D 요소 모델링 | **구현됨**(벽·슬래브·지붕·기둥·램프·그리드·레벨·방·현장) | `app/model-walls.js`, `model-slabs.js`, `model-roof.js`, `model-columns.js`, `model-ramps.js`, `model-grids.js` | 계단은 `res-stair.js`(28KB) 별도. 구조·철근은 `res-framing.js`, `res-foundation.js` |
| 2 | 호스팅·조인 | **구현됨** | 벽 L/T/X 조인: `model-walls.js` 헤더 "Joins" 절(노드에서 방향 정렬 후 인접 면 교차=마이터, 4배 두께 초과 마이터는 직각 절단, 자기교차 시 사각형 폴백). 문·창 호스팅: `model-openings.js`(IfcOpeningElement + IfcRelVoidsElement/FillsElement, 벽 이동·삭제 시 상대 배치로 같이 이동) | 실행 미확인. 벽 두께 변경 시 `regenOpenings(w, wall)` 호출 방식 — 자동 전파가 아니라 **명시적 재생성 함수** |
| 3 | 타입/인스턴스 | **구현됨** | WallType(레이어 구성), Pset_*Common, Qto_*BaseQuantities, `CadCraft_*` pset. `project-materials.js`, `wall-assemblies.js` | IFC 표준 구조에 매핑 |
| 4 | **양방향 연관성** | **부분** | 일람표·시트는 *요청 시 모델에서 다시 도출*: `res-sheets.js` 헤더 "made again from the model whenever it is asked for (so it always matches)", `model-schedule.js` "change with every edit". 치수·태그는 `comforts-views.js`, `dimension-geometry.js`. | **증분 의존성 그래프는 없음.** `regenWalls(w, storey, guids)`처럼 변경된 요소 + 조인 이웃만 다시 쓰는 *수동 호출형 부분 재생성*. 그 위의 뷰는 전체 재도출 방식이므로 대형 모델에서 비용은 미지수(미측정) |
| 5 | 2D 도면(평면·단면·입면) | **구현됨**(소스 기준) | 평면 `model-planview.js`(캔버스, 단면컷·언더레이·트레이스·크롭·위상·펜 오버라이드), 단면/입면 `res-views.js`, `section-volume.js`, 해치 `wall-section-graphics.js` | HLR 품질·성능은 미확인. 스크린샷 `screenshots/paper.png` 있음 |
| 6 | 시트·타이틀 블록·출력 | **구현됨**(소스) | `res-sheets.js`: `buildSet`, `composeSheet`(뷰를 임의 위치·축척에 배치), `setPdf`(벡터 PDF), `sheetSvg`, 단면선 IfcAnnotation 저장 | **PDF 주석에 "Helvetica, WinAnsi"** — 한글 등 CJK 출력 방법은 이 파일만으론 불명. `picture-pdf.js` 등 다른 경로가 있을 수 있어 **한글 시트 출력은 직접 확인 필요**(추정 리스크) |
| 7 | 주석(치수·태그·텍스트·인출선) | **부분~구현됨** | `comforts-views.js`, `dimension-geometry.js`, `drafting.js`(18KB), `keynotes.js`, 도면 해치/라인 | 객체 연관 치수(모델 변경 시 값 갱신)의 정확한 의미는 코드 미정독 → 미확인 |
| 8 | 일람표·수량 | **구현됨** | `model-schedule.js`(문·창·벽 타입별·방), `schedule-io.js`(17KB), `ifc-qto.js`(CSV), `res-takeoff-packs.js` | 필드 사용자 정의 일람표(Revit 수준)인지는 미확인 |
| 9 | IFC 입출력 | **구현됨**(자체 엔진) | `app/ifc/`: STEP 파서 `step.js`, IFC2X3/IFC4/IFC4X3 스키마(`schema-*.js` 합 450KB), 지오메트리 `geo-*.js`(압출·회전·스윕·NURBS·B-rep·불리언), web-ifc 호환 `api.js`, 큰 파일 스캔을 Worker로(`open-async.js`: "약 2초/200MB" 주장) | 테스트 `test-bimcraft-revit-ifc.mjs`가 Revit 내보내기 IFC 2종을 여는데 **공개 클론엔 샘플이 없어 skip**. 왕복 무손실 여부는 미확인 |
| 10 | DWG/DXF | **구현됨**(소스) | `app/dwg/` 25파일 425KB(ODA 사양 기반 자체 리더), `dwg-compile.js`, `dxf-model.js` | CI 로그에 "79 of 79 files opened" 출력 확인(DWG 코퍼스 오픈 테스트). 쓰기 품질 미확인 |
| 11 | **파라메트릭 패밀리** | **구현됨(구속 솔버는 없음)** | `family-model.js`(65KB): 레시피 JSON(`cadcraft-family`), 파라미터(length/angle/number/yesno)+**수식**, 솔리드 box/extrude/revolve/sweep + difference/union, 가시성 식, 중첩 패밀리(nested parts), MEP 커넥터. `family-editor.js`, `family-ops.js`, `family-symbol.js`(평면 심볼 상세도) | **저장 방식이 핵심**: 패밀리=`IfcGroup`(ObjectType "Family") + 크기별 `IfcDoorType` 등 + Pset `CadCraft_Family`에 레시피 JSON. 다른 프로그램은 평범한 IFC 타입으로 읽음. Revit식 *참조 평면·치수 구속·솔버*는 소스에서 못 찾음(수식/식 기반만) |
| 12 | 워크셰어링 | **부분(클라이언트만)** | `app/live-client.js`(63KB): MessagePack 직접 구현, WebSocket 핸드셰이크, **보류 큐 + 수신 커밋 위 rebase, 거부된 편집 롤백, 사용자별 undo/redo, holds, presence, 편집 로그(누가 마지막으로 바꿨나)**. `live-crypto.js`(암호화). 서버는 `tests/tools/mcp/live-server.mjs`(테스트용 로컬 MCP 서버)뿐 | 프로덕션 협업 서버·`docs/LIVE-PROTOCOL.md`는 **저장소에 없음**. Revit식 워크셋·요소 차용·중앙 모델 동기화 개념은 없고 *OT/CRDT 유사 실시간 공동 편집* 모델. 03 검증에서 grep으로 못 찾은 이유는 이름이 "Live"이기 때문. `app/vault/`(30KB+)는 리비전/문서 관리 |
| 13 | 구조 해석·철근 | **부분** | `res-framing.js`, `res-foundation.js`, `analysis-bim.js`, `app/frame/` 26파일 | 깊이는 미확인 |
| 14 | MEP | **부분~구현됨** | `mep-model.js`(67KB), `mep-ui.js`, `app/mep/` 25파일; 이슈 #8~#12(renanem PR: 커넥터, 브라질 PVC 표, 위생기구 단위 배수 크기산정) 병합됨; 테스트 connectors/drainage/pipes-br | 자동 라우팅·시스템 해석 수준은 미확인 |
| 15 | 간섭 확인 | **구현됨** | `clash.js`+`clash-worker.js`, `bcf.js`(BCF 2.1을 XSD로 검증하는 테스트), `ifc-checks.js`, `ids-ui.js`+`app/ids/` | `--clash`, `--bcf` 테스트 섹션 존재 |
| 16 | Phase/Option | **부분** | `phases-ui.js`, 평면 `scene.walls[].phase: existing/demolish/new` | Design Option은 못 찾음 |
| 17 | 링크 모델 | **구현됨** | `model-links.js`, federation 테스트(`--federation`), `geo-align.js`, `crs.js` | |
| 18 | API/스크립팅 | **부분** | 앱 내 어시스턴트(`agents.js`, `bim-agent.js`) 도구 API. 외부 사용 가능한 **헤드리스 MCP/CLI는 미공개**(이슈 #14, npm 404) | Dynamo 대응 비주얼 스크립팅은 못 찾음 |
| 19 | 대형 모델 | **주장+일부 근거** | Worker 스캔(`open-async.js`), `clash-worker.js`, three-mesh-bvh, `compute/bench*.js`. 스트리밍 메시 API | **실측 데이터 없음**. 웹/JS 한계(싱글스레드·2GB 힙)는 그대로. 수십만 객체 규모 검증은 미확인 |
| 21 | .rvt 읽기 | **데모** | `app/rvt-reader.js`(31KB): MS-CFB 컨테이너·정보/프로젝트정보·미리보기·링크만 읽고 **"모델 지오메트리는 읽지 않음"**(PROVENANCE.md) | 대체 근거 못 됨. `--partial` 테스트는 잘린 RVT 복구 |
| 23 | BCF 이슈 | **구현됨** | `bcf.js` + XSD 검증 테스트 | |

요약: 02 문서 MVP 필수 묶음(1·2·3·4·5·6·7·8·9·10) 중 **4번만 "부분"**이고 나머지는 소스상 구현. 이 정도 폭이 3.5주(공개 기준)에 나온 점은 비공개 개발 기간이 더 길었거나 에이전트 대량 생성이라는 두 설명 중 하나인데, 위 1.1의 정황은 후자를 가리킴.

---

## 3. 설계 분석

### 3.1 데이터 정본 = IFC 텍스트 그 자체 (가장 중요한 설계 선택)
- 앱이 별도 내부 모델 없이 **열린 IFC 파일(STEP 라인)을 직접 편집**한다. `ModelWriter`(model-write.js)는 `IfcPlacer`를 확장해 *편집 저널*(만든 라인 + 바뀐 라인의 최초 상태)을 쌓고, 한 도구 동작 = 하나의 undo 단위. 요소는 expressID가 아니라 **GlobalId**로 찾아서 redo가 안전.
- 요소는 **자기 소유 라인만** 쓴다(점·방향·프로파일 공유 금지) → 형상을 교체할 때 "누가 이걸 참조하나" 안 물어도 되고 삭제가 안전. 이 불변식이 부분 재생성을 가능하게 하는 열쇠로 보임.
- BimCraft 고유 정보(패밀리 레시피, 벽 파라미터 등)는 `CadCraft_*` Pset에 넣어, **다른 IFC 프로그램이 평범한 IFC로 열 수 있게** 함. Revit이 가진 "패밀리 vs IFC 표준 불일치" 문제를 *IFC 타입 + 레시피 pset*으로 우회한 설계(02 문서 11번의 "IFC에 패밀리 개념 없음" 쟁점에 대한 구체 해법 후보).

### 3.2 형상 커널
- **B-rep 커널 아님.** 자체 `cc-ifc`(app/ifc/geo-*.js)가 압출·회전·스윕·NURBS를 *메시로 테셀레이션*하고, 불리언(개구부·IfcBooleanResult·하프스페이스)은 **Manifold(메시 불리언)**로 처리. 닫힌 매니폴드가 아니면 평면 클리핑 폴백, 그래도 안 되면 "빼지 못하고 노트"(geo-bool.js 헤더). 즉 *정밀 B-rep 연산(필렛·오프셋·정확한 교차)은 기대 불가*, 건축 요소 수준 불리언에는 충분.
- 문서화 도면의 단면 해치·HLR은 메시 기반일 가능성(추정; 코드 미정독).

### 3.3 의존성 재생성 방식
- 증분 의존성 그래프(dirty propagation) 방식이 **아님**. 패턴은 (1) 도구가 변경 요소와 조인 이웃에 대해 `regenWalls/regenOpenings` 같은 **명시적 재생성** 호출, (2) 뷰·일람표·시트는 필요할 때 모델에서 **전체 재도출**.
- 장점: 단순·디버깅 쉬움·IFC가 항상 정본. 단점: 파생 데이터(치수, 태그, 뷰 캐시)가 늘면 호출 누락이 버그가 되고, 대형 모델에서 재도출 비용 증가. 02 문서의 "난이도 5" 항목(4번)을 *해결*한 것이 아니라 *회피*한 구조라고 판단.

### 3.4 테스트
- `tests/bin/test-bimcraft.mjs` 하나가 272KB·`ok(` 568건, 옵션 섹션 수십 개(`--edit --walls --clash --bcf --section --qto --compare --partial` 등) + 보조 6개 파일. 테스트 인프라 `tests/tools/*`(2,187파일; library 1,102, cabinetcraft 191, mcp 76 …)에 다른 앱의 것이 혼재(8앱 공유 저장소 구조).
- 품질 한계: (a) 테스트가 비공개 저장소 경로(`public_html/assets/apps/bimcraft/dev`, `/var/www/CADCRAFT/testfiles/samples`)를 가정하고 `devshell/test.mjs`가 `.cc-test/`에 그 레이아웃을 *복제해서* 돌림(어댑터가 있어야 도는 구조), (b) 공개 샘플이 없으면 다수 검사가 skip(CI 로그에 "public samples are not in this checkout"), (c) **현재 CI 적색**. 테스트는 많지만 "기능 정확성 보증"으로 곧바로 믿을 수준은 아님.

### 3.5 대형 모델 대응
- 있는 것: STEP 스캔 Worker, 메시 스트리밍, BVH, clash 워커, 벤치 코드(`compute/bench*.js`). 없는 것: 인스턴싱/LOD/공간 인덱스 기반 증분 렌더의 *검증된 수치*. 공개 fixture 중 가장 큰 IFC는 약 6MB(`bimcraft-piping-retrofit.ifc`) — 02 문서가 말하는 수십~수백 MB 규모 근거 없음.

---

## 4. 가져올 수 있는 것 (ConstructCAD·Bonsai 비교)

| 관점 | BimCraft | ConstructCAD | Bonsai/IfcOpenShell |
|---|---|---|---|
| 라이선스 | **Apache-2.0** (코드 재사용 가능, 상표 제외) | GPL-3.0 (이식 불가) | GPL-3.0(Bonsai) / LGPL-3.0(코어) |
| 정본 포맷 | IFC 텍스트 직접 편집 | 자체 `.ccad`(RON) + IFC 변환 | IFC |
| 패밀리 | 레시피(수식·솔리드·불리언·커넥터·중첩) → IFC 타입+pset | 표현식 컴포넌트 `.ccomp`(README 주장) | IFC 타입 + Bonsai 전용 파라메트릭 |
| 시트/일람표 | 소스 확인(PDF/SVG) | README+스크린샷만 확인(코드 미열람) | SVG 기반 drawing 모듈 |
| 워크셰어링 | 실시간 공동편집 클라이언트(서버 없음) | 없음(추정) | IfcGit 실험 |
| 규모/성숙도 | 큼, 신생, AI 정황, 스냅샷 미러 | 작음, 신생, 기여자 3 | 큼, 성숙, 기여자 288 |
| 포크 현실성 | 낮음(스냅샷, docs·서버 누락, 상표, 8앱 얽힘) | 낮음(GPL) | 높음 |

### 4.1 항목별 가져오기 판단
1. **설계 참고(최우선)**: IFC 직접 편집 + GlobalId 저널 undo(model-write.js), "자기 소유 라인" 불변식, 패밀리=IfcGroup+IfcXxxType+Pset 레시피, 벽 조인(노드 정렬 마이터 + 폴백), `regen*` 부분 재생성, 시트=모델에서 on-demand 재도출, Pset 이름 규약. → **새로 만들 시스템에 그대로 쓸 만한 아이디어이며, 아키텍트가 02 문서 11번(패밀리) 설계에서 우선 검토할 가치**.
2. **코드 재사용(조건부 가능)**: Apache-2.0이므로 `family-model.js`, `model-walls.js`(조인 알고리즘), `res-sheets.js`, `ifc/` 일부(STEP 파서·스키마 테이블)를 *발췌 이식*할 수 있음. 단 (a) 코드가 BimCraft 내부 전제(`./i18n.js`, `ModelWriter`, 세션 ID 주석, 8앱 공유 shell)에 강하게 묶여 있어 발췌 비용이 큼, (b) AI 대량 생성 코드는 *숨은 불변식·테스트 공백*이 있을 수 있어 검증 비용이 큼, (c) 고지·NOTICE 유지 의무.
3. **기여·포크 대상으로는 비추천**: 공개본은 단방향 스냅샷 미러("Sync from development")이고 외부 PR이 어떻게 비공개 본선에 반영되는지 불명(외부 PR 2건 `action_required` 대기, 이슈 6건 열림). 포크하면 상표 때문에 개명, 업스트림 추적 불가, docs·서버·`app-devices-studio.js` 같은 누락 파일을 직접 메워야 함.
4. **라이브러리 링크 형태**: 불가능에 가깝다(npm 패키지 아님, ES 모듈 묶음, `package.json` private). `cc-ifc`가 "standalone library"를 표방하나 별도 배포 단위로 분리돼 있지 않음.
5. **벤더 의존 위험**: 사실상 단일 회사(Data Design Group)가 8개 앱을 AI 주도로 빠르게 찍어내는 구조. 방향 전환·상업화(라이선스 변경은 이미 받은 버전에 소급 안 됨, 그러나 업데이트는 끊길 수 있음) 시 유지보수 인력이 없다.

### 4.2 02 문서 요구 표와의 정합
- MVP 묶음(1·2·3·4제한·5·6·7·8·9·10)을 BimCraft가 *소스 수준으로* 이미 덮고 있어, **"MVP 범위를 자체 구현했을 때의 설계 참조 구현"으로서 가치가 가장 큼**(ConstructCAD는 GPL이라 참고만, Bonsai는 IFC 전용 호스트 종속).
- 11번(패밀리)·12번(워크셰어링)은 MVP 제외 권고였는데, BimCraft는 *패밀리는 있고(구속 없음), 워크셰어링은 클라이언트만* 있음. 확장 여지를 데이터 모델에 남기는 방법으로 "Pset 레시피 + 사이즈 타입" 구조를 참고할 만함.
- 한국 납품 관점 미확인: `app/lang/ko.json`(613KB)은 존재하나 한글 PDF 시트·한국 도면 표준·세움터 IFC 적합성은 확인 못 함.

---

## 5. 미확인 항목 (실행·정독 불가로 남는 것)

1. **어떤 기능도 실행해 보지 못함.** 특히 시트/일람표/패밀리 편집 UI가 실제로 사용 가능한지, 크래시 없이 도는지 미확인. CI가 적색인 것만 사실.
2. 실패한 CI 테스트(`test-bimcraft-fixtures.mjs`)의 구체 원인(코드 결함 vs 환경/픽스처 불일치)은 로그 끝부분만 봐서 불명.
3. 4번(양방향 연관성)에서 치수·태그가 *모델 변경에 따라 값·위치가 갱신되는지*의 정확한 동작은 `comforts-views.js`를 정독하지 않아 미확인.
4. 한글(CJK) PDF/시트 출력, DWG *쓰기* 품질, IFC 왕복 무손실, Revit IFC 가져오기 품질(샘플이 공개 클론에 없음).
5. 대형 모델 성능 실측(공개 fixture는 최대 6MB).
6. 워크셰어링 프로덕션 서버의 존재 여부·라이선스(저장소 밖).
7. AI 생성 비율·도구·코드 리뷰 여부는 정황 판단일 뿐, 단정 아님. 저자(Data Design Group)의 개발 방식은 공개 자료에서 확인하지 못함.
8. 번들 라이선스 전수 검사는 THIRD_PARTY.md 표와 LICENSE 원문 기준. 테스트 폴더(`tests/tools/*`)와 fixtures의 개별 파일 라이선스(DWG/IFC 샘플 출처)는 미확인.
9. star 189의 구성(홍보/실사용)은 확인 불가.

---

## 6. 출처

- 저장소 메타·커밋·릴리스·이슈·Actions: `gh api repos/getcadcraft/bimcraft[/commits|/releases|/issues|/actions/runs]` (2026-11-07).
- 트리: `gh api repos/getcadcraft/bimcraft/git/trees/HEAD?recursive=1`.
- 소스 열람: `app/family-model.js`, `family-ops.js`, `family-editor.js`, `model-walls.js`, `model-openings.js`, `model-write.js`, `model-schedule.js`, `model-planview.js`, `res-sheets.js`, `live-client.js`, `host.js`, `ifc/api.js`, `ifc/geo-bool.js`, `ifc/open-async.js`, `tests/bin/test-bimcraft.mjs`, `tests/bin/test-bimcraft-revit-ifc.mjs`, `tests/tools/mcp/live-server.mjs`, `devshell/test.mjs`, `.github/workflows/test.yml`, `LICENSE`, `NOTICE`, `THIRD_PARTY.md`, `PROVENANCE.md`, `CONTRIBUTING.md`, `BUILDING.md`.
- 이슈: https://github.com/getcadcraft/bimcraft/issues/14 (헤드리스 MCP 미공개), /issues/17 (누락 파일로 입력 장치 모듈 링크 실패), /issues/18, /issues/16.
- 01-oss-candidates.md, 02-revit-gap-analysis.md(요구 표), 03 검증 전달 사항(공개본=미러, 커밋 약 30개).
