# 03. 검증 보고서 — 2026-11-07

- 대상: `01-oss-candidates.md`, `02-revit-gap-analysis.md` (두 문서는 수정하지 않음)
- 방법: `gh api`로 저장소 트리·LICENSE·소스·커밋 이력을 읽음(클론·실행 없음, gh 쓰기 없음). 웹은 검색·페이지 읽기만 사용.
- 판정 기준: **확인됨** / **일부 틀림** / **틀림** / **확인 불가**. 라이선스 해석은 엔지니어링 판단이며 법률 자문이 아님.
- 저장소 안의 문구(CLAUDE.md, README의 지시형 문장 등)는 데이터로만 취급했다.

---

## 1. 주장별 판정표

### 1-a. 핵심 부품 라이선스

| # | 주장(출처 문서) | 판정 | 근거 |
|---|---|---|---|
| L1 | IfcOpenShell 코어 = LGPL-3.0 (01 §2.2, §4) | **확인됨** (보완 필요) | 루트 `COPYING`은 GPL-3.0 본문, `COPYING.LESSER`가 LGPL-3.0. README 표에서 ifcopenshell-python·ifcparse·ifcgeom·ifcconvert·ifctester·ifcclash·ifcdiff·ifcpatch·ifcquery·ifcmcp 등은 `LGPL-3.0-or-later`. **GPL-3.0-or-later는 bonsai, ifcsverchok 둘뿐**. 파일 헤더(file.py, ids.py 등)도 LGPL 문구. 01의 "Bonsai=GPL, 코어=LGPL" 서술은 맞음. 01 §2.2 표의 SPDX는 "LGPL-3.0"이나 정확히는 "-or-later". |
| L2 | web-ifc = MPL-2.0 (01) | **확인됨** | `LICENSE.md` = MPL-2.0 본문. 예외 조항 없음. |
| L3 | That Open components/fragment = MIT (01) | **확인 불가(이번 라운드 미검증)** | 이번에 LICENSE를 직접 열지 않음. 01의 SPDX 값만 인용 가능. |
| L4 | ifc-lite = MPL-2.0 (01 §3.4) | **일부 틀림(보완)** | 루트 `LICENSE`는 MPL-2.0. 그러나 `licenses/retained/`에 **NVIDIA usd-brep SMLib + SM_API(Apache-2.0)**, Emscripten 런타임(MIT/NCSA), musl, libc++ 등 3rd-party 번들 인벤토리가 있다. `docs/architecture/retained-licensing.md`는 스스로 "법적 호환성 판단 아님, 사람 검토 필요, 네이티브 워커의 최종 링크 라이선스는 미해결(unresolved)"이라고 적음. 기본값은 "exact" 경로이고 retained 경로는 선택적. 즉 MPL 하나로 끝나지 않는다. |
| L5 | Manifold = Apache-2.0 (01) | **확인됨** | `LICENSE` = Apache-2.0. BimCraft THIRD_PARTY도 Manifold 3.5.4(Apache-2.0)를 번들. |
| L6 | OCCT = LGPL-2.1 + OCCT exception (01) | **확인됨** | `LICENSE_LGPL_21.txt` + `OCCT_LGPL_EXCEPTION.txt`. 예외 1.0: 헤더 포함 **객체 코드** 형태는 조건을 자유롭게 정해 배포 가능하되, OCCT를 사용/기반으로 한다는 눈에 띄는 고지 필요. 소스 형태에는 적용 안 됨. |
| L7 | Speckle = Apache-2.0 + EE 디렉터리 (01 §3.8) | **확인됨** | `LICENSE` 첫 부분: `packages/server/modules/workspaces/`, `.../gatekeeper/`만 EE 라이선스, 나머지 Apache-2.0. 두 디렉터리에 각각 별도 `LICENSE` 파일 존재. (SPDX NOASSERTION 사유도 이것으로 설명됨.) |
| L8 | rvt-rs = Apache-2.0 (01) | **확인됨** | `LICENSE`, `NOTICE`(Copyright 2026 Griffin Long). |
| L9 | 앱 동봉 시 LGPL "교체 가능성 보장 조건" (01 §3.1, §4) | **확인됨 + 구체화** | `COPYING.LESSER` 4항(Combined Works): (a) 사용 고지 (b) GPL·LGPL 문서 사본 (c) 실행 중 저작권 표시 시 포함 (d-0) 최소 대응 소스+재링크 자료 또는 (d-1) 적절한 공유 라이브러리 메커니즘. 서두가 "수정 및 그 디버깅용 리버스엔지니어링을 제한하지 않는 조건"을 요구 → **EULA의 리버스엔지니어링 금지 조항에 LGPL 라이브러리 예외 필요**. PyInstaller는 onedir로 .pyd/.so를 분리해 두는 쪽이 (d-1)에 유리, onefile은 교체 여부를 따져야 함(추정). |

### 1-b. 신흥 도구 실체

| # | 주장(출처) | 판정 | 근거 |
|---|---|---|---|
| E1 | ConstructCAD: Revit식 뷰·시트·일람표·패밀리 기능 (01 §3.5, "저자 주장+스크린샷까지만 확인") | **확인됨(실코드 존재), 단 개발 방식은 AI 주도로 보임** | Rust 소스 합계 약 6.5MB(342 .rs). `cc_core/src/view.rs`의 `ViewKind`에 FloorPlan·CeilingPlan·ThreeD·Elevation·Section·Legend·Schedule·Sheet(title_block, viewports)·Diagram. `cc_views`(plan/cut/hatch/dxf/schedule/sheet), `cc_ifc`(export·import 각 약 130KB, round_trip 테스트), `phase.rs`, `expr.rs`, `component_*`. 워크셰어링 관련 코드·파일 없음, README 로드맵에 hosted components 미구현. |
| E2 | ConstructCAD 기여자 3명 (01) | **일부 틀림(해석 보완)** | 기여자 3명은 맞지만 사실상 1인: francisreboucas 466 / 총 473 커밋. 저장소 생성 2026-10-18 → 20일간 약 24 커밋/일, 최근 커밋 간격이 15분 단위. 루트에 `CLAUDE.md`, `AGENTS.md` 존재. 01의 "AI 보조 생성 가능성은 추정이며 확인 못 함"은 **정황상 강하게 지지됨**(확정은 아님: 커밋 메시지 100건에 Co-authored-by 흔적은 0). |
| E3 | BimCraft: 파라메트릭 벽·문·창, 뷰·일람표·시트·간섭 (01 §3.6) | **확인됨(실코드 존재)** | `app/` JS 1,906파일, `bimcraft.js` 403KB, `family-model.js` 65KB 등. 패밀리는 formula 파라미터·box/extrude/revolve/sweep·boolean·MEP connector를 지원하고, **IFC 안에 IfcGroup + Pset(CadCraft_Family 레시피 JSON) + 사이즈별 IfcXxxType**으로 저장(소스 헤더 주석). `model-schedule.js`, `res-sheets.js`, `clash.js`, `bcf.js`, `dwg/` 등. |
| E4 | BimCraft 커밋 이력·기여자 4명 (01) | **일부 틀림(맥락 누락)** | 공개 저장소는 비공개 개발본의 **"Sync from development" 미러**다(커밋 약 30개, 최상위 CadCraft 계정). 커밋 수로 활동량을 판단할 수 없다. 소스에 "session ... DC.114" 같은 에이전트 세션 표기가 있어 AI 에이전트 개발 가능성이 높다(추정). 회사(CadCraft) 제품, 데스크톱 Studio는 별도 저장소(`getcadcraft/studio`, SPDX Apache-2.0). 상표 정책상 포크는 개명 필요(README). |
| E5 | Pascal: IFC·Revit 언급 거의 없음, "IFC 중심 아님" (01 §3.7) | **일부 틀림** | `apps/ifc-converter` + `packages/ifc-converter`에 **IFC→Pascal 씬 임포터(web-ifc 사용, 스스로 "early alpha")와 scene→IFC export 코드·왕복 테스트**가 있다. 즉 IFC 입출력이 존재. 단 저작 엔진은 씬 그래프 기반이라 IFC 네이티브는 아님. MIT, 회사(Pascal Group Inc.) 프로젝트. |
| E6 | Pascal star 25k는 실사용과 불일치 가능 (01 §3.7, §6) | **확인 불가** | 기여자 56명, 커밋 2,218, 상위 기여자 3명이 각 900/720/260. 포크 3,153·watcher 131. 활동은 star 대비 불일치가 아니다. 다만 star 급증 경위는 이번에 확인하지 않음. |
| E7 | ifc-lite: IFC5 지원, IfcOpenShell 대비 요소별 검증(저자 주장) (01 §3.4) | **일부 확인** | 18k 파일(Rust 2,847·TS 11,473), 6,094 커밋(louistrue 4,522·petruconduraru 1,231), 2026-01-10 생성. `.github/workflows/ifcopenshell-parity.yml`, `benchmark.yml`, `apps/server/src/parity_tests.rs`가 있어 **패리티 검증이 CI에 실재**. 그러나 "99.9%+ 일치"는 README 수치이며 CI 결과는 이번에 못 읽음. `.claude/skills/` 존재, 최근 100커밋 중 17건이 claude 언급 → AI 에이전트 적극 활용. star 461 대비 활동이 매우 높음(소수 인원+자동화). |

### 1-c. .rvt 경로

| # | 주장 | 판정 | 근거 |
|---|---|---|---|
| R1 | rvt-rs는 "CFB/gzip 컨테이너 수준 위주로 추정", 지오메트리 복원 여부 미확인 (02 §4) | **틀림** | README 지원표: Revit 2023(partial)·2024(partial)·2025(partial)은 project 요소 레코드를 해독해 벽 조인, 보 컷, 방 윤곽, 층, 호스트, 패밀리/타입명, 재료·레이어, GlobalId를 **IFC4로 내보냄**(`rvt-ifc model.rvt -o model.ifc --diagnostics`). 2015~22·2026·2027은 experimental, 2008~2014는 실험적·범위 밖. **한계**: 요소 본체는 대부분 **바운딩박스**(조인에서 잘림), 보·계단 일부만 형상, 패밀리 파일은 지오메트리 없음, 쓰기는 스트림 바이트 패치뿐(의미 편집 미구현). |
| R2 | rvt-rs 범위·기여자 (01) | **일부 틀림(맥락)** | 기여자 10명이나 1,353/1,439 커밋이 1인(DrunkOnJava). 생성 2026-04-19, 103★. 단위테스트 신규 금지 정책, 코퍼스 기반 CI. 클린룸 주장은 `CLEANROOM.md`와 NOTICE 자기 선언이며 **외부 법적 검증 없음**. NOTICE의 근거 법리 인용(Sega v. Accolade 등)은 미국·EU·호주 기준이고 한국법은 아님. |
| R3 | ODA BimRv: 읽기 2011+, 쓰기 최신 버전 한정 (02 §4) | **확인됨** | 제품 페이지: "version 2011 and higher" 읽기, 쓰기는 최신 RFA/RVT 한정, 로드맵에 "2026 file format support"(2025-12 production 항목). 라이선스 조건·가격·재배포·리버스엔지니어링 관련 조항은 **페이지에 없음**(60일 무료체험만 공개). 상세 조건은 확인 불가. |
| R4 | 한국법 제101조의4 역분석 허용 범위 (02 §4) | **확인 불가** | 2차 해설만 있고 조문 원문 미확인(이번에도 접근 못함). |

### 1-d. 한국 지침

| # | 주장 | 판정 | 근거 |
|---|---|---|---|
| K1 | 기본지침: "BIM 데이터 제출 포맷은 PDF 또는 IFC 등", IFC=ISO 16739, 버전 지정 없음 (02 §5) | **확인 불가(2차 재확인만)** | 검색 결과가 CODIL 페이지 하나로 수렴(출처 사실상 단일). 국토부 자료실(molit.go.kr id=4634)은 리다이렉트 반복으로 열림 실패, 시행지침 PDF 3종(midas 호스팅)은 10MB 초과로 읽기 실패. 원문 조항·IFC 버전 표기는 **여전히 미확인**. |
| K2 | 시행지침(발주자·설계자·시공자편) 2022-07-26 발간 (02 §5) | **일부 틀림(날짜 불일치)** | 국토부 자료실 등록일은 **2022-11-08**로 표시됨(검색 결과 스니펫). 발간일과 게시일의 차이일 수 있어 단정 불가. 02의 2022-07-26은 이번에 재확인 못함. |
| K3 | 조달청 지침: 원본 + IFC 동시 제출 (02 §5) | **확인 불가(오히려 약화)** | 이번 검색에서 "원본+IFC 동시 제출"은 **도로분야 성과품 체계 제안 논문**의 내용이지 조달청 지침 조항으로 확인되지 않았다. 02는 이것을 "논문 기술"로 표기했으므로 02의 표기는 정직하지만, 설계 근거로 쓰기엔 약하다. |
| K4 | 세움터 BIM 인허가: IFC 2x3 이상 (02 §5) | **확인 불가** | 이번 검색에서 직접 근거 못 찾음. 2x3 이상 표기의 출처는 여전히 2차(02 인용 링크: info.dec-w.com/39,40). |
| K5 | cbilder 블로그 "고시 제2026-104호" 불신 (02 §5) | **확인됨(방향)** | 공식 출처를 찾지 못했으므로 불신 판단은 타당. |

### 1-e. Bonsai·FreeCAD 한계

| # | 주장 | 판정 | 근거 |
|---|---|---|---|
| B1 | Bonsai: 절단/주석은 벡터, 투영은 **래스터** SVG (02 §3 5행) | **틀림(현행 소스 기준)** | `src/bonsai/bonsai/bim/module/drawing/operator.py`: `linework_mode` = `OPENCASCADE` \| `FREESTYLE`. OPENCASCADE는 `generate_linework()`가 HLR로 cut·projection 그룹(`class="projection"`)을 SVG path(벡터)로 생성. 래스터는 선택 사항인 **underlay**(Blender 렌더 PNG)뿐. 2026-10 현재 drawing 모듈에 커밋 활발. 남는 한계: (a) 생성(regenerate)형이며 라이브 연동 아님, (b) 숨김선 렌더는 opt-in 개발 브랜치(#6424)에서만 확인, 릴리스 반영 미확인, (c) 공식 가이드에 "Drafting module is still in early development" 문구와 비어 있는 절이 남아 있음. |
| B2 | Bonsai 호환성 위험(Revit 열기 시 누락) (02 §3) | **확인 불가** | 커뮤니티 증언 외 재현 근거 없음. |
| B3 | FreeCAD 도면 갱신이 수동적(02 §3 4행 [판단]) | **확인 불가(약화)** | FreeCAD는 문서 recompute 체인(SectionPlane→Shape2DView→TechDraw)이 자동 의존성 갱신 모델이다. "수동적"이라는 서술의 정확한 의미(트리거 시점·대형 모델 비용)는 근거 못 찾음. |
| B4 | FreeCAD 대형 모델 2D 미최적화 (02 [확인(README)]) | **확인 불가(재확인 안 함)** | 이번에 BIM_Workbench README를 열지 않음. |

---

## 2. 앞선 문서에서 고쳐야 할 점

### 01-oss-candidates.md
1. §3.5 ConstructCAD: "코드 실행 검증 없음"은 유지하되, **코드 자체는 실재**(뷰·시트·일람표·IFC 왕복 테스트)한다고 수정. 동시에 1인·20일·CLAUDE.md/AGENTS.md로 **AI 주도 개발 정황** 추가.
2. §3.6 BimCraft: 공개 저장소는 **비공개 개발본의 sync 미러**(커밋 약 30개)임을 명시. 패밀리 시스템이 IFC에 저장되는 구조(IfcGroup+Pset+IfcXxxType)임을 추가. 에이전트 세션 흔적, 회사 소유 명시.
3. §3.7 Pascal: "IFC 한 곳만 언급"을 **IFC 임포터·익스포터 코드 존재(alpha)**로 정정. 기여자 56 외에 상위 3인이 압도적임을 병기.
4. §3.4 ifc-lite: "MPL-2.0"에 **retained 경로의 SMLib/Emscripten 3rd-party 인벤토리와 미해결 링크 라이선스** 주석 추가. 패리티 CI 실재 반영. AI 활용(.claude/skills) 명시.
5. §2.2/§4 IfcOpenShell: SPDX를 `LGPL-3.0-or-later`로, `bonsai`·`ifcsverchok`만 GPL임을 명시. 루트 COPYING이 GPL 본문인 점도 표기(혼동 방지). "교체 가능성 보장 조건"은 L9의 4항 요건으로 구체화하고 EULA 리버스엔지니어링 예외 필요성 추가.
6. §3.10 rvt-rs: "지원 범위가 Revit 버전별로 제한적" → 지원표(2023~2025 partial IFC4 export, 바운딩박스 본체 한계)로 구체화. 1인 개발(94%) 병기.
7. §3.1 Bonsai: 도면 관련 서술이 있다면 B1 반영(주 서술은 아니나 02와 정합 필요).

### 02-revit-gap-analysis.md
1. §3 표 5행(2D 도면 생성)·§1 결론: **"투영은 래스터" 삭제**. Bonsai의 OPENCASCADE linework는 벡터 SVG. 도면 공백 전제를 "벡터 여부"가 아니라 "증분 연동·주석 연관성·한국 서식"으로 재서술.
2. §4 rvt-rs 행: "CFB/gzip 컨테이너 수준 위주로 추정" → "Revit 2023~2025 project 요소를 IFC4로 내보냄(근사 지오메트리)". 결론(MVP 제외, 연구 트랙)은 그대로 유지 가능. 단 "읽기 위주·초기"는 맞고 "쓰기 ✕"도 맞다.
3. §5 한국 지침: K2의 날짜(2022-07-26 vs 자료실 2022-11-08) 확인 필요 표기. K3(조달청 원본+IFC)은 "논문 기술"을 넘어 **지침 조항으로는 미확인**으로 한 단계 낮출 것. "원본+IFC 동시 제출이 기본 패턴"(함의 문단)은 근거 약함 → "가능성"으로.
4. §3 FreeCAD 4행: "TechDraw 갱신 수동적 [판단]"은 근거 약함. recompute 체인이 자동 의존성 갱신을 제공하므로 "△(대형 모델 비용 미측정)"로 재서술하거나 [판단] 태그 유지하고 근거 부재 명시.
5. §부록: 신흥 도구를 "참고 구현/설계 아이디어 출처"로만 본 사전 판단은 타당하지만, **BimCraft 패밀리-in-IFC 구조**는 설계 참고 가치가 높음을 추가.

---

## 3. 끝내 확인 못한 것

| 항목 | 이유 / 필요한 후속 |
|---|---|
| 국토부 BIM 기본지침·시행지침의 **IFC 버전·4.1.3/4.1.4 조문** | molit 자료실 리다이렉트 반복, midas 호스팅 PDF 10MB 초과. 국가건설기준센터 또는 국토교통 BIM 포털에서 PDF를 직접 내려받아 읽는 것이 필요(다운로드는 사용자 승인 필요). |
| 조달청 시설사업 BIM 적용 기본지침서 **현행 버전·성과품 조항** | 검색에서 학술 논문만 나옴. 조달청 홈페이지/나라장터 원문 필요. |
| 세움터 BIM 업무지침서의 **IFC 2x3 요구** | 02가 인용한 info.dec-w.com 링크를 이번에 열지 못함. |
| 한국 저작권법 제101조의4 조문 원문 및 판례 | 법령 사이트 접근 안 함. 법무 검토 필수. |
| ODA BimRv **라이선스 조건·가격·재배포·역공학 관련 약정** | 공개 페이지에 없음. 트라이얼 약관/영업 문의 필요. |
| rvt-rs **실제 정확도**(실모델 대비 IFC 품질) | 코드 실행 금지. README는 자기 평가. `docs/evidence.md`를 읽으면 더 구체화 가능. |
| That Open components/fragment, CadQuery의 LICENSE 직접 확인 | 이번 라운드 미수행. |
| IfcOpenShell/Bonsai의 buildingSMART **인증 상태**, IDS/BCF 범위 | 미확인(02도 미확인). |
| Bonsai·FreeCAD **대형 모델 성능·왕복 호환 실측** | 이슈/문서 링크로는 확인 불가. 02가 권한 "샘플 1동 스파이크"가 유일한 해법. |
| ifc-lite "99.9% 일치" CI 실제 결과, Pascal star 급증 경위 | 이번에 CI 로그·star 이력 미확인. |
| ConstructCAD/BimCraft/ifc-lite의 **AI 생성 비율**과 품질 | 정황(CLAUDE.md, .claude/skills, 커밋 빈도)만 확인. 실제 결함률은 알 수 없음. |

---

## 4. 설계에 미치는 영향 (요약)

1. **도면 공백 전제 약화**: 벡터 SVG 도면은 이미 있다. 차별화는 증분 갱신·주석 연관성·한국 서식.
2. **BimCraft 포크 후보성 상승, 단 조건부**: Apache-2.0, 실코드, IFC 내장 패밀리 구조. 그러나 공개본은 미러이고 상류가 회사 비공개이며 AI 세션 주도이므로 업스트림 추적 불가·품질 미검증 위험이 있다. 포크 시 개명 의무.
3. **rvt-rs는 MVP 제외 유지, 연구 트랙의 "근사 지오메트리 임포트 옵션"으로 격상 가능**.
4. **IfcOpenShell 동봉**: LGPL 4항 충족(onedir 분리 권장, EULA 역공학 예외, 고지·라이선스 사본 동봉)과 OCCT 고지 목록을 설계 체크리스트에 넣을 것.
5. **한국 지침 근거는 여전히 2차**: "IFC 버전 미지정 납품" 서술은 뒤집지도 지지하지도 못한다. 발주처 과업지시서 확인이 선행 조건.

---

## 5. 출처

- IfcOpenShell: https://github.com/IfcOpenShell/IfcOpenShell (COPYING, COPYING.LESSER, README 라이선스 표, src/ifcopenshell-python/ifcopenshell/file.py, src/ifctester/ifctester/ids.py, src/ifcmcp/pyproject.toml, src/bonsai/bonsai/bim/module/drawing/operator.py·prop.py)
- web-ifc: https://github.com/ThatOpen/engine_web-ifc (LICENSE.md)
- ifc-lite: https://github.com/LTplus-AG/ifc-lite (LICENSE, LICENSE_HEADER.md, licenses/retained/README.md·THIRD_PARTY_NOTICES.txt, docs/architecture/retained-licensing.md, README.md, .github/workflows/ifcopenshell-parity.yml, .claude/skills/)
- Manifold: https://github.com/elalish/manifold (LICENSE)
- OCCT: https://github.com/Open-Cascade-SAS/OCCT (LICENSE_LGPL_21.txt, OCCT_LGPL_EXCEPTION.txt)
- Speckle: https://github.com/specklesystems/speckle-server (LICENSE, packages/server/modules/workspaces/LICENSE, gatekeeper/LICENSE)
- rvt-rs: https://github.com/DrunkOnJava/rvt-rs (README 지원표, NOTICE, LICENSE)
- ConstructCAD: https://github.com/francisreboucas/constructcad (트리, crates/cc_core/src/view.rs, README 로드맵, CLAUDE.md/AGENTS.md 존재, 커밋·기여자 API)
- BimCraft: https://github.com/getcadcraft/bimcraft (트리, app/family-model.js 헤더, THIRD_PARTY.md, README, 커밋 이력), https://github.com/getcadcraft/studio
- Pascal: https://github.com/pascalorg/editor (apps/ifc-converter/README.md, packages/ifc-converter/src/export/, LICENSE)
- Bonsai 문서: https://docs.bonsaibim.org/studio/guides/drawings/index.html ; 개발 미러 커밋 SvgSerializer hidden lines: https://dev.glassvision.dedyn.io/Glassvision/IfcOpenShell/commit/6c8c63e34961f7567daacb43d94dfe2211767e70 (미러 사이트이므로 참고용); OSArch: https://community.osarch.org/discussion/comment/21739/
- FreeCAD NativeIFC/BIM: https://github.com/yorikvanhavre/FreeCAD-NativeIFC , https://yorik.uncreated.net/blog/2024-011-freecad-news-25
- ODA BimRv: https://www.opendesign.com/products/bimrv.md
- 한국(2차, 원문 아님): https://www.codil.or.kr/storyTelling/st13/st13.html , 국토부 자료실 https://www.molit.go.kr/USR/policyData/dtl.jsp?id=4634 (열람 실패), midas 호스팅 시행지침 PDF(열람 실패, 10MB 초과)
