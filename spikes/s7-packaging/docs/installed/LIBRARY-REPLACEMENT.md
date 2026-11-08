# 라이브러리 교체 안내

DoBIM 설치 폴더에는 LGPL 라이선스의 구성요소가 들어 있습니다. 이 구성요소들은 실행 파일(`dobim.exe`, `dobim-mcp.exe`) 안에 묶여 있지 않고
설치 폴더의 `_internal` 안에 **별도 파일**로 있어서, 다시 빌드하지 않고 파일만 바꿔 다른(또는 직접 수정한) 빌드로 교체해 쓸 수 있습니다.
각 구성요소의 라이선스 전문과 소스 위치는 설치 폴더의 `THIRD-PARTY-NOTICES.txt` 에 있습니다.

| 구성요소 | 라이선스 | 위치 (설치 폴더 기준) |
|---|---|---|
| ifcopenshell | LGPL-3.0 이상 | `_internal\ifcopenshell\` (`.py` 소스 파일 + `_ifcopenshell_wrapper*.pyd` + `ifcopenshell*.dll`) |
| Open CASCADE Technology | LGPL-2.1 + OCCT 예외 | `_internal\ifcopenshell\TK*.dll` (ifcopenshell 과 같은 폴더) |
| GEOS | LGPL-2.1 | `_internal\Shapely.libs\geos*.dll` (shapely 와 함께 들어 있음. DoBIM 코어는 쓰지 않음) |

## 먼저: 설치 방식

- **사용자 설치(NSIS, `-setup.exe`) 를 권장합니다.** 사용자 폴더(`%LOCALAPPDATA%`)에 설치되어 관리자 권한 없이 파일을 바꿀 수 있습니다.
- MSI 설치는 `C:\Program Files` 에 관리자 권한으로 설치됩니다. 이 위치의 파일을 바꾸려면 관리자 권한이 필요하고,
  `__pycache__` 를 쓸 수 없어 실행할 때마다 `.py` 를 다시 컴파일하므로 시작이 조금 느립니다. 교체해서 쓸 계획이면 NSIS 설치본을 쓰세요.

## ifcopenshell 을 다른 휠로 교체하는 절차

1. DoBIM 을 모두 종료합니다 (CLI 프로세스, MCP 서버 포함).
2. 원하는 휠을 받습니다. 파이썬 3.12 용 64비트 윈도우 휠(`cp312`, `win_amd64`)이어야 합니다.
   `pip download --no-deps --only-binary=:all: --python-version 3.12 --platform win_amd64 ifcopenshell==<버전> -d 폴더`
3. 휠은 zip 파일입니다. 안의 `ifcopenshell\` 폴더를 임시 위치에 풉니다.
4. 설치 폴더의 `_internal\ifcopenshell\` 폴더를 통째로 백업(이름 바꾸기)하고, 푼 `ifcopenshell\` 폴더를 그 자리에 복사합니다.
   **일부 파일만 섞어 넣지 마세요.** `.py` 와 `.pyd`/`.dll` 의 버전이 어긋나면 `ImportError` 가 납니다.
5. `dobim.exe run model.hash -p <프로젝트>` 가 교체 전과 같은 의미 해시를 내는지 확인합니다. 되돌리려면 백업 폴더를 원래 이름으로 돌려놓습니다.

호환 범위: DoBIM 은 ifcopenshell 0.9.0.post1 로 검증했습니다. 0.9.0, 0.8.5 로 바꿔도 기준 프로젝트의 해시가 같았지만, DoBIM 이 쓰는 ifcopenshell
기능은 일부일 뿐이므로 "모든 빌드와 호환된다"는 뜻이 아닙니다. 휠을 바꾼 뒤에는 5단계의 해시 확인을 꼭 하세요.

## 직접 수정한 ifcopenshell 로 교체

1. ifcopenshell 소스를 받아 수정하고 Windows 용 cp312 휠로 빌드합니다 (IfcOpenShell 의 빌드 문서를 따릅니다).
2. 위 3~5단계와 같습니다. `.py` 만 고쳤다면 `_internal\ifcopenshell\*.py` 를 직접 편집해도 됩니다 (이 `.py` 파일들이 실제로 실행됩니다).

## Open CASCADE (TK*.dll) 교체

ifcopenshell 휠에는 Open CASCADE DLL(`TK*.dll`)이 함께 들어 있습니다. 같은 휠의 `TK*.dll` 한 벌이 서로 맞물리므로 일부만 바꾸지 말고,
위 절차대로 `ifcopenshell\` 폴더를 통째로 교체하세요. Open CASCADE 만 따로 바꾸는 절차는 검증하지 않았습니다.

## GEOS 교체

GEOS 는 shapely 휠에 들어 있고 DoBIM 코어 경로에서는 로드되지 않습니다(shapely 를 부르는 ifcopenshell 보조 기능에서만 쓰입니다).
DLL 이름에 해시가 붙어 있어(`geos-<해시>.dll`) 같은 휠의 `shapely\*.pyd` 가 그 이름으로 링크합니다. 그래서 DLL 만 따로 바꾸지 않고
`_internal\shapely\` 와 `_internal\Shapely.libs\` 를 같은 shapely 휠의 것으로 함께 교체합니다.
파일 교체로 다른 GEOS 버전이 로드되는 것은 파이썬 환경에서 확인했습니다. 다만 shapely 의 `.py` 는 실행 파일 안에 묶여 있어
이 교체를 실행 파일 안에서 확인하지는 못했습니다.

## 소스

교체나 수정에 필요한 소스는 `THIRD-PARTY-NOTICES.txt` 의 "소스 위치" 에 적힌 상류 프로젝트에서 받을 수 있습니다.
DoBIM 은 이 구성요소들을 수정하지 않고 상류가 배포한 바이너리를 그대로 동봉합니다.
