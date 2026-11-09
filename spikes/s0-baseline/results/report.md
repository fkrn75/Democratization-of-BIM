# S0 기준선 보고서 (1단계)

기준일 2026-11-07. 범위는 `docs/roadmap.md` 3절 중 RA-01 생성, 측정 도구, 확립된 도구(IfcOpenShell, FreeCAD, Bonsai)까지다. ConstructCAD, BimCraft, ifc-lite, Pascal 은 설치·실행하지 않았다(사용자 승인 대기, 8절). 실행하지 못한 것은 "미시험"으로 적었다.

## 1. 한 줄 요약
- RA-01 은 사양 표와 일치하고 결정적이며 M8(express 규칙 포함) 오류 0건이다. G0 규칙 "RA-01 자체가 M8 실패"에 해당하지 않아 진행했다.
- 측정 도구는 해석적 체적 대조와 음성 시험 14건을 통과했다(두 사례는 첫 실행에서 내 예측이 틀려 기대값을 고쳤다).
- 확립된 도구 중 G0 규칙 1항(한 도구가 M1~M6, E1~E6, C1 또는 C2 + C3 + C5 통과)을 만족한 것은 없다. 신흥 도구가 전부 남아 있으므로 신규 저작 앱 중단 여부는 판정할 수 없다.

## 2. 환경과 출처
- Windows 11, Python 3.12.2(`py -V:3.12`). 주 venv `.venv`: ifcopenshell 0.9.0.post1, ifctester 0.9.0, pytest(EXPRESS 규칙 검증에 필수).
- `tools/ifc-cli/.venv`(gitignore): ifcedit 0.9.0, ifcquery 0.9.0, ifcopenshell-mcp 0.9.0(`ifcmcp`), mcp SDK 1.30.0, pyvista 0.49.2, cairosvg, pypdf. PyPI 공식 패키지, 전역 설치 없음.
- FreeCAD 1.1.4 Windows 포터블 7z 398MB, SHA-256 `4828741fc91ee37fafcdb97a1abacb18b04ba451ac4372d9ff7a7349b36f4d6d`(공식 해시 파일과 일치). `-u/-s` + `FREECAD_USER_HOME` 으로 격리, `%APPDATA%\FreeCAD` 생성 안 됨 확인.
- Bonsai 0.9.0-post1 py311 windows-x64 137MB, SHA-256 `7f37085c37238a6eacd75d63ac20c241f1880b9af9a264d0bf8eb5cc8f766815`(릴리스 digest 일치). Blender 4.5.10 LTS 에 격리된 `BLENDER_USER_RESOURCES` 로 설치. 소스 미열람, 공개 오퍼레이터만 호출.
- 외래 표본: buildingSMART/Sample-Test-Files `IFC 4.0.2.1 (IFC 4 ADD2 TC1)/Simple-Scene/Building-Architecture.ifc` 142,325B, SHA-256 `8790a1e193e82b8e7e7f337ec2633cd40f2120590317a1443503a25b079e2e80`, README 가 CC-BY-4.0 명시. 미커밋, `fetch_samples.py` 가 받고 해시 검증.
- claude 2.1.162, codex-cli 0.147.0.

## 3. RA-01
`build_ra01.py` → `RA-01.ifc`(IFC4, 미터), `RA-01.golden.json`.
- 수량 일치(불일치 시 스크립트 실패): Site 1, Building 1, Storey 2, 외벽 8(층당 4), 내벽 6, 슬래브 3, 기둥 12, 보 8, 문 5, 창 8, StairFlight 1, Space 6, 벽 타입 2, 문 타입 2, 창 타입 2, Opening 13쌍(Voids+Fills), 공간 경계 32.
- 외벽 3층 `IfcMaterialLayerSet`(마감 20/단열 100/콘크리트 200 = 320mm), 내벽 100mm 별도 타입, 전 벽·문에 `DoBIM_Intent`(JSON).
- M8 = 0건. 결정성: GlobalId 를 호출 순서 기반 UUIDv5 로 고정, 헤더 시각 고정. `check_determinism.py` 가 별도 프로세스 2회 재생성해 커밋본과 바이트 일치 확인(2/2).
- 한계: Windows 에서만 확인. 파일이 CRLF 라 Linux 해시 일치는 미확인(S10 "산출물 해시 일치" 기준에 영향 가능). 지오메트리는 단순화(간섭 없는 설계 아님, 계단 개구부 없음). IFC4 `IfcDoorType`/`IfcWindowType` 에 폭·높이 속성이 없어 타입 치수는 커스텀 Pset `DoBIM_TypeParams`.

## 4. 측정 도구 `roundtrip_check.py` 검증
해석: M1 분모=공간 구조·개구부 포함 `IfcProduct` 전체, M3=이름이 `Pset_` 로 시작하는 속성, M4=(벽,개구부)·(개구부,채움) 쌍 둘 다, M6 합격=모든 형상 요소 0.5% 이내. 보조 지표 X1(bbox 중심 1mm)은 G0 규칙에 없으며 M1~M8 이 요소 이동을 못 잡아서 추가(N09 에서 확인).

| 시험 | 결과 |
|---|---|
| RA-01 대 자기 자신 | M1~M6 100%, M8 0건, X1 100% |
| G1: 형상 엔진 체적 대 해석적 체적 | 57개 요소 모두 1e-6 이내(개구부로 깎인 벽 포함) |
| 외래 표본 자기 비교 / IfcOpenShell 재저장 | 전 지표 통과(M4·M5 분모 0, M3=5, M6=13) |
| 음성 시험 14건 | 14/14 기대한 지표만 실패 (`negative_tests.py`, `results/negative_tests.json`) |

음성 시험: 무결함 통과, 외벽 삭제, 문 GlobalId 변경, 벽 전체 IsExternal 반전(7%, M3 실패) 대 5개 반전(2.6%, 통과), 개구부 관계 삭제, 단열층 100→90mm(M5 만), 기둥 높이 +0.6%(M6 실패) 대 +0.4%(M6 통과, X1 만 실패), DoBIM_Intent 삭제(M7 하락), 기둥 프로파일 삭제(M8 포착), 벽 0.5m 이동(X1 만), 클래스 재분류(M2 만), 단위 m→mm 오표기(M5, M6, X1).
정직한 기록: 첫 실행에서 2건이 내 기대와 달랐다. (a) 개구부 없는 벽 삭제는 M4 가 아니라 M5 가 실패(층 재료 소실, 맞는 동작). (b) 개구부 관계 삭제는 M8 도 실패(검증기가 `IfcOpeningElement` 의 `VoidsElements` 카디널리티 위반을 잡음, 맞는 동작). 도구는 고치지 않고 기대값만 정정했다.
`ifcopenshell.validate(express_rules=True)` 는 pytest 가 없으면 예외로 죽는다(도구가 오류 1건으로 세므로 조용히 통과하진 않음). M7 100% 는 의도가 낡았을 수 있다는 뜻이기도 하다.

## 5. 도구별 결과
범례: ○ 확인 / △ 부분 / × 실패 / – 미시험.

### 5.1 IfcOpenShell 0.9.0.post1
- M1~M6, M8: ○(ifcedit 무변경 호출 후 저장본 비교, RA-01·표본).
- E1: ○ `geometry.edit_object_placement`(`--should_transform_children true`) 1회. W-E-01, D-01, WIN-01, WIN-02 만 이동. 인접 벽·슬래브는 갱신 안 됨(저수준 API 는 조인을 모름). 유효성 오류 0.
- E2: △ 3단계 수동(타입 Pset, 인스턴스 `OverallWidth`, 문·개구부 프로파일 `XDim`). 타입→인스턴스 전파와 개구부 재계산은 도구가 하지 않음.
- E3: △ 4단계 수동(층 Elevation, 층 배치, 1층 벽·기둥 압출 깊이 13개). 2층 이상 32개 요소는 +0.3m 상승. 벽 높이 자동 추종 없음.
- E4: △ `ifcopenshell.draw` 로 평면·단면·입면 SVG(벡터) 0.2~0.5초. 갱신은 수동 재생성. `ifcquery plot`/`ifc_plot` 은 0.9.0.post1 에서 어떤 파일이든 AttributeError(`cells=True` 경로), 라이브러리 직접 호출 + `cells=False` 로 우회.
- E5: △ SVG 1:100 에서 12.32m = 123.2mm 정확. SVG→PDF 는 IfcOpenShell 에 없음(cairosvg 는 Cairo DLL 없어 실패). Edge 헤드리스 인쇄 PDF 는 A1(841×594mm), 이미지 XObject 0(벡터), 2쪽으로 넘침. 한글·제목 블록 미시험.
- E6: △ CLI 에 일람표 명령 없음(`ifcquery schedule` 은 4D). 라이브러리 selector 로 가능. E2 후 값 추종은 내가 속성을 바꿨기 때문이지 자동 전파가 아님.
- C1 3/6(E1~E3). C2 실측 1/6(E1 을 `ifc_edit` 로 수행, CLI 결과와 바이트 동일; E2·E3 미실행; `ifc_plot` 같은 버그; `ifc_render` 이미지 반환 3.2초). C3 ○. C4: JSON 결과·오류 ○(오류 시 미저장), `--dry-run` 은 검증 안 함(없는 ID 도 ok:true), 트랜잭션·undo 없음, MCP 는 메모리 모델 + 명시적 `ifc_save`. C5: Codex 등록만 ○(임시 CODEX_HOME), 호출 미시험(인증). Claude: 등록 인자 수락, 호출 미시험(중첩 `claude -p` "Not logged in").
- MCP 시동 1.65초, 도구 25개, 어노테이션 전무.
- 실패 원인 구분. 도구: plot 크래시, dry-run 무검증, `ifc_edit` 가 JSON bool 거부(`'bool' object has no attribute 'lower'`, 문자열 "true" 로 우회), `ifcmcp --help` cp949 크래시(`PYTHONUTF8=1` 필요), `ifc_validate` pytest 누락, `ifc_render` pyvista 누락. 조작(내 실수): E3 첫 실행에서 `should_transform_children` 을 false 로 줘 2층이 안 따라옴(true 로 재실행).

### 5.2 FreeCAD 1.1.4 (FreeCADCmd)
- M1~M8: × 측정 불가. `importers/importIFC.py` 가 `settings.USE_BREP_DATA` 를 쓰는데 번들 ifcopenshell 0.8.4 에 없음. RA-01·표본 모두 같은 오류(2/2), 조작 무관.
- 대체 시험: Arch API 로 벽+창+층 생성, IFC 내보내기 0.3초. 내보낸 IFC 에 EXPRESS 규칙 1건 위반(`Curve2D` 에 `IfcIndexedPolyCurve`). E1 유사: 벽 600mm 이동 시 호스트 창·개구부 동행, 벽 체적 5.508 유지. E3 유사: 층 `Height` 3300→3600 해도 벽 높이 3300 그대로.
- E4: △ 콘솔 TechDraw 평면 뷰 생성·recompute·DXF 출력 됨(단순 벽 하나, 엔티티 5개). 벽 길이 변경 후 recompute 하면 DXF 변화. 단면·IFC 벽·SVG 페이지(`PageResult` 는 콘솔에 없음) 미시험.
- E2, E5, E6: –. C1 △(스크립트 실행기이지 커맨드 CLI 아님). C2 –(freecad-mcp 는 GUI 애드온 전제라 설치 안 함). C3 ○. C5 –.
- 실패 원인: 가져오기 실패는 도구(공식 릴리스 번들 불일치). TechDraw 템플릿 경로·PageResult 는 조작 학습 문제.

### 5.3 Bonsai 0.9.0-post1 (Blender 4.5.10 --background)
- M1~M6, M8: ○(열기→저장, RA-01·표본). Bonsai 는 메모리의 IFC 를 다시 쓸 뿐이라 편집 없는 왕복 증거로는 약함.
- 헤드리스 ○: 확장 활성화 3.8초, 불러오기 0.5초. 오퍼레이터 1310개 중 `poll()` 통과 1110개(85%)이나 동작 비율이 아님.
- E1: △ 벽 오브젝트 +Y 0.6m 이동 후 저장하면 IFC 반영 ○, 문·창은 따라오지 않음(1개 요소만 이동). UI 기본 동작과 같은지 미확인.
- E4: △ `add_drawing`→`activate_drawing`→`create_drawing` 으로 평면 SVG(path 179, text 7) 0.15초. `activate_drawing` 없이 호출해 첫 시도 실패("No active drawing", 조작). E1 후 재생성·단면 미시험.
- E5: △ `add_sheet`/`create_sheets` 로 A1 시트 SVG. PDF 미시험(Inkscape 없음).
- E2, E3, E6: –. C1/C2 ×(전용 CLI·MCP 없음). C3 △. C4: 오퍼레이터는 `{'FINISHED'}` 뿐. C5: 해당 없음.

### 5.4 요약표
| 도구 | M1~M6, M8 | E1 | E2 | E3 | E4 | E5 | E6 | C1 | C2 | C3 | C5 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| IfcOpenShell | ○ | ○ | △ | △ | △ | △ | △ | 3/6 | 1/6 | ○ | Codex 등록만 |
| FreeCAD | × | △(대체) | – | △(대체) | △ | – | – | △ | – | ○ | – |
| Bonsai | ○(약함) | △ | – | – | △ | △ | – | × | × | △ | n/a |

## 6. 미시험
M9(계정·약관 전). Revit(D5, 납품 전 재시험). Linux·DISPLAY 없는 환경(S10)과 RA-01 해시의 Linux 일치. Claude/Codex 실제 도구 호출(C5), 데스크톱 앱 2종, 최신판 CLI 대비. FreeCAD RA-01 기반 전부·freecad-mcp·TechDraw 단면/한글. Bonsai E2·E3·E6·PDF·E1 후 도면 재생성. IfcOpenShell E2·E3 의 MCP 경유, 한글 PDF. 신흥 도구 전부. 도구별 학습시간 정밀 기록 없음.

## 7. 재현
```text
cd spikes/s0-baseline
py -V:3.12 -m venv .venv ; .venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python build_ra01.py
.venv\Scripts\python check_determinism.py
.venv\Scripts\python roundtrip_check.py RA-01.ifc RA-01.ifc --golden RA-01.golden.json
.venv\Scripts\python negative_tests.py
.venv\Scripts\python fetch_samples.py
```
IfcOpenShell 도구 시험은 `tools/ifc-cli/.venv` 에 `ifcedit ifcquery ifcopenshell-mcp[mcp] pytest pyvista` 설치 후 `run_ifcopenshell_edits.py`, `draw_ifcopenshell.py`, `mcp_probe.py`, `mcp_edit.py`. FreeCAD 는 `run_freecad.sh`(환경변수 `FC`, `PROFILE`), Bonsai 는 `bonsai_*.py` 를 `blender -b --factory-startup --python`(`BLENDER_USER_RESOURCES` 짧은 격리 폴더). 환경 메모: Blender 확장 설치가 긴 경로(약 150자)에서 실패, 짧은 경로에서 성공(S7·S11 Windows 항목에서 경로 길이 확인).

## 8. 신흥 도구 설치에 필요한 승인 (설치·실행 안 함, 공개 메타 조회만)
공통: 관리자 권한·시스템 PATH·레지스트리·전역 설치 없음. 도구마다 빈 폴더 + 해시 검증, 사용자 프로필 분리. 이 PC 에 Docker 가 PATH 에 있어(동작 미확인) Linux 컨테이너 실행을 우선 제안. 소스 열람 금지(ADR-0003) 유지, 실행만.

| 도구 | 라이선스 / 출처 | 파일 | 해시·비고 | 격리 제안 |
|---|---|---|---|---|
| ConstructCAD v0.9.1 | GPL-3.0, francisreboucas/constructcad, 생성 2026-10-18, 별 177 | `constructcad-v0.9.1-x86_64-pc-windows-msvc.zip` 27MB(msi 보다 권장) | zip `bbfd0cacaaec5e41039bc02449b3db1728dcb85e429275eeea06f6faf9fc9cce`, msi 24MB `0247ae8bffc7a9c1da80b5d6d665d091eb7cc73aba3155dbbf8d0d338998fc84`, Linux tar.gz 33MB `755dbebfdf6c3ca977574c22cfda0d8dd77a4b9a1c749ba2ea4048ff426f2b6f` | zip 을 작업 폴더에 풀어 실행. 설정 위치 미확인이라 별도 로컬 계정/VM, 또는 컨테이너의 Linux `ccad`. msi 는 시스템 설치라 비권장 |
| BimCraft v0.8.1 | Apache-2.0, getcadcraft/bimcraft(생성 2026-10-13). 동명 `IPedrax/BIMCraft` 혼동 주의 | `bimcraft-browser-edition-v0.8.1.zip` 46MB | `93ccdf2500eab65a001b98594eeda63c6ddee40e98e3283b66863abbe2a3673a` | 풀어서 127.0.0.1 `python -m http.server`, 브라우저 별도 프로필. `@getcadcraft/mcp` npm 404 재확인 |
| ifc-lite | MPL-2.0, npm `@ifc-lite/cli` 0.45.1, `@ifc-lite/mcp` 0.29.0, 관리자 1명(louistrue), 패키지 생성 2026-03 | npm 패키지(의존성 다수) | 최신 버전에 preinstall/postinstall 없음 | npx 금지. 컨테이너에서 `npm install --ignore-scripts --prefix <빈 폴더>`, 버전 고정 + lockfile |
| Pascal | MIT, npm `@pascal-app/cli` 1.0.6, 관리자 2명, 생성 2026-08 | npm 패키지(MCP SDK 의존) | lifecycle 스크립트 없음 | 위와 동일. IFC 왕복만 확인, 후순위 |

승인 결과(2026-11-07): 신흥 도구는 컨테이너에서 시험한다. 순서는 ConstructCAD → ifc-lite → BimCraft → Pascal.

## 9. 설계 문서와 어긋난 발견
1. ADR-0011 결정 3: `ifcmcp` 는 도구 25개 모두 어노테이션 없음, 편집은 범용 `ifc_edit` 하나, 메모리 세션. ADR 이 기각한 "MCP 도구 단 1개" 구조와 비슷하고 "명시적 핸들" 권고와 반대. 참조 구현이 아니라 반면교사.
2. Bonsai 는 전용 CLI·MCP 없음(ADR 영향 문구와 부합). 다만 `--background` 도면·시트 생성은 동작.
3. IFC4 에는 타입 수준 문·창 치수가 없다(IFC2x3 `*Style` 에만). 로드맵 E2 "문 타입 폭"은 도구 정의 파라미터에 의존. ADR-0008 이 타입 치수 저장처를 정해야 한다.
4. `ifcmcp` 는 PyPI `ifcopenshell-mcp` 의 실행 파일명(`ifcmcp` 패키지는 404). `ifcedit`·`ifcquery` 는 `ifc5d` 의존성을 끌어온다. "API 350개 이상"은 다 확인하지 않음.
5. FreeCAD 1.1.4 공식 포터블은 그대로는 IFC 를 열지 못한다. 판정표에 FreeCAD 를 왕복 후보로 두기 전에 수정 여부(주간 빌드 등, 미시험) 확인 필요.
6. M1~M8 은 요소 이동을 못 잡는다(X1 추가). 로드맵 합격 기준에 위치 지표를 넣을지 사용자 판단 필요. M8 EXPRESS 검사는 pytest 요구.
7. Windows 에서 `.ifc` 가 CRLF 로 쓰인다. Linux 해시 일치 확인 전에는 OS 간 해시 기준으로 삼지 말 것(S10).
8. 저수준 편집은 `DoBIM_Intent` JSON 을 갱신하지 않아 M7 100% 여도 의도와 형상이 어긋난다(E1 후 `axis` 낡음). ADR-0005 "의도 생존"은 값 보존이 아니라 일관성으로 정의해야 한다.

## 10. G0 권고 (1단계 사실만)
1. 진행 가능: RA-01 의 M8 통과. G0 "RA-01 자체가 M8, M9 실패" 항목에서 M8 은 해소, M9 는 미시험이라 규칙은 절반만 확인.
2. 확립된 도구 3종은 규칙 1항(신규 저작 앱 중단 조건)을 충족하지 못했다. IfcOpenShell 은 왕복·저수준 편집은 확실하나 저작 의미(조인, 타입 전파, 라이브 도면)가 없고, FreeCAD 1.1.4 는 IFC 가져오기부터 실패, Bonsai 는 왕복·도면은 되나 CLI·MCP 가 없어 ADR-0011 의 C1 또는 C2 를 만족할 수 없다.
3. 이 결과만으로 "신규 저작 앱 필요"를 결론내리지 말 것. ConstructCAD, BimCraft, ifc-lite, Pascal 이 미시험이고 그 결과가 G0 의 결정적 입력이다.
4. E4(라이브 도면)·E2(타입 전파)에서 확립된 도구가 모두 수동·부분이라는 점은 "왕복은 통과하나 E4 전부 실패 → 경로 F 유지" 행에 가까운 모양이나, 신흥 도구 결과 전에는 확정하지 않는다.
5. 다음 순서: ConstructCAD → ifc-lite → BimCraft → Pascal.

# S0 2단계 보고: 격리 컨테이너 시험

**G0 결정(2026-11-07, 사용자): 경로 F 유지, MPL-2.0 유지, S1 진행.** 아래 8-8은 결정 전 권고 원문이다.

### 8-2. 2단계 개요: 격리 컨테이너 시험

신흥 도구 4종을 사용자 승인에 따라 Docker Linux 컨테이너에서 시험했다. 베이스 이미지는 digest 고정(`debian:12-slim@sha256:734a72b3…`, `node:22-slim@sha256:5ec23b7c…`, `python:3.12-slim@sha256:4840d514…`)이다. 시험 실행은 `--network none --cap-drop=ALL --security-opt no-new-privileges --read-only`, 메모리 2~3g, CPU 2, 비 root(uid 10001)이고, RA-01 은 읽기 전용, 출력 폴더 하나만 쓰기 가능하다. 네트워크는 이미지 빌드 단계에서만 썼다. ConstructCAD 소스는 읽지 않았다. 재현 파일은 `spikes/s0-baseline/phase2/`(Dockerfile 6종, `run_ccad.sh`, `run_node.sh`, 드라이버 스크립트)와 `results/phase2/`(호출 기록, 비교 결과, 락파일)에 있다. Docker 이미지는 시험 후 삭제했고 `phase2/build_images.sh` 로 다시 만들 수 있다.

| 도구 | 출처, 파일, 해시 | 라이선스 |
|---|---|---|
| ConstructCAD v0.9.1 | `constructcad-v0.9.1-x86_64-unknown-linux-gnu.tar.gz` 33MB, sha256 `755dbebfdf6c3ca977574c22cfda0d8dd77a4b9a1c749ba2ea4048ff426f2b6f`(릴리스 digest와 일치) | GPL-3.0 |
| ifc-lite | `@ifc-lite/cli` 0.45.1 + `@ifc-lite/mcp` 0.29.0, 설치 312개 패키지, lockfile 기록, lockfile 상 `hasInstallScript` 0건 | MPL-2.0 |
| BimCraft v0.8.1 | `bimcraft-browser-edition-v0.8.1.zip` 46MB, sha256 `93ccdf25…673a`(릴리스 SHA256SUMS와 일치) | Apache-2.0 |
| Pascal | `@pascal-app/cli` 1.0.6, 설치 15개 패키지, lockfile 기록, lifecycle 스크립트 0건 | MIT |

### 8-3. ConstructCAD v0.9.1 (가장 정밀하게 시험)

**구조**: 하나의 커맨드 레지스트리(`ccad commands` = 211개: edit 163, query 37, file 11)에서 `ccad run|script|mcp` 가 같이 나온다. 모든 `ccad` 호출은 컨테이너 안에서 실행했고 화면·GPU 없이 동작했다(C3 ○, Linux 헤드리스).

**RA-01 네이티브 모델링(roadmap "RA-01 전 과정 CLI")**: 명령 81개, 호출 5회, 약 3.4초(호출당 docker 시동 포함 0.7초). 레벨 3개(RF 추가), 타입 직접 정의(`WT-EXT-320` 3층 320mm, `WT-INT-100`, 문 2종, 창 2종, 기둥 400, 보 300x400, 슬래브 200), 벽 14, 문 5, 창 8, 슬래브 3, 기둥 12, 보 8, 계단 1, 공간 6을 만들었다. 내보낸 IFC: M8 오류 0건, `IfcProduct` 클래스 수는 `IfcStair`(조립 계단)와 층 3개를 제외하고 사양과 일치, Voids·Fills 13쌍, 벽 총 체적 89.583(RA-01과 정확히 일치). 한계: 공간 경계(`IfcRelSpaceBoundary`) 0건, 사용자 정의 Pset(`DoBIM_Intent`)을 쓸 수단이 없음, 문·창 형상은 타입 정의에서 재구성되어 RA-01 체적과 다름.

**외래 IFC 왕복**(RA-01을 `ifc_import` 후 `ifc_export`, M1~M8·X1):

| M1 | M2 | M3 | M4 | M5 | M6 | M7 | M8 | X1 |
|---|---|---|---|---|---|---|---|---|
| 77.0% 실패 | 91.7% 실패 | 16.1% 실패 | 0% 실패(엄격) | 100% 통과 | 59.6% 실패 | 0%(기록) | 0건 통과 | 78.9% 실패 |

분해: 누락 GlobalId 는 개구부 13, Site·Building·Storey 4(새로 발급). 벽·기둥·문·창·보·슬래브 GlobalId 는 보존. 슬래브 3개가 슬래브 2 + 지붕 1로 재분류. M4 는 Voids 13/13·Fills 13/13 개수는 같지만 개구부 GlobalId 가 새로 발급되어 쌍 일치가 0%. M3 은 보·기둥·문·창·슬래브·공간의 Pset 전부 소실(벽은 56개 중 28개 보존), M6 은 문·창·보 전부와 공간 2개의 형상이 재구성되어 불일치. 즉 **외래 IFC 를 무손실 통과시키는 도구가 아니다**(가져오기 전용 성격).

**편집 E1~E6**(네이티브 RA-01 위에서, CLI 와 MCP 로 각각 실행):

| ID | 결과 |
|---|---|
| E1 벽 600mm 이동 | ○ `elements_move` 1회. 같은 벽의 문·창이 자동 동행(변경 목록 4개 요소), **방 3개의 경계·면적이 자동 갱신**. 인접 벽 파라미터는 변경 없음. 0.7초 |
| E2 문 타입 폭 0.9→1.0 | ○ `type_set` 1회로 DT-01 문 2개와 호스트 벽 2개의 체적이 자동 재계산(개구부 재계산 포함). 문 일람표도 1.00으로 갱신 |
| E3 2층 층고 3.3→3.6 | ○ `level_set`. 상부 요소 이동(IFC 로 45개 요소), 1층 벽(상단이 2층에 부착) 높이 3.3→3.6, 2층 벽 3.6→3.0(상단은 RF 부착), 기둥 12개 길이 갱신. 상단이 "Unconnected"인 벽은 따라가지 않음(가져온 모델의 경우) |
| E4 평면·단면 + E1 후 갱신 | ○ 평면 1:50·단면 SVG 벡터(`<line>` 2,991/2,333개). 뷰는 저장된 객체이며 벽을 옮긴 뒤 같은 뷰를 다시 내보내면 별도 재생성 명령 없이 갱신됨(평면 SVG 선 2,991→3,009). 소요 약 0.7초. 평면에서 12.32m 벽 = 246.4mm 로 1:50 정확 |
| E5 A1 시트 PDF | △ `view_set size=A1`, 시트 PDF 841×594mm, 이미지 XObject 0(벡터), 1쪽. **한글은 PDF 에서 "?"로 깨짐**(PDF 가 Helvetica 만 사용, CJK 글꼴을 설치한 이미지에서도 동일 → 환경 문제가 아니라 도구 한계). 같은 시트의 SVG 는 한글 텍스트를 유지. 시트 생성 기본 크기는 A3 |
| E6 일람표 | ○ `schedule_table`/`schedule_export_csv`. E2 후 폭 1.00 자동 반영 |

**제어 C1~C4**: C1 6/6(CLI만). C2 6/6(MCP만; E1~E6 전부 실행, E1 산출은 CLI 와 헤더 외 동일). C3 ○(컨테이너, 화면·GPU 없음; CPU 3D 뷰 PNG 600x523 0.7초). C4: 오류는 구조화된 JSON(`not_found`, `invalid_arguments`), 편집 1건은 undo 1단계이며 모델을 무효로 만드는 편집은 거부, 스크립트 중간 실패 시 `--save-as` 파일이 생성되지 않음(파일 단위 전부 아니면 무), 같은 세션의 `undo` 동작. **dry-run 없음**. C5: **미시험**(인증을 컨테이너에 넣지 않음).
**MCP 서버 형태**: 직접 도구 211개, `tools/list` 298KB, 모든 도구에 `readOnlyHint`·`destructiveHint` 어노테이션(읽기 전용 37개), 시동 0.3초(docker 포함). 도구 11개의 입력 스키마가 5,000바이트를 넘고 최대 8,194바이트다.

### 8-4. ifc-lite (`@ifc-lite/cli` 0.45.1, `@ifc-lite/mcp` 0.29.0)

- **왕복**: `export --format ifc` 후 RA-01 대비 M1~M6 100%, M7 100%(의도 보존), M8 0건, X1 100%. 이 PC의 IfcOpenShell 왕복과 같은 결과다.
- **CLI**: 약 50개 명령(info, query, schedule, ids, clash, create, mutate, run, diff, merge 등). `doctor` 0.7초. 도면·단면·시트·PDF 명령 **없음**(E4·E5 ×).
- **저작**: CLI `create` 는 요소별 별도 파일을 만든다(시험하지 않음). `run` 스크립트 샌드박스에서 `bim.create` 는 `project` 하나만 쓸 수 있었다. `bim.store.add*`(벽, 문, 창, 기둥, 보, 슬래브, 공간, 타입, 재료)는 **기존 모델에 추가**하는 방식이며, RA-01 에 요소 7종을 추가해 M8 0건, Voids·Fills 15쌍, 벽 연결 1건이었다. 새 문·벽 체적 계산 정상(벽 2개 3.408, 1.254).
- **E1**: ○ MCP `edit_element_geometry` `transform move` 1회, 문 1·창 2 동행, M8 0. CLI 로는 `run` 스크립트에서 위치 점의 id 를 직접 지정해야 한다(저수준).
- **E2**: △ MCP `edit_hosted_element` 로 문 인스턴스 폭 변경은 되나 **타입 전파 없음**(타입이 인스턴스에 치수를 갖지 않음). 더 중요한 점은 **산출 IFC 가 M8 오류 8건**(역참조 `ShapeOfProduct` 0개, 고아 표현 객체)으로 무효가 된다(도구 결함으로 판단, 입력은 도구가 허용한 값만 썼다). SDK `setPositionalAttribute` 로 속성 값을 raw 숫자로 쓰면 `IfcValue` 선택형이 깨져 M8 오류 1건(입력 방식 문제이나 방어 장치 없음).
- **E3**: △ MCP `size` 로 1층 벽·기둥 높이 변경 ○(13개 체적 갱신, M8 0). 층 고도 이동은 MCP 에 없음(저수준 SDK 로는 45개 요소 이동 ○, M8 0).
- **E6**: ○ `schedule --preset door`/`--columns`. 속성 값을 바꾸면 표에 반영. 문 폭(`Width`) 프리셋 열은 비어 있음(수량 기반이라 이 모델엔 값 없음).
- **제어**: C1 △(E1~E3 는 스크립트 경유, E6 ○, E4·E5 ×), C2 E1 ○ E2 △ E3 △ (E6 MCP 경유 미시험), C3 ○, C4: MCP 도구 125개, 시동 1.44초, `tools/list` 144KB, **어노테이션 없음**(`readOnlyHint` 0개, 대신 `--read-only` 플래그), `place_door` 가 겹치는 개구부를 **원자적으로 거부**하고 한 단계 `mutation_undo` 제공, `mutation_batch` 는 문서상 비원자적, 편집 dry-run 없음. C5 미시험.

### 8-5. BimCraft v0.8.1, Pascal 1.0.6

- **BimCraft**: 정적 웹앱(1,890개 파일). 컨테이너의 headless Chromium 으로 로드 4.5초. 기본 headless 에서는 `THREE.WebGLRenderer: Error creating WebGL context` 가 나고 SwiftShader 옵션(`--use-angle=swiftshader --enable-unsafe-swiftshader`)을 주면 로드됨. RA-01 을 파일 입력으로 올리자 파일 카드가 정상 표시(스키마 IFC4, 프로젝트 RA-01, 작성 도구 표시)됐다. 릴리스에 `assets/js/app-devices-studio.js`, `skill-packs.js`, `app-addons-account.js` 등 참조 파일이 없어 404 가 난다(앱은 동작). 앱 안내문상 IFC 열기 모드에서 바꿀 수 있는 것은 이름·태그·속성값뿐이고 형상 편집은 새 건물 모드에서만이라 E1~E3 에 해당하는 기능은 확인하지 못했다(안내문 기준, 실행 확인은 하지 않음). "Save as IFC" 는 변경이 없을 때 파일을 내려주지 않아 **저장 왕복은 미시험**. 전용 CLI·MCP 없음(`@getcadcraft/mcp` npm 404 재확인). C1 ×, C2 ×, C3 △(브라우저와 소프트웨어 GL 필요).
- **Pascal**: npm 패키지는 CLI 와 MCP 연결 도구이고 **웹 편집기 런타임은 첫 실행 때 네트워크로 내려받는다**. 오프라인에서 `doctor`, `mcp config`, `plugin list` 는 동작, `status` 는 "런타임 미설치"(홈 디렉터리 쓰기 필요). 런타임을 내려받는 것은 벤더 서버의 추가 코드를 받는 일이라 승인 범위 밖으로 보고 **IFC 왕복·편집 전부 미시험**.

### 8-6. Linux 해시 일치

`build_ra01.py` 를 `python:3.12-slim`(ifcopenshell 0.9.0.post1)에서 3회 재생성: 3회 모두 `46e51c5d…a692` 로 동일(Linux 내 결정적). Windows `49bf1c83…b969` 와 **바이트는 다르다**(줄바꿈 CRLF vs LF, 속성 순서: 단위 지정 순서, 층·집합 순서). `roundtrip_check` 로 의미 비교하면 M1~M6, M8, X1, G1 전부 통과(GlobalId 동일). 골든 JSON 도 해시가 다르다.

### 8-7. 도구별 요약 표 (실측)

| 도구 | M1~M6, M8 (RA-01 왕복) | E1 | E2 | E3 | E4 | E5 | E6 | C1 | C2 | C3 | C4 | C5 | 라이선스 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ConstructCAD | × (M1 77, M3 16, M6 60) | ○ | ○ | ○ | ○ | △(한글 PDF ×) | ○ | 6/6 | 6/6 | ○ | 구조화 오류, 원자적, undo, dry-run ×, 어노테이션 ○ | 미시험 | GPL-3.0 |
| ifc-lite | ○ (전부 통과) | ○ | △(M8 8건) | △ | × | × | ○ | △ | E1 ○, E2·E3 △ | ○ | 어노테이션 ×, 겹침 거부 ○, 일괄은 비원자적 | 미시험 | MPL-2.0 |
| BimCraft | 미시험 | – | – | – | – | – | – | × | × | △ | – | 미시험 | Apache-2.0 |
| Pascal | 미시험 | – | – | – | – | – | – | – | 미시험 | – | – | 미시험 | MIT |
| (참고) IfcOpenShell | ○ | ○ | △ | △ | △ | △ | △ | 3/6 | 1/6 | ○ | JSON 오류, dry-run 무검증 | Codex 등록만 | LGPL |
| (참고) Bonsai / FreeCAD | ○(약함) / × | △ / △ | – | – / △ | △ | △ / – | – | × / △ | × / – | △ / ○ | – | – | GPL / LGPL |

### 8-8. G0 판정 (roadmap 3절 판정표, 라이선스 MPL-2.0 확정 반영)

| 규칙 | 적용 결과 |
|---|---|
| 1. 한 도구가 M1~M6, E1~E6, C1 또는 C2 + C3 + C5 통과하고 라이선스 수용 → 신규 앱 중단 | **충족한 도구 없음.** ConstructCAD 는 E1~E6, C1, C2, C3 이 통과(E5 한글 PDF 제외)하나 외래 IFC 왕복 M1~M6 실패, C5 미시험, **GPL-3.0 이라 MPL-2.0 프로젝트에 포함·포크 불가**. ifc-lite 는 M1~M6 통과, 라이선스 수용 가능(MPL-2.0)하나 E4·E5 ×, E2 에서 M8 결함, C5 미시험 |
| 2. ConstructCAD 만 기준 통과 + 사용자가 GPL 불가 → F 유지, 구조 사례로 기록 | **적용**. 기준 통과 자체가 안 됐고(왕복 실패, C5 미시험), 설령 통과해도 GPL 불가이므로 F 유지. 구조(단일 레지스트리 + CLI + MCP 생성, 파라메트릭 타입, 뷰·시트·일람표 자동 갱신, 편집 단위 undo)는 "검증된 구조 사례"로 기록. 소스 참조 금지 유지 |
| 3. 왕복은 통과하나 E4·연관 주석에서 전부 실패 → F 유지, S1 진행 | ConstructCAD 는 E4 성공이라 이 행에 정확히 해당하지 않음. 확립된 도구(IfcOpenShell, Bonsai, FreeCAD)와 ifc-lite 는 왕복은 되나 E4 가 수동·부분·없음이어서 이 행에 가깝다 |
| 4. RA-01 자체가 M8, M9 실패 → 수정 전 진행 금지 | RA-01 M8 통과(1단계). M9 미시험 |
| 5. ifc-lite 가 왕복과 생성에서 IfcOpenShell 과 대등 → ADR-0004 Rust + ifc-lite 대안 재검토 | **부분 충족**. 왕복은 대등(전부 통과, 의도 보존). 생성은 기존 모델에 추가하는 API 로 M8 0건 확인, 빈 모델에서 시작하는 생성은 미시험, 도면·시트 기능 없음, E2 에서 M8 결함 발견. 라이선스는 수용 가능. ADR-0004 재검토의 **입력으로 올리되 단정하지 않는다** |
| 6. Revit 판정 불가 → "Revit 미확인" | 유지 |

**권고**: **경로 F 유지(신규 저작 앱 중단 아님)**, S1 진행. 근거는 신흥 도구 중 GPL 이 아닌 후보(ifc-lite, BimCraft, Pascal)가 도면·시트·타입 전파를 제공하지 못했거나 시험하지 못했고, 요건을 가장 많이 충족한 ConstructCAD 는 GPL-3.0 이라 사용할 수 없기 때문이다. 다만 아래 미시험 항목이 해소되기 전에는 "기여 또는 포크 대상 없음"을 최종 확정하지 말 것.

### 8-9. 미시험

C5(Claude·Codex 실제 호출, 인증을 컨테이너에 넣지 않음), M9, Revit(D5), BimCraft 저장 왕복·편집·내보내기, Pascal 전 항목(웹 런타임 다운로드 필요), ifc-lite E2·E3·E6 의 MCP 전수 및 빈 모델에서의 생성(`create` CLI 요소별 파일, 병합), ConstructCAD 의 GUI 전용 기능(GUI 실행 안 함, 시험 범위가 아님)과 Claude·Codex 에서의 도구 탐색 성공률, 토큰 비용.

### 8-10. 설계 문서와 어긋난 발견 (2단계)

1. **ADR-0011 결정 3(메타 도구 8개)**: ConstructCAD 는 반대로 211개 직접 도구를 노출하고 `tools/list` 가 298KB 이며, 그래도 위험 어노테이션(읽기 전용 37개, 파괴성 28개)을 갖췄다. 도구 11개가 스키마 5,000바이트를 넘어 ADR 이 인용한 Codex `tool_input_schema_max_bytes` 기본값(공식 샘플 기준)과 충돌할 수 있다. 이는 S11 에서 실측해야 할 위험이다. 반면 **결정 1(레지스트리 하나에서 CLI·MCP 생성, 같은 결과)은 실측으로 확인**: 같은 명령 시퀀스의 CLI 와 MCP 산출 IFC 가 헤더만 다르고 같았고, E1~E6 이 두 경로에서 같은 값을 냈다.
2. **ADR-0011 의 dry-run 요구**: ConstructCAD, ifc-lite 모두 편집 dry-run 이 없다. ConstructCAD 는 거부(무효 편집), 스크립트 전부 아니면 무 저장, undo 로 대신한다. ifc-lite 는 hosted 편집을 원자적으로 거부하고 mutation_undo 를 제공한다.
3. **S2 "외래 IFC 를 무손실 통과"**: ConstructCAD 가져오기는 GlobalId 일부(개구부, 공간 구조)를 새로 발급하고 Pset 대부분과 사용자 정의 Pset 을 버린다. 외래 IFC 보존은 별도 설계가 필요하다(도구를 그대로 쓰면 불가). ifc-lite 는 통과시킨다.
4. **S10 "산출물 해시 일치"**: 같은 스크립트가 Linux 와 Windows 에서 서로 다른 바이트를 만든다(의미는 같음). OS 간 해시 기준은 부적합하며 의미 비교 또는 OS별 골든을 써야 한다.
5. **E5 한글**: ConstructCAD 의 PDF 는 한글을 "?"로 쓴다(SVG 는 유지). 로드맵 E5 "한글" 항목을 PDF 에서 통과시키려면 글꼴 내장이 필요하다. 이 설계의 시트 출력 요건(한국 납품)에 직결된다.
6. **ifc-lite E2**: `edit_hosted_element` 가 M8 오류 8건을 만든다. 왕복 통과만으로 편집 후 유효성을 믿을 수 없다. 편집 후 M8 을 모든 시험의 필수 단계로 둘 것(이번 시험에서 그것이 결함을 잡았다).
7. **Pascal**: npm 패키지만으로는 동작하지 않고 첫 실행 때 런타임을 네트워크로 받는다. 오프라인 CI·재현성 요건(헤드리스 H)과 충돌할 수 있다.
8. **BimCraft**: 릴리스 zip 에 앱이 참조하는 JS 일부가 없다(404). 기본 headless 에서는 WebGL 이 없어 3D 가 뜨지 않는다.
