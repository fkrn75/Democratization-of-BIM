# 깨끗한 Windows VM 설치 시험 절차 (사람이 수행 — S7 미확인 항목)

이 스파이크는 사용자 화면 보호 때문에 인스톨러를 **실행하지 않았다**. 아래는 합격 기준
"깨끗한 Windows VM에서 동작"을 확정하기 위해 사람이 할 절차다. 결과는 표에 채워 `results/` 에 남긴다.

## 준비

- VM: Windows 10 22H2 또는 Windows 11, **파이썬·VC++ 재배포 패키지·Rust 가 없는** 깨끗한 이미지(스냅숏 권장). 일반 사용자 계정(관리자 아님) 하나.
  가능하면 두 번째 이미지로 **WebView2 런타임이 없는** Windows 10 도 시험한다 (Windows 11 에는 기본 포함).
- 전달물: `app/src-tauri/target/release/bundle/nsis/DoBIM-S7_0.0.1_x64-setup.exe`, `.../msi/DoBIM-S7_0.0.1_x64_en-US.msi`
  (크기·SHA-256 은 `results/s7_tauri_build.json`). VM 안에서 해시를 다시 계산해 일치를 확인한다.
- 시험 입력: `results/s7_frozen_check.json` 의 `A_fixture_hashes` 기대 해시 3종, 이 저장소의 `spikes/s1-core/src/dobim_core/fixture.py` 의 SCRIPT.

## 절차와 합격 판정

| # | 절차 | 합격 |
|---|---|---|
| 1 | NSIS 설치 파일을 일반 사용자로 실행(installMode=currentUser, 관리자 권한 요구가 없어야 함) | UAC 프롬프트 없이 설치 완료, 설치 폴더에 `dobim.exe`, `dobim-mcp.exe`, `_internal\`, `THIRD-PARTY-NOTICES.txt` |
| 2 | 시작 메뉴 바로가기로 앱 실행, **"코어 호출 (dobim list)"** 버튼 클릭 | 창에 커맨드 목록 JSON 이 나온다. 검은 콘솔 창이 깜빡이지 않는다 |
| 3 | 새 PowerShell 에서 설치 폴더의 `dobim.exe --workspace <빈 폴더> run project.new --json "{\"path\":\"f.dobim\",\"guid\":\"5d0b1a50-0000-4a00-8000-0d0b1a5000f1\"}"` 후 SCRIPT 10줄을 차례로 실행, 마지막에 `run model.hash -p f.dobim` | 해시 3종이 기대값과 같다 |
| 4 | `dobim.exe run model.validate --json "{\"express_rules\":true}" -p f.dobim` | `errors: 0` (※ `_pytest` 누락 회귀 확인) |
| 5 | `dobim-mcp.exe --workspace <폴더>` 를 MCP 클라이언트(Claude Code `claude mcp add` 또는 Codex 설정)에 stdio 서버로 등록 | initialize 성공, tools/list 8개 |
| 6 | 한글 사용자 이름·공백이 든 경로(예: `C:\Users\홍길동\My Docs`)에 설치/작업 폴더를 두고 3~4 반복 | 동일 결과 (경로 인코딩 확인) |
| 7 | 백신(Defender) 실시간 보호 켠 상태에서 설치·실행 | 오탐 격리 없음. 있으면 파일명·시그니처 기록 (UPX 미사용이 전제) |
| 8 | `_internal\ifcopenshell\` 을 `docs/library-swap.md` 절차대로 0.9.0 휠로 교체 후 3 재실행 | 해시 동일 |
| 9 | NSIS 제거(앱 및 기능) | 설치 폴더·바로가기·레지스트리 제거, 사용자 프로젝트 폴더는 남음 |
| 10 | MSI 로 같은 시험(1,2,9) | 동일. 관리자 권한 설치(perMachine) 동작 확인 |
| 11 | 설치 후 오프라인 상태(네트워크 차단)에서 2~4 | 동작 (런타임 다운로드 없음) |

## 기록할 것

- 각 단계 통과/실패, 실패 시 화면 캡처와 `%LOCALAPPDATA%` 아래 로그
- 첫 실행(콜드)과 재실행(웜) 시작 시간: `Measure-Command { dobim.exe list }`
- 설치 크기(디스크), 설치 소요 시간
- 위 절차에서 사람이 판단한 항목(바로가기 이름, 설치 마법사 문구 등)은 S7 범위 밖이므로 의견만 기록
