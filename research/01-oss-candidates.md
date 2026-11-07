# 01. Revit 대체용 오픈소스 BIM 후보 조사

- 기준일: 2026-11-07
- 수치 출처: `gh api repos/<owner>/<repo>` 실측(star·fork·push일·SPDX·언어·릴리스), `contributors` 헤더 카운트(anon 포함). 기억 기반 수치는 쓰지 않았고, 확인 못 한 값은 "미확인"으로 표기.
- 라이선스 판단은 **엔지니어링 관점의 추정이며 법률 자문이 아님**. 실제 채택 전 법무 검토 필수.

---

## 1. 요약

**결론: "Revit 전체를 그대로 대체하는" 완성형 오픈소스는 없다. 다만 조각별로는 매우 성숙한 부품이 있고, 조합하면 상당 부분(IFC 중심 저작·도면·일람표)을 대체할 수 있다.**

| 범주 | 최강 후보 | 이유(실측) |
|---|---|---|
| 완성형 저작 도구 | **Bonsai (IfcOpenShell 모노레포)** / FreeCAD BIM | Bonsai: IFC가 곧 문서(네이티브 IFC), drawing·qto·clash·bcf·sequence·cost 모듈 보유, 릴리스 2026-11-05. FreeCAD: 35k star, 기여자 1,103, 안정 릴리스 1.1.4 |
| IFC 라이브러리 | **IfcOpenShell** | 기여자 288, 2026-11-06 push, 지오메트리·API·IDS(tester)·diff·clash·MCP까지 한 저장소 (LGPL-3.0) |
| 웹 플랫폼/뷰어 | **That Open Engine** (web-ifc + components + fragment) / **ifc-lite** | 둘 다 2026-10~11 활발. ifc-lite는 Rust→WASM+WebGPU, IFC5(IFCX) 지원 주장 |
| 형상 커널 | **OCCT** (+ **Manifold** 보조) | OCCT V8.0.1 (2026-07), Manifold v3.5.4 (Apache-2.0). Rust 순수 커널 truck(Apache-2.0)은 유망하나 BIM 규모 검증 부족 |
| 협업·데이터 | **Speckle** (server는 사실상 Apache-2.0 + EE 디렉터리 예외) / buildingSMART IDS | specklepy Apache-2.0 |
| 신흥(2025~26) | pascalorg/editor, ConstructCAD, BimCraft, ifc-lite | **주장 대비 검증 필요**. 기여자 3~4명 수준 프로젝트의 README 기능 목록은 미검증 |

**대체 가능성 판단(조사 의견)**
1. IFC를 정본 포맷으로 삼는 소규모~중규모 건축 프로젝트: Bonsai 또는 FreeCAD로 **현재도 실사용 가능**. 단 UX 완성도·패밀리(파라메트릭 컴포넌트) 편집성·워크셰어링은 Revit에 못 미침.
2. "처음부터 새로 만든다"면 가져올 가치가 큰 것은 (a) IfcOpenShell의 API·지오메트리 설계, (b) That Open/ifc-lite의 웹 렌더·파싱 스택, (c) OCCT/Manifold 커널, (d) IDS·BCF 같은 표준 검증/이슈 흐름. 저작 UI 레이어(파라메트릭 패밀리·뷰/시트 모델)가 가장 비어 있는 부분이며, ConstructCAD/BimCraft가 정확히 그 부분을 시도하고 있으나 아직 1인~소수 프로젝트.
3. Revit `.rvt` 직접 읽기는 법적·기술적 위험 영역. rvt-rs(Apache-2.0, 클린룸 주장)가 있으나 지원 범위가 Revit 버전별로 제한적이라고 스스로 밝힘. 현실 경로는 "Revit → IFC export → 가져오기".

---

## 2. 범주별 비교표

(★=GitHub star, push=최근 push일, 기여자=contributors API 헤더 기준, 릴리스=latest release)

### 2.1 완성형 저작 도구

| 후보 | ★ | push | SPDX | 언어 | 기여자 | 최근 릴리스 | 한 줄 |
|---|---|---|---|---|---|---|---|
| FreeCAD (https://github.com/FreeCAD/FreeCAD) | 35,193 | 2026-11-06 | LGPL-2.1 | C++ | 1,103 | 1.1.4 (2026-09-28) | 파라메트릭 3D + BIM 워크벤치 + TechDraw |
| Bonsai (IfcOpenShell/src/bonsai) | (모노레포 2,935) | 2026-11-06 | GPL-3.0 (파일 헤더 확인) | Python | 288(모노레포) | bonsai-0.9.1-alpha (2026-11-05) | Blender 애드온, 네이티브 IFC 저작 |
| ConstructCAD (https://github.com/francisreboucas/constructcad) | 177 | 2026-11-07 | GPL-3.0 | Rust | 3 | v0.9.1 (2026-11-04) | Rust/egui/wgpu 저작도구, Revit식 UI·시트·일람표 주장 |
| BimCraft (https://github.com/getcadcraft/bimcraft) | 189 | 2026-11-02 | Apache-2.0 | JavaScript | 4 | v0.8.1 (2026-11-02) | 브라우저/데스크톱 BIM 저작, 자체 IFC·DWG 엔진 |
| Pascal Editor (https://github.com/pascalorg/editor) | 25,370 | 2026-11-06 | MIT | TypeScript | 56 | cli 1.0.6 (2026-10-23) | WebGPU 3D 건축 에디터 + MCP/AI 에이전트. IFC 중심 아님 |
| IngeTrazo (https://github.com/ingelibre/ingetrazo) | 1,437 | 2026-11-06 | GPL-3.0 | Python | 51 | v0.6.0 (2026-11-01) | SketchUp식 push/pull, BIM 태깅·IFC 수량은 선택 레이어 |
| bimrocket (https://github.com/bimrocket/bimrocket) | 105 | 2026-11-01 | NOASSERTION | JavaScript | 10 | 없음 | 웹 BIM 프로젝트 관리/편집 |

### 2.2 IFC 라이브러리/엔진

| 후보 | ★ | push | SPDX | 언어 | 기여자 | 릴리스 |
|---|---|---|---|---|---|---|
| IfcOpenShell (https://github.com/IfcOpenShell/IfcOpenShell) | 2,935 | 2026-11-06 | LGPL-3.0 (COPYING.LESSER) | C++/Python | 288 | 2026-11-05 |
| web-ifc (https://github.com/ThatOpen/engine_web-ifc) | 1,092 | 2026-11-06 | MPL-2.0 | TS/C++(WASM) | 58 | 0.78 (2026-09-21) |
| ifc-lite (https://github.com/LTplus-AG/ifc-lite) | 461 | 2026-11-07 | MPL-2.0 | TS/Rust | 38 | cli 0.45.1 (2026-11-03) |
| IfcPlusPlus (https://github.com/ifcquery/ifcplusplus) | 664 | 2026-10-26 | MIT | C++ | 31 | 2.5 (2024-08-09) |
| Xbim Essentials (https://github.com/xBimTeam/XbimEssentials) | 582 | 2026-08-28 | CDDL (LICENCE.md 확인; API는 NOASSERTION) | C# | 50 | 6.0.517 (2025-04-18) |
| topologicpy (wassimj/topologicpy) | 280 | 2026-11-06 | LGPL-3.0 | Python | 13 | v0.9.88 |

### 2.3 웹 플랫폼/뷰어/서버

| 후보 | ★ | push | SPDX | 비고 |
|---|---|---|---|---|
| That Open engine_components (https://github.com/ThatOpen/engine_components) | 731 | 2026-10-22 | MIT | v3.6.0, 기여자 40 |
| That Open engine_fragment | 229 | 2026-10-22 | MIT | v3.6.0, 대형 모델용 fragment 포맷 |
| xeokit-sdk (https://github.com/xeokit/xeokit-sdk) | 949 | 2026-11-02 | AGPL-3.0 | 상용 폐쇄 제품엔 별도 상용 라이선스 필요 |
| BIMserver (https://github.com/opensourceBIM/BIMserver) | 1,769 | 2026-03-13 | AGPL-3.0 | 릴리스 2025-07, 개발 정체 |
| BIMsurfer (opensourceBIM/BIMsurfer) | 432 | 2025-12-30 | MIT | 뷰어, 저활동 |
| bldrs-ai/Share | 193 | 2026-11-07 | AGPL-3.0 | 웹 IFC 공유 뷰어 (conway 엔진도 AGPL) |

### 2.4 형상 커널

| 후보 | ★ | push | SPDX | 언어 | 비고 |
|---|---|---|---|---|---|
| OCCT (https://github.com/Open-Cascade-SAS/OCCT) | 3,133 | 2026-10-20 | LGPL-2.1 + OCCT exception | C++ | V8.0.1 (2026-07-30), 기여자 221 |
| Manifold (https://github.com/elalish/manifold) | 2,395 | 2026-11-07 | Apache-2.0 | C++ | v3.5.4, 불리언 견고성 |
| truck (https://github.com/ricosjp/truck) | 1,682 | 2026-11-02 | Apache-2.0 | Rust | B-rep 순수 Rust 커널 |
| build123d | 3,644 | 2026-11-05 | Apache-2.0 | Python | OCCT 래퍼, v0.13.0 |
| CadQuery | 6,072 | 2026-11-06 | LICENSE 본문 LGPL 계열(API NOASSERTION; 라이선스 파일 확인 필요) | Python | OCCT 래퍼 |
| Chili3D (https://github.com/xiangechen/chili3d) | 4,970 | 2026-11-02 | AGPL-3.0 | TS | OCCT→WASM 브라우저 CAD, AI 어시스턴트 내장 |
| pythonocc-core | 1,996 | 2026-11-03 | LGPL-3.0 | SWIG | OCCT 파이썬 바인딩 |

### 2.5 협업·버전관리·데이터 표준

| 후보 | ★ | push | SPDX | 비고 |
|---|---|---|---|---|
| Speckle server (https://github.com/specklesystems/speckle-server) | 860 | 2026-11-06 | NOASSERTION → LICENSE 확인: Apache-2.0, 단 `workspaces/`·`gatekeeper/` 모듈은 Enterprise 라이선스 | 릴리스 2.31.14 |
| specklepy | 140 | 2026-11-06 | Apache-2.0 | Python SDK |
| speckle-sharp-connectors | 70 | 2026-11-06 | Apache-2.0 | 구 speckle-sharp(아카이브)의 후속 |
| buildingSMART IDS | 331 | 2026-09-25 | NOASSERTION (표준 문서: buildingSMART 저작권 표기) | 정보요구 사양 표준. 구현체는 IfcOpenShell `ifctester` |
| IFC5-development | 240 | 2026-09-08 | 라이선스 필드 없음 | 차세대 IFC 개발 저장소 |

### 2.6 파라메트릭/스크립팅, 도면, 물량

| 후보 | ★ | push | SPDX | 비고 |
|---|---|---|---|---|
| Dynamo (https://github.com/DynamoDS/Dynamo) | 2,051 | 2026-11-06 | Apache-2.0 | 비주얼 프로그래밍. Revit 연동 부분(DynamoRevit)은 Autodesk 종속 |
| Sverchok (nortikin/sverchok) | 2,554 | 2026-11-06 | GPL-3.0 | Blender 노드 파라메트릭 |
| Hypar Elements (hypar-io/Elements) | 422 | 2026-11-04 | MIT | .NET 건축 요소 생성 라이브러리 |
| OpenConstructionERP | 1,294 | 2026-11-06 | AGPL-3.0 | BOQ·물량·견적(ERP 범위, 저작 아님) |
| IfcOpenShell `ifc5d`/`ifccsv`/`ifcdiff`/`ifcclash` | 모노레포 | 2026-11-06 | LGPL-3.0 | 물량·표·diff·간섭 (src/ 디렉터리 목록으로 확인) |

---

## 3. 후보 상세 (상위만)

### 3.1 Bonsai + IfcOpenShell (최우선 흡수 대상)
- 저장소: https://github.com/IfcOpenShell/IfcOpenShell , Bonsai는 `src/bonsai`.
- **강점(근거: 저장소 디렉터리 구조)**: `src/` 아래에 ifcopenshell-python, ifcgeom, ifcparse, ifcconvert, ifcdiff, ifcclash, ifctester, ifccsv, ifc4d, ifc5d, ifcpatch, ifcquery, ifcmcp(MCP 서버, 같은 README "AI 코딩 도구 보조로 생성" 표기), ifcviewer-web, pyodide 등. Bonsai 모듈에는 `drawing, qto, clash, bcf, sequence, cost, structural, georeference, model, pset, tester, diff, ifcgit`. 즉 도면·물량·4D/5D·BCF·IDS 검증·git 버전관리까지 IFC 위에서 이미 연결돼 있음.
- **한계**: 호스트가 Blender(UX가 건축가 관행과 다름), Bonsai는 GPL-3.0 애드온, 릴리스가 alpha 라벨(`bonsai-0.9.1-alpha2611051029`). 이슈 2,130건(open_issues_count)으로 품질 부채 큼.
- **흡수 방식**:
  - IfcOpenShell 코어(지오메트리·파서·Python API): **라이브러리 링크 가능**(LGPL-3.0, 동적 링크/교체 가능성 보장 조건).
  - Bonsai UI/모듈 코드: GPL-3.0이라 **설계 참고만**(폐쇄 제품에 코드 이식 불가).
  - ifcdiff·ifcclash·ifctester·ifcpatch: 서브프로세스(CLI)/라이브러리로 호출 가능.
- 라이선스: 코어 파일 헤더에서 LGPL 문구 확인. Bonsai 파일 헤더는 GPL v3 문구 확인.

### 3.2 FreeCAD BIM (Arch/BIM 워크벤치)
- https://github.com/FreeCAD/FreeCAD , `src/Mod/BIM`.
- **강점(근거: 디렉터리)**: ArchWall/Window/Stairs/Roof/Structure/Rebar/CurtainWall/Space/Schedule/SectionPlane, `nativeifc`(IFC 네이티브), `importers`, TechDraw 모듈로 2D 도면 생성, Spreadsheet, OCCT 기반 파라메트릭 모델링, Python 스크립팅.
- **한계**: 이슈 4,053건. 위상 명명 문제·대형 BIM 모델 성능은 FreeCAD 커뮤니티의 공지된 약점(이슈 근거는 본 조사에서 개별 링크까지는 확인하지 못함 — 추정).
- **흡수 방식**: 전체 앱을 서브프로세스(헤드리스 `FreeCADCmd`)로 쓰거나 Arch 모듈 설계 참고. LGPL-2.1+ 이므로 라이브러리 링크는 가능하나 C++ 앱 전체를 임베드하는 건 비현실적.

### 3.3 That Open Engine (web-ifc / components / fragment)
- 활동 양호: components v3.6.0·fragment v3.6.0 (2026-10-22), web-ifc 0.78 (2026-09-21).
- web-ifc: MPL-2.0 → **파일 단위 카피레프트**. 수정한 web-ifc 소스만 공개하면 폐쇄 앱에서 링크 가능. components/fragment는 MIT.
- 한계: 읽기·뷰·쿼리 중심, 저작(파라메트릭 모델링)은 약함. 구 `web-ifc-viewer`(2023-09 이후 push 없음)는 제외.

### 3.4 ifc-lite (LTplus-AG)
- https://github.com/LTplus-AG/ifc-lite — "Rust core → WASM, WebGPU 렌더러, 모듈식 npm 패키지", IFC2X3/IFC4/IFC4X3/IFC5(IFCX) 지원, 지오메트리를 IfcOpenShell과 "공개 벤치마크 코퍼스에서 요소별 검증"했다고 README에 주장(미검증, 저자 주장).
- 라이선스 MPL-2.0. 2026-11-07 push, 기여자 38. 신흥이지만 활동 매우 높음. **웹 기반 신규 제품의 IFC 입출력 후보 1순위**(검증 후).

### 3.5 ConstructCAD (신흥, 기능 주장 광범위)
- https://github.com/francisreboucas/constructcad — GPL-3.0, Rust, 기여자 3, 177★, v0.9.1.
- README 기능 목록(저자 주장): 프로젝트 브라우저(평면·천장·3D·입면·단면·범례·일람표·시트), 벽 자동 조인·문/창 호스팅, 구조·MEP·계단·난간, 파라메트릭 컴포넌트(표현식 기반 `.ccomp`), 뷰 템플릿/필터/가시성 오버라이드, 공사 단계(phases), 일람표(CSV 내보내기), **시트 A0~A4 + 벡터 PDF/SVG 내보내기**, **자체 IFC4 export/IFC2x3·IFC4 import**, DWG/DXF 언더레이, DXF 내보내기, SketchUp 가져오기.
- **요구 분석 쪽 질문에 대한 답변(시트·일람표 실제 동작 여부)**: README와 스크린샷(`docs/screenshots/en/sheet.png`, `schedule.png`)은 **동작한다고 보여주지만, 본 조사는 코드를 실행하지 않았으므로 "동작 확인됨"이 아니라 "저자 주장 + 스크린샷 존재"까지만 확인**. 근거 파일: https://github.com/francisreboucas/constructcad (README의 Features 절, `examples/showcase.ccad`).
- **리스크**: 기여자 3명으로 범위가 매우 넓음(AI 보조 생성 가능성은 추정이며 확인 못 함). 실제 모델링 품질은 직접 빌드·시험 전 신뢰 불가.
- **흡수 방식**: GPL-3.0 → 코드 이식 불가, **설계 참고**(뷰=모델 쿼리 구조, 표현식 파라메트릭 컴포넌트, 뷰 템플릿·필터 모델).

### 3.6 BimCraft (CadCraft 계열)
- https://github.com/getcadcraft/bimcraft — Apache-2.0, JS, 기여자 4, 189★, v0.8.1 (2026-11-02).
- README: "파라메트릭 벽·문·창(레벨·그리드), 하나의 모델에서 뷰·일람표·시트·간섭 체크". 브라우저판 v0.7 + 데스크톱(CadCraft Studio).
- PROVENANCE.md: 자체 IFC 엔진(cc-ifc, IFC2X3/IFC4/IFC4X3), 자체 DWG 리더(클린룸, ODA 사양 5.4.1 기준), BCF, **Revit 파일은 컨테이너(MS-CFB)·메타·미리보기만 읽고 "모델 지오메트리는 읽지 않음"**, GPL/LGPL/MPL 도구는 테스트 오라클로만 사용(소스 미열람)이라고 명시.
- **시트·일람표 동작 여부(요구 분석 쪽 질문)**: README 스크린샷 `screenshots/paper.png`(페이퍼 스페이스)와 `tests/`, `fixtures/` 디렉터리 존재까지 확인. 실행 검증은 안 함 → "주장 + 테스트 코드 존재".
- **Apache-2.0라서 폐쇄 제품에 코드 흡수가 가장 쉬운 신흥 BIM 저작 후보**. 단 상표 정책(포크 시 이름 변경). 규모가 작아(4명) 장기 지속성은 불확실.

### 3.7 Pascal Editor
- 25k★, MIT, 기여자 56, TS(React Three Fiber/WebGPU). 로컬 CLI+MCP 서버, Claude/Codex 플러그인 제공.
- README에서 IFC·Revit 언급은 확인 못 함(전체 README에서 `ifc` 문자열 검색 시 파이프라인 목록의 `ifc-converter` 한 곳). BIM 정밀 저작이라기보다 **AI 에이전트 친화적 3D 건축 에디터**. 흡수 가치: MCP 도구 설계, 에이전트 워크플로. MIT라 코드 재사용 쉬움. star는 홍보 효과 가능성(추정).

### 3.8 Speckle
- 서버: LICENSE는 Apache-2.0 본문 + `workspaces/`, `gatekeeper/` 디렉터리만 Enterprise 라이선스(LICENSE 첫 부분 확인). GitHub SPDX가 NOASSERTION인 이유로 추정.
- 흡수 방식: 데이터 허브/버전 객체 모델 개념, specklepy(Apache-2.0)로 연동. 자체 호스팅 가능하나 운영 부담 있음.

### 3.9 형상 커널 (OCCT / Manifold / truck)
- OCCT: 사실상 유일한 성숙 오픈 B-rep 커널. LGPL-2.1 + 예외 → 동적 링크 상용 사용 가능.
- Manifold: 메시 불리언 전용(B-rep 아님), Apache-2.0 — 벽 개구부 컷 같은 메시 불리언에 적합.
- truck: Rust B-rep, Apache-2.0, 1.7k★. BIM급 필렛/오프셋/불리언 견고성은 미검증.

### 3.10 기타 참고
- **rvt-rs** (DrunkOnJava/rvt-rs, Apache-2.0, 103★, Rust, v0.15.0 2026-11-05, 기여자 10): Revit 파일을 Revit 없이 읽어 IFC4/glTF/SVG/CSV로 내보낸다고 주장하나 "지원 Revit 릴리스·파일 종류·기능에 따라 다름"이라고 README에 명시. 클린룸 주장은 저자 주장. 법적 리스크(리버스 엔지니어링 약관)는 별도 검토 필요.
- **Autodesk/revit-ifc** (653★, 기여자 18, 2026-10-30 push, 라이선스 필드 없음): Revit용 IFC 내보내기 애드인. 대체 도구가 받을 IFC 출력 품질의 기준점. 코드는 Revit API 종속이라 흡수 대상 아님.
- **OpenAEC-Foundation/ifccad** (19★, MPL-2.0), **BIMvoice/ifc-sheet** (28★, MPL-2.0), **louistrue/ifc5cad**(36★, AGPL-3.0): 소규모 실험, 후속 관찰 대상.

---

## 4. 라이선스 호환성 (엔지니어링 판단, 법률 자문 아님)

가정: 대체 제품은 **폐쇄 상용 소스**, 배포 형태는 데스크톱/웹.

| 후보 | SPDX | 폐쇄 제품에서 사용 | 전염 조건 요점 |
|---|---|---|---|
| IfcOpenShell 코어 | LGPL-3.0 | 링크 가능 | 라이브러리 수정분 공개, 사용자가 라이브러리를 교체할 수 있어야 함(동적 링크 권장). LGPL-3은 설치 정보 제공 조항 주의 |
| Bonsai | GPL-3.0 | **코드 이식 불가** (설계 참고만) | 결합/파생물 전체 GPL |
| FreeCAD | LGPL-2.1 | 링크 가능(실질적으로 앱 임베드는 어려움) | 라이브러리 수정분 공개 |
| OCCT | LGPL-2.1 + OCCT exception | 링크 가능 | 예외 조항으로 일부 완화 |
| web-ifc, ifc-lite | MPL-2.0 | 링크 가능 | 파일 단위: 수정한 MPL 파일만 공개 |
| That Open components/fragment | MIT | 자유 | 고지 유지 |
| Manifold, truck, build123d, specklepy, BimCraft, rvt-rs, Dynamo | Apache-2.0 | 자유 | 고지·NOTICE 유지, 특허 조항 |
| Pascal Editor, IfcPlusPlus, BIMsurfer, Hypar Elements | MIT | 자유 | 고지 유지 |
| xeokit-sdk, BIMserver, Chili3D, bldrs Share, OpenConstructionERP | AGPL-3.0 | **불가**(별도 상용 라이선스 협의 필요) | 네트워크 서비스 제공 시에도 소스 공개 의무 |
| ConstructCAD, IngeTrazo, Sverchok | GPL-3.0 | **코드 이식 불가** | 파생물 전체 GPL |
| Xbim Essentials | CDDL (LICENCE.md 확인) | 링크 가능성 있음(파일 단위 카피레프트 유사) | 수정 파일 공개. 세부는 검토 필요 |
| Speckle server | Apache-2.0 + EE 디렉터리 | 대부분 자유, EE 디렉터리 제외 | EE 부분은 상용 약관 |
| buildingSMART IDS / IFC 스펙 | 별도 표준 라이선스 | 구현은 자유(표준 문서 재배포 조건 확인) | — |

주의: 사용자가 만약 "오픈소스로 공개하는 대체 도구"를 만들 계획이라면 위 제약이 훨씬 완화됨(GPL/AGPL 채택 시 대부분의 후보 코드를 직접 사용 가능). 어느 쪽인지가 아키텍처 결정의 첫 변수.

---

## 5. 제외 목록

| 후보 | 이유 |
|---|---|
| ThatOpen/web-ifc-viewer (1,041★) | 마지막 push 2023-09-29, 후속 engine_components로 대체 |
| ThatOpen/web-ifc-three (597★) | 마지막 push 2024-04-17 |
| xeolabs/xeogl (1,170★) | 마지막 push 2020-09, 라이선스 other |
| specklesystems/speckle-sharp (436★) | 아카이브(2025-07), 후속 speckle-sharp-connectors로 대체 |
| hannobraun/fornjot (2,545★) | README상 "더 이상 개발 안 함", 아카이브 |
| mlt131220/Astral3D (2,490★) | 3D 엔진/에디터, BIM 경량화 뷰 위주, 기여자 사실상 1명(`<=1`), 데모 수준으로 판단(추정) |
| Autodesk/revit-ifc | Revit API 종속, 라이선스 불명(필드 없음) |
| datadrivenconstruction/cad2data-Revit-IFC-DWG (546★) | 라이선스 other, 변환 바이너리 의존 여부 미확인 → 신뢰 불가 |
| red1oon/bim-ootb, BIMCompiler 등 | 17★·기여자 2, 데모 수준 |
| DynamoRevit | Autodesk Revit 종속 |

(1년 이상 push 없음 또는 아카이브/데모급만 제외 대상으로 분류. 이 조사에서 `BIMserver`는 2026-03 push·릴리스 2025-07이라 정체지만 제외하지 않고 "관찰"로 둠.)

---

## 6. 미확인·불확실 사항

- 신흥 4종(ConstructCAD, BimCraft, Pascal, ifc-lite)의 기능은 README 주장이며 **코드 실행 검증 없음**(지시상 클론 금지).
- 일부 SPDX가 GitHub에서 NOASSERTION/other로 표시되어 LICENSE 파일을 직접 확인했으나, CadQuery·IDS 등은 본문 앞부분만 확인.
- 기여자 수는 anon 포함 헤더 기반이라 봇 계정 포함 가능성.
- star 급증 프로젝트(Pascal 25k)는 실사용 규모와 일치하지 않을 수 있음(추정).
- GitHub 검색 API 한도(분당 30회)에 걸려 일부 주제 검색(IFC diff, BIM + AI 등)이 얕음. 다단어 질의는 결과가 비는 경우가 있었음.
- Bonsai·FreeCAD의 성능/안정성 한계는 이슈 개수 외 개별 이슈 링크로 확인하지 못함(추정 표기).

---

## 7. 출처

- GitHub REST API (`gh api repos/<owner>/<repo>`, 2026-11-07 조회): 위 모든 저장소.
- README/LICENSE 직접 열람: pascalorg/editor, getcadcraft/bimcraft(+PROVENANCE.md), francisreboucas/constructcad, ingelibre/ingetrazo, LTplus-AG/ifc-lite, DrunkOnJava/rvt-rs, xiangechen/chili3d, specklesystems/speckle-server(LICENSE), IfcOpenShell(COPYING, COPYING.LESSER, 파일 헤더, src/ 디렉터리 목록), FreeCAD(src/Mod/BIM 디렉터리), OCCT(LICENSE_LGPL_21.txt, OCCT_LGPL_EXCEPTION.txt).
- URL: https://github.com/IfcOpenShell/IfcOpenShell , https://github.com/FreeCAD/FreeCAD , https://github.com/ThatOpen/engine_web-ifc , https://github.com/ThatOpen/engine_components , https://github.com/LTplus-AG/ifc-lite , https://github.com/xeokit/xeokit-sdk , https://github.com/specklesystems/speckle-server , https://github.com/Open-Cascade-SAS/OCCT , https://github.com/elalish/manifold , https://github.com/ricosjp/truck , https://github.com/getcadcraft/bimcraft , https://github.com/francisreboucas/constructcad , https://github.com/pascalorg/editor , https://github.com/DrunkOnJava/rvt-rs , https://github.com/buildingSMART/IDS , https://github.com/buildingSMART/IFC5-development
