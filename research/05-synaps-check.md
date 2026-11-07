# 05. "synaps" 확인

- 기준일 2026-11-07
- 방법: `gh search repos`(철자 변형·설명 검색), `gh api`(저장소·트리·README·LICENSE 읽기), WebSearch, WebFetch(벤더 페이지 2곳). 클론·실행·gh 쓰기 동작 없음.
- 법률 자문 아님.

## 0. 결론

**"synaps"는 하나로 특정되지 않는다.** BIM·건축 맥락의 후보가 2개이며, 사용자가 어느 쪽을 가리켰는지는 사용자에게 물어야 확정된다. 나는 임의로 하나를 고르지 않는다. 가능성만 적으면 **A(Synaps 건축 설계 웹 서비스)가 맥락상 더 그럴듯**하지만 근거는 약하다(Revit 대안 비교 맥락에 노출되는 쪽이 A뿐이라는 것).

| 후보 | 정체 | 오픈소스 | BIM/건축 관련 | 소스 열람 |
|---|---|---|---|---|
| **A. Synaps (synaps.app)** | 클라우드 건축 설계 워크스페이스(상용 SaaS 추정), 도면·3D 연동 | **아님/불명** (공개 저장소 없음) | 높음(평면·단면·입면·IFC/DWG 내보내기 주장) | **불가** (소스 비공개) |
| **B. SynapsCAD (timschmidt/synaps-cad)** | OpenSCAD 코드 편집 + AI 대화형 3D CAD IDE, Rust | 예 (MIT OR Apache-2.0) | **없음** (BIM 아님) | 가능, 구조·README 확인 |

## 1. 검색 이력

- `gh search repos`: `synaps`, `synapsbim`, `synaps bim`, `synaps ifc`, `synapse-bim`, `synaps-bim`, `synaps architecture`, `synaps3d`, `synapsis-bim`, `synaps in:name,description,readme bim`, `synapse in:name,description ifc`, `synaps in:name,description architecture`, `synaps in:name,description cad`, owner `getcadcraft`에서 `synaps`.
- WebSearch: "Synaps BIM open source IFC", `"Synaps" BIM Revit alternative software`, "synaps-cad timschmidt AI 3D CAD IDE".
- WebFetch: https://website-7eq.pages.dev/news/blog/why-were-not-trying-to-replace-revit/ (벤더 블로그 2026-03-31), https://synaps.app.
- 걸러낸 무관 프로젝트: Matrix Synapse(matrix-org/synapse 12k★, element-hq/synapse), Azure Synapse 샘플, SynapseML(Microsoft), airbnb/synapse(서비스 디스커버리), vertexproject/synapse(정보 시스템), Luminarys/synapse(BitTorrent), ProfSynapse/Professor-Synapse(AI 프롬프트), HaseebKhalid1507/SynapsCLI(Rust 터미널 AI 에이전트 런타임), maximem-ai/maximem_synap_sdk(에이전트 메모리), synaps-app/synaps(Lovable로 만든 노드 그래프 UI, `vite_react_shadcn_ts`, 폴란드어 UI, BIM과 무관), synaps3du·bimbo_synapse_mex(0★, 무관).
- **GitHub에서 "Synaps" BIM 저장소는 찾지 못했다.** CadCraft 가족 15개 저장소(getcadcraft) 안에도 synaps는 없다.

## 2. 후보 A — Synaps (상용 웹 서비스)

### 2.1 확인된 것 (벤더 자기 서술, 독립 검증 없음)
- 출처: https://synaps.app , 벤더 블로그 https://website-7eq.pages.dev/news/blog/why-were-not-trying-to-replace-revit/ (2026-03-31 글). `website-7eq.pages.dev`는 Cloudflare Pages 기본 도메인이라 정식 도메인 이전 전 미러일 가능성(추정).
- 제품: 브라우저 기반 건축 설계 워크스페이스. 텍스트/스케치로 평면 생성, 이미지에서 평면 트레이싱(자체 평면 모델 "Vecy-2" 언급), 1mm 정밀 드래프팅(미터법/야드파운드), 공간 시각화(카메라 워크스루), 한 캔버스 실시간 협업. 평면·단면·입면·배치도·3D 모델이 연결되어 편집이 전파된다고 주장.
- 출력: PDF 레이아웃, IFC, DWG, DXF 내보내기 주장. Revit·AutoCAD·Archicad·SketchUp과 호환한다고 표기.
- 포지셔닝: 벤더 스스로 "Revit을 대체하려 하지 않는다", 자신을 "의도적으로 2D 드래프팅 환경 + 같은 건물의 3D 뷰", 풀 BIM 모델링 도구가 아니라고 설명. Revit과의 연동은 "장기 핵심 사고"일 뿐 확정 기능 아님. 베타 런치 후 4개월에 5만 사용자 주장.
- 상태: 앱 도메인 `beta.synaps.app`, 무료 체험, 가격표는 페이지에서 못 봄. 한때 "7월 1일까지 모두 무료" 문구가 있었으나 이미 지난 일정.
- **저장소/라이선스: 없음.** 오픈소스라는 언급·GitHub 링크를 두 페이지 어디에서도 찾지 못함. 회사 소재·창립자 정보도 페이지에 없음.

### 2.2 판정 (02 문서 요구 표 기준; 소스 없음 → 전부 "주장만" 또는 "벤더가 부정")
| # | 기능 | 판정 | 근거 |
|---|---|---|---|
| 1 | 3D 요소 모델링 | 주장만(축소: "3D view") | 벤더가 풀 BIM 모델링 아님이라 명시 |
| 2 | 호스팅·조인 | 불명 | 언급 없음 |
| 4 | 양방향 연관성 | 주장만 | "views stay linked" 홍보 문구뿐 |
| 5·6 | 2D 도면·시트 | 주장만 | 평면/단면/입면 생성, PDF 레이아웃. 벤더 블로그는 "시트 세트"를 Revit과의 역할 분담 예로 듦 |
| 9 | IFC 입출력 | 주장만(내보내기) | 가져오기 언급 없음 |
| 10 | DWG/DXF | 주장만(내보내기) | |
| 11·12 | 패밀리/워크셰어링 | 패밀리 언급 없음 / 실시간 협업은 주장만 | |
| 21 | .rvt | 없음 | Revit과는 IFC/DWG 경로 |

### 2.3 흡수 가능 항목·위험
- **코드 흡수: 불가**(비공개). 설계 참고도 공개 문서가 마케팅 수준이라 얻을 게 거의 없음.
- **경쟁/참고 관점**: "풀 BIM 복제가 아니라 2D 중심 + 연결된 3D, Revit과 병용"이라는 **좁은 쐐기 포지셔닝**은 02 문서 결론(4)과 같은 방향의 시장 사례(벤더 주장).
- 위험: 폐쇄 SaaS 락인, 가격·존속 불명, 데이터는 "소유권 유지·모델 학습 미사용"이라고 벤더가 밝히나 검증 불가. 오픈소스 대체 목적에는 맞지 않음.

## 3. 후보 B — SynapsCAD (timschmidt/synaps-cad)

### 3.1 저장소 실측
| 항목 | 값 | 근거 |
|---|---|---|
| URL | https://github.com/timschmidt/synaps-cad (이전 소유자 `ierror`로도 접근됨 — 같은 저장소로 리다이렉트, 이관 추정) | gh api, WebSearch |
| star / fork | 375 / 23 | gh api |
| 생성 / 마지막 push | 2026-02-27 / 2026-10-05 | |
| 커밋 수 | 248 | commits API |
| 기여자 | 2 | contributors API |
| 릴리스 | v0.10.0 (2026-03-26) | |
| 언어(바이트) | Rust 543,708 / OpenSCAD 80,747 | languages API |
| 라이선스 | **MIT OR Apache-2.0 이중** (LICENSE 본문 "dual-licensed", LICENSE-MIT·LICENSE-APACHE 존재, Cargo.toml `license = "MIT OR Apache-2.0"`). GitHub SPDX는 NOASSERTION. WebSearch 결과에는 과거에 GPL v3라는 서술이 있었으나 현재 파일과 불일치(과거 상태일 수 있음) | 파일 직접 확인 |
| 소스 규모 | 171개 파일, `src/compiler/{evaluator,geometry,rendering}`, `plugins/{ai_chat,code_editor,camera,scene,ui}` | git tree |

### 3.2 정체와 기능
- README: "OpenSCAD 편집, 정확한 CSG, 대화형 시각화, AI 보조 모델링을 한 Rust 앱에". OpenSCAD 소스를 OpenSCAD 실행 파일 없이 파싱·평가, Bevy 0.15 + egui 뷰포트, STL/OBJ/3MF 내보내기, LLM(OpenAI/Claude/Gemini/Ollama 등)으로 코드 수정. 스스로 "early prototype, OpenSCAD 호환 불완전"이라고 명시. 숫자를 `hyperreal::Real`(정확 유리수/기호)로 처리하는 점이 특이.
- **BIM 요소 없음**: 트리에서 `ifc|bim|wall|revit` 일치 0건. IFC·시트·일람표·호스팅·패밀리 모두 해당 없음.
- **빌드 재현성 문제**: `Cargo.toml`이 `../csgrs`, `../hyperlimit`, `../hypercurve`, `../hyperlattice`, `../hypermesh`, `../hyperreal`을 **상대 경로 의존**으로 요구(README: "Hyper 의존성 옆에 clone"). 단독 저장소만으로는 빌드 불가(소스 열람 기준; 실행 안 함).

### 3.3 02 문서 요구 표 기준 판정
BIM 기능 **전부 해당 없음**. 18번(API/스크립팅)만 일부 연관: 코드로 형상을 기술하는 파라메트릭 접근(OpenSCAD)이며 build123d/CadQuery 계열과 같은 범주.

### 3.4 흡수 가능 항목·위험
- 라이선스는 MIT/Apache라 자유. 가져올 만한 것: (1) **AI가 코드를 수정하고 3D 뷰포트로 확인하는 루프의 UI 구조**(`plugins/ai_chat.rs`, `compilation.rs`), (2) Rust 순수 파이프라인(OpenSCAD 파서 `openscad_rs` + `csgrs`)이라는 점, (3) 컴파일러가 라이브러리로 분리되어 있다는 점. 건축 BIM 요구와는 거리가 멀어 우선순위 낮음.
- 위험: 기여자 2명 프로토타입, 로컬 경로 의존 때문에 재현 어려움, 상류 `csgrs`/`hyper*` 안정성 미확인.

## 4. 미확인 항목
1. 사용자가 말한 "synaps"가 A인지 B인지 다른 것인지 — **사용자 확인 필요**. 철자 변형(Synapse BIM, SynapseBIM, Sinaps 등)으로 한국어 발음 표기가 다를 수 있음.
2. Synaps(A)의 실제 기능 동작, 가격, 회사 정체, IFC/DWG 품질, 데이터 처리 — 모두 벤더 자기 서술이며 제품을 실행해 보지 못함(베타 로그인 필요).
3. Synaps(A)가 오픈소스 부분(SDK 등)을 갖는지 — 공식 페이지에 언급 없음, GitHub에서 대응 저장소 못 찾음.
4. SynapsCAD(B)는 소스 파일 본문을 정독하지 않았고 README·Cargo.toml·트리·LICENSE만 확인. 과거 GPL 서술이 맞는지(라이선스 변경 이력)는 미확인.
5. 이 이름의 건축 프로젝트가 GitHub 밖(GitLab, 자체 호스팅, 중국·한국 로컬 플랫폼)에 있을 가능성.

## 5. 출처
- GitHub: https://github.com/timschmidt/synaps-cad (README, LICENSE, Cargo.toml, tree), https://github.com/synaps-app/synaps (무관 확인용 CHANGELOG·package.json)
- https://synaps.app , https://website-7eq.pages.dev/news/blog/why-were-not-trying-to-replace-revit/
- https://github.com/ierror/synaps-cad , https://hn.svelte.dev/item/47183039 (Show HN, 검색 결과 노출만 확인)
