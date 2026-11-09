# S10 헤드리스 스파이크 보고서

질문: GUI·디스플레이·GPU 없이 전 과정이 도는가? (roadmap S10, architecture 15.9·15.10)

결론: Windows·Ubuntu 24.04·Debian 13 모두 합격. 단 "산출물 해시 일치"는 의미 해시 3종 + 정규화 IFC 해시로 충족하고, 원시 IFC 바이트는 불일치한다(코어 개선 필요). Linux가 막히지 않아 "Windows만 1급" 강등은 불필요하다.

## 1. 합격 기준별 결과

- **RA-01급 스크립트 완주, 종료 코드 0, stdout JSON만: 합격.** `ra01_headless.py`가 코어 CLI를 자식 프로세스로 7회 호출(stdin=DEVNULL)하고, 모든 호출 stdout이 JSON 한 줄, 자기 stdout도 JSON 한 줄이다. 실패 시에도 JSON 한 줄 + 비 0 종료.
- **Windows 깨끗한 환경(근사)**: 새 venv(py -3.12, dobim-core[validate]), 환경변수 6개(SystemRoot + 임시 프로필 5개), `python -I`, CREATE_NO_WINDOW. `results/windows_clean_venv.json`.
- **Ubuntu 24.04(Python 3.12.3, apt)와 Debian 13(공식 python:3.12-slim, 3.12.16)**: `--network none`, 루트 FS 읽기 전용, fd 0 닫기, DISPLAY·WAYLAND_DISPLAY 없음, 비루트. `results/linux_ubuntu.json`, `linux_debian.json`. 네트워크 차단은 컨테이너 안 연결 시도가 "Network is unreachable"로 실패함으로 확인.
- **GUI 패키지 미설치**: dpkg 92개(Debian)/121개(Ubuntu) 중 libx11·libxcb·libgl·libegl·mesa·wayland·gtk·qt·tk·xorg 계열 0개, pip 배포 중 GUI 이름 0개.
- **해시 일치**: 의미 해시 3종(state·derived·ifc, rev 76)이 Windows == Debian == Ubuntu. IFC 정규화 DATA 해시·제품 GlobalId 집합 해시·클래스별 개수도 3환경 일치. `results/s10_summary.json`의 all_equal 10항목 전부 true.
- **CPU 3D 렌더 RA-01(988삼각형·51요소) 1280×800**: 삼각형 추출 포함 Windows 중앙값 1.05~1.29s, Debian 1.18s, Ubuntu 0.56s(최대 1.6s). Windows 프로세스 시작~PNG 저장 약 2.0s. 목표 10s 이내 합격.
- **렌더 결정성**: 반복·스레드 1/4 동일, Windows·Debian·Ubuntu의 메시·픽셀·PNG 바이트·ID 버퍼 해시 전부 일치(s0 RA-01, 코어가 만든 모델, 외래 Building-Architecture, 합성 정육면체).
- **의존성 검사 음성 시험**: 임시 git 저장소에 기준 커밋 + 위반 "시험 커밋"(이 저장소에는 커밋 안 함). 12/12 탐지, 오탐 0. 위반 예: 코어→dobim_render(직접·from·간접 2단계), 코어→tkinter·PyQt6·wgpu·numpy, 렌더→dobim_core·ifcopenshell(raster)·mesh(png)·PyQt6·mcp.

## 2. RA-01 스크립트 범위

project.new → script --atomic(76 커맨드) → model.stats → model.validate(express) → ifc.export(미리보기 종료 코드 6 → 확인 토큰 재호출 → 0) → model.hash. 프로젝트 UUID·id 고정으로 결정적이다.

- 포함: 레벨 2, 벽 14(외벽 8 두께 320·내벽 6 두께 100·높이 3300), 외벽 모서리 조인 8, 문 5(s0와 같은 호스트·위치·폭), 속성셋(IsExternal·FireRating), 재료.
- 범위 밖(코어에 커맨드 없음): 슬래브·기둥·보·계단·공간·창·타입·레이어 재료·공간 경계. 그래서 s0 골든과 직접 비교하지 않는다.
- 단계 시간(Debian, 프로세스마다 새로 띄운 값): project.new 1.2s, script 1.5s, stats 1.3s, validate 8.5s(Windows 3.6s), export 1.1s, hash 1.1s.

## 3. 발견 (코어에 환류)

1. `model.validate`(express_rules 기본 true)가 ifcopenshell.validate의 `_pytest` import 때문에, 코어 의존성만 설치한 환경에서 errors=1(ModuleNotFoundError)·종료 코드 0의 거짓 오류를 낸다. S10은 dobim-core[validate]로 설치했다. 권고: pytest를 런타임 의존으로 올리거나 UNSUPPORTED로 명시 실패.
2. `ifc.export` 산출물은 바이트 결정적이지 않다: (1) 헤더 FILE_NAME의 작성 시각, (2) 관계·속성셋 GlobalId 무작위, (3) 일부 SET 원소 순서가 OS마다 다름. 이 셋을 지운 정규화 해시는 3환경 일치. 코어는 수정하지 않았다.
3. Linux ifcopenshell 휠이 libGL.so.1·libEGL.so.1을 NEEDED로 가진다(IGES·STEP 변환기). 깨끗한 컨테이너에는 없지만 지오메트리·입출력·렌더는 정상이고, 렌더 후 GUI/GL 라이브러리 로드 0개. CI에 IGES/STEP 한정 허용목록이 필요하다.
4. Windows PE 임포트 검사에서 GUI 툴킷·OpenGL·D3D DLL 링크 없음.
5. wgpu 소프트웨어 어댑터(lavapipe)는 시스템 패키지 14개(libx11 등)를 끌고 온다 → 선택 이미지에만.

## 4. 렌더 후보 (`results/render_candidates.json`)

- **자체 numpy z-버퍼(dobim_render, 채택)**: 의존 추가 없음, 평면 음영 + 특징선 + 요소 ID 버퍼(pick→GlobalId), 3환경 PNG 바이트 동일. 한계: 공면 겹침 z-충돌, 안티에일리어싱 없음, 대형 모델 성능 미측정.
- **wgpu-py 0.33(BSD-2)**: WARP 3.0s, lavapipe 3.45s. 어댑터마다 픽셀 해시가 다르다. 자체 렌더와 98.5% 일치.
- **VTK 9.3.1 vtk-osmesa(BSD-3, 약 250MB)**: Linux 0.12s 동작. Windows 경로 없음 → 교차 결정성 미확인.
- pyrender+OSMesa·trimesh: 미시험. GPL 후보 없음.

결정성 설계: 삼각함수·BLAS·reduction 미사용, 엄격 깊이 비교, 음영 정수화, 메시 1e-6 m 반올림, 요소 GlobalId 정렬. PNG는 표준 zlib(구현이 다르면 바이트가 달라질 수 있어 픽셀 해시를 기준으로 삼음).

## 5. 패키지 경계

dobim-render(`spikes/s10-headless/pyproject.toml`)는 dobim-core에 의존하지 않고 코어도 렌더를 모른다. import-linter 계약: ① dobim_core⟂dobim_render 독립 ② 코어의 GUI·GPU·렌더 라이브러리 금지 ③ 렌더의 GUI·mcp·pydantic 금지 ④ raster·png의 ifcopenshell·mesh 금지. S1의 기존 계약 파일은 수정하지 않았다(합칠 때 병합 필요).

## 6. 시험 결과

- s10: `pytest tests -m "not docker"` → 24 passed, 1 skipped(POSIX 전용, Docker 시험이 같은 항목 실행). `pytest tests/test_docker.py` → 2 passed(Debian·Ubuntu).
- 기존 S1: 코어는 수정하지 않았다. 파일 단위로 test_lint_and_licenses, test_registry, test_graph, test_protocol 통과. 나머지는 다른 작업의 부하로 이 브랜치에서 돌리지 못했다(미확인).

## 7. 한계·미확인

- Windows "깨끗한 환경"은 근사다(Sandbox 미사용, 같은 OS 설치·계정·레지스트리). 진짜 깨끗한 Windows·다른 CPU·ARM·Alpine(musl)은 미확인. Linux는 Docker Desktop(WSL2) 컨테이너다.
- 대형 IFC 렌더 시간 미측정(표본은 약 1천 삼각형). 렌더 이미지 품질의 실무 수용성은 미확인(사람 판정 필요, `results/png/`).
- VTK의 교차 OS 결정성 미확인, pyrender·trimesh 미시험.
- 권고: ① 코어 발견 1·2 처리 ② CI에 ldd 허용목록·import-linter 계약 병합 ③ 실제 깨끗한 Windows·다른 CPU에서 렌더 해시 재확인 ④ 대형 모델 렌더 성능 측정.
- Dockerfile·sh 스크립트는 LF가 필요하다. 독립 검수에서 Windows 체크아웃(autocrlf)으로 CRLF가 되면 Docker 빌드와 sh가 깨지는 것을 재현해, `spikes/s10-headless/.gitattributes`로 LF를 강제했다.

## 8. 독립 검수 결과와 반영

판정은 조건부 통과였다. 주장한 수치(무인 완주 해시 3종·rev·정규화 해시, 렌더 시간, PNG 바이트 일치, import-linter 12/12)는 Windows 재실행에서 재현됐고, 코어(spikes/s1-core)는 수정되지 않았다.

- 반영: LF 강제(`.gitattributes`), `ra01_headless.py`의 최종 JSON을 UTF-8 바이트로 출력(한국어 Windows 실패 경로에서 cp949로 나가 JSON 해독이 깨지던 문제).
- 남은 권장·참고:
  - 의존 경계 계약은 나열 방식이라 `cv2` 같은 목록 밖 GUI 라이브러리, `importlib.import_module`·`__import__` 동적 import는 잡지 못한다. 시험은 어느 계약이 깨졌는지까지는 단언하지 않는다.
  - architecture 15.9는 CPU 렌더를 dobim-core 안에 둔다고 적었지만 이 스파이크는 코어와 렌더를 독립 패키지로 갈랐다. 합칠 때 둘 중 하나로 정해야 한다.
  - 렌더러는 정점 하나가 NaN이면 경고 없이 빈 화면을 낸다. 아주 큰 좌표의 삼각형은 조용히 빠질 수 있다. 결정성 근거 중 `** 0.5`는 libm pow라 보장 근거가 아니다(3환경에서는 일치).
  - `render_candidates.json`의 wgpu·VTK 수치는 원자료 없이 적은 값이다. `s10_summary.json`은 스크립트 산출물이다.
  - GUI 패키지 판정 목록에 pywin32, PyGObject, imgui, pyvista 등이 없다. Windows 새 venv에는 pywin32(mcp 의존)가 들어 있다.
  - 세 환경은 같은 PC·같은 x86_64 CPU(WSL2)다. 다른 CPU 아키텍처 간 일치는 증명되지 않았다.
  - 렌더 10초 근거는 1천 삼각형급 모델뿐이다. 합성 시험 외삽으로 약 12만 삼각형에서 10초에 이른다.
