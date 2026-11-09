# S11 보고서: 양쪽 클라이언트 종단 시험 (Codex 측정 완료, Claude 미시험)

질문(docs/roadmap.md S11): Claude와 Codex 양쪽에서 같은 경로로 모든 기능을 쓸 수 있는가?

이 보고서는 `spikes/s11-clients/results/*.json`에 남은 측정값만 모아 쓴 것이다. 새로 잰 값은 없다. 측정 대상 코어는 spike/s1-core 0b8d147 기준(커맨드 31개, MCP 도구 8개)이며 통합 이후 코어(커맨드 54개)에서는 다시 재지 않았다.

**결론: Codex 쪽은 측정을 마쳤고 통과했다. Claude(Claude Code, 데스크톱 앱) 쪽 종단 시험은 하지 않았다(미시험).** 그래서 "양쪽에서 쓸 수 있는가"라는 질문 전체는 아직 닫히지 않았다. 프로토콜 층(A)은 클라이언트와 무관하므로 Claude에도 같은 서버가 쓰이지만, 실제 Claude 클라이언트로 돌린 결과는 이 저장소에 없다.

## 1. 층 A: 프로토콜 (공식 SDK 클라이언트)

근거: `results/s11_layer_a_parity.json`, `results/s11_layer_a_probe.json`

- 패리티 C 100%: 레지스트리 31개 커맨드 전부를 `examples` 입력으로 CLI·MCP 양쪽에서 실행(예시 34회, 오류 사례 38회), 불일치 0, 음성 대조가 불일치를 잡음, 위험 등급 게이트 확인.
- MCP 도구 8개(`dobim_commands_search`, `dobim_command_describe`, `dobim_query`, `dobim_execute`, `dobim_execute_destructive`, `dobim_job`, `dobim_render`, `dobim_project`). 협상 프로토콜 2026-07-28.
- 서버 시작에서 initialize까지 약 1.0초, tools/list 2ms.

## 2. 층 C: 실제 클라이언트 (Codex)

근거: `results/s11_layer_c_codex_desktop.json`, `results/s11_layer_c_codex_npm.json`, `results/s11_layer_c_codex_desktop_aed3d5e.json`

- 대상: Codex 데스크톱(MCP 클라이언트 0.167.0-alpha.3.2)과 Codex npm CLI(0.147.0). 둘 다 협상 프로토콜 2025-06-18.
- 작업 그룹 G1~G5(모델링, 편집, 내보내기, 위험 커맨드·경로 거부, 대량 호출)를 클라이언트가 스스로 수행했다. 두 클라이언트 모두 G1~G5 종료 코드 0, 작업 폴더 밖 파일 생성 없음.
- Codex 데스크톱 기준 커맨드 31개 중 31개를 호출해 통과(`_summary`: commands_passed 31, 실패 0).
- 0b8d147 이전 코어(aed3d5e)로 잰 데스크톱 결과(G1~G4)도 종료 코드 0.

## 3. 결과 동등성

근거: `results/s11_equiv_codex_desktop.json`, `results/s11_equiv_codex_npm.json`

- 같은 15단계 시나리오를 CLI로 돌린 기준과 Codex로 돌린 결과를 비교했다. 호출 순서가 스크립트와 같고 추가·누락 호출 0, 의미 해시(state·derived·ifc)가 CLI와 같고, 내보낸 IFC와 첫 스냅숏의 의미 비교가 동일(요소 11개). 데스크톱·npm 둘 다 같다.

## 4. 에이전트 탐색 시험

근거: `results/s11_explore_codex_desktop.json`, `results/s11_explore_controls.json`

- Codex 데스크톱이 20개 과제를 스스로 검색·조회해 수행: 20/20 성공. 과제당 평균 도구 호출 7.75회, 평균 검색 호출 4.95회. 양성 대조 20/20, 판정 오탐 0.

## 5. 기타 측정

- 분기 린트(`results/s11_lint_no_client_branch.json`): 코어 소스 15개 파일에서 클라이언트별 분기 0건. 음성 대조가 탐지함.
- 큰 응답·환경 탐침(`results/s11_probe_bigresp_codex_desktop.json`, `results/s11_probe_env_codex_desktop.json`): Codex 데스크톱이 오류 없이 수행.
- 응답 크기(`results/s11_size_probe.json`): 벽 600·문 300 모델에서 `element.query` 기본 응답 5,362B(바이트/3 추정 약 3,575토큰, 잘림 표시와 다음 커서 있음), `commands.search` 전체 4,879B. 토큰 수는 Claude 토크나이저가 아닌 추정이다.
- Windows 점검(`results/s11_windows_checks.json`): 10회 시작 중앙값 initialize 1.03초, 첫 프로젝트 호출 0.55초(ifcopenshell 첫 import 포함), 정상 호출의 stdout 비JSON 줄 0, 한글 이름·UTF-8 왕복 이상 없음.

## 6. 한계·미확인

- **Claude 쪽 층 C·동등성·탐색 시험: 미시험.** 이 저장소의 결과 파일에 Claude 클라이언트 측정이 없다.
- 측정은 통합 이전 코어(커맨드 31개)에서 했다. 통합 코어(54개)와 패밀리·뷰·주석 커맨드는 Codex로 다시 재지 않았다. 프로토콜 층 패리티 C는 통합 코어에서 100%로 확인됐다(spikes/integration/report.md).
- 클라이언트별 토큰 비용은 Codex 사용량 값만 있고 Claude 값은 없다.
- 이 결과 파일 중 일부(층 A 외)는 한 대의 Windows PC에서 한 번씩 쟀다.
