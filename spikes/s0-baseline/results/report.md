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
