# LGPL 라이브러리 교체 절차 (ifcopenshell, GEOS, OCCT)

DoBIM 설치 폴더는 PyInstaller onedir 이다. LGPL 구성요소는 실행 파일 안에 묶이지 않고 `_internal` 안의 **별도 파일**로 있어,
사용자는 재빌드 없이 파일만 바꿔서 다른 빌드로 교체할 수 있다 (ADR-0003 규칙 2, COPYING.LESSER 4항 취지).

| 구성요소 | 위치 (설치 폴더 기준) | 형태 |
|---|---|---|
| ifcopenshell (LGPL-3.0+) | `_internal\ifcopenshell\` | `.py` 소스 + `_ifcopenshell_wrapper*.pyd` + `ifcopenshell*.dll` |
| OCCT (LGPL-2.1 + 예외) | `_internal\ifcopenshell\TK*.dll` | ifcopenshell 휠에 함께 들어 있다 |
| GEOS (LGPL-2.1) | `_internal\Shapely.libs\geos*.dll` | shapely 휠 동봉. shapely 와 한 벌로 교체한다 |

> 중요: ifcopenshell 의 `.py` 는 반드시 **파일로** 있어야 한다 (`freeze/dobim.spec` 의 `module_collection_mode={"ifcopenshell": "py"}`).
> PYZ(실행 파일 안 압축 묶음)에 들어가면 파일을 고쳐도 반영되지 않는다 — S7 시험에서 확인한 함정이다
> (`results/s7_library_swap_pyz_mode.json`: 수정 표식이 실행되지 않음).

## 절차 (ifcopenshell 을 다른 휠로 교체)

1. DoBIM 을 모두 종료한다 (CLI 프로세스, MCP 서버 포함).
2. 원하는 휠을 받는다. 반드시 **같은 파이썬 ABI(cp312, win_amd64)** 여야 한다.
   `pip download --no-deps --only-binary=:all: --python-version 3.12 --platform win_amd64 ifcopenshell==<버전> -d 폴더`
3. 휠은 zip 이다. `ifcopenshell\` 폴더를 임시 위치에 푼다.
4. 설치 폴더의 `_internal\ifcopenshell\` 폴더를 통째로 백업(이름 바꾸기)하고, 푼 `ifcopenshell\` 폴더를 그 자리에 복사한다.
   일부 파일만 섞어 넣지 않는다 (`.py` 와 `.pyd` 의 버전이 어긋나면 `ImportError` — 시험에서 확인).
5. `dobim.exe run model.hash -p <프로젝트>` 가 같은 의미 해시를 내는지 확인한다. 이전으로 되돌리려면 백업 폴더를 되돌려 놓는다.

시험 범위(`results/s7_library_swap.json`): 0.9.0.post1(같은 버전 재설치), 0.9.0, 0.8.5 세 휠 모두 교체 후 기준 프로젝트의 의미 해시 3종이 같았고
`model.validate`(express 규칙)도 통과했다. 다만 S1 코어가 쓰는 ifcopenshell API 는 좁다(`api.*` 일부 + `validate` + 파일 쓰기). 이 결과를
"모든 ifcopenshell 빌드와 호환"으로 일반화하지 않는다. 휠을 바꿀 때는 위 5단계의 해시 확인을 반드시 한다.
`.py` 와 네이티브 파일의 버전이 어긋나면(일부만 교체) `ImportError` 가 난다 — PYZ 모드 시험에서 0.8.5 네이티브 + 0.9.0.post1 파이썬 조합으로 확인했다.

## ifcopenshell 을 직접 고친 빌드로 교체

LGPL 의 재링크 요건: 사용자가 라이브러리를 수정해 같은 자리에 넣을 수 있어야 한다.
1. ifcopenshell 소스를 받아 수정하고 Windows 용 cp312 휠로 빌드한다 (IfcOpenShell 의 빌드 문서를 따른다).
2. 위 3~5단계와 같다. `.py` 만 고친 경우에는 `_internal\ifcopenshell\*.py` 를 직접 편집해도 된다.

## GEOS 교체

`Shapely.libs\geos*.dll` 의 이름에는 해시가 붙어 있어 `shapely\*.pyd` 가 그 이름으로 링크한다.
다른 GEOS 를 쓰려면 shapely 휠을 통째로 바꾼다: `_internal\shapely\` 와 `_internal\Shapely.libs\` 를 같은 휠의 것으로 함께 교체한다.
(DoBIM 코어 경로는 shapely 를 쓰지 않는다 — ifcopenshell 의 일부 기능이 import 한다.)

## 법적 고지

- `THIRD-PARTY-NOTICES.txt` 는 설치 폴더 루트에 함께 설치한다 (`tools/gen_notices.py` 가 동결 결과물에서 자동 생성).
- LGPL 구성요소의 **소스 제공 안내**(해당 버전의 상류 소스 위치)를 설치 프로그램의 라이선스 화면과 이 파일에 넣어야 한다 — 법무 확인 필요(미결).
- 최종 사용자 계약(EULA)에 리버스 엔지니어링 금지가 있다면 LGPL 구성요소에 대해서는 예외를 명시해야 한다 (ADR-0003).
