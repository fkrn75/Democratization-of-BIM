# Revit 대체 요구 분석 — 2026-11-07

> 근거 수준 표기: **[확인]** = 공식/1차 자료로 확인, **[2차]** = 뉴스·논문·포럼·커뮤니티, **[판단]** = 내 추론(근거 약함), **[미확인]**.
> 한계: 웹검색 도구 위주로 조사했고 일부 정부·FreeCAD 위키 페이지는 접근 거부(403/인증서 오류)로 원문을 못 읽었다. 한국 지침의 현행 개정본 번호·조문은 대부분 미확인이다. OSS 후보 목록(01-oss-candidates.md)은 작성 시점에 폴더에 없어서 FreeCAD BIM·Bonsai만 직접 판정했다.

## 1. 결론 (먼저)

1. **"Revit을 대체하는 오픈소스"는 2026-11 현재 없다. 부분 대체만 가능하다.** 가장 가까운 것은 Bonsai(IfcOpenShell 기반, Blender 위)이며, 대체 가능 범위는 "IFC를 네이티브로 쓰는 소규모~중규모 건축 모델링 + 2D 도면 생성 + 데이터 검증"이다. [판단, 근거: 아래 2~3절]
2. **대체 가능한 사용자**: (a) IFC 중심 워크플로를 받아들이는 소규모 사무소·개인, (b) 모델 QA/IDS 검증·수량/데이터 추출·자동화 개발자, (c) 학습·연구. **대체 불가능한 사용자**: Revit 패밀리 자산·.rvt 협업 파트너를 가진 중대형 설계사·시공사, 공공 납품에서 Revit 원본+IFC를 함께 요구받는 곳, 다분야(구조·MEP) 동시 협업 팀.
3. **어떤 후보로도 안 메워지는 공백**: ① .rvt/.rfa 합법적 읽기/쓰기(상용 ODA BimRv뿐, 오픈소스 rvt-rs는 읽기 위주·초기), ② 파라메트릭 패밀리 생태계(IFC에는 패밀리 개념이 없어 Bonsai의 파라메트릭 문/창은 앱 내부 기능), ③ 대규모 다인 동시 편집(워크셰어링) + 대형 모델 성능, ④ 라이브 연동 도면(뷰 자동 갱신)과 한국 DWG/납품 서식의 완결성.
4. **만들 가치가 있는 빈틈**은 "Revit 전체 복제"가 아니라 **IFC 네이티브 + 한국 납품/인허가(세움터 IFC)·한국 표준 라이브러리(KBIMS) 연계**라는 좁은 쐐기다. Revit 풀 복제는 인력·기간상 비현실적이라는 것이 내 판단이다 [판단].

## 2. 기능 요구 표 (Revit 핵심 기능 분해)

등급: **M**=필수(MVP), **I**=중요, **L**=후순위. 난이도 1(쉬움)~5(매우 어려움). 근거 링크는 7절.

| # | 기능 | 등급 | 난이도 | 왜 어려운가 / 비고 |
|---|---|---|---|---|
| 1 | 3D 건축 요소 모델링 (벽·슬래브·기둥·보·지붕·계단) | M | 3 | 객체별 스타일(레이어드 재료), 조인(벽 코너·교차) 규칙이 핵심. 형상 단순해도 편집 후 재생성 규칙이 많음 |
| 2 | 호스팅·조인 (문/창이 벽에 종속, 벽 이동 시 개구부 따라감) | M | 4 | 호스트-게스트 의존성 + 불리언(개구) 재계산. Bonsai 커뮤니티에서도 개구부 공유 등이 미완 이슈로 언급 [2차] |
| 3 | 타입/인스턴스 파라미터 모델 | M | 3 | 데이터 모델은 IFC 속성셋·타입 객체로 대응 가능. UI와 일괄 편집이 일 |
| 4 | **양방향 연관성**: 모델↔평면/단면/입면/일람표 자동 갱신 | M | **5** | 모델 변경 → 영향받는 뷰·태그·치수·일람표를 증분 갱신하는 **의존성 그래프 + 변경 추적(dirty propagation)**이 필요. 뷰 하나가 필터·크롭·위상 설정 등 수십 개 상태에 의존. 전체 재생성은 대형 모델에서 느려 증분이 필수 |
| 5 | 2D 도면 생성 (평면·단면·입면, 절단/투영선 스타일, 해치) | M | 4 | 은선 제거(HLR) 품질·성능, 절단면 해치, 선 굵기 규칙. 한국 도면 표준 적용 |
| 6 | 시트·타이틀 블록·뷰 배치·출력(PDF/DWG) | M | 3 | 레이아웃 자체는 쉬우나 뷰 갱신 연동·번호 참조(시트 번호↔뷰 참조 태그)가 얽힘 |
| 7 | 주석: 치수·태그·텍스트·인출선·범례 | M | 4 | 모델 객체에 **연관된** 치수/태그(객체 바뀌면 값·위치 갱신)가 어려움. 단순 2D 선 그리기와 차이 |
| 8 | 일람표(Schedule)·수량 | M | 2~3 | 데이터 쿼리 + 필터/정렬/집계. IFC 속성 기반이면 비교적 쉬움 |
| 9 | **IFC 입출력 (IFC4/2x3, 속성·타입·재료·공간 구조 보존)** | M | 3~4 | 스키마는 공개이나 **품질**(왕복 무손실, 뷰어별 해석 차이)이 난점. 한국 납품·세움터 필수 |
| 10 | DWG/DXF 입출력 | M | 3 | DXF는 쉬움. DWG는 Autodesk 포맷(ODA Teigha/Drawings SDK 등 상용 또는 LibreDWG GPL). 2D 납품 호환에 필요 |
| 11 | **파라메트릭 패밀리 에디터** (참조 평면, 치수 구속, 타입/인스턴스 파라미터, 공식, 가시성) | I (자산 호환 필요 시 M) | **5** | 구속 솔버 + 파라미터 공식 평가 + 호스트 규칙 + 라이브러리 자산(수만 개)이 문제. 기술보다 **생태계/자산** 장벽이 큼. IFC에는 패밀리 개념이 없어 표준 교환 불가 |
| 12 | 워크셰어링: 중앙 모델, 워크셋, 요소 차용, 동기화 충돌 해결 | I (팀 사용 시 M) | 5 | 객체 단위 락/병합 + 변경 로그 + 충돌 해결 UX. 파일 기반 IFC는 병합이 어려움. Bonsai는 git 기반 IfcGit 실험 수준 [2차] |
| 13 | 구조 해석 모델·철근 | I | 4 | 해석 모델 분리, 철근 상세(RC 배근) 자동화는 별도 대형 기능 |
| 14 | MEP: 덕트/배관/케이블 라우팅, 시스템, 용량 계산 | I | 5 | 연결성 그래프, 자동 라우팅, 시스템 해석. 한국 설비 실무는 MEP 비중이 큼 |
| 15 | 간섭 확인 (충돌 검출) | I | 2~3 | 기하 충돌은 IfcClash 등 기존 도구로 가능 [판단]. 이슈 관리까지 하면 어려워짐 |
| 16 | Phase / Design Option | L | 4 | 모든 객체에 위상/옵션 소속 필드 + 뷰 필터. 데이터 모델 초반 설계에 넣지 않으면 후반 추가가 매우 고통 |
| 17 | 링크 모델·참조(외부 모델 연결, 좌표 공유) | I | 3 | 좌표계·변환·갱신 감지 |
| 18 | API / 스크립팅 (Revit API, Dynamo 대응) | I | 2~3 | 오픈소스는 오히려 강점: IfcOpenShell Python API가 공개·성숙 [2차] |
| 19 | 대형 모델 성능 (수십~수백 MB, 수만~수십만 객체) | I (공공·대형 프로젝트 M) | 5 | 인스턴스 렌더링, LOD, 공간 인덱스, 증분 갱신. FreeCAD README조차 대형 모델 2D 출력 미최적화라고 인정 [확인] |
| 20 | 재료·렌더링·시각화 | L | 2~3 | Blender 기반이면 우위 |
| 21 | **.rvt/.rfa 읽기·쓰기** | M (전환 시나리오 한정) / 그 외 L | 5 | 4절 참조. 독점 포맷 + 법적 위험 |
| 22 | 에너지·일조 해석, 실내 환경 | L | 4 | 별도 해석 엔진 |
| 23 | 클라우드 협업·코멘트·이슈 (BCF) | I | 2~3 | BCF 표준 존재 |

**MVP 필수 묶음(내 제안)**: 1·2·3·4(제한적)·5·6·7·8·9·10. 즉 "IFC 네이티브 모델링 + 호스팅 + 라이브 갱신 도면 + 시트 + 일람표 + IFC/DWG". 패밀리 에디터(11)와 워크셰어링(12)은 MVP에서 빼되 **데이터 모델에서 확장 여지는 남겨야** 한다.

## 3. 후보 매트릭스 (충족 ○ / 부분 △ / 미충족 ✕ / ? 미확인)

Revit 기능 대비 **내 판정**. 근거가 포럼·커뮤니티뿐인 칸은 "(2)"로 표시.

| 요구 | Bonsai (IfcOpenShell) | FreeCAD BIM | 근거·비고 |
|---|---|---|---|
| 1 3D 요소 모델링 | ○ | ○ | 둘 다 벽·슬래브·구조 요소 제공. 사용자 후기상 Bonsai가 Revit/ArchiCAD에 더 가까움(2) |
| 2 호스팅·조인 | △ | △ | Bonsai: 개구부 공유 등 미완 이슈 언급(2). FreeCAD: 벽 문/창 제거 기능 개선 중 [확인] |
| 3 타입/인스턴스 | ○ | △ | Bonsai는 IFC 타입 네이티브. FreeCAD는 NativeIFC 모드에서 개선 중 |
| 4 양방향 연관성 | △ | △ | Bonsai: 도면은 IFC에서 SVG로 **생성**(동기화 호출 시 갱신), 라이브 뷰가 아님 [2차]. FreeCAD도 TechDraw 갱신 수동적 [판단] |
| 5 2D 도면 생성 | △ | △ | Bonsai: 절단/주석 벡터 + 투영은 **래스터** SVG, 고해상 정밀 도면엔 한계 언급(2). FreeCAD: 2D→DXF/DWG 후 재작업 권장, 대형 모델 미최적화 [확인(README)] |
| 6 시트 | △ | △ | Bonsai: SVG 시트, Inkscape 편집 [2차]. 한국 서식 타이틀 블록은 직접 제작 |
| 7 주석 | △ | △ | Bonsai Drafting 모듈은 "초기 개발 단계" [2차]. 모델 연관 치수 제한 |
| 8 일람표 | ○/△ | △ | Bonsai는 IFC 문서로서 구현 [2차]. FreeCAD 1.1에서 배열 처리 개선 [확인] |
| 9 IFC 입출력 | ○ (최고 수준) | △~○ | IfcOpenShell: IFC2x3·4·4x1·4x2·4x3 파싱 지원 [2차 문서]. buildingSMART 인증 여부 **미확인**. FreeCAD 1.1은 NativeIFC에 2D 선/텍스트/치수 지원 추가 [확인] |
| 10 DWG | ✕~△ | △ | 둘 다 DXF 위주, DWG는 외부 변환기 의존 [판단] |
| 11 파라메트릭 패밀리 | ✕ | △ | Bonsai: 문/창은 Bonsai 전용 속성셋에 저장되어 다른 앱에서 편집 불가 [2차]. FreeCAD: 파라메트릭 객체(Part/Sketcher) 자체는 강점이나 Revit 패밀리 개념과 다름 |
| 12 워크셰어링 | ✕~△ | ✕ | Bonsai: git 기반 IfcGit 개념(로컬 저장소, 리비전) — 실시간 다인 편집 아님 [2차] |
| 13 구조 | △ | △ | 구조 해석 연동 일부. Revit 대비 철근 상세 부족 [판단] |
| 14 MEP | △ | △ | Bonsai 커뮤니티가 MEP 모델링을 홍보하나 자동 라우팅·시스템 수준은 아님 [판단] |
| 15 간섭 | ○ | △ | IfcClash(IfcOpenShell 계열) 존재 [판단, 직접 검증 안 함] |
| 16 Phase/Option | ✕ | ✕ | 확인 못함 [미확인] |
| 18 API | ○ | ○ | Python 완전 공개. Revit API+Dynamo보다 개방적 |
| 19 대형 모델 | △ | ✕~△ | FreeCAD: 대형 모델 2D 출력 미최적화 [확인]. Bonsai는 Viewer 별도 앱이 대형 연합 모델용으로 홍보됨 [확인] — 실측은 없음 |
| 21 .rvt | ✕ | ✕ | 둘 다 .rvt 직접 불가, IFC 경유 |
| 개발 활동 | 활발 (0.9.0post1 2026-10-15, 매일 알파 빌드 2026-11-05~06) [확인] | 활발 (1.1 2026-03 릴리스, 위키 영문 페이지는 아직 개발중 표기가 혼재) [2차] | |

**Bonsai 호환성 위험**: Bonsai 생성 IFC를 Revit에서 열 때 벽 높이·문/창 위치 손실, 일부 요소 누락, 개구부가 Generic Models로 들어오는 사례가 보고됨 [2차, 커뮤니티]. Revit 사용자와 협업하는 순간 이것이 첫 장벽이다.

## 4. .rvt 호환의 현실

| 경로 | 읽기 | 쓰기 | 합법성/위험 | 비고 |
|---|---|---|---|---|
| **Revit 자체 + 공식 API / IFC 내보내기** | ○ | ○ | 안전 | 사실상 유일한 완전 경로. Revit 라이선스 필요 |
| **ODA BimRv SDK (상용)** | ○ 2011 이상 [확인] | △ 최신 버전 RFA/RVT만 [확인] | 계약 필요, 합법 | 생성은 단순 요소(텍스트 노트, 뷰, 레벨, 바닥, 선/호 기반 기본 벽, DirectShape 등) 수준 [확인]. 로드맵에 2026 파일 포맷 지원이 "계획" 표기 [확인, 일정 보장 없음]. 60일 무료 체험만 공개, 가격·멤버십은 페이지에 없음 [확인] |
| **오픈소스 rvt-rs (Apache-2.0, 클린룸 주장)** | △ (CFB/gzip 컨테이너 수준 위주로 추정) | ✕ | 프로젝트 자체는 호환성 목적 역공학, 암호화·접근통제 없음, Autodesk 코드 미사용을 **스스로 선언**함 [확인(NOTICE), 법적 검증은 안 됨] | 2026 저작권 표기, 버전 0.15.0. 기하·파라메트릭 의미까지 복원하는 수준인지는 **미확인** |
| **서드파티 뷰어/변환(Autodesk APS 클라우드 변환 등)** | ○ (변환 서비스) | ✕ | 약관·비용·데이터 외부 전송 | 본 조사에서 미검증 [미확인] |

**법적·기술 위험 요약**
- 한국 저작권법 제101조의4는 정당한 이용자가 **호환에 필요한 정보를 쉽게 얻을 수 없고 불가피한 경우, 호환에 필요한 부분에 한해** 역분석을 허용한다. 단 저작권자의 이익을 부당하게 해치면 예외가 안 된다. 라이선스 계약의 역분석 금지 조항 효력은 학설이 갈린다 [2차 해설, 조문 원문은 미확인]. EU 소프트웨어 지침 6조도 유사 [rvt-rs 인용].
- 즉 "정당한 이용자가 호환 목적으로 최소 범위 역분석"은 이론상 여지가 있으나, **Autodesk EULA 위반 소지**, 그리고 **ODA 코드를 본 사람이 같은 걸 구현하면 오염 주장**이 가능하다. 실제로 Autodesk 포럼에서는 BimRv 코드의 개념조차 포팅하지 않겠다는 발언이 있었다 [2차].
- 기술적으로: 읽기만 해도 Revit의 **파라메트릭 의미(패밀리 규칙, 구속, 의존성)**는 파일 바이트에서 복원하기 매우 어렵고, **쓰기**는 Revit 버전마다 달라지는 내부 스키마 때문에 "Revit이 열었을 때 깨지지 않는" 보장이 거의 불가능하다 [판단]. ODA도 최신 버전만 쓰기 지원하는 게 이 때문으로 보인다 [판단].
- **현실적 권고**: .rvt 직접 파싱 프로젝트는 **연구 트랙**(rvt-rs 협업·기여 수준)으로 분리하고 MVP 의존성에서 제외. 전환은 "Revit 쪽에서 IFC 내보내기 + (유료) ODA 경로 옵션"으로 설계. 법무 검토 없이 상용 제품에 포함하지 말 것.

## 5. 한국 실무 요구

| 항목 | 확인된 내용 | 근거 수준 |
|---|---|---|
| 국토부 건설산업 BIM 기본지침 | 2020-12 발간. 개방형 BIM 적용, 개방형 BIM의 기준을 ISO 16739(IFC)로 보고, **BIM 데이터 제출 포맷은 "PDF 또는 IFC 등"** (IFC 버전 지정 없음, IFC 필수 단정 불가) | [2차 인용] 원문 미확인 |
| 국토부 건설산업 BIM 시행지침(발주자·설계자·시공자편) | 2022-07-26 발간. 성과품 작성·납품·품질검토 기준(목차상 4.1.3 성과품 포맷, 4.1.4 납품 기준). **본문 내용 미확인**, 이후 개정 여부 미확인 | [2차] |
| 조달청 시설사업 BIM 적용 기본지침서 | v1.2로 인용됨. 성과품이 건물·공간·외피 데이터로 구분, **구분마다 원본 파일과 IFC를 함께 제출**, 보고서 포함 (논문 기술). 현행 버전·개정 **미확인** | [2차 논문] |
| 공공 BIM 의무화 일정 | 2022 스마트 건설 활성화 방안: 1,000억 이상 우선 → **2026년 500억 이상 → 2028년 300억 이상** 확대 예정. 2026 이후 실제 이행 상황은 **미확인**, 정부 원문 미확인 | [2차 뉴스·Autodesk 홍보 자료] |
| 세움터 BIM 인허가 | 2021-12 지침: 건축허가(신고)·착공신고·사용승인 대상, 단독주택·공동주택·오피스텔 등은 제외, **IFC 2x3 이상**으로 개요·건축·구조·대지 모델을 각각 제출, 도면은 PDF. 현업은 변환 오류로 DWG 병행 제출 곤란 보도 | [2차] 최신 개정 미확인 |
| BIM 기반 건축 인허가 체계 | 국토부 2025-11-21 연구용역 사전규격 공고. **단계적 의무화 로드맵** 연구 예정. 대상·일정 확정 정보 없음, IFC 언급 없음 | [2차 뉴스] |
| 디지털 건설기준 | 국가 건설기준 3,432 코드를 구조화 데이터로 전환, BIM 연동. **2026년 구축 완료 예정, 완료 후 API 무상 배포** | [2차 뉴스, 2025-12-10] |
| KBIMS | buildingSMART Korea가 2011년 시범 v0.9 공개, 대상은 Revit·ArchiCAD용 라이브러리. 2020 연구는 793개 건축·구조 부재를 IFC로 변환(2,443 엔티티 무손실 검증 보고). **현재 포털 운영·무료 다운로드·최신 IFC 지원 여부 미확인** | [2차 논문] |
| 서울시 | 서울형 설계 BIM 적용지침 2025-07 배포(건축시설물편 포함) | [2차] |
| 한국 도면 표준 | KS F 건축 도면 표기, 국토부 건축도면 작성 기준 등은 **미확인** (이번 조사 범위에서 못 찾음) | 미확인 |
| 블로그(cbilder) 주장 "고시 제2026-104호 2026-03-01 시행" | 공식 출처 없음, **신뢰하지 말 것** | 불신 |

**함의**: 한국 공공 납품은 "원본 + IFC 동시 제출"이 기본 패턴이라 IFC 출력 품질이 필수이며, 이는 오픈소스 IFC 도구의 강점과 맞는다. 단, 발주처가 **원본 파일 포맷(rvt 등)** 또는 특정 소프트웨어 호환을 요구하면 오픈소스가 진입할 수 없다. 발주처별 과업지시서 확인이 필요하다 [판단].

## 6. 메워지지 않는 공백 (우선순위)

1. **.rvt/.rfa 합법·실용적 호환**: 오픈소스는 사실상 없음. 상용(ODA) 아니면 연구 수준.
2. **파라메트릭 패밀리 생태계**: 기술(구속 솔버)도 어렵지만 자산·표준(IFC에 패밀리 없음)이 더 큰 장벽. 한국에서는 KBIMS 라이브러리 현황도 불투명.
3. **라이브 연동 도면 + 대형 모델 성능**: 오픈소스 도면은 "생성형"이고 대형 모델 최적화는 미흡.
4. **다인 동시 협업**: 워크셰어링 대응 부재.
5. **MEP·구조 상세**: 분야별 자동화 깊이 부족. 한국 설비·구조 설계 실무에 직접 영향.
6. **한국 현지화**: 도면 표준 서식, 세움터 제출 검증, 한국어 UI — 후보에 없음 [판단, 일부 미확인].

## 7. 출처

- Revit 패밀리 공식 문서: [Use Formulas in the Family Editor](https://help.autodesk.com/cloudhelp/2025/ENU/Revit-Customize/files/GUID-EEC4A03D-1EE8-49C0-8390-91C0BF649AE4.htm), [Workflow: Creating Loadable Families](https://help.autodesk.com/cloudhelp/2024/ENU/Revit-Customize/files/GUID-09A2439A-FDCB-4233-B08E-6F160D4296DE.htm)
- Revit 워크셰어링: [Synchronize with the Central Model](https://help.autodesk.com/cloudhelp/2025/ENU/Revit-Collaborate/files/GUID-7AF97381-C703-4ACE-8153-E328605CB018.htm)
- ODA BimRv: [제품 페이지](https://www.opendesign.com/products/bimrv.md), [백서](https://www.opendesign.com/files/white-papers/ODA%20BimRv%C2%AE%20SDK.pdf)
- rvt-rs: [NOTICE (docs.rs)](https://docs.rs/crate/rvt/latest/source/NOTICE), [Autodesk 포럼 "You can parse .rvt files"](https://forums.autodesk.com/t5/revit-api-forum/you-can-parse-rvt-files/m-p/13261003)
- IfcOpenShell 릴리스(Bonsai 0.9.0post1, 0.9.1 알파): [GitHub releases](https://www.github.com/IfcOpenShell/IfcOpenShell/releases)
- Bonsai 도면/한계: [OSArch wiki: Bonsai exporting 2D documentation](https://wiki.osarch.org/bonsai-exporting-2d-documentation/), [Bonsai Drawings docs(unstable)](https://docs-unstable.bonsaibim.org/guides/drawings/index.html), [OSArch: 2D drawings in dwg from IFC files](https://community.osarch.org/discussion/1450/), [OSArch: Bonsai "Families"](https://community.osarch.org/discussion/comment/26910/), [OSArch: IFC 호환 문제](https://community.osarch.org/discussion/comment/27604), [OSArch: IfcGit](https://community.osarch.org/discussion/comment/15172/)
- IFC 인증/지원: [OSArch: IfcOpenShell wiki](https://wiki.osarch.org/ifcopenshell/), [OSArch: BlenderBIM IFC Certification?](https://community.osarch.org/discussion/152/)
- FreeCAD: [Release notes 1.1](https://wiki.freecad.org/Release_notes_1.1) (검색 요약으로 확인, 직접 열람은 차단됨), [BIM Workbench README](https://github.com/yorikvanhavre/BIM_Workbench), [OSArch: A medium-term Revit replacement](https://community.osarch.org/discussion/comment/292/), [Yorik: Revit and FreeCAD](https://yorik.uncreated.net/blog/2017-325)
- 한국: [Autodesk Korea BIM 의무화 안내](https://www.autodesk.com/kr/campaigns/bim-mandate), [뉴스토마토: 스마트건설 BIM 의무화](https://newstomato.com/ReadNews.aspx?no=1135980), [캐드앤그래픽스: 건설산업 BIM 시행지침](https://www.cadgraphics.co.kr/newsview.php?sub=lecture03&catecode=9&num=72155), [대한전문건설신문: BIM 기반 건축 인허가 기반 마련](https://www.koscaj.com/news/articleView.html?idxno=320556), [Newspim: 디지털 건설기준](https://newspim.com/news/view/20251210001119), [세움터 BIM 업무지침서 v1.0](https://info.dec-w.com/40), [세움터 BIM 설계기준서 v1.0](https://info.dec-w.com/39), [코딜: 스마트 건설 BIM 편](https://www.codil.or.kr/storyTelling/st13/st13.html), [KoreaScience: KBIMS IFC 변환 연구](https://koreascience.kr/article/JAKO202008540579302.do), [서울시 주택분야 누리집](https://news.seoul.go.kr/citybuild/?p=528232)
- 법: [저작권법 제101조의4 해설(nepla)](https://www.nepla.ai/wiki/it-정보-방송통신/데이터-소프트웨어/컴퓨터프로그램-코드의-역분석-reverse-engineering-3wo9q4m2o8lp), [신김 뉴스레터](https://shinkim.com/kor/media/newsletter/3575)

## 8. 불확실성 / 후속 검증 필요
- 국토부·조달청 지침 **현행 원문**(개정일, IFC 버전, 4.1.3/4.1.4 조문) 직접 열람 필요 (국가건설기준센터/국토교통 BIM 포털/조달청).
- Bonsai/FreeCAD 판정 중 "△/✕"는 다수가 커뮤니티 증언이다. **실제 샘플 건물 1동을 두 도구로 모델링→도면→IFC 왕복→Revit 열기** 하는 스파이크로 검증하길 권한다.
- IfcOpenShell/Bonsai의 buildingSMART 인증 상태, IDS/BCF 지원 범위, Bonsai Web 진행 상황 미확인.
- rvt-rs 실제 기능 범위 미확인(리포지토리 직접 못 열어봄).
- OSS 후보 조사의 다른 후보는 본 문서에서 **판정하지 않았다**(작성 후 목록 수신). 아래는 후보 조사 보고 요약이며 내가 검증하지 않은 것이다.

### 부록: OSS 후보 조사 1차 후보 (조사 보고 그대로, 미검증)
- 저작도구 신흥: pascalorg/editor(MIT, WebGPU, IFC 중심 아님), getcadcraft/bimcraft(Apache-2.0, 파라메트릭 벽/문/창·시트·일람표·간섭, 기여자 4명), francisreboucas/constructcad(GPL-3.0, Rust, IFC4 export, 기여자 3명), ingelibre/ingetrazo(GPL-3.0).
- IFC 라이브러리: IfcOpenShell(LGPL-3.0, 기여자 288), web-ifc(MPL-2.0), ifcplusplus(MIT), Xbim(라이선스 확인중). 웹: That Open components(MIT), xeokit(AGPL), BIMserver(AGPL). 커널: OCCT, Manifold, build123d. 협업: Speckle, IDS.
- 후보 조사 후속: ConstructCAD/BimCraft의 시트·일람표는 README+스크린샷 존재까지만 확인(코드 미실행). **저자 주장**으로만 취급 (상세: 01-oss-candidates.md 3.5·3.6절).
- 내 사전 판단 [판단]: 신흥 저작도구는 기여자 3~4명·v0.x라 Revit 대체 후보가 아니라 **참고 구현/설계 아이디어 출처**. 실제 재사용 기반은 IfcOpenShell(IFC 코어)·OCCT/Manifold(커널)·That Open(웹 뷰어) 조합이 현실적. 라이선스는 GPL/AGPL 혼입 시 배포 형태 제약이 있으므로 코어는 LGPL/MIT/Apache/MPL 위주로 선별 필요.
