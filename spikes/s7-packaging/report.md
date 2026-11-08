# 스파이크 S7 보고: 배포가 가능한가?

기준 커밋 65bc15d(spike/s1-core) 위에서 만들었다. 모든 측정은 `spikes/s7-packaging/`의 스크립트로 재현되고 결과는 `results/*.json`에 있다. 사용자 화면 보호를 위해 GUI 앱과 인스톨러는 **실행하지 않았다**(콘솔·stdio 동결본만 창 없이 실행). 그래서 "깨끗한 VM에서 동작"은 **미확인**이다.

이 보고서는 독립 검수(조건부 판정)를 반영해 고쳤다. 가장 큰 정정은 라이선스 결론이다: 처음 보고서는 "불명확 2건뿐"이라고 했지만, 검수가 **고지에서 빠진 정적 링크 라이브러리**(GMP/MPIR·MPFR·libxml2 등)를 찾아냈다. 지금은 그 DLL을 배포물에서 제외했고, 남은 네이티브 파일 전체를 내용 기반으로 다시 점검하는 도구와 시험을 추가했다(8절).

## 합격 기준별 판정

| 기준 | 판정 | 근거 |
|---|---|---|
| 동결 Python onedir가 소스 실행과 같은 결과 | **통과**(제외 후 재빌드본으로 재확인) | 1절 |
| Tauri + 동결 사이드카 설치본 생성 | **빌드 통과**(NSIS 64.6 MiB, MSI 85.9 MiB) | 4절 |
| 깨끗한 Windows VM에서 동작 | **미확인(사람·VM 필요)** | `docs/manual-vm-test.md` 11단계 |
| 라이선스 고지 생성 + 금지 검출 | **통과**(엄격 모드: 금지 0, 미확인 0, 귀속 불명 0, 정적 링크 전수 점검 통과). 전문 누락 13건·법무 확인 항목은 남음 | 2절 |
| 라이브러리 교체 실험 | **통과**(ifcopenshell 3개 휠에서 해시 동일, GEOS는 부분 시험). PYZ 함정 발견·수정 | 3절 |
| 대안(설치 스크립트 + 내장 파이썬) | 사용 안 함 | VM 시험이 실패하면 재검토 |

## 1. 동결 코어 = 소스 실행 (`tools/frozen_check.py`, `parity_frozen.py`)

PyInstaller 6.22.3 onedir, UPX 금지. `dobim.exe`(CLI)와 `dobim-mcp.exe`(stdio MCP)가 `_internal` 하나를 공유한다(`freeze/dobim.spec`). **CGAL 커널·svgfill·manifold DLL 5개를 spec에서 제외한 빌드**로 아래를 전부 다시 실행했다.

- **기준 프로젝트**(fixture.SCRIPT, RA-01급 결정적 스크립트)를 커맨드마다 새 프로세스로 실행했다. venv 실행·동결 실행·S1 기록값의 의미 해시 3종이 모두 일치한다(`state c85d7be0…`, `derived 47c215bb…`, `ifc 9386029c…`). S0의 RA-01.ifc는 이 코어가 가져오지 못해(IFC는 투영만) 같은 성격의 결정적 스크립트를 썼다.
- **시드 고정 무작위 열 3개**(각 300항목, 배치·undo/redo·일부러 실패하는 항목 포함)를 프로세스 안 기준 실행, venv MCP 서버, 동결 MCP 서버에 재생했다. 항목별 성공 여부와 최종 해시 3종이 셋 다 일치한다(3/3).
- **레지스트리 31개 커맨드의 예시 34건**을 venv CLI와 동결 CLI에 실행했다(확인 토큰 2단계 포함). 봉투·종료 코드·해시가 전부 같다.
- **패리티 C 전체**: S1 `tools/parity.py`의 서브프로세스만 동결 exe로 바꿔 실행했고 소스 트리 접근은 차단했다. 100.0%. 음성 대조가 불일치를 실제로 잡았다.
- **MCP stdio**: 공식 SDK 클라이언트로 initialize와 tools/list(8개)가 응답한다.
- **`model.validate`(express 규칙)·`ifc.export`**: 동결본과 venv 결과가 같다(errors 0, IFC 본문은 GUID를 가린 뒤 동일).
- **크기**: 동결 폴더 204.9 MiB(1,481 파일, 제외 전 213.9 MiB/1,486 파일), 그중 ifcopenshell 114.0 MiB.
- **시작 시간**(`results/s7_timing.json`, 다른 작업이 도는 PC라 상대 비교용, 중앙값): `dobim list` 동결 0.25s / venv 0.39s, `model.hash` 1.06s / 1.00s, MCP initialize 0.71s / 1.00s.

### 시험이 잡아낸 함정 (spec에 반영)

1. **`_pytest`가 필요하다.** `ifcopenshell.validate(express_rules=True)`가 함수 안에서 `from _pytest import assertion`을 한다. 정적 분석에 안 잡히고, 빠지면 `model.validate`가 ok=true인 채 errors=1로 조용히 실패한다. CLI·MCP를 서로 비교하는 패리티로는 못 잡고(양쪽이 같이 틀림), venv 대 동결본 비교가 잡았다. hiddenimports에 추가했다(pytest·pluggy·iniconfig, MIT). S10에서도 같은 문제가 따로 발견됐다.
2. **PYZ에 묶인 `.py`는 파일을 바꿔도 반영되지 않는다**(3절).
3. `mcp.cli`는 typer가 없으면 import 중 `sys.exit`해 `collect_submodules("mcp")`가 빌드를 죽인다. 제외했다.
4. 상대 경로로 서브프로세스를 띄우면 이 환경에서 실패했다. 도구는 절대 경로를 쓴다.

## 2. 라이선스 고지 (`tools/gen_notices.py`, `licensing.py`, `native_libs.py`, `audit_native.py`, `rust_notices.py`)

동결 **결과물**이 기준이다. PYZ 모듈 목록과 `_internal` 파일 1,481개를 빌드 venv의 RECORD와 대조해 소유 배포를 찾고, 휠에 묻어 온 네이티브 라이브러리와 런타임은 패턴 표로 귀속한다. 여기에 **네이티브 파일 내용의 정적 링크 흔적**(고유 오류 문자열·이름 장식·빌드 경로)으로 이름표에 없는 라이브러리를 증거로 올린다. 산출물은 `results/THIRD-PARTY-NOTICES.txt`(구성요소 65개 + Rust 크레이트 234개, 전문 포함, 약 0.96 MB)와 `results/s7_licenses.json`, `results/s7_native_audit.json`.

### 처음 보고서가 틀린 곳과 정정

- 처음엔 "unknown 2건(CGAL 커널, svgfill)뿐"이라 했다. 검수가 DLL 내용을 보니 `ifcopenshell_geometry_kernel_cgal.dll`·`…_cgalsimple.dll`·`…_svgfill.dll`에 **GMP/MPIR와 MPFR(LGPL)** 이 정적으로 들어 있었고, svgfill에는 **libxml2(MIT)** 와 OpenCOLLADA 경로까지 있었다. CGAL이 GPL 패키지에 링크됐는지도 여전히 모른다. 이 라이브러리들은 고지에 한 줄도 없었다.
- **결정**: `tools/slim_check.py`로 이 DLL들을 빼도 코어가 동작하고 해시가 같음이 확인됐으므로(제외 전 전체 빌드 기준 재측정: 5개 제거, -9.5 MB, 해시 3종 동일, validate 통과) **spec의 `EXCLUDE_NATIVE`로 배포물에서 제외**했다. 제외 대상은 CGAL 커널 2개, svgfill, manifold.dll, 그리고 manifold.dll을 import하는 `ifcopenshell_geometry_kernel_manifold.dll`(남기면 로드 못 하는 플러그인이 된다)이다. 지오메트리 코어 DLL까지 지우면 로드에 실패한다. **S3·S6에서 지오메트리 커널을 쓰게 되면 이 결정을 다시 해야 한다.**
- 이 결정은 `tools/audit_native.py`가 강제한다: 제외 DLL이 폴더에 다시 나타나거나 GMP/MPFR/CGAL/libxml2/GPL 문구 흔적이 생기면 실패한다.

### 남은 네이티브 파일 전수 점검 결과 (`audit_native.py`, 동결 파일 186개)

금지 흔적 0, 허용 목록 밖 빌드 의존 경로 0, 끊긴 import 0. 흔적으로 확인되어 고지에 올라간 정적 구성요소:

| 구성요소 | 라이선스 | 위치 |
|---|---|---|
| RocksDB 9.11.2 | Apache-2.0 또는 GPL-2.0 중 선택(Apache 사용) | ifcopenshell 래퍼·parse DLL |
| PROJ 9.4.1, SQLite, nlohmann/json | MIT, 퍼블릭 도메인, MIT | `ifcopenshell_geometry_glb.dll`(nlohmann은 document_json DLL 3개에도) |
| Eigen | MPL-2.0(헤더 라이브러리, DLL 17개) | ifcopenshell 계열 |
| Boost 1.92.0 | BSL-1.0(DLL 26개에 헤더 코드) | ifcopenshell 계열 |
| libgfortran(GCC 런타임) | GPL-3.0+ **WITH GCC-exception-3.1** | OpenBLAS DLL에 정적 링크(처음엔 누락) |
| zlib, bzip2, liblzma, expat, mpdecimal | 허용형 | CPython 런타임에 정적 링크 |

Eigen·Boost 같은 헤더 라이브러리는 이름 장식으로 잡았다. **한계**: 문자열 흔적 기반이라 문자열이 제거된 바이너리나 흔적이 없는 헤더 라이브러리는 못 잡는다. 목록에 없는 새 흔적은 사람이 `native_libs.py`에 보강해야 한다(고지에 올라가지 않으면 `audit_native.py`가 실패하도록 연결해 두었다).

### 판정기 강화 (`licensing.py`) — 기본이 엄격

- `gen_notices.py`는 **unknown·귀속 불명 파일이 있으면 실패**한다(예전엔 `--strict`일 때만). 탐색용 `--lenient`는 unknown만 봐주고 forbid는 여전히 실패한다.
- `GPL`·`AGPL`·`GNU General Public License` 같은 SPDX가 아닌 표기와 자유 문장 속 GPL 문구도 **forbid**다(LGPL·Lesser는 제외).
- 메타데이터 분류자가 여러 개일 때 OR가 아니라 **AND**로 묶는다(이중 라이선스인지 모르는데 OR로 묶으면 GPL이 가려진다). 모르는 분류자는 unknown이 된다.
- 음성 대조(`tests/test_licensing.py`): `evil=GPL-3.0-only`·`GPLv3`·`GNU General Public License v3`·`AGPL-3.0-only`·`MIT AND GPL-2.0-only` 주입이 모두 종료 코드 1, `--lenient`로도 1. 모르는 라이선스 주입은 기본에서 1, `--lenient`에서 0. 금지 흔적(GMP·MPFR·CGAL·libxml2·GPL 문구)이 든 합성 DLL은 `audit_native`가 잡고, LGPL 문구는 잡지 않는다. 정적 링크가 확인됐는데 고지 JSON에 없으면 실패한다.

### Rust 크레이트 고지 (Tauri 셸)

`cargo metadata --filter-platform x86_64-pc-windows-msvc`에서 루트로부터 일반 의존 간선으로 닿는 크레이트 234개를 고지에 넣는다(빌드 스크립트·개발 의존 제외, 고유 전문 130개). 금지 0, 미확인 0. **MPL-2.0 크레이트는 5개**(cssparser, cssparser-macros, dtoa-short, option-ext, selectors)이며 수정 없이 쓴다. 소스 위치를 고지에 적었다. 라이선스 파일이 소스에 없는 크레이트 5개(alloc-stdlib, selectors, webview2-com 3종)는 SPDX 식과 저장소 주소로 대신했다고 고지에 밝혔다. Python 확장 모듈(cryptography·pydantic-core·rpds 등) 내부의 Rust 크레이트는 각 배포의 라이선스 파일을 따른다.

### 배포용 고지 정리

- 고지에서 **내부 메모를 뺐다**(법무 확인 표시, ADR 번호, 시험 메모, 판정 라벨 등). 내부 메모는 결과 JSON의 `internal_note`에만 남는다.
- 고지 머리에 **교체 안내**(설치 폴더의 `LIBRARY-REPLACEMENT.md`)와 **소스 위치**(LGPL·MPL·GCC 예외 구성요소의 상류 주소)를 넣었다.

### 남은 한계 (사람이 처리)

- **전문 누락 13건**(해당 구성요소 고유의 저작권 줄이 든 원문을 휠에서 못 찾음): Boost, bzip2, Expat, liblzma, MS UCRT·VC++ 런타임 4곳, mpdecimal, nlohmann/json, PROJ, zlib. 고지에 상류 주소와 "라이선스 전문은 상류 소스에 있다"를 적었다. **표준 전문(참고 사본)으로 채운 것**: ifcopenshell(LGPL-3.0·GPL-3.0), CPython, OpenSSL, libffi, Eigen(MPL-2.0), RocksDB(Apache-2.0), OCCT(LGPL-2.1; OCCT 예외 문구는 휠에 없어 못 붙였다). **ifcopenshell 휠에는 LICENSE/COPYING 파일이 없다.** 해당 버전의 원문과 저작권 줄로 교체해야 한다.
- 법무 확인 대상: PyInstaller 부트로더 예외절의 적용 범위, MS 재배포 파일의 조항 준수, 소스 제공 안내 문안, EULA의 역공학 예외 문안(ADR-0003 후속). OCCT 버전은 문자열로 확인되지 않아 고지에 비웠다.
- `ifcopenshell/python312.dll`(6.7 MB) 중복 사본의 제거 가능 여부는 미시험.

## 3. 라이브러리 교체 실험 (`tools/library_swap.py`, `docs/library-swap.md`, 설치본용 `docs/installed/LIBRARY-REPLACEMENT.md`)

설치 폴더 복사본에서 파일만 바꿔 확인했다(재빌드 없음).

| 시험 | 결과 |
|---|---|
| PYZ 모드(첫 빌드, `results/s7_library_swap_pyz_mode.json`): `_internal/ifcopenshell/guid.py`에 표식 삽입 | **반영 안 됨.** 파일은 있지만(`guid_py_file_present_in_source_dir: true`) 로더가 PYZ의 pyc를 먼저 쓴다(`effective_loader`: PYZ 우선). 파일 교체만으로는 LGPL "교체 가능" 의무를 못 채운다 |
| 수정: spec에 `module_collection_mode={"ifcopenshell":"py"}` → 같은 시험 | **반영됨**(`effective_loader`: 파일 우선) |
| ifcopenshell 폴더를 0.9.0.post1(같은 버전 재설치)로 교체 | 로드 버전 일치, 해시 3종 동일, validate 통과 |
| 0.9.0으로 교체 | 같음 |
| 0.8.5로 교체 | 같음 |
| 일부 파일만 섞기(파이썬 0.9.0.post1 + 네이티브 0.8.5) | `ImportError`. 폴더 통째 교체를 절차로 정했다 |
| **GEOS(부분 시험)**: shapely 휠을 2.1.2·2.0.7로 파일만 교체 | GEOS 3.14.1 → 3.13.1 / 3.11.4가 실제로 로드되고 buffer·intersection 결과가 같다. **격리 파이썬에서의 시험이다**: 코어가 shapely를 부르지 않아 `dobim.exe`로는 GEOS를 부를 수 없고, 동결본의 shapely `.py`는 PYZ 안에 있어 동결 exe 안에서의 교체는 못 봤다. 시험에 쓴 파일이 동결본의 GEOS DLL과 바이트 동일함은 해시로 확인했다 |

- PYZ 모드 JSON의 `pass: false`는 그 모드가 기준을 못 채운다는 증거이지 도구 실패가 아니다.
- S1 코어가 쓰는 ifcopenshell API는 좁다. "모든 빌드와 호환"으로 일반화하지 않는다.
- **설치 방식**: 쓰기 불가 위치에 설치하면 `__pycache__`를 못 써 매번 컴파일한다. NSIS는 `installMode=currentUser`(사용자 폴더)라 해당 없다. **MSI는 Tauri 2 기본이 `Program Files` 관리자 설치(perMachine)** 이고 Tauri 설정에 사용자 범위 옵션이 없다. WiX 템플릿을 고치면 바꿀 수 있으나 설치본을 실행해 확인할 수 없어 시도하지 않았다. 그래서 **교체가 중요한 배포는 NSIS를 권장**하고, 사용자 문서에 이 제약과 관리자 권한 필요를 적었다.

## 4. Tauri 2 최소 셸 (`app/`)

- Tauri 2 + `tauri-plugin-shell`. `externalBin`=dobim, dobim-mcp. `resources`=`{"resources/": ""}`(`_internal/`, `THIRD-PARTY-NOTICES.txt`, `LIBRARY-REPLACEMENT.md`). 프런트는 정적 HTML 1장이고 버튼이 사이드카 `dobim list`를 호출한다.
- **권한 축소**: `capabilities/default.json`에서 웹뷰용 `shell:allow-execute`(`args: true`)를 뺐다. 사이드카는 Rust 쪽 커맨드가 직접 실행하므로 웹뷰에 실행 권한이 필요 없다. 뺀 뒤 빌드가 통과한다(실행은 안 함).
- `tools/prepare_tauri.py`로 동결본을 배치하고 `npx tauri build --bundles nsis,msi`로 둘 다 생성했다(Rust 1.96). 인스톨러는 저장소에 넣지 않았고 크기·해시만 `results/s7_tauri_build.json`에 기록했다: NSIS 67,691,386 B(64.6 MiB), MSI 90,116,272 B(85.9 MiB), 셸 exe 4.5 MB.
- 인스톨러를 실행하지 않고 NSIS 스크립트를 해석해 확인한 것: `dobim.exe`·`dobim-mcp.exe`가 설치 폴더 루트에 놓이고, `_internal` 1,479개 항목이 동결본과 같고, `THIRD-PARTY-NOTICES.txt`와 `LIBRARY-REPLACEMENT.md`가 포함된다.
- 같은 배치를 임시 폴더에 복사해 사이드카를 창 없이 실행하면 정상 응답한다(설치 시험은 아니다).
- 알려진 제약: 기본 WebView2 방식은 WebView2 없는 Windows 10에서 설치 중 인터넷이 필요하다(오프라인 방식 미시험). 코드 서명이 없어 SmartScreen 경고가 예상된다. CSP에 `script-src 'unsafe-inline'`이 있다(인라인 스크립트 1개, `textContent`만 사용).

## 5. 사람이 할 일

`docs/manual-vm-test.md`: 깨끗한 VM(일반 사용자, VC++/Python 없음)에서 NSIS·MSI 설치와 제거, 앱 버튼으로 사이드카 호출, 해시 재확인, `model.validate`(errors 0), MCP 등록, 한글 경로, Defender 오탐, 교체 절차, 오프라인 설치를 확인한다. 동결본은 VC++ 런타임을 직접 싣지만 VM에서 확인된 것은 아직 없다. 법무 확인 항목(2절 끝)도 사람 몫이다.

## 6. 후속 반영 제안 (docs/는 수정하지 않았다)

- ADR-0003 규칙 2 보강: "onedir만으로는 부족하다. LGPL 파이썬 패키지는 PYZ 밖 `.py` 파일(`module_collection_mode=py`)이어야 파일 교체가 반영된다."
- ADR-0003 법무 점검표에 추가: ifcopenshell 휠 안 정적 링크 라이브러리(GMP/MPFR/CGAL 계열은 제외로 해소, RocksDB·Eigen·PROJ·libgfortran 예외 등은 남음), PyInstaller 부트로더 예외, MS 재배포 파일, ifcopenshell 전문 보충. 규칙 추가: "휠에서 가져온 네이티브 라이브러리는 파일 이름이 아니라 내용으로 점검한다(`audit_native.py`)."
- ADR 후보: "지오메트리 커널(CGAL·manifold) 제외"를 의사결정으로 기록. S3·S6이 지오메트리를 쓰게 되면 재결정.
- architecture.md 패키징 절에 추가: 동결 시 `_pytest` 필요, 패리티 시험에 "동결본 대 소스 실행" 비교 추가.

## 7. 시험

- S7 pytest 48개 통과(판정기 식·분류자·Rust 표기 정규화, 합성 DLL 음성 대조, 생성기 GPL 주입·엄격 모드, 실제 동결본 점검).
- S1 코드는 건드리지 않았다. S1 `-m "not slow"`는 다른 작업과 CPU를 나눠 쓰는 중 시간 제한에 걸려 끝까지 확인하지 못했다(진행된 구간은 전부 통과).

## 8. 독립 검수 반영 (조건부 판정의 결함과 조치)

| 검수 지적 | 조치 | 상태 |
|---|---|---|
| 필수 1. 정적 링크 라이브러리(GMP/MPIR·MPFR·libxml2 등) 고지 누락 | CGAL 커널 2·svgfill·manifold 2 DLL을 spec에서 제외, 재빌드 후 소스 해시·패리티·validate 재확인, 남은 DLL 전수 점검 도구 `audit_native.py` + 증거 기반 고지 | **해소**(점검 한계는 2절) |
| 권장 2. unknown 통과, GPL 문구, 분류자 OR 묶음 | 기본 strict, GPL 문구 forbid, 분류자 AND, 음성 대조 시험 | **해소** |
| 권장 3. 교체 절차·소스 안내를 설치본에, 내부 메모 제거 | `LIBRARY-REPLACEMENT.md`를 resources로 동봉, 고지에 소스 위치 절, 내부 메모를 JSON으로 분리 | **해소**(소스 제공 문안의 법적 충분성은 법무 확인) |
| 권장 4. MSI 관리자 설치, GEOS 교체 시험 | NSIS 권장·MSI 제약 문서화(MSI 사용자 범위화는 미시도), GEOS 부분 시험 추가·고지 문구 정정 | **부분 해소**(동결 exe 안 GEOS 교체와 MSI perUser는 미확인) |
| 권장 5. `shell:allow-execute(args:true)` 제거 | 제거, 빌드 통과 | **해소**(실행 미검증) |
| 권장 6. Rust 크레이트 고지 | `rust_notices.py`(cargo metadata 기반), 고지에 234개 + MPL-2.0 5개 소스 안내 | **해소** |
| 참고. 교체 결과 JSON 필드명 | `guid_py_file_present_in_source_dir`·`effective_loader`로 분리, 두 모드 JSON 재생성 | **해소** |
| 참고. slim_check JSON 깨진 문자 | 서브프로세스 출력을 UTF-8 실패 시 cp949로 디코딩, 재생성 | **해소** |

## 재현

```
venv(py 3.12): pip install ifcopenshell==0.9.0.post1 pydantic==2.14.3 mcp==2.3.0 pyinstaller pytest pip-licenses
python -m PyInstaller --noconfirm --distpath build/dist --workpath build/work freeze/dobim.spec
DOBIM_KEEP_NATIVE=1 python -m PyInstaller --noconfirm --distpath build/dist_full --workpath build/work_full freeze/dobim.spec   # slim_check 비교용 전체 빌드
python tools/gen_notices.py ; python tools/audit_native.py ; python -m pytest tests
python tools/library_swap.py build/dist/dobim s7_library_swap.json ; python tools/slim_check.py
S7_SEEDS=1,2,3 S7_N=300 python tools/frozen_check.py ; python tools/parity_frozen.py ; python tools/timing.py
python tools/prepare_tauri.py ; (cd app && npm install && npx tauri build --bundles nsis,msi) ; python tools/record_artifacts.py
```

PyInstaller 빌드 약 3분, 시험 전체 약 30분. 교체 시험은 `S7_WHEELS` 폴더에 ifcopenshell 휠 3개와 shapely 휠 2개(2.1.2, 2.0.7)가 필요하다. `gen_notices.py`는 `cargo`가 PATH에 있어야 한다(없으면 `--no-rust`).
