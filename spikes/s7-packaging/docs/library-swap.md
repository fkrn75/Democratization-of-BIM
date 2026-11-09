# LGPL 라이브러리 교체 — 개발자용 근거 문서 (ifcopenshell, GEOS, OCCT)

> 설치본에 함께 들어가는 사용자용 절차는 [`docs/installed/LIBRARY-REPLACEMENT.md`](installed/LIBRARY-REPLACEMENT.md) 이다(`tools/prepare_tauri.py` 가 설치 폴더에 복사).
> 이 문서는 그 절차의 시험 근거와 알려진 제약을 적는다. 사용자 문서에는 내부 메모를 넣지 않는다.

DoBIM 설치 폴더는 PyInstaller onedir 이다. LGPL 구성요소는 실행 파일 안에 묶이지 않고 `_internal` 안의 **별도 파일**로 있어,
사용자는 재빌드 없이 파일만 바꿔서 다른 빌드로 교체할 수 있다 (ADR-0003 규칙 2, COPYING.LESSER 4항 취지).

| 구성요소 | 위치 (설치 폴더 기준) | 형태 |
|---|---|---|
| ifcopenshell (LGPL-3.0+) | `_internal\ifcopenshell\` | `.py` 소스 + `_ifcopenshell_wrapper*.pyd` + `ifcopenshell*.dll` |
| OCCT (LGPL-2.1 + 예외) | `_internal\ifcopenshell\TK*.dll` | ifcopenshell 휠에 함께 들어 있다 |
| GEOS (LGPL-2.1) | `_internal\Shapely.libs\geos*.dll` | shapely 휠 동봉. shapely 와 한 벌로 교체한다. 코어는 쓰지 않는다 |

> 중요: ifcopenshell 의 `.py` 는 반드시 **파일로** 있어야 한다 (`freeze/dobim.spec` 의 `module_collection_mode={"ifcopenshell": "py"}`).
> PYZ(실행 파일 안 압축 묶음)에 들어가면 파일을 고쳐도 반영되지 않는다 — S7 시험에서 확인한 함정이다
> (`results/s7_library_swap_pyz_mode.json`: `effective_loader` 가 "PYZ 우선", 수정 표식이 실행되지 않음).
> 같은 이유로 shapely·numpy 등 다른 패키지의 `.py` 는 PYZ 안에 있다(파일로 있는 것은 ifcopenshell 뿐). LGPL 의무 대상은 GEOS DLL 이며
> shapely 의 `.py`(BSD)는 의무 대상이 아니다.

## 시험 범위

`results/s7_library_swap.json` (`tools/library_swap.py`):

- E1 `.py` 수정이 반영되는가 — 반영됨(파일 우선).
- E2 ifcopenshell 휠 교체 — 0.9.0.post1(같은 버전 재설치), 0.9.0, 0.8.5 세 휠 모두 교체 후 기준 프로젝트의 의미 해시 3종이 같았고 `model.validate`(express 규칙)도 통과했다.
  S1 코어가 쓰는 ifcopenshell API 는 좁다(`api.*` 일부 + `validate` + 파일 쓰기). "모든 ifcopenshell 빌드와 호환"으로 일반화하지 않는다.
  `.py` 와 네이티브 파일의 버전이 어긋나면(일부만 교체) `ImportError` — PYZ 모드 시험에서 0.8.5 네이티브 + 0.9.0.post1 파이썬 조합으로 확인했다.
- E3 GEOS(shapely 휠) 교체 — **부분 시험.** 코어가 shapely 를 import 하지 않아 `dobim.exe` 로는 GEOS 를 부를 수 없고, 동결본의 shapely `.py` 는 PYZ 안에 있다.
  그래서 동결 폴더의 GEOS DLL 과 바이트가 같은 venv 의 shapely 2.2.0 파일을 임시 폴더에 두고, shapely 2.1.2 / 2.0.7 휠로 `shapely\` 와 `shapely.libs\` 를
  파일만 바꿔 격리 인터프리터로 로드했다: GEOS 3.14.1 → 3.13.1 / 3.11.4 가 실제로 로드되고 buffer·intersection 결과가 같았다.
  **동결 인터프리터 안의 교체(PYZ 의 옛 `.py` 와 새 `.pyd` 조합)는 시험하지 못했다.** GEOS 를 코어가 쓰게 되면 `dobim.exe` 경로로 다시 시험해야 하고,
  그때 shapely 도 `module_collection_mode` 를 `py` 로 둘지 결정한다.

## 설치 방식의 제약 (MSI)

- NSIS(`installMode=currentUser`)는 `%LOCALAPPDATA%` 에 설치되어 관리자 권한 없이 파일 교체·`__pycache__` 쓰기가 된다.
- **MSI(WiX)는 Tauri 2 기본이 `Program Files` 관리자 설치(perMachine)다.** 이 경우 (a) 설치 파일 교체에 관리자 권한이 필요하고 (b) `__pycache__` 를 못 써서
  `.py` 를 실행마다 컴파일한다(시작이 느려진다). Tauri 2 에는 MSI 사용자 범위(perUser) 설정이 없어, 바꾸려면 WiX 템플릿(`bundle.windows.wix.template`)을
  직접 수정해야 하는데 설치본을 실행해 확인할 수 없는 이번 스파이크에서는 시도하지 않았다.
  따라서 교체 가능성이 중요한 배포는 **NSIS 를 권장**하고, MSI 는 관리자 일괄 배포용으로 두며 사용자 문서에 이 제약을 적었다.
- 완화안(미시도): 빌드 때 `ifcopenshell\**\*.py` 를 `checked-hash` 방식 `.pyc` 로 미리 컴파일해 동봉(소스가 바뀌면 자동 재컴파일되므로 교체 가능성은 유지).

## 법적 고지 현황

- `THIRD-PARTY-NOTICES.txt` 는 설치 폴더 루트에 함께 설치된다(`tools/gen_notices.py` 가 동결 결과물에서 자동 생성). "소스 위치" 절에 LGPL·MPL·GCC 예외 구성요소의 상류 주소가 있다.
- 최종 사용자 계약(EULA)에 리버스 엔지니어링 금지가 있다면 LGPL 구성요소에 대해서는 예외를 명시해야 한다 (ADR-0003). 법무 확인 필요.
- 소스 제공 안내의 문안(서면 제안 등)이 법적으로 충분한지는 법무 확인 대상이다. 현재 문안은 "수정 없이 상류 배포본을 그대로 쓰며 소스는 상류에서 받는다"는 사실 진술이다.
