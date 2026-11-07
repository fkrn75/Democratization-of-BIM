# ADR-0004. 플랫폼과 기술 스택

- 상태: **제안** (사용자 결정 D3, S1·S6·S7로 검증)
- 일자: 2026-11-07

## 결정 (제안)

- **플랫폼**: 코어는 Windows + Linux x64, GUI는 Windows 우선. 건축 실무 환경이 Windows 중심이라는 가정(추정)이고 개발 PC도 Windows다.
- **코어**: Python 3.12 + IfcOpenShell. 앱과 분리된 라이브러리·CLI·MCP.
- **앱 셸(선택 패키지)**: Tauri 2 + Svelte 5 + Python 사이드카. MeetScribe에서 쓴 구조와 동결 경험을 재사용한다.
- **2D**: PixiJS 편집 뷰. RefBoard의 가상화 경험을 재사용한다. 출력은 별도 벡터 경로.
- **3D**: S6에서 ifc-lite 렌더러, That Open Fragments, three.js 직접 구현 중 선택.
- **병목 코드**: 측정으로 확인된 부분만 Rust(PyO3)로 옮긴다.

## ADR-0011 반영

- `dobim-core`(Python)만 설치하면 CLI·MCP·도면·CPU 렌더가 동작한다. Windows와 Linux x64를 1급으로 지원한다. `ifcopenshell` 휠은 두 플랫폼 모두 있다(PyPI 확인).
- Tauri 앱은 필수 구성이 아니라 `dobim-core`에 의존하는 선택 패키지다. Windows 전용으로 시작한다.
- 배포는 `uvx` 또는 onedir `dobim.exe` 두 경로를 S7·S11에서 비교한다. 이 PC의 다른 MCP 서버도 `uvx.exe` 절대 경로로 실행 중이다.
- Python의 약점이 새로 드러났다. stdio MCP의 콜드 스타트와 C++ 라이브러리의 stdout 오염이다. Rust 단일 바이너리는 이 두 가지에 유리하다. **코어 언어 판단을 바꿀 만큼은 아니라고 본다.** IFC 의미 API의 재사용 이득이 더 크고, 시작 시간은 지연 import나 얇은 실행기로 해결할 수 있다고 추정한다. S11에서 틀리면 다시 본다.

## Python을 고른 이유

1. `ifcopenshell.api`, `ifctester`, `ifcdiff`, `ifcclash`, `ifcpatch`, `ifcopenshell.draw`를 바로 쓴다. IFC 의미 조작을 다시 짜지 않아도 된다.
2. 같은 코어를 Blender(Bonsai)에도 얹을 수 있어 호스트 선택지가 유지된다.
3. 스파이크가 빠르다.
4. 위험은 속도다. S1에서 5,000요소 합성 모델로 측정하고, 실패하면 그래프만 Rust로 옮긴다.

## 대안

| 대안 | 기각 이유 | 다시 볼 조건 |
|---|---|---|
| Rust 코어 + ifc-lite (Tauri 단일 바이너리) | 패키징은 깔끔하고 빠르다. 그러나 IFC 의미 API가 10개월짜리 프로젝트에 묶이고, 작성 코드량이 늘어난다 | S1에서 Python 실패, 또는 S0에서 ifc-lite의 왕복·create 품질이 IfcOpenShell과 대등 |
| 웹 우선 (TS + web-ifc/ifc-lite) | 설치가 필요 없지만 IfcOpenShell을 쓰기 어렵다(pyodide 경로는 검증 안 함). 큰 파일 제약 | 타깃이 "설치 불가 환경"으로 바뀔 때 |
| Unity 기반 | 사용자 경험은 있으나 런타임 라이선스 종속, 도면·문서 UI에 맞지 않음 | 시각화 전용 부가 도구로만 |
| Blender 호스트 고정 | ADR-0001의 경로 A에 해당 | 사용자가 경로 A를 선택할 때 |

## 다르게 정하면

- **웹 우선**: 코어를 TS/Rust로 바꾸고 ifc-lite가 사실상 기반이 된다. 커맨드 로그·그래프 설계는 유지되지만 코드는 공유하지 못한다. 저작 MVP 비용이 약 30% 늘어날 것으로 본다(추정).
- **크로스플랫폼 필수**: Tauri·Python은 이식 가능하지만 macOS·Linux WebView의 WebGPU 지원이 달라 렌더러 폴백 검증이 추가된다.

## 환경 확인 (2026-11-07 실측)

Python 3.12 설치됨(`py -V:3.12`). 기본 `py -3`는 3.14라 쓰지 않는다. `ifcopenshell 0.9.0.post1` 휠은 py310~py315의 win_amd64를 PyPI에서 확인했다. `manifold3d 3.5.4`는 cp312 win_amd64 휠이 있다. Rust 1.96, Node 24, RTX 4060 Laptop(VRAM 8GB), Blender 4.5·5.2 설치됨. **Revit은 설치되어 있지 않다**(`Program Files\Autodesk`에는 3ds Max 2025만 있음).
